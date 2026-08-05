"""Structural diff of two Vissim ``.inpx`` networks — topology only.

Roadmap R1.6 / row 2.3 (benchmark half): the "Murdoch 벤치마크 diff 리포트 v1"
that lets a generated network be compared against the hand-built consultant
model without a licence seat, without OSM, and without any projection work.

What it compares
----------------
* **links** — added / removed / lane-count changed (and re-classified between
  plain link and connector);
* **connectors** — added / removed / rewired (``fromLinkEndPt`` or
  ``toLinkEndPt`` now points at a different link), plus lane-count changes;
* **movements per node** — a movement is one connector that the node itself
  declares as a member (Vissim's own ``<linkSegment>`` list), so membership is
  read from the network, never inferred from proximity;
* **signalized-approach coverage** — per node, how many of its approach links
  (the upstream link of each movement) carry at least one signal head.

TOPOLOGY ONLY — position is deliberately NOT diffed
---------------------------------------------------
The only working georeference in this project is a five-constant Helmert fit
(``backend/routers/geojson.py``: two offsets, one rotation, two scales) with a
max residual of about 14 m, and its scale term — 1.1782 — is provably just
Web-Mercator latitude distortion (1/cos 31.93°) rather than a real survey
scale. Any positional claim therefore starts with an error floor of roughly
four lane widths, which is wider than most of the differences a network diff
would want to talk about.

So this module reports **no** coordinate, offset, distance or bearing delta.
Two links with the same id and the same lane count are "unchanged" here even
if their polylines differ. That blind spot is not left silent: when such links
exist, :func:`diff_topologies` emits a single ``WARN`` finding stating how many
there are **and** stating the uncertainty band, which is the only form the
brief permits a positional statement to take. :class:`PositionScope` carries
the same numbers as data for a future consumer (roadmap row 2.4, the network
diff view) so it can render the caveat rather than re-derive it.

Shape
-----
The computation and the rendering are separate on purpose. :func:`diff_topologies`
returns a :class:`StructuralDiff` — a pydantic model, JSON-serialisable, with
every list always present — and :func:`render_diff_report` turns that same
object into human-readable text. Row 2.4 consumes the structured half directly.

Findings follow the house pattern established by
``services/dataimport/quality.py``: a stable snake_case ``code`` that tests
assert on, English ``message`` prose, an i18n ``messages`` hook for the
ko-primary UI layer, and numeric evidence in ``context``. The severity
vocabulary (``refuse``/``warn``/``info``) has the same string values as
``dataimport.base.IssueSeverity`` and ``agent.constraints.Tier`` but is
declared locally — the same way ``constraints.py`` declares its own rather than
importing the import layer's, so ``genesis`` stays dependency-free.

Refusal, not a silently empty diff
----------------------------------
An unreadable, unparseable or non-Vissim input produces a
:class:`StructuralDiff` with ``refused=True``, a ``REFUSE`` finding saying
exactly which file failed and why, and **every section set to ``None``** — not
to an empty container. A consumer cannot mistake "we could not read this" for
"nothing changed", because there is no empty change list to read.

No COM, no Vissim, no network access, no third-party dependency beyond pydantic
(already required).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field

from services.inpx_parser import (
    _node_link_ids,
    _parse_links,
    parse_network_elements,
)

# ---------------------------------------------------------------------------
# Position: the numbers behind the refusal to compare geometry.
#
# Sourced from backend/routers/geojson.py's DEFAULT_MAP_* block and its
# comment, which is the only georeference this project has.
# ---------------------------------------------------------------------------

#: Max residual of the Helmert fit over the 9 surveyed intersection
#: coordinates, in metres.
GEOREF_MAX_RESIDUAL_M = 14.0

#: The fit's uniform scale term. 1/cos(31.93°) = 1.1783 — i.e. the Web-Mercator
#: latitude distortion at Perth, not a surveyed scale factor.
GEOREF_SCALE = 1.1782

#: Nominal lane width used only to express the residual in units a traffic
#: engineer reads at a glance. Matches inpx_parser's own fallback lane width.
NOMINAL_LANE_WIDTH_M = 3.5

POSITION_BASIS = (
    "backend/routers/geojson.py — five-constant Helmert fit (2 offsets, 1 rotation, "
    "2 scales) to 9 surveyed intersection coordinates; the .inpx carries no EPSG or "
    "projection metadata and its refPointMap anchor is ~11 km off."
)

POSITION_NOTE = (
    f"Position is NOT diffed. The only georeference available has a max residual of "
    f"~{GEOREF_MAX_RESIDUAL_M:g} m (~{GEOREF_MAX_RESIDUAL_M / NOMINAL_LANE_WIDTH_M:.0f} lane widths) "
    f"and its scale term {GEOREF_SCALE:g} is Web-Mercator latitude distortion (1/cos 31.93°), "
    "not a surveyed scale — so any coordinate, offset or distance delta would inherit that "
    "floor. A diff that implied positional accuracy it does not have would be worse than one "
    "that declines to report position."
)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


class DiffSeverity(str, Enum):
    """Severity vocabulary, string-aligned with the rest of the project.

    * ``REFUSE`` — the comparison could not be performed at all. No change
      sets are produced; the caller must not proceed as if the networks
      matched.
    * ``WARN`` — the diff is valid but something about it needs a human: a
      dangling reference, a network with no links, a difference this module
      deliberately does not measure.
    * ``INFO`` — an observation with no judgement attached (scope statements,
      counts).
    """

    REFUSE = "refuse"
    WARN = "warn"
    INFO = "info"


class DiffFinding(BaseModel):
    """One structured observation about the comparison itself.

    ``code`` is the stable machine identifier tests assert on; ``message`` is
    reviewer English; ``messages`` is the ``{lang: text}`` i18n hook the
    ko-primary dashboard layer fills in, exactly as ``ImportIssue`` does.
    """

    model_config = ConfigDict(extra="forbid")

    severity: DiffSeverity
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    messages: dict[str, str] = Field(default_factory=dict)
    subject: str | None = Field(
        default=None, description='What the finding is about: "node 4", "base network".'
    )
    context: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Structured payload
# ---------------------------------------------------------------------------


class NetworkSummary(BaseModel):
    """Head-count of one side of the comparison. Every number here is a count
    of objects, never a measurement of where they are."""

    model_config = ConfigDict(extra="forbid")

    label: str
    source: str | None = None
    link_count: int = Field(description="Every <link> element, connectors included.")
    plain_link_count: int
    connector_count: int
    node_count: int
    movement_count: int = Field(description="Connectors declared as members of some node.")
    signal_head_count: int
    signalized_link_count: int


class LaneCountChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    link_id: str
    base_lanes: int
    target_lanes: int

    @property
    def delta(self) -> int:
        return self.target_lanes - self.base_lanes


class LinkReclassification(BaseModel):
    """A link id that exists on both sides but changed kind (a plain link
    gained connector endpoints, or a connector lost them).

    Reported on its own rather than as an add+remove pair, because an id that
    survives is not an addition and not a removal — calling it both would put
    two phantom changes into a diff that has one real one.
    """

    model_config = ConfigDict(extra="forbid")

    link_id: str
    base_kind: str
    target_kind: str


class LinkDiff(BaseModel):
    """Plain (non-connector) links only. Connectors live in :class:`ConnectorDiff`."""

    model_config = ConfigDict(extra="forbid")

    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    lane_count_changed: list[LaneCountChange] = Field(default_factory=list)
    unchanged_count: int = 0

    @property
    def change_count(self) -> int:
        return len(self.added) + len(self.removed) + len(self.lane_count_changed)


class ConnectorRewire(BaseModel):
    """A connector whose ``fromLinkEndPt``/``toLinkEndPt`` now attaches to a
    different link. ``changed_endpoints`` names which end(s) moved."""

    model_config = ConfigDict(extra="forbid")

    connector_id: str
    base_from_link: str | None = None
    target_from_link: str | None = None
    base_to_link: str | None = None
    target_to_link: str | None = None
    changed_endpoints: list[str] = Field(default_factory=list)


class ConnectorDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    rewired: list[ConnectorRewire] = Field(default_factory=list)
    lane_count_changed: list[LaneCountChange] = Field(default_factory=list)
    unchanged_count: int = 0

    @property
    def change_count(self) -> int:
        return (
            len(self.added)
            + len(self.removed)
            + len(self.rewired)
            + len(self.lane_count_changed)
        )


class MovementRef(BaseModel):
    """One movement: the connector that carries it, and the links it joins."""

    model_config = ConfigDict(extra="forbid")

    connector_id: str
    from_link: str | None = None
    to_link: str | None = None

    @property
    def label(self) -> str:
        return f"{self.from_link or '?'}->{self.to_link or '?'} (connector {self.connector_id})"


class NodeMovementDiff(BaseModel):
    """Movements at one node. ``present_in_*`` distinguishes "the node has no
    movements" from "the node does not exist on that side" — a node that was
    deleted wholesale and a node that was emptied are different findings."""

    model_config = ConfigDict(extra="forbid")

    node_id: str
    present_in_base: bool
    present_in_target: bool
    base_movement_count: int = 0
    target_movement_count: int = 0
    added: list[MovementRef] = Field(default_factory=list)
    removed: list[MovementRef] = Field(default_factory=list)
    rewired: list[ConnectorRewire] = Field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed or self.rewired) or (
            self.present_in_base != self.present_in_target
        )


class NodeSignalCoverage(BaseModel):
    """Signalized-approach coverage at one node, both sides.

    An *approach* is the upstream link of a movement at the node (an approach
    may itself be a connector — Vissim chains them). It is *signalized* when
    that link carries at least one ``<signalHead>``. Coverage is the share of
    approaches that are signalized: the question "did the rebuilt network keep
    every signalized approach the reference model had?".

    Approaches are read from what the connectors *claim*, not from what
    survives: if a connector still points at a link the network no longer
    contains, that link is still counted as an approach here. That is the
    truthful reading of the file — the network really does declare that
    approach — and the incoherence is reported separately and by id as
    ``connector_reference_dangling``. Quietly dropping it would make a broken
    network's coverage look better than a correct one's.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str
    base_approaches: list[str] = Field(default_factory=list)
    base_signalized: list[str] = Field(default_factory=list)
    target_approaches: list[str] = Field(default_factory=list)
    target_signalized: list[str] = Field(default_factory=list)
    newly_signalized: list[str] = Field(default_factory=list)
    no_longer_signalized: list[str] = Field(default_factory=list)

    @staticmethod
    def _ratio(part: int, whole: int) -> float | None:
        return round(part / whole, 4) if whole else None

    @property
    def base_coverage(self) -> float | None:
        return self._ratio(len(self.base_signalized), len(self.base_approaches))

    @property
    def target_coverage(self) -> float | None:
        return self._ratio(len(self.target_signalized), len(self.target_approaches))

    @property
    def changed(self) -> bool:
        return bool(self.newly_signalized or self.no_longer_signalized) or (
            self.base_approaches != self.target_approaches
        )


class PositionScope(BaseModel):
    """The unit's position decision, as data rather than as prose in a docstring.

    ``reported`` is ``False`` and there is no code path that sets it ``True``.
    The remaining fields exist so a consumer (row 2.4's view) can state the
    uncertainty band without re-deriving it, and so ``geometry_differs_count``
    makes the blind spot visible: that many links are structurally identical
    on both sides but carry a different polyline.
    """

    model_config = ConfigDict(extra="forbid")

    reported: bool = False
    max_residual_m: float = GEOREF_MAX_RESIDUAL_M
    residual_lane_widths: float = round(GEOREF_MAX_RESIDUAL_M / NOMINAL_LANE_WIDTH_M, 1)
    scale_term: float = GEOREF_SCALE
    basis: str = POSITION_BASIS
    note: str = POSITION_NOTE
    geometry_differs_count: int = Field(
        default=0,
        description=(
            "Links present on both sides with identical id, kind and lane count whose "
            "polyline differs. Counted, never measured."
        ),
    )


class StructuralDiff(BaseModel):
    """The whole comparison.

    When ``refused`` is true every section is ``None`` — deliberately, so that
    a refusal can never be read as an empty diff. Check ``refused`` (or ``ok``)
    before touching a section.
    """

    model_config = ConfigDict(extra="forbid")

    refused: bool = False
    base: NetworkSummary | None = None
    target: NetworkSummary | None = None
    links: LinkDiff | None = None
    connectors: ConnectorDiff | None = None
    reclassified: list[LinkReclassification] | None = None
    node_movements: list[NodeMovementDiff] | None = None
    signal_coverage: list[NodeSignalCoverage] | None = None
    position: PositionScope = Field(default_factory=PositionScope)
    findings: list[DiffFinding] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when a comparison was actually performed. Warnings never block."""
        return not self.refused

    @property
    def refusals(self) -> list[DiffFinding]:
        return [f for f in self.findings if f.severity is DiffSeverity.REFUSE]

    @property
    def warnings(self) -> list[DiffFinding]:
        return [f for f in self.findings if f.severity is DiffSeverity.WARN]

    @property
    def infos(self) -> list[DiffFinding]:
        return [f for f in self.findings if f.severity is DiffSeverity.INFO]

    def finding_codes(self, severity: DiffSeverity | None = None) -> list[str]:
        return [f.code for f in self.findings if severity is None or f.severity is severity]

    @property
    def change_count(self) -> int:
        """Total structural changes. ``0`` on an identical pair; meaningless
        (and always ``0``) on a refusal — guard with ``ok`` first."""
        if self.refused or self.links is None or self.connectors is None:
            return 0
        node_changes = sum(1 for n in (self.node_movements or []) if n.changed)
        return (
            self.links.change_count
            + self.connectors.change_count
            + len(self.reclassified or [])
            + node_changes
        )

    @property
    def has_changes(self) -> bool:
        return self.change_count > 0


# ---------------------------------------------------------------------------
# Topology extraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NetworkTopology:
    """Everything the diff needs from one ``.inpx``, and nothing else.

    Built exclusively from ``services.inpx_parser`` output — no XML traversal
    is reimplemented here. ``node_members`` comes from the parser's
    ``_node_link_ids`` (the node's own ``<linkSegment>`` list): a module-private
    helper, used rather than duplicated, because the alternative — assigning
    connectors to their nearest node — would be exactly the positional
    inference this unit refuses to make.

    ``geometry`` holds each link's rounded polyline. It is used for ONE thing:
    counting how many otherwise-identical links carry a different polyline, so
    the "we do not diff position" statement can say how much it is not saying.
    No delta is ever computed from it.

    ``signal_head_declared`` vs ``signal_head_total``: the parser resolves each
    signal head to an absolute placement and *skips* any whose link, lane or
    pos will not resolve. Those heads would silently vanish from the coverage
    metric, so both numbers are kept and the diff warns when they disagree.
    """

    label: str
    source: str | None
    links: dict[str, dict]
    node_members: dict[str, set[str]]
    signal_head_links: dict[str, int]
    signal_head_total: int
    signal_head_declared: int
    geometry: dict[str, tuple[tuple[float, float], ...]]

    @property
    def connector_ids(self) -> set[str]:
        return {lid for lid, lk in self.links.items() if lk.get("is_connector")}

    @property
    def plain_link_ids(self) -> set[str]:
        return {lid for lid, lk in self.links.items() if not lk.get("is_connector")}

    def movements(self, node_id: str) -> list[dict]:
        """Connectors the node itself declares, in stable id order."""
        members = self.node_members.get(node_id, set())
        return [
            self.links[lid]
            for lid in sorted(members & self.connector_ids, key=_id_sort_key)
        ]

    def summary(self) -> NetworkSummary:
        movement_ids: set[str] = set()
        for members in self.node_members.values():
            movement_ids |= members & self.connector_ids
        return NetworkSummary(
            label=self.label,
            source=self.source,
            link_count=len(self.links),
            plain_link_count=len(self.plain_link_ids),
            connector_count=len(self.connector_ids),
            node_count=len(self.node_members),
            movement_count=len(movement_ids),
            signal_head_count=self.signal_head_total,
            signalized_link_count=len(self.signal_head_links),
        )


def _id_sort_key(value: str) -> tuple[int, int, str]:
    """Numeric-aware id ordering, so 505 sorts before 10953."""
    text = value or ""
    if text.isdigit():
        return (0, int(text), "")
    return (1, 0, text)


def _sorted_ids(ids: Iterable[str]) -> list[str]:
    return sorted(ids, key=_id_sort_key)


def _geometry_key(points: Any) -> tuple[tuple[float, float], ...]:
    """Rounded polyline, for equality only. 1e-6 m is far below any meaningful
    difference and immunises the comparison against float-formatting noise."""
    result: list[tuple[float, float]] = []
    for point in points or []:
        try:
            result.append((round(float(point[0]), 6), round(float(point[1]), 6)))
        except (TypeError, ValueError, IndexError):
            continue
    return tuple(result)


def topology_from_root(
    root: ET.Element, *, label: str, source: str | None = None
) -> NetworkTopology:
    """Extract the topology of one already-parsed ``.inpx`` tree.

    Duplicate link ids (which Vissim itself forbids, but a generated file could
    produce) are resolved last-wins and are *not* silently accepted — the
    caller-facing :func:`load_topology` reports them. Links with an empty ``no``
    attribute are dropped for the same reason: an unidentifiable link cannot be
    matched across two files, and matching it by position is what this module
    does not do.
    """
    parsed = _parse_links(root)
    links: dict[str, dict] = {}
    geometry: dict[str, tuple[tuple[float, float], ...]] = {}
    for link in parsed:
        link_id = link.get("id") or ""
        if not link_id:
            continue
        links[link_id] = link
        geometry[link_id] = _geometry_key(link.get("points"))

    node_members = {
        node_id: {lid for lid in members if lid}
        for node_id, members in _node_link_ids(root).items()
    }

    signal_heads = parse_network_elements(root, parsed).get("signal_heads", [])
    signal_head_links: dict[str, int] = {}
    for head in signal_heads:
        head_link = head.get("link")
        if head_link:
            signal_head_links[head_link] = signal_head_links.get(head_link, 0) + 1
    # Same xpath the parser uses, so the two counts are comparable.
    declared = len(root.findall(".//signalHeads/signalHead"))

    return NetworkTopology(
        label=label,
        source=source,
        links=links,
        node_members=node_members,
        signal_head_links=signal_head_links,
        signal_head_total=len(signal_heads),
        signal_head_declared=declared,
        geometry=geometry,
    )


def load_topology(
    path: str | Path, *, label: str | None = None
) -> tuple[NetworkTopology | None, list[DiffFinding]]:
    """Read one ``.inpx`` from disk. Reports, never raises.

    Returns ``(None, [REFUSE finding])`` for a file that is missing, unreadable,
    not well-formed XML, or well-formed but not a Vissim network (no ``<links>``
    container anywhere in it). A file whose ``<links>`` container is *present
    but empty* is a coherent — if unusual — network, so it loads and earns a
    ``WARN`` instead: "empty" and "unreadable" are different answers and the
    caller deserves to be told which.

    ``.inpx`` files are plain XML on disk. Vissim also ships ``.inpx`` variants
    that are zip containers; those fail the well-formedness check above and
    refuse with the parser's own message rather than being half-read.
    """
    file_path = Path(path)
    name = label or file_path.name
    findings: list[DiffFinding] = []

    if not file_path.exists():
        return None, [
            DiffFinding(
                severity=DiffSeverity.REFUSE,
                code="network_file_missing",
                message=f"{name}: no such file — {file_path}",
                subject=name,
                context={"path": str(file_path)},
            )
        ]
    if file_path.is_dir():
        return None, [
            DiffFinding(
                severity=DiffSeverity.REFUSE,
                code="network_file_missing",
                message=f"{name}: expected an .inpx file, found a directory — {file_path}",
                subject=name,
                context={"path": str(file_path)},
            )
        ]

    try:
        root = ET.parse(file_path).getroot()
    except ET.ParseError as exc:
        return None, [
            DiffFinding(
                severity=DiffSeverity.REFUSE,
                code="network_unparseable",
                message=(
                    f"{name}: not well-formed XML, so no network could be read from it — {exc}"
                ),
                subject=name,
                context={"path": str(file_path), "error": str(exc)},
            )
        ]
    except OSError as exc:
        return None, [
            DiffFinding(
                severity=DiffSeverity.REFUSE,
                code="network_unreadable",
                message=f"{name}: could not be read — {exc}",
                subject=name,
                context={"path": str(file_path), "error": str(exc)},
            )
        ]

    if root.find(".//links") is None:
        return None, [
            DiffFinding(
                severity=DiffSeverity.REFUSE,
                code="network_not_recognised",
                message=(
                    f"{name}: parsed as XML but carries no <links> element, so it is not a "
                    "Vissim network. Refusing rather than reporting an empty network."
                ),
                subject=name,
                context={"path": str(file_path), "root_tag": root.tag},
            )
        ]

    topology = topology_from_root(root, label=name, source=str(file_path))

    duplicate_ids = _duplicate_link_ids(root)
    if duplicate_ids:
        findings.append(
            DiffFinding(
                severity=DiffSeverity.WARN,
                code="duplicate_link_ids",
                message=(
                    f"{name}: {len(duplicate_ids)} link id(s) appear more than once "
                    f"({', '.join(duplicate_ids[:5])}"
                    f"{', ...' if len(duplicate_ids) > 5 else ''}); only the last occurrence of "
                    "each was compared."
                ),
                subject=name,
                context={"ids": duplicate_ids},
            )
        )
    if not topology.links:
        findings.append(
            DiffFinding(
                severity=DiffSeverity.WARN,
                code="network_empty",
                message=(
                    f"{name}: the <links> container is present but holds no links. The "
                    "comparison is valid — every link on the other side reads as added or "
                    "removed — but this is almost certainly not the file you meant."
                ),
                subject=name,
                context={"path": str(file_path)},
            )
        )
    return topology, findings


def _duplicate_link_ids(root: ET.Element) -> list[str]:
    """Link ``no`` values that occur more than once, in document order.

    The one place this module touches raw XML, and only to *count* — the
    parser's list is a dict-friendly view that has already collapsed
    duplicates, so a duplicate is invisible downstream unless counted here.
    """
    seen: set[str] = set()
    duplicates: list[str] = []
    container = root.find(".//links")
    if container is None:
        return duplicates
    for link in container.findall("link"):
        link_id = link.get("no") or ""
        if not link_id:
            continue
        if link_id in seen and link_id not in duplicates:
            duplicates.append(link_id)
        seen.add(link_id)
    return duplicates


# ---------------------------------------------------------------------------
# The diff
# ---------------------------------------------------------------------------


def _lane_count(link: dict) -> int:
    try:
        return int(link.get("lanes") or 0)
    except (TypeError, ValueError):
        return 0


def _kind(link: dict) -> str:
    return "connector" if link.get("is_connector") else "link"


def _movement_ref(link: dict) -> MovementRef:
    return MovementRef(
        connector_id=link.get("id") or "",
        from_link=link.get("from_link"),
        to_link=link.get("to_link"),
    )


def _diff_links(
    base: NetworkTopology, target: NetworkTopology
) -> tuple[LinkDiff, ConnectorDiff, list[LinkReclassification], int]:
    """Split the link universe into plain links, connectors and reclassifications.

    An id present on both sides is never an add *and* a remove: if its kind
    changed it goes to ``reclassified`` and to neither list, so the change
    counts stay equal to the number of edits actually made.
    """
    base_ids = set(base.links)
    target_ids = set(target.links)
    common = base_ids & target_ids

    link_diff = LinkDiff()
    connector_diff = ConnectorDiff()
    reclassified: list[LinkReclassification] = []
    geometry_differs = 0

    for link_id in _sorted_ids(base_ids - target_ids):
        if base.links[link_id].get("is_connector"):
            connector_diff.removed.append(link_id)
        else:
            link_diff.removed.append(link_id)

    for link_id in _sorted_ids(target_ids - base_ids):
        if target.links[link_id].get("is_connector"):
            connector_diff.added.append(link_id)
        else:
            link_diff.added.append(link_id)

    for link_id in _sorted_ids(common):
        before = base.links[link_id]
        after = target.links[link_id]
        base_kind, target_kind = _kind(before), _kind(after)
        if base_kind != target_kind:
            reclassified.append(
                LinkReclassification(
                    link_id=link_id, base_kind=base_kind, target_kind=target_kind
                )
            )
            continue

        changed = False
        base_lanes, target_lanes = _lane_count(before), _lane_count(after)
        if base_lanes != target_lanes:
            changed = True
            change = LaneCountChange(
                link_id=link_id, base_lanes=base_lanes, target_lanes=target_lanes
            )
            if target_kind == "connector":
                connector_diff.lane_count_changed.append(change)
            else:
                link_diff.lane_count_changed.append(change)

        if target_kind == "connector":
            rewire = _rewire(link_id, before, after)
            if rewire is not None:
                changed = True
                connector_diff.rewired.append(rewire)

        if not changed:
            if target_kind == "connector":
                connector_diff.unchanged_count += 1
            else:
                link_diff.unchanged_count += 1
            if base.geometry.get(link_id) != target.geometry.get(link_id):
                geometry_differs += 1

    return link_diff, connector_diff, reclassified, geometry_differs


def _rewire(link_id: str, before: dict, after: dict) -> ConnectorRewire | None:
    changed: list[str] = []
    if before.get("from_link") != after.get("from_link"):
        changed.append("from")
    if before.get("to_link") != after.get("to_link"):
        changed.append("to")
    if not changed:
        return None
    return ConnectorRewire(
        connector_id=link_id,
        base_from_link=before.get("from_link"),
        target_from_link=after.get("from_link"),
        base_to_link=before.get("to_link"),
        target_to_link=after.get("to_link"),
        changed_endpoints=changed,
    )


def _diff_node_movements(
    base: NetworkTopology, target: NetworkTopology
) -> list[NodeMovementDiff]:
    node_ids = _sorted_ids(set(base.node_members) | set(target.node_members))
    result: list[NodeMovementDiff] = []
    for node_id in node_ids:
        base_movements = {m["id"]: m for m in base.movements(node_id)}
        target_movements = {m["id"]: m for m in target.movements(node_id)}
        added = [
            _movement_ref(target_movements[cid])
            for cid in _sorted_ids(set(target_movements) - set(base_movements))
        ]
        removed = [
            _movement_ref(base_movements[cid])
            for cid in _sorted_ids(set(base_movements) - set(target_movements))
        ]
        rewired: list[ConnectorRewire] = []
        for cid in _sorted_ids(set(base_movements) & set(target_movements)):
            rewire = _rewire(cid, base_movements[cid], target_movements[cid])
            if rewire is not None:
                rewired.append(rewire)
        result.append(
            NodeMovementDiff(
                node_id=node_id,
                present_in_base=node_id in base.node_members,
                present_in_target=node_id in target.node_members,
                base_movement_count=len(base_movements),
                target_movement_count=len(target_movements),
                added=added,
                removed=removed,
                rewired=rewired,
            )
        )
    return result


def _approaches(topology: NetworkTopology, node_id: str) -> list[str]:
    """Distinct upstream links of the node's movements — its approaches."""
    return _sorted_ids(
        {m["from_link"] for m in topology.movements(node_id) if m.get("from_link")}
    )


def _diff_signal_coverage(
    base: NetworkTopology, target: NetworkTopology
) -> list[NodeSignalCoverage]:
    node_ids = _sorted_ids(set(base.node_members) | set(target.node_members))
    result: list[NodeSignalCoverage] = []
    for node_id in node_ids:
        base_app = _approaches(base, node_id)
        target_app = _approaches(target, node_id)
        base_sig = [a for a in base_app if a in base.signal_head_links]
        target_sig = [a for a in target_app if a in target.signal_head_links]
        result.append(
            NodeSignalCoverage(
                node_id=node_id,
                base_approaches=base_app,
                base_signalized=base_sig,
                target_approaches=target_app,
                target_signalized=target_sig,
                newly_signalized=_sorted_ids(set(target_sig) - set(base_sig)),
                no_longer_signalized=_sorted_ids(set(base_sig) - set(target_sig)),
            )
        )
    return result


def _dangling_references(topology: NetworkTopology) -> list[str]:
    """Connector endpoints pointing at a link id the network does not contain.

    A structural fault in one file rather than a difference between two, so it
    is a finding, never a change — but a connector hanging off a deleted link
    is exactly the damage a bad generated network does, and silence about it
    would be the failure mode this project exists to prevent.
    """
    problems: list[str] = []
    known = set(topology.links)
    for link_id in _sorted_ids(topology.connector_ids):
        link = topology.links[link_id]
        for end in ("from_link", "to_link"):
            ref = link.get(end)
            if ref and ref not in known:
                problems.append(f"connector {link_id} {end.replace('_link', '')}-link {ref}")
    return problems


def diff_topologies(base: NetworkTopology, target: NetworkTopology) -> StructuralDiff:
    """Compare two extracted topologies. Pure, total, and never positional."""
    link_diff, connector_diff, reclassified, geometry_differs = _diff_links(base, target)
    node_movements = _diff_node_movements(base, target)
    signal_coverage = _diff_signal_coverage(base, target)

    findings: list[DiffFinding] = []
    position = PositionScope(geometry_differs_count=geometry_differs)

    findings.append(
        DiffFinding(
            severity=DiffSeverity.INFO,
            code="topology_only_scope",
            message=(
                "Structural comparison only: links, connectors, node movements and "
                "signalized-approach coverage. " + POSITION_NOTE
            ),
            context={
                "max_residual_m": GEOREF_MAX_RESIDUAL_M,
                "residual_lane_widths": position.residual_lane_widths,
                "scale_term": GEOREF_SCALE,
                "basis": POSITION_BASIS,
            },
        )
    )

    if geometry_differs:
        findings.append(
            DiffFinding(
                severity=DiffSeverity.WARN,
                code="geometry_differs_not_measured",
                message=(
                    f"{geometry_differs} link(s) are structurally identical on both sides "
                    "(same id, same kind, same lane count) but carry a different polyline. "
                    "How different was NOT measured: any positional delta in this project "
                    f"inherits a floor of ~{GEOREF_MAX_RESIDUAL_M:g} m "
                    f"(~{position.residual_lane_widths:g} lane widths) from the five-constant "
                    f"Helmert fit, whose scale term {GEOREF_SCALE:g} is Web-Mercator latitude "
                    "distortion rather than a surveyed scale."
                ),
                context={
                    "links": geometry_differs,
                    "max_residual_m": GEOREF_MAX_RESIDUAL_M,
                    "basis": POSITION_BASIS,
                },
            )
        )

    for topology in (base, target):
        unresolved = topology.signal_head_declared - topology.signal_head_total
        if unresolved > 0:
            findings.append(
                DiffFinding(
                    severity=DiffSeverity.WARN,
                    code="signal_heads_unresolved",
                    message=(
                        f"{topology.label}: {unresolved} of "
                        f"{topology.signal_head_declared} declared signal head(s) could not be "
                        "resolved to a link/lane/pos and are therefore missing from the "
                        "signalized-approach coverage below — the coverage figures for this "
                        "side understate the network."
                    ),
                    subject=topology.label,
                    context={
                        "declared": topology.signal_head_declared,
                        "resolved": topology.signal_head_total,
                    },
                )
            )

        problems = _dangling_references(topology)
        if problems:
            findings.append(
                DiffFinding(
                    severity=DiffSeverity.WARN,
                    code="connector_reference_dangling",
                    message=(
                        f"{topology.label}: {len(problems)} connector endpoint(s) reference a "
                        f"link the network does not contain — {problems[0]}"
                        + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else "")
                    ),
                    subject=topology.label,
                    context={"problems": problems},
                )
            )

    for coverage in signal_coverage:
        if coverage.no_longer_signalized:
            findings.append(
                DiffFinding(
                    severity=DiffSeverity.WARN,
                    code="signalized_approach_lost",
                    message=(
                        f"Node {coverage.node_id}: "
                        f"{len(coverage.no_longer_signalized)} approach(es) signalized in "
                        f"{base.label} are not signalized in {target.label} "
                        f"({', '.join(coverage.no_longer_signalized)})."
                    ),
                    subject=f"node {coverage.node_id}",
                    context={
                        "approaches": coverage.no_longer_signalized,
                        "base_coverage": coverage.base_coverage,
                        "target_coverage": coverage.target_coverage,
                    },
                )
            )

    return StructuralDiff(
        refused=False,
        base=base.summary(),
        target=target.summary(),
        links=link_diff,
        connectors=connector_diff,
        reclassified=reclassified,
        node_movements=node_movements,
        signal_coverage=signal_coverage,
        position=position,
        findings=findings,
    )


def diff_network_files(
    base_path: str | Path,
    target_path: str | Path,
    *,
    base_label: str = "base",
    target_label: str = "target",
) -> StructuralDiff:
    """Load two ``.inpx`` files and compare them. Reports, never raises.

    If either file refuses to load, the result is a refusal: ``refused=True``,
    the REFUSE finding(s) explaining which file and why, and every section
    ``None``. Both files are attempted even when the first fails, so one call
    tells the user about both problems.
    """
    base, base_findings = load_topology(base_path, label=base_label)
    target, target_findings = load_topology(target_path, label=target_label)
    load_findings = base_findings + target_findings

    if base is None or target is None:
        return StructuralDiff(refused=True, findings=load_findings)

    diff = diff_topologies(base, target)
    diff.findings = load_findings + diff.findings
    return diff


# ---------------------------------------------------------------------------
# Rendering — deliberately separate from the computation above, so roadmap row
# 2.4 (the network diff view) can consume StructuralDiff directly without
# parsing prose back out of a string.
# ---------------------------------------------------------------------------

_SEVERITY_TAG = {
    DiffSeverity.REFUSE: "REFUSE",
    DiffSeverity.WARN: "WARN  ",
    DiffSeverity.INFO: "INFO  ",
}

#: Long id lists are truncated in the TEXT rendering only; the structured
#: payload always carries every id.
_MAX_LISTED = 20


def _render_ids(ids: list[str]) -> str:
    return _render_list(ids, str)


def _render_list(items: list, label) -> str:
    """One truncation rule for every list in the report, so no list can be cut
    short without saying so."""
    if not items:
        return "-"
    shown = ", ".join(label(item) for item in items[:_MAX_LISTED])
    if len(items) > _MAX_LISTED:
        shown += f" ... (+{len(items) - _MAX_LISTED} more)"
    return shown


def _lane_label(change: LaneCountChange) -> str:
    return f"{change.link_id}: {change.base_lanes}->{change.target_lanes}"


def _rewire_label(rewire: ConnectorRewire) -> str:
    return (
        f"{rewire.connector_id}: {rewire.base_from_link}->{rewire.base_to_link}"
        f" => {rewire.target_from_link}->{rewire.target_to_link}"
    )


def _render_coverage(part: int, whole: int, ratio: float | None) -> str:
    return f"{part}/{whole}" + (f" ({ratio * 100:.0f}%)" if ratio is not None else "")


def render_diff_report(diff: StructuralDiff, *, title: str = "Network structural diff") -> str:
    """Human-readable rendering of a :class:`StructuralDiff`.

    English, matching ``dataimport/quality.py``'s finding prose; the ko/ja
    surface goes through ``DiffFinding.messages`` and the dashboard's existing
    ``t()`` plumbing when this reaches the UI, rather than being hard-coded
    here in one language.
    """
    lines: list[str] = [title, "=" * len(title), ""]

    if diff.refused:
        lines.append("REFUSED — no comparison was performed.")
        lines.append("")
        for finding in diff.findings:
            lines.append(f"  [{_SEVERITY_TAG[finding.severity]}] {finding.code}: {finding.message}")
        lines.append("")
        lines.append("No change sets are reported. This is NOT an empty diff.")
        return "\n".join(lines)

    base, target = diff.base, diff.target
    links, connectors = diff.links, diff.connectors
    if base is None or target is None or links is None or connectors is None:
        # Unreachable through this module's own constructors (a non-refused
        # diff always carries every section). Handled rather than asserted so a
        # hand-built or deserialised object cannot crash the renderer.
        lines.append("INCOMPLETE — the diff object carries no change sets and is not marked")
        lines.append("refused. Nothing can be reported from it.")
        return "\n".join(lines)

    lines.append(f"base   : {base.label}" + (f"  ({base.source})" if base.source else ""))
    lines.append(f"target : {target.label}" + (f"  ({target.source})" if target.source else ""))
    lines.append("")
    lines.append("Totals")
    lines.append("------")
    lines.append(f"{'':24}{'base':>10}{'target':>10}{'delta':>10}")
    for field_name, caption in (
        ("link_count", "links (incl. conn.)"),
        ("plain_link_count", "plain links"),
        ("connector_count", "connectors"),
        ("node_count", "nodes"),
        ("movement_count", "movements"),
        ("signal_head_count", "signal heads"),
        ("signalized_link_count", "signalized links"),
    ):
        before = getattr(base, field_name)
        after = getattr(target, field_name)
        lines.append(f"{caption:24}{before:>10}{after:>10}{after - before:>+10}")
    lines.append("")

    lines.append("Links")
    lines.append("-----")
    lines.append(f"added   ({len(links.added):>3}): {_render_ids(links.added)}")
    lines.append(f"removed ({len(links.removed):>3}): {_render_ids(links.removed)}")
    lines.append(f"lanes   ({len(links.lane_count_changed):>3}): "
                 + _render_list(links.lane_count_changed, _lane_label))
    lines.append(f"unchanged     : {links.unchanged_count}")
    lines.append("")

    lines.append("Connectors")
    lines.append("----------")
    lines.append(f"added   ({len(connectors.added):>3}): {_render_ids(connectors.added)}")
    lines.append(f"removed ({len(connectors.removed):>3}): {_render_ids(connectors.removed)}")
    lines.append(f"rewired ({len(connectors.rewired):>3}): "
                 + _render_list(connectors.rewired, _rewire_label))
    lines.append(f"lanes   ({len(connectors.lane_count_changed):>3}): "
                 + _render_list(connectors.lane_count_changed, _lane_label))
    lines.append(f"unchanged     : {connectors.unchanged_count}")
    lines.append("")

    if diff.reclassified:
        lines.append("Reclassified")
        lines.append("------------")
        for item in diff.reclassified:
            lines.append(f"  {item.link_id}: {item.base_kind} -> {item.target_kind}")
        lines.append("")

    lines.append("Movements per node")
    lines.append("------------------")
    lines.append(f"{'node':>8}{'base':>8}{'target':>8}{'+':>6}{'-':>6}{'rewired':>9}  status")
    for node in diff.node_movements or []:
        if not node.present_in_base:
            status = f"node absent from {base.label}"
        elif not node.present_in_target:
            status = f"node absent from {target.label}"
        elif node.changed:
            status = "changed"
        else:
            status = "unchanged"
        lines.append(
            f"{node.node_id:>8}{node.base_movement_count:>8}{node.target_movement_count:>8}"
            f"{len(node.added):>6}{len(node.removed):>6}{len(node.rewired):>9}  {status}"
        )
        for ref in node.added:
            lines.append(f"           + {ref.label}")
        for ref in node.removed:
            lines.append(f"           - {ref.label}")
        for rewire in node.rewired:
            lines.append(
                f"           ~ connector {rewire.connector_id}: "
                f"{rewire.base_from_link}->{rewire.base_to_link} => "
                f"{rewire.target_from_link}->{rewire.target_to_link}"
            )
    lines.append("")

    lines.append("Signalized-approach coverage")
    lines.append("----------------------------")
    lines.append(f"{'node':>8}{'base':>14}{'target':>14}  changes")
    for coverage in diff.signal_coverage or []:
        changes: list[str] = []
        if coverage.newly_signalized:
            changes.append(f"+signal {', '.join(coverage.newly_signalized)}")
        if coverage.no_longer_signalized:
            changes.append(f"-signal {', '.join(coverage.no_longer_signalized)}")
        before = _render_coverage(
            len(coverage.base_signalized), len(coverage.base_approaches), coverage.base_coverage
        )
        after = _render_coverage(
            len(coverage.target_signalized),
            len(coverage.target_approaches),
            coverage.target_coverage,
        )
        lines.append(
            f"{coverage.node_id:>8}{before:>14}{after:>14}"
            f"  {'; '.join(changes) if changes else '-'}"
        )
    lines.append("")

    lines.append("Position")
    lines.append("--------")
    lines.append(f"reported: {'yes' if diff.position.reported else 'no'}")
    lines.append(f"  {diff.position.note}")
    lines.append(f"  basis: {diff.position.basis}")
    lines.append(
        f"  links identical in topology but different in polyline: "
        f"{diff.position.geometry_differs_count} (counted, not measured)"
    )
    lines.append("")

    lines.append("Findings")
    lines.append("--------")
    if not diff.findings:
        lines.append("  (none)")
    for finding in diff.findings:
        subject = f" [{finding.subject}]" if finding.subject else ""
        lines.append(f"  [{_SEVERITY_TAG[finding.severity]}] {finding.code}{subject}: {finding.message}")
    lines.append("")

    lines.append(
        f"TOTAL structural changes: {diff.change_count}"
        + ("" if diff.has_changes else "  (networks are structurally identical)")
    )
    return "\n".join(lines)


__all__ = [
    "GEOREF_MAX_RESIDUAL_M",
    "GEOREF_SCALE",
    "POSITION_BASIS",
    "POSITION_NOTE",
    "ConnectorDiff",
    "ConnectorRewire",
    "DiffFinding",
    "DiffSeverity",
    "LaneCountChange",
    "LinkDiff",
    "LinkReclassification",
    "MovementRef",
    "NetworkSummary",
    "NetworkTopology",
    "NodeMovementDiff",
    "NodeSignalCoverage",
    "PositionScope",
    "StructuralDiff",
    "diff_network_files",
    "diff_topologies",
    "load_topology",
    "render_diff_report",
    "topology_from_root",
]

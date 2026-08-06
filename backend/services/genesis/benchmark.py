"""Topological correspondence between two independently-authored networks.

Roadmap R1.6 / row 2.3, benchmark **v2**. The Murdoch rebuild benchmark asks one
question: *how much of the consultant's hand-built model does the generated draft
actually contain?* Benchmark v1 answered it with :mod:`services.genesis.diff`,
which matches objects **by id**, and the answer was wrong in a way that looked
right — see below. This module answers it by **geometry**, and every tolerance it
uses is stated here with the number it was derived from.

Why id-matching had to be replaced (the measured failure, 2026-08-06)
---------------------------------------------------------------------
``diff_network_files(consultant, generated)`` reported **0 links removed**. That
is not a good result, it is a broken one: the generator numbers its links
sequentially from 1, and the consultant's link ids also start at 1, so link "17"
in one file and link "17" in the other are DIFFERENT ROADS that the diff read as
the same object. The 52 "lane-count changes" it reported were phantoms — two
unrelated roads disagreeing about lanes — and every genuinely missing consultant
link was hidden behind an id collision. Id equality is a valid correspondence
only between two revisions of one file. Between two files authored independently
it is noise.

So correspondence here is **positional**: two objects correspond when they lie on
top of each other and run the same way. Unit U2 refused to *report* position
because this project's georeference is too weak to make a positional claim; it
did not refuse to *use* position to decide which object is which. Those are
different acts, and the difference is exactly the tolerance below: matching only
needs to know "closer than 22 m, same direction", which the georeference
supports; reporting would need "3.1 m north", which it does not. **No positional
delta appears anywhere in this module's output** — not in the models, not in the
rendering. Counts of things that differ, yes; metres, never.

The two frames and the bridge between them
------------------------------------------
The consultant model lives in Vissim local coordinates with no EPSG or
projection metadata. The generated draft lives in metres on a local tangent
plane about a recorded WGS84 origin (:mod:`services.genesis.project`). They meet
in the generated draft's plane:

    consultant (Vissim local)
        --[ backend/routers/geojson.py: Helmert fit + Web-Mercator inverse ]-->
    WGS84
        --[ project.LocalTangentPlane.forward, about the draft's own origin ]-->
    common local metre frame  <-- generated draft is already here (identity)

The generated side needs no transform at all: :mod:`services.genesis.draft`
writes ``plane.forward(lat, lon)`` straight into the ``.inpx``, so its stored
coordinates *are* the common frame — provided the caller supplies the same
origin, which is why the origin is a required input rather than a guess.

``routers.geojson`` is imported **lazily, inside the one function that needs
it**. A service importing a router is a layering inversion, and that router
pulls in FastAPI and the dataset layer, which the rest of ``services.genesis``
deliberately does without. The lazy import keeps the package importable (and its
fixtures runnable) on a machine where neither is available, and the failure is a
REFUSE finding naming the import error rather than an ImportError at collection
time.

The tolerance, derived
----------------------
:data:`MATCH_TOLERANCE_M` = 22.0 m, the sum of three terms, each of which is a
number this repository already owns:

======================= ======== ==============================================
term                      metres  provenance
======================= ======== ==============================================
georeference residual       14.0  ``geojson.py``'s own comment: the Helmert fit
                                  (2 offsets, 1 rotation, 2 scales) to 9
                                  surveyed intersection coordinates has a **max
                                  residual of ~14 m**, and its scale term
                                  1.1782 is Web-Mercator latitude distortion
                                  (1/cos 31.93°), not a survey scale. Mirrored
                                  as ``diff.GEOREF_MAX_RESIDUAL_M``. This is the
                                  FLOOR: no tolerance below it can be honest,
                                  because the bridge itself moves points by up
                                  to this much.
projection budget            1.0  ``project.MAX_PLANIMETRIC_ERROR_M`` — the
                                  budget the tangent plane's validity radius is
                                  solved for. The Murdoch draft's *measured*
                                  bound is 0.79 m, but the constant is used
                                  rather than the measurement so the tolerance
                                  cannot silently shrink when a different
                                  network is benchmarked.
centreline allowance         7.0  2 x ``diff.NOMINAL_LANE_WIDTH_M``. The two
                                  files do not draw the same line, by
                                  construction: OSM carries one way per road and
                                  the generator offsets each direction off it,
                                  while the consultant draws each carriageway
                                  where the modeller put it. Two nominal lane
                                  widths is the smallest allowance that covers a
                                  directional pair straddling a shared
                                  centreline. This one is an **allowance, not a
                                  measurement**, and is named as such.
======================= ======== ==============================================

Everything else scales off it, so a caller who changes ``tolerance_m`` changes
the whole method coherently rather than one arbitrary knob:

* :data:`CORRIDOR_BUFFER_FACTOR` = 2 -> corridor buffer 44 m. One tolerance is
  the *correctness* half (a generated link can only match if it is within one
  tolerance of consultant geometry, so a buffer of at least one tolerance can
  never exclude a matchable link — asserted, not assumed, in
  :func:`benchmark_networks`). The second tolerance is the *usefulness* half:
  ``in_corridor_unmatched`` is supposed to mean "road the consultant chose not
  to model", and the frontage roads, service lanes and slip roads that phrase
  refers to sit one carriageway away from what is modelled. 44 m is roughly a
  dual-carriageway cross-section plus verges.
* :data:`LATERAL_CLUSTER_GAP_M` = the tolerance. Two candidate chains separated
  laterally by more than one tolerance are different roads, not one road drawn
  twice, so they are reported as ``ambiguous`` and never merged or force-picked.
* :data:`BEARING_BAND_DEG` = 30°. The generator emits both directions of a
  two-way road as separate links a lane-width apart, so the **wrong-direction
  twin of every correct candidate is always inside the positional tolerance**;
  direction is the only thing that separates them, and it separates them by
  180°. The next-nearest wrong answer is the cross street at ~90°. 30° is a
  sixth of the first and a third of the second, and wide enough for two
  hand-drawn polylines of the same road to disagree. It is never sufficient on
  its own: a member must also lie within tolerance along most of its length, and
  its chain must cover most of the consultant object.
* :data:`MIN_MEMBER_COVERAGE` = 0.8 — a generated link joins a chain only if 80%
  of its own sampled length is within tolerance of the consultant object, so a
  link that merely clips it near an intersection cannot join.
* :data:`MIN_CHAIN_COVERAGE` = 0.8 — a chain matches only if it accounts for 80%
  of the consultant object's length. The two files cut roads at different places
  (OSM at every intersection node, the consultant at modelling decisions), so
  chain ends legitimately overrun or fall short; one fifth is the slack that
  buys, and on a ~110 m link it is the tolerance itself.
* :data:`SAMPLE_STEP_M` = 5.0 — polylines are compared as densified point sets;
  5 m is a quarter of the tolerance, so the sampling can never be what decides a
  match.

Directed-chain matching
-----------------------
One consultant link is normally **one-to-many**: the consultant cuts a corridor
into modelling units, OSM cuts the same corridor at every intersection node. So
the unit of correspondence is *consultant object <-> chain of generated objects*
(plain links and connectors both, since a turn the consultant models as a link
may be a connector in the draft, and vice versa). For each consultant object:

1. **candidates** — generated objects with a sample within tolerance
   (grid-indexed);
2. **members** — candidates that keep :data:`MIN_MEMBER_COVERAGE` of their own
   length within tolerance *and* run the same way as the consultant object's
   local tangent over the arc window they occupy (:data:`BEARING_BAND_DEG`);
3. **chains** — members clustered by signed lateral offset, split wherever
   consecutive offsets differ by more than :data:`LATERAL_CLUSTER_GAP_M`;
4. **bucket** — exactly one chain covering :data:`MIN_CHAIN_COVERAGE` of the
   consultant object is ``matched``; two or more is ``ambiguous`` (every
   candidate chain named, none picked); none is ``missing_from_draft``.

Every consultant object lands in exactly one bucket. That is checked, not hoped:
:func:`benchmark_networks` raises :class:`BenchmarkInvariantError` if the three
buckets do not partition the consultant object set, because a benchmark that
loses objects would understate exactly what it exists to find.

The chain that is NAMED is the chain that CARRIES
-------------------------------------------------
Every admissible member decides the bucket; only the subset that actually
accounts for the consultant object is named (:func:`_carrying_chain`, a greedy
interval cover with :data:`MIN_CHAIN_GAIN_M` as the floor on a resolvable
contribution). Without that split, a 63 m consultant link on the real pair named
thirty-odd members — every intersection connector lying inside a stretch some
other member already covered. They are near the road; they are not what carries
it, and naming them made every chain read as "lanes vary 1..4".

What is compared on a match, and what is not
--------------------------------------------
* **lane counts** — consultant lanes against the chain's, with the chain's
  min/max when it varies along the chain, and compared LIKE WITH LIKE: a
  consultant link against the chain's plain-link members, a consultant connector
  against its connector members. A generated connector's lane count states the
  generator's one-connector-per-movement convention, not how many lanes the road
  has. A matched pair with no same-kind member is ``not_comparable`` and stays
  out of the denominator instead of being scored as a disagreement.
* **signalized approaches** — reported as a whole-scope gap, once. The generator
  is geometry-only: it emits no signal heads at all, so a per-link "signal
  missing" finding would be 129 restatements of one scope decision.
* **position** — never. ``geometry_differs``-style counts are permitted; a
  metres number is not.

Where this method is weakest, stated rather than hidden
-------------------------------------------------------
An object shorter than the tolerance has its whole extent inside the positional
uncertainty, so position cannot discriminate it and the bucket rests on
direction and adjacency alone. On the real Murdoch pair that is 145 of 312
consultant objects (126 of them connectors, which are short by nature). Every
such match carries ``shorter_than_tolerance``, the count leads the report, and
a WARN finding says so — it is a property of a 14 m georeference, not of the
draft, and it is the first thing that would improve if the network were ever
surveyed.

No COM, no Vissim, no network access, stdlib + pydantic only.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
import unicodedata
import xml.etree.ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field

from services.genesis.diff import (
    GEOREF_MAX_RESIDUAL_M,
    GEOREF_SCALE,
    NOMINAL_LANE_WIDTH_M,
    POSITION_BASIS,
    DiffFinding,
    DiffSeverity,
    NetworkSummary,
    NetworkTopology,
    _id_sort_key,
    load_topology,
)
from services.genesis.project import MAX_PLANIMETRIC_ERROR_M, LocalTangentPlane

# ---------------------------------------------------------------------------
# Tolerances. Every one of these is derived in the module docstring; none is a
# bare literal chosen because it looked about right.
# ---------------------------------------------------------------------------

#: Term 1 — the georeference floor. ``geojson.py``'s Helmert fit, max residual.
GEOREF_RESIDUAL_M = GEOREF_MAX_RESIDUAL_M

#: Term 2 — ``project.MAX_PLANIMETRIC_ERROR_M``, the tangent plane's budget.
PROJECTION_BUDGET_M = MAX_PLANIMETRIC_ERROR_M

#: Term 3 — an allowance, not a measurement: two nominal lane widths, for the
#: fact that OSM's single centreline and the consultant's per-carriageway
#: centreline are not the same line.
CENTRELINE_ALLOWANCE_M = 2.0 * NOMINAL_LANE_WIDTH_M

#: The matching tolerance: 14.0 + 1.0 + 7.0 = 22.0 m.
MATCH_TOLERANCE_M = GEOREF_RESIDUAL_M + PROJECTION_BUDGET_M + CENTRELINE_ALLOWANCE_M

#: Corridor buffer = this many tolerances. One for correctness, one for use.
CORRIDOR_BUFFER_FACTOR = 2.0

#: Direction agreement band, degrees. 180° is the reverse carriageway, ~90° the
#: cross street; 30° admits neither.
BEARING_BAND_DEG = 30.0

#: Share of a candidate's own length that must lie within tolerance for it to
#: join a chain.
MIN_MEMBER_COVERAGE = 0.8

#: Share of the consultant object's length a chain must account for to match.
MIN_CHAIN_COVERAGE = 0.8

#: Polyline densification step, metres. A quarter of the tolerance.
SAMPLE_STEP_M = 5.0

#: Lateral separation above which two candidate chains are different roads.
LATERAL_CLUSTER_GAP_M = MATCH_TOLERANCE_M

#: Minimum arc window on the consultant object used to read its local direction.
#: Below this a chord is bearing noise rather than a direction.
MIN_DIRECTION_WINDOW_M = 5.0

#: New arc a member must contribute to be NAMED in a chain. Coverage is measured
#: on samples :data:`SAMPLE_STEP_M` apart, so a contribution smaller than that is
#: not resolvable from its neighbours' and naming it would only pad the chain.
#: Never changes a bucket: the match decision uses every admissible member, and
#: only the reported chain is reduced to the subset that carries the object.
MIN_CHAIN_GAIN_M = SAMPLE_STEP_M

TOLERANCE_BASIS = (
    f"{GEOREF_RESIDUAL_M:g} m georeference residual (backend/routers/geojson.py: Helmert "
    f"fit to 9 surveyed intersection coordinates, max residual ~{GEOREF_RESIDUAL_M:g} m, "
    f"scale term {GEOREF_SCALE:g} = 1/cos 31.93 deg Web-Mercator latitude distortion, not a "
    f"surveyed scale) + {PROJECTION_BUDGET_M:g} m tangent-plane planimetric budget "
    f"(services.genesis.project.MAX_PLANIMETRIC_ERROR_M) + {CENTRELINE_ALLOWANCE_M:g} m "
    f"centreline allowance (2 x {NOMINAL_LANE_WIDTH_M:g} m nominal lane width; an allowance "
    f"for OSM's single centreline vs the consultant's per-carriageway centreline, not a "
    f"measurement) = {MATCH_TOLERANCE_M:g} m."
)

NO_POSITION_DELTA_NOTE = (
    "Correspondence is decided by position; no position is reported. Distances are used "
    "internally to answer 'which object is which' — a question the georeference above can "
    "answer — and are never surfaced as a finding, because 'how far apart' is a question it "
    "cannot answer. Counts of objects that differ are reported; metres are not."
)

SIGNAL_SCOPE_NOTE = (
    "The generator is geometry-only: it emits links, connectors and node polygons and no "
    "signal heads at all. Signalized-approach coverage is therefore reported once, as a scope "
    "gap for the whole benchmark, rather than as a per-link failure — 129 restatements of one "
    "scope decision would bury the differences that are real."
)


class BenchmarkInvariantError(RuntimeError):
    """The bucket partition failed. A code fault, never a data fault.

    Raised rather than reported because a benchmark whose buckets have lost or
    duplicated consultant objects is not a weaker benchmark, it is a wrong one,
    and its headline percentage would be a lie with a denominator.
    """


# ---------------------------------------------------------------------------
# i18n. Declared locally rather than imported from ``draft.pick_text`` for the
# same reason ``diff.py`` declares its own severity enum: this module stays
# importable without pulling the OSM/draft half of the package in behind it.
# ---------------------------------------------------------------------------


def pick_text(table: dict[str, str], language: str) -> str:
    """ko primary; an unknown language code falls back to Korean."""
    return table.get(language, table["ko"])


# ---------------------------------------------------------------------------
# Geometry — plain stdlib, no shapely.
# ---------------------------------------------------------------------------

Point = tuple[float, float]


def _clean_points(raw: Any) -> tuple[Point, ...]:
    out: list[Point] = []
    for item in raw or []:
        try:
            x, y = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        if out and out[-1] == (x, y):
            continue  # duplicate vertex: no length, no direction, no information
        out.append((x, y))
    return tuple(out)


def _cumulative(points: Sequence[Point]) -> list[float]:
    cum = [0.0]
    for a, b in zip(points, points[1:]):
        cum.append(cum[-1] + math.dist(a, b))
    return cum


def _length(points: Sequence[Point]) -> float:
    return _cumulative(points)[-1] if len(points) > 1 else 0.0


def _point_at(points: Sequence[Point], cum: Sequence[float], s: float) -> Point:
    """The point at arc length ``s``, clamped to the polyline's own extent."""
    if not points:
        return (0.0, 0.0)
    if len(points) == 1 or s <= 0.0:
        return points[0]
    if s >= cum[-1]:
        return points[-1]
    for i in range(len(points) - 1):
        if cum[i + 1] >= s:
            span = cum[i + 1] - cum[i]
            t = 0.0 if span <= 0.0 else (s - cum[i]) / span
            ax, ay = points[i]
            bx, by = points[i + 1]
            return (ax + t * (bx - ax), ay + t * (by - ay))
    return points[-1]


@dataclass(frozen=True)
class _Projection:
    """Where a point falls on a polyline: arc length, signed offset, distance.

    ``lateral`` is positive to the LEFT of the polyline's direction of travel.
    Sign matters: it is what separates two parallel carriageways into different
    candidate chains instead of one merged blob.
    """

    s: float
    lateral: float
    distance: float


def _project(points: Sequence[Point], cum: Sequence[float], p: Point) -> _Projection:
    if not points:
        return _Projection(0.0, 0.0, math.inf)
    if len(points) == 1:
        return _Projection(0.0, 0.0, math.dist(points[0], p))

    px, py = p
    best = _Projection(0.0, 0.0, math.inf)
    for i in range(len(points) - 1):
        ax, ay = points[i]
        bx, by = points[i + 1]
        dx, dy = bx - ax, by - ay
        seg_len_sq = dx * dx + dy * dy
        if seg_len_sq <= 0.0:
            continue
        t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
        t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
        qx, qy = ax + t * dx, ay + t * dy
        distance = math.hypot(px - qx, py - qy)
        if distance < best.distance:
            seg_len = math.sqrt(seg_len_sq)
            lateral = ((dx * (py - ay)) - (dy * (px - ax))) / seg_len
            best = _Projection(cum[i] + t * seg_len, lateral, distance)
    return best


def _densify(points: Sequence[Point], step: float) -> tuple[Point, ...]:
    """Every vertex, plus intermediate points no further apart than ``step``.

    Vertices are always kept: a bend is where two polylines most disagree, and
    dropping it would smooth away the disagreement.
    """
    if len(points) < 2:
        return tuple(points)
    out: list[Point] = [points[0]]
    for a, b in zip(points, points[1:]):
        seg = math.dist(a, b)
        if seg > step:
            n = int(seg // step)
            for k in range(1, n + 1):
                t = (k * step) / seg
                if t < 1.0:
                    out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
        out.append(b)
    return tuple(out)


def _angle_between(v1: tuple[float, float], v2: tuple[float, float]) -> float:
    """Unsigned angle between two vectors, degrees in [0, 180]. ``180`` for a
    reversal — which is the whole point of measuring it."""
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 <= 0.0 or n2 <= 0.0:
        return 180.0
    cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    cos = -1.0 if cos < -1.0 else (1.0 if cos > 1.0 else cos)
    return math.degrees(math.acos(cos))


def _union_length(intervals: Sequence[tuple[float, float]]) -> float:
    if not intervals:
        return 0.0
    ordered = sorted((min(a, b), max(a, b)) for a, b in intervals)
    total = 0.0
    lo, hi = ordered[0]
    for a, b in ordered[1:]:
        if a > hi:
            total += hi - lo
            lo, hi = a, b
        else:
            hi = max(hi, b)
    total += hi - lo
    return total


# ---------------------------------------------------------------------------
# Networks in a common metre frame
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeoObject:
    """One link or connector, its polyline already in the common metre frame."""

    id: str
    name: str
    kind: str  # "link" | "connector"
    lanes: int
    points: tuple[Point, ...]

    @property
    def length_m(self) -> float:
        return _length(self.points)


@dataclass(frozen=True)
class GeoNetwork:
    """One side of the comparison, in the common frame, plus its head-count.

    ``frame`` names the transform that put it here, so a report can say how the
    two sides were made comparable instead of implying they always were.
    """

    label: str
    source: str | None
    frame: str
    objects: dict[str, GeoObject]
    summary: NetworkSummary

    def sorted_ids(self) -> list[str]:
        return sorted(self.objects, key=_id_sort_key)

    @classmethod
    def from_objects(
        cls,
        label: str,
        objects: Iterable[GeoObject],
        *,
        source: str | None = None,
        frame: str = "hand-built (already in the common metre frame)",
        node_count: int = 0,
        movement_count: int = 0,
        signal_head_count: int = 0,
        signalized_link_count: int = 0,
    ) -> "GeoNetwork":
        """Build a network straight from objects, deriving the head-count.

        For fixtures and for any caller that has geometry in the common frame
        already and no ``.inpx`` to read it back out of.
        """
        mapping = {obj.id: obj for obj in objects}
        summary = NetworkSummary(
            label=label,
            source=source,
            link_count=len(mapping),
            plain_link_count=sum(1 for o in mapping.values() if o.kind == "link"),
            connector_count=sum(1 for o in mapping.values() if o.kind == "connector"),
            node_count=node_count,
            movement_count=movement_count,
            signal_head_count=signal_head_count,
            signalized_link_count=signalized_link_count,
        )
        return cls(
            label=label, source=source, frame=frame, objects=mapping, summary=summary
        )


def _geo_objects(
    topology: NetworkTopology, transform: Callable[[Point], Point]
) -> dict[str, GeoObject]:
    objects: dict[str, GeoObject] = {}
    for link_id, link in topology.links.items():
        raw = _clean_points(link.get("points"))
        points = tuple(transform(p) for p in raw)
        try:
            lanes = int(link.get("lanes") or 0)
        except (TypeError, ValueError):
            lanes = 0
        objects[link_id] = GeoObject(
            id=link_id,
            name=(link.get("name") or "").strip(),
            kind="connector" if link.get("is_connector") else "link",
            lanes=lanes,
            points=points,
        )
    return objects


def node_membership_findings(
    path: str | Path, topology: NetworkTopology, *, label: str
) -> list[DiffFinding]:
    """Warn when a network DECLARES node membership that the parser resolves to
    nothing — so a zero in the Totals table cannot be read as a real zero.

    ``inpx_parser._node_link_ids`` derives node membership geometrically, from
    each node's ``<geometry><points>`` polygon, on the stated grounds that
    "Vissim does not write ``<linkSegment>``". The real consultant model
    contradicts both halves of that: its nodes carry ``<linkSegs>`` lists and no
    polygon at all, so the helper returns an empty set for every node and every
    membership-derived number — ``movement_count`` here, and the whole
    movements-per-node and signalized-approach-coverage half of
    ``services.genesis.diff`` — reads 0.

    This module does not use node membership (correspondence is decided from
    link geometry), so the benchmark's own numbers are unaffected. But the
    Totals row would still print a 0 that a reader would take for a finding.
    Fixing the parser is out of this unit's scope; saying so is not.
    """
    file_path = Path(path)
    try:
        root = ET.parse(file_path).getroot()
    except (ET.ParseError, OSError):  # already reported by load_topology
        return []

    declared = 0
    nodes_with_declarations = 0
    for node in root.findall(".//nodes/node"):
        segments = node.findall(".//linkSegment")
        if segments:
            nodes_with_declarations += 1
            declared += len(segments)
    resolved = sum(len(members) for members in topology.node_members.values())
    if not declared or resolved:
        return []

    return [
        DiffFinding(
            severity=DiffSeverity.WARN,
            code="node_membership_unresolved",
            message=(
                f"{label}: {nodes_with_declarations} node(s) declare {declared} <linkSegment> "
                "member(s) but the parser resolves none of them, because it derives node "
                "membership from a <geometry> polygon these nodes do not carry "
                "(services/inpx_parser.py::_node_link_ids). Every membership-derived number for "
                "this network therefore reads 0 — including 'movements' in the totals below and "
                "the movements-per-node and signalized-approach sections of the id-based diff. "
                "That 0 is a parser gap, not a property of the model. This benchmark does not "
                "use node membership, so its own figures are unaffected."
            ),
            messages={
                "ko": (
                    f"{label}: 노드 {nodes_with_declarations}개가 <linkSegment> {declared}개를 "
                    "선언하지만 파서가 하나도 해석하지 못했습니다(폴리곤 기반 판정인데 이 노드들에는 "
                    "폴리곤이 없음). 아래 '이동류' 0은 모델의 성질이 아니라 파서의 한계입니다. "
                    "본 벤치마크는 노드 소속을 쓰지 않으므로 대응 결과에는 영향이 없습니다."
                ),
                "en": (
                    f"{label}: {declared} declared node member(s) resolve to none — a parser gap, "
                    "not a property of the model. The benchmark does not use node membership."
                ),
                "ja": (
                    f"{label}: ノードが宣言する <linkSegment> {declared} 件をパーサが 1 件も解決できません"
                    "(ポリゴン基準の判定だが該当ノードにポリゴンがない)。下表の「移動流」0 はモデルの性質"
                    "ではなくパーサの制約です。本ベンチマークはノード所属を使いません。"
                ),
            },
            subject=label,
            context={
                "declared_link_segments": declared,
                "nodes_with_declarations": nodes_with_declarations,
                "resolved_members": resolved,
            },
        )
    ]


def local_frame_network(
    path: str | Path, *, label: str
) -> tuple[GeoNetwork | None, list[DiffFinding]]:
    """Load an ``.inpx`` whose stored coordinates ARE the common frame.

    That is true of a :mod:`services.genesis.draft` output by construction —
    ``draft.py`` writes ``plane.forward(lat, lon)`` straight into the file — and
    of any hand-built fixture written in metres about the same origin. It is NOT
    true of a consultant model; that one goes through :func:`georeferenced_network`.
    """
    topology, findings = load_topology(path, label=label)
    if topology is None:
        return None, findings
    findings = findings + node_membership_findings(path, topology, label=label)
    return (
        GeoNetwork(
            label=label,
            source=topology.source,
            frame="identity (stored coordinates are already the common metre frame)",
            objects=_geo_objects(topology, lambda p: p),
            summary=topology.summary(),
        ),
        findings,
    )


def helmert_bridge(
    path: str | Path, plane: LocalTangentPlane, *, label: str
) -> tuple[Callable[[Point], Point] | None, list[DiffFinding]]:
    """Vissim local coordinates -> the common metre frame, for one ``.inpx``.

    Composes this project's only georeference (``backend/routers/geojson.py``:
    the network's own ``refPointMap``/``refPointNet``/``northDir`` plus the
    five-constant Helmert fit, into Web Mercator, then out to WGS84) with
    ``plane.forward``. Its error is the 14 m residual that sets the matching
    tolerance's floor.

    ``routers.geojson`` is imported here and nowhere else — see the module
    docstring. An import failure is a REFUSE finding naming the error, not an
    exception at collection time.
    """
    findings: list[DiffFinding] = []
    try:
        from routers.geojson import (  # noqa: PLC0415 - deliberately lazy, see docstring
            DEFAULT_MAP_OFFSET_X,
            DEFAULT_MAP_OFFSET_Y,
            DEFAULT_MAP_ROTATION_DEG,
            DEFAULT_MAP_SCALE_X,
            DEFAULT_MAP_SCALE_Y,
            GeoTransform,
        )
    except Exception as exc:  # pragma: no cover - only on a stripped install
        return None, [
            DiffFinding(
                severity=DiffSeverity.REFUSE,
                code="georeference_unavailable",
                message=(
                    f"{label}: the georeference in backend/routers/geojson.py could not be "
                    f"imported ({exc}), so the consultant network cannot be placed in the "
                    "same frame as the draft. Refusing rather than comparing two frames."
                ),
                messages={
                    "ko": f"{label}: geojson.py 좌표 변환을 불러오지 못해 동일 좌표계 비교가 불가합니다 ({exc}).",
                    "en": (
                        f"{label}: could not import the georeference from geojson.py ({exc}); "
                        "the two networks cannot be placed in one frame."
                    ),
                    "ja": f"{label}: geojson.py の座標変換を読み込めず、同一座標系での比較ができません ({exc})。",
                },
                subject=label,
                context={"error": str(exc)},
            )
        ]

    try:
        root = ET.parse(Path(path)).getroot()
    except (ET.ParseError, OSError) as exc:
        return None, [
            DiffFinding(
                severity=DiffSeverity.REFUSE,
                code="georeference_source_unreadable",
                message=f"{label}: could not read the network's reference points — {exc}",
                subject=label,
                context={"path": str(path), "error": str(exc)},
            )
        ]

    net_para = root.find(".//netPara")
    ref_map = net_para.find("refPointMap") if net_para is not None else None
    ref_net = net_para.find("refPointNet") if net_para is not None else None
    if net_para is None or ref_map is None or ref_net is None:
        return None, [
            DiffFinding(
                severity=DiffSeverity.REFUSE,
                code="georeference_reference_points_missing",
                message=(
                    f"{label}: no <netPara> refPointMap/refPointNet, so the Helmert bridge has "
                    "no anchor and the network cannot be placed in the common frame. This is a "
                    "refusal, not a zero-offset guess."
                ),
                messages={
                    "ko": f"{label}: refPointMap/refPointNet가 없어 좌표계 정합이 불가합니다 — 0 오프셋으로 추정하지 않고 거부합니다.",
                    "en": (
                        f"{label}: no refPointMap/refPointNet anchor, so the network cannot be "
                        "placed in the common frame."
                    ),
                    "ja": f"{label}: refPointMap/refPointNet がなく共通座標系に配置できません。",
                },
                subject=label,
                context={"path": str(path)},
            )
        ]

    def _f(el: ET.Element, attr: str) -> float:
        try:
            return float(el.get(attr, "0") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    transform = GeoTransform(
        ref_map_x=_f(ref_map, "x"),
        ref_map_y=_f(ref_map, "y"),
        ref_net_x=_f(ref_net, "x"),
        ref_net_y=_f(ref_net, "y"),
        north_dir=_f(net_para, "northDir"),
    )
    params = dict(
        offset_x=DEFAULT_MAP_OFFSET_X,
        offset_y=DEFAULT_MAP_OFFSET_Y,
        rotation_deg=DEFAULT_MAP_ROTATION_DEG,
        scale_x=DEFAULT_MAP_SCALE_X,
        scale_y=DEFAULT_MAP_SCALE_Y,
    )

    def bridge(point: Point) -> Point:
        lat, lon = transform.to_wgs84(point[0], point[1], **params)
        return plane.forward(lat, lon)

    findings.append(
        DiffFinding(
            severity=DiffSeverity.INFO,
            code="frame_bridge_applied",
            message=(
                f"{label}: Vissim local coordinates were carried into the draft's tangent plane "
                f"through the Helmert fit in backend/routers/geojson.py (max residual "
                f"~{GEOREF_RESIDUAL_M:g} m) and project.LocalTangentPlane about origin "
                f"lat {plane.lat0_deg:.6f}, lon {plane.lon0_deg:.6f}."
            ),
            messages={
                "ko": (
                    f"{label}: geojson.py Helmert 변환(최대 잔차 약 {GEOREF_RESIDUAL_M:g} m)과 "
                    f"기준점 위도 {plane.lat0_deg:.6f}, 경도 {plane.lon0_deg:.6f}의 접평면을 통해 "
                    "동일 좌표계로 옮겼습니다."
                ),
                "en": (
                    f"{label}: bridged into the draft's tangent plane via the geojson.py Helmert "
                    f"fit (max residual ~{GEOREF_RESIDUAL_M:g} m)."
                ),
                "ja": (
                    f"{label}: geojson.py の Helmert 変換(最大残差約 {GEOREF_RESIDUAL_M:g} m)を経て "
                    "共通座標系へ変換しました。"
                ),
            },
            subject=label,
            context={
                "max_residual_m": GEOREF_RESIDUAL_M,
                "origin_lat_deg": plane.lat0_deg,
                "origin_lon_deg": plane.lon0_deg,
                "basis": POSITION_BASIS,
            },
        )
    )
    return bridge, findings


def georeferenced_network(
    path: str | Path, plane: LocalTangentPlane, *, label: str
) -> tuple[GeoNetwork | None, list[DiffFinding]]:
    """Load a consultant ``.inpx`` and bridge it into the common metre frame."""
    topology, findings = load_topology(path, label=label)
    bridge, bridge_findings = helmert_bridge(path, plane, label=label)
    findings = findings + bridge_findings
    if topology is None or bridge is None:
        return None, findings
    findings = findings + node_membership_findings(path, topology, label=label)
    return (
        GeoNetwork(
            label=label,
            source=topology.source,
            frame=(
                "geojson.py Helmert fit -> WGS84 -> project.LocalTangentPlane about "
                f"lat {plane.lat0_deg:.6f}, lon {plane.lon0_deg:.6f}"
            ),
            objects=_geo_objects(topology, bridge),
            summary=topology.summary(),
        ),
        findings,
    )


# ---------------------------------------------------------------------------
# Structured result
# ---------------------------------------------------------------------------


class MatchBucket(str, Enum):
    MATCHED = "matched"
    MISSING = "missing_from_draft"
    AMBIGUOUS = "ambiguous"


class MissingReason(str, Enum):
    #: Nothing generated came within tolerance of this object at all.
    NO_CANDIDATES = "no_candidates"
    #: Something came close, but nothing that ran the same way over enough of it.
    NO_DIRECTED_MEMBER = "no_directed_member"
    #: Members exist but no single chain accounts for enough of the object.
    NO_COVERING_CHAIN = "no_covering_chain"


class GeneratedScope(str, Enum):
    MATCHED = "matched"
    IN_CORRIDOR_UNMATCHED = "in_corridor_unmatched"
    OUT_OF_SCOPE = "out_of_scope"


class ChainMember(BaseModel):
    """One generated object inside a chain. Named, kinded, lane-counted — never
    positioned."""

    model_config = ConfigDict(extra="forbid")

    id: str
    kind: str
    lanes: int


class MatchedPair(BaseModel):
    """One consultant object and the chain of generated objects that carries it.

    Note what is absent: there is no distance, offset or bearing field here, and
    that absence is load-bearing. Distances decided this pair; the georeference
    is not good enough to report them.
    """

    model_config = ConfigDict(extra="forbid")

    consultant_id: str
    consultant_name: str = ""
    consultant_kind: str = "link"
    consultant_lanes: int = 0
    chain: list[ChainMember] = Field(default_factory=list)
    chain_lanes_min: int = 0
    chain_lanes_max: int = 0
    lane_comparable: bool = False
    lane_agreement: bool = False
    lane_within_chain_range: bool = False
    lane_varies_along_chain: bool = False
    shorter_than_tolerance: bool = Field(
        default=False,
        description=(
            "The consultant object is shorter than the matching tolerance, so its whole "
            "extent sits inside the positional uncertainty: this match was decided by "
            "direction and adjacency, not by position. A flag, not a measurement."
        ),
    )

    @property
    def chain_ids(self) -> list[str]:
        return [m.id for m in self.chain]

    @property
    def chain_label(self) -> str:
        return " + ".join(f"{m.id}{'c' if m.kind == 'connector' else ''}" for m in self.chain)


class MissingObject(BaseModel):
    """A consultant object the draft does not contain.

    R1.6's actual product. Turn pockets, storage lanes and slip roads that OSM
    does not carry land here, and so does anything the generator dropped —
    ``reason`` is what tells those apart from "nothing was near it at all".
    """

    model_config = ConfigDict(extra="forbid")

    consultant_id: str
    consultant_name: str = ""
    consultant_kind: str = "link"
    consultant_lanes: int = 0
    reason: MissingReason = MissingReason.NO_CANDIDATES
    candidate_count: int = 0
    best_chain_coverage_pct: int = Field(
        default=0,
        description=(
            "Percent of the consultant object's length the best candidate chain accounted "
            "for. A share of the object's own length, never a distance."
        ),
    )


class AmbiguousObject(BaseModel):
    """Two or more candidate chains, laterally separated, each covering enough.

    Never force-picked. Whichever one is right, choosing it here would put a
    guess into a benchmark that exists to measure guesses.
    """

    model_config = ConfigDict(extra="forbid")

    consultant_id: str
    consultant_name: str = ""
    consultant_kind: str = "link"
    candidates: list[list[ChainMember]] = Field(default_factory=list)

    @property
    def candidate_labels(self) -> list[str]:
        return [
            " + ".join(f"{m.id}{'c' if m.kind == 'connector' else ''}" for m in chain)
            for chain in self.candidates
        ]


class CorridorScope(BaseModel):
    """The generated side, split into what is in the study corridor and what is
    simply somewhere else in the OSM extract."""

    model_config = ConfigDict(extra="forbid")

    buffer_m: float = 0.0
    generated_total: int = 0
    matched_ids: list[str] = Field(default_factory=list)
    in_corridor_unmatched: list[str] = Field(default_factory=list)
    out_of_scope_count: int = 0

    @property
    def matched_count(self) -> int:
        return len(self.matched_ids)

    @property
    def in_corridor_total(self) -> int:
        return self.matched_count + len(self.in_corridor_unmatched)

    def scope_of(self, generated_id: str) -> GeneratedScope:
        """Which of the three generated-side buckets an object fell in.

        ``out_of_scope`` is the residue and is only counted, never listed: on
        the real pair it is most of an OSM extract, and a report that listed
        1 700 side streets as failures would be useless.
        """
        if generated_id in set(self.matched_ids):
            return GeneratedScope.MATCHED
        if generated_id in set(self.in_corridor_unmatched):
            return GeneratedScope.IN_CORRIDOR_UNMATCHED
        return GeneratedScope.OUT_OF_SCOPE


class LaneAgreement(BaseModel):
    """Lane counts on matched pairs only. Nothing else has a counterpart to
    disagree with.

    Compared **like with like**: a consultant link against the chain's plain-link
    members, a consultant connector against its connector members. A generated
    connector's lane count is a generator convention (it writes one connector per
    movement), not a statement about how many lanes the road has, so scoring a
    3-lane consultant carriageway against a 1-lane intersection connector inside
    its chain would measure the convention and call it an error. A matched pair
    with no same-kind member is ``not_comparable`` and is kept out of the
    denominator rather than counted as a disagreement.
    """

    model_config = ConfigDict(extra="forbid")

    compared: int = 0
    agree: int = 0
    within_chain_range: int = 0
    varies_along_chain: int = 0
    not_comparable: int = 0

    @property
    def rate(self) -> float | None:
        return round(self.agree / self.compared, 4) if self.compared else None


class SignalScopeGap(BaseModel):
    """Signalized approaches: the consultant has them, the generator does not
    emit any. Stated once, as scope."""

    model_config = ConfigDict(extra="forbid")

    consultant_signal_heads: int = 0
    consultant_signalized_links: int = 0
    generated_signal_heads: int = 0
    note: str = SIGNAL_SCOPE_NOTE


class BenchmarkMethods(BaseModel):
    """Every tolerance this run used, and where each number came from.

    In the payload rather than only in the rendering, so a consumer cannot show
    a match rate without being able to show what it was measured with.
    """

    model_config = ConfigDict(extra="forbid")

    tolerance_m: float = MATCH_TOLERANCE_M
    georef_residual_m: float = GEOREF_RESIDUAL_M
    projection_budget_m: float = PROJECTION_BUDGET_M
    centreline_allowance_m: float = CENTRELINE_ALLOWANCE_M
    corridor_buffer_m: float = MATCH_TOLERANCE_M * CORRIDOR_BUFFER_FACTOR
    bearing_band_deg: float = BEARING_BAND_DEG
    min_member_coverage: float = MIN_MEMBER_COVERAGE
    min_chain_coverage: float = MIN_CHAIN_COVERAGE
    lateral_cluster_gap_m: float = LATERAL_CLUSTER_GAP_M
    sample_step_m: float = SAMPLE_STEP_M
    tolerance_basis: str = TOLERANCE_BASIS
    georeference_basis: str = POSITION_BASIS
    position_note: str = NO_POSITION_DELTA_NOTE
    consultant_frame: str = ""
    generated_frame: str = ""


class TopologicalBenchmark(BaseModel):
    """The whole correspondence.

    ``refused`` true means no correspondence was computed and every section is
    ``None`` — the same contract :class:`services.genesis.diff.StructuralDiff`
    makes, so a refusal can never be read as "nothing was missing".
    """

    model_config = ConfigDict(extra="forbid")

    refused: bool = False
    consultant: NetworkSummary | None = None
    generated: NetworkSummary | None = None
    matched: list[MatchedPair] | None = None
    missing: list[MissingObject] | None = None
    ambiguous: list[AmbiguousObject] | None = None
    corridor: CorridorScope | None = None
    lanes: LaneAgreement | None = None
    signals: SignalScopeGap | None = None
    methods: BenchmarkMethods = Field(default_factory=BenchmarkMethods)
    findings: list[DiffFinding] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.refused

    @property
    def consultant_object_count(self) -> int:
        if self.refused:
            return 0
        return len(self.matched or []) + len(self.missing or []) + len(self.ambiguous or [])

    def match_rate(self, kind: str | None = None) -> float | None:
        """Share of consultant objects that matched. ``kind`` narrows it to
        ``"link"`` or ``"connector"``; ``None`` is everything."""
        if self.refused:
            return None

        def _count(items: Iterable[Any]) -> int:
            return sum(1 for item in items if kind is None or item.consultant_kind == kind)

        total = (
            _count(self.matched or [])
            + _count(self.missing or [])
            + _count(self.ambiguous or [])
        )
        if not total:
            return None
        return round(_count(self.matched or []) / total, 4)

    def finding_codes(self, severity: DiffSeverity | None = None) -> list[str]:
        return [f.code for f in self.findings if severity is None or f.severity is severity]

    def bucket_of(self, consultant_id: str) -> MatchBucket | None:
        """Which bucket one consultant object fell in — ``None`` if it is not in
        this benchmark at all, which :meth:`partition_check` forbids."""
        if any(p.consultant_id == consultant_id for p in (self.matched or [])):
            return MatchBucket.MATCHED
        if any(m.consultant_id == consultant_id for m in (self.missing or [])):
            return MatchBucket.MISSING
        if any(a.consultant_id == consultant_id for a in (self.ambiguous or [])):
            return MatchBucket.AMBIGUOUS
        return None

    def partition_check(self, consultant_ids: Iterable[str]) -> tuple[bool, str]:
        """matched + missing + ambiguous == every consultant object, exactly once."""
        expected = set(consultant_ids)
        seen: list[str] = [p.consultant_id for p in (self.matched or [])]
        seen += [m.consultant_id for m in (self.missing or [])]
        seen += [a.consultant_id for a in (self.ambiguous or [])]
        duplicates = sorted({i for i in seen if seen.count(i) > 1}, key=_id_sort_key)
        lost = sorted(expected - set(seen), key=_id_sort_key)
        extra = sorted(set(seen) - expected, key=_id_sort_key)
        if duplicates or lost or extra:
            return False, (
                f"bucket partition broken: {len(lost)} consultant object(s) in no bucket "
                f"{lost[:5]}, {len(duplicates)} in more than one {duplicates[:5]}, "
                f"{len(extra)} bucketed but not in the consultant network {extra[:5]}"
            )
        return True, f"all {len(expected)} consultant object(s) in exactly one bucket"


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Indexed:
    """A generated object prepared for matching: its densified samples once,
    not once per consultant object."""

    obj: GeoObject
    samples: tuple[Point, ...]


@dataclass
class _Member:
    """A generated object admitted to a consultant object's candidate set."""

    obj: GeoObject
    covered: tuple[float, float]  # arc interval on the consultant object
    lateral: float  # mean signed offset, metres (internal only — never reported)


class _Grid:
    """Uniform-cell point index. Enough for 5 197 x 312 objects; a k-d tree would
    be faster and would add a dependency this package does not have."""

    def __init__(self, cell: float) -> None:
        self.cell = max(cell, 1.0)
        self._cells: dict[tuple[int, int], list[tuple[Point, str]]] = {}

    def add(self, point: Point, key: str) -> None:
        self._cells.setdefault(self._key(point), []).append((point, key))

    def _key(self, point: Point) -> tuple[int, int]:
        return (int(math.floor(point[0] / self.cell)), int(math.floor(point[1] / self.cell)))

    def near(self, point: Point, radius: float) -> set[str]:
        found: set[str] = set()
        for candidate, key in self._sweep(point, radius):
            if math.dist(candidate, point) <= radius:
                found.add(key)
        return found

    def any_within(self, point: Point, radius: float) -> bool:
        for candidate, _key in self._sweep(point, radius):
            if math.dist(candidate, point) <= radius:
                return True
        return False

    def _sweep(self, point: Point, radius: float) -> Iterable[tuple[Point, str]]:
        gx, gy = self._key(point)
        reach = int(math.ceil(radius / self.cell))
        for i in range(gx - reach, gx + reach + 1):
            for j in range(gy - reach, gy + reach + 1):
                yield from self._cells.get((i, j), ())


def _local_direction(
    points: Sequence[Point], cum: Sequence[float], s_lo: float, s_hi: float
) -> tuple[float, float]:
    """The consultant object's own forward direction over an arc window.

    The window is widened to :data:`MIN_DIRECTION_WINDOW_M` about its midpoint
    when a member occupies less than that, so a short connector is compared
    against a real direction rather than against numerical noise. Always
    FORWARD along the consultant polyline: a generated link that runs the other
    way therefore reads ~180° and is rejected, which is the only thing that
    separates a carriageway from its opposite twin one lane width away.
    """
    total = cum[-1] if len(cum) > 1 else 0.0
    lo, hi = min(s_lo, s_hi), max(s_lo, s_hi)
    if hi - lo < MIN_DIRECTION_WINDOW_M:
        mid = (lo + hi) / 2.0
        half = MIN_DIRECTION_WINDOW_M / 2.0
        lo, hi = mid - half, mid + half
    lo = max(0.0, min(lo, total))
    hi = max(0.0, min(hi, total))
    if hi - lo <= 0.0:
        lo, hi = 0.0, total
    a = _point_at(points, cum, lo)
    b = _point_at(points, cum, hi)
    return (b[0] - a[0], b[1] - a[1])


def _members_for(
    target: GeoObject,
    candidates: Iterable[_Indexed],
    *,
    tolerance_m: float,
    bearing_band_deg: float,
    min_member_coverage: float,
) -> tuple[list[_Member], int]:
    """Which candidates run along ``target``, and how many merely came near it."""
    points = target.points
    cum = _cumulative(points)
    members: list[_Member] = []
    near_count = 0

    for indexed in candidates:
        samples = indexed.samples
        if not samples:
            continue
        hits = [_project(points, cum, p) for p in samples]
        inside = [h for h in hits if h.distance <= tolerance_m]
        if not inside:
            continue
        near_count += 1
        if len(inside) / len(samples) < min_member_coverage:
            continue

        s_values = [h.s for h in inside]
        covered = (min(s_values), max(s_values))
        direction = _local_direction(points, cum, hits[0].s, hits[-1].s)
        chord = (
            indexed.obj.points[-1][0] - indexed.obj.points[0][0],
            indexed.obj.points[-1][1] - indexed.obj.points[0][1],
        )
        if _angle_between(chord, direction) > bearing_band_deg:
            continue

        members.append(
            _Member(
                obj=indexed.obj,
                covered=covered,
                lateral=sum(h.lateral for h in inside) / len(inside),
            )
        )
    return members, near_count


def _cluster_by_lateral(members: Sequence[_Member], gap_m: float) -> list[list[_Member]]:
    """Split members into candidate chains by signed lateral offset.

    Single linkage on one axis: members are sorted by offset and a new cluster
    starts wherever consecutive offsets differ by more than ``gap_m``. Two
    carriageways further apart than the tolerance are two roads, and merging
    them would manufacture a chain that matches nothing on the ground.
    """
    if not members:
        return []
    ordered = sorted(members, key=lambda m: (m.lateral, _id_sort_key(m.obj.id)))
    clusters: list[list[_Member]] = [[ordered[0]]]
    for previous, current in zip(ordered, ordered[1:]):
        if current.lateral - previous.lateral > gap_m:
            clusters.append([current])
        else:
            clusters[-1].append(current)
    return clusters


def _chain_coverage(members: Sequence[_Member], target_length_m: float) -> float:
    if target_length_m <= 0.0:
        return 1.0 if members else 0.0
    return _union_length([m.covered for m in members]) / target_length_m


def _merged_intervals(members: Sequence[_Member]) -> list[tuple[float, float]]:
    if not members:
        return []
    ordered = sorted((min(a, b), max(a, b)) for a, b in (m.covered for m in members))
    merged: list[tuple[float, float]] = [ordered[0]]
    for lo, hi in ordered[1:]:
        last_lo, last_hi = merged[-1]
        if lo > last_hi:
            merged.append((lo, hi))
        else:
            merged[-1] = (last_lo, max(last_hi, hi))
    return merged


def _carrying_chain(members: Sequence[_Member]) -> list[_Member]:
    """The smallest subset of a cluster that accounts for everything the cluster
    accounts for — the objects that actually CARRY the consultant link.

    Without this the chain is "every generated object admissible anywhere along
    the consultant object", which on the real pair meant a 63 m consultant link
    naming thirty-odd members, most of them intersection connectors lying inside
    a stretch some other member already covered. Those members are near the road;
    they are not what carries it, and naming them wrecked the lane statistics
    (any chain containing one 1-lane connector reads as "lanes vary 1..4").

    Classic greedy interval cover, run per connected component of the members'
    arc coverage: at each position take the member that reaches furthest, ties
    broken by id so two runs agree. Optimal for full coverage of a component, so
    the reduced chain covers exactly what the cluster did and the bucket decision
    is untouched.
    """
    if not members:
        return []
    chosen: list[_Member] = []
    for lo, hi in _merged_intervals(members):
        position = lo
        pool = [
            m
            for m in members
            if max(m.covered) > lo + 1e-9 and min(m.covered) < hi - 1e-9
        ]
        while position < hi - 1e-9:
            reachable = [
                m
                for m in pool
                if min(m.covered) <= position + 1e-9
                and max(m.covered) > position + MIN_CHAIN_GAIN_M
            ]
            if not reachable:
                # No member extends the cover by a resolvable amount: take the
                # furthest-reaching one that still advances at all, or stop.
                reachable = [m for m in pool if min(m.covered) <= position + 1e-9
                             and max(m.covered) > position + 1e-9]
                if not reachable:
                    break
            best = max(reachable, key=lambda m: (max(m.covered), _id_sort_key(m.obj.id)))
            chosen.append(best)
            pool.remove(best)
            position = max(best.covered)
    return chosen


def _ordered_chain(members: Sequence[_Member]) -> list[ChainMember]:
    """Chain members in travel order along the consultant object — the order a
    modeller would drive them, not the order the ids happen to fall in."""
    ordered = sorted(members, key=lambda m: (m.covered[0], _id_sort_key(m.obj.id)))
    return [
        ChainMember(id=m.obj.id, kind=m.obj.kind, lanes=m.obj.lanes) for m in ordered
    ]


def benchmark_networks(
    consultant: GeoNetwork,
    generated: GeoNetwork,
    *,
    tolerance_m: float = MATCH_TOLERANCE_M,
    corridor_buffer_m: float | None = None,
    bearing_band_deg: float = BEARING_BAND_DEG,
    min_member_coverage: float = MIN_MEMBER_COVERAGE,
    min_chain_coverage: float = MIN_CHAIN_COVERAGE,
    lateral_cluster_gap_m: float | None = None,
    sample_step_m: float = SAMPLE_STEP_M,
) -> TopologicalBenchmark:
    """Match two networks in a common metre frame. Pure, total, never positional.

    ``corridor_buffer_m`` and ``lateral_cluster_gap_m`` default to
    :data:`CORRIDOR_BUFFER_FACTOR` x ``tolerance_m`` and to ``tolerance_m``, so a
    caller who moves the tolerance moves the whole method with it rather than
    leaving three constants disagreeing about what "close" means.
    """
    buffer_m = (
        tolerance_m * CORRIDOR_BUFFER_FACTOR if corridor_buffer_m is None else corridor_buffer_m
    )
    gap_m = tolerance_m if lateral_cluster_gap_m is None else lateral_cluster_gap_m

    findings: list[DiffFinding] = []
    methods = BenchmarkMethods(
        tolerance_m=tolerance_m,
        corridor_buffer_m=buffer_m,
        bearing_band_deg=bearing_band_deg,
        min_member_coverage=min_member_coverage,
        min_chain_coverage=min_chain_coverage,
        lateral_cluster_gap_m=gap_m,
        sample_step_m=sample_step_m,
        consultant_frame=consultant.frame,
        generated_frame=generated.frame,
    )

    # --- index the generated side once -------------------------------------
    indexed: dict[str, _Indexed] = {}
    grid = _Grid(cell=max(tolerance_m, sample_step_m))
    for object_id in generated.sorted_ids():
        obj = generated.objects[object_id]
        samples = _densify(obj.points, sample_step_m)
        indexed[object_id] = _Indexed(obj=obj, samples=samples)
        for sample in samples:
            grid.add(sample, object_id)

    # --- the consultant network's own extent, for corridor scoping ---------
    corridor_grid = _Grid(cell=max(buffer_m, sample_step_m))
    for object_id in consultant.sorted_ids():
        for sample in _densify(consultant.objects[object_id].points, sample_step_m):
            corridor_grid.add(sample, object_id)

    matched: list[MatchedPair] = []
    missing: list[MissingObject] = []
    ambiguous: list[AmbiguousObject] = []
    used_generated: set[str] = set()
    empty_consultant_geometry: list[str] = []

    for object_id in consultant.sorted_ids():
        target = consultant.objects[object_id]
        if len(target.points) < 2:
            # No polyline, no correspondence: cannot be matched by position and
            # must not be silently dropped from the denominator.
            empty_consultant_geometry.append(object_id)
            missing.append(
                MissingObject(
                    consultant_id=object_id,
                    consultant_name=target.name,
                    consultant_kind=target.kind,
                    consultant_lanes=target.lanes,
                    reason=MissingReason.NO_CANDIDATES,
                    candidate_count=0,
                )
            )
            continue

        candidate_ids: set[str] = set()
        for sample in _densify(target.points, sample_step_m):
            candidate_ids |= grid.near(sample, tolerance_m)
        candidates = [indexed[cid] for cid in sorted(candidate_ids, key=_id_sort_key)]

        members, near_count = _members_for(
            target,
            candidates,
            tolerance_m=tolerance_m,
            bearing_band_deg=bearing_band_deg,
            min_member_coverage=min_member_coverage,
        )

        clusters = _cluster_by_lateral(members, gap_m)
        target_length = target.length_m
        scored = [(cluster, _chain_coverage(cluster, target_length)) for cluster in clusters]
        covering = [cluster for cluster, coverage in scored if coverage >= min_chain_coverage]

        if len(covering) == 1:
            chain = _ordered_chain(_carrying_chain(covering[0]))
            same_kind = [m.lanes for m in chain if m.kind == target.kind]
            comparable = bool(same_kind)
            lanes_min, lanes_max = (min(same_kind), max(same_kind)) if comparable else (0, 0)
            matched.append(
                MatchedPair(
                    consultant_id=object_id,
                    consultant_name=target.name,
                    consultant_kind=target.kind,
                    consultant_lanes=target.lanes,
                    chain=chain,
                    chain_lanes_min=lanes_min,
                    chain_lanes_max=lanes_max,
                    lane_comparable=comparable,
                    lane_agreement=comparable and target.lanes == lanes_min == lanes_max,
                    lane_within_chain_range=(
                        comparable and lanes_min <= target.lanes <= lanes_max
                    ),
                    lane_varies_along_chain=comparable and lanes_min != lanes_max,
                    shorter_than_tolerance=target_length < tolerance_m,
                )
            )
            used_generated.update(m.id for m in chain)
        elif len(covering) > 1:
            ambiguous.append(
                AmbiguousObject(
                    consultant_id=object_id,
                    consultant_name=target.name,
                    consultant_kind=target.kind,
                    candidates=[_ordered_chain(_carrying_chain(cluster)) for cluster in covering],
                )
            )
        else:
            if not near_count:
                reason = MissingReason.NO_CANDIDATES
            elif not members:
                reason = MissingReason.NO_DIRECTED_MEMBER
            else:
                reason = MissingReason.NO_COVERING_CHAIN
            best = max((coverage for _, coverage in scored), default=0.0)
            missing.append(
                MissingObject(
                    consultant_id=object_id,
                    consultant_name=target.name,
                    consultant_kind=target.kind,
                    consultant_lanes=target.lanes,
                    reason=reason,
                    candidate_count=near_count,
                    best_chain_coverage_pct=int(round(min(best, 1.0) * 100)),
                )
            )

    # --- generated side: matched / in corridor / out of scope ---------------
    in_corridor_unmatched: list[str] = []
    out_of_scope = 0
    for object_id in generated.sorted_ids():
        if object_id in used_generated:
            continue
        samples = indexed[object_id].samples
        inside = any(corridor_grid.any_within(sample, buffer_m) for sample in samples)
        if inside:
            in_corridor_unmatched.append(object_id)
        else:
            out_of_scope += 1

    corridor = CorridorScope(
        buffer_m=buffer_m,
        generated_total=len(generated.objects),
        matched_ids=sorted(used_generated, key=_id_sort_key),
        in_corridor_unmatched=in_corridor_unmatched,
        out_of_scope_count=out_of_scope,
    )

    if corridor.in_corridor_total + corridor.out_of_scope_count != len(generated.objects):
        raise BenchmarkInvariantError(
            "generated-side buckets do not partition: "
            f"{corridor.matched_count} matched + {len(corridor.in_corridor_unmatched)} in "
            f"corridor + {corridor.out_of_scope_count} out of scope != "
            f"{len(generated.objects)} generated objects"
        )

    lanes_summary = LaneAgreement(
        compared=sum(1 for p in matched if p.lane_comparable),
        agree=sum(1 for p in matched if p.lane_agreement),
        within_chain_range=sum(1 for p in matched if p.lane_within_chain_range),
        varies_along_chain=sum(1 for p in matched if p.lane_varies_along_chain),
        not_comparable=sum(1 for p in matched if not p.lane_comparable),
    )
    signals = SignalScopeGap(
        consultant_signal_heads=consultant.summary.signal_head_count,
        consultant_signalized_links=consultant.summary.signalized_link_count,
        generated_signal_heads=generated.summary.signal_head_count,
    )

    benchmark = TopologicalBenchmark(
        refused=False,
        consultant=consultant.summary,
        generated=generated.summary,
        matched=matched,
        missing=missing,
        ambiguous=ambiguous,
        corridor=corridor,
        lanes=lanes_summary,
        signals=signals,
        methods=methods,
        findings=findings,
    )

    ok, detail = benchmark.partition_check(consultant.objects)
    if not ok:
        raise BenchmarkInvariantError(detail)

    # A matched generated object outside the corridor would mean the buffer is
    # narrower than the matching tolerance — the one way corridor scoping could
    # hide a real correspondence. Checked rather than argued.
    if buffer_m < tolerance_m:
        raise BenchmarkInvariantError(
            f"corridor buffer {buffer_m:g} m is narrower than the matching tolerance "
            f"{tolerance_m:g} m, so a matchable generated object could be scoped out"
        )

    findings.append(
        DiffFinding(
            severity=DiffSeverity.INFO,
            code="bucket_partition_verified",
            message=detail,
            messages={
                "ko": f"컨설턴트 객체 {len(consultant.objects)}개가 모두 정확히 하나의 분류에 속합니다.",
                "en": detail,
                "ja": f"コンサルタント側 {len(consultant.objects)} 件がすべて 1 つの分類に属します。",
            },
            context={
                "consultant_objects": len(consultant.objects),
                "matched": len(matched),
                "missing": len(missing),
                "ambiguous": len(ambiguous),
            },
        )
    )
    findings.append(
        DiffFinding(
            severity=DiffSeverity.INFO,
            code="geometric_correspondence_scope",
            message=(
                "Objects were matched by position and direction, not by id: the two files were "
                "authored independently and their id spaces collide. " + TOLERANCE_BASIS + " "
                + NO_POSITION_DELTA_NOTE
            ),
            messages={
                "ko": (
                    "두 네트워크는 독립적으로 작성되어 id가 충돌하므로, id가 아니라 위치와 진행 "
                    f"방향으로 대응시켰습니다. 허용 오차 {tolerance_m:g} m "
                    f"(지오레퍼런스 잔차 {GEOREF_RESIDUAL_M:g} m + 투영 예산 "
                    f"{PROJECTION_BUDGET_M:g} m + 중심선 여유 {CENTRELINE_ALLOWANCE_M:g} m). "
                    "위치 차이는 판정에만 쓰고 보고하지 않습니다."
                ),
                "en": (
                    "Matched by position and direction, not by id — the id spaces of two "
                    f"independently authored files collide. Tolerance {tolerance_m:g} m. "
                    "Position decides; position is not reported."
                ),
                "ja": (
                    "2 つのファイルは独立に作成され id が衝突するため、id ではなく位置と進行方向で "
                    f"対応付けました。許容差 {tolerance_m:g} m。位置は判定にのみ用い、報告しません。"
                ),
            },
            context={
                "tolerance_m": tolerance_m,
                "georef_residual_m": GEOREF_RESIDUAL_M,
                "projection_budget_m": PROJECTION_BUDGET_M,
                "centreline_allowance_m": CENTRELINE_ALLOWANCE_M,
                "corridor_buffer_m": buffer_m,
                "bearing_band_deg": bearing_band_deg,
                "basis": POSITION_BASIS,
            },
        )
    )

    if empty_consultant_geometry:
        findings.append(
            DiffFinding(
                severity=DiffSeverity.WARN,
                code="consultant_object_without_geometry",
                message=(
                    f"{len(empty_consultant_geometry)} consultant object(s) carry fewer than two "
                    "polyline points and cannot be matched by position; they are counted as "
                    f"missing rather than dropped ({', '.join(empty_consultant_geometry[:5])}"
                    f"{', ...' if len(empty_consultant_geometry) > 5 else ''})."
                ),
                messages={
                    "ko": (
                        f"폴리라인 점이 2개 미만인 컨설턴트 객체 {len(empty_consultant_geometry)}개는 "
                        "위치로 대응시킬 수 없어 누락으로 계상했습니다(제외하지 않음)."
                    ),
                    "en": (
                        f"{len(empty_consultant_geometry)} consultant object(s) have no usable "
                        "polyline; counted as missing, not dropped."
                    ),
                    "ja": (
                        f"ポリライン点が 2 未満のコンサルタント側 {len(empty_consultant_geometry)} 件は "
                        "位置照合できないため欠落として計上しました。"
                    ),
                },
                context={"ids": empty_consultant_geometry},
            )
        )

    if ambiguous:
        findings.append(
            DiffFinding(
                severity=DiffSeverity.WARN,
                code="ambiguous_correspondence",
                message=(
                    f"{len(ambiguous)} consultant object(s) have more than one candidate chain "
                    f"further apart than {gap_m:g} m laterally. Every candidate is named and "
                    "none was picked: forcing a choice would put a guess into the match rate."
                ),
                messages={
                    "ko": (
                        f"컨설턴트 객체 {len(ambiguous)}개에 대해 횡방향 {gap_m:g} m 이상 떨어진 "
                        "후보 체인이 둘 이상입니다. 후보를 모두 제시하고 임의 선택하지 않았습니다."
                    ),
                    "en": (
                        f"{len(ambiguous)} consultant object(s) have multiple separated candidate "
                        "chains; all are named, none was picked."
                    ),
                    "ja": (
                        f"コンサルタント側 {len(ambiguous)} 件で候補チェーンが複数あります。"
                        "すべて提示し、いずれも選択していません。"
                    ),
                },
                context={"ids": [a.consultant_id for a in ambiguous]},
            )
        )

    short_objects = [
        object_id
        for object_id in consultant.sorted_ids()
        if consultant.objects[object_id].length_m < tolerance_m
    ]
    if short_objects:
        findings.append(
            DiffFinding(
                severity=DiffSeverity.WARN,
                code="object_shorter_than_tolerance",
                message=(
                    f"{len(short_objects)} of {len(consultant.objects)} consultant object(s) are "
                    f"shorter than the {tolerance_m:g} m matching tolerance, so their whole extent "
                    "lies inside the positional uncertainty. Their buckets were decided by "
                    "direction and adjacency rather than by position, and they should be read as "
                    "weaker evidence than the long links. Most are connectors, which are short by "
                    "nature — this is a property of the georeference, not of the draft."
                ),
                messages={
                    "ko": (
                        f"컨설턴트 객체 {len(consultant.objects)}개 중 {len(short_objects)}개는 "
                        f"길이가 허용 오차 {tolerance_m:g} m보다 짧아 객체 전체가 위치 불확실성 안에 "
                        "들어갑니다. 이 객체들의 분류는 위치가 아니라 진행 방향과 인접성으로 정해졌으며, "
                        "긴 링크보다 약한 근거로 읽어야 합니다."
                    ),
                    "en": (
                        f"{len(short_objects)} of {len(consultant.objects)} consultant objects are "
                        f"shorter than the {tolerance_m:g} m tolerance; their buckets rest on "
                        "direction and adjacency, not position."
                    ),
                    "ja": (
                        f"コンサルタント側 {len(consultant.objects)} 件のうち {len(short_objects)} 件は "
                        f"許容差 {tolerance_m:g} m より短く、判定は位置ではなく方向と近接性に依存します。"
                    ),
                },
                context={
                    "count": len(short_objects),
                    "of": len(consultant.objects),
                    "tolerance_m": tolerance_m,
                },
            )
        )

    if lanes_summary.not_comparable:
        findings.append(
            DiffFinding(
                severity=DiffSeverity.INFO,
                code="lane_count_not_comparable",
                message=(
                    f"{lanes_summary.not_comparable} matched pair(s) have no chain member of the "
                    "same kind (a consultant link carried only by generated connectors, or a "
                    "consultant connector carried only by generated links), so their lane counts "
                    "are not comparable and are outside the agreement denominator rather than "
                    "counted as disagreements."
                ),
                messages={
                    "ko": (
                        f"일치 쌍 {lanes_summary.not_comparable}개는 같은 종류의 체인 구성원이 없어 "
                        "차로 수를 비교할 수 없습니다. 불일치로 계상하지 않고 분모에서 제외했습니다."
                    ),
                    "en": (
                        f"{lanes_summary.not_comparable} matched pair(s) have no same-kind chain "
                        "member; lane counts are not comparable and are excluded, not failed."
                    ),
                    "ja": (
                        f"一致ペア {lanes_summary.not_comparable} 件は同種のチェーン構成員がなく、"
                        "車線数を比較できないため分母から除外しました(不一致ではありません)。"
                    ),
                },
                context={"not_comparable": lanes_summary.not_comparable},
            )
        )

    if signals.consultant_signal_heads and not signals.generated_signal_heads:
        findings.append(
            DiffFinding(
                severity=DiffSeverity.INFO,
                code="signal_scope_gap",
                message=(
                    f"{consultant.label} declares {signals.consultant_signal_heads} signal head(s) "
                    f"on {signals.consultant_signalized_links} link(s); {generated.label} declares "
                    "none. " + SIGNAL_SCOPE_NOTE
                ),
                messages={
                    "ko": (
                        f"컨설턴트 모델은 신호기 {signals.consultant_signal_heads}개"
                        f"(링크 {signals.consultant_signalized_links}개)를 선언하지만 생성 모델은 "
                        "기하만 생성하므로 신호기가 없습니다. 링크별 실패가 아니라 범위 차이입니다."
                    ),
                    "en": (
                        f"{signals.consultant_signal_heads} consultant signal head(s) have no "
                        "generated counterpart: the generator is geometry-only. Scope gap, not a "
                        "per-link failure."
                    ),
                    "ja": (
                        f"コンサルタント側の信号機 {signals.consultant_signal_heads} 基に対応する "
                        "生成側の要素はありません(幾何のみ生成)。リンク単位の不合格ではなく範囲差です。"
                    ),
                },
                context={
                    "consultant_signal_heads": signals.consultant_signal_heads,
                    "consultant_signalized_links": signals.consultant_signalized_links,
                    "generated_signal_heads": signals.generated_signal_heads,
                },
            )
        )

    benchmark.findings = findings
    return benchmark


def benchmark_network_files(
    consultant_path: str | Path,
    generated_path: str | Path,
    *,
    origin_lat_deg: float,
    origin_lon_deg: float,
    consultant_label: str = "consultant",
    generated_label: str = "generated",
    **options: Any,
) -> TopologicalBenchmark:
    """Load both ``.inpx`` files, bridge the consultant into the draft's plane,
    and match. Reports, never raises (except the partition invariant).

    ``origin_lat_deg``/``origin_lon_deg`` are the generated draft's own recorded
    projection origin — ``metadata.projection.origin_lat_deg`` /
    ``origin_lon_deg`` in its ``.genesis.json``. They are required, not
    defaulted: the generated file's coordinates are metres about *that* origin
    and about no other, and guessing an origin would silently mis-place an
    entire network.
    """
    plane = LocalTangentPlane(lat0_deg=origin_lat_deg, lon0_deg=origin_lon_deg)
    consultant, consultant_findings = georeferenced_network(
        consultant_path, plane, label=consultant_label
    )
    generated, generated_findings = local_frame_network(generated_path, label=generated_label)
    load_findings = consultant_findings + generated_findings

    if consultant is None or generated is None:
        return TopologicalBenchmark(refused=True, findings=load_findings)

    benchmark = benchmark_networks(consultant, generated, **options)
    benchmark.findings = load_findings + benchmark.findings
    return benchmark


# ---------------------------------------------------------------------------
# Rendering — separate from the computation, the same way diff.py separates
# them, so roadmap row 2.4 can consume the structured half directly.
#
# ko primary, en/ja alongside (CLAUDE.md's user-facing-strings rule). diff.py
# renders English only and carries ko/ja on the findings; here the section
# headers are translated too, because this report is the R1.6 deliverable a
# Korean-reading consultant actually opens.
# ---------------------------------------------------------------------------

_SEVERITY_TAG = {
    DiffSeverity.REFUSE: "REFUSE",
    DiffSeverity.WARN: "WARN  ",
    DiffSeverity.INFO: "INFO  ",
}

#: Long lists are truncated in the TEXT rendering only; the structured payload
#: always carries every id.
_MAX_LISTED = 40

_TEXT: dict[str, dict[str, str]] = {
    "title": {
        "ko": "Murdoch 벤치마크 v2 — 위상 대응 리포트",
        "en": "Murdoch benchmark v2 — topological correspondence report",
        "ja": "Murdoch ベンチマーク v2 — トポロジー対応レポート",
    },
    "refused": {
        "ko": "거부됨 — 대응 관계를 계산하지 않았습니다. 빈 결과가 아닙니다.",
        "en": "REFUSED — no correspondence was computed. This is NOT an empty result.",
        "ja": "拒否 — 対応関係は計算していません。空の結果ではありません。",
    },
    "headline": {
        "ko": "요약 — 컨설턴트 모델이 초안에 얼마나 들어 있는가",
        "en": "Headline — how much of the consultant model the draft contains",
        "ja": "要約 — コンサルタントモデルが草案にどれだけ含まれるか",
    },
    "matched_links": {
        "ko": "일치한 컨설턴트 링크",
        "en": "consultant links matched",
        "ja": "一致したコンサルタントリンク",
    },
    "matched_connectors": {
        "ko": "일치한 컨설턴트 커넥터",
        "en": "consultant connectors matched",
        "ja": "一致したコンサルタントコネクタ",
    },
    "matched_all": {
        "ko": "일치한 컨설턴트 객체 전체",
        "en": "all consultant objects matched",
        "ja": "コンサルタント客体 全体",
    },
    "lane_rate": {
        "ko": "차로 수 일치율(일치한 쌍 기준)",
        "en": "lane-count agreement (matched pairs only)",
        "ja": "車線数一致率(一致ペアのみ)",
    },
    "missing_head": {
        "ko": "초안에 없는 컨설턴트 객체 — R1.6의 실제 산출물",
        "en": "Missing from the draft — R1.6's actual product",
        "ja": "草案に存在しないコンサルタント客体 — R1.6 の実成果",
    },
    "missing_empty": {
        "ko": "없음 — 모든 컨설턴트 객체가 초안에서 대응 체인을 찾았습니다.",
        "en": "none — every consultant object found a chain in the draft.",
        "ja": "なし — すべての客体が草案側の対応チェーンを見つけました。",
    },
    "lanes_head": {
        "ko": "차로 수 비교(일치한 쌍만)",
        "en": "Lane counts (matched pairs only)",
        "ja": "車線数の比較(一致ペアのみ)",
    },
    "ambiguous_head": {
        "ko": "모호 — 후보가 둘 이상, 임의 선택하지 않음",
        "en": "Ambiguous — more than one candidate, never force-picked",
        "ja": "曖昧 — 候補が複数、強制選択なし",
    },
    "ambiguous_empty": {
        "ko": "없음.",
        "en": "none.",
        "ja": "なし。",
    },
    "matched_head": {
        "ko": "일치 쌍 — 컨설턴트 객체 ↔ 생성 체인",
        "en": "Matched pairs — consultant object <-> generated chain",
        "ja": "一致ペア — コンサルタント客体 ↔ 生成チェーン",
    },
    "corridor_head": {
        "ko": "회랑 범위 — 생성 측 객체의 분류",
        "en": "Corridor scoping — where the generated objects went",
        "ja": "回廊スコープ — 生成側客体の分類",
    },
    "signal_head": {
        "ko": "신호 — 알려진 범위 차이",
        "en": "Signals — the known scope gap",
        "ja": "信号 — 既知の範囲差",
    },
    "methods_head": {
        "ko": "방법 및 허용 오차(출처 포함)",
        "en": "Methods and tolerances (with provenance)",
        "ja": "手法と許容差(出典付き)",
    },
    "findings_head": {
        "ko": "관찰 사항",
        "en": "Findings",
        "ja": "所見",
    },
    "totals_head": {
        "ko": "총계",
        "en": "Totals",
        "ja": "総計",
    },
    "col_consultant": {"ko": "컨설턴트", "en": "consultant", "ja": "コンサルタント"},
    "col_generated": {"ko": "생성", "en": "generated", "ja": "生成"},
    "reason": {"ko": "사유", "en": "reason", "ja": "理由"},
    "road_name": {"ko": "도로명", "en": "road name", "ja": "道路名"},
    "unnamed": {"ko": "(이름 없음)", "en": "(unnamed)", "ja": "(名称なし)"},
    "matched_into_chain": {
        "ko": "체인에 사용된 생성 객체",
        "en": "generated objects used in a chain",
        "ja": "チェーンに使用された生成客体",
    },
    "in_corridor_unmatched": {
        "ko": "회랑 안이지만 미대응 — 컨설턴트가 모델링하지 않은 도로이거나 컨설턴트가 단순화한 형상",
        "en": "in corridor, unmatched — road the consultant chose not to model, or geometry it simplified",
        "ja": "回廊内で未対応 — コンサルタントが対象外とした道路、または簡略化した形状",
    },
    "out_of_scope": {
        "ko": "회랑 밖 — 집계만 하고 실패로 보지 않음",
        "en": "out of scope — counted, never a failure",
        "ja": "回廊外 — 集計のみ、不合格ではない",
    },
    "not_comparable": {
        "ko": "비교 불가(같은 종류의 체인 구성원 없음)",
        "en": "not comparable (no same-kind chain member)",
        "ja": "比較不可(同種のチェーン構成員なし)",
    },
    "short_objects": {
        "ko": "허용 오차보다 짧은 컨설턴트 객체(위치가 아니라 방향·인접성으로 판정): 일치 {matched}건 중 {count}건",
        "en": (
            "consultant objects shorter than the tolerance (decided by direction and "
            "adjacency, not position): {count} of {matched} matched"
        ),
        "ja": "許容差より短いコンサルタント客体(位置ではなく方向・近接性で判定): 一致 {matched} 件中 {count} 件",
    },
    "within_range": {
        "ko": "체인 최소~최대 범위 안",
        "en": "within chain min..max",
        "ja": "チェーンの最小〜最大の範囲内",
    },
    "lanes_vary": {
        "ko": "체인 안에서 차로 수가 변하는 경우",
        "en": "chains whose lanes vary",
        "ja": "チェーン内で車線数が変わる件数",
    },
    "signal_line": {
        "ko": "컨설턴트 신호기 {heads}개(링크 {links}개) / 생성 {generated}개",
        "en": "consultant signal heads: {heads} on {links} link(s); generated: {generated}",
        "ja": "コンサルタント側 信号機 {heads} 基(リンク {links} 本)/ 生成側 {generated} 基",
    },
    "m_tolerance": {"ko": "매칭 허용 오차", "en": "matching tolerance", "ja": "マッチング許容差"},
    "m_buffer": {"ko": "회랑 버퍼", "en": "corridor buffer", "ja": "回廊バッファ"},
    "m_band": {"ko": "방향 허용 범위", "en": "direction band", "ja": "方向許容範囲"},
    "m_member": {"ko": "구성원 커버리지", "en": "member coverage", "ja": "構成員カバレッジ"},
    "m_chain": {"ko": "체인 커버리지", "en": "chain coverage", "ja": "チェーンカバレッジ"},
    "m_gap": {"ko": "횡방향 군집 간격", "en": "lateral cluster gap", "ja": "横方向クラスタ間隔"},
    "m_step": {"ko": "폴리라인 표본 간격", "en": "polyline sample step", "ja": "ポリライン標本間隔"},
    "m_derivation": {"ko": "허용 오차 산출 근거", "en": "tolerance derivation", "ja": "許容差の導出"},
    "m_georef": {"ko": "지오레퍼런스", "en": "georeference", "ja": "ジオリファレンス"},
    "m_consultant_frame": {"ko": "컨설턴트 좌표계", "en": "consultant frame", "ja": "コンサルタント座標系"},
    "m_generated_frame": {"ko": "생성 좌표계", "en": "generated frame", "ja": "生成座標系"},
    "t_links": {"ko": "일반 링크", "en": "plain links", "ja": "通常リンク"},
    "t_connectors": {"ko": "커넥터", "en": "connectors", "ja": "コネクタ"},
    "t_nodes": {"ko": "노드", "en": "nodes", "ja": "ノード"},
    "t_movements": {"ko": "이동류", "en": "movements", "ja": "移動流"},
    "t_signal_heads": {"ko": "신호기", "en": "signal heads", "ja": "信号機"},
    "no_position": {
        "ko": "위치 차이는 어디에도 보고하지 않습니다(판정에만 사용).",
        "en": "No positional delta is reported anywhere (position decides, it does not report).",
        "ja": "位置差はどこにも報告しません(判定にのみ使用)。",
    },
}

_REASON_TEXT: dict[MissingReason, dict[str, str]] = {
    MissingReason.NO_CANDIDATES: {
        "ko": "허용 오차 안에 생성 객체가 전혀 없음",
        "en": "nothing generated came within tolerance",
        "ja": "許容差内に生成客体がまったくない",
    },
    MissingReason.NO_DIRECTED_MEMBER: {
        "ko": "가까운 객체는 있으나 같은 방향으로 충분히 겹치는 것이 없음",
        "en": "something was near, but nothing ran the same way over enough of it",
        "ja": "近接客体はあるが同方向に十分重なるものがない",
    },
    MissingReason.NO_COVERING_CHAIN: {
        "ko": "체인이 컨설턴트 객체 길이를 충분히 설명하지 못함",
        "en": "no single chain accounted for enough of the object",
        "ja": "単一チェーンで客体長を十分に説明できない",
    },
}


def _display_width(text: str) -> int:
    """Terminal columns a string occupies. CJK glyphs take two.

    Without this the ko/ja section rules come out half the length of the
    headings they sit under, which reads as a rendering bug in the one
    deliverable a Korean-reading consultant actually opens.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _rule(text: str, char: str = "-") -> str:
    return char * _display_width(text)


def _pct(part: int, whole: int) -> str:
    return f"{part}/{whole}" + (f" ({part / whole * 100:.1f}%)" if whole else " (n/a)")


def _truncated(items: Sequence[str]) -> str:
    if not items:
        return "-"
    shown = ", ".join(items[:_MAX_LISTED])
    if len(items) > _MAX_LISTED:
        shown += f" ... (+{len(items) - _MAX_LISTED} more)"
    return shown


def _name_or_placeholder(name: str, lang: str) -> str:
    return name if name else pick_text(_TEXT["unnamed"], lang)


def _finding_text(finding: DiffFinding, lang: str) -> str:
    """ko-primary fallback for finding prose, matching :func:`pick_text`.

    An unknown language code must fall back the SAME way everywhere, or the
    report ends up half Korean and half English. Findings that carry no
    ``messages`` at all — the load refusals inherited from ``diff.py`` — fall
    back to their English ``message``, which is the only text they have.
    """
    return finding.messages.get(lang) or finding.messages.get("ko") or finding.message


def _counts_by_kind(items: Sequence[Any], kind: str) -> int:
    return sum(1 for item in items if getattr(item, "consultant_kind", None) == kind)


def render_benchmark_report(
    benchmark: TopologicalBenchmark, *, lang: str = "ko", title: str | None = None
) -> str:
    """Human-readable R1.6 v2 report. ko primary; ``lang`` also takes en/ja.

    Leads with the numbers a consultant asks for first — what share of their
    model the draft reproduced, what it is missing and by what name, whether the
    lane counts agree — and puts the method at the bottom where it can be
    checked but does not delay the answer.
    """
    heading = title or pick_text(_TEXT["title"], lang)
    lines: list[str] = [heading, _rule(heading, "="), ""]

    if benchmark.refused:
        lines.append(pick_text(_TEXT["refused"], lang))
        lines.append("")
        for finding in benchmark.findings:
            lines.append(
                f"  [{_SEVERITY_TAG[finding.severity]}] {finding.code}: "
                f"{_finding_text(finding, lang)}"
            )
        return "\n".join(lines)

    consultant, generated = benchmark.consultant, benchmark.generated
    matched = benchmark.matched or []
    missing = benchmark.missing or []
    ambiguous = benchmark.ambiguous or []
    corridor = benchmark.corridor
    lanes = benchmark.lanes
    methods = benchmark.methods

    if consultant is None or generated is None or corridor is None or lanes is None:
        # Unreachable through this module's constructors; handled so a
        # hand-built or deserialised object cannot crash the renderer.
        lines.append("INCOMPLETE — this object carries no correspondence and is not refused.")
        return "\n".join(lines)

    lines.append(f"{pick_text(_TEXT['col_consultant'], lang)}: {consultant.label}"
                 + (f"  ({consultant.source})" if consultant.source else ""))
    lines.append(f"{pick_text(_TEXT['col_generated'], lang)}: {generated.label}"
                 + (f"  ({generated.source})" if generated.source else ""))
    lines.append("")

    # --- headline -----------------------------------------------------------
    head = pick_text(_TEXT["headline"], lang)
    lines.append(head)
    lines.append(_rule(head))
    link_total = (
        _counts_by_kind(matched, "link")
        + _counts_by_kind(missing, "link")
        + _counts_by_kind(ambiguous, "link")
    )
    conn_total = (
        _counts_by_kind(matched, "connector")
        + _counts_by_kind(missing, "connector")
        + _counts_by_kind(ambiguous, "connector")
    )
    total = link_total + conn_total
    lines.append(
        f"  {pick_text(_TEXT['matched_links'], lang)}: "
        f"{_pct(_counts_by_kind(matched, 'link'), link_total)}"
    )
    lines.append(
        f"  {pick_text(_TEXT['matched_connectors'], lang)}: "
        f"{_pct(_counts_by_kind(matched, 'connector'), conn_total)}"
    )
    lines.append(f"  {pick_text(_TEXT['matched_all'], lang)}: {_pct(len(matched), total)}")
    lines.append(
        f"  {pick_text(_TEXT['lane_rate'], lang)}: {_pct(lanes.agree, lanes.compared)}"
    )
    lines.append(f"  {pick_text(_TEXT['missing_head'], lang)}: {len(missing)}")
    lines.append(f"  {pick_text(_TEXT['ambiguous_head'], lang)}: {len(ambiguous)}")
    lines.append(
        "  "
        + pick_text(_TEXT["short_objects"], lang).format(
            count=sum(1 for p in matched if p.shorter_than_tolerance), matched=len(matched)
        )
    )
    lines.append("")

    # --- missing ------------------------------------------------------------
    head = pick_text(_TEXT["missing_head"], lang)
    lines.append(head)
    lines.append(_rule(head))
    if not missing:
        lines.append(f"  {pick_text(_TEXT['missing_empty'], lang)}")
    else:
        for item in missing[:_MAX_LISTED]:
            lines.append(
                f"  {item.consultant_kind:>9} {item.consultant_id:>8}  "
                f"{_name_or_placeholder(item.consultant_name, lang)}  "
                f"[{pick_text(_REASON_TEXT[item.reason], lang)}; "
                f"{item.candidate_count} near, best chain {item.best_chain_coverage_pct}%]"
            )
        if len(missing) > _MAX_LISTED:
            lines.append(f"  ... (+{len(missing) - _MAX_LISTED} more)")
    lines.append("")

    # --- lanes --------------------------------------------------------------
    head = pick_text(_TEXT["lanes_head"], lang)
    lines.append(head)
    lines.append(_rule(head))
    lines.append(f"  {pick_text(_TEXT['lane_rate'], lang)}: {_pct(lanes.agree, lanes.compared)}")
    lines.append(
        f"  {pick_text(_TEXT['within_range'], lang)}: "
        f"{_pct(lanes.within_chain_range, lanes.compared)}"
    )
    lines.append(f"  {pick_text(_TEXT['lanes_vary'], lang)}: {lanes.varies_along_chain}")
    lines.append(f"  {pick_text(_TEXT['not_comparable'], lang)}: {lanes.not_comparable}")
    disagreements = [p for p in matched if p.lane_comparable and not p.lane_agreement]
    for pair in disagreements[:_MAX_LISTED]:
        span = (
            f"{pair.chain_lanes_min}"
            if pair.chain_lanes_min == pair.chain_lanes_max
            else f"{pair.chain_lanes_min}..{pair.chain_lanes_max}"
        )
        lines.append(
            f"    {pair.consultant_id:>8} {_name_or_placeholder(pair.consultant_name, lang)}: "
            f"{pair.consultant_lanes} vs {span}  [{pair.chain_label}]"
        )
    if len(disagreements) > _MAX_LISTED:
        lines.append(f"    ... (+{len(disagreements) - _MAX_LISTED} more)")
    lines.append("")

    # --- ambiguous ----------------------------------------------------------
    head = pick_text(_TEXT["ambiguous_head"], lang)
    lines.append(head)
    lines.append(_rule(head))
    if not ambiguous:
        lines.append(f"  {pick_text(_TEXT['ambiguous_empty'], lang)}")
    else:
        for item in ambiguous[:_MAX_LISTED]:
            lines.append(
                f"  {item.consultant_kind:>9} {item.consultant_id:>8}  "
                f"{_name_or_placeholder(item.consultant_name, lang)}"
            )
            for label in item.candidate_labels:
                lines.append(f"      ? {label}")
        if len(ambiguous) > _MAX_LISTED:
            lines.append(f"  ... (+{len(ambiguous) - _MAX_LISTED} more)")
    lines.append("")

    # --- matched pairs ------------------------------------------------------
    head = pick_text(_TEXT["matched_head"], lang)
    lines.append(head)
    lines.append(_rule(head))
    for pair in matched[:_MAX_LISTED]:
        lines.append(
            f"  {pair.consultant_kind:>9} {pair.consultant_id:>8}  "
            f"{_name_or_placeholder(pair.consultant_name, lang)}  ->  {pair.chain_label}"
        )
    if len(matched) > _MAX_LISTED:
        lines.append(f"  ... (+{len(matched) - _MAX_LISTED} more)")
    lines.append("")

    # --- corridor -----------------------------------------------------------
    head = pick_text(_TEXT["corridor_head"], lang)
    lines.append(head)
    lines.append(_rule(head))
    lines.append(
        f"  {pick_text(_TEXT['matched_into_chain'], lang)}: "
        f"{_pct(corridor.matched_count, corridor.generated_total)}"
    )
    lines.append(
        f"  {pick_text(_TEXT['in_corridor_unmatched'], lang)}: "
        f"{len(corridor.in_corridor_unmatched)}"
    )
    lines.append(f"      {_truncated(corridor.in_corridor_unmatched)}")
    lines.append(f"  {pick_text(_TEXT['out_of_scope'], lang)}: {corridor.out_of_scope_count}")
    lines.append("")

    # --- totals -------------------------------------------------------------
    head = pick_text(_TEXT["totals_head"], lang)
    lines.append(head)
    lines.append(_rule(head))
    lines.append(
        f"{'':26}{pick_text(_TEXT['col_consultant'], lang):>14}"
        f"{pick_text(_TEXT['col_generated'], lang):>14}"
    )
    for field_name, caption_key in (
        ("plain_link_count", "t_links"),
        ("connector_count", "t_connectors"),
        ("node_count", "t_nodes"),
        ("movement_count", "t_movements"),
        ("signal_head_count", "t_signal_heads"),
    ):
        caption = pick_text(_TEXT[caption_key], lang)
        pad = " " * max(1, 26 - _display_width(caption))
        lines.append(
            f"{caption}{pad}{getattr(consultant, field_name):>14}"
            f"{getattr(generated, field_name):>14}"
        )
    lines.append("")

    # --- signals ------------------------------------------------------------
    signals = benchmark.signals or SignalScopeGap()
    head = pick_text(_TEXT["signal_head"], lang)
    lines.append(head)
    lines.append(_rule(head))
    lines.append(
        "  "
        + pick_text(_TEXT["signal_line"], lang).format(
            heads=signals.consultant_signal_heads,
            links=signals.consultant_signalized_links,
            generated=signals.generated_signal_heads,
        )
    )
    lines.append(f"  {signals.note}")
    lines.append("")

    # --- methods ------------------------------------------------------------
    head = pick_text(_TEXT["methods_head"], lang)
    lines.append(head)
    lines.append(_rule(head))
    for key, value in (
        ("m_tolerance", f"{methods.tolerance_m:g} m"),
        ("m_buffer", f"{methods.corridor_buffer_m:g} m"),
        ("m_band", f"+/-{methods.bearing_band_deg:g} deg"),
        ("m_member", f"{methods.min_member_coverage:g}"),
        ("m_chain", f"{methods.min_chain_coverage:g}"),
        ("m_gap", f"{methods.lateral_cluster_gap_m:g} m"),
        ("m_step", f"{methods.sample_step_m:g} m"),
        # The two basis strings stay English on purpose: they cite file paths
        # and constant names (backend/routers/geojson.py, MAX_PLANIMETRIC_ERROR_M)
        # that a reader has to be able to grep for verbatim.
        ("m_derivation", methods.tolerance_basis),
        ("m_georef", methods.georeference_basis),
        ("m_consultant_frame", methods.consultant_frame),
        ("m_generated_frame", methods.generated_frame),
    ):
        lines.append(f"  {pick_text(_TEXT[key], lang)}: {value}")
    lines.append(f"  {methods.position_note}")
    lines.append("")

    # --- findings -----------------------------------------------------------
    head = pick_text(_TEXT["findings_head"], lang)
    lines.append(head)
    lines.append(_rule(head))
    if not benchmark.findings:
        lines.append("  (none)")
    for finding in benchmark.findings:
        subject = f" [{finding.subject}]" if finding.subject else ""
        lines.append(
            f"  [{_SEVERITY_TAG[finding.severity]}] {finding.code}{subject}: "
            f"{_finding_text(finding, lang)}"
        )
    lines.append("")
    lines.append(pick_text(_TEXT["no_position"], lang))
    return "\n".join(lines)


__all__ = [
    "BEARING_BAND_DEG",
    "CENTRELINE_ALLOWANCE_M",
    "CORRIDOR_BUFFER_FACTOR",
    "GEOREF_RESIDUAL_M",
    "LATERAL_CLUSTER_GAP_M",
    "MATCH_TOLERANCE_M",
    "MIN_CHAIN_COVERAGE",
    "MIN_MEMBER_COVERAGE",
    "PROJECTION_BUDGET_M",
    "SAMPLE_STEP_M",
    "TOLERANCE_BASIS",
    "AmbiguousObject",
    "BenchmarkInvariantError",
    "BenchmarkMethods",
    "ChainMember",
    "CorridorScope",
    "GeneratedScope",
    "GeoNetwork",
    "GeoObject",
    "LaneAgreement",
    "MatchBucket",
    "MatchedPair",
    "MissingObject",
    "MissingReason",
    "SignalScopeGap",
    "TopologicalBenchmark",
    "benchmark_network_files",
    "benchmark_networks",
    "georeferenced_network",
    "helmert_bridge",
    "local_frame_network",
    "node_membership_findings",
    "pick_text",
    "render_benchmark_report",
]

"""Draft network → ``.inpx`` XML, written to the schema Vissim itself saves.

Roadmap row 2.3, Genesis (a), last stage. **Emitter v2.**

What changed, and why it had to
-------------------------------
v1 was built against ``data/practice.inpx``, the only ``.inpx`` on the machine
at the time. The 2026-08-06 live check
(``backend/tools/genesis_open_live_check.py``) measured that that file is a 6 KB
HAND-WRITTEN exemplar, not Vissim output: its ``version="2400"`` is not a Vissim
schema version. Every v1 file — the synthetic crossroads and the 2,069-link
Murdoch draft alike — was refused by ``LoadNet`` with "The network file could
not be loaded.", and Vissim writes NO ``.err`` for that failure. The probe
bisected it in both directions: a REAL network stripped to v1's attribute set
becomes unloadable with the identical error, and v1's own geometry grafted into
a real skeleton loads.

v2 emits against networks **PTV Vissim saved**. Every constant lives in
:mod:`services.genesis.schema`, each one naming the saved file it was copied
from; nothing in this module invents a value. Concretely v2 adds:

* the real root — ``version="1403"`` plus ``vissimVersion`` — instead of 2400;
* the ~35 mandatory ``<link>`` attributes (``linkBehavType``, ``displayType``,
  ``level``, ``lnChgDist``, ``mesoSpeedModel``, …) and ``<lane markingType>``.
  A real network with its links reduced to ``no``/``name`` is refused, so these
  are not decoration;
* the sibling containers that make this file's own references resolve, and the
  full transitive closure of them: ``displayTypes``, ``levels``,
  ``linkBehaviorTypes``, ``laneMarkingTypes``, the ``drivingBehaviors`` entry
  the link behaviour type delegates to, and the two ``timeDistributions`` that
  driving behaviour in turn names. A real network stripped to ``netPara`` +
  ``links`` reports load SUCCESS with **zero links**, and so did v2's first
  live attempt when the last two hops of that chain were missing: unresolved
  references make links vanish silently, with no ``.err``, which is exactly the
  failure class this project exists to catch;
* ``netPara trafReg``, from the draft's own :attr:`~.DraftNetwork.
  traffic_regulation`. Measured: without it a network with left-hand geometry
  opens as RIGHT-hand traffic — the attribute decides the regime, the geometry
  does not;
* the real connector form. Endpoints are ``<fromLinkEndPt link="32" pos="…"/>``
  — attribute ``link``, no lane token. The ``"<link> <lane>"`` token lives in
  ``<objectRef key="32 2"/>`` under each connector lane's ``<fromLanes>`` /
  ``<toLanes>``: ONE ``<lane>`` per connector lane, each mapping one approach
  lane to one exit lane. v1 wrote ``lane="<link> 1"`` on the endpoints and
  nothing per-lane;
* correct lane indices. Index 1 is the RIGHT-most lane (measured in both
  driving regimes), so under left-hand traffic it is the MEDIAN side. v1 wrote 1
  everywhere, putting every kerb-side turn on the median lane; ``draft.py``'s
  :func:`~.draft.connector_lane_pairs` now derives the block per movement.

What this emission still is, and is not
---------------------------------------
Geometry — links, connectors and node polygons, plus the minimum supporting
objects those references need — and, since roadmap 3.1 Genesis (b), an
**optional** signal layer: pass ``signals`` and this module also writes
``signalControllers``, ``signalHeads`` and ``detectors``. Everything about that
layer (the group layout, the timings, §5.4.2's compliance grading) lives in
:mod:`services.genesis.signals`; this module only serialises what it is handed,
and the constants it serialises with are in :mod:`services.genesis.schema` like
every other measured default.

The signal layer is strictly ADDITIVE. Omit ``signals`` and every byte of the
output is what it was before roadmap 3.1 — same containers, same provenance
comment, same everything — which is pinned rather than asserted
(``test_genesis_signals.py::test_unsignalised_draft_bytes_are_unchanged``).

Since roadmap 3.2 Genesis (c) there is a second optional layer on exactly the
same contract: pass ``demand`` and this module also writes ``timeIntervalSets``,
``vehicleCompositions``, ``vehicleInputs`` and ``vehicleRoutingDecisionsStatic``.
Everything about that layer — the join to the observed counts, the volumes, the
relative flows, §5.2.2/§5.2.3/§5.5's compliance grading, and the two references
it deliberately does not declare — lives in :mod:`services.genesis.demand`.
Omit ``demand`` and, again, not one byte changes
(``test_genesis_demand.py::test_demand_less_draft_bytes_are_unchanged``), and a
signals-only file is byte-identical to what roadmap 3.1 produced.

Still **no** desired-speed decisions, reduced-speed areas, lane closures,
conflict areas or OD matrices — every one of those is outside the row's "기하
생성" (geometry generation) scope, and the §5 rules that govern them are reported
``not_checkable(outside_emitted_subset)`` by ``draft.py`` rather than
approximated here.

Node membership is no longer a stated gap: ``<linkSegment>``/``<linkSegs>`` does
not exist in ANY Vissim-saved network, so there was never anything to write.
``services.inpx_parser._node_link_ids`` now derives membership geometrically
from the node polygon, which is how Vissim itself defines it.

Verification status
-------------------
Offline this file is still round-tripped through ``services.inpx_parser``,
self-diffed empty and mutation-diffed through ``genesis.diff`` as the oracle,
and is byte-identical across runs.

MEASURED LIVE 2026-08-06, PTV Vissim Academic 2026 SP 09, via
``backend/tools/genesis_open_live_check.py --only both --label-suffix _v2``
(``attach_to_running=False``, temp work dirs, LoadNet → ``.err`` → SaveNetAs →
close, no simulation, zero orphaned processes):

===================== ========== ========== ==================================
network               LoadNet    SaveNetAs  round trip (emitted → saved)
===================== ========== ========== ==================================
synthetic crossroads  OK 1.59 s  OK 0.06 s  8 links / 12 connectors / 12+14
                                            lanes / 1 node — all unchanged
Murdoch (real OSM)    OK 2.28 s  OK 0.36 s  2069 links / 3128 connectors /
                                            2461+3481 lanes / 14173 polyline
                                            points / 941 nodes — all unchanged
===================== ========== ========== ==================================

No ``.err`` was written for either, Vissim dropped no container, ``trafReg``
came back ``LEFTHANDTRAFFIC``, every connector's lane mapping came back
identical, and Vissim computed conflict areas over the geometry (40 and 5,518)
— which it only does for a network it actually built.

That is a statement about the SCHEMA, not a certificate for any particular
generated file, and :func:`provenance_comment` says so in the file itself.

Determinism
-----------
Two runs from the same draft produce byte-identical files: ids are assigned by
``draft.py`` from sorted OSM ids, elements are written in id order, attributes
are written in a fixed (sorted) order, every number goes through one formatter
(:func:`_num`, fixed 3 decimals), and nothing — no timestamp, no host name, no
run id — that varies between runs is written. The provenance comment carries the
projection origin and the source file name, both properties of the input.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from . import schema
from .demand import DraftDemand, demand_containers, demand_provenance_note
from .draft import DraftConnector, DraftLink, DraftNetwork, DraftNode
from .project import NOT_GEOREFERENCED_NOTE
from .signals import DraftSignals, signal_containers, signal_provenance_note

#: Re-exported so callers that imported it from here keep working. The value is
#: measured; see :data:`services.genesis.schema.NETWORK_VERSION`.
NETWORK_VERSION = schema.NETWORK_VERSION

#: Decimal places for every number written. 1 mm — three orders of magnitude
#: below the projection's own one-metre budget, and fixed so formatting can
#: never vary between runs.
NUM_DECIMALS = 3


class EmitRefused(ValueError):
    """Raised when asked to write a refused (or empty) draft.

    The one place this package raises rather than reports: a caller that has a
    refused draft in its hand and asks for an ``.inpx`` anyway has a bug, and
    writing a plausible empty network for it would hide that bug in a file.
    """


def _num(value: float) -> str:
    text = f"{float(value):.{NUM_DECIMALS}f}"
    # -0.000 and 0.000 are the same number; only one of them may ever be written.
    return "0.000" if text == "-0.000" else text


def _element(parent: ET.Element, tag: str, attrs: dict) -> ET.Element:
    """One ``SubElement`` with attributes in a FIXED order.

    ``ElementTree`` preserves insertion order, so sorting here is what makes two
    runs byte-identical regardless of how the attribute dicts were assembled.
    Vissim writes its own attributes alphabetically too, which keeps a generated
    file diffable against one Vissim saved.
    """
    return ET.SubElement(parent, tag, {key: attrs[key] for key in sorted(attrs)})


def _link_poly_points(parent: ET.Element, points: list[tuple[float, float]]) -> None:
    container = ET.SubElement(parent, "linkPolyPts")
    for x, y in points:
        _element(
            container,
            "linkPolyPoint",
            {**schema.LINK_POLY_POINT_ATTRS, "x": _num(x), "y": _num(y)},
        )


def _link_lanes(parent: ET.Element, count: int, width_m: float) -> None:
    """A plain link's lanes: ``markingType`` and ``width``, as Vissim writes them."""
    lanes = ET.SubElement(parent, "lanes")
    for _ in range(max(count, 1)):
        _element(
            lanes,
            "lane",
            {"markingType": schema.LANE_MARKING_TYPE_NO, "width": _num(width_m)},
        )


def _connector_lanes(parent: ET.Element, connector: DraftConnector) -> None:
    """A connector's lanes: ``markingType`` only, plus the from/to lane refs.

    Vissim writes NO ``width`` on a connector lane — a connector inherits the
    width of the lanes it joins — and ``inpx_parser._parse_lane_widths`` falls
    back to 3.5 m for exactly that case, which is this generator's own width.

    One ``<lane>`` per connector lane, each carrying a single
    ``<fromLanes><objectRef key="<link> <lane>"/></fromLanes>`` and the matching
    ``<toLanes>``. That key is where the ``"link lane"`` token lives; the
    endpoint elements take ``link=`` and no lane.
    """
    pairs = list(zip(connector.from_lanes, connector.to_lanes))
    if not pairs:
        # A draft built before the lane mapping existed (or hand-assembled in a
        # test) still emits a well-formed connector rather than a lane-less one.
        pairs = [(1, 1)] * max(connector.lanes, 1)

    lanes = ET.SubElement(parent, "lanes")
    for from_lane, to_lane in pairs:
        lane = _element(lanes, "lane", {"markingType": schema.LANE_MARKING_TYPE_NO})
        _element(
            ET.SubElement(lane, "fromLanes"),
            "objectRef",
            {"key": f"{connector.from_link} {from_lane}"},
        )
        _element(
            ET.SubElement(lane, "toLanes"),
            "objectRef",
            {"key": f"{connector.to_link} {to_lane}"},
        )


def _link_element(parent: ET.Element, link: DraftLink) -> None:
    element = _element(
        parent,
        "link",
        {
            **schema.LINK_COMMON_ATTRS,
            **schema.LINK_ONLY_ATTRS,
            "displayType": schema.DISPLAY_TYPE_NO,
            "linkBehavType": schema.LINK_BEHAV_TYPE_NO,
            "no": str(link.id),
            "name": link.name,
        },
    )
    geometry = ET.SubElement(element, "geometry")
    _link_poly_points(geometry, link.points)
    _link_lanes(element, link.lanes, link.lane_width_m)


def _connector_element(parent: ET.Element, connector: DraftConnector) -> None:
    """A connector is a ``<link>`` with endpoints — and WITHOUT ``level``.

    Measured on all three saved references: every element carrying
    ``<fromLinkEndPt>`` lacks ``level``, every element without one has it.

    Child order is Vissim's own: ``fromLinkEndPt``, ``geometry``, ``lanes``,
    ``toLinkEndPt``.
    """
    element = _element(
        parent,
        "link",
        {
            **schema.LINK_COMMON_ATTRS,
            "displayType": schema.DISPLAY_TYPE_NO,
            "linkBehavType": schema.LINK_BEHAV_TYPE_NO,
            "no": str(connector.id),
            "name": connector.name,
        },
    )
    _element(
        element,
        "fromLinkEndPt",
        {"link": str(connector.from_link), "pos": _num(connector.from_pos_m)},
    )
    geometry = ET.SubElement(element, "geometry")
    _link_poly_points(geometry, connector.points)
    _connector_lanes(element, connector)
    _element(
        element,
        "toLinkEndPt",
        {"link": str(connector.to_link), "pos": _num(connector.to_pos_m)},
    )


def _node_element(parent: ET.Element, node: DraftNode) -> None:
    element = _element(
        parent, "node", {**schema.NODE_ATTRS, "no": str(node.id), "name": node.name}
    )
    geometry = ET.SubElement(element, "geometry")
    points = ET.SubElement(geometry, "points")
    for x, y in node.polygon:
        # x and y and nothing else: no Vissim-saved node point carries zOffset.
        _element(points, "point", {"x": _num(x), "y": _num(y)})


def _support_containers() -> dict[str, ET.Element]:
    """The objects this file's own ``<link>``/``<lane>`` references point at.

    Without them Vissim loads the network, reports success, and holds ZERO links
    — measured twice: on the diagnose ladder's v5/v6, and again on emitter v2's
    FIRST live run, where an unresolved ``w99cc1Distr`` two links down the chain
    took the whole ``drivingBehaviors`` container with it and every link after
    it. Nothing here is a preference; each element exists because an attribute
    this file writes names its ``no``.

    Returned unattached so :func:`build_tree` can place each container in
    Vissim's own top-level order rather than in this table's order.
    """

    def _node(parent: ET.Element, spec: tuple) -> None:
        tag, attrs, children = spec
        element = _element(parent, tag, attrs)
        for child in children:
            _node(element, child)

    containers: dict[str, ET.Element] = {}
    for tag, children in schema.SUPPORT_CONTAINERS:
        container = ET.Element(tag)
        for child in children:
            _node(container, child)
        containers[tag] = container
    return containers


def _comment_safe(text: str) -> str:
    """XML forbids ``--`` inside a comment, and forbids one at its end.

    This is not hypothetical: the first draft this module ever wrote was
    unparseable because the source ``.osm`` sat under a temp directory whose
    name contained ``--``, and the path went straight into the comment. Names
    the generator does not control are sanitised rather than trusted.
    """
    cleaned = text.replace("--", "- -")
    while cleaned.endswith("-"):
        cleaned = cleaned[:-1] + " "
    return cleaned


def provenance_comment(
    draft: DraftNetwork,
    signals: DraftSignals | None = None,
    demand: DraftDemand | None = None,
) -> str:
    """The honesty statement written into the file itself.

    An XML comment, so ``ElementTree`` (and Vissim) ignore it and no consumer
    can mistake it for data — but a human opening the file in an editor reads it
    before anything else, and the projection origin travels with the geometry it
    explains.

    The source is named by FILE NAME, never by full path: an absolute path is
    this machine's directory layout, it does not belong in a deliverable, and it
    would make the output's bytes depend on where the input happened to sit.

    With no signals and no demand the text is character-for-character what it
    was before roadmap 3.1 — including the "GEOMETRY ONLY … No signal heads,
    detectors" sentence, which stays TRUE for that file. With either layer that
    sentence would be a lie, so it is replaced rather than appended to, and the
    signals-only branch is byte-for-byte what roadmap 3.1 wrote.
    """
    projection = draft.metadata.get("projection", {})
    signal_note = signal_provenance_note(signals)
    demand_note = demand_provenance_note(demand)
    if not signal_note and not demand_note:
        scope = (
            "GEOMETRY ONLY: links, connectors and node polygons. No signal heads, detectors, "
            "vehicle inputs or routing. "
        )
    elif signal_note and not demand_note:
        scope = (
            "GEOMETRY + SIGNALS: links, connectors, node polygons, signal controllers, "
            "signal groups, signal heads and detectors. No vehicle inputs or routing. "
            f"{signal_note}"
        )
    elif demand_note and not signal_note:
        scope = (
            "GEOMETRY + DEMAND: links, connectors, node polygons, vehicle inputs, vehicle "
            "compositions and static vehicle routing decisions. No signal heads or detectors. "
            f"{demand_note}"
        )
    else:
        scope = (
            "GEOMETRY + SIGNALS + DEMAND: links, connectors, node polygons, signal controllers, "
            "signal groups, signal heads, detectors, vehicle inputs, vehicle compositions and "
            f"static vehicle routing decisions. {signal_note}{demand_note}"
        )
    return _comment_safe(
        " Generated by Prism services.genesis (roadmap 2.3 Genesis (a)) from "
        f"{Path(draft.source_osm).name}. "
        f"{scope}"
        f"Traffic regulation {draft.traffic_regulation}. "
        f"{NOT_GEOREFERENCED_NOTE} "
        f"Projection origin lat={projection.get('origin_lat_deg')} "
        f"lon={projection.get('origin_lon_deg')} "
        f"({projection.get('kind')}); validity radius {projection.get('validity_radius_m')} m; "
        f"planimetric error bound {projection.get('planimetric_error_bound_m')} m. "
        "Emitter v2: written to the schema PTV Vissim itself saves, every default copied "
        "from a Vissim-saved network. VERIFIED offline against this repo's inpx_parser and "
        "genesis.diff. THIS SCHEMA was measured to LoadNet in PTV Vissim Academic 2026 "
        "(SP 09) on 2026-08-06 - two networks, 20 and 5197 links, both loaded with every "
        "link, connector, lane and node preserved through a SaveNetAs round trip. That is a "
        "statement about the schema, not a certificate for this particular file: no Vissim "
        "has opened THIS file unless someone opened it. "
    )


def build_tree(
    draft: DraftNetwork,
    signals: DraftSignals | None = None,
    demand: DraftDemand | None = None,
) -> ET.ElementTree:
    """The ``.inpx`` element tree for a draft. Deterministic given the inputs.

    ``signals`` (roadmap 3.1 Genesis (b)) and ``demand`` (roadmap 3.2 Genesis
    (c)) are OPTIONAL and purely additive. When one is ``None``, its package's
    empty sentinel (:data:`~services.genesis.signals.NO_SIGNALS`,
    :data:`~services.genesis.demand.NO_DEMAND`), or a layer that refused, the
    corresponding ``*_containers`` call returns no containers, the provenance
    comment is unchanged, and the emitted bytes are exactly what a draft without
    that layer produced before the argument existed — pinned by
    ``test_genesis_signals.py`` and ``test_genesis_demand.py``.
    """
    if draft.refused:
        raise EmitRefused(
            f"{draft.name}: draft was refused, nothing to emit — {'; '.join(draft.refusals)}"
        )
    if not draft.links:
        raise EmitRefused(f"{draft.name}: draft has no links; refusing to write an empty network")

    root = ET.Element(
        "network",
        {"version": schema.NETWORK_VERSION, "vissimVersion": schema.VISSIM_VERSION},
    )
    root.append(ET.Comment(provenance_comment(draft, signals, demand)))

    containers = _support_containers()

    links = ET.Element("links")
    for link in sorted(draft.links, key=lambda l: l.id):
        _link_element(links, link)
    for connector in sorted(draft.connectors, key=lambda c: c.id):
        _connector_element(links, connector)
    containers["links"] = links

    # ``trafReg`` comes from the draft, never from a constant here, so the file
    # cannot claim a regime the geometry was not built for. No refPointMap /
    # refPointNet and no children at all: those anchor a network to the map, and
    # this file makes no absolute claim. ``northDir="0"`` (in NETPARA_ATTRS)
    # states only that +y is north IN THIS FILE'S OWN FRAME.
    net_para = ET.Element("netPara")
    for key in sorted({**schema.NETPARA_ATTRS, "trafReg": draft.traffic_regulation}):
        net_para.set(
            key, {**schema.NETPARA_ATTRS, "trafReg": draft.traffic_regulation}[key]
        )
    containers["netPara"] = net_para

    nodes = ET.Element("nodes")
    for node in sorted(draft.nodes, key=lambda n: n.id):
        _node_element(nodes, node)
    containers["nodes"] = nodes

    # Signals, when asked for. ``signal_containers`` returns {} for an
    # unsignalised draft, so this line adds no key and changes no byte.
    containers.update(signal_containers(signals))

    # Demand, when asked for. Same contract: ``demand_containers`` returns {}
    # for a draft with no demand layer, or one that refused.
    containers.update(demand_containers(demand))

    # Placed in VISSIM's own top-level order, not in the order this module
    # happened to build them.
    for tag in schema.CONTAINER_ORDER:
        if tag in containers:
            root.append(containers.pop(tag))
    if containers:  # pragma: no cover - guard
        raise AssertionError(f"containers with no place in CONTAINER_ORDER: {sorted(containers)}")

    _assert_container_order(root)
    return ET.ElementTree(root)


def _assert_container_order(root: ET.Element) -> None:
    """Top-level containers must come out in the order Vissim writes them.

    A guard rather than a sort: if a later edit adds a container, this fails
    loudly here instead of producing a file that differs from a Vissim save in a
    way nobody notices until the next refusal has to be bisected.
    """
    emitted = [child.tag for child in root if isinstance(child.tag, str)]
    expected = [tag for tag in schema.CONTAINER_ORDER if tag in emitted]
    if emitted != expected:  # pragma: no cover - guard
        raise AssertionError(
            f"container order {emitted} does not match the Vissim order {expected}"
        )


def to_xml_text(
    draft: DraftNetwork,
    signals: DraftSignals | None = None,
    demand: DraftDemand | None = None,
) -> str:
    """The ``.inpx`` as text. Byte-identical for equal inputs (draft, signals, demand)."""
    root = build_tree(draft, signals, demand).getroot()
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def emit_inpx(
    draft: DraftNetwork,
    path: str | Path,
    signals: DraftSignals | None = None,
    demand: DraftDemand | None = None,
) -> Path:
    """Write the draft to ``path`` as UTF-8 with LF newlines, and return it.

    ``newline=""`` so Windows does not translate ``\\n`` to ``\\r\\n``: a file
    whose bytes depend on the host OS is not the byte-identical artefact this
    module promises. (The CRLFs inside a signal controller's ``intSupplyData``
    are unaffected — they live inside a base64 payload, so they are bytes of the
    encoded string, not line endings of this file.)
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_xml_text(draft, signals, demand), encoding="utf-8", newline="")
    return out

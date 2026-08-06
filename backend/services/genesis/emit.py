"""Draft network → ``.inpx`` XML. Geometry only, and deliberately small.

Roadmap row 2.3, Genesis (a), last stage.

WHAT THIS EMISSION IS VERIFIED AGAINST — read this before trusting a file
-------------------------------------------------------------------------
The output of :func:`emit_inpx` is verified, offline, to:

* parse through **this repository's** ``services.inpx_parser.parse_network``,
  recovering every link id, lane count and polyline it was given;
* load through ``services.genesis.diff.load_topology`` and **self-diff empty**;
* diff against a mutated copy of itself with exactly the mutation, using U2's
  differ as the oracle rather than any comparison written here;
* be **byte-identical** across two runs from the same draft.

It has **NOT** been opened by a real PTV Vissim. There is no Vissim licence on
this machine and no COM is touched anywhere in this package, so "Vissim accepts
this file" is a claim nobody here is in a position to make. Opening a generated
draft on the licensed box is a **named follow-up** — it is the other half of
roadmap row 2.3's exit alongside the real-Murdoch benchmark run — and the
compliance report says so in every language it renders.

Scope: only what the parser can read back
-----------------------------------------
The emitted subset is bounded by ``inpx_parser``, not by what Vissim supports.
Where the two disagree the SUBSET SHRINKS; the parser is not extended to meet
the emitter. Concretely this writes:

* ``<links><link>`` with ``<geometry><linkPolyPts>`` and ``<lanes><lane width>``
* connectors — the same ``<link>`` element plus ``<fromLinkEndPt>`` /
  ``<toLinkEndPt>``, which is exactly how Vissim (and the parser) distinguish
  them
* ``<nodes><node>`` with a ``<geometry><points>`` polygon

and writes **no** signal heads, detectors, vehicle inputs, routing decisions,
desired-speed decisions, reduced-speed areas, link behaviour types, lane
closures or conflict areas. Every one of those is either outside the row's
"기하 생성" (geometry generation) scope or unverifiable offline; the §5 rules
that govern them are reported ``not_checkable(outside_emitted_subset)`` by
``draft.py`` rather than approximated here.

Two things are known gaps rather than oversights, and are stated so a later unit
does not rediscover them as bugs:

1. **No ``<linkSegs>`` on nodes.** ``inpx_parser._node_link_ids`` — which
   ``genesis.diff`` uses to decide which connectors a node "declares" — reads
   ``<linkSegment link=… fromPos=… internalToPos=…>``. The only ``.inpx``
   exemplar on this machine (``data/practice.inpx``) carries node *polygons*
   and no ``<linkSegs>`` at all, so the container's real element name and
   attribute set cannot be verified offline. Consequence, stated plainly: a
   diff of two genesis drafts reports **0 movements per node**, because the
   nodes declare no members. The connectors themselves are compared normally.
2. **Connector endpoint lane index is always 1.** Vissim's endpoint attribute is
   ``lane="<linkNo> <laneNo>"``; the parser only reads the link number. Which
   physical lane index 1 denotes under left-hand traffic is not verifiable
   without Vissim, so writing anything else would be a guess dressed as data.

Determinism
-----------
Two runs from the same draft produce byte-identical files: ids are assigned by
``draft.py`` from sorted OSM ids, elements are written in id order, every number
goes through one formatter (:func:`_num`, fixed 3 decimals), and nothing —
no timestamp, no host name, no run id — that varies between runs is written.
The provenance comment carries the projection origin and the source file name,
both of which are properties of the input.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .draft import DraftConnector, DraftLink, DraftNetwork, DraftNode
from .project import NOT_GEOREFERENCED_NOTE

#: Copied from ``data/practice.inpx`` — the only ``.inpx`` on this machine. It
#: is not a claim about which Vissim version will open the file; see the
#: §5.1.2 item in the compliance report, which is ``not_checkable``.
NETWORK_VERSION = "2400"

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


def _polyline(parent: ET.Element, points: list[tuple[float, float]], *, tag: str, point_tag: str) -> None:
    container = ET.SubElement(parent, tag)
    for x, y in points:
        ET.SubElement(container, point_tag, {"x": _num(x), "y": _num(y), "zOffset": "0"})


def _lanes(parent: ET.Element, count: int, width_m: float) -> None:
    lanes = ET.SubElement(parent, "lanes")
    for _ in range(max(count, 1)):
        ET.SubElement(lanes, "lane", {"width": _num(width_m)})


def _link_element(parent: ET.Element, link: DraftLink) -> None:
    element = ET.SubElement(parent, "link", {"no": str(link.id), "name": link.name})
    geometry = ET.SubElement(element, "geometry")
    _polyline(geometry, link.points, tag="linkPolyPts", point_tag="linkPolyPoint")
    _lanes(element, link.lanes, link.lane_width_m)


def _connector_element(parent: ET.Element, connector: DraftConnector) -> None:
    element = ET.SubElement(parent, "link", {"no": str(connector.id), "name": connector.name})
    geometry = ET.SubElement(element, "geometry")
    _polyline(geometry, connector.points, tag="linkPolyPts", point_tag="linkPolyPoint")
    _lanes(element, connector.lanes, connector.lane_width_m)
    # lane="<linkNo> <laneNo>": the parser reads the link number; the lane index
    # is always 1 — see the module docstring's known-gap 2.
    ET.SubElement(
        element,
        "fromLinkEndPt",
        {"lane": f"{connector.from_link} 1", "pos": _num(connector.from_pos_m)},
    )
    ET.SubElement(
        element,
        "toLinkEndPt",
        {"lane": f"{connector.to_link} 1", "pos": _num(connector.to_pos_m)},
    )


def _node_element(parent: ET.Element, node: DraftNode) -> None:
    element = ET.SubElement(parent, "node", {"no": str(node.id), "name": node.name})
    geometry = ET.SubElement(element, "geometry")
    _polyline(geometry, node.polygon, tag="points", point_tag="point")


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


def provenance_comment(draft: DraftNetwork) -> str:
    """The honesty statement written into the file itself.

    An XML comment, so ``ElementTree`` (and Vissim) ignore it and no consumer
    can mistake it for data — but a human opening the file in an editor reads it
    before anything else, and the projection origin travels with the geometry it
    explains.

    The source is named by FILE NAME, never by full path: an absolute path is
    this machine's directory layout, it does not belong in a deliverable, and it
    would make the output's bytes depend on where the input happened to sit.
    """
    projection = draft.metadata.get("projection", {})
    return _comment_safe(
        " Generated by Prism services.genesis (roadmap 2.3 Genesis (a)) from "
        f"{Path(draft.source_osm).name}. "
        f"GEOMETRY ONLY: links, connectors and node polygons. No signal heads, detectors, "
        f"vehicle inputs or routing. "
        f"{NOT_GEOREFERENCED_NOTE} "
        f"Projection origin lat={projection.get('origin_lat_deg')} "
        f"lon={projection.get('origin_lon_deg')} "
        f"({projection.get('kind')}); validity radius {projection.get('validity_radius_m')} m; "
        f"planimetric error bound {projection.get('planimetric_error_bound_m')} m. "
        "VERIFIED offline against this repo's inpx_parser and genesis.diff only. "
        "NOT opened by a real Vissim - that smoke test is a named follow-up on the "
        "licensed machine. "
    )


def build_tree(draft: DraftNetwork) -> ET.ElementTree:
    """The ``.inpx`` element tree for a draft. Deterministic given the draft."""
    if draft.refused:
        raise EmitRefused(
            f"{draft.name}: draft was refused, nothing to emit — {'; '.join(draft.refusals)}"
        )
    if not draft.links:
        raise EmitRefused(f"{draft.name}: draft has no links; refusing to write an empty network")

    root = ET.Element("network", {"version": NETWORK_VERSION})
    root.append(ET.Comment(provenance_comment(draft)))
    # northDir="0" states that +y is north IN THIS FILE'S OWN FRAME, which is
    # what the projection produces. No refPointMap/refPointNet is written: those
    # anchor a network to the map, and this file makes no absolute claim.
    ET.SubElement(root, "netPara", {"northDir": "0"})

    links = ET.SubElement(root, "links")
    for link in sorted(draft.links, key=lambda l: l.id):
        _link_element(links, link)
    for connector in sorted(draft.connectors, key=lambda c: c.id):
        _connector_element(links, connector)

    nodes = ET.SubElement(root, "nodes")
    for node in sorted(draft.nodes, key=lambda n: n.id):
        _node_element(nodes, node)

    return ET.ElementTree(root)


def to_xml_text(draft: DraftNetwork) -> str:
    """The ``.inpx`` as text. Byte-identical for equal drafts."""
    root = build_tree(draft).getroot()
    ET.indent(root, space="  ")
    body = ET.tostring(root, encoding="unicode", xml_declaration=False)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + body + "\n"


def emit_inpx(draft: DraftNetwork, path: str | Path) -> Path:
    """Write the draft to ``path`` as UTF-8 with LF newlines, and return it.

    ``newline=""`` so Windows does not translate ``\\n`` to ``\\r\\n``: a file
    whose bytes depend on the host OS is not the byte-identical artefact this
    module promises.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_xml_text(draft), encoding="utf-8", newline="")
    return out

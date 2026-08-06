"""``inpx_parser._node_link_ids`` — node membership, which is GEOMETRIC.

Why this file exists
--------------------
The helper used to read ``<linkSegment link=… fromPos=… internalToPos=…>``
inside each ``<node>``. The 2026-08-06 live check
(``backend/tools/genesis_open_live_check.py``, PTV Vissim Academic 2026 SP 09)
scanned every Vissim-saved network on this machine — PTV's own Examples Demo
networks included, plus Vissim's save of a network we generated — and found
**no** ``<linkSegment>`` and no ``<linkSegs>`` anywhere. Vissim does not write
them. So the helper read an element that does not exist and returned an empty
set for EVERY node of EVERY network, real customer networks included, and
``services.genesis.diff``'s "0 movements per node" was a parser assumption
rather than an emitter gap.

A Vissim node IS its polygon. Membership is now computed the same way: a link
belongs to a node when any vertex of its polyline lies inside (or on) the node's
``<geometry><points>`` polygon. These tests cover the polygon arithmetic on
synthetic geometry with hand-derived answers, and then on the two real fixtures
this repo has — ``data/practice.inpx`` and its successor, a file emitted by
``services.genesis``.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
import re
from pathlib import Path

import pytest

import services.inpx_parser as inpx_parser

REPO_ROOT = Path(__file__).resolve().parents[2]
PRACTICE_INPX = REPO_ROOT / "data" / "practice.inpx"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _link(no: str, points: list[tuple[float, float]], *, connector: bool = False) -> str:
    pts = "".join(f'<linkPolyPoint x="{x}" y="{y}"/>' for x, y in points)
    endpoints = (
        '<fromLinkEndPt link="1" pos="0"/><toLinkEndPt link="2" pos="0"/>'
        if connector
        else ""
    )
    return (
        f'<link no="{no}" name="l{no}">{endpoints}'
        f"<geometry><linkPolyPts>{pts}</linkPolyPts></geometry>"
        f'<lanes><lane width="3.5"/></lanes></link>'
    )


def _node(no: str, polygon: list[tuple[float, float]]) -> str:
    if not polygon:
        return f'<node no="{no}" name="n{no}"><geometry><points/></geometry></node>'
    pts = "".join(f'<point x="{x}" y="{y}"/>' for x, y in polygon)
    return (
        f'<node no="{no}" name="n{no}">'
        f"<geometry><points>{pts}</points></geometry></node>"
    )


def _root(links: str, nodes: str) -> ET.Element:
    return ET.fromstring(f"<network><links>{links}</links><nodes>{nodes}</nodes></network>")


#: A 20 m box centred on the origin.
BOX = [(-10.0, -10.0), (10.0, -10.0), (10.0, 10.0), (-10.0, 10.0)]


# ---------------------------------------------------------------------------
# 1. The polygon predicate, hand-derived
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("point", "inside"),
    [
        ((0.0, 0.0), True),  # centre
        ((9.999, 9.999), True),  # just inside a corner
        ((10.0, 0.0), True),  # ON an edge — a link ending on the boundary is a member
        ((-10.0, -10.0), True),  # ON a vertex
        ((10.001, 0.0), False),  # just outside
        ((0.0, 20.0), False),  # far outside, above
        ((-50.0, 0.0), False),  # far outside, left (the ray-cast direction)
    ],
)
def test_point_in_polygon_on_a_square(point, inside):
    assert inpx_parser._point_in_polygon(point, BOX) is inside


def test_point_in_polygon_on_a_concave_polygon():
    """A comb — the shape the genesis differ's own fixtures use — so the
    predicate is not silently only correct for convex boxes."""
    comb = [
        (0.0, -2.0), (30.0, -2.0),
        (21.0, -1.0), (21.0, 1.0), (19.0, 1.0), (19.0, -1.0),
        (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0), (-1.0, -1.0),
        (0.0, -1.0),
    ]
    assert inpx_parser._point_in_polygon((0.0, 0.0), comb) is True
    assert inpx_parser._point_in_polygon((20.0, 0.0), comb) is True
    assert inpx_parser._point_in_polygon((10.0, 0.0), comb) is False
    assert inpx_parser._point_in_polygon((30.0, 0.0), comb) is False


@pytest.mark.parametrize("degenerate", [[], [(0.0, 0.0)], [(0.0, 0.0), (1.0, 1.0)]])
def test_a_polygon_with_fewer_than_three_points_contains_nothing(degenerate):
    assert inpx_parser._point_in_polygon((0.0, 0.0), degenerate) is False


# ---------------------------------------------------------------------------
# 2. Membership on synthetic networks
# ---------------------------------------------------------------------------


def test_a_link_whose_vertex_lies_in_the_polygon_is_a_member():
    root = _root(
        _link("1", [(-100.0, 0.0), (-5.0, 0.0)]) + _link("2", [(100.0, 0.0), (50.0, 0.0)]),
        _node("10", BOX),
    )
    assert inpx_parser._node_link_ids(root) == {"10": {"1"}}


def test_membership_covers_connectors_and_plain_links_alike():
    """``_node_link_ids`` does not know what a connector is — the differ filters
    movements out of the membership set afterwards. Both kinds must come back."""
    root = _root(
        _link("1", [(-100.0, 0.0), (-5.0, 0.0)])
        + _link("9", [(-5.0, 0.0), (5.0, 0.0)], connector=True),
        _node("10", BOX),
    )
    assert inpx_parser._node_link_ids(root) == {"10": {"1", "9"}}


def test_a_link_that_only_passes_near_the_node_is_not_a_member():
    """No proximity inference: 10.001 m from the box edge is outside it, and
    nothing rounds that into membership."""
    root = _root(_link("1", [(-100.0, 10.001), (100.0, 10.001)]), _node("10", BOX))
    assert inpx_parser._node_link_ids(root) == {"10": set()}


def test_two_nodes_each_get_their_own_members():
    far_box = [(90.0, -10.0), (110.0, -10.0), (110.0, 10.0), (90.0, 10.0)]
    root = _root(
        _link("1", [(-5.0, 0.0), (-100.0, 0.0)]) + _link("2", [(100.0, 0.0), (200.0, 0.0)]),
        _node("10", BOX) + _node("20", far_box),
    )
    assert inpx_parser._node_link_ids(root) == {"10": {"1"}, "20": {"2"}}


def test_one_link_can_belong_to_two_overlapping_nodes():
    """Nothing forces membership to be exclusive, and nothing here invents a
    tie-break: a link with a vertex in both polygons is in both sets."""
    other = [(-20.0, -20.0), (0.0, -20.0), (0.0, 20.0), (-20.0, 20.0)]
    root = _root(_link("1", [(-5.0, 0.0), (-100.0, 0.0)]), _node("10", BOX) + _node("20", other))
    assert inpx_parser._node_link_ids(root) == {"10": {"1"}, "20": {"1"}}


def test_the_bounding_box_prefilter_cannot_widen_membership():
    """``_node_link_ids`` rejects most (link, node) pairs on the polygon's
    bounding box before ray-casting — 941 nodes against the 5,197-link Murdoch
    draft is 12 s without it and 0.4 s with it. It is an optimisation and must
    behave like one: a link sitting INSIDE the box but OUTSIDE a concave
    polygon is still not a member.
    """
    comb = [
        (0.0, -2.0), (30.0, -2.0),
        (21.0, -1.0), (21.0, 1.0), (19.0, 1.0), (19.0, -1.0),
        (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0), (-1.0, -1.0),
        (0.0, -1.0),
    ]
    root = _root(
        _link("1", [(0.0, 0.0), (0.0, -50.0)])  # in a tooth  -> member
        + _link("2", [(10.0, 0.0), (10.0, -50.0)])  # in the box, between teeth
        + _link("3", [(20.0, 0.0), (20.0, -50.0)]),  # in the other tooth
        _node("10", comb),
    )
    assert inpx_parser._node_link_ids(root) == {"10": {"1", "3"}}


def test_a_node_without_a_polygon_has_no_members_rather_than_all_of_them():
    """The failure mode that would be worse than the bug this replaced: a node
    Vissim gave no polygon must claim nothing, not everything."""
    root = _root(_link("1", [(0.0, 0.0), (1.0, 1.0)]), _node("10", []))
    assert inpx_parser._node_link_ids(root) == {"10": set()}


def test_a_network_with_no_nodes_maps_to_nothing():
    root = ET.fromstring(f"<network><links>{_link('1', [(0.0, 0.0)])}</links></network>")
    assert inpx_parser._node_link_ids(root) == {}


def test_links_without_geometry_or_with_unreadable_coordinates_are_skipped():
    """Never raises: a malformed link is not a member, and does not take the
    whole network's membership down with it."""
    root = _root(
        '<link no="1" name="no geometry"/>'
        + '<link no="2" name="bad"><geometry><linkPolyPts>'
        '<linkPolyPoint x="oops" y="0"/></linkPolyPts></geometry></link>'
        + _link("3", [(0.0, 0.0), (1.0, 1.0)]),
        _node("10", BOX),
    )
    assert inpx_parser._node_link_ids(root) == {"10": {"3"}}


def test_a_link_with_an_empty_id_is_not_a_member_of_anything():
    root = _root(_link("", [(0.0, 0.0), (1.0, 1.0)]), _node("10", BOX))
    assert inpx_parser._node_link_ids(root) == {"10": set()}


# ---------------------------------------------------------------------------
# 3. The real fixtures
# ---------------------------------------------------------------------------


def test_practice_inpx_nodes_are_measurably_empty_and_the_reason_is_geometry():
    """``data/practice.inpx`` — the hand-written exemplar emitter v1 was built
    against — yields empty membership even geometrically, and that is CORRECT
    rather than the old bug in disguise.

    Its two node boxes are ±15 m squares while every link stops 20 m short of
    the junction and every connector spans −20 → +20 m with no vertex between.
    Nothing this file writes is inside those boxes.
    """
    root = ET.parse(PRACTICE_INPX).getroot()
    members = inpx_parser._node_link_ids(root)
    assert set(members) == {"1", "2"}
    assert members == {"1": set(), "2": set()}

    # the reason, asserted rather than asserted-about: the nearest vertex to
    # node 1's centre is 20 m out, and the box reaches 15 m
    box = [
        (float(p.get("x")), float(p.get("y")))
        for p in root.findall("./nodes/node[@no='1']/geometry/points/point")
    ]
    assert max(abs(x) for x, _ in box) == 15.0
    nearest = min(
        max(abs(float(p.get("x"))), abs(float(p.get("y"))))
        for p in root.findall("./links/link/geometry/linkPolyPts/linkPolyPoint")
    )
    assert nearest == 20.0


def test_widening_practice_inpxs_node_box_makes_the_same_links_members(tmp_path: Path):
    """The counterfactual that proves the emptiness above is geometry and not a
    parser that still returns nothing. Copy the fixture (``data/`` is read-only),
    grow node 1's box from ±15 m to ±25 m, and the four links plus the connector
    that reach 20 m appear."""
    text = PRACTICE_INPX.read_text(encoding="utf-8")
    widened = re.sub(
        r'(<node no="1".*?</node>)',
        lambda m: m.group(1).replace('"-15"', '"-25"').replace('"15"', '"25"'),
        text,
        count=1,
        flags=re.DOTALL,
    )
    assert widened != text
    path = tmp_path / "widened.inpx"
    path.write_text(widened, encoding="utf-8")

    members = inpx_parser._node_link_ids(ET.parse(path).getroot())
    assert members["1"] == {"101", "102", "103", "104", "105", "106", "140", "141", "301"}
    assert members["2"] == set()  # untouched box, still empty


def test_a_genesis_emitted_network_declares_its_movements(tmp_path: Path):
    """practice.inpx's successor. The generator that replaced it draws 30 m node
    boxes around junctions its links stop 12 m short of, so the node contains
    every approach, every exit and every movement through it — the membership
    ``genesis.diff`` needs, from a file with no ``<linkSegs>`` in it anywhere.
    """
    from services.genesis import build_draft, emit_inpx, parse_osm_file
    from tests.test_genesis_osm_draft import crossroads_osm

    source = tmp_path / "cross.osm"
    source.write_text(crossroads_osm(), encoding="utf-8")
    emitted = emit_inpx(build_draft(parse_osm_file(source), name="cross"), tmp_path / "c.inpx")

    root = ET.parse(emitted).getroot()
    assert root.find(".//linkSegment") is None and root.find(".//linkSegs") is None
    members = inpx_parser._node_link_ids(root)
    assert set(members) == {"1"}
    assert members["1"] == {str(i) for i in range(1, 9)} | {
        str(i) for i in range(10001, 10013)
    }

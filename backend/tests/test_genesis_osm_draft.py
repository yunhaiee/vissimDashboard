"""Tests for roadmap row 2.3 Genesis (a) — the OSM → draft ``.inpx`` generator.

Covers ``services/genesis/{osm,project,draft,emit}.py`` and
``backend/tools/genesis_osm_draft.py``.

**Every expected number in this file is derived BY HAND in the test's own
docstring**, from the fixture's coordinates or from MRWA §5 as the extract
states it. A generator test that asserts ``f(x) == f(x)`` proves only that the
code is deterministic, and the failure mode this unit is most exposed to — a
mirrored network, or a lane count invented rather than read — looks perfectly
plausible in every self-consistent check. A reviewer must be able to check the
numbers here with a calculator, the fixture, and the extract.

Five families of test:

1. **Hand-derived topology** (§1, §4) — synthetic ``.osm`` fixtures with known
   answers: a 4-way crossroads with lane tags, a T-junction without them, a
   one-way pair, and ways whose ``highway`` value is not drivable.
2. **The mirror check** (§2) — the sign of the left-hand-traffic side
   assignment, derived by hand and asserted per cardinal direction, plus the
   explicit counterfactual of what right-hand traffic would produce.
3. **The projection bound** (§3) — the module's own error formula measured
   against WGS84 geodesics (a Vincenty inverse implemented here as the oracle),
   at the validity radius and beyond it.
4. **Citation traceability** (§5) — the module's ``CIT_*`` constants really do
   quote the §5 extract, with a mutation counterfactual proving the check bites.
5. **Round-trip against U2's differ** (§7) — the emitted file is read back by
   ``services.inpx_parser`` and compared by ``services.genesis.diff``, which is
   the oracle; no comparison logic is written here.
"""
from __future__ import annotations

import ast
import json
import math
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType

import pytest

from services.genesis import diff as diff_module
from services.genesis import draft as draft_module
from services.genesis import emit as emit_module
from services.genesis import osm as osm_module
from services.genesis import project as project_module
from services.genesis import schema
from services.genesis.draft import (
    ComplianceState,
    INTERSECTION_SETBACK_M,
    LANE_WIDTH_M,
    REASON_FIGURE_LOST,
    TURN_LEFT,
    TURN_RIGHT,
    TURN_THROUGH,
    build_draft,
    classify_turn,
)
from services.genesis.emit import EmitRefused, emit_inpx, to_xml_text
from services.genesis.osm import (
    LANE_SOURCE_CLASS_DEFAULT,
    LANE_SOURCE_TAG,
    LANE_SOURCE_TAG_SPLIT,
    LANE_SOURCE_TURN_LANES,
    SKIP_AREA,
    SKIP_HIGHWAY_NOT_DRIVABLE,
    SKIP_NO_HIGHWAY_TAG,
    left_normal,
    parse_osm_file,
)
from services.genesis.project import (
    MAX_PLANIMETRIC_ERROR_M,
    LocalTangentPlane,
    origin_for_bounds,
)
from services.inpx_parser import parse_network

# The citation comparison is reused, not re-implemented: unit 1.6 factored it
# out of its own test for exactly this ("so the identical comparison can be
# pointed at a deliberately corrupted copy of the module"). One implementation
# means a weakening of the check cannot be introduced here without also
# weakening it there.
from tests.test_dataimport_fixed_time import (  # noqa: E402
    _citation_constants,
    _citation_traceability_failures,
    _normalise_prose,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "backend" / "tools" / "genesis_osm_draft.py"


# ---------------------------------------------------------------------------
# 0. Fixtures
#
# Coordinates are chosen so the arithmetic is checkable by hand. Every fixture
# is centred on lat -32.0700 / lon 115.8360 (Perth, MRWA's jurisdiction), and
# offsets are round numbers of degrees, so the projected metres come out of one
# multiplication.
# ---------------------------------------------------------------------------

LAT0 = -32.0700
LON0 = 115.8360

#: Projected metres for a ±0.003° longitude offset at LAT0.
#: x = N(φ₀)·cos φ₀·Δλ = 6 384 164.1 × cos(32.07°) × (0.003 × π/180)
#:   = 6 384 164.1 × 0.847257 × 5.235988e-5 = 283.26 m.
X_AT_0_003_LON = 283.26

#: Projected metres for a ±0.002° latitude offset at LAT0.
#: y = M(φ₀)·Δφ = 6 353 416.4 × (0.002 × π/180) = 6 353 416.4 × 3.490659e-5
#:   = 221.78 m.
Y_AT_0_002_LAT = 221.78


def _osm_text(nodes: list[tuple[int, float, float]], ways: list[tuple[int, list[int], dict]]) -> str:
    parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6" generator="prism-test">']
    for node_id, lat, lon in nodes:
        parts.append(f'  <node id="{node_id}" lat="{lat:.6f}" lon="{lon:.6f}"/>')
    for way_id, refs, tags in ways:
        parts.append(f'  <way id="{way_id}">')
        parts.extend(f'    <nd ref="{ref}"/>' for ref in refs)
        parts.extend(f'    <tag k="{k}" v="{v}"/>' for k, v in tags.items())
        parts.append("  </way>")
    parts.append("</osm>")
    return "\n".join(parts) + "\n"


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


#: Node ids used across the fixtures: 1 west, 2 centre, 3 east, 4 south, 5 north.
_CROSS_NODES = [
    (1, LAT0, LON0 - 0.003),
    (2, LAT0, LON0),
    (3, LAT0, LON0 + 0.003),
    (4, LAT0 - 0.002, LON0),
    (5, LAT0 + 0.002, LON0),
]


def crossroads_osm() -> str:
    """A 4-way crossroads WITH lane tags, plus one way that must be skipped.

    * way 101 "Main St", nodes 1-2-3, west→east, ``highway=secondary``,
      ``lanes=4``. Two-way, even total ⇒ **2 lanes each direction**, read from
      the tag (not assumed).
    * way 102 "First Ave", nodes 4-2-5, south→north, ``highway=residential``,
      **no lane tag** ⇒ the class default **1 lane each direction**, FLAGGED.
    * way 103, ``highway=footway`` ⇒ skipped by name.

    Node 2 is the only node shared by two kept ways, so it is the only
    intersection.
    """
    return _osm_text(
        _CROSS_NODES + [(9, LAT0 - 0.005, LON0)],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St", "lanes": "4"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (103, [9, 4], {"highway": "footway"}),
        ],
    )


def tee_osm() -> str:
    """A T-junction WITHOUT lane tags.

    * way 201, nodes 1-2-3 (west→east), ``highway=tertiary``, no lane tag.
    * way 202, nodes 4-2 (south→centre), ``highway=residential``, no lane tag.

    Both fall back to the class default of 1 lane per direction, flagged. Node 2
    is interior to way 201 (so way 201 splits in two) and is way 202's LAST
    node (so way 202 does not split).
    """
    return _osm_text(
        _CROSS_NODES[:4],
        [
            (201, [1, 2, 3], {"highway": "tertiary", "name": "Tee Rd"}),
            (202, [4, 2], {"highway": "residential", "name": "Stem St"}),
        ],
    )


def oneway_pair_osm() -> str:
    """A one-way couplet: two parallel one-way carriageways.

    * way 301, nodes 1→2, ``oneway=yes``, ``lanes=2``  — eastbound.
    * way 302, nodes 6→7, ``oneway=-1``, ``lanes=2``   — digitised west→east but
      travelled east→west, so its single direction is the BACKWARD one.

    Each way therefore yields exactly ONE directional link, and neither is
    offset sideways: a one-way OSM way is already its own carriageway's
    centreline.
    """
    return _osm_text(
        [
            (1, LAT0, LON0 - 0.003),
            (2, LAT0, LON0 + 0.003),
            (6, LAT0 + 0.0002, LON0 - 0.003),
            (7, LAT0 + 0.0002, LON0 + 0.003),
        ],
        [
            (301, [1, 2], {"highway": "primary", "oneway": "yes", "lanes": "2"}),
            (302, [6, 7], {"highway": "primary", "oneway": "-1", "lanes": "2"}),
        ],
    )


def unsupported_tags_osm() -> str:
    """One drivable way, and four that must each be skipped BY NAME."""
    return _osm_text(
        _CROSS_NODES,
        [
            (401, [1, 2, 3], {"highway": "residential"}),
            (402, [4, 5], {"highway": "cycleway"}),
            (403, [4, 5], {"highway": "hyperloop_of_the_future"}),
            (404, [4, 5], {"name": "no highway tag at all"}),
            (405, [1, 4, 5, 1], {"highway": "service", "area": "yes"}),
        ],
    )


def dedicated_turn_lane_osm() -> str:
    """A one-way approach whose ``turn:lanes`` declares a DEDICATED left pocket.

    ``turn:lanes=left|through|through`` — three ``|``-separated entries, so
    three lanes, and the first entry names a single movement. §5.3.3.3
    recommends that lane be coded as a separate pocket link; this generator
    codes it as a lane of the approach, so the rule must come back VIOLATED and
    name way 501.
    """
    return _osm_text(
        _CROSS_NODES,
        [
            (
                501,
                [1, 2],
                {
                    "highway": "primary",
                    "oneway": "yes",
                    "turn:lanes": "left|through|through",
                    "name": "Pocket Approach",
                },
            ),
            (502, [2, 5], {"highway": "residential"}),
        ],
    )


def bus_lane_osm() -> str:
    """A way declaring a bus lane, which this emission cannot express."""
    return _osm_text(
        _CROSS_NODES,
        [
            (601, [1, 2], {"highway": "primary", "lanes:bus": "1", "lanes": "4"}),
            (602, [2, 5], {"highway": "residential"}),
        ],
    )


@pytest.fixture()
def crossroads_draft(tmp_path: Path):
    return build_draft(parse_osm_file(_write(tmp_path, "cross.osm", crossroads_osm())), name="cross")


def _links_named(draft, fragment: str):
    return [link for link in draft.links if fragment in link.name]


def _one(items):
    assert len(items) == 1, f"expected exactly one, got {len(items)}: {items}"
    return items[0]


# ---------------------------------------------------------------------------
# 1. Reading the .osm — allowlist, skips, lane provenance
# ---------------------------------------------------------------------------


def test_only_allowlisted_highway_values_become_ways(tmp_path: Path):
    """``crossroads_osm`` has three ways; ``highway=footway`` is not drivable, so
    exactly two are kept."""
    extract = parse_osm_file(_write(tmp_path, "a.osm", crossroads_osm()))
    assert not extract.refused
    assert sorted(w.id for w in extract.ways) == [101, 102]


def test_every_way_is_either_kept_or_named_in_the_skip_list(tmp_path: Path):
    """Nothing is dropped silently: seen == kept + skipped, and every skip
    carries a reason code and the highway value it was rejected on.

    ``unsupported_tags_osm`` has 5 ways: 1 residential (kept), a cycleway, an
    unheard-of value, one with no ``highway`` tag at all, and an ``area=yes``
    surface — 4 skips.
    """
    extract = parse_osm_file(_write(tmp_path, "b.osm", unsupported_tags_osm()))
    assert extract.ways_seen == 5
    assert len(extract.ways) == 1
    assert len(extract.skipped) == 4
    assert extract.ways_seen == len(extract.ways) + len(extract.skipped)

    by_id = {skip.id: skip for skip in extract.skipped}
    assert by_id[402].reason == SKIP_HIGHWAY_NOT_DRIVABLE and by_id[402].highway == "cycleway"
    assert by_id[403].reason == SKIP_HIGHWAY_NOT_DRIVABLE
    assert by_id[403].highway == "hyperloop_of_the_future"
    assert by_id[404].reason == SKIP_NO_HIGHWAY_TAG
    assert by_id[405].reason == SKIP_AREA
    assert all(skip.detail for skip in extract.skipped)


def test_the_allowlist_rejects_a_value_it_has_never_heard_of(tmp_path: Path):
    """The point of an allowlist over a denylist: OSM's tag vocabulary grows,
    and an unknown value must be refused rather than admitted by default."""
    extract = parse_osm_file(_write(tmp_path, "c.osm", unsupported_tags_osm()))
    assert 403 not in {way.id for way in extract.ways}
    assert "hyperloop_of_the_future" not in osm_module.DRIVABLE_HIGHWAY_DEFAULT_LANES


def test_lane_count_from_the_tag_is_not_flagged_as_assumed(tmp_path: Path):
    """Main St: ``lanes=4`` on a two-way way is the TOTAL across both
    directions, so 4 ÷ 2 = **2 lanes each way**, provenance ``lanes``, and the
    split of an even total is arithmetic — not an assumption."""
    extract = parse_osm_file(_write(tmp_path, "d.osm", crossroads_osm()))
    main = _one([w for w in extract.ways if w.id == 101])
    assert [d.lanes for d in main.directions] == [2, 2]
    for direction in main.directions:
        assert direction.provenance.source == LANE_SOURCE_TAG_SPLIT
        assert direction.provenance.osm_key == "lanes"
        assert direction.provenance.raw_value == "4"
        assert direction.provenance.assumed is False


def test_absent_lane_tag_is_a_flagged_class_default(tmp_path: Path):
    """First Ave carries no lane tag at all, so the generator supplies
    ``residential`` → 1 lane per direction and says so: ``assumed=True``, the
    key that drove it (``highway``) and its raw value (``residential``)."""
    extract = parse_osm_file(_write(tmp_path, "e.osm", crossroads_osm()))
    first = _one([w for w in extract.ways if w.id == 102])
    assert [d.lanes for d in first.directions] == [1, 1]
    for direction in first.directions:
        assert direction.provenance.assumed is True
        assert direction.provenance.source == LANE_SOURCE_CLASS_DEFAULT
        assert (direction.provenance.osm_key, direction.provenance.raw_value) == (
            "highway",
            "residential",
        )


def test_odd_two_way_lane_total_is_split_and_the_split_is_flagged(tmp_path: Path):
    """``lanes=3`` on a two-way way: OSM does not say which direction has the
    extra lane. 3 → ceil 2 forward / floor 1 backward, and BOTH provenances say
    ``assumed=True`` because the assignment is the generator's guess."""
    text = _osm_text(
        _CROSS_NODES,
        [(701, [1, 2, 3], {"highway": "secondary", "lanes": "3"})],
    )
    way = _one(parse_osm_file(_write(tmp_path, "f.osm", text)).ways)
    assert [d.lanes for d in way.directions] == [2, 1]
    assert all(d.provenance.assumed for d in way.directions)
    assert all("ODD" in d.provenance.note for d in way.directions)


def test_turn_lanes_entry_count_is_the_lane_count(tmp_path: Path):
    """``turn:lanes=left|through|through`` is three ``|``-separated entries, so
    three lanes — read from the file, not assumed."""
    extract = parse_osm_file(_write(tmp_path, "g.osm", dedicated_turn_lane_osm()))
    way = _one([w for w in extract.ways if w.id == 501])
    direction = _one(list(way.directions))
    assert direction.lanes == 3
    assert direction.provenance.source == LANE_SOURCE_TURN_LANES
    assert direction.provenance.assumed is False
    assert direction.dedicated_turn_lanes == ("left",)


def test_unreadable_lane_value_falls_through_to_the_flagged_default(tmp_path: Path):
    """``lanes=1.5`` is a real OSM value (a single-track road). It is NOT
    coerced to 1 or 2 — a value this module cannot read unambiguously must land
    on the visible, flagged default rather than on an invisible guess."""
    text = _osm_text(_CROSS_NODES, [(702, [1, 2], {"highway": "residential", "lanes": "1.5"})])
    way = _one(parse_osm_file(_write(tmp_path, "h.osm", text)).ways)
    assert all(d.provenance.assumed for d in way.directions)
    assert all(d.provenance.osm_key == "highway" for d in way.directions)


@pytest.mark.parametrize(
    ("tags", "expected_oneway", "expected_assumed"),
    [
        ({"highway": "residential", "oneway": "yes"}, 1, False),
        ({"highway": "residential", "oneway": "-1"}, -1, False),
        ({"highway": "residential", "oneway": "no"}, 0, False),
        ({"highway": "residential"}, 0, True),
        ({"highway": "motorway"}, 1, True),
        ({"highway": "residential", "junction": "roundabout"}, 1, True),
        ({"highway": "residential", "oneway": "alternating"}, 0, True),
    ],
)
def test_oneway_reading_and_its_provenance(
    tmp_path: Path, tags: dict, expected_oneway: int, expected_assumed: bool
):
    """Explicit ``oneway`` wins and is not flagged. The two OSM *conventions* —
    a roundabout junction and a motorway class — are applied but flagged, since
    they are community practice rather than a statement in this file. An
    ``oneway`` value the module cannot read (``alternating``) is treated as
    two-way AND flagged, never silently taken as one-way."""
    text = _osm_text(_CROSS_NODES, [(801, [1, 2], tags)])
    way = _one(parse_osm_file(_write(tmp_path, "i.osm", text)).ways)
    assert way.oneway == expected_oneway
    assert way.oneway_provenance.assumed is expected_assumed
    assert len(way.directions) == (1 if expected_oneway else 2)


def test_intersections_are_the_nodes_two_kept_ways_share(tmp_path: Path):
    """In ``crossroads_osm`` only node 2 belongs to both kept ways. Nodes 1/3/4/5
    are termini of a single way, and node 9 belongs only to the skipped
    footway."""
    extract = parse_osm_file(_write(tmp_path, "j.osm", crossroads_osm()))
    assert extract.intersection_node_ids() == {2}


def test_a_way_whose_nodes_are_missing_is_dropped_by_name_not_shortened(tmp_path: Path):
    """A clipped extract references nodes it does not contain. Shortening the
    polyline would silently change link lengths — which is exactly what
    §5.3.2.1's queue-containment rule depends on — so the way is dropped and
    named instead."""
    text = _osm_text(
        [(1, LAT0, LON0 - 0.003), (2, LAT0, LON0)],
        [
            (901, [1, 2], {"highway": "residential"}),
            (902, [1, 2, 999], {"highway": "residential"}),
        ],
    )
    extract = parse_osm_file(_write(tmp_path, "k.osm", text))
    assert [w.id for w in extract.ways] == [901]
    assert 902 in {skip.id for skip in extract.skipped}


def test_an_osm_with_nothing_drivable_is_refused_not_returned_empty(tmp_path: Path):
    text = _osm_text(_CROSS_NODES, [(1001, [1, 2], {"highway": "footway"})])
    extract = parse_osm_file(_write(tmp_path, "l.osm", text))
    assert extract.refused
    assert extract.refusals[0].code == osm_module.REFUSE_NO_DRIVABLE_WAYS


def test_a_missing_or_malformed_osm_file_is_refused_not_raised(tmp_path: Path):
    missing = parse_osm_file(tmp_path / "nope.osm")
    assert missing.refused and missing.refusals[0].code == osm_module.REFUSE_FILE_MISSING
    broken = parse_osm_file(_write(tmp_path, "m.osm", "<osm><way></osm>"))
    assert broken.refused and broken.refusals[0].code == osm_module.REFUSE_UNPARSEABLE


# ---------------------------------------------------------------------------
# 2. Left-hand traffic — the silent-mirror bug, asserted by sign
#
# MRWA §5.2.1.1 (doc p.180): "Left-hand traffic should be adopted for Traffic
# regulations." A driver keeps LEFT, so a direction's carriageway sits to the
# left of its own travel direction. In a +x-east / +y-north frame "left" is the
# +90° (counter-clockwise) normal.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("direction", "vector", "expected_normal"),
    [
        ("east", (1.0, 0.0), (0.0, 1.0)),
        ("north", (0.0, 1.0), (-1.0, 0.0)),
        ("west", (-1.0, 0.0), (0.0, -1.0)),
        ("south", (0.0, -1.0), (1.0, 0.0)),
    ],
)
def test_left_normal_is_the_counter_clockwise_normal(direction, vector, expected_normal):
    """Hand-derived, one per cardinal direction. Facing EAST your left hand
    points NORTH: (1,0) → (0,+1). Facing NORTH it points WEST: (0,1) → (−1,0).
    Rotating the frame 90° each time gives the other two."""
    assert left_normal(*vector) == pytest.approx(expected_normal, abs=1e-12)


def test_eastbound_carriageway_sits_NORTH_of_the_centreline(crossroads_draft):
    """Main St runs west→east along y = 0 (all its nodes are at LAT0).

    Hand-derived: the eastbound link travels (+1, 0); its left normal is (0, +1)
    = north; Main St has 2 lanes per direction so the offset is
    2 × 3.5 / 2 = **+3.5 m**. Every point of the eastbound link must therefore
    have **y = +3.5**.

    Under RIGHT-hand traffic every one of these would be −3.5, the network would
    still look correct in a rendering, and every turn would be wrong. That is
    the bug this assertion exists for.
    """
    eastbound = _links_named(crossroads_draft, "Main St [FWD]")
    assert eastbound, "no eastbound Main St link"
    for link in eastbound:
        assert link.lanes == 2
        assert link.side_offset_m == pytest.approx(3.5)
        assert link.points[-1][0] > link.points[0][0], "not travelling east"
        for _x, y in link.points:
            assert y == pytest.approx(3.5, abs=1e-6)


def test_westbound_carriageway_sits_SOUTH_of_the_centreline(crossroads_draft):
    """The reverse direction of the same way travels (−1, 0), whose left normal
    is (0, −1) = south. Same 3.5 m magnitude, opposite sign: **y = −3.5**."""
    westbound = _links_named(crossroads_draft, "Main St [REV]")
    assert westbound
    for link in westbound:
        assert link.points[-1][0] < link.points[0][0], "not travelling west"
        for _x, y in link.points:
            assert y == pytest.approx(-3.5, abs=1e-6)


def test_northbound_carriageway_sits_WEST_of_the_centreline(crossroads_draft):
    """First Ave runs south→north along x = 0. Northbound travels (0, +1),
    whose left normal is (−1, 0) = west. First Ave has 1 lane per direction, so
    the offset is 1 × 3.5 / 2 = **1.75 m** and every point has **x = −1.75**."""
    northbound = _links_named(crossroads_draft, "First Ave [FWD]")
    assert northbound
    for link in northbound:
        assert link.lanes == 1
        assert link.side_offset_m == pytest.approx(1.75)
        assert link.points[-1][1] > link.points[0][1], "not travelling north"
        for x, _y in link.points:
            assert x == pytest.approx(-1.75, abs=1e-6)


def test_southbound_carriageway_sits_EAST_of_the_centreline(crossroads_draft):
    """Travelling (0, −1), left normal (+1, 0) = east: **x = +1.75**."""
    southbound = _links_named(crossroads_draft, "First Ave [REV]")
    assert southbound
    for link in southbound:
        assert link.points[-1][1] < link.points[0][1], "not travelling south"
        for x, _y in link.points:
            assert x == pytest.approx(1.75, abs=1e-6)


def test_the_two_directions_straddle_the_centreline_symmetrically(crossroads_draft):
    """A mirrored network is self-consistent, so symmetry alone is not the test —
    the four sign assertions above are. This adds the cross-check that the pair
    is symmetric about the shared centreline (their offsets sum to zero), which
    catches an offset applied to only one of the two directions."""
    east = _one(_links_named(crossroads_draft, "Main St [FWD] w101s0"))
    west = _one(_links_named(crossroads_draft, "Main St [REV] w101s0"))
    assert east.points[0][1] + west.points[0][1] == pytest.approx(0.0, abs=1e-9)


def test_a_right_hand_traffic_generator_would_produce_the_opposite_sign(crossroads_draft):
    """The counterfactual, spelled out: right-hand traffic is the same geometry
    with the normal negated. Asserting that the produced sign is the OPPOSITE of
    the right-hand result is what makes "we chose left-hand" a claim with
    content rather than a comment."""
    east = _one(_links_named(crossroads_draft, "Main St [FWD] w101s0"))
    right_hand_normal = tuple(-component for component in left_normal(1.0, 0.0))
    right_hand_y = right_hand_normal[1] * east.side_offset_m
    assert right_hand_y == pytest.approx(-3.5)
    assert east.points[0][1] == pytest.approx(-right_hand_y)


def test_a_one_way_way_is_placed_on_its_own_centreline_not_offset(tmp_path: Path):
    """A one-way OSM way is drawn where its carriageway actually is (a couplet
    or a dual carriageway is mapped as two separate ways), so offsetting it
    would slide the road sideways by half its width. Offset must be **0**, and
    the link must lie on the way's own line."""
    draft = build_draft(
        parse_osm_file(_write(tmp_path, "n.osm", oneway_pair_osm())), name="pair"
    )
    assert len(draft.links) == 2, "one directional link per one-way way"
    for link in draft.links:
        assert link.side_offset_m == pytest.approx(0.0)
        # lanes=2 on a ONE-way way is that direction's own count, not a total to
        # halve — read from the tag, so nothing here is assumed.
        assert link.lanes == 2
        assert link.lane_provenance["source"] == LANE_SOURCE_TAG
        assert link.lane_provenance["assumed"] is False
    eastbound = _one([l for l in draft.links if l.osm_way_id == 301])
    assert eastbound.forward is True
    assert eastbound.points[-1][0] > eastbound.points[0][0]
    westbound = _one([l for l in draft.links if l.osm_way_id == 302])
    assert westbound.forward is False, "oneway=-1 travels against the digitised order"
    assert westbound.points[-1][0] < westbound.points[0][0]


# ---------------------------------------------------------------------------
# 3. The projection, and its error bound
#
# Oracle: Vincenty's inverse formula on WGS84 — an exact geodesic distance to
# well under a millimetre at these ranges. It is implemented here rather than
# imported because the module under test must not supply its own oracle, and
# because a spherical haversine is NOT adequate: with a single mean radius it
# disagrees with the ellipsoid by 0.34 % (9 m over 3 km at the equator), which
# would swamp the sub-metre effect being measured.
# ---------------------------------------------------------------------------

_A = 6378137.0
_F = 1 / 298.257223563
_B = _A * (1 - _F)


def vincenty_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """WGS84 geodesic distance, metres. Vincenty inverse."""
    L = math.radians(lon2 - lon1)
    u1 = math.atan((1 - _F) * math.tan(math.radians(lat1)))
    u2 = math.atan((1 - _F) * math.tan(math.radians(lat2)))
    su1, cu1 = math.sin(u1), math.cos(u1)
    su2, cu2 = math.sin(u2), math.cos(u2)
    lam = L
    sin_sigma = cos_sigma = sigma = cos2_alpha = cos2_sigma_m = 0.0
    for _ in range(200):
        sl, cl = math.sin(lam), math.cos(lam)
        sin_sigma = math.hypot(cu2 * sl, cu1 * su2 - su1 * cu2 * cl)
        if sin_sigma == 0:
            return 0.0
        cos_sigma = su1 * su2 + cu1 * cu2 * cl
        sigma = math.atan2(sin_sigma, cos_sigma)
        sin_alpha = cu1 * cu2 * sl / sin_sigma
        cos2_alpha = 1 - sin_alpha**2
        cos2_sigma_m = cos_sigma - 2 * su1 * su2 / cos2_alpha if cos2_alpha else 0.0
        c = _F / 16 * cos2_alpha * (4 + _F * (4 - 3 * cos2_alpha))
        previous = lam
        lam = L + (1 - c) * _F * sin_alpha * (
            sigma
            + c * sin_sigma * (cos2_sigma_m + c * cos_sigma * (-1 + 2 * cos2_sigma_m**2))
        )
        if abs(lam - previous) < 1e-14:
            break
    u_sq = cos2_alpha * (_A * _A - _B * _B) / (_B * _B)
    big_a = 1 + u_sq / 16384 * (4096 + u_sq * (-768 + u_sq * (320 - 175 * u_sq)))
    big_b = u_sq / 1024 * (256 + u_sq * (-128 + u_sq * (74 - 47 * u_sq)))
    d_sigma = (
        big_b
        * sin_sigma
        * (
            cos2_sigma_m
            + big_b
            / 4
            * (
                cos_sigma * (-1 + 2 * cos2_sigma_m**2)
                - big_b
                / 6
                * cos2_sigma_m
                * (-3 + 4 * sin_sigma**2)
                * (-3 + 4 * cos2_sigma_m**2)
            )
        )
    )
    return _B * big_a * (sigma - d_sigma)


def _worst_distance_error_m(plane: LocalTangentPlane, half_extent: float, grid: int = 7) -> float:
    """Largest |planar − geodesic| over every pair of a grid spanning the box."""
    points = []
    for i in range(grid):
        for j in range(grid):
            x = -half_extent + 2 * half_extent * i / (grid - 1)
            y = -half_extent + 2 * half_extent * j / (grid - 1)
            points.append(((x, y), plane.inverse(x, y)))
    worst = 0.0
    for index, ((xa, ya), (lat_a, lon_a)) in enumerate(points):
        for (xb, yb), (lat_b, lon_b) in points[index + 1 :]:
            planar = math.hypot(xb - xa, yb - ya)
            worst = max(worst, abs(planar - vincenty_m(lat_a, lon_a, lat_b, lon_b)))
    return worst


def test_projection_round_trips_a_known_coordinate_pair():
    """forward() then inverse() must return the input. The transform is two
    multiplications, so its inverse is exact to float precision — anything
    looser would mean the recorded origin does not actually reproduce it."""
    plane = LocalTangentPlane(LAT0, LON0)
    for lat, lon in ((LAT0, LON0), (LAT0 + 0.002, LON0 - 0.003), (-32.05, 115.85)):
        x, y = plane.forward(lat, lon)
        back_lat, back_lon = plane.inverse(x, y)
        assert (back_lat, back_lon) == pytest.approx((lat, lon), abs=1e-11)


def test_projected_metres_match_the_hand_derived_arithmetic():
    """x = N(φ₀)·cos φ₀·Δλ and y = M(φ₀)·Δφ, with N = 6 384 164.1 m and
    M = 6 353 416.4 m at φ₀ = −32.07°:

        Δλ = 0.003° = 5.235988e-5 rad → x = 6384164.1 × 0.847257 × 5.235988e-5
                                          = 283.26 m
        Δφ = 0.002° = 3.490659e-5 rad → y = 6353416.4 × 3.490659e-5 = 221.78 m
    """
    plane = origin_for_bounds(LAT0 - 0.002, LON0 - 0.003, LAT0 + 0.002, LON0 + 0.003)
    assert (plane.lat0_deg, plane.lon0_deg) == pytest.approx((LAT0, LON0))
    assert plane.prime_vertical_radius_m == pytest.approx(6384164.1, abs=0.1)
    assert plane.meridional_radius_m == pytest.approx(6353416.4, abs=0.1)
    assert plane.forward(LAT0, LON0 + 0.003)[0] == pytest.approx(X_AT_0_003_LON, abs=0.01)
    assert plane.forward(LAT0 + 0.002, LON0)[1] == pytest.approx(Y_AT_0_002_LAT, abs=0.01)


def test_the_documented_error_bound_holds_at_the_validity_radius():
    """The module claims ε_xy(d) = 2·d²·tan|φ₀|/R and solves it for a 1.0 m
    budget. Measured against Vincenty geodesics over a 7×7 grid spanning the box
    at exactly that radius, the worst pairwise distance error must be at or
    under the budget — for Perth, for Daejeon, and for a high latitude where the
    radius is much smaller."""
    for lat in (-32.07, -35.0, 36.35, 60.0):
        plane = LocalTangentPlane(lat, 115.836)
        radius = plane.validity_radius_m
        measured = _worst_distance_error_m(plane, radius)
        assert measured <= MAX_PLANIMETRIC_ERROR_M + 1e-6, (lat, radius, measured)
        assert plane.planimetric_error_bound_m(radius) == pytest.approx(
            MAX_PLANIMETRIC_ERROR_M, rel=1e-9
        )


def test_the_error_bound_is_tight_rather_than_a_safe_overestimate():
    """A bound of "1000 m" would also pass the test above. At the validity
    radius the MEASURED worst case must come within 1 % of the stated bound —
    i.e. the formula describes the real dominant term, not a comfortable
    cushion. (Measured/predicted at −32.07° is 0.998/1.000.)"""
    plane = LocalTangentPlane(-32.07, 115.836)
    measured = _worst_distance_error_m(plane, plane.validity_radius_m)
    assert measured >= 0.99 * MAX_PLANIMETRIC_ERROR_M


def test_the_convergence_term_vanishes_at_the_equator_and_the_measurement_agrees():
    """tan(0) = 0, so the convergence term is exactly zero on the equator and
    the curvature term becomes what binds. The measured error there must be
    ~0 (millimetres at 5 km), which is the check that the formula names the
    right physics: the d²/2R sagitta is an OUT-of-plane number and does not
    enter 2-D distances."""
    equator = LocalTangentPlane(0.0, 0.0)
    assert equator.binding_term == project_module.BINDING_CURVATURE
    assert equator.planimetric_error_bound_m(5000.0) == pytest.approx(0.0)
    assert _worst_distance_error_m(equator, 5000.0) < 0.01
    # ...and the sagitta the module quotes is real: 3565.6² / (2 × 6 356 752) ≈ 1.0 m
    assert equator.curvature_drop_m(equator.validity_radius_m) == pytest.approx(1.0, abs=1e-3)


def test_the_bound_is_exceeded_beyond_the_validity_radius():
    """Doubling the half-extent quadruples a quadratic term: at 2× the radius
    the measured error must exceed the budget, so the radius is a real edge and
    not decoration."""
    plane = LocalTangentPlane(-32.07, 115.836)
    beyond = plane.validity_radius_m * 2
    assert plane.planimetric_error_bound_m(beyond) > MAX_PLANIMETRIC_ERROR_M
    assert _worst_distance_error_m(plane, beyond) > MAX_PLANIMETRIC_ERROR_M


@pytest.mark.parametrize(
    ("lat", "expected_term"),
    [
        (0.0, project_module.BINDING_CURVATURE),
        (10.0, project_module.BINDING_CURVATURE),
        (-32.07, project_module.BINDING_CONVERGENCE),
        (36.35, project_module.BINDING_CONVERGENCE),
    ],
)
def test_which_term_binds_flips_at_arctan_one_quarter(lat, expected_term):
    """√(εR/2tanφ) < √(2εR) ⟺ tan φ > ¼, i.e. |φ| > 14.036°."""
    assert project_module.CONVERGENCE_BINDS_ABOVE_DEG == pytest.approx(14.0362, abs=1e-3)
    assert LocalTangentPlane(lat, 0.0).binding_term == expected_term


def test_check_extent_refuses_rather_than_degrading():
    plane = LocalTangentPlane(-32.07, 115.836)
    ok = plane.check_extent(plane.validity_radius_m - 1.0)
    assert ok.ok and "within" in ok.message
    bad = plane.check_extent(plane.validity_radius_m + 1.0)
    assert not bad.ok
    assert "REFUSED" in bad.message and "validity radius" in bad.message


def test_generation_is_refused_when_the_bounding_box_is_too_big(tmp_path: Path):
    """A 0.10° × 0.10° box at Perth is ~5.6 km across — half-extent ~4.7 km,
    over the 2.25 km radius — so ``build_draft`` refuses, names the reason, and
    returns no links at all rather than a network with silently wrong shape."""
    text = _osm_text(
        [(1, LAT0 - 0.05, LON0 - 0.05), (2, LAT0, LON0), (3, LAT0 + 0.05, LON0 + 0.05)],
        [(1101, [1, 2, 3], {"highway": "primary"})],
    )
    draft = build_draft(parse_osm_file(_write(tmp_path, "big.osm", text)), name="big")
    assert draft.refused
    assert draft.links == []
    assert any("projection_out_of_range" in reason for reason in draft.refusals)
    assert any("validity radius" in reason for reason in draft.refusals)


def test_the_origin_is_recorded_so_the_transform_is_reproducible(crossroads_draft):
    """The origin is the ONE thing that makes these metres invertible, so it
    rides in the metadata — and next to it, the statement that recording a
    transform is not claiming a georeference."""
    projection = crossroads_draft.metadata["projection"]
    assert projection["origin_lat_deg"] == pytest.approx(LAT0)
    assert projection["origin_lon_deg"] == pytest.approx(LON0)
    assert projection["georeferenced"] is False
    assert "no datum" in projection["note"]
    plane = LocalTangentPlane(projection["origin_lat_deg"], projection["origin_lon_deg"])
    assert plane.forward(LAT0, LON0 + 0.003)[0] == pytest.approx(X_AT_0_003_LON, abs=0.01)


# ---------------------------------------------------------------------------
# 4. Draft topology — hand-derived counts and turns
# ---------------------------------------------------------------------------


def test_crossroads_link_count_matches_hand_derivation(crossroads_draft):
    """Way 101 splits at node 2 into segments (1,2) and (2,3); way 102 splits at
    node 2 into (4,2) and (2,5). Both ways are two-way, so:

        4 segments × 2 directions = **8 links**

    with Main St's four links carrying 2 lanes and First Ave's four carrying 1.
    """
    assert len(crossroads_draft.links) == 8
    assert sorted(l.lanes for l in crossroads_draft.links) == [1, 1, 1, 1, 2, 2, 2, 2]
    assert len(crossroads_draft.nodes) == 1
    assert crossroads_draft.nodes[0].osm_id == 2


def test_crossroads_connector_count_matches_hand_derivation(crossroads_draft):
    """At node 2 there are 4 approaches (each of the four segment-directions
    that ENDS there) and 4 exits. 4 × 4 = 16 ordered pairs, of which the 4 that
    turn a carriageway back onto its own reverse are U-turns and are suppressed:

        16 − 4 = **12 connectors**
    """
    assert len(crossroads_draft.connectors) == 12
    assert all(c.node_osm_id == 2 for c in crossroads_draft.connectors)
    assert len({c.id for c in crossroads_draft.connectors}) == 12


def test_no_connector_turns_a_carriageway_back_onto_itself(crossroads_draft):
    """The suppressed 4: for each segment, its forward link into its own reverse
    link. Asserted as a property so a future refactor cannot reintroduce them."""
    by_id = {link.id: link for link in crossroads_draft.links}
    for connector in crossroads_draft.connectors:
        approach, exit_link = by_id[connector.from_link], by_id[connector.to_link]
        assert approach.segment_key != exit_link.segment_key
        assert connector.turn != draft_module.TURN_U


def test_crossroads_turn_types_match_hand_derivation(crossroads_draft):
    """From the eastbound Main St approach (heading (+1, 0)) at node 2:

    * → eastbound Main St beyond the junction, heading (+1, 0):
      angle 0° ⇒ **through**
    * → northbound First Ave, heading (0, +1): cross = 1·1 − 0·0 = +1,
      dot = 0 ⇒ +90° ⇒ **left** (the near-side turn under left-hand traffic)
    * → southbound First Ave, heading (0, −1): cross = 1·(−1) − 0·0 = −1,
      dot = 0 ⇒ −90° ⇒ **right** (the across-traffic turn)

    Four approaches each give exactly one of each, so the whole crossroads is
    4 through / 4 left / 4 right.
    """
    counts = {turn: 0 for turn in (TURN_THROUGH, TURN_LEFT, TURN_RIGHT)}
    for connector in crossroads_draft.connectors:
        counts[connector.turn] += 1
    assert counts == {TURN_THROUGH: 4, TURN_LEFT: 4, TURN_RIGHT: 4}

    approach = _one(_links_named(crossroads_draft, "Main St [FWD] w101s0"))
    from_east = {
        c.turn: c for c in crossroads_draft.connectors if c.from_link == approach.id
    }
    assert set(from_east) == {TURN_THROUGH, TURN_LEFT, TURN_RIGHT}
    assert from_east[TURN_LEFT].heading_change_deg == pytest.approx(90.0, abs=1.0)
    assert from_east[TURN_RIGHT].heading_change_deg == pytest.approx(-90.0, abs=1.0)
    assert from_east[TURN_THROUGH].heading_change_deg == pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize(
    ("out_vector", "expected"),
    [((1.0, 0.0), TURN_THROUGH), ((0.0, 1.0), TURN_LEFT), ((0.0, -1.0), TURN_RIGHT)],
)
def test_classify_turn_signs(out_vector, expected):
    """The classifier on its own, so the crossroads test above is checking
    topology rather than trigonometry."""
    turn, _angle = classify_turn(1.0, 0.0, *out_vector)
    assert turn == expected


def test_tee_junction_counts_match_hand_derivation(tmp_path: Path):
    """Way 201 (1-2-3) splits at node 2 into 2 segments; way 202 (4-2) has node 2
    as its LAST node, so it does not split — 1 segment. Both two-way:

        (2 + 1) segments × 2 directions = **6 links**

    At node 2: 3 approaches, 3 exits, 9 pairs, minus 3 U-turns = **6
    connectors**. Every link carries the flagged class default of 1 lane.
    """
    draft = build_draft(parse_osm_file(_write(tmp_path, "t.osm", tee_osm())), name="tee")
    assert len(draft.links) == 6
    assert len(draft.connectors) == 6
    assert {link.lanes for link in draft.links} == {1}
    assert all(link.lane_provenance["assumed"] for link in draft.links)


def test_links_stop_short_of_the_junction_by_the_setback(crossroads_draft):
    """Main St's western segment runs from x = −283.26 to x = 0 on the
    centreline. Node 2 is an intersection, so the link's downstream end is cut
    back by INTERSECTION_SETBACK_M = 12.0 m and must end at x = −12.0; its
    upstream end (node 1, a terminus) is not cut, so it starts at −283.26.
    Length = 283.26 − 12.0 = **271.26 m**, which is also the arc-length the
    connectors leaving it must report."""
    link = _one(_links_named(crossroads_draft, "Main St [FWD] w101s0"))
    assert link.points[0][0] == pytest.approx(-X_AT_0_003_LON, abs=0.01)
    assert link.points[-1][0] == pytest.approx(-INTERSECTION_SETBACK_M, abs=1e-6)
    assert link.length_m == pytest.approx(X_AT_0_003_LON - INTERSECTION_SETBACK_M, abs=0.01)
    for connector in crossroads_draft.connectors:
        if connector.from_link == link.id:
            assert connector.from_pos_m == pytest.approx(link.length_m, abs=1e-3)
            assert connector.to_pos_m == 0.0


def test_connector_endpoints_reference_links_that_exist(crossroads_draft):
    ids = {link.id for link in crossroads_draft.links}
    for connector in crossroads_draft.connectors:
        assert connector.from_link in ids and connector.to_link in ids
        assert connector.lanes == min(
            crossroads_draft.link_by_id(connector.from_link).lanes,
            crossroads_draft.link_by_id(connector.to_link).lanes,
        )


def test_lane_gain_and_drop_movements_are_labelled(crossroads_draft):
    """Main St (2 lanes) ↔ First Ave (1 lane) gives 8 movements that change lane
    count — 4 drops leaving Main St and 4 gains entering it — and 4 that do not
    (the 2 Main-through and 2 Ave-through movements)."""
    labels = [connector.lane_change for connector in crossroads_draft.connectors]
    assert labels.count("same") == 4
    assert labels.count("gain") + labels.count("drop") == 8


def test_ids_and_ordering_are_stable_across_two_builds(tmp_path: Path):
    path = _write(tmp_path, "stable.osm", crossroads_osm())
    first = build_draft(parse_osm_file(path), name="s")
    second = build_draft(parse_osm_file(path), name="s")
    assert [l.id for l in first.links] == [l.id for l in second.links]
    assert [c.id for c in first.connectors] == [c.id for c in second.connectors]
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


# ---------------------------------------------------------------------------
# 5. Citation traceability — and the counterfactual that proves it bites
# ---------------------------------------------------------------------------

#: ``(module, inventory floor)``. The floors exist because discovery finding
#: FEWER constants than expected would otherwise read as a pass. ``draft``
#: re-exports the two ``osm`` citations by importing them, hence 16 + 2.
_CITED_MODULES = [
    pytest.param(osm_module, 2, id="osm"),
    pytest.param(draft_module, 18, id="draft"),
]


def _extract_text() -> str:
    return _normalise_prose((REPO_ROOT / osm_module.EXTRACT).read_text(encoding="utf-8"))


def test_the_section_5_extract_is_a_file_on_this_machine():
    """Traceability is only checkable while the source ships with the repo. If
    the extract moves or empties, every citation in this package becomes
    unfalsifiable prose, and this is the test that says so."""
    path = REPO_ROOT / osm_module.EXTRACT
    assert path.is_file(), f"{path} is missing — genesis.osm.EXTRACT points nowhere"
    assert "MRWA OMG v2.0 §5" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(("module", "minimum_constants"), _CITED_MODULES)
def test_genesis_citations_quote_the_section_5_extract_verbatim(module, minimum_constants):
    """Every ``CIT_*`` constant claims to quote the §5 extract; this reads those
    constants out of the module by introspection and checks the claim, quote by
    quote. The asserted text IS the module's own text, so editing a citation
    away from its source — or editing the extract out from under one — fails and
    names the constant."""
    constants = _citation_constants(module)
    assert len(constants) >= minimum_constants, f"only found {sorted(constants)}"
    for name, citation in sorted(constants.items()):
        assert module.EXTRACT in citation, f"{name} does not name the file it cites"

    unquotable, missing = _citation_traceability_failures(
        module, module.EXTRACT, _extract_text()
    )
    assert unquotable == [], f"citations quoting nothing checkable: {unquotable}"
    assert missing == [], f"quoted in {module.__name__}, absent from the extract: {missing}"


def _citations_from_source(path: Path, name: str) -> ModuleType:
    """Load ONLY the ``CIT_*`` string constants out of a source FILE.

    ``draft.py`` uses package-relative imports and so cannot be imported from a
    copy sitting in a scratch directory. But the comparison under test reads
    nothing except ``vars(module)``, and every citation is a plain (f-)string
    over ``EXTRACT``. Evaluating just the module-level ``CIT_*`` assignments —
    and only those whose right-hand side is a literal or an f-string, so nothing
    else in the file can run — reproduces exactly the surface the check looks
    at, **from the file's real text on disk**.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    namespace: dict[str, str] = {"EXTRACT": osm_module.EXTRACT}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not target.id.startswith("CIT_"):
            continue
        if not isinstance(node.value, (ast.Constant, ast.JoinedStr)):
            continue
        value = eval(  # noqa: S307 - literals and f-strings only, no builtins
            compile(ast.Expression(node.value), str(path), "eval"),
            {"__builtins__": {}},
            namespace,
        )
        if isinstance(value, str):
            namespace[target.id] = value

    module = ModuleType(name)
    for key, value in namespace.items():
        setattr(module, key, value)
    return module


def test_a_digit_swapped_citation_fails_the_traceability_check(tmp_path: Path):
    """The mutation counterfactual, run against a real copy of the file.

    ``draft.py`` is copied to a scratch directory with ONE digit changed inside
    a quotation — §5.3.3.1's "500 meters" becomes "600 meters" — and the same
    comparison must then fail and name the constant. The untouched copy, loaded
    the identical way, must still pass.

    Without this, "the citations quote the extract" could be satisfied by a
    check that never actually compares anything, which is the failure mode the
    Appendix A unit's own citation test was rewritten to close.
    """
    source = REPO_ROOT / "backend" / "services" / "genesis" / "draft.py"
    text = source.read_text(encoding="utf-8")
    assert text.count("500 meters is recommended") == 1

    honest_copy = tmp_path / "draft_honest.py"
    honest_copy.write_text(text, encoding="utf-8")
    honest = _citations_from_source(honest_copy, "draft_honest")
    assert len(_citation_constants(honest)) >= 16
    assert _citation_traceability_failures(honest, osm_module.EXTRACT, _extract_text()) == (
        [],
        [],
    )

    typo_copy = tmp_path / "draft_typo.py"
    typo_copy.write_text(text.replace("500 meters", "600 meters"), encoding="utf-8")
    corrupted = _citations_from_source(typo_copy, "draft_typo")
    unquotable, missing = _citation_traceability_failures(
        corrupted, osm_module.EXTRACT, _extract_text()
    )
    assert unquotable == []
    assert [name for name, _fragment in missing] == ["CIT_S5331_LANE_CHANGE_DISTANCE"]


@pytest.mark.parametrize(("module", "minimum_constants"), _CITED_MODULES)
def test_citation_locators_name_a_section_or_table_and_a_doc_page(module, minimum_constants):
    """The extract records NINE cross-reference errors where §5's body text says
    "Figure N" and the object is "Table N". So a locator here names a § or a
    Table plus the doc page, and never an in-text figure label. (Lost figures
    are still named — but as *evidence* in a ``not_checkable`` item, never as the
    authority for a rule.)"""
    for name, citation in sorted(_citation_constants(module).items()):
        locator = citation.split(f"({module.EXTRACT})")[0]
        assert re.search(r"doc p\.\d{2,3}\s*$", locator.strip().rstrip(",")), (
            f"{name}: locator does not end in a doc page — {locator!r}"
        )
        assert re.match(r"MRWA OMG v2\.0 (§5(\.\d+)*|Table 5-\d+)\b", locator), name
        assert "Figure" not in locator, f"{name}: locator cites an in-text figure label"


# ---------------------------------------------------------------------------
# 6. The compliance report — three states, and a violation that is NAMED
# ---------------------------------------------------------------------------


def test_the_report_uses_all_three_states(crossroads_draft):
    counts = crossroads_draft.compliance.counts()
    assert counts["applied"] >= 4
    assert counts["violated"] >= 1
    assert counts["not_checkable"] >= 8
    assert sum(counts.values()) == len(crossroads_draft.compliance.items)


def test_every_rule_carries_a_citation_naming_the_extract(crossroads_draft):
    for item in crossroads_draft.compliance.items:
        assert osm_module.EXTRACT in item.citation, item.rule_id
        assert item.doc_page >= 179, item.rule_id  # §5 starts on doc p.179


def test_every_not_checkable_item_gives_a_machine_readable_reason(crossroads_draft):
    for item in crossroads_draft.compliance.not_checkable:
        assert item.reason in {
            draft_module.REASON_FIGURE_LOST,
            draft_module.REASON_DATA_ABSENT_FROM_OSM,
            draft_module.REASON_OUTSIDE_EMITTED_SUBSET,
            draft_module.REASON_REQUIRES_SITE_OBSERVATION,
            draft_module.REASON_REQUIRES_VISSIM,
            draft_module.REASON_NO_GEOREFERENCE,
        }, item.rule_id


def test_rules_that_lived_in_a_lost_figure_are_not_checkable_and_name_the_figure(
    crossroads_draft,
):
    """Figure 5-1 (Network Settings) is the canonical case: only its left-hand
    traffic sentence reached the text layer. Guessing the rest of that dialog
    would be inventing the client's guideline, so those rules are NOT CHECKABLE
    with reason ``figure_lost`` — never rounded up to applied."""
    figure_lost = [
        item for item in crossroads_draft.compliance.items if item.reason == REASON_FIGURE_LOST
    ]
    assert len(figure_lost) >= 3
    assert all(item.state is ComplianceState.NOT_CHECKABLE for item in figure_lost)
    named = " ".join(str(item.evidence) for item in figure_lost)
    assert "Figure 5-1" in named and "Figure 5-2" in named and "Figure 5-11" in named


def test_the_left_hand_traffic_rule_comes_back_applied(crossroads_draft):
    item = _one(
        [i for i in crossroads_draft.compliance.items if i.rule_id == "5.2.1.1-left-hand-traffic"]
    )
    assert item.state is ComplianceState.APPLIED
    assert item.doc_page == 180
    assert item.instances == len(crossroads_draft.links)
    assert item.evidence["two_way_osm_ways_split"] == 2


def test_the_georeference_rule_is_a_named_violation_and_the_draft_is_built_anyway(
    crossroads_draft,
):
    """§5.3.1.1 makes coordinate alignment the modeller's responsibility. This
    draft has no datum at all, so it cannot satisfy the rule — and rather than
    passing silently or refusing to generate, it produces the network and NAMES
    the violation. The consumer decides."""
    item = _one(
        [
            i
            for i in crossroads_draft.compliance.items
            if i.rule_id == "5.3.1.1-coordinate-alignment"
        ]
    )
    assert item.state is ComplianceState.VIOLATED
    assert item.subjects == ["<whole draft>"]
    assert item.reason == draft_module.REASON_NO_GEOREFERENCE
    assert crossroads_draft.links, "the draft is generated anyway"


def test_a_dedicated_turn_lane_in_the_data_produces_a_named_violation(tmp_path: Path):
    """``turn:lanes=left|...`` on way 501 declares a lane dedicated to one
    movement. §5.3.3.3 recommends a separate pocket link for exactly that; this
    generator codes it as a lane of the approach, so the rule must come back
    VIOLATED with way 501 named."""
    draft = build_draft(
        parse_osm_file(_write(tmp_path, "pocket.osm", dedicated_turn_lane_osm())), name="p"
    )
    item = _one(
        [i for i in draft.compliance.items if i.rule_id == "5.3.3.3-dedicated-pocket-lane"]
    )
    assert item.state is ComplianceState.VIOLATED
    assert item.subjects == ["osm way 501"]
    assert item.instances == 1


def test_a_bus_lane_in_the_data_produces_a_named_violation(tmp_path: Path):
    """§5.3.3.4 says a bus lane is coded as a lane of a link using lane
    closures. This emission writes no lane closures, so a way declaring one is a
    named violation — and the report also states that the lane was NOT coded as
    a separate link, which is the mis-coding §5.3.3.4 is actually warning
    against."""
    draft = build_draft(parse_osm_file(_write(tmp_path, "bus.osm", bus_lane_osm())), name="b")
    item = _one([i for i in draft.compliance.items if i.rule_id == "5.3.3.4-bus-and-hv-lane"])
    assert item.state is ComplianceState.VIOLATED
    assert item.subjects == ["osm way 601"]
    assert item.evidence["lane_closures_emitted"] == 0


def test_the_same_two_rules_pass_vacuously_when_the_data_has_no_such_lane(crossroads_draft):
    """The counterfactual for the two data-driven violations: on a fixture with
    neither a dedicated turn lane nor a bus lane they are ``applied`` with zero
    instances, and the report SAYS the pass is vacuous rather than implying the
    generator did something."""
    for rule_id in ("5.3.3.3-dedicated-pocket-lane", "5.3.3.4-bus-and-hv-lane"):
        item = _one([i for i in crossroads_draft.compliance.items if i.rule_id == rule_id])
        assert item.state is ComplianceState.APPLIED
        assert item.instances == 0
        assert "vacuous" in item.detail["en"]


def test_no_lane_count_minimum_is_asserted_because_section_5_states_none(crossroads_draft):
    """Read for this deliberately: §5.3 (doc p.191–197) gives lane WIDTH (Table
    5-7's 3.5 m) but **no minimum lane count** anywhere — not in §5.3.3, not in
    §5.3.3.2/3/4, not in §5.6.1.3's input checklist. So no rule here claims one,
    and this test records the reading so a future unit does not invent a
    threshold and attribute it to Main Roads.

    The violation fixtures above use rules §5 *does* state (§5.3.3.3's pocket
    lane, §5.3.3.4's bus lane, §5.3.1.1's coordinate alignment) instead.
    """
    text = (REPO_ROOT / osm_module.EXTRACT).read_text(encoding="utf-8")
    section_5_3 = text[text.index("## 5.3 Network Data") : text.index("## 5.4 Intersection")]
    assert not re.search(r"minimum (number of )?lane", section_5_3, re.IGNORECASE)

    rendered = " ".join(
        item.rule_id + " " + json.dumps(item.evidence, ensure_ascii=False) + " " + item.detail.get("en", "")
        for item in crossroads_draft.compliance.items
    )
    assert not re.search(r"minimum.{0,20}lane|lane.{0,20}minimum", rendered, re.IGNORECASE)


@pytest.mark.parametrize("language", ["ko", "en", "ja", "xx"])
def test_the_report_renders_in_ko_en_and_ja_with_ko_as_the_fallback(
    crossroads_draft, language
):
    """ko primary, en/ja through the same table, and an unknown code falls back
    to Korean — the contract ``pick_language_text`` sets for the rest of the
    product."""
    rendered = crossroads_draft.render_compliance(language)
    assert "MRWA OMG v2.0" in rendered
    assert osm_module.EXTRACT in rendered
    for rule_id in ("5.2.1.1-left-hand-traffic", "5.3.1.1-coordinate-alignment"):
        item = _one([i for i in crossroads_draft.compliance.items if i.rule_id == rule_id])
        assert item.title(language) in rendered
    if language in ("ko", "xx"):
        assert "좌측통행" in rendered
    elif language == "en":
        assert "left-hand traffic" in rendered
    else:
        assert "左側通行" in rendered


def test_the_report_lists_every_generator_assumption_with_its_basis(crossroads_draft):
    """The numbers §5 does not state. Each says where it came from, and the only
    one with a §5 number behind it carries the citation for it."""
    ids = {assumption.id for assumption in crossroads_draft.assumptions}
    assert {"LANE_WIDTH_M", "INTERSECTION_SETBACK_M", "NODE_HALF_SIZE_M"} <= ids
    lane_width = _one([a for a in crossroads_draft.assumptions if a.id == "LANE_WIDTH_M"])
    assert lane_width.value == LANE_WIDTH_M == 3.5
    assert lane_width.citation and "Table 5-7" in lane_width.citation
    assert all(assumption.basis for assumption in crossroads_draft.assumptions)


def test_the_report_names_every_skipped_way(tmp_path: Path):
    draft = build_draft(
        parse_osm_file(_write(tmp_path, "skips.osm", unsupported_tags_osm())), name="s"
    )
    rendered = draft.render_compliance("en")
    for way_id in (402, 403, 404, 405):
        assert str(way_id) in rendered


# ---------------------------------------------------------------------------
# 7. Emission — round-tripped through THIS repo's parser and U2's differ
#
# The differ is the oracle. Nothing in this section compares two networks by
# hand; every structural claim goes through ``genesis.diff``, which was built
# and proven in its own unit.
# ---------------------------------------------------------------------------


@pytest.fixture()
def emitted(tmp_path: Path, crossroads_draft):
    return emit_inpx(crossroads_draft, tmp_path / "out" / "cross.inpx")


def test_the_emitted_file_parses_through_this_repos_parser(emitted, crossroads_draft):
    """Every link id, lane count and polyline the draft holds must come back out
    of ``inpx_parser.parse_network`` unchanged, and the connectors must come back
    flagged as connectors with their endpoints resolved."""
    network = parse_network(str(emitted))
    parsed = {link["id"]: link for link in network["links"]}
    assert len(parsed) == len(crossroads_draft.links) + len(crossroads_draft.connectors)

    for link in crossroads_draft.links:
        got = parsed[str(link.id)]
        assert got["lanes"] == link.lanes
        assert got["is_connector"] is False
        assert [tuple(point) for point in got["points"]] == [tuple(p) for p in link.points]
        assert got["lane_widths"] == [LANE_WIDTH_M] * link.lanes

    for connector in crossroads_draft.connectors:
        got = parsed[str(connector.id)]
        assert got["is_connector"] is True
        assert got["from_link"] == str(connector.from_link)
        assert got["to_link"] == str(connector.to_link)
        assert got["from_pos"] == pytest.approx(connector.from_pos_m, abs=1e-3)
        assert got["to_pos"] == pytest.approx(0.0)

    assert [node["id"] for node in network["nodes"]] == [
        str(node.id) for node in crossroads_draft.nodes
    ]


def test_load_topology_consumes_the_emitted_file_without_refusing(emitted):
    topology, findings = diff_module.load_topology(emitted)
    assert topology is not None
    assert [finding.code for finding in findings] == []
    assert len(topology.plain_link_ids) == 8
    assert len(topology.connector_ids) == 12


def test_self_diff_of_the_emitted_file_is_empty(emitted):
    """The strongest single statement available offline: the file the emitter
    wrote, read back and compared to itself by U2's differ, shows no change."""
    diff = diff_module.diff_network_files(str(emitted), str(emitted))
    assert not diff.refused
    assert diff.change_count == 0
    assert not diff.has_changes


def test_removing_one_link_diffs_as_exactly_that_removal(tmp_path: Path, crossroads_draft):
    """The mutated-draft oracle. Dropping link 8 also drops the three connectors
    that referenced it — leaving them would be a dangling reference, not a
    smaller network — so "exactly that change" is one removed link plus its
    three movements, and NOTHING else: no lane-count change, no rewire, no
    added object."""
    original = emit_inpx(crossroads_draft, tmp_path / "a.inpx")
    doomed = 8
    orphans = sorted(
        c.id
        for c in crossroads_draft.connectors
        if doomed in (c.from_link, c.to_link)
    )
    mutant = crossroads_draft.model_copy(deep=True)
    mutant.links = [l for l in mutant.links if l.id != doomed]
    mutant.connectors = [c for c in mutant.connectors if c.id not in orphans]
    mutated = emit_inpx(mutant, tmp_path / "b.inpx")

    diff = diff_module.diff_network_files(str(original), str(mutated))
    assert diff.links.removed == [str(doomed)]
    assert diff.links.added == []
    assert diff.links.lane_count_changed == []
    assert diff.connectors.removed == [str(i) for i in orphans]
    assert diff.connectors.added == []
    assert diff.connectors.rewired == []
    assert diff.connectors.lane_count_changed == []
    # 1 link + 3 connectors + THE NODE. The node's movement set is the fifth
    # change, and it only exists because node membership became geometric on
    # 2026-08-06: before that ``_node_link_ids`` read a ``<linkSegment>``
    # element Vissim never writes, every node reported zero members, and this
    # count was 4. Pin moved deliberately — see
    # ``test_node_membership_is_geometric_because_vissim_declares_none``.
    assert diff.change_count == 1 + len(orphans) + 1
    node = _one([n for n in diff.node_movements if n.changed])
    assert [m.connector_id for m in node.removed] == [str(i) for i in orphans]
    assert node.added == []


def test_changing_one_lane_count_diffs_as_exactly_that_change(tmp_path: Path, crossroads_draft):
    """A mutation with no collateral at all: one link goes from 2 lanes to 3, and
    the differ reports exactly one change, on that link, 2 → 3."""
    original = emit_inpx(crossroads_draft, tmp_path / "c.inpx")
    mutant = crossroads_draft.model_copy(deep=True)
    target = _one([l for l in mutant.links if l.id == 1])
    assert target.lanes == 2
    mutant.links = [
        l.model_copy(update={"lanes": 3}) if l.id == 1 else l for l in mutant.links
    ]
    mutated = emit_inpx(mutant, tmp_path / "d.inpx")

    diff = diff_module.diff_network_files(str(original), str(mutated))
    assert diff.change_count == 1
    change = _one(diff.links.lane_count_changed)
    assert (change.link_id, change.base_lanes, change.target_lanes) == ("1", 2, 3)
    assert change.delta == 1


def test_two_generations_from_the_same_osm_are_byte_identical(tmp_path: Path):
    """Deterministic ids, deterministic ordering, one number formatter, and
    nothing time- or host-dependent written anywhere."""
    source = _write(tmp_path, "det.osm", crossroads_osm())
    first = to_xml_text(build_draft(parse_osm_file(source), name="det"))
    second = to_xml_text(build_draft(parse_osm_file(source), name="det"))
    assert first.encode("utf-8") == second.encode("utf-8")

    path_a = emit_inpx(build_draft(parse_osm_file(source), name="det"), tmp_path / "1.inpx")
    path_b = emit_inpx(build_draft(parse_osm_file(source), name="det"), tmp_path / "2.inpx")
    assert path_a.read_bytes() == path_b.read_bytes()


def test_the_emission_stays_inside_the_subset_the_parser_understands(emitted):
    """Nothing is written that could not be read back and checked offline: no
    signal heads, no detectors, no vehicle inputs, no routing, no reduced-speed
    areas. The §5 rules governing those are reported ``not_checkable`` instead of
    approximated.

    Emitter v2 adds FIVE containers to v1's three, and not one of them is a
    modelling choice: every element in them exists because something this file
    writes names its ``no``, transitively. A real network stripped to
    ``netPara`` + ``links`` was measured to load with ``load_ok=True`` and ZERO
    links, and so was v2's first live attempt — an unresolved reference ANYWHERE
    in the chain makes links vanish silently, with no ``.err``.
    """
    root = ET.parse(emitted).getroot()
    assert {child.tag for child in root} == {
        "displayTypes",
        "drivingBehaviors",
        "laneMarkingTypes",
        "levels",
        "linkBehaviorTypes",
        "links",
        "netPara",
        "nodes",
        "timeDistributions",
    }
    # each support container declares exactly the id the links/lanes reference
    assert [el.get("no") for el in root.findall("./levels/level")] == [schema.LEVEL_NO]
    assert [el.get("no") for el in root.findall("./displayTypes/displayType")] == [
        schema.DISPLAY_TYPE_NO
    ]
    assert [
        el.get("no") for el in root.findall("./linkBehaviorTypes/linkBehaviorType")
    ] == [schema.LINK_BEHAV_TYPE_NO]
    assert [
        el.get("no") for el in root.findall("./laneMarkingTypes/laneMarkingType")
    ] == [schema.LANE_MARKING_TYPE_NO]
    assert [el.get("no") for el in root.findall("./drivingBehaviors/drivingBehavior")] == [
        schema.DRIVING_BEHAVIOR_NO
    ]
    for absent in (
        "signalHeads",
        "signalControllers",
        "vehicleInputs",
        "vehicleRoutingDecisionsStatic",
        "dataCollectionMeasurements",
        "reducedSpeedAreas",
        "conflictAreas",
    ):
        assert root.find(f".//{absent}") is None, absent
    elements = parse_network(str(emitted))["elements"]
    assert elements["signal_heads"] == []
    assert elements["vehicle_inputs"] == []


def test_every_id_reference_in_the_file_resolves_inside_the_file(emitted):
    """The invariant the first v2 live run broke, generalised so it cannot break
    silently again.

    That run loaded with ``load_ok=True``, wrote no ``.err``, and held ZERO
    links. Vissim had dropped the whole ``drivingBehaviors`` container because
    the one driving behaviour referenced two time distributions the file never
    declared — which made ``linkBehaviorType no="1"`` dangle, which made every
    link vanish. A dangling reference two hops from a link is still a network
    with no links in it, and Vissim calls that a successful load.

    So: walk EVERY attribute that names another object by ``no`` and assert the
    target exists. The map is explicit rather than derived, because guessing
    which attributes are references is how the first run got here.
    """
    root = ET.parse(emitted).getroot()

    def declared(container: str, tag: str) -> set[str]:
        return {el.get("no") for el in root.findall(f"./{container}/{tag}")}

    levels = declared("levels", "level")
    display_types = declared("displayTypes", "displayType")
    behaviour_types = declared("linkBehaviorTypes", "linkBehaviorType")
    driving_behaviours = declared("drivingBehaviors", "drivingBehavior")
    marking_types = declared("laneMarkingTypes", "laneMarkingType")
    time_distributions = declared("timeDistributions", "timeDistribution")
    link_ids = {el.get("no") for el in root.findall("./links/link")}

    for link in root.findall("./links/link"):
        where = link.get("no")
        assert link.get("displayType") in display_types, where
        assert link.get("linkBehavType") in behaviour_types, where
        if "level" in link.attrib:
            assert link.get("level") in levels, where
        for lane in link.findall("./lanes/lane"):
            assert lane.get("markingType") in marking_types, where
            for ref in lane.findall("./fromLanes/objectRef") + lane.findall(
                "./toLanes/objectRef"
            ):
                assert ref.get("key").split()[0] in link_ids, (where, ref.get("key"))
        for tag in ("fromLinkEndPt", "toLinkEndPt"):
            endpoint = link.find(tag)
            if endpoint is not None:
                assert endpoint.get("link") in link_ids, (where, tag)

    for behaviour_type in root.findall("./linkBehaviorTypes/linkBehaviorType"):
        assert behaviour_type.get("drivBehavDef") in driving_behaviours

    for behaviour in root.findall("./drivingBehaviors/drivingBehavior"):
        # the two that were missing, and the reason this test exists
        assert behaviour.get("w99cc1Distr") in time_distributions
        assert behaviour.get("zipperOvtTmDistr") in time_distributions


def test_no_map_anchor_is_written_anywhere_in_the_file(emitted):
    """The projection origin is recorded in the METADATA and in the provenance
    comment so the transform can be reproduced. It is deliberately NOT written
    as ``refPointMap``: that attribute anchors a network to the map, and this
    file makes no absolute claim. ``northDir="0"`` is kept because it states
    only that +y is north in this file's own frame, which is what the projection
    produces."""
    text = emitted.read_text(encoding="utf-8")
    assert "refPointMap" not in text
    assert "refPointNet" not in text
    assert 'northDir="0"' in text


def test_the_file_says_what_it_has_and_has_not_been_verified_against(emitted):
    """The honesty travels with the artefact, not only with the report."""
    text = emitted.read_text(encoding="utf-8")
    assert "Emitter v2" in text
    assert "inpx_parser" in text and "genesis.diff" in text
    assert "no absolute position is claimed" in text
    assert "Projection origin lat=" in text
    assert "GEOMETRY ONLY" in text
    # the regime the geometry was built for travels with it too
    assert f"Traffic regulation {draft_module.TRAFFIC_REGULATION}" in text
    # the live result is claimed for the SCHEMA and explicitly not for the file
    assert "LoadNet in PTV Vissim Academic 2026" in text
    assert "not a certificate for this particular file" in text


def test_the_provenance_comment_never_breaks_the_xml(tmp_path: Path):
    """Regression: XML forbids ``--`` inside a comment, and the source path once
    supplied one (a temp directory named ``...C--Users-...``). The source is now
    named by file name and sanitised on top of that."""
    weird = tmp_path / "a--b"
    weird.mkdir()
    source = _write(weird, "sec--tion.osm", crossroads_osm())
    draft = build_draft(parse_osm_file(source), name="w")
    path = emit_inpx(draft, tmp_path / "w.inpx")
    root = ET.parse(path).getroot()  # must not raise
    assert root.tag == "network"
    text = path.read_text(encoding="utf-8")
    comment = text[text.index("<!--") : text.index("-->")]
    assert "--" not in comment[4:]
    assert str(weird) not in text, "an absolute host path must not reach the artefact"


def test_emitting_a_refused_or_empty_draft_raises_rather_than_writing_a_plausible_file(
    tmp_path: Path,
):
    """The one place this package raises. A caller holding a refused draft and
    asking for an ``.inpx`` has a bug; writing a believable empty network for it
    would hide that bug inside a file somebody later opens in Vissim."""
    refused = build_draft(parse_osm_file(tmp_path / "missing.osm"), name="r")
    assert refused.refused
    with pytest.raises(EmitRefused):
        to_xml_text(refused)
    empty = draft_module.DraftNetwork(name="e", source_osm="none")
    with pytest.raises(EmitRefused):
        to_xml_text(empty)


def test_node_membership_is_geometric_because_vissim_declares_none(emitted):
    """PIN MOVED 2026-08-06, replacing ``test_node_link_membership_is_a_known_
    and_stated_gap``, which asserted ``node_members["1"] == set()``.

    That pin recorded a real property of the old code and a WRONG belief about
    the format: ``_node_link_ids`` read ``<linkSegment link=…>``, and the live
    check found no such element in ANY Vissim-saved network — PTV's own examples
    included. Vissim never writes one, so the helper returned an empty set for
    every node of every network, real ones as well as generated ones, and "0
    movements per genesis draft" was a parser bug wearing a documented-gap
    label.

    Membership is geometric now — a node IS its polygon — so the crossroads'
    single node declares all twelve of its movements, plus the eight approach
    and exit links whose ends sit inside the 30 m box.
    """
    topology, _findings = diff_module.load_topology(emitted)
    assert set(topology.node_members) == {"1"}
    assert topology.node_members["1"] == {str(i) for i in range(1, 9)} | {
        str(i) for i in range(10001, 10013)
    }
    # ``movements`` filters to connectors, so the plain links are members
    # without being movements
    assert [m["id"] for m in topology.movements("1")] == [
        str(i) for i in range(10001, 10013)
    ]
    assert topology.summary().movement_count == 12


# ---------------------------------------------------------------------------
# 7b. Emitter v2 — the emitted XML against the schema PTV Vissim SAVES
#
# The fixtures below are verbatim excerpts of networks Vissim itself wrote,
# captured by ``backend/tools/genesis_open_live_check.py`` on PTV Vissim
# Academic 2026 SP 09 and recorded in
# ``runtime/genesis-open-live-check/state.json``. They are quoted here rather
# than read from ``runtime/`` (which is git-ignored, so a clean checkout has
# none of it) — the evidence has to travel with the test, not with the machine.
# ---------------------------------------------------------------------------

#: Verbatim from ``rt_generated/rt_generated_geometry_saved.inpx`` — Vissim's
#: OWN save of this generator's crossroads geometry. Attributes only; the
#: children are asserted separately.
VISSIM_SAVED_LINK = (
    '<link alc_v_limit="130" assumSpeedOncom="60" consVehInDynPot="false" costPerKm="0"'
    ' desSpeedFact="1" direction="ALL" displayType="5" emergStopDist="5" emiCalcAct="true"'
    ' gradient="0" hasOvtLn="false" isPedArea="false" level="2" linkBehavType="1"'
    ' linkEvalAct="true" linkEvalSegLen="10" lnChgDist="200" lnChgDistIsPerLn="false"'
    ' lnChgEvalAct="true" lookAheadDistOvt="250" mesoFollowUpGap="0" mesoSpeed="50"'
    ' mesoSpeedModel="VEHICLEBASED" name="Main St [FWD] w101s0" netPerfEvalAct="true" no="1"'
    ' ovtOnlyPT="false" ovtSpeedFact="1.3" rvsPark="false" showClsfValues="true"'
    ' showLinkBar="true" showVeh="true" surch1="0" surch2="0" thickness="0" vehDynPotG="3"'
    ' vehRecAct="true"/>'
)

#: Verbatim from the same file — a CONNECTOR, i.e. the same element WITH
#: endpoints and WITHOUT ``level``. Its ``<lanes>`` subtree is quoted in full
#: because the endpoint/lane shape is the thing v1 got wrong.
VISSIM_SAVED_CONNECTOR = (
    '<link alc_v_limit="130" assumSpeedOncom="60" consVehInDynPot="false" costPerKm="0"'
    ' desSpeedFact="1" direction="ALL" displayType="5" emergStopDist="5" emiCalcAct="true"'
    ' gradient="0" hasOvtLn="false" isPedArea="false" linkBehavType="1" linkEvalAct="true"'
    ' linkEvalSegLen="10" lnChgDist="200" lnChgDistIsPerLn="false" lnChgEvalAct="true"'
    ' lookAheadDistOvt="250" mesoFollowUpGap="0" mesoSpeed="50" mesoSpeedModel="VEHICLEBASED"'
    ' name="Main St [FWD] w101s0 -&gt; Main St [FWD] w101s1 (through)" netPerfEvalAct="true"'
    ' no="10001" ovtOnlyPT="false" ovtSpeedFact="1.3" rvsPark="false" showClsfValues="true"'
    ' showLinkBar="true" showVeh="true" surch1="0" surch2="0" thickness="0" vehDynPotG="3"'
    ' vehRecAct="true">'
    '<fromLinkEndPt link="1" pos="271.264"/>'
    '<geometry><linkPolyPts>'
    '<linkPolyPoint radiusEffect="BRAKE" radiusSrc="CALCULATED" x="-12" y="3.5" zOffset="0"/>'
    '<linkPolyPoint radiusEffect="BRAKE" radiusSrc="CALCULATED" x="12" y="3.5" zOffset="0"/>'
    "</linkPolyPts></geometry>"
    "<lanes>"
    '<lane markingType="1">'
    '<fromLanes><objectRef key="1 1"/></fromLanes>'
    '<toLanes><objectRef key="3 1"/></toLanes>'
    "</lane>"
    '<lane markingType="1">'
    '<fromLanes><objectRef key="1 2"/></fromLanes>'
    '<toLanes><objectRef key="3 2"/></toLanes>'
    "</lane>"
    "</lanes>"
    '<toLinkEndPt link="3" pos="0"/>'
    "</link>"
)

#: Verbatim from ``ref_nodes/3 Roundabouts - Conflict areas_saved.inpx``.
VISSIM_SAVED_NODE = (
    '<node allowRecr="false" anmConnNodeNo="0" anmConnZoneNo="0" anmZoneNo="0"'
    ' hasUserDefOrient="false" mesoPenalMerg="0" name="" no="1" showClsfValues="true"'
    ' showTurnValVisual="false" turnValVisualSize="70" useForDynAssign="true"'
    ' useForEval="true" useForMeso="false">'
    '<geometry><points><point x="-307.521" y="84.051"/></points></geometry>'
    "</node>"
)

#: Verbatim from ``ref_lht/Roundabout London_saved.inpx`` — the only left-hand
#: example Vissim ships, and the source of every ``netPara`` value the emitter
#: writes.
VISSIM_SAVED_NETPARA_ATTRS = {
    "concatMaxLen": "255",
    "concatSeparator": ",",
    "drivSimActive": "false",
    "linkGradBas": "ZCOORDINATES",
    "northDir": "0",
    "odrFilePath": "",
    "pedDefCellSize": "0.15",
    "pedDefObstDist": "0.5",
    "pedDirChgAngle": "4",
    "pedDirChgClip": "true",
    "pedNbhSearGridSize": "5",
    "pedQueueOrder": "0.7",
    "pedQueueStraight": "0.6",
    "powWghtRatioMax": "30",
    "powWghtRatioMin": "7",
    "radExper": "2",
    "sigHead3DArrowColorRedAmb": "COLOREDARROWONBLACK",
    "speedLimCurves": "true",
    "trafReg": "LEFTHANDTRAFFIC",
    "underScenMngm": "false",
    "unitAccel": "METERSPERSQUARESECOND",
    "unitJerk": "METERSPERCUBICSECOND",
    "unitLenLong": "KILOMETERS",
    "unitLenShort": "METERS",
    "unitLenVeryShort": "MILLIMETERS",
    "unitSpeed": "KILOMETERSPERHOUR",
    "unitSpeedSmall": "METERSPERSECOND",
}


def _emitted_split(emitted):
    root = ET.parse(emitted).getroot()
    links = root.findall("./links/link")
    return (
        root,
        [el for el in links if el.find("fromLinkEndPt") is None],
        [el for el in links if el.find("fromLinkEndPt") is not None],
    )


def test_the_root_element_is_the_one_vissim_writes(emitted):
    """v1 wrote ``version="2400"``, copied from ``data/practice.inpx`` — a
    hand-written exemplar whose version is not a Vissim schema version at all.
    Every Vissim-saved network measured says 1403."""
    root = ET.parse(emitted).getroot()
    assert root.tag == "network"
    assert root.get("version") == "1403"
    assert root.get("vissimVersion") == "2026.00-09 [307071]"


def test_every_emitted_link_carries_exactly_the_attributes_vissim_writes(emitted):
    """Attribute-SET equality against a link Vissim saved — not "it parses".

    A real network with its links reduced to ``no``/``name`` (which is all v1
    wrote) is refused by ``LoadNet`` with the same error the generated files
    got, so a missing attribute here is a file that will not open.
    """
    reference = set(ET.fromstring(VISSIM_SAVED_LINK).attrib)
    _root, plain, connectors = _emitted_split(emitted)
    assert plain and connectors
    for element in plain:
        assert set(element.attrib) == reference, element.get("no")
    # a connector is the same element MINUS ``level`` — measured on every
    # element with endpoints in all three saved references
    assert reference - {"level"} == set(ET.fromstring(VISSIM_SAVED_CONNECTOR).attrib)
    for element in connectors:
        assert set(element.attrib) == reference - {"level"}, element.get("no")
        assert "level" not in element.attrib


def test_link_attribute_values_are_the_reference_defaults_not_invented_ones(emitted):
    """Every value the generator does not compute equals the saved reference's,
    character for character."""
    reference = ET.fromstring(VISSIM_SAVED_LINK).attrib
    _root, plain, connectors = _emitted_split(emitted)
    computed = {"no", "name", "displayType", "level", "linkBehavType"}
    for element in plain + connectors:
        for key, value in reference.items():
            if key in computed:
                continue
            assert element.get(key) == value, (element.get("no"), key)


def test_lane_elements_match_the_saved_shape_for_links_and_for_connectors(emitted):
    """A link lane carries ``markingType`` + ``width``; a CONNECTOR lane carries
    ``markingType`` only and the from/to lane references. Vissim writes no width
    on a connector lane — it inherits the lanes it joins — and the parser's own
    3.5 m fallback is this generator's width, so nothing is lost."""
    saved_connector = ET.fromstring(VISSIM_SAVED_CONNECTOR)
    _root, plain, connectors = _emitted_split(emitted)

    for element in plain:
        for lane in element.findall("./lanes/lane"):
            assert set(lane.attrib) == {"markingType", "width"}
            assert lane.get("markingType") == "1"
            assert list(lane) == []

    reference_lane = saved_connector.find("./lanes/lane")
    for element in connectors:
        for lane in element.findall("./lanes/lane"):
            assert set(lane.attrib) == set(reference_lane.attrib) == {"markingType"}
            assert [child.tag for child in lane] == [
                child.tag for child in reference_lane
            ] == ["fromLanes", "toLanes"]
            assert [el.tag for el in lane.find("fromLanes")] == ["objectRef"]
            assert set(lane.find("fromLanes/objectRef").attrib) == {"key"}


def test_connector_endpoints_use_the_link_attribute_and_carry_no_lane_token(emitted):
    """The shape v1 got wrong. Vissim writes ``<fromLinkEndPt link="32" pos=…/>``
    — attribute ``link``, no lane — and puts the ``"<link> <lane>"`` token in
    ``<objectRef key>`` under each connector lane instead."""
    saved = ET.fromstring(VISSIM_SAVED_CONNECTOR)
    assert set(saved.find("fromLinkEndPt").attrib) == {"link", "pos"}
    _root, _plain, connectors = _emitted_split(emitted)
    for element in connectors:
        for tag in ("fromLinkEndPt", "toLinkEndPt"):
            endpoint = element.find(tag)
            assert set(endpoint.attrib) == {"link", "pos"}, (element.get("no"), tag)
            assert "lane" not in endpoint.attrib
        assert [child.tag for child in element] == [
            child.tag for child in saved
        ] == ["fromLinkEndPt", "geometry", "lanes", "toLinkEndPt"]


def test_geometry_points_match_the_saved_shape_on_links_and_on_nodes(emitted):
    """A link polyline point carries ``radiusEffect``/``radiusSrc``/``zOffset``
    besides x and y; a NODE polygon point carries x and y and nothing else. v1
    wrote ``zOffset`` on node points too — no Vissim-saved node point has one."""
    saved_point = ET.fromstring(VISSIM_SAVED_CONNECTOR).find(
        "./geometry/linkPolyPts/linkPolyPoint"
    )
    saved_node_point = ET.fromstring(VISSIM_SAVED_NODE).find("./geometry/points/point")
    root = ET.parse(emitted).getroot()

    for point in root.findall("./links/link/geometry/linkPolyPts/linkPolyPoint"):
        assert set(point.attrib) == set(saved_point.attrib)
        assert point.get("radiusEffect") == "BRAKE"
        assert point.get("radiusSrc") == "CALCULATED"
    for point in root.findall("./nodes/node/geometry/points/point"):
        assert set(point.attrib) == set(saved_node_point.attrib) == {"x", "y"}


def test_node_elements_carry_exactly_the_attributes_vissim_writes(emitted):
    reference = set(ET.fromstring(VISSIM_SAVED_NODE).attrib)
    root = ET.parse(emitted).getroot()
    nodes = root.findall("./nodes/node")
    assert nodes
    for node in nodes:
        assert set(node.attrib) == reference
        assert [child.tag for child in node] == ["geometry"]


def test_netpara_is_the_saved_left_hand_one_with_the_drafts_own_regulation(emitted):
    """``trafReg`` is the measured landmine: WITHOUT it a network built with
    left-hand geometry opens as RIGHT-hand traffic, because the attribute
    decides the regime and the geometry does not. It comes from the draft, so
    the file cannot disagree with the offsets."""
    root = ET.parse(emitted).getroot()
    netpara = root.find("./netPara")
    assert netpara.attrib == VISSIM_SAVED_NETPARA_ATTRS
    assert netpara.get("trafReg") == "LEFTHANDTRAFFIC"
    assert list(netpara) == []  # no refPoint* anchor, and no dangling defRefs


def test_the_regulation_written_is_the_drafts_own_not_a_constant_in_the_emitter(
    crossroads_draft, tmp_path: Path
):
    """A counterfactual: change the draft's declared regime and the file follows.
    If the emitter hard-coded it, this would still say LEFTHANDTRAFFIC."""
    mutant = crossroads_draft.model_copy(deep=True)
    mutant.traffic_regulation = "RIGHTHANDTRAFFIC"
    root = ET.parse(emit_inpx(mutant, tmp_path / "rht.inpx")).getroot()
    assert root.find("./netPara").get("trafReg") == "RIGHTHANDTRAFFIC"


# ---------------------------------------------------------------------------
# 7c. Connector lane indices — hand-derived, from a measured convention
# ---------------------------------------------------------------------------


def test_lane_index_one_is_the_rightmost_lane_and_the_code_says_so():
    """The measurement this whole section rests on: the live check compared each
    connector-lane endpoint's signed lateral offset from its host link's
    centreline on two Vissim-SAVED networks — 7 links to 0 on the left-hand
    London roundabout, 11 to 0 on the right-hand Buehl one, lane 1 at −1.75 m
    (half a lane RIGHT of centre). Index 1 is the right-most lane in both
    driving regimes, so under left-hand traffic it is the MEDIAN side."""
    assert draft_module.LANE_INDEX_1_IS_RIGHTMOST is True
    assert draft_module.TRAFFIC_REGULATION == "LEFTHANDTRAFFIC"


@pytest.mark.parametrize(
    ("turn", "approach_lanes", "exit_lanes", "expected"),
    [
        # Hand derivation, left-hand traffic. Approach lane 1 = right-most =
        # MEDIAN side; the highest index = left-most = KERB side.
        #
        # 2-lane approach into a 1-lane exit, k = 1:
        #   a near-side (left) turn leaves from the kerb lane ................ 2
        #   an across-traffic (right) turn leaves from the median lane ....... 1
        #   a through movement aligns on the median ........................... 1
        (draft_module.TURN_LEFT, 2, 1, [(2, 1)]),
        (draft_module.TURN_RIGHT, 2, 1, [(1, 1)]),
        (draft_module.TURN_THROUGH, 2, 1, [(1, 1)]),
        # 3-lane approach into a 2-lane exit, k = 2. Connector lane 1 is the
        # RIGHT-most of the connector, so it takes the right-most of the
        # selected block on both sides.
        (draft_module.TURN_LEFT, 3, 2, [(2, 1), (3, 2)]),
        (draft_module.TURN_RIGHT, 3, 2, [(1, 1), (2, 2)]),
        (draft_module.TURN_THROUGH, 3, 2, [(1, 1), (2, 2)]),
        # 1-lane approach into a 3-lane exit, k = 1: a left turn joins the
        # exit's kerb-most lane, a right turn its median-most.
        (draft_module.TURN_LEFT, 1, 3, [(1, 3)]),
        (draft_module.TURN_RIGHT, 1, 3, [(1, 1)]),
        # equal counts: every lane is used, and the blocks coincide
        (draft_module.TURN_LEFT, 2, 2, [(1, 1), (2, 2)]),
        (draft_module.TURN_THROUGH, 2, 2, [(1, 1), (2, 2)]),
    ],
)
def test_connector_lane_pairs_hand_derivation(turn, approach_lanes, exit_lanes, expected):
    assert draft_module.connector_lane_pairs(turn, approach_lanes, exit_lanes) == expected


def test_every_connector_lane_pairing_is_a_bijection_of_contiguous_blocks(
    crossroads_draft,
):
    """Structural invariants, on the drafted network rather than in the
    abstract: one pair per connector lane, indices inside their link's range,
    no lane used twice, and both blocks contiguous."""
    by_id = {link.id: link for link in crossroads_draft.links}
    for connector in crossroads_draft.connectors:
        assert len(connector.from_lanes) == len(connector.to_lanes) == connector.lanes
        assert len(set(connector.from_lanes)) == len(connector.from_lanes)
        assert len(set(connector.to_lanes)) == len(connector.to_lanes)
        for index in connector.from_lanes:
            assert 1 <= index <= by_id[connector.from_link].lanes
        for index in connector.to_lanes:
            assert 1 <= index <= by_id[connector.to_link].lanes
        assert connector.from_lanes == sorted(connector.from_lanes)
        assert connector.to_lanes == sorted(connector.to_lanes)
        assert connector.from_lanes[-1] - connector.from_lanes[0] == connector.lanes - 1


def test_the_crossroads_left_turns_leave_from_the_kerb_lane_not_lane_one(
    crossroads_draft, emitted
):
    """The v1 bug, in the file. The crossroads' two-lane approaches feed
    one-lane side roads, so every left turn is a single connector lane — v1
    wrote ``lane="<link> 1"`` for it, putting the near-side turn on the median
    lane. It must now leave from lane 2, and the ``objectRef`` in the emitted
    XML must say so."""
    by_id = {link.id: link for link in crossroads_draft.links}
    lefts = [c for c in crossroads_draft.connectors if c.turn == draft_module.TURN_LEFT]
    assert lefts
    checked = 0
    for connector in lefts:
        approach = by_id[connector.from_link]
        if approach.lanes < 2:
            continue
        checked += 1
        assert connector.from_lanes == [approach.lanes]
        assert connector.from_lanes != [1]
    assert checked, "the fixture must contain a multi-lane left turn to be a test"

    keys = {
        el.get("no"): [
            ref.get("key")
            for ref in el.findall("./lanes/lane/fromLanes/objectRef")
        ]
        for el in ET.parse(emitted).getroot().findall("./links/link")
    }
    for connector in lefts:
        approach = by_id[connector.from_link]
        if approach.lanes < 2:
            continue
        assert keys[str(connector.id)] == [f"{connector.from_link} {approach.lanes}"]


def test_right_turns_and_through_movements_stay_on_the_median_side(crossroads_draft):
    for connector in crossroads_draft.connectors:
        if connector.turn in (draft_module.TURN_RIGHT, draft_module.TURN_THROUGH):
            assert connector.from_lanes[0] == 1
            assert connector.to_lanes[0] == 1


def test_the_emitted_objectrefs_agree_with_the_draft_lane_mapping(
    emitted, crossroads_draft
):
    """The emission is not allowed to re-derive the mapping — it must write the
    draft's. One ``<lane>`` per connector lane, one ``objectRef`` per side."""
    elements = {
        el.get("no"): el for el in ET.parse(emitted).getroot().findall("./links/link")
    }
    for connector in crossroads_draft.connectors:
        lanes = elements[str(connector.id)].findall("./lanes/lane")
        assert len(lanes) == connector.lanes
        assert [
            lane.find("fromLanes/objectRef").get("key") for lane in lanes
        ] == [f"{connector.from_link} {index}" for index in connector.from_lanes]
        assert [
            lane.find("toLanes/objectRef").get("key") for lane in lanes
        ] == [f"{connector.to_link} {index}" for index in connector.to_lanes]


# ---------------------------------------------------------------------------
# 8. The CLI
# ---------------------------------------------------------------------------


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT / "backend"),
    )


def test_cli_writes_three_files_into_the_caller_named_directory(tmp_path: Path):
    """``.osm`` in, ``.inpx`` + compliance report + structured JSON out — into
    ``--out-dir`` and nowhere else."""
    source = _write(tmp_path, "cli.osm", crossroads_osm())
    out = tmp_path / "deliverable"
    result = _run_cli("--osm", str(source), "--out-dir", str(out), "--lang", "en")
    assert result.returncode == 0, result.stderr

    assert sorted(p.name for p in out.iterdir()) == [
        "cli.compliance.md",
        "cli.genesis.json",
        "cli.inpx",
    ]
    network = parse_network(str(out / "cli.inpx"))
    assert len(network["links"]) == 20  # 8 links + 12 connectors
    payload = json.loads((out / "cli.genesis.json").read_text(encoding="utf-8"))
    assert payload["metadata"]["projection"]["georeferenced"] is False
    assert len(payload["links"]) == 8
    assert payload["compliance"]["items"], "the report travels as data too"
    assert "NOT CHECKABLE" in (out / "cli.compliance.md").read_text(encoding="utf-8")


def test_cli_writes_nothing_outside_the_out_dir(tmp_path: Path):
    """No ``data/``, no ``runtime/``, no workspace: the only new files are the
    three in ``--out-dir``."""
    source = _write(tmp_path, "scope.osm", crossroads_osm())
    out = tmp_path / "only-here"
    before = {p for p in tmp_path.rglob("*")}
    assert _run_cli("--osm", str(source), "--out-dir", str(out)).returncode == 0
    created = {p for p in tmp_path.rglob("*")} - before
    assert all(out in p.parents or p == out for p in created), sorted(map(str, created))


def test_cli_refuses_a_missing_file_with_exit_2(tmp_path: Path):
    result = _run_cli("--osm", str(tmp_path / "nope.osm"), "--out-dir", str(tmp_path / "o"))
    assert result.returncode == 2
    assert "osm_file_missing" in result.stderr
    assert not (tmp_path / "o").exists(), "a refusal must not create the output directory"


def test_cli_refuses_an_osm_with_nothing_drivable_with_exit_2(tmp_path: Path):
    source = _write(
        tmp_path, "ped.osm", _osm_text(_CROSS_NODES, [(1, [1, 2], {"highway": "footway"})])
    )
    result = _run_cli("--osm", str(source), "--out-dir", str(tmp_path / "o2"))
    assert result.returncode == 2
    assert "osm_no_drivable_ways" in result.stderr


def test_cli_refuses_an_area_too_large_for_the_projection(tmp_path: Path):
    source = _write(
        tmp_path,
        "wide.osm",
        _osm_text(
            [(1, LAT0 - 0.05, LON0 - 0.05), (2, LAT0 + 0.05, LON0 + 0.05)],
            [(1, [1, 2], {"highway": "primary"})],
        ),
    )
    result = _run_cli("--osm", str(source), "--out-dir", str(tmp_path / "o3"), "--lang", "en")
    assert result.returncode == 2
    assert "projection_out_of_range" in result.stderr
    assert "validity radius" in result.stderr

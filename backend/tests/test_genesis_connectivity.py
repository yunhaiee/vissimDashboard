"""Tests for the connectivity layer of roadmap 2.3 Genesis (a).

Covers ``services/genesis/connectivity.py``, the connectivity half of
``services/genesis/{osm,draft}.py``, and
``backend/tools/genesis_connectivity_probe.py``.

The failure this unit exists to prevent is **a network that looks right and
strands vehicles**. A link whose end no connector leaves is a place where
traffic silently departs the model: Vissim writes no error, no warning and no
``.err``, the demand simply evaporates, and every number downstream is wrong by
an amount nothing measures. It is also invisible in a rendering, because the
~12 m intersection setback makes every *correct* junction look like a gap too.
So it is counted, and the counts here are derived by hand.

Same discipline as ``test_genesis_osm_draft.py``: every expected number is
derived in the test's own docstring from the fixture's coordinates, and the
fixtures are synthetic — no client geometry, no client-derived constant, appears
anywhere in this file.

Seven families:

1. **Cul-de-sacs** (§1) — a two-way street that dead-ends must gain a U-turn, and
   the same street at the model boundary must NOT. A dead end is not always a
   terminal nobody else touches: where several ways all END at one point and
   every one is one-way inbound, the node is shared and is still a trap.
2. **Node merging** (§2) — a way's stray end merges into a junction the file
   DECLARES; two roads that merely end near each other never do, however close,
   because that would invent a junction rather than reconcile one. The
   counterfactuals are the identical geometry with a ``layer`` disagreement
   (which must stay two, pairwise AND through a mixed-grade intermediate node)
   and the identical distance with no declared junction (which must stay two).
2b. **The last-resort U-turn and the dangling-end census** (§2b) — the seventh
   rule fires only where every movement is antiparallel, never at the model
   boundary, and the GENERATOR names every link end no connector leaves.
3. **Turn restrictions** (§3) — an OSM movement ban removes exactly that
   movement; "enforced" counts bans that actually removed one and not relations
   that merely parsed; and a restriction this generator cannot apply is named
   by its own reason code, not dropped.
4. **The service-road policy** (§4) — stated, and asserted in both directions.
5. **Determinism and the emitter contract** (§5) — byte-identical output, and
   the new connectors round-trip through this repo's own parser.
6. **The probe** (§6) — the measurement tool reports the generator's own census
   rather than a second classifier's guess, its JSON is UTF-8 wherever it runs,
   and it notices a connector that goes missing.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
from collections import deque
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from services.genesis import connectivity as conn
from services.genesis import diff as diff_module
from services.genesis import draft as draft_module
from services.genesis import osm as osm_module
from services.genesis.draft import (
    _segment_skip,
    LANE_WIDTH_M,
    TURN_LEFT,
    TURN_U,
    UNBUILDABLE_U_TURN_NO_LINK_PAIR,
    build_draft,
    connector_lane_pairs,
)
from services.genesis.emit import emit_inpx, to_xml_text
from services.genesis.osm import (
    RESTRICTION_VIA_NOT_A_NODE,
    SKIP_ISOLATED_ZONE,
    SKIP_SERVICE_EXCLUDED_BY_POLICY,
    SKIP_ZERO_LENGTH,
    parse_osm_file,
)
from services.genesis.project import origin_for_bounds
from services.inpx_parser import parse_network

REPO_ROOT = Path(__file__).resolve().parents[2]
PROBE = REPO_ROOT / "backend" / "tools" / "genesis_connectivity_probe.py"

# ---------------------------------------------------------------------------
# 0. Fixtures
#
# Centred on Perth (MRWA's jurisdiction) like the sibling suite, so the metre
# conversions are the ones already derived there:
#     1° of longitude at this latitude = 94 406 m  →  1 m = 1.0593e-5°
#     1° of latitude  at this latitude = 110 887 m →  1 m = 9.0182e-6°
# ---------------------------------------------------------------------------

LAT0 = -32.0700
LON0 = 115.8360

#: 0.00004° of longitude here = 0.00004 × 94 406 = **3.78 m**, comfortably under
#: the 7.0 m node-merge tolerance. Used for the "two ways that touch but share no
#: node" fixture so the expected outcome is checkable with a calculator.
NEAR_TOUCH_LON = 0.00004
NEAR_TOUCH_M = 3.78

#: 0.0001° of longitude here = 9.44 m — OVER the tolerance, so the same fixture
#: with this offset must NOT merge.
FAR_TOUCH_LON = 0.0001
FAR_TOUCH_M = 9.44


def _osm_text(nodes, ways, *, bounds=None, relations=()) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<osm version="0.6" generator="prism-test">',
    ]
    if bounds is not None:
        parts.append(
            '  <bounds minlat="{0}" minlon="{1}" maxlat="{2}" maxlon="{3}"/>'.format(*bounds)
        )
    for node_id, lat, lon in nodes:
        # 10 decimals, not the sibling suite's 6: one of the fixtures below has
        # to place a node at EXACTLY the merge tolerance, and 1e-7° of longitude
        # here is 9.4 mm — enough rounding to flip a strict comparison.
        parts.append(f'  <node id="{node_id}" lat="{lat:.10f}" lon="{lon:.10f}"/>')
    for way_id, refs, tags in ways:
        parts.append(f'  <way id="{way_id}">')
        parts.extend(f'    <nd ref="{ref}"/>' for ref in refs)
        parts.extend(f'    <tag k="{k}" v="{v}"/>' for k, v in tags.items())
        parts.append("  </way>")
    for rel_id, members, tags in relations:
        parts.append(f'  <relation id="{rel_id}">')
        parts.extend(
            f'    <member type="{t}" ref="{r}" role="{role}"/>' for t, r, role in members
        )
        parts.extend(f'    <tag k="{k}" v="{v}"/>' for k, v in tags.items())
        parts.append("  </relation>")
    parts.append("</osm>")
    return "\n".join(parts) + "\n"


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


#: 1 west, 2 centre, 3 east, 4 south, 5 north — the same frame the sibling suite
#: uses, so the four outer nodes define the extent rectangle and are therefore
#: all ON the model boundary.
_FRAME = [
    (1, LAT0, LON0 - 0.003),
    (2, LAT0, LON0),
    (3, LAT0, LON0 + 0.003),
    (4, LAT0 - 0.002, LON0),
    (5, LAT0 + 0.002, LON0),
]


def cul_de_sac_osm(*, highway: str = "residential", oneway: bool = False) -> str:
    """A crossroads plus a two-way stub ending at node 6, strictly INSIDE the model.

    Node 6 sits at (LAT0 + 0.001, LON0 + 0.001) = **(94.4 m east, 110.9 m north)**
    of the origin. The extent rectangle is set by the four frame nodes at
    x = ±283.3 m and y = ±221.8 m, so node 6 is 188.9 m from the nearest east/west
    edge and 110.9 m from the nearest north/south edge — nowhere near the 1.0 m
    boundary band. It is referenced by way 103 alone, so it is a genuine dead end.

    Way 103 is two-way, so it becomes a FWD link (2→6) and a REV link (6→2)
    3.5 m apart, one lane each. Without a U-turn connector a vehicle that drives
    up this street can never drive back down it.
    """
    tags = {"highway": highway, "name": "Dead End Rd"}
    if oneway:
        tags["oneway"] = "yes"
    return _osm_text(
        _FRAME + [(6, LAT0 + 0.001, LON0 + 0.001)],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St", "lanes": "4"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (103, [2, 6], tags),
        ],
    )


def near_touch_osm(*, lon_offset: float = NEAR_TOUCH_LON, second_way_tags=None) -> str:
    """A way whose end fails to attach to a junction the file DOES declare.

    Way 201 runs west→node 2 (at the origin) and way 203 (Cross St) runs
    south→node 2→north, so node 2 is referenced by two ways: **the file itself
    says there is a junction there**. Way 202 starts at node 7, ``lon_offset``
    east of node 2, and runs on east to node 3. With ``lon_offset`` = 0.00004°
    the two are 3.78 m apart: under the 7.0 m tolerance, so node 7 is that same
    junction mapped a second time. With 0.0001° they are 9.44 m apart: over it,
    so they are two.

    Cross St runs THROUGH node 2 deliberately, and that is the fixture's load-
    bearing detail rather than scenery. A merge may only reconcile a junction
    the source declares — see ``REFUSE_NO_JUNCTION_TO_RECONCILE`` and
    ``test_two_lone_road_ends_are_never_welded_into_an_invented_junction``. If
    node 2 were a lone way terminal like node 7, this fixture would be two roads
    that merely end near each other, and joining them would be an invention.
    """
    return _osm_text(
        _FRAME + [(7, LAT0, LON0 + lon_offset)],
        [
            (201, [1, 2], {"highway": "secondary", "name": "West Arm"}),
            (202, [7, 3], {"highway": "secondary", "name": "East Arm", **(second_way_tags or {})}),
            (203, [4, 2, 5], {"highway": "residential", "name": "Cross St"}),
        ],
    )


def two_lone_dead_ends_osm(*, lon_offset: float = NEAR_TOUCH_LON) -> str:
    """Two cul-de-sacs whose heads end ``lon_offset`` apart and share nothing.

    Court A (way 201) branches off the crossroads at node 2 and ends at node 20.
    Court B (way 202) branches off node 5 — the north end of First Ave — and
    ends at node 21, 3.78 m east of node 20. The two share no node and no way,
    and **neither node 20 nor node 21 is a junction the file declares**: each is
    the sole terminal of one way.

    This is what two dead-end streets that back onto each other look like in
    OSM, and in plan it is indistinguishable from the seam ``near_touch_osm``
    describes. That is the whole point: the two need opposite treatments, so the
    decision has to be made on the topology — does the file declare a junction
    here? — and never on the distance, which is identical in both.

    Node 20 sits at (LAT0 + 0.001, LON0 + 0.001) = (94.4 m east, 110.9 m north).
    The extent rectangle is ±283.3 m by ±221.8 m, so both heads are far inside
    the model and neither is a boundary terminal.
    """
    return _osm_text(
        _FRAME + [
            (20, LAT0 + 0.001, LON0 + 0.001),
            (21, LAT0 + 0.001, LON0 + 0.001 + lon_offset),
        ],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (201, [2, 20], {"highway": "residential", "name": "Court A"}),
            (202, [5, 21], {"highway": "residential", "name": "Court B"}),
        ],
    )


#: 5.0 m of longitude here is 5.0 / 94 406 = 5.296e-5°. Three nodes one step
#: apart in a row are 5.0 m / 5.0 m / 10.0 m apart — the outer pair OVER the
#: 7.0 m tolerance (so it is never a candidate pair and is never compared) and
#: the whole row UNDER the 14.0 m cluster cap (so the diameter refusal cannot
#: fire and take the credit).
GRADE_STEP_LON = 5.0 / 94406.0


def transitive_grade_osm() -> str:
    """A ground node, a ground/bridge transition node and a bridge node, in a row.

    * node 30 — ground only, a junction the file declares (ways 601 and 602).
    * node 31 — the transition: way 603 is at grade and way 604 is
      ``layer=1``/``bridge=yes``, both referencing it. A real interchange has
      exactly this node, and its vertical signature is legitimately BOTH.
    * node 32 — the elevated way 605's terminal, ``layer=1``/``bridge=yes``.

    30↔31 is 5.0 m and 31↔32 is 5.0 m, so both are candidate pairs; 30↔32 is
    10.0 m, over the tolerance, so those two are **never compared to each
    other**. Node 31's mixed signature intersects either grade, so a pairwise
    check waves both merges through and the local ground street ends up sharing
    a node with the freeway deck without any pair ever disagreeing.
    """
    return _osm_text(
        _FRAME + [
            (30, LAT0 + 0.001, LON0 - GRADE_STEP_LON),
            (31, LAT0 + 0.001, LON0),
            (32, LAT0 + 0.001, LON0 + GRADE_STEP_LON),
            (33, LAT0 + 0.0018, LON0 + 0.001),
        ],
        [
            (601, [1, 30], {"highway": "residential", "name": "Frontage Rd"}),
            (602, [30, 4], {"highway": "residential", "name": "Local St"}),
            (603, [31, 5], {"highway": "secondary", "name": "Interchange Approach"}),
            (
                604,
                [31, 3],
                {
                    "highway": "motorway",
                    "name": "Elevated Fwy",
                    "layer": "1",
                    "bridge": "yes",
                },
            ),
            (
                605,
                [32, 33],
                {
                    "highway": "motorway",
                    "name": "Fwy Deck",
                    "layer": "1",
                    "bridge": "yes",
                },
            ),
        ],
    )


def one_way_trap_osm() -> str:
    """Two one-way ways that both END at node 40, so nothing leaves it.

    Node 40 is at (LAT0 + 0.001, LON0 + 0.001) — 94.4 m east, 110.9 m north,
    far inside the model. It is referenced TWICE (ways 701 and 702), so it is a
    junction as far as occurrence-counting goes and ``dead_ends()`` used to skip
    it outright. Both ways are one-way *inbound*, so a vehicle that arrives on
    either can neither continue nor turn around: the node is a trap that a
    node-count test cannot see.
    """
    return _osm_text(
        _FRAME + [(40, LAT0 + 0.001, LON0 + 0.001)],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (701, [2, 40], {"highway": "service", "oneway": "yes", "name": "Aisle In"}),
            (702, [5, 40], {"highway": "service", "oneway": "yes", "name": "Aisle Also In"}),
        ],
    )


def trapped_two_way_osm() -> str:
    """A TWO-way way ending at node 40, where the only other way is one-way in.

    Same node 40 as above. Way 703 is two-way, so a vehicle that drives up it
    can turn around — but only if something notices that it must. Way 701 is
    one-way inbound and offers no exit, so the U-turn is the sole physically
    available action. Node 40 is referenced twice, so it IS an intersection with
    a ``<node>`` polygon — which is what makes it the case where the cul-de-sac
    pass's ``node_id = 0`` sentinel is the wrong answer.
    """
    return _osm_text(
        _FRAME + [(40, LAT0 + 0.001, LON0 + 0.001)],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (701, [2, 40], {"highway": "service", "oneway": "yes", "name": "Aisle In"}),
            (703, [5, 40], {"highway": "service", "name": "Aisle Two Way"}),
        ],
    )


def antiparallel_junction_osm() -> str:
    """A junction where every onward movement is a U-turn, so none is built.

    Way 801 runs north from the crossroads (node 2, the origin) to node 50 at
    (LAT0 + 0.001, LON0) = (0 m, 110.9 m). Way 802 leaves node 50 and runs back
    SOUTH to node 51 at (LAT0 + 0.0005, LON0 + 0.00005) = (4.7 m, 55.4 m).

    Hand derivation: 801's approach heading at node 50 is due north, (0, +1);
    802's departure heading is (4.7, −55.5), i.e. 175.2° away. That is over
    ``UTURN_ANGLE_DEG`` = 150°, so ``classify_turn`` calls it a U-turn and
    §5.3.3 suppresses it — and 801's own reverse carriageway is the same segment
    and is skipped too. Both approaches at node 50 therefore lose EVERY
    candidate movement, and only the last-resort pass can keep them from being
    traps. Node 50 is referenced by two ways, so it is a real junction with a
    ``<node>`` polygon.
    """
    return _osm_text(
        _FRAME + [
            (50, LAT0 + 0.001, LON0),
            (51, LAT0 + 0.0005, LON0 + 0.00005),
        ],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (801, [2, 50], {"highway": "service", "name": "Aisle A"}),
            (802, [50, 51], {"highway": "service", "name": "Aisle B"}),
        ],
    )


def _draft(tmp_path: Path, text: str, name: str = "fixture", **kwargs):
    return build_draft(parse_osm_file(_write(tmp_path, f"{name}.osm", text), **kwargs), name=name)


def _u_turns(draft):
    return [c for c in draft.connectors if c.turn == TURN_U]


def _links_of_way(draft, way_id: int):
    return sorted((l for l in draft.links if l.osm_way_id == way_id), key=lambda l: l.id)


def _reachable_from(draft, start_link_id: int) -> set[int]:
    """Link ids reachable by following connectors out of ``start_link_id``."""
    outgoing: dict[int, list[int]] = {}
    for connector in draft.connectors:
        outgoing.setdefault(connector.from_link, []).append(connector.to_link)
    seen = {start_link_id}
    queue = deque([start_link_id])
    while queue:
        current = queue.popleft()
        for nxt in outgoing.get(current, ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


# ---------------------------------------------------------------------------
# 1. Cul-de-sacs
# ---------------------------------------------------------------------------


def test_a_two_way_dead_end_gains_exactly_one_u_turn(tmp_path: Path):
    """Way 103 is two-way and ends at node 6, which no other way touches.

    Hand derivation: three ways × directions gives links for 101 (2 segments ×
    2 directions = 4), 102 (2 × 2 = 4) and 103 (1 × 2 = 2) = 10 links. Node 6 is
    the ONLY dead end that is neither one-way nor on the boundary, so exactly
    ONE U-turn connector is expected, and it must join way 103's own two
    directions.
    """
    draft = _draft(tmp_path, cul_de_sac_osm(), "cds")
    u_turns = _u_turns(draft)
    assert len(u_turns) == 1, [c.name for c in u_turns]

    fwd, rev = _links_of_way(draft, 103)
    assert fwd.forward is True and rev.forward is False
    connector = u_turns[0]
    assert (connector.from_link, connector.to_link) == (fwd.id, rev.id)
    assert connector.node_osm_id == 6
    # A cul-de-sac is not an intersection: no <node> polygon is written for it,
    # and the signal layer keys off node_id, so it must be the 0 sentinel.
    assert connector.node_id == 0
    assert all(node.osm_id != 6 for node in draft.nodes)


def test_without_the_u_turn_the_dead_end_street_is_a_one_way_trap(tmp_path: Path):
    """The property the U-turn exists for, stated as reachability rather than count.

    Drive up way 103 (the FWD link) and you must be able to get back to way 101.
    Before this unit that was false: the two carriageways of the same street
    ended 3.5 m apart with nothing joining them.
    """
    draft = _draft(tmp_path, cul_de_sac_osm(), "cds")
    fwd, rev = _links_of_way(draft, 103)
    reachable = _reachable_from(draft, fwd.id)
    assert rev.id in reachable, "a vehicle that drives into the cul-de-sac is stranded"
    assert any(link.osm_way_id == 101 for link in draft.links if link.id in reachable)


def test_every_link_that_is_not_on_the_boundary_can_be_left(tmp_path: Path):
    """No link inside the model may be a dead end. The boundary ones may.

    This is the whole-network form of the check above, and it is the one that
    catches a fix that repairs the fixture's own street and leaves some other
    link stranded.
    """
    draft = _draft(tmp_path, cul_de_sac_osm(), "cds")
    boundary_nodes = {
        end.node_id
        for end in conn.dead_ends(conn.merge_junction_nodes(parse_osm_file(tmp_path / "cds.osm"))[0])
        if not end.emit_u_turn
    }
    leaving = {connector.from_link for connector in draft.connectors}
    stranded = [
        link.name
        for link in draft.links
        if link.id not in leaving and link.to_node_osm_id not in boundary_nodes
    ]
    assert stranded == []


def test_a_dead_end_on_the_model_boundary_gets_no_u_turn(tmp_path: Path):
    """Nodes 1/3/4/5 define the extent rectangle, so each is ON the boundary.

    A U-turn there would make the study edge a reflecting wall: traffic that
    should leave the model would bounce back into it. The four are reported as
    ``model_boundary`` and produce no connector.
    """
    extract = parse_osm_file(_write(tmp_path, "cds.osm", cul_de_sac_osm()))
    merged, _report = conn.merge_junction_nodes(extract)
    ends = {end.node_id: end for end in conn.dead_ends(merged)}
    assert sorted(ends) == [1, 3, 4, 5, 6]
    for node_id in (1, 3, 4, 5):
        assert ends[node_id].reason == conn.DEAD_END_MODEL_BOUNDARY
        assert ends[node_id].emit_u_turn is False
    assert ends[6].reason == conn.DEAD_END_U_TURN
    assert ends[6].emit_u_turn is True


def test_a_declared_bounds_element_moves_the_boundary(tmp_path: Path):
    """``<bounds>`` is the download window, and it is believed over the extent.

    With a window wider than the geometry, node 3 (previously ON the derived
    boundary) is inside the model and is read as a cul-de-sac instead. The
    reading of node 6 does not change, which is what makes this a test of the
    boundary rule rather than of the fixture.
    """
    text = _osm_text(
        _FRAME + [(6, LAT0 + 0.001, LON0 + 0.001)],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (103, [2, 6], {"highway": "residential", "name": "Dead End Rd"}),
        ],
        bounds=(LAT0 - 0.01, LON0 - 0.01, LAT0 + 0.01, LON0 + 0.01),
    )
    extract = parse_osm_file(_write(tmp_path, "wide.osm", text))
    assert extract.declared_bounds == (LAT0 - 0.01, LON0 - 0.01, LAT0 + 0.01, LON0 + 0.01)
    ends = {end.node_id: end for end in conn.dead_ends(conn.merge_junction_nodes(extract)[0])}
    assert ends[3].reason == conn.DEAD_END_U_TURN
    assert ends[6].reason == conn.DEAD_END_U_TURN


def test_a_one_way_dead_end_is_reported_and_never_repaired(tmp_path: Path):
    """A one-way street that ends nowhere has no second carriageway to turn into.

    There is nothing to connect, so nothing is connected; the case is REPORTED
    by name instead. Inventing a reverse carriageway to turn into would be
    inventing a road.
    """
    draft = _draft(tmp_path, cul_de_sac_osm(oneway=True), "oneway")
    assert _u_turns(draft) == []
    reasons = draft.metadata["connectivity"]["dead_ends_by_reason"]
    assert reasons[conn.DEAD_END_ONE_WAY] == 1
    assert len(_links_of_way(draft, 103)) == 1


def test_an_arterial_cul_de_sac_is_emitted_AND_named(tmp_path: Path):
    """Guardrail tier 2: legal but suspicious → do it, and attach the warning.

    An arterial that dead-ends is more often a break in the map than a real
    turning head. Refusing would leave vehicles stranded, so the U-turn is
    emitted — and the way is listed by name so a reviewer can disagree.
    """
    draft = _draft(tmp_path, cul_de_sac_osm(highway="primary"), "arterial")
    assert len(_u_turns(draft)) == 1
    named = draft.metadata["connectivity"]["arterial_dead_ends_named"]
    assert named == [f"way 103 (primary) at node 6: {conn.DEAD_END_U_TURN}"]
    assert "way 103" in draft.render_compliance("en")

    # An arterial dead end that could NOT be repaired lands in the same list —
    # with its own reason, so the heading is not wrong for half its contents.
    one_way = _draft(tmp_path, cul_de_sac_osm(highway="primary", oneway=True), "arterial1w")
    assert one_way.metadata["connectivity"]["arterial_dead_ends_named"] == [
        f"way 103 (primary) at node 6: {conn.DEAD_END_ONE_WAY}"
    ]


def test_a_dead_end_whose_continuation_is_a_footway_is_named_separately(tmp_path: Path):
    """Two reasons, deliberately not one — and the second had no fixture.

    Way 103 ends at node 6, and a footway (way 500) carries on from there. A
    motor vehicle still cannot continue, so the U-turn is still right; but the
    corridor DOES continue in the file, and a reviewer may reasonably disagree
    with modelling it as a turning head. Counting the two together would hide
    that judgement call inside a number — on the real Murdoch network this
    reason is 30 of the 156 U-turns emitted, about a fifth of them.
    """
    text = _osm_text(
        _FRAME + [
            (6, LAT0 + 0.001, LON0 + 0.001),
            (60, LAT0 + 0.0015, LON0 + 0.0015),
        ],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (103, [2, 6], {"highway": "residential", "name": "Dead End Rd"}),
            (500, [6, 60], {"highway": "footway", "name": "Bush Path"}),
        ],
    )
    extract = parse_osm_file(_write(tmp_path, "foot.osm", text))
    assert 6 in extract.non_drivable_node_ids
    ends = {end.node_id: end for end in conn.dead_ends(conn.merge_junction_nodes(extract)[0])}
    assert ends[6].reason == conn.DEAD_END_U_TURN_NON_DRIVABLE_CONTINUATION
    assert ends[6].emit_u_turn is True
    assert "footway" in ends[6].detail

    draft = build_draft(extract, name="foot")
    assert len(_u_turns(draft)) == 1
    reasons = draft.metadata["connectivity"]["dead_ends_by_reason"]
    assert reasons[conn.DEAD_END_U_TURN_NON_DRIVABLE_CONTINUATION] == 1
    assert conn.DEAD_END_U_TURN not in reasons

    # The counterfactual: the SAME cul-de-sac with no footway is the plain code.
    plain = parse_osm_file(_write(tmp_path, "plain.osm", cul_de_sac_osm()))
    plain_ends = {e.node_id: e for e in conn.dead_ends(conn.merge_junction_nodes(plain)[0])}
    assert plain_ends[6].reason == conn.DEAD_END_U_TURN


def test_a_residential_cul_de_sac_is_not_named_as_arterial(tmp_path: Path):
    """The counterfactual: the warning must not fire on every cul-de-sac."""
    draft = _draft(tmp_path, cul_de_sac_osm(), "cds")
    assert draft.metadata["connectivity"]["arterial_dead_ends_named"] == []


def test_the_u_turn_uses_the_lane_nearest_the_centreline(tmp_path: Path):
    """Hand derivation, left-hand traffic, an asymmetric cul-de-sac.

    ``lanes:forward=2`` / ``lanes:backward=1`` gives a 2-lane approach into a
    1-lane exit, so the connector carries ``k = min(2, 1) = 1`` lane and the
    choice of WHICH approach lane is visible — which is the whole point of the
    fixture, because a symmetric 2→2 case uses every lane and hides the rule.

    Vissim lane index 1 is the RIGHT-most lane (measured 2026-08-06, in both
    driving regimes). Under left-hand traffic each direction's carriageway sits
    LEFT of the shared centreline, so each carriageway's index 1 is the lane
    nearest the centreline. A U-turn crosses the centreline, so it is made from
    index **1** — not from index 2, the kerb lane, which is the one furthest
    from where the vehicle is going. The near-side LEFT turn, which leaves to
    the kerb side, is the counterfactual and takes index 2.
    """
    text = _osm_text(
        _FRAME + [(6, LAT0 + 0.001, LON0 + 0.001)],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (
                103,
                [2, 6],
                {
                    "highway": "residential",
                    "name": "Wide Dead End",
                    "lanes:forward": "2",
                    "lanes:backward": "1",
                },
            ),
        ],
    )
    draft = _draft(tmp_path, text, "wide")
    fwd, rev = _links_of_way(draft, 103)
    assert (fwd.lanes, rev.lanes) == (2, 1)
    connector = _u_turns(draft)[0]
    assert connector.lanes == 1
    assert connector.from_lanes == [1]
    assert connector.to_lanes == [1]
    assert connector_lane_pairs(TURN_U, 2, 1) == [(1, 1)]
    assert connector_lane_pairs(TURN_LEFT, 2, 1) == [(2, 1)]
    # A symmetric U-turn uses every lane, innermost to innermost.
    assert connector_lane_pairs(TURN_U, 2, 2) == [(1, 1), (2, 2)]


def test_a_node_where_every_way_is_one_way_inbound_is_a_named_dead_end(tmp_path: Path):
    """``occurrences == 1`` is not the only shape a dead end has.

    Node 40 is referenced by two ways, so the old terminal scan skipped it — and
    both approaches were places a vehicle left the network with nothing anywhere
    naming them. There is no U-turn to emit (both ways are one-way, so neither
    has a second carriageway), so this is REPORTED, exactly like the
    single-occurrence one-way case it is topologically the same as.
    """
    extract = parse_osm_file(_write(tmp_path, "trap.osm", one_way_trap_osm()))
    merged, _report = conn.merge_junction_nodes(extract)
    ends = {(end.way_id, end.node_id): end for end in conn.dead_ends(merged)}

    assert (701, 40) in ends and (702, 40) in ends, sorted(ends)
    for key in ((701, 40), (702, 40)):
        assert ends[key].reason == conn.DEAD_END_ONE_WAY
        assert ends[key].emit_u_turn is False

    # …and the generator's own census says the same thing about the built links.
    draft = build_draft(extract, name="trap")
    causes = {
        entry["link"]: entry["cause"]
        for entry in draft.metadata["connectivity"]["dangling_approaches"]
    }
    trapped = {name: cause for name, cause in causes.items() if "Aisle" in name}
    assert len(trapped) == 2, causes
    assert set(trapped.values()) == {conn.DANGLING_ONE_WAY}


def test_a_one_way_ways_START_node_is_not_a_trap(tmp_path: Path):
    """The counterfactual, so the rule above is not "every shared terminal".

    Node 2 is where way 701 BEGINS and it is one-way, so no vehicle can ever
    arrive there along it — it is an origin with no feeder, not a place traffic
    is stranded. Only terminals a vehicle can REACH are dead ends.
    """
    extract = parse_osm_file(_write(tmp_path, "trap.osm", one_way_trap_osm()))
    merged, _report = conn.merge_junction_nodes(extract)
    ends = {(end.way_id, end.node_id) for end in conn.dead_ends(merged)}
    assert (701, 2) not in ends and (702, 5) not in ends


def test_a_trapped_two_way_terminal_gets_its_u_turn_at_the_REAL_node_id(tmp_path: Path):
    """A dead end can be an intersection, and then the 0 sentinel is a lie.

    ``node_id = 0`` means "no ``<node>`` object exists here", which is true of a
    true cul-de-sac and false of node 40: two ways meet there, a polygon IS
    written, and ``signals.py``'s two node-scoped checks filter on exactly this
    field. A 0 makes this connector invisible to them, and the §5 report then
    says the approach has no onward movement while one genuinely leaves it.
    """
    draft = _draft(tmp_path, trapped_two_way_osm(), "trapped")
    node_40 = next(node for node in draft.nodes if node.osm_id == 40)

    u_turns = [c for c in _u_turns(draft) if c.node_osm_id == 40]
    assert len(u_turns) == 1, [c.name for c in u_turns]
    assert u_turns[0].node_id == node_40.id != 0

    # It came from the dead-end pass, named — not from the unnamed last resort.
    reasons = draft.metadata["connectivity"]["dead_ends_by_reason"]
    assert reasons[conn.DEAD_END_U_TURN] >= 1
    assert draft.metadata["connectivity"]["stranded_approach_u_turns"] == []
    assert draft.metadata["connectivity"]["dangling_approaches_by_cause"].get(
        conn.DANGLING_NO_EXIT_AT_JUNCTION, 0
    ) == 0

    # A TRUE cul-de-sac still carries the 0, which is what makes it a sentinel
    # rather than a default.
    plain = _draft(tmp_path, cul_de_sac_osm(), "cds")
    assert _u_turns(plain)[0].node_id == 0


# ---------------------------------------------------------------------------
# 2. Node merging
# ---------------------------------------------------------------------------


def test_the_tolerance_is_one_carriageway_of_the_narrowest_modelled_road():
    """7.0 m = 2 × 3.5 m, and 3.5 m is the ONE lane width MRWA §5 prints.

    Asserted as an identity against the two other places the number lives, so
    the three cannot drift apart and leave the tolerance meaning something
    nobody re-derived.
    """
    assert diff_module.NOMINAL_LANE_WIDTH_M == LANE_WIDTH_M == 3.5
    assert conn.NARROWEST_CARRIAGEWAY_M == 2.0 * LANE_WIDTH_M == 7.0
    assert conn.NODE_MERGE_TOLERANCE_M == 7.0
    assert conn.MAX_CLUSTER_DIAMETER_M == 14.0
    assert "Table 5-7" in conn.TOLERANCE_BASIS


def test_two_ways_that_touch_without_sharing_a_node_become_one_junction(tmp_path: Path):
    """Node 7 is 0.00004° east of node 2 = 3.78 m, under the 7.0 m tolerance.

    Before this unit the two ways had no node in common, so no connector was
    ever built between them and the road was severed. After it, node 7 is
    merged into node 2 and the movement exists.
    """
    extract = parse_osm_file(_write(tmp_path, "near.osm", near_touch_osm()))
    merged, report = conn.merge_junction_nodes(extract)
    assert len(report.merges) == 1
    cluster = report.merges[0]
    assert cluster.canonical_id == 2
    assert cluster.member_ids == (2, 7)
    assert cluster.max_separation_m == pytest.approx(NEAR_TOUCH_M, abs=0.05)
    assert report.canonical == {7: 2}
    assert all(7 not in way.node_ids for way in merged.ways)

    draft = build_draft(extract, name="near")
    west = _links_of_way(draft, 201)
    east = _links_of_way(draft, 202)
    assert _reachable_from(draft, west[0].id) & {link.id for link in east}


def test_a_pair_over_the_tolerance_is_left_alone(tmp_path: Path):
    """The counterfactual for the test above: 0.0001° = 9.44 m > 7.0 m.

    Same fixture, same shape, one coordinate changed — and the two ways stay two
    ways. Without this the merge test would pass for a function that merged
    everything.
    """
    extract = parse_osm_file(
        _write(tmp_path, "far.osm", near_touch_osm(lon_offset=FAR_TOUCH_LON))
    )
    merged, report = conn.merge_junction_nodes(extract)
    assert report.merges == []
    assert merged is extract
    draft = build_draft(extract, name="far")
    west = _links_of_way(draft, 201)
    east = _links_of_way(draft, 202)
    assert not (_reachable_from(draft, west[0].id) & {link.id for link in east})


def test_the_threshold_sits_where_the_derivation_puts_it(tmp_path: Path):
    """The tolerance is bracketed to ±1 mm around one carriageway, not asserted
    loosely.

    The two offsets are computed through the projection's OWN forward transform
    (``x = N(φ₀)·cos φ₀·Δλ``) rather than the rounded 94 406 m/° used for the
    readable fixtures above, because a millimetre bracket needs better than that
    conversion's 0.07 %. Each side re-measures its own separation before
    asserting the outcome, so a fixture that drifts fails as a fixture rather
    than as a threshold.

    Exactly 7.0 m is not tested through a file: an ``.osm`` coordinate is a
    decimal degree, and at this latitude even ten decimal places quantises
    longitude to ~9 µm, which is not a distance a file can express exactly.
    Bracketing at ±1 mm is the honest form of the claim.
    """
    from services.genesis.project import prime_vertical_radius_m

    metres_per_degree = (
        prime_vertical_radius_m(LAT0) * math.cos(math.radians(LAT0)) * math.pi / 180.0
    )

    def separation_and_merges(metres: float, label: str):
        extract = parse_osm_file(
            _write(tmp_path, f"{label}.osm", near_touch_osm(lon_offset=metres / metres_per_degree))
        )
        plane = origin_for_bounds(*extract.bounds())
        assert plane.lat0_deg == pytest.approx(LAT0, abs=1e-9)
        measured = math.dist(
            plane.forward(extract.nodes[2].lat, extract.nodes[2].lon),
            plane.forward(extract.nodes[7].lat, extract.nodes[7].lon),
        )
        return measured, conn.merge_junction_nodes(extract)[1].merges

    over, merges_over = separation_and_merges(conn.NODE_MERGE_TOLERANCE_M + 0.001, "over")
    assert over == pytest.approx(7.001, abs=1e-4)
    assert merges_over == []

    under, merges_under = separation_and_merges(conn.NODE_MERGE_TOLERANCE_M - 0.001, "under")
    assert under == pytest.approx(6.999, abs=1e-4)
    assert len(merges_under) == 1


def test_two_ways_at_different_layers_are_NEVER_merged(tmp_path: Path):
    """The grade-separation refusal, as a counterfactual on one tag.

    Identical geometry to the merge test — 3.78 m apart, inside the tolerance —
    but way 202 carries ``layer=1`` and ``bridge=yes``. A freeway passes within
    centimetres of the road under it *in plan*; merging here would weld a
    network to the one crossing it. The pair is refused BY NAME and the two
    remain unconnected.
    """
    extract = parse_osm_file(
        _write(
            tmp_path,
            "bridge.osm",
            near_touch_osm(second_way_tags={"layer": "1", "bridge": "yes"}),
        )
    )
    _merged, report = conn.merge_junction_nodes(extract)
    assert report.merges == []
    assert [r.code for r in report.refusals] == [conn.REFUSE_GRADE_SEPARATED]
    refusal = report.refusals[0]
    assert {refusal.node_a, refusal.node_b} == {2, 7}
    assert refusal.separation_m == pytest.approx(NEAR_TOUCH_M, abs=0.05)

    draft = build_draft(extract, name="bridge")
    west = _links_of_way(draft, 201)
    east = _links_of_way(draft, 202)
    assert not (_reachable_from(draft, west[0].id) & {link.id for link in east})


def test_a_tunnel_under_a_road_is_refused_for_the_same_reason(tmp_path: Path):
    """``tunnel=yes`` + ``layer=-1`` is the same fact with the sign flipped."""
    extract = parse_osm_file(
        _write(
            tmp_path,
            "tunnel.osm",
            near_touch_osm(second_way_tags={"layer": "-1", "tunnel": "yes"}),
        )
    )
    _merged, report = conn.merge_junction_nodes(extract)
    assert report.merges == []
    assert [r.code for r in report.refusals] == [conn.REFUSE_GRADE_SEPARATED]


def test_a_cluster_may_not_walk_further_than_the_cap(tmp_path: Path):
    """Four nodes 5.0 m apart in a row span 15.0 m — over the 14.0 m cap.

    Each consecutive pair is admissible on its own, so without the cap the union
    would chain across the whole row and declare a 15 m stretch of road to be
    one junction. The cap stops the chain and names the refusal; the pairs that
    fit are still merged.

    5.0 m of longitude here is 5.0 / 94 406 = 5.296e-5°.

    Each of the four chain nodes is a junction the file DECLARES — two ways meet
    at each — because only a declared junction is mergeable at all
    (``REFUSE_NO_JUNCTION_TO_RECONCILE``). What is under test here is the
    diameter cap, so the fixture has to clear the eligibility rule first, or it
    would pass for the wrong reason.
    """
    step = 5.0 / 94406.0
    nodes = _FRAME + [
        (10, LAT0 + 0.001, LON0 + 0 * step),
        (11, LAT0 + 0.001, LON0 + 1 * step),
        (12, LAT0 + 0.001, LON0 + 2 * step),
        (13, LAT0 + 0.001, LON0 + 3 * step),
    ]
    ways = [
        (101, [1, 2, 3], {"highway": "secondary"}),
        (102, [4, 2, 5], {"highway": "residential"}),
        (301, [1, 10], {"highway": "residential"}),
        (311, [10, 3], {"highway": "residential"}),
        (302, [11, 3], {"highway": "residential"}),
        (312, [11, 4], {"highway": "residential"}),
        (303, [12, 4], {"highway": "residential"}),
        (313, [12, 5], {"highway": "residential"}),
        (304, [13, 5], {"highway": "residential"}),
        (314, [13, 1], {"highway": "residential"}),
    ]
    extract = parse_osm_file(_write(tmp_path, "chain.osm", _osm_text(nodes, ways)))
    _merged, report = conn.merge_junction_nodes(extract)
    assert conn.REFUSE_CLUSTER_TOO_WIDE in {r.code for r in report.refusals}
    for cluster in report.merges:
        assert cluster.max_separation_m <= conn.MAX_CLUSTER_DIAMETER_M


def test_a_stub_entirely_inside_one_junction_is_dropped_by_name(tmp_path: Path):
    """A way whose every node lands in one cluster is not a road between
    junctions — it IS the junction.

    Way 401 runs from node 8 to node 9, 3.78 m apart, and both ends merge with
    ways arriving there. Dropping it silently would be a deleted carriageway, so
    it appears in ``skipped`` with the existing ``zero_length_after_deduplication``
    reason and in the merge report's refusal list.
    """
    nodes = _FRAME + [
        (8, LAT0 + 0.001, LON0),
        (9, LAT0 + 0.001, LON0 + NEAR_TOUCH_LON),
    ]
    ways = [
        (101, [1, 2, 3], {"highway": "secondary"}),
        (102, [4, 2, 5], {"highway": "residential"}),
        (401, [8, 9], {"highway": "service"}),
        (402, [1, 8], {"highway": "residential"}),
        (403, [9, 3], {"highway": "residential"}),
    ]
    extract = parse_osm_file(_write(tmp_path, "stub.osm", _osm_text(nodes, ways)))
    merged, report = conn.merge_junction_nodes(extract)
    assert report.collapsed_way_ids == (401,)
    assert conn.REFUSE_WOULD_COLLAPSE_WAY in {r.code for r in report.refusals}
    assert 401 in {skip.id for skip in merged.skipped if skip.reason == SKIP_ZERO_LENGTH}
    assert 401 not in {way.id for way in merged.ways}
    # …and the two roads it joined are still joined, through the merged junction.
    draft = build_draft(extract, name="stub")
    assert _reachable_from(draft, _links_of_way(draft, 402)[0].id) & {
        link.id for link in _links_of_way(draft, 403)
    }


def test_two_lone_road_ends_are_never_welded_into_an_invented_junction(tmp_path: Path):
    """The rule that keeps the tolerance's own derivation honest.

    "Two junctions cannot be closer than one carriageway" is a statement about
    JUNCTIONS. Court A's head and Court B's head are 3.78 m apart and neither is
    a junction the file declares — they are two roads that end near each other.
    Merging them would make node 20 a node of two ways, which
    ``intersection_node_ids()`` then reads as an intersection, and ``build_draft``
    would emit ordinary turn connectors joining one street to the other: a road
    that exists in no source data and in no reality, named nowhere, because
    ``dead_ends()`` no longer sees either terminal afterwards.

    So the merge is REFUSED, by name, and the refusal is the output. Nothing is
    stranded by refusing: each head is then read as the cul-de-sac it is and
    gets its own U-turn.
    """
    extract = parse_osm_file(_write(tmp_path, "courts.osm", two_lone_dead_ends_osm()))
    merged, report = conn.merge_junction_nodes(extract)

    assert report.merges == [], "two unrelated road ends were welded into a junction"
    refusals = [r for r in report.refusals if r.code == conn.REFUSE_NO_JUNCTION_TO_RECONCILE]
    assert len(refusals) == 1
    assert {refusals[0].node_a, refusals[0].node_b} == {20, 21}
    assert refusals[0].separation_m == pytest.approx(NEAR_TOUCH_M, abs=0.05)
    assert merged.nodes[20] is extract.nodes[20] and 21 in merged.nodes

    # …and no connector joins the two heads. Stated as "no connector between a
    # Court A link and a Court B link" rather than as reachability, because the
    # two courts ARE legitimately connected the long way round, through First
    # Ave — which is the point: the invented road is the short cut at the heads.
    draft = build_draft(extract, name="courts")
    court_a = {link.id for link in _links_of_way(draft, 201)}
    court_b = {link.id for link in _links_of_way(draft, 202)}
    welded = [
        connector.name
        for connector in draft.connectors
        if {connector.from_link, connector.to_link} & court_a
        and {connector.from_link, connector.to_link} & court_b
    ]
    assert welded == [], "Court A was joined to Court B by a road the file does not contain"
    assert all(node.osm_id not in (20, 21) for node in draft.nodes)

    # Refusing costs nothing a vehicle notices: both heads are cul-de-sacs and
    # both get a U-turn, so neither street is a trap.
    ends = {end.node_id: end for end in conn.dead_ends(merged)}
    assert ends[20].reason == conn.DEAD_END_U_TURN and ends[20].emit_u_turn is True
    assert ends[21].reason == conn.DEAD_END_U_TURN and ends[21].emit_u_turn is True
    turned = {connector.node_osm_id for connector in _u_turns(draft)}
    assert {20, 21} <= turned
    # Nothing dangles because of the refusal. The only link ends no connector
    # leaves are frame terminals 1, 3 and 4 — the model boundary, and correct.
    # (Node 5 is not among them: Court B leaves from it, so First Ave's inbound
    # carriageway has somewhere to go.)
    assert draft.metadata["connectivity"]["dangling_approaches_by_cause"] == {
        conn.DANGLING_MODEL_BOUNDARY: 3
    }


def test_the_refusal_to_invent_a_junction_is_reported_not_silent(tmp_path: Path):
    """A refusal a reader cannot find is the same as a silent merge.

    "These two roads end within one carriageway of each other and were NOT
    joined" is a line a reviewer can act on — by correcting the base map if they
    really do meet. It goes into the merge report, the ``genesis.json`` and the
    §5 compliance render.
    """
    draft = _draft(tmp_path, two_lone_dead_ends_osm(), "courts")
    refusals = draft.metadata["connectivity"]["node_merge"]["refusals"]
    assert [r["code"] for r in refusals] == [conn.REFUSE_NO_JUNCTION_TO_RECONCILE]
    assert "junction" in refusals[0]["detail"]
    for language in ("ko", "en", "ja"):
        rendered = draft.render_compliance(language)
        assert f"refused / {conn.REFUSE_NO_JUNCTION_TO_RECONCILE}: 1" in rendered
    # …and the rule is in the tolerance's own stated basis, so a reader meets it
    # wherever the 7.0 m is quoted rather than only in the source.
    assert "junction the file already declares" in conn.TOLERANCE_BASIS


def test_a_terminal_still_merges_into_a_junction_the_file_DOES_declare(tmp_path: Path):
    """The counterfactual for the rule above, so it is not "refuse everything".

    ``near_touch_osm``'s node 2 is referenced by West Arm AND Cross St, so the
    file declares a junction there; node 7 is East Arm's stray terminal 3.78 m
    away. That is a junction described twice, and reconciling it is the case the
    merge exists for. One node of the pair being a declared junction is the only
    difference from the fixture above — same distance, same "no shared node",
    opposite answer.
    """
    extract = parse_osm_file(_write(tmp_path, "near.osm", near_touch_osm()))
    assert 2 in extract.intersection_node_ids()
    assert 7 not in extract.intersection_node_ids()
    _merged, report = conn.merge_junction_nodes(extract)
    assert [c.member_ids for c in report.merges] == [(2, 7)]
    assert conn.REFUSE_NO_JUNCTION_TO_RECONCILE not in {r.code for r in report.refusals}


def test_grade_separation_refuses_the_CLUSTER_not_only_the_pair(tmp_path: Path):
    """A ground street may not reach a freeway deck through a transition node.

    Node 31 is a real ground/bridge transition and its signature is legitimately
    BOTH grades, so it intersects node 30's (ground) and node 32's (bridge)
    alike. Pairwise, both merges are admissible. 30 and 32 are 10.0 m apart —
    over the tolerance — so that pair is never a candidate and is never
    compared, and the union would quietly put a local street and an elevated
    motorway on one node. The check therefore has to hold for the whole cluster:
    every member must share one common vertical position.

    The 14.0 m diameter cap cannot be what refuses this — the row spans 10.0 m —
    so a pass here is the grade rule and nothing else.
    """
    extract = parse_osm_file(_write(tmp_path, "grade.osm", transitive_grade_osm()))
    merged, report = conn.merge_junction_nodes(extract)

    assert [c.member_ids for c in report.merges] == [(30, 31)]
    refusals = [r for r in report.refusals if r.code == conn.REFUSE_GRADE_SEPARATED]
    assert len(refusals) == 1, [r.as_dict() for r in report.refusals]
    assert {refusals[0].node_a, refusals[0].node_b} == {31, 32}
    assert refusals[0].separation_m == pytest.approx(5.0, abs=0.05)
    assert conn.REFUSE_CLUSTER_TOO_WIDE not in {r.code for r in report.refusals}

    # 32 kept its own identity, so the deck touches nothing at grade.
    assert all(32 not in way.node_ids or way.id == 605 for way in merged.ways)
    ground_nodes = {n for way in merged.ways if way.id in (601, 602) for n in way.node_ids}
    deck_nodes = {n for way in merged.ways if way.id == 605 for n in way.node_ids}
    assert not (ground_nodes & deck_nodes)

    draft = build_draft(extract, name="grade")
    local = _links_of_way(draft, 602)
    deck = {link.id for link in _links_of_way(draft, 605)}
    assert not (_reachable_from(draft, local[0].id) & deck), (
        "a local street was welded onto the elevated freeway deck"
    )


def test_the_cluster_grade_rule_still_admits_a_coherent_junction(tmp_path: Path):
    """The counterfactual: sharing one grade is enough, mixed members and all.

    Nodes 30 and 31 DO merge in the fixture above — 31 carries a bridge way, but
    both members are present at grade 0, so the cluster is one junction at one
    height that a bridge happens to leave from. Refusing that would sever every
    real interchange approach from the road it meets.
    """
    extract = parse_osm_file(_write(tmp_path, "grade.osm", transitive_grade_osm()))
    _merged, report = conn.merge_junction_nodes(extract)
    assert report.merges[0].member_ids == (30, 31)
    assert report.canonical == {31: 30}


def test_no_coordinate_pair_can_express_the_exact_tolerance(tmp_path: Path):
    """Why the test below moves the threshold instead of moving the geometry.

    Both nodes sit at LAT0, so their separation is a pure x difference and the
    only free variable is one longitude. One ULP of a longitude near 115.8°
    moves that separation by ~1.3e-9 m, while the float resolution *at* 7.0 m is
    ~8.9e-16 m — six orders of magnitude finer. Between two adjacent
    representable longitudes the separation steps clean over 7.0 without ever
    landing on it, so **no coordinate pair in this frame is exactly one
    carriageway apart**, in a file or in memory.

    Measured here rather than asserted in prose, so the claim cannot rot.
    """
    extract = parse_osm_file(_write(tmp_path, "exact.osm", near_touch_osm()))
    plane = origin_for_bounds(*extract.bounds())
    anchor = plane.forward(extract.nodes[2].lat, extract.nodes[2].lon)
    lon = plane.inverse(anchor[0] + conn.NODE_MERGE_TOLERANCE_M, anchor[1])[1]

    def separation(value: float) -> float:
        return math.dist(plane.forward(LAT0, value), anchor)

    below, above = separation(lon), separation(math.nextafter(lon, math.inf))
    assert below < conn.NODE_MERGE_TOLERANCE_M < above, (below, above)
    assert above - below > 1e-10, "one longitude ULP moves the separation by ~1.3 nm"
    assert math.ulp(conn.NODE_MERGE_TOLERANCE_M) < 1e-14


def test_a_pair_exactly_at_the_tolerance_is_not_merged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The strict ``<`` at exact equality, tested at the boundary itself.

    The geometry cannot be placed exactly one carriageway apart (the test above
    measures why), so the threshold is moved onto the geometry instead: the
    fixture's own measured separation becomes ``NODE_MERGE_TOLERANCE_M`` for the
    duration, which makes ``distance < NODE_MERGE_TOLERANCE_M`` an exact-equality
    comparison on the real code path. What is under test is the OPERATOR, and
    that is what this reaches.

    A one-character regression (``<`` → ``<=``) merges the two junctions the
    module docstring calls the closest two DISTINCT junctions that can legally
    exist, and nothing else in the suite would notice: the existing bracket only
    goes to ±1 mm.
    """
    extract = parse_osm_file(_write(tmp_path, "exact.osm", near_touch_osm()))
    plane = origin_for_bounds(*extract.bounds())
    measured = math.dist(
        plane.forward(extract.nodes[2].lat, extract.nodes[2].lon),
        plane.forward(extract.nodes[7].lat, extract.nodes[7].lon),
    )
    assert measured == pytest.approx(NEAR_TOUCH_M, abs=0.05)

    monkeypatch.setattr("services.genesis.connectivity.NODE_MERGE_TOLERANCE_M", measured)
    assert conn.merge_junction_nodes(extract)[1].merges == [], (
        "a pair exactly one carriageway apart is the closest two DISTINCT "
        "junctions can be, and must be left alone"
    )

    # One ULP of TOLERANCE wider and the same pair IS one junction, so the
    # assertion above is about the comparison and not about the pair being
    # unmergeable for some other reason.
    monkeypatch.setattr(
        "services.genesis.connectivity.NODE_MERGE_TOLERANCE_M",
        math.nextafter(measured, math.inf),
    )
    assert len(conn.merge_junction_nodes(extract)[1].merges) == 1
    # The shipped value is untouched by all of the above.
    assert conn.NARROWEST_CARRIAGEWAY_M == 7.0


def test_a_clean_extract_is_returned_unchanged(tmp_path: Path):
    """No candidate pair → the SAME object back, so a file with clean topology
    produces exactly the bytes it produced before this pass existed."""
    extract = parse_osm_file(_write(tmp_path, "clean.osm", cul_de_sac_osm()))
    merged, report = conn.merge_junction_nodes(extract)
    assert merged is extract
    assert report.merges == [] and report.refusals == []


# ---------------------------------------------------------------------------
# 2b. The last-resort U-turn, and the generator's own dangling-end census
# ---------------------------------------------------------------------------


def test_the_last_resort_u_turn_fires_when_every_movement_is_antiparallel(tmp_path: Path):
    """The seventh rule, exercised — it had no fixture at all.

    At node 50 both approaches lose every candidate movement: the onward way is
    175.2° away (over ``UTURN_ANGLE_DEG`` = 150°, so ``classify_turn`` says
    U-turn and §5.3.3 suppresses it) and each way's own reverse carriageway is
    the same segment and is skipped. Turning around is then the ONLY physically
    available action, which is why supplying it creates no alternative path.

    Neither approach is a dead end by topology — way 802 genuinely leaves node
    50 — so ``dead_ends()`` cannot reach this case and the last-resort pass is
    the only thing standing between these links and being silent traps.
    """
    draft = _draft(tmp_path, antiparallel_junction_osm(), "anti")
    stranded = draft.metadata["connectivity"]["stranded_approach_u_turns"]
    assert len(stranded) == 2, stranded
    assert all("u_turn" in name for name in stranded)

    ends = {(e.way_id, e.node_id) for e in conn.dead_ends(parse_osm_file(tmp_path / "anti.osm"))}
    assert (801, 50) not in ends, "node 50 is a junction, not a topological dead end"

    leaving = {connector.from_link for connector in draft.connectors}
    at_50 = [link for link in draft.links if link.to_node_osm_id == 50]
    assert len(at_50) == 2
    assert all(link.id in leaving for link in at_50)


def test_a_last_resort_u_turn_carries_the_real_node_id_of_its_junction(tmp_path: Path):
    """This pass exists for "an approach that reaches a node OTHER WAYS DO
    TOUCH", so its node is a real ``<node>`` with a real polygon — the opposite
    of the cul-de-sac case whose ``0`` sentinel it used to copy.

    ``signals.py`` filters on ``connector.node_id`` in both of its node-scoped
    checks, so a 0 here hides the connector and the report then WARNs that the
    approach has no onward movement while one genuinely leaves it.
    """
    draft = _draft(tmp_path, antiparallel_junction_osm(), "anti")
    node_50 = next(node for node in draft.nodes if node.osm_id == 50)
    last_resort = [c for c in _u_turns(draft) if c.node_osm_id == 50]
    assert len(last_resort) == 2
    assert {c.node_id for c in last_resort} == {node_50.id}
    assert node_50.id != 0


def test_the_last_resort_pass_never_supplies_a_u_turn_at_the_model_boundary(
    tmp_path: Path,
):
    """The guard that a real regression on Hope Road put there, as a test.

    Every refusal the dead-end pass made binds this pass too. Without that, a
    boundary link — whose only "exit" is its own reverse carriageway — gains a
    U-turn and the study edge becomes the reflecting wall the boundary band
    exists to prevent: traffic that should leave the model bounces back into it.
    """
    draft = _draft(tmp_path, antiparallel_junction_osm(), "anti")
    boundary = {1, 3, 4, 5}
    assert not [c for c in _u_turns(draft) if c.node_osm_id in boundary], (
        "the study boundary was turned into a reflecting wall"
    )
    causes = draft.metadata["connectivity"]["dangling_approaches_by_cause"]
    assert causes.get(conn.DANGLING_MODEL_BOUNDARY, 0) == len(boundary)


def test_the_generator_names_every_link_end_no_connector_leaves(tmp_path: Path):
    """The census is the generator's, not a second tool's.

    Every dangling end carries a cause the module that BUILT the network
    decided. Before this, two of the four causes reported on the real network
    existed only inside the probe: ``dead_ends()`` structurally could not see
    them, so no reason code, no compliance line and no ``genesis.json`` entry
    named them anywhere, and 8 approaches were silent traps findable only by
    running a separate script that re-derived the answer for itself.
    """
    draft = _draft(tmp_path, one_way_trap_osm(), "trap")
    census = draft.metadata["connectivity"]["dangling_approaches"]
    by_cause = draft.metadata["connectivity"]["dangling_approaches_by_cause"]

    leaving = {connector.from_link for connector in draft.connectors}
    expected = {link.id for link in draft.links if link.id not in leaving}
    assert {entry["link_id"] for entry in census} == expected
    assert expected, "the fixture must actually strand something"
    assert sum(by_cause.values()) == len(census)
    assert all(entry["cause"] for entry in census)
    assert conn.DANGLING_UNATTRIBUTED not in by_cause

    rendered = draft.render_compliance("en")
    assert "link ends no connector leaves" in rendered
    for cause, count in by_cause.items():
        assert f"{cause}: {count}" in rendered


def test_a_u_turn_whose_carriageway_pair_is_missing_is_counted_not_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """``UNBUILDABLE_U_TURN_NO_LINK_PAIR`` — defined, and now exercised.

    A dead end is decided from OSM topology, which knows nothing about
    ``MIN_LINK_LENGTH_M``. So a terminal segment that was dropped for being too
    short leaves a dead end whose FWD/REV pair is not in the draft to join. That
    is not a U-turn that can be built, and it must not be a U-turn that quietly
    is not built either.

    The threshold is monkeypatched for the same reason
    ``test_a_segment_too_short_to_emit_is_named_in_the_report`` patches it: at
    the shipped 2.0 m the branch is nearly unreachable by construction, and what
    is under test is the RECORDING, not the constant. Way 103 runs node 2 → node
    6, ~146 m, so 200 m drops it and leaves the 283 m frame ways alone.
    """
    monkeypatch.setattr("services.genesis.draft.MIN_LINK_LENGTH_M", 200.0)
    draft = _draft(tmp_path, cul_de_sac_osm(), "unbuildable")

    assert not _links_of_way(draft, 103), "the fixture must drop way 103's only segment"
    unbuildable = draft.metadata["connectivity"]["u_turns_unbuildable"]
    assert len(unbuildable) == 1, unbuildable
    assert "way 103 at node 6" in unbuildable[0]
    assert UNBUILDABLE_U_TURN_NO_LINK_PAIR in unbuildable[0]
    assert _u_turns(draft) == []


# ---------------------------------------------------------------------------
# 3. Turn restrictions
# ---------------------------------------------------------------------------


def _restriction_osm(kind: str, *, via_type: str = "node") -> str:
    """The crossroads, plus one ``type=restriction`` relation over node 2.

    ``from`` is way 101's western half, ``to`` is way 102, ``via`` is node 2.
    """
    return _osm_text(
        _FRAME,
        [
            (101, [1, 2], {"highway": "secondary", "name": "Main St", "oneway": "yes"}),
            (102, [2, 5], {"highway": "residential", "name": "First Ave"}),
            (104, [2, 3], {"highway": "residential", "name": "Onward St"}),
        ],
        relations=[
            (
                900,
                [("way", 101, "from"), (via_type, 2 if via_type == "node" else 104, "via"),
                 ("way", 102, "to")],
                {"type": "restriction", "restriction": kind},
            )
        ],
    )


def test_a_banned_movement_is_not_built(tmp_path: Path):
    """``no_left_turn`` from way 101 into way 102 at node 2 removes that movement
    and nothing else.

    Counted rather than eyeballed: the same fixture without the relation has one
    more connector, and the missing one is named in the report.
    """
    with_ban = _draft(tmp_path, _restriction_osm("no_left_turn"), "ban")
    without = _draft(
        tmp_path,
        _osm_text(
            _FRAME,
            [
                (101, [1, 2], {"highway": "secondary", "name": "Main St", "oneway": "yes"}),
                (102, [2, 5], {"highway": "residential", "name": "First Ave"}),
                (104, [2, 3], {"highway": "residential", "name": "Onward St"}),
            ],
        ),
        "noban",
    )
    assert len(with_ban.connectors) == len(without.connectors) - 1
    suppressed = with_ban.metadata["connectivity"]["movements_suppressed_by_restriction"]
    assert len(suppressed) == 1
    assert "no_left_turn" in suppressed[0] and "relation 900" in suppressed[0]
    assert with_ban.metadata["connectivity"]["osm_turn_restrictions_enforced"] == 1


def test_an_only_restriction_bans_everything_else(tmp_path: Path):
    """``only_straight_on`` from way 101 into way 102 leaves ONLY that movement.

    The fixture gives way 101 two possible exits at node 2 (ways 102 and 104),
    so the ``only_`` form must remove the one that is not named.
    """
    draft = _draft(tmp_path, _restriction_osm("only_straight_on"), "only")
    approach = _links_of_way(draft, 101)[0]
    onward = {link.id for link in _links_of_way(draft, 104)}
    allowed = {link.id for link in _links_of_way(draft, 102)}
    reached = {c.to_link for c in draft.connectors if c.from_link == approach.id}
    assert reached & allowed
    assert not (reached & onward)


def test_a_via_way_restriction_is_refused_by_name_not_ignored(tmp_path: Path):
    """A restriction over a whole link has no single connector to remove.

    Approximating it would either ban a legal turn or leave a banned one legal,
    and neither is visible in a rendering — so it is recorded as a refusal and
    counted in the report.
    """
    draft = _draft(tmp_path, _restriction_osm("no_left_turn", via_type="way"), "viaway")
    refused = draft.metadata["connectivity"]["osm_turn_restrictions_refused"]
    assert [r["reason"] for r in refused] == [RESTRICTION_VIA_NOT_A_NODE]
    assert draft.metadata["connectivity"]["osm_turn_restrictions_enforced"] == 0
    assert draft.metadata["connectivity"]["movements_suppressed_by_restriction"] == []


def test_a_restriction_that_suppressed_nothing_is_not_reported_as_enforced(
    tmp_path: Path,
):
    """ENFORCED must mean a movement was removed, not that a relation parsed.

    The relation here is well formed and names two ways the network keeps — but
    its ``via`` is node 5, and ways 101 and 102 actually meet at node 2.
    ``suppressed_by_restriction`` is only ever asked about real junction nodes,
    so this ban never fires: no connector is removed, and the connector count is
    identical with and without the relation. Reporting it as "1 restriction
    enforced" tells a reader a movement ban took effect when nothing did — the
    confident wrong number this repository exists to catch, in the report that
    exists to catch them.

    So both numbers are published, and the relation that did nothing is NAMED.
    """
    text = _osm_text(
        _FRAME,
        [
            (101, [1, 2], {"highway": "secondary", "name": "Main St", "oneway": "yes"}),
            (102, [2, 5], {"highway": "residential", "name": "First Ave"}),
            (104, [2, 3], {"highway": "residential", "name": "Onward St"}),
        ],
        relations=[
            (
                900,
                [("way", 101, "from"), ("node", 5, "via"), ("way", 102, "to")],
                {"type": "restriction", "restriction": "no_left_turn"},
            )
        ],
    )
    draft = _draft(tmp_path, text, "inert")
    connectivity = draft.metadata["connectivity"]

    assert connectivity["osm_turn_restrictions_parsed"] == 1
    assert connectivity["osm_turn_restrictions_enforced"] == 0
    assert connectivity["osm_turn_restrictions_inert"] == [900]
    assert connectivity["movements_suppressed_by_restriction"] == []
    assert connectivity["osm_turn_restrictions_refused"] == []

    # …and it really is inert: the same fixture without the relation builds the
    # same connectors.
    without = _draft(
        tmp_path,
        _osm_text(
            _FRAME,
            [
                (101, [1, 2], {"highway": "secondary", "name": "Main St", "oneway": "yes"}),
                (102, [2, 5], {"highway": "residential", "name": "First Ave"}),
                (104, [2, 3], {"highway": "residential", "name": "Onward St"}),
            ],
        ),
        "noinert",
    )
    assert len(draft.connectors) == len(without.connectors)
    rendered = draft.render_compliance("en")
    assert "inert" in rendered and "relation(s) [900]" in rendered


def test_a_restriction_that_DOES_suppress_a_movement_counts_as_enforced(tmp_path: Path):
    """The counterfactual for the test above, so "enforced" is not always zero."""
    draft = _draft(tmp_path, _restriction_osm("no_left_turn"), "ban")
    connectivity = draft.metadata["connectivity"]
    assert connectivity["osm_turn_restrictions_parsed"] == 1
    assert connectivity["osm_turn_restrictions_enforced"] == 1
    assert connectivity["osm_turn_restrictions_inert"] == []


def test_a_no_u_turn_that_stops_a_cul_de_sac_repair_also_counts_as_enforced(
    tmp_path: Path,
):
    """A ban that removes a U-turn is enforced as much as one that removes a turn.

    The two live on different code paths — ``dead_ends()`` reads the no-U-turn
    family, the connector loop reads the rest — and a count that saw only one of
    them would under-report bans that DID take effect, which is the mirror of
    the over-report above and just as wrong.
    """
    text = _osm_text(
        _FRAME + [(6, LAT0 + 0.001, LON0 + 0.001)],
        [
            (101, [1, 2, 3], {"highway": "secondary"}),
            (102, [4, 2, 5], {"highway": "residential"}),
            (103, [2, 6], {"highway": "residential", "name": "Dead End Rd"}),
        ],
        relations=[
            (
                901,
                [("way", 103, "from"), ("node", 6, "via"), ("way", 103, "to")],
                {"type": "restriction", "restriction": "no_u_turn"},
            )
        ],
    )
    draft = _draft(tmp_path, text, "noturn")
    connectivity = draft.metadata["connectivity"]
    assert connectivity["osm_turn_restrictions_enforced"] == 1
    assert connectivity["osm_turn_restrictions_inert"] == []


def test_the_other_three_restriction_refusals_are_each_named(tmp_path: Path):
    """An unread restriction leaves a banned movement legal, so all four
    refusal codes have to be reachable and correctly tagged — not just the
    via-way one.

    ``restriction=no_stop`` is outside the enforced set; a relation missing its
    ``via`` member cannot be reduced to a movement at all; and a relation naming
    a way this extract does not keep (way 999 is absent) is a ban the model does
    not carry. Each is a REFUSAL by name, never a silent drop.
    """
    from services.genesis.osm import (
        RESTRICTION_KIND_UNKNOWN,
        RESTRICTION_MEMBERS_INCOMPLETE,
        RESTRICTION_WAY_NOT_KEPT,
    )

    ways = [
        (101, [1, 2], {"highway": "secondary", "name": "Main St", "oneway": "yes"}),
        (102, [2, 5], {"highway": "residential", "name": "First Ave"}),
        (104, [2, 3], {"highway": "residential", "name": "Onward St"}),
    ]
    cases = {
        RESTRICTION_KIND_UNKNOWN: (
            [("way", 101, "from"), ("node", 2, "via"), ("way", 102, "to")],
            {"type": "restriction", "restriction": "no_stop"},
        ),
        RESTRICTION_MEMBERS_INCOMPLETE: (
            [("way", 101, "from"), ("way", 102, "to")],
            {"type": "restriction", "restriction": "no_left_turn"},
        ),
        RESTRICTION_WAY_NOT_KEPT: (
            [("way", 101, "from"), ("node", 2, "via"), ("way", 999, "to")],
            {"type": "restriction", "restriction": "no_left_turn"},
        ),
    }
    for reason, (members, tags) in cases.items():
        draft = _draft(
            tmp_path, _osm_text(_FRAME, ways, relations=[(910, members, tags)]), reason
        )
        connectivity = draft.metadata["connectivity"]
        refused = connectivity["osm_turn_restrictions_refused"]
        assert [r["reason"] for r in refused] == [reason], (reason, refused)
        assert refused[0]["relation_id"] == 910
        assert refused[0]["detail"], "a refusal with no detail is not actionable"
        assert connectivity["osm_turn_restrictions_parsed"] == 0
        assert connectivity["osm_turn_restrictions_enforced"] == 0
        assert connectivity["movements_suppressed_by_restriction"] == []


def test_a_no_u_turn_relation_suppresses_the_cul_de_sac_u_turn(tmp_path: Path):
    """The map's ban wins over the generator's repair.

    Same cul-de-sac fixture as §1, plus ``restriction=no_u_turn`` on way 103 at
    node 6: no U-turn connector is emitted, and the dead end is reported with
    the restriction as its reason rather than silently repaired anyway.
    """
    text = _osm_text(
        _FRAME + [(6, LAT0 + 0.001, LON0 + 0.001)],
        [
            (101, [1, 2, 3], {"highway": "secondary"}),
            (102, [4, 2, 5], {"highway": "residential"}),
            (103, [2, 6], {"highway": "residential", "name": "Dead End Rd"}),
        ],
        relations=[
            (
                901,
                [("way", 103, "from"), ("node", 6, "via"), ("way", 103, "to")],
                {"type": "restriction", "restriction": "no_u_turn"},
            )
        ],
    )
    draft = _draft(tmp_path, text, "noturn")
    assert _u_turns(draft) == []
    reasons = draft.metadata["connectivity"]["dead_ends_by_reason"]
    assert reasons[conn.DEAD_END_U_TURN_RESTRICTED] == 1


# ---------------------------------------------------------------------------
# 4. The service-road policy
# ---------------------------------------------------------------------------


def _service_cul_de_sac_osm() -> str:
    return _osm_text(
        _FRAME + [(6, LAT0 + 0.001, LON0 + 0.001)],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (103, [2, 6], {"highway": "service", "service": "parking_aisle"}),
        ],
    )


def test_by_default_a_service_way_is_a_carriageway_and_is_connected(tmp_path: Path):
    """The stated default: service ways are modelled and connected on the same
    terms as any other drivable way, U-turn included.

    The unacceptable third state — present in the file, and impossible to drive
    out of — is what this asserts against.
    """
    draft = _draft(tmp_path, _service_cul_de_sac_osm(), "svc")
    assert any(link.highway == "service" for link in draft.links)
    assert len(_u_turns(draft)) == 1
    fwd, rev = _links_of_way(draft, 103)
    assert rev.id in _reachable_from(draft, fwd.id)


def test_excluding_service_ways_is_explicit_and_produces_a_NAMED_skip(tmp_path: Path):
    """The opt-in half. An exclusion the report cannot see is a silent deletion."""
    path = _write(tmp_path, "svc.osm", _service_cul_de_sac_osm())
    extract = parse_osm_file(path, exclude_service=True)
    assert [s.id for s in extract.skipped if s.reason == SKIP_SERVICE_EXCLUDED_BY_POLICY] == [103]
    draft = build_draft(extract, name="svc")
    assert all(link.highway != "service" for link in draft.links)
    assert "103" in draft.render_compliance("en")


def test_the_service_policy_is_written_down_where_a_reader_will_find_it():
    """The unstated accident this unit replaced was a table entry with no reason
    attached. The policy is prose in the module that owns the allowlist."""
    from services.genesis import osm as osm_module

    source = Path(osm_module.__file__).read_text(encoding="utf-8")
    marker = source[: source.index("DRIVABLE_HIGHWAY_DEFAULT_LANES")]
    assert "service" in marker.lower()
    assert "exclude_service" in marker
    assert osm_module.SERVICE_HIGHWAY == "service"


# ---------------------------------------------------------------------------
# 5. Determinism and the emitter contract
# ---------------------------------------------------------------------------


def test_two_builds_of_a_merged_network_are_byte_identical(tmp_path: Path):
    """Determinism has to be re-proved for the paths this unit added: a union-find
    over a hash-derived candidate set is exactly where run-to-run order leaks in.

    The fixture exercises both new paths at once — a node merge AND a cul-de-sac
    U-turn — so a nondeterministic merge or U-turn ordering shows up as
    different bytes.
    """
    text = _osm_text(
        _FRAME + [(6, LAT0 + 0.001, LON0 + 0.001), (7, LAT0, LON0 + NEAR_TOUCH_LON)],
        [
            (101, [1, 2], {"highway": "secondary", "name": "Main St"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (103, [2, 6], {"highway": "residential", "name": "Dead End Rd"}),
            (104, [7, 3], {"highway": "secondary", "name": "East Arm"}),
        ],
    )
    path = _write(tmp_path, "det.osm", text)
    first = to_xml_text(build_draft(parse_osm_file(path), name="det"))
    second = to_xml_text(build_draft(parse_osm_file(path), name="det"))
    assert first == second
    draft = build_draft(parse_osm_file(path), name="det")
    assert len(_u_turns(draft)) == 1
    assert draft.metadata["connectivity"]["node_merge"]["nodes_merged"] == 2


def test_connector_ids_are_unique_and_ordered_across_all_three_passes(tmp_path: Path):
    """Junction connectors, cul-de-sac U-turns and last-resort U-turns are built
    in three passes off one counter. A collision would silently overwrite a link
    in the emitted file."""
    draft = _draft(tmp_path, cul_de_sac_osm(), "cds")
    ids = [connector.id for connector in draft.connectors]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids)
    assert set(ids).isdisjoint({link.id for link in draft.links})


def test_the_u_turn_connector_survives_the_repos_own_parser(tmp_path: Path):
    """The emitter invariant a live LoadNet established must not be weakened:
    every connector carries endpoint elements plus one ``<lane>`` per connector
    lane with a ``<fromLanes>``/``<toLanes>`` object reference.

    Read back through ``inpx_parser`` (the same oracle the sibling suite uses)
    the U-turn is a connector like any other.
    """
    draft = _draft(tmp_path, cul_de_sac_osm(), "cds")
    path = emit_inpx(draft, tmp_path / "cds.inpx")
    parsed = {link["id"]: link for link in parse_network(str(path))["links"]}
    assert len(parsed) == len(draft.links) + len(draft.connectors)

    connector = _u_turns(draft)[0]
    got = parsed[str(connector.id)]
    assert got["is_connector"] is True
    assert got["from_link"] == str(connector.from_link)
    assert got["to_link"] == str(connector.to_link)
    assert got["from_pos"] == pytest.approx(connector.from_pos_m, abs=1e-3)
    assert got["to_pos"] == pytest.approx(0.0)

    text = path.read_text(encoding="utf-8")
    assert f'<objectRef key="{connector.from_link} 1" />' in text
    assert f'<objectRef key="{connector.to_link} 1" />' in text
    # The name is written verbatim (XML-escaped): the "->" of a connector name
    # becomes "-&gt;", which is what a reader diffing against a Vissim save sees.
    assert escape(connector.name, {'"': "&quot;"}) in text

    # The differ — the sibling suite's oracle — reads the file back as a network
    # with one more connector than junctions alone would give.
    topology, findings = diff_module.load_topology(path)
    assert topology is not None and [f.code for f in findings] == []
    assert str(connector.id) in topology.connector_ids


def test_the_report_states_the_tolerance_and_the_dead_end_counts(tmp_path: Path):
    """A number a reader cannot find is a number nobody checks."""
    draft = _draft(tmp_path, cul_de_sac_osm(), "cds")
    for language in ("ko", "en", "ja"):
        rendered = draft.render_compliance(language)
        assert "Table 5-7" in rendered
        assert "cul-de-sac U-turn connectors emitted: 1" in rendered
        assert conn.DEAD_END_MODEL_BOUNDARY in rendered
    assert any(a.id == "NODE_MERGE_TOLERANCE_M" for a in draft.assumptions)
    assert any(a.id == "BOUNDARY_BAND_M" for a in draft.assumptions)


# ---------------------------------------------------------------------------
# 6. The probe
# ---------------------------------------------------------------------------


def _run_probe(*args: str) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(PROBE), *args],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT / "backend"),
    )
    return result.returncode, result.stdout


def test_the_probe_reports_zero_cul_de_sac_gaps_on_a_repaired_network(tmp_path: Path):
    """The measurement tool and the generator must agree, or one of them is lying."""
    osm_path = _write(tmp_path, "cds.osm", cul_de_sac_osm())
    inpx = emit_inpx(build_draft(parse_osm_file(osm_path), name="cds"), tmp_path / "cds.inpx")
    code, out = _run_probe("--inpx", str(inpx), "--osm", str(osm_path), "--json")
    assert code == 0, out
    payload = json.loads(out)
    assert payload["by_cause"].get("cul_de_sac_u_turn_missing", 0) == 0
    assert payload["connectivity"]["u_turn_connectors_emitted"] == 1
    assert payload["file_matches_regeneration"] is True
    # Only the boundary terminals are left, and those are correct.
    assert payload["dangling_ends"] == payload["by_cause"]["model_boundary_correct"]


def test_the_probe_says_so_when_the_file_and_the_osm_disagree(tmp_path: Path):
    """``--osm`` attributes causes by REGENERATING. If the given ``.inpx`` is a
    different network, the causes describe the regeneration and not the file —
    a measurement that reported them silently under the file's name would be
    exactly the confident wrong number this repository exists to catch."""
    osm_path = _write(tmp_path, "cds.osm", cul_de_sac_osm())
    other = emit_inpx(
        build_draft(parse_osm_file(_write(tmp_path, "near.osm", near_touch_osm())), name="near"),
        tmp_path / "other.inpx",
    )
    code, out = _run_probe("--inpx", str(other), "--osm", str(osm_path), "--json")
    assert code == 0, out
    payload = json.loads(out)
    assert payload["file_matches_regeneration"] is False
    assert "drop --osm" in payload["warning"]


def test_the_probe_counts_a_connector_that_goes_missing(tmp_path: Path):
    """The check that the probe can fail. A measurement that cannot report a bad
    number is not a measurement.

    One connector is deleted from the emitted file and the ``.inpx``-only mode —
    the one CI can run without the source ``.osm`` — must see one more dangling
    end than before.
    """
    osm_path = _write(tmp_path, "cds.osm", cul_de_sac_osm())
    draft = build_draft(parse_osm_file(osm_path), name="cds")
    inpx = emit_inpx(draft, tmp_path / "cds.inpx")
    code, out = _run_probe("--inpx", str(inpx), "--json")
    assert code == 0, out
    healthy = json.loads(out)["dangling_ends"]

    victim = _u_turns(draft)[0]
    text = inpx.read_text(encoding="utf-8")
    start = text.index(escape(victim.name, {'"': "&quot;"}))
    open_tag = text.rindex("<link ", 0, start)
    close_tag = text.index("</link>", start) + len("</link>")
    broken = tmp_path / "broken.inpx"
    broken.write_text(text[:open_tag] + text[close_tag:], encoding="utf-8", newline="")

    code, out = _run_probe("--inpx", str(broken), "--json")
    assert code == 0, out
    payload = json.loads(out)
    assert payload["dangling_ends"] == healthy + 1
    assert payload["by_cause"]["cul_de_sac_u_turn_missing"] == 1


def test_the_probe_json_is_utf8_wherever_it_runs(tmp_path: Path):
    """A report that only parses on the machine that wrote it is not a report.

    ``--json`` writes ``ensure_ascii=False``, so the payload carries the em
    dashes in ``TOLERANCE_BASIS`` and in every refusal detail. Those are
    cp1252-encodable, so on a Windows console ``errors="replace"`` never fires
    and the redirected file comes out as well-formed JSON that is simply not
    UTF-8 — and a consumer doing ``json.load(open(path, encoding="utf-8"))``,
    such as a ``--baseline-json`` diff carried between this project's two dev
    boxes, gets a ``UnicodeDecodeError`` rather than a number.

    Redirected to a FILE, not a pipe, because that is the path that broke: it is
    where the console's own encoding is inherited.
    """
    osm_path = _write(tmp_path, "near.osm", near_touch_osm())
    inpx = emit_inpx(build_draft(parse_osm_file(osm_path), name="near"), tmp_path / "near.inpx")
    out = tmp_path / "probe.json"
    with out.open("wb") as handle:
        result = subprocess.run(
            [sys.executable, str(PROBE), "--inpx", str(inpx), "--osm", str(osm_path), "--json"],
            stdout=handle,
            stderr=subprocess.PIPE,
            cwd=str(REPO_ROOT / "backend"),
        )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")

    raw = out.read_bytes()
    payload = json.loads(raw.decode("utf-8"))  # the assertion: it MUST decode
    assert payload["connectivity"]["node_merge"]["basis"]
    assert "—" in raw.decode("utf-8"), "the fixture must actually carry a non-ASCII char"

    # …and the baseline path a CI diff would take round-trips it.
    code, _out = _run_probe("--inpx", str(inpx), "--baseline-json", str(out))
    assert code == 0


def test_the_probe_reports_the_generators_own_census_not_its_own_guess(tmp_path: Path):
    """The probe and the generator cannot disagree, because only one classifies.

    Every cause the probe prints comes from
    ``metadata['connectivity']['dangling_approaches']``. The probe used to carry
    a private fallback for the cases ``dead_ends()`` could not see and gave them
    confident names; on the real network that made 8 approaches visible ONLY to
    this script — no reason code, no compliance line, no ``genesis.json`` entry.
    """
    osm_path = _write(tmp_path, "trap.osm", one_way_trap_osm())
    draft = build_draft(parse_osm_file(osm_path), name="trap")
    inpx = emit_inpx(draft, tmp_path / "trap.inpx")

    code, out = _run_probe("--inpx", str(inpx), "--osm", str(osm_path), "--json")
    assert code == 0, out
    payload = json.loads(out)
    generator = draft.metadata["connectivity"]["dangling_approaches_by_cause"]
    assert payload["by_cause"] == generator, (payload["by_cause"], generator)
    assert payload["dangling_ends"] == sum(generator.values())
    assert payload["by_cause"][conn.DANGLING_ONE_WAY] >= 2

    # The tool's cause vocabulary IS the library's, not a second copy of it.
    import importlib.util

    spec = importlib.util.spec_from_file_location("probe_under_test", PROBE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.CAUSE_ALL_MOVEMENTS_U is conn.DANGLING_ALL_MOVEMENTS_U
    assert module.CAUSE_NO_EXIT_AT_JUNCTION is conn.DANGLING_NO_EXIT_AT_JUNCTION
    assert module.BENIGN_CAUSES is conn.BENIGN_DANGLING_CAUSES


def test_the_probe_refuses_a_file_that_is_not_there(tmp_path: Path):
    code, _out = _run_probe("--inpx", str(tmp_path / "nope.inpx"))
    assert code == 2


def test_a_segment_too_short_to_emit_is_named_in_the_report_never_dropped_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A dropped link must leave a trace. Nobody can notice an absence.

    ``build_draft`` skips a way-segment shorter than ``MIN_LINK_LENGTH_M``. Both
    skips used to be bare ``continue`` statements, and the cost was measured on
    2026-08-10: **40 of the generated Murdoch network's 225 dangling ends** were
    segments that had vanished exactly here — including an arm of Murdoch Drive's
    roundabout, the arterial the whole study is about — while every report said
    the network was fine. The node merge in this unit cut that population from 58
    segments to 9; it did not make the remaining 9 visible, which is what this
    test is for.

    **Why the threshold is monkeypatched rather than the geometry made tiny.**
    At the shipped 2.0 m, this branch is nearly unreachable by construction: any
    segment that short has endpoints inside ``NODE_MERGE_TOLERANCE_M`` (7.0 m),
    so the merge collapses it FIRST and names it ``zero_length_after_dedup‑
    lication``. The length check is only reached when a merge was REFUSED —
    which is exactly why the real network still hits it 9 times against 61
    refused merges. Raising the threshold reaches the same branch without
    fabricating a grade separation to defeat the merge, and what is under test is
    the RECORDING, not the constant.
    """
    monkeypatch.setattr("services.genesis.draft.MIN_LINK_LENGTH_M", 200.0)

    # Way 103 runs node 2 -> node 6: ~94.4 m east and ~110.9 m north of the
    # origin, so ~146 m — under the patched 200 m, while the 283 m frame ways
    # stay well over it even after their setbacks are trimmed.
    draft = _draft(tmp_path, cul_de_sac_osm(), "shortseg")

    assert not [link for link in draft.links if link.osm_way_id == 103]
    assert [link for link in draft.links if link.osm_way_id == 101], "frame must survive"

    skips = [s for s in draft.skipped_ways if s["reason"].startswith("segment_too_short")]
    assert len(skips) == 1, draft.skipped_ways
    assert skips[0]["way_id"] == 103
    # The detail carries the measured length AND the threshold it failed, so a
    # reader can tell "a 1 m stub" from "the setback ate a real road".
    assert "MIN_LINK_LENGTH_M" in skips[0]["detail"]
    assert "146" in skips[0]["detail"] or "145" in skips[0]["detail"]

    # And it reaches the human-facing report, not just the JSON.
    assert "segment_too_short" in draft.render_compliance("en")


def test_the_setback_trim_drop_is_named_apart_from_the_plain_too_short_drop():
    """Two reasons, deliberately not one.

    ``segment_too_short`` is a genuinely tiny piece of road. ``segment_too_short
    _after_setback_trim`` is a segment the §5.3.1 intersection setback consumed —
    the generator's OWN doing. Collapsing them into one reason would hide the
    second behind the first, and it is the second that means "the setback is
    eating real geometry", which is a generator bug rather than an OSM quirk.
    """
    way = type("W", (), {"id": 999, "highway": "residential"})()

    plain = _segment_skip(way, 0, 1.25, trimmed=False)
    trimmed = _segment_skip(way, 1, 1.75, trimmed=True)

    assert plain["reason"] == "segment_too_short"
    assert trimmed["reason"] == "segment_too_short_after_setback_trim"
    assert plain["reason"] != trimmed["reason"]
    assert "setback" in trimmed["detail"] and "setback" not in plain["detail"]
    # Same shape as every other skipped_ways entry, so the report's group-by
    # rendering needs no special case for these.
    assert set(plain) == {"way_id", "reason", "highway", "detail"}


# ---------------------------------------------------------------------------
# 7. Isolated zones — the fault a census of link ends structurally cannot see
#
# Every link of the pockets below has a connector in AND a connector out, so
# §2b's dangling-end census scores all of them healthy. The question that finds
# them is a different one — can a vehicle entering the model ever GET here, and
# can one here ever get out? — and on the real Murdoch extract the two questions
# disagreed by 150 links in 8 pockets, 7.6 % of the network.
#
# The answer is exclusion, never a connector: each cause below is a statement
# about the SOURCE (the access road is a footway, or is absent, or left through
# the extract's edge), and adding a connector would invent a road. The one cause
# that is NOT about the source — the pocket the source connects and this
# generator does not — is never excluded, because a pass that can delete its own
# bugs is worse than no pass at all.
# ---------------------------------------------------------------------------


def pocket_osm(*, link_class: str = "footway", attach: bool = False) -> str:
    """A crossroads plus a pocket of two residential streets it cannot reach.

    The crossroads is the ``_FRAME`` (Main St 1-2-3, First Ave 4-2-5). The
    pocket is way 301 (node 30 to 31) and way 302 (node 31 to 32); the two share
    node 31, so the pocket is internally connected and — once §1's cul-de-sac
    U-turns are added at nodes 30 and 32 — not one of its four links is a
    dangling end.

    Node 30 sits at (LAT0 + 0.0015, LON0 + 0.0015) = (141.6 m east, 166.3 m
    north). The extent rectangle is +/-283.3 m by +/-221.8 m, so the whole
    pocket is strictly INSIDE the model and no part of it is a boundary
    terminal.

    ``link_class`` is the class of way 303, which runs from the crossroads
    (node 5) to the pocket (node 30):

    * ``"footway"`` — the corridor exists for a pedestrian and not for a car;
    * ``""`` — way 303 is omitted, so nothing at all joins the pocket.

    ``attach=True`` instead makes way 303 a ``residential`` carriageway, i.e.
    the pocket IS reachable and there is nothing to find.
    """
    ways = [
        (101, [1, 2, 3], {"highway": "secondary", "name": "Main St", "lanes": "4"}),
        (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
        (301, [30, 31], {"highway": "residential", "name": "Pocket Way"}),
        (302, [31, 32], {"highway": "residential", "name": "Pocket Close"}),
    ]
    if attach:
        ways.append((303, [5, 30], {"highway": "residential", "name": "Access Rd"}))
    elif link_class:
        ways.append((303, [5, 30], {"highway": link_class}))
    return _osm_text(
        _FRAME
        + [
            (30, LAT0 + 0.0015, LON0 + 0.0015),
            (31, LAT0 + 0.0015, LON0 + 0.002),
            (32, LAT0 + 0.001, LON0 + 0.002),
        ],
        ways,
    )


def _way_ids_built(draft) -> set[int]:
    return {link.osm_way_id for link in draft.links}


def test_a_pocket_whose_only_access_is_a_footway_is_excluded_and_the_class_named(
    tmp_path: Path,
):
    """The Murdoch finding in miniature — and the reason must name the footway.

    Ways 301 and 302 build 4 links (two two-way streets), every one of which has
    both an incoming and an outgoing connector, so the dangling census sees
    nothing wrong. Nothing can drive in: the only thing joining the pocket to
    the crossroads is ``highway=footway``, which is not in the drivable
    allowlist.

    "Isolated" alone is not an actionable finding; "isolated, and your base map
    connects it by footpath" is. So the class is asserted, not just the count.
    """
    draft = build_draft(
        parse_osm_file(_write(tmp_path, "pocket.osm", pocket_osm())), name="pocket"
    )

    zones = draft.metadata["connectivity"]["excluded_zones"]
    assert len(zones) == 1
    assert zones[0]["reason"] == conn.ZONE_NON_DRIVABLE_ACCESS
    assert "highway=footway" in zones[0]["detail"]
    assert zones[0]["links"] == 4 and zones[0]["ways"] == 2
    assert zones[0]["way_ids"] == [301, 302]
    assert sorted(zones[0]["street_names"]) == ["Pocket Close", "Pocket Way"]
    # Removed from the network, not merely reported.
    assert _way_ids_built(draft) == {101, 102}


def test_removing_a_pocket_leaves_the_rest_of_the_network_exactly_as_it_was(
    tmp_path: Path,
):
    """The pocket goes; Main St and First Ave do not notice.

    Compared against the identical file with the pocket ways never present, so
    the claim is "the same network", not "a network of the same size".
    """
    with_pocket = build_draft(
        parse_osm_file(_write(tmp_path, "pocket.osm", pocket_osm())), name="n"
    )
    without = build_draft(
        parse_osm_file(
            _write(
                tmp_path,
                "plain.osm",
                _osm_text(
                    _FRAME,
                    [
                        (
                            101,
                            [1, 2, 3],
                            {"highway": "secondary", "name": "Main St", "lanes": "4"},
                        ),
                        (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
                    ],
                ),
            )
        ),
        name="n",
    )

    assert [l.points for l in with_pocket.links] == [l.points for l in without.links]
    assert [
        (c.from_link, c.to_link, c.from_lanes, c.to_lanes) for c in with_pocket.connectors
    ] == [(c.from_link, c.to_link, c.from_lanes, c.to_lanes) for c in without.connectors]


def test_a_pocket_nothing_in_the_file_touches_is_excluded_as_severed_in_the_source(
    tmp_path: Path,
):
    """No footway either — the severance is unqualified, and says so.

    The two reasons are kept apart because they are different messages to a
    client: "your base map joins this by footpath" is a mapping decision to
    review, "nothing joins this at all" is missing data.
    """
    draft = build_draft(
        parse_osm_file(_write(tmp_path, "severed.osm", pocket_osm(link_class=""))),
        name="severed",
    )

    zones = draft.metadata["connectivity"]["excluded_zones"]
    assert len(zones) == 1
    assert zones[0]["reason"] == conn.ZONE_SEVERED_IN_SOURCE
    assert "not so much as a footway" in zones[0]["detail"]
    assert _way_ids_built(draft) == {101, 102}


def test_a_reachable_pocket_is_never_excluded(tmp_path: Path):
    """The counterfactual: identical geometry, access road drivable.

    Without this, the two tests above would pass just as well for a pass that
    deleted every pocket it could find.
    """
    draft = build_draft(
        parse_osm_file(_write(tmp_path, "attached.osm", pocket_osm(attach=True))),
        name="attached",
    )

    assert draft.metadata["connectivity"]["excluded_zones"] == []
    assert _way_ids_built(draft) == {101, 102, 301, 302, 303}


def test_every_excluded_way_is_a_named_skip_and_a_compliance_item(tmp_path: Path):
    """An exclusion no report can name is indistinguishable from a lost road.

    Same doctrine as ``--exclude-service``: each way leaves a
    :data:`SKIP_ISOLATED_ZONE` behind carrying the reason, so the removal is
    visible in ``skipped_summary()``, in ``*.genesis.json`` and in the rendered
    §5 report — where a client who reads it can go and fix the base map.
    """
    draft = build_draft(
        parse_osm_file(_write(tmp_path, "pocket.osm", pocket_osm())), name="pocket"
    )

    skips = {
        s["way_id"]: s for s in draft.skipped_ways if s["reason"] == SKIP_ISOLATED_ZONE
    }
    assert sorted(skips) == [301, 302]
    assert conn.ZONE_NON_DRIVABLE_ACCESS in skips[301]["detail"]

    item = next(
        i for i in draft.compliance.items if i.rule_id == "5.3.3-isolated-zone-excluded"
    )
    assert item.state.value == "applied"
    assert item.evidence["excluded_links"] == 4
    assert item.evidence["retained_zones_this_generator_severed"] == 0
    rendered = draft.render_compliance("en")
    assert "Pocket Way" in rendered
    assert conn.ZONE_NON_DRIVABLE_ACCESS in rendered


def test_a_pocket_the_source_connects_is_reported_and_never_excluded(tmp_path: Path):
    """The guard on the whole pass: it may not delete this generator's own bugs.

    Way 303 IS a residential carriageway, sharing node 5 with First Ave and node
    30 with the pocket, so the source says these roads meet. Two turn
    restrictions then ban every movement across node 5 in both directions and
    the pocket becomes unreachable while remaining connected *in the file*.

    That is not a severance in the base map, so the zone stays in the network
    with its name on it and the compliance item turns VIOLATED. Excluding it
    would delete the evidence and let a gate go green over a movement that went
    missing on roads the source joins.
    """
    text = _osm_text(
        _FRAME
        + [
            (30, LAT0 + 0.0015, LON0 + 0.0015),
            (31, LAT0 + 0.0015, LON0 + 0.002),
        ],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St", "lanes": "4"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (303, [5, 30], {"highway": "residential", "name": "Access Rd"}),
            (301, [30, 31], {"highway": "residential", "name": "Pocket Way"}),
        ],
        relations=[
            (
                9001,
                [("way", 102, "from"), ("node", 5, "via"), ("way", 303, "to")],
                {"type": "restriction", "restriction": "no_left_turn"},
            ),
            (
                9002,
                [("way", 303, "from"), ("node", 5, "via"), ("way", 102, "to")],
                {"type": "restriction", "restriction": "no_left_turn"},
            ),
        ],
    )
    draft = build_draft(
        parse_osm_file(_write(tmp_path, "ourbug.osm", text)), name="ourbug"
    )

    assert draft.metadata["connectivity"]["excluded_zones"] == []
    retained = draft.metadata["connectivity"]["isolated_zones_retained"]
    assert len(retained) == 1
    assert retained[0]["reason"] == conn.ZONE_SEVERED_BY_THIS_GENERATOR
    assert "way 102" in retained[0]["detail"] or "way 303" in retained[0]["detail"]
    # Every road the source contains is still in the network.
    assert _way_ids_built(draft) == {101, 102, 301, 303}
    item = next(
        i for i in draft.compliance.items if i.rule_id == "5.3.3-isolated-zone-excluded"
    )
    assert item.state.value == "violated"
    assert item.evidence["retained_zones_this_generator_severed"] == 1


def test_isolated_zones_requires_both_unreachable_and_inescapable():
    """A pocket a vehicle CAN reach is never excluded, however badly it ends.

    Three cases, and the middle one is the whole reason the rule is an AND:

    1. ``1 -> 2``: link 1 is a network entry and link 2 an exit. An ordinary
       corridor; a rule that removed it would remove every model boundary.
    2. ``1 -> 2 -> 3 -> 2``: traffic enters at 1 and then circulates forever —
       no link reaches an exit, so every link is "trapped". It is still a road
       vehicles drive on, and it is named by the dangling-end census. Deleting
       it would delete a live network on the strength of half the test.
    3. ``1 -> 2 -> 1``: a closed cycle with no entry and no exit. Nothing can
       ever be here. This one, and only this one, is a zone.
    """
    link = lambda i: type("L", (), {"id": i, "osm_way_id": i, "name": f"L{i}"})()
    edge = lambda a, b: type("C", (), {"from_link": a, "to_link": b})()

    assert conn.isolated_zones([link(1), link(2)], [edge(1, 2)]) == []
    assert (
        conn.isolated_zones(
            [link(1), link(2), link(3)], [edge(1, 2), edge(2, 3), edge(3, 2)]
        )
        == []
    )
    assert conn.isolated_zones([link(1), link(2)], [edge(1, 2), edge(2, 1)]) == [(1, 2)]


def test_a_pocket_at_the_model_edge_is_excluded_as_truncated_not_as_severed(
    tmp_path: Path,
):
    """A pocket ON the extent is a road that leaves the window, not a broken map.

    The pocket here is a one-way loop — ways 301/302/303 running 30 to 31 to 32
    and back to 30, the shape of a car-park circulation ring. Every one of its
    three links has a connector in and a connector out, none of them is a dead
    end, and there is no entry and no exit, so nothing can ever be on it.

    A two-way stub at the edge is deliberately NOT this case: its outbound
    carriageway is a network exit and its inbound one an entry, which is exactly
    what a road leaving the study area should look like. It takes a loop to
    produce a boundary pocket that is genuinely inert.

    Node 30 sits at LON0 + 0.004, the east-most point of the kept geometry, so
    the pocket is part of the model's own edge — and the honest reason is that
    whatever joins it to the network is OUTSIDE the extract. That is a statement
    about where the study area was drawn, not a defect in anybody's data, which
    is why it does not share a reason code with the two above.

    Turn angles, hand-derived so the fixture cannot silently become a U-turn
    (which §5.3.3 would suppress, breaking the loop): at node 30 the movement
    turns 80.9 deg, at nodes 31 and 32 it turns 139.6 deg — all under
    UTURN_ANGLE_DEG = 150.
    """
    text = _osm_text(
        _FRAME
        + [
            (30, LAT0 + 0.0015, LON0 + 0.004),
            (31, LAT0 + 0.002, LON0 + 0.0035),
            (32, LAT0 + 0.001, LON0 + 0.0035),
        ],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St", "lanes": "4"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (301, [30, 31], {"highway": "service", "oneway": "yes"}),
            (302, [31, 32], {"highway": "service", "oneway": "yes"}),
            (303, [32, 30], {"highway": "service", "oneway": "yes"}),
        ],
    )
    draft = build_draft(parse_osm_file(_write(tmp_path, "edge.osm", text)), name="edge")

    zones = draft.metadata["connectivity"]["excluded_zones"]
    assert len(zones) == 1
    assert zones[0]["reason"] == conn.ZONE_TRUNCATED_AT_BOUNDARY
    assert zones[0]["links"] == 3
    assert _way_ids_built(draft) == {101, 102}


def test_when_the_removal_cap_is_exhausted_the_zones_stay_and_are_named(
    tmp_path: Path, monkeypatch
):
    """The fail-safe direction: give up LOUDLY and leave the network alone.

    Removing a component nothing can reach cannot isolate anything else, so one
    extra round is enough and the cap should never be hit. It exists so that
    claim is falsifiable rather than assumed — and if it ever is hit, the
    pockets stay in the file with a named gap on them and the R1.5 gate refuses,
    which is the right way round. A silent loop that quietly stopped converging
    is the failure mode this package keeps finding in other people's code.
    """
    monkeypatch.setattr(draft_module, "MAX_ISOLATION_ROUNDS", 1)
    draft = build_draft(
        parse_osm_file(_write(tmp_path, "pocket.osm", pocket_osm())), name="pocket"
    )

    connectivity = draft.metadata["connectivity"]
    assert connectivity["excluded_zones"] == []
    assert connectivity["excluded_zones_unresolved"] == draft_module.ISOLATION_ROUNDS_EXHAUSTED
    assert _way_ids_built(draft) == {101, 102, 301, 302}

    # "Named" is the load-bearing word in this test's title, and the first cut
    # of the cap branch failed it: a stuck zone landed in NEITHER ledger (not
    # excluded — the break ran before the extend; not retained — its reason was
    # excludable), so a pocket still physically in the file appeared in no
    # report at all. The adversarial pass caught it. The stuck zone must be in
    # `isolated_zones_retained` re-tagged with the cap reason, and the §5.3.3
    # compliance item must exist and be VIOLATED — a generator that cannot
    # finish its own cleanup must not grade itself APPLIED.
    stuck = connectivity["isolated_zones_retained"]
    assert len(stuck) == 1, stuck
    assert stuck[0]["reason"] == draft_module.ISOLATION_ROUNDS_EXHAUSTED
    assert stuck[0]["street_names"], "the stuck pocket must carry its streets"

    item = next(
        i for i in draft.compliance.items if i.rule_id == "5.3.3-isolated-zone-excluded"
    )
    assert item.state.name == "VIOLATED"
    assert any("[" + draft_module.ISOLATION_ROUNDS_EXHAUSTED + "]" in s for s in item.subjects)


def test_the_compliance_item_names_every_street_of_a_pocket_not_one(tmp_path: Path):
    """"Names every pocket by street" must mean ALL of them.

    The 96-link Murdoch pocket spans a dozen named streets; a subject reading
    "Scandrett Way +95 link(s)" names one and buries eleven. A client fixing
    their base map works street by street, so the subject carries the whole
    list. ``pocket_osm``'s pocket is Pocket Way + Pocket Close — two distinct
    names, and both must appear in the SAME subject line.
    """
    draft = build_draft(
        parse_osm_file(_write(tmp_path, "pocket.osm", pocket_osm())), name="pocket"
    )

    item = next(
        i for i in draft.compliance.items if i.rule_id == "5.3.3-isolated-zone-excluded"
    )
    assert len(item.subjects) == 1, item.subjects
    subject = item.subjects[0]
    assert "Pocket Way" in subject
    assert "Pocket Close" in subject


def test_a_way_with_no_highway_tag_is_reported_as_such_not_as_an_empty_string(
    tmp_path: Path,
):
    """``highway=`` reads as a bug in a report; "no highway tag" reads as data.

    Way 303 carries a ``barrier`` and no ``highway``, so it is skipped as
    ``no_highway_tag`` — and it is still the only thing in the file touching the
    pocket, so it is still the reason the pocket is unreachable.
    """
    text = _osm_text(
        _FRAME
        + [
            (30, LAT0 + 0.0015, LON0 + 0.0015),
            (31, LAT0 + 0.0015, LON0 + 0.002),
        ],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St", "lanes": "4"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (303, [5, 30], {"barrier": "hedge"}),
            (301, [30, 31], {"highway": "residential", "name": "Pocket Way"}),
        ],
    )
    draft = build_draft(parse_osm_file(_write(tmp_path, "hedge.osm", text)), name="hedge")

    zones = draft.metadata["connectivity"]["excluded_zones"]
    assert len(zones) == 1
    assert zones[0]["reason"] == conn.ZONE_NON_DRIVABLE_ACCESS
    assert "highway=no highway tag" in zones[0]["detail"]


def test_without_ways_prunes_the_nodes_that_leave_with_them(tmp_path: Path):
    """The extract's extent is the projection origin, and the origin is every
    coordinate in the emitted file.

    So a removal that left the departed geometry's nodes behind would shrink the
    network while keeping the map's old centre — every link in the file would
    move, for a road that is no longer in it. Asserted on ``bounds()`` because
    that is the value ``build_draft`` actually consumes.
    """
    text = _osm_text(
        _FRAME + [(40, LAT0 + 0.010, LON0 + 0.010), (41, LAT0 + 0.011, LON0 + 0.011)],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St", "lanes": "4"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (401, [40, 41], {"highway": "residential", "name": "Far Rd"}),
        ],
    )
    extract = parse_osm_file(_write(tmp_path, "far.osm", text))
    assert extract.bounds()[3] == pytest.approx(LON0 + 0.011)

    reduced = extract.without_ways(
        [401], reason=SKIP_ISOLATED_ZONE, detail_for=lambda way_id: f"way {way_id}"
    )

    assert set(reduced.nodes) == {1, 2, 3, 4, 5}
    assert reduced.bounds()[3] == pytest.approx(LON0 + 0.003)
    # The original is untouched: the caller keeps it to diagnose against.
    assert set(extract.nodes) == {1, 2, 3, 4, 5, 40, 41}
    assert [s["way_id"] for s in (w.as_dict() for w in reduced.skipped)].count(401) == 1


def test_the_r15_gate_finds_no_isolated_zone_in_what_the_generator_now_emits(
    tmp_path: Path,
):
    """The two definitions must agree, and only an end-to-end run proves it.

    ``connectivity.isolated_zones`` runs on the draft; ``gate._check_v3`` runs on
    the finished ``.inpx``. They are deliberately separate implementations — the
    generator may not import its own judge — so nothing but this test stops them
    drifting apart. Emitting the footway-pocket fixture and running the real gate
    over the real file is the only version of the assertion that cannot be
    satisfied by two functions agreeing about nothing.
    """
    from services.genesis.gate import run_gate

    source = _write(tmp_path, "pocket.osm", pocket_osm())
    draft = build_draft(parse_osm_file(source), name="pocket")
    path = emit_inpx(draft, tmp_path / "pocket.inpx")

    v3 = run_gate(path).check("V3")
    assert [f.code for f in v3.findings if f.code.endswith("_zone")] == []
    assert v3.evidence["links_in_a_broken_zone"] == 0
    assert v3.evidence["plain_links"] == len(draft.links)


# ---------------------------------------------------------------------------
# 8. The requested bbox: the study area the download was asked for
#
# Overpass answers a bbox query with WHOLE ways, so the geometry that comes back
# overhangs the request — 100 m to 2300 m, measured on this project's own
# Daejeon fetches. Before this unit the hull of that geometry WAS the model
# boundary, so it was set by the longest tail rather than by the study area, and
# every legitimate boundary cut sat hundreds of metres inside it: outside the
# 1 m band, classified as an interior dead end, and refused by the R1.5 gate's
# V3 as a silent departure point. Every bbox extraction the generator produced
# was refused for that one reason.
#
# The fix is the RECTANGLE, never the band (see BOUNDARY_BAND_M's own note): the
# requested bbox becomes the boundary, and every overhanging tail is cut off AT
# it with a node synthesized exactly on the line, so a boundary terminal is
# 0.00 m from the edge by construction.
#
# The fixture frame below is the same Perth frame the rest of this file uses.
# 1 degree of longitude here = 94 406 m and 1 degree of latitude = 110 887 m, so
# every distance in these docstrings is checkable with a calculator.
# ---------------------------------------------------------------------------

#: The study rectangle these tests ask for: +/-0.002 deg lat (+/-221.8 m) and
#: +/-0.002 deg lon (+/-188.8 m) about the frame's centre.
BBOX = (LAT0 - 0.002, LON0 - 0.002, LAT0 + 0.002, LON0 + 0.002)


def overhang_osm() -> str:
    """A crossroads inside the study area whose east-west arm runs far outside it.

    * way 201 "Main St", nodes ``10-1-2-3-11``. Nodes 10 and 11 sit at
      ``LON0 -/+ 0.005`` = **472.0 m** from the centre, i.e. **283.2 m** outside
      the +/-188.8 m rectangle. Nodes 1, 2, 3 are inside it.
    * way 202 "First Ave", nodes ``4-2-5``, entirely inside: 4 and 5 sit
      110.9 m north/south of the centre and therefore 110.9 m from the nearest
      edge of the rectangle — nowhere near the 1.0 m band, so they are genuine
      interior cul-de-sacs and MUST keep their U-turns.

    So the trim has exactly two tails to cut and two dead ends it must not
    touch, which is the pair of claims that make this a test of the rule rather
    than of the geometry.
    """
    return _osm_text(
        [
            (10, LAT0, LON0 - 0.005),
            (1, LAT0, LON0 - 0.001),
            (2, LAT0, LON0),
            (3, LAT0, LON0 + 0.001),
            (11, LAT0, LON0 + 0.005),
            (4, LAT0 - 0.001, LON0),
            (5, LAT0 + 0.001, LON0),
        ],
        [
            (201, [10, 1, 2, 3, 11], {"highway": "secondary", "name": "Main St"}),
            (202, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
        ],
    )


def _trims_of(extract, way_id: int) -> list[dict]:
    return [t.as_dict() for t in extract.way_trims if t.way_id == way_id]


def test_the_requested_bbox_beats_declared_bounds_beats_the_hull(tmp_path: Path):
    """The three-rung ladder, on ONE file, read off node 3's classification.

    The frame's node 3 sits at ``LON0 + 0.003`` = 283.2 m east of the centre.

    * **hull** — node 3 IS the eastmost geometry, so it is ON the boundary and
      reads ``model_boundary``. (That is the rung-3 test at the top of this
      file, restated here so all three rungs are compared side by side.)
    * **declared <bounds>** of +/-0.01 deg — node 3 is 660 m inside it, so it
      reads ``cul_de_sac_u_turn``. That is the rung-2 test's own claim.
    * **requested bbox** equal to the frame — node 3 is back exactly ON the east
      edge and reads ``model_boundary`` again, WITH the wide ``<bounds>`` still
      in the file. Only a ladder that believes the request over the declaration
      can produce that, which is why the third case keeps the second's
      ``<bounds>`` rather than dropping it.
    """
    wide = (LAT0 - 0.01, LON0 - 0.01, LAT0 + 0.01, LON0 + 0.01)
    frame = (LAT0 - 0.002, LON0 - 0.003, LAT0 + 0.002, LON0 + 0.003)
    ways = [
        (101, [1, 2, 3], {"highway": "secondary", "name": "Main St"}),
        (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
        (103, [2, 6], {"highway": "residential", "name": "Dead End Rd"}),
    ]
    nodes = _FRAME + [(6, LAT0 + 0.001, LON0 + 0.001)]

    hull = parse_osm_file(_write(tmp_path, "hull.osm", _osm_text(nodes, ways)))
    declared = parse_osm_file(
        _write(tmp_path, "declared.osm", _osm_text(nodes, ways, bounds=wide))
    )
    requested = parse_osm_file(
        _write(tmp_path, "requested.osm", _osm_text(nodes, ways, bounds=wide)),
        requested_bbox=frame,
    )

    assert conn.boundary_source(hull) == conn.BOUNDARY_SOURCE_HULL
    assert conn.boundary_source(declared) == conn.BOUNDARY_SOURCE_DECLARED_BOUNDS
    assert conn.boundary_source(requested) == conn.BOUNDARY_SOURCE_REQUESTED_BBOX

    def reason_at_node_3(extract) -> str:
        ends = {e.node_id: e for e in conn.dead_ends(conn.merge_junction_nodes(extract)[0])}
        return ends[3].reason

    assert reason_at_node_3(hull) == conn.DEAD_END_MODEL_BOUNDARY
    assert reason_at_node_3(declared) == conn.DEAD_END_U_TURN
    assert reason_at_node_3(requested) == conn.DEAD_END_MODEL_BOUNDARY


def test_an_overhanging_tail_is_cut_at_the_requested_boundary(tmp_path: Path):
    """The measured failure, and its fix, in one assertion chain.

    Way 201's two tails are cut, one node is synthesized on each edge at
    ``LON0 -/+ 0.002`` exactly, and the resulting terminals are **0.00 m** from
    the boundary — inside the 1.0 m band by construction rather than by luck.
    Both therefore read ``model_boundary`` and get NO U-turn, which is the whole
    point: a U-turn there would make the study edge a reflecting wall.

    The 283.2 m of overhang per side is the number the old hull boundary was
    being set by.
    """
    from services.genesis.osm import trim_ways_to_requested_bbox

    source = _write(tmp_path, "overhang.osm", overhang_osm())
    trimmed = trim_ways_to_requested_bbox(parse_osm_file(source, requested_bbox=BBOX))

    way = next(w for w in trimmed.ways if w.id == 201)
    assert way.node_ids[1:-1] == (1, 2, 3), "the inside nodes are untouched"
    west, east = trimmed.nodes[way.node_ids[0]], trimmed.nodes[way.node_ids[-1]]
    assert (west.lat, west.lon) == (LAT0, LON0 - 0.002)
    assert (east.lat, east.lon) == (LAT0, LON0 + 0.002)
    assert way.node_ids[0] not in {10, 11} and way.node_ids[-1] not in {10, 11}

    cuts = _trims_of(trimmed, 201)
    assert [c["reason"] for c in cuts] == [osm_module.TRIM_TAIL_CUT] * 2
    assert [c["end"] for c in cuts] == ["start", "end"]
    assert all(c["nodes_cut"] == 1 for c in cuts)
    assert all(c["metres_cut"] == pytest.approx(283.2, abs=1.0) for c in cuts)

    ends = {e.node_id: e for e in conn.dead_ends(conn.merge_junction_nodes(trimmed)[0])}
    for node_id in (way.node_ids[0], way.node_ids[-1]):
        assert ends[node_id].reason == conn.DEAD_END_MODEL_BOUNDARY
        assert ends[node_id].emit_u_turn is False
        assert "0.00 m from the model boundary" in ends[node_id].detail
        assert conn.BOUNDARY_SOURCE_REQUESTED_BBOX in ends[node_id].detail

    # ...and the two genuine interior cul-de-sacs are untouched by all of it.
    assert ends[4].reason == conn.DEAD_END_U_TURN
    assert ends[5].reason == conn.DEAD_END_U_TURN

    draft = build_draft(parse_osm_file(source, requested_bbox=BBOX), name="overhang")
    boundary_links = {
        link.id
        for link in draft.links
        if link.to_node_osm_id in (way.node_ids[0], way.node_ids[-1])
    }
    leaving = {c.from_link for c in draft.connectors}
    assert boundary_links and not (boundary_links & leaving), (
        "a link ending on the requested boundary must have no connector leaving it"
    )


def test_an_interior_excursion_out_of_the_bbox_is_kept_whole_and_named(tmp_path: Path):
    """v1 scope, stated as a test rather than as a comment.

    Way 203 runs ``1 -> 2 -> 20 -> 3``: inside, inside, OUTSIDE, inside. Both
    TERMINALS are already inside, so there is no tail to cut; the only thing
    outside is an interior excursion. Cutting that would either delete node 20's
    leg — a road the study area really does contain on both sides of — or split
    one OSM way into two ids, and a silent partial edit of a way is exactly what
    this pass exists not to do. So the way is kept ENTIRE, and the fact is
    recorded by name, because absence from the trim list would be
    indistinguishable from "nothing happened here".

    The sibling case — a re-entrant way whose TERMINALS hang outside — is
    ``test_a_way_that_leaves_and_re_enters_still_has_its_tails_cut`` in §9: those
    tails ARE cut, because a terminal outside the rectangle is one the boundary
    band cannot honestly judge.
    """
    from services.genesis.osm import trim_ways_to_requested_bbox

    text = _osm_text(
        [
            (1, LAT0, LON0 - 0.001),
            (2, LAT0, LON0),
            (20, LAT0 + 0.004, LON0),
            (3, LAT0, LON0 + 0.001),
            (4, LAT0 - 0.001, LON0),
        ],
        [
            (203, [1, 2, 20, 3], {"highway": "residential", "name": "Loop Rd"}),
            (204, [4, 2], {"highway": "residential", "name": "First Ave"}),
        ],
    )
    trimmed = trim_ways_to_requested_bbox(
        parse_osm_file(_write(tmp_path, "reenter.osm", text), requested_bbox=BBOX)
    )

    way = next(w for w in trimmed.ways if w.id == 203)
    assert way.node_ids == (1, 2, 20, 3), "kept whole, not cut and not split"
    assert 20 in trimmed.nodes
    record = _trims_of(trimmed, 203)
    assert [r["reason"] for r in record] == [osm_module.TRIM_KEPT_WHOLE_REENTRANT]
    assert record[0]["end"] == "whole"
    assert "re-enters" in record[0]["detail"]


def test_a_way_entirely_outside_the_requested_bbox_is_dropped_by_name(tmp_path: Path):
    """Two ways outside the study area, and they are NOT the same fact.

    * way 301 is 665 m north of the rectangle and never touches it. It came back
      only because Overpass returns whole ways, and it is dropped as
      ``entirely_outside_the_requested_bbox``.
    * way 302 has both of its nodes outside the rectangle but the segment
      between them cuts across a corner of it. It is REAL road inside the study
      area, and it is still dropped — under its OWN reason — because every
      terminal a trim could give it would be a terminal this generator
      invented, and a carriageway all of whose vertices are invented is the
      mirror image of one silently deleted. Measured on job d31961abd950:
      keeping such a way whole put a 144 m link entirely outside the study area
      into the network, joined to nothing, and the gate refused it as
      ``v3_orphan_link``.

    Both are NAMED skips, so both are counted in ``skipped_summary()`` and both
    reach the section 5 compliance report.
    """
    from services.genesis.osm import trim_ways_to_requested_bbox

    text = _osm_text(
        [
            (1, LAT0, LON0 - 0.001),
            (2, LAT0, LON0),
            (30, LAT0 + 0.006, LON0 - 0.001),
            (31, LAT0 + 0.006, LON0 + 0.001),
            # 40 -> 41 crosses the north-east corner of the rectangle without a
            # node inside it: 40 is north of the box, 41 is east of it, and the
            # straight line between them is inside the rectangle for the middle
            # half of its length (t in [0.25, 0.75] — a real chord, not a
            # single-point touch at the corner, which no float would agree on).
            (40, LAT0 + 0.0025, LON0 + 0.0005),
            (41, LAT0 + 0.0005, LON0 + 0.0025),
        ],
        [
            (201, [1, 2], {"highway": "secondary", "name": "Main St"}),
            (301, [30, 31], {"highway": "residential", "name": "Far Rd"}),
            (302, [40, 41], {"highway": "residential", "name": "Corner Rd"}),
        ],
    )
    trimmed = trim_ways_to_requested_bbox(
        parse_osm_file(_write(tmp_path, "outside.osm", text), requested_bbox=BBOX)
    )

    assert [w.id for w in trimmed.ways] == [201]
    by_way = {s.id: s.reason for s in trimmed.skipped}
    assert by_way[301] == osm_module.SKIP_OUTSIDE_REQUESTED_BBOX
    assert by_way[302] == osm_module.SKIP_ONLY_GRAZES_REQUESTED_BBOX
    assert trimmed.skipped_summary()[osm_module.SKIP_OUTSIDE_REQUESTED_BBOX] == 1
    # Their nodes leave with them: a node no surviving way references would
    # still size bounds(), and bounds() is the projection origin.
    assert set(trimmed.nodes) == {1, 2}


def test_every_trim_reaches_the_draft_metadata_and_the_compliance_report(
    tmp_path: Path,
):
    """A geometry edit a reader cannot find is the same to them as a silent one."""
    draft = build_draft(
        parse_osm_file(
            _write(tmp_path, "overhang.osm", overhang_osm()), requested_bbox=BBOX
        ),
        name="overhang",
    )
    boundary = draft.metadata["boundary"]
    assert boundary["source"] == conn.BOUNDARY_SOURCE_REQUESTED_BBOX
    assert boundary["requested_bbox_wgs84"] == {
        "min_lat": BBOX[0],
        "min_lon": BBOX[1],
        "max_lat": BBOX[2],
        "max_lon": BBOX[3],
    }
    assert boundary["way_trims_by_reason"] == {osm_module.TRIM_TAIL_CUT: 2}
    assert boundary["metres_cut_total"] == pytest.approx(566.4, abs=2.0)

    for language in ("ko", "en", "ja"):
        rendered = draft.render_compliance(language)
        assert conn.BOUNDARY_SOURCE_REQUESTED_BBOX in rendered
        assert osm_module.TRIM_TAIL_CUT in rendered
        assert "way 201" in rendered


def test_the_requested_bbox_survives_both_extract_reconstructions(tmp_path: Path):
    """The boundary must not MOVE between isolation rounds.

    ``merge_junction_nodes`` and ``without_ways`` each rebuild the extract field
    by field, and a field either of them forgets is gone for the rest of the
    build. Forgetting this one would drop the boundary back to the hull of the
    SHRUNKEN geometry — a different rectangle on every round, so the same
    terminal could be a boundary in round 0 and a cul-de-sac in round 1.
    """
    extract = parse_osm_file(
        _write(tmp_path, "overhang.osm", overhang_osm()), requested_bbox=BBOX
    )
    merged, _report = conn.merge_junction_nodes(extract)
    assert merged.requested_bbox == BBOX

    reduced = extract.without_ways(
        [202], reason=SKIP_ISOLATED_ZONE, detail_for=lambda way_id: f"way {way_id}"
    )
    assert reduced.requested_bbox == BBOX


def test_every_extract_reconstruction_carries_every_field_the_dataclass_declares(
    tmp_path: Path,
):
    """Mechanical, and deliberately not a list of the fields anyone remembered.

    All THREE reconstruction sites — ``connectivity.merge_junction_nodes``,
    ``osm.OsmExtract.without_ways`` and ``osm.trim_ways_to_requested_bbox`` —
    write a fresh ``OsmExtract`` out field by field, and there is no
    language-level reason any of them has to be complete. One was not:
    ``non_drivable_node_highways`` was dropped by the merge from the day the
    field was added, and it was harmless only by accident
    (``diagnose_isolated_zones`` reads the PRE-merge extract, so nobody ever
    consulted the empty map). ``requested_bbox`` would have been the second such
    field and would NOT have been harmless, and ``synthesized_node_ids`` the
    third — losing that one un-refuses the merge that welds a boundary marker
    into a junction.

    So this test asks the dataclass what its fields are, fails if the sentinel
    below does not set every one of them, and fails again if any transform loses
    one. A future field cannot be silently dropped without breaking this test
    twice.

    ``refusals`` is the one exemption, and it is structural rather than a
    convenience: all three transforms return the input UNCHANGED for a refused
    extract, so an extract carrying a refusal cannot exercise any of them. Its
    carry is asserted separately at the end.
    """
    from dataclasses import fields

    from services.genesis.osm import (
        OsmExtract,
        OsmRefusal,
        RestrictionRefusal,
        SkippedWay,
        TurnRestriction,
        WayTrim,
        trim_ways_to_requested_bbox,
    )

    text = _osm_text(
        _FRAME + [(7, LAT0, LON0 + NEAR_TOUCH_LON), (8, LAT0 + 0.001, LON0 + 0.002)],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (103, [7, 8], {"highway": "residential", "name": "Near Rd"}),
        ],
    )
    extract = parse_osm_file(_write(tmp_path, "sentinel.osm", text))

    sentinel = {
        "source": extract.source,
        "ways": extract.ways,
        "nodes": extract.nodes,
        "ways_seen": 7,
        "skipped": [SkippedWay(999, osm_module.SKIP_NO_HIGHWAY_TAG, "", "sentinel")],
        "refusals": [],
        "declared_bounds": (LAT0 - 0.01, LON0 - 0.01, LAT0 + 0.01, LON0 + 0.01),
        "restrictions": [TurnRestriction(9001, "no_left_turn", 101, 2, 102)],
        "restriction_refusals": [
            RestrictionRefusal(8001, osm_module.RESTRICTION_KIND_UNKNOWN, "sentinel")
        ],
        "non_drivable_node_ids": {7},
        "non_drivable_node_highways": {7: {"footway"}},
        "requested_bbox": (LAT0 - 0.02, LON0 - 0.02, LAT0 + 0.02, LON0 + 0.02),
        "way_trims": [
            WayTrim(101, "secondary", osm_module.TRIM_TAIL_CUT, "start", 1, 12.5, 555, "s")
        ],
        "synthesized_node_ids": frozenset({555}),
    }
    declared = {field.name for field in fields(OsmExtract)}
    assert set(sentinel) == declared, (
        "OsmExtract grew a field this test does not set — set it, then check that "
        "ALL THREE reconstruction sites carry it"
    )
    for name, value in sentinel.items():
        setattr(extract, name, value)

    merged, report = conn.merge_junction_nodes(extract)
    assert report.merges, "the fixture must actually merge, or nothing is proved"
    reduced = extract.without_ways(
        [103], reason=SKIP_ISOLATED_ZONE, detail_for=lambda way_id: f"way {way_id}"
    )
    # The requested bbox above is far wider than this fixture, so the trim cuts
    # nothing — which is exactly what this assertion wants: a transform that
    # changes nothing must still carry everything.
    trimmed = trim_ways_to_requested_bbox(extract)
    assert [w.node_ids for w in trimmed.ways] == [w.node_ids for w in extract.ways]

    for name in sorted(declared - {"refusals"}):
        assert getattr(merged, name), f"merge_junction_nodes dropped {name}"
        assert getattr(reduced, name), f"without_ways dropped {name}"
        assert getattr(trimmed, name), f"trim_ways_to_requested_bbox dropped {name}"

    extract.refusals = [OsmRefusal(osm_module.REFUSE_UNREADABLE, "sentinel")]
    assert (
        extract.without_ways(
            [103], reason=SKIP_ISOLATED_ZONE, detail_for=lambda way_id: ""
        ).refusals
        == extract.refusals
    )
    assert conn.merge_junction_nodes(extract)[0] is extract
    assert trim_ways_to_requested_bbox(extract) is extract


def test_the_node_merge_remaps_the_non_drivable_class_map_it_used_to_drop(
    tmp_path: Path,
):
    """The latent defect this unit killed on the way past, with its own test.

    Node 7 carries ``highway=footway`` and merges into junction node 2. The map
    must survive the merge AND move to the cluster's canonical id — the same
    remapping ``non_drivable_node_ids`` already got — because after the merge
    node 7 does not exist and a class map keyed on it answers no question. Two
    nodes that merge into one junction have their classes UNIONED: both footways
    really do touch the junction that now exists.
    """
    text = _osm_text(
        _FRAME + [(7, LAT0, LON0 + NEAR_TOUCH_LON), (8, LAT0 + 0.001, LON0 + 0.002)],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (103, [7, 8], {"highway": "residential", "name": "Near Rd"}),
        ],
    )
    extract = parse_osm_file(_write(tmp_path, "classes.osm", text))
    extract.non_drivable_node_ids = {2, 7}
    extract.non_drivable_node_highways = {2: {"path"}, 7: {"footway"}}

    merged, report = conn.merge_junction_nodes(extract)
    assert any(2 in merge.member_ids and 7 in merge.member_ids for merge in report.merges)
    assert merged.non_drivable_node_ids == {2}
    assert merged.non_drivable_node_highways == {2: {"footway", "path"}}


def test_two_builds_with_a_requested_bbox_are_byte_identical(tmp_path: Path):
    """Determinism, re-proved for the path the trim added.

    The synthesized node ids are ``max(every node id the extract mentions) + 1``
    and upwards, handed out in visit order — ways ascending by id, and within a
    way ``start`` before ``end``. The offset makes a collision with a real OSM
    id impossible by construction (every real id is at most ``base - 1``), and
    the order is a pure function of the file, so the same ``.osm`` synthesizes
    the same ids twice. If it did not, the emitted bytes would differ here.
    """
    from services.genesis.osm import trim_ways_to_requested_bbox

    path = _write(tmp_path, "overhang.osm", overhang_osm())
    first = to_xml_text(build_draft(parse_osm_file(path, requested_bbox=BBOX), name="ov"))
    second = to_xml_text(build_draft(parse_osm_file(path, requested_bbox=BBOX), name="ov"))
    assert first == second

    extract = parse_osm_file(path, requested_bbox=BBOX)
    trimmed = trim_ways_to_requested_bbox(extract)
    synthesized = sorted(nid for nid in trimmed.nodes if nid not in extract.nodes)
    assert max(extract.nodes) == 11
    assert synthesized == [12, 13], "deterministic ids, above the highest real id"


def test_a_build_without_a_requested_bbox_is_untouched_by_any_of_this(tmp_path: Path):
    """The no-bbox path must be the path it was before this unit existed.

    Asserted as an IDENTITY rather than as an equality: with no requested bbox
    the trim returns the very object it was given, so there is no second code
    path that could drift. The two rungs of the ladder that existed before still
    decide the boundary, and no trim record exists to change any report.
    """
    from services.genesis.osm import trim_ways_to_requested_bbox

    extract = parse_osm_file(_write(tmp_path, "overhang.osm", overhang_osm()))
    assert extract.requested_bbox is None
    assert trim_ways_to_requested_bbox(extract) is extract
    assert conn.boundary_source(extract) == conn.BOUNDARY_SOURCE_HULL

    draft = build_draft(extract, name="ov")
    assert draft.metadata["boundary"]["way_trims"] == []
    assert draft.metadata["boundary"]["requested_bbox_wgs84"] is None


def test_the_r15_gate_passes_v3_on_a_bbox_extraction_with_overhanging_tails(
    tmp_path: Path,
):
    """The unit's whole reason for existing, end to end and judged by the gate.

    The same fixture built twice: once as the generator used to see it (the hull
    of the received geometry is the boundary) and once with the bbox that was
    requested. The gate is the real one, run over the real emitted file with the
    real census.

    Without the bbox, way 203 is a ONE-WAY arm whose far end is an interior dead
    end, so there is no second carriageway to turn into, the generator refuses to
    invent one, and V3 fails with ``one_way_dead_end_unfixable`` — the exact code
    that refused every bbox extraction this project made before this unit.

    With the bbox the tails are cut at the rectangle, the terminals are 0.00 m
    from it, every dangling cause is ``model_boundary_correct`` — which the gate
    filters as BENIGN — and V3 reports nothing.
    """
    from services.genesis.gate import run_gate

    text = _osm_text(
        [
            (10, LAT0, LON0 - 0.005),
            (1, LAT0, LON0 - 0.001),
            (2, LAT0, LON0),
            (3, LAT0, LON0 + 0.001),
            (11, LAT0, LON0 + 0.005),
            (4, LAT0 - 0.001, LON0),
            (5, LAT0 + 0.001, LON0),
            (12, LAT0 + 0.0005, LON0 + 0.004),
        ],
        [
            (201, [10, 1, 2, 3, 11], {"highway": "secondary", "name": "Main St"}),
            (202, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
            (
                203,
                [3, 12],
                {"highway": "secondary", "name": "Slip Rd", "oneway": "yes"},
            ),
        ],
    )
    source = _write(tmp_path, "bbox.osm", text)

    def v3_of(bbox):
        draft = build_draft(parse_osm_file(source, requested_bbox=bbox), name="bbox")
        path = emit_inpx(draft, tmp_path / f"{'with' if bbox else 'without'}.inpx")
        census = json.loads(json.dumps(draft.model_dump(mode="json")))
        return draft, run_gate(path, draft_metadata=census).check("V3")

    _hull_draft, hull_v3 = v3_of(None)
    assert [f.code for f in hull_v3.findings] == ["v3_silent_departure_point"]
    assert hull_v3.findings[0].context["cause"] == conn.DANGLING_ONE_WAY

    draft, v3 = v3_of(BBOX)
    assert [f.code for f in v3.findings] == []
    causes = draft.metadata["connectivity"]["dangling_approaches_by_cause"]
    assert set(causes) <= set(conn.BENIGN_DANGLING_CAUSES), causes
    assert causes[conn.DANGLING_MODEL_BOUNDARY] >= 2


def test_the_isolation_pass_may_remove_a_pocket_and_never_the_network(tmp_path: Path):
    """A pocket is a PART of a network. A network is not a pocket of itself.

    With no entry and no exit anywhere — every arm closed by a cul-de-sac U-turn
    — ``isolated_zones``'s "unreachable from any entry" is vacuously true of
    every link, so it reports the WHOLE network as one zone. That answer is
    correct and it stays; what must not happen is REMOVING it, because the
    rebuild then has no ways and the caller is handed ``osm_no_nodes: no node
    coordinates to project`` — a refusal about a network that does not exist,
    for a network that did.

    Measured 2026-08-12 on the region end-to-end fixture: 8 links in, 0 links
    back, no artifact, and the reason in no report at all. The zones are now
    RETAINED under their own name and the network is emitted for the gate to
    judge, because a network that exists and is refused beats a refusal about
    nothing.
    """
    text = _osm_text(
        _FRAME,
        [
            (101, [1, 2, 3], {"highway": "residential", "name": "Main St"}),
            (102, [4, 2, 5], {"highway": "residential", "name": "First Ave"}),
        ],
    )
    # A requested bbox far wider than the geometry: every arm end is interior,
    # so every one of them gains a U-turn and the model closes.
    wide = (LAT0 - 0.02, LON0 - 0.02, LAT0 + 0.02, LON0 + 0.02)
    draft = build_draft(
        parse_osm_file(_write(tmp_path, "closed.osm", text), requested_bbox=wide),
        name="closed",
    )

    assert not draft.refused and draft.links, "the network must survive"
    connectivity = draft.metadata["connectivity"]
    assert connectivity["network_has_no_entry_or_exit"] is True
    assert connectivity["excluded_zones"] == []
    assert [z["reason"] for z in connectivity["isolated_zones_retained"]] == [
        draft_module.ISOLATION_WOULD_EMPTY_THE_NETWORK
    ]


def test_the_provenance_region_maps_onto_the_bbox_tuple_without_transposing():
    """south/west/north/east -> (min_lat, min_lon, max_lat, max_lon), asserted.

    The fetch provenance names its window with compass words and this package's
    bbox tuples are ``(lat, lon, lat, lon)``. A transposition would rotate every
    study area onto its side and nothing downstream could tell, so the mapping is
    a named function with a test rather than four subscripts in a caller.
    """
    from services.genesis.osm import requested_bbox_from_provenance

    assert requested_bbox_from_provenance(
        {"requested_region": {"south": 1.0, "west": 2.0, "north": 3.0, "east": 4.0}}
    ) == (1.0, 2.0, 3.0, 4.0)

    # Nothing usable is never guessed at: a caller with no window falls back to
    # the behaviour it had before there was one.
    assert requested_bbox_from_provenance(None) is None
    assert requested_bbox_from_provenance({}) is None
    assert requested_bbox_from_provenance({"requested_region": {"south": 1.0}}) is None
    assert (
        requested_bbox_from_provenance(
            {"requested_region": {"south": 3.0, "west": 2.0, "north": 1.0, "east": 4.0}}
        )
        is None
    )


def test_the_job_runner_reads_the_bbox_out_of_the_frozen_source_osm(tmp_path: Path):
    """The requested bbox survives a fetch only as a comment in the .osm itself.

    The job record crosses the HTTP boundary and carries no region, so the file's
    own leading ``<!-- prism-osm-fetch -->`` comment is the only place the study
    area still exists by the time a generation runs. Until this unit nothing read
    it during a job.

    The upload road has no such comment and must be completely unaffected — that
    is what keeps every uploaded ``.osm`` building exactly as it did.
    """
    from services import genesis_jobs
    from services.osm_fetch import annotate

    plain = _write(tmp_path, "plain.osm", overhang_osm())
    assert genesis_jobs._requested_bbox_of(plain) is None

    annotated = tmp_path / "annotated.osm"
    annotated.write_bytes(
        annotate(
            plain.read_bytes(),
            {
                "requested_region": {
                    "south": BBOX[0],
                    "west": BBOX[1],
                    "north": BBOX[2],
                    "east": BBOX[3],
                }
            },
        )
    )
    assert genesis_jobs._requested_bbox_of(annotated) == BBOX


def test_the_probe_reads_the_same_bbox_the_generator_does(tmp_path: Path):
    """One seam, or the tool measures a network the file is not.

    The probe regenerates the draft to attribute causes. If it rebuilt WITHOUT
    the requested bbox while the generator built WITH it, every number it printed
    would describe a different network — the split brain its own module docstring
    forbids. The ``--ignore-requested-bbox`` half of this test proves the two
    networks really are different, so the first half is not asserting a
    coincidence.
    """
    from services.osm_fetch import annotate

    source = tmp_path / "probe.osm"
    source.write_bytes(
        annotate(
            overhang_osm().encode("utf-8"),
            {
                "requested_region": {
                    "south": BBOX[0],
                    "west": BBOX[1],
                    "north": BBOX[2],
                    "east": BBOX[3],
                }
            },
        )
    )
    draft = build_draft(parse_osm_file(source, requested_bbox=BBOX), name="probe")
    inpx = emit_inpx(draft, tmp_path / "probe.inpx")

    code, out = _run_probe("--inpx", str(inpx), "--osm", str(source), "--json")
    assert code == 0, out
    measured = json.loads(out)
    assert measured["requested_bbox"] == list(BBOX)
    assert measured["boundary"] == conn.BOUNDARY_SOURCE_REQUESTED_BBOX
    assert measured["file_matches_regeneration"] is True
    assert measured["links"] == len(draft.links)

    ignored = json.loads(
        _run_probe(
            "--inpx",
            str(inpx),
            "--osm",
            str(source),
            "--json",
            "--ignore-requested-bbox",
        )[1]
    )
    assert ignored["file_matches_regeneration"] is False, (
        "dropping the bbox must produce a DIFFERENT network, or this test proves nothing"
    )


# ---------------------------------------------------------------------------
# 9. What adversarial review found in section 8, and what now stops it
#
# Every test below is a construction a refuter built to break the trim, kept as
# a regression. Two of them were CRITICAL: the first silently deleted a boundary
# exit and invented a turning movement, the second reported terminals 23 m and
# 103 m OUTSIDE the study rectangle as "model_boundary_correct" — a cause the
# R1.5 gate filters out as benign, so V3 passed over them without a word.
#
# Metre conversions, as everywhere else in this file: 1 deg of longitude here =
# 94 406 m, 1 deg of latitude = 110 887 m.
# ---------------------------------------------------------------------------

#: 6.0 m west of BBOX's east edge, in degrees of longitude: 6 / 94 406.
_SIX_M_LON = 6.0 / 94406.0
#: 1.921 m of latitude: 1.921 / 110 887. With the 6.0 m above it makes a
#: hypotenuse of sqrt(6.0^2 + 1.921^2) = 6.30 m — inside the 7.0 m node-merge
#: tolerance, and therefore a genuine merge candidate.
_OFFSET_LAT = 1.921 / 110887.0


def test_a_synthesized_boundary_node_is_never_merged_into_a_real_junction(
    tmp_path: Path,
):
    """CRITICAL. The refuter's construction, kept exactly.

    A T-junction the source DECLARES sits 6.0 m inside the east edge of the
    requested rectangle. An unrelated way crosses that edge 1.921 m further
    north, so the node the trim synthesizes there is **6.30 m** from the
    junction — inside the 7.0 m node-merge tolerance, and the merge's
    no-junction guard does not stop it because that guard passes as soon as ONE
    side is a declared junction, which this one is.

    Merged, three things went wrong at once and none of them was visible in a
    count. The cluster position is the MEAN of its members, so the boundary node
    moved **4.501 m off the rectangle** against a 1.0 m band. The terminal's
    occurrences rose to 3, so ``dead_ends`` stopped treating it as a terminal at
    all and the boundary exit vanished from every report. And the network gained
    a turning movement between two roads the source never joins.

    A synthesized node is a truncation MARKER, not a surveyed meeting point, so
    it is refused by name.

    Way 404 exists to make the end-to-end half of this test mean something. It
    joins the T-junction's pocket to way 403 the long way round — through nodes
    3 and 4, nowhere near the boundary — so the pocket is reachable and survives
    ``build_draft``'s isolated-zone removal. Without it the pocket was excluded
    before the merge ever ran and the "no connector between 403 and 401/402"
    assertion below was vacuously true of a network that no longer contained
    401 or 402.
    """
    from services.genesis.osm import trim_ways_to_requested_bbox

    text = _osm_text(
        [
            # the declared T-junction, 6.0 m inside the east edge
            (1, LAT0, LON0 + 0.002 - _SIX_M_LON),
            (2, LAT0, LON0 - 0.001),
            (3, LAT0 + 0.001, LON0 + 0.002 - _SIX_M_LON),
            # the unrelated way, crossing the east edge 1.921 m further north
            (4, LAT0 + _OFFSET_LAT, LON0 + 0.001),
            (5, LAT0 + _OFFSET_LAT, LON0 + 0.004),
        ],
        [
            (401, [2, 1], {"highway": "residential", "name": "Stem St"}),
            (402, [1, 3], {"highway": "residential", "name": "Cross St"}),
            (403, [4, 5], {"highway": "residential", "name": "Unrelated Rd"}),
            (404, [3, 4], {"highway": "residential", "name": "Long Way Round"}),
        ],
    )
    trimmed = trim_ways_to_requested_bbox(
        parse_osm_file(_write(tmp_path, "weld.osm", text), requested_bbox=BBOX)
    )
    synthesized = sorted(trimmed.synthesized_node_ids)
    assert len(synthesized) == 1
    marker = synthesized[0]
    assert trimmed.nodes[marker].lon == LON0 + 0.002, "on the rectangle, exactly"

    merged, report = conn.merge_junction_nodes(trimmed)
    refusals = [r for r in report.refusals if r.code == conn.REFUSE_SYNTHESIZED_BOUNDARY_NODE]
    assert len(refusals) == 1, [r.code for r in report.refusals]
    refusal = refusals[0]
    assert {refusal.node_a, refusal.node_b} == {1, marker}
    assert refusal.separation_m == pytest.approx(6.30, abs=0.05), (
        "the pair must really be inside the tolerance, or this proves nothing"
    )
    assert refusal.separation_m < conn.NODE_MERGE_TOLERANCE_M
    assert "truncation marker" in refusal.detail

    # Nothing moved, and the boundary exit is still reported as one.
    assert report.merges == []
    assert merged.nodes[marker] == trimmed.nodes[marker]
    ends = {e.node_id: e for e in conn.dead_ends(merged)}
    assert ends[marker].reason == conn.DEAD_END_MODEL_BOUNDARY
    assert ends[marker].emit_u_turn is False
    assert "0.00 m from the model boundary" in ends[marker].detail

    # …and no movement was invented between the two roads the source keeps apart.
    draft = build_draft(
        parse_osm_file(tmp_path / "weld.osm", requested_bbox=BBOX), name="weld"
    )
    ways_of = {link.id: link.osm_way_id for link in draft.links}
    assert {401, 402, 403, 404} <= set(ways_of.values()), (
        "every way must survive to the merge, or the assertion below proves nothing"
    )
    assert draft.metadata["connectivity"]["excluded_zones"] == []
    crossings = [
        c
        for c in draft.connectors
        if {ways_of[c.from_link], ways_of[c.to_link]} in ({403, 401}, {403, 402})
    ]
    assert crossings == [], (
        "the source joins way 403 to 401/402 only the long way round, through way 404; "
        "a merge at the boundary would have joined them directly at node 1"
    )
    assert [c for c in draft.connectors if c.node_osm_id == marker] == []


def test_a_way_that_leaves_and_re_enters_still_has_its_tails_cut(tmp_path: Path):
    """CRITICAL. Keeping a re-entrant way WHOLE left terminals outside the model.

    Way 405 runs OUTSIDE → inside → OUTSIDE → inside: node 10 hangs 283.2 m west
    of the rectangle, node 12 is an interior excursion 443.5 m north of the
    centre (outside the lat band), and nodes 11 and 13 are inside.

    The rule is stated on the FIRST and LAST inside node, not on "is there
    exactly one run of them", so:

    * the leading tail (node 10) is CUT and a node is synthesized on the west
      edge — because a terminal outside the rectangle is one the boundary band
      cannot honestly judge;
    * the interior excursion (node 12) is KEPT, because cutting it would delete
      road the study area contains on both sides of, or split one way into two.

    Measured on job d31961abd950 before this: two re-entrant ways kept whole
    left terminals 23 m and 103 m outside the rectangle, and both were reported
    ``model_boundary_correct`` — benign to the gate — because the band test is a
    SIGNED distance that goes negative out there.
    """
    from services.genesis.osm import trim_ways_to_requested_bbox

    text = _osm_text(
        [
            (10, LAT0, LON0 - 0.005),
            (11, LAT0, LON0 - 0.001),
            (12, LAT0 + 0.004, LON0),
            (13, LAT0, LON0 + 0.001),
            (14, LAT0 - 0.001, LON0 + 0.001),
        ],
        [
            (405, [10, 11, 12, 13], {"highway": "residential", "name": "Wiggle Rd"}),
            (406, [13, 14], {"highway": "residential", "name": "Anchor Rd"}),
        ],
    )
    trimmed = trim_ways_to_requested_bbox(
        parse_osm_file(_write(tmp_path, "wiggle.osm", text), requested_bbox=BBOX)
    )

    way = next(w for w in trimmed.ways if w.id == 405)
    assert way.node_ids[0] not in {10, 11, 12, 13}, "the tail was cut, not kept"
    assert way.node_ids[1:] == (11, 12, 13), "the interior excursion stays whole"
    assert trimmed.nodes[way.node_ids[0]].lon == LON0 - 0.002
    assert 10 not in trimmed.nodes

    reasons = sorted(t.reason for t in trimmed.way_trims if t.way_id == 405)
    assert reasons == [
        osm_module.TRIM_KEPT_WHOLE_REENTRANT,
        osm_module.TRIM_TAIL_CUT,
    ], "both facts about this way are recorded, not one of them"

    # The invariant, asserted directly: every TERMINAL is inside or on the
    # rectangle. Interior nodes may be outside; nothing classifies those.
    min_lat, min_lon, max_lat, max_lon = BBOX
    for kept in trimmed.ways:
        for node_id in (kept.node_ids[0], kept.node_ids[-1]):
            node = trimmed.nodes[node_id]
            assert min_lat <= node.lat <= max_lat and min_lon <= node.lon <= max_lon

    ends = {e.node_id: e for e in conn.dead_ends(conn.merge_junction_nodes(trimmed)[0])}
    assert ends[way.node_ids[0]].reason == conn.DEAD_END_MODEL_BOUNDARY


def test_a_terminal_outside_the_boundary_is_never_called_a_boundary(tmp_path: Path):
    """Defense in depth, fed the shape the trim now makes impossible.

    ``edge = min(x - minx, maxx - x, y - miny, maxy - y)`` is a SIGNED distance
    to the nearest edge. Outside the rectangle it is NEGATIVE, so ``edge <=
    BOUNDARY_BAND_M`` is satisfied 100 m out exactly as it is 0.1 m in — which
    is how two terminals 23 m and 103 m outside job d31961abd950's study area
    came to be reported as ``model_boundary_correct``, a cause the gate filters
    out as benign.

    ``dead_ends`` is called here directly on an UNTRIMMED extract whose
    requested bbox is smaller than its geometry, which is the only way to
    produce the shape now that the trim removes it. The classification must be
    the new non-benign one, so that if the geometry rule ever regresses the gate
    FAILS loudly instead of passing quietly.
    """
    text = _osm_text(
        [
            (1, LAT0, LON0 - 0.001),
            (2, LAT0, LON0),
            (3, LAT0, LON0 + 0.006),
            (4, LAT0 - 0.001, LON0),
        ],
        [
            (501, [1, 2, 3], {"highway": "residential", "name": "Long Rd"}),
            (502, [4, 2], {"highway": "residential", "name": "Stub Rd"}),
        ],
    )
    extract = parse_osm_file(_write(tmp_path, "outside.osm", text), requested_bbox=BBOX)
    ends = {e.node_id: e for e in conn.dead_ends(extract)}

    # Node 3 is at LON0 + 0.006 = 566.4 m east of the centre, i.e. 377.6 m
    # OUTSIDE the +/-188.8 m rectangle.
    assert ends[3].reason == conn.DEAD_END_BEYOND_REQUESTED_BBOX
    assert ends[3].emit_u_turn is False
    assert "377.6" in ends[3].detail or "OUTSIDE the model boundary" in ends[3].detail

    cause = conn.DEAD_END_REASON_TO_DANGLING_CAUSE[conn.DEAD_END_BEYOND_REQUESTED_BBOX]
    assert cause == conn.DANGLING_BEYOND_MODEL_BOUNDARY
    assert cause not in conn.BENIGN_DANGLING_CAUSES, (
        "a terminal beyond the study area is a defect, and the gate must see it as one"
    )


def test_two_ways_sharing_a_cut_segment_share_one_synthesized_node(tmp_path: Path):
    """Given one node each they became two coincident disconnected stubs.

    Ways 601 and 602 both contain the segment ``11 -> 12`` that leaves the
    rectangle. Keyed by the WAY, the trim gave each its own synthesized node at
    the identical point; the node merge then REFUSED to join them (they are both
    synthesized, and before that rule they were two lone terminals with no
    declared junction), so the emitted network carried two carriageways lying on
    top of each other with nothing between them.

    Keyed by the SOURCE SEGMENT, they share one node — which is not tidiness but
    the truth: both ways really do contain that segment, so they really do meet
    where it crosses the boundary. The shared node then has two occurrences and
    flows through the ordinary junction machinery.
    """
    from services.genesis.osm import trim_ways_to_requested_bbox

    text = _osm_text(
        [
            (10, LAT0 - 0.001, LON0),
            (11, LAT0, LON0 + 0.001),
            (12, LAT0, LON0 + 0.004),
            (13, LAT0 + 0.001, LON0),
        ],
        [
            (601, [10, 11, 12], {"highway": "residential", "name": "South App"}),
            (602, [13, 11, 12], {"highway": "residential", "name": "North App"}),
        ],
    )
    source = _write(tmp_path, "shared.osm", text)
    trimmed = trim_ways_to_requested_bbox(parse_osm_file(source, requested_bbox=BBOX))

    synthesized = sorted(trimmed.synthesized_node_ids)
    assert len(synthesized) == 1, "one crossing segment, one synthesized node"
    marker = synthesized[0]
    assert [w.node_ids for w in trimmed.ways] == [(10, 11, marker), (13, 11, marker)]
    assert trimmed.nodes[marker].lon == LON0 + 0.002
    assert marker in trimmed.intersection_node_ids(), (
        "two ways end there, so it is a junction and the junction machinery owns it"
    )

    # The merge still refuses to weld it to anything real, and determinism and
    # the emitted bytes are unaffected by the sharing.
    _merged, report = conn.merge_junction_nodes(trimmed)
    assert [r for r in report.refusals if r.code == conn.REFUSE_SYNTHESIZED_BOUNDARY_NODE] == []
    first = to_xml_text(build_draft(parse_osm_file(source, requested_bbox=BBOX), name="sh"))
    second = to_xml_text(build_draft(parse_osm_file(source, requested_bbox=BBOX), name="sh"))
    assert first == second

    # --- and NOTHING is modelled across it -------------------------------
    # Sharing the node gives it two occurrences, which is what the boundary
    # rule had to be taught about: the "is this a terminal?" test asks whether
    # a vehicle can arrive and not leave, and at a shared boundary node the
    # sibling way looks like an exit. Blinded to ``synthesized_node_ids`` this
    # exact fixture produced NO model_boundary dead end, no dangling approach at
    # all, and two same-way FWD→REV U-turns AT the boundary node (connectors
    # 10013 and 10014, both named in ``stranded_approach_u_turns``) — a
    # reflecting wall at the study edge that no gate check can see. So the
    # assertions below are on ``draft.connectors``, not on a count.
    draft = build_draft(parse_osm_file(source, requested_bbox=BBOX), name="sh")
    assert [c.name for c in draft.connectors if c.node_osm_id == marker] == [], (
        "no movement may be modelled at a node this generator invented on the edge"
    )
    ways_of = {link.id: link.osm_way_id for link in draft.links}
    ends_at_marker = {
        link.id for link in draft.links if link.to_node_osm_id == marker
    }
    starts_at_marker = {
        link.id for link in draft.links if link.from_node_osm_id == marker
    }
    assert ends_at_marker and starts_at_marker
    assert [
        c.name
        for c in draft.connectors
        if c.from_link in ends_at_marker
        and c.to_link in starts_at_marker
        and ways_of[c.from_link] == ways_of[c.to_link]
    ] == [], "no same-way U-turn at the boundary — that is the reflecting wall"

    connectivity = draft.metadata["connectivity"]
    assert connectivity["stranded_approach_u_turns"] == []
    dangling = {
        entry["link_id"]: entry
        for entry in connectivity["dangling_approaches"]
        if entry["node_osm_id"] == marker
    }
    assert set(dangling) == ends_at_marker, "both approaches dangle, and are named"
    assert {entry["cause"] for entry in dangling.values()} == {
        conn.DANGLING_MODEL_BOUNDARY
    }
    assert connectivity["dead_ends_by_reason"][conn.DEAD_END_MODEL_BOUNDARY] == 2


def test_a_restriction_whose_via_node_was_trimmed_is_refused_by_name(tmp_path: Path):
    """An unapplied ban is a movement that stays legal although the map forbids it.

    The relation's ``via`` node sits on a tail the trim cut off, so the junction
    the ban applies at is no longer in the network. Both the ``from`` and ``to``
    ways SURVIVE — that is what makes this a different fact from
    ``RESTRICTION_WAY_NOT_KEPT`` and what makes it this generator's doing rather
    than the extract's edge — so it gets its own reason code, its own count, and
    a line in the compliance report.
    """
    from services.genesis.osm import trim_ways_to_requested_bbox

    text = _osm_text(
        [
            (1, LAT0, LON0 - 0.001),
            (2, LAT0, LON0 + 0.001),
            (3, LAT0, LON0 + 0.003),
            (4, LAT0 + 0.001, LON0 + 0.001),
            (5, LAT0 + 0.001, LON0 - 0.001),
        ],
        [
            (701, [1, 2, 3], {"highway": "residential", "name": "From Rd"}),
            (702, [3, 4, 5], {"highway": "residential", "name": "To Rd"}),
        ],
        relations=(
            (
                9001,
                (("way", 701, "from"), ("node", 3, "via"), ("way", 702, "to")),
                {"type": "restriction", "restriction": "no_left_turn"},
            ),
        ),
    )
    source = _write(tmp_path, "viacut.osm", text)
    parsed = parse_osm_file(source, requested_bbox=BBOX)
    assert [r.id for r in parsed.restrictions] == [9001], "the relation must parse first"

    trimmed = trim_ways_to_requested_bbox(parsed)
    assert {w.id for w in trimmed.ways} == {701, 702}, "both named ways survive"
    assert 3 not in trimmed.nodes, "the via node left with the tail"
    assert trimmed.restrictions == []
    refusals = {r.id: r for r in trimmed.restriction_refusals}
    assert refusals[9001].reason == osm_module.RESTRICTION_VIA_NODE_TRIMMED
    assert "outside the requested bbox" in refusals[9001].detail

    draft = build_draft(parse_osm_file(source, requested_bbox=BBOX), name="viacut")
    connectivity = draft.metadata["connectivity"]
    assert connectivity["osm_turn_restrictions_refused_by_reason"] == {
        osm_module.RESTRICTION_VIA_NODE_TRIMMED: 1
    }
    rendered = draft.render_compliance("ko")
    assert f"not applied / {osm_module.RESTRICTION_VIA_NODE_TRIMMED}: 1" in rendered


def test_the_gate_sees_the_lane_drop_the_requested_boundary_shortens(tmp_path: Path):
    """The honest consequence of trimming, pinned so it cannot silently vanish.

    Way 801 enters the study area from the west carrying 4 lanes per direction
    and drops to 2 at the junction with way 802. Its approach used to include
    283 m of road OUTSIDE the requested rectangle; cut at the boundary, the
    runway is what the MODEL actually contains — well under the 200 m the file's
    own ``lnChgDist`` says a lane change needs — and V4 says so.

    That finding is true of the emitted network and was previously masked by
    geometry nobody asked for. Nothing in the gate is touched here and nothing
    about V4 is being defended as ideal: a boundary-aware V4 is a stage-2
    question. This test exists so that the answer to it cannot be "the finding
    quietly went away".
    """
    from services.genesis.gate import run_gate

    text = _osm_text(
        [
            (1, LAT0, LON0 - 0.005),
            (2, LAT0, LON0 - 0.0005),
            (3, LAT0, LON0 + 0.001),
            (4, LAT0 - 0.001, LON0 - 0.0005),
        ],
        [
            (801, [1, 2], {"highway": "secondary", "name": "Wide St", "lanes": "8"}),
            (802, [2, 3], {"highway": "secondary", "name": "Narrow St", "lanes": "4"}),
            (803, [4, 2], {"highway": "residential", "name": "Side Rd"}),
        ],
    )
    source = _write(tmp_path, "drop.osm", text)
    draft = build_draft(parse_osm_file(source, requested_bbox=BBOX), name="drop")
    path = emit_inpx(draft, tmp_path / "drop.inpx")
    census = json.loads(json.dumps(draft.model_dump(mode="json")))

    v4 = run_gate(path, draft_metadata=census).check("V4")
    drops = [f for f in v4.findings if f.code == "v4_lane_drop_without_runway"]
    assert drops, [f.code for f in v4.findings]
    assert drops[0].context["available_m"] < drops[0].context["required_m"]
    assert v4.state.value == "fail"


def test_a_declared_bounds_window_narrower_than_its_geometry_is_not_a_fault(
    tmp_path: Path,
):
    """The tripwire is scoped to the rung that has a remedy, and only that one.

    A file whose ``<bounds>`` window is narrower than the geometry inside it is
    the NORMAL shape of a whole-way extract — Overpass and JOSM both produce it
    — and it was read as a (benign) model boundary long before this unit
    existed. There is no trim on that rung, so grading it a fault would refuse
    such files permanently with nothing the user could do about it. Trimming
    rung 2 as well is a deliberate non-goal here and a named follow-up.

    What DOES change on that rung is the sentence: the old text printed the
    signed distance, so a terminal 377 m outside the window was reported as
    "-377.61 m from the model boundary". It now says how far OUTSIDE, and says
    which window.

    The same geometry with a REQUESTED bbox is a different fact — this
    generator cut every tail at that rectangle, so a terminal beyond it is its
    own defect — and there the tripwire still fires, non-benign.
    """
    nodes = [
        (1, LAT0, LON0 - 0.001),
        (2, LAT0, LON0),
        (3, LAT0, LON0 + 0.006),
        (4, LAT0 - 0.001, LON0),
    ]
    ways = [
        (501, [1, 2, 3], {"highway": "residential", "name": "Long Rd"}),
        (502, [4, 2], {"highway": "residential", "name": "Stub Rd"}),
    ]
    # Node 3 is 566.4 m east of the centre; the window's east edge is 188.8 m
    # east of it, so the terminal is 377.6 m OUTSIDE the declared window.
    declared = parse_osm_file(
        _write(tmp_path, "narrow.osm", _osm_text(nodes, ways, bounds=BBOX))
    )
    assert declared.requested_bbox is None
    assert conn.boundary_source(declared) == conn.BOUNDARY_SOURCE_DECLARED_BOUNDS

    ends = {e.node_id: e for e in conn.dead_ends(declared)}
    assert ends[3].reason == conn.DEAD_END_MODEL_BOUNDARY
    assert ends[3].emit_u_turn is False
    assert conn.DEAD_END_REASON_TO_DANGLING_CAUSE[ends[3].reason] in (
        conn.BENIGN_DANGLING_CAUSES
    )
    assert "-" not in ends[3].detail.split("—")[0], (
        "a distance printed as negative is a number no reader can act on"
    )
    assert "377.6" in ends[3].detail
    assert "outside the declared <bounds> window" in ends[3].detail
    assert "overhangs the window it declares" in ends[3].detail

    # V3 is unaffected: the cause is benign, so the gate has nothing to say.
    from services.genesis.gate import run_gate

    draft = build_draft(declared, name="narrow")
    path = emit_inpx(draft, tmp_path / "narrow.inpx")
    census = json.loads(json.dumps(draft.model_dump(mode="json")))
    v3 = run_gate(path, draft_metadata=census).check("V3")
    assert [f.code for f in v3.findings if f.code == "v3_silent_departure_point"] == []

    # …and the identical geometry judged against a REQUESTED rectangle is a
    # fault, because that rung comes with a trim that should have prevented it.
    requested = parse_osm_file(
        _write(tmp_path, "requested.osm", _osm_text(nodes, ways)), requested_bbox=BBOX
    )
    fired = {e.node_id: e for e in conn.dead_ends(requested)}
    assert fired[3].reason == conn.DEAD_END_BEYOND_REQUESTED_BBOX
    assert (
        conn.DEAD_END_REASON_TO_DANGLING_CAUSE[fired[3].reason]
        not in conn.BENIGN_DANGLING_CAUSES
    )

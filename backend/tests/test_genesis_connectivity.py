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

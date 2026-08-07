"""Roadmap 3.1 Genesis (b) — signals: layout, emission, round trip, refusals.

What this file proves, in the order it matters:

1. **The 1.6 chain end-to-end.** A SCATS-shaped observed history →
   ``convert_to_fixed_time`` → ``generate_node_signals`` → emitted ``.inpx`` →
   read back through ``services.signal_parser`` and ``services.inpx_parser``,
   with the recovered cycle, groups and green windows equal to the plan's own
   numbers. The fixture is 1.6's own A.2 composite (cycle 78, greens 35/21/6,
   offsets 0/41/67) and the expected windows are written out by hand.
2. **Zero invented numbers.** Every timing traces to the plan and every geometry
   offset to the draft or to a cited §5 default.
3. **The refusals fire**, one test per named shape, each carrying ko/en/ja.
4. **The unsignalised path is byte-identical** to what it emitted before this
   module existed.
5. **The schema constants are the Vissim-saved ones** — transcribed verbatim
   here, the same way ``test_genesis_osm_draft.py`` pins ``netPara`` and
   ``<node>``, so the check does not depend on the (git-ignored) reference
   networks being present.
6. **The citations quote the §5 extract**, with the mutation counterfactual that
   proves the comparison bites.
"""
from __future__ import annotations

import ast
import base64
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType

import pytest

from services.dataimport.base import IssueSeverity
from services.dataimport.fixed_time import convert_to_fixed_time
from services.dataimport.schema import (
    PhaseData,
    ProgramSourceKind,
    Provenance,
    SignalControllerData,
    SignalProgramData,
)
from services.genesis import emit, schema
from services.genesis.draft import (
    ComplianceState,
    DraftConnector,
    DraftLink,
    DraftNetwork,
    DraftNode,
)
from services.genesis import signals as signals_module
from services.genesis.signals import (
    NO_SIGNALS,
    REFUSE_CODES,
    DraftSignals,
    SignalSettings,
    build_vissig_document,
    generate_node_signals,
    generate_signals,
)
from services.genesis.osm import EXTRACT
from services.inpx_parser import parse_network_elements
from services.signal_parser import parse_signal_controllers, parse_signal_programs

# The citation comparison is reused, never re-implemented — one implementation
# means it cannot be weakened here without also weakening it for unit 1.6 and
# for the OSM draft. Same import the genesis draft test makes.
from tests.test_dataimport_fixed_time import (  # noqa: E402
    _citation_constants,
    _citation_traceability_failures,
    _normalise_prose,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 0. Fixtures — 1.6's own A.2 composite, and a four-approach crossroads
#
# The history is chosen so every derived number is exact rather than
# nearly-exact: 10 complete cycles and totals of 410/260/110 s give phase
# averages of 41/26/11 s with NO rounding residual, which is what makes the
# composite's published figures (cycle 78, greens 35/21/6, offsets 0/41/67)
# come out of ``convert_to_fixed_time`` rather than out of this file.
# ---------------------------------------------------------------------------

PROV = Provenance(source_file="scats-history.csv", locator="row 1")

#: The composite's own published figures, asserted against the CONVERTER's
#: output before anything in this module touches them.
A2_CYCLE_S = 78.0
A2_GREENS_S = [35.0, 21.0, 6.0]
A2_OFFSETS_S = [0.0, 41.0, 67.0]
#: The intergreens those figures imply — each one a subtraction on paper:
#: 41-35, (67-41)-21, (78-67)-6.
A2_INTERGREENS_S = [6.0, 5.0, 5.0]
#: A.3 ambers, and therefore the all-red remainders 6-3, 5-3, 5-3.
A2_AMBERS_S = [3.0, 3.0, 3.0]
A2_ALL_REDS_S = [3.0, 2.0, 2.0]


def _phase(phase_id, total_s, amber_s, all_red_s, *, stretch=False, seq=0, **extra):
    return PhaseData(
        provenance=PROV,
        phase_id=phase_id,
        sequence_index=seq,
        amber_s=amber_s,
        all_red_s=all_red_s,
        is_stretch_phase=stretch,
        observation_count=10,
        observed_total_duration_s=total_s,
        **extra,
    )


@pytest.fixture
def a2_plan():
    """1.6's A.2 composite, produced by 1.6 rather than hand-written."""
    program = SignalProgramData(
        provenance=PROV,
        program_id="P1",
        source_kind=ProgramSourceKind.OBSERVED_HISTORY,
        observed_cycle_count=10,
        phases=[
            _phase("A", 410.0, 3.0, 3.0, stretch=True, seq=0, min_green_s=10.0),
            _phase("B", 260.0, 3.0, 2.0, seq=1, min_green_s=8.0),
            _phase("C", 110.0, 3.0, 2.0, seq=2, min_green_s=5.0),
        ],
    )
    controller = SignalControllerData(
        provenance=PROV, controller_id="TCS999", programs=[program]
    )
    return convert_to_fixed_time(controller)


def _link(link_id, name, points, lanes=1):
    return DraftLink(
        id=link_id,
        name=name,
        osm_way_id=link_id,
        segment_index=0,
        forward=True,
        lanes=lanes,
        lane_width_m=3.5,
        points=points,
        lane_provenance={},
        highway="primary",
        from_node_osm_id=100 + link_id,
        to_node_osm_id=1,
        side_offset_m=0.0,
    )


#: Every approach is exactly 100 m long, so a head's ``pos`` is 100.000 and
#: §5.4.2.6's detector lands at 100 - 2.0 - 4.5 = 93.5 with no arithmetic to
#: hide a mistake in.
APPROACH_LENGTH_M = 100.0


@pytest.fixture
def crossroads():
    """Four approaches into one node, with a through movement off each."""
    links = [
        _link(1, "North approach", [(0.0, APPROACH_LENGTH_M), (0.0, 0.0)]),
        _link(3, "South approach", [(0.0, -APPROACH_LENGTH_M), (0.0, 0.0)]),
        _link(5, "East approach", [(APPROACH_LENGTH_M, 0.0), (0.0, 0.0)], lanes=2),
        _link(7, "West approach", [(-APPROACH_LENGTH_M, 0.0), (0.0, 0.0)]),
    ]
    connectors = [
        DraftConnector(
            id=10000 + index,
            name=f"{a}->{b}",
            node_osm_id=1,
            node_id=1,
            from_link=a,
            to_link=b,
            from_pos_m=APPROACH_LENGTH_M,
            to_pos_m=0.0,
            lanes=1,
            lane_width_m=3.5,
            points=[(0.0, 0.0), (1.0, 1.0)],
            turn="through",
            heading_change_deg=0.0,
            lane_change="same",
            from_lanes=[1],
            to_lanes=[1],
        )
        for index, (a, b) in enumerate([(1, 3), (3, 1), (5, 7), (7, 5)], start=1)
    ]
    node = DraftNode(
        id=1,
        name="n1",
        osm_id=1,
        x=0.0,
        y=0.0,
        polygon=[(-15.0, -15.0), (15.0, -15.0), (15.0, 15.0), (-15.0, 15.0)],
    )
    return DraftNetwork(
        name="crossroads",
        source_osm="crossroads.osm",
        links=links,
        nodes=[node],
        connectors=connectors,
    )


#: The layout this module chose: two opposing approaches share the main phase.
STANDARD_ASSIGNMENT = {1: "A", 3: "A", 5: "B", 7: "C"}


@pytest.fixture
def node_signals(crossroads, a2_plan):
    return generate_node_signals(
        crossroads,
        crossroads.nodes[0],
        a2_plan,
        approach_phases=STANDARD_ASSIGNMENT,
    )


# ---------------------------------------------------------------------------
# 1. The converter really does produce the composite this file claims
# ---------------------------------------------------------------------------


def test_the_fixture_is_1_6s_published_a2_composite(a2_plan):
    """Before any signal is laid out: the CONVERTER's own output is the
    composite. If 1.6 ever changes, this fails here rather than silently
    changing what the round-trip test is proving."""
    assert a2_plan.ok
    assert a2_plan.plan_cycle_s == A2_CYCLE_S
    assert a2_plan.round_up_residual_s == 0.0
    assert [row.green_s for row in a2_plan.rows] == A2_GREENS_S
    assert [row.start_offset_s for row in a2_plan.rows] == A2_OFFSETS_S
    assert [row.intergreen_s for row in a2_plan.rows] == A2_INTERGREENS_S


# ---------------------------------------------------------------------------
# 2. The layout decision, and the windows it produces
# ---------------------------------------------------------------------------


def test_one_signal_group_per_approach_numbered_by_link_id(node_signals):
    """The layout decision itself: four approaches → four groups, numbered
    1..4 in ascending approach-link-id order, each naming its phase."""
    assert node_signals.ok
    assert [group.no for group in node_signals.groups] == [1, 2, 3, 4]
    assert [group.approach_link_id for group in node_signals.groups] == [1, 3, 5, 7]
    assert [group.phase_id for group in node_signals.groups] == ["A", "A", "B", "C"]
    # Two approaches share phase A, which is the whole point of a per-approach
    # layout: they are separately addressable but run together.
    assert node_signals.groups[0].green_start_s == node_signals.groups[1].green_start_s


def test_the_layout_decision_is_declared_on_every_run(node_signals):
    """The module states its own layout, and states that it is NOT §5.4.2.1
    compliance, every single time — never only in a docstring."""
    (info,) = [
        issue
        for issue in node_signals.issues
        if issue.code == "signals_group_layout_is_per_approach"
    ]
    assert info.severity is IssueSeverity.INFO
    assert "ONE SIGNAL GROUP PER APPROACH" in info.message
    assert "placeholder" in info.message
    assert info.context["mapping"] == {1: 1, 2: 3, 3: 5, 4: 7}
    assert info.context["phase_of_group"] == {1: "A", 2: "A", 3: "B", 4: "C"}
    assert info.citation == signals_module.CIT_S5421_SIGNAL_GROUPS


def test_green_windows_are_the_plans_own_numbers_by_hand(node_signals):
    """The worked example from the module docstring, asserted number by number.

    SG1/SG2 (phase A):  green 0 → 35,  amber → 38,  all-red → 41
    SG3     (phase B):  green 41 → 62, amber → 65, all-red → 67
    SG4     (phase C):  green 67 → 73, amber → 76, all-red → 78 (= cycle)
    """
    by_no = {group.no: group for group in node_signals.groups}

    assert (by_no[1].green_start_s, by_no[1].green_s) == (0.0, 35.0)
    assert (by_no[1].amber_s, by_no[1].all_red_s) == (3.0, 3.0)
    assert (by_no[1].green_end_s, by_no[1].red_start_s) == (35.0, 38.0)

    assert (by_no[3].green_start_s, by_no[3].green_s) == (41.0, 21.0)
    assert (by_no[3].amber_s, by_no[3].all_red_s) == (3.0, 2.0)
    assert (by_no[3].green_end_s, by_no[3].red_start_s) == (62.0, 65.0)

    assert (by_no[4].green_start_s, by_no[4].green_s) == (67.0, 6.0)
    assert (by_no[4].amber_s, by_no[4].all_red_s) == (3.0, 2.0)
    assert (by_no[4].green_end_s, by_no[4].red_start_s) == (73.0, 76.0)

    # Every group's phase closes exactly where the next one opens, and the last
    # closes on the cycle: the windows tile the plan cycle with no gap and no
    # overlap, which is the invariant a hand-built program gets wrong.
    assert by_no[1].red_start_s + by_no[1].all_red_s == by_no[3].green_start_s
    assert by_no[3].red_start_s + by_no[3].all_red_s == by_no[4].green_start_s
    assert by_no[4].red_start_s + by_no[4].all_red_s == A2_CYCLE_S


def test_every_timing_traces_to_the_plan_and_nothing_is_invented(node_signals, a2_plan):
    """Zero invented numbers: the SET of every timing this module emits is a
    subset of the SET the plan published (plus the declared red/amber zero)."""
    plan_greens = {row.green_s for row in a2_plan.rows if row.green_s is not None}
    plan_offsets = {row.start_offset_s for row in a2_plan.rows}
    plan_intergreens = {row.intergreen_s for row in a2_plan.rows}

    assert {group.green_s for group in node_signals.groups} <= plan_greens
    assert {group.green_start_s for group in node_signals.groups} <= plan_offsets
    assert {group.intergreen_s for group in node_signals.groups} <= plan_intergreens
    assert node_signals.cycle_s == a2_plan.plan_cycle_s
    # Red/amber is the one number this module contributes, it is zero, and it is
    # a declared setting rather than a literal buried in the emitter.
    assert {group.red_amber_s for group in node_signals.groups} == {0.0}
    assert SignalSettings().red_amber_s == 0.0


def test_min_green_comes_from_the_controller_record(node_signals):
    """§5.4.2.1: minimum greens are input from controller/SCATS data. The
    fixture states 10/8/5 s, and those are what the groups carry."""
    assert [group.min_green_s for group in node_signals.groups] == [10.0, 10.0, 8.0, 5.0]
    assert "signals_min_green_absent" not in node_signals.issue_codes()


def test_a_missing_min_green_is_named_not_silently_zeroed(crossroads, a2_plan):
    program = a2_plan.program
    for phase in program.phases:
        phase.min_green_s = None
    result = generate_node_signals(
        crossroads, crossroads.nodes[0], a2_plan, approach_phases=STANDARD_ASSIGNMENT
    )
    assert result.ok
    assert "signals_min_green_absent" in result.issue_codes(IssueSeverity.WARN)
    assert {group.min_green_s for group in result.groups} == {0.0}


# ---------------------------------------------------------------------------
# 3. Heads (§5.4.2.2) and detectors (§5.4.2.6)
# ---------------------------------------------------------------------------


def test_one_head_per_approach_lane_at_the_stop_line(node_signals):
    """§5.4.2.2 puts heads on LINKS. One per lane, at the link's end, with NO
    offset — §5.4.2.2 prescribes none (the 2.0 m often quoted at it is
    §5.4.2.6's detector setback)."""
    # 1 + 1 + 2 + 1 lanes = 5 heads.
    assert len(node_signals.heads) == 5
    assert [head.link_id for head in node_signals.heads] == [1, 3, 5, 5, 7]
    assert [head.lane for head in node_signals.heads] == [1, 1, 1, 2, 1]
    assert {head.pos_m for head in node_signals.heads} == {APPROACH_LENGTH_M}
    # Each head points at its own approach's group.
    assert [head.group_no for head in node_signals.heads] == [1, 2, 3, 3, 4]
    assert [head.no for head in node_signals.heads] == [1, 2, 3, 4, 5]


def test_head_lane_indices_use_the_measured_left_hand_semantics(node_signals):
    """Lane index 1 is the RIGHT-most lane in both regimes (measured, see
    ``draft.LANE_INDEX_1_IS_RIGHTMOST``), so under this generator's left-hand
    traffic it is the MEDIAN-side lane. The two-lane approach therefore gets
    heads on 1 (median) and 2 (kerb) — every lane of the approach, indexed the
    way Vissim indexes them, not renumbered by this module."""
    from services.genesis.draft import LANE_INDEX_1_IS_RIGHTMOST

    assert LANE_INDEX_1_IS_RIGHTMOST is True
    two_lane = [head for head in node_signals.heads if head.link_id == 5]
    assert [head.lane for head in two_lane] == [1, 2]
    assert {head.group_no for head in two_lane} == {3}


def test_detectors_use_section_5_4_2_6s_published_numbers(node_signals):
    """The FALLBACK: 2.0 m back from the head, 4.5 m long, per approach lane.

    On a 100 m approach that puts the detector's downstream end at 98.0 and its
    upstream end — which is what Vissim's ``pos`` is — at 100 - 2.0 - 4.5 = 93.5.
    """
    assert signals_module.DETECTOR_SETBACK_M == 2.0
    assert signals_module.DETECTOR_LENGTH_M == 4.5
    assert len(node_signals.detectors) == 5
    assert {detector.pos_m for detector in node_signals.detectors} == {93.5}
    assert {detector.length_m for detector in node_signals.detectors} == {4.5}
    # The downstream end really is 2.0 m back from the head it belongs to —
    # under ASSUMPTION S6, that ``pos`` anchors the detector's UPSTREAM end.
    heads = {head.no: head for head in node_signals.heads}
    for detector in node_signals.detectors:
        head = heads[detector.head_no]
        assert head.pos_m - (detector.pos_m + detector.length_m) == 2.0


def test_the_detector_anchor_assumption_is_declared_not_buried(node_signals):
    """ASSUMPTION S6 is the one geometry semantic this module could NOT measure
    offline: whether Vissim's detector ``pos`` anchors the upstream or the
    downstream end. Emitter v1 died of exactly this kind of unmeasured choice,
    so the choice must be stated in the module rather than only in the numbers.

    The consequence is bounded and the module says why: for a FIXEDTIME
    controller the detectors are inert, so the alternative reading changes no
    vehicle's behaviour in the emitted model.
    """
    source = (
        REPO_ROOT / "backend" / "services" / "genesis" / "signals.py"
    ).read_text(encoding="utf-8")
    assert "S6." in source
    assert "UPSTREAM end" in source
    assert "live open check" in source
    # Both candidate positions are named, so a reader can see what was rejected.
    assert "head − 2.0 − 4.5" in source and "head − 2.0" in source
    # The chosen one is what the objects actually carry.
    assert {detector.pos_m for detector in node_signals.detectors} == {93.5}


def test_every_detector_is_marked_assumed_and_carries_the_citation(node_signals):
    """The rule applies only when no plans exist. A genesis draft has none by
    construction, so the marker travels with the object rather than living in a
    docstring a consumer will not read."""
    assert all(detector.assumed for detector in node_signals.detectors)
    assert {detector.citation for detector in node_signals.detectors} == {
        signals_module.CIT_S5426_DETECTOR_FALLBACK
    }
    (info,) = [
        issue
        for issue in node_signals.issues
        if issue.code == "signals_detectors_not_required_for_fixed_time"
    ]
    assert "FALLBACK" in info.message
    assert "SCATSIM or VAP" in info.message
    assert info.context["assumed"] is True


def test_a_detector_that_does_not_fit_is_left_out_and_named(a2_plan):
    """§5.4.2.6 states ONE length. An approach shorter than setback + length
    gets NO detector rather than a shortened one or one shoved to position 0 —
    a detector of a different length is a different object.

    Three approaches so all three plan phases are used: two 100 m ones that fit
    and one 5 m one that cannot (5 < 2.0 + 4.5). The heads are unaffected: a
    head needs no room upstream.
    """
    links = [
        _link(1, "Long N", [(0.0, APPROACH_LENGTH_M), (0.0, 0.0)]),
        _link(3, "Long S", [(0.0, -APPROACH_LENGTH_M), (0.0, 0.0)]),
        _link(5, "Stub E", [(5.0, 0.0), (0.0, 0.0)]),
    ]
    node = DraftNode(id=1, name="n1", osm_id=1, x=0.0, y=0.0, polygon=[(0.0, 0.0)])
    draft = DraftNetwork(
        name="short", source_osm="s.osm", links=links, nodes=[node], connectors=[]
    )
    result = generate_node_signals(
        draft, node, a2_plan, approach_phases={1: "A", 3: "B", 5: "C"}
    )
    assert result.ok
    # Three heads, but only the two long approaches carry a detector.
    assert len(result.heads) == 3
    assert [detector.link_id for detector in result.detectors] == [1, 3]
    assert {detector.pos_m for detector in result.detectors} == {93.5}

    (warning,) = [
        issue
        for issue in result.issues
        if issue.code == "signals_detector_does_not_fit"
    ]
    assert warning.severity is IssueSeverity.WARN
    assert warning.context["subjects"] == ["link 5 lane 1"]
    assert warning.citation == signals_module.CIT_S5426_DETECTOR_FALLBACK


def test_detectors_can_be_turned_off(crossroads, a2_plan):
    result = generate_node_signals(
        crossroads,
        crossroads.nodes[0],
        a2_plan,
        approach_phases=STANDARD_ASSIGNMENT,
        settings=SignalSettings(emit_detectors=False),
    )
    assert result.ok
    assert result.detectors == ()
    assert result.heads  # heads are not conditional on detectors


# ---------------------------------------------------------------------------
# 4. THE ROUND TRIP — the headline check
# ---------------------------------------------------------------------------


def _emit(tmp_path, draft, node_signals) -> Path:
    out = tmp_path / "signalised.inpx"
    emit.emit_inpx(draft, out, DraftSignals(nodes=(node_signals,)))
    return out


def test_a2_composite_round_trips_to_the_plans_own_numbers(
    tmp_path, crossroads, node_signals
):
    """SCATS history → 1.6 → this module → XML → ``signal_parser`` → the plan.

    ``signal_parser`` recovers a green as ``red_begin - green_begin - amber``.
    That returns 38-0-3 = 35, 65-41-3 = 21 and 76-67-3 = 6 — the plan's own
    greens — and a cycle of 78000 ms // 1000 = 78. Those are the numbers below,
    written out rather than computed, so a change in either direction fails.
    """
    path = _emit(tmp_path, crossroads, node_signals)
    controllers = parse_signal_controllers(path)

    assert list(controllers) == ["1"]
    info = controllers["1"]
    assert info["controller_type"] == "FIXEDTIME"
    assert info["cycle_time"] == 78
    assert info["num_signal_groups"] == 4
    assert info["num_phases"] == 3

    recovered = [
        (phase["start"], phase["duration"], phase["signal_group_ids"])
        for phase in info["phases"]
    ]
    assert recovered == [
        (0, 35, ["1", "2"]),
        (41, 21, ["3"]),
        (67, 6, ["4"]),
    ]

    # And the recovered numbers ARE the plan's, not merely equal to a literal.
    assert [start for start, _dur, _sgs in recovered] == [int(v) for v in A2_OFFSETS_S]
    assert [dur for _start, dur, _sgs in recovered] == [int(v) for v in A2_GREENS_S]


def test_heads_and_detectors_round_trip_through_inpx_parser(
    tmp_path, crossroads, node_signals
):
    """The geometry half of the round trip: ``inpx_parser`` resolves every head
    and detector back onto a real lane at the position that was written."""
    path = _emit(tmp_path, crossroads, node_signals)
    root = ET.parse(path).getroot()
    elements = parse_network_elements(root)

    heads = elements["signal_heads"]
    assert len(heads) == 5
    assert {head["pos"] for head in heads} == {APPROACH_LENGTH_M}
    assert {head["sc"] for head in heads} == {"1"}
    assert sorted({head["sg"] for head in heads}) == ["1", "2", "3", "4"]
    assert sorted(head["link"] for head in heads) == ["1", "3", "5", "5", "7"]

    detectors = elements["detectors"]
    assert len(detectors) == 5
    assert {detector["pos"] for detector in detectors} == {93.5}
    assert {detector["length"] for detector in detectors} == {4.5}


def test_the_emitted_program_is_valid_vissig(tmp_path, crossroads, node_signals):
    """Decode the base64 and read the program the way Vissim's own save writes
    it: a green command at the offset and a red command after the amber."""
    path = _emit(tmp_path, crossroads, node_signals)
    root = ET.parse(path).getroot()
    controller = root.find(".//signalControllers/signalController")
    inner = ET.fromstring(
        base64.b64decode(controller.get("intSupplyData")).decode("utf-8")
    )

    prog = inner.find("progs/prog")
    assert prog.get("id") == controller.get("progNo") == "1"
    assert prog.get("cycletime") == "78000"

    windows = {}
    for sg in prog.find("sgs"):
        begins = {cmd.get("display"): cmd.get("begin") for cmd in sg.find("cmds")}
        ambers = {
            fs.get("display"): fs.get("duration") for fs in sg.find("fixedstates")
        }
        windows[sg.get("sg_id")] = (
            begins[schema.DISPLAY_GREEN],
            begins[schema.DISPLAY_RED],
            ambers[schema.DISPLAY_AMBER],
            ambers[schema.DISPLAY_RED_AMBER],
        )

    assert windows == {
        "1": ("0", "38000", "3000", "0"),
        "2": ("0", "38000", "3000", "0"),
        "3": ("41000", "65000", "3000", "0"),
        "4": ("67000", "76000", "3000", "0"),
    }


def test_the_split_read_path_reports_green_plus_amber(tmp_path, crossroads, node_signals):
    """A NAMED GAP in the read-only parser, pinned so it stays visible.

    ``parse_signal_programs`` reports ``green_end_s = red_begin`` without
    subtracting the amber it reports alongside, so its "green window" is green +
    amber: 38/65/76 where the greens actually end at 35/62/73. Nothing is lost —
    subtracting the reported amber recovers the plan's own greens, which is the
    second half of this test — but a consumer reading ``green_end_s`` as "when
    green ends" is reading green + amber.

    This is a property of the reader, not of the emission: the emitted commands
    are the shape Vissim's own save uses.
    """
    path = _emit(tmp_path, crossroads, node_signals)
    (entry,) = parse_signal_programs(path)
    (program,) = entry["programs"]

    assert program["cycle_s"] == A2_CYCLE_S
    assert program["offset_s"] == 0.0
    splits = {split["sg_no"]: split for split in program["sg_splits"]}

    # As reported: the RED-command instants, not the green ends.
    assert [splits[sg]["green_end_s"] for sg in ("1", "2", "3", "4")] == [
        38.0,
        38.0,
        65.0,
        76.0,
    ]
    # Subtract the amber this same path reports and the plan's greens come back.
    recovered = [
        splits[sg]["green_end_s"] - splits[sg]["green_start_s"] - splits[sg]["amber_s"]
        for sg in ("1", "2", "3", "4")
    ]
    assert recovered == [35.0, 35.0, 21.0, 6.0]
    assert sorted(set(recovered)) == sorted(A2_GREENS_S)
    assert program["data_quality"] == []


def test_min_green_is_written_but_not_recovered_by_the_split_reader(
    tmp_path, crossroads, node_signals
):
    """The second NAMED GAP, proved in both directions.

    ``_parse_fixedtime_blob_programs`` hard-codes ``min_green_s = None`` for
    every FIXEDTIME controller, by its own stated choice, because it reads the
    BLOB and the blob's ``defaultDurations`` semantics are unverified. This
    module writes ``minGreen`` on the outer ``<signalGroup>`` — where Vissim's
    own save puts it, and where §5.4.2.1 requires the controller's value to
    land.

    So: the attribute IS in the file (first assertion) and the reader does NOT
    return it (second). The field is kept rather than shrunk — dropping a
    guideline-mandated, Vissim-authored field to satisfy one internal read path
    would be a real compliance defect traded for a cosmetic win.
    """
    path = _emit(tmp_path, crossroads, node_signals)

    root = ET.parse(path).getroot()
    groups = root.findall(".//signalControllers/signalController/sgs/signalGroup")
    assert [group.get("minGreen") for group in groups] == ["10", "10", "8", "5"]

    (entry,) = parse_signal_programs(path)
    (program,) = entry["programs"]
    assert {split["min_green_s"] for split in program["sg_splits"]} == {None}


def test_emission_is_deterministic(tmp_path, crossroads, node_signals):
    first = emit.to_xml_text(crossroads, DraftSignals(nodes=(node_signals,)))
    second = emit.to_xml_text(crossroads, DraftSignals(nodes=(node_signals,)))
    assert first == second
    # Including the base64 payload, which is where a dict iteration order or a
    # float repr would show up first.
    assert first.count("intSupplyData") == 1


def test_containers_come_out_in_vissims_own_order(tmp_path, crossroads, node_signals):
    path = _emit(tmp_path, crossroads, node_signals)
    root = ET.parse(path).getroot()
    emitted = [child.tag for child in root if isinstance(child.tag, str)]
    assert emitted == [tag for tag in schema.CONTAINER_ORDER if tag in emitted]
    assert {"detectors", "signalControllers", "signalHeads"} <= set(emitted)
    # detectors before displayTypes; the two signal containers after netPara.
    assert emitted.index("detectors") < emitted.index("displayTypes")
    assert emitted.index("netPara") < emitted.index("signalControllers")
    assert emitted.index("signalControllers") < emitted.index("signalHeads")


# ---------------------------------------------------------------------------
# 5. ADDITIVITY — the unsignalised draft must not move by one byte
# ---------------------------------------------------------------------------


def test_unsignalised_draft_bytes_are_unchanged(crossroads):
    """The whole additivity guarantee, pinned. ``None``, :data:`NO_SIGNALS` and
    a signal set whose every node refused must all produce the SAME bytes — and
    those bytes must still carry the old "GEOMETRY ONLY … No signal heads,
    detectors" provenance sentence, which is true for exactly these files."""
    baseline = emit.to_xml_text(crossroads)

    assert emit.to_xml_text(crossroads, None) == baseline
    assert emit.to_xml_text(crossroads, NO_SIGNALS) == baseline

    refused = generate_node_signals(
        crossroads, crossroads.nodes[0], _refused_plan(), approach_phases={}
    )
    assert not refused.ok
    assert emit.to_xml_text(crossroads, DraftSignals(nodes=(refused,))) == baseline

    assert "GEOMETRY ONLY" in baseline
    assert "No signal heads, detectors" in baseline
    for tag in ("signalControllers", "signalHeads", "<detectors>"):
        assert tag not in baseline


def test_a_signalised_draft_says_so_in_its_provenance_comment(crossroads, node_signals):
    text = emit.to_xml_text(crossroads, DraftSignals(nodes=(node_signals,)))
    assert "GEOMETRY ONLY" not in text
    assert "GEOMETRY + SIGNALS" in text
    assert "ONE PER APPROACH" in text
    assert "PLACEHOLDER" in text
    assert "FALLBACK" in text


def _refused_plan():
    """A plan 1.6 itself refused: no observed-history program at all."""
    controller = SignalControllerData(
        provenance=PROV, controller_id="TCS000", programs=[]
    )
    plan = convert_to_fixed_time(controller)
    assert not plan.ok
    return plan


# ---------------------------------------------------------------------------
# 6. Refusals — one test per named shape, each with ko/en/ja
# ---------------------------------------------------------------------------


def _only_refusal(result):
    assert not result.ok
    assert result.groups == ()
    assert result.heads == ()
    assert result.detectors == ()
    codes = result.issue_codes(IssueSeverity.REFUSE)
    assert len(codes) == 1
    assert codes[0] in REFUSE_CODES
    (refusal,) = result.refusals
    assert set(refusal.messages) == {"ko", "en", "ja"}
    assert all(text.strip() for text in refusal.messages.values())
    return codes[0]


def test_refuses_when_the_plan_itself_refused(crossroads):
    result = generate_node_signals(
        crossroads, crossroads.nodes[0], _refused_plan(), approach_phases={}
    )
    assert _only_refusal(result) == "signals_plan_refused"


def test_refuses_when_an_approach_has_no_phase(crossroads, a2_plan):
    """plan phases ≠ node approaches, from the node's side."""
    result = generate_node_signals(
        crossroads,
        crossroads.nodes[0],
        a2_plan,
        approach_phases={1: "A", 3: "A", 5: "B"},  # link 7 left out
    )
    assert _only_refusal(result) == "signals_approach_without_phase"
    assert result.refusals[0].context["unassigned_link_ids"] == [7]


def test_refuses_when_a_plan_phase_is_never_assigned(crossroads, a2_plan):
    """plan phases ≠ node approaches, from the plan's side: phase C would hold
    green for a movement no head shows."""
    result = generate_node_signals(
        crossroads,
        crossroads.nodes[0],
        a2_plan,
        approach_phases={1: "A", 3: "A", 5: "B", 7: "B"},
    )
    assert _only_refusal(result) == "signals_phase_never_assigned"
    assert result.refusals[0].context["unassigned_phase_ids"] == ["C"]


def test_refuses_an_assignment_naming_a_phase_the_plan_does_not_have(
    crossroads, a2_plan
):
    result = generate_node_signals(
        crossroads,
        crossroads.nodes[0],
        a2_plan,
        approach_phases={1: "A", 3: "A", 5: "B", 7: "D"},
    )
    assert _only_refusal(result) == "signals_assignment_names_unknown_phase"
    assert result.refusals[0].context["unknown_phase_ids"] == ["D"]


def test_refuses_an_assignment_naming_a_link_that_is_not_an_approach(
    crossroads, a2_plan
):
    result = generate_node_signals(
        crossroads,
        crossroads.nodes[0],
        a2_plan,
        approach_phases={**STANDARD_ASSIGNMENT, 99: "A"},
    )
    assert _only_refusal(result) == "signals_assignment_names_unknown_link"
    assert result.refusals[0].context["unknown_link_ids"] == [99]


def test_refuses_a_node_that_is_not_in_the_draft(crossroads, a2_plan):
    stranger = DraftNode(
        id=42, name="elsewhere", osm_id=42, x=0.0, y=0.0, polygon=[(0.0, 0.0)]
    )
    result = generate_node_signals(
        crossroads, stranger, a2_plan, approach_phases={1: "A"}
    )
    assert _only_refusal(result) == "signals_node_not_in_draft"


def test_refuses_a_node_with_no_approaches(a2_plan):
    node = DraftNode(id=1, name="n", osm_id=999, x=0.0, y=0.0, polygon=[(0.0, 0.0)])
    draft = DraftNetwork(
        name="lonely",
        source_osm="l.osm",
        links=[_link(1, "somewhere else", [(0.0, 10.0), (0.0, 0.0)])],
        nodes=[node],
        connectors=[],
    )
    result = generate_node_signals(draft, node, a2_plan, approach_phases={})
    assert _only_refusal(result) == "signals_node_has_no_approaches"


def test_refuses_when_a_phase_states_no_yellow(crossroads, a2_plan):
    """§5.4.2.1 requires yellow to come from controller/SCATS data. Vissim needs
    it as its own display, so a default would be inventing exactly the number
    the guideline says must be supplied."""
    a2_plan.program.phases[1].amber_s = None
    result = generate_node_signals(
        crossroads, crossroads.nodes[0], a2_plan, approach_phases=STANDARD_ASSIGNMENT
    )
    assert _only_refusal(result) == "signals_amber_unknown"


def test_refuses_when_the_intergreen_leaves_no_all_red(crossroads, a2_plan):
    """A.3 floors all-red at 1.0 s; at zero the red command also lands on the
    cycle boundary, where the read-back cannot tell it from the next cycle."""
    a2_plan.program.phases[1].amber_s = 5.0  # phase B's whole intergreen
    result = generate_node_signals(
        crossroads, crossroads.nodes[0], a2_plan, approach_phases=STANDARD_ASSIGNMENT
    )
    assert _only_refusal(result) == "signals_all_red_not_positive"


def test_refuses_a_turn_leaving_a_link_that_is_not_an_approach(crossroads, a2_plan):
    crossroads.connectors.append(
        DraftConnector(
            id=99999,
            name="ghost",
            node_osm_id=1,
            node_id=1,
            from_link=1234,
            to_link=1,
            from_pos_m=0.0,
            to_pos_m=0.0,
            lanes=1,
            lane_width_m=3.5,
            points=[(0.0, 0.0), (1.0, 1.0)],
            turn="through",
            heading_change_deg=0.0,
            lane_change="same",
        )
    )
    result = generate_node_signals(
        crossroads, crossroads.nodes[0], a2_plan, approach_phases=STANDARD_ASSIGNMENT
    )
    assert _only_refusal(result) == "signals_turn_from_unassigned_approach"


def test_every_declared_refuse_code_is_reachable_or_named():
    """``REFUSE_CODES`` is data, so it can drift from the code that raises. The
    module's own source is the check."""
    source = (
        REPO_ROOT / "backend" / "services" / "genesis" / "signals.py"
    ).read_text(encoding="utf-8")
    for code in REFUSE_CODES:
        assert source.count(f'"{code}"') >= 2, f"{code} is declared but never emitted"


def test_a_refused_node_emits_nothing_at_all(crossroads, a2_plan):
    """No half-controllers: a node that refused contributes findings and zero
    objects, and the containers stay empty."""
    refused = generate_node_signals(
        crossroads,
        crossroads.nodes[0],
        a2_plan,
        approach_phases={1: "A", 3: "A", 5: "B"},
    )
    signals = DraftSignals(nodes=(refused,))
    assert signals.emitting == ()
    assert signals_module.signal_containers(signals) == {}
    assert not signals.ok


# ---------------------------------------------------------------------------
# 7. Multi-node numbering
# ---------------------------------------------------------------------------


def test_head_and_detector_numbers_are_unique_across_controllers(crossroads, a2_plan):
    """``no`` is unique per CONTAINER in Vissim, not per controller, so the
    numbering is threaded across nodes."""
    second_node = DraftNode(
        id=2, name="n2", osm_id=2, x=500.0, y=0.0, polygon=[(0.0, 0.0)]
    )
    extra_links = [
        DraftLink(
            id=link_id,
            name=f"approach {link_id}",
            osm_way_id=link_id,
            segment_index=0,
            forward=True,
            lanes=1,
            lane_width_m=3.5,
            points=[(500.0, APPROACH_LENGTH_M), (500.0, 0.0)],
            lane_provenance={},
            highway="primary",
            from_node_osm_id=200 + link_id,
            to_node_osm_id=2,
            side_offset_m=0.0,
        )
        for link_id in (11, 13, 15)
    ]
    draft = crossroads.model_copy(
        update={
            "links": crossroads.links + extra_links,
            "nodes": crossroads.nodes + [second_node],
        }
    )
    result = generate_signals(
        draft,
        {
            1: (a2_plan, STANDARD_ASSIGNMENT),
            2: (a2_plan, {11: "A", 13: "B", 15: "C"}),
        },
    )
    assert result.ok
    assert [node.controller_no for node in result.nodes] == [1, 2]

    head_numbers = [head.no for node in result.nodes for head in node.heads]
    assert head_numbers == sorted(head_numbers)
    assert len(set(head_numbers)) == len(head_numbers) == 8  # 5 + 3
    detector_numbers = [d.no for node in result.nodes for d in node.detectors]
    assert len(set(detector_numbers)) == len(detector_numbers) == 8

    # Each controller's own port numbers restart at 1 — portNo is per-controller.
    for node in result.nodes:
        assert [d.port_no for d in node.detectors] == list(
            range(1, len(node.detectors) + 1)
        )


def test_generate_signals_reports_a_node_the_draft_does_not_have(crossroads, a2_plan):
    result = generate_signals(crossroads, {77: (a2_plan, {})})
    assert not result.ok
    assert result.issue_codes(IssueSeverity.REFUSE) == ["signals_node_not_in_draft"]


# ---------------------------------------------------------------------------
# 8. The compliance layer — U3's three states
# ---------------------------------------------------------------------------


def test_a_clean_run_reports_applied_and_not_checkable_and_no_violation(node_signals):
    """A node with nothing wrong grades in TWO states. The third is not padding
    — a violation appears only when something is actually violated, which is
    the next test."""
    states = {item.state for item in node_signals.compliance}
    assert states == {ComplianceState.APPLIED, ComplianceState.NOT_CHECKABLE}


def test_a_warning_becomes_a_named_violation(a2_plan):
    """U3's third state, reached by an actual defect: an approach nothing
    leaves. The violated item NAMES the codes rather than saying 'problems'."""
    links = [
        _link(1, "N", [(0.0, APPROACH_LENGTH_M), (0.0, 0.0)]),
        _link(3, "S", [(0.0, -APPROACH_LENGTH_M), (0.0, 0.0)]),
        _link(5, "E", [(APPROACH_LENGTH_M, 0.0), (0.0, 0.0)]),
        _link(7, "W", [(-APPROACH_LENGTH_M, 0.0), (0.0, 0.0)]),
    ]
    node = DraftNode(id=1, name="n", osm_id=1, x=0.0, y=0.0, polygon=[(0.0, 0.0)])
    draft = DraftNetwork(
        name="d", source_osm="d.osm", links=links, nodes=[node], connectors=[]
    )
    result = generate_node_signals(
        draft, node, a2_plan, approach_phases=STANDARD_ASSIGNMENT
    )
    states = {item.state for item in result.compliance}
    assert states == {
        ComplianceState.APPLIED,
        ComplianceState.VIOLATED,
        ComplianceState.NOT_CHECKABLE,
    }
    (violated,) = [
        item for item in result.compliance if item.state is ComplianceState.VIOLATED
    ]
    assert "signals_approach_has_no_turns" in violated.subjects


def test_section_5_4_2_1_group_numbering_is_never_claimed_as_applied(node_signals):
    """The honest grade of this module's central choice: a layout derived from
    OSM link ids is NOT the SCATS numbering §5.4.2.1 requires."""
    (item,) = [
        item
        for item in node_signals.compliance
        if item.rule_id == "5.4.2.1-signal-groups-match-scats"
    ]
    assert item.state is ComplianceState.NOT_CHECKABLE
    assert item.reason == "no_signal_plan"
    assert item.instances == 4
    assert item.evidence["layout"] == "one_group_per_approach"
    assert item.citation == signals_module.CIT_S5421_SIGNAL_GROUPS


def test_every_signal_compliance_item_renders_in_all_three_languages(node_signals):
    """A missing reason label or rule title is a KeyError at render time, not a
    quiet blank, so rendering every item in every language is the check."""
    for item in node_signals.compliance:
        for language in ("ko", "en", "ja"):
            rendered = item.render(language)
            assert rendered.strip()
            assert item.citation in rendered
            # The title must be a real title, never the raw rule id fallback.
            assert item.title(language) != item.rule_id


def test_signal_items_join_the_drafts_own_report(crossroads, node_signals):
    """U3 style: one report, not two. The items are ``ComplianceItem`` and drop
    straight into the draft's ``ComplianceReport``."""
    report = crossroads.compliance
    before = report.counts()
    report.items.extend(node_signals.compliance)
    after = report.counts()
    assert sum(after.values()) == sum(before.values()) + len(node_signals.compliance)
    assert any(item.section.startswith("5.4.2") for item in report.items)


def test_detector_compliance_disappears_when_no_detector_is_written(
    crossroads, a2_plan
):
    result = generate_node_signals(
        crossroads,
        crossroads.nodes[0],
        a2_plan,
        approach_phases=STANDARD_ASSIGNMENT,
        settings=SignalSettings(emit_detectors=False),
    )
    rules = {item.rule_id for item in result.compliance}
    assert not any(rule.startswith("5.4.2.6") for rule in rules)


# ---------------------------------------------------------------------------
# 9. Schema constants — verbatim from the networks Vissim SAVED
#
# Transcribed here as literals, exactly the way ``test_genesis_osm_draft.py``
# pins ``netPara`` and ``<node>``: the reference networks live under the
# git-ignored ``runtime/``, so a test that read them would pass or skip
# depending on the machine. This makes the measurement part of the repo.
# ---------------------------------------------------------------------------

#: Verbatim from ``ref_lht/Roundabout London_saved.inpx``
#: ``<signalController no="24" type="FIXEDTIME">``, minus ``no`` and the
#: ``intSupplyData`` payload (both supplied per controller). Controller 25 of
#: the same file is byte-identical on all of these.
VISSIM_SAVED_SIGNAL_CONTROLLER_ATTRS = {
    "active": "true",
    "cycTm": "0",
    "cycTmIsVar": "true",
    "debug": "false",
    "guiFile": "",
    "name": "",
    "offset": "0",
    "progFile": "",
    "progNo": "1",
    "scDetRecLabelDet": "PORTNUMBER",
    "scDetRecLabelSG": "NUMBER",
    "scDetRecShortNam": "false",
    "sigTmsTabAutoConfig": "true",
    "sigTmsTabLabelDet": "PORTNUMBER",
    "sigTmsTabLabelSG": "NUMBER",
    "supplyFile1": "",
    "supplyFile2": "",
    "supplyFile3": "",
    "type": "FIXEDTIME",
}

#: Verbatim from the same file's ``<signalController><rampMeterPara>`` —
#: identical on all three of its controllers.
VISSIM_SAVED_RAMP_METER_PARA = {
    "metEvalInt": "60",
    "metRateQDisp": "0.25",
    "occupDes": "0.25",
    "occupQDispAct": "0.5",
    "occupQDispDeact": "0.1",
    "regPara": "0.019444444444444441",
}

#: Verbatim from ``<signalController no="24"><sgs><signalGroup no="1">``. The
#: four computed fields (``no``, ``amber``, ``redAmber``, ``minGreen``) are
#: dropped; the rest is what the emitter must reproduce.
VISSIM_SAVED_SIGNAL_GROUP_ATTRS = {
    "actuationType": "UNDEFINED",
    "callDetPortNo": "0",
    "chkOutDetPortNo": "0",
    "clearTm": "0",
    "endGreen": "0",
    "endRed": "0",
    "greenFlsh": "0",
    "maxGreen": "0",
    "minRed": "0",
    "name": "",
    "type": "NORMAL",
}

#: Verbatim from ``<signalHeads><signalHead no="3">``, minus the five
#: identifying/positional fields (``no``, ``localNo``, ``lane``, ``pos``,
#: ``sg``). The attribute SET is identical on all 27 heads of that file.
VISSIM_SAVED_SIGNAL_HEAD_ATTRS = {
    "allPedTypes": "true",
    "allVehTypes": "true",
    "anmFlag": "false",
    "complRate": "1",
    "dischRecAct": "false",
    "isBlockSig": "false",
    "name": "",
    "scndSGTyp": "ORSG",
    "slowDownDist": "3",
    "type": "CIRCULAR",
    "vAmberBlock": "0",
}

#: Verbatim from ``<detectors><detector no="1">``, minus ``no``, ``portNo``,
#: ``lane``, ``pos``, ``length`` and ``sc``. Identical on both detectors of
#: that file. NOTE its ``length`` is 1.5 m — this emission uses §5.4.2.6's
#: 4.5 m instead, which is the deliberate split between Vissim's defaults and
#: MRWA's engineering numbers.
VISSIM_SAVED_DETECTOR_ATTRS = {
    "allPedTypes": "true",
    "allVehTypes": "true",
    "anmFlag": "true",
    "maxPedSpeed": "3.6",
    "name": "",
    "smthFactDecr": "0.25",
    "smthFactIncr": "0.25",
    "soundFile": "",
    "tmBefDep": "0",
    "type": "STANDARD",
}


@pytest.mark.parametrize(
    ("constant", "measured"),
    [
        (schema.SIGNAL_CONTROLLER_ATTRS, VISSIM_SAVED_SIGNAL_CONTROLLER_ATTRS),
        (schema.RAMP_METER_PARA_ATTRS, VISSIM_SAVED_RAMP_METER_PARA),
        (schema.SIGNAL_GROUP_ATTRS, VISSIM_SAVED_SIGNAL_GROUP_ATTRS),
        (schema.SIGNAL_HEAD_ATTRS, VISSIM_SAVED_SIGNAL_HEAD_ATTRS),
        (schema.DETECTOR_ATTRS, VISSIM_SAVED_DETECTOR_ATTRS),
    ],
    ids=["controller", "ramp_meter", "signal_group", "signal_head", "detector"],
)
def test_schema_constants_are_the_vissim_saved_ones(constant, measured):
    """Emitter v1 died of an attribute set invented from a hand-written
    exemplar. Nothing in this layer may drift from what Vissim itself wrote."""
    assert constant == measured


def test_the_emitted_elements_carry_exactly_the_measured_attribute_sets(
    tmp_path, crossroads, node_signals
):
    """Not just the constants — the ELEMENTS. A computed field that stopped
    being written, or a stray one that started, shows up here."""
    path = _emit(tmp_path, crossroads, node_signals)
    root = ET.parse(path).getroot()

    controller = root.find(".//signalControllers/signalController")
    assert set(controller.attrib) == set(VISSIM_SAVED_SIGNAL_CONTROLLER_ATTRS) | {
        "no",
        "intSupplyData",
    }
    assert controller.find("rampMeterPara").attrib == VISSIM_SAVED_RAMP_METER_PARA
    assert [child.tag for child in controller] == ["rampMeterPara", "sgs", "sigTmsTabConfig"]

    group = controller.find("sgs/signalGroup")
    assert set(group.attrib) == set(VISSIM_SAVED_SIGNAL_GROUP_ATTRS) | {
        "no",
        "amber",
        "redAmber",
        "minGreen",
    }

    head = root.find(".//signalHeads/signalHead")
    assert set(head.attrib) == set(VISSIM_SAVED_SIGNAL_HEAD_ATTRS) | {
        "no",
        "localNo",
        "lane",
        "pos",
        "sg",
    }
    detector = root.find(".//detectors/detector")
    assert set(detector.attrib) == set(VISSIM_SAVED_DETECTOR_ATTRS) | {
        "no",
        "portNo",
        "lane",
        "pos",
        "length",
        "sc",
    }


def test_signal_times_table_rows_reference_their_own_groups(
    tmp_path, crossroads, node_signals
):
    path = _emit(tmp_path, crossroads, node_signals)
    root = ET.parse(path).getroot()
    rows = root.findall(".//sigTmsTabConfig/signalOutputConfigurationElement")
    assert [row.get("sg") for row in rows] == ["1 1", "1 2", "1 3", "1 4"]
    assert [row.get("varNo") for row in rows] == ["1", "2", "3", "4"]
    assert {row.get("configName") for row in rows} == {"SIGGRP"}


def test_the_vissig_document_has_vissims_own_shape(node_signals):
    """Structure copied from the decoded ``intSupplyData`` of Vissim's own
    fixed-time controllers, including the three empty stage containers it
    always writes and the CRLF line endings it uses inside the blob."""
    document = build_vissig_document(node_signals)
    assert document.startswith('<?xml version="1.0" encoding="UTF-8"?>\r\n<sc ')
    assert "\r\n" in document
    for tag in ("<sgs>", "<intergreenmatrices />", "<progs>", "<stages />",
                "<interstageProgs />", "<stageProgs />"):
        assert tag in document
    inner = ET.fromstring(document)
    assert inner.get("frequency") == "1"
    assert inner.get("defaultIntergreenMatrix") == "0"
    assert [sg.get("defaultSignalSequence") for sg in inner.find("sgs")] == ["3"] * 4


def test_display_enum_matches_the_measured_values():
    assert (
        schema.DISPLAY_RED,
        schema.DISPLAY_RED_AMBER,
        schema.DISPLAY_GREEN,
        schema.DISPLAY_AMBER,
    ) == ("1", "2", "3", "4")
    assert schema.VISSIG_MS_PER_S == 1000
    assert schema.SIGNAL_SEQUENCE_VEHICLE == "3"


# ---------------------------------------------------------------------------
# 10. Citation traceability, and the counterfactual that proves it bites
# ---------------------------------------------------------------------------

#: Discovery finding FEWER constants than this would otherwise read as a pass.
#: §5.4.2 ×1, §5.4.2.1 ×2, §5.4.2.2 ×1, §5.4.2.3 ×2, §5.4.2.6 ×2.
MINIMUM_CITATIONS = 8


def _extract_text() -> str:
    return _normalise_prose((REPO_ROOT / EXTRACT).read_text(encoding="utf-8"))


def test_signal_citations_quote_the_section_5_extract_verbatim():
    constants = _citation_constants(signals_module)
    assert len(constants) >= MINIMUM_CITATIONS, f"only found {sorted(constants)}"
    for name, citation in sorted(constants.items()):
        assert EXTRACT in citation, f"{name} does not name the file it cites"

    unquotable, missing = _citation_traceability_failures(
        signals_module, EXTRACT, _extract_text()
    )
    assert unquotable == [], f"citations quoting nothing checkable: {unquotable}"
    assert missing == [], f"quoted in signals.py, absent from the extract: {missing}"


def test_signal_citation_locators_name_a_section_and_a_doc_page():
    import re

    for name, citation in sorted(_citation_constants(signals_module).items()):
        locator = citation.split(f"({EXTRACT})")[0]
        assert re.search(r"doc p\.\d{2,3}\s*$", locator.strip().rstrip(",")), name
        assert re.match(r"MRWA OMG v2\.0 §5(\.\d+)*\b", locator), name
        assert "Figure" not in locator, f"{name}: locator cites an in-text figure label"


def _citations_from_source(path: Path, name: str) -> ModuleType:
    """Load ONLY the ``CIT_*`` string constants out of a source FILE — the same
    technique the genesis draft test uses, for the same reason: ``signals.py``
    uses package-relative imports and cannot be imported from a scratch copy,
    but the comparison under test reads nothing except ``vars(module)``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    namespace: dict[str, str] = {"EXTRACT": EXTRACT}
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


def test_a_digit_swapped_signal_citation_fails_the_traceability_check(tmp_path):
    """The mutation counterfactual, run against a real copy of ``signals.py``.

    §5.4.2.6's "4.5m in length" becomes "5.5m in length" — the single most
    load-bearing number in this module, since it is the one engineering figure
    the module owns rather than reads off the plan. The comparison must then
    fail and name ``CIT_S5426_DETECTOR_FALLBACK``, and the untouched copy,
    loaded the identical way, must still pass.
    """
    source = REPO_ROOT / "backend" / "services" / "genesis" / "signals.py"
    text = source.read_text(encoding="utf-8")
    assert text.count("4.5m in length") == 1

    honest_copy = tmp_path / "signals_honest.py"
    honest_copy.write_text(text, encoding="utf-8")
    honest = _citations_from_source(honest_copy, "signals_honest")
    assert len(_citation_constants(honest)) >= MINIMUM_CITATIONS
    assert _citation_traceability_failures(honest, EXTRACT, _extract_text()) == ([], [])

    typo_copy = tmp_path / "signals_typo.py"
    typo_copy.write_text(text.replace("4.5m in length", "5.5m in length"), encoding="utf-8")
    corrupted = _citations_from_source(typo_copy, "signals_typo")
    unquotable, missing = _citation_traceability_failures(
        corrupted, EXTRACT, _extract_text()
    )
    assert unquotable == []
    assert [name for name, _fragment in missing] == ["CIT_S5426_DETECTOR_FALLBACK"]


def test_the_section_5_4_2_2_head_offset_miscite_stays_corrected():
    """§5.4.2.2 prescribes NO head offset. The "2.0 m" belongs to §5.4.2.6's
    DETECTORS, and the extract carries a bold note saying so. This module must
    never attach that number to a head."""
    extract = _extract_text()
    assert 'no "2.0 m / 4.5 m" appears in §5.4.2.2' in extract
    assert "§5.4.2.2 prescribes no signal-head position offset at all" in extract
    assert (
        "The 2.0 m / 4.5 m numbers are in §5.4.2.6 Detectors (below) and are detector "
        "defaults, not signal-head placement defaults" in extract
    )
    # And the head citation quotes only the two sentences §5.4.2.2 actually has:
    # neither detector number appears anywhere in it. (Compared against the
    # QUOTED text, not the whole constant — its locator legitimately contains
    # "v2.0".)
    quoted = signals_module.CIT_S5422_SIGNAL_HEADS.split(f"({EXTRACT})")[1]
    assert "2.0" not in quoted
    assert "4.5" not in quoted
    # …while the detector citation is where both numbers do live.
    assert "2.0m back from signal heads" in signals_module.CIT_S5426_DETECTOR_FALLBACK
    assert "4.5m in length" in signals_module.CIT_S5426_DETECTOR_FALLBACK


# ---------------------------------------------------------------------------
# 11. The round-trip's own honesty
# ---------------------------------------------------------------------------


def test_phases_closer_than_the_parsers_cluster_are_named(crossroads, a2_plan):
    """``signal_parser`` merges green starts within 5 s into one phase. When a
    plan has two that close, the round-trip claim would be false for it — and
    the module says so rather than letting a test pass on a coincidence."""
    program = a2_plan.program
    # Rebuild a plan whose phases B and C start 4 s apart.
    rows = list(a2_plan.rows)
    tight = a2_plan.__class__(
        controller_id=a2_plan.controller_id,
        source_program_id=a2_plan.source_program_id,
        program=program,
        complete_cycles=a2_plan.complete_cycles,
        stretch_phase_id=a2_plan.stretch_phase_id,
        average_cycle_s=a2_plan.average_cycle_s,
        plan_cycle_s=a2_plan.plan_cycle_s,
        round_up_residual_s=a2_plan.round_up_residual_s,
        rows=(
            rows[0],
            rows[1].__class__(**{**rows[1].__dict__, "start_offset_s": 41.0}),
            rows[2].__class__(**{**rows[2].__dict__, "start_offset_s": 45.0}),
        ),
        issues=a2_plan.issues,
    )
    result = generate_node_signals(
        crossroads, crossroads.nodes[0], tight, approach_phases=STANDARD_ASSIGNMENT
    )
    assert result.ok
    assert "signals_phase_starts_within_parser_cluster" in result.issue_codes(
        IssueSeverity.WARN
    )


def test_an_approach_with_no_onward_movement_is_named(a2_plan):
    """A signalised approach nothing leaves holds vehicles at a red that never
    releases anything — exactly the silent failure this project exists to
    catch."""
    links = [
        _link(1, "N", [(0.0, APPROACH_LENGTH_M), (0.0, 0.0)]),
        _link(3, "S", [(0.0, -APPROACH_LENGTH_M), (0.0, 0.0)]),
        _link(5, "E", [(APPROACH_LENGTH_M, 0.0), (0.0, 0.0)]),
        _link(7, "W", [(-APPROACH_LENGTH_M, 0.0), (0.0, 0.0)]),
    ]
    node = DraftNode(id=1, name="n", osm_id=1, x=0.0, y=0.0, polygon=[(0.0, 0.0)])
    draft = DraftNetwork(
        name="d", source_osm="d.osm", links=links, nodes=[node], connectors=[]
    )
    result = generate_node_signals(
        draft, node, a2_plan, approach_phases=STANDARD_ASSIGNMENT
    )
    assert result.ok
    warning = [
        issue
        for issue in result.issues
        if issue.code == "signals_approach_has_no_turns"
    ]
    assert warning and warning[0].context["link_ids"] == [1, 3, 5, 7]

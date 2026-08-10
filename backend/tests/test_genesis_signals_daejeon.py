"""Roadmap 3.1 Genesis (b), Korean half — the 대전 signal-sheet parser.

What this file proves, in the order it matters:

1. **A clean sheet parses completely**, and every number it produces is one a
   reviewer can re-derive from the fixture by hand.
2. **The layout variants are handled**: the firmware that prints the ``ID`` on
   the first row of a 시간분할 pair and the one that prints it on the second
   produce identical plans; the 감응제어 sub-columns are read and carried
   without being merged.
3. **The refusals fire**, one test per named shape, and a refused sheet produces
   NO controller rather than a partial one.
4. **The bridge refuses by default** — the sheet does not separate 황색 from
   전적색 — and produces a plan only under an explicit reviewer declaration.
5. **The bridged plan feeds ``signals.generate_node_signals``** and comes out
   the other side as green windows equal to the fixture's own arithmetic.
6. **Nothing is invented**: every field the sheet does not state is ``None``
   with a reason in ``absent``.

Every fixture under ``tests/fixtures/daejeon_signal/`` is SYNTHETIC — authored
for this test, mimicking the character grid pypdf's layout extractor produces.
No test here reads ``client-data/``, and none needs pypdf, Vissim or a licence.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.dataimport.base import IssueSeverity
from services.dataimport.schema import ProgramSourceKind
from services.genesis import signals_daejeon as dj
from services.genesis.draft import DraftConnector, DraftLink, DraftNetwork, DraftNode
from services.genesis.signals import generate_node_signals

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "daejeon_signal"

#: The clean fixture's own figures, written out by hand so a change in the
#: parser fails here rather than quietly redefining what "correct" means.
CLEAN_CYCLE_S = 120.0
CLEAN_PLAN1_SPLITS = (30.0, 30.0, 20.0, 20.0, 20.0)
CLEAN_PLAN2_SPLITS = (40.0, 20.0, 20.0, 20.0, 20.0)
CLEAN_YELLOW_RED_S = 5.0
#: 30+30+20+20 running totals — the phase starts the bridge must produce.
CLEAN_PLAN1_STARTS = (0.0, 30.0, 60.0, 80.0, 100.0)
#: split − YELLOW(RED), phase by phase.
CLEAN_PLAN1_GREENS = (25.0, 25.0, 15.0, 15.0, 15.0)

#: A reviewer declaration, invented for the test. It is NOT a claim about the
#: real documents — the whole point of the module is that nobody has answered
#: the question this value stands in for.
DECLARED = dj.DaejeonSettings(
    declared_amber_s=3.0, amber_declaration_note="test fixture declaration"
)


def load(name: str) -> dj.DaejeonSheet:
    path = FIXTURES / name
    return dj.parse_sheet_text(path.read_text(encoding="utf-8"), source_file=name)


@pytest.fixture
def clean() -> dj.DaejeonSheet:
    return load("clean_five_phase.txt")


# ---------------------------------------------------------------------------
# 1. A clean sheet, read completely
# ---------------------------------------------------------------------------


def test_clean_sheet_parses_identity_and_structure(clean):
    assert clean.ok
    assert clean.refusals == ()
    assert clean.intersection_no == "9001"
    assert clean.intersection_name == "가상클린네거리"
    assert clean.group_raw == "SA 900"
    assert clean.controller_model == "ZZ-1000S"
    assert clean.revised_at_raw == "2099-01-01 00:00"
    assert clean.phase_count == 5


def test_clean_sheet_reads_the_signal_table_by_column_not_by_order(clean):
    """The blank cells are the point.

    ``BEF. PED.`` is blank on RING A for Φ1/Φ3/Φ5 and blank on RING B for
    Φ2/Φ4. Read by ORDER, ring A's two values would land on Φ1 and Φ2 — this
    asserts they land on Φ2 and Φ4, which only column-position attribution can
    do. It is the check that would have caught the whole class of silent
    misassignment the module's docstring warns about.
    """
    timings = {t.phase_no: t for t in clean.phases}
    assert sorted(timings) == [1, 2, 3, 4, 5]
    assert [timings[n].yellow_red_s for n in range(1, 6)] == [CLEAN_YELLOW_RED_S] * 5
    assert [timings[n].min_green_s for n in range(1, 6)] == [25.0, 25.0, 15.0, 15.0, 15.0]
    assert [timings[n].max_green_s for n in range(1, 6)] == [60.0, 60.0, 40.0, 40.0, 40.0]
    # BEF. PED. is stated by exactly one ring per phase and never for Φ5.
    assert [timings[n].before_ped_s for n in range(1, 6)] == [2.0, 2.0, 2.0, 2.0, None]
    assert [timings[n].walk_s for n in range(1, 6)] == [10.0, 10.0, 8.0, 8.0, None]


def test_clean_sheet_reads_the_two_plan_table_lookups_independently(clean):
    """수준 → 주기 and ID → (연동, 시간분할) are separate tables.

    The TOD rows are what pair them, and a level printed beside a plan id means
    nothing. Asserted by reading each table on its own terms.
    """
    assert [(lv.level_no, lv.cycle_s) for lv in clean.levels] == [
        (1, CLEAN_CYCLE_S), (2, CLEAN_CYCLE_S), (3, CLEAN_CYCLE_S)
    ]
    plans = {p.plan_id: p for p in clean.plans}
    assert sorted(plans) == [1, 2, 3]
    assert plans[1].splits_ring_a == CLEAN_PLAN1_SPLITS
    assert plans[1].splits_ring_b == CLEAN_PLAN1_SPLITS
    assert plans[1].offset_s == 10.0
    assert plans[2].splits_ring_a == CLEAN_PLAN2_SPLITS
    assert plans[2].offset_s == 20.0
    assert plans[3].offset_s == 0.0, "a printed 0 is a stated zero, not an absence"
    assert plans[1].rings_agree and not plans[1].has_empty_field


def test_clean_sheet_reads_every_tod_table_and_the_weekly_plan(clean):
    by_plan = {}
    for entry in clean.tod_entries:
        by_plan.setdefault(entry.tod_plan_no, []).append(
            (entry.clock, entry.level_no, entry.plan_id)
        )
    assert by_plan[1] == [("00:00", 1, 1), ("07:00", 2, 2), ("21:00", 3, 3)]
    assert by_plan[2] == [("00:00", 1, 1), ("07:00", 2, 2), ("21:00", 3, 3)]
    assert by_plan[3] == [("00:00", 1, 1), ("21:00", 3, 3)]
    assert [d.weekday for d in clean.day_assignments] == list(dj.WEEKDAYS)
    assert [(d.tod_plan_no, d.time_plan_no) for d in clean.day_assignments][0] == (3, 1)


def test_clean_sheet_emits_one_program_per_scheduled_level_plan_pair(clean):
    controller = clean.controller
    assert controller is not None
    assert controller.controller_id == "9001"
    assert controller.region == dj.REGION_JURISDICTION
    # (1,1), (2,2), (3,3) — three distinct pairs, however many TOD tables
    # select them.
    assert [p.program_id for p in controller.programs] == ["L1-P1", "L2-P2", "L3-P3"]
    assert all(p.source_kind is ProgramSourceKind.DECLARED for p in controller.programs)
    program = controller.program_by_id("L1-P1")
    assert program.cycle_time_s == CLEAN_CYCLE_S
    assert program.offset_s == 10.0
    assert [p.phase_id for p in program.phases] == [f"Φ{n}" for n in range(1, 6)]


def test_a_pair_the_tod_table_never_selects_is_not_emitted_as_a_program(clean):
    """A (수준, ID) combination the controller never runs is a spare.

    The fixture defines levels 1–3 and plans 1–3 — nine possible pairings — and
    schedules three. Emitting the other six would claim the intersection runs
    signal plans it does not.
    """
    ids = {p.program_id for p in clean.controller.programs}
    assert "L1-P2" not in ids and "L3-P1" not in ids
    assert len(ids) == 3


# ---------------------------------------------------------------------------
# 2. Layout variants
# ---------------------------------------------------------------------------


def test_the_id_on_second_row_layout_produces_identical_plans(clean):
    """The ``ID``/``연동`` label is vertically centred between the two 시간분할
    rows, so it lands on the first row in one firmware and the second in
    another. Both must bind the same split vector to the same plan id — the
    alternative is one intersection silently getting another plan's timings."""
    variant = load("clean_five_phase_id_on_second_row.txt")
    assert variant.ok
    assert [(p.plan_id, p.offset_s, p.splits_ring_a) for p in variant.plans] == [
        (p.plan_id, p.offset_s, p.splits_ring_a) for p in clean.plans
    ]
    assert [pr.program_id for pr in variant.controller.programs] == [
        pr.program_id for pr in clean.controller.programs
    ]


def test_actuated_sub_columns_are_read_and_carried_but_never_merged():
    sheet = load("actuated_variant.txt")
    assert sheet.ok
    assert "daejeon_actuated_split_variant_present" in sheet.issue_codes(IssueSeverity.WARN)
    plan = sheet.plan(1)
    assert plan.splits_ring_a == (30.0, 30.0, 40.0, 20.0)  # the BASE split, untouched
    assert plan.actuated_splits == (("압버튼", (35.0, 25.0, 40.0, 20.0)),)
    # …and the program the controller emits carries the base split, not the variant.
    program = sheet.controller.program_by_id("L1-P1")
    assert [p.min_green_s for p in program.phases] == [25.0, 25.0, 35.0, 15.0]


def test_an_empty_split_field_is_preserved_positionally_not_compacted():
    """``30,30,,20,20,20`` is six positions with the third unrun.

    Compacting it to five values would move 20 s of green from Φ4 onto Φ3 —
    a wrong number Vissim would run without a murmur.
    """
    sheet = load("empty_split_field.txt")
    assert sheet.ok
    assert "daejeon_split_field_empty" in sheet.issue_codes(IssueSeverity.WARN)
    plan = sheet.plan(1)
    assert plan.splits_ring_a == (30.0, 30.0, None, 20.0, 20.0, 20.0)
    assert plan.has_empty_field
    # The unrun phase contributes no program phase…
    program = sheet.controller.program_by_id("L1-P1")
    assert [p.phase_id for p in program.phases] == ["Φ1", "Φ2", "Φ4", "Φ5", "Φ6"]
    # …and in the bridge it becomes a DROPPED row, which
    # generate_node_signals filters out of the assignable phase set.
    fixed, _ = dj.to_fixed_time_plan(sheet, level_no=1, plan_id=1, settings=DECLARED)
    assert fixed is not None
    dropped = [r for r in fixed.rows if r.dropped]
    assert [r.phase_id for r in dropped] == ["Φ3"]
    assert dropped[0].green_s is None and dropped[0].drop_reason


# ---------------------------------------------------------------------------
# 3. Refusals — a refused sheet produces NO controller
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture, code",
    [
        ("not_a_signal_sheet.txt", "daejeon_sheet_not_recognised"),
        ("split_sum_mismatch.txt", "daejeon_split_sum_mismatch"),
        ("tod_selects_empty_level.txt", "daejeon_tod_references_unknown_level"),
        ("empty_split_field_count_mismatch.txt", "daejeon_split_count_mismatch"),
    ],
)
def test_a_malformed_sheet_is_refused_with_a_named_reason(fixture, code):
    sheet = load(fixture)
    assert not sheet.ok
    assert code in sheet.issue_codes(IssueSeverity.REFUSE)
    assert code in dj.REFUSE_CODES
    assert sheet.controller is None, "a refused sheet must produce no controller at all"


def test_the_split_sum_refusal_names_the_arithmetic_a_reviewer_would_redo():
    sheet = load("split_sum_mismatch.txt")
    issue = next(
        i for i in sheet.refusals if i.code == "daejeon_split_sum_mismatch"
    )
    assert issue.context["cycle_s"] == CLEAN_CYCLE_S
    assert issue.context["split_sum_s"] == 125.0  # 30+30+20+20+25
    assert issue.context["plan_id"] == 1


def test_a_missing_field_never_becomes_a_default():
    """Φ3 states no YELLOW(RED) in either ring.

    The sheet still parses — the other four phases are fine — but the phase's
    clearance stays ``None``, is reported, and blocks the bridge. A parser that
    filled in a plausible 3 s here would produce a signal plan that runs.
    """
    sheet = load("missing_yellow.txt")
    assert sheet.ok
    assert "daejeon_phase_yellow_missing" in sheet.issue_codes(IssueSeverity.WARN)
    assert sheet.phase(3).yellow_red_s is None
    assert not sheet.fully_parsed, "a sheet with a hole is partially parsed, not fully"

    plan, issues = dj.to_fixed_time_plan(sheet, level_no=1, plan_id=1, settings=DECLARED)
    assert plan is None
    codes = {i.code for i in issues if i.severity is IssueSeverity.REFUSE}
    assert codes == {"daejeon_phase_yellow_unknown"}


def test_rings_that_state_different_numbers_leave_the_value_unstated():
    """The neutral model has one phase list and no ring axis. Taking ring A's
    number would describe a controller that is not the one installed."""
    sheet = load("ring_values_disagree.txt")
    assert sheet.ok
    assert "daejeon_ring_values_disagree" in sheet.issue_codes(IssueSeverity.WARN)
    assert sheet.phase(2).min_green_s is None
    assert "MIN. GREEN" in sheet.phase(2).rings_disagree
    assert sheet.phase(1).min_green_s == 25.0  # the agreeing phases are unaffected


def test_a_group_that_is_not_in_sa_form_is_kept_verbatim_and_flagged():
    sheet = load("group_without_sa.txt")
    assert sheet.ok
    assert sheet.group_raw == "77"
    assert "daejeon_group_not_in_sa_form" in sheet.issue_codes(IssueSeverity.WARN)


def test_the_memo_note_that_changes_pedestrian_arithmetic_is_surfaced():
    sheet = load("memo_yellow_included.txt")
    assert sheet.memo_notes
    assert "daejeon_memo_alters_pedestrian_timing" in sheet.issue_codes(IssueSeverity.WARN)


# ---------------------------------------------------------------------------
# 4. The bridge — refused by default, allowed only on a recorded declaration
# ---------------------------------------------------------------------------


def test_the_bridge_refuses_by_default_because_the_sheet_states_one_clearance(clean):
    """The finding, not a bug.

    The form has a single ``YELLOW(RED)`` row and no 전적색 row. Vissim needs
    the amber as its own display; §5.4.2.1 forbids substituting a default. So
    the default path produces no plan and says exactly why.
    """
    plan, issues = dj.to_fixed_time_plan(clean, level_no=1, plan_id=1)
    assert plan is None
    codes = {i.code for i in issues if i.severity is IssueSeverity.REFUSE}
    assert codes == {"daejeon_intergreen_split_unstated"}
    issue = next(i for i in issues if i.code == "daejeon_intergreen_split_unstated")
    assert issue.context["blocked_only_by_amber"] is True
    assert issue.citation == dj.CIT_S5421_TIMINGS_UNCHANGED


def test_the_amber_gate_runs_after_every_amber_independent_check():
    """A broken sheet must report what is broken, not hide behind the amber.

    ``missing_yellow`` has no usable clearance on Φ3, which is a defect the
    declaration cannot fix; the refusal names that and not the declaration.
    """
    sheet = load("missing_yellow.txt")
    _, issues = dj.to_fixed_time_plan(sheet, level_no=1, plan_id=1)
    codes = {i.code for i in issues if i.severity is IssueSeverity.REFUSE}
    assert codes == {"daejeon_phase_yellow_unknown"}
    assert "daejeon_intergreen_split_unstated" not in codes


def test_a_declared_amber_produces_a_plan_whose_arithmetic_is_re_derivable(clean):
    plan, issues = dj.to_fixed_time_plan(
        clean, level_no=1, plan_id=1, settings=DECLARED
    )
    assert plan is not None and plan.ok
    assert plan.plan_cycle_s == CLEAN_CYCLE_S
    assert plan.controller_id == "9001"
    assert plan.source_program_id == "L1-P1"

    rows = {row.phase_id: row for row in plan.rows}
    assert [rows[f"Φ{n}"].start_offset_s for n in range(1, 6)] == list(CLEAN_PLAN1_STARTS)
    assert [rows[f"Φ{n}"].green_s for n in range(1, 6)] == list(CLEAN_PLAN1_GREENS)
    assert [rows[f"Φ{n}"].intergreen_s for n in range(1, 6)] == [CLEAN_YELLOW_RED_S] * 5
    # green + clearance closes the phase, and the phases close the cycle.
    assert sum(rows[f"Φ{n}"].rounded_phase_s for n in range(1, 6)) == CLEAN_CYCLE_S

    phases = {p.phase_id: p for p in plan.program.phases}
    assert all(p.amber_s == 3.0 for p in phases.values())
    assert all(p.all_red_s == CLEAN_YELLOW_RED_S - 3.0 for p in phases.values())
    # …and every one of them says it was computed, and under whose declaration.
    assert all("reviewer declaration" in p.provenance.derived_by for p in phases.values())

    warn = {i.code for i in issues if i.severity is IssueSeverity.WARN}
    assert "daejeon_coordination_offset_not_applied" in warn


def test_a_declaration_that_contradicts_the_sheet_is_refused(clean):
    """An amber no shorter than the sheet's clearance leaves a zero all-red.

    Either the declaration is wrong or ``YELLOW(RED)`` is the yellow alone — in
    which case the all-red is genuinely unstated. Both refuse.
    """
    settings = dj.DaejeonSettings(declared_amber_s=CLEAN_YELLOW_RED_S)
    plan, issues = dj.to_fixed_time_plan(clean, level_no=1, plan_id=1, settings=settings)
    assert plan is None
    assert {i.code for i in issues if i.severity is IssueSeverity.REFUSE} == {
        "daejeon_all_red_not_positive"
    }


def test_rings_running_different_splits_refuse_rather_than_emitting_one_ring():
    sheet = load("ring_splits_disagree.txt")
    assert sheet.ok
    plan, issues = dj.to_fixed_time_plan(sheet, level_no=1, plan_id=1, settings=DECLARED)
    assert plan is None
    assert {i.code for i in issues if i.severity is IssueSeverity.REFUSE} == {
        "daejeon_ring_splits_disagree"
    }


def test_an_unscheduled_level_plan_pair_is_refused_by_the_bridge(clean):
    plan, issues = dj.to_fixed_time_plan(clean, level_no=1, plan_id=2, settings=DECLARED)
    assert plan is None
    assert "daejeon_program_not_found" in {i.code for i in issues}


# ---------------------------------------------------------------------------
# 5. The bridge really does feed services.genesis.signals
# ---------------------------------------------------------------------------


APPROACH_LENGTH_M = 100.0


def _link(link_id: int, name: str, points):
    return DraftLink(
        id=link_id,
        name=name,
        osm_way_id=link_id,
        segment_index=0,
        forward=True,
        lanes=1,
        lane_width_m=3.5,
        points=points,
        lane_provenance={},
        highway="primary",
        from_node_osm_id=100 + link_id,
        to_node_osm_id=1,
        side_offset_m=0.0,
    )


@pytest.fixture
def crossroads() -> DraftNetwork:
    links = [
        _link(1, "North approach", [(0.0, APPROACH_LENGTH_M), (0.0, 0.0)]),
        _link(3, "South approach", [(0.0, -APPROACH_LENGTH_M), (0.0, 0.0)]),
        _link(5, "East approach", [(APPROACH_LENGTH_M, 0.0), (0.0, 0.0)]),
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
        id=1, name="n1", osm_id=1, x=0.0, y=0.0,
        polygon=[(-15.0, -15.0), (15.0, -15.0), (15.0, 15.0), (-15.0, 15.0)],
    )
    return DraftNetwork(
        name="crossroads", source_osm="crossroads.osm",
        links=links, nodes=[node], connectors=connectors,
    )


def test_a_bridged_plan_becomes_signal_groups_with_the_fixtures_own_numbers(crossroads):
    """End to end, offline: sheet text → neutral controller → FixedTimePlan →
    ``generate_node_signals`` → green windows.

    The four-phase fixture states splits 30/30/40/20 in a 120 s cycle with a
    5 s clearance, so with a declared 3 s amber the windows are pure
    subtraction: greens 25/25/35/15 starting at 0/30/60/100, each followed by
    3 s of amber and 2 s of all-red.

    This is an OFFLINE round trip. It proves the objects are self-consistent
    and that the interface matches; it does NOT prove PTV Vissim loads the
    result — that needs the live check, and this project has already been
    bitten by the difference.
    """
    sheet = load("actuated_variant.txt")
    plan, _ = dj.to_fixed_time_plan(sheet, level_no=1, plan_id=1, settings=DECLARED)
    assert plan is not None

    result = generate_node_signals(
        crossroads,
        crossroads.nodes[0],
        plan,
        approach_phases={1: "Φ1", 3: "Φ2", 5: "Φ3", 7: "Φ4"},
    )
    assert result.ok, result.issue_codes(IssueSeverity.REFUSE)
    assert result.cycle_s == CLEAN_CYCLE_S
    assert [g.green_start_s for g in result.groups] == [0.0, 30.0, 60.0, 100.0]
    assert [g.green_s for g in result.groups] == [25.0, 25.0, 35.0, 15.0]
    assert [g.amber_s for g in result.groups] == [3.0] * 4
    assert [g.all_red_s for g in result.groups] == [2.0] * 4
    # The last group's red begins at 100+15+3 = 118, inside the 120 s cycle.
    assert result.groups[-1].red_start_s == 118.0


# ---------------------------------------------------------------------------
# 6. Never invent — the absences are recorded, not filled
# ---------------------------------------------------------------------------


def test_the_neutral_controller_states_no_green_no_amber_and_no_all_red(clean):
    """Before any declaration, the sheet supports none of the three.

    ``intergreen_s`` IS stated (the YELLOW(RED) row) and is carried; the split
    of it into amber and all-red is not, and neither is the green that depends
    on it. Each absence carries its own reason.
    """
    program = clean.controller.program_by_id("L1-P1")
    for phase in program.phases:
        assert phase.green_s is None
        assert phase.amber_s is None
        assert phase.all_red_s is None
        assert phase.intergreen_s == CLEAN_YELLOW_RED_S
        assert set(phase.absent) >= {"green_s", "amber_s", "all_red_s"}
        assert all(reason.strip() for reason in phase.absent.values())


def test_the_controller_records_why_it_has_no_signal_groups_or_control_type(clean):
    controller = clean.controller
    assert controller.control_type is None
    assert controller.signal_groups == []
    assert "control_type" in controller.absent
    assert "signal_groups" in controller.absent
    assert "현시도" in controller.absent["signal_groups"]
    assert "daejeon_phase_movement_diagram_not_text" in clean.issue_codes(IssueSeverity.INFO)


def test_every_neutral_record_cites_where_it_came_from(clean):
    controller = clean.controller
    assert controller.provenance.source_file == "clean_five_phase.txt"
    for program in controller.programs:
        assert program.provenance.locator
        for phase in program.phases:
            assert phase.provenance.locator and phase.provenance.native_id
            # read, not computed — the un-bridged controller derives nothing
            assert phase.provenance.derived_by is None


def test_an_empty_level_slot_is_an_observation_not_a_refusal(clean):
    """The form prints six 수준 slots whether or not the site uses them."""
    assert "daejeon_level_slot_empty" in clean.issue_codes(IssueSeverity.INFO)
    assert clean.ok
    assert [lv.level_no for lv in clean.levels] == [1, 2, 3]


# ---------------------------------------------------------------------------
# 7. Contract checks on the module itself
# ---------------------------------------------------------------------------


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "services" / "genesis" / "signals_daejeon.py"
).read_text(encoding="utf-8")


def test_every_refuse_code_in_the_source_is_declared_in_a_refuse_set():
    """The docstrings claim "these produce nothing". Kept honest as data.

    Any ``IssueSeverity.REFUSE`` raised with a code not in ``REFUSE_CODES`` or
    ``BRIDGE_REFUSE_CODES`` is a refusal nobody downstream can branch on.
    """
    raised = set(
        re.findall(
            r"IssueSeverity\.REFUSE,\s*\n\s*\"([a-z0-9_]+)\"", SOURCE
        )
    )
    assert raised, "the regex found no refusals — it has gone stale"
    undeclared = raised - dj.REFUSE_CODES - dj.BRIDGE_REFUSE_CODES
    assert undeclared == set()


def test_every_refusal_carries_ko_en_and_ja(clean):
    """ko primary, en/ja alongside — the house rule for user-facing strings."""
    sheets = [load(name) for name in (
        "not_a_signal_sheet.txt", "split_sum_mismatch.txt",
        "tod_selects_empty_level.txt", "empty_split_field_count_mismatch.txt",
    )]
    bridge_issues = []
    for level_no, plan_id, settings in (
        (1, 1, None),
        (1, 1, dj.DaejeonSettings(declared_amber_s=CLEAN_YELLOW_RED_S)),
    ):
        _, issues = dj.to_fixed_time_plan(
            clean, level_no=level_no, plan_id=plan_id, settings=settings
        )
        bridge_issues.extend(issues)
    every = [i for s in sheets for i in s.refusals] + [
        i for i in bridge_issues if i.severity is IssueSeverity.REFUSE
    ]
    assert every
    for issue in every:
        assert set(issue.messages) >= {"ko", "en", "ja"}, issue.code
        assert all(text.strip() for text in issue.messages.values()), issue.code


def test_the_module_never_imports_vissim_or_com():
    """Offline by construction, like the rest of ``services.genesis``.

    The docstrings talk about Vissim constantly — that is the point of the
    module — so the check is on the IMPORTS, not on the prose.
    """
    imports = re.findall(r"^\s*(?:from|import)\s+\S+", SOURCE, flags=re.MULTILINE)
    forbidden = [
        line
        for line in imports
        if any(token in line.lower() for token in ("win32", "vissim_com", "pythoncom"))
    ]
    assert forbidden == []


def test_pypdf_is_only_touched_inside_the_text_reader():
    """The parser's entry point takes TEXT. Every test in this file, and every
    consumer that already has extracted text, must work with no PDF stack at
    all — so ``pypdf`` may only be imported lazily inside
    :func:`read_sheet_text`."""
    assert "\nfrom pypdf" not in SOURCE and "\nimport pypdf" not in SOURCE
    reader = SOURCE.split("def read_sheet_text")[1].split("\ndef ")[0]
    assert "from pypdf import PdfReader" in reader


# ---------------------------------------------------------------------------
# 8. Coverage — the measurement is a number, and it does not flatter itself
# ---------------------------------------------------------------------------


def test_coverage_separates_parsed_from_vissim_ready():
    sheets = [load(p.name) for p in sorted(FIXTURES.glob("*.txt"))]
    measured = dj.coverage(sheets)
    assert measured.sheets == len(sheets)
    assert measured.refused == 4  # the four malformed fixtures
    assert measured.parsed == measured.sheets - measured.refused
    assert measured.fully_parsed + measured.partially_parsed == measured.parsed
    # With no declaration NOTHING is Vissim-ready, and the table says so
    # instead of quietly reporting the parse rate as if it were the same thing.
    assert measured.bridge_ready == 0
    assert measured.bridge_blocked_only_by_amber > 0
    assert dict(measured.refusal_reasons)["daejeon_split_sum_mismatch"] == 1


def test_coverage_reports_plans_once_a_reviewer_declares_the_amber():
    sheets = [load(p.name) for p in sorted(FIXTURES.glob("*.txt"))]
    measured = dj.coverage(sheets, settings=DECLARED)
    assert measured.bridge_ready > 0
    assert measured.bridge_blocked_only_by_amber == 0

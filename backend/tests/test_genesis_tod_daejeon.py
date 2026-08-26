"""Unit #70 through the 대전 sheet — the AUTHORITY route.

The typed (``excel_upload``) route is a reviewer re-keying numbers; the sheet is
where the numbers come from, and it is the only route whose sibling plans are
DERIVED rather than supplied. It had no coverage at all in the first round, and
the first test written against the real fixture immediately found a defect: every
program of ``clean_five_phase.txt`` states its clock under all three TOD plans
(``"TOD1 00:00, TOD2 00:00, TOD3 00:00"``), and the parser refused every one of
them for "spanning multiple day plans" — which would have made the whole feature
unreachable on its own source data while the typed-route tests stayed green.

These tests run against the fixture the 대전 adapter's own suite uses, so a change
to the sheet parser that breaks TOD derivation fails here rather than in a
synthetic document that agrees with the code.
"""

from __future__ import annotations

import base64
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from services.dataimport.base import IssueSeverity
from services.genesis import side_inputs, signals_daejeon as dj, tod_schedule as tod
from services.genesis.compose import compose_layers
from services.genesis.side_inputs import SIGNALS_DOC_KIND, parse_side_input
from services.genesis.signals import build_int_supply_data

from tests.test_genesis_signals import crossroads  # noqa: F401  (pytest fixture)

FIXTURES = Path(__file__).parent / "fixtures" / "daejeon_signal"

#: The crossroads draft's approach→phase map, over the sheet's own Φ ids.
SHEET_APPROACH_PHASES = {1: "Φ1", 3: "Φ2", 5: "Φ3", 7: "Φ4"}

#: Three programs of FOUR phases — the shape a four-approach draft can carry.
ROUND_TRIP_SHEET = "actuated_variant.txt"


def sheet_text(name: str = "clean_five_phase.txt") -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _assignment(name: str = "clean_five_phase.txt", *, tod_block=None, **overrides):
    assignment = {
        "node_id": 1,
        "plan": {
            "source": "daejeon_sheet",
            "sheet_text": sheet_text(name),
            "source_file": name,
            "level_no": 1,
            "plan_id": 1,
            "declared_amber_s": 3.0,
        },
        "approach_phases": {str(k): v for k, v in SHEET_APPROACH_PHASES.items()},
    }
    if tod_block is not None:
        assignment["tod"] = tod_block
    assignment.update(overrides)
    return assignment


def _build(assignment):
    document = {"kind": SIGNALS_DOC_KIND, "assignments": [assignment]}
    parsed = parse_side_input(
        "signals", json.dumps(document, ensure_ascii=False).encode("utf-8")
    )
    return side_inputs.build_signal_assignments(parsed)


@pytest.fixture
def sheet():
    return dj.parse_sheet_text(sheet_text(), source_file="clean_five_phase.txt")


# ---------------------------------------------------------------------------
# What the sheet actually says
# ---------------------------------------------------------------------------


def test_the_fixture_really_carries_several_scheduled_programs(sheet):
    """The premise of the whole route: one sheet, several (수준, 계획 ID) pairs,
    each with the clock times that select it."""
    assert sheet.controller is not None
    programs = {p.program_id: p.time_of_day for p in sheet.controller.programs}
    assert programs == {
        "L1-P1": "TOD1 00:00, TOD2 00:00, TOD3 00:00",
        "L2-P2": "TOD1 07:00, TOD2 07:00",
        "L3-P3": "TOD1 21:00, TOD2 21:00, TOD3 21:00",
    }


def test_day_types_that_agree_on_the_clock_are_not_a_refusal(sheet):
    """The defect this file found. ``TOD1 00:00, TOD2 00:00, TOD3 00:00`` names
    three day types and ONE time, so there is no day to choose between."""
    for program in sheet.controller.programs:
        windows, issues = tod.parse_tod_phrase(
            program.time_of_day, program_key=program.program_id, subject="s"
        )
        assert [i.code for i in issues] == [], program.program_id
        assert len(windows) == 1
        assert windows[0].day_plan_no is None  # the tag is dropped once they agree


def test_day_types_that_disagree_on_the_clock_still_refuse():
    """The rule is unchanged where it was doing real work."""
    _, issues = tod.parse_tod_phrase(
        "TOD1 21:00, TOD2 06:00", program_key="L1-P1", subject="s"
    )
    assert [i.code for i in issues] == ["tod_phrase_spans_multiple_day_plans"]


def test_the_sheet_s_own_programs_tile_a_day(sheet):
    candidates = []
    for program in sheet.controller.programs:
        windows, _ = tod.parse_tod_phrase(
            program.time_of_day, program_key=program.program_id, subject="s"
        )
        candidates.append(
            tod.TodCandidate(
                program_key=program.program_id, phrase=program.time_of_day, windows=windows
            )
        )
    schedule, issues = tod.build_tod_schedule(candidates, subject="s")
    assert schedule is not None
    assert [(e.program_key, e.start_clock) for e in schedule.entries] == [
        ("L1-P1", "00:00"),
        ("L2-P2", "07:00"),
        ("L3-P3", "21:00"),
    ]
    assert [i.code for i in issues] == []


# ---------------------------------------------------------------------------
# Derivation through the side-input door
# ---------------------------------------------------------------------------


def test_the_sheet_route_derives_its_siblings_without_being_given_any():
    """No ``additional_plans`` anywhere: the schedule and every plan in it come
    out of the one ``sheet_text`` the confirmation already carried."""
    build = _build(_assignment(tod_block={"enabled": True}))
    assert 1 in build.tod_plans
    request = build.tod_plans[1]
    assert request.primary_program_key == "L1-P1"
    assert request.schedule.program_keys == ("L1-P1", "L2-P2", "L3-P3")
    assert set(request.plans) == {"L1-P1", "L2-P2", "L3-P3"}


def test_every_derived_sibling_is_a_real_plan_built_by_the_sheet_s_own_converter():
    build = _build(_assignment(tod_block={"enabled": True}))
    for key, plan in build.tod_plans[1].plans.items():
        assert plan.ok, key
        assert plan.program is not None, key
        assert plan.plan_cycle_s and plan.plan_cycle_s > 0, key


def test_the_program_key_regex_matches_what_the_sheet_writes():
    """``_PROGRAM_KEY`` is how a program id becomes the (수준, 계획 ID) pair the
    sheet converter is re-invoked with. A sheet id it cannot parse is skipped, so
    a drift between the two spellings would silently empty the schedule."""
    for program_id in ("L1-P1", "L2-P2", "L3-P3", "L10-P24"):
        match = side_inputs._PROGRAM_KEY.match(program_id)
        assert match is not None, program_id
    assert side_inputs._PROGRAM_KEY.match("P1") is None
    assert side_inputs._PROGRAM_KEY.match("L1P1") is None


def test_derived_siblings_reuse_the_confirmed_amber():
    """The reviewer declared ONE amber on the attestation. Every sibling built
    from the same sheet must be converted with it — a sibling built with a
    different clearance split would be a plan nobody attested to."""
    build = _build(_assignment(tod_block={"enabled": True}))
    ambers = set()
    for plan in build.tod_plans[1].plans.values():
        for phase in plan.program.phases:
            if phase.amber_s is not None:
                ambers.add(phase.amber_s)
    assert ambers == {3.0}


def test_typed_siblings_beside_a_sheet_are_refused_by_name():
    """The sheet is the authority; a typed plan beside it would be a second
    source for one controller."""
    build = _build(
        _assignment(
            tod_block={
                "enabled": True,
                "additional_plans": [
                    {
                        "source": "excel_upload",
                        "source_file": "typed.xlsx",
                        "intersection_name": "원신흥",
                        "level_no": 9,
                        "plan_id": 9,
                        "time_of_day": "12:00",
                        "declared_amber_s": 3.0,
                        "phases": [
                            {"phase_id": "Φ1", "green_s": 30.0, "clearance_s": 5.0},
                            {"phase_id": "Φ2", "green_s": 20.0, "clearance_s": 5.0},
                        ],
                    }
                ],
            }
        )
    )
    codes = [i.code for i in build.issues]
    assert "tod_sheet_route_ignores_additional_plans" in codes
    # And the typed plan is genuinely absent from the schedule.
    assert "L9-P9" not in build.tod_plans[1].plans


def test_no_tod_block_leaves_the_sheet_route_exactly_as_it_was():
    build = _build(_assignment())
    assert build.tod_plans == {}
    assert 1 in build.assignments


def test_nothing_the_sheet_route_produces_can_refuse_a_generation():
    for name in ("clean_five_phase.txt", "tod_selects_empty_level.txt"):
        build = _build(_assignment(name, tod_block={"enabled": True}))
        tod_issues = [i for i in build.issues if i.code.startswith("tod_")]
        assert all(i.severity is IssueSeverity.WARN for i in tod_issues), name


def test_a_sheet_with_no_controller_names_the_absence_rather_than_returning_silently():
    """The leg that used to ``return None, issues`` with nothing appended."""
    build = _build(_assignment("not_a_signal_sheet.txt", tod_block={"enabled": True}))
    assert build.tod_plans == {}
    codes = [i.code for i in build.issues]
    assert "tod_sheet_carries_no_controller" in codes or not build.assignments


# ---------------------------------------------------------------------------
# Sheet → document → emitted network, in one pass
# ---------------------------------------------------------------------------


def test_the_sheet_becomes_a_three_program_controller_end_to_end(crossroads):
    """Uses ``actuated_variant.txt``: 3 programs of FOUR phases, which is what a
    four-approach crossroads can carry. The five-phase fixture is refused by
    ``signals_phase_never_assigned`` against this draft, correctly and for a
    reason that predates this unit."""
    """One 대전 sheet, opted in, all the way to the Vissig blob: three programs
    and a schedule that switches between them at the sheet's own clocks."""
    build = _build(_assignment(ROUND_TRIP_SHEET, tod_block={"enabled": True}))
    composed = compose_layers(
        crossroads,
        signal_assignments=build.assignments,
        signal_tod_plans=build.tod_plans,
    )
    assert composed.ok
    node = composed.signals.emitting[0]
    assert node.tod is not None
    assert node.tod.program_count == 3

    root = ET.fromstring(base64.b64decode(build_int_supply_data(node)).decode("utf-8"))
    assert [p.get("id") for p in root.findall("./progs/prog")] == ["1", "2", "3"]
    entries = root.findall("./dailyProgLists/dailyProgList/dailyProg")
    # 00:00 is already the first switch on this sheet, so no synthetic midnight
    # entry is added — the sheet states midnight itself.
    assert [e.get("from") for e in entries] == ["0", "25200000", "75600000"]
    assert composed.summary()["signals"]["tod"]["switch_points"] == {
        "1": ["00:00", "07:00", "21:00"]
    }


def test_the_confirmed_plan_is_program_1_on_the_sheet_route_too(crossroads):
    """L1-P1 is what the attestation named; it must stay the program ``progNo``
    selects even though the schedule also runs L2-P2 and L3-P3."""
    build = _build(_assignment(ROUND_TRIP_SHEET, tod_block={"enabled": True}))
    composed = compose_layers(
        crossroads,
        signal_assignments=build.assignments,
        signal_tod_plans=build.tod_plans,
    )
    node = composed.signals.emitting[0]
    assert build.tod_plans[1].primary_program_key == "L1-P1"
    root = ET.fromstring(base64.b64decode(build_int_supply_data(node)).decode("utf-8"))
    primary = root.findall("./progs/prog")[0]
    assert primary.get("id") == "1"
    assert int(primary.get("cycletime")) == int(node.cycle_s * 1000)


def test_every_program_on_the_sheet_route_uses_only_declared_groups(crossroads):
    build = _build(_assignment(ROUND_TRIP_SHEET, tod_block={"enabled": True}))
    composed = compose_layers(
        crossroads,
        signal_assignments=build.assignments,
        signal_tod_plans=build.tod_plans,
    )
    root = ET.fromstring(
        base64.b64decode(build_int_supply_data(composed.signals.emitting[0])).decode("utf-8")
    )
    declared = {sg.get("id") for sg in root.findall("./sgs/sg")}
    for prog in root.findall("./progs/prog"):
        assert {sg.get("sg_id") for sg in prog.findall("./sgs/sg")} <= declared

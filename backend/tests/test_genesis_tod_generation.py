"""Unit #70 — generating a switching controller through the real pipeline.

Uses ``test_genesis_signals``'s own draft and 1.6-produced plan so the alternates
are built by the same converter the primary goes through, not by a fixture that
agrees with the code under test.
"""

from __future__ import annotations

import base64
import inspect
import xml.etree.ElementTree as ET

import pytest

from services.genesis import signals as signals_module

from services.dataimport.base import IssueSeverity
from services.dataimport.fixed_time import convert_to_fixed_time
from services.dataimport.schema import (
    ProgramSourceKind,
    SignalControllerData,
    SignalProgramData,
)
from services.genesis import tod_schedule
from services.genesis.compose import compose_layers
from services.genesis.signals import (
    TodNodePlans,
    build_int_supply_data,
    generate_signals,
)
from tests.test_genesis_signals import (  # noqa: F401  (pytest fixtures)
    PROV,
    STANDARD_ASSIGNMENT,
    _phase,
    a2_plan,
    crossroads,
)


def _schedule(*pairs):
    """``("L1-P1", "07:00"), ("L1-P2", "17:00")`` → a TodSchedule."""
    entries = []
    for key, clock in pairs:
        hour, _, minute = clock.partition(":")
        entries.append(
            tod_schedule.TodScheduleEntry(
                program_key=key,
                start_s=int(hour) * 3600 + int(minute) * 60,
                start_clock=clock,
            )
        )
    return tod_schedule.TodSchedule(
        entries=tuple(entries), day_plan_no=1, family=tod_schedule.FAMILY_SWITCH_POINT
    )


def _request(a2_plan, alternate_plan, *, keys=("L1-P1", "L1-P2")):
    primary_key, alternate_key = keys
    return TodNodePlans(
        schedule=_schedule((primary_key, "07:00"), (alternate_key, "17:00")),
        primary_program_key=primary_key,
        plans={primary_key: a2_plan, alternate_key: alternate_plan},
        approach_phases=STANDARD_ASSIGNMENT,
    )


def _plan(phases, program_id="P2"):
    """A plan built by 1.6 itself, so its cycle and phase offsets agree."""
    program = SignalProgramData(
        provenance=PROV,
        program_id=program_id,
        source_kind=ProgramSourceKind.OBSERVED_HISTORY,
        observed_cycle_count=10,
        phases=phases,
    )
    controller = SignalControllerData(
        provenance=PROV, controller_id="TCS999", programs=[program]
    )
    return convert_to_fixed_time(controller)


@pytest.fixture
def slower_plan():
    """A legitimate sibling: the SAME phase ids — so the same signal groups —
    observed over longer durations, giving a longer cycle and longer greens."""
    return _plan(
        [
            _phase("A", 500.0, 3.0, 3.0, stretch=True, seq=0, min_green_s=10.0),
            _phase("B", 300.0, 3.0, 2.0, seq=1, min_green_s=8.0),
            _phase("C", 150.0, 3.0, 2.0, seq=2, min_green_s=5.0),
        ]
    )


@pytest.fixture
def plan_missing_a_phase():
    """A sibling that does not cover approach 7's phase C at all."""
    return _plan(
        [
            _phase("A", 410.0, 3.0, 3.0, stretch=True, seq=0, min_green_s=10.0),
            _phase("B", 260.0, 3.0, 2.0, seq=1, min_green_s=8.0),
        ]
    )


# ---------------------------------------------------------------------------
# Additivity through the real generator
# ---------------------------------------------------------------------------


def test_a_draft_generated_without_tod_plans_is_unchanged(crossroads, a2_plan):
    plain = generate_signals(crossroads, {1: (a2_plan, STANDARD_ASSIGNMENT)})
    with_empty = generate_signals(
        crossroads, {1: (a2_plan, STANDARD_ASSIGNMENT)}, tod_plans={}
    )
    assert plain.nodes[0].tod is None
    assert build_int_supply_data(plain.nodes[0]) == build_int_supply_data(
        with_empty.nodes[0]
    )


def test_compose_without_tod_plans_writes_no_tod_summary_key(crossroads, a2_plan):
    composed = compose_layers(
        crossroads, signal_assignments={1: (a2_plan, STANDARD_ASSIGNMENT)}
    )
    assert "tod" not in composed.summary()["signals"]


# ---------------------------------------------------------------------------
# The switching controller
# ---------------------------------------------------------------------------


def test_two_plans_become_two_programs_and_a_schedule(crossroads, a2_plan, slower_plan):
    built = generate_signals(
        crossroads,
        {1: (a2_plan, STANDARD_ASSIGNMENT)},
        tod_plans={1: _request(a2_plan, slower_plan)},
    )
    node = built.nodes[0]
    assert node.ok
    assert node.tod is not None
    assert node.tod.program_count == 2
    assert [a.prog_id for a in node.tod.alternates] == [2]
    # An explicit midnight entry carrying the WRAPPED program (the 17:00 one
    # runs until 07:00), so no reader has to supply a "before the first switch"
    # convention of its own.
    assert node.tod.entries == ((0, 2), (7 * 3600, 1), (17 * 3600, 2))

    root = ET.fromstring(base64.b64decode(build_int_supply_data(node)).decode("utf-8"))
    assert [p.get("id") for p in root.findall("./progs/prog")] == ["1", "2"]
    entries = root.findall("./dailyProgLists/dailyProgList/dailyProg")
    assert [e.get("from") for e in entries] == ["0", "25200000", "61200000"]
    assert [e.get("prog_id") for e in entries] == ["2", "1", "2"]


def test_a_schedule_that_already_starts_at_midnight_gains_no_extra_entry(
    crossroads, a2_plan, slower_plan
):
    request = TodNodePlans(
        schedule=_schedule(("L1-P1", "00:00"), ("L1-P2", "12:00")),
        primary_program_key="L1-P1",
        plans={"L1-P1": a2_plan, "L1-P2": slower_plan},
        approach_phases=STANDARD_ASSIGNMENT,
    )
    built = generate_signals(
        crossroads, {1: (a2_plan, STANDARD_ASSIGNMENT)}, tod_plans={1: request}
    )
    assert built.nodes[0].tod.entries == ((0, 1), (12 * 3600, 2))


def test_the_alternate_carries_its_own_green_times(crossroads, a2_plan, slower_plan):
    built = generate_signals(
        crossroads,
        {1: (a2_plan, STANDARD_ASSIGNMENT)},
        tod_plans={1: _request(a2_plan, slower_plan)},
    )
    node = built.nodes[0]
    primary_greens = [g.green_s for g in node.groups]
    alternate_greens = [g.green_s for g in node.tod.alternates[0].groups]
    assert alternate_greens != primary_greens
    assert all(b > a for a, b in zip(primary_greens, alternate_greens))


def test_every_program_references_only_declared_signal_groups(
    crossroads, a2_plan, slower_plan
):
    """The controller declares its groups once. This is the invariant the
    group-set guard exists to protect, asserted on the emitted document."""
    built = generate_signals(
        crossroads,
        {1: (a2_plan, STANDARD_ASSIGNMENT)},
        tod_plans={1: _request(a2_plan, slower_plan)},
    )
    root = ET.fromstring(
        base64.b64decode(build_int_supply_data(built.nodes[0])).decode("utf-8")
    )
    declared = {sg.get("id") for sg in root.findall("./sgs/sg")}
    for prog in root.findall("./progs/prog"):
        assert {sg.get("sg_id") for sg in prog.findall("./sgs/sg")} <= declared


def test_a_sibling_that_cannot_carry_every_approach_is_dropped_by_name(
    crossroads, a2_plan, plan_missing_a_phase
):
    """A plan that does not cover approach 7's phase refuses inside
    ``generate_node_signals``; the alternate is dropped BY NAME and the node
    survives with its confirmed program."""
    built = generate_signals(
        crossroads,
        {1: (a2_plan, STANDARD_ASSIGNMENT)},
        tod_plans={1: _request(a2_plan, plan_missing_a_phase)},
    )
    node = built.nodes[0]
    codes = node.issue_codes()
    assert "signals_tod_alternate_refused" in codes
    assert "signals_tod_not_configured" in codes
    assert node.tod is None  # nothing left to switch between
    assert node.ok  # and the node is STILL emitted, with its confirmed program
    assert node.groups


def test_group_numbering_is_a_function_of_the_draft_alone(
    crossroads, a2_plan, slower_plan
):
    """The invariant that replaced a guard which could not fire.

    ``_build_tod_programs`` used to compare each alternate's
    ``(no, approach_link_id)`` sequence against the primary's and drop any that
    differed — but group numbers come from ``enumerate(node_approaches(draft,
    node))``, so two plans on one node CANNOT differ and the check never fired.
    Deleting a guard is only honest if the property it claimed is pinned
    somewhere real: this test fails the moment group numbering becomes
    plan-dependent, which is exactly when such a guard would start to be needed.
    """
    from services.genesis.signals import generate_node_signals

    shapes = set()
    for plan in (a2_plan, slower_plan):
        built = generate_node_signals(
            crossroads,
            crossroads.nodes[0],
            plan,
            approach_phases=STANDARD_ASSIGNMENT,
        )
        assert built.ok
        shapes.add(tuple((g.no, g.approach_link_id) for g in built.groups))
    assert len(shapes) == 1, (
        "two plans on one node produced different signal-group numbering; the "
        "deleted signals_tod_group_set_differs guard is now needed again"
    )


def test_the_deleted_guard_is_gone_and_its_absence_is_explained():
    """The claim was deleted along with the branch. A guard named in prose but
    unable to fire is worse than none — it invites the next reader to trust it,
    so the module must carry the reasoning rather than a silent removal."""
    source = inspect.getsource(signals_module)
    assert '"signals_tod_group_set_differs"' not in source
    assert "WHY THERE IS NO SIGNAL-GROUP EQUALITY CHECK HERE." in source


def test_a_dropped_sibling_never_refuses_the_node(
    crossroads, a2_plan, plan_missing_a_phase
):
    built = generate_signals(
        crossroads,
        {1: (a2_plan, STANDARD_ASSIGNMENT)},
        tod_plans={1: _request(a2_plan, plan_missing_a_phase)},
    )
    node = built.nodes[0]
    assert node.refusals == ()
    assert built.emitting  # the controller is still written


def test_the_summary_discloses_the_switching_and_that_it_is_unverified(
    crossroads, a2_plan, slower_plan
):
    """Where signals absences already surface: layers.summary()['signals'].

    ``schedule_element_verified`` was False while S7 was open, so the summary
    could not imply Vissim had accepted a schedule nothing had shown it. The
    live roundtrip on 2026-08-26 returned it byte-identical, so the flag is now
    True — and carries its evidence, because a bare boolean is what it was False
    to avoid."""
    composed = compose_layers(
        crossroads,
        signal_assignments={1: (a2_plan, STANDARD_ASSIGNMENT)},
        signal_tod_plans={1: _request(a2_plan, slower_plan)},
    )
    block = composed.summary()["signals"]["tod"]
    assert block["controllers"] == 1
    assert block["nodes"] == [1]
    assert block["programs"] == {"1": 2}
    assert block["switch_points"] == {"1": ["00:00", "07:00", "17:00"]}
    assert block["assumption"] == "S7"
    assert block["schedule_element_verified"] is True
    assert block["schedule_element_verified_on"] == "2026-08-26"
    assert "KEPT_VERBATIM" in block["schedule_element_verified_by"]


def test_dropped_siblings_reach_the_summary_warnings(
    crossroads, a2_plan, plan_missing_a_phase
):
    composed = compose_layers(
        crossroads,
        signal_assignments={1: (a2_plan, STANDARD_ASSIGNMENT)},
        signal_tod_plans={1: _request(a2_plan, plan_missing_a_phase)},
    )
    warnings = composed.summary()["signals"]["warnings"]
    assert any(code.startswith("signals_tod_") for code in warnings)
    assert composed.summary()["signals"]["refusals"] == []


def test_switching_never_makes_the_composition_refuse(
    crossroads, a2_plan, plan_missing_a_phase
):
    """The anti-poison rule at the top of the stack: ``genesis_jobs`` turns
    ``not layers.ok`` into a refused generation, so nothing about a 시간대 may
    reach REFUSE tier."""
    composed = compose_layers(
        crossroads,
        signal_assignments={1: (a2_plan, STANDARD_ASSIGNMENT)},
        signal_tod_plans={1: _request(a2_plan, plan_missing_a_phase)},
    )
    assert composed.ok
    assert all(
        i.severity is not IssueSeverity.REFUSE
        for i in composed.all_issues
        if i.code.startswith(("tod_", "signals_tod_"))
    )


def test_the_setting_can_emit_programs_without_the_assumed_schedule(
    crossroads, a2_plan, slower_plan
):
    from services.genesis.signals import SignalSettings

    built = generate_signals(
        crossroads,
        {1: (a2_plan, STANDARD_ASSIGNMENT)},
        settings=SignalSettings(emit_tod_schedule_element=False),
        tod_plans={1: _request(a2_plan, slower_plan)},
    )
    document = base64.b64decode(build_int_supply_data(built.nodes[0])).decode("utf-8")
    root = ET.fromstring(document)
    assert [p.get("id") for p in root.findall("./progs/prog")] == ["1", "2"]
    assert list(root.find("./dailyProgLists")) == []

"""Unit #70 — the side-input document, the staging door, and the opt-in.

The obligations here are about ADDITIVITY: a document written before this unit
must parse unchanged, and a consumer who never opts in must get today's output.
"""

from __future__ import annotations

import json

import pytest

from services.dataimport.base import IssueSeverity
from services.genesis import phase_diagram, side_inputs
from services.genesis.side_inputs import (
    SIGNALS_DOC_KIND,
    SignalsSideInput,
    TodSwitching,
    parse_side_input,
)

# ---------------------------------------------------------------------------
# Schema additivity
# ---------------------------------------------------------------------------


def _excel_plan(level_no: int, plan_id: int, time_of_day=None, cycle_s=60.0):
    return {
        "source": "excel_upload",
        "source_file": "원신흥.xlsx",
        "intersection_name": "원신흥",
        "level_no": level_no,
        "plan_id": plan_id,
        "cycle_s": cycle_s,
        "time_of_day": time_of_day,
        "declared_amber_s": 3.0,
        "phases": [
            {"phase_id": "Φ1", "green_s": cycle_s / 2 - 5, "clearance_s": 5.0},
            {"phase_id": "Φ2", "green_s": cycle_s / 2 - 5, "clearance_s": 5.0},
        ],
    }


def _doc(assignments) -> bytes:
    return json.dumps(
        {"kind": SIGNALS_DOC_KIND, "assignments": assignments}, ensure_ascii=False
    ).encode("utf-8")


OLD_STYLE_ASSIGNMENT = {
    "node_id": 7,
    "plan": _excel_plan(1, 1),
    "approach_phases": {"101": "Φ1", "102": "Φ2"},
}


def test_a_document_written_before_this_unit_parses_unchanged():
    """No ``tod`` key anywhere — the shape every staged document on disk has."""
    parsed = parse_side_input("signals", _doc([OLD_STYLE_ASSIGNMENT]))
    assert isinstance(parsed, SignalsSideInput)
    assert parsed.assignments[0].tod is None


def test_an_old_document_round_trips_through_the_model_without_growing_a_tod_key():
    parsed = parse_side_input("signals", _doc([OLD_STYLE_ASSIGNMENT]))
    dumped = parsed.model_dump(exclude_none=True)
    assert "tod" not in dumped["assignments"][0]


def test_a_document_with_tod_parses_and_carries_the_opt_in():
    assignment = dict(OLD_STYLE_ASSIGNMENT, tod={"enabled": True})
    parsed = parse_side_input("signals", _doc([assignment]))
    assert isinstance(parsed.assignments[0].tod, TodSwitching)
    assert parsed.assignments[0].tod.enabled is True
    assert parsed.assignments[0].tod.additional_plans == []


def test_tod_carries_additional_plans_for_the_typed_route():
    assignment = dict(
        OLD_STYLE_ASSIGNMENT,
        plan=_excel_plan(1, 1, "07:00"),
        tod={"enabled": True, "additional_plans": [_excel_plan(1, 2, "17:00")]},
    )
    parsed = parse_side_input("signals", _doc([assignment]))
    extra = parsed.assignments[0].tod.additional_plans
    assert [p.plan_id for p in extra] == [2]
    assert extra[0].time_of_day == "17:00"


def test_a_typo_inside_tod_is_refused_and_never_silently_ignored():
    """``extra="forbid"`` is the document's whole honesty contract; the new
    model must not be the one place a typo slips through."""
    assignment = dict(OLD_STYLE_ASSIGNMENT, tod={"enabled": True, "enabeld": True})
    with pytest.raises(side_inputs.SideInputRefused) as caught:
        parse_side_input("signals", _doc([assignment]))
    assert caught.value.code == "genesis_side_input_invalid_signals"
    assert any("enabeld" in reason for reason in caught.value.reasons)


def test_tod_disabled_is_the_same_as_absent():
    assignment = dict(OLD_STYLE_ASSIGNMENT, tod={"enabled": False})
    parsed = parse_side_input("signals", _doc([assignment]))
    build = side_inputs.build_signal_assignments(parsed)
    assert build.tod_plans == {}


# ---------------------------------------------------------------------------
# Building the plans
# ---------------------------------------------------------------------------


def _build(assignments):
    return side_inputs.build_signal_assignments(parse_side_input("signals", _doc(assignments)))


def test_the_typed_route_builds_a_schedule_from_its_siblings():
    assignment = {
        "node_id": 7,
        "plan": _excel_plan(1, 1, "07:00", cycle_s=60.0),
        "approach_phases": {"101": "Φ1", "102": "Φ2"},
        "tod": {
            "enabled": True,
            "additional_plans": [_excel_plan(1, 2, "17:00", cycle_s=90.0)],
        },
    }
    build = _build([assignment])
    assert 7 in build.tod_plans
    request = build.tod_plans[7]
    assert request.primary_program_key == "L1-P1"
    assert request.schedule.program_keys == ("L1-P1", "L1-P2")
    assert set(request.plans) == {"L1-P1", "L1-P2"}
    assert [e.start_clock for e in request.schedule.entries] == ["07:00", "17:00"]


def test_an_unreadable_sibling_phrase_costs_only_that_sibling():
    """The anti-poison rule end to end: the node still gets its plan and its
    assignment, and the refusal is a WARN naming the sibling."""
    assignment = {
        "node_id": 7,
        "plan": _excel_plan(1, 1, "07:00"),
        "approach_phases": {"101": "Φ1", "102": "Φ2"},
        "tod": {
            "enabled": True,
            "additional_plans": [
                _excel_plan(1, 2, "17:00"),
                _excel_plan(1, 3, "TOD2"),  # unreadable
            ],
        },
    }
    build = _build([assignment])
    assert 7 in build.assignments  # the node is NOT lost
    assert build.tod_plans[7].schedule.program_keys == ("L1-P1", "L1-P2")
    codes = [i.code for i in build.issues]
    assert "tod_phrase_states_no_clock" in codes
    assert all(i.severity is not IssueSeverity.REFUSE for i in build.issues)


def test_no_readable_phrase_falls_back_to_the_single_plan_with_a_named_absence():
    assignment = {
        "node_id": 7,
        "plan": _excel_plan(1, 1, "TOD2"),
        "approach_phases": {"101": "Φ1", "102": "Φ2"},
        "tod": {"enabled": True, "additional_plans": [_excel_plan(1, 2, None)]},
    }
    build = _build([assignment])
    assert 7 in build.assignments
    assert build.tod_plans == {}
    codes = [i.code for i in build.issues]
    assert "tod_schedule_no_entries" in codes
    assert build.ok


def test_a_schedule_that_never_selects_the_confirmed_plan_is_not_built():
    """The confirmed plan is program 1. A schedule that never names it would
    quietly demote the one plan a human attested to."""
    assignment = {
        "node_id": 7,
        "plan": _excel_plan(1, 1, "TOD2"),  # the CONFIRMED plan is unreadable
        "approach_phases": {"101": "Φ1", "102": "Φ2"},
        "tod": {
            "enabled": True,
            "additional_plans": [
                _excel_plan(1, 2, "07:00"),
                _excel_plan(1, 3, "17:00"),
            ],
        },
    }
    build = _build([assignment])
    assert build.tod_plans == {}
    assert "tod_confirmed_plan_not_scheduled" in [i.code for i in build.issues]


def test_observed_history_names_the_route_as_unsupported_rather_than_ignoring_it():
    document = {
        "kind": SIGNALS_DOC_KIND,
        "controllers": {
            "adapter": {
                "name": "scats_wa",
                "region": "AU-WA",
                "source_system": "SCATS",
                "version": "1",
            },
            "controllers": [
                {
                    "controller_id": "359",
                    "provenance": {"source_file": "scats.csv", "locator": "site 359"},
                    "programs": [
                        {
                            "program_id": "P1",
                            "provenance": {"source_file": "scats.csv", "locator": "plan 1"},
                            "source_kind": "declared",
                            "cycle_time_s": 60.0,
                            "phases": [],
                        }
                    ],
                }
            ],
        },
        "assignments": [
            {
                "node_id": 7,
                "plan": {"source": "observed_history", "controller_id": "359"},
                "approach_phases": {"101": "Φ1"},
                "tod": {"enabled": True},
            }
        ],
    }
    parsed = parse_side_input(
        "signals", json.dumps(document, ensure_ascii=False).encode("utf-8")
    )
    build = side_inputs.build_signal_assignments(parsed)
    codes = [i.code for i in build.issues]
    # The route refusal is named whenever the plan itself got far enough to be
    # offered one; either way it is never silently treated as switching.
    assert build.tod_plans == {}
    assert "tod_route_not_supported" in codes or build.assignments == {}


# ---------------------------------------------------------------------------
# The staging door
# ---------------------------------------------------------------------------


class _Attestation:
    def __init__(self, node_id: int) -> None:
        self.node_id = node_id
        self.level_no = 1
        self.plan_id = 1
        self.source_file = "원신흥.pdf"
        self.declared_amber_s = 3.0
        self.amber_declaration_note = None
        self.approach_phases = {101: "Φ1", 102: "Φ2"}


def _confirmed(node_id: int = 7):
    mapping = phase_diagram.ConfirmedMapping.__new__(phase_diagram.ConfirmedMapping)
    object.__setattr__(mapping, "attestation", _Attestation(node_id))
    object.__setattr__(mapping, "sheet_text", "교차로 운영 DATABASE ...")
    object.__setattr__(mapping, "declared_plan", None)
    return mapping


def test_the_staging_door_defaults_to_no_switching_and_the_document_is_unchanged():
    document = phase_diagram.build_signals_document([_confirmed()])
    assert "tod" not in document["assignments"][0]


def test_the_staging_door_opt_in_marks_every_assignment():
    document = phase_diagram.build_signals_document(
        [_confirmed(7), _confirmed(8)], time_of_day_switching=True
    )
    assert all(a["tod"] == {"enabled": True} for a in document["assignments"])


def test_the_opt_in_does_not_change_what_the_attestation_signs():
    """Constraint from the unit brief: 확인 semantics are untouched. The plan the
    document names is still the attestation's own (수준, 계획 ID), and no field of
    the attestation is read differently."""
    plain = phase_diagram.build_signals_document([_confirmed()])
    switched = phase_diagram.build_signals_document(
        [_confirmed()], time_of_day_switching=True
    )
    assert switched["assignments"][0]["plan"] == plain["assignments"][0]["plan"]
    assert (
        switched["assignments"][0]["approach_phases"]
        == plain["assignments"][0]["approach_phases"]
    )
    assert set(switched["assignments"][0]) - set(plain["assignments"][0]) == {"tod"}


def test_the_http_flag_defaults_to_off_at_every_layer():
    """MEDIUM 8. Three defaults have to agree, and all three are OFF: the HTTP
    request model, the document builder, and the document schema. A flip of any
    one would turn switching on for callers who never asked — and the whole
    additivity claim of this unit is that not asking changes nothing."""
    import inspect

    from routers.genesis import BuildSignalsInputRequest

    field = BuildSignalsInputRequest.model_fields["time_of_day_switching"]
    assert field.default is False
    assert BuildSignalsInputRequest(review_ids=["r1"]).time_of_day_switching is False

    parameter = inspect.signature(phase_diagram.build_signals_document).parameters[
        "time_of_day_switching"
    ]
    assert parameter.default is False

    assert side_inputs.TodSwitching.model_fields["enabled"].default is True
    assert side_inputs.SignalAssignment.model_fields["tod"].default is None


def test_an_explicit_false_flag_stages_the_pre_unit_document():
    document = phase_diagram.build_signals_document(
        [_confirmed()], time_of_day_switching=False
    )
    assert "tod" not in document["assignments"][0]


def test_the_opt_in_document_still_passes_the_side_input_validator():
    document = phase_diagram.build_signals_document(
        [_confirmed()], time_of_day_switching=True
    )
    parsed = parse_side_input(
        "signals", json.dumps(document, ensure_ascii=False).encode("utf-8")
    )
    assert parsed.assignments[0].tod.enabled is True

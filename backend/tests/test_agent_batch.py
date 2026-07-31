"""Tests for QUEUE 3 unit 8's services.agent.batch: the pure planning logic
behind tools_edit.apply_edit_plan (no COM, no FastAPI, no orchestrator loop
-- see test_agent_orchestrator_batch.py for the full end-to-end approval-flow
transcripts).

Imports services.agent.tools_edit purely for its REGISTRATION side effect
(so default_registry actually knows every write/read/run tool -- batch.py
reads the real registry, not a fake one), mirroring test_agent_rollback.py's
own "REAL registered tool" convention.
"""
from __future__ import annotations

from typing import Any

import services.agent.tools_edit  # noqa: F401 - registers the real tools (incl. apply_edit_plan)
import services.agent.tools_network  # noqa: F401 - registers read tools (get_network_overview, ...)
import services.agent.tools_run  # noqa: F401 - registers run_simulation (tier "run")
from services.agent import batch
from services.agent.toolkit import default_registry

LANGS = {"ko", "en", "ja"}


def _shadow_model(**overrides: Any) -> dict[str, Any]:
    shadow: dict[str, Any] = {
        "links": [{"id": "101", "name": "Main St EB approach", "lanes": 3, "length": 280.0}],
        "vehicle_inputs": [{"no": "1", "name": "Main St EB", "link": "101", "volume": 900.0}],
        "signals": [
            {
                "sc_no": "1",
                "sc_name": "SC1",
                "cycle_time": 90,
                "offset": 0,
                "controller_type": "FIXEDTIME",
                "min_green_intergreen_sum": 34,
                "phases": [],
            }
        ],
        "nodes": [],
        "conflict_areas": [],
    }
    shadow.update(overrides)
    return shadow


def _step(tool: str, **params: Any) -> dict[str, Any]:
    return {"tool": tool, "params": params}


def _plan_action(*steps: dict[str, Any]) -> dict[str, Any]:
    return {"tool": "apply_edit_plan", "params": {"steps": list(steps)}}


def _assert_well_formed(finding) -> None:
    assert set(finding.messages.keys()) == LANGS
    for lang in LANGS:
        assert finding.messages[lang].strip()


# ---------------------------------------------------------------------------
# Requirement #1: plan validation before the card
# ---------------------------------------------------------------------------


def test_validate_plan_refuses_single_step_plan():
    findings = batch.validate_plan(_plan_action(_step("set_signal_offset", sc_no="1", offset=5)), _shadow_model())

    matches = [f for f in findings if f.constraint_id == "apply_edit_plan_step_count_out_of_range"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    _assert_well_formed(matches[0])
    assert matches[0].details["requested_step_count"] == 1


def test_validate_plan_refuses_plan_above_max_steps():
    steps = [_step("set_signal_offset", sc_no="1", offset=i) for i in range(batch.MAX_STEPS + 1)]

    findings = batch.validate_plan(_plan_action(*steps), _shadow_model())

    matches = [f for f in findings if f.constraint_id == "apply_edit_plan_step_count_out_of_range"]
    assert len(matches) == 1
    assert matches[0].details["requested_step_count"] == batch.MAX_STEPS + 1


def test_validate_plan_allows_exactly_min_and_max_step_counts_through_structural_gate():
    # Neither triggers the step-count refusal (may still trigger other
    # findings -- this only asserts the COUNT gate itself is inclusive).
    two_steps = batch.validate_plan(
        _plan_action(
            _step("set_signal_offset", sc_no="1", offset=5),
            _step("set_signal_cycle", sc_no="1", cycle=100),
        ),
        _shadow_model(),
    )
    assert not any(f.constraint_id == "apply_edit_plan_step_count_out_of_range" for f in two_steps)

    ten_steps = [_step("set_signal_offset", sc_no="1", offset=i) for i in range(batch.MAX_STEPS)]
    ten = batch.validate_plan(_plan_action(*ten_steps), _shadow_model())
    assert not any(f.constraint_id == "apply_edit_plan_step_count_out_of_range" for f in ten)


def test_validate_plan_refuses_malformed_step_shape():
    action = {"tool": "apply_edit_plan", "params": {"steps": [{"tool": "set_signal_offset"}, {"tool": "set_signal_cycle", "params": {"sc_no": "1", "cycle": 90}}]}}

    findings = batch.validate_plan(action, _shadow_model())

    matches = [f for f in findings if f.constraint_id == "apply_edit_plan_step_malformed"]
    assert len(matches) == 1
    assert matches[0].details["batch_step_index"] == 1
    _assert_well_formed(matches[0])


def test_validate_plan_refuses_unknown_tool_with_suggestion():
    findings = batch.validate_plan(
        _plan_action(_step("set_signal_offst", sc_no="1", offset=5), _step("set_signal_cycle", sc_no="1", cycle=90)),
        _shadow_model(),
    )

    matches = [f for f in findings if f.constraint_id == "apply_edit_plan_step_unknown_tool"]
    assert len(matches) == 1
    assert matches[0].details["batch_step_index"] == 1
    assert "set_signal_offset" in (matches[0].suggested_fix or [])


def test_validate_plan_refuses_read_tier_tool_as_a_step():
    findings = batch.validate_plan(
        _plan_action(_step("get_network_overview"), _step("set_signal_cycle", sc_no="1", cycle=90)),
        _shadow_model(),
    )

    matches = [f for f in findings if f.constraint_id == "apply_edit_plan_step_tool_ineligible"]
    assert len(matches) == 1
    assert matches[0].details["tool"] == "get_network_overview"
    assert matches[0].details["tier"] == "read"


def test_validate_plan_refuses_run_simulation_as_a_step():
    findings = batch.validate_plan(
        _plan_action(_step("run_simulation", duration_s=60), _step("set_signal_cycle", sc_no="1", cycle=90)),
        _shadow_model(),
    )

    matches = [f for f in findings if f.constraint_id == "apply_edit_plan_step_tool_ineligible"]
    assert len(matches) == 1
    assert matches[0].details["tool"] == "run_simulation"
    assert matches[0].details["tier"] == "run"


def test_validate_plan_refuses_rollback_last_edit_as_a_step():
    findings = batch.validate_plan(
        _plan_action(_step("rollback_last_edit"), _step("set_signal_cycle", sc_no="1", cycle=90)),
        _shadow_model(),
    )

    matches = [f for f in findings if f.constraint_id == "apply_edit_plan_step_tool_ineligible"]
    assert len(matches) == 1
    assert matches[0].details["tool"] == "rollback_last_edit"


def test_validate_plan_refuses_apply_edit_plan_as_a_step_no_recursion():
    findings = batch.validate_plan(
        _plan_action(
            _step("apply_edit_plan", steps=[]),
            _step("set_signal_cycle", sc_no="1", cycle=90),
        ),
        _shadow_model(),
    )

    matches = [f for f in findings if f.constraint_id == "apply_edit_plan_step_tool_ineligible"]
    assert len(matches) == 1
    assert matches[0].details["tool"] == "apply_edit_plan"
    assert "apply_edit_plan" in batch.BATCH_STEP_BLOCKLIST
    assert "rollback_last_edit" in batch.BATCH_STEP_BLOCKLIST


def test_validate_plan_refuses_invalid_step_params():
    findings = batch.validate_plan(
        _plan_action(
            _step("set_signal_cycle", sc_no="1", cycle="not-a-number"),
            _step("set_signal_offset", sc_no="1", offset=5),
        ),
        _shadow_model(),
    )

    matches = [f for f in findings if f.constraint_id == "apply_edit_plan_step_invalid_params"]
    assert len(matches) == 1
    assert matches[0].details["batch_step_index"] == 1


def test_validate_plan_attributes_per_step_preflight_refusal_with_step_index():
    findings = batch.validate_plan(
        _plan_action(
            _step("set_signal_cycle", sc_no="1", cycle=100),
            _step("set_signal_offset", sc_no="does-not-exist", offset=5),
        ),
        _shadow_model(),
    )

    matches = [f for f in findings if f.constraint_id == "object_not_found" and f.tier == "refuse"]
    assert len(matches) == 1
    assert matches[0].details["batch_step_index"] == 2
    assert matches[0].details["batch_step_tool"] == "set_signal_offset"
    assert "[Step 2: set_signal_offset]" in matches[0].messages["en"]


# ---------------------------------------------------------------------------
# Requirement #2a: same-object-same-field collision
# ---------------------------------------------------------------------------


def test_validate_plan_refuses_same_object_same_field_collision():
    shadow = _shadow_model(conflict_areas=[{"no": "5", "link_a": "101", "link_b": "102", "status": "PASSIVE"}])
    findings = batch.validate_plan(
        _plan_action(
            _step("set_conflict_area_status", conflict_area_no="5", status="AHASRIGHTOFWAY"),
            _step("set_conflict_area_status", conflict_area_no="5", status="BHASRIGHTOFWAY"),
        ),
        shadow,
    )

    matches = [f for f in findings if f.constraint_id == "apply_edit_plan_target_collision"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    _assert_well_formed(finding)
    assert finding.details["first_step_index"] == 1
    assert finding.details["second_step_index"] == 2
    assert finding.details["object_class"] == "conflict_areas"
    assert finding.details["target_id"] == "5"
    assert finding.details["shared_fields"] == ["status"]
    assert "1" in finding.messages["en"] and "2" in finding.messages["en"]


def test_validate_plan_does_not_collide_on_different_targets():
    shadow = _shadow_model(
        conflict_areas=[
            {"no": "5", "link_a": "101", "link_b": "102", "status": "PASSIVE"},
            {"no": "6", "link_a": "103", "link_b": "104", "status": "PASSIVE"},
        ]
    )
    findings = batch.validate_plan(
        _plan_action(
            _step("set_conflict_area_status", conflict_area_no="5", status="AHASRIGHTOFWAY"),
            _step("set_conflict_area_status", conflict_area_no="6", status="BHASRIGHTOFWAY"),
        ),
        shadow,
    )

    assert not any(f.constraint_id == "apply_edit_plan_target_collision" for f in findings)


# ---------------------------------------------------------------------------
# Requirement #2b: combined effect trips a constraint no single step trips
# (patched-shadow preflight -- the worked case from the task brief).
# ---------------------------------------------------------------------------


def test_validate_plan_refuses_combined_sibling_volumes_exceeding_capacity_when_each_step_alone_passes():
    # Link 101: 3 lanes -> rough capacity 3 * 1900 = 5700 veh/h. Two vehicle
    # inputs share it, both starting at an unknown/absent volume.
    shadow = _shadow_model(
        vehicle_inputs=[
            {"no": "1", "name": "Main St EB A", "link": "101"},
            {"no": "2", "name": "Main St EB B", "link": "101"},
        ]
    )
    step1 = _step("set_vehicle_input_volume", input_no="1", volume=4000.0)
    step2 = _step("set_vehicle_input_volume", input_no="2", volume=4000.0)

    # Each step ALONE, checked against the CURRENT (pre-batch) shadow model,
    # is under capacity (the other sibling's volume is unknown/0) -- this is
    # the fixture-level proof that "each step alone passes".
    from services.agent.constraints import run_checks

    assert not any(
        f.constraint_id == "latent_demand_overflow" and f.tier == "refuse"
        for f in run_checks("preflight", step1, shadow, {})
    )
    assert not any(
        f.constraint_id == "latent_demand_overflow" and f.tier == "refuse"
        for f in run_checks("preflight", step2, shadow, {})
    )
    assert not any(f.constraint_id == "latent_demand_overflow" for f in run_checks("preflight", step2, shadow, {}))

    findings = batch.validate_plan(_plan_action(step1, step2), shadow)

    combined = [f for f in findings if f.constraint_id == "apply_edit_plan_combined_effect"]
    assert len(combined) == 1
    finding = combined[0]
    assert finding.tier == "refuse"
    _assert_well_formed(finding)
    assert finding.details["batch_step_index"] == 2
    assert finding.details["combined_with_step_indices"] == [1]
    assert finding.details["underlying_constraint_id"] == "latent_demand_overflow"
    assert finding.details["underlying_tier"] == "warn"  # single-edit tier, unescalated

    # The whole plan is blocking (has at least one refuse-tier finding).
    assert any(f.tier == "refuse" for f in findings)
    # Neither step's OWN attributed preflight (against the unpatched model)
    # carries a refuse-tier finding -- the refusal is genuinely a cross-step
    # discovery, not something either step's individual review would show.
    per_step_refusals = [
        f for f in findings if f.tier == "refuse" and f.constraint_id not in ("apply_edit_plan_combined_effect",)
    ]
    assert per_step_refusals == []


def test_validate_plan_does_not_double_report_a_finding_that_already_fires_standalone():
    # input "1" already carries enough volume that step 1 alone (against the
    # UNPATCHED model) already trips latent_demand_overflow -- the SAME
    # constraint firing again in the patched pass for a later step must not
    # be reported as a NEW "combined effect" if it fires standalone too.
    shadow = _shadow_model(
        vehicle_inputs=[
            {"no": "1", "name": "A", "link": "101", "volume": 6000.0},
            {"no": "2", "name": "B", "link": "101", "volume": 6000.0},
        ]
    )
    step1 = _step("set_vehicle_input_volume", input_no="1", volume=100.0)
    step2 = _step("set_vehicle_input_volume", input_no="2", volume=100.0)

    findings = batch.validate_plan(_plan_action(step1, step2), shadow)

    assert not any(f.constraint_id == "apply_edit_plan_combined_effect" for f in findings)
    # Both steps' own (unpatched) preflight already carries the warning.
    step2_warnings = [
        f for f in findings if f.constraint_id == "latent_demand_overflow" and f.details.get("batch_step_index") == 2
    ]
    assert len(step2_warnings) == 1
    assert step2_warnings[0].tier == "warn"


# ---------------------------------------------------------------------------
# Always-present plan overview
# ---------------------------------------------------------------------------


def test_validate_plan_always_includes_plan_overview_when_structurally_valid():
    shadow = _shadow_model()
    findings = batch.validate_plan(
        _plan_action(
            _step("set_signal_cycle", sc_no="1", cycle=100),
            _step("set_signal_offset", sc_no="1", offset=5),
        ),
        shadow,
    )

    overview = [f for f in findings if f.constraint_id == "apply_edit_plan_summary"]
    assert len(overview) == 1
    assert overview[0].tier == "warn"
    assert "set_signal_cycle" in overview[0].messages["en"]
    assert "set_signal_offset" in overview[0].messages["en"]
    assert "90" in overview[0].messages["en"]  # old cycle_time value shown
    assert "100" in overview[0].messages["en"]  # new cycle_time value shown


def test_validate_plan_no_overview_when_structurally_broken():
    findings = batch.validate_plan(_plan_action(_step("set_signal_cycle", sc_no="1", cycle=100)), _shadow_model())
    assert not any(f.constraint_id == "apply_edit_plan_summary" for f in findings)


# ---------------------------------------------------------------------------
# resolve_write_target
# ---------------------------------------------------------------------------


def test_resolve_write_target_resolves_single_id_tools():
    target = batch.resolve_write_target(_step("set_signal_cycle", sc_no="1", cycle=100))
    assert target == {"object_class": "signals", "target_id": "1", "fields": frozenset({"cycle_time"})}


def test_resolve_write_target_none_for_unmapped_tool():
    assert batch.resolve_write_target(_step("set_reduced_speed_area", link_id="101", speed_kmh=40.0, from_m=0, length_m=10)) is None


def test_resolve_write_target_two_part_identity():
    target = batch.resolve_write_target(_step("set_vehicle_composition_share", composition_no="1", veh_type_no="10", rel_flow=5.0))
    assert target["object_class"] == "vehicle_composition_entries"
    assert target["target_id"] == "1.10"


# ---------------------------------------------------------------------------
# BatchAbortedError
# ---------------------------------------------------------------------------


def test_batch_aborted_error_message_and_details():
    exc = batch.BatchAbortedError(
        step_index=2,
        total_steps=3,
        tool="set_signal_offset",
        reason="read-back mismatch after apply (wrote 5, read back 4)",
        rolled_back=[{"index": 1, "tool": "set_signal_cycle"}],
        never_reached=[{"index": 3, "tool": "set_vehicle_composition_share"}],
        restore_verified=True,
    )

    text = str(exc)
    assert "batch_step_apply_failed" in text
    assert "step 2/3" in text
    assert "set_signal_offset" in text

    assert exc.details["failed_step_index"] == 2
    assert exc.details["restore_verified"] is True
    statuses = {s["index"]: s["status"] for s in exc.details["steps"]}
    assert statuses == {1: "rolled_back", 2: "failed", 3: "never_reached"}

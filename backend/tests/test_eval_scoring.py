"""Unit tests for eval/scoring.py's pure functions -- no agent runtime, no
filesystem, synthetic trace/shadow-model fixtures only. Complements
test_eval_runner.py's end-to-end coverage with fast, isolated checks of the
scoring branches themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eval import scoring
from eval.types import ExpectedOutcome, PostStateCheck, TaskSpec


def _spec(**expected_kwargs: Any) -> TaskSpec:
    return TaskSpec(
        id="t-fixture",
        category="edit",
        user_message="테스트",
        network="demo_two_intersection",
        expected=ExpectedOutcome(**expected_kwargs),
    )


@dataclass
class _FakeStep:
    action: str
    observation: dict[str, Any]


# ---------------------------------------------------------------------------
# values_match
# ---------------------------------------------------------------------------


def test_values_match_numeric_within_tolerance():
    assert scoring.values_match(600, 600.0000001, 1e-3) is True


def test_values_match_numeric_outside_tolerance():
    assert scoring.values_match(600, 601, 1e-3) is False


def test_values_match_non_numeric_equality():
    assert scoring.values_match("a", "a", 1e-6) is True
    assert scoring.values_match("a", "b", 1e-6) is False


def test_values_match_bool_excluded_from_numeric_tolerance_path():
    # bool is an int subclass; _is_number excludes it so a bool is never
    # compared via floating-point tolerance -- it falls through to plain
    # `==` instead, where Python's own True == 1 semantics apply (booleans
    # never appear as real task post_state values, so this is documented
    # behavior, not a bug to guard against).
    assert scoring.values_match(True, 1, 1e-6) is True
    assert scoring.values_match(True, 1.5, 1e-6) is False


# ---------------------------------------------------------------------------
# trace inspection helpers
# ---------------------------------------------------------------------------


def test_observed_write_run_dispatches_filters_to_known_tools_and_ok_status():
    trace = [
        {"kind": "action_end", "payload": {"tool": "set_vehicle_input_volume", "status": "ok"}},
        {"kind": "action_end", "payload": {"tool": "get_network_overview", "status": "ok"}},
        {"kind": "action_end", "payload": {"tool": "run_simulation", "status": "error"}},
    ]
    assert scoring.observed_write_run_dispatches(trace) == ["set_vehicle_input_volume"]


def test_observed_refusal_constraint_ids():
    trace = [
        {"kind": "refusal", "payload": {"error_type": "object_not_found"}},
        {"kind": "plan", "payload": {}},
        {"kind": "refusal", "payload": {"error_type": "volume_out_of_range"}},
    ]
    assert scoring.observed_refusal_constraint_ids(trace) == [
        "object_not_found",
        "volume_out_of_range",
    ]


def test_observed_warning_constraint_ids_flattens_findings():
    trace = [
        {"kind": "warning", "payload": {"findings": ["latent_demand_overflow"]}},
        {"kind": "warning", "payload": {"findings": ["results_missing_kpi_tables", "vehicles_deleted_in_run"]}},
    ]
    assert scoring.observed_warning_constraint_ids(trace) == [
        "latent_demand_overflow",
        "results_missing_kpi_tables",
        "vehicles_deleted_in_run",
    ]


def test_count_plan_records():
    trace = [{"kind": "plan"}, {"kind": "action_start"}, {"kind": "plan"}]
    assert scoring.count_plan_records(trace) == 2


# ---------------------------------------------------------------------------
# check_post_state
# ---------------------------------------------------------------------------


def test_check_post_state_shadow_model_pass_and_fail():
    shadow_model = {"vehicle_inputs": [{"no": "1", "volume": 600.0}]}
    checks = scoring.check_post_state(
        (
            PostStateCheck(object="vehicle_input:1", attr="volume", value=600),
            PostStateCheck(object="vehicle_input:1", attr="volume", value=999),
        ),
        final_shadow_model=shadow_model,
        history=[],
    )
    assert checks[0].passed is True
    assert checks[1].passed is False
    assert checks[1].actual == 600.0


def test_check_post_state_shadow_model_missing_object_fails():
    checks = scoring.check_post_state(
        (PostStateCheck(object="vehicle_input:999", attr="volume", value=600),),
        final_shadow_model={"vehicle_inputs": []},
        history=[],
    )
    assert checks[0].passed is False
    assert checks[0].actual is None


def test_check_post_state_verification_source_reads_last_successful_step():
    history = [
        _FakeStep("set_sim_duration", {"status": "error"}),
        _FakeStep("set_sim_duration", {"status": "ok", "verification": {"readback_value": 5400}}),
    ]
    checks = scoring.check_post_state(
        (PostStateCheck(source="verification", tool="set_sim_duration", attr="readback_value", value=5400),),
        final_shadow_model={},
        history=history,
    )
    assert checks[0].passed is True


def test_check_post_state_verification_source_no_successful_step_fails():
    checks = scoring.check_post_state(
        (PostStateCheck(source="verification", tool="set_sim_duration", attr="readback_value", value=5400),),
        final_shadow_model={},
        history=[],
    )
    assert checks[0].passed is False


# ---------------------------------------------------------------------------
# check_refusal / check_warnings / check_not_warnings / check_clarification
# ---------------------------------------------------------------------------


def test_check_refusal_not_applicable_when_must_refuse_false():
    assert scoring.check_refusal(ExpectedOutcome(must_refuse=False), dispatches=[], refusal_ids=[]) == []


def test_check_refusal_passes_on_no_dispatch_and_matching_constraint():
    checks = scoring.check_refusal(
        ExpectedOutcome(must_refuse=True, refuse_constraint_id="object_not_found"),
        dispatches=[],
        refusal_ids=["object_not_found"],
    )
    assert all(c.passed for c in checks)


def test_check_refusal_fails_when_a_write_actually_dispatched():
    checks = scoring.check_refusal(
        ExpectedOutcome(must_refuse=True),
        dispatches=["set_vehicle_input_volume"],
        refusal_ids=[],
    )
    assert checks[0].passed is False


def test_check_refusal_constraint_id_none_means_any_reason_acceptable():
    checks = scoring.check_refusal(
        ExpectedOutcome(must_refuse=True, refuse_constraint_id=None),
        dispatches=[],
        refusal_ids=[],
    )
    # only the no-dispatch check is emitted; no constraint-id check at all
    assert len(checks) == 1
    assert checks[0].passed is True


def test_check_warnings_and_not_warnings():
    warn_checks = scoring.check_warnings(("latent_demand_overflow",), ["latent_demand_overflow"])
    assert warn_checks[0].passed is True
    not_warn_checks = scoring.check_not_warnings(("results_missing_kpi_tables",), ["latent_demand_overflow"])
    assert not_warn_checks[0].passed is True
    not_warn_checks_fail = scoring.check_not_warnings(("latent_demand_overflow",), ["latent_demand_overflow"])
    assert not_warn_checks_fail[0].passed is False


def test_check_clarification():
    assert scoring.check_clarification(ExpectedOutcome(clarification_expected=False), result_kind="answer") == []
    ok = scoring.check_clarification(ExpectedOutcome(clarification_expected=True), result_kind="clarification")
    assert ok[0].passed is True
    bad = scoring.check_clarification(ExpectedOutcome(clarification_expected=True), result_kind="answer")
    assert bad[0].passed is False


# ---------------------------------------------------------------------------
# score_task
# ---------------------------------------------------------------------------


def test_score_task_happy_path_all_checks_pass():
    spec = _spec(post_state=(PostStateCheck(object="vehicle_input:1", attr="volume", value=600),))
    trace = [
        {"kind": "action_end", "payload": {"tool": "set_vehicle_input_volume", "status": "ok"}},
    ]
    scored = scoring.score_task(
        spec,
        trace_records=trace,
        history=[],
        final_shadow_model={"vehicle_inputs": [{"no": "1", "volume": 600}]},
        result_kind="answer",
        result_answer="완료했습니다.",
    )
    assert scored.success is True


def test_score_task_no_assertions_falls_back_to_terminal_state_check():
    spec = _spec()
    scored = scoring.score_task(
        spec,
        trace_records=[],
        history=[],
        final_shadow_model={},
        result_kind="answer",
        result_answer="",
    )
    assert scored.success is True
    assert scored.checks[0].description == "turn_reached_terminal_state"


# ---------------------------------------------------------------------------
# detect_silent_failure -- the killer metric
# ---------------------------------------------------------------------------


def test_detect_silent_failure_true_when_answer_claims_success_but_state_is_wrong():
    spec = _spec(post_state=(PostStateCheck(object="vehicle_input:1", attr="volume", value=999),))
    trace = [{"kind": "action_end", "payload": {"tool": "set_vehicle_input_volume", "status": "ok"}}]
    scored = scoring.score_task(
        spec,
        trace_records=trace,
        history=[],
        final_shadow_model={"vehicle_inputs": [{"no": "1", "volume": 600}]},  # NOT 999
        result_kind="answer",
        result_answer="999로 변경했습니다.",
    )
    assert scored.success is False
    assert scoring.detect_silent_failure(spec, trace_records=trace, scored=scored, result_kind="answer") is True


def test_detect_silent_failure_false_when_a_refusal_record_exists():
    spec = _spec(post_state=(PostStateCheck(object="vehicle_input:1", attr="volume", value=999),))
    trace = [{"kind": "refusal", "payload": {"error_type": "write_verification_failed"}}]
    scored = scoring.score_task(
        spec,
        trace_records=trace,
        history=[],
        final_shadow_model={"vehicle_inputs": [{"no": "1", "volume": 600}]},
        result_kind="answer",
        result_answer="문제가 발견되어 되돌렸습니다.",
    )
    assert scoring.detect_silent_failure(spec, trace_records=trace, scored=scored, result_kind="answer") is False


def test_detect_silent_failure_false_when_post_state_actually_matches():
    spec = _spec(post_state=(PostStateCheck(object="vehicle_input:1", attr="volume", value=600),))
    trace: list[dict[str, Any]] = []
    scored = scoring.score_task(
        spec,
        trace_records=trace,
        history=[],
        final_shadow_model={"vehicle_inputs": [{"no": "1", "volume": 600}]},
        result_kind="answer",
        result_answer="완료했습니다.",
    )
    assert scoring.detect_silent_failure(spec, trace_records=trace, scored=scored, result_kind="answer") is False


def test_detect_silent_failure_false_when_no_post_state_declared():
    spec = _spec()  # no post_state at all -- nothing to compare against
    scored = scoring.score_task(
        spec, trace_records=[], history=[], final_shadow_model={}, result_kind="answer", result_answer=""
    )
    assert scoring.detect_silent_failure(spec, trace_records=[], scored=scored, result_kind="answer") is False


def test_detect_silent_failure_false_when_turn_did_not_end_in_answer():
    spec = _spec(post_state=(PostStateCheck(object="vehicle_input:1", attr="volume", value=999),))
    scored = scoring.score_task(
        spec,
        trace_records=[],
        history=[],
        final_shadow_model={"vehicle_inputs": [{"no": "1", "volume": 600}]},
        result_kind="clarification",
        result_answer="어떤 입력을 말씀하시나요?",
    )
    assert (
        scoring.detect_silent_failure(spec, trace_records=[], scored=scored, result_kind="clarification")
        is False
    )

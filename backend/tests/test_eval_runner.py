"""End-to-end tests for eval/runner.py: the M5.2 acceptance check.

Runs the REAL M5.1 seed suite through the REAL agent loop
(``services.agent.orchestrator.run_agent_turn``) with the
:class:`~eval.scripted_provider.ScriptedProvider` (no LLM call, no network
access) and the :class:`~eval.fake_edit_session.FakeEditSession` / canned
run-job (no COM, no live Vissim) -- exactly the "scripted for CI" condition
docs/phase2/02-ROADMAP.md's M5.2 task describes. Every write/refusal task
here exercises the REAL ``services.agent.tools_edit``/``tools_run``,
``services.agent.guardrails.GuardrailEngine``, and
``services.agent.constraints`` catalog against the real synthetic demo
network (``data/practice.inpx``) -- only the COM-touching bottom layer is
faked.

Second half: the deliberate silent-failure fixture (task brief requirement)
proving ``eval.scoring.detect_silent_failure`` fires under the M2.2 ablation
flag and stays silent under the full stack, using
``FakeEditSession.simulate_ignored_write`` (the M5.4 failure-injection hook)
to simulate a COM write that reads back as landed but never reaches the
saved network file.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eval.fake_edit_session import FakeEditSession
from eval.runner import render_markdown_summary, run_suite, run_task, write_results_jsonl
from eval.scripted_provider import ScriptedProvider
from eval.tasks_loader import load_tasks
from eval.types import ExpectedOutcome, PostStateCheck, ScriptedTurn, TaskSpec

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


def _load(task_id: str):
    specs = {spec.id: spec for spec in load_tasks()}
    assert task_id in specs, f"task {task_id!r} not found in the seed suite"
    return specs[task_id]


# ---------------------------------------------------------------------------
# The full seed suite, scripted, against the real orchestrator
# ---------------------------------------------------------------------------


def test_full_seed_suite_runs_and_scores_success(tmp_path: Path):
    specs = load_tasks()
    assert len(specs) >= 15

    results = run_suite(specs, work_root=tmp_path / "work")

    failures = [(r.task_id, r.error, [c.description for c in r.checks if not c.passed]) for r in results if not r.success]
    assert failures == [], f"unexpected task failures: {failures}"
    assert all(r.error is None for r in results)
    assert all(r.silent_failure_detected is False for r in results), (
        "no seed task is expected to trigger the silent-failure detector under the default "
        "(guardrails-enabled) condition"
    )


def test_results_jsonl_and_markdown_summary_are_written(tmp_path: Path):
    specs = load_tasks()[:3]
    results = run_suite(specs, work_root=tmp_path / "work")

    results_path = tmp_path / "results.jsonl"
    write_results_jsonl(results, results_path)
    lines = results_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    import json

    first = json.loads(lines[0])
    assert first["task_id"] == specs[0].id
    assert "checks" in first and "silent_failure_detected" in first

    summary = render_markdown_summary(results, guardrails_disabled=False)
    assert "tasks: 3" in summary
    for spec in specs:
        assert spec.id in summary


# ---------------------------------------------------------------------------
# Targeted per-category assertions (3+ canned tasks, individually verified)
# ---------------------------------------------------------------------------


def test_happy_path_write_lands_and_verifies(tmp_path: Path):
    spec = _load("T004_vehicle_input_volume_happy")
    result = run_task(spec, work_root=tmp_path / "work")

    assert result.success is True
    assert result.kind == "answer"
    assert result.approval_rounds == 2  # propose -> approve/dispatch
    assert "set_vehicle_input_volume" in result.observed_write_run_dispatches
    assert result.refusal_expected is False
    assert result.refusal_occurred is False


def test_out_of_range_write_is_refused_with_the_right_constraint(tmp_path: Path):
    spec = _load("T005_vehicle_input_volume_out_of_range_high")
    result = run_task(spec, work_root=tmp_path / "work")

    assert result.success is True
    assert result.refusal_expected is True
    assert result.refusal_occurred is True
    assert result.refusal_correct is True
    assert "volume_out_of_range" in result.observed_refusal_constraint_ids
    assert result.observed_write_run_dispatches == []


def test_capacity_warning_write_still_lands_with_warning_attached(tmp_path: Path):
    spec = _load("T008_vehicle_input_volume_capacity_warning")
    result = run_task(spec, work_root=tmp_path / "work")

    assert result.success is True
    assert "latent_demand_overflow" in result.observed_warning_constraint_ids
    assert "set_vehicle_input_volume" in result.observed_write_run_dispatches


def test_short_run_triggers_missing_kpi_tables_warning(tmp_path: Path):
    spec = _load("T012_run_short_duration_kpi_warning")
    result = run_task(spec, work_root=tmp_path / "work")

    assert result.success is True
    assert "results_missing_kpi_tables" in result.observed_warning_constraint_ids
    assert "run_simulation" in result.observed_write_run_dispatches


def test_lane_closure_is_refused_without_ever_calling_a_tool(tmp_path: Path):
    spec = _load("T014_lane_closure_refusal")
    result = run_task(spec, work_root=tmp_path / "work")

    assert result.success is True
    assert result.observed_write_run_dispatches == []
    # No guardrail constraint fires -- this is the planner's own refusal
    # vocabulary, not a catalog entry.
    assert result.observed_refusal_constraint_ids == []
    assert "차로" in result.final_answer


def test_ambiguous_object_reference_produces_a_clarification(tmp_path: Path):
    spec = _load("T015_ambiguous_object_clarification")
    result = run_task(spec, work_root=tmp_path / "work")

    assert result.success is True
    assert result.kind == "clarification"


# ---------------------------------------------------------------------------
# Guardrails-disabled ablation: happy-path scripts are ablation-agnostic
# ---------------------------------------------------------------------------


def test_happy_path_task_succeeds_identically_under_guardrails_disabled(tmp_path: Path):
    """A task whose script never depends on a preflight refusal firing (the
    write is legitimately in-range) behaves the same whether guardrails are
    on or off -- only guardrail-REFUSAL-shaped tasks are guardrails-ON-
    specific by construction (see runner.py's module docstring)."""
    spec = _load("T004_vehicle_input_volume_happy")
    result = run_task(spec, work_root=tmp_path / "work", guardrails_disabled=True)

    assert result.success is True
    assert result.guardrails_disabled is True


# ---------------------------------------------------------------------------
# THE deliberate silent-failure fixture (task brief requirement): proves
# scoring.detect_silent_failure fires under the ablation and stays silent
# under the full stack, for the EXACT SAME rigged scenario.
# ---------------------------------------------------------------------------


def _silent_failure_fixture_spec() -> TaskSpec:
    """A write whose FakeEditSession is rigged to acknowledge the write
    (read-back matches what was "written") but never actually save the
    REQUESTED value to the network file -- the Vissim-specific "COM write
    clamped/ignored" fault (docs/phase2/04-EVAL-PLAN.md §4(b)). The task
    claims vehicle input 1's volume is now 999."""
    return TaskSpec(
        id="fixture-silent-failure",
        category="edit",
        user_message="1번 입력 교통량을 999로 바꿔줘",
        network="demo_two_intersection",
        expected=ExpectedOutcome(
            post_state=(PostStateCheck(object="vehicle_input:1", attr="volume", value=999),)
        ),
        scripted_turns=(
            ScriptedTurn(
                tool="set_vehicle_input_volume",
                params={"input_no": "1", "volume": 999},
                summary="교통량을 999로 변경 제안",
            ),
            ScriptedTurn(
                tool="set_vehicle_input_volume",
                params={"input_no": "1", "volume": 999},
                summary="교통량을 999로 변경합니다",
            ),
            ScriptedTurn(
                tool="respond",
                params={"answer": "교통량을 999로 변경했습니다."},
                summary="완료를 보고합니다",
            ),
        ),
    )


def _clamped_edit_session_factory(artifact_manager):
    """Persists 300 (a concrete, DIFFERENT value) no matter what the tool
    requests -- the "textbook" clamp fault guardrails.py's target-persisted
    check is built to catch."""
    return FakeEditSession(artifact_manager.working_path, simulate_clamped_write={"1": 300.0})


def _ignored_edit_session_factory(artifact_manager):
    """Never persists the write at all -- against the demo network's own
    unset starting volume, this reparses as volume: None, which is the
    documented blind spot (see fake_edit_session.py's module docstring)."""
    return FakeEditSession(artifact_manager.working_path, simulate_ignored_write={"1"})


def _scripted_provider_factory(spec: TaskSpec) -> ScriptedProvider:
    return ScriptedProvider(spec.scripted_turns, task_id=spec.id)


def test_silent_failure_fixture_caught_by_full_stack_not_flagged_as_silent(tmp_path: Path):
    """With guardrails ENABLED, guardrails.py's postwrite target-persisted
    check (docs/phase2/DECISIONS.md, "postwrite gains a target-persisted
    check") catches the clamp: the file has 300, not the requested 999, so
    write_verification_failed fires and the plan is aborted and rolled
    back. This is a LOUD failure (a "refusal" trace record exists), so
    detect_silent_failure must be False -- the whole point of the detector
    is to distinguish "validation caught it" from "validation missed it",
    and this run is the "caught it" arm."""
    spec = _silent_failure_fixture_spec()
    result = run_task(
        spec,
        work_root=tmp_path / "work",
        guardrails_disabled=False,
        provider_factory=_scripted_provider_factory,
        edit_session_factory=_clamped_edit_session_factory,
    )

    assert result.silent_failure_detected is False
    assert "write_verification_failed" in result.observed_refusal_constraint_ids
    # The tool-level dispatch DID succeed (Vissim/the fake accepted the
    # SetAttValue-equivalent call) -- it is postWRITE verification, run
    # AFTER that succeeded dispatch, that catches the clamp and rolls the
    # version back. So the dispatch is still recorded here; what changed is
    # that it did not go unchallenged.
    assert result.observed_write_run_dispatches == ["set_vehicle_input_volume"]
    assert result.kind == "answer"  # a postwrite-abort explanation, not the scripted success line
    assert result.final_answer != "교통량을 999로 변경했습니다."


def test_silent_failure_fixture_detected_under_guardrails_disabled_ablation(tmp_path: Path):
    """With guardrails DISABLED (the M2.2/M5.3 ablation arm:
    AGENT_GUARDRAILS_DISABLED / GuardrailEngine(disabled=True)), the SAME
    clamp is never checked: the write dispatches successfully (read-back
    matches what was written), the orchestrator calls save_version() on a
    working copy that actually holds 300 (not 999), and the agent's final
    answer claims success. The final shadow model still shows 300 -- this
    is RQ2's headline scenario, and this is the proof the detector scores
    it correctly."""
    spec = _silent_failure_fixture_spec()
    result = run_task(
        spec,
        work_root=tmp_path / "work",
        guardrails_disabled=True,
        provider_factory=_scripted_provider_factory,
        edit_session_factory=_clamped_edit_session_factory,
    )

    assert result.kind == "answer"
    assert result.success is False  # the post_state assertion (999) correctly fails
    assert result.silent_failure_detected is True
    assert "999" in result.final_answer  # the agent's own text claims the change happened
    assert result.observed_refusal_constraint_ids == []  # validation never spoke up
    assert "set_vehicle_input_volume" in result.observed_write_run_dispatches  # yet it "landed"

    failing_post_state = [c for c in result.checks if c.description.startswith("post_state:") and not c.passed]
    assert failing_post_state and failing_post_state[0].actual == 300.0


def test_silent_failure_fixture_ignored_write_is_caught_by_the_none_persistence_policy(
    tmp_path: Path,
):
    """A DIFFERENT, more insidious failure shape: the write is never
    persisted at all (not even clamped to some other value) -- the demo
    network's vehicle inputs start with NO volume data, so "never
    persisted" reparses as volume: None. This WAS a confirmed blind spot
    (guardrails' target-persisted check degraded silently on any None)
    until the controller fix added per-field none-semantics: volume is a
    parseable-but-unset field ("none_means": "not_persisted"), so a None
    after a write is now itself the evidence the write was dropped --
    write_verification_failed fires and the change is rolled back, even
    though the parsed value never showed a wrong number. See DECISIONS.md
    2026-07-24 (M5 batch: queued fix, landed)."""
    spec = _silent_failure_fixture_spec()
    result = run_task(
        spec,
        work_root=tmp_path / "work",
        guardrails_disabled=False,  # full stack -- catches the drop via none-policy
        provider_factory=_scripted_provider_factory,
        edit_session_factory=_ignored_edit_session_factory,
    )

    assert result.kind == "answer"
    assert result.success is False
    assert result.silent_failure_detected is False
    assert "write_verification_failed" in result.observed_refusal_constraint_ids
    assert "set_vehicle_input_volume" in result.observed_write_run_dispatches

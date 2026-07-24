"""Tests for eval/injection.py (M5.4): injector determinism and one real
end-to-end injected mini-run through the REAL agent loop/guardrail engine
(``services.agent.orchestrator.run_agent_turn``), mirroring the discipline
``backend/tests/test_eval_runner.py`` already established for the plain
M5.1/M5.2 suite.

No live Vissim, no paid LLM call anywhere in this file -- every trial runs
through :class:`~eval.scripted_provider.ScriptedProvider` and
:class:`~eval.fake_edit_session.FakeEditSession`/the canned run-job seam,
exactly like the rest of the eval harness.
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest

from eval.injection import (
    INJECTORS,
    InjectorError,
    _trial_rng,
    run_injection_matrix,
    run_injection_suite,
    write_injection_results_jsonl,
)
from eval.tasks_loader import load_tasks

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


# ---------------------------------------------------------------------------
# Registry sanity
# ---------------------------------------------------------------------------


def test_five_injectors_are_registered_with_the_named_faults():
    expected = {
        "wrong_param_name",
        "hallucinated_object_id",
        "clamped_write",
        "ignored_write",
        "kpi_tables_stripped",
    }
    assert set(INJECTORS) == expected


def test_every_injector_has_a_non_empty_description_and_detected_label():
    for name, injector in INJECTORS.items():
        assert injector.description.strip(), name
        assert injector.detected_label.strip(), name


# ---------------------------------------------------------------------------
# Pure-function determinism: the rng seam itself, and each injector's build()
# ---------------------------------------------------------------------------


def test_trial_rng_is_deterministic_across_independent_calls():
    a = _trial_rng(20260724, "clamped_write", 3)
    b = _trial_rng(20260724, "clamped_write", 3)
    assert [a.random() for _ in range(5)] == [b.random() for _ in range(5)]


def test_trial_rng_differs_across_trial_index_and_injector_name():
    base = [_trial_rng(1, "x", 0).random() for _ in range(3)]
    diff_index = [_trial_rng(1, "x", 1).random() for _ in range(3)]
    diff_name = [_trial_rng(1, "y", 0).random() for _ in range(3)]
    assert base != diff_index
    assert base != diff_name


def _vehicle_input_edit_specs():
    specs = {s.id: s for s in load_tasks()}
    return specs["T004_vehicle_input_volume_happy"], specs["T008_vehicle_input_volume_capacity_warning"]


def test_hallucinated_object_id_build_is_deterministic_under_fixed_seed():
    base, _ = _vehicle_input_edit_specs()
    injector = INJECTORS["hallucinated_object_id"]

    rng1 = _trial_rng(42, "hallucinated_object_id", 0)
    rng2 = _trial_rng(42, "hallucinated_object_id", 0)
    trial1 = injector.build(base, 0, rng1)
    trial2 = injector.build(base, 0, rng2)

    assert trial1.label == trial2.label
    assert trial1.task_spec.scripted_turns[0].params == trial2.task_spec.scripted_turns[0].params


def test_hallucinated_object_id_build_varies_with_seed():
    base, _ = _vehicle_input_edit_specs()
    injector = INJECTORS["hallucinated_object_id"]

    trial_seed_a = injector.build(base, 0, _trial_rng(1, "hallucinated_object_id", 0))
    trial_seed_b = injector.build(base, 0, _trial_rng(2, "hallucinated_object_id", 0))

    assert trial_seed_a.label != trial_seed_b.label


def test_clamped_write_build_is_deterministic_under_fixed_seed():
    base, _ = _vehicle_input_edit_specs()
    injector = INJECTORS["clamped_write"]

    trial1 = injector.build(base, 2, _trial_rng(7, "clamped_write", 2))
    trial2 = injector.build(base, 2, _trial_rng(7, "clamped_write", 2))

    assert trial1.label == trial2.label


def test_wrong_param_name_build_renames_the_correct_key_for_each_tool():
    injector = INJECTORS["wrong_param_name"]
    specs = {s.id: s for s in load_tasks()}

    vehicle_trial = injector.build(specs["T004_vehicle_input_volume_happy"], 0, _trial_rng(1, "wrong_param_name", 0))
    assert vehicle_trial.task_spec.scripted_turns[0].tool == "set_vehicle_input_volume"
    assert "volume" not in vehicle_trial.task_spec.scripted_turns[0].params
    assert "vol" in vehicle_trial.task_spec.scripted_turns[0].params
    assert vehicle_trial.task_spec.scripted_turns[0].params["vol"] == 600
    # input_no (the object-id param) must be untouched -- this injector only
    # ever corrupts a NON-id param, by construction.
    assert vehicle_trial.task_spec.scripted_turns[0].params["input_no"] == "1"

    duration_trial = injector.build(specs["T009_sim_duration_happy"], 0, _trial_rng(1, "wrong_param_name", 0))
    assert "seconds" not in duration_trial.task_spec.scripted_turns[0].params
    assert duration_trial.task_spec.scripted_turns[0].params["duration_s"] == 5400

    seed_trial = injector.build(specs["T010_random_seed_happy"], 0, _trial_rng(1, "wrong_param_name", 0))
    assert "seed" not in seed_trial.task_spec.scripted_turns[0].params
    assert seed_trial.task_spec.scripted_turns[0].params["random_seed"] == 77

    # The final "respond" turn (claiming success) must be left untouched --
    # that is the whole point of the fault: the claim survives even though
    # the write never actually dispatched.
    assert vehicle_trial.task_spec.scripted_turns[-1].tool == "respond"
    assert "600" in vehicle_trial.task_spec.scripted_turns[-1].params["answer"]


# ---------------------------------------------------------------------------
# Eligibility filters
# ---------------------------------------------------------------------------


def test_eligible_filters_select_the_expected_seed_tasks():
    specs = load_tasks()

    wrong_param_ids = {s.id for s in specs if INJECTORS["wrong_param_name"].eligible(s)}
    assert wrong_param_ids == {
        "T004_vehicle_input_volume_happy",
        "T008_vehicle_input_volume_capacity_warning",
        "T009_sim_duration_happy",
        "T010_random_seed_happy",
    }

    hallucinated_ids = {s.id for s in specs if INJECTORS["hallucinated_object_id"].eligible(s)}
    assert hallucinated_ids == {
        "T004_vehicle_input_volume_happy",
        "T008_vehicle_input_volume_capacity_warning",
    }

    clamped_ids = {s.id for s in specs if INJECTORS["clamped_write"].eligible(s)}
    assert clamped_ids == hallucinated_ids  # same eligibility shape today

    kpi_ids = {s.id for s in specs if INJECTORS["kpi_tables_stripped"].eligible(s)}
    assert kpi_ids == {
        "T012_run_short_duration_kpi_warning",
        "T013_run_normal_duration_happy",
    }

    # T006/T005 are themselves refusal tasks -- must never be selected as a
    # base task to further corrupt (must_refuse tasks are excluded).
    assert "T006_vehicle_input_nonexistent" not in hallucinated_ids
    assert "T005_vehicle_input_volume_out_of_range_high" not in hallucinated_ids


def test_run_injection_suite_raises_for_unknown_injector(tmp_path: Path):
    with pytest.raises(InjectorError):
        run_injection_suite("not_a_real_injector", load_tasks(), n=1, seed=1, work_root=tmp_path)


def test_run_injection_suite_raises_for_non_positive_n(tmp_path: Path):
    with pytest.raises(InjectorError):
        run_injection_suite("clamped_write", load_tasks(), n=0, seed=1, work_root=tmp_path)


def test_run_injection_suite_raises_when_corpus_has_no_eligible_task(tmp_path: Path):
    specs = [s for s in load_tasks() if s.id == "T001_network_overview_read"]
    with pytest.raises(InjectorError):
        run_injection_suite("clamped_write", specs, n=1, seed=1, work_root=tmp_path)


# ---------------------------------------------------------------------------
# End-to-end: injected trials through the REAL orchestrator/guardrail stack
# ---------------------------------------------------------------------------


def _stable_fields(trial_result) -> dict:
    """The subset of an InjectionTrialResult that must be byte-identical
    across two independently-run suites with the same seed (excludes
    wall-clock-derived fields like trace_path/wall_latency_s)."""
    return {
        "injector": trial_result.injector,
        "arm": trial_result.arm,
        "base_task_id": trial_result.base_task_id,
        "trial_id": trial_result.trial_id,
        "label": trial_result.label,
        "detected": trial_result.detected,
        "refused": trial_result.refused,
        "success": trial_result.success,
        "silent_failure_detected": trial_result.silent_failure_detected,
        "kind": trial_result.kind,
    }


def test_clamped_write_injection_run_is_deterministic_end_to_end(tmp_path: Path):
    specs = load_tasks()
    run_a = run_injection_suite("clamped_write", specs, n=3, seed=999, work_root=tmp_path / "a")
    run_b = run_injection_suite("clamped_write", specs, n=3, seed=999, work_root=tmp_path / "b")

    assert [_stable_fields(r) for r in run_a] == [_stable_fields(r) for r in run_b]


def test_mini_run_three_trials_one_injector_full_stack_catches_the_clamp(tmp_path: Path):
    """The task brief's required end-to-end check: 3 trials x 1 injector
    (clamped_write) through the REAL runner, full stack (guardrails ON).
    Every trial's clamp must be caught (detected=True), exactly like the
    single hand-built fixture in test_eval_runner.py, now run N times with a
    seeded, varying clamped value each time."""
    specs = load_tasks()
    results = run_injection_suite(
        "clamped_write", specs, n=3, seed=20260724, work_root=tmp_path, guardrails_disabled=False
    )

    assert len(results) == 3
    assert all(r.arm == "full_stack" for r in results)
    assert all(r.detected is True for r in results), [r.label for r in results if not r.detected]
    assert all(r.silent_failure_detected is False for r in results)
    assert all("write_verification_failed" in r.task_result["observed_refusal_constraint_ids"] for r in results)
    # The base task cycles across the 2 eligible tasks (T004, T008), so
    # trial 2 wraps back to the same base task as trial 0.
    assert results[0].base_task_id == results[2].base_task_id
    assert results[0].base_task_id != results[1].base_task_id


def test_mini_run_three_trials_one_injector_ablation_misses_the_clamp(tmp_path: Path):
    """Same 3 trials, same injector, guardrails DISABLED (the M5.3 ablation
    arm): the clamp is never checked, so every trial is an undetected silent
    failure -- this is RQ2's headline contrast, reproduced via the injector
    abstraction with N trials instead of one hand-built fixture."""
    specs = load_tasks()
    results = run_injection_suite(
        "clamped_write", specs, n=3, seed=20260724, work_root=tmp_path, guardrails_disabled=True
    )

    assert len(results) == 3
    assert all(r.arm == "guardrails_disabled" for r in results)
    assert all(r.detected is False for r in results)
    assert all(r.silent_failure_detected is True for r in results)
    assert all(r.task_result["observed_refusal_constraint_ids"] == [] for r in results)


def test_ignored_write_is_undetected_under_both_arms_the_documented_blind_spot(tmp_path: Path):
    specs = load_tasks()
    full_stack = run_injection_suite(
        "ignored_write", specs, n=2, seed=1, work_root=tmp_path / "full", guardrails_disabled=False
    )
    ablation = run_injection_suite(
        "ignored_write", specs, n=2, seed=1, work_root=tmp_path / "ablation", guardrails_disabled=True
    )

    assert all(r.detected is False for r in full_stack), "documented blind spot: full stack still misses this"
    assert all(r.detected is False for r in ablation)


def test_hallucinated_object_id_detected_only_under_full_stack(tmp_path: Path):
    specs = load_tasks()
    full_stack = run_injection_suite(
        "hallucinated_object_id", specs, n=2, seed=5, work_root=tmp_path / "full", guardrails_disabled=False
    )
    ablation = run_injection_suite(
        "hallucinated_object_id", specs, n=2, seed=5, work_root=tmp_path / "ablation", guardrails_disabled=True
    )

    assert all(r.detected is True for r in full_stack)
    assert all("object_not_found" in r.task_result["observed_refusal_constraint_ids"] for r in full_stack)
    # Under the ablation, the specific guardrail signal never fires -- even
    # though (for vehicle inputs specifically) the fake edit session's XML
    # matcher happens to raise on an unknown id, that is an accidental,
    # non-guardrail catch and this injector deliberately does not count it.
    assert all(r.detected is False for r in ablation)


def test_kpi_tables_stripped_detected_only_under_full_stack(tmp_path: Path):
    specs = load_tasks()
    full_stack = run_injection_suite(
        "kpi_tables_stripped", specs, n=2, seed=3, work_root=tmp_path / "full", guardrails_disabled=False
    )
    ablation = run_injection_suite(
        "kpi_tables_stripped", specs, n=2, seed=3, work_root=tmp_path / "ablation", guardrails_disabled=True
    )

    assert all(r.detected is True for r in full_stack)
    assert all(r.detected is False for r in ablation)


def test_wrong_param_name_is_undetected_under_both_arms(tmp_path: Path):
    """The dispatch-level invalid_params gap this task's brief asks us to
    surface: no trace 'refusal' record is ever written for a pydantic
    schema rejection, so RQ2's silent-failure detector reads this as an
    undetected lie regardless of the guardrails flag -- guardrails never
    even get a chance to run (dispatch fails before postwrite)."""
    specs = load_tasks()
    full_stack = run_injection_suite(
        "wrong_param_name", specs, n=2, seed=11, work_root=tmp_path / "full", guardrails_disabled=False
    )
    ablation = run_injection_suite(
        "wrong_param_name", specs, n=2, seed=11, work_root=tmp_path / "ablation", guardrails_disabled=True
    )

    assert all(r.detected is False for r in full_stack)
    assert all(r.detected is False for r in ablation)
    assert all(r.success is False for r in full_stack)  # post_state correctly fails


# ---------------------------------------------------------------------------
# run_injection_matrix + JSONL round-trip
# ---------------------------------------------------------------------------


def test_run_injection_matrix_covers_every_named_injector_in_order(tmp_path: Path):
    specs = load_tasks()
    names = ["clamped_write", "kpi_tables_stripped"]
    results = run_injection_matrix(names, specs, n=1, seed=1, work_root=tmp_path)

    assert [r.injector for r in results] == names


def test_write_injection_results_jsonl_round_trips(tmp_path: Path):
    import json

    specs = load_tasks()
    results = run_injection_suite("clamped_write", specs, n=2, seed=1, work_root=tmp_path / "work")
    out_path = tmp_path / "results.jsonl"
    write_injection_results_jsonl(results, out_path)

    lines = out_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["injector"] == "clamped_write"
    assert "task_result" in first and "checks" in first["task_result"]

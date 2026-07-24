"""M5.4: the failure-injection protocol (docs/phase2/04-EVAL-PLAN.md §4,
adapted from ChatSUMO-Agent §4.4 -- see docs/phase2/03-RESEARCH-DIGEST.md).

Five named, deterministic fault injectors, each a pure function registered
in :data:`INJECTORS` via the :func:`register_injector` decorator (addressable
by name from this module's CLI):

- ``wrong_param_name`` -- the provider hallucinates a plausible-but-wrong
  param key for a write tool (e.g. ``volume`` -> ``vol``).
- ``hallucinated_object_id`` -- the provider names an object id that does
  not exist in the shadow model (wrong-object grounding, EVAL-PLAN §4(a)).
- ``clamped_write`` -- the COM write is acknowledged (its own read-back
  matches) but a DIFFERENT value is what actually reaches the saved network
  file (EVAL-PLAN §4(b), the "clamped" half).
- ``ignored_write`` -- the write never reaches the saved file at all
  (EVAL-PLAN §4(b), the "ignored" half).
- ``kpi_tables_stripped`` -- the run's result database is forced down to
  ONLY the ``SIMULATIONRUN`` table, regardless of what the task's own run
  would normally produce (EVAL-PLAN §4(c), "run producing lie conditions").

Every injector wraps the runner's EXISTING seams (``eval.runner.run_task``'s
``provider_factory``/``edit_session_factory``/``run_job_result`` params) --
none of them reach into ``services.agent`` internals, and none of them
change ``eval/runner.py``, ``eval/scoring.py``, or ``eval/types.py``. A
faulted task runs through the REAL agent loop, REAL guardrail engine, and
REAL tools_edit/tools_run exactly like an ordinary M5.1 seed task; only the
edit-session/provider/run-job adapter underneath it is rigged.

**What "detected" means, per injector** (documented per-injector below and
surfaced via ``Injector.detected_label``): this is deliberately NOT one
uniform definition, because the five faults are caught (or missed) by
different layers of the stack:

- ``wrong_param_name``/``clamped_write``/``ignored_write`` reuse
  ``eval.scoring.detect_silent_failure`` (RQ2's own headline detector) --
  "detected" means the turn did NOT end in an undetected lie. This is a
  deliberate choice of "the simplest deterministic option" (per this task's
  FABLE-PLANNED note) over inventing a parallel detection heuristic: it
  reuses machinery this suite already trusts (``backend/tests/
  test_eval_runner.py``'s silent-failure fixture pins the same function).
- ``hallucinated_object_id`` measures the SPECIFIC guardrail catch
  (``object_not_found`` in the refusal trace), not merely "nothing landed"
  -- under the guardrails-disabled ablation, a hallucinated vehicle-input id
  still fails to persist (the fake edit session's XML matcher raises for an
  unknown id), which is a real but ACCIDENTAL catch, not the validation
  layer working. Scoring it as "detected" would overstate the ablation
  arm's safety, so this injector only counts the guardrail-specific signal.
- ``kpi_tables_stripped`` measures the specific postrun warning
  (``results_missing_kpi_tables``), since this fault is warn-tier by design
  (the run is not refused; the DATA it produced is), not refuse-tier.

Trial selection is fully deterministic given ``(seed, injector_name,
trial_index)``: the base task cycles through the injector's eligible corpus
subset (sorted by task id) via ``trial_index % len(eligible)``, and any
injector-internal randomness (which nonexistent id, which clamped value)
draws from ``random.Random(f"{seed}:{injector_name}:{trial_index}")`` --
Python seeds a ``Random`` from a string via a fixed SHA-512-based algorithm,
so this is reproducible across processes/interpreters, unlike raw ``hash()``
(which is salted per-process unless ``PYTHONHASHSEED`` is pinned).
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable

from eval.fake_edit_session import FakeEditSession
from eval.fake_run_job import canned_terminal_job_record
from eval.runner import EditSessionFactory, ProviderFactory, run_task
from eval.tasks_loader import load_tasks
from eval.types import ExpectedOutcome, ScriptedTurn, TaskResult, TaskSpec

# ---------------------------------------------------------------------------
# Injector plumbing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InjectedTrial:
    """One injector's rigged inputs for a single trial -- everything
    :func:`~eval.runner.run_task` needs beyond ``work_root``/
    ``guardrails_disabled``. ``label`` is a short, human-readable
    description of exactly what this trial corrupted (rendered into the
    trial's result record for a diagnosable results table)."""

    task_spec: TaskSpec
    label: str
    provider_factory: ProviderFactory | None = None
    edit_session_factory: EditSessionFactory | None = None
    run_job_result: dict[str, Any] | None = None


BuildFn = Callable[[TaskSpec, int, "random.Random"], InjectedTrial]
DetectedFn = Callable[[TaskResult], bool]
EligibleFn = Callable[[TaskSpec], bool]


@dataclass(frozen=True)
class Injector:
    """One registered fault injector: identity, applicability, how to build
    a rigged trial, and how to score whether the fault was DETECTED (caught
    loudly by the validation layer) versus slipped through undetected."""

    name: str
    description: str
    eligible: EligibleFn
    build: BuildFn
    detected: DetectedFn
    detected_label: str


INJECTORS: dict[str, Injector] = {}


class InjectorError(ValueError):
    """An injector was misconfigured or misused (unknown name, empty
    eligible corpus, non-positive trial count)."""


def register_injector(
    *,
    name: str,
    description: str,
    eligible: EligibleFn,
    detected: DetectedFn,
    detected_label: str,
) -> Callable[[BuildFn], BuildFn]:
    """Decorator registering ``build_fn`` as the named injector in
    :data:`INJECTORS`. Raises if ``name`` is already registered (duplicate
    injector names would silently shadow each other, same anti-drift
    discipline as ``toolkit.ToolRegistry.register``)."""

    def decorator(build_fn: BuildFn) -> BuildFn:
        if name in INJECTORS:
            raise InjectorError(f"Injector {name!r} is already registered")
        INJECTORS[name] = Injector(
            name=name,
            description=description,
            eligible=eligible,
            build=build_fn,
            detected=detected,
            detected_label=detected_label,
        )
        return build_fn

    return decorator


def _trial_id(injector_name: str, base_id: str, trial_index: int) -> str:
    return f"inj-{injector_name}-{base_id}-{trial_index:03d}"


def _trial_rng(seed: int, injector_name: str, trial_index: int) -> random.Random:
    """A ``random.Random`` seeded deterministically from ``(seed,
    injector_name, trial_index)`` -- see module docstring on why a string
    seed is safe for cross-process reproducibility."""
    return random.Random(f"{seed}:{injector_name}:{trial_index}")


# ---------------------------------------------------------------------------
# 1. wrong_param_name
# ---------------------------------------------------------------------------

# {tool: (correct_param, corrupted_param)} -- the corrupted key is always a
# NON-id param (never input_no/sc_no), so this exercises toolkit.dispatch's
# pydantic extra="forbid" validation specifically, not constraints.py's
# object_not_found check (see hallucinated_object_id below for that one).
_WRONG_PARAM_NAME_TARGETS: dict[str, tuple[str, str]] = {
    "set_vehicle_input_volume": ("volume", "vol"),
    "set_sim_duration": ("seconds", "duration_s"),
    "set_random_seed": ("seed", "random_seed"),
}


def _rename_scripted_turn_param(turn: ScriptedTurn, tool: str, old_key: str, new_key: str) -> ScriptedTurn:
    if turn.tool != tool or old_key not in turn.params:
        return turn
    new_params = dict(turn.params)
    new_params[new_key] = new_params.pop(old_key)
    return ScriptedTurn(tool=turn.tool, params=new_params, summary=turn.summary)


def _wrong_param_name_eligible(spec: TaskSpec) -> bool:
    if spec.category != "edit" or not spec.scripted_turns:
        return False
    return spec.scripted_turns[0].tool in _WRONG_PARAM_NAME_TARGETS


@register_injector(
    name="wrong_param_name",
    description=(
        "Provider emits a plausible-but-wrong param name for the target write tool (e.g. "
        "volume -> vol). toolkit.dispatch's extra='forbid' pydantic validation rejects it as "
        "invalid_params -- but that rejection happens beneath the guardrail sandwich (dispatch "
        "fails before postwrite ever runs, so no 'refusal'-kind trace record is ever written "
        "for it). See this task's drafted DECISIONS.md entry."
    ),
    eligible=_wrong_param_name_eligible,
    detected=lambda result: not result.silent_failure_detected,
    detected_label="not silent_failure_detected (RQ2's own detector: a write that never landed "
    "was correctly recognized as a lie, not silently believed)",
)
def _build_wrong_param_name_trial(base: TaskSpec, trial_index: int, rng: random.Random) -> InjectedTrial:
    del rng  # deterministic rename; no randomness needed for this injector
    tool = base.scripted_turns[0].tool
    old_key, new_key = _WRONG_PARAM_NAME_TARGETS[tool]
    corrupted_turns = tuple(
        _rename_scripted_turn_param(turn, tool, old_key, new_key) for turn in base.scripted_turns
    )
    trial_spec = replace(
        base,
        id=_trial_id("wrong_param_name", base.id, trial_index),
        scripted_turns=corrupted_turns,
    )
    return InjectedTrial(task_spec=trial_spec, label=f"{tool}: {old_key!r} -> {new_key!r}")


# ---------------------------------------------------------------------------
# 2. hallucinated_object_id
# ---------------------------------------------------------------------------

_OBJECT_ID_PARAM: dict[str, str] = {
    "set_vehicle_input_volume": "input_no",
    "set_signal_cycle": "sc_no",
    "set_signal_offset": "sc_no",
}


def _hallucinated_id_eligible(spec: TaskSpec) -> bool:
    if spec.category != "edit" or spec.expected.must_refuse or not spec.scripted_turns:
        return False
    return spec.scripted_turns[0].tool in _OBJECT_ID_PARAM


@register_injector(
    name="hallucinated_object_id",
    description=(
        "Provider names an object id absent from the shadow model (wrong-object grounding, "
        "docs/phase2/04-EVAL-PLAN.md #4(a)). constraints.py's object_not_found preflight check "
        "reads the id straight off action['params'][id_param] (not through pydantic), so this "
        "fires BEFORE dispatch under the full stack."
    ),
    eligible=_hallucinated_id_eligible,
    detected=lambda result: "object_not_found" in result.observed_refusal_constraint_ids,
    detected_label="'object_not_found' present in observed_refusal_constraint_ids (the specific "
    "guardrail catch, not merely 'nothing landed' -- see module docstring)",
)
def _build_hallucinated_object_id_trial(
    base: TaskSpec, trial_index: int, rng: random.Random
) -> InjectedTrial:
    tool = base.scripted_turns[0].tool
    id_param = _OBJECT_ID_PARAM[tool]
    # Deliberately far outside the demo network's real id range (single/low
    # double digits) so this is never coincidentally a real id.
    bogus_id = str(9001 + rng.randrange(0, 900))
    propose = base.scripted_turns[0]
    corrupted_params = dict(propose.params)
    corrupted_params[id_param] = bogus_id
    trial_spec = replace(
        base,
        id=_trial_id("hallucinated_object_id", base.id, trial_index),
        scripted_turns=(
            ScriptedTurn(tool=tool, params=corrupted_params, summary=propose.summary),
            ScriptedTurn(
                tool="respond",
                params={"answer": f"요청하신 대상 '{bogus_id}'을(를) 찾을 수 없습니다."},
                summary="대상 없음을 안내합니다",
            ),
        ),
        expected=ExpectedOutcome(must_refuse=True, refuse_constraint_id="object_not_found"),
    )
    return InjectedTrial(task_spec=trial_spec, label=f"{tool}: {id_param}={bogus_id!r} (nonexistent)")


# ---------------------------------------------------------------------------
# 3. clamped_write
# ---------------------------------------------------------------------------


def _vehicle_input_write_eligible(spec: TaskSpec) -> bool:
    if spec.category != "edit" or spec.expected.must_refuse or not spec.scripted_turns:
        return False
    return spec.scripted_turns[0].tool == "set_vehicle_input_volume"


@register_injector(
    name="clamped_write",
    description=(
        "The Vissim-specific 'COM write clamped' fault (docs/phase2/04-EVAL-PLAN.md #4(b)): the "
        "edit session acknowledges the write (its own read-back matches what was requested) but "
        "persists a DIFFERENT concrete value to the saved network file. guardrails.py's postwrite "
        "target-persisted check (docs/phase2/DECISIONS.md, 'postwrite gains a target-persisted "
        "check') is built to catch exactly this."
    ),
    eligible=_vehicle_input_write_eligible,
    detected=lambda result: not result.silent_failure_detected,
    detected_label="not silent_failure_detected (the target-persisted postwrite check caught the "
    "clamp and rolled the write back)",
)
def _build_clamped_write_trial(base: TaskSpec, trial_index: int, rng: random.Random) -> InjectedTrial:
    propose = base.scripted_turns[0]
    input_no = str(propose.params.get("input_no"))
    requested_volume = float(propose.params.get("volume"))
    clamped_volume = requested_volume + 100.0 + rng.randrange(1, 50)  # guaranteed != requested
    trial_spec = replace(base, id=_trial_id("clamped_write", base.id, trial_index))

    def _edit_session_factory(artifact_manager: Any, _input_no: str = input_no, _clamped: float = clamped_volume) -> FakeEditSession:
        return FakeEditSession(artifact_manager.working_path, simulate_clamped_write={_input_no: _clamped})

    return InjectedTrial(
        task_spec=trial_spec,
        label=f"input {input_no}: requested {requested_volume} -> persisted {clamped_volume}",
        edit_session_factory=_edit_session_factory,
    )


# ---------------------------------------------------------------------------
# 4. ignored_write
# ---------------------------------------------------------------------------


@register_injector(
    name="ignored_write",
    description=(
        "The Vissim-specific 'COM write ignored' fault (docs/phase2/04-EVAL-PLAN.md #4(b)): the "
        "write never reaches the saved network file at all. Against the demo network's own unset "
        "starting volume this reparses as None, which guardrails.py's target-persisted check "
        "explicitly degrades silently on (by design, to avoid a false positive against a genuine "
        "parser gap) -- CONFIRMED BLIND SPOT already recorded in docs/phase2/DECISIONS.md's "
        "'2026-07-24 M5.1+M5.2 decision batch'."
    ),
    eligible=_vehicle_input_write_eligible,
    detected=lambda result: not result.silent_failure_detected,
    detected_label="not silent_failure_detected -- expected to read FALSE under both arms today "
    "(see the pre-existing, documented blind spot this injector re-confirms with N trials + CIs)",
)
def _build_ignored_write_trial(base: TaskSpec, trial_index: int, rng: random.Random) -> InjectedTrial:
    del rng  # nothing to randomize: the fault is "never persisted", not a value choice
    propose = base.scripted_turns[0]
    input_no = str(propose.params.get("input_no"))
    trial_spec = replace(base, id=_trial_id("ignored_write", base.id, trial_index))

    def _edit_session_factory(artifact_manager: Any, _input_no: str = input_no) -> FakeEditSession:
        return FakeEditSession(artifact_manager.working_path, simulate_ignored_write={_input_no})

    return InjectedTrial(
        task_spec=trial_spec,
        label=f"input {input_no}: write silently dropped, never persisted",
        edit_session_factory=_edit_session_factory,
    )


# ---------------------------------------------------------------------------
# 5. kpi_tables_stripped
# ---------------------------------------------------------------------------


def _run_task_eligible(spec: TaskSpec) -> bool:
    return (
        spec.category == "run"
        and bool(spec.scripted_turns)
        and spec.scripted_turns[0].tool == "run_simulation"
    )


@register_injector(
    name="kpi_tables_stripped",
    description=(
        "The Vissim-specific 'short/degenerate run' fault (docs/phase2/04-EVAL-PLAN.md #4(c)): "
        "the result database is forced down to ONLY the SIMULATIONRUN table -- as if evaluation "
        "intervals never elapsed -- regardless of what the task's own run would normally "
        "produce, exercising constraints.py's results_missing_kpi_tables postrun warning."
    ),
    eligible=_run_task_eligible,
    detected=lambda result: "results_missing_kpi_tables" in result.observed_warning_constraint_ids,
    detected_label="'results_missing_kpi_tables' present in observed_warning_constraint_ids "
    "(warn-tier by design: the run is not refused, the data it produced is flagged)",
)
def _build_kpi_tables_stripped_trial(base: TaskSpec, trial_index: int, rng: random.Random) -> InjectedTrial:
    del rng  # the fault is a fixed table-set override, not a value choice
    trial_spec = replace(base, id=_trial_id("kpi_tables_stripped", base.id, trial_index))
    return InjectedTrial(
        task_spec=trial_spec,
        label="result db forced to ['SIMULATIONRUN'] regardless of the task's own fake_run_result",
        run_job_result=canned_terminal_job_record(tables=["SIMULATIONRUN"]),
    )


# ---------------------------------------------------------------------------
# Running a suite of trials for one injector
# ---------------------------------------------------------------------------


@dataclass
class InjectionTrialResult:
    """One injected trial's outcome -- the M5.4 per-trial result record
    ``report.py`` consumes (one JSON line per trial)."""

    injector: str
    arm: str
    base_task_id: str
    trial_id: str
    trial_index: int
    seed: int
    label: str
    detected: bool
    refused: bool
    success: bool
    silent_failure_detected: bool
    kind: str
    guardrails_disabled: bool
    task_result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "injector": self.injector,
            "arm": self.arm,
            "base_task_id": self.base_task_id,
            "trial_id": self.trial_id,
            "trial_index": self.trial_index,
            "seed": self.seed,
            "label": self.label,
            "detected": self.detected,
            "refused": self.refused,
            "success": self.success,
            "silent_failure_detected": self.silent_failure_detected,
            "kind": self.kind,
            "guardrails_disabled": self.guardrails_disabled,
            "task_result": self.task_result,
        }


def _default_arm(guardrails_disabled: bool) -> str:
    return "guardrails_disabled" if guardrails_disabled else "full_stack"


def run_injection_suite(
    injector_name: str,
    specs: Iterable[TaskSpec],
    *,
    n: int,
    seed: int,
    work_root: Path,
    guardrails_disabled: bool = False,
    arm: str | None = None,
) -> list[InjectionTrialResult]:
    """Run ``n`` deterministic trials of the named injector against the
    eligible subset of ``specs`` (sorted by task id, cycled via ``trial_index
    % len(eligible)``) through the REAL :func:`~eval.runner.run_task`, and
    return one :class:`InjectionTrialResult` per trial, in trial order.

    Raises :class:`InjectorError` for an unknown injector name, a non-positive
    ``n``, or a corpus with no eligible base task for this injector.
    """
    injector = INJECTORS.get(injector_name)
    if injector is None:
        raise InjectorError(f"Unknown injector {injector_name!r}. Known: {sorted(INJECTORS)}")
    if n < 1:
        raise InjectorError(f"n must be >= 1, got {n!r}")

    eligible_specs = sorted((s for s in specs if injector.eligible(s)), key=lambda s: s.id)
    if not eligible_specs:
        raise InjectorError(
            f"No eligible base task in the given corpus for injector {injector_name!r}"
        )

    resolved_arm = arm or _default_arm(guardrails_disabled)
    results: list[InjectionTrialResult] = []
    for trial_index in range(n):
        base = eligible_specs[trial_index % len(eligible_specs)]
        rng = _trial_rng(seed, injector_name, trial_index)
        trial = injector.build(base, trial_index, rng)

        result = run_task(
            trial.task_spec,
            work_root=work_root,
            guardrails_disabled=guardrails_disabled,
            provider_factory=trial.provider_factory,
            edit_session_factory=trial.edit_session_factory,
            run_job_result=trial.run_job_result,
        )

        results.append(
            InjectionTrialResult(
                injector=injector_name,
                arm=resolved_arm,
                base_task_id=base.id,
                trial_id=trial.task_spec.id,
                trial_index=trial_index,
                seed=seed,
                label=trial.label,
                detected=injector.detected(result),
                refused=result.refusal_occurred,
                success=result.success,
                silent_failure_detected=result.silent_failure_detected,
                kind=result.kind,
                guardrails_disabled=guardrails_disabled,
                task_result=result.to_dict(),
            )
        )
    return results


def run_injection_matrix(
    injector_names: Iterable[str],
    specs: Iterable[TaskSpec],
    *,
    n: int,
    seed: int,
    work_root: Path,
    guardrails_disabled: bool = False,
    arm: str | None = None,
) -> list[InjectionTrialResult]:
    """Run every named injector's suite (in the given order) against the SAME
    corpus and return the concatenated trial list -- the convenience
    :func:`main` uses to cover every injector in one CLI invocation."""
    specs = list(specs)
    all_results: list[InjectionTrialResult] = []
    for name in injector_names:
        all_results.extend(
            run_injection_suite(
                name,
                specs,
                n=n,
                seed=seed,
                work_root=work_root,
                guardrails_disabled=guardrails_disabled,
                arm=arm,
            )
        )
    return all_results


def write_injection_results_jsonl(results: list[InjectionTrialResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="M5.4 failure-injection protocol")
    parser.add_argument(
        "--injector",
        action="append",
        default=None,
        help="Injector name (repeatable). Default: every registered injector.",
    )
    parser.add_argument("--n", type=int, default=10, help="Trials per injector (default 10).")
    parser.add_argument(
        "--seed", type=int, default=20260724, help="Deterministic base seed (default 20260724)."
    )
    parser.add_argument("--tasks-dir", type=Path, default=None, help="Defaults to backend/eval/tasks/")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "results" / "injection",
        help="Where results.jsonl and per-trial work dirs are written",
    )
    parser.add_argument(
        "--guardrails-disabled",
        action="store_true",
        help="M2.2/M5.3 ablation arm: run every trial with GuardrailEngine(disabled=True).",
    )
    parser.add_argument(
        "--arm",
        type=str,
        default=None,
        help="Arm label override (default: 'full_stack' / 'guardrails_disabled').",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    names = args.injector or sorted(INJECTORS)
    unknown = set(names) - set(INJECTORS)
    if unknown:
        raise SystemExit(f"Unknown --injector value(s): {sorted(unknown)}. Known: {sorted(INJECTORS)}")

    specs = load_tasks(args.tasks_dir)
    work_root = args.out_dir / "work"
    work_root.mkdir(parents=True, exist_ok=True)

    results = run_injection_matrix(
        names,
        specs,
        n=args.n,
        seed=args.seed,
        work_root=work_root,
        guardrails_disabled=args.guardrails_disabled,
        arm=args.arm,
    )

    out_path = args.out_dir / "results.jsonl"
    write_injection_results_jsonl(results, out_path)
    print(f"Wrote {len(results)} injection trial(s) to {out_path}")

    from eval.report import load_injection_records, render_full_report  # local: keep report.py optional for CI

    print(render_full_report(load_injection_records([out_path])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "INJECTORS",
    "InjectedTrial",
    "Injector",
    "InjectorError",
    "InjectionTrialResult",
    "main",
    "register_injector",
    "run_injection_matrix",
    "run_injection_suite",
    "write_injection_results_jsonl",
]

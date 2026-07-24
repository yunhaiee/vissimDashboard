"""M5.3: eval baselines -- alternative adapters run over the SAME M5.1 task
corpus (``backend/eval/tasks/*.yaml``), scored into the SAME
:class:`~eval.types.TaskResult` shape :func:`eval.runner.run_task` produces
-- see docs/phase2/04-EVAL-PLAN.md §4's condition table.

Every adapter here exposes ``run(spec, *, work_root) -> TaskResult`` (the
:class:`BaselineAdapter` duck type) so report code (markdown tables,
per-arm suite comparisons) needs ZERO baseline-specific branches: it can
treat a list of :class:`~eval.types.TaskResult` from any adapter exactly
like the full-agent runner's own output.

Two adapters ship:

- :class:`RegexBaselineAdapter` -- wraps the existing, pre-agent
  ``services.assistant_router.parse_prompt_simulation_command`` (read-only;
  nothing in that module is modified). Its ``SimParams``-shaped output is
  mapped onto direct calls against an :class:`~eval.fake_edit_session.
  FakeEditSession` -- the SAME fake-edit-session seam :func:`eval.runner.
  run_task` drives the real agent through -- so post-state scoring is
  apples-to-apples between arms (same XML-mutation code, same
  ``ArtifactManager``/``build_shadow_model`` re-parse). This baseline has NO
  refusal vocabulary and NO clarification vocabulary: every run either
  applies a recognized command directly (no guardrail checks of any kind --
  there is no guardrail *engine* here to disable) or falls through to a
  generic "didn't understand" answer. On a ``must_refuse``/
  ``clarification_expected`` task it cannot express, it is scored by the
  SAME :mod:`eval.scoring` functions the full agent is scored by --
  deliberately NOT special-cased to pass. See this module's tests for the
  resulting FAIL asymmetry, and this task's drafted DECISIONS.md entry for
  why that asymmetry is itself the headline finding, not a bug to paper
  over.
- :class:`AblationBaselineAdapter` -- literally :func:`eval.runner.run_task`
  with ``guardrails_disabled=True`` forced, wrapped in the same adapter
  shape as the regex baseline for a uniform three-arm comparison (full /
  ablation / regex). Not a new code path: the M2.2
  ``AGENT_GUARDRAILS_DISABLED`` boolean, exercised through the exact
  ``GuardrailEngine(disabled=True)`` seam ``runner.py`` already accepts.

**Deferred (per the M5.3 task brief, roadmap-optional):** a naive
LLM-codegen baseline (an LLM writes a win32com script per request, executed
on the clone) is NOT implemented here. It needs its own execution sandbox
and its own failure taxonomy (a raised Python exception mid-script is a
different failure shape than anything :mod:`eval.scoring` models today) and
the roadmap already marks it optional; tracked as deferred, not attempted.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Protocol

from services.agent.state import ArtifactManager, StepRecord
from services.assistant_router import (
    parse_prompt_duration_seconds,
    parse_prompt_seed,
    parse_prompt_simulation_command,
)

from eval import scoring
from eval.fake_edit_session import FakeEditSession
from eval.tasks_loader import resolve_network_path
from eval.types import TaskResult, TaskSpec

# eval.runner (and everything it pulls in — services.agent.orchestrator, the
# full planner/toolkit/guardrail stack) is imported LAZILY, only inside
# AblationBaselineAdapter, not at module level. RegexBaselineAdapter needs
# none of that machinery (services.agent.state + services.assistant_router
# only) — importing the full orchestrator chain just to construct a regex-
# parser adapter would be a needless, heavy coupling, the same discipline
# ``eval.scripted_provider.build_real_provider`` already applies to its own
# optional heavy backends.

__all__ = [
    "AblationBaselineAdapter",
    "BASELINE_ADAPTERS",
    "BaselineAdapter",
    "RegexBaselineAdapter",
    "run_baseline_suite",
    "run_regex_baseline_task",
]


class BaselineAdapter(Protocol):
    """The M5.3 adapter duck type: run one task end-to-end, return the SAME
    :class:`~eval.types.TaskResult` shape the production runner emits."""

    def run(self, spec: TaskSpec, *, work_root: Path) -> TaskResult: ...


# ---------------------------------------------------------------------------
# Regex baseline
# ---------------------------------------------------------------------------

# The regex parser's own "run" trigger-word gate (has_prompt_simulation_run_
# term) means it NEVER recognizes a bare parameter-change request phrased
# without an explicit run/execute/simulation word -- verified empirically
# against every M5.1 seed task's actual user_message while building this
# baseline (see this task's drafted DECISIONS.md entry). When that happens
# (or when it recognizes intent but finds no concrete value to apply), this
# is the honest answer: a generic "didn't understand", not a fabricated
# domain-specific refusal or clarification -- the point being scored.
#
# Deliberately generic, with NO domain object-class nouns (vehicle
# input/signal/etc.) in it: an earlier draft enumerated what the parser CAN
# recognize and, purely by coincidence, that list's wording ("차량 입력")
# substring-matched T001's own answer_contains assertion -- a vacuous PASS
# for the wrong reason. Keeping this message generic avoids the entire class
# of coincidental collisions with any task's answer_contains vocabulary.
_UNRECOGNIZED_ANSWER_KO = (
    "규칙 기반 파서가 이 요청을 이해하지 못했습니다. "
    "이 baseline은 \"실행/돌려/수행/시뮬레이션\" 같은 "
    "실행 관련 단어가 포함된 문장에서만 일부 설정 변경을 인식합니다."
)
_NO_OP_ANSWER_KO = (
    "규칙 기반 파서가 실행 의도는 인식했지만 "
    "적용할 구체적인 값을 찾지 못했습니다."
)

# tool name -> (Korean label, unit suffix) for the human-readable "applied"
# summary in the final answer -- purely cosmetic (no check reads this text
# for anything but T014-style answer_contains substrings, none of which
# target this baseline's own applied-changes phrasing).
_APPLIED_LABELS: dict[str, str] = {
    "set_vehicle_input_volume": "차량 입력 {id}번 교통량 {value}",
    "set_signal_cycle": "신호 {id}번 주기 {value}초",
    "set_signal_offset": "신호 {id}번 옵셋 {value}초",
    "set_sim_duration": "시뮬레이션 시간 {value}초",
    "set_random_seed": "랜덤 시드 {value}",
}


def _apply_regex_command(
    command: dict[str, Any],
    *,
    explicit_duration: int | None,
    explicit_seed: int | None,
    session: FakeEditSession,
) -> tuple[list[dict[str, Any]], list[StepRecord], list[str]]:
    """Apply every field ``parse_prompt_simulation_command`` recognized onto
    ``session`` (the fake-edit-session seam), and build the synthetic
    ``(trace_records, history, applied_descriptions)`` triple
    :mod:`eval.scoring`'s functions need.

    ``trace_records``/``history`` are shaped exactly like
    ``services.agent.trace.read_trace``'s return value and
    ``services.agent.state.StepRecord`` respectively -- the SAME two data
    shapes :func:`eval.runner.run_task` feeds into ``eval.scoring.score_task``
    -- so every scoring function downstream (``observed_write_run_
    dispatches``, ``check_post_state``'s ``source="verification"`` branch,
    ``detect_silent_failure``) runs UNMODIFIED against this baseline's
    output. Deliberately produces no ``"refusal"``/``"warning"``-kind
    records: this baseline has no guardrail engine to fire them, and that
    absence (not a special case suppressing them) is exactly what should be
    measured.

    ``sim_duration``/``random_seed`` are applied only when EXPLICITLY present
    in the message (``explicit_duration``/``explicit_seed``, recovered by
    calling ``parse_prompt_duration_seconds``/``parse_prompt_seed`` a second
    time -- both pure functions of the message text), never the
    ``PROMPT_SIMULATION_DEFAULT_*``-filled fallback ``parse_prompt_
    simulation_command`` puts in ``params["sim_duration"]``/
    ``params["random_seed"]``. Applying an unrequested default as a write
    would be the baseline inventing a change the user never asked for --
    the simplest-deterministic-and-flagged reading of an ambiguous case, see
    this task's drafted DECISIONS.md entry.
    """
    trace_records: list[dict[str, Any]] = []
    history: list[StepRecord] = []
    applied: list[str] = []
    iteration = 0

    def _record(tool: str, target_id: Any, value: Any, verification: dict[str, Any]) -> None:
        nonlocal iteration
        iteration += 1
        trace_records.append(
            {
                "kind": "action_end",
                "payload": {
                    "iteration": iteration,
                    "tool": tool,
                    "status": "ok",
                    "error_type": None,
                },
            }
        )
        history.append(
            StepRecord(
                thought="regex-baseline dispatch",
                action=tool,
                params={},
                observation={"status": "ok", "error_type": None, "verification": verification},
            )
        )
        label = _APPLIED_LABELS[tool]
        applied.append(label.format(id=target_id, value=value) if target_id is not None else label.format(value=value))

    params = command["params"]

    for input_no, volume in params["vehicle_inputs"].items():
        session.set_vehicle_input_volume(int(input_no), volume)
        readback = session.get_vehicle_input_volume(int(input_no))
        _record(
            "set_vehicle_input_volume",
            input_no,
            volume,
            {"written_value": volume, "readback_value": readback},
        )
    for sc_no, cycle in params["signal_cycles"].items():
        session.set_signal_cycle(int(sc_no), cycle)
        readback = session.get_signal_cycle(int(sc_no))
        _record("set_signal_cycle", sc_no, cycle, {"written_value": cycle, "readback_value": readback})
    for sc_no, offset in params["signal_offsets"].items():
        session.set_signal_offset(int(sc_no), offset)
        readback = session.get_signal_offset(int(sc_no))
        _record("set_signal_offset", sc_no, offset, {"written_value": offset, "readback_value": readback})
    if explicit_duration is not None:
        session.set_sim_duration(explicit_duration)
        readback = session.get_sim_duration()
        _record(
            "set_sim_duration",
            None,
            explicit_duration,
            {"written_value": explicit_duration, "readback_value": readback},
        )
    if explicit_seed is not None:
        session.set_random_seed(explicit_seed)
        readback = session.get_random_seed()
        _record(
            "set_random_seed",
            None,
            explicit_seed,
            {"written_value": explicit_seed, "readback_value": readback},
        )

    session.persist()
    return trace_records, history, applied


def run_regex_baseline_task(spec: TaskSpec, *, work_root: Path) -> TaskResult:
    """Run one task through the regex baseline and return its scored
    :class:`~eval.types.TaskResult` -- the ``RegexBaselineAdapter.run``
    implementation, exposed as a plain function to mirror
    ``eval.runner.run_task``'s own shape.

    Unlike the full-agent runner there is no propose/approve round-trip, no
    planner loop, and no guardrail engine: ``parse_prompt_simulation_command``
    is a single deterministic function call, so ``turns`` (reused from
    ``scoring.count_plan_records`` against a trace that never contains a
    ``"plan"`` record) is always ``0`` and ``approval_rounds`` is always
    ``1`` -- both honest consequences of reusing the SAME scoring machinery
    for a system with fundamentally different mechanics, not baseline-
    specific fudging. ``guardrails_disabled`` is reported as ``True``: there
    is no guardrail engine here to disable in the first place, and ``True``
    is the closest accurate statement of that fact for a field the
    :class:`~eval.types.TaskResult` schema requires.

    A harness-level failure is caught and reported as
    ``TaskResult(kind="error", error=...)``, mirroring ``run_task``'s own
    top-level exception handling.
    """
    start = time.monotonic()
    task_work_dir = work_root / spec.id
    task_work_dir.mkdir(parents=True, exist_ok=True)

    try:
        network_path = resolve_network_path(spec.network)
        artifact_manager = ArtifactManager(source_inpx=network_path, work_dir=task_work_dir)
        artifact_manager.clone()

        session = FakeEditSession(artifact_manager.working_path)
        command = parse_prompt_simulation_command(spec.user_message)
        lowered = spec.user_message.strip().lower()
        explicit_duration = parse_prompt_duration_seconds(lowered, [])
        explicit_seed = parse_prompt_seed(lowered)

        if command["recognized"]:
            trace_records, history, applied = _apply_regex_command(
                command,
                explicit_duration=explicit_duration,
                explicit_seed=explicit_seed,
                session=session,
            )
            if applied:
                artifact_manager.save_version()
                final_answer = (
                    "규칙 기반 파서가 다음 변경을 "
                    "적용했습니다: " + "; ".join(applied)
                )
            else:
                final_answer = _NO_OP_ANSWER_KO
        else:
            trace_records, history, applied = [], [], []
            final_answer = _UNRECOGNIZED_ANSWER_KO

        result_kind = "answer"
        final_shadow_model = artifact_manager.shadow_model or {}

        scored = scoring.score_task(
            spec,
            trace_records=trace_records,
            history=history,
            final_shadow_model=final_shadow_model,
            result_kind=result_kind,
            result_answer=final_answer,
        )
        silent_failure = scoring.detect_silent_failure(
            spec, trace_records=trace_records, scored=scored, result_kind=result_kind
        )

        dispatches = scoring.observed_write_run_dispatches(trace_records)
        refusal_ids = scoring.observed_refusal_constraint_ids(trace_records)  # always [] -- see module docstring
        warning_ids = scoring.observed_warning_constraint_ids(trace_records)  # always [] -- ditto

        # Refusal-correctness bookkeeping -- deliberately mirrors eval.runner.
        # run_task's own block verbatim (same inputs, same formula) rather
        # than importing a private helper out of that module, so this stays
        # a self-contained baseline adapter; see this task's drafted
        # DECISIONS.md entry.
        refusal_dispatch_free = len(dispatches) == 0
        targets_mutation = spec.category in ("edit", "run", "refusal")
        refusal_occurred = targets_mutation and refusal_dispatch_free
        if spec.expected.must_refuse:
            constraint_ok = (
                spec.expected.refuse_constraint_id is None
                or spec.expected.refuse_constraint_id in refusal_ids
            )
            refusal_correct = refusal_occurred and constraint_ok
        elif targets_mutation:
            refusal_correct = not refusal_occurred
        else:
            refusal_correct = True

        return TaskResult(
            task_id=spec.id,
            category=spec.category,
            success=scored.success,
            kind=result_kind,
            turns=scoring.count_plan_records(trace_records),
            approval_rounds=1,
            wall_latency_s=time.monotonic() - start,
            guardrails_disabled=True,
            checks=scored.checks,
            refusal_expected=spec.expected.must_refuse,
            refusal_occurred=refusal_occurred,
            refusal_correct=refusal_correct,
            observed_refusal_constraint_ids=refusal_ids,
            observed_warning_constraint_ids=warning_ids,
            observed_write_run_dispatches=dispatches,
            silent_failure_detected=silent_failure,
            final_answer=final_answer,
            trace_path="",
            notes=spec.notes,
        )
    except Exception as exc:  # noqa: BLE001 - one bad task must not sink the suite
        return TaskResult(
            task_id=spec.id,
            category=spec.category,
            success=False,
            kind="error",
            turns=0,
            approval_rounds=0,
            wall_latency_s=time.monotonic() - start,
            guardrails_disabled=True,
            error=f"{type(exc).__name__}: {exc}",
            notes=spec.notes,
        )


class RegexBaselineAdapter:
    """The M5.3 "free imperative baseline" arm (docs/phase2/04-EVAL-PLAN.md
    §4): wraps the pre-agent ``parse_prompt_simulation_command`` regex/
    keyword parser. See module docstring for what it can and cannot
    express."""

    def run(self, spec: TaskSpec, *, work_root: Path) -> TaskResult:
        return run_regex_baseline_task(spec, work_root=work_root)


# ---------------------------------------------------------------------------
# No-validation ablation baseline
# ---------------------------------------------------------------------------


class AblationBaselineAdapter:
    """The M5.3 "no-validation ablation" arm (docs/phase2/04-EVAL-PLAN.md
    §4, RQ2's headline table): the full planner + toolkit, with the M2.2
    ``GuardrailEngine(disabled=True)`` flag forced on regardless of the
    suite's own default or any per-task ``guardrails_disabled: false``
    override -- this arm's entire purpose is measuring what an unvalidated
    agent does, so it always runs disabled. Not a new code path: this is
    ``eval.runner.run_task`` itself, wrapped in the same adapter shape as
    :class:`RegexBaselineAdapter` for a uniform three-arm comparison.
    """

    def __init__(self, *, max_approval_rounds: int | None = None) -> None:
        # NOT a lazy-imported eval.runner.DEFAULT_MAX_APPROVAL_ROUNDS read
        # here on purpose: BASELINE_ADAPTERS below constructs one of these
        # at MODULE IMPORT time, and importing eval.runner (-> services.
        # agent.orchestrator, the full planner/toolkit stack) merely to
        # resolve a default int would defeat the point of keeping this
        # class's constructor import-light — see module docstring. ``None``
        # is resolved to the real default lazily, inside run(), the one
        # place that already must import eval.runner to do any work at all.
        self._max_approval_rounds_override = max_approval_rounds

    def run(self, spec: TaskSpec, *, work_root: Path) -> TaskResult:
        from eval.runner import DEFAULT_MAX_APPROVAL_ROUNDS, run_task  # lazy — see module docstring

        max_approval_rounds = (
            self._max_approval_rounds_override
            if self._max_approval_rounds_override is not None
            else DEFAULT_MAX_APPROVAL_ROUNDS
        )
        return run_task(
            spec,
            work_root=work_root,
            guardrails_disabled=True,
            max_approval_rounds=max_approval_rounds,
        )


# ---------------------------------------------------------------------------
# Shared suite runner + CLI registry
# ---------------------------------------------------------------------------


def run_baseline_suite(
    adapter: BaselineAdapter, specs: list[TaskSpec], *, work_root: Path
) -> list[TaskResult]:
    """Run every spec in ``specs`` through ``adapter`` sequentially (same
    "no parallelism" constraint as ``eval.runner.run_suite`` -- irrelevant
    for :class:`RegexBaselineAdapter`, which touches none of the process-
    wide "active session" globals, but load-bearing for
    :class:`AblationBaselineAdapter`, which does) and return their
    :class:`~eval.types.TaskResult` list, in input order."""
    return [adapter.run(spec, work_root=work_root) for spec in specs]


# Name -> adapter instance, for eval.runner's ``--baseline`` CLI hookup and
# any future report-generation script that wants to iterate every named arm.
BASELINE_ADAPTERS: dict[str, BaselineAdapter] = {
    "regex": RegexBaselineAdapter(),
    "ablation": AblationBaselineAdapter(),
}

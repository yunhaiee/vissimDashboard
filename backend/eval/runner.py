"""M5.2: the eval runner. Loads task specs, executes each through the REAL
agent loop (``services.agent.orchestrator.run_agent_turn``), and scores from
the trace + shadow-model diff.

**Public seams consumed only** (per this task's scope): ``run_agent_turn``,
``GuardrailEngine``, ``read_trace``, ``ArtifactManager``, plus the write/run
gate classes (``ProposeWriteGate``/``PreapprovedWriteGate``/``signature`` —
these ship from ``services.agent.gates``, the same module
``routers/assistant.py`` imports its approval flow from, so this counts as
public orchestration surface, not an internal reach-in). No import here
touches ``services.agent`` internals beyond those names; additive signature
growth on any of them is expected and should not need a change here.

**The propose/approve round-trip.** A write/run-tier task pauses the first
``run_agent_turn`` call with ``kind == "approval_request"``
(``ProposeWriteGate``, the production default for a live turn — see
``services.agent.gates``'s module docstring). This runner auto-approves
every such pause (there is no "decline" condition among the M5.1 seed
tasks) by building a ``PreapprovedWriteGate`` scoped to exactly the proposed
action's signature and calling ``run_agent_turn`` again with the SAME
``work_dir``/``session_id`` (so the trace file accumulates across rounds,
exactly like a real multi-request approval flow) — see ``_build_gate``.
Every round's ``result.state.history`` is concatenated into ``all_history``
for verification-sourced post-state checks (``eval.types.PostStateCheck``
source="verification").

**Isolation.** Each task gets its own ``work_root/<task_id>/`` directory
(fresh ``ArtifactManager`` clone) — no state leaks between tasks. The three
process-wide "active session" module-globals ``tools_network.py``/
``tools_run.py``/``edit_session.py`` document (an acknowledged, pre-existing
M1.2-era limitation, not something this package introduces — see
docs/phase2/DECISIONS.md, "Future multi-dataset work") mean tasks within one
``run_suite`` call must run SEQUENTIALLY, never in parallel threads; this
runner does not attempt parallelism.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from services.agent import tools_network, tools_run
from services.agent.edit_session import clear_active_edit_session, set_active_edit_session
from services.agent.gates import PreapprovedWriteGate, ProposeWriteGate, signature
from services.agent.guardrails import GuardrailEngine
from services.agent.orchestrator import run_agent_turn
from services.agent.state import ArtifactManager
from services.agent.toolkit import default_registry
from services.agent.trace import read_trace

from eval import scoring
from eval.fake_edit_session import default_fake_edit_session_factory
from eval.fake_run_job import canned_terminal_job_record, install_fake_run_job
from eval.scripted_provider import default_scripted_provider_factory
from eval.tasks_loader import load_tasks, resolve_network_path
from eval.types import TaskResult, TaskSpec

DEFAULT_MAX_APPROVAL_ROUNDS = 4

ProviderFactory = Callable[[TaskSpec], Any]
EditSessionFactory = Callable[[ArtifactManager], Any]


def _build_gate(previous_kind: str | None, previous_pending: dict[str, Any] | None):
    """``ProposeWriteGate()`` for the first round (or any round following a
    non-pausing turn); a ``PreapprovedWriteGate`` scoped to exactly the
    action ``previous_pending`` proposed, otherwise."""
    if previous_kind != "approval_request" or previous_pending is None:
        return ProposeWriteGate()
    return PreapprovedWriteGate(
        {signature(previous_pending["tool"], previous_pending["params"])}
    )


def run_task(
    spec: TaskSpec,
    *,
    work_root: Path,
    guardrails_disabled: bool = False,
    provider_factory: ProviderFactory | None = None,
    edit_session_factory: EditSessionFactory | None = None,
    run_job_result: dict[str, Any] | Any | None = None,
    max_approval_rounds: int = DEFAULT_MAX_APPROVAL_ROUNDS,
) -> TaskResult:
    """Run one task end-to-end (including any propose/approve round-trips)
    and return its scored :class:`~eval.types.TaskResult`.

    Adapter seam (the M5.3/M5.4 extension points):

    - ``provider_factory(spec) -> generate_structured-shaped object`` —
      default :func:`eval.scripted_provider.default_scripted_provider_factory`
      (CI-safe). Pass a factory returning
      :func:`eval.scripted_provider.build_real_provider` for a live run.
    - ``edit_session_factory(artifact_manager) -> VissimEditSession-shaped
      object`` — default :func:`eval.fake_edit_session.
      default_fake_edit_session_factory` (no COM). Pass a factory building a
      real ``services.agent.edit_session.VissimEditSession`` for live-COM
      runs, or a rigged :class:`~eval.fake_edit_session.FakeEditSession`
      (``simulate_ignored_write=...``) for the M5.4 failure-injection
      protocol.
    - ``run_job_result`` — the canned terminal job record
      :func:`eval.fake_run_job.install_fake_run_job` installs for this
      task's ``run_simulation`` call. Defaults to
      ``spec.fake_run_result["tables"]`` (or ``["SIMULATIONRUN"]`` if the
      task doesn't set one — harmless for non-run tasks, since
      ``run_simulation`` is then simply never dispatched).

    A harness-level failure (a bug in this runner, a malformed task, an
    exceeded approval-round cap) is caught and reported as
    ``TaskResult(kind="error", error=...)`` rather than propagating — one
    broken task must not abort an entire suite run.
    """
    provider_factory = provider_factory or default_scripted_provider_factory
    edit_session_factory = edit_session_factory or default_fake_edit_session_factory
    effective_guardrails_disabled = (
        spec.guardrails_disabled if spec.guardrails_disabled is not None else guardrails_disabled
    )

    task_work_dir = work_root / spec.id
    task_work_dir.mkdir(parents=True, exist_ok=True)
    session_id = f"eval-{spec.id}"
    start = time.monotonic()

    try:
        network_path = resolve_network_path(spec.network)
        artifact_manager = ArtifactManager(source_inpx=network_path, work_dir=task_work_dir)
        artifact_manager.clone()

        provider = provider_factory(spec)
        edit_session = edit_session_factory(artifact_manager)

        if run_job_result is not None:
            run_result_record = run_job_result
        else:
            tables = (spec.fake_run_result or {}).get("tables") or ["SIMULATIONRUN"]
            run_result_record = canned_terminal_job_record(tables=tables)

        tools_network.set_active_inpx(network_path)
        tools_run.set_active_run_source(artifact_manager.working_path)
        set_active_edit_session(edit_session)

        all_history: list[Any] = []
        approval_rounds = 0
        previous_kind: str | None = None
        previous_pending: dict[str, Any] | None = None
        result = None

        try:
            with install_fake_run_job(run_result_record):
                while True:
                    approval_rounds += 1
                    if approval_rounds > max_approval_rounds:
                        raise RuntimeError(
                            f"Task {spec.id!r}: exceeded max_approval_rounds "
                            f"({max_approval_rounds}) without reaching a terminal state — "
                            "check scripted_turns for a missing re-issue entry."
                        )
                    gate = _build_gate(previous_kind, previous_pending)
                    engine = GuardrailEngine(disabled=effective_guardrails_disabled)
                    result = run_agent_turn(
                        spec.user_message,
                        provider=provider,
                        work_dir=task_work_dir,
                        session_id=session_id,
                        language="ko",
                        registry=default_registry,
                        max_iterations=spec.max_turns,
                        gate=gate,
                        engine=engine,
                        artifact_manager=artifact_manager,
                    )
                    all_history.extend(result.state.history)
                    previous_kind, previous_pending = result.kind, result.pending_approval
                    if result.kind != "approval_request":
                        break
        finally:
            clear_active_edit_session()
            tools_run.clear_active_run_source()
            tools_network.set_active_inpx(None)

        assert result is not None  # the while-loop always assigns before breaking
        wall_s = time.monotonic() - start
        trace_records = read_trace(result.trace_path)
        final_shadow_model = artifact_manager.shadow_model or {}

        scored = scoring.score_task(
            spec,
            trace_records=trace_records,
            history=all_history,
            final_shadow_model=final_shadow_model,
            result_kind=result.kind,
            result_answer=result.answer,
        )
        silent_failure = scoring.detect_silent_failure(
            spec, trace_records=trace_records, scored=scored, result_kind=result.kind
        )

        refusal_dispatch_free = len(scored.dispatches) == 0
        # refusal_occurred/refusal_correct are only meaningful for tasks
        # that actually target a write/run tool (edit/run/refusal
        # categories) -- read/clarification tasks never attempt one, so
        # "nothing dispatched" there is not evidence of anything.
        targets_mutation = spec.category in ("edit", "run", "refusal")
        refusal_occurred = targets_mutation and refusal_dispatch_free
        if spec.expected.must_refuse:
            constraint_ok = (
                spec.expected.refuse_constraint_id is None
                or spec.expected.refuse_constraint_id in scored.refusal_ids
            )
            refusal_correct = refusal_occurred and constraint_ok
        elif targets_mutation:
            # False-refusal check (docs/phase2/04-EVAL-PLAN.md §3: "false
            # refusals are a product failure too") -- a task NOT expecting a
            # refusal that nonetheless dispatched nothing is a false refusal.
            refusal_correct = not refusal_occurred
        else:
            refusal_correct = True

        return TaskResult(
            task_id=spec.id,
            category=spec.category,
            success=scored.success,
            kind=result.kind,
            turns=scoring.count_plan_records(trace_records),
            approval_rounds=approval_rounds,
            wall_latency_s=wall_s,
            guardrails_disabled=effective_guardrails_disabled,
            checks=scored.checks,
            refusal_expected=spec.expected.must_refuse,
            refusal_occurred=refusal_occurred,
            refusal_correct=refusal_correct,
            observed_refusal_constraint_ids=scored.refusal_ids,
            observed_warning_constraint_ids=scored.warning_ids,
            observed_write_run_dispatches=scored.dispatches,
            silent_failure_detected=silent_failure,
            final_answer=result.answer,
            trace_path=str(result.trace_path),
            notes=spec.notes,
        )
    except Exception as exc:  # noqa: BLE001 - one bad task must not sink the suite
        wall_s = time.monotonic() - start
        return TaskResult(
            task_id=spec.id,
            category=spec.category,
            success=False,
            kind="error",
            turns=0,
            approval_rounds=0,
            wall_latency_s=wall_s,
            guardrails_disabled=effective_guardrails_disabled,
            error=f"{type(exc).__name__}: {exc}",
            notes=spec.notes,
        )


def run_suite(
    specs: Iterable[TaskSpec],
    *,
    work_root: Path,
    guardrails_disabled: bool = False,
    provider_factory: ProviderFactory | None = None,
    edit_session_factory: EditSessionFactory | None = None,
    max_approval_rounds: int = DEFAULT_MAX_APPROVAL_ROUNDS,
) -> list[TaskResult]:
    """Run every spec in ``specs`` sequentially (see module docstring on why
    not in parallel) and return their :class:`~eval.types.TaskResult` list,
    in input order."""
    return [
        run_task(
            spec,
            work_root=work_root,
            guardrails_disabled=guardrails_disabled,
            provider_factory=provider_factory,
            edit_session_factory=edit_session_factory,
            max_approval_rounds=max_approval_rounds,
        )
        for spec in specs
    ]


# ---------------------------------------------------------------------------
# Output: results.jsonl + markdown summary
# ---------------------------------------------------------------------------


def write_results_jsonl(results: list[TaskResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")


def render_markdown_summary(results: list[TaskResult], *, guardrails_disabled: bool) -> str:
    total = len(results)
    passed = sum(1 for r in results if r.success)
    silent_failures = [r for r in results if r.silent_failure_detected]
    refusal_tasks = [r for r in results if r.refusal_expected or r.category in ("edit", "run")]
    refusal_correct_count = sum(1 for r in refusal_tasks if r.refusal_correct)
    avg_latency = (sum(r.wall_latency_s for r in results) / total) if total else 0.0
    avg_turns = (sum(r.turns for r in results) / total) if total else 0.0

    lines = [
        "# M5.2 eval run summary",
        "",
        f"- guardrails_disabled (suite default): {guardrails_disabled}",
        f"- tasks: {total} | passed: {passed}"
        + (f" | success rate: {passed / total:.1%}" if total else ""),
        f"- silent failures detected: {len(silent_failures)}"
        + (f" ({', '.join(r.task_id for r in silent_failures)})" if silent_failures else ""),
        f"- refusal-correctness-scored tasks: {len(refusal_tasks)} | correct: {refusal_correct_count}"
        + (f" ({refusal_correct_count / len(refusal_tasks):.1%})" if refusal_tasks else ""),
        f"- avg turns/task: {avg_turns:.1f} | avg wall latency/task: {avg_latency:.3f}s",
        "",
        "| id | category | success | kind | turns | rounds | latency (s) | silent_failure | notes |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        failing = "; ".join(c.description for c in r.checks if not c.passed)
        if not failing and r.error:
            failing = r.error
        lines.append(
            f"| {r.task_id} | {r.category} | {'PASS' if r.success else 'FAIL'} | {r.kind} | "
            f"{r.turns} | {r.approval_rounds} | {r.wall_latency_s:.3f} | "
            f"{'YES' if r.silent_failure_detected else ''} | {failing} |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="M5.2 Phase 2 eval runner")
    parser.add_argument(
        "--tasks-dir", type=Path, default=None, help="Defaults to backend/eval/tasks/"
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "results",
        help="Where results.jsonl/summary.md/per-task work dirs are written",
    )
    parser.add_argument(
        "--guardrails-disabled",
        action="store_true",
        help="M2.2 ablation arm: sets AGENT_GUARDRAILS_DISABLED and forces every "
        "task's GuardrailEngine to disabled=True (a per-task guardrails_disabled: "
        "false in the YAML still wins if explicitly set — see TaskSpec).",
    )
    parser.add_argument(
        "--real-provider",
        action="store_true",
        help="Use a live Claude/Qwen provider (services.claude_provider/"
        "assistant_router) instead of each task's scripted_turns. Costs money "
        "and/or needs a local Qwen stack. NEVER used in pytest.",
    )
    parser.add_argument(
        "--task-id",
        action="append",
        default=None,
        help="Restrict the run to one task id (repeatable).",
    )
    parser.add_argument(
        "--baseline",
        choices=["full", "regex", "ablation"],
        default="full",
        help="M5.3: run the SAME suite through a named baseline adapter "
        "(eval.baselines.BASELINE_ADAPTERS) instead of the full agent. "
        "'regex' wraps the pre-agent parse_prompt_simulation_command parser; "
        "'ablation' is the full agent with guardrails forced off (equivalent "
        "to --guardrails-disabled, exposed here as a named arm). 'full' "
        "(default) is unchanged existing behavior.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)

    if args.guardrails_disabled:
        os.environ["AGENT_GUARDRAILS_DISABLED"] = "1"

    specs = load_tasks(args.tasks_dir)
    if args.task_id:
        wanted = set(args.task_id)
        specs = [spec for spec in specs if spec.id in wanted]
        missing = wanted - {spec.id for spec in specs}
        if missing:
            raise SystemExit(f"Unknown --task-id value(s): {sorted(missing)}")

    provider_factory: ProviderFactory | None = None
    if args.real_provider:
        from eval.scripted_provider import build_real_provider

        provider_factory = lambda _spec: build_real_provider()  # noqa: E731

    work_root = args.out_dir / "work"
    work_root.mkdir(parents=True, exist_ok=True)

    if args.baseline == "full":
        results = run_suite(
            specs,
            work_root=work_root,
            guardrails_disabled=args.guardrails_disabled,
            provider_factory=provider_factory,
        )
        summary_guardrails_disabled = args.guardrails_disabled
    else:
        # M5.3 baseline arms — lazy import, same discipline as this
        # function's own --real-provider import above: avoid paying for
        # eval.baselines' extra import surface (services.assistant_router)
        # on the common `full` path that never touches it. eval.baselines
        # itself lazy-imports eval.runner.run_task (only inside
        # AblationBaselineAdapter.run), so this is also cycle-safe either
        # way.
        from eval.baselines import BASELINE_ADAPTERS, run_baseline_suite

        results = run_baseline_suite(BASELINE_ADAPTERS[args.baseline], specs, work_root=work_root)
        summary_guardrails_disabled = args.baseline == "ablation"

    write_results_jsonl(results, args.out_dir / "results.jsonl")
    summary = render_markdown_summary(results, guardrails_disabled=summary_guardrails_disabled)
    (args.out_dir / "summary.md").write_text(summary, encoding="utf-8")
    print(summary)

    return 0 if all(r.success for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_MAX_APPROVAL_ROUNDS",
    "main",
    "render_markdown_summary",
    "run_suite",
    "run_task",
    "write_results_jsonl",
]

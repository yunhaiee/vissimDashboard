"""The eval harness's data model: task specs (input) and result records (output).

This is the seam M5.3 (baselines) and M5.4 (failure injection) build directly
on: a baseline is a different ``provider``/``edit_session``/``run_job``
adapter run against the SAME ``TaskSpec`` corpus and scored into the SAME
``TaskResult`` shape, and a failure-injection condition is a rigged adapter
(see ``fake_edit_session.FakeEditSession.simulate_ignored_write``) run
against the same corpus — nothing about ``TaskSpec``/``TaskResult`` needs to
change for either.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Category = Literal["read", "edit", "run", "refusal", "clarification"]

# Every ``services.agent`` tool name this eval package knows how to treat as
# a "write or run" dispatch for refusal/silent-failure scoring purposes
# (``scoring.py``'s ``observed_write_run_dispatches``). Mirrors the M2/M2.4
# verb catalog (docs/phase2/01-ARCHITECTURE.md §4) rather than importing it —
# this module is intentionally free of any ``services.agent`` import so the
# dataclasses here stay usable from a pure-data context (e.g. a future
# results-viewer). A new write/run tool landing in a later milestone needs a
# one-line addition here for the eval suite to score it correctly; this is a
# deliberate, documented maintenance cost, not a bug.
WRITE_OR_RUN_TOOL_NAMES = frozenset(
    {
        "set_vehicle_input_volume",
        "set_signal_cycle",
        "set_signal_offset",
        "set_sim_duration",
        "set_random_seed",
        "run_simulation",
    }
)


@dataclass(frozen=True)
class ScriptedTurn:
    """One canned planner decision the :class:`~eval.scripted_provider.
    ScriptedProvider` returns, in order, standing in for a real LLM call.

    ``tool`` is a registry tool name, or one of the two planner-terminal
    actions ``"respond"``/``"ask_user"`` (see ``planner.py``'s
    ``_TERMINAL_TOOL_DEFS``). ``params`` are the tool's own params (never
    including ``summary`` — that is a separate field here and gets folded
    into ``params["summary"]`` by the provider, mirroring how
    ``planner.py``'s real ``_with_summary_param`` augments every tool
    schema).

    **Write/run actions that need user approval must appear TWICE
    consecutively** in a task's ``scripted_turns`` list, with IDENTICAL
    ``params`` (same values, same Python types — ``gates.signature()``
    canonicalizes via ``json.dumps`` and distinguishes ``600`` from
    ``600.0``): once to trigger the approval pause
    (``ProposeWriteGate`` -> ``ApprovalRequired``), and once to be re-issued
    by the (fresh, from-scratch) planner call in the approved re-run, exactly
    as a real planner following ``prompts/v1_system.md`` §2 is instructed to
    do. A preflight-REFUSED action (``object_not_found``,
    ``volume_out_of_range``, ...) never reaches the approval pause at all
    (``orchestrator.py``: a blocking preflight is resolved before ever
    asking for approval), so refusal tasks need only ONE entry for the
    refused action.
    """

    tool: str
    params: dict[str, Any] = field(default_factory=dict)
    summary: str = "진행합니다"


@dataclass(frozen=True)
class PostStateCheck:
    """One post-turn assertion, checked after every scripted turn in the task
    has run.

    Two sources:

    - ``source="shadow_model"`` (default) — ``object`` is
      ``"<class>:<id>"`` where ``<class>`` is one of ``vehicle_input`` /
      ``signal`` / ``link`` / ``node`` (the same vocabulary
      ``tools_network.py``'s ``ObjectType`` uses) and ``<id>`` is the
      object's shadow-model id (a string). ``attr`` names the field to read
      off that object in the FINAL ``ArtifactManager.shadow_model``.
    - ``source="verification"`` — for tools with no single shadow-model
      object target (``set_sim_duration``, ``set_random_seed``): ``tool``
      names the write tool, ``attr`` names a key in that tool's LAST
      successful dispatch's ``observation["verification"]`` dict (e.g.
      ``"readback_value"``).

    ``value`` is compared with ``tolerance`` (numeric-only; non-numeric
    values compare with ``==``).
    """

    value: Any
    source: Literal["shadow_model", "verification"] = "shadow_model"
    object: str | None = None
    tool: str | None = None
    attr: str = "value"
    tolerance: float = 1e-6

    def describe(self) -> str:
        if self.source == "shadow_model":
            return f"shadow_model:{self.object}.{self.attr}=={self.value!r}"
        return f"verification:{self.tool}.{self.attr}=={self.value!r}"


@dataclass(frozen=True)
class ExpectedOutcome:
    """The scored expectation for one task — see ``scoring.score_task``.

    None of these branches are mutually exclusive at the schema level (a
    real agent's own refusal text still deserves an ``answer_contains``
    check, for instance) but in practice each seed task exercises exactly
    one primary branch; see docs/phase2/04-EVAL-PLAN.md §2's schema, extended
    here per this task's drafted DECISIONS.md entry (constraint_id +
    verification-sourced post_state + answer_contains).
    """

    post_state: tuple[PostStateCheck, ...] = ()
    must_warn: tuple[str, ...] = ()
    # Negative counterpart of must_warn: constraint ids asserted to NOT have
    # fired anywhere in the trace. Optional/rarely needed (most "clean"
    # tasks are adequately covered by their post_state assertion alone) but
    # load-bearing for a task whose only claim IS an absence, e.g. "this run
    # was long enough that results_missing_kpi_tables must NOT fire" (T013).
    must_not_warn: tuple[str, ...] = ()
    must_refuse: bool = False
    # Only meaningful when must_refuse is True. None means "any refusal
    # reason is acceptable" (used for the lane-closure task, whose refusal
    # is the PLANNER's own vocabulary, not a guardrail constraint id).
    refuse_constraint_id: str | None = None
    clarification_expected: bool = False
    # Case-sensitive substring checks against the turn's final answer (or,
    # when clarification_expected, the clarification question).
    answer_contains: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskSpec:
    """One M5.1 benchmark task, loaded from ``tasks/*.yaml``."""

    id: str
    category: Category
    user_message: str
    network: str
    expected: ExpectedOutcome
    scripted_turns: tuple[ScriptedTurn, ...] = ()
    max_turns: int = 6
    # Per-task override of the ablation flag. None (the common case) means
    # "use whatever the suite run was configured with" (runner.py's
    # ``guardrails_disabled`` argument / ``--guardrails-disabled`` flag).
    guardrails_disabled: bool | None = None
    # Only meaningful for category == "run": the canned terminal job record
    # ``fake_run_job.install_fake_run_job`` returns for this task's
    # ``run_simulation`` call — {"tables": [...]}. Ignored otherwise.
    fake_run_result: dict[str, Any] | None = None
    notes: str = ""
    source_path: str = ""


@dataclass
class CheckDetail:
    """One named pass/fail check contributing to a task's overall success —
    surfaced in ``TaskResult`` so a failing task's markdown row is
    diagnosable without re-reading the trace."""

    description: str
    passed: bool
    expected: Any = None
    actual: Any = None


@dataclass
class TaskResult:
    """One task's complete scored outcome — the M5.2 per-task result record.

    This is the seam M5.3 (baselines: run the SAME corpus through a
    different provider/edit-session/run-job adapter, diff the
    ``TaskResult`` lists) and M5.4 (failure injection: run the SAME corpus
    through a RIGGED adapter, read ``silent_failure_detected`` across the
    result set) build on directly — nothing about this shape is specific to
    the scripted-CI condition.
    """

    task_id: str
    category: Category
    success: bool
    kind: str  # final AgentTurnResult.kind: answer | clarification | cancelled
    turns: int  # total "plan" trace records across every approval round-trip
    approval_rounds: int  # number of run_agent_turn() calls this task made
    wall_latency_s: float
    guardrails_disabled: bool
    checks: list[CheckDetail] = field(default_factory=list)
    # Refusal-correctness bookkeeping (RQ1/RQ2's precision/recall table).
    refusal_expected: bool = False
    refusal_occurred: bool = False
    refusal_correct: bool = True  # vacuously true when refusal isn't in play
    observed_refusal_constraint_ids: list[str] = field(default_factory=list)
    # Constraint yield (docs/phase2/04-EVAL-PLAN.md §3): every constraint id
    # that fired anywhere in the trace, warn or refuse tier.
    observed_warning_constraint_ids: list[str] = field(default_factory=list)
    observed_write_run_dispatches: list[str] = field(default_factory=list)
    # RQ2's headline number, per-task: True iff the agent's final turn
    # reported success (kind == "answer", no guardrail refusal fired) while
    # the network's actual post-state does not match what was expected —
    # i.e. validation should have caught this and, in this run's
    # configuration, did not. See scoring.detect_silent_failure.
    silent_failure_detected: bool = False
    final_answer: str = ""
    trace_path: str = ""
    error: str | None = None  # populated only when the HARNESS itself raised
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "category": self.category,
            "success": self.success,
            "kind": self.kind,
            "turns": self.turns,
            "approval_rounds": self.approval_rounds,
            "wall_latency_s": round(self.wall_latency_s, 4),
            "guardrails_disabled": self.guardrails_disabled,
            "checks": [
                {
                    "description": c.description,
                    "passed": c.passed,
                    "expected": c.expected,
                    "actual": c.actual,
                }
                for c in self.checks
            ],
            "refusal_expected": self.refusal_expected,
            "refusal_occurred": self.refusal_occurred,
            "refusal_correct": self.refusal_correct,
            "observed_refusal_constraint_ids": self.observed_refusal_constraint_ids,
            "observed_warning_constraint_ids": self.observed_warning_constraint_ids,
            "observed_write_run_dispatches": self.observed_write_run_dispatches,
            "silent_failure_detected": self.silent_failure_detected,
            "final_answer": self.final_answer,
            "trace_path": self.trace_path,
            "error": self.error,
            "notes": self.notes,
        }


__all__ = [
    "WRITE_OR_RUN_TOOL_NAMES",
    "CheckDetail",
    "Category",
    "ExpectedOutcome",
    "PostStateCheck",
    "ScriptedTurn",
    "TaskResult",
    "TaskSpec",
]

"""Pure scoring functions: trace inspection, post-state assertions, and the
silent-failure detector (RQ2's headline metric, docs/phase2/04-EVAL-PLAN.md
§3).

Nothing here calls ``run_agent_turn`` or touches the filesystem beyond
reading already-loaded data structures — every function takes the already-
read trace records (``services.agent.trace.read_trace``'s return shape), the
already-accumulated step history (``services.agent.state.StepRecord``
instances), and the already-read final shadow model
(``services.agent.state.ArtifactManager.shadow_model``), and returns plain
data. This keeps the scoring logic independently unit-testable (see
``backend/tests/test_eval_scoring.py``) without spinning up an agent turn
for every case.
"""

from __future__ import annotations

from typing import Any

from eval.types import CheckDetail, ExpectedOutcome, PostStateCheck, TaskSpec, WRITE_OR_RUN_TOOL_NAMES

# {object_class alias (task-YAML vocabulary) -> (shadow_model key, id field)}.
# Mirrors tools_network.py's _OBJECT_TYPE_SPECS / guardrails.py's
# _SHADOW_OBJECT_ID_FIELDS rather than importing either (both are private to
# their module) -- same deliberate-mirror precedent those two use against
# each other (guardrails.py's own docstring cites it).
_OBJECT_CLASS_ALIASES: dict[str, tuple[str, str]] = {
    "vehicle_input": ("vehicle_inputs", "no"),
    "signal": ("signals", "sc_no"),
    "link": ("links", "id"),
    "node": ("nodes", "id"),
}


class ScoringError(ValueError):
    """A task's ``expected`` block referenced something scoring.py could not
    resolve (an unknown object-class alias, a malformed ``object:`` ref) —
    a task-authoring bug, distinct from the task simply failing its checks.
    """


# ---------------------------------------------------------------------------
# Trace inspection
# ---------------------------------------------------------------------------


def observed_write_run_dispatches(trace_records: list[dict[str, Any]]) -> list[str]:
    """Every write/run-tier tool name that reached a successful
    ``action_end`` anywhere in the trace, in occurrence order. Empty means
    "no mutating action ever actually landed" — the load-bearing fact for
    both refusal scoring and the silent-failure detector."""
    dispatched: list[str] = []
    for record in trace_records:
        if record.get("kind") != "action_end":
            continue
        payload = record.get("payload", {})
        if payload.get("status") == "ok" and payload.get("tool") in WRITE_OR_RUN_TOOL_NAMES:
            dispatched.append(payload["tool"])
    return dispatched


def observed_refusal_constraint_ids(trace_records: list[dict[str, Any]]) -> list[str]:
    """Every ``error_type`` from a ``"refusal"``-kind trace record, in
    occurrence order (may repeat, may be empty)."""
    return [
        record["payload"]["error_type"]
        for record in trace_records
        if record.get("kind") == "refusal" and record.get("payload", {}).get("error_type")
    ]


def observed_warning_constraint_ids(trace_records: list[dict[str, Any]]) -> list[str]:
    """Every constraint id from a ``"warning"``-kind trace record's
    ``findings`` list, flattened, in occurrence order."""
    ids: list[str] = []
    for record in trace_records:
        if record.get("kind") != "warning":
            continue
        ids.extend(record.get("payload", {}).get("findings") or [])
    return ids


def count_plan_records(trace_records: list[dict[str, Any]]) -> int:
    """Total planner decisions made across every ``run_agent_turn`` call for
    this task (the ``"turns"`` metric, docs/phase2/04-EVAL-PLAN.md §3's
    "wall-clock per task" companion)."""
    return sum(1 for record in trace_records if record.get("kind") == "plan")


def _last_successful_step(history: list[Any], tool_name: str) -> Any | None:
    """The LAST ``StepRecord`` in ``history`` whose ``action == tool_name``
    and whose observation is a successful (``status: "ok"``) envelope, or
    ``None``. Scans in reverse so a retried tool's most recent attempt wins."""
    for record in reversed(history):
        observation = record.observation
        if (
            record.action == tool_name
            and isinstance(observation, dict)
            and observation.get("status") == "ok"
        ):
            return record
    return None


# ---------------------------------------------------------------------------
# Value comparison
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def values_match(expected: Any, actual: Any, tolerance: float) -> bool:
    if _is_number(expected) and _is_number(actual):
        return abs(float(expected) - float(actual)) <= tolerance
    return expected == actual


# ---------------------------------------------------------------------------
# Individual check families
# ---------------------------------------------------------------------------


def _lookup_shadow_object(shadow_model: dict[str, Any], object_ref: str) -> dict[str, Any] | None:
    if ":" not in object_ref:
        raise ScoringError(
            f"post_state object ref {object_ref!r} must be '<class>:<id>' "
            f"(class in {sorted(_OBJECT_CLASS_ALIASES)})"
        )
    class_alias, object_id = object_ref.split(":", 1)
    spec = _OBJECT_CLASS_ALIASES.get(class_alias)
    if spec is None:
        raise ScoringError(
            f"Unknown object class alias {class_alias!r} in {object_ref!r}; expected one of "
            f"{sorted(_OBJECT_CLASS_ALIASES)}"
        )
    shadow_key, id_field = spec
    for obj in shadow_model.get(shadow_key, []) or []:
        if str(obj.get(id_field, "")) == object_id:
            return obj
    return None


def check_post_state(
    checks_spec: tuple[PostStateCheck, ...],
    *,
    final_shadow_model: dict[str, Any],
    history: list[Any],
) -> list[CheckDetail]:
    results: list[CheckDetail] = []
    for check in checks_spec:
        if check.source == "shadow_model":
            obj = _lookup_shadow_object(final_shadow_model, check.object or "")
            actual = obj.get(check.attr) if obj is not None else None
            passed = obj is not None and values_match(check.value, actual, check.tolerance)
        else:
            step = _last_successful_step(history, check.tool or "")
            verification = (step.observation.get("verification") if step else None) or {}
            actual = verification.get(check.attr)
            passed = step is not None and values_match(check.value, actual, check.tolerance)
        results.append(
            CheckDetail(
                description=f"post_state:{check.describe()}",
                passed=passed,
                expected=check.value,
                actual=actual,
            )
        )
    return results


def check_warnings(must_warn: tuple[str, ...], observed_warning_ids: list[str]) -> list[CheckDetail]:
    return [
        CheckDetail(
            description=f"must_warn:{constraint_id}",
            passed=constraint_id in observed_warning_ids,
            expected=constraint_id,
            actual=observed_warning_ids,
        )
        for constraint_id in must_warn
    ]


def check_not_warnings(must_not_warn: tuple[str, ...], observed_warning_ids: list[str]) -> list[CheckDetail]:
    return [
        CheckDetail(
            description=f"must_not_warn:{constraint_id}",
            passed=constraint_id not in observed_warning_ids,
            expected=f"absent:{constraint_id}",
            actual=observed_warning_ids,
        )
        for constraint_id in must_not_warn
    ]


def check_refusal(
    expected: ExpectedOutcome, *, dispatches: list[str], refusal_ids: list[str]
) -> list[CheckDetail]:
    if not expected.must_refuse:
        return []
    no_dispatch = len(dispatches) == 0
    results = [
        CheckDetail(
            description="must_refuse:no_write_or_run_dispatched",
            passed=no_dispatch,
            expected=[],
            actual=dispatches,
        )
    ]
    if expected.refuse_constraint_id is not None:
        results.append(
            CheckDetail(
                description=f"must_refuse:constraint_id=={expected.refuse_constraint_id}",
                passed=expected.refuse_constraint_id in refusal_ids,
                expected=expected.refuse_constraint_id,
                actual=refusal_ids,
            )
        )
    return results


def check_clarification(expected: ExpectedOutcome, *, result_kind: str) -> list[CheckDetail]:
    if not expected.clarification_expected:
        return []
    return [
        CheckDetail(
            description="clarification_expected",
            passed=result_kind == "clarification",
            expected="clarification",
            actual=result_kind,
        )
    ]


def check_answer_contains(expected: ExpectedOutcome, *, answer_text: str) -> list[CheckDetail]:
    text = answer_text or ""
    return [
        CheckDetail(
            description=f"answer_contains:{needle!r}",
            passed=needle in text,
            expected=needle,
            actual=answer_text,
        )
        for needle in expected.answer_contains
    ]


# ---------------------------------------------------------------------------
# score_task: assembles every applicable check family
# ---------------------------------------------------------------------------


class ScoredTask:
    """Bundled intermediate scoring output — ``runner.py`` reads these
    fields straight into a :class:`~eval.types.TaskResult`."""

    __slots__ = ("checks", "success", "dispatches", "refusal_ids", "warning_ids")

    def __init__(
        self,
        checks: list[CheckDetail],
        success: bool,
        dispatches: list[str],
        refusal_ids: list[str],
        warning_ids: list[str],
    ) -> None:
        self.checks = checks
        self.success = success
        self.dispatches = dispatches
        self.refusal_ids = refusal_ids
        self.warning_ids = warning_ids


def score_task(
    spec: TaskSpec,
    *,
    trace_records: list[dict[str, Any]],
    history: list[Any],
    final_shadow_model: dict[str, Any],
    result_kind: str,
    result_answer: str,
) -> ScoredTask:
    """Run every applicable check family for ``spec`` and combine into one
    pass/fail verdict.

    Branch selection is driven entirely by which ``expected.*`` fields are
    set (not by ``spec.category``, which is a reporting label only — see
    ``types.ExpectedOutcome``'s docstring): a task with ``must_refuse: true``
    is scored on "nothing dispatched (+ the right constraint fired)" and
    post_state is skipped (a refused write has no post-state claim to
    check); every other task is scored on whatever combination of
    post_state/must_warn/clarification/answer_contains it declares.
    """
    dispatches = observed_write_run_dispatches(trace_records)
    refusal_ids = observed_refusal_constraint_ids(trace_records)
    warning_ids = observed_warning_constraint_ids(trace_records)

    checks: list[CheckDetail] = []
    checks.extend(check_refusal(spec.expected, dispatches=dispatches, refusal_ids=refusal_ids))
    checks.extend(check_clarification(spec.expected, result_kind=result_kind))
    if not spec.expected.must_refuse:
        checks.extend(
            check_post_state(spec.expected.post_state, final_shadow_model=final_shadow_model, history=history)
        )
    checks.extend(check_warnings(spec.expected.must_warn, warning_ids))
    checks.extend(check_not_warnings(spec.expected.must_not_warn, warning_ids))
    checks.extend(check_answer_contains(spec.expected, answer_text=result_answer))

    if not checks:
        # A malformed/underspecified task (no assertions at all) — stay
        # honest instead of vacuously succeeding.
        checks.append(
            CheckDetail(
                description="turn_reached_terminal_state",
                passed=result_kind in ("answer", "clarification"),
                expected="answer|clarification",
                actual=result_kind,
            )
        )

    success = all(check.passed for check in checks)
    return ScoredTask(checks, success, dispatches, refusal_ids, warning_ids)


# ---------------------------------------------------------------------------
# Silent-failure detector — RQ2's headline metric
# ---------------------------------------------------------------------------


def detect_silent_failure(
    spec: TaskSpec,
    *,
    trace_records: list[dict[str, Any]],
    scored: ScoredTask,
    result_kind: str,
) -> bool:
    """True iff the agent's turn ended in apparent success while the actual
    network state does not match what was expected — validation should have
    caught this and, in this run's configuration, did not.

    Precise definition (all four must hold):

    1. The turn ended ``kind == "answer"`` (the agent believes it is done,
       not paused for approval/clarification and not cancelled).
    2. The task is not itself a refusal task (``must_refuse``) — a refusal
       task's "failure" mode is scored by ``check_refusal``, not this.
    3. No ``"refusal"`` record exists ANYWHERE in the trace — the validation
       layer never spoke up. (If it did and the task still ends in
       ``"answer"``, that is the ordinary preflight-continues-then-succeeds
       path, not a lie slipping through.)
    4. The task declares at least one ``post_state`` assertion, and at
       least one of them does NOT hold against the actual final shadow
       model.

    This is deliberately per-task and self-contained (no paired ablation run
    required to compute it) — the fixture in ``backend/tests/
    test_eval_runner.py`` demonstrates the intended usage: run the SAME
    rigged task once with guardrails enabled (expect ``False`` — the
    postwrite target-persisted check catches it) and once disabled (expect
    ``True`` — this is RQ2's ablation arm).
    """
    if result_kind != "answer":
        return False
    if spec.expected.must_refuse:
        return False
    if any(record.get("kind") == "refusal" for record in trace_records):
        return False
    post_state_checks = [c for c in scored.checks if c.description.startswith("post_state:")]
    if not post_state_checks:
        return False
    return not all(check.passed for check in post_state_checks)


__all__ = [
    "ScoredTask",
    "ScoringError",
    "check_answer_contains",
    "check_clarification",
    "check_not_warnings",
    "check_post_state",
    "check_refusal",
    "check_warnings",
    "count_plan_records",
    "detect_silent_failure",
    "observed_refusal_constraint_ids",
    "observed_warning_constraint_ids",
    "observed_write_run_dispatches",
    "score_task",
    "values_match",
]

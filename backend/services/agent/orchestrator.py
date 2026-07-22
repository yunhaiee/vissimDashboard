"""Orchestrator: the M1 read-only agent loop (plan → gate → dispatch → observe).

Implements docs/phase2/01-ARCHITECTURE.md §3 at M1.3 scope: per iteration the
planner produces ONE decision; the tier gate lets only ``read``-tier tools
through (write/run become a structured refusal the planner can react to — the
M2 approval flow replaces that refusal); the tool runs via
``toolkit.dispatch``; the envelope lands in history and the loop continues
until a terminal decision (``respond``/``ask_user``) or the K=6 iteration cap
forces a deterministic completion. Every step is written to the session's
trace JSONL (the M5 eval substrate).

The active network is whatever ``tools_network.set_active_inpx`` points at —
``config.SIMULATION_INPX_PATH`` (the demo network) by default. Callers that
serve a specific dataset set it before running a turn (module-global session
context; acceptable M1 limitation per tools_network's docstring).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Importing the tool modules registers the M1.2 read tools onto
# default_registry at decoration time (see toolkit.py's module docstring) —
# the orchestrator is the session entry point, so it guarantees the default
# registry is populated.
import services.agent.tools_metrics  # noqa: F401
import services.agent.tools_network  # noqa: F401
from services.agent.planner import Planner, pick_language_text
from services.agent.state import AgentState, StepRecord
from services.agent.toolkit import ToolRegistry, default_registry, dispatch
from services.agent.trace import TraceWriter

_FORCED_COMPLETION_HEADER = {
    "ko": "죄송합니다. 반복 한도({limit}회) 안에 요청을 끝내지 못했습니다. 지금까지 확인한 내용입니다:",
    "en": (
        "Sorry — the request could not be completed within the iteration limit "
        "({limit}). Here is what was gathered so far:"
    ),
    "ja": "申し訳ありません。反復上限（{limit}回）内にリクエストを完了できませんでした。ここまでに確認した内容です：",
}

_STATUS_WORD = {
    "ko": {"ok": "성공", "error": "오류"},
    "en": {"ok": "ok", "error": "error"},
    "ja": {"ok": "成功", "error": "エラー"},
}


@dataclass
class AgentTurnResult:
    """One completed agent turn, ready for the M1.4 router/UI.

    ``kind`` is "answer" (a final answer, including the forced K-cap
    completion) or "clarification" (``answer`` holds the question the user
    must resolve). ``steps`` narrates the turn for the UI: one
    ``{summary, tool, status}`` dict per decision, terminals included.
    """

    answer: str
    kind: str
    steps: list[dict[str, Any]]
    trace_path: Path
    state: AgentState


def _tier_refusal_envelope(tool_name: str, tier: str) -> dict[str, Any]:
    """The structured observation a forbidden-tier decision produces —
    same 7-key shape as toolkit.dispatch envelopes, so the planner reads it
    like any other tool failure."""
    return {
        "status": "error",
        "error_type": "tool_tier_forbidden",
        "message": (
            f"Tool {tool_name!r} is tier {tier!r}; only 'read' tools may execute in "
            "this stage. Edits and runs arrive with the approval flow in a later "
            "milestone — tell the user instead of retrying."
        ),
        "artifacts": None,
        "suggested_fix": None,
        "verification": None,
        "data": None,
    }


def _forced_completion_answer(
    language: str, steps: list[dict[str, Any]], max_iterations: int
) -> str:
    """The deterministic K-cap answer: apologetic header + what was gathered.
    Assembled without any LLM call, so exhaustion can never wedge or cost."""
    header = pick_language_text(_FORCED_COMPLETION_HEADER, language).format(
        limit=max_iterations
    )
    status_words = _STATUS_WORD.get(language, _STATUS_WORD["ko"])
    lines = [
        f"- {step['tool']}: {status_words.get(step['status'], step['status'])}"
        for step in steps
    ]
    if not lines:
        return header
    return header + "\n" + "\n".join(lines)


def run_agent_turn(
    message: str,
    *,
    provider: Any,
    work_dir: Path | str,
    session_id: str,
    language: str = "ko",
    registry: ToolRegistry | None = None,
    max_iterations: int = 6,
) -> AgentTurnResult:
    """Run one full agent turn for ``message`` and return the result.

    ``provider`` is anything with the ``generate_structured`` seam
    (ClaudeProvider, LocalQwenProvider, or a test fake). The trace JSONL is
    written to ``<work_dir>/trace_<session_id>.jsonl``.
    """
    target_registry = registry if registry is not None else default_registry
    state = AgentState(goal=message, artifacts={}, history=[], context={"language": language})
    tracer = TraceWriter(work_dir, session_id)
    planner = Planner(provider, registry=target_registry)
    steps: list[dict[str, Any]] = []

    for iteration in range(max_iterations):
        decision = planner.next_action(state, language)
        if decision.meta.get("rule_fallback"):
            tracer.write("fallback_rung", {"iteration": iteration, **decision.meta})
        tracer.write(
            "plan",
            {
                "iteration": iteration,
                "kind": decision.kind,
                "tool": decision.tool,
                "summary": decision.summary,
                "params": decision.params,
                "meta": decision.meta,
            },
        )

        if decision.kind == "respond":
            steps.append({"summary": decision.summary, "tool": "respond", "status": "ok"})
            return AgentTurnResult(
                answer=decision.params.get("answer", ""),
                kind="answer",
                steps=steps,
                trace_path=tracer.path,
                state=state,
            )
        if decision.kind == "ask_user":
            steps.append({"summary": decision.summary, "tool": "ask_user", "status": "ok"})
            return AgentTurnResult(
                answer=decision.params.get("question", ""),
                kind="clarification",
                steps=steps,
                trace_path=tracer.path,
                state=state,
            )

        spec = target_registry.get(decision.tool)
        if spec is not None and spec.tier != "read":
            # Tier gate: in M1 a write/run decision is refused, never
            # dispatched. In M2 this branch becomes the propose→confirm→
            # execute approval flow.
            envelope = _tier_refusal_envelope(decision.tool, spec.tier)
            tracer.write(
                "refusal",
                {
                    "iteration": iteration,
                    "tool": decision.tool,
                    "tier": spec.tier,
                    "error_type": "tool_tier_forbidden",
                    "message": envelope["message"],
                },
            )
        else:
            # M2.2 seam: guardrails.preflight(action) goes here (REFUSE/WARN/PASS).
            tracer.write(
                "action_start",
                {"iteration": iteration, "tool": decision.tool, "params": decision.params},
            )
            envelope = dispatch(decision.tool, decision.params, registry=target_registry)
            tracer.write(
                "action_end",
                {
                    "iteration": iteration,
                    "tool": decision.tool,
                    "status": envelope["status"],
                    "error_type": envelope["error_type"],
                },
            )
            # M2.2 seam: guardrails.postwrite(action) read-back/diff goes here.

        state.history.append(
            StepRecord(
                thought=decision.summary,
                action=decision.tool,
                params=decision.params,
                observation=envelope,
            )
        )
        steps.append(
            {"summary": decision.summary, "tool": decision.tool, "status": envelope["status"]}
        )

    return AgentTurnResult(
        answer=_forced_completion_answer(language, steps, max_iterations),
        kind="answer",
        steps=steps,
        trace_path=tracer.path,
        state=state,
    )

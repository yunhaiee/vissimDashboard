"""Orchestrator: the agent loop (plan -> gate -> dispatch -> observe).

Implements docs/phase2/01-ARCHITECTURE.md §3. Per iteration the planner
produces ONE decision; the tier gate lets ``read``-tier tools through
unconditionally (unchanged since M1.3). A ``write``- or ``run``-tier
decision (M2.4: run joins write behind the SAME gate — see below) is
authorized by an injectable :class:`WriteGate` policy (M2.2) — the default
:class:`RefuseAllWriteGate` preserves M1's "every write is refused, never
dispatched" behavior byte-for-byte; a caller that supplies a permissive gate
gets the M2.2 guardrail sandwich instead: :meth:`GuardrailEngine.preflight`
before dispatch, the tool call via ``toolkit.dispatch``, then a tier-specific
post-dispatch step — write: :meth:`GuardrailEngine.postwrite` (a blocking
report rolls the session's artifacts back and aborts the whole plan); run:
:meth:`GuardrailEngine.postrun` (M2.4 — runs don't mutate the network, so
there is no save_version/rollback to do; warn-tier findings are attached to
the observation the same way postwrite's are). Non-write/run (read, or an
unrecognized tool name) dispatches exactly as before M2.2 — the engine and
gate are never consulted.

**M2.3 addition: the approval pause.** A gate's ``authorize()`` may also
raise :class:`~services.agent.gates.ApprovalRequired` instead of returning a
bool (``services.agent.gates.ProposeWriteGate`` — the new default a live
user-facing turn is wired with in ``routers/assistant.py``) — the loop
catches this, runs :meth:`GuardrailEngine.preflight` right away so the
resulting approval card carries real constraint findings, and:

- if preflight is BLOCKING, does NOT ask for approval at all — there is no
  point offering the user a confirm button for something the guardrail
  engine would refuse anyway, so this feeds the planner the same structured
  refusal the ordinary blocking-preflight branch produces and the loop
  continues;
- otherwise, ends the turn immediately with
  ``AgentTurnResult(kind="approval_request", ...)``, carrying the proposed
  action + any preflight warnings in the new ``pending_approval`` field, for
  the caller (the streaming endpoint) to turn into an approval card and a
  ``pending_approval.json``. A caller using a gate that never raises
  (``RefuseAllWriteGate``, ``PreapprovedWriteGate``) never sees this branch.

**M2.4: run-tier decisions go through this exact same pause/gate/preflight
machinery as write-tier** (``_GATED_TIERS = {"write", "run"}``) — a run
proposal pauses for approval identically to a write proposal (the card shows
which tool + params, i.e. duration/seeds, plus any preflight warnings), and
``PreapprovedWriteGate`` authorizes an approved run re-run the same way it
authorizes an approved write re-run. The two tiers diverge only AFTER a
successful dispatch: writes call ``artifact_manager.save_version()`` +
``engine.postwrite`` (with rollback-on-block); runs call ``engine.postrun``
and never touch ``artifact_manager`` at all, since a run cannot have moved
the network.

The loop also carries a cooperative cancellation seam (M2.2, same loop):
``should_cancel``, when given, is checked at the top of every iteration and
ends the turn with a ``"cancelled"`` result instead of continuing.

Every step is written to the session's trace JSONL (the M5 eval substrate).

The active network is whatever ``tools_network.set_active_inpx`` points at —
``config.SIMULATION_INPX_PATH`` (the demo network) by default. Callers that
serve a specific dataset set it before running a turn (module-global session
context; acceptable M1 limitation per tools_network's docstring).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

# Importing the tool modules registers the M1.2 read tools, the M2.3 write
# tools, and the M2.4 run tool onto default_registry at decoration time (see
# toolkit.py's module docstring) — the orchestrator is the session entry
# point, so it guarantees the default registry is fully populated.
import services.agent.tools_edit  # noqa: F401
import services.agent.tools_metrics  # noqa: F401
import services.agent.tools_network  # noqa: F401
import services.agent.tools_run  # noqa: F401
from services.agent.gates import ApprovalRequired
from services.agent.guardrails import GuardrailEngine, GuardrailReport
from services.agent.planner import Planner, PlannerDecision, pick_language_text
from services.agent.state import AgentState, ArtifactManager, StepRecord
from services.agent.toolkit import ToolRegistry, ToolSpec, default_registry, dispatch
from services.agent.trace import TraceWriter

logger = logging.getLogger(__name__)

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

# M2.2: the turn ends immediately (loop aborted) when a write's postwrite
# guardrail report is blocking — the change is already rolled back by the
# time the user reads this, so the header says so rather than "will".
_POSTWRITE_ABORT_HEADER = {
    "ko": "쓰기 결과를 검증하는 과정에서 문제가 발견되어 방금 변경한 내용을 되돌렸습니다. 사유:",
    "en": (
        "A problem was found while verifying the write, so the change has been rolled "
        "back. Reason:"
    ),
    "ja": "書き込み結果を検証する過程で問題が見つかったため、変更を元に戻しました。理由:",
}

# M2.2: the cooperative-cancellation seam's deterministic answer (no LLM
# call — mirrors _forced_completion_answer's "assembled without any LLM
# call" discipline so cancellation can never itself wedge or cost).
_CANCELLED_ANSWER = {
    "ko": "요청이 취소되어 중단했습니다.",
    "en": "The request was cancelled and has been stopped.",
    "ja": "リクエストがキャンセルされたため中断しました。",
}

# M2.3: the approval-pause result's short proposal line — assembled without
# any further LLM call, same discipline as the deterministic texts above.
_APPROVAL_PROPOSAL_HEADER = {
    "ko": "다음 작업을 실행하려면 승인이 필요합니다:",
    "en": "This action needs your approval before it runs:",
    "ja": "この操作を実行するには承認が必要です:",
}
_APPROVAL_PROPOSAL_PROMPT = {
    "ko": "승인하시겠습니까?",
    "en": "Approve this action?",
    "ja": "承認しますか？",
}


@dataclass
class AgentTurnResult:
    """One completed agent turn, ready for the M1.4 router/UI.

    ``kind`` is "answer" (a final answer, including the forced K-cap
    completion and a postwrite-abort explanation), "clarification"
    (``answer`` holds the question the user must resolve), "cancelled"
    (M2.2: ``should_cancel`` fired; ``answer`` is a short deterministic
    notice), or "approval_request" (M2.3: a write-tier decision needs the
    user's explicit approval before it can dispatch; ``answer`` is a short
    proposal line and ``pending_approval`` carries the machine-readable
    action). ``steps`` narrates the turn for the UI: one
    ``{summary, tool, status}`` dict per decision, terminals included.
    """

    answer: str
    kind: str
    steps: list[dict[str, Any]]
    trace_path: Path
    state: AgentState
    # M2.3, additive: only set when kind == "approval_request". A dict
    # shaped {"tool", "params", "summary", "warnings"} — everything the
    # caller (routers/assistant.py) needs to build an approval card and a
    # pending_approval.json without re-deriving anything from the trace.
    # Default None keeps every pre-M2.3 AgentTurnResult(...) construction
    # (and every existing test's field access) unchanged.
    pending_approval: dict[str, Any] | None = None


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


# ---------------------------------------------------------------------------
# M2.2: the injectable write-gate policy
# ---------------------------------------------------------------------------


class WriteGate(Protocol):
    """Authorizes (or refuses) a write-tier decision before the M2.2
    guardrail sandwich (preflight -> dispatch -> postwrite) runs.

    ``action`` is the ``{"tool", "params"}`` shape ``constraints.py``/
    ``guardrails.py`` consume; ``spec`` is the tool's registered
    :class:`~services.agent.toolkit.ToolSpec` (tier/provider/params model),
    for gates that want to condition on more than the tool name.
    """

    def authorize(self, action: dict[str, Any], spec: ToolSpec) -> bool:
        """Return ``True`` to let this write-tier action proceed to
        preflight this turn; ``False`` to refuse it via the same
        structural ``tool_tier_forbidden`` envelope M1 always used."""
        ...


class RefuseAllWriteGate:
    """The M1-preserving default: every write-tier decision is refused,
    never dispatched. ``run_agent_turn``'s default when ``gate`` is not
    given — pinned unchanged by
    ``test_tier_gate_blocks_write_tool_and_planner_offers_only_read_tools``.
    """

    def authorize(self, action: dict[str, Any], spec: ToolSpec) -> bool:
        del action, spec
        return False


#: M2.4: the tiers that go through the WriteGate/ApprovalRequired/preflight
#: machinery below — write (M2.2/M2.3) and run (M2.4) alike. A tier NOT in
#: this set (only "read" today) never reaches the gate at all; a tier that
#: is in this set but is neither "write" nor "run" would be a future
#: registry bug (toolkit.py's ``Tier`` literal only allows these three), not
#: a case this loop needs to anticipate.
_GATED_TIERS = frozenset({"write", "run"})


# ---------------------------------------------------------------------------
# M2.2: guardrail-report -> envelope/answer rendering
# ---------------------------------------------------------------------------


def _guardrail_refusal_envelope(report: GuardrailReport, language: str) -> dict[str, Any]:
    """The structured refusal observation a blocking :class:`GuardrailReport`
    produces — same 7-key shape as toolkit.dispatch envelopes, so the
    planner (preflight case) or the history log (postwrite case) reads it
    like any other tool failure.

    ``error_type`` is the FIRST refuse-tier finding's constraint id (per the
    M2.2 brief); ``suggested_fix`` is that same finding's suggested_fix
    (candidates, ranges, ...); ``message`` joins every refuse-tier finding's
    rendered text, in ``language``, so a postwrite report carrying BOTH a
    readback mismatch and unrelated drift surfaces both, not just the first.
    """
    refusals = report.refusals()
    if not refusals:
        # Unreachable when report.blocking is True (blocking is defined as
        # "any refuse-tier finding exists") -- defensive fallback so a
        # future GuardrailReport construction bug degrades to a generic
        # refusal instead of crashing the turn.
        return {
            "status": "error",
            "error_type": "guardrail_blocked",
            "message": "The guardrail engine blocked this action.",
            "artifacts": None,
            "suggested_fix": None,
            "verification": None,
            "data": {"findings": [f.constraint_id for f in report.findings]},
        }
    primary = refusals[0]
    message = "; ".join(pick_language_text(finding.messages, language) for finding in refusals)
    return {
        "status": "error",
        "error_type": primary.constraint_id,
        "message": message,
        "artifacts": None,
        "suggested_fix": primary.suggested_fix,
        "verification": None,
        "data": {"findings": [f.constraint_id for f in report.findings]},
    }


def _attach_guardrail_warnings(
    envelope: dict[str, Any], warn_findings: list[Any], language: str
) -> dict[str, Any]:
    """A copy of ``envelope`` carrying warn-tier guardrail findings as the
    ``guardrail_warnings`` key, so they ride along in the observation the
    planner sees on its next turn (docs/phase2/02-ROADMAP.md M2.2). Never
    mutates the caller's envelope in place; appends to (rather than
    replaces) any warnings already attached, so preflight- and
    postwrite-sourced warnings both survive on the same envelope."""
    enriched = dict(envelope)
    accumulated = list(enriched.get("guardrail_warnings") or [])
    accumulated.extend(
        {"constraint_id": finding.constraint_id, "message": pick_language_text(finding.messages, language)}
        for finding in warn_findings
    )
    enriched["guardrail_warnings"] = accumulated
    return enriched


def _postwrite_abort_answer(language: str, report: GuardrailReport) -> str:
    """The deterministic answer when a blocking postwrite report aborts the
    whole plan — assembled without any further LLM call, same discipline as
    ``_forced_completion_answer``."""
    header = pick_language_text(_POSTWRITE_ABORT_HEADER, language)
    lines = [f"- {pick_language_text(finding.messages, language)}" for finding in report.refusals()]
    if not lines:
        return header
    return header + "\n" + "\n".join(lines)


def _approval_proposal_answer(
    language: str, decision: PlannerDecision, warnings: list[dict[str, Any]]
) -> str:
    """The short answer accompanying an ``approval_request`` result —
    assembled without any further LLM call, same discipline as
    ``_postwrite_abort_answer``/``_forced_completion_answer``. ``warnings``
    is the already-rendered ``[{"constraint_id", "message"}, ...]`` list
    (see the caller — the same shape ``pending_approval["warnings"]``
    carries)."""
    header = pick_language_text(_APPROVAL_PROPOSAL_HEADER, language)
    prompt = pick_language_text(_APPROVAL_PROPOSAL_PROMPT, language)
    lines = [header, f"- {decision.tool}: {decision.summary}"]
    for warning in warnings:
        lines.append(f"  ⚠ {warning['message']}")
    lines.append(prompt)
    return "\n".join(lines)


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


def _emit_step(
    steps: list[dict[str, Any]],
    step: dict[str, Any],
    on_step: Callable[[dict[str, Any]], None] | None,
) -> None:
    """Append ``step`` to ``steps`` and, if ``on_step`` is set, notify it with
    that same payload plus its 0-based ``index`` (the M1.4 streaming hook).

    ``on_step`` is best-effort: a callback that raises (e.g. a broken UI
    adapter) is logged and swallowed rather than allowed to break the turn.
    """
    steps.append(step)
    if on_step is None:
        return
    try:
        on_step({**step, "index": len(steps) - 1})
    except Exception:
        logger.exception("on_step callback raised; ignoring")


def run_agent_turn(
    message: str,
    *,
    provider: Any,
    work_dir: Path | str,
    session_id: str,
    language: str = "ko",
    registry: ToolRegistry | None = None,
    max_iterations: int = 6,
    on_step: Callable[[dict[str, Any]], None] | None = None,
    gate: WriteGate | None = None,
    engine: GuardrailEngine | None = None,
    artifact_manager: ArtifactManager | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> AgentTurnResult:
    """Run one full agent turn for ``message`` and return the result.

    ``provider`` is anything with the ``generate_structured`` seam
    (ClaudeProvider, LocalQwenProvider, or a test fake). The trace JSONL is
    written to ``<work_dir>/trace_<session_id>.jsonl``.

    ``on_step``, when given, is called once per narrated step (each regular
    tool dispatch, and the terminal ``respond``/``ask_user`` decision) with
    ``{"summary", "tool", "status", "index"}`` — the M1.4 router bridges this
    to NDJSON ``step`` events. Default ``None`` keeps this function's
    behavior byte-for-byte identical to before M1.4.

    M2.2 additions, all optional and all defaulting to M1-identical
    behavior:

    - ``gate``: a :class:`WriteGate` authorizing write-tier decisions.
      Defaults to :class:`RefuseAllWriteGate` — every write is refused,
      never dispatched, exactly as before M2.2. A permissive gate routes an
      approved write through ``engine.preflight`` -> dispatch ->
      ``engine.postwrite``; a blocking postwrite report rolls
      ``artifact_manager`` back and aborts the whole turn. ``run``-tier
      decisions are unconditionally refused regardless of ``gate`` (M2.4
      defines their own gating).
    - ``engine``: the :class:`~services.agent.guardrails.GuardrailEngine` run
      for gate-approved writes. Defaults to a fresh ``GuardrailEngine()``
      (env-var ablation-controlled). Never consulted for read tools or for
      writes the gate itself refuses.
    - ``artifact_manager``: the session's
      :class:`~services.agent.state.ArtifactManager`, already ``clone()``-d
      by the caller. Required (raises ``RuntimeError``) the first time
      ``gate`` actually approves a write — supplying a permissive gate
      without one is a caller configuration error, not a guardrail refusal.
      Unused, and safe to omit, under the default refuse-all gate.
    - ``should_cancel``: checked at the top of every iteration; when it
      returns ``True`` the turn ends immediately with
      ``AgentTurnResult(kind="cancelled", ...)`` and a trace record of kind
      ``"cancelled"``. Default ``None`` never cancels.
    """
    target_registry = registry if registry is not None else default_registry
    target_gate: WriteGate = gate if gate is not None else RefuseAllWriteGate()
    target_engine = engine if engine is not None else GuardrailEngine()
    state = AgentState(goal=message, artifacts={}, history=[], context={"language": language})
    tracer = TraceWriter(work_dir, session_id)
    planner = Planner(provider, registry=target_registry)
    steps: list[dict[str, Any]] = []

    for iteration in range(max_iterations):
        if should_cancel is not None and should_cancel():
            tracer.write("cancelled", {"iteration": iteration})
            return AgentTurnResult(
                answer=pick_language_text(_CANCELLED_ANSWER, language),
                kind="cancelled",
                steps=steps,
                trace_path=tracer.path,
                state=state,
            )

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
            _emit_step(
                steps, {"summary": decision.summary, "tool": "respond", "status": "ok"}, on_step
            )
            return AgentTurnResult(
                answer=decision.params.get("answer", ""),
                kind="answer",
                steps=steps,
                trace_path=tracer.path,
                state=state,
            )
        if decision.kind == "ask_user":
            _emit_step(
                steps, {"summary": decision.summary, "tool": "ask_user", "status": "ok"}, on_step
            )
            return AgentTurnResult(
                answer=decision.params.get("question", ""),
                kind="clarification",
                steps=steps,
                trace_path=tracer.path,
                state=state,
            )

        spec = target_registry.get(decision.tool)

        if spec is not None and spec.tier in _GATED_TIERS:
            action = {"tool": decision.tool, "params": decision.params}

            try:
                action_authorized = target_gate.authorize(action, spec)
            except ApprovalRequired:
                # M2.3 (write) / M2.4 (run — same machinery): the gate wants
                # the user's explicit approval before this action proceeds.
                # Preflight now (not later, at approve-time) so the approval
                # card carries real constraint findings, and so an action
                # that would be refused anyway is never presented as worth
                # approving.
                approval_preflight_model = (
                    artifact_manager.shadow_model if artifact_manager is not None else {}
                )
                preflight_report = target_engine.preflight(action, approval_preflight_model)
                tracer.write(
                    "preflight",
                    {
                        "iteration": iteration,
                        "tool": decision.tool,
                        "blocking": preflight_report.blocking,
                        "disabled": preflight_report.disabled,
                        "findings": [f.constraint_id for f in preflight_report.findings],
                    },
                )

                if preflight_report.blocking:
                    # Would be refused anyway — do not ask the user to
                    # approve it. Feed the planner the same structured
                    # refusal the ordinary blocking-preflight branch below
                    # produces, and let the loop continue (falls through to
                    # this iteration's common history/step bookkeeping).
                    envelope = _guardrail_refusal_envelope(preflight_report, language)
                    tracer.write(
                        "refusal",
                        {
                            "iteration": iteration,
                            "tool": decision.tool,
                            "tier": spec.tier,
                            "phase": "preflight",
                            "error_type": envelope["error_type"],
                            "message": envelope["message"],
                        },
                    )
                else:
                    preflight_warnings = preflight_report.warnings()
                    warning_payload = [
                        {
                            "constraint_id": finding.constraint_id,
                            "message": pick_language_text(finding.messages, language),
                        }
                        for finding in preflight_warnings
                    ]
                    tracer.write(
                        "user_confirm",
                        {
                            "stage": "requested",
                            "iteration": iteration,
                            "tool": decision.tool,
                            "params": decision.params,
                            "warnings": warning_payload,
                        },
                    )
                    pending_approval = {
                        "tool": decision.tool,
                        "params": decision.params,
                        "summary": decision.summary,
                        "warnings": warning_payload,
                    }
                    _emit_step(
                        steps,
                        {
                            "summary": decision.summary,
                            "tool": decision.tool,
                            "status": "pending_approval",
                        },
                        on_step,
                    )
                    return AgentTurnResult(
                        answer=_approval_proposal_answer(language, decision, warning_payload),
                        kind="approval_request",
                        steps=steps,
                        trace_path=tracer.path,
                        state=state,
                        pending_approval=pending_approval,
                    )
            else:
                if not action_authorized:
                    # Tier gate refused: unchanged M1 behavior, never dispatched.
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
                    if spec.tier == "write" and artifact_manager is None:
                        raise RuntimeError(
                            f"run_agent_turn: the write gate approved {decision.tool!r} but no "
                            "artifact_manager was provided. A permissive gate requires an "
                            "ArtifactManager (already clone()-d) to preflight/postwrite/roll back "
                            "against."
                        )
                    # Run-tier does not require artifact_manager: it never
                    # save_version()s or rollback_to()s (a run cannot have
                    # moved the network) — only preflight's shadow_model
                    # argument would use it, and {} is a safe, honest
                    # fallback when it's absent (no run-tier constraint
                    # consults the shadow model today).

                    last_good_version = artifact_manager.version if artifact_manager is not None else None
                    before_model = (
                        (artifact_manager.shadow_model or {}) if artifact_manager is not None else {}
                    )
                    preflight_report = target_engine.preflight(action, before_model)
                    tracer.write(
                        "preflight",
                        {
                            "iteration": iteration,
                            "tool": decision.tool,
                            "blocking": preflight_report.blocking,
                            "disabled": preflight_report.disabled,
                            "findings": [f.constraint_id for f in preflight_report.findings],
                        },
                    )

                    if preflight_report.blocking:
                        # REFUSE tier: skip dispatch entirely, feed the planner a
                        # structured refusal it can react to next iteration (the
                        # loop continues -- this does NOT abort the plan).
                        envelope = _guardrail_refusal_envelope(preflight_report, language)
                        tracer.write(
                            "refusal",
                            {
                                "iteration": iteration,
                                "tool": decision.tool,
                                "tier": spec.tier,
                                "phase": "preflight",
                                "error_type": envelope["error_type"],
                                "message": envelope["message"],
                            },
                        )
                    else:
                        preflight_warnings = preflight_report.warnings()
                        if preflight_warnings:
                            tracer.write(
                                "warning",
                                {
                                    "iteration": iteration,
                                    "tool": decision.tool,
                                    "phase": "preflight",
                                    "findings": [f.constraint_id for f in preflight_warnings],
                                    "messages": [
                                        pick_language_text(f.messages, language)
                                        for f in preflight_warnings
                                    ],
                                },
                            )

                        tracer.write(
                            "action_start",
                            {
                                "iteration": iteration,
                                "tool": decision.tool,
                                "params": decision.params,
                            },
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

                        if preflight_warnings:
                            envelope = _attach_guardrail_warnings(
                                envelope, preflight_warnings, language
                            )

                        if envelope["status"] == "ok":
                            if spec.tier == "write":
                                # The write landed at the toolkit-dispatch level --
                                # snapshot + verify it actually did what it claims.
                                artifact_manager.save_version()
                                after_model = artifact_manager.shadow_model or {}
                                com_result = envelope.get("verification") or {}
                                postwrite_report = target_engine.postwrite(
                                    action, before_model, after_model, com_result
                                )
                                tracer.write(
                                    "postwrite",
                                    {
                                        "iteration": iteration,
                                        "tool": decision.tool,
                                        "blocking": postwrite_report.blocking,
                                        "disabled": postwrite_report.disabled,
                                        "findings": [
                                            f.constraint_id for f in postwrite_report.findings
                                        ],
                                    },
                                )

                                if postwrite_report.blocking:
                                    # REFUSE tier, postwrite: unlike preflight, this
                                    # aborts the WHOLE plan -- roll back and stop.
                                    refusal_envelope = _guardrail_refusal_envelope(
                                        postwrite_report, language
                                    )
                                    tracer.write(
                                        "refusal",
                                        {
                                            "iteration": iteration,
                                            "tool": decision.tool,
                                            "tier": spec.tier,
                                            "phase": "postwrite",
                                            "error_type": refusal_envelope["error_type"],
                                            "message": refusal_envelope["message"],
                                        },
                                    )
                                    artifact_manager.rollback_to(last_good_version)
                                    state.history.append(
                                        StepRecord(
                                            thought=decision.summary,
                                            action=decision.tool,
                                            params=decision.params,
                                            observation=refusal_envelope,
                                        )
                                    )
                                    _emit_step(
                                        steps,
                                        {
                                            "summary": decision.summary,
                                            "tool": decision.tool,
                                            "status": refusal_envelope["status"],
                                        },
                                        on_step,
                                    )
                                    return AgentTurnResult(
                                        answer=_postwrite_abort_answer(language, postwrite_report),
                                        kind="answer",
                                        steps=steps,
                                        trace_path=tracer.path,
                                        state=state,
                                    )

                                postwrite_warnings = postwrite_report.warnings()
                                if postwrite_warnings:
                                    tracer.write(
                                        "warning",
                                        {
                                            "iteration": iteration,
                                            "tool": decision.tool,
                                            "phase": "postwrite",
                                            "findings": [
                                                f.constraint_id for f in postwrite_warnings
                                            ],
                                            "messages": [
                                                pick_language_text(f.messages, language)
                                                for f in postwrite_warnings
                                            ],
                                        },
                                    )
                                    envelope = _attach_guardrail_warnings(
                                        envelope, postwrite_warnings, language
                                    )
                            else:
                                # M2.4: run-tier post-dispatch handling.
                                # Runs never save_version()/rollback_to() --
                                # a run cannot have moved the network, so
                                # there is nothing to snapshot or restore.
                                # run_result rides inside the envelope's
                                # `data` key (tools_run.py's run_simulation
                                # return shape); a missing/None run_result
                                # (an unmapped or misbehaving run tool) makes
                                # every postrun constraint check a silent
                                # no-op (constraints.py: `if run_result is
                                # None: return None`) rather than a crash.
                                run_result = (envelope.get("data") or {}).get("run_result")
                                postrun_report = target_engine.postrun(action, run_result)
                                tracer.write(
                                    "postrun",
                                    {
                                        "iteration": iteration,
                                        "tool": decision.tool,
                                        "blocking": postrun_report.blocking,
                                        "disabled": postrun_report.disabled,
                                        "findings": [
                                            f.constraint_id for f in postrun_report.findings
                                        ],
                                    },
                                )

                                postrun_warnings = postrun_report.warnings()
                                if postrun_warnings:
                                    tracer.write(
                                        "warning",
                                        {
                                            "iteration": iteration,
                                            "tool": decision.tool,
                                            "phase": "postrun",
                                            "findings": [
                                                f.constraint_id for f in postrun_warnings
                                            ],
                                            "messages": [
                                                pick_language_text(f.messages, language)
                                                for f in postrun_warnings
                                            ],
                                        },
                                    )
                                    envelope = _attach_guardrail_warnings(
                                        envelope, postrun_warnings, language
                                    )

        elif spec is not None and spec.tier != "read":
            # Any future tier beyond read/write/run: toolkit.py's Tier
            # literal only allows "read"/"write"/"run" today, so this is
            # a defensive default for a registry that has grown a new
            # tier this loop does not yet know how to gate, not a live
            # path for "run" anymore (run joined _GATED_TIERS in M2.4).
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
            # Read-tier tool, or an unrecognized tool name: unchanged M1
            # behavior -- the gate and engine are never consulted.
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

        state.history.append(
            StepRecord(
                thought=decision.summary,
                action=decision.tool,
                params=decision.params,
                observation=envelope,
            )
        )
        _emit_step(
            steps,
            {"summary": decision.summary, "tool": decision.tool, "status": envelope["status"]},
            on_step,
        )

    return AgentTurnResult(
        answer=_forced_completion_answer(language, steps, max_iterations),
        kind="answer",
        steps=steps,
        trace_path=tracer.path,
        state=state,
    )

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

**M3.3: multi-turn session + clarification memory.** At the top of every
call, ``run_agent_turn`` attempts ``session_store.load_state(work_dir,
session_id)``; when a caller reuses the same ``work_dir``/``session_id`` as
an earlier call AND that earlier call's ``state.json`` is present and
parses, the reloaded ``AgentState`` (history, context — including the
running ``accumulated_warnings`` ledger) becomes THIS turn's starting state
instead of a fresh one. A brand-new session (no ``state.json`` yet) is a
no-op reload, so this is byte-for-byte inert for every pre-M3.3 caller. At
the END of every turn — every terminal ``kind``, including
``clarification``/``approval_request``/``cancelled``, not just a clean
``answer`` — the (possibly mutated) state is written back via
``session_store.save_state`` through the single ``finalize()`` choke point
below, so the very next call on the same session always has the freshest
state available, regardless of how this turn ended. Two concrete effects of
carrying state forward:

- **Clarification memory:** when the reloaded state's previous turn ended in
  ``ask_user`` (tracked via ``state.context["pending_clarification"]``,
  since a terminal ``ask_user``/``respond`` decision is never appended to
  ``state.history`` — see the M1.3 loop above), this turn prepends a
  deterministic system note (the original goal + the question + this turn's
  reply, ko/en/ja) into ``state.context["warnings"]`` — the ONE per-turn
  context field ``planner.py``'s ``_state_block`` already renders (present
  since M1.3, unused until now) — so the merge is left to the planner
  prompt/model, never regex-spliced here.
- **Accumulated warnings surfacing:** every WARN-tier finding that actually
  fires on a DISPATCHED write/run action (never a preflight shown on an
  approval request that might still be declined) is recorded into
  ``state.context["accumulated_warnings"]`` (deduped by
  constraint_id+message — see ``_record_accumulated_warnings``). Warnings
  already present in that ledger BEFORE this turn started (i.e. from an
  EARLIER turn) are appended as a deterministic suffix to this turn's final
  ``answer`` (kind == "answer" only) via ``_append_carried_forward_warnings``
  — the cross-turn counterpart to the M3.2 agentic-tier safety net
  (``_append_agentic_warning_notice``, unchanged, intra-turn only); both
  share one low-level renderer, ``_append_warning_block``, so a future
  formatting fix only has one place to land.

The active network is whatever ``tools_network.set_active_inpx`` points at —
``config.SIMULATION_INPX_PATH`` (the demo network) by default. Callers that
serve a specific dataset set it before running a turn (module-global session
context; acceptable M1 limitation per tools_network's docstring).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
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
from services.agent import complexity, session_store
from services.agent.gates import ApprovalRequired
from services.agent.guardrails import GuardrailEngine, GuardrailReport
from services.agent.planner import (
    SELECTION_CONTEXT_KEY,
    Planner,
    PlannerDecision,
    pick_language_text,
)
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

# M3.2: the agentic-tier anti-exploit safety net's notice header — see
# _append_agentic_warning_notice below. Assembled without any further LLM
# call, same discipline as the deterministic texts above.
_AGENTIC_WARNING_NOTICE_HEADER = {
    "ko": (
        "참고: 이번 요청 처리 중 다음 경고가 발생했습니다. 위 결과는 반드시 아래 내용과 "
        "함께 해석해야 합니다:"
    ),
    "en": (
        "Note: the following warning(s) fired while handling this request. The result "
        "above must be read together with these, not in isolation:"
    ),
    "ja": (
        "注意: 今回のリクエスト処理中に以下の警告が発生しました。上記の結果は必ずこれらと"
        "合わせて解釈してください:"
    ),
}

# Post-incident fix (2026-07-24): the message a PreapprovedWriteGate-scoped
# action gets when its approval signature was ALREADY consumed earlier in
# THIS SAME re-run (services.agent.gates.PreapprovedWriteGate.authorize now
# consumes on success — see its docstring for the live-demo incident this
# closes: one approval driving six real Vissim runs). Deliberately DISTINCT
# from _tier_refusal_envelope's generic "arrives in a later milestone" text
# below, which would actively mislead the model here: the action WAS
# approved and DID run once; it just cannot run again on the strength of
# that same approval. Assembled without any further LLM call, same
# discipline as every other deterministic text in this module.
_APPROVAL_CONSUMED_MESSAGE = {
    "ko": (
        "이 작업({tool})은 이번 승인으로 이미 한 번 실행되었습니다. 같은 작업을 다시 "
        "요청하지 말고, 지금까지 확인한 내용을 바탕으로 사용자에게 답변하세요."
    ),
    "en": (
        "This action ({tool}) has already been dispatched once under this approval. "
        "Do not re-issue it — respond to the user with what has been found so far "
        "instead."
    ),
    "ja": (
        "この操作（{tool}）は今回の承認で既に一度実行されました。同じ操作を再度要求せず、"
        "これまでに確認した内容をもとにユーザーに回答してください。"
    ),
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

# M3.3: the context key carrying a not-yet-resolved clarification across a
# state.json reload — {"goal": <the goal that prompted the question>,
# "question": <the question asked>}. Set by _finalize_turn whenever a turn
# ends with kind == "clarification" (ask_user is planner-terminal and, like
# `respond`, is never appended to state.history — see the module docstring
# — so this context field, not history, is the only place "was the previous
# turn a clarification" can be recovered from after a reload); cleared the
# moment the NEXT turn consumes it, whether or not that turn asks another
# question (a second question gets its OWN fresh entry, keyed off that
# turn's own goal — see run_agent_turn's reload block).
_CLARIFICATION_CONTEXT_KEY = "pending_clarification"

# M3.3: the deterministic system note prepended into
# state.context["warnings"] (planner.py's _state_block already renders this
# field verbatim; it is otherwise unused today) when a reloaded session's
# previous turn was left waiting on a clarification question. Merging the
# original goal with the user's new reply is deliberately left to the
# planner PROMPT (i.e. the model reads both raw texts together), never done
# by string-splicing/regex here — see the module docstring.
_CLARIFICATION_MERGE_NOTE = {
    "ko": (
        "[시스템 참고: 이전 요청 '{goal}'에 대해 '{question}'라고 확인을 요청했고, "
        "사용자가 이번에 '{reply}'라고 응답했습니다. 두 내용을 종합하여 이번 요청을 "
        "이해하세요.]"
    ),
    "en": (
        "[System note: for the earlier request '{goal}', the assistant asked "
        "'{question}', and the user just replied '{reply}'. Reconcile both "
        "when understanding this request.]"
    ),
    "ja": (
        "[システム注記: 以前のリクエスト『{goal}』について『{question}』と確認を求め、"
        "ユーザーは今回『{reply}』と返答しました。両方を踏まえて今回のリクエストを"
        "理解してください。]"
    ),
}

# M3.3: the cross-turn accumulated-warnings suffix header — deliberately
# DIFFERENT text from _AGENTIC_WARNING_NOTICE_HEADER's "참고:"/"Note:" so the
# two notices (intra-turn agentic safety net vs. cross-turn carried-forward
# ledger) are never visually confusable with each other if both happen to
# fire on the same agentic-tier turn.
_CARRIED_FORWARD_WARNINGS_HEADER = {
    "ko": (
        "참고: 이전 대화에서 발생한 경고가 여전히 유효합니다. 이번 답변도 아래 내용과 "
        "함께 참고하세요:"
    ),
    "en": (
        "Note: warning(s) from earlier in this conversation still apply. Read this "
        "answer together with them:"
    ),
    "ja": (
        "注意: これまでの会話で発生した警告は引き続き有効です。今回の回答もこれらと"
        "合わせてご確認ください:"
    ),
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


#: Dispatch-level error types that ARE refusals-with-reason (the registry
#: rejected the call before any execution): a hallucinated param or tool
#: name never lands, and the eval layer's silent-failure detector keys on
#: "refusal"-kind trace records — without this tagging a rejected call
#: scored as an undetected lie under BOTH arms (DECISIONS 2026-07-24, M5.4
#: batch). tool_execution_error is deliberately excluded: a crash is a
#: failure, not a refusal.
_DISPATCH_REFUSAL_ERROR_TYPES = frozenset({"invalid_params", "unknown_tool"})


def _trace_dispatch_refusal(
    tracer: "TraceWriter", iteration: int, tool: str | None, envelope: dict[str, Any]
) -> None:
    """Write a "refusal"-kind trace record for validation-class dispatch
    rejections, right after the action_end record."""
    if envelope.get("status") == "error" and envelope.get("error_type") in _DISPATCH_REFUSAL_ERROR_TYPES:
        tracer.write(
            "refusal",
            {
                "iteration": iteration,
                "tool": tool,
                "error_type": envelope.get("error_type"),
                "message": envelope.get("message"),
                "phase": "dispatch",
            },
        )


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


def _approval_consumed_envelope(tool_name: str, language: str) -> dict[str, Any]:
    """The structured observation for a gate refusal whose
    ``refusal_reason`` came back ``"approval_consumed"`` (post-incident fix
    — see ``services.agent.gates.PreapprovedWriteGate`` and
    ``_APPROVAL_CONSUMED_MESSAGE`` above). Same 7-key envelope shape as
    ``_tier_refusal_envelope``, distinct ``error_type`` so trace/eval
    consumers can tell the two refusal reasons apart."""
    return {
        "status": "error",
        "error_type": "approval_consumed",
        "message": pick_language_text(_APPROVAL_CONSUMED_MESSAGE, language).format(tool=tool_name),
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


def _collect_history_warnings(history: list[StepRecord]) -> list[dict[str, Any]]:
    """Every accumulated ``guardrail_warnings`` entry across ``history``'s
    observations, in first-seen order, de-duplicated by ``constraint_id``.

    This is the M3.2 agentic-tier safety net's source of truth for what must
    ride along on the final answer regardless of what the model's own answer
    text said — see ``_append_agentic_warning_notice``. Observations that
    are not dicts (never happens today — every dispatch/refusal envelope and
    every rule-fallback observation is a dict — but StepRecord.observation is
    typed ``Any``) are skipped defensively rather than raising.
    """
    seen: set[Any] = set()
    collected: list[dict[str, Any]] = []
    for record in history:
        observation = record.observation
        if not isinstance(observation, dict):
            continue
        for warning in observation.get("guardrail_warnings") or []:
            constraint_id = warning.get("constraint_id")
            if constraint_id in seen:
                continue
            seen.add(constraint_id)
            collected.append(warning)
    return collected


def _append_warning_block(answer: str, header: str, warnings: list[dict[str, Any]]) -> str:
    """Shared low-level suffix renderer (M3.3): header line + one ``- message``
    bullet per warning, appended after a blank line. A no-op (returns
    ``answer`` unchanged) when ``warnings`` is empty, so a clean answer with
    nothing to report is never mutated. Used by both
    ``_append_agentic_warning_notice`` (M3.2, intra-turn, agentic-tier only)
    and ``_append_carried_forward_warnings`` (M3.3, cross-turn, every tier) —
    one formatting implementation, two distinct trigger conditions and
    header texts (see each caller's own docstring for why they stay
    separate rather than being merged into one trigger).
    """
    if not warnings:
        return answer
    lines = [f"- {warning.get('message', '')}" for warning in warnings]
    notice = header + "\n" + "\n".join(lines)
    if not answer.strip():
        return notice
    return answer + "\n\n" + notice


def _append_agentic_warning_notice(
    language: str, answer: str, warnings: list[dict[str, Any]]
) -> str:
    """M3.2 agentic-tier safety net for the "may not exploit an improvement
    that coexists with a fired constraint warning" prompt rule
    (docs/phase2/02-ROADMAP.md M3.2): every accumulated guardrail warning is
    appended to the agent's final answer verbatim, UNCONDITIONALLY —
    regardless of whether the model's own answer text mentioned it. This is
    a structural guarantee, not a prompt hope: a fired warning (e.g.
    ``vehicles_deleted_in_run`` on the run that produced the "best" reported
    delay) can never be silently absent from what the user actually reads
    next to a claimed improvement. Deterministic, assembled without any
    further LLM call, same discipline as ``_postwrite_abort_answer``/
    ``_forced_completion_answer``. A no-op (returns ``answer`` unchanged)
    when ``warnings`` is empty.
    """
    return _append_warning_block(
        answer, pick_language_text(_AGENTIC_WARNING_NOTICE_HEADER, language), warnings
    )


def _append_carried_forward_warnings(
    language: str, answer: str, warnings: list[dict[str, Any]]
) -> str:
    """M3.3 cross-turn counterpart to ``_append_agentic_warning_notice``:
    unconditionally appends warnings that were ALREADY in
    ``state.context["accumulated_warnings"]`` before THIS turn started (see
    ``run_agent_turn``'s ``carried_forward_warnings`` snapshot) to a
    kind == "answer" turn's final answer, regardless of tier — the roadmap's
    accept criterion ("a warning from turn 2 still visible in turn 3's
    answer") does not restrict this to the agentic tier. Deliberately scoped
    to warnings from EARLIER turns only (not this turn's own new findings):
    an agentic-tier turn's own same-turn findings are already covered by
    ``_append_agentic_warning_notice`` above, and duplicating them here
    would produce two overlapping notice blocks on the same answer. A no-op
    when ``warnings`` is empty (the common case: most sessions never
    accumulate a warning at all).
    """
    return _append_warning_block(
        answer, pick_language_text(_CARRIED_FORWARD_WARNINGS_HEADER, language), warnings
    )


def _record_accumulated_warnings(
    state: AgentState, warn_findings: list[Any], language: str
) -> None:
    """M3.3: append every WARN-tier finding that fired on a DISPATCHED
    write/run action to ``state.context["accumulated_warnings"]`` —
    ``{"constraint_id", "message"}`` per finding, deduped by
    (constraint_id, message) so an identical warning firing again later in
    the same session is not repeated. This is the session's persisted,
    cross-turn ledger (``session_store.save_state`` writes ``state.context``
    to ``state.json`` at the end of every turn via ``_finalize_turn`` below)
    — distinct from ``_attach_guardrail_warnings``, which only rides ONE
    tool call's own observation envelope for the CURRENT turn's planner to
    see. Deliberately NOT called for a preflight warning shown on an
    ``approval_request`` the user might still decline (see the ``except
    ApprovalRequired`` branch) — nothing has actually happened yet, so
    recording it here would be premature.
    """
    accumulated = state.context.setdefault("accumulated_warnings", [])
    seen = {(entry.get("constraint_id"), entry.get("message")) for entry in accumulated}
    for finding in warn_findings:
        message = pick_language_text(finding.messages, language)
        key = (finding.constraint_id, message)
        if key in seen:
            continue
        seen.add(key)
        accumulated.append({"constraint_id": finding.constraint_id, "message": message})


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


def _finalize_turn(
    result: AgentTurnResult,
    *,
    state: AgentState,
    language: str,
    work_dir: Path | str,
    session_id: str,
    carried_forward_warnings: list[dict[str, Any]],
) -> AgentTurnResult:
    """M3.3: the single choke point every ``return AgentTurnResult(...)`` in
    ``run_agent_turn`` routes through, so "persist state.json" and "append
    the cross-turn warning suffix" can never be forgotten on a return path
    added later (docs/phase2/02-ROADMAP.md M3.3's "write state.json ... at
    the END of every turn, ALL terminal kinds").

    1. Clarification-memory bookkeeping: a turn ending in ``kind ==
       "clarification"`` records ``{goal, question}`` under
       ``state.context[_CLARIFICATION_CONTEXT_KEY]`` for the NEXT reload to
       consume; every other terminal kind clears it (the question either
       just got answered — this turn's reload already popped and consumed
       the OLD entry, see ``run_agent_turn`` — or was never asked).
    2. Cross-turn warning suffix: ``carried_forward_warnings`` (the
       ``accumulated_warnings`` snapshot taken BEFORE this turn ran) is
       appended to the answer, but ONLY for ``kind == "answer"`` — a
       clarification question, an approval proposal line, or the short
       cancellation notice each have their own pinned deterministic text
       elsewhere and are left untouched.
    3. Persistence: ``session_store.save_state`` writes ``state`` (by now
       carrying every ``StepRecord``/``accumulated_warnings`` entry this
       turn produced) to ``<work_dir>/state.json``. Best-effort: a
       persistence failure (bad session_id, unwritable dir, a
       non-JSON-serializable value that snuck into ``context``) is logged
       and swallowed, never allowed to turn an already-computed answer into
       a 500 — the session simply won't resume with full context next time,
       which is what the reload's own OWN "missing/corrupt state.json
       degrades to a fresh state" contract already treats as a normal,
       recoverable case.
    """
    if result.kind == "clarification":
        state.context[_CLARIFICATION_CONTEXT_KEY] = {
            "goal": state.goal,
            "question": result.answer,
        }
    else:
        state.context.pop(_CLARIFICATION_CONTEXT_KEY, None)

    finalized = result
    if result.kind == "answer" and carried_forward_warnings:
        finalized = replace(
            result,
            answer=_append_carried_forward_warnings(
                language, result.answer, carried_forward_warnings
            ),
        )

    try:
        session_store.save_state(work_dir, session_id, state)
    except Exception:  # noqa: BLE001 - persistence must never break a computed answer
        logger.exception(
            "Failed to persist agent session state session_id=%s work_dir=%s",
            session_id,
            work_dir,
        )

    return finalized


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
    selection: dict[str, Any] | None = None,
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

    M3.3 addition, unconditional (no opt-out flag — see the module
    docstring): ``session_store.load_state(work_dir, session_id)`` is
    attempted first; a caller reusing the same ``work_dir``/``session_id``
    as an earlier call transparently resumes that session's history and
    context (including ``accumulated_warnings``), while a brand-new
    session/work dir (no ``state.json`` yet) is a no-op reload — every
    pre-M3.3 caller's behavior is unaffected.

    P7 addition, optional: ``selection`` is what the user has CLICKED in the
    rendered network for THIS turn — the compact structure
    ``services.network_selection.build_selection_context`` produces
    (``{"nodes", "links", "element", "scenario_ids"}``, every key optional).
    It is surfaced to the PLANNER as one delimited block in the per-turn
    state block (``planner.render_selection_block``) so "이 링크 차로 수
    알려줘" has a referent.

    **Selection is CONTEXT ONLY.** It deliberately does not touch the write
    gate, the tool offer, the tier classification, or the guardrail engine's
    preflight/postwrite: the planner must still name explicit targets in its
    tool params, and those params are still validated exactly as before. A
    clicked object tells the agent *which* object the sentence is about; it
    never authorizes an action on it. ``None`` (the default, and every
    pre-P7 caller) stores nothing and renders nothing — the prompt is
    byte-identical to pre-P7.
    """
    target_registry = registry if registry is not None else default_registry
    target_gate: WriteGate = gate if gate is not None else RefuseAllWriteGate()
    target_engine = engine if engine is not None else GuardrailEngine()
    # M3.2: classified ONCE per turn from the CURRENT message (not
    # re-classified per iteration/per compacted state block — see
    # complexity.py's module docstring) and threaded through state.context so
    # the planner (system-prompt selection, meta["tier"] on every decision,
    # the mandatory-preview diversion) and this loop (the agentic-tier
    # warning-notice safety net below) share the exact same value. Computed
    # from ``message`` regardless of whether this turn reloads a prior
    # session's state — a resumed conversation's tier can legitimately shift
    # turn to turn (e.g. a plain follow-up after an optimization request).
    tier = complexity.classify_tier(message)

    # M3.3: reload-by-session-id (see the module + function docstrings).
    reloaded_state = session_store.load_state(work_dir, session_id)
    if reloaded_state is not None:
        state = reloaded_state
        state.context["language"] = language
        state.context["tier"] = tier
        # Clarification memory: consume (pop, not peek) whatever the
        # PREVIOUS turn left pending — this turn's message is presumed to be
        # the user's reply to it. A fresh, per-turn note only; stale notes
        # from turns further back never accumulate here (that is what
        # accumulated_warnings is for, deliberately kept as a SEPARATE
        # ledger — see _record_accumulated_warnings).
        pending_clarification = state.context.pop(_CLARIFICATION_CONTEXT_KEY, None)
        if pending_clarification:
            note = pick_language_text(_CLARIFICATION_MERGE_NOTE, language).format(
                goal=pending_clarification.get("goal", ""),
                question=pending_clarification.get("question", ""),
                reply=message,
            )
            state.context["warnings"] = [note]
        else:
            state.context["warnings"] = []
        # This turn's own message is the goal from here on (persisted goal
        # from a reloaded turn would otherwise leak a stale prior request
        # into every subsequent planner prompt via _state_block's "Goal:"
        # line).
        state.goal = message
    else:
        state = AgentState(
            goal=message, artifacts={}, history=[], context={"language": language, "tier": tier}
        )

    # P7: THIS turn's on-screen selection, set unconditionally (set-or-clear).
    # A resumed session's state.json carries the PREVIOUS turn's selection in
    # `context`, and the user may have clicked elsewhere — or deselected —
    # since; the client re-sends its current selection on every turn, so the
    # stored value is always replaced, never merged, and an absent selection
    # actively clears the stale one rather than letting it haunt the prompt.
    if selection:
        state.context[SELECTION_CONTEXT_KEY] = selection
    else:
        state.context.pop(SELECTION_CONTEXT_KEY, None)

    # Snapshot BEFORE this turn's own findings can append to it, so the
    # cross-turn suffix (_append_carried_forward_warnings) only ever shows
    # warnings from EARLIER turns — see _finalize_turn.
    carried_forward_warnings: list[dict[str, Any]] = list(
        state.context.get("accumulated_warnings") or []
    )

    tracer = TraceWriter(work_dir, session_id)
    planner = Planner(provider, registry=target_registry)
    steps: list[dict[str, Any]] = []

    def finalize(result: AgentTurnResult) -> AgentTurnResult:
        return _finalize_turn(
            result,
            state=state,
            language=language,
            work_dir=work_dir,
            session_id=session_id,
            carried_forward_warnings=carried_forward_warnings,
        )

    for iteration in range(max_iterations):
        if should_cancel is not None and should_cancel():
            tracer.write("cancelled", {"iteration": iteration})
            return finalize(
                AgentTurnResult(
                    answer=pick_language_text(_CANCELLED_ANSWER, language),
                    kind="cancelled",
                    steps=steps,
                    trace_path=tracer.path,
                    state=state,
                )
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
            answer = decision.params.get("answer", "")
            if tier == "agentic":
                # M3.2 anti-exploit safety net: never let a fired warning go
                # missing from the final answer, regardless of what the
                # model's own text said — see _append_agentic_warning_notice.
                answer = _append_agentic_warning_notice(
                    language, answer, _collect_history_warnings(state.history)
                )
            _emit_step(
                steps, {"summary": decision.summary, "tool": "respond", "status": "ok"}, on_step
            )
            return finalize(
                AgentTurnResult(
                    answer=answer,
                    kind="answer",
                    steps=steps,
                    trace_path=tracer.path,
                    state=state,
                )
            )
        if decision.kind == "ask_user":
            _emit_step(
                steps, {"summary": decision.summary, "tool": "ask_user", "status": "ok"}, on_step
            )
            return finalize(
                AgentTurnResult(
                    answer=decision.params.get("question", ""),
                    kind="clarification",
                    steps=steps,
                    trace_path=tracer.path,
                    state=state,
                )
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
                    return finalize(
                        AgentTurnResult(
                            answer=_approval_proposal_answer(language, decision, warning_payload),
                            kind="approval_request",
                            steps=steps,
                            trace_path=tracer.path,
                            state=state,
                            pending_approval=pending_approval,
                        )
                    )
            else:
                if not action_authorized:
                    # Tier gate refused: unchanged M1 behavior, never
                    # dispatched -- UNLESS the gate can tell us THIS action's
                    # own approval was already consumed earlier in this same
                    # re-run (PreapprovedWriteGate, post-incident fix: an
                    # approved signature now authorizes exactly one
                    # dispatch). In that case the generic "arrives in a
                    # later milestone" tier-refusal text would mislead the
                    # model -- the action WAS approved and DID run -- so a
                    # distinct message is used instead. `refusal_reason` is
                    # an optional protocol extension (see
                    # PreapprovedWriteGate's docstring); gates that don't
                    # define it (RefuseAllWriteGate, ProposeWriteGate) fall
                    # back to the unchanged generic envelope.
                    refusal_reason_fn = getattr(target_gate, "refusal_reason", None)
                    refusal_reason = (
                        refusal_reason_fn(action, spec) if refusal_reason_fn is not None else None
                    )
                    if refusal_reason == "approval_consumed":
                        envelope = _approval_consumed_envelope(decision.tool, language)
                    else:
                        envelope = _tier_refusal_envelope(decision.tool, spec.tier)
                    tracer.write(
                        "refusal",
                        {
                            "iteration": iteration,
                            "tool": decision.tool,
                            "tier": spec.tier,
                            "error_type": envelope["error_type"],
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
                        _trace_dispatch_refusal(tracer, iteration, decision.tool, envelope)

                        if preflight_warnings:
                            envelope = _attach_guardrail_warnings(
                                envelope, preflight_warnings, language
                            )
                            # M3.3: the action actually dispatched (see the
                            # class docstring for why an approval-pending
                            # preflight warning is NOT recorded here) --
                            # persist it into the cross-turn ledger too.
                            _record_accumulated_warnings(state, preflight_warnings, language)

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
                                    return finalize(
                                        AgentTurnResult(
                                            answer=_postwrite_abort_answer(
                                                language, postwrite_report
                                            ),
                                            kind="answer",
                                            steps=steps,
                                            trace_path=tracer.path,
                                            state=state,
                                        )
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
                                    _record_accumulated_warnings(
                                        state, postwrite_warnings, language
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
                                    _record_accumulated_warnings(
                                        state, postrun_warnings, language
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
            _trace_dispatch_refusal(tracer, iteration, decision.tool, envelope)

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

    return finalize(
        AgentTurnResult(
            answer=_forced_completion_answer(language, steps, max_iterations),
            kind="answer",
            steps=steps,
            trace_path=tracer.path,
            state=state,
        )
    )

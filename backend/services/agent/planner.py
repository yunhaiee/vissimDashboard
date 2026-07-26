"""Planner: one decision per loop iteration, provider-agnostic.

The planner owns the "think" half of the M1.3 loop (docs/phase2/01-ARCHITECTURE.md
§3): it assembles the per-turn prompt (static system prompt + compact state
block), offers the provider the native tool definitions it may act with, and
normalizes the provider's structured action into a :class:`PlannerDecision`.
It never dispatches anything — execution, tier gating, and tracing are
orchestrator.py's job.

Three design points from the M1.3 brief live here:

- **Terminal actions.** ``respond`` and ``ask_user`` end the turn. They are
  planner-terminal, not dispatchable tools, so they are NOT in the M0.4
  registry — their native tool defs are constructed in this module and
  offered to the provider alongside the registry's read-tier tools.
- **The ``summary`` param.** Every native tool def gets an extra REQUIRED
  string param ``summary`` (≤80 chars, the planner's step narration in the
  user's language, defensively truncated here). It is planner metadata, not
  a tool param, and is stripped from params BEFORE the orchestrator's strict
  dispatch validation would reject it.
- **Rule-based fallback (ladder rung 3).** When the provider's own retry
  ladder is exhausted (``generate_structured`` returned ``(None, meta)``),
  the planner falls back deterministically: a safe grounding read when the
  history is empty, otherwise ``ask_user`` — so the loop can never wedge
  (architecture §6).

P7 adds one more thing to the per-turn state block: the objects the user
CLICKED in the rendered network (``state.context["selection"]``, put there by
``orchestrator.run_agent_turn(selection=...)``), rendered as one compact,
clearly-delimited block so "이 링크 차로 수 알려줘" has a referent. It is
context ONLY — it changes no gate, no tool offer and no preflight; the model
still names explicit ids in its tool params, and the guardrails still validate
them. No selection renders nothing at all, so every pre-P7 caller's prompt
stays byte-identical.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from services.agent import complexity
from services.agent.state import AgentState
from services.agent.toolkit import (
    ToolRegistry,
    default_registry,
    native_tool_defs,
    render_contract,
)

DEFAULT_SYSTEM_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "v1_system.md"

SUMMARY_MAX_CHARS = 80

# Prompt-economy knobs (M1.3 brief): last-K history entries in the state
# block, each observation compacted to ~300 chars.
_HISTORY_K = 6
_OBSERVATION_SNIPPET_CHARS = 300

# M1: only read-tier registry tools were offered to the provider. M2.3 adds
# write-tier tools to the offer: the approval flow (services.agent.gates,
# orchestrator.py's ApprovalRequired handling) needs the planner to be able
# to SELECT a write action in the first place, since a native tool_choice
# call can only ever pick from tools actually offered here -- gating safety
# now lives entirely in the dispatch-side tier gate (orchestrator.py's
# WriteGate) and the guardrail engine, not in withholding the tool
# definition itself. ``run`` tools join in M2.4 behind their own gating.
_OFFERED_TIERS = frozenset({"read", "write", "run"})  # run added at M2.4 review: the gate, not the offer, is the safety layer

_FALLBACK_GROUNDING_TOOL = "get_network_overview"

_TERMINAL_TOOL_DEFS: tuple[dict[str, Any], ...] = (
    {
        "name": "respond",
        "description": "Deliver the final answer to the user and end the turn.",
        "input_schema": {
            "type": "object",
            "properties": {
                "answer": {
                    "type": "string",
                    "description": "The final answer, in the user's language.",
                }
            },
            "required": ["answer"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ask_user",
        "description": "Ask the user one clarification question and end the turn.",
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "One specific question, in the user's language.",
                }
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
)

# Appended after render_contract() when filling the system prompt's
# {TOOL_CONTRACT} placeholder, so the prompt documents the terminal actions
# in the same format as the registry tools.
_TERMINAL_CONTRACT = (
    "## respond\n"
    "tier: terminal | provider: planner\n"
    "Deliver the final answer to the user and end the turn.\n"
    'params: {"type":"object","properties":{"answer":{"type":"string"}},"required":["answer"]}\n'
    "\n"
    "## ask_user\n"
    "tier: terminal | provider: planner\n"
    "Ask the user one clarification question and end the turn.\n"
    'params: {"type":"object","properties":{"question":{"type":"string"}},"required":["question"]}\n'
)

# Deterministic ko/en/ja texts for the rule-based fallback. The clarification
# question stays clean user-facing language; the provider's raw error string
# travels in decision.meta (and the trace), never in the question itself.
_FALLBACK_GROUNDING_SUMMARY = {
    "ko": "네트워크 개요부터 확인합니다",
    "en": "Starting with a network overview",
    "ja": "ネットワークの概要から確認します",
}
_FALLBACK_ASK_SUMMARY = {
    "ko": "요청 내용을 다시 여쭤봅니다",
    "en": "Asking the user to restate the request",
    "ja": "リクエスト内容を再確認します",
}
_FALLBACK_QUESTION = {
    "ko": (
        "요청을 정확히 이해하지 못했습니다. 어떤 네트워크 객체나 지표를 확인하면 될까요? "
        "예: '107번 입력 교통량'"
    ),
    "en": (
        "I could not understand the request. Which network object or metric should I "
        "look up? For example: 'vehicle input 107'."
    ),
    "ja": (
        "リクエストを正しく理解できませんでした。どのネットワークオブジェクトや指標を"
        "確認しましょうか？ 例：「107番の入力交通量」"
    ),
}

# M3.2: deterministic ko/en/ja step narration for the mandatory-preview
# diversion (see _mandatory_preview_decision) -- complex/agentic tiers may
# not end the turn with `respond` as their very first decision; this is the
# narrated grounding read substituted in its place.
_MANDATORY_PREVIEW_SUMMARY = {
    "ko": "결론 전에 근거 데이터를 먼저 확인합니다",
    "en": "Gathering grounding data before concluding",
    "ja": "結論の前に根拠データを確認します",
}

# ---------------------------------------------------------------------------
# P7: the on-screen selection block
# ---------------------------------------------------------------------------

#: ``state.context`` key ``orchestrator.run_agent_turn`` stores this turn's
#: on-screen selection under (see its ``selection`` parameter). One name,
#: shared by writer and reader, so the two can never drift.
SELECTION_CONTEXT_KEY = "selection"

#: Header of the selection block. The ko label is what the user's own UI calls
#: the thing; the English half is the instruction that keeps this
#: CONTEXT-ONLY: the planner must still put explicit ids in its tool params
#: (which the guardrail engine then validates), exactly as it did pre-P7.
_SELECTION_BLOCK_HEADER = (
    "[사용자가 화면에서 선택한 객체] (context only — still name explicit ids in tool params)"
)

#: Rendered names are truncated here: a name comes from the parsed .inpx, not
#: the client, but this block is rebuilt on EVERY planner call, so prompt
#: economy wins over showing a pathological 300-char link name in full.
_SELECTION_NAME_CHARS = 60

_SELECTION_MISSING_SUFFIX = "not found in this network"


def _selection_name(value: Any) -> str:
    """A quoted, truncated display name, or ``""`` when there is none."""
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > _SELECTION_NAME_CHARS:
        text = text[: _SELECTION_NAME_CHARS - 3] + "..."
    return f'"{text}"'


def _selection_number(value: Any) -> str:
    """Compact number rendering: ``280.0`` -> ``280``, ``23.75`` -> ``23.75``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return f"{value:g}" if isinstance(value, float) else str(value)


def _selection_link_line(link: dict[str, Any]) -> str:
    kind = "connector" if link.get("is_connector") else "link"
    if link.get("missing"):
        # Stated, never dropped: the agent has to be able to say "that link is
        # not in this network" instead of silently answering about something
        # else (same rationale as network_selection.resolve_selected_links).
        return f"- link {link.get('id')}: {_SELECTION_MISSING_SUFFIX}"
    parts = [f"- {kind} {link.get('id')}"]
    name = _selection_name(link.get("name"))
    if name:
        parts.append(name)
    if link.get("lanes") is not None:
        parts.append(f"lanes={_selection_number(link.get('lanes'))}")
    if link.get("length") is not None:
        parts.append(f"length={_selection_number(link.get('length'))}m")
    if link.get("from_link"):
        parts.append(f"from={link.get('from_link')}")
    if link.get("to_link"):
        parts.append(f"to={link.get('to_link')}")
    return " ".join(parts)


def _selection_node_line(node: dict[str, Any]) -> str:
    if node.get("missing"):
        return f"- node {node.get('id')}: {_SELECTION_MISSING_SUFFIX}"
    name = _selection_name(node.get("name"))
    return f"- node {node.get('id')}" + (f" {name}" if name else "")


def _selection_element_line(element: dict[str, Any]) -> str:
    head = f"- element {element.get('type')} {element.get('id')}"
    if element.get("missing"):
        return f"{head}: {_SELECTION_MISSING_SUFFIX}"
    parts = [head]
    name = _selection_name(element.get("name"))
    if name:
        parts.append(name)
    if element.get("link"):
        link_name = _selection_name(element.get("link_name"))
        parts.append(f"link={element.get('link')}" + (f" {link_name}" if link_name else ""))
    if element.get("lane") is not None:
        parts.append(f"lane={_selection_number(element.get('lane'))}")
    if element.get("pos") is not None:
        parts.append(f"pos={_selection_number(element.get('pos'))}")
    return " ".join(parts)


def render_selection_block(selection: dict[str, Any] | None) -> list[str]:
    """The state block's lines for ``selection`` (empty list when there is none).

    Shape produced by ``services.network_selection.build_selection_context``:
    ``{"nodes": [...], "links": [...], "element": {...}, "scenario_ids": [...]}``,
    every key optional. One short line per object — worst case today is 6 links
    + 6 nodes + 1 element + 1 scenario line, and each line is a handful of
    tokens, because this whole block is re-sent on every loop iteration.
    Anything unrecognized is skipped rather than dumped raw.
    """
    if not isinstance(selection, dict) or not selection:
        return []

    lines: list[str] = []
    for node in selection.get("nodes") or []:
        if isinstance(node, dict):
            lines.append(_selection_node_line(node))
    for link in selection.get("links") or []:
        if isinstance(link, dict):
            lines.append(_selection_link_line(link))
    element = selection.get("element")
    if isinstance(element, dict):
        lines.append(_selection_element_line(element))
    scenario_ids = [str(value) for value in (selection.get("scenario_ids") or [])]
    if scenario_ids:
        lines.append("- scenarios: " + ", ".join(scenario_ids))

    if not lines:
        return []
    return [_SELECTION_BLOCK_HEADER, *lines]


def pick_language_text(table: dict[str, str], language: str) -> str:
    """The language table lookup every deterministic agent text goes through
    (ko primary — unknown codes fall back to Korean)."""
    return table.get(language, table["ko"])


@dataclass
class PlannerDecision:
    """One planner decision: a tool to dispatch, or a terminal action.

    - kind "tool": ``tool``/``params`` name a registry dispatch.
    - kind "respond": ``params["answer"]`` is the final answer; turn ends.
    - kind "ask_user": ``params["question"]`` goes to the user; turn ends.

    ``summary`` is the ≤80-char step narration (user's language); ``meta`` is
    the provider's generate_structured meta, or the rule-fallback meta
    (``{"rule_fallback": True, "rung": 3, "provider_meta": ...}``).
    """

    kind: str
    summary: str
    meta: dict[str, Any]
    tool: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


@lru_cache(maxsize=8)
def _read_prompt_file(path_str: str, mtime: float) -> str:
    del mtime  # cache-busting arg only (same pattern as tools_network's model cache)
    return Path(path_str).read_text(encoding="utf-8")


def _with_summary_param(tool_def: dict[str, Any]) -> dict[str, Any]:
    """A copy of ``tool_def`` whose schema requires the ``summary`` string."""
    augmented = copy.deepcopy(tool_def)
    schema = augmented.setdefault("input_schema", {"type": "object"})
    schema.setdefault("properties", {})["summary"] = {
        "type": "string",
        "maxLength": SUMMARY_MAX_CHARS,
        "description": (
            "Step narration shown live in the UI: at most 80 characters, in the "
            "user's language."
        ),
    }
    required = list(schema.get("required", []))
    if "summary" not in required:
        required.append("summary")
    schema["required"] = required
    return augmented


def _observation_snippet(observation: Any) -> str:
    try:
        text = json.dumps(observation, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(observation)
    if len(text) > _OBSERVATION_SNIPPET_CHARS:
        text = text[: _OBSERVATION_SNIPPET_CHARS - 3] + "..."
    return text


class Planner:
    """Builds prompts, calls the provider's ``generate_structured`` seam, and
    normalizes its output (or falls back deterministically) into decisions."""

    def __init__(
        self,
        provider: Any,
        registry: ToolRegistry | None = None,
        system_prompt_path: Path | str = DEFAULT_SYSTEM_PROMPT_PATH,
    ) -> None:
        self._provider = provider
        self._registry = registry if registry is not None else default_registry
        self._system_prompt_path = Path(system_prompt_path)

    def system_prompt(self, tier: complexity.Tier = "simple") -> str:
        """The versioned system prompt with {TOOL_CONTRACT} and
        {TIER_GUIDANCE} filled in.

        The file read is cached on (path, mtime); the contract is re-rendered
        per call so a test-registered tool is never stale. ``tier`` selects
        the M3.2 per-tier guidance block (``complexity.TIER_GUIDANCE``) --
        the ONLY part of the rendered prompt that varies by tier within one
        session, so a session that stays on the same tier for its whole turn
        (the common case: tier is classified once per turn, not per
        iteration) still lets providers prompt-cache the rest (M1.1's
        cache_control).
        """
        raw = _read_prompt_file(
            str(self._system_prompt_path), os.path.getmtime(self._system_prompt_path)
        )
        contract = render_contract(registry=self._registry)
        full_contract = (contract + "\n" if contract else "") + _TERMINAL_CONTRACT
        tier_guidance = complexity.TIER_GUIDANCE.get(tier, complexity.TIER_GUIDANCE["simple"])
        filled = raw.replace("{TOOL_CONTRACT}", full_contract)
        return filled.replace("{TIER_GUIDANCE}", tier_guidance)

    def native_tools(self) -> list[dict[str, Any]]:
        """The native tool defs offered to the provider this milestone:
        read-tier registry tools + the two terminals, each requiring
        ``summary``."""
        offered_names = {
            spec.name for spec in self._registry.specs() if spec.tier in _OFFERED_TIERS
        }
        registry_defs = [
            tool_def
            for tool_def in native_tool_defs(registry=self._registry)
            if tool_def["name"] in offered_names
        ]
        return [
            _with_summary_param(tool_def)
            for tool_def in (*registry_defs, *_TERMINAL_TOOL_DEFS)
        ]

    def next_action(self, state: AgentState, language: str) -> PlannerDecision:
        """One decision for the orchestrator's current iteration.

        M3.2: ``tier`` is classified once per turn (preferably read from
        ``state.context["tier"]``, set once by ``orchestrator.run_agent_turn``
        — falling back to a fresh classification keeps this method correct
        even when called against a bare ``AgentState`` that skipped that
        step, e.g. a future direct planner-level test). It selects the
        system prompt's tier guidance, rides along on every returned
        decision's ``meta["tier"]`` (so every "plan" trace record carries
        the classified tier per the M3.2 brief), and gates the
        mandatory-preview diversion below.
        """
        tier: complexity.Tier = state.context.get("tier") or complexity.classify_tier(state.goal)
        messages = [{"role": "user", "content": self._state_block(state, language)}]
        action, raw_meta = self._provider.generate_structured(
            messages, self.native_tools(), schema=None, system=self.system_prompt(tier)
        )
        if action is None:
            return self._rule_fallback(state, language, raw_meta, tier)

        meta = {**(raw_meta or {}), "tier": tier}
        params = dict(action.get("params") or {})
        # summary is planner metadata — strip it BEFORE it could reach
        # toolkit.dispatch's strict validation; truncate defensively.
        summary = str(params.pop("summary", ""))[:SUMMARY_MAX_CHARS]
        tool_name = action.get("tool")

        if tool_name == "respond":
            answer = str(params.get("answer", ""))
            if not answer.strip():
                # An empty/whitespace terminal answer would render as a
                # blank bubble in the UI; treat it the same as a provider
                # failure and fall through to the deterministic ladder.
                return self._rule_fallback(state, language, raw_meta, tier)
            if tier in complexity.MANDATORY_PREVIEW_TIERS and not state.history:
                # M3.2 "mandatory preview": complex/agentic may not conclude
                # the turn as their very first decision -- divert into a
                # grounding read instead (deterministic, no extra provider
                # call, same discipline as the rule-fallback ladder).
                return self._mandatory_preview_decision(tier, language, meta)
            return PlannerDecision(
                kind="respond",
                summary=summary,
                meta=meta,
                params={"answer": answer},
            )
        if tool_name == "ask_user":
            question = str(params.get("question", ""))
            if not question.strip():
                return self._rule_fallback(state, language, raw_meta, tier)
            return PlannerDecision(
                kind="ask_user",
                summary=summary,
                meta=meta,
                params={"question": question},
            )
        return PlannerDecision(
            kind="tool", summary=summary, meta=meta, tool=tool_name, params=params
        )

    def _state_block(self, state: AgentState, language: str) -> str:
        """The per-iteration user message: goal + on-screen selection (P7) +
        compacted recent history.

        Rebuilt every iteration; the system prompt and tool defs stay static
        so provider-side prompt caching covers them.
        """
        lines = [f"Goal: {state.goal}", f"Language: {language}"]
        # P7: what the user clicked, right under the goal — "이 링크" in the
        # goal line is meaningless without it. Context only: it names objects,
        # it never authorizes anything (see the module docstring).
        lines.extend(render_selection_block(state.context.get(SELECTION_CONTEXT_KEY)))
        warnings = state.context.get("warnings") or []
        if warnings:
            lines.append("Warnings: " + "; ".join(str(warning) for warning in warnings))
        recent = state.history[-_HISTORY_K:]
        if recent:
            lines.append(f"Recent steps (last {len(recent)}):")
            for record in recent:
                lines.append(f"- {record.action}: {_observation_snippet(record.observation)}")
        else:
            lines.append("Recent steps: none yet.")
        lines.append("Choose the single next action.")
        return "\n".join(lines)

    def _rule_fallback(
        self,
        state: AgentState,
        language: str,
        provider_meta: dict[str, Any] | None,
        tier: complexity.Tier,
    ) -> PlannerDecision:
        """Ladder rung 3: a deterministic safe decision when the provider's
        own retry ladder gave up — grounding read first, then ask_user."""
        meta = {
            "rule_fallback": True,
            "rung": 3,
            "provider_meta": dict(provider_meta or {}),
            "tier": tier,
        }
        if not state.history:
            return PlannerDecision(
                kind="tool",
                tool=_FALLBACK_GROUNDING_TOOL,
                params={},
                summary=pick_language_text(_FALLBACK_GROUNDING_SUMMARY, language),
                meta=meta,
            )
        return PlannerDecision(
            kind="ask_user",
            params={"question": pick_language_text(_FALLBACK_QUESTION, language)},
            summary=pick_language_text(_FALLBACK_ASK_SUMMARY, language),
            meta=meta,
        )

    def _mandatory_preview_decision(
        self, tier: complexity.Tier, language: str, meta: dict[str, Any]
    ) -> PlannerDecision:
        """M3.2 "mandatory preview" (complex/agentic tiers, see
        ``complexity.MANDATORY_PREVIEW_TIERS``): the planner may not end the
        turn via `respond` as its very first decision. Diverted into the
        same safe grounding read the rule-fallback ladder uses, deterministic
        and provider-call-free — the discarded `respond` was never dispatched
        or shown to the user, so nothing it claimed leaks out ungrounded.
        ``meta`` already carries ``tier`` (set by the caller); this only adds
        the diversion flag so the trace shows WHY this step happened.
        """
        return PlannerDecision(
            kind="tool",
            tool=_FALLBACK_GROUNDING_TOOL,
            params={},
            summary=pick_language_text(_MANDATORY_PREVIEW_SUMMARY, language),
            meta={**meta, "mandatory_preview_forced": True},
        )

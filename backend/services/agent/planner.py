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
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

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

# M1: only read-tier registry tools are offered to the provider. Write/run
# tools join in M2 behind the approval flow (this is the planner-side half of
# the tier gate; orchestrator.py enforces the dispatch-side half).
_OFFERED_TIERS = frozenset({"read"})

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

    def system_prompt(self) -> str:
        """The versioned system prompt with {TOOL_CONTRACT} filled in.

        The file read is cached on (path, mtime); the contract is re-rendered
        per call so a test-registered tool is never stale. Static across a
        session, so providers can prompt-cache it (M1.1's cache_control).
        """
        raw = _read_prompt_file(
            str(self._system_prompt_path), os.path.getmtime(self._system_prompt_path)
        )
        contract = render_contract(registry=self._registry)
        full_contract = (contract + "\n" if contract else "") + _TERMINAL_CONTRACT
        return raw.replace("{TOOL_CONTRACT}", full_contract)

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
        """One decision for the orchestrator's current iteration."""
        messages = [{"role": "user", "content": self._state_block(state, language)}]
        action, meta = self._provider.generate_structured(
            messages, self.native_tools(), schema=None, system=self.system_prompt()
        )
        if action is None:
            return self._rule_fallback(state, language, meta)

        params = dict(action.get("params") or {})
        # summary is planner metadata — strip it BEFORE it could reach
        # toolkit.dispatch's strict validation; truncate defensively.
        summary = str(params.pop("summary", ""))[:SUMMARY_MAX_CHARS]
        tool_name = action.get("tool")

        if tool_name == "respond":
            return PlannerDecision(
                kind="respond",
                summary=summary,
                meta=meta,
                params={"answer": str(params.get("answer", ""))},
            )
        if tool_name == "ask_user":
            return PlannerDecision(
                kind="ask_user",
                summary=summary,
                meta=meta,
                params={"question": str(params.get("question", ""))},
            )
        return PlannerDecision(
            kind="tool", summary=summary, meta=meta, tool=tool_name, params=params
        )

    def _state_block(self, state: AgentState, language: str) -> str:
        """The per-iteration user message: goal + compacted recent history.

        Rebuilt every iteration; the system prompt and tool defs stay static
        so provider-side prompt caching covers them.
        """
        lines = [f"Goal: {state.goal}", f"Language: {language}"]
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
        self, state: AgentState, language: str, provider_meta: dict[str, Any] | None
    ) -> PlannerDecision:
        """Ladder rung 3: a deterministic safe decision when the provider's
        own retry ladder gave up — grounding read first, then ask_user."""
        meta = {
            "rule_fallback": True,
            "rung": 3,
            "provider_meta": dict(provider_meta or {}),
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

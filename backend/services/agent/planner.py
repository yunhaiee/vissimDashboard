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
stays byte-identical. The selection may hold SEVERAL elements (the diagram's
shift+click / shift+drag multi-select): each one gets its own line, because the
case this exists for — an approach whose three vehicle inputs sit on one
coordinate, one per vehicle class — is unanswerable if the block names only one
of them or reports a count.

**Live-demo fix (docs/phase2/DECISIONS.md): history-observation rendering is
recency-weighted and item-boundary-safe.** The old ``_observation_snippet``
applied one flat ~300-char RAW CHARACTER SLICE to every history entry's
``json.dumps`` text, regardless of recency -- against a real Beijing-sized
network this chopped envelopes mid-object (e.g. a 224-row
``get_conflict_areas`` result, ~27.6KB, cut to its first ~3 items with no
signal anything was missing), so the model reported the data as "truncated"
and the planner re-issued the identical call up to the iteration cap trying
to see more of something that was never actually incomplete on the tool
side. ``_render_observation`` fixes both halves: (a) the MOST RECENT history
entry -- the one this iteration's decision is actually made from --
renders under a generous, env-tunable budget (``_recent_observation_budget``,
default 12000 chars via ``AGENT_OBS_RECENT_BUDGET``) while older entries stay
compact; (b) any truncation that IS still needed cuts at an ITEM boundary
(binary search for the largest list anywhere in the structure, truncated to
the item count that fits) with an explicit ``_TRUNCATION_MARKER`` string
appended -- never a raw slice that could land mid-object and produce
unparseable JSON.
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
# block. Live-demo bug (docs/phase2/DECISIONS.md): a flat ~300-char cap
# applied to EVERY entry -- including the one the model must actually answer
# from -- silently chopped Beijing-sized envelopes mid-JSON (e.g. 224
# conflict areas at 27.6KB truncated to the first ~3 items with no signal
# that anything was cut), so the planner told the user the data was
# "truncated" and re-issued the identical call up to the K=6 cap trying to
# get more of it. Fixed as recency-weighted rendering (see
# ``_render_observation``/``_recent_observation_budget`` below): the MOST
# RECENT entry (the one this turn's decision is actually made from) renders
# under a generous, env-tunable budget; older entries stay compact under
# this cap. Both budgets go through the SAME item-boundary-aware truncator
# -- never a raw character slice that can land mid-object.
_HISTORY_K = 6
_OBSERVATION_SNIPPET_CHARS = 300

#: Env var overriding the most-recent history entry's render budget (chars).
_RECENT_OBS_BUDGET_ENV = "AGENT_OBS_RECENT_BUDGET"
#: Default recent-entry budget: measured (backend/tools/agent_obs_size_probe.py)
#: to comfortably fit a real Beijing-sized get_vehicle_inputs envelope (12
#: inputs incl. per-interval volumes[], ~1.9KB) and get_network_overview
#: (~2.9KB) WHOLE, while a 224-row get_conflict_areas envelope (~27.6KB)
#: still needs item-boundary truncation even at this budget -- exactly the
#: two outcomes FIX 1(c) requires.
_DEFAULT_RECENT_OBS_BUDGET = 12000


def _recent_observation_budget() -> int:
    """The most-recent history entry's render budget, read fresh on every
    call (never cached) so ``AGENT_OBS_RECENT_BUDGET`` always takes effect
    immediately -- same pattern as ``services.agent.structured.
    ladder_ceiling_seconds()``. Falls back to
    :data:`_DEFAULT_RECENT_OBS_BUDGET` on anything unset, unparsable, or
    non-positive.
    """
    raw = os.getenv(_RECENT_OBS_BUDGET_ENV, "").strip()
    if not raw:
        return _DEFAULT_RECENT_OBS_BUDGET
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_RECENT_OBS_BUDGET
    return value if value > 0 else _DEFAULT_RECENT_OBS_BUDGET


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

# FIX 1(b): the item-boundary truncation marker (see _render_observation).
# Deliberately a plain string element inside the (still-valid-JSON) list it
# truncates, formatted with {shown}/{total} -- both machine-readable (a
# downstream parser/eval script can regex "shown" vs "total" straight out of
# it) and model-readable (natural language, telling the planner exactly what
# happened and what to do next: narrow the query, never just retry the same
# call hoping for a different truncation point). This replaces the OLD
# behavior of silently chopping raw JSON text mid-object, which is what let
# the model believe the tool itself had failed/truncated and retry the
# identical call up to the K=6 cap (the live-demo bug this fixes).
_TRUNCATION_MARKER = {
    "ko": "[표시 {shown}/{total} — 항목이 잘렸습니다. 특정 항목을 조회하거나 조건을 좁혀 주세요]",
    "en": "[showing {shown}/{total} — list truncated. Look up a specific item or narrow your query]",
    "ja": "[表示 {shown}/{total} — 項目が切り捨てられました。特定の項目を照会するか、条件を絞ってください]",
}

# Last-resort fallback when NO list exists anywhere in the observation (or
# truncating the largest one still doesn't fit the budget, e.g. one huge
# scalar field) -- still never a raw character slice of the JSON text itself,
# just an honest "this didn't fit" notice in place of the whole thing.
_OBSERVATION_TOO_LARGE_MARKER = {
    "ko": "[관측 결과가 너무 커서 표시할 수 없습니다. 더 구체적으로 조회해 주세요]",
    "en": "[the observation was too large to display. Try a more specific lookup]",
    "ja": "[観測結果が大きすぎて表示できません。より具体的に照会してください]",
}

# D2 live-demo fix (docs/phase2/DECISIONS.md, 2026-07-27): a SHORTER marker
# for when the full, explanatory _TRUNCATION_MARKER's own cost is what would
# push a comfortably-fitting item out of budget (see _render_observation's
# hard-floor tier). Keeps the same "표시 {shown}/{total}" substring the full
# marker uses (so any N/M-scraping regex matches either form identically),
# just without the trailing guidance sentence.
_COMPACT_TRUNCATION_MARKER = {
    "ko": "[표시 {shown}/{total} — 잘림]",
    "en": "[{shown}/{total} shown — truncated]",
    "ja": "[表示 {shown}/{total} — 切捨て]",
}

# D2: per-item cap ladder for the "not even one COMPLETE item fits" tier --
# tried largest-first so the summary is as informative as budget allows.
_COMPACT_ITEM_MAX_CHARS: tuple[int, ...] = (60, 30, 12)

# Defect 1 live-demo fix (docs/phase2/DECISIONS.md, 2026-07-31): Tier 5, the
# message-preserving fallback. Every tier above shrinks the ONE dominant LIST
# found anywhere in the envelope; none of them can help when the envelope's
# OWN NON-LIST fields already exceed budget on their own (e.g. two
# independently sizable lists at different paths, so truncating just one
# still doesn't fit; or a bulky non-list dict field). The old code's only
# answer at that point was _OBSERVATION_TOO_LARGE_MARKER, which discards the
# WHOLE envelope -- including a `message` field that a write tool
# (rollback_last_edit, apply_edit_plan -- see tools_edit.py's own write-up)
# builds specifically to be "the compact, self-sufficient outcome summary...
# the field that renders first and survives truncation". The live-demo trace
# this closes: rollback_last_edit executed successfully and its own message
# DID say what happened, but the envelope's other fields pushed the whole
# thing past budget anyway, and the marker discarded that message along with
# everything else -- the model then genuinely could not tell what had
# happened and re-requested the write it had just successfully undone. This
# tier keeps ONLY `status`/`error_type`/`message` (dropping every bulky
# evidence field: artifacts/verification/suggested_fix/data -- those still
# live in the full, on-disk envelope/trace, just not in THIS rendered
# snippet), shrinking the message TEXT itself, never dropping it, if even
# that minimal shape doesn't fit.
_MESSAGE_TIER_KEYS: tuple[str, ...] = ("status", "error_type", "message")


def _message_preserving_fallback(observation: Any, budget: int, language: str) -> str:
    """The renderer's last line of defense before
    :data:`_OBSERVATION_TOO_LARGE_MARKER`: if ``observation`` is envelope-
    shaped (a dict with a non-empty string ``message``), keep that message
    (plus the cheap ``status``/``error_type`` fields) even when every list-
    truncation tier above has failed to fit ``budget``. Falls through to the
    unconditional "too large" marker only when there is no message to
    preserve at all, or budget is too tight to hold even a single truncated
    character of one.
    """
    if isinstance(observation, dict):
        message = observation.get("message")
        if isinstance(message, str) and message:
            minimal = {key: observation[key] for key in _MESSAGE_TIER_KEYS if key in observation}
            candidate = json.dumps(minimal, ensure_ascii=False, separators=(",", ":"))
            if len(candidate) <= budget:
                return candidate
            # Even the minimal {status, error_type, message} shape doesn't
            # fit -- shrink the message TEXT itself (never the surrounding
            # keys) until it does.
            overhead = len(
                json.dumps({**minimal, "message": ""}, ensure_ascii=False, separators=(",", ":"))
            )
            available = budget - overhead
            if available > 1:
                minimal["message"] = message[: available - 1] + "…"
                return json.dumps(minimal, ensure_ascii=False, separators=(",", ":"))
    return pick_language_text(_OBSERVATION_TOO_LARGE_MARKER, language)


def _compact_item_text(item: Any, max_chars: int) -> str:
    """A short, generic one-line summary of a single list item -- D2's
    "not even one full item fits" fallback tier.

    Deliberately generic: never assumes a conflict-area/vehicle-input/etc
    specific shape (this renderer serves EVERY tool's observation). For a
    dict, joins ``key=value`` for its flat (non-dict/non-list) fields; for
    anything else, ``str()``. Always capped to ``max_chars`` -- callers retry
    with a smaller cap until the result fits the remaining budget.
    """
    if isinstance(item, dict):
        parts = [
            f"{key}={value}"
            for key, value in item.items()
            if not isinstance(value, (dict, list))
        ]
        text = ",".join(parts) if parts else "{...}"
    else:
        text = str(item)
    if len(text) > max_chars:
        text = text[: max(max_chars - 1, 1)] + "…"
    return text

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


def _selection_elements(selection: dict[str, Any]) -> list[dict[str, Any]]:
    """Every selected element, plural key first, legacy singular key folded in.

    ``build_selection_context`` emits ``elements`` (two or more objects) OR
    ``element`` (exactly one) and never both, but a selection replayed from an
    older ``pending_approval.json`` carries only the singular key — so both are
    read, de-duplicated on ``(type, id)`` so an object can never be listed
    twice.
    """
    found: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    candidates = [*(selection.get("elements") or []), selection.get("element")]
    for element in candidates:
        if not isinstance(element, dict):
            continue
        key = (element.get("type"), element.get("id"))
        if key in seen:
            continue
        seen.add(key)
        found.append(element)
    return found


def render_selection_block(selection: dict[str, Any] | None) -> list[str]:
    """The state block's lines for ``selection`` (empty list when there is none).

    Shape produced by ``services.network_selection.build_selection_context``:
    ``{"nodes": [...], "links": [...], "element": {...} | "elements": [...],
    "elements_omitted": N, "scenario_ids": [...]}``, every key optional. One
    short line per object — worst case today is 6 links + 6 nodes + 8 elements
    + 1 scenario line, and each line is a handful of tokens, because this whole
    block is re-sent on every loop iteration. Anything unrecognized is skipped
    rather than dumped raw.

    Multi-select is rendered object BY object: three vehicle inputs stacked on
    one approach coordinate (the real client model's shape — one input per
    vehicle class) produce three named lines, never "3 selected" and never just
    the first one. A selection the request boundary capped adds an explicit
    "+N more" line, so the model can say the group was truncated instead of
    reasoning about a silently shortened set.
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
    for element in _selection_elements(selection):
        lines.append(_selection_element_line(element))
    try:
        omitted = int(selection.get("elements_omitted") or 0)
    except (TypeError, ValueError):
        omitted = 0
    if omitted > 0:
        # Stated, never silent — same rationale as the "not found in this
        # network" lines above: the model must be able to tell the user their
        # selection was larger than what it can see.
        lines.append(f"- (+{omitted} more selected objects were not sent: selection limit reached)")
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


def _json_len(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


def _find_largest_list_path(obj: Any) -> tuple[Any, ...] | None:
    """DFS for the path (dict keys / list indices) to the list value, ANYWHERE
    in ``obj``'s nested dict/list structure, with the greatest serialized
    size -- the "dominant" list responsible for a large observation (e.g.
    ``data.conflict_areas``, ``data.vehicle_inputs``; for
    ``get_network_overview``'s nested shape, one category's own
    ``data.<type>.items``). ``None`` when no list exists anywhere.

    A path, not the list object itself, so a caller can navigate a *separate*
    deep copy the same way (see ``_render_observation``) without any
    by-identity matching, which would break the moment two lists happen to
    compare equal.
    """
    best_path: tuple[Any, ...] | None = None
    best_size = -1

    def walk(node: Any, path: tuple[Any, ...]) -> None:
        nonlocal best_path, best_size
        if isinstance(node, list):
            size = _json_len(node)
            if size > best_size:
                best_path, best_size = path, size
            for idx, item in enumerate(node):
                walk(item, path + (idx,))
        elif isinstance(node, dict):
            for key, value in node.items():
                walk(value, path + (key,))

    walk(obj, ())
    return best_path


def _get_at_path(obj: Any, path: tuple[Any, ...]) -> Any:
    for key in path:
        obj = obj[key]
    return obj


def _set_at_path(obj: Any, path: tuple[Any, ...], value: Any) -> None:
    target = obj
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def _render_observation(observation: Any, budget: int, language: str) -> str:
    """Render ``observation`` as compact JSON text that fits within
    ``budget`` chars -- FIX 1(b): item-boundary truncation, never a mid-JSON
    character chop that leaves an unparseable fragment.

    When the full serialization already fits, it is returned byte-identical
    (this is what makes FIX 1(a)'s generous recent-entry budget actually
    matter: a 12-input Beijing ``get_vehicle_inputs`` envelope, ~1.9KB, now
    renders WHOLE instead of being chopped at 300 chars). When it doesn't
    fit, the single largest list anywhere in the structure is truncated at
    an item boundary (binary search for the largest item count that still
    fits) and a trailing marker string (:data:`_TRUNCATION_MARKER`) states
    exactly how many of how many are shown -- still valid JSON (the marker is
    just one more string element in the list), still parseable, and
    unambiguous to the model: this is a deliberate, bounded truncation with a
    next step (narrow the query), not a signal that the tool call itself
    needs to be retried.

    **D2 live-demo fix (docs/phase2/DECISIONS.md, 2026-07-27): the zero-items
    bug.** The binary search above always folds the marker's OWN cost into
    every trial size, including the smallest one. Against a real envelope,
    that marker cost (~45-50 chars) can be exactly what pushes a single,
    otherwise-comfortably-fitting item over a tight budget (measured on the
    live incident: an older-entry 300-char snippet budget, 224-item
    ``get_conflict_areas``, overhead 145 chars, one item 106 chars -- 0 items
    + marker fits at 194 chars, but 1 item + marker is 301, ONE char over —
    so the "largest count that fits" search correctly, but uselessly,
    settles on ZERO real items shown). Worse, when the envelope's own
    non-list overhead is ALREADY close to budget (a write result's
    ``guardrail_warnings`` list sitting next to a compact but non-trivial
    ``verification``/``message`` block), even the marker-only (zero items)
    candidate can miss budget entirely, and the old code fell all the way
    through to :data:`_OBSERVATION_TOO_LARGE_MARKER` -- discarding fields
    (like a write's own ``verification.readback_value``) that have nothing
    to do with the oversized list.

    The fix adds two escalating tiers below the normal binary search, each
    keeping the "N/M 표시" marker accurate for whatever it actually kept:

    1. **Hard floor (>=1 item).** If the binary search settled for zero (or
       found nothing that fit at all), try ONE complete item with a
       progressively cheaper marker -- the full marker, then
       :data:`_COMPACT_TRUNCATION_MARKER`, then no marker at all. The
       marker's cost must never be the reason a single item that otherwise
       fits gets dropped.
    2. **Compact per-item summary.** If not even one COMPLETE item fits
       alongside the envelope overhead, degrade to a short, generic
       ``key=value`` text summary of the first item (:func:`_compact_item_text`,
       shrinking through :data:`_COMPACT_ITEM_MAX_CHARS`) instead of dropping
       the whole observation.

    Only when even a genuinely empty list still doesn't fit (the envelope's
    OTHER fields alone already exceed budget) does this fall to Tier 5 (the
    Defect 1 live-demo fix, docs/phase2/DECISIONS.md, 2026-07-31): preserve
    ``message`` alone, shrunk if necessary, via :func:`_message_preserving_fallback`
    -- so a write tool's own guaranteed-visible outcome summary survives even
    when every list-truncation tier above has failed. Only when there is no
    ``message`` to preserve at all does this fall back to
    :data:`_OBSERVATION_TOO_LARGE_MARKER` -- never a raw slice of the JSON
    text itself.
    """
    try:
        full = json.dumps(observation, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        # Not JSON-serializable at all (never happens for a real dispatch
        # envelope/rule-fallback dict today, but observation is typed Any) --
        # str() it and fall through to the same budget check below.
        full = str(observation)
    if len(full) <= budget:
        return full

    path = _find_largest_list_path(observation)
    if path is None:
        # No list anywhere to truncate (e.g. one pathologically long scalar
        # field) -- never chop the JSON text itself. Tier 5: preserve
        # `message` if there is one before falling back to the generic
        # omission notice.
        return _message_preserving_fallback(observation, budget, language)

    original_list = _get_at_path(observation, path)
    total = len(original_list)
    trial = copy.deepcopy(observation)

    def render(items: list[Any]) -> str | None:
        _set_at_path(trial, path, items)
        candidate = json.dumps(trial, ensure_ascii=False, separators=(",", ":"))
        return candidate if len(candidate) <= budget else None

    def marker(shown: int, *, compact: bool) -> str:
        table = _COMPACT_TRUNCATION_MARKER if compact else _TRUNCATION_MARKER
        return pick_language_text(table, language).format(shown=shown, total=total)

    # Tier 1 (unchanged happy path): binary search for the largest item
    # count that fits WITH the full, explanatory marker.
    lo, hi = 0, total
    best_count, best_rendered = -1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = render(original_list[:mid] + [marker(mid, compact=False)])
        if candidate is not None:
            best_count, best_rendered = mid, candidate
            lo = mid + 1
        else:
            hi = mid - 1
    if best_count >= 1 and best_rendered is not None:
        return best_rendered

    # Tier 2 (D2 hard floor): Tier 1 settled for zero (or nothing fit at
    # all, not even zero items + marker) -- try ONE complete item with a
    # progressively cheaper marker before ever giving up on it.
    if total > 0:
        for candidate_marker in (marker(1, compact=False), marker(1, compact=True), None):
            items = [original_list[0]] + ([candidate_marker] if candidate_marker else [])
            candidate = render(items)
            if candidate is not None:
                return candidate

    # Tier 3 (D2 compact summary): not even one COMPLETE item fits alongside
    # the envelope overhead -- degrade to a short, generic per-item text
    # summary rather than dropping the whole observation, shrinking the
    # summary until something fits.
    if total > 0:
        for max_chars in _COMPACT_ITEM_MAX_CHARS:
            compact_first = _compact_item_text(original_list[0], max_chars)
            for candidate_marker in (marker(1, compact=True), None):
                items = [compact_first] + ([candidate_marker] if candidate_marker else [])
                candidate = render(items)
                if candidate is not None:
                    return candidate

    # Tier 4: even a genuinely EMPTY list fits -- every other envelope field
    # (status/message/verification/etc, which have nothing to do with the
    # oversized list) survives, just without any signal of how much was cut.
    # A truthful partial render beats discarding unrelated fields wholesale.
    empty = render([])
    if empty is not None:
        return empty

    # Tier 5: the bare envelope overhead alone already exceeds budget (e.g.
    # two independently large lists at different paths) -- preserve
    # `message` if there is one rather than discarding it along with
    # everything else.
    return _message_preserving_fallback(observation, budget, language)


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

        FIX 1(a) (docs/phase2/DECISIONS.md, live-demo truncation bug): history
        rendering is recency-weighted, not uniform. ``state.history`` is
        chronological, so the LAST entry in ``recent`` is the most recent
        observation -- the one THIS iteration's decision is actually made
        from -- and renders under the generous
        ``_recent_observation_budget()``; every older entry among the last
        ``_HISTORY_K`` renders compact, under ``_OBSERVATION_SNIPPET_CHARS``.
        Both go through the same item-boundary-aware ``_render_observation``
        -- the old uniform 300-char raw-JSON-text slice (byte-identical for
        every entry regardless of recency) is what silently chopped a real
        Beijing-sized envelope mid-object with no signal it had happened.
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
            most_recent_index = len(recent) - 1
            recent_budget = _recent_observation_budget()
            for index, record in enumerate(recent):
                budget = recent_budget if index == most_recent_index else _OBSERVATION_SNIPPET_CHARS
                rendered = _render_observation(record.observation, budget, language)
                lines.append(f"- {record.action}: {rendered}")
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

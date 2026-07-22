"""Shared logic for the M1.1 structured provider method.

Both ``ClaudeProvider.generate_structured`` (backend/services/claude_provider.py)
and ``LocalQwenProvider.generate_structured`` (backend/services/assistant_router.py)
build on this module: strict-JSON prompt construction for the non-native-tool-use
path, action-envelope validation, and the ``(action, meta)`` fallback-rung
bookkeeping. Keeping all of that here — rather than duplicating it in either
provider file — is the resolution recorded in docs/phase2/DECISIONS.md for the
"assistant_router untouched" vs "give LocalQwenProvider the same method"
conflict: LocalQwenProvider gets only a thin method; every bit of shared logic
lives in this new module instead.

Both providers import this module (and, for the Qwen path,
``services.dashboard_curator.extract_json_object``) lazily inside their
``generate_structured`` method body, matching ``claude_provider.py``'s existing
lazy ``import anthropic`` pattern. This module itself has no import-cycle risk:
``agent/__init__.py`` is empty and this file imports nothing from either
provider module.
"""
from __future__ import annotations

import json
import re
from typing import Any

_JSON_TYPE_CHECKS: dict[str, Any] = {
    "string": lambda value: isinstance(value, str),
    "number": lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "boolean": lambda value: isinstance(value, bool),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
    "null": lambda value: value is None,
}


def _json_type_compatible(value: Any, json_type: Any) -> bool:
    """True if ``value`` is compatible with a JSON-schema ``type`` (str or list of str)."""
    candidates = json_type if isinstance(json_type, list) else [json_type]
    return any(_JSON_TYPE_CHECKS.get(candidate, lambda _v: True)(value) for candidate in candidates)


def validate_action(
    action: Any,
    tools: list[dict[str, Any]],
    schema: dict[str, Any] | None,
) -> str | None:
    """Structural validation of an action envelope.

    Returns ``None`` when ``action`` is valid, else a short human-readable
    rejection reason (used both to build a Claude retry turn and as the final
    ``meta["error"]`` on give-up). Checks, in order:

    - ``action`` is a dict.
    - ``action["tool"]`` is a string naming one of ``tools`` (by ``"name"``).
    - ``action["params"]`` is a dict.
    - if ``schema`` is given: every key in ``schema["required"]`` is present
      at the *top level* of ``action`` (the ACTION envelope, e.g. "tool",
      "params", or any caller-defined envelope field — NOT nested inside
      params), and, where ``schema["properties"][key]["type"]`` is declared,
      the value at that key is JSON-type-compatible with it.

    Deep validation of a tool's own params (matching its real pydantic params
    model) is toolkit.dispatch's job (M0.4) — this function only checks the
    action envelope's own shape and never duplicates that.
    """
    if not isinstance(action, dict):
        return "action must be a JSON object"

    tool_name = action.get("tool")
    if not isinstance(tool_name, str):
        return "action.tool must be a string"

    known_names = {tool.get("name") for tool in tools}
    if tool_name not in known_names:
        return f"action.tool {tool_name!r} does not name a known tool"

    params = action.get("params")
    if not isinstance(params, dict):
        return "action.params must be an object"

    if schema:
        required = schema.get("required") or []
        properties = schema.get("properties") or {}
        for key in required:
            if key not in action:
                return f"action is missing required key {key!r}"
            prop_schema = properties.get(key)
            if isinstance(prop_schema, dict) and "type" in prop_schema:
                if not _json_type_compatible(action[key], prop_schema["type"]):
                    return f"action[{key!r}] does not match expected type {prop_schema['type']!r}"

    return None


# ---------------------------------------------------------------------------
# XML-tool-syntax salvage
#
# Fix for a live-browser-verified defect (docs/phase2/DECISIONS.md, dated
# entry for this section): some models occasionally emit XML-style tool
# syntax instead of a clean native tool_use call. When that happens, the
# API's own salvage can leave one string param's value contaminated with a
# stray closing tag for ITSELF plus a serialized ``<parameter name="X">VALUE``
# fragment meant for a DIFFERENT param — e.g. an "answer" value ending in
# ``...text</answer>\n<parameter name="summary">the real summary`` with
# ``summary`` itself arriving empty. Observed verbatim in
# runtime/agent_sessions/e1dd1bdb76ee/trace_e1dd1bdb76ee.jsonl (plan record,
# iteration 1, fallback_rung 0 — i.e. this reached the user un-retried).
# ---------------------------------------------------------------------------

# `<parameter name="X">VALUE`, VALUE running to the next `</parameter>`, the
# next `<parameter name="...">`, or end of string (re.DOTALL so VALUE may
# span newlines, as the observed case does).
_PARAMETER_TAG_RE = re.compile(
    r'<parameter\s+name="([^"]+)">(.*?)(?=</parameter>|<parameter\s+name="|\Z)',
    re.DOTALL,
)

# A bare trailing `</parameter>` or a dangling, never-closed `<parameter
# name="...">` remnant right at the end of a string. Anchored to the literal
# end (`\Z`, no MULTILINE) so this can never fire on ordinary prose that
# merely contains angle brackets elsewhere (e.g. "<b>bold</b>").
_DANGLING_TAG_RE = re.compile(r'(?:<parameter\s+name="[^"]*">|\s*</parameter>)\s*\Z')


def _tool_input_schema(tool_name: Any, tools: list[dict[str, Any]]) -> dict[str, Any]:
    for tool in tools:
        if tool.get("name") == tool_name:
            return tool.get("input_schema") or {}
    return {}


def salvage_tool_params(action: dict[str, Any], tools: list[dict[str, Any]]) -> dict[str, Any]:
    """Repair XML-tool-syntax contamination in a tool_use action's string params.

    Conservative by construction: a param's value is only ever touched when
    one of the literal artifact markers below is actually present in it —
    plain prose that happens to contain angle brackets (e.g. quoting HTML) is
    returned completely unchanged. For each string param value:

    1. If it contains ``</{param_name}>`` (that param's OWN closing tag),
       truncate the value there, keeping only the clean prefix.
    2. Parse everything after that truncation point for
       ``<parameter name="X">VALUE`` fragments. For each ``X`` that both
       names a declared property of the tool's ``input_schema`` and is
       currently absent or empty (falsy) in ``params``, recover ``VALUE``
       (stripped) into ``params[X]``. A non-empty existing param is never
       overwritten.
    3. Independently, strip a bare trailing ``</parameter>`` or dangling
       ``<parameter name="...">`` remnant from any string param.

    Returns a NEW action dict when anything was salvaged; returns the exact
    same ``action`` object, untouched, when nothing needed it (so callers get
    a true passthrough — no copying, no risk of accidental mutation either
    way, since this function never mutates its input in place).
    """
    params = action.get("params")
    if not isinstance(params, dict):
        return action

    properties = _tool_input_schema(action.get("tool"), tools).get("properties") or {}

    cleaned_params = dict(params)
    recovered: dict[str, str] = {}
    changed = False

    for name, value in params.items():
        if not isinstance(value, str):
            continue

        close_tag = f"</{name}>"
        index = value.find(close_tag)
        if index != -1:
            remainder = value[index + len(close_tag) :]
            value = value[:index]
            changed = True
            for recovered_name, recovered_value in _PARAMETER_TAG_RE.findall(remainder):
                if recovered_name in properties and recovered_name not in recovered:
                    recovered[recovered_name] = recovered_value.strip()

        stripped = _DANGLING_TAG_RE.sub("", value).rstrip()
        if stripped != value:
            value = stripped
            changed = True

        cleaned_params[name] = value

    for name, value in recovered.items():
        if not cleaned_params.get(name):
            cleaned_params[name] = value
            changed = True

    if not changed:
        return action
    return {**action, "params": cleaned_params}


def salvage_and_validate(
    action: Any,
    tools: list[dict[str, Any]],
    schema: dict[str, Any] | None,
) -> tuple[Any, str | None]:
    """Salvage XML-tool-syntax contamination, then validate the result.

    The single shared point both providers' ``generate_structured`` call
    instead of ``validate_action`` directly, so the salvage pass in
    :func:`salvage_tool_params` always runs first without duplicating that
    call at each provider's several call sites (Claude's one call site with
    its retry loop; Qwen's json.loads path and its extract_json_object rescue
    path). Returns ``(cleaned_action, reason)`` — ``reason`` is ``None`` when
    valid, matching :func:`validate_action`'s own convention; the returned
    action is what callers should use downstream either way.
    """
    if not isinstance(action, dict):
        return action, validate_action(action, tools, schema)
    cleaned = salvage_tool_params(action, tools)
    return cleaned, validate_action(cleaned, tools, schema)


def build_strict_json_prompt(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    schema: dict[str, Any] | None,
    system: str | None,
) -> str:
    """Compose one prompt for a strict-JSON, non-native-tool-use model (Qwen).

    Renders system text + a compact tool contract + the conversation turns +
    a hard instruction to reply with ONLY the action JSON object. There is no
    native tool-use on this path, so the entire "protocol" lives in the
    prompt text itself.
    """
    parts: list[str] = []
    if system:
        parts.append(system.strip())

    parts.append("Available tools:")
    for tool in tools:
        name = tool.get("name", "")
        description = tool.get("description", "")
        input_schema = tool.get("input_schema", {})
        parts.append(
            f"- {name}: {description}\n"
            f"  params schema: {json.dumps(input_schema, separators=(',', ':'))}"
        )

    parts.append("Conversation:")
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")
        parts.append(f"[{role}] {content}")

    if schema:
        parts.append(f"Action schema: {json.dumps(schema, separators=(',', ':'))}")

    parts.append(
        "Respond with ONLY a JSON object, no markdown fences, no prose, exactly "
        'this shape: {"tool": "<tool name>", "params": {...}}'
    )
    return "\n".join(parts)


def build_meta(
    *,
    mode: str,
    fallback_rung: int,
    model: str | None = None,
    error: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """The one place both providers build their ``generate_structured`` meta dict.

    Always carries ``mode`` and ``fallback_rung`` (0 = clean native/strict path
    on the first try); ``model``/``error`` are included only when given, so
    successful calls don't grow a stray ``error: None`` key — matching the
    existing ``generate()`` meta convention on both providers.
    """
    meta: dict[str, Any] = {"mode": mode, "fallback_rung": fallback_rung}
    if model is not None:
        meta["model"] = model
    if error is not None:
        meta["error"] = error
    meta.update(extra)
    return meta

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

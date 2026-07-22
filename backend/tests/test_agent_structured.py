"""Tests for M1.1's structured provider method (docs/phase2/02-ROADMAP.md).

Covers both providers' ``generate_structured(messages, tools, schema=None, *,
system=None, max_tokens=None) -> tuple[dict | None, dict]`` contracts plus the
shared ``services.agent.structured`` helpers. See docs/phase2/DECISIONS.md for
the dated entry explaining why LocalQwenProvider's method stays thin while the
shared prompt/validation/meta logic lives in ``services.agent.structured``.

One test (``test_claude_generate_structured_live_smoke``) makes a real Claude
API call and is skipped when no key is available; keep it cheap (tiny prompt,
small max_tokens).
"""
from __future__ import annotations

import json
import os
import types
from typing import Any

import pytest

import config  # noqa: F401 - importing triggers load_dotenv of backend/.env

from services.agent.structured import validate_action
from services.claude_provider import ClaudeProvider

TOOL_A: dict[str, Any] = {
    "name": "get_link_volume",
    "description": "Fetch the current volume for a link.",
    "input_schema": {
        "type": "object",
        "properties": {"link_id": {"type": "string"}},
        "required": ["link_id"],
    },
}
TOOLS: list[dict[str, Any]] = [TOOL_A]


# ---------------------------------------------------------------------------
# Claude-path stubs (lightweight SimpleNamespace content blocks, matching the
# shape of anthropic's ToolUseBlock/TextBlock closely enough for this method's
# own attribute access: .type, .name, .input / .text).
# ---------------------------------------------------------------------------


def _tool_use_block(name: str, params: dict[str, Any], block_id: str = "toolu_1") -> types.SimpleNamespace:
    return types.SimpleNamespace(type="tool_use", id=block_id, name=name, input=params)


def _text_block(text: str) -> types.SimpleNamespace:
    return types.SimpleNamespace(type="text", text=text)


def _response(content: list[Any]) -> types.SimpleNamespace:
    return types.SimpleNamespace(content=content)


class _StubMessages:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _StubClient:
    def __init__(self, responses: list[Any]) -> None:
        self.messages = _StubMessages(responses)


def _provider_with_stub(
    monkeypatch: pytest.MonkeyPatch, responses: list[Any]
) -> tuple[ClaudeProvider, _StubClient]:
    provider = ClaudeProvider()
    monkeypatch.setattr(provider, "_api_key_present", lambda: True)
    stub = _StubClient(responses)
    monkeypatch.setattr(provider, "_get_client", lambda: stub)
    return provider, stub


# ---------------------------------------------------------------------------
# 1. Claude mocked: clean tool_use on first call -> fallback_rung 0
# ---------------------------------------------------------------------------


def test_claude_generate_structured_clean_first_call(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _response([_tool_use_block("get_link_volume", {"link_id": "101"})])
    provider, stub = _provider_with_stub(monkeypatch, [response])

    action, meta = provider.generate_structured(
        messages=[{"role": "user", "content": "check link 101"}],
        tools=TOOLS,
        system="You are a tool-calling planner.",
    )

    assert action == {"tool": "get_link_volume", "params": {"link_id": "101"}}
    assert meta["fallback_rung"] == 0
    assert meta["mode"] == "claude-api"
    assert len(stub.messages.calls) == 1

    call = stub.messages.calls[0]
    assert call["tool_choice"] == {"type": "any"}
    assert call["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert call["system"][0]["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# 2. Claude mocked: malformed then valid -> fallback_rung 1, retry turn present
# ---------------------------------------------------------------------------


def test_claude_generate_structured_retries_once_on_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _response([_text_block("not a tool call")])
    second = _response([_tool_use_block("get_link_volume", {"link_id": "202"})])
    provider, stub = _provider_with_stub(monkeypatch, [first, second])

    action, meta = provider.generate_structured(
        messages=[{"role": "user", "content": "check link 202"}],
        tools=TOOLS,
    )

    assert action == {"tool": "get_link_volume", "params": {"link_id": "202"}}
    assert meta["fallback_rung"] == 1
    assert len(stub.messages.calls) == 2

    retry_messages = stub.messages.calls[1]["messages"]
    assert retry_messages[0] == {"role": "user", "content": "check link 202"}
    assert retry_messages[1]["role"] == "assistant"
    assert retry_messages[2]["role"] == "user"
    assert "invalid" in retry_messages[2]["content"].lower()


# ---------------------------------------------------------------------------
# 3. Claude mocked: malformed twice -> (None, meta), fallback_rung 2
# ---------------------------------------------------------------------------


def test_claude_generate_structured_gives_up_after_one_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _response([_text_block("nope")])
    second = _response([_text_block("still nope")])
    provider, stub = _provider_with_stub(monkeypatch, [first, second])

    action, meta = provider.generate_structured(
        messages=[{"role": "user", "content": "check link 303"}],
        tools=TOOLS,
    )

    assert action is None
    assert meta["fallback_rung"] == 2
    assert meta["error"]
    assert len(stub.messages.calls) == 2


def test_claude_generate_structured_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ClaudeProvider()
    monkeypatch.setattr(provider, "_api_key_present", lambda: False)

    action, meta = provider.generate_structured(messages=[], tools=TOOLS)

    assert action is None
    assert meta["mode"] == "fallback"
    assert meta["fallback_rung"] == 2
    assert meta["error"] == "ANTHROPIC_API_KEY is not set."


# ---------------------------------------------------------------------------
# 4. Qwen path canned (unit test, no worker/subprocess involved)
# ---------------------------------------------------------------------------


def test_qwen_generate_structured_clean_json(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.assistant_router import LocalQwenProvider

    provider = LocalQwenProvider()
    canned = json.dumps({"tool": "get_link_volume", "params": {"link_id": "101"}})
    monkeypatch.setattr(
        provider, "generate", lambda prompt, on_token=None, think=False: (canned, {"mode": "ollama"})
    )

    action, meta = provider.generate_structured(
        messages=[{"role": "user", "content": "hi"}], tools=TOOLS
    )

    assert action == {"tool": "get_link_volume", "params": {"link_id": "101"}}
    assert meta["fallback_rung"] == 0


def test_qwen_generate_structured_prose_wrapped_json(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.assistant_router import LocalQwenProvider

    provider = LocalQwenProvider()
    canned = (
        "Sure, here is the action:\n"
        + json.dumps({"tool": "get_link_volume", "params": {"link_id": "202"}})
        + "\nLet me know if that helps."
    )
    monkeypatch.setattr(
        provider, "generate", lambda prompt, on_token=None, think=False: (canned, {"mode": "ollama"})
    )

    action, meta = provider.generate_structured(
        messages=[{"role": "user", "content": "hi"}], tools=TOOLS
    )

    assert action == {"tool": "get_link_volume", "params": {"link_id": "202"}}
    assert meta["fallback_rung"] == 1


def test_qwen_generate_structured_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.assistant_router import LocalQwenProvider

    provider = LocalQwenProvider()
    monkeypatch.setattr(
        provider, "generate", lambda prompt, on_token=None, think=False: ("not json at all", {"mode": "ollama"})
    )

    action, meta = provider.generate_structured(
        messages=[{"role": "user", "content": "hi"}], tools=TOOLS
    )

    assert action is None
    assert meta["fallback_rung"] == 2
    assert meta["error"]


# ---------------------------------------------------------------------------
# 5. validate_action unit cases
# ---------------------------------------------------------------------------


def test_validate_action_rejects_unknown_tool_name() -> None:
    reason = validate_action({"tool": "nope", "params": {}}, TOOLS, None)
    assert reason is not None


def test_validate_action_rejects_non_dict_params() -> None:
    reason = validate_action({"tool": "get_link_volume", "params": "nope"}, TOOLS, None)
    assert reason is not None


def test_validate_action_rejects_missing_schema_required_key() -> None:
    schema = {
        "required": ["tool", "params", "confidence"],
        "properties": {"confidence": {"type": "number"}},
    }
    action = {"tool": "get_link_volume", "params": {"link_id": "101"}}
    reason = validate_action(action, TOOLS, schema)
    assert reason is not None


def test_validate_action_accepts_valid_action_with_schema() -> None:
    schema = {"required": ["tool", "params"]}
    action = {"tool": "get_link_volume", "params": {"link_id": "101"}}
    assert validate_action(action, TOOLS, schema) is None


# ---------------------------------------------------------------------------
# 6. Live smoke test (real API call, skipped without a key)
# ---------------------------------------------------------------------------

_HAS_LIVE_KEY = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())


@pytest.mark.skipif(not _HAS_LIVE_KEY, reason="ANTHROPIC_API_KEY not set; skipping live Claude smoke test")
def test_claude_generate_structured_live_smoke() -> None:
    provider = ClaudeProvider()
    tool = {
        "name": "ping",
        "description": "Acknowledge the request by calling this tool.",
        "input_schema": {
            "type": "object",
            "properties": {"note": {"type": "string"}},
            "required": [],
        },
    }

    action, meta = provider.generate_structured(
        messages=[{"role": "user", "content": "Call the ping tool now, with no arguments."}],
        tools=[tool],
        max_tokens=300,
    )

    assert action is not None
    assert action["tool"] == "ping"
    assert meta["fallback_rung"] in (0, 1)

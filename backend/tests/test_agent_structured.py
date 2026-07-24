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
import logging
import os
import time
import types
from typing import Any

import pytest

import config  # noqa: F401 - importing triggers load_dotenv of backend/.env

from services import claude_provider as claude_provider_module
from services.agent import structured as structured_module
from services.agent.structured import salvage_tool_params, validate_action
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

# The real M1.3 planner-terminal ``respond`` tool shape (planner.py's
# _with_summary_param augmentation) — used by the salvage-pass tests below,
# which reproduce the exact live defect surfaced against this tool.
RESPOND_TOOL: dict[str, Any] = {
    "name": "respond",
    "description": "Deliver the final answer to the user and end the turn.",
    "input_schema": {
        "type": "object",
        "properties": {
            "answer": {"type": "string"},
            "summary": {"type": "string", "maxLength": 80},
        },
        "required": ["answer", "summary"],
    },
}


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
    """Pops one queued item per ``create()`` call. An item that is a
    ``BaseException`` instance is RAISED (simulating a transport/timeout
    failure); anything else is returned (the existing canned-response
    convention)."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


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
# 2b. M2.3 (docs/phase2/DECISIONS.md, BLOCKING finding): when the REJECTED
# response DID contain a tool_use block, the retry turn must pair a
# tool_result block against that exact id, not a plain-text echo -- the
# plain-text shape 400s once the Messages API enforces tool_result pairing
# for a preceding assistant tool_use turn.
# ---------------------------------------------------------------------------


def test_claude_generate_structured_retry_uses_tool_result_block_when_tool_use_was_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The first response's tool_use block names a tool that isn't in TOOLS
    # -- validate_action rejects it ("does not name a known tool") while
    # tool_use_block is still non-None, which is exactly the case the fix
    # targets (as opposed to test #2 above, where the first response has NO
    # tool_use block at all and the plain-text retry shape remains correct).
    first = _response(
        [_tool_use_block("totally_made_up_tool", {"x": 1}, block_id="toolu_abc123")]
    )
    second = _response([_tool_use_block("get_link_volume", {"link_id": "303"})])
    provider, stub = _provider_with_stub(monkeypatch, [first, second])

    action, meta = provider.generate_structured(
        messages=[{"role": "user", "content": "check link 303"}],
        tools=TOOLS,
    )

    assert action == {"tool": "get_link_volume", "params": {"link_id": "303"}}
    assert meta["fallback_rung"] == 1
    assert len(stub.messages.calls) == 2

    retry_messages = stub.messages.calls[1]["messages"]
    assert retry_messages[0] == {"role": "user", "content": "check link 303"}
    # The assistant turn echoes the ORIGINAL content (including its
    # tool_use block) verbatim -- required for the tool_result pairing to
    # even make sense to the API.
    assert retry_messages[1] == {"role": "assistant", "content": first.content}

    retry_user_turn = retry_messages[2]
    assert retry_user_turn["role"] == "user"
    assert isinstance(retry_user_turn["content"], list)
    assert len(retry_user_turn["content"]) == 1

    tool_result_block = retry_user_turn["content"][0]
    assert tool_result_block["type"] == "tool_result"
    assert tool_result_block["tool_use_id"] == "toolu_abc123"
    assert tool_result_block["is_error"] is True
    assert "invalid" in tool_result_block["content"].lower()
    assert "totally_made_up_tool" in tool_result_block["content"]


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


# ---------------------------------------------------------------------------
# 3b. Live-demo resilience fix (2026-07-24): per-attempt logging + a hard
# wall-clock ceiling across the whole rung ladder, so a stalled/timing-out
# provider call can never silently walk for minutes with zero log output.
# See docs/phase2/DECISIONS.md for the dated entry and
# services/claude_provider.py's module docstring for the incident.
# ---------------------------------------------------------------------------

_CLAUDE_PROVIDER_LOGGER = "services.claude_provider"


def test_claude_generate_structured_fails_once_then_succeeds_logs_warning_then_info(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A transport-level exception (e.g. a timed-out request) on rung 0,
    followed by a clean tool_use on rung 1: the ladder recovers (unlike the
    pre-fix code, which gave up immediately on ANY exception, never trying a
    second rung), logs one WARNING for the failed attempt and one INFO for
    the recovery, and returns the normal answer -- not a hang, not silence.
    """
    second = _response([_tool_use_block("get_link_volume", {"link_id": "404"})])
    provider, stub = _provider_with_stub(monkeypatch, [TimeoutError("request timed out"), second])

    with caplog.at_level(logging.INFO, logger=_CLAUDE_PROVIDER_LOGGER):
        action, meta = provider.generate_structured(
            messages=[{"role": "user", "content": "check link 404"}],
            tools=TOOLS,
        )

    assert action == {"tool": "get_link_volume", "params": {"link_id": "404"}}
    assert meta["fallback_rung"] == 1
    assert len(stub.messages.calls) == 2

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(warnings) == 1
    assert "rung=0" in warnings[0].message
    assert "TimeoutError" in warnings[0].message
    assert len(infos) == 1
    assert "recovered" in infos[0].message
    assert "rung=1" in infos[0].message


def test_claude_generate_structured_every_call_times_out_gives_up_without_hanging(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A client whose every call raises TimeoutError: the ladder still
    concludes (both rungs attempted, per the existing 2-rung shape) with a
    structured failure the planner's rule-fallback ladder turns into a
    visible answer -- never an unbounded/silent walk. Each attempt logs a
    WARNING (server-visible even though httpx itself never logs a timeout)."""
    provider, stub = _provider_with_stub(
        monkeypatch, [TimeoutError("request timed out"), TimeoutError("request timed out again")]
    )

    with caplog.at_level(logging.WARNING, logger=_CLAUDE_PROVIDER_LOGGER):
        action, meta = provider.generate_structured(
            messages=[{"role": "user", "content": "check link 505"}],
            tools=TOOLS,
        )

    assert action is None
    assert meta["fallback_rung"] == 2
    assert "request timed out again" in meta["error"]
    assert len(stub.messages.calls) == 2  # both rungs attempted -- no silent extra walk

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert "rung=0" in warnings[0].message and "TimeoutError" in warnings[0].message
    assert "rung=1" in warnings[1].message and "TimeoutError" in warnings[1].message


def test_claude_generate_structured_ceiling_stops_the_walk_before_a_further_call(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """AGENT_PROVIDER_LADDER_CEILING_S is read fresh on every call (env
    override respected) and enforced BETWEEN attempts: once the elapsed
    budget is used up, the ladder logs one ERROR line and returns the
    failure immediately instead of starting another bounded-but-lengthy
    call. A deterministic fake clock (not real sleeps) drives the elapsed
    time so this test is fast and reproducible."""
    monkeypatch.setenv("AGENT_PROVIDER_LADDER_CEILING_S", "0.15")

    # Advances by 0.1s on every time.monotonic() call. Sequence for this
    # code path: ladder_start=0.0, elapsed_before(rung0)=0.1s (< 0.15,
    # proceeds), attempt_start=0.2s, the raise, attempt_elapsed=0.1s ->
    # WARNING logged, continue to rung=1; elapsed_before(rung1)=0.4s (0.4 -
    # 0.0) >= ceiling(0.15s) -> ceiling trips, NO second client call is made.
    counter = {"t": -0.1}

    def fake_monotonic() -> float:
        counter["t"] += 0.1
        return counter["t"]

    monkeypatch.setattr(claude_provider_module.time, "monotonic", fake_monotonic)

    provider, stub = _provider_with_stub(monkeypatch, [TimeoutError("slow request")])

    with caplog.at_level(logging.WARNING, logger=_CLAUDE_PROVIDER_LOGGER):
        action, meta = provider.generate_structured(
            messages=[{"role": "user", "content": "check link 606"}],
            tools=TOOLS,
        )

    assert action is None
    assert meta["fallback_rung"] == 2
    assert "ceiling" in meta["error"].lower()
    assert len(stub.messages.calls) == 1  # the walk stopped -- rung 1 never attempted

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "ceiling" in errors[0].message.lower()
    assert "rung=1" in errors[0].message


def test_ladder_ceiling_seconds_env_override_and_safe_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``ladder_ceiling_seconds`` (services.agent.structured) is the ONE
    shared helper both providers' ladders read -- exercised here once rather
    than duplicated per provider."""
    monkeypatch.delenv("AGENT_PROVIDER_LADDER_CEILING_S", raising=False)
    assert (
        structured_module.ladder_ceiling_seconds()
        == structured_module.DEFAULT_LADDER_CEILING_S
    )

    monkeypatch.setenv("AGENT_PROVIDER_LADDER_CEILING_S", "12.5")
    assert structured_module.ladder_ceiling_seconds() == 12.5

    # Unparsable or non-positive overrides must never disable the ceiling.
    monkeypatch.setenv("AGENT_PROVIDER_LADDER_CEILING_S", "not-a-number")
    assert (
        structured_module.ladder_ceiling_seconds()
        == structured_module.DEFAULT_LADDER_CEILING_S
    )
    monkeypatch.setenv("AGENT_PROVIDER_LADDER_CEILING_S", "-5")
    assert (
        structured_module.ladder_ceiling_seconds()
        == structured_module.DEFAULT_LADDER_CEILING_S
    )


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
# 4b. Qwen mirror: generate()'s own stdio-worker attempts loop is a second
# bounded-but-lengthy ladder generate_structured feeds through (see
# services/assistant_router.py's docstring above the loop and
# docs/phase2/DECISIONS.md). Exercised directly against ``generate()``
# (generate_structured always calls it exactly once and would otherwise
# never surface this loop's own retry behavior).
# ---------------------------------------------------------------------------


def _worker_ready_provider(monkeypatch: pytest.MonkeyPatch):
    from services.assistant_router import LocalQwenProvider

    provider = LocalQwenProvider()
    # Bypass the two guards that keep this branch inert under pytest by
    # design (PYTEST_CURRENT_TEST + real model/python paths) -- this test
    # exercises ONLY the attempts-loop bookkeeping, never a real subprocess.
    monkeypatch.setattr(provider, "_worker_auto_start_enabled", lambda: True)
    monkeypatch.setattr(type(provider.model_dir), "exists", lambda self: True)
    monkeypatch.setattr(type(provider.python_path), "exists", lambda self: True)
    monkeypatch.setattr(provider, "_restart_worker_after_failure", lambda: None)
    return provider


def test_qwen_generate_worker_ladder_fails_once_then_succeeds_logs_warning_then_info(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    provider = _worker_ready_provider(monkeypatch)
    calls: list[int] = []

    def fake_generate_with_worker(prompt: str, on_token: Any = None) -> str:
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("worker timed out")
        return "final answer text"

    monkeypatch.setattr(provider, "_generate_with_worker", fake_generate_with_worker)

    with caplog.at_level(logging.INFO, logger="services.assistant_router"):
        answer, meta = provider.generate("hi")

    assert answer == "final answer text"
    assert meta == {"mode": "stdio-worker", "attempts": 2}
    assert len(calls) == 2

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(warnings) == 1
    assert "attempt=1/2" in warnings[0].message
    assert "TimeoutError" in warnings[0].message
    assert len(infos) == 1
    assert "recovered" in infos[0].message
    assert "attempt=2/2" in infos[0].message


def test_qwen_generate_worker_ladder_every_attempt_fails_gives_up_without_hanging(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    provider = _worker_ready_provider(monkeypatch)

    def always_times_out(prompt: str, on_token: Any = None) -> str:
        raise TimeoutError("worker timed out")

    monkeypatch.setattr(provider, "_generate_with_worker", always_times_out)

    with caplog.at_level(logging.WARNING, logger="services.assistant_router"):
        answer, meta = provider.generate("hi")

    assert answer is None
    assert meta["mode"] == "fallback"
    assert "worker timed out" in meta["error"]
    assert meta["attempts"] == 2

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert "attempt=1/2" in warnings[0].message
    assert "attempt=2/2" in warnings[1].message


def test_qwen_generate_worker_ladder_ceiling_stops_the_walk_before_a_further_attempt(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    provider = _worker_ready_provider(monkeypatch)
    monkeypatch.setenv("AGENT_PROVIDER_LADDER_CEILING_S", "0.15")

    counter = {"t": -0.1}

    def fake_monotonic() -> float:
        counter["t"] += 0.1
        return counter["t"]

    monkeypatch.setattr(time, "monotonic", fake_monotonic)

    def always_times_out(prompt: str, on_token: Any = None) -> str:
        raise TimeoutError("worker timed out")

    monkeypatch.setattr(provider, "_generate_with_worker", always_times_out)

    with caplog.at_level(logging.WARNING, logger="services.assistant_router"):
        answer, meta = provider.generate("hi")

    assert answer is None
    assert meta["mode"] == "fallback"
    assert "ceiling" in meta["error"].lower()

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "ceiling" in errors[0].message.lower()


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
# XML-tool-syntax salvage pass (fix for the M1.4 live-browser defect: a
# contaminated native tool_use ``answer`` param arriving with a stray
# ``</answer>`` tag followed by a serialized ``<parameter name="summary">...``
# fragment for a DIFFERENT param — see docs/phase2/DECISIONS.md dated entry).
# Cases (a)-(d) exercise ``salvage_tool_params`` directly; (e) below (Qwen)
# and the Claude-path test verify both providers route through it.
# ---------------------------------------------------------------------------

# Exact strings from runtime/agent_sessions/e1dd1bdb76ee/trace_e1dd1bdb76ee.jsonl
# (plan record, iteration 1) that surfaced this bug.
_OBSERVED_CONTAMINATED_ANSWER = (
    "현재 네트워크 개요는 다음과 같습니다.\n\n"
    "- **링크(Link): 14개** — 예: Main St EB approach(101), Main St WB exit(102), "
    "1st Ave SB approach(103), 1st Ave NB exit(104) 등\n\n"
    "전체 데이터가 일부만 표시되어 노드, 차량 입력(vehicle input), 신호(signal) 등 "
    "나머지 항목은 아직 확인하지 못했습니다. 필요하시면 차량 입력 목록이나 신호 컨트롤러 "
    "목록을 추가로 조회해 드릴 수 있습니다.</answer>\n"
    '<parameter name="summary">네트워크 개요 결과를 정리해 답변'
)
_OBSERVED_CLEAN_ANSWER_SUFFIX = "조회해 드릴 수 있습니다."
_OBSERVED_RECOVERED_SUMMARY = "네트워크 개요 결과를 정리해 답변"


def test_salvage_tool_params_recovers_real_observed_contamination() -> None:
    # (a) the EXACT observed contamination -> answer cleaned, summary recovered.
    action = {
        "tool": "respond",
        "params": {"answer": _OBSERVED_CONTAMINATED_ANSWER, "summary": ""},
    }

    cleaned = salvage_tool_params(action, [RESPOND_TOOL])

    assert "</answer>" not in cleaned["params"]["answer"]
    assert "<parameter" not in cleaned["params"]["answer"]
    assert cleaned["params"]["answer"].endswith(_OBSERVED_CLEAN_ANSWER_SUFFIX)
    assert cleaned["params"]["summary"] == _OBSERVED_RECOVERED_SUMMARY


def test_salvage_tool_params_never_overwrites_non_empty_existing_param() -> None:
    # (b) contamination alongside an already-populated target param -> not
    # overwritten, but the contaminated param is still cleaned.
    answer = 'Some clean answer text.</answer>\n<parameter name="summary">recovered summary text'
    action = {
        "tool": "respond",
        "params": {"answer": answer, "summary": "already set summary"},
    }

    cleaned = salvage_tool_params(action, [RESPOND_TOOL])

    assert cleaned["params"]["summary"] == "already set summary"
    assert cleaned["params"]["answer"] == "Some clean answer text."


def test_salvage_tool_params_passthrough_when_clean() -> None:
    # (c) clean params -> byte-identical passthrough (same object, untouched).
    action = {
        "tool": "respond",
        "params": {"answer": "A perfectly clean answer.", "summary": "ok"},
    }

    cleaned = salvage_tool_params(action, [RESPOND_TOOL])

    assert cleaned is action


def test_salvage_tool_params_leaves_benign_angle_brackets_untouched() -> None:
    # (d) prose with benign angle brackets that don't match the artifact
    # markers must never be altered.
    action = {
        "tool": "respond",
        "params": {"answer": "Use <b>bold</b> tags here.", "summary": "ok"},
    }

    cleaned = salvage_tool_params(action, [RESPOND_TOOL])

    assert cleaned is action
    assert cleaned["params"]["answer"] == "Use <b>bold</b> tags here."


# ---------------------------------------------------------------------------
# Reviewer stress-test regressions: tail-anchored detection (rfind + full
# remainder decomposition, not first-occurrence `find`) and rstrip-only-on-
# actual-change (byte-identical passthrough even with trailing whitespace).
# ---------------------------------------------------------------------------


def test_salvage_tool_params_multi_occurrence_keeps_prefix_and_recovers_tail() -> None:
    # IMPORTANT-1 repro: a legitimate mid-sentence mention of the closing tag
    # substring, PLUS the real tail artifact. Only the tail artifact must be
    # stripped; the mid-sentence mention and all the real prose around it
    # must survive untouched, and the tail's summary must still be recovered.
    answer = (
        "Quoting: the tag looks like <foo></answer> mid-sentence. "
        'Real content continues here.</answer>\n<parameter name="summary">the real summary'
    )
    action = {"tool": "respond", "params": {"answer": answer, "summary": ""}}

    cleaned = salvage_tool_params(action, [RESPOND_TOOL])

    assert "<foo></answer> mid-sentence." in cleaned["params"]["answer"]
    assert "Real content continues here." in cleaned["params"]["answer"]
    assert cleaned["params"]["answer"].endswith("Real content continues here.")
    assert "<parameter" not in cleaned["params"]["answer"]
    assert cleaned["params"]["summary"] == "the real summary"


def test_salvage_tool_params_bare_substring_with_nothing_after_is_untouched() -> None:
    # IMPORTANT-2 repro: a single occurrence of the closing-tag substring with
    # NOTHING real after it (empty remainder) is not sufficient evidence of
    # contamination on its own -> byte-identical passthrough.
    action = {
        "tool": "respond",
        "params": {
            "answer": "Some legit text discussing the tag: <answer>foo</answer>",
            "summary": "ok",
        },
    }

    cleaned = salvage_tool_params(action, [RESPOND_TOOL])

    assert cleaned is action


def test_salvage_tool_params_trailing_whitespace_only_is_untouched() -> None:
    # MINOR-1 repro: a clean value with ordinary trailing whitespace and no
    # artifact markers at all must come back as the identical object -- the
    # dangling-tag rstrip must never fire when its regex found no match.
    action = {
        "tool": "respond",
        "params": {"answer": "A perfectly clean answer.   \n", "summary": "ok"},
    }

    cleaned = salvage_tool_params(action, [RESPOND_TOOL])

    assert cleaned is action


def test_claude_generate_structured_salvages_contaminated_tool_use(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: the real defect happened via the Claude path at rung 0."""
    response = _response(
        [
            _tool_use_block(
                "respond",
                {"answer": _OBSERVED_CONTAMINATED_ANSWER, "summary": ""},
            )
        ]
    )
    provider, stub = _provider_with_stub(monkeypatch, [response])

    action, meta = provider.generate_structured(
        messages=[{"role": "user", "content": "네트워크 개요 알려줘"}],
        tools=[RESPOND_TOOL],
    )

    assert meta["fallback_rung"] == 0
    assert "</answer>" not in action["params"]["answer"]
    assert action["params"]["answer"].endswith(_OBSERVED_CLEAN_ANSWER_SUFFIX)
    assert action["params"]["summary"] == _OBSERVED_RECOVERED_SUMMARY
    assert len(stub.messages.calls) == 1  # salvage succeeded before validation; no retry needed


def test_qwen_generate_structured_routes_through_salvage(monkeypatch: pytest.MonkeyPatch) -> None:
    # (e) the Qwen strict-JSON path also routes through the salvage pass.
    from services.assistant_router import LocalQwenProvider

    provider = LocalQwenProvider()
    answer = 'Clean text.</answer>\n<parameter name="summary">recovered'
    canned = json.dumps({"tool": "respond", "params": {"answer": answer, "summary": ""}})
    monkeypatch.setattr(
        provider, "generate", lambda prompt, on_token=None, think=False: (canned, {"mode": "ollama"})
    )

    action, meta = provider.generate_structured(
        messages=[{"role": "user", "content": "hi"}], tools=[RESPOND_TOOL]
    )

    assert meta["fallback_rung"] == 0
    assert action["params"]["answer"] == "Clean text."
    assert action["params"]["summary"] == "recovered"


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

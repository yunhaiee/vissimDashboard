"""Endpoint tests for M1.4: ``POST /api/assistant/agent/query/stream``.

FastAPI ``TestClient`` against the real app, with ``_default_provider``
monkeypatched to a canned-queue fake provider — deterministic, no paid API
calls. The fake replicates ``test_agent_orchestrator.py``'s
``FakeStructuredProvider`` (kept local rather than cross-imported so this
file stays a self-contained endpoint test).
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from routers import assistant as assistant_endpoint
from services.agent.tools_metrics import _looks_like_path


class FakeStructuredProvider:
    """Canned (action, meta) queue implementing the ``generate_structured``
    seam — see test_agent_orchestrator.py's identical fixture for the full
    rationale."""

    def __init__(self, queue: list[tuple[dict[str, Any] | None, dict[str, Any]]]) -> None:
        self._queue = list(queue)

    def generate_structured(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if not self._queue:
            raise AssertionError("FakeStructuredProvider queue exhausted unexpectedly")
        return self._queue.pop(0)


def _action(
    tool_name: str, *, summary: str = "확인합니다", **params: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "fake", "fallback_rung": 0}


def _assert_no_path_like_strings(value: Any) -> None:
    """Recursively assert no string anywhere in ``value`` looks like a
    filesystem path — reuses tools_metrics.py's own path-detection heuristic
    (``_looks_like_path``) rather than inventing a second definition."""
    if isinstance(value, dict):
        for v in value.values():
            _assert_no_path_like_strings(v)
    elif isinstance(value, list):
        for item in value:
            _assert_no_path_like_strings(item)
    elif isinstance(value, str):
        assert not _looks_like_path(value), f"path-like string leaked into a stream event: {value!r}"


def _stream_events(response) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


@pytest.fixture()
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def test_overview_question_streams_status_step_chunk_done_in_order(client, monkeypatch):
    provider = FakeStructuredProvider(
        [
            _action("get_network_overview", summary="네트워크 개요를 확인합니다"),
            _action(
                "respond",
                summary="확인한 내용을 정리했습니다",
                answer="이 네트워크에는 링크 14개, 노드 2개가 있습니다.",
            ),
        ]
    )
    monkeypatch.setattr(assistant_endpoint, "_default_provider", lambda: provider)

    response = client.post(
        "/api/assistant/agent/query/stream",
        json={"message": "이 네트워크에 뭐가 있어?", "response_language": "ko"},
    )

    assert response.status_code == 200
    events = _stream_events(response)
    types = [event["type"] for event in events]

    # Event ORDER: status -> step+ -> chunk+ -> done.
    assert types[0] == "status"
    assert types[-1] == "done"
    first_step_at = types.index("step")
    first_chunk_at = types.index("chunk")
    assert first_step_at < first_chunk_at
    assert types[1:first_chunk_at] == ["step", "step"]  # exactly 2 steps, no chunk in between

    step_events = [event for event in events if event["type"] == "step"]
    assert [event["tool"] for event in step_events] == ["get_network_overview", "respond"]
    assert all(event["summary"] for event in step_events)  # step summaries present
    assert [event["index"] for event in step_events] == [0, 1]

    done_event = events[-1]
    assert done_event["response"]["kind"] == "answer"
    assert done_event["response"]["steps"]
    assert done_event["response"]["session_id"]

    for event in events:
        _assert_no_path_like_strings(event)


def test_clarification_flow_yields_done_kind_clarification(client, monkeypatch):
    provider = FakeStructuredProvider(
        [
            _action(
                "ask_user",
                summary="요청의 의도를 확인합니다",
                question="어떤 네트워크 객체나 지표를 확인하고 싶으신가요?",
            ),
        ]
    )
    monkeypatch.setattr(assistant_endpoint, "_default_provider", lambda: provider)

    response = client.post(
        "/api/assistant/agent/query/stream",
        json={"message": "바나나 우주선을 발사해줘", "response_language": "ko"},
    )

    assert response.status_code == 200
    events = _stream_events(response)
    done_event = events[-1]
    assert done_event["type"] == "done"
    assert done_event["response"]["kind"] == "clarification"
    assert "어떤 네트워크 객체" in done_event["response"]["answer"]

    for event in events:
        _assert_no_path_like_strings(event)


def test_empty_message_returns_400(client):
    response = client.post("/api/assistant/agent/query/stream", json={"message": "   "})
    assert response.status_code == 400

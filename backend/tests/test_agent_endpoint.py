"""Endpoint tests for M1.4: ``POST /api/assistant/agent/query/stream``.

FastAPI ``TestClient`` against the real app, with ``_default_provider``
monkeypatched to a canned-queue fake provider — deterministic, no paid API
calls. The fake replicates ``test_agent_orchestrator.py``'s
``FakeStructuredProvider`` (kept local rather than cross-imported so this
file stays a self-contained endpoint test).

M2.3: the endpoint now resolves its working network via
``services.datasets.get_active_inpx_path()`` (an uploaded dataset, when
present, takes priority over ``data/practice.inpx``) rather than
``config.SIMULATION_INPX_PATH`` directly — the ``_pin_active_dataset_to_demo``
autouse fixture below pins it to the demo network for every test in this
file, so a stray uploaded dataset left on a dev machine (or CI runner) from
an unrelated manual session can never make these tests non-deterministic.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from routers import assistant as assistant_endpoint
from services.agent.tools_metrics import _looks_like_path

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"


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


class RaisingAfterOneStepProvider:
    """First ``generate_structured`` call returns a canned tool action (so one
    real step streams and dispatches); the second call raises — the realistic
    trigger for the router's exception branch, since a tool failure is caught
    and reported internally by ``toolkit.dispatch`` while a *planner* call
    raising is not caught anywhere before the router's own worker-thread
    try/except."""

    def __init__(self, action: tuple[dict[str, Any], dict[str, Any]]) -> None:
        self._action = action
        self.calls = 0

    def generate_structured(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        self.calls += 1
        if self.calls == 1:
            return self._action
        raise RuntimeError("forced planner failure after one streamed step")


class SlowThenRespondProvider:
    """Sleeps synchronously on its first ``generate_structured`` call before
    returning a canned action -- simulating a long-running turn (a slow
    planner call and a slow tool dispatch look identical to the router's
    queue-drain loop: both leave ``step_queue`` empty for the delay's
    duration, see ``_stream_agent_turn``'s heartbeat comment). Used to prove
    the ``ping`` heartbeat fires while the worker thread is busy and
    otherwise-silent, the regression this test guards
    (docs/phase2/DECISIONS.md, 2026-07-24 heartbeat entry)."""

    def __init__(self, delay_seconds: float, action: tuple[dict[str, Any], dict[str, Any]]) -> None:
        self._delay_seconds = delay_seconds
        self._action = action
        self.calls = 0

    def generate_structured(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        self.calls += 1
        if self.calls == 1:
            time.sleep(self._delay_seconds)
        return self._action


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


@pytest.fixture(autouse=True)
def _pin_active_dataset_to_demo(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(assistant_endpoint, "get_active_inpx_path", lambda: DEMO_INPX)


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


def test_ping_events_stream_while_worker_is_slow(client, monkeypatch):
    """A long-running tool call (e.g. a 2+ minute simulation) must never
    leave the NDJSON stream silent for longer than
    AGENT_STREAM_PING_INTERVAL_S -- an idle NDJSON connection that long is
    exactly what an intermediary (observed: the Next.js dev proxy) kills,
    which is the root cause of the "answer never arrives" live bug (see
    docs/phase2/DECISIONS.md's 2026-07-24 heartbeat entry). The interval is
    shrunk via the env var so this stays fast."""
    monkeypatch.setenv("AGENT_STREAM_PING_INTERVAL_S", "0.05")
    provider = SlowThenRespondProvider(
        delay_seconds=0.35,
        action=_action("respond", summary="확인했습니다", answer="완료했습니다."),
    )
    monkeypatch.setattr(assistant_endpoint, "_default_provider", lambda: provider)

    response = client.post(
        "/api/assistant/agent/query/stream",
        json={"message": "상태를 요약해줘", "response_language": "ko"},
    )

    assert response.status_code == 200
    events = _stream_events(response)

    ping_events = [event for event in events if event["type"] == "ping"]
    assert len(ping_events) >= 2  # 0.35s delay / 0.05s interval ~= 7 pings
    for event in ping_events:
        assert event["stream_id"]

    # ping is a pure heartbeat: it never disturbs the existing
    # status -> step+ -> chunk+ -> done ordering other tests pin.
    non_ping_types = [event["type"] for event in events if event["type"] != "ping"]
    assert non_ping_types[0] == "status"
    assert non_ping_types[-1] == "done"
    assert events[-1]["type"] == "done"

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


def test_exception_after_one_step_preserves_streamed_narration(client, monkeypatch):
    """Regression for the reviewer's IMPORTANT-1 finding: a planner call that
    raises after 1+ steps already streamed must not wipe those steps from
    done.response.steps."""
    provider = RaisingAfterOneStepProvider(
        _action("get_network_overview", summary="네트워크 개요를 확인합니다")
    )
    monkeypatch.setattr(assistant_endpoint, "_default_provider", lambda: provider)

    response = client.post(
        "/api/assistant/agent/query/stream",
        json={"message": "이 네트워크에 뭐가 있어?", "response_language": "ko"},
    )

    assert response.status_code == 200
    events = _stream_events(response)
    types = [event["type"] for event in events]
    assert types[0] == "status"
    assert types[-1] == "done"

    # The one real step (dispatched against the real demo network before the
    # second planner call raised) streamed as a `step` event...
    step_events = [event for event in events if event["type"] == "step"]
    assert len(step_events) == 1
    assert step_events[0]["tool"] == "get_network_overview"
    assert step_events[0]["summary"] == "네트워크 개요를 확인합니다"
    assert step_events[0]["status"] == "ok"

    # ...and it must still be there in `done`, not wiped to [].
    done_event = events[-1]
    response_payload = done_event["response"]
    assert response_payload["steps"] == [
        {"summary": "네트워크 개요를 확인합니다", "tool": "get_network_overview", "status": "ok"}
    ]
    assert response_payload["kind"] == "answer"
    assert response_payload["answer"]  # apologetic language-appropriate fallback, not empty

    # No raw exception text anywhere in any event.
    for event in events:
        _assert_no_path_like_strings(event)
        serialized = json.dumps(event)
        assert "RuntimeError" not in serialized
        assert "forced planner failure" not in serialized

"""Endpoint tests for M2.4: propose -> approve for a RUN-tier action across
``POST /api/assistant/agent/query/stream`` and ``POST /api/assistant/agent/
approve``.

Mirrors test_agent_approval_endpoint.py's ``TestClient`` +
``_default_provider`` monkeypatch pattern (kept local per that file's own
stated convention), but injects a fake ``services.agent.tools_run.
_run_job_and_wait`` instead of a ``FakeEditSession`` — a run never touches
COM through this endpoint at all; the job queue owns that (M2.4's whole
point, and design item 3's explicit "NOT a VissimEditSession" requirement).
``_build_edit_session`` is spied on to prove it is never even CALLED for a
run-tier approval, not merely that its (real, COM-touching) session never
connects.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from routers import assistant as assistant_endpoint
import services.agent.tools_run as tools_run
from services.agent.edit_session import get_active_edit_session

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"


class FakeStructuredProvider:
    """Canned (action, meta) queue — see test_agent_approval_endpoint.py's
    identical fixture for the full rationale."""

    def __init__(self, queue: list[tuple[dict[str, Any] | None, dict[str, Any]]]) -> None:
        self._queue = list(queue)
        self.calls: list[dict[str, Any]] = []

    def generate_structured(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        self.calls.append(
            {"messages": messages, "tools": tools, "schema": schema, "system": system}
        )
        if not self._queue:
            raise AssertionError("FakeStructuredProvider queue exhausted unexpectedly")
        return self._queue.pop(0)


def _action(
    tool_name: str, *, summary: str = "확인합니다", **params: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "fake", "fallback_rung": 0}


class _FakeJobRunner:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.record


@pytest.fixture(autouse=True)
def _pin_active_dataset_to_demo(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(assistant_endpoint, "get_active_inpx_path", lambda: DEMO_INPX)


@pytest.fixture(autouse=True)
def _reset_active_edit_session_and_run_source():
    from services.agent.edit_session import clear_active_edit_session

    clear_active_edit_session()
    tools_run.clear_active_run_source()
    yield
    clear_active_edit_session()
    tools_run.clear_active_run_source()


@pytest.fixture()
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def _stream_events(response) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _propose(client, monkeypatch, provider) -> tuple[Any, list[dict[str, Any]]]:
    monkeypatch.setattr(assistant_endpoint, "_default_provider", lambda: provider)
    response = client.post(
        "/api/assistant/agent/query/stream",
        json={"message": "5분 시뮬레이션 돌려줘", "response_language": "ko"},
    )
    assert response.status_code == 200
    return response, _stream_events(response)


# ---------------------------------------------------------------------------
# (a) full propose -> pending file -> approve -> dispatch -> verified answer,
#     WITHOUT ever building a VissimEditSession
# ---------------------------------------------------------------------------


def test_full_propose_pending_file_approve_dispatch_run_verified_answer(client, monkeypatch):
    # 5 seeds: enough to stay clear of constraints.py's (M3.1)
    # single_seed_run_not_statistically_valid, which is not what this test is
    # isolating (it's testing the no-VissimEditSession propose->approve wiring).
    provider = FakeStructuredProvider(
        [
            _action(
                "run_simulation",
                summary="5분 시뮬레이션 실행 제안",
                duration_s=300,
                random_seeds=[1, 2, 3, 4, 5],
            ),
            _action(
                "run_simulation",
                summary="승인된 실행을 시작합니다",
                duration_s=300,
                random_seeds=[1, 2, 3, 4, 5],
            ),
            _action("respond", summary="실행을 완료했습니다", answer="5분 시뮬레이션 실행을 완료했습니다."),
        ]
    )

    _, propose_events = _propose(client, monkeypatch, provider)
    types = [e["type"] for e in propose_events]
    assert "approval_request" in types
    approval_event = next(e for e in propose_events if e["type"] == "approval_request")
    propose_done = propose_events[-1]
    assert propose_done["type"] == "done"
    assert propose_done["response"]["kind"] == "approval_request"

    assert approval_event["actions"] == [
        {
            "tool": "run_simulation",
            "params": {"duration_s": 300, "random_seeds": [1, 2, 3, 4, 5]},
            "summary": "5분 시뮬레이션 실행 제안",
        }
    ]
    assert approval_event["warnings"] == []

    session_id = propose_done["response"]["session_id"]
    approval_id = approval_event["approval_id"]

    work_dir = assistant_endpoint._agent_session_work_dir(session_id)
    pending_path = work_dir / "pending_approval.json"
    assert pending_path.exists()

    # This is the whole point of design item 3: a run-tier approval must
    # NEVER construct a VissimEditSession at all -- the job queue owns COM.
    def _fail_if_called(working_path):
        raise AssertionError(
            f"_build_edit_session must not be called for a run-tier approval (working_path={working_path!r})"
        )

    monkeypatch.setattr(assistant_endpoint, "_build_edit_session", _fail_if_called)

    fake_runner = _FakeJobRunner(
        {"id": "fake-run-job", "status": "done", "result_tables": ["SIMULATIONRUN"]}
    )
    monkeypatch.setattr(tools_run, "_run_job_and_wait", fake_runner)

    approve_response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": approval_id, "approved": True},
    )
    assert approve_response.status_code == 200
    approve_events = _stream_events(approve_response)
    approve_done = approve_events[-1]

    assert approve_done["type"] == "done"
    assert approve_done["response"]["kind"] == "answer"
    assert "완료" in approve_done["response"]["answer"]

    # single-use: consumed.
    assert not pending_path.exists()

    # the run actually dispatched against the injected fake job runner.
    assert len(fake_runner.calls) == 1
    assert fake_runner.calls[0]["duration_s"] == 300
    assert fake_runner.calls[0]["source_inpx"] == work_dir / "working.inpx"

    # no VissimEditSession was ever left active (there never was one).
    assert get_active_edit_session() is None

    # the working copy was never touched by a run -- byte-identical to the
    # source it was cloned from (a run cannot mutate the network).
    assert (work_dir / "working.inpx").read_bytes() == DEMO_INPX.read_bytes()


# ---------------------------------------------------------------------------
# (b) decline -> pending deleted, no job ever submitted
# ---------------------------------------------------------------------------


def test_decline_run_action_never_submits_a_job(client, monkeypatch):
    provider = FakeStructuredProvider(
        [_action("run_simulation", summary="시뮬레이션 실행 제안", duration_s=300)]
    )

    _, propose_events = _propose(client, monkeypatch, provider)
    propose_done = propose_events[-1]
    session_id = propose_done["response"]["session_id"]
    approval_id = propose_done["response"]["approval"]["approval_id"]

    fake_runner = _FakeJobRunner({"id": "unused", "status": "done", "result_tables": []})
    monkeypatch.setattr(tools_run, "_run_job_and_wait", fake_runner)

    decline_response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": approval_id, "approved": False},
    )
    assert decline_response.status_code == 200
    decline_done = _stream_events(decline_response)[-1]
    assert decline_done["response"]["kind"] == "answer"

    assert fake_runner.calls == []  # never submitted

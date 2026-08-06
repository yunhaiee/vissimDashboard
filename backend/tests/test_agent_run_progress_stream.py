"""RUN-PROGRESS UI (2026-07-28): tests for the ``run_started`` NDJSON event
and the ``tools_run.set_run_started_callback`` hook it rides on.

Unlike test_agent_run_approval_endpoint.py (which replaces
``tools_run._run_job_and_wait`` wholesale with a fake), the endpoint test
here keeps the REAL ``_run_job_and_wait`` and only fakes what's beneath it
(``routers.simulations.start_agent_simulation_job`` and
``services.simulation_runs.get_job_record``) -- proving the callback fires
from the actual production code path, not a bypassed mock. The unit tests
below exercise the callback plumbing in isolation.

No real Vissim COM anywhere in this file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
import routers.simulations as simulations_router
import services.agent.tools_run as tools_run
import services.simulation_runs as simulation_runs
from routers import assistant as assistant_endpoint

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"


# ---------------------------------------------------------------------------
# Unit tests: tools_run's run_started callback plumbing in isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_run_started_callback():
    tools_run.clear_run_started_callback()
    yield
    tools_run.clear_run_started_callback()


def test_set_and_clear_run_started_callback():
    assert tools_run._on_run_started is None
    seen: list[str] = []

    def callback(job_id: str) -> None:
        seen.append(job_id)

    tools_run.set_run_started_callback(callback)
    assert tools_run._on_run_started is callback
    tools_run._on_run_started("job-1")
    assert seen == ["job-1"]
    tools_run.clear_run_started_callback()
    assert tools_run._on_run_started is None


def test_run_job_and_wait_invokes_callback_before_polling_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The callback must fire as soon as the job is SUBMITTED -- proven here
    by making the polled record only turn terminal on the SECOND
    get_job_record call, so a callback that fired only after the job
    finished would be observably too late (the first poll would already see
    a non-terminal record, and the callback must have already run by then)."""
    submitted_ids: list[str] = []

    def fake_start(*, source_inpx: Path, duration_s: int, random_seeds: list):
        submitted_ids.append("job-xyz")
        return {"id": "job-xyz", "status": "queued"}

    poll_count = {"n": 0}

    def fake_get_job_record(job_id: str):
        poll_count["n"] += 1
        if poll_count["n"] < 2:
            return {"id": job_id, "status": "running"}
        return {"id": job_id, "status": "done", "result_tables": ["SIMULATIONRUN"]}

    monkeypatch.setattr(simulations_router, "start_agent_simulation_job", fake_start)
    monkeypatch.setattr(simulation_runs, "get_job_record", fake_get_job_record)
    monkeypatch.setattr(tools_run.time, "sleep", lambda _seconds: None)  # no real waiting

    seen: list[str] = []
    tools_run.set_run_started_callback(seen.append)

    source = tmp_path / "working.inpx"
    source.write_text("<net/>", encoding="utf-8")

    record = tools_run._run_job_and_wait(
        source_inpx=source, duration_s=60, random_seeds=[None], timeout_s=30.0
    )

    assert seen == ["job-xyz"]  # fired exactly once, with the real job id
    assert record["status"] == "done"
    assert poll_count["n"] == 2  # proves the callback fired BEFORE the terminal poll


def test_run_job_and_wait_swallows_a_raising_callback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A broken callback must never break the run itself -- same best-effort
    discipline as orchestrator.py's on_step (_emit_step's own docstring)."""

    def fake_start(*, source_inpx: Path, duration_s: int, random_seeds: list):
        return {"id": "job-abc", "status": "queued"}

    def fake_get_job_record(job_id: str):
        return {"id": job_id, "status": "done", "result_tables": ["SIMULATIONRUN"]}

    monkeypatch.setattr(simulations_router, "start_agent_simulation_job", fake_start)
    monkeypatch.setattr(simulation_runs, "get_job_record", fake_get_job_record)

    def broken_callback(job_id: str) -> None:
        raise RuntimeError("callback exploded")

    tools_run.set_run_started_callback(broken_callback)

    source = tmp_path / "working.inpx"
    source.write_text("<net/>", encoding="utf-8")

    record = tools_run._run_job_and_wait(
        source_inpx=source, duration_s=60, random_seeds=[None], timeout_s=30.0
    )
    assert record["status"] == "done"  # the run itself is unaffected


def test_no_callback_set_is_a_silent_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def fake_start(*, source_inpx: Path, duration_s: int, random_seeds: list):
        return {"id": "job-def", "status": "queued"}

    def fake_get_job_record(job_id: str):
        return {"id": job_id, "status": "done", "result_tables": ["SIMULATIONRUN"]}

    monkeypatch.setattr(simulations_router, "start_agent_simulation_job", fake_start)
    monkeypatch.setattr(simulation_runs, "get_job_record", fake_get_job_record)

    source = tmp_path / "working.inpx"
    source.write_text("<net/>", encoding="utf-8")

    record = tools_run._run_job_and_wait(
        source_inpx=source, duration_s=60, random_seeds=[None], timeout_s=30.0
    )
    assert record["status"] == "done"


# ---------------------------------------------------------------------------
# Endpoint integration: the real _run_job_and_wait, faked underneath, proves
# `run_started` actually reaches the NDJSON stream with the right job_id,
# ahead of the terminal `done` event.
# ---------------------------------------------------------------------------


class FakeStructuredProvider:
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


def _action(tool_name: str, *, summary: str = "확인합니다", **params: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "fake", "fallback_rung": 0}


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


def test_approve_run_action_streams_run_started_with_job_id_before_done(
    client, monkeypatch: pytest.MonkeyPatch, approver_headers
):
    provider = FakeStructuredProvider(
        [
            _action(
                "run_simulation", summary="5분 시뮬레이션 실행 제안", duration_s=300, random_seeds=[1, 2, 3, 4, 5]
            ),
            _action(
                "run_simulation", summary="승인된 실행을 시작합니다", duration_s=300, random_seeds=[1, 2, 3, 4, 5]
            ),
            _action("respond", summary="실행을 완료했습니다", answer="시뮬레이션 실행을 완료했습니다."),
        ]
    )
    monkeypatch.setattr(assistant_endpoint, "_default_provider", lambda: provider)

    propose_response = client.post(
        "/api/assistant/agent/query/stream",
        json={"message": "2개 시드로 5분 시뮬레이션 돌려줘", "response_language": "ko"},
    )
    assert propose_response.status_code == 200
    propose_events = _stream_events(propose_response)
    propose_done = propose_events[-1]
    session_id = propose_done["response"]["session_id"]
    approval_id = propose_done["response"]["approval"]["approval_id"]

    # Real _run_job_and_wait, faked only beneath: proves run_started fires
    # from the actual tools_run.py code path this milestone wired up, not a
    # wholesale-replaced fake runner.
    def fake_start(*, source_inpx: Path, duration_s: int, random_seeds: list):
        return {"id": "progress-job-1", "status": "queued"}

    poll_count = {"n": 0}

    def fake_get_job_record(job_id: str):
        poll_count["n"] += 1
        if poll_count["n"] < 2:
            return {"id": job_id, "status": "running"}
        return {
            "id": job_id,
            "status": "done",
            "result_tables": ["SIMULATIONRUN"],
            "evaluation_configured": True,
            "trajectory_recorded": False,
        }

    monkeypatch.setattr(simulations_router, "start_agent_simulation_job", fake_start)
    monkeypatch.setattr(simulation_runs, "get_job_record", fake_get_job_record)
    monkeypatch.setattr(tools_run.time, "sleep", lambda _seconds: None)

    approve_response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": approval_id, "approved": True},
        headers=approver_headers,
    )
    assert approve_response.status_code == 200
    events = _stream_events(approve_response)

    run_started_events = [e for e in events if e["type"] == "run_started"]
    assert len(run_started_events) == 1
    assert run_started_events[0]["job_id"] == "progress-job-1"
    assert run_started_events[0]["stream_id"]

    # run_started must arrive strictly BEFORE the terminal `done` event --
    # that's the entire point (frontend can start polling immediately
    # instead of waiting for the run to finish).
    run_started_index = events.index(run_started_events[0])
    done_index = next(i for i, e in enumerate(events) if e["type"] == "done")
    assert run_started_index < done_index

    done_event = events[done_index]
    assert done_event["response"]["kind"] == "answer"

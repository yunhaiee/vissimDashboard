"""Endpoint tests for M2.3: the approval flow across
``POST /api/assistant/agent/query/stream`` (propose) and
``POST /api/assistant/agent/approve`` (resolve).

Same ``TestClient`` + monkeypatched ``_default_provider`` pattern as
test_agent_endpoint.py (kept local rather than cross-imported, matching that
file's own stated convention), PLUS a monkeypatched ``_build_edit_session``
(the small M2.3 injection seam) so the approved-write leg never touches real
Vissim COM. ``FakeEditSession.persist()`` actually mutates the working
copy's XML on disk (not just an in-memory flag) — required for
``guardrails.py``'s postwrite "target-persisted" check (unmodifiable in
this task) to positively verify the write instead of silently degrading,
which is what proves the "postwrite-verified answer streamed" outcome this
file's main test asserts, not merely "no refusal happened".
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from routers import assistant as assistant_endpoint
from services.agent.edit_session import get_active_edit_session
from services.agent.state import ArtifactManager
from services.agent.tools_metrics import _looks_like_path
from services.agent.tools_network import build_shadow_model

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"


class FakeStructuredProvider:
    """Canned (action, meta) queue — see test_agent_endpoint.py's identical
    fixture for the full rationale."""

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


class FakeEditSession:
    """Duck-types VissimEditSession's surface (same shape as
    test_agent_tools_edit.py's fixture) PLUS an XML-mutating ``persist()``,
    since these tests exercise the write end-to-end through the REAL
    ``tools_edit.set_vehicle_input_volume`` and the real
    ``guardrails.postwrite`` target-persisted check, not a fake tool.

    ``practice.inpx``'s ``<vehicleInput>`` elements are self-closing with no
    ``<timeIntervalVehVolume>`` child (docs: tools_network.py's own
    docstring) — persist() injects one the first time a given input's
    volume is set, mirroring how a real Vissim SaveNetAs would materialize
    it.
    """

    def __init__(
        self,
        working_inpx: Path,
        *,
        readback: dict[str, Any] | None = None,
        persist_delay_s: float = 0.0,
    ) -> None:
        self.working_inpx = Path(working_inpx)
        self.calls: list[tuple[Any, ...]] = []
        self._readback = readback or {}
        self._pending_volume: dict[int, float] = {}
        self.persist_calls = 0
        self.closed = False
        # Widens the propose/approve write's real-world window (a real
        # Vissim COM SaveNetAs takes real wall-clock seconds) so a
        # concurrency test can deterministically land a second turn's
        # request WHILE this turn's write is still in flight -- see
        # test_concurrent_turn_after_approve_does_not_lose_state_or_working_copy.
        self._persist_delay_s = persist_delay_s

    def set_vehicle_input_volume(self, input_no: int, volume: float) -> None:
        self.calls.append(("set_vehicle_input_volume", input_no, volume))
        self._pending_volume[input_no] = volume

    def get_vehicle_input_volume(self, input_no: int) -> float:
        self.calls.append(("get_vehicle_input_volume", input_no))
        return self._readback["vehicle_input_volume"]

    def set_signal_cycle(self, sc_no: int, cycle: int) -> None:
        self.calls.append(("set_signal_cycle", sc_no, cycle))

    def get_signal_cycle(self, sc_no: int) -> int:
        self.calls.append(("get_signal_cycle", sc_no))
        return self._readback.get("signal_cycle", 0)

    def set_signal_offset(self, sc_no: int, offset: int) -> None:
        self.calls.append(("set_signal_offset", sc_no, offset))

    def get_signal_offset(self, sc_no: int) -> int:
        self.calls.append(("get_signal_offset", sc_no))
        return self._readback.get("signal_offset", 0)

    def set_sim_duration(self, seconds: int) -> None:
        self.calls.append(("set_sim_duration", seconds))

    def get_sim_duration(self) -> int:
        self.calls.append(("get_sim_duration",))
        return self._readback.get("sim_duration", 0)

    def set_random_seed(self, seed: int) -> None:
        self.calls.append(("set_random_seed", seed))

    def get_random_seed(self) -> int:
        self.calls.append(("get_random_seed",))
        return self._readback.get("random_seed", 0)

    def persist(self) -> None:
        if self._persist_delay_s:
            time.sleep(self._persist_delay_s)
        self.persist_calls += 1
        if not self._pending_volume:
            return
        text = self.working_inpx.read_text(encoding="utf-8")
        for input_no, volume in self._pending_volume.items():
            pattern = re.compile(rf'(<vehicleInput no="{input_no}"[^>]*?)/>')
            replacement = rf'\1><timeIntervalVehVolume volume="{volume}"/></vehicleInput>'
            text, count = pattern.subn(replacement, text, count=1)
            assert count == 1, f"fixture: could not locate vehicleInput no={input_no} to mutate"
        self.working_inpx.write_text(text, encoding="utf-8")
        self._pending_volume.clear()

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _pin_active_dataset_to_demo(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(assistant_endpoint, "get_active_inpx_path", lambda: DEMO_INPX)


@pytest.fixture(autouse=True)
def _reset_active_edit_session():
    from services.agent.edit_session import clear_active_edit_session

    clear_active_edit_session()
    yield
    clear_active_edit_session()


@pytest.fixture()
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def _stream_events(response) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _assert_no_path_like_strings(value: Any) -> None:
    if isinstance(value, dict):
        for v in value.values():
            _assert_no_path_like_strings(v)
    elif isinstance(value, list):
        for item in value:
            _assert_no_path_like_strings(item)
    elif isinstance(value, str):
        assert not _looks_like_path(value), f"path-like string leaked into a stream event: {value!r}"


def _propose(client, monkeypatch, provider) -> tuple[Any, list[dict[str, Any]]]:
    monkeypatch.setattr(assistant_endpoint, "_default_provider", lambda: provider)
    response = client.post(
        "/api/assistant/agent/query/stream",
        json={"message": "1번 입력 교통량을 600으로 바꿔줘", "response_language": "ko"},
    )
    assert response.status_code == 200
    return response, _stream_events(response)


# ---------------------------------------------------------------------------
# (a) full propose -> pending file -> approve -> dispatch -> verified answer
# ---------------------------------------------------------------------------


def test_full_propose_pending_file_approve_dispatch_verified_answer(client, monkeypatch):
    provider = FakeStructuredProvider(
        [
            _action(
                "set_vehicle_input_volume",
                summary="교통량을 600으로 변경 제안",
                input_no="1",
                volume=600,
            ),
            _action(
                "set_vehicle_input_volume",
                summary="승인된 변경을 적용합니다",
                input_no="1",
                volume=600,
            ),
            _action(
                "respond", summary="변경을 완료했습니다", answer="1번 입력 교통량을 600으로 변경했습니다."
            ),
        ]
    )

    _, propose_events = _propose(client, monkeypatch, provider)
    for event in propose_events:
        _assert_no_path_like_strings(event)

    types = [e["type"] for e in propose_events]
    assert "approval_request" in types
    approval_event = next(e for e in propose_events if e["type"] == "approval_request")
    propose_done = propose_events[-1]
    assert propose_done["type"] == "done"
    assert propose_done["response"]["kind"] == "approval_request"
    assert propose_done["response"]["approval"]["approval_id"] == approval_event["approval_id"]

    session_id = propose_done["response"]["session_id"]
    approval_id = approval_event["approval_id"]

    # actions carry the RAW proposed params (pre-dispatch, pre-pydantic-
    # coercion) -- volume is still the int 600 the fake planner emitted,
    # not the float 600.0 pydantic later coerces it to during dispatch.
    assert approval_event["actions"] == [
        {
            "tool": "set_vehicle_input_volume",
            "params": {"input_no": "1", "volume": 600},
            "summary": "교통량을 600으로 변경 제안",
        }
    ]
    assert approval_event["warnings"] == []
    assert approval_event["assumptions"] == []

    # pending file actually written to this session's work dir.
    work_dir = assistant_endpoint._agent_session_work_dir(session_id)
    pending_path = work_dir / "pending_approval.json"
    assert pending_path.exists()
    pending_data = json.loads(pending_path.read_text(encoding="utf-8"))
    assert pending_data["approval_id"] == approval_id
    assert pending_data["actions"] == approval_event["actions"]
    assert pending_data["language"] == "ko"
    assert "1번 입력 교통량을 600으로 바꿔줘" == pending_data["message"]

    fake_session = FakeEditSession(work_dir / "working.inpx", readback={"vehicle_input_volume": 600.0})
    monkeypatch.setattr(assistant_endpoint, "_build_edit_session", lambda working_path: fake_session)

    approve_response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": approval_id, "approved": True},
    )
    assert approve_response.status_code == 200
    approve_events = _stream_events(approve_response)
    for event in approve_events:
        _assert_no_path_like_strings(event)

    approve_done = approve_events[-1]
    assert approve_done["type"] == "done"
    assert approve_done["response"]["kind"] == "answer"
    assert "600" in approve_done["response"]["answer"]

    # single-use: consumed.
    assert not pending_path.exists()

    # the write actually dispatched against the injected fake session --
    # int coercion happened at the COM boundary (input_no "1" -> 1).
    assert fake_session.persist_calls == 1
    assert fake_session.closed is True
    assert ("set_vehicle_input_volume", 1, 600.0) in fake_session.calls

    # the module-global active-session context was cleaned up afterward.
    assert get_active_edit_session() is None

    # postwrite-verified: the version was actually saved AND the saved file
    # reflects the write (guardrails.py's target-persisted check passed
    # cleanly, not just silently degraded -- see FakeEditSession's docstring).
    # A FRESH ArtifactManager just to read verify_manager.version (directory
    # scan only, no filesystem mutation); the shadow model itself is read
    # via the free build_shadow_model function directly against the ALREADY
    # -saved working.inpx, since clone()/rollback_to() would themselves
    # mutate this test's own evidence.
    verify_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    assert verify_manager.version >= 1
    saved_shadow = build_shadow_model(work_dir / "working.inpx")
    saved_volume = next(v["volume"] for v in saved_shadow["vehicle_inputs"] if v["no"] == "1")
    assert saved_volume == 600.0


# ---------------------------------------------------------------------------
# (b) decline -> pending deleted, network untouched
# ---------------------------------------------------------------------------


def test_decline_deletes_pending_and_leaves_network_untouched(client, monkeypatch):
    provider = FakeStructuredProvider(
        [
            _action(
                "set_vehicle_input_volume",
                summary="교통량을 600으로 변경 제안",
                input_no="1",
                volume=600,
            ),
        ]
    )

    _, propose_events = _propose(client, monkeypatch, provider)
    propose_done = propose_events[-1]
    session_id = propose_done["response"]["session_id"]
    approval_id = propose_done["response"]["approval"]["approval_id"]

    work_dir = assistant_endpoint._agent_session_work_dir(session_id)
    pending_path = work_dir / "pending_approval.json"
    assert pending_path.exists()

    decline_response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": approval_id, "approved": False},
    )
    assert decline_response.status_code == 200
    decline_events = _stream_events(decline_response)
    for event in decline_events:
        _assert_no_path_like_strings(event)

    decline_done = decline_events[-1]
    assert decline_done["type"] == "done"
    assert decline_done["response"]["kind"] == "answer"
    assert decline_done["response"]["steps"] == []
    assert decline_done["response"]["session_id"] == session_id

    assert not pending_path.exists()  # consumed either way

    # Network untouched: version() is 0 (nothing was ever saved) and the
    # working copy is still byte-identical to the source it was cloned from.
    verify_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    assert verify_manager.version == 0
    assert (work_dir / "working.inpx").read_bytes() == DEMO_INPX.read_bytes()


# ---------------------------------------------------------------------------
# (c) stale / unknown / malformed approval_id or session_id -> 404
# ---------------------------------------------------------------------------


def test_stale_approval_id_returns_404(client, monkeypatch):
    provider = FakeStructuredProvider(
        [_action("set_vehicle_input_volume", summary="제안", input_no="1", volume=600)]
    )
    _, propose_events = _propose(client, monkeypatch, provider)
    session_id = propose_events[-1]["response"]["session_id"]

    response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": "not-the-real-approval-id", "approved": True},
    )

    assert response.status_code == 404


def test_unknown_session_id_returns_404(client):
    response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": "0123456789ab", "approval_id": "whatever", "approved": True},
    )

    assert response.status_code == 404


def test_malformed_session_id_returns_404_not_a_path_traversal(client):
    response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": "../../etc", "approval_id": "whatever", "approved": True},
    )

    assert response.status_code == 404


def test_approving_twice_the_second_call_404s(client, monkeypatch):
    provider = FakeStructuredProvider(
        [
            _action("set_vehicle_input_volume", summary="제안", input_no="1", volume=600),
            _action("set_vehicle_input_volume", summary="적용", input_no="1", volume=600),
            _action("respond", summary="완료", answer="600으로 변경했습니다."),
        ]
    )
    _, propose_events = _propose(client, monkeypatch, provider)
    propose_done = propose_events[-1]
    session_id = propose_done["response"]["session_id"]
    approval_id = propose_done["response"]["approval"]["approval_id"]

    work_dir = assistant_endpoint._agent_session_work_dir(session_id)
    fake_session = FakeEditSession(work_dir / "working.inpx", readback={"vehicle_input_volume": 600.0})
    monkeypatch.setattr(assistant_endpoint, "_build_edit_session", lambda working_path: fake_session)

    first = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": approval_id, "approved": True},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": approval_id, "approved": True},
    )
    assert second.status_code == 404


# ---------------------------------------------------------------------------
# (d) BUG 1 repro: the turn AFTER approve must resume the SAME session, not
# start fresh -- docs/phase2/DECISIONS.md 2026-07-24 "QUEUED (server-side
# investigation)" entry (M4 batch).
# ---------------------------------------------------------------------------


def test_turn_after_approve_resumes_history_and_written_value_not_fresh(client, monkeypatch):
    """Full propose -> approve -> THIRD turn, carrying the id from approve's
    own ``done.response.session_id`` (exactly what a real client would
    thread forward, per the router's own docstring for
    ``AgentQueryRequest.session_id``). Turn B must NOT behave like a brand
    new session: its reloaded ``state.history`` must contain the write, and
    a read tool dispatched THIS turn must see the WRITTEN value (600), not
    the pre-edit network."""
    provider = FakeStructuredProvider(
        [
            _action(
                "set_vehicle_input_volume",
                summary="교통량을 600으로 변경 제안",
                input_no="1",
                volume=600,
            ),
            _action(
                "set_vehicle_input_volume",
                summary="승인된 변경을 적용합니다",
                input_no="1",
                volume=600,
            ),
            _action(
                "respond", summary="변경을 완료했습니다", answer="1번 입력 교통량을 600으로 변경했습니다."
            ),
        ]
    )

    _, propose_events = _propose(client, monkeypatch, provider)
    propose_done = propose_events[-1]
    session_id = propose_done["response"]["session_id"]
    approval_id = propose_done["response"]["approval"]["approval_id"]

    work_dir = assistant_endpoint._agent_session_work_dir(session_id)
    fake_session = FakeEditSession(work_dir / "working.inpx", readback={"vehicle_input_volume": 600.0})
    monkeypatch.setattr(assistant_endpoint, "_build_edit_session", lambda working_path: fake_session)

    approve_response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": approval_id, "approved": True},
    )
    assert approve_response.status_code == 200
    approve_events = _stream_events(approve_response)
    approve_done = approve_events[-1]
    assert approve_done["type"] == "done"
    assert approve_done["response"]["kind"] == "answer"

    # This is the id a real client threads into the NEXT turn (the router's
    # own AgentQueryRequest.session_id docstring: "a client that supplies
    # the session_id from an earlier turn's `done.response.session_id`
    # resumes that SAME work dir").
    resumed_session_id = approve_done["response"]["session_id"]
    assert resumed_session_id == session_id

    # --- turn B: same session_id, a fresh FakeStructuredProvider queue ---
    turn_b_provider = FakeStructuredProvider(
        [
            _action("get_vehicle_inputs", summary="현재 교통량을 확인합니다"),
            _action(
                "respond",
                summary="현재 값을 알려드립니다",
                answer="현재 1번 입력 교통량은 600입니다.",
            ),
        ]
    )
    monkeypatch.setattr(assistant_endpoint, "_default_provider", lambda: turn_b_provider)

    turn_b_response = client.post(
        "/api/assistant/agent/query/stream",
        json={
            "message": "지금 교통량이 얼마야?",
            "response_language": "ko",
            "session_id": resumed_session_id,
        },
    )
    assert turn_b_response.status_code == 200
    turn_b_events = _stream_events(turn_b_response)
    turn_b_done = turn_b_events[-1]
    assert turn_b_done["type"] == "done"
    assert turn_b_done["response"]["session_id"] == session_id

    # (1) The reloaded state.json's history must carry the write StepRecord
    # from the approve turn -- NOT an empty history (the "fresh session"
    # symptom).
    from services.agent import session_store

    persisted = session_store.load_state(work_dir, session_id)
    assert persisted is not None, "state.json failed to reload for the resumed session_id"
    write_records = [r for r in persisted.history if r.action == "set_vehicle_input_volume"]
    assert len(write_records) == 1, (
        f"expected the approved write in reloaded history, got actions="
        f"{[r.action for r in persisted.history]}"
    )

    # (2) The read tool dispatched THIS turn must see the WRITTEN value
    # (600), not the pre-edit network (input 1 has no volume at all in the
    # source practice.inpx -- see FakeEditSession's docstring -- so a
    # reverted working copy would show None/absent here, not merely a
    # different number).
    read_step = next(e for e in turn_b_events if e.get("type") == "step" and e.get("tool") == "get_vehicle_inputs")
    assert read_step["status"] == "ok"

    verify_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    saved_shadow = build_shadow_model(work_dir / "working.inpx")
    saved_volume = next(v["volume"] for v in saved_shadow["vehicle_inputs"] if v["no"] == "1")
    assert saved_volume == 600.0, (
        "turn B's working copy lost the approved write -- rolled back to the pre-edit clone"
    )
    assert verify_manager.version >= 1, "turn B's ArtifactManager lost the saved version history"


# ---------------------------------------------------------------------------
# (e) BUG 1 root cause, pinned: a turn fired WHILE the approve turn's write
# is still in flight must not race it and silently lose state/artifacts.
#
# docs/phase2/DECISIONS.md's 2026-07-24 "QUEUED" entry ("the turn after an
# /agent/approve started a FRESH session despite the request verifiably
# carrying the correct session_id") does NOT reproduce from a strictly
# SEQUENTIAL propose -> approve -> next-turn call sequence (see the previous
# test) -- session_id threading, state.json reload, and the ArtifactManager
# version-aware rollback are all individually correct. It DOES reproduce
# once a second turn for the SAME session_id is fired while the first
# (write) turn is still mid-flight (entirely plausible in the live app: a
# real Vissim COM write/run can take real wall-clock seconds, and nothing
# blocks a client from sending its next message before the previous turn's
# stream has resolved). Both `run_agent_turn` calls independently
# `session_store.load_state`/scan the SAME stale on-disk snapshot and
# independently persist at the end -- last-writer-wins silently drops
# whichever turn's history/version was NOT the last one saved.
# ---------------------------------------------------------------------------


def test_concurrent_turn_after_approve_does_not_lose_state_or_working_copy(client, monkeypatch):
    provider = FakeStructuredProvider(
        [
            _action(
                "set_vehicle_input_volume", summary="교통량을 600으로 변경 제안", input_no="1", volume=600
            ),
            _action(
                "set_vehicle_input_volume", summary="승인된 변경을 적용합니다", input_no="1", volume=600
            ),
            _action("respond", summary="변경을 완료했습니다", answer="600으로 변경했습니다."),
        ]
    )
    _, propose_events = _propose(client, monkeypatch, provider)
    propose_done = propose_events[-1]
    session_id = propose_done["response"]["session_id"]
    approval_id = propose_done["response"]["approval"]["approval_id"]

    work_dir = assistant_endpoint._agent_session_work_dir(session_id)
    # A slow persist() widens the write's in-flight window so the second
    # turn below deterministically lands WHILE the approve turn still holds
    # (or is about to acquire) the session lock, instead of relying on luck.
    fake_session = FakeEditSession(
        work_dir / "working.inpx", readback={"vehicle_input_volume": 600.0}, persist_delay_s=0.3
    )
    monkeypatch.setattr(assistant_endpoint, "_build_edit_session", lambda working_path: fake_session)

    approve_result: dict[str, Any] = {}

    def do_approve() -> None:
        approve_result["response"] = client.post(
            "/api/assistant/agent/approve",
            json={"session_id": session_id, "approval_id": approval_id, "approved": True},
        )

    approve_thread = threading.Thread(target=do_approve)
    approve_thread.start()
    # Give the approve request just enough head start to enter the endpoint
    # (past pending-approval validation) but well before the slow persist()
    # finishes -- lands turn B squarely inside the write's in-flight window.
    time.sleep(0.05)

    turn_b_provider = FakeStructuredProvider(
        [
            _action("get_vehicle_inputs", summary="현재 교통량을 확인합니다"),
            _action("respond", summary="답변합니다", answer="현재 값을 알려드립니다."),
        ]
    )
    monkeypatch.setattr(assistant_endpoint, "_default_provider", lambda: turn_b_provider)

    turn_b_response = client.post(
        "/api/assistant/agent/query/stream",
        json={
            "message": "지금 교통량이 얼마야?",
            "response_language": "ko",
            "session_id": session_id,
        },
    )
    approve_thread.join(timeout=10)
    assert not approve_thread.is_alive(), "approve turn did not finish"

    assert approve_result["response"].status_code == 200
    assert turn_b_response.status_code == 200

    turn_b_events = _stream_events(turn_b_response)
    for event in turn_b_events:
        _assert_no_path_like_strings(event)

    # Both turns' history must survive -- neither silently clobbered the
    # other's save_state() call. Order is not asserted (which turn's lock
    # wait resolves first is not the contract), only that nothing is LOST.
    from services.agent import session_store

    persisted = session_store.load_state(work_dir, session_id)
    assert persisted is not None
    actions = [r.action for r in persisted.history]
    assert "set_vehicle_input_volume" in actions, (
        f"the approved write vanished from the session's persisted history: {actions!r}"
    )
    assert "get_vehicle_inputs" in actions, (
        f"turn B's own read vanished from the session's persisted history: {actions!r}"
    )

    # The working copy must reflect the write, not a mid-race revert to the
    # pristine clone (input 1 has no volume at all in the source network --
    # see FakeEditSession's docstring -- so any revert shows up as a missing
    # value, not merely a different number).
    saved_shadow = build_shadow_model(work_dir / "working.inpx")
    saved_volume = next(v["volume"] for v in saved_shadow["vehicle_inputs"] if v["no"] == "1")
    assert saved_volume == 600.0, "the concurrent turn reverted the working copy mid-write"

    verify_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    assert verify_manager.version >= 1, "the concurrent turn lost the saved version history"

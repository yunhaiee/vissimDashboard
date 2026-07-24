"""Tests for M3.3's session_store.py: save/load AgentState round-tripping,
the reload contract's degrade-to-None-never-raise guarantee, atomic writes,
and session_id sanitization (shared with services.agent.trace's, see
test_agent_trace.py's own sanitization tests for TraceWriter's side of the
same blocking finding)."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from services.agent import session_store
from services.agent.state import AgentState, StepRecord

SESSION_ID = "s-store-1"


def _sample_state() -> AgentState:
    return AgentState(
        goal="1번 입력 교통량을 600으로 바꿔줘",
        artifacts={},
        history=[
            StepRecord(
                thought="현재 값을 확인합니다",
                action="get_vehicle_inputs",
                params={},
                observation={"status": "ok"},
                kpi=None,
            )
        ],
        context={
            "language": "ko",
            "tier": "simple",
            "accumulated_warnings": [
                {"constraint_id": "latent_demand_overflow", "message": "잠재 수요 경고"}
            ],
        },
    )


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_save_then_load_round_trips_the_full_state(tmp_path: Path):
    state = _sample_state()

    session_store.save_state(tmp_path, SESSION_ID, state)
    reloaded = session_store.load_state(tmp_path, SESSION_ID)

    assert reloaded is not None
    assert reloaded.goal == state.goal
    assert reloaded.context == state.context
    assert len(reloaded.history) == 1
    assert reloaded.history[0] == state.history[0]


def test_save_writes_to_the_fixed_state_json_filename(tmp_path: Path):
    session_store.save_state(tmp_path, SESSION_ID, _sample_state())

    assert (tmp_path / "state.json").exists()
    assert session_store.state_path(tmp_path) == tmp_path / "state.json"


def test_save_overwrites_a_prior_state_json(tmp_path: Path):
    first = _sample_state()
    session_store.save_state(tmp_path, SESSION_ID, first)

    second = AgentState(goal="다른 요청", artifacts={}, history=[], context={})
    session_store.save_state(tmp_path, SESSION_ID, second)

    reloaded = session_store.load_state(tmp_path, SESSION_ID)
    assert reloaded is not None
    assert reloaded.goal == "다른 요청"
    assert reloaded.history == []


def test_save_creates_the_work_dir_if_missing(tmp_path: Path):
    work_dir = tmp_path / "not-yet-created"
    assert not work_dir.exists()

    session_store.save_state(work_dir, SESSION_ID, _sample_state())

    assert (work_dir / "state.json").exists()


def test_save_leaves_no_leftover_tmp_file(tmp_path: Path):
    session_store.save_state(tmp_path, SESSION_ID, _sample_state())

    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


# ---------------------------------------------------------------------------
# Reload contract: degrade to None, never raise, log a warning
# ---------------------------------------------------------------------------


def test_load_returns_none_when_state_json_is_absent(tmp_path: Path):
    assert session_store.load_state(tmp_path, SESSION_ID) is None


def test_load_returns_none_and_logs_a_warning_for_malformed_json(tmp_path: Path, caplog):
    (tmp_path / "state.json").write_text("{not valid json", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="services.agent.session_store"):
        result = session_store.load_state(tmp_path, SESSION_ID)

    assert result is None
    assert any(
        "state.json" in record.message and record.levelno == logging.WARNING
        for record in caplog.records
    )


def test_load_returns_none_for_valid_json_missing_a_required_key(tmp_path: Path):
    # Valid JSON, but not a shape AgentState.from_dict recognizes (missing
    # "context").
    (tmp_path / "state.json").write_text(
        '{"goal": "x", "artifacts": {}, "history": []}', encoding="utf-8"
    )

    assert session_store.load_state(tmp_path, SESSION_ID) is None


def test_load_returns_none_for_json_that_is_not_an_object(tmp_path: Path):
    (tmp_path / "state.json").write_text("[1, 2, 3]", encoding="utf-8")

    assert session_store.load_state(tmp_path, SESSION_ID) is None


# ---------------------------------------------------------------------------
# Session-id sanitization — the M3.3 blocking finding, session_store's side
# (TraceWriter's side is test_agent_trace.py).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    ["../../../etc/passwd", "a/b", "a\\b", "", "..", "session id with spaces"],
)
def test_save_rejects_a_malformed_session_id_without_writing_anything(
    tmp_path: Path, bad_id: str
):
    with pytest.raises(ValueError):
        session_store.save_state(tmp_path, bad_id, _sample_state())

    # Nothing was written anywhere under tmp_path (no path-traversal escape,
    # no partial write left behind either).
    assert list(tmp_path.rglob("*")) == []


def test_load_rejects_a_malformed_session_id(tmp_path: Path):
    with pytest.raises(ValueError):
        session_store.load_state(tmp_path, "../../../etc/passwd")


def test_valid_hyphenated_session_id_shape_used_throughout_the_test_suite_is_accepted(
    tmp_path: Path,
):
    # The exact shape every orchestrator test in this repo uses today (e.g.
    # "s-tier-simple") — must keep working, not just the production
    # uuid.uuid4().hex[:12] shape.
    session_store.save_state(tmp_path, "s-tier-simple", _sample_state())
    assert session_store.load_state(tmp_path, "s-tier-simple") is not None

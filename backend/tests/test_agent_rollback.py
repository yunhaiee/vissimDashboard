"""Tests for QUEUE 3 unit 7's services.agent.rollback: the pure planning
logic behind tools_edit.rollback_last_edit (no COM, no FastAPI, no
orchestrator loop -- see test_agent_orchestrator_rollback.py for the full
end-to-end approval-flow transcript).

Imports services.agent.tools_edit purely for its REGISTRATION side effect
(so default_registry actually knows "set_vehicle_input_volume"/
"run_simulation"/"rollback_last_edit" are tier write/run/write -- rollback.py
reads the real registry, not a fake one), mirroring
test_agent_orchestrator_reduced_speed_area.py's own "REAL registered tool"
convention.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import services.agent.tools_edit  # noqa: F401 - registers the real tools
import services.agent.tools_run  # noqa: F401 - registers run_simulation (tier "run")
from services.agent import rollback, session_store
from services.agent.state import AgentState, StepRecord


def _write_version(work_dir: Path, version: int, content: bytes = b"<network/>") -> Path:
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / f"edit_{version:03d}.inpx"
    path.write_bytes(content)
    return path


def _ok_write_step(tool: str, params: dict, verification: dict | None = None) -> StepRecord:
    return StepRecord(
        thought="...",
        action=tool,
        params=params,
        observation={
            "status": "ok",
            "error_type": None,
            "message": "",
            "artifacts": None,
            "suggested_fix": None,
            "verification": verification or {},
            "data": None,
        },
    )


def _save_history(work_dir: Path, history: list[StepRecord]) -> None:
    state = AgentState(goal="test", artifacts={}, history=history, context={})
    session_store.save_state(work_dir, work_dir.name, state)


# ---------------------------------------------------------------------------
# list_saved_versions
# ---------------------------------------------------------------------------


def test_list_saved_versions_empty_for_a_fresh_work_dir(tmp_path: Path):
    work_dir = tmp_path / "session"
    work_dir.mkdir()
    assert rollback.list_saved_versions(work_dir) == []


def test_list_saved_versions_empty_for_a_missing_work_dir(tmp_path: Path):
    assert rollback.list_saved_versions(tmp_path / "does-not-exist") == []


def test_list_saved_versions_sorted_regardless_of_creation_order(tmp_path: Path):
    work_dir = tmp_path / "session"
    _write_version(work_dir, 3)
    _write_version(work_dir, 1)
    _write_version(work_dir, 2)
    (work_dir / "working.inpx").write_bytes(b"current")  # not a version file, must be ignored

    assert rollback.list_saved_versions(work_dir) == [1, 2, 3]


# ---------------------------------------------------------------------------
# compute_rollback_plan -- structured refusals
# ---------------------------------------------------------------------------


def test_compute_rollback_plan_raises_nothing_to_roll_back_when_no_versions(tmp_path: Path):
    work_dir = tmp_path / "session"
    work_dir.mkdir()

    with pytest.raises(rollback.NothingToRollBackError, match="rollback_nothing_to_undo"):
        rollback.compute_rollback_plan(work_dir)


def test_compute_rollback_plan_raises_target_not_recoverable_when_snapshot_missing(tmp_path: Path):
    work_dir = tmp_path / "session"
    # A gap in the chain: version 1 exists, version 2 was (somehow) lost,
    # version 3 is the latest saved -- target_version (2) has no file.
    _write_version(work_dir, 1)
    _write_version(work_dir, 3)

    with pytest.raises(rollback.RollbackTargetNotRecoverableError, match="rollback_target_not_recoverable"):
        rollback.compute_rollback_plan(work_dir)


def test_compute_rollback_plan_raises_already_at_boundary_when_last_write_was_a_rollback(tmp_path: Path):
    work_dir = tmp_path / "session"
    _write_version(work_dir, 1)
    _write_version(work_dir, 2)
    _save_history(
        work_dir,
        [
            _ok_write_step("set_vehicle_input_volume", {"input_no": "1", "volume": 500.0}),
            _ok_write_step("rollback_last_edit", {}),
        ],
    )

    with pytest.raises(rollback.RollbackAlreadyAtBoundaryError, match="rollback_already_at_boundary"):
        rollback.compute_rollback_plan(work_dir)


# ---------------------------------------------------------------------------
# compute_rollback_plan -- the normal case, with and without history
# ---------------------------------------------------------------------------


def test_compute_rollback_plan_targets_version_before_latest(tmp_path: Path):
    work_dir = tmp_path / "session"
    _write_version(work_dir, 1)
    _write_version(work_dir, 2)

    plan = rollback.compute_rollback_plan(work_dir)

    assert plan.reverted_from_version == 2
    assert plan.target_version == 1


def test_compute_rollback_plan_target_version_zero_means_pristine_clone(tmp_path: Path):
    work_dir = tmp_path / "session"
    _write_version(work_dir, 1)

    plan = rollback.compute_rollback_plan(work_dir)

    assert plan.reverted_from_version == 1
    assert plan.target_version == 0


def test_compute_rollback_plan_reads_undone_tool_and_params_from_history(tmp_path: Path):
    work_dir = tmp_path / "session"
    _write_version(work_dir, 1)
    _write_version(work_dir, 2)
    _save_history(
        work_dir,
        [
            _ok_write_step(
                "set_vehicle_input_volume",
                {"input_no": "1", "volume": 700.0},
                verification={"written_value": 700.0, "readback_value": 700.0},
            ),
        ],
    )

    plan = rollback.compute_rollback_plan(work_dir)

    assert plan.history_available is True
    assert plan.undone_tool == "set_vehicle_input_volume"
    assert plan.undone_params == {"input_no": "1", "volume": 700.0}
    assert plan.undone_verification == {"written_value": 700.0, "readback_value": 700.0}


def test_compute_rollback_plan_skips_read_tier_history_entries_to_find_the_last_write(tmp_path: Path):
    work_dir = tmp_path / "session"
    _write_version(work_dir, 1)
    read_step = StepRecord(
        thought="checking",
        action="get_vehicle_inputs",
        params={},
        observation={
            "status": "ok",
            "error_type": None,
            "message": "",
            "artifacts": None,
            "suggested_fix": None,
            "verification": None,
            "data": None,
        },
    )
    _save_history(
        work_dir,
        [
            _ok_write_step("set_vehicle_input_volume", {"input_no": "1", "volume": 500.0}),
            read_step,
        ],
    )

    plan = rollback.compute_rollback_plan(work_dir)

    assert plan.undone_tool == "set_vehicle_input_volume"


def test_compute_rollback_plan_detects_run_since_edit(tmp_path: Path):
    work_dir = tmp_path / "session"
    _write_version(work_dir, 1)
    run_step = StepRecord(
        thought="run it",
        action="run_simulation",
        params={"duration_s": 60},
        observation={
            "status": "ok",
            "error_type": None,
            "message": "",
            "artifacts": None,
            "suggested_fix": None,
            "verification": {"job_id": "j1"},
            "data": {"run_result": {}},
        },
    )
    _save_history(
        work_dir,
        [
            _ok_write_step("set_vehicle_input_volume", {"input_no": "1", "volume": 500.0}),
            run_step,
        ],
    )

    plan = rollback.compute_rollback_plan(work_dir)

    assert plan.run_since_edit is True


def test_compute_rollback_plan_run_before_edit_does_not_count(tmp_path: Path):
    work_dir = tmp_path / "session"
    _write_version(work_dir, 1)
    run_step = StepRecord(
        thought="run it",
        action="run_simulation",
        params={"duration_s": 60},
        observation={
            "status": "ok",
            "error_type": None,
            "message": "",
            "artifacts": None,
            "suggested_fix": None,
            "verification": {"job_id": "j1"},
            "data": {"run_result": {}},
        },
    )
    _save_history(
        work_dir,
        [
            run_step,
            _ok_write_step("set_vehicle_input_volume", {"input_no": "1", "volume": 500.0}),
        ],
    )

    plan = rollback.compute_rollback_plan(work_dir)

    assert plan.run_since_edit is False


def test_compute_rollback_plan_degrades_gracefully_without_state_json(tmp_path: Path):
    work_dir = tmp_path / "session"
    _write_version(work_dir, 1)
    # No state.json written at all.

    plan = rollback.compute_rollback_plan(work_dir)

    assert plan.history_available is False
    assert plan.undone_tool is None
    assert plan.run_since_edit is False
    # Restore mechanics are unaffected by missing history.
    assert plan.target_version == 0
    assert plan.reverted_from_version == 1


# ---------------------------------------------------------------------------
# restore_working_copy
# ---------------------------------------------------------------------------


def test_restore_working_copy_is_byte_identical_and_returns_matching_hash(tmp_path: Path):
    import hashlib

    work_dir = tmp_path / "session"
    work_dir.mkdir()
    working_inpx = work_dir / "working.inpx"
    working_inpx.write_bytes(b"<network>edited</network>")
    target = _write_version(work_dir, 1, b"<network>original</network>")

    digest = rollback.restore_working_copy(working_inpx, target)

    assert working_inpx.read_bytes() == target.read_bytes()
    assert digest == hashlib.sha256(target.read_bytes()).hexdigest()
    # No leftover temp file.
    assert not (work_dir / "working_rollback_tmp.inpx").exists()

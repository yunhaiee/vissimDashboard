"""Tests for the trajectory-replay unit's job-runner wiring in
``routers/simulations.py::_run_agent_simulation_job``:

  - AGENT_RUN_TRAJECTORY_MAX_S is respected (duration over the cap never
    even attempts to arm vehicle-record output).
  - A vehicle-record arming FAILURE never fails the run itself (KPIs stay
    primary; ``trajectory_recorded`` just comes back False).
  - ``trajectory_recorded``/``trajectory_paths`` are populated correctly
    when arming succeeds AND a .fzp actually materializes, and left False
    when it doesn't (armed-but-nothing-written).
  - ``_clear_stale_result_dbs`` also clears stale .fzp files (extended by
    this unit alongside its original *.db-only behavior).

No real Vissim COM is touched: ``services.vissim_com.VissimController`` is
monkeypatched to a fake that writes a real result .db (with a SIMULATIONRUN
row -- the run-completed artifact the job runner itself verifies) and,
when armed, a real .fzp file directly into the seed work dir, mirroring
what a real COM RunContinuous call's side effects on disk look like.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

import services.simulation_runs as simulation_runs
import services.vissim_com as vissim_com
from routers import simulations as simulations_router


# ---------------------------------------------------------------------------
# Fake VissimController -- writes real files to disk, like a real RunContinuous
# ---------------------------------------------------------------------------


class _FakeSimulation:
    def __init__(self, controller: "_FakeVissimController") -> None:
        self._controller = controller

    def RunContinuous(self) -> None:
        self._controller._run()


class _FakeVissimHandle:
    def __init__(self, controller: "_FakeVissimController") -> None:
        self.Simulation = _FakeSimulation(controller)


class _FakeVissimController:
    def __init__(self, *, fail_vehrec: bool = False, write_fzp_on_run: bool = True) -> None:
        self.vissim = _FakeVissimHandle(self)
        self._dir: Path | None = None
        self._fail_vehrec = fail_vehrec
        self._write_fzp_on_run = write_fzp_on_run
        self._vehrec_armed = False
        self.configure_vehicle_record_evaluation_calls = 0

    def connect(self, **kwargs: Any) -> None:
        pass

    def load_network(self, inpx_path: str) -> None:
        self._dir = Path(inpx_path).parent

    def set_sim_duration(self, seconds: int) -> None:
        pass

    def set_random_seed(self, seed: int) -> None:
        pass

    def configure_network_performance_evaluation(self, *, duration_s: int) -> dict[str, Any]:
        return {"vehicle_network_performance": {"collect_data": True}}

    def configure_vehicle_record_evaluation(
        self, *, duration_s: int, resolution: int = 5
    ) -> dict[str, Any]:
        self.configure_vehicle_record_evaluation_calls += 1
        self.last_resolution = resolution
        if self._fail_vehrec:
            raise RuntimeError("simulated vehicle-record arming failure")
        self._vehrec_armed = True
        return {
            "write_file": True,
            "write_database": False,
            "from_time": 0,
            "to_time": duration_s,
            "resolution": resolution,
        }

    def disconnect(self) -> None:
        pass

    def _run(self) -> None:
        assert self._dir is not None
        db_path = self._dir / "run.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE SIMULATIONRUN (NO INTEGER, RANDSEED INTEGER)")
        conn.execute("INSERT INTO SIMULATIONRUN VALUES (1, 42)")
        conn.commit()
        conn.close()
        if self._vehrec_armed and self._write_fzp_on_run:
            fzp_path = self._dir / "run_001.fzp"
            fzp_path.write_text(
                "$VEHICLE:SIMSEC;NO;LINK;POS\n0.0;1;101;0.0\n", encoding="utf-8"
            )


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "registry"
    monkeypatch.setattr(simulation_runs, "SIMULATION_ROOT", root)
    monkeypatch.setattr(simulation_runs, "REGISTRY_PATH", root / "simulation_runs.json")
    return root


def _run_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    duration_s: int,
    fail_vehrec: bool = False,
    write_fzp_on_run: bool = True,
    max_s_env: str | None = None,
) -> tuple[dict[str, Any], list[_FakeVissimController]]:
    if max_s_env is None:
        monkeypatch.delenv("AGENT_RUN_TRAJECTORY_MAX_S", raising=False)
    else:
        monkeypatch.setenv("AGENT_RUN_TRAJECTORY_MAX_S", max_s_env)

    created: list[_FakeVissimController] = []

    def factory() -> _FakeVissimController:
        fake = _FakeVissimController(fail_vehrec=fail_vehrec, write_fzp_on_run=write_fzp_on_run)
        created.append(fake)
        return fake

    monkeypatch.setattr(vissim_com, "VissimController", factory)

    source = tmp_path / "source.inpx"
    source.write_text("<net/>", encoding="utf-8")

    job_id = "trajtest01"
    simulation_runs.create_job_record(
        job_id, {"name": "t", "source": "agent", "sim_duration": duration_s, "random_seeds": [None]}
    )

    simulations_router._run_agent_simulation_job(job_id, source, duration_s, [None])

    record = simulation_runs.get_job_record(job_id)
    assert record is not None
    return record, created


# ---------------------------------------------------------------------------
# _trajectory_max_s -- pure env-fallback function
# ---------------------------------------------------------------------------


def test_trajectory_max_s_defaults_to_1800(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AGENT_RUN_TRAJECTORY_MAX_S", raising=False)
    assert simulations_router._trajectory_max_s() == 1800.0


def test_trajectory_max_s_reads_env_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_RUN_TRAJECTORY_MAX_S", "300")
    assert simulations_router._trajectory_max_s() == 300.0


def test_trajectory_max_s_falls_back_on_invalid_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_RUN_TRAJECTORY_MAX_S", "not-a-number")
    assert simulations_router._trajectory_max_s() == 1800.0


# ---------------------------------------------------------------------------
# _trajectory_resolution -- pure env-fallback function
# ---------------------------------------------------------------------------


def test_trajectory_resolution_defaults_to_5(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AGENT_RUN_TRAJECTORY_RESOLUTION", raising=False)
    assert simulations_router._trajectory_resolution() == 5


def test_trajectory_resolution_reads_env_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_RUN_TRAJECTORY_RESOLUTION", "10")
    assert simulations_router._trajectory_resolution() == 10


def test_trajectory_resolution_falls_back_on_invalid_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_RUN_TRAJECTORY_RESOLUTION", "not-a-number")
    assert simulations_router._trajectory_resolution() == 5


def test_trajectory_resolution_floors_at_one(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_RUN_TRAJECTORY_RESOLUTION", "0")
    assert simulations_router._trajectory_resolution() == 1


# ---------------------------------------------------------------------------
# _clear_stale_result_dbs -- extended to also clear stale .fzp files
# ---------------------------------------------------------------------------


def test_clear_stale_result_dbs_also_removes_fzp_files(tmp_path: Path):
    stale_db = tmp_path / "leftover.db"
    stale_db.write_bytes(b"stale")
    stale_fzp = tmp_path / "leftover.fzp"
    stale_fzp.write_text("$VEHICLE:SIMSEC;NO;LINK;POS\n", encoding="utf-8")
    keep = tmp_path / "keep.inpx"
    keep.write_text("<net/>", encoding="utf-8")

    removed = simulations_router._clear_stale_result_dbs(tmp_path)

    assert set(removed) == {stale_db, stale_fzp}
    assert not stale_db.exists()
    assert not stale_fzp.exists()
    assert keep.exists()


# ---------------------------------------------------------------------------
# Guard: AGENT_RUN_TRAJECTORY_MAX_S is respected
# ---------------------------------------------------------------------------


def test_duration_under_cap_arms_and_records_trajectory(tmp_path, monkeypatch, isolated_registry):
    record, created = _run_job(tmp_path, monkeypatch, duration_s=60, max_s_env="1800")

    assert record["status"] == "done"
    assert created[0].configure_vehicle_record_evaluation_calls == 1
    assert created[0].last_resolution == 5  # DEFAULT_VEHICLE_RECORD_RESOLUTION
    assert record["trajectory_recorded"] is True
    assert len(record["trajectory_paths"]) == 1
    assert Path(record["trajectory_paths"][0]).is_file()
    assert Path(record["trajectory_paths"][0]).suffix == ".fzp"


def test_duration_over_cap_skips_arming_entirely(tmp_path, monkeypatch, isolated_registry):
    record, created = _run_job(tmp_path, monkeypatch, duration_s=3600, max_s_env="1800")

    assert record["status"] == "done"  # the run itself still succeeds
    assert created[0].configure_vehicle_record_evaluation_calls == 0  # never even attempted
    assert record["trajectory_recorded"] is False
    assert record["trajectory_paths"] == []


def test_duration_exactly_at_cap_still_arms(tmp_path, monkeypatch, isolated_registry):
    record, created = _run_job(tmp_path, monkeypatch, duration_s=1800, max_s_env="1800")

    assert created[0].configure_vehicle_record_evaluation_calls == 1
    assert record["trajectory_recorded"] is True


# ---------------------------------------------------------------------------
# Non-fatal: arming failure must never fail the run
# ---------------------------------------------------------------------------


def test_arming_failure_is_non_fatal_run_still_succeeds(tmp_path, monkeypatch, isolated_registry):
    record, created = _run_job(tmp_path, monkeypatch, duration_s=60, fail_vehrec=True)

    assert created[0].configure_vehicle_record_evaluation_calls == 1  # attempted
    assert record["status"] == "done"  # KPIs/run unaffected
    assert record.get("error") is None
    assert record["trajectory_recorded"] is False
    assert record["trajectory_paths"] == []
    assert "result_tables" in record and record["result_tables"]  # KPI path still populated


def test_armed_but_no_fzp_materializes_is_not_recorded(tmp_path, monkeypatch, isolated_registry):
    """Arming succeeds (no exception) but nothing lands on disk -- must
    degrade to not-recorded, not raise and not fail the job."""
    record, created = _run_job(tmp_path, monkeypatch, duration_s=60, write_fzp_on_run=False)

    assert created[0].configure_vehicle_record_evaluation_calls == 1
    assert record["status"] == "done"
    assert record["trajectory_recorded"] is False
    assert record["trajectory_paths"] == []

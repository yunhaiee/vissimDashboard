"""Tests for M2.4's agent-run job-runner helpers in routers/simulations.py:
the stale-result-.db refusal, table-name reading, and the
``start_agent_simulation_job`` submission guard rails. No Vissim COM is
touched anywhere in this file — ``_clear_stale_result_dbs``/
``_read_result_table_names`` are pure filesystem/sqlite helpers, and the
submission-time guards raise before any COM call would happen.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from routers import simulations as simulations_router


# ---------------------------------------------------------------------------
# _clear_stale_result_dbs — a pre-existing .db must never be read as THIS
# run's result (docs/phase2/02-ROADMAP.md M2.4)
# ---------------------------------------------------------------------------


def test_clear_stale_result_dbs_removes_pre_existing_db_files(tmp_path: Path):
    stale_db = tmp_path / "leftover_run.db"
    stale_db.write_bytes(b"not a real sqlite file, just needs to exist")
    nested = tmp_path / "nested"
    nested.mkdir()
    nested_stale = nested / "also_stale.db"
    nested_stale.write_bytes(b"stale")
    keep = tmp_path / "keep.inpx"
    keep.write_text("<net/>", encoding="utf-8")

    removed = simulations_router._clear_stale_result_dbs(tmp_path)

    assert set(removed) == {stale_db, nested_stale}
    assert not stale_db.exists()
    assert not nested_stale.exists()
    assert keep.exists()  # only *.db files are touched


def test_clear_stale_result_dbs_on_a_clean_directory_removes_nothing(tmp_path: Path):
    removed = simulations_router._clear_stale_result_dbs(tmp_path)

    assert removed == []


def test_clear_stale_result_dbs_on_a_missing_directory_is_a_no_op(tmp_path: Path):
    missing = tmp_path / "does_not_exist_yet"

    removed = simulations_router._clear_stale_result_dbs(missing)

    assert removed == []


def test_a_stale_db_never_survives_to_be_read_as_this_runs_result(tmp_path: Path):
    """End-to-end (filesystem-level) proof of the guarantee: plant a stale
    .db carrying a DIFFERENT SIMULATIONRUN row, clear it, then write "this
    run's" real result — the stale row must never be observable afterward."""
    seed_dir = tmp_path / "seed_00"
    seed_dir.mkdir()

    stale_db = seed_dir / "old_run.db"
    conn = sqlite3.connect(str(stale_db))
    conn.execute("CREATE TABLE SIMULATIONRUN (NO INTEGER, RANDSEED INTEGER)")
    conn.execute("INSERT INTO SIMULATIONRUN VALUES (999, 999999)")
    conn.commit()
    conn.close()

    simulations_router._clear_stale_result_dbs(seed_dir)

    fresh_db = seed_dir / "new_run.db"
    conn = sqlite3.connect(str(fresh_db))
    conn.execute("CREATE TABLE SIMULATIONRUN (NO INTEGER, RANDSEED INTEGER)")
    conn.execute("INSERT INTO SIMULATIONRUN VALUES (1, 42)")
    conn.commit()
    conn.close()

    db_paths = sorted(seed_dir.rglob("*.db"))
    assert db_paths == [fresh_db]  # the stale one is gone, only the fresh one remains
    conn = sqlite3.connect(str(fresh_db))
    try:
        row = conn.execute("SELECT RANDSEED FROM SIMULATIONRUN").fetchone()
    finally:
        conn.close()
    assert row == (42,)  # this run's own value, never the stale 999999


# ---------------------------------------------------------------------------
# _read_result_table_names
# ---------------------------------------------------------------------------


def test_read_result_table_names_returns_uppercased_table_set(tmp_path: Path):
    db_path = tmp_path / "run.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE SIMULATIONRUN (NO INTEGER)")
    conn.execute("CREATE TABLE vehiclenetworkperformancemeasurement_evaluationtimeintervalclass (x INTEGER)")
    conn.commit()
    conn.close()

    tables = simulations_router._read_result_table_names(db_path)

    assert tables == {
        "SIMULATIONRUN",
        "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS",
    }


def test_read_result_table_names_only_simulationrun_matches_the_short_run_shape(tmp_path: Path):
    db_path = tmp_path / "short_run.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE SIMULATIONRUN (NO INTEGER)")
    conn.commit()
    conn.close()

    assert simulations_router._read_result_table_names(db_path) == {"SIMULATIONRUN"}


# ---------------------------------------------------------------------------
# start_agent_simulation_job — submission-time guards (no COM reached)
# ---------------------------------------------------------------------------


def test_start_agent_simulation_job_rejects_a_missing_source_inpx(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(simulations_router, "_check_pywin32", lambda: (True, None))
    monkeypatch.setattr(simulations_router.os, "name", "nt")

    with pytest.raises(simulations_router.SimulationUnavailableError, match="not found"):
        simulations_router.start_agent_simulation_job(
            source_inpx=tmp_path / "does_not_exist.inpx", duration_s=300, random_seeds=[None]
        )


def test_start_agent_simulation_job_rejects_empty_random_seeds(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(simulations_router, "_check_pywin32", lambda: (True, None))
    monkeypatch.setattr(simulations_router.os, "name", "nt")
    source = tmp_path / "working.inpx"
    source.write_text("<net/>", encoding="utf-8")

    with pytest.raises(simulations_router.SimulationParameterError, match="random_seeds"):
        simulations_router.start_agent_simulation_job(
            source_inpx=source, duration_s=300, random_seeds=[]
        )


def test_start_agent_simulation_job_rejects_when_pywin32_unavailable(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(simulations_router, "_check_pywin32", lambda: (False, "no pywin32"))
    source = tmp_path / "working.inpx"
    source.write_text("<net/>", encoding="utf-8")

    with pytest.raises(simulations_router.SimulationUnavailableError):
        simulations_router.start_agent_simulation_job(
            source_inpx=source, duration_s=300, random_seeds=[None]
        )


def test_start_agent_simulation_job_submits_a_job_record_and_returns_immediately(
    tmp_path: Path, monkeypatch
):
    """Proves the "job record in the existing registry" + "process-unique
    work dir" + background-thread submission shape WITHOUT touching COM --
    the spawned thread is intercepted before it would import VissimController.

    Redirects the job registry to an isolated tmp path (both module-level
    constants ``services.simulation_runs`` reads them from) rather than
    writing a fake job record into the real, shared
    ``runtime/simulations/simulation_runs.json`` this repo's actual app
    uses.
    """
    import services.simulation_runs as simulation_runs

    tmp_registry_root = tmp_path / "registry"
    monkeypatch.setattr(simulation_runs, "SIMULATION_ROOT", tmp_registry_root)
    monkeypatch.setattr(simulation_runs, "REGISTRY_PATH", tmp_registry_root / "simulation_runs.json")

    monkeypatch.setattr(simulations_router, "_check_pywin32", lambda: (True, None))
    monkeypatch.setattr(simulations_router.os, "name", "nt")
    source = tmp_path / "working.inpx"
    source.write_text("<net/>", encoding="utf-8")

    started = threading.Event()

    # Unit 1.4 adds a trailing workspace_id (the runner re-binds the
    # submitting request's workspace on its own thread).
    def _fake_runner(job_id, source_inpx, duration_s, random_seeds, workspace_id=None):
        started.set()

    monkeypatch.setattr(simulations_router, "_run_agent_simulation_job", _fake_runner)

    submitted = simulations_router.start_agent_simulation_job(
        source_inpx=source, duration_s=300, random_seeds=[None]
    )

    assert submitted["status"] == "queued"
    assert "id" in submitted
    assert started.wait(timeout=5.0)

    record = simulation_runs.get_job_record(submitted["id"])
    assert record is not None
    assert record["params"]["source"] == "agent"
    assert record["params"]["sim_duration"] == 300

"""Tests for M2.4's tools_run.py: the single run gate (``run_simulation``).

Every test injects a FAKE ``_run_job_and_wait`` — mirrors
``test_agent_approval_endpoint.py``'s ``FakeEditSession`` /
``routers/assistant.py``'s ``_build_edit_session`` injection pattern — so no
test here touches real Vissim COM, a real background thread, or the real
file-based job registry. ``_summarize_kpis``'s ``results_reader`` call IS
real (it's pure sqlite reading, deterministic, and exercising the real
degrade-gracefully path is the point), pointed at a tiny sqlite fixture this
file builds directly via a monkeypatched ``services.simulation_runs.job_dir``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

import services.agent.tools_run as tools_run
import services.simulation_runs as simulation_runs
from services.agent.toolkit import default_registry, dispatch

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_active_run_source():
    """Every test starts with no active run source and clears it after, so
    tests can't leak an override into one another regardless of order —
    mirrors test_agent_approval_endpoint.py's _reset_active_edit_session."""
    tools_run.clear_active_run_source()
    yield
    tools_run.clear_active_run_source()


def _write_full_results_db(db_path: Path, *, delay_avg: float = 32.4) -> None:
    """A minimal but SCHEMA-COMPLETE results .db: every table
    ``results_reader._read_db`` unconditionally queries (not behind
    ``_query_rows_with_fallback``) exists, so the network aggregate is
    actually produced instead of the whole db being silently skipped."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE SIMULATIONRUN (NO INTEGER, RANDSEED INTEGER, SIMPERIOD REAL, "
            "SIMEND REAL, VISSIMVER TEXT)"
        )
        conn.execute("INSERT INTO SIMULATIONRUN VALUES (1, 42, 3600, 3600, '2026')")

        conn.execute(
            "CREATE TABLE VEHICLENETWORKPERFORMANCEMEASUREMENT_EvaluationTimeIntervalClass ("
            "ARG_VEHICLECLASS TEXT, DELAYAVG REAL, STOPSAVG REAL, SPEEDAVG REAL, "
            "DELAYSTOPAVG REAL, DISTTOT REAL, TRAVTMTOT REAL, DELAYTOT REAL, VEHACT REAL, "
            "VEHARR REAL)"
        )
        conn.execute(
            "INSERT INTO VEHICLENETWORKPERFORMANCEMEASUREMENT_EvaluationTimeIntervalClass "
            "VALUES ('ALL', ?, 1.6, 38.2, 21.0, 6480000.0, 52000.0, 19400.0, 310.0, 5050.0)",
            (delay_avg,),
        )

        conn.execute(
            "CREATE TABLE MOVEMENT_EvaluationTimeIntervalClass (OBJECT_ID TEXT, "
            "ARG_VEHICLECLASS TEXT, VEHS REAL, VEHDELAY REAL, STOPDELAY REAL, STOPS REAL)"
        )
        conn.execute(
            "CREATE TABLE VEHICLETRAVELTIMEMEASUREMENT_EvaluationTimeIntervalClass ("
            "OBJECT_ID TEXT, ARG_VEHICLECLASS TEXT, VEHS REAL, TRAVTM REAL, DISTTRAV REAL)"
        )
        conn.execute(
            "CREATE TABLE DATACOLLECTIONMEASUREMENT_EvaluationTimeIntervalClass (OBJECT_ID TEXT, "
            "ARG_VEHICLECLASS TEXT, VEHS REAL, SPEEDAVGARITH REAL, SPEEDAVGHARM REAL, "
            "QUEUEDELAY REAL)"
        )
        conn.execute(
            "CREATE TABLE MOVEMENT_EvaluationTimeInterval (OBJECT_ID TEXT, QLEN REAL, "
            "QLENMAX REAL, SUMDELAYS REAL, SUMSTOPS REAL)"
        )
        conn.commit()
    finally:
        conn.close()


def _write_simulationrun_only_db(db_path: Path) -> None:
    """A results .db from a run too short for evaluation intervals to have
    elapsed — the exact SIMULATIONRUN-only shape docs/phase2/
    00-EXECUTION-GUIDE.md SS3.1 documents and this milestone's live
    acceptance check exercises."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE SIMULATIONRUN (NO INTEGER, RANDSEED INTEGER, SIMPERIOD REAL, "
            "SIMEND REAL, VISSIMVER TEXT)"
        )
        conn.execute("INSERT INTO SIMULATIONRUN VALUES (1, 42, 300, 300, '2026')")
        conn.commit()
    finally:
        conn.close()


class _FakeJobRunner:
    """A canned ``_run_job_and_wait`` replacement: records every call's
    kwargs and returns a pre-built terminal job record — no real job queue,
    no real COM, no real background thread."""

    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self.record


# ---------------------------------------------------------------------------
# Active run source context
# ---------------------------------------------------------------------------


def test_no_active_run_source_raises_no_active_run_source_error():
    with pytest.raises(tools_run.NoActiveRunSourceError, match="no_active_run_source"):
        tools_run._require_active_run_source()


def test_active_run_source_pointing_at_a_missing_file_raises_file_not_found(tmp_path: Path):
    tools_run.set_active_run_source(tmp_path / "does_not_exist.inpx")

    with pytest.raises(FileNotFoundError):
        tools_run._require_active_run_source()


def test_set_get_clear_active_run_source_round_trip(tmp_path: Path):
    source = tmp_path / "working.inpx"
    source.write_text("<net/>", encoding="utf-8")

    assert tools_run.get_active_run_source() is None
    tools_run.set_active_run_source(source)
    assert tools_run.get_active_run_source() == source
    tools_run.clear_active_run_source()
    assert tools_run.get_active_run_source() is None


# ---------------------------------------------------------------------------
# _job_timeout_s — pure function
# ---------------------------------------------------------------------------


def test_job_timeout_scales_with_duration_and_seed_count():
    one_seed = tools_run._job_timeout_s(300, 1)
    two_seeds = tools_run._job_timeout_s(300, 2)
    longer_run = tools_run._job_timeout_s(3600, 1)

    assert two_seeds == pytest.approx(one_seed * 2)
    assert longer_run > one_seed
    assert one_seed > 0


# ---------------------------------------------------------------------------
# _summarize_kpis — real results_reader, degrading gracefully
# ---------------------------------------------------------------------------


def test_summarize_kpis_reads_network_metrics_from_a_full_results_db(tmp_path: Path):
    work_dir = tmp_path / "job"
    work_dir.mkdir()
    _write_full_results_db(work_dir / "run_001.db", delay_avg=27.5)

    summary = tools_run._summarize_kpis(work_dir)

    assert summary is not None
    assert summary["run_count"] == 1
    assert summary["metrics"]["avg_delay_s"] == 27.5
    assert summary["metrics"]["avg_speed_kmh"] == 38.2


def test_summarize_kpis_degrades_to_none_when_only_simulationrun_table_exists(tmp_path: Path):
    """The exact M2.4 acceptance scenario: a run too short for evaluation
    intervals produces a .db with ONLY SIMULATIONRUN — results_reader's own
    per-db try/except skips the unreadable db, the aggregate has no runs,
    and this degrades to None rather than raising."""
    work_dir = tmp_path / "job"
    work_dir.mkdir()
    _write_simulationrun_only_db(work_dir / "run_001.db")

    summary = tools_run._summarize_kpis(work_dir)

    assert summary is None


def test_summarize_kpis_no_db_at_all_returns_none(tmp_path: Path):
    work_dir = tmp_path / "empty_job"
    work_dir.mkdir()

    assert tools_run._summarize_kpis(work_dir) is None


# ---------------------------------------------------------------------------
# run_simulation — full tool dispatch, fake job runner
# ---------------------------------------------------------------------------


def _source_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "working.inpx"
    path.write_text("<net/>", encoding="utf-8")
    return path


def test_run_simulation_clean_run_returns_ok_envelope_with_kpis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _source_inpx(tmp_path)
    tools_run.set_active_run_source(source)

    job_work_dir = tmp_path / "job-clean"
    job_work_dir.mkdir()
    _write_full_results_db(job_work_dir / "run_001.db")
    monkeypatch.setattr(simulation_runs, "job_dir", lambda job_id: job_work_dir)

    fake_runner = _FakeJobRunner(
        {
            "id": "fake-job-1",
            "status": "done",
            "result_tables": [
                "SIMULATIONRUN",
                "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS",
            ],
        }
    )
    monkeypatch.setattr(tools_run, "_run_job_and_wait", fake_runner)

    envelope = dispatch("run_simulation", {"duration_s": 3600}, registry=default_registry)

    assert envelope["status"] == "ok"
    assert envelope["error_type"] is None
    assert envelope["verification"] == {
        "job_id": "fake-job-1",
        "simulationrun_verified": True,
        "duration_s": 3600,
        "random_seeds": [None],
        "tables": [
            "SIMULATIONRUN",
            "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS",
        ],
    }
    assert envelope["data"]["run_result"] == {
        "tables": [
            "SIMULATIONRUN",
            "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS",
        ],
        "deleted_vehicles": None,
    }
    assert envelope["data"]["kpis"]["metrics"]["avg_delay_s"] == 32.4

    # The fake runner received the SESSION's active source, the exact
    # duration, and the default one-seed list ([None] -- "don't override").
    assert len(fake_runner.calls) == 1
    assert fake_runner.calls[0]["source_inpx"] == source
    assert fake_runner.calls[0]["duration_s"] == 3600
    assert fake_runner.calls[0]["random_seeds"] == [None]
    assert fake_runner.calls[0]["timeout_s"] > 0


def test_run_simulation_short_run_reports_missing_kpi_tables_and_no_kpis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A 5-minute run: the job runner marks it "done" (SIMULATIONRUN row
    present), but the results .db carries ONLY that table -- run_result and
    the KPI summary must both reflect that honestly, since this is the exact
    shape guardrails.postrun's results_missing_kpi_tables detector fires on."""
    source = _source_inpx(tmp_path)
    tools_run.set_active_run_source(source)

    job_work_dir = tmp_path / "job-short"
    job_work_dir.mkdir()
    _write_simulationrun_only_db(job_work_dir / "run_001.db")
    monkeypatch.setattr(simulation_runs, "job_dir", lambda job_id: job_work_dir)

    fake_runner = _FakeJobRunner(
        {"id": "fake-job-2", "status": "done", "result_tables": ["SIMULATIONRUN"]}
    )
    monkeypatch.setattr(tools_run, "_run_job_and_wait", fake_runner)

    envelope = dispatch("run_simulation", {"duration_s": 300}, registry=default_registry)

    assert envelope["status"] == "ok"
    assert envelope["data"]["run_result"] == {"tables": ["SIMULATIONRUN"], "deleted_vehicles": None}
    assert envelope["data"]["kpis"] is None


def test_run_simulation_multi_seed_submits_one_job_with_all_seeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _source_inpx(tmp_path)
    tools_run.set_active_run_source(source)

    job_work_dir = tmp_path / "job-multiseed"
    job_work_dir.mkdir()
    _write_full_results_db(job_work_dir / "run_001.db")
    monkeypatch.setattr(simulation_runs, "job_dir", lambda job_id: job_work_dir)

    fake_runner = _FakeJobRunner(
        {
            "id": "fake-job-3",
            "status": "done",
            "result_tables": [
                "SIMULATIONRUN",
                "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS",
            ],
        }
    )
    monkeypatch.setattr(tools_run, "_run_job_and_wait", fake_runner)

    envelope = dispatch(
        "run_simulation", {"duration_s": 600, "random_seeds": [1, 2, 3]}, registry=default_registry
    )

    assert envelope["status"] == "ok"
    assert fake_runner.calls[0]["random_seeds"] == [1, 2, 3]
    assert envelope["verification"]["random_seeds"] == [1, 2, 3]
    assert "3 seed(s)" in envelope["message"]
    # ONE job submitted, not three -- see tools_run.py's module docstring on
    # why multi-seed is one job with N sequential sub-runs.
    assert len(fake_runner.calls) == 1


def test_run_simulation_no_active_source_is_a_tool_execution_error(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_runner = _FakeJobRunner({"id": "unused", "status": "done", "result_tables": []})
    monkeypatch.setattr(tools_run, "_run_job_and_wait", fake_runner)

    envelope = dispatch("run_simulation", {"duration_s": 300}, registry=default_registry)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_active_run_source" in envelope["message"]
    assert fake_runner.calls == []  # never reached the job runner


def test_run_simulation_job_error_status_raises_tool_execution_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _source_inpx(tmp_path)
    tools_run.set_active_run_source(source)
    fake_runner = _FakeJobRunner(
        {"id": "fake-job-4", "status": "error", "error": "VISSIM COM raised during load_network"}
    )
    monkeypatch.setattr(tools_run, "_run_job_and_wait", fake_runner)

    envelope = dispatch("run_simulation", {"duration_s": 300}, registry=default_registry)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "fake-job-4" in envelope["message"]
    assert "VISSIM COM raised during load_network" in envelope["message"]


def test_run_simulation_done_status_but_no_simulationrun_row_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Defense in depth (module docstring): a claimed "done" status is never
    trusted alone -- even if a future job-runner regression marks a job
    done without a verified SIMULATIONRUN row, this tool must still refuse
    rather than report a phantom completed run."""
    source = _source_inpx(tmp_path)
    tools_run.set_active_run_source(source)
    fake_runner = _FakeJobRunner(
        {"id": "fake-job-5", "status": "done", "result_tables": ["SOMETHING_ELSE"]}
    )
    monkeypatch.setattr(tools_run, "_run_job_and_wait", fake_runner)

    envelope = dispatch("run_simulation", {"duration_s": 300}, registry=default_registry)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "SIMULATIONRUN" in envelope["message"]


# ---------------------------------------------------------------------------
# Params validation — strict, matches the anti-drift contract (toolkit.py)
# ---------------------------------------------------------------------------


def test_run_simulation_rejects_unknown_params():
    envelope = dispatch(
        "run_simulation", {"duration_s": 300, "bogus": 1}, registry=default_registry
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_run_simulation_rejects_missing_duration_s():
    envelope = dispatch("run_simulation", {}, registry=default_registry)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_run_simulation_random_seeds_defaults_to_none():
    params = tools_run.RunSimulationParams(duration_s=300)

    assert params.random_seeds is None

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
import services.results_evidence as results_evidence
import services.simulation_runs as simulation_runs
from services.agent import constraints
from services.agent.guardrails import GuardrailEngine
from services.agent.toolkit import default_registry, dispatch

# The run_result keys that existed BEFORE unit U1. Unit U1 only adds keys;
# these four keep their exact pre-U1 values, which
# test_run_result_four_pre_existing_keys_are_unchanged_by_unit_u1 pins.
_BASE_RUN_RESULT_KEYS = ("tables", "deleted_vehicles", "evaluation_configured", "trajectory_recorded")

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


def _write_full_results_db(
    db_path: Path,
    *,
    delay_avg: float = 32.4,
    movements: "list[tuple[str, float | None, float | None]] | None" = None,
    queues: "list[tuple[str, float | None, float | None]] | None" = None,
) -> None:
    """A minimal but SCHEMA-COMPLETE results .db: every table
    ``results_reader._read_db`` unconditionally queries (not behind
    ``_query_rows_with_fallback``) exists, so the network aggregate is
    actually produced instead of the whole db being silently skipped.

    ``movements`` (``[(OBJECT_ID, VEHS, VEHDELAY), ...]``) and ``queues``
    (``[(OBJECT_ID, QLEN, QLENMAX), ...]``) populate the two node-results
    tables unit U1's evidence keys are read from. Both default to EMPTY —
    the table is still created, which is the real ``ARMED_BUT_EMPTY`` shape
    (measured on 48 vendor-example result .dbs in this checkout), not the
    same thing as the table being absent."""
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

        for object_id, vehs, vehdelay in movements or ():
            conn.execute(
                "INSERT INTO MOVEMENT_EvaluationTimeIntervalClass VALUES (?, 'ALL', ?, ?, 0, 0)",
                (object_id, vehs, vehdelay),
            )
        for object_id, qlen, qlenmax in queues or ():
            conn.execute(
                "INSERT INTO MOVEMENT_EvaluationTimeInterval VALUES (?, ?, ?, 0, 0)",
                (object_id, qlen, qlenmax),
            )
        conn.commit()
    finally:
        conn.close()


def _write_movement_tableless_db(db_path: Path) -> None:
    """A results .db with KPI tables but NO ``MOVEMENT_EvaluationTimeInterval``
    at all — the shape 17 real vendor .dbs in this checkout have.

    Deliberately distinct from ``_write_full_results_db`` with empty
    ``queues``: there the table exists and is empty (``ARMED_BUT_EMPTY``),
    here it does not exist (``TABLE_ABSENT``). Collapsing those two is
    exactly what unit U1's descriptor refuses to do."""
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
            "VALUES ('ALL', 32.4, 1.6, 38.2, 21.0, 6480000.0, 52000.0, 19400.0, 310.0, 5050.0)"
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
            "evaluation_configured": True,
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
        # Disclosed alongside random_seeds on purpose (2026-08-07 numRuns
        # finding): one requested seed, one result database. When those two
        # numbers disagree, the envelope now says so instead of reporting the
        # seed list as though it were the run count.
        "result_db_count": 1,
        "tables": [
            "SIMULATIONRUN",
            "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS",
        ],
        "evaluation_configured": True,
        "trajectory_recorded": False,
    }
    # The four pre-U1 keys, pinned byte-identically (unit U1 only ADDS keys —
    # see test_run_result_four_pre_existing_keys_are_unchanged_by_unit_u1).
    run_result = envelope["data"]["run_result"]
    assert {key: run_result[key] for key in _BASE_RUN_RESULT_KEYS} == {
        "tables": [
            "SIMULATIONRUN",
            "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS",
        ],
        "deleted_vehicles": None,
        "evaluation_configured": True,
        "trajectory_recorded": False,
    }
    assert envelope["data"]["kpis"]["metrics"]["avg_delay_s"] == 32.4
    assert "network-performance evaluation configured" in envelope["message"]

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
    shape guardrails.postrun's results_missing_kpi_tables detector fires on.

    ``evaluation_configured`` is still True here -- the job runner now
    hard-fails the whole job (never reaches "done") if configuration itself
    failed, so a "done"-but-KPI-less run means "configured, but the window
    was too short for any interval to elapse", a DIFFERENT failure mode from
    "never configured at all". results_missing_kpi_tables (not
    evaluation_not_configured_before_run) is the detector for THIS shape."""
    source = _source_inpx(tmp_path)
    tools_run.set_active_run_source(source)

    job_work_dir = tmp_path / "job-short"
    job_work_dir.mkdir()
    _write_simulationrun_only_db(job_work_dir / "run_001.db")
    monkeypatch.setattr(simulation_runs, "job_dir", lambda job_id: job_work_dir)

    fake_runner = _FakeJobRunner(
        {
            "id": "fake-job-2",
            "status": "done",
            "result_tables": ["SIMULATIONRUN"],
            "evaluation_configured": True,
        }
    )
    monkeypatch.setattr(tools_run, "_run_job_and_wait", fake_runner)

    envelope = dispatch("run_simulation", {"duration_s": 300}, registry=default_registry)

    assert envelope["status"] == "ok"
    run_result = envelope["data"]["run_result"]
    assert {key: run_result[key] for key in _BASE_RUN_RESULT_KEYS} == {
        "tables": ["SIMULATIONRUN"],
        "deleted_vehicles": None,
        "evaluation_configured": True,
        "trajectory_recorded": False,
    }
    assert envelope["data"]["kpis"] is None
    # A SIMULATIONRUN-only .db has none of the evaluation tables, so unit U1's
    # evidence keys are OMITTED rather than defaulted (see the absence-honesty
    # test below for the full statement of that rule).
    for key in ("queue_results", "los_by_movement", "movement_volumes", "delay_segments"):
        assert key not in run_result


def test_run_simulation_missing_evaluation_configured_field_defaults_to_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Defensive default (module comment in tools_run.py): a "done" job
    record that omits ``evaluation_configured`` entirely (e.g. written by a
    future/older job-runner variant) must read as "not proven configured"
    here, never silently as True -- this is what lets
    evaluation_not_configured_before_run's postrun check actually fire."""
    source = _source_inpx(tmp_path)
    tools_run.set_active_run_source(source)

    job_work_dir = tmp_path / "job-no-eval-field"
    job_work_dir.mkdir()
    _write_simulationrun_only_db(job_work_dir / "run_001.db")
    monkeypatch.setattr(simulation_runs, "job_dir", lambda job_id: job_work_dir)

    fake_runner = _FakeJobRunner(
        {"id": "fake-job-6", "status": "done", "result_tables": ["SIMULATIONRUN"]}
    )
    monkeypatch.setattr(tools_run, "_run_job_and_wait", fake_runner)

    envelope = dispatch("run_simulation", {"duration_s": 300}, registry=default_registry)

    assert envelope["status"] == "ok"
    assert envelope["data"]["run_result"]["evaluation_configured"] is False
    assert envelope["verification"]["evaluation_configured"] is False
    assert "NOT configured" in envelope["message"]


def test_run_simulation_surfaces_trajectory_recorded_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """trajectory_recorded (the real-run-replay unit) flows from the job
    record through run_result/verification/message -- read directly off
    the job runner's own field, never re-derived."""
    source = _source_inpx(tmp_path)
    tools_run.set_active_run_source(source)

    job_work_dir = tmp_path / "job-with-trajectory"
    job_work_dir.mkdir()
    _write_full_results_db(job_work_dir / "run_001.db")
    monkeypatch.setattr(simulation_runs, "job_dir", lambda job_id: job_work_dir)

    fake_runner = _FakeJobRunner(
        {
            "id": "fake-job-traj",
            "status": "done",
            "result_tables": [
                "SIMULATIONRUN",
                "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS",
            ],
            "evaluation_configured": True,
            "trajectory_recorded": True,
            "trajectory_paths": [str(job_work_dir / "run_001.fzp")],
        }
    )
    monkeypatch.setattr(tools_run, "_run_job_and_wait", fake_runner)

    envelope = dispatch("run_simulation", {"duration_s": 60}, registry=default_registry)

    assert envelope["status"] == "ok"
    assert envelope["data"]["run_result"]["trajectory_recorded"] is True
    assert envelope["verification"]["trajectory_recorded"] is True
    assert "trajectories recorded" in envelope["message"]


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


# ===========================================================================
# Unit U1 — run_result carries the evidence the postrun catalogue asks for
# ===========================================================================
#
# Five postrun constraints in constraints.py were written against run_result
# fields nothing produced, so they could never fire on a real run. U1 supplies
# four of them. Everything below is offline: sqlite fixtures, an .inpx written
# as text, and (for the read-only reproduction test) the developer's own
# already-finished result databases opened `mode=ro&immutable=1`.

# Real OBJECT_IDs and real QLENMAX values, lifted from
# runtime/workspaces/.../Scenarios/S000001/S000001.results/1.db so the
# synthetic fixture is shaped like the thing it stands in for. Node token is
# the leading "1"; 119.4837 m and 135.2885 m both exceed Vissim's 100 m
# nodesDelaySegmentStart default, which is what makes the delay measurement
# truncation real rather than hypothetical.
_MOVEMENT_BUSY = "1-508-100.779980-510-62.109716"
_MOVEMENT_GRIDLOCKED = "1-508-100.779980-632-22.519481"
_QLENMAX_BUSY = 119.4837
_QLENMAX_GRIDLOCKED = 135.2885

_TABLES_WITH_NODE_RESULTS = [
    "SIMULATIONRUN",
    "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS",
    "MOVEMENT_EVALUATIONTIMEINTERVAL",
    "MOVEMENT_EVALUATIONTIMEINTERVALCLASS",
    "VEHICLETRAVELTIMEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS",
]


def _write_inpx(
    path: Path,
    *,
    queue_definition: bool = True,
    delay_segment_start_m: float | None = 100.0,
    node_results_interval_s: float | None = None,
    node_results_armed: bool = True,
) -> Path:
    """An .inpx carrying just the ``<evaluation>`` element U1 reads.

    Attribute names and values are copied from real networks in this
    checkout: the vendor examples carry ``queueBeginSpeed="3"
    queueEndSpeed="6" queueMaxClear="10" queueMaxLength="500"``, and every
    network measured (vendor and customer alike) carries
    ``nodesDelaySegmentStart="100"``."""
    attrs: list[str] = []
    if delay_segment_start_m is not None:
        attrs.append(f'nodesDelaySegmentStart="{delay_segment_start_m}"')
    if queue_definition:
        attrs += [
            'queueBeginSpeed="3"',
            'queueEndSpeed="6"',
            'queueMaxClear="10"',
            'queueMaxLength="500"',
            'queueConsiderAdjacentLanes="true"',
        ]
    node_results = ""
    if node_results_interval_s is not None:
        node_results = (
            f'<nodeResults collectData="{str(node_results_armed).lower()}" fromTime="900" '
            f'interval="{node_results_interval_s}" toTime="4500"/>'
        )
    path.write_text(
        f"<network><evaluation {' '.join(attrs)}>{node_results}</evaluation></network>",
        encoding="utf-8",
    )
    return path


def _run_and_get_run_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    job_work_dir: Path,
    source: Path,
    tables: list[str],
    duration_s: int = 3600,
) -> dict[str, Any]:
    tools_run.set_active_run_source(source)
    monkeypatch.setattr(simulation_runs, "job_dir", lambda job_id: job_work_dir)
    monkeypatch.setattr(
        tools_run,
        "_run_job_and_wait",
        _FakeJobRunner(
            {
                "id": "u1-job",
                "status": "done",
                "result_tables": tables,
                "evaluation_configured": True,
            }
        ),
    )
    envelope = dispatch(
        "run_simulation", {"duration_s": duration_s}, registry=default_registry
    )
    assert envelope["status"] == "ok", envelope
    return envelope["data"]["run_result"]


def _unhealthy_run_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A run whose evidence trips all three newly-firing constraints.

    * a zero-throughput movement Vissim graded LOS A (VEHS 0, VEHDELAY 0);
    * queue numbers present with NO defining parameters in the .inpx;
    * queues longer than the 100 m delay-measurement segment.
    """
    work = tmp_path / "job-unhealthy"
    work.mkdir()
    _write_full_results_db(
        work / "run_001.db",
        movements=[(_MOVEMENT_BUSY, 1134.0, 44.839438562360534), (_MOVEMENT_GRIDLOCKED, 0.0, 0.0)],
        queues=[(_MOVEMENT_BUSY, 12.0, _QLENMAX_BUSY), (_MOVEMENT_GRIDLOCKED, 30.0, _QLENMAX_GRIDLOCKED)],
    )
    source = _write_inpx(tmp_path / "unhealthy.inpx", queue_definition=False)
    return _run_and_get_run_result(
        tmp_path,
        monkeypatch,
        job_work_dir=work,
        source=source,
        tables=_TABLES_WITH_NODE_RESULTS,
    )


def _healthy_run_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The same evidence keys, all of them clean: every movement carries
    throughput, the queue definition IS attached, and no queue outruns the
    delay-measurement segment."""
    work = tmp_path / "job-healthy"
    work.mkdir()
    _write_full_results_db(
        work / "run_001.db",
        movements=[(_MOVEMENT_BUSY, 1134.0, 44.8), (_MOVEMENT_GRIDLOCKED, 510.0, 10.28)],
        queues=[(_MOVEMENT_BUSY, 12.0, 41.5), (_MOVEMENT_GRIDLOCKED, 8.0, 22.25)],
    )
    source = _write_inpx(tmp_path / "healthy.inpx", queue_definition=True)
    return _run_and_get_run_result(
        tmp_path,
        monkeypatch,
        job_work_dir=work,
        source=source,
        tables=_TABLES_WITH_NODE_RESULTS,
    )


# ---------------------------------------------------------------------------
# (a) The actual values reproduce — not a summary of them
# ---------------------------------------------------------------------------


def test_run_result_reproduces_actual_queue_los_and_volume_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    run_result = _unhealthy_run_result(tmp_path, monkeypatch)

    # queue_results is a MAPPING keyed by OBJECT_ID —
    # queue_length_definition_not_portable calls .keys() on it.
    queue_results = run_result["queue_results"]
    assert sorted(queue_results.keys()) == sorted([_MOVEMENT_BUSY, _MOVEMENT_GRIDLOCKED])
    assert queue_results[_MOVEMENT_BUSY]["qlen"] == 12.0
    assert queue_results[_MOVEMENT_BUSY]["qlenmax"] == _QLENMAX_BUSY
    assert queue_results[_MOVEMENT_GRIDLOCKED]["qlen"] == 30.0
    assert queue_results[_MOVEMENT_GRIDLOCKED]["qlenmax"] == _QLENMAX_GRIDLOCKED

    # los_by_movement matches gridlock_reports_los_a's expected shape exactly:
    # a list of {"movement", "los", "volume"} (plus the delay it was banded
    # from, which that constraint's own message asks for).
    by_movement = {entry["movement"]: entry for entry in run_result["los_by_movement"]}
    assert by_movement[_MOVEMENT_BUSY]["volume"] == 1134.0
    assert by_movement[_MOVEMENT_BUSY]["los"] == "D"  # 44.84 s falls in 35 < d <= 55
    assert by_movement[_MOVEMENT_GRIDLOCKED]["volume"] == 0.0
    assert by_movement[_MOVEMENT_GRIDLOCKED]["los"] == "A"
    assert by_movement[_MOVEMENT_GRIDLOCKED]["delay_s"] == 0.0

    # movement_volumes exposes the node token — the key that later lets a
    # reality-check compare the RUN against observations.
    assert run_result["movement_volumes"][_MOVEMENT_BUSY] == {"vehs": 1134.0, "node": "1"}

    # delay_segments pairs the .inpx segment start with the node's worst queue.
    assert run_result["delay_segments"] == [
        {"node": "1", "segment_length_m": 100.0, "observed_queue_m": _QLENMAX_GRIDLOCKED}
    ]


def test_run_result_carries_the_queue_definition_read_from_the_inpx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    run_result = _healthy_run_result(tmp_path, monkeypatch)

    assert run_result["queue_definition"] == {
        "begin_speed": 3.0,
        "end_speed": 6.0,
        "max_clear": 10.0,
        "max_length": 500.0,
        "consider_adjacent_lanes": True,
    }


def test_evaluation_interval_is_the_interval_the_run_was_armed_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """NOT the .inpx's own ``<nodeResults interval=...>``.

    ``VissimController.configure_network_performance_evaluation`` overwrites
    every evaluation type's interval with ``max(int(duration_s), 1)`` before
    RunContinuous, so for an agent run (``evaluation_configured`` True) the
    network's stored interval is not the one the run used — reporting it
    would be a quiet lie. The .inpx below deliberately says 3600 while the
    run asks for 900."""
    work = tmp_path / "job-interval"
    work.mkdir()
    _write_full_results_db(work / "run_001.db")
    source = _write_inpx(tmp_path / "interval.inpx", node_results_interval_s=3600)

    run_result = _run_and_get_run_result(
        tmp_path,
        monkeypatch,
        job_work_dir=work,
        source=source,
        tables=_TABLES_WITH_NODE_RESULTS,
        duration_s=900,
    )

    assert run_result["evaluation_interval_s"] == 900.0


# ---------------------------------------------------------------------------
# (b) The delta measurement — N -> N+k through the REAL postrun entry point
# ---------------------------------------------------------------------------

_NEWLY_FIRING = (
    "gridlock_reports_los_a",
    "queue_length_definition_not_portable",
    "delay_measurement_truncated_by_queue",
)


def _postrun_ids(run_result: dict[str, Any]) -> list[str]:
    """Drive ``GuardrailEngine.postrun`` — the same method orchestrator.py
    calls — and return the constraint ids that fired."""
    report = GuardrailEngine(disabled=False).postrun(
        {"tool": "run_simulation", "params": {"duration_s": 3600}}, run_result
    )
    return sorted(finding.constraint_id for finding in report.findings)


def test_postrun_delta_three_constraints_move_from_never_firing_to_firing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The measurement this unit exists to produce: N -> N + k, k >= 3.

    "Before" is not a story about HEAD — it is HEAD's literal run_result,
    reconstructed by taking only the four keys HEAD produced from the very
    same run, and pushed through the very same entry point."""
    run_result = _unhealthy_run_result(tmp_path, monkeypatch)

    head_run_result = {key: run_result[key] for key in _BASE_RUN_RESULT_KEYS}
    before = _postrun_ids(head_run_result)
    after = _postrun_ids(run_result)

    # Measured on this fixture: N = 0 -> N + 3 = 3.
    assert before == []
    assert len(after) == len(before) + 3
    for constraint_id in _NEWLY_FIRING:
        assert constraint_id not in before
        assert constraint_id in after


@pytest.mark.parametrize("constraint_id", _NEWLY_FIRING)
def test_each_newly_firing_constraint_returns_none_at_head_and_a_finding_now(
    constraint_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Per-constraint, at the check-function level: ``None`` on HEAD's
    run_result, a real ``Finding`` on U1's."""
    run_result = _unhealthy_run_result(tmp_path, monkeypatch)
    head_run_result = {key: run_result[key] for key in _BASE_RUN_RESULT_KEYS}

    check = {c.id: c.check for c in constraints.get_registry()}[constraint_id]
    action = {"tool": "run_simulation", "params": {"duration_s": 3600}}

    assert check(action, {}, {"run_result": head_run_result}) is None
    finding = check(action, {}, {"run_result": run_result})
    assert finding is not None
    assert finding.constraint_id == constraint_id


def test_none_of_the_three_fire_on_a_healthy_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The other half of the delta. Supplying evidence that makes three
    detectors reachable is only useful if they stay silent when there is
    nothing to detect."""
    run_result = _healthy_run_result(tmp_path, monkeypatch)

    fired = _postrun_ids(run_result)

    assert fired == []
    # ...and not because the evidence was missing.
    assert run_result["queue_results"]
    assert run_result["queue_definition"]
    assert run_result["los_by_movement"]
    assert run_result["delay_segments"]


# ---------------------------------------------------------------------------
# (c) Absence honesty — TABLE_ABSENT omits the key. Not None. Not defaulted.
# ---------------------------------------------------------------------------


def test_absent_movement_table_omits_the_key_entirely_and_the_constraint_stays_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A .db with no ``MOVEMENT_EvaluationTimeInterval`` at all.

    ``queue_results`` must be ABSENT from run_result — not ``None``, not
    ``{}``. A defaulted value is a claim ("we looked, there were no queues");
    an omitted key is the truth ("this run has no opinion about queues").
    This test must not be softened into ``.get(...) in (None, {})``."""
    work = tmp_path / "job-no-movement-table"
    work.mkdir()
    _write_movement_tableless_db(work / "run_001.db")
    source = _write_inpx(tmp_path / "no-movement.inpx")

    run_result = _run_and_get_run_result(
        tmp_path,
        monkeypatch,
        job_work_dir=work,
        source=source,
        tables=["SIMULATIONRUN", "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS"],
    )

    assert "queue_results" not in run_result
    assert "delay_segments" not in run_result
    assert "los_by_movement" not in run_result
    assert "movement_volumes" not in run_result
    sentinel = object()
    assert run_result.get("queue_results", sentinel) is sentinel

    assert (
        run_result["evidence"][results_evidence.MOVEMENT_INTERVAL_TABLE]
        == results_evidence.TABLE_ABSENT
    )
    assert _postrun_ids(run_result) == []


def test_armed_but_empty_keeps_the_key_with_an_explicit_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The other side of absence honesty: the evaluation WAS armed and
    collected nothing. The key stays, carrying a marker that says so — and
    the marker is falsy, because every consumer guards with ``if not
    payload`` and would otherwise manufacture a finding out of the marker
    itself."""
    work = tmp_path / "job-armed-empty"
    work.mkdir()
    _write_full_results_db(work / "run_001.db")  # tables created, zero rows
    source = _write_inpx(tmp_path / "armed-empty.inpx")

    run_result = _run_and_get_run_result(
        tmp_path,
        monkeypatch,
        job_work_dir=work,
        source=source,
        tables=_TABLES_WITH_NODE_RESULTS,
    )

    assert "queue_results" in run_result
    marker = run_result["queue_results"]
    assert not marker
    assert marker.state == results_evidence.ARMED_BUT_EMPTY
    assert marker.source == results_evidence.MOVEMENT_INTERVAL_TABLE
    assert "collected zero rows" in marker.reason
    assert isinstance(run_result["los_by_movement"], results_evidence.EmptyEvidenceList)
    assert isinstance(run_result["delay_segments"], results_evidence.EmptyEvidenceList)

    assert (
        run_result["evidence"][results_evidence.MOVEMENT_INTERVAL_TABLE]
        == results_evidence.ARMED_BUT_EMPTY
    )
    # A falsy marker must not become a phantom finding.
    assert _postrun_ids(run_result) == []

    # run_result rides inside the tool envelope, which the orchestrator
    # serialises onto the NDJSON stream. The marker degrades to {} / [] there
    # -- stated, not hidden: the JSON-durable copy of the same fact is the
    # "evidence" descriptor, which is why it exists.
    import json

    round_tripped = json.loads(json.dumps(run_result))
    assert round_tripped["queue_results"] == {}
    assert round_tripped["los_by_movement"] == []
    assert (
        round_tripped["evidence"][results_evidence.MOVEMENT_INTERVAL_TABLE]
        == results_evidence.ARMED_BUT_EMPTY
    )


# ---------------------------------------------------------------------------
# (d) Tri-state discrimination — the point of the descriptor
# ---------------------------------------------------------------------------


def test_populated_and_armed_but_empty_dbs_do_not_read_alike(tmp_path: Path):
    """A plain SELECT collapses "63 real queue rows" and "table exists, zero
    rows" into the same ``[]``. describe_result_db must not."""
    populated = tmp_path / "populated.db"
    _write_full_results_db(
        populated,
        movements=[(_MOVEMENT_BUSY, 1134.0, 44.8)],
        queues=[(_MOVEMENT_BUSY, 12.0, _QLENMAX_BUSY), (_MOVEMENT_GRIDLOCKED, 30.0, _QLENMAX_GRIDLOCKED)],
    )
    armed_but_empty = tmp_path / "armed_but_empty.db"
    _write_full_results_db(armed_but_empty)
    absent = tmp_path / "absent.db"
    _write_movement_tableless_db(absent)

    table = results_evidence.MOVEMENT_INTERVAL_TABLE
    populated_state = results_evidence.describe_result_db(populated)[table]
    empty_state = results_evidence.describe_result_db(armed_but_empty)[table]
    absent_state = results_evidence.describe_result_db(absent)[table]

    assert populated_state == "PRESENT(2)"
    assert results_evidence.rows_in(populated_state) == 2
    assert empty_state == results_evidence.ARMED_BUT_EMPTY
    assert absent_state == results_evidence.TABLE_ABSENT
    assert len({populated_state, empty_state, absent_state}) == 3
    # ARMED_BUT_EMPTY is not a row count of zero — asking for one says None.
    assert results_evidence.rows_in(empty_state) is None
    assert results_evidence.rows_in(absent_state) is None


def test_descriptor_merges_multi_seed_result_dirs(tmp_path: Path):
    """A multi-seed job writes one .db per seed. Row counts add up; a table
    populated in ANY seed is PRESENT for the run."""
    work = tmp_path / "job"
    (work / "seed_00").mkdir(parents=True)
    (work / "seed_01").mkdir(parents=True)
    _write_full_results_db(work / "seed_00" / "1.db", queues=[(_MOVEMENT_BUSY, 1.0, 2.0)])
    _write_full_results_db(work / "seed_01" / "1.db")  # armed, empty

    descriptor = results_evidence.describe_result_dir(work)

    assert descriptor[results_evidence.MOVEMENT_INTERVAL_TABLE] == "PRESENT(1)"
    assert descriptor[results_evidence.QUEUECOUNTER_INTERVAL_TABLE] == results_evidence.TABLE_ABSENT


# ---------------------------------------------------------------------------
# (e) Contract pin — the four pre-existing keys are untouched
# ---------------------------------------------------------------------------


def test_run_result_four_pre_existing_keys_are_unchanged_by_unit_u1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """U1 is plumbing: it ADDS keys and changes nothing that was already
    there. Pinned literally, on a fixture that exercises every new code path
    at once.

    Re-affirmed 2026-08-07 when ``result_db_count`` was added: it is another
    ADDED key, collected by ``_collect_run_evidence`` and therefore appended
    by the same ``run_result.update(...)`` call, so the four base keys keep
    both their values and their leading positions."""
    run_result = _unhealthy_run_result(tmp_path, monkeypatch)

    assert {key: run_result[key] for key in _BASE_RUN_RESULT_KEYS} == {
        "tables": sorted(_TABLES_WITH_NODE_RESULTS),
        "deleted_vehicles": None,
        "evaluation_configured": True,
        "trajectory_recorded": False,
    }
    assert list(run_result)[:4] == list(_BASE_RUN_RESULT_KEYS)
    # Additive, and after the four -- never interleaved with them.
    assert run_result["result_db_count"] == 1
    assert "result_db_count" in list(run_result)[4:]


# ---------------------------------------------------------------------------
# (e2) result_db_count — the run says how many result sets it actually wrote
#
# MEASURED 2026-08-07 (runtime/calibration-loop-live-check/artifacts/
# FINDING_agent_run_numRuns_blocker.json): a single-seed agent run of the real
# consultant model wrote FIVE result databases into ONE seed directory
# (<simulation numRuns="5" randSeed="42" randSeedIncr="123"> + RunContinuous),
# and the envelope still reported random_seeds=[42] / simulationrun_verified=
# true -- the multiplicity was invisible to the caller while the evidence
# readers silently summed five runs' volumes. Both causes are fixed (state.py
# _force_single_run at clone time; routers/simulations.py NumRuns=1 + read-back
# before RunContinuous); this disclosure exists so a future recurrence by any
# other route is READ, not inferred.
# ---------------------------------------------------------------------------


def _dispatch_and_get_envelope(
    monkeypatch: pytest.MonkeyPatch,
    *,
    job_work_dir: Path,
    source: Path,
    random_seeds: list[int] | None = None,
    job_id: str | None = "dbcount-job",
) -> dict[str, Any]:
    tools_run.set_active_run_source(source)
    monkeypatch.setattr(simulation_runs, "job_dir", lambda _job_id: job_work_dir)
    record: dict[str, Any] = {
        "status": "done",
        "result_tables": _TABLES_WITH_NODE_RESULTS,
        "evaluation_configured": True,
    }
    if job_id is not None:
        record["id"] = job_id
    monkeypatch.setattr(tools_run, "_run_job_and_wait", _FakeJobRunner(record))
    params: dict[str, Any] = {"duration_s": 3600}
    if random_seeds is not None:
        params["random_seeds"] = random_seeds
    envelope = dispatch("run_simulation", params, registry=default_registry)
    assert envelope["status"] == "ok", envelope
    return envelope


def test_envelope_discloses_the_real_db_count_when_one_seed_wrote_five(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The measured failure shape, reproduced on disk: ONE requested seed,
    five result databases in that seed's own results directory.

    The count must appear in run_result, in verification next to the
    single-entry random_seeds list it contradicts, and in the message -- so
    the disagreement is legible rather than something a reader has to go
    count files to discover."""
    work = tmp_path / "job-five-dbs"
    results_dir = work / "seed_00" / "working.results"
    results_dir.mkdir(parents=True)
    for run_no in range(1, 6):
        _write_full_results_db(
            results_dir / f"{run_no}.db",
            movements=[(_MOVEMENT_BUSY, 378.0, 44.8)],
            queues=[(_MOVEMENT_BUSY, 12.0, 41.5)],
        )
    source = _write_inpx(tmp_path / "five.inpx")

    envelope = _dispatch_and_get_envelope(
        monkeypatch, job_work_dir=work, source=source, random_seeds=[42]
    )

    assert envelope["data"]["run_result"]["result_db_count"] == 5
    assert envelope["verification"]["result_db_count"] == 5
    assert envelope["verification"]["random_seeds"] == [42]
    assert "1 seed(s), writing 5 result database(s)" in envelope["message"]
    # The disclosure is what changed; the summed-volume behaviour it exposes
    # is unchanged (5 x 378 vehicles) -- stated here so a future reader can
    # see WHY the count matters rather than taking it on faith.
    assert envelope["data"]["run_result"]["movement_volumes"][_MOVEMENT_BUSY]["vehs"] == 1890.0


def test_envelope_discloses_one_result_db_for_a_normal_single_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The fixed shape: one seed, one result database, and the two numbers
    in the envelope agree."""
    work = tmp_path / "job-one-db"
    results_dir = work / "seed_00" / "working.results"
    results_dir.mkdir(parents=True)
    _write_full_results_db(
        results_dir / "1.db",
        movements=[(_MOVEMENT_BUSY, 378.0, 44.8)],
        queues=[(_MOVEMENT_BUSY, 12.0, 41.5)],
    )
    source = _write_inpx(tmp_path / "one.inpx")

    envelope = _dispatch_and_get_envelope(
        monkeypatch, job_work_dir=work, source=source, random_seeds=[42]
    )

    assert envelope["data"]["run_result"]["result_db_count"] == 1
    assert envelope["verification"]["result_db_count"] == 1
    assert "1 seed(s), writing 1 result database(s)" in envelope["message"]
    assert envelope["data"]["run_result"]["movement_volumes"][_MOVEMENT_BUSY]["vehs"] == 378.0


def test_result_db_count_is_unknown_not_zero_when_no_work_dir_was_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A job record with no id resolves no work dir, so there is nothing to
    count. The key is OMITTED from run_result and reported as None (never 0)
    in verification -- 0 would be a measurement nobody took, the same
    absence-honesty rule the tri-state evidence keys follow."""
    work = tmp_path / "job-unused"
    work.mkdir()
    source = _write_inpx(tmp_path / "nowork.inpx")

    envelope = _dispatch_and_get_envelope(
        monkeypatch, job_work_dir=work, source=source, job_id=None
    )

    assert "result_db_count" not in envelope["data"]["run_result"]
    assert envelope["verification"]["result_db_count"] is None
    assert "writing unknown result database(s)" in envelope["message"]


def test_collect_run_evidence_counts_every_db_under_the_work_dir(tmp_path: Path):
    """At the collector level, without the tool: the count is
    ``results_evidence.result_db_paths``' own recursive discovery rule (the
    same list every volume reader sums over), not a per-seed-directory
    assumption."""
    work = tmp_path / "job"
    (work / "seed_00" / "working.results").mkdir(parents=True)
    (work / "seed_01" / "working.results").mkdir(parents=True)
    _write_full_results_db(work / "seed_00" / "working.results" / "1.db")
    _write_full_results_db(work / "seed_00" / "working.results" / "2.db")
    _write_full_results_db(work / "seed_01" / "working.results" / "1.db")
    source = _write_inpx(tmp_path / "counted.inpx")

    evidence = tools_run._collect_run_evidence(
        work, source, duration_s=3600, evaluation_configured=True
    )

    assert evidence["result_db_count"] == 3
    assert len(results_evidence.result_db_paths(work)) == 3


# ---------------------------------------------------------------------------
# (f) Capability matrix — checked in, re-measurable, provenance only
# ---------------------------------------------------------------------------

_MATRIX_PATH = Path(__file__).parent / "fixtures" / "result_evidence" / "families.json"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _capability_matrix() -> dict[str, Any]:
    import json

    return json.loads(_MATRIX_PATH.read_text(encoding="utf-8"))


def test_capability_matrix_fixture_records_both_result_db_families():
    """The checked-in matrix, validated without touching any database.

    It exists because the two families in this tree disagree about which
    table carries queue data, and a reader written against either one alone
    reads zero rows on the other."""
    matrix = _capability_matrix()
    families = {family["id"]: family for family in matrix["families"]}

    customer = families["customer-model-noderesults-populated"]
    vendor = families["vendor-example-queuecounter-populated-movement-armed-but-empty"]

    # The inversion, stated as data: nodeResults populated / queues absent on
    # one side, exactly the reverse on the other.
    assert customer["evidence_states"][results_evidence.MOVEMENT_INTERVAL_TABLE] == "PRESENT"
    assert (
        customer["evidence_states"][results_evidence.QUEUECOUNTER_INTERVAL_TABLE]
        == results_evidence.TABLE_ABSENT
    )
    assert (
        vendor["evidence_states"][results_evidence.MOVEMENT_INTERVAL_TABLE]
        == results_evidence.ARMED_BUT_EMPTY
    )
    assert vendor["evidence_states"][results_evidence.QUEUECOUNTER_INTERVAL_TABLE] == "PRESENT"

    assert customer["db_count"] == 70
    assert vendor["db_count"] == 48
    assert customer["row_count_range"][results_evidence.MOVEMENT_INTERVAL_TABLE] == {
        "min": 62,
        "max": 67,
    }

    # Grouping by SIMULATIONRUN signature alone does NOT discriminate: all 136
    # .dbs share one signature. Recorded so the next reader does not repeat
    # the experiment.
    assert len(matrix["distinct_simulationrun_signatures"]) == 1
    assert set(matrix["families"][0]["evidence_states"]) == set(results_evidence.EVALUATION_TABLES)

    # Provenance only — no customer content anywhere in the fixture.
    assert matrix["contains"].startswith("table sets, row counts and provenance only")
    for family in matrix["families"]:
        assert set(family) >= {"db_count", "roots", "provenance", "sample_paths"}
        assert not any(
            key in family for key in ("rows", "values", "object_ids", "qlenmax")
        )


def test_capability_matrix_still_reproduces_against_the_real_result_dbs():
    """Re-measure, read-only, rather than trusting the checked-in numbers.

    Every sample .db is opened ``mode=ro&immutable=1`` — no write, no lock
    file, nothing left behind in ``runtime/``. Skipped wholesale on a machine
    that has none of the recorded databases (e.g. a fresh clone or the Mac
    laptop): the fixture is then a historical record, not a live claim."""
    matrix = _capability_matrix()
    checked = 0
    for family in matrix["families"]:
        for rel in family["sample_paths"]:
            db_path = _REPO_ROOT / rel
            if not db_path.exists():
                continue
            descriptor = results_evidence.describe_result_db(db_path, immutable=True)
            for table, expected_kind in family["evidence_states"].items():
                state = descriptor[table]
                actual_kind = (
                    "PRESENT" if results_evidence.is_present(state) else state
                )
                assert actual_kind == expected_kind, (rel, table, state)
                rows = results_evidence.rows_in(state)
                if rows is not None:
                    bounds = family["row_count_range"][table]
                    assert bounds["min"] <= rows <= bounds["max"], (rel, table, rows)
            checked += 1

    if checked == 0:
        pytest.skip(
            "none of the result databases recorded in "
            f"{_MATRIX_PATH.name} are present on this machine"
        )


def test_delay_measurement_truncated_by_queue_reproduces_against_a_real_result_db():
    """The acceptance check, run against a real customer result database.

    ``MOVEMENT_EvaluationTimeInterval`` in
    ``.../Scenarios/S000001/S000001.results/1.db`` carries QLENMAX 119.4837 m
    on one movement into node 1 and 135.2885 m on another — both past the
    100 m ``nodesDelaySegmentStart`` every network in this checkout uses. The
    delay beyond that segment was never counted, and until this unit nothing
    could say so. Read-only, ``immutable=1``; skipped when the database is
    not on this machine."""
    db_path = (
        _REPO_ROOT
        / "runtime/workspaces/bfc69251bd04/uploads/current/Scenarios/S000001/S000001.results/1.db"
    )
    if not db_path.exists():
        pytest.skip(f"real result database not present on this machine: {db_path}")

    queue_results = results_evidence.read_queue_rows([db_path], immutable=True)
    assert queue_results[_MOVEMENT_BUSY]["qlenmax"] == pytest.approx(_QLENMAX_BUSY, abs=1e-4)
    assert queue_results[_MOVEMENT_GRIDLOCKED]["qlenmax"] == pytest.approx(
        _QLENMAX_GRIDLOCKED, abs=1e-4
    )

    segments = tools_run._delay_segments(queue_results, 100.0)
    run_result = {"tables": ["SIMULATIONRUN"], "delay_segments": segments}

    finding = {c.id: c.check for c in constraints.get_registry()}[
        "delay_measurement_truncated_by_queue"
    ]({"tool": "run_simulation", "params": {}}, {}, {"run_result": run_result})

    assert finding is not None
    assert "1" in finding.details["nodes"]


# ---------------------------------------------------------------------------
# results_evidence unit behaviour
# ---------------------------------------------------------------------------


def test_los_band_never_invents_a_grade_for_an_ungraded_movement():
    """5 of 63 rows in the real customer sample are zero-volume movements
    with a NULL VEHDELAY. Banding NULL to "A" would fabricate the very lie
    gridlock_reports_los_a exists to catch."""
    assert results_evidence.los_band(None) is None
    assert results_evidence.los_band(0.0) == "A"
    assert results_evidence.los_band(10.0) == "A"
    assert results_evidence.los_band(10.01) == "B"
    assert results_evidence.los_band(80.1) == "F"


def test_movement_class_rows_keep_a_null_delay_null(tmp_path: Path):
    db_path = tmp_path / "null_delay.db"
    _write_full_results_db(db_path, movements=[("4-12302-118.201532-619-9.588695", 0, None)])

    rows = results_evidence.read_movement_class_rows([db_path])

    assert rows["4-12302-118.201532-619-9.588695"]["vehdelay"] is None
    assert results_evidence.los_band(rows["4-12302-118.201532-619-9.588695"]["vehdelay"]) is None


def test_node_token_survives_negative_coordinates_and_node_level_rows():
    assert results_evidence.node_token("1-508-100.779980-510-62.109716") == "1"
    assert results_evidence.node_token("10-508--100.77-510-62.1") == "10"
    assert results_evidence.node_token("8") == "8"


def test_evaluation_element_read_degrades_on_a_network_without_one(tmp_path: Path):
    source = tmp_path / "bare.inpx"
    source.write_text("<net/>", encoding="utf-8")

    assert tools_run._read_evaluation_element(source) == {}
    assert tools_run._read_evaluation_element(tmp_path / "missing.inpx") == {}


def test_queue_definition_is_omitted_rather_than_filled_with_nones(tmp_path: Path):
    """An <evaluation> element with no queue attributes must not produce a
    dict of Nones: queue_length_definition_not_portable reads that as
    "definition attached" and goes quiet on a run where it should speak."""
    source = _write_inpx(tmp_path / "no-queue-attrs.inpx", queue_definition=False)

    assert tools_run._read_evaluation_element(source)["queue_definition"] is None

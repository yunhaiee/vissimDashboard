"""Tests for Phase 0.1's tools_sweep.py: the demand-stress sweep
(``run_demand_stress_sweep``, tier "run").

Every test injects a FAKE ``tools_run._run_job_and_wait`` -- mirrors
``test_agent_tools_run.py``'s own injection pattern exactly, since this
module calls that SAME module-level seam (see tools_sweep.py's own module
docstring, "Reuse, not reinvention") -- so no test here touches real Vissim
COM, a real background thread, or the real file-based job registry.
``_summarize_kpis``'s ``results_reader`` call (reused from tools_run.py) IS
real (pure sqlite reading, deterministic); ``scale_vehicle_input_volumes``
(pure ElementTree XML) is ALSO real throughout -- only the job-queue/COM
seam is mocked, per the task brief's "orchestration logic with a MOCKED
runner ... must run without Vissim".
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

import pytest

import services.agent.tools_run as tools_run
import services.agent.tools_sweep as tools_sweep
import services.simulation_runs as simulation_runs
from services.agent.toolkit import default_registry, dispatch

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_active_run_source():
    """Mirrors test_agent_tools_run.py's own autouse reset -- every test
    starts with no active run source and clears it after."""
    tools_run.clear_active_run_source()
    yield
    tools_run.clear_active_run_source()


_INPX_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?><network version="2400">'
    '<links><link no="101" name="L"><geometry><linkPolyPts>'
    '<linkPolyPoint x="0" y="0" zOffset="0"/><linkPolyPoint x="100" y="0" zOffset="0"/>'
    "</linkPolyPts></geometry><lanes><lane width=\"3.5\"/></lanes></link></links>"
)


def _make_source_inpx(tmp_path: Path, *, volumes: dict[str, float | None]) -> Path:
    """A small, real-shaped .inpx with one ``<vehicleInput>`` per
    ``volumes`` entry -- a ``None`` value means "no <timeIntervalVehVolume>
    children at all" (the zero-configured-demand shape), matching the real
    quirk found live on data/practice.inpx."""
    inputs_xml = []
    for no, volume in volumes.items():
        if volume is None:
            inputs_xml.append(f'<vehicleInput no="{no}" name="VI{no}" link="101"/>')
        else:
            inputs_xml.append(
                f'<vehicleInput no="{no}" name="VI{no}" link="101"><timeIntVehVols>'
                f'<timeIntervalVehVolume timeInt="1 0" volume="{volume}" cont="false" '
                f'vehComp="1"/></timeIntVehVols></vehicleInput>'
            )
    inpx = tmp_path / "working.inpx"
    inpx.write_text(
        _INPX_HEADER + f"<vehicleInputs>{''.join(inputs_xml)}</vehicleInputs></network>",
        encoding="utf-8",
    )
    return inpx


def _write_results_db(db_path: Path, *, delay_avg: float) -> None:
    """Schema-complete results .db (mirrors test_agent_tools_run.py's own
    ``_write_full_results_db``) so ``results_reader.get_runtime_results``
    actually produces a network aggregate instead of silently skipping it."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE SIMULATIONRUN (NO INTEGER, RANDSEED INTEGER, SIMPERIOD REAL, "
            "SIMEND REAL, VISSIMVER TEXT)"
        )
        conn.execute("INSERT INTO SIMULATIONRUN VALUES (1, 1, 3600, 3600, '2026')")
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
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE SIMULATIONRUN (NO INTEGER, RANDSEED INTEGER, SIMPERIOD REAL, "
            "SIMEND REAL, VISSIMVER TEXT)"
        )
        conn.execute("INSERT INTO SIMULATIONRUN VALUES (1, 1, 60, 60, '2026')")
        conn.commit()
    finally:
        conn.close()


class _FakeMultiJobRunner:
    """A canned ``tools_run._run_job_and_wait`` replacement returning a
    FRESH job_id per call (base and scaled variants each get their own real
    job dir with pre-seeded per-seed result databases) -- mirrors
    test_agent_tools_run.py's ``_FakeJobRunner`` but supports multiple
    distinct job records, since a sweep submits TWO jobs, not one."""

    def __init__(self, work_root: Path, *, delay_avg_by_label: dict[str, float], kpi_bearing: bool = True):
        self.work_root = work_root
        self.delay_avg_by_label = delay_avg_by_label
        self.kpi_bearing = kpi_bearing
        self.calls: list[dict[str, Any]] = []
        self.job_dirs: dict[str, Path] = {}
        self._counter = 0

    def __call__(self, *, source_inpx: Path, duration_s: int, random_seeds: list, timeout_s: float):
        # Snapshot the scaled variant's XML content NOW, at call time --
        # run_demand_stress_sweep deletes its temp scratch dir in a
        # `finally` block once BOTH variants have been submitted, so by the
        # time dispatch() returns, `source_inpx` (for the scaled call) no
        # longer exists on disk. Callers that need to inspect what was
        # actually submitted read `calls[i]["source_inpx_text"]`, not the
        # (by-then-deleted) path itself.
        self.calls.append(
            {
                "source_inpx": source_inpx,
                "source_inpx_text": source_inpx.read_text(encoding="utf-8"),
                "duration_s": duration_s,
                "random_seeds": list(random_seeds),
                "timeout_s": timeout_s,
            }
        )
        self._counter += 1
        # Deterministic label from call order: first call is always "base"
        # (run_demand_stress_sweep's own body dispatches base before scaled).
        label = "base" if self._counter == 1 else "scaled"
        job_id = f"fake-sweep-job-{self._counter}"
        job_dir = self.work_root / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        delay_avg = self.delay_avg_by_label[label]
        for index, _seed in enumerate(random_seeds):
            seed_dir = job_dir / f"seed_{index:02d}"
            seed_dir.mkdir(parents=True, exist_ok=True)
            if self.kpi_bearing:
                _write_results_db(seed_dir / "run_001.db", delay_avg=delay_avg)
            else:
                _write_simulationrun_only_db(seed_dir / "run_001.db")
        self.job_dirs[job_id] = job_dir
        return {
            "id": job_id,
            "status": "done",
            "result_tables": (
                ["SIMULATIONRUN", "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS"]
                if self.kpi_bearing
                else ["SIMULATIONRUN"]
            ),
        }


def _patch_job_dir(monkeypatch: pytest.MonkeyPatch, runner: _FakeMultiJobRunner) -> None:
    monkeypatch.setattr(simulation_runs, "job_dir", lambda job_id: runner.job_dirs[job_id])


def _source_and_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **params: Any):
    source = _make_source_inpx(tmp_path, volumes={"1": 600.0, "2": 400.0})
    tools_run.set_active_run_source(source)
    runner = _FakeMultiJobRunner(
        tmp_path / "jobs", delay_avg_by_label={"base": 30.0, "scaled": 36.0}
    )
    monkeypatch.setattr(tools_run, "_run_job_and_wait", runner)
    _patch_job_dir(monkeypatch, runner)
    envelope = dispatch("run_demand_stress_sweep", params, registry=default_registry)
    return source, runner, envelope


# ---------------------------------------------------------------------------
# Params validation -- strict, matches the anti-drift contract (toolkit.py)
# ---------------------------------------------------------------------------


def test_rejects_unknown_params():
    envelope = dispatch(
        "run_demand_stress_sweep",
        {"scale_percent": 10, "duration_s": 300, "bogus": 1},
        registry=default_registry,
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_rejects_missing_required_params():
    envelope = dispatch("run_demand_stress_sweep", {}, registry=default_registry)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_seed_count_defaults_to_five():
    params = tools_sweep.RunDemandStressSweepParams(scale_percent=10, duration_s=300)
    assert params.seed_count == 5


@pytest.mark.parametrize("seed_count", [1, 4, 11, 20])
def test_seed_count_out_of_bounds_is_invalid_params(seed_count: int):
    envelope = dispatch(
        "run_demand_stress_sweep",
        {"scale_percent": 10, "duration_s": 300, "seed_count": seed_count},
        registry=default_registry,
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


@pytest.mark.parametrize("seed_count", [5, 6, 10])
def test_seed_count_within_bounds_is_accepted_by_the_params_model(seed_count: int):
    params = tools_sweep.RunDemandStressSweepParams(
        scale_percent=10, duration_s=300, seed_count=seed_count
    )
    assert params.seed_count == seed_count


@pytest.mark.parametrize("scale_percent", [100.0, -100.0, 100.0000001, -100.1, 250.0, -999.0])
def test_scale_percent_hard_bound(scale_percent: float):
    envelope = dispatch(
        "run_demand_stress_sweep",
        {"scale_percent": scale_percent, "duration_s": 300},
        registry=default_registry,
    )
    if abs(scale_percent) > 100.0:
        assert envelope["status"] == "error"
        assert envelope["error_type"] == "invalid_params"
        assert "100" in envelope["message"]
    else:
        # exactly +/-100 is legal (boundary, not "beyond") -- this call still
        # needs an active source to get past param validation, so just prove
        # construction succeeds without raising.
        tools_sweep.RunDemandStressSweepParams(scale_percent=scale_percent, duration_s=300)


# ---------------------------------------------------------------------------
# No active source
# ---------------------------------------------------------------------------


def test_no_active_source_is_a_tool_execution_error():
    envelope = dispatch(
        "run_demand_stress_sweep",
        {"scale_percent": 10, "duration_s": 300},
        registry=default_registry,
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_active_sweep_source" in envelope["message"]


# ---------------------------------------------------------------------------
# Seed identity -- HARD requirement, test-pinned per the task brief
# ---------------------------------------------------------------------------


def test_base_and_scaled_variants_receive_the_identical_seed_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _source, runner, envelope = _source_and_dispatch(
        tmp_path, monkeypatch, scale_percent=10, duration_s=600, seed_count=5
    )

    assert envelope["status"] == "ok"
    assert len(runner.calls) == 2
    base_seeds = runner.calls[0]["random_seeds"]
    scaled_seeds = runner.calls[1]["random_seeds"]
    assert base_seeds == [1, 2, 3, 4, 5]
    assert scaled_seeds == [1, 2, 3, 4, 5]
    assert base_seeds == scaled_seeds
    # Same duration on both sides too.
    assert runner.calls[0]["duration_s"] == runner.calls[1]["duration_s"] == 600


def test_seed_list_scales_with_seed_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _source, runner, envelope = _source_and_dispatch(
        tmp_path, monkeypatch, scale_percent=5, duration_s=300, seed_count=8
    )
    assert envelope["status"] == "ok"
    assert runner.calls[0]["random_seeds"] == list(range(1, 9))
    assert runner.calls[1]["random_seeds"] == list(range(1, 9))


# ---------------------------------------------------------------------------
# Isolation: the working copy is never mutated
# ---------------------------------------------------------------------------


def test_working_copy_is_byte_identical_before_and_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source, _runner, envelope = _source_and_dispatch(
        tmp_path, monkeypatch, scale_percent=25, duration_s=300, seed_count=5
    )

    assert envelope["status"] == "ok"
    on_disk_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    verification = envelope["verification"]
    assert verification["working_copy_untouched"] is True
    assert verification["working_copy_sha256_before"] == verification["working_copy_sha256_after"]
    assert verification["working_copy_sha256_before"] == on_disk_hash


def test_scaled_variant_source_is_a_different_file_from_the_working_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source, runner, envelope = _source_and_dispatch(
        tmp_path, monkeypatch, scale_percent=10, duration_s=300, seed_count=5
    )
    assert envelope["status"] == "ok"
    base_call, scaled_call = runner.calls
    assert base_call["source_inpx"] == source
    assert scaled_call["source_inpx"] != source
    # The scaled clone actually carries the multiplied volume -- never the
    # original file's own content.
    scaled_xml = scaled_call["source_inpx_text"]
    assert 'volume="660"' in scaled_xml  # 600 * 1.10
    assert 'volume="440"' in scaled_xml  # 400 * 1.10
    # And the ORIGINAL working copy's volumes are untouched (still their
    # original fixture-authored values, "600.0"/"400.0").
    original_xml = source.read_text(encoding="utf-8")
    assert 'volume="600.0"' in original_xml
    assert 'volume="400.0"' in original_xml


# ---------------------------------------------------------------------------
# Delta math
# ---------------------------------------------------------------------------


def test_delta_math_matches_the_fixture_kpis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _source, _runner, envelope = _source_and_dispatch(
        tmp_path, monkeypatch, scale_percent=10, duration_s=600, seed_count=5
    )
    assert envelope["status"] == "ok"
    delta = envelope["data"]["sweep"]["delta"]
    entry = delta["avg_delay_s"]
    assert entry["base"] == pytest.approx(30.0)
    assert entry["scaled"] == pytest.approx(36.0)
    assert entry["abs_delta"] == pytest.approx(6.0)
    assert entry["pct_delta"] == pytest.approx(20.0)

    sweep = envelope["data"]["sweep"]
    assert sweep["base"]["metrics"]["avg_delay_s"] == pytest.approx(30.0)
    assert sweep["scaled"]["metrics"]["avg_delay_s"] == pytest.approx(36.0)
    assert sweep["run_count"] == 10
    assert sweep["seeds"] == [1, 2, 3, 4, 5]

    assert "20.0%" in envelope["message"]
    assert "증가" in envelope["message"]


def test_delta_entry_none_delta_math():
    entry = tools_sweep._delta_entry(None, 36.0)
    assert entry == {"base": None, "scaled": 36.0, "abs_delta": None, "pct_delta": None}

    entry2 = tools_sweep._delta_entry(30.0, None)
    assert entry2 == {"base": 30.0, "scaled": None, "abs_delta": None, "pct_delta": None}

    entry3 = tools_sweep._delta_entry(0.0, 5.0)
    assert entry3["abs_delta"] == 5.0
    assert entry3["pct_delta"] is None  # base==0 -> no percentage, never a divide-by-zero


def test_kpis_missing_on_both_sides_degrades_headline_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _make_source_inpx(tmp_path, volumes={"1": 600.0})
    tools_run.set_active_run_source(source)
    runner = _FakeMultiJobRunner(
        tmp_path / "jobs", delay_avg_by_label={"base": 0.0, "scaled": 0.0}, kpi_bearing=False
    )
    monkeypatch.setattr(tools_run, "_run_job_and_wait", runner)
    _patch_job_dir(monkeypatch, runner)

    envelope = dispatch(
        "run_demand_stress_sweep",
        {"scale_percent": 10, "duration_s": 60, "seed_count": 5},
        registry=default_registry,
    )
    assert envelope["status"] == "ok"
    assert envelope["data"]["sweep"]["base"]["metrics"] is None
    assert envelope["data"]["sweep"]["scaled"]["metrics"] is None
    assert envelope["data"]["sweep"]["delta"] == {}
    assert "데이터가 부족" in envelope["message"]


# ---------------------------------------------------------------------------
# Message bounding
# ---------------------------------------------------------------------------


def test_message_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _source, _runner, envelope = _source_and_dispatch(
        tmp_path, monkeypatch, scale_percent=10, duration_s=300, seed_count=5
    )
    assert envelope["status"] == "ok"
    assert len(envelope["message"]) <= tools_sweep._MESSAGE_MAX_CHARS


def test_cap_message_truncates_with_ellipsis():
    text = "x" * 1000
    capped = tools_sweep._cap_message(text, max_chars=50)
    assert len(capped) == 50
    assert capped.endswith("…")


def test_high_scale_percent_warns_in_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _source, _runner, envelope = _source_and_dispatch(
        tmp_path, monkeypatch, scale_percent=75, duration_s=300, seed_count=5
    )
    assert envelope["status"] == "ok"
    assert "75" in envelope["message"]
    assert "유의" in envelope["message"]


def test_moderate_scale_percent_does_not_warn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _source, _runner, envelope = _source_and_dispatch(
        tmp_path, monkeypatch, scale_percent=10, duration_s=300, seed_count=5
    )
    assert envelope["status"] == "ok"
    assert "유의" not in envelope["message"]


# ---------------------------------------------------------------------------
# Ledger: artifacts.runs carries 2 * seed_count children
# ---------------------------------------------------------------------------


def test_artifacts_runs_has_two_times_seed_count_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _source, _runner, envelope = _source_and_dispatch(
        tmp_path, monkeypatch, scale_percent=10, duration_s=300, seed_count=6
    )
    assert envelope["status"] == "ok"
    runs = envelope["artifacts"]["runs"]
    assert len(runs) == 12
    assert sorted({r["variant"] for r in runs}) == ["base", "scaled"]
    base_runs = [r for r in runs if r["variant"] == "base"]
    scaled_runs = [r for r in runs if r["variant"] == "scaled"]
    assert [r["seed"] for r in base_runs] == [1, 2, 3, 4, 5, 6]
    assert [r["seed"] for r in scaled_runs] == [1, 2, 3, 4, 5, 6]
    for run in runs:
        assert run["metrics"]["avg_delay_s"] in (30.0, 36.0)


def test_run_result_shape_matches_run_simulation_for_postrun_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _source, _runner, envelope = _source_and_dispatch(
        tmp_path, monkeypatch, scale_percent=10, duration_s=300, seed_count=5
    )
    assert envelope["status"] == "ok"
    run_result = envelope["data"]["run_result"]
    assert set(run_result.keys()) == {"tables", "deleted_vehicles"}
    assert run_result["deleted_vehicles"] is None
    assert "SIMULATIONRUN" in run_result["tables"]


# ---------------------------------------------------------------------------
# Refusal paths
# ---------------------------------------------------------------------------


def test_unmatched_input_nos_refuses_before_running_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _make_source_inpx(tmp_path, volumes={"1": 600.0})
    tools_run.set_active_run_source(source)
    runner = _FakeMultiJobRunner(tmp_path / "jobs", delay_avg_by_label={"base": 30.0, "scaled": 36.0})
    monkeypatch.setattr(tools_run, "_run_job_and_wait", runner)
    _patch_job_dir(monkeypatch, runner)

    envelope = dispatch(
        "run_demand_stress_sweep",
        {"scale_percent": 10, "duration_s": 300, "input_nos": ["999"]},
        registry=default_registry,
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "999" in envelope["message"]
    assert runner.calls == []  # never reached the job runner


def test_base_variant_job_error_status_aborts_before_scaled_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _make_source_inpx(tmp_path, volumes={"1": 600.0})
    tools_run.set_active_run_source(source)

    calls: list[dict[str, Any]] = []

    def failing_runner(*, source_inpx, duration_s, random_seeds, timeout_s):
        calls.append({"source_inpx": source_inpx})
        return {"id": "fake-base-fail", "status": "error", "error": "VISSIM COM raised"}

    monkeypatch.setattr(tools_run, "_run_job_and_wait", failing_runner)

    envelope = dispatch(
        "run_demand_stress_sweep",
        {"scale_percent": 10, "duration_s": 300, "seed_count": 5},
        registry=default_registry,
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "base" in envelope["message"]
    assert "fake-base-fail" in envelope["message"]
    # ONLY the base variant was ever attempted.
    assert len(calls) == 1


def test_scaled_variant_missing_simulationrun_row_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _make_source_inpx(tmp_path, volumes={"1": 600.0})
    tools_run.set_active_run_source(source)

    def runner(*, source_inpx, duration_s, random_seeds, timeout_s):
        # base variant (first call) looks fine; scaled variant (second call)
        # claims done but has no SIMULATIONRUN table.
        if source_inpx == source:
            return {
                "id": "fake-base-ok",
                "status": "done",
                "result_tables": ["SIMULATIONRUN"],
            }
        return {"id": "fake-scaled-bad", "status": "done", "result_tables": ["SOMETHING_ELSE"]}

    monkeypatch.setattr(tools_run, "_run_job_and_wait", runner)
    monkeypatch.setattr(simulation_runs, "job_dir", lambda job_id: tmp_path / "empty" / job_id)

    envelope = dispatch(
        "run_demand_stress_sweep",
        {"scale_percent": 10, "duration_s": 300, "seed_count": 5},
        registry=default_registry,
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "scaled" in envelope["message"]
    assert "SIMULATIONRUN" in envelope["message"]


def test_working_copy_mutated_mid_sweep_is_detected_and_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Defense in depth (module docstring's "verify, not trust"): even
    though nothing in this tool's own code path should ever be ABLE to
    mutate the working copy, the hash re-check must still catch it and
    refuse rather than silently report results against a network that may
    no longer be what was analyzed."""
    source = _make_source_inpx(tmp_path, volumes={"1": 600.0})
    tools_run.set_active_run_source(source)
    runner = _FakeMultiJobRunner(tmp_path / "jobs", delay_avg_by_label={"base": 30.0, "scaled": 36.0})

    call_count = {"n": 0}
    real_call = runner.__call__

    def tampering_runner(*, source_inpx, duration_s, random_seeds, timeout_s):
        call_count["n"] += 1
        if call_count["n"] == 2:
            # Simulate an unexpected external mutation of the working copy
            # happening while the scaled variant is running.
            source.write_text(source.read_text(encoding="utf-8") + "<!-- tampered -->", encoding="utf-8")
        return real_call(
            source_inpx=source_inpx, duration_s=duration_s, random_seeds=random_seeds, timeout_s=timeout_s
        )

    monkeypatch.setattr(tools_run, "_run_job_and_wait", tampering_runner)
    _patch_job_dir(monkeypatch, runner)

    envelope = dispatch(
        "run_demand_stress_sweep",
        {"scale_percent": 10, "duration_s": 300, "seed_count": 5},
        registry=default_registry,
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "changed during the sweep" in envelope["message"]


# ---------------------------------------------------------------------------
# input_nos filter
# ---------------------------------------------------------------------------


def test_input_nos_filter_scales_only_the_named_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = _make_source_inpx(tmp_path, volumes={"1": 600.0, "2": 400.0})
    tools_run.set_active_run_source(source)
    runner = _FakeMultiJobRunner(tmp_path / "jobs", delay_avg_by_label={"base": 30.0, "scaled": 36.0})
    monkeypatch.setattr(tools_run, "_run_job_and_wait", runner)
    _patch_job_dir(monkeypatch, runner)

    envelope = dispatch(
        "run_demand_stress_sweep",
        {"scale_percent": 10, "duration_s": 300, "seed_count": 5, "input_nos": ["1"]},
        registry=default_registry,
    )
    assert envelope["status"] == "ok"
    scaled_xml = runner.calls[1]["source_inpx_text"]
    assert 'volume="660"' in scaled_xml  # input 1 scaled
    assert 'volume="400.0"' in scaled_xml  # input 2 left untouched (original value)
    assert 'volume="440"' not in scaled_xml
    summary = envelope["artifacts"]["scale_input_summary"]
    assert summary["scaled_input_nos"] == ["1"]


def test_empty_volume_inputs_are_reported_not_silently_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The real data/practice.inpx quirk this milestone's live check hit:
    vehicle inputs that exist but carry no <timeIntervalVehVolume> children
    at all -- must be reported, never crash, never silently vanish."""
    source = _make_source_inpx(tmp_path, volumes={"1": None, "2": None})
    tools_run.set_active_run_source(source)
    runner = _FakeMultiJobRunner(tmp_path / "jobs", delay_avg_by_label={"base": 0.0, "scaled": 0.0}, kpi_bearing=False)
    monkeypatch.setattr(tools_run, "_run_job_and_wait", runner)
    _patch_job_dir(monkeypatch, runner)

    envelope = dispatch(
        "run_demand_stress_sweep",
        {"scale_percent": 10, "duration_s": 300, "seed_count": 5},
        registry=default_registry,
    )
    assert envelope["status"] == "ok"
    summary = envelope["artifacts"]["scale_input_summary"]
    assert sorted(summary["empty_input_nos"]) == ["1", "2"]
    assert summary["scaled_input_nos"] == []


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


def test_registered_as_tier_run():
    spec = default_registry.get("run_demand_stress_sweep")
    assert spec is not None
    assert spec.tier == "run"
    assert spec.provider == "run"


def test_seed_values_pure_function():
    assert tools_sweep._seed_values(5) == [1, 2, 3, 4, 5]
    assert tools_sweep._seed_values(10) == list(range(1, 11))

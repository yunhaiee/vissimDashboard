"""Live acceptance check: the fix's own definition of done.

Runs a 600s x 1-seed simulation on a Beijing clone through the REAL agent
run path (``services.agent.tools_run.run_simulation`` ->
``routers.simulations.start_agent_simulation_job`` ->
``_run_agent_simulation_job`` -> real Vissim COM, RunContinuous) and asserts:

  1. the result .db (opened DIRECTLY via sqlite, independent of any tool's
     own claim) contains VEHICLENETWORKPERFORMANCEMEASUREMENT_
     EvaluationTimeIntervalClass with at least one row -- the exact table
     services/results_reader.py's ``_read_db`` reads as the "network" KPI
     dict;
  2. ``services.results_reader.get_runtime_results`` (the SAME function
     ``routers/simulations.py``'s ``/results`` endpoint and
     ``tools_run._summarize_kpis`` both call) returns a non-None result with
     a populated "network" dict;
  3. the ``run_simulation`` tool's envelope (via ``toolkit.dispatch`` --
     never a hand-built shortcut) carries both ``data.kpis`` (non-None) and
     ``data.run_result.evaluation_configured is True`` (also mirrored at
     ``verification.evaluation_configured``).

Never touches the source example in place (copies into a process-unique
runtime/ work dir); always disconnects/leaves no Vissim process running.

Usage: python tools/evaluation_run_live_check.py [example_dir]
Default example: Urban Intersection Beijing.CN (2026 Examples Demo).
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "evaluation-run-live-check"
RUN_DURATION_S = 600
TARGET_TABLE = "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh_work_dir(example_dir: Path) -> Path:
    for stale in WORK_ROOT.glob(f"{example_dir.name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    work_dir = WORK_ROOT / f"{example_dir.name}_{os.getpid()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _check_no_vissim_processes_remain() -> None:
    try:
        import psutil
    except ImportError:
        _log("[cleanup] psutil not installed -- skipping the no-leftover-Vissim-process check.")
        return
    lingering = []
    for proc in psutil.process_iter(["name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "vissim" in name:
                lingering.append(f"{proc.pid}:{name}")
        except Exception:
            continue
    if lingering:
        _log(f"[cleanup] WARNING: Vissim process(es) still running after the script finished: {lingering}")
    else:
        _log("[cleanup] Confirmed: no Vissim process is running after the script finished.")


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    work_dir = _fresh_work_dir(example_dir)
    _log(f"Process-unique work dir: {work_dir}")

    source_dir = work_dir / "source"
    shutil.copytree(example_dir, source_dir)
    source_inpx = next(source_dir.glob("*.inpx"))
    _log(f"[1/4] Cloned {example_dir.name} -> {source_inpx}")

    import services.agent.tools_run as tools_run  # noqa: F401 - registers run_simulation
    from services.agent.toolkit import default_registry, dispatch
    from services.simulation_runs import job_dir
    from services import results_reader

    overall_ok = True
    try:
        tools_run.set_active_run_source(source_inpx)

        _log(
            f"[2/4] Dispatching run_simulation(duration_s={RUN_DURATION_S}, "
            "random_seeds=[1]) via toolkit.dispatch (the real agent run path: "
            "tools_run.run_simulation -> routers.simulations."
            "start_agent_simulation_job -> _run_agent_simulation_job -> real "
            "Vissim COM RunContinuous)..."
        )
        started = time.monotonic()
        envelope = dispatch(
            "run_simulation",
            {"duration_s": RUN_DURATION_S, "random_seeds": [1]},
            registry=default_registry,
        )
        elapsed = time.monotonic() - started
        _log(f"      dispatch() returned in {elapsed:.1f}s")
        _log(f"      envelope = {envelope}")

        if envelope["status"] != "ok":
            _log(f"      FAIL: expected status ok, got {envelope!r}")
            raise SystemExit(1)

        data = envelope.get("data") or {}
        run_result = data.get("run_result") or {}
        kpis = data.get("kpis")
        job_id = (envelope.get("verification") or {}).get("job_id")

        _log(f"[3/4] job_id={job_id!r}")
        _log(f"      run_result = {run_result}")
        _log(f"      kpis = {kpis}")
        _log(
            f"      verification.evaluation_configured = "
            f"{(envelope.get('verification') or {}).get('evaluation_configured')!r}"
        )

        if run_result.get("evaluation_configured") is not True:
            _log(f"      FAIL: run_result.evaluation_configured is not True: {run_result!r}")
            overall_ok = False
        else:
            _log("      PASS: run_result.evaluation_configured is True")

        if (envelope.get("verification") or {}).get("evaluation_configured") is not True:
            _log("      FAIL: verification.evaluation_configured is not True")
            overall_ok = False
        else:
            _log("      PASS: verification.evaluation_configured is True")

        if kpis is None:
            _log("      FAIL: envelope data.kpis is None")
            overall_ok = False
        else:
            _log(f"      PASS: envelope data.kpis is non-None: {kpis}")

        # --- Independent verification #1: sqlite directly, never trust the envelope alone ---
        _log(f"[4/4] Independently verifying via sqlite + results_reader (job_dir={job_dir(job_id)})...")
        db_paths = sorted(job_dir(job_id).rglob("*.db"))
        if not db_paths:
            _log(f"      FAIL: no result .db found under {job_dir(job_id)}")
            overall_ok = False
        else:
            found_table_with_rows = False
            for db_path in db_paths:
                conn = sqlite3.connect(str(db_path))
                try:
                    tables = {
                        row[0].upper()
                        for row in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                    if TARGET_TABLE in tables:
                        rows = conn.execute(f"SELECT COUNT(*) FROM {TARGET_TABLE}").fetchone()[0]
                        _log(f"      {db_path.name}: tables={sorted(tables)}, {TARGET_TABLE} rows={rows}")
                        if rows >= 1:
                            found_table_with_rows = True
                    else:
                        _log(f"      {db_path.name}: tables={sorted(tables)} (missing {TARGET_TABLE})")
                finally:
                    conn.close()
            if found_table_with_rows:
                _log(f"      PASS: {TARGET_TABLE} exists with >=1 row in at least one result .db")
            else:
                _log(f"      FAIL: {TARGET_TABLE} missing or empty in every result .db")
                overall_ok = False

        # --- Independent verification #2: results_reader.get_runtime_results ---
        reader_result = results_reader.get_runtime_results(job_dir(job_id))
        _log(f"      results_reader.get_runtime_results(job_dir) = {reader_result}")
        if reader_result is None or not reader_result.get("network"):
            _log("      FAIL: results_reader returned None or an empty 'network' dict")
            overall_ok = False
        else:
            _log(f"      PASS: results_reader network KPIs = {reader_result['network']}")
    finally:
        tools_run.clear_active_run_source()
        _log("")
        _log("Waiting briefly before the leftover-process check...")
        time.sleep(2.0)
        _check_no_vissim_processes_remain()

    _log("")
    _log("=" * 78)
    _log(f"RESULT: {'PASS' if overall_ok else 'FAIL'}")
    _log("=" * 78)
    if not overall_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()

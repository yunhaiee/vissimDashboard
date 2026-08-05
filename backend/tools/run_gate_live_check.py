"""Live verification for M2.4: the run gate + post-run lie detectors.

Two phases against a REAL Vissim 2026 instance, on a cloned copy of the
Beijing example network (the same default network com_smoke_test.py and
edit_tool_live_check.py already prove end-to-end) -- NEVER any user dataset:

  [Phase A] Edit a vehicle input via a real VissimEditSession (reuses
  edit_tool_live_check.py's Phase A pattern exactly: toolkit.dispatch against
  a real ArtifactManager + a real VissimEditSession, verified via a re-parsed
  shadow model) -- proves the network run_simulation submits in Phase B is
  the SAME session's real, edited working copy, not the pristine source.

  [Phase B] Dispatch run_simulation for a 300s (5-minute) run through the
  REAL simulation job queue (RunContinuous, never RunSingleStep -- per
  docs/phase2/00-EXECUTION-GUIDE.md SS3.1), driven through the FULL
  orchestrator (real ArtifactManager, real enabled GuardrailEngine,
  PreapprovedWriteGate, a canned planner -- no paid LLM calls needed to
  prove the plumbing) exactly like a production ``/agent/approve`` re-run.
  Asserts:
    - the job's result .db carries a SIMULATIONRUN row, verified DIRECTLY
      via sqlite (independent of the tool's own envelope claim) -- the
      run-completed artifact per SS3.1, never SimSec
    - the trace's "postrun" record fires ``results_missing_kpi_tables`` --
      a 5-minute run is too short for evaluation intervals to have produced
      any KPI table, so the warning firing here (against a REAL Vissim run,
      not a canned fixture) is the milestone's acceptance proof that the
      detector works (docs/phase2/02-ROADMAP.md M2.4)

Always disconnects/closes every Vissim session in ``finally`` blocks, so no
instance is left running when the script exits (best-effort psutil check at
the end confirms this).

Usage: python tools/run_gate_live_check.py [example_dir]
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# This app is Korean-primary; the Windows console's default codepage cannot
# encode Hangul, and answers/summaries legitimately contain it. Reconfigure
# stdout to UTF-8 (same fix as edit_tool_live_check.py) so this script can
# print real ko text instead of crashing on the first Korean character.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "run-gate-live-check"
RUN_DURATION_S = 300  # 5 minutes -- deliberately too short for evaluation intervals


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh_work_dir(example_dir: Path) -> Path:
    for stale in WORK_ROOT.glob(f"{example_dir.name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass  # held open by a running Vissim instance -- leave it
    work_dir = WORK_ROOT / f"{example_dir.name}_{os.getpid()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


# ---------------------------------------------------------------------------
# Phase A: edit a vehicle input via a real VissimEditSession
# (mirrors tools/edit_tool_live_check.py's phase_a_tool_dispatch exactly)
# ---------------------------------------------------------------------------


def phase_a_edit_vehicle_input(example_dir: Path, work_dir: Path):
    _log("=" * 78)
    _log("[Phase A] Edit a vehicle input via a real VissimEditSession (edit_tool_live_check.py's pattern)")
    _log("=" * 78)

    from services.agent.edit_session import (
        VissimEditSession,
        clear_active_edit_session,
        set_active_edit_session,
    )
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    import services.agent.tools_edit  # noqa: F401 - registers the write tools
    from services.agent.toolkit import default_registry, dispatch

    artifact_manager = ArtifactManager(source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir)
    shadow = artifact_manager.clone()
    vi_no = int(shadow["vehicle_inputs"][0]["no"])
    before_volume = shadow["vehicle_inputs"][0].get("volume")
    new_volume = round((before_volume or 0.0) + 137.0, 3)
    _log(f"[A.1] Cloned {example_dir.name} -> {artifact_manager.working_path}")
    _log(f"      Vehicle input {vi_no} volume before (shadow model): {before_volume!r} -> targeting {new_volume}")

    session = VissimEditSession(artifact_manager.working_path)
    ok = False
    try:
        set_active_edit_session(session)
        _log(f"[A.2] Dispatching set_vehicle_input_volume(input_no={vi_no!r}, volume={new_volume}) via toolkit.dispatch...")
        started = time.monotonic()
        envelope = dispatch(
            "set_vehicle_input_volume",
            {"input_no": str(vi_no), "volume": new_volume},
            registry=default_registry,
        )
        _log(f"      dispatch() returned in {time.monotonic() - started:.1f}s")
        _log(f"      envelope = {envelope}")

        assert envelope["status"] == "ok", f"expected status ok, got {envelope}"
        assert envelope["verification"] == {"written_value": new_volume, "readback_value": new_volume}, (
            f"verification mismatch: {envelope['verification']!r}"
        )
        _log("[A.3] PASS: envelope status ok, verification written_value == readback_value == new_volume")

        after_shadow = build_shadow_model(artifact_manager.working_path)
        after_volume = next(v["volume"] for v in after_shadow["vehicle_inputs"] if v["no"] == str(vi_no))
        _log(f"[A.4] build_shadow_model(working copy) re-parse: vehicle_input[{vi_no}].volume = {after_volume!r}")
        assert after_volume == new_volume, f"persisted volume {after_volume!r} != expected {new_volume!r}"
        _log("      PASS: the edit actually persisted to the SESSION's working copy on disk --")
        _log("            this is the exact file Phase B will submit to the run job queue.")
        ok = True
    finally:
        clear_active_edit_session()
        session.close()
        _log("[A.5] Edit session closed (Vissim instance exited).")

    if not ok:
        raise SystemExit("Phase A FAILED -- aborting before touching the run gate.")
    return artifact_manager, vi_no, new_volume


# ---------------------------------------------------------------------------
# Phase B: run_simulation through the FULL orchestrator + REAL job queue
# ---------------------------------------------------------------------------


class _CannedProvider:
    """No paid LLM calls -- same fixture shape as edit_tool_live_check.py's
    Phase B (and test_agent_orchestrator_run_gate.py's FakeStructuredProvider),
    proving the M2.4 plumbing independent of planner quality."""

    def __init__(self, queue: list[tuple[dict[str, Any] | None, dict[str, Any]]]) -> None:
        self._queue = list(queue)

    def generate_structured(self, messages, tools, schema=None, *, system=None, max_tokens=None):
        return self._queue.pop(0)


def _action(tool_name: str, *, summary: str, **params: Any):
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "canned", "fallback_rung": 0}


def phase_b_run_simulation(artifact_manager, work_dir: Path) -> bool:
    _log("")
    _log("=" * 78)
    _log(f"[Phase B] run_simulation(duration_s={RUN_DURATION_S}) through the REAL job queue")
    _log("           (full orchestrator: real ArtifactManager + real enabled GuardrailEngine +")
    _log("           PreapprovedWriteGate, canned planner -- no paid LLM calls)")
    _log("=" * 78)

    import services.agent.tools_run as tools_run  # noqa: F401 - registers run_simulation
    from services.agent.gates import PreapprovedWriteGate, signature
    from services.agent.guardrails import GuardrailEngine
    from services.agent.orchestrator import run_agent_turn
    from services.agent.trace import read_trace
    from services.simulation_runs import job_dir

    action = {"tool": "run_simulation", "params": {"duration_s": RUN_DURATION_S}}
    gate = PreapprovedWriteGate({signature(action["tool"], action["params"])})
    engine = GuardrailEngine(disabled=False)  # production default -- the real catalog
    provider = _CannedProvider(
        [
            _action("run_simulation", summary="5분 시뮬레이션을 실행합니다", duration_s=RUN_DURATION_S),
            _action(
                "respond",
                summary="실행을 완료했습니다",
                answer=f"{RUN_DURATION_S}초 시뮬레이션 실행을 완료했습니다.",
            ),
        ]
    )

    # M2.4 wiring: the SAME module-global "current turn" source
    # routers/assistant.py's _stream_agent_turn sets before every turn (see
    # tools_run.py's module docstring) -- points run_simulation at THIS
    # session's edited working copy, not the pristine source.
    tools_run.set_active_run_source(artifact_manager.working_path)

    _log(f"[B.1] Submitting via run_agent_turn (RunContinuous, real job queue, real GuardrailEngine)...")
    started = time.monotonic()
    try:
        result = run_agent_turn(
            f"{RUN_DURATION_S}초 시뮬레이션 돌려줘",
            provider=provider,
            work_dir=work_dir,
            session_id="run-gate-live-check",
            language="ko",
            gate=gate,
            engine=engine,
            artifact_manager=artifact_manager,
        )
    finally:
        tools_run.clear_active_run_source()
    elapsed = time.monotonic() - started
    _log(f"      run_agent_turn returned in {elapsed:.1f}s -- kind={result.kind!r}")
    _log(f"      answer={result.answer!r}")

    records = read_trace(result.trace_path)
    kinds = [r["kind"] for r in records]
    _log(f"[B.2] Trace kinds = {kinds}")

    ok = True

    if result.kind != "answer":
        _log(f"      FAIL: expected kind='answer', got {result.kind!r}")
        ok = False

    if not result.state.history:
        _log("      FAIL: expected the run_simulation dispatch to appear in state.history")
        ok = False
        return ok

    run_observation = result.state.history[0].observation
    _log(f"[B.3] run_simulation observation = {run_observation}")
    if run_observation.get("status") != "ok":
        _log(f"      FAIL: run_simulation dispatch did not return status ok: {run_observation}")
        ok = False
        return ok

    job_id = (run_observation.get("verification") or {}).get("job_id")
    run_result = (run_observation.get("data") or {}).get("run_result") or {}
    _log(f"      job_id={job_id!r}  run_result={run_result!r}")

    # --- Independent, direct SIMULATIONRUN verification (never trust the
    # envelope's claim alone -- re-open the actual result .db) ---
    _log(f"[B.4] Independently verifying the SIMULATIONRUN row directly via sqlite (job_dir={job_dir(job_id)})...")
    db_paths = sorted(job_dir(job_id).rglob("*.db"))
    if not db_paths:
        _log(f"      FAIL: no result .db found under {job_dir(job_id)}")
        ok = False
    else:
        for db_path in db_paths:
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute(
                    "SELECT RANDSEED, SIMPERIOD, SIMEND, VISSIMVER FROM SIMULATIONRUN ORDER BY NO DESC LIMIT 1"
                ).fetchone()
                tables = {
                    r[0].upper()
                    for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                }
            finally:
                conn.close()
            if row is None:
                _log(f"      FAIL: {db_path.name} has no SIMULATIONRUN row")
                ok = False
                continue
            seed, period, sim_end, version = row
            _log(
                f"      PASS: {db_path.name} SIMULATIONRUN: seed={seed} period={period}s "
                f"simulated-to={sim_end}s Vissim={version}; tables={sorted(tables)}"
            )
            if float(sim_end) < float(period):
                _log(f"      FAIL: simulated-to ({sim_end}) < requested period ({period})")
                ok = False
            if tables != {"SIMULATIONRUN"}:
                _log(
                    f"      NOTE: {db_path.name} has MORE than SIMULATIONRUN ({sorted(tables)}) -- "
                    "the 5-minute-run-has-no-KPI-tables precondition wasn't met by this db; the "
                    "postrun warning check below is the authoritative acceptance signal."
                )

    # --- The acceptance proof: results_missing_kpi_tables fires ---
    _log("[B.5] Checking the trace for a postrun results_missing_kpi_tables warning...")
    postrun_records = [r for r in records if r["kind"] == "postrun"]
    if not postrun_records:
        _log("      FAIL: no 'postrun' trace record was written at all")
        ok = False
    else:
        postrun_payload = postrun_records[0]["payload"]
        _log(f"      postrun trace payload = {postrun_payload}")
        findings = postrun_payload.get("findings") or []
        if "results_missing_kpi_tables" in findings:
            _log(
                "      PASS: results_missing_kpi_tables FIRED against a REAL 5-minute Vissim run -- "
                "this is the M2.4 acceptance proof the detector works."
            )
        else:
            _log(
                f"      FAIL: results_missing_kpi_tables did NOT fire (findings={findings}). "
                "Either the run produced more KPI tables than expected for 300s, or the detector "
                "is broken."
            )
            ok = False

    warning_records = [r for r in records if r["kind"] == "warning" and r["payload"].get("phase") == "postrun"]
    if warning_records:
        _log(f"[B.6] postrun 'warning' trace record = {warning_records[0]['payload']}")
        guardrail_warnings = run_observation.get("guardrail_warnings") or []
        _log(f"      run_simulation observation.guardrail_warnings = {guardrail_warnings}")
        if not any(w.get("constraint_id") == "results_missing_kpi_tables" for w in guardrail_warnings):
            _log("      FAIL: the warning did not attach to the observation the planner would see")
            ok = False
        else:
            _log("      PASS: the warning is attached to the observation (mirrors postwrite's warning-attachment).")
    else:
        _log("      FAIL: no postrun 'warning' trace record (expected one alongside the finding)")
        ok = False

    return ok


# ---------------------------------------------------------------------------
# Best-effort: confirm no Vissim instance is left running
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    work_dir = _fresh_work_dir(example_dir)
    _log(f"Process-unique work dir: {work_dir}")

    # No pythoncom.CoInitialize() here -- like edit_tool_live_check.py, this
    # script never touches COM directly; Phase A's VissimEditSession and
    # Phase B's job-queue thread each own their own CoInitialize/
    # CoUninitialize pairing.
    a_ok = False
    b_ok = False
    try:
        artifact_manager, vi_no, new_volume = phase_a_edit_vehicle_input(example_dir, work_dir)
        a_ok = True
        b_ok = phase_b_run_simulation(artifact_manager, work_dir)
    finally:
        _log("")
        _log("Waiting briefly before the leftover-process check (job-queue thread's own COM")
        _log("teardown may still be unwinding its last CoUninitialize)...")
        time.sleep(2.0)
        _check_no_vissim_processes_remain()

    _log("")
    _log("=" * 78)
    _log(f"RESULT: Phase A {'PASS' if a_ok else 'FAIL'}, Phase B {'PASS' if b_ok else 'FAIL'}")
    _log("=" * 78)
    if not (a_ok and b_ok):
        raise SystemExit(1)


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()

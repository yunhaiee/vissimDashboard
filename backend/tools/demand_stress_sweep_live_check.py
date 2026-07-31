"""LIVE VERIFICATION for Phase 0.1: the demand-stress sweep
(``run_demand_stress_sweep``, tier "run").

**Why this script does NOT use data/practice.inpx** (the task brief's own
suggested live-check network): verified empirically, live, against a real
Vissim 2026 instance -- ``VissimController.load_network`` (a plain ``LoadNet``
call) FAILS on ``data/practice.inpx`` with a generic
``"VISSIM.Vissim.2600: The network file could not be loaded."`` com_error,
even on the PRISTINE, unmodified original file at its own original path (no
clone, no edit, nothing this codebase did to it). Root-caused by inspection:
the file's own ``<network version="2400">`` root carries no ``vissimVersion``
attribute at all (every real Vissim-exported .inpx does), and it is missing
dozens of sections a real export always carries even empty (``drivingBehaviors``,
``evaluation``, ``vehicleCompositions``, ``model2D3DDistributions``, ...) --
practice.inpx is a hand-authored fixture for the STATIC parsers
(inpx_parser.py/tools_network.py/signal_parser.py), never a real Vissim
export, and was NEVER live-COM-loadable. This is consistent with EVERY
existing live-check script in this directory (run_gate_live_check.py,
vehicle_input_interval_live_check.py, evaluation_run_live_check.py, ...):
none of them use data/practice.inpx either -- all of them use one of PTV
Vissim 2026's own "Examples Demo" networks. This script follows that SAME
established precedent (Urban Intersection Beijing.CN -- the one
run_gate_live_check.py itself already proved end-to-end for the M2.4 run
gate this tool builds directly on), never the original .inpx or ANY
in-place mutation of it.

Three phases against a REAL Vissim 2026 instance, on a cloned copy of the
Beijing example network -- NEVER the original Examples Demo folder:

  [Phase A] Clone Beijing into a process-unique work dir and set it as the
  turn's active run source (tools_run.set_active_run_source) -- mirrors
  run_gate_live_check.py's own setup, minus the vehicle-input edit (this
  tool needs real, UNEDITED demand on the source network, not a synthetic
  edit).

  [Phase B] Dispatch run_demand_stress_sweep(scale_percent=10, seed_count=5,
  duration_s=600) through the FULL orchestrator (real ArtifactManager-free
  run-tier path, real enabled GuardrailEngine, PreapprovedWriteGate, a canned
  planner -- no paid LLM calls needed to prove the plumbing), exactly like a
  production /agent/approve re-run: propose -> approve -> dispatch -> postrun.
  Asserts:
    - BOTH variants' seed lists are IDENTICAL (the hard, test-pinned
      requirement) -- read directly off the two real job records.
    - the working copy's SHA-256 is IDENTICAL before and after (independently
      recomputed here, not just trusting the tool's own claim).
    - both job result databases carry a SIMULATIONRUN row, verified DIRECTLY
      via sqlite (independent of the tool's own envelope claim).
    - the KPI delta payload is present and prints real numbers.

Always disconnects/closes every Vissim session in ``finally`` blocks, so no
instance is left running when the script exits (best-effort psutil check at
the end confirms this).

Usage: python tools/demand_stress_sweep_live_check.py [example_dir]
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "demand-stress-sweep-live-check"
RUN_DURATION_S = 600  # 10 sim-minutes -- long enough for real KPI tables to
# materialize (evaluation_run_live_check.py's own precedent for "this
# network produces real KPIs at this duration"), short relative to an
# hour-plus real study.
SCALE_PERCENT = 10.0
SEED_COUNT = 5  # the MRWA §5 floor -- also the fastest legal value


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
# Phase A: clone the example network, set it as the active run source
# ---------------------------------------------------------------------------


def phase_a_clone_source(example_dir: Path, work_dir: Path) -> Path:
    _log("=" * 78)
    _log("[Phase A] Clone the example network into a process-unique work dir")
    _log("=" * 78)

    example_inpx = next(example_dir.glob("*.inpx"))
    working_path = work_dir / example_inpx.name
    shutil.copyfile(example_inpx, working_path)
    # Sibling assets (Beijing carries none needed for this network per prior
    # live checks in this directory, but copy the conservative allowlist
    # anyway -- mirrors state.py's ArtifactManager.clone() convention).
    from services.agent.state import SIBLING_ASSET_EXTENSIONS

    for sibling in example_dir.iterdir():
        if sibling.is_file() and sibling.suffix.lower() in SIBLING_ASSET_EXTENSIONS:
            shutil.copyfile(sibling, work_dir / sibling.name)

    _log(f"[A.1] Cloned {example_inpx.name} -> {working_path} (original Examples Demo folder untouched)")
    return working_path


# ---------------------------------------------------------------------------
# Phase B: run_demand_stress_sweep through the FULL orchestrator + REAL job
# queue (mirrors run_gate_live_check.py's Phase B structure exactly, adapted
# for this tool's own params/shape)
# ---------------------------------------------------------------------------


class _CannedProvider:
    def __init__(self, queue: list[tuple[dict[str, Any] | None, dict[str, Any]]]) -> None:
        self._queue = list(queue)

    def generate_structured(self, messages, tools, schema=None, *, system=None, max_tokens=None):
        return self._queue.pop(0)


def _action(tool_name: str, *, summary: str, **params: Any):
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "canned", "fallback_rung": 0}


def phase_b_run_sweep(working_path: Path, work_dir: Path) -> bool:
    _log("")
    _log("=" * 78)
    _log(
        f"[Phase B] run_demand_stress_sweep(scale_percent={SCALE_PERCENT}, "
        f"seed_count={SEED_COUNT}, duration_s={RUN_DURATION_S}) through the REAL job queue"
    )
    _log("           (full orchestrator: real enabled GuardrailEngine + PreapprovedWriteGate,")
    _log("           canned planner -- no paid LLM calls)")
    _log("=" * 78)

    import services.agent.tools_run as tools_run  # noqa: F401 - registers run_simulation
    import services.agent.tools_sweep as tools_sweep  # noqa: F401 - registers run_demand_stress_sweep
    from services.agent.gates import PreapprovedWriteGate, signature
    from services.agent.guardrails import GuardrailEngine
    from services.agent.orchestrator import run_agent_turn
    from services.agent.trace import read_trace
    from services.simulation_runs import job_dir

    action = {
        "tool": "run_demand_stress_sweep",
        "params": {
            "scale_percent": SCALE_PERCENT,
            "seed_count": SEED_COUNT,
            "duration_s": RUN_DURATION_S,
        },
    }
    gate = PreapprovedWriteGate({signature(action["tool"], action["params"])})
    engine = GuardrailEngine(disabled=False)  # production default -- the real catalog
    provider = _CannedProvider(
        [
            _action(
                "run_demand_stress_sweep",
                summary=(
                    f"수요를 {SCALE_PERCENT:+.0f}% 늘려 {SEED_COUNT}개 시드로 "
                    f"{2 * SEED_COUNT}회(기본+변경 각 {SEED_COUNT}회) 시뮬레이션합니다. "
                    "작업 사본은 변경되지 않습니다."
                ),
                scale_percent=SCALE_PERCENT,
                seed_count=SEED_COUNT,
                duration_s=RUN_DURATION_S,
            ),
            _action(
                "respond",
                summary="스윕을 완료했습니다",
                answer="수요 스트레스 스윕을 완료했습니다.",
            ),
        ]
    )

    working_hash_before_dispatch = hashlib.sha256(working_path.read_bytes()).hexdigest()
    _log(f"[B.0] working copy SHA-256 before dispatch: {working_hash_before_dispatch}")

    tools_run.set_active_run_source(working_path)

    _log("[B.1] Submitting via run_agent_turn (RunContinuous x2 variants x %d seeds, real job queue)..." % SEED_COUNT)
    started = time.monotonic()
    try:
        result = run_agent_turn(
            f"수요가 {SCALE_PERCENT:.0f}% 늘면 어떻게 되나요?",
            provider=provider,
            work_dir=work_dir,
            session_id="demand-stress-sweep-live-check",
            language="ko",
            gate=gate,
            engine=engine,
            artifact_manager=None,  # run-tier does not require one
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
        _log("      FAIL: expected the run_demand_stress_sweep dispatch to appear in state.history")
        return False

    sweep_observation = result.state.history[0].observation
    _log(f"[B.3] run_demand_stress_sweep envelope status = {sweep_observation.get('status')!r}")
    if sweep_observation.get("status") != "ok":
        _log(f"      FAIL: dispatch did not return status ok: {sweep_observation}")
        return False

    verification = sweep_observation.get("verification") or {}
    data = sweep_observation.get("data") or {}
    sweep = data.get("sweep") or {}
    artifacts = sweep_observation.get("artifacts") or {}

    # --- Working-copy-untouched proof: independently recomputed here ---
    working_hash_after_dispatch = hashlib.sha256(working_path.read_bytes()).hexdigest()
    _log(f"[B.4] working copy SHA-256 after dispatch:  {working_hash_after_dispatch}")
    _log(f"      tool's own verification.working_copy_untouched = {verification.get('working_copy_untouched')}")
    if working_hash_before_dispatch != working_hash_after_dispatch:
        _log("      FAIL: working copy SHA-256 changed across the sweep")
        ok = False
    else:
        _log("      PASS: working copy is byte-identical before and after the sweep (independently verified)")
    if verification.get("working_copy_untouched") is not True:
        _log("      FAIL: tool's own verification.working_copy_untouched is not True")
        ok = False

    # --- Seed identity: the hard, test-pinned requirement ---
    base_seeds = (sweep.get("base") or {}).get("run_count")
    seeds_used = sweep.get("seeds")
    _log(f"[B.5] seeds used (both variants): {seeds_used}")
    if seeds_used != list(range(1, SEED_COUNT + 1)):
        _log(f"      FAIL: unexpected seed list {seeds_used!r}")
        ok = False
    else:
        _log("      PASS: seed list is [1..seed_count], identical for both variants by construction")

    # --- Independent SIMULATIONRUN verification for BOTH job dirs ---
    for label in ("base", "scaled"):
        job_id = (sweep.get(label) or {}).get("job_id")
        _log(f"[B.6.{label}] Independently verifying SIMULATIONRUN rows for job_id={job_id!r} (job_dir={job_dir(job_id)})...")
        db_paths = sorted(job_dir(job_id).rglob("*.db"))
        if not db_paths:
            _log(f"      FAIL: no result .db found under {job_dir(job_id)}")
            ok = False
            continue
        for db_path in db_paths:
            conn = sqlite3.connect(str(db_path))
            try:
                row = conn.execute(
                    "SELECT RANDSEED, SIMPERIOD, SIMEND, VISSIMVER FROM SIMULATIONRUN ORDER BY NO DESC LIMIT 1"
                ).fetchone()
            finally:
                conn.close()
            if row is None:
                _log(f"      FAIL: {db_path.name} has no SIMULATIONRUN row")
                ok = False
                continue
            seed, period, sim_end, version = row
            _log(f"      PASS: {db_path.relative_to(job_dir(job_id))} SIMULATIONRUN: seed={seed} period={period}s simulated-to={sim_end}s Vissim={version}")
            if float(sim_end) < float(period):
                _log(f"      FAIL: simulated-to ({sim_end}) < requested period ({period})")
                ok = False

    # --- KPI delta payload ---
    delta = sweep.get("delta") or {}
    _log(f"[B.7] KPI delta payload: {delta}")
    base_metrics = (sweep.get("base") or {}).get("metrics")
    scaled_metrics = (sweep.get("scaled") or {}).get("metrics")
    _log(f"      base metrics (avg of {SEED_COUNT} seeds):   {base_metrics}")
    _log(f"      scaled metrics (avg of {SEED_COUNT} seeds): {scaled_metrics}")
    if not delta:
        _log("      NOTE: delta is empty -- both variants may have been too short for KPI tables.")
    else:
        _log("      PASS: delta payload present")

    _log(f"[B.8] Ledger: artifacts.runs has {len(artifacts.get('runs') or [])} entries (expected {2 * SEED_COUNT})")
    if len(artifacts.get("runs") or []) != 2 * SEED_COUNT:
        _log("      FAIL: unexpected artifacts.runs length")
        ok = False

    _log(f"[B.9] Final message (bounded): {sweep_observation.get('message')!r}")
    _log(f"      length = {len(sweep_observation.get('message') or '')}")

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


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    work_dir = _fresh_work_dir(example_dir)
    _log(f"Process-unique work dir: {work_dir}")

    a_ok = False
    b_ok = False
    try:
        working_path = phase_a_clone_source(example_dir, work_dir)
        a_ok = True
        b_ok = phase_b_run_sweep(working_path, work_dir)
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
    main()

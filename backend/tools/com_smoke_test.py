"""First-contact COM smoke test: drive Vissim through services.vissim_com.

Copies a PTV example network into runtime/com-smoke/ (results are written
next to the .inpx, so never load the original in place), then:
attach-or-launch Vissim -> load network -> enumerate vehicle inputs and
signal controllers -> set duration/seed -> step the simulation to the end.

Leaves the Vissim window open (no Exit) so the next run attaches instantly
and a human can inspect the loaded network.

Usage: python tools/com_smoke_test.py [example_dir] [--duration 60]
Default example: Urban Intersection Beijing.CN from the 2026 Examples Demo.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

DEFAULT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "com-smoke"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("example_dir", nargs="?", default=str(DEFAULT_EXAMPLE))
    parser.add_argument("--duration", type=int, default=60, help="simulation seconds to run")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    example_dir = Path(args.example_dir)
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    # A still-open Vissim window from a prior run keeps its working copy locked,
    # so use a process-unique dir and best-effort clean any stale siblings.
    for stale in WORK_ROOT.glob(f"{example_dir.name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass  # held open by a running Vissim instance — leave it
    work_dir = WORK_ROOT / f"{example_dir.name}_{os.getpid()}"
    shutil.copytree(example_dir, work_dir)
    inpx_files = sorted(work_dir.glob("*.inpx"))
    if not inpx_files:
        raise SystemExit(f"No .inpx in {work_dir}")
    inpx = inpx_files[0]
    print(f"[1/6] Working copy: {inpx}")

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    print("[2/6] Launching a dedicated Vissim instance (never attaches to an open one)...", flush=True)
    started = time.monotonic()
    controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
    print(f"      Connected via {controller.prog_id} in {time.monotonic() - started:.1f}s", flush=True)

    print(f"[3/6] Loading network: {inpx.name}")
    started = time.monotonic()
    controller.load_network(str(inpx))
    print(f"      Loaded in {time.monotonic() - started:.1f}s")

    vehicle_inputs = controller.available_vehicle_input_nos()
    signal_controllers = controller.available_signal_controller_nos()
    print(f"[4/6] Vehicle inputs: {vehicle_inputs}")
    print(f"      Signal controllers: {signal_controllers}")

    print(f"[5/6] Setting SimPeriod={args.duration}s, RandSeed={args.seed}", flush=True)
    controller.set_sim_duration(args.duration)
    controller.set_random_seed(args.seed)

    # RunContinuous is a single COM call that runs to SimPeriod internally.
    # Stepping via RunSingleStep in a Python loop is 1-2 COM round-trips per
    # step and is far too slow for a real network (minutes for 60s) — the
    # production runner should prefer RunContinuous + periodic polling.
    # NOTE: SimSec resets to 0 when the run ends, so success is verified from
    # the SIMULATIONRUN row Vissim writes to the results database instead.
    print(f"[6/6] Running simulation to {args.duration}s via RunContinuous...", flush=True)
    started = time.monotonic()
    controller.vissim.Simulation.RunContinuous()
    wall = time.monotonic() - started

    import sqlite3

    db_paths = sorted(work_dir.rglob("*.db"), key=lambda p: p.stat().st_mtime)
    if not db_paths:
        raise SystemExit(f"FAIL: RunContinuous returned ({wall:.1f}s) but no results .db was written in {work_dir}")
    conn = sqlite3.connect(db_paths[-1])
    try:
        row = conn.execute(
            "SELECT RANDSEED, SIMPERIOD, SIMEND, VISSIMVER FROM SIMULATIONRUN ORDER BY NO DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise SystemExit("FAIL: results database has no SIMULATIONRUN row — the run did not complete")
    seed, period, sim_end, version = row
    ok = float(sim_end) >= float(period) and int(seed) == args.seed
    verdict = "SUCCESS" if ok else "FAIL"
    print(
        f"\n{verdict}: SIMULATIONRUN says seed={seed} period={period}s simulated-to={sim_end}s "
        f"on Vissim {version} ({wall:.1f}s wall, {float(sim_end) / wall if wall > 0 else 0:.1f}x realtime).",
        flush=True,
    )
    print("Vissim window left open for inspection (dedicated instance).", flush=True)
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

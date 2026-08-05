"""Follow-up probe: does VehRecResolution actually reduce the .fzp write
cadence (and thus file size / row count) proportionally? Verified live
against Vissim 2026 -- the trajectory_replay_live_check.py acceptance run
exposed a 90MB/300s payload at the network's default resolution (SIMSEC
step 0.1s), completely impractical for a browser fetch. If resolution=5
genuinely produces a 0.5s step (5x fewer rows), that is the right ROOT-CAUSE
fix (record coarser, not just truncate/stride after the fact).

Usage: python tools/vehicle_record_resolution_probe.py [example_dir]
"""
from __future__ import annotations

import os
import shutil
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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "vehicle-record-resolution-probe"
DURATION_S = 60


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh_work_dir(example_dir: Path, tag: str) -> Path:
    work_dir = WORK_ROOT / f"{example_dir.name}_{tag}_{os.getpid()}"
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    shutil.copytree(example_dir, work_dir)
    return work_dir


def _run_with_resolution(example_dir: Path, resolution: int) -> tuple[Path, int, float | None]:
    work_dir = _fresh_work_dir(example_dir, f"res{resolution}")
    working = sorted(work_dir.glob("*.inpx"))[0]
    pre_existing = set(work_dir.rglob("*.fzp"))

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController, _VEHICLE_RECORD_ATTR_IDS

    controller = VissimController()
    try:
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(working))
        evaluation = controller.vissim.Evaluation

        controller.configure_vehicle_record_evaluation(duration_s=DURATION_S)
        # Set resolution AFTER the standard configure call (which doesn't
        # touch it) -- this probe's whole point is testing this ONE extra
        # field.
        evaluation.SetAttValue("VehRecResolution", resolution)
        readback = evaluation.AttValue("VehRecResolution")
        _log(f"  VehRecResolution set to {resolution}, read back {readback!r}")

        controller.set_sim_duration(DURATION_S)
        controller.set_random_seed(1)
        controller.vissim.Simulation.RunContinuous()

        new_fzp = sorted(p for p in work_dir.rglob("*.fzp") if p not in pre_existing)
        if not new_fzp:
            raise SystemExit(f"FAIL: no .fzp written for resolution={resolution}")
        fzp_path = new_fzp[0]
        size = fzp_path.stat().st_size

        from services.trajectory_reader import parse_fzp

        parsed = parse_fzp(fzp_path)
        step = parsed["meta"]["step"]
        _log(
            f"  resolution={resolution}: file={fzp_path.name} size={size} bytes "
            f"frames={len(parsed['frames'])} step={step}"
        )
        return fzp_path, size, step
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    _log(f"[1/2] Baseline: default resolution (1), {DURATION_S}s run")
    _, size1, step1 = _run_with_resolution(example_dir, 1)

    _log(f"\n[2/2] Coarser: resolution=5, {DURATION_S}s run")
    _, size5, step5 = _run_with_resolution(example_dir, 5)

    _log("\n" + "=" * 78)
    _log("RESULT")
    _log("=" * 78)
    _log(f"resolution=1: step={step1}s size={size1} bytes")
    _log(f"resolution=5: step={step5}s size={size5} bytes")
    if step1 and step5:
        _log(f"step ratio (5/1): {step5 / step1:.2f}")
    _log(f"size ratio (5/1): {size5 / size1:.3f}")


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()

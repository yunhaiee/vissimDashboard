"""Live acceptance for v2 unit (2): set_reduced_speed_area's XML injection
produces a VALID, RUNNABLE Vissim network -- not just well-formed XML.

The tool's own verification (tools_edit.py's ``set_reduced_speed_area``) is a
REPARSE readback (no COM read-back exists for an XML-injected object -- see
that tool's docstring). That proves the intended attributes landed on disk,
but not that Vissim itself can still load and simulate the file. This script
is the real oracle for that second, stronger claim:

  [Phase A] Clone the Beijing example (same pattern/default network as
  com_smoke_test.py / edit_tool_live_check.py), dispatch the REAL registered
  ``set_reduced_speed_area`` tool through ``services.agent.toolkit.dispatch``
  on a real link id pulled from the shadow model (never hardcoded), and
  assert the envelope's ``verification`` written/readback values match.
  No Vissim COM anywhere in this phase -- the tool itself never touches COM
  (see tools_edit.py's module docstring).

  [Phase B] Launch a REAL, dedicated Vissim instance
  (``attach_to_running=False`` per 00-EXECUTION-GUIDE.md rule 3), LoadNet the
  MODIFIED working copy from Phase A, run a short RunContinuous, and assert
  the results database has a SIMULATIONRUN row -- proving the injected
  reducedSpeedArea/speedDistribution elements did not corrupt the network in
  any way Vissim itself would reject. Always disconnects/exits in a
  ``finally`` block.

Usage: python tools/reduced_speed_area_live_check.py [example_dir]
Default example: Urban Intersection Beijing.CN (2026 Examples Demo) -- same
default as com_smoke_test.py / edit_tool_live_check.py.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# This app is Korean-primary; the Windows console's default codepage (often
# cp1252) cannot encode Hangul, and answers/messages legitimately contain it.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "reduced-speed-area-live-check"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh_work_dir(example_dir: Path) -> Path:
    for stale in WORK_ROOT.glob(f"{example_dir.name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass  # held open by a running Vissim instance from a prior run -- leave it
    work_dir = WORK_ROOT / f"{example_dir.name}_{os.getpid()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


# ---------------------------------------------------------------------------
# Phase A: real toolkit.dispatch, no COM
# ---------------------------------------------------------------------------


def phase_a_tool_dispatch(example_dir: Path) -> tuple[bool, Path | None, str | None]:
    _log("=" * 78)
    _log('[Phase A] toolkit.dispatch("set_reduced_speed_area", ...) -- no Vissim COM in this phase')
    _log("=" * 78)

    from services.agent.edit_session import clear_active_edit_session, set_active_edit_session
    from services.agent.state import ArtifactManager
    from services.simulation_network_builder import reparse_reduced_speed_area
    import services.agent.tools_edit  # noqa: F401 - registers set_reduced_speed_area
    from services.agent.toolkit import default_registry, dispatch

    class _FakeWorkingInpxSession:
        """set_reduced_speed_area needs only ``.working_inpx`` -- see
        tools_edit.py's module docstring. No COM methods on purpose: if the
        tool regressed into touching COM, this would raise AttributeError
        instead of silently succeeding."""

        def __init__(self, working_inpx: Path) -> None:
            self.working_inpx = working_inpx

    work_dir = _fresh_work_dir(example_dir)
    artifact_manager = ArtifactManager(source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir)
    shadow = artifact_manager.clone()
    if not shadow["links"]:
        _log("[A.1] FAIL: this example network has no links to target")
        return False, None, None

    link = shadow["links"][0]
    link_id = str(link["id"])
    link_length = float(link.get("length") or 0.0)
    from_m = round(min(5.0, link_length / 4), 3) if link_length > 0 else 0.0
    length_m = round(max(min(20.0, link_length / 2), 1.0), 3) if link_length > 0 else 10.0
    _log(f"[A.1] Cloned {example_dir.name} -> {artifact_manager.working_path}")
    _log(f"      Target link {link_id!r} ({link.get('name')!r}), length={link_length}m")
    _log(f"      Injecting speed_kmh=30.0, from_m={from_m}, length_m={length_m}")

    session = _FakeWorkingInpxSession(artifact_manager.working_path)
    set_active_edit_session(session)
    try:
        started = time.monotonic()
        envelope = dispatch(
            "set_reduced_speed_area",
            {"link_id": link_id, "speed_kmh": 30.0, "from_m": from_m, "length_m": length_m},
            registry=default_registry,
        )
        _log(f"[A.2] dispatch() returned in {time.monotonic() - started:.2f}s")
        _log(f"      envelope = {envelope}")

        if envelope["status"] != "ok":
            _log(f"[A.3] FAIL: expected status ok, got {envelope}")
            return False, None, None

        verification = envelope["verification"]
        if verification["written_value"] != verification["readback_value"]:
            _log(f"[A.3] FAIL: written_value != readback_value: {verification}")
            return False, None, None
        _log("[A.3] PASS: envelope status ok, written_value == readback_value (reparse readback matched)")

        # Independent re-confirmation, straight from disk, not trusting the
        # tool's own envelope: this is the SAME function the tool uses
        # internally, called fresh here as a second witness.
        area_nos = envelope["artifacts"]["area_nos"]
        independent_readback = reparse_reduced_speed_area(artifact_manager.working_path, area_nos)
        _log(f"[A.4] Independent reparse_reduced_speed_area(...) = {independent_readback}")
        if (
            independent_readback["link_id"] != link_id
            or abs(independent_readback["speed_kmh"] - 30.0) > 1e-6
            or abs(independent_readback["from_m"] - from_m) > 1e-6
            or abs(independent_readback["length_m"] - length_m) > 1e-6
        ):
            _log("      FAIL: independent readback does not match what was requested")
            return False, None, None
        _log("      PASS: independent readback confirms the injected element on disk.")
        return True, artifact_manager.working_path, link_id
    finally:
        clear_active_edit_session()


# ---------------------------------------------------------------------------
# Phase B: real Vissim -- LoadNet + a short RunContinuous on the MODIFIED file
# ---------------------------------------------------------------------------


def phase_b_vissim_loads_and_runs(modified_inpx: Path, *, duration: int = 60, seed: int = 42) -> bool:
    _log("")
    _log("=" * 78)
    _log("[Phase B] Real Vissim: LoadNet the MODIFIED file + a short RunContinuous")
    _log("=" * 78)

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    ok = False
    try:
        _log("[B.1] Launching a dedicated Vissim instance (never attaches to an open one)...")
        started = time.monotonic()
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        _log(f"      Connected via {controller.prog_id} in {time.monotonic() - started:.1f}s")

        _log(f"[B.2] LoadNet: {modified_inpx}")
        started = time.monotonic()
        controller.load_network(str(modified_inpx))
        _log(f"      PASS: LoadNet succeeded in {time.monotonic() - started:.1f}s "
             f"(the injected reducedSpeedArea/speedDistribution did not break loading)")

        _log(f"[B.3] Setting SimPeriod={duration}s, RandSeed={seed}")
        controller.set_sim_duration(duration)
        controller.set_random_seed(seed)

        _log(f"[B.4] Running simulation to {duration}s via RunContinuous...")
        started = time.monotonic()
        controller.vissim.Simulation.RunContinuous()
        wall = time.monotonic() - started

        import sqlite3

        db_paths = sorted(modified_inpx.parent.rglob("*.db"), key=lambda p: p.stat().st_mtime)
        if not db_paths:
            _log(f"[B.5] FAIL: RunContinuous returned ({wall:.1f}s) but no results .db was written")
            return False
        conn = sqlite3.connect(db_paths[-1])
        try:
            row = conn.execute(
                "SELECT RANDSEED, SIMPERIOD, SIMEND, VISSIMVER FROM SIMULATIONRUN ORDER BY NO DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            _log("[B.5] FAIL: results database has no SIMULATIONRUN row -- the run did not complete")
            return False
        got_seed, period, sim_end, version = row
        run_ok = float(sim_end) >= float(period) and int(got_seed) == seed
        verdict = "PASS" if run_ok else "FAIL"
        _log(
            f"[B.5] {verdict}: SIMULATIONRUN says seed={got_seed} period={period}s "
            f"simulated-to={sim_end}s on Vissim {version} "
            f"({wall:.1f}s wall, {float(sim_end) / wall if wall > 0 else 0:.1f}x realtime)."
        )
        ok = run_ok
    finally:
        controller.disconnect()
        _log("[B.6] Disconnected/exited the dedicated Vissim instance.")

    return ok


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    a_ok, modified_inpx, link_id = phase_a_tool_dispatch(example_dir)
    b_ok = False
    if a_ok and modified_inpx is not None:
        b_ok = phase_b_vissim_loads_and_runs(modified_inpx)
    else:
        _log("\nSkipping Phase B: Phase A did not produce a modified network to load.")

    _log("")
    _log("=" * 78)
    _log(f"RESULT: Phase A {'PASS' if a_ok else 'FAIL'} (link {link_id!r}), Phase B {'PASS' if b_ok else 'FAIL'}")
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

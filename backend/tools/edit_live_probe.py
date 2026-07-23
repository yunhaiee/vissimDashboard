"""Live probe for M2.3's persist() design (docs/phase2/02-ROADMAP.md M2.3,
item 2 of the controller's design brief) -- run this BEFORE building
edit_session.py/tools_edit.py around its assumption.

Question this answers: after a live COM session has LoadNet'd a working
copy, can that SAME session's own edits be persisted back onto that exact
path via ``save_net_as(tmp)`` followed by ``os.replace(tmp, working)`` --
and does the still-open session tolerate the file being replaced out from
under it well enough to set + save a SECOND time? M2.1's ``save_net_as``
guard already forbids saving directly onto the loaded path (see
``services/vissim_com.py``), so the two-step tmp-then-replace is the only
candidate route; this script proves (or disproves) that it actually works
on this machine before any production code depends on it.

If ``os.replace`` raises (most likely cause: Windows keeps a lock on the
destination while Vissim still holds a handle to it), the script falls back
to ``shutil.copyfile(tmp, working)`` and repeats the same two-write proof
with that strategy instead. If BOTH strategies fail, this halts loudly --
that would mean M2.3's whole persist() design needs to change before any
other file in this milestone gets written.

Usage: python tools/edit_live_probe.py [example_dir]
Default example: Urban Intersection Beijing.CN (the same network
tools/com_smoke_test.py already proves end-to-end, including a vehicle
input to mutate).

Always disconnects its dedicated Vissim instance in a ``finally`` block.
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

DEFAULT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "edit-probe"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _try_os_replace(tmp: Path, working: Path) -> tuple[bool, str]:
    try:
        os.replace(tmp, working)
        return True, f"os.replace({tmp.name} -> {working.name}) succeeded"
    except Exception as exc:
        return False, f"os.replace FAILED: {type(exc).__name__}: {exc}"


def _try_shutil_copyfile(tmp: Path, working: Path) -> tuple[bool, str]:
    try:
        shutil.copyfile(tmp, working)
        tmp.unlink(missing_ok=True)
        return True, f"shutil.copyfile({tmp.name} -> {working.name}) succeeded"
    except Exception as exc:
        return False, f"shutil.copyfile FAILED: {type(exc).__name__}: {exc}"


def _persist_round(
    controller: Any,
    working: Path,
    tmp: Path,
    strategy: str,
    round_no: int,
) -> tuple[bool, str]:
    """One save_net_as(tmp) -> replace-strategy(tmp, working) round.
    Returns (ok, detail)."""
    try:
        controller.save_net_as(tmp)
    except Exception as exc:
        return False, f"round {round_no}: save_net_as({tmp.name}) FAILED: {type(exc).__name__}: {exc}"

    if strategy == "os.replace":
        ok, detail = _try_os_replace(tmp, working)
    else:
        ok, detail = _try_shutil_copyfile(tmp, working)
    return ok, f"round {round_no}: {detail}"


def _run_persist_strategy(
    controller: Any,
    vi_no: int,
    working: Path,
    tmp: Path,
    strategy: str,
    start_volume: float,
) -> tuple[bool, list[str], float | None]:
    """Runs the two-write probe (set -> save/replace -> set again -> save/replace
    again) using ``strategy`` ("os.replace" or "shutil.copyfile"). Returns
    (ok, log_lines, last_volume_written)."""
    log: list[str] = []
    v1 = round(start_volume + 111.0, 3)
    v2 = round(v1 + 222.0, 3)

    log.append(f"-- strategy: {strategy} --")

    try:
        controller.set_vehicle_input(vi_no, v1)
        readback1 = controller.get_vehicle_input_volume(vi_no)
    except Exception as exc:
        log.append(f"round 1: set/get FAILED: {type(exc).__name__}: {exc}")
        return False, log, None
    if readback1 != v1:
        log.append(f"round 1: readback mismatch: wrote {v1!r}, read back {readback1!r}")
        return False, log, None
    log.append(f"round 1: set_vehicle_input({vi_no}, {v1}) -> readback {readback1} OK (in-memory)")

    ok1, detail1 = _persist_round(controller, working, tmp, strategy, 1)
    log.append(detail1)
    if not ok1:
        return False, log, None
    log.append(f"round 1: {working.name} on disk after replace: size={working.stat().st_size} bytes")

    # Round 2: prove the STILL-OPEN session (never reloaded) tolerates its
    # own loaded-file path having just been replaced out from under it, and
    # can set + save_net_as + replace a SECOND time.
    try:
        controller.set_vehicle_input(vi_no, v2)
        readback2 = controller.get_vehicle_input_volume(vi_no)
    except Exception as exc:
        log.append(f"round 2: set/get FAILED (session may have been destabilized by round 1's replace): {type(exc).__name__}: {exc}")
        return False, log, None
    if readback2 != v2:
        log.append(f"round 2: readback mismatch: wrote {v2!r}, read back {readback2!r}")
        return False, log, None
    log.append(f"round 2: set_vehicle_input({vi_no}, {v2}) -> readback {readback2} OK (in-memory, post-replace)")

    ok2, detail2 = _persist_round(controller, working, tmp, strategy, 2)
    log.append(detail2)
    if not ok2:
        return False, log, None
    log.append(f"round 2: {working.name} on disk after SECOND replace: size={working.stat().st_size} bytes")

    return True, log, v2


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    for stale in WORK_ROOT.glob(f"{example_dir.name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass  # held open by a running Vissim instance from a prior probe -- leave it
    work_dir = WORK_ROOT / f"{example_dir.name}_{os.getpid()}"
    shutil.copytree(example_dir, work_dir)
    inpx_files = sorted(work_dir.glob("*.inpx"))
    if not inpx_files:
        raise SystemExit(f"No .inpx in {work_dir}")
    working = inpx_files[0]
    tmp = working.with_name(working.stem + "_saving" + working.suffix)
    _log(f"[1/5] Working copy: {working}")

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    overall_ok = False
    strategy_used = None
    try:
        _log("[2/5] Launching a dedicated Vissim instance (never attaches to an open one)...")
        started = time.monotonic()
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        _log(f"      Connected via {controller.prog_id} in {time.monotonic() - started:.1f}s")

        _log(f"[3/5] Loading network: {working.name}")
        controller.load_network(str(working))

        vi_nos = controller.available_vehicle_input_nos()
        if not vi_nos:
            raise SystemExit(f"FAIL: {example_dir.name} has no vehicle inputs -- cannot run this probe on it")
        vi_no = vi_nos[0]
        start_volume = float(controller.get_vehicle_input_volume(vi_no))
        _log(f"      Vehicle input {vi_no} starting volume: {start_volume}")

        _log("[4/5] Persist probe: set -> save_net_as(tmp) -> replace(tmp, working) -- twice, same live session")
        ok, log_lines, last_written = _run_persist_strategy(
            controller, vi_no, working, tmp, "os.replace", start_volume
        )
        for line in log_lines:
            _log(f"      {line}")

        if ok:
            strategy_used = "os.replace"
            overall_ok = True
        else:
            _log("")
            _log("      os.replace design FAILED -- falling back to shutil.copyfile per the task brief.")
            _log("      Reconnecting fresh (the failed round may have left the session/file in an unknown state).")
            controller.disconnect()
            controller = VissimController()
            controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
            controller.load_network(str(working))
            start_volume = float(controller.get_vehicle_input_volume(vi_no))
            ok2, log_lines2, last_written2 = _run_persist_strategy(
                controller, vi_no, working, tmp, "shutil.copyfile", start_volume
            )
            for line in log_lines2:
                _log(f"      {line}")
            if ok2:
                strategy_used = "shutil.copyfile"
                overall_ok = True
                last_written = last_written2
            else:
                _log("")
                _log("      BOTH os.replace AND shutil.copyfile FAILED. Halting M2.3 per the task brief --")
                _log("      the persist() design needs to be rethought before edit_session.py is built.")
                raise SystemExit(1)

        _log(f"[5/5] Fresh-reload verification (proves persistence, not just in-memory state)")
        controller.disconnect()
        verify_controller = VissimController()
        verify_controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        verify_controller.load_network(str(working))
        reloaded_volume = float(verify_controller.get_vehicle_input_volume(vi_no))
        _log(f"      Fresh COM reload of {working.name}: vehicle_input[{vi_no}].volume = {reloaded_volume}")
        if reloaded_volume != last_written:
            _log(f"      FAIL: fresh reload shows {reloaded_volume!r}, expected the last-written {last_written!r}")
            overall_ok = False
        else:
            _log("      PASS: fresh COM reload confirms the last write truly persisted to disk.")

        from services.agent.tools_network import build_shadow_model

        shadow = build_shadow_model(working)
        shadow_entry = next((v for v in shadow["vehicle_inputs"] if v["no"] == str(vi_no)), None)
        shadow_volume = shadow_entry.get("volume") if shadow_entry else None
        _log(f"      Shadow-model reparse of {working.name}: vehicle_input[{vi_no}].volume = {shadow_volume}")
        if shadow_volume != last_written:
            _log(f"      FAIL: shadow model shows {shadow_volume!r}, expected {last_written!r}")
            overall_ok = False
        else:
            _log("      PASS: shadow-model reparse confirms the persisted value too.")

        controller = verify_controller  # so the finally block disconnects this one
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        _log("\nDisconnected the dedicated Vissim instance.")

    _log("")
    if overall_ok:
        _log(f"RESULT: PASS -- persist() strategy to use in edit_session.py: {strategy_used}")
    else:
        _log("RESULT: FAIL -- see the failures above.")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

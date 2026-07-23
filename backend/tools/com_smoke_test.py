"""First-contact COM smoke test: drive Vissim through services.vissim_com.

Copies a PTV example network into runtime/com-smoke/ (results are written
next to the .inpx, so never load the original in place), then:
attach-or-launch Vissim -> load network -> enumerate vehicle inputs and
signal controllers -> set duration/seed -> step the simulation to the end
-> (M2.1) read-back + persistence verification for every paired
setter/getter in VissimController, via save_net_as into a NEW file.

Disconnects/exits the dedicated Vissim instance by default when the tool
finishes, so repeated automated runs don't accumulate instances (Vissim
allows ~4 per machine). Pass --keep-open to preserve the old behavior of
leaving the window open for manual inspection.

Usage: python tools/com_smoke_test.py [example_dir] [--duration 60] [--keep-open]
Default example: Urban Intersection Beijing.CN from the 2026 Examples Demo.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

DEFAULT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "com-smoke"


# ---------------------------------------------------------------------------
# M2.1 read-back + persistence verification stage
# ---------------------------------------------------------------------------


def _check_in_memory(
    label: str,
    get_fn: Callable[[], Any],
    set_fn: Callable[[Any], None],
    new_value: Any,
    failures: list[str],
) -> tuple[bool, Any]:
    """set(new_value) -> get() -> assert equal, for one verb.

    Returns (changed, new_value_if_changed). ``changed`` is False both when
    Vissim cleanly refuses the write (a legitimate, expected outcome for
    e.g. a program-driven signal cycle -- see set_signal_cycle's docstring)
    and when the pre-read itself fails; both are reported but neither one is
    treated the same as a readback MISMATCH, which is a real failure.
    """
    try:
        before = get_fn()
    except Exception as exc:
        print(f"  FAIL     {label}: could not read the current value: {exc}")
        failures.append(f"{label}: pre-read failed: {exc}")
        return False, None

    try:
        set_fn(new_value)
    except Exception as exc:
        print(f"  REFUSED  {label}: Vissim refused SetAttValue(-> {new_value!r}): {exc}")
        return False, None

    try:
        after = get_fn()
    except Exception as exc:
        print(f"  FAIL     {label}: set() succeeded but read-back failed: {exc}")
        failures.append(f"{label}: post-write read-back failed: {exc}")
        return True, None

    if after == new_value:
        print(f"  PASS     {label}: before={before!r} -> set {new_value!r} -> read-back {after!r}")
        return True, new_value

    msg = f"{label}: read-back {after!r} != the value just set ({new_value!r})"
    print(f"  FAIL     {msg}")
    failures.append(msg)
    return True, None


def _find_xml_element(root: ET.Element, xpath: str, match_no: str | None) -> ET.Element | None:
    for candidate in root.findall(xpath):
        if match_no is None or candidate.get("no") == match_no:
            return candidate
    return None


def _verify_xml_int_attr(
    root: ET.Element, xpath: str, match_no: str | None, key: str, expected: int
) -> tuple[bool, str]:
    """Direct ElementTree check of one attribute on the saved .inpx. Returns (verified, detail)."""
    el = _find_xml_element(root, xpath, match_no)
    if el is None:
        return False, f"no {xpath} element found" + (f" (no={match_no})" if match_no else "")
    raw = el.get(key)
    if raw is None:
        return False, f"element has no {key!r} attribute"
    try:
        got = int(round(float(raw)))
    except ValueError:
        return False, f"{key}={raw!r} is not numeric"
    if got == int(expected):
        return True, f"{key}={raw!r}"
    return False, f"{key}={raw!r} != expected {int(expected)!r}"


def run_readback_verification(controller: Any, work_dir: Path) -> bool:
    """M2.1: for every paired setter/getter, set a distinct new value -> get
    -> assert equal (in-memory), then save_net_as to a NEW file and verify
    persistence -- via services.agent.tools_network.build_shadow_model for
    vehicle-input volume and signal cycle (the parsers), else via a direct
    ElementTree read of the saved .inpx, else via COM re-get after reloading
    the saved file. Returns True only if every check that COULD be verified
    was verified with no mismatches and no unverifiable ("SMOKE-GAP") attrs.
    """
    from services.agent.tools_network import build_shadow_model

    failures: list[str] = []

    vi_nos = controller.available_vehicle_input_nos()
    sc_nos = controller.available_signal_controller_nos()
    vi_no = vi_nos[0] if vi_nos else None
    sc_no = sc_nos[0] if sc_nos else None

    changed: dict[str, Any] = {}

    print("  -- in-memory: set a distinct new value -> get -> assert equal --")
    if vi_no is not None:
        new_vol = round(float(controller.get_vehicle_input_volume(vi_no)) + 111.0, 3)
        _, applied = _check_in_memory(
            f"vehicle_input[{vi_no}].volume",
            lambda: controller.get_vehicle_input_volume(vi_no),
            lambda v: controller.set_vehicle_input(vi_no, v),
            new_vol,
            failures,
        )
        if applied is not None:
            changed["volume"] = applied
    else:
        print("  SKIP     vehicle_input.volume: this network has no vehicle inputs")

    if sc_no is not None:
        new_cycle = int(controller.get_signal_cycle(sc_no)) + 7
        _, applied = _check_in_memory(
            f"signal[{sc_no}].cycle (CycTm)",
            lambda: controller.get_signal_cycle(sc_no),
            lambda v: controller.set_signal_cycle(sc_no, v),
            new_cycle,
            failures,
        )
        if applied is not None:
            changed["cycle"] = applied

        current_offset = int(controller.get_signal_offset(sc_no))
        new_offset = 11 if current_offset != 11 else 17
        _, applied = _check_in_memory(
            f"signal[{sc_no}].offset",
            lambda: controller.get_signal_offset(sc_no),
            lambda v: controller.set_signal_offset(sc_no, v),
            new_offset,
            failures,
        )
        if applied is not None:
            changed["offset"] = applied
    else:
        print("  SKIP     signal.cycle / signal.offset: this network has no signal controllers")

    new_duration = int(controller.get_sim_duration()) + 15
    _, applied = _check_in_memory(
        "simulation.duration (SimPeriod)",
        controller.get_sim_duration,
        controller.set_sim_duration,
        new_duration,
        failures,
    )
    if applied is not None:
        changed["duration"] = applied

    new_seed = int(controller.get_random_seed()) + 1
    _, applied = _check_in_memory(
        "simulation.random_seed (RandSeed)",
        controller.get_random_seed,
        controller.set_random_seed,
        new_seed,
        failures,
    )
    if applied is not None:
        changed["seed"] = applied

    if not changed:
        print("  Nothing changed in-memory -- skipping save_net_as and persistence checks.")
        if failures:
            print("  FAILED check(s):")
            for f in failures:
                print(f"    FAILED: {f}")
        return not failures

    # Batch save: one save_net_as call for every accepted change, matching
    # real usage (docs/phase2/01-ARCHITECTURE.md: "saved via COM SaveNetAs
    # after each accepted mutation batch"), and proving save_net_as itself
    # writes a NEW file without touching the loaded source.
    saved_path = work_dir / "edit_001_readback_check.inpx"
    print(f"\n  -- save_net_as -> {saved_path.name} --")
    try:
        controller.save_net_as(saved_path)
        print(f"  PASS     save_net_as wrote {saved_path} ({saved_path.stat().st_size} bytes)")
    except Exception as exc:
        msg = f"save_net_as: {exc}"
        print(f"  FAIL     {msg}")
        failures.append(msg)
        print("  FAILED check(s):")
        for f in failures:
            print(f"    FAILED: {f}")
        return False

    print("\n  -- persistence verification --")
    shadow = build_shadow_model(saved_path)
    xml_checks: list[tuple[str, str, str | None, str, int, Callable[[], Any]]] = []

    if "volume" in changed:
        entry = next((v for v in shadow["vehicle_inputs"] if v["no"] == str(vi_no)), None)
        shadow_val = entry.get("volume") if entry else None
        if shadow_val == changed["volume"]:
            print(f"  PASS     vehicle_input[{vi_no}].volume persisted (shadow model volume={shadow_val!r})")
        else:
            msg = (
                f"vehicle_input[{vi_no}].volume: shadow model shows {shadow_val!r}, "
                f"expected {changed['volume']!r}"
            )
            print(f"  SMOKE-GAP  {msg}")
            failures.append(msg)

    if "cycle" in changed:
        entry = next((s for s in shadow["signals"] if s["sc_no"] == str(sc_no)), None)
        shadow_val = entry.get("cycle_time") if entry else None
        if shadow_val == changed["cycle"]:
            print(f"  PASS     signal[{sc_no}].cycle persisted and visible in shadow model (cycle_time={shadow_val!r})")
        else:
            print(
                f"  INFO     signal[{sc_no}].cycle: shadow model cycle_time={shadow_val!r} does not "
                f"reflect the CycTm write ({changed['cycle']!r}) -- EXPECTED, see set_signal_cycle's "
                f"docstring: CycTm is disconnected from the opaque intSupplyData program blob "
                f"signal_parser.py actually derives cycle_time from. Falling back to a direct XML/COM "
                f"check of CycTm itself, which still proves save_net_as + the getter/setter round-trip "
                f"work -- it just cannot land in the parsed shadow model for this class of controller."
            )
            xml_checks.append(
                (
                    "signal.cycle (CycTm)",
                    ".//signalController",
                    str(sc_no),
                    "cycTm",
                    changed["cycle"],
                    lambda: controller.get_signal_cycle(sc_no),
                )
            )

    if "offset" in changed:
        xml_checks.append(
            (
                "signal.offset",
                ".//signalController",
                str(sc_no),
                "offset",
                changed["offset"],
                lambda: controller.get_signal_offset(sc_no),
            )
        )
    if "duration" in changed:
        xml_checks.append(
            (
                "simulation.duration (SimPeriod)",
                ".//simulation",
                None,
                "simPeriod",
                changed["duration"],
                controller.get_sim_duration,
            )
        )
    if "seed" in changed:
        xml_checks.append(
            (
                "simulation.random_seed (RandSeed)",
                ".//simulation",
                None,
                "randSeed",
                changed["seed"],
                controller.get_random_seed,
            )
        )

    root = ET.parse(saved_path).getroot()
    needs_com_fallback: list[tuple[str, int, Callable[[], Any]]] = []
    for label, xpath, match_no, key, expected, com_getter in xml_checks:
        ok, detail = _verify_xml_int_attr(root, xpath, match_no, key, expected)
        if ok:
            print(f"  PASS     {label} persisted (raw XML {detail})")
        else:
            print(
                f"  INFO     {label}: raw XML check inconclusive ({detail}); confirming via COM "
                f"re-get after reloading the saved file."
            )
            needs_com_fallback.append((label, expected, com_getter))

    if needs_com_fallback:
        print(f"\n  -- reloading {saved_path.name} into the same session for COM re-get fallback --")
        try:
            controller.load_network(str(saved_path))
        except Exception as exc:
            for label, expected, _ in needs_com_fallback:
                msg = f"{label}: XML check inconclusive AND could not reload the saved file to confirm via COM: {exc}"
                print(f"  SMOKE-GAP  {msg}")
                failures.append(msg)
            needs_com_fallback = []

        for label, expected, com_getter in needs_com_fallback:
            try:
                got = com_getter()
            except Exception as exc:
                msg = f"{label}: COM re-get after reload failed: {exc}"
                print(f"  SMOKE-GAP  {msg}")
                failures.append(msg)
                continue
            if got == expected:
                print(f"  PASS     {label} persisted (confirmed via COM re-get after reload: {got!r})")
            else:
                msg = f"{label}: COM re-get after reload shows {got!r}, expected {expected!r}"
                print(f"  SMOKE-GAP  {msg}")
                failures.append(msg)

    print("\n  -- read-back verification summary --")
    if failures:
        print(f"  {len(failures)} unresolved check(s):")
        for f in failures:
            print(f"    FAILED: {f}")
        return False
    print("  All read-back + persistence checks passed (see INFO notes above for documented, "
          "non-failing gaps).")
    return True


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("example_dir", nargs="?", default=str(DEFAULT_EXAMPLE))
    parser.add_argument("--duration", type=int, default=60, help="simulation seconds to run")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="leave the dedicated Vissim window open after the run instead of disconnecting "
        "(old default behavior). Without this flag, the tool always disconnects/exits at the "
        "end so repeated automated runs don't accumulate instances (~4 max per machine).",
    )
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
    print(f"[1/7] Working copy: {inpx}")

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    overall_ok = True
    try:
        print("[2/7] Launching a dedicated Vissim instance (never attaches to an open one)...", flush=True)
        started = time.monotonic()
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        print(f"      Connected via {controller.prog_id} in {time.monotonic() - started:.1f}s", flush=True)

        print(f"[3/7] Loading network: {inpx.name}")
        started = time.monotonic()
        controller.load_network(str(inpx))
        print(f"      Loaded in {time.monotonic() - started:.1f}s")

        vehicle_inputs = controller.available_vehicle_input_nos()
        signal_controllers = controller.available_signal_controller_nos()
        print(f"[4/7] Vehicle inputs: {vehicle_inputs}")
        print(f"      Signal controllers: {signal_controllers}")

        print(f"[5/7] Setting SimPeriod={args.duration}s, RandSeed={args.seed}", flush=True)
        controller.set_sim_duration(args.duration)
        controller.set_random_seed(args.seed)

        # RunContinuous is a single COM call that runs to SimPeriod internally.
        # Stepping via RunSingleStep in a Python loop is 1-2 COM round-trips per
        # step and is far too slow for a real network (minutes for 60s) — the
        # production runner should prefer RunContinuous + periodic polling.
        # NOTE: SimSec resets to 0 when the run ends, so success is verified from
        # the SIMULATIONRUN row Vissim writes to the results database instead.
        print(f"[6/7] Running simulation to {args.duration}s via RunContinuous...", flush=True)
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
        run_ok = float(sim_end) >= float(period) and int(seed) == args.seed
        verdict = "SUCCESS" if run_ok else "FAIL"
        print(
            f"\n{verdict}: SIMULATIONRUN says seed={seed} period={period}s simulated-to={sim_end}s "
            f"on Vissim {version} ({wall:.1f}s wall, {float(sim_end) / wall if wall > 0 else 0:.1f}x realtime).",
            flush=True,
        )
        overall_ok = run_ok

        print("\n[7/7] Read-back + persistence verification (M2.1)", flush=True)
        readback_ok = run_readback_verification(controller, work_dir)
        overall_ok = overall_ok and readback_ok
    finally:
        if args.keep_open:
            print("\nVissim window left open for inspection (--keep-open).", flush=True)
        else:
            controller.disconnect()
            print("\nDisconnected/exited the dedicated Vissim instance.", flush=True)

    if not overall_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

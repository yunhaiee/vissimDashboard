"""v2 unit (5) Phase 1 EMPIRICAL PROBE: time-varying demand (multi-interval
vehicle-input volumes) over COM.

Answers the questions the task brief and the catalog's
``vehicle_input_multi_interval_write_gap`` finding leave open, on a live
Vissim 2026 instance (attach_to_running=False, clone, closed in a ``finally``
block):

(a) how time intervals are defined -- the ``timeIntervalSet``/``timeInterval``
    elements, and which set vehicle inputs reference (a network-wide XML
    scan across all 25 example networks showed a SINGLE named set,
    ``<timeIntervalSet no="VEHICLEINPUT">``, shared by every vehicle input in
    the network -- there is no per-input set selection in the XML).
(b) the COM surface: ``VehInput`` AttValue ids for per-interval volumes
    (``Volume(1)``, ``Volume(2)``, ... -- verify indexing basis: 1-based or
    0-based) and what happens reading an interval index that does not exist
    (fail open -- returns something, e.g. None/0/last -- or fail closed --
    raises). Also the ``Cont(i)`` continuous flag (the catalog's named trap:
    an interval with ``cont=true`` inherits interval 1's volume; the task
    brief hypothesizes ``Volume(i)`` may need ``Cont(i)=0`` first -- verified
    empirically here, not assumed).
(c) whether ``SetAttValue Volume(i)`` on a genuinely non-existent interval
    index (beyond the network's own interval-set length) creates it or
    raises.
(d) XML serialization after ``save_net_as`` -- does the persisted
    ``<timeIntervalVehVolume>`` reflect the new volume AND (if flipped) the
    new ``cont`` attribute, with the untouched intervals byte-for-byte
    unchanged?

Network selection: a network-wide scan
(``backend/tools/vehicle_input_interval_probe.py``'s own pre-flight, see the
scan this docstring documents) found THREE real Vissim 2026 example networks
with vehicle inputs that mix ``cont="true"`` and ``cont="false"`` across
intervals: Border Crossing Breitenau.DE, LRT Priority Luxembourg.LU, Railroad
Trucks Portland.US. This probe defaults to Border Crossing Breitenau.DE,
vehicle input no=2 (4 intervals: interval 1 ``cont=false`` volume=1000;
intervals 2-4 ``cont=true`` volume=1000 each, i.e. genuinely continuous from
interval 1) -- the smallest network exhibiting the Cont-flag trap the task
brief asks to verify.

Never touches the original Examples Demo folder (00-EXECUTION-GUIDE.md rule
4) -- everything happens on a work-dir clone. Always disconnects the
dedicated Vissim instance in a ``finally`` block.

Usage: python tools/vehicle_input_interval_probe.py [example_dir] [input_no]
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Border Crossing Breitenau.DE"
)
DEFAULT_INPUT_NO = "2"
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "vehicle-input-interval-probe"

_CONTROL_BOGUS_ATTR = "ThisAttributeDoesNotExistProbe12345"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh_work_dir(name: str) -> Path:
    for stale in WORK_ROOT.glob(f"{name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    return WORK_ROOT / f"{name}_{os.getpid()}"


# ---------------------------------------------------------------------------
# Phase 1a: static type-library scan (no COM instance required)
# ---------------------------------------------------------------------------


def probe_typelib() -> None:
    import win32com.client.makepy as makepy
    import win32com.client.selecttlb as selecttlb
    from win32com.client import gencache

    _log("[1a] Locating the installed 'Vissim Object Library' type library via selecttlb...")
    candidates = [
        tlb
        for tlb in selecttlb.EnumTlbs()
        if "vissim object library" in (getattr(tlb, "desc", "") or "").lower()
    ]
    if not candidates:
        raise RuntimeError(
            "No registered 'Vissim Object Library' type library found via "
            "win32com.client.selecttlb.EnumTlbs() -- cannot run the static scan."
        )

    def _hex_int(value: str) -> int:
        return int(value, 16)

    match = max(candidates, key=lambda t: (_hex_int(t.major), _hex_int(t.minor)))
    for tlb in candidates:
        _log(f"      registered: {tlb.desc}  clsid={tlb.clsid} major={tlb.major} minor={tlb.minor}")
    _log(f"      using: {match.desc}  clsid={match.clsid}")

    major_i, minor_i = _hex_int(match.major), _hex_int(match.minor)
    makepy.GenerateFromTypeLibSpec((match.clsid, match.lcid, major_i, minor_i))
    mod = gencache.EnsureModule(match.clsid, match.lcid, major_i, minor_i)
    src_path = Path(mod.__file__)
    _log(f"      Generated wrapper: {src_path} ({src_path.stat().st_size} bytes)")
    lines = src_path.read_text(encoding="utf-8", errors="replace").splitlines()

    keywords = ("vehicleinput", "timeinterval")
    class_name = None
    in_target = False
    target_hits = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("class I") and "(DispatchBaseClass)" in stripped:
            class_name = stripped.split("(")[0].removeprefix("class ").strip()
            in_target = any(kw in class_name.lower() for kw in keywords)
            if in_target:
                target_hits += 1
                _log(f"\n      -- {class_name} (DispatchBaseClass) --")
            continue
        if in_target and stripped.startswith("def ") and "(self" in stripped:
            _log(f"        {stripped}")
        if in_target and (stripped.startswith("_prop_map_get_") or stripped.startswith("_prop_map_put_")):
            _log(f"        {stripped}")

    if target_hits == 0:
        _log("      NO interface with 'VehicleInput'/'TimeInterval' in its name found in the type library.")
    else:
        _log(f"\n      Found {target_hits} matching interface(s) in the type library (listed above).")


# ---------------------------------------------------------------------------
# Network-wide pre-scan: which example networks have multi-interval inputs,
# and which of THOSE mix cont=true/false (the interesting trap case)
# ---------------------------------------------------------------------------


def prescan_example_networks(examples_root: Path) -> None:
    _log("[0] Pre-scanning all example networks for multi-interval vehicleInput elements...")
    for inpx in sorted(examples_root.glob("**/*.inpx")):
        try:
            text = inpx.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        try:
            root = ET.parse(inpx).getroot()
        except Exception:
            continue
        multi = 0
        mixed_cont = 0
        for vi in root.findall(".//vehicleInput"):
            ivs = vi.findall(".//timeIntervalVehVolume")
            if len(ivs) > 1:
                multi += 1
                conts = {iv.get("cont") for iv in ivs}
                if len(conts) > 1:
                    mixed_cont += 1
        if multi:
            rel = inpx.relative_to(examples_root)
            _log(f"      {rel}: {multi} multi-interval input(s), {mixed_cont} with mixed cont=true/false")


# ---------------------------------------------------------------------------
# Phase 1b: live probe against a network clone
# ---------------------------------------------------------------------------


def _xml_vehicle_input(inpx_path: Path, input_no: str) -> dict[str, Any] | None:
    root = ET.parse(inpx_path).getroot()
    for vi in root.findall(".//vehicleInput"):
        if vi.get("no") == input_no:
            intervals = [dict(iv.attrib) for iv in vi.findall(".//timeIntervalVehVolume")]
            return {"attrib": dict(vi.attrib), "intervals": intervals}
    return None


def _xml_time_interval_set(inpx_path: Path, set_no: str = "VEHICLEINPUT") -> list[str]:
    root = ET.parse(inpx_path).getroot()
    for tis in root.findall(".//timeIntervalSet"):
        if tis.get("no") == set_no:
            return [ti.get("start", "") for ti in tis.findall(".//timeInterval")]
    return []


def _try_read(item: Any, attr_id: str) -> tuple[bool, Any]:
    try:
        value = item.AttValue(attr_id)
        return True, value
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    input_no = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_INPUT_NO
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    _log("#" * 78)
    _log("# PHASE 0 -- network-wide pre-scan (which example networks have multi-interval inputs)")
    _log("#" * 78)
    prescan_example_networks(example_dir.parent)

    _log("\n" + "#" * 78)
    _log("# PHASE 1a -- static type-library scan (no COM instance, no licence used)")
    _log("#" * 78)
    probe_typelib()

    _log("\n" + "#" * 78)
    _log(f"# PHASE 1b -- live probe on a {example_dir.name} clone, vehicle input no={input_no}")
    _log("#" * 78)

    work_dir = _fresh_work_dir(example_dir.name)
    shutil.copytree(example_dir, work_dir)
    inpx_files = sorted(work_dir.glob("*.inpx"))
    if not inpx_files:
        raise SystemExit(f"No .inpx in {work_dir}")
    working = inpx_files[0]
    _log(f"[1b.0] Working copy: {working}")

    xml_set = _xml_time_interval_set(working)
    _log(f"       XML timeIntervalSet[no='VEHICLEINPUT'] starts (s) = {xml_set}")
    xml_before = _xml_vehicle_input(working, input_no)
    if xml_before is None:
        raise SystemExit(f"vehicleInput no={input_no!r} not found in {working}")
    _log(f"       XML vehicleInput no={input_no}: attrib={xml_before['attrib']}")
    for i, iv in enumerate(xml_before["intervals"], start=1):
        _log(f"         interval[{i}] (xml order): {iv}")

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    try:
        _log("[1b.1] Launching a dedicated Vissim instance (attach_to_running=False)...")
        started = time.monotonic()
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        _log(f"       Connected via {controller.prog_id} in {time.monotonic() - started:.1f}s")

        _log(f"[1b.2] Loading network: {working.name}")
        controller.load_network(str(working))

        vehicle_inputs = controller.vissim.Net.VehicleInputs
        item = vehicle_inputs.ItemByKey(int(input_no))

        n_xml_intervals = len(xml_before["intervals"])
        _log(f"\n[1b.3] Reading Volume(i)/Cont(i) for i=1..{n_xml_intervals + 2} "
             f"(XML says {n_xml_intervals} intervals exist; probing 2 past the end)...")
        for i in range(1, n_xml_intervals + 3):
            ok_v, val_v = _try_read(item, f"Volume({i})")
            ok_c, val_c = _try_read(item, f"Cont({i})")
            _log(f"       i={i}: Volume({i}) ok={ok_v} -> {val_v!r}   |   Cont({i}) ok={ok_c} -> {val_c!r}")

        _log("\n[1b.4] Index-basis check: Volume(0) / Cont(0)...")
        ok_v0, val_v0 = _try_read(item, "Volume(0)")
        ok_c0, val_c0 = _try_read(item, "Cont(0)")
        _log(f"       Volume(0) ok={ok_v0} -> {val_v0!r}   |   Cont(0) ok={ok_c0} -> {val_c0!r}")

        _log("\n[1b.5] CONTROL: bogus indexed attribute id (fail-open/closed check)...")
        ok_bogus, val_bogus = _try_read(item, f"{_CONTROL_BOGUS_ATTR}(1)")
        _log(f"       {_CONTROL_BOGUS_ATTR}(1) ok={ok_bogus} -> {val_bogus!r}")

        # -------------------------------------------------------------
        # Locate an interval with cont=true (per XML) to test the trap:
        # does SetAttValue Volume(i) work directly, or does Vissim refuse/
        # silently ignore it while Cont(i) is still true?
        # -------------------------------------------------------------
        cont_true_indices = [
            idx for idx, iv in enumerate(xml_before["intervals"], start=1) if iv.get("cont") == "true"
        ]
        if not cont_true_indices:
            _log("\n[1b.6] No cont=true interval found on this input -- skipping the Cont-flag trap test.")
        else:
            target_i = cont_true_indices[0]
            _log(f"\n[1b.6] Target interval i={target_i} has XML cont='true' (inherits interval 1's volume). "
                 "Testing the Cont-flag trap...")

            ok_before, cont_before = _try_read(item, f"Cont({target_i})")
            ok_volb, vol_before = _try_read(item, f"Volume({target_i})")
            _log(f"       BEFORE any write: Cont({target_i})={cont_before!r} (ok={ok_before}), "
                 f"Volume({target_i})={vol_before!r} (ok={ok_volb})")

            new_volume_attempt_1 = float(vol_before) + 777 if ok_volb else 999.0
            _log(f"       [attempt A] SetAttValue Volume({target_i}, {new_volume_attempt_1}) "
                 f"WITHOUT touching Cont({target_i}) first...")
            try:
                item.SetAttValue(f"Volume({target_i})", new_volume_attempt_1)
                _log("         SetAttValue succeeded (no exception).")
            except Exception as exc:
                _log(f"         SetAttValue RAISED: {type(exc).__name__}: {exc}")

            ok_after_a, vol_after_a = _try_read(item, f"Volume({target_i})")
            ok_cont_after_a, cont_after_a = _try_read(item, f"Cont({target_i})")
            _log(f"       Read-back after attempt A: Volume({target_i})={vol_after_a!r} (ok={ok_after_a}), "
                 f"Cont({target_i})={cont_after_a!r} (ok={ok_cont_after_a})")
            _log(f"       Attempt A actually changed the value: "
                 f"{ok_after_a and ok_volb and float(vol_after_a) != float(vol_before)}")

            _log(f"\n       [attempt B] SetAttValue Cont({target_i}, False) FIRST, then Volume({target_i})...")
            try:
                item.SetAttValue(f"Cont({target_i})", False)
                _log(f"         SetAttValue Cont({target_i}, False) succeeded.")
            except Exception as exc:
                _log(f"         SetAttValue Cont(...) RAISED: {type(exc).__name__}: {exc}")
            ok_cont_after_clear, cont_after_clear = _try_read(item, f"Cont({target_i})")
            _log(f"       Cont({target_i}) read-back after clearing = {cont_after_clear!r} (ok={ok_cont_after_clear})")

            new_volume_attempt_2 = float(vol_before) + 333 if ok_volb else 555.0
            _log(f"       Now SetAttValue Volume({target_i}, {new_volume_attempt_2})...")
            try:
                item.SetAttValue(f"Volume({target_i})", new_volume_attempt_2)
                _log("         SetAttValue succeeded (no exception).")
            except Exception as exc:
                _log(f"         SetAttValue RAISED: {type(exc).__name__}: {exc}")
            ok_after_b, vol_after_b = _try_read(item, f"Volume({target_i})")
            _log(f"       Read-back after attempt B: Volume({target_i})={vol_after_b!r} (ok={ok_after_b})")
            _log(f"       Attempt B (after clearing Cont) actually changed the value: "
                 f"{ok_after_b and float(vol_after_b) == new_volume_attempt_2}")

            # Check whether interval 1 (untouched) is unaffected, and whether
            # a LATER still-cont=true interval (e.g. target_i+1, if it exists
            # and is still cont=true) now silently mirrors the NEW value
            # (confirms "continuous" semantics chain forward from whichever
            # interval it inherits from).
            ok_v1, vol1_after = _try_read(item, "Volume(1)")
            _log(f"       Volume(1) (never touched) after all writes = {vol1_after!r} (ok={ok_v1})")
            next_i = target_i + 1
            if next_i in cont_true_indices or (
                next_i <= n_xml_intervals and xml_before["intervals"][next_i - 1].get("cont") == "true"
            ):
                ok_next, vol_next = _try_read(item, f"Volume({next_i})")
                _log(f"       Volume({next_i}) (still cont=true per XML, downstream of target) "
                     f"after writing target = {vol_next!r} (ok={ok_next})")

        # -------------------------------------------------------------
        # (c) SetAttValue on a genuinely non-existent interval index
        # -------------------------------------------------------------
        beyond_i = n_xml_intervals + 5
        _log(f"\n[1b.7] SetAttValue Volume({beyond_i}) on an interval index far beyond the network's "
             f"own interval-set length ({n_xml_intervals})...")
        try:
            item.SetAttValue(f"Volume({beyond_i})", 42.0)
            _log("       SetAttValue succeeded (no exception) -- checking whether it actually created anything...")
            ok_beyond, val_beyond = _try_read(item, f"Volume({beyond_i})")
            _log(f"       Read-back Volume({beyond_i}) = {val_beyond!r} (ok={ok_beyond})")
        except Exception as exc:
            _log(f"       SetAttValue RAISED: {type(exc).__name__}: {exc}")

        # -------------------------------------------------------------
        # (a) time-interval-set discovery via COM, if it exists
        # -------------------------------------------------------------
        _log("\n[1b.8] Looking for a COM surface for the time-interval-SET itself "
              "(Net.TimeIntervalSets or similar)...")
        for candidate_attr in ("TimeIntervalSets", "TimeIntervalSet", "TimeIntervals"):
            try:
                obj = getattr(controller.vissim.Net, candidate_attr)
                _log(f"       Net.{candidate_attr} exists: {obj!r}")
                try:
                    items = list(obj.GetAll())
                    _log(f"         .GetAll() count = {len(items)}")
                except Exception as exc2:
                    _log(f"         .GetAll() raised: {type(exc2).__name__}: {exc2}")
            except Exception as exc:
                _log(f"       Net.{candidate_attr} -> {type(exc).__name__}: {exc}")

        # -------------------------------------------------------------
        # (d) Persist + reparse: does the saved XML reflect the writes?
        # -------------------------------------------------------------
        tmp_path = working.with_name(working.stem + "_saving" + working.suffix)
        _log(f"\n[1b.9] Persisting via save_net_as({tmp_path.name}) + os.replace onto {working.name} "
             "(same route VissimEditSession.persist() uses)...")
        controller.save_net_as(tmp_path)
        os.replace(tmp_path, working)
        _log(f"       Persisted. Working copy size = {working.stat().st_size} bytes")

        xml_after = _xml_vehicle_input(working, input_no)
        _log(f"[1b.10] Saved XML vehicleInput no={input_no}:")
        for i, iv in enumerate(xml_after["intervals"], start=1):
            _log(f"         interval[{i}] (xml order): {iv}")

        _log("\n[1b.11] Fresh COM reload of the saved file (separate connection) to confirm persistence...")
        controller.disconnect()
        verify_controller = VissimController()
        verify_controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        verify_controller.load_network(str(working))
        reload_item = verify_controller.vissim.Net.VehicleInputs.ItemByKey(int(input_no))
        for i in range(1, n_xml_intervals + 1):
            ok_rv, rv = _try_read(reload_item, f"Volume({i})")
            ok_rc, rc = _try_read(reload_item, f"Cont({i})")
            _log(f"       reload i={i}: Volume={rv!r} (ok={ok_rv})  Cont={rc!r} (ok={ok_rc})")

        controller = verify_controller  # so the finally block disconnects this one

        _log("\n" + "#" * 78)
        _log("# SUMMARY (see full transcript above for exact ok/raise text per probe)")
        _log("#" * 78)

    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        _log("\nDisconnected the dedicated Vissim instance.")


if __name__ == "__main__":
    main()

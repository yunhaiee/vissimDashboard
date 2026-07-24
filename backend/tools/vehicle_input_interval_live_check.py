"""LIVE ACCEPTANCE for v2 unit (5): time-varying demand (multi-interval
vehicle-input volumes) end to end on a Border Crossing Breitenau.DE clone --
preflight -> dispatch -> COM readback match -> persisted evidence (XML grep,
interval 1 untouched) -> load_check still clean.

[Phase 1] Static parse -- no COM. Parses the REAL example-network XML
directly through ``services.agent.tools_network.build_shadow_model`` and
asserts vehicle input no=2 carries the full 4-interval profile the Phase 1
probe found (backend/tools/vehicle_input_interval_probe.py): interval 1
cont=false volume=1000, intervals 2-4 cont=true volume=1000 (mirrored).

[Phase 2] Real preflight path -- no COM. Clones the network via a real
``ArtifactManager``, builds real action dicts, and runs them through the REAL
``GuardrailEngine().preflight()``:
  - interval_no=99 (far beyond the input's 4 real intervals) REFUSES via
    vehicle_input_interval_out_of_range.
  - interval_no=2 (real) PASSES (no refusal).
  - interval_no=1 (default) on this SAME multi-interval input carries the
    vehicle_input_multi_interval_write_gap WARNING (not blocking).

[Phase 3] ONE live COM write, full pipeline (preflight -> toolkit.dispatch ->
postwrite), against a CLONE (never the original .inpx): set_vehicle_input_
volume(input_no=2, interval_no=2, volume=<new>) on an interval whose Cont
flag is TRUE per the network's own XML -- exercising the Cont-flag trap
end-to-end through the real write tool, not just the raw COM probe. Proves:
preflight lets the write through, the COM write+readback succeed (with
Cont(2) cleared and reported in the verification payload), the persisted
working copy's XML shows interval 2 changed and interval 1 UNTOUCHED, and
load_check on that same working copy still comes back clean.

Always closes every Vissim instance in a ``finally`` block;
attach_to_running=False throughout. Never touches the original Examples Demo
folder.

Usage: python tools/vehicle_input_interval_live_check.py
"""
from __future__ import annotations

import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO_ROOT = BACKEND_ROOT.parent
DEFAULT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Border Crossing Breitenau.DE"
)
DEFAULT_INPUT_NO = "2"
DEFAULT_INTERVAL_NO = 2
WORK_ROOT = REPO_ROOT / "runtime" / "vehicle-input-interval-live-check"

_FAILURES: list[str] = []


def _log(msg: str) -> None:
    print(msg, flush=True)


def _check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        _log(f"  PASS  {label}")
    else:
        _log(f"  FAIL  {label}  {detail}")
        _FAILURES.append(label)


def _fresh_work_dir(suffix: str) -> Path:
    import os

    for stale in WORK_ROOT.glob(f"{suffix}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    work_dir = WORK_ROOT / f"{suffix}_{os.getpid()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _find_inpx(example_dir: Path) -> Path:
    candidates = sorted(example_dir.glob("*.inpx"))
    if not candidates:
        raise SystemExit(f"No .inpx in {example_dir}")
    return candidates[0]


def _copy_sibling_assets(example_dir: Path, work_dir: Path) -> None:
    """Copy every non-.inpx file from ``example_dir`` into ``work_dir``.

    LIVE FINDING (Border Crossing Breitenau.DE, this script's first run):
    ``ArtifactManager.clone()`` (services/agent/state.py) only
    ``shutil.copyfile``s the single .inpx -- it never copies sibling assets
    a network's signal controllers may reference by relative path (this
    network's controller loads an external ``parking control.vap`` file
    alongside the .inpx). Without it, ``load_check`` correctly reports
    ``init_ok=False`` ("Import file ... not found") -- a real, pre-existing
    ArtifactManager limitation, NOT something v2 unit ⑤ introduced or is
    scoped to fix (out of this task's SCOPE). This helper is a SCRIPT-LEVEL
    workaround so this live check exercises the vehicle-input-interval write
    path specifically, not a fix to the shared ArtifactManager.
    """
    for path in example_dir.iterdir():
        if path.is_file() and path.suffix.lower() != ".inpx":
            shutil.copyfile(path, work_dir / path.name)


def _xml_vehicle_input(inpx_path: Path, input_no: str) -> dict[str, Any] | None:
    root = ET.parse(inpx_path).getroot()
    for vi in root.findall(".//vehicleInput"):
        if vi.get("no") == input_no:
            intervals = [dict(iv.attrib) for iv in vi.findall(".//timeIntervalVehVolume")]
            return {"attrib": dict(vi.attrib), "intervals": intervals}
    return None


# ---------------------------------------------------------------------------
# Phase 1: static parse
# ---------------------------------------------------------------------------


def phase_1_static_parse(example_inpx: Path, input_no: str) -> None:
    _log("=" * 78)
    _log("[Phase 1] tools_network.build_shadow_model against the real Border Crossing XML")
    _log("=" * 78)

    from services.agent.tools_network import build_shadow_model

    shadow = build_shadow_model(example_inpx)
    vehicle_inputs = shadow.get("vehicle_inputs") or []
    target = next((vi for vi in vehicle_inputs if vi["no"] == input_no), None)
    _check(f"vehicle input no={input_no} present in shadow model", target is not None)
    if target is None:
        return

    _log(f"[1.1] vehicle_inputs[no={input_no}] = {target}")
    _check("interval_count == 4", target["interval_count"] == 4, detail=str(target["interval_count"]))
    _check(
        "volume (backward-compat top-level) == interval 1's volume == 1000.0",
        target["volume"] == 1000.0,
        detail=str(target["volume"]),
    )
    volumes = target["volumes"]
    _check("volumes has 4 entries", len(volumes) == 4, detail=str(len(volumes)))
    _check(
        "interval 1: {interval_no:1, from_s:0.0, volume:1000.0, continuous:False}",
        volumes[0] == {"interval_no": 1, "from_s": 0.0, "volume": 1000.0, "continuous": False},
        detail=str(volumes[0]),
    )
    for i in (1, 2, 3):
        _check(
            f"interval {i + 1}: continuous=True, volume=1000.0 (mirrors interval 1)",
            volumes[i]["continuous"] is True and volumes[i]["volume"] == 1000.0,
            detail=str(volumes[i]),
        )


# ---------------------------------------------------------------------------
# Phase 2: real preflight path (clone + GuardrailEngine, no COM)
# ---------------------------------------------------------------------------


def phase_2_real_preflight(example_inpx: Path, input_no: str, interval_no: int) -> None:
    _log("")
    _log("=" * 78)
    _log("[Phase 2] Real preflight path: ArtifactManager.clone() -> GuardrailEngine().preflight()")
    _log("=" * 78)

    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager

    engine = GuardrailEngine()

    work_dir = _fresh_work_dir("phase2-bogus-interval")
    am = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir)
    shadow = am.clone()
    action_bogus = {
        "tool": "set_vehicle_input_volume",
        "params": {"input_no": input_no, "volume": 1500, "interval_no": 99},
    }
    report_bogus = engine.preflight(action_bogus, shadow)
    ids_bogus = [f.constraint_id for f in report_bogus.findings]
    _log(f"[2.1] interval_no=99 preflight findings: {ids_bogus} (blocking={report_bogus.blocking})")
    _check(
        "interval_no=99 REFUSES via vehicle_input_interval_out_of_range",
        "vehicle_input_interval_out_of_range" in ids_bogus and report_bogus.blocking,
        detail=str(ids_bogus),
    )

    work_dir2 = _fresh_work_dir("phase2-real-interval")
    am2 = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir2)
    shadow2 = am2.clone()
    action_real = {
        "tool": "set_vehicle_input_volume",
        "params": {"input_no": input_no, "volume": 1333, "interval_no": interval_no},
    }
    report_real = engine.preflight(action_real, shadow2)
    ids_real = [f.constraint_id for f in report_real.findings]
    _log(f"[2.2] interval_no={interval_no} preflight findings: {ids_real} (blocking={report_real.blocking})")
    _check(
        f"interval_no={interval_no} PASSES (no refusal)",
        not report_real.blocking,
        detail=str(ids_real),
    )
    _check(
        "interval_no>1 write does NOT carry vehicle_input_multi_interval_write_gap",
        "vehicle_input_multi_interval_write_gap" not in ids_real,
        detail=str(ids_real),
    )

    action_interval1 = {
        "tool": "set_vehicle_input_volume",
        "params": {"input_no": input_no, "volume": 1333},
    }
    report_i1 = engine.preflight(action_interval1, shadow2)
    ids_i1 = [f.constraint_id for f in report_i1.findings]
    _log(f"[2.3] interval_no absent (default 1) preflight findings: {ids_i1} (blocking={report_i1.blocking})")
    _check(
        "default interval_no=1 write on this multi-interval input carries the gap WARNING",
        "vehicle_input_multi_interval_write_gap" in ids_i1,
        detail=str(ids_i1),
    )
    gap_finding = next(f for f in report_i1.findings if f.constraint_id == "vehicle_input_multi_interval_write_gap")
    _check("the gap finding is warn-tier, not blocking", gap_finding.tier == "warn" and not report_i1.blocking)


# ---------------------------------------------------------------------------
# Phase 3: ONE live COM write, full pipeline, then load_check
# ---------------------------------------------------------------------------


def phase_3_live_com_write(example_inpx: Path, input_no: str, interval_no: int) -> bool:
    _log("")
    _log("=" * 78)
    _log(
        f"[Phase 3] ONE live COM write: set_vehicle_input_volume(input_no={input_no!r}, "
        f"interval_no={interval_no}) on a CLONE of Border Crossing Breitenau.DE"
    )
    _log("           (full pipeline: preflight -> toolkit.dispatch -> postwrite -> persisted XML -> load_check)")
    _log("=" * 78)

    import services.agent.tools_check  # noqa: F401 - registers load_check
    import services.agent.tools_edit  # noqa: F401 - registers the write tools
    from services.agent.edit_session import (
        VissimEditSession,
        clear_active_edit_session,
        set_active_edit_session,
    )
    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    from services.agent.tools_run import clear_active_run_source, set_active_run_source
    from services.agent.toolkit import default_registry, dispatch

    work_dir = _fresh_work_dir("phase3-live-write")
    am = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir)
    before_shadow = am.clone()
    _copy_sibling_assets(example_inpx.parent, work_dir)
    _log(f"[3.1] Cloned Border Crossing Breitenau.DE -> {am.working_path} (never the original .inpx)")
    _log(f"      Copied sibling assets (e.g. external VAP signal-program files) into {work_dir} "
         "-- ArtifactManager.clone() itself only copies the .inpx (see _copy_sibling_assets docstring).")

    before_xml = _xml_vehicle_input(am.working_path, input_no)
    _log(f"      vehicle input {input_no} before (working copy XML): {before_xml}")
    old_volume = float(before_xml["intervals"][interval_no - 1]["volume"])
    old_cont = before_xml["intervals"][interval_no - 1]["cont"]
    new_volume = old_volume + 333.0
    _log(f"      target interval {interval_no}: cont={old_cont!r} volume={old_volume} -> new volume={new_volume}")

    interval1_before = dict(before_xml["intervals"][0])

    action = {
        "tool": "set_vehicle_input_volume",
        "params": {"input_no": input_no, "volume": new_volume, "interval_no": interval_no},
    }
    engine = GuardrailEngine()

    write_ok = False
    session = VissimEditSession(am.working_path)
    try:
        _log("[3.2] preflight (real GuardrailEngine, real cloned shadow model)...")
        pre_report = engine.preflight(action, before_shadow)
        pre_ids = [f.constraint_id for f in pre_report.findings]
        _log(f"      findings={pre_ids} blocking={pre_report.blocking}")
        _check("Preflight PASSES for the real interval write", not pre_report.blocking, detail=str(pre_ids))
        if pre_report.blocking:
            return False

        set_active_edit_session(session)
        _log(
            f"[3.3] Dispatching set_vehicle_input_volume(input_no={input_no!r}, volume={new_volume}, "
            f"interval_no={interval_no}) via toolkit.dispatch (real COM)..."
        )
        envelope = dispatch(
            "set_vehicle_input_volume",
            {"input_no": input_no, "volume": new_volume, "interval_no": interval_no},
            registry=default_registry,
        )
        _log(f"      envelope = {envelope}")
        _check("dispatch() status == ok", envelope.get("status") == "ok", detail=str(envelope))
        verification = envelope.get("verification", {})
        _check(
            "written_value == readback_value == new_volume (live COM read-back)",
            verification.get("written_value") == new_volume
            and verification.get("readback_value") == new_volume,
            detail=str(verification),
        )
        _check(
            "cont_after present and False (Cont(interval_no) cleared, per the verified live trap)",
            verification.get("cont_after") is False,
            detail=str(verification),
        )

        after_shadow = build_shadow_model(am.working_path)
        postwrite_report = engine.postwrite(action, before_shadow, after_shadow, verification)
        post_ids = [f.constraint_id for f in postwrite_report.findings]
        _log(f"[3.4] postwrite findings={post_ids} blocking={postwrite_report.blocking}")
        _check(
            "Postwrite has no write_verification_failed finding",
            "write_verification_failed" not in post_ids,
            detail=str(post_ids),
        )

        after_xml = _xml_vehicle_input(am.working_path, input_no)
        _log(f"[3.5] vehicle input {input_no} after (working copy XML, post-SaveNetAs): {after_xml}")
        after_target_interval = after_xml["intervals"][interval_no - 1]
        _check(
            f"Persisted XML interval {interval_no} volume == new_volume",
            float(after_target_interval["volume"]) == new_volume,
            detail=f"expected {new_volume}, got {after_target_interval}",
        )
        _check(
            f"Persisted XML interval {interval_no} cont flipped to false",
            after_target_interval.get("cont") == "false",
            detail=str(after_target_interval),
        )
        interval1_after = after_xml["intervals"][0]
        _check(
            "Persisted XML interval 1 UNTOUCHED (only interval "
            f"{interval_no} moved)",
            interval1_after == interval1_before,
            detail=f"before={interval1_before} after={interval1_after}",
        )

        write_ok = (
            envelope.get("status") == "ok"
            and float(after_target_interval["volume"]) == new_volume
            and interval1_after == interval1_before
            and not pre_report.blocking
        )
    finally:
        clear_active_edit_session()
        session.close()
        _log("[3.6] Vissim edit session closed.")

    if not write_ok:
        return False

    _log("[3.7] load_check on the SAME working copy -- must still be clean after the interval edit...")
    set_active_run_source(am.working_path)
    try:
        check_envelope = dispatch("load_check", {}, registry=default_registry)
    finally:
        clear_active_run_source()
    _log(f"      load_check envelope = {check_envelope}")
    _check("load_check dispatch status == ok", check_envelope.get("status") == "ok", detail=str(check_envelope))
    check_verification = check_envelope.get("verification", {})
    _check(
        "load_check reports load_ok=True, init_ok=True after the edit",
        check_verification == {"load_ok": True, "init_ok": True},
        detail=str(check_verification),
    )

    return write_ok and check_verification == {"load_ok": True, "init_ok": True}


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    input_no = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_INPUT_NO
    interval_no = int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_INTERVAL_NO
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")
    example_inpx = _find_inpx(example_dir)

    phase_1_static_parse(example_inpx, input_no)
    phase_2_real_preflight(example_inpx, input_no, interval_no)
    phase_3_ok = phase_3_live_com_write(example_inpx, input_no, interval_no)

    _log("")
    _log("=" * 78)
    if _FAILURES:
        _log(f"RESULT: FAIL -- {len(_FAILURES)} check(s) failed: {_FAILURES}")
    elif not phase_3_ok:
        _log("RESULT: FAIL -- Phase 3 live COM write did not complete cleanly.")
    else:
        _log("RESULT: PASS -- all phases green.")
    _log("=" * 78)
    if _FAILURES or not phase_3_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

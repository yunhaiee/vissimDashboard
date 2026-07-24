"""Live acceptance for v2 unit ①: signal controller TYPE extraction unlocks
set_signal_offset preflight for real fixed-time-family controllers, while
still refusing genuinely unsupported/unknown ones.

[Phase 1] Static parse -- no COM. Parses the REAL example-network XML
(``runtime/com-smoke/*/*.inpx``, the same corpus com_smoke_test.py/
smoke_parse_examples.py already exercise) directly through
``services.signal_parser.parse_signal_controllers`` and asserts
``controller_type`` is extracted correctly: Beijing SC 1 = "FIXEDTIME"
(program-driven, per the M2.1 CycTm finding), Air Taxi Vertiport SC 11/14/21
= "FIXEDTIMESIMPLE", SC 12/13 = "RAILWAYCROSSING".

[Phase 2] Real preflight path -- no COM. Clones each example network via a
real ``ArtifactManager`` (file copy + real XML reparse, exactly what a live
agent turn does before any COM touches the network), builds the real action
dict, and runs it through the REAL ``GuardrailEngine().preflight()`` (the
same call orchestrator.py makes). Asserts:
  - set_signal_offset on Beijing SC 1 (FIXEDTIME) PASSES (no
    signal_controller_type_unsupported finding).
  - set_signal_offset on Air Taxi SC 11 (FIXEDTIMESIMPLE) PASSES too --
    the M2.1 "both networks" live evidence this task cites.
  - set_signal_offset on Air Taxi SC 12 (RAILWAYCROSSING, a REAL
    non-COM-writable type) REFUSES.
  - set_signal_offset on a synthetic "unknown"-type fixture REFUSES (the
    conservative gap-era stance, still correct now that the field is real).

[Phase 3] ONE live COM write, full pipeline (preflight -> toolkit.dispatch
-> postwrite), against a CLONE of Beijing (never the original .inpx):
set_signal_offset on SC 1. Proves preflight now genuinely lets the write
through, the COM write+readback succeed, and the persisted working copy's
<signalController offset="..."> attribute round-trips on disk. Always closes
Vissim in a ``finally`` block; attach_to_running=False throughout (via
VissimEditSession, per 00-EXECUTION-GUIDE.md rule 3).

Usage: python tools/signal_controller_type_live_check.py
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
BEIJING_DIR = REPO_ROOT / "runtime" / "com-smoke" / "Urban Intersection Beijing.CN_31360"
AIR_TAXI_DIR = REPO_ROOT / "runtime" / "com-smoke" / "Air Taxi Vertiport_42580"
# Named explicitly (not globbed) -- both dirs also carry a leftover
# edit_001_readback_check.inpx from a prior com_smoke_test.py run;
# next(dir.glob("*.inpx")) is not deterministic about which one it picks.
BEIJING_INPX = BEIJING_DIR / "Intersection Beijing.inpx"
AIR_TAXI_INPX = AIR_TAXI_DIR / "Air Taxi Vertiport.inpx"
WORK_ROOT = REPO_ROOT / "runtime" / "signal-controller-type-live-check"

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
    for stale in WORK_ROOT.glob(f"{suffix}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    import os

    work_dir = WORK_ROOT / f"{suffix}_{os.getpid()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


# ---------------------------------------------------------------------------
# Phase 1: static parse
# ---------------------------------------------------------------------------


def phase_1_static_parse() -> None:
    _log("=" * 78)
    _log("[Phase 1] signal_parser.parse_signal_controllers against the real example XML")
    _log("=" * 78)

    from services.signal_parser import parse_signal_controllers

    beijing = parse_signal_controllers(BEIJING_INPX)
    _log(f"[1.1] Beijing ({BEIJING_INPX.name}): {[(k, v['controller_type']) for k, v in beijing.items()]}")
    _check("Beijing SC 1 controller_type == FIXEDTIME", beijing.get("1", {}).get("controller_type") == "FIXEDTIME")
    _check("Beijing SC 1 cycle_time > 0 (program-driven, opaque blob)", beijing.get("1", {}).get("cycle_time", 0) > 0)

    air_taxi = parse_signal_controllers(AIR_TAXI_INPX)
    types = {k: v["controller_type"] for k, v in air_taxi.items()}
    _log(f"[1.2] Air Taxi Vertiport ({AIR_TAXI_INPX.name}): {types}")
    _check("Air Taxi SC 11 controller_type == FIXEDTIMESIMPLE", types.get("11") == "FIXEDTIMESIMPLE")
    _check("Air Taxi SC 14 controller_type == FIXEDTIMESIMPLE", types.get("14") == "FIXEDTIMESIMPLE")
    _check("Air Taxi SC 21 controller_type == FIXEDTIMESIMPLE", types.get("21") == "FIXEDTIMESIMPLE")
    _check("Air Taxi SC 12 controller_type == RAILWAYCROSSING", types.get("12") == "RAILWAYCROSSING")
    _check("Air Taxi SC 13 controller_type == RAILWAYCROSSING", types.get("13") == "RAILWAYCROSSING")


# ---------------------------------------------------------------------------
# Phase 2: real preflight path (clone + GuardrailEngine, no COM)
# ---------------------------------------------------------------------------


def phase_2_real_preflight() -> None:
    _log("")
    _log("=" * 78)
    _log("[Phase 2] Real preflight path: ArtifactManager.clone() -> GuardrailEngine().preflight()")
    _log("=" * 78)

    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager

    engine = GuardrailEngine()

    def _preflight_offset(inpx_path: Path, suffix: str, sc_no: str, offset: int = 5):
        work_dir = _fresh_work_dir(suffix)
        am = ArtifactManager(source_inpx=inpx_path, work_dir=work_dir)
        shadow = am.clone()
        action = {"tool": "set_signal_offset", "params": {"sc_no": sc_no, "offset": offset}}
        report = engine.preflight(action, shadow)
        finding_ids = [f.constraint_id for f in report.findings]
        return shadow, report, finding_ids

    _, report, ids = _preflight_offset(BEIJING_INPX, "phase2-beijing-sc1", "1")
    _log(f"[2.1] Beijing SC 1 set_signal_offset preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "Beijing SC 1 (FIXEDTIME) preflight has NO signal_controller_type_unsupported finding",
        "signal_controller_type_unsupported" not in ids,
        detail=str(ids),
    )

    _, report, ids = _preflight_offset(AIR_TAXI_INPX, "phase2-airtaxi-sc11", "11")
    _log(f"[2.2] Air Taxi SC 11 set_signal_offset preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "Air Taxi SC 11 (FIXEDTIMESIMPLE) preflight has NO signal_controller_type_unsupported finding",
        "signal_controller_type_unsupported" not in ids,
        detail=str(ids),
    )

    _, report, ids = _preflight_offset(AIR_TAXI_INPX, "phase2-airtaxi-sc12", "12")
    _log(f"[2.3] Air Taxi SC 12 (RAILWAYCROSSING) set_signal_offset preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "Air Taxi SC 12 (RAILWAYCROSSING, real non-COM-writable type) preflight REFUSES via signal_controller_type_unsupported",
        "signal_controller_type_unsupported" in ids and report.blocking,
        detail=str(ids),
    )

    synthetic_shadow: dict[str, Any] = {
        "links": [],
        "nodes": [],
        "vehicle_inputs": [],
        "signals": [
            {"sc_no": "1", "sc_name": "Synthetic unknown", "cycle_time": 90, "controller_type": "unknown", "phases": []}
        ],
    }
    action = {"tool": "set_signal_offset", "params": {"sc_no": "1", "offset": 5}}
    report = engine.preflight(action, synthetic_shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.4] Synthetic unknown-type fixture set_signal_offset preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "Synthetic 'unknown'-type fixture preflight still REFUSES via signal_controller_type_unsupported",
        "signal_controller_type_unsupported" in ids and report.blocking,
        detail=str(ids),
    )


# ---------------------------------------------------------------------------
# Phase 3: ONE live COM write, full pipeline
# ---------------------------------------------------------------------------


def _read_offset_from_xml(inpx_path: Path, sc_no: str) -> str | None:
    root = ET.parse(inpx_path).getroot()
    for sc in root.findall(".//signalController"):
        if sc.get("no") == sc_no:
            return sc.get("offset")
    return None


def phase_3_live_com_write() -> bool:
    _log("")
    _log("=" * 78)
    _log("[Phase 3] ONE live COM write: set_signal_offset on a CLONE of Beijing SC 1")
    _log("           (full pipeline: preflight -> toolkit.dispatch -> postwrite)")
    _log("=" * 78)

    from services.agent.edit_session import (
        VissimEditSession,
        clear_active_edit_session,
        set_active_edit_session,
    )
    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    import services.agent.tools_edit  # noqa: F401 - registers the write tools
    from services.agent.toolkit import default_registry, dispatch

    work_dir = _fresh_work_dir("phase3-beijing-live-write")
    am = ArtifactManager(source_inpx=BEIJING_INPX, work_dir=work_dir)
    before_shadow = am.clone()
    _log(f"[3.1] Cloned Beijing -> {am.working_path} (never the original .inpx)")

    before_offset_xml = _read_offset_from_xml(am.working_path, "1")
    _log(f"      SC 1 offset attribute before (working copy XML): {before_offset_xml!r}")
    try:
        new_offset = (int(before_offset_xml or 0) + 7) % 130
    except ValueError:
        new_offset = 7

    action = {"tool": "set_signal_offset", "params": {"sc_no": "1", "offset": new_offset}}
    engine = GuardrailEngine()

    ok = False
    session = VissimEditSession(am.working_path)
    try:
        _log("[3.2] preflight (real GuardrailEngine, real cloned shadow model)...")
        pre_report = engine.preflight(action, before_shadow)
        pre_ids = [f.constraint_id for f in pre_report.findings]
        _log(f"      findings={pre_ids} blocking={pre_report.blocking}")
        _check(
            "Preflight PASSES for Beijing SC 1 offset write (no signal_controller_type_unsupported)",
            not pre_report.blocking,
            detail=str(pre_ids),
        )
        if pre_report.blocking:
            return False

        set_active_edit_session(session)
        _log(f"[3.3] Dispatching set_signal_offset(sc_no='1', offset={new_offset}) via toolkit.dispatch (real COM)...")
        envelope = dispatch(
            "set_signal_offset",
            {"sc_no": "1", "offset": new_offset},
            registry=default_registry,
        )
        _log(f"      envelope = {envelope}")
        _check("dispatch() status == ok", envelope.get("status") == "ok", detail=str(envelope))
        verification = envelope.get("verification", {})
        _check(
            "written_value == readback_value == new_offset (live COM read-back)",
            verification == {"written_value": new_offset, "readback_value": new_offset},
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

        after_offset_xml = _read_offset_from_xml(am.working_path, "1")
        _log(f"[3.5] SC 1 offset attribute after (working copy XML, post-SaveNetAs): {after_offset_xml!r}")
        _check(
            "Persisted working-copy XML offset attribute == new_offset",
            after_offset_xml == str(new_offset),
            detail=f"expected {new_offset!r}, got {after_offset_xml!r}",
        )

        ok = envelope.get("status") == "ok" and after_offset_xml == str(new_offset) and not pre_report.blocking
    finally:
        clear_active_edit_session()
        session.close()
        _log("[3.6] Vissim session closed.")

    return ok


def main() -> None:
    phase_1_static_parse()
    phase_2_real_preflight()
    phase_3_ok = phase_3_live_com_write()

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

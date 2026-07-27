"""LIVE ACCEPTANCE for v2 unit ⑦: set_vehicle_composition_share end to end on
a Beijing.CN clone -- preflight -> dispatch -> COM readback match -> persisted
evidence (XML grep) -> load_check still clean afterward.

[Phase 1] Static parse -- no COM. Parses the REAL example-network XML
(a fresh clone of Urban Intersection Beijing.CN, mirroring the other
live-check scripts' corpus) directly through
``services.agent.tools_network.build_shadow_model`` and asserts
``vehicle_compositions``/``vehicle_composition_entries`` are populated (3
real compositions on Beijing, per the v2 unit ⑦ probe), with the expected
shape and correct ``estimated_share_pct``/``referencing_vehicle_input_nos``.

[Phase 2] Real preflight path -- no COM. Clones Beijing via a real
``ArtifactManager``, builds the real action dict, and runs it through the
REAL ``GuardrailEngine().preflight()``:
  - a bogus composition_no REFUSES via object_not_found.
  - a bogus veh_type_no (within a real composition) REFUSES via
    vehicle_composition_entry_not_found.
  - a non-positive rel_flow REFUSES via vehicle_composition_weight_invalid.
  - a real (composition_no, veh_type_no) PASSES (no refusal) and carries the
    vehicle_composition_share_redistribution_gap + (when the composition has
    referencing vehicle inputs) vehicle_composition_referenced_by_vehicle_
    inputs warnings.

[Phase 3] ONE live COM write, full pipeline (preflight -> toolkit.dispatch ->
postwrite), against a CLONE of Beijing (never the original .inpx):
set_vehicle_composition_share on composition no=1's vehType=100 entry
(0.85 -> 5.85, an ARBITRARY raw-weight bump, never a percentage). Proves
preflight lets the write through, the COM write+readback succeed, EVERY
sibling entry's own relFlow is left untouched in the persisted working
copy (the central v2 unit ⑦ empirical finding -- Vissim does NOT
renormalize on write), and load_check on that same working copy still
comes back clean (load_ok=True, init_ok=True) -- the edit did not break the
network.

Always closes every Vissim instance in a ``finally`` block;
attach_to_running=False throughout (via VissimEditSession / load_check's own
connect, per 00-EXECUTION-GUIDE.md rule 3). Never touches the original
Examples Demo folder.

Usage: python tools/live_check_vehicle_composition.py
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
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
WORK_ROOT = REPO_ROOT / "runtime" / "vehicle-composition-live-check"

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


# ---------------------------------------------------------------------------
# Phase 1: static parse
# ---------------------------------------------------------------------------


def phase_1_static_parse(example_inpx: Path) -> None:
    _log("=" * 78)
    _log("[Phase 1] tools_network.build_shadow_model against the real Beijing.CN XML")
    _log("=" * 78)

    from services.agent.tools_network import build_shadow_model

    shadow = build_shadow_model(example_inpx)
    vehicle_compositions = shadow.get("vehicle_compositions")
    entries = shadow.get("vehicle_composition_entries")
    _log(f"[1.1] vehicle_compositions count = {len(vehicle_compositions) if vehicle_compositions else 0}")
    _check("vehicle_compositions is populated (not None/empty) on Beijing", bool(vehicle_compositions))
    _check(
        "vehicle_compositions count == 3 (matches the v2 unit ⑦ probe's XML grep)",
        len(vehicle_compositions) == 3,
        detail=str([c["no"] for c in vehicle_compositions]),
    )

    comp_1 = next((c for c in vehicle_compositions if c["no"] == "1"), None)
    _check("composition no=1 present", comp_1 is not None)
    if comp_1 is not None:
        _log(f"       composition 1: {comp_1}")
        _check("composition 1 has 4 entries", len(comp_1["entries"]) == 4, detail=str(comp_1["entries"]))
        _check(
            "composition 1 is referenced by vehicle inputs 1 and 2",
            comp_1["referencing_vehicle_input_nos"] == ["1", "2"],
            detail=str(comp_1["referencing_vehicle_input_nos"]),
        )
        entry_100 = next((e for e in comp_1["entries"] if e["veh_type"] == "100"), None)
        _check("composition 1 vehType=100 entry present", entry_100 is not None)
        if entry_100 is not None:
            _check(
                "composition 1 vehType=100 rel_flow == 0.85",
                entry_100["rel_flow"] == 0.85,
                detail=str(entry_100),
            )

    _log(f"[1.2] vehicle_composition_entries (flat) count = {len(entries) if entries else 0}")
    flat_ids = {e["id"] for e in entries} if entries else set()
    _check("flat entries include composition 1's vehType=100 as id '1.100'", "1.100" in flat_ids)


# ---------------------------------------------------------------------------
# Phase 2: real preflight path (clone + GuardrailEngine, no COM)
# ---------------------------------------------------------------------------


def phase_2_real_preflight(example_inpx: Path) -> dict[str, Any]:
    _log("")
    _log("=" * 78)
    _log("[Phase 2] Real preflight path: ArtifactManager.clone() -> GuardrailEngine().preflight()")
    _log("=" * 78)

    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager

    engine = GuardrailEngine()

    work_dir = _fresh_work_dir("phase2-bogus-composition")
    am = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir)
    shadow = am.clone()
    action = {
        "tool": "set_vehicle_composition_share",
        "params": {"composition_no": "999999", "veh_type_no": "100", "rel_flow": 5.0},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.1] Bogus composition_no=999999 preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "bogus composition_no REFUSES via object_not_found",
        "object_not_found" in ids and report.blocking,
        detail=str(ids),
    )

    work_dir_bad_entry = _fresh_work_dir("phase2-bogus-entry")
    am_bad_entry = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir_bad_entry)
    shadow_bad_entry = am_bad_entry.clone()
    action_bad_entry = {
        "tool": "set_vehicle_composition_share",
        "params": {"composition_no": "1", "veh_type_no": "999999", "rel_flow": 5.0},
    }
    report_bad_entry = engine.preflight(action_bad_entry, shadow_bad_entry)
    ids_bad_entry = [f.constraint_id for f in report_bad_entry.findings]
    _log(
        f"[2.2] Real composition_no=1, bogus veh_type_no=999999 preflight findings: {ids_bad_entry} "
        f"(blocking={report_bad_entry.blocking})"
    )
    _check(
        "bogus veh_type_no REFUSES via vehicle_composition_entry_not_found",
        "vehicle_composition_entry_not_found" in ids_bad_entry and report_bad_entry.blocking,
        detail=str(ids_bad_entry),
    )

    work_dir_bad_weight = _fresh_work_dir("phase2-bad-weight")
    am_bad_weight = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir_bad_weight)
    shadow_bad_weight = am_bad_weight.clone()
    action_bad_weight = {
        "tool": "set_vehicle_composition_share",
        "params": {"composition_no": "1", "veh_type_no": "100", "rel_flow": -5.0},
    }
    report_bad_weight = engine.preflight(action_bad_weight, shadow_bad_weight)
    ids_bad_weight = [f.constraint_id for f in report_bad_weight.findings]
    _log(f"[2.3] Negative rel_flow preflight findings: {ids_bad_weight} (blocking={report_bad_weight.blocking})")
    _check(
        "negative rel_flow REFUSES via vehicle_composition_weight_invalid",
        "vehicle_composition_weight_invalid" in ids_bad_weight and report_bad_weight.blocking,
        detail=str(ids_bad_weight),
    )

    work_dir2 = _fresh_work_dir("phase2-real")
    am2 = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir2)
    shadow2 = am2.clone()
    action2 = {
        "tool": "set_vehicle_composition_share",
        "params": {"composition_no": "1", "veh_type_no": "100", "rel_flow": 5.85},
    }
    report2 = engine.preflight(action2, shadow2)
    ids2 = [f.constraint_id for f in report2.findings]
    _log(f"[2.4] Real (composition_no=1, veh_type_no=100) preflight findings: {ids2} (blocking={report2.blocking})")
    _check(
        "real target PASSES (no refuse-tier finding, not blocking)",
        not report2.blocking,
        detail=str(ids2),
    )
    redistribution = next(
        (f for f in report2.findings if f.constraint_id == "vehicle_composition_share_redistribution_gap"),
        None,
    )
    _check("vehicle_composition_share_redistribution_gap warning present", redistribution is not None)
    if redistribution is not None:
        _log(f"      redistribution finding details: {redistribution.details}")
        _check(
            "redistribution finding's message uses 'raw' language, not the old disproven 'auto-lowers' premise",
            "raw" in redistribution.messages["en"].lower()
            and "auto" not in redistribution.messages["en"].lower(),
            detail=redistribution.messages["en"],
        )
    referenced = next(
        (f for f in report2.findings if f.constraint_id == "vehicle_composition_referenced_by_vehicle_inputs"),
        None,
    )
    _check(
        "vehicle_composition_referenced_by_vehicle_inputs warning present (composition 1 has real refs)",
        referenced is not None,
    )
    if referenced is not None:
        _log(f"      referenced-by finding details: {referenced.details}")

    return {"composition_no": "1", "veh_type_no": "100"}


# ---------------------------------------------------------------------------
# Phase 3: ONE live COM write, full pipeline, then load_check
# ---------------------------------------------------------------------------


def _read_composition_from_xml(inpx_path: Path, composition_no: str) -> dict[str, Any] | None:
    root = ET.parse(inpx_path).getroot()
    for comp in root.findall(".//vehicleComposition"):
        if comp.get("no") == composition_no:
            entries = [dict(rf.attrib) for rf in comp.findall(".//vehicleCompositionRelativeFlow")]
            return {"no": comp.get("no"), "name": comp.get("name"), "entries": entries}
    return None


def phase_3_live_com_write(example_inpx: Path, composition_no: str, veh_type_no: str) -> bool:
    _log("")
    _log("=" * 78)
    _log(
        f"[Phase 3] ONE live COM write: set_vehicle_composition_share(composition_no={composition_no!r}, "
        f"veh_type_no={veh_type_no!r}) on a CLONE of Beijing"
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
    _log(f"[3.1] Cloned Beijing -> {am.working_path} (never the original .inpx)")

    before_xml = _read_composition_from_xml(am.working_path, composition_no)
    _log(f"      composition {composition_no} before (working copy XML): {before_xml}")
    before_entries_by_veh_type = {e["vehType"]: e for e in (before_xml["entries"] if before_xml else [])}
    old_rel_flow = before_entries_by_veh_type.get(veh_type_no, {}).get("relFlow")
    new_rel_flow = (float(old_rel_flow) if old_rel_flow is not None else 0.0) + 5.0

    action = {
        "tool": "set_vehicle_composition_share",
        "params": {"composition_no": composition_no, "veh_type_no": veh_type_no, "rel_flow": new_rel_flow},
    }
    engine = GuardrailEngine()

    write_ok = False
    session = VissimEditSession(am.working_path)
    try:
        _log("[3.2] preflight (real GuardrailEngine, real cloned shadow model)...")
        pre_report = engine.preflight(action, before_shadow)
        pre_ids = [f.constraint_id for f in pre_report.findings]
        _log(f"      findings={pre_ids} blocking={pre_report.blocking}")
        _check("Preflight PASSES for the real vehicle composition write", not pre_report.blocking, detail=str(pre_ids))
        if pre_report.blocking:
            return False

        set_active_edit_session(session)
        _log(
            f"[3.3] Dispatching set_vehicle_composition_share(composition_no={composition_no!r}, "
            f"veh_type_no={veh_type_no!r}, rel_flow={new_rel_flow!r}) via toolkit.dispatch (real COM)..."
        )
        envelope = dispatch(
            "set_vehicle_composition_share",
            {"composition_no": composition_no, "veh_type_no": veh_type_no, "rel_flow": new_rel_flow},
            registry=default_registry,
        )
        _log(f"      envelope = {envelope}")
        _check("dispatch() status == ok", envelope.get("status") == "ok", detail=str(envelope))
        verification = envelope.get("verification", {})
        _check(
            "written_value == readback_value == new_rel_flow (live COM read-back)",
            verification == {"written_value": new_rel_flow, "readback_value": new_rel_flow},
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

        after_xml = _read_composition_from_xml(am.working_path, composition_no)
        _log(f"[3.5] composition {composition_no} after (working copy XML, post-SaveNetAs): {after_xml}")
        after_entries_by_veh_type = {e["vehType"]: e for e in (after_xml["entries"] if after_xml else [])}
        target_after = after_entries_by_veh_type.get(veh_type_no, {})
        _check(
            "Persisted working-copy XML relFlow attribute == new_rel_flow",
            target_after.get("relFlow") is not None and abs(float(target_after["relFlow"]) - new_rel_flow) < 1e-6,
            detail=f"expected {new_rel_flow!r}, got {target_after}",
        )

        _log("[3.6] THE CENTRAL FINDING, re-verified end to end: every SIBLING entry's own relFlow is UNCHANGED...")
        siblings_changed = {}
        for veh_type, before_entry in before_entries_by_veh_type.items():
            if veh_type == veh_type_no:
                continue
            after_entry = after_entries_by_veh_type.get(veh_type, {})
            if before_entry.get("relFlow") != after_entry.get("relFlow"):
                siblings_changed[veh_type] = (before_entry.get("relFlow"), after_entry.get("relFlow"))
        _check(
            "no sibling entry's relFlow changed (Vissim does NOT renormalize on write)",
            not siblings_changed,
            detail=str(siblings_changed),
        )

        write_ok = (
            envelope.get("status") == "ok"
            and target_after.get("relFlow") is not None
            and abs(float(target_after["relFlow"]) - new_rel_flow) < 1e-6
            and not pre_report.blocking
            and not siblings_changed
        )
    finally:
        clear_active_edit_session()
        session.close()
        _log("[3.7] Vissim edit session closed.")

    if not write_ok:
        return False

    _log("[3.8] load_check on the SAME working copy -- must still be clean after the vehicle-composition edit...")
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
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")
    example_inpx = _find_inpx(example_dir)

    phase_1_static_parse(example_inpx)
    phase2_result = phase_2_real_preflight(example_inpx)
    phase_3_ok = phase_3_live_com_write(
        example_inpx, phase2_result["composition_no"], phase2_result["veh_type_no"]
    )

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

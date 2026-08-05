"""LIVE ACCEPTANCE for QUEUE 3 unit 6: the FULL driving-behavior
duplicate-then-edit flow end to end on a "Roundabout Billings.US" clone --
preflight (refuse-when-shared, live against a REAL shared behavior) ->
duplicate (real COM DuplicateDrivingBehavior, full copy-fidelity diff) ->
edit the copy (real COM write + readback) -> assign the copy to a real
link-behavior-type (real COM write + readback) -> persisted evidence (XML
grep) -> fresh COM reload -> all three survive -> load_check still clean.

Mirrors live_check_time_distributions.py's own three-phase shape exactly.
"Roundabout Billings.US" is this unit's own live target throughout (probe +
follow-up probes) -- it carries 6 real driving behaviors, 2 real
link-behavior-types, and ONE real per-vehicle-class override (lbt=1,
vehClass=20 -> drivBehav=6), letting this check exercise BOTH assignment
paths (default AND override) against real data.

Always closes every Vissim instance in a ``finally`` block;
attach_to_running=False throughout. Never touches the original Examples
Demo folder.

Usage: python tools/driving_behavior_live_check.py
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
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Roundabout Billings.US"
)
WORK_ROOT = REPO_ROOT / "runtime" / "driving-behavior-live-check"

_FAILURES: list[str] = []
_WHITELISTED_PARAMS = [
    "ax", "bx_add", "bx_mult", "saf_dist_fact_ln_chg", "coop_ln_chg_speed_diff",
    "min_speed_for_lat", "lat_dist_driv_def", "lat_dist_stand_def",
]


def _close(a: Any, b: Any, tol: float = 1e-6) -> bool:
    """Float-tolerant equality -- the meters<->feet round trip (division
    then multiplication by 0.3048) introduces ~1e-15 noise on EVERY
    length-type value (e.g. 3.5 -> 3.5/0.3048 -> *0.3048 = 3.499999999999999),
    live-verified below. This is the exact same class of noise
    guardrails.py's own _values_differ (1e-9 tolerance) already absorbs in
    production -- a bare `==` in this probe script is stricter than the
    real validation layer and would flag noise as a false failure."""
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return a == b


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


def _read_driving_behavior_from_xml(inpx_path: Path, no: str) -> dict[str, Any] | None:
    root = ET.parse(inpx_path).getroot()
    for db in root.findall(".//drivingBehaviors/drivingBehavior"):
        if db.get("no") == no:
            return dict(db.attrib)
    return None


def _read_lbt_from_xml(inpx_path: Path, no: str) -> dict[str, Any] | None:
    root = ET.parse(inpx_path).getroot()
    for lbt in root.findall(".//linkBehaviorTypes/linkBehaviorType"):
        if lbt.get("no") == no:
            overrides = [dict(c.attrib) for c in lbt.findall(".//vehClassDrivingBehavior")]
            return {**dict(lbt.attrib), "overrides": overrides}
    return None


# ---------------------------------------------------------------------------
# Phase 1: static parse + refuse-when-shared against REAL shared data
# ---------------------------------------------------------------------------


def phase_1_static_and_refuse_when_shared(example_inpx: Path) -> None:
    _log("=" * 78)
    _log("[Phase 1] build_shadow_model + refuse-when-shared preflight against REAL Roundabout Billings.US data")
    _log("=" * 78)

    from services.agent.guardrails import GuardrailEngine
    from services.agent.tools_network import build_shadow_model

    shadow = build_shadow_model(example_inpx)
    behaviors = shadow.get("driving_behaviors") or []
    lbts = shadow.get("link_behavior_types") or []
    _log(f"[1.1] driving_behaviors count = {len(behaviors)} (expect 6)")
    _check("driving_behaviors count == 6", len(behaviors) == 6)
    _log(f"[1.2] link_behavior_types count = {len(lbts)} (expect 2)")
    _check("link_behavior_types count == 2", len(lbts) == 2)

    behavior_1 = next((b for b in behaviors if b["no"] == "1"), None)
    _check("behavior no=1 present", behavior_1 is not None)
    if behavior_1 is not None:
        _log(f"       behavior 1: reference_count={behavior_1['reference_count']} referenced_by={behavior_1['referenced_by']}")
        _check(
            "behavior 1 is a REAL shared object (reference_count >= 1, per this unit's own live probe)",
            behavior_1["reference_count"] >= 1,
            detail=str(behavior_1["reference_count"]),
        )

    engine = GuardrailEngine()
    action = {"tool": "set_driving_behavior_parameter", "params": {"behavior_no": "1", "parameter": "ax", "value": 3.0}}
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[1.3] preflight on shared behavior no=1 (edit in place): findings={ids} blocking={report.blocking}")
    _check(
        "editing the REAL shared behavior no=1 in place REFUSES via driving_behavior_shared_object_edit",
        "driving_behavior_shared_object_edit" in ids and report.blocking,
        detail=str(ids),
    )
    refusal = next((f for f in report.findings if f.constraint_id == "driving_behavior_shared_object_edit"), None)
    if refusal is not None:
        _log(f"       refusal message (en): {refusal.messages['en']}")
        _check("refusal names the real consuming link-behavior-type", "Urban" in refusal.messages["en"], detail=refusal.messages["en"])

    footpath = next((b for b in behaviors if b["car_follow_mod_type"] == "NOINTERACTION"), None)
    _check("a real NOINTERACTION (Footpath) behavior exists", footpath is not None)
    if footpath is not None:
        action2 = {
            "tool": "set_driving_behavior_parameter",
            "params": {"behavior_no": footpath["no"], "parameter": "ax", "value": 2.0},
        }
        report2 = engine.preflight(action2, shadow)
        ids2 = [f.constraint_id for f in report2.findings]
        _log(f"[1.4] preflight: W74 'ax' param on a REAL NOINTERACTION behavior: findings={ids2} blocking={report2.blocking}")
        _check(
            "W74 param on a real NOINTERACTION behavior REFUSES via driving_behavior_parameter_model_mismatch",
            "driving_behavior_parameter_model_mismatch" in ids2 and report2.blocking,
            detail=str(ids2),
        )


# ---------------------------------------------------------------------------
# Phase 2: the FULL live COM flow -- duplicate -> edit -> assign
# ---------------------------------------------------------------------------


def phase_2_live_duplicate_edit_assign(example_inpx: Path) -> bool:
    _log("")
    _log("=" * 78)
    _log("[Phase 2] FULL live flow: duplicate_driving_behavior -> set_driving_behavior_parameter -> set_link_behavior_type_driving_behavior")
    _log("           on a CLONE of Roundabout Billings.US (real COM throughout)")
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

    work_dir = _fresh_work_dir("phase2-live-flow")
    am = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir)
    shadow_0 = am.clone()
    _log(f"[2.1] Cloned Roundabout Billings.US -> {am.working_path} (never the original .inpx)")

    before_source_xml = _read_driving_behavior_from_xml(am.working_path, "1")
    _log(f"      source behavior no=1 BEFORE (XML): {before_source_xml}")

    write_ok = False
    session = VissimEditSession(am.working_path)
    engine = GuardrailEngine()
    try:
        set_active_edit_session(session)

        # --- Step 1: duplicate ---------------------------------------
        dup_params = {"source_no": "1", "new_name": "Test Ramp Calibration"}
        dup_action = {"tool": "duplicate_driving_behavior", "params": dup_params}
        pre_dup = engine.preflight(dup_action, shadow_0)
        _log(f"[2.2] preflight(duplicate_driving_behavior source_no=1) findings={[f.constraint_id for f in pre_dup.findings]} blocking={pre_dup.blocking}")
        _check("preflight PASSES for duplicate (source exists)", not pre_dup.blocking)

        _log("[2.3] Dispatching duplicate_driving_behavior via toolkit.dispatch (real COM DuplicateDrivingBehavior)...")
        dup_envelope = dispatch("duplicate_driving_behavior", dup_params, registry=default_registry)
        _log(f"      envelope = {dup_envelope}")
        _check("duplicate_driving_behavior status == ok", dup_envelope.get("status") == "ok", detail=str(dup_envelope))
        new_no = (dup_envelope.get("artifacts") or {}).get("new_no")
        _check("a new behavior number was assigned", bool(new_no), detail=str(dup_envelope))

        copy_dict = (dup_envelope.get("artifacts") or {}).get("driving_behavior") or {}
        source_dict_live = None
        if new_no:
            # COPY-FIDELITY DIFF: re-read the SOURCE live (fresh get, not
            # cached) and compare EVERY whitelisted field plus name/model --
            # proves DuplicateDrivingBehavior's exact-copy claim through the
            # REAL production tool path, not just the standalone probe.
            source_dict_live = session.get_driving_behavior(1)
            mismatches = []
            for field in ["name", "car_follow_mod_type", *_WHITELISTED_PARAMS]:
                if field == "name":
                    continue  # name is intentionally renamed by this tool
                if source_dict_live.get(field) != copy_dict.get(field):
                    mismatches.append((field, source_dict_live.get(field), copy_dict.get(field)))
            _log(f"      copy-fidelity diff (source no=1 vs copy no={new_no}): mismatches={mismatches}")
            _check("copy-fidelity: every field EXCEPT no/name matches the source exactly", mismatches == [], detail=str(mismatches))
            _check("copy name is the NEW name, not the source's", copy_dict.get("name") == "Test Ramp Calibration", detail=str(copy_dict))

        shadow_1 = build_shadow_model(am.working_path)
        post_dup = engine.postwrite(
            dup_action, shadow_0, shadow_1,
            {"written_value": dup_params["new_name"], "readback_value": dup_envelope.get("verification", {}).get("readback_value")},
        )
        _log(f"[2.4] postwrite(duplicate) findings={[f.constraint_id for f in post_dup.findings]} blocking={post_dup.blocking}")
        _check(
            "postwrite(duplicate) has no write_verification_failed (the new object is expected, not evidence)",
            "write_verification_failed" not in [f.constraint_id for f in post_dup.findings],
            detail=str([f.constraint_id for f in post_dup.findings]),
        )
        new_behavior = next((b for b in (shadow_1.get("driving_behaviors") or []) if b["no"] == str(new_no)), None)
        _check("new behavior appears in the shadow model with reference_count == 0", new_behavior is not None and new_behavior["reference_count"] == 0, detail=str(new_behavior))

        if not new_no:
            return False

        # --- Step 2: edit the copy (allowed: zero references) --------
        edit_params = {"behavior_no": str(new_no), "parameter": "ax", "value": 3.5}
        edit_action = {"tool": "set_driving_behavior_parameter", "params": edit_params}
        pre_edit = engine.preflight(edit_action, shadow_1)
        _log(f"[2.5] preflight(set_driving_behavior_parameter on the COPY, zero refs) findings={[f.constraint_id for f in pre_edit.findings]} blocking={pre_edit.blocking}")
        _check("preflight ALLOWS editing the zero-reference copy", not pre_edit.blocking, detail=str([f.constraint_id for f in pre_edit.findings]))

        _log(f"[2.6] Dispatching set_driving_behavior_parameter(behavior_no={new_no}, ax=3.5) via toolkit.dispatch (real COM)...")
        edit_envelope = dispatch("set_driving_behavior_parameter", edit_params, registry=default_registry)
        _log(f"      envelope = {edit_envelope}")
        _check("set_driving_behavior_parameter status == ok", edit_envelope.get("status") == "ok", detail=str(edit_envelope))
        edit_verification = edit_envelope.get("verification", {})
        _check(
            "ax written_value == readback_value ~= 3.5 (float-tolerant -- see _close's own docstring for the live-verified m<->ft round-trip noise)",
            _close(edit_verification.get("written_value"), 3.5) and _close(edit_verification.get("readback_value"), 3.5),
            detail=str(edit_verification),
        )

        shadow_2 = build_shadow_model(am.working_path)
        post_edit = engine.postwrite(edit_action, shadow_1, shadow_2, edit_verification)
        _log(f"[2.7] postwrite(edit copy) findings={[f.constraint_id for f in post_edit.findings]} blocking={post_edit.blocking}")
        _check(
            "postwrite(edit copy) has no write_verification_failed",
            "write_verification_failed" not in [f.constraint_id for f in post_edit.findings],
            detail=str([f.constraint_id for f in post_edit.findings]),
        )

        # Sanity: the ORIGINAL source (no=1) must be COMPLETELY untouched by
        # editing the copy -- the whole point of duplicate-then-edit.
        source_after_edit_xml = _read_driving_behavior_from_xml(am.working_path, "1")
        _check(
            "the SHARED SOURCE (no=1) is byte-identical before/after editing the copy",
            source_after_edit_xml == before_source_xml,
            detail=f"before={before_source_xml} after={source_after_edit_xml}",
        )

        # --- Step 3: assign the copy to a link-behavior-type ----------
        # Real per-vehicle-class override path: lbt=1, vehClass=20 currently
        # points at behavior 6 (real data) -- reassign it to the new copy.
        assign_params = {"link_behavior_type_no": "1", "driving_behavior_no": str(new_no), "veh_class_no": "20"}
        assign_action = {"tool": "set_link_behavior_type_driving_behavior", "params": assign_params}
        pre_assign = engine.preflight(assign_action, shadow_2)
        _log(f"[2.8] preflight(set_link_behavior_type_driving_behavior, real override lbt=1/vehClass=20) findings={[f.constraint_id for f in pre_assign.findings]} blocking={pre_assign.blocking}")
        _check("preflight ALLOWS the real per-vehicle-class override reassignment", not pre_assign.blocking, detail=str([f.constraint_id for f in pre_assign.findings]))

        _log(f"[2.9] Dispatching set_link_behavior_type_driving_behavior(lbt=1, veh_class=20 -> {new_no}) via toolkit.dispatch (real COM)...")
        assign_envelope = dispatch("set_link_behavior_type_driving_behavior", assign_params, registry=default_registry)
        _log(f"      envelope = {assign_envelope}")
        _check("set_link_behavior_type_driving_behavior status == ok", assign_envelope.get("status") == "ok", detail=str(assign_envelope))
        assign_verification = assign_envelope.get("verification", {})
        _check(
            "assignment written_value == readback_value == new_no",
            assign_verification.get("written_value") == str(new_no) and assign_verification.get("readback_value") == str(new_no),
            detail=str(assign_verification),
        )

        shadow_3 = build_shadow_model(am.working_path)
        post_assign = engine.postwrite(assign_action, shadow_2, shadow_3, assign_verification)
        _log(f"[2.10] postwrite(assign) findings={[f.constraint_id for f in post_assign.findings]} blocking={post_assign.blocking}")
        _check(
            "postwrite(assign) has no write_verification_failed",
            "write_verification_failed" not in [f.constraint_id for f in post_assign.findings],
            detail=str([f.constraint_id for f in post_assign.findings]),
        )
        new_behavior_after_assign = next((b for b in (shadow_3.get("driving_behaviors") or []) if b["no"] == str(new_no)), None)
        _check(
            "the copy now shows reference_count == 1 (assignment landed)",
            new_behavior_after_assign is not None and new_behavior_after_assign["reference_count"] == 1,
            detail=str(new_behavior_after_assign),
        )

        after_lbt_xml = _read_lbt_from_xml(am.working_path, "1")
        _log(f"[2.11] Persisted XML link-behavior-type no=1 AFTER: {after_lbt_xml}")
        override_after = next((o for o in (after_lbt_xml or {}).get("overrides", []) if o.get("vehClass") == "20"), None)
        _check(
            "persisted XML shows vehClass=20 override now points at the new behavior",
            override_after is not None and override_after.get("drivBehav") == str(new_no),
            detail=str(after_lbt_xml),
        )

        after_copy_xml = _read_driving_behavior_from_xml(am.working_path, str(new_no))
        _check(
            "persisted XML shows the copy's w74ax ~= 3.5 (edit survived the SaveNetAs from step 3 too)",
            after_copy_xml is not None and _close(after_copy_xml.get("w74ax"), 3.5),
            detail=str(after_copy_xml),
        )

        write_ok = (
            dup_envelope.get("status") == "ok"
            and edit_envelope.get("status") == "ok"
            and assign_envelope.get("status") == "ok"
            and not pre_dup.blocking and not pre_edit.blocking and not pre_assign.blocking
            and "write_verification_failed" not in [f.constraint_id for f in post_dup.findings]
            and "write_verification_failed" not in [f.constraint_id for f in post_edit.findings]
            and "write_verification_failed" not in [f.constraint_id for f in post_assign.findings]
        )
    finally:
        clear_active_edit_session()
        session.close()
        _log("[2.12] Vissim edit session closed.")

    if not write_ok or not new_no:
        return False

    _log("[2.13] Fresh COM reload of the SAVED file (separate connection) -- confirms ALL THREE writes survived a full round trip...")
    verify_controller = None
    try:
        import pythoncom

        pythoncom.CoInitialize()
        from services.vissim_com import VissimController

        verify_controller = VissimController()
        verify_controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        verify_controller.load_network(str(am.working_path))
        reload_copy = verify_controller.get_driving_behavior(int(new_no))
        reload_source = verify_controller.get_driving_behavior(1)
        reload_assignment = verify_controller.get_link_behavior_type_driving_behavior(1, veh_class_no=20)
        _log(f"      reload copy no={new_no}: {reload_copy}")
        _log(f"      reload source no=1: {reload_source}")
        _log(f"      reload assignment lbt=1/vehClass=20: {reload_assignment}")
        _check("fresh reload: copy name == 'Test Ramp Calibration'", reload_copy["name"] == "Test Ramp Calibration", detail=str(reload_copy))
        _check("fresh reload: copy ax ~= 3.5", _close(reload_copy["ax"], 3.5), detail=str(reload_copy))
        _check(
            "fresh reload: source no=1 ax UNCHANGED (still the original)",
            _close(reload_source["ax"], source_dict_live["ax"]) if source_dict_live else True,
            detail=str(reload_source),
        )
        _check("fresh reload: lbt=1/vehClass=20 assignment == new copy", reload_assignment["driving_behavior_no"] == str(new_no), detail=str(reload_assignment))
    finally:
        if verify_controller is not None:
            try:
                verify_controller.disconnect()
            except Exception:
                pass
        try:
            import pythoncom

            pythoncom.CoUninitialize()
        except Exception:
            pass

    _log("[2.14] load_check on the SAME working copy -- must still be clean after all three writes...")
    set_active_run_source(am.working_path)
    try:
        check_envelope = dispatch("load_check", {}, registry=default_registry)
    finally:
        clear_active_run_source()
    _log(f"      load_check envelope = {check_envelope}")
    _check("load_check dispatch status == ok", check_envelope.get("status") == "ok", detail=str(check_envelope))
    check_verification = check_envelope.get("verification", {})
    _check(
        "load_check reports load_ok=True, init_ok=True after all three writes",
        check_verification == {"load_ok": True, "init_ok": True},
        detail=str(check_verification),
    )

    return write_ok and check_verification == {"load_ok": True, "init_ok": True}


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")
    example_inpx = _find_inpx(example_dir)

    phase_1_static_and_refuse_when_shared(example_inpx)
    phase_2_ok = phase_2_live_duplicate_edit_assign(example_inpx)

    _log("")
    _log("=" * 78)
    if _FAILURES:
        _log(f"RESULT: FAIL -- {len(_FAILURES)} check(s) failed: {_FAILURES}")
    elif not phase_2_ok:
        _log("RESULT: FAIL -- Phase 2 live COM flow did not complete cleanly.")
    else:
        _log("RESULT: PASS -- all phases green.")
    _log("=" * 78)
    if _FAILURES or not phase_2_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()

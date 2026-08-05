"""LIVE ACCEPTANCE for the QUEUE (v2a) unit ("vehicle type properties,
SCALARS/REFERENCES only"): set_vehicle_type_reference end to end on a
Roundabout Billings.US clone -- preflight -> dispatch -> COM readback match ->
persisted evidence (XML grep) -> FRESH COM reload -> load_check still clean.

[Phase 1] Static parse -- no COM. Parses the REAL example-network XML (a
fresh clone of Roundabout Billings.US) directly through
``services.agent.tools_network.build_shadow_model`` and asserts
``vehicle_types`` + its 8 target-collection shadow keys are populated (6
real vehicle types, per the ``backend/tools/probe_vehicle_types.py`` probe),
with the expected shape (including the live-verified nullable-reference
finding on the PEDESTRIAN types).

[Phase 2] Real preflight path -- no COM. Clones Roundabout Billings.US via a
real ``ArtifactManager``, builds real action dicts, and runs them through the
REAL ``GuardrailEngine().preflight()``:
  - a bogus vehicle_type_no REFUSES via object_not_found.
  - a bogus target_no (for a real vehicle type) REFUSES via
    vehicle_type_reference_target_not_found.
  - a real target PASSES.

[Phase 3] ONE live COM write, full pipeline (preflight -> toolkit.dispatch ->
postwrite), against a CLONE of Roundabout Billings.US (never the original
.inpx): set_vehicle_type_reference on vehicle type no=200 (HGV)'s
weight_distr (2 -> 3), sibling vehicle type no=100 (Car)'s own weight_distr
left untouched, persisted XML reflects the write, a FRESH COM reload confirms
the round trip, and load_check on that same working copy still comes back
clean (load_ok=True, init_ok=True).

Always closes every Vissim instance in a ``finally`` block;
attach_to_running=False throughout (via VissimEditSession / load_check's own
connect, per 00-EXECUTION-GUIDE.md rule 3). Never touches the original
Examples Demo folder.

Usage: python tools/live_check_vehicle_type_reference.py
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
WORK_ROOT = REPO_ROOT / "runtime" / "vehicle-type-reference-live-check"

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
    _log("[Phase 1] tools_network.build_shadow_model against the real Roundabout Billings.US XML")
    _log("=" * 78)

    from services.agent.tools_network import build_shadow_model

    shadow = build_shadow_model(example_inpx)
    vehicle_types = shadow.get("vehicle_types")
    _log(f"[1.1] vehicle_types count = {len(vehicle_types) if vehicle_types else 0}")
    _check(
        "vehicle_types count == 6 (matches the probe's XML grep)",
        len(vehicle_types) == 6,
        detail=str([vt["no"] for vt in vehicle_types]),
    )

    hgv = next((vt for vt in vehicle_types if vt["no"] == "200"), None)
    _check("vehicle type no=200 (HGV) present", hgv is not None)
    if hgv is not None:
        _log(f"       vehicle type 200: {hgv}")
        _check("HGV weight_distr_no == '2'", hgv["weight_distr_no"] == "2", detail=str(hgv["weight_distr_no"]))
        _check(
            "HGV references.weight_distr resolves with real min/max",
            hgv["references"]["weight_distr"] is not None
            and hgv["references"]["weight_distr"]["min"] is not None,
            detail=str(hgv["references"]["weight_distr"]),
        )

    man = next((vt for vt in vehicle_types if vt["no"] == "510"), None)
    _check("vehicle type no=510 (Man, PEDESTRIAN) present", man is not None)
    if man is not None:
        _log(f"       vehicle type 510: {man}")
        _check(
            "PEDESTRIAN type's weight_distr_no is None (live-verified nullable reference)",
            man["weight_distr_no"] is None,
            detail=str(man["weight_distr_no"]),
        )
        _check("PEDESTRIAN type's references.weight_distr is None", man["references"]["weight_distr"] is None)

    for key in (
        "model2d3d_distributions",
        "weight_distributions",
        "power_distributions",
        "des_accel_functions",
        "max_accel_functions",
        "des_decel_functions",
        "max_decel_functions",
        "curve_speed_functions",
    ):
        values = shadow.get(key)
        _log(f"[1.2] {key} count = {len(values) if values else 0}")
        _check(f"{key} is populated (not None/empty)", bool(values), detail=str(values))


# ---------------------------------------------------------------------------
# Phase 2: real preflight path (clone + GuardrailEngine, no COM)
# ---------------------------------------------------------------------------


def phase_2_real_preflight(example_inpx: Path) -> dict[str, str]:
    _log("")
    _log("=" * 78)
    _log("[Phase 2] Real preflight path: ArtifactManager.clone() -> GuardrailEngine().preflight()")
    _log("=" * 78)

    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager

    engine = GuardrailEngine()

    def _fresh_shadow(suffix: str):
        work_dir = _fresh_work_dir(suffix)
        am = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir)
        return am.clone()

    shadow = _fresh_shadow("phase2-bogus-vehicle-type")
    action = {
        "tool": "set_vehicle_type_reference",
        "params": {"vehicle_type_no": "999999", "field": "weight_distr", "target_no": "3"},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.1] Bogus vehicle_type_no=999999 preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "bogus vehicle_type_no REFUSES via object_not_found",
        "object_not_found" in ids and report.blocking,
        detail=str(ids),
    )

    shadow = _fresh_shadow("phase2-bogus-target")
    action = {
        "tool": "set_vehicle_type_reference",
        "params": {"vehicle_type_no": "200", "field": "weight_distr", "target_no": "999999"},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.2] Real vehicle_type_no=200, bogus target_no=999999 preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "bogus target_no REFUSES via vehicle_type_reference_target_not_found",
        "vehicle_type_reference_target_not_found" in ids and report.blocking,
        detail=str(ids),
    )

    shadow = _fresh_shadow("phase2-real-target")
    action = {
        "tool": "set_vehicle_type_reference",
        "params": {"vehicle_type_no": "200", "field": "weight_distr", "target_no": "3"},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.3] Real target preflight findings: {ids} (blocking={report.blocking})")
    _check("real target PASSES (not blocking)", not report.blocking, detail=str(ids))

    return {"vehicle_type_no": "200", "field": "weight_distr", "target_no": "3"}


# ---------------------------------------------------------------------------
# Phase 3: ONE live COM write, full pipeline, then a fresh reload + load_check
# ---------------------------------------------------------------------------


def _read_vehicle_type_from_xml(inpx_path: Path, vehicle_type_no: str) -> dict[str, Any] | None:
    root = ET.parse(inpx_path).getroot()
    for vt in root.findall(".//vehicleTypes/vehicleType"):
        if vt.get("no") == vehicle_type_no:
            return dict(vt.attrib)
    return None


def phase_3_live_com_write(example_inpx: Path, params: dict[str, str]) -> bool:
    _log("")
    _log("=" * 78)
    _log(
        "[Phase 3] ONE live COM write: set_vehicle_type_reference("
        f"vehicle_type_no={params['vehicle_type_no']!r}, field={params['field']!r}, "
        f"target_no={params['target_no']!r}) on a CLONE of Roundabout Billings.US"
    )
    _log("           (full pipeline: preflight -> toolkit.dispatch -> postwrite -> persisted XML -> fresh reload -> load_check)")
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
    from services.vissim_com import VissimController

    work_dir = _fresh_work_dir("phase3-live-write")
    am = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir)
    before_shadow = am.clone()
    _log(f"[3.1] Cloned Roundabout Billings.US -> {am.working_path} (never the original .inpx)")

    before_target = _read_vehicle_type_from_xml(am.working_path, params["vehicle_type_no"])
    before_sibling = _read_vehicle_type_from_xml(am.working_path, "100")  # Car -- must stay untouched
    _log(f"      vehicle type {params['vehicle_type_no']} before (working copy XML): weightDistr={before_target.get('weightDistr') if before_target else None}")
    _log(f"      vehicle type 100 (Car, sibling) before: weightDistr={before_sibling.get('weightDistr') if before_sibling else None}")

    action = {"tool": "set_vehicle_type_reference", "params": dict(params)}
    engine = GuardrailEngine()

    write_ok = False
    session = VissimEditSession(am.working_path)
    try:
        _log("[3.2] preflight (real GuardrailEngine, real cloned shadow model)...")
        pre_report = engine.preflight(action, before_shadow)
        pre_ids = [f.constraint_id for f in pre_report.findings]
        _log(f"      findings={pre_ids} blocking={pre_report.blocking}")
        _check("Preflight PASSES for the real vehicle-type-reference write", not pre_report.blocking, detail=str(pre_ids))
        if pre_report.blocking:
            return False

        set_active_edit_session(session)
        _log("[3.3] Dispatching set_vehicle_type_reference via toolkit.dispatch (real COM)...")
        envelope = dispatch("set_vehicle_type_reference", dict(params), registry=default_registry)
        _log(f"      envelope = {envelope}")
        _check("dispatch() status == ok", envelope.get("status") == "ok", detail=str(envelope))
        verification = envelope.get("verification", {})
        _check(
            "written_value == readback_value == '3'",
            verification.get("written_value") == "3" and verification.get("readback_value") == "3",
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

        after_target = _read_vehicle_type_from_xml(am.working_path, params["vehicle_type_no"])
        after_sibling = _read_vehicle_type_from_xml(am.working_path, "100")
        _log(f"[3.5] vehicle type {params['vehicle_type_no']} after (working copy XML, post-SaveNetAs): weightDistr={after_target.get('weightDistr') if after_target else None}")
        _log(f"      vehicle type 100 (Car, sibling) after: weightDistr={after_sibling.get('weightDistr') if after_sibling else None}")

        _check(
            "Persisted TARGET vehicle type's weightDistr == '3'",
            after_target is not None and after_target.get("weightDistr") == "3",
            detail=str(after_target),
        )
        _check(
            "Persisted SIBLING vehicle type 100 (Car)'s weightDistr UNCHANGED",
            after_sibling is not None and after_sibling.get("weightDistr") == before_sibling.get("weightDistr"),
            detail=str((after_sibling, before_sibling)),
        )

        write_ok = (
            envelope.get("status") == "ok"
            and after_target is not None
            and after_target.get("weightDistr") == "3"
            and after_sibling is not None
            and after_sibling.get("weightDistr") == before_sibling.get("weightDistr")
            and not pre_report.blocking
            and "write_verification_failed" not in post_ids
        )
    finally:
        clear_active_edit_session()
        session.close()
        _log("[3.6] Vissim edit session closed.")

    if not write_ok:
        return False

    _log("[3.7] FRESH COM reload of the persisted working copy (separate connection)...")
    # VissimEditSession.close() (in the finally block above) already
    # CoUninitialize()'d this thread's COM apartment -- a bare
    # VissimController() here needs its own fresh CoInitialize(), the SAME
    # pattern backend/tools/probe_vehicle_types.py's own reload phase uses.
    import pythoncom

    pythoncom.CoInitialize()
    reload_controller = VissimController()
    try:
        reload_controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        reload_controller.load_network(str(am.working_path))
        reload_value = reload_controller.get_vehicle_type_reference(int(params["vehicle_type_no"]), params["field"])
        _log(f"      reload {params['field']} on vehicle type {params['vehicle_type_no']} = {reload_value!r} (intended {params['target_no']!r})")
        _check(
            "Fresh reload's reference value matches the intended write",
            str(reload_value) == params["target_no"],
            detail=str(reload_value),
        )
    finally:
        reload_controller.disconnect()
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    _log("[3.8] load_check on the SAME working copy -- must still be clean after the edit...")
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
    phase_3_ok = phase_3_live_com_write(example_inpx, phase2_result)

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
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()

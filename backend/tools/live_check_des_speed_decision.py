"""LIVE ACCEPTANCE for v2a unit ⑧: set_des_speed_decision's ATOMIC PAIRED
write end to end on a Roundabout Billings.US clone -- preflight -> dispatch
-> COM readback match for BOTH halves -> persisted evidence (XML grep, both
decisions) -> load_check still clean afterward.

[Phase 1] Static parse -- no COM. Parses the REAL example-network XML
(a fresh clone of Roundabout Billings.US -- Beijing.CN, every other v2-unit
live check's default, has ZERO desSpeedDecision elements) directly through
``services.agent.tools_network.build_shadow_model`` and asserts
``des_speed_decisions``/``des_speed_decision_entries``/
``des_speed_distributions`` are populated (5 real decisions, per the v2a
unit ⑧ probe), with the expected shape.

[Phase 2] Real preflight path -- no COM. Clones Roundabout Billings.US via a
real ``ArtifactManager``, builds real action dicts, and runs them through the
REAL ``GuardrailEngine().preflight()``:
  - a bogus decision_no REFUSES via object_not_found.
  - a bogus veh_class_no (within a real decision) REFUSES via
    des_speed_decision_entry_not_found.
  - a bogus des_speed_distr_no REFUSES via des_speed_distribution_not_found.
  - a bogus restore_decision_no REFUSES via des_speed_decision_entry_not_found
    (the ONLY refusal path for it -- object_not_found's _TOOL_TARGET_SPEC
    only ever names the "decision_no" param, never "restore_decision_no").
  - a real UNPAIRED target PASSES and carries the speed_decision_unpaired
    warning.
  - a real PAIRED target PASSES and does NOT carry speed_decision_unpaired.

[Phase 3] ONE live COM PAIRED write, full pipeline (preflight -> toolkit.
dispatch -> postwrite), against a CLONE of Roundabout Billings.US (never the
original .inpx): set_des_speed_decision on decision no=6's vehClass=10 entry
(50 -> 12, "10 mph Roundabout HGV") PLUS, in the SAME action, restore
decision no=7's vehClass=10 entry (50 -> 40, "45 mph") via restore_decision_no/
restore_distr_no. Proves preflight lets the paired write through with no
speed_decision_unpaired warning, BOTH COM writes+readbacks succeed, BOTH
sibling vehClass=20 entries (on decisions 6 AND 7) are left untouched in the
persisted working copy, the persisted XML reflects BOTH writes, and
load_check on that same working copy still comes back clean (load_ok=True,
init_ok=True) -- the paired edit did not break the network.

Always closes every Vissim instance in a ``finally`` block;
attach_to_running=False throughout (via VissimEditSession / load_check's own
connect, per 00-EXECUTION-GUIDE.md rule 3). Never touches the original
Examples Demo folder.

Usage: python tools/live_check_des_speed_decision.py
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
WORK_ROOT = REPO_ROOT / "runtime" / "des-speed-decision-live-check"

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
    decisions = shadow.get("des_speed_decisions")
    entries = shadow.get("des_speed_decision_entries")
    distributions = shadow.get("des_speed_distributions")
    _log(f"[1.1] des_speed_decisions count = {len(decisions) if decisions else 0}")
    _check("des_speed_decisions is populated (not None/empty)", bool(decisions))
    _check(
        "des_speed_decisions count == 5 (matches the v2a unit ⑧ probe's XML grep)",
        len(decisions) == 5,
        detail=str([d["no"] for d in decisions]),
    )

    dec_6 = next((d for d in decisions if d["no"] == "6"), None)
    _check("decision no=6 present", dec_6 is not None)
    if dec_6 is not None:
        _log(f"       decision 6: {dec_6}")
        _check("decision 6 has 2 veh-class assignments", len(dec_6["veh_class_assignments"]) == 2)
        _check("decision 6 link == '6'", dec_6["link"] == "6", detail=dec_6["link"])
        assignment_10 = next(
            (a for a in dec_6["veh_class_assignments"] if a["veh_class_no"] == "10"), None
        )
        _check("decision 6 vehClass=10 assignment present", assignment_10 is not None)
        if assignment_10 is not None:
            _check(
                "decision 6 vehClass=10 des_speed_distr_no == '50'",
                assignment_10["des_speed_distr_no"] == "50",
                detail=str(assignment_10),
            )

    _log(f"[1.2] des_speed_decision_entries (flat) count = {len(entries) if entries else 0}")
    flat_ids = {e["id"] for e in entries} if entries else set()
    _check("flat entries include decision 6's vehClass=10 as id '6.10'", "6.10" in flat_ids)

    _log(f"[1.3] des_speed_distributions count = {len(distributions) if distributions else 0}")
    distr_nos = {d["no"] for d in distributions} if distributions else set()
    _check(
        "des_speed_distributions includes 25/50/12/40 (real Billings distributions)",
        {"25", "50", "12", "40"} <= distr_nos,
        detail=str(sorted(distr_nos)),
    )


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

    def _fresh_shadow(suffix: str):
        work_dir = _fresh_work_dir(suffix)
        am = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir)
        return am.clone()

    shadow = _fresh_shadow("phase2-bogus-decision")
    action = {
        "tool": "set_des_speed_decision",
        "params": {"decision_no": "999999", "veh_class_no": "10", "des_speed_distr_no": "25"},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.1] Bogus decision_no=999999 preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "bogus decision_no REFUSES via object_not_found",
        "object_not_found" in ids and report.blocking,
        detail=str(ids),
    )

    shadow = _fresh_shadow("phase2-bogus-vehclass")
    action = {
        "tool": "set_des_speed_decision",
        "params": {"decision_no": "6", "veh_class_no": "999999", "des_speed_distr_no": "25"},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.2] Real decision_no=6, bogus veh_class_no=999999 preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "bogus veh_class_no REFUSES via des_speed_decision_entry_not_found",
        "des_speed_decision_entry_not_found" in ids and report.blocking,
        detail=str(ids),
    )

    shadow = _fresh_shadow("phase2-bogus-distr")
    action = {
        "tool": "set_des_speed_decision",
        "params": {"decision_no": "6", "veh_class_no": "10", "des_speed_distr_no": "999999"},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.3] Bogus des_speed_distr_no=999999 preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "bogus des_speed_distr_no REFUSES via des_speed_distribution_not_found",
        "des_speed_distribution_not_found" in ids and report.blocking,
        detail=str(ids),
    )

    shadow = _fresh_shadow("phase2-bogus-restore-decision")
    action = {
        "tool": "set_des_speed_decision",
        "params": {
            "decision_no": "6",
            "veh_class_no": "10",
            "des_speed_distr_no": "25",
            "restore_decision_no": "999999",
            "restore_distr_no": "50",
        },
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.4] Bogus restore_decision_no=999999 preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "bogus restore_decision_no REFUSES via des_speed_decision_entry_not_found "
        "(object_not_found has NO coverage for this param)",
        "des_speed_decision_entry_not_found" in ids and report.blocking,
        detail=str(ids),
    )
    _check(
        "...and does NOT spuriously also fire object_not_found",
        "object_not_found" not in ids,
        detail=str(ids),
    )

    shadow = _fresh_shadow("phase2-real-unpaired")
    action = {
        "tool": "set_des_speed_decision",
        "params": {"decision_no": "6", "veh_class_no": "10", "des_speed_distr_no": "12"},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.5] Real UNPAIRED target preflight findings: {ids} (blocking={report.blocking})")
    _check("real unpaired target PASSES (not blocking)", not report.blocking, detail=str(ids))
    _check("unpaired target carries speed_decision_unpaired warning", "speed_decision_unpaired" in ids)

    shadow = _fresh_shadow("phase2-real-paired")
    action = {
        "tool": "set_des_speed_decision",
        "params": {
            "decision_no": "6",
            "veh_class_no": "10",
            "des_speed_distr_no": "12",
            "restore_decision_no": "7",
            "restore_distr_no": "40",
        },
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.6] Real PAIRED target preflight findings: {ids} (blocking={report.blocking})")
    _check("real paired target PASSES (not blocking)", not report.blocking, detail=str(ids))
    _check(
        "paired target does NOT carry speed_decision_unpaired warning",
        "speed_decision_unpaired" not in ids,
        detail=str(ids),
    )

    return {
        "decision_no": "6",
        "veh_class_no": "10",
        "des_speed_distr_no": "12",
        "restore_decision_no": "7",
        "restore_distr_no": "40",
    }


# ---------------------------------------------------------------------------
# Phase 3: ONE live COM paired write, full pipeline, then load_check
# ---------------------------------------------------------------------------


def _read_decision_from_xml(inpx_path: Path, decision_no: str) -> dict[str, Any] | None:
    root = ET.parse(inpx_path).getroot()
    for dec in root.findall(".//desSpeedDecision"):
        if dec.get("no") == decision_no:
            entries = {
                e.get("vehClass"): e.get("desSpeedDistr")
                for e in dec.findall(".//vehClassDesSpeedDistribution")
            }
            return {"no": dec.get("no"), "entries": entries}
    return None


def phase_3_live_com_write(example_inpx: Path, params: dict[str, str]) -> bool:
    _log("")
    _log("=" * 78)
    _log(
        "[Phase 3] ONE live COM PAIRED write: set_des_speed_decision("
        f"decision_no={params['decision_no']!r}, veh_class_no={params['veh_class_no']!r}, "
        f"des_speed_distr_no={params['des_speed_distr_no']!r}, "
        f"restore_decision_no={params['restore_decision_no']!r}, "
        f"restore_distr_no={params['restore_distr_no']!r}) on a CLONE of Roundabout Billings.US"
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
    _log(f"[3.1] Cloned Roundabout Billings.US -> {am.working_path} (never the original .inpx)")

    before_primary = _read_decision_from_xml(am.working_path, params["decision_no"])
    before_restore = _read_decision_from_xml(am.working_path, params["restore_decision_no"])
    _log(f"      decision {params['decision_no']} before (working copy XML): {before_primary}")
    _log(f"      decision {params['restore_decision_no']} before (working copy XML): {before_restore}")

    action = {"tool": "set_des_speed_decision", "params": dict(params)}
    engine = GuardrailEngine()

    write_ok = False
    session = VissimEditSession(am.working_path)
    try:
        _log("[3.2] preflight (real GuardrailEngine, real cloned shadow model)...")
        pre_report = engine.preflight(action, before_shadow)
        pre_ids = [f.constraint_id for f in pre_report.findings]
        _log(f"      findings={pre_ids} blocking={pre_report.blocking}")
        _check("Preflight PASSES for the real paired des-speed-decision write", not pre_report.blocking, detail=str(pre_ids))
        if pre_report.blocking:
            return False

        set_active_edit_session(session)
        _log("[3.3] Dispatching set_des_speed_decision via toolkit.dispatch (real COM, paired)...")
        envelope = dispatch("set_des_speed_decision", dict(params), registry=default_registry)
        _log(f"      envelope = {envelope}")
        _check("dispatch() status == ok", envelope.get("status") == "ok", detail=str(envelope))
        verification = envelope.get("verification", {})
        _check(
            "primary written_value == readback_value == '12'",
            verification.get("written_value") == "12" and verification.get("readback_value") == "12",
            detail=str(verification),
        )
        _check(
            "restore_written_value == restore_readback_value == '40'",
            verification.get("restore_written_value") == "40"
            and verification.get("restore_readback_value") == "40",
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

        after_primary = _read_decision_from_xml(am.working_path, params["decision_no"])
        after_restore = _read_decision_from_xml(am.working_path, params["restore_decision_no"])
        _log(f"[3.5] decision {params['decision_no']} after (working copy XML, post-SaveNetAs): {after_primary}")
        _log(f"      decision {params['restore_decision_no']} after (working copy XML, post-SaveNetAs): {after_restore}")

        _check(
            "Persisted PRIMARY decision's vehClass=10 desSpeedDistr == '12'",
            after_primary is not None and after_primary["entries"].get("10") == "12",
            detail=str(after_primary),
        )
        _check(
            "Persisted RESTORE decision's vehClass=10 desSpeedDistr == '40'",
            after_restore is not None and after_restore["entries"].get("10") == "40",
            detail=str(after_restore),
        )
        _check(
            "Persisted PRIMARY decision's SIBLING vehClass=20 desSpeedDistr UNCHANGED ('50')",
            after_primary is not None and after_primary["entries"].get("20") == "50",
            detail=str(after_primary),
        )
        _check(
            "Persisted RESTORE decision's SIBLING vehClass=20 desSpeedDistr UNCHANGED ('50')",
            after_restore is not None and after_restore["entries"].get("20") == "50",
            detail=str(after_restore),
        )

        write_ok = (
            envelope.get("status") == "ok"
            and after_primary is not None
            and after_primary["entries"].get("10") == "12"
            and after_primary["entries"].get("20") == "50"
            and after_restore is not None
            and after_restore["entries"].get("10") == "40"
            and after_restore["entries"].get("20") == "50"
            and not pre_report.blocking
            and "write_verification_failed" not in post_ids
        )
    finally:
        clear_active_edit_session()
        session.close()
        _log("[3.6] Vissim edit session closed.")

    if not write_ok:
        return False

    _log("[3.7] load_check on the SAME working copy -- must still be clean after the paired des-speed-decision edit...")
    set_active_run_source(am.working_path)
    try:
        check_envelope = dispatch("load_check", {}, registry=default_registry)
    finally:
        clear_active_run_source()
    _log(f"      load_check envelope = {check_envelope}")
    _check("load_check dispatch status == ok", check_envelope.get("status") == "ok", detail=str(check_envelope))
    check_verification = check_envelope.get("verification", {})
    _check(
        "load_check reports load_ok=True, init_ok=True after the paired edit",
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

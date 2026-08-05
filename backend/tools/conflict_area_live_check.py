"""LIVE ACCEPTANCE for v2 unit ④: set_conflict_area_status end to end on a
Beijing.CN clone -- preflight -> dispatch -> COM readback match -> persisted
evidence (XML grep) -> load_check still clean afterward.

[Phase 1] Static parse -- no COM. Parses the REAL example-network XML
(``runtime/com-smoke/*/*.inpx``, the same corpus the other live-check
scripts in this directory use) directly through
``services.agent.tools_network.build_shadow_model`` and asserts
``conflict_areas`` is populated (224 real conflict areas on Beijing, per the
Phase 1 probe), with the expected shape (no/link_a/link_b/link_pair/status/
name) and at least one Passive entry.

[Phase 2] Real preflight path -- no COM. Clones Beijing via a real
``ArtifactManager``, builds the real action dict, and runs it through the
REAL ``GuardrailEngine().preflight()``:
  - a bogus conflict_area_no REFUSES via object_not_found.
  - a real conflict_area_no PASSES (no refusal) and carries the
    conflict_area_status_change_confirm warning echoing the link pair.

[Phase 3] ONE live COM write, full pipeline (preflight -> toolkit.dispatch ->
postwrite), against a CLONE of Beijing (never the original .inpx):
set_conflict_area_status on a Passive conflict area -> AHASRIGHTOFWAY. Proves
preflight lets the write through, the COM write+readback succeed, the
persisted working copy's <conflictArea status="..."> attribute round-trips
on disk, and load_check on that same working copy still comes back clean
(load_ok=True, init_ok=True) -- the edit did not break the network.

Always closes every Vissim instance in a ``finally`` block;
attach_to_running=False throughout (via VissimEditSession / load_check's own
connect, per 00-EXECUTION-GUIDE.md rule 3). Never touches the original
Examples Demo folder.

Usage: python tools/conflict_area_live_check.py
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
WORK_ROOT = REPO_ROOT / "runtime" / "conflict-area-live-check"

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
    conflict_areas = shadow.get("conflict_areas")
    _log(f"[1.1] conflict_areas count = {len(conflict_areas) if conflict_areas else 0}")
    _check("conflict_areas is populated (not None/empty) on Beijing", bool(conflict_areas))
    _check("conflict_areas count == 224 (matches the Phase 1 probe's XML grep)", len(conflict_areas) == 224)

    first = conflict_areas[0]
    _check(
        "each entry has the expected shape",
        set(first.keys()) == {"no", "link_a", "link_b", "link_pair", "status", "name"},
        detail=str(sorted(first.keys())),
    )
    passive = [ca for ca in conflict_areas if ca["status"] == "PASSIVE"]
    _log(f"[1.2] Passive conflict areas: {len(passive)}/{len(conflict_areas)}")
    _check("at least one Passive conflict area exists on Beijing", len(passive) > 0)


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

    work_dir = _fresh_work_dir("phase2-bogus")
    am = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir)
    shadow = am.clone()
    action = {
        "tool": "set_conflict_area_status",
        "params": {"conflict_area_no": "999999", "status": "AHASRIGHTOFWAY"},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.1] Bogus conflict_area_no=999999 preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "bogus conflict_area_no REFUSES via object_not_found",
        "object_not_found" in ids and report.blocking,
        detail=str(ids),
    )

    passive_entry = next(ca for ca in shadow["conflict_areas"] if ca["status"] == "PASSIVE")
    target_no = passive_entry["no"]
    work_dir2 = _fresh_work_dir("phase2-real")
    am2 = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir2)
    shadow2 = am2.clone()
    action2 = {
        "tool": "set_conflict_area_status",
        "params": {"conflict_area_no": target_no, "status": "AHASRIGHTOFWAY"},
    }
    report2 = engine.preflight(action2, shadow2)
    ids2 = [f.constraint_id for f in report2.findings]
    _log(f"[2.2] Real conflict_area_no={target_no!r} preflight findings: {ids2} (blocking={report2.blocking})")
    _check(
        "real conflict_area_no PASSES (no object_not_found, not blocking)",
        "object_not_found" not in ids2 and not report2.blocking,
        detail=str(ids2),
    )
    confirm = next((f for f in report2.findings if f.constraint_id == "conflict_area_status_change_confirm"), None)
    _check("conflict_area_status_change_confirm warning present", confirm is not None)
    if confirm is not None:
        _log(f"      confirm finding details: {confirm.details}")
        _check(
            "confirm finding echoes the real link pair",
            confirm.details["link_a"] == passive_entry["link_a"]
            and confirm.details["link_b"] == passive_entry["link_b"],
            detail=str(confirm.details),
        )
        _check(
            "approval-card message mentions both link ids",
            passive_entry["link_a"] in confirm.messages["en"] and passive_entry["link_b"] in confirm.messages["en"],
            detail=confirm.messages["en"],
        )

    return {"target_no": target_no, "target_link_a": passive_entry["link_a"], "target_link_b": passive_entry["link_b"]}


# ---------------------------------------------------------------------------
# Phase 3: ONE live COM write, full pipeline, then load_check
# ---------------------------------------------------------------------------


def _read_conflict_area_from_xml(inpx_path: Path, no: str) -> dict[str, str] | None:
    root = ET.parse(inpx_path).getroot()
    for ca in root.findall(".//conflictArea"):
        if ca.get("no") == no:
            return dict(ca.attrib)
    return None


def phase_3_live_com_write(example_inpx: Path, target_no: str) -> bool:
    _log("")
    _log("=" * 78)
    _log(f"[Phase 3] ONE live COM write: set_conflict_area_status(no={target_no!r}) on a CLONE of Beijing")
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
    from services.agent.tools_check import load_check, LoadCheckParams
    from services.agent.tools_network import build_shadow_model
    from services.agent.tools_run import clear_active_run_source, set_active_run_source
    from services.agent.toolkit import default_registry, dispatch

    work_dir = _fresh_work_dir("phase3-live-write")
    am = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir)
    before_shadow = am.clone()
    _log(f"[3.1] Cloned Beijing -> {am.working_path} (never the original .inpx)")

    before_xml = _read_conflict_area_from_xml(am.working_path, target_no)
    _log(f"      conflict area {target_no} before (working copy XML): {before_xml}")
    old_status = before_xml["status"] if before_xml else None
    new_status = "PASSIVE" if old_status == "AHASRIGHTOFWAY" else "AHASRIGHTOFWAY"

    action = {
        "tool": "set_conflict_area_status",
        "params": {"conflict_area_no": target_no, "status": new_status},
    }
    engine = GuardrailEngine()

    ok = False
    session = VissimEditSession(am.working_path)
    try:
        _log("[3.2] preflight (real GuardrailEngine, real cloned shadow model)...")
        pre_report = engine.preflight(action, before_shadow)
        pre_ids = [f.constraint_id for f in pre_report.findings]
        _log(f"      findings={pre_ids} blocking={pre_report.blocking}")
        _check("Preflight PASSES for the real conflict area write", not pre_report.blocking, detail=str(pre_ids))
        if pre_report.blocking:
            return False

        set_active_edit_session(session)
        _log(f"[3.3] Dispatching set_conflict_area_status(conflict_area_no={target_no!r}, status={new_status!r}) "
             "via toolkit.dispatch (real COM)...")
        envelope = dispatch(
            "set_conflict_area_status",
            {"conflict_area_no": target_no, "status": new_status},
            registry=default_registry,
        )
        _log(f"      envelope = {envelope}")
        _check("dispatch() status == ok", envelope.get("status") == "ok", detail=str(envelope))
        verification = envelope.get("verification", {})
        _check(
            "written_value == readback_value == new_status (live COM read-back)",
            verification == {"written_value": new_status, "readback_value": new_status},
            detail=str(verification),
        )
        artifacts = envelope.get("artifacts", {})
        _check(
            "artifacts echo the link pair",
            artifacts.get("link_a") == before_xml["linkA"] and artifacts.get("link_b") == before_xml["linkB"],
            detail=str(artifacts),
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

        after_xml = _read_conflict_area_from_xml(am.working_path, target_no)
        _log(f"[3.5] conflict area {target_no} after (working copy XML, post-SaveNetAs): {after_xml}")
        _check(
            "Persisted working-copy XML status attribute == new_status",
            after_xml is not None and after_xml.get("status") == new_status,
            detail=f"expected {new_status!r}, got {after_xml}",
        )
        _check(
            "Persisted XML link pair UNCHANGED (only status moved)",
            after_xml is not None
            and after_xml.get("linkA") == before_xml["linkA"]
            and after_xml.get("linkB") == before_xml["linkB"],
        )

        write_ok = (
            envelope.get("status") == "ok"
            and after_xml is not None
            and after_xml.get("status") == new_status
            and not pre_report.blocking
        )
    finally:
        clear_active_edit_session()
        session.close()
        _log("[3.6] Vissim edit session closed.")

    if not write_ok:
        return False

    _log("[3.7] load_check on the SAME working copy -- must still be clean after the conflict-area edit...")
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

    ok = write_ok and check_verification == {"load_ok": True, "init_ok": True}
    return ok


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")
    example_inpx = _find_inpx(example_dir)

    phase_1_static_parse(example_inpx)
    phase2_result = phase_2_real_preflight(example_inpx)
    phase_3_ok = phase_3_live_com_write(example_inpx, phase2_result["target_no"])

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

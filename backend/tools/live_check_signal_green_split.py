"""LIVE ACCEPTANCE for v2a unit ⑨: set_signal_green_split end to end.

[Phase 1] Static parse -- no COM. build_shadow_model on a fresh Urban
Intersection Beijing.CN clone: signal_programs populated (1 FIXEDTIME
controller, 2 programs, 22 SGs each, active progNo=2), flat
signal_program_splits with three-part "{sc}.{prog}.{sg}" ids.

[Phase 2] Real preflight path -- no COM. Real ArtifactManager clones + real
GuardrailEngine().preflight():
  - bogus sc_no             REFUSES via object_not_found
  - bogus program_no        REFUSES via signal_program_split_not_found
  - bogus sg_no             REFUSES via signal_program_split_not_found
  - green_end beyond cycle  REFUSES via green_split_outside_cycle (Vissim
                            itself ACCEPTS this silently -- probe-proven --
                            so this refusal is the only screen)
  - real target             PASSES, carrying signal_green_split_change_confirm
                            (old->new window echo + overlap + education)
  - inactive-program target PASSES with the loud inactive-program note

[Phase 3] ONE live COM write, full pipeline (preflight -> toolkit.dispatch ->
postwrite), on a Beijing clone: set_signal_green_split(sc 1, program 2
[ACTIVE], sg 1, green 1s -> 8s; was 1s -> 15s). The write goes through the
COM-writable IntSupplyData program blob (the probe's central finding -- no
per-SG attribute is writable on a program-driven FIXEDTIME controller).
Asserts: dispatch ok + readback match; postwrite clean (target-persisted
check against the reparsed blob + nothing-else-moved diff over ALL 44 flat
splits); persisted working-copy blob has prog2/sg1 red-begin == 8000 ms with
prog2/sg2 AND all of prog1 byte-identical; load_check on the edited copy
still clean.

[Phase 4] BEHAVIOR ORACLE -- the proof CycTm never had: fresh COM load of
the persisted working copy, SimBreakAt=11, RunContinuous, then
SGs.ItemByKey(1).AttValue('SigState') must be RED (pre-edit ground truth at
t=11 is GREEN -- probe [1b.9]); SG 2 stays RED. The edited program provably
DRIVES simulated signal behavior; a silently-discarded blob would read GREEN.

[Phase 5] FIXEDTIMESIMPLE branch (Air Taxi Vertiport clone, direct
VissimController -- the plain EndRed/EndGreen attribute path): write green
window [10, 300] on SC 11 SG 1, readback, save_net_as, persisted XML
endRed/endGreen assert, fresh-parse green_window assert.

Always closes every Vissim instance in a ``finally`` block;
attach_to_running=False throughout. Never touches the original Examples Demo
folders.

Usage: python tools/live_check_signal_green_split.py
"""
from __future__ import annotations

import base64
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

REPO_ROOT = BACKEND_ROOT.parent
BEIJING_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
VERTIPORT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Air Taxi Vertiport"
)
WORK_ROOT = REPO_ROOT / "runtime" / "signal-green-split-live-check"

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


def _decode_blob_cmds(inpx_path: Path, sc_no: str) -> dict[str, dict[str, dict[str, str]]]:
    """{prog_id: {sg_id: {display: begin_ms}}} straight from the file."""
    root = ET.parse(inpx_path).getroot()
    sc_el = next(el for el in root.findall(".//signalController") if el.get("no") == sc_no)
    inner = ET.fromstring(base64.b64decode(sc_el.get("intSupplyData", "")).decode("utf-8"))
    result: dict[str, dict[str, dict[str, str]]] = {}
    for prog in list(inner.find("progs")):
        per_sg: dict[str, dict[str, str]] = {}
        sgs = prog.find("sgs")
        for sg in (list(sgs) if sgs is not None else []):
            cmds = sg.find("cmds")
            per_sg[sg.get("sg_id", "")] = {
                c.get("display", ""): c.get("begin", "") for c in (list(cmds) if cmds is not None else [])
            }
        result[prog.get("id", "")] = per_sg
    return result


# ---------------------------------------------------------------------------
# Phase 1: static parse
# ---------------------------------------------------------------------------


def phase_1_static_parse(example_inpx: Path) -> None:
    _log("=" * 78)
    _log("[Phase 1] build_shadow_model signal_programs against the real Beijing XML")
    _log("=" * 78)

    from services.agent.tools_network import build_shadow_model

    shadow = build_shadow_model(example_inpx)
    controllers = shadow.get("signal_programs")
    splits = shadow.get("signal_program_splits")
    _check("signal_programs populated", bool(controllers))
    sc1 = next((c for c in controllers if c["sc_no"] == "1"), None)
    _check("controller 1 present, FIXEDTIME", sc1 is not None and sc1["controller_type"] == "FIXEDTIME")
    if sc1 is not None:
        _check("active_program_no == '2' (progNo attribute)", sc1["active_program_no"] == "2")
        prog_nos = [p["prog_no"] for p in sc1["programs"]]
        _check("both programs parsed", prog_nos == ["1", "2"], detail=str(prog_nos))
        prog2 = next(p for p in sc1["programs"] if p["prog_no"] == "2")
        _check("program 2 cycle_s == 130.0", prog2["cycle_s"] == 130.0, detail=str(prog2["cycle_s"]))
        _check("program 2 has 22 sg_splits", len(prog2["sg_splits"]) == 22, detail=str(len(prog2["sg_splits"])))
        sg1 = next(s for s in prog2["sg_splits"] if s["sg_no"] == "1")
        _check(
            "ACTIVE program's SG 1 green window == [1, 15] s",
            sg1["green_start_s"] == 1.0 and sg1["green_end_s"] == 15.0,
            detail=str(sg1),
        )
        _check("SG 1 head_count > 0 (impact datum)", sg1["head_count"] > 0, detail=str(sg1["head_count"]))
    flat_ids = {s["id"] for s in splits} if splits else set()
    _check("flat splits include '1.2.1' (three-part id)", "1.2.1" in flat_ids)
    _check("flat splits count == 44 (22 SGs x 2 programs)", len(flat_ids) == 44, detail=str(len(flat_ids)))
    if splits:
        active_split = next(s for s in splits if s["id"] == "1.2.1")
        _check("flat '1.2.1' is marked active", active_split["active"] is True)
        _check(
            "flat '1.2.1' green_window dict",
            active_split["green_window"] == {"green_start_s": 1.0, "green_end_s": 15.0},
            detail=str(active_split["green_window"]),
        )


# ---------------------------------------------------------------------------
# Phase 2: real preflight path (clone + GuardrailEngine, no COM)
# ---------------------------------------------------------------------------


def phase_2_real_preflight(example_inpx: Path) -> None:
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

    def _preflight(label: str, params: dict[str, Any]):
        shadow = _fresh_shadow(f"phase2-{label}")
        action = {"tool": "set_signal_green_split", "params": params}
        report = engine.preflight(action, shadow)
        ids = [f.constraint_id for f in report.findings]
        _log(f"[2.{label}] params={params} -> findings={ids} blocking={report.blocking}")
        return report, ids

    report, ids = _preflight(
        "bogus-sc", {"sc_no": "999", "sg_no": "1", "program_no": "2", "green_start_s": 1.0, "green_end_s": 8.0}
    )
    _check("bogus sc_no REFUSES via object_not_found", "object_not_found" in ids and report.blocking)

    report, ids = _preflight(
        "bogus-program", {"sc_no": "1", "sg_no": "1", "program_no": "9", "green_start_s": 1.0, "green_end_s": 8.0}
    )
    _check(
        "bogus program_no REFUSES via signal_program_split_not_found",
        "signal_program_split_not_found" in ids and report.blocking,
        detail=str(ids),
    )

    report, ids = _preflight(
        "bogus-sg", {"sc_no": "1", "sg_no": "99", "program_no": "2", "green_start_s": 1.0, "green_end_s": 8.0}
    )
    _check(
        "bogus sg_no REFUSES via signal_program_split_not_found",
        "signal_program_split_not_found" in ids and report.blocking,
        detail=str(ids),
    )

    report, ids = _preflight(
        "beyond-cycle", {"sc_no": "1", "sg_no": "1", "program_no": "2", "green_start_s": 1.0, "green_end_s": 200.0}
    )
    _check(
        "green_end beyond the 130s cycle REFUSES via green_split_outside_cycle",
        "green_split_outside_cycle" in ids and report.blocking,
        detail=str(ids),
    )

    report, ids = _preflight(
        "real-target", {"sc_no": "1", "sg_no": "1", "program_no": "2", "green_start_s": 1.0, "green_end_s": 8.0}
    )
    _check("real target PASSES (not blocking)", not report.blocking, detail=str(ids))
    _check(
        "real target carries signal_green_split_change_confirm",
        "signal_green_split_change_confirm" in ids,
        detail=str(ids),
    )
    confirm = next(
        (f for f in report.findings if f.constraint_id == "signal_green_split_change_confirm"), None
    )
    if confirm is not None:
        _log(f"       confirm details: {confirm.details}")
        _check(
            "confirm echoes the OLD window [1, 15]",
            confirm.details["old_green_window"] == {"green_start_s": 1.0, "green_end_s": 15.0},
            detail=str(confirm.details.get("old_green_window")),
        )
        _check("confirm targets the ACTIVE program", confirm.details["targets_active_program"] is True)
        _check(
            "confirm carries the Vissim-will-not-stop-you education (ko)",
            "막지 않습니다" in confirm.messages["ko"],
        )

    report, ids = _preflight(
        "inactive-program", {"sc_no": "1", "sg_no": "1", "program_no": "1", "green_start_s": 20.0, "green_end_s": 40.0}
    )
    confirm = next(
        (f for f in report.findings if f.constraint_id == "signal_green_split_change_confirm"), None
    )
    _check(
        "inactive-program target passes but is loudly flagged as NOT the active program",
        not report.blocking
        and confirm is not None
        and confirm.details["targets_active_program"] is False
        and "NOT the active program" in confirm.messages["en"],
        detail=str(ids),
    )


# ---------------------------------------------------------------------------
# Phase 3: ONE live COM write, full pipeline, then load_check
# ---------------------------------------------------------------------------


def phase_3_live_com_write(example_inpx: Path) -> tuple[bool, Path | None]:
    _log("")
    _log("=" * 78)
    _log(
        "[Phase 3] ONE live COM write via the IntSupplyData blob: "
        "set_signal_green_split(sc 1, program 2 [ACTIVE], sg 1, green 1s -> 8s) on a Beijing clone"
    )
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

    cmds_before = _decode_blob_cmds(am.working_path, "1")
    _log(f"      blob before: prog2 sg1 cmds={cmds_before['2']['1']} prog1 sg1 cmds={cmds_before['1']['1']}")

    params = {"sc_no": "1", "sg_no": "1", "program_no": "2", "green_start_s": 1.0, "green_end_s": 8.0}
    action = {"tool": "set_signal_green_split", "params": dict(params)}
    engine = GuardrailEngine()

    write_ok = False
    session = VissimEditSession(am.working_path)
    try:
        _log("[3.2] preflight (real GuardrailEngine, real cloned shadow model)...")
        pre_report = engine.preflight(action, before_shadow)
        pre_ids = [f.constraint_id for f in pre_report.findings]
        _log(f"      findings={pre_ids} blocking={pre_report.blocking}")
        _check("Preflight PASSES for the real green-split write", not pre_report.blocking, detail=str(pre_ids))
        if pre_report.blocking:
            return False, None

        set_active_edit_session(session)
        _log("[3.3] Dispatching set_signal_green_split via toolkit.dispatch (real COM, blob write)...")
        envelope = dispatch("set_signal_green_split", dict(params), registry=default_registry)
        _log(f"      envelope = {envelope}")
        _check("dispatch() status == ok", envelope.get("status") == "ok", detail=str(envelope))
        verification = envelope.get("verification", {})
        _check(
            "written_value == readback_value == {green_start_s: 1.0, green_end_s: 8.0}",
            verification.get("written_value") == {"green_start_s": 1.0, "green_end_s": 8.0}
            and verification.get("readback_value") == {"green_start_s": 1.0, "green_end_s": 8.0},
            detail=str(verification),
        )
        artifacts = envelope.get("artifacts", {})
        _check(
            "artifacts report controller_type=FIXEDTIME, program_no=2",
            artifacts.get("controller_type") == "FIXEDTIME" and artifacts.get("program_no") == 2,
            detail=str(artifacts),
        )

        after_shadow = build_shadow_model(am.working_path)
        postwrite_report = engine.postwrite(action, before_shadow, after_shadow, verification)
        post_ids = [f.constraint_id for f in postwrite_report.findings]
        _log(f"[3.4] postwrite findings={post_ids} blocking={postwrite_report.blocking}")
        _check(
            "Postwrite clean (target-persisted + nothing-else-moved over all 44 splits)",
            "write_verification_failed" not in post_ids,
            detail=str(post_ids),
        )

        cmds_after = _decode_blob_cmds(am.working_path, "1")
        _log(f"[3.5] blob after: prog2 sg1 cmds={cmds_after['2']['1']} prog1 sg1 cmds={cmds_after['1']['1']}")
        _check(
            "persisted blob: prog2 sg1 green-on 1000 ms, red-on 8000 ms",
            cmds_after["2"]["1"] == {"3": "1000", "1": "8000"},
            detail=str(cmds_after["2"]["1"]),
        )
        _check(
            "persisted blob: prog2 sg2 UNTOUCHED",
            cmds_after["2"]["2"] == cmds_before["2"]["2"],
            detail=f"{cmds_before['2']['2']} -> {cmds_after['2']['2']}",
        )
        _check(
            "persisted blob: ALL of prog1 UNTOUCHED",
            cmds_after["1"] == cmds_before["1"],
        )

        write_ok = (
            envelope.get("status") == "ok"
            and "write_verification_failed" not in post_ids
            and cmds_after["2"]["1"] == {"3": "1000", "1": "8000"}
        )
    finally:
        clear_active_edit_session()
        session.close()
        _log("[3.6] Vissim edit session closed.")

    if not write_ok:
        return False, None

    _log("[3.7] load_check on the SAME working copy -- must still be clean after the blob edit...")
    set_active_run_source(am.working_path)
    try:
        check_envelope = dispatch("load_check", {}, registry=default_registry)
    finally:
        clear_active_run_source()
    _log(f"      load_check envelope = {check_envelope}")
    check_verification = check_envelope.get("verification", {})
    _check(
        "load_check reports load_ok=True, init_ok=True after the blob edit",
        check_verification == {"load_ok": True, "init_ok": True},
        detail=str(check_verification),
    )
    return write_ok and check_verification == {"load_ok": True, "init_ok": True}, am.working_path


# ---------------------------------------------------------------------------
# Phase 4: BEHAVIOR ORACLE (SimBreakAt + SigState)
# ---------------------------------------------------------------------------


def phase_4_behavior_oracle(edited_inpx: Path) -> None:
    _log("")
    _log("=" * 78)
    _log("[Phase 4] BEHAVIOR ORACLE: the edited program must DRIVE simulated signal state")
    _log("          (pre-edit ground truth at t=11: SG1=GREEN -- probe [1b.9]; edited: red from 8s)")
    _log("=" * 78)

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    try:
        _log("[4.1] Fresh dedicated Vissim instance (attach_to_running=False) + LoadNet(edited copy)...")
        started = time.monotonic()
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(edited_inpx))
        _log(f"      connected+loaded in {time.monotonic() - started:.1f}s")

        controller.vissim.Simulation.SetAttValue("SimBreakAt", 11)
        controller.vissim.Simulation.RunContinuous()
        sim_sec = controller.vissim.Simulation.AttValue("SimSec")
        sg1_state = controller.vissim.Net.SignalControllers.ItemByKey(1).SGs.ItemByKey(1).AttValue("SigState")
        sg2_state = controller.vissim.Net.SignalControllers.ItemByKey(1).SGs.ItemByKey(2).AttValue("SigState")
        _log(f"[4.2] t={sim_sec}: SigState SG1={sg1_state!r} SG2={sg2_state!r}")
        _check(
            "SG1 is RED at t=11 on the edited program (was GREEN pre-edit) -- the blob edit "
            "provably drives behavior",
            str(sg1_state) == "RED",
            detail=str(sg1_state),
        )
        _check("SG2 stays RED at t=11 (its window 63..105 is untouched)", str(sg2_state) == "RED")
        try:
            controller.vissim.Simulation.Stop()
        except Exception:
            pass
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        _log("[4.3] Oracle Vissim instance closed.")


# ---------------------------------------------------------------------------
# Phase 5: FIXEDTIMESIMPLE branch (plain EndRed/EndGreen attributes)
# ---------------------------------------------------------------------------


def phase_5_fixedtimesimple(example_dir: Path) -> None:
    _log("")
    _log("=" * 78)
    _log("[Phase 5] FIXEDTIMESIMPLE branch on an Air Taxi Vertiport clone (SC 11, SG 1)")
    _log("=" * 78)

    work_dir = _fresh_work_dir("phase5-simple")
    clone_dir = work_dir / example_dir.name
    shutil.copytree(example_dir, clone_dir)
    working = _find_inpx(clone_dir)
    _log(f"[5.1] Working copy: {working}")

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    try:
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(working))

        before = controller.get_signal_green_split(11, 1)
        _log(f"[5.2] before: {before}")
        _check(
            "getter reads the XML ground truth (endRed=998 -> green_start, endGreen=0 -> green_end)",
            before["green_start_s"] == 998.0 and before["green_end_s"] == 0.0,
            detail=str(before),
        )
        _check("getter reports FIXEDTIMESIMPLE", before["controller_type"] == "FIXEDTIMESIMPLE")

        written = controller.set_signal_green_split(11, 1, 10.0, 300.0, 1)
        readback = controller.get_signal_green_split(11, 1, 1)
        _log(f"[5.3] written={written} readback={readback}")
        _check(
            "readback matches the written window [10, 300]",
            readback["green_start_s"] == 10.0 and readback["green_end_s"] == 300.0,
            detail=str(readback),
        )

        saved = working.with_name(working.stem + "_saved" + working.suffix)
        controller.save_net_as(saved)
        root = ET.parse(saved).getroot()
        sc_el = next(el for el in root.findall(".//signalController") if el.get("no") == "11")
        sg_el = next(sg for sg in sc_el.find("sgs") if sg.get("no") == "1")
        _log(f"[5.4] persisted XML SG1 attrs: endRed={sg_el.get('endRed')} endGreen={sg_el.get('endGreen')}")
        _check(
            "persisted XML endRed=10, endGreen=300",
            sg_el.get("endRed") == "10" and sg_el.get("endGreen") == "300",
        )

        from services.signal_parser import parse_signal_programs

        parsed = {c["sc_no"]: c for c in parse_signal_programs(saved)}
        split = parsed["11"]["programs"][0]["sg_splits"][0]
        _check(
            "fresh parse of the saved file shows the new green window",
            split["green_start_s"] == 10.0 and split["green_end_s"] == 300.0,
            detail=str(split),
        )
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        _log("[5.5] Vissim instance closed.")


def main() -> None:
    if not BEIJING_EXAMPLE.is_dir():
        raise SystemExit(f"Example folder not found: {BEIJING_EXAMPLE}")
    example_inpx = _find_inpx(BEIJING_EXAMPLE)

    phase_1_static_parse(example_inpx)
    phase_2_real_preflight(example_inpx)
    phase_3_ok, edited_path = phase_3_live_com_write(example_inpx)
    if phase_3_ok and edited_path is not None:
        phase_4_behavior_oracle(edited_path)
    else:
        _log("[Phase 4] SKIPPED -- Phase 3 did not complete cleanly.")
    if VERTIPORT_EXAMPLE.is_dir():
        phase_5_fixedtimesimple(VERTIPORT_EXAMPLE)
    else:
        _log(f"[Phase 5] SKIPPED -- example folder not found: {VERTIPORT_EXAMPLE}")

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

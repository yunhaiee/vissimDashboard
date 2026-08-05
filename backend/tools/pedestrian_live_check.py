"""Q6 unit (3) LIVE END-TO-END CHECK: add_pedestrian_input /
set_pedestrian_input_volume / remove_pedestrian_input against a REAL cloned
Examples Demo network -- no fake anywhere in this script. House shape =
``parking_lot_live_check.py`` (Q6 unit (2)'s own), including its ``_log`` TEE:
the transcript is written to ``runtime/pedestrian-live-check/live_check.log``
rather than printed only, because unit (1)'s counterfactual evidence survived
its session solely in a scratchpad when this was a bare ``print``.

THE LICENCE QUESTION was settled by the probe before this script existed: this
box creates and simulates Viswalk objects, so these are real write verbs. That
verdict is behavioural, not a flag read -- no licence/module attribute exists
on the Vissim COM object at all.

Five phases:

  [Phase A] ``add_pedestrian_input`` through the real ``toolkit.dispatch`` +
  the real ``GuardrailEngine.postwrite`` against genuine before/after shadow
  models -- and then **the counterfactual the house rule demands is RUN, not
  assumed**: ``_ALLOWS_NEW_OBJECTS_IN['add_pedestrian_input']`` is popped, and
  also NARROWED to the WRONG class (``{'pedestrian_areas'}``), with the
  resulting refusals and their evidence rows printed. An exemption that cannot
  be shown to be load-bearing has no business being in the table. (Narrowing
  to the composition classes is NOT a runnable variant -- they are not shadow-
  model classes at all, so the diff cannot see them; the log says so at the
  point it matters. An earlier draft of this header claimed otherwise.)

  [Phase B] ``set_pedestrian_input_volume`` on the input Phase A created,
  plus its own postwrite.

  [Phase C] ``remove_pedestrian_input``: the clean removal + the
  ``_ALLOWS_REMOVED_OBJECTS_IN`` counterfactual.

  [Phase D] THE REFUSAL -- ``add_pedestrian_input`` on an area with no
  pedestrian routing decision, which is this unit's flagship measured failure
  (the saved network stops initialising, and the demand volume is irrelevant).
  Run at volume 750 AND at volume 0, because the probe measured both failing.

  [Phase E] The COM load oracle over the final working copy: Vissim's own
  opinion on everything the three verbs did to it.

Usage: python tools/pedestrian_live_check.py [example_dir]
Default example: Urban Intersection Beijing.CN -- the METRES network whose
Viswalk layer (16 areas, 8 of them routed, 8 pedestrian inputs) this unit's
probe measured every behaviour on.

Always disconnects/closes its Vissim session(s) in ``finally`` blocks.
"""
from __future__ import annotations

import os
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

EXAMPLES_ROOT = Path(r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo")
DEFAULT_EXAMPLE = EXAMPLES_ROOT / "Urban Intersection Beijing.CN"
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "pedestrian-live-check"

#: Beijing's areas split 8 routed / 8 unrouted (measured). Phase A uses a
#: routed one, Phase D an UNROUTED one -- both shipped with the network, so
#: the refusal is demonstrated on real connected geometry rather than on a
#: freshly minted polygon.
TARGET_VOLUME_PPH = 750.0
RETARGET_VOLUME_PPH = 1250.0


def _log(msg: str) -> None:
    # Tee to a machine-readable transcript next to the work dirs -- unit (1)'s
    # recorded lesson: a bare print leaves the counterfactual evidence
    # nowhere once the session ends.
    print(msg, flush=True)
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    with (WORK_ROOT / "live_check.log").open("a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def _fresh_work_dir(name: str) -> Path:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    for stale in WORK_ROOT.glob(f"{name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    work_dir = WORK_ROOT / f"{name}_{os.getpid()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _evidence(report: Any) -> list[str]:
    rows: list[str] = []
    for finding in report.findings:
        for item in (finding.details or {}).get("nothing_else_moved_diff", []) or []:
            rows.append(
                f"{item['object_class']}#{item['object_id']}:{item.get('field', item['change'])}"
            )
    return rows


def _counterfactual(
    engine: Any,
    guardrails: Any,
    action: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    verification: dict[str, Any],
    table_name: str,
    tool: str,
    replacement: Any,
    label: str,
) -> bool:
    """Temporarily replace ONE entry of one exemption table, re-run the REAL
    postwrite over the SAME before/after pair, and report whether the correct
    call is now refused. ``replacement is None`` pops the entry entirely."""
    table = getattr(guardrails, table_name)
    original = table.get(tool)
    try:
        if replacement is None:
            table.pop(tool, None)
        else:
            table[tool] = frozenset(replacement)
        report = engine.postwrite(action, before, after, verification)
        rows = _evidence(report)
        _log(f"      [counterfactual] {table_name}[{tool!r}] = {label}: blocking="
             f"{report.blocking} findings={[f.constraint_id for f in report.findings]} "
             f"evidence={rows}")
        return bool(report.blocking)
    finally:
        if original is None:
            table.pop(tool, None)
        else:
            table[tool] = original


def _ped_counts(inpx: Path) -> dict[str, int]:
    root = ET.parse(inpx).getroot()
    return {
        "pedestrian_inputs": len(root.findall(".//pedestrianInputs/pedestrianInput")),
        "areas": len(root.findall(".//areas/area")),
        "ped_volume_intervals": len(root.findall(".//timeIntervalPedVolume")),
        "ped_routing_decisions_static": len(root.findall(".//pedestrianRoutingDecisionStatic")),
        "ped_routes_static": len(root.findall(".//pedestrianRouteStatic")),
        "pedestrian_compositions": len(
            root.findall(".//pedestrianCompositions/pedestrianComposition")
        ),
    }


def phase_abcd(example_dir: Path) -> bool:
    _log("=" * 78)
    _log("[Phases A-D] add -> set volume -> remove -> refuse, with the counterfactuals")
    _log("=" * 78)

    from services.agent import guardrails as guardrails_module
    from services.agent.edit_session import (
        VissimEditSession,
        clear_active_edit_session,
        set_active_edit_session,
    )
    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager
    import services.agent.tools_edit  # noqa: F401 - registers the 3 verbs
    from services.agent.tools_network import build_shadow_model
    from services.agent.toolkit import default_registry, dispatch

    work_dir = _fresh_work_dir("phaseABCD")
    artifact_manager = ArtifactManager(
        source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir
    )
    before = artifact_manager.clone()
    engine = GuardrailEngine(disabled=False)
    _log(f"[A.1] Cloned {example_dir.name} -> {artifact_manager.working_path}")
    _log(f"      persisted counts: {_ped_counts(artifact_manager.working_path)}")

    areas = before["pedestrian_areas"]
    routed = [a["no"] for a in areas if a.get("has_routing_decision")]
    unrouted = [a["no"] for a in areas if not a.get("has_routing_decision")]
    _log(f"      areas: {len(areas)} total; {len(routed)} routed {routed}; "
         f"{len(unrouted)} UNROUTED {unrouted}")
    if not routed or not unrouted:
        _log("      This network cannot exercise both arms (needs at least one routed AND one "
             "unrouted area).")
        return False
    target_area, unrouted_area = routed[0], unrouted[0]

    ok = True
    session = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session)

        # ---- Phase A ---------------------------------------------------
        add_params = {"area_no": target_area, "volume_pph": TARGET_VOLUME_PPH}
        _log(f"\n[A.2] add_pedestrian_input({add_params}) ...")
        add_env = dispatch("add_pedestrian_input", dict(add_params), registry=default_registry)
        _log(f"      envelope = {add_env}")
        ok = ok and add_env["status"] == "ok"
        new_no = (add_env.get("artifacts") or {}).get("new_pedestrian_input_no")

        after_add = build_shadow_model(artifact_manager.working_path)
        inputs_after = after_add["pedestrian_inputs"]
        _log(f"      reparsed: {len(before['pedestrian_inputs'])} -> {len(inputs_after)} "
             f"pedestrian inputs; new = "
             f"{next((i for i in inputs_after if i['no'] == str(new_no)), None)}")
        ok = ok and len(inputs_after) == len(before["pedestrian_inputs"]) + 1
        _log(f"      persisted counts now: {_ped_counts(artifact_manager.working_path)}")

        add_action = {"tool": "add_pedestrian_input", "params": add_params}
        _log("\n[A.3] REAL GuardrailEngine.postwrite for the create ...")
        report = engine.postwrite(add_action, before, after_add, add_env["verification"])
        _log(f"      report.blocking={report.blocking} "
             f"findings={[f.constraint_id for f in report.findings]}")
        ok = ok and report.blocking is False

        _log("\n[A.4] THE COUNTERFACTUALS for _ALLOWS_NEW_OBJECTS_IN['add_pedestrian_input'] "
             "(RUN over this same real before/after pair, never assumed):")
        popped = _counterfactual(
            engine, guardrails_module, add_action, before, after_add, add_env["verification"],
            "_ALLOWS_NEW_OBJECTS_IN", "add_pedestrian_input", None, "POPPED ENTIRELY",
        )
        _log(f"      => popped refuses: {popped}")
        narrowed = _counterfactual(
            engine, guardrails_module, add_action, before, after_add, add_env["verification"],
            "_ALLOWS_NEW_OBJECTS_IN", "add_pedestrian_input",
            {"pedestrian_areas"}, "NARROWED to {'pedestrian_areas'} (the wrong class)",
        )
        _log(f"      => narrowed-to-the-wrong-class refuses: {narrowed}")
        _log("      (only ONE class is opted in for this verb: the probe measured the create as "
             "exactly one new pedestrian_inputs row. The composition classes that DO materialise "
             "on a zero-composition network are not shadow-model classes at all, so the diff "
             "cannot see them and they need no exemption -- which is why narrowing to them is "
             "not a runnable variant here.)")
        ok = ok and popped and narrowed

        # ---- Phase B ---------------------------------------------------
        _log("\n" + "=" * 78)
        _log("[Phase B] set_pedestrian_input_volume on the input Phase A created")
        _log("=" * 78)
        set_params = {"pedestrian_input_no": str(new_no), "volume_pph": RETARGET_VOLUME_PPH}
        _log(f"[B.1] set_pedestrian_input_volume({set_params}) ...")
        set_env = dispatch(
            "set_pedestrian_input_volume", dict(set_params), registry=default_registry
        )
        _log(f"      envelope = {set_env}")
        ok = ok and set_env["status"] == "ok"

        after_set = build_shadow_model(artifact_manager.working_path)
        persisted = next((i for i in after_set["pedestrian_inputs"] if i["no"] == str(new_no)), None)
        _log(f"      persisted input now: {persisted}")
        ok = ok and persisted is not None and persisted["volume"] == RETARGET_VOLUME_PPH

        set_action = {"tool": "set_pedestrian_input_volume", "params": set_params}
        _log("\n[B.2] REAL postwrite for the volume write ...")
        report = engine.postwrite(set_action, after_add, after_set, set_env["verification"])
        _log(f"      report.blocking={report.blocking} "
             f"findings={[f.constraint_id for f in report.findings]} evidence={_evidence(report)}")
        ok = ok and report.blocking is False

        # ---- Phase C ---------------------------------------------------
        _log("\n" + "=" * 78)
        _log("[Phase C] remove_pedestrian_input + the removal-side counterfactual")
        _log("=" * 78)
        remove_params = {"pedestrian_input_no": str(new_no)}
        _log(f"[C.1] remove_pedestrian_input({remove_params}) ...")
        rm_env = dispatch("remove_pedestrian_input", dict(remove_params), registry=default_registry)
        _log(f"      envelope = {rm_env}")
        ok = ok and rm_env["status"] == "ok"

        after_remove = build_shadow_model(artifact_manager.working_path)
        _log(f"      reparsed: {len(after_set['pedestrian_inputs'])} -> "
             f"{len(after_remove['pedestrian_inputs'])} pedestrian inputs")
        _log(f"      persisted counts now: {_ped_counts(artifact_manager.working_path)}")
        ok = ok and len(after_remove["pedestrian_inputs"]) == len(after_set["pedestrian_inputs"]) - 1

        rm_action = {"tool": "remove_pedestrian_input", "params": remove_params}
        _log("\n[C.2] REAL postwrite for the removal ...")
        report = engine.postwrite(rm_action, after_set, after_remove, rm_env["verification"])
        _log(f"      report.blocking={report.blocking} "
             f"findings={[f.constraint_id for f in report.findings]}")
        ok = ok and report.blocking is False

        _log("\n[C.3] THE COUNTERFACTUAL for "
             "_ALLOWS_REMOVED_OBJECTS_IN['remove_pedestrian_input'] (RUN):")
        popped_rm = _counterfactual(
            engine, guardrails_module, rm_action, after_set, after_remove, rm_env["verification"],
            "_ALLOWS_REMOVED_OBJECTS_IN", "remove_pedestrian_input", None, "POPPED ENTIRELY",
        )
        _log(f"      => popped refuses: {popped_rm}")
        ok = ok and popped_rm

        # ---- Phase D ---------------------------------------------------
        _log("\n" + "=" * 78)
        _log("[Phase D] THE REFUSAL -- an origin area with no pedestrian routing decision")
        _log("=" * 78)
        _log(f"      area {unrouted_area} shipped with this network (real, connected walkable "
             "geometry) but carries no routing decision. The probe measured that an input there "
             "stops the network initialising, at ANY volume.")
        for volume in (TARGET_VOLUME_PPH, 0.0):
            params = {"area_no": unrouted_area, "volume_pph": volume}
            env = dispatch("add_pedestrian_input", dict(params), registry=default_registry)
            _log(f"\n[D.{'1' if volume else '2'}] add_pedestrian_input({params}) -> "
                 f"status={env['status']} error_type={env['error_type']}")
            _log(f"      message ({len(env['message'])} chars): {env['message']}")
            ok = ok and env["status"] == "error"
            # Refusals must survive toolkit.dispatch's 300-char cap whole.
            if len(env["message"]) >= 300:
                _log("      WARNING: refusal exceeds the 300-char dispatch cap and will truncate.")
                ok = False

        after_refusals = build_shadow_model(artifact_manager.working_path)
        moved = len(after_refusals["pedestrian_inputs"]) != len(after_remove["pedestrian_inputs"])
        _log(f"\n[D.3] the refusals touched NOTHING: pedestrian input count unchanged = "
             f"{not moved}")
        ok = ok and not moved

        # And every legal spelling of a ROUTED area is still accepted.
        _log("\n[D.4] canonical-id spellings of a ROUTED area must all still be ACCEPTED:")
        for spelling in (target_area, f"0{target_area}", f" {target_area}", f"+{target_area}"):
            env = dispatch(
                "add_pedestrian_input",
                {"area_no": spelling, "volume_pph": 10.0},
                registry=default_registry,
            )
            _log(f"      area_no={spelling!r} -> status={env['status']}")
            ok = ok and env["status"] == "ok"
        # ... and every spelling of an UNROUTED one must all still be REFUSED.
        _log("\n[D.5] canonical-id spellings of an UNROUTED area must all still be REFUSED:")
        for spelling in (unrouted_area, f"0{unrouted_area}", f" {unrouted_area}",
                         f"+{unrouted_area}"):
            env = dispatch(
                "add_pedestrian_input",
                {"area_no": spelling, "volume_pph": 10.0},
                registry=default_registry,
            )
            _log(f"      area_no={spelling!r} -> status={env['status']}")
            ok = ok and env["status"] == "error"
    finally:
        try:
            session.close()
        except Exception:
            pass
        clear_active_edit_session()

    _log(f"\n[Phases A-D] {'PASS' if ok else 'FAIL'}")
    return ok, artifact_manager.working_path


def phase_e(working_inpx: Path) -> bool:
    _log("\n" + "=" * 78)
    _log("[Phase E] the COM load oracle over the final working copy")
    _log("=" * 78)
    from services.inpx_structure import run_load_oracle

    result = run_load_oracle(working_inpx)
    _log(f"      load_ok={result.get('load_ok')} init_ok={result.get('init_ok')}")
    _log(f"      load_error={str(result.get('load_error'))[:200]!r}")
    _log(f"      init_error={str(result.get('init_error'))[:200]!r}")
    ok = bool(result.get("load_ok")) and bool(result.get("init_ok"))
    _log(f"\n[Phase E] {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> int:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    _log("\n" + "#" * 78)
    _log(f"# Q6 unit (3) pedestrian live check -- {example_dir.name} (pid {os.getpid()})")
    _log("#" * 78)

    import pythoncom

    pythoncom.CoInitialize()
    try:
        abcd_ok, working = phase_abcd(example_dir)
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    e_ok = phase_e(working)

    _log("\n" + "=" * 78)
    _log(f"OVERALL: {'PASS' if (abcd_ok and e_ok) else 'FAIL'}")
    _log(f"transcript: {WORK_ROOT / 'live_check.log'}")
    _log("=" * 78)
    return 0 if (abcd_ok and e_ok) else 1


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        raise SystemExit(main())

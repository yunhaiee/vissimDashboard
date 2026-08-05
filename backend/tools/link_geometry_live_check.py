"""Q6 unit ① LIVE END-TO-END CHECK: set_link_lane_count / set_link_geometry /
set_link_point_radius against a REAL cloned Examples Demo network -- no fake
anywhere in this script. House shape = ``add_links_connectors_live_check.py``
(Q4 unit ⑤'s own live check).

Four phases:

  [Phase A] ``set_link_lane_count``, BOTH directions, each through the real
  ``toolkit.dispatch`` + the real ``GuardrailEngine.postwrite`` against
  genuine before/after shadow models -- and then **the counterfactual the
  house rule demands is RUN, not assumed**: each ``_ALLOWS_NEW_OBJECTS_IN`` /
  ``_ALLOWS_REMOVED_OBJECTS_IN`` entry this unit adds is popped (and narrowed)
  in turn, and the resulting refusal is printed with its evidence rows. An
  exemption that cannot be shown to be load-bearing has no business being
  in the table.

  [Phase B] ``set_link_geometry``: a legal reshape (postwrite must be clean)
  and then the REFUSAL -- a shortening that would silently relocate the
  objects anchored past the new end, which is this unit's flagship measured
  silent failure.

  [Phase C] ``set_link_point_radius``: the two-step curvature write, verified
  by REPARSING the persisted working copy (the shadow model is structurally
  blind to per-point curvature, so the tool's own file readback is the proof)
  and by the COM load oracle.

  [Phase D] The COM load oracle over the final working copy: Vissim's own
  opinion on everything the three verbs did to it.

Usage: python tools/link_geometry_live_check.py [example_dir]
Default example: Urban Intersection Beijing.CN (2026 Examples Demo) -- the
same METERS-display network this unit's own probe measured every landmine on.

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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "link-geometry-live-check"

#: Beijing links with FOUR lanes whose LAST lane no element references -- the
#: probe's own static scan picked these, and Parts A/B measured both
#: directions on them.
GROW_LINK = "4"
SHRINK_LINK = "8"


def _log(msg: str) -> None:
    # Tee to a machine-readable transcript next to the work dirs: the
    # 2026-08-03 run's refusal evidence survived only in a scratchpad
    # because this was a bare print — the runtime dir held .inpx work
    # copies but no record of what the counterfactuals measured.
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


def phase_a(example_dir: Path) -> bool:
    _log("=" * 78)
    _log("[Phase A] set_link_lane_count, both directions + the exemption counterfactuals")
    _log("=" * 78)

    from services.agent import guardrails as guardrails_module
    from services.agent.edit_session import (
        VissimEditSession,
        clear_active_edit_session,
        set_active_edit_session,
    )
    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    import services.agent.tools_edit  # noqa: F401 - registers the 3 verbs
    from services.agent.toolkit import default_registry, dispatch

    work_dir = _fresh_work_dir("phaseA")
    artifact_manager = ArtifactManager(
        source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir
    )
    before = artifact_manager.clone()
    engine = GuardrailEngine(disabled=False)
    _log(f"[A.1] Cloned {example_dir.name} -> {artifact_manager.working_path}")

    grow = next(l for l in before["links"] if l["id"] == GROW_LINK)
    shrink = next(l for l in before["links"] if l["id"] == SHRINK_LINK)
    _log(f"      link {GROW_LINK}: {grow['lanes']} lanes, widths {grow['lane_widths']}, "
         f"points {grow['points']}")
    _log(f"      link {SHRINK_LINK}: {shrink['lanes']} lanes, widths {shrink['lane_widths']}")

    ok = True
    session = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session)

        _log(f"\n[A.2] set_link_lane_count(link_id={GROW_LINK!r}, lane_count={grow['lanes'] + 1}) ...")
        grow_params = {"link_id": GROW_LINK, "lane_count": grow["lanes"] + 1}
        grow_env = dispatch("set_link_lane_count", dict(grow_params), registry=default_registry)
        _log(f"      envelope = {grow_env}")
        ok = ok and grow_env["status"] == "ok"

        after_grow = build_shadow_model(artifact_manager.working_path)
        grown = next(l for l in after_grow["links"] if l["id"] == GROW_LINK)
        _log(f"      reparsed: lanes {grow['lanes']} -> {grown['lanes']}, points "
             f"{grow['points']} -> {grown['points']}")
        ok = ok and grown["lanes"] == grow["lanes"] + 1

        grow_action = {"tool": "set_link_lane_count", "params": grow_params}
        _log("\n[A.3] REAL GuardrailEngine.postwrite for the widen ...")
        report = engine.postwrite(grow_action, before, after_grow, grow_env["verification"])
        _log(f"      report.blocking={report.blocking} "
             f"findings={[f.constraint_id for f in report.findings]}")
        ok = ok and report.blocking is False

        _log("\n[A.4] THE COUNTERFACTUALS for _ALLOWS_NEW_OBJECTS_IN['set_link_lane_count'] "
             "(each RUN over this same real before/after pair, never assumed):")
        popped = _counterfactual(
            engine, guardrails_module, grow_action, before, after_grow, grow_env["verification"],
            "_ALLOWS_NEW_OBJECTS_IN", "set_link_lane_count", None, "POPPED ENTIRELY",
        )
        lanes_only = _counterfactual(
            engine, guardrails_module, grow_action, before, after_grow, grow_env["verification"],
            "_ALLOWS_NEW_OBJECTS_IN", "set_link_lane_count", {"link_lanes"}, "{'link_lanes'} only",
        )
        conflict_only = _counterfactual(
            engine, guardrails_module, grow_action, before, after_grow, grow_env["verification"],
            "_ALLOWS_NEW_OBJECTS_IN", "set_link_lane_count", {"conflict_areas"},
            "{'conflict_areas'} only",
        )
        _log(f"      => popped refuses: {popped}; link_lanes-only refuses: {lanes_only}; "
             f"conflict_areas-only refuses: {conflict_only}")
        _log("      (each class is individually necessary iff BOTH narrowings refuse; a class "
             "whose narrowing does NOT refuse would not be load-bearing on this network)")
        ok = ok and popped

        _log(f"\n[A.5] set_link_lane_count(link_id={SHRINK_LINK!r}, "
             f"lane_count={shrink['lanes'] - 1}) -- the shrink direction ...")
        before_shrink = build_shadow_model(artifact_manager.working_path)
        shrink_params = {"link_id": SHRINK_LINK, "lane_count": shrink["lanes"] - 1}
        shrink_env = dispatch("set_link_lane_count", dict(shrink_params), registry=default_registry)
        _log(f"      envelope = {shrink_env}")
        ok = ok and shrink_env["status"] == "ok"

        after_shrink = build_shadow_model(artifact_manager.working_path)
        shrunk = next(l for l in after_shrink["links"] if l["id"] == SHRINK_LINK)
        _log(f"      reparsed: lanes {shrink['lanes']} -> {shrunk['lanes']}")
        ok = ok and shrunk["lanes"] == shrink["lanes"] - 1

        shrink_action = {"tool": "set_link_lane_count", "params": shrink_params}
        _log("\n[A.6] REAL GuardrailEngine.postwrite for the narrow ...")
        report = engine.postwrite(
            shrink_action, before_shrink, after_shrink, shrink_env["verification"]
        )
        _log(f"      report.blocking={report.blocking} "
             f"findings={[f.constraint_id for f in report.findings]}")
        ok = ok and report.blocking is False

        _log("\n[A.7] THE COUNTERFACTUAL for _ALLOWS_REMOVED_OBJECTS_IN['set_link_lane_count'] ...")
        removed_popped = _counterfactual(
            engine, guardrails_module, shrink_action, before_shrink, after_shrink,
            shrink_env["verification"], "_ALLOWS_REMOVED_OBJECTS_IN", "set_link_lane_count",
            None, "POPPED ENTIRELY",
        )
        _log(f"      => popped refuses: {removed_popped}")
        ok = ok and removed_popped

        _log("\n[A.8] The reference-integrity REFUSAL: shrinking a link whose remaining lanes "
             "are referenced (Vissim's own RemoveLane would silently delete what is on them) ...")
        refuse_env = dispatch(
            "set_link_lane_count", {"link_id": "1", "lane_count": 1}, registry=default_registry
        )
        _log(f"      envelope = {refuse_env}")
        ok = ok and refuse_env["status"] == "error"
    finally:
        try:
            session.close()
        except Exception:
            pass
        clear_active_edit_session()

    _log(f"\n[Phase A] {'PASS' if ok else 'FAIL'}")
    return ok


def phase_b(example_dir: Path) -> bool:
    _log("=" * 78)
    _log("[Phase B] set_link_geometry: a legal reshape, then the truncation REFUSAL")
    _log("=" * 78)

    from services.agent.edit_session import (
        VissimEditSession,
        clear_active_edit_session,
        set_active_edit_session,
    )
    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    import services.agent.tools_edit  # noqa: F401
    from services.agent.toolkit import default_registry, dispatch

    work_dir = _fresh_work_dir("phaseB")
    artifact_manager = ArtifactManager(
        source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir
    )
    before = artifact_manager.clone()
    engine = GuardrailEngine(disabled=False)

    # A plain link that nothing is anchored to and no connector touches: the
    # only place a geometry edit can be BOTH legal and free of Vissim's own
    # connector re-snapping. Chosen by measurement over the clone, not by hand.
    anchored: set[str] = set()
    for link in before["links"]:
        if link.get("is_connector"):
            anchored.add(str(link.get("from_link")))
            anchored.add(str(link.get("to_link")))
    for object_class, field in (
        ("signal_heads", "link"), ("detectors", "link"), ("data_collection_points", "link"),
        ("stop_signs", "link"), ("pt_stops", "link"), ("queue_counters", "link_id"),
    ):
        for obj in before.get(object_class, []) or []:
            anchored.add(str(obj.get(field)))
    free = [
        l for l in before["links"]
        if not l.get("is_connector") and str(l["id"]) not in anchored and len(l["points"]) == 2
    ]
    _log(f"[B.1] {len(free)} plain 2-point link(s) with nothing anchored on them: "
         f"{[l['id'] for l in free][:8]}")
    if not free:
        # Every plain link on a real signalised junction carries SOMETHING
        # (Beijing: all 56 do). The legal-reshape half is still measurable:
        # LENGTHENING a link can never truncate an anchor, so an anchored link
        # is a valid subject for it -- and it is the more interesting subject,
        # because it is where Vissim's own connector re-snapping could show up
        # as postwrite evidence.
        free = [l for l in before["links"] if not l.get("is_connector") and len(l["points"]) == 2]
        _log(f"      Falling back to an ANCHORED link and LENGTHENING it (which cannot truncate "
             f"anything): {[l['id'] for l in free][:8]}")
    if not free:
        _log("      No plain 2-point link at all -- cannot run this phase here.")
        return False
    target = free[0]
    points = [list(p) for p in target["points"]]
    _log(f"      target link {target['id']}: points={points} length={target['length']}")

    ok = True
    session = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session)

        # Extend the last point 10 m along the link's own direction: a real
        # shape change that cannot truncate anything (it only lengthens).
        dx, dy = points[1][0] - points[0][0], points[1][1] - points[0][1]
        norm = (dx * dx + dy * dy) ** 0.5 or 1.0
        new_points = [points[0], [points[1][0] + 10.0 * dx / norm, points[1][1] + 10.0 * dy / norm]]
        _log(f"\n[B.2] set_link_geometry(link_id={target['id']!r}, points_xy={new_points}) ...")
        params = {"link_id": target["id"], "points_xy": new_points}
        env = dispatch("set_link_geometry", dict(params), registry=default_registry)
        _log(f"      envelope = {env}")
        ok = ok and env["status"] == "ok"

        after = build_shadow_model(artifact_manager.working_path)
        reshaped = next(l for l in after["links"] if l["id"] == target["id"])
        _log(f"      reparsed: length {target['length']} -> {reshaped['length']}, "
             f"points {reshaped['points']}")

        _log("\n[B.3] REAL GuardrailEngine.postwrite for the reshape ...")
        action = {"tool": "set_link_geometry", "params": params}
        report = engine.postwrite(action, before, after, env["verification"])
        _log(f"      report.blocking={report.blocking} "
             f"findings={[f.constraint_id for f in report.findings]} evidence={_evidence(report)}")
        ok = ok and report.blocking is False

        _log("\n[B.3b] THE COUNTERFACTUAL for _ALLOWS_NEW_OBJECTS_IN['set_link_geometry'] "
             "(this entry exists BECAUSE this phase caught its absence live) ...")
        from services.agent import guardrails as guardrails_module

        popped = _counterfactual(
            engine, guardrails_module, action, before, after, env["verification"],
            "_ALLOWS_NEW_OBJECTS_IN", "set_link_geometry", None, "POPPED ENTIRELY",
        )
        _log(f"      => popped refuses: {popped}")
        ok = ok and popped

        # THE refusal: shorten a link that carries real anchors.
        anchored_link = next(
            (l for l in after["links"] if not l.get("is_connector") and str(l["id"]) in anchored),
            None,
        )
        if anchored_link is not None:
            half = [
                anchored_link["points"][0],
                [
                    anchored_link["points"][0][0]
                    + (anchored_link["points"][-1][0] - anchored_link["points"][0][0]) * 0.5,
                    anchored_link["points"][0][1]
                    + (anchored_link["points"][-1][1] - anchored_link["points"][0][1]) * 0.5,
                ],
            ]
            if len(anchored_link["points"]) == 2:
                _log(f"\n[B.4] set_link_geometry on ANCHORED link {anchored_link['id']}, halved -- "
                     "must REFUSE ...")
                refuse_env = dispatch(
                    "set_link_geometry",
                    {"link_id": anchored_link["id"], "points_xy": half},
                    registry=default_registry,
                )
                _log(f"      envelope = {refuse_env}")
                ok = ok and refuse_env["status"] == "error"
    finally:
        try:
            session.close()
        except Exception:
            pass
        clear_active_edit_session()

    _log(f"\n[Phase B] {'PASS' if ok else 'FAIL'}")
    return ok


def phase_c(example_dir: Path) -> bool:
    _log("=" * 78)
    _log("[Phase C] set_link_point_radius: the two-step curvature write, verified from the file")
    _log("=" * 78)

    from services.agent.edit_session import (
        VissimEditSession,
        clear_active_edit_session,
        set_active_edit_session,
    )
    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    import services.agent.tools_edit  # noqa: F401
    from services.agent.toolkit import default_registry, dispatch

    work_dir = _fresh_work_dir("phaseC")
    artifact_manager = ArtifactManager(
        source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir
    )
    before = artifact_manager.clone()
    engine = GuardrailEngine(disabled=False)

    target = next(
        (l for l in before["links"] if not l.get("is_connector") and len(l["points"]) >= 3), None
    )
    if target is None:
        _log("      No multi-point plain link on this network.")
        return False
    _log(f"[C.1] target link {target['id']} with {len(target['points'])} points")

    ok = True
    session = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session)
        params = {"link_id": target["id"], "point_index": 2, "radius_m": 250.0}
        _log(f"\n[C.2] set_link_point_radius({params}) ...")
        env = dispatch("set_link_point_radius", dict(params), registry=default_registry)
        _log(f"      envelope = {env}")
        ok = ok and env["status"] == "ok"

        root = ET.parse(artifact_manager.working_path).getroot()
        link_el = next(
            el for el in root.findall(".//links/link") if el.get("no") == str(target["id"])
        )
        point = link_el.findall("geometry/linkPolyPts/linkPolyPoint")[1]
        _log(f"[C.3] the PERSISTED point: rad={point.get('rad')!r} "
             f"radiusSrc={point.get('radiusSrc')!r}")
        ok = ok and point.get("radiusSrc") == "USERINPUT"
        ok = ok and abs(float(point.get("rad") or 0) - 250.0) < 1e-6

        after = build_shadow_model(artifact_manager.working_path)
        _log("\n[C.4] REAL GuardrailEngine.postwrite for the curvature write (the shadow model is "
             "structurally blind to per-point radius -- this proves the write causes no COLLATERAL "
             "diff, and the file readback above is what proves the write itself) ...")
        report = engine.postwrite(
            {"tool": "set_link_point_radius", "params": params}, before, after, env["verification"]
        )
        _log(f"      report.blocking={report.blocking} "
             f"findings={[f.constraint_id for f in report.findings]} evidence={_evidence(report)}")
        ok = ok and report.blocking is False

        _log("\n[C.5] restore: set_link_point_radius with radius_m omitted ...")
        restore_env = dispatch(
            "set_link_point_radius",
            {"link_id": target["id"], "point_index": 2},
            registry=default_registry,
        )
        _log(f"      envelope = {restore_env}")
        ok = ok and restore_env["status"] == "ok"
        root = ET.parse(artifact_manager.working_path).getroot()
        link_el = next(
            el for el in root.findall(".//links/link") if el.get("no") == str(target["id"])
        )
        point = link_el.findall("geometry/linkPolyPts/linkPolyPoint")[1]
        _log(f"      the PERSISTED point after restore: radiusSrc={point.get('radiusSrc')!r}")
        ok = ok and point.get("radiusSrc") == "CALCULATED"
    finally:
        try:
            session.close()
        except Exception:
            pass
        clear_active_edit_session()

    _log("\n[C.6] COM load oracle over the curvature-edited working copy ...")
    from services.agent.tools_check import _load_check

    result = _load_check(artifact_manager.working_path)
    _log(f"      load_check_result = {result}")
    ok = ok and bool(result.get("load_ok")) and bool(result.get("init_ok"))

    _log(f"\n[Phase C] {'PASS' if ok else 'FAIL'}")
    return ok


def main() -> int:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.exists():
        _log(f"Example network not found: {example_dir}")
        return 1

    import pythoncom

    pythoncom.CoInitialize()
    try:
        results = {
            "A": phase_a(example_dir),
            "B": phase_b(example_dir),
            "C": phase_c(example_dir),
        }
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    _log("\n" + "=" * 78)
    _log("SUMMARY")
    _log("=" * 78)
    for phase, ok in results.items():
        _log(f"  Phase {phase}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        raise SystemExit(main())

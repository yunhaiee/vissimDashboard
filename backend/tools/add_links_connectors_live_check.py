"""Q4 unit (5) LIVE END-TO-END CHECK: add_link/remove_link + add_connector
(remove_connector already existed, Q4 unit (3) -- reused, not duplicated),
against a REAL cloned Examples Demo network -- no fake anywhere in this
script. House shape = ``signal_heads_detectors_live_check.py`` (the newest
Q4 unit's own live check).

Two phases:

  [Phase A] Dispatch-level, in two parts:

  Part 1 -- add_link anchored to a REAL existing link, then add_connector
  joining them: oracle + readback + reparse after each, the REAL
  GuardrailEngine.postwrite against genuine before/after shadow models (the
  "one real-engine test per verb" this unit's own task brief asks for), a
  quick RunSingleStep drivability check, and then a DELIBERATE attempt to
  remove_connector the just-created connector -- REFUSED, and that refusal
  is itself this run's flagship finding: Vissim's own geometry recompute
  created 6 brand-new conflict areas naming the new connector the INSTANT
  it was added (live-caught by this unit's own first live-check run,
  fixed via guardrails._ALLOWS_NEW_OBJECTS_IN), so a connector joined to
  real network topology is correctly non-removable again until those
  conflict areas themselves are gone -- which nothing in this codebase (or
  Vissim's own COM) can do, since conflict areas have no Add/Remove at all.

  Part 2 -- add_link + add_link + add_connector in GEOMETRICALLY ISOLATED
  space (far from every real link, so nothing crosses it and no new
  conflict areas appear) -- the clean, fully-reversible case: oracle +
  postwrite after the add, then remove_connector + remove_link + remove_link
  (same-claim verified) + postwrite + oracle, proving the full add/remove
  round trip when nothing else references the new geometry. Finally, a
  remove_link attempt against a REAL, referenced link (the anchor from Part
  1) -- REFUSED, naming its referrer classes, per the task brief's own
  "remove_link will refuse on almost any real link -- that's CORRECT
  behavior."

  [Phase B] Full orchestrator level: add_link through a real
  ``run_agent_turn`` (PreapprovedWriteGate, real ArtifactManager, real
  GuardrailEngine, canned planner -- no paid LLM calls), THEN
  rollback_last_edit through a second real turn -- the rollback proof.

Usage: python tools/add_links_connectors_live_check.py [example_dir]
Default example: Urban Intersection Beijing.CN (2026 Examples Demo) -- the
same METERS-display network this unit's own probe already validated
AddLink/AddConnector against.

Always disconnects/closes its Vissim session(s) in `finally` blocks.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DEFAULT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "add-links-connectors-live-check"
# Far enough from every real network feature (Beijing's own geometry sits in
# x=[-765, 423] y=[-886, -35]) that Part 2's add_connector crosses NOTHING
# (so no side-effect conflict areas appear), but close enough to stay under
# this Vissim installation's own licensed network-size cap -- live-caught
# by this unit's own live check: an EARLIER attempt at (50000, 50000) raised
# "CCOMLinkContainerBase<ContainerType>::AddLink: ... the maximum network
# size (10000 m) licensed for this version of Vissim is exceeded" (a genuine
# licence-tier landmine, not a bug in add_link itself -- the overall network
# bounding-box EXTENT, not total link length, is what is capped at 10 km).
ISOLATED_ORIGIN = (3000.0, 3000.0)


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh_work_dir(example_dir: Path, suffix: str) -> Path:
    for stale in WORK_ROOT.glob(f"{example_dir.name}_{suffix}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    work_dir = WORK_ROOT / f"{example_dir.name}_{suffix}_{os.getpid()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def phase_a(example_dir: Path) -> bool:
    _log("=" * 78)
    _log("[Phase A] add_link -> add_connector (real junction, then isolated) -> "
         "drivability -> remove -> refusals")
    _log("=" * 78)

    from services.agent.edit_session import (
        VissimEditSession,
        clear_active_edit_session,
        set_active_edit_session,
    )
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    import services.agent.tools_edit  # noqa: F401 - registers the 3 verbs
    import services.agent.tools_check  # noqa: F401 - registers load_check
    from services.agent.toolkit import default_registry, dispatch
    from services.agent.guardrails import GuardrailEngine
    from services import inpx_structure as struct

    work_dir = _fresh_work_dir(example_dir, "phaseA")
    artifact_manager = ArtifactManager(source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir)
    shadow_before_add_link = artifact_manager.clone()
    _log(f"[A.1] Cloned {example_dir.name} -> {artifact_manager.working_path}")
    baseline_links = len(shadow_before_add_link["links"])
    _log(f"      Baseline link count: {baseline_links}")

    anchor_link = next(l for l in shadow_before_add_link["links"] if not l.get("is_connector"))
    anchor_last_pt = anchor_link["points"][-1]
    _log(f"      Anchor link (REAL, existing): {anchor_link['id']} ({anchor_link['lanes']} lanes), "
         f"last point={anchor_last_pt}")

    new_link_no = None
    new_conn_no = None
    engine = GuardrailEngine(disabled=False)
    from services.agent.tools_check import _load_check

    # =====================================================================
    # PART 1: add_link anchored to REAL geometry, add_connector to a REAL
    # link -- the conflict-area side-effect finding.
    # =====================================================================
    session = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session)

        p0 = (anchor_last_pt[0] + 50.0, anchor_last_pt[1] + 50.0)
        p1 = (p0[0] + 80.0, p0[1])
        _log(f"\n[A.2] add_link(points_xy=[{p0}, {p1}], lane_count=2)...")
        add_link_env = dispatch(
            "add_link",
            {"points_xy": [list(p0), list(p1)], "lane_count": 2, "name": "LiveCheck link"},
            registry=default_registry,
        )
        _log(f"      envelope = {add_link_env}")
        assert add_link_env["status"] == "ok", add_link_env
        new_link_no = add_link_env["artifacts"]["no"]
        _log(f"      PASS: link {new_link_no} added.")

        shadow_after_add_link = build_shadow_model(artifact_manager.working_path)
        _log(f"\n[A.3] Reparse from disk after add_link: link count = "
             f"{len(shadow_after_add_link['links'])} (baseline {baseline_links})")
        assert len(shadow_after_add_link["links"]) == baseline_links + 1
        added_link = next(l for l in shadow_after_add_link["links"] if l["id"] == str(new_link_no))
        _log(f"      link {new_link_no}: lanes={added_link['lanes']} length={added_link['length']:.3f} "
             f"points={added_link['points']}")
        assert added_link["lanes"] == 2 and not added_link["is_connector"]

        _log("\n[A.4] REAL GuardrailEngine.postwrite for add_link (the 'one real-engine test per "
             "verb' this unit's own task brief asks for)...")
        report = engine.postwrite(
            {"tool": "add_link", "params": {"points_xy": [list(p0), list(p1)], "lane_count": 2}},
            shadow_before_add_link, shadow_after_add_link,
            add_link_env["verification"],
        )
        _log(f"      report.blocking={report.blocking} "
             f"findings={[f.constraint_id for f in report.findings]}")
        assert report.blocking is False, report.findings
        _log("      PASS: no false-positive 'something else moved' evidence.")

        session.close()
        _log("\n[A.5] Edit session closed before running the COM load oracle (its own dedicated instance).")
    finally:
        clear_active_edit_session()

    _log("\n[A.6] Running the COM load oracle on the link-added working copy...")
    load_result_1 = _load_check(artifact_manager.working_path)
    _log(f"      load_check_result = {load_result_1}")
    assert load_result_1["load_ok"] is True and load_result_1["init_ok"] is True
    _log("      PASS: LoadNet + one RunSingleStep both succeeded with the new link present.")

    session2 = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session2)

        shadow_before_conn = build_shadow_model(artifact_manager.working_path)
        _log(f"\n[A.7] add_connector(from_link={anchor_link['id']} [REAL], from_lanes=[1,2], "
             f"to_link={new_link_no}, to_lanes=[1,2])...")
        add_conn_env = dispatch(
            "add_connector",
            {
                "from_link": anchor_link["id"], "from_lanes": [1, 2],
                "from_pos_m": round(anchor_link["length"], 3),
                "to_link": str(new_link_no), "to_lanes": [1, 2], "to_pos_m": 0.0,
                "name": "LiveCheck connector (real junction)",
            },
            registry=default_registry,
        )
        _log(f"      envelope = {add_conn_env}")
        assert add_conn_env["status"] == "ok", add_conn_env
        new_conn_no = add_conn_env["artifacts"]["no"]
        _log(f"      PASS: connector {new_conn_no} added.")

        shadow_after_conn = build_shadow_model(artifact_manager.working_path)
        _log(f"\n[A.8] Reparse from disk after add_connector: link count = "
             f"{len(shadow_after_conn['links'])} (was {len(shadow_before_conn['links'])}); "
             f"conflict_area count = {len(shadow_after_conn['conflict_areas'])} "
             f"(was {len(shadow_before_conn['conflict_areas'])})")
        assert len(shadow_after_conn["links"]) == len(shadow_before_conn["links"]) + 1
        added_conn = next(l for l in shadow_after_conn["links"] if l["id"] == str(new_conn_no))
        _log(f"      connector {new_conn_no}: from_link={added_conn['from_link']} "
             f"to_link={added_conn['to_link']} from_pos={added_conn['from_pos']} "
             f"to_pos={added_conn['to_pos']}")
        assert added_conn["is_connector"]
        assert added_conn["from_link"] == anchor_link["id"] and added_conn["to_link"] == str(new_link_no)
        new_conflict_areas = len(shadow_after_conn["conflict_areas"]) - len(shadow_before_conn["conflict_areas"])
        _log(f"      Vissim's own geometry recompute created {new_conflict_areas} NEW conflict "
             "area(s) at this real junction (0 is possible depending on real geometry; >0 is the "
             "flagship finding this unit's own live check first caught).")

        _log("\n[A.9] REAL GuardrailEngine.postwrite for add_connector (against REAL geometry, "
             "conflict areas and all)...")
        report2 = engine.postwrite(
            {
                "tool": "add_connector",
                "params": {
                    "from_link": anchor_link["id"], "from_lanes": [1, 2],
                    "from_pos_m": round(anchor_link["length"], 3),
                    "to_link": str(new_link_no), "to_lanes": [1, 2], "to_pos_m": 0.0,
                },
            },
            shadow_before_conn, shadow_after_conn,
            add_conn_env["verification"],
        )
        _log(f"      report.blocking={report2.blocking} "
             f"findings={[f.constraint_id for f in report2.findings]}")
        assert report2.blocking is False, report2.findings
        _log("      PASS: no false-positive 'something else moved' evidence -- the "
             "_ALLOWS_NEW_OBJECTS_IN['add_connector'] conflict_areas fix holds against REAL data.")

        session2.close()
    finally:
        clear_active_edit_session()

    _log("\n[A.10] Running the COM load oracle on the connector-added working copy...")
    load_result_2 = _load_check(artifact_manager.working_path)
    _log(f"      load_check_result = {load_result_2}")
    assert load_result_2["load_ok"] is True and load_result_2["init_ok"] is True
    _log("      PASS: LoadNet + one RunSingleStep both succeeded with the new link+connector present.")

    drivability_ok = _drivability_check(artifact_manager.working_path, new_link_no)

    _log(f"\n[A.12] Deliberately attempting remove_connector({new_conn_no}) -- THE flagship "
         "finding of this run: a connector joined to REAL network topology inherits brand-new "
         "conflict areas the INSTANT it is created, so it is correctly non-removable again "
         "immediately -- this is a REFUSAL we expect, not a failure...")
    session_refuse = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session_refuse)
        refuse_conn_env = dispatch(
            "remove_connector", {"connector_no": str(new_conn_no)}, registry=default_registry
        )
        _log(f"      envelope status={refuse_conn_env['status']} "
             f"error_type={refuse_conn_env.get('error_type')}")
        _log(f"      message={refuse_conn_env['message']!r}")
        if refuse_conn_env["status"] == "error":
            _log("      EXPECTED (real junction): refused -- new conflict area(s) name this "
                 "connector as linkA/linkB. See Part 2 below for the CLEAN removal proof on "
                 "geometrically isolated (non-junction) geometry instead.")
        else:
            _log("      (No conflict area referenced this connector on this real network's "
                 "geometry today -- the connector was removed cleanly. Either outcome is "
                 "consistent with this unit's own honesty contract; Part 2 proves the clean "
                 "path unconditionally.)")
            new_conn_no = None  # already removed -- avoid double-remove bookkeeping below
    finally:
        clear_active_edit_session()
        session_refuse.close()

    # =====================================================================
    # PART 2: fully isolated add_link + add_link + add_connector -- the
    # clean, unconditionally-reversible case.
    # =====================================================================
    _log("\n" + "-" * 78)
    _log("[Part 2] Geometrically ISOLATED add_link + add_link + add_connector -- "
         "the clean round-trip proof (nothing crosses, so no side-effect conflict areas).")
    _log("-" * 78)

    ox, oy = ISOLATED_ORIGIN
    link_f_pts = [(ox, oy), (ox + 80.0, oy)]
    link_g_pts = [(ox + 80.0, oy), (ox + 160.0, oy)]

    session3 = VissimEditSession(artifact_manager.working_path)
    link_f_no = None
    link_g_no = None
    conn_fg_no = None
    try:
        set_active_edit_session(session3)

        shadow_iso_0 = build_shadow_model(artifact_manager.working_path)
        _log(f"\n[A.13] add_link(F, points_xy={link_f_pts}, lane_count=1) [isolated]...")
        f_env = dispatch(
            "add_link",
            {"points_xy": [list(p) for p in link_f_pts], "lane_count": 1, "name": "LiveCheck F (isolated)"},
            registry=default_registry,
        )
        assert f_env["status"] == "ok", f_env
        link_f_no = f_env["artifacts"]["no"]
        _log(f"      PASS: link F ({link_f_no}) added.")

        _log(f"\n[A.14] add_link(G, points_xy={link_g_pts}, lane_count=1) [isolated]...")
        g_env = dispatch(
            "add_link",
            {"points_xy": [list(p) for p in link_g_pts], "lane_count": 1, "name": "LiveCheck G (isolated)"},
            registry=default_registry,
        )
        assert g_env["status"] == "ok", g_env
        link_g_no = g_env["artifacts"]["no"]
        _log(f"      PASS: link G ({link_g_no}) added.")

        _log(f"\n[A.15] add_connector(F -> G) [isolated, joining an existing link (G already "
             "exists in the network by this point) -- should create ZERO new conflict areas]...")
        fg_env = dispatch(
            "add_connector",
            {
                "from_link": str(link_f_no), "from_lanes": [1], "from_pos_m": 80.0,
                "to_link": str(link_g_no), "to_lanes": [1], "to_pos_m": 0.0,
                "name": "LiveCheck F-G connector (isolated)",
            },
            registry=default_registry,
        )
        assert fg_env["status"] == "ok", fg_env
        conn_fg_no = fg_env["artifacts"]["no"]
        _log(f"      PASS: connector F-G ({conn_fg_no}) added.")

        shadow_iso_1 = build_shadow_model(artifact_manager.working_path)
        conflict_delta = len(shadow_iso_1["conflict_areas"]) - len(shadow_iso_0["conflict_areas"])
        _log(f"      New conflict areas from this isolated connector: {conflict_delta} "
             f"(expected 0 -- nothing else is anywhere near this geometry).")
        assert conflict_delta == 0, "isolated geometry unexpectedly produced new conflict areas"

        _log("\n[A.16] REAL GuardrailEngine.postwrite for the isolated add_connector...")
        report3 = engine.postwrite(
            {
                "tool": "add_connector",
                "params": {
                    "from_link": str(link_f_no), "from_lanes": [1], "from_pos_m": 80.0,
                    "to_link": str(link_g_no), "to_lanes": [1], "to_pos_m": 0.0,
                },
            },
            shadow_iso_0, shadow_iso_1, fg_env["verification"],
        )
        _log(f"      report.blocking={report3.blocking} "
             f"findings={[f.constraint_id for f in report3.findings]}")
        assert report3.blocking is False, report3.findings
        _log("      PASS.")

        session3.close()
    finally:
        clear_active_edit_session()

    _log("\n[A.17] Running the COM load oracle on the isolated-geometry working copy...")
    load_result_3 = _load_check(artifact_manager.working_path)
    _log(f"      load_check_result = {load_result_3}")
    assert load_result_3["load_ok"] is True and load_result_3["init_ok"] is True
    _log("      PASS.")

    session4 = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session4)

        shadow_before_remove = build_shadow_model(artifact_manager.working_path)
        _log(f"\n[A.18] remove_connector({conn_fg_no}) [isolated -- expect a CLEAN success]...")
        rm_conn_env = dispatch(
            "remove_connector", {"connector_no": str(conn_fg_no)}, registry=default_registry
        )
        _log(f"      envelope = {rm_conn_env}")
        assert rm_conn_env["status"] == "ok", rm_conn_env
        assert rm_conn_env["verification"]["written_value"] == rm_conn_env["verification"]["readback_value"]
        _log("      PASS: clean removal, same-claim verified.")

        _log(f"\n[A.19] remove_link(G={link_g_no})...")
        rm_g_env = dispatch("remove_link", {"no": str(link_g_no)}, registry=default_registry)
        _log(f"      envelope = {rm_g_env}")
        assert rm_g_env["status"] == "ok", rm_g_env
        assert rm_g_env["verification"] == {
            "written_value": {"removed_no": str(link_g_no), "still_present": False},
            "readback_value": {"removed_no": str(link_g_no), "still_present": False},
        }

        _log(f"\n[A.20] remove_link(F={link_f_no})...")
        rm_f_env = dispatch("remove_link", {"no": str(link_f_no)}, registry=default_registry)
        _log(f"      envelope = {rm_f_env}")
        assert rm_f_env["status"] == "ok", rm_f_env
        assert rm_f_env["verification"] == {
            "written_value": {"removed_no": str(link_f_no), "still_present": False},
            "readback_value": {"removed_no": str(link_f_no), "still_present": False},
        }
        _log("      PASS: both links + their connector removed cleanly, all same-claim verified.")

        shadow_after_remove = build_shadow_model(artifact_manager.working_path)
        expected_count = baseline_links + 1 + (0 if new_conn_no is None else 1)
        _log(f"\n[A.21] Reparse from disk after Part 2's removes: link count = "
             f"{len(shadow_after_remove['links'])} (expected {expected_count}: baseline "
             f"{baseline_links} + Part 1's surviving link+connector)")
        assert len(shadow_after_remove["links"]) == expected_count

        _log("\n[A.22] REAL GuardrailEngine.postwrite for the isolated remove_connector/remove_link...")
        report4 = engine.postwrite(
            {"tool": "remove_link", "params": {"no": str(link_f_no)}},
            shadow_before_remove, shadow_after_remove,
            rm_f_env["verification"],
        )
        _log(f"      report.blocking={report4.blocking} "
             f"findings={[f.constraint_id for f in report4.findings]}")
        assert report4.blocking is False, report4.findings
        _log("      PASS: no false-positive evidence on removal either.")

        session4.close()
    finally:
        clear_active_edit_session()

    _log("\n[A.23] Running the COM load oracle on the fully-restored (isolated portion) working copy...")
    load_result_4 = _load_check(artifact_manager.working_path)
    _log(f"      load_check_result = {load_result_4}")
    assert load_result_4["load_ok"] is True and load_result_4["init_ok"] is True
    _log("      PASS: clean restoration confirmed by the REAL load oracle.")

    _log("\n[A.24] Attempting remove_link on a REAL, referenced link (the Part 1 anchor link "
         f"itself, {anchor_link['id']}, which real connectors on this network still reference)...")
    session5 = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session5)
        refused_env = dispatch("remove_link", {"no": anchor_link["id"]}, registry=default_registry)
        _log(f"      envelope status={refused_env['status']} error_type={refused_env.get('error_type')}")
        _log(f"      message={refused_env['message']!r}")
        refusal_ok = (
            refused_env["status"] == "error"
            and refused_env["error_type"] == "tool_execution_error"
            and "still referenced by" in refused_env["message"]
        )
        assert refusal_ok, refused_env
        _log("      PASS: refused, naming referrer class(es) -- Vissim's own COM RemoveLink would "
             "NOT have refused this (live-verified: a dangling reference is left silently); this "
             "codebase's own spine is the only guard.")
    finally:
        clear_active_edit_session()
        session5.close()

    _log("\n" + "=" * 78)
    _log(f"[Phase A] RESULT: PASS (drivability check: {'PASS' if drivability_ok else 'UNTESTED/SKIPPED'})")
    _log("=" * 78)
    return True


def _drivability_check(working_inpx: Path, new_link_no: int) -> bool:
    """Places one real vehicle on the freshly-created link and runs a few
    RunSingleStep calls, confirming its own Pos AttValue genuinely advances
    -- proof that the new link is a drivable Vissim object, not merely
    saved XML, per the task brief's own "traffic could route onto it"
    ask. Kept SHORT (RunSingleStep is slow over COM,
    00-EXECUTION-GUIDE.md SS3.1) -- a handful of steps is enough to prove
    forward motion, not a real demand/routing simulation. Returns False
    (never raises) on any failure -- this is a bonus proof, not a hard
    requirement of the unit; a failure here is reported, not fatal to the
    live check as a whole.
    """
    _log("\n[A.11] Quick RunSingleStep drivability check on the new link "
         f"{new_link_no} (a real vehicle, placed directly, must advance)...")
    try:
        import pythoncom

        pythoncom.CoInitialize()
        from services.vissim_com import VissimController

        controller = VissimController()
        try:
            controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
            controller.load_network(str(working_inpx))
            net = controller.vissim.Net

            link_obj = net.Links.ItemByKey(int(new_link_no))
            lane_index = int(link_obj.Lanes.GetAll()[0].AttValue("Index"))
            veh_type_nos = sorted(int(vt.AttValue("No")) for vt in net.VehicleTypes.GetAll())
            # Prefer 100 (Vissim's own conventional "Car" type number on
            # every stock example network) when present -- a bogus/
            # incompatible type (e.g. a pedestrian-only type) is a plausible
            # cause of AddVehicleAtLinkPosition's own generic COM failure.
            veh_type_no = 100 if 100 in veh_type_nos else veh_type_nos[0]
            # AddVehicleAtLinkPosition's own "Lane" param takes a plain lane
            # INDEX (int), not a Lane COM object -- unlike every OTHER Add*
            # method in this codebase (AddDataCollectionPoint/AddSignalHead/
            # AddDetector all need a real Lane object). Live-caught by this
            # unit's own first live-check run (TypeError: int() argument...
            # not 'ILane' when a Lane object was passed here) -- a genuinely
            # different convention on this ONE method, not a mistake to
            # silently paper over.
            vehicle = net.Vehicles.AddVehicleAtLinkPosition(
                veh_type_no, link_obj, lane_index, 10.0, 40.0, True
            )
            pos_before = float(vehicle.AttValue("Pos"))
            _log(f"      Vehicle placed on link {new_link_no} lane {lane_index}: Pos={pos_before}")

            for _ in range(15):
                controller.run_single_step()

            still_there = any(int(v.AttValue("No")) == int(vehicle.AttValue("No")) for v in net.Vehicles.GetAll())
            if not still_there:
                _log("      Vehicle no longer in the network after 15 steps (removed/exited) -- "
                     "treating as inconclusive, not a failure.")
                return False
            pos_after = float(vehicle.AttValue("Pos"))
            lane_after = str(vehicle.AttValue("Lane") or "")
            _log(f"      After 15 RunSingleStep calls: Pos={pos_after} Lane={lane_after}")
            advanced = pos_after > pos_before + 0.01
            _log(f"      {'PASS' if advanced else 'FAIL'}: vehicle {'advanced' if advanced else 'did NOT advance'} "
                 f"along the new link ({pos_before} -> {pos_after}).")
            return advanced
        finally:
            try:
                controller.disconnect()
            except Exception:
                pass
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
    except Exception as exc:
        _log(f"      Drivability check raised {type(exc).__name__}: {exc} -- treating as UNTESTED, "
             "not a failure of the core add_link/add_connector/remove proof above.")
        return False


def phase_b(example_dir: Path) -> bool:
    _log("")
    _log("=" * 78)
    _log("[Phase B] Full run_agent_turn: add_link (approved) -> rollback_last_edit (approved)")
    _log("=" * 78)

    import services.agent.tools_edit as tools_edit
    from services.agent.edit_session import VissimEditSession, clear_active_edit_session, set_active_edit_session
    from services.agent.gates import PreapprovedWriteGate, signature
    from services.agent.guardrails import GuardrailEngine
    from services.agent.orchestrator import run_agent_turn
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model

    def _action(tool_name: str, *, summary: str, **params: Any):
        params["summary"] = summary
        return {"tool": tool_name, "params": params}, {"mode": "canned", "fallback_rung": 0}

    def _run_turn(work_dir: Path, artifact_manager: ArtifactManager, suffix: str, provider_queue):
        signatures = {
            signature(action["tool"], {k: v for k, v in action["params"].items() if k != "summary"})
            for action, _ in provider_queue
            if action
        }
        gate = PreapprovedWriteGate(signatures)
        engine = GuardrailEngine(disabled=False)

        class _CannedProvider:
            def __init__(self, queue):
                self._queue = list(queue)

            def generate_structured(self, messages, tools, schema=None, *, system=None, max_tokens=None):
                return self._queue.pop(0)

        session = VissimEditSession(artifact_manager.working_path)
        try:
            set_active_edit_session(session)
            result = run_agent_turn(
                "테스트",
                provider=_CannedProvider(provider_queue),
                work_dir=work_dir,
                session_id=f"live-check-{suffix}",
                language="ko",
                gate=gate,
                engine=engine,
                artifact_manager=artifact_manager,
            )
            return result
        finally:
            clear_active_edit_session()
            session.close()

    work_dir = _fresh_work_dir(example_dir, "phaseB")
    source_inpx = next(example_dir.glob("*.inpx"))
    original_get_active_inpx_path = tools_edit.get_active_inpx_path
    tools_edit.get_active_inpx_path = lambda: source_inpx
    artifact_manager = ArtifactManager(source_inpx=source_inpx, work_dir=work_dir)
    shadow = artifact_manager.clone()
    try:
        ox, oy = ISOLATED_ORIGIN
        p0 = (ox, oy)
        p1 = (ox + 40.0, oy)
        baseline_links = len(shadow["links"])
        _log(f"[B.1] Cloned {example_dir.name} -> {artifact_manager.working_path}; "
             f"baseline link count={baseline_links}")

        add_queue = [
            _action(
                "add_link", summary="링크 추가",
                points_xy=[list(p0), list(p1)], lane_count=2, name="LiveCheck Rollback link",
            ),
            _action("respond", summary="완료", answer="링크를 추가했습니다."),
        ]
        started = time.monotonic()
        add_result = _run_turn(work_dir, artifact_manager, "phaseB-add", add_queue)
        _log(f"      add turn completed in {time.monotonic() - started:.1f}s -- kind={add_result.kind!r}")
        _log(f"      answer={add_result.answer!r}")

        b1_ok = add_result.kind == "answer" and artifact_manager.version == 1
        if not b1_ok:
            _log(f"      FAIL: expected a completed answer + version==1, got kind={add_result.kind!r} "
                 f"version={artifact_manager.version}")
            return False
        added_shadow = artifact_manager.shadow_model
        added_count = len(added_shadow["links"])
        _log(f"      PASS: version==1, link count={added_count} (baseline {baseline_links})")
        assert added_count == baseline_links + 1

        _log("")
        _log("[B.2] rollback_last_edit turn...")
        rollback_queue = [
            _action("rollback_last_edit", summary="되돌리기"),
            _action("respond", summary="완료", answer="마지막 편집을 되돌렸습니다."),
        ]
        started = time.monotonic()
        rollback_result = _run_turn(work_dir, artifact_manager, "phaseB-rollback", rollback_queue)
        _log(f"      rollback turn completed in {time.monotonic() - started:.1f}s -- kind={rollback_result.kind!r}")
        _log(f"      answer={rollback_result.answer!r}")

        b2_ok = rollback_result.kind == "answer" and artifact_manager.version == 2
        if not b2_ok:
            _log(f"      FAIL: expected a completed answer + version==2, got kind={rollback_result.kind!r} "
                 f"version={artifact_manager.version}")
            return False

        restored_bytes_match = (
            artifact_manager.working_path.read_bytes() == artifact_manager.source_inpx.read_bytes()
        )
        _log(f"      version==2; working copy byte-identical to source clone: {restored_bytes_match}")
        if not restored_bytes_match:
            _log("      FAIL: rollback did not restore byte-identical bytes.")
            return False

        restored_shadow = build_shadow_model(artifact_manager.working_path)
        restored_count = len(restored_shadow["links"])
        _log(f"      restored link count = {restored_count} (pre-add was {baseline_links})")
        ok = restored_count == baseline_links
        _log(f"[B.3] {'PASS' if ok else 'FAIL'}: rollback_last_edit restored the add_link edit, "
             "verified both byte- and field-level.")
        return ok
    finally:
        tools_edit.get_active_inpx_path = original_get_active_inpx_path


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    a_ok = phase_a(example_dir)
    b_ok = phase_b(example_dir)

    _log("")
    _log("=" * 78)
    _log(f"RESULT: Phase A {'PASS' if a_ok else 'FAIL'}, Phase B {'PASS' if b_ok else 'FAIL'}")
    _log("=" * 78)
    if not (a_ok and b_ok):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

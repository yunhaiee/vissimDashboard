"""Q4 unit (2) LIVE END-TO-END CHECK: all 8 measurement-infrastructure
placement verbs (add/remove x queue counter, data-collection point,
data-collection measurement, travel-time section), against a REAL cloned
Examples Demo network -- no fake anywhere in this script. House shape =
``lane_closure_live_check.py`` (the newest v2b live check).

Three phases:

  [Phase A] Dispatch-level: a real VissimEditSession, all 4 ADD verbs via
  ``toolkit.dispatch`` (real COM Add* calls), readback-verify each, run the
  REAL COM load oracle (``tools_check._load_check``'s own underlying probe)
  against the now-mutated working copy, reparse from disk to confirm every
  object landed exactly as intended, then all 4 REMOVE verbs in dependency
  order (measurement before its own point -- the reference-integrity
  refusal is proven FIRST, deliberately, before the measurement is
  removed), run the load oracle AGAIN to confirm clean restoration, and
  reparse to confirm the network's queue_counters/travel_time_sections
  counts are back to their PRE-EXISTING baseline (Billings ships some of
  its own).

  [Phase B] Full orchestrator level: add_queue_counter through a real
  ``run_agent_turn`` (``PreapprovedWriteGate``, real ``ArtifactManager``,
  real ``GuardrailEngine``, canned planner -- no paid LLM calls), THEN
  ``rollback_last_edit`` through a second real turn -- proves "rollback
  after a measurement-infra add -> restored, verified" against real
  Vissim/COM artifacts, not just the synthetic pytest suite.

Usage: python tools/measurement_infra_live_check.py [example_dir]
Default example: Roundabout Billings.US (2026 Examples Demo) -- the SAME
network every other v2/v2a/v2b/Q4 live check already uses successfully
(FEET display -- exercises the display-unit landmine on every readback;
ships 5 queueCounters + 3 vehicleTravelTimeMeasurements of its own, giving
this script a genuine "did the baseline survive untouched" check).

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
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Roundabout Billings.US"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "measurement-infra-live-check"


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


def phase_a_dispatch_add_all_load_oracle_remove_all(example_dir: Path) -> bool:
    _log("=" * 78)
    _log("[Phase A] add_* x4 via toolkit.dispatch -> load oracle -> remove_* x4 -> load oracle")
    _log("=" * 78)

    from services.agent.edit_session import (
        VissimEditSession,
        clear_active_edit_session,
        set_active_edit_session,
    )
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    import services.agent.tools_edit  # noqa: F401 - registers the 8 verbs
    import services.agent.tools_check  # noqa: F401 - registers load_check
    from services.agent.toolkit import default_registry, dispatch

    work_dir = _fresh_work_dir(example_dir, "phaseA")
    artifact_manager = ArtifactManager(source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir)
    shadow = artifact_manager.clone()
    _log(f"[A.1] Cloned {example_dir.name} -> {artifact_manager.working_path}")

    baseline_qc = len(shadow["queue_counters"])
    baseline_dcp = len(shadow["data_collection_points"])
    baseline_dcm = len(shadow["data_collection_measurements"])
    baseline_tt = len(shadow["travel_time_sections"])
    _log(f"      Baseline counts: queue_counters={baseline_qc} data_collection_points={baseline_dcp} "
         f"data_collection_measurements={baseline_dcm} travel_time_sections={baseline_tt}")

    links_by_length = sorted(shadow["links"], key=lambda l: -(l.get("length") or 0))
    qc_link = links_by_length[0]
    dcp_link = next(l for l in links_by_length if l["lanes"] >= 1)
    tt_from_link, tt_to_link = links_by_length[0], links_by_length[1]
    _log(f"      Target links: queue_counter->{qc_link['id']} (len {qc_link['length']}), "
         f"data_collection_point->{dcp_link['id']} lane 1 (len {dcp_link['length']}), "
         f"travel_time_section {tt_from_link['id']}->{tt_to_link['id']}")

    ok = False
    session = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session)

        _log("\n[A.2] add_queue_counter...")
        qc_pos = round(min(20.0, qc_link["length"] / 2), 2)
        qc_env = dispatch(
            "add_queue_counter", {"link_no": qc_link["id"], "pos_m": qc_pos, "name": "LiveCheck QC"},
            registry=default_registry,
        )
        _log(f"      envelope = {qc_env}")
        assert qc_env["status"] == "ok", qc_env
        qc_no = qc_env["artifacts"]["no"]
        _log(f"      PASS: queue counter {qc_no} added.")

        _log("\n[A.3] add_data_collection_point...")
        dcp_pos = round(min(10.0, dcp_link["length"] / 2), 2)
        dcp_env = dispatch(
            "add_data_collection_point",
            {"link_no": dcp_link["id"], "lane_index": 1, "pos_m": dcp_pos, "name": "LiveCheck DCP"},
            registry=default_registry,
        )
        _log(f"      envelope = {dcp_env}")
        assert dcp_env["status"] == "ok", dcp_env
        dcp_no = dcp_env["artifacts"]["no"]
        _log(f"      PASS: data collection point {dcp_no} added.")

        _log("\n[A.4] add_data_collection_measurement (grouping the point just added)...")
        dcm_env = dispatch(
            "add_data_collection_measurement",
            {"point_nos": [str(dcp_no)], "name": "LiveCheck DCM"},
            registry=default_registry,
        )
        _log(f"      envelope = {dcm_env}")
        assert dcm_env["status"] == "ok", dcm_env
        dcm_no = dcm_env["artifacts"]["no"]
        _log(f"      PASS: data collection measurement {dcm_no} added, grouping point {dcp_no}.")

        _log("\n[A.5] add_travel_time_section...")
        tt_env = dispatch(
            "add_travel_time_section",
            {
                "name": "LiveCheck TT",
                "from_link": tt_from_link["id"],
                "from_pos_m": 0.0,
                "to_link": tt_to_link["id"],
                "to_pos_m": round(min(10.0, tt_to_link["length"] / 2), 2),
            },
            registry=default_registry,
        )
        _log(f"      envelope = {tt_env}")
        assert tt_env["status"] == "ok", tt_env
        tt_no = tt_env["artifacts"]["no"]
        _log(f"      PASS: travel time section {tt_no} added.")

        after_add_shadow = build_shadow_model(artifact_manager.working_path)
        _log(f"\n[A.6] Reparse from disk after all 4 adds: "
             f"queue_counters={len(after_add_shadow['queue_counters'])} "
             f"data_collection_points={len(after_add_shadow['data_collection_points'])} "
             f"data_collection_measurements={len(after_add_shadow['data_collection_measurements'])} "
             f"travel_time_sections={len(after_add_shadow['travel_time_sections'])}")
        assert len(after_add_shadow["queue_counters"]) == baseline_qc + 1
        assert len(after_add_shadow["data_collection_points"]) == baseline_dcp + 1
        assert len(after_add_shadow["data_collection_measurements"]) == baseline_dcm + 1
        assert len(after_add_shadow["travel_time_sections"]) == baseline_tt + 1
        added_qc = next(q for q in after_add_shadow["queue_counters"] if q["no"] == str(qc_no))
        added_dcp = next(p for p in after_add_shadow["data_collection_points"] if p["no"] == str(dcp_no))
        added_dcm = next(m for m in after_add_shadow["data_collection_measurements"] if m["no"] == str(dcm_no))
        added_tt = next(t for t in after_add_shadow["travel_time_sections"] if t["no"] == str(tt_no))
        _log(f"      queue counter {qc_no}: link={added_qc['link_id']} pos={added_qc['pos']} name={added_qc['name']!r}")
        _log(f"      data collection point {dcp_no}: link={added_dcp['link_id']} lane={added_dcp['lane_index']} "
             f"pos={added_dcp['pos']} name={added_dcp['name']!r}")
        _log(f"      data collection measurement {dcm_no}: point_nos={added_dcm['point_nos']} name={added_dcm['name']!r}")
        _log(f"      travel time section {tt_no}: from={added_tt['from_link']}@{added_tt['from_pos']} "
             f"to={added_tt['to_link']}@{added_tt['to_pos']} name={added_tt['name']!r}")
        assert added_qc["link_id"] == qc_link["id"]
        assert added_dcp["link_id"] == dcp_link["id"] and added_dcp["lane_index"] == 1
        assert added_dcm["point_nos"] == [str(dcp_no)]
        assert added_tt["from_link"] == tt_from_link["id"] and added_tt["to_link"] == tt_to_link["id"]
        _log("      PASS: all 4 new objects landed on disk with the exact intended attributes.")

        _log("\n[A.7] Reference-integrity REFUSAL proof: remove_data_collection_point while the "
             "measurement still references it...")
        blocked_env = dispatch(
            "remove_data_collection_point", {"no": str(dcp_no)}, registry=default_registry
        )
        _log(f"      envelope = {blocked_env}")
        assert blocked_env["status"] == "error" and blocked_env["error_type"] == "tool_execution_error"
        assert "still referenced by" in blocked_env["message"]
        _log("      PASS: refused, naming the referencing measurement -- COM itself would NOT have refused this "
             "(see probe_measurement_infra_followup.py's own live finding).")

        session.close()
        _log("\n[A.8] Edit session closed before running the COM load oracle (its own dedicated instance).")
    finally:
        clear_active_edit_session()

    _log("\n[A.9] Running the COM load oracle (load_check's own underlying probe) on the "
         "4-object-added working copy...")
    from services.agent.tools_check import _load_check

    load_result = _load_check(artifact_manager.working_path)
    _log(f"      load_check_result = {load_result}")
    assert load_result["load_ok"] is True and load_result["init_ok"] is True, (
        f"expected the mutated network to load+init cleanly, got {load_result}"
    )
    _log("      PASS: LoadNet + one RunSingleStep both succeeded with all 4 new objects present.")

    _log("\n[A.10] Removing all 4 in dependency order (measurement first, freeing its point) via a "
         "fresh VissimEditSession...")
    session2 = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session2)

        dcm_remove_env = dispatch(
            "remove_data_collection_measurement", {"no": str(dcm_no)}, registry=default_registry
        )
        _log(f"      remove_data_collection_measurement({dcm_no}) -> {dcm_remove_env['status']}")
        assert dcm_remove_env["status"] == "ok", dcm_remove_env

        dcp_remove_env = dispatch(
            "remove_data_collection_point", {"no": str(dcp_no)}, registry=default_registry
        )
        _log(f"      remove_data_collection_point({dcp_no}) -> {dcp_remove_env['status']} "
             "(now allowed -- the measurement is gone)")
        assert dcp_remove_env["status"] == "ok", dcp_remove_env

        qc_remove_env = dispatch("remove_queue_counter", {"no": str(qc_no)}, registry=default_registry)
        _log(f"      remove_queue_counter({qc_no}) -> {qc_remove_env['status']}")
        assert qc_remove_env["status"] == "ok", qc_remove_env

        tt_remove_env = dispatch(
            "remove_travel_time_section", {"no": str(tt_no)}, registry=default_registry
        )
        _log(f"      remove_travel_time_section({tt_no}) -> {tt_remove_env['status']}")
        assert tt_remove_env["status"] == "ok", tt_remove_env

        restored_shadow = build_shadow_model(artifact_manager.working_path)
        _log(f"\n[A.11] Reparse from disk after all 4 removes: "
             f"queue_counters={len(restored_shadow['queue_counters'])} (baseline {baseline_qc}) "
             f"data_collection_points={len(restored_shadow['data_collection_points'])} (baseline {baseline_dcp}) "
             f"data_collection_measurements={len(restored_shadow['data_collection_measurements'])} (baseline {baseline_dcm}) "
             f"travel_time_sections={len(restored_shadow['travel_time_sections'])} (baseline {baseline_tt})")
        counts_restored = (
            len(restored_shadow["queue_counters"]) == baseline_qc
            and len(restored_shadow["data_collection_points"]) == baseline_dcp
            and len(restored_shadow["data_collection_measurements"]) == baseline_dcm
            and len(restored_shadow["travel_time_sections"]) == baseline_tt
        )
        assert counts_restored, "counts did not return to baseline after removal"
        _log("      PASS: every count back to its pre-edit baseline -- Billings' own pre-existing "
             "queue counters/travel-time sections survived untouched.")

        session2.close()
    finally:
        clear_active_edit_session()

    _log("\n[A.12] Running the COM load oracle AGAIN on the fully-restored working copy...")
    load_result_2 = _load_check(artifact_manager.working_path)
    _log(f"      load_check_result = {load_result_2}")
    assert load_result_2["load_ok"] is True and load_result_2["init_ok"] is True
    _log("      PASS: clean restoration confirmed by the REAL load oracle a second time.")
    ok = True
    return ok


def phase_b_full_orchestrator_add_then_rollback(example_dir: Path) -> bool:
    _log("")
    _log("=" * 78)
    _log("[Phase B] Full run_agent_turn: add_queue_counter (approved) -> rollback_last_edit (approved)")
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
        target_link = max(shadow["links"], key=lambda l: (l.get("length") or 0))
        pos = round(min(15.0, target_link["length"] / 2), 2)
        _log(f"[B.1] Cloned {example_dir.name} -> {artifact_manager.working_path}; "
             f"target link {target_link['id']} (len {target_link['length']})")

        add_queue = [
            _action(
                "add_queue_counter", summary="대기행렬 카운터 추가",
                link_no=target_link["id"], pos_m=pos, name="LiveCheck Rollback QC",
            ),
            _action("respond", summary="완료", answer="대기행렬 카운터를 추가했습니다."),
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
        added_count = len(added_shadow["queue_counters"])
        _log(f"      PASS: version==1, queue_counters count={added_count}")

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
        restored_count = len(restored_shadow["queue_counters"])
        _log(f"      restored queue_counters count = {restored_count} (pre-add was {added_count - 1})")
        ok = restored_count == added_count - 1
        _log(f"[B.3] {'PASS' if ok else 'FAIL'}: rollback_last_edit restored the queue-counter add, "
             "verified both byte- and field-level.")
        return ok
    finally:
        tools_edit.get_active_inpx_path = original_get_active_inpx_path


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    a_ok = phase_a_dispatch_add_all_load_oracle_remove_all(example_dir)
    b_ok = phase_b_full_orchestrator_add_then_rollback(example_dir)

    _log("")
    _log("=" * 78)
    _log(f"RESULT: Phase A {'PASS' if a_ok else 'FAIL'}, Phase B {'PASS' if b_ok else 'FAIL'}")
    _log("=" * 78)
    if not (a_ok and b_ok):
        raise SystemExit(1)


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()

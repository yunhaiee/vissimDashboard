"""Q4 unit (4) LIVE END-TO-END CHECK: add_signal_head/remove_signal_head +
add_detector/remove_detector, against a REAL cloned Examples Demo network --
no fake anywhere in this script. House shape =
``measurement_infra_live_check.py`` (the newest Q4 unit's own live check).

Two phases:

  [Phase A] Dispatch-level: a real VissimEditSession, add_signal_head wired
  to a REAL signal group on the network's own controller, add_detector with
  pos_m OMITTED (proving the MRWA stop-line-proxy default against the head
  just added), readback-verify both, run the REAL COM load oracle against
  the mutated working copy, prove the NEW reference-integrity refusal this
  unit's own inpx_structure.py extension enables (remove_last_lane on the
  lane the new signal head/detector now occupy -- refused, naming both),
  then remove_detector/remove_signal_head (same-claim verification), run the
  load oracle again to confirm clean restoration, and reparse to confirm
  signal_heads/detectors counts are back to their pre-edit baseline.

  [Phase B] Full orchestrator level: add_signal_head through a real
  ``run_agent_turn`` (PreapprovedWriteGate, real ArtifactManager, real
  GuardrailEngine, canned planner -- no paid LLM calls), THEN
  rollback_last_edit through a second real turn.

Usage: python tools/signal_heads_detectors_live_check.py [example_dir]
Default example: Urban Intersection Beijing.CN (2026 Examples Demo) -- the
task brief's own named live-check network (4-arm signalized, 1 FIXEDTIME
controller, 22 signal groups, 52 real signal heads, ZERO detectors).

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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "signal-heads-detectors-live-check"


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


def phase_a_dispatch_add_load_oracle_refusal_remove(example_dir: Path) -> bool:
    _log("=" * 78)
    _log("[Phase A] add_signal_head + add_detector(default pos) via toolkit.dispatch -> "
         "load oracle -> lane reference-integrity REFUSAL -> remove both -> load oracle")
    _log("=" * 78)

    from services.agent.edit_session import (
        VissimEditSession,
        clear_active_edit_session,
        set_active_edit_session,
    )
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    import services.agent.tools_edit  # noqa: F401 - registers the 4 verbs
    import services.agent.tools_check  # noqa: F401 - registers load_check
    from services import inpx_structure as struct
    from services.agent.toolkit import default_registry, dispatch

    work_dir = _fresh_work_dir(example_dir, "phaseA")
    artifact_manager = ArtifactManager(source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir)
    shadow = artifact_manager.clone()
    _log(f"[A.1] Cloned {example_dir.name} -> {artifact_manager.working_path}")

    baseline_sh = len(shadow["signal_heads"])
    baseline_det = len(shadow["detectors"])
    _log(f"      Baseline counts: signal_heads={baseline_sh} detectors={baseline_det}")

    signal_no = shadow["signals"][0]["sc_no"]
    # Pick a real signal group this controller actually has (from a real,
    # existing signal head -- guarantees a genuinely wired sg_no rather than
    # a guessed number).
    existing_head = shadow["signal_heads"][0]
    sg_no = existing_head["sg"]
    # A fresh LAST lane for the new head/detector pair, on a link with >=2
    # lanes: remove_last_lane (A.7 below) always targets the link's own
    # HIGHEST-index lane, so the reference-integrity proof needs the new
    # head/detector to sit on THAT exact lane, not lane 1 -- and a 1-lane
    # link's only lane is refused for a DIFFERENT reason first ("a link
    # cannot have zero lanes"), which would never reach the reference check
    # at all.
    used_lanes = {(h["link"], h["lane_index"]) for h in shadow["signal_heads"]}
    used_lanes |= {(d["link"], d["lane_index"]) for d in shadow["detectors"]}
    target_link = next(
        l for l in shadow["links"]
        if not l.get("is_connector")
        and l.get("lanes", 0) >= 2
        and (l["id"], l["lanes"]) not in used_lanes
        and (l.get("length") or 0) > 30
    )
    last_lane = target_link["lanes"]
    _log(f"      Target: SC {signal_no}, SG {sg_no}, link {target_link['id']} "
         f"(length {target_link['length']}, {last_lane} lanes) lane {last_lane} (its LAST lane)")

    ok = False
    session = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session)

        head_pos = round(min(20.0, target_link["length"] / 2), 3)
        _log(f"\n[A.2] add_signal_head(link={target_link['id']}, lane={last_lane}, pos={head_pos}, "
             f"sc_no={signal_no}, sg_no={sg_no})...")
        sh_env = dispatch(
            "add_signal_head",
            {
                "link_no": target_link["id"], "lane_index": last_lane, "pos_m": head_pos,
                "sc_no": str(signal_no), "sg_no": str(sg_no), "name": "LiveCheck SH",
            },
            registry=default_registry,
        )
        _log(f"      envelope = {sh_env}")
        assert sh_env["status"] == "ok", sh_env
        sh_no = sh_env["artifacts"]["no"]
        _log(f"      PASS: signal head {sh_no} added.")

        _log("\n[A.3] add_detector(link={}, lane={}) -- pos_m OMITTED, proving the MRWA "
             "stop-line-proxy default against the head just added...".format(
                 target_link["id"], last_lane
             ))
        det_env = dispatch(
            "add_detector",
            {"link_no": target_link["id"], "lane_index": last_lane, "name": "LiveCheck DET"},
            registry=default_registry,
        )
        _log(f"      envelope = {det_env}")
        assert det_env["status"] == "ok", det_env
        det_no = det_env["artifacts"]["no"]
        expected_pos = round(head_pos - 2.0, 3)
        _log(f"      PASS: detector {det_no} added, message={det_env['message']!r}")
        assert abs(det_env["verification"]["written_value"]["pos"] - expected_pos) < 1e-6, (
            det_env["verification"]["written_value"]["pos"], expected_pos
        )
        _log(f"      PASS: default pos == signal head pos ({head_pos}) - 2.0 m MRWA setback "
             f"== {expected_pos} m, exactly.")

        after_add_shadow = build_shadow_model(artifact_manager.working_path)
        _log(f"\n[A.4] Reparse from disk after both adds: "
             f"signal_heads={len(after_add_shadow['signal_heads'])} "
             f"detectors={len(after_add_shadow['detectors'])}")
        assert len(after_add_shadow["signal_heads"]) == baseline_sh + 1
        assert len(after_add_shadow["detectors"]) == baseline_det + 1
        added_sh = next(h for h in after_add_shadow["signal_heads"] if h["no"] == str(sh_no))
        added_det = next(d for d in after_add_shadow["detectors"] if d["no"] == str(det_no))
        _log(f"      signal head {sh_no}: link={added_sh['link']} lane={added_sh['lane_index']} "
             f"pos={added_sh['pos']} sc={added_sh['sc']} sg={added_sh['sg']}")
        _log(f"      detector {det_no}: link={added_det['link']} lane={added_det['lane_index']} "
             f"pos={added_det['pos']} length={added_det['length']}")
        assert added_sh["link"] == target_link["id"] and added_sh["lane_index"] == last_lane
        assert added_sh["sc"] == str(signal_no) and added_sh["sg"] == str(sg_no)
        assert added_det["link"] == target_link["id"] and added_det["lane_index"] == last_lane
        assert added_det["length"] == 4.5
        _log("      PASS: both new objects landed on disk with the exact intended attributes.")

        session.close()
        _log("\n[A.5] Edit session closed before running the COM load oracle (its own dedicated instance).")
    finally:
        clear_active_edit_session()

    _log("\n[A.6] Running the COM load oracle on the 2-object-added working copy...")
    from services.agent.tools_check import _load_check

    load_result = _load_check(artifact_manager.working_path)
    _log(f"      load_check_result = {load_result}")
    assert load_result["load_ok"] is True and load_result["init_ok"] is True, (
        f"expected the mutated network to load+init cleanly, got {load_result}"
    )
    _log("      PASS: LoadNet + one RunSingleStep both succeeded with both new objects present.")

    _log("\n[A.7] NEW reference-integrity REFUSAL proof (this unit's own "
         "LANE_TOKEN_REFERENCE_SITES extension): remove_last_lane on the lane the "
         "signal head/detector just added now occupy...")
    try:
        struct.remove_last_lane(artifact_manager.working_path, target_link["id"], run_load_check=False)
        _log("      FAIL: remove_last_lane did NOT refuse -- expected ReferenceIntegrityError.")
        return False
    except struct.ReferenceIntegrityError as exc:
        sites = sorted({r["site"] for r in exc.referrers})
        _log(f"      REFUSED, naming referrers: {exc.referrers}")
        # On a real network, the target lane may ALSO be referenced by other,
        # pre-existing sites (here: a connector's own fromLanes -- Beijing's
        # real geometry) -- the claim under test is only that the NEW signal
        # head/detector are BOTH named among them, not that they are the
        # ONLY referrers.
        assert "signal head" in sites and "detector" in sites, sites
        _log("      PASS: refused, naming BOTH the new signal head and the new detector "
             f"(plus {len(sites) - 2} other real, pre-existing referrer(s) on this lane) -- "
             "Vissim's own LoadNet would NOT have caught this (untested here, but matches "
             "the connector-removal precedent's own live finding: Vissim silently drops "
             "dangling references rather than refusing them).")

    _log("\n[A.8] Removing both in a fresh VissimEditSession (detector then signal head)...")
    session2 = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session2)

        det_remove_env = dispatch("remove_detector", {"no": str(det_no)}, registry=default_registry)
        _log(f"      remove_detector({det_no}) -> {det_remove_env['status']}")
        assert det_remove_env["status"] == "ok", det_remove_env
        assert det_remove_env["verification"] == {
            "written_value": {"removed_no": str(det_no), "still_present": False},
            "readback_value": {"removed_no": str(det_no), "still_present": False},
        }

        sh_remove_env = dispatch("remove_signal_head", {"no": str(sh_no)}, registry=default_registry)
        _log(f"      remove_signal_head({sh_no}) -> {sh_remove_env['status']}")
        assert sh_remove_env["status"] == "ok", sh_remove_env
        assert sh_remove_env["verification"] == {
            "written_value": {"removed_no": str(sh_no), "still_present": False},
            "readback_value": {"removed_no": str(sh_no), "still_present": False},
        }
        _log("      PASS: both same-claim verifications agree on genuine success "
             "(written_value == readback_value, the Q4 unit (3) _remove_verification pattern).")

        restored_shadow = build_shadow_model(artifact_manager.working_path)
        _log(f"\n[A.9] Reparse from disk after both removes: "
             f"signal_heads={len(restored_shadow['signal_heads'])} (baseline {baseline_sh}) "
             f"detectors={len(restored_shadow['detectors'])} (baseline {baseline_det})")
        counts_restored = (
            len(restored_shadow["signal_heads"]) == baseline_sh
            and len(restored_shadow["detectors"]) == baseline_det
        )
        assert counts_restored, "counts did not return to baseline after removal"
        _log("      PASS: both counts back to their pre-edit baseline.")

        session2.close()
    finally:
        clear_active_edit_session()

    _log("\n[A.10] Running the COM load oracle AGAIN on the fully-restored working copy...")
    load_result_2 = _load_check(artifact_manager.working_path)
    _log(f"      load_check_result = {load_result_2}")
    assert load_result_2["load_ok"] is True and load_result_2["init_ok"] is True
    _log("      PASS: clean restoration confirmed by the REAL load oracle a second time.")
    ok = True
    return ok


def phase_b_full_orchestrator_add_then_rollback(example_dir: Path) -> bool:
    _log("")
    _log("=" * 78)
    _log("[Phase B] Full run_agent_turn: add_signal_head (approved) -> rollback_last_edit (approved)")
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
        signal_no = shadow["signals"][0]["sc_no"]
        existing_head = shadow["signal_heads"][0]
        sg_no = existing_head["sg"]
        used_lanes = {(h["link"], h["lane_index"]) for h in shadow["signal_heads"]}
        target_link = next(
            l for l in shadow["links"]
            if not l.get("is_connector") and (l["id"], 1) not in used_lanes and (l.get("length") or 0) > 30
        )
        pos = round(min(15.0, target_link["length"] / 2), 3)
        _log(f"[B.1] Cloned {example_dir.name} -> {artifact_manager.working_path}; "
             f"target link {target_link['id']} (len {target_link['length']}), SC {signal_no} SG {sg_no}")

        add_queue = [
            _action(
                "add_signal_head", summary="신호 헤드 추가",
                link_no=target_link["id"], lane_index=1, pos_m=pos,
                sc_no=str(signal_no), sg_no=str(sg_no), name="LiveCheck Rollback SH",
            ),
            _action("respond", summary="완료", answer="신호 헤드를 추가했습니다."),
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
        added_count = len(added_shadow["signal_heads"])
        _log(f"      PASS: version==1, signal_heads count={added_count}")

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
        restored_count = len(restored_shadow["signal_heads"])
        _log(f"      restored signal_heads count = {restored_count} (pre-add was {added_count - 1})")
        ok = restored_count == added_count - 1
        _log(f"[B.3] {'PASS' if ok else 'FAIL'}: rollback_last_edit restored the signal-head add, "
             "verified both byte- and field-level.")
        return ok
    finally:
        tools_edit.get_active_inpx_path = original_get_active_inpx_path


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    a_ok = phase_a_dispatch_add_load_oracle_refusal_remove(example_dir)
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

"""v2b unit (1) LIVE END-TO-END CHECK: the shipped attribute-level
``set_lane_closure`` verb (COM ``BlockedVehClasses``) AND the spine's own
genuinely-structural proof pair (``remove_last_lane``/``add_lane``),
against a REAL cloned Examples Demo network -- not a fake anywhere in this
script.

Three phases:

  [Phase A] Dispatch-level: a real VissimEditSession, the REGISTERED
  ``set_lane_closure`` tool via ``toolkit.dispatch`` -- close lane 1 of
  link 1 to vehicle classes [10, 20], readback-verify, run the COM load
  oracle (``load_check``'s own underlying probe) against the now-closed
  working copy, REOPEN (``vehicle_classes=[]``), readback-verify, and
  confirm semantic restoration by reparsing the working copy fresh from
  disk.

  [Phase B] Full orchestrator level: the SAME close, through a real
  ``run_agent_turn`` (``PreapprovedWriteGate``, real ``ArtifactManager``,
  real ``GuardrailEngine``, canned planner -- no paid LLM calls), THEN
  ``rollback_last_edit`` through a second real turn -- proves "rollback
  after a closure -> restored, verified" against real Vissim/COM artifacts,
  not just the synthetic pytest suite.

  [Phase C] The spine's own structural proof pair, run_load_check=True for
  real (component 4, "the COM load oracle as structural postwrite"): remove
  the last lane of an unreferenced link, confirm the REAL load oracle
  passes, then add it back.

Usage: python tools/lane_closure_live_check.py [example_dir]
Default example: Roundabout Billings.US (2026 Examples Demo) -- confirmed
live by probe_lane_closure.py to have simple 2-lane links and no
reducedSpeedAreas/priorityRules that could collide with this script's own
edits.

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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "lane-closure-live-check"

#: Phase C needs a link whose LAST lane is referenced by NOTHING at all --
#: verified statically (no COM) that Roundabout Billings.US (Phase A/B's
#: network) has ZERO such links among its 29 (every lane in this compact
#: roundabout feeds a connector); Urban Intersection Beijing.CN has 4 (e.g.
#: link 4, 4 lanes) among its 112. Phase C uses this network specifically;
#: Phase A/B keep the CLI-overridable Roundabout Billings.US default.
PHASE_C_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)


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


def phase_a_dispatch_close_load_oracle_reopen(example_dir: Path) -> bool:
    _log("=" * 78)
    _log("[Phase A] set_lane_closure via toolkit.dispatch -> load_check oracle -> reopen")
    _log("=" * 78)

    from services.agent.edit_session import (
        VissimEditSession,
        clear_active_edit_session,
        set_active_edit_session,
    )
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    import services.agent.tools_edit  # noqa: F401 - registers set_lane_closure
    import services.agent.tools_check  # noqa: F401 - registers load_check
    from services.agent.toolkit import default_registry, dispatch

    work_dir = _fresh_work_dir(example_dir, "phaseA")
    artifact_manager = ArtifactManager(source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir)
    shadow = artifact_manager.clone()
    link_id = shadow["link_lanes"][0]["link_id"]
    lane_index = shadow["link_lanes"][0]["lane_index"]
    before_blocked = shadow["link_lanes"][0]["blocked_vehicle_classes"]
    _log(f"[A.1] Cloned {example_dir.name} -> {artifact_manager.working_path}")
    _log(f"      Target lane: link {link_id} lane {lane_index}, blocked_vehicle_classes before = {before_blocked!r}")

    ok = False
    session = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session)

        _log(f"[A.2] Dispatching set_lane_closure(link_id={link_id!r}, lane_index={lane_index}, vehicle_classes=['10','20'])...")
        started = time.monotonic()
        close_envelope = dispatch(
            "set_lane_closure",
            {"link_id": link_id, "lane_index": lane_index, "vehicle_classes": ["10", "20"]},
            registry=default_registry,
        )
        _log(f"      dispatch() returned in {time.monotonic() - started:.1f}s")
        _log(f"      envelope = {close_envelope}")
        assert close_envelope["status"] == "ok", f"expected status ok, got {close_envelope}"
        assert close_envelope["verification"] == {"written_value": ["10", "20"], "readback_value": ["10", "20"]}
        _log("[A.3] PASS: close envelope ok, written == readback == ['10', '20'].")

        after_close_shadow = build_shadow_model(artifact_manager.working_path)
        closed_entry = next(
            e for e in after_close_shadow["link_lanes"] if e["link_id"] == link_id and e["lane_index"] == lane_index
        )
        _log(f"[A.4] Reparse from disk: blocked_vehicle_classes = {closed_entry['blocked_vehicle_classes']!r}")
        assert closed_entry["blocked_vehicle_classes"] == ["10", "20"]
        _log("      PASS: the closure genuinely reached the saved file (not just Vissim's in-memory state).")

        session.close()
        _log("[A.5] Edit session closed before running the COM load oracle (its own dedicated instance).")
    finally:
        clear_active_edit_session()

    _log("[A.6] Running the COM load oracle (load_check's own underlying probe) on the CLOSED working copy...")
    from services.agent.tools_check import _load_check

    load_result = _load_check(artifact_manager.working_path)
    _log(f"      load_check_result = {load_result}")
    assert load_result["load_ok"] is True and load_result["init_ok"] is True, (
        f"expected the closed network to load+init cleanly, got {load_result}"
    )
    _log("      PASS: LoadNet + one RunSingleStep both succeeded on the lane-closed network.")

    _log("[A.7] Reopening (vehicle_classes=[]) via a fresh VissimEditSession on the same working copy...")
    session2 = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session2)
        reopen_envelope = dispatch(
            "set_lane_closure",
            {"link_id": link_id, "lane_index": lane_index, "vehicle_classes": []},
            registry=default_registry,
        )
        _log(f"      envelope = {reopen_envelope}")
        assert reopen_envelope["status"] == "ok"
        assert reopen_envelope["verification"] == {"written_value": [], "readback_value": []}
        _log("[A.8] PASS: reopen envelope ok, written == readback == [].")

        restored_shadow = build_shadow_model(artifact_manager.working_path)
        restored_entry = next(
            e for e in restored_shadow["link_lanes"] if e["link_id"] == link_id and e["lane_index"] == lane_index
        )
        _log(f"[A.9] Reparse from disk after reopen: blocked_vehicle_classes = {restored_entry['blocked_vehicle_classes']!r}")
        assert restored_entry["blocked_vehicle_classes"] == before_blocked == []
        _log("      PASS: SEMANTIC restoration confirmed -- the reopened lane matches its pre-closure state exactly.")

        from services.agent.guardrails import diff_shadow_models

        pristine_shadow = ArtifactManager(
            source_inpx=artifact_manager.source_inpx, work_dir=_fresh_work_dir(example_dir, "phaseA-pristine")
        ).clone()
        diff = diff_shadow_models(pristine_shadow, restored_shadow)
        lane_diff = [d for d in diff if d["object_class"] == "link_lanes"]
        _log(f"[A.10] Full nothing-else-moved diff (pristine clone vs. close-then-reopen), link_lanes only: {lane_diff}")
        assert lane_diff == [], f"expected zero link_lanes diff evidence after reopen, got {lane_diff}"
        _log("       PASS: zero link_lanes evidence -- close-then-reopen is indistinguishable from never having edited.")
        ok = True
    finally:
        clear_active_edit_session()
        session2.close()
        _log("[A.11] Session closed.")

    return ok


def _run_turn(
    example_dir: Path, suffix: str, provider_queue, *, work_dir: Path | None = None, artifact_manager=None
):
    from services.agent.edit_session import VissimEditSession, clear_active_edit_session, set_active_edit_session
    from services.agent.gates import PreapprovedWriteGate, signature
    from services.agent.guardrails import GuardrailEngine
    from services.agent.orchestrator import run_agent_turn
    from services.agent.state import ArtifactManager

    class _CannedProvider:
        def __init__(self, queue):
            self._queue = list(queue)

        def generate_structured(self, messages, tools, schema=None, *, system=None, max_tokens=None):
            return self._queue.pop(0)

    if work_dir is None:
        work_dir = _fresh_work_dir(example_dir, suffix)
    if artifact_manager is None:
        artifact_manager = ArtifactManager(source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir)
        artifact_manager.clone()

    # planner.py pops "summary" out of an action's params BEFORE the
    # orchestrator ever calls gate.authorize() (it becomes decision.summary,
    # a separate field) -- the signature this gate checks against must be
    # computed the SAME summary-free way, or it can never match what the
    # orchestrator actually authorizes.
    signatures = {
        signature(action["tool"], {k: v for k, v in action["params"].items() if k != "summary"})
        for action, _ in provider_queue
        if action
    }
    gate = PreapprovedWriteGate(signatures)
    engine = GuardrailEngine(disabled=False)
    provider = _CannedProvider(provider_queue)

    session = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session)
        result = run_agent_turn(
            "테스트",
            provider=provider,
            work_dir=work_dir,
            session_id=f"live-check-{suffix}",
            language="ko",
            gate=gate,
            engine=engine,
            artifact_manager=artifact_manager,
        )
        return result, artifact_manager
    finally:
        clear_active_edit_session()
        session.close()


def phase_b_full_orchestrator_close_then_rollback(example_dir: Path) -> bool:
    _log("")
    _log("=" * 78)
    _log("[Phase B] Full run_agent_turn: close (approved) -> rollback_last_edit (approved)")
    _log("=" * 78)

    import services.agent.tools_edit as tools_edit
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model

    def _action(tool_name: str, *, summary: str, **params: Any):
        params["summary"] = summary
        return {"tool": tool_name, "params": params}, {"mode": "canned", "fallback_rung": 0}

    work_dir = _fresh_work_dir(example_dir, "phaseB")
    source_inpx = next(example_dir.glob("*.inpx"))
    # rollback_last_edit's target_version==0 case restores from
    # tools_edit.get_active_inpx_path() (mirrors production's own
    # ArtifactManager(source_inpx=get_active_inpx_path(), ...) construction,
    # where the two are therefore always identical by construction -- see
    # rollback.py's own module docstring). This script's ArtifactManager is
    # NOT built from that call (it points straight at the cloned example),
    # so it must be patched to agree, or rollback would restore from
    # whatever dataset happens to be active on this dev box instead of the
    # network this script actually cloned from.
    original_get_active_inpx_path = tools_edit.get_active_inpx_path
    tools_edit.get_active_inpx_path = lambda: source_inpx
    artifact_manager = ArtifactManager(source_inpx=source_inpx, work_dir=work_dir)
    shadow = artifact_manager.clone()
    try:
        link_id = shadow["link_lanes"][0]["link_id"]
        lane_index = shadow["link_lanes"][0]["lane_index"]
        _log(f"[B.1] Cloned {example_dir.name} -> {artifact_manager.working_path}; target link {link_id} lane {lane_index}")

        close_queue = [
            _action("set_lane_closure", summary="차로 폐쇄", link_id=link_id, lane_index=lane_index, vehicle_classes=["20"]),
            _action("respond", summary="완료", answer=f"링크 {link_id} 차로 {lane_index}를 폐쇄했습니다."),
        ]
        started = time.monotonic()
        close_result, artifact_manager = _run_turn(
            example_dir, "phaseB-close", close_queue, work_dir=work_dir, artifact_manager=artifact_manager
        )
        _log(f"      close turn completed in {time.monotonic() - started:.1f}s -- kind={close_result.kind!r}")
        _log(f"      answer={close_result.answer!r}")

        b1_ok = close_result.kind == "answer" and artifact_manager.version == 1
        if not b1_ok:
            _log(f"      FAIL: expected a completed answer + version==1, got kind={close_result.kind!r} version={artifact_manager.version}")
            return False
        closed_shadow = artifact_manager.shadow_model
        closed_entry = next(
            e for e in closed_shadow["link_lanes"] if e["link_id"] == link_id and e["lane_index"] == lane_index
        )
        _log(f"      PASS: version==1, blocked_vehicle_classes={closed_entry['blocked_vehicle_classes']!r}")

        _log("")
        _log("[B.2] rollback_last_edit turn...")
        rollback_queue = [
            _action("rollback_last_edit", summary="되돌리기"),
            _action("respond", summary="완료", answer="마지막 편집을 되돌렸습니다."),
        ]
        started = time.monotonic()
        rollback_result, artifact_manager = _run_turn(
            example_dir, "phaseB-rollback", rollback_queue, work_dir=work_dir, artifact_manager=artifact_manager
        )
        _log(f"      rollback turn completed in {time.monotonic() - started:.1f}s -- kind={rollback_result.kind!r}")
        _log(f"      answer={rollback_result.answer!r}")

        b2_ok = rollback_result.kind == "answer" and artifact_manager.version == 2
        if not b2_ok:
            _log(f"      FAIL: expected a completed answer + version==2, got kind={rollback_result.kind!r} version={artifact_manager.version}")
            return False

        restored_bytes_match = artifact_manager.working_path.read_bytes() == artifact_manager.source_inpx.read_bytes()
        _log(f"      version==2; working copy byte-identical to source clone: {restored_bytes_match}")
        if not restored_bytes_match:
            _log("      FAIL: rollback did not restore byte-identical bytes.")
            return False

        restored_shadow = build_shadow_model(artifact_manager.working_path)
        restored_entry = next(
            e for e in restored_shadow["link_lanes"] if e["link_id"] == link_id and e["lane_index"] == lane_index
        )
        _log(f"      restored blocked_vehicle_classes = {restored_entry['blocked_vehicle_classes']!r}")
        ok = restored_entry["blocked_vehicle_classes"] == []
        _log(f"[B.3] {'PASS' if ok else 'FAIL'}: rollback_last_edit restored the lane closure, verified both byte- and field-level.")
        return ok
    finally:
        tools_edit.get_active_inpx_path = original_get_active_inpx_path


def phase_c_structural_proof_live_load_oracle(example_dir: Path) -> bool:
    _log("")
    _log("=" * 78)
    _log("[Phase C] The spine's structural proof pair, REAL COM load oracle (run_load_check=True)")
    _log("=" * 78)

    from services import inpx_structure as struct
    from services.agent.state import ArtifactManager

    import xml.etree.ElementTree as ET

    work_dir = _fresh_work_dir(example_dir, "phaseC")
    artifact_manager = ArtifactManager(source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir)
    shadow = artifact_manager.clone()

    # Pick a link with >=2 lanes whose LAST lane specifically is not
    # referenced by any connector/RSA/priority-rule (checked via the
    # spine's own find_lane_token_references -- exactly the check
    # remove_last_lane runs internally) -- a small roundabout network
    # commonly attaches connectors to a link's outer (highest-index) lane,
    # so this is a genuine search, not a formality.
    root = ET.parse(artifact_manager.working_path).getroot()
    candidate_link = None
    for candidate in shadow["links"]:
        if candidate["lanes"] < 2 or candidate["is_connector"]:
            continue
        if not struct.find_lane_token_references(root, candidate["id"], candidate["lanes"]):
            candidate_link = candidate
            break
    assert candidate_link is not None, "no non-connector link with an unreferenced last lane found"
    link_id = candidate_link["id"]
    _log(f"[C.1] Cloned {example_dir.name} -> {artifact_manager.working_path}; target link {link_id} ({candidate_link['lanes']} lanes, last lane unreferenced)")

    started = time.monotonic()
    _log(f"[C.2] remove_last_lane({link_id!r}, run_load_check=True) -- launches a real Vissim instance for the oracle...")
    try:
        result = struct.remove_last_lane(artifact_manager.working_path, link_id, run_load_check=True)
    except struct.ReferenceIntegrityError as exc:
        _log(f"      Link {link_id} last lane IS referenced -- {exc}. FAIL.")
        return False
    _log(f"      completed in {time.monotonic() - started:.1f}s")
    _log(f"      metadata = {result['metadata']}")
    _log(f"      load_check_result = {result['load_check_result']}")
    assert result["load_check_result"]["load_ok"] and result["load_check_result"]["init_ok"]
    target_link = next(l for l in result["shadow_model"]["links"] if l["id"] == link_id)
    assert target_link["lanes"] == candidate_link["lanes"] - 1
    _log("[C.3] PASS: the REAL COM load oracle passed on the structurally-mutated network; lane count decremented.")

    _log(f"[C.4] add_lane({link_id!r}, width={result['metadata']['removed_width']}, run_load_check=True) -- restoring the lane count...")
    started = time.monotonic()
    add_result = struct.add_lane(
        artifact_manager.working_path, link_id, width=result["metadata"]["removed_width"], run_load_check=True
    )
    _log(f"      completed in {time.monotonic() - started:.1f}s")
    _log(f"      metadata = {add_result['metadata']}")
    _log(f"      load_check_result = {add_result['load_check_result']}")
    assert add_result["load_check_result"]["load_ok"] and add_result["load_check_result"]["init_ok"]
    restored_link = next(l for l in add_result["shadow_model"]["links"] if l["id"] == link_id)
    ok = restored_link["lanes"] == candidate_link["lanes"]
    _log(f"[C.5] {'PASS' if ok else 'FAIL'}: lane count restored to {restored_link['lanes']} (original {candidate_link['lanes']}); REAL load oracle passed both structural edits.")
    return ok


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    a_ok = phase_a_dispatch_close_load_oracle_reopen(example_dir)
    b_ok = phase_b_full_orchestrator_close_then_rollback(example_dir)
    c_ok = phase_c_structural_proof_live_load_oracle(PHASE_C_EXAMPLE)

    _log("")
    _log("=" * 78)
    _log(f"RESULT: Phase A {'PASS' if a_ok else 'FAIL'}, Phase B {'PASS' if b_ok else 'FAIL'}, Phase C {'PASS' if c_ok else 'FAIL'}")
    _log("=" * 78)
    if not (a_ok and b_ok and c_ok):
        raise SystemExit(1)


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()

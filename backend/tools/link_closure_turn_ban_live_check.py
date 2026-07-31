"""Q4 unit (3) LIVE END-TO-END CHECK: set_link_closure, set_turn_ban, and
remove_connector against REAL cloned Examples Demo networks -- not a fake
anywhere in this script. House shape = tools/lane_closure_live_check.py.

Phases (Roundabout Billings.US unless noted):

  [Phase A] set_link_closure via toolkit.dispatch on a PLAIN link that a
  real route traverses: the route-consequence WARN (constraints.py's
  link_closure_route_consequence) fires in preflight BEFORE the write is
  even attempted, naming the route; the write itself still lands (a warn
  never blocks); close -> readback -> the REAL COM load oracle -> reopen ->
  readback -> reparse-verified semantic restoration.

  [Phase B] set_turn_ban via toolkit.dispatch on a real connector (a turn):
  ban -> readback + reparse -> un-ban -> readback + reparse.

  [Phase C] remove_connector on a genuinely UNREFERENCED connector
  (Roundabout Buehl.DE -- Billings has none, live-verified by
  probe_link_closure_turn_bans.py), through a REAL run_agent_turn ->
  the REAL COM load oracle passes -> rollback_last_edit through a SECOND
  real turn -> byte-identical restoration.

  [Phase D] remove_connector on a connector STILL referenced by a conflict
  area (Billings connector 10002, referenced by conflictArea no=17 only) --
  REFUSES, naming the referrer, before any write.

Usage: python tools/link_closure_turn_ban_live_check.py [example_dir]
Default example: Roundabout Billings.US (2026 Examples Demo).

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
PHASE_C_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Roundabout Buehl.DE"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "link-closure-turn-ban-live-check"


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


def phase_a_link_closure_route_warn_oracle_reopen(example_dir: Path) -> bool:
    _log("=" * 78)
    _log("[Phase A] set_link_closure: route-consequence WARN -> close -> load oracle -> reopen")
    _log("=" * 78)

    from services.agent.edit_session import VissimEditSession, clear_active_edit_session, set_active_edit_session
    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    import services.agent.tools_edit  # noqa: F401 - registers set_link_closure
    import services.agent.tools_check  # noqa: F401 - registers load_check
    from services.agent.toolkit import default_registry, dispatch

    work_dir = _fresh_work_dir(example_dir, "phaseA")
    artifact_manager = ArtifactManager(source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir)
    shadow = artifact_manager.clone()

    # Find a PLAIN (non-connector) link that a real route traverses -- the
    # route-consequence warn's own target.
    connector_ids = {l["id"] for l in shadow["links"] if l.get("is_connector")}
    routed_link_id = None
    routing_route_id = None
    for route in shadow["routes"]:
        for link_id in route.get("link_seq") or []:
            if link_id not in connector_ids:
                routed_link_id = link_id
                routing_route_id = route["id"]
                break
        if routed_link_id:
            break
    assert routed_link_id is not None, "no plain link found in any route's link_seq"
    before_blocked = next(
        e["blocked_vehicle_classes"] for e in shadow["link_lanes"] if e["link_id"] == routed_link_id
    )
    _log(f"[A.1] Cloned {example_dir.name} -> {artifact_manager.working_path}")
    _log(f"      Target link {routed_link_id} is traversed by route {routing_route_id}; "
         f"blocked_vehicle_classes before = {before_blocked!r}")

    action = {
        "tool": "set_link_closure",
        "params": {"link_id": routed_link_id, "vehicle_classes": ["10"], "closed": True},
    }
    engine = GuardrailEngine(disabled=False)
    preflight = engine.preflight(action, shadow)
    warn_findings = [f for f in preflight.findings if f.constraint_id == "link_closure_route_consequence"]
    _log(f"[A.2] GuardrailEngine.preflight findings: "
         f"{[(f.constraint_id, f.tier) for f in preflight.findings]}")
    assert len(warn_findings) == 1, f"expected exactly one route-consequence warn, got {warn_findings}"
    assert routing_route_id in warn_findings[0].details["affected_routes"]
    assert preflight.blocking is False, "a warn-tier finding must never block"
    _log(f"      PASS: link_closure_route_consequence fired (warn, non-blocking), naming route "
         f"{routing_route_id}: {warn_findings[0].messages['en']}")

    ok = False
    session = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session)

        _log(f"[A.3] Dispatching set_link_closure(link_id={routed_link_id!r}, "
             f"vehicle_classes=['10'], closed=True)...")
        started = time.monotonic()
        close_envelope = dispatch(
            "set_link_closure",
            {"link_id": routed_link_id, "vehicle_classes": ["10"], "closed": True},
            registry=default_registry,
        )
        _log(f"      dispatch() returned in {time.monotonic() - started:.1f}s")
        _log(f"      envelope = {close_envelope}")
        assert close_envelope["status"] == "ok", f"expected status ok, got {close_envelope}"
        assert close_envelope["verification"]["readback_value"]["vehicle_classes"] == ["10"]
        _log("[A.4] PASS: close envelope ok, readback vehicle_classes == ['10'] on every lane.")

        after_close_shadow = build_shadow_model(artifact_manager.working_path)
        closed_lanes = [e for e in after_close_shadow["link_lanes"] if e["link_id"] == routed_link_id]
        _log(f"[A.5] Reparse from disk: {[(e['lane_index'], e['blocked_vehicle_classes']) for e in closed_lanes]}")
        assert all(e["blocked_vehicle_classes"] == ["10"] for e in closed_lanes)
        _log("      PASS: closure genuinely reached the saved file on every lane.")

        session.close()
        _log("[A.6] Edit session closed before running the COM load oracle (its own dedicated instance).")
    finally:
        clear_active_edit_session()

    _log("[A.7] Running the COM load oracle on the CLOSED working copy...")
    from services.agent.tools_check import _load_check

    load_result = _load_check(artifact_manager.working_path)
    _log(f"      load_check_result = {load_result}")
    assert load_result["load_ok"] is True and load_result["init_ok"] is True
    _log("      PASS: LoadNet + one RunSingleStep both succeeded on the closed network.")

    _log("[A.8] Reopening (vehicle_classes=[], closed=False) via a fresh VissimEditSession...")
    session2 = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session2)
        reopen_envelope = dispatch(
            "set_link_closure",
            {"link_id": routed_link_id, "vehicle_classes": [], "closed": False},
            registry=default_registry,
        )
        _log(f"      envelope = {reopen_envelope}")
        assert reopen_envelope["status"] == "ok"
        assert reopen_envelope["verification"]["readback_value"]["vehicle_classes"] == []
        _log("[A.9] PASS: reopen envelope ok, readback vehicle_classes == [] on every lane.")

        restored_shadow = build_shadow_model(artifact_manager.working_path)
        restored_lanes = [e for e in restored_shadow["link_lanes"] if e["link_id"] == routed_link_id]
        assert all(e["blocked_vehicle_classes"] == [] for e in restored_lanes)
        ok = True
        _log("[A.10] PASS: SEMANTIC restoration confirmed -- every lane reopened, matching pre-closure state.")
    finally:
        clear_active_edit_session()
        session2.close()

    return ok


def phase_b_turn_ban_readback_reparse_unban(example_dir: Path) -> bool:
    _log("")
    _log("=" * 78)
    _log("[Phase B] set_turn_ban: ban -> readback + reparse -> un-ban -> readback + reparse")
    _log("=" * 78)

    from services.agent.edit_session import VissimEditSession, clear_active_edit_session, set_active_edit_session
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    import services.agent.tools_edit  # noqa: F401
    from services.agent.toolkit import default_registry, dispatch

    work_dir = _fresh_work_dir(example_dir, "phaseB")
    artifact_manager = ArtifactManager(source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir)
    shadow = artifact_manager.clone()
    connector = next(l for l in shadow["links"] if l.get("is_connector"))
    connector_id = connector["id"]
    _log(f"[B.1] Cloned {example_dir.name} -> {artifact_manager.working_path}; target connector "
         f"{connector_id} ({connector['lanes']} lane(s))")

    ok = False
    session = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session)

        _log(f"[B.2] Dispatching set_turn_ban(connector_no={connector_id!r}, "
             f"vehicle_classes=['20'], banned=True)...")
        ban_envelope = dispatch(
            "set_turn_ban",
            {"connector_no": connector_id, "vehicle_classes": ["20"], "banned": True},
            registry=default_registry,
        )
        _log(f"      envelope = {ban_envelope}")
        assert ban_envelope["status"] == "ok"
        assert ban_envelope["verification"]["readback_value"]["vehicle_classes"] == ["20"]
        _log("[B.3] PASS: ban envelope ok, readback vehicle_classes == ['20'] on every lane.")

        banned_shadow = build_shadow_model(artifact_manager.working_path)
        banned_lanes = [e for e in banned_shadow["link_lanes"] if e["link_id"] == connector_id]
        _log(f"[B.4] Reparse from disk: {[(e['lane_index'], e['blocked_vehicle_classes']) for e in banned_lanes]}")
        assert all(e["blocked_vehicle_classes"] == ["20"] for e in banned_lanes)
        _log("      PASS: the turn ban genuinely reached the saved file.")

        _log(f"[B.5] Dispatching set_turn_ban(connector_no={connector_id!r}, "
             f"vehicle_classes=[], banned=False) (un-ban)...")
        unban_envelope = dispatch(
            "set_turn_ban",
            {"connector_no": connector_id, "vehicle_classes": [], "banned": False},
            registry=default_registry,
        )
        _log(f"      envelope = {unban_envelope}")
        assert unban_envelope["status"] == "ok"
        assert unban_envelope["verification"]["readback_value"]["vehicle_classes"] == []
        _log("[B.6] PASS: un-ban envelope ok, readback vehicle_classes == [] on every lane.")

        restored_shadow = build_shadow_model(artifact_manager.working_path)
        restored_lanes = [e for e in restored_shadow["link_lanes"] if e["link_id"] == connector_id]
        ok = all(e["blocked_vehicle_classes"] == [] for e in restored_lanes)
        _log(f"[B.7] {'PASS' if ok else 'FAIL'}: reparse confirms every lane un-banned.")
    finally:
        clear_active_edit_session()
        session.close()

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


def phase_c_remove_unreferenced_connector_then_rollback(example_dir: Path) -> bool:
    _log("")
    _log("=" * 78)
    _log("[Phase C] Full run_agent_turn: remove_connector (unreferenced) -> rollback_last_edit")
    _log("=" * 78)

    import services.agent.tools_edit as tools_edit
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    from services import inpx_structure as struct
    import xml.etree.ElementTree as ET

    def _action(tool_name: str, *, summary: str, **params: Any):
        params["summary"] = summary
        return {"tool": tool_name, "params": params}, {"mode": "canned", "fallback_rung": 0}

    work_dir = _fresh_work_dir(example_dir, "phaseC")
    source_inpx = next(example_dir.glob("*.inpx"))
    original_get_active_inpx_path = tools_edit.get_active_inpx_path
    tools_edit.get_active_inpx_path = lambda: source_inpx
    artifact_manager = ArtifactManager(source_inpx=source_inpx, work_dir=work_dir)
    shadow = artifact_manager.clone()
    try:
        root = ET.parse(artifact_manager.working_path).getroot()
        connector = None
        for candidate in shadow["links"]:
            if not candidate.get("is_connector"):
                continue
            referrers = struct.find_id_references(root, "links", candidate["id"])
            if referrers:
                continue
            lanes_el_lane_count = candidate.get("lanes", 0)
            lane_referrers = []
            for lane_index in range(1, lanes_el_lane_count + 1):
                lane_referrers.extend(struct.find_lane_token_references(root, candidate["id"], lane_index))
            if lane_referrers:
                continue
            connector = candidate
            break
        assert connector is not None, "no genuinely unreferenced connector found"
        connector_id = connector["id"]
        _log(f"[C.1] Cloned {example_dir.name} -> {artifact_manager.working_path}; target connector "
             f"{connector_id} (genuinely unreferenced, {connector['lanes']} lane(s))")

        remove_queue = [
            _action("remove_connector", summary="커넥터 제거", connector_no=connector_id),
            _action("respond", summary="완료", answer=f"커넥터 {connector_id}를 제거했습니다."),
        ]
        started = time.monotonic()
        remove_result, artifact_manager = _run_turn(
            example_dir, "phaseC-remove", remove_queue, work_dir=work_dir, artifact_manager=artifact_manager
        )
        _log(f"      remove turn completed in {time.monotonic() - started:.1f}s -- kind={remove_result.kind!r}")
        _log(f"      answer={remove_result.answer!r}")

        c1_ok = remove_result.kind == "answer" and artifact_manager.version == 1
        if not c1_ok:
            _log(f"      FAIL: expected a completed answer + version==1, got kind={remove_result.kind!r} "
                 f"version={artifact_manager.version}")
            return False
        removed_shadow = artifact_manager.shadow_model
        still_present = any(l["id"] == connector_id for l in removed_shadow["links"])
        _log(f"      PASS: version==1, connector {connector_id} still present after remove: {still_present}")
        if still_present:
            return False

        _log("")
        _log("[C.2] rollback_last_edit turn...")
        rollback_queue = [
            _action("rollback_last_edit", summary="되돌리기"),
            _action("respond", summary="완료", answer="마지막 편집을 되돌렸습니다."),
        ]
        started = time.monotonic()
        rollback_result, artifact_manager = _run_turn(
            example_dir, "phaseC-rollback", rollback_queue, work_dir=work_dir, artifact_manager=artifact_manager
        )
        _log(f"      rollback turn completed in {time.monotonic() - started:.1f}s -- kind={rollback_result.kind!r}")
        _log(f"      answer={rollback_result.answer!r}")

        c2_ok = rollback_result.kind == "answer" and artifact_manager.version == 2
        if not c2_ok:
            _log(f"      FAIL: expected a completed answer + version==2, got kind={rollback_result.kind!r} "
                 f"version={artifact_manager.version}")
            return False

        restored_bytes_match = artifact_manager.working_path.read_bytes() == artifact_manager.source_inpx.read_bytes()
        _log(f"      version==2; working copy byte-identical to source clone: {restored_bytes_match}")
        if not restored_bytes_match:
            return False

        restored_shadow = build_shadow_model(artifact_manager.working_path)
        restored_present = any(l["id"] == connector_id for l in restored_shadow["links"])
        ok = restored_present
        _log(f"[C.3] {'PASS' if ok else 'FAIL'}: rollback_last_edit restored connector {connector_id}, "
             "verified both byte- and field-level.")
        return ok
    finally:
        tools_edit.get_active_inpx_path = original_get_active_inpx_path


def phase_d_remove_referenced_connector_refuses(example_dir: Path) -> bool:
    _log("")
    _log("=" * 78)
    _log("[Phase D] remove_connector on a REFERENCED connector -- refuses, names the referrer")
    _log("=" * 78)

    from services.agent.edit_session import VissimEditSession, clear_active_edit_session, set_active_edit_session
    from services.agent.state import ArtifactManager
    import services.agent.tools_edit  # noqa: F401
    from services.agent.toolkit import default_registry, dispatch

    work_dir = _fresh_work_dir(example_dir, "phaseD")
    artifact_manager = ArtifactManager(source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir)
    shadow = artifact_manager.clone()
    conflict_referenced_connector_id = None
    referenced_ca_no = None
    connector_ids = {l["id"] for l in shadow["links"] if l.get("is_connector")}
    for ca in shadow["conflict_areas"]:
        if ca["link_a"] in connector_ids:
            conflict_referenced_connector_id, referenced_ca_no = ca["link_a"], ca["no"]
            break
        if ca["link_b"] in connector_ids:
            conflict_referenced_connector_id, referenced_ca_no = ca["link_b"], ca["no"]
            break
    assert conflict_referenced_connector_id is not None, "no conflict-area-referenced connector found"
    original_bytes = artifact_manager.working_path.read_bytes()
    _log(f"[D.1] Cloned {example_dir.name} -> {artifact_manager.working_path}; target connector "
         f"{conflict_referenced_connector_id} referenced by conflict area {referenced_ca_no}")

    session = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session)
        _log(f"[D.2] Dispatching remove_connector(connector_no={conflict_referenced_connector_id!r})...")
        envelope = dispatch(
            "remove_connector", {"connector_no": conflict_referenced_connector_id}, registry=default_registry
        )
        _log(f"      envelope = {envelope}")
        refused = envelope["status"] == "error" and envelope["error_type"] == "tool_execution_error"
        names_referrer = "conflict area" in envelope.get("message", "")
        file_untouched = artifact_manager.working_path.read_bytes() == original_bytes
        ok = refused and names_referrer and file_untouched
        _log(f"[D.3] {'PASS' if ok else 'FAIL'}: refused={refused}, names_referrer={names_referrer}, "
             f"file_untouched={file_untouched}")
        return ok
    finally:
        clear_active_edit_session()
        session.close()


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    a_ok = phase_a_link_closure_route_warn_oracle_reopen(example_dir)
    b_ok = phase_b_turn_ban_readback_reparse_unban(example_dir)
    c_ok = phase_c_remove_unreferenced_connector_then_rollback(PHASE_C_EXAMPLE)
    d_ok = phase_d_remove_referenced_connector_refuses(example_dir)

    _log("")
    _log("=" * 78)
    _log(
        f"RESULT: Phase A {'PASS' if a_ok else 'FAIL'}, Phase B {'PASS' if b_ok else 'FAIL'}, "
        f"Phase C {'PASS' if c_ok else 'FAIL'}, Phase D {'PASS' if d_ok else 'FAIL'}"
    )
    _log("=" * 78)
    if not (a_ok and b_ok and c_ok and d_ok):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

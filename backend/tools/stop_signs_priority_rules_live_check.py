"""Q4 unit (6) LIVE END-TO-END CHECK: add_stop_sign/remove_stop_sign +
add_priority_rule/remove_priority_rule, against a REAL cloned Examples Demo
network -- no fake anywhere in this script. House shape =
``signal_heads_detectors_live_check.py`` (the newest sibling unit's own live
check).

Two phases:

  [Phase A] Dispatch-level: a real VissimEditSession, add_stop_sign on the
  LAST lane of a fresh link, add_priority_rule with TWO conflict markers
  (proving the ordinal-addressing readback this unit's own
  vissim_com.add_priority_rule re-derives, since marker order is NOT
  append-order -- see that module's own comment), an INTEGRATION PROOF that
  set_priority_rule_gap_headway (the EXISTING QUEUE 3 unit 2 setter) can
  edit one of the freshly-created markers by its real marker_index, the REAL
  COM load oracle against the mutated working copy, the NEW reference-
  integrity refusal this unit's own inpx_structure.py extension enables
  (remove_last_lane on the lane the new stop sign now occupies -- refused,
  naming it), then remove_priority_rule/remove_stop_sign (same-claim
  verification), the load oracle again to confirm clean restoration, and a
  reparse to confirm stop_signs/priority_rules counts are back to baseline.

  [Phase B] Full orchestrator level: add_stop_sign through a real
  ``run_agent_turn`` (PreapprovedWriteGate, real ArtifactManager, real
  GuardrailEngine, canned planner -- no paid LLM calls), THEN
  rollback_last_edit through a second real turn.

Usage: python tools/stop_signs_priority_rules_live_check.py [example_dir]
Default example: Roundabout Buehl.DE (2026 Examples Demo) -- 61 real
priority rules (add_priority_rule's own required structural template; see
vissim_com.VissimController.add_priority_rule's own docstring), 0 stop
signs.

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
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Roundabout Buehl.DE"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "stop-signs-priority-rules-live-check"


def _log(msg: str) -> None:
    print(msg, flush=True)


def _pick_source_inpx(example_dir: Path) -> Path:
    """LIVE-CAUGHT (this script's own first run): Roundabout Buehl.DE's
    example folder ships TWO .inpx files for the SAME topology --
    ``3 Roundabouts - Conflict areas.inpx`` (0 priority rules) and
    ``3 Roundabouts - Prio rules.inpx`` (61 real priority rules) -- a plain
    ``next(example_dir.glob("*.inpx"))`` non-deterministically (glob order)
    picked the WRONG one, correctly triggering add_priority_rule's own
    no-template refusal (a genuine, working refusal path -- just not what
    this script means to exercise). Prefer a file whose name contains "Prio"
    (case-insensitive); fall back to the first match for any other example
    directory with a single .inpx."""
    candidates = list(example_dir.glob("*.inpx"))
    for candidate in candidates:
        if "prio" in candidate.name.lower():
            return candidate
    return candidates[0]


def _fresh_work_dir(example_dir: Path, suffix: str) -> Path:
    for stale in WORK_ROOT.glob(f"{example_dir.name}_{suffix}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    work_dir = WORK_ROOT / f"{example_dir.name}_{suffix}_{os.getpid()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _pick_two_links(shadow: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """(stop_link, marker_link_1, marker_link_2) -- three distinct real,
    non-connector links each with real length, none currently carrying a
    stop sign or a priority-rule stop line (avoids any accidental real-data
    collision, though not load-bearing for correctness). ``stop_link`` MUST
    have >=2 lanes (LIVE-CAUGHT, this script's own second run: a 1-lane
    link's only lane is refused by remove_last_lane for a DIFFERENT reason
    first -- "a link cannot have zero lanes" -- never reaching the
    reference-integrity check [A.7] means to exercise at all; mirrors
    signal_heads_detectors_live_check.py's own identical, already-documented
    precaution)."""
    used = {str(s["link"]) for s in shadow.get("stop_signs", [])}
    used |= {str(r["link"]) for r in shadow.get("priority_rules", [])}
    stop_candidates = [
        l for l in shadow["links"]
        if not l.get("is_connector") and (l.get("length") or 0) > 20 and l.get("lanes", 0) >= 2
        and str(l["id"]) not in used
    ]
    marker_candidates = [
        l for l in shadow["links"]
        if not l.get("is_connector") and (l.get("length") or 0) > 20 and str(l["id"]) not in used
    ]
    if len(marker_candidates) < 3:
        marker_candidates = [l for l in shadow["links"] if not l.get("is_connector") and (l.get("length") or 0) > 20]
    stop_link = stop_candidates[0] if stop_candidates else marker_candidates[0]
    others = [l for l in marker_candidates if l["id"] != stop_link["id"]]
    return stop_link, others[0], others[1]


def phase_a_dispatch_add_load_oracle_refusal_remove(example_dir: Path) -> bool:
    _log("=" * 78)
    _log("[Phase A] add_stop_sign + add_priority_rule(2 markers) via toolkit.dispatch -> "
         "integration proof (set_priority_rule_gap_headway) -> load oracle -> lane "
         "reference-integrity REFUSAL -> remove both -> load oracle")
    _log("=" * 78)

    from services.agent.edit_session import (
        VissimEditSession,
        clear_active_edit_session,
        set_active_edit_session,
    )
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    import services.agent.tools_edit  # noqa: F401 - registers the new verbs
    import services.agent.tools_check  # noqa: F401 - registers load_check
    from services import inpx_structure as struct
    from services.agent.toolkit import default_registry, dispatch

    work_dir = _fresh_work_dir(example_dir, "phaseA")
    artifact_manager = ArtifactManager(source_inpx=_pick_source_inpx(example_dir), work_dir=work_dir)
    shadow = artifact_manager.clone()
    _log(f"[A.1] Cloned {example_dir.name} -> {artifact_manager.working_path}")

    baseline_ss = len(shadow["stop_signs"])
    baseline_pr = len(shadow["priority_rules"])
    _log(f"      Baseline counts: stop_signs={baseline_ss} priority_rules={baseline_pr}")

    stop_link, marker_link_1, marker_link_2 = _pick_two_links(shadow)
    stop_lane = stop_link["lanes"]  # LAST lane -- the reference-integrity proof at A.7 needs this
    _log(f"      Target: stop sign on link {stop_link['id']} (length {stop_link['length']}, "
         f"{stop_link['lanes']} lanes) lane {stop_lane} (its LAST lane); "
         f"priority-rule markers on links {marker_link_1['id']}/{marker_link_2['id']}")

    ok = False
    session = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session)

        stop_pos = round(min(5.0, stop_link["length"] / 4), 3)
        _log(f"\n[A.2] add_stop_sign(link={stop_link['id']}, lane={stop_lane}, pos={stop_pos})...")
        ss_env = dispatch(
            "add_stop_sign",
            {"link_no": stop_link["id"], "lane_index": stop_lane, "pos_m": stop_pos, "name": "LiveCheck SS"},
            registry=default_registry,
        )
        _log(f"      envelope = {ss_env}")
        assert ss_env["status"] == "ok", ss_env
        ss_no = ss_env["artifacts"]["no"]
        _log(f"      PASS: stop sign {ss_no} added.")

        pr_pos = round(min(5.0, marker_link_1["length"] / 4), 3)
        m1_pos = round(min(3.0, marker_link_1["length"] / 5), 3)
        m2_pos = round(min(6.0, marker_link_2["length"] / 5), 3)
        _log(
            f"\n[A.3] add_priority_rule(stop_link={marker_link_1['id']}, stop_lane=1, "
            f"pos={pr_pos}, markers=[link {marker_link_1['id']} lane 1 pos {m1_pos}, "
            f"link {marker_link_2['id']} lane 1 pos {m2_pos}]) -- TWO markers, proving the "
            "atomic-create-then-add-second-marker path this unit's own vissim_com.py "
            "documents (DuplicatePriorityRule + AddConflictMarker)..."
        )
        pr_env = dispatch(
            "add_priority_rule",
            {
                "stop_link_no": marker_link_1["id"], "stop_lane_index": 1, "stop_pos_m": pr_pos,
                "markers": [
                    {"link_no": marker_link_1["id"], "lane_index": 1, "pos_m": m1_pos},
                    {"link_no": marker_link_2["id"], "lane_index": 1, "pos_m": m2_pos, "gap_time_s": 2.0, "headway_m": 8.0},
                ],
            },
            registry=default_registry,
        )
        _log(f"      envelope = {pr_env}")
        assert pr_env["status"] == "ok", pr_env
        pr_no = pr_env["artifacts"]["no"]
        markers = pr_env["artifacts"]["markers"]
        assert len(markers) == 2, markers
        _log(f"      PASS: priority rule {pr_no} added with {len(markers)} markers: {markers}")
        _log(
            "      Real, readback-verified marker_index per entry (NOT assumed to equal "
            f"input order -- see vissim_com.py's own module comment): "
            f"marker[0] (link {marker_link_1['id']}) -> marker_index={markers[0]['marker_index']}, "
            f"marker[1] (link {marker_link_2['id']}) -> marker_index={markers[1]['marker_index']}."
        )

        _log(
            "\n[A.3b] INTEGRATION PROOF: set_priority_rule_gap_headway (the EXISTING QUEUE 3 "
            f"unit (2) setter) editing marker_index={markers[0]['marker_index']} of the "
            f"JUST-CREATED rule {pr_no}..."
        )
        gh_env = dispatch(
            "set_priority_rule_gap_headway",
            {
                "priority_rule_no": str(pr_no), "marker_index": markers[0]["marker_index"],
                "gap_time_s": 4.5, "headway_m": 12.0,
            },
            registry=default_registry,
        )
        _log(f"      envelope = {gh_env}")
        assert gh_env["status"] == "ok", gh_env
        assert gh_env["verification"]["readback_value"] == {"gap_time_s": 4.5, "headway_m": 12.0}, gh_env
        _log(
            "      PASS: a rule created THIS turn by add_priority_rule is immediately editable "
            "by the pre-existing set_priority_rule_gap_headway verb -- gap_time_s/headway_m "
            "now 4.5s/12.0m."
        )

        after_add_shadow = build_shadow_model(artifact_manager.working_path)
        _log(
            f"\n[A.4] Reparse from disk after both adds: "
            f"stop_signs={len(after_add_shadow['stop_signs'])} "
            f"priority_rules={len(after_add_shadow['priority_rules'])}"
        )
        assert len(after_add_shadow["stop_signs"]) == baseline_ss + 1
        assert len(after_add_shadow["priority_rules"]) == baseline_pr + 1
        added_ss = next(s for s in after_add_shadow["stop_signs"] if s["no"] == str(ss_no))
        added_pr = next(r for r in after_add_shadow["priority_rules"] if r["no"] == str(pr_no))
        _log(f"      stop sign {ss_no}: link={added_ss['link']} lane_index={added_ss['lane_index']} pos={added_ss['pos']}")
        _log(f"      priority rule {pr_no}: link={added_pr['link']} markers={len(added_pr['conflict_markers'])}")
        assert added_ss["link"] == stop_link["id"] and added_ss["lane_index"] == stop_lane
        assert added_pr["link"] == marker_link_1["id"]
        assert len(added_pr["conflict_markers"]) == 2
        persisted_gap = next(
            m["gap_time_s"] for m in added_pr["conflict_markers"]
            if m["marker_index"] == markers[0]["marker_index"]
        )
        assert persisted_gap == 4.5, persisted_gap
        _log("      PASS: both new objects landed on disk with the exact intended attributes, "
             "including the integration-proof gap edit.")

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

    _log(
        "\n[A.7] NEW reference-integrity REFUSAL proof (this unit's own "
        "LANE_TOKEN_REFERENCE_SITES extension): remove_last_lane on the lane the "
        "stop sign just added now occupies..."
    )
    try:
        struct.remove_last_lane(artifact_manager.working_path, stop_link["id"], run_load_check=False)
        _log("      FAIL: remove_last_lane did NOT refuse -- expected ReferenceIntegrityError.")
        return False
    except struct.ReferenceIntegrityError as exc:
        sites = sorted({r["site"] for r in exc.referrers})
        _log(f"      REFUSED, naming referrers: {exc.referrers}")
        assert "stop sign" in sites, sites
        _log(
            "      PASS: refused, naming the new stop sign "
            f"(plus {len(sites) - 1} other real, pre-existing referrer(s) on this lane, if any) -- "
            "Vissim's own LoadNet would not have caught this."
        )

    _log("\n[A.8] Removing both in a fresh VissimEditSession (priority rule then stop sign)...")
    session2 = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session2)

        pr_remove_env = dispatch("remove_priority_rule", {"no": str(pr_no)}, registry=default_registry)
        _log(f"      remove_priority_rule({pr_no}) -> {pr_remove_env['status']}")
        assert pr_remove_env["status"] == "ok", pr_remove_env
        assert pr_remove_env["verification"] == {
            "written_value": {"removed_no": str(pr_no), "still_present": False},
            "readback_value": {"removed_no": str(pr_no), "still_present": False},
        }

        ss_remove_env = dispatch("remove_stop_sign", {"no": str(ss_no)}, registry=default_registry)
        _log(f"      remove_stop_sign({ss_no}) -> {ss_remove_env['status']}")
        assert ss_remove_env["status"] == "ok", ss_remove_env
        assert ss_remove_env["verification"] == {
            "written_value": {"removed_no": str(ss_no), "still_present": False},
            "readback_value": {"removed_no": str(ss_no), "still_present": False},
        }
        _log(
            "      PASS: both same-claim verifications agree on genuine success "
            "(written_value == readback_value, the Q4 unit (3) _remove_verification pattern)."
        )

        restored_shadow = build_shadow_model(artifact_manager.working_path)
        _log(
            f"\n[A.9] Reparse from disk after both removes: "
            f"stop_signs={len(restored_shadow['stop_signs'])} (baseline {baseline_ss}) "
            f"priority_rules={len(restored_shadow['priority_rules'])} (baseline {baseline_pr})"
        )
        # A removed priority rule also removes its OWN 2 markers atomically
        # (verified live by this unit's own probe) -- confirmed here too via
        # the plain count.
        counts_restored = (
            len(restored_shadow["stop_signs"]) == baseline_ss
            and len(restored_shadow["priority_rules"]) == baseline_pr
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
    _log("[Phase B] Full run_agent_turn: add_stop_sign (approved) -> rollback_last_edit (approved)")
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
    source_inpx = _pick_source_inpx(example_dir)
    original_get_active_inpx_path = tools_edit.get_active_inpx_path
    tools_edit.get_active_inpx_path = lambda: source_inpx
    artifact_manager = ArtifactManager(source_inpx=source_inpx, work_dir=work_dir)
    shadow = artifact_manager.clone()
    try:
        stop_link, _, _ = _pick_two_links(shadow)
        pos = round(min(5.0, stop_link["length"] / 4), 3)
        _log(
            f"[B.1] Cloned {example_dir.name} -> {artifact_manager.working_path}; "
            f"target link {stop_link['id']} (len {stop_link['length']})"
        )

        add_queue = [
            _action(
                "add_stop_sign", summary="정지 표지판 추가",
                link_no=stop_link["id"], lane_index=1, pos_m=pos, name="LiveCheck Rollback SS",
            ),
            _action("respond", summary="완료", answer="정지 표지판을 추가했습니다."),
        ]
        started = time.monotonic()
        add_result = _run_turn(work_dir, artifact_manager, "phaseB-add", add_queue)
        _log(f"      add turn completed in {time.monotonic() - started:.1f}s -- kind={add_result.kind!r}")
        _log(f"      answer={add_result.answer!r}")

        b1_ok = add_result.kind == "answer" and artifact_manager.version == 1
        if not b1_ok:
            _log(
                f"      FAIL: expected a completed answer + version==1, got kind={add_result.kind!r} "
                f"version={artifact_manager.version}"
            )
            return False
        added_shadow = artifact_manager.shadow_model
        added_count = len(added_shadow["stop_signs"])
        _log(f"      PASS: version==1, stop_signs count={added_count}")

        _log("")
        _log("[B.2] rollback_last_edit turn...")
        rollback_queue = [
            _action("rollback_last_edit", summary="되돌리기"),
            _action("respond", summary="완료", answer="마지막 편집을 되돌렸습니다."),
        ]
        started = time.monotonic()
        rollback_result = _run_turn(work_dir, artifact_manager, "phaseB-rollback", rollback_queue)
        _log(
            f"      rollback turn completed in {time.monotonic() - started:.1f}s -- "
            f"kind={rollback_result.kind!r}"
        )
        _log(f"      answer={rollback_result.answer!r}")

        b2_ok = rollback_result.kind == "answer" and artifact_manager.version == 2
        if not b2_ok:
            _log(
                f"      FAIL: expected a completed answer + version==2, got kind={rollback_result.kind!r} "
                f"version={artifact_manager.version}"
            )
            return False

        restored_bytes_match = (
            artifact_manager.working_path.read_bytes() == artifact_manager.source_inpx.read_bytes()
        )
        _log(f"      version==2; working copy byte-identical to source clone: {restored_bytes_match}")
        if not restored_bytes_match:
            _log("      FAIL: rollback did not restore byte-identical bytes.")
            return False

        restored_shadow = build_shadow_model(artifact_manager.working_path)
        restored_count = len(restored_shadow["stop_signs"])
        _log(f"      restored stop_signs count = {restored_count} (pre-add was {added_count - 1})")
        ok = restored_count == added_count - 1
        _log(
            f"[B.3] {'PASS' if ok else 'FAIL'}: rollback_last_edit restored the stop-sign add, "
            "verified both byte- and field-level."
        )
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
    main()

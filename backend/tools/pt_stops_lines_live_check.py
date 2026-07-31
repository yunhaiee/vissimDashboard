"""Q4 unit (7) LIVE END-TO-END CHECK: add_pt_stop/remove_pt_stop,
add_pt_line/remove_pt_line, set_pt_line_stop_dwell -- against a REAL cloned
Examples Demo network, no fake anywhere in this script. House shape =
``stop_signs_priority_rules_live_check.py`` (the newest sibling unit's own
live check).

Two phases:

  [Phase A] Dispatch-level: a real VissimEditSession, add_pt_stop on a fresh
  link, add_pt_line from that link to a real destination (proving Vissim's
  own MINIMAL default route + auto-created line-stop, live-verified by this
  unit's own probe), set_pt_line_stop_dwell wiring a DIFFERENT real time
  distribution onto that auto-created line-stop (the BusDwell story's
  missing verb -- readback names the newly-wired distribution), the REAL COM
  load oracle against the mutated working copy, the NEW reference-integrity
  refusal this unit's own inpx_structure.py extension enables (remove_pt_stop
  on the stop the new line's own line-stop still references -- refused,
  naming it, BEFORE Vissim's own silent-cascade-delete could ever run), then
  remove_pt_line/remove_pt_stop (same-claim verification), the load oracle
  again to confirm clean restoration, and a reparse to confirm pt_stops/
  pt_lines counts are back to baseline.

  [Phase B] Full orchestrator level: add_pt_stop through a real
  ``run_agent_turn`` (PreapprovedWriteGate, real ArtifactManager, real
  GuardrailEngine, canned planner -- no paid LLM calls), THEN
  rollback_last_edit through a second real turn.

Usage: python tools/pt_stops_lines_live_check.py [example_dir]
Default example: BRT Priority Texas.US (2026 Examples Demo) -- proven
loadable, PT-rich (32 real ptStops / 10 real ptLines / 62 real ptLineStops),
per this unit's own probe (backend/tools/probe_pt_stops_lines.py).

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
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\BRT Priority Texas.US"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "pt-stops-lines-live-check"


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


def _pick_stop_link(shadow: dict[str, Any]) -> dict[str, Any]:
    """A real, non-connector link with decent length, not currently hosting
    a real PT stop -- avoids any accidental real-data collision, though not
    load-bearing for correctness."""
    used = {str(s["link"]) for s in shadow.get("pt_stops", [])}
    candidates = [
        l for l in shadow["links"]
        if not l.get("is_connector") and (l.get("length") or 0) > 30 and str(l["id"]) not in used
    ]
    if not candidates:
        candidates = [l for l in shadow["links"] if not l.get("is_connector") and (l.get("length") or 0) > 30]
    return candidates[0]


def phase_a_dispatch_add_load_oracle_refusal_remove(example_dir: Path) -> bool:
    _log("=" * 78)
    _log("[Phase A] add_pt_stop + add_pt_line + set_pt_line_stop_dwell via toolkit.dispatch -> "
         "load oracle -> PT-stop reference-integrity REFUSAL -> remove both -> load oracle")
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
    inpx_files = list(example_dir.glob("*.inpx"))
    if not inpx_files:
        raise SystemExit(f"No .inpx in {example_dir}")
    source_inpx = inpx_files[0]
    artifact_manager = ArtifactManager(source_inpx=source_inpx, work_dir=work_dir)
    shadow = artifact_manager.clone()
    _log(f"[A.1] Cloned {example_dir.name} -> {artifact_manager.working_path}")

    baseline_stops = len(shadow["pt_stops"])
    baseline_lines = len(shadow["pt_lines"])
    time_distributions = shadow["time_distributions"]
    _log(f"      Baseline counts: pt_stops={baseline_stops} pt_lines={baseline_lines} "
         f"time_distributions={len(time_distributions)}")
    if len(time_distributions) < 2:
        raise SystemExit("Need >=2 real time distributions on this network for the dwell-wiring proof.")

    stop_link = _pick_stop_link(shadow)
    # Reuse an EXISTING real ptLine's own (entry_link, dest_link, dest_pos)
    # triple -- a GUARANTEED reachable pair (a real line already routes
    # between them), isolating this check's own open question (does a
    # genesis line need departure times to be simulation-ready, independent
    # of route completeness?) from an unrelated "did I pick a disconnected
    # destination" variable.
    template_line = shadow["pt_lines"][0]
    entry_link = next(l for l in shadow["links"] if str(l["id"]) == template_line["entry_link"])
    dest_link_id = template_line["dest_link"]
    dest_pos = template_line["dest_pos"]
    _log(
        f"      Target: PT stop on link {stop_link['id']} (length {stop_link['length']}); "
        f"PT line from link {entry_link['id']} to link {dest_link_id}@{dest_pos}m "
        f"(the SAME entry/dest/pos as real template line {template_line['no']}, a guaranteed "
        "reachable pair)"
    )

    ok = False
    session = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session)

        stop_pos = round(min(5.0, stop_link["length"] / 4), 3)
        _log(f"\n[A.2] add_pt_stop(link={stop_link['id']}, lane=1, pos={stop_pos}, length_m=12.0)...")
        stop_env = dispatch(
            "add_pt_stop",
            {"link_no": stop_link["id"], "lane_index": 1, "pos_m": stop_pos, "length_m": 12.0, "name": "LiveCheck Stop"},
            registry=default_registry,
        )
        _log(f"      envelope = {stop_env}")
        assert stop_env["status"] == "ok", stop_env
        stop_no = stop_env["artifacts"]["no"]
        _log(f"      PASS: PT stop {stop_no} added (length {stop_env['artifacts']['length']}m).")

        _log(
            f"\n[A.3] add_pt_line(entry_link={entry_link['id']}, dest_link={dest_link_id}, "
            f"dest_pos={dest_pos}) -- NO pt_stop_no given, proving Vissim's own auto-pick "
            "(whichever real PT stop sits on the entry link, per this unit's own probe)..."
        )
        line_env = dispatch(
            "add_pt_line",
            {"entry_link_no": entry_link["id"], "dest_link_no": dest_link_id, "dest_pos_m": dest_pos},
            registry=default_registry,
        )
        _log(f"      envelope = {line_env}")
        assert line_env["status"] == "ok", line_env
        line_no = line_env["artifacts"]["no"]
        auto_stops = line_env["artifacts"]["stops"]
        _log(f"      PASS: PT line {line_no} added, link_seq={line_env['artifacts']['link_seq']}, "
             f"auto-created stop(s)={auto_stops}.")
        assert len(auto_stops) == 1, auto_stops
        auto_stop_no = auto_stops[0]["pt_stop_no"]
        auto_dwell_before = auto_stops[0]["dwell_tm_distr"]
        _log(f"      Auto line-stop references PT stop {auto_stop_no} "
             f"(dwell distribution {auto_dwell_before}).")

        # The BusDwell story's missing verb: wire a DIFFERENT real time
        # distribution onto the auto-created line-stop.
        target_distr = next(
            d for d in time_distributions if str(d["no"]) != str(auto_dwell_before)
        )
        _log(
            f"\n[A.4] set_pt_line_stop_dwell(line_no={line_no}, stop_no={auto_stop_no}, "
            f"time_distribution_no={target_distr['no']}) -- reconnecting a staged distribution "
            "to a real line-stop..."
        )
        dwell_env = dispatch(
            "set_pt_line_stop_dwell",
            {"line_no": str(line_no), "stop_no": auto_stop_no, "time_distribution_no": target_distr["no"]},
            registry=default_registry,
        )
        _log(f"      envelope = {dwell_env}")
        assert dwell_env["status"] == "ok", dwell_env
        assert dwell_env["verification"]["readback_value"] == str(target_distr["no"]), dwell_env
        _log(f"      PASS: readback names the newly-wired distribution {target_distr['no']} "
             f"({target_distr['name']!r}).")

        after_add_shadow = build_shadow_model(artifact_manager.working_path)
        _log(
            f"\n[A.5] Reparse from disk after add_pt_stop+add_pt_line+set_pt_line_stop_dwell: "
            f"pt_stops={len(after_add_shadow['pt_stops'])} pt_lines={len(after_add_shadow['pt_lines'])}"
        )
        assert len(after_add_shadow["pt_stops"]) == baseline_stops + 1
        assert len(after_add_shadow["pt_lines"]) == baseline_lines + 1
        added_line = next(pl for pl in after_add_shadow["pt_lines"] if pl["no"] == str(line_no))
        assert added_line["entry_link"] == str(entry_link["id"])
        assert len(added_line["stops"]) == 1
        assert added_line["stops"][0]["pt_stop_no"] == auto_stop_no
        assert added_line["stops"][0]["dwell_tm_distr"] == str(target_distr["no"])
        _log("      PASS: both new objects AND the dwell rewiring landed on disk with the "
             "exact intended attributes.")

        session.close()
        _log("\n[A.6] Edit session closed before running the COM load oracle (its own dedicated instance).")
    finally:
        clear_active_edit_session()

    _log("\n[A.7] Running the COM load oracle on the mutated working copy (genesis line has ZERO "
         "departure times so far -- this step's own open question: does Vissim's init refuse a "
         "PT line with no timetable at all)...")
    from services.agent.tools_check import _load_check

    load_result = _load_check(artifact_manager.working_path)
    _log(f"      load_check_result = {load_result}")
    genesis_line_needs_departure_times = not (load_result["load_ok"] and load_result["init_ok"])
    if genesis_line_needs_departure_times:
        _log(
            "      FINDING (live-caught, not guessed): LoadNet succeeds but RunSingleStep's own "
            "initialization REFUSES -- Vissim's own err_content names the exact cause: "
            f"{load_result.get('err_content', '')!r}. A genesis add_pt_line call, exactly as "
            "shipped, is NOT simulation-ready with zero departure times -- add_pt_line's own "
            "message already states this unconditionally (see tools_edit.py); confirming here, "
            "live, before writing the report."
        )
        _log(
            "\n[A.7b] ISOLATION TEST -- does adding ONE real departure time (COM AddDepartureTime, "
            "confirmed tractable by this unit's own probe but NOT wired into any shipped verb) "
            "make the SAME line pass the load oracle? (proves the fix is departure times, not an "
            "unrelated route-completeness issue -- entry/dest/pos here are a real template line's "
            "own values, a guaranteed-reachable pair)..."
        )
        import pythoncom

        from services.vissim_com import VissimController

        pythoncom.CoInitialize()
        dep_controller = VissimController()
        try:
            dep_controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
            dep_controller.load_network(str(artifact_manager.working_path))
            pt_line_obj = dep_controller.vissim.Net.PTLines.ItemByKey(int(line_no))
            pt_line_obj.DepTimes.AddDepartureTime(1, 0.0)
            tmp_path = artifact_manager.working_path.with_name(
                artifact_manager.working_path.stem + "_deptime_saving" + artifact_manager.working_path.suffix
            )
            dep_controller.save_net_as(tmp_path)
            os.replace(tmp_path, artifact_manager.working_path)
            _log(f"      Added ONE departure time (dep=0.0) to line {line_no} and persisted.")
        finally:
            try:
                dep_controller.disconnect()
            except Exception:
                pass
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

        load_result_dep = _load_check(artifact_manager.working_path)
        _log(f"      load_check_result (with 1 departure time) = {load_result_dep}")
        if load_result_dep["load_ok"] and load_result_dep["init_ok"]:
            _log(
                "      CONFIRMED: exactly ONE departure time is what 'completes' a genesis PT "
                "line for Vissim's own init -- route completeness (entry/dest reachability) was "
                "NOT the cause here (a real template's own reachable pair was already used)."
            )
            load_result = load_result_dep
        else:
            _log(
                "      Adding a departure time did NOT fix it either -- the incompleteness has "
                "another cause beyond missing departure times (route shape, vehicle type, or "
                "something else); reported honestly, not papered over."
            )

    assert load_result["load_ok"] is True and load_result["init_ok"] is True, (
        f"expected the mutated network to load+init cleanly (after the departure-time isolation "
        f"test, if one ran), got {load_result}"
    )
    _log("      PASS: LoadNet + one RunSingleStep both succeeded with the new stop, line, and "
         "dwell rewiring all present.")

    _log(
        "\n[A.8] NEW reference-integrity REFUSAL proof (this unit's own "
        "services.inpx_structure.ID_REFERENCE_SITES['pt_stops'] extension): remove_pt_stop on "
        f"PT stop {auto_stop_no} -- still referenced by the new line's own auto-created "
        "line-stop. COM itself would NOT refuse this (it would SILENTLY CASCADE-DELETE the "
        "referencing line-stop instead, live-verified by this unit's own probe [1b.15b])..."
    )
    session_refuse = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session_refuse)
        refuse_env = dispatch("remove_pt_stop", {"no": auto_stop_no}, registry=default_registry)
        _log(f"      envelope = {refuse_env}")
        if refuse_env["status"] != "error" or "still referenced by" not in refuse_env["message"]:
            _log("      FAIL: remove_pt_stop did NOT refuse -- expected a still-referenced-by error.")
            return False
        _log("      PASS: refused, naming the referrer -- the stop was NEVER touched by COM.")
    finally:
        clear_active_edit_session()
        session_refuse.close()

    _log("\n[A.9] Removing both in a fresh VissimEditSession (PT line first, freeing the stop, "
         "then the PT stop)...")
    session2 = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session2)

        line_remove_env = dispatch("remove_pt_line", {"no": str(line_no)}, registry=default_registry)
        _log(f"      remove_pt_line({line_no}) -> {line_remove_env['status']}")
        assert line_remove_env["status"] == "ok", line_remove_env
        assert line_remove_env["verification"] == {
            "written_value": {"removed_no": str(line_no), "still_present": False},
            "readback_value": {"removed_no": str(line_no), "still_present": False},
        }

        stop_remove_env = dispatch("remove_pt_stop", {"no": str(stop_no)}, registry=default_registry)
        _log(f"      remove_pt_stop({stop_no}) -> {stop_remove_env['status']}")
        assert stop_remove_env["status"] == "ok", stop_remove_env
        assert stop_remove_env["verification"] == {
            "written_value": {"removed_no": str(stop_no), "still_present": False},
            "readback_value": {"removed_no": str(stop_no), "still_present": False},
        }
        _log(
            "      PASS: both same-claim verifications agree on genuine success "
            "(written_value == readback_value, the Q4 unit (3) _remove_verification pattern)."
        )

        restored_shadow = build_shadow_model(artifact_manager.working_path)
        _log(
            f"\n[A.10] Reparse from disk after both removes: "
            f"pt_stops={len(restored_shadow['pt_stops'])} (baseline {baseline_stops}) "
            f"pt_lines={len(restored_shadow['pt_lines'])} (baseline {baseline_lines})"
        )
        counts_restored = (
            len(restored_shadow["pt_stops"]) == baseline_stops
            and len(restored_shadow["pt_lines"]) == baseline_lines
        )
        assert counts_restored, "counts did not return to baseline after removal"
        _log("      PASS: both counts back to their pre-edit baseline.")

        session2.close()
    finally:
        clear_active_edit_session()

    _log("\n[A.11] Running the COM load oracle AGAIN on the fully-restored working copy...")
    load_result_2 = _load_check(artifact_manager.working_path)
    _log(f"      load_check_result = {load_result_2}")
    assert load_result_2["load_ok"] is True and load_result_2["init_ok"] is True
    _log("      PASS: clean restoration confirmed by the REAL load oracle a second time.")
    ok = True
    return ok


def phase_b_full_orchestrator_add_then_rollback(example_dir: Path) -> bool:
    _log("")
    _log("=" * 78)
    _log("[Phase B] Full run_agent_turn: add_pt_stop (approved) -> rollback_last_edit (approved)")
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
    inpx_files = list(example_dir.glob("*.inpx"))
    source_inpx = inpx_files[0]
    original_get_active_inpx_path = tools_edit.get_active_inpx_path
    tools_edit.get_active_inpx_path = lambda: source_inpx
    artifact_manager = ArtifactManager(source_inpx=source_inpx, work_dir=work_dir)
    shadow = artifact_manager.clone()
    try:
        stop_link = _pick_stop_link(shadow)
        pos = round(min(5.0, stop_link["length"] / 4), 3)
        _log(
            f"[B.1] Cloned {example_dir.name} -> {artifact_manager.working_path}; "
            f"target link {stop_link['id']} (len {stop_link['length']})"
        )

        add_queue = [
            _action(
                "add_pt_stop", summary="PT 정류장 추가",
                link_no=stop_link["id"], lane_index=1, pos_m=pos, name="LiveCheck Rollback Stop",
            ),
            _action("respond", summary="완료", answer="PT 정류장을 추가했습니다."),
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
        added_count = len(added_shadow["pt_stops"])
        _log(f"      PASS: version==1, pt_stops count={added_count}")

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
        restored_count = len(restored_shadow["pt_stops"])
        _log(f"      restored pt_stops count = {restored_count} (pre-add was {added_count - 1})")
        ok = restored_count == added_count - 1
        _log(
            f"[B.3] {'PASS' if ok else 'FAIL'}: rollback_last_edit restored the PT-stop add, "
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

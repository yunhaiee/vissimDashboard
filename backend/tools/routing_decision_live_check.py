"""LIVE ACCEPTANCE for v2 unit ⑥: set_route_relative_flow end to end on a
Beijing.CN clone -- read decisions -> preflight (bogus decision refuses with
candidates) -> approved write -> readback -> persisted XML shows the one
relFlow changed, siblings untouched -> load_check clean.

[Phase 1] Static parse -- no COM. Parses the REAL example-network XML
through ``services.agent.tools_network.build_shadow_model`` and asserts
``routing_decisions``/``routes`` are populated (12 decisions, 28 routes on
Beijing, per the Phase 1 probe), with the expected shape.

[Phase 2] Real preflight path -- no COM. Clones Beijing via a real
``ArtifactManager``, builds the real action dict, and runs it through the
REAL ``GuardrailEngine().preflight()``:
  - a bogus decision_no REFUSES via object_not_found, WITH candidates.
  - a bogus route_no (within a real decision) REFUSES via
    route_not_found_in_decision, WITH candidates.
  - a real (decision_no, route_no) PASSES (no refusal) and carries the
    route_relative_flow_split_confirm warning echoing the estimated share.

[Phase 3] ONE live COM write, full pipeline (preflight -> toolkit.dispatch ->
postwrite), against a CLONE of Beijing (never the original .inpx):
set_route_relative_flow on decision 1's route 1. Proves preflight lets the
write through, the COM write+readback succeed, the persisted working copy's
<vehicleRouteStatic relFlow="..."> attribute round-trips on disk with the
target route's value changed and EVERY SIBLING route's relFlow byte-for-byte
untouched (the central empirical finding: Vissim does not renormalize on
write), and load_check on that same working copy still comes back clean
(load_ok=True, init_ok=True) -- the edit did not break the network.

Always closes every Vissim instance in a ``finally`` block;
attach_to_running=False throughout. Never touches the original Examples Demo
folder.

Usage: python tools/routing_decision_live_check.py
"""
from __future__ import annotations

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

REPO_ROOT = BACKEND_ROOT.parent
DEFAULT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
WORK_ROOT = REPO_ROOT / "runtime" / "routing-decision-live-check"

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


# ---------------------------------------------------------------------------
# Phase 1: static parse
# ---------------------------------------------------------------------------


def phase_1_static_parse(example_inpx: Path) -> None:
    _log("=" * 78)
    _log("[Phase 1] tools_network.build_shadow_model against the real Beijing.CN XML")
    _log("=" * 78)

    from services.agent.tools_network import build_shadow_model

    shadow = build_shadow_model(example_inpx)
    routing_decisions = shadow.get("routing_decisions")
    routes = shadow.get("routes")
    _log(f"[1.1] routing_decisions count = {len(routing_decisions) if routing_decisions else 0}, "
         f"flat routes count = {len(routes) if routes else 0}")
    _check("routing_decisions is populated (not None/empty) on Beijing", bool(routing_decisions))
    _check("routing_decisions count == 12 (matches the Phase 1 probe)", len(routing_decisions) == 12)
    _check("flat routes count == 28 (matches the Phase 1 probe)", len(routes) == 28)

    decision_1 = next(d for d in routing_decisions if d["no"] == "1")
    _check(
        "decision 1 has 3 routes numbered 1/2/3",
        {r["route_no"] for r in decision_1["routes"]} == {"1", "2", "3"},
        detail=str([r["route_no"] for r in decision_1["routes"]]),
    )
    route_1 = next(r for r in decision_1["routes"] if r["route_no"] == "1")
    _check("route 1's rel_flow == 245.0 (matches the static XML pre-scan)", route_1["rel_flow"] == 245.0)
    _check("no disconnected routes on a clean, unedited network", not any(r["disconnected"] for r in routes))


# ---------------------------------------------------------------------------
# Phase 2: real preflight path (clone + GuardrailEngine, no COM)
# ---------------------------------------------------------------------------


def phase_2_real_preflight(example_inpx: Path) -> dict[str, Any]:
    _log("")
    _log("=" * 78)
    _log("[Phase 2] Real preflight path: ArtifactManager.clone() -> GuardrailEngine().preflight()")
    _log("=" * 78)

    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager

    engine = GuardrailEngine()

    work_dir = _fresh_work_dir("phase2-bogus-decision")
    am = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir)
    shadow = am.clone()
    action = {
        "tool": "set_route_relative_flow",
        "params": {"decision_no": "999999", "route_no": "1", "rel_flow": 500.0},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.1] Bogus decision_no=999999 preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "bogus decision_no REFUSES via object_not_found, with candidates",
        "object_not_found" in ids and report.blocking,
        detail=str(ids),
    )
    obj_not_found = next(f for f in report.findings if f.constraint_id == "object_not_found")
    _log(f"      object_not_found suggested_fix (difflib candidates) = {obj_not_found.suggested_fix!r}")
    _check(
        "object_not_found carries a (possibly empty) candidates list -- '999999' has no close "
        "lexical match among Beijing's 12 decision numbers, so an empty list here is the correct, "
        "expected difflib outcome, not a bug",
        isinstance(obj_not_found.suggested_fix, list),
    )

    work_dir_b = _fresh_work_dir("phase2-bogus-route")
    am_b = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir_b)
    shadow_b = am_b.clone()
    action_b = {
        "tool": "set_route_relative_flow",
        "params": {"decision_no": "1", "route_no": "999999", "rel_flow": 500.0},
    }
    report_b = engine.preflight(action_b, shadow_b)
    ids_b = [f.constraint_id for f in report_b.findings]
    _log(f"[2.2] Real decision, bogus route_no=999999 preflight findings: {ids_b} (blocking={report_b.blocking})")
    _check(
        "bogus route_no REFUSES via route_not_found_in_decision, with candidates",
        "route_not_found_in_decision" in ids_b and report_b.blocking,
        detail=str(ids_b),
    )
    route_not_found = next(f for f in report_b.findings if f.constraint_id == "route_not_found_in_decision")
    _check(
        "route_not_found_in_decision candidates == this decision's real route numbers",
        set(route_not_found.suggested_fix) == {"1", "2", "3"},
        detail=str(route_not_found.suggested_fix),
    )

    work_dir2 = _fresh_work_dir("phase2-real")
    am2 = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir2)
    shadow2 = am2.clone()
    decision_1 = next(d for d in shadow2["routing_decisions"] if d["no"] == "1")
    route_1 = next(r for r in decision_1["routes"] if r["route_no"] == "1")
    action2 = {
        "tool": "set_route_relative_flow",
        "params": {"decision_no": "1", "route_no": "1", "rel_flow": 745.0},
    }
    report2 = engine.preflight(action2, shadow2)
    ids2 = [f.constraint_id for f in report2.findings]
    _log(f"[2.3] Real (decision=1, route=1) preflight findings: {ids2} (blocking={report2.blocking})")
    _check(
        "real (decision, route) PASSES (no refusal, not blocking)",
        not report2.blocking,
        detail=str(ids2),
    )
    confirm = next((f for f in report2.findings if f.constraint_id == "route_relative_flow_split_confirm"), None)
    _check("route_relative_flow_split_confirm warning present", confirm is not None)
    if confirm is not None:
        _log(f"      confirm finding details: {confirm.details}")
        _check(
            "confirm finding echoes the real old relFlow",
            confirm.details["old_rel_flow"] == route_1["rel_flow"],
            detail=str(confirm.details),
        )
        _check("confirm message states 'raw' weight semantics", "raw" in confirm.messages["en"].lower())
        _log(f"      approval-card en text: {confirm.messages['en']}")

    return {"decision_no": "1", "route_no": "1", "old_rel_flow": route_1["rel_flow"]}


# ---------------------------------------------------------------------------
# Phase 3: ONE live COM write, full pipeline, then load_check
# ---------------------------------------------------------------------------


def _read_decision_from_xml(inpx_path: Path, decision_no: str) -> dict[str, Any] | None:
    root = ET.parse(inpx_path).getroot()
    for dec in root.findall(".//vehicleRoutingDecisionStatic"):
        if dec.get("no") == decision_no:
            routes = {r.get("no"): r.get("relFlow") for r in dec.findall(".//vehicleRouteStatic")}
            return routes
    return None


def phase_3_live_com_write(example_inpx: Path, decision_no: str, route_no: str) -> bool:
    _log("")
    _log("=" * 78)
    _log(
        f"[Phase 3] ONE live COM write: set_route_relative_flow(decision={decision_no!r}, "
        f"route={route_no!r}) on a CLONE of Beijing"
    )
    _log("           (full pipeline: preflight -> toolkit.dispatch -> postwrite -> persisted XML -> load_check)")
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

    before_xml_routes = _read_decision_from_xml(am.working_path, decision_no)
    _log(f"      decision {decision_no} routes before (working copy XML): {before_xml_routes}")
    old_rel_flow_str = before_xml_routes[route_no]
    old_value = float(old_rel_flow_str.split(":")[-1])
    new_value = old_value + 500.0

    action = {
        "tool": "set_route_relative_flow",
        "params": {"decision_no": decision_no, "route_no": route_no, "rel_flow": new_value},
    }
    engine = GuardrailEngine()

    write_ok = False
    session = VissimEditSession(am.working_path)
    try:
        _log("[3.2] preflight (real GuardrailEngine, real cloned shadow model)...")
        pre_report = engine.preflight(action, before_shadow)
        pre_ids = [f.constraint_id for f in pre_report.findings]
        _log(f"      findings={pre_ids} blocking={pre_report.blocking}")
        _check("Preflight PASSES for the real route write", not pre_report.blocking, detail=str(pre_ids))
        if pre_report.blocking:
            return False

        set_active_edit_session(session)
        _log(
            f"[3.3] Dispatching set_route_relative_flow(decision_no={decision_no!r}, "
            f"route_no={route_no!r}, rel_flow={new_value!r}) via toolkit.dispatch (real COM)..."
        )
        envelope = dispatch(
            "set_route_relative_flow",
            {"decision_no": decision_no, "route_no": route_no, "rel_flow": new_value},
            registry=default_registry,
        )
        _log(f"      envelope = {envelope}")
        _check("dispatch() status == ok", envelope.get("status") == "ok", detail=str(envelope))
        verification = envelope.get("verification", {})
        _check(
            "written_value == readback_value == new_value (live COM read-back)",
            verification == {"written_value": new_value, "readback_value": new_value},
            detail=str(verification),
        )

        after_shadow = build_shadow_model(am.working_path)
        postwrite_report = engine.postwrite(action, before_shadow, after_shadow, verification)
        post_ids = [f.constraint_id for f in postwrite_report.findings]
        _log(f"[3.4] postwrite findings={post_ids} blocking={postwrite_report.blocking}")
        _check(
            "Postwrite has no write_verification_failed finding",
            "write_verification_failed" not in post_ids,
            detail=str(post_ids),
        )

        after_xml_routes = _read_decision_from_xml(am.working_path, decision_no)
        _log(f"[3.5] decision {decision_no} routes after (working copy XML, post-SaveNetAs): {after_xml_routes}")
        after_value_str = after_xml_routes[route_no]
        after_value = float(after_value_str.split(":")[-1])
        _check(
            "Persisted working-copy XML relFlow for the TARGET route == new_value",
            abs(after_value - new_value) < 1e-6,
            detail=f"expected {new_value!r}, got {after_value_str!r}",
        )

        siblings_unchanged = all(
            after_xml_routes[no] == before_xml_routes[no]
            for no in before_xml_routes
            if no != route_no
        )
        _check(
            "Persisted XML: EVERY SIBLING route's relFlow byte-for-byte UNCHANGED "
            "(the central empirical finding: Vissim does not renormalize on write)",
            siblings_unchanged,
            detail=f"before={before_xml_routes} after={after_xml_routes}",
        )

        write_ok = (
            envelope.get("status") == "ok"
            and abs(after_value - new_value) < 1e-6
            and siblings_unchanged
            and not pre_report.blocking
        )
    finally:
        clear_active_edit_session()
        session.close()
        _log("[3.6] Vissim edit session closed.")

    if not write_ok:
        return False

    _log("[3.7] load_check on the SAME working copy -- must still be clean after the route-flow edit...")
    set_active_run_source(am.working_path)
    try:
        check_envelope = dispatch("load_check", {}, registry=default_registry)
    finally:
        clear_active_run_source()
    _log(f"      load_check envelope = {check_envelope}")
    _check("load_check dispatch status == ok", check_envelope.get("status") == "ok", detail=str(check_envelope))
    check_verification = check_envelope.get("verification", {})
    _check(
        "load_check reports load_ok=True, init_ok=True after the edit",
        check_verification == {"load_ok": True, "init_ok": True},
        detail=str(check_verification),
    )

    return write_ok and check_verification == {"load_ok": True, "init_ok": True}


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")
    example_inpx = _find_inpx(example_dir)

    phase_1_static_parse(example_inpx)
    phase2_result = phase_2_real_preflight(example_inpx)
    phase_3_ok = phase_3_live_com_write(
        example_inpx, phase2_result["decision_no"], phase2_result["route_no"]
    )

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
    main()

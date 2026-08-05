"""LIVE ACCEPTANCE for QUEUE 3 (v2a) unit 3: set_des_speed_distribution_points'
ATOMIC full-curve replace end to end on a Roundabout Billings.US clone --
preflight -> dispatch (real COM, real unit conversion) -> COM readback match
-> persisted evidence (XML grep, genuine km/h, not display-unit-scaled) ->
fresh COM reload -> match -> load_check still clean afterward.

[Phase 1] Static parse -- no COM. Parses the REAL example-network XML (a
fresh clone of Roundabout Billings.US -- the SAME network v2a unit ⑧'s own
des-speed-decision probe/live-check used) directly through
``services.agent.tools_network.build_shadow_model`` and asserts
``des_speed_distributions`` carries real points + reference counts (13 real
distributions, several multi-point, several referenced by desSpeedDecisions).

[Phase 2] Real preflight path -- no COM. Clones Roundabout Billings.US via a
real ``ArtifactManager``, builds real action dicts, and runs them through the
REAL ``GuardrailEngine().preflight()``:
  - a bogus distribution_no REFUSES via object_not_found.
  - fewer than 2 points REFUSES via des_speed_distribution_points_count_invalid.
  - a negative speed REFUSES via des_speed_distribution_points_speed_negative.
  - a genuine speed decrease REFUSES via
    des_speed_distribution_points_speed_not_monotonic (Vissim itself silently
    accepts this -- proves OUR screen catches what Vissim won't).
  - a last-point fraction != 1 REFUSES via
    des_speed_distribution_points_fraction_invalid.
  - an absurd speed WARNS via des_speed_distribution_points_speed_implausible
    without blocking.
  - a real, VALID curve on a SHARED distribution (no=25, referenced by
    desSpeedDecision no=10) PASSES and carries
    des_speed_distribution_points_shared_target.
  - the SAME curve on a genuinely UNREFERENCED distribution (discovered live
    from the real shadow model, not guessed) PASSES with NO shared_target
    warning.

[Phase 3] ONE live COM ATOMIC REPLACE, full pipeline (preflight -> toolkit.
dispatch -> postwrite -> persisted XML -> fresh COM reload -> load_check),
against a CLONE of Roundabout Billings.US (never the original .inpx):
set_des_speed_distribution_points on distribution no=25 (originally 4 points,
"25 mph"), replaced with a brand-new 3-point curve
[(0, 0), (20, 0.5), (40, 1.0)] -- speeds given in genuine km/h. Proves:
  - preflight lets the write through with the expected shared_target warning.
  - the COM write+readback succeeds and reports genuine km/h (not the
    network's own MILESPERHOUR display unit -- Roundabout Billings.US is the
    SAME imperial-display network unit ⑧'s own probe found the Pos landmine
    on, and this unit's own probe found the SAME display-unit translation
    for speed-distribution X).
  - the persisted XML shows EXACTLY x="0"/x="20"/x="40" (genuine km/h, NOT
    32.19/64.37 -- the mph-to-km/h-scaled values a units bug would produce).
  - a fresh COM reload of the saved file reports the same 3-point curve.
  - load_check on that same working copy still comes back clean (load_ok=True,
    init_ok=True) -- the atomic curve replace did not break the network.

Always closes every Vissim instance in a ``finally`` block;
attach_to_running=False throughout (via VissimEditSession / load_check's own
connect, per 00-EXECUTION-GUIDE.md rule 3). Never touches the original
Examples Demo folder.

Usage: python tools/live_check_des_speed_distribution_points.py
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
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Roundabout Billings.US"
)
WORK_ROOT = REPO_ROOT / "runtime" / "des-speed-distribution-points-live-check"

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


def phase_1_static_parse(example_inpx: Path) -> dict[str, Any]:
    _log("=" * 78)
    _log("[Phase 1] tools_network.build_shadow_model against the real Roundabout Billings.US XML")
    _log("=" * 78)

    from services.agent.tools_network import build_shadow_model

    shadow = build_shadow_model(example_inpx)
    distributions = shadow.get("des_speed_distributions") or []
    _log(f"[1.1] des_speed_distributions count = {len(distributions)}")
    _check("des_speed_distributions is populated (not None/empty)", bool(distributions))
    _check(
        "des_speed_distributions count == 13 (matches unit ⑧'s own probe's XML grep)",
        len(distributions) == 13,
        detail=str(sorted(d["no"] for d in distributions)),
    )

    dist_25 = next((d for d in distributions if d["no"] == "25"), None)
    _check("distribution no=25 present", dist_25 is not None)
    if dist_25 is not None:
        _log(f"       distribution 25: {dist_25}")
        _check("distribution 25 has 4 points", dist_25["point_count"] == 4, detail=str(dist_25["points"]))
        _check(
            "distribution 25 is referenced by >= 1 desSpeedDecision entry",
            dist_25["referenced_by"]["des_speed_decisions"]["count"] >= 1,
            detail=str(dist_25["referenced_by"]),
        )

    unreferenced = next((d for d in distributions if d["reference_count"] == 0), None)
    _check(
        "at least one genuinely UNREFERENCED distribution exists on this network",
        unreferenced is not None,
        detail=str([d["no"] for d in distributions]),
    )
    if unreferenced is not None:
        _log(f"       genuinely unreferenced distribution: no={unreferenced['no']} name={unreferenced['name']!r}")

    return {"unreferenced_no": unreferenced["no"] if unreferenced is not None else None}


# ---------------------------------------------------------------------------
# Phase 2: real preflight path (clone + GuardrailEngine, no COM)
# ---------------------------------------------------------------------------


def phase_2_real_preflight(example_inpx: Path, unreferenced_no: str | None) -> None:
    _log("")
    _log("=" * 78)
    _log("[Phase 2] Real preflight path: ArtifactManager.clone() -> GuardrailEngine().preflight()")
    _log("=" * 78)

    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager

    engine = GuardrailEngine()

    def _fresh_shadow(suffix: str):
        work_dir = _fresh_work_dir(suffix)
        am = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir)
        return am.clone()

    def _pts(pairs: list[tuple[float, float]]) -> list[dict[str, float]]:
        return [{"speed_kmh": s, "cum_fraction": f} for s, f in pairs]

    valid_curve = _pts([(0.0, 0.0), (20.0, 0.5), (40.0, 1.0)])

    shadow = _fresh_shadow("phase2-bogus-distr")
    action = {
        "tool": "set_des_speed_distribution_points",
        "params": {"distribution_no": "999999", "points": valid_curve},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.1] Bogus distribution_no=999999 preflight findings: {ids} (blocking={report.blocking})")
    _check("bogus distribution_no REFUSES via object_not_found", "object_not_found" in ids and report.blocking, detail=str(ids))

    shadow = _fresh_shadow("phase2-too-few-points")
    action = {
        "tool": "set_des_speed_distribution_points",
        "params": {"distribution_no": "25", "points": _pts([(50.0, 1.0)])},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.2] 1-point curve preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "fewer than 2 points REFUSES via des_speed_distribution_points_count_invalid",
        "des_speed_distribution_points_count_invalid" in ids and report.blocking,
        detail=str(ids),
    )

    shadow = _fresh_shadow("phase2-negative-speed")
    action = {
        "tool": "set_des_speed_distribution_points",
        "params": {"distribution_no": "25", "points": _pts([(-10.0, 0.0), (50.0, 1.0)])},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.3] Negative speed preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "negative speed REFUSES via des_speed_distribution_points_speed_negative",
        "des_speed_distribution_points_speed_negative" in ids and report.blocking,
        detail=str(ids),
    )

    shadow = _fresh_shadow("phase2-speed-decrease")
    action = {
        "tool": "set_des_speed_distribution_points",
        "params": {"distribution_no": "25", "points": _pts([(0.0, 0.0), (100.0, 0.5), (50.0, 1.0)])},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.4] Speed-decrease curve preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "genuine speed decrease REFUSES via des_speed_distribution_points_speed_not_monotonic "
        "(Vissim itself silently accepts this live -- see the probe's own silent-failure map)",
        "des_speed_distribution_points_speed_not_monotonic" in ids and report.blocking,
        detail=str(ids),
    )

    shadow = _fresh_shadow("phase2-bad-last-fx")
    action = {
        "tool": "set_des_speed_distribution_points",
        "params": {"distribution_no": "25", "points": _pts([(0.0, 0.0), (50.0, 0.9)])},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.5] Last-point fraction != 1 preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "last-point fraction != 1 REFUSES via des_speed_distribution_points_fraction_invalid",
        "des_speed_distribution_points_fraction_invalid" in ids and report.blocking,
        detail=str(ids),
    )

    shadow = _fresh_shadow("phase2-absurd-speed")
    action = {
        "tool": "set_des_speed_distribution_points",
        "params": {"distribution_no": "25", "points": _pts([(0.0, 0.0), (99999.0, 1.0)])},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.6] Absurd speed preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "absurd speed WARNS via des_speed_distribution_points_speed_implausible without blocking",
        "des_speed_distribution_points_speed_implausible" in ids and not report.blocking,
        detail=str(ids),
    )

    shadow = _fresh_shadow("phase2-shared-target")
    action = {
        "tool": "set_des_speed_distribution_points",
        "params": {"distribution_no": "25", "points": valid_curve},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.7] Real, valid curve on SHARED distribution 25 preflight findings: {ids} (blocking={report.blocking})")
    _check("valid curve on a shared distribution PASSES (not blocking)", not report.blocking, detail=str(ids))
    _check(
        "shared distribution carries des_speed_distribution_points_shared_target warning",
        "des_speed_distribution_points_shared_target" in ids,
        detail=str(ids),
    )

    if unreferenced_no is not None:
        shadow = _fresh_shadow("phase2-unreferenced")
        action = {
            "tool": "set_des_speed_distribution_points",
            "params": {"distribution_no": unreferenced_no, "points": valid_curve},
        }
        report = engine.preflight(action, shadow)
        ids = [f.constraint_id for f in report.findings]
        _log(
            f"[2.8] Real, valid curve on UNREFERENCED distribution {unreferenced_no} preflight "
            f"findings: {ids} (blocking={report.blocking})"
        )
        _check(
            "unreferenced distribution does NOT carry the shared_target warning",
            "des_speed_distribution_points_shared_target" not in ids,
            detail=str(ids),
        )


# ---------------------------------------------------------------------------
# Phase 3: ONE live COM atomic-replace write, then load_check
# ---------------------------------------------------------------------------


def _read_distribution_points_from_xml(inpx_path: Path, distribution_no: str) -> list[tuple[str, str]] | None:
    root = ET.parse(inpx_path).getroot()
    for d in root.findall(".//speedDistributions/speedDistribution"):
        if d.get("no") == distribution_no:
            return [
                (p.get("x"), p.get("fx")) for p in d.findall("speedDistrDatPts/speedDistributionDataPoint")
            ]
    return None


def phase_3_live_com_write(example_inpx: Path) -> bool:
    _log("")
    _log("=" * 78)
    _log(
        "[Phase 3] ONE live COM ATOMIC REPLACE: set_des_speed_distribution_points("
        "distribution_no='25', points=[(0,0),(20,0.5),(40,1.0)] km/h) on a CLONE of "
        "Roundabout Billings.US (MILESPERHOUR display unit -- the units landmine's own network)"
    )
    _log("           (full pipeline: preflight -> toolkit.dispatch -> postwrite -> persisted XML -> fresh COM reload -> load_check)")
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
    _log(f"[3.1] Cloned Roundabout Billings.US -> {am.working_path} (never the original .inpx)")

    before_points = _read_distribution_points_from_xml(am.working_path, "25")
    _log(f"      distribution 25 BEFORE (working copy XML, genuine km/h): {before_points}")

    new_points = [
        {"speed_kmh": 0.0, "cum_fraction": 0.0},
        {"speed_kmh": 20.0, "cum_fraction": 0.5},
        {"speed_kmh": 40.0, "cum_fraction": 1.0},
    ]
    params = {"distribution_no": "25", "points": new_points}
    action = {"tool": "set_des_speed_distribution_points", "params": params}
    engine = GuardrailEngine()

    write_ok = False
    session = VissimEditSession(am.working_path)
    try:
        _log("[3.2] preflight (real GuardrailEngine, real cloned shadow model)...")
        pre_report = engine.preflight(action, before_shadow)
        pre_ids = [f.constraint_id for f in pre_report.findings]
        _log(f"      findings={pre_ids} blocking={pre_report.blocking}")
        _check(
            "Preflight PASSES for the real atomic curve replace (shared_target warns but never blocks)",
            not pre_report.blocking,
            detail=str(pre_ids),
        )
        if pre_report.blocking:
            return False

        set_active_edit_session(session)
        _log("[3.3] Dispatching set_des_speed_distribution_points via toolkit.dispatch (real COM)...")
        envelope = dispatch("set_des_speed_distribution_points", params, registry=default_registry)
        _log(f"      envelope = {envelope}")
        _check("dispatch() status == ok", envelope.get("status") == "ok", detail=str(envelope))
        verification = envelope.get("verification", {})
        _check(
            "written_value == readback_value == the intended 3-point curve (genuine km/h)",
            verification.get("written_value") == new_points
            and verification.get("readback_value") == new_points,
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

        after_points = _read_distribution_points_from_xml(am.working_path, "25")
        _log(f"[3.5] distribution 25 AFTER (working copy XML, post-SaveNetAs): {after_points}")
        _check(
            "Persisted XML shows EXACTLY x=0/20/40 (genuine km/h -- NOT display-unit-scaled "
            "32.19/64.37, which a units bug would produce)",
            after_points is not None
            and {round(float(x), 3) for x, _ in after_points} == {0.0, 20.0, 40.0},
            detail=str(after_points),
        )
        _check(
            "Persisted XML fractions are EXACTLY 0/0.5/1",
            after_points is not None
            and {round(float(fx), 3) for _, fx in after_points} == {0.0, 0.5, 1.0},
            detail=str(after_points),
        )

        write_ok = (
            envelope.get("status") == "ok"
            and after_points is not None
            and {round(float(x), 3) for x, _ in after_points} == {0.0, 20.0, 40.0}
            and not pre_report.blocking
            and "write_verification_failed" not in post_ids
        )
    finally:
        clear_active_edit_session()
        session.close()
        _log("[3.6] Vissim edit session closed.")

    if not write_ok:
        return False

    _log("[3.7] Fresh COM reload of the SAVED file (separate connection) -- confirms the curve survived a full round trip...")
    verify_controller = None
    try:
        import pythoncom

        pythoncom.CoInitialize()
        from services.vissim_com import VissimController

        verify_controller = VissimController()
        verify_controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        verify_controller.load_network(str(am.working_path))
        reload_points = verify_controller.get_des_speed_distribution_points(25)
        _log(f"      reload distribution 25 points (genuine km/h): {reload_points}")
        _check(
            "Fresh COM reload reports the SAME 3-point curve (genuine km/h)",
            {round(p["speed_kmh"], 3) for p in reload_points} == {0.0, 20.0, 40.0},
            detail=str(reload_points),
        )
    finally:
        if verify_controller is not None:
            try:
                verify_controller.disconnect()
            except Exception:
                pass
        try:
            import pythoncom

            pythoncom.CoUninitialize()
        except Exception:
            pass

    _log("[3.8] load_check on the SAME working copy -- must still be clean after the atomic curve replace...")
    set_active_run_source(am.working_path)
    try:
        check_envelope = dispatch("load_check", {}, registry=default_registry)
    finally:
        clear_active_run_source()
    _log(f"      load_check envelope = {check_envelope}")
    _check("load_check dispatch status == ok", check_envelope.get("status") == "ok", detail=str(check_envelope))
    check_verification = check_envelope.get("verification", {})
    _check(
        "load_check reports load_ok=True, init_ok=True after the atomic curve replace",
        check_verification == {"load_ok": True, "init_ok": True},
        detail=str(check_verification),
    )

    return write_ok and check_verification == {"load_ok": True, "init_ok": True}


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")
    example_inpx = _find_inpx(example_dir)

    phase1_result = phase_1_static_parse(example_inpx)
    phase_2_real_preflight(example_inpx, phase1_result.get("unreferenced_no"))
    phase_3_ok = phase_3_live_com_write(example_inpx)

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
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()

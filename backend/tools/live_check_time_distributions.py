"""LIVE ACCEPTANCE for QUEUE (Q3, v2a) unit 5: set_time_distribution_normal /
set_time_distribution_points end to end on a "BRT Priority Texas.US" clone --
preflight -> dispatch (real COM) -> COM readback match -> persisted evidence
(XML grep) -> fresh COM reload -> match -> load_check still clean.

Mirrors live_check_des_speed_distribution_points.py's own three-phase shape
exactly. "Roundabout Billings.US" (every prior QUEUE-3/v2a unit's live
target) has ZERO PT stops -- unsuitable here. "Signalization RBC Boise.US"
(this unit's own static scan's richest-PT-network pick) RAISES on LoadNet
itself ("The network file could not be loaded.") -- a NEW landmine (see
probe_dwell_time_distributions.py's own module docstring). "BRT Priority
Texas.US" substituted: loads cleanly, has both NORMAL and EMPIRICAL time
distributions, and real dwellTmDistr/w99cc1Distr/zipperOvtTmDistr references.

Always closes every Vissim instance in a ``finally`` block;
attach_to_running=False throughout. Never touches the original Examples
Demo folder.

Usage: python tools/live_check_time_distributions.py
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
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\BRT Priority Texas.US"
)
WORK_ROOT = REPO_ROOT / "runtime" / "time-distribution-live-check"

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


def _read_time_distribution_from_xml(inpx_path: Path, no: str) -> dict[str, Any] | None:
    root = ET.parse(inpx_path).getroot()
    for td in root.findall(".//timeDistributions/timeDistribution"):
        if td.get("no") == no:
            points = [
                (p.get("x"), p.get("fx"))
                for p in td.findall("durDistrDataPts/durationDistributionDataPoint")
            ]
            return {**dict(td.attrib), "points": points}
    return None


# ---------------------------------------------------------------------------
# Phase 1: static parse
# ---------------------------------------------------------------------------


def phase_1_static_parse(example_inpx: Path) -> None:
    _log("=" * 78)
    _log("[Phase 1] tools_network.build_shadow_model against the real BRT Priority Texas.US XML")
    _log("=" * 78)

    from services.agent.tools_network import build_shadow_model

    shadow = build_shadow_model(example_inpx)
    distributions = shadow.get("time_distributions") or []
    _log(f"[1.1] time_distributions count = {len(distributions)}")
    _check("time_distributions is populated", bool(distributions))
    _check("time_distributions count == 20 (matches the probe's own XML grep)", len(distributions) == 20)

    normal_20 = next((d for d in distributions if d["no"] == "20"), None)
    _check("distribution no=20 (NORMAL, referenced by ptLineStop) present", normal_20 is not None)
    if normal_20 is not None:
        _log(f"       distribution 20: {normal_20}")
        _check("distribution 20 is NORMAL", normal_20["type"] == "NORMAL")
        _check(
            "distribution 20 is referenced by >= 1 pt_stops entry",
            normal_20["referenced_by"]["pt_stops"]["count"] >= 1,
            detail=str(normal_20["referenced_by"]),
        )

    empirical_200 = next((d for d in distributions if d["no"] == "200"), None)
    _check("distribution no=200 (EMPIRICAL, zipperOvtTmDistr) present", empirical_200 is not None)
    if empirical_200 is not None:
        _check("distribution 200 is EMPIRICAL", empirical_200["type"] == "EMPIRICAL")
        _check("distribution 200 has 2 points", empirical_200["point_count"] == 2)
        _check(
            "distribution 200 is referenced by >= 1 driving_behaviors entry (zipper)",
            empirical_200["referenced_by"]["driving_behaviors"]["count"] >= 1,
            detail=str(empirical_200["referenced_by"]),
        )

    unreferenced = next((d for d in distributions if d["reference_count"] == 0), None)
    _check("at least one genuinely UNREFERENCED distribution exists", unreferenced is not None)
    if unreferenced is not None:
        _log(f"       genuinely unreferenced distribution: no={unreferenced['no']} name={unreferenced['name']!r}")


# ---------------------------------------------------------------------------
# Phase 2: real preflight path (clone + GuardrailEngine, no COM)
# ---------------------------------------------------------------------------


def phase_2_real_preflight(example_inpx: Path) -> None:
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

    shadow = _fresh_shadow("phase2-bogus-target")
    action = {
        "tool": "set_time_distribution_normal",
        "params": {"time_distribution_no": "999999", "mean_s": 30.0},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.1] Bogus time_distribution_no=999999 preflight findings: {ids} (blocking={report.blocking})")
    _check("bogus target REFUSES via object_not_found", "object_not_found" in ids and report.blocking, detail=str(ids))

    shadow = _fresh_shadow("phase2-type-mismatch")
    action = {
        "tool": "set_time_distribution_normal",
        "params": {"time_distribution_no": "200", "mean_s": 30.0},  # 200 is EMPIRICAL
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.2] set_time_distribution_normal on EMPIRICAL target=200 findings: {ids} (blocking={report.blocking})")
    _check(
        "wrong-type target REFUSES via time_distribution_type_mismatch",
        "time_distribution_type_mismatch" in ids and report.blocking,
        detail=str(ids),
    )

    shadow = _fresh_shadow("phase2-negative-mean")
    action = {
        "tool": "set_time_distribution_normal",
        "params": {"time_distribution_no": "20", "mean_s": -5.0},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.3] Negative mean preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "negative mean REFUSES via time_distribution_mean_or_stddev_negative",
        "time_distribution_mean_or_stddev_negative" in ids and report.blocking,
        detail=str(ids),
    )

    shadow = _fresh_shadow("phase2-shared-target")
    action = {
        "tool": "set_time_distribution_normal",
        "params": {"time_distribution_no": "20", "mean_s": 22.0},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.4] Real, valid write on SHARED distribution 20 findings: {ids} (blocking={report.blocking})")
    _check("valid write on a shared distribution PASSES (not blocking)", not report.blocking, detail=str(ids))
    _check(
        "shared distribution carries time_distribution_shared_target warning",
        "time_distribution_shared_target" in ids,
        detail=str(ids),
    )

    shadow = _fresh_shadow("phase2-points-too-few")
    action = {
        "tool": "set_time_distribution_points",
        "params": {"time_distribution_no": "200", "points": [{"duration_s": 5.0, "cum_fraction": 1.0}]},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[2.5] 1-point curve preflight findings: {ids} (blocking={report.blocking})")
    _check(
        "fewer than 2 points REFUSES via time_distribution_points_count_invalid",
        "time_distribution_points_count_invalid" in ids and report.blocking,
        detail=str(ids),
    )


# ---------------------------------------------------------------------------
# Phase 3: TWO live COM writes (normal + points), then load_check
# ---------------------------------------------------------------------------


def phase_3_live_com_write(example_inpx: Path) -> bool:
    _log("")
    _log("=" * 78)
    _log(
        "[Phase 3] TWO live COM writes: set_time_distribution_normal(no=550, mean_s=22.0, "
        "std_dev_s=4.0) + set_time_distribution_points(no=200, [(0,0),(2,0.4),(5,1.0)]) on a "
        "CLONE of BRT Priority Texas.US"
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
    _log(f"[3.1] Cloned BRT Priority Texas.US -> {am.working_path} (never the original .inpx)")

    before_normal = _read_time_distribution_from_xml(am.working_path, "550")
    before_empirical = _read_time_distribution_from_xml(am.working_path, "200")
    _log(f"      distribution 550 BEFORE: {before_normal}")
    _log(f"      distribution 200 BEFORE: {before_empirical}")

    normal_params = {"time_distribution_no": "550", "mean_s": 22.0, "std_dev_s": 4.0}
    normal_action = {"tool": "set_time_distribution_normal", "params": normal_params}
    new_points = [
        {"duration_s": 0.0, "cum_fraction": 0.0},
        {"duration_s": 2.0, "cum_fraction": 0.4},
        {"duration_s": 5.0, "cum_fraction": 1.0},
    ]
    points_params = {"time_distribution_no": "200", "points": new_points}
    points_action = {"tool": "set_time_distribution_points", "params": points_params}
    engine = GuardrailEngine()

    write_ok = False
    session = VissimEditSession(am.working_path)
    try:
        _log("[3.2] preflight for BOTH actions (real GuardrailEngine, real cloned shadow model)...")
        pre_normal = engine.preflight(normal_action, before_shadow)
        pre_points = engine.preflight(points_action, before_shadow)
        _log(f"      normal findings={[f.constraint_id for f in pre_normal.findings]} blocking={pre_normal.blocking}")
        _log(f"      points findings={[f.constraint_id for f in pre_points.findings]} blocking={pre_points.blocking}")
        _check("Preflight PASSES for the real mean/stddev write", not pre_normal.blocking)
        _check("Preflight PASSES for the real points write", not pre_points.blocking)
        if pre_normal.blocking or pre_points.blocking:
            return False

        set_active_edit_session(session)

        _log("[3.3] Dispatching set_time_distribution_normal via toolkit.dispatch (real COM)...")
        normal_envelope = dispatch("set_time_distribution_normal", normal_params, registry=default_registry)
        _log(f"      envelope = {normal_envelope}")
        _check("set_time_distribution_normal dispatch() status == ok", normal_envelope.get("status") == "ok", detail=str(normal_envelope))
        normal_verification = normal_envelope.get("verification", {})
        _check(
            "normal written_value == readback_value == the intended pair",
            normal_verification.get("written_value") == {"mean_s": 22.0, "std_dev_s": 4.0}
            and normal_verification.get("readback_value") == {"mean_s": 22.0, "std_dev_s": 4.0},
            detail=str(normal_verification),
        )

        # Snapshot the shadow model IMMEDIATELY after the FIRST write (before
        # the SECOND write happens) -- postwrite's "nothing else moved" diff
        # must compare each write against ITS OWN before/after pair, exactly
        # as production does (one postwrite call per dispatch, back to
        # back), never a before/after pair spanning TWO separate writes
        # (which would make the SECOND write's own change look like
        # "unrelated drift" against the FIRST write's diff, and vice versa).
        mid_shadow = build_shadow_model(am.working_path)
        post_normal = engine.postwrite(normal_action, before_shadow, mid_shadow, normal_verification)
        _log(f"[3.4b] postwrite (normal) findings={[f.constraint_id for f in post_normal.findings]} blocking={post_normal.blocking}")
        _check(
            "Postwrite (normal) has no write_verification_failed finding",
            "write_verification_failed" not in [f.constraint_id for f in post_normal.findings],
            detail=str([f.constraint_id for f in post_normal.findings]),
        )

        _log("[3.4c] Dispatching set_time_distribution_points via toolkit.dispatch (real COM)...")
        points_envelope = dispatch("set_time_distribution_points", points_params, registry=default_registry)
        _log(f"      envelope = {points_envelope}")
        _check("set_time_distribution_points dispatch() status == ok", points_envelope.get("status") == "ok", detail=str(points_envelope))
        points_verification = points_envelope.get("verification", {})
        _check(
            "points written_value == readback_value == the intended curve",
            points_verification.get("written_value") == new_points
            and points_verification.get("readback_value") == new_points,
            detail=str(points_verification),
        )

        after_shadow = build_shadow_model(am.working_path)
        post_points = engine.postwrite(points_action, mid_shadow, after_shadow, points_verification)
        _log(f"[3.5] postwrite (points) findings={[f.constraint_id for f in post_points.findings]} blocking={post_points.blocking}")
        _check(
            "Postwrite (points) has no write_verification_failed finding",
            "write_verification_failed" not in [f.constraint_id for f in post_points.findings],
            detail=str([f.constraint_id for f in post_points.findings]),
        )

        after_normal = _read_time_distribution_from_xml(am.working_path, "550")
        after_empirical = _read_time_distribution_from_xml(am.working_path, "200")
        _log(f"[3.6] distribution 550 AFTER (working copy XML, post-SaveNetAs): {after_normal}")
        _log(f"      distribution 200 AFTER (working copy XML, post-SaveNetAs): {after_empirical}")
        _check(
            "Persisted XML shows mean=22 stdDev=4 for no=550",
            after_normal is not None and after_normal.get("mean") == "22" and after_normal.get("stdDev") == "4",
            detail=str(after_normal),
        )
        _check(
            "Persisted XML shows EXACTLY (0,0)/(2,0.4)/(5,1) for no=200",
            after_empirical is not None
            and {(round(float(x), 3), round(float(fx), 3)) for x, fx in after_empirical["points"]}
            == {(0.0, 0.0), (2.0, 0.4), (5.0, 1.0)},
            detail=str(after_empirical),
        )

        write_ok = (
            normal_envelope.get("status") == "ok"
            and points_envelope.get("status") == "ok"
            and after_normal is not None
            and after_normal.get("mean") == "22"
            and after_empirical is not None
            and not pre_normal.blocking
            and not pre_points.blocking
            and "write_verification_failed" not in [f.constraint_id for f in post_normal.findings]
            and "write_verification_failed" not in [f.constraint_id for f in post_points.findings]
        )
    finally:
        clear_active_edit_session()
        session.close()
        _log("[3.7] Vissim edit session closed.")

    if not write_ok:
        return False

    _log("[3.8] Fresh COM reload of the SAVED file (separate connection) -- confirms both writes survived a full round trip...")
    verify_controller = None
    try:
        import pythoncom

        pythoncom.CoInitialize()
        from services.vissim_com import VissimController

        verify_controller = VissimController()
        verify_controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        verify_controller.load_network(str(am.working_path))
        reload_normal = verify_controller.get_time_distribution(550)
        reload_points = verify_controller.get_time_distribution_points(200)
        _log(f"      reload distribution 550: {reload_normal}")
        _log(f"      reload distribution 200 points: {reload_points}")
        _check(
            "Fresh COM reload reports mean_s=22.0 std_dev_s=4.0 for no=550",
            reload_normal["mean_s"] == 22.0 and reload_normal["std_dev_s"] == 4.0,
            detail=str(reload_normal),
        )
        _check(
            "Fresh COM reload reports the SAME 3-point curve for no=200",
            {(round(p["duration_s"], 3), round(p["cum_fraction"], 3)) for p in reload_points}
            == {(0.0, 0.0), (2.0, 0.4), (5.0, 1.0)},
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

    _log("[3.9] load_check on the SAME working copy -- must still be clean after both writes...")
    set_active_run_source(am.working_path)
    try:
        check_envelope = dispatch("load_check", {}, registry=default_registry)
    finally:
        clear_active_run_source()
    _log(f"      load_check envelope = {check_envelope}")
    _check("load_check dispatch status == ok", check_envelope.get("status") == "ok", detail=str(check_envelope))
    check_verification = check_envelope.get("verification", {})
    _check(
        "load_check reports load_ok=True, init_ok=True after both writes",
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
    phase_2_real_preflight(example_inpx)
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

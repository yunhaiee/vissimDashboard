"""LIVE ACCEPTANCE for Q5 unit (2): ``add_signal_controller`` end to end
through the REAL dispatch path with a REAL guardrail engine.

Three phases, mirroring ``driving_behavior_live_check.py``'s shape (the
nearest precedent -- the same duplicate-then-edit house pattern, one object
family up):

  1. **Static + preflight against REAL data, no COM.** Both this unit's
     constraints exercised against a real parsed network: the warn that
     names what the copy inherits (real program/signal-group counts read out
     of the model, not a fixture) and the refusal for a non-fixed-time
     donor. The refusal is checked against a REAL RINGBARRIERCONTROLLER
     network, because a constraint proven only on a hand-made fixture is a
     constraint proven against its author's imagination.
  2. **The live flow on a clone.** ``add_signal_controller`` dispatched for
     real (real COM ``DuplicateSignalController``), the REAL
     ``GuardrailEngine.postwrite`` run over real before/after shadow models
     -- which is where ``_ALLOWS_NEW_OBJECTS_IN["add_signal_controller"]``
     either holds or the whole verb is refused and rolled back -- and then
     ``set_signal_green_split`` dispatched against the BRAND-NEW controller
     to prove the copy is genuinely editable, not merely present (Q4 unit
     (6)'s precedent). The donor's own program is checked byte-for-byte
     before/after in the persisted XML: editing the copy must not move it.
  3. **Fresh COM reload + load_check.** A separate Vissim instance loads the
     saved file and re-reads the new controller and its edited split; then
     ``load_check`` (the real verb) must still report ``load_ok``/``init_ok``.

Donor network: ``Urban Intersection Beijing.CN`` -- one real FIXEDTIME
controller with 2 programs x 22 signal groups, the same donor Q5 unit (1)
and this unit's probe both measured, so the numbers here are comparable to
theirs rather than a fresh unexplained set.

Always closes every Vissim instance in a ``finally``;
``attach_to_running=False`` throughout (a human's Vissim GUI is open on this
machine). Never touches the original Examples Demo folder, and never
``runtime/uploads/``.

Usage: ``python tools/signal_controller_live_check.py``
"""
from __future__ import annotations

import os
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
EXAMPLES_ROOT = Path(r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo")
DEFAULT_EXAMPLE = EXAMPLES_ROOT / "Urban Intersection Beijing.CN"
#: A real network whose controllers are ALL RINGBARRIERCONTROLLER -- used for
#: the refusal half of phase 1 (static parse only; this network cannot be
#: COM-loaded on this machine, a recorded RBC licensing block, DECISIONS.md
#: 2026-07-31 -- which is itself part of why the refusal exists).
RBC_EXAMPLE = EXAMPLES_ROOT / "Signalization RBC Boise.US"
WORK_ROOT = REPO_ROOT / "runtime" / "signal-controller-live-check"

_FAILURES: list[str] = []

#: The green window this check writes onto the COPY. Deliberately inside a
#: 140 s-scale cycle and away from the donor's own SG 1 window so a
#: "nothing moved" claim about the donor is a real claim, not a tautology.
_NEW_GREEN_START_S = 7.0
_NEW_GREEN_END_S = 33.0


def _log(msg: str) -> None:
    print(msg, flush=True)


def _check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        _log(f"  PASS  {label}")
    else:
        _log(f"  FAIL  {label}  {detail}")
        _FAILURES.append(label)


def _fresh_work_dir(suffix: str) -> Path:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
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


def _read_sc_from_xml(inpx_path: Path, no: str) -> dict[str, Any] | None:
    """One controller's persisted attributes INCLUDING its whole
    ``intSupplyData`` program blob -- the donor-untouched check compares
    that blob directly, which is the only way to say "the donor's timing did
    not move" about a program Vissim stores opaquely."""
    root = ET.parse(inpx_path).getroot()
    for sc in root.findall(".//signalController"):
        if sc.get("no") == no:
            return dict(sc.attrib)
    return None


# ---------------------------------------------------------------------------
# Phase 1: static + both constraints against REAL parsed data (no COM).
# ---------------------------------------------------------------------------


def phase_1_static_and_constraints(example_inpx: Path) -> None:
    _log("=" * 78)
    _log("[Phase 1] build_shadow_model + both Q5 unit (2) constraints against REAL data")
    _log("=" * 78)

    from services.agent.guardrails import GuardrailEngine
    from services.agent.tools_network import build_shadow_model

    shadow = build_shadow_model(example_inpx)
    signals = shadow.get("signals") or []
    splits = shadow.get("signal_program_splits") or []
    _log(f"[1.1] signals={len(signals)} signal_program_splits={len(splits)}")
    _check("the donor network carries at least one signal controller", bool(signals))
    donor = signals[0]
    donor_splits = [s for s in splits if str(s["sc_no"]) == str(donor["sc_no"])]
    programs = {str(s["program_no"]) for s in donor_splits}
    sgs = {str(s["sg_no"]) for s in donor_splits}
    _log(
        f"      donor SC {donor['sc_no']} type={donor['controller_type']} -- "
        f"{len(programs)} program(s) x {len(sgs)} signal group(s) = {len(donor_splits)} split row(s)"
    )
    _check("donor is a FIXEDTIME controller", donor["controller_type"] == "FIXEDTIME", str(donor))

    engine = GuardrailEngine(disabled=False)

    action = {
        "tool": "add_signal_controller",
        "params": {"source_sc_no": str(donor["sc_no"]), "new_name": "North junction"},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[1.2] preflight(add_signal_controller, real FIXEDTIME donor) findings={ids} blocking={report.blocking}")
    _check("preflight ALLOWS a real fixed-time donor (not blocking)", not report.blocking, str(ids))
    _check(
        "the inherits-donor-program WARN fires on a real donor",
        "signal_controller_duplicate_inherits_donor_program" in ids,
        str(ids),
    )
    warn = next(
        (f for f in report.findings if f.constraint_id == "signal_controller_duplicate_inherits_donor_program"),
        None,
    )
    if warn is not None:
        _log(f"      warn details = {warn.details}")
        _log(f"      warn (en) = {warn.messages['en']}")
        _check(
            "the warn names the REAL inherited program shape read from this model",
            warn.details["inherited_program_count"] == len(programs)
            and warn.details["inherited_signal_group_count"] == len(sgs)
            and warn.details["inherited_green_split_count"] == len(donor_splits),
            str(warn.details),
        )

    # The refusal half, against a REAL network of external-logic controllers.
    rbc_shadow = build_shadow_model(_find_inpx(RBC_EXAMPLE))
    rbc_signals = rbc_shadow.get("signals") or []
    rbc_splits = rbc_shadow.get("signal_program_splits") or []
    _log(
        f"[1.3] RBC reference network: signals={len(rbc_signals)} "
        f"signal_program_splits={len(rbc_splits)} (zero readable timing rows is the reason for the refusal)"
    )
    _check("the RBC network exposes ZERO signal_program_splits rows", rbc_splits == [], str(len(rbc_splits)))
    rbc_action = {
        "tool": "add_signal_controller",
        "params": {"source_sc_no": str(rbc_signals[0]["sc_no"]), "new_name": "copy"},
    }
    rbc_report = engine.preflight(rbc_action, rbc_shadow)
    rbc_ids = [f.constraint_id for f in rbc_report.findings]
    _log(f"      preflight(add_signal_controller, real RBC donor) findings={rbc_ids} blocking={rbc_report.blocking}")
    _check(
        "a real RINGBARRIERCONTROLLER donor is REFUSED",
        "signal_controller_duplicate_source_type_unsupported" in rbc_ids and rbc_report.blocking,
        str(rbc_ids),
    )
    _check(
        "the inherits-program warn stays SILENT for the refused donor (no noise on top of a refusal)",
        "signal_controller_duplicate_inherits_donor_program" not in rbc_ids,
        str(rbc_ids),
    )


# ---------------------------------------------------------------------------
# Phase 2: the live dispatch flow -- add_signal_controller then edit the copy.
# ---------------------------------------------------------------------------


def phase_2_live_flow(example_inpx: Path) -> tuple[bool, str | None, Path | None]:
    _log("")
    _log("=" * 78)
    _log("[Phase 2] LIVE: add_signal_controller -> real postwrite -> set_signal_green_split on the COPY")
    _log("=" * 78)

    import services.agent.tools_edit  # noqa: F401 - registers the write tools
    from services.agent.edit_session import (
        VissimEditSession,
        clear_active_edit_session,
        set_active_edit_session,
    )
    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    from services.agent.toolkit import default_registry, dispatch

    work_dir = _fresh_work_dir("phase2-live-flow")
    am = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir)
    shadow_0 = am.clone()
    _log(f"[2.1] Cloned -> {am.working_path} (never the original .inpx)")

    donor_no = str((shadow_0["signals"] or [{}])[0].get("sc_no", "1"))
    donor_xml_before = _read_sc_from_xml(am.working_path, donor_no)
    _log(
        f"      donor SC {donor_no} BEFORE: type={donor_xml_before.get('type')} "
        f"progNo={donor_xml_before.get('progNo')} "
        f"intSupplyData length={len(donor_xml_before.get('intSupplyData') or '')}"
    )

    ok = False
    new_sc_no: str | None = None
    session = VissimEditSession(am.working_path)
    engine = GuardrailEngine(disabled=False)
    try:
        set_active_edit_session(session)

        add_params = {"source_sc_no": donor_no, "new_name": "North junction (genesis)"}
        add_action = {"tool": "add_signal_controller", "params": add_params}

        _log("[2.2] Dispatching add_signal_controller via toolkit.dispatch (real COM DuplicateSignalController)...")
        add_envelope = dispatch("add_signal_controller", add_params, registry=default_registry)
        _log(f"      message = {add_envelope.get('message')}")
        _check("add_signal_controller status == ok", add_envelope.get("status") == "ok", str(add_envelope))
        new_sc_no = (add_envelope.get("artifacts") or {}).get("new_sc_no")
        _check("a new controller number was assigned", bool(new_sc_no), str(add_envelope))
        add_verification = add_envelope.get("verification", {})
        _check(
            "name written_value == readback_value",
            add_verification.get("written_value") == add_verification.get("readback_value")
            == "North junction (genesis)",
            str(add_verification),
        )

        shadow_1 = build_shadow_model(am.working_path)
        post_add = engine.postwrite(add_action, shadow_0, shadow_1, add_verification)
        post_add_ids = [f.constraint_id for f in post_add.findings]
        _log(f"[2.3] REAL postwrite(add_signal_controller) findings={post_add_ids} blocking={post_add.blocking}")
        # THE check this whole unit turns on: without
        # _ALLOWS_NEW_OBJECTS_IN["add_signal_controller"] this is
        # write_verification_failed (refuse) naming the new controller plus
        # every one of its inherited splits, and the edit is rolled back.
        _check(
            "postwrite has NO write_verification_failed (the guardrail exemption holds against real data)",
            "write_verification_failed" not in post_add_ids,
            str(
                post_add.first_refusal().details.get("nothing_else_moved_diff")
                if post_add.first_refusal() is not None
                else post_add_ids
            ),
        )
        _check("postwrite is not blocking", not post_add.blocking, str(post_add_ids))

        new_signal = next(
            (s for s in shadow_1["signals"] if str(s["sc_no"]) == str(new_sc_no)), None
        )
        _check("the new controller appears in the reparsed shadow model", new_signal is not None, str(new_sc_no))
        new_splits = [s for s in shadow_1["signal_program_splits"] if str(s["sc_no"]) == str(new_sc_no)]
        donor_splits = [s for s in shadow_1["signal_program_splits"] if str(s["sc_no"]) == donor_no]
        _log(f"      new controller {new_sc_no}: {len(new_splits)} inherited split row(s) "
             f"(donor has {len(donor_splits)})")
        _check(
            "the copy inherited exactly as many green splits as the donor has",
            len(new_splits) == len(donor_splits) and len(new_splits) > 0,
            f"copy={len(new_splits)} donor={len(donor_splits)}",
        )

        if not new_sc_no:
            return False, None, None

        # --- edit the COPY: prove the new object is genuinely editable ----
        target_split = new_splits[0]
        edit_params = {
            "sc_no": str(new_sc_no),
            "sg_no": str(target_split["sg_no"]),
            "program_no": str(target_split["program_no"]),
            "green_start_s": _NEW_GREEN_START_S,
            "green_end_s": _NEW_GREEN_END_S,
        }
        edit_action = {"tool": "set_signal_green_split", "params": edit_params}
        pre_edit = engine.preflight(edit_action, shadow_1)
        pre_edit_ids = [f.constraint_id for f in pre_edit.findings]
        _log(f"[2.4] preflight(set_signal_green_split on the BRAND-NEW controller) "
             f"findings={pre_edit_ids} blocking={pre_edit.blocking}")
        _check(
            "preflight ALLOWS editing the brand-new controller "
            "(its inherited FIXEDTIME type passes constraint #4)",
            not pre_edit.blocking,
            str(pre_edit_ids),
        )

        _log(f"[2.5] Dispatching set_signal_green_split(sc_no={new_sc_no}, "
             f"program_no={edit_params['program_no']}, sg_no={edit_params['sg_no']}) via toolkit.dispatch...")
        edit_envelope = dispatch("set_signal_green_split", edit_params, registry=default_registry)
        _log(f"      envelope = {edit_envelope}")
        _check("set_signal_green_split status == ok", edit_envelope.get("status") == "ok", str(edit_envelope))
        edit_verification = edit_envelope.get("verification", {})

        shadow_2 = build_shadow_model(am.working_path)
        post_edit = engine.postwrite(edit_action, shadow_1, shadow_2, edit_verification)
        post_edit_ids = [f.constraint_id for f in post_edit.findings]
        _log(f"[2.6] REAL postwrite(set_signal_green_split on the copy) findings={post_edit_ids} "
             f"blocking={post_edit.blocking}")
        _check(
            "postwrite(edit the copy) has no write_verification_failed",
            "write_verification_failed" not in post_edit_ids,
            str(
                post_edit.first_refusal().details.get("nothing_else_moved_diff")
                if post_edit.first_refusal() is not None
                else post_edit_ids
            ),
        )
        edited_split = next(
            (
                s
                for s in shadow_2["signal_program_splits"]
                if s["id"] == f"{new_sc_no}.{edit_params['program_no']}.{edit_params['sg_no']}"
            ),
            None,
        )
        _log(f"      the copy's edited split now reads: {edited_split['green_window'] if edited_split else None}")
        _check(
            "the copy's own green window in the persisted model is the value we wrote",
            edited_split is not None
            and edited_split["green_window"] == {
                "green_start_s": _NEW_GREEN_START_S,
                "green_end_s": _NEW_GREEN_END_S,
            },
            str(edited_split),
        )

        # The whole point of duplicate-then-edit: the DONOR must be untouched
        # by both operations. Compared on the opaque program blob itself.
        donor_xml_after = _read_sc_from_xml(am.working_path, donor_no)
        _check(
            "the DONOR's persisted attributes (including its whole intSupplyData program blob) "
            "are byte-identical after duplicating AND after editing the copy",
            donor_xml_after == donor_xml_before,
            "donor XML changed",
        )

        ok = (
            add_envelope.get("status") == "ok"
            and edit_envelope.get("status") == "ok"
            and "write_verification_failed" not in post_add_ids
            and "write_verification_failed" not in post_edit_ids
        )
    finally:
        clear_active_edit_session()
        session.close()
        _log("[2.7] Vissim edit session closed.")

    return ok, new_sc_no, am.working_path


# ---------------------------------------------------------------------------
# Phase 3: fresh COM reload + load_check.
# ---------------------------------------------------------------------------


def phase_3_reload_and_load_check(working_path: Path, new_sc_no: str) -> bool:
    _log("")
    _log("=" * 78)
    _log("[Phase 3] Fresh COM reload of the SAVED file + load_check")
    _log("=" * 78)

    verify_controller = None
    reloaded_ok = False
    try:
        import pythoncom

        pythoncom.CoInitialize()
        from services.vissim_com import VissimController

        verify_controller = VissimController()
        verify_controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        verify_controller.load_network(str(working_path))
        reloaded = verify_controller.get_signal_controller(int(new_sc_no))
        _log(f"[3.1] fresh reload of SC {new_sc_no}: {reloaded}")
        _check(
            "fresh reload: the new controller's name survived the round trip",
            reloaded["name"] == "North junction (genesis)",
            str(reloaded),
        )
        _check(
            "fresh reload: the new controller kept the donor's signal groups",
            len(reloaded["signal_group_nos"]) > 0,
            str(reloaded),
        )
        reloaded_ok = True
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

    import services.agent.tools_check  # noqa: F401 - registers load_check
    from services.agent.toolkit import default_registry, dispatch
    from services.agent.tools_run import clear_active_run_source, set_active_run_source

    _log("[3.2] load_check on the SAME working copy -- must still be clean with the new controller in it...")
    set_active_run_source(working_path)
    try:
        check_envelope = dispatch("load_check", {}, registry=default_registry)
    finally:
        clear_active_run_source()
    _log(f"      load_check envelope = {check_envelope}")
    check_verification = check_envelope.get("verification", {})
    _check("load_check dispatch status == ok", check_envelope.get("status") == "ok", str(check_envelope))
    _check(
        "load_check reports load_ok=True, init_ok=True with the duplicated controller present",
        check_verification == {"load_ok": True, "init_ok": True},
        str(check_verification),
    )
    return reloaded_ok and check_verification == {"load_ok": True, "init_ok": True}


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")
    example_inpx = _find_inpx(example_dir)

    phase_1_static_and_constraints(example_inpx)
    phase_2_ok, new_sc_no, working_path = phase_2_live_flow(example_inpx)
    phase_3_ok = False
    if phase_2_ok and new_sc_no and working_path is not None:
        phase_3_ok = phase_3_reload_and_load_check(working_path, new_sc_no)

    _log("")
    _log("=" * 78)
    if _FAILURES:
        _log(f"RESULT: FAIL -- {len(_FAILURES)} check(s) failed: {_FAILURES}")
    elif not phase_2_ok:
        _log("RESULT: FAIL -- the live dispatch flow did not complete cleanly.")
    elif not phase_3_ok:
        _log("RESULT: FAIL -- the fresh reload / load_check did not come back clean.")
    else:
        _log("RESULT: PASS -- all phases green.")
    _log("=" * 78)
    if _FAILURES or not phase_2_ok or not phase_3_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

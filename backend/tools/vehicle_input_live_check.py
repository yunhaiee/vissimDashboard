"""LIVE ACCEPTANCE for Q5 unit (3): ``add_vehicle_input`` end to end through
the REAL dispatch path with a REAL guardrail engine.

TWO ARMS, because this verb's own guardrail exemption is only fully
exercised by the second one:

  * ``beijing``  -- ``Urban Intersection Beijing.CN``: 12 existing vehicle
    inputs, 3 existing vehicle compositions. The ordinary case. Also the
    only arm that can exercise the connector WARN against real geometry.
  * ``airtaxi``  -- ``Air Taxi Vertiport``: ZERO vehicle inputs and ZERO
    ``<vehicleComposition>`` elements. The from-nothing genesis case, and
    the ONLY one where ``_ALLOWS_NEW_OBJECTS_IN["add_vehicle_input"]``'s
    second class (``vehicle_composition_entries``) is load-bearing --
    saving materialises the default composition the fresh input references.
    A live check that ran only the Beijing arm would pass with a table
    entry that is broken for exactly the use this verb was built for.

Three phases per arm, mirroring ``signal_controller_live_check.py``'s shape:

  1. **Static + preflight against REAL data, no COM.** Both of this unit's
     constraints plus the two refusals (bogus link, out-of-range volume)
     exercised against a real parsed network rather than a fixture.
  2. **The live flow on a clone.** ``add_vehicle_input`` dispatched for real
     (real COM ``AddVehicleInput``), the REAL ``GuardrailEngine.postwrite``
     run over real before/after shadow models -- which is where the
     exemption either holds or the whole verb is refused and rolled back --
     then ``set_vehicle_input_volume`` dispatched against the BRAND-NEW
     input number to prove it is genuinely editable, not merely present
     (Q4 unit (6)'s precedent).
  3. **Fresh COM reload + load_check.** A separate Vissim instance loads the
     saved file, re-reads the new input, and ``load_check`` (the real verb)
     must still report ``load_ok``/``init_ok``.

Each arm runs as its own OS process (``main()`` shells out to itself once
per arm) -- Q5 unit (1)'s validated fix for the native crash that follows
~2-3 sequential COM connect/disconnect cycles in one long-lived process.
Each arm process holds exactly 3 cycles (the edit session's, the reload's,
and ``load_check``'s).

``attach_to_running=False`` throughout. Never touches the original Examples
Demo folders (each is cloned into ``runtime/vehicle-input-live-check/``),
and never ``runtime/uploads/``.

---------------------------------------------------------------------------
WHAT THE ACCEPTANCE RUN ACTUALLY RETURNED (2026-08-03, PTV Vissim 2026 SP09
on this machine). RESULT: **PASS -- 54 checks, 0 failures, both arms.**
Every line below is transcribed from that run's own output, not predicted.
---------------------------------------------------------------------------

**Arm ``beijing`` -- 26 checks, all PASS.**

  [1.1] shadow BEFORE: ``vehicle_inputs=12 links=112 (56 connectors)
        vehicle_composition_entries=7 per-input interval counts=[1]``.
  [1.2] preflight(plain link 1, 450.0 veh/h) -> ``findings=[]
        blocking=False``. Neither of this unit's warns fires on the
        ordinary case, which is the point.
  [1.3] preflight(CONNECTOR 10000) -> ``findings=
        ['vehicle_input_on_connector'] blocking=False``, details
        ``{'link_id': '10000', 'is_connector': True, 'upstream_link': '1',
        'downstream_link': '2', 'from_pos': 335.021206218735}`` -- the
        upstream approach is read off the real model, and it is a WARN, not
        a refusal (probe Parts D/E/F measured that such an input genuinely
        generates traffic).
  [1.4] preflight(bogus link 999999) -> ``['object_not_found']
        blocking=True``.
  [1.5] preflight(volume 25000) -> ``['volume_out_of_range']
        blocking=True``.
  [2.2] dispatch(add_vehicle_input, link_no='1', volume_vph=450.0,
        name='North approach (genesis)') -> status ok, artifacts
        ``{'new_vehicle_input_no': '21', 'interval_count': 1,
        'intervals_left_at_zero': 0}``, written == readback. Message:
        "Vehicle input 21 added on link 1 at 450.0 veh/h (vehicle
        composition 1)." -- correctly WITHOUT an interval caveat, because
        this network really has one interval.
  [2.3] REAL postwrite -> ``findings=['conflict_area_data_unavailable']
        blocking=False``, no ``write_verification_failed``.
        ``vehicle_composition_entries: 7 -> 7`` (nothing materialised here;
        this network already had compositions).
  [2.4] counterfactual, live, on this arm's own before/after pair:
        narrowed to ``{vehicle_inputs}`` -> ``blocking=False
        evidence_rows=0``; narrowed to ``{vehicle_composition_entries}`` ->
        ``blocking=True evidence_rows=1 ['vehicle_inputs#21:added']``. So
        on THIS network only the first class is load-bearing -- which is
        exactly why the airtaxi arm exists.
  [2.5] preflight(set_vehicle_input_volume on the new input) ->
        ``['vehicle_input_siblings_share_link'] blocking=False``. A
        pre-existing warn firing correctly and unprompted: the new input
        shares link 1 with Beijing's own input 1, which is a real
        double-counting hazard worth surfacing.
  [2.6] dispatch(set_vehicle_input_volume, input_no='21', volume=1250.0)
        -> ok; postwrite non-blocking; persisted volume reads 1250.0. The
        new object is genuinely editable by the pre-existing setter, not a
        decorative row.
  [2.7] ``pre-existing inputs whose volume changed: none (12 checked)``.
  [3.1] fresh Vissim instance, fresh load: ``get_vehicle_input(21)`` ->
        ``{'no': '21', 'name': 'North approach (genesis)', 'link': '1',
        'volume': 1250.0, 'composition_no': '1', 'volume_type':
        'STOCHASTIC'}``.
  [3.2] ``load_check`` -> ``{'load_ok': True, 'init_ok': True}``.

**Arm ``airtaxi`` -- 28 checks, all PASS. This is the arm that matters.**

  [1.1] shadow BEFORE: ``vehicle_inputs=0 links=60 (15 connectors)
        vehicle_composition_entries=0 per-input interval counts=[]``. The
        from-nothing premise, stated before any COM call.
  [1.2] preflight -> ``findings=[] blocking=False``. The interval warn is
        correctly SILENT: with no existing input there is nothing to read
        an interval count off. That is the STATED CATALOG-GAP, and [2.2] is
        how it is covered instead.
  [2.2] dispatch(add_vehicle_input, link_no='1', volume_vph=450.0) ->
        status ok, ``new_vehicle_input_no='1'`` -- **a network with ZERO
        vehicle inputs minted its first through the real production
        dispatch path**, the finding this whole unit turns on. artifacts
        ``{'interval_count': 12, 'intervals_left_at_zero': 11}`` and the
        message says so verbatim: "This network's demand model has 12 time
        intervals and this volume applies to interval 1 only -- the other
        11 are still 0 veh/h. Use set_vehicle_input_volume(interval_no=...)
        on the new number to fill them." The post-write readback covering
        the preflight gap, working exactly as designed -- and the reparsed
        input confirms the 12 rows really do start at 0 s, 1 s, 190 s, ...,
        i.e. the requested demand would have run for ONE SECOND.
  [2.3] REAL postwrite -> ``findings=['conflict_area_data_unavailable']
        blocking=False``, no ``write_verification_failed``.
        ``vehicle_composition_entries: 0 -> 1``.
  [2.4] counterfactual, live: narrowed to ``{vehicle_inputs}`` ->
        ``blocking=True evidence_rows=1
        ['vehicle_composition_entries#1.700:added']``; narrowed to
        ``{vehicle_composition_entries}`` -> ``blocking=True
        evidence_rows=1 ['vehicle_inputs#1:added']``. **Both classes proven
        individually load-bearing here, live, not only in an offline
        script.**
  [2.5] preflight(set_vehicle_input_volume on the new input) ->
        ``['vehicle_input_multi_interval_write_gap'] blocking=False`` --
        again a pre-existing warn firing correctly on the 12-interval input
        this verb just created.
  [2.6] edit -> ok; persisted volume 1250.0.
  [2.7] ``pre-existing inputs whose volume changed: none (0 checked)``.
  [3.1] fresh reload: ``get_vehicle_input(1)`` -> ``{'no': '1', 'name':
        'North approach (genesis)', 'link': '1', 'volume': 1250.0,
        'composition_no': '1', 'volume_type': 'STOCHASTIC'}``.
  [3.2] ``load_check`` -> ``{'load_ok': True, 'init_ok': True}``.

Usage: ``python tools/vehicle_input_live_check.py`` -- runs both arms (via
internal per-arm subprocesses) and prints one PASS/FAIL summary. Advanced:
``--arm {beijing,airtaxi}`` runs exactly one arm in the current process.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
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
WORK_ROOT = REPO_ROOT / "runtime" / "vehicle-input-live-check"

#: (example dir, plain link to place demand on, a connector or None,
#:  expected new input number, whether this arm is the zero-input one)
ARMS: dict[str, dict[str, Any]] = {
    "beijing": {
        "example": EXAMPLES_ROOT / "Urban Intersection Beijing.CN",
        "link": "1",
        "connector": "10000",
        "zero_input": False,
    },
    "airtaxi": {
        "example": EXAMPLES_ROOT / "Air Taxi Vertiport",
        "link": "1",
        "connector": None,
        "zero_input": True,
    },
}

_NEW_VOLUME_VPH = 450.0
#: The value the follow-up editability check writes onto the BRAND-NEW input.
#: Deliberately different from _NEW_VOLUME_VPH so "the edit landed" is a real
#: claim rather than a tautology.
_EDITED_VOLUME_VPH = 1250.0

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


def _volumes_by_input(shadow: dict[str, Any]) -> dict[str, Any]:
    return {str(vi["no"]): vi.get("volume") for vi in shadow.get("vehicle_inputs", [])}


# ---------------------------------------------------------------------------
# Phase 1: static + both constraints against REAL parsed data (no COM).
# ---------------------------------------------------------------------------


def phase_1_static_and_constraints(arm: str, example_inpx: Path) -> None:
    _log("=" * 78)
    _log(f"[Phase 1 / {arm}] build_shadow_model + this unit's constraints against REAL data")
    _log("=" * 78)

    from services.agent.guardrails import GuardrailEngine
    from services.agent.tools_network import build_shadow_model

    spec = ARMS[arm]
    shadow = build_shadow_model(example_inpx)
    inputs = shadow.get("vehicle_inputs") or []
    links = shadow.get("links") or []
    connectors = [l for l in links if l.get("is_connector")]
    comp_entries = shadow.get("vehicle_composition_entries") or []
    interval_counts = sorted({vi.get("interval_count") for vi in inputs})
    _log(
        f"[1.1] shadow BEFORE: vehicle_inputs={len(inputs)} links={len(links)} "
        f"({len(connectors)} connectors) vehicle_composition_entries={len(comp_entries)} "
        f"per-input interval counts={interval_counts}"
    )
    if spec["zero_input"]:
        _check("this arm's network genuinely has ZERO vehicle inputs", not inputs, str(len(inputs)))
        _check(
            "this arm's network genuinely has ZERO vehicle composition entries "
            "(what makes the second exemption class load-bearing)",
            not comp_entries,
            str(len(comp_entries)),
        )
    else:
        _check("this arm's network has existing vehicle inputs to sit alongside", bool(inputs))

    engine = GuardrailEngine(disabled=False)

    action = {
        "tool": "add_vehicle_input",
        "params": {"link_no": spec["link"], "volume_vph": _NEW_VOLUME_VPH},
    }
    report = engine.preflight(action, shadow)
    ids = [f.constraint_id for f in report.findings]
    _log(f"[1.2] preflight(plain link {spec['link']}, {_NEW_VOLUME_VPH} veh/h) "
         f"findings={ids} blocking={report.blocking}")
    _check("preflight ALLOWS a plain-link placement (not blocking)", not report.blocking, str(ids))
    _check(
        "the connector warn does NOT fire on a plain link",
        "vehicle_input_on_connector" not in ids,
        str(ids),
    )
    if spec["zero_input"]:
        _check(
            "the interval warn is correctly SILENT on a zero-input network "
            "(the STATED CATALOG-GAP -- the tool's own readback covers it, see [2.2])",
            "vehicle_input_created_covers_first_interval_only" not in ids,
            str(ids),
        )

    if spec["connector"]:
        conn_action = {
            "tool": "add_vehicle_input",
            "params": {"link_no": spec["connector"], "volume_vph": _NEW_VOLUME_VPH},
        }
        conn_report = engine.preflight(conn_action, shadow)
        conn_ids = [f.constraint_id for f in conn_report.findings]
        _log(f"[1.3] preflight(CONNECTOR {spec['connector']}) findings={conn_ids} "
             f"blocking={conn_report.blocking}")
        _check(
            "the connector WARN fires on a real connector",
            "vehicle_input_on_connector" in conn_ids,
            str(conn_ids),
        )
        _check(
            "it is a WARN, not a refusal -- COM accepts such an input and it genuinely "
            "generates traffic (probe Parts D/E/F), so refusing would refuse something "
            "Vissim actually permits",
            not conn_report.blocking,
            str(conn_ids),
        )
        warn = next(
            (f for f in conn_report.findings if f.constraint_id == "vehicle_input_on_connector"),
            None,
        )
        if warn is not None:
            _log(f"      warn details = {warn.details}")
            _log(f"      warn (en) = {warn.messages['en']}")
            _check(
                "the warn names the REAL upstream approach read out of this model",
                bool(warn.details.get("upstream_link")),
                str(warn.details),
            )

    bogus = engine.preflight(
        {"tool": "add_vehicle_input", "params": {"link_no": "999999", "volume_vph": 450.0}}, shadow
    )
    bogus_ids = [f.constraint_id for f in bogus.findings]
    _log(f"[1.4] preflight(bogus link 999999) findings={bogus_ids} blocking={bogus.blocking}")
    _check(
        "a bogus link is REFUSED by object_not_found (proves the _TOOL_TARGET_SPEC entry AND "
        "the applies_to entry both landed -- a spec alone does nothing)",
        "object_not_found" in bogus_ids and bogus.blocking,
        str(bogus_ids),
    )

    over = engine.preflight(
        {"tool": "add_vehicle_input", "params": {"link_no": spec["link"], "volume_vph": 25000.0}},
        shadow,
    )
    over_ids = [f.constraint_id for f in over.findings]
    _log(f"[1.5] preflight(volume 25000) findings={over_ids} blocking={over.blocking}")
    _check(
        "an out-of-range volume is REFUSED by volume_out_of_range (the table wiring holds)",
        "volume_out_of_range" in over_ids and over.blocking,
        str(over_ids),
    )


# ---------------------------------------------------------------------------
# Phase 2: the live dispatch flow.
# ---------------------------------------------------------------------------


def phase_2_live_flow(arm: str, example_inpx: Path) -> tuple[bool, str | None, Path | None]:
    _log("")
    _log("=" * 78)
    _log(f"[Phase 2 / {arm}] LIVE: add_vehicle_input -> real postwrite -> "
         "set_vehicle_input_volume on the NEW input")
    _log("=" * 78)

    import services.agent.tools_edit  # noqa: F401 - registers the write tools
    from services.agent import guardrails as guardrails_module
    from services.agent.edit_session import (
        VissimEditSession,
        clear_active_edit_session,
        set_active_edit_session,
    )
    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    from services.agent.toolkit import default_registry, dispatch

    spec = ARMS[arm]
    work_dir = _fresh_work_dir(f"phase2-{arm}")
    am = ArtifactManager(source_inpx=example_inpx, work_dir=work_dir)
    shadow_0 = am.clone()
    _log(f"[2.1] Cloned -> {am.working_path} (never the original .inpx)")
    volumes_before = _volumes_by_input(shadow_0)

    ok = False
    new_no: str | None = None
    session = VissimEditSession(am.working_path)
    engine = GuardrailEngine(disabled=False)
    try:
        set_active_edit_session(session)

        add_params: dict[str, Any] = {
            "link_no": spec["link"],
            "volume_vph": _NEW_VOLUME_VPH,
            "name": "North approach (genesis)",
        }
        add_action = {"tool": "add_vehicle_input", "params": add_params}

        _log("[2.2] Dispatching add_vehicle_input via toolkit.dispatch (real COM AddVehicleInput)...")
        add_envelope = dispatch("add_vehicle_input", add_params, registry=default_registry)
        _log(f"      message = {add_envelope.get('message')}")
        _log(f"      artifacts = { {k: v for k, v in (add_envelope.get('artifacts') or {}).items() if k != 'vehicle_input'} }")
        _check("add_vehicle_input status == ok", add_envelope.get("status") == "ok", str(add_envelope))
        artifacts = add_envelope.get("artifacts") or {}
        new_no = artifacts.get("new_vehicle_input_no")
        _check("a new vehicle-input number was assigned", bool(new_no), str(add_envelope))
        if spec["zero_input"]:
            _check(
                "a network with ZERO vehicle inputs minted its FIRST one through the real "
                "dispatch path (no family limitation -- unlike add_priority_rule / "
                "add_signal_controller)",
                str(new_no) == "1",
                str(new_no),
            )
        add_verification = add_envelope.get("verification", {})
        _check(
            "written_value == readback_value",
            add_verification.get("written_value") == add_verification.get("readback_value"),
            str(add_verification),
        )

        shadow_1 = build_shadow_model(am.working_path)
        post_add = engine.postwrite(add_action, shadow_0, shadow_1, add_verification)
        post_add_ids = [f.constraint_id for f in post_add.findings]
        _log(f"[2.3] REAL postwrite(add_vehicle_input) findings={post_add_ids} "
             f"blocking={post_add.blocking}")
        # THE check this whole unit turns on: without
        # _ALLOWS_NEW_OBJECTS_IN["add_vehicle_input"] this is
        # write_verification_failed (refuse) and the edit is rolled back.
        _check(
            "postwrite has NO write_verification_failed (the guardrail exemption holds "
            "against real data)",
            "write_verification_failed" not in post_add_ids,
            str(
                post_add.first_refusal().details.get("nothing_else_moved_diff")
                if post_add.first_refusal() is not None
                else post_add_ids
            ),
        )
        _check("postwrite is not blocking", not post_add.blocking, str(post_add_ids))

        entries_before = len(shadow_0.get("vehicle_composition_entries") or [])
        entries_after = len(shadow_1.get("vehicle_composition_entries") or [])
        _log(f"      vehicle_composition_entries: {entries_before} -> {entries_after}")

        # [2.4] The counterfactual, re-run LIVE on this arm's own real
        # before/after pair: narrow the table to one class at a time and show
        # the refusal come back. Proves each class is individually necessary
        # here, not only in an offline script.
        original = guardrails_module._ALLOWS_NEW_OBJECTS_IN["add_vehicle_input"]
        try:
            for narrowed in (frozenset({"vehicle_inputs"}), frozenset({"vehicle_composition_entries"})):
                guardrails_module._ALLOWS_NEW_OBJECTS_IN["add_vehicle_input"] = narrowed
                narrowed_report = engine.postwrite(add_action, shadow_0, shadow_1, add_verification)
                refusal = narrowed_report.first_refusal()
                rows = (refusal.details.get("nothing_else_moved_diff") if refusal else None) or []
                labels = [f"{r['object_class']}#{r['object_id']}:{r['change']}" for r in rows]
                _log(f"[2.4] counterfactual, table narrowed to {sorted(narrowed)}: "
                     f"blocking={narrowed_report.blocking} evidence_rows={len(rows)} {labels}")
                if narrowed == frozenset({"vehicle_composition_entries"}):
                    _check(
                        "narrowing the table to {vehicle_composition_entries} brings the refusal "
                        "back, naming the new input -- so 'vehicle_inputs' is load-bearing",
                        narrowed_report.blocking
                        and any(r["object_class"] == "vehicle_inputs" for r in rows),
                        str(labels),
                    )
                elif entries_after > entries_before:
                    _check(
                        "narrowing the table to {vehicle_inputs} brings the refusal back, naming "
                        "the auto-materialised composition entry -- so "
                        "'vehicle_composition_entries' is load-bearing TOO (only provable on a "
                        "network that starts with no compositions)",
                        narrowed_report.blocking
                        and any(r["object_class"] == "vehicle_composition_entries" for r in rows),
                        str(labels),
                    )
                else:
                    _log("      (this arm's network already had compositions, so narrowing to "
                         "{vehicle_inputs} is expected to be sufficient HERE -- the airtaxi arm "
                         "is what proves the second class)")
        finally:
            guardrails_module._ALLOWS_NEW_OBJECTS_IN["add_vehicle_input"] = original

        persisted = next(
            (vi for vi in shadow_1["vehicle_inputs"] if str(vi["no"]) == str(new_no)), None
        )
        _check("the new input appears in the reparsed shadow model", persisted is not None, str(new_no))
        if persisted is not None:
            _log(f"      persisted new input = {persisted}")
            _check(
                "its interval count matches what the tool reported",
                int(persisted.get("interval_count") or 0) == int(artifacts.get("interval_count") or -1),
                f"{persisted.get('interval_count')} vs {artifacts.get('interval_count')}",
            )
            if int(artifacts.get("intervals_left_at_zero") or 0) > 0:
                _check(
                    "the message NAMES the intervals left at zero (the one fact a caller cannot "
                    "infer from the parameters, because the count is the NETWORK's property)",
                    "time intervals" in (add_envelope.get("message") or ""),
                    str(add_envelope.get("message")),
                )

        if not new_no:
            return False, None, None

        # --- edit the NEW input: prove it is genuinely usable, not just present
        edit_params = {"input_no": str(new_no), "volume": _EDITED_VOLUME_VPH}
        edit_action = {"tool": "set_vehicle_input_volume", "params": edit_params}
        pre_edit = engine.preflight(edit_action, shadow_1)
        pre_edit_ids = [f.constraint_id for f in pre_edit.findings]
        _log(f"[2.5] preflight(set_vehicle_input_volume on the BRAND-NEW input) "
             f"findings={pre_edit_ids} blocking={pre_edit.blocking}")
        _check("preflight ALLOWS editing the brand-new input", not pre_edit.blocking, str(pre_edit_ids))

        edit_envelope = dispatch("set_vehicle_input_volume", edit_params, registry=default_registry)
        _log(f"      envelope = {edit_envelope.get('message')}")
        _check("set_vehicle_input_volume status == ok", edit_envelope.get("status") == "ok",
               str(edit_envelope))
        edit_verification = edit_envelope.get("verification", {})

        shadow_2 = build_shadow_model(am.working_path)
        post_edit = engine.postwrite(edit_action, shadow_1, shadow_2, edit_verification)
        post_edit_ids = [f.constraint_id for f in post_edit.findings]
        _log(f"[2.6] REAL postwrite(edit the new input) findings={post_edit_ids} "
             f"blocking={post_edit.blocking}")
        _check(
            "postwrite(edit) has no write_verification_failed",
            "write_verification_failed" not in post_edit_ids,
            str(
                post_edit.first_refusal().details.get("nothing_else_moved_diff")
                if post_edit.first_refusal() is not None
                else post_edit_ids
            ),
        )
        edited = next(
            (vi for vi in shadow_2["vehicle_inputs"] if str(vi["no"]) == str(new_no)), None
        )
        _check(
            "the new input's persisted volume is the value we wrote",
            edited is not None and abs(float(edited["volume"]) - _EDITED_VOLUME_VPH) < 1e-6,
            str(edited),
        )

        # Nobody else's demand may have moved.
        volumes_after = _volumes_by_input(shadow_2)
        drifted = {
            no: (volumes_before[no], volumes_after.get(no))
            for no in volumes_before
            if volumes_after.get(no) != volumes_before[no]
        }
        _log(f"[2.7] pre-existing inputs whose volume changed: {drifted or 'none'} "
             f"({len(volumes_before)} checked)")
        _check(
            "every PRE-EXISTING vehicle input's volume is unchanged after both writes",
            not drifted,
            str(drifted),
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
        _log("[2.8] Vissim edit session closed.")

    return ok, new_no, am.working_path


# ---------------------------------------------------------------------------
# Phase 3: fresh COM reload + load_check.
# ---------------------------------------------------------------------------


def phase_3_reload_and_load_check(arm: str, working_path: Path, new_no: str) -> bool:
    _log("")
    _log("=" * 78)
    _log(f"[Phase 3 / {arm}] Fresh COM reload of the SAVED file + load_check")
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
        reloaded = verify_controller.get_vehicle_input(int(new_no))
        _log(f"[3.1] fresh reload of vehicle input {new_no}: {reloaded}")
        _check(
            "fresh reload: the new input's own No came back from Vissim",
            reloaded["no"] == str(new_no),
            str(reloaded),
        )
        _check(
            "fresh reload: the edited volume survived the round trip",
            abs(float(reloaded["volume"]) - _EDITED_VOLUME_VPH) < 1e-6,
            str(reloaded),
        )
        _check(
            "fresh reload: it carries a real vehicle composition and volume type",
            bool(reloaded["composition_no"]) and reloaded["volume_type"] == "STOCHASTIC",
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
    from services.agent.tools_run import clear_active_run_source, set_active_run_source
    from services.agent.toolkit import default_registry, dispatch

    _log("[3.2] load_check on the SAME working copy -- must still be clean with the new input...")
    set_active_run_source(working_path)
    try:
        check_envelope = dispatch("load_check", {}, registry=default_registry)
    finally:
        clear_active_run_source()
    _log(f"      load_check envelope = {check_envelope}")
    check_verification = check_envelope.get("verification", {})
    _check("load_check dispatch status == ok", check_envelope.get("status") == "ok",
           str(check_envelope))
    _check(
        "load_check reports load_ok=True, init_ok=True with the new vehicle input present",
        check_verification == {"load_ok": True, "init_ok": True},
        str(check_verification),
    )
    return reloaded_ok and check_verification == {"load_ok": True, "init_ok": True}


def _run_arm(arm: str) -> int:
    spec = ARMS[arm]
    example_dir = spec["example"]
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")
    example_inpx = _find_inpx(example_dir)

    phase_1_static_and_constraints(arm, example_inpx)
    phase_2_ok, new_no, working_path = phase_2_live_flow(arm, example_inpx)
    phase_3_ok = False
    if phase_2_ok and new_no and working_path is not None:
        phase_3_ok = phase_3_reload_and_load_check(arm, working_path, new_no)

    _log("")
    _log("=" * 78)
    if _FAILURES:
        _log(f"ARM {arm}: FAIL -- {len(_FAILURES)} check(s) failed: {_FAILURES}")
    elif not phase_2_ok:
        _log(f"ARM {arm}: FAIL -- the live dispatch flow did not complete cleanly.")
    elif not phase_3_ok:
        _log(f"ARM {arm}: FAIL -- the fresh reload / load_check did not come back clean.")
    else:
        _log(f"ARM {arm}: PASS -- all phases green.")
    _log("=" * 78)
    return 1 if (_FAILURES or not phase_2_ok or not phase_3_ok) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Q5 unit (3) vehicle-input live acceptance")
    parser.add_argument("--arm", choices=sorted(ARMS))
    args = parser.parse_args()
    if args.arm:
        return _run_arm(args.arm)

    # Each arm in its own OS process -- Q5 unit (1)'s validated fix for the
    # native crash after ~2-3 sequential COM connect/disconnect cycles.
    results: dict[str, int] = {}
    for arm in sorted(ARMS):
        _log(f"\n\n########## spawning arm {arm} ##########")
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--arm", arm],
            cwd=str(BACKEND_ROOT),
        )
        results[arm] = proc.returncode

    _log("\n" + "=" * 78)
    _log("SUMMARY")
    _log("=" * 78)
    for arm, code in results.items():
        _log(f"  [{arm}] {'PASS' if code == 0 else f'FAIL (exit {code})'}")
    return 0 if all(code == 0 for code in results.values()) else 1


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        raise SystemExit(main())

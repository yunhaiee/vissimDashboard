"""Q6 unit ② LIVE END-TO-END CHECK: add_parking_lot / set_parking_lot_capacity
/ remove_parking_lot against a REAL cloned Examples Demo network -- no fake
anywhere in this script. House shape = ``link_geometry_live_check.py`` (Q6 unit
①'s own live check), including its ``_log`` TEE: the transcript is written to
``runtime/parking-lot-live-check/live_check.log`` rather than printed only,
because unit ①'s counterfactual evidence survived its session solely in a
scratchpad when this was a bare ``print``.

Four phases:

  [Phase A] ``add_parking_lot`` through the real ``toolkit.dispatch`` + the
  real ``GuardrailEngine.postwrite`` against genuine before/after shadow
  models -- and then **the counterfactual the house rule demands is RUN, not
  assumed**: ``_ALLOWS_NEW_OBJECTS_IN['add_parking_lot']`` is popped and the
  resulting refusal is printed with its evidence rows. An exemption that
  cannot be shown to be load-bearing has no business being in the table.

  [Phase B] ``set_parking_lot_capacity`` on the lot Phase A created: the
  DERIVED-capacity claim end to end (the tool writes a length; Vissim's own
  readback is what the verification compares), plus its postwrite.

  [Phase C] ``remove_parking_lot``: the clean removal + the
  ``_ALLOWS_REMOVED_OBJECTS_IN`` counterfactual, and then the REFUSAL --
  a lot a parking route still references, which is this unit's flagship
  measured silent failure (Vissim's own removal cascade-deletes that route).
  The refusal phase runs on the ONE corpus network that has both.

  [Phase D] The COM load oracle over the final working copy: Vissim's own
  opinion on everything the three verbs did to it.

Usage: python tools/parking_lot_live_check.py [example_dir]
Default example: Urban Intersection Beijing.CN (2026 Examples Demo) -- the same
METERS-display network with ZERO parking lots this unit's probe measured the
from-nothing create on.

Always disconnects/closes its Vissim session(s) in ``finally`` blocks.
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

EXAMPLES_ROOT = Path(r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo")
DEFAULT_EXAMPLE = EXAMPLES_ROOT / "Urban Intersection Beijing.CN"
#: The ONLY corpus network carrying BOTH REALPARKINGSPACE lots and the parking
#: routing decisions that reference them (573 lots / 573 routes / 12 decisions,
#: zero unreferenced) -- Phase C's refusal has nowhere else to run.
REFERENCED_EXAMPLE = EXAMPLES_ROOT / "3D - Complex Intersection Karlsruhe.DE"
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "parking-lot-live-check"

#: A long plain Beijing link (Length2D 335.122 m, measured) with room for a
#: real multi-space lot.
TARGET_LINK = "1"
TARGET_LANE = 1
TARGET_POS_M = 100.0
TARGET_CAPACITY = 10


def _log(msg: str) -> None:
    # Tee to a machine-readable transcript next to the work dirs -- unit ①'s
    # recorded lesson: a bare print leaves the counterfactual evidence
    # nowhere once the session ends.
    print(msg, flush=True)
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    with (WORK_ROOT / "live_check.log").open("a", encoding="utf-8") as fh:
        fh.write(msg + "\n")


def _fresh_work_dir(name: str) -> Path:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    for stale in WORK_ROOT.glob(f"{name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    work_dir = WORK_ROOT / f"{name}_{os.getpid()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _evidence(report: Any) -> list[str]:
    rows: list[str] = []
    for finding in report.findings:
        for item in (finding.details or {}).get("nothing_else_moved_diff", []) or []:
            rows.append(
                f"{item['object_class']}#{item['object_id']}:{item.get('field', item['change'])}"
            )
    return rows


def _counterfactual(
    engine: Any,
    guardrails: Any,
    action: dict[str, Any],
    before: dict[str, Any],
    after: dict[str, Any],
    verification: dict[str, Any],
    table_name: str,
    tool: str,
    replacement: Any,
    label: str,
) -> bool:
    """Temporarily replace ONE entry of one exemption table, re-run the REAL
    postwrite over the SAME before/after pair, and report whether the correct
    call is now refused. ``replacement is None`` pops the entry entirely."""
    table = getattr(guardrails, table_name)
    original = table.get(tool)
    try:
        if replacement is None:
            table.pop(tool, None)
        else:
            table[tool] = frozenset(replacement)
        report = engine.postwrite(action, before, after, verification)
        rows = _evidence(report)
        _log(f"      [counterfactual] {table_name}[{tool!r}] = {label}: blocking="
             f"{report.blocking} findings={[f.constraint_id for f in report.findings]} "
             f"evidence={rows}")
        return bool(report.blocking)
    finally:
        if original is None:
            table.pop(tool, None)
        else:
            table[tool] = original


def _parking_counts(inpx: Path) -> dict[str, int]:
    root = ET.parse(inpx).getroot()
    return {
        "parking_lots": len(root.findall(".//parkingLots/parkingLot")),
        "parking_routes": len(root.findall(".//vehicleRouteParking")),
        "zones": len(root.findall(".//zones/zone")),
    }


def phase_abc(example_dir: Path) -> bool:
    _log("=" * 78)
    _log("[Phases A-C] add -> set capacity -> remove, with the exemption counterfactuals")
    _log("=" * 78)

    from services.agent import guardrails as guardrails_module
    from services.agent.edit_session import (
        VissimEditSession,
        clear_active_edit_session,
        set_active_edit_session,
    )
    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model
    import services.agent.tools_edit  # noqa: F401 - registers the 3 verbs
    from services.agent.toolkit import default_registry, dispatch

    work_dir = _fresh_work_dir("phaseABC")
    artifact_manager = ArtifactManager(
        source_inpx=next(example_dir.glob("*.inpx")), work_dir=work_dir
    )
    before = artifact_manager.clone()
    engine = GuardrailEngine(disabled=False)
    _log(f"[A.1] Cloned {example_dir.name} -> {artifact_manager.working_path}")
    _log(f"      parking lots in the source: {len(before['parking_lots'])} "
         f"(persisted counts {_parking_counts(artifact_manager.working_path)})")

    link = next(l for l in before["links"] if l["id"] == TARGET_LINK)
    _log(f"      target link {TARGET_LINK}: length {link['length']} m, {link['lanes']} lanes")

    ok = True
    session = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session)

        add_params = {
            "link_no": TARGET_LINK,
            "lane_index": TARGET_LANE,
            "pos_m": TARGET_POS_M,
            "capacity": TARGET_CAPACITY,
        }
        _log(f"\n[A.2] add_parking_lot({add_params}) ...")
        add_env = dispatch("add_parking_lot", dict(add_params), registry=default_registry)
        _log(f"      envelope = {add_env}")
        ok = ok and add_env["status"] == "ok"

        after_add = build_shadow_model(artifact_manager.working_path)
        lots_after = after_add["parking_lots"]
        _log(f"      reparsed: {len(before['parking_lots'])} -> {len(lots_after)} parking lots; "
             f"new = {lots_after[-1] if lots_after else None}")
        ok = ok and len(lots_after) == len(before["parking_lots"]) + 1

        # The DERIVED-capacity claim, verified against the persisted file
        # rather than against the tool's own return value.
        if lots_after:
            persisted = lots_after[-1]
            _log(f"      persisted capacity={persisted['capacity']} length={persisted['length']} "
                 f"len_per_space={persisted['len_per_space']} "
                 f"(capacity x len_per_space = "
                 f"{persisted['capacity'] * (persisted['len_per_space'] or 0)})")
            ok = ok and persisted["capacity"] == TARGET_CAPACITY

        add_action = {"tool": "add_parking_lot", "params": add_params}
        _log("\n[A.3] REAL GuardrailEngine.postwrite for the create ...")
        report = engine.postwrite(add_action, before, after_add, add_env["verification"])
        _log(f"      report.blocking={report.blocking} "
             f"findings={[f.constraint_id for f in report.findings]}")
        ok = ok and report.blocking is False

        _log("\n[A.4] THE COUNTERFACTUAL for _ALLOWS_NEW_OBJECTS_IN['add_parking_lot'] "
             "(RUN over this same real before/after pair, never assumed):")
        popped = _counterfactual(
            engine, guardrails_module, add_action, before, after_add, add_env["verification"],
            "_ALLOWS_NEW_OBJECTS_IN", "add_parking_lot", None, "POPPED ENTIRELY",
        )
        _log(f"      => popped refuses: {popped}")
        _log("      (only ONE class is opted in for this verb -- probe Part A measured the "
             "create as exactly `parking_lots: {'added': 1}` and nothing else -- so there is "
             "no narrowing to run, unlike unit ①'s two-class entry)")
        ok = ok and popped

        # ---- Phase B ---------------------------------------------------
        _log("\n" + "=" * 78)
        _log("[Phase B] set_parking_lot_capacity -- the derived-capacity claim end to end")
        _log("=" * 78)
        new_no = str(lots_after[-1]["no"])
        before_set = build_shadow_model(artifact_manager.working_path)
        set_params = {"parking_lot_no": new_no, "capacity": 25}
        _log(f"\n[B.1] set_parking_lot_capacity({set_params}) ...")
        set_env = dispatch("set_parking_lot_capacity", dict(set_params), registry=default_registry)
        _log(f"      envelope = {set_env}")
        ok = ok and set_env["status"] == "ok"

        after_set = build_shadow_model(artifact_manager.working_path)
        resized = next(l for l in after_set["parking_lots"] if str(l["no"]) == new_no)
        _log(f"      reparsed: capacity {lots_after[-1]['capacity']} -> {resized['capacity']}, "
             f"length {lots_after[-1]['length']} -> {resized['length']}")
        ok = ok and resized["capacity"] == 25

        set_action = {"tool": "set_parking_lot_capacity", "params": set_params}
        _log("\n[B.2] REAL GuardrailEngine.postwrite for the resize ...")
        report = engine.postwrite(set_action, before_set, after_set, set_env["verification"])
        _log(f"      report.blocking={report.blocking} "
             f"findings={[f.constraint_id for f in report.findings]}")
        ok = ok and report.blocking is False

        _log("\n[B.3] THE COUPLED-FIELDS COUNTERFACTUAL: exempting 'capacity' alone (dropping "
             "length/len_per_space) must make this CORRECT call refuse ...")
        spec = guardrails_module._WRITE_TARGET_FIELD_SPEC["set_parking_lot_capacity"]
        original_coupled = spec.get("coupled_fields")
        try:
            spec["coupled_fields"] = ()
            narrowed = engine.postwrite(set_action, before_set, after_set, set_env["verification"])
            _log(f"      [counterfactual] coupled_fields=(): blocking={narrowed.blocking} "
                 f"findings={[f.constraint_id for f in narrowed.findings]} "
                 f"evidence={_evidence(narrowed)}")
            ok = ok and bool(narrowed.blocking)
        finally:
            if original_coupled is None:
                spec.pop("coupled_fields", None)
            else:
                spec["coupled_fields"] = original_coupled

        # ---- Phase C (clean removal) -----------------------------------
        _log("\n" + "=" * 78)
        _log("[Phase C] remove_parking_lot -- the clean removal + its counterfactual")
        _log("=" * 78)
        before_remove = build_shadow_model(artifact_manager.working_path)
        remove_params = {"no": new_no}
        _log(f"\n[C.1] remove_parking_lot({remove_params}) ...")
        remove_env = dispatch("remove_parking_lot", dict(remove_params), registry=default_registry)
        _log(f"      envelope = {remove_env}")
        ok = ok and remove_env["status"] == "ok"

        after_remove = build_shadow_model(artifact_manager.working_path)
        _log(f"      reparsed: {len(before_remove['parking_lots'])} -> "
             f"{len(after_remove['parking_lots'])} parking lots")
        ok = ok and len(after_remove["parking_lots"]) == len(before_remove["parking_lots"]) - 1

        remove_action = {"tool": "remove_parking_lot", "params": remove_params}
        _log("\n[C.2] REAL GuardrailEngine.postwrite for the removal ...")
        report = engine.postwrite(
            remove_action, before_remove, after_remove, remove_env["verification"]
        )
        _log(f"      report.blocking={report.blocking} "
             f"findings={[f.constraint_id for f in report.findings]}")
        ok = ok and report.blocking is False

        _log("\n[C.3] THE COUNTERFACTUAL for _ALLOWS_REMOVED_OBJECTS_IN['remove_parking_lot'] ...")
        removed_popped = _counterfactual(
            engine, guardrails_module, remove_action, before_remove, after_remove,
            remove_env["verification"], "_ALLOWS_REMOVED_OBJECTS_IN", "remove_parking_lot",
            None, "POPPED ENTIRELY",
        )
        _log(f"      => popped refuses: {removed_popped}")
        ok = ok and removed_popped
    finally:
        clear_active_edit_session()
        try:
            session.close()
        except Exception:
            pass

    _log(f"\n[A-C] working copy left at {artifact_manager.working_path}")
    return ok


def phase_c_refusal() -> bool:
    """The flagship refusal, on the only network that can host it."""
    _log("\n" + "=" * 78)
    _log("[Phase C2] remove_parking_lot REFUSES a lot a parking route still references")
    _log("=" * 78)

    from services.agent.edit_session import (
        VissimEditSession,
        clear_active_edit_session,
        set_active_edit_session,
    )
    from services.agent.state import ArtifactManager
    import services.agent.tools_edit  # noqa: F401
    from services.agent.toolkit import default_registry, dispatch

    if not REFERENCED_EXAMPLE.exists():
        _log(f"      SKIPPED -- {REFERENCED_EXAMPLE} not present on this machine")
        return True

    work_dir = _fresh_work_dir("phaseC2")
    artifact_manager = ArtifactManager(
        source_inpx=next(REFERENCED_EXAMPLE.glob("*.inpx")), work_dir=work_dir
    )
    artifact_manager.clone()
    counts_before = _parking_counts(artifact_manager.working_path)
    _log(f"[C2.1] Cloned {REFERENCED_EXAMPLE.name}: {counts_before}")

    root = ET.parse(artifact_manager.working_path).getroot()
    lots = [el.get("no") for el in root.findall(".//parkingLots/parkingLot")]
    referenced = {el.get("parkLot") for el in root.findall(".//vehicleRouteParking")}
    target = next((no for no in lots if no in referenced), None)
    _log(f"      {len(lots)} lots, {len(referenced)} of them referenced by a parking route; "
         f"target = {target}")
    if target is None:
        _log("      SKIPPED -- no referenced lot found")
        return True

    ok = True
    session = VissimEditSession(artifact_manager.working_path)
    try:
        set_active_edit_session(session)
        _log(f"\n[C2.2] remove_parking_lot(no={target!r}) -- must REFUSE before touching COM ...")
        env = dispatch("remove_parking_lot", {"no": target}, registry=default_registry)
        _log(f"      envelope = {env}")
        _log(f"      message length = {len(env.get('message', ''))} chars "
             f"(toolkit.dispatch caps exception text at 300)")
        ok = ok and env["status"] == "error"
        ok = ok and "still referenced by" in env.get("message", "")

        counts_after = _parking_counts(artifact_manager.working_path)
        _log(f"[C2.3] persisted counts unchanged: {counts_before} -> {counts_after}")
        ok = ok and counts_after == counts_before
    finally:
        clear_active_edit_session()
        try:
            session.close()
        except Exception:
            pass
    return ok


def phase_d(working: Path) -> bool:
    _log("\n" + "=" * 78)
    _log("[Phase D] The COM load oracle over the final working copy")
    _log("=" * 78)
    from services.inpx_structure import run_load_oracle

    result = run_load_oracle(working)
    _log(f"      load_ok={result.get('load_ok')} init_ok={result.get('init_ok')} "
         f"load_error={str(result.get('load_error'))[:160]!r} "
         f"init_error={str(result.get('init_error'))[:160]!r}")
    return bool(result.get("load_ok") and result.get("init_ok"))


def main() -> int:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.exists():
        _log(f"Example network not found: {example_dir}")
        return 2

    _log("#" * 78)
    _log(f"# Q6 unit (2) parking-lot LIVE CHECK -- {example_dir.name}")
    _log("#" * 78)

    results: dict[str, bool] = {}
    results["A-C"] = phase_abc(example_dir)
    results["C2"] = phase_c_refusal()

    working = None
    for candidate in sorted(WORK_ROOT.glob("phaseABC_*/*.inpx")):
        working = candidate
    results["D"] = phase_d(working) if working else False

    _log("\n" + "=" * 78)
    _log("SUMMARY")
    _log("=" * 78)
    for phase, ok in results.items():
        _log(f"  [{phase}] {'PASS' if ok else 'FAIL'}")
    _log(f"\ntranscript: {WORK_ROOT / 'live_check.log'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

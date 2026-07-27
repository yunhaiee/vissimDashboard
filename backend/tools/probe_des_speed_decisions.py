"""v2a queue unit ⑧ Phase 1 EMPIRICAL PROBE: desired-speed decisions
(``DesSpeedDecisions`` -> per-vehicle-class distribution assignments) over
COM, plus the ``DesSpeedDistributions`` collection the write needs to
validate a target distribution actually exists.

Static XML pre-scan (already run, not repeated here at runtime -- see the
findings pasted into ``services.agent.tools_network`` once this unit ships):
a network-wide grep across all 25 real Vissim 2026 example networks found 17
with real ``<desSpeedDecision>`` elements (584 decisions total, zero of them
carrying a non-empty ``name`` attribute -- see the "pairing reality check"
note below). Beijing.CN (the usual v2-unit pick) has ZERO -- this unit uses
"Roundabout Billings.US" instead (5 decisions, all two-vehicle-class,
verified via a raw grep of the .inpx before ever touching COM):

    <desSpeedDecisions>
      <desSpeedDecision anmFlag="false" lane="6 1" name="" no="6" pos="108.744"
                         timeFrom="0" timeTo="2147483647">
        <vehClassDesSpeedDistr>
          <vehClassDesSpeedDistribution desSpeedDistr="50" vehClass="10"/>
          <vehClassDesSpeedDistribution desSpeedDistr="50" vehClass="20"/>
        </vehClassDesSpeedDistr>
      </desSpeedDecision>
      ... (no=7,8,9 identical shape, desSpeedDistr=50)
      <desSpeedDecision ... no="10" ...>
        <vehClassDesSpeedDistr>
          <vehClassDesSpeedDistribution desSpeedDistr="25" vehClass="10"/>
          <vehClassDesSpeedDistribution desSpeedDistr="25" vehClass="20"/>
        </vehClassDesSpeedDistr>
      </desSpeedDecision>
    </desSpeedDecisions>

``lane`` is a two-token "<linkNo> <laneNo>" attribute, the SAME encoding
connector endpoints/conflict-area-adjacent constructs use elsewhere in this
codebase (Phase 3's ``_parse_connector_endpoint`` precedent). Distributions
referenced by ``desSpeedDistr`` (50 -> "55 mph", 25 -> "25 mph") live in a
SEPARATE top-level ``<speedDistributions><speedDistribution no="50"
name="55 mph">`` container -- the SAME element type
``services.simulation_network_builder``'s ``set_reduced_speed_area`` path
already injects (v2 unit ②); no dedicated ``<desSpeedDistributions>``
container exists in the .inpx at all, confirming desired-speed decisions and
reduced-speed areas SHARE one distribution object type at the XML level.
Whether that holds at the COM level too (one ``Net.*Distributions``
collection, or two) is Phase 1a's job to confirm from the real type library,
never guessed.

**Pairing reality check (already answered statically, reported here for the
record):** a broader scan of every real desSpeedDecision element across all
17 networks that have any (584 decisions) found ``name=""`` on EVERY SINGLE
ONE -- zero counterexamples. There is no grouping attribute, no naming
convention, nothing to key a "this is the restore for that" relationship on
anywhere in the .inpx. Pairing is confirmed to be a PURE HUMAN CONVENTION
(matching link + adjacent pos, by construction), never a structural link
this codebase could discover automatically -- confirming
``constraints.py``'s existing forward-declared ``speed_decision_unpaired``
CATALOG-GAP placeholder's own premise.

This probe answers, on a live Vissim 2026 instance (attach_to_running=False,
Roundabout Billings.US clone, closed in a ``finally`` block):

[Phase 1a] STATIC type-library scan (no COM instance, no licence consumed) --
same technique every prior v2-unit probe used: dump every interface whose
name contains "speed" (case-insensitive) so the REAL COM attribute/
collection ids come from the type library, never a guess from the XML
attribute spelling (the CycTm/NodeRes landmines both burned a plausible-
looking guess; unit ⑦ found RelFlow scalar-only where unit ⑥ found it
indexed-only -- always verify, never assume).

[Phase 1b] LIVE probe on a Roundabout Billings.US clone:
  1. ``Net.DesSpeedDecisions`` (name TBD by 1a) ``GetAll()`` count
     cross-checked against the XML element count (5).
  2. Decision-level attribute discovery: No, Name, Lane, Pos, TimeFrom,
     TimeTo -- read back and compared against the XML ground truth.
  3. THE KEY QUESTION: how is the per-vehicle-class distribution assignment
     exposed? Tests BOTH hypotheses live, never assuming either:
       (a) a separate per-decision CHILD COLLECTION (mirrors
           VehCompRelFlows/VehRoutSta precedent) -- try
           ``VehClassDesSpeedDistr`` (matches the XML child tag) and
           siblings, then entry-level ``VehClass``/``DesSpeedDistr``
           AttValue.
       (b) a DIRECT sub-attributed AttValue on the decision object itself,
           ``DesSpeedDistr(<vehClassNo>)`` (the task brief's own hypothesis
           -- unit ⑤ found indexed sub-attributes fail CLOSED for a
           genuinely-absent index, unit ⑦ found RelFlow has NO indexed form
           at all for a similar-looking per-entry value, so this must be
           tested, not assumed either way).
  4. ``Net.DesSpeedDistributions`` (name TBD by 1a) enumeration: No + Name,
     cross-checked against the XML ``<speedDistribution>`` ground truth (50
     -> "55 mph", 25 -> "25 mph", etc.) -- needed so the write tool can
     validate a target distribution actually EXISTS before writing a bogus
     reference.
  5. ItemByKey error-behavior data points: bogus decision no on
     Net.DesSpeedDecisions, bogus vehClass on whichever per-class mechanism
     1b.3 confirms real, and (if a genuine SetAttValue with a bogus
     distribution number is attempted) fail-open vs fail-closed for a
     dangling distribution reference -- the central "does COM itself protect
     against writing a bogus distribution no" question the write tool's own
     validation strategy depends on.
  6. ONE live write on a MULTI-class decision (no=6, vehClass 10+20, both
     distr=50): change vehClass=10's assignment to distr=25, read back
     immediately, AND read back vehClass=20's own assignment in the SAME
     decision -- sibling-untouched sanity check (expected trivially true --
     there is no RelFlow-style shared-sum semantics here at all -- but
     verify, never assume).
  7. A SECOND live write simulating the "restore" half of a pair: on decision
     no=10 (currently distr=25 for both classes), set vehClass=10 back to
     distr=50 -- proves the SAME mechanism works on a DIFFERENT decision
     object, the atomic-pair write tool's actual shape.
  8. Persist (save_net_as, the sanctioned VissimEditSession.persist() route)
     + grep the saved XML's desSpeedDistr attributes for BOTH writes + a
     fresh COM reload to confirm both writes survived a full round trip.

Never touches the original Examples Demo folder (00-EXECUTION-GUIDE.md rule
4) -- everything happens on a work-dir clone. Always disconnects every
dedicated Vissim instance in a ``finally`` block.

Usage: python tools/probe_des_speed_decisions.py [example_dir]
Default example: Roundabout Billings.US (2026 Examples Demo) -- Beijing.CN
(every other v2-unit probe's default) has ZERO desSpeedDecision elements.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
import xml.etree.ElementTree as ET
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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "des-speed-decision-probe"

_CONTROL_BOGUS_ATTR = "ThisAttributeDoesNotExistProbe12345"

_DECISIONS_COLLECTION_CANDIDATES = ["DesSpeedDecisions", "DesiredSpeedDecisions"]
_DISTRIBUTIONS_COLLECTION_CANDIDATES = ["DesSpeedDistributions", "SpeedDistributions"]
_VEH_CLASS_CHILD_CANDIDATES = [
    "VehClassDesSpeedDistr",
    "DesSpeedDecisionVehClasses",
    "VehClassDesSpeedDistributions",
]
_NO_CANDIDATES = ["No", "no"]
_NAME_CANDIDATES = ["Name", "name"]
_LANE_CANDIDATES = ["Lane", "lane"]
_POS_CANDIDATES = ["Pos", "pos"]
_VEH_CLASS_CANDIDATES = ["VehClass", "VehicleClass"]
_DES_SPEED_DISTR_SCALAR_CANDIDATES = ["DesSpeedDistr", "DesiredSpeedDistr"]


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh_work_dir(name: str) -> Path:
    for stale in WORK_ROOT.glob(f"{name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    return WORK_ROOT / f"{name}_{os.getpid()}"


# ---------------------------------------------------------------------------
# Phase 1a: static type-library scan (no COM instance required)
# ---------------------------------------------------------------------------


def probe_typelib() -> None:
    import win32com.client.makepy as makepy
    import win32com.client.selecttlb as selecttlb
    from win32com.client import gencache

    _log("[1a] Locating the installed 'Vissim Object Library' type library via selecttlb...")
    candidates = [
        tlb
        for tlb in selecttlb.EnumTlbs()
        if "vissim object library" in (getattr(tlb, "desc", "") or "").lower()
    ]
    if not candidates:
        raise RuntimeError(
            "No registered 'Vissim Object Library' type library found via "
            "win32com.client.selecttlb.EnumTlbs() -- cannot run the static scan."
        )

    def _hex_int(value: str) -> int:
        return int(value, 16)

    match = max(candidates, key=lambda t: (_hex_int(t.major), _hex_int(t.minor)))
    for tlb in candidates:
        _log(f"      registered: {tlb.desc}  clsid={tlb.clsid} major={tlb.major} minor={tlb.minor}")
    _log(f"      using: {match.desc}  clsid={match.clsid}")

    major_i, minor_i = _hex_int(match.major), _hex_int(match.minor)
    makepy.GenerateFromTypeLibSpec((match.clsid, match.lcid, major_i, minor_i))
    mod = gencache.EnsureModule(match.clsid, match.lcid, major_i, minor_i)
    src_path = Path(mod.__file__)
    _log(f"      Generated wrapper: {src_path} ({src_path.stat().st_size} bytes)")
    lines = src_path.read_text(encoding="utf-8", errors="replace").splitlines()

    class_name = None
    in_target = False
    target_hits = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("class I") and "(DispatchBaseClass)" in stripped:
            class_name = stripped.split("(")[0].removeprefix("class ").strip()
            in_target = "speed" in class_name.lower()
            if in_target:
                target_hits += 1
                _log(f"\n      -- {class_name} (DispatchBaseClass) --")
            continue
        if in_target and stripped.startswith("def ") and "(self" in stripped:
            _log(f"        {stripped}")
        if in_target and (stripped.startswith("_prop_map_get_") or stripped.startswith("_prop_map_put_")):
            _log(f"        {stripped}")

    if target_hits == 0:
        _log("      NO interface with 'speed' in its name found anywhere in the type library.")
    else:
        _log(f"\n      Found {target_hits} '*speed*' interface(s) in the type library (listed above).")


# ---------------------------------------------------------------------------
# Phase 1b: live probe against a Roundabout Billings.US clone
# ---------------------------------------------------------------------------


def _xml_des_speed_decisions(inpx_path: Path) -> list[dict[str, Any]]:
    root = ET.parse(inpx_path).getroot()
    result = []
    for dec in root.findall(".//desSpeedDecision"):
        entries = [dict(e.attrib) for e in dec.findall(".//vehClassDesSpeedDistribution")]
        result.append({**dict(dec.attrib), "entries": entries})
    return result


def _xml_speed_distributions(inpx_path: Path) -> list[dict[str, Any]]:
    root = ET.parse(inpx_path).getroot()
    return [dict(d.attrib) for d in root.findall(".//speedDistributions/speedDistribution")]


def _first_working_collection(net: Any, candidates: list[str], label: str) -> tuple[str | None, Any]:
    for name in candidates:
        try:
            obj = getattr(net, name)
            count = len(list(obj.GetAll()))
            _log(f"        [WORKS] Net.{name} -> GetAll() count = {count}")
            return name, obj
        except Exception as exc:
            _log(f"        [no]    Net.{name} -> {type(exc).__name__}: {exc}")
    _log(f"        NONE of the candidate collection names for {label} worked.")
    return None, None


def _first_working_child_collection(item: Any, candidates: list[str], label: str) -> tuple[str | None, Any]:
    for name in candidates:
        try:
            obj = getattr(item, name)
            count = len(list(obj.GetAll()))
            _log(f"        [WORKS] .{name} -> GetAll() count = {count}")
            return name, obj
        except Exception as exc:
            _log(f"        [no]    .{name} -> {type(exc).__name__}: {exc}")
    _log(f"        NONE of the candidate child-collection names for {label} worked.")
    return None, None


def _first_working_attr(item: Any, candidates: list[str], label: str) -> tuple[str | None, Any]:
    for attr_id in candidates:
        try:
            value = item.AttValue(attr_id)
            _log(f"        [WORKS] AttValue({attr_id!r}) -> {value!r}")
            return attr_id, value
        except Exception as exc:
            _log(f"        [no]    AttValue({attr_id!r}) -> {type(exc).__name__}: {exc}")
    _log(f"        NONE of the candidate ids for {label} worked on this item.")
    return None, None


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    _log("#" * 78)
    _log("# PHASE 1a -- static type-library scan (no COM instance, no licence used)")
    _log("#" * 78)
    probe_typelib()

    _log("\n" + "#" * 78)
    _log("# PHASE 1b -- live probe on a Roundabout Billings.US clone")
    _log("#" * 78)

    work_dir = _fresh_work_dir(example_dir.name)
    shutil.copytree(example_dir, work_dir)
    inpx_files = sorted(work_dir.glob("*.inpx"))
    if not inpx_files:
        raise SystemExit(f"No .inpx in {work_dir}")
    working = inpx_files[0]
    _log(f"[1b.0] Working copy: {working}")

    xml_before = _xml_des_speed_decisions(working)
    _log(f"       XML <desSpeedDecision> count = {len(xml_before)}")
    for dec in xml_before:
        entry_summary = [(e["vehClass"], e["desSpeedDistr"]) for e in dec["entries"]]
        _log(f"       decision no={dec['no']} lane={dec['lane']!r} entries(vehClass,desSpeedDistr)={entry_summary}")

    xml_distributions = _xml_speed_distributions(working)
    _log(f"       XML <speedDistribution> count = {len(xml_distributions)}")
    for d in xml_distributions:
        _log(f"       distribution no={d['no']} name={d.get('name')!r}")

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    try:
        _log("[1b.1] Launching a dedicated Vissim instance (attach_to_running=False)...")
        started = time.monotonic()
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        _log(f"       Connected via {controller.prog_id} in {time.monotonic() - started:.1f}s")

        _log(f"[1b.2] Loading network: {working.name}")
        controller.load_network(str(working))

        _log("\n[1b.3] Decisions collection name discovery on Net:")
        coll_name, decisions_coll = _first_working_collection(
            controller.vissim.Net, _DECISIONS_COLLECTION_CANDIDATES, "DesSpeedDecisions"
        )
        if decisions_coll is None:
            raise SystemExit("FAIL: could not find the des-speed-decisions collection on Net.")

        decision_items = list(decisions_coll.GetAll())
        _log(f"       Net.{coll_name}.GetAll() count = {len(decision_items)} (XML count = {len(xml_before)})")

        _log("\n[1b.4] Distributions collection name discovery on Net:")
        distr_coll_name, distr_coll = _first_working_collection(
            controller.vissim.Net, _DISTRIBUTIONS_COLLECTION_CANDIDATES, "DesSpeedDistributions"
        )
        distr_items = list(distr_coll.GetAll()) if distr_coll is not None else []
        distr_nos: list[int] = []
        if distr_coll is not None:
            distr_no_id, _ = _first_working_attr(distr_items[0], _NO_CANDIDATES, "No (distribution)")
            distr_name_id, _ = _first_working_attr(distr_items[0], _NAME_CANDIDATES, "Name (distribution)")
            for item in distr_items:
                try:
                    distr_nos.append(int(item.AttValue(distr_no_id)))
                except Exception:
                    continue
            _log(f"       Distribution nos found via COM: {sorted(distr_nos)}")
            _log(f"       Distribution nos found via XML: {sorted(int(d['no']) for d in xml_distributions)}")

        decision_no_by_multi = next(
            d["no"] for d in xml_before if len(d["entries"]) >= 2
        )
        first_decision = next(item for item in decision_items if str(item.AttValue("No")) == decision_no_by_multi)
        _log(f"\n[1b.5] Decision-level attribute discovery on decision no={decision_no_by_multi} (multi-class):")
        dec_no_id, dec_no_val = _first_working_attr(first_decision, _NO_CANDIDATES, "No")
        dec_name_id, dec_name_val = _first_working_attr(first_decision, _NAME_CANDIDATES, "Name")
        dec_lane_id, dec_lane_val = _first_working_attr(first_decision, _LANE_CANDIDATES, "Lane")
        dec_pos_id, dec_pos_val = _first_working_attr(first_decision, _POS_CANDIDATES, "Pos")

        xml_first_dec = next(d for d in xml_before if d["no"] == decision_no_by_multi)
        _log(f"       XML ground truth for this decision: no={xml_first_dec['no']} lane={xml_first_dec['lane']!r} pos={xml_first_dec['pos']!r}")

        # -------------------------------------------------------------
        # [1b.6] THE KEY QUESTION: per-vehicle-class assignment mechanism.
        # Hypothesis (a): a child collection, mirroring VehCompRelFlows.
        # -------------------------------------------------------------
        _log("\n[1b.6] Hypothesis (a): per-decision CHILD COLLECTION for veh-class assignments...")
        child_coll_name, child_coll = _first_working_child_collection(
            first_decision, _VEH_CLASS_CHILD_CANDIDATES, "VehClassDesSpeedDistr"
        )
        child_items = list(child_coll.GetAll()) if child_coll is not None else []
        veh_class_id = None
        des_speed_distr_id = None
        if child_items:
            first_entry = child_items[0]
            veh_class_id, veh_class_val = _first_working_attr(first_entry, _VEH_CLASS_CANDIDATES, "VehClass (entry)")
            des_speed_distr_id, des_speed_distr_val = _first_working_attr(
                first_entry, _DES_SPEED_DISTR_SCALAR_CANDIDATES, "DesSpeedDistr (entry, scalar)"
            )
            _log(f"       Entry-level ground truth (first entry): {xml_first_dec['entries'][0]}")

        # -------------------------------------------------------------
        # Hypothesis (b): a direct sub-attributed AttValue on the decision
        # object itself, DesSpeedDistr(<vehClassNo>).
        # -------------------------------------------------------------
        _log("\n[1b.7] Hypothesis (b): DIRECT sub-attributed AttValue on the decision object...")
        sample_veh_class = xml_first_dec["entries"][0]["vehClass"]
        for candidate_form in (
            f"DesSpeedDistr({sample_veh_class})",
            f"DesiredSpeedDistr({sample_veh_class})",
        ):
            try:
                value = first_decision.AttValue(candidate_form)
                _log(f"        [WORKS] first_decision.AttValue({candidate_form!r}) -> {value!r}")
            except Exception as exc:
                _log(f"        [no]    first_decision.AttValue({candidate_form!r}) -> {type(exc).__name__}: {exc}")

        # -------------------------------------------------------------
        # [1b.8] CONTROL: bogus attribute id (fail-open/closed check).
        # -------------------------------------------------------------
        _log("\n[1b.8] CONTROL: bogus attribute id on the real decision object (fail-open/closed check)...")
        try:
            bogus = first_decision.AttValue(_CONTROL_BOGUS_ATTR)
            _log(f"        [CONTROL] AttValue({_CONTROL_BOGUS_ATTR!r}) -> {bogus!r} (fails OPEN)")
        except Exception as exc:
            _log(f"        [CONTROL] AttValue({_CONTROL_BOGUS_ATTR!r}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        _log("\n[1b.9] Bogus decision no -- ItemByKey error behavior...")
        try:
            bogus_dec = decisions_coll.ItemByKey(999999)
            _log(f"        ItemByKey(999999) -> {bogus_dec!r} (UNEXPECTED -- did not raise)")
        except Exception as exc:
            _log(f"        ItemByKey(999999) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        if distr_coll is not None:
            _log("\n[1b.10] Bogus distribution no -- ItemByKey error behavior on DesSpeedDistributions...")
            try:
                bogus_distr = distr_coll.ItemByKey(999999)
                _log(f"        ItemByKey(999999) -> {bogus_distr!r} (UNEXPECTED -- did not raise)")
            except Exception as exc:
                _log(f"        ItemByKey(999999) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        if child_coll is not None:
            _log("\n[1b.11] Does the veh-class child collection expose ItemByKey?...")
            has_item_by_key = hasattr(child_coll, "ItemByKey")
            _log(f"        hasattr(child_coll, 'ItemByKey') = {has_item_by_key}")
            if has_item_by_key:
                try:
                    bogus_entry = child_coll.ItemByKey(999999)
                    _log(f"        ItemByKey(999999) -> {bogus_entry!r} (UNEXPECTED -- did not raise)")
                except Exception as exc:
                    _log(f"        ItemByKey(999999) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        # -------------------------------------------------------------
        # [1b.12] MULTI-class decision write: change vehClass=10's
        # assignment, verify vehClass=20's own assignment is untouched.
        # -------------------------------------------------------------
        if child_coll is not None and veh_class_id and des_speed_distr_id:
            _log(f"\n[1b.12] MULTI-class write test -- decision no={decision_no_by_multi}")
            before_vals: dict[int, Any] = {}
            for e in child_items:
                vc = int(e.AttValue(veh_class_id))
                before_vals[vc] = e.AttValue(des_speed_distr_id)
            _log(f"        BEFORE -- every entry's DesSpeedDistr (by vehClass): {before_vals}")

            target_veh_class = min(before_vals)
            target_entry = next(e for e in child_items if int(e.AttValue(veh_class_id)) == target_veh_class)
            old_val = before_vals[target_veh_class]
            # Write to a REAL, different distribution (25 mph -- confirmed to
            # exist on this network above) rather than an arbitrary number.
            new_val = 25 if str(old_val) != "25" else 50
            _log(f"        SetAttValue({des_speed_distr_id!r}, {new_val}) on vehClass={target_veh_class} (was {old_val!r})...")
            try:
                target_entry.SetAttValue(des_speed_distr_id, new_val)
                _log("        SetAttValue succeeded (no exception).")
            except Exception as exc:
                _log(f"        SetAttValue RAISED: {type(exc).__name__}: {exc}")

            after_vals: dict[int, Any] = {}
            for e in child_items:
                vc = int(e.AttValue(veh_class_id))
                after_vals[vc] = e.AttValue(des_speed_distr_id)
            _log(f"        AFTER -- every entry's DesSpeedDistr (by vehClass): {after_vals}")
            siblings_changed = {
                k: (before_vals[k], after_vals[k])
                for k in before_vals
                if k != target_veh_class and str(before_vals[k]) != str(after_vals[k])
            }
            _log(
                f"        Sibling entries whose DesSpeedDistr VALUE changed as a side effect: {siblings_changed} "
                "(empty == independent per-class assignment, as expected)"
            )

            # -------------------------------------------------------------
            # [1b.12b] CONTROL: write a BOGUS distribution number -- does
            # SetAttValue itself validate the target exists (fail closed),
            # or accept any integer (fail open, needs our own validation)?
            # -------------------------------------------------------------
            _log("\n[1b.12b] CONTROL: SetAttValue with a BOGUS distribution no (999999)...")
            try:
                target_entry.SetAttValue(des_speed_distr_id, 999999)
                readback_bogus = target_entry.AttValue(des_speed_distr_id)
                _log(
                    f"        SetAttValue(..., 999999) succeeded (no exception); readback = {readback_bogus!r} "
                    "(fails OPEN -- our own validation against DesSpeedDistributions is required)"
                )
                # restore to the intended real value before persisting.
                target_entry.SetAttValue(des_speed_distr_id, new_val)
            except Exception as exc:
                _log(f"        SetAttValue(..., 999999) RAISED: {type(exc).__name__}: {exc} (fails CLOSED)")

            # -------------------------------------------------------------
            # [1b.13] SECOND live write on a DIFFERENT decision (no=10) --
            # simulates the "restore" half of a pair.
            # -------------------------------------------------------------
            restore_decision_no = next(
                (d["no"] for d in xml_before if d["no"] != decision_no_by_multi and len(d["entries"]) >= 2),
                None,
            )
            restore_after_vals: dict[int, Any] = {}
            restore_target_veh_class = None
            restore_new_val = None
            if restore_decision_no is not None:
                _log(f"\n[1b.13] SECOND write (restore-pair simulation) -- decision no={restore_decision_no}")
                restore_decision_item = next(
                    item for item in decision_items if str(item.AttValue("No")) == restore_decision_no
                )
                restore_child_coll = getattr(restore_decision_item, child_coll_name)
                restore_child_items = list(restore_child_coll.GetAll())
                restore_before_vals: dict[int, Any] = {}
                for e in restore_child_items:
                    vc = int(e.AttValue(veh_class_id))
                    restore_before_vals[vc] = e.AttValue(des_speed_distr_id)
                _log(f"        BEFORE -- decision {restore_decision_no} entries: {restore_before_vals}")
                restore_target_veh_class = min(restore_before_vals)
                restore_target_entry = next(
                    e for e in restore_child_items if int(e.AttValue(veh_class_id)) == restore_target_veh_class
                )
                restore_old_val = restore_before_vals[restore_target_veh_class]
                restore_new_val = 50 if str(restore_old_val) != "50" else 25
                _log(
                    f"        SetAttValue({des_speed_distr_id!r}, {restore_new_val}) on vehClass="
                    f"{restore_target_veh_class} (was {restore_old_val!r})..."
                )
                restore_target_entry.SetAttValue(des_speed_distr_id, restore_new_val)
                for e in restore_child_items:
                    vc = int(e.AttValue(veh_class_id))
                    restore_after_vals[vc] = e.AttValue(des_speed_distr_id)
                _log(f"        AFTER -- decision {restore_decision_no} entries: {restore_after_vals}")

            # -------------------------------------------------------------
            # [1b.14] Persist + reparse + fresh COM reload.
            # -------------------------------------------------------------
            tmp_path = working.with_name(working.stem + "_saving" + working.suffix)
            _log(f"\n[1b.14] Persisting via save_net_as({tmp_path.name}) + os.replace onto {working.name}...")
            controller.save_net_as(tmp_path)
            os.replace(tmp_path, working)
            _log(f"        Persisted. Working copy size = {working.stat().st_size} bytes")

            xml_after = _xml_des_speed_decisions(working)
            xml_multi_after = next(d for d in xml_after if d["no"] == decision_no_by_multi)
            _log(f"[1b.15] Saved XML decision no={decision_no_by_multi} entries: {xml_multi_after['entries']}")
            if restore_decision_no is not None:
                xml_restore_after = next(d for d in xml_after if d["no"] == restore_decision_no)
                _log(f"        Saved XML decision no={restore_decision_no} entries: {xml_restore_after['entries']}")

            _log("\n[1b.16] Fresh COM reload of the saved file (separate connection)...")
            controller.disconnect()
            verify_controller = VissimController()
            verify_controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
            verify_controller.load_network(str(working))
            reload_decisions = getattr(verify_controller.vissim.Net, coll_name)
            reload_multi_dec = reload_decisions.ItemByKey(int(decision_no_by_multi))
            reload_child = getattr(reload_multi_dec, child_coll_name)
            reload_target_entry = next(
                e for e in reload_child.GetAll() if int(e.AttValue(veh_class_id)) == target_veh_class
            )
            reload_val = reload_target_entry.AttValue(des_speed_distr_id)
            _log(
                f"        Reload decision no={decision_no_by_multi} vehClass={target_veh_class} "
                f"DesSpeedDistr={reload_val!r} (intended {new_val!r}) -> match: {str(reload_val) == str(new_val)}"
            )
            if restore_decision_no is not None:
                reload_restore_dec = reload_decisions.ItemByKey(int(restore_decision_no))
                reload_restore_child = getattr(reload_restore_dec, child_coll_name)
                reload_restore_entry = next(
                    e for e in reload_restore_child.GetAll()
                    if int(e.AttValue(veh_class_id)) == restore_target_veh_class
                )
                reload_restore_val = reload_restore_entry.AttValue(des_speed_distr_id)
                _log(
                    f"        Reload decision no={restore_decision_no} vehClass={restore_target_veh_class} "
                    f"DesSpeedDistr={reload_restore_val!r} (intended {restore_new_val!r}) -> match: "
                    f"{str(reload_restore_val) == str(restore_new_val)}"
                )

            controller = verify_controller  # so the finally block disconnects this one

            _log("\n" + "#" * 78)
            _log("# SUMMARY")
            _log("#" * 78)
            _log(f"Decisions collection: Net.{coll_name}")
            _log(f"Distributions collection: Net.{distr_coll_name}")
            _log(f"Per-decision veh-class child collection: .{child_coll_name}")
            _log(f"Entry attribute ids: VehClass={veh_class_id!r} DesSpeedDistr={des_speed_distr_id!r}")
            _log(f"Sibling untouched on multi-class write: {not siblings_changed}")
        else:
            _log("\n[SKIP] Hypothesis (a)'s child collection did not resolve -- see [1b.6]/[1b.7] output above.")

    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        _log("\nDisconnected the dedicated Vissim instance.")


if __name__ == "__main__":
    main()

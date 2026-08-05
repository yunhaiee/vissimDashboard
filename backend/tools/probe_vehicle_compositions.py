"""v2 unit (7) Phase 1 EMPIRICAL PROBE: vehicle compositions
(``VehicleCompositions`` -> per-composition relative-flow entries) over COM.

Static XML pre-scan (already run, not repeated here at runtime -- see the
findings pasted into ``services.agent.tools_network`` once this unit ships):
a network-wide grep across all 21 real Vissim 2026 example networks that
carry any (73 ``<vehicleComposition>`` elements total) confirmed the XML
shape:

    <vehicleComposition no="1" name="5% HGV, 5% LGV, 5% Bus">
      <vehCompRelFlows>
        <vehicleCompositionRelativeFlow desSpeedDistr="65" relFlow="0.85" vehType="100"/>
        <vehicleCompositionRelativeFlow desSpeedDistr="50" relFlow="0.05" vehType="190"/>
        ...
      </vehCompRelFlows>
    </vehicleComposition>

``<vehicleCompositionRelativeFlow>`` carries ONLY ``desSpeedDistr``/
``relFlow``/``vehType`` -- no ``no`` attribute of its own (unlike conflict
areas/routes/vehicle inputs), so there is no ready-made single-token id for
an entry other than ``vehType``. The static scan found ZERO compositions,
across all 73, where the same ``vehType`` repeats within one composition --
so ``vehType`` alone is a plausible entry identity, but this must still be
confirmed as the COM-native key (does ``ItemByKey`` on the relative-flow
collection actually accept the vehType number?) rather than assumed from the
XML shape alone (the CycTm/NodeRes landmines in docs/phase2/DECISIONS.md
both burned a plausible-looking guess). Every example network's relFlow
values across one composition's entries happened to SUM TO 1.0 (e.g.
0.85+0.05+0.05+0.05) -- this LOOKS like a pre-normalized share, which is
exactly the assumption baked into ``constraints.py``'s forward-declared
CATALOG-GAP check ``vehicle_composition_share_redistribution_gap`` (#36,
"vehicle mixes are relative shares normalized to 100%; raising the truck
share LOWERS the car share"). Whether that assumption is actually true at
the COM SetAttValue layer -- i.e. whether Vissim RENORMALIZES sibling
entries on write, matching #36's docstring -- or whether, like
``set_route_relative_flow`` (v2 unit (6): RelFlow is a RAW weight, siblings
untouched, share only computed at run time), the "sums to 1.0" shape is
merely an authoring convention and not an enforced invariant, is the
CENTRAL EMPIRICAL QUESTION this probe exists to answer. Do not trust the
placeholder docstring's premise; verify live.

Vehicle inputs reference a composition via the ``vehComp`` attribute -- but
this attribute lives on EACH ``<timeIntervalVehVolume>`` child (i.e. it is
interval-scoped, potentially different per interval), not once on the
``<vehicleInput>`` element itself (verified: Beijing's ``<vehicleInput no="1">``
carries a single interval with ``vehComp="1"``). "Which vehicle inputs
reference composition N" must therefore scan every interval of every vehicle
input, not just interval 1.

This probe answers, on a live Vissim 2026 instance (attach_to_running=False,
Beijing.CN clone, closed in a ``finally`` block):

[Phase 1a] STATIC type-library scan (no COM instance, no licence consumed) --
same technique ``tools/conflict_area_probe.py``/``routing_decision_probe.py``
used: dump every interface whose name contains "composition"
(case-insensitive) so the REAL COM attribute ids come from the type library,
never a guess from the XML attribute spelling.

[Phase 1b] LIVE probe on a Beijing.CN clone:
  1. ``Net.VehicleCompositions`` (name TBD by 1a) ``GetAll()`` count
     cross-checked against the working copy's own ``<vehicleComposition>``
     element count (3 on Beijing: no=1 four entries, no=2 two entries, no=4
     one entry).
  2. Per-composition child relative-flow collection discovery (``VehCompRelFlows``
     per the XML child tag, or whatever 1a's scan reveals) and its own
     ``GetAll()`` count per composition, cross-checked against the XML entry
     count.
  3. Attribute-id discovery on ONE relative-flow entry: VehType,
     DesSpeedDistr, RelFlow (scalar vs indexed like route RelFlow(i)?) --
     read back and compared against the XML ground truth.
  4. Entry identity: does the relative-flow collection's ``ItemByKey``
     accept the vehType number directly? What does ``AttValue('No')`` (if it
     exists at all) return on an entry?
  5. Bogus composition-no / bogus vehType-within-a-real-composition read --
     per-object AttValue error-behavior data point (v2 unit (4) found
     per-object ConflictArea fails CLOSED, v2 unit (6) found routes/routing
     decisions also fail CLOSED -- this probe checks whether vehicle
     compositions/entries follow the same pattern or are a new data point).
  6. ONE live write: SetAttValue RelFlow on ONE entry of a MULTI-entry
     composition (no=1, 4 entries), read back immediately, AND read back
     every SIBLING entry's RelFlow in the SAME composition -- THE central
     renormalization question.
  7. A SECOND live write: SetAttValue RelFlow on the ONE entry of a
     single-entry composition (no=4, "Cycle") -- what happens when there is
     nothing to redistribute against?
  8. Persist (save_net_as, the sanctioned VissimEditSession.persist() route)
     + grep the saved XML's relFlow attribute for both writes + a fresh COM
     reload to confirm the write survived a full round trip.
  9. Which vehicle inputs reference each composition (scanning EVERY
     interval of every vehicle input's ``vehComp``, not just interval 1) --
     needed for the impact-warning list on the write tool's approval card.

Never touches the original Examples Demo folder (00-EXECUTION-GUIDE.md rule
4) -- everything happens on a work-dir clone. Always disconnects every
dedicated Vissim instance in a ``finally`` block.

Usage: python tools/probe_vehicle_compositions.py [example_dir]
Default example: Urban Intersection Beijing.CN (2026 Examples Demo) -- same
default as every other live probe/check tool in this directory.
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
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "vehicle-composition-probe"

_CONTROL_BOGUS_ATTR = "ThisAttributeDoesNotExistProbe12345"

_COMPOSITIONS_COLLECTION_CANDIDATES = ["VehicleCompositions", "VehCompositions"]
_REL_FLOWS_CHILD_CANDIDATES = ["VehCompRelFlows", "RelFlows", "VehicleCompositionRelativeFlows"]
_NO_CANDIDATES = ["No", "no"]
_VEH_TYPE_CANDIDATES = ["VehType", "VehicleType"]
_DES_SPEED_DISTR_CANDIDATES = ["DesSpeedDistr", "DesiredSpeedDistr", "DesSpeedDist"]
_REL_FLOW_SCALAR_CANDIDATES = ["RelFlow"]
_REL_FLOW_INDEXED_CANDIDATES = ["RelFlow(1)"]


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
            in_target = "composition" in class_name.lower()
            if in_target:
                target_hits += 1
                _log(f"\n      -- {class_name} (DispatchBaseClass) --")
            continue
        if in_target and stripped.startswith("def ") and "(self" in stripped:
            _log(f"        {stripped}")
        if in_target and (stripped.startswith("_prop_map_get_") or stripped.startswith("_prop_map_put_")):
            _log(f"        {stripped}")

    if target_hits == 0:
        _log("      NO interface with 'composition' in its name found anywhere in the type library.")
    else:
        _log(f"\n      Found {target_hits} '*composition*' interface(s) in the type library (listed above).")


# ---------------------------------------------------------------------------
# Phase 1b: live probe against a Beijing.CN clone
# ---------------------------------------------------------------------------


def _xml_compositions(inpx_path: Path) -> list[dict[str, Any]]:
    root = ET.parse(inpx_path).getroot()
    result = []
    for comp in root.findall(".//vehicleComposition"):
        entries = [dict(rf.attrib) for rf in comp.findall(".//vehicleCompositionRelativeFlow")]
        result.append({**dict(comp.attrib), "entries": entries})
    return result


def _xml_vehicle_input_composition_refs(inpx_path: Path) -> dict[str, list[str]]:
    """{composition_no: [vehicle_input_no, ...]} scanning EVERY interval's
    ``vehComp``, not just interval 1 -- an input can appear more than once if
    multiple intervals reference the same composition (deduped here)."""
    root = ET.parse(inpx_path).getroot()
    refs: dict[str, set[str]] = {}
    for vi in root.findall(".//vehicleInput"):
        vi_no = vi.get("no", "")
        for tv in vi.findall(".//timeIntervalVehVolume"):
            comp_no = tv.get("vehComp")
            if comp_no:
                refs.setdefault(comp_no, set()).add(vi_no)
    return {k: sorted(v, key=lambda x: int(x) if x.isdigit() else 0) for k, v in refs.items()}


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
    _log("# PHASE 1b -- live probe on a Beijing.CN clone")
    _log("#" * 78)

    work_dir = _fresh_work_dir(example_dir.name)
    shutil.copytree(example_dir, work_dir)
    inpx_files = sorted(work_dir.glob("*.inpx"))
    if not inpx_files:
        raise SystemExit(f"No .inpx in {work_dir}")
    working = inpx_files[0]
    _log(f"[1b.0] Working copy: {working}")

    xml_before = _xml_compositions(working)
    _log(f"       XML <vehicleComposition> count = {len(xml_before)}")
    for comp in xml_before:
        entry_summary = [(e["vehType"], e["relFlow"]) for e in comp["entries"]]
        _log(f"       composition no={comp['no']} name={comp['name']!r} entries(vehType,relFlow)={entry_summary}")

    vi_refs_before = _xml_vehicle_input_composition_refs(working)
    _log(f"       Vehicle-input composition references (composition_no -> [vehicle_input_no,...]): {vi_refs_before}")

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

        _log("\n[1b.3] Compositions collection name discovery on Net:")
        coll_name, comps_coll = _first_working_collection(
            controller.vissim.Net, _COMPOSITIONS_COLLECTION_CANDIDATES, "VehicleCompositions"
        )
        if comps_coll is None:
            raise SystemExit("FAIL: could not find the vehicle compositions collection on Net.")

        comp_items = list(comps_coll.GetAll())
        _log(f"       Net.{coll_name}.GetAll() count = {len(comp_items)} (XML count = {len(xml_before)})")

        first_comp = comp_items[0]
        _log("\n[1b.4] Composition-level attribute discovery on the FIRST composition:")
        comp_no_id, comp_no_val = _first_working_attr(first_comp, _NO_CANDIDATES, "No")

        _log("\n[1b.5] Child relative-flow collection name discovery on the FIRST composition:")
        rf_coll_name, rf_coll = _first_working_child_collection(
            first_comp, _REL_FLOWS_CHILD_CANDIDATES, "VehCompRelFlows"
        )
        if rf_coll is None:
            raise SystemExit("FAIL: could not find the per-composition relative-flow child collection.")

        rf_items = list(rf_coll.GetAll())
        xml_first_comp = next(c for c in xml_before if c["no"] == str(comp_no_val))
        _log(
            f"       .{rf_coll_name}.GetAll() count = {len(rf_items)} "
            f"(XML entry count for composition no={comp_no_val} = {len(xml_first_comp['entries'])})"
        )

        first_entry = rf_items[0]
        _log("\n[1b.6] Entry-level attribute discovery on the FIRST relative-flow entry:")
        entry_no_id, entry_no_val = _first_working_attr(first_entry, _NO_CANDIDATES, "No (entry)")
        veh_type_id, veh_type_val = _first_working_attr(first_entry, _VEH_TYPE_CANDIDATES, "VehType")
        des_speed_id, des_speed_val = _first_working_attr(
            first_entry, _DES_SPEED_DISTR_CANDIDATES, "DesSpeedDistr"
        )
        _log("       -- RelFlow: scalar vs indexed --")
        relflow_scalar_id, relflow_scalar_val = _first_working_attr(
            first_entry, _REL_FLOW_SCALAR_CANDIDATES, "RelFlow (scalar)"
        )
        relflow_indexed_id, relflow_indexed_val = _first_working_attr(
            first_entry, _REL_FLOW_INDEXED_CANDIDATES, "RelFlow(1) (indexed)"
        )
        xml_first_entry = xml_first_comp["entries"][0]
        _log(f"       XML ground truth for this entry: {xml_first_entry}")

        _log("\n[1b.7] CONTROL: bogus attribute id on a real relative-flow entry (fail-open/closed check)...")
        try:
            bogus = first_entry.AttValue(_CONTROL_BOGUS_ATTR)
            _log(f"        [CONTROL] AttValue({_CONTROL_BOGUS_ATTR!r}) -> {bogus!r} (fails OPEN)")
        except Exception as exc:
            _log(f"        [CONTROL] AttValue({_CONTROL_BOGUS_ATTR!r}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        _log("\n[1b.8] Bogus composition no -- ItemByKey error behavior...")
        try:
            bogus_comp = comps_coll.ItemByKey(999999)
            _log(f"        ItemByKey(999999) -> {bogus_comp!r} (UNEXPECTED -- did not raise)")
        except Exception as exc:
            _log(f"        ItemByKey(999999) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        _log("\n[1b.9] Does the relative-flow container expose ItemByKey at all?...")
        has_item_by_key = hasattr(rf_coll, "ItemByKey")
        _log(f"        hasattr(rf_coll, 'ItemByKey') = {has_item_by_key}")
        if has_item_by_key:
            try:
                bogus_entry = rf_coll.ItemByKey(999999)
                _log(f"        ItemByKey(999999) -> {bogus_entry!r} (UNEXPECTED -- did not raise)")
            except Exception as exc:
                _log(f"        ItemByKey(999999) -> {type(exc).__name__}: {exc} (fails CLOSED)")
        else:
            _log(
                "        NO ItemByKey on IVehicleCompositionRelativeFlowContainer (confirmed via the "
                "type-lib dump -- only GetAll/FilteredBy/GetFilteredSet/Add.../Remove.../SetMulti* exist). "
                "Entry lookup must scan GetAll() and match on VehType client-side -- a genuinely NEW "
                "landmine distinct from every other per-object collection in this codebase (conflict "
                "areas/routes/vehicle inputs/signal controllers all support ItemByKey)."
            )

        _log("\n[1b.9b] Entry identity: scanning GetAll() and matching by VehType (client-side, no ItemByKey)...")
        real_key = int(veh_type_val) if veh_type_id else int(xml_first_entry["vehType"])
        match = next((e for e in rf_items if int(e.AttValue(veh_type_id)) == real_key), None)
        _log(f"        GetAll()-scan match for VehType={real_key}: {'FOUND' if match is not None else 'NOT FOUND'}")

        _log("\n[1b.9c] FilteredBy('VehType=<real>') as a possible alternative to ItemByKey...")
        try:
            filtered = rf_coll.FilteredBy(f"VehType={real_key}")
            filtered_items = list(filtered.GetAll()) if filtered is not None else []
            _log(f"        FilteredBy('VehType={real_key}').GetAll() -> {len(filtered_items)} item(s)")
        except Exception as exc:
            _log(f"        FilteredBy('VehType={real_key}') -> {type(exc).__name__}: {exc}")

        _log("\n[1b.9d] FilteredBy on a BOGUS VehType -- does an empty-result filter raise or return empty?...")
        try:
            filtered_bogus = rf_coll.FilteredBy("VehType=999999")
            filtered_bogus_items = list(filtered_bogus.GetAll()) if filtered_bogus is not None else []
            _log(f"        FilteredBy('VehType=999999').GetAll() -> {len(filtered_bogus_items)} item(s) (fails OPEN if 0, no raise)")
        except Exception as exc:
            _log(f"        FilteredBy('VehType=999999') -> {type(exc).__name__}: {exc} (fails CLOSED)")

        if relflow_indexed_id is None and relflow_scalar_id is None:
            raise SystemExit("FAIL: neither scalar nor indexed RelFlow worked -- cannot continue.")
        relflow_id = relflow_scalar_id or relflow_indexed_id

        # -------------------------------------------------------------
        # [1b.10] MULTI-entry composition write: does Vissim renormalize
        # sibling entries, or store an independent raw weight? THE CENTRAL
        # QUESTION.
        # -------------------------------------------------------------
        multi_comp_no = next(c["no"] for c in xml_before if len(c["entries"]) >= 3)
        multi_comp_item = comps_coll.ItemByKey(int(multi_comp_no))
        multi_rf_coll = getattr(multi_comp_item, rf_coll_name)
        multi_rf_items = list(multi_rf_coll.GetAll())
        _log(f"\n[1b.10] MULTI-entry write test -- composition no={multi_comp_no} ({len(multi_rf_items)} entries)")
        before_vals: dict[int, Any] = {}
        for e in multi_rf_items:
            vt = int(e.AttValue(veh_type_id))
            before_vals[vt] = e.AttValue(relflow_id)
        _log(f"        BEFORE -- every entry's RelFlow (by vehType): {before_vals}")

        target_veh_type = min(before_vals)
        target_entry = next(e for e in multi_rf_items if int(e.AttValue(veh_type_id)) == target_veh_type)
        old_val = before_vals[target_veh_type]
        new_val = float(old_val) + 5.0
        _log(f"        SetAttValue({relflow_id!r}, {new_val}) on vehType={target_veh_type} (was {old_val!r})...")
        try:
            target_entry.SetAttValue(relflow_id, new_val)
            _log("        SetAttValue succeeded (no exception).")
        except Exception as exc:
            _log(f"        SetAttValue RAISED: {type(exc).__name__}: {exc}")

        after_vals: dict[int, Any] = {}
        for e in multi_rf_items:
            vt = int(e.AttValue(veh_type_id))
            after_vals[vt] = e.AttValue(relflow_id)
        _log(f"        AFTER -- every entry's RelFlow (by vehType): {after_vals}")
        _log(
            "        Target entry changed to the RAW written value (not pre-normalized): "
            f"{abs(float(after_vals[target_veh_type]) - new_val) < 1e-6}"
        )
        siblings_changed = {
            k: (before_vals[k], after_vals[k])
            for k in before_vals
            if k != target_veh_type and str(before_vals[k]) != str(after_vals[k])
        }
        _log(
            f"        Sibling entries whose RelFlow VALUE changed as a side effect: {siblings_changed} "
            "(empty == raw/independent weight like routes, non-empty == Vissim renormalizes on write)"
        )

        # -------------------------------------------------------------
        # [1b.11] SINGLE-entry composition write (no=4, "Cycle")
        # -------------------------------------------------------------
        single_comp_no = next((c["no"] for c in xml_before if len(c["entries"]) == 1), None)
        if single_comp_no is not None:
            single_comp_item = comps_coll.ItemByKey(int(single_comp_no))
            single_rf_coll = getattr(single_comp_item, rf_coll_name)
            single_rf_items = list(single_rf_coll.GetAll())
            _log(
                f"\n[1b.11] SINGLE-entry write test -- composition no={single_comp_no} "
                f"({len(single_rf_items)} entry)"
            )
            only_entry = single_rf_items[0]
            only_veh_type = int(only_entry.AttValue(veh_type_id))
            old_single_val = only_entry.AttValue(relflow_id)
            new_single_val = float(old_single_val) + 41.0
            _log(f"        BEFORE: vehType={only_veh_type} RelFlow={old_single_val!r}")
            _log(f"        SetAttValue({relflow_id!r}, {new_single_val}) on the ONLY entry...")
            try:
                only_entry.SetAttValue(relflow_id, new_single_val)
                _log("        SetAttValue succeeded (no exception).")
            except Exception as exc:
                _log(f"        SetAttValue RAISED: {type(exc).__name__}: {exc}")
            after_single_val = only_entry.AttValue(relflow_id)
            _log(
                f"        AFTER: vehType={only_veh_type} RelFlow={after_single_val!r} "
                f"(matches written value: {abs(float(after_single_val) - new_single_val) < 1e-6})"
            )
        else:
            _log("\n[1b.11] SKIPPED -- no single-entry composition found on this network.")

        # -------------------------------------------------------------
        # [1b.11b] Invalid-entry CONTROL: since there is no ItemByKey, an
        # "invalid entry" write can only ever be caught by the WRAPPER's own
        # GetAll()-scan finding no match (there is no COM-level lookup call
        # to fail open/closed here at all -- this documents that the
        # fail-closed behavior for a bad vehType, if any, must be
        # IMPLEMENTED by tools_edit.py/vissim_com.py, not inherited from
        # COM). Confirmed: scanning a real composition's relFlows for a
        # vehType that is not present returns no match.
        # -------------------------------------------------------------
        _log("\n[1b.11b] Invalid-entry CONTROL: GetAll()-scan for a vehType NOT present in this composition...")
        no_match = next((e for e in multi_rf_items if int(e.AttValue(veh_type_id)) == 999999), None)
        _log(
            f"        scan for VehType=999999 in composition no={multi_comp_no}: "
            f"{'FOUND (unexpected)' if no_match is not None else 'NOT FOUND -- wrapper must raise itself, COM gives no signal here'}"
        )

        # -------------------------------------------------------------
        # [1b.12] Persist + reparse + fresh COM reload
        # -------------------------------------------------------------
        tmp_path = working.with_name(working.stem + "_saving" + working.suffix)
        _log(f"\n[1b.12] Persisting via save_net_as({tmp_path.name}) + os.replace onto {working.name}...")
        controller.save_net_as(tmp_path)
        os.replace(tmp_path, working)
        _log(f"        Persisted. Working copy size = {working.stat().st_size} bytes")

        xml_after = _xml_compositions(working)
        xml_multi_after = next(c for c in xml_after if c["no"] == multi_comp_no)
        _log(f"[1b.13] Saved XML composition no={multi_comp_no} entries: {xml_multi_after['entries']}")
        if single_comp_no is not None:
            xml_single_after = next(c for c in xml_after if c["no"] == single_comp_no)
            _log(f"        Saved XML composition no={single_comp_no} entries: {xml_single_after['entries']}")

        _log("\n[1b.14] Fresh COM reload of the saved file (separate connection)...")
        controller.disconnect()
        verify_controller = VissimController()
        verify_controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        verify_controller.load_network(str(working))
        reload_comps = getattr(verify_controller.vissim.Net, coll_name)
        reload_multi_comp = reload_comps.ItemByKey(int(multi_comp_no))
        reload_multi_rf = getattr(reload_multi_comp, rf_coll_name)
        reload_target_entry = next(
            e for e in reload_multi_rf.GetAll() if int(e.AttValue(veh_type_id)) == target_veh_type
        )
        reload_relflow = reload_target_entry.AttValue(relflow_id)
        _log(
            f"        Reload composition no={multi_comp_no} vehType={target_veh_type} RelFlow={reload_relflow!r} "
            f"(intended {new_val!r}) -> match: {abs(float(reload_relflow) - new_val) < 1e-6}"
        )

        controller = verify_controller  # so the finally block disconnects this one

        _log("\n" + "#" * 78)
        _log("# SUMMARY")
        _log("#" * 78)
        _log(f"Compositions collection: Net.{coll_name}")
        _log(f"Per-composition relative-flow child collection: .{rf_coll_name}")
        _log(
            f"Entry attribute ids: No(entry)={entry_no_id!r} VehType={veh_type_id!r} "
            f"DesSpeedDistr={des_speed_id!r} RelFlow scalar={relflow_scalar_id!r} "
            f"RelFlow indexed={relflow_indexed_id!r}"
        )
        _log(f"Entry identity is the vehType number, but NO ItemByKey exists -- GetAll()-scan required: see [1b.9] above")
        _log(f"Normalization on write: siblings changed = {bool(siblings_changed)} (see [1b.10])")
        _log(f"Vehicle-input composition references (BEFORE any edit): {vi_refs_before}")

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
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()

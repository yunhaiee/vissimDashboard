"""QUEUE (v2a) unit EMPIRICAL PROBE: vehicle type properties
(``Net.VehicleTypes`` -> its SCALAR attributes + REFERENCE attributes
pointing at 2D/3D model distributions, weight/power distributions, and
accel/decel/curve-speed FUNCTION objects) over COM.

Static XML pre-scan (already run, not repeated here at runtime -- see the
findings pasted into ``services.agent.tools_network`` once this unit ships):
a network-wide scan across all 25 real Vissim 2026 example networks found 168
real ``<vehicleType>`` elements. Every attribute key ever seen on one:
``no, name, category, capacity, capExact, model2D3DDistr, weightDistr,
powerDistr, desAccelFunc, maxAccelFunc, desDecelFunc, maxDecelFunc,
desCurveSpeedFunc, colorDistr1..4, occupDistr, linkCostCoeff, travTmCoeff,
distCoeffPerMeter, alightTm, boardTm, clearTmPT, doorClosDur, dwellTmIsAdd,
anmFlag, equip, extDriver*``. This probe/unit only cares about the task
brief's own scope ("accel/decel curves, lengths, weight/power" + the category
enum) -- the REFERENCE attributes ``model2D3DDistr`` (-> length, via the
referenced distribution's constituent 2D/3D model shapes' own
``model2D3DSegment/@length``), ``weightDistr``/``powerDistr`` (->
weight/power, plain 2-point ``distrDataPts`` distributions -- THE SAME shape
``speedDistribution`` uses, verified statically: e.g. Beijing's
weightDistribution no=1 is ``[(fx=0,x=700), (fx=1,x=1500)]``), and the FOUR
accel/decel/curve-speed FUNCTION references (``desAccelFunc``, ``maxAccelFunc``,
``desDecelFunc``, ``maxDecelFunc``, ``desCurveSpeedFunc`` -- five, not four;
each a distinct top-level XML section: ``desAccelerationFunctions``,
``maxAccelerationFunctions``, ``desDecelerationFunctions``,
``maxDecelerationFunctions``, ``curvSpeedFuncs`` -- note the SPELLING
MISMATCH already visible in the raw XML: the vehicleType attribute is
``desCurveSpeedFunc`` ("Curve") but the referenced section/element is
``curvSpeedFuncs``/``curvSpeedFunc`` ("Curv", no e) -- never assume the COM
attribute id follows either spelling without checking live, per the
CycTm-class precedent).

**Nullable-per-category finding (static, cross-network):** ``weightDistr``/
``powerDistr``/``desCurveSpeedFunc`` are ABSENT from the XML entirely (not
present as an attribute at all) on every ``category="PEDESTRIAN"``
vehicleType across all 25 networks (e.g. Roundabout Billings.US's "Man"/
"Woman" pedestrian types) -- these references are genuinely OPTIONAL per
vehicle category, not always-populated. The parser and the read tool must
tolerate an absent reference (``None``), not assume every vehicle type
carries every field.

**model2D3D length resolution is STATIC XML ONLY, never COM.** ``Net.
Model2D3DDistributions`` (per unit (8)'s "SpeedDistributions is one shared
collection" precedent, a plausible sibling name) -> per-distribution
``Model2D3DDistrEl`` child collection of (``model2D3D`` id, ``share``) pairs
-> ``Net.Models2D3D`` (or whatever this probe's live phase discovers) ->
``Model2D3DSegs`` child collection of segments, each carrying its own
``length`` attribute (verified statically: model2D3D segment counts range
1-95 across the corpus -- most vehicles are a single segment carrying the
WHOLE vehicle's length already, e.g. "Car - Toyota Yaris" length=3.749; a
minority (rail/articulated) have 2-95 segments, most plausibly read as
sequential body sections whose lengths SUM to the whole unit's length -- an
engineering approximation, not COM-verified, flagged below). Because this
chain is 3 levels deep (distribution -> element -> model2D3D -> segments)
and used ONLY for a cheap, read-only length summary (never gates any write or
refusal), :func:`services.agent.tools_network`'s parser resolves it entirely
from the ALREADY-PARSED static XML tree it already holds -- no COM round
trip is needed or attempted for this part, so this probe does NOT exercise
that chain over COM at all; it only confirms the ONE COM attribute
(``Model2D3DDistr``) the write tool needs actually exists and is writable on
a ``VehicleType``, exactly like every other reference field here.

This probe answers, on a live Vissim 2026 instance (attach_to_running=False,
a clone of "Roundabout Billings.US", closed in a ``finally`` block):

[Phase 1a] STATIC type-library scan (no COM instance, no licence consumed) --
same technique every prior v2/v2a-unit probe used: dump every interface whose
name contains "vehicletype" (catches ``IVehicleType``/``IVehicleTypes``/
``IVehicleTypeContainer``).

[Phase 1b] LIVE probe on a Roundabout Billings.US clone (6 vehicle types:
Car/HGV/Bus/Man/Woman/Bike -- Man/Woman are PEDESTRIAN, missing weightDistr/
powerDistr/desCurveSpeedFunc per the static finding above, a useful
nullable-reference test case already present without needing a second
network):
  1. ``Net.VehicleTypes`` ``GetAll()`` count cross-checked against the XML
     element count (6).
  2. Attribute-id discovery on a FULL vehicle type (no=100, "Car"): No, Name,
     Category, Capacity, CapExact, Model2D3DDistr, WeightDistr, PowerDistr,
     DesAccelFunc, MaxAccelFunc, DesDecelFunc, MaxDecelFunc,
     DesCurveSpeedFunc/DesCurvSpeedFunc (both spellings tried) -- read back
     and compared against the XML ground truth.
  3. THE NULLABLE-REFERENCE TEST: same discovery on a PEDESTRIAN vehicle type
     (no=510, "Man") -- what does ``AttValue('WeightDistr')`` return when the
     XML attribute is absent entirely? (expect: a documented "null" sentinel,
     never a crash -- confirmed live, not assumed.)
  4. Target-collection enumerability + ItemByKey error behavior for EVERY
     referenced collection: Model2D3DDistributions, WeightDistributions,
     PowerDistributions, DesAccelerationFunctions, MaxAccelerationFunctions,
     DesDecelerationFunctions, MaxDecelerationFunctions, CurvSpeedFuncs (name
     TBD) -- each tried against a real number (from step 2) AND a bogus one
     (999999), fail-open/closed data point per collection.
  5. CONTROL: bogus attribute id on a REAL VehicleType object (fail-open/
     closed check, the SAME control every prior unit's probe runs).
  6. Bogus VehicleType no -- ``ItemByKey`` error behavior on
     ``Net.VehicleTypes`` itself.
  7. ONE live write per FIELD CLASS (not every field -- the mechanism is
     identical scalar ``SetAttValue`` per field, verified once per class is
     sufficient): WeightDistr (distribution-class reference) and
     DesAccelFunc (function-class reference) on vehicle type no=200 (HGV),
     changed to a DIFFERENT valid target number, read back immediately, AND
     read back every OTHER vehicle type's OWN same field (sibling-untouched
     check).
  8. BOGUS REFERENCE TARGET write: ``SetAttValue('WeightDistr', 999999)`` on
     a real vehicle type -- fail-open/closed (the exact question unit (8)'s
     ``des_speed_distribution_not_found`` finding answered for
     ``DesSpeedDistr``; this probe checks whether the SAME answer holds for a
     vehicle type's OWN distribution/function references, never assumed to
     generalize).
  9. Persist (save_net_as) + grep the saved XML's vehicleType attributes for
     both writes + a fresh COM reload to confirm the accepted write survived
     a full round trip.

Never touches the original Examples Demo folder (00-EXECUTION-GUIDE.md rule
4) -- everything happens on a work-dir clone. Always disconnects every
dedicated Vissim instance in a ``finally`` block.

Usage: python tools/probe_vehicle_types.py [example_dir]
Default example: Roundabout Billings.US (2026 Examples Demo) -- named
explicitly in this unit's own task brief as a network that has worked for
prior units; the real Murdoch model is known to raise on ``load_network``
(a separate, pre-existing, already-flagged landmine -- not fixed here).
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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "vehicle-type-probe"

_CONTROL_BOGUS_ATTR = "ThisAttributeDoesNotExistProbe12345"
_BOGUS_NO = 999999

_TYPES_COLLECTION_CANDIDATES = ["VehicleTypes"]
_NO_CANDIDATES = ["No", "no"]
_NAME_CANDIDATES = ["Name", "name"]
_CATEGORY_CANDIDATES = ["Category", "category"]
_CAPACITY_CANDIDATES = ["Capacity", "capacity"]
_CAP_EXACT_CANDIDATES = ["CapExact", "capExact"]

# {label: (attr_candidates, target_collection_candidates)}
_REFERENCE_FIELDS: dict[str, tuple[list[str], list[str]]] = {
    "model2D3DDistr": (["Model2D3DDistr"], ["Model2D3DDistributions"]),
    "weightDistr": (["WeightDistr"], ["WeightDistributions"]),
    "powerDistr": (["PowerDistr"], ["PowerDistributions"]),
    "desAccelFunc": (["DesAccelFunc"], ["DesAccelerationFunctions"]),
    "maxAccelFunc": (["MaxAccelFunc"], ["MaxAccelerationFunctions"]),
    "desDecelFunc": (["DesDecelFunc"], ["DesDecelerationFunctions"]),
    "maxDecelFunc": (["MaxDecelFunc"], ["MaxDecelerationFunctions"]),
    "desCurveSpeedFunc": (
        ["DesCurveSpeedFunc", "DesCurvSpeedFunc", "CurvSpeedFunc"],
        ["CurvSpeedFuncs", "DesCurvSpeedFuncs", "CurveSpeedFunctions"],
    ),
}


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
            in_target = "vehicletype" in class_name.lower()
            if in_target:
                target_hits += 1
                _log(f"\n      -- {class_name} (DispatchBaseClass) --")
            continue
        if in_target and stripped.startswith("def ") and "(self" in stripped:
            _log(f"        {stripped}")

    if target_hits == 0:
        _log("      NO interface with 'vehicletype' in its name found anywhere in the type library.")
    else:
        _log(f"\n      Found {target_hits} '*vehicletype*' interface(s) in the type library (listed above).")


# ---------------------------------------------------------------------------
# Phase 1b: live probe against a Roundabout Billings.US clone
# ---------------------------------------------------------------------------


def _xml_vehicle_types(inpx_path: Path) -> list[dict[str, Any]]:
    root = ET.parse(inpx_path).getroot()
    return [dict(vt.attrib) for vt in root.findall(".//vehicleType")]


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

    xml_before = _xml_vehicle_types(working)
    _log(f"       XML <vehicleType> count = {len(xml_before)}")
    for vt in xml_before:
        _log(
            f"       no={vt.get('no')} name={vt.get('name')!r} category={vt.get('category')!r} "
            f"weightDistr={vt.get('weightDistr')!r} powerDistr={vt.get('powerDistr')!r} "
            f"model2D3DDistr={vt.get('model2D3DDistr')!r} desAccelFunc={vt.get('desAccelFunc')!r} "
            f"desCurveSpeedFunc={vt.get('desCurveSpeedFunc')!r}"
        )

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

        _log("\n[1b.3] VehicleTypes collection name discovery on Net:")
        coll_name, types_coll = _first_working_collection(
            controller.vissim.Net, _TYPES_COLLECTION_CANDIDATES, "VehicleTypes"
        )
        if types_coll is None:
            raise SystemExit("FAIL: could not find the vehicle types collection on Net.")

        type_items = list(types_coll.GetAll())
        _log(f"       Net.{coll_name}.GetAll() count = {len(type_items)} (XML count = {len(xml_before)})")

        car = next(item for item in type_items if str(item.AttValue("No")) == "100")
        _log("\n[1b.4] Scalar attribute discovery on no=100 (Car, a FULL vehicle type):")
        no_id, _ = _first_working_attr(car, _NO_CANDIDATES, "No")
        name_id, _ = _first_working_attr(car, _NAME_CANDIDATES, "Name")
        category_id, _ = _first_working_attr(car, _CATEGORY_CANDIDATES, "Category")
        capacity_id, _ = _first_working_attr(car, _CAPACITY_CANDIDATES, "Capacity")
        cap_exact_id, _ = _first_working_attr(car, _CAP_EXACT_CANDIDATES, "CapExact")
        xml_car = next(vt for vt in xml_before if vt.get("no") == "100")
        _log(f"       XML ground truth: {xml_car}")

        _log("\n[1b.5] REFERENCE attribute discovery on no=100 (Car):")
        ref_attr_ids: dict[str, str] = {}
        for label, (attr_candidates, _target_candidates) in _REFERENCE_FIELDS.items():
            attr_id, _ = _first_working_attr(car, attr_candidates, label)
            if attr_id:
                ref_attr_ids[label] = attr_id

        # -------------------------------------------------------------
        # [1b.6] THE NULLABLE-REFERENCE TEST: a PEDESTRIAN vehicle type
        # (no=510, "Man") is missing weightDistr/powerDistr/
        # desCurveSpeedFunc in the XML entirely -- what does COM report?
        # -------------------------------------------------------------
        man = next((item for item in type_items if str(item.AttValue("No")) == "510"), None)
        if man is not None:
            _log("\n[1b.6] NULLABLE-REFERENCE TEST on no=510 (Man, PEDESTRIAN -- XML has NO weightDistr attribute at all):")
            for label in ("weightDistr", "powerDistr", "desCurveSpeedFunc"):
                attr_id = ref_attr_ids.get(label)
                if not attr_id:
                    continue
                try:
                    value = man.AttValue(attr_id)
                    _log(f"        AttValue({attr_id!r}) on a PEDESTRIAN type -> {value!r}")
                except Exception as exc:
                    _log(f"        AttValue({attr_id!r}) on a PEDESTRIAN type -> {type(exc).__name__}: {exc}")

        # -------------------------------------------------------------
        # [1b.7] Target-collection enumerability + ItemByKey error
        # behavior for EVERY referenced collection.
        # -------------------------------------------------------------
        _log("\n[1b.7] Target-collection enumerability + ItemByKey error behavior:")
        target_colls: dict[str, Any] = {}
        target_coll_names: dict[str, str] = {}
        for label, (_attr_candidates, target_candidates) in _REFERENCE_FIELDS.items():
            _log(f"\n       -- {label} --")
            coll_name2, coll_obj = _first_working_collection(
                controller.vissim.Net, target_candidates, label
            )
            if coll_obj is None:
                continue
            target_colls[label] = coll_obj
            target_coll_names[label] = coll_name2
            xml_target_no = xml_car.get(label)
            if xml_target_no:
                try:
                    item = coll_obj.ItemByKey(int(xml_target_no))
                    item_name = None
                    try:
                        item_name = item.AttValue("Name")
                    except Exception:
                        pass
                    _log(f"        ItemByKey({xml_target_no}) [REAL] -> ok, Name={item_name!r}")
                except Exception as exc:
                    _log(f"        ItemByKey({xml_target_no}) [REAL] -> {type(exc).__name__}: {exc} (UNEXPECTED)")
            try:
                bogus_item = coll_obj.ItemByKey(_BOGUS_NO)
                _log(f"        ItemByKey({_BOGUS_NO}) [BOGUS] -> {bogus_item!r} (UNEXPECTED -- did not raise, fails OPEN)")
            except Exception as exc:
                _log(f"        ItemByKey({_BOGUS_NO}) [BOGUS] -> {type(exc).__name__}: {exc} (fails CLOSED)")

        # -------------------------------------------------------------
        # [1b.8] CONTROL: bogus attribute id on a REAL VehicleType object.
        # -------------------------------------------------------------
        _log("\n[1b.8] CONTROL: bogus attribute id on a REAL VehicleType object...")
        try:
            bogus = car.AttValue(_CONTROL_BOGUS_ATTR)
            _log(f"        [CONTROL] AttValue({_CONTROL_BOGUS_ATTR!r}) -> {bogus!r} (fails OPEN)")
        except Exception as exc:
            _log(f"        [CONTROL] AttValue({_CONTROL_BOGUS_ATTR!r}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        _log("\n[1b.9] Bogus VehicleType no -- ItemByKey error behavior on Net.VehicleTypes...")
        try:
            bogus_type = types_coll.ItemByKey(_BOGUS_NO)
            _log(f"        ItemByKey({_BOGUS_NO}) -> {bogus_type!r} (UNEXPECTED -- did not raise)")
        except Exception as exc:
            _log(f"        ItemByKey({_BOGUS_NO}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        # -------------------------------------------------------------
        # [1b.10] ONE live write per field CLASS: WeightDistr
        # (distribution-class) and DesAccelFunc (function-class) on
        # no=200 (HGV) -- read back + sibling-untouched check.
        # -------------------------------------------------------------
        hgv = next(item for item in type_items if str(item.AttValue("No")) == "200")
        xml_hgv = next(vt for vt in xml_before if vt.get("no") == "200")
        siblings = [item for item in type_items if str(item.AttValue("No")) != "200"]

        for label, new_value_desc, new_value in (
            ("weightDistr", "a DIFFERENT valid weight distribution (3, HGV's own Bus sibling)", 3),
            ("desAccelFunc", "a DIFFERENT valid accel function (3, Bus's own)", 3),
        ):
            attr_id = ref_attr_ids.get(label)
            if not attr_id:
                _log(f"\n[1b.10] SKIPPED write test for {label} -- no working attribute id found.")
                continue
            old_value = hgv.AttValue(attr_id)
            _log(f"\n[1b.10] WRITE TEST -- {label} (COM id {attr_id!r}) on no=200 (HGV): {old_value!r} -> {new_value} ({new_value_desc})")
            before_siblings = {str(s.AttValue("No")): s.AttValue(attr_id) for s in siblings}
            try:
                hgv.SetAttValue(attr_id, new_value)
                _log("        SetAttValue succeeded (no exception).")
            except Exception as exc:
                _log(f"        SetAttValue RAISED: {type(exc).__name__}: {exc}")
                continue
            readback = hgv.AttValue(attr_id)
            _log(f"        Immediate readback: {readback!r} (matches intent: {str(readback) == str(new_value)})")
            after_siblings = {str(s.AttValue("No")): s.AttValue(attr_id) for s in siblings}
            changed_siblings = {
                k: (before_siblings[k], after_siblings[k])
                for k in before_siblings
                if str(before_siblings[k]) != str(after_siblings[k])
            }
            _log(f"        Sibling vehicle types whose OWN {label} changed as a side effect: {changed_siblings} (empty == independent field, as expected)")
            # restore before moving to the next field / persisting
            hgv.SetAttValue(attr_id, old_value)

        # -------------------------------------------------------------
        # [1b.11] BOGUS REFERENCE TARGET write -- fail open/closed?
        # -------------------------------------------------------------
        weight_attr = ref_attr_ids.get("weightDistr")
        if weight_attr:
            _log(f"\n[1b.11] BOGUS REFERENCE TARGET write test -- SetAttValue({weight_attr!r}, {_BOGUS_NO}) on no=200 (HGV)...")
            try:
                hgv.SetAttValue(weight_attr, _BOGUS_NO)
                readback_bogus = hgv.AttValue(weight_attr)
                _log(f"        SetAttValue({_BOGUS_NO}) succeeded (no exception) -- readback {readback_bogus!r} (fails OPEN if this stuck)")
                # restore regardless of what happened
                hgv.SetAttValue(weight_attr, xml_hgv.get("weightDistr"))
            except Exception as exc:
                _log(f"        SetAttValue({_BOGUS_NO}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        # -------------------------------------------------------------
        # [1b.12] A REAL write to persist for the round-trip check:
        # weightDistr on no=200 (HGV): 2 -> 3.
        # -------------------------------------------------------------
        persisted_new_value = None
        if weight_attr:
            old_hgv_weight = hgv.AttValue(weight_attr)
            persisted_new_value = 3 if str(old_hgv_weight) != "3" else 2
            _log(f"\n[1b.12] PERSIST TEST write -- {weight_attr}: {old_hgv_weight!r} -> {persisted_new_value} on no=200 (HGV)...")
            hgv.SetAttValue(weight_attr, persisted_new_value)
            _log(f"        Immediate readback: {hgv.AttValue(weight_attr)!r}")

        # -------------------------------------------------------------
        # [1b.13] Persist + reparse + fresh COM reload.
        # -------------------------------------------------------------
        tmp_path = working.with_name(working.stem + "_saving" + working.suffix)
        _log(f"\n[1b.13] Persisting via save_net_as({tmp_path.name}) + os.replace onto {working.name}...")
        controller.save_net_as(tmp_path)
        os.replace(tmp_path, working)
        _log(f"        Persisted. Working copy size = {working.stat().st_size} bytes")

        xml_after = _xml_vehicle_types(working)
        xml_hgv_after = next(vt for vt in xml_after if vt.get("no") == "200")
        _log(f"[1b.14] Saved XML no=200 (HGV) weightDistr = {xml_hgv_after.get('weightDistr')!r} (intended {persisted_new_value})")

        _log("\n[1b.15] Fresh COM reload of the saved file (separate connection)...")
        controller.disconnect()
        verify_controller = VissimController()
        verify_controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        verify_controller.load_network(str(working))
        reload_types = getattr(verify_controller.vissim.Net, coll_name)
        reload_hgv = reload_types.ItemByKey(200)
        reload_weight = reload_hgv.AttValue(weight_attr) if weight_attr else None
        _log(f"        Reload no=200 (HGV) {weight_attr} = {reload_weight!r} (intended {persisted_new_value}) -> match: {str(reload_weight) == str(persisted_new_value)}")

        controller = verify_controller  # so the finally block disconnects this one

        _log("\n" + "#" * 78)
        _log("# SUMMARY")
        _log("#" * 78)
        _log(f"VehicleTypes collection: Net.{coll_name}")
        _log(f"Scalar attribute ids: No={no_id!r} Name={name_id!r} Category={category_id!r} Capacity={capacity_id!r} CapExact={cap_exact_id!r}")
        _log(f"Reference attribute ids: {ref_attr_ids}")
        _log(f"Reference target collection names: {target_coll_names}")

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

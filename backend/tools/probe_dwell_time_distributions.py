"""QUEUE (Q3, v2a) unit 5 PROBE-FIRST EMPIRICAL PROBE: dwell-time
distributions -- Vissim's object model for "how long does a vehicle wait"
(dwell time, headway, direction-change duration, ...) over COM.

**STATIC XML PRE-SCAN, already run across all 25 real Vissim 2026 example
networks (a cheap ElementTree scan, no COM instance, no licence consumed --
run BEFORE this probe script per this unit's own task brief: "your first
probe question is which example networks even have PT stops / dwell-time
objects" -- reported here for the record, findings pasted into
``services.agent.tools_network`` once this unit ships):

A GLOBAL ``<timeDistributions><timeDistribution no= name= type= mean=
stdDev=></timeDistributions>`` collection exists in EVERY ONE of the 25
example networks (186 total ``<timeDistribution>`` elements; also present,
unsurprisingly, in the real Murdoch model per 00-EXECUTION-GUIDE.md's own
"CC1 is a reference to a Time Distribution object" landmine). Two ``type``
values found, NORMAL (156, plain ``mean``/``stdDev`` attributes) and
EMPIRICAL (30, an ADDITIONAL ``<durDistrDataPts><durationDistributionData
Point fx="..." x="..."/></durDistrDataPts>`` child -- ``x`` is the duration
value, ``fx`` the cumulative fraction, the SAME (value, cumulative-fraction)
shape unit 3's ``speedDistrDatPts``/``speedDistributionDataPoint`` already
established for desired-speed curves). An EMPIRICAL element's ``mean``/
``stdDev`` attributes are STILL PRESENT (informational summary stats,
apparently NOT the values driving simulated sampling -- exactly the kind of
"looks editable, isn't the real lever" trap this probe's live phase must
settle, never assumed).

**Reference-count static scan (the "shared collection" lesson every prior
QUEUE unit's probe already generalizes -- unit 3's SpeedDistributions,
unit 4's conflict areas):** a ``timeDistribution`` number is referenced from
SEVEN real attribute sites across the corpus, none of them "stop signs"
(disproving the task brief's own example guess -- ``<stopSign>`` carries NO
distribution reference of any kind, verified): ``ptLineStop.dwellTmDistr``
(409 occurrences -- the PT-stop-level dwell assignment), ``partialPTLineStop.
dwellTmDistr`` (4, a rare partial-route variant of the same shape),
``vehClassDwellTimeDistribution.dwellTmDistr`` (327, a PER-VEHICLE-CLASS
override nested inside a ptLineStop), ``ptLine.entryTmDistr`` (22, a PT
line's network-entry wait time -- NOT a dwell time), ``drivingBehavior.
w99cc1Distr`` (81 -- CONFIRMS 00-EXECUTION-GUIDE.md's CC1 claim empirically:
CC1 genuinely IS a reference into this exact collection), ``drivingBehavior.
zipperOvtTmDistr`` (zipper-merge overtake time), and ``parkingLot.
dirChgDurDist``/``defRefsParkingLot.dirChgDurDist`` (a parking direction-
change duration -- present in the schema, zero live references on the
network this probe targets). This collection is genuinely SHARED across
driving-behavior physics (CC1, sub-2-second scale), PT operations (dwell
time, seconds to tens of minutes), and parking (unverified scale) -- editing
one distribution can silently change unrelated subsystems' behavior; the
read tool below surfaces reference counts for exactly this reason.

**Per-network richness scan (deciding the live target, per this unit's own
"scan the examples directory statically first" instruction):**
"Roundabout Billings.US" (every prior QUEUE-3/v2a unit's default probe
target) has ZERO ``<ptStop>``/``<ptLineStop>`` elements at all -- unsuitable
here. "Signalization RBC Boise.US" is the richest real PT network found (47
ptStops, 149 ptLineStops, 16 timeDistributions [15 NORMAL + 1 EMPIRICAL], 11
DISTINCT dwellTmDistr targets, ptLineStop dwell means ranging 0.5s [a driving-
behavior CC1 distribution, no=1] to 1500s [25 minutes, no=1500] -- multiple
roles sharing one object type in ONE real network) -- selected as this
probe's live target over BRT Priority Texas.US (32 ptStops but only 3
distinct dwellTmDistr targets) and LRT Priority Luxembourg.LU (50 ptStops,
only 1 distinct target).

This probe answers, on a live Vissim 2026 instance (attach_to_running=False,
a clone of "Signalization RBC Boise.US", closed in a ``finally`` block):

[Phase 1a] STATIC type-library scan (no COM instance, no licence consumed) --
same technique every prior QUEUE-unit probe used, filtering interface names
for "timedistribution".

[Phase 1b] LIVE probe:
  1. ``Net.TimeDistributions`` ``GetAll()`` count cross-checked against the
     XML element count (16).
  2. Scalar attribute discovery (No, Name, Type, Mean, StdDev) on a NORMAL
     distribution (no=25, a real PT dwell-time target, mean=25s stdDev=5s).
  3. Points CHILD COLLECTION discovery on the ONE EMPIRICAL distribution
     (no=200, referenced by a driving behavior's zipperOvtTmDistr, mean=3
     stdDev=0.5) -- try ``DurDistrDataPts`` (matches the XML child tag
     exactly) and siblings; point-level X/FX attribute discovery, read back
     against XML ground truth ((2,0),(4,1)).
  4. Identity check on the points collection (ItemByKey? expect none, per
     unit 3's SpeedDistrDatPts precedent).
  5. CONTROL: bogus attribute id, distribution object AND point object
     separately (fail-open/closed).
  6. Bogus TimeDistribution no -- ItemByKey error behavior on
     Net.TimeDistributions.
  7. WRITE TEST -- Mean/StdDev on an UNREFERENCED NORMAL distribution
     (no=1200, zero real referrers per the static reference scan) to a new
     value, immediate readback, AND a sibling-untouched check against no=1500
     (a DIFFERENT real distribution) to rule out any collection-wide side
     effect.
  8. SILENT-FAILURE MAP for Mean/StdDev: negative mean, negative stdDev, zero
     stdDev (expect ACCEPTED -- real data has plenty of stdDev=0 rows), an
     absurdly large mean (expect accepted, unbounded -- Vissim rarely bounds
     magnitude, matching unit 3's speed-curve finding).
  9. TYPE-MISMATCH PROBES (the central open question this probe exists to
     settle -- does editing "the wrong half" of a distribution silently lie
     about what the simulation actually samples?):
       a. SetAttValue('Mean', ...) on the EMPIRICAL distribution (no=200) --
          does it succeed? Does Type flip?
       b. Does DurDistrDataPts even exist / return points on a NORMAL
          distribution (no=1200)?
       c. Is Type itself writable (SetAttValue('Type', ...)) on either
          object -- if Vissim allows silently flipping NORMAL<->EMPIRICAL
          via a plain attribute write, that is its own landmine worth
          recording even though this unit's ships verbs never touch Type.
 10. WRITE TEST -- ReplaceAll on no=200's DurDistrDataPts (the flat
     alternating-sequence shape unit 3 already proved is the only one that
     actually takes effect) to a new valid 3-point curve, verified via a
     FRESH GetAll(), then the SAME silent-failure map unit 3's probe ran for
     speed curves (fewer than 2 points, fraction outside [0,1], endpoints !=
     0/1, negative duration, decreasing duration, duplicate point, legitimate
     step function) -- re-run here rather than assumed to generalize, since
     duration and speed are semantically different quantities even though
     the COM mechanism (ReplaceAll, flat X/FX sequence) is presumptively the
     same.
 11. Persist (save_net_as) + fresh COM reload to confirm BOTH the Mean/StdDev
     write (no=1200) and the points write (no=200, restored to a valid final
     state) survive a full round trip.

Never touches the original Examples Demo folder (00-EXECUTION-GUIDE.md rule
4) -- everything happens on a work-dir clone. Always disconnects every
dedicated Vissim instance in a ``finally`` block.

Usage: python tools/probe_dwell_time_distributions.py [example_dir]
Default example: Signalization RBC Boise.US (2026 Examples Demo) -- selected
over the usual "Roundabout Billings.US" default (which has ZERO PT stops)
per this unit's own static-scan-first instruction; mirrors the precedent
every prior unit set when the usual default network lacked the object type
in question (e.g. unit 2's priority-rule probe substituting Roundabout
Buehl.DE).
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
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\BRT Priority Texas.US"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "dwell-time-distribution-probe"

_CONTROL_BOGUS_ATTR = "ThisAttributeDoesNotExistProbe12345"
_BOGUS_NO = 999999

_DISTRIBUTIONS_COLLECTION_CANDIDATES = ["TimeDistributions"]
_POINTS_CHILD_CANDIDATES = ["DurDistrDataPts", "TimeDistrDatPts", "DurationDistrDataPts"]
_NO_CANDIDATES = ["No", "no"]
_NAME_CANDIDATES = ["Name", "name"]
_TYPE_CANDIDATES = ["Type", "type", "DistType"]
_MEAN_CANDIDATES = ["Mean", "mean"]
_STDDEV_CANDIDATES = ["StdDev", "stdDev", "StdDeviation"]
_X_CANDIDATES = ["X", "x", "Duration"]
_FX_CANDIDATES = ["FX", "Fx", "fx", "CumFraction"]

#: A NORMAL distribution with ZERO real referrers on this network (per the
#: static reference scan in this module's own docstring) -- safe scratch
#: target for the destructive Mean/StdDev write tests.
_SCRATCH_NORMAL_NO = "550"
#: A DIFFERENT real distribution, used ONLY as the sibling-untouched check.
_SIBLING_NORMAL_NO = "1003"
#: The one real EMPIRICAL distribution on this network (referenced by a
#: driving behavior's zipperOvtTmDistr) -- also this probe's scratch target
#: for the points ReplaceAll tests (no second EMPIRICAL distribution exists
#: on this network to use as an untouched sibling).
_EMPIRICAL_NO = "200"


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
            in_target = "timedistribution" in class_name.lower()
            if in_target:
                target_hits += 1
                _log(f"\n      -- {class_name} (DispatchBaseClass) --")
            continue
        if in_target and stripped.startswith("def ") and "(self" in stripped:
            _log(f"        {stripped}")

    if target_hits == 0:
        _log("      NO interface with 'timedistribution' in its name found anywhere in the type library.")
    else:
        _log(f"\n      Found {target_hits} '*timedistribution*' interface(s) in the type library (listed above).")


# ---------------------------------------------------------------------------
# Phase 1b: live probe against a Signalization RBC Boise.US clone
# ---------------------------------------------------------------------------


def _xml_time_distributions(inpx_path: Path) -> list[dict[str, Any]]:
    root = ET.parse(inpx_path).getroot()
    result = []
    for d in root.findall(".//timeDistributions/timeDistribution"):
        points = [dict(p.attrib) for p in d.findall("durDistrDataPts/durationDistributionDataPoint")]
        result.append({**dict(d.attrib), "points": points})
    return result


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


def _try_replace_all(points_coll: Any, elements: Any, label: str) -> bool:
    try:
        points_coll.ReplaceAll(elements)
        _log(f"        [WORKS] ReplaceAll({label}) succeeded (no exception).")
        return True
    except Exception as exc:
        _log(f"        [no]    ReplaceAll({label}) -> {type(exc).__name__}: {exc}")
        return False


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    _log("#" * 78)
    _log("# PHASE 1a -- static type-library scan (no COM instance, no licence used)")
    _log("#" * 78)
    probe_typelib()

    _log("\n" + "#" * 78)
    _log("# PHASE 1b -- live probe on a Signalization RBC Boise.US clone")
    _log("#" * 78)

    work_dir = _fresh_work_dir(example_dir.name)
    shutil.copytree(example_dir, work_dir)
    inpx_files = sorted(work_dir.glob("*.inpx"))
    if not inpx_files:
        raise SystemExit(f"No .inpx in {work_dir}")
    working = inpx_files[0]
    _log(f"[1b.0] Working copy: {working}")

    xml_before = _xml_time_distributions(working)
    _log(f"       XML <timeDistribution> count = {len(xml_before)}")
    for d in xml_before:
        pts = [(p.get("x"), p.get("fx")) for p in d["points"]]
        _log(
            f"       no={d['no']:>5} type={d.get('type'):10s} mean={d.get('mean')!r:>7} "
            f"stdDev={d.get('stdDev')!r:>7} name={d.get('name')!r:20s} points={pts}"
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

        _log("\n[1b.3] TimeDistributions collection name discovery on Net:")
        coll_name, distr_coll = _first_working_collection(
            controller.vissim.Net, _DISTRIBUTIONS_COLLECTION_CANDIDATES, "TimeDistributions"
        )
        if distr_coll is None:
            raise SystemExit("FAIL: could not find the time-distributions collection on Net.")

        distr_items = list(distr_coll.GetAll())
        _log(f"       Net.{coll_name}.GetAll() count = {len(distr_items)} (XML count = {len(xml_before)})")

        normal_no = "20"
        normal_distr = next(item for item in distr_items if str(item.AttValue("No")) == normal_no)
        xml_normal = next(d for d in xml_before if d["no"] == normal_no)
        _log(f"\n[1b.4] Scalar attribute discovery on NORMAL distribution no={normal_no}:")
        no_id, _ = _first_working_attr(normal_distr, _NO_CANDIDATES, "No")
        name_id, _ = _first_working_attr(normal_distr, _NAME_CANDIDATES, "Name")
        type_id, _ = _first_working_attr(normal_distr, _TYPE_CANDIDATES, "Type")
        mean_id, _ = _first_working_attr(normal_distr, _MEAN_CANDIDATES, "Mean")
        stddev_id, _ = _first_working_attr(normal_distr, _STDDEV_CANDIDATES, "StdDev")
        _log(f"       XML ground truth: {xml_normal}")

        empirical_distr = next(item for item in distr_items if str(item.AttValue("No")) == _EMPIRICAL_NO)
        xml_empirical = next(d for d in xml_before if d["no"] == _EMPIRICAL_NO)
        _log(f"\n[1b.5] Points CHILD COLLECTION discovery on the EMPIRICAL distribution no={_EMPIRICAL_NO}:")
        points_coll_name, points_coll = _first_working_child_collection(
            empirical_distr, _POINTS_CHILD_CANDIDATES, "DurDistrDataPts"
        )
        if points_coll is None:
            raise SystemExit("FAIL: could not find the points child collection.")

        point_items = list(points_coll.GetAll())
        _log(f"       Point count via COM = {len(point_items)} (XML count = {len(xml_empirical['points'])})")

        x_id = fx_id = None
        if point_items:
            _log("\n[1b.6] Point-level attribute discovery (first point):")
            x_id, _ = _first_working_attr(point_items[0], _X_CANDIDATES, "X (point)")
            fx_id, _ = _first_working_attr(point_items[0], _FX_CANDIDATES, "FX (point)")
            _log("\n[1b.6b] Reading EVERY point via COM, compared against XML ground truth:")
            for idx, (pt, xml_pt) in enumerate(zip(point_items, xml_empirical["points"])):
                com_x = pt.AttValue(x_id) if x_id else None
                com_fx = pt.AttValue(fx_id) if fx_id else None
                _log(f"       point[{idx}]: COM=({com_x!r},{com_fx!r})  XML=({xml_pt.get('x')!r},{xml_pt.get('fx')!r})")

        _log("\n[1b.7] Does the points child collection expose ItemByKey?...")
        has_item_by_key = hasattr(points_coll, "ItemByKey")
        _log(f"        hasattr(points_coll, 'ItemByKey') = {has_item_by_key}")
        if has_item_by_key:
            try:
                bogus_pt = points_coll.ItemByKey(_BOGUS_NO)
                _log(f"        ItemByKey({_BOGUS_NO}) -> {bogus_pt!r} (UNEXPECTED -- did not raise)")
            except Exception as exc:
                _log(f"        ItemByKey({_BOGUS_NO}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        _log("\n[1b.8] CONTROL: bogus attribute id on the DISTRIBUTION object...")
        try:
            bogus = normal_distr.AttValue(_CONTROL_BOGUS_ATTR)
            _log(f"        [CONTROL] distribution.AttValue({_CONTROL_BOGUS_ATTR!r}) -> {bogus!r} (fails OPEN)")
        except Exception as exc:
            _log(f"        [CONTROL] distribution.AttValue({_CONTROL_BOGUS_ATTR!r}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        if point_items:
            _log("\n[1b.8b] CONTROL: bogus attribute id on the POINT object...")
            try:
                bogus_p = point_items[0].AttValue(_CONTROL_BOGUS_ATTR)
                _log(f"        [CONTROL] point.AttValue({_CONTROL_BOGUS_ATTR!r}) -> {bogus_p!r} (fails OPEN)")
            except Exception as exc:
                _log(f"        [CONTROL] point.AttValue({_CONTROL_BOGUS_ATTR!r}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        _log("\n[1b.9] Bogus TimeDistribution no -- ItemByKey error behavior on Net.TimeDistributions...")
        try:
            bogus_distr = distr_coll.ItemByKey(_BOGUS_NO)
            _log(f"        ItemByKey({_BOGUS_NO}) -> {bogus_distr!r} (UNEXPECTED -- did not raise)")
        except Exception as exc:
            _log(f"        ItemByKey({_BOGUS_NO}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        # -------------------------------------------------------------
        # [1b.10] WRITE TEST -- Mean/StdDev on an UNREFERENCED NORMAL
        # distribution (scratch) + sibling-untouched check.
        # -------------------------------------------------------------
        scratch_distr = next(item for item in distr_items if str(item.AttValue("No")) == _SCRATCH_NORMAL_NO)
        sibling_distr = next(item for item in distr_items if str(item.AttValue("No")) == _SIBLING_NORMAL_NO)
        xml_scratch = next(d for d in xml_before if d["no"] == _SCRATCH_NORMAL_NO)
        _log(
            f"\n[1b.10] WRITE TEST -- Mean/StdDev on scratch distribution no={_SCRATCH_NORMAL_NO} "
            f"(XML ground truth: mean={xml_scratch.get('mean')!r} stdDev={xml_scratch.get('stdDev')!r}, "
            "zero real referrers per the static reference scan)"
        )
        if mean_id and stddev_id:
            before_sibling = (sibling_distr.AttValue(mean_id), sibling_distr.AttValue(stddev_id))
            old_mean = scratch_distr.AttValue(mean_id)
            old_stddev = scratch_distr.AttValue(stddev_id)
            new_mean, new_stddev = 42.0, 7.5
            scratch_distr.SetAttValue(mean_id, new_mean)
            scratch_distr.SetAttValue(stddev_id, new_stddev)
            readback = (scratch_distr.AttValue(mean_id), scratch_distr.AttValue(stddev_id))
            after_sibling = (sibling_distr.AttValue(mean_id), sibling_distr.AttValue(stddev_id))
            _log(f"        BEFORE mean={old_mean!r} stdDev={old_stddev!r} -> wrote mean={new_mean} stdDev={new_stddev}")
            _log(f"        Immediate readback: {readback}")
            _log(f"        Sibling no={_SIBLING_NORMAL_NO} mean/stdDev before={before_sibling} after={after_sibling} (should be IDENTICAL)")

            # -------------------------------------------------------------
            # [1b.11] SILENT-FAILURE MAP for Mean/StdDev.
            # -------------------------------------------------------------
            _log("\n[1b.11] SILENT-FAILURE MAP for Mean/StdDev on the scratch distribution:")
            failure_cases = [
                ("negative_mean", mean_id, -5.0),
                ("negative_stddev", stddev_id, -1.0),
                ("zero_stddev", stddev_id, 0.0),
                ("huge_mean", mean_id, 999999.0),
            ]
            for case_name, attr_id, value in failure_cases:
                current = scratch_distr.AttValue(attr_id)
                try:
                    scratch_distr.SetAttValue(attr_id, value)
                    got = scratch_distr.AttValue(attr_id)
                    _log(f"        [{case_name}] SetAttValue({attr_id!r}, {value}) ACCEPTED -> readback {got!r}")
                    scratch_distr.SetAttValue(attr_id, current)  # restore immediately
                except Exception as exc:
                    _log(f"        [{case_name}] SetAttValue({attr_id!r}, {value}) REJECTED: {type(exc).__name__}: {exc}")

            # restore scratch to its original values before persisting
            scratch_distr.SetAttValue(mean_id, old_mean)
            scratch_distr.SetAttValue(stddev_id, old_stddev)
            # re-apply the intended final write for the persistence check
            scratch_distr.SetAttValue(mean_id, new_mean)
            scratch_distr.SetAttValue(stddev_id, new_stddev)

        # -------------------------------------------------------------
        # [1b.12] TYPE-MISMATCH PROBES.
        # -------------------------------------------------------------
        _log("\n[1b.12] TYPE-MISMATCH PROBES:")
        if mean_id:
            _log(f"[1b.12a] SetAttValue({mean_id!r}, 77) on the EMPIRICAL distribution no={_EMPIRICAL_NO}...")
            old_emp_mean = empirical_distr.AttValue(mean_id)
            try:
                empirical_distr.SetAttValue(mean_id, 77.0)
                new_type = empirical_distr.AttValue(type_id) if type_id else None
                _log(f"        ACCEPTED -- readback Mean={empirical_distr.AttValue(mean_id)!r}, Type now={new_type!r}")
                empirical_distr.SetAttValue(mean_id, old_emp_mean)
            except Exception as exc:
                _log(f"        REJECTED: {type(exc).__name__}: {exc}")

        _log(f"[1b.12b] Does DurDistrDataPts exist / return points on the NORMAL distribution no={_SCRATCH_NORMAL_NO}?")
        try:
            scratch_points = list(getattr(scratch_distr, points_coll_name).GetAll())
            _log(f"        ACCESSIBLE -- GetAll() count = {len(scratch_points)} on a NORMAL-type distribution")
        except Exception as exc:
            _log(f"        NOT ACCESSIBLE: {type(exc).__name__}: {exc}")

        if type_id:
            _log(f"[1b.12c] Is Type itself writable? SetAttValue({type_id!r}, ...) on both objects...")
            for label, obj, current_type, attempt in (
                ("EMPIRICAL no=" + _EMPIRICAL_NO, empirical_distr, "EMPIRICAL", "NORMAL"),
                ("NORMAL no=" + _SCRATCH_NORMAL_NO, scratch_distr, "NORMAL", "EMPIRICAL"),
            ):
                try:
                    obj.SetAttValue(type_id, attempt)
                    got = obj.AttValue(type_id)
                    _log(f"        [{label}] SetAttValue({type_id!r}, {attempt!r}) ACCEPTED -> readback {got!r}")
                    obj.SetAttValue(type_id, current_type)  # restore
                except Exception as exc:
                    _log(f"        [{label}] SetAttValue({type_id!r}, {attempt!r}) REJECTED: {type(exc).__name__}: {exc}")

        # -------------------------------------------------------------
        # [1b.13] WRITE TEST + SILENT-FAILURE MAP for EMPIRICAL points
        # (ReplaceAll on no=200's DurDistrDataPts -- the ONLY EMPIRICAL
        # distribution on this network, used as its own scratch target).
        # -------------------------------------------------------------
        if x_id and fx_id:
            _log(f"\n[1b.13] WRITE TEST -- ReplaceAll on no={_EMPIRICAL_NO}'s {points_coll_name} (flat alternating shape)...")

            def _elements(pairs: list[tuple[float, float]]) -> Any:
                return tuple(v for pair in pairs for v in pair)

            target_intent = [(0.0, 0.0), (5.0, 0.5), (10.0, 1.0)]
            ok = _try_replace_all(points_coll, _elements(target_intent), "flat-alternating (0,0,5,.5,10,1)")
            readback = [(p.AttValue(x_id), p.AttValue(fx_id)) for p in points_coll.GetAll()]
            _log(f"        Readback after write: {readback} (matches intent: {ok and readback == target_intent})")

            _log("\n[1b.13b] SILENT-FAILURE MAP for duration points (same invariant set unit 3's speed-curve probe ran):")
            silent_failure_cases: list[tuple[str, list[tuple[float, float]]]] = [
                ("duration_decrease", [(0, 0), (20, 0.5), (10, 1)]),
                ("fx_above_1", [(0, 0), (10, 0.5), (20, 1.5)]),
                ("fx_below_0", [(0, -0.2), (10, 0.5), (20, 1)]),
                ("fx_non_monotonic", [(0, 0), (10, 0.9), (20, 0.5)]),
                ("last_fx_not_1", [(0, 0), (10, 0.5), (20, 0.9)]),
                ("first_fx_not_0", [(0, 0.2), (10, 0.5), (20, 1)]),
                ("single_point", [(10, 1)]),
                ("empty", []),
                ("negative_duration", [(-5, 0), (10, 1)]),
                ("absurd_duration", [(0, 0), (99999, 1)]),
                ("duplicate_point", [(0, 0), (10, 0.5), (10, 0.5), (20, 1)]),
                ("legit_step_function", [(10, 0), (10, 1)]),
            ]
            for case_name, pairs in silent_failure_cases:
                try:
                    points_coll.ReplaceAll(_elements(pairs))
                    rb = [(p.AttValue(x_id), p.AttValue(fx_id)) for p in points_coll.GetAll()]
                    _log(f"        [{case_name}] ACCEPTED (no exception). Intended={pairs} Readback={rb}")
                except Exception as exc:
                    _log(f"        [{case_name}] REJECTED: {type(exc).__name__}: {exc}")

            # Restore no=200 to a valid, deliberately-changed final state
            # before persisting (proves the write survives, doesn't need to
            # match the ORIGINAL XML since this is a discarded clone).
            final_valid = [(0.0, 0.0), (2.0, 0.4), (4.0, 1.0)]
            points_coll.ReplaceAll(_elements(final_valid))
            _log(f"\n        Restored no={_EMPIRICAL_NO} to a valid final curve before persisting: {final_valid}")

        # -------------------------------------------------------------
        # [1b.14] Persist + reparse + fresh COM reload.
        # -------------------------------------------------------------
        tmp_path = working.with_name(working.stem + "_saving" + working.suffix)
        _log(f"\n[1b.14] Persisting via save_net_as({tmp_path.name}) + os.replace onto {working.name}...")
        controller.save_net_as(tmp_path)
        os.replace(tmp_path, working)
        _log(f"        Persisted. Working copy size = {working.stat().st_size} bytes")

        xml_after = _xml_time_distributions(working)
        xml_scratch_after = next(d for d in xml_after if d["no"] == _SCRATCH_NORMAL_NO)
        xml_empirical_after = next(d for d in xml_after if d["no"] == _EMPIRICAL_NO)
        _log(f"[1b.15] Saved XML no={_SCRATCH_NORMAL_NO} mean={xml_scratch_after.get('mean')!r} stdDev={xml_scratch_after.get('stdDev')!r} (intended 42.0/7.5)")
        _log(f"        Saved XML no={_EMPIRICAL_NO} points={[(p.get('x'), p.get('fx')) for p in xml_empirical_after['points']]} (intended {final_valid if (x_id and fx_id) else 'n/a'})")

        _log("\n[1b.16] Fresh COM reload of the saved file (separate connection)...")
        controller.disconnect()
        verify_controller = VissimController()
        verify_controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        verify_controller.load_network(str(working))
        reload_distrs = getattr(verify_controller.vissim.Net, coll_name)
        reload_scratch = reload_distrs.ItemByKey(int(_SCRATCH_NORMAL_NO))
        _log(f"        Reload no={_SCRATCH_NORMAL_NO} mean={reload_scratch.AttValue(mean_id)!r} stdDev={reload_scratch.AttValue(stddev_id)!r}")
        reload_empirical = reload_distrs.ItemByKey(int(_EMPIRICAL_NO))
        reload_points = [(p.AttValue(x_id), p.AttValue(fx_id)) for p in getattr(reload_empirical, points_coll_name).GetAll()]
        _log(f"        Reload no={_EMPIRICAL_NO} points={reload_points}")

        controller = verify_controller  # so the finally block disconnects this one

        _log("\n" + "#" * 78)
        _log("# SUMMARY")
        _log("#" * 78)
        _log(f"TimeDistributions collection: Net.{coll_name}")
        _log(f"Scalar attribute ids: No={no_id!r} Name={name_id!r} Type={type_id!r} Mean={mean_id!r} StdDev={stddev_id!r}")
        _log(f"Points child collection: .{points_coll_name}")
        _log(f"Point attribute ids: X={x_id!r} FX={fx_id!r}")

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

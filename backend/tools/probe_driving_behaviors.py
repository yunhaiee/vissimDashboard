"""QUEUE 3 unit 6 PROBE-FIRST EMPIRICAL PROBE: driving-behavior parameters
via duplicate-then-edit -- the last v2a COM bullet and the hardest unit of
the queue (00-EXECUTION-GUIDE.md SS3.2: "Driving behaviours are global
objects -- duplicate before editing or you change every link").

**STATIC XML PRE-SCAN, already run BEFORE this probe script (cheap
ElementTree scans, no COM instance, no licence consumed) -- findings pasted
here for the record, folded into ``services.agent.tools_network`` once this
unit ships:**

Real Murdoch Drive production model (``runtime/uploads/current/current.inpx``
-- known to RAISE on live COM ``LoadNet``, "The network file could not be
loaded", per every prior QUEUE-3 unit's own finding; static-XML-only here):
15 ``<drivingBehavior>`` elements (no 1-6, 10-13, 19-20, 101-103), EXACTLY 15
``<linkBehaviorType>`` elements, and a clean 1:1 ``linkBehaviorType ->
drivBehavDef`` mapping with ZERO ``<vehClassDrivingBehavior>`` per-class
overrides anywhere in this real model (every ``vehClassDrivBehav`` child is
empty). Of the 15 link-behavior-types, only TWO are actually assigned to any
link: no=101 (293 links -- EVERY named corridor link found: "Murdoch Dr
NB/SB", "South St EB/WB", "Barry Marshal Pde NB/SB", "Robin Warren EB/WB",
"Jennalup St EB/WB" -- i.e. every SLK-relevant link in this model) and no=1
(19 links, unnamed). The other 13 link-behavior-types/driving-behaviors are
defined but orphaned (0 links). Driving behavior #101 ("Urban (motorized) -
flyt updated 2.4 3.0", WIEDEMANN74) is therefore the single most shared
calibration object in the real production model: editing it in place would
silently change car-following physics on the ENTIRE modeled corridor
simultaneously -- the exact live proof point this unit's refuse-when-shared
design exists for. carFollowModType across the 15 real behaviors: 10
WIEDEMANN74 (the SatFlow-calibrated urban set + the two "flyt" sets), 3
WIEDEMANN99 (Cycle-Track/Freeway/Right-side-rule), 1 NOINTERACTION
(Footpath, no W74/W99 params meaningful at all -- a THIRD model value this
unit's model-mismatch reasoning must account for, beyond the existing
codebase's WIEDEMANN74/WIEDEMANN99-only assumption).

Per-network richness scan across all 25 real Vissim 2026 example networks
(reported in this unit's DECISIONS.md-drafted entry, not repeated in full
here): every network's ``<drivingBehaviors>`` collection is non-empty (a
mandatory network object, like time distributions); "Roundabout Billings.US"
(this probe's live target, matching the task brief's own suggested default
and every prior QUEUE-3 unit's precedent) carries 6 real ``<drivingBehavior>``
elements (no 1-4 unused by the earlier probe's own text, no 5-6 real) and 2
``<linkBehaviorType>`` elements (no=1 "Urban (motorized)" -> drivBehavDef=1,
WITH ONE real per-class override ``<vehClassDrivingBehavior drivBehav="6"
vehClass="20"/>`` -- vehicle class 20 gets driving behavior 6 instead of the
link-behavior-type's own default of 1; no=4 "Footpath (no interaction)" ->
drivBehavDef=4, NOINTERACTION model, no CC/W74 params meaningful) -- exactly
the per-vehicle-class-override shape the real Murdoch model happens not to
use, making this network the right live target to prove BOTH assignment
paths (default AND per-class override) actually work over COM.

This probe answers, on a live Vissim 2026 instance (attach_to_running=False,
a clone of "Roundabout Billings.US", closed in a ``finally`` block):

[Phase 1a] STATIC type-library scan (no COM instance, no licence consumed) --
filtering interface names for "drivingbehavior"/"linkbehaviortype", looking
specifically for an ``Add``-shaped method (the duplicate mechanism).

[Phase 1b] LIVE probe:
  1. ``Net.DrivingBehaviors`` GetAll() count cross-checked against XML (6).
  2. Full scalar attribute discovery on driving behavior no=1: every W99
     CC0-CC9 id, every W74 id, and a curated set of lane-change/lateral
     candidate ids -- tried against BOTH a bare guess AND the real XML
     attribute spelling (``w99cc0``, ``w74ax`` etc. capitalized), since prior
     units (CycTm, MinHdwy) proved COM ids do not reliably mirror XML names.
  3. THE DUPLICATE MECHANISM -- does ``Net.DrivingBehaviors`` expose an
     ``Add``-shaped method? Try candidates. If found: duplicate behavior
     no=1, inspect what number the copy gets, and diff EVERY discovered
     scalar attribute (plus w99cc1Distr/zipperOvtTmDistr) between source and
     copy to prove whether Add copies attributes or starts from library
     defaults -- the copy-fidelity question this unit's brief flags as
     central and unresolved.
  4. Identity/control checks: bogus attribute id (fail-open/closed), bogus
     DrivingBehaviors ItemByKey (fail-open/closed).
  5. THE ASSIGNMENT SURFACE -- ``Net.LinkBehaviorTypes``: scalar discovery
     (No/Name/DrivBehavDef), read behavior-type no=1's current DrivBehavDef,
     write it to the just-duplicated copy's number, readback, restore. Then
     the per-class override child collection: discovery
     (VehClassDrivBehav/vehClassDrivingBehavior candidates), read the real
     no=1/vehClass=20/drivBehav=6 override, and (if a write mechanism
     exists) probe whether it takes a ReplaceAll (unit 3/5 precedent) or a
     scalar SetAttValue per entry.
  6. SILENT-FAILURE MAP for the candidate whitelist scalars: negative CC0,
     zero CC2, a huge CC7, negative W74 Ax/BxAdd/BxMult, a huge lane-change
     value -- what does Vissim reject vs silently accept.
  7. Persist (save_net_as) + fresh COM reload to confirm the duplicate
     object, its edited scalar, and the link-behavior-type reassignment all
     survive a full round trip.

Never touches the original Examples Demo folder (00-EXECUTION-GUIDE.md rule
4) -- everything happens on a work-dir clone. Always disconnects every
dedicated Vissim instance in a ``finally`` block.

Usage: python tools/probe_driving_behaviors.py [example_dir]
Default example: Roundabout Billings.US (2026 Examples Demo) -- matches the
task brief's own default and every prior QUEUE-3 unit's live-COM precedent;
it is also, per the static scan above, the one real example network proven
to carry a genuine per-vehicle-class driving-behavior override, needed to
exercise the assignment verb's second (non-default) write path.
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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "driving-behavior-probe"

_CONTROL_BOGUS_ATTR = "ThisAttributeDoesNotExistProbe12345"
_BOGUS_NO = 999999

_BEHAVIOR_COLLECTION_CANDIDATES = ["DrivingBehaviors"]
_LINK_BEHAV_TYPE_COLLECTION_CANDIDATES = ["LinkBehaviorTypes", "LinkBehavTypes"]
_VEH_CLASS_OVERRIDE_CHILD_CANDIDATES = ["VehClassDrivBehav", "VehClassDrivingBehaviors"]

#: Candidate COM attribute ids for every W99 CC + W74 param, tried in this
#: order (first that resolves on a live AttValue() call wins) -- the real
#: XML attribute spelling first (matches this codebase's own "prefer the
#: XML-matching id" precedent, verified repeatedly to be right more often
#: than not), then a couple of plausible COM-only variants.
_SCALAR_PARAM_CANDIDATES: dict[str, list[str]] = {
    "cc0": ["W99cc0", "W99CC0", "w99cc0"],
    "cc1": ["W99cc1", "W99CC1"],  # expected to be a DISTRIBUTION REFERENCE, not a plain scalar -- see w99cc1_distr below
    "cc2": ["W99cc2", "W99CC2", "w99cc2"],
    "cc3": ["W99cc3", "W99CC3", "w99cc3"],
    "cc4": ["W99cc4", "W99CC4", "w99cc4"],
    "cc5": ["W99cc5", "W99CC5", "w99cc5"],
    "cc6": ["W99cc6", "W99CC6", "w99cc6"],
    "cc7": ["W99cc7", "W99CC7", "w99cc7"],
    "cc8": ["W99cc8", "W99CC8", "w99cc8"],
    "cc9": ["W99cc9", "W99CC9", "w99cc9"],
    "w99cc1_distr": ["W99cc1Distr", "W99CC1Distr", "w99cc1Distr"],
    "ax": ["W74ax", "W74Ax", "w74ax"],
    "bx_add": ["W74bxAdd", "W74BxAdd", "w74bxAdd"],
    "bx_mult": ["W74bxMult", "W74BxMult", "w74bxMult"],
    "car_follow_mod_type": ["CarFollowModType", "carFollowModType"],
    # Lane-change / lateral candidates (Vissim's "Lane Change" driving-behavior
    # tab + general lateral params) -- names guessed from the XML dump this
    # probe's own docstring already captured (safDistFactLnChg, coopLnChg,
    # coopLnChgSpeedDiff, lnChgDist is a LINK attribute not a behavior one,
    # latDistDrivDef/latDistStandDef, minSpeedForLat).
    "saf_dist_fact_ln_chg": ["SafDistFactLnChg", "safDistFactLnChg"],
    "coop_ln_chg": ["CoopLnChg", "coopLnChg"],
    "coop_ln_chg_speed_diff": ["CoopLnChgSpeedDiff", "coopLnChgSpeedDiff"],
    "lat_dist_driv_def": ["LatDistDrivDef", "latDistDrivDef"],
    "lat_dist_stand_def": ["LatDistStandDef", "latDistStandDef"],
    "min_speed_for_lat": ["MinSpeedForLat", "minSpeedForLat"],
    "max_decel_own": ["MaxDecelOwn", "maxDecelOwn"],
    "diffus_tm": ["DiffusTm", "diffusTm"],
    "no": ["No", "no"],
    "name": ["Name", "name"],
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
            in_target = "drivingbehav" in class_name.lower() or "linkbehav" in class_name.lower()
            if in_target:
                target_hits += 1
                _log(f"\n      -- {class_name} (DispatchBaseClass) --")
            continue
        if in_target and stripped.startswith("def ") and "(self" in stripped:
            _log(f"        {stripped}")

    if target_hits == 0:
        _log("      NO interface with 'drivingbehav'/'linkbehav' in its name found in the type library.")
    else:
        _log(f"\n      Found {target_hits} matching interface(s) in the type library (listed above).")


# ---------------------------------------------------------------------------
# Phase 1b: live probe against a Roundabout Billings.US clone
# ---------------------------------------------------------------------------


def _xml_driving_behaviors(inpx_path: Path) -> list[dict[str, Any]]:
    root = ET.parse(inpx_path).getroot()
    return [dict(d.attrib) for d in root.findall(".//drivingBehaviors/drivingBehavior")]


def _xml_link_behavior_types(inpx_path: Path) -> list[dict[str, Any]]:
    root = ET.parse(inpx_path).getroot()
    result = []
    for lbt in root.findall(".//linkBehaviorTypes/linkBehaviorType"):
        overrides = [dict(c.attrib) for c in lbt.findall(".//vehClassDrivingBehavior")]
        result.append({**dict(lbt.attrib), "overrides": overrides})
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


def _discover_scalars(item: Any) -> dict[str, tuple[str, Any]]:
    """{canonical_name: (working_com_id, value)} for every candidate in
    _SCALAR_PARAM_CANDIDATES that resolves on ``item``."""
    found: dict[str, tuple[str, Any]] = {}
    for canonical, candidates in _SCALAR_PARAM_CANDIDATES.items():
        for attr_id in candidates:
            try:
                value = item.AttValue(attr_id)
                found[canonical] = (attr_id, value)
                break
            except Exception:
                continue
    return found


def _probe_add_methods(collection: Any) -> list[str]:
    """Every callable attribute on ``collection`` whose name suggests an
    Add-shaped creation method (never actually calls anything destructive --
    just enumerates candidates via dir())."""
    candidates = []
    for name in dir(collection):
        if name.lower().startswith("add"):
            candidates.append(name)
    return candidates


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

    xml_behaviors_before = _xml_driving_behaviors(working)
    _log(f"       XML <drivingBehavior> count = {len(xml_behaviors_before)}")
    for d in xml_behaviors_before:
        _log(f"       no={d.get('no'):>3} name={d.get('name')!r:35s} model={d.get('carFollowModType')}")

    xml_lbt_before = _xml_link_behavior_types(working)
    _log(f"       XML <linkBehaviorType> count = {len(xml_lbt_before)}")
    for lbt in xml_lbt_before:
        _log(f"       no={lbt.get('no'):>3} name={lbt.get('name')!r:35s} drivBehavDef={lbt.get('drivBehavDef')} overrides={lbt['overrides']}")

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

        _log("\n[1b.3] DrivingBehaviors collection name discovery on Net:")
        coll_name, behav_coll = _first_working_collection(
            controller.vissim.Net, _BEHAVIOR_COLLECTION_CANDIDATES, "DrivingBehaviors"
        )
        if behav_coll is None:
            raise SystemExit("FAIL: could not find the driving-behaviors collection on Net.")

        behav_items = list(behav_coll.GetAll())
        _log(f"       Net.{coll_name}.GetAll() count = {len(behav_items)} (XML count = {len(xml_behaviors_before)})")

        source_no = "1"
        source = next(item for item in behav_items if str(item.AttValue("No")) == source_no)
        xml_source = next(d for d in xml_behaviors_before if d["no"] == source_no)
        _log(f"\n[1b.4] Full scalar attribute discovery on driving behavior no={source_no} (XML: {xml_source}):")
        source_scalars = _discover_scalars(source)
        for canonical, (com_id, value) in source_scalars.items():
            xml_key = {"w99cc1_distr": "w99cc1Distr", "ax": "w74ax", "bx_add": "w74bxAdd", "bx_mult": "w74bxMult",
                       "car_follow_mod_type": "carFollowModType"}.get(canonical, canonical.replace("_", ""))
            xml_val = xml_source.get(xml_key, xml_source.get(canonical, "?"))
            _log(f"        {canonical:22s} COM id={com_id!r:20s} value={value!r:20} xml={xml_val!r}")
        missing = [c for c in _SCALAR_PARAM_CANDIDATES if c not in source_scalars]
        if missing:
            _log(f"        NOT FOUND on this object: {missing}")

        # -------------------------------------------------------------
        # [1b.5] THE DUPLICATE MECHANISM.
        # -------------------------------------------------------------
        _log(f"\n[1b.5] Add-method candidates on Net.{coll_name} (via dir()):")
        add_candidates = _probe_add_methods(behav_coll)
        _log(f"        {add_candidates}")

        new_no = None
        copy_item = None
        for method_name in add_candidates:
            try:
                method = getattr(behav_coll, method_name)
                _log(f"        Trying {method_name}()  (no args)...")
                result = method()
                new_no = result.AttValue("No")
                copy_item = result
                _log(f"        [WORKS] {method_name}() -> new object No={new_no!r}")
                break
            except Exception as exc:
                _log(f"        [no]    {method_name}() (no args) -> {type(exc).__name__}: {exc}")
                try:
                    _log(f"        Trying {method_name}(<next free no>)...")
                    guess_no = max(int(item.AttValue("No")) for item in behav_items) + 1
                    result = method(guess_no)
                    new_no = result.AttValue("No")
                    copy_item = result
                    _log(f"        [WORKS] {method_name}({guess_no}) -> new object No={new_no!r}")
                    break
                except Exception as exc2:
                    _log(f"        [no]    {method_name}({guess_no}) -> {type(exc2).__name__}: {exc2}")

        if copy_item is not None:
            _log(f"\n[1b.6] COPY-FIDELITY DIFF: source no={source_no} vs new copy no={new_no}:")
            copy_scalars = _discover_scalars(copy_item)
            all_match = True
            for canonical, (com_id, src_val) in source_scalars.items():
                copy_val = copy_scalars.get(canonical, (None, "<MISSING>"))[1]
                same = src_val == copy_val
                all_match = all_match and same
                marker = "SAME" if same else "DIFFERS"
                _log(f"        {canonical:22s} source={src_val!r:20} copy={copy_val!r:20} [{marker}]")
            _log(f"        ALL SCALARS IDENTICAL: {all_match} (False means Add() starts from DEFAULTS, not a copy of source -- attribute-by-attribute copy needed)")

            # ---------------------------------------------------------
            # [1b.7] SILENT-FAILURE MAP on the COPY (never the shared source).
            # ---------------------------------------------------------
            _log(f"\n[1b.7] SILENT-FAILURE MAP on the copy (no={new_no}), never touching the shared source:")
            failure_cases = [
                ("negative_cc0", "cc0", -1.5),
                ("zero_cc2", "cc2", 0.0),
                ("huge_cc7", "cc7", 99999.0),
                ("negative_ax", "ax", -2.0),
                ("negative_bx_add", "bx_add", -2.0),
                ("negative_bx_mult", "bx_mult", -3.0),
                ("huge_saf_dist_fact_ln_chg", "saf_dist_fact_ln_chg", 99999.0),
            ]
            for case_name, canonical, value in failure_cases:
                if canonical not in copy_scalars:
                    _log(f"        [{case_name}] SKIPPED -- {canonical!r} id not discovered")
                    continue
                com_id, current = copy_scalars[canonical]
                try:
                    copy_item.SetAttValue(com_id, value)
                    got = copy_item.AttValue(com_id)
                    _log(f"        [{case_name}] SetAttValue({com_id!r}, {value}) ACCEPTED -> readback {got!r}")
                    copy_item.SetAttValue(com_id, current)
                except Exception as exc:
                    _log(f"        [{case_name}] SetAttValue({com_id!r}, {value}) REJECTED: {type(exc).__name__}: {exc}")

            # Apply the intended final edit (cc2, a genuine calibration
            # scalar) for the persistence check below.
            if "cc2" in copy_scalars:
                cc2_id, old_cc2 = copy_scalars["cc2"]
                copy_item.SetAttValue(cc2_id, 5.0)
                _log(f"\n        Set copy no={new_no}'s cc2 ({cc2_id}) from {old_cc2} to 5.0 for the persistence check.")

        else:
            _log("\n[1b.5b] NO working Add() method found -- duplicate-then-edit may need a different mechanism (full attribute-copy Add(no) or CopyToClipboard/PasteFromClipboard-style API); recorded as a probe finding, not assumed.")

        # -------------------------------------------------------------
        # [1b.8] CONTROL checks.
        # -------------------------------------------------------------
        _log("\n[1b.8] CONTROL: bogus attribute id on a driving-behavior object...")
        try:
            bogus = source.AttValue(_CONTROL_BOGUS_ATTR)
            _log(f"        [CONTROL] AttValue({_CONTROL_BOGUS_ATTR!r}) -> {bogus!r} (fails OPEN)")
        except Exception as exc:
            _log(f"        [CONTROL] AttValue({_CONTROL_BOGUS_ATTR!r}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        _log("\n[1b.9] Bogus DrivingBehaviors no -- ItemByKey error behavior...")
        try:
            bogus_behav = behav_coll.ItemByKey(_BOGUS_NO)
            _log(f"        ItemByKey({_BOGUS_NO}) -> {bogus_behav!r} (UNEXPECTED -- did not raise)")
        except Exception as exc:
            _log(f"        ItemByKey({_BOGUS_NO}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        # -------------------------------------------------------------
        # [1b.10] THE ASSIGNMENT SURFACE -- Net.LinkBehaviorTypes.
        # -------------------------------------------------------------
        _log(f"\n[1b.10] LinkBehaviorTypes collection name discovery on Net:")
        lbt_coll_name, lbt_coll = _first_working_collection(
            controller.vissim.Net, _LINK_BEHAV_TYPE_COLLECTION_CANDIDATES, "LinkBehaviorTypes"
        )
        if lbt_coll is not None:
            lbt_items = list(lbt_coll.GetAll())
            _log(f"       Net.{lbt_coll_name}.GetAll() count = {len(lbt_items)} (XML count = {len(xml_lbt_before)})")

            lbt1 = next(item for item in lbt_items if str(item.AttValue("No")) == "1")
            _log("\n[1b.11] Scalar discovery on link-behavior-type no=1 (DrivBehavDef candidates):")
            drivbehavdef_id = None
            for cand in ["DrivBehavDef", "drivBehavDef"]:
                try:
                    val = lbt1.AttValue(cand)
                    drivbehavdef_id = cand
                    _log(f"        [WORKS] AttValue({cand!r}) -> {val!r}")
                    break
                except Exception as exc:
                    _log(f"        [no]    AttValue({cand!r}) -> {type(exc).__name__}: {exc}")

            if drivbehavdef_id and new_no is not None:
                _log(f"\n[1b.12] WRITE TEST -- reassign link-behavior-type no=1's default to the copy (no={new_no}), readback, restore:")
                old_default = lbt1.AttValue(drivbehavdef_id)
                try:
                    lbt1.SetAttValue(drivbehavdef_id, int(new_no))
                    readback = lbt1.AttValue(drivbehavdef_id)
                    _log(f"        SetAttValue({drivbehavdef_id!r}, {new_no}) -> readback {readback!r} (expect {new_no})")
                    lbt1.SetAttValue(drivbehavdef_id, old_default)
                    _log(f"        Restored to {old_default!r}.")
                except Exception as exc:
                    _log(f"        REJECTED: {type(exc).__name__}: {exc}")

                _log(f"\n[1b.12b] CONTROL -- bogus DrivBehavDef target on link-behavior-type no=1:")
                try:
                    lbt1.SetAttValue(drivbehavdef_id, _BOGUS_NO)
                    _log(f"        SetAttValue({drivbehavdef_id!r}, {_BOGUS_NO}) ACCEPTED (UNEXPECTED, fails OPEN)")
                    lbt1.SetAttValue(drivbehavdef_id, old_default)
                except Exception as exc:
                    _log(f"        SetAttValue({drivbehavdef_id!r}, {_BOGUS_NO}) REJECTED: {type(exc).__name__}: {exc} (fails CLOSED)")

            _log("\n[1b.13] Per-vehicle-class override child collection discovery on link-behavior-type no=1:")
            override_coll_name, override_coll = _first_working_child_collection(
                lbt1, _VEH_CLASS_OVERRIDE_CHILD_CANDIDATES, "VehClassDrivBehav"
            )
            if override_coll is not None:
                overrides = list(override_coll.GetAll())
                _log(f"        {len(overrides)} override(s) found (XML says {xml_lbt_before[0]['overrides']}):")
                for o in overrides:
                    for veh_id in ["VehClass", "vehClass"]:
                        try:
                            veh = o.AttValue(veh_id)
                            break
                        except Exception:
                            veh = "?"
                    for db_id in ["DrivBehav", "drivBehav"]:
                        try:
                            db = o.AttValue(db_id)
                            break
                        except Exception:
                            db = "?"
                    _log(f"        vehClass={veh!r} drivBehav={db!r}")
                _log(f"\n[1b.14] Does the override child collection have ItemByKey?...")
                _log(f"        hasattr = {hasattr(override_coll, 'ItemByKey')}")

        else:
            _log("        NO working LinkBehaviorTypes collection name found -- the assignment surface is elsewhere.")

        # -------------------------------------------------------------
        # [1b.15] Persist + reparse + fresh COM reload.
        # -------------------------------------------------------------
        tmp_path = working.with_name(working.stem + "_saving" + working.suffix)
        _log(f"\n[1b.15] Persisting via save_net_as({tmp_path.name}) + os.replace onto {working.name}...")
        controller.save_net_as(tmp_path)
        os.replace(tmp_path, working)
        _log(f"        Persisted. Working copy size = {working.stat().st_size} bytes")

        xml_behaviors_after = _xml_driving_behaviors(working)
        _log(f"[1b.16] Saved XML <drivingBehavior> count = {len(xml_behaviors_after)} (was {len(xml_behaviors_before)}; expect +1 if Add() worked)")
        if new_no is not None:
            xml_copy_after = next((d for d in xml_behaviors_after if d["no"] == str(new_no)), None)
            _log(f"        Saved XML copy no={new_no}: {xml_copy_after}")

        _log("\n[1b.17] Fresh COM reload of the saved file (separate connection)...")
        controller.disconnect()
        verify_controller = VissimController()
        verify_controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        verify_controller.load_network(str(working))
        reload_behaviors = getattr(verify_controller.vissim.Net, coll_name)
        reload_count = len(list(reload_behaviors.GetAll()))
        _log(f"        Reload DrivingBehaviors count = {reload_count}")
        if new_no is not None:
            reload_copy = reload_behaviors.ItemByKey(int(new_no))
            if "cc2" in source_scalars:
                cc2_com_id = source_scalars["cc2"][0]
                _log(f"        Reload copy no={new_no} cc2 = {reload_copy.AttValue(cc2_com_id)!r} (expect 5.0)")

        controller = verify_controller  # so the finally block disconnects this one

        _log("\n" + "#" * 78)
        _log("# SUMMARY")
        _log("#" * 78)
        _log(f"DrivingBehaviors collection: Net.{coll_name}")
        _log(f"Discovered scalar COM ids: {[(k, v[0]) for k, v in source_scalars.items()]}")
        _log(f"Add() candidates tried: {add_candidates}; working: {new_no is not None}")
        _log(f"LinkBehaviorTypes collection: Net.{lbt_coll_name if lbt_coll is not None else '<none found>'}")

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

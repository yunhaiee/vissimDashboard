"""Q4 unit (7) EMPIRICAL PROBE: PT stop + PT line placement (genesis) --
the natural home of the BusDwell finding (Q3 unit 5, docs/phase2/
DECISIONS.md 2026-07-31: Murdoch has 5 staged-but-unreferenced BusDwell_0-4
empirical distributions; 16 ptStops / 12 ptLines / 19 ptLineStops; every
LIVE dwell assignment shares ONE 10s+-2s NORMAL distribution). This probe
answers whether add_pt_stop/remove_pt_stop, add_pt_line, and
set_pt_line_stop_dwell(line, stop, time_distribution_no) are shippable, and
on what real COM surface.

**STATIC XML PRE-SCAN (already run, no COM instance, no licence -- a plain
ElementTree scan across all 25 real Vissim 2026 example networks; findings
pasted into services.agent.tools_network once this unit ships):**

Per-network PT richness (ptStop / ptLine / ptLineStop / partialPTLineStop /
vehClassDwellTimeDistribution counts):
  3D - Complex Intersection Karlsruhe.DE   12  19   38  0   6
  Air Taxi Vertiport                        1   1    1  0   0
  Border Crossing Breitenau.DE              4   4    4  0 313
  BRT Priority Texas.US                    32  10   62  0   0   <- proven loadable, PT-rich
  Bus Mall Eugene.US                       20  30   30  0   0
  Cableways                                 5   5    2  4   0
  LRT Priority Luxembourg.LU               50  39  123  0   0
  Railroad Trucks Portland.US               7  23   27  0   0
  Roundabout London.UK                      3   3    6  0   0
  Signalization RBC Boise.US               47  17  149  0   1
  Toll Plaza Camden.US                      0   0    0  0   7
  Urban Expressway Rome.IT                  8   5   14  0   0
(everything else in the corpus: zero PT elements)

BRT Priority Texas.US selected as this probe's live target: real (proven
loadable, per this unit's own task brief), the biggest ptLineStop count
(62) among loadable networks, and a genuinely two-digit ptLine count (10)
without Boise's own known LoadNet-raises landmine (Q3 unit 5's own probe
had to substitute away from Boise for exactly this reason before this unit
ever started -- reusing that lesson rather than re-discovering it).

**ptStop real shape** (top-level ``<ptStops><ptStop .../></ptStops>``
section; disproves inpx_parser.py's own 2026-07-26 INFERRED shape, which
guessed ``lane``/``pos``/``length`` correctly but never had a real network
to check ``no``/``name``/``anmid`` against):
    <ptStop anmid="" lane="7140323 1" length="15.24"
            name="BRT WB Medical - 50' East of Ewing Halsell" no="1"
            pos="41.753"/>
``lane`` is the SAME space-separated "<link> <lane>" token every other
lane-referencing element in this codebase's registry already uses
(LANE_TOKEN_REFERENCE_SITES) -- ``length`` is the stop's own platform
length in metres (display-unit-scaled, per this codebase's now-6th
confirmation of the landmine).

**ptLine real shape** -- NOT a flat object; a route through the network
plus a timetable plus its own stop-dwell assignments, matching the task
brief's own worry:
    <ptLine no="52201" name="NB" vehType="300" desSpeedDistr="1"
            entryLink="13270462" destLink="12270123" destPos="189.13"
            entTmOffset="0" slackTmFrac="1" color="ffff8000" ...>
      <depTimes>
        <departureTime dep="840" occup="40" teleCour="0"/>
        ...
      </depTimes>
      <lineStops>
        <ptLineStop ptStop="99569" dwellTmDistr="20" dwellTmDef="DISTRIBUTION"
                    active="true" alightL="true" alightR="true" boardL="true"
                    boardR="true" alightPerc="0" chgDrivDir="false"
                    depOffset="0" doorClosDel="3" doorLockDurBefDep="1"
                    lateBoard="true" skipPoss="false" useMinDwellTm="false"/>
        ...
      </lineStops>
      <linkSeq>
        <intObjectRef key="10900"/>
        ...
      </linkSeq>
    </ptLine>
``linkSeq`` is the SAME shape ``vehicleRoutingDecisionsStatic``'s own route
linkSeq already uses (services.inpx_structure.ID_REFERENCE_SITES["links"]'s
"route link sequence" entry, Q4 unit 3) -- a full link-by-link path through
the network, NOT a single start-link + auto-route. A ptLine carrying a real
route this way is why "full PT-line-with-route creation may be deep" in the
task brief's own words -- CONFIRMED by this shape, not merely suspected.
``entryTmDistr`` (a time-distribution reference, Q3 unit 5's own finding)
does exist on SOME real ptLine elements in the corpus (22 occurrences) but
not on either example dumped above -- optional, absent here.

**Q3 unit 5's own docstring has TWO now-confirmed inaccuracies, found while
verifying ITS OWN reference-scan machinery for reuse here (not touched by
this unit -- constraints.py minimal, flagged as a follow-up):**
  1. ``vehClassDwellTimeDistribution`` NEVER appears nested inside a
     ``<ptLineStop>``/``<ptLine>`` anywhere in the 25-network corpus --
     ``tools_network._pt_line_stop_veh_class_override_refs`` searches
     ``pt_line.findall(".//vehClassDwellTimeDistribution")`` which can only
     ever return {} on real data. The real parent, on all 327 occurrences
     found corpus-wide, is ``<stopSign><vehClassDwTmDistr>
     <vehClassDwellTimeDistribution dwellTmDistr="" vehClass=""/>
     </vehClassDwTmDistr></stopSign>`` -- a per-vehicle-class STOP-SIGN dwell
     override (e.g. border-crossing/customs wait time), unrelated to PT at
     all. This directly disproves Q3 unit 5's own "disproves the brief's
     stop-sign guess" claim -- stop signs DO carry a dwell-distribution
     reference, just not the plain ``dwellTmDistr`` attribute form the
     brief's example anticipated.
  2. ``partialPTLineStop`` also never nests inside ``<ptLine>`` -- it lives
     under an entirely separate top-level object,
     ``vehicleRoutingDecisionsPartialPT/vehicleRoutingDecisionPartialPT/
     vehRoutPartPT/vehicleRoutePartialPT/partLineStops/partialPTLineStop``
     (a "partial-route PT decision", a different feature from a ptLine's own
     scheduled stops) -- so ``_pt_line_stop_dwell_refs``'s own
     ``partialPTLineStop`` half is also always {} on real data (its
     ``ptLineStop`` half is fine -- real, correctly nested).
Both are pre-existing bugs in a PRIOR unit's code, not introduced or fixed
here (task brief: "constraints.py minimal"); this unit's OWN reference-
integrity registry entry for pt_stops (added to
services.inpx_structure.ID_REFERENCE_SITES) uses the CORRECTED xpaths so
remove_pt_stop's own refusal is not built on the same bug.

This probe answers, on a live Vissim 2026 instance (attach_to_running=False,
a clone of BRT Priority Texas.US, closed in a ``finally`` block):

[Phase 1a] STATIC type-library scan (no COM instance, no licence consumed) --
container-first (Q4 unit (5)'s own recorded lesson: scan
IPTStopContainer/IPTLineContainer-shaped interfaces, not just the item
interfaces) -- filters for "ptstop", "ptline", "dwelltm", "departuretime".

[Phase 1b] LIVE probe:
  1. Net.PTStops collection existence + count vs XML (32).
  2. Scalar attribute discovery on an existing ptStop (No/Name/Lane/Pos/
     Length) vs XML ground truth.
  3. AddPTStop signature discovery (candidate shapes) -- create one, read
     back every attribute, remove it (RemovePTStop discovery).
  4. Net.PTLines collection existence + count vs XML (10).
  5. Scalar attribute discovery on an existing ptLine (No/Name/VehType/
     DesSpeedDistr/EntryLink/DestLink/DestPos/EntTmOffset/EntryTmDistr).
  6. Child-collection discovery on a ptLine: LineStops (candidates
     LineStops/PTLineStops), DepTimes (candidates DepTimes/
     DepartureTimes), Path/route (candidates Path/LinkSeq/Route).
  7. Scalar attribute discovery on an existing ptLineStop (PtStop/
     DwellTmDistr/DwellTmDef/Active) -- WRITE TEST: SetAttValue on
     DwellTmDistr, immediate readback, sibling-untouched check.
  8. AddPTLine signature discovery on Net.PTLines -- multiple candidate arg
     shapes (mirrors the AddPriorityRule silent-no-op discipline: GetAll()
     diff AND saved-XML diff, never trust "no exception" alone). If it
     fails/no-ops, check for a DuplicatePTLine-shaped alternative (mirrors
     Q4 unit 6's own AddPriorityRule -> DuplicatePriorityRule pivot).
  9. CONTROL: bogus ItemByKey on both new collections (fail-open/closed).
 10. Persist (save_net_as) + reparse to confirm exact serialized shape;
     fresh COM reload (separate connection) to confirm round trip.

Never touches the original Examples Demo folder (00-EXECUTION-GUIDE.md rule
4) -- everything happens on a work-dir clone. Always disconnects every
dedicated Vissim instance in a ``finally`` block.

Usage: python tools/probe_pt_stops_lines.py [inpx_path]
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

DEFAULT_EXAMPLE_DIR = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\BRT Priority Texas.US"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "pt-stops-lines-probe"

_CONTROL_BOGUS_NO = 999999


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
# Phase 1a: static type-library scan (no COM instance required) --
# container-first (Q4 unit (5)'s own recorded lesson).
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
    _log(f"      using: {match.desc}  clsid={match.clsid}")

    major_i, minor_i = _hex_int(match.major), _hex_int(match.minor)
    makepy.GenerateFromTypeLibSpec((match.clsid, match.lcid, major_i, minor_i))
    mod = gencache.EnsureModule(match.clsid, match.lcid, major_i, minor_i)
    src_path = Path(mod.__file__)
    _log(f"      Generated wrapper: {src_path} ({src_path.stat().st_size} bytes)")
    lines = src_path.read_text(encoding="utf-8", errors="replace").splitlines()

    keywords = ("ptstop", "ptline", "dwelltm", "departuretime", "lstop")
    class_name = None
    in_target = False
    target_hits = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("class I") and "(DispatchBaseClass)" in stripped:
            class_name = stripped.split("(")[0].removeprefix("class ").strip()
            in_target = any(kw in class_name.lower() for kw in keywords)
            if in_target:
                target_hits += 1
                _log(f"\n      -- {class_name} (DispatchBaseClass) --")
            continue
        if in_target and stripped.startswith("def ") and "(self" in stripped:
            _log(f"        {stripped}")
        if in_target and (stripped.startswith("_prop_map_get_") or stripped.startswith("_prop_map_put_")):
            _log(f"        {stripped}")

    if target_hits == 0:
        _log(f"      NO interface matching any of {keywords!r} found in the type library.")
    else:
        _log(f"\n      Found {target_hits} matching interface(s) (listed above).")


# ---------------------------------------------------------------------------
# Phase 1b: live probe against a BRT Priority Texas.US clone
# ---------------------------------------------------------------------------


def _xml_pt_stops(inpx_path: Path) -> list[dict[str, Any]]:
    root = ET.parse(inpx_path).getroot()
    return [dict(el.attrib) for el in root.findall(".//ptStops/ptStop")]


def _xml_pt_lines(inpx_path: Path) -> list[dict[str, Any]]:
    root = ET.parse(inpx_path).getroot()
    result = []
    for pl in root.findall(".//ptLines/ptLine"):
        stops = [dict(s.attrib) for s in pl.findall("lineStops/ptLineStop")]
        deps = [dict(d.attrib) for d in pl.findall("depTimes/departureTime")]
        link_seq = [r.get("key") for r in pl.findall("linkSeq/intObjectRef")]
        result.append({**dict(pl.attrib), "stops": stops, "deps": deps, "link_seq": link_seq})
    return result


def _try_add(container: Any, method_name: str, *arg_sets: tuple) -> tuple[Any, tuple | None]:
    method = getattr(container, method_name, None)
    if method is None:
        _log(f"        Container has no {method_name!r} method at all (hasattr fails).")
        return None, None
    for args in arg_sets:
        try:
            result = method(*args)
            _log(f"        [WORKS] {method_name}{args!r} -> {result!r}")
            return result, args
        except Exception as exc:
            _log(f"        [no]    {method_name}{args!r} -> {type(exc).__name__}: {exc}")
    return None, None


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
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE_DIR
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    _log("#" * 78)
    _log("# PHASE 1a -- static type-library scan (no COM instance, no licence used)")
    _log("#" * 78)
    probe_typelib()

    _log("\n" + "#" * 78)
    _log("# PHASE 1b -- live probe on a BRT Priority Texas.US clone")
    _log("#" * 78)

    work_dir = _fresh_work_dir(example_dir.name)
    shutil.copytree(example_dir, work_dir)
    inpx_files = sorted(work_dir.glob("*.inpx"))
    if not inpx_files:
        raise SystemExit(f"No .inpx in {work_dir}")
    working = inpx_files[0]
    _log(f"[1b.0] Working copy: {working}")

    xml_stops_before = _xml_pt_stops(working)
    xml_lines_before = _xml_pt_lines(working)
    _log(f"       XML ptStop count = {len(xml_stops_before)}; ptLine count = {len(xml_lines_before)}")
    for s in xml_stops_before[:3]:
        _log(f"       ptStop {s}")
    for pl in xml_lines_before[:2]:
        _log(
            f"       ptLine no={pl.get('no')} name={pl.get('name')!r} vehType={pl.get('vehType')} "
            f"stops={len(pl['stops'])} deps={len(pl['deps'])} linkSeq_len={len(pl['link_seq'])}"
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
        net = controller.vissim.Net

        _log("\n[1b.3] PTStops collection name discovery on Net:")
        stop_coll_name, stop_coll = _first_working_collection(net, ["PTStops"], "PTStops")
        if stop_coll is None:
            raise SystemExit("FAIL: could not find the PT-stops collection on Net.")
        stop_items = list(stop_coll.GetAll())
        _log(f"       Net.{stop_coll_name}.GetAll() count = {len(stop_items)} (XML count = {len(xml_stops_before)})")

        first_stop_no = xml_stops_before[0]["no"]
        first_stop = next(item for item in stop_items if str(item.AttValue("No")) == first_stop_no)
        xml_first_stop = xml_stops_before[0]
        _log(f"\n[1b.4] Scalar attribute discovery on ptStop no={first_stop_no}:")
        no_id, _ = _first_working_attr(first_stop, ["No", "no"], "No")
        name_id, _ = _first_working_attr(first_stop, ["Name", "name"], "Name")
        lane_id, _ = _first_working_attr(first_stop, ["Lane", "lane"], "Lane")
        pos_id, _ = _first_working_attr(first_stop, ["Pos", "pos"], "Pos")
        length_id, _ = _first_working_attr(first_stop, ["Length", "length"], "Length")
        _log(f"       XML ground truth: {xml_first_stop}")

        unit = controller._net_unit_length()
        _log(f"       Net display length unit = {unit!r}")

        _log("\n[1b.5] AddPTStop signature discovery on Net.PTStops...")
        # try candidate arg shapes: link/lane object needed -- reuse
        # controller's own _lane_by_index helper for the lane COM object.
        new_stop_no = (max(int(s["no"]) for s in xml_stops_before) if xml_stops_before else 0) + 1
        target_link, target_lane_idx = xml_first_stop["lane"].split()
        lane_obj = controller._lane_by_index(int(target_link), int(target_lane_idx))
        pos_com = controller._length_m_to_com(50.0, unit)
        length_com = controller._length_m_to_com(15.0, unit)
        new_stop, used_args = _try_add(
            stop_coll,
            "AddPTStop",
            (new_stop_no, lane_obj, pos_com),
            (new_stop_no, lane_obj, pos_com, length_com),
        )
        if new_stop is not None:
            _log("       Reading back every attribute of the newly-created ptStop:")
            for attr_id in (no_id, name_id, lane_id, pos_id, length_id):
                if attr_id:
                    try:
                        _log(f"         {attr_id} = {new_stop.AttValue(attr_id)!r}")
                    except Exception as exc:
                        _log(f"         {attr_id} -> {type(exc).__name__}: {exc}")
            if name_id:
                new_stop.SetAttValue(name_id, "PROBE STOP")
                _log(f"       After SetAttValue(Name, 'PROBE STOP') -> {new_stop.AttValue(name_id)!r}")
            if length_id:
                _log(f"       Length written via AddPTStop args? readback = {new_stop.AttValue(length_id)!r}")
                _log("\n[1b.5b] WRITE TEST -- SetAttValue(Length, ...) on the new ptStop "
                     "(AddPTStop's own signature has no Length param -- can it be set after?):")
                try:
                    new_stop.SetAttValue(length_id, length_com)
                    _log(f"        [WORKS] SetAttValue(Length, {length_com}) -> readback {new_stop.AttValue(length_id)!r}")
                except Exception as exc:
                    _log(f"        [no]    SetAttValue(Length, {length_com}) -> {type(exc).__name__}: {exc}")

            _log("\n[1b.6] RemovePTStop discovery...")
            remove_method = getattr(stop_coll, "RemovePTStop", None)
            if remove_method is None:
                _log("        Container has no RemovePTStop method at all.")
            else:
                try:
                    remove_method(new_stop)
                    after_count = len(list(stop_coll.GetAll()))
                    _log(f"        [WORKS] RemovePTStop(new_stop) -> GetAll() count now {after_count}")
                except Exception as exc:
                    _log(f"        [no]    RemovePTStop(new_stop) -> {type(exc).__name__}: {exc}")

        _log("\n[1b.7] CONTROL: bogus ItemByKey on PTStops...")
        try:
            bogus = stop_coll.ItemByKey(_CONTROL_BOGUS_NO)
            _log(f"        ItemByKey({_CONTROL_BOGUS_NO}) -> {bogus!r} (UNEXPECTED -- did not raise)")
        except Exception as exc:
            _log(f"        ItemByKey({_CONTROL_BOGUS_NO}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        # -------------------------------------------------------------
        # PTLines
        # -------------------------------------------------------------
        _log("\n[1b.8] PTLines collection name discovery on Net:")
        line_coll_name, line_coll = _first_working_collection(net, ["PTLines"], "PTLines")
        if line_coll is None:
            raise SystemExit("FAIL: could not find the PT-lines collection on Net.")
        line_items = list(line_coll.GetAll())
        _log(f"       Net.{line_coll_name}.GetAll() count = {len(line_items)} (XML count = {len(xml_lines_before)})")

        first_line_no = xml_lines_before[0]["no"]
        first_line = next(item for item in line_items if str(item.AttValue("No")) == first_line_no)
        xml_first_line = xml_lines_before[0]
        _log(f"\n[1b.9] Scalar attribute discovery on ptLine no={first_line_no}:")
        line_no_id, _ = _first_working_attr(first_line, ["No", "no"], "No")
        line_name_id, _ = _first_working_attr(first_line, ["Name", "name"], "Name")
        vehtype_id, _ = _first_working_attr(first_line, ["VehType", "vehType"], "VehType")
        desspeed_id, _ = _first_working_attr(first_line, ["DesSpeedDistr", "desSpeedDistr"], "DesSpeedDistr")
        entrylink_id, _ = _first_working_attr(first_line, ["EntryLink", "entryLink"], "EntryLink")
        destlink_id, _ = _first_working_attr(first_line, ["DestLink", "destLink"], "DestLink")
        destpos_id, _ = _first_working_attr(first_line, ["DestPos", "destPos"], "DestPos")
        entrytmdistr_id, _ = _first_working_attr(first_line, ["EntryTmDistr", "entryTmDistr"], "EntryTmDistr")
        _log(f"       XML ground truth (no stops/deps/linkSeq): "
             f"{ {k: v for k, v in xml_first_line.items() if k not in ('stops', 'deps', 'link_seq')} }")

        _log("\n[1b.10] Child-collection discovery on ptLine (LineStops):")
        stops_coll_name, stops_coll = _first_working_child_collection(
            first_line, ["LineStops", "PTLineStops", "PtLineStops"], "LineStops"
        )
        _log("\n[1b.10b] Child-collection discovery on ptLine (DepTimes):")
        deptimes_coll_name, deptimes_coll = _first_working_child_collection(
            first_line, ["DepTimes", "DepartureTimes"], "DepTimes"
        )
        _log("\n[1b.10c] Child-collection / attribute discovery on ptLine (route/Path/LinkSeq):")
        for candidate in ["Path", "LinkSeq", "Route", "Links"]:
            try:
                obj = getattr(first_line, candidate)
                try:
                    count = len(list(obj.GetAll()))
                    _log(f"        [WORKS coll] .{candidate} -> GetAll() count = {count}")
                except Exception as exc2:
                    _log(f"        [prop, not a GetAll-collection] .{candidate} -> {obj!r} ({type(exc2).__name__}: {exc2})")
            except Exception as exc:
                _log(f"        [no]    .{candidate} -> {type(exc).__name__}: {exc}")

        if stops_coll is not None:
            stop_entries = list(stops_coll.GetAll())
            _log(f"\n[1b.11] Scalar attribute discovery on FIRST ptLineStop of line {first_line_no} "
                 f"(COM count={len(stop_entries)}, XML count={len(xml_first_line['stops'])}):")
            if stop_entries:
                first_entry = stop_entries[0]
                xml_first_entry = xml_first_line["stops"][0] if xml_first_line["stops"] else {}
                ptstop_ref_id, _ = _first_working_attr(first_entry, ["PtStop", "ptStop", "PTStop"], "PtStop")
                dwell_id, _ = _first_working_attr(first_entry, ["DwellTmDistr", "dwellTmDistr"], "DwellTmDistr")
                dwelldef_id, _ = _first_working_attr(first_entry, ["DwellTmDef", "dwellTmDef"], "DwellTmDef")
                active_id, _ = _first_working_attr(first_entry, ["Active", "active"], "Active")
                _log(f"        XML ground truth: {xml_first_entry}")

                _log("\n[1b.12] WRITE TEST -- SetAttValue(DwellTmDistr, ...) on this ptLineStop "
                     "+ sibling-untouched check on a SECOND line's stop:")
                if dwell_id:
                    old_dwell = first_entry.AttValue(dwell_id)
                    # pick a real, different time-distribution no to assign --
                    # any real timeDistribution no from Net.TimeDistributions.
                    distr_nos = [int(d.AttValue("No")) for d in net.TimeDistributions.GetAll()]
                    candidate_new = next((d for d in distr_nos if str(d) != str(old_dwell)), None)
                    _log(f"        old dwellTmDistr={old_dwell!r}; writing {candidate_new!r}")
                    if candidate_new is not None:
                        # sibling: a DIFFERENT ptLineStop on a DIFFERENT line, if any
                        sibling_entry = None
                        sibling_before = None
                        if len(line_items) > 1:
                            other_line = next((l for l in line_items if str(l.AttValue("No")) != first_line_no), None)
                            if other_line is not None:
                                other_stops_name, other_stops_coll = _first_working_child_collection(
                                    other_line, [stops_coll_name], "sibling LineStops"
                                )
                                if other_stops_coll is not None:
                                    other_entries = list(other_stops_coll.GetAll())
                                    if other_entries:
                                        sibling_entry = other_entries[0]
                                        sibling_before = sibling_entry.AttValue(dwell_id)
                        first_entry.SetAttValue(dwell_id, candidate_new)
                        readback = first_entry.AttValue(dwell_id)
                        _log(f"        Immediate readback: {readback!r}")
                        if sibling_entry is not None:
                            sibling_after = sibling_entry.AttValue(dwell_id)
                            _log(f"        Sibling stop dwellTmDistr before={sibling_before!r} after={sibling_after!r} (should be IDENTICAL)")
                        # restore
                        first_entry.SetAttValue(dwell_id, old_dwell)
                        restored = first_entry.AttValue(dwell_id)
                        _log(f"        Restored to {restored!r}")

                        _log("\n[1b.12b] CONTROL: bogus time-distribution no on DwellTmDistr...")
                        try:
                            first_entry.SetAttValue(dwell_id, _CONTROL_BOGUS_NO)
                            got = first_entry.AttValue(dwell_id)
                            _log(f"        [CONTROL] SetAttValue(DwellTmDistr, {_CONTROL_BOGUS_NO}) ACCEPTED -> {got!r} (fails OPEN)")
                            first_entry.SetAttValue(dwell_id, old_dwell)
                        except Exception as exc:
                            _log(f"        [CONTROL] SetAttValue(DwellTmDistr, {_CONTROL_BOGUS_NO}) -> {type(exc).__name__}: {exc} (fails CLOSED)")
                            first_entry.SetAttValue(dwell_id, old_dwell)

        # -------------------------------------------------------------
        # PtStop writability on an EXISTING ptLineStop -- does reassigning
        # WHICH stop a line-stop entry points at work via plain SetAttValue?
        # (bears on whether a duplicated line's fixed-count inherited
        # line-stops could ever be repointed at different real stops.)
        # -------------------------------------------------------------
        if stop_entries and ptstop_ref_id:
            _log(f"\n[1b.12c] WRITE TEST -- SetAttValue({ptstop_ref_id}, ...) on an existing "
                 "ptLineStop (repointing WHICH stop it references):")
            other_stop_no = next((s["no"] for s in xml_stops_before if s["no"] != xml_first_entry.get("ptStop")), None)
            if other_stop_no is not None:
                old_ptstop = first_entry.AttValue(ptstop_ref_id)
                try:
                    other_stop_com = stop_coll.ItemByKey(int(other_stop_no))
                    first_entry.SetAttValue(ptstop_ref_id, other_stop_com)
                    _log(f"        [WORKS via COM object] -> readback {first_entry.AttValue(ptstop_ref_id)!r}")
                    first_entry.SetAttValue(ptstop_ref_id, stop_coll.ItemByKey(int(old_ptstop)))
                except Exception as exc:
                    _log(f"        [no, COM object form]    -> {type(exc).__name__}: {exc}")
                    try:
                        first_entry.SetAttValue(ptstop_ref_id, int(other_stop_no))
                        _log(f"        [WORKS via plain int] -> readback {first_entry.AttValue(ptstop_ref_id)!r}")
                        first_entry.SetAttValue(ptstop_ref_id, int(old_ptstop))
                    except Exception as exc2:
                        _log(f"        [no, plain-int form]    -> {type(exc2).__name__}: {exc2}")

        # -------------------------------------------------------------
        # AddPTLine discovery (the central open scope question) -- CORRECT
        # signature per the [1a] type-lib scan: AddPTLine(Key, EntryLink,
        # DestLink, DestPos) -- NOT (Key, VehType, EntryLink) as first
        # guessed; VehType is not a creation arg at all.
        # -------------------------------------------------------------
        _log("\n[1b.13] AddPTLine signature discovery on Net.PTLines (corrected: "
             "Key, EntryLink, DestLink, DestPos per the [1a] type-lib scan)...")
        before_line_nos = {int(l.AttValue("No")) for l in line_coll.GetAll()}
        new_line_no = (max(before_line_nos) if before_line_nos else 0) + 1
        entry_link_obj = dest_link_obj = None
        try:
            entry_link_obj = net.Links.ItemByKey(int(xml_first_line.get("entryLink")))
            dest_link_obj = net.Links.ItemByKey(int(xml_first_line.get("destLink")))
        except Exception as exc:
            _log(f"        Could not resolve entry/dest Link COM objects for AddPTLine args: {exc}")

        new_line = None
        if entry_link_obj is not None and dest_link_obj is not None:
            dest_pos_com = controller._length_m_to_com(float(xml_first_line["destPos"]), unit)
            new_line, used_args = _try_add(
                line_coll, "AddPTLine", (new_line_no, entry_link_obj, dest_link_obj, dest_pos_com)
            )
        after_line_nos = {int(l.AttValue("No")) for l in line_coll.GetAll()}
        _log(f"        GetAll() diff: before={len(before_line_nos)} after={len(after_line_nos)} "
             f"added={after_line_nos - before_line_nos} (empty-set added means SILENT NO-OP "
             "even if no exception was raised, per the AddPriorityRule precedent)")

        if new_line is not None:
            _log("\n[1b.13b] Reading back the freshly-created (empty) ptLine's own attributes:")
            for attr_id, label in ((line_no_id, "No"), (line_name_id, "Name"), (vehtype_id, "VehType")):
                if attr_id:
                    try:
                        _log(f"        {label} = {new_line.AttValue(attr_id)!r}")
                    except Exception as exc:
                        _log(f"        {label} -> {type(exc).__name__}: {exc}")

            _log("\n[1b.13c] WRITE TEST -- SetAttValue(VehType, ...) on the new (empty) ptLine:")
            if vehtype_id:
                try:
                    veh_type_obj = net.VehicleTypes.ItemByKey(int(xml_first_line.get("vehType")))
                    new_line.SetAttValue(vehtype_id, veh_type_obj)
                    _log(f"        [WORKS via COM object] -> readback {new_line.AttValue(vehtype_id)!r}")
                except Exception as exc:
                    _log(f"        [no, COM object form]    -> {type(exc).__name__}: {exc}")
                    try:
                        new_line.SetAttValue(vehtype_id, int(xml_first_line.get("vehType")))
                        _log(f"        [WORKS via plain int] -> readback {new_line.AttValue(vehtype_id)!r}")
                    except Exception as exc2:
                        _log(f"        [no, plain-int form]    -> {type(exc2).__name__}: {exc2}")

            _log("\n[1b.13d] New (empty) ptLine's own LineStops count (expect 0 -- no create "
                 "surface exists on IPTLineStopContainer per the [1a] scan):")
            try:
                new_line_stops = list(getattr(new_line, stops_coll_name).GetAll())
                _log(f"        LineStops count = {len(new_line_stops)}")
                for entry in new_line_stops:
                    for attr_id, label in ((ptstop_ref_id, "PtStop"), (dwell_id, "DwellTmDistr"), (active_id, "Active")):
                        if attr_id:
                            try:
                                _log(f"        auto line-stop {label} = {entry.AttValue(attr_id)!r}")
                            except Exception as exc:
                                _log(f"        auto line-stop {label} -> {type(exc).__name__}: {exc}")
            except Exception as exc:
                _log(f"        -> {type(exc).__name__}: {exc}")

            _log("\n[1b.13d2] New ptLine's own auto-computed LinkSeq (does AddPTLine give a "
                 "real DEFAULT route from EntryLink to DestLink automatically?):")
            try:
                new_line_seq = list(getattr(new_line, "LinkSeq").GetAll())
                _log(f"        LinkSeq count = {len(new_line_seq)}")
                for el in new_line_seq[:25]:
                    try:
                        _log(f"          key={el.AttValue('Key')!r}")
                    except Exception:
                        pass
            except Exception as exc:
                _log(f"        -> {type(exc).__name__}: {exc}")

            _log("\n[1b.13e] UpdateLinkSequence signature discovery on the new ptLine (IPTLine's "
                 "own method, per the [1a] scan) -- try the template's own linkSeq resolved to "
                 "real Link/Connector COM objects:")
            try:
                route_link_objs = [net.Links.ItemByKey(int(k)) for k in xml_first_line["link_seq"]]
                new_line.UpdateLinkSequence(route_link_objs)
                _log(f"        [WORKS, plain list of Link objects] UpdateLinkSequence({len(route_link_objs)} links)")
                new_seq_coll_name, new_seq_coll = _first_working_child_collection(
                    new_line, ["LinkSeq"], "new line's LinkSeq"
                )
            except Exception as exc:
                _log(f"        [no, plain list]    -> {type(exc).__name__}: {exc}")
                try:
                    new_line.UpdateLinkSequence(tuple(route_link_objs))
                    _log(f"        [WORKS, tuple of Link objects] UpdateLinkSequence({len(route_link_objs)} links)")
                except Exception as exc2:
                    _log(f"        [no, tuple form]    -> {type(exc2).__name__}: {exc2}")

            _log("\n[1b.13f] AddDepartureTime signature discovery on the new ptLine's DepTimes "
                 "child collection:")
            try:
                new_line_deptimes_coll = getattr(new_line, deptimes_coll_name)
                new_dep, dep_args = _try_add(
                    new_line_deptimes_coll, "AddDepartureTime", (1, 0.0), (1, 0)
                )
                if new_dep is not None:
                    dep_attr_id, _ = _first_working_attr(new_dep, ["Dep", "dep"], "Dep")
                    occup_attr_id, _ = _first_working_attr(new_dep, ["Occup", "occup"], "Occup")
            except Exception as exc:
                _log(f"        -> {type(exc).__name__}: {exc}")

            _log("\n[1b.13g] Removing the probe's own newly-created ptLine (RemovePTLine) so the "
                 "persisted XML below reflects only genuinely-intended state...")
            try:
                line_coll.RemovePTLine(new_line)
                _log(f"        [WORKS] RemovePTLine -> GetAll() count now {len(list(line_coll.GetAll()))}")
            except Exception as exc:
                _log(f"        [no]    RemovePTLine -> {type(exc).__name__}: {exc}")

        _log("\n[1b.14] DuplicatePTLine discovery (in case AddPTLine no-ops, mirroring the "
             "AddPriorityRule -> DuplicatePriorityRule pivot)...")
        dup_method = getattr(line_coll, "DuplicatePTLine", None)
        if dup_method is None:
            _log("        Container has no DuplicatePTLine method at all.")
        else:
            before_dup_nos = {int(l.AttValue("No")) for l in line_coll.GetAll()}
            dup_new_no = (max(before_dup_nos) if before_dup_nos else 0) + 1
            try:
                dup_method(first_line, dup_new_no)
                after_dup_nos = {int(l.AttValue("No")) for l in line_coll.GetAll()}
                _log(f"        [WORKS] DuplicatePTLine(first_line, {dup_new_no}) -> "
                     f"added={after_dup_nos - before_dup_nos}")
                dup_line = line_coll.ItemByKey(dup_new_no)
                dup_stops = list(getattr(dup_line, stops_coll_name).GetAll())
                _log(f"        Duplicated line's own LineStops count = {len(dup_stops)} "
                     f"(template had {len(stop_entries) if stop_entries else '?'})")
                _log("        Removing the probe's own duplicated line...")
                line_coll.RemovePTLine(dup_line)
                _log(f"        GetAll() count now {len(list(line_coll.GetAll()))}")
            except Exception as exc:
                _log(f"        [no]    DuplicatePTLine(first_line, {dup_new_no}) -> {type(exc).__name__}: {exc}")

        _log("\n[1b.15] CONTROL: bogus ItemByKey on PTLines...")
        try:
            bogus_line = line_coll.ItemByKey(_CONTROL_BOGUS_NO)
            _log(f"        ItemByKey({_CONTROL_BOGUS_NO}) -> {bogus_line!r} (UNEXPECTED -- did not raise)")
        except Exception as exc:
            _log(f"        ItemByKey({_CONTROL_BOGUS_NO}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        # -------------------------------------------------------------
        # Reference-integrity test (destructive, deliberately LAST before
        # persist -- this working copy is a throwaway clone under runtime/,
        # discarded on the probe's next run): does Vissim's OWN
        # RemovePTStop refuse when a REAL ptLineStop still references the
        # stop, or does it silently dangle (the RemoveDataCollectionPoint
        # precedent, Q4 unit (2))?
        # -------------------------------------------------------------
        _log("\n[1b.15b] REFERENCE-INTEGRITY TEST -- RemovePTStop on ptStop no=99569, which "
             "line 52004's own ptLineStop still references as its PtStop...")
        try:
            referenced_stop = stop_coll.ItemByKey(99569)
            stop_coll.RemovePTStop(referenced_stop)
            _log("        [ACCEPTED, no exception] -- checking whether line 52004's ptLineStop "
                 "now dangles...")
            line_52004 = line_coll.ItemByKey(52004)
            dangling_check = list(getattr(line_52004, stops_coll_name).GetAll())
            for entry in dangling_check:
                try:
                    _log(f"        line 52004 ptLineStop PtStop now reads: {entry.AttValue(ptstop_ref_id)!r}")
                except Exception as exc:
                    _log(f"        line 52004 ptLineStop PtStop read -> {type(exc).__name__}: {exc} "
                         "(a raise HERE would itself be the dangling-reference symptom)")
        except Exception as exc:
            _log(f"        [REFUSED by COM itself] RemovePTStop(99569) -> {type(exc).__name__}: {exc}")

        # -------------------------------------------------------------
        # Persist + reparse + fresh COM reload.
        # -------------------------------------------------------------
        tmp_path = working.with_name(working.stem + "_saving" + working.suffix)
        _log(f"\n[1b.16] Persisting via save_net_as({tmp_path.name}) + os.replace onto {working.name}...")
        controller.save_net_as(tmp_path)
        os.replace(tmp_path, working)
        _log(f"        Persisted. Working copy size = {working.stat().st_size} bytes")

        xml_stops_after = _xml_pt_stops(working)
        xml_lines_after = _xml_pt_lines(working)
        _log(f"[1b.17] Saved XML ptStop count = {len(xml_stops_after)} (before {len(xml_stops_before)})")
        _log(f"        Saved XML ptLine count = {len(xml_lines_after)} (before {len(xml_lines_before)})")

        _log("\n" + "#" * 78)
        _log("# SUMMARY")
        _log("#" * 78)
        _log(f"PTStops collection: Net.{stop_coll_name}")
        _log(f"PTStop scalar ids: No={no_id!r} Name={name_id!r} Lane={lane_id!r} Pos={pos_id!r} Length={length_id!r}")
        _log(f"PTLines collection: Net.{line_coll_name}")
        _log(f"LineStops child collection: {stops_coll_name!r}; DepTimes child collection: {deptimes_coll_name!r}")

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

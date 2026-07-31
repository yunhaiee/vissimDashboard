"""v2b unit (1) PROBE-FIRST EMPIRICAL PROBE: how does Vissim represent lane
closure -- structurally (a new element type / a per-lane attribute) or is
it purely an attribute-level convention layered on an EXISTING schema
section (docs/phase2/00-EXECUTION-GUIDE.md rule: probe first, never trust
names -- verified repeatedly to matter: CycTm/CycleTime, MinHdwy/MinClear,
NodeRes/NodeResults)?

**STATIC PRE-SCAN (already run before this file existed, cheap ElementTree
grep, no COM instance, no licence consumed) -- findings pasted here for the
record:**

Grepped every ``<...closure...>``-shaped tag (case-insensitive) across the
full .inpx of all 20 real Vissim 2026 Examples Demo networks: ZERO hits, in
any network, for anything containing "closure". A ``<link>`` element's full
real attribute set (dumped from "Roundabout Billings.US") is: alc_v_limit,
assumSpeedOncom, consVehInDynPot, costPerKm, desSpeedFact, direction,
displayType, emergStopDist, emiCalcAct, gradient, hasOvtLn, isPedArea,
level, linkBehavType, linkEvalAct, linkEvalSegLen, lnChgDist,
lnChgDistIsPerLn, lnChgEvalAct, lookAheadDistOvt, mesoFollowUpGap,
mesoSpeed, mesoSpeedModel, name, netPerfEvalAct, no, ovtOnlyPT, ovtSpeedFact,
rvsPark, showClsfValues, showLinkBar, showVeh, surch1, surch2, thickness,
vehDynPotG, vehRecAct -- no closure/blocked/allowed-vehicle-class attribute
anywhere. A ``<lane>`` child element carries ONLY ``width`` (+ optionally
``markingType``) -- **no identity attribute at all**: lane addressing is a
synthesized 1-based ordinal (document order within ``<lanes>``), the same
landmine class as priority-rule conflict markers
(docs/phase2/DECISIONS.md, 2026-07-29 unit (2)). A real
``<reducedSpeedArea>`` (dumped from "Air Taxi Vertiport") carries exactly
{anmFlag, lane, length, name, no, pos, timeFrom, timeTo} + one
``<vehClassSpeedRed>`` child holding one ``<vehClassSpeedReduction
decel=... desSpeedDistr=... vehClass=.../>`` per scoped vehicle class -- the
SAME schema ``services.simulation_network_builder.inject_reduced_speed_area``
already writes for v2 unit (2) (``set_reduced_speed_area``); no "closed"
boolean or blocking flag exists on it.

This probe now confirms the negative live (via a type-library scan for any
Lane/Closure-named COM interface, plus a curated AttValue() candidate sweep
on a real Lane object and on Net itself) rather than resting on the XML
grep alone -- an interface/attribute could in principle exist in the COM
surface without ever having been exercised by any example network's own
.inpx (the CycTm/MinHdwy precedent: a plausible name can still be WRONG,
but so can "absent from every example" without a live cross-check of the
type library and a live AttValue() try).

Usage: python tools/probe_lane_closure.py [example_dir]
Default example: Roundabout Billings.US (2026 Examples Demo) -- 2-lane
links, matches every prior QUEUE-3/v2a unit's live-COM precedent.
"""
from __future__ import annotations

import os
import shutil
import sys
import re
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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "lane-closure-probe"

# Curated candidate AttValue() ids to try on a live Lane object -- real XML
# spelling first (this codebase's own "prefer the XML-matching id" precedent
# has been right more often than not), then plausible COM-only variants.
_LANE_ATTR_CANDIDATES: dict[str, list[str]] = {
    "index": ["Index", "index"],
    "width": ["Width", "width"],
    "link_no": ["Link\\No", "LinkNo"],
    "marking_type": ["MarkingType", "markingType"],
    "closed": ["Closed", "IsClosed", "LnClosed", "LaneClosed"],
    "blocked_veh_classes": [
        "BlockedVehClasses", "ClosedVehClasses", "VehClassesBlocked",
        "VehClassesClosed", "VehClosed", "BlockedForVehClasses",
    ],
    "allowed_veh_classes": ["AllowedVehClasses", "VehClassesAllowed"],
}

# Candidate collection names on Net that might expose a dedicated lane-
# closure object type (mirroring how ReducedSpeedAreas/ConflictAreas/
# PriorityRules are each their own Net collection).
_NET_CLOSURE_COLLECTION_CANDIDATES = [
    "LaneClosures", "LinkClosures", "LaneClosure", "Closures",
]

_BOGUS_ATTR = "ThisAttributeDoesNotExistProbe12345"


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
# Phase 0: static XML schema re-confirmation (this run, not just pasted
# findings) -- re-grep every example network for anything closure-shaped.
# ---------------------------------------------------------------------------


def probe_static_xml() -> None:
    examples_root = DEFAULT_EXAMPLE.parent
    hits = 0
    for inpx_path in sorted(examples_root.glob("*/*.inpx")):
        try:
            text = inpx_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "closure" in text.lower():
            hits += 1
            _log(f"      HIT: {inpx_path}")
    _log(f"      Scanned {len(list(examples_root.glob('*/*.inpx')))} example .inpx files "
         f"for the substring 'closure' (case-insensitive): {hits} hit(s).")

    # Full <link> attribute-name union across the default example, and the
    # full <lane> attribute-name union, to reconfirm neither carries a
    # closure-shaped attribute.
    root = ET.parse(next((DEFAULT_EXAMPLE).glob("*.inpx"))).getroot()
    link_attrs: set[str] = set()
    lane_attrs: set[str] = set()
    for link in root.findall(".//links/link"):
        link_attrs.update(link.attrib.keys())
        lanes_el = link.find("lanes")
        if lanes_el is not None:
            for lane in lanes_el.findall("lane"):
                lane_attrs.update(lane.attrib.keys())
    _log(f"      <link> attribute union ({len(link_attrs)}): {sorted(link_attrs)}")
    _log(f"      <lane> attribute union ({len(lane_attrs)}): {sorted(lane_attrs)}")
    closure_like = [a for a in link_attrs | lane_attrs if "clos" in a.lower() or "block" in a.lower()]
    _log(f"      closure/blocked-shaped attribute names found: {closure_like or '(none)'}")


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
            in_target = "lane" in class_name.lower() or "closure" in class_name.lower()
            if in_target:
                target_hits += 1
                _log(f"\n      -- {class_name} (DispatchBaseClass) --")
            continue
        if in_target and stripped.startswith("def ") and "(self" in stripped:
            _log(f"        {stripped}")

    if target_hits == 0:
        _log("      NO interface with 'lane'/'closure' in its name found in the type library.")
    else:
        _log(f"\n      Found {target_hits} matching interface(s) in the type library (listed above).")


# ---------------------------------------------------------------------------
# Phase 1b: live probe against a Roundabout Billings.US clone
# ---------------------------------------------------------------------------


def _discover_scalars(item: Any) -> dict[str, tuple[str, Any]]:
    found: dict[str, tuple[str, Any]] = {}
    for canonical, candidates in _LANE_ATTR_CANDIDATES.items():
        for attr_id in candidates:
            try:
                value = item.AttValue(attr_id)
                found[canonical] = (attr_id, value)
                break
            except Exception:
                continue
    return found


def main() -> None:
    example_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_EXAMPLE
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    _log("#" * 78)
    _log("# PHASE 0 -- static XML re-confirmation (no COM instance)")
    _log("#" * 78)
    probe_static_xml()

    _log("\n" + "#" * 78)
    _log("# PHASE 1a -- static type-library scan (no COM instance, no licence used)")
    _log("#" * 78)
    probe_typelib()

    _log("\n" + "#" * 78)
    _log(f"# PHASE 1b -- live probe on a {example_dir.name} clone")
    _log("#" * 78)

    work_dir = _fresh_work_dir(example_dir.name)
    shutil.copytree(example_dir, work_dir)
    inpx_files = sorted(work_dir.glob("*.inpx"))
    if not inpx_files:
        raise SystemExit(f"No .inpx in {work_dir}")
    working = inpx_files[0]
    _log(f"[1b.0] Working copy: {working}")

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

        _log("\n[1b.3] Net-level closure collection name discovery:")
        for name in _NET_CLOSURE_COLLECTION_CANDIDATES:
            try:
                obj = getattr(net, name)
                count = len(list(obj.GetAll()))
                _log(f"        [WORKS] Net.{name} -> GetAll() count = {count}")
            except Exception as exc:
                _log(f"        [no]    Net.{name} -> {type(exc).__name__}: {exc}")

        _log("\n[1b.4] Net.Links collection + a real Link's Lanes child collection:")
        links = list(net.Links.GetAll())
        _log(f"        Net.Links.GetAll() count = {len(links)}")
        target_link = next((lk for lk in links if int(lk.AttValue("NumLanes") or 0) >= 2), links[0])
        link_no = int(target_link.AttValue("No"))
        _log(f"        Target link No={link_no}, NumLanes={target_link.AttValue('NumLanes')!r}")

        lanes_candidates = ["Lanes"]
        lane_coll = None
        lane_coll_name = None
        for name in lanes_candidates:
            try:
                obj = getattr(target_link, name)
                items = list(obj.GetAll())
                _log(f"        [WORKS] Link.{name} -> GetAll() count = {len(items)}")
                lane_coll, lane_coll_name = obj, name
                break
            except Exception as exc:
                _log(f"        [no]    Link.{name} -> {type(exc).__name__}: {exc}")

        if lane_coll is None:
            raise SystemExit("FAIL: could not find a Lanes child collection on Link.")

        lane_items = list(lane_coll.GetAll())
        _log(f"\n[1b.5] Full scalar attribute discovery on every lane of link {link_no} "
             f"({len(lane_items)} lane(s)):")
        for idx, lane in enumerate(lane_items, start=1):
            scalars = _discover_scalars(lane)
            _log(f"        lane[{idx}]: {scalars}")
        missing = [c for c in _LANE_ATTR_CANDIDATES if c not in _discover_scalars(lane_items[0])]
        _log(f"        NOT FOUND on a Lane object: {missing}")

        _log("\n[1b.6] Control check: bogus attribute id on a Lane object "
              "(fail-open/closed sanity):")
        try:
            lane_items[0].AttValue(_BOGUS_ATTR)
            _log("        [UNEXPECTED] bogus attribute id did NOT raise -- fail-open!")
        except Exception as exc:
            _log(f"        [expected] bogus attribute id raised {type(exc).__name__}: {exc}")

        _log("\n[1b.7] Lane ItemByKey addressing check (is a Lane individually keyed, "
              "and by what -- index within Lanes, or a compound Link.Lane key?):")
        for key in (1, f"{link_no}-1", f"{link_no},1", (link_no, 1)):
            try:
                item = lane_coll.ItemByKey(key)
                _log(f"        [WORKS] Lanes.ItemByKey({key!r}) -> lane, Index={item.AttValue('Index')!r}")
            except Exception as exc:
                _log(f"        [no]    Lanes.ItemByKey({key!r}) -> {type(exc).__name__}: {exc}")

        _log("\n[1b.8] Add/Remove-shaped methods on the Lanes collection (dir() scan -- "
              "never called, just enumerated):")
        add_remove = [n for n in dir(lane_coll) if n.lower().startswith(("add", "remove"))]
        _log(f"        {add_remove}")

        _log("\n[1b.9] Reduced-speed-area schema re-confirmation: does ReducedSpeedArea "
              "expose a 'Closed'-shaped attribute distinct from desSpeedDistr?")
        rsa_coll_name = None
        for name in ["ReducedSpeedAreas"]:
            try:
                rsa_coll = getattr(net, name)
                rsa_items = list(rsa_coll.GetAll())
                rsa_coll_name = name
                _log(f"        Net.{name} -> GetAll() count = {len(rsa_items)}")
                if rsa_items:
                    for cand in ["Closed", "IsClosed", "Blocked"]:
                        try:
                            val = rsa_items[0].AttValue(cand)
                            _log(f"        [WORKS] ReducedSpeedArea.AttValue({cand!r}) = {val!r}")
                        except Exception as exc:
                            _log(f"        [no]    ReducedSpeedArea.AttValue({cand!r}) -> {type(exc).__name__}")
            except Exception as exc:
                _log(f"        [no]    Net.{name} -> {type(exc).__name__}: {exc}")

        _log("\n[1b.10] BlockedVehClasses write/readback (the ANSWER: [1b.5] found this attribute "
              "readable, blank on every real network -- is it genuinely WRITABLE, and in what "
              "format?):")
        target_lane = lane_items[0]
        before_blocked = target_lane.AttValue("BlockedVehClasses")
        _log(f"        before = {before_blocked!r}")
        for candidate_value in ["10", "10,20", "10;20"]:
            try:
                target_lane.SetAttValue("BlockedVehClasses", candidate_value)
                after = target_lane.AttValue("BlockedVehClasses")
                _log(f"        SetAttValue({candidate_value!r}) -> readback {after!r}")
            except Exception as exc:
                _log(f"        SetAttValue({candidate_value!r}) -> {type(exc).__name__}: {exc}")
        # Restore to empty before saving, so this probe's own saved XML dump
        # (next step) does not leave a stray closure on the example clone.
        target_lane.SetAttValue("BlockedVehClasses", "10,20")

        _log("\n[1b.11] Save + raw XML dump: is a non-empty BlockedVehClasses a plain <lane> "
              "ATTRIBUTE, or does it need a CHILD element (i.e. genuinely structural)?")
        save_path = work_dir / "after_blocked.inpx"
        controller.save_net_as(str(save_path))
        saved_text = save_path.read_text(encoding="utf-8", errors="replace")
        match = re.search(rf'<link [^>]*\bno="{link_no}"[^>]*>', saved_text)
        if match:
            lanes_idx = saved_text.find("<lanes>", match.start())
            _log(f"        <lanes> block for link {link_no} after SetAttValue(BlockedVehClasses, "
                 f"'10,20'):\n{saved_text[lanes_idx:lanes_idx + 260]}")
        target_lane.SetAttValue("BlockedVehClasses", "")
        _log("        Reverted lane back to BlockedVehClasses='' (open) after the dump.")

        _log("\n[1b.12] AddLane/RemoveLane SIGNATURE discovery (non-destructive: tries each "
              "candidate call shape but immediately undoes any that succeeds -- never leaves the "
              "clone's lane count changed):")
        try:
            removed = lane_coll.RemoveLane(lane_items[-1])
            after_remove_count = len(list(lane_coll.GetAll()))
            _log(f"        [WORKS] Lanes.RemoveLane(<lane object>) -- count {len(lane_items)} -> {after_remove_count}")
            restored = lane_coll.AddLane(after_remove_count, float(lane_items[-1].AttValue("Width")))
            _log(f"        [WORKS] Lanes.AddLane(index, width) -> restored count = {len(list(lane_coll.GetAll()))}")
        except Exception as exc:
            _log(f"        RemoveLane/AddLane signature probe -> {type(exc).__name__}: {exc}")

        _log("\n" + "#" * 78)
        _log("# SUMMARY")
        _log("#" * 78)
        _log("Net-level dedicated lane-closure collection found: "
              f"{'YES' if any('WORKS' in '' for _ in []) else 'see [1b.3] above'}")
        _log(f"Lane per-object closure/blocked attribute found: see [1b.5]/[1b.6] above")
        _log(f"ReducedSpeedAreas collection name: {rsa_coll_name}")

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

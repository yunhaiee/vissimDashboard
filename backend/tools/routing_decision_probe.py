"""v2 unit (6) Phase 1 EMPIRICAL PROBE: static vehicle routing decisions
(vehicleRoutingDecisionStatic > vehicleRouteStatic) and their relFlow
(relative-flow split) over COM.

Static XML pre-scan (already run, not repeated here at runtime -- see the
docstring of ``services.agent.tools_network._parse_routing_decisions`` for
the write-up): a network-wide grep across all 25 real Vissim 2026 example
networks confirmed ``relFlow`` is a comma-separated list of ``"2 <start_ms>:
<value>"`` fragments, one per referenced time interval -- exactly the
``timeIntervalSet no="VEHICLEROUTESTATIC"`` set (mirrors ``timeInt``'s
"constant-token start_ms" shape from vehicle inputs, v2 unit (5)). Beijing
(``docs/phase2/DECISIONS.md``'s pick, per its M2.4-era .err evidence and its
lack of external controller files -- verified: only .inpx/.layx/.jpg/.pdf
siblings) has 12 static routing decisions / 28 routes, single-interval
relFlow only (``"2 0:<value>"``) -- decisions with 1, 2, 3, and 5 routes all
present (no.9/10/11/12 are single-route, useful for the "one route" question
below). ``<vehicleRouteStatic>`` also carries a ``<linkSeq><intObjectRef
key="..."/></linkSeq>`` child -- every key verified to already match a
``services.inpx_parser.parse_network`` link id (both plain links and
connectors share one id space) -- this is the datum
``route_integrity_unverifiable`` needs and did not have before this unit.

This probe answers, on a live Vissim 2026 instance (attach_to_running=False,
Beijing.CN clone, closed in a ``finally`` block):

[Phase 1a] STATIC type-library scan (no COM instance, no licence consumed) --
same technique ``tools/conflict_area_probe.py``/``check_network_probe.py``
used: dump every interface whose name contains "rout" (case-insensitive) so
the REAL COM attribute ids come from the type library, never a guess from
the XML attribute spelling (the CycTm/NodeRes landmines both burned a
plausible-looking guess).

[Phase 1b] LIVE probe on a Beijing.CN clone:
  1. ``Net.VehicleRoutingDecisionsStatic`` (name TBD by 1a) ``GetAll()`` count
     cross-checked against the working copy's own
     ``<vehicleRoutingDecisionStatic>`` element count (12).
  2. Per-decision child collection discovery (``VehRoutSta`` per the XML
     child tag, or whatever 1a's scan reveals) and its own ``GetAll()``
     count per decision, cross-checked against the XML route count.
  3. Attribute-id discovery on ONE route item: No, RelFlow (indexed like
     Volume(i)?  scalar?), DestLink, DestPos -- read back and compared
     against the XML ground truth.
  4. Bogus decision-no / bogus route-no read -- per-object AttValue
     error-behavior data point (v2 unit (4) found per-object ConflictArea
     fails CLOSED, opposite of v2 unit (3)'s Net.AttValue fail-OPEN; v2 unit
     (5) found per-object-INDEXED Volume(i) also fails CLOSED -- this probe
     checks whether RelFlow(i)/ItemByKey on a routing decision/route follow
     the same pattern or are a THIRD data point).
  5. ONE live write: SetAttValue RelFlow(1) on one route of a MULTI-route
     decision, read back immediately, AND read back every SIBLING route's
     RelFlow(1) in the SAME decision -- this is the normalization question:
     does Vissim renormalize the siblings' stored values, or store the write
     as a raw, independent weight (i.e. proportions are computed at
     simulation time from the ratio of raw values, not enforced at write
     time)?
  6. A SECOND live write: SetAttValue RelFlow(1) on the ONE route of a
     single-route decision (no.9) -- what happens when there is nothing to
     redistribute against?
  7. Persist (save_net_as, the sanctioned VissimEditSession.persist() route)
     + grep the saved XML's relFlow string for both writes + a fresh COM
     reload to confirm the write survived a full round trip.

Never touches the original Examples Demo folder (00-EXECUTION-GUIDE.md rule
4) -- everything happens on a work-dir clone. Always disconnects every
dedicated Vissim instance in a ``finally`` block.

Usage: python tools/routing_decision_probe.py [example_dir]
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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "routing-decision-probe"

_CONTROL_BOGUS_ATTR = "ThisAttributeDoesNotExistProbe12345"

# Every plausible COM collection/attribute name, tried in this order -- the
# FIRST one that works is treated as real for the rest of the probe (same
# discipline as conflict_area_probe.py's _first_working_attr).
_DECISIONS_COLLECTION_CANDIDATES = [
    "VehicleRoutingDecisionsStatic",
    "VehicleRoutingDecisionStatic",
    "RoutingDecisionsStatic",
]
_ROUTES_CHILD_CANDIDATES = ["VehRoutSta", "VehicleRoutesStatic", "Routes", "VehRoutStas"]
_NO_CANDIDATES = ["No", "no"]
_LINK_CANDIDATES = ["Link", "link"]
_DEST_LINK_CANDIDATES = ["DestLink", "DestinationLink"]
_DEST_POS_CANDIDATES = ["DestPos", "DestinationPos"]
_REL_FLOW_INDEXED_CANDIDATES = ["RelFlow(1)"]
_REL_FLOW_SCALAR_CANDIDATES = ["RelFlow"]


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
            in_target = "rout" in class_name.lower()
            if in_target:
                target_hits += 1
                _log(f"\n      -- {class_name} (DispatchBaseClass) --")
            continue
        if in_target and stripped.startswith("def ") and "(self" in stripped:
            _log(f"        {stripped}")
        if in_target and (stripped.startswith("_prop_map_get_") or stripped.startswith("_prop_map_put_")):
            _log(f"        {stripped}")

    if target_hits == 0:
        _log("      NO interface with 'rout' in its name found anywhere in the type library.")
    else:
        _log(f"\n      Found {target_hits} '*rout*' interface(s) in the type library (listed above).")


# ---------------------------------------------------------------------------
# Phase 1b: live probe against a Beijing.CN clone
# ---------------------------------------------------------------------------


def _xml_decisions(inpx_path: Path) -> list[dict[str, Any]]:
    root = ET.parse(inpx_path).getroot()
    result = []
    for dec in root.findall(".//vehicleRoutingDecisionStatic"):
        routes = []
        for r in dec.findall(".//vehicleRouteStatic"):
            link_seq = [ref.get("key", "") for ref in r.findall(".//linkSeq/intObjectRef")]
            routes.append({**dict(r.attrib), "link_seq": link_seq})
        result.append({**dict(dec.attrib), "routes": routes})
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

    xml_before = _xml_decisions(working)
    _log(f"       XML <vehicleRoutingDecisionStatic> count = {len(xml_before)}")
    for dec in xml_before:
        _log(f"       decision no={dec['no']} link={dec['link']} routes={[r['no'] for r in dec['routes']]}")

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

        _log("\n[1b.3] Decision collection name discovery on Net:")
        coll_name, decisions_coll = _first_working_collection(
            controller.vissim.Net, _DECISIONS_COLLECTION_CANDIDATES, "VehicleRoutingDecisionsStatic"
        )
        if decisions_coll is None:
            raise SystemExit("FAIL: could not find the static routing decisions collection on Net.")

        decision_items = list(decisions_coll.GetAll())
        _log(f"       Net.{coll_name}.GetAll() count = {len(decision_items)} (XML count = {len(xml_before)})")

        first_decision = decision_items[0]
        _log("\n[1b.4] Decision-level attribute discovery on the FIRST decision:")
        dec_no_id, dec_no_val = _first_working_attr(first_decision, _NO_CANDIDATES, "No")
        dec_link_id, dec_link_val = _first_working_attr(first_decision, _LINK_CANDIDATES, "Link")

        _log("\n[1b.5] Child route-collection name discovery on the FIRST decision:")
        routes_name, routes_coll = _first_working_child_collection(
            first_decision, _ROUTES_CHILD_CANDIDATES, "VehRoutSta"
        )
        if routes_coll is None:
            raise SystemExit("FAIL: could not find the per-decision route child collection.")

        route_items = list(routes_coll.GetAll())
        xml_first_dec = next(d for d in xml_before if d["no"] == str(dec_no_val))
        _log(
            f"       .{routes_name}.GetAll() count = {len(route_items)} "
            f"(XML count for decision no={dec_no_val} = {len(xml_first_dec['routes'])})"
        )

        first_route = route_items[0]
        _log("\n[1b.6] Route-level attribute discovery on the FIRST route:")
        route_no_id, route_no_val = _first_working_attr(first_route, _NO_CANDIDATES, "No")
        dest_link_id, dest_link_val = _first_working_attr(first_route, _DEST_LINK_CANDIDATES, "DestLink")
        dest_pos_id, dest_pos_val = _first_working_attr(first_route, _DEST_POS_CANDIDATES, "DestPos")
        _log("       -- RelFlow: scalar vs indexed --")
        relflow_scalar_id, relflow_scalar_val = _first_working_attr(
            first_route, _REL_FLOW_SCALAR_CANDIDATES, "RelFlow (scalar)"
        )
        relflow_indexed_id, relflow_indexed_val = _first_working_attr(
            first_route, _REL_FLOW_INDEXED_CANDIDATES, "RelFlow(1) (indexed)"
        )
        xml_first_route = xml_first_dec["routes"][0]
        _log(f"       XML ground truth for this route: {xml_first_route}")

        _log("\n[1b.7] CONTROL: bogus attribute id on a real route item (fail-open/closed check)...")
        try:
            bogus = first_route.AttValue(_CONTROL_BOGUS_ATTR)
            _log(f"        [CONTROL] AttValue({_CONTROL_BOGUS_ATTR!r}) -> {bogus!r} (fails OPEN)")
        except Exception as exc:
            _log(f"        [CONTROL] AttValue({_CONTROL_BOGUS_ATTR!r}) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        _log("\n[1b.8] Bogus decision no -- ItemByKey error behavior...")
        try:
            bogus_dec = decisions_coll.ItemByKey(999999)
            _log(f"        ItemByKey(999999) -> {bogus_dec!r} (UNEXPECTED -- did not raise)")
        except Exception as exc:
            _log(f"        ItemByKey(999999) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        _log("\n[1b.9] Bogus route no within a real decision -- ItemByKey error behavior...")
        try:
            bogus_route = routes_coll.ItemByKey(999999)
            _log(f"        ItemByKey(999999) -> {bogus_route!r} (UNEXPECTED -- did not raise)")
        except Exception as exc:
            _log(f"        ItemByKey(999999) -> {type(exc).__name__}: {exc} (fails CLOSED)")

        if relflow_indexed_id is None and relflow_scalar_id is None:
            raise SystemExit("FAIL: neither scalar nor indexed RelFlow worked -- cannot continue.")
        relflow_id = relflow_indexed_id or relflow_scalar_id

        # -------------------------------------------------------------
        # [1b.10] MULTI-route decision write: does Vissim renormalize
        # sibling routes, or store an independent raw weight?
        # -------------------------------------------------------------
        multi_dec_no = "1"  # 3 routes on Beijing, per the static pre-scan
        multi_dec_item = decisions_coll.ItemByKey(int(multi_dec_no))
        multi_routes_coll = getattr(multi_dec_item, routes_name)
        multi_route_items = list(multi_routes_coll.GetAll())
        _log(f"\n[1b.10] MULTI-route write test -- decision no={multi_dec_no} ({len(multi_route_items)} routes)")
        before_vals = {}
        for r in multi_route_items:
            r_no = int(r.AttValue(route_no_id))
            before_vals[r_no] = r.AttValue(relflow_id)
        _log(f"        BEFORE -- every route's RelFlow: {before_vals}")

        target_route_no = min(before_vals)
        target_route_item = multi_routes_coll.ItemByKey(target_route_no)
        old_val = before_vals[target_route_no]
        new_val = float(old_val) + 500.0
        _log(f"        SetAttValue({relflow_id!r}, {new_val}) on route no={target_route_no} "
             f"(was {old_val!r})...")
        try:
            target_route_item.SetAttValue(relflow_id, new_val)
            _log("        SetAttValue succeeded (no exception).")
        except Exception as exc:
            _log(f"        SetAttValue RAISED: {type(exc).__name__}: {exc}")

        after_vals = {}
        for r in multi_route_items:
            r_no = int(r.AttValue(route_no_id))
            after_vals[r_no] = r.AttValue(relflow_id)
        _log(f"        AFTER -- every route's RelFlow: {after_vals}")
        _log(f"        Target route changed to the RAW written value (not pre-normalized): "
             f"{abs(float(after_vals[target_route_no]) - new_val) < 1e-6}")
        siblings_changed = {
            k: (before_vals[k], after_vals[k])
            for k in before_vals
            if k != target_route_no and str(before_vals[k]) != str(after_vals[k])
        }
        _log(f"        Sibling routes whose RelFlow VALUE changed as a side effect: {siblings_changed} "
             f"(empty == raw/independent weight, non-empty == Vissim renormalizes on write)")

        # -------------------------------------------------------------
        # [1b.11] SINGLE-route decision write (no.9)
        # -------------------------------------------------------------
        single_dec_no = "9"
        single_dec_item = decisions_coll.ItemByKey(int(single_dec_no))
        single_routes_coll = getattr(single_dec_item, routes_name)
        single_route_items = list(single_routes_coll.GetAll())
        _log(f"\n[1b.11] SINGLE-route write test -- decision no={single_dec_no} ({len(single_route_items)} route(s))")
        only_route = single_route_items[0]
        only_route_no = int(only_route.AttValue(route_no_id))
        old_single_val = only_route.AttValue(relflow_id)
        new_single_val = float(old_single_val) + 999.0
        _log(f"        BEFORE: route no={only_route_no} RelFlow={old_single_val!r}")
        _log(f"        SetAttValue({relflow_id!r}, {new_single_val}) on the ONLY route...")
        try:
            only_route.SetAttValue(relflow_id, new_single_val)
            _log("        SetAttValue succeeded (no exception).")
        except Exception as exc:
            _log(f"        SetAttValue RAISED: {type(exc).__name__}: {exc}")
        after_single_val = only_route.AttValue(relflow_id)
        _log(f"        AFTER: route no={only_route_no} RelFlow={after_single_val!r} "
             f"(matches written value: {abs(float(after_single_val) - new_single_val) < 1e-6})")

        # -------------------------------------------------------------
        # [1b.12] Persist + reparse + fresh COM reload
        # -------------------------------------------------------------
        tmp_path = working.with_name(working.stem + "_saving" + working.suffix)
        _log(f"\n[1b.12] Persisting via save_net_as({tmp_path.name}) + os.replace onto {working.name}...")
        controller.save_net_as(tmp_path)
        os.replace(tmp_path, working)
        _log(f"        Persisted. Working copy size = {working.stat().st_size} bytes")

        xml_after = _xml_decisions(working)
        xml_multi_after = next(d for d in xml_after if d["no"] == multi_dec_no)
        xml_single_after = next(d for d in xml_after if d["no"] == single_dec_no)
        _log(f"[1b.13] Saved XML decision no={multi_dec_no} routes: {xml_multi_after['routes']}")
        _log(f"        Saved XML decision no={single_dec_no} routes: {xml_single_after['routes']}")

        _log("\n[1b.14] Fresh COM reload of the saved file (separate connection)...")
        controller.disconnect()
        verify_controller = VissimController()
        verify_controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        verify_controller.load_network(str(working))
        reload_decisions = getattr(verify_controller.vissim.Net, coll_name)
        reload_multi_dec = reload_decisions.ItemByKey(int(multi_dec_no))
        reload_multi_routes = getattr(reload_multi_dec, routes_name)
        reload_target_route = reload_multi_routes.ItemByKey(target_route_no)
        reload_relflow = reload_target_route.AttValue(relflow_id)
        _log(f"        Reload decision no={multi_dec_no} route no={target_route_no} RelFlow={reload_relflow!r} "
             f"(intended {new_val!r}) -> match: {abs(float(reload_relflow) - new_val) < 1e-6}")

        controller = verify_controller  # so the finally block disconnects this one

        _log("\n" + "#" * 78)
        _log("# SUMMARY")
        _log("#" * 78)
        _log(f"Decisions collection: Net.{coll_name}")
        _log(f"Per-decision route child collection: .{routes_name}")
        _log(f"Route attribute ids: No={route_no_id!r} DestLink={dest_link_id!r} DestPos={dest_pos_id!r} "
             f"RelFlow scalar={relflow_scalar_id!r} RelFlow indexed={relflow_indexed_id!r}")
        _log(f"Normalization on write: siblings changed = {bool(siblings_changed)}")

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

"""Q4 unit (3) PROBE-FIRST EMPIRICAL PROBE: full link closure + turn bans
(movement removal). House shape = ``probe_measurement_infra.py`` (the
newest v2b/Q4 probe): static XML re-scan -> static type-library scan (no
COM instance) -> live COM probe on real example-network clones.

**Two capabilities this probe informs:**

A. Full link closure/reopen -- is there a dedicated LINK-level "Blocked"/
   "Closed" attribute (candidate 2 in the task brief), or is unit (1)'s
   per-lane ``BlockedVehClasses`` (candidate 1) composed link-wide the only
   real mechanism, exactly as v2b unit (1) already found for a SINGLE lane?

B. Turn bans / movement removal -- do CONNECTORS have the same ``<lanes>``
   child collection a plain link has (so unit (1)'s ``set_lane_closure``
   verb already works, unmodified, against a connector's own lane)? And
   when a connector is REMOVED structurally, what happens to a conflict
   area whose ``linkA``/``linkB`` names that connector -- does Vissim's own
   LoadNet refuse, silently drop the stale ``<conflictArea>``, or leave a
   dangling reference the way Q4 unit (2) found for data-collection-point
   removal?

**STATIC PRE-SCAN (already run before this file existed, cheap ElementTree
grep across all 25 real Vissim 2026 Examples Demo networks -- findings
pasted here for the record, re-confirmed live by :func:`probe_static_xml`
below):**

Every real ``.inpx``'s connector IS a plain ``<link>`` element (Vissim's own
convention -- ``fromLinkEndPt``/``toLinkEndPt`` children mark it as a
connector; nothing else distinguishes the tag) carrying the SAME ``<lanes>``
child collection a non-connector link has -- confirmed across Roundabout
Billings.US (17 connectors, 1 lane each), Air Taxi Vertiport, Roundabout
Buehl.DE. A network's own routing decisions traverse both plain links and
connectors in one shared id space (``<linkSeq><intObjectRef key="..."/>``,
already established by v2 unit (6)); conflict areas commonly name a
connector as ``linkA``/``linkB`` (measured: 163/224 conflict areas on Urban
Intersection Beijing.CN reference a connector, not a plain link -- the
COMMON case, not an edge case).

Static full-network scan (``tools/probe_link_closure_turn_bans_static_scan.py``
output, folded into this file's own :func:`probe_static_xml` below) found
NO example network anywhere with a fully-unreferenced connector AND real
routes/conflict-areas AND a small link count all at once for Roundabout
Billings.US specifically (every one of its 17 connectors is referenced by
at least a route or a conflict area) -- Roundabout Buehl.DE (already proven
live-COM-loadable, Q3 unit (2)) has exactly 2 (10059, 10064), used here for
the clean-removal half of this probe. Billings is used for the turn-ban
live write and the REFERENCED-connector removal probe (connector 10002,
referenced by exactly one conflict area, no=17, and no route -- a clean
single-referrer case).

Usage: python tools/probe_link_closure_turn_bans.py
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

EXAMPLES_ROOT = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo"
)
BILLINGS = EXAMPLES_ROOT / "Roundabout Billings.US"
BUEHL = EXAMPLES_ROOT / "Roundabout Buehl.DE"
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "link-closure-turn-ban-probe"

_BOGUS_ATTR = "ThisAttributeDoesNotExistProbe12345"

# Curated candidate LINK-level (not lane-level -- v2b unit (1) already
# exhausted the lane-level candidate list) AttValue() ids to try, in case a
# dedicated whole-link closure attribute exists that unit (1)'s own probe
# (scoped to Lane objects only) would never have seen.
_LINK_ATTR_CANDIDATES: list[str] = [
    "Closed", "IsClosed", "Blocked", "BlockedVehClasses", "ClosedVehClasses",
    "LinkClosed", "Active", "IsActive", "Enabled",
]

# Candidate Net-level collection names that might expose a dedicated
# turn-ban/movement-removal object type distinct from a plain connector
# remove (mirrors probe_lane_closure.py's own _NET_CLOSURE_COLLECTION_CANDIDATES).
_NET_TURN_BAN_COLLECTION_CANDIDATES = [
    "TurnBans", "MovementRestrictions", "BannedTurns", "ManagedLanes",
]


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
# Phase 0: static XML re-confirmation (this run, not just pasted findings)
# ---------------------------------------------------------------------------


def _connector_ids(root: ET.Element) -> set[str]:
    ids = set()
    for link in root.findall(".//links/link"):
        if link.find("fromLinkEndPt") is not None or link.find("toLinkEndPt") is not None:
            ids.add(link.get("no", ""))
    return ids


def probe_static_xml() -> None:
    for inpx_dir in (BILLINGS, BUEHL):
        root = ET.parse(next(inpx_dir.glob("*.inpx"))).getroot()
        links = root.findall(".//links/link")
        connector_ids = _connector_ids(root)
        connector_el = next(
            (l for l in links if l.get("no") in connector_ids and l.find("lanes") is not None), None
        )
        _log(f"      {inpx_dir.name}: {len(links)} links, {len(connector_ids)} connectors")
        if connector_el is not None:
            lanes_el = connector_el.find("lanes")
            _log(
                f"        connector {connector_el.get('no')} <lanes> child present: "
                f"{lanes_el is not None}, lane count={len(lanes_el.findall('lane')) if lanes_el is not None else 0}"
            )
            _log(f"        connector element attrs: {sorted(connector_el.attrib.keys())}")
        else:
            _log("        NO connector with a <lanes> child found (unexpected)")

        # Re-confirm no link-level closure-shaped attribute exists in the XML
        # (unit (1) already checked this for <lane>; recheck <link> itself).
        link_attrs: set[str] = set()
        for link in links:
            link_attrs.update(link.attrib.keys())
        closure_like = [a for a in link_attrs if "clos" in a.lower() or "block" in a.lower()]
        _log(f"        <link> attrs closure/block-shaped: {closure_like or '(none)'}")

    # Cross-network tally: how often does a conflict area reference a
    # CONNECTOR (vs a plain link) as linkA/linkB -- establishes whether the
    # movement-removal consequence check matters in the common case or an
    # edge case.
    total_ca = 0
    total_ca_on_connector = 0
    for inpx_path in sorted(EXAMPLES_ROOT.glob("*/*.inpx")):
        try:
            root = ET.parse(inpx_path).getroot()
        except ET.ParseError:
            continue
        connector_ids = _connector_ids(root)
        cas = root.findall(".//conflictArea")
        total_ca += len(cas)
        total_ca_on_connector += sum(
            1 for ca in cas if ca.get("linkA") in connector_ids or ca.get("linkB") in connector_ids
        )
    _log(
        f"\n      Cross-network tally: {total_ca_on_connector}/{total_ca} conflict areas "
        "reference a CONNECTOR as linkA/linkB (across all 25 example networks)."
    )


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
        raise RuntimeError("No registered 'Vissim Object Library' type library found.")

    def _hex_int(value: str) -> int:
        return int(value, 16)

    match = max(candidates, key=lambda t: (_hex_int(t.major), _hex_int(t.minor)))
    _log(f"      using: {match.desc}  clsid={match.clsid}")
    major_i, minor_i = _hex_int(match.major), _hex_int(match.minor)
    makepy.GenerateFromTypeLibSpec((match.clsid, match.lcid, major_i, minor_i))
    mod = gencache.EnsureModule(match.clsid, match.lcid, major_i, minor_i)
    src_path = Path(mod.__file__)
    lines = src_path.read_text(encoding="utf-8", errors="replace").splitlines()

    # Any interface whose name contains "turn"/"movement"/"ban"/"restrict".
    class_name = None
    in_target = False
    hits = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("class I") and "(DispatchBaseClass)" in stripped:
            class_name = stripped.split("(")[0].removeprefix("class ").strip()
            in_target = any(
                token in class_name.lower() for token in ("turn", "movement", "ban", "restrict")
            )
            if in_target:
                hits += 1
                _log(f"\n      -- {class_name} (DispatchBaseClass) --")
            continue
        if in_target and stripped.startswith("def ") and "(self" in stripped:
            _log(f"        {stripped}")
    if hits == 0:
        _log("      NO interface with 'turn'/'movement'/'ban'/'restrict' in its name found.")

    # Does ILink itself expose anything Closed/Blocked-shaped as a property
    # (not just via AttValue)?
    _log("\n      -- ILink property scan for Clos/Block-shaped names --")
    class_name = None
    in_ilink = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("class I") and "(DispatchBaseClass)" in stripped:
            class_name = stripped.split("(")[0].removeprefix("class ").strip()
            in_ilink = class_name == "ILink"
            continue
        if in_ilink and stripped.startswith("def "):
            name_lower = stripped.lower()
            if "clos" in name_lower or "block" in name_lower:
                _log(f"        {stripped}")
    _log("      (empty above == no Clos/Block-shaped ILink method found)")


# ---------------------------------------------------------------------------
# Phase 1b: live probe against a Roundabout Billings.US clone -- link-wide
# closure composition + turn ban (connector lane closure) + referenced-
# connector removal consequence.
# ---------------------------------------------------------------------------


def _dump_lanes(saved_text: str, link_no: str) -> str:
    import re

    match = re.search(rf'<link [^>]*\bno="{link_no}"[^>]*>', saved_text)
    if not match:
        return "(link not found in saved XML)"
    lanes_idx = saved_text.find("<lanes>", match.start())
    end_idx = saved_text.find("</lanes>", lanes_idx)
    return saved_text[lanes_idx : end_idx + len("</lanes>")] if lanes_idx != -1 else "(no <lanes>)"


def main() -> None:
    _log("#" * 78)
    _log("# PHASE 0 -- static XML re-confirmation (no COM instance)")
    _log("#" * 78)
    probe_static_xml()

    _log("\n" + "#" * 78)
    _log("# PHASE 1a -- static type-library scan (no COM instance, no licence used)")
    _log("#" * 78)
    probe_typelib()

    _log("\n" + "#" * 78)
    _log(f"# PHASE 1b -- live probe on a {BILLINGS.name} clone")
    _log("#" * 78)

    work_dir = _fresh_work_dir(BILLINGS.name)
    shutil.copytree(BILLINGS, work_dir)
    working = sorted(work_dir.glob("*.inpx"))[0]
    _log(f"[1b.0] Working copy: {working}")

    xml_before_root = ET.parse(working).getroot()
    connector_ids = _connector_ids(xml_before_root)
    _log(f"       Connector ids: {sorted(connector_ids, key=int)}")

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

        _log("\n[1b.3] Net-level turn-ban collection name discovery:")
        for name in _NET_TURN_BAN_COLLECTION_CANDIDATES:
            try:
                obj = getattr(net, name)
                count = len(list(obj.GetAll()))
                _log(f"        [WORKS] Net.{name} -> GetAll() count = {count}")
            except Exception as exc:
                _log(f"        [no]    Net.{name} -> {type(exc).__name__}: {exc}")

        _log("\n[1b.4] A connector resolved via Net.Links.ItemByKey (same collection as a "
             "plain link, per v2 unit (6)'s own finding)?")
        target_connector_no = 10006  # 3 conflict-area references + 2 route references
        connector_link = net.Links.ItemByKey(target_connector_no)
        _log(f"        Net.Links.ItemByKey({target_connector_no}) -> "
             f"No={connector_link.AttValue('No')!r} Name={connector_link.AttValue('Name')!r}")
        conn_lanes = list(connector_link.Lanes.GetAll())
        _log(f"        .Lanes.GetAll() count = {len(conn_lanes)} "
             f"(Index of lane[0] = {conn_lanes[0].AttValue('Index')!r})")

        _log("\n[1b.5] LINK-level (not lane-level) closure attribute candidate sweep on a "
             "connector object -- does a dedicated whole-link Closed/Blocked attribute exist?")
        for attr_id in _LINK_ATTR_CANDIDATES:
            try:
                val = connector_link.AttValue(attr_id)
                _log(f"        [WORKS] connector.AttValue({attr_id!r}) = {val!r}")
            except Exception as exc:
                _log(f"        [no]    connector.AttValue({attr_id!r}) -> {type(exc).__name__}")

        _log("\n[1b.6] Control: bogus attribute id on the connector object (fail-open/closed sanity):")
        try:
            connector_link.AttValue(_BOGUS_ATTR)
            _log("        [UNEXPECTED] bogus attribute id did NOT raise -- fail-open!")
        except Exception as exc:
            _log(f"        [expected] bogus attribute id raised {type(exc).__name__}: {exc}")

        _log("\n[1b.7] TURN BAN write: BlockedVehClasses on EVERY lane of connector "
             f"{target_connector_no} (composition over unit (1)'s own per-lane mechanism) -- "
             "then reopen:")
        for lane in conn_lanes:
            before = lane.AttValue("BlockedVehClasses")
            _log(f"        lane Index={lane.AttValue('Index')} before = {before!r}")
        for lane in conn_lanes:
            lane.SetAttValue("BlockedVehClasses", "10,20")
        for lane in conn_lanes:
            after = lane.AttValue("BlockedVehClasses")
            _log(f"        lane Index={lane.AttValue('Index')} after SetAttValue('10,20') = {after!r}")
        save_path_1 = work_dir / "after_turn_ban.inpx"
        controller.save_net_as(str(save_path_1))
        saved_text = save_path_1.read_text(encoding="utf-8", errors="replace")
        _log(f"        Saved <lanes> block for connector {target_connector_no}:\n"
             f"{_dump_lanes(saved_text, str(target_connector_no))}")
        for lane in conn_lanes:
            lane.SetAttValue("BlockedVehClasses", "")
        _log("        Reverted connector lanes back to BlockedVehClasses='' (reopened).")

        _log("\n[1b.8] FULL LINK CLOSURE write: BlockedVehClasses on EVERY lane of a "
             "PLAIN (non-connector) link -- link 6 (2 lanes, referenced by conflict areas "
             "2/3/6/8/9/13/14/15 and route link_seqs) -- then reopen:")
        plain_link_no = 6
        plain_link = net.Links.ItemByKey(plain_link_no)
        plain_lanes = list(plain_link.Lanes.GetAll())
        _log(f"        Link {plain_link_no} lane count = {len(plain_lanes)}")
        for lane in plain_lanes:
            lane.SetAttValue("BlockedVehClasses", "10,20,30")
        save_path_2 = work_dir / "after_link_closure.inpx"
        controller.save_net_as(str(save_path_2))
        saved_text_2 = save_path_2.read_text(encoding="utf-8", errors="replace")
        _log(f"        Saved <lanes> block for link {plain_link_no}:\n"
             f"{_dump_lanes(saved_text_2, str(plain_link_no))}")
        for lane in plain_lanes:
            lane.SetAttValue("BlockedVehClasses", "")
        _log("        Reverted link lanes back to BlockedVehClasses='' (reopened).")

        _log("\n[1b.9] REFERENCED-connector removal consequence: connector 10002 is "
             "referenced by exactly ONE conflict area (no=17, linkA=4/linkB=10002), no "
             "route. Remove connector 10002's <link> element via raw XML (never through "
             "COM -- no Add/Remove exists for a plain link/connector), then run this "
             "codebase's own load/init oracle (tools_check._load_check) on the mutated "
             "file to see whether Vissim's LoadNet refuses, silently drops the stale "
             "conflictArea, or leaves a dangling reference:")
        removal_dir = _fresh_work_dir(BILLINGS.name + "_removal")
        shutil.copytree(BILLINGS, removal_dir)
        removal_inpx = sorted(removal_dir.glob("*.inpx"))[0]
        tree = ET.parse(removal_inpx)
        root = tree.getroot()
        links_el = root.find(".//links")
        target_el = next(l for l in links_el.findall("link") if l.get("no") == "10002")
        links_el.remove(target_el)
        ca_before = len(root.findall(".//conflictArea"))
        ca17 = next((ca for ca in root.findall(".//conflictArea") if ca.get("no") == "17"), None)
        _log(f"        Before save: conflictArea no=17 still present in tree: {ca17 is not None} "
             f"(linkA={ca17.get('linkA') if ca17 is not None else None}, "
             f"linkB={ca17.get('linkB') if ca17 is not None else None})")
        if hasattr(ET, "indent"):
            ET.indent(tree, space="  ")
        tree.write(removal_inpx, encoding="UTF-8", xml_declaration=True)

        from services.agent.tools_check import _load_check

        load_result = _load_check(removal_inpx)
        _log(f"        load_check result: load_ok={load_result.get('load_ok')} "
             f"load_error={load_result.get('load_error')!r} "
             f"init_ok={load_result.get('init_ok')} init_error={load_result.get('init_error')!r}")

        if load_result.get("load_ok") and load_result.get("init_ok"):
            _log("        LoadNet SUCCEEDED with the dangling conflictArea reference still "
                 "in the XML -- checking whether Vissim's own SaveNetAs re-derives/drops it "
                 "or preserves the dangling reference verbatim:")
            reload_save = removal_dir / "after_dangling_ca_reload.inpx"
            pythoncom.CoInitialize()
            reload_controller = VissimController()
            try:
                reload_controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
                reload_controller.load_network(str(removal_inpx))
                reload_net = reload_controller.vissim.Net
                ca_count_com = len(list(reload_net.ConflictAreas.GetAll()))
                _log(f"        Net.ConflictAreas.GetAll() count after loading the mutated "
                     f"file = {ca_count_com} (was {ca_before} in the pre-mutation XML)")
                try:
                    ca17_com = next(
                        ca for ca in reload_net.ConflictAreas.GetAll() if str(ca.AttValue("No")) == "17"
                    )
                    _log(f"        conflictArea 17 still resolvable via COM: LinkA="
                         f"{ca17_com.AttValue('LinkA')!r} LinkB={ca17_com.AttValue('LinkB')!r}")
                except StopIteration:
                    _log("        conflictArea 17 NOT found via COM after reload (Vissim "
                         "silently dropped it) -- would confirm a WARN-not-refuse design is safe.")
                reload_controller.save_net_as(str(reload_save))
                reload_saved_text = reload_save.read_text(encoding="utf-8", errors="replace")
                still_names_10002 = (
                    'linkB="10002"' in reload_saved_text or 'linkA="10002"' in reload_saved_text
                )
                _log(f"        Re-saved XML still names link 10002 as linkA/linkB anywhere: "
                     f"{still_names_10002}")
            finally:
                try:
                    reload_controller.disconnect()
                except Exception:
                    pass
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass
        else:
            _log("        LoadNet/init FAILED on the dangling-reference file -- confirms a "
                 "REFUSE (never-silently-cascade) design is the only safe choice for "
                 "remove_connector when a conflict area still references it.")

        _log("\n" + "#" * 78)
        _log("# SUMMARY (Billings)")
        _log("#" * 78)

    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        _log("\nDisconnected the dedicated Vissim instance (Billings).")

    # -----------------------------------------------------------------
    # Phase 1c: UNREFERENCED-connector removal on a Roundabout Buehl.DE
    # clone -- proves the clean-removal + load-oracle path end to end.
    # -----------------------------------------------------------------
    _log("\n" + "#" * 78)
    _log(f"# PHASE 1c -- live probe on a {BUEHL.name} clone (unreferenced connector removal)")
    _log("#" * 78)

    buehl_work = _fresh_work_dir(BUEHL.name)
    shutil.copytree(BUEHL, buehl_work)
    buehl_inpx = sorted(buehl_work.glob("*.inpx"))[0]
    buehl_root = ET.parse(buehl_inpx).getroot()
    buehl_connectors = _connector_ids(buehl_root)
    _log(f"[1c.0] Buehl connectors: {len(buehl_connectors)}; testing unreferenced connector 10059")

    tree = ET.parse(buehl_inpx)
    root = tree.getroot()
    links_el = root.find(".//links")
    target_el = next(l for l in links_el.findall("link") if l.get("no") == "10059")
    links_el.remove(target_el)
    if hasattr(ET, "indent"):
        ET.indent(tree, space="  ")
    tree.write(buehl_inpx, encoding="UTF-8", xml_declaration=True)

    pythoncom.CoInitialize()
    from services.agent.tools_check import _load_check as _load_check2

    load_result_2 = _load_check2(buehl_inpx)
    _log(f"[1c.1] load_check result for unreferenced-connector removal: "
         f"load_ok={load_result_2.get('load_ok')} init_ok={load_result_2.get('init_ok')} "
         f"load_error={load_result_2.get('load_error')!r} init_error={load_result_2.get('init_error')!r}")
    try:
        pythoncom.CoUninitialize()
    except Exception:
        pass


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()

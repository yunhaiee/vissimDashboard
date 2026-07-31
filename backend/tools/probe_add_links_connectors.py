"""Q4 unit (5) PROBE-FIRST EMPIRICAL PROBE: add/remove links & connectors.

House shape = ``probe_signal_heads_detectors.py`` (the newest Q4 probe,
per this unit's own task brief): static XML re-scan across real 2026
Examples Demo networks -> static COM type-library scan (no COM instance, no
licence used) -> live COM probe on a real example-network clone (create ->
inspect -> SaveNetAs dump -> remove).

**STATIC PRE-SCAN (already run before this file existed, via the .venv
python + ElementTree directly against Urban Intersection Beijing.CN --
findings pasted here for the record, re-confirmed by :func:`probe_static_xml`
below):**

A plain ``<link>``'s geometry is **NOT** ``<points3D><point3D x y zOffset>``
(the task brief's own guess) -- the REAL shape, verified against every
example network that has one, is::

    <link no="1" name="" displayType="1" linkBehavType="1" ...>
      <geometry>
        <linkPolyPts>
          <linkPolyPoint x="-764.823" y="-501.918" zOffset="0"
                         radiusEffect="BRAKE" radiusSrc="CALCULATED" />
          <linkPolyPoint x="-429.701" y="-502.22" zOffset="0"
                         radiusEffect="BRAKE" radiusSrc="CALCULATED" />
        </linkPolyPts>
      </geometry>
      <lanes>
        <lane markingType="1" width="3" />
        ...
      </lanes>
    </link>

A connector is the SAME ``<link>`` element, carrying ``fromLinkEndPt``/
``toLinkEndPt`` children (confirming unit (3)'s own finding, re-verified
here) PLUS its own ``<lanes><lane><fromLanes><objectRef key="1 1"/></fromLanes>
<toLanes><objectRef key="2 1"/></toLanes></lane></lanes>`` per-lane mapping::

    <link no="10000" ...>
      <fromLinkEndPt link="1" pos="335.021..." />
      <geometry><linkPolyPts>...</linkPolyPts></geometry>
      <lanes>
        <lane markingType="1">
          <fromLanes><objectRef key="1 1" /></fromLanes>
          <toLanes><objectRef key="2 1" /></toLanes>
        </lane>
        ...
      </lanes>
      <toLinkEndPt link="2" pos="0.109..." />
    </link>

``fromLinkEndPt``/``toLinkEndPt`` in a REAL export carry a plain ``link``
attribute (not the ``lane="<link> <lane>"`` token form
``services.inpx_structure``'s own comment says the hand-authored
``data/practice.inpx`` fixture uses) -- confirms that module's own existing
dual-form handling is exercising a real distinction, not a hypothetical one.

Coordinate space: ``linkPolyPoint``'s ``x``/``y`` are stored in the SAME
units as every other length-typed .inpx attribute this codebase has already
found (always METRES in the saved XML, display-unit-scaled only via COM
``AttValue``/``SetAttValue`` -- e.g. a FEET-display network's real lane
``width="3.66"`` is exactly 12 ft in metres, not "12"). This probe's own
[1b] phase live-confirms whether the ``AddLink``/``AddConnector`` COM call
takes ``x``/``y`` in metres directly or display-unit-scaled like ``Pos``/
``Length`` already are.

Usage: python tools/probe_add_links_connectors.py [example_dir]
Default example: Urban Intersection Beijing.CN (2026 Examples Demo) -- a
METERS-display network already used by unit (4)'s own probe, so the
display-unit question is isolated to a SEPARATE, explicit Camden (FEET)
cross-check in [1c].
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
DEFAULT_EXAMPLE = EXAMPLES_ROOT / "Urban Intersection Beijing.CN"
FEET_EXAMPLE = EXAMPLES_ROOT / "Toll Plaza Camden.US"
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "add-links-connectors-probe"

_M_PER_FT = 0.3048


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
# Phase 0: static XML schema re-confirmation
# ---------------------------------------------------------------------------


def probe_static_xml() -> None:
    plain_links = 0
    connectors = 0
    geometry_tags: set[str] = set()
    endpoint_forms: set[str] = set()
    for inpx_path in sorted(EXAMPLES_ROOT.glob("*/*.inpx"))[:8]:
        try:
            root = ET.parse(inpx_path).getroot()
        except ET.ParseError:
            continue
        for link in root.findall(".//links/link"):
            geom = link.find("geometry")
            if geom is not None:
                for child in geom:
                    geometry_tags.add(child.tag)
            fr = link.find("fromLinkEndPt")
            to = link.find("toLinkEndPt")
            if fr is None and to is None:
                plain_links += 1
            else:
                connectors += 1
                for el in (fr, to):
                    if el is None:
                        continue
                    form = "link+pos" if el.get("link") else ("lane+pos" if el.get("lane") else "?")
                    endpoint_forms.add(form)
    _log(f"      plain links={plain_links} connectors={connectors} (first 8 example networks)")
    _log(f"      <geometry> child tags observed: {sorted(geometry_tags)}")
    _log(f"      connector endpoint attribute forms observed: {sorted(endpoint_forms)}")

    # One full connector dump, with its own lane fromLanes/toLanes mapping.
    root = ET.parse(next(DEFAULT_EXAMPLE.glob("*.inpx"))).getroot()
    for link in root.findall(".//links/link"):
        if link.find("fromLinkEndPt") is not None:
            _log(f"      sample connector XML:\n{ET.tostring(link, encoding='unicode')[:1400]}")
            break
    for link in root.findall(".//links/link"):
        if link.find("fromLinkEndPt") is None:
            _log(f"      sample plain-link XML:\n{ET.tostring(link, encoding='unicode')[:900]}")
            break


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

    # Broad sweep: any method literally named Add*/Remove* whose name
    # contains "Link" or "Connector" or "Lane", regardless of which
    # interface hosts it -- avoids guessing the interface name up front
    # (unlike unit (4)'s probe, which already knew ISignalHeadContainer/
    # IDetectorContainer existed).
    keywords = ("Link", "Connector", "Lane")
    class_name = None
    in_class = False
    i = 0
    hits: list[tuple[str, str]] = []
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("class I") and "(DispatchBaseClass)" in stripped:
            class_name = stripped.split("(")[0].removeprefix("class ").strip()
            in_class = True
            i += 1
            continue
        if in_class and stripped.startswith("def ") and (
            stripped.startswith("def Add") or stripped.startswith("def Remove")
        ):
            name_part = stripped[len("def "):].split("(")[0]
            if any(kw in name_part for kw in keywords):
                sig = stripped
                j = i + 1
                while j < len(lines) and ")" not in sig.split("(", 1)[-1]:
                    sig += " " + lines[j].strip()
                    j += 1
                    if j - i > 4:
                        break
                hits.append((class_name, sig))
                i = j
                continue
        i += 1

    _log(f"\n      Every Add*/Remove* method whose name contains Link/Connector/Lane "
         f"(across ALL interfaces, {len(hits)} found):")
    for cname, sig in hits:
        _log(f"        [{cname}] {sig}")


# ---------------------------------------------------------------------------
# Phase 1b: live probe against a real example-network clone
# ---------------------------------------------------------------------------


def _dump_links(root: ET.Element) -> dict[str, ET.Element]:
    return {el.get("no", ""): el for el in root.findall(".//links/link")}


def _run_live_probe(example_dir: Path, *, do_connector_and_geometry: bool = True) -> None:
    work_dir = _fresh_work_dir(example_dir.name)
    shutil.copytree(example_dir, work_dir)
    inpx_files = sorted(work_dir.glob("*.inpx"))
    if not inpx_files:
        raise SystemExit(f"No .inpx in {work_dir}")
    working = inpx_files[0]
    _log(f"[1b.0] Working copy: {working}")

    xml_before = _dump_links(ET.parse(working).getroot())
    _log(f"       XML ground truth: link count={len(xml_before)}")

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

        unit_len = str(net.NetPara.AttValue("UnitLenShort") or "").strip().upper()
        _log(f"       Net.NetPara.AttValue('UnitLenShort') = {unit_len!r}")

        links = list(net.Links.GetAll())
        existing_nos = sorted(int(lk.AttValue("No")) for lk in links)
        new_link_no = existing_nos[-1] + 1 if existing_nos else 1
        _log(f"       {len(links)} existing links; using new_link_no={new_link_no}")

        # -------------------------------------------------------------
        # [1b.3] AddLink signature discovery -- the type-lib scan [1a]
        # already found the REAL signature: ILinkContainer.AddLink(Key,
        # WktLinestring, LaneWidths) -- try the WKT form directly, plus a
        # couple of plausible LaneWidths shapes.
        # -------------------------------------------------------------
        _log("\n[1b.3] AddLink signature discovery (WKT form, per [1a]'s type-lib finding):")
        test_points_xy = [(1000.0, 1000.0), (1050.0, 1000.0)]
        wkt = "LINESTRING (" + ", ".join(f"{x} {y}" for x, y in test_points_xy) + ")"
        _log(f"        WKT: {wkt!r}")
        new_link = None

        for label, lane_widths in [
            ("LaneWidths=[3.5, 3.5] (2 lanes)", [3.5, 3.5]),
            ("LaneWidths=(3.5, 3.5) tuple", (3.5, 3.5)),
            ("LaneWidths=3.5 (bare scalar)", 3.5),
            ("LaneWidths=None", None),
        ]:
            try:
                new_link = net.Links.AddLink(new_link_no, wkt, lane_widths)
                _log(f"        [WORKS with {label}] AddLink(No, wkt, LaneWidths) -> "
                     f"No={new_link.AttValue('No')}")
                break
            except Exception as exc:
                _log(f"        AddLink(No, wkt, {label}) -> {type(exc).__name__}: {exc}")

        if new_link is None:
            # Fall back: 2-arg form (no LaneWidths at all -- maybe it's
            # truly optional and Vissim defaults to 1 lane).
            try:
                new_link = net.Links.AddLink(new_link_no, wkt)
                _log(f"        [WORKS] AddLink(No, wkt) 2-arg -> No={new_link.AttValue('No')}")
            except Exception as exc:
                _log(f"        AddLink(No, wkt) 2-arg -> {type(exc).__name__}: {exc}")

        if new_link is None:
            _log("        [FAILED] no AddLink signature worked -- see the report's own writeup.")
        else:
            _log("\n[1b.4] New link attribute discovery (lane count, geometry, behavior/display type):")
            for attr_id in [
                "No", "Name", "NumLanes", "LinkBehavType", "DisplayType", "Length2D",
            ]:
                try:
                    val = new_link.AttValue(attr_id)
                    _log(f"        AttValue({attr_id!r}) = {val!r}")
                except Exception as exc:
                    _log(f"        [no]    AttValue({attr_id!r}) -> {type(exc).__name__}: {exc}")
            try:
                lane_items = list(new_link.Lanes.GetAll())
                _log(f"        Lanes.GetAll() count = {len(lane_items)} "
                     f"(does AddLink create lanes atomically, or does it start with a default "
                     f"count needing AddLane/RemoveLane -- unit (1)'s own precedent -- to reach "
                     f"the caller's requested lane_count?)")
            except Exception as exc:
                _log(f"        [no]    .Lanes.GetAll() -> {type(exc).__name__}: {exc}")

            # Try to set/observe LinkBehavType and DisplayType.
            for attr_id, value in [("LinkBehavType", 1), ("DisplayType", 1)]:
                try:
                    new_link.SetAttValue(attr_id, value)
                    _log(f"        [WORKS] SetAttValue({attr_id!r}, {value!r}) -> "
                         f"readback={new_link.AttValue(attr_id)!r}")
                except Exception as exc:
                    _log(f"        [no]    SetAttValue({attr_id!r}, {value!r}) -> "
                         f"{type(exc).__name__}: {exc}")

            # AddLane / RemoveLane on the new link's own Lanes container --
            # unit (1)'s own precedent (services/inpx_structure.py's proof
            # pair worked at the XML level; does the SAME operation exist
            # live over COM too?).
            try:
                before_n = len(list(new_link.Lanes.GetAll()))
                next_index = before_n + 1
                added = new_link.Lanes.AddLane(next_index, 3.5)
                after_n = len(list(new_link.Lanes.GetAll()))
                _log(f"        [WORKS] Lanes.AddLane({next_index}, 3.5) -- count {before_n} -> "
                     f"{after_n}, new lane Index={added.AttValue('Index') if added else '?'}")
            except Exception as exc:
                _log(f"        [no]    Lanes.AddLane(Key, Width) -> {type(exc).__name__}: {exc}")

        # -------------------------------------------------------------
        # [1b.5] Save + raw XML dump of the new link.
        # -------------------------------------------------------------
        _log("\n[1b.5] Save + raw XML dump of the newly-added link:")
        save_path = work_dir / "after_add_link.inpx"
        controller.save_net_as(str(save_path))
        saved_root = ET.parse(save_path).getroot()
        xml_after = _dump_links(saved_root)
        new_no_str = str(new_link_no)
        el = xml_after.get(new_no_str)
        if el is None:
            _log(f"        Link no={new_no_str}: NOT FOUND in saved XML!")
        else:
            _log(f"        Link no={new_no_str} saved XML:\n"
                 f"{ET.tostring(el, encoding='unicode')[:1600]}")
            geom = el.find("geometry")
            if geom is not None:
                pts = geom.findall(".//linkPolyPoint")
                _log(f"        Saved geometry points: "
                     f"{[(p.get('x'), p.get('y')) for p in pts]} (compare against requested "
                     f"{test_points_xy} -- same units, or display-unit-scaled?)")

        # -------------------------------------------------------------
        # [1b.6] AddConnector signature discovery (only if the link add
        # itself worked -- a connector needs two real links to join).
        # -------------------------------------------------------------
        new_connector = None
        if do_connector_and_geometry and new_link is not None and len(links) >= 1:
            _log("\n[1b.6] AddConnector signature discovery:")
            from_link_no = int(links[0].AttValue("No"))
            to_link_no = new_link_no
            from_link_obj = links[0]
            to_link_obj = new_link
            existing_conn_nos = sorted(
                int(lk.AttValue("No")) for lk in net.Links.GetAll()
            )
            new_conn_no = existing_conn_nos[-1] + 1 if existing_conn_nos else 1

            # [1a]'s type-lib finding: ILinkContainer.AddConnector(Key,
            # FromLane, FromPos, ToLane, ToPos, NumberOfLanes, WktLinestring)
            # -- FromLane/ToLane are most likely real Lane COM objects (the
            # SAME _lane_by_index-shaped resolution every other Add* method
            # in this codebase already needs), each link's lane 1 as the
            # anchor, connecting lane-for-lane upward. Try the Lane-object
            # form first (this codebase's own "prefer the object form"
            # precedent), a bare link-object/int fallback second.
            from_lane_obj = from_link_obj.Lanes.GetAll()[0]
            to_lane_obj = to_link_obj.Lanes.GetAll()[0]
            from_link_len = float(from_link_obj.AttValue("Length2D") or 0.0)
            conn_wkt = "LINESTRING (-429.701 -502.22, 1000 1001.75)"
            attempts = [
                (
                    f"AddConnector(No, FromLane<obj>, FromPos, ToLane<obj>, ToPos, "
                    f"NumLanes, WktLinestring={conn_wkt!r})",
                    lambda: net.Links.AddConnector(
                        new_conn_no, from_lane_obj, from_link_len, to_lane_obj, 0.0, 1, conn_wkt
                    ),
                ),
                (
                    "AddConnector(No, FromLane<obj>, FromPos, ToLane<obj>, ToPos, NumLanes, "
                    "WktLinestring='')",
                    lambda: net.Links.AddConnector(
                        new_conn_no, from_lane_obj, from_link_len, to_lane_obj, 0.0, 1, ""
                    ),
                ),
                (
                    "AddConnector(No, FromLane<obj>, FromPos, ToLane<obj>, ToPos, NumLanes) 6-arg",
                    lambda: net.Links.AddConnector(
                        new_conn_no, from_lane_obj, from_link_len, to_lane_obj, 0.0, 1
                    ),
                ),
                (
                    "AddConnector(No, FromLink<obj>, FromPos, ToLink<obj>, ToPos, NumLanes)",
                    lambda: net.Links.AddConnector(
                        new_conn_no, from_link_obj, from_link_len, to_link_obj, 0.0, 1
                    ),
                ),
                (
                    "AddConnector(No, FromLinkNo, FromPos, ToLinkNo, ToPos, NumLanes)",
                    lambda: net.Links.AddConnector(
                        new_conn_no, from_link_no, from_link_len, to_link_no, 0.0, 1
                    ),
                ),
            ]
            for label, thunk in attempts:
                try:
                    new_connector = thunk()
                    _log(f"        [WORKS] {label} -> No={new_connector.AttValue('No')}")
                    break
                except Exception as exc:
                    _log(f"        {label} -> {type(exc).__name__}: {exc}")

            if new_connector is None:
                _log("        [FAILED] no AddConnector signature worked -- see the report's own writeup.")
            else:
                _log("\n[1b.7] New connector attribute + lane-mapping discovery:")
                for attr_id in ["No", "NumLanes", "FromLink", "ToLink"]:
                    try:
                        _log(f"        AttValue({attr_id!r}) = {new_connector.AttValue(attr_id)!r}")
                    except Exception as exc:
                        _log(f"        [no]    AttValue({attr_id!r}) -> {type(exc).__name__}: {exc}")
                try:
                    from_pt = new_connector.FromLinkEndPt
                    _log(f"        .FromLinkEndPt -> {from_pt}, Link={from_pt.AttValue('Link')}, "
                         f"Pos={from_pt.AttValue('Pos')}")
                except Exception as exc:
                    _log(f"        [no]    .FromLinkEndPt -> {type(exc).__name__}: {exc}")
                try:
                    conn_lanes = list(new_connector.Lanes.GetAll())
                    _log(f"        Lanes.GetAll() count = {len(conn_lanes)}")
                    for lane in conn_lanes:
                        try:
                            from_lanes = list(lane.FromLanes.GetAll())
                            to_lanes = list(lane.ToLanes.GetAll())
                            _log(f"          lane Index={lane.AttValue('Index')}: "
                                 f"FromLanes={[l.AttValue('Link') if hasattr(l, 'AttValue') else l for l in from_lanes]} "
                                 f"ToLanes={[l.AttValue('Link') if hasattr(l, 'AttValue') else l for l in to_lanes]}")
                        except Exception as exc:
                            _log(f"          lane fromLanes/toLanes -> {type(exc).__name__}: {exc}")
                except Exception as exc:
                    _log(f"        [no]    connector .Lanes.GetAll() -> {type(exc).__name__}: {exc}")

                save_path2 = work_dir / "after_add_connector.inpx"
                controller.save_net_as(str(save_path2))
                saved_root2 = ET.parse(save_path2).getroot()
                conn_el = _dump_links(saved_root2).get(str(new_connector.AttValue("No")))
                if conn_el is not None:
                    _log(f"\n        Saved connector XML:\n"
                         f"{ET.tostring(conn_el, encoding='unicode')[:1800]}")

        # -------------------------------------------------------------
        # [1b.8] Remove-method roundtrip. [1a]'s type-lib scan found
        # ILinkContainer.RemoveLink(Link) -- CONTRARY to unit (3)'s own
        # "no native COM Add/Remove for a plain link or connector" note
        # (that unit never scanned ILinkContainer itself, only the
        # Lane-container/closure-shaped surface) -- so this phase actually
        # exercises it, on BOTH the connector (removed first, since it
        # references the link) and the plain link.
        # -------------------------------------------------------------
        _log("\n[1b.8] RemoveLink roundtrip (Net.Links.RemoveLink exists per [1a] -- exercising it "
             "live, contrary to unit (3)'s own 'no native remove' note for a PLAIN link/connector):")
        for method_name in ["RemoveLink", "RemoveConnector"]:
            has_method = hasattr(net.Links, method_name)
            _log(f"        Net.Links has attribute {method_name!r}: {has_method}")
        if new_connector is not None:
            try:
                before = len(list(net.Links.GetAll()))
                net.Links.RemoveLink(new_connector)
                after = len(list(net.Links.GetAll()))
                _log(f"        [WORKS] Net.Links.RemoveLink(<connector>) -- count {before} -> {after}")
            except Exception as exc:
                _log(f"        Net.Links.RemoveLink(<connector>) -> {type(exc).__name__}: {exc}")
        if new_link is not None:
            try:
                before = len(list(net.Links.GetAll()))
                net.Links.RemoveLink(new_link)
                after = len(list(net.Links.GetAll()))
                _log(f"        [WORKS] Net.Links.RemoveLink(<plain link>) -- count {before} -> {after}")
            except Exception as exc:
                _log(f"        Net.Links.RemoveLink(<plain link>) -> {type(exc).__name__}: {exc}")
                # Control check: does it refuse when the link is still
                # referenced (e.g. by the just-created connector, if that
                # removal above itself failed)?
                _log(f"        (this may be Vissim itself REFUSING because something still "
                     f"references link {new_link_no} -- a genuine reference-integrity signal, "
                     f"not necessarily a missing-method failure)")

        _log("\n" + "#" * 78)
        _log("# SUMMARY (see report for the full write-up)")
        _log("#" * 78)
        _log(f"NetPara.UnitLenShort on {example_dir.name} = {unit_len!r}")

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
    _run_live_probe(example_dir)


if __name__ == "__main__":
    main()

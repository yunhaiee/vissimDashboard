"""Q4 unit (2) PROBE-FIRST EMPIRICAL PROBE: measurement-infrastructure
placement -- queue counters, travel-time measurement sections, and
data-collection points/measurements. House shape = ``probe_lane_closure.py``
(the newest v2b probe): static XML re-scan -> static type-library scan (no
COM instance) -> live COM probe on a real example-network clone.

**STATIC PRE-SCAN (already run before this file existed, cheap ElementTree
grep across all 20 real Vissim 2026 Examples Demo networks -- findings
pasted here for the record, re-confirmed live by :func:`probe_static_xml`
below):**

Every real ``.inpx`` that uses these features shows the SAME shapes, with
zero variance across 15 networks that carry at least one instance:

```
<queueCounters>
  <queueCounter link="10043" name="" no="1" pos="10.31" />
</queueCounters>

<dataCollectionPoints>
  <dataCollectionPoint lane="10005 1" name="" no="1" pos="3.836" />
</dataCollectionPoints>

<dataCollectionMeasurements>
  <dataCollectionMeasurement name="" no="1">
    <dataCollectionPoints>
      <intObjectRef key="1" />
      <intObjectRef key="2" />
      <intObjectRef key="3" />
    </dataCollectionPoints>
  </dataCollectionMeasurement>
</dataCollectionMeasurements>

<vehicleTravelTimeMeasurements>
  <vehicleTravelTimeMeasurement name="" no="4">
    <end link="29" pos="66.753" />
    <start link="11" pos="8.748" />
  </vehicleTravelTimeMeasurement>
</vehicleTravelTimeMeasurements>
```

This CONFIRMS the FIRST of ``inpx_parser._resolve_tt_endpoint``'s three
guessed shapes for travel-time measurements (child ``<start>``/``<end>``
elements with ``link``/``pos`` attributes) as the real one -- the other two
guesses (flat ``startLink``/``startPos`` attrs, combined ``"LINK POS"``
strings) are never used by any real Vissim export. Queue counters are
LINK+POS only (no lane -- matches ``inpx_parser._parse_link_pos_elements``'s
existing assumption exactly). Data collection points are LANE-TOKEN+POS
(``"<link> <lane>"``, the SAME token convention as ``reducedSpeedArea``/
``priorityRule`` -- matches ``_parse_lane_pos_elements`` exactly). A
``dataCollectionMeasurement`` is a SEPARATE, referencing object: it groups
1..N data-collection points by id via ``<intObjectRef key="...">`` children
-- the SAME ``intObjectRef`` convention ``_parse_link_lanes`` already found
for ``lane/blockedVehClasses`` (v2b unit (1)). A lone ``dataCollectionPoint``
with no measurement produces no queryable result in Vissim's own evaluation
UI -- the point is a placement marker, the MEASUREMENT is what accumulates
counts. None of these four element types carry a ``TimeFrom``/``TimeTo``
window -- unlike ``reducedSpeedArea``, they are always active for the whole
simulation once placed.

**PHASE 1a static type-library scan finding (this probe's own, confirmed
before any COM instance was launched):** unlike lane closure (v2b unit (1),
which found ZERO native Add/Remove surface and had to fall back to XML
injection) and unlike conflict areas (documented COM-less, geometry-derived
in DECISIONS.md), all three of THIS unit's object types have genuine,
purpose-built COM creation methods on their ``Net`` containers:

- ``IQueueCounterContainer.AddQueueCounter(Key, Link, Pos)`` /
  ``RemoveQueueCounter(QueueCounter)``
- ``IDataCollectionPointContainer.AddDataCollectionPoint(Key, Lane, Pos)`` /
  ``RemoveDataCollectionPoint(DataCollectionPoint)``
- ``IDataCollectionMeasurementContainer.AddDataCollectionMeasurement(Key)`` /
  ``RemoveDataCollectionMeasurement(DataCollectionMeasurement)`` -- no
  points param; membership is wired separately (this probe's [1b.7] finds
  out how -- ``IDataCollectionMeasurement.DataCollectionPoints`` is a
  READ-ONLY ``IDataCollectionPointCollection`` property, no Add method of
  its own, so membership must be set some other way -- tested live below).
- ``IVehicleTravelTimeMeasurementContainer.AddVehicleTravelTimeMeasurement(
  Key, StartLink, StartPos, EndLink, EndPos)`` /
  ``RemoveVehicleTravelTimeMeasurement(VehicleTravelTimeMeasurement)`` --
  flat COM params even though the XML shape nests ``<start>``/``<end>``
  children; Vissim itself handles the (de)nesting.

**THE DISPLAY-UNIT LANDMINE (twice-documented already --
docs/phase2/DECISIONS.md 2026-07-27 v2a unit (8) found ``AttValue('Pos')``
on an imperial-display network reads ~3.28x the XML value; 2026-07-31 Q3
unit (6) found ``NetPara.UnitLenShort`` -- not ``UnitLength`` -- drives
EVERY length-typed COM attribute, verified both directions). This probe's
job is to determine whether that landmine ALSO applies to the ``Pos``
param of the ADD METHODS above (untested territory -- every prior length
landmine finding was about ``AttValue``/``SetAttValue`` on an EXISTING
object, never a constructor-style Add call) by round-tripping a known
value through ``AddQueueCounter``/``AddDataCollectionPoint``/
``AddVehicleTravelTimeMeasurement`` on an IMPERIAL-display network (Toll
Plaza Camden.US, ``unitLenShort="FEET"`` -- also chosen because it is the
only one of the 20 examples that carries all four element types with real
data, letting one clone exercise every readback cross-check) and comparing
the SAVED XML's ``pos``/``length`` attribute against what was passed in.

Usage: python tools/probe_measurement_infra.py [example_dir]
Default example: Toll Plaza Camden.US (2026 Examples Demo) -- FEET display,
has real queueCounter/dataCollectionPoint/dataCollectionMeasurement/
vehicleTravelTimeMeasurement instances to cross-check attribute discovery
against known XML ground truth.
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

EXAMPLES_ROOT = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo"
)
DEFAULT_EXAMPLE = EXAMPLES_ROOT / "Toll Plaza Camden.US"
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "measurement-infra-probe"

_M_PER_FT = 0.3048

_ELEMENT_XPATHS = {
    "queueCounter": ".//queueCounters/queueCounter",
    "dataCollectionPoint": ".//dataCollectionPoints/dataCollectionPoint",
    "dataCollectionMeasurement": ".//dataCollectionMeasurements/dataCollectionMeasurement",
    "vehicleTravelTimeMeasurement": ".//vehicleTravelTimeMeasurements/vehicleTravelTimeMeasurement",
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
# Phase 0: static XML schema re-confirmation (this run, not just pasted
# findings) -- re-grep every example network for the four element types.
# ---------------------------------------------------------------------------


def probe_static_xml() -> None:
    hits: dict[str, int] = {k: 0 for k in _ELEMENT_XPATHS}
    networks_with_any = 0
    for inpx_path in sorted(EXAMPLES_ROOT.glob("*/*.inpx")):
        try:
            root = ET.parse(inpx_path).getroot()
        except ET.ParseError:
            continue
        any_hit = False
        for tag, xpath in _ELEMENT_XPATHS.items():
            count = len(root.findall(xpath))
            if count:
                hits[tag] += count
                any_hit = True
        if any_hit:
            networks_with_any += 1
    _log(f"      Scanned {len(list(EXAMPLES_ROOT.glob('*/*.inpx')))} example .inpx files.")
    _log(f"      Networks carrying >=1 of these element types: {networks_with_any}")
    _log(f"      Total element counts across all examples: {hits}")

    # Full attribute-name union + one full-element dump per type, from the
    # DEFAULT_EXAMPLE (Camden, has all four types).
    root = ET.parse(next(DEFAULT_EXAMPLE.glob("*.inpx"))).getroot()
    for tag, xpath in _ELEMENT_XPATHS.items():
        els = root.findall(xpath)
        if not els:
            _log(f"      {tag}: none in {DEFAULT_EXAMPLE.name}")
            continue
        attrs: set[str] = set()
        child_tags: set[str] = set()
        for el in els:
            attrs.update(el.attrib.keys())
            child_tags.update(c.tag for c in el)
        _log(f"      {tag} (n={len(els)}): attrs={sorted(attrs)} children={sorted(child_tags)}")
        _log(f"        first element: {ET.tostring(els[0], encoding='unicode').strip()}")


# ---------------------------------------------------------------------------
# Phase 1a: static type-library scan (no COM instance required)
# ---------------------------------------------------------------------------


def probe_typelib() -> dict[str, list[str]]:
    """Dump every Add/Remove-shaped method on the four containers, and
    return the raw method-signature lines keyed by container name (used by
    the report; also lets a human sanity-check the exact param order/names
    without re-running this probe)."""
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

    targets = {
        "IQueueCounterContainer": [],
        "IDataCollectionPointContainer": [],
        "IDataCollectionMeasurementContainer": [],
        "IVehicleTravelTimeMeasurementContainer": [],
    }
    class_name = None
    in_target = False
    buf: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("class I") and "(DispatchBaseClass)" in stripped:
            class_name = stripped.split("(")[0].removeprefix("class ").strip()
            in_target = class_name in targets
            i += 1
            continue
        if in_target and stripped.startswith("def ") and ("Add" in stripped or "Remove" in stripped):
            sig = stripped
            # Signatures with many params wrap onto a continuation line
            # ending in a bare ", Name=...):" -- fold it back onto one line
            # for readability.
            j = i + 1
            while j < len(lines) and not sig.rstrip().endswith("):") and ")" not in sig.split("(", 1)[-1]:
                sig += " " + lines[j].strip()
                j += 1
                if j - i > 3:
                    break
            targets[class_name].append(sig)
            i = j
            continue
        i += 1

    for name, sigs in targets.items():
        _log(f"\n      -- {name} --")
        for sig in sigs:
            _log(f"        {sig}")
    return targets


# ---------------------------------------------------------------------------
# Phase 1b: live probe against a Toll Plaza Camden.US clone
# ---------------------------------------------------------------------------


def _dump_xml_snapshot(root: ET.Element) -> dict[str, dict[str, ET.Element]]:
    """``{tag: {no: element}}`` for all four types, from a freshly-parsed
    root -- used both for the pre-add ground truth and the post-add/
    post-remove re-check."""
    snapshot: dict[str, dict[str, ET.Element]] = {}
    for tag, xpath in _ELEMENT_XPATHS.items():
        snapshot[tag] = {el.get("no", ""): el for el in root.findall(xpath)}
    return snapshot


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

    xml_before = _dump_xml_snapshot(ET.parse(working).getroot())
    for tag, items in xml_before.items():
        _log(f"       XML ground truth: {tag} count={len(items)}")

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
        _log(f"       Net.NetPara.AttValue('UnitLenShort') = {unit_len!r} (expect FEET for Camden)")

        _log("\n[1b.3] Net-level collection existence + GetAll() count cross-check vs XML:")
        collections: dict[str, Any] = {}
        for name in ["QueueCounters", "DataCollectionPoints", "DataCollectionMeasurements",
                     "VehicleTravelTimeMeasurements"]:
            try:
                obj = getattr(net, name)
                items = list(obj.GetAll())
                collections[name] = obj
                _log(f"        [WORKS] Net.{name} -> GetAll() count = {len(items)}")
            except Exception as exc:
                _log(f"        [no]    Net.{name} -> {type(exc).__name__}: {exc}")

        # ---------------------------------------------------------------
        # [1b.4] Attribute discovery on a real, existing object of each
        # type -- cross-checked against the XML ground truth dumped above.
        # ---------------------------------------------------------------
        _log("\n[1b.4] Attribute discovery on a real existing object per type "
              "(cross-checked against XML ground truth):")

        qc_items = list(collections["QueueCounters"].GetAll()) if "QueueCounters" in collections else []
        if qc_items:
            qc = qc_items[0]
            qc_no = str(qc.AttValue("No"))
            xml_qc = xml_before["queueCounter"].get(qc_no)
            _log(f"        QueueCounter No={qc_no} Name={qc.AttValue('Name')!r} "
                 f"Link={qc.AttValue('Link')!r} Pos={qc.AttValue('Pos')!r}")
            if xml_qc is not None:
                xml_pos = float(xml_qc.get("pos"))
                com_pos = float(qc.AttValue("Pos"))
                ratio = com_pos / xml_pos if xml_pos else float("nan")
                _log(f"        XML ground truth: link={xml_qc.get('link')} pos={xml_pos} "
                     f"-- COM/XML pos ratio = {ratio:.6f} (expect ~{1/_M_PER_FT:.6f} if COM reads FEET)")

        dcp_items = list(collections["DataCollectionPoints"].GetAll()) if "DataCollectionPoints" in collections else []
        if dcp_items:
            dcp = dcp_items[0]
            dcp_no = str(dcp.AttValue("No"))
            xml_dcp = xml_before["dataCollectionPoint"].get(dcp_no)
            _log(f"        DataCollectionPoint No={dcp_no} Name={dcp.AttValue('Name')!r} "
                 f"Lane={dcp.AttValue('Lane')!r} Pos={dcp.AttValue('Pos')!r}")
            if xml_dcp is not None:
                xml_pos = float(xml_dcp.get("pos"))
                com_pos = float(dcp.AttValue("Pos"))
                ratio = com_pos / xml_pos if xml_pos else float("nan")
                _log(f"        XML ground truth: lane={xml_dcp.get('lane')!r} pos={xml_pos} "
                     f"-- COM/XML pos ratio = {ratio:.6f}")

        dcm_items = list(collections["DataCollectionMeasurements"].GetAll()) if "DataCollectionMeasurements" in collections else []
        if dcm_items:
            dcm = dcm_items[0]
            dcm_no = str(dcm.AttValue("No"))
            xml_dcm = xml_before["dataCollectionMeasurement"].get(dcm_no)
            xml_point_nos = sorted(
                ref.get("key") for ref in xml_dcm.findall("dataCollectionPoints/intObjectRef")
            ) if xml_dcm is not None else []
            _log(f"        DataCollectionMeasurement No={dcm_no} Name={dcm.AttValue('Name')!r}")
            _log(f"        XML ground truth point nos: {xml_point_nos}")
            try:
                sub_points = list(dcm.DataCollectionPoints.GetAll())
                sub_nos = sorted(str(p.AttValue("No")) for p in sub_points)
                _log(f"        [WORKS] DataCollectionMeasurement.DataCollectionPoints.GetAll() "
                     f"-> point nos = {sub_nos} (matches XML: {sub_nos == xml_point_nos})")
            except Exception as exc:
                _log(f"        [no]    .DataCollectionPoints sub-collection -> {type(exc).__name__}: {exc}")
            # Is DataCollectionPoints ALSO addressable as a plain AttValue
            # (mirrors the blockedVehClasses/DataCollectionPoints XML
            # <intObjectRef> convention)?
            try:
                val = dcm.AttValue("DataCollectionPoints")
                _log(f"        AttValue('DataCollectionPoints') = {val!r}")
            except Exception as exc:
                _log(f"        [no]    AttValue('DataCollectionPoints') -> {type(exc).__name__}: {exc}")

        tt_items = list(collections["VehicleTravelTimeMeasurements"].GetAll()) if "VehicleTravelTimeMeasurements" in collections else []
        if tt_items:
            tt = tt_items[0]
            tt_no = str(tt.AttValue("No"))
            xml_tt = xml_before["vehicleTravelTimeMeasurement"].get(tt_no)
            # Try both a nested Start/End object and flat StartLink/StartPos
            # attribute ids -- mirrors inpx_parser._resolve_tt_endpoint's own
            # three-hypothesis approach, but live this time.
            for attr_id in ["StartLink", "StartPos", "EndLink", "EndPos"]:
                try:
                    val = tt.AttValue(attr_id)
                    _log(f"        TravelTimeMeasurement No={tt_no} AttValue({attr_id!r}) = {val!r}")
                except Exception as exc:
                    _log(f"        [no]    AttValue({attr_id!r}) -> {type(exc).__name__}: {exc}")
            for nested_name in ["Start", "End"]:
                try:
                    nested = getattr(tt, nested_name)
                    _log(f"        [WORKS] .{nested_name} -> nested object, "
                         f"Link={nested.AttValue('Link')!r} Pos={nested.AttValue('Pos')!r}")
                except Exception as exc:
                    _log(f"        [no]    .{nested_name} -> {type(exc).__name__}: {exc}")
            if xml_tt is not None:
                start_el = xml_tt.find("start")
                end_el = xml_tt.find("end")
                _log(f"        XML ground truth: start link={start_el.get('link')} pos={start_el.get('pos')}, "
                     f"end link={end_el.get('link')} pos={end_el.get('pos')}")
                try:
                    com_start_pos = float(tt.AttValue("StartPos"))
                    xml_start_pos = float(start_el.get("pos"))
                    ratio = com_start_pos / xml_start_pos if xml_start_pos else float("nan")
                    _log(f"        COM/XML StartPos ratio = {ratio:.6f}")
                except Exception:
                    pass

        # ---------------------------------------------------------------
        # [1b.5] Bogus attribute id control check (fail-open/closed sanity).
        # ---------------------------------------------------------------
        _log("\n[1b.5] Control check: bogus attribute id (fail-open/closed sanity):")
        if qc_items:
            try:
                qc_items[0].AttValue("ThisAttributeDoesNotExistProbe12345")
                _log("        [UNEXPECTED] bogus attribute id did NOT raise -- fail-open!")
            except Exception as exc:
                _log(f"        [expected] bogus attribute id raised {type(exc).__name__}: {exc}")

        # ---------------------------------------------------------------
        # [1b.6] Add-method live tests: create one of each type, inspect the
        # resulting object + SaveNetAs XML dump for exact shape/units, then
        # Remove it and confirm the count returns to baseline. A real link
        # (with >=1 lane) is picked for Link/Lane params.
        # ---------------------------------------------------------------
        _log("\n[1b.6] Add-method live tests (create -> inspect -> SaveNetAs dump -> remove):")
        links = list(net.Links.GetAll())
        target_link = next((lk for lk in links if int(lk.AttValue("NumLanes") or 0) >= 1), links[0])
        target_link_no = int(target_link.AttValue("No"))
        link_length_m = float(target_link.AttValue("Length2D") or target_link.AttValue("Length") or 0)
        # Length2D/Length is ALSO a length-typed attribute -- read it back
        # once here so the saved XML comparison below has a sane pos value
        # regardless of which display unit governs it (probed at [1b.4]/
        # [1b.7] already; use a conservative small pos near the link start).
        _log(f"        Target link No={target_link_no}, NumLanes={target_link.AttValue('NumLanes')!r}, "
             f"length attr(s) read = {link_length_m!r}")

        test_pos_input = 20.0  # the value handed to each Add call's Pos/StartPos/EndPos param
        new_qc_no = None
        new_dcp_no = None
        new_dcm_no = None
        new_tt_no = None

        if "QueueCounters" in collections:
            try:
                qc_before = len(list(collections["QueueCounters"].GetAll()))
                new_qc = collections["QueueCounters"].AddQueueCounter(None, target_link_no, test_pos_input)
                new_qc_no = str(new_qc.AttValue("No"))
                qc_after = len(list(collections["QueueCounters"].GetAll()))
                _log(f"        [WORKS] AddQueueCounter(None, {target_link_no}, {test_pos_input}) "
                     f"-> No={new_qc_no}, count {qc_before} -> {qc_after}, "
                     f"AttValue('Pos') readback = {new_qc.AttValue('Pos')!r}")
            except Exception as exc:
                _log(f"        AddQueueCounter(None, {target_link_no}, {test_pos_input}) "
                     f"-> {type(exc).__name__}: {exc}")
                try:
                    new_qc = collections["QueueCounters"].AddQueueCounter(9999001, target_link_no, test_pos_input)
                    new_qc_no = str(new_qc.AttValue("No"))
                    _log(f"        [WORKS with explicit Key] AddQueueCounter(9999001, ...) -> No={new_qc_no}")
                except Exception as exc2:
                    _log(f"        AddQueueCounter(9999001, ...) -> {type(exc2).__name__}: {exc2}")

        if "DataCollectionPoints" in collections:
            lane_token = f"{target_link_no} 1"
            try:
                new_dcp = collections["DataCollectionPoints"].AddDataCollectionPoint(None, lane_token, test_pos_input)
                new_dcp_no = str(new_dcp.AttValue("No"))
                _log(f"        [WORKS] AddDataCollectionPoint(None, {lane_token!r}, {test_pos_input}) "
                     f"-> No={new_dcp_no}, AttValue('Lane')={new_dcp.AttValue('Lane')!r}, "
                     f"AttValue('Pos')={new_dcp.AttValue('Pos')!r}")
            except Exception as exc:
                _log(f"        AddDataCollectionPoint(None, {lane_token!r}, ...) -> {type(exc).__name__}: {exc}")
                # Try passing an actual Lane object instead of a token string.
                try:
                    lane_obj = target_link.Lanes.GetAll()[0]
                    new_dcp = collections["DataCollectionPoints"].AddDataCollectionPoint(None, lane_obj, test_pos_input)
                    new_dcp_no = str(new_dcp.AttValue("No"))
                    _log(f"        [WORKS with Lane object] AddDataCollectionPoint(None, <Lane obj>, ...) "
                         f"-> No={new_dcp_no}")
                except Exception as exc2:
                    _log(f"        AddDataCollectionPoint(None, <Lane obj>, ...) -> {type(exc2).__name__}: {exc2}")

        if "DataCollectionMeasurements" in collections and new_dcp_no is not None:
            try:
                new_dcm = collections["DataCollectionMeasurements"].AddDataCollectionMeasurement(None)
                new_dcm_no = str(new_dcm.AttValue("No"))
                _log(f"        [WORKS] AddDataCollectionMeasurement(None) -> No={new_dcm_no}")
                # Now try to wire the new point into the new measurement --
                # test every plausible mechanism, non-fatally.
                try:
                    new_dcm.SetAttValue("DataCollectionPoints", new_dcp_no)
                    _log(f"        [WORKS?] SetAttValue('DataCollectionPoints', {new_dcp_no!r}) "
                         f"did not raise -- readback: {list(str(p.AttValue('No')) for p in new_dcm.DataCollectionPoints.GetAll())}")
                except Exception as exc:
                    _log(f"        [no]    SetAttValue('DataCollectionPoints', {new_dcp_no!r}) "
                         f"-> {type(exc).__name__}: {exc}")
                try:
                    new_dcm.SetAttValue("DataCollectionPoints", f"[{new_dcp_no}]")
                    _log(f"        [WORKS?] SetAttValue('DataCollectionPoints', '[{new_dcp_no}]') did not raise")
                except Exception as exc:
                    _log(f"        [no]    SetAttValue('DataCollectionPoints', '[{new_dcp_no}]') "
                         f"-> {type(exc).__name__}: {exc}")
                # Existing measurement's own points collection -- does IT
                # expose an Add method distinct from the top-level container?
                try:
                    add_names = [n for n in dir(new_dcm.DataCollectionPoints) if n.lower().startswith("add")]
                    _log(f"        dir(<new measurement>.DataCollectionPoints) add-shaped names: {add_names}")
                except Exception as exc:
                    _log(f"        [no]    dir(.DataCollectionPoints) -> {type(exc).__name__}: {exc}")
            except Exception as exc:
                _log(f"        AddDataCollectionMeasurement(None) -> {type(exc).__name__}: {exc}")

        if "VehicleTravelTimeMeasurements" in collections:
            try:
                new_tt = collections["VehicleTravelTimeMeasurements"].AddVehicleTravelTimeMeasurement(
                    None, target_link_no, 0.0, target_link_no, test_pos_input
                )
                new_tt_no = str(new_tt.AttValue("No"))
                _log(f"        [WORKS] AddVehicleTravelTimeMeasurement(None, {target_link_no}, 0.0, "
                     f"{target_link_no}, {test_pos_input}) -> No={new_tt_no}, "
                     f"StartPos readback={new_tt.AttValue('StartPos')!r}, EndPos readback={new_tt.AttValue('EndPos')!r}")
            except Exception as exc:
                _log(f"        AddVehicleTravelTimeMeasurement(...) -> {type(exc).__name__}: {exc}")

        _log("\n[1b.7] Save + raw XML dump of every newly-added object -- "
             "THE units answer (compare saved pos/startPos/endPos against "
             f"the {test_pos_input} passed to each Add call):")
        save_path = work_dir / "after_adds.inpx"
        controller.save_net_as(str(save_path))
        saved_root = ET.parse(save_path).getroot()
        xml_after = _dump_xml_snapshot(saved_root)
        for label, tag, no in [
            ("QueueCounter", "queueCounter", new_qc_no),
            ("DataCollectionPoint", "dataCollectionPoint", new_dcp_no),
            ("DataCollectionMeasurement", "dataCollectionMeasurement", new_dcm_no),
            ("VehicleTravelTimeMeasurement", "vehicleTravelTimeMeasurement", new_tt_no),
        ]:
            if no is None:
                continue
            el = xml_after[tag].get(no)
            if el is None:
                _log(f"        {label} no={no}: NOT FOUND in saved XML!")
                continue
            _log(f"        {label} no={no} saved XML: {ET.tostring(el, encoding='unicode').strip()}")
            if tag != "vehicleTravelTimeMeasurement" and tag != "dataCollectionMeasurement":
                saved_pos = float(el.get("pos"))
                unit_ratio = test_pos_input / saved_pos if saved_pos else float("nan")
                _log(f"          input Pos={test_pos_input} vs saved pos={saved_pos} "
                     f"-> input/saved ratio = {unit_ratio:.6f} "
                     f"(1.0 = Add takes METRES already; ~{1/_M_PER_FT:.6f} = Add takes the display unit/FEET)")

        _log("\n[1b.8] Remove-method roundtrip (undo everything this probe added):")
        for label, coll_name, no_val, obj in [
            ("QueueCounter", "QueueCounters", new_qc_no, locals().get("new_qc")),
            ("DataCollectionMeasurement", "DataCollectionMeasurements", new_dcm_no, locals().get("new_dcm")),
            ("DataCollectionPoint", "DataCollectionPoints", new_dcp_no, locals().get("new_dcp")),
            ("VehicleTravelTimeMeasurement", "VehicleTravelTimeMeasurements", new_tt_no, locals().get("new_tt")),
        ]:
            if no_val is None or obj is None:
                continue
            try:
                remove_method = getattr(collections[coll_name], f"Remove{coll_name[:-1] if coll_name.endswith('s') else coll_name}")
            except Exception:
                remove_method = None
            try:
                before = len(list(collections[coll_name].GetAll()))
                getattr(collections[coll_name], f"Remove{label}")(obj)
                after = len(list(collections[coll_name].GetAll()))
                _log(f"        [WORKS] Remove{label}(<obj>) -- count {before} -> {after}")
            except Exception as exc:
                _log(f"        Remove{label}(<obj>) -> {type(exc).__name__}: {exc}")

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


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()

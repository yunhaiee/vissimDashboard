"""Q4 unit (4) PROBE-FIRST EMPIRICAL PROBE: signal-head + detector placement.

House shape = ``probe_measurement_infra.py`` (the newest Q4 probe): static
XML re-scan across every real 2026 Examples Demo network -> static COM
type-library scan (no COM instance, no licence used) -> live COM probe on a
real example-network clone (create -> inspect -> SaveNetAs dump -> remove).

**STATIC PRE-SCAN (already run before this file existed, via PowerShell
Select-String across all 25 real Vissim 2026 Examples Demo .inpx files --
:func:`probe_static_xml` below re-confirms it with ElementTree, this run):**

```
<signalHeads>
  <signalHead allPedTypes="true" allVehTypes="true" anmFlag="false"
    complRate="1" dischRecAct="false" isBlockSig="false" lane="13 1"
    localNo="8" name="06" no="2" pos="228.655..." scndSGTyp="ORSG"
    sg="1 21" slowDownDist="3" type="CIRCULAR" vAmberBlock="0"/>
</signalHeads>

<detectors>
  <detector allPedTypes="true" allVehTypes="true" anmFlag="false"
    lane="137 1" length="1" maxPedSpeed="3.6" name="" no="1" portNo="101"
    pos="1" sc="52" smthFactDecr="0.25" smthFactIncr="0.25" soundFile=""
    tmBefDep="0" type="STANDARD"/>
</detectors>
```

Confirms/resolves every open question the task brief posed:

- **Signal head placement** is the SAME ``"<link> <lane>"`` token convention
  every other lane-anchored static element in this codebase already uses
  (reducedSpeedArea/desSpeedDecision/priorityRule/dataCollectionPoint --
  ``inpx_parser._lane_ref``, ``signal_parser._build_sg_lane_index``). A head's
  signal-group wiring is ONE composite attribute, ``sg="<sc_no> <sg_no>"`` --
  NOT two separate attributes, and NOT a nested reference element. There is
  no separate ``sc``/controller attribute on a signal head at all -- the
  controller is implied by whichever ``sc_no`` the ``sg`` token's first part
  names (a signal group's own identity is scoped to its controller, matching
  ``vissim_com._signal_group_by_key``'s existing ``SGs.ItemByKey`` precedent).
- **Detector placement** is lane+pos+length (task brief's MRWA-cited shape),
  PLUS controller wiring via TWO flat attributes: ``sc="<sc_no>"`` (plain,
  not a composite token) and ``portNo="<port>"`` -- this resolves the task
  brief's own "port numbers bind detectors to external controllers" note
  (constraints.py's pre-existing catalog line) into an exact schema: a
  detector's controller/channel identity is (``sc``, ``portNo``), not a
  single-controller-plus-channel-index pair the brief's "channel?" guess
  implied. EVERY real detector in every example network that has one carries
  BOTH -- there is no example of an "unbound" detector with no controller at
  all (confirmed across Rollercoaster/Air Taxi, whose own ``sc="1"`` targets a
  controller with no vehicle signal heads at all -- e.g. a railway-crossing
  or gate controller -- proving ``sc`` is populated even when the detector
  has nothing to do with vehicle right-of-way).
- ``length`` is genuinely per-detector, real networks show 1/2/2.8/5/6 m --
  NOT hard-pinned to MRWA's 4.5 m by Vissim itself (that figure is a POLICY
  default this unit's own tool applies, not a schema constraint).
- Real ``pos`` values range from 0.1 m to 229+ m -- advance/setback loop
  detectors for actuated control are NOT all "2.0 m back from the stop
  line" (that MRWA figure is specifically an INTERSECTION-APPROACH default
  for genesis auto-placement, not a universal detector-siting rule -- stated
  correctly-scoped in the tool docstrings this unit ships).

Usage: python tools/probe_signal_heads_detectors.py [example_dir]
Default example: Urban Intersection Beijing.CN (2026 Examples Demo) -- the
task brief's own named live-check network (4-arm signalized, 1 FIXEDTIME
controller, 22 signal groups, 52 real signal heads, ZERO detectors -- a
clean field to prove add_detector against with no pre-existing objects to
confuse the before/after diff).
"""
from __future__ import annotations

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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "signal-heads-detectors-probe"

_M_PER_FT = 0.3048

_ELEMENT_XPATHS = {
    "signalHead": ".//signalHeads/signalHead",
    "detector": ".//detectors/detector",
}


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh_work_dir(name: str) -> Path:
    import os

    for stale in WORK_ROOT.glob(f"{name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    return WORK_ROOT / f"{name}_{os.getpid()}"


# ---------------------------------------------------------------------------
# Phase 0: static XML schema re-confirmation (this run, not just pasted
# findings) -- re-grep every example network for both element types.
# ---------------------------------------------------------------------------


def probe_static_xml() -> None:
    hits: dict[str, int] = {k: 0 for k in _ELEMENT_XPATHS}
    networks_with_signal_heads = 0
    networks_with_detectors = 0
    networks_with_both = 0
    for inpx_path in sorted(EXAMPLES_ROOT.glob("*/*.inpx")):
        try:
            root = ET.parse(inpx_path).getroot()
        except ET.ParseError:
            continue
        n_heads = len(root.findall(_ELEMENT_XPATHS["signalHead"]))
        n_dets = len(root.findall(_ELEMENT_XPATHS["detector"]))
        hits["signalHead"] += n_heads
        hits["detector"] += n_dets
        if n_heads:
            networks_with_signal_heads += 1
        if n_dets:
            networks_with_detectors += 1
        if n_heads and n_dets:
            networks_with_both += 1
        if n_heads or n_dets:
            _log(f"      {inpx_path.parent.name:<40s} heads={n_heads:4d} detectors={n_dets:4d}")

    _log(f"      Networks carrying signal heads: {networks_with_signal_heads}; "
         f"detectors: {networks_with_detectors}; both: {networks_with_both}")
    _log(f"      Total element counts across all examples: {hits}")

    # Full attribute-name union + one full-element dump per type, from the
    # richest example (Signalization RBC Boise.US -- 547 heads, 24 detectors,
    # real actuated sc/portNo wiring).
    rich = EXAMPLES_ROOT / "Signalization RBC Boise.US"
    root = ET.parse(next(rich.glob("*.inpx"))).getroot()
    for tag, xpath in _ELEMENT_XPATHS.items():
        els = root.findall(xpath)
        if not els:
            _log(f"      {tag}: none in {rich.name}")
            continue
        attrs: set[str] = set()
        for el in els:
            attrs.update(el.attrib.keys())
        _log(f"      {tag} (n={len(els)}): attrs={sorted(attrs)}")
        _log(f"        first element: {ET.tostring(els[0], encoding='unicode').strip()}")
        # sg / sc+portNo composite-form sanity across every element of this type
        if tag == "signalHead":
            sg_tokens = {el.get("sg", "") for el in els}
            bad = [t for t in sg_tokens if t and len(t.split()) != 2]
            _log(f"        sg attribute: {len(sg_tokens)} distinct values, "
                 f"non-2-token (unexpected) count={len(bad)}")
        if tag == "detector":
            missing_sc = sum(1 for el in els if not (el.get("sc") or "").strip())
            missing_port = sum(1 for el in els if not (el.get("portNo") or "").strip())
            _log(f"        detectors missing sc={missing_sc}, missing portNo={missing_port} "
                 f"(out of {len(els)})")


# ---------------------------------------------------------------------------
# Phase 1a: static type-library scan (no COM instance required)
# ---------------------------------------------------------------------------


def probe_typelib() -> dict[str, list[str]]:
    """Dump every Add/Remove-shaped method on the SignalHead/Detector
    containers (and, for context, SignalController/SignalGroup, since a
    signal head's own SG wiring might route through one of those instead of
    a flat AttValue)."""
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
        "ISignalHeadContainer": [],
        "ISignalHead": [],
        "IDetectorContainer": [],
        "IDetector": [],
        "ISignalGroupContainer": [],
        "ISignalGroup": [],
    }
    class_name = None
    in_target = False
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("class I") and "(DispatchBaseClass)" in stripped:
            class_name = stripped.split("(")[0].removeprefix("class ").strip()
            in_target = class_name in targets
            i += 1
            continue
        if in_target and stripped.startswith("def ") and (
            "Add" in stripped or "Remove" in stripped or stripped.startswith("def AttValue")
            or stripped.startswith("def SetAttValue")
        ):
            sig = stripped
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
        for sig in sigs[:10]:
            _log(f"        {sig}")
    return targets


# ---------------------------------------------------------------------------
# Phase 1b: live probe against an Urban Intersection Beijing.CN clone
# ---------------------------------------------------------------------------


def _dump_xml_snapshot(root: ET.Element) -> dict[str, dict[str, ET.Element]]:
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
        _log(f"       Net.NetPara.AttValue('UnitLenShort') = {unit_len!r}")

        _log("\n[1b.3] Net-level collection existence + GetAll() count cross-check vs XML:")
        collections: dict[str, Any] = {}
        for name in ["SignalHeads", "Detectors", "SignalControllers"]:
            try:
                obj = getattr(net, name)
                items = list(obj.GetAll())
                collections[name] = obj
                _log(f"        [WORKS] Net.{name} -> GetAll() count = {len(items)}")
            except Exception as exc:
                _log(f"        [no]    Net.{name} -> {type(exc).__name__}: {exc}")

        # -----------------------------------------------------------------
        # [1b.4] Attribute discovery on a real, existing signal head.
        # -----------------------------------------------------------------
        _log("\n[1b.4] Attribute discovery on a real existing signal head "
             "(cross-checked against XML ground truth):")
        sh_items = list(collections["SignalHeads"].GetAll()) if "SignalHeads" in collections else []
        sample_sh = None
        if sh_items:
            sample_sh = sh_items[0]
            sh_no = str(sample_sh.AttValue("No"))
            xml_sh = xml_before["signalHead"].get(sh_no)
            for attr_id in ["Name", "Lane", "Pos", "SigGroup", "SG", "SignalGroup", "Type"]:
                try:
                    val = sample_sh.AttValue(attr_id)
                    _log(f"        AttValue({attr_id!r}) = {val!r}")
                except Exception as exc:
                    _log(f"        [no]    AttValue({attr_id!r}) -> {type(exc).__name__}: {exc}")
            if xml_sh is not None:
                xml_pos = float(xml_sh.get("pos"))
                try:
                    com_pos = float(sample_sh.AttValue("Pos"))
                    ratio = com_pos / xml_pos if xml_pos else float("nan")
                    _log(f"        XML ground truth: lane={xml_sh.get('lane')!r} pos={xml_pos} "
                         f"sg={xml_sh.get('sg')!r} -- COM/XML pos ratio = {ratio:.6f}")
                except Exception:
                    pass
            # Does the SignalHead object expose a nested SG/SignalGroup object?
            for nested_name in ["SigGroup", "SG", "SignalGroup"]:
                try:
                    nested = getattr(sample_sh, nested_name)
                    _log(f"        [WORKS] .{nested_name} -> nested object: "
                         f"No={nested.AttValue('No')!r}")
                except Exception as exc:
                    _log(f"        [no]    .{nested_name} (property) -> {type(exc).__name__}: {exc}")

        _log("\n[1b.4b] Attribute discovery on a real existing detector "
             "(cross-checked against XML ground truth, if any exist here):")
        det_items = list(collections["Detectors"].GetAll()) if "Detectors" in collections else []
        _log(f"        Detectors present on {example_dir.name}: {len(det_items)}")
        if det_items:
            sample_det = det_items[0]
            det_no = str(sample_det.AttValue("No"))
            xml_det = xml_before["detector"].get(det_no)
            for attr_id in ["Name", "Lane", "Pos", "Length", "SC", "PortNo", "Type"]:
                try:
                    val = sample_det.AttValue(attr_id)
                    _log(f"        AttValue({attr_id!r}) = {val!r}")
                except Exception as exc:
                    _log(f"        [no]    AttValue({attr_id!r}) -> {type(exc).__name__}: {exc}")
            if xml_det is not None:
                _log(f"        XML ground truth: {ET.tostring(xml_det, encoding='unicode').strip()}")

        # -----------------------------------------------------------------
        # [1b.5] Control check: bogus attribute id (fail-open/closed sanity).
        # -----------------------------------------------------------------
        _log("\n[1b.5] Control check: bogus attribute id (fail-open/closed sanity):")
        if sample_sh is not None:
            try:
                sample_sh.AttValue("ThisAttributeDoesNotExistProbe12345")
                _log("        [UNEXPECTED] bogus attribute id did NOT raise -- fail-open!")
            except Exception as exc:
                _log(f"        [expected] bogus attribute id raised {type(exc).__name__}: {exc}")

        # -----------------------------------------------------------------
        # [1b.6] Add-method live tests.
        # -----------------------------------------------------------------
        _log("\n[1b.6] Add-method live tests (create -> inspect -> SaveNetAs dump -> remove):")
        links = list(net.Links.GetAll())
        # Pick a link that already carries >=1 real signal head, so the SG
        # target used for the new head is genuinely meaningful (matches an
        # approach that really has a controller/signal group serving it).
        target_lane_token = xml_before["signalHead"].get(
            next(iter(xml_before["signalHead"]), "")
        )
        target_link_no = None
        target_sg_ref = None
        if sh_items:
            first_head_xml = next(iter(xml_before["signalHead"].values()))
            lane_ref = (first_head_xml.get("lane") or "").split()
            target_link_no = int(lane_ref[0]) if lane_ref else None
            sg_ref = (first_head_xml.get("sg") or "").split()
            target_sg_ref = (sg_ref[0], sg_ref[1]) if len(sg_ref) == 2 else None
        if target_link_no is None:
            target_link_no = int(links[0].AttValue("No"))
        _log(f"        Target link No={target_link_no}; target sg={target_sg_ref}")

        test_pos_input = 15.0
        new_sh_no = None
        new_sh = None
        if "SignalHeads" in collections:
            lane_token = f"{target_link_no} 1"
            attempts = []
            try:
                lane_obj = next(lk for lk in links if int(lk.AttValue("No")) == target_link_no).Lanes.GetAll()[0]
            except Exception:
                lane_obj = None
            for label, lane_arg in [("lane token str", lane_token), ("Lane COM object", lane_obj)]:
                if lane_arg is None:
                    continue
                try:
                    candidate = collections["SignalHeads"].AddSignalHead(None, lane_arg, test_pos_input)
                    new_sh_no = str(candidate.AttValue("No"))
                    new_sh = candidate
                    _log(f"        [WORKS with {label}] AddSignalHead(None, <{label}>, {test_pos_input}) "
                         f"-> No={new_sh_no}")
                    break
                except Exception as exc:
                    _log(f"        AddSignalHead(None, <{label}>, {test_pos_input}) "
                         f"-> {type(exc).__name__}: {exc}")

            if new_sh is not None and target_sg_ref is not None:
                sc_no, sg_no = target_sg_ref
                sg_composite = f"{sc_no} {sg_no}"
                for attr_id, value in [
                    ("SG", sg_composite), ("SigGroup", sg_composite), ("SignalGroup", sg_composite),
                ]:
                    try:
                        new_sh.SetAttValue(attr_id, value)
                        _log(f"        [WORKS?] SetAttValue({attr_id!r}, {value!r}) did not raise -- "
                             f"readback AttValue({attr_id!r})={new_sh.AttValue(attr_id)!r}")
                    except Exception as exc:
                        _log(f"        [no]    SetAttValue({attr_id!r}, {value!r}) -> "
                             f"{type(exc).__name__}: {exc}")
                # Try assigning via a resolved SignalController->SGs->ItemByKey object instead of a string.
                try:
                    sc_obj = collections["SignalControllers"].ItemByKey(int(sc_no))
                    sg_obj = sc_obj.SGs.ItemByKey(int(sg_no))
                    for attr_id in ["SG", "SigGroup", "SignalGroup"]:
                        try:
                            new_sh.SetAttValue(attr_id, sg_obj)
                            _log(f"        [WORKS?] SetAttValue({attr_id!r}, <SG COM object>) did not raise -- "
                                 f"readback={new_sh.AttValue(attr_id)!r}")
                        except Exception as exc:
                            _log(f"        [no]    SetAttValue({attr_id!r}, <SG COM object>) -> "
                                 f"{type(exc).__name__}: {exc}")
                except Exception as exc:
                    _log(f"        Could not resolve SG COM object for control test -> "
                         f"{type(exc).__name__}: {exc}")

        new_det_no = None
        new_det = None
        if "Detectors" in collections:
            lane_token = f"{target_link_no} 1"
            try:
                new_det = collections["Detectors"].AddDetector(None, lane_token, test_pos_input)
                new_det_no = str(new_det.AttValue("No"))
                _log(f"        [WORKS] AddDetector(None, {lane_token!r}, {test_pos_input}) "
                     f"-> No={new_det_no}")
            except Exception as exc:
                _log(f"        AddDetector(None, {lane_token!r}, {test_pos_input}) "
                     f"-> {type(exc).__name__}: {exc}")
                try:
                    lane_obj = next(lk for lk in links if int(lk.AttValue("No")) == target_link_no).Lanes.GetAll()[0]
                    new_det = collections["Detectors"].AddDetector(None, lane_obj, test_pos_input)
                    new_det_no = str(new_det.AttValue("No"))
                    _log(f"        [WORKS with Lane object] AddDetector(None, <Lane obj>, {test_pos_input}) "
                         f"-> No={new_det_no}")
                except Exception as exc2:
                    _log(f"        AddDetector(None, <Lane obj>, {test_pos_input}) -> "
                         f"{type(exc2).__name__}: {exc2}")

            if new_det is not None:
                # length
                for attr_id, value in [("Length", 4.5)]:
                    try:
                        new_det.SetAttValue(attr_id, value)
                        _log(f"        [WORKS] SetAttValue({attr_id!r}, {value!r}) -> "
                             f"readback={new_det.AttValue(attr_id)!r}")
                    except Exception as exc:
                        _log(f"        [no]    SetAttValue({attr_id!r}, {value!r}) -> "
                             f"{type(exc).__name__}: {exc}")
                # controller wiring: sc (plain no) + portNo
                sc_target = 1  # Beijing's own SC no
                for attr_id, value in [("SC", sc_target), ("PortNo", 501)]:
                    try:
                        new_det.SetAttValue(attr_id, value)
                        _log(f"        [WORKS] SetAttValue({attr_id!r}, {value!r}) -> "
                             f"readback={new_det.AttValue(attr_id)!r}")
                    except Exception as exc:
                        _log(f"        [no]    SetAttValue({attr_id!r}, {value!r}) -> "
                             f"{type(exc).__name__}: {exc}")

        _log("\n[1b.7] Save + raw XML dump of every newly-added object -- "
             "THE unit's answer (compare saved pos/sg/sc/portNo against what was set):")
        save_path = work_dir / "after_adds.inpx"
        controller.save_net_as(str(save_path))
        saved_root = ET.parse(save_path).getroot()
        xml_after = _dump_xml_snapshot(saved_root)
        for label, tag, no in [
            ("SignalHead", "signalHead", new_sh_no),
            ("Detector", "detector", new_det_no),
        ]:
            if no is None:
                continue
            el = xml_after[tag].get(no)
            if el is None:
                _log(f"        {label} no={no}: NOT FOUND in saved XML!")
                continue
            _log(f"        {label} no={no} saved XML: {ET.tostring(el, encoding='unicode').strip()}")

        _log("\n[1b.8] Remove-method roundtrip (undo everything this probe added):")
        if new_sh is not None and "SignalHeads" in collections:
            try:
                before = len(list(collections["SignalHeads"].GetAll()))
                collections["SignalHeads"].RemoveSignalHead(new_sh)
                after = len(list(collections["SignalHeads"].GetAll()))
                _log(f"        [WORKS] RemoveSignalHead(<obj>) -- count {before} -> {after}")
            except Exception as exc:
                _log(f"        RemoveSignalHead(<obj>) -> {type(exc).__name__}: {exc}")
        if new_det is not None and "Detectors" in collections:
            try:
                before = len(list(collections["Detectors"].GetAll()))
                collections["Detectors"].RemoveDetector(new_det)
                after = len(list(collections["Detectors"].GetAll()))
                _log(f"        [WORKS] RemoveDetector(<obj>) -- count {before} -> {after}")
            except Exception as exc:
                _log(f"        RemoveDetector(<obj>) -> {type(exc).__name__}: {exc}")

        # -----------------------------------------------------------------
        # [1b.9] Reference-map probe: what breaks if a signal head/detector
        # is removed (does anything else reference THEM by id)?
        # -----------------------------------------------------------------
        _log("\n[1b.9] Reference-map static scan: does anything reference a "
             "signalHead/detector's own 'no' anywhere in the schema?")
        root_check = ET.parse(working).getroot()
        every_attr_values: dict[str, set[str]] = {}
        for el in root_check.iter():
            for k, v in el.attrib.items():
                every_attr_values.setdefault(k, set())
        # Cheap heuristic: search the whole document text for any OTHER
        # element carrying an attribute literally equal to a real signal
        # head/detector "no" value, outside the elements' own tags.
        head_nos = {el.get("no") for el in root_check.findall(_ELEMENT_XPATHS["signalHead"])}
        det_nos = {el.get("no") for el in root_check.findall(_ELEMENT_XPATHS["detector"])}
        referrers_of_heads = []
        referrers_of_dets = []
        for el in root_check.iter():
            if el.tag in ("signalHead", "detector"):
                continue
            for k, v in el.attrib.items():
                if v in head_nos:
                    referrers_of_heads.append((el.tag, k, v))
                if v in det_nos:
                    referrers_of_dets.append((el.tag, k, v))
        _log(f"        Elements (other than signalHead itself) whose attribute value "
             f"equals a real signal-head 'no': {referrers_of_heads[:10]} "
             f"(total {len(referrers_of_heads)})")
        _log(f"        Elements (other than detector itself) whose attribute value "
             f"equals a real detector 'no': {referrers_of_dets[:10]} "
             f"(total {len(referrers_of_dets)})")

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

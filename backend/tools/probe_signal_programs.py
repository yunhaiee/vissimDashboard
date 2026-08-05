"""v2a queue unit ⑨ Phase 1 EMPIRICAL PROBE: green splits inside fixed-time
signal programs -- where do they live, and is ANY write path verifiable?

Static XML pre-scan (already run against all 21 example folders / 25 .inpx
files before this probe ever touches COM -- scratchpad script, findings
recorded here for the record):

* FIXEDTIME (Vissig) -- 20 controllers across 10 networks (Beijing.CN,
  Bus Mall Eugene.US, Karlsruhe 3D, Rollercoaster, Cableways, Luxembourg,
  Mainz x2, Roundabout London.UK, Rome.IT). The ENTIRE program lives in the
  base64 ``intSupplyData`` attribute of ``<signalController>`` -- decoded it
  is a Vissig ``<sc>`` XML document:

      <sc version="202001" id="1" ... checkSum="344017207">   <- Beijing has
        <signaldisplays/> <signalsequences/>                     a checkSum;
        <sgs>  <sg id="1" name="01" defaultSignalSequence="3">   Bus Mall's
                 <defaultDurations .../>  </sg> ... </sgs>       older blob
        <intergreenmatrices/>  (EMPTY on Beijing)                (no version
        <progs>                                                  attr) does
          <prog id="2" cycletime="130000" offset="0" ...>        NOT
            <sgs>                                                (landmine:
              <sg sg_id="1" signal_sequence="3">                 two blob
                <cmds>                                           generations
                  <cmd display="3" begin="1000"/>   <- green on  in the wild)
                  <cmd display="1" begin="15000"/>  <- red on
                </cmds>
                <fixedstates>
                  <fixedstate display="2" duration="1000"/>  <- red-amber
                  <fixedstate display="4" duration="3000"/>  <- amber
                </fixedstates> ...

  display codes: 1=red, 2=red-amber, 3=green, 4=amber; ``begin``/``duration``
  in MILLISECONDS. The green split of SG s in program p IS the pair of cmd
  begins. The outer element's ``progNo`` attribute selects the active
  program (Beijing: progNo=2 of progs {1,2}); ``cycTm`` stays 0 on every
  real FIXEDTIME controller (the CycTm-is-disconnected finding, 2026-07-23).
  ``progFile``/``supplyFile1..3`` are EMPTY on ALL 20 -- the program is
  always EMBEDDED in the .inpx for this family, never an external file.

* FIXEDTIMESIMPLE -- 7 controllers across 4 networks (Air Taxi Vertiport,
  Karlsruhe 3D, Cube Crush, Toll Plaza Camden.US). NO intSupplyData; the
  green window lives as PLAIN XML attributes on each ``<signalGroup>``:
  ``endGreen`` / ``endRed`` / ``amber`` / ``redAmber`` / ``minGreen`` /
  ``minRed`` / ``maxGreen`` / ``greenFlsh`` / ``clearTm`` (seconds, ints),
  with the cycle on the controller's own ``cycTm``.

* VAP / RINGBARRIERCONTROLLER / etc. reference EXTERNAL supply files
  (``supplyFile1="#data#....vap/.prbc"``) -- the refuse-by-design family,
  untouched by this unit.

This probe answers LIVE (Vissim 2026, attach_to_running=False, work-dir
clones, every instance disconnected in ``finally``):

[1a] STATIC type-library scan (no COM instance, no licence): every interface
     matching signal/program vocabulary -- do ISignalController/ISignalGroup
     expose green-split attributes or a program collection at all?

[1b] Urban Intersection Beijing.CN clone (FIXEDTIME, 1 SC, 22 SGs, 2 progs):
  1. Generic attribute-table enumeration via the COM ``Attributes``
     collection on the SC AND one SG -- REAL ids + editability flags from
     Vissim itself, not name guessing (the CycTm/NodeRes lesson).
  2. SGs collection shape: count vs blob, ItemByKey presence + bogus-key
     error behavior (units ⑦/⑧ both found containers WITHOUT ItemByKey).
  3. Program-collection candidates on SC and Net (SignalPrograms/SigPrograms/
     Progs/...) -- expected absent per the type-lib scan, verified live.
  4. Green-window attr candidates on an SG (EndGreen/EndRed/GreenEnd/RedEnd/
     Amber/MinGreen/..., plus indexed forms) -- read behavior on a
     program-driven controller.
  5. WRITE probes on the program-driven FIXEDTIME SC: SG green-window ids,
     SC ProgNo (program switch), SC IntSupplyData (the blob itself -- read
     first, then write-identical, then write-modified). Expected refusals
     per the CycTm precedent; every exact error recorded.
  6. THE BLOB-WRITER FEASIBILITY TEST (the unit's central question):
     baseline SimBreakAt=11 + RunContinuous + SigState read (prog 2 SG1 is
     green 1s..15s => GREEN at t=11); then decode+edit the blob OFFLINE
     (prog id=2 sg_id=1 red-begin 15000 -> 8000, checkSum left STALE on
     purpose -- does load validate it?), write a NEW .inpx, LoadNet, re-run
     to t=11, SigState again. RED proves the edited program genuinely
     drives behavior; GREEN proves silent discard (the silent-failure
     answer). Then SaveNetAs + decode the RE-SAVED blob: edit preserved?
     checkSum recomputed by Vissim on save?
  7. signal_parser.parse_signal_controllers on the re-saved file -- our own
     read path must see the changed green window (read-verb oracle).

[1c] Air Taxi Vertiport clone (FIXEDTIMESIMPLE, SC 11, 2 SGs):
  1. Same generic attribute-table enumeration on one SG.
  2. LIVE writability of EndGreen/EndRed on a NON-program controller +
     readback + SaveNetAs + XML grep (endRed attribute) + fresh reload.
  3. Constraint physics: EndRed > CycTm accepted? negative accepted?
     overlapping green windows of both SGs accepted? (silent-failure
     candidates for the guardrail catalog).

Impact scan (static, reported in [1b.0]): signalHead sg="<sc> <sg>" counts
per controller -- which heads a green-split edit actually drives.

Usage: python tools/probe_signal_programs.py
"""
from __future__ import annotations

import base64
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

BEIJING_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Urban Intersection Beijing.CN"
)
VERTIPORT_EXAMPLE = Path(
    r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo\Air Taxi Vertiport"
)
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "signal-program-probe"

_CONTROL_BOGUS_ATTR = "ThisAttributeDoesNotExistProbe12345"

# Candidate ids for the green window on a signal group. The XML
# (FIXEDTIMESIMPLE) spells them endGreen/endRed; COM ids are discovered, not
# assumed (CycTm lesson). GreenEnd/RedEnd variants cover the reversed-word
# convention seen elsewhere (NodeRes lesson).
_SG_GREEN_ATTR_CANDIDATES = [
    "EndGreen", "EndRed", "GreenEnd", "RedEnd", "Amber", "RedAmber",
    "MinGreen", "MinRed", "MaxGreen", "GreenFlsh", "ClearTm", "SigState",
    "ContrByCOM",
]
_SC_ATTR_CANDIDATES = [
    "Type", "CycTm", "ProgNo", "Offset", "SupplyFile1", "ProgFile",
    "IntSupplyData",
]
_PROGRAM_COLLECTION_CANDIDATES = [
    "SignalPrograms", "SigPrograms", "Progs", "Programs", "SCPrograms",
    "SignalControllerPrograms",
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


def _clone(example_dir: Path) -> Path:
    work_dir = _fresh_work_dir(example_dir.name)
    shutil.copytree(example_dir, work_dir)
    inpx_files = sorted(work_dir.glob("*.inpx"))
    if not inpx_files:
        raise SystemExit(f"No .inpx in {work_dir}")
    return inpx_files[0]


# ---------------------------------------------------------------------------
# Blob helpers (offline)
# ---------------------------------------------------------------------------


def _decode_blob(inpx_path: Path, sc_no: str) -> tuple[ET.Element, str]:
    root = ET.parse(inpx_path).getroot()
    sc_el = next(
        el for el in root.findall(".//signalController") if el.get("no") == sc_no
    )
    isd = sc_el.get("intSupplyData", "")
    inner = ET.fromstring(base64.b64decode(isd).decode("utf-8"))
    return inner, isd


def _summarize_blob(inner: ET.Element) -> None:
    _log(f"        blob root <{inner.tag}> attrs={dict(inner.attrib)}")
    progs = inner.find("progs")
    if progs is None:
        _log("        NO <progs> in blob")
        return
    for prog in list(progs):
        _log(
            f"        prog id={prog.get('id')} cycletime={prog.get('cycletime')} "
            f"offset={prog.get('offset')} name={prog.get('name')!r}"
        )
        sgs = prog.find("sgs")
        for sg in (list(sgs)[:3] if sgs is not None else []):
            cmds = sg.find("cmds")
            cmd_pairs = [
                (c.get("display"), c.get("begin")) for c in (list(cmds) if cmds is not None else [])
            ]
            _log(f"          sg_id={sg.get('sg_id')} cmds(display,begin_ms)={cmd_pairs}")


def _edit_blob_green_split(
    inpx_path: Path,
    out_path: Path,
    *,
    sc_no: str,
    prog_id: str,
    sg_id: str,
    display: str,
    new_begin_ms: int,
) -> tuple[str, str]:
    """Rewrite ONE cmd begin inside the blob; checkSum left untouched on
    purpose (whether load validates it is a probe question). Returns
    (old_begin_ms, checksum) for the log."""
    tree = ET.parse(inpx_path)
    root = tree.getroot()
    sc_el = next(
        el for el in root.findall(".//signalController") if el.get("no") == sc_no
    )
    inner = ET.fromstring(base64.b64decode(sc_el.get("intSupplyData", "")).decode("utf-8"))
    checksum = inner.get("checkSum", "(absent)")
    progs = inner.find("progs")
    prog = next(p for p in list(progs) if p.get("id") == prog_id)
    sg = next(s for s in list(prog.find("sgs")) if s.get("sg_id") == sg_id)
    cmd = next(c for c in list(sg.find("cmds")) if c.get("display") == display)
    old_begin = cmd.get("begin", "")
    cmd.set("begin", str(new_begin_ms))
    new_blob = base64.b64encode(
        ET.tostring(inner, encoding="unicode").encode("utf-8")
    ).decode("ascii")
    sc_el.set("intSupplyData", new_blob)
    tree.write(out_path, encoding="UTF-8", xml_declaration=True)
    return old_begin, checksum


def _signal_head_impact(inpx_path: Path) -> dict[str, int]:
    root = ET.parse(inpx_path).getroot()
    counts: dict[str, int] = {}
    for head in root.findall(".//signalHead"):
        sg_ref = (head.get("sg") or "").strip().split()
        if len(sg_ref) == 2:
            counts[sg_ref[0]] = counts.get(sg_ref[0], 0) + 1
    return counts


# ---------------------------------------------------------------------------
# COM helpers
# ---------------------------------------------------------------------------


def _dump_attribute_table(obj: Any, label: str, cap: int = 200) -> list[str]:
    """Enumerate the object's REAL attribute table via the COM Attributes
    collection -- ids + editability from Vissim itself, never guessed."""
    codes: list[str] = []
    try:
        attrs = obj.Attributes
    except Exception as exc:
        _log(f"        [no Attributes collection on {label}] {type(exc).__name__}: {exc}")
        return codes
    items = None
    for getter in ("GetAll", None):
        try:
            items = list(attrs.GetAll()) if getter else list(attrs)
            break
        except Exception:
            continue
    if items is None:
        _log(f"        [Attributes on {label} not iterable]")
        return codes
    _log(f"        {label}: {len(items)} attributes in the COM attribute table:")
    shown = 0
    for a in items:
        fields = {}
        for prop in ("Code", "Name", "Editable", "ValueType"):
            try:
                fields[prop] = getattr(a, prop)
            except Exception:
                fields[prop] = "?"
        codes.append(str(fields.get("Code")))
        if shown < cap:
            _log(
                f"          code={fields['Code']!r} name={fields['Name']!r} "
                f"editable={fields['Editable']!r}"
            )
            shown += 1
    if len(items) > cap:
        _log(f"          ... ({len(items) - cap} more suppressed)")
    return codes


def _probe_attrs(item: Any, candidates: list[str], label: str) -> dict[str, Any]:
    found: dict[str, Any] = {}
    _log(f"        Candidate AttValue probes on {label}:")
    for attr_id in candidates:
        try:
            value = item.AttValue(attr_id)
            rendered = repr(value)
            if isinstance(value, str) and len(rendered) > 80:
                rendered = rendered[:80] + f"... ({len(value)} chars)"
            _log(f"          [WORKS] AttValue({attr_id!r}) -> {rendered}")
            found[attr_id] = value
        except Exception as exc:
            _log(f"          [no]    AttValue({attr_id!r}) -> {type(exc).__name__}: {exc}")
    return found


def _try_set(item: Any, attr_id: str, value: Any, label: str) -> bool:
    try:
        item.SetAttValue(attr_id, value)
        readback = item.AttValue(attr_id)
        rendered = repr(readback)
        if isinstance(readback, str) and len(rendered) > 60:
            rendered = rendered[:60] + f"... ({len(readback)} chars)"
        _log(f"          [WRITABLE] {label}: SetAttValue({attr_id!r}, ...) ok; readback={rendered}")
        return True
    except Exception as exc:
        _log(f"          [refused]  {label}: SetAttValue({attr_id!r}, ...) -> {type(exc).__name__}: {exc}")
        return False


def _run_to_break(controller: Any, break_s: float) -> None:
    controller.vissim.Simulation.SetAttValue("SimBreakAt", break_s)
    started = time.monotonic()
    controller.vissim.Simulation.RunContinuous()
    _log(
        f"        RunContinuous to SimBreakAt={break_s} returned in "
        f"{time.monotonic() - started:.1f}s (SimSec={controller.vissim.Simulation.AttValue('SimSec')})"
    )


def _sig_state(controller: Any, sc_no: int, sg_no: int) -> Any:
    sg = controller.vissim.Net.SignalControllers.ItemByKey(sc_no).SGs.ItemByKey(sg_no)
    for attr in ("SigState", "State"):
        try:
            return sg.AttValue(attr)
        except Exception:
            continue
    return "(unreadable)"


def _stop_sim(controller: Any) -> None:
    try:
        controller.vissim.Simulation.Stop()
    except Exception as exc:
        _log(f"        Simulation.Stop() -> {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Phase 1a: static type-library scan
# ---------------------------------------------------------------------------


def probe_typelib() -> None:
    import win32com.client.makepy as makepy
    import win32com.client.selecttlb as selecttlb
    from win32com.client import gencache

    _log("[1a] Locating the installed 'Vissim Object Library' type library...")
    candidates = [
        tlb
        for tlb in selecttlb.EnumTlbs()
        if "vissim object library" in (getattr(tlb, "desc", "") or "").lower()
    ]
    if not candidates:
        raise RuntimeError("No registered Vissim Object Library found.")

    def _hex_int(value: str) -> int:
        return int(value, 16)

    match = max(candidates, key=lambda t: (_hex_int(t.major), _hex_int(t.minor)))
    _log(f"      using: {match.desc}  clsid={match.clsid}")
    major_i, minor_i = _hex_int(match.major), _hex_int(match.minor)
    makepy.GenerateFromTypeLibSpec((match.clsid, match.lcid, major_i, minor_i))
    mod = gencache.EnsureModule(match.clsid, match.lcid, major_i, minor_i)
    src_path = Path(mod.__file__)
    lines = src_path.read_text(encoding="utf-8", errors="replace").splitlines()

    interesting = ("signal", "program", "prog")
    class_name = None
    in_target = False
    target_names: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("class I") and "(DispatchBaseClass)" in stripped:
            class_name = stripped.split("(")[0].removeprefix("class ").strip()
            in_target = any(k in class_name.lower() for k in interesting)
            if in_target:
                target_names.append(class_name)
                _log(f"\n      -- {class_name} --")
            continue
        if in_target and stripped.startswith("def ") and "(self" in stripped:
            _log(f"        {stripped.split('(')[0]}(...)")
    _log(
        f"\n      {len(target_names)} interfaces matching {interesting}: "
        f"{target_names}"
    )


# ---------------------------------------------------------------------------
# Phase 1b: Beijing (FIXEDTIME, program-driven)
# ---------------------------------------------------------------------------


def probe_beijing(controller: Any) -> None:
    working = _clone(BEIJING_EXAMPLE)
    _log(f"\n[1b.0] Beijing working copy: {working}")
    impact = _signal_head_impact(working)
    _log(f"       Signal-head impact map (sc_no -> head count): {impact}")
    inner, isd_before = _decode_blob(working, "1")
    _log(f"       Blob before ({len(isd_before)} b64 chars):")
    _summarize_blob(inner)

    _log("\n[1b.1] LoadNet (unmodified clone)...")
    controller.load_network(str(working))
    net = controller.vissim.Net

    sc = net.SignalControllers.ItemByKey(1)

    _log("\n[1b.2] FULL COM attribute table of the SignalController object:")
    _dump_attribute_table(sc, "SignalController")

    _log("\n[1b.3] SC-level candidate reads:")
    sc_found = _probe_attrs(sc, _SC_ATTR_CANDIDATES, "SC 1 (FIXEDTIME)")

    _log("\n[1b.4] SGs collection shape:")
    sgs = sc.SGs
    sg_items = list(sgs.GetAll())
    _log(f"        SGs.GetAll() count = {len(sg_items)} (blob <sgs> count = 22)")
    _log(f"        hasattr(SGs, 'ItemByKey') = {hasattr(sgs, 'ItemByKey')}")
    if hasattr(sgs, "ItemByKey"):
        try:
            sg1 = sgs.ItemByKey(1)
            _log(f"        SGs.ItemByKey(1) works -> No={sg1.AttValue('No')}")
        except Exception as exc:
            _log(f"        SGs.ItemByKey(1) RAISED: {type(exc).__name__}: {exc}")
        try:
            bogus = sgs.ItemByKey(999999)
            _log(f"        SGs.ItemByKey(999999) -> {bogus!r} (UNEXPECTED: no raise)")
        except Exception as exc:
            _log(f"        SGs.ItemByKey(999999) -> {type(exc).__name__} (fails CLOSED)")

    sg1 = sgs.ItemByKey(1) if hasattr(sgs, "ItemByKey") else sg_items[0]

    _log("\n[1b.5] FULL COM attribute table of one SignalGroup object:")
    _dump_attribute_table(sg1, "SignalGroup(1)")

    _log("\n[1b.6] SG-level candidate reads (program-driven FIXEDTIME):")
    sg_found = _probe_attrs(sg1, _SG_GREEN_ATTR_CANDIDATES, "SG 1 of SC 1")
    _log("        Indexed forms (per-program sub-attribute hypothesis):")
    for form in ("EndGreen(1)", "EndGreen(2)", "GreenEnd(1)", "EndRed(1)"):
        try:
            v = sg1.AttValue(form)
            _log(f"          [WORKS] AttValue({form!r}) -> {v!r}")
        except Exception as exc:
            _log(f"          [no]    AttValue({form!r}) -> {type(exc).__name__}: {exc}")
    _log("        CONTROL (bogus attr, fail-open/closed):")
    try:
        v = sg1.AttValue(_CONTROL_BOGUS_ATTR)
        _log(f"          AttValue(bogus) -> {v!r} (fails OPEN)")
    except Exception as exc:
        _log(f"          AttValue(bogus) -> {type(exc).__name__} (fails CLOSED)")

    _log("\n[1b.7] Program-collection candidates (SC then Net):")
    for host, host_label in ((sc, "SC"), (net, "Net")):
        for name in _PROGRAM_COLLECTION_CANDIDATES:
            try:
                coll = getattr(host, name)
                count = len(list(coll.GetAll()))
                _log(f"          [EXISTS] {host_label}.{name} -> count={count}")
            except Exception as exc:
                _log(f"          [no]     {host_label}.{name} -> {type(exc).__name__}")

    _log("\n[1b.8] WRITE probes on the program-driven FIXEDTIME controller:")
    for attr_id in ("EndGreen", "EndRed", "GreenEnd", "RedEnd"):
        if attr_id in sg_found or True:  # attempt regardless; record the exact error
            _try_set(sg1, attr_id, 20, f"SG1 green-window id {attr_id}")
    _log("        SC ProgNo (program switch):")
    prog_no_before = sc_found.get("ProgNo")
    if _try_set(sc, "ProgNo", 1, "SC ProgNo 2->1"):
        _try_set(sc, "ProgNo", prog_no_before if prog_no_before is not None else 2, "SC ProgNo revert")
    _log("        SC IntSupplyData (the blob itself):")
    try:
        blob_com = sc.AttValue("IntSupplyData")
        readable = isinstance(blob_com, str) and len(blob_com) > 0
        _log(f"          AttValue('IntSupplyData') readable={readable} len={len(blob_com) if readable else 0}")
        if readable:
            _try_set(sc, "IntSupplyData", blob_com, "SC IntSupplyData write-identical")
    except Exception as exc:
        _log(f"          AttValue('IntSupplyData') -> {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    # [1b.9] Behavior baseline: prog 2 SG1 green 1..15 s => GREEN at t=11.
    # ------------------------------------------------------------------
    _log("\n[1b.9] BASELINE behavior run (unmodified blob): SimBreakAt=11...")
    _run_to_break(controller, 11)
    state_sg1 = _sig_state(controller, 1, 1)
    state_sg2 = _sig_state(controller, 1, 2)
    _log(f"        t=11 SigState SG1={state_sg1!r} (expect GREEN) SG2={state_sg2!r} (expect RED)")
    _stop_sim(controller)

    # ------------------------------------------------------------------
    # [1b.10] Blob edit offline: prog 2, sg_id 1, red-begin 15000 -> 8000.
    # ------------------------------------------------------------------
    edited = working.with_name(working.stem + "_blobedit" + working.suffix)
    old_begin, checksum = _edit_blob_green_split(
        working, edited, sc_no="1", prog_id="2", sg_id="1", display="1", new_begin_ms=8000
    )
    _log(
        f"\n[1b.10] Wrote blob-edited copy {edited.name}: prog 2 sg 1 red-begin "
        f"{old_begin} -> 8000 ms; checkSum left STALE at {checksum}"
    )

    _log("[1b.11] LoadNet the blob-edited file (does load validate the checksum?)...")
    err_before = {p.name for p in edited.parent.glob("*.err")}
    try:
        controller.load_network(str(edited))
        _log("        LoadNet OK (no exception).")
    except Exception as exc:
        _log(f"        LoadNet RAISED: {type(exc).__name__}: {exc}")
        return
    err_after = {p.name for p in edited.parent.glob("*.err")} - err_before
    _log(f"        New .err files after load: {sorted(err_after) or 'NONE'}")

    _log("[1b.12] Post-load COM readback of the blob (did Vissim keep the edit?)...")
    sc2 = controller.vissim.Net.SignalControllers.ItemByKey(1)
    try:
        blob_after_load = sc2.AttValue("IntSupplyData")
        kept = "8000" in base64.b64decode(blob_after_load).decode("utf-8", errors="replace")
        _log(f"        edited begin=8000 visible in COM-read blob: {kept}")
    except Exception as exc:
        _log(f"        IntSupplyData read failed: {type(exc).__name__}: {exc}")

    _log("[1b.13] DECISIVE behavior run on the edited program: SimBreakAt=11...")
    _run_to_break(controller, 11)
    state_sg1_mod = _sig_state(controller, 1, 1)
    state_sg2_mod = _sig_state(controller, 1, 2)
    _log(
        f"        t=11 SigState SG1={state_sg1_mod!r} (RED proves the blob edit DRIVES "
        f"behavior; GREEN proves silent discard) SG2={state_sg2_mod!r}"
    )
    _stop_sim(controller)

    _log("[1b.14] SaveNetAs round trip: does Vissim preserve the edit / recompute the checksum?")
    resaved = working.with_name(working.stem + "_blobedit_resaved" + working.suffix)
    controller.save_net_as(resaved)
    inner_resaved, _ = _decode_blob(resaved, "1")
    prog2 = next(p for p in list(inner_resaved.find("progs")) if p.get("id") == "2")
    sg1_el = next(s for s in list(prog2.find("sgs")) if s.get("sg_id") == "1")
    red_cmd = next(c for c in list(sg1_el.find("cmds")) if c.get("display") == "1")
    _log(
        f"        re-saved blob: prog2 sg1 red-begin={red_cmd.get('begin')} (intended 8000); "
        f"checkSum before={checksum} after={inner_resaved.get('checkSum', '(absent)')}"
    )

    _log("[1b.15] Our own read path (signal_parser) on the re-saved file:")
    from services.signal_parser import parse_signal_controllers

    parsed = parse_signal_controllers(resaved)
    info = parsed.get("1")
    if info:
        _log(
            f"        cycle_time={info['cycle_time']} phases="
            f"{[(p['start'], p['duration'], p['signal_group_ids']) for p in info['phases'][:6]]}"
        )
        _log(
            "        NOTE: signal_parser reads progs[0] (prog id=1), while the ACTIVE "
            "program is progNo=2 -- checked here to see whether the parser tracks the edit."
        )


# ---------------------------------------------------------------------------
# Phase 1c: Air Taxi Vertiport (FIXEDTIMESIMPLE)
# ---------------------------------------------------------------------------


def probe_vertiport(controller: Any) -> None:
    working = _clone(VERTIPORT_EXAMPLE)
    _log(f"\n[1c.0] Vertiport working copy: {working}")
    _log("[1c.1] LoadNet...")
    controller.load_network(str(working))
    sc = controller.vissim.Net.SignalControllers.ItemByKey(11)
    _log("        SC 11 candidate reads:")
    _probe_attrs(sc, ["Type", "CycTm", "ProgNo"], "SC 11 (FIXEDTIMESIMPLE)")
    sgs = sc.SGs
    sg1 = sgs.ItemByKey(1) if hasattr(sgs, "ItemByKey") else list(sgs.GetAll())[0]

    _log("\n[1c.2] FULL COM attribute table of a FIXEDTIMESIMPLE SignalGroup:")
    _dump_attribute_table(sg1, "SG1 of SC11")

    _log("\n[1c.3] Candidate reads (XML ground truth: endGreen=0 endRed=998 amber=0):")
    found = _probe_attrs(sg1, _SG_GREEN_ATTR_CANDIDATES, "SG1 of SC11")

    _log("\n[1c.4] LIVE writability on FIXEDTIMESIMPLE (the real green-split write):")
    wrote = _try_set(sg1, "EndRed", 500, "SG1 EndRed 998->500")
    _try_set(sg1, "EndGreen", 600, "SG1 EndGreen 0->600")

    _log("\n[1c.5] Constraint physics (does Vissim itself refuse anything?):")
    cyc = None
    try:
        cyc = sc.AttValue("CycTm")
    except Exception:
        pass
    _log(f"        SC CycTm = {cyc!r}")
    _try_set(sg1, "EndRed", 5000, "SG1 EndRed BEYOND CycTm (999)")
    _try_set(sg1, "EndRed", -5, "SG1 EndRed NEGATIVE")
    try:
        sg2 = sgs.ItemByKey(2)
        _try_set(sg2, "EndRed", 500, "SG2 EndRed (overlap with SG1's window)")
        _try_set(sg2, "EndGreen", 600, "SG2 EndGreen (fully overlapping green)")
        _log("        (both SGs now share one green window -- did anything refuse? see above)")
    except Exception as exc:
        _log(f"        SG2 probe failed: {type(exc).__name__}: {exc}")

    if wrote:
        _log("\n[1c.6] Persist + XML check + fresh readback:")
        # restore a deterministic value first so the XML assertion is exact
        _try_set(sg1, "EndRed", 500, "SG1 EndRed re-set to 500 before persist")
        _try_set(sg1, "EndGreen", 600, "SG1 EndGreen re-set to 600 before persist")
        saved = working.with_name(working.stem + "_saved" + working.suffix)
        controller.save_net_as(saved)
        root = ET.parse(saved).getroot()
        sc_el = next(el for el in root.findall(".//signalController") if el.get("no") == "11")
        sg_el = next(sg for sg in sc_el.find("sgs") if sg.get("no") == "1")
        _log(
            f"        persisted XML SG1 attrs: endRed={sg_el.get('endRed')} "
            f"endGreen={sg_el.get('endGreen')} (intended 500/600)"
        )
        controller.load_network(str(saved))
        sg1_re = controller.vissim.Net.SignalControllers.ItemByKey(11).SGs.ItemByKey(1)
        _log(
            f"        fresh-reload readback: EndRed={sg1_re.AttValue('EndRed')!r} "
            f"EndGreen={sg1_re.AttValue('EndGreen')!r}"
        )


def main() -> None:
    _log("#" * 78)
    _log("# PHASE 1a -- static type-library scan (no COM instance)")
    _log("#" * 78)
    probe_typelib()

    _log("\n" + "#" * 78)
    _log("# PHASE 1b -- Urban Intersection Beijing.CN (FIXEDTIME, program-driven)")
    _log("#" * 78)

    import pythoncom

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    try:
        _log("[1b.-] Launching a dedicated Vissim instance (attach_to_running=False)...")
        started = time.monotonic()
        controller.connect(visible=True, launch_if_missing=True, attach_to_running=False)
        _log(f"       Connected via {controller.prog_id} in {time.monotonic() - started:.1f}s")

        probe_beijing(controller)

        _log("\n" + "#" * 78)
        _log("# PHASE 1c -- Air Taxi Vertiport (FIXEDTIMESIMPLE)")
        _log("#" * 78)
        probe_vertiport(controller)
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

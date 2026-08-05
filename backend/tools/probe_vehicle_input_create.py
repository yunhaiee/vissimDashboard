"""Q5 unit (3) PROBE: what does ``AddVehicleInput`` actually accept, what
does it actually create, and what does the shadow model see afterwards?

Runs BEFORE any verb code, per the house rule -- ``add_vehicle_input`` is
the LAST item on the GENESIS GAP LIST (docs/phase2/DECISIONS.md, 2026-08-01):
when it lands an agent can build a working intersection from nothing and
Vissim will run it.

**Deliberately NOT re-probed here, because Q5 unit (1) already proved it
live** (``backend/tools/composite_intersection_probe.py`` Stage 0 [0d] and
Stage 5, 2026-08-03): that ``Net.VehicleInputs.AddVehicleInput(Key, Link)``
genuinely creates a real input (``GetAll()`` id-set diff, not a bare
non-exception), and that the created input genuinely GENERATES TRAFFIC.
This probe answers only the questions a VERB wrapper raises that that probe
never had to -- what the second argument really accepts, what a fresh
input's defaults are, what happens on a link that "cannot" host an input,
whether a zero-input network can mint its first, and what the postwrite
shadow diff actually looks like.

Parts, each network chosen from this file's own static corpus scan [0]:

  a. ``Urban Intersection Beijing.CN``  -- 12 real inputs, 112 links (56
     connectors). Arg-shape matrix, fresh defaults, writability, saved XML,
     shadow delta, load oracle.
  b. ``Urban Intersection Beijing.CN``  -- the CONNECTOR target: does COM
     refuse it, and does the model still load?
  c. ``Air Taxi Vertiport``             -- 0 vehicle inputs: the genesis
     family-limitation question.
  d/e/f. ``Urban Intersection Beijing.CN``, every existing input ZEROED --
     a controlled traffic triple: one new input on a PLAIN link (d), the
     same on a CONNECTOR (e), and NO new input at all (f, the null control
     that makes d's and e's counts attributable to the new input rather
     than to some unnoticed other source of demand).
  g. ``Toll Plaza Camden.US``           -- 5 inputs x 5 interval rows each:
     how many interval rows does a FRESH input get, and which carry the
     caller's volume?

**Per-part process boundaries, not one long-lived process** -- gap (1)'s
validated fix (Q5 unit (1)): sequential COM connect/disconnect cycles in one
process segfault after ~2-3. ``main()`` shells out to itself once per part;
each part process holds at most 2 COM cycles (its own, plus
``tools_check._load_check``'s). State crosses process boundaries through a
JSON file, and **every number in the FINDINGS below is written into that
file by the probe itself** (``runtime/vehicle-input-create-probe/state.json``)
so the claims here are machine-checkable rather than transcribed by hand.

Hard rules honored: no git commands; no edits to ``services/`` from here
(this file is read/measure-only); work dirs confined to
``runtime/vehicle-input-create-probe/``; every connection is
``attach_to_running=False``; example networks are COPIED into the work dir,
never opened in place; ``runtime/uploads/`` is never touched.

---------------------------------------------------------------------------
FINDINGS (live, 2026-08-03, PTV Vissim 2026 SP09 on this machine; all seven
parts PASS)
---------------------------------------------------------------------------

**A0. The container's whole creation surface, from the type library:**
``IVehicleInputContainer.AddVehicleInput(Key, Link)``,
``DuplicateVehicleInput(Source, NewKey)``, ``RemoveVehicleInput(
VehicleInput)``, ``ItemByKey``/``ItemKeyExists``. Unlike signal controllers
(Q5 unit 2), the plain ``Add`` here is the REAL path and needs no
duplicate-then-edit dance -- ``DuplicateVehicleInput`` exists but is
deliberately not wrapped by any verb: there is nothing a duplicate gives
that ``AddVehicleInput`` + a volume does not.

**A1. The Link argument REQUIRES a real COM object; every wrong form fails
LOUDLY, and none of them creates anything.** Measured as a matrix, each
attempt with its own free key and judged by its own ``GetAll()`` id-set diff
(never by a bare non-exception -- the ``AddPriorityRule`` silent-no-op
precedent)::

    AddVehicleInput(21, 1)             TypeError: The Python instance can not
                                       be converted to a COM object    added=[]
    AddVehicleInput(21, "1")           TypeError (same)                added=[]
    AddVehicleInput(21, <Lane obj>)    com_error -2147352571 'Type mismatch.'
                                                                       added=[]
    AddVehicleInput(21, None)          com_error ... 'CCOMVehicleInputContainer
                                       Base<CVehicleInputManager>::AddVehicle
                                       Input: Argument Link must be provided.'
                                                                       added=[]
    AddVehicleInput(21)                com_error -2147352561 'Parameter not
                                       optional.'                      added=[]
    AddVehicleInput(21, <Link obj>)    created                       added=[21]

Consequence: ``add_vehicle_input`` resolves the link through
``_link_object_by_no`` -- the SAME helper Q4 unit (2)'s measurement-infra
verbs already use for the identical pywin32 marshalling landmine -- so a
bogus link number raises this codebase's own structured
``VissimObjectNotFoundError`` before COM is ever asked. **Nothing in this
matrix fails silently**, so the argument shape needs no constraint.

**A2. ``No`` comes back as a python ``int`` here, NOT a float.** Stated
plainly because the opposite would have been the more useful finding: Q5
unit (2)'s ``get_signal_controller`` shipped a ``"2.0"`` bug because
``SignalControllers`` hands ``No`` back as a float. ``VehicleInputs`` does
not, on this version, on these three networks. ``get_vehicle_input`` still
does ``str(int(AttValue("No")))`` -- consistent with ``_collection_nos``'s
own guard and free -- but that ``int()`` is a defensive house convention
here, **not** a fix for anything this probe observed.

**B. A fresh input's defaults, read straight off the live object with
nothing written yet:** ``No=<key>`` ``Name=''`` ``Link='<link number>'``
``Volume(1)=0.0`` ``VehComp(1)='1'`` ``VolType(1)='STOCHASTIC'``
``Cont(1)=0``. So a created-and-left-alone input generates NOTHING (volume
0) -- which is why the verb takes the volume as a required parameter rather
than an optional one. ``Name``, ``Volume(1)`` and ``VehComp(1)`` are all
writable and read back (``VehComp(1)`` accepts an int and reads back the
string ``'1'``).

**C. The persisted shape matches the network's own real inputs exactly.**
Beijing's fresh input 21 saves as ``<vehicleInput no="21" name="Probe
input" link="1" anmFlag="false">`` with ONE
``<timeIntervalVehVolume volume="450" vehComp="1" volType="STOCHASTIC"
cont="false" timeInt="1 0"/>`` -- element attribute names and interval
attribute names both SET-EQUAL to input 1's own, and the same interval
count (1 vs 1). Nothing is only-in-real or only-in-fresh either way.

  *HARNESS BUG, live-caught in this probe's own first run and fixed here:*
  the first version read the interval rows with a DIRECT-CHILD xpath
  (``./timeIntervalVehVolume``) and got zero rows for every input in every
  network -- which read as "vehicle inputs carry no volume rows at all",
  a confident wrong answer rather than a missing one. The rows are nested
  one level deeper (``<vehicleInput><timeIntVehVols><timeIntervalVehVolume/>``),
  exactly the trap Q5 unit (1) hit with ``<signalController><sgs>
  <signalGroup/>``. ``tools_network._parse_vehicle_inputs`` uses ``.//``
  and was right all along. See ``_INTERVAL_XPATH``.

**D. NO family limitation -- a zero-input network CAN mint its first.**
``Air Taxi Vertiport`` has 0 ``<vehicleInput>`` elements; the SAME
``GetAll()`` enumeration that returns 12 items on Beijing returns 0 there
(so this is a measurement, not a silent failure -- Q5 unit (1)'s "a 'none
found' result is evidence only if the window could have contained one"
rule). ``AddVehicleInput(1, <link object>)`` CREATED input 1 (diff
``[] -> [1]``) and the saved file reloads clean (``load_ok`` +
``init_ok``). **This is the first genesis verb in this codebase with no
zero-object limitation** -- ``add_priority_rule`` (Q4 unit 6) and
``add_signal_controller`` (Q5 unit 2) are duplicate-based and genuinely
stuck without a donor; this one takes a Key and a Link. Together with
``add_link``/``add_connector`` (Q4 unit 5), which never had one either,
that is what finally makes "build an intersection from nothing" reachable.

**E. THE exemption, derived -- and it is TWO classes, not one.** A real
``diff_shadow_models`` over the saved .inpx (the SAME machinery
``GuardrailEngine.postwrite`` runs) for ONE created input reports:

  - on Beijing (Part A) and Toll Plaza (Part G): ``vehicle_inputs: 1
    added``, and **zero** other differences of any kind;
  - on Air Taxi (Part C): ``vehicle_inputs: 1 added`` **plus
    ``vehicle_composition_entries: 1 added`` (``1.700``)**, and zero
    non-"added" differences.

The second class is the surprise, and it is the exact analogue of Q5 unit
(2)'s 44 unpredicted ``signal_program_splits`` rows. Air Taxi has NO
``<vehicleComposition>`` element at all before the write (measured:
``{}``); a fresh input references ``VehComp(1)='1'``, and saving
MATERIALISES composition 1 with one relative-flow row
(``vehType="700" desSpeedDistr="2" relFlow="1"``) -- Vissim inventing the
default composition the input needs. So::

    _ALLOWS_NEW_OBJECTS_IN["add_vehicle_input"] = {"vehicle_inputs",
                                                   "vehicle_composition_entries"}

Both are needed: without ``vehicle_inputs`` EVERY call is refused and
rolled back; without ``vehicle_composition_entries`` every call **on a
composition-less network** is -- i.e. precisely the genesis case this verb
exists for. (``vehicle_compositions`` itself needs no entry: it is not in
``_SHADOW_OBJECT_ID_FIELDS``, so the diff never watches it.)

**F. A vehicle input on a CONNECTOR is ACCEPTED by COM, and it genuinely
generates traffic. The refusal I expected to ship would have been wrong.**
Measured as a controlled triple on Beijing with all 12 real inputs zeroed
(read back: all 0.0), one new input at 2000 veh/h, 300 ``RunSingleStep``
calls = 30 simulation seconds at ``simRes=10``, an expected 16.7 arrivals
stated BEFORE stepping, ``locate_failures=0`` in all three:

  =========================  ======  ==========================================
  arm                        total   vehicles by link
  =========================  ======  ==========================================
  (d) input on PLAIN link 1      13   {1: 8, 10000: 2, 10002: 1, 2: 2}
  (e) input on CONNECTOR 10000   13   {10002: 12, 2: 1}
  (f) NULL CONTROL, no input      0   {}
  =========================  ======  ==========================================

The null control is what makes this evidence: with no new input the same
network over the same window produces exactly ZERO vehicles, so all 13 in
each of the other two arms came from the created input. The connector arm
produces the SAME count as the plain-link arm -- so "a connector input is
dead" would have been a false claim.

What genuinely differs is WHERE demand enters. Connector 10000 runs link 1
-> link 2 (from pos 335.02 m on link 1), so demand placed on it appears
mid-junction: the entire 335 m upstream approach is bypassed, and by t=30 s
12 of its 13 vehicles have already piled onto the NEXT connector (10002),
the 13th onto link 2 -- while the plain-link arm still has 8 of its 13 on
the approach itself.
Nothing queues on the approach, nothing is metered by whatever controls
that approach, and any report of the approach's own flow/queue simply does
not see this demand. That is a real modelling consequence with no COM
error attached to it -- a WARN, not a refusal, and the shipped constraint
``vehicle_input_on_connector`` says exactly this and offers the
connector's own upstream link as the alternative.

**G. A created input only ever gets interval 1 filled -- and the number of
intervals is the NETWORK's, not the verb's.** ``Toll Plaza Camden.US``
(5 real inputs, each with 5 interval rows -- stated before the COM call):
the fresh input came back with exactly 5 rows too, volumes
``['450', '0', '0', '0', '0']``. ``Air Taxi Vertiport`` (Part C) is the
extreme case: its own time-interval set has 12 intervals starting at
0 ms, 1000 ms, 190000 ms, ... so the first-ever input there carries 12
rows and the requested 450 veh/h applies for **one second** before
dropping to 0 for the rest of the run. Nothing in Vissim complains. Hence
constraint ``vehicle_input_created_covers_first_interval_only`` (warn),
and hence the verb's own message reports the REAL interval count read back
off the persisted file rather than assuming 1.

Usage: ``python tools/probe_vehicle_input_create.py`` -- runs every part end
to end (via internal per-part subprocesses) and prints one PASS/FAIL
summary. Advanced: ``--part {a,b,c,d,e,f,g} --state <path>`` runs exactly
one part against a persisted JSON state file (what the orchestrator shells
out to).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

EXAMPLES_ROOT = Path(r"C:\Users\Public\Documents\PTV Vision\PTV Vissim 2026\Examples Demo")
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "vehicle-input-create-probe"

#: Networks, chosen from this file's own static corpus scan (see [0]).
INPUT_RICH_NET = EXAMPLES_ROOT / "Urban Intersection Beijing.CN"
ZERO_INPUT_NET = EXAMPLES_ROOT / "Air Taxi Vertiport"
#: Every one of its 5 real inputs carries 5 time-interval rows (static scan
#: below) -- the corpus's densest multi-interval demand model, and the one
#: place the "a created input only ever gets interval 1 filled" claim can be
#: measured on a network that ALREADY has inputs (Air Taxi proves the same
#: thing from zero, but a reader could object that a zero-input network is a
#: special case).
MULTI_INTERVAL_NET = EXAMPLES_ROOT / "Toll Plaza Camden.US"

#: The volume the structural parts write onto a fresh input. Deliberately NOT
#: the default (0.0) so "the write landed" is a real claim rather than a
#: tautology against Vissim's own default.
_PROBE_VOLUME_VPH = 450.0

#: Parts D/E's demand window, sized from Beijing's OWN <simulation simRes="10">
#: rather than guessed (Q5 unit (1)'s harness landmine 2: a "no vehicles
#: found" result is evidence only if the window could have contained one).
#: 300 RunSingleStep calls = 30 SIMULATION seconds; at 2000 veh/h that is a
#: 1.80 s headway, i.e. ~16.7 expected arrivals.
_DEMAND_STEPS = 300
_DEMAND_VOLUME_VPH = 2000.0

_PART_LABELS = {
    "a": "Part A: input-rich network -- arg-shape matrix, fresh defaults, saved XML, shadow delta",
    "b": "Part B: CONNECTOR target -- does COM refuse, or silently create an input?",
    "c": "Part C: zero-input network -- is there a genesis family limitation at all?",
    "d": "Part D: TRAFFIC CONTROL -- demand-isolated network, one input on a PLAIN link",
    "e": "Part E: TRAFFIC TREATMENT -- demand-isolated network, one input on a CONNECTOR",
    "f": "Part F: TRAFFIC NULL CONTROL -- demand-isolated network, NO new input at all",
    "g": "Part G: multi-interval network -- how many interval rows does a fresh input get?",
}
_PART_ORDER = ["a", "b", "c", "d", "e", "f", "g"]


def _log(msg: str) -> None:
    print(msg, flush=True)


def _fresh_work_dir(name: str) -> Path:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    for stale in WORK_ROOT.glob(f"{name}_*"):
        try:
            shutil.rmtree(stale)
        except OSError:
            pass
    work_dir = WORK_ROOT / f"{name}_{os.getpid()}"
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _clone(example_dir: Path, name: str) -> Path:
    """Copy the whole example folder (sibling assets matter) into our own
    work dir and return the .inpx inside it -- the example is NEVER opened
    in place (00-EXECUTION-GUIDE.md rule 4)."""
    work_dir = _fresh_work_dir(name)
    shutil.copytree(example_dir, work_dir, dirs_exist_ok=True)
    return sorted(work_dir.glob("*.inpx"))[0]


def _load_state(state_path: Path) -> dict[str, Any]:
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {"part_results": []}


def _save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _record(state: dict[str, Any], part: str, status: str, detail: str = "") -> None:
    state["part_results"].append({"part": part, "status": status, "detail": detail})
    _log(f"\n>>> [{part}] {status}{(' -- ' + detail) if detail else ''}")


# ---------------------------------------------------------------------------
# [0] Static scans -- no COM, no licence.
# ---------------------------------------------------------------------------


def static_corpus_scan() -> dict[str, Any]:
    _log("=" * 78)
    _log("[0] STATIC corpus scan: <vehicleInput> counts across Examples Demo")
    _log("=" * 78)
    per_network: dict[str, int] = {}
    zero_input_networks: list[str] = []
    for inpx in sorted(EXAMPLES_ROOT.rglob("*.inpx")):
        try:
            root = ET.parse(inpx).getroot()
        except Exception as exc:
            _log(f"      {inpx.parent.name}: PARSE FAILED ({type(exc).__name__}) -- skipped")
            continue
        count = len(root.findall(".//vehicleInput"))
        per_network[inpx.parent.name] = max(count, per_network.get(inpx.parent.name, 0))
        if count == 0:
            zero_input_networks.append(inpx.parent.name)
    _log(f"      per-network vehicleInput counts: {dict(sorted(per_network.items()))}")
    _log(f"      networks with ZERO vehicle inputs: {sorted(set(zero_input_networks))}")
    return {
        "per_network": per_network,
        "zero_input_networks": sorted(set(zero_input_networks)),
    }


# HARNESS BUG, live-caught in this probe's own first run and fixed here (see
# the module docstring): the xpath below MUST be descendant (".//") and not
# direct-child ("./"). A vehicle input's volume rows are nested one level
# deeper than a naive guess -- <vehicleInput><timeIntVehVols>
# <timeIntervalVehVolume/></timeIntVehVols></vehicleInput> -- exactly the
# same trap Q5 unit (1) hit with <signalController><sgs><signalGroup/>. The
# direct-child form silently returned ZERO intervals for every input in
# every network, which read as "a fresh input carries no volume row at all"
# -- a confident wrong answer, not a missing one. ``tools_network.
# _parse_vehicle_inputs`` uses ".//" and was right all along.
_INTERVAL_XPATH = ".//timeIntervalVehVolume"


def _static_vehicle_input_shape(inpx: Path) -> dict[str, Any]:
    """The persisted shape of this network's own real vehicle inputs -- the
    cheap oracle the fresh, COM-created one is compared against."""
    root = ET.parse(inpx).getroot()
    elements = root.findall(".//vehicleInput")
    attr_sets = [set(el.attrib) for el in elements]
    child_attr_sets = [
        set(child.attrib) for el in elements for child in el.findall(_INTERVAL_XPATH)
    ]
    return {
        "count": len(elements),
        "element_attrs": sorted(set().union(*attr_sets)) if attr_sets else [],
        "interval_attrs": sorted(set().union(*child_attr_sets)) if child_attr_sets else [],
        "interval_counts": sorted({len(el.findall(_INTERVAL_XPATH)) for el in elements}),
        "first": dict(elements[0].attrib) if elements else None,
        "first_interval": (
            dict(elements[0].findall(_INTERVAL_XPATH)[0].attrib)
            if elements and elements[0].findall(_INTERVAL_XPATH)
            else None
        ),
    }


def _saved_vehicle_input(inpx: Path, no: str) -> dict[str, Any] | None:
    """One vehicle input's persisted element + its interval children."""
    root = ET.parse(inpx).getroot()
    for el in root.findall(".//vehicleInput"):
        if el.get("no") == no:
            return {
                "attrs": dict(el.attrib),
                "intervals": [dict(c.attrib) for c in el.findall(_INTERVAL_XPATH)],
            }
    return None


def _composition_shape(inpx: Path) -> dict[str, int]:
    """{composition no: relative-flow-entry count} -- the second class Part C
    caught moving, so every part records it."""
    root = ET.parse(inpx).getroot()
    return {
        vc.get("no", ""): len(vc.findall(".//vehicleCompositionRelativeFlow"))
        for vc in root.findall(".//vehicleComposition")
    }


def _pick_links(inpx: Path) -> tuple[str, str | None]:
    """(a real non-connector link id, a connector link id or None) straight
    from the XML -- a connector is a <link> carrying <fromLinkEndPt>, the
    same discriminator ``inpx_parser`` itself uses."""
    root = ET.parse(inpx).getroot()
    plain: str | None = None
    connector: str | None = None
    for link in root.findall(".//links/link"):
        is_connector = link.find(".//fromLinkEndPt") is not None
        if is_connector and connector is None:
            connector = link.get("no")
        if not is_connector and plain is None:
            plain = link.get("no")
        if plain and connector:
            break
    if plain is None:
        raise RuntimeError(f"{inpx} has no plain (non-connector) link at all")
    return plain, connector


# ---------------------------------------------------------------------------
# Live helpers.
# ---------------------------------------------------------------------------


def _vi_ids(net: Any) -> set[int]:
    return {int(v.AttValue("No")) for v in net.VehicleInputs.GetAll()}


def _read_vi(vi_obj: Any) -> dict[str, Any]:
    """Every attribute a verb might want off a live IVehicleInput, each read
    defensively -- an attribute that does not exist on this Vissim version
    must show up as a NAMED failure, never as a confident wrong value (Q5
    unit (1)'s harness-landmine-2 lesson: a bare ``except`` turned a
    nonexistent attribute into a false measurement)."""
    out: dict[str, Any] = {}
    for attr in ("No", "Name", "Link", "Volume(1)", "VehComp(1)", "VolType(1)", "Cont(1)"):
        try:
            value = vi_obj.AttValue(attr)
            out[attr] = value
            out[f"{attr}__pytype"] = type(value).__name__
        except Exception as exc:
            out[attr] = f"<UNREADABLE: {type(exc).__name__}: {exc}>"
    return out


def _try_add(net: Any, label: str, args: tuple) -> tuple[bool, Any, str]:
    """One ``AddVehicleInput`` attempt, judged by a ``GetAll()`` id-set diff
    -- never by the bare return value (``AddPriorityRule``'s silent-no-op
    precedent: it returns None, raises nothing, creates nothing). Returns
    ``(created, returned_object, detail)``."""
    before = _vi_ids(net)
    returned: Any = None
    err = ""
    try:
        returned = net.VehicleInputs.AddVehicleInput(*args)
        _log(f"      [no exception] AddVehicleInput({label}) -> {returned!r}")
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        _log(f"      [raised]       AddVehicleInput({label}) -> {err}")
    after = _vi_ids(net)
    added = sorted(after - before)
    _log(f"      GetAll() id-set diff: before={sorted(before)} after={sorted(after)} added={added}")
    detail = f"added={added}" + (f" err={err}" if err else "")
    return bool(added), returned, detail


def _grouped_diff(before_model: dict[str, Any], after_model: dict[str, Any]) -> dict[str, list[str]]:
    """``diff_shadow_models`` (the SAME machinery ``GuardrailEngine.postwrite``
    runs), grouped by object class and printed."""
    from services.agent.guardrails import diff_shadow_models

    by_class: dict[str, list[str]] = {}
    for entry in diff_shadow_models(before_model, after_model):
        label = f"{entry['object_id']}:{entry['change']}"
        if entry["change"] == "field_changed":
            label += f".{entry.get('field')}"
        by_class.setdefault(entry["object_class"], []).append(label)
    for object_class, entries in sorted(by_class.items()):
        _log(
            f"        {object_class}: {len(entries)} -- {entries[:6]}"
            f"{' ...' if len(entries) > 6 else ''}"
        )
    if not by_class:
        _log("        (no differences at all)")
    return by_class


def _scan_typelib_methods(keywords: tuple[str, ...]) -> list[str]:
    """Static type-library scan (no COM instance, no licence used) -- the
    house 'check the type library FIRST rather than assume' step."""
    import win32com.client.makepy as makepy
    import win32com.client.selecttlb as selecttlb
    from win32com.client import gencache

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
    lines = Path(mod.__file__).read_text(encoding="utf-8", errors="replace").splitlines()

    matched: list[str] = []
    signatures: list[str] = []
    in_target = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("class I") and "(DispatchBaseClass)" in stripped:
            class_name = stripped.split("(")[0].removeprefix("class ").strip()
            in_target = any(kw in class_name.lower() for kw in keywords)
            if in_target:
                matched.append(class_name)
                _log(f"\n      -- {class_name} (DispatchBaseClass) --")
            continue
        if in_target and stripped.startswith("def ") and "(self" in stripped:
            _log(f"        {stripped}")
            signatures.append(stripped)
    if not matched:
        _log(f"      NO interface matching any of {keywords!r} found in the type library.")
    return signatures


def _count_vehicles(controller: Any) -> tuple[int, list[str], int, str | None]:
    """(total vehicles, their '<link>-<lane>' tokens, locate failures, first
    error). ``IVehicle`` has NO ``Link`` attribute -- ``Lane`` is the
    house-proven read (Q5 unit (1)'s harness landmine 2, where a bare
    ``except`` swallowed 13 identical 'Could not find attribute with ID
    LINK' errors and turned a structurally impossible proof into a claimed
    measurement). Any locate failure is reported LOUDLY here and voids the
    proof rather than silently emptying it."""
    vehicles = list(controller.vissim.Net.Vehicles.GetAll())
    lanes: list[str] = []
    failures = 0
    first_error: str | None = None
    for veh in vehicles:
        try:
            lanes.append(str(veh.AttValue("Lane") or ""))
        except Exception as exc:
            failures += 1
            if first_error is None:
                first_error = f"{type(exc).__name__}: {exc}"
    return len(vehicles), lanes, failures, first_error


def _demand_isolated_run(
    part: str, on_connector: bool, state: dict[str, Any], add_input: bool = True
) -> bool:
    """Parts D/E/F share one body: clone the input-rich network, ZERO every
    one of its existing vehicle inputs (so the network has no ambient traffic
    at all and any vehicle observed can only have come from OUR new input),
    then add exactly ONE input -- on a plain link (D) or on a connector (E)
    -- at the same volume, or NONE at all (F, the null control), step the
    same window, and count.

    D and E differ in exactly one bit, which is what makes the pair a
    controlled comparison rather than two anecdotes; F is what makes "13
    vehicles appeared" attributable to the new input rather than to some
    other, unnoticed source of demand in the network.
    """
    import pythoncom

    from services.vissim_com import VissimController

    label = "CONNECTOR" if on_connector else "PLAIN LINK"
    _log("=" * 78)
    _log(f"[{part.upper()}] {_PART_LABELS[part]}")
    _log("=" * 78)

    working = _clone(INPUT_RICH_NET, f"demand-{part}")
    plain_link, connector_link = _pick_links(working)
    target_link = connector_link if on_connector else plain_link
    if target_link is None:
        _log("    No connector in this network -- cannot run this part.")
        return False
    _log(f"    clone: {working}")
    _log(f"    target: link {target_link} ({label})" if add_input
         else "    NULL CONTROL: no new vehicle input will be added at all")

    findings: dict[str, Any] = {
        "network": INPUT_RICH_NET.name,
        "on_connector": on_connector,
        "add_input": add_input,
        "target_link": target_link if add_input else None,
        "steps": _DEMAND_STEPS,
        "volume_vph": _DEMAND_VOLUME_VPH if add_input else 0.0,
    }

    pythoncom.CoInitialize()
    controller = VissimController()
    try:
        controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(working))
        net = controller.vissim.Net

        existing = sorted(_vi_ids(net))
        _log(f"\n[{part.upper()}.1] zeroing all {len(existing)} pre-existing vehicle inputs {existing} "
             "so the network has ZERO ambient demand ...")
        for no in existing:
            net.VehicleInputs.ItemByKey(no).SetAttValue("Volume(1)", 0.0)
        residual = {
            no: float(net.VehicleInputs.ItemByKey(no).AttValue("Volume(1)")) for no in existing
        }
        all_zero = all(abs(v) < 1e-9 for v in residual.values())
        _log(f"      read back: {residual} -> all zero = {all_zero}")
        findings["preexisting_inputs"] = existing
        findings["preexisting_all_zeroed"] = all_zero
        if not all_zero:
            _log("      Could not zero the ambient demand -- the count below would be meaningless.")
            state[f"part_{part}"] = findings
            return False

        if add_input:
            link_obj = net.Links.ItemByKey(int(target_link))
            is_conn = link_obj.AttValue("IsConn")
            _log(f"      target link {target_link}'s own IsConn attribute reads {is_conn!r}")
            findings["target_is_conn"] = is_conn

            key = max(_vi_ids(net) | {0}) + 1
            created, new_vi, detail = _try_add(net, f"{key}, <{label} Link object>", (key, link_obj))
            findings["created"] = created
            findings["create_detail"] = detail
            if not created:
                state[f"part_{part}"] = findings
                return False
            new_vi.SetAttValue("Volume(1)", _DEMAND_VOLUME_VPH)
            readback = float(new_vi.AttValue("Volume(1)"))
            _log(f"      new input {key} volume: wrote {_DEMAND_VOLUME_VPH} -> read back {readback}")
            findings["new_input_no"] = key
            findings["new_input_volume_readback"] = readback

        sim_res = float(controller.vissim.Simulation.AttValue("SimRes"))
        sim_seconds = _DEMAND_STEPS / sim_res
        headway_s = 3600.0 / _DEMAND_VOLUME_VPH
        expected = sim_seconds * _DEMAND_VOLUME_VPH / 3600.0 if add_input else 0.0
        _log(
            f"\n[{part.upper()}.2] WINDOW BOUNDS, stated BEFORE stepping: simRes={sim_res:g} "
            f"steps/sim-second -> {_DEMAND_STEPS} RunSingleStep calls = {sim_seconds:g} SIMULATION "
            f"seconds. "
            + (
                f"At {_DEMAND_VOLUME_VPH:g} veh/h (headway {headway_s:.2f} s) that is an expected "
                f"{expected:.1f} arrivals. A zero result is only evidence because this number is "
                ">> 1."
                if add_input
                else "With every input zeroed and NO new input, the expected arrival count is "
                "EXACTLY 0 -- this is the null control that makes D's and E's counts attributable."
            )
        )
        findings.update(
            {"sim_res": sim_res, "sim_seconds": sim_seconds, "headway_s": headway_s,
             "expected_arrivals": expected}
        )

        for _ in range(_DEMAND_STEPS):
            controller.run_single_step()

        total, lanes, failures, first_error = _count_vehicles(controller)
        on_target = [t for t in lanes if add_input and t.partition("-")[0] == str(target_link)]
        by_link: dict[str, int] = {}
        for token in lanes:
            link_of = token.partition("-")[0]
            by_link[link_of] = by_link.get(link_of, 0) + 1
        _log(f"      vehicles by link: {dict(sorted(by_link.items()))}")
        if failures:
            _log(
                f"      WARNING: {failures}/{total} vehicle(s) could not be located at all "
                f"(first error: {first_error}). Every count below is NOT trustworthy while this "
                "is non-zero."
            )
        _log(
            f"\n[{part.upper()}.3] After {_DEMAND_STEPS} RunSingleStep calls: {total} vehicle(s) "
            f"network-wide; {len(on_target)} of them on the target link {target_link} itself. "
            f"locate_failures={failures}"
        )
        findings.update(
            {
                "vehicles_total": total,
                "vehicles_on_target_link": len(on_target),
                "vehicles_by_link": dict(sorted(by_link.items())),
                "locate_failures": failures,
                "first_locate_error": first_error,
                "lane_tokens_sample": lanes[:12],
            }
        )
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    state[f"part_{part}"] = findings
    return findings.get("locate_failures") == 0


# ---------------------------------------------------------------------------
# Part A: input-rich network -- arg shapes, defaults, saved XML, shadow delta.
# ---------------------------------------------------------------------------


def part_a(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.tools_network import build_shadow_model
    from services.vissim_com import VissimController

    _log("=" * 78)
    _log(f"[A] {_PART_LABELS['a']}")
    _log("=" * 78)

    _log("\n[A.0] Static type-library scan: IVehicleInput* interfaces ...")
    signatures = _scan_typelib_methods(("vehicleinput",))

    working = _clone(INPUT_RICH_NET, "input-rich")
    _log(f"\n    clone: {working}")
    static_before = _static_vehicle_input_shape(working)
    comps_before = _composition_shape(working)
    plain_link, connector_link = _pick_links(working)
    _log(f"    static shape of this network's own real inputs: count={static_before['count']} "
         f"element_attrs={static_before['element_attrs']} interval_attrs={static_before['interval_attrs']} "
         f"per-input interval counts={static_before['interval_counts']}")
    _log(f"    first real input: {static_before['first']} interval={static_before['first_interval']}")
    _log(f"    vehicle compositions BEFORE (no -> relFlow entry count): {comps_before}")
    _log(f"    anchor plain link={plain_link}  connector={connector_link}")

    shadow_before = build_shadow_model(working)
    _log(
        f"    shadow BEFORE: vehicle_inputs={len(shadow_before['vehicle_inputs'])} "
        f"links={len(shadow_before['links'])} "
        f"vehicle_composition_entries={len(shadow_before['vehicle_composition_entries'])}"
    )

    findings: dict[str, Any] = {
        "network": INPUT_RICH_NET.name,
        "typelib_signatures": signatures,
        "static_before": static_before,
        "compositions_before": comps_before,
        "plain_link": plain_link,
        "shadow_vehicle_inputs_before": len(shadow_before["vehicle_inputs"]),
    }

    pythoncom.CoInitialize()
    controller = VissimController()
    saved: Path | None = None
    new_no: int | None = None
    obj_created = False
    int_created = str_created = lane_created = none_created = onearg_created = False
    try:
        controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(working))
        net = controller.vissim.Net

        before_ids = _vi_ids(net)
        _log(f"\n[A.1] live GetAll(): {len(before_ids)} existing input(s) -- {sorted(before_ids)}")
        findings["com_inputs_before"] = sorted(before_ids)

        link_obj = net.Links.ItemByKey(int(plain_link))
        lane_obj = None
        try:
            lane_obj = link_obj.Lanes.ItemByKey(1)
        except Exception as exc:
            _log(f"      (could not resolve lane 1 of link {plain_link}: {exc})")

        _log("\n[A.2] ARGUMENT-SHAPE MATRIX -- each attempt gets its own free key and is judged "
             "by its own GetAll() id-set diff, never by a bare non-exception.")
        matrix: dict[str, str] = {}

        def _attempt(label: str, make_args: Any) -> bool:
            key = max(_vi_ids(net) | {0}) + 1
            try:
                args = make_args(key)
            except Exception as exc:  # a shape we cannot even build
                matrix[label] = f"could not build args: {type(exc).__name__}: {exc}"
                _log(f"      {label}: could not build args -- {exc}")
                return False
            created, _obj, detail = _try_add(net, f"{key}, {label}", args)
            matrix[label] = detail
            return created

        int_created = _attempt("<raw int link no>", lambda k: (k, int(plain_link)))
        str_created = _attempt("<raw str link no>", lambda k: (k, plain_link))
        lane_created = (
            _attempt("<Lane COM object>", lambda k: (k, lane_obj)) if lane_obj is not None else False
        )
        none_created = _attempt("None", lambda k: (k, None))
        onearg_created = _attempt("<no second argument>", lambda k: (k,))

        # ... and the shape Q5 unit (1) already proved, last, so nothing
        # above can have been polluted by a real object already existing.
        key = max(_vi_ids(net) | {0}) + 1
        obj_created, new_vi, obj_detail = _try_add(
            net, f"{key}, <Link COM object>", (key, link_obj)
        )
        matrix["<Link COM object>"] = obj_detail
        findings["arg_shape_matrix"] = matrix
        findings["arg_shapes_that_created"] = {
            "raw_int": int_created,
            "raw_str": str_created,
            "lane_object": lane_created,
            "none": none_created,
            "one_argument": onearg_created,
            "link_object": obj_created,
        }
        if not obj_created:
            _log("      The Link-OBJECT form did not create anything -- Q5 unit (1)'s result did "
                 "not reproduce; everything below would be meaningless.")
            state["part_a"] = findings
            return False
        new_no = key

        _log(f"\n[A.3] FRESH input {new_no}'s defaults, read straight back off the live object "
             "(nothing written yet):")
        defaults = _read_vi(new_vi)
        for attr, value in defaults.items():
            if not attr.endswith("__pytype"):
                _log(f"      {attr} = {value!r}  (python type: {defaults.get(attr + '__pytype')})")
        findings["fresh_defaults"] = defaults
        findings["no_pytype"] = defaults.get("No__pytype")

        _log("\n[A.4] Which of Name / Volume(1) / VehComp(1) are writable on a fresh input?")
        writes: dict[str, Any] = {}
        for attr, value in (
            ("Name", "Probe input"),
            ("Volume(1)", _PROBE_VOLUME_VPH),
            ("VehComp(1)", 1),
        ):
            try:
                new_vi.SetAttValue(attr, value)
                readback = new_vi.AttValue(attr)
                writes[attr] = {"wrote": value, "read_back": readback, "ok": True}
                _log(f"      {attr}: wrote {value!r} -> read back {readback!r}")
            except Exception as exc:
                writes[attr] = {"wrote": value, "error": f"{type(exc).__name__}: {exc}", "ok": False}
                _log(f"      {attr}: REFUSED -- {type(exc).__name__}: {exc}")
        findings["writes"] = writes

        saved = working.with_name(working.stem + "_saved.inpx")
        controller.save_net_as(saved)
        _log(f"\n[A.5] saved: {saved}")
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    if saved is None or new_no is None:
        state["part_a"] = findings
        return False

    # -- the persisted shape, against this network's OWN real inputs.
    fresh_saved = _saved_vehicle_input(saved, str(new_no))
    real_saved = _saved_vehicle_input(saved, str(sorted(findings["com_inputs_before"])[0]))
    _log(f"\n[A.6] saved <vehicleInput> for the FRESH input: {fresh_saved}")
    _log(f"      saved <vehicleInput> for a REAL   input: {real_saved}")
    fresh_attrs = set((fresh_saved or {}).get("attrs", {}))
    real_attrs = set((real_saved or {}).get("attrs", {}))
    fresh_iv = set(((fresh_saved or {}).get("intervals") or [{}])[0])
    real_iv = set(((real_saved or {}).get("intervals") or [{}])[0])
    _log(f"      element attribute names -- fresh={sorted(fresh_attrs)} real={sorted(real_attrs)} "
         f"only-in-real={sorted(real_attrs - fresh_attrs)} only-in-fresh={sorted(fresh_attrs - real_attrs)}")
    _log(f"      interval attribute names -- fresh={sorted(fresh_iv)} real={sorted(real_iv)} "
         f"only-in-real={sorted(real_iv - fresh_iv)} only-in-fresh={sorted(fresh_iv - real_iv)}")
    _log(f"      interval COUNT -- fresh={len((fresh_saved or {}).get('intervals') or [])} "
         f"real={len((real_saved or {}).get('intervals') or [])}")
    findings["saved_fresh"] = fresh_saved
    findings["saved_real"] = real_saved
    findings["saved_attr_delta"] = {
        "element_only_in_real": sorted(real_attrs - fresh_attrs),
        "element_only_in_fresh": sorted(fresh_attrs - real_attrs),
        "interval_only_in_real": sorted(real_iv - fresh_iv),
        "interval_only_in_fresh": sorted(fresh_iv - real_iv),
    }
    findings["compositions_after"] = _composition_shape(saved)
    _log(f"      vehicle compositions AFTER: {findings['compositions_after']} "
         f"(BEFORE was {comps_before})")

    # -- THE measurement the guardrail exemption is derived from.
    shadow_after = build_shadow_model(saved)
    _log(
        f"\n[A.7] shadow AFTER one created input: vehicle_inputs={len(shadow_after['vehicle_inputs'])} "
        f"vehicle_composition_entries={len(shadow_after['vehicle_composition_entries'])}"
    )
    _log("      diff_shadow_models (the SAME machinery GuardrailEngine.postwrite runs):")
    by_class = _grouped_diff(shadow_before, shadow_after)

    from services.agent.guardrails import diff_shadow_models

    raw = diff_shadow_models(shadow_before, shadow_after)
    added_classes = sorted({e["object_class"] for e in raw if e["change"] == "added"})
    other = [e for e in raw if e["change"] != "added"]
    added_rows = {
        cls: len([e for e in raw if e["change"] == "added" and e["object_class"] == cls])
        for cls in added_classes
    }
    _log(f"\n      => object classes that gained rows: {added_classes} (rows: {added_rows})")
    _log(f"      => non-'added' differences (must be EMPTY for a clean exemption): {other}")
    findings["diff_classes"] = {k: len(v) for k, v in by_class.items()}
    findings["added_classes"] = added_classes
    findings["added_rows_by_class"] = added_rows
    findings["other_change_count"] = len(other)
    findings["other_changes"] = other[:10]

    # -- the shadow model's own view of the new input (what the verb's
    #    postwrite target-persisted check will compare against).
    parsed_new = next(
        (v for v in shadow_after["vehicle_inputs"] if str(v.get("no")) == str(new_no)), None
    )
    _log(f"\n[A.8] the new input as build_shadow_model sees it: {parsed_new}")
    findings["shadow_new_input"] = parsed_new
    findings["shadow_volume_matches_write"] = (
        parsed_new is not None and abs(float(parsed_new.get("volume") or -1) - _PROBE_VOLUME_VPH) < 1e-6
    )

    # -- a REAL COM load oracle over the saved file (own fresh process-local
    #    Vissim instance -- this is the second and last COM cycle in this part).
    from services.agent.tools_check import _load_check

    load_result = _load_check(saved)
    _log(f"\n[A.9] load_check on the saved file: {load_result}")
    findings["load_check"] = load_result

    state["part_a"] = findings
    return (
        bool(obj_created)
        and not int_created
        and not str_created
        and not lane_created
        and not none_created
        and not onearg_created
        and added_classes == ["vehicle_inputs"]
        and not other
        and bool(findings["shadow_volume_matches_write"])
        and bool(load_result.get("load_ok"))
        and bool(load_result.get("init_ok"))
    )


# ---------------------------------------------------------------------------
# Part B: the CONNECTOR target.
# ---------------------------------------------------------------------------


def part_b(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.tools_network import build_shadow_model
    from services.vissim_com import VissimController

    _log("=" * 78)
    _log(f"[B] {_PART_LABELS['b']}")
    _log("=" * 78)

    working = _clone(INPUT_RICH_NET, "connector-target")
    plain_link, connector_link = _pick_links(working)
    if connector_link is None:
        _log("    This network has no connector at all -- cannot answer this here.")
        state["part_b"] = {"network": INPUT_RICH_NET.name, "connector_available": False}
        return False
    _log(f"    clone: {working}")
    _log(f"    connector under test: {connector_link} (a <link> carrying <fromLinkEndPt>); "
         f"control plain link: {plain_link}")

    shadow_before = build_shadow_model(working)
    findings: dict[str, Any] = {
        "network": INPUT_RICH_NET.name,
        "connector_available": True,
        "connector_link": connector_link,
        "control_plain_link": plain_link,
    }

    pythoncom.CoInitialize()
    controller = VissimController()
    saved: Path | None = None
    conn_key: int | None = None
    try:
        controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(working))
        net = controller.vissim.Net

        before = _vi_ids(net)
        _log(f"\n[B.1] live GetAll(): {len(before)} existing input(s)")

        conn_obj = net.Links.ItemByKey(int(connector_link))
        is_conn = conn_obj.AttValue("IsConn")
        _log(f"      the target link's own IsConn attribute reads {is_conn!r} -- confirming this "
             "really is a connector before anything is claimed about connectors")
        findings["target_is_conn"] = is_conn

        key = max(before | {0}) + 1
        created, conn_vi, detail = _try_add(net, f"{key}, <CONNECTOR Link object>", (key, conn_obj))
        findings["connector_created"] = created
        findings["connector_detail"] = detail
        if created:
            conn_key = key
            conn_vi.SetAttValue("Volume(1)", _PROBE_VOLUME_VPH)
            findings["connector_input_read"] = _read_vi(conn_vi)
            _log(f"      the connector-hosted input reads back: {findings['connector_input_read']}")

        # The CONTROL: the same call shape on a plain link in the SAME
        # session must succeed, otherwise "the connector was refused" would
        # be indistinguishable from "this session refuses everything".
        plain_obj = net.Links.ItemByKey(int(plain_link))
        key2 = max(_vi_ids(net) | {0}) + 1
        control_created, _o2, control_detail = _try_add(
            net, f"{key2}, <PLAIN Link object> (CONTROL)", (key2, plain_obj)
        )
        findings["control_created"] = control_created
        findings["control_detail"] = control_detail
        _log(
            f"\n[B.2] connector target created={created}, plain-link control "
            f"created={control_created}."
        )

        saved = working.with_name(working.stem + "_saved.inpx")
        controller.save_net_as(saved)
        _log(f"      saved: {saved}")
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    if saved is not None and conn_key is not None:
        shadow_after = build_shadow_model(saved)
        parsed = next(
            (v for v in shadow_after["vehicle_inputs"] if str(v.get("no")) == str(conn_key)), None
        )
        _log(f"\n[B.3] the connector-hosted input as build_shadow_model sees it: {parsed}")
        findings["shadow_connector_input"] = parsed
        findings["saved_connector_input"] = _saved_vehicle_input(saved, str(conn_key))
        _log(f"      persisted XML: {findings['saved_connector_input']}")

        from services.agent.tools_check import _load_check

        load_result = _load_check(saved)
        _log(f"[B.4] load_check on the saved file (does Vissim itself object at load/init?): "
             f"{load_result}")
        findings["load_check"] = load_result

    _log(
        "\n[B.5] VERDICT: COM "
        f"{'ACCEPTED' if findings.get('connector_created') else 'REFUSED'} a vehicle input on a "
        "connector. Whether that input is a LIE (accepted but generating nothing) is measured "
        "separately and decisively by Parts D/E."
    )

    state["part_b"] = findings
    return bool(findings.get("control_created"))


# ---------------------------------------------------------------------------
# Part C: zero-input network -- is there a genesis family limitation at all?
# ---------------------------------------------------------------------------


def part_c(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.tools_network import build_shadow_model
    from services.vissim_com import VissimController

    _log("=" * 78)
    _log(f"[C] {_PART_LABELS['c']}")
    _log("=" * 78)

    working = _clone(ZERO_INPUT_NET, "zero-input")
    static_shape = _static_vehicle_input_shape(working)
    comps_before = _composition_shape(working)
    plain_link, _connector = _pick_links(working)
    _log(f"    clone: {working}")
    _log(f"    static <vehicleInput> count in this network: {static_shape['count']} "
         "(the premise of this whole part -- stated before any COM call)")
    _log(f"    vehicle compositions BEFORE (no -> relFlow entry count): {comps_before}")
    _log(f"    anchor plain link: {plain_link}")

    shadow_before = build_shadow_model(working)
    findings: dict[str, Any] = {
        "network": ZERO_INPUT_NET.name,
        "static_input_count": static_shape["count"],
        "compositions_before": comps_before,
        "shadow_vehicle_inputs_before": len(shadow_before["vehicle_inputs"]),
        "shadow_composition_entries_before": len(shadow_before["vehicle_composition_entries"]),
        "plain_link": plain_link,
    }

    pythoncom.CoInitialize()
    controller = VissimController()
    saved: Path | None = None
    new_no: int | None = None
    try:
        controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(working))
        net = controller.vissim.Net

        before = _vi_ids(net)
        _log(
            f"\n[C.1] live GetAll() on the zero-input network: {len(before)} input(s) -- "
            "the SAME enumeration returns 12 on Beijing (Part A [A.1]), so an empty result here "
            "is a MEASUREMENT, not a silent failure."
        )
        findings["com_inputs_before"] = sorted(before)

        link_obj = net.Links.ItemByKey(int(plain_link))
        key = (max(before) if before else 0) + 1
        _log(f"      the verb's own numbering rule (max(existing) if existing else 0) + 1 "
             f"yields key={key} on an empty collection")
        created, new_vi, detail = _try_add(net, f"{key}, <Link COM object>", (key, link_obj))
        findings["first_input_created"] = created
        findings["first_input_detail"] = detail
        findings["first_input_key"] = key
        if created:
            new_no = key
            new_vi.SetAttValue("Volume(1)", _PROBE_VOLUME_VPH)
            defaults = _read_vi(new_vi)
            _log(f"\n[C.2] the first-ever input on this network reads back: {defaults}")
            findings["first_input_read"] = defaults
            saved = working.with_name(working.stem + "_saved.inpx")
            controller.save_net_as(saved)
            _log(f"      saved: {saved}")
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    if saved is not None and new_no is not None:
        shadow_after = build_shadow_model(saved)
        saved_input = _saved_vehicle_input(saved, str(new_no))
        comps_after = _composition_shape(saved)
        _log(f"\n[C.3] persisted XML for the first-ever input: "
             f"{len(saved_input['intervals'])} interval row(s); first two = "
             f"{saved_input['intervals'][:2]}")
        _log(f"      vehicle compositions AFTER: {comps_after} (BEFORE was {comps_before})")
        _log(f"      shadow AFTER: vehicle_inputs={len(shadow_after['vehicle_inputs'])} "
             f"vehicle_composition_entries={len(shadow_after['vehicle_composition_entries'])}")
        _log("      diff_shadow_models:")
        _grouped_diff(shadow_before, shadow_after)

        from services.agent.guardrails import diff_shadow_models

        raw = diff_shadow_models(shadow_before, shadow_after)
        added_classes = sorted({e["object_class"] for e in raw if e["change"] == "added"})
        other = [e for e in raw if e["change"] != "added"]
        added_rows = {
            cls: len([e for e in raw if e["change"] == "added" and e["object_class"] == cls])
            for cls in added_classes
        }
        _log(f"      => object classes that gained rows: {added_classes} (rows: {added_rows})")
        _log(f"      => non-'added' differences: {other}")
        findings.update(
            {
                "saved_first_input": saved_input,
                "first_input_interval_count": len(saved_input["intervals"]),
                "compositions_after": comps_after,
                "shadow_vehicle_inputs_after": len(shadow_after["vehicle_inputs"]),
                "shadow_composition_entries_after": len(shadow_after["vehicle_composition_entries"]),
                "added_classes": added_classes,
                "added_rows_by_class": added_rows,
                "other_change_count": len(other),
                "other_changes": other[:10],
                "shadow_new_input": next(
                    (v for v in shadow_after["vehicle_inputs"] if str(v.get("no")) == str(new_no)),
                    None,
                ),
            }
        )

        from services.agent.tools_check import _load_check

        load_result = _load_check(saved)
        _log(f"[C.4] load_check on the saved file: {load_result}")
        findings["load_check"] = load_result

    _log(
        "\n[C.5] VERDICT: a network with ZERO vehicle inputs "
        f"{'CAN' if findings.get('first_input_created') else 'CANNOT'} mint its first from "
        "AddVehicleInput -- unlike add_priority_rule (Q4 unit 6) and add_signal_controller "
        "(Q5 unit 2), both of which are duplicate-based and genuinely stuck without a donor."
    )

    state["part_c"] = findings
    return (
        bool(findings.get("first_input_created"))
        and findings.get("shadow_vehicle_inputs_after") == 1
        and bool((findings.get("load_check") or {}).get("load_ok"))
    )


def part_d(state: dict[str, Any]) -> bool:
    return _demand_isolated_run("d", on_connector=False, state=state)


def part_e(state: dict[str, Any]) -> bool:
    return _demand_isolated_run("e", on_connector=True, state=state)


def part_f(state: dict[str, Any]) -> bool:
    return _demand_isolated_run("f", on_connector=False, state=state, add_input=False)


# ---------------------------------------------------------------------------
# Part G: a multi-interval demand model -- how many interval rows does a fresh
# input get, and which of them carry the volume the caller asked for?
# ---------------------------------------------------------------------------


def part_g(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.tools_network import build_shadow_model
    from services.vissim_com import VissimController

    _log("=" * 78)
    _log(f"[G] {_PART_LABELS['g']}")
    _log("=" * 78)

    working = _clone(MULTI_INTERVAL_NET, "multi-interval")
    static_before = _static_vehicle_input_shape(working)
    plain_link, _connector = _pick_links(working)
    _log(f"    clone: {working}")
    _log(f"    this network's own {static_before['count']} real inputs each carry "
         f"{static_before['interval_counts']} interval row(s) -- stated BEFORE any COM call, so "
         "the fresh input's own count below is compared against a known number, not a guess")
    _log(f"    anchor plain link: {plain_link}")

    shadow_before = build_shadow_model(working)
    findings: dict[str, Any] = {
        "network": MULTI_INTERVAL_NET.name,
        "existing_interval_counts": static_before["interval_counts"],
        "existing_input_count": static_before["count"],
        "plain_link": plain_link,
    }

    pythoncom.CoInitialize()
    controller = VissimController()
    saved: Path | None = None
    new_no: int | None = None
    try:
        controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(working))
        net = controller.vissim.Net

        before = _vi_ids(net)
        _log(f"\n[G.1] live GetAll(): {len(before)} existing input(s) -- {sorted(before)}")
        link_obj = net.Links.ItemByKey(int(plain_link))
        key = (max(before) if before else 0) + 1
        created, new_vi, detail = _try_add(net, f"{key}, <Link COM object>", (key, link_obj))
        findings["created"] = created
        findings["create_detail"] = detail
        if not created:
            state["part_g"] = findings
            return False
        new_no = key
        # Exactly what the verb will do: write interval 1's volume and
        # nothing else.
        new_vi.SetAttValue("Volume(1)", _PROBE_VOLUME_VPH)
        saved = working.with_name(working.stem + "_saved.inpx")
        controller.save_net_as(saved)
        _log(f"      saved: {saved}")
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    if saved is None or new_no is None:
        state["part_g"] = findings
        return False

    fresh = _saved_vehicle_input(saved, str(new_no))
    volumes = [iv.get("volume") for iv in (fresh or {}).get("intervals", [])]
    _log(f"\n[G.2] the fresh input's persisted interval rows: {len(volumes)} of them, "
         f"volumes={volumes}")
    _log("      i.e. the caller's volume lands on interval 1 ONLY; every other interval this "
         "network defines stays at 0 -- the demand silently stops after interval 1's window.")
    shadow_after = build_shadow_model(saved)
    parsed_new = next(
        (v for v in shadow_after["vehicle_inputs"] if str(v.get("no")) == str(new_no)), None
    )
    _log(f"      as build_shadow_model sees it: interval_count={parsed_new.get('interval_count') if parsed_new else None} "
         f"volumes={[iv.get('volume') for iv in (parsed_new or {}).get('volumes', [])]}")

    from services.agent.guardrails import diff_shadow_models

    raw = diff_shadow_models(shadow_before, shadow_after)
    added_classes = sorted({e["object_class"] for e in raw if e["change"] == "added"})
    other = [e for e in raw if e["change"] != "added"]
    _log(f"      diff: added classes={added_classes} non-added differences={other}")

    findings.update(
        {
            "fresh_interval_count": len(volumes),
            "fresh_interval_volumes": volumes,
            "shadow_interval_count": parsed_new.get("interval_count") if parsed_new else None,
            "added_classes": added_classes,
            "other_change_count": len(other),
        }
    )
    state["part_g"] = findings
    return (
        len(volumes) == (static_before["interval_counts"] or [0])[0]
        and len(volumes) > 1
        and float(volumes[0] or 0) == _PROBE_VOLUME_VPH
        and all(float(v or 0) == 0.0 for v in volumes[1:])
    )


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------

_PARTS = {
    "a": part_a, "b": part_b, "c": part_c, "d": part_d, "e": part_e, "f": part_f, "g": part_g,
}


def _run_one_part(part: str, state_path: Path) -> int:
    state = _load_state(state_path)
    try:
        ok = _PARTS[part](state)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        _record(state, part, "FAIL", f"{type(exc).__name__}: {exc}")
        _save_state(state_path, state)
        return 1
    _record(state, part, "PASS" if ok else "FAIL", f"pid={os.getpid()}")
    _save_state(state_path, state)
    return 0 if ok else 1


def _run_all() -> int:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    state_path = WORK_ROOT / "state.json"
    if state_path.exists():
        state_path.unlink()

    state = _load_state(state_path)
    state["static_scan"] = static_corpus_scan()
    _save_state(state_path, state)

    failures: list[str] = []
    for part in _PART_ORDER:
        _log(f"\n\n########## spawning {_PART_LABELS[part]} ##########")
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--part", part, "--state", str(state_path)],
            cwd=str(BACKEND_ROOT),
        )
        state = _load_state(state_path)
        recorded = [r for r in state["part_results"] if r["part"] == part]
        if not recorded:
            # The subprocess died before recording its own outcome -- the
            # native-crash case gap (1) exists for. Recorded HERE so a crash
            # can never read as a silent pass.
            _record(state, part, "CRASHED", f"subprocess exit={proc.returncode}, no state record")
            _save_state(state_path, state)
            failures.append(part)
        elif recorded[-1]["status"] != "PASS":
            failures.append(part)

    _log("\n" + "=" * 78)
    _log("SUMMARY")
    _log("=" * 78)
    for record in _load_state(state_path)["part_results"]:
        _log(f"  [{record['part']}] {record['status']}  {record['detail']}")
    _log(f"\nstate file: {state_path}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Q5 unit (3) vehicle-input creation probe")
    parser.add_argument("--part", choices=_PART_ORDER)
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    if args.part:
        if args.state is None:
            parser.error("--part requires --state")
        return _run_one_part(args.part, args.state)
    return _run_all()


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        raise SystemExit(main())

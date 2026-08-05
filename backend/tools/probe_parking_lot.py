"""Q6 unit (2) PROBE: what IS a Vissim parking lot over COM, what does creating
or removing one really do, and is a created one functional or a skeleton?

Runs BEFORE any verb code, per the house rule (TOOL-CATALOG v2b bullet
"Parking lots / zones", ~line 192). Parking lots have ZERO precedent in this
repo: DECISIONS.md (2026-07-26 Tier-1 entry, ~line 1154) deferred them for
having "no settled attribute schema anywhere in this repo", and
``tools_network.build_shadow_model`` carried no parking-lot class at all until
this unit. The ONLY prior contact is Q6 unit (1), which registered
``parkingLot@lane`` (573 corpus occurrences) as a lane-token reference site --
so removals that would dangle a lot are already refused, but nothing could
read, create, or remove one.

Shape follows the house probe ``probe_link_geometry.py`` (Q6 unit 1): static
corpus scan (no COM, no licence) -> static type-library scan -> live COM parts,
**one subprocess per part** (sequential COM connect/disconnect cycles in ONE
process segfault after ~2-3; each part process holds at most 2 -- its own, plus
``tools_check._load_check``'s). State crosses process boundaries through a JSON
file, and **every number in the FINDINGS below is written into that file by the
probe itself** (``runtime/parking-lot-probe/state.json``).

**Sign convention (Q6 unit (1)'s recorded defect):** every persisted delta
reported anywhere in this file and in ``state.json`` is AFTER minus BEFORE.

**Independent re-derivation (Q6 unit (1)'s entry requirement):** the FINDINGS
numbers below were re-derived from the persisted ``.inpx`` pairs by
``--audit``, a SEPARATE code path (:func:`audit_state`) that re-parses the
saved files with ElementTree and re-computes each claim, rather than reading
the fields the live parts wrote. Its verdict is stored under ``state["audit"]``.

Parts (each network chosen by this file's own static scan [0]):

  a. ``Urban Intersection Beijing.CN`` -- a network with NO ``<parkingLots>``
     section at all: does plain ``AddParkingLot(Key, Lane, Pos)`` produce a
     functional object (vs the duplicate-then-edit family)? What attributes are
     real? What materialises in the saved XML and in the shadow diff?
  b. ``Urban Intersection Beijing.CN`` -- the attribute WRITABILITY sweep on a
     fresh lot: which of the 21 probed COM ids take a ``SetAttValue`` with a
     genuine readback, which are refused, and which are DERIVED from others.
     (21 is the write plan's own length; ``_XML_ATTRS`` lists 31 XML attribute
     names and a freshly created lot persists 28 of them -- three different
     numbers for three different things, kept distinct here on purpose.)
  c. ``3D - Complex Intersection Karlsruhe.DE`` -- ``RemoveParkingLot`` on a
     lot that a ``vehicleRouteParking`` still references: refusal, dangle, or
     silent cascade-delete? (The three species this codebase has catalogued --
     Q4 units (2)/(5)/(7).)
  d. ``Urban Intersection Beijing.CN`` -- ``RemoveParkingLot`` on a CLEAN lot
     (created in the same session, nothing references it): persisted diff +
     load oracle.
  e. ``Urban Intersection Beijing.CN`` -- the OTHER species:
     ``AddAbstractParkingLot(Key, Link, Pos, Zone)`` and ``Zones.AddZone`` --
     is the ZONECONNECTOR (zone) half reachable on a non-dynamic-assignment
     network at all?
  f. ``Toll Plaza Camden.US`` (FEET) -- the display-unit cross-check every new
     length-shaped surface needs: ``Pos``/``Length``/``LenPerSpc`` COM values
     against the same objects' persisted XML, read AND write.
  g. ``Urban Intersection Beijing.CN`` -- NULL CONTROL: load and save with ZERO
     edits. Every "the diff shows X" claim above is only evidence because this
     part shows an untouched round-trip changes nothing.
  h. ``Urban Intersection Beijing.CN`` -- the SKELETON question: create a lot
     on a demand-carrying link, run the simulation, and read the lot's own
     occupancy. Does a parking lot with no parking routing decision do
     anything?
  i. ``Urban Intersection Beijing.CN`` -- the derived-capacity relation,
     ISOLATED against an untouched control lot. Part B measured "Capacity is
     refused loudly" and, several writes later, read it back as 100 -- two
     facts a single sweep cannot connect, because Length, LenPerSpc AND Type
     had all been written by then. This part changes one thing at a time.
  j. ``Urban Intersection Beijing.CN`` -- the ``Type`` write: does flipping
     species silently re-anchor the lot from a lane to a link, and does it
     mint a ``<zone>`` on a network that has none?

Hard rules honored: no git commands; no edits to ``services/`` from here (this
file is read/measure-only); work dirs confined to ``runtime/parking-lot-probe/``;
every connection is ``attach_to_running=False``; example networks are COPIED
into the work dir, never opened in place; ``runtime/uploads/`` is never touched.

---------------------------------------------------------------------------
FINDINGS (live, 2026-08-03/04, PTV Vissim 2026 on this machine; all ten parts
PASS)
---------------------------------------------------------------------------

Every number below is written into ``runtime/parking-lot-probe/state.json`` by
the probe itself; that file is the authority if this block ever drifts. Deltas
are AFTER minus BEFORE throughout. Numbers marked *(audit)* were additionally
re-derived by :func:`audit_state` straight from the persisted ``.inpx`` files,
a code path that re-derives them from the files alone.

**Scope of that claim, exactly:** it holds for A, C, D, G, I and J, which
recompute everything from persisted XML. It does NOT hold for F -- a COM
reading cannot be re-derived offline at all, so ``audit_state`` takes Part F's
recorded ``com_attrs`` values as the ratio NUMERATOR and only recomputes the
denominator (the persisted XML) itself. Part F's ratios are therefore
half-independent, and that half is stated here rather than left to the inline
comment at the call site.

**A0. The whole surface, from the type library (scan the CONTAINER first).**
``IParkingLotContainer`` exposes ``AddParkingLot(Key, Lane, Pos)``,
``AddAbstractParkingLot(Key, Link, Pos, Zone)``,
``DuplicateParkingLot(Source, NewKey)``, ``RemoveParkingLot(ParkingLot)``,
``ItemByKey``/``ItemKeyExists``/``GetAll``. Neighbours that exist and are
deliberately left alone: ``IZoneContainer.AddZone/RemoveZone/DuplicateZone``,
``IParkingLotGroupContainer.AddParkingLotGroup/...``,
``IVehicleRoutingDecisionParkingContainer.AddVehicleRoutingDecisionParking(Key,
Link, Pos)`` and ``IVehicleRouteParkingContainer.AddVehicleRouteParking(Key,
ParkLot)``. The decisive absence: **``IParkingSpaceContainer`` has NO
Add/Remove of any kind** -- individual parking SPACES are derived, never
placed.

**A1. Plain ``AddParkingLot`` produces a real object from nothing -- NOT a
duplicate-then-edit family member.** Urban Intersection Beijing.CN carries 0
parking lots and no ``<parkingLots>`` section at all; ``AddParkingLot(1, <link
1 lane 1>, 100.0)`` created lot 1, judged by a ``ParkingLots.GetAll()`` id-set
diff (``[]`` -> ``[1]``), never by a bare non-exception. The saved file
materialises the ``<parkingLots>`` section and one lot *(audit:
``a_container_created`` true, ``a_lots_in_saved_file`` 1)*, and ``load_check``
returns ``load_ok``/``init_ok``. Counts delta: ``{'parking_lots': +1}``.

**A2. A create is exactly ONE shadow-model row.** ``diff_shadow_models`` over
the persisted pair reports ``parking_lots: {'added': 1}`` (``1:added``) and
nothing else -- no conflict-area recompute (Q6 unit (1)'s reshape surprise), no
materialised sibling class (Q5 unit (3)'s ``vehicle_composition_entries``, Q5
unit (2)'s 44 ``signal_program_splits``). Hence
``_ALLOWS_NEW_OBJECTS_IN["add_parking_lot"] = {"parking_lots"}`` -- with the
counterfactual RUN, not assumed, in
``backend/tools/parking_lot_live_check.py``.

**A3. COM silently fills 25 defaults, and one of them is a REFERENCE.** A
fresh lot reads ``Capacity=1``, ``Length=6.0``, ``LenPerSpc=6.0``,
``ParkTmMax=99999``, ``OpenFrom=0``, ``OpenUntil=99999``,
``ParkDir='FORWARDINFORWARDOUT'``, ``SpeedForw=5.0``, ``SpeedRvs=5.0``,
``RelFlow=1.0``, ``Attrac=0.0``, ``DetBlock=1``, ``DestGrp=0``,
``InitOccup=0``, ``MinGapTmMajFl=5.0``, ``RoutDecDist=50.0``,
``InitOccupComp='1'`` -- and ``DesSpeedDistrDef='15'``. (The saved lot
carries 28 XML attributes in total; 3 of them -- ``no``, ``lane``, ``pos`` --
are the caller's own arguments, so 25 are defaults Vissim chose.) That last one is a
reference Vissim picks on the caller's behalf: ``'15'`` on Beijing (whose
``speedDistribution`` ids are 15/50/65/1101/1102 -- the lowest) and ``'1'`` on
Toll Plaza Camden.US (ids 1..11 + 1001..1007 -- again the lowest). It is
therefore never DANGLING, but it is never the caller's choice either. Q4 unit
(4)'s "COM silently fills defaults for unset optional fields" landmine, now
with a reference-valued instance: write verification claims only what the
caller supplied.

  *Recorded as WRONG, in the artifact itself:* Part B's
  ``state.json["part_b"]["silently_ignored"]`` lists FIVE attributes and only
  ONE of them (``Zone``) is a genuine no-op. ``ParkTmMax`` (99999 -> 1234),
  ``OpenFrom`` (0 -> 111), ``OpenUntil`` (99999 -> 8888) and ``DetBlock``
  (1 -> 0) all TOOK EFFECT and are misfiled, because :func:`_try_write`'s
  ``took`` flag is a STRING-equality proxy (``str(after) == str(value)``) and
  those four are int-typed attributes written with float/bool arguments --
  ``1234.0`` written, ``1234`` read back. The ``changed`` field on each write
  row is correct and shows the truth; ``writable``/``silently_ignored`` are
  the derived summaries that inherit the proxy's flaw. Same class of error as
  A6's route-set defect below: the raw per-write rows are right, a summary
  computed over them is not. Read ``writes[].changed``, not
  ``silently_ignored``.

**A4. ``Capacity`` is NOT writable -- it is DERIVED from ``Length`` /
``LenPerSpc``, ON ONE SPECIES ONLY.** ``SetAttValue("Capacity", 37)`` fails LOUDLY on a clean
REALPARKINGSPACE ("AttValue failed: Object 2: Attribute Capacity cannot be
changed"), with an untouched CONTROL lot in the same session proving nothing
moved. Measured stepwise on the treatment lot (control triple stayed
``6.0/6.0/1`` at every step):

  ===========================  ==============  ==============  ==========
  step                         Length          LenPerSpc       Capacity
  ===========================  ==============  ==============  ==========
  fresh                        6.0             6.0             1
  write Length 60              60.0            6.0             10
  write LenPerSpc 4            60.0            4.0             15
  write Length 0               0.0             1.0 (forced)    0
  ===========================  ==============  ==============  ==========

*(audit)* both persisted lots satisfy ``capacity == floor(length/lenPerSpc) ==
round(length/lenPerSpc)``.

**The derivation is TYPE-SCOPED, and the first version of this finding was
not.** It holds on 573/573 REALPARKINGSPACE lots in the corpus and is
violated by 56/56 ZONECONNECTORs (``capacity="10000"`` stored beside
``length="3"``/``lenPerSpc="6"``) and by A9's own ABSTRACTPARKINGLOT
(``length="6"``, ``lenPerSpc="6"``, ``capacity="100"``) -- on those two
species capacity is stored INDEPENDENTLY. ``set_parking_lot_capacity``
therefore refuses any species but REALPARKINGSPACE by name, rather than
writing a length that would change nothing and letting the readback compare
refuse opaquely.

**So the only way to give a caller "a 20-space parking lot" is to write
``Length = 20 x LenPerSpc``** -- which is exactly what
``add_parking_lot``/``set_parking_lot_capacity`` do, then read ``Capacity``
back to prove it.

**A5. Three edges on that same surface: one loud, two silent.**

  - **Loud, and usefully so.** A ``Length`` past the link's end is REFUSED with
    a diagnostic that names the link and its real length: writing 1005.3664
    (3x link 1's own ``Length2D`` of 335.12213607578946 m) raised "Parking Lot
    2 does not fit on link 1 (335.122 m). Please reduce its length or move
    it." The triple was unchanged afterwards.
  - **Silent clamp.** ``LenPerSpc`` LARGER than ``Length`` is neither applied
    nor refused: writing 1000.0 against a 60.0 m lot left ``LenPerSpc`` at
    **60.0** with NO exception, and ``Capacity`` fell 15 -> 1. A caller who
    asked for 1000 m bays and saw no error got 60 m bays. The FEET part hit
    the same clamp from the other side -- ``SetAttValue('LenPerSpc', 20.0)``
    on a 6.0-ft-long lot: ``before=6.0 after=6.0 changed=False``, no error at
    all.
  - **Silent zero.** ``Length = 0`` is ACCEPTED: ``Capacity`` 0, ``LenPerSpc``
    silently forced to 1.0, persisted as ``length="0" lenPerSpc="1"
    capacity="0"``, and ``load_check`` still returns ``load_ok``/``init_ok``.
    A zero-space parking lot Vissim never complains about -- the exact
    silent-lie shape this layer exists to refuse.

**A6. Removal SILENTLY CASCADE-DELETES the parking route that references the
lot.** 3D - Complex Intersection Karlsruhe.DE is the only corpus network with
REALPARKINGSPACE lots and parking routing decisions: 573 lots, 573
``vehicleRouteParking`` elements, 12 ``vehicleRoutingDecisionParking``, and
**every single lot referenced by exactly one route (0 unreferenced)**.
``RemoveParkingLot(lot 14)`` -- referenced by route 114 under decision 40 --
raised nothing, ``ItemByKey(14)`` then reported "object does not exist", and
the persisted counts moved ``{'parking_lots': -1, 'parking_routes': -1}``
*(audit: 573 -> 572 lots, 573 -> 572 route ELEMENTS, the lost row identified
exactly as ``(decision 40, route 114, parkLot 14)``, ``c_dangling_refs_after``
empty)*. ``load_check`` on the result: ``load_ok``/``init_ok``. This is the
THIRD species of the taxonomy -- Q4 unit (7)'s ``RemovePTStop`` cascade, not
``RemoveDataCollectionPoint``'s dangle and not a refusal. It is why
``remove_parking_lot`` refuses through
``inpx_structure.ID_REFERENCE_SITES["parking_lots"]`` instead of trusting COM.

  *Recorded as WRONG:* the live part's own "parking routes that vanished
  entirely" figure printed **0**, because it differenced SETS of route
  numbers and route numbers are not globally unique across decisions on this
  network. The independent audit path counts route ELEMENTS keyed by their
  owning decision and lands on -1, matching the count delta. The set-based
  live figure is a defect in this probe's own reporting helper, left in
  ``state.json`` as recorded and corrected here.

**A7. A clean removal is exactly ONE shadow-model row.** Control/treatment
inside one Beijing session: control saved with the fresh lot
(``parking_lots: 1``), treatment saved after ``RemoveParkingLot``
(``parking_lots: 0``), delta treatment-minus-control ``{'parking_lots': -1}``
*(audit: control ``["1"]`` -> treatment ``[]``)*, ``diff_shadow_models``
reports ``parking_lots: {'removed': 1}`` and nothing else, ``load_check``
green. Hence ``_ALLOWS_REMOVED_OBJECTS_IN["remove_parking_lot"] =
{"parking_lots"}``, counterfactual RUN.

**A8. ``Type`` is a writable STRUCTURAL mutation that mints a new object, and
it does not fully undo.** On a fresh Beijing lot with nothing else written,
``SetAttValue("Type", "ZONECONNECTOR")`` succeeded and, in the same instant:
``Lane`` went ``'1-1'`` -> ``None`` (the lane anchor was dropped), ``Zone``
went ``None`` -> ``'1'`` on a network with **zero** ``<zones>``, ``Capacity``
jumped 1 -> 100 (no longer ``Length``/``LenPerSpc``, which were still
6.0/6.0), and ``Net.Zones`` went ``[]`` -> ``[1]`` -- Vissim minted a zone.
Flipping BACK to ``REALPARKINGSPACE`` restored ``Lane='1-1'`` and
``Capacity=1`` but **left the minted zone behind**: persisted counts delta
``{'parking_lots': +1, 'zones': +1}`` and a lot carrying BOTH ``lane="1 1"``
and ``zone="1"`` *(audit: ``j_zones_in_source`` 0, ``j_zones_after`` 1)*. A
``type`` write is therefore not an attribute edit at all; it is a re-anchor
plus a silent creation in a class this shadow model does not represent.
Deliberately unwrapped -- no verb accepts a ``type`` parameter.

**A9. ``AddAbstractParkingLot`` works, and produces a THIRD type value the
corpus never shows.** ``Zones.AddZone(1)`` then ``AddAbstractParkingLot(1,
<link 1>, 100.0, <zone 1>)`` both succeeded on Beijing; the persisted lot reads
``type="ABSTRACTPARKINGLOT"`` with ``intLink="1"``, ``zone="1"``,
``capacity="100"`` -- where the entire 629-lot corpus only ever shows
``REALPARKINGSPACE`` (573) and ``ZONECONNECTOR`` (56). ``load_check`` green.
Deliberately unwrapped: it needs a ``<zone>`` (a class the shadow model does
not carry, so the verb would be unverifiable by construction), it only means
anything under dynamic assignment, and ``IntLink`` is not even readable over
COM without that module ("get_AttValue failed - module not active" on
Beijing).

**A10. Units: parking-lot lengths/positions are DISPLAY units in COM and
METRES in the XML -- the exact opposite of a link polyline.** On Toll Plaza
Camden.US (``UnitLenShort='FEET'``), COM/XML ratios on one fresh lot:
``Pos`` 29.999999999999996 / 9.144 = **3.280840**, ``Length`` 6.0 / 1.8288 =
**3.280840**, ``LenPerSpc`` 6.0 / 1.8288 = **3.280840**; and
``SpeedForw``/``SpeedRvs`` 3.1068559611866697 / 5.0 = **0.621371** (= 1 /
1.609344 -- COM reads mph where the XML stores km/h) *(audit recomputed
Pos/Length/SpeedForw from the persisted XML alone: 3.28084, 3.28084,
0.621371)*. Contrast Q6 unit (1) A8: a link polyline's ``X``/``Y`` are raw
metres in BOTH directions. Every parking verb converts through
``vissim_com``'s ``_length_m_to_com``/``_length_com_to_m``.

**A11. The null control.** Beijing loaded and saved with ZERO edits: counts
delta ``{}`` *(audit: ``g_counts_delta`` empty)* and ``diff_shadow_models``
reports no differences at all. Every diff attributed above is attributable to
its own write.

**A12. The skeleton question: a created lot is inert, and its own attribute
surface says so.** Beijing carries **0** parking routing decisions, and a
REALPARKINGSPACE is only reachable through one
(``vehicleRoutingDecisionParking`` -> ``vehicleRouteParking@parkLot``). The
lot has no plain occupancy attribute at all: ``Occup``, ``Occupancy``,
``NumSpaces``, ``ParkSpcs`` and ``ConcatLen`` each raise "PARKINGLOT: Could
not find attribute with ID ..."; ``OccupRate`` exists but is a
simulation-run-indexed RESULT attribute ("The subattribute no. 1 of type
'Simulation Run' of the attribute 'OCCUPRATE()' is invalid"). A 120 s
``RunContinuous`` completed without error and ``Capacity`` was 1 either side.

  *Honest limit, stated rather than papered over:* this part measured the
  NEGATIVE case only, and the ``0`` vehicles read after ``RunContinuous``
  returned is NOT evidence of anything -- the run had already ended (the
  sibling of 00-EXECUTION-GUIDE.md's SimSec-reset landmine). No positive
  control (a network WITH a parking routing decision, occupancy > 0) was run.
  What carries the claim is the corpus: the only network with
  REALPARKINGSPACE lots has exactly one parking route per lot (573/573, zero
  unreferenced), and the two ZONECONNECTOR networks have zero parking routes
  because they are driven by an OD matrix under dynamic assignment instead.
  In every real model a lot is reachable only through routing machinery this
  toolkit does not build -- hence the WARN, not a refusal.

**A13. The corpus, for the record** *(scan [0], no licence needed)*: 629
``<parkingLot>`` elements across 3 real ``.inpx`` files out of 21 example
networks -- 573 REALPARKINGSPACE in "3D - Complex Intersection Karlsruhe.DE"
and 28 ZONECONNECTOR in each of "Motorway Dyn Assign Mainz.DE"'s two variants.
REALPARKINGSPACE anchors on ``lane="<link> <laneIdx>"`` (573 -- exactly the
count Q6 unit (1) registered in ``LANE_TOKEN_REFERENCE_SITES``, reproduced
independently here); ZONECONNECTOR anchors on ``intLink="<link>"`` plus a
``zone`` reference and carries no lane at all.

Usage: ``python tools/probe_parking_lot.py`` -- runs every part end to end (via
internal per-part subprocesses) and prints one PASS/FAIL summary.
Advanced: ``--part {a..j} --state <path>`` runs exactly one part against a
persisted JSON state file (what the orchestrator shells out to); ``--only``
re-runs a comma-separated subset without clearing prior state; ``--static-only``
runs just the licence-free corpus scans; ``--audit`` re-derives every headline
number from the persisted .inpx pairs alone.
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "parking-lot-probe"

#: METERS network with ZERO parking lots and no ``<parkingLots>`` section at
#: all -- the from-nothing genesis case (this file's own static scan [0]).
METERS_NET = EXAMPLES_ROOT / "Urban Intersection Beijing.CN"
#: The ONLY corpus network with REALPARKINGSPACE lots (573) AND parking
#: routing decisions (12) that reference them -- the reference-integrity case.
PARKING_NET = EXAMPLES_ROOT / "3D - Complex Intersection Karlsruhe.DE"
#: FEET network -- the display-unit cross-check (Q4 unit (5) landmine 2).
FEET_NET = EXAMPLES_ROOT / "Toll Plaza Camden.US"

#: Beijing target: a plain multi-lane link carrying real demand, so Part H's
#: occupancy question has vehicles to answer it with.
BEIJING_LINK = 1
BEIJING_LANE = 1
BEIJING_POS_M = 100.0

_PART_LABELS = {
    "a": "Part A: AddParkingLot on a network with NO parking lots -- functional object or skeleton?",
    "b": "Part B: attribute writability sweep on a fresh lot (readback-judged)",
    "c": "Part C: RemoveParkingLot on a lot a parking ROUTE references -- refuse, dangle, or cascade?",
    "d": "Part D: RemoveParkingLot on a CLEAN lot -- persisted diff + load oracle",
    "e": "Part E: AddAbstractParkingLot + Zones.AddZone -- is the ZONECONNECTOR species reachable?",
    "f": "Part F: FEET cross-check -- Pos/Length/LenPerSpc display-unit scaling, read AND write",
    "g": "Part G: NULL CONTROL -- load + save with zero edits; nothing may change",
    "h": "Part H: SKELETON question -- create a lot, run, read occupancy",
    "i": "Part I: capacity <- length / lenPerSpc -- the derived-capacity relation, isolated",
    "j": "Part J: the Type write -- does flipping species silently re-anchor the lot and mint a zone?",
}
_PART_ORDER = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]

_M_PER_FT = 0.3048

#: Every attribute a REAL corpus ``<parkingLot>`` carries, from scan [0]. The
#: probe reads/writes against COM ids, which are NOT the XML attribute names --
#: the mapping is measured in Part A, not assumed.
_XML_ATTRS = (
    "no", "name", "type", "lane", "intLink", "zone", "pos", "length", "capacity",
    "lenPerSpc", "attrac", "attracLast", "relFlow", "parkTmMax", "parkFeeFlat",
    "parkFeeHr", "openFrom", "openUntil", "parkDir", "speedForw", "speedRvs",
    "desSpeedDistrDef", "dirChgDurDist", "detBlock", "destGrp", "initOccup",
    "initOccupComp", "minGapTmMajFl", "routDecDist", "anmConnNodeNo", "anmConnSrc",
)

#: COM attribute-id candidates to probe defensively on a live IParkingLot.
#: Vissim's COM ids are abbreviations that only sometimes match the XML
#: attribute name -- every one is read through :func:`_read_attrs`, so a
#: non-existent id shows up as a NAMED failure, never a confident wrong value.
_COM_ATTR_CANDIDATES = (
    "No", "Name", "Type", "Lane", "Link", "IntLink", "Zone", "Pos", "Length",
    "Capacity", "LenPerSpc", "Attrac", "AttracLast", "RelFlow", "ParkTmMax",
    "ParkFeeFlat", "ParkFeeHr", "OpenFrom", "OpenUntil", "ParkDir", "SpeedForw",
    "SpeedRvs", "DesSpeedDistrDef", "DirChgDurDist", "DetBlock", "DestGrp",
    "InitOccup", "InitOccupComp", "MinGapTmMajFl", "RoutDecDist", "Occup",
    "OccupRate", "Occupancy", "NumSpaces", "ParkSpcs", "ConcatLen",
)


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
    """Copy the whole example folder (sibling assets matter) into our own work
    dir and return the .inpx inside it -- the example is NEVER opened in place
    (00-EXECUTION-GUIDE.md rule 4)."""
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


def _lots(root: ET.Element) -> list[ET.Element]:
    return root.findall(".//parkingLots/parkingLot")


def _lot_map(inpx: Path) -> dict[str, dict[str, str]]:
    """{lot_no: full attrib dict} for one persisted .inpx -- the cheap oracle
    every live measurement below is compared against."""
    root = ET.parse(inpx).getroot()
    return {el.get("no", ""): dict(el.attrib) for el in _lots(root)}


def _parking_route_refs(inpx: Path) -> dict[str, list[str]]:
    """{parking-lot no: [referring vehicleRouteParking nos]} from the persisted
    file -- the reference-integrity question Part C answers, measured
    statically so 'the route vanished' is a diffable claim."""
    root = ET.parse(inpx).getroot()
    out: dict[str, list[str]] = {}
    for route in root.findall(".//vehicleRouteParking"):
        lot = route.get("parkLot") or ""
        out.setdefault(lot, []).append(route.get("no", ""))
    return out


def _counts(inpx: Path) -> dict[str, int]:
    """Object counts for every class a parking edit could plausibly disturb."""
    root = ET.parse(inpx).getroot()
    return {
        "parking_lots": len(_lots(root)),
        "parking_lot_groups": len(root.findall(".//parkLotGrps/parkingLotGroup")),
        "zones": len(root.findall(".//zones/zone")),
        "parking_routing_decisions": len(root.findall(".//vehicleRoutingDecisionParking")),
        "parking_routes": len(root.findall(".//vehicleRouteParking")),
        "links": len(root.findall(".//links/link")),
        "conflict_areas": len(root.findall(".//conflictAreas/conflictArea")),
        "vehicle_inputs": len(root.findall(".//vehicleInputs/vehicleInput")),
        "time_distributions": len(root.findall(".//timeDistributions/timeDistribution")),
        "speed_distributions": len(root.findall(".//speedDistributions/speedDistribution")),
        "des_speed_distributions": len(root.findall(".//desSpeedDistributions/desSpeedDistribution")),
    }


def static_corpus_scan() -> dict[str, Any]:
    """What a real ``<parkingLot>`` IS, across the whole 2026 example corpus:
    which networks have any, the per-TYPE attribute schema, and which other
    elements reference a lot by id.

    This is the representation evidence -- the shadow model had no parking-lot
    class at all before this unit, so 'what fields exist' had to be measured
    from real exports rather than invented from the COM interface's own
    vocabulary (the two disagree: XML ``parkTmMax`` vs COM ``ParkTmMax``, XML
    ``lane`` vs COM ``Lane``, and several COM ids have no XML twin at all --
    Part A measures the mapping).
    """
    _log("=" * 78)
    _log("[0] STATIC corpus scan: parkingLot schema + referrers across Examples Demo")
    _log("=" * 78)
    per_network: dict[str, Any] = {}
    attr_totals: dict[str, int] = {}
    type_totals: dict[str, int] = {}
    attrs_by_type: dict[str, dict[str, int]] = {}
    referrer_totals: dict[str, int] = {}
    value_samples: dict[str, dict[str, int]] = {}

    for inpx in sorted(EXAMPLES_ROOT.rglob("*.inpx")):
        try:
            root = ET.parse(inpx).getroot()
        except Exception as exc:
            _log(f"      {inpx.parent.name}: PARSE FAILED ({type(exc).__name__}) -- skipped")
            continue
        lots = _lots(root)
        key = f"{inpx.parent.name}/{inpx.name}"
        entry = {
            "lots": len(lots),
            "has_container": root.find("parkingLots") is not None,
            "counts": _counts(inpx),
        }
        per_network[key] = entry
        if not lots:
            continue
        lot_ids = {el.get("no") for el in lots}
        for lot in lots:
            lot_type = lot.get("type", "<absent>")
            type_totals[lot_type] = type_totals.get(lot_type, 0) + 1
            attrs_by_type.setdefault(lot_type, {})
            for name, value in lot.attrib.items():
                attr_totals[name] = attr_totals.get(name, 0) + 1
                attrs_by_type[lot_type][name] = attrs_by_type[lot_type].get(name, 0) + 1
                bucket = value_samples.setdefault(name, {})
                bucket[value] = bucket.get(value, 0) + 1

        # Who references a parking lot BY ID? Walked with the parkingLot
        # elements themselves excluded, so a lot's own "no" is never counted
        # as a reference to itself.
        def walk(element: ET.Element, chain: list[str]) -> None:
            chain = chain + [element.tag]
            if element.tag == "parkingLot":
                return
            for name, value in element.attrib.items():
                if value in lot_ids:
                    label = f"{'/'.join(chain[-3:])}@{name}"
                    referrer_totals[label] = referrer_totals.get(label, 0) + 1
            for child in element:
                walk(child, chain)

        walk(root, [])

    _log("      networks carrying parking lots:")
    for key, entry in sorted(per_network.items(), key=lambda kv: -kv[1]["lots"]):
        if entry["lots"] or entry["has_container"]:
            _log(f"        {key:52s} lots={entry['lots']:4d}  counts={entry['counts']}")
    _log(f"      lot TYPE totals: {type_totals}")
    _log("      attributes by type:")
    for lot_type, attrs in sorted(attrs_by_type.items()):
        _log(f"        {lot_type}: {sorted(attrs)}")
    _log("      candidate referrers (attribute VALUE == some lot no; includes coincidences):")
    for label, count in sorted(referrer_totals.items(), key=lambda kv: -kv[1])[:15]:
        _log(f"        {label:70s} {count}")
    return {
        "per_network": per_network,
        "attr_totals": attr_totals,
        "type_totals": type_totals,
        "attrs_by_type": attrs_by_type,
        "referrer_candidates": referrer_totals,
        "value_samples": {k: dict(sorted(v.items(), key=lambda kv: -kv[1])[:6]) for k, v in value_samples.items()},
    }


def static_route_scan() -> dict[str, Any]:
    """The ONE genuine reference site, isolated from the coincidences scan [0]
    reports: ``vehicleRouteParking@parkLot``. Counted per network, plus how
    many lots are referenced by at least one route (the removal-cascade
    blast radius)."""
    _log("=" * 78)
    _log("[0b] STATIC scan: vehicleRouteParking@parkLot -- the real reference site")
    _log("=" * 78)
    rows: list[dict[str, Any]] = []
    for inpx in sorted(EXAMPLES_ROOT.rglob("*.inpx")):
        try:
            root = ET.parse(inpx).getroot()
        except Exception:
            continue
        lots = {el.get("no", "") for el in _lots(root)}
        if not lots:
            continue
        refs = _parking_route_refs(inpx)
        referenced = {lot for lot in refs if lot in lots}
        row = {
            "network": f"{inpx.parent.name}/{inpx.name}",
            "lots": len(lots),
            "parking_routes": sum(len(v) for v in refs.values()),
            "lots_referenced_by_a_route": len(referenced),
            "unreferenced_lots": len(lots - referenced),
        }
        rows.append(row)
        _log(f"      {row['network']:52s} lots={row['lots']:4d} routes={row['parking_routes']:4d} "
             f"referenced={row['lots_referenced_by_a_route']:4d} free={row['unreferenced_lots']:4d}")
    return {"rows": rows}


def _scan_typelib_methods(keywords: tuple[str, ...]) -> dict[str, list[str]]:
    """Static type-library scan (no COM instance, no licence used) -- the house
    'scan the CONTAINER first rather than assume' step Q4 unit (5) recorded
    after unit (3) missed ``ILinkContainer.AddLink`` entirely."""
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

    by_class: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("class I") and "(DispatchBaseClass)" in stripped:
            class_name = stripped.split("(")[0].removeprefix("class ").strip()
            current = class_name if class_name.lower() in keywords else None
            if current:
                by_class[current] = []
                _log(f"\n      -- {current} (DispatchBaseClass) --")
            continue
        if current and stripped.startswith("def ") and "(self" in stripped:
            _log(f"        {stripped}")
            by_class[current].append(stripped)
    return by_class


# ---------------------------------------------------------------------------
# Live helpers.
# ---------------------------------------------------------------------------


def _read_attrs(obj: Any, names: tuple[str, ...]) -> dict[str, Any]:
    """Read every candidate attribute defensively -- an attribute that does not
    exist on this Vissim version must show up as a NAMED failure, never as a
    confident wrong value (Q5 unit (1)'s harness-landmine-2 lesson)."""
    out: dict[str, Any] = {}
    for name in names:
        try:
            value = obj.AttValue(name)
            out[name] = {"value": value, "pytype": type(value).__name__}
        except Exception as exc:
            out[name] = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    return out


def _try_write(obj: Any, name: str, value: Any, read_after: Any = None) -> dict[str, Any]:
    """One SetAttValue attempt, judged by a READBACK (never by the bare absence
    of an exception -- ``AddPriorityRule``'s silent-no-op precedent)."""
    before: Any = None
    try:
        before = obj.AttValue(name)
    except Exception as exc:
        before = f"<unreadable: {type(exc).__name__}>"
    error = ""
    try:
        obj.SetAttValue(name, value)
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
    after: Any = None
    try:
        after = read_after() if callable(read_after) else obj.AttValue(name)
    except Exception as exc:
        after = f"<unreadable: {type(exc).__name__}>"
    changed = str(before) != str(after)
    took = changed and str(after) == str(value)
    _log(f"      SetAttValue({name!r}, {value!r}): before={before!r} after={after!r} "
         f"changed={changed} took={took}" + (f" error={error}" if error else ""))
    return {"attribute": name, "wrote": value, "before": before, "after": after,
            "changed": changed, "took": took, "error": error}


def _lot_nos(net: Any) -> list[int]:
    out: list[int] = []
    for lot in net.ParkingLots.GetAll():
        try:
            out.append(int(lot.AttValue("No")))
        except Exception:
            continue
    return sorted(out)


def _grouped_diff(before_model: dict[str, Any], after_model: dict[str, Any]) -> dict[str, Any]:
    """``diff_shadow_models`` (the SAME machinery ``GuardrailEngine.postwrite``
    runs), grouped by object class and printed."""
    from services.agent.guardrails import diff_shadow_models

    raw = diff_shadow_models(before_model, after_model)
    by_class: dict[str, dict[str, int]] = {}
    samples: dict[str, list[str]] = {}
    for entry in raw:
        cls = entry["object_class"]
        change = entry["change"]
        by_class.setdefault(cls, {})
        by_class[cls][change] = by_class[cls].get(change, 0) + 1
        label = f"{entry['object_id']}:{change}"
        if change == "field_changed":
            label += f".{entry.get('field')}({entry.get('before')!r}->{entry.get('after')!r})"
        samples.setdefault(cls, []).append(label)
    for cls, counts in sorted(by_class.items()):
        _log(f"        {cls}: {counts} -- {samples[cls][:5]}{' ...' if len(samples[cls]) > 5 else ''}")
    if not by_class:
        _log("        (no differences at all)")
    return {"by_class": by_class, "samples": {k: v[:8] for k, v in samples.items()}, "total": len(raw)}


def _connect(working: Path) -> Any:
    from services.vissim_com import VissimController

    controller = VissimController()
    controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
    controller.load_network(str(working))
    return controller


def _finish(controller: Any) -> None:
    try:
        controller.disconnect()
    except Exception:
        pass


def _load_oracle(inpx: Path) -> dict[str, Any]:
    from services.inpx_structure import run_load_oracle

    result = run_load_oracle(inpx)
    _log(f"      load oracle: load_ok={result.get('load_ok')} init_ok={result.get('init_ok')} "
         f"load_error={str(result.get('load_error'))[:120]!r} "
         f"init_error={str(result.get('init_error'))[:120]!r}")
    return result


# ---------------------------------------------------------------------------
# Part A: does plain AddParkingLot produce a real object?
# ---------------------------------------------------------------------------


def part_a(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.tools_network import build_shadow_model

    _log("=" * 78)
    _log(f"[A] {_PART_LABELS['a']}")
    _log("=" * 78)

    _log("\n[A.0] Static type-library scan: the parking/zone CONTAINERS ...")
    signatures = _scan_typelib_methods(
        ("iparkinglot", "iparkinglotcontainer", "iparkinglotcollection", "iparkinglotgroup",
         "iparkinglotgroupcontainer", "izone", "izonecontainer", "iparkingspace",
         "iparkingspacecontainer", "ivehicleroutingdecisionparkingcontainer",
         "ivehicleparkingroutecontainer", "ivehicleroutepakingcontainer",
         "ivehiclerouteparkingcontainer")
    )

    working = _clone(METERS_NET, "add")
    counts_before = _counts(working)
    lots_before_xml = _lot_map(working)
    shadow_before = build_shadow_model(working)
    _log(f"\n    clone: {working}")
    _log(f"    BEFORE (persisted): {counts_before}")

    findings: dict[str, Any] = {
        "network": METERS_NET.name,
        "typelib": signatures,
        "counts_before": counts_before,
        "lots_before_xml": lots_before_xml,
        "target_link": BEIJING_LINK,
        "target_lane": BEIJING_LANE,
        "requested_pos_m": BEIJING_POS_M,
    }

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        unit = controller._net_unit_length()
        findings["unit_len_short"] = unit
        _log(f"\n[A.1] display unit (UnitLenShort): {unit!r}")

        nos_before = _lot_nos(net)
        _log(f"[A.2] live ParkingLots BEFORE: {nos_before} (len={len(nos_before)})")
        findings["com_lot_nos_before"] = nos_before

        lane = controller._lane_by_index(BEIJING_LINK, BEIJING_LANE)
        new_no = (max(nos_before) if nos_before else 0) + 1
        pos_com = controller._length_m_to_com(BEIJING_POS_M, unit)
        _log(f"\n[A.3] AddParkingLot(Key={new_no}, Lane=<link {BEIJING_LINK} lane {BEIJING_LANE}>, "
             f"Pos={pos_com}) ...")
        add_error = ""
        try:
            net.ParkingLots.AddParkingLot(new_no, lane, pos_com)
        except Exception as exc:
            add_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            _log(f"      RAISED: {add_error}")
        findings["add_error"] = add_error

        nos_after = _lot_nos(net)
        findings["com_lot_nos_after"] = nos_after
        findings["created_by_id_set_diff"] = sorted(set(nos_after) - set(nos_before))
        _log(f"[A.4] live ParkingLots AFTER: {nos_after} -- created (id-set diff, never a bare "
             f"non-exception): {findings['created_by_id_set_diff']}")
        if not findings["created_by_id_set_diff"]:
            state["part_a"] = findings
            return False

        lot = net.ParkingLots.ItemByKey(new_no)
        attrs = _read_attrs(lot, _COM_ATTR_CANDIDATES)
        _log("\n[A.5] every candidate COM attribute on the FRESH lot (defensive reads):")
        for name, result in attrs.items():
            _log(f"      {name:18s} {result}")
        findings["fresh_lot_com_attrs"] = attrs

        saved = working.with_name("after_add.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved is None or not saved.exists():
        state["part_a"] = findings
        return False

    counts_after = _counts(saved)
    lots_after_xml = _lot_map(saved)
    findings["counts_after"] = counts_after
    findings["lots_after_xml"] = lots_after_xml
    findings["new_lot_xml"] = lots_after_xml.get(str(new_no))
    findings["new_lot_no"] = new_no
    _log(f"\n[A.6] persisted AFTER: {counts_after}")
    _log(f"      the NEW lot's persisted XML: {findings['new_lot_xml']}")
    _log(f"      counts delta (after-minus-before): "
         f"{ {k: counts_after[k] - counts_before[k] for k in counts_before if counts_after[k] != counts_before[k]} }")

    findings["load_check"] = _load_oracle(saved)

    _log("\n[A.7] shadow diff (the same diff_shadow_models GuardrailEngine.postwrite runs):")
    shadow_after = build_shadow_model(saved)
    findings["shadow_diff"] = _grouped_diff(shadow_before, shadow_after)

    state["part_a"] = findings
    return bool(findings["created_by_id_set_diff"]) and bool(findings["load_check"].get("init_ok"))


# ---------------------------------------------------------------------------
# Part B: which attributes are actually writable?
# ---------------------------------------------------------------------------


def part_b(state: dict[str, Any]) -> bool:
    import pythoncom

    _log("=" * 78)
    _log(f"[B] {_PART_LABELS['b']}")
    _log("=" * 78)

    working = _clone(METERS_NET, "writability")
    findings: dict[str, Any] = {"network": METERS_NET.name}

    #: (COM id, value to write) -- one probe per candidate. Values chosen to be
    #: unmistakably different from any plausible default so a readback that
    #: matches cannot be a coincidence.
    write_plan = [
        ("Name", "PROBE LOT"),
        ("Capacity", 37),
        ("Attrac", 42.5),
        ("ParkTmMax", 1234.0),
        ("ParkFeeFlat", 7.25),
        ("ParkFeeHr", 3.75),
        ("OpenFrom", 111.0),
        ("OpenUntil", 8888.0),
        ("LenPerSpc", 5.75),
        ("RelFlow", 0.25),
        ("Pos", 55.5),
        ("Length", 99.0),
        ("SpeedForw", 4.5),
        ("SpeedRvs", 2.5),
        ("DetBlock", False),
        ("RoutDecDist", 77.0),
        ("MinGapTmMajFl", 6.5),
        ("InitOccup", 3),
        ("DestGrp", 2),
        ("Type", "ZONECONNECTOR"),
        ("Zone", 1),
    ]

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    new_no = None
    try:
        net = controller.vissim.Net
        unit = controller._net_unit_length()
        lane = controller._lane_by_index(BEIJING_LINK, BEIJING_LANE)
        nos_before = _lot_nos(net)
        new_no = (max(nos_before) if nos_before else 0) + 1
        net.ParkingLots.AddParkingLot(new_no, lane, controller._length_m_to_com(BEIJING_POS_M, unit))
        lot = net.ParkingLots.ItemByKey(new_no)

        _log("\n[B.1] defaults on a fresh lot, BEFORE any write:")
        defaults = _read_attrs(lot, tuple(name for name, _ in write_plan))
        for name, result in defaults.items():
            _log(f"      {name:18s} {result}")
        findings["defaults"] = defaults

        _log("\n[B.2] one SetAttValue per candidate, each judged by a readback:")
        writes = [_try_write(lot, name, value) for name, value in write_plan]
        findings["writes"] = writes
        findings["writable"] = [w["attribute"] for w in writes if w["took"]]
        findings["refused"] = [w["attribute"] for w in writes if w["error"]]
        findings["silently_ignored"] = [
            w["attribute"] for w in writes if not w["error"] and not w["took"]
        ]

        # Capacity/LenPerSpc/Length coupling: is Length derived?
        _log("\n[B.3] is Length DERIVED from Capacity x LenPerSpc? (re-read all three together)")
        coupling = _read_attrs(lot, ("Capacity", "LenPerSpc", "Length"))
        _log(f"      {coupling}")
        findings["coupling_after_writes"] = coupling

        saved = working.with_name("after_writes.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved and saved.exists():
        findings["persisted_lot_xml"] = _lot_map(saved).get(str(new_no))
        _log(f"\n[B.4] the lot's persisted XML after every write: {findings['persisted_lot_xml']}")
        findings["load_check"] = _load_oracle(saved)

    state["part_b"] = findings
    return bool(findings.get("writable"))


# ---------------------------------------------------------------------------
# Part C: removal with a parking ROUTE referencing the lot.
# ---------------------------------------------------------------------------


def part_c(state: dict[str, Any]) -> bool:
    import pythoncom

    _log("=" * 78)
    _log(f"[C] {_PART_LABELS['c']}")
    _log("=" * 78)

    working = _clone(PARKING_NET, "remove-referenced")
    counts_before = _counts(working)
    refs_before = _parking_route_refs(working)
    lots_before = _lot_map(working)
    referenced = sorted(
        (lot for lot in refs_before if lot in lots_before),
        key=lambda s: int(s) if s.isdigit() else 0,
    )
    target = referenced[0] if referenced else None
    _log(f"    clone: {working}")
    _log(f"    BEFORE: {counts_before}")
    _log(f"    lots referenced by >=1 parking route: {len(referenced)}; target lot = {target} "
         f"(routes {refs_before.get(target or '', [])})")

    findings: dict[str, Any] = {
        "network": PARKING_NET.name,
        "counts_before": counts_before,
        "target_lot": target,
        "routes_referencing_target_before": refs_before.get(target or "", []),
        "lot_before_xml": lots_before.get(target or ""),
    }
    if target is None:
        state["part_c"] = findings
        return False

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        nos_before = _lot_nos(net)
        findings["com_lot_nos_before_len"] = len(nos_before)
        lot = net.ParkingLots.ItemByKey(int(target))
        findings["target_attrs_before"] = _read_attrs(lot, _COM_ATTR_CANDIDATES)

        _log(f"\n[C.1] RemoveParkingLot(lot {target}) -- a lot "
             f"{len(findings['routes_referencing_target_before'])} parking route(s) reference ...")
        remove_error = ""
        try:
            net.ParkingLots.RemoveParkingLot(lot)
        except Exception as exc:
            remove_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            _log(f"      RAISED: {remove_error}")
        findings["remove_error"] = remove_error

        nos_after = _lot_nos(net)
        findings["removed_by_id_set_diff"] = sorted(set(nos_before) - set(nos_after))
        _log(f"[C.2] removed (id-set diff): {findings['removed_by_id_set_diff']}")

        try:
            net.ParkingLots.ItemByKey(int(target))
            findings["item_by_key_after"] = "still resolves"
        except Exception as exc:
            findings["item_by_key_after"] = f"{type(exc).__name__}: {str(exc)[:140]}"
        _log(f"      ItemByKey({target}) right after removal: {findings['item_by_key_after']}")

        saved = working.with_name("after_remove_referenced.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved is None or not saved.exists():
        state["part_c"] = findings
        return False

    counts_after = _counts(saved)
    refs_after = _parking_route_refs(saved)
    findings["counts_after"] = counts_after
    findings["routes_referencing_target_after"] = refs_after.get(target, [])
    findings["counts_delta"] = {
        k: counts_after[k] - counts_before[k] for k in counts_before if counts_after[k] != counts_before[k]
    }
    surviving_routes = {r for rs in refs_after.values() for r in rs}
    lost_routes = sorted(
        {r for rs in refs_before.values() for r in rs} - surviving_routes,
        key=lambda s: int(s) if s.isdigit() else 0,
    )
    findings["parking_routes_lost"] = lost_routes[:20]
    findings["parking_routes_lost_count"] = len(lost_routes)
    findings["dangling_after"] = [
        (lot, routes) for lot, routes in refs_after.items() if lot not in _lot_map(saved)
    ][:20]
    _log(f"\n[C.3] persisted AFTER: {counts_after}")
    _log(f"      counts delta (after-minus-before): {findings['counts_delta']}")
    _log(f"      routes still naming lot {target}: {findings['routes_referencing_target_after']}")
    _log(f"      parking routes that vanished entirely: {findings['parking_routes_lost_count']} "
         f"{findings['parking_routes_lost']}")
    _log(f"      DANGLING (route names a lot that no longer exists): {findings['dangling_after']}")

    findings["load_check"] = _load_oracle(saved)

    state["part_c"] = findings
    return bool(findings["removed_by_id_set_diff"]) or bool(findings["remove_error"])


# ---------------------------------------------------------------------------
# Part D: removal of a CLEAN lot (control/treatment inside one session).
# ---------------------------------------------------------------------------


def part_d(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.tools_network import build_shadow_model

    _log("=" * 78)
    _log(f"[D] {_PART_LABELS['d']}")
    _log("=" * 78)

    working = _clone(METERS_NET, "remove-clean")
    findings: dict[str, Any] = {"network": METERS_NET.name}

    pythoncom.CoInitialize()
    controller = _connect(working)
    control: Path | None = None
    treatment: Path | None = None
    new_no = None
    try:
        net = controller.vissim.Net
        unit = controller._net_unit_length()
        lane = controller._lane_by_index(BEIJING_LINK, BEIJING_LANE)
        nos_before = _lot_nos(net)
        new_no = (max(nos_before) if nos_before else 0) + 1
        net.ParkingLots.AddParkingLot(new_no, lane, controller._length_m_to_com(BEIJING_POS_M, unit))
        control = working.with_name("control_with_lot.inpx")
        controller.save_net_as(control)
        _log(f"\n[D.1] CONTROL saved with the fresh lot {new_no}: {_counts(control)}")

        lot = net.ParkingLots.ItemByKey(new_no)
        nos_mid = _lot_nos(net)
        remove_error = ""
        try:
            net.ParkingLots.RemoveParkingLot(lot)
        except Exception as exc:
            remove_error = f"{type(exc).__name__}: {str(exc)[:300]}"
        findings["remove_error"] = remove_error
        nos_after = _lot_nos(net)
        findings["removed_by_id_set_diff"] = sorted(set(nos_mid) - set(nos_after))
        _log(f"[D.2] RemoveParkingLot({new_no}) -> removed (id-set diff): "
             f"{findings['removed_by_id_set_diff']} error={remove_error!r}")

        treatment = working.with_name("treatment_lot_removed.inpx")
        controller.save_net_as(treatment)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    findings["new_lot_no"] = new_no
    if control and control.exists() and treatment and treatment.exists():
        findings["counts_control"] = _counts(control)
        findings["counts_treatment"] = _counts(treatment)
        findings["counts_delta"] = {
            k: findings["counts_treatment"][k] - findings["counts_control"][k]
            for k in findings["counts_control"]
            if findings["counts_treatment"][k] != findings["counts_control"][k]
        }
        _log(f"\n[D.3] control={findings['counts_control']}")
        _log(f"      treatment={findings['counts_treatment']}")
        _log(f"      delta (treatment-minus-control): {findings['counts_delta']}")
        findings["load_check"] = _load_oracle(treatment)
        _log("\n[D.4] shadow diff control -> treatment (what a remove verb's postwrite would see):")
        findings["shadow_diff"] = _grouped_diff(
            build_shadow_model(control), build_shadow_model(treatment)
        )

    state["part_d"] = findings
    return bool(findings.get("removed_by_id_set_diff"))


# ---------------------------------------------------------------------------
# Part E: the ZONECONNECTOR species.
# ---------------------------------------------------------------------------


def part_e(state: dict[str, Any]) -> bool:
    import pythoncom

    _log("=" * 78)
    _log(f"[E] {_PART_LABELS['e']}")
    _log("=" * 78)

    working = _clone(METERS_NET, "abstract")
    findings: dict[str, Any] = {"network": METERS_NET.name, "counts_before": _counts(working)}

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    new_no = None
    try:
        net = controller.vissim.Net
        unit = controller._net_unit_length()

        zones_before = []
        try:
            zones_before = sorted(int(z.AttValue("No")) for z in net.Zones.GetAll())
        except Exception as exc:
            findings["zones_read_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        findings["zones_before"] = zones_before
        _log(f"\n[E.1] Zones BEFORE: {zones_before}")

        zone_no = (max(zones_before) if zones_before else 0) + 1
        zone_error = ""
        zone_obj = None
        try:
            zone_obj = net.Zones.AddZone(zone_no)
        except Exception as exc:
            zone_error = f"{type(exc).__name__}: {str(exc)[:300]}"
        findings["add_zone_error"] = zone_error
        try:
            findings["zones_after"] = sorted(int(z.AttValue("No")) for z in net.Zones.GetAll())
        except Exception:
            findings["zones_after"] = []
        _log(f"[E.2] AddZone({zone_no}): error={zone_error!r} zones now {findings['zones_after']}")

        link = controller._link_object_by_no(BEIJING_LINK)
        nos_before = _lot_nos(net)
        new_no = (max(nos_before) if nos_before else 0) + 1
        abstract_error = ""
        try:
            net.ParkingLots.AddAbstractParkingLot(
                new_no, link, controller._length_m_to_com(BEIJING_POS_M, unit), zone_obj
            )
        except Exception as exc:
            abstract_error = f"{type(exc).__name__}: {str(exc)[:300]}"
        findings["add_abstract_error"] = abstract_error
        nos_after = _lot_nos(net)
        findings["created_by_id_set_diff"] = sorted(set(nos_after) - set(nos_before))
        _log(f"[E.3] AddAbstractParkingLot({new_no}, link {BEIJING_LINK}, pos, zone {zone_no}): "
             f"error={abstract_error!r} created={findings['created_by_id_set_diff']}")

        if findings["created_by_id_set_diff"]:
            lot = net.ParkingLots.ItemByKey(new_no)
            findings["abstract_lot_attrs"] = _read_attrs(lot, _COM_ATTR_CANDIDATES)
            saved = working.with_name("after_abstract.inpx")
            controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved and saved.exists():
        findings["counts_after"] = _counts(saved)
        findings["abstract_lot_xml"] = _lot_map(saved).get(str(new_no))
        _log(f"\n[E.4] persisted: {findings['counts_after']}")
        _log(f"      the abstract lot's XML: {findings['abstract_lot_xml']}")
        findings["load_check"] = _load_oracle(saved)

    state["part_e"] = findings
    # This part PASSES whatever the answer is -- a measured "not reachable" is
    # as valuable as a measured "reachable"; it only fails if it could not
    # measure at all.
    return "add_abstract_error" in findings


# ---------------------------------------------------------------------------
# Part F: FEET cross-check.
# ---------------------------------------------------------------------------


def part_f(state: dict[str, Any]) -> bool:
    import pythoncom

    _log("=" * 78)
    _log(f"[F] {_PART_LABELS['f']}")
    _log("=" * 78)

    working = _clone(FEET_NET, "feet")
    findings: dict[str, Any] = {"network": FEET_NET.name}

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    new_no = None
    try:
        net = controller.vissim.Net
        unit = controller._net_unit_length()
        findings["unit_len_short"] = unit
        _log(f"\n[F.1] display unit: {unit!r}")

        link_nos = controller._collection_nos(net.Links)
        target_link = None
        for candidate in link_nos:
            try:
                if controller.available_lane_indices(candidate):
                    length = float(net.Links.ItemByKey(candidate).AttValue("Length2D"))
                    if length > 120.0:
                        target_link = candidate
                        break
            except Exception:
                continue
        findings["target_link"] = target_link
        if target_link is None:
            state["part_f"] = findings
            return False

        lane = controller._lane_by_index(target_link, 1)
        nos_before = _lot_nos(net)
        new_no = (max(nos_before) if nos_before else 0) + 1
        # Pass the position RAW (no conversion) so the ratio against the
        # persisted XML measures the display-unit space directly.
        raw_pos = 30.0
        net.ParkingLots.AddParkingLot(new_no, lane, raw_pos)
        findings["raw_pos_written"] = raw_pos
        lot = net.ParkingLots.ItemByKey(new_no)
        findings["com_attrs"] = _read_attrs(
            lot, ("Pos", "Length", "LenPerSpc", "Capacity", "Lane", "SpeedForw", "SpeedRvs")
        )
        _log(f"[F.2] fresh lot COM values: { {k: v.get('value') for k, v in findings['com_attrs'].items()} }")

        findings["lenperspc_write"] = _try_write(lot, "LenPerSpc", 20.0)
        saved = working.with_name("after_feet.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved and saved.exists():
        persisted = _lot_map(saved).get(str(new_no)) or {}
        findings["persisted_lot_xml"] = persisted
        ratios: dict[str, Any] = {}
        for com_id, xml_attr in (("Pos", "pos"), ("Length", "length"), ("LenPerSpc", "lenPerSpc"),
                                 ("SpeedForw", "speedForw"), ("SpeedRvs", "speedRvs")):
            com_value = (findings["com_attrs"].get(com_id) or {}).get("value")
            xml_value = persisted.get(xml_attr)
            if com_id == "LenPerSpc":
                com_value = findings["lenperspc_write"]["after"]
            try:
                ratios[com_id] = {
                    "com": float(com_value),
                    "xml": float(xml_value),
                    "ratio": round(float(com_value) / float(xml_value), 6) if float(xml_value) else None,
                }
            except (TypeError, ValueError):
                ratios[com_id] = {"com": com_value, "xml": xml_value, "ratio": None}
        findings["com_over_xml_ratios"] = ratios
        findings["ft_per_m"] = round(1.0 / _M_PER_FT, 6)
        _log(f"\n[F.3] persisted XML: {persisted}")
        _log(f"      COM/XML ratios (1.0 = raw, {findings['ft_per_m']} = display-unit-scaled):")
        for name, row in ratios.items():
            _log(f"        {name:12s} {row}")
        findings["load_check"] = _load_oracle(saved)

    state["part_f"] = findings
    return bool(findings.get("com_over_xml_ratios"))


# ---------------------------------------------------------------------------
# Part G: NULL CONTROL.
# ---------------------------------------------------------------------------


def part_g(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.tools_network import build_shadow_model

    _log("=" * 78)
    _log(f"[G] {_PART_LABELS['g']}")
    _log("=" * 78)

    working = _clone(METERS_NET, "null")
    counts_before = _counts(working)
    shadow_before = build_shadow_model(working)
    findings: dict[str, Any] = {"network": METERS_NET.name, "counts_before": counts_before}

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        saved = working.with_name("null_control.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved is None or not saved.exists():
        state["part_g"] = findings
        return False

    counts_after = _counts(saved)
    findings["counts_after"] = counts_after
    findings["counts_delta"] = {
        k: counts_after[k] - counts_before[k] for k in counts_before if counts_after[k] != counts_before[k]
    }
    _log(f"\n[G.1] counts delta (after-minus-before) on an UNTOUCHED load+save: "
         f"{findings['counts_delta'] or '{} (nothing changed)'}")
    _log("[G.2] shadow diff on an untouched round-trip:")
    findings["shadow_diff"] = _grouped_diff(shadow_before, build_shadow_model(saved))
    state["part_g"] = findings
    return not findings["counts_delta"] and findings["shadow_diff"]["total"] == 0


# ---------------------------------------------------------------------------
# Part H: the skeleton question.
# ---------------------------------------------------------------------------


def part_h(state: dict[str, Any]) -> bool:
    import pythoncom

    _log("=" * 78)
    _log(f"[H] {_PART_LABELS['h']}")
    _log("=" * 78)

    working = _clone(METERS_NET, "skeleton")
    findings: dict[str, Any] = {"network": METERS_NET.name, "sim_seconds": 120}

    pythoncom.CoInitialize()
    controller = _connect(working)
    new_no = None
    try:
        net = controller.vissim.Net
        unit = controller._net_unit_length()
        lane = controller._lane_by_index(BEIJING_LINK, BEIJING_LANE)
        nos_before = _lot_nos(net)
        new_no = (max(nos_before) if nos_before else 0) + 1
        net.ParkingLots.AddParkingLot(new_no, lane, controller._length_m_to_com(BEIJING_POS_M, unit))
        lot = net.ParkingLots.ItemByKey(new_no)

        try:
            findings["parking_routing_decisions"] = len(
                list(net.VehicleRoutingDecisionsParking.GetAll())
            )
        except Exception as exc:
            findings["parking_routing_decisions_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        _log(f"\n[H.1] parking routing decisions on this network: "
             f"{findings.get('parking_routing_decisions')}")

        findings["before_run"] = _read_attrs(
            lot, ("Capacity", "Occup", "OccupRate", "ParkSpcs", "ConcatLen")
        )
        _log(f"[H.2] occupancy-ish attributes BEFORE the run: {findings['before_run']}")

        controller.set_sim_duration(findings["sim_seconds"])
        controller.set_random_seed(42)
        _log(f"[H.3] RunContinuous for {findings['sim_seconds']} sim-seconds ...")
        run_error = ""
        try:
            controller.vissim.Simulation.RunContinuous()
        except Exception as exc:
            run_error = f"{type(exc).__name__}: {str(exc)[:300]}"
        findings["run_error"] = run_error
        _log(f"      run error: {run_error!r}")

        lot = net.ParkingLots.ItemByKey(new_no)
        findings["after_run"] = _read_attrs(
            lot, ("Capacity", "Occup", "OccupRate", "ParkSpcs", "ConcatLen")
        )
        _log(f"[H.4] occupancy-ish attributes AFTER the run: {findings['after_run']}")
        try:
            findings["vehicles_in_network_after_run"] = len(list(net.Vehicles.GetAll()))
        except Exception as exc:
            findings["vehicles_in_network_after_run"] = f"<error {type(exc).__name__}: {exc}>"
        _log(f"      vehicles present at run end: {findings['vehicles_in_network_after_run']}")
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    findings["new_lot_no"] = new_no
    state["part_h"] = findings
    return "after_run" in findings


# ---------------------------------------------------------------------------
# Part I: the derived-capacity relation, isolated.
#
# Part B measured "SetAttValue('Capacity', 37) is REFUSED LOUDLY" and, several
# writes later, read Capacity back as 100 -- two facts a single sweep cannot
# connect, because by then Length, LenPerSpc AND Type had all been written.
# This part isolates the relation with a CONTROL lot that is never written.
# ---------------------------------------------------------------------------


def _triple(lot: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in ("Length", "LenPerSpc", "Capacity"):
        try:
            out[name] = lot.AttValue(name)
        except Exception as exc:
            out[name] = f"<error {type(exc).__name__}>"
    return out


def part_i(state: dict[str, Any]) -> bool:
    import pythoncom

    _log("=" * 78)
    _log(f"[I] {_PART_LABELS['i']}")
    _log("=" * 78)

    working = _clone(METERS_NET, "capacity")
    findings: dict[str, Any] = {"network": METERS_NET.name, "steps": []}

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    control_no = treatment_no = None
    try:
        net = controller.vissim.Net
        unit = controller._net_unit_length()
        lane = controller._lane_by_index(BEIJING_LINK, BEIJING_LANE)
        findings["link_length2d"] = float(
            net.Links.ItemByKey(BEIJING_LINK).AttValue("Length2D")
        )
        _log(f"\n[I.0] link {BEIJING_LINK} Length2D = {findings['link_length2d']} ({unit})")

        control_no = 1
        treatment_no = 2
        net.ParkingLots.AddParkingLot(control_no, lane, controller._length_m_to_com(20.0, unit))
        net.ParkingLots.AddParkingLot(treatment_no, lane, controller._length_m_to_com(120.0, unit))
        control = net.ParkingLots.ItemByKey(control_no)
        treatment = net.ParkingLots.ItemByKey(treatment_no)

        findings["control_initial"] = _triple(control)
        findings["treatment_initial"] = _triple(treatment)
        _log(f"[I.1] fresh triples -- control {findings['control_initial']} "
             f"treatment {findings['treatment_initial']}")

        def step(label: str, attribute: str, value: Any) -> None:
            write = _try_write(treatment, attribute, value)
            after = _triple(treatment)
            control_now = _triple(control)
            row = {
                "label": label,
                "write": write,
                "treatment_triple": after,
                "control_triple": control_now,
            }
            findings["steps"].append(row)
            _log(f"      -> treatment triple {after}   control triple {control_now}")

        _log("\n[I.2] Capacity is refused LOUDLY on a clean REALPARKINGSPACE:")
        step("capacity_direct", "Capacity", 37)
        _log("\n[I.3] Length drives it:")
        step("length_60", "Length", 60.0)
        _log("\n[I.4] LenPerSpc drives it too:")
        step("lenperspc_4", "LenPerSpc", 4.0)
        _log("\n[I.5] LenPerSpc LARGER than Length -- refused, silent no-op, or accepted?")
        step("lenperspc_1000", "LenPerSpc", 1000.0)
        _log("\n[I.6] Length beyond the link's own end -- clamped, refused, or accepted?")
        step("length_beyond_link", "Length", findings["link_length2d"] * 3.0)
        _log("\n[I.7] Length zero:")
        step("length_zero", "Length", 0.0)

        saved = working.with_name("after_capacity.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    findings["control_no"] = control_no
    findings["treatment_no"] = treatment_no
    if saved and saved.exists():
        lots = _lot_map(saved)
        findings["persisted_control"] = lots.get(str(control_no))
        findings["persisted_treatment"] = lots.get(str(treatment_no))
        _log(f"\n[I.8] persisted control:   {findings['persisted_control']}")
        _log(f"      persisted treatment: {findings['persisted_treatment']}")
        findings["load_check"] = _load_oracle(saved)

    state["part_i"] = findings
    return bool(findings["steps"])


# ---------------------------------------------------------------------------
# Part J: the Type write.
# ---------------------------------------------------------------------------


def part_j(state: dict[str, Any]) -> bool:
    import pythoncom

    _log("=" * 78)
    _log(f"[J] {_PART_LABELS['j']}")
    _log("=" * 78)

    working = _clone(METERS_NET, "typeflip")
    findings: dict[str, Any] = {"network": METERS_NET.name, "counts_before": _counts(working)}

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    new_no = None
    try:
        net = controller.vissim.Net
        unit = controller._net_unit_length()
        lane = controller._lane_by_index(BEIJING_LINK, BEIJING_LANE)
        new_no = 1
        net.ParkingLots.AddParkingLot(new_no, lane, controller._length_m_to_com(BEIJING_POS_M, unit))
        lot = net.ParkingLots.ItemByKey(new_no)

        watched = ("Type", "Lane", "Link", "Zone", "Capacity", "Length", "LenPerSpc")
        findings["before_flip"] = _read_attrs(lot, watched)
        try:
            findings["zones_before"] = sorted(int(z.AttValue("No")) for z in net.Zones.GetAll())
        except Exception:
            findings["zones_before"] = []
        _log(f"\n[J.1] before the flip: "
             f"{ {k: v.get('value', v.get('error')) for k, v in findings['before_flip'].items()} }")
        _log(f"      zones before: {findings['zones_before']}")

        _log("\n[J.2] SetAttValue('Type', 'ZONECONNECTOR') -- nothing else written:")
        findings["flip_write"] = _try_write(lot, "Type", "ZONECONNECTOR")
        findings["after_flip"] = _read_attrs(lot, watched)
        try:
            findings["zones_after"] = sorted(int(z.AttValue("No")) for z in net.Zones.GetAll())
        except Exception:
            findings["zones_after"] = []
        _log(f"      after the flip: "
             f"{ {k: v.get('value', v.get('error')) for k, v in findings['after_flip'].items()} }")
        _log(f"      zones after: {findings['zones_after']}")

        _log("\n[J.3] flip BACK to REALPARKINGSPACE -- does the lane anchor return?")
        findings["flip_back_write"] = _try_write(lot, "Type", "REALPARKINGSPACE")
        findings["after_flip_back"] = _read_attrs(lot, watched)
        try:
            findings["zones_after_back"] = sorted(int(z.AttValue("No")) for z in net.Zones.GetAll())
        except Exception:
            findings["zones_after_back"] = []
        _log(f"      after flipping back: "
             f"{ {k: v.get('value', v.get('error')) for k, v in findings['after_flip_back'].items()} }")
        _log(f"      zones after flipping back: {findings['zones_after_back']}")

        saved = working.with_name("after_typeflip.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved and saved.exists():
        findings["counts_after"] = _counts(saved)
        findings["counts_delta"] = {
            k: findings["counts_after"][k] - findings["counts_before"][k]
            for k in findings["counts_before"]
            if findings["counts_after"][k] != findings["counts_before"][k]
        }
        findings["persisted_lot_xml"] = _lot_map(saved).get(str(new_no))
        _log(f"\n[J.4] counts delta (after-minus-before): {findings['counts_delta']}")
        _log(f"      persisted lot: {findings['persisted_lot_xml']}")
        findings["load_check"] = _load_oracle(saved)

    state["part_j"] = findings
    return "after_flip" in findings


# ---------------------------------------------------------------------------
# --audit: re-derive every headline number from the persisted .inpx pairs
# alone, through a SEPARATE code path from the one that wrote state.json.
# ---------------------------------------------------------------------------


def audit_state(state_path: Path) -> dict[str, Any]:
    """Re-derive the FINDINGS numbers straight from the saved .inpx files with
    ElementTree, WITHOUT reading the fields the live parts recorded.

    Q6 unit (1) shipped a probe defect precisely because one function wrote
    both ``state.json`` and the prose it was checked against (a sign-inverted
    delta survived review). This function is the independent second path: it
    walks ``runtime/parking-lot-probe/*/`` for the persisted before/after pairs
    and recomputes each claim from the XML.
    """
    state = _load_state(state_path)
    audit: dict[str, Any] = {}

    def _find(work_prefix: str, filename: str) -> Path | None:
        for candidate in sorted(WORK_ROOT.glob(f"{work_prefix}_*/{filename}")):
            return candidate
        return None

    # A: the created lot, re-read from the saved file rather than from
    # state["part_a"]["new_lot_xml"].
    after_add = _find("add", "after_add.inpx")
    if after_add is not None:
        root = ET.parse(after_add).getroot()
        lots = _lots(root)
        audit["a_lots_in_saved_file"] = len(lots)
        audit["a_lot_attribs"] = [dict(el.attrib) for el in lots]
        audit["a_container_created"] = root.find("parkingLots") is not None
        audit["a_counts"] = _counts(after_add)

    # C: the referenced-removal cascade, recomputed from the source example
    # (never opened in place -- read only) against the saved treatment file.
    after_remove = _find("remove-referenced", "after_remove_referenced.inpx")
    if after_remove is not None:
        source = sorted(PARKING_NET.glob("*.inpx"))[0]
        before_lots = set(_lot_map(source))
        after_lots = set(_lot_map(after_remove))
        audit["c_lots_before"] = len(before_lots)
        audit["c_lots_after"] = len(after_lots)
        audit["c_lots_delta"] = len(after_lots) - len(before_lots)
        audit["c_lots_removed"] = sorted(before_lots - after_lots, key=lambda s: int(s) if s.isdigit() else 0)

        # Route identity is (owning decision, route no) -- route numbers are
        # NOT globally unique across decisions on this network, so the live
        # part's set-of-ids difference under-reports (it showed "0 lost" while
        # the element COUNT fell by 1). Counting ELEMENTS and keying by the
        # owning decision is the correct measure; this is exactly the kind of
        # divergence the independent audit path exists to surface.
        def _route_rows(path: Path) -> list[tuple[str, str, str]]:
            root = ET.parse(path).getroot()
            rows: list[tuple[str, str, str]] = []
            for decision in root.findall(".//vehicleRoutingDecisionParking"):
                for route in decision.findall(".//vehicleRouteParking"):
                    rows.append((decision.get("no", ""), route.get("no", ""), route.get("parkLot", "")))
            return rows

        before_rows = _route_rows(source)
        after_rows = _route_rows(after_remove)
        audit["c_route_elements_before"] = len(before_rows)
        audit["c_route_elements_after"] = len(after_rows)
        audit["c_route_elements_delta"] = len(after_rows) - len(before_rows)
        audit["c_route_rows_lost"] = sorted(set(before_rows) - set(after_rows))[:20]
        audit["c_dangling_refs_after"] = sorted(
            {lot for _, _, lot in after_rows if lot and lot not in after_lots}
        )
        audit["c_counts_delta"] = {
            k: v - _counts(source)[k]
            for k, v in _counts(after_remove).items()
            if v != _counts(source)[k]
        }

    # D: control/treatment pair.
    control = _find("remove-clean", "control_with_lot.inpx")
    treatment = _find("remove-clean", "treatment_lot_removed.inpx")
    if control is not None and treatment is not None:
        audit["d_counts_delta"] = {
            k: v - _counts(control)[k]
            for k, v in _counts(treatment).items()
            if v != _counts(control)[k]
        }
        audit["d_lots_control"] = sorted(_lot_map(control))
        audit["d_lots_treatment"] = sorted(_lot_map(treatment))

    # F: units, recomputed from the persisted XML against the recorded COM
    # reads (the COM side cannot be re-derived offline; the XML side can, and
    # the ratio is what the claim rests on).
    after_feet = _find("feet", "after_feet.inpx")
    if after_feet is not None:
        lots = _lot_map(after_feet)
        audit["f_persisted_lots"] = lots
        part_f_state = state.get("part_f", {})
        recorded = part_f_state.get("com_attrs", {})
        lot_no = None
        for candidate, attrs in lots.items():
            lot_no = candidate
        if lot_no:
            xml_row = lots[lot_no]
            audit["f_recomputed_ratios"] = {}
            for com_id, xml_attr in (("Pos", "pos"), ("Length", "length"), ("SpeedForw", "speedForw")):
                com_value = (recorded.get(com_id) or {}).get("value")
                try:
                    audit["f_recomputed_ratios"][com_id] = round(
                        float(com_value) / float(xml_row[xml_attr]), 6
                    )
                except (TypeError, ValueError, ZeroDivisionError, KeyError):
                    audit["f_recomputed_ratios"][com_id] = None

    # I: the derived-capacity relation, re-derived from the PERSISTED triple
    # rather than from the live reads the part recorded. capacity ==
    # round(length / lenPerSpc) is the hypothesis; this recomputes it.
    after_capacity = _find("capacity", "after_capacity.inpx")
    if after_capacity is not None:
        rows = {}
        for lot_no, attrs in _lot_map(after_capacity).items():
            try:
                length = float(attrs["length"])
                per = float(attrs["lenPerSpc"])
                capacity = int(attrs["capacity"])
            except (KeyError, TypeError, ValueError):
                continue
            rows[lot_no] = {
                "length": length,
                "len_per_space": per,
                "capacity": capacity,
                "length_over_len_per_space": round(length / per, 6) if per else None,
                "matches_floor": capacity == int(length // per) if per else None,
                "matches_round": capacity == round(length / per) if per else None,
            }
        audit["i_persisted_triples"] = rows

    # J: the Type flip -- did a zone materialise, did the anchor change?
    after_typeflip = _find("typeflip", "after_typeflip.inpx")
    if after_typeflip is not None:
        source = sorted(METERS_NET.glob("*.inpx"))[0]
        root = ET.parse(after_typeflip).getroot()
        audit["j_zones_in_source"] = len(ET.parse(source).getroot().findall(".//zones/zone"))
        audit["j_zones_after"] = len(root.findall(".//zones/zone"))
        audit["j_lot_xml"] = [dict(el.attrib) for el in _lots(root)]
        audit["j_counts_delta"] = {
            k: v - _counts(source)[k]
            for k, v in _counts(after_typeflip).items()
            if v != _counts(source)[k]
        }

    # G: null control.
    null_control = _find("null", "null_control.inpx")
    if null_control is not None:
        source = sorted(METERS_NET.glob("*.inpx"))[0]
        audit["g_counts_delta"] = {
            k: v - _counts(source)[k]
            for k, v in _counts(null_control).items()
            if v != _counts(source)[k]
        }

    state["audit"] = audit
    _save_state(state_path, state)
    _log("=" * 78)
    _log("[AUDIT] independently re-derived from the persisted .inpx files")
    _log("=" * 78)
    _log(json.dumps(audit, indent=2, default=str))
    return audit


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------

_PARTS = {
    "a": part_a, "b": part_b, "c": part_c, "d": part_d,
    "e": part_e, "f": part_f, "g": part_g, "h": part_h,
    "i": part_i, "j": part_j,
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


def _run_all(only: list[str] | None = None) -> int:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    state_path = WORK_ROOT / "state.json"
    order = only or _PART_ORDER
    if only is None and state_path.exists():
        state_path.unlink()

    state = _load_state(state_path)
    if only is None:
        state["static_scan"] = static_corpus_scan()
        state["route_scan"] = static_route_scan()
        _save_state(state_path, state)

    failures: list[str] = []
    for part in order:
        _log(f"\n\n########## spawning {_PART_LABELS[part]} ##########")
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--part", part, "--state", str(state_path)],
            cwd=str(BACKEND_ROOT),
        )
        state = _load_state(state_path)
        recorded = [r for r in state["part_results"] if r["part"] == part]
        if not recorded:
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
    parser = argparse.ArgumentParser(description="Q6 unit (2) parking-lot probe")
    parser.add_argument("--part", choices=_PART_ORDER)
    parser.add_argument("--only", help="comma-separated part list to re-run without clearing state")
    parser.add_argument("--static-only", action="store_true", help="run the licence-free scans only")
    parser.add_argument("--audit", action="store_true", help="re-derive findings from persisted .inpx")
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    if args.audit:
        audit_state(args.state or (WORK_ROOT / "state.json"))
        return 0
    if args.static_only:
        state_path = WORK_ROOT / "state.json"
        state = _load_state(state_path)
        state["static_scan"] = static_corpus_scan()
        state["route_scan"] = static_route_scan()
        _save_state(state_path, state)
        _log(f"\nstate file: {state_path}")
        return 0
    if args.part:
        if args.state is None:
            parser.error("--part requires --state")
        return _run_one_part(args.part, args.state)
    only = [p.strip() for p in args.only.split(",")] if args.only else None
    return _run_all(only)


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        raise SystemExit(main())

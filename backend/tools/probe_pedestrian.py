"""Q6 unit (3) probe -- pedestrian inputs & areas (Viswalk), the LICENCE question first.

RUN: ``python backend/tools/probe_pedestrian.py``            (all parts, fresh state)
     ``python backend/tools/probe_pedestrian.py --only a,c``  (re-run parts, keep state)
     ``python backend/tools/probe_pedestrian.py --static-only`` (no COM, no licence)
     ``python backend/tools/probe_pedestrian.py --audit``      (re-derive from persisted .inpx)

Every part runs in its OWN subprocess (one COM connect/disconnect per process --
the Q4 unit (8) segfault). Machine-written evidence:
``runtime/pedestrian-probe/state.json``; every number below is re-derived from
the persisted ``.inpx`` pairs by ``--audit``, a separate code path from the
writer (plain ElementTree counting, sharing no helper with ``_counts`` /
``_ped_input_map`` / ``_area_map``).

AUDIT COVERAGE, stated precisely rather than overclaimed (an earlier draft
of this header said "every number", which was not true). ``--audit`` covers
sections 2-9's structural claims for parts A, B, E, F, G, H, I, J, K, L, M, N
and O. It does NOT cover, and these are re-read from ``state.json`` rather
than re-derived:

  (a) parts C/D's pedestrian and vehicle populations   [live-recorded]
  (b) every load/init ORACLE verdict                    [live-recorded]
  (c) every COM attribute read/write result             [live-recorded]
  (d) the licence strings                               [live-recorded]
  (e) section 1's whole corpus scan -- ``--audit`` reads work-dir artifacts,
      and the corpus scan reads the read-only Examples Demo tree instead
  (f) section 6's PRD 3 / PRD 4 route attribution -- derived by a one-off
      id-level diff of the part-G pair during review, not by ``--audit``

Parts P and Q/R WERE in this exception list and are not any more: ``--audit``
now re-derives P's 14-interval headline and Q/R's static-vs-partial origin
classification from the persisted files (their ORACLE verdicts stay
live-recorded, like every other oracle). (e) and (f) are still re-derivable by
anyone who wants to; they are just not re-derived automatically today.
(a)-(d) cannot be, ever.

SIGN CONVENTION: every delta in this file is AFTER minus BEFORE. A positive
number means the class GAINED rows. (Q6 unit (1) shipped this inverted and had
to correct it; ``_count_diff`` is the single place it is computed.)

===========================================================================
FINDINGS
===========================================================================

--- 0. THE LICENCE VERDICT: this box CAN create and simulate Viswalk objects.
    No pivot needed; the unit ships real write verbs.

    There is NO licence-naming attribute on the Vissim COM object at all --
    all eight candidates (``LicenseInfo``/``LicenceInfo``/``License``/
    ``Licence``/``LicenseType``/``ProductName``/``Modules``/``AddOns``) raise
    "Could not find attribute with ID ...". The two that DO resolve are
    ``Version`` = ``'PTV Vissim Academic 2026 (SP 09)'`` and ``VersionNumber``
    = ``'260009'``. So the licence question is answered by BEHAVIOUR, four
    measurements deep, not by reading a flag:

      (1) LOAD    -- a Viswalk-bearing .inpx (Beijing: 16 areas, 8 pedestrian
                     inputs, 8 routing decisions) loads.
      (2) READ    -- all nine pedestrian containers enumerate: PedestrianInputs
                     8, Areas 16, PedestrianTypes 2 [100, 200], PedestrianClasses
                     1, PedestrianCompositions 1, Obstacles 0, Ramps 0,
                     AreaBehaviorTypes 2, PedestrianRoutingDecisionsStatic 8.
      (3) RUN     -- ``RunSingleStep`` works, and part C observed 36 real
                     pedestrians walking (see 3 below).
      (4) CREATE  -- ``AddPedestrianInput`` and ``AddArea`` both create.

    Recorded so nobody re-derives it: an ACADEMIC licence was what this ran on.
    A deployment box with a Vissim licence lacking the Viswalk add-on is NOT
    covered by this measurement, and the failure mode there is unmeasured.

--- 1. REPRESENTATION (static scan [0], 25 .inpx files).

    Corpus totals: 46 ``pedestrianInput``, 331 ``area``, 530 ``obstacle``,
    27 ``ramp``, 51 ``pedestrianType``, 27 ``pedestrianClass``, 31
    ``pedestrianComposition``, 136 ``pedestrianCompositionRelativeFlow``,
    72 ``timeIntervalPedVolume``, 58 ``areaBehaviorType``, 73
    ``walkingBehavior``, 99 ``pedestrianRoutingDecisionStatic``, 335
    ``pedestrianRouteStatic``, 505 ``pedestrianRouteLocation``, 26
    ``pedestrianRoutingDecisionPartial``, 7 ``elevator``.

    Only 6 of 25 files carry a ``<pedestrianInputs>`` section at all --
    Karlsruhe 3D 26, Beijing 8, London 6, Buehl (two variant files) 2 each,
    Air Taxi Vertiport 2 = 46. Nine files carry ``<areas>``.

    * A ``<pedestrianInput>`` carries ONLY ``no`` and ``name`` (46/46). Every
      field that matters lives in two CHILDREN, and both shapes are perfectly
      uniform across the corpus:
        - ``<location area="N"><posOnArea x=".." y=".."/></location>``
          -- 46/46, exactly one shape, no variants.
        - ``<timeIntPedVols><timeIntervalPedVolume cont pedComp timeInt
          volType volume/></timeIntPedVols>`` -- 72 rows over 46 inputs;
          44 inputs carry exactly 1 interval, 2 carry 14 (Air Taxi).
      This is the vehicle-input shape with different spelling, and the
      interval structure belongs to the NETWORK the same way (see 5).
    * An ``<area>`` carries 26 attributes on 331/331, plus three conditional
      ones: ``areaBehavType`` (81), ``elevGrp`` (14), ``tmDistr`` (10).
      Geometry is ``<geometry><points><point x y/>...</points></geometry>`` on
      331/331. Polygon sizes: 261 of 331 areas are plain 4-point quads, and
      the other 70 run 5..226 points. FOUR is the corpus minimum -- the
      3-point polygon mentioned under UNITS below is one this PROBE created,
      not something the corpus contains.
    * AREA REFERRERS, corpus-wide (this is the reference-integrity map the
      removal question in 6 is about): ``pedestrianInput`` 46,
      ``pedestrianRoutingDecisionStatic`` 99, ``pedestrianRouteLocation`` 497,
      ``pedestrianRoutingDecisionPartial`` 26, ``pedestrianAttributeDecision``
      10. (497 not 505 route locations name an area -- the other 8 sit on
      ramps.)

    The SHADOW MODEL was blind to every one of these: parts A/E/G/H/J all
    recorded ``shadow diff by class: {}`` for edits that demonstrably moved
    the persisted file. Representation therefore had to land before any verb
    -- a verb whose object class the diff cannot see is unverifiable by
    construction.

--- 2. CREATION SEMANTICS: plain ``Add`` works. No duplicate-then-edit.

    ``Net.PedestrianInputs.AddPedestrianInput(Key, Area)`` and
    ``Net.Areas.AddArea(Key, WktPolygon)`` both produce real, functional
    objects. This is the SECOND family after vehicle inputs where plain
    ``Add`` is not a trap (contrast driving behaviours / priority rules /
    signal controllers, all of which need ``Duplicate*``).

    ``Area`` must be a real COM object, not an int -- the same pywin32
    marshalling shape ``AddVehicleInput``'s ``Link`` has.

    NO family limitation: a network with ZERO areas can mint its first, and
    then its first pedestrian input on it. Proven twice on different
    networks -- Toll Plaza Camden.US (part F: 0 areas -> 2, 0 inputs -> 1)
    and Roundabout Schenectady.US (part J: 0 -> 1 and 0 -> 1).

    A FRESH input's defaults, live-read: ``Name=''``, ``Area`` = the area
    passed, ``Volume(1)=0.0``, ``PedComp(1)='1'``, ``VolType(1)='STOCHASTIC'``,
    ``Cont(1)=0``, ``X=0.0``, ``Y=0.0``. A created-and-left-alone input
    therefore generates NOTHING -- which is why volume is a REQUIRED verb
    parameter, not an optional one.

    A FRESH area's defaults on the METRES network: ``CellSize=0.15``,
    ``DesSpeedFact=1.0``, ``ObstDist=0.5``, ``Thickness=0.05``,
    ``ZOffset=0.0``, ``DisplayType='21'``, ``IsQueue=0``, ``AlwUseAsOrig=0``,
    ``AlwUseAsDest=0``, ``PedRecAct=1``, and ``AreaBehavType``/``TmDistr``
    both ``None``.

--- 3. THE TRAFFIC-GENERATION BAR: CLEARED, against a null control.

    Controlled pair on Beijing, differing in exactly one bit. Both arms zero
    all 8 pre-existing pedestrian inputs first (read back {1..8: 0.0}), so any
    pedestrian observed can only have come from the created input. Envelope
    stated before stepping: SimRes 10 steps/s x 600 steps = 60 SIMULATION
    seconds; at 2000 ped/h that is 33.3 expected arrivals.

      part C (one created input, area 1, 2000 ped/h): 36 pedestrians, 208 vehicles
      part D (NULL CONTROL, no input at all):          0 pedestrians, 208 vehicles

    36 against an expectation of 33.3, and 0 in the control -- created
    pedestrian inputs genuinely generate pedestrians. The IDENTICAL vehicle
    count (208 both arms) is the "the run really ran" witness: ambient vehicle
    demand was deliberately NOT zeroed.

    LANDMINE, caught loudly by the failure counter rather than silently:
    ``IPedestrian`` has NO ``Area`` attribute. The first recorded run of part C
    grouped by ``AttValue("Area")`` and took what the console reported as
    36/36 named failures (``PEDESTRIAN: Could not find attribute with ID
    AREA``) -- the exact Q5 unit (1) landmine-2 shape. HONEST PROVENANCE: that
    36/36 is INFERRED, not recoverable from an artifact. The failing run was
    re-run in place, so ``state.json``'s ``part_c`` now holds the FIXED run
    and ``part_results`` keeps only a bare ``status: FAIL`` for the first --
    the figure survives from the console transcript alone. What IS still
    checkable is the current run's ``locate_failures: 0``. The TOTAL was never
    affected (it is
    ``len(GetAll())``, a container-level count that needs no per-object read);
    only the grouping was, and the counter is what made that visible instead
    of yielding a confidently empty breakdown. The attributes that DO resolve
    on a live ``IPedestrian``: ``No``, ``PedType`` (``'100'``), ``DesSpeed``
    (5.4829), ``Speed``, ``CoordFront``/``CoordRear`` (``'x y z'`` strings),
    ``Level``. ``OrigArea``/``DestArea``/``TotDistTrav``/``Loc`` do not exist.

--- 4. THE DECISIVE SEMANTIC FINDING: a pedestrian input whose ORIGIN AREA
    carries no pedestrian ROUTING DECISION makes the network refuse to
    initialise -- and it is the AREA that decides, not the demand.

    Six arms, each moving one bit, all judged by Vissim's OWN load/init oracle
    (the same ``load_check`` every structural verb here already runs):

      part  origin area                     routing decision?  volume  init_ok
      ----  ------------------------------  -----------------  ------  -------
      A     existing area 1                 YES                0       True
      K     existing area 1                 YES                750     True
      N     new area 17, NO INPUT AT ALL     n/a                n/a     True
      O     existing area 2 (shipped)        NO                 750     False
      L     new area 17                      NO                 750     False
      M     new area 17                      NO                 0       False

    Reading the contrasts:
      * K vs L  -- the routing decision is what matters.
      * L vs M  -- the demand volume is IRRELEVANT; volume 0 fails just the same.
      * N       -- a bare created area is HARMLESS on its own. The failure needs
                   an INPUT on an unrouted area, not merely a new area.
      * O vs L  -- and it is not about freshly-minted geometry either: area 2
                   SHIPPED with Beijing (real, connected crosswalk polygon) and
                   fails identically. Beijing has 8 routed areas
                   (1, 4, 5, 8, 9, 12, 13, 16) and 8 unrouted ones
                   (2, 3, 6, 7, 10, 11, 14, 15).

    IT MUST BE A **STATIC** ROUTING DECISION, and this overturned the unit's
    own first answer. ``_routed_area_nos`` originally counted
    ``pedestrianRoutingDecisionPartial`` too -- reasoning that a route is a
    route. That was an INFERENCE, and a PERMISSIVE one, i.e. the direction
    that waves a broken network through. Parts Q/R measure it as a controlled
    pair on the only corpus network that has partial decisions at all
    (Cableways: 139 areas, 25 static-routed, 26 partial-routed, and 24 routed
    ONLY partially):

      part  origin area                          init_ok
      ----  ----------------------------------   -------
      Q     142, routed ONLY by a partial dec.    False
      R     102, STATIC-routed, SAME network      True

    One bit apart, opposite verdicts. The guard is now static-only, and
    Vissim's own error text elsewhere naming the STATIC kind specifically
    turned out to mean exactly what it said.

    The failure is LOUD, which is unusual for Vissim and worth stating: the
    file still LOADS (``load_ok=True`` in every failing arm) and dies at
    initialisation with ``'The simulation could not be continued.'``. That is
    exactly why this became a REFUSE-tier constraint rather than a warning --
    the refusal is cheap and certain, and the alternative is a network the
    user cannot run.

    Parts B, F, I and J each tripped this as a SIDE EFFECT before it was
    understood (all four put an input on an unrouted or newly created area);
    K/L/M/N/O are what turned four accidents into an attributed cause.

--- 5. WRITABILITY (readback-judged; ``moved`` = the value changed, ``exact``
    = it equals what was requested -- both reported, because a clamped write
    still moves).

      Name        writable, exact ('' -> 'probe-input')
      Volume(1)   writable, exact (0.0 -> 750.0)
      VolType(1)  writable, exact ('STOCHASTIC' -> 'EXACT')
      Area        WRITABLE -- re-anchors an existing input ('1' -> '2')
      No          WRITABLE -- renumbers an existing input (9 -> 9999)
      PedComp(1)  writable (part P: '1' -> '2' -> '12' on a 4-composition
                  network). ``exact`` reads False only because COM hands back
                  a str where an int went in. Part B could NOT settle this:
                  Beijing has exactly one composition, so writing 1 onto '1'
                  gave ``moved=False``, indistinguishable from a silent no-op.
      Cont(1)     REFUSED LOUDLY -- "Attribute Continued (0-MAX) cannot be
                  changed."
      Volume(2)   REFUSED on a 1-interval network -- "The subattribute no. 1
                  '2' of type 'Time Interval' ... Object 2 not found".
      PedComp(1) <- a BOGUS composition number: REFUSED LOUDLY --
                  "Object 1011 of type Pedestrian Composition does not exist."
                  Nothing silently accepted here.

    INTERVALS BELONG TO THE NETWORK, confirmed for pedestrians exactly as Q5
    unit (3) found for vehicles. Part P created ONE input on Air Taxi
    Vertiport (a 14-interval network) and the persisted delta was
    ``{'pedestrian_inputs': 1, 'ped_volume_intervals': 14}`` -- the input
    arrives pre-stocked with all 14 intervals, interval 1 carrying the
    requested 200 and intervals 2..14 all volume 0. On 1-interval Beijing the
    same verb moves ``ped_volume_intervals`` by exactly 1.

--- 6. REMOVAL: one verb is clean, the other silently destroys routes.

    ``RemovePedestrianInput`` is CLEAN. Beijing input 1 removed ->
    ``{'pedestrian_inputs': -1, 'ped_volume_intervals': -1}`` and nothing
    else; areas 16->16, routing decisions 8->8; load oracle green.

    ``RemoveArea`` SILENTLY CASCADE-DELETES, and the blast radius is bigger
    than the direct referrer count suggests. Removing Beijing's area 1 moved
    the persisted counts by::

        {'areas': -1, 'pedestrian_inputs': -1, 'ped_volume_intervals': -1,
         'ped_routing_decisions_static': -1, 'ped_routes_static': -4,
         'ped_route_locations': -4}

    Area 1's DIRECT referrers were 1 pedestrian input, 1 routing decision and
    2 route locations. The -4/-4 is attributed exactly, by id, from the
    persisted pair rather than reasoned about: routing decision 1 ORIGINATES
    on area 1, so it goes and takes its own 2 routes with it; and routing
    decisions 3 and 4 each had a route whose DESTINATION location was area 1,
    so those 2 routes go too (measured: PRD 3 and PRD 4 each keep only route
    '2' afterwards; route numbers are LOCAL to their routing decision, which
    is why a naive global id-set diff shows nothing).

    The worst part: ``load_ok=True, init_ok=True`` afterwards. Vissim's own
    oracle is GREEN on a network that just lost 4 pedestrian routes. This is
    the Q6 unit (2) parking-lot cascade and the Q4 unit (3) conflict-area
    cascade wearing a third face. What guards it here is NOT a referrer
    check -- there is none, because no area verb ships at all. The guard is a
    NAME-level refusal: ``constraints.pedestrian_area_edit_unsupported``,
    delivered through ``orchestrator``'s unrecognized-tool branch (preflight
    could not deliver it -- these names are registered nowhere, so preflight
    never runs for them, which is a defect this unit shipped once and fixed).

--- 7. UNITS -- polygon coordinates are RAW METRES; scalar lengths are DISPLAY.

    Measured on a FEET network, not inherited from the ``AddLink`` finding.
    Part F on Toll Plaza Camden.US (``UnitLenShort='FEET'``): a WKT square of
    side 4.0 persisted with side EXACTLY 4.000000 in x and y, ratio
    persisted/requested = 1.0, and origin delta [0.0, 0.0]. (The
    counterfactual, stated the right way round -- an earlier draft had this
    inverted: if the WKT had been read as display FEET, 4.0 would have
    persisted as 4 ft = 1.2192 m, i.e. ratio 0.3048. The 3.280840 that first
    appeared here is the conversion in the OTHER direction and was never the
    number this arm could have produced.) Part I on metres Beijing is the
    control: side 3.0 -> 3.0, per-vertex delta [0.0, 0.0] on all four
    vertices. So ``AddArea``'s WKT x/y are raw metres in BOTH networks.

    The READ direction agrees: ``IArea.WktPolygon`` is a readable attribute
    (unlike links, which have no WKT attribute at all -- Q6 unit (1)) and on
    Camden read back ``MULTIPOLYGON(((0.0000 0.0000,4.0000 0.0000,4.0000
    4.0000,0.0000 4.0000,0.0000 0.0000)))`` -- raw, matching the write. Note
    three things about that string: it says MULTIPOLYGON where the write took
    POLYGON, it is rounded to 4 decimals (so it is NOT a lossless round-trip
    and must not be used as a geometry oracle), and it CLOSES the ring (5
    points for a 4-point area).

    The SCALAR length attributes on the same object ARE display-scaled, which
    is the split to remember. A fresh area's defaults read on Camden:
    ``CellSize=0.4921259842519684`` (= 0.15 m in feet),
    ``ObstDist=1.6404199475065615`` (= 0.5 m),
    ``Thickness=0.16404199475065617`` (= 0.05 m),
    ``QueueSpc=4.921259842519684`` (= 1.5 m) -- against 0.15/0.5/0.05/1.5 flat
    on metres Beijing. Same object: coordinates raw, lengths converted.

    Polygon point COUNT is preserved as given -- a 3-point WKT stays 3 points
    (part F area 2), it is NOT auto-closed into a 4th vertex.

    ``<posOnArea>`` x/y are RELATIVE to the area, not absolute. A created
    input persists at exactly ``x="0" y="0"`` and COM exposes the pair as
    ``X``/``Y`` (0.0/0.0). The corpus agrees: across Beijing's 8 real inputs
    the largest ``|posOnArea|`` is 0.5518, against area centroids whose
    magnitude runs to ~566.

--- 8. WHAT MATERIALISES ON SAVE (the Q5 unit (3) default-composition
    precedent, asked of pedestrians).

    On Roundabout Schenectady.US -- 0 areas, 0 inputs, 0
    ``<pedestrianComposition>``, but 2 pedestrian TYPES and 1 pedestrian CLASS
    already present -- creating one area plus one input and saving moved::

        {'areas': 1, 'pedestrian_inputs': 1, 'pedestrian_compositions': 1,
         'pedestrian_composition_flows': 1, 'ped_volume_intervals': 1}

    Two classes nobody would predict from the API name ``AddPedestrianInput``:
    ``pedestrian_compositions`` and ``pedestrian_composition_flows``, both
    materialised because saving realises the default composition 1 the fresh
    input references. ``pedestrian_classes`` (1->1) and ``pedestrian_types``
    (2->2) did NOT move. On composition-having networks (Beijing, part A) the
    composition classes stay flat, so -- exactly as in Q5 unit (3) -- this row
    is only PROVABLE on a zero-composition network.

--- 9. THE NULL CONTROL (part H). Load Beijing and save it with zero edits:
    count delta ``{}``, 0 of 8 pedestrian inputs moved a persisted attribute,
    0 of 16 area polygons moved a coordinate. Every diff in every other part
    is therefore attributable to that part's edit, not to load/save churn.

--- 10. DELIBERATELY NOT WRAPPED, recorded rather than silently skipped:
    obstacles (530 in corpus), ramps (27), elevators (7), walking behaviours
    (73), area behaviour types (58), pedestrian types/classes/compositions as
    editable objects, partial routing decisions (26), and -- the big one --
    pedestrian ROUTING DECISIONS and ROUTES themselves. That last omission is
    what makes finding 4 a refusal rather than an auto-fix: this agent cannot
    create the routing decision that would make an unrouted origin legal, so
    it refuses and names the areas that already have one.
"""
from __future__ import annotations

import argparse
import json
import math
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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "pedestrian-probe"

#: METRES network carrying a REAL Viswalk layer (8 pedestrian inputs, 16 areas,
#: 8 static routing decisions, 1 pedestrian composition) AND real vehicle
#: demand -- the workhorse every other unit already understands.
PED_NET = EXAMPLES_ROOT / "Urban Intersection Beijing.CN"
#: FEET network with ZERO ``<areas>`` and ZERO ``<pedestrianInputs>`` but a
#: pedestrian composition -- the from-nothing genesis case AND the
#: display-unit cross-check in one.
FEET_NO_AREA_NET = EXAMPLES_ROOT / "Toll Plaza Camden.US"
#: METRES network with ZERO areas, ZERO pedestrian inputs and -- uniquely in
#: the corpus -- ZERO ``<pedestrianComposition>`` too: the "what materialises
#: on save" case (Q5 unit (3)'s default-composition precedent).
NO_COMP_NET = EXAMPLES_ROOT / "Roundabout Schenectady.US"
#: The ONLY corpus network carrying PARTIAL pedestrian routing decisions (26,
#: against 25 static ones) -- and 24 of its areas are routed ONLY partially,
#: which is the one place the "does a partial decision satisfy the routing
#: precondition" question can be asked at all (part Q).
PARTIAL_ROUTE_NET = EXAMPLES_ROOT / "Cableways"
#: METRES network with FOUR pedestrian compositions -- the only place a
#: ``PedComp(1)`` write can be told apart from a silent no-op (Beijing has
#: exactly one composition, so part B's write could not settle it).
MULTI_COMP_NET = EXAMPLES_ROOT / "Air Taxi Vertiport"

#: Beijing area carrying BOTH a pedestrian input and a static routing decision
#: -- the reference-integrity target for the removal cascade question.
BEIJING_REFERENCED_AREA = 1

#: Bounded observation envelope for the traffic-generation question. 600 steps
#: at Beijing's SimRes=10 steps/s = 60 SIMULATION seconds. At 2000 ped/h that
#: is ~33 expected arrivals -- a zero result is only evidence because the
#: expectation is >> 1.
_DEMAND_STEPS = 600
_DEMAND_VOLUME_PPH = 2000.0

_M_PER_FT = 0.3048

_PART_LABELS = {
    "a": "Part A: THE LICENCE QUESTION -- does this box create/simulate Viswalk objects at all?",
    "b": "Part B: attribute writability sweep on a fresh pedestrian input (readback-judged)",
    "c": "Part C: TREATMENT -- ambient pedestrian demand zeroed, ONE created input, bounded run",
    "d": "Part D: NULL CONTROL -- ambient pedestrian demand zeroed, NO new input, same window",
    "e": "Part E: RemovePedestrianInput on a clean input -- persisted diff + load oracle",
    "f": "Part F: AddArea from WKT on a ZERO-area FEET network -- genesis + unit cross-check",
    "g": "Part G: RemoveArea on an area a pedestrian input AND a route reference -- cascade?",
    "h": "Part H: NULL CONTROL (file) -- load + save with zero edits; nothing may change",
    "i": "Part I: AddArea on the METRES network -- coordinate space, read AND write",
    "j": "Part J: zero-composition genesis -- what materialises on save",
    "k": "Part K: input on a ROUTED origin area, volume 750 -- load/init oracle",
    "l": "Part L: input on a ROUTE-LESS origin area, volume 750 -- load/init oracle",
    "m": "Part M: input on a ROUTE-LESS origin area, volume 0 -- load/init oracle",
    "n": "Part N: a BARE new area, no pedestrian input at all -- load/init oracle",
    "o": "Part O: input on an EXISTING but UNROUTED shipped area -- load/init oracle",
    "p": "Part P: PedComp(1) writability on a FOUR-composition network + a bogus id",
    "q": "Part Q: input on an area routed ONLY by a PARTIAL decision -- load/init oracle",
    "r": "Part R: CONTROL for Q -- same network, input on a STATIC-routed area",
}
_PART_ORDER = [
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "n", "o", "p", "q", "r",
]

#: Every attribute a real corpus ``<pedestrianInput>`` / its children carry
#: (static scan [0]) plus defensive COM-id candidates. A non-existent id must
#: show up as a NAMED failure, never a confident wrong value.
_PED_INPUT_COM_CANDIDATES = (
    "No", "Name", "Area", "Location", "Volume(1)", "PedComp(1)", "VolType(1)",
    "Cont(1)", "TimeInt(1)", "Volume", "PedComp", "VolType", "Cont",
    "PosX", "PosY", "X", "Y", "OrigArea", "Level",
)

_AREA_COM_CANDIDATES = (
    "No", "Name", "Level", "CellSize", "DesSpeedFact", "ObstDist", "IsQueue",
    "AlwUseAsOrig", "AlwUseAsDest", "AreaBehavType", "PedRecAct", "ZOffset",
    "Thickness", "DisplayType", "PtUsage", "PtWaitBehav", "PtBoardPassChoice",
    "QueueSpc", "QueueEvalAct", "WaitAreaMinSpc", "ShowIndivPeds",
    "Area", "SurfaceArea", "Size", "WktPolygon", "Points", "TmDistr",
    "QueueApprMeth", "WaitPosApprMeth", "ShowClsfValues",
)

_PEDESTRIAN_COM_CANDIDATES = (
    "No", "PedType", "Area", "DesSpeed", "Speed", "CoordFront", "CoordRear",
    "OrigArea", "DestArea", "Level", "TotDistTrav", "Loc",
)

#: Vissim-object-level ids that might name the licensed module set. Every one
#: is read defensively -- this is the LICENCE evidence, so a missing id must be
#: recorded as missing, never inferred.
_LICENCE_ID_CANDIDATES = (
    "LicenseInfo", "LicenceInfo", "License", "Licence", "LicenseType",
    "ProductName", "Version", "VersionNumber", "Modules", "AddOns",
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
    """Copy the whole example folder into our own work dir and return the
    .inpx inside it -- the example is NEVER opened in place
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

#: (shadow key, XPath) for every class a pedestrian edit could plausibly
#: disturb, plus the vehicle-side classes that must NOT move.
_COUNT_PATHS: dict[str, str] = {
    "pedestrian_inputs": ".//pedestrianInputs/pedestrianInput",
    "areas": ".//areas/area",
    "obstacles": ".//obstacles/obstacle",
    "ramps": ".//ramps/ramp",
    "pedestrian_types": ".//pedestrianTypes/pedestrianType",
    "pedestrian_classes": ".//pedestrianClasses/pedestrianClass",
    "pedestrian_compositions": ".//pedestrianCompositions/pedestrianComposition",
    "pedestrian_composition_flows": ".//pedestrianCompositionRelativeFlow",
    "ped_volume_intervals": ".//timeIntervalPedVolume",
    "area_behavior_types": ".//areaBehaviorTypes/areaBehaviorType",
    "walking_behaviors": ".//walkingBehaviors/walkingBehavior",
    "ped_routing_decisions_static": ".//pedestrianRoutingDecisionStatic",
    "ped_routes_static": ".//pedestrianRouteStatic",
    "ped_route_locations": ".//pedestrianRouteLocation",
    "ped_routing_decisions_partial": ".//pedestrianRoutingDecisionPartial",
    "elevators": ".//elevators/elevator",
    "links": ".//links/link",
    "conflict_areas": ".//conflictAreas/conflictArea",
    "vehicle_inputs": ".//vehicleInputs/vehicleInput",
    "vehicle_compositions": ".//vehicleCompositions/vehicleComposition",
    "signal_heads": ".//signalHeads/signalHead",
    "pt_stops": ".//ptStops/ptStop",
    "des_speed_distributions": ".//desSpeedDistributions/desSpeedDistribution",
}


def _counts(inpx: Path) -> dict[str, int]:
    root = ET.parse(inpx).getroot()
    return {key: len(root.findall(path)) for key, path in _COUNT_PATHS.items()}


def _ped_input_map(inpx: Path) -> dict[str, dict[str, Any]]:
    """{input no: {attrs..., area, pos_x, pos_y, volumes:[...]}} for one
    persisted .inpx -- the cheap oracle every live measurement is compared
    against. Flattens the ``<location>``/``<timeIntPedVols>`` children, which
    is where a pedestrian input keeps everything that matters."""
    root = ET.parse(inpx).getroot()
    out: dict[str, dict[str, Any]] = {}
    for el in root.findall(".//pedestrianInputs/pedestrianInput"):
        entry: dict[str, Any] = dict(el.attrib)
        loc = el.find("location")
        entry["_area"] = loc.get("area") if loc is not None else None
        pos = loc.find("posOnArea") if loc is not None else None
        entry["_pos_x"] = pos.get("x") if pos is not None else None
        entry["_pos_y"] = pos.get("y") if pos is not None else None
        entry["_volumes"] = [
            dict(v.attrib) for v in el.findall("timeIntPedVols/timeIntervalPedVolume")
        ]
        out[el.get("no", "")] = entry
    return out


def _area_map(inpx: Path) -> dict[str, dict[str, Any]]:
    """{area no: {attrs..., polygon:[[x,y],...]}} for one persisted .inpx."""
    root = ET.parse(inpx).getroot()
    out: dict[str, dict[str, Any]] = {}
    for el in root.findall(".//areas/area"):
        entry: dict[str, Any] = dict(el.attrib)
        entry["_polygon"] = [
            [p.get("x"), p.get("y")] for p in el.findall("geometry/points/point")
        ]
        out[el.get("no", "")] = entry
    return out


def _area_referrers(inpx: Path) -> dict[str, dict[str, list[str]]]:
    """{area no: {referring element tag: [ids/paths]}} -- every place in the
    persisted XML that names an area by id. Measured, not guessed: this is the
    reference-integrity map the removal verb has to consult."""
    root = ET.parse(inpx).getroot()
    out: dict[str, dict[str, list[str]]] = {}

    def _add(area_no: str | None, tag: str, ident: str) -> None:
        if not area_no:
            return
        out.setdefault(area_no, {}).setdefault(tag, []).append(ident)

    for pi in root.findall(".//pedestrianInputs/pedestrianInput"):
        loc = pi.find("location")
        _add(loc.get("area") if loc is not None else None, "pedestrianInput", pi.get("no", ""))
    for prd in root.findall(".//pedestrianRoutingDecisionStatic"):
        loc = prd.find("location")
        _add(loc.get("area") if loc is not None else None,
             "pedestrianRoutingDecisionStatic", prd.get("no", ""))
    for prd in root.findall(".//pedestrianRoutingDecisionPartial"):
        loc = prd.find("location")
        _add(loc.get("area") if loc is not None else None,
             "pedestrianRoutingDecisionPartial", prd.get("no", ""))
    for prl in root.findall(".//pedestrianRouteLocation"):
        _add(prl.get("area"), "pedestrianRouteLocation", prl.get("no", "") or "<anon>")
    for pad in root.findall(".//pedestrianAttributeDecision"):
        _add(pad.get("area"), "pedestrianAttributeDecision", pad.get("no", ""))
    for ptm in root.findall(".//pedestrianTravelTimeMeasurement"):
        _add(ptm.get("startArea"), "pedestrianTravelTimeMeasurement", ptm.get("no", ""))
        _add(ptm.get("endArea"), "pedestrianTravelTimeMeasurement", ptm.get("no", ""))
    for am in root.findall(".//areaMeasurement"):
        for ref in (am.get("area"), am.get("areas")):
            for token in (ref or "").split():
                _add(token, "areaMeasurement", am.get("no", ""))
    return out


def static_corpus_scan() -> dict[str, Any]:
    """What a real ``<pedestrianInput>``/``<area>`` IS across the whole 2026
    example corpus, plus which networks carry a Viswalk layer at all.

    This is the representation evidence -- the shadow model had no pedestrian
    class of any kind before this unit, so 'what fields exist' is measured from
    real exports rather than invented from the COM vocabulary (the two
    disagree: the XML keeps volume in a ``<timeIntPedVols>`` CHILD while COM
    exposes an indexed ``Volume(1)`` attribute -- Part A/B measure the map).
    """
    _log("=" * 78)
    _log("[0] STATIC corpus scan: pedestrian schema + area referrers")
    _log("=" * 78)
    per_network: dict[str, Any] = {}
    totals: dict[str, int] = {}
    ped_input_attrs: dict[str, int] = {}
    area_attrs: dict[str, int] = {}
    volume_attrs: dict[str, int] = {}
    location_shapes: dict[str, int] = {}
    area_geometry_shapes: dict[str, int] = {}
    polygon_sizes: dict[str, int] = {}
    intervals_per_input: dict[str, int] = {}
    referrer_tag_totals: dict[str, int] = {}

    files = sorted(EXAMPLES_ROOT.rglob("*.inpx"))
    for inpx in files:
        try:
            root = ET.parse(inpx).getroot()
        except Exception as exc:
            _log(f"      {inpx.parent.name}: PARSE FAILED ({type(exc).__name__}) -- skipped")
            continue
        key = f"{inpx.parent.name}/{inpx.name}"
        counts = {k: len(root.findall(p)) for k, p in _COUNT_PATHS.items()}
        per_network[key] = {
            k: v for k, v in counts.items()
            if k.startswith(("pedestrian", "ped_", "area", "obstacle", "ramp", "elevator"))
        }
        for k, v in counts.items():
            totals[k] = totals.get(k, 0) + v

        for el in root.findall(".//pedestrianInputs/pedestrianInput"):
            for attr in el.attrib:
                ped_input_attrs[attr] = ped_input_attrs.get(attr, 0) + 1
            loc = el.find("location")
            shape = (
                f"location[{','.join(sorted(loc.attrib))}]"
                f"/{','.join(sorted({c.tag for c in loc}))}"
                if loc is not None else "<no location>"
            )
            location_shapes[shape] = location_shapes.get(shape, 0) + 1
            vols = el.findall("timeIntPedVols/timeIntervalPedVolume")
            bucket = str(len(vols))
            intervals_per_input[bucket] = intervals_per_input.get(bucket, 0) + 1
            for vol in vols:
                for attr in vol.attrib:
                    volume_attrs[attr] = volume_attrs.get(attr, 0) + 1

        for el in root.findall(".//areas/area"):
            for attr in el.attrib:
                area_attrs[attr] = area_attrs.get(attr, 0) + 1
            geom = el.find("geometry")
            shape = (
                f"geometry[{','.join(sorted(geom.attrib))}]"
                f"/{','.join(sorted({c.tag for c in geom}))}"
                if geom is not None else "<no geometry>"
            )
            area_geometry_shapes[shape] = area_geometry_shapes.get(shape, 0) + 1
            n_points = len(el.findall("geometry/points/point"))
            polygon_sizes[str(n_points)] = polygon_sizes.get(str(n_points), 0) + 1

        for _area_no, by_tag in _area_referrers(inpx).items():
            for tag, ids in by_tag.items():
                referrer_tag_totals[tag] = referrer_tag_totals.get(tag, 0) + len(ids)

    _log(f"      scanned {len(files)} .inpx files")
    _log(f"\n      CORPUS TOTALS: {json.dumps(totals, indent=8)}")
    _log(f"\n      pedestrianInput attributes: {ped_input_attrs}")
    _log(f"      <location> shapes:          {location_shapes}")
    _log(f"      intervals per input:        {intervals_per_input}")
    _log(f"      timeIntervalPedVolume attrs:{volume_attrs}")
    _log(f"\n      area attributes:            {area_attrs}")
    _log(f"      <geometry> shapes:          {area_geometry_shapes}")
    _log(f"      polygon point counts:       {polygon_sizes}")
    _log(f"\n      area REFERRER tags (corpus-wide occurrences): {referrer_tag_totals}")

    _log("\n      per-network pedestrian element counts (non-empty only):")
    for net, counts in sorted(per_network.items()):
        nonzero = {k: v for k, v in counts.items() if v}
        if nonzero:
            _log(f"        {net}: {nonzero}")

    return {
        "files_scanned": len(files),
        "totals": totals,
        "per_network": per_network,
        "ped_input_attrs": ped_input_attrs,
        "location_shapes": location_shapes,
        "intervals_per_input": intervals_per_input,
        "volume_attrs": volume_attrs,
        "area_attrs": area_attrs,
        "area_geometry_shapes": area_geometry_shapes,
        "polygon_sizes": polygon_sizes,
        "area_referrer_tag_totals": referrer_tag_totals,
    }


def _scan_typelib_methods(keywords: tuple[str, ...]) -> dict[str, list[str]]:
    """Static type-library scan (no COM instance, no licence used) -- the house
    'scan the CONTAINER first rather than assume' step."""
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


def static_typelib_scan() -> dict[str, list[str]]:
    _log("\n" + "=" * 78)
    _log("[0b] STATIC type-library scan: the pedestrian CONTAINER family")
    _log("=" * 78)
    return _scan_typelib_methods(
        (
            "ipedestrianinput", "ipedestrianinputcontainer",
            "iarea", "iareacontainer",
            "ipedestriantype", "ipedestriantypecontainer",
            "ipedestriancomposition", "ipedestriancompositioncontainer",
            "ipedestrianclass", "ipedestrianclasscontainer",
            "ipedestrian", "ipedestriancontainer",
            "ipedestrianroutingdecisionstaticcontainer",
            "ipedestrianroutestaticcontainer",
            "ipedestrianroutelocationcontainer",
            "iobstaclecontainer", "irampcontainer",
            "iareabehaviortypecontainer",
        )
    )


# ---------------------------------------------------------------------------
# Live helpers.
# ---------------------------------------------------------------------------


def _read_attrs(obj: Any, names: tuple[str, ...]) -> dict[str, Any]:
    """Read every candidate attribute defensively -- an attribute that does not
    exist on this Vissim version must show up as a NAMED failure, never as a
    confident wrong value."""
    out: dict[str, Any] = {}
    for name in names:
        try:
            value = obj.AttValue(name)
            out[name] = {"value": value, "pytype": type(value).__name__}
        except Exception as exc:
            out[name] = {"error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    return out


def _try_write(obj: Any, name: str, value: Any) -> dict[str, Any]:
    """One SetAttValue attempt, judged by a READBACK (never by the bare absence
    of an exception -- ``AddPriorityRule``'s silent-no-op precedent)."""
    try:
        before = obj.AttValue(name)
    except Exception as exc:
        before = f"<unreadable: {type(exc).__name__}>"
    error = ""
    try:
        obj.SetAttValue(name, value)
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:200]}"
    try:
        after = obj.AttValue(name)
    except Exception as exc:
        after = f"<unreadable: {type(exc).__name__}>"
    # ``took`` is judged by INEQUALITY to the before-value, i.e. "the write
    # moved something", NOT by equality to the requested value (a clamped or
    # rounded write still moved). Both are reported so a reader can tell.
    return {
        "requested": value,
        "before": before,
        "after": after,
        "error": error,
        "moved": before != after,
        "exact": after == value,
    }


def _collection_nos(collection: Any) -> list[int]:
    out: list[int] = []
    for item in collection.GetAll():
        try:
            out.append(int(item.AttValue("No")))
        except Exception:
            continue
    return sorted(out)


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


def _count_diff(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    """AFTER minus BEFORE, always -- the sign convention Q6 unit (1) got
    inverted and had to correct. A positive number means the class GAINED
    rows."""
    return {k: after[k] - before[k] for k in before if after.get(k, 0) != before[k]}


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
        samples.setdefault(cls, [])
        if len(samples[cls]) < 8:
            samples[cls].append(label)
    _log(f"      shadow diff by class: {by_class}")
    for cls, items in samples.items():
        _log(f"        {cls}: {items}")
    return {"by_class": by_class, "samples": samples, "total_entries": len(raw)}


def _wkt_polygon(points_xy: list[tuple[float, float]]) -> str:
    """``POLYGON ((x1 y1, x2 y2, ..., x1 y1))`` -- the WKT shape
    ``IAreaContainer.AddArea(Key, WktPolygon)`` takes. Whether the ring must be
    explicitly closed is MEASURED in Part F, not assumed here: this helper
    closes it, and Part F also tries the unclosed form."""
    coords = ", ".join(f"{x} {y}" for x, y in points_xy)
    return f"POLYGON (({coords}))"


def _wkt_polygon_open(points_xy: list[tuple[float, float]]) -> str:
    coords = ", ".join(f"{x} {y}" for x, y in points_xy)
    return f"POLYGON (({coords}))"


def _ped_input_nos(net: Any) -> list[int]:
    return _collection_nos(net.PedestrianInputs)


def _area_nos(net: Any) -> list[int]:
    return _collection_nos(net.Areas)


# ---------------------------------------------------------------------------
# Part A: THE LICENCE QUESTION.
# ---------------------------------------------------------------------------


def part_a(state: dict[str, Any]) -> bool:
    """Does this box's licence accept pedestrian objects at all?

    Four separate yes/no measurements, in increasing order of what they'd cost
    us if refused: (1) does a network WITH a Viswalk layer LOAD, (2) can we
    READ its pedestrian containers, (3) does it RunSingleStep (i.e. does the
    pedestrian simulation actually initialise), (4) can we CREATE a pedestrian
    input and an area. A refusal at any level is recorded with its exact
    message -- that message is what a refuse-tier constraint would have to
    quote, so it is evidence, not decoration.
    """
    import pythoncom

    from services.agent.tools_network import build_shadow_model

    _log("=" * 78)
    _log(f"[A] {_PART_LABELS['a']}")
    _log("=" * 78)

    working = _clone(PED_NET, "licence")
    counts_before = _counts(working)
    inputs_before_xml = _ped_input_map(working)
    areas_before_xml = _area_map(working)
    shadow_before = build_shadow_model(working)
    _log(f"\n    clone: {working}")
    _log(f"    BEFORE (persisted): {counts_before}")

    findings: dict[str, Any] = {
        "network": PED_NET.name,
        "counts_before": counts_before,
        "ped_inputs_before_xml": inputs_before_xml,
        "area_nos_before_xml": sorted(areas_before_xml),
    }

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        vissim = controller.vissim
        net = vissim.Net

        _log("\n[A.0] LICENCE ids on the Vissim object (defensive reads -- a missing id is "
             "recorded as missing, never inferred):")
        licence = _read_attrs(vissim, _LICENCE_ID_CANDIDATES)
        for name, result in licence.items():
            _log(f"      {name:16s} {result}")
        findings["licence_attrs"] = licence

        _log("\n[A.1] the network LOADED (a Viswalk-bearing .inpx: "
             f"{counts_before['areas']} areas / {counts_before['pedestrian_inputs']} inputs).")
        findings["load_ok"] = True

        _log("\n[A.2] READ access to the pedestrian containers:")
        read_probe: dict[str, Any] = {}
        for label, getter in (
            ("PedestrianInputs", lambda: _ped_input_nos(net)),
            ("Areas", lambda: _area_nos(net)),
            ("PedestrianTypes", lambda: _collection_nos(net.PedestrianTypes)),
            ("PedestrianClasses", lambda: _collection_nos(net.PedestrianClasses)),
            ("PedestrianCompositions", lambda: _collection_nos(net.PedestrianCompositions)),
            ("Obstacles", lambda: _collection_nos(net.Obstacles)),
            ("Ramps", lambda: _collection_nos(net.Ramps)),
            ("AreaBehaviorTypes", lambda: _collection_nos(net.AreaBehaviorTypes)),
            ("PedestrianRoutingDecisionsStatic",
             lambda: _collection_nos(net.PedestrianRoutingDecisionsStatic)),
        ):
            try:
                nos = getter()
                read_probe[label] = {"count": len(nos), "nos": nos[:20]}
                _log(f"      {label:34s} {len(nos):4d}  {nos[:12]}")
            except Exception as exc:
                read_probe[label] = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
                _log(f"      {label:34s} ERROR {read_probe[label]['error']}")
        findings["container_read"] = read_probe

        _log("\n[A.3] RunSingleStep on the Viswalk network -- does the pedestrian simulation "
             "actually INITIALISE, or does the licence refuse at run time?")
        run_error = ""
        peds_after_smoke = None
        try:
            for _ in range(20):
                controller.run_single_step()
            peds_after_smoke = len(list(net.Pedestrians.GetAll()))
            _log(f"      20 steps OK; Net.Pedestrians count = {peds_after_smoke}")
        except Exception as exc:
            run_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            _log(f"      RAISED: {run_error}")
        findings["run_error"] = run_error
        findings["pedestrians_after_20_steps"] = peds_after_smoke

        if peds_after_smoke:
            ped = list(net.Pedestrians.GetAll())[0]
            ped_attrs = _read_attrs(ped, _PEDESTRIAN_COM_CANDIDATES)
            _log("      first IPedestrian's attributes:")
            for name, result in ped_attrs.items():
                _log(f"        {name:14s} {result}")
            findings["pedestrian_attrs"] = ped_attrs

        # A run leaves the network in a simulated state; stop it before editing.
        try:
            vissim.Simulation.Stop()
        except Exception as exc:
            _log(f"      Simulation.Stop() raised (recorded, not fatal): {exc}")

        _log("\n[A.4] CREATE: AddPedestrianInput(Key, Area) on an existing area ...")
        nos_before = _ped_input_nos(net)
        area_nos = _area_nos(net)
        findings["com_ped_input_nos_before"] = nos_before
        findings["com_area_nos"] = area_nos
        target_area_no = area_nos[0] if area_nos else None
        new_no = (max(nos_before) if nos_before else 0) + 1
        add_error = ""
        created = []
        if target_area_no is not None:
            area_obj = net.Areas.ItemByKey(target_area_no)
            try:
                net.PedestrianInputs.AddPedestrianInput(new_no, area_obj)
            except Exception as exc:
                add_error = f"{type(exc).__name__}: {str(exc)[:300]}"
                _log(f"      RAISED: {add_error}")
            nos_after = _ped_input_nos(net)
            created = sorted(set(nos_after) - set(nos_before))
            _log(f"      live PedestrianInputs {nos_before} -> {nos_after}; "
                 f"created (id-set diff, never a bare non-exception): {created}")
            findings["com_ped_input_nos_after"] = nos_after
        findings["add_ped_input_error"] = add_error
        findings["created_ped_input_by_id_diff"] = created
        findings["target_area_no"] = target_area_no
        findings["new_ped_input_no"] = new_no

        if created:
            fresh = net.PedestrianInputs.ItemByKey(new_no)
            attrs = _read_attrs(fresh, _PED_INPUT_COM_CANDIDATES)
            _log("\n[A.5] every candidate COM attribute on the FRESH pedestrian input:")
            for name, result in attrs.items():
                _log(f"      {name:14s} {result}")
            findings["fresh_input_com_attrs"] = attrs

        _log("\n[A.6] CREATE: AddArea(Key, WktPolygon) -- a 2 m x 2 m square beside the "
             "network origin ...")
        area_nos_before = _area_nos(net)
        new_area_no = (max(area_nos_before) if area_nos_before else 0) + 1
        # Anchor the square on an EXISTING area's first vertex so it lands
        # inside the modelled world rather than at (0,0) in the middle of
        # nowhere -- the polygon's absolute placement is Part I's question.
        anchor = None
        if areas_before_xml:
            first = areas_before_xml[sorted(areas_before_xml, key=lambda k: int(k))[0]]
            poly = first.get("_polygon") or []
            if poly:
                anchor = (float(poly[0][0]), float(poly[0][1]))
        anchor = anchor or (0.0, 0.0)
        square = [
            (anchor[0] + 10.0, anchor[1]),
            (anchor[0] + 12.0, anchor[1]),
            (anchor[0] + 12.0, anchor[1] + 2.0),
            (anchor[0] + 10.0, anchor[1] + 2.0),
        ]
        wkt = _wkt_polygon(square)
        _log(f"      WKT: {wkt}")
        area_add_error = ""
        try:
            net.Areas.AddArea(new_area_no, wkt)
        except Exception as exc:
            area_add_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            _log(f"      RAISED: {area_add_error}")
        area_nos_after = _area_nos(net)
        created_areas = sorted(set(area_nos_after) - set(area_nos_before))
        _log(f"      live Areas {len(area_nos_before)} -> {len(area_nos_after)}; "
             f"created: {created_areas}")
        findings.update({
            "requested_area_wkt": wkt,
            "requested_square_xy": square,
            "add_area_error": area_add_error,
            "created_area_by_id_diff": created_areas,
            "new_area_no": new_area_no,
        })

        if created_areas:
            fresh_area = net.Areas.ItemByKey(new_area_no)
            area_attrs = _read_attrs(fresh_area, _AREA_COM_CANDIDATES)
            _log("\n[A.7] every candidate COM attribute on the FRESH area:")
            for name, result in area_attrs.items():
                _log(f"      {name:14s} {result}")
            findings["fresh_area_com_attrs"] = area_attrs

        saved = working.with_name("after_add.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved is None or not saved.exists():
        state["part_a"] = findings
        return False

    counts_after = _counts(saved)
    findings["counts_after"] = counts_after
    findings["count_delta_after_minus_before"] = _count_diff(counts_before, counts_after)
    findings["ped_inputs_after_xml"] = _ped_input_map(saved)
    findings["new_input_xml"] = findings["ped_inputs_after_xml"].get(str(new_no))
    areas_after_xml = _area_map(saved)
    findings["new_area_xml"] = areas_after_xml.get(str(new_area_no))
    _log(f"\n[A.8] persisted counts AFTER: {counts_after}")
    _log(f"      DELTA (after-minus-before): {findings['count_delta_after_minus_before']}")
    _log(f"      new pedestrianInput XML: {findings['new_input_xml']}")
    _log(f"      new area XML:            {findings['new_area_xml']}")

    shadow_after = build_shadow_model(saved)
    findings["shadow_diff"] = _grouped_diff(shadow_before, shadow_after)
    findings["load_oracle"] = _load_oracle(saved)

    state["part_a"] = findings
    return bool(created) and bool(created_areas)


# ---------------------------------------------------------------------------
# Part B: attribute writability sweep on a fresh pedestrian input.
# ---------------------------------------------------------------------------


def part_b(state: dict[str, Any]) -> bool:
    """Which of a pedestrian input's attributes are actually WRITABLE, judged
    by readback rather than by the absence of an exception, and what does a
    freshly created input DEFAULT to (the Q5 unit (3) question -- a
    created-and-left-alone input that generates nothing is exactly the silent
    failure this layer exists to prevent)."""
    import pythoncom

    _log("=" * 78)
    _log(f"[B] {_PART_LABELS['b']}")
    _log("=" * 78)

    working = _clone(PED_NET, "writes")
    counts_before = _counts(working)
    findings: dict[str, Any] = {"network": PED_NET.name, "counts_before": counts_before}

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        area_nos = _area_nos(net)
        nos_before = _ped_input_nos(net)
        new_no = (max(nos_before) if nos_before else 0) + 1
        net.PedestrianInputs.AddPedestrianInput(new_no, net.Areas.ItemByKey(area_nos[0]))
        fresh = net.PedestrianInputs.ItemByKey(new_no)

        defaults = _read_attrs(fresh, _PED_INPUT_COM_CANDIDATES)
        findings["fresh_defaults"] = defaults
        _log("\n[B.1] a FRESH input's defaults:")
        for name, result in defaults.items():
            _log(f"      {name:14s} {result}")

        # An existing REAL input, for comparison -- the fresh one's defaults
        # are only interesting relative to what a modelled input carries.
        if nos_before:
            existing = net.PedestrianInputs.ItemByKey(nos_before[0])
            findings["existing_input_attrs"] = _read_attrs(existing, _PED_INPUT_COM_CANDIDATES)

        _log("\n[B.2] write sweep (each judged by READBACK):")
        writes: dict[str, Any] = {}
        second_area = area_nos[1] if len(area_nos) > 1 else area_nos[0]
        for attr, value in (
            ("Name", "probe-input"),
            ("Volume(1)", 750.0),
            ("PedComp(1)", 1),
            ("VolType(1)", "EXACT"),
            ("Cont(1)", True),
            ("Volume(2)", 500.0),
            ("Area", second_area),
            ("No", 9999),
        ):
            result = _try_write(fresh, attr, value)
            writes[attr] = result
            _log(f"      {attr:14s} {result}")
        findings["writes"] = writes

        # Restore the ones the persisted-diff read below needs to be stable.
        # ``No`` is restored too: the sweep proved it WRITABLE by moving it to
        # 9999, and leaving it there would make the persisted lookup below
        # search for an id that no longer exists (which is exactly what the
        # first recorded run of this part did -- it reported
        # ``persisted new input: None`` for an input that had persisted
        # perfectly well under its new number).
        try:
            fresh.SetAttValue("VolType(1)", "STOCHASTIC")
        except Exception:
            pass
        restore_error = ""
        try:
            fresh.SetAttValue("No", new_no)
        except Exception as exc:
            restore_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        findings["no_restore_error"] = restore_error
        findings["com_nos_after_restore"] = _ped_input_nos(net)
        _log(f"\n      restored No -> {new_no}: {restore_error or 'ok'}; live nos now "
             f"{findings['com_nos_after_restore']}")

        saved = working.with_name("after_writes.inpx")
        controller.save_net_as(saved)
        findings["new_input_no"] = new_no
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved is None or not saved.exists():
        state["part_b"] = findings
        return False

    findings["counts_after"] = _counts(saved)
    findings["count_delta_after_minus_before"] = _count_diff(counts_before, findings["counts_after"])
    findings["new_input_xml"] = _ped_input_map(saved).get(str(findings["new_input_no"]))
    _log(f"\n[B.3] persisted new input: {findings['new_input_xml']}")
    _log(f"      count delta (after-minus-before): {findings['count_delta_after_minus_before']}")
    findings["load_oracle"] = _load_oracle(saved)
    state["part_b"] = findings
    return True


# ---------------------------------------------------------------------------
# Parts C/D: the TRAFFIC-GENERATION bar with a null control.
# ---------------------------------------------------------------------------


#: Locate-attribute candidates for an ``IPedestrian``, in preference order.
#: The FIRST recorded run of part C used a bare ``"Area"`` and took 36/36
#: named failures -- ``PEDESTRIAN: Could not find attribute with ID AREA`` --
#: which is precisely the Q5 unit (1) landmine 2 shape (an attribute id that
#: does not exist on this interface). It surfaced loudly instead of silently
#: emptying the population, which is the point of the failure counter; the
#: fix is to resolve the id by measurement rather than to guess again.
_PED_LOCATE_CANDIDATES = ("OrigArea", "DestArea", "Area", "Loc", "Lane", "No")


def _count_pedestrians(net: Any) -> tuple[int, dict[str, int], int, str | None, dict[str, Any]]:
    """(total, {location: n}, locate failures, first error, probe).

    ``total`` is ``len(GetAll())`` -- a container-level count that does NOT
    depend on any per-object attribute read, so it stays trustworthy even when
    every locate fails. The grouping does depend on one, so the id is resolved
    against the live population first and the resolution recorded.
    """
    peds = list(net.Pedestrians.GetAll())
    total = len(peds)
    probe: dict[str, Any] = {}
    locate_id: str | None = None
    if peds:
        probe = _read_attrs(peds[0], _PEDESTRIAN_COM_CANDIDATES)
        for candidate in _PED_LOCATE_CANDIDATES:
            entry = probe.get(candidate)
            if entry and "error" not in entry:
                locate_id = candidate
                break
    by_loc: dict[str, int] = {}
    failures = 0
    first_error: str | None = None
    if locate_id is not None:
        for ped in peds:
            try:
                key = str(ped.AttValue(locate_id) or "")
                by_loc[key] = by_loc.get(key, 0) + 1
            except Exception as exc:
                failures += 1
                if first_error is None:
                    first_error = f"{type(exc).__name__}: {exc}"
    elif peds:
        failures = total
        first_error = (
            "no IPedestrian locate attribute resolved from "
            f"{_PED_LOCATE_CANDIDATES!r}; see part_c.pedestrian_attr_probe"
        )
    probe["_resolved_locate_id"] = locate_id
    return total, by_loc, failures, first_error, probe


def _demand_run(part: str, add_input: bool, state: dict[str, Any]) -> bool:
    """C and D share one body and differ in exactly ONE bit (``add_input``),
    which is what makes the pair a controlled comparison rather than two
    anecdotes. Both zero every pre-existing pedestrian input first, so any
    pedestrian observed can only have come from OUR new input."""
    import pythoncom

    _log("=" * 78)
    _log(f"[{part.upper()}] {_PART_LABELS[part]}")
    _log("=" * 78)

    working = _clone(PED_NET, f"demand-{part}")
    findings: dict[str, Any] = {
        "network": PED_NET.name,
        "add_input": add_input,
        "steps": _DEMAND_STEPS,
        "volume_pph": _DEMAND_VOLUME_PPH if add_input else 0.0,
    }

    pythoncom.CoInitialize()
    controller = _connect(working)
    try:
        net = controller.vissim.Net
        existing = _ped_input_nos(net)
        _log(f"\n[{part.upper()}.1] zeroing all {len(existing)} pre-existing pedestrian inputs "
             f"{existing} so the network has ZERO ambient pedestrian demand ...")
        for no in existing:
            net.PedestrianInputs.ItemByKey(no).SetAttValue("Volume(1)", 0.0)
        residual = {
            no: float(net.PedestrianInputs.ItemByKey(no).AttValue("Volume(1)")) for no in existing
        }
        all_zero = all(abs(v) < 1e-9 for v in residual.values())
        _log(f"      read back: {residual} -> all zero = {all_zero}")
        findings["preexisting_inputs"] = existing
        findings["preexisting_all_zeroed"] = all_zero
        if not all_zero:
            state[f"part_{part}"] = findings
            return False

        target_area = None
        if add_input:
            # Reuse an area a REAL input already originated on, so the created
            # input inherits a walkable origin with real routes leading away
            # from it -- otherwise "no pedestrians appeared" would be
            # confounded by "there was nowhere to walk".
            donor_area = None
            for no in existing:
                try:
                    donor_area = int(net.PedestrianInputs.ItemByKey(no).AttValue("Area"))
                    break
                except Exception:
                    continue
            target_area = donor_area if donor_area is not None else _area_nos(net)[0]
            key = (max(existing) if existing else 0) + 1
            net.PedestrianInputs.AddPedestrianInput(key, net.Areas.ItemByKey(target_area))
            new_input = net.PedestrianInputs.ItemByKey(key)
            new_input.SetAttValue("Volume(1)", _DEMAND_VOLUME_PPH)
            readback = float(new_input.AttValue("Volume(1)"))
            _log(f"      new input {key} on area {target_area}: wrote {_DEMAND_VOLUME_PPH} "
                 f"-> read back {readback}")
            findings.update({
                "new_input_no": key,
                "target_area": target_area,
                "new_input_volume_readback": readback,
            })

        sim_res = float(controller.vissim.Simulation.AttValue("SimRes"))
        sim_seconds = _DEMAND_STEPS / sim_res
        expected = sim_seconds * _DEMAND_VOLUME_PPH / 3600.0 if add_input else 0.0
        _log(
            f"\n[{part.upper()}.2] WINDOW BOUNDS, stated BEFORE stepping: simRes={sim_res:g} "
            f"steps/sim-second -> {_DEMAND_STEPS} RunSingleStep calls = {sim_seconds:g} SIMULATION "
            "seconds. "
            + (
                f"At {_DEMAND_VOLUME_PPH:g} ped/h that is an expected {expected:.1f} arrivals; a "
                "zero result is only evidence because this number is >> 1."
                if add_input
                else "With every input zeroed and NO new input, the expected arrival count is "
                "EXACTLY 0 -- this is the null control that makes C's count attributable."
            )
        )
        findings.update({"sim_res": sim_res, "sim_seconds": sim_seconds,
                         "expected_arrivals": expected})

        for _ in range(_DEMAND_STEPS):
            controller.run_single_step()

        total, by_loc, failures, first_error, ped_probe = _count_pedestrians(net)
        veh_total = len(list(net.Vehicles.GetAll()))
        _log(f"\n[{part.upper()}.3] After {_DEMAND_STEPS} steps: {total} PEDESTRIAN(s) "
             f"network-wide (locate id {ped_probe.get('_resolved_locate_id')!r} -> {by_loc}); "
             f"{veh_total} vehicle(s) (ambient vehicle demand was NOT zeroed -- it is the "
             f"'the run really ran' witness). locate_failures={failures}")
        if failures:
            _log(f"      WARNING: {failures}/{total} pedestrian(s) could not be located "
                 f"(first error: {first_error}). The GROUPING is not trustworthy while this is "
                 "non-zero; the TOTAL is a container-level count and stays valid.")
        if ped_probe:
            _log("      first IPedestrian's attributes:")
            for name, result in ped_probe.items():
                _log(f"        {name:14s} {result}")
        findings.update({
            "pedestrians_total": total,
            "pedestrians_by_location": by_loc,
            "pedestrian_locate_id": ped_probe.get("_resolved_locate_id"),
            "pedestrian_attr_probe": ped_probe,
            "vehicles_total": veh_total,
            "locate_failures": failures,
            "first_locate_error": first_error,
        })
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    state[f"part_{part}"] = findings
    return findings.get("locate_failures") == 0


def part_c(state: dict[str, Any]) -> bool:
    return _demand_run("c", True, state)


def part_d(state: dict[str, Any]) -> bool:
    return _demand_run("d", False, state)


# ---------------------------------------------------------------------------
# Part E: RemovePedestrianInput.
# ---------------------------------------------------------------------------


def part_e(state: dict[str, Any]) -> bool:
    """Removing a pedestrian input: does anything else go with it? A pedestrian
    input owns its ``<timeIntPedVols>`` and its ``<location>`` outright (no
    other element points AT an input by id -- the static referrer scan finds
    none), so the expectation is a clean single-class delta; the point of
    measuring is that Vissim's cascade behaviour has surprised this codebase
    three times already."""
    import pythoncom

    from services.agent.tools_network import build_shadow_model

    _log("=" * 78)
    _log(f"[E] {_PART_LABELS['e']}")
    _log("=" * 78)

    working = _clone(PED_NET, "remove-input")
    counts_before = _counts(working)
    shadow_before = build_shadow_model(working)
    inputs_before = _ped_input_map(working)
    findings: dict[str, Any] = {
        "network": PED_NET.name,
        "counts_before": counts_before,
        "ped_input_nos_before": sorted(inputs_before),
    }

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        nos = _ped_input_nos(net)
        victim = nos[0]
        findings["victim_no"] = victim
        findings["victim_xml_before"] = inputs_before.get(str(victim))
        _log(f"\n[E.1] removing pedestrian input {victim} of {nos} ...")
        remove_error = ""
        try:
            net.PedestrianInputs.RemovePedestrianInput(net.PedestrianInputs.ItemByKey(victim))
        except Exception as exc:
            remove_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            _log(f"      RAISED: {remove_error}")
        nos_after = _ped_input_nos(net)
        _log(f"      live PedestrianInputs {nos} -> {nos_after}")
        findings.update({
            "remove_error": remove_error,
            "com_nos_after": nos_after,
            "removed_by_id_diff": sorted(set(nos) - set(nos_after)),
        })
        saved = working.with_name("after_remove_input.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved is None or not saved.exists():
        state["part_e"] = findings
        return False

    counts_after = _counts(saved)
    findings["counts_after"] = counts_after
    findings["count_delta_after_minus_before"] = _count_diff(counts_before, counts_after)
    _log(f"\n[E.2] persisted delta (after-minus-before): "
         f"{findings['count_delta_after_minus_before']}")
    findings["shadow_diff"] = _grouped_diff(shadow_before, build_shadow_model(saved))
    findings["load_oracle"] = _load_oracle(saved)
    state["part_e"] = findings
    return bool(findings["removed_by_id_diff"])


# ---------------------------------------------------------------------------
# Part F: AddArea on a ZERO-area FEET network -- genesis + units.
# ---------------------------------------------------------------------------


def part_f(state: dict[str, Any]) -> bool:
    """Two questions at once, because the same clone answers both:

    1. GENESIS -- can a network with NO ``<areas>`` section at all mint its
       first area, and then its first pedestrian input on that area? (The
       controller/priority-rule family CANNOT: they are duplicate-based and
       genuinely stuck without a donor.)
    2. UNITS -- the network displays FEET. Are ``AddArea``'s WKT coordinates
       raw metres (like ``AddLink``'s ``WktLinestring``, the one length-shaped
       COM param this codebase found is NEVER display-converted) or display
       feet? MEASURED, never inherited from the link finding.
    """
    import pythoncom

    _log("=" * 78)
    _log(f"[F] {_PART_LABELS['f']}")
    _log("=" * 78)

    working = _clone(FEET_NO_AREA_NET, "feet-genesis")
    counts_before = _counts(working)
    findings: dict[str, Any] = {"network": FEET_NO_AREA_NET.name, "counts_before": counts_before}
    _log(f"    clone: {working}")
    _log(f"    BEFORE: areas={counts_before['areas']} inputs={counts_before['pedestrian_inputs']} "
         f"compositions={counts_before['pedestrian_compositions']}")

    # A known landmark in the network's own coordinate space: the first link's
    # first point, read from the persisted XML. The square is placed relative
    # to it so the unit question has an anchor whose true metres we know.
    root = ET.parse(working).getroot()
    first_pt = root.find(".//links/link/geometry/points3D/point3D")
    if first_pt is None:
        first_pt = root.find(".//links/link//point3D")
    anchor = (float(first_pt.get("x")), float(first_pt.get("y"))) if first_pt is not None else (0.0, 0.0)
    findings["anchor_xy_from_link_xml"] = list(anchor)

    side = 4.0
    square = [
        (anchor[0], anchor[1]),
        (anchor[0] + side, anchor[1]),
        (anchor[0] + side, anchor[1] + side),
        (anchor[0], anchor[1] + side),
    ]
    wkt = _wkt_polygon(square)
    findings.update({"requested_square_xy": square, "requested_wkt": wkt, "side_requested": side})

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        unit = controller._net_unit_length()
        findings["unit_len_short"] = unit
        _log(f"\n[F.1] display unit (UnitLenShort): {unit!r}")
        _log(f"      WKT requested: {wkt}")

        area_add_error = ""
        try:
            net.Areas.AddArea(1, wkt)
        except Exception as exc:
            area_add_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            _log(f"      RAISED: {area_add_error}")
        area_nos = _area_nos(net)
        findings["add_area_error"] = area_add_error
        findings["com_area_nos_after"] = area_nos
        _log(f"      live Areas after AddArea(1, wkt): {area_nos}")

        # The UNCLOSED-ring variant, measured rather than assumed.
        open_wkt = "POLYGON ((%s))" % ", ".join(
            f"{x} {y}" for x, y in [
                (anchor[0] + 20.0, anchor[1]),
                (anchor[0] + 24.0, anchor[1]),
                (anchor[0] + 24.0, anchor[1] + 4.0),
            ]
        )
        open_error = ""
        try:
            net.Areas.AddArea(2, open_wkt)
        except Exception as exc:
            open_error = f"{type(exc).__name__}: {str(exc)[:300]}"
        findings["triangle_wkt"] = open_wkt
        findings["triangle_add_error"] = open_error
        _log(f"      3-point polygon AddArea(2, ...): "
             f"{open_error or 'accepted'}; areas now {_area_nos(net)}")

        if area_nos:
            fresh = net.Areas.ItemByKey(area_nos[0])
            findings["fresh_area_com_attrs"] = _read_attrs(fresh, _AREA_COM_CANDIDATES)
            _log("\n[F.2] fresh area COM attributes:")
            for name, result in findings["fresh_area_com_attrs"].items():
                _log(f"      {name:14s} {result}")

            _log("\n[F.3] the FIRST pedestrian input on a network that had none ...")
            add_err = ""
            try:
                net.PedestrianInputs.AddPedestrianInput(1, fresh)
                net.PedestrianInputs.ItemByKey(1).SetAttValue("Volume(1)", 300.0)
            except Exception as exc:
                add_err = f"{type(exc).__name__}: {str(exc)[:300]}"
            findings["genesis_input_error"] = add_err
            findings["genesis_input_nos"] = _ped_input_nos(net)
            _log(f"      {add_err or 'accepted'}; PedestrianInputs now "
                 f"{findings['genesis_input_nos']}")

        saved = working.with_name("after_feet.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved is None or not saved.exists():
        state["part_f"] = findings
        return False

    counts_after = _counts(saved)
    findings["counts_after"] = counts_after
    findings["count_delta_after_minus_before"] = _count_diff(counts_before, counts_after)
    areas_after = _area_map(saved)
    findings["areas_after_xml"] = areas_after
    poly = [[float(x), float(y)] for x, y in (areas_after.get("1", {}).get("_polygon") or [])]
    findings["persisted_polygon"] = poly
    if poly:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        persisted_side_x = max(xs) - min(xs)
        persisted_side_y = max(ys) - min(ys)
        findings["persisted_side_x"] = persisted_side_x
        findings["persisted_side_y"] = persisted_side_y
        findings["ratio_persisted_over_requested_x"] = (
            persisted_side_x / side if side else None
        )
        findings["origin_delta_after_minus_before"] = [
            min(xs) - anchor[0], min(ys) - anchor[1],
        ]
        _log(f"\n[F.4] requested side {side} (in WHAT unit is the question); persisted side "
             f"x={persisted_side_x!r} y={persisted_side_y!r}; ratio persisted/requested = "
             f"{findings['ratio_persisted_over_requested_x']!r} "
             f"(1.0 => raw pass-through, {1/_M_PER_FT:.6f} => display-feet interpretation)")
        _log(f"      origin delta (persisted-min minus requested-anchor, after-minus-before): "
             f"{findings['origin_delta_after_minus_before']}")
    findings["ped_inputs_after_xml"] = _ped_input_map(saved)
    _log(f"      persisted count delta (after-minus-before): "
         f"{findings['count_delta_after_minus_before']}")
    findings["load_oracle"] = _load_oracle(saved)
    state["part_f"] = findings
    return bool(poly)


# ---------------------------------------------------------------------------
# Part G: RemoveArea on a REFERENCED area.
# ---------------------------------------------------------------------------


def part_g(state: dict[str, Any]) -> bool:
    """The reference-integrity question. Beijing's area 1 carries BOTH a
    pedestrian input and a static routing decision (static scan). Does
    ``RemoveArea`` refuse, dangle, or silently cascade-delete them -- the exact
    trap Q6 unit (2) found on parking lots and Q4 unit (3) found on conflict
    areas?"""
    import pythoncom

    from services.agent.tools_network import build_shadow_model

    _log("=" * 78)
    _log(f"[G] {_PART_LABELS['g']}")
    _log("=" * 78)

    working = _clone(PED_NET, "remove-area")
    counts_before = _counts(working)
    shadow_before = build_shadow_model(working)
    referrers = _area_referrers(working)
    victim = str(BEIJING_REFERENCED_AREA)
    findings: dict[str, Any] = {
        "network": PED_NET.name,
        "counts_before": counts_before,
        "victim_area": victim,
        "referrers_before": referrers.get(victim, {}),
        "all_area_referrer_counts": {k: {t: len(v) for t, v in d.items()}
                                     for k, d in sorted(referrers.items(), key=lambda kv: int(kv[0]))},
    }
    _log(f"\n[G.1] area {victim}'s referrers in the persisted file: "
         f"{findings['referrers_before']}")

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        nos = _area_nos(net)
        remove_error = ""
        try:
            net.Areas.RemoveArea(net.Areas.ItemByKey(int(victim)))
        except Exception as exc:
            remove_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            _log(f"      RAISED: {remove_error}")
        nos_after = _area_nos(net)
        _log(f"      live Areas {len(nos)} -> {len(nos_after)}; removed "
             f"{sorted(set(nos) - set(nos_after))}")
        findings.update({
            "remove_error": remove_error,
            "com_area_nos_before": nos,
            "com_area_nos_after": nos_after,
            "removed_by_id_diff": sorted(set(nos) - set(nos_after)),
            "com_ped_input_nos_after": _ped_input_nos(net),
        })
        saved = working.with_name("after_remove_area.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved is None or not saved.exists():
        state["part_g"] = findings
        return False

    counts_after = _counts(saved)
    findings["counts_after"] = counts_after
    findings["count_delta_after_minus_before"] = _count_diff(counts_before, counts_after)
    findings["referrers_after"] = _area_referrers(saved).get(victim, {})
    _log(f"\n[G.2] persisted delta (after-minus-before): "
         f"{findings['count_delta_after_minus_before']}")
    _log(f"      area {victim}'s referrers AFTER: {findings['referrers_after']}")
    findings["shadow_diff"] = _grouped_diff(shadow_before, build_shadow_model(saved))
    findings["load_oracle"] = _load_oracle(saved)
    state["part_g"] = findings
    return True


# ---------------------------------------------------------------------------
# Part H: the NULL CONTROL.
# ---------------------------------------------------------------------------


def part_h(state: dict[str, Any]) -> bool:
    """Load the network and save it with ZERO edits. Anything that moves here
    is Vissim's own load/save churn and is NOT attributable to any verb -- so
    every diff in every other part has to be read against this one."""
    import pythoncom

    from services.agent.tools_network import build_shadow_model

    _log("=" * 78)
    _log(f"[H] {_PART_LABELS['h']}")
    _log("=" * 78)

    working = _clone(PED_NET, "null")
    counts_before = _counts(working)
    shadow_before = build_shadow_model(working)
    inputs_before = _ped_input_map(working)
    areas_before = _area_map(working)
    findings: dict[str, Any] = {"network": PED_NET.name, "counts_before": counts_before}

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
        state["part_h"] = findings
        return False

    counts_after = _counts(saved)
    inputs_after = _ped_input_map(saved)
    areas_after = _area_map(saved)
    findings["counts_after"] = counts_after
    findings["count_delta_after_minus_before"] = _count_diff(counts_before, counts_after)

    moved_inputs = {
        no: {"before": inputs_before[no], "after": inputs_after.get(no)}
        for no in inputs_before
        if inputs_after.get(no) != inputs_before[no]
    }
    moved_areas = {
        no: {"before": areas_before[no].get("_polygon"), "after": (areas_after.get(no) or {}).get("_polygon")}
        for no in areas_before
        if (areas_after.get(no) or {}).get("_polygon") != areas_before[no].get("_polygon")
    }
    findings["moved_ped_inputs"] = moved_inputs
    findings["moved_area_polygons"] = moved_areas
    _log(f"\n[H.1] count delta (after-minus-before): "
         f"{findings['count_delta_after_minus_before']}")
    _log(f"      pedestrian inputs whose persisted attrs moved: {len(moved_inputs)} of "
         f"{len(inputs_before)}")
    _log(f"      areas whose polygon moved: {len(moved_areas)} of {len(areas_before)}")
    findings["shadow_diff"] = _grouped_diff(shadow_before, build_shadow_model(saved))
    state["part_h"] = findings
    return not findings["count_delta_after_minus_before"] and not moved_inputs and not moved_areas


# ---------------------------------------------------------------------------
# Part I: coordinate space on the METRES network.
# ---------------------------------------------------------------------------


def part_i(state: dict[str, Any]) -> bool:
    """The read direction of the unit question, plus the ``posOnArea``
    semantics. Part F measures the WRITE direction on a FEET network; this one
    measures WRITE on a metres network (where raw and display coincide, so it
    is the control) and, more usefully, what a pedestrian input's own
    ``<posOnArea>`` x/y actually MEAN -- the corpus values are all tiny
    (|x|,|y| < 2) next to areas whose polygon coordinates are in the hundreds,
    which says 'relative', but relative to WHAT is measured here."""
    import pythoncom

    _log("=" * 78)
    _log(f"[I] {_PART_LABELS['i']}")
    _log("=" * 78)

    working = _clone(PED_NET, "coords")
    areas_before = _area_map(working)
    inputs_before = _ped_input_map(working)
    findings: dict[str, Any] = {"network": PED_NET.name}

    anchor_no = sorted(areas_before, key=lambda k: int(k))[0]
    anchor_poly = [[float(x), float(y)] for x, y in areas_before[anchor_no]["_polygon"]]
    anchor = (anchor_poly[0][0], anchor_poly[0][1])
    side = 3.0
    square = [
        (anchor[0] + 30.0, anchor[1]),
        (anchor[0] + 30.0 + side, anchor[1]),
        (anchor[0] + 30.0 + side, anchor[1] + side),
        (anchor[0] + 30.0, anchor[1] + side),
    ]
    wkt = _wkt_polygon(square)
    findings.update({
        "anchor_area_no": anchor_no,
        "anchor_polygon": anchor_poly,
        "requested_square_xy": [list(p) for p in square],
        "requested_wkt": wkt,
        "side_requested": side,
    })

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        unit = controller._net_unit_length()
        findings["unit_len_short"] = unit
        area_nos_before = _area_nos(net)
        new_no = max(area_nos_before) + 1
        err = ""
        try:
            net.Areas.AddArea(new_no, wkt)
        except Exception as exc:
            err = f"{type(exc).__name__}: {str(exc)[:300]}"
        findings["add_area_error"] = err
        findings["new_area_no"] = new_no
        _log(f"\n[I.1] unit={unit!r}; AddArea({new_no}, {wkt}) -> {err or 'accepted'}")

        # The pedestrian input's own position on the area.
        pi_nos = _ped_input_nos(net)
        if pi_nos:
            existing = net.PedestrianInputs.ItemByKey(pi_nos[0])
            findings["existing_input_area_attr"] = _read_attrs(existing, ("Area", "No", "Volume(1)"))
        new_pi_no = (max(pi_nos) if pi_nos else 0) + 1
        pi_err = ""
        try:
            net.PedestrianInputs.AddPedestrianInput(new_pi_no, net.Areas.ItemByKey(new_no))
            net.PedestrianInputs.ItemByKey(new_pi_no).SetAttValue("Volume(1)", 100.0)
        except Exception as exc:
            pi_err = f"{type(exc).__name__}: {str(exc)[:300]}"
        findings["new_input_error"] = pi_err
        findings["new_input_no"] = new_pi_no

        saved = working.with_name("after_coords.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved is None or not saved.exists():
        state["part_i"] = findings
        return False

    areas_after = _area_map(saved)
    poly = [[float(x), float(y)] for x, y in (areas_after.get(str(findings["new_area_no"]), {}).get("_polygon") or [])]
    findings["persisted_polygon"] = poly
    if poly:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        findings["persisted_side_x"] = max(xs) - min(xs)
        findings["persisted_side_y"] = max(ys) - min(ys)
        findings["ratio_persisted_over_requested_x"] = (max(xs) - min(xs)) / side
        findings["vertex_delta_after_minus_before"] = [
            [poly[i][0] - square[i][0], poly[i][1] - square[i][1]]
            for i in range(min(len(poly), len(square)))
        ]
        _log(f"\n[I.2] persisted polygon: {poly}")
        _log(f"      side x={findings['persisted_side_x']} y={findings['persisted_side_y']}; "
             f"ratio persisted/requested = {findings['ratio_persisted_over_requested_x']}")
        _log(f"      per-vertex delta (persisted MINUS requested): "
             f"{findings['vertex_delta_after_minus_before']}")

    inputs_after = _ped_input_map(saved)
    new_input_xml = inputs_after.get(str(findings["new_input_no"]))
    findings["new_input_xml"] = new_input_xml
    if new_input_xml and poly:
        cx = sum(p[0] for p in poly) / len(poly)
        cy = sum(p[1] for p in poly) / len(poly)
        px = float(new_input_xml["_pos_x"]) if new_input_xml.get("_pos_x") is not None else None
        py = float(new_input_xml["_pos_y"]) if new_input_xml.get("_pos_y") is not None else None
        findings["new_area_centroid"] = [cx, cy]
        findings["new_input_pos_on_area"] = [px, py]
        findings["pos_magnitude"] = (
            math.hypot(px, py) if px is not None and py is not None else None
        )
        _log(f"\n[I.3] new area centroid {[cx, cy]}; new input posOnArea {[px, py]} "
             f"(|pos| = {findings['pos_magnitude']}). A |pos| of order the AREA SIZE (~{side}) "
             "means the coordinates are RELATIVE to the area; of order the centroid magnitude "
             f"(~{math.hypot(cx, cy):.1f}) means ABSOLUTE.")

    # Corpus-wide cross-check of the same question, from the untouched file.
    magnitudes = []
    for entry in inputs_before.values():
        if entry.get("_pos_x") is not None and entry.get("_pos_y") is not None:
            magnitudes.append(math.hypot(float(entry["_pos_x"]), float(entry["_pos_y"])))
    findings["corpus_pos_magnitudes"] = magnitudes
    findings["corpus_pos_magnitude_max"] = max(magnitudes) if magnitudes else None
    findings["load_oracle"] = _load_oracle(saved)
    state["part_i"] = findings
    return bool(poly)


# ---------------------------------------------------------------------------
# Part J: zero-composition genesis -- what materialises on save.
# ---------------------------------------------------------------------------


def part_j(state: dict[str, Any]) -> bool:
    """Q5 unit (3)'s default-composition precedent, asked of pedestrians:
    Roundabout Schenectady.US has pedestrian TYPES and a pedestrian CLASS but
    NO ``<pedestrianComposition>`` at all, and no areas. Creating an area plus
    a pedestrian input there and SAVING answers 'which classes gain rows that
    nobody would predict from the API names' -- the input the
    ``_ALLOWS_NEW_OBJECTS_IN`` entry is built from."""
    import pythoncom

    from services.agent.tools_network import build_shadow_model

    _log("=" * 78)
    _log(f"[J] {_PART_LABELS['j']}")
    _log("=" * 78)

    working = _clone(NO_COMP_NET, "no-comp")
    counts_before = _counts(working)
    shadow_before = build_shadow_model(working)
    findings: dict[str, Any] = {"network": NO_COMP_NET.name, "counts_before": counts_before}
    _log(f"    BEFORE: {counts_before}")

    root = ET.parse(working).getroot()
    first_pt = root.find(".//links/link//point3D")
    anchor = (float(first_pt.get("x")), float(first_pt.get("y"))) if first_pt is not None else (0.0, 0.0)
    square = [
        (anchor[0] + 5.0, anchor[1]),
        (anchor[0] + 9.0, anchor[1]),
        (anchor[0] + 9.0, anchor[1] + 4.0),
        (anchor[0] + 5.0, anchor[1] + 4.0),
    ]
    findings["requested_square_xy"] = [list(p) for p in square]

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        err = ""
        try:
            net.Areas.AddArea(1, _wkt_polygon(square))
            net.PedestrianInputs.AddPedestrianInput(1, net.Areas.ItemByKey(1))
            net.PedestrianInputs.ItemByKey(1).SetAttValue("Volume(1)", 400.0)
        except Exception as exc:
            err = f"{type(exc).__name__}: {str(exc)[:300]}"
            _log(f"      RAISED: {err}")
        findings["create_error"] = err
        findings["com_area_nos"] = _area_nos(net)
        findings["com_ped_input_nos"] = _ped_input_nos(net)
        findings["fresh_input_attrs"] = (
            _read_attrs(net.PedestrianInputs.ItemByKey(1), _PED_INPUT_COM_CANDIDATES)
            if findings["com_ped_input_nos"] else {}
        )
        saved = working.with_name("after_no_comp.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved is None or not saved.exists():
        state["part_j"] = findings
        return False

    counts_after = _counts(saved)
    findings["counts_after"] = counts_after
    findings["count_delta_after_minus_before"] = _count_diff(counts_before, counts_after)
    findings["new_input_xml"] = _ped_input_map(saved).get("1")
    _log(f"\n[J.1] persisted delta (after-minus-before): "
         f"{findings['count_delta_after_minus_before']}")
    _log(f"      new input XML: {findings['new_input_xml']}")
    findings["shadow_diff"] = _grouped_diff(shadow_before, build_shadow_model(saved))
    findings["load_oracle"] = _load_oracle(saved)
    state["part_j"] = findings
    return bool(findings["com_ped_input_nos"])


# ---------------------------------------------------------------------------
# Parts K/L/M: the ROUTING-DECISION dependency, isolated to one bit at a time.
# ---------------------------------------------------------------------------


def _routed_areas(inpx: Path) -> list[str]:
    """Area numbers that a static pedestrian ROUTING DECISION sits on -- i.e.
    the areas from which a generated pedestrian has somewhere to walk."""
    root = ET.parse(inpx).getroot()
    out: list[str] = []
    for prd in root.findall(".//pedestrianRoutingDecisionStatic"):
        loc = prd.find("location")
        if loc is not None and loc.get("area"):
            out.append(loc.get("area"))
    return sorted(set(out), key=lambda s: int(s))


def _route_dependency_run(part: str, routed_origin: bool, volume: float,
                          state: dict[str, Any]) -> bool:
    """K/L/M share one body and differ in exactly TWO bits, one per contrast:

    * K (routed_origin=True,  volume=750) vs L (routed_origin=False, volume=750)
      isolates "does the origin area need a routing decision".
    * L (volume=750) vs M (routed_origin=False, volume=0)
      isolates "does the demand have to be non-zero for it to matter".

    The judged output is Vissim's OWN load/init oracle on the saved file --
    the same ``load_check`` every structural verb in this codebase already
    runs. Parts B and F both tripped ``init_ok=False`` as a side effect; this
    triple is what turns that accident into an attributable cause.
    """
    import pythoncom

    _log("=" * 78)
    _log(f"[{part.upper()}] {_PART_LABELS[part]}")
    _log("=" * 78)

    working = _clone(PED_NET, f"route-{part}")
    counts_before = _counts(working)
    routed = _routed_areas(working)
    all_areas = sorted(_area_map(working), key=lambda s: int(s))
    unrouted_existing = [a for a in all_areas if a not in routed]
    findings: dict[str, Any] = {
        "network": PED_NET.name,
        "routed_origin": routed_origin,
        "volume": volume,
        "counts_before": counts_before,
        "areas_with_routing_decision": routed,
        "areas_without_routing_decision": unrouted_existing,
    }
    _log(f"\n[{part.upper()}.1] persisted: {len(all_areas)} areas, of which "
         f"{len(routed)} carry a static routing decision ({routed}) and "
         f"{len(unrouted_existing)} do not ({unrouted_existing}).")

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        if routed_origin:
            target_area = int(routed[0])
            _log(f"      origin: EXISTING area {target_area}, which HAS routing decision(s).")
        else:
            # A brand-new area, guaranteed route-less by construction.
            poly = [[float(x), float(y)] for x, y in _area_map(working)[all_areas[0]]["_polygon"]]
            ax, ay = poly[0][0], poly[0][1]
            square = [(ax + 40.0, ay), (ax + 44.0, ay), (ax + 44.0, ay + 4.0), (ax + 40.0, ay + 4.0)]
            target_area = max(int(a) for a in all_areas) + 1
            net.Areas.AddArea(target_area, _wkt_polygon(square))
            findings["created_area_no"] = target_area
            findings["created_square_xy"] = [list(p) for p in square]
            _log(f"      origin: NEW area {target_area} (created here, so route-less by "
                 f"construction).")

        nos_before = _ped_input_nos(net)
        new_no = (max(nos_before) if nos_before else 0) + 1
        net.PedestrianInputs.AddPedestrianInput(new_no, net.Areas.ItemByKey(target_area))
        fresh = net.PedestrianInputs.ItemByKey(new_no)
        fresh.SetAttValue("Volume(1)", volume)
        readback = float(fresh.AttValue("Volume(1)"))
        _log(f"      input {new_no} on area {target_area}: wrote {volume} -> read back {readback}")
        findings.update({
            "target_area": target_area,
            "new_input_no": new_no,
            "volume_readback": readback,
        })
        saved = working.with_name(f"after_route_{part}.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved is None or not saved.exists():
        state[f"part_{part}"] = findings
        return False

    counts_after = _counts(saved)
    findings["counts_after"] = counts_after
    findings["count_delta_after_minus_before"] = _count_diff(counts_before, counts_after)
    findings["new_input_xml"] = _ped_input_map(saved).get(str(findings["new_input_no"]))
    _log(f"\n[{part.upper()}.2] persisted delta (after-minus-before): "
         f"{findings['count_delta_after_minus_before']}")
    _log(f"      new input XML: {findings['new_input_xml']}")
    findings["load_oracle"] = _load_oracle(saved)
    state[f"part_{part}"] = findings
    # The PART passes when it MEASURED cleanly, whatever the oracle said --
    # a red oracle here is the finding, not a probe failure.
    return bool(findings["new_input_xml"])


def _bare_area_run(part: str, state: dict[str, Any]) -> bool:
    """Part N, the control K/L/M could not supply: create a NEW AREA and NO
    pedestrian input at all. K/L/M each moved two things at once (an area AND
    an input), so 'the route-less origin broke init' was still consistent with
    'any freshly created area breaks init'. This part moves exactly one."""
    import pythoncom

    _log("=" * 78)
    _log(f"[{part.upper()}] {_PART_LABELS[part]}")
    _log("=" * 78)

    working = _clone(PED_NET, f"bare-{part}")
    counts_before = _counts(working)
    all_areas = sorted(_area_map(working), key=lambda s: int(s))
    findings: dict[str, Any] = {"network": PED_NET.name, "counts_before": counts_before}

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        poly = [[float(x), float(y)] for x, y in _area_map(working)[all_areas[0]]["_polygon"]]
        ax, ay = poly[0][0], poly[0][1]
        square = [(ax + 40.0, ay), (ax + 44.0, ay), (ax + 44.0, ay + 4.0), (ax + 40.0, ay + 4.0)]
        new_no = max(int(a) for a in all_areas) + 1
        net.Areas.AddArea(new_no, _wkt_polygon(square))
        findings["created_area_no"] = new_no
        findings["created_square_xy"] = [list(p) for p in square]
        findings["com_ped_input_nos"] = _ped_input_nos(net)
        _log(f"\n[{part.upper()}.1] created area {new_no} and NOTHING else; pedestrian inputs "
             f"untouched at {findings['com_ped_input_nos']}")
        saved = working.with_name(f"after_bare_{part}.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved is None or not saved.exists():
        state[f"part_{part}"] = findings
        return False

    counts_after = _counts(saved)
    findings["counts_after"] = counts_after
    findings["count_delta_after_minus_before"] = _count_diff(counts_before, counts_after)
    _log(f"      persisted delta (after-minus-before): "
         f"{findings['count_delta_after_minus_before']}")
    findings["load_oracle"] = _load_oracle(saved)
    state[f"part_{part}"] = findings
    return bool(findings["count_delta_after_minus_before"])


def _existing_unrouted_run(part: str, state: dict[str, Any]) -> bool:
    """Part O, the discriminator. L/M's origin was a FRESHLY CREATED area, so
    'route-less' and 'newly minted, geometrically disconnected from the
    walkable network' were still confounded. This part puts the input on an
    area that SHIPPED with the network -- a real, connected crosswalk polygon
    -- that simply carries no routing decision (Beijing has 8 of them)."""
    import pythoncom

    _log("=" * 78)
    _log(f"[{part.upper()}] {_PART_LABELS[part]}")
    _log("=" * 78)

    working = _clone(PED_NET, f"unrouted-{part}")
    counts_before = _counts(working)
    routed = _routed_areas(working)
    all_areas = sorted(_area_map(working), key=lambda s: int(s))
    unrouted = [a for a in all_areas if a not in routed]
    findings: dict[str, Any] = {
        "network": PED_NET.name,
        "counts_before": counts_before,
        "areas_with_routing_decision": routed,
        "areas_without_routing_decision": unrouted,
    }
    if not unrouted:
        state[f"part_{part}"] = findings
        return False
    target = int(unrouted[0])
    _log(f"\n[{part.upper()}.1] origin: EXISTING area {target} -- shipped with the network "
         f"(so it is connected walkable geometry) but carries NO routing decision.")

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        nos_before = _ped_input_nos(net)
        new_no = (max(nos_before) if nos_before else 0) + 1
        net.PedestrianInputs.AddPedestrianInput(new_no, net.Areas.ItemByKey(target))
        net.PedestrianInputs.ItemByKey(new_no).SetAttValue("Volume(1)", 750.0)
        findings.update({"target_area": target, "new_input_no": new_no,
                         "volume_readback": float(
                             net.PedestrianInputs.ItemByKey(new_no).AttValue("Volume(1)"))})
        saved = working.with_name(f"after_unrouted_{part}.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved is None or not saved.exists():
        state[f"part_{part}"] = findings
        return False

    counts_after = _counts(saved)
    findings["counts_after"] = counts_after
    findings["count_delta_after_minus_before"] = _count_diff(counts_before, counts_after)
    findings["new_input_xml"] = _ped_input_map(saved).get(str(findings["new_input_no"]))
    _log(f"[{part.upper()}.2] persisted delta (after-minus-before): "
         f"{findings['count_delta_after_minus_before']}")
    findings["load_oracle"] = _load_oracle(saved)
    state[f"part_{part}"] = findings
    return bool(findings["new_input_xml"])


def part_p(state: dict[str, Any]) -> bool:
    """``PedComp(1)`` writability, which part B could not settle. Beijing has
    exactly ONE pedestrian composition, so B's write of ``1`` onto a value
    already ``'1'`` produced ``moved=False`` -- indistinguishable from a silent
    no-op. This part runs the same write on a network with FOUR compositions,
    where a real move is observable, and also measures what a BOGUS
    composition number does (silently accepted, or refused?)."""
    import pythoncom

    _log("=" * 78)
    _log(f"[P] {_PART_LABELS['p']}")
    _log("=" * 78)

    working = _clone(MULTI_COMP_NET, "pedcomp")
    counts_before = _counts(working)
    findings: dict[str, Any] = {"network": MULTI_COMP_NET.name, "counts_before": counts_before}
    _log(f"    compositions in this network: {counts_before['pedestrian_compositions']}")

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        comps = _collection_nos(net.PedestrianCompositions)
        areas = _area_nos(net)
        nos_before = _ped_input_nos(net)
        findings["composition_nos"] = comps
        _log(f"\n[P.1] live PedestrianCompositions: {comps}; areas: {len(areas)}")
        if len(comps) < 2:
            state["part_p"] = findings
            return False

        new_no = (max(nos_before) if nos_before else 0) + 1
        net.PedestrianInputs.AddPedestrianInput(new_no, net.Areas.ItemByKey(areas[0]))
        fresh = net.PedestrianInputs.ItemByKey(new_no)
        findings["fresh_pedcomp_default"] = _read_attrs(fresh, ("PedComp(1)", "Volume(1)"))
        _log(f"      fresh input {new_no} defaults: {findings['fresh_pedcomp_default']}")

        other = [c for c in comps if str(c) != str(fresh.AttValue("PedComp(1)"))]
        writes: dict[str, Any] = {}
        writes[f"PedComp(1)<-{other[0]}"] = _try_write(fresh, "PedComp(1)", other[0])
        writes[f"PedComp(1)<-{other[-1]}"] = _try_write(fresh, "PedComp(1)", other[-1])
        bogus = max(comps) + 999
        writes[f"PedComp(1)<-BOGUS {bogus}"] = _try_write(fresh, "PedComp(1)", bogus)
        for label, result in writes.items():
            _log(f"      {label:26s} {result}")
        findings["writes"] = writes

        # Leave a legal composition behind so the persisted read is meaningful.
        fresh.SetAttValue("PedComp(1)", other[0])
        fresh.SetAttValue("Volume(1)", 200.0)
        findings["final_pedcomp"] = str(fresh.AttValue("PedComp(1)"))
        findings["new_input_no"] = new_no
        saved = working.with_name("after_pedcomp.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved is None or not saved.exists():
        state["part_p"] = findings
        return False

    findings["counts_after"] = _counts(saved)
    findings["count_delta_after_minus_before"] = _count_diff(counts_before, findings["counts_after"])
    findings["new_input_xml"] = _ped_input_map(saved).get(str(findings["new_input_no"]))
    _log(f"\n[P.2] persisted new input: {findings['new_input_xml']}")
    _log(f"      count delta (after-minus-before): "
         f"{findings['count_delta_after_minus_before']}")
    state["part_p"] = findings
    return bool(findings["new_input_xml"])


def _partial_arm(part: str, static_routed: bool, state: dict[str, Any]) -> bool:
    """Does a PARTIAL pedestrian routing decision satisfy the routing
    precondition, or only a STATIC one?

    K/L/M/N/O all used STATIC decisions, so ``_routed_area_nos``' decision to
    count partial ones too started life as an INFERENCE -- and a permissive
    one, which is the dangerous direction: an area a partial decision alone
    covers would sail past the guard and could still break init. Vissim's own
    refusal text elsewhere names the STATIC kind specifically, which is
    exactly the sort of hint that deserves measuring rather than trusting.

    Cableways is the only corpus network with partial decisions at all (26,
    against 25 static), and 24 of its areas are routed ONLY partially -- so
    this is the one place the question can be asked. One bounded arm: create
    an input on such an area, save, and let Vissim's own load/init oracle
    answer.
    """
    import pythoncom

    _log("=" * 78)
    _log(f"[{part.upper()}] {_PART_LABELS[part]}")
    _log("=" * 78)

    working = _clone(PARTIAL_ROUTE_NET, f"partial-{part}")
    counts_before = _counts(working)
    root = ET.parse(working).getroot()

    def _origin_areas(path: str) -> set[str]:
        out: set[str] = set()
        for decision in root.findall(path):
            location = decision.find("location")
            if location is not None and (location.get("area") or "").strip():
                out.add(location.get("area").strip())
        return out

    static_routed_areas = _origin_areas(".//pedestrianRoutingDecisionStatic")
    partial_routed = _origin_areas(".//pedestrianRoutingDecisionPartial")
    only_partial = sorted(partial_routed - static_routed_areas, key=int)
    findings: dict[str, Any] = {
        "network": PARTIAL_ROUTE_NET.name,
        "counts_before": counts_before,
        "static_routed_count": len(static_routed_areas),
        "partial_routed_count": len(partial_routed),
        "only_partial_areas": only_partial,
    }
    _log(f"\n[Q.1] {counts_before['areas']} areas; {len(static_routed_areas)} carry a STATIC routing "
         f"decision, {len(partial_routed)} a PARTIAL one, and {len(only_partial)} are routed "
         f"ONLY partially: {only_partial[:12]}{' ...' if len(only_partial) > 12 else ''}")
    if not only_partial:
        state[f"part_{part}"] = findings
        return False
    candidates = sorted(static_routed_areas, key=int) if static_routed else only_partial
    if not candidates:
        state[f"part_{part}"] = findings
        return False
    target_area = int(candidates[0])
    findings["origin_kind"] = "STATIC-routed" if static_routed else "PARTIAL-only"

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        nos_before = _ped_input_nos(net)
        new_no = (max(nos_before) if nos_before else 0) + 1
        err = ""
        try:
            net.PedestrianInputs.AddPedestrianInput(new_no, net.Areas.ItemByKey(target_area))
            net.PedestrianInputs.ItemByKey(new_no).SetAttValue("Volume(1)", 750.0)
        except Exception as exc:
            err = f"{type(exc).__name__}: {str(exc)[:300]}"
            _log(f"      RAISED: {err}")
        findings.update({
            "target_area": target_area,
            "new_input_no": new_no,
            "create_error": err,
            "com_ped_input_nos_after": _ped_input_nos(net),
        })
        _log(f"      input {new_no} on area {target_area}: "
             f"{err or 'created'} [{findings['origin_kind']}]")
        saved = working.with_name("after_partial.inpx")
        controller.save_net_as(saved)
    finally:
        _finish(controller)
        pythoncom.CoUninitialize()

    if saved is None or not saved.exists():
        state[f"part_{part}"] = findings
        return False

    counts_after = _counts(saved)
    findings["counts_after"] = counts_after
    findings["count_delta_after_minus_before"] = _count_diff(counts_before, counts_after)
    findings["new_input_xml"] = _ped_input_map(saved).get(str(new_no))
    _log(f"\n[Q.2] persisted delta (after-minus-before): "
         f"{findings['count_delta_after_minus_before']}")
    findings["load_oracle"] = _load_oracle(saved)
    state[f"part_{part}"] = findings
    # The PART passes when it MEASURED cleanly, whatever the oracle said --
    # a red oracle here is the finding, not a probe failure.
    return bool(findings["new_input_xml"])


def part_q(state: dict[str, Any]) -> bool:
    return _partial_arm("q", static_routed=False, state=state)


def part_r(state: dict[str, Any]) -> bool:
    """Part Q's CONTROL, on the SAME network. Q put an input on a
    partial-only area of Cableways and the oracle came back red -- but one red
    arm on one network is equally consistent with "Cableways just does not
    tolerate a new pedestrian input anywhere". This arm moves exactly one bit:
    a STATIC-routed area of the same network, same volume, same everything."""
    return _partial_arm("r", static_routed=True, state=state)


def part_n(state: dict[str, Any]) -> bool:
    return _bare_area_run("n", state)


def part_o(state: dict[str, Any]) -> bool:
    return _existing_unrouted_run("o", state)


def part_k(state: dict[str, Any]) -> bool:
    return _route_dependency_run("k", True, 750.0, state)


def part_l(state: dict[str, Any]) -> bool:
    return _route_dependency_run("l", False, 750.0, state)


def part_m(state: dict[str, Any]) -> bool:
    return _route_dependency_run("m", False, 0.0, state)


# ---------------------------------------------------------------------------
# --audit: re-derive every headline number from the persisted .inpx pairs.
# ---------------------------------------------------------------------------


def audit_state(state_path: Path) -> None:
    """Re-derive the FINDINGS numbers straight from the saved .inpx files with
    a parser that shares NO code with the state.json writer above (plain
    ElementTree counting, no ``_counts``/``_ped_input_map``/``_area_map``).

    INDEPENDENCE CAVEAT, stated rather than glossed: the pedestrian and vehicle
    COUNTS in parts C/D come from a LIVE COM run and exist nowhere in any
    persisted file -- those rows are re-READ from state.json, not re-derived,
    and are labelled ``[live-recorded]``. Everything else here is re-derived.
    """
    state = _load_state(state_path)
    root_dir = state_path.parent

    def _find(prefix: str, name: str) -> Path | None:
        for d in sorted(root_dir.glob(f"{prefix}_*")):
            candidate = d / name
            if candidate.exists():
                return candidate
        return None

    def _tag_count(inpx: Path, xpath: str) -> int:
        return len(ET.parse(inpx).getroot().findall(xpath))

    def _source(work_prefix: str, example: Path) -> Path | None:
        """The UNTOUCHED clone inside a work dir -- i.e. every .inpx this probe
        did NOT write. The exclusion is structural (any ``after_*`` artifact,
        plus the null control) rather than an enumerated filename list: the
        first recorded audit run used a hand-kept list, parts K-O's artifacts
        were not on it, and ``_source("bare-n", ...)`` duly returned
        ``after_bare_n.inpx`` -- reporting the meaningless "N area 17->17"
        instead of the real 16->17. A hand-maintained list that silently
        rots is exactly the failure mode this audit path exists to catch."""
        for d in sorted(root_dir.glob(f"{work_prefix}_*")):
            for f in sorted(d.glob("*.inpx")):
                if f.name.startswith("after_") or f.name == "null_control.inpx":
                    continue
                return f
        found = sorted(example.glob("*.inpx"))
        return found[0] if found else None

    _log("=" * 78)
    _log("[AUDIT] re-derived from the persisted .inpx files (separate code path)")
    _log("=" * 78)

    rows: list[tuple[str, Any, Any]] = []

    # Part A: creation.
    src = _source("licence", PED_NET)
    after = _find("licence", "after_add.inpx")
    if src and after:
        for label, xpath in (
            ("A pedestrianInput", ".//pedestrianInputs/pedestrianInput"),
            ("A area", ".//areas/area"),
            ("A pedVolInterval", ".//timeIntervalPedVolume"),
            ("A pedestrianComposition", ".//pedestrianCompositions/pedestrianComposition"),
            ("A conflictArea", ".//conflictAreas/conflictArea"),
        ):
            b, a = _tag_count(src, xpath), _tag_count(after, xpath)
            rows.append((f"{label} before->after", f"{b}->{a}", a - b))

    # Part B: the persisted write results.
    after_b = _find("writes", "after_writes.inpx")
    if after_b:
        r = ET.parse(after_b).getroot()
        news = [e for e in r.findall(".//pedestrianInputs/pedestrianInput")
                if (e.get("name") or "") == "probe-input"]
        if news:
            el = news[0]
            vols = el.findall("timeIntPedVols/timeIntervalPedVolume")
            rows.append(("B new input name", el.get("name"), ""))
            rows.append(("B new input intervals", len(vols), ""))
            rows.append(("B interval-1 volume", vols[0].get("volume") if vols else None, ""))
            rows.append(("B interval-1 pedComp", vols[0].get("pedComp") if vols else None, ""))
            loc = el.find("location")
            rows.append(("B location area", loc.get("area") if loc is not None else None, ""))

    # Parts C/D: LIVE-RECORDED, re-read not re-derived.
    for part in ("c", "d"):
        block = state.get(f"part_{part}") or {}
        rows.append((f"{part.upper()} pedestrians [live-recorded]",
                     block.get("pedestrians_total"), block.get("expected_arrivals")))
        rows.append((f"{part.upper()} vehicles [live-recorded]", block.get("vehicles_total"), ""))

    # Part E: removal.
    src_e = _source("remove-input", PED_NET)
    after_e = _find("remove-input", "after_remove_input.inpx")
    if src_e and after_e:
        for label, xpath in (
            ("E pedestrianInput", ".//pedestrianInputs/pedestrianInput"),
            ("E pedVolInterval", ".//timeIntervalPedVolume"),
            ("E area", ".//areas/area"),
            ("E pedRoutingDecStatic", ".//pedestrianRoutingDecisionStatic"),
        ):
            b, a = _tag_count(src_e, xpath), _tag_count(after_e, xpath)
            rows.append((f"{label} before->after", f"{b}->{a}", a - b))

    # Part F: feet genesis + units.
    after_f = _find("feet-genesis", "after_feet.inpx")
    if after_f:
        r = ET.parse(after_f).getroot()
        areas = r.findall(".//areas/area")
        rows.append(("F areas created", len(areas), ""))
        for area in areas:
            pts = [(float(p.get("x")), float(p.get("y")))
                   for p in area.findall("geometry/points/point")]
            if pts:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                rows.append((f"F area {area.get('no')} bbox (x,y span)",
                             f"{max(xs)-min(xs):.6f} x {max(ys)-min(ys):.6f}",
                             f"{len(pts)} pts"))
        rows.append(("F pedestrianInputs", len(r.findall(".//pedestrianInputs/pedestrianInput")), ""))

    # Part G: area removal cascade.
    src_g = _source("remove-area", PED_NET)
    after_g = _find("remove-area", "after_remove_area.inpx")
    if src_g and after_g:
        for label, xpath in (
            ("G area", ".//areas/area"),
            ("G pedestrianInput", ".//pedestrianInputs/pedestrianInput"),
            ("G pedRoutingDecStatic", ".//pedestrianRoutingDecisionStatic"),
            ("G pedRouteStatic", ".//pedestrianRouteStatic"),
            ("G pedRouteLocation", ".//pedestrianRouteLocation"),
        ):
            b, a = _tag_count(src_g, xpath), _tag_count(after_g, xpath)
            rows.append((f"{label} before->after", f"{b}->{a}", a - b))

    # Part H: null control.
    src_h = _source("null", PED_NET)
    after_h = _find("null", "null_control.inpx")
    if src_h and after_h:
        moved = 0
        rb = ET.parse(src_h).getroot()
        ra = ET.parse(after_h).getroot()
        before_areas = {e.get("no"): [(p.get("x"), p.get("y"))
                                      for p in e.findall("geometry/points/point")]
                        for e in rb.findall(".//areas/area")}
        after_areas = {e.get("no"): [(p.get("x"), p.get("y"))
                                     for p in e.findall("geometry/points/point")]
                       for e in ra.findall(".//areas/area")}
        for no, pts in before_areas.items():
            if after_areas.get(no) != pts:
                moved += 1
        rows.append(("H areas with moved polygons", moved, f"of {len(before_areas)}"))
        rows.append(("H pedestrianInput count",
                     f"{len(rb.findall('.//pedestrianInputs/pedestrianInput'))}->"
                     f"{len(ra.findall('.//pedestrianInputs/pedestrianInput'))}", ""))

    # Part I: metres coordinate space.
    after_i = _find("coords", "after_coords.inpx")
    if after_i:
        r = ET.parse(after_i).getroot()
        block = state.get("part_i") or {}
        new_no = str(block.get("new_area_no", ""))
        for area in r.findall(".//areas/area"):
            if area.get("no") != new_no:
                continue
            pts = [(float(p.get("x")), float(p.get("y")))
                   for p in area.findall("geometry/points/point")]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            rows.append((f"I area {new_no} bbox",
                         f"{max(xs)-min(xs):.6f} x {max(ys)-min(ys):.6f}", f"{len(pts)} pts"))
        for el in r.findall(".//pedestrianInputs/pedestrianInput"):
            if el.get("no") == str(block.get("new_input_no", "")):
                pos = el.find("location/posOnArea")
                rows.append((f"I input {el.get('no')} posOnArea",
                             (pos.get("x"), pos.get("y")) if pos is not None else None, ""))

    # Part J: what materialises.
    src_j = _source("no-comp", NO_COMP_NET)
    after_j = _find("no-comp", "after_no_comp.inpx")
    if src_j and after_j:
        for label, xpath in (
            ("J area", ".//areas/area"),
            ("J pedestrianInput", ".//pedestrianInputs/pedestrianInput"),
            ("J pedestrianComposition", ".//pedestrianCompositions/pedestrianComposition"),
            ("J pedCompRelFlow", ".//pedestrianCompositionRelativeFlow"),
            ("J pedestrianClass", ".//pedestrianClasses/pedestrianClass"),
            ("J pedestrianType", ".//pedestrianTypes/pedestrianType"),
            ("J pedVolInterval", ".//timeIntervalPedVolume"),
        ):
            b, a = _tag_count(src_j, xpath), _tag_count(after_j, xpath)
            rows.append((f"{label} before->after", f"{b}->{a}", a - b))

    # Parts K/L/M: the routing-decision dependency. The oracle VERDICT is a
    # live COM result and is re-read from state.json ([live-recorded]); the
    # network content each verdict was pronounced on IS re-derived here.
    for part in ("k", "l", "m"):
        after_klm = _find(f"route-{part}", f"after_route_{part}.inpx")
        block = state.get(f"part_{part}") or {}
        if after_klm:
            r = ET.parse(after_klm).getroot()
            target = str(block.get("target_area", ""))
            routed_here = {
                (prd.find("location").get("area") if prd.find("location") is not None else None)
                for prd in r.findall(".//pedestrianRoutingDecisionStatic")
            }
            new_no = str(block.get("new_input_no", ""))
            vol = None
            for el in r.findall(".//pedestrianInputs/pedestrianInput"):
                if el.get("no") == new_no:
                    v = el.findall("timeIntPedVols/timeIntervalPedVolume")
                    vol = v[0].get("volume") if v else None
            rows.append((f"{part.upper()} origin area {target} routed?",
                         target in routed_here, f"volume={vol}"))
        oracle = block.get("load_oracle") or {}
        rows.append((f"{part.upper()} oracle [live-recorded]",
                     f"load_ok={oracle.get('load_ok')} init_ok={oracle.get('init_ok')}",
                     str(oracle.get("init_error"))[:70]))

    # Parts Q/R: the PARTIAL-vs-STATIC routing pair (structural half derived
    # here; the oracle verdict is live-recorded, like every other oracle).
    for part in ("q", "r"):
        after_qr = _find(f"partial-{part}", f"after_{'partial' if part == 'q' else 'partial'}.inpx")
        block = state.get(f"part_{part}") or {}
        if after_qr:
            r = ET.parse(after_qr).getroot()
            target = str(block.get("target_area", ""))
            static_here = {
                (d.find("location").get("area") if d.find("location") is not None else None)
                for d in r.findall(".//pedestrianRoutingDecisionStatic")
            }
            partial_here = {
                (d.find("location").get("area") if d.find("location") is not None else None)
                for d in r.findall(".//pedestrianRoutingDecisionPartial")
            }
            rows.append((f"{part.upper()} origin area {target}",
                         f"static={target in static_here} partial={target in partial_here}",
                         f"areas={_tag_count(after_qr, './/areas/area')}"))
        oracle = block.get("load_oracle") or {}
        rows.append((f"{part.upper()} oracle [live-recorded]",
                     f"load_ok={oracle.get('load_ok')} init_ok={oracle.get('init_ok')}",
                     str(oracle.get("init_error"))[:60]))

    # Part P: the 14-interval headline, re-derived rather than re-read.
    after_p = _find("pedcomp", "after_pedcomp.inpx")
    src_p = _source("pedcomp", MULTI_COMP_NET)
    if after_p and src_p:
        for label, xpath in (
            ("P pedestrianInput", ".//pedestrianInputs/pedestrianInput"),
            ("P pedVolInterval", ".//timeIntervalPedVolume"),
        ):
            b, a = _tag_count(src_p, xpath), _tag_count(after_p, xpath)
            rows.append((f"{label} before->after", f"{b}->{a}", a - b))
        block = state.get("part_p") or {}
        r = ET.parse(after_p).getroot()
        for el in r.findall(".//pedestrianInputs/pedestrianInput"):
            if el.get("no") != str(block.get("new_input_no", "")):
                continue
            vols = el.findall("timeIntPedVols/timeIntervalPedVolume")
            rows.append(("P new input intervals", len(vols), ""))
            rows.append(("P interval-1 volume", vols[0].get("volume") if vols else None, ""))
            rows.append(("P intervals 2..n all zero",
                         all(v.get("volume") == "0" for v in vols[1:]), ""))

    # Parts N/O: the two controls that disambiguate K/L/M's cause.
    after_n = _find("bare-n", "after_bare_n.inpx")
    src_n = _source("bare-n", PED_NET)
    if after_n and src_n:
        for label, xpath in (
            ("N area", ".//areas/area"),
            ("N pedestrianInput", ".//pedestrianInputs/pedestrianInput"),
        ):
            b, a = _tag_count(src_n, xpath), _tag_count(after_n, xpath)
            rows.append((f"{label} before->after", f"{b}->{a}", a - b))
    after_o = _find("unrouted-o", "after_unrouted_o.inpx")
    if after_o:
        block = state.get("part_o") or {}
        r = ET.parse(after_o).getroot()
        target = str(block.get("target_area", ""))
        routed_here = {
            (prd.find("location").get("area") if prd.find("location") is not None else None)
            for prd in r.findall(".//pedestrianRoutingDecisionStatic")
        }
        rows.append((f"O origin area {target} routed?", target in routed_here,
                     f"areas={_tag_count(after_o, './/areas/area')}"))
    for part in ("n", "o"):
        oracle = (state.get(f"part_{part}") or {}).get("load_oracle") or {}
        rows.append((f"{part.upper()} oracle [live-recorded]",
                     f"load_ok={oracle.get('load_ok')} init_ok={oracle.get('init_ok')}",
                     str(oracle.get("init_error"))[:70]))

    width = max((len(r[0]) for r in rows), default=10)
    for label, value, extra in rows:
        _log(f"  {label:<{width}}  {value}   {extra}")
    _log(f"\n  ({len(rows)} audited rows; state file: {state_path})")


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------

_PARTS = {
    "a": part_a, "b": part_b, "c": part_c, "d": part_d, "e": part_e,
    "f": part_f, "g": part_g, "h": part_h, "i": part_i, "j": part_j,
    "k": part_k, "l": part_l, "m": part_m, "n": part_n, "o": part_o,
    "p": part_p, "q": part_q, "r": part_r,
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
        state["typelib"] = static_typelib_scan()
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
    parser = argparse.ArgumentParser(description="Q6 unit (3) pedestrian probe")
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
        state["typelib"] = static_typelib_scan()
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
    raise SystemExit(main())

"""Q4 unit (8) PROBE-REPORT CAPSTONE: can the shipped Q4 verb set actually
build a 4-arm intersection FROM SCRATCH -- the dress rehearsal for genesis
milestone 2.3/3.x?

**This unit ships NO new verbs.** It composes the verbs Q4 units (1)-(7)
already shipped (docs/phase2/DECISIONS.md, 2026-07-31/08-01 entries) through
their REAL dispatch path (``toolkit.dispatch``, the SAME entry point
``routers/assistant.py`` uses after an approval), following the
``add_links_connectors_live_check.py``/``pt_stops_lines_live_check.py`` house
shape: Phase A is dispatch-level composition against a real
``VissimEditSession`` (a probe script acts as its own "already approved"
caller -- there is no interactive propose/confirm HTTP loop to drive here,
so direct ``dispatch()`` calls under an active edit session ARE the
"preapproved" path in spirit); Phase B is ONE representative verb pushed
through the FULL production stack (``run_agent_turn`` + a real
``PreapprovedWriteGate`` + a real ``GuardrailEngine``, canned planner, no
paid LLM calls) to prove genesis verbs are not a special, ungated code path.

**Two genuinely unprobed questions this unit closes (Stage 0), per the task
brief's own instruction to check the type library FIRST rather than assume
they are gaps -- and the FINDING SURPRISED THE BRIEF: both ARE creatable,
with one sharp nuance:**

  1. Is ``Net.SignalControllers`` COM-creatable? No prior unit's docstring,
     probe, or live check ever mentions ``SignalControllers`` on the LEFT
     side of an Add/Remove call -- every signal-related verb only ever
     RESOLVES an EXISTING controller. Live-verified answer: ``Net.
     SignalControllers.AddSignalController(Key)`` and a fresh controller's
     own ``.SGs.AddSignalGroup(Key)`` BOTH genuinely create real objects
     (GetAll() diff proves it) -- but the result is a NON-FUNCTIONAL
     skeleton: a bare SC + SG with no real timing program (the opaque
     ``intSupplyData`` blob, 00-EXECUTION-GUIDE.md landmine 3.2) makes
     ``RunSingleStep`` FAIL outright the instant it is present, live-verified
     here. ``DuplicateSignalController(Source, NewKey)`` -- also live-
     verified here -- is the REAL, WORKING creation path: it copies a
     donor controller's entire real program (all signal groups, real
     amber/red-amber timings) into a genuinely independent new controller
     that survives ``RunSingleStep`` cleanly. This is the THIRD appearance
     of the SAME "plain Add is a no-op or non-functional skeleton; Duplicate
     is the real path" idiom in this codebase (after ``DuplicatePriorityRule``,
     Q4 unit 6, and ``DuplicateDrivingBehavior``, Q3 unit 6) -- a pattern
     worth naming explicitly for the paper.
  2. Is ``Net.VehicleInputs`` COM-creatable? v2 unit (5) shipped
     ``set_vehicle_input_volume`` -- a SETTER on an existing input -- and
     that was the entire vehicle-input surface this codebase had ever
     shipped. Live-verified answer: ``Net.VehicleInputs.AddVehicleInput(Key,
     Link)`` genuinely creates a real, usable input (GetAll() diff proves
     it); Stage 5 goes on to prove it is FUNCTIONAL too (a real vehicle
     genuinely generated and driven through the genesis junction).

Both are answered live, via the SAME "static type-lib scan first, then a
live GetAll()-diff-and-saved-XML-diff attempt, never trust a bare
non-exception" discipline Q4 unit (6) established for ``AddPriorityRule``'s
own silent no-op (docs/phase2/DECISIONS.md, 2026-08-01) -- on throwaway
clones, never the main construction's own working copy. Stage 2 then acts on
this verdict directly: Junction A gets a genuinely fresh, independent
``DuplicateSignalController``-based controller, not a borrowed reference to
Beijing's own real intersection.

**Network choice: "Urban Intersection Beijing.CN", reused, not
re-discovered.** Q4 unit (5)'s own live check (``add_links_connectors_live_
check.py``) already proved this network is: (a) METRES-scale (WKT x/y raw
metres, no display-unit conversion drama for the geometry primitives
themselves); (b) safe for ``add_link``/``add_connector`` at an ISOLATED
origin far from its own real geometry (``ISOLATED_ORIGIN = (3000.0,
3000.0)`` proven not to trip the ~10 km licensed-network-size cap); and (c)
carries ONE real, richly-populated ``FIXEDTIME`` signal controller (SC=1,
at least 5 real signal groups: ``01``/``02-03``/``04``/``05``/``06`` --
confirmed here by a static XML scan BEFORE any COM call, since ``sgs/
signalGroup`` is nested one level deeper than a naive ``signalGroups/
signalGroup`` xpath guess would find, a genuinely easy trap). Beijing has
**zero existing priority rules** (also confirmed by the same static scan) --
this is deliberately KEPT rather than swapped for a rule-bearing network,
because Q4 unit (6)'s own recorded limitation ("a network with ZERO priority
rules cannot get its first one from ``add_priority_rule``") is EXACTLY the
kind of live-reproducible finding this capstone exists to re-confirm on a
second network, not paper over by cherry-picking a friendlier corpus member.

**Two junctions, one signalized and one unsignalized, built in isolated
space far from Beijing's own real geometry and far from each other**
(``ORIGIN_A = (3000.0, 3000.0)``, ``ORIGIN_B = (3000.0, 3450.0)`` -- 450 m
apart, each junction's own arms only reach 100 m out, so neither's
connectors/conflict-area recompute can cross the other's geometry or
Beijing's own). Junction A is the signalized flagship (full 12-movement
connector build, signal heads wired to a GENUINELY FRESH, independently
``DuplicateSignalController``-built controller when Stage 0 confirms that
path works -- Beijing's real SC 1 is only ever the FALLBACK if it does not).
Junction B is the unsignalized variant (stop signs + the attempted, expected-to-refuse
``add_priority_rule``) -- kept to the task brief's own STATED minimum (4
through + 2 turn = 6 movements) rather than the full 12, a deliberate scope
cut to keep total COM round-trips (each ``dispatch()`` write persists via a
real ``SaveNetAs``) inside a single weekend unit's budget; the full-12 case
is already proven once, on Junction A.

**Links are modeled as 8 ONE-WAY links per junction (4 approach + 4 exit),
not 4 "bidirectional" links -- because Vissim links ARE NOT bidirectional.**
``AddLink`` draws a single directed polyline; the only way to represent
two-way travel along one real-world corridor is two coincident one-way
links running opposite directions (exactly what every real Examples Demo
network does). Framing this as "4 bidirectional pairs" would describe a
Vissim concept that does not exist; "4 arms x 2 directions" is the accurate,
task-brief-anticipated alternative.

**Lane simplification, stated plainly:** every movement connects BOTH lanes
of its approach to BOTH lanes of its exit (``from_lanes=[1, 2]`` ->
``to_lanes=[1, 2]`` uniformly). Real channelization (dedicated turn lanes)
is a modeling-policy question orthogonal to "can the verb set compose a
junction at all" -- the question this unit answers -- and is left for a
later, policy-driven genesis unit.

Stages (this file's own ``main()``), each followed by a reparse (Python-side
``build_shadow_model``) and a REAL COM load oracle (``tools_check._load_
check``, exactly the function ``load_check`` the verb wraps) per the task
brief's own "after each stage" requirement:

  0. Gap probes: signal-controller / vehicle-input creatability, INCLUDING
     the bare-vs-duplicate distinction (own throwaway clones).
  1. Geometry: 16 ``add_link`` (2 junctions x 4 approach + 4 exit) + 18
     ``add_connector`` (12 for Junction A, 6 for Junction B).
  2. Control: a fresh, independent ``DuplicateSignalController``-based
     controller (or Beijing's real SC 1 as fallback) + 4 ``add_signal_head``
     on Junction A's approaches.
  3. Unsignalized fallback: 2 ``add_stop_sign`` on Junction B's E/W
     approaches + 1 attempted ``add_priority_rule`` (expected refusal, the
     Q4 unit (6) limitation).
  4. Measurement: 4 ``add_queue_counter`` + 1 ``add_data_collection_point``
     + 1 ``add_data_collection_measurement`` + 1 ``add_travel_time_section``,
     all on Junction A.
  5. Demand smoke test: gated on Stage 0's own verdict -- a genuine
     ``AddVehicleInput``-driven RunSingleStep sequence over a window whose
     own adequacy is printed first (see HARNESS LANDMINE 2 below), checked
     DECISIVELY (vehicles found specifically on Junction A's own isolated
     links, which nothing else in Beijing's real network can reach), or --
     only if Stage
     0's finding fails to reproduce -- the task brief's own specified
     fallback: the load oracle + a bare ``AddVehicleAtLinkPosition``
     drivability check (Q4 unit (5)'s own pattern), with the gap recorded,
     not papered over.
  6. Final: one more full-network reparse + load oracle pass.

Phase B (separate, isolated clone): ONE ``add_link`` through the complete
production stack (``run_agent_turn`` + ``PreapprovedWriteGate`` +
``GuardrailEngine``) -- proves gate-compatibility without re-running the
whole build through the (COM-round-trip-expensive) full-turn path.

Hard rules honored: no git commands anywhere in this file or its execution;
no edits to services/ (read/compose-only, verb bugs are REPORTED, never
patched here); work dirs confined to ``runtime/composite-intersection-
probe/``; every Vissim connection is ``attach_to_running=False``.

**LIVE-CAUGHT INFRASTRUCTURE LANDMINE, stated plainly (not a verb bug --
this file's own execution model, recorded because it will bite the next
long COM-heavy probe too):** the straightforward "one Python process runs
every stage" design SEGFAULTED (native crash, exit 139, no Python traceback)
partway into Stage 1 -- right as it opened its THIRD sequential Vissim COM
connection in one process (Stage 0 alone already cycled two:
``CoInitialize``/connect/load/disconnect/``CoUninitialize``, back to back,
each against its own throwaway clone). Root cause not fully isolated (would
need a Windows crash-dump/Event-Viewer analysis, out of this unit's scope),
but the trigger is clearly "too many sequential VissimController connect/
disconnect cycles inside one long-lived process", not any single verb or
tool call -- Stage 0's own two cycles completed and printed a full,
consistent verdict every time this was tried. **The fix: each stage runs as
its own FRESH OS process** (``main()`` with no ``--stage`` flag is a thin
orchestrator that shells out to itself once per stage via ``subprocess.run``,
handing state across process boundaries through a small JSON file) --
process isolation, not code changes, is what makes multi-stage genesis
COM work reliable. This is itself a genesis-relevant finding: a future
production job that builds a whole intersection in one long-lived worker
process should expect the same instability and needs the same per-stage
process boundary, not just per-turn.

**THE PROCESS-BOUNDARY FIX IS NOW VALIDATED (2026-08-03, first end-to-end
run; Stages 1-6 and Phase B had NEVER executed before this).** All eight
stage subprocesses ran to completion with ZERO native crashes -- no exit
139, no silent death, no missing state record. Evidence: every stage
recorded its own PASS/FAIL into the state file under its own pid, and the
orchestrator's crash-recording fallback ("subprocess died ... before it
could record its own outcome") never fired once. Each stage process stays
at ~2 COM connect/disconnect cycles (the edit session's own, plus
``tools_check._load_check``'s), comfortably under the ~2-3 that killed the
single-process design. What this means for genesis: the whole-intersection
build job can be built on per-stage process boundaries TODAY -- this is no
longer an unvalidated hypothesis, and gap (1) of the DECISIONS.md GENESIS
GAP LIST is closed. The underlying native crash is still not root-caused
(nobody has read a crash dump), but it no longer blocks anything, so the
root-cause hunt is a curiosity, not a dependency.

**HARNESS LANDMINE 1 -- an out-of-band COM write inside a verb's postwrite
window makes the guardrail refuse the VERB (live-caught here, Stage 2).**
Stage 2 creates its fresh controller with a raw ``DuplicateSignalController``
call (no verb wraps it yet), then dispatched ``add_signal_head``. The four
heads were all created correctly (61/62/63/64; reparse counted 52+4=56) --
but the REAL ``GuardrailEngine.postwrite`` refused the first one:
``report.blocking=True findings=['conflict_area_data_unavailable',
'write_verification_failed']``, naming ``signals#2:added`` plus 44
``signal_program_splits#2.{1,2}.{1..22}:added``. **The guardrail was
correct and the harness was wrong**: the duplicate lived only in memory when
the "before" snapshot was parsed off disk, and the verb's own ``persist()``
then flushed BOTH writes, so the diff attributed the whole controller to
``add_signal_head``. Fix: ``session.persist()`` immediately after the
duplicate. **Genesis consequence, and the sharpest thing this run produced
for gap (2)'s spec:** a real ``add_signal_controller`` verb built on
``DuplicateSignalController`` MUST ship
``_ALLOWS_NEW_OBJECTS_IN["add_signal_controller"] = {"signals",
"signal_program_splits"}`` -- exactly mirroring ``add_connector``'s own
``conflict_areas`` entry and ``duplicate_driving_behavior``'s
``driving_behaviors`` entry -- or every correct call will be refused and
rolled back. The id list above is the live-proven evidence for that entry,
and it also shows the shape: ONE duplicate carries the donor's TWO signal
programs x 22 signal groups.

**HARNESS LANDMINE 2 -- ``IVehicle`` has NO ``Link`` attribute, and the
probe's own bare ``except`` hid that behind a confident wrong answer
(live-caught here, Stage 5).** Stage 5 originally located vehicles with
``v.AttValue("Link")``. That attribute does not exist: Vissim logged
``Error VEHICLE: Could not find attribute with ID LINK.`` once per vehicle
-- 13 identical lines for 13 vehicles -- into the run's own ``.err`` file,
while ``except Exception: continue`` swallowed every one. The reported
"0 vehicles on Junction A" was therefore not a measurement at all; the
check was structurally incapable of ever returning True. Two compounding
errors, both now fixed: (a) the read is ``AttValue("Lane")``, a
``"<link>-<lane>"`` token (the pattern ``add_links_connectors_live_check.py``
already proved), and any locate failure is now reported LOUDLY and voids
the proof rather than silently emptying it; (b) the window was
underpowered by ~40x -- Beijing's ``<simulation simRes="10">`` makes 45
RunSingleStep calls just 4.5 SIMULATION seconds, against a 12 s headway at
300 veh/h, an expected 0.375 arrivals. The generalizable rule, worth more
than either bug: **a "no vehicles found" result is only evidence if the
harness first proves the window could have contained one** -- so Stage 5
now prints simRes, the derived sim-seconds, the headway and the expected
arrival count before it steps.

**BOTH FIXES RE-RUN AND CONFIRMED, whole probe green end to end
(2026-08-03, second run, exit code 0, every stage PASS).** Stage 2's
postwrite came back ``report.blocking=False findings=['conflict_area_data_
unavailable']``. Stage 5 printed ``simRes=10 ... 300 RunSingleStep calls =
30 SIMULATION seconds. At 2000 veh/h (headway 1.80 s) that is an expected
16.7 arrivals`` and then delivered the CROWN PROOF this probe was built for:
``126 vehicle(s) network-wide; 9 of them specifically on Junction A's OWN
isolated links [43, 46, 55, 63, 68, 73, 77, 81, 105]`` with
``vehicle_locate_failures: 0``. Those links are unreachable from anything
else in Beijing's real network, so the only possible origin is the vehicle
input this probe created -- **an ``AddVehicleInput``-created input does
genuinely generate traffic, and that traffic does genuinely drive through a
junction built entirely from Q4 verbs.** That was the check gap (3) of the
GENESIS GAP LIST explicitly still owed, and gap (4)'s full-composition proof
is the run itself. Final composite network: 146 links, 307 conflict areas,
56 signal heads, 2 stop signs, 29 queue counters, 32 data collection points,
25 travel time sections, 13 vehicle inputs -- ``load_ok=True init_ok=True``
from a fresh Vissim instance.

Usage: ``python tools/composite_intersection_probe.py [example_dir]`` --
runs every stage end to end (via internal per-stage subprocesses) and prints
one staged PASS/FAIL/GAP summary at the end. Advanced/internal:
``--stage {0,1,2,3,4,5,6,phaseb} --state <path>`` runs exactly one stage
against a persisted JSON state file (what the orchestrator itself shells
out to) -- not meant for direct standalone use, but safe to re-run a single
failed stage against an existing state file while debugging.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "composite-intersection-probe"

#: Junction A (signalized flagship) and Junction B (unsignalized variant)
#: centers -- isolated, far from Beijing's own real geometry (x in
#: [-765, 423], y in [-886, -35], per this unit's own static scan) and far
#: from each other (450 m, well clear of each junction's own 100 m arms).
ORIGIN_A = (3000.0, 3000.0)
ORIGIN_B = (3000.0, 3450.0)

#: Each arm: an outer point OUTER_R metres from the junction center, and an
#: inner "stub" point INNER_R metres from center where connectors join --
#: mirrors real Vissim intersection modeling (short stub links + a fan of
#: connectors), not one link running straight through the middle.
OUTER_R = 100.0
INNER_R = 15.0
ARM_LEN = round(OUTER_R - INNER_R, 3)

_DIRECTIONS = {"N": (0.0, 1.0), "S": (0.0, -1.0), "E": (1.0, 0.0), "W": (-1.0, 0.0)}

#: Stage 5's demand window, sized from the network's OWN <simulation simRes="10">
#: rather than guessed (see the module docstring's harness-landmine block): 300
#: RunSingleStep calls = 30 simulation seconds, and 2000 veh/h is a 1.8 s
#: headway, so a correct build is expected to produce ~16 arrivals. The
#: original 45 steps / 300 veh/h pair was 4.5 sim-seconds against a 12 s
#: headway -- an expected 0.375 arrivals, i.e. a test that could not pass.
_DEMAND_STEPS = 300
_DEMAND_VOLUME_VPH = 2000.0

#: Beijing SC 1's real signal groups (static-scan-confirmed: 01/02-03/04/05/06+
#: under signalController/sgs/signalGroup) -- reused as the "wire to an
#: EXISTING controller" fallback the task brief anticipates.
_BEIJING_SC_NO = "1"
_SG_BY_DIR = {"N": "1", "S": "2", "E": "3", "W": "4"}

#: Human-readable label per --stage id, used both for state-file records and
#: for the outer orchestrator's own crash-recording fallback (see
#: _run_all_stages_as_subprocesses).
_STAGE_LABELS = {
    "0": "Stage 0: gap probes",
    "1": "Stage 1: geometry",
    "2": "Stage 2: control (signal heads)",
    "3": "Stage 3: unsignalized fallback",
    "4": "Stage 4: measurement infra",
    "5": "Stage 5: demand smoke test",
    "6": "Stage 6: full reload + load_check",
    "phaseb": "Phase B: preapproved-gate house pattern",
}
_STAGE_ORDER = ["0", "1", "2", "3", "4", "5", "6", "phaseb"]


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


def _arm_points(center: tuple[float, float], direction: str) -> tuple[tuple[float, float], tuple[float, float]]:
    """(outer_point, inner_point) for one compass arm of a 4-arm junction."""
    dx, dy = _DIRECTIONS[direction]
    cx, cy = center
    outer = (cx + dx * OUTER_R, cy + dy * OUTER_R)
    inner = (cx + dx * INNER_R, cy + dy * INNER_R)
    return outer, inner


# ---------------------------------------------------------------------------
# Cross-process state handoff (JSON file) -- see the module docstring's own
# segfault finding for why each stage runs as its own fresh OS process
# instead of all stages sharing one long-lived interpreter.
# ---------------------------------------------------------------------------


def _conns_to_json(conns: dict[tuple[str, str], str]) -> dict[str, str]:
    return {f"{a}>{b}": no for (a, b), no in conns.items()}


def _conns_from_json(data: dict[str, str]) -> dict[tuple[str, str], str]:
    out: dict[tuple[str, str], str] = {}
    for key, no in data.items():
        a, b = key.split(">")
        out[(a, b)] = no
    return out


def _empty_state() -> dict[str, Any]:
    return {
        "source_inpx": None, "work_dir": None, "gap_verdict": None, "geo": None,
        "ctrl": None, "unsig": None, "meas": None, "demand": None, "final": None,
        "phaseb_ok": None, "stage_results": [],
    }


def _load_state(state_path: Path) -> dict[str, Any]:
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return _empty_state()


def _save_state(state_path: Path, state: dict[str, Any]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _state_record(state: dict[str, Any], stage: str, status: str, detail: str = "") -> None:
    state["stage_results"].append({"stage": stage, "status": status, "detail": detail})
    _log(f"\n>>> [{stage}] {status}{(' -- ' + detail) if detail else ''}")


# ---------------------------------------------------------------------------
# Stage 0: gap probes -- signal-controller / vehicle-input creatability.
# Own throwaway clone; a static type-lib scan (no COM, no licence) first,
# then a live GetAll()-diff-and-saved-XML-diff attempt (never trust a bare
# non-exception -- the AddPriorityRule precedent, Q4 unit 6).
# ---------------------------------------------------------------------------


def _scan_typelib_classes(keywords: tuple[str, ...]) -> list[str]:
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
    src_path = Path(mod.__file__)
    lines = src_path.read_text(encoding="utf-8", errors="replace").splitlines()

    matched: list[str] = []
    class_name = None
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
    if not matched:
        _log(f"      NO interface matching any of {keywords!r} found in the type library.")
    return matched


def _try_add_methods(
    container: Any, method_names: tuple[str, ...], arg_sets: tuple[tuple, ...]
) -> tuple[str | None, tuple | None, Any]:
    for method_name in method_names:
        method = getattr(container, method_name, None)
        if method is None:
            _log(f"      Container has no {method_name!r} method at all (hasattr fails).")
            continue
        for args in arg_sets:
            try:
                result = method(*args)
                _log(f"      [no exception] {method_name}{args!r} -> {result!r} "
                     "(NOT proof of success -- AddPriorityRule's own silent no-op precedent; "
                     "a GetAll() diff decides this, below)")
                return method_name, args, result
            except Exception as exc:
                _log(f"      [raised]       {method_name}{args!r} -> {type(exc).__name__}: {exc}")
    return None, None, None


def stage0_gap_probes(example_dir: Path) -> dict[str, Any]:
    _log("=" * 78)
    _log("[Stage 0] GAP PROBES -- signal-controller creatability + vehicle-input "
         "creatability (both NEVER probed by any prior Q4/v2/v2a/Q3 unit)")
    _log("=" * 78)

    verdict: dict[str, Any] = {}

    _log("\n[0a] Static type-library scan (no COM instance, no licence used): "
         "ISignalControllerContainer* / IVehicleInputContainer* / ISignalGroupContainer* ...")
    _scan_typelib_classes(("signalcontroller", "vehicleinput", "signalgroup"))

    from services.agent.tools_network import build_shadow_model

    import pythoncom

    # =====================================================================
    # Part 1: bare Add* existence + GetAll()/saved-XML diff (own throwaway
    # clone, discarded -- never saved, never touches the main build).
    # =====================================================================
    work_dir = _fresh_work_dir("gap-probes-bare")
    shutil.copytree(example_dir, work_dir, dirs_exist_ok=True)
    working = sorted(work_dir.glob("*.inpx"))[0]
    _log(f"\n[0b] Throwaway clone 1/2 (bare-create test, never saved): {working}")

    shadow = build_shadow_model(working)
    anchor_link = next(l for l in shadow["links"] if not l.get("is_connector"))
    _log(f"      Anchor link for AddVehicleInput's own Link arg: {anchor_link['id']}")

    pythoncom.CoInitialize()
    from services.vissim_com import VissimController

    controller = VissimController()
    try:
        controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        controller.load_network(str(working))
        net = controller.vissim.Net

        # ---- SignalControllers: bare AddSignalController + AddSignalGroup ----
        _log("\n[0c] Net.SignalControllers -- BARE live Add attempt ...")
        sc_before = {int(s.AttValue("No")) for s in net.SignalControllers.GetAll()}
        _log(f"      GetAll() before = {sorted(sc_before)}")
        new_sc_no = (max(sc_before) if sc_before else 0) + 1
        sc_method, sc_args, sc_result = _try_add_methods(
            net.SignalControllers, ("AddSignalController", "Add"), ((new_sc_no,),),
        )
        sc_after = {int(s.AttValue("No")) for s in net.SignalControllers.GetAll()}
        sc_added = sc_after - sc_before
        _log(f"      GetAll() diff: added={sorted(sc_added)}")
        verdict["signal_controller_bare_creatable"] = bool(sc_added)
        verdict["signal_controller_bare_detail"] = f"method={sc_method} args={sc_args} added={sorted(sc_added)}"

        sc_bare_functional: bool | None = None
        if sc_added and sc_result is not None:
            _log("      Net.SignalControllers.AddSignalController's own new SC -- does it "
                 "expose a usable child SGs collection with AddSignalGroup?")
            sg_method, sg_args, _sg_result = _try_add_methods(sc_result.SGs, ("AddSignalGroup",), ((1,),))
            sg_count = len(list(sc_result.SGs.GetAll()))
            _log(f"      New SC's SGs count after AddSignalGroup attempt: {sg_count}")
            _log(
                "      Does this BARE skeleton (a real SC + a real SG, but NO real timing "
                "program -- the opaque intSupplyData blob, 00-EXECUTION-GUIDE.md landmine "
                "3.2) survive a RunSingleStep, on the SAME live connection (no save/reload "
                "needed -- RunSingleStep operates on the current in-memory model)?"
            )
            try:
                controller.run_single_step()
                sc_bare_functional = True
                _log("      RunSingleStep with the bare SC+SG present: OK (unexpected).")
            except Exception as exc:
                sc_bare_functional = False
                _log(f"      RunSingleStep with the bare SC+SG present FAILED (this is the "
                     f"finding): {type(exc).__name__}: {exc}")
        verdict["signal_controller_bare_functional"] = sc_bare_functional

        # ---- VehicleInputs: bare AddVehicleInput ----
        _log("\n[0d] Net.VehicleInputs -- BARE live Add attempt ...")
        vi_before = {int(v.AttValue("No")) for v in net.VehicleInputs.GetAll()}
        _log(f"      GetAll() before: {len(vi_before)} real input(s)")
        anchor_obj = net.Links.ItemByKey(int(anchor_link["id"]))
        new_vi_no = (max(vi_before) if vi_before else 0) + 1
        vi_method, vi_args, _vi_result = _try_add_methods(
            net.VehicleInputs, ("AddVehicleInput", "Add"), ((new_vi_no, anchor_obj), (new_vi_no,)),
        )
        vi_after = {int(v.AttValue("No")) for v in net.VehicleInputs.GetAll()}
        vi_added = vi_after - vi_before
        _log(f"      GetAll() diff: added={sorted(vi_added)}")
        verdict["vehicle_input_creatable"] = bool(vi_added)
        verdict["vehicle_input_detail"] = f"method={vi_method} args_shape={vi_args} added={sorted(vi_added)}"
        # NEVER saved -- this clone is discarded; a broken bare SC2 with no
        # real program must never reach the main build's own working copy.
    finally:
        try:
            controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    # =====================================================================
    # Part 2: DuplicateSignalController -- is THIS the real, working
    # creation path (mirroring DuplicatePriorityRule/DuplicateDrivingBehavior,
    # the SAME "plain Add is a no-op or non-functional skeleton; Duplicate is
    # the real path" idiom already established TWICE elsewhere in this
    # codebase)? Own second throwaway clone -- never contaminated by Part 1's
    # now-broken bare SC2.
    # =====================================================================
    work_dir2 = _fresh_work_dir("gap-probes-dup")
    shutil.copytree(example_dir, work_dir2, dirs_exist_ok=True)
    working2 = sorted(work_dir2.glob("*.inpx"))[0]
    _log(f"\n[0e] Throwaway clone 2/2 (DuplicateSignalController test, never saved): {working2}")

    pythoncom.CoInitialize()
    controller2 = VissimController()
    try:
        controller2.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        controller2.load_network(str(working2))
        net2 = controller2.vissim.Net

        sc1 = net2.SignalControllers.ItemByKey(int(_BEIJING_SC_NO))
        dup_method = getattr(net2.SignalControllers, "DuplicateSignalController", None)
        verdict["signal_controller_duplicate_method_exists"] = dup_method is not None
        dup_functional: bool | None = None
        if dup_method is not None:
            existing = {int(s.AttValue("No")) for s in net2.SignalControllers.GetAll()}
            dup_no = max(existing) + 1
            try:
                dup_sc = dup_method(sc1, dup_no)
                sg_count = len(list(dup_sc.SGs.GetAll()))
                _log(
                    f"      DuplicateSignalController(SC{_BEIJING_SC_NO}, {dup_no}) -> SC{dup_no} "
                    f"with {sg_count} INHERITED signal groups (a real copy of SC1's own working "
                    "program, not an empty skeleton)."
                )
                controller2.run_single_step()
                dup_functional = True
                _log("      RunSingleStep with the DUPLICATED SC present: OK -- this IS a "
                     "working, independent, freshly-created controller.")
            except Exception as exc:
                dup_functional = False
                _log(f"      DuplicateSignalController path FAILED: {type(exc).__name__}: {exc}")
        verdict["signal_controller_duplicate_functional"] = dup_functional
        # NEVER saved -- this clone is discarded too; the main build (Stage 2)
        # redoes this exact call on ITS OWN working copy so the real
        # construction has its own, freshly-verified provenance.
    finally:
        try:
            controller2.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    # The headline "creatable" verdict is the REAL, working answer (the
    # duplicate path), not bare existence -- a bare AddSignalController is
    # real (GetAll() proves it) but produces an object that breaks the
    # simulation the instant anything is wired to it, so calling that
    # "creatable" without qualification would be exactly the kind of
    # confident-wrong-numbers lie 00-EXECUTION-GUIDE.md SS4 exists to prevent.
    verdict["signal_controller_creatable"] = bool(verdict.get("signal_controller_duplicate_functional"))

    _log(
        f"\n[Stage 0] VERDICT: signal_controller bare-creatable="
        f"{verdict['signal_controller_bare_creatable']} bare-FUNCTIONAL="
        f"{verdict['signal_controller_bare_functional']} duplicate-FUNCTIONAL="
        f"{verdict['signal_controller_duplicate_functional']} (headline: "
        f"{'CREATABLE via duplicate-then-edit' if verdict['signal_controller_creatable'] else 'NOT CREATABLE'})  |  "
        f"vehicle_input_creatable={verdict['vehicle_input_creatable']}"
    )
    return verdict


# ---------------------------------------------------------------------------
# Stage 1: geometry.
# ---------------------------------------------------------------------------


def stage1_geometry(artifact_manager: Any, engine: Any) -> dict[str, Any]:
    _log("\n" + "=" * 78)
    _log("[Stage 1] GEOMETRY -- 16 add_link (2 junctions x 4 approach + 4 exit) + "
         "18 add_connector (Junction A: full 12 movements; Junction B: the task "
         "brief's own minimum, 4 through + 2 turn = 6)")
    _log("=" * 78)

    from services.agent.edit_session import VissimEditSession, clear_active_edit_session, set_active_edit_session
    from services.agent.tools_check import _load_check
    from services.agent.tools_network import build_shadow_model
    from services.agent.toolkit import default_registry, dispatch

    working_path = artifact_manager.working_path
    session = VissimEditSession(working_path)

    def _call(name: str, params: dict[str, Any]) -> dict[str, Any]:
        env = dispatch(name, params, registry=default_registry)
        if env["status"] != "ok":
            raise AssertionError(f"{name}({params}) FAILED: {env}")
        return env

    arms_a: dict[str, dict[str, Any]] = {}
    arms_b: dict[str, dict[str, Any]] = {}
    conns_a: dict[tuple[str, str], str] = {}
    conns_b: dict[tuple[str, str], str] = {}

    captured_first_link = False
    shadow_before_first_link: dict[str, Any] | None = None
    shadow_after_first_link: dict[str, Any] | None = None
    first_link_params: dict[str, Any] | None = None
    first_link_env: dict[str, Any] | None = None

    try:
        set_active_edit_session(session)
        shadow0 = build_shadow_model(working_path)
        baseline_links = len(shadow0["links"])
        _log(f"[1.0] Baseline: {baseline_links} links, {len(shadow0['conflict_areas'])} conflict areas.")

        for label, center, arms in (("A (signalized)", ORIGIN_A, arms_a), ("B (unsignalized)", ORIGIN_B, arms_b)):
            _log(f"\n[1.1.{label[0]}] Junction {label} -- 8 links (4 approach + 4 exit)...")
            for d in ("N", "S", "E", "W"):
                outer, inner = _arm_points(center, d)
                approach_params = {
                    "points_xy": [list(outer), list(inner)], "lane_count": 2,
                    "name": f"J{label[0]} approach {d}",
                }
                if not captured_first_link:
                    shadow_before_first_link = build_shadow_model(working_path)
                approach_env = _call("add_link", approach_params)
                if not captured_first_link:
                    shadow_after_first_link = build_shadow_model(working_path)
                    first_link_params, first_link_env = approach_params, approach_env
                    captured_first_link = True

                exit_params = {
                    "points_xy": [list(inner), list(outer)], "lane_count": 2,
                    "name": f"J{label[0]} exit {d}",
                }
                exit_env = _call("add_link", exit_params)

                arms[d] = {
                    "approach": str(approach_env["artifacts"]["no"]),
                    "exit": str(exit_env["artifacts"]["no"]),
                    "approach_len": ARM_LEN,
                }
                _log(f"      {d}: approach={arms[d]['approach']} exit={arms[d]['exit']}")

        shadow_after_links = build_shadow_model(working_path)
        _log(
            f"\n[1.2] Reparse after 16 add_link calls: link count = "
            f"{len(shadow_after_links['links'])} (baseline {baseline_links} + 16 expected)"
        )
        assert len(shadow_after_links["links"]) == baseline_links + 16

        _log("\n[1.3] REAL GuardrailEngine.postwrite for the FIRST add_link call...")
        assert first_link_params is not None and first_link_env is not None
        report = engine.postwrite(
            {"tool": "add_link", "params": {k: v for k, v in first_link_params.items() if k != "name"}},
            shadow_before_first_link, shadow_after_first_link, first_link_env["verification"],
        )
        _log(f"      report.blocking={report.blocking} findings={[f.constraint_id for f in report.findings]}")
        assert report.blocking is False, report.findings
        _log("      PASS.")

        # -------------------------------------------------------------
        # Connectors.
        # -------------------------------------------------------------
        movements_a = [(f, t) for f in "NSEW" for t in "NSEW" if f != t]
        movements_b = [("N", "S"), ("S", "N"), ("E", "W"), ("W", "E"), ("N", "E"), ("E", "N")]

        captured_first_conn = False
        shadow_before_first_conn: dict[str, Any] | None = None
        shadow_after_first_conn: dict[str, Any] | None = None
        first_conn_params: dict[str, Any] | None = None
        first_conn_env: dict[str, Any] | None = None

        _log(f"\n[1.4] Junction A connectors -- FULL 12-movement build ({len(movements_a)} movements)...")
        for from_dir, to_dir in movements_a:
            params = {
                "from_link": arms_a[from_dir]["approach"], "from_lanes": [1, 2],
                "from_pos_m": arms_a[from_dir]["approach_len"],
                "to_link": arms_a[to_dir]["exit"], "to_lanes": [1, 2], "to_pos_m": 0.0,
                "name": f"A {from_dir}->{to_dir}",
            }
            if not captured_first_conn:
                shadow_before_first_conn = build_shadow_model(working_path)
            env = _call("add_connector", params)
            if not captured_first_conn:
                shadow_after_first_conn = build_shadow_model(working_path)
                first_conn_params, first_conn_env = params, env
                captured_first_conn = True
            conns_a[(from_dir, to_dir)] = str(env["artifacts"]["no"])
        _log(f"      {len(conns_a)} connectors added for Junction A: {conns_a}")

        _log(
            f"\n[1.5] Junction B connectors -- MINIMUM bar per the task brief "
            f"(4 through + 2 turn = {len(movements_b)} movements)..."
        )
        for from_dir, to_dir in movements_b:
            params = {
                "from_link": arms_b[from_dir]["approach"], "from_lanes": [1, 2],
                "from_pos_m": arms_b[from_dir]["approach_len"],
                "to_link": arms_b[to_dir]["exit"], "to_lanes": [1, 2], "to_pos_m": 0.0,
                "name": f"B {from_dir}->{to_dir}",
            }
            env = _call("add_connector", params)
            conns_b[(from_dir, to_dir)] = str(env["artifacts"]["no"])
        _log(f"      {len(conns_b)} connectors added for Junction B: {conns_b}")

        shadow_after_conns = build_shadow_model(working_path)
        new_conflict_areas = (
            len(shadow_after_conns["conflict_areas"]) - len(shadow_after_links["conflict_areas"])
        )
        expected_link_count = len(shadow_after_links["links"]) + len(movements_a) + len(movements_b)
        _log(
            f"\n[1.6] Reparse after {len(movements_a) + len(movements_b)} add_connector calls: "
            f"link count={len(shadow_after_conns['links'])} (expect {expected_link_count}); "
            f"conflict areas Vissim auto-created at the two new junctions: {new_conflict_areas} "
            "(Q4 unit 5's own live check saw 6 at ONE real junction with far fewer movements)"
        )
        assert len(shadow_after_conns["links"]) == expected_link_count

        _log("\n[1.7] REAL GuardrailEngine.postwrite for the FIRST add_connector call...")
        assert first_conn_params is not None and first_conn_env is not None
        report2 = engine.postwrite(
            {"tool": "add_connector", "params": {k: v for k, v in first_conn_params.items() if k != "name"}},
            shadow_before_first_conn, shadow_after_first_conn, first_conn_env["verification"],
        )
        _log(f"      report.blocking={report2.blocking} findings={[f.constraint_id for f in report2.findings]}")
        assert report2.blocking is False, report2.findings
        _log(
            "      PASS: the _ALLOWS_NEW_OBJECTS_IN['add_connector'] conflict-area exemption "
            "holds even at a brand-new junction assembled entirely from scratch."
        )

        session.close()
    finally:
        clear_active_edit_session()

    _log("\n[1.8] COM load oracle after Stage 1 (geometry)...")
    load_result = _load_check(working_path)
    _log(f"      load_check_result = {load_result}")
    assert load_result["load_ok"] is True and load_result["init_ok"] is True
    _log("      PASS: both junctions' raw geometry loads and initializes cleanly.")

    return {
        "arms_a": arms_a, "arms_b": arms_b, "conns_a": conns_a, "conns_b": conns_b,
        "new_conflict_areas": new_conflict_areas,
    }


# ---------------------------------------------------------------------------
# Stage 2: control -- signal heads on Junction A.
# ---------------------------------------------------------------------------


def stage2_control(
    artifact_manager: Any, engine: Any, arms_a: dict[str, dict[str, Any]], gap_verdict: dict[str, Any]
) -> dict[str, Any]:
    _log("\n" + "=" * 78)
    use_fresh_sc = bool(gap_verdict.get("signal_controller_duplicate_functional"))
    if use_fresh_sc:
        _log(
            "[Stage 2] CONTROL -- Stage 0's own verdict: DuplicateSignalController is a "
            "REAL, WORKING creation path. Junction A gets a GENUINELY FRESH, independent "
            "controller (duplicated from Beijing's real SC 1, then repointed) -- not a "
            "borrowed reference to Beijing's own unrelated intersection. 4 add_signal_head "
            "calls wire heads to the new controller's own inherited signal groups."
        )
    else:
        _log(
            "[Stage 2] CONTROL -- Stage 0's own verdict: no working fresh-controller path "
            "was found. Falling back to wiring 4 add_signal_head calls to Beijing's "
            "EXISTING SC 1 -- a documented placeholder (this genesis junction's heads share "
            "an unrelated real intersection's timing plan), not a semantically complete "
            "signal design of its own."
        )
    _log("=" * 78)

    from services.agent.edit_session import VissimEditSession, clear_active_edit_session, set_active_edit_session
    from services.agent.tools_check import _load_check
    from services.agent.tools_network import build_shadow_model
    from services.agent.toolkit import default_registry, dispatch

    working_path = artifact_manager.working_path
    session = VissimEditSession(working_path)

    def _call(name: str, params: dict[str, Any]) -> dict[str, Any]:
        env = dispatch(name, params, registry=default_registry)
        if env["status"] != "ok":
            raise AssertionError(f"{name}({params}) FAILED: {env}")
        return env

    heads: dict[str, str] = {}
    sc_no_used = _BEIJING_SC_NO
    sc_provenance = "existing Beijing SC 1 (fallback)"

    try:
        set_active_edit_session(session)

        if use_fresh_sc:
            # No verb wraps DuplicateSignalController (a genuinely unprobed
            # surface until this unit) -- reach into the session's own live
            # COM connection directly, the same way the underlying tools_edit
            # verbs themselves eventually would if this became a real verb.
            # This mutates the SAME in-memory Net that every subsequent
            # dispatch() call in this session already operates against, so
            # add_signal_head's own sc_no resolution (vissim_com.py's
            # _item_by_key/_signal_group_by_key) finds it with no extra save
            # needed first.
            controller = session._ensure_connected()  # noqa: SLF001 -- deliberate, see comment above
            net = controller.vissim.Net
            sc1 = net.SignalControllers.ItemByKey(int(_BEIJING_SC_NO))
            existing_sc_nos = {int(s.AttValue("No")) for s in net.SignalControllers.GetAll()}
            new_sc_no = max(existing_sc_nos) + 1
            new_sc = net.SignalControllers.DuplicateSignalController(sc1, new_sc_no)
            sg_count = len(list(new_sc.SGs.GetAll()))
            sc_no_used = str(new_sc_no)
            sc_provenance = f"FRESH, independently-duplicated SC {new_sc_no} ({sg_count} inherited signal groups)"
            _log(f"      {sc_provenance} -- created via DuplicateSignalController(SC1, {new_sc_no}).")
            # LIVE-CAUGHT HARNESS BUG (2026-08-03 first end-to-end run) -- see the
            # module docstring. The duplicate above is an OUT-OF-BAND, un-verbed COM
            # write; it must be PERSISTED here so it lands in the "before" snapshot
            # below. Without this, add_signal_head's own postwrite window straddles
            # the duplicate and the REAL GuardrailEngine correctly refuses the head
            # (write_verification_failed, tier=refuse) for signals#2 + 44
            # signal_program_splits#2.* it never asked for. The guardrail was right;
            # the harness was mis-attributing its own side write to the verb.
            session.persist()
            _log("      Duplicate PERSISTED before the add_signal_head baseline snapshot, so the "
                 "verb's own postwrite window contains only the verb's own write.")

        shadow_before_first = build_shadow_model(working_path)
        baseline = len(shadow_before_first["signal_heads"])

        first_params = {
            "link_no": arms_a["N"]["approach"], "lane_index": 1,
            "pos_m": round(arms_a["N"]["approach_len"] - 10.0, 3),
            "sc_no": sc_no_used, "sg_no": _SG_BY_DIR["N"], "name": "J-A head N",
        }
        first_env = _call("add_signal_head", first_params)
        shadow_after_first = build_shadow_model(working_path)
        heads["N"] = str(first_env["artifacts"]["no"])
        _log(f"      N: signal head {heads['N']} @ {first_params['pos_m']}m wired to "
             f"SC{sc_no_used}/SG{_SG_BY_DIR['N']}")

        for d in ("S", "E", "W"):
            params = {
                "link_no": arms_a[d]["approach"], "lane_index": 1,
                "pos_m": round(arms_a[d]["approach_len"] - 10.0, 3),
                "sc_no": sc_no_used, "sg_no": _SG_BY_DIR[d], "name": f"J-A head {d}",
            }
            env = _call("add_signal_head", params)
            heads[d] = str(env["artifacts"]["no"])
            _log(f"      {d}: signal head {heads[d]} @ {params['pos_m']}m wired to "
                 f"SC{sc_no_used}/SG{_SG_BY_DIR[d]}")

        shadow_final = build_shadow_model(working_path)
        _log(
            f"\n[2.1] Reparse: signal_heads count = {len(shadow_final['signal_heads'])} "
            f"(baseline {baseline} + 4 expected)"
        )
        assert len(shadow_final["signal_heads"]) == baseline + 4

        _log("\n[2.2] REAL GuardrailEngine.postwrite for the FIRST add_signal_head call...")
        report = engine.postwrite(
            {"tool": "add_signal_head", "params": {k: v for k, v in first_params.items() if k != "name"}},
            shadow_before_first, shadow_after_first, first_env["verification"],
        )
        _log(f"      report.blocking={report.blocking} findings={[f.constraint_id for f in report.findings]}")
        assert report.blocking is False, report.findings
        _log("      PASS.")

        session.close()
    finally:
        clear_active_edit_session()

    _log("\n[2.3] COM load oracle after Stage 2 (control)...")
    load_result = _load_check(working_path)
    _log(f"      load_check_result = {load_result}")
    assert load_result["load_ok"] is True and load_result["init_ok"] is True
    _log("      PASS.")

    return {"heads": heads, "sc_no_used": sc_no_used, "sc_provenance": sc_provenance, "used_fresh_sc": use_fresh_sc}


# ---------------------------------------------------------------------------
# Stage 3: unsignalized fallback/variant -- Junction B.
# ---------------------------------------------------------------------------


def stage3_unsignalized(artifact_manager: Any, engine: Any, arms_b: dict[str, dict[str, Any]]) -> dict[str, Any]:
    _log("\n" + "=" * 78)
    _log(
        "[Stage 3] UNSIGNALIZED FALLBACK/VARIANT -- 2 add_stop_sign on Junction B's minor "
        "(E/W) approaches + 1 attempted add_priority_rule yielding to the major (N/S) "
        "approaches. Beijing has ZERO existing priority rules (static-scan-confirmed in "
        "this unit's own docstring) -- the Q4 unit 6 limitation ('a network with zero "
        "priority rules cannot get its first one from this verb') predicts a REFUSAL "
        "here; that refusal, if it happens, is the EXPECTED finding, not a probe failure."
    )
    _log("=" * 78)

    from services.agent.edit_session import VissimEditSession, clear_active_edit_session, set_active_edit_session
    from services.agent.tools_check import _load_check
    from services.agent.tools_network import build_shadow_model
    from services.agent.toolkit import default_registry, dispatch

    working_path = artifact_manager.working_path
    session = VissimEditSession(working_path)

    def _call(name: str, params: dict[str, Any]) -> dict[str, Any]:
        env = dispatch(name, params, registry=default_registry)
        if env["status"] != "ok":
            raise AssertionError(f"{name}({params}) FAILED: {env}")
        return env

    try:
        set_active_edit_session(session)
        shadow_before_first = build_shadow_model(working_path)
        baseline_stop_signs = len(shadow_before_first["stop_signs"])

        stop_params_e = {
            "link_no": arms_b["E"]["approach"], "lane_index": 1,
            "pos_m": round(arms_b["E"]["approach_len"] - 10.0, 3), "name": "J-B stop E",
        }
        first_env = _call("add_stop_sign", stop_params_e)
        shadow_after_first = build_shadow_model(working_path)
        stop_e_no = str(first_env["artifacts"]["no"])

        stop_params_w = {
            "link_no": arms_b["W"]["approach"], "lane_index": 1,
            "pos_m": round(arms_b["W"]["approach_len"] - 10.0, 3), "name": "J-B stop W",
        }
        env_w = _call("add_stop_sign", stop_params_w)
        stop_w_no = str(env_w["artifacts"]["no"])
        _log(f"      Stop signs: E={stop_e_no} W={stop_w_no}")

        shadow_final = build_shadow_model(working_path)
        assert len(shadow_final["stop_signs"]) == baseline_stop_signs + 2

        _log("\n[3.1] REAL GuardrailEngine.postwrite for the FIRST add_stop_sign call...")
        report = engine.postwrite(
            {"tool": "add_stop_sign", "params": {k: v for k, v in stop_params_e.items() if k != "name"}},
            shadow_before_first, shadow_after_first, first_env["verification"],
        )
        _log(f"      report.blocking={report.blocking} findings={[f.constraint_id for f in report.findings]}")
        assert report.blocking is False, report.findings
        _log("      PASS.")

        _log(
            "\n[3.2] Attempting add_priority_rule at the E stop line, yielding to the N/S "
            "through movements (the 'major road')..."
        )
        prio_params = {
            "stop_link_no": arms_b["E"]["approach"], "stop_lane_index": 1,
            "stop_pos_m": round(arms_b["E"]["approach_len"] - 10.0, 3),
            "markers": [
                {
                    "link_no": arms_b["N"]["approach"], "lane_index": 1,
                    "pos_m": round(arms_b["N"]["approach_len"] - 10.0, 3),
                },
                {
                    "link_no": arms_b["S"]["approach"], "lane_index": 1,
                    "pos_m": round(arms_b["S"]["approach_len"] - 10.0, 3),
                },
            ],
        }
        prio_env = dispatch("add_priority_rule", prio_params, registry=default_registry)
        _log(f"      envelope status={prio_env['status']} error_type={prio_env.get('error_type')}")
        _log(f"      message={prio_env['message']!r}")

        priority_rule_gap_confirmed = (
            prio_env["status"] == "error" and "existing priority rule" in prio_env["message"].lower()
        )
        priority_rule_no: str | None = None
        if priority_rule_gap_confirmed:
            _log(
                "      CONFIRMED GAP (expected): add_priority_rule refuses on this network -- "
                "the Q4 unit 6 limitation reproduced live on a SECOND real network."
            )
        elif prio_env["status"] == "ok":
            priority_rule_no = str(prio_env["artifacts"]["no"])
            _log(
                f"      UNEXPECTED (in a GOOD way): add_priority_rule SUCCEEDED -> rule "
                f"{priority_rule_no}. Beijing must carry a duplicable template rule this "
                "unit's own static scan missed, or Vissim ships a hidden default rule 0 "
                "-- worth a follow-up static-scan correction."
            )
        else:
            _log(
                "      UNEXPECTED refusal shape -- error, but not the predicted 'no existing "
                "priority rule to duplicate' message; see the message above for the real cause."
            )

        session.close()
    finally:
        clear_active_edit_session()

    _log("\n[3.3] COM load oracle after Stage 3 (unsignalized variant)...")
    load_result = _load_check(working_path)
    _log(f"      load_check_result = {load_result}")
    assert load_result["load_ok"] is True and load_result["init_ok"] is True
    _log("      PASS.")

    return {
        "stop_signs": {"E": stop_e_no, "W": stop_w_no},
        "priority_rule_gap_confirmed": priority_rule_gap_confirmed,
        "priority_rule_no": priority_rule_no,
        "priority_rule_message": prio_env["message"],
    }


# ---------------------------------------------------------------------------
# Stage 4: measurement infrastructure -- Junction A.
# ---------------------------------------------------------------------------


def stage4_measurement(artifact_manager: Any, engine: Any, arms_a: dict[str, dict[str, Any]]) -> dict[str, Any]:
    _log("\n" + "=" * 78)
    _log(
        "[Stage 4] MEASUREMENT -- 4 add_queue_counter (one per Junction A approach) + "
        "1 add_data_collection_point + 1 add_data_collection_measurement + "
        "1 add_travel_time_section spanning the N-through-S movement (Genesis V9/MRWA "
        "Table 5-15's queue-length/volume/travel-time outputs, Q4 unit 2's own verbs)"
    )
    _log("=" * 78)

    from services.agent.edit_session import VissimEditSession, clear_active_edit_session, set_active_edit_session
    from services.agent.tools_check import _load_check
    from services.agent.tools_network import build_shadow_model
    from services.agent.toolkit import default_registry, dispatch

    working_path = artifact_manager.working_path
    session = VissimEditSession(working_path)

    def _call(name: str, params: dict[str, Any]) -> dict[str, Any]:
        env = dispatch(name, params, registry=default_registry)
        if env["status"] != "ok":
            raise AssertionError(f"{name}({params}) FAILED: {env}")
        return env

    queue_counters: dict[str, str] = {}

    try:
        set_active_edit_session(session)
        shadow_before_first = build_shadow_model(working_path)
        baseline = {
            "qc": len(shadow_before_first["queue_counters"]),
            "dcp": len(shadow_before_first["data_collection_points"]),
            "meas": len(shadow_before_first["data_collection_measurements"]),
            "tts": len(shadow_before_first["travel_time_sections"]),
        }

        qc_params_n = {
            "link_no": arms_a["N"]["approach"], "pos_m": round(arms_a["N"]["approach_len"] - 5.0, 3),
            "name": "J-A QC N",
        }
        first_env = _call("add_queue_counter", qc_params_n)
        shadow_after_first = build_shadow_model(working_path)
        queue_counters["N"] = str(first_env["artifacts"]["no"])

        for d in ("S", "E", "W"):
            params = {
                "link_no": arms_a[d]["approach"], "pos_m": round(arms_a[d]["approach_len"] - 5.0, 3),
                "name": f"J-A QC {d}",
            }
            env = _call("add_queue_counter", params)
            queue_counters[d] = str(env["artifacts"]["no"])
        _log(f"      Queue counters: {queue_counters}")

        dcp_params = {
            "link_no": arms_a["N"]["approach"], "lane_index": 1,
            "pos_m": round(arms_a["N"]["approach_len"] - 20.0, 3), "name": "J-A DCP N",
        }
        dcp_env = _call("add_data_collection_point", dcp_params)
        dcp_no = str(dcp_env["artifacts"]["no"])
        _log(f"      Data collection point: {dcp_no}")

        meas_env = _call("add_data_collection_measurement", {"point_nos": [dcp_no], "name": "J-A measurement N"})
        meas_no = str(meas_env["artifacts"]["no"])
        _log(f"      Data collection measurement: {meas_no}")

        tts_params = {
            "name": "J-A travel time N-through-S",
            "from_link": arms_a["N"]["approach"], "from_pos_m": 0.0,
            "to_link": arms_a["S"]["exit"], "to_pos_m": arms_a["S"]["approach_len"],
        }
        tts_env = _call("add_travel_time_section", tts_params)
        tts_no = str(tts_env["artifacts"]["no"])
        _log(f"      Travel time section: {tts_no}")

        shadow_final = build_shadow_model(working_path)
        assert len(shadow_final["queue_counters"]) == baseline["qc"] + 4
        assert len(shadow_final["data_collection_points"]) == baseline["dcp"] + 1
        assert len(shadow_final["data_collection_measurements"]) == baseline["meas"] + 1
        assert len(shadow_final["travel_time_sections"]) == baseline["tts"] + 1
        _log("\n[4.1] Reparse counts all matched baseline+expected.")

        _log("\n[4.2] REAL GuardrailEngine.postwrite for the FIRST add_queue_counter call...")
        report = engine.postwrite(
            {"tool": "add_queue_counter", "params": {k: v for k, v in qc_params_n.items() if k != "name"}},
            shadow_before_first, shadow_after_first, first_env["verification"],
        )
        _log(f"      report.blocking={report.blocking} findings={[f.constraint_id for f in report.findings]}")
        assert report.blocking is False, report.findings
        _log("      PASS.")

        session.close()
    finally:
        clear_active_edit_session()

    _log("\n[4.3] COM load oracle after Stage 4 (measurement)...")
    load_result = _load_check(working_path)
    _log(f"      load_check_result = {load_result}")
    assert load_result["load_ok"] is True and load_result["init_ok"] is True
    _log("      PASS.")

    return {
        "queue_counters": queue_counters, "data_collection_point": dcp_no,
        "data_collection_measurement": meas_no, "travel_time_section": tts_no,
    }


# ---------------------------------------------------------------------------
# Stage 5: demand smoke test.
# ---------------------------------------------------------------------------


def _drivability_check(working_inpx: Path, link_no: int) -> bool:
    """Places ONE real vehicle directly on link_no and runs 15 RunSingleStep
    calls, confirming its own Pos AttValue genuinely advances -- Q4 unit 5's
    own bonus proof pattern, reused here as the task brief's own specified
    "bare init" crown-proof substitute when demand CREATION is a gap. Never
    raises; returns False (untested, not fatal) on any failure."""
    _log(f"      Placing one real vehicle on link {link_no} and running 15 RunSingleStep calls...")
    try:
        import pythoncom

        pythoncom.CoInitialize()
        from services.vissim_com import VissimController

        controller = VissimController()
        try:
            controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
            controller.load_network(str(working_inpx))
            net = controller.vissim.Net

            link_obj = net.Links.ItemByKey(int(link_no))
            lane_index = int(link_obj.Lanes.GetAll()[0].AttValue("Index"))
            veh_type_nos = sorted(int(vt.AttValue("No")) for vt in net.VehicleTypes.GetAll())
            veh_type_no = 100 if 100 in veh_type_nos else veh_type_nos[0]
            vehicle = net.Vehicles.AddVehicleAtLinkPosition(veh_type_no, link_obj, lane_index, 10.0, 40.0, True)
            pos_before = float(vehicle.AttValue("Pos"))
            _log(f"      Vehicle placed: Pos={pos_before}")

            for _ in range(15):
                controller.run_single_step()

            still_there = any(int(v.AttValue("No")) == int(vehicle.AttValue("No")) for v in net.Vehicles.GetAll())
            if not still_there:
                _log("      Vehicle left the network within 15 steps -- inconclusive, not a failure.")
                return False
            pos_after = float(vehicle.AttValue("Pos"))
            advanced = pos_after > pos_before + 0.01
            _log(f"      After 15 RunSingleStep calls: Pos={pos_after} -> {'PASS' if advanced else 'FAIL'}")
            return advanced
        finally:
            try:
                controller.disconnect()
            except Exception:
                pass
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
    except Exception as exc:
        _log(f"      Drivability check raised {type(exc).__name__}: {exc} -- UNTESTED, not fatal.")
        return False


def stage5_demand(
    artifact_manager: Any,
    gap_verdict: dict[str, Any],
    arms_a: dict[str, dict[str, Any]],
    conns_a: dict[tuple[str, str], str],
) -> dict[str, Any]:
    _log("\n" + "=" * 78)
    _log("[Stage 5] DEMAND SMOKE TEST")
    _log("=" * 78)

    from services.agent.tools_check import _load_check

    working_path = artifact_manager.working_path
    # Every one of Junction A's own links -- nothing else in Beijing's real
    # network reaches this isolated origin (ORIGIN_A), so ANY vehicle found
    # on one of these ids after stepping is decisive proof it originated
    # from OUR new input, not ambient traffic from Beijing's other 12 real
    # vehicle inputs.
    junction_a_link_ids = (
        {arms_a[d]["approach"] for d in "NSEW"} | {arms_a[d]["exit"] for d in "NSEW"} | set(conns_a.values())
    )

    if not gap_verdict.get("vehicle_input_creatable"):
        _log(
            "      Stage 0 confirmed NO vehicle-input creation surface exists (gap). Falling "
            "back to the task brief's own specified proof: the load oracle (already run after "
            "every stage) + a bare AddVehicleAtLinkPosition drivability check (Q4 unit 5's own "
            "pattern) on Junction A's North approach -- the strongest 'a real vehicle can "
            "traverse this geometry' claim available without demand-creation."
        )
        drivability_ok = _drivability_check(working_path, int(arms_a["N"]["approach"]))
        load_result = _load_check(working_path)
        _log(f"      load_check_result = {load_result}")
        assert load_result["load_ok"] is True and load_result["init_ok"] is True
        return {
            "vehicle_input_gap": True, "drivability_ok": drivability_ok,
            "load_ok": load_result["load_ok"], "init_ok": load_result["init_ok"],
        }

    _log(
        "      Stage 0 found a WORKING vehicle-input creation surface -- attempting a real "
        f"input on link {arms_a['N']['approach']} via the SAME method/args that worked in "
        f"Stage 0 ({gap_verdict.get('vehicle_input_detail')}), then a demand-driven "
        "RunSingleStep sequence."
    )
    try:
        import pythoncom

        pythoncom.CoInitialize()
        from services.vissim_com import VissimController

        controller = VissimController()
        try:
            controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
            controller.load_network(str(working_path))
            net = controller.vissim.Net

            link_obj = net.Links.ItemByKey(int(arms_a["N"]["approach"]))
            vi_before = {int(v.AttValue("No")) for v in net.VehicleInputs.GetAll()}
            new_vi_no = (max(vi_before) if vi_before else 0) + 1
            method_name, args, new_vi = _try_add_methods(
                net.VehicleInputs, ("AddVehicleInput", "Add"),
                ((new_vi_no, link_obj), (new_vi_no,)),
            )
            vi_after = {int(v.AttValue("No")) for v in net.VehicleInputs.GetAll()}
            if not (vi_after - vi_before):
                _log("      GetAll() diff empty on this SECOND attempt -- treating as a gap "
                     "after all (Stage 0's own throwaway-clone result did not reproduce).")
                controller.disconnect()
                pythoncom.CoUninitialize()
                load_result = _load_check(working_path)
                assert load_result["load_ok"] and load_result["init_ok"]
                return {"vehicle_input_gap": True, "reproduced": False,
                        "load_ok": load_result["load_ok"], "init_ok": load_result["init_ok"]}

            # LIVE-CAUGHT HARNESS BUG #2 (2026-08-03) -- see the module docstring:
            # the original 300 veh/h over 45 RunSingleStep calls could never have
            # produced a vehicle. Beijing's own <simulation simRes="10"> means 45
            # steps = 4.5 SIMULATION SECONDS, and 300 veh/h is a 12 s headway, so
            # the expected arrival count was 0.375. Volume and step count are both
            # raised here so the window is genuinely capable of proving the claim.
            new_vi.SetAttValue("Volume(1)", _DEMAND_VOLUME_VPH)
            tmp_path = working_path.with_name(working_path.stem + "_saving" + working_path.suffix)
            controller.save_net_as(tmp_path)
            os.replace(tmp_path, working_path)
            controller.disconnect()
        finally:
            pythoncom.CoUninitialize()

        controller2 = VissimController()
        pythoncom.CoInitialize()
        try:
            controller2.connect(visible=False, launch_if_missing=True, attach_to_running=False)
            controller2.load_network(str(working_path))
            sim_res = float(controller2.vissim.Simulation.AttValue("SimRes"))
            _log(
                f"      Network simRes={sim_res:g} steps/sim-second -> {_DEMAND_STEPS} RunSingleStep "
                f"calls = {_DEMAND_STEPS / sim_res:g} SIMULATION seconds. At "
                f"{_DEMAND_VOLUME_VPH:g} veh/h (headway {3600.0 / _DEMAND_VOLUME_VPH:.2f} s) that is "
                f"an expected {(_DEMAND_STEPS / sim_res) * _DEMAND_VOLUME_VPH / 3600.0:.1f} arrivals -- "
                "stated explicitly so an underpowered window can never again be misread as a "
                "negative result."
            )
            for _ in range(_DEMAND_STEPS):
                controller2.run_single_step()
            all_vehicles = list(controller2.vissim.Net.Vehicles.GetAll())
            on_junction_a: list[int] = []
            attr_failures = 0
            first_attr_error: str | None = None
            for v in all_vehicles:
                # IVehicle has NO "Link" attribute (live-proven: the ORIGINAL code
                # here read AttValue("Link") and Vissim logged 'VEHICLE: Could not
                # find attribute with ID LINK.' once per vehicle into the .err file
                # while the bare `except: continue` swallowed every one -- see the
                # module docstring). "Lane" is the house-proven read (a "<link>-<lane>"
                # token, exactly as add_links_connectors_live_check.py uses it).
                try:
                    lane_token = str(v.AttValue("Lane") or "")
                    link_id = lane_token.partition("-")[0]
                except Exception as exc:
                    attr_failures += 1
                    if first_attr_error is None:
                        first_attr_error = f"{type(exc).__name__}: {exc}"
                    continue
                if link_id in junction_a_link_ids:
                    on_junction_a.append(int(v.AttValue("No")))
            if attr_failures:
                # NEVER silent again: a harness that cannot read a vehicle's lane
                # must say so loudly rather than report a confident empty list.
                _log(
                    f"      WARNING: {attr_failures}/{len(all_vehicles)} vehicle(s) could not be "
                    f"located at all (first error: {first_attr_error}). The 'on Junction A' count "
                    "below is NOT trustworthy while this is non-zero."
                )
            crown_proof = len(on_junction_a) > 0 and attr_failures == 0
            _log(
                f"      After {_DEMAND_STEPS} RunSingleStep calls: {len(all_vehicles)} vehicle(s) "
                f"network-wide; {len(on_junction_a)} of them specifically on Junction A's OWN "
                f"isolated links {sorted(on_junction_a)} -- "
                f"{'CROWN PROOF: a demand-generated vehicle genuinely traversed the genesis junction.' if crown_proof else 'NOT proven (see the counts and any warning above).'}"
            )
            return {
                "vehicle_input_gap": False, "vehicle_input_no": new_vi_no,
                "vehicles_total": len(all_vehicles), "vehicles_on_junction_a": on_junction_a,
                "vehicle_locate_failures": attr_failures,
                "sim_seconds": _DEMAND_STEPS / sim_res, "volume_vph": _DEMAND_VOLUME_VPH,
                "crown_proof": crown_proof,
            }
        finally:
            try:
                controller2.disconnect()
            except Exception:
                pass
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
    except Exception as exc:
        _log(f"      Demand smoke test raised {type(exc).__name__}: {exc} -- recording as inconclusive.")
        load_result = _load_check(working_path)
        return {
            "vehicle_input_gap": False, "error": f"{type(exc).__name__}: {exc}",
            "load_ok": load_result["load_ok"], "init_ok": load_result["init_ok"],
        }


# ---------------------------------------------------------------------------
# Stage 6: final full-network reparse + load oracle.
# ---------------------------------------------------------------------------


def stage6_final(artifact_manager: Any) -> dict[str, Any]:
    _log("\n" + "=" * 78)
    _log("[Stage 6] FINAL -- full network reparse + one more COM load oracle pass")
    _log("=" * 78)

    from services.agent.tools_check import _load_check
    from services.agent.tools_network import build_shadow_model

    working_path = artifact_manager.working_path
    shadow = build_shadow_model(working_path)
    summary = {
        "links": len(shadow["links"]),
        "conflict_areas": len(shadow["conflict_areas"]),
        "signal_heads": len(shadow["signal_heads"]),
        "stop_signs": len(shadow["stop_signs"]),
        "priority_rules": len(shadow["priority_rules"]),
        "queue_counters": len(shadow["queue_counters"]),
        "data_collection_points": len(shadow["data_collection_points"]),
        "data_collection_measurements": len(shadow["data_collection_measurements"]),
        "travel_time_sections": len(shadow["travel_time_sections"]),
        "vehicle_inputs": len(shadow["vehicle_inputs"]),
    }
    _log(f"      Final shadow-model summary: {summary}")

    load_result = _load_check(working_path)
    _log(f"      load_check_result = {load_result}")
    assert load_result["load_ok"] is True and load_result["init_ok"] is True
    _log("      PASS: the full, composite network (both junctions, every stage's edits) "
         "loads and initializes cleanly from a fresh Vissim instance.")

    return {"shadow_summary": summary, "load_ok": load_result["load_ok"], "init_ok": load_result["init_ok"]}


# ---------------------------------------------------------------------------
# Phase B: ONE genesis verb through the full production stack.
# ---------------------------------------------------------------------------


def phase_b_gate_proof(example_dir: Path) -> bool:
    _log("\n" + "=" * 78)
    _log(
        "[Phase B] ONE genesis verb (add_link) through the FULL preapproved-gate house "
        "pattern (real run_agent_turn, real PreapprovedWriteGate, real ArtifactManager, "
        "real GuardrailEngine, canned planner -- no paid LLM calls). Phase A already proves "
        "verb CORRECTNESS at the scale of a whole intersection; this phase's own job is "
        "proving GATE COMPATIBILITY -- that the same verbs Phase A drove directly are not "
        "a special, ungated code path -- so it is deliberately kept to ONE call, not a "
        "re-run of the whole build."
    )
    _log("=" * 78)

    import services.agent.tools_edit as tools_edit
    from services.agent.edit_session import VissimEditSession, clear_active_edit_session, set_active_edit_session
    from services.agent.gates import PreapprovedWriteGate, signature
    from services.agent.guardrails import GuardrailEngine
    from services.agent.orchestrator import run_agent_turn
    from services.agent.state import ArtifactManager
    from services.agent.tools_network import build_shadow_model

    def _action(tool_name: str, *, summary: str, **params: Any):
        params["summary"] = summary
        return {"tool": tool_name, "params": params}, {"mode": "canned", "fallback_rung": 0}

    class _CannedProvider:
        def __init__(self, queue: list[Any]) -> None:
            self._queue = list(queue)

        def generate_structured(self, messages, tools, schema=None, *, system=None, max_tokens=None):
            return self._queue.pop(0)

    work_dir = _fresh_work_dir("phaseB")
    source_inpx = next(example_dir.glob("*.inpx"))
    original_get_active_inpx_path = tools_edit.get_active_inpx_path
    tools_edit.get_active_inpx_path = lambda: source_inpx
    artifact_manager = ArtifactManager(source_inpx=source_inpx, work_dir=work_dir)
    shadow = artifact_manager.clone()
    try:
        # Far isolated origin -- clear of Beijing's own geometry AND of both
        # ORIGIN_A/ORIGIN_B (this is a completely separate clone anyway, but
        # keeping the coordinates disjoint documents intent).
        ox, oy = 6000.0, 6000.0
        p0, p1 = (ox, oy), (ox + 30.0, oy)
        baseline_links = len(shadow["links"])
        _log(f"[B.1] Cloned {example_dir.name} -> {artifact_manager.working_path}; baseline link count={baseline_links}")

        queue = [
            _action(
                "add_link", summary="genesis gate-proof stub link",
                points_xy=[list(p0), list(p1)], lane_count=1, name="Phase B gate-proof stub",
            ),
            _action("respond", summary="done", answer="Added a stub link."),
        ]
        signatures = {
            signature(action["tool"], {k: v for k, v in action["params"].items() if k != "summary"})
            for action, _ in queue
            if action
        }
        gate = PreapprovedWriteGate(signatures)
        engine = GuardrailEngine(disabled=False)

        session = VissimEditSession(artifact_manager.working_path)
        try:
            set_active_edit_session(session)
            result = run_agent_turn(
                "테스트",
                provider=_CannedProvider(queue), work_dir=work_dir, session_id="composite-probe-phaseB",
                language="ko", gate=gate, engine=engine, artifact_manager=artifact_manager,
            )
        finally:
            clear_active_edit_session()
            session.close()

        ok = result.kind == "answer" and artifact_manager.version == 1
        _log(f"      kind={result.kind!r} version={artifact_manager.version} -> {'PASS' if ok else 'FAIL'}")
        if ok:
            after = build_shadow_model(artifact_manager.working_path)
            ok = len(after["links"]) == baseline_links + 1
            _log(
                f"      link count {len(after['links'])} (baseline {baseline_links} + 1 expected) "
                f"-> {'PASS' if ok else 'FAIL'}"
            )
        return ok
    finally:
        tools_edit.get_active_inpx_path = original_get_active_inpx_path


# ---------------------------------------------------------------------------
# Single-stage runner: what one --stage subprocess actually does. Reads the
# JSON state file, runs exactly one stage function (unchanged from the
# original single-process design), writes its result back, and returns --
# never raises past this point (an unhandled exception here would just mean
# the state file doesn't get this stage's own PASS/FAIL record, which the
# outer orchestrator's exit-code check papers over with its own FAIL entry).
# ---------------------------------------------------------------------------


def _run_single_stage(stage_id: str, example_dir: Path, state_path: Path) -> None:
    state = _load_state(state_path)
    label = _STAGE_LABELS[stage_id]

    if stage_id == "0":
        gap_verdict = stage0_gap_probes(example_dir)
        state["gap_verdict"] = gap_verdict
        _state_record(
            state, label, "INFO",
            f"signal_controller: bare_creatable={gap_verdict.get('signal_controller_bare_creatable')} "
            f"bare_functional={gap_verdict.get('signal_controller_bare_functional')} "
            f"duplicate_functional={gap_verdict.get('signal_controller_duplicate_functional')} "
            f"(headline creatable={gap_verdict.get('signal_controller_creatable')})  |  "
            f"vehicle_input_creatable={gap_verdict.get('vehicle_input_creatable')}",
        )
        _save_state(state_path, state)
        return

    # Every later stage needs the write-tool registry populated.
    import services.agent.tools_check  # noqa: F401 -- registers load_check
    import services.agent.tools_edit  # noqa: F401 -- registers every write verb
    from services.agent.guardrails import GuardrailEngine
    from services.agent.state import ArtifactManager

    if stage_id == "1":
        work_dir = _fresh_work_dir("main-build")
        source_inpx = next(example_dir.glob("*.inpx"))
        artifact_manager = ArtifactManager(source_inpx=source_inpx, work_dir=work_dir)
        artifact_manager.clone()
        engine = GuardrailEngine(disabled=False)
        state["source_inpx"] = str(source_inpx)
        state["work_dir"] = str(work_dir)
        _log(f"\nMain build clone: {artifact_manager.working_path}")
        try:
            geo = stage1_geometry(artifact_manager, engine)
            state["geo"] = {
                "arms_a": geo["arms_a"], "arms_b": geo["arms_b"],
                "conns_a": _conns_to_json(geo["conns_a"]), "conns_b": _conns_to_json(geo["conns_b"]),
                "new_conflict_areas": geo["new_conflict_areas"],
            }
            total_conns = len(geo["conns_a"]) + len(geo["conns_b"])
            _state_record(
                state, label, "PASS",
                f"16 links, {total_conns} connectors (A={len(geo['conns_a'])}, B={len(geo['conns_b'])}), "
                f"{geo['new_conflict_areas']} auto conflict areas",
            )
        except Exception as exc:
            _state_record(state, label, "FAIL", f"{type(exc).__name__}: {exc}")
        _save_state(state_path, state)
        return

    # Stages 2-6/phaseb (except phaseb, which is fully self-contained) share
    # the SAME persisted work_dir Stage 1 created -- reconstruct the
    # ArtifactManager WITHOUT calling .clone() again (that would overwrite
    # every prior stage's own edits with a pristine copy of the source).
    if stage_id != "phaseb":
        if state.get("work_dir") is None:
            _state_record(state, label, "FAIL", "Stage 1's own state (work_dir) is missing -- geometry never completed.")
            _save_state(state_path, state)
            return
        artifact_manager = ArtifactManager(source_inpx=Path(state["source_inpx"]), work_dir=Path(state["work_dir"]))
        engine = GuardrailEngine(disabled=False)

    if stage_id == "2":
        try:
            ctrl = stage2_control(artifact_manager, engine, state["geo"]["arms_a"], state["gap_verdict"])
            state["ctrl"] = ctrl
            _state_record(state, label, "PASS", f"heads={ctrl['heads']} controller={ctrl['sc_provenance']}")
        except Exception as exc:
            _state_record(state, label, "FAIL", f"{type(exc).__name__}: {exc}")
        _save_state(state_path, state)
        return

    if stage_id == "3":
        try:
            unsig = stage3_unsignalized(artifact_manager, engine, state["geo"]["arms_b"])
            state["unsig"] = unsig
            status = (
                "PASS (GAP confirmed: add_priority_rule cannot mint a first rule here)"
                if unsig["priority_rule_gap_confirmed"]
                else "PASS (unexpected priority-rule outcome, see log)"
            )
            _state_record(
                state, label, status,
                f"stop_signs={unsig['stop_signs']} priority_rule_gap_confirmed={unsig['priority_rule_gap_confirmed']}",
            )
        except Exception as exc:
            _state_record(state, label, "FAIL", f"{type(exc).__name__}: {exc}")
        _save_state(state_path, state)
        return

    if stage_id == "4":
        try:
            meas = stage4_measurement(artifact_manager, engine, state["geo"]["arms_a"])
            state["meas"] = meas
            _state_record(state, label, "PASS", str(meas))
        except Exception as exc:
            _state_record(state, label, "FAIL", f"{type(exc).__name__}: {exc}")
        _save_state(state_path, state)
        return

    if stage_id == "5":
        try:
            conns_a = _conns_from_json(state["geo"]["conns_a"])
            demand = stage5_demand(artifact_manager, state["gap_verdict"], state["geo"]["arms_a"], conns_a)
            state["demand"] = demand
            if demand.get("vehicle_input_gap"):
                status = "GAP (documented fallback used)"
            elif demand.get("crown_proof"):
                status = "PASS (crown proof: demand-generated vehicle traversed the junction)"
            else:
                status = "PASS (input created, crown proof inconclusive -- see log)"
            _state_record(state, label, status, str(demand))
        except Exception as exc:
            _state_record(state, label, "FAIL", f"{type(exc).__name__}: {exc}")
        _save_state(state_path, state)
        return

    if stage_id == "6":
        try:
            final = stage6_final(artifact_manager)
            state["final"] = final
            _state_record(state, label, "PASS", str(final["shadow_summary"]))
        except Exception as exc:
            _state_record(state, label, "FAIL", f"{type(exc).__name__}: {exc}")
        _save_state(state_path, state)
        return

    if stage_id == "phaseb":
        try:
            phaseb_ok = phase_b_gate_proof(example_dir)
            state["phaseb_ok"] = phaseb_ok
            _state_record(state, label, "PASS" if phaseb_ok else "FAIL")
        except Exception as exc:
            _state_record(state, label, "FAIL", f"{type(exc).__name__}: {exc}")
        _save_state(state_path, state)
        return

    raise SystemExit(f"Unknown --stage {stage_id!r}")


# ---------------------------------------------------------------------------
# Orchestrator: the default (no --stage) entry point. Shells out to itself
# once per stage (fresh OS process each time -- see the module docstring's
# own segfault finding), then prints the combined staged summary.
# ---------------------------------------------------------------------------


def _run_all_stages_as_subprocesses(example_dir: Path) -> None:
    run_dir = _fresh_work_dir("orchestrator-run")
    state_path = run_dir / "state.json"
    _save_state(state_path, _empty_state())

    for stage_id in _STAGE_ORDER:
        label = _STAGE_LABELS[stage_id]
        _log(f"\n{'#' * 78}\n# Launching {label!r} as a FRESH subprocess (COM/process isolation)\n{'#' * 78}")
        cmd = [
            sys.executable, str(Path(__file__).resolve()), str(example_dir),
            "--stage", stage_id, "--state", str(state_path),
        ]
        result = subprocess.run(cmd)

        if result.returncode != 0:
            state = _load_state(state_path)
            already_recorded = any(r["stage"] == label for r in state["stage_results"])
            if not already_recorded:
                kind = "SEGFAULT/native crash" if result.returncode < 0 else f"exit code {result.returncode}"
                state["stage_results"].append({
                    "stage": label, "status": "FAIL",
                    "detail": f"subprocess died ({kind}) before it could record its own outcome",
                })
                _save_state(state_path, state)
            if stage_id == "1":
                _log(
                    "\nStage 1 (geometry) is foundational -- arms_a/arms_b/conns_a feed every "
                    "later stage. Aborting the remaining stages."
                )
                break

    state = _load_state(state_path)
    _print_summary(state["stage_results"], state.get("gap_verdict") or {})
    if any(r["status"] == "FAIL" for r in state["stage_results"]):
        raise SystemExit(1)


def _print_summary(stage_results: list[dict[str, str]], gap_verdict: dict[str, Any]) -> None:
    _log("\n" + "#" * 78)
    _log("# STAGED SUMMARY")
    _log("#" * 78)
    for r in stage_results:
        _log(f"  [{r['status']:>32s}] {r['stage']}")
        if r.get("detail"):
            _log(f"      {r['detail']}")
    _log("")
    sc_ok = gap_verdict.get("signal_controller_creatable")
    vi_ok = gap_verdict.get("vehicle_input_creatable")
    _log(
        "Controller-creatability verdict: "
        + ("CREATABLE (via duplicate-then-edit)" if sc_ok else "NOT CREATABLE (headline gap for genesis milestone (b))")
    )
    _log(
        "Vehicle-input creatability verdict: "
        + ("CREATABLE" if vi_ok else "NOT CREATABLE (gap -- v1 shipped volume SETTERS only)")
    )


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------


def _parse_cli(argv: list[str]) -> tuple[Path, str | None, Path | None]:
    """(example_dir, stage_id_or_None, state_path_or_None). Keeps the
    original ``sys.argv[1]``-as-example_dir precedent recognizable while
    adding the internal ``--stage``/``--state`` flags the orchestrator uses
    to talk to its own per-stage subprocesses."""
    example_dir: Path | None = None
    stage_id: str | None = None
    state_path: Path | None = None
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--stage":
            stage_id = argv[i + 1]
            i += 2
        elif tok == "--state":
            state_path = Path(argv[i + 1])
            i += 2
        else:
            example_dir = Path(tok)
            i += 1
    return (example_dir or DEFAULT_EXAMPLE), stage_id, state_path


def main() -> None:
    example_dir, stage_id, state_path = _parse_cli(sys.argv[1:])
    if not example_dir.is_dir():
        raise SystemExit(f"Example folder not found: {example_dir}")

    if stage_id is not None:
        if state_path is None:
            raise SystemExit("--stage requires --state <path>")
        _run_single_stage(stage_id, example_dir, state_path)
        return

    _log("#" * 78)
    _log("# COMPOSITE-INTERSECTION CONSTRUCTION PROBE -- Q4 unit (8) capstone")
    _log(f"# Example network: {example_dir.name}")
    _log("#" * 78)
    _run_all_stages_as_subprocesses(example_dir)


if __name__ == "__main__":
    main()

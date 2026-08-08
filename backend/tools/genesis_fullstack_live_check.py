"""GENESIS **FULL-STACK** LIVE CHECK: does a ``services.genesis`` draft carrying
geometry + signals + demand + FLEET — the whole self-contained package in ONE
``.inpx`` — open in a real PTV Vissim, and what does Vissim itself say about the
two external boundaries the OFFLINE fleet unit correctly refused to claim?

The genesis package now emits geometry (``osm``/``draft``/``emit``), signals
(``signals.py``, ``4f38e39`` — loads live, S6 measured ``0efaf5d``), demand
(``demand.py``, ``2971385``) and fleet (``fleet.py``, ``bb6af41``). ``emit.py``
takes all four on one call (``emit_inpx(draft, path, signals=, demand=,
fleet=)``). Everything that could be settled offline was; TWO boundaries only a
real Vissim can settle are named IN ``fleet.py``'s own disclosures, and this is
their named follow-up. It settles exactly those two and FIXES NOTHING — a wrong
emission gets a finding and the exact fix, not a patch.

  B1  ``models2D3D/@file3D`` points at a ``.u3dm``/``.fbx`` asset on the Vissim
      install (``#3dmodels#…``) that no ``.inpx`` embeds. Does a fleet-carrying
      draft LOAD — does Vissim resolve those model refs, refuse, or substitute a
      fallback? If LoadNet succeeds but a model is unresolved, HOW Vissim
      represents that is the finding. Evidence: the ``.err`` verbatim (any line
      mentioning a 3D model / ``.u3dm`` / a missing file), the COM read-back of
      each vehicle type's ``Model2D3DDistr`` reference, and the saved file's
      ``models2D3D`` closure (did ``file3D`` survive, get rewritten, or get a
      fallback).

  B2  Table 5-2's acceleration envelope is INVERTED at 96 km/h for every heavy
      type (``fleet.py``: type 3 reads yMin=0.098 > y=0.043 > yMax=0.031 there,
      verbatim from the guideline; the module emits it unswapped and WARNs). A
      Vissim acceleration function expects ``yMin ≤ y ≤ yMax``. Does Vissim
      accept the inverted-envelope desired-acceleration distribution on load,
      and what does it read back — verbatim (still inverted), clamped, reordered,
      or does it reject the load? Evidence: the emitted vs SAVED
      ``desAccelerationFunction no="503"`` (type 3) data point at 96 km/h, plus a
      best-effort COM read of the same function.

Re-confirmed in the SAME combined file, because the point is that the four
layers COMPOSE and nothing regressed: the signal groups' green windows still
read back as the plan's numbers, the vehicle inputs still carry their volumes,
and the compositions still resolve to the declared vehicle types (no dangling
``vehType``/``desSpeedDistr`` reference — the failure class this project exists
to catch).

THE NETWORK UNDER TEST
----------------------
A synthetic four-arm crossroads, hand-built through ``services.genesis``'s own
dataclasses (NOT via OSM — one draft has to satisfy three generators at once, so
its topology is constructed to be simultaneously: four approaches each with an
onward through movement, for signals; four ENTRY links each with counted demand,
for demand; three observed vehicle classes that map to MRWA types, for fleet):

    4 approach links (1/3/5/7, each 100 m, arriving at node 1)
    4 exit links     (2/4/6/8, each 100 m, leaving node 1)
    4 through connectors (1->6, 3->8, 5->2, 7->4)
      -> generate_signals   on 1.6's A.2 composite (cycle 78, greens 35/21/6),
         approach->phase {1:A, 3:A, 5:B, 7:C}
      -> build_fleet        on {car(LIGHT), truck(HEAVY), bus(BUS)} -> types
         {1 Short, 3 Two axle truck, 30 Bus}; type 3 carries the inverted
         Table 5-2 envelope (B2) and every type names an install-asset model (B1)
      -> generate_demand     approach_links {leg->1/3/5/7}, vehicle_types =
         fleet.map, counts car/truck/bus THROUGH on every approach
      -> emit.emit_inpx(draft, path, signals, demand, fleet)   ONE file

The approach->phase mapping and the class set are exactly the shapes
``test_genesis_signals.py`` / ``test_genesis_demand.py`` / ``test_genesis_fleet.py``
use; nothing here claims §5 compliance the offline units did not already grade.

WHAT THIS SCRIPT TOUCHES — and what it refuses to do
----------------------------------------------------
Identical envelope to ``genesis_signals_live_check.py`` /
``genesis_open_live_check.py``:

* ``connect(..., attach_to_running=False)`` ALWAYS (00-EXECUTION-GUIDE rule:
  GetActiveObject attaches to a human's open GUI and LoadNet would destroy it).
* NO real dataset is opened, in place or otherwise. Every network loaded is a
  file this script wrote into ``runtime/genesis-fullstack-live-check/nets/``.
* LoadNet -> ``.err`` capture -> COM census (READ ONLY — no ``add_*``, no
  ``SetAttValue``) -> SaveNetAs (NEW name, same work dir) -> disconnect. **NO
  simulation**: no ``RunContinuous``, no ``RunSingleStep``, ever. This probe
  makes ZERO COM writes — the census reads, the save round-trips, nothing edits.
* BOUNDED. All COM work runs in a CHILD process under a wall clock. On timeout
  the child is killed and every ``vissim.exe`` that did not exist before the
  child started is killed too — PIDs present beforehand (a human's session) are
  never touched, and leftovers are re-counted and logged after cleanup.

IF LoadNet REFUSES
------------------
A refusal is a FINDING, not a probe failure, and it is bisected to the layer
that caused it — the four layers are grafted on ONE at a time onto the geometry
skeleton already proven to load (``genesis_open_live_check``, ``small_v2``):
geometry-only -> geometry+fleet -> +demand -> +signals — each ``LoadNet``-ed in
one session (loads only, nothing saved, nothing edited). The rung that first
refuses names the emitter whose emission Vissim rejects (which this probe does
NOT fix — it names the fix).

Usage (from ``backend/``, with the venv python)::

    python tools/genesis_fullstack_live_check.py                 # build + live
    python tools/genesis_fullstack_live_check.py --only build    # offline only
    python tools/genesis_fullstack_live_check.py --only live
    python tools/genesis_fullstack_live_check.py --only bisect   # if live refused
    python tools/genesis_fullstack_live_check.py --only all

Re-runnable and idempotent: each run recreates its own ``nets/<label>/`` from
scratch and merges results into ``state.json`` under that label. ``log.txt`` is
appended with a run header (the house tee rule: live-check evidence lands in
``runtime/``, not only on a console).
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - detached stream
    pass

WORK_ROOT = REPO_ROOT / "runtime" / "genesis-fullstack-live-check"
NETS_ROOT = WORK_ROOT / "nets"
STATE_PATH = WORK_ROOT / "state.json"
LOG_PATH = WORK_ROOT / "log.txt"

DEFAULT_LIVE_TIMEOUT_S = 480.0
DEFAULT_BISECT_TIMEOUT_S = 600.0

_MAX_ERR_CHARS = 20000
_MAX_FRAGMENT_CHARS = 6000

#: The heavy vehicle type whose Table 5-2 envelope is inverted at 96 km/h, and
#: the desAccelerationFunction number it references (500 + type_no, from
#: ``fleet.MRWA_DES_ACCEL_FUNC_OFFSET``). B2 is asked at this function, this speed.
INVERTED_TYPE_NO = 3
INVERTED_FUNC_NO = 503
INVERTED_SPEED_KMH = "96"

#: Vissim-object-level ids that might carry the version/product string.
_VERSION_CANDIDATES = (
    "Version",
    "VersionNumber",
    "ProductName",
    "LicenseInfo",
    "LicenceInfo",
    "ServicePack",
    "Build",
)

#: Vehicle-type attribute ids probed defensively off the live COM item. The
#: reference fields (``Model2D3DDistr`` … ``MaxDecelFunc``) are the same ids
#: ``VissimController.VEHICLE_TYPE_REFERENCE_FIELDS`` maps to; ``Category`` is
#: read-only. An id Vissim does not expose is recorded unavailable, never
#: inferred.
_VT_ATTR_CANDIDATES = (
    "No",
    "Name",
    "Category",
    "Model2D3DDistr",
    "ColorDistr1",
    "DesAccelFunc",
    "MaxAccelFunc",
    "DesDecelFunc",
    "MaxDecelFunc",
    "DesSpeedDistr",
    "Capacity",
)

#: Signal-group attribute ids read back off the live COM object (same set the
#: signals live check probes).
_SG_ATTR_CANDIDATES = (
    "No",
    "Name",
    "Type",
    "Amber",
    "RedAmber",
    "MinGreen",
    "MaxGreen",
    "EndGreen",
    "EndRed",
    "ClearTm",
    "MinRed",
)


# ---------------------------------------------------------------------------
# Logging / state (tee — the house rule: evidence lands in runtime/)
# ---------------------------------------------------------------------------


def _log(msg: str = "") -> None:
    print(msg, flush=True)
    try:
        WORK_ROOT.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
    except OSError:
        pass


_STATE: dict[str, Any] = {}


def _load_state() -> dict[str, Any]:
    global _STATE
    if STATE_PATH.exists():
        try:
            _STATE = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except ValueError:
            _STATE = {}
    return _STATE


def _save_state() -> None:
    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(_STATE, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# The A.2 composite plan — 1.6's own, produced by the converter (never hand-set)
# ---------------------------------------------------------------------------

A2_CYCLE_S = 78.0
A2_GREENS_S = [35.0, 21.0, 6.0]
A2_OFFSETS_S = [0.0, 41.0, 67.0]

#: approach link id -> plan phase id. Two opposing approaches share phase A; the
#: other two take B and C — the standard-crossroads layout the signals unit's own
#: fixture (``STANDARD_ASSIGNMENT``) uses.
APPROACH_PHASES = {1: "A", 3: "A", 5: "B", 7: "C"}


def build_a2_plan() -> Any:
    """1.6's A.2 composite, produced by ``convert_to_fixed_time`` from the same
    10-cycle history the suites use (totals 410/260/110 over 10 cycles give
    41/26/11 averages with no rounding residual, so cycle 78 / greens 35-21-6 /
    offsets 0-41-67 come out of the converter, not out of this file)."""
    from services.dataimport.fixed_time import convert_to_fixed_time
    from services.dataimport.schema import (
        PhaseData,
        ProgramSourceKind,
        Provenance,
        SignalControllerData,
        SignalProgramData,
    )

    prov = Provenance(source_file="scats-history.csv", locator="row 1")

    def phase(phase_id, seq, total_s, min_green_s, all_red_s, stretch=False):
        return PhaseData(
            provenance=prov,
            phase_id=phase_id,
            sequence_index=seq,
            amber_s=3.0,
            all_red_s=all_red_s,
            is_stretch_phase=stretch,
            observation_count=10,
            observed_total_duration_s=total_s,
            min_green_s=min_green_s,
        )

    program = SignalProgramData(
        provenance=prov,
        program_id="P1",
        source_kind=ProgramSourceKind.OBSERVED_HISTORY,
        observed_cycle_count=10,
        phases=[
            phase("A", 0, 410.0, 10.0, 3.0, stretch=True),
            phase("B", 1, 260.0, 8.0, 2.0),
            phase("C", 2, 110.0, 5.0, 2.0),
        ],
    )
    return convert_to_fixed_time(
        SignalControllerData(provenance=prov, controller_id="TCS999", programs=[program])
    )


# ---------------------------------------------------------------------------
# The crossroads draft + its demand counts — hand-built through the genesis
# dataclasses, exactly as the three test suites build their fixtures.
# ---------------------------------------------------------------------------

APPROACH_LENGTH_M = 100.0
SITE = "가람네거리"
#: approach link id -> the leg name the counts use for it.
LEG_OF_LINK = {1: "북측", 3: "동측", 5: "남측", 7: "서측"}


def _crossroads_draft() -> Any:
    """Four approaches (1/3/5/7) into node 1, each with a through movement onto
    its own exit (2/4/6/8). The approaches are the ENTRY links (no connector
    arrives on them) and the ONLY approaches of node 1 (``to_node_osm_id == 1``),
    so ``entry_links`` and ``node_approaches`` both return [1, 3, 5, 7]."""
    from services.genesis.draft import DraftConnector, DraftLink, DraftNetwork, DraftNode

    def link(link_id, name, points, from_node, to_node, lanes=1):
        return DraftLink(
            id=link_id,
            name=name,
            osm_way_id=link_id,
            segment_index=0,
            forward=True,
            lanes=lanes,
            lane_width_m=3.5,
            points=points,
            lane_provenance={"source": "genesis_fullstack_live_check"},
            highway="primary",
            from_node_osm_id=from_node,
            to_node_osm_id=to_node,
            side_offset_m=0.0,
        )

    def connector(conn_id, from_link, to_link):
        return DraftConnector(
            id=conn_id,
            name=f"{from_link}->{to_link}",
            node_osm_id=1,
            node_id=1,
            from_link=from_link,
            to_link=to_link,
            from_pos_m=APPROACH_LENGTH_M,
            to_pos_m=0.0,
            lanes=1,
            lane_width_m=3.5,
            points=[(0.0, 0.0), (1.0, 1.0)],
            turn="through",
            heading_change_deg=0.0,
            lane_change="same",
            from_lanes=[1],
            to_lanes=[1],
        )

    links = [
        # approaches (arrive at node 1)
        link(1, "North approach", [(0.0, APPROACH_LENGTH_M), (0.0, 0.0)], 101, 1),
        link(3, "East approach", [(APPROACH_LENGTH_M, 0.0), (0.0, 0.0)], 103, 1),
        link(5, "South approach", [(0.0, -APPROACH_LENGTH_M), (0.0, 0.0)], 105, 1),
        link(7, "West approach", [(-APPROACH_LENGTH_M, 0.0), (0.0, 0.0)], 107, 1),
        # exits (leave node 1)
        link(2, "South exit", [(0.0, 0.0), (0.0, -APPROACH_LENGTH_M)], 1, 102),
        link(4, "West exit", [(0.0, 0.0), (-APPROACH_LENGTH_M, 0.0)], 1, 104),
        link(6, "North exit", [(0.0, 0.0), (0.0, APPROACH_LENGTH_M)], 1, 106),
        link(8, "East exit", [(0.0, 0.0), (APPROACH_LENGTH_M, 0.0)], 1, 108),
    ]
    connectors = [
        connector(10001, 1, 2),  # N approach -> S exit  (through)
        connector(10002, 3, 4),  # E approach -> W exit
        connector(10003, 5, 6),  # S approach -> N exit
        connector(10004, 7, 8),  # W approach -> E exit
    ]
    node = DraftNode(
        id=1,
        name="n1",
        osm_id=1,
        x=0.0,
        y=0.0,
        polygon=[(-15.0, -15.0), (15.0, -15.0), (15.0, 15.0), (-15.0, 15.0)],
    )
    return DraftNetwork(
        name="fullstack_crossroads",
        source_osm="fullstack_crossroads.osm",
        links=links,
        nodes=[node],
        connectors=connectors,
    )


def _observed_classes() -> tuple[Any, Any, Any]:
    from services.dataimport.schema import (
        CanonicalVehicleClass,
        ClassificationScheme,
        VehicleClassRef,
    )

    def cls(code, canonical):
        return VehicleClassRef(
            scheme=ClassificationScheme.KOREA_SMART_INTERSECTION_6,
            code=code,
            canonical=canonical,
        )

    car = cls("승용", CanonicalVehicleClass.LIGHT_VEHICLE)
    truck = cls("화물_대형", CanonicalVehicleClass.HEAVY_VEHICLE)
    bus = cls("버스_소형", CanonicalVehicleClass.BUS)
    return car, truck, bus


def _counts_result() -> Any:
    """Four 15-minute bins per approach, THROUGH movement only (the one drafted
    turn per approach), car/truck/bus each bin — flat, so peak == mean and every
    figure is exact."""
    from services.dataimport.base import AdapterInfo, ImportResult
    from services.dataimport.schema import (
        ApproachRef,
        MovementRef,
        Provenance,
        TurningCountData,
        TurnType,
        VolumeUnit,
    )

    car, truck, bus = _observed_classes()
    prov = Provenance(source_file="counts.xlsx", locator="Sheet1!row 4")
    base = datetime(2026, 4, 8, 7, 0)
    rows = []
    for link_id, leg in LEG_OF_LINK.items():
        for index in range(4):
            start = base + timedelta(minutes=15 * index)
            for vehicle_class, volume in ((car, 30), (truck, 10), (bus, 5)):
                rows.append(
                    TurningCountData(
                        provenance=prov,
                        site_id=SITE,
                        approach=ApproachRef(leg_id=leg),
                        movement=MovementRef(turn=TurnType.THROUGH),
                        vehicle_class=vehicle_class,
                        interval_start=start,
                        interval_end=start + timedelta(minutes=15),
                        volume=volume,
                        volume_unit=VolumeUnit.VEHICLES,
                    )
                )
    return ImportResult(
        adapter=AdapterInfo(name="test", region="XX", source_system="Test", version="0"),
        counts=rows,
    )


# ---------------------------------------------------------------------------
# Offline build of the full-stack network under test
# ---------------------------------------------------------------------------


def build_fullstack_network(target_dir: Path) -> dict[str, Any]:
    """Regenerate the crossroads draft + signals + demand + fleet through
    ``services.genesis`` into ``target_dir``.

    Writes the four bisect inputs as well: geometry-only, geometry+fleet,
    geometry+fleet+demand and the full four-layer file (the network under test),
    so a refusal can be located without re-running the build."""
    from services.dataimport.base import IssueSeverity
    from services.genesis import signals as signals_module
    from services.genesis.demand import DemandSettings, REDUCTION_PEAK, generate_demand
    from services.genesis.emit import emit_inpx
    from services.genesis.fleet import build_fleet, observed_vehicle_classes

    draft = _crossroads_draft()
    if draft.refused:
        raise RuntimeError(f"crossroads draft was refused: {draft.refusals}")

    # -- signals ------------------------------------------------------------
    plan = build_a2_plan()
    plan_check = {
        "ok": bool(plan.ok),
        "cycle_s": plan.plan_cycle_s,
        "greens_s": [row.green_s for row in plan.rows],
        "offsets_s": [row.start_offset_s for row in plan.rows],
        "is_the_a2_composite": bool(plan.ok)
        and plan.plan_cycle_s == A2_CYCLE_S
        and [row.green_s for row in plan.rows] == A2_GREENS_S
        and [row.start_offset_s for row in plan.rows] == A2_OFFSETS_S,
    }
    signals = signals_module.generate_signals(draft, {draft.nodes[0].id: (plan, APPROACH_PHASES)})

    # -- fleet --------------------------------------------------------------
    result = _counts_result()
    classes = observed_vehicle_classes(result)
    fleet = build_fleet(classes)

    # -- demand (references the fleet's own type map) -----------------------
    approach_links = {SITE: {LEG_OF_LINK[link_id]: link_id for link_id in LEG_OF_LINK}}
    demand = generate_demand(
        draft,
        result,
        approach_links=approach_links,
        vehicle_types=dict(fleet.map),
        settings=DemandSettings(single_interval_reduction=REDUCTION_PEAK),
    )

    # -- emit ALL FOUR (the network under test) + the three bisect inputs ---
    full = emit_inpx(draft, target_dir / "full.inpx", signals, demand, fleet)
    geom = emit_inpx(draft, target_dir / "geometry_only.inpx")
    geom_fleet = emit_inpx(draft, target_dir / "geometry_fleet.inpx", None, None, fleet)
    geom_fleet_demand = emit_inpx(
        draft, target_dir / "geometry_fleet_demand.inpx", None, demand, fleet
    )

    node_signals = signals.nodes[0] if signals.nodes else None
    expected_groups = [
        {
            "no": g.no,
            "approach_link_id": g.approach_link_id,
            "phase_id": g.phase_id,
            "green_start_s": g.green_start_s,
            "green_s": g.green_s,
            "green_end_s": g.green_end_s,
            "red_start_s": g.red_start_s,
            "amber_s": g.amber_s,
            "all_red_s": g.all_red_s,
        }
        for g in (node_signals.groups if node_signals else ())
    ]

    expected = {
        "vehicle_types": [
            {
                "no": t.no,
                "name": t.name,
                "category": t.category,
                "model_distr_no": t.model_distr_no,
                "des_accel_func_no": t.des_accel_func_no,
                "heavy_desired_accel": t.heavy_desired_accel,
            }
            for t in sorted(fleet.types, key=lambda t: t.no)
        ],
        "fleet_map": {code: ref.veh_type_no for code, ref in sorted(fleet.map.items())},
        "speed_distr_no": fleet.speed_distr_no,
        "groups": expected_groups,
        "controller_no": node_signals.controller_no if node_signals else None,
        "cycle_s": node_signals.cycle_s if node_signals else None,
        "demand_inputs": [
            {"no": p.no, "link_id": p.link_id, "volume_vph": p.emitted_volume_vph}
            for p in demand.inputs
        ],
        "composition_type_nos": sorted(
            {flow.veh_type_no for plan_ in demand.compositions for flow in plan_.flows}
        ),
        "composition_speed_distr_nos": sorted(
            {flow.des_speed_distr_no for plan_ in demand.compositions for flow in plan_.flows}
        ),
    }
    (target_dir / "expected.json").write_text(
        json.dumps(expected, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )

    # The two boundary anchors, read straight off the EMITTED file so the live
    # comparison is emitted-vs-saved, not code-vs-saved.
    emitted_root = _parse(full)
    b1_emitted = _models_index(emitted_root)
    b2_emitted = _accel_point(emitted_root, INVERTED_FUNC_NO, INVERTED_SPEED_KMH)

    return {
        "full_inpx": str(full),
        "geometry_only_inpx": str(geom),
        "geometry_fleet_inpx": str(geom_fleet),
        "geometry_fleet_demand_inpx": str(geom_fleet_demand),
        "full_bytes": full.stat().st_size,
        "draft": {
            "links": len(draft.links),
            "connectors": len(draft.connectors),
            "nodes": len(draft.nodes),
        },
        "plan_check": plan_check,
        "signals_ok": bool(signals.ok),
        "signals_issue_codes": sorted({i.code for i in signals.issues}),
        "fleet_ok": bool(fleet.ok),
        "fleet_types": [t.no for t in sorted(fleet.types, key=lambda t: t.no)],
        "fleet_warn_codes": fleet.issue_codes(IssueSeverity.WARN),
        "fleet_info_codes": fleet.issue_codes(IssueSeverity.INFO),
        "demand_ok": bool(demand.ok),
        "demand_issue_codes": sorted({i.code for i in demand.issues}),
        "demand_input_count": len(demand.inputs),
        "demand_composition_count": len(demand.compositions),
        "expected": expected,
        "b1_models_emitted": b1_emitted,
        "b2_accel_point_emitted": b2_emitted,
        "counts": {
            "vehicle_types": len(expected["vehicle_types"]),
            "signal_groups": len(expected["groups"]),
            "vehicle_inputs": len(expected["demand_inputs"]),
        },
    }


# ---------------------------------------------------------------------------
# XML helpers (offline analysis; no COM)
# ---------------------------------------------------------------------------


def _strip_ns(root: ET.Element) -> ET.Element:
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
        for key in list(el.attrib):
            if "}" in key:
                el.attrib[key.split("}", 1)[1]] = el.attrib.pop(key)
    return root


def _parse(path: Path) -> ET.Element:
    return _strip_ns(ET.parse(path).getroot())


def _numeric_equal(a: str | None, b: str | None) -> bool:
    if a is None or b is None:
        return a == b
    if a == b:
        return True
    try:
        return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=1e-6)
    except (TypeError, ValueError):
        return False


def _diff_attrs(emitted: dict[str, str], saved: dict[str, str]) -> dict[str, Any]:
    added = sorted(set(saved) - set(emitted))
    dropped = sorted(set(emitted) - set(saved))
    changed = {
        key: {"emitted": emitted[key], "saved": saved[key]}
        for key in sorted(set(emitted) & set(saved))
        if emitted[key] != saved[key]
    }
    reformat = {k: v for k, v in changed.items() if _numeric_equal(v["emitted"], v["saved"])}
    real = {k: v for k, v in changed.items() if k not in reformat}
    return {
        "attrs_added_by_vissim": added,
        "attrs_dropped_by_vissim": dropped,
        "values_rewritten": real,
        "reformatted_only": reformat,
    }


#: The eight container families the round-trip table names, each as
#: (family key, container tag, element tag).
_FAMILIES = (
    ("vehicleType", "vehicleTypes", "vehicleType"),
    ("model2D3D", "models2D3D", "model2D3D"),
    ("desSpeedDistribution", "speedDistributions", "speedDistribution"),
    ("desAcceleration", "desAccelerationFunctions", "desAccelerationFunction"),
    ("vehicleComposition", "vehicleCompositions", "vehicleComposition"),
    ("vehicleInput", "vehicleInputs", "vehicleInput"),
    ("routingDecision", "vehicleRoutingDecisionsStatic", "vehicleRoutingDecisionStatic"),
    ("signalController", "signalControllers", "signalController"),
)


def _family_index(root: ET.Element, container: str, element: str) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for el in root.findall(f"./{container}/{element}"):
        no = el.get("no") or "?"
        # ``intSupplyData`` is a huge base64 blob; keep it out of the attr diff
        # (its round trip is checked through the decoded green windows instead).
        out[no] = {k: v for k, v in el.attrib.items() if k != "intSupplyData"}
    return out


def _container_round_trip(emitted: ET.Element, saved: ET.Element) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for key, container, element in _FAMILIES:
        em = _family_index(emitted, container, element)
        sv = _family_index(saved, container, element)
        per_object = {
            no: _diff_attrs(em[no], sv[no]) for no in sorted(set(em) & set(sv))
        }
        rows[key] = {
            "emitted_count": len(em),
            "saved_count": len(sv),
            "ids_missing_in_saved": sorted(set(em) - set(sv)),
            "ids_added_in_saved": sorted(set(sv) - set(em)),
            "per_object_with_changes": {
                no: diff
                for no, diff in per_object.items()
                if diff["attrs_added_by_vissim"]
                or diff["attrs_dropped_by_vissim"]
                or diff["values_rewritten"]
            },
        }
    return rows


# --- B1: the 2D/3D model closure -------------------------------------------


def _models_index(root: ET.Element) -> dict[str, Any]:
    """Every ``models2D3D/model2D3D`` and each of its segments' ``file3D``."""
    out: dict[str, Any] = {"models": {}, "file3D_refs": []}
    for model in root.findall("./models2D3D/model2D3D"):
        no = model.get("no") or "?"
        segs = []
        for seg in model.findall("./model2D3DSegs/model2D3DSegment"):
            file3d = seg.get("file3D")
            segs.append({"file3D": file3d, "length": seg.get("length"), "width": seg.get("width")})
            if file3d:
                out["file3D_refs"].append(file3d)
        out["models"][no] = {"name": model.get("name"), "segments": segs}
    # the distribution -> model link, and the vehicleType -> distribution link
    out["distributions"] = {
        d.get("no"): [
            e.get("model2D3D")
            for e in d.findall("./model2D3DDistrEl/model2D3DDistributionElement")
        ]
        for d in root.findall("./model2D3DDistributions/model2D3DDistribution")
    }
    out["vehicle_type_model_refs"] = {
        vt.get("no"): vt.get("model2D3DDistr")
        for vt in root.findall("./vehicleTypes/vehicleType")
    }
    return out


def verdict_models(
    emitted: dict[str, Any], saved: dict[str, Any], err_text: str, com_types: list[dict[str, Any]]
) -> dict[str, Any]:
    """B1: did the install-asset 2D/3D model refs survive the load?"""
    out: dict[str, Any] = {
        "question": "B1 — does a fleet-carrying draft LOAD, and does Vissim resolve the "
        "install-asset 2D/3D model refs (.u3dm/.fbx under #3dmodels#), refuse, or substitute?",
        "emitted_file3D_refs": emitted.get("file3D_refs"),
        "saved_file3D_refs": saved.get("file3D_refs"),
        "emitted_vehicle_type_model_refs": emitted.get("vehicle_type_model_refs"),
        "saved_vehicle_type_model_refs": saved.get("vehicle_type_model_refs"),
        "com_vehicle_type_model_refs": {
            str(t.get("no")): (t.get("att_values") or {}).get("Model2D3DDistr")
            for t in com_types
        },
    }
    # any .err line naming a 3D model / .u3dm / .fbx / a missing file
    lowered = (err_text or "").lower()
    hits = [
        line
        for line in (err_text or "").splitlines()
        if any(tok in line.lower() for tok in (".u3dm", ".fbx", "3dmodel", "3d model", "model", "missing", "not found", "could not"))
    ]
    out["err_lines_mentioning_a_model_or_missing_file"] = hits[:40]

    saved_refs = saved.get("file3D_refs") or []
    emitted_refs = emitted.get("file3D_refs") or []
    saved_models_present = bool(saved.get("models"))

    if not saved_models_present:
        out["verdict"] = (
            "MODELS DROPPED: Vissim's own SaveNetAs wrote NO models2D3D container — the "
            "install-asset model refs did not survive the load. See the .err and the COM "
            "read-back for how it represents the loss."
        )
    elif sorted(saved_refs) == sorted(emitted_refs):
        out["verdict"] = (
            "REFS SURVIVE VERBATIM: every file3D the emitter wrote is present unchanged in "
            "Vissim's own save. Whether the asset FILE resolved on disk is a rendering/runtime "
            "concern; the reference itself was accepted and round-tripped. See the .err — a "
            "resolved load with no model-related .err line is Vissim accepting the ref as-is."
        )
    else:
        out["verdict"] = (
            "REFS REWRITTEN/SUBSTITUTED: the saved file3D set differs from the emitted one — "
            "Vissim changed or replaced a model reference. Compare emitted vs saved above."
        )
    return out


# --- B2: the inverted acceleration envelope --------------------------------


def _accel_point(root: ET.Element, func_no: int, speed_kmh: str) -> dict[str, Any]:
    """The (yMin, y, yMax) of one desAccelerationFunction data point."""
    fn = root.find(f"./desAccelerationFunctions/desAccelerationFunction[@no='{func_no}']")
    if fn is None:
        return {"present": False, "func_no": func_no}
    points = [
        p
        for p in fn.iter("accelerationFunctionDataPoint")
        if p.get("x") == speed_kmh
    ]
    out: dict[str, Any] = {
        "present": True,
        "func_no": func_no,
        "func_name": fn.get("name"),
        "speed_kmh": speed_kmh,
    }
    if points:
        p = points[0]
        out.update({"x": p.get("x"), "yMin": p.get("yMin"), "y": p.get("y"), "yMax": p.get("yMax")})
        out["is_inverted"] = not (
            _le(p.get("yMin"), p.get("y")) and _le(p.get("y"), p.get("yMax"))
        )
    else:
        out["point_found"] = False
    return out


def _le(a: str | None, b: str | None) -> bool:
    try:
        return float(a) <= float(b) + 1e-9
    except (TypeError, ValueError):
        return False


def verdict_acceleration(
    emitted: dict[str, Any], saved: dict[str, Any], com: dict[str, Any]
) -> dict[str, Any]:
    """B2: what did Vissim do with the inverted envelope at 96 km/h?"""
    out: dict[str, Any] = {
        "question": "B2 — does Vissim accept the INVERTED Table 5-2 desired-acceleration "
        f"envelope (type {INVERTED_TYPE_NO}, func {INVERTED_FUNC_NO}, {INVERTED_SPEED_KMH} km/h) "
        "verbatim, clamp it, reorder it, or reject the load?",
        "emitted_point": emitted,
        "saved_point": saved,
        "com_read_back": com,
    }
    if not saved.get("present"):
        out["verdict"] = (
            f"FUNCTION DROPPED: desAccelerationFunction {INVERTED_FUNC_NO} is absent from "
            "Vissim's own save — the load did not keep the heavy type's desired-acceleration "
            "function. See the round-trip table and .err."
        )
        return out
    same = all(
        _numeric_equal(emitted.get(k), saved.get(k)) for k in ("yMin", "y", "yMax")
    )
    if same and saved.get("is_inverted"):
        out["verdict"] = (
            "ACCEPTED VERBATIM, STILL INVERTED: Vissim loaded the inverted envelope and its own "
            f"save reads it back unchanged (yMin={saved.get('yMin')} > y={saved.get('y')} > "
            f"yMax={saved.get('yMax')} at {INVERTED_SPEED_KMH} km/h). Vissim does NOT validate "
            "yMin ≤ y ≤ yMax on load — the house thesis (Vissim fails silently) restated. The "
            "guideline's Table 5-2 anomaly reaches the running model uncorrected; the fix is in "
            "the SOURCE table / its extraction, to raise with the client (NOT a silent swap in "
            "fleet.py, which would be inventing)."
        )
    elif same and not saved.get("is_inverted"):
        out["verdict"] = "ACCEPTED, and the point is not inverted as read back — inspect the values."
    else:
        out["verdict"] = (
            "REWRITTEN: Vissim changed the emitted (yMin, y, yMax) on save — it clamped or "
            "reordered the inverted envelope. Emitted vs saved above; this is Vissim imposing "
            "its own ordering, and names what fleet.py would have to pre-normalise if the "
            "guideline value is to survive unchanged."
        )
    return out


# --- signals / demand re-confirmation --------------------------------------


def _decode_green_windows(sc_el: ET.Element) -> dict[str, dict[str, str]]:
    """Decode a controller's ``intSupplyData`` and return, per signal group,
    the green-begin and red-begin (ms) of program 1 — read straight from the
    file's own bytes."""
    blob = sc_el.get("intSupplyData")
    if not blob:
        return {}
    try:
        text = base64.b64decode(blob).decode("utf-8")
        inner = ET.fromstring(text)
    except Exception:
        return {}
    windows: dict[str, dict[str, str]] = {}
    prog = inner.find("./progs/prog")
    if prog is None:
        return {}
    for sg in prog.findall("./sgs/sg"):
        begins = {cmd.get("display"): cmd.get("begin") for cmd in sg.findall("./cmds/cmd")}
        windows[sg.get("sg_id") or "?"] = {
            "green_begin_ms": begins.get("3"),
            "red_begin_ms": begins.get("1"),
        }
    return windows


def verdict_signals_demand(
    saved: ET.Element, expected: dict[str, Any], census: dict[str, Any]
) -> dict[str, Any]:
    """Re-confirm, in the combined file, that signals green windows, vehicle
    input volumes and composition references all survive and resolve."""
    out: dict[str, Any] = {}

    # --- signals: green windows from the saved blob vs the plan ------------
    sc = saved.find("./signalControllers/signalController")
    windows = _decode_green_windows(sc) if sc is not None else {}
    group_rows = []
    plan_ok = bool(windows) and bool(expected.get("groups"))
    for g in expected.get("groups", []):
        sg_id = str(g["no"])
        w = windows.get(sg_id, {})
        # emitted green begins at green_start_s; red begins at green_start+green+amber
        exp_green_ms = int(round(g["green_start_s"] * 1000))
        exp_red_ms = int(round((g["green_start_s"] + g["green_s"] + g["amber_s"]) * 1000))
        green_ok = w.get("green_begin_ms") is not None and int(w["green_begin_ms"]) == exp_green_ms
        red_ok = w.get("red_begin_ms") is not None and int(w["red_begin_ms"]) == exp_red_ms
        group_rows.append(
            {
                "sg": sg_id,
                "phase": g["phase_id"],
                "expected_green_begin_ms": exp_green_ms,
                "saved_green_begin_ms": w.get("green_begin_ms"),
                "expected_red_begin_ms": exp_red_ms,
                "saved_red_begin_ms": w.get("red_begin_ms"),
                "matches_plan": green_ok and red_ok,
            }
        )
        plan_ok = plan_ok and green_ok and red_ok
    out["signals"] = {
        "green_windows": group_rows,
        "all_match_plan": plan_ok,
        "com_green_splits": census.get("signal_green_splits"),
        "verdict": (
            "SIGNALS SURVIVE: every group's green/red command in the combined file's saved blob "
            "is the plan's own number"
            if plan_ok
            else "SIGNALS MISMATCH or MISSING — see the per-group rows and the COM splits"
        ),
    }

    # --- demand: vehicle inputs carry volumes -----------------------------
    saved_inputs = {
        vi.get("no"): {
            "link": vi.get("link"),
            "volume": (
                vi.find("./timeIntVehVols/timeIntervalVehVolume").get("volume")
                if vi.find("./timeIntVehVols/timeIntervalVehVolume") is not None
                else None
            ),
            "vehComp": (
                vi.find("./timeIntVehVols/timeIntervalVehVolume").get("vehComp")
                if vi.find("./timeIntVehVols/timeIntervalVehVolume") is not None
                else None
            ),
        }
        for vi in saved.findall("./vehicleInputs/vehicleInput")
    }
    input_rows = []
    inputs_ok = bool(expected.get("demand_inputs"))
    for plan_ in expected.get("demand_inputs", []):
        no = str(plan_["no"])
        sv = saved_inputs.get(no, {})
        vol_ok = _numeric_equal(sv.get("volume"), str(plan_["volume_vph"]))
        inputs_ok = inputs_ok and vol_ok
        input_rows.append(
            {
                "no": no,
                "link": sv.get("link"),
                "expected_volume_vph": plan_["volume_vph"],
                "saved_volume": sv.get("volume"),
                "vehComp": sv.get("vehComp"),
                "matches": vol_ok,
            }
        )

    # --- demand: compositions resolve to declared types -------------------
    declared_type_nos = {vt.get("no") for vt in saved.findall("./vehicleTypes/vehicleType")}
    declared_speed_nos = {
        s.get("no") for s in saved.findall("./speedDistributions/speedDistribution")
    }
    referenced = saved.findall(
        "./vehicleCompositions/vehicleComposition/vehCompRelFlows/vehicleCompositionRelativeFlow"
    )
    ref_type_nos = {r.get("vehType") for r in referenced}
    ref_speed_nos = {r.get("desSpeedDistr") for r in referenced}
    dangling_types = sorted(ref_type_nos - declared_type_nos)
    dangling_speeds = sorted(ref_speed_nos - declared_speed_nos)
    out["demand"] = {
        "vehicle_inputs": input_rows,
        "all_input_volumes_match": inputs_ok,
        "composition_referenced_types": sorted(ref_type_nos),
        "declared_vehicle_types": sorted(declared_type_nos),
        "composition_referenced_speed_distr": sorted(ref_speed_nos),
        "declared_speed_distr": sorted(declared_speed_nos),
        "dangling_type_refs": dangling_types,
        "dangling_speed_refs": dangling_speeds,
        "com_vehicle_inputs": census.get("vehicle_inputs"),
        "com_vehicle_composition_nos": census.get("vehicle_composition_nos"),
        "verdict": (
            "DEMAND SURVIVES: vehicle inputs carry their volumes and every composition "
            "reference resolves to a type/speed the SAME file declares (no dangling ref)"
            if inputs_ok and not dangling_types and not dangling_speeds
            else "DEMAND MISMATCH or DANGLING REFERENCE — see the rows"
        ),
    }
    return out


# ---------------------------------------------------------------------------
# CHILD process: the only code in this file that touches COM.  READ ONLY.
# ---------------------------------------------------------------------------


def _snapshot_err_paths(directory: Path) -> set[str]:
    try:
        return {p.name for p in directory.glob("*.err")}
    except OSError:
        return set()


def _read_err(directory: Path, pre_existing: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {"files": [], "new_files": []}
    try:
        paths = sorted(directory.glob("*.err"))
    except OSError as exc:
        out["error"] = str(exc)
        return out
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            text = f"[could not read: {exc}]"
        if len(text) > _MAX_ERR_CHARS:
            text = text[:_MAX_ERR_CHARS] + "\n... [truncated]"
        out["files"].append({"name": path.name, "bytes": path.stat().st_size, "text": text})
        if path.name not in pre_existing:
            out["new_files"].append(path.name)
    return out


def _read_version(vissim: Any) -> dict[str, Any]:
    info: dict[str, Any] = {"att_values": {}}
    for att in _VERSION_CANDIDATES:
        try:
            info["att_values"][att] = str(vissim.AttValue(att))
        except Exception as exc:
            info["att_values"][att] = f"<unavailable: {type(exc).__name__}>"
    return info


def _probe_att_values(obj: Any, candidates: tuple[str, ...]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for attr_id in candidates:
        try:
            found[attr_id] = str(obj.AttValue(attr_id))
        except Exception as exc:
            found[attr_id] = f"<unavailable: {type(exc).__name__}>"
    return found


def _attribute_codes(collection: Any, cap: int = 400) -> dict[str, Any]:
    """The container's real attribute-code table via COM ``Attributes`` — ids
    from Vissim itself, never guessed (used to find any end/model attribute the
    candidate list missed)."""
    out: dict[str, Any] = {}
    try:
        attrs = collection.Attributes
    except Exception as exc:
        return {"error": f"no Attributes: {type(exc).__name__}: {exc}"}
    items = None
    for use_getall in (True, False):
        try:
            items = list(attrs.GetAll()) if use_getall else list(attrs)
            break
        except Exception:
            continue
    if items is None:
        return {"error": "Attributes collection not iterable"}
    codes = []
    for item in items[:cap]:
        try:
            codes.append(str(item.Code))
        except Exception:
            codes.append("?")
    out["count"] = len(items)
    out["codes"] = codes
    return out


def _read_accel_function_com(vissim: Any, func_no: int, speed_kmh: str) -> dict[str, Any]:
    """Best-effort COM read of one desired-acceleration function's data point at
    ``speed_kmh`` km/h. Every access is defensive: the SAVED file is the
    authoritative evidence, this is the second, independent read."""
    out: dict[str, Any] = {"func_no": func_no, "speed_kmh": speed_kmh}
    collection = None
    for accessor in ("DesAccelerationFunctions", "DesAccelFunctions"):
        try:
            collection = getattr(vissim.Net, accessor)
            out["collection"] = accessor
            break
        except Exception as exc:
            out.setdefault("tried", []).append(f"{accessor}: {type(exc).__name__}")
    if collection is None:
        out["error"] = "no desired-acceleration-function collection on Net"
        return out
    try:
        func = collection.ItemByKey(func_no)
    except Exception as exc:
        out["error"] = f"ItemByKey({func_no}): {type(exc).__name__}: {exc}"
        return out
    out["func_att_values"] = _probe_att_values(func, ("No", "Name"))
    # the data-point child collection — probe a few plausible accessors
    points = None
    for accessor in ("AccelFunctionDataPts", "AccelFunctionDataPoints", "DataPts", "FuncData"):
        try:
            points = list(getattr(func, accessor).GetAll())
            out["points_via"] = accessor
            break
        except Exception:
            continue
    if points is None:
        out["error"] = "could not enumerate the function's data points over COM"
        out["point_attribute_codes"] = _attribute_codes(getattr(func, "AccelFunctionDataPts", collection))
        return out
    for point in points:
        probe = _probe_att_values(point, ("X", "Y", "YMin", "YMax"))
        if str(probe.get("X")).split(".")[0] == speed_kmh:
            out["point_at_speed"] = probe
            break
    out["point_count"] = len(points)
    return out


def _fullstack_census(controller: Any) -> dict[str, Any]:
    """READ-ONLY COM census answering B1, B2 and the signals/demand re-confirm."""
    out: dict[str, Any] = {}
    vissim = controller.vissim

    for name, accessor in (
        ("vehicle_types", "VehicleTypes"),
        ("vehicle_inputs", "VehicleInputs"),
        ("vehicle_compositions", "VehicleCompositions"),
        ("speed_distributions", "DesSpeedDistributions"),
        ("signal_controllers", "SignalControllers"),
        ("signal_heads", "SignalHeads"),
        ("detectors", "Detectors"),
        ("links", "Links"),
        ("nodes", "Nodes"),
    ):
        try:
            out[f"{name}_count"] = int(getattr(vissim.Net, accessor).Count)
        except Exception as exc:
            out[f"{name}_count"] = f"<unavailable: {type(exc).__name__}>"

    # -- B1: each vehicle type's identity + model reference, read back ------
    vt_rows: list[dict[str, Any]] = []
    try:
        vt_nos = controller.available_vehicle_type_nos()
    except Exception as exc:
        out["vehicle_type_error"] = f"{type(exc).__name__}: {exc}"
        vt_nos = []
    out["vehicle_type_nos"] = vt_nos
    for no in vt_nos:
        row: dict[str, Any] = {"no": no}
        try:
            item = vissim.Net.VehicleTypes.ItemByKey(no)
            row["att_values"] = _probe_att_values(item, _VT_ATTR_CANDIDATES)
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        # the reference getter the shipped verbs use, as a second read
        try:
            row["model2d3d_distr_ref"] = controller.get_vehicle_type_reference(no, "model2d3d_distr")
            row["des_accel_func_ref"] = controller.get_vehicle_type_reference(no, "des_accel_func")
        except Exception as exc:
            row["reference_error"] = f"{type(exc).__name__}: {exc}"
        vt_rows.append(row)
    out["vehicle_types"] = vt_rows
    if vt_nos:
        try:
            out["vehicle_type_attribute_codes"] = _attribute_codes(vissim.Net.VehicleTypes)
        except Exception as exc:
            out["vehicle_type_attribute_codes"] = {"error": f"{type(exc).__name__}: {exc}"}

    # -- B2: the inverted heavy desired-acceleration function ---------------
    out["accel_function_read_back"] = _read_accel_function_com(
        vissim, INVERTED_FUNC_NO, INVERTED_SPEED_KMH
    )

    # -- signals re-confirmation: green splits per group -------------------
    splits: list[dict[str, Any]] = []
    try:
        sc_nos = controller.available_signal_controller_nos()
    except Exception as exc:
        sc_nos = []
        out["signal_controller_error"] = f"{type(exc).__name__}: {exc}"
    for sc_no in sc_nos:
        try:
            header = controller.get_signal_controller(sc_no)
        except Exception as exc:
            splits.append({"sc": sc_no, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for sg_no in [int(n) for n in (header.get("signal_group_nos") or [])]:
            row: dict[str, Any] = {"sc": sc_no, "sg": sg_no}
            try:
                row["green_split"] = controller.get_signal_green_split(sc_no, sg_no)
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
            splits.append(row)
    out["signal_green_splits"] = splits

    # -- demand re-confirmation: inputs carry volumes ----------------------
    inputs: list[dict[str, Any]] = []
    try:
        for no in controller.available_vehicle_input_nos():
            try:
                inputs.append(controller.get_vehicle_input(no))
            except Exception as exc:
                inputs.append({"no": no, "error": f"{type(exc).__name__}: {exc}"})
    except Exception as exc:
        out["vehicle_input_error"] = f"{type(exc).__name__}: {exc}"
    out["vehicle_inputs"] = inputs
    try:
        out["vehicle_composition_nos"] = controller.available_vehicle_composition_nos()
    except Exception as exc:
        out["vehicle_composition_nos"] = f"<unavailable: {type(exc).__name__}>"

    return out


def _com_phase(net_path: Path, label: str, out_path: Path) -> int:
    """LoadNet -> .err -> census (READ ONLY) -> SaveNetAs -> disconnect. Child
    process only, so the parent can bound it with a wall clock. NO simulation,
    NO COM writes."""
    import pythoncom

    result: dict[str, Any] = {
        "label": label,
        "network": str(net_path),
        "started_utc": _now(),
        "connected": False,
        "load_ok": None,
        "load_error": None,
        "save_ok": None,
        "save_error": None,
    }

    def flush() -> None:
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )

    flush()
    pythoncom.CoInitialize()
    controller = None
    try:
        from services.vissim_com import VissimController

        controller = VissimController()
        t0 = time.time()
        controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        result["connected"] = True
        result["prog_id"] = controller.prog_id
        result["connect_seconds"] = round(time.time() - t0, 2)
        result["version"] = _read_version(controller.vissim)
        flush()

        pre_err = _snapshot_err_paths(net_path.parent)
        result["err_files_before_load"] = sorted(pre_err)

        t0 = time.time()
        try:
            controller.load_network(str(net_path))
            result["load_ok"] = True
        except Exception as exc:
            result["load_ok"] = False
            result["load_error"] = f"{type(exc).__name__}: {exc}"
        result["load_seconds"] = round(time.time() - t0, 2)
        result["err_after_load"] = _read_err(net_path.parent, pre_err)
        flush()

        if result["load_ok"]:
            try:
                result["census"] = _fullstack_census(controller)
            except Exception as exc:
                result["census"] = {"error": f"{type(exc).__name__}: {exc}"}
            flush()

            saved = net_path.with_name(f"{net_path.stem}_saved.inpx")
            t0 = time.time()
            try:
                controller.save_net_as(saved)
                result["save_ok"] = True
                result["saved_path"] = str(saved)
            except Exception as exc:
                result["save_ok"] = False
                result["save_error"] = f"{type(exc).__name__}: {exc}"
            result["save_seconds"] = round(time.time() - t0, 2)
            result["err_after_save"] = _read_err(net_path.parent, pre_err)
        result["finished_utc"] = _now()
        flush()
        return 0
    except Exception as exc:  # infrastructure failure (COM unavailable, etc.)
        result["fatal_error"] = f"{type(exc).__name__}: {exc}"
        result["finished_utc"] = _now()
        flush()
        return 3
    finally:
        try:
            if controller is not None:
                controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        flush()


def _com_load_many(plan_path: Path, out_path: Path) -> int:
    """The bisect: ``LoadNet`` every file named in ``plan_path`` in ONE session,
    recording ok/error and any ``.err`` per variant. Loads only — nothing saved,
    nothing edited."""
    import pythoncom

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    result: dict[str, Any] = {"started_utc": _now(), "connected": False, "variants": []}

    def flush() -> None:
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )

    flush()
    pythoncom.CoInitialize()
    controller = None
    try:
        from services.vissim_com import VissimController

        controller = VissimController()
        controller.connect(visible=False, launch_if_missing=True, attach_to_running=False)
        result["connected"] = True
        result["prog_id"] = controller.prog_id
        result["version"] = _read_version(controller.vissim)
        flush()

        for item in plan:
            path = Path(item["path"])
            entry: dict[str, Any] = {"label": item["label"], "path": str(path), "note": item.get("note")}
            pre = _snapshot_err_paths(path.parent)
            t0 = time.time()
            try:
                controller.load_network(str(path))
                entry["load_ok"] = True
            except Exception as exc:
                entry["load_ok"] = False
                entry["load_error"] = f"{type(exc).__name__}: {exc}"
            entry["load_seconds"] = round(time.time() - t0, 2)
            err = _read_err(path.parent, pre)
            entry["err_new_files"] = err.get("new_files")
            entry["err_text"] = (
                "\n".join(
                    f["text"]
                    for f in err.get("files", [])
                    if f["name"] in (err.get("new_files") or [])
                )
                or None
            )
            if entry["load_ok"]:
                for name, accessor in (
                    ("link_count", "Links"),
                    ("vehicle_type_count", "VehicleTypes"),
                    ("vehicle_input_count", "VehicleInputs"),
                    ("signal_controller_count", "SignalControllers"),
                ):
                    try:
                        entry[name] = int(getattr(controller.vissim.Net, accessor).Count)
                    except Exception as exc:
                        entry[name] = f"<unavailable: {type(exc).__name__}>"
            result["variants"].append(entry)
            flush()
        result["finished_utc"] = _now()
        flush()
        return 0
    except Exception as exc:
        result["fatal_error"] = f"{type(exc).__name__}: {exc}"
        result["finished_utc"] = _now()
        flush()
        return 3
    finally:
        try:
            if controller is not None:
                controller.disconnect()
        except Exception:
            pass
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass
        flush()


# ---------------------------------------------------------------------------
# PARENT: process bounding + orphan-Vissim cleanup
# ---------------------------------------------------------------------------


def _vissim_pids() -> set[int]:
    """PIDs of every running process whose image name starts with "vissim"
    (filtered in python so it behaves identically under cmd/PowerShell/Git
    Bash)."""
    try:
        proc = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    pids: set[int] = set()
    for line in proc.stdout.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2:
            continue
        if parts[0].strip('"').lower().startswith("vissim"):
            try:
                pids.add(int(parts[1]))
            except ValueError:
                continue
    return pids


def _kill_new_vissim(before: set[int]) -> list[int]:
    """Kill only Vissim processes that did NOT exist before we started — a human's
    open GUI (a PID present beforehand) is never touched."""
    killed: list[int] = []
    for pid in sorted(_vissim_pids() - before):
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True, timeout=60
            )
            killed.append(pid)
        except (OSError, subprocess.SubprocessError):
            continue
    return killed


def _bounded_child(cmd_tail: list[str], out_path: Path, timeout_s: float) -> dict[str, Any]:
    """Run the COM work in a child process with a wall clock, and guarantee no
    Vissim of ours outlives it."""
    if out_path.exists():
        out_path.unlink()
    before = _vissim_pids()
    _log(f"  vissim.exe PIDs already running (never touched): {sorted(before) or 'none'}")
    cmd = [sys.executable, str(Path(__file__).resolve()), *cmd_tail]
    _log(f"  child: {' '.join(cmd)}")
    _log(f"  timeout: {timeout_s:.0f}s")
    started = time.time()
    timed_out = False
    stdout = stderr = ""
    returncode: int | None = None
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s, cwd=str(BACKEND_ROOT)
        )
        returncode = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        _log(f"  !! TIMEOUT after {timeout_s:.0f}s — killing the child and any Vissim it started")
    elapsed = round(time.time() - started, 2)

    killed = _kill_new_vissim(before)
    if killed:
        _log(f"  killed leftover Vissim PIDs (ours only): {killed}")
    leftover = sorted(_vissim_pids() - before)
    if leftover:
        _log(f"  !! WARNING: Vissim PIDs still alive after cleanup: {leftover}")
    else:
        _log("  orphan check: zero Vissim PIDs of ours left behind")

    child_result: dict[str, Any] = {}
    if out_path.exists():
        try:
            child_result = json.loads(out_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            child_result = {"child_json_unreadable": str(exc)}

    return {
        "timed_out": timed_out,
        "elapsed_seconds": elapsed,
        "returncode": returncode,
        "child_stdout_tail": stdout[-4000:],
        "child_stderr_tail": stderr[-4000:],
        "vissim_pids_before": sorted(before),
        "orphan_vissim_pids_after_cleanup": leftover,
        "killed_pids": killed,
        "com": child_result,
    }


# ---------------------------------------------------------------------------
# Phases
# ---------------------------------------------------------------------------


def _fresh_dir(path: Path) -> Path:
    import shutil

    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_build(label: str) -> dict[str, Any]:
    _log("")
    _log("=" * 78)
    _log(f"BUILD — regenerating the FULL-STACK crossroads through services.genesis  [{label}]")
    _log("=" * 78)
    target = _fresh_dir(NETS_ROOT / label)
    built = build_fullstack_network(target)
    _log(f"  draft            : {built['draft']}")
    _log(f"  A.2 plan is 1.6's: {built['plan_check']['is_the_a2_composite']}  "
         f"cycle={built['plan_check']['cycle_s']} greens={built['plan_check']['greens_s']}")
    _log(f"  signals ok       : {built['signals_ok']}  codes={built['signals_issue_codes']}")
    _log(f"  fleet ok         : {built['fleet_ok']}  types={built['fleet_types']}  "
         f"warn={built['fleet_warn_codes']}")
    _log(f"  demand ok        : {built['demand_ok']}  inputs={built['demand_input_count']} "
         f"comps={built['demand_composition_count']}  codes={built['demand_issue_codes']}")
    _log(f"  full .inpx       : {built['full_inpx']}  ({built['full_bytes']} bytes)")
    _log(f"  B1 emitted file3D refs: {built['b1_models_emitted'].get('file3D_refs')}")
    _log(f"  B2 emitted accel @96 (func {INVERTED_FUNC_NO}): {built['b2_accel_point_emitted']}")
    _STATE.setdefault("builds", {})[label] = built
    _save_state()
    return built


def run_live(label: str, timeout_s: float) -> dict[str, Any]:
    build = (_STATE.get("builds") or {}).get(label)
    if not build:
        build = run_build(label)
    full = Path(build["full_inpx"])
    expected = build["expected"]

    _log("")
    _log("=" * 78)
    _log(f"LIVE — LoadNet / .err / READ-ONLY census / SaveNetAs  [{label}]")
    _log("=" * 78)
    _log(f"  network: {full}")

    out_path = full.parent / f"{label}_com.json"
    run = _bounded_child(
        ["--com-network", str(full), "--com-label", label, "--com-out", str(out_path)],
        out_path,
        timeout_s,
    )
    com = run.get("com", {})
    _log(f"  connected  : {com.get('connected')}  prog_id={com.get('prog_id')}")
    _log(f"  version    : {(com.get('version') or {}).get('att_values')}")
    _log(f"  load_ok    : {com.get('load_ok')}  ({com.get('load_seconds')}s)")
    if com.get("load_error"):
        _log(f"  load_error : {com['load_error']}")
    err = com.get("err_after_load") or {}
    _log(f"  .err new   : {err.get('new_files')}")
    err_text_all = "\n".join(f["text"] for f in err.get("files", []))
    for f in err.get("files", []):
        if f["name"] in (err.get("new_files") or []):
            _log(f"    --- {f['name']} ({f['bytes']} bytes) ---")
            for line in f["text"].splitlines():
                _log(f"    | {line}")
    _log(f"  save_ok    : {com.get('save_ok')}  -> {com.get('saved_path')}")

    entry: dict[str, Any] = {"label": label, "build": build, "run": run, "finished_utc": _now()}
    census = com.get("census") or {}
    if census:
        _log(f"  COM counts : vehTypes={census.get('vehicle_types_count')} "
             f"vehInputs={census.get('vehicle_inputs_count')} "
             f"comps={census.get('vehicle_compositions_count')} "
             f"SCs={census.get('signal_controllers_count')} "
             f"heads={census.get('signal_heads_count')} dets={census.get('detectors_count')}")

    saved_path = com.get("saved_path")
    if saved_path and Path(saved_path).exists():
        emitted_root = _parse(full)
        saved_root = _parse(Path(saved_path))

        entry["container_round_trip"] = _container_round_trip(emitted_root, saved_root)

        b1_saved = _models_index(saved_root)
        entry["b1_models"] = verdict_models(
            build["b1_models_emitted"], b1_saved, err_text_all, census.get("vehicle_types") or []
        )
        b2_saved = _accel_point(saved_root, INVERTED_FUNC_NO, INVERTED_SPEED_KMH)
        entry["b2_acceleration"] = verdict_acceleration(
            build["b2_accel_point_emitted"], b2_saved, census.get("accel_function_read_back") or {}
        )
        entry["reconfirm"] = verdict_signals_demand(saved_root, expected, census)

        _log("")
        _log(f"  B1 VERDICT : {entry['b1_models']['verdict']}")
        _log(f"     emitted file3D: {entry['b1_models']['emitted_file3D_refs']}")
        _log(f"     saved   file3D: {entry['b1_models']['saved_file3D_refs']}")
        _log(f"     .err model/missing lines: {entry['b1_models']['err_lines_mentioning_a_model_or_missing_file']}")
        _log(f"  B2 VERDICT : {entry['b2_acceleration']['verdict']}")
        _log(f"     emitted @96: {build['b2_accel_point_emitted']}")
        _log(f"     saved   @96: {b2_saved}")
        _log(f"     COM read  : {census.get('accel_function_read_back')}")
        _log(f"  SIGNALS    : {entry['reconfirm']['signals']['verdict']}")
        _log(f"  DEMAND     : {entry['reconfirm']['demand']['verdict']}")
        _log("")
        _log("  ROUND-TRIP CONTAINER TABLE (emitted -> saved):")
        for key, _c, _e in _FAMILIES:
            r = entry["container_round_trip"][key]
            flag = "" if (r["emitted_count"] == r["saved_count"] and not r["ids_missing_in_saved"]
                          and not r["ids_added_in_saved"] and not r["per_object_with_changes"]) else "   <-- CHANGED"
            _log(f"    {key:<22} {r['emitted_count']:>3} -> {r['saved_count']:<3}"
                 f"  missing={r['ids_missing_in_saved']} added={r['ids_added_in_saved']}"
                 f" changed={sorted(r['per_object_with_changes'])}{flag}")
    elif com.get("load_ok") is False:
        _log("")
        _log("  LoadNet REFUSED — run --only bisect to locate the layer Vissim rejects.")

    _log(f"  orphan Vissim PIDs after cleanup: {run.get('orphan_vissim_pids_after_cleanup')}")
    _STATE.setdefault("runs", {})[label] = entry
    _save_state()
    return entry


def _write_tree(root: ET.Element, path: Path) -> Path:
    ET.indent(root, space="  ")
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode", xml_declaration=False)
        + "\n",
        encoding="utf-8",
        newline="",
    )
    return path


def _drop_containers(root: ET.Element, tags: tuple[str, ...]) -> None:
    for child in list(root):
        if child.tag in tags:
            root.remove(child)


def run_diagnose(label: str, timeout_s: float) -> dict[str, Any]:
    """Fleet-internal container strip: which fleet container does Vissim reject?

    Only reached when the LAYER bisect located the fleet layer. Builds a ladder
    off the geometry+fleet file (already known to refuse), dropping fleet
    containers one group at a time — vehicleTypes first, then the model closure,
    then the rest — each LoadNet-ed in one session (loads only). The rung that
    flips from refuse to load names the container whose emission Vissim rejects.
    A dropped ``vehicleTypes`` leaves the distributions unreferenced, which
    Vissim allows, so each rung is a well-formed file."""
    build = (_STATE.get("builds") or {}).get(label)
    if not build:
        build = run_build(label)
    _log("")
    _log("=" * 78)
    _log("DIAGNOSE — stripping fleet containers off the geometry+fleet file")
    _log("=" * 78)
    target = _fresh_dir(NETS_ROOT / f"{label}_diagnose")
    geom_fleet = Path(build["geometry_fleet_inpx"])

    #: each rung DROPS the listed containers from geometry+fleet.
    ladder = [
        ("d0_geometry_fleet_as_is", (), "control: geometry + fleet, unmodified (expected refuse)"),
        ("d1_drop_vehicleTypes", ("vehicleTypes",),
         "vehicleTypes removed; all distributions/functions/models kept (now unreferenced)"),
        ("d2_drop_types_and_models",
         ("vehicleTypes", "models2D3D", "model2D3DDistributions"),
         "vehicleTypes + the 2D/3D model closure removed"),
        ("d3_drop_types_models_accel",
         ("vehicleTypes", "models2D3D", "model2D3DDistributions",
          "desAccelerationFunctions", "maxAccelerationFunctions",
          "desDecelerationFunctions", "maxDecelerationFunctions"),
         "also the accel/decel function families removed"),
        ("d4_geometry_only_equiv",
         ("vehicleTypes", "models2D3D", "model2D3DDistributions",
          "desAccelerationFunctions", "maxAccelerationFunctions",
          "desDecelerationFunctions", "maxDecelerationFunctions",
          "colorDistributions", "speedDistributions"),
         "every fleet container removed (should equal geometry-only: loads)"),
    ]
    variants = []
    for name, drop, note in ladder:
        root = _parse(geom_fleet)
        _drop_containers(root, drop)
        path = _write_tree(root, target / f"{name}.inpx")
        variants.append({"label": name, "path": str(path), "note": note})
        _log(f"  {name:<28} {note}")

    plan_path = target / "plan.json"
    plan_path.write_text(json.dumps(variants, indent=2, sort_keys=True), encoding="utf-8")
    out_path = target / "diagnose_com.json"
    run = _bounded_child(
        ["--com-plan", str(plan_path), "--com-out", str(out_path)], out_path, timeout_s
    )
    com = run.get("com", {})
    _log("")
    _log(f"  connected: {com.get('connected')}")
    for e in com.get("variants", []):
        _log(f"  {e['label']:<28} load_ok={e.get('load_ok')} links={e.get('link_count')} "
             f"vehTypes={e.get('vehicle_type_count')} err={e.get('err_new_files')}")
        if e.get("load_error"):
            _log(f"      error: {e['load_error']}")
        if e.get("err_text"):
            for line in e["err_text"].splitlines():
                _log(f"      | {line}")
    _log(f"  orphan Vissim PIDs after cleanup: {run.get('orphan_vissim_pids_after_cleanup')}")
    result = {"variants": variants, "run": run, "finished_utc": _now()}
    _STATE.setdefault("diagnoses", {})[label] = result
    _save_state()
    return result


def run_bisect(label: str, timeout_s: float) -> dict[str, Any]:
    build = (_STATE.get("builds") or {}).get(label)
    if not build:
        build = run_build(label)
    _log("")
    _log("=" * 78)
    _log("BISECT — grafting the four layers onto the known-loadable geometry skeleton")
    _log("=" * 78)
    variants = [
        {"label": "v1_geometry_only", "path": build["geometry_only_inpx"],
         "note": "the known-loadable skeleton (genesis_open_live_check small_v2)"},
        {"label": "v2_geometry_fleet", "path": build["geometry_fleet_inpx"],
         "note": "geometry + FLEET (vehicleTypes, models2D3D, accel/decel, colours, speeds)"},
        {"label": "v3_geometry_fleet_demand", "path": build["geometry_fleet_demand_inpx"],
         "note": "geometry + fleet + DEMAND (inputs, compositions, routing)"},
        {"label": "v4_full", "path": build["full_inpx"],
         "note": "all four layers — the file under test"},
    ]
    target = _fresh_dir(NETS_ROOT / f"{label}_bisect")
    plan_path = target / "plan.json"
    plan_path.write_text(json.dumps(variants, indent=2, sort_keys=True), encoding="utf-8")
    for v in variants:
        _log(f"  {v['label']:<28} {v['note']}")

    out_path = target / "bisect_com.json"
    run = _bounded_child(
        ["--com-plan", str(plan_path), "--com-out", str(out_path)], out_path, timeout_s
    )
    com = run.get("com", {})
    _log("")
    _log(f"  connected: {com.get('connected')}")
    for e in com.get("variants", []):
        _log(f"  {e['label']:<28} load_ok={e.get('load_ok')} links={e.get('link_count')} "
             f"vehTypes={e.get('vehicle_type_count')} vehInputs={e.get('vehicle_input_count')} "
             f"SCs={e.get('signal_controller_count')} err={e.get('err_new_files')}")
        if e.get("load_error"):
            _log(f"      error: {e['load_error']}")
        if e.get("err_text"):
            for line in e["err_text"].splitlines():
                _log(f"      | {line}")
    _log(f"  orphan Vissim PIDs after cleanup: {run.get('orphan_vissim_pids_after_cleanup')}")
    result = {"variants": variants, "run": run, "finished_utc": _now()}
    _STATE.setdefault("bisects", {})[label] = result
    _save_state()
    return result


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--only",
        choices=("build", "live", "bisect", "diagnose", "all"),
        default="all",
        help="which phase to run (default: all = build + live)",
    )
    parser.add_argument("--label", default="fullstack", help="run label / nets subdirectory")
    parser.add_argument("--timeout", type=float, default=DEFAULT_LIVE_TIMEOUT_S)
    parser.add_argument("--bisect-timeout", type=float, default=DEFAULT_BISECT_TIMEOUT_S)
    # child-process hooks (not for interactive use)
    parser.add_argument("--com-network")
    parser.add_argument("--com-label")
    parser.add_argument("--com-out")
    parser.add_argument("--com-plan")
    args = parser.parse_args(argv)

    if args.com_network and args.com_out:
        return _com_phase(Path(args.com_network), args.com_label or "child", Path(args.com_out))
    if args.com_plan and args.com_out:
        return _com_load_many(Path(args.com_plan), Path(args.com_out))

    WORK_ROOT.mkdir(parents=True, exist_ok=True)
    NETS_ROOT.mkdir(parents=True, exist_ok=True)
    _load_state()
    _STATE["probe"] = "genesis_fullstack_live_check"
    _STATE["last_run_utc"] = _now()
    _STATE["python"] = sys.version
    _STATE["work_root"] = str(WORK_ROOT)
    _save_state()

    _log("")
    _log("#" * 78)
    _log(f"# genesis full-stack live check — {_now()} — only={args.only} label={args.label}")
    _log("#" * 78)

    started = time.time()
    if args.only in ("build", "all"):
        run_build(args.label)
    if args.only in ("live", "all"):
        run_live(args.label, args.timeout)
    if args.only == "bisect":
        run_bisect(args.label, args.bisect_timeout)
    if args.only == "diagnose":
        run_diagnose(args.label, args.bisect_timeout)
    _STATE["wall_clock_seconds"] = round(time.time() - started, 2)
    _save_state()
    _log("")
    _log(f"wall clock: {_STATE['wall_clock_seconds']}s   state: {STATE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

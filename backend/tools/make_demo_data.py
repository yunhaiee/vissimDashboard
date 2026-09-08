"""Generate synthetic demo data so the dashboard runs without Vissim.

Builds into the repo-root ``data/`` directory (git-ignored):
  - practice.inpx           two-intersection arterial network
  - practice.vissimpdb      SCENARIO table with three scenarios
  - Scenarios/S00000N/S00000N.results/run_00K.db   3 seed-runs per scenario
  - real_counts.xlsx        observed counts + travel times (the OBSERVED side)

The result databases match the schema ``services/results_reader.py`` reads:
movement OBJECT_IDs use the ``node-fromlink-pos-tolink-pos`` format so
directional breakdowns resolve against the .inpx geometry.

Usage:  python -m tools.make_demo_data  [--out DIR]     (from ``backend/``)
Run it with the VENV interpreter and as ``-m``, not as a path: a path run puts
``backend/tools/`` on ``sys.path`` rather than ``backend/``, so the
``from services.workspaces import ...`` below raises ModuleNotFoundError, and
the workbook step needs ``openpyxl`` from the venv.
Deterministic — same output every run (fixed RNG seed per scenario/run, and a
byte-stable workbook; see :func:`_stable_xlsx_bytes`).

WHAT A DEFAULT-``--out`` RUN CHANGES ABOUT THE RUNNING PRODUCT
--------------------------------------------------------------
Regenerating into the default directory is not a no-op on an existing demo
install, even though every byte it writes is reproducible:

* it MATERIALISES ``data/real_counts.xlsx`` at exactly the path
  ``config.REAL_COUNTS_EXCEL`` points at — the fallback ``services.datasets``
  uses when no workbook has been uploaded through the dashboard. The demo
  dashboard's validation surface therefore flips from "workbook not found" to
  "observed counts present", and the agent's ``run_reality_check`` starts
  returning REAL proposals (and refusals) on demo data instead of declining
  for want of an observed side. That is the intent of unit U4a — but it is a
  visible change of behaviour on a machine somebody is demoing from, so it is
  stated here rather than discovered;
* it ``rmtree``s ``data/Scenarios`` before rebuilding it (pre-existing
  behaviour, unchanged). The rebuild is byte-identical, so nothing is lost
  that this script wrote — but anything a HUMAN put in that tree is deleted.

Pass ``--out`` to build somewhere else; nothing outside that directory is
touched.
"""
from __future__ import annotations

import argparse
import datetime
import random
import re
import shutil
import sqlite3
import zipfile
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------- network ---
# Node 1 "Central & Main" at (0,0); Node 2 "Central & Park" at (400,0).
# Approach/exit links are one-way, Vissim-style. Direction classification in
# results_reader uses the link endpoint farthest from the node centre.
LINKS = [
    # (no, name, points, lanes)
    (101, "Main St EB approach", [(-300, 0), (-20, 0)], 3),
    (102, "Main St WB exit", [(-20, 0), (-300, 0)], 3),
    (103, "1st Ave SB approach", [(0, 300), (0, 20)], 2),
    (104, "1st Ave NB exit", [(0, 20), (0, 300)], 2),
    (105, "1st Ave NB approach", [(0, -300), (0, -20)], 2),
    (106, "1st Ave SB exit", [(0, -20), (0, -300)], 2),
    (140, "Main St EB arterial", [(20, 0), (380, 0)], 3),
    (141, "Main St WB arterial", [(380, 0), (20, 0)], 3),
    (203, "Park Ave SB approach", [(400, 300), (400, 20)], 2),
    (204, "Park Ave NB exit", [(400, 20), (400, 300)], 2),
    (205, "Park Ave NB approach", [(400, -300), (400, -20)], 2),
    (206, "Park Ave SB exit", [(400, -20), (400, -300)], 2),
    (207, "Main St WB approach", [(700, 0), (420, 0)], 3),
    (208, "Main St EB exit", [(420, 0), (700, 0)], 3),
]

NODES = [
    (1, "Central & Main", [(-15, -15), (15, -15), (15, 15), (-15, 15)]),
    (2, "Central & Park", [(385, -15), (415, -15), (415, 15), (385, 15)]),
]

# Connector links (Phase 3): Vissim represents intersection-internal geometry
# as <link> elements carrying fromLinkEndPt/toLinkEndPt children instead of a
# lanes-only body. Each bridges a real "from" link's downstream end to a real
# "to" link's upstream end via the lane="linkNo laneNo" schema, chosen to
# mirror an actual MOVEMENTS entry at that node so the topology is plausible:
#   - 301 mirrors MOVEMENTS (1, 101, 140, ...) — Node 1's EB through movement.
#   - 302 mirrors MOVEMENTS (2, 140, 208, ...) — Node 2's EB through movement.
# from_pos/to_pos are the arc-length position on the referenced link where the
# connector attaches (end of the upstream link, start of the downstream one).
CONNECTORS = [
    # (no, name, points, lanes, from_link, from_lane, from_pos, to_link, to_lane, to_pos)
    (301, "Central & Main EB through connector", [(-20, 0), (20, 0)], 3, 101, 1, 280.0, 140, 1, 0.0),
    (302, "Central & Park EB through connector", [(380, 0), (420, 0)], 3, 140, 1, 360.0, 208, 1, 0.0),
]

# (node, from_link, to_link, base hourly volume, base movement delay s)
MOVEMENTS = [
    (1, 101, 140, 780, 28.0),  # W in, through
    (1, 101, 104, 120, 41.0),  # W in, left
    (1, 101, 106, 90, 14.0),   # W in, right
    (1, 141, 102, 700, 26.0),  # E in, through
    (1, 141, 104, 80, 15.0),   # E in, right
    (1, 141, 106, 110, 43.0),  # E in, left
    (1, 103, 106, 240, 33.0),  # N in, through
    (1, 103, 140, 140, 47.0),  # N in, left
    (1, 103, 102, 60, 16.0),   # N in, right
    (1, 105, 104, 220, 34.0),  # S in, through
    (1, 105, 102, 130, 46.0),  # S in, left
    (1, 105, 140, 70, 15.0),   # S in, right
    (2, 140, 208, 750, 27.0),
    (2, 140, 204, 110, 42.0),
    (2, 140, 206, 80, 13.0),
    (2, 207, 141, 680, 25.0),
    (2, 207, 204, 90, 14.0),
    (2, 207, 206, 100, 44.0),
    (2, 203, 206, 200, 32.0),
    (2, 203, 208, 120, 45.0),
    (2, 203, 141, 50, 15.0),
    (2, 205, 204, 210, 35.0),
    (2, 205, 141, 110, 48.0),
    (2, 205, 208, 60, 16.0),
]

TRAVELTIME_MEASUREMENTS = [
    # (no, name, distance m, base travel time s per interval)
    (1, "Main St EB", 1000.0, [95.0, 118.0, 142.0, 110.0]),
    (2, "Main St WB", 1000.0, [88.0, 102.0, 131.0, 99.0]),
]
INTERVALS = ["0-900", "900-1800", "1800-2700", "2700-3600"]

# scenario number -> (name, volume factor, delay factor)
SCENARIOS = {
    1: ("Base (AM Peak)", 1.00, 1.00),
    2: ("Signal Optimization", 1.00, 0.74),
    3: ("Demand +20%", 1.20, 1.48),
}
RUNS_PER_SCENARIO = 3

BASE_NETWORK = {
    "DELAYAVG": 32.4,
    "STOPSAVG": 1.6,
    "SPEEDAVG": 38.2,
    "DELAYSTOPAVG": 21.0,
    "DISTTOT": 6_480_000.0,
    "TRAVTMTOT": 52_000.0,
    "DELAYTOT": 19_400.0,
    "VEHACT": 310.0,
    "VEHARR": 5_050.0,
}
# Speed moves opposite to delay; totals scale with volume.
NETWORK_DELAY_KEYS = {"DELAYAVG", "STOPSAVG", "DELAYSTOPAVG", "DELAYTOT", "TRAVTMTOT"}
NETWORK_VOLUME_KEYS = {"DISTTOT", "VEHACT", "VEHARR", "DELAYTOT", "TRAVTMTOT"}


def build_inpx(out: Path) -> None:
    def pts(points, tag_container, tag_point):
        rows = "".join(
            f'<{tag_point} x="{x}" y="{y}" zOffset="0"/>' for x, y in points
        )
        return f"<{tag_container}>{rows}</{tag_container}>"

    link_xml = []
    for no, name, points, lanes in LINKS:
        lanes_xml = "".join('<lane width="3.5"/>' for _ in range(lanes))
        link_xml.append(
            f'<link no="{no}" name="{escape(name)}">'
            f"<geometry>{pts(points, 'linkPolyPts', 'linkPolyPoint')}</geometry>"
            f"<lanes>{lanes_xml}</lanes>"
            f"</link>"
        )

    for no, name, points, lanes, from_link, from_lane, from_pos, to_link, to_lane, to_pos in CONNECTORS:
        lanes_xml = "".join('<lane width="3.5"/>' for _ in range(lanes))
        link_xml.append(
            f'<link no="{no}" name="{escape(name)}">'
            f"<geometry>{pts(points, 'linkPolyPts', 'linkPolyPoint')}</geometry>"
            f"<lanes>{lanes_xml}</lanes>"
            f'<fromLinkEndPt lane="{from_link} {from_lane}" pos="{from_pos}"/>'
            f'<toLinkEndPt lane="{to_link} {to_lane}" pos="{to_pos}"/>'
            f"</link>"
        )

    node_xml = []
    for no, name, polygon in NODES:
        node_xml.append(
            f'<node no="{no}" name="{escape(name)}">'
            f"<geometry>{pts(polygon, 'points', 'point')}</geometry>"
            f"</node>"
        )

    tt_xml = "".join(
        f'<vehicleTravelTimeMeasurement no="{no}" name="{escape(name)}"/>'
        for no, name, _, _ in TRAVELTIME_MEASUREMENTS
    )

    # Routing decisions on the two main approaches (read by the assistant's
    # node-routing context; relFlow format is "vehComp time:value").
    rd_xml = (
        '<vehicleRoutingDecisionsStatic>'
        '<vehicleRoutingDecisionStatic no="1" name="EB at Central&amp;Main" link="101">'
        '<vehicleRouteStatic no="1" name="TH" destLink="140" relFlow="1 0:780"/>'
        '<vehicleRouteStatic no="2" name="LT" destLink="104" relFlow="1 0:120"/>'
        '<vehicleRouteStatic no="3" name="RT" destLink="106" relFlow="1 0:90"/>'
        "</vehicleRoutingDecisionStatic>"
        '<vehicleRoutingDecisionStatic no="2" name="WB at Central&amp;Park" link="207">'
        '<vehicleRouteStatic no="1" name="TH" destLink="141" relFlow="1 0:680"/>'
        '<vehicleRouteStatic no="2" name="RT" destLink="204" relFlow="1 0:90"/>'
        '<vehicleRouteStatic no="3" name="LT" destLink="206" relFlow="1 0:100"/>'
        "</vehicleRoutingDecisionStatic>"
        "</vehicleRoutingDecisionsStatic>"
    )

    vi_xml = (
        "<vehicleInputs>"
        '<vehicleInput no="1" name="Main St EB" link="101"/>'
        '<vehicleInput no="2" name="Main St WB" link="207"/>'
        '<vehicleInput no="3" name="1st Ave SB" link="103"/>'
        '<vehicleInput no="4" name="1st Ave NB" link="105"/>'
        '<vehicleInput no="5" name="Park Ave SB" link="203"/>'
        '<vehicleInput no="6" name="Park Ave NB" link="205"/>'
        "</vehicleInputs>"
    )

    sc_xml = (
        "<signalControllers>"
        '<signalController no="1" name="SC1 Central &amp; Main"/>'
        '<signalController no="2" name="SC2 Central &amp; Park"/>'
        "</signalControllers>"
    )

    # Minimal georeference so the Leaflet geojson endpoint doesn't 500.
    # (The hardcoded Helmert constants are fitted to the SouthSt Perth demo
    # network; placement of this synthetic net on the basemap is arbitrary.)
    net_para = (
        '<netPara northDir="0">'
        '<refPointMap x="115.8360" y="-32.0670"/>'
        '<refPointNet x="0" y="0"/>'
        "</netPara>"
    )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<network version="2400">'
        f"{net_para}"
        f"<links>{''.join(link_xml)}</links>"
        f"<nodes>{''.join(node_xml)}</nodes>"
        f"<vehicleTravelTimeMeasurements>{tt_xml}</vehicleTravelTimeMeasurements>"
        f"{rd_xml}{vi_xml}{sc_xml}"
        "</network>"
    )
    (out / "practice.inpx").write_text(xml, encoding="utf-8")


def build_project_db(out: Path) -> None:
    db_path = out / "practice.vissimpdb"
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE SCENARIO (NO INTEGER, NAME TEXT, MODIFICATIONS TEXT)")
        for number, (name, _, _) in SCENARIOS.items():
            conn.execute("INSERT INTO SCENARIO VALUES (?, ?, ?)", (number, name, ""))
        conn.commit()
    finally:
        conn.close()


def build_result_db(
    db_path: Path, volume_factor: float, delay_factor: float, rng: random.Random
) -> dict[str, dict[str, object]]:
    """Write one result .db and RETURN what it wrote.

    The return value is unit U4a's: ``{"movements": {OBJECT_ID: {vehs, qlen,
    qlenmax}}, "travel_times": {measurement no: {vehs, travtm, dist}}}``.
    :func:`build_counts_workbook` derives the OBSERVED counts from exactly
    these numbers, so the fixture's two sides cannot drift apart — nothing is
    re-derived, re-jittered or re-seeded there.

    Purely additive: the RNG call sequence below is untouched, so every byte
    this function writes is identical to what it wrote before U4a.
    """

    def jitter() -> float:
        return rng.uniform(0.965, 1.035)

    movements_written: dict[str, dict[str, object]] = {}
    travel_written: dict[str, dict[str, object]] = {}

    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE VEHICLENETWORKPERFORMANCEMEASUREMENT_EvaluationTimeIntervalClass ("
            "ARG_VEHICLECLASS TEXT, DELAYAVG REAL, STOPSAVG REAL, SPEEDAVG REAL,"
            "DELAYSTOPAVG REAL, DISTTOT REAL, TRAVTMTOT REAL, DELAYTOT REAL,"
            "VEHACT REAL, VEHARR REAL)"
        )
        network = {}
        for key, base in BASE_NETWORK.items():
            factor = 1.0
            if key in NETWORK_DELAY_KEYS:
                factor *= delay_factor
            if key in NETWORK_VOLUME_KEYS:
                factor *= volume_factor
            if key == "SPEEDAVG":
                factor = 1.0 / delay_factor
            network[key] = round(base * factor * jitter(), 2)
        cur.execute(
            "INSERT INTO VEHICLENETWORKPERFORMANCEMEASUREMENT_EvaluationTimeIntervalClass "
            "VALUES ('ALL', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                network["DELAYAVG"], network["STOPSAVG"], network["SPEEDAVG"],
                network["DELAYSTOPAVG"], network["DISTTOT"], network["TRAVTMTOT"],
                network["DELAYTOT"], network["VEHACT"], network["VEHARR"],
            ),
        )

        cur.execute(
            "CREATE TABLE MOVEMENT_EvaluationTimeIntervalClass ("
            "OBJECT_ID TEXT, ARG_EVALUATIONTIMEINTERVAL TEXT, ARG_VEHICLECLASS TEXT,"
            "VEHS REAL, VEHDELAY REAL, STOPDELAY REAL, STOPS REAL)"
        )
        cur.execute(
            "CREATE TABLE MOVEMENT_EvaluationTimeInterval ("
            "OBJECT_ID TEXT, QLEN REAL, QLENMAX REAL, SUMDELAYS REAL, SUMSTOPS REAL)"
        )
        for node, from_link, to_link, volume, delay in MOVEMENTS:
            object_id = f"{node}-{from_link}-250.0-{to_link}-5.0"
            vehs = round(volume * volume_factor * jitter(), 1)
            veh_delay = round(delay * delay_factor * jitter(), 2)
            stop_delay = round(veh_delay * 0.62, 2)
            stops = round(min(4.0, 0.4 + veh_delay / 30.0), 2)
            cur.execute(
                "INSERT INTO MOVEMENT_EvaluationTimeIntervalClass VALUES (?, '0-3600', 'ALL', ?, ?, ?, ?)",
                (object_id, vehs, veh_delay, stop_delay, stops),
            )
            qlen = round(veh_delay * 0.8 * jitter(), 1)
            cur.execute(
                "INSERT INTO MOVEMENT_EvaluationTimeInterval VALUES (?, ?, ?, ?, ?)",
                (object_id, qlen, round(qlen * 2.2, 1), round(veh_delay * vehs, 1), round(stops * vehs, 1)),
            )
            movements_written[object_id] = {
                "node": node,
                "from_link": from_link,
                "to_link": to_link,
                "vehs": vehs,
                "qlen": qlen,
                "qlenmax": round(qlen * 2.2, 1),
            }

        cur.execute(
            "CREATE TABLE VEHICLETRAVELTIMEMEASUREMENT_EvaluationTimeIntervalClass ("
            "OBJECT_ID TEXT, ARG_TIMEINTERVAL TEXT, ARG_VEHICLECLASS TEXT,"
            "VEHS REAL, TRAVTM REAL, DISTTRAV REAL)"
        )
        for no, _name, dist, base_times in TRAVELTIME_MEASUREMENTS:
            interval_vehs: list[float] = []
            interval_travtm: list[float] = []
            for interval, base_tt in zip(INTERVALS, base_times):
                vehs_tt = round(140 * volume_factor * jitter(), 1)
                travtm = round(base_tt * delay_factor * jitter(), 1)
                cur.execute(
                    "INSERT INTO VEHICLETRAVELTIMEMEASUREMENT_EvaluationTimeIntervalClass "
                    "VALUES (?, ?, 'ALL', ?, ?, ?)",
                    (str(no), interval, vehs_tt, travtm, dist),
                )
                interval_vehs.append(vehs_tt)
                interval_travtm.append(travtm)
            travel_written[str(no)] = {
                "vehs": round(sum(interval_vehs) / len(interval_vehs), 1),
                "travtm": round(sum(interval_travtm) / len(interval_travtm), 1),
                "dist": dist,
            }

        cur.execute(
            "CREATE TABLE DATACOLLECTIONMEASUREMENT_EvaluationTimeIntervalClass ("
            "OBJECT_ID TEXT, ARG_VEHICLECLASS TEXT, VEHS REAL,"
            "SPEEDAVGARITH REAL, SPEEDAVGHARM REAL, QUEUEDELAY REAL)"
        )
        for no in (1, 2):
            speed = round(BASE_NETWORK["SPEEDAVG"] / delay_factor * jitter(), 1)
            cur.execute(
                "INSERT INTO DATACOLLECTIONMEASUREMENT_EvaluationTimeIntervalClass "
                "VALUES (?, 'ALL', ?, ?, ?, ?)",
                (str(no), round(900 * volume_factor * jitter(), 1), speed, round(speed * 0.93, 1), round(4.2 * delay_factor * jitter(), 2)),
            )

        conn.commit()
    finally:
        conn.close()

    return {"movements": movements_written, "travel_times": travel_written}


# ---------------------------------------------------- observed counts (U4a) ---
#
# WHY THIS EXISTS. CLAUDE.md's synthetic-data rule says non-COM code must work
# against this script's output — but until unit U4a the script emitted result
# databases (the MODEL side) and no counts workbook at all, so the entire
# validation / calibration surface had NO synthetic observed side to compare
# against. Everything below closes that gap.
#
# LAYOUT: the STANDARD base workbook, not a new one. The Counts sheet's columns
# are exactly ``services.validation.create_base_workbook_template``'s
# (SimRun / TimeInt / Period / Movement / Direction / QLen / QLenMax /
# Vehs(All) / Observed) and the TravelTimes sheet is the same stacked Vissim
# travel-time export with AM/PM section markers. ``_parse_counts_sheet``
# resolves that header row through its "Vissim Node Results" branch
# (time_range + movement + model_volume + observed_volume), and
# ``load_base_workbook`` parses the whole file with ZERO warnings.
#
# DERIVED, NEVER HAND-WRITTEN. Every observed number is a transform of the
# MODELLED number this script just wrote into
# ``Scenarios/S000001/S000001.results/run_001.db`` (scenario 1 = Base AM,
# run 1). Change a base volume in MOVEMENTS and both sides move together, so
# the fixture cannot drift out of sync with itself.

#: The compass leg each link occupies AT a given node. Needed per (node, link)
#: rather than per link because the arterial links are shared: 140/141 are node
#: 1's EAST leg and node 2's WEST leg.
LINK_LEG: dict[tuple[int, int], str] = {
    (1, 101): "W", (1, 102): "W", (1, 103): "N", (1, 104): "N",
    (1, 105): "S", (1, 106): "S", (1, 140): "E", (1, 141): "E",
    (2, 140): "W", (2, 141): "W", (2, 203): "N", (2, 204): "N",
    (2, 205): "S", (2, 206): "S", (2, 207): "E", (2, 208): "E",
}

#: TimeInt values per period, copied from the standard template so
#: ``_period_from_time_range`` classifies them even if the Period column is
#: ever dropped by an analyst editing the file.
COUNTS_TIME_INT = {"AM": "900-4500", "PM": "61200-64800"}

#: Node 3 exists ONLY in the counts workbook — a field count collected at an
#: intersection outside the model boundary. It is the fixture's deliberate
#: unjoinable node: unit U5's reality check must NAME it as observed-but-absent
#: from the run rather than dropping it in a silent inner join.
OBSERVED_ONLY_NODE = 3
OBSERVED_ONLY_NODE_NAME = "Central & River"
#: Its counts are node 2's, halved. Arbitrary but deterministic — nothing can
#: be compared against them, so their only job is to exist.
OBSERVED_ONLY_SCALE = 0.5

#: (a) DEMAND MISS. Node 1's AM observed = modelled * this, every movement.
#: The node TOTAL therefore misses and U4's branch (a) fires.
AM_NODE1_DEMAND_FACTOR = 1.15

#: (b) SPLIT MISS. Node 2's AM observed keeps the node total EXACTLY (so U4's
#: branch (a) cannot fire) and moves this many vehicles out of one movement
#: into the others, in proportion to their modelled volume.
AM_NODE2_SPLIT_MOVEMENT = (2, 207, 141)  # the WB through movement: E-W
AM_NODE2_SPLIT_SHIFT_VEHS = 200

#: Travel-time observed = model * this. Travel-time validation is not part of
#: U5's story; the sheet exists so the STANDARD workbook parses warning-free.
TRAVEL_TIME_OBSERVED_FACTOR = {"AM": 1.06, "PM": 1.02}

#: Fixed zip entry timestamp + document dates, so two runs of this script
#: produce a byte-identical workbook (openpyxl otherwise stamps every zip
#: member and docProps/core.xml with the wall clock).
_XLSX_EPOCH = (2026, 1, 1, 0, 0, 0)
#: The same instant in the W3CDTF spelling ``docProps/core.xml`` uses.
_XLSX_EPOCH_W3CDTF = "{:04d}-{:02d}-{:02d}T{:02d}:{:02d}:{:02d}Z".format(*_XLSX_EPOCH)
#: The zip member carrying the document dates.
_CORE_PROPERTIES_MEMBER = "docProps/core.xml"
#: ``<dcterms:modified ...>WALL CLOCK</dcterms:modified>``, attributes intact.
_MODIFIED_STAMP_RE = re.compile(rb"(<dcterms:modified\b[^>]*>)[^<]*(</dcterms:modified>)")


def movement_direction(node: int, from_link: int, to_link: int) -> str:
    """``"W-E"`` — the turn token the workbook's Direction column carries.

    Unique per movement at both demo nodes (12 movements, 12 tokens each), and
    it matches the ``^-?[NSEW]{1,3}-[NSEW]{1,3}$`` shape
    ``validation._column_is_direction_like`` and ``_parse_vissim_movement``
    both expect.
    """
    return f"{LINK_LEG[(node, from_link)]}-{LINK_LEG[(node, to_link)]}"


def movement_object_id(node: int, from_link: int, to_link: int) -> str:
    """The result-.db OBJECT_ID for one movement — one spelling, shared by
    :func:`build_result_db` and every consumer that has to join the workbook
    back onto the run."""
    return f"{node}-{from_link}-250.0-{to_link}-5.0"


def node_movements(node: int) -> list[tuple[int, int, int]]:
    """``(node, from_link, to_link)`` for one node, in MOVEMENTS order."""
    return [(n, f, t) for n, f, t, _v, _d in MOVEMENTS if n == node]


def _observed_scaled(modelled: dict[str, float], factor: float) -> dict[str, int]:
    """Every movement scaled by the same factor — so the node TOTAL misses."""
    return {key: int(round(value * factor)) for key, value in modelled.items()}


def _observed_reconciling(modelled: dict[str, float]) -> dict[str, int]:
    """Observed == modelled (to the nearest vehicle). Every GEH is ~0."""
    return {key: int(round(value)) for key, value in modelled.items()}


def _observed_split_shifted(
    modelled: dict[str, float], *, shift_from: str, vehicles: int
) -> dict[str, int]:
    """Move ``vehicles`` out of one movement into the others, TOTAL preserved.

    Distribution is proportional to each other movement's own modelled volume,
    with the integer residue handed to the largest of them, so the sum is
    exactly the sum of the rounded modelled values: the node total reconciles
    (U4 branch (a) cannot fire) and only the drained movement's SPLIT misses.
    """
    observed = {key: int(round(value)) for key, value in modelled.items()}
    others = {key: value for key, value in observed.items() if key != shift_from}
    pool = sum(others.values())
    if not pool:  # pragma: no cover - the demo network always has siblings
        return observed
    observed[shift_from] -= vehicles
    handed_out = 0
    for key, value in others.items():
        share = int(vehicles * value / pool)
        observed[key] += share
        handed_out += share
    residue = vehicles - handed_out
    if residue:
        largest = max(others, key=lambda key: others[key])
        observed[largest] += residue
    return observed


def _observed_counts_by_period(
    modelled: dict[str, dict[str, object]]
) -> dict[str, dict[int, dict[str, int]]]:
    """``{period: {node: {OBJECT_ID: observed}}}`` — the whole design, derived.

    TWO MODELLED SIDES, TWO SETS OF DIGITS. Every GEH below is quoted for both,
    because two different consumers read two different modelled columns:

    * the **workbook path** — the ``Vehs(All)`` column this function's caller
      writes, which is ``round(VEHS)``, an INTEGER. This is what
      ``validation.load_base_workbook`` + ``_real_counts_from_count_rows``
      return, i.e. what the dashboard's base validation summary and
      ``tests/test_make_demo_data_real_counts.py`` grade;
    * the **run path** — the RAW 1-decimal ``VEHS`` sums straight out of
      ``run_001.db``. Unit U5 BLANKS the workbook's model column on purpose
      (``reality_check._observed_side_only``) and refills it from U1's
      ``run_result["movement_volumes"]``, so the exit demonstration in
      ``tests/test_agent_reality_check.py`` — and any live reality check —
      sees these, not the integers.

    The two agree to ~0.01 GEH; they are not identical, and the PM block is
    where the difference is visible at all.

    THE THREE U4 OUTCOMES, and the arithmetic each is built to produce (GEH =
    sqrt(2*(M-O)^2/(M+O)), ``services.validation.geh``, threshold 5.0):

    * **AM node 1 — demand proposal.** O = round(1.15 * M) on all 12
      movements, so the node total is 1.15x too. Nominal (MOVEMENTS base
      volumes, no jitter): M = 2740, O = 3151, GEH = sqrt(2*411^2 / 5891) =
      7.57. AS GENERATED (run-1 jitter), O = 3150 both ways:
      workbook M = 2738 -> GEH **7.593** (U4 reports 7.59); run M = 2738.5 ->
      GEH **7.5837** (U4 reports 7.58). Either way >= 5 -> U4 branch (a),
      ``set_vehicle_input_volume``. Every individual movement stays under the
      threshold (worst is W-E: 4.083 workbook / 4.065 run), which is the
      point: the miss is a TOTAL miss, not a split one.
    * **AM node 2 — routing proposal.** The total is preserved exactly in
      INTEGERS, so the workbook path's node GEH is **0.0**; the run path sums
      to 2541.6 against O = 2542 and lands at **0.0079** (U4 reports 0.01).
      Both are far below 5, so branch (a) refuses a demand change either way,
      while 200 veh move out of the E-W through movement. Nominal: M = 680,
      O = 480, GEH = sqrt(2*200^2 / 1160) = 8.30. AS GENERATED: M = 671 (the
      jittered value is already integral, so both paths agree exactly),
      O = 471, GEH = **8.3697** >= 5 -> U4 branch (b),
      ``set_route_relative_flow``. The 200 land on the other 11 movements in
      proportion to their volume and the largest of them (M = 727, +84) only
      reaches GEH = 3.029 workbook / 3.022 run — so exactly ONE movement
      crosses the threshold and it is unambiguously the worst.
    * **PM nodes 1 and 2 — refusal.** O = round(M) everywhere. On the WORKBOOK
      path both sides are the same integers, so every node-total GEH and every
      movement GEH is exactly **0.0**. On the RUN path the 1-decimal VEHS meet
      those rounded integers and the difference DOES register, just far below
      the threshold: node totals **0.0096** (node 1) and **0.0079** (node 2),
      both reported as 0.01, and the worst single movement is **0.0452**
      (node 1, E-N; node 2's worst is 0.0382), reported as 0.05 and 0.04. Both
      paths -> U4 branch (c), ``MODEL_REPRODUCES_OBSERVED``; only the digits
      in the refusal's rationale differ, and a test that pins "0.0" for the
      run path is pinning a number the run path never produces.

    WHY A PM BLOCK AT ALL. The demo network has exactly two nodes and adding a
    third would rewrite practice.inpx and all nine result .dbs, which unit U4a
    is explicitly not allowed to do. Periods are a first-class dimension of
    this workbook (``load_real_counts(path, period)``, ``payload["periods"]``),
    so the third outcome lives in PM. It is a fixture device and is documented
    as one: the demo ships ONE run, and the PM observed values are derived from
    that same run's modelled values — which is what makes their GEH exact.
    """
    per_node_modelled = {
        node: {
            movement_object_id(*key): float(modelled["movements"][movement_object_id(*key)]["vehs"])
            for key in node_movements(node)
        }
        for node in (1, 2)
    }

    am = {
        1: _observed_scaled(per_node_modelled[1], AM_NODE1_DEMAND_FACTOR),
        2: _observed_split_shifted(
            per_node_modelled[2],
            shift_from=movement_object_id(*AM_NODE2_SPLIT_MOVEMENT),
            vehicles=AM_NODE2_SPLIT_SHIFT_VEHS,
        ),
    }
    pm = {node: _observed_reconciling(values) for node, values in per_node_modelled.items()}
    return {"AM": am, "PM": pm}


def _counts_rows_for_period(
    period: str,
    modelled: dict[str, dict[str, object]],
    observed_by_node: dict[int, dict[str, int]],
) -> list[list[object]]:
    """One period's Counts rows: every movement, then the node's Total row."""
    time_int = COUNTS_TIME_INT[period]
    rows: list[list[object]] = []

    for node, name in ((no, node_name) for no, node_name, _poly in NODES):
        observed = observed_by_node[node]
        movement_label = f"{node}: {name}"
        model_total = 0
        observed_total = 0
        qlens: list[float] = []
        qlenmaxes: list[float] = []
        for key in node_movements(node):
            object_id = movement_object_id(*key)
            written = modelled["movements"][object_id]
            model_value = int(round(float(written["vehs"])))
            observed_value = observed[object_id]
            qlen = float(written["qlen"])
            qlenmax = float(written["qlenmax"])
            qlens.append(qlen)
            qlenmaxes.append(qlenmax)
            model_total += model_value
            observed_total += observed_value
            rows.append(
                [
                    "Average", time_int, period, movement_label,
                    movement_direction(*key), qlen, qlenmax,
                    model_value, observed_value,
                ]
            )
        rows.append(
            [
                "Average", time_int, period, movement_label, "Total",
                round(sum(qlens) / len(qlens), 2), max(qlenmaxes),
                model_total, observed_total,
            ]
        )

    # The observed-only node: field counts with no model column at all. The
    # Vehs(All) cell is left EMPTY rather than zero-filled -- "the model has no
    # opinion here" and "the model says zero" are different facts, and a 0
    # would manufacture the second.
    source = observed_by_node[2]
    observed_only_label = f"{OBSERVED_ONLY_NODE}: {OBSERVED_ONLY_NODE_NAME}"
    observed_only_total = 0
    for key in node_movements(2):
        value = int(round(source[movement_object_id(*key)] * OBSERVED_ONLY_SCALE))
        observed_only_total += value
        rows.append(
            [
                "Average", time_int, period, observed_only_label,
                movement_direction(*key), None, None, None, value,
            ]
        )
    rows.append(
        [
            "Average", time_int, period, observed_only_label, "Total",
            None, None, None, observed_only_total,
        ]
    )
    return rows


def _seconds_to_time(seconds: float) -> datetime.time:
    """Seconds -> a ``datetime.time`` the template renders as mm:ss."""
    whole = int(seconds)
    micro = int(round((seconds - whole) * 1_000_000))
    if micro >= 1_000_000:  # pragma: no cover - rounding guard
        whole += 1
        micro -= 1_000_000
    return datetime.time(whole // 3600, (whole % 3600) // 60, whole % 60, micro)


def _travel_time_rows(period: str, travel: dict[str, dict[str, object]]) -> list[list[object]]:
    factor = TRAVEL_TIME_OBSERVED_FACTOR[period]
    rows: list[list[object]] = []
    for no, name, _dist, _base_times in TRAVELTIME_MEASUREMENTS:
        written = travel[str(no)]
        model_tt = float(written["travtm"])
        observed_tt = round(model_tt * factor, 2)
        rows.append(
            [
                "Average", COUNTS_TIME_INT[period], f"{no}: {name}",
                written["vehs"], model_tt, written["dist"], observed_tt,
                _seconds_to_time(observed_tt * 0.85),
                _seconds_to_time(model_tt),
                _seconds_to_time(observed_tt),
                _seconds_to_time(observed_tt * 1.15),
            ]
        )
    return rows


def _stable_xlsx_bytes(raw: bytes) -> bytes:
    """Rewrite a workbook zip with fixed member timestamps AND a fixed
    ``dcterms:modified``.

    Two wall-clock stamps make an openpyxl workbook churn, and normalising one
    without the other leaves a time bomb:

    * every ZIP MEMBER's timestamp — normalised below to ``_XLSX_EPOCH``.
      Member ORDER is preserved exactly as openpyxl wrote it; only the
      timestamp and the external attributes are touched.
    * ``docProps/core.xml``'s ``dcterms:modified``. MEASURED: assigning
      ``wb.properties.modified`` before ``save()`` does NOT hold — openpyxl's
      ``save_workbook`` overwrites it with ``datetime.now(utc)`` on the way out
      (``openpyxl/writer/excel.py``: ``workbook.properties.modified =
      datetime.datetime.now(tz=...)``, one line before the writer runs). The
      member CONTENT therefore carries the wall clock, and two generations
      more than a second apart differed for no semantic reason — a 3.3% time
      flake in the fixture's own byte-identity test, which did fire in a full
      suite run. So the stamp is rewritten HERE, on the bytes, where openpyxl
      can no longer overwrite it, exactly matching how ``dcterms:created`` is
      already pinned.

    Only that one text node changes; the element's attributes (namespaces and
    ``xsi:type``) survive untouched.
    """
    out = BytesIO()
    with zipfile.ZipFile(BytesIO(raw)) as source:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
            for member in source.infolist():
                info = zipfile.ZipInfo(member.filename, date_time=_XLSX_EPOCH)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = member.external_attr
                data = source.read(member.filename)
                if member.filename == _CORE_PROPERTIES_MEMBER:
                    data = _MODIFIED_STAMP_RE.sub(
                        rb"\g<1>" + _XLSX_EPOCH_W3CDTF.encode("ascii") + rb"\g<2>", data
                    )
                target.writestr(info, data)
    return out.getvalue()


def build_counts_workbook(
    out: Path, modelled: dict[str, dict[str, object]]
) -> dict[str, dict[int, dict[str, int]]]:
    """Write ``real_counts.xlsx`` — the OBSERVED side of the demo dataset.

    ``modelled`` is :func:`build_result_db`'s return value for scenario 1's
    run 1. Returns the observed design (``{period: {node: {OBJECT_ID: veh}}}``)
    so a caller can assert against the same numbers the workbook carries.
    """
    try:
        import openpyxl
        from openpyxl.styles import Font
    except ImportError as exc:  # pragma: no cover - openpyxl is a hard dep
        raise RuntimeError(
            "openpyxl is required to write real_counts.xlsx (pip install openpyxl)"
        ) from exc

    observed = _observed_counts_by_period(modelled)
    travel = modelled["travel_times"]

    wb = openpyxl.Workbook()
    # docProps/core.xml must be identical run to run (openpyxl defaults both
    # dates to datetime.now()). MEASURED: only ``created`` survives this
    # assignment — ``save()`` overwrites ``modified`` with the wall clock (see
    # _stable_xlsx_bytes, which pins it back on the saved bytes). It is set
    # here anyway so the two dates are declared together and the workbook is
    # correct even for a caller that saves it without that helper.
    stamp = datetime.datetime(*_XLSX_EPOCH)
    wb.properties.created = stamp
    wb.properties.modified = stamp

    counts = wb.active
    counts.title = "Counts"
    counts.append(
        ["SimRun", "TimeInt", "Period", "Movement", "Direction",
         "QLen", "QLenMax", "Vehs(All)", "Observed"]
    )
    for index, period in enumerate(("AM", "PM")):
        if index:
            counts.append([])
        for row in _counts_rows_for_period(period, modelled, observed[period]):
            counts.append(row)

    travel_times = wb.create_sheet("TravelTimes")
    travel_header = [
        "SimRun", "TimeInt", "VehicleTravelTimeMeasurement",
        "Vehs(All)", "TravelTime", "DisTrav(All)", "Observed",
        "TomTom Avg -15%", "model output", "TomTom avg", "TomTom Avg 15%",
    ]
    travel_header_rows: list[int] = []
    for period in ("AM", "PM"):
        travel_times.append([period])
        travel_times.append(travel_header)
        travel_header_rows.append(travel_times.max_row)
        for row in _travel_time_rows(period, travel):
            travel_times.append(row)
        travel_times.append([])

    bold = Font(bold=True)
    counts.freeze_panes = "A2"
    for cell in counts[1]:
        cell.font = bold
    for column_cells in counts.columns:
        max_len = max(len(str(cell.value or "")) for cell in column_cells)
        counts.column_dimensions[column_cells[0].column_letter].width = min(max(max_len + 2, 12), 32)

    for row_index in travel_header_rows:
        for cell in travel_times[row_index]:
            cell.font = bold
    for row in travel_times.iter_rows():
        if row and str(row[0].value or "").strip() in {"AM", "PM"}:
            row[0].font = bold
    for column_cells in travel_times.columns:
        max_len = max(len(str(cell.value or "")) for cell in column_cells)
        travel_times.column_dimensions[column_cells[0].column_letter].width = min(max(max_len + 2, 12), 36)
    for row in travel_times.iter_rows():
        for cell in row:
            if isinstance(cell.value, datetime.time):
                cell.number_format = "mm:ss"

    buffer = BytesIO()
    wb.save(buffer)
    (out / "real_counts.xlsx").write_bytes(_stable_xlsx_bytes(buffer.getvalue()))
    return observed


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Regenerating into the DEFAULT --out changes what the running demo shows:\n"
            "  * data/real_counts.xlsx is (re)created at exactly config.REAL_COUNTS_EXCEL,\n"
            "    the path services.datasets falls back to when no workbook has been\n"
            "    uploaded. The dashboard's validation surface flips from 'workbook not\n"
            "    found' to 'observed counts present', and the agent's run_reality_check\n"
            "    starts returning real proposals/refusals on demo data.\n"
            "  * data/Scenarios is deleted and rebuilt (byte-identical rebuild, but any\n"
            "    file a human put in that tree is gone).\n"
            "Pass --out DIR to build elsewhere; nothing outside it is touched."
        ),
    )
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "data"),
        help=(
            "output directory (default: <repo>/data — the live demo dataset: writing there "
            "publishes real_counts.xlsx to config.REAL_COUNTS_EXCEL and rmtree's Scenarios; "
            "see the note below)"
        ),
    )
    args = parser.parse_args()
    out = Path(args.out)

    scenarios_dir = out / "Scenarios"
    if scenarios_dir.exists():
        shutil.rmtree(scenarios_dir)
    out.mkdir(parents=True, exist_ok=True)

    build_inpx(out)
    build_project_db(out)
    # Unit U4a: the counts workbook is derived from the BASE scenario's first
    # run, so this holds exactly what that .db was written with.
    baseline_modelled: dict[str, dict[str, object]] | None = None
    for number, (_name, volume_factor, delay_factor) in SCENARIOS.items():
        scenario_id = f"S{number:06d}"
        results_dir = scenarios_dir / scenario_id / f"{scenario_id}.results"
        results_dir.mkdir(parents=True, exist_ok=True)
        for run in range(1, RUNS_PER_SCENARIO + 1):
            rng = random.Random(number * 100 + run)
            written = build_result_db(
                results_dir / f"run_{run:03d}.db", volume_factor, delay_factor, rng
            )
            if number == 1 and run == 1:
                baseline_modelled = written

    assert baseline_modelled is not None  # SCENARIOS always carries scenario 1
    build_counts_workbook(out, baseline_modelled)

    print(f"Demo data written to {out}")
    print(f"  scenarios: {', '.join(f'S{n:06d} ({name})' for n, (name, _, _) in SCENARIOS.items())}")
    print(f"  runs per scenario: {RUNS_PER_SCENARIO}")
    print("  observed counts: real_counts.xlsx (Counts + TravelTimes, AM/PM)")


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()

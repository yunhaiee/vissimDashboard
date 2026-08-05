"""Generate synthetic demo data so the dashboard runs without Vissim.

Builds into the repo-root ``data/`` directory (git-ignored):
  - practice.inpx           two-intersection arterial network
  - practice.vissimpdb      SCENARIO table with three scenarios
  - Scenarios/S00000N/S00000N.results/run_00K.db   3 seed-runs per scenario

The result databases match the schema ``services/results_reader.py`` reads:
movement OBJECT_IDs use the ``node-fromlink-pos-tolink-pos`` format so
directional breakdowns resolve against the .inpx geometry.

Usage:  python tools/make_demo_data.py  [--out DIR]
Deterministic — same output every run (fixed RNG seed per scenario/run).
"""
from __future__ import annotations

import argparse
import random
import shutil
import sqlite3
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


def build_result_db(db_path: Path, volume_factor: float, delay_factor: float, rng: random.Random) -> None:
    def jitter() -> float:
        return rng.uniform(0.965, 1.035)

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

        cur.execute(
            "CREATE TABLE VEHICLETRAVELTIMEMEASUREMENT_EvaluationTimeIntervalClass ("
            "OBJECT_ID TEXT, ARG_TIMEINTERVAL TEXT, ARG_VEHICLECLASS TEXT,"
            "VEHS REAL, TRAVTM REAL, DISTTRAV REAL)"
        )
        for no, _name, dist, base_times in TRAVELTIME_MEASUREMENTS:
            for interval, base_tt in zip(INTERVALS, base_times):
                cur.execute(
                    "INSERT INTO VEHICLETRAVELTIMEMEASUREMENT_EvaluationTimeIntervalClass "
                    "VALUES (?, ?, 'ALL', ?, ?, ?)",
                    (
                        str(no), interval,
                        round(140 * volume_factor * jitter(), 1),
                        round(base_tt * delay_factor * jitter(), 1),
                        dist,
                    ),
                )

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO_ROOT / "data"), help="output directory (default: <repo>/data)")
    args = parser.parse_args()
    out = Path(args.out)

    scenarios_dir = out / "Scenarios"
    if scenarios_dir.exists():
        shutil.rmtree(scenarios_dir)
    out.mkdir(parents=True, exist_ok=True)

    build_inpx(out)
    build_project_db(out)
    for number, (_name, volume_factor, delay_factor) in SCENARIOS.items():
        scenario_id = f"S{number:06d}"
        results_dir = scenarios_dir / scenario_id / f"{scenario_id}.results"
        results_dir.mkdir(parents=True, exist_ok=True)
        for run in range(1, RUNS_PER_SCENARIO + 1):
            rng = random.Random(number * 100 + run)
            build_result_db(results_dir / f"run_{run:03d}.db", volume_factor, delay_factor, rng)

    print(f"Demo data written to {out}")
    print(f"  scenarios: {', '.join(f'S{n:06d} ({name})' for n, (name, _, _) in SCENARIOS.items())}")
    print(f"  runs per scenario: {RUNS_PER_SCENARIO}")


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()

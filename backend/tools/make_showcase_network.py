"""Generate a geometry-rich showcase .inpx to exercise the NetworkViewer.

Same XML dialect as ``backend/tools/make_demo_data.py`` (the shapes
``services/inpx_parser.py`` reads), but with the geometry variety the flat
demo cross lacks: multi-point curved links, a roundabout built from arc
links, a T-intersection, a diagonal cut-through, varied lane counts (1-4),
several curved turn connectors, and — the point of this fixture — one
instance of every Tier-1 static network element type the parser resolves
under the ``elements`` key: signal heads, vehicle inputs, reduced speed
areas, desired speed decisions, detectors, data collection points, a
geometry-bearing travel time measurement, a queue counter, a stop sign, a
PT stop, and a Viswalk pedestrian area.

No results/scenarios — geometry only.

Usage:  python tools/make_showcase_network.py  [--out DIR]
Default output: <repo>/data/showcase.inpx (data/ is git-ignored — the tool
itself is what's committed).
"""
from __future__ import annotations

import argparse
import sys
from math import cos, sin, pi
from pathlib import Path
from xml.sax.saxutils import escape

# Runnable from anywhere, like make_demo_data.py: the `services` import in
# __main__ resolves against backend/ whichever directory the shell is in.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

RA_CX, RA_CY, RA_R = 620.0, 40.0, 30.0


def arc(start_deg: float, end_deg: float, n: int = 7, r: float = RA_R):
    pts = []
    for i in range(n):
        a = (start_deg + (end_deg - start_deg) * i / (n - 1)) * pi / 180.0
        pts.append((round(RA_CX + r * cos(a), 2), round(RA_CY + r * sin(a), 2)))
    return pts


# (no, name, points, lanes)
LINKS = [
    # Crossroads (Node 11) west approach/exit — straight dual carriageway
    (401, "Harbor Rd EB approach", [(-420, -5), (-24, -5)], 2),
    (402, "Harbor Rd WB exit", [(-24, 5), (-420, 5)], 2),
    # S-curved arterial between crossroads and roundabout, 3 lanes each way
    (403, "River Arterial EB", [(24, -6), (140, -46), (300, -56), (460, 2), (556, 30)], 3),
    (404, "River Arterial WB", [(560, 48), (464, 16), (300, -42), (144, -32), (24, 8)], 3),
    # Crossroads south leg — narrow one-laners
    (405, "Dock Lane SB", [(-5, -24), (-5, -310)], 1),
    (406, "Dock Lane NB", [(5, -310), (5, -24)], 1),
    # Curved avenue north from crossroads up to the T (Node 13)
    (407, "Hill Ave NB", [(-6, 24), (-10, 150), (-40, 270), (-74, 396)], 2),
    (408, "Hill Ave SB", [(-66, 392), (-32, 266), (-2, 148), (2, 24)], 2),
    # T-intersection east-west road
    (409, "Ridge Rd WB approach", [(-360, 428), (-102, 424)], 2),
    (410, "Ridge Rd EB exit", [(-102, 416), (-360, 420)], 2),
    (411, "Ridge Rd EB", [(-58, 420), (120, 428), (300, 436), (430, 408)], 2),
    # Diagonal cut-through from the T area down to the arterial
    (412, "Quarry Cut SE", [(-52, 396), (80, 268), (210, 150), (330, 76)], 1),
    # Roundabout circulating carriageway (CCW quarter arcs, one lane)
    (421, "Roundabout E-N arc", arc(0, 90), 1),
    (422, "Roundabout N-W arc", arc(90, 180), 1),
    (423, "Roundabout W-S arc", arc(180, 270), 1),
    (424, "Roundabout S-E arc", arc(270, 360), 1),
    # Roundabout legs
    (425, "Summit Rd N approach", [(624, 320), (622, 110), (621, 74)], 1),
    (426, "Summit Rd N exit", [(613, 74), (612, 112), (610, 320)], 1),
    (427, "East Gate exit", [(654, 46), (760, 58), (960, 74)], 4),
    (428, "East Gate approach", [(960, 90), (770, 76), (658, 56)], 2),
]

# (no, name, points, lanes, from_link, from_lane, from_pos, to_link, to_lane, to_pos)
CONNECTORS = [
    # Crossroads: right turn from Harbor Rd EB into Hill Ave NB (curved)
    (501, "Harbor→Hill right turn", [(-24, -5), (-14, 2), (-8, 12), (-6, 24)], 1, 401, 1, 396.0, 407, 1, 0.0),
    # Crossroads: left turn from Hill Ave SB onto River Arterial EB
    (502, "Hill→River left turn", [(2, 24), (8, 8), (16, -2), (24, -6)], 1, 408, 1, 380.0, 403, 1, 0.0),
    # Crossroads: through Harbor→River
    (503, "Harbor→River through", [(-24, -5), (0, -6), (24, -6)], 2, 401, 1, 396.0, 403, 1, 0.0),
    # Roundabout entries/exits (short curved stubs)
    (511, "Arterial→Roundabout entry", [(556, 30), (576, 24), (592, 19)], 1, 403, 1, 470.0, 423, 1, 15.0),
    (512, "Roundabout→East Gate exit", [(648, 25), (652, 36), (654, 46)], 1, 424, 1, 30.0, 427, 1, 0.0),
    (513, "Summit→Roundabout entry", [(621, 74), (622, 72), (623, 70)], 1, 425, 1, 246.0, 421, 1, 20.0),
    # T-intersection: right turn Ridge WB → Hill Ave SB... (curved)
    (521, "Ridge→Hill left turn", [(-102, 424), (-88, 414), (-74, 400)], 1, 409, 1, 258.0, 408, 1, 0.0),
]

# (no, name, polygon)
NODES = [
    (11, "Harbor & Hill Crossroads", [(-24, -24), (24, -24), (24, 24), (-24, 24)]),
    (12, "Riverside Roundabout", [(round(RA_CX + (RA_R + 12) * cos(a * pi / 4), 2), round(RA_CY + (RA_R + 12) * sin(a * pi / 4), 2)) for a in range(8)]),
    (13, "Ridge & Hill T", [(-102, 398), (-58, 398), (-58, 442), (-102, 442)]),
]

# --- Tier 1 static network elements -----------------------------------------
# Placed to read as a plausible real intersection: signal heads on 3 of the
# crossroads' 4 legs (Dock Lane is stop-controlled instead, so its own stop
# sign doesn't collide with a signal there), detectors near the same stop
# lines, vehicle inputs already cover the 3 entry links, one reduced-speed
# area on the arterial's sharpest curve, desired-speed decisions and a data
# collection point further along the arterial/Ridge Rd, a travel-time
# measurement spanning the crossroads exit to the roundabout entry, a queue
# counter on the Harbor Rd approach, a PT stop on Ridge Rd, and one small
# pedestrian area near the crossroads.

# (no, name, link, lane, pos, sc, sg)
SIGNAL_HEADS = [
    (1, "Harbor EB stop line signal", 401, 1, 388.0, 1, 1),
    (2, "Hill SB stop line signal", 408, 1, 368.0, 1, 2),
    (3, "River Arterial WB stop line signal", 404, 1, 548.0, 1, 3),
]

# (no, name, link, lane, pos, length)
REDUCED_SPEED_AREAS = [
    (1, "River Arterial curve slow zone", 403, 1, 250.0, 100.0),
]

# (no, name, link, lane, pos)
DES_SPEED_DECISIONS = [
    (1, "River Arterial approach desired speed", 403, 1, 50.0),
    (2, "Ridge Rd EB desired speed", 411, 1, 400.0),
]

# (no, name, link, lane, pos, length)
DETECTORS = [
    (1, "Harbor EB stop-bar detector", 401, 1, 385.0, 2.0),
    (2, "Hill SB stop-bar detector", 408, 1, 365.0, 2.0),
]

# (no, name, link, lane, pos)
DATA_COLLECTION_POINTS = [
    (1, "River Arterial mid-link count", 403, 1, 276.0),
    (2, "Ridge Rd EB mid-link count", 411, 1, 246.0),
]

# (no, name, start_link, start_pos, end_link, end_pos)
TRAVELTIME_MEASUREMENTS_GEO = [
    (1, "Crossroads to Roundabout", 403, 0.0, 423, 15.0),
]

# (no, name, link, pos)
QUEUE_COUNTERS = [
    (1, "Harbor EB queue", 401, 350.0),
]

# (no, name, link, lane, pos)
STOP_SIGNS = [
    (1, "Dock Lane NB stop sign", 406, 1, 270.0),
]

# (no, name, link, lane, pos, length)
PT_STOPS = [
    (1, "Ridge Rd EB PT stop", 411, 1, 350.0, 15.0),
]

# (no, name, polygon)
PED_AREAS = [
    (1, "Crossroads NE ped plaza", [(30, 30), (46, 30), (46, 46), (30, 46)]),
]


def pts(points, tag_container, tag_point):
    rows = "".join(f'<{tag_point} x="{x}" y="{y}" zOffset="0"/>' for x, y in points)
    return f"<{tag_container}>{rows}</{tag_container}>"


def build() -> str:
    link_xml = []
    for no, name, points, lanes in LINKS:
        lanes_xml = "".join('<lane width="3.5"/>' for _ in range(lanes))
        link_xml.append(
            f'<link no="{no}" name="{escape(name)}">'
            f"<geometry>{pts(points, 'linkPolyPts', 'linkPolyPoint')}</geometry>"
            f"<lanes>{lanes_xml}</lanes>"
            f"</link>"
        )
    for no, name, points, lanes, fl, fla, fp, tl, tla, tp in CONNECTORS:
        lanes_xml = "".join('<lane width="3.5"/>' for _ in range(lanes))
        link_xml.append(
            f'<link no="{no}" name="{escape(name)}">'
            f"<geometry>{pts(points, 'linkPolyPts', 'linkPolyPoint')}</geometry>"
            f"<lanes>{lanes_xml}</lanes>"
            f'<fromLinkEndPt lane="{fl} {fla}" pos="{fp}"/>'
            f'<toLinkEndPt lane="{tl} {tla}" pos="{tp}"/>'
            f"</link>"
        )
    node_xml = [
        f'<node no="{no}" name="{escape(name)}">'
        f"<geometry>{pts(polygon, 'points', 'point')}</geometry>"
        f"</node>"
        for no, name, polygon in NODES
    ]
    net_para = (
        '<netPara northDir="0">'
        '<refPointMap x="115.8360" y="-32.0670"/>'
        '<refPointNet x="0" y="0"/>'
        "</netPara>"
    )
    vi_xml = (
        "<vehicleInputs>"
        '<vehicleInput no="1" name="Harbor Rd EB" link="401"/>'
        '<vehicleInput no="2" name="Ridge Rd WB" link="409"/>'
        '<vehicleInput no="3" name="East Gate WB" link="428"/>'
        "</vehicleInputs>"
    )
    sh_xml = "".join(
        f'<signalHead no="{no}" name="{escape(name)}" lane="{link} {lane}" pos="{pos}" sg="{sc} {sg}"/>'
        for no, name, link, lane, pos, sc, sg in SIGNAL_HEADS
    )
    rsa_xml = "".join(
        f'<reducedSpeedArea no="{no}" name="{escape(name)}" lane="{link} {lane}" pos="{pos}" length="{length}"/>'
        for no, name, link, lane, pos, length in REDUCED_SPEED_AREAS
    )
    dsd_xml = "".join(
        f'<desSpeedDecision no="{no}" name="{escape(name)}" lane="{link} {lane}" pos="{pos}"/>'
        for no, name, link, lane, pos in DES_SPEED_DECISIONS
    )
    det_xml = "".join(
        f'<detector no="{no}" name="{escape(name)}" lane="{link} {lane}" pos="{pos}" length="{length}"/>'
        for no, name, link, lane, pos, length in DETECTORS
    )
    dcp_xml = "".join(
        f'<dataCollectionPoint no="{no}" name="{escape(name)}" lane="{link} {lane}" pos="{pos}"/>'
        for no, name, link, lane, pos in DATA_COLLECTION_POINTS
    )
    tt_geo_xml = "".join(
        f'<vehicleTravelTimeMeasurement no="{no}" name="{escape(name)}">'
        f'<start link="{sl}" pos="{sp}"/><end link="{el}" pos="{ep}"/>'
        f"</vehicleTravelTimeMeasurement>"
        for no, name, sl, sp, el, ep in TRAVELTIME_MEASUREMENTS_GEO
    )
    qc_xml = "".join(
        f'<queueCounter no="{no}" name="{escape(name)}" link="{link}" pos="{pos}"/>'
        for no, name, link, pos in QUEUE_COUNTERS
    )
    ss_xml = "".join(
        f'<stopSign no="{no}" name="{escape(name)}" lane="{link} {lane}" pos="{pos}"/>'
        for no, name, link, lane, pos in STOP_SIGNS
    )
    pts_xml = "".join(
        f'<ptStop no="{no}" name="{escape(name)}" lane="{link} {lane}" pos="{pos}" length="{length}"/>'
        for no, name, link, lane, pos, length in PT_STOPS
    )
    areas_xml = "".join(
        f'<area no="{no}" name="{escape(name)}">{pts(polygon, "points", "point")}</area>'
        for no, name, polygon in PED_AREAS
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<network version="2400">'
        f"{net_para}"
        f"<links>{''.join(link_xml)}</links>"
        f"<nodes>{''.join(node_xml)}</nodes>"
        f"{vi_xml}"
        f"<signalHeads>{sh_xml}</signalHeads>"
        f"<reducedSpeedAreas>{rsa_xml}</reducedSpeedAreas>"
        f"<desSpeedDecisions>{dsd_xml}</desSpeedDecisions>"
        f"<detectors>{det_xml}</detectors>"
        f"<dataCollectionPoints>{dcp_xml}</dataCollectionPoints>"
        f"<vehicleTravelTimeMeasurements>{tt_geo_xml}</vehicleTravelTimeMeasurements>"
        f"<queueCounters>{qc_xml}</queueCounters>"
        f"<stopSigns>{ss_xml}</stopSigns>"
        f"<ptStops>{pts_xml}</ptStops>"
        f"<areas>{areas_xml}</areas>"
        "</network>"
    )


def write(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "showcase.inpx"
    out_path.write_text(build(), encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO_ROOT / "data"), help="output directory (default: <repo>/data)")
    args = parser.parse_args()
    out_path = write(Path(args.out))
    print(
        f"wrote {out_path} ({out_path.stat().st_size} bytes, {len(LINKS)} links, "
        f"{len(CONNECTORS)} connectors, {len(NODES)} nodes, "
        f"{len(SIGNAL_HEADS)} signal heads, {len(REDUCED_SPEED_AREAS)} reduced speed areas, "
        f"{len(DES_SPEED_DECISIONS)} des speed decisions, {len(DETECTORS)} detectors, "
        f"{len(DATA_COLLECTION_POINTS)} data collection points, {len(TRAVELTIME_MEASUREMENTS_GEO)} tt measurements, "
        f"{len(QUEUE_COUNTERS)} queue counters, {len(STOP_SIGNS)} stop signs, {len(PT_STOPS)} pt stops, "
        f"{len(PED_AREAS)} ped areas)"
    )


if __name__ == "__main__":
    # Unit 1.4: a CLI run has no HTTP request to scope it to a project, and
    # services/workspaces.py refuses to guess one -- so say "the default
    # project" explicitly before anything resolves a dataset path.
    from services.workspaces import bind_workspace, default_workspace_id

    with bind_workspace(default_workspace_id()):
        main()

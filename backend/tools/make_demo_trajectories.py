"""Generate a schema-faithful synthetic .fzp vehicle-trajectory export for the
showcase network (``tools/make_showcase_network.py``).

No real Vissim ``.fzp`` file exists on this machine (no COM, no lab box), so
the format is implemented from Vissim's documented per-timestep vehicle
record convention rather than a captured sample -- same inferred-defensive
policy as the connector/element-topology entries in
``docs/phase2/DECISIONS.md``. The header line drives column mapping (``
$VEHICLE:SIMSEC;NO;LINK;LANE;POS;SPEED``); ``services/trajectory_reader.py``
reads it the same defensive way regardless of column order.

Kinematics are demo-grade, not traffic-realistic: piecewise-constant speed per
link with an authored slowdown inside the arterial's reduced-speed area
(``REDUCED_SPEED_AREAS`` in ``make_showcase_network.py``) and a full stop at
whichever signal-head stop line / stop sign a route passes, driven by a
trivial fixed-cycle signal model. No car-following, no acceleration curves --
see the dated DECISIONS.md entry for this unit.

Positions are kept as (link, lane, pos) exactly like a real Vissim export --
x/y are never baked in here; ``trajectory_reader.resolve_frames`` resolves
them server-side against whichever network is active, via the same
``point_at_pos``/``lane_offset_point`` primitives Tier 1 already uses.

Usage:  python tools/make_demo_trajectories.py  [--out DIR]
Default output: <repo>/data/demo_trajectories.fzp (data/ is git-ignored -- the
tool itself is what's committed). Deterministic -- same output every run
(fixed RNG seed).
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from tools.make_showcase_network import (  # noqa: E402
    CONNECTORS,
    LINKS,
    REDUCED_SPEED_AREAS,
    SIGNAL_HEADS,
    STOP_SIGNS,
)

# ---------------------------------------------------------------------------
# Geometry facts pulled straight from the showcase network's own source data
# (never re-hardcoded here) so this generator can never silently drift from
# the network tools/make_showcase_network.py actually builds.
# ---------------------------------------------------------------------------


def _polyline_length(points: list) -> float:
    total = 0.0
    for i in range(1, len(points)):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        total += math.hypot(x1 - x0, y1 - y0)
    return total


_LINK_LENGTHS: dict[int, float] = {no: round(_polyline_length(pts), 2) for no, _name, pts, _lanes in LINKS}
_LINK_LENGTHS.update({no: round(_polyline_length(pts), 2) for no, _name, pts, _lanes, *_rest in CONNECTORS})

_LANE_COUNTS: dict[int, int] = {no: lanes for no, _name, _pts, lanes in LINKS}
_LANE_COUNTS.update({no: lanes for no, _name, _pts, lanes, *_rest in CONNECTORS})

_CONNECTORS_BY_NO = {c[0]: c for c in CONNECTORS}
# CONNECTORS tuple shape: (no, name, points, lanes, from_link, from_lane, from_pos, to_link, to_lane, to_pos)

_SIGNALS_BY_LINK = {sh[2]: {"pos": sh[4], "sg": sh[6]} for sh in SIGNAL_HEADS}
# SIGNAL_HEADS tuple shape: (no, name, link, lane, pos, sc, sg)

_STOPSIGNS_BY_LINK = {ss[2]: {"pos": ss[4]} for ss in STOP_SIGNS}
# STOP_SIGNS tuple shape: (no, name, link, lane, pos)

_rsa = REDUCED_SPEED_AREAS[0]
# REDUCED_SPEED_AREAS tuple shape: (no, name, link, lane, pos, length)
RSA_LINK = _rsa[2]
RSA_START = _rsa[4]
RSA_END = _rsa[4] + _rsa[5]
RSA_SPEED_KMH = 25.0  # authored slow-zone speed -- the network fixture has no speed field


def _link_length(no: int) -> float:
    return _LINK_LENGTHS[no]


def _conn_from_pos(no: int) -> float:
    return _CONNECTORS_BY_NO[no][6]


def _conn_to_pos(no: int) -> float:
    return _CONNECTORS_BY_NO[no][9]


def _clamped(pos: float, link: int) -> float:
    """Some connector ``from_pos`` values in the showcase fixture slightly
    exceed their own from-link's true centerline length (e.g. connector 502
    declares ``from_pos=380.0`` on link 408, whose polyline is only 376.33m) --
    an existing quirk of that committed fixture, not something this generator
    should silently "fix". Clamp defensively rather than emit an out-of-range
    ``pos``."""
    return min(pos, _link_length(link))


# ---------------------------------------------------------------------------
# Route definitions: an ordered list of link segments. Every position that
# reflects real network topology (connector attach points, signal/stop-sign
# positions, the reduced-speed-area bounds) is pulled from the showcase
# fixture above rather than duplicated as a literal; only the desired cruise
# speeds (km/h) are authored here, since the fixture carries no speed data.
#
# Simplifying assumption (documented in DECISIONS.md): the four roundabout
# quarter-arc links (421-424) are treated as chaining directly into one
# another at their shared endpoints, with no dedicated connector link between
# them -- the showcase fixture only defines connectors into/out of the
# roundabout (511/512/513), not between adjacent arcs.
# ---------------------------------------------------------------------------


def _seg(link: int, start: float, end: float, speed_kmh: float, *, slow_zone=None, stop=None) -> dict:
    return {
        "link": link,
        "start": start,
        "end": end,
        "speed": speed_kmh,
        "slow_zone": slow_zone,
        "stop": stop,
    }


_STOP_401 = {"kind": "signal", "sg": _SIGNALS_BY_LINK[401]["sg"], "pos": _SIGNALS_BY_LINK[401]["pos"]}
_STOP_408 = {"kind": "signal", "sg": _SIGNALS_BY_LINK[408]["sg"], "pos": _SIGNALS_BY_LINK[408]["pos"]}
_STOP_406 = {"kind": "stopsign", "pos": _STOPSIGNS_BY_LINK[406]["pos"]}
_RSA_403 = (RSA_START, RSA_END, RSA_SPEED_KMH)

ROUTES: dict[str, list[dict]] = {
    # Harbor Rd EB -> through connector -> River Arterial EB -> roundabout
    # entry -> half the roundabout -> East Gate exit.
    "harbor_roundabout_eastgate": [
        _seg(401, 0.0, _conn_from_pos(503), 45.0, stop=_STOP_401),
        _seg(503, 0.0, _link_length(503), 20.0),
        _seg(403, 0.0, _conn_from_pos(511), 55.0, slow_zone=_RSA_403),
        _seg(511, 0.0, _link_length(511), 20.0),
        _seg(423, _conn_to_pos(511), _link_length(423), 20.0),
        _seg(424, 0.0, _conn_from_pos(512), 20.0),
        _seg(512, 0.0, _link_length(512), 18.0),
        _seg(427, 0.0, _link_length(427), 50.0),
    ],
    # Harbor Rd EB -> right-turn connector -> Hill Ave NB (dead-ends at the
    # tracked network's edge, same as a vehicle continuing off-map).
    "harbor_hill_rightturn": [
        _seg(401, 0.0, _conn_from_pos(501), 45.0, stop=_STOP_401),
        _seg(501, 0.0, _link_length(501), 18.0),
        _seg(407, 0.0, _link_length(407), 40.0),
    ],
    # T-intersection route: Ridge Rd WB -> left turn -> Hill Ave SB (own
    # signal) -> left turn -> River Arterial EB (through the slow zone).
    "ridge_t_route": [
        _seg(409, 0.0, _conn_from_pos(521), 45.0),
        _seg(521, 0.0, _link_length(521), 18.0),
        _seg(408, 0.0, _clamped(_conn_from_pos(502), 408), 40.0, stop=_STOP_408),
        _seg(502, 0.0, _link_length(502), 18.0),
        _seg(403, 0.0, 400.0, 55.0, slow_zone=_RSA_403),
    ],
    # Dock Lane NB: single link, mandatory stop at the stop sign near the
    # crossroads before the route ends (no connector continues from here in
    # the fixture).
    "dock_lane_stopsign": [
        _seg(406, 0.0, _link_length(406), 30.0, stop=_STOP_406),
    ],
    # Summit Rd N -> roundabout entry -> most of the way around -> East Gate.
    "summit_roundabout_eastgate": [
        _seg(425, 0.0, _clamped(_conn_from_pos(513), 425), 40.0),
        _seg(513, 0.0, _link_length(513), 18.0),
        _seg(421, _conn_to_pos(513), _link_length(421), 20.0),
        _seg(422, 0.0, _link_length(422), 20.0),
        _seg(423, 0.0, _link_length(423), 20.0),
        _seg(424, 0.0, _conn_from_pos(512), 20.0),
        _seg(512, 0.0, _link_length(512), 18.0),
        _seg(427, 0.0, _link_length(427), 50.0),
    ],
}

# ---------------------------------------------------------------------------
# Kinematics
# ---------------------------------------------------------------------------

SIM_START = 0.0
SIM_END = 180.0
STEP = 0.5

SIGNAL_CYCLE_S = 40.0
SIGNAL_SPLIT_S = 20.0  # sg1 green in [0, 20); sg2 green in [20, 40) -- a trivial two-phase signal
STOP_SIGN_DWELL_S = 3.0

NUM_VEHICLES = 32
RELEASE_SPAN_S = 150.0
RNG_SEED = 20260726


def _signal_wait(sg: str | int, arrival_t: float) -> float:
    """Seconds a vehicle arriving at ``arrival_t`` must wait for its signal
    group to go green, under a trivial fixed 40s-cycle, two-phase signal
    (sg1 and sg2 always run complementary phases). Unknown sg -> no wait."""
    phase = arrival_t % SIGNAL_CYCLE_S
    sg_i = int(sg)
    if sg_i == 1:
        return 0.0 if phase < SIGNAL_SPLIT_S else (SIGNAL_CYCLE_S - phase)
    if sg_i == 2:
        return 0.0 if phase >= SIGNAL_SPLIT_S else (SIGNAL_SPLIT_S - phase)
    return 0.0


def _build_legs(segment: dict) -> list[dict]:
    """Expand one route segment into ordered (pos0, pos1, speed, stop_after)
    legs, splitting at reduced-speed-area boundaries and the segment's own
    stop position (if any falls inside its range)."""
    start, end = segment["start"], segment["end"]
    breakpoints = {start, end}

    slow_zone = segment.get("slow_zone")
    if slow_zone:
        zs, ze, _ = slow_zone
        if start < ze and end > zs:
            breakpoints.add(max(start, zs))
            breakpoints.add(min(end, ze))

    stop = segment.get("stop")
    if stop and start <= stop["pos"] <= end:
        breakpoints.add(stop["pos"])

    points = sorted(p for p in breakpoints if start <= p <= end)

    legs = []
    for lo, hi in zip(points, points[1:]):
        if hi - lo <= 1e-9:
            continue
        mid = (lo + hi) / 2.0
        speed = segment["speed"]
        if slow_zone and slow_zone[0] <= mid <= slow_zone[1]:
            speed = slow_zone[2]
        stop_after = stop if stop and abs(hi - stop["pos"]) < 1e-6 else None
        legs.append({"pos0": lo, "pos1": hi, "speed": speed, "stop_after": stop_after})
    return legs


def _build_profile(route_segments: list[dict], start_time: float, veh_index: int) -> list[dict]:
    """Absolute-time leg list for one vehicle:
    ``[{"t0", "t1", "link", "lane", "pos0", "pos1", "speed"}, ...]``.
    Signal waits are resolved here (not in ``_build_legs``) because the wait
    duration depends on the vehicle's actual arrival clock time, which is
    only known once legs are walked in order."""
    profile: list[dict] = []
    t = start_time
    for segment in route_segments:
        link = segment["link"]
        lane_count = _LANE_COUNTS.get(link, 1)
        lane = 1 + (veh_index % lane_count)
        for leg in _build_legs(segment):
            distance = leg["pos1"] - leg["pos0"]
            speed_mps = leg["speed"] / 3.6
            dt = distance / speed_mps if speed_mps > 0 else 0.0
            profile.append({
                "t0": t, "t1": t + dt, "link": link, "lane": lane,
                "pos0": leg["pos0"], "pos1": leg["pos1"], "speed": leg["speed"],
            })
            t += dt

            stop_after = leg["stop_after"]
            if stop_after:
                if stop_after["kind"] == "signal":
                    wait = _signal_wait(stop_after["sg"], t)
                else:
                    wait = STOP_SIGN_DWELL_S
                if wait > 0:
                    profile.append({
                        "t0": t, "t1": t + wait, "link": link, "lane": lane,
                        "pos0": leg["pos1"], "pos1": leg["pos1"], "speed": 0.0,
                    })
                    t += wait
    return profile


def _sample_profile(profile: list[dict], *, step: float = STEP, sim_end: float = SIM_END) -> list[dict]:
    """Sample a vehicle's absolute-time leg profile onto the fixed global time
    grid, linearly interpolating position within whichever leg is active."""
    if not profile:
        return []

    t_start = profile[0]["t0"]
    t_end = profile[-1]["t1"]

    frames = []
    idx = 0
    t = math.ceil(t_start / step) * step
    while t <= min(t_end, sim_end) + 1e-9:
        while idx < len(profile) - 1 and t > profile[idx]["t1"] + 1e-9:
            idx += 1
        leg = profile[idx]
        span = leg["t1"] - leg["t0"]
        frac = 0.0 if span <= 1e-9 else max(0.0, min(1.0, (t - leg["t0"]) / span))
        pos = leg["pos0"] + (leg["pos1"] - leg["pos0"]) * frac
        frames.append({
            "t": round(t, 2), "link": leg["link"], "lane": leg["lane"],
            "pos": round(pos, 2), "speed": round(leg["speed"], 1),
        })
        t += step
    return frames


def generate_vehicles(num_vehicles: int = NUM_VEHICLES, seed: int = RNG_SEED) -> list[dict]:
    """Deterministic list of ``{"no", "route", "start", "frames"}`` per
    vehicle, released with staggered (jittered) start times across
    ``RELEASE_SPAN_S`` seconds and round-robined across every route so each
    gets comparable coverage."""
    rng = random.Random(seed)
    route_names = list(ROUTES.keys())
    vehicles = []
    veh_no = 1
    for i in range(num_vehicles):
        route_name = route_names[i % len(route_names)]
        route = ROUTES[route_name]
        base_t = (i / num_vehicles) * RELEASE_SPAN_S
        start_time = round(max(0.0, base_t + rng.uniform(-1.5, 1.5)), 1)
        profile = _build_profile(route, start_time, i)
        frames = _sample_profile(profile)
        if not frames:
            continue
        vehicles.append({"no": veh_no, "route": route_name, "start": start_time, "frames": frames})
        veh_no += 1
    return vehicles


# ---------------------------------------------------------------------------
# .fzp text rendering
# ---------------------------------------------------------------------------


def _fzp_rows(vehicles: list[dict]) -> list[tuple]:
    rows = []
    for v in vehicles:
        for f in v["frames"]:
            rows.append((f["t"], v["no"], f["link"], f["lane"], f["pos"], f["speed"]))
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows


def build_fzp_text(vehicles: list[dict]) -> str:
    rows = _fzp_rows(vehicles)
    lines = [
        "* Prism synthetic demo vehicle-trajectory export",
        "* Not captured from a live Vissim run -- generated by",
        "* tools/make_demo_trajectories.py for the Phase 3 P3 trajectory-playback",
        "* data layer. Header-driven column mapping per Vissim's .fzp convention",
        "* (see docs/phase2/DECISIONS.md's dated entry for this unit).",
        "*",
        f"* Vehicles: {len(vehicles)}   Window: {SIM_START:.1f}-{SIM_END:.1f}s   Resolution: {STEP:.1f}s",
        "*",
        "$VEHICLE:SIMSEC;NO;LINK;LANE;POS;SPEED",
    ]
    for t, no, link, lane, pos, speed in rows:
        lines.append(f"{t:.1f};{no};{link};{lane};{pos:.2f};{speed:.1f}")
    return "\n".join(lines) + "\n"


def write(out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "demo_trajectories.fzp"
    vehicles = generate_vehicles()
    out_path.write_text(build_fzp_text(vehicles), encoding="utf-8")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(REPO_ROOT / "data"), help="output directory (default: <repo>/data)")
    args = parser.parse_args()

    out_path = write(Path(args.out))
    vehicles = generate_vehicles()
    total_rows = sum(len(v["frames"]) for v in vehicles)
    routes_used = sorted({v["route"] for v in vehicles})
    print(
        f"wrote {out_path} ({out_path.stat().st_size} bytes, {len(vehicles)} vehicles, "
        f"{total_rows} rows, routes: {routes_used})"
    )


if __name__ == "__main__":
    main()

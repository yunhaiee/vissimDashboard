"""Q6 unit (1) PROBE: what can COM actually change about a link's GEOMETRY --
lane COUNT, polyline/length, curvature -- and what silently moves, dangles or
vanishes when it does?

Runs BEFORE any verb code, per the house rule (TOOL-CATALOG v2b bullet "Link
geometry edits"). Every earlier geometry unit created or removed WHOLE links
(Q4 unit (5) ``add_link``/``remove_link``/``add_connector``); nothing in this
codebase has ever edited an EXISTING link's shape, and two follow-ups were
recorded open by that unit -- curvature and z-coordinates.

Shape follows the house probe ``probe_vehicle_input_create.py`` (Q5 unit 3):
static corpus scan (no COM, no licence) -> static type-library scan -> live
COM parts, **one subprocess per part** (gap (1)'s validated fix: sequential
COM connect/disconnect cycles in ONE process segfault after ~2-3; each part
process holds at most 2 -- its own, plus ``tools_check._load_check``'s).
State crosses process boundaries through a JSON file, and **every number in
the FINDINGS below is written into that file by the probe itself**
(``runtime/link-geometry-probe/state.json``) so the claims here are
machine-checkable rather than transcribed by hand.

Parts (each network chosen by this file's own static scan [0]):

  a. ``Urban Intersection Beijing.CN`` -- the lane-count surface: is
     ``NumLanes`` writable? does ``ILaneContainer.AddLane`` work on an
     EXISTING link, and what does it do to the link's own centerline?
  b. ``Urban Intersection Beijing.CN`` -- ``RemoveLane`` on a CLEAN lane (no
     referrers), persisted + load oracle + shadow diff.
  c. ``Urban Intersection Beijing.CN`` -- ``RemoveLane`` on a lane that a
     just-created data-collection point and signal head still reference:
     refusal, dangle, or silent cascade-delete? (The three species this
     codebase has already catalogued -- Q4 units (2)/(5)/(7).)
  d. ``Urban Intersection Beijing.CN`` -- moving polyline points: does
     ``Length2D`` follow, what happens to a connector attached to the link,
     to conflict areas, and to an object anchored BEYOND the new end?
  e. ``Urban Intersection Beijing.CN`` -- the geometry SURFACE questions:
     can the point COUNT change at all (``WktLinestring`` as an attribute?
     container Add/Remove?), which per-point attributes exist and which are
     writable (curvature: ``rad``/``radiusSrc``/``radiusEffect``), and what
     ``ILink.RecalculateSpline`` does.
  f. ``Toll Plaza Camden.US`` (FEET) -- the display-unit cross-check the Q4
     unit (5) landmine demands for every new length-shaped surface: point
     X/Y read AND write, and ``AddLane``'s ``Width``.
  g. ``Urban Intersection Beijing.CN`` -- NULL CONTROL: load and save with
     ZERO edits. Every "the diff shows X moved" claim in a/b/d is only
     evidence because this part shows an untouched round-trip moves nothing.
  h. ``Urban Intersection Beijing.CN`` -- CURVATURE, the write direction: is
     ``Rad`` writable at all, does it need ``RadiusSrc='USERINPUT'`` first,
     and does a write with the source left CALCULATED silently vanish? (Part
     E measured only which per-point attributes EXIST; its own writability
     loop skipped ``Rad`` because a straight point reads it as ``None`` --
     recorded as a gap and closed here rather than left implied.)
  i. ``Urban Intersection Beijing.CN`` -- shortening a link whose CONNECTOR
     is anchored BEYOND the new end. Part D's link happened to carry its
     connector at pos 0.33 (near the start), so that part is NOT evidence
     about connector endpoints at all -- this one is.
  j. ``Toll Plaza Camden.US`` (FEET) -- the radius UNIT question Part H
     could not answer on a METERS network: is ``Rad`` display-unit-scaled
     like every other length-shaped COM value, or raw like the point
     coordinates? Read-only.

Hard rules honored: no git commands; no edits to ``services/`` from here
(this file is read/measure-only); work dirs confined to
``runtime/link-geometry-probe/``; every connection is
``attach_to_running=False``; example networks are COPIED into the work dir,
never opened in place; ``runtime/uploads/`` is never touched.

---------------------------------------------------------------------------
FINDINGS (live, 2026-08-03, PTV Vissim 2026 SP09 on this machine; all ten
parts PASS)
---------------------------------------------------------------------------

Every number below is written into ``runtime/link-geometry-probe/state.json``
by the probe itself; that file is the authority if this block ever drifts.

**A0. The whole surface, from the type library.**
``ILaneContainer.AddLane(Key, Width)`` / ``RemoveLane(Lane)``;
``ILinkContainer.AddLink`` / ``AddConnector`` / ``AddConnectorEx`` /
``DuplicateLink`` / ``GenerateOppositeDirection`` / ``RemoveLink``;
``ILink.InvertDirection`` / ``MoveLinkDuringSimulation`` /
``MoveLinkToPointDuringSimulation`` / ``RecalculateSpline`` /
``RecalculateSplineHeight`` / ``SplitLink``. And the decisive absence:
**``ILinkPolyPointContainer`` and ``ILinkPolyPointCollection`` expose NO
Add/Remove/Insert of any kind** -- only ``GetAll`` plus the bulk
``Set*AttValues``. Scanning the CONTAINER first is the house rule Q4 unit (5)
recorded after unit (3) missed ``AddLink`` entirely; here it is what proves a
negative rather than assuming one.

**A1. The lane count is NOT an attribute -- but the lanes are a container.**
``SetAttValue("NumLanes", 5)`` fails LOUDLY ("put_AttValue failed - the
attribute NUMLANES is not editable") and the lane index set is unchanged
after it (``[1, 2, 3, 4]``, judged by a ``Lanes.GetAll()`` Index-set diff, not
by the absence of an exception). ``AddLane``/``RemoveLane`` genuinely work on
an EXISTING link: Beijing link 4 ``[1,2,3,4] -> [1,2,3,4,5]``, link 8
``[1,2,3,4] -> [1,2,3]``. This CORRECTS the M0.3 catalog note "there is no
per-lane add/remove on an existing link" that
``constraints.lane_count_edit_unsupported`` was built on.

**A2. Every lane-count change silently MOVES the link's centreline by half
the lane width, perpendicular.** Measured three times, two networks, both
directions -- Vissim keeps a link's lanes centred on its polyline, so
changing the width re-centres the POLYLINE:

  ============================  ==================  =======================
  case                          lane width          persisted point delta
  ============================  ==================  =======================
  Beijing link 4, +1 lane       3.5 m               (-0.000606, +1.75) x2
  Beijing link 8, -1 lane       3.0 m               (-0.002331, +1.499998) x2
  Camden link 1, +1 lane        1.0668 m (3.5 ft)   |(-0.25139, +0.470445)| = 0.5334
  ============================  ==================  =======================

Deltas above are after-minus-before, re-derived from the persisted .inpx
pairs themselves. NOTE: the recorded 2026-08-03 ``state.json`` stores the
first two rows NEGATED -- ``_moved_links`` originally computed
before-minus-after (fixed since); magnitudes and every conclusion are
unaffected, but read that file's ``deltas`` with the sign flipped.

``Length2D`` never changes. On Camden the shift is a uniform translation of
the WHOLE polyline, which is also what explains that part's own X-write
readback exactly (25.408 requested, 25.15661 persisted, difference 0.25139 --
the same translation, not a failed write).

**A3. RemoveLane SILENTLY CASCADE-DELETES whatever is anchored on the lane.**
The taxonomy's third species (Q4 unit (7)'s ``RemovePTStop`` precedent), now
proven for lanes by a CONTROL/TREATMENT pair inside ONE session on Beijing
link 4 lane 4: a data-collection point (no 4203) and a signal head (no 61)
are created, the control save has **53 heads / 32 points** with both present
and carrying lane token ``"4 4"``, and the treatment save -- after a single
``RemoveLane`` -- has **52 / 31** with neither object existing at all. COM's
own ``ItemByKey`` says "object does not exist" for both immediately after the
call, nothing raises, and ``load_check`` on the treatment file returns
``load_ok``/``init_ok``: Vissim never objects to what it just destroyed.

**A4. The registry gap that cascade exposed.**
``LANE_TOKEN_REFERENCE_SITES`` did not know about ``dataCollectionPoint@lane``
-- the very object A3 watched get deleted. A corpus-wide scan of every
``"<int> <int>"``-shaped attribute across all 25 networks found SIX
unregistered LANE-token sites: ``pavementMarking@lane`` (815),
``desSpeedDecision@lane`` (584), ``parkingLot@lane`` (573),
``dataCollectionPoint@lane`` (222), ``ptStop@lane`` (189),
``vehicleAttributeDecision@lane`` (66). The same scan also reports
same-SHAPE attributes in a DIFFERENT token space -- ``signalHead@sg`` is
``"<sc> <sg>"`` (1264), ``timeIntervalVehVolume@timeInt`` is a time-interval
pair (1161), ``signalHead3D@assocSG*`` are 3D-signal references -- which are
deliberately NOT registered, separated by reading the scan rather than by a
silent filter.

**A5. Points are writable one coordinate at a time; the point COUNT is not
writable at all.** ``X``/``Y``/``ZOffset`` all take ``SetAttValue`` with a
genuine readback, and ``Length2D`` follows the polyline exactly (165.60570883
-> 82.80285441 m on a half-length write, ratio 0.5000). There is no
``WktLinestring``/``WKTLinestring``/``Wkt``/``WKT``/``Geometry`` attribute on
``ILink`` -- all five spellings raise "Could not find attribute" -- so WKT
exists ONLY as an ``AddLink``/``AddConnector`` argument, and combined with A0
that means **COM can reshape a link but never re-point it**.

**A6. Shortening a link silently RELOCATES everything anchored beyond the new
end.** Two independent measurements:

  - a data-collection point at pos 132.485 m on a link shortened from
    165.606 m to 82.803 m came back at pos **82.80285441921609** -- exactly
    the new end -- and persisted as ``pos="82.802854419216089"``;
  - Beijing link 5 (452.059 m) shortened to 226.030 m clamped **all three**
    connectors anchored at its far end (``FromPos`` 452.023 / 452.029 /
    452.059 -> 226.030 for 10006, 10008 and 10011) AND stretched their own
    geometry to reach:
    connector 10006's length went **11.71 m -> 237.70 m**, 10008's
    **12.88 -> 238.86 m**. A twelve-metre turn silently became a
    two-hundred-and-thirty-eight-metre one.

``load_check`` passes in both cases and the conflict-area count is unchanged
at 224. Nothing anywhere reports it. This is the flagship silent failure of
this unit.

*Honest scope note:* Part D's own link happened to carry its connector at pos
0.327 (near the START), so that part is NOT evidence about connector
endpoints at all -- Part I exists precisely because reading it as such would
have been the confident-wrong-answer failure mode this probe's own harness
notes lecture about.

**A7. Curvature is not spline data -- it is a per-point radius OVERRIDE, and
it is a two-step write.** Each point carries ``Rad`` + ``RadiusSrc`` +
``RadiusEffect`` (persisted as ``rad``/``radiusSrc``/``radiusEffect``);
``Index``/``No``/``Radius``/``RadSrc``/``RadEffect``/``Radii``/``Curvature``
do not exist. ``Rad`` is REFUSED LOUDLY while the source is ``CALCULATED``
("AttValue failed: Object : Attribute Radius cannot be changed") and becomes
writable after ``RadiusSrc='USERINPUT'`` -- 24292.235828170615 -> 222.0,
persisted ``rad="222" radiusSrc="USERINPUT"``, with x/y and ``Length2D``
unchanged. Corpus-wide, 16 082 of 60 420 points are already ``USERINPUT``
(44 338 CALCULATED), so this is a first-class Vissim feature rather than an
abuse. ``RecalculateSpline()`` is deliberately left unwrapped: it
REDISTRIBUTES the intermediate points (Beijing link 12's middle point moved
~30 m; spacing 27.4/87.5 -> 57.4/57.4) with no return value, and
``RecalculateSplineHeight()`` zeroed a ZOffset that had just been written --
both are geometry mutations disguised as recomputes.

**A8. Units: x/y are the exception, in BOTH directions -- and z is not.**
On Camden (``UnitLenShort='FEET'``) a point's COM ``X``/``Y`` equal the
persisted XML x/y EXACTLY (ratio 1.0 on every coordinate measured) and a
written ``X`` lands raw, confirming in the WRITE direction, on a FEET
network, the coordinate-space landmine Q4 unit (5) could only infer.
Everything else on this surface IS display-unit-scaled, each measured on the
same object: ``Length2D`` 200.09060700006594 against a 60.9876 m polyline
(ratio **3.280840** = 1/0.3048), ``AddLane(4, 3.5)`` passed RAW persisted
``width="1.0668"`` (3.5 FEET), ``Rad`` 940.9516827989281 against a persisted
``rad`` of 286.8020729171133 (same ratio), and ``ZOffset`` 22.001312335958005
against a persisted ``zOffset`` of 6.706 (same ratio). So on ONE point
object, x and y are raw metres while z is not.

**A9. The null control.** Beijing loaded and saved with ZERO edits: 0 links
moved (of 112, 530 polyline points), 0 lane-count changes, 0 connector
endpoint changes, and ``diff_shadow_models`` reports no differences at all.
Every diff attributed above is therefore attributable to its own write.

**A10. What each write does to the shadow model** (what the guardrail
exemption tables are derived from):

  - ``AddLane``: ``links`` field_changed x4 (lanes, lane_widths, points,
    width_total) + ``link_lanes`` ADDED (``4.5``) + ``conflict_areas`` ADDED
    (``905`` -- the widened link now overlaps a crossing movement);
  - ``RemoveLane``: the same four ``links`` fields + ``link_lanes`` REMOVED
    (``8.4``); no conflict-area change;
  - a point move: ``links`` field_changed (length, points) only -- and, when
    the link's own connectors get re-snapped, ``links`` rows for those
    CONNECTORS too (11 field_changed rows in the link-5 case).

Usage: ``python tools/probe_link_geometry.py`` -- runs every part end to end
(via internal per-part subprocesses) and prints one PASS/FAIL summary.
Advanced: ``--part {a..j} --state <path>`` runs exactly one part against a
persisted JSON state file (what the orchestrator shells out to); ``--only``
re-runs a comma-separated subset of parts without clearing prior state.
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
WORK_ROOT = BACKEND_ROOT.parent / "runtime" / "link-geometry-probe"

#: METERS-display network with clean multi-lane links (4 and 8: 4 lanes each,
#: ZERO lane-token referrers on their last lane -- this file's own static
#: scan [0]) and a busy signalized junction for the downstream-recompute
#: questions.
METERS_NET = EXAMPLES_ROOT / "Urban Intersection Beijing.CN"
#: FEET-display network -- the display-unit cross-check every new
#: length-shaped COM surface needs (Q4 unit (5) landmine 2).
FEET_NET = EXAMPLES_ROOT / "Toll Plaza Camden.US"

#: Beijing targets, from [0]: both are plain links with 4 lanes whose LAST
#: lane no element references (so a removal is not confounded by a refusal
#: this codebase's own spine would raise first).
CLEAN_LINK_A = 4
CLEAN_LINK_B = 8

_PART_LABELS = {
    "a": "Part A: lane-count surface -- NumLanes writable? AddLane on an existing link? centerline?",
    "b": "Part B: RemoveLane on a CLEAN lane -- persisted, load oracle, shadow diff",
    "c": "Part C: RemoveLane on a REFERENCED lane -- refusal, dangle, or silent cascade-delete?",
    "d": "Part D: moving polyline points -- length, connector endpoints, conflict areas, anchored objects",
    "e": "Part E: geometry SURFACE -- point-count change, per-point attributes, curvature, RecalculateSpline",
    "f": "Part F: FEET cross-check -- point X/Y and AddLane Width display-unit scaling",
    "g": "Part G: NULL CONTROL -- load + save with zero edits; nothing may move",
    "h": "Part H: CURVATURE writes -- is Rad writable, and does it need RadiusSrc=USERINPUT?",
    "i": "Part I: shortening a link whose CONNECTOR is anchored beyond the new end",
    "j": "Part J: FEET cross-check for Rad -- is the curve radius display-unit-scaled?",
}
_PART_ORDER = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]

_M_PER_FT = 0.3048


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

#: The real geometry element chain, re-confirmed by Q4 unit (5)'s own probe:
#: <link><geometry><linkPolyPts><linkPolyPoint x y zOffset radiusEffect
#: radiusSrc [rad]/></linkPolyPts></geometry>.
_POINT_XPATH = "geometry/linkPolyPts/linkPolyPoint"


def _link_elements(root: ET.Element) -> list[ET.Element]:
    return root.findall(".//links/link")


def _is_connector(link_el: ET.Element) -> bool:
    return link_el.find("fromLinkEndPt") is not None or link_el.find("toLinkEndPt") is not None


def static_lane_token_scan() -> dict[str, Any]:
    """Corpus-wide scan for EVERY element attribute whose value has the shape
    of Vissim's own ``"<link> <lane>"`` lane token, checked against
    ``services.inpx_structure.LANE_TOKEN_REFERENCE_SITES``.

    This is the "found by a corpus-wide attribute-name scan, not inferred
    from the schema" method Q4 unit (7) used to discover the
    ``detector.ptStop`` boarding gate. It exists here because Part C proves
    a lane removal SILENTLY CASCADE-DELETES whatever is anchored on the
    lane -- so any anchored object type the registry does not know about is
    a hole through which this codebase's own removal verbs destroy data
    without a word. NOTE the token shape ``"<int> <int>"`` is also used by
    non-lane references (a signal head's ``sg`` is ``"<sc> <sg>"``, a volume
    row's ``timeInt`` is a time-interval pair) -- those are reported too and
    separated by hand rather than silently filtered, so the filter's own
    judgment is visible.
    """
    import re

    token_re = re.compile(r"^\d+\s+\d+$")
    from services.inpx_structure import LANE_TOKEN_REFERENCE_SITES

    registered = {(s.xpath.rsplit("/", 1)[-1], s.attr) for s in LANE_TOKEN_REFERENCE_SITES}
    hits: dict[str, int] = {}
    example_path: dict[str, str] = {}

    def walk(element: ET.Element, chain: list[str]) -> None:
        chain = chain + [element.tag]
        for key, value in element.attrib.items():
            if value and token_re.match(value.strip()):
                label = f"{element.tag}@{key}"
                hits[label] = hits.get(label, 0) + 1
                example_path.setdefault(label, "/".join(chain[-4:]))
        for child in element:
            walk(child, chain)

    for inpx in sorted(EXAMPLES_ROOT.rglob("*.inpx")):
        try:
            root = ET.parse(inpx).getroot()
        except Exception:
            continue
        walk(root, [])

    _log("=" * 78)
    _log("[0b] STATIC corpus scan: every '<int> <int>'-shaped attribute vs the lane-token registry")
    _log("=" * 78)
    rows = []
    for label, count in sorted(hits.items(), key=lambda kv: -kv[1]):
        tag, _, attr = label.partition("@")
        is_registered = (tag, attr) in registered
        rows.append({"label": label, "count": count, "path": example_path[label],
                     "registered": is_registered})
        _log(f"      {label:42s} {count:7d}  {example_path[label]:58s} "
             f"{'REGISTERED' if is_registered else '*** NOT REGISTERED ***'}")
    return {"registered_sites": sorted(f"{t}@{a}" for t, a in registered), "rows": rows}


def static_corpus_scan() -> dict[str, Any]:
    """Two questions no COM call can answer more cheaply: what attributes does
    a real ``linkPolyPoint`` carry (i.e. what does Vissim itself consider part
    of a link's geometry), and how common is a NON-default curvature source?"""
    _log("=" * 78)
    _log("[0] STATIC corpus scan: linkPolyPoint attributes + radiusSrc across Examples Demo")
    _log("=" * 78)
    per_network: dict[str, Any] = {}
    attr_union: set[str] = set()
    radius_src_totals: dict[str, int] = {}
    for inpx in sorted(EXAMPLES_ROOT.rglob("*.inpx")):
        try:
            root = ET.parse(inpx).getroot()
        except Exception as exc:
            _log(f"      {inpx.parent.name}: PARSE FAILED ({type(exc).__name__}) -- skipped")
            continue
        points = root.findall(f".//{_POINT_XPATH}")
        attrs: dict[str, int] = {}
        srcs: dict[str, int] = {}
        for point in points:
            for key in point.attrib:
                attrs[key] = attrs.get(key, 0) + 1
            src = point.get("radiusSrc", "<absent>")
            srcs[src] = srcs.get(src, 0) + 1
            radius_src_totals[src] = radius_src_totals.get(src, 0) + 1
        attr_union |= set(attrs)
        plain = [l for l in _link_elements(root) if not _is_connector(l)]
        lane_counts: dict[int, int] = {}
        for link in plain:
            n = len(link.findall("lanes/lane"))
            lane_counts[n] = lane_counts.get(n, 0) + 1
        per_network[f"{inpx.parent.name}/{inpx.name}"] = {
            "points": len(points),
            "point_attrs": attrs,
            "radius_src": srcs,
            "plain_links": len(plain),
            "plain_link_lane_counts": {str(k): v for k, v in sorted(lane_counts.items())},
        }
    _log(f"      linkPolyPoint attribute names, union over the whole corpus: {sorted(attr_union)}")
    _log(f"      radiusSrc totals over the whole corpus: {radius_src_totals}")
    return {
        "per_network": per_network,
        "point_attr_union": sorted(attr_union),
        "radius_src_totals": radius_src_totals,
    }


def _static_link_shape(inpx: Path, link_no: int | str) -> dict[str, Any]:
    """One link's persisted geometry + lanes -- the cheap oracle every live
    measurement below is compared against."""
    root = ET.parse(inpx).getroot()
    for link in _link_elements(root):
        if link.get("no") == str(link_no):
            points = [dict(p.attrib) for p in link.findall(_POINT_XPATH)]
            lanes = [dict(l.attrib) for l in link.findall("lanes/lane")]
            return {
                "no": str(link_no),
                "is_connector": _is_connector(link),
                "point_count": len(points),
                "points": points,
                "lane_count": len(lanes),
                "lanes": lanes,
                "xy": [(float(p["x"]), float(p["y"])) for p in points],
            }
    return {"no": str(link_no), "missing": True}


def _polyline_length_m(xy: list[tuple[float, float]]) -> float:
    return sum(math.hypot(xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1]) for i in range(1, len(xy)))


def _all_lane_counts(inpx: Path) -> dict[str, int]:
    root = ET.parse(inpx).getroot()
    return {l.get("no", ""): len(l.findall("lanes/lane")) for l in _link_elements(root)}


def _all_point_xy(inpx: Path) -> dict[str, list[tuple[float, float]]]:
    root = ET.parse(inpx).getroot()
    return {
        l.get("no", ""): [(float(p.get("x", 0)), float(p.get("y", 0))) for p in l.findall(_POINT_XPATH)]
        for l in _link_elements(root)
    }


def _moved_links(before: dict[str, list], after: dict[str, list], tol: float = 1e-6) -> dict[str, Any]:
    """Which links' persisted geometry differs, and by how much -- the
    centerline-recompute question, answered over the WHOLE network rather than
    only the link that was written (a recompute that moves a NEIGHBOUR is
    exactly the kind of silent side effect this probe exists to find)."""
    moved: dict[str, Any] = {}
    for link_no, pts_before in before.items():
        pts_after = after.get(link_no)
        if pts_after is None:
            moved[link_no] = {"gone": True}
            continue
        if len(pts_before) != len(pts_after):
            moved[link_no] = {"point_count": [len(pts_before), len(pts_after)]}
            continue
        deltas = [
            (round(a[0] - b[0], 6), round(a[1] - b[1], 6))
            for b, a in zip(pts_before, pts_after)
            if abs(a[0] - b[0]) > tol or abs(a[1] - b[1]) > tol
        ]
        if deltas:
            moved[link_no] = {"deltas": deltas[:6], "moved_points": len(deltas)}
    for link_no in set(after) - set(before):
        moved[link_no] = {"new": True}
    return moved


def _anchored_objects(inpx: Path) -> dict[str, list[dict[str, str]]]:
    """Every lane-anchored / link-anchored object this codebase's own
    reference registries know about, with its raw lane token and pos -- so
    "the signal head vanished" / "its lane token now points at nothing" are
    both measurable claims rather than impressions."""
    root = ET.parse(inpx).getroot()
    out: dict[str, list[dict[str, str]]] = {}
    for name, xpath, keys in (
        ("signal_heads", ".//signalHeads/signalHead", ("no", "lane", "pos")),
        ("detectors", ".//detectors/detector", ("no", "lane", "pos")),
        ("data_collection_points", ".//dataCollectionPoints/dataCollectionPoint", ("no", "lane", "pos")),
        ("queue_counters", ".//queueCounters/queueCounter", ("no", "link", "pos")),
        ("stop_signs", ".//stopSigns/stopSign", ("no", "lane", "pos")),
        ("reduced_speed_areas", ".//reducedSpeedAreas/reducedSpeedArea", ("no", "lane", "pos")),
        ("conflict_areas", ".//conflictAreas/conflictArea", ("no", "linkA", "linkB", "status")),
    ):
        out[name] = [{k: (el.get(k) or "") for k in keys} for el in root.findall(xpath)]
    return out


def _connector_endpoints(inpx: Path) -> dict[str, dict[str, str]]:
    root = ET.parse(inpx).getroot()
    out: dict[str, dict[str, str]] = {}
    for link in _link_elements(root):
        if not _is_connector(link):
            continue
        from_el = link.find("fromLinkEndPt")
        to_el = link.find("toLinkEndPt")
        out[link.get("no", "")] = {
            "from_link": (from_el.get("link") if from_el is not None else "") or "",
            "from_pos": (from_el.get("pos") if from_el is not None else "") or "",
            "to_link": (to_el.get("link") if to_el is not None else "") or "",
            "to_pos": (to_el.get("pos") if to_el is not None else "") or "",
        }
    return out


def _scan_typelib_methods(keywords: tuple[str, ...]) -> dict[str, list[str]]:
    """Static type-library scan (no COM instance, no licence used) -- the
    house 'scan the CONTAINER first rather than assume' step Q4 unit (5)
    recorded after unit (3) missed ``ILinkContainer.AddLink`` entirely."""
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


def _lane_indices(link_obj: Any) -> list[int]:
    return sorted(int(l.AttValue("Index")) for l in link_obj.Lanes.GetAll())


def _lane_widths(link_obj: Any) -> dict[int, float]:
    return {int(l.AttValue("Index")): float(l.AttValue("Width")) for l in link_obj.Lanes.GetAll()}


def _com_points(link_obj: Any) -> list[tuple[float, float, float]]:
    out: list[tuple[float, float, float]] = []
    for point in link_obj.LinkPolyPts.GetAll():
        z = 0.0
        try:
            z = float(point.AttValue("ZOffset") or 0.0)
        except Exception:
            pass
        out.append((float(point.AttValue("X")), float(point.AttValue("Y")), z))
    return out


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
    """One SetAttValue attempt, judged by a readback (never by the bare absence
    of an exception -- ``AddPriorityRule``'s silent-no-op precedent). When
    ``read_after`` is a callable it is used instead of the attribute's own
    readback (for writes whose EFFECT lives somewhere else)."""
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
    _log(f"      SetAttValue({name!r}, {value!r}): before={before!r} after={after!r} "
         f"changed={changed}" + (f" error={error}" if error else ""))
    return {"attribute": name, "wrote": value, "before": before, "after": after,
            "changed": changed, "error": error}


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


# ---------------------------------------------------------------------------
# Part A: the lane-count surface.
# ---------------------------------------------------------------------------


def part_a(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.tools_network import build_shadow_model

    _log("=" * 78)
    _log(f"[A] {_PART_LABELS['a']}")
    _log("=" * 78)

    _log("\n[A.0] Static type-library scan: the link/lane/geometry CONTAINERS ...")
    signatures = _scan_typelib_methods(
        ("ilink", "ilinkcontainer", "ilanecontainer", "ilane", "ilinkpolypoint",
         "ilinkpolypointcontainer", "ilinkpolypointcollection")
    )

    working = _clone(METERS_NET, "lane-count")
    static_before = _static_link_shape(working, CLEAN_LINK_A)
    xy_before_all = _all_point_xy(working)
    lanes_before_all = _all_lane_counts(working)
    shadow_before = build_shadow_model(working)
    _log(f"\n    clone: {working}")
    _log(f"    target link {CLEAN_LINK_A} BEFORE (persisted): lanes={static_before['lane_count']} "
         f"points={static_before['point_count']} xy={static_before['xy']} "
         f"lane widths={[l.get('width') for l in static_before['lanes']]}")

    findings: dict[str, Any] = {
        "network": METERS_NET.name,
        "typelib": signatures,
        "target_link": CLEAN_LINK_A,
        "static_before": static_before,
        "polyline_length_before_m": _polyline_length_m(static_before["xy"]),
    }

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        link = net.Links.ItemByKey(CLEAN_LINK_A)

        unit = controller._net_unit_length()
        _log(f"\n[A.1] this network's display unit (UnitLenShort): {unit!r}")
        findings["unit_len_short"] = unit

        link_attrs = _read_attrs(
            link,
            ("No", "Name", "NumLanes", "Length2D", "Length3D", "IsConn", "WktLinestring",
             "Wkt", "LinkPolyPts", "LinkBehavType", "DisplayType", "Level", "Gradient"),
        )
        _log("\n[A.2] which LINK-level attributes exist at all (each read defensively):")
        for name, result in link_attrs.items():
            _log(f"      {name}: {result}")
        findings["link_attributes"] = link_attrs

        lanes_before = _lane_indices(link)
        widths_before = _lane_widths(link)
        points_before = _com_points(link)
        length_before = float(link.AttValue("Length2D"))
        _log(f"\n[A.3] live BEFORE: lane indices={lanes_before} widths={widths_before} "
             f"Length2D={length_before} points={points_before}")
        findings.update(
            {"lanes_before": lanes_before, "widths_before": widths_before,
             "com_points_before": points_before, "length2d_before": length_before}
        )

        # -- (1) is the lane COUNT a writable attribute in its own right?
        _log("\n[A.4] Is NumLanes writable? (judged by a Lanes.GetAll() index-set diff, "
             "never by the absence of an exception)")
        numlanes_write = _try_write(
            link, "NumLanes", len(lanes_before) + 1, read_after=lambda: len(_lane_indices(link))
        )
        lanes_after_numlanes = _lane_indices(link)
        numlanes_write["lane_indices_after"] = lanes_after_numlanes
        numlanes_write["created_a_lane"] = lanes_after_numlanes != lanes_before
        _log(f"      lane indices after the NumLanes write: {lanes_after_numlanes} "
             f"(created a lane = {numlanes_write['created_a_lane']})")
        findings["numlanes_write"] = numlanes_write

        # -- (2) the real container primitive.
        new_index = max(lanes_before) + 1
        width_m = 3.5
        width_com = controller._length_m_to_com(width_m, unit)
        _log(f"\n[A.5] ILaneContainer.AddLane(Key={new_index}, Width={width_com}) "
             f"(= {width_m} m in this network's display unit)")
        add_error = ""
        try:
            link.Lanes.AddLane(new_index, width_com)
        except Exception as exc:
            add_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            _log(f"      raised: {add_error}")
        lanes_after_add = _lane_indices(link)
        widths_after_add = _lane_widths(link)
        points_after_add = _com_points(link)
        length_after_add = float(link.AttValue("Length2D"))
        added = sorted(set(lanes_after_add) - set(lanes_before))
        _log(f"      lane index-set diff: {lanes_before} -> {lanes_after_add}  added={added}")
        _log(f"      widths after: {widths_after_add}")
        _log(f"      COM points after AddLane: {points_after_add}")
        _log(f"      Length2D: {length_before} -> {length_after_add}")
        findings.update(
            {
                "add_lane_error": add_error,
                "add_lane_key": new_index,
                "add_lane_width_com": width_com,
                "lanes_after_add": lanes_after_add,
                "widths_after_add": widths_after_add,
                "com_points_after_add": points_after_add,
                "length2d_after_add": length_after_add,
                "com_centerline_shift": [
                    (round(a[0] - b[0], 6), round(a[1] - b[1], 6))
                    for b, a in zip(points_before, points_after_add)
                ],
                "add_lane_created": bool(added),
            }
        )

        saved = working.with_name(working.stem + "_saved.inpx")
        controller.save_net_as(saved)
        _log(f"\n[A.6] saved: {saved}")
    finally:
        _finish(controller)
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    if saved is None:
        state["part_a"] = findings
        return False

    static_after = _static_link_shape(saved, CLEAN_LINK_A)
    xy_after_all = _all_point_xy(saved)
    lanes_after_all = _all_lane_counts(saved)
    moved = _moved_links(xy_before_all, xy_after_all)
    lane_count_changes = {
        k: [lanes_before_all.get(k), v] for k, v in lanes_after_all.items() if lanes_before_all.get(k) != v
    }
    _log(f"\n[A.7] persisted link {CLEAN_LINK_A}: lanes={static_after['lane_count']} "
         f"points={static_after['point_count']} xy={static_after['xy']} "
         f"widths={[l.get('width') for l in static_after['lanes']]}")
    _log(f"      NETWORK-WIDE lane-count changes: {lane_count_changes}")
    _log(f"      NETWORK-WIDE geometry moves: {len(moved)} link(s) -- "
         f"{dict(list(moved.items())[:6])}")
    findings.update(
        {
            "static_after": static_after,
            "persisted_lane_count_changes": lane_count_changes,
            "persisted_moved_links": moved,
            "persisted_moved_link_count": len(moved),
        }
    )

    shadow_after = build_shadow_model(saved)
    _log("\n[A.8] diff_shadow_models (the SAME machinery GuardrailEngine.postwrite runs):")
    findings["shadow_diff"] = _grouped_diff(shadow_before, shadow_after)

    state["part_a"] = findings
    return bool(findings["add_lane_created"]) and static_after["lane_count"] == len(lanes_before) + 1


# ---------------------------------------------------------------------------
# Part B: RemoveLane on a CLEAN lane.
# ---------------------------------------------------------------------------


def part_b(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.tools_check import _load_check
    from services.agent.tools_network import build_shadow_model

    _log("=" * 78)
    _log(f"[B] {_PART_LABELS['b']}")
    _log("=" * 78)

    working = _clone(METERS_NET, "lane-remove-clean")
    static_before = _static_link_shape(working, CLEAN_LINK_B)
    xy_before_all = _all_point_xy(working)
    lanes_before_all = _all_lane_counts(working)
    anchored_before = _anchored_objects(working)
    shadow_before = build_shadow_model(working)

    # The premise, stated before any COM call: nothing references this link's
    # LAST lane (otherwise a removal would be refused by this codebase's own
    # spine first, and this part would be measuring the wrong thing).
    from services.inpx_structure import find_lane_token_references

    root = ET.parse(working).getroot()
    last_index = static_before["lane_count"]
    referrers = find_lane_token_references(root, str(CLEAN_LINK_B), last_index)
    _log(f"\n    clone: {working}")
    _log(f"    target link {CLEAN_LINK_B}: {static_before['lane_count']} lanes; lane-token "
         f"referrers on its LAST lane ({CLEAN_LINK_B} {last_index}): {referrers}")

    findings: dict[str, Any] = {
        "network": METERS_NET.name,
        "target_link": CLEAN_LINK_B,
        "last_lane_index": last_index,
        "last_lane_referrers": referrers,
        "static_before": static_before,
        "anchored_before_counts": {k: len(v) for k, v in anchored_before.items()},
    }

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        link = net.Links.ItemByKey(CLEAN_LINK_B)
        lanes_before = _lane_indices(link)
        points_before = _com_points(link)
        length_before = float(link.AttValue("Length2D"))
        _log(f"\n[B.1] live BEFORE: lanes={lanes_before} Length2D={length_before} points={points_before}")

        lane_obj = link.Lanes.ItemByKey(last_index)
        remove_error = ""
        try:
            link.Lanes.RemoveLane(lane_obj)
        except Exception as exc:
            remove_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            _log(f"      RemoveLane raised: {remove_error}")
        lanes_after = _lane_indices(link)
        points_after = _com_points(link)
        length_after = float(link.AttValue("Length2D"))
        _log(f"\n[B.2] lane index-set diff: {lanes_before} -> {lanes_after} "
             f"removed={sorted(set(lanes_before) - set(lanes_after))}")
        _log(f"      Length2D {length_before} -> {length_after}; COM points {points_after}")
        findings.update(
            {
                "remove_error": remove_error,
                "lanes_before": lanes_before,
                "lanes_after": lanes_after,
                "removed": sorted(set(lanes_before) - set(lanes_after)),
                "length2d_before": length_before,
                "length2d_after": length_after,
                "com_points_before": points_before,
                "com_points_after": points_after,
                "com_centerline_shift": [
                    (round(a[0] - b[0], 6), round(a[1] - b[1], 6))
                    for b, a in zip(points_before, points_after)
                ],
            }
        )

        saved = working.with_name(working.stem + "_saved.inpx")
        controller.save_net_as(saved)
        _log(f"      saved: {saved}")
    finally:
        _finish(controller)
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    if saved is None:
        state["part_b"] = findings
        return False

    static_after = _static_link_shape(saved, CLEAN_LINK_B)
    lanes_after_all = _all_lane_counts(saved)
    lane_count_changes = {
        k: [lanes_before_all.get(k), v] for k, v in lanes_after_all.items() if lanes_before_all.get(k) != v
    }
    moved = _moved_links(xy_before_all, _all_point_xy(saved))
    anchored_after = _anchored_objects(saved)
    _log(f"\n[B.3] persisted link {CLEAN_LINK_B}: lanes={static_after['lane_count']} "
         f"xy={static_after['xy']}")
    _log(f"      NETWORK-WIDE lane-count changes: {lane_count_changes}")
    _log(f"      NETWORK-WIDE geometry moves: {len(moved)} -- {dict(list(moved.items())[:6])}")
    _log(f"      anchored-object counts BEFORE={findings['anchored_before_counts']} "
         f"AFTER={{{', '.join(f'{k!r}: {len(v)}' for k, v in anchored_after.items())}}}")
    findings.update(
        {
            "static_after": static_after,
            "persisted_lane_count_changes": lane_count_changes,
            "persisted_moved_links": moved,
            "anchored_after_counts": {k: len(v) for k, v in anchored_after.items()},
        }
    )

    shadow_after = build_shadow_model(saved)
    _log("\n[B.4] diff_shadow_models:")
    findings["shadow_diff"] = _grouped_diff(shadow_before, shadow_after)

    load_result = _load_check(saved)
    _log(f"\n[B.5] load_check on the saved file: {load_result}")
    findings["load_check"] = load_result

    state["part_b"] = findings
    return (
        static_after["lane_count"] == static_before["lane_count"] - 1
        and bool(load_result.get("load_ok"))
        and bool(load_result.get("init_ok"))
    )


# ---------------------------------------------------------------------------
# Part C: RemoveLane on a REFERENCED lane -- the dangle question.
# ---------------------------------------------------------------------------


def part_c(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.tools_check import _load_check

    _log("=" * 78)
    _log(f"[C] {_PART_LABELS['c']}")
    _log("=" * 78)

    working = _clone(METERS_NET, "lane-remove-referenced")
    static_before = _static_link_shape(working, CLEAN_LINK_A)
    last_index = static_before["lane_count"]
    _log(f"\n    clone: {working}")
    _log(f"    target: link {CLEAN_LINK_A} lane {last_index} -- a lane NOTHING references today "
         "(Part B's premise), onto which this part FIRST places a data-collection point and a "
         "signal head, so the removal below has a genuine referrer to dangle.")

    findings: dict[str, Any] = {
        "network": METERS_NET.name,
        "target_link": CLEAN_LINK_A,
        "target_lane": last_index,
        "static_before": static_before,
    }

    pythoncom.CoInitialize()
    controller = _connect(working)
    control_saved: Path | None = None
    treatment_saved: Path | None = None
    dcp: dict[str, Any] = {}
    head: dict[str, Any] = {}
    remove_error = ""
    try:
        net = controller.vissim.Net

        # -- an existing signal group to hang the head on (Beijing has 52 heads).
        sc_no = sg_no = None
        for sc in net.SignalControllers.GetAll():
            groups = list(sc.SGs.GetAll())
            if groups:
                sc_no = int(sc.AttValue("No"))
                sg_no = int(groups[0].AttValue("No"))
                break
        _log(f"\n[C.1] donor signal group for the head: SC={sc_no} SG={sg_no}")
        findings["sc_no"] = sc_no
        findings["sg_no"] = sg_no

        dcp = controller.add_data_collection_point(CLEAN_LINK_A, last_index, 20.0, "probe DCP")
        head = controller.add_signal_head(CLEAN_LINK_A, last_index, 30.0, sc_no, sg_no, "probe head")
        _log(f"      created data-collection point {dcp} and signal head {head} on lane "
             f"{CLEAN_LINK_A}-{last_index}")
        findings["created_dcp"] = dcp
        findings["created_head"] = head

        # -- the CONTROL save: both objects exist, no lane removed. Without it,
        #    "the head disappeared" could not be distinguished from "the head
        #    was never really created".
        control_saved = working.with_name(working.stem + "_control.inpx")
        controller.save_net_as(control_saved)
        _log(f"      CONTROL saved (objects created, NO lane removed): {control_saved}")

        link = net.Links.ItemByKey(CLEAN_LINK_A)
        lanes_before = _lane_indices(link)
        lane_obj = link.Lanes.ItemByKey(last_index)
        remove_error = ""
        try:
            link.Lanes.RemoveLane(lane_obj)
        except Exception as exc:
            remove_error = f"{type(exc).__name__}: {str(exc)[:300]}"
            _log(f"\n[C.2] RemoveLane on the REFERENCED lane raised: {remove_error}")
        lanes_after = _lane_indices(link)
        _log(f"[C.2] lane index-set diff: {lanes_before} -> {lanes_after}")
        findings.update(
            {"remove_error": remove_error, "lanes_before": lanes_before, "lanes_after": lanes_after,
             "lane_actually_removed": lanes_after != lanes_before}
        )

        # -- what does COM itself now say about the two objects?
        live_after: dict[str, Any] = {}
        for label, collection, key in (
            ("dcp", net.DataCollectionPoints, dcp.get("no")),
            ("signal_head", net.SignalHeads, head.get("no")),
        ):
            try:
                obj = collection.ItemByKey(int(key))
                live_after[label] = {
                    "exists": True,
                    "lane": str(obj.AttValue("Lane")),
                    "pos": float(obj.AttValue("Pos")),
                }
            except Exception as exc:
                live_after[label] = {"exists": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
        _log(f"[C.3] the two objects, as COM sees them AFTER the removal: {live_after}")
        findings["live_objects_after_removal"] = live_after

        treatment_saved = working.with_name(working.stem + "_treatment.inpx")
        controller.save_net_as(treatment_saved)
        _log(f"      TREATMENT saved: {treatment_saved}")
    finally:
        _finish(controller)
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    if control_saved is None or treatment_saved is None:
        state["part_c"] = findings
        return False

    control_objects = _anchored_objects(control_saved)
    treatment_objects = _anchored_objects(treatment_saved)
    control_lanes = _all_lane_counts(control_saved)
    treatment_lanes = _all_lane_counts(treatment_saved)
    _log(f"\n[C.4] CONTROL  : lanes on link {CLEAN_LINK_A} = {control_lanes.get(str(CLEAN_LINK_A))}; "
         f"heads={len(control_objects['signal_heads'])} dcps={len(control_objects['data_collection_points'])}")
    _log(f"      TREATMENT: lanes on link {CLEAN_LINK_A} = {treatment_lanes.get(str(CLEAN_LINK_A))}; "
         f"heads={len(treatment_objects['signal_heads'])} dcps={len(treatment_objects['data_collection_points'])}")

    def _by_no(rows: list[dict[str, str]], no: str) -> dict[str, str] | None:
        return next((r for r in rows if r.get("no") == str(no)), None)

    control_head = _by_no(control_objects["signal_heads"], head.get("no"))
    treatment_head = _by_no(treatment_objects["signal_heads"], head.get("no"))
    control_dcp = _by_no(control_objects["data_collection_points"], dcp.get("no"))
    treatment_dcp = _by_no(treatment_objects["data_collection_points"], dcp.get("no"))
    _log(f"      created head  : control={control_head} treatment={treatment_head}")
    _log(f"      created DCP   : control={control_dcp} treatment={treatment_dcp}")
    findings.update(
        {
            "control_lane_count": control_lanes.get(str(CLEAN_LINK_A)),
            "treatment_lane_count": treatment_lanes.get(str(CLEAN_LINK_A)),
            "control_head": control_head,
            "treatment_head": treatment_head,
            "control_dcp": control_dcp,
            "treatment_dcp": treatment_dcp,
            "head_survived": treatment_head is not None,
            "dcp_survived": treatment_dcp is not None,
            "control_counts": {k: len(v) for k, v in control_objects.items()},
            "treatment_counts": {k: len(v) for k, v in treatment_objects.items()},
        }
    )

    load_result = _load_check(treatment_saved)
    _log(f"\n[C.5] load_check on the TREATMENT file (does Vissim itself object to whatever "
         f"state the removal left behind?): {load_result}")
    findings["load_check_treatment"] = load_result

    state["part_c"] = findings
    return bool(findings.get("lane_actually_removed")) or bool(remove_error)


# ---------------------------------------------------------------------------
# Part D: moving polyline points.
# ---------------------------------------------------------------------------


def part_d(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.tools_check import _load_check
    from services.agent.tools_network import build_shadow_model

    _log("=" * 78)
    _log(f"[D] {_PART_LABELS['d']}")
    _log("=" * 78)

    working = _clone(METERS_NET, "geometry-move")
    static_before = _static_link_shape(working, CLEAN_LINK_A)
    xy_before_all = _all_point_xy(working)
    endpoints_before = _connector_endpoints(working)
    anchored_before = _anchored_objects(working)
    shadow_before = build_shadow_model(working)

    xy = static_before["xy"]
    length_before_m = _polyline_length_m(xy)
    # Halve the link by pulling its LAST point back along the polyline -- a
    # length change big enough that an object anchored near the old end lands
    # BEYOND the new end, which is the silent-failure question this part is
    # really about.
    first, last = xy[0], xy[-1]
    target_last = (first[0] + (last[0] - first[0]) * 0.5, first[1] + (last[1] - first[1]) * 0.5)
    _log(f"\n    clone: {working}")
    _log(f"    link {CLEAN_LINK_A}: {static_before['point_count']} points, polyline length "
         f"{length_before_m:.3f} m; plan = move the LAST point from {last} to {target_last} "
         f"(target length {length_before_m / 2:.3f} m)")

    findings: dict[str, Any] = {
        "network": METERS_NET.name,
        "target_link": CLEAN_LINK_A,
        "static_before": static_before,
        "polyline_length_before_m": length_before_m,
        "planned_last_point": list(target_last),
    }

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    dcp: dict[str, Any] = {}
    try:
        net = controller.vissim.Net
        link = net.Links.ItemByKey(CLEAN_LINK_A)
        length2d_before = float(link.AttValue("Length2D"))
        com_points_before = _com_points(link)
        _log(f"\n[D.1] live BEFORE: Length2D={length2d_before} COM points={com_points_before}")

        # An object anchored NEAR THE OLD END, deliberately beyond the planned
        # new end -- created here so the "what happens to it" question has a
        # controlled subject rather than depending on what the example ships.
        anchor_pos_m = length_before_m * 0.8
        dcp = controller.add_data_collection_point(CLEAN_LINK_A, 1, anchor_pos_m, "probe far DCP")
        _log(f"      anchored a data-collection point at pos {anchor_pos_m:.3f} m "
             f"(= 80% of the OLD length, i.e. beyond the planned new end): {dcp}")
        findings["anchor_pos_m"] = anchor_pos_m
        findings["anchor_dcp"] = dcp

        points = list(link.LinkPolyPts.GetAll())
        last_point = points[-1]
        write_x = _try_write(last_point, "X", target_last[0])
        write_y = _try_write(last_point, "Y", target_last[1])
        com_points_after = _com_points(link)
        length2d_after = float(link.AttValue("Length2D"))
        _log(f"\n[D.2] after moving the last point: COM points={com_points_after}")
        _log(f"      Length2D {length2d_before} -> {length2d_after} "
             f"(ratio {length2d_after / length2d_before if length2d_before else float('nan'):.4f})")
        findings.update(
            {
                "write_x": write_x,
                "write_y": write_y,
                "com_points_before": com_points_before,
                "com_points_after": com_points_after,
                "length2d_before": length2d_before,
                "length2d_after": length2d_after,
                "length2d_follows_geometry": abs(length2d_after - length_before_m / 2) < 0.5,
            }
        )

        # What does COM say about the anchored object now that its Pos is past
        # the end of the link?
        try:
            dcp_obj = net.DataCollectionPoints.ItemByKey(int(dcp["no"]))
            findings["anchor_after_com"] = {
                "exists": True,
                "lane": str(dcp_obj.AttValue("Lane")),
                "pos": float(dcp_obj.AttValue("Pos")),
            }
        except Exception as exc:
            findings["anchor_after_com"] = {"exists": False, "error": f"{type(exc).__name__}: {exc}"}
        _log(f"[D.3] the anchored object as COM sees it after the shortening: "
             f"{findings['anchor_after_com']}")

        saved = working.with_name(working.stem + "_saved.inpx")
        controller.save_net_as(saved)
        _log(f"      saved: {saved}")
    finally:
        _finish(controller)
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    if saved is None:
        state["part_d"] = findings
        return False

    static_after = _static_link_shape(saved, CLEAN_LINK_A)
    moved = _moved_links(xy_before_all, _all_point_xy(saved))
    endpoints_after = _connector_endpoints(saved)
    endpoint_changes = {
        k: [endpoints_before.get(k), v] for k, v in endpoints_after.items() if endpoints_before.get(k) != v
    }
    anchored_after = _anchored_objects(saved)
    conflict_before = {c["no"] for c in anchored_before["conflict_areas"]}
    conflict_after = {c["no"] for c in anchored_after["conflict_areas"]}
    _log(f"\n[D.4] persisted link {CLEAN_LINK_A}: xy={static_after['xy']} "
         f"(persisted polyline length {_polyline_length_m(static_after['xy']):.3f} m)")
    _log(f"      NETWORK-WIDE geometry moves: {len(moved)} -- {dict(list(moved.items())[:8])}")
    _log(f"      connector endpoint changes: {len(endpoint_changes)} -- "
         f"{dict(list(endpoint_changes.items())[:6])}")
    _log(f"      conflict areas: {len(conflict_before)} -> {len(conflict_after)} "
         f"(dropped={sorted(conflict_before - conflict_after)[:8]} "
         f"new={sorted(conflict_after - conflict_before)[:8]})")
    findings.update(
        {
            "static_after": static_after,
            "persisted_polyline_length_m": _polyline_length_m(static_after["xy"]),
            "persisted_moved_links": moved,
            "persisted_moved_link_count": len(moved),
            "connector_endpoint_changes": endpoint_changes,
            "conflict_area_before": len(conflict_before),
            "conflict_area_after": len(conflict_after),
            "conflict_area_dropped": sorted(conflict_before - conflict_after),
            "conflict_area_new": sorted(conflict_after - conflict_before),
            "anchor_persisted": next(
                (r for r in anchored_after["data_collection_points"] if r["no"] == str(dcp.get("no"))),
                None,
            ),
        }
    )
    _log(f"      the anchored object in the SAVED file: {findings['anchor_persisted']}")

    shadow_after = build_shadow_model(saved)
    _log("\n[D.5] diff_shadow_models:")
    findings["shadow_diff"] = _grouped_diff(shadow_before, shadow_after)

    load_result = _load_check(saved)
    _log(f"\n[D.6] load_check on the saved file: {load_result}")
    findings["load_check"] = load_result

    state["part_d"] = findings
    return bool(findings.get("write_x", {}).get("changed"))


# ---------------------------------------------------------------------------
# Part E: the geometry SURFACE -- point count, curvature, spline.
# ---------------------------------------------------------------------------


def part_e(state: dict[str, Any]) -> bool:
    import pythoncom

    _log("=" * 78)
    _log(f"[E] {_PART_LABELS['e']}")
    _log("=" * 78)

    working = _clone(METERS_NET, "geometry-surface")
    # A link with THREE persisted points (so a middle point exists) -- from
    # this file's own static scan of the clone, not assumed.
    root = ET.parse(working).getroot()
    multi_point = next(
        (
            l.get("no")
            for l in _link_elements(root)
            if not _is_connector(l) and len(l.findall(_POINT_XPATH)) >= 3
        ),
        None,
    )
    _log(f"\n    clone: {working}")
    _log(f"    multi-point plain link chosen from the clone's own XML: {multi_point}")

    findings: dict[str, Any] = {"network": METERS_NET.name, "multi_point_link": multi_point}

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        link = net.Links.ItemByKey(int(multi_point))
        points = list(link.LinkPolyPts.GetAll())
        _log(f"\n[E.1] link {multi_point} has {len(points)} COM polyline points")

        # -- which per-point attributes exist at all?
        candidates = (
            "X", "Y", "ZOffset", "Index", "No", "Rad", "Radius", "RadiusSrc", "RadSrc",
            "RadiusEffect", "RadEffect", "Radii", "Curvature",
        )
        point_attrs = _read_attrs(points[1], candidates)
        _log("[E.2] per-point attribute existence (middle point), each read defensively:")
        for name, result in point_attrs.items():
            _log(f"      {name}: {result}")
        findings["point_attributes"] = point_attrs

        # -- writability of every one that EXISTS.
        writable: dict[str, Any] = {}
        for name, result in point_attrs.items():
            if "error" in result or name in {"X", "Y"}:
                continue
            value = result["value"]
            probe_value: Any
            if isinstance(value, (int, float)):
                probe_value = float(value) + 5.0 if value is not None else 5.0
            elif name.lower().endswith("src"):
                probe_value = "USERINPUT"
            elif name.lower().endswith("effect"):
                probe_value = "NOEFFECT"
            else:
                continue
            writable[name] = _try_write(points[1], name, probe_value)
        _log(f"[E.3] writability of the non-coordinate per-point attributes: "
             f"{ {k: v['changed'] for k, v in writable.items()} }")
        findings["point_attribute_writes"] = writable

        # -- can the point COUNT change at all?
        container = link.LinkPolyPts
        container_methods = sorted(
            name for name in dir(container)
            if not name.startswith("_") and any(k in name.lower() for k in ("add", "remove", "insert", "set"))
        )
        _log(f"\n[E.4] LinkPolyPts container's own add/remove/set-shaped members: {container_methods}")
        findings["polypt_container_members"] = container_methods

        wkt_read = _read_attrs(link, ("WktLinestring", "WKTLinestring", "Wkt", "WKT", "Geometry"))
        _log(f"      link-level WKT-shaped attributes: {wkt_read}")
        findings["wkt_attributes"] = wkt_read

        wkt_write: dict[str, Any] = {}
        for name, result in wkt_read.items():
            if "error" in result:
                continue
            xy = [(p[0], p[1]) for p in _com_points(link)]
            extended = xy[:-1] + [((xy[-2][0] + xy[-1][0]) / 2, (xy[-2][1] + xy[-1][1]) / 2), xy[-1]]
            new_wkt = "LINESTRING (" + ", ".join(f"{x} {y}" for x, y in extended) + ")"
            wkt_write[name] = _try_write(
                link, name, new_wkt, read_after=lambda: len(list(link.LinkPolyPts.GetAll()))
            )
            wkt_write[name]["point_count_after"] = len(list(link.LinkPolyPts.GetAll()))
            wkt_write[name]["requested_point_count"] = len(extended)
        _log(f"      WKT write attempts (point count is the judge): "
             f"{ {k: (v['point_count_after'], v['requested_point_count']) for k, v in wkt_write.items()} }")
        findings["wkt_writes"] = wkt_write

        # -- the spline methods the type library advertises on ILink.
        spline: dict[str, Any] = {}
        for method in ("RecalculateSpline", "RecalculateSplineHeight"):
            before_points = _com_points(link)
            error = ""
            try:
                getattr(link, method)()
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
            after_points = _com_points(link)
            spline[method] = {
                "error": error,
                "point_count_before": len(before_points),
                "point_count_after": len(after_points),
                "points_changed": before_points != after_points,
            }
            _log(f"[E.5] {method}(): {spline[method]}")
        findings["spline_methods"] = spline

        saved = working.with_name(working.stem + "_saved.inpx")
        controller.save_net_as(saved)
        _log(f"      saved: {saved}")
    finally:
        _finish(controller)
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    if saved is not None:
        after = _static_link_shape(saved, multi_point)
        _log(f"\n[E.6] persisted link {multi_point} points: {after['points']}")
        findings["static_after"] = after

    state["part_e"] = findings
    return "error" not in findings["point_attributes"].get("X", {"error": 1})


# ---------------------------------------------------------------------------
# Part F: FEET cross-check.
# ---------------------------------------------------------------------------


def part_f(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.tools_check import _load_check

    _log("=" * 78)
    _log(f"[F] {_PART_LABELS['f']}")
    _log("=" * 78)

    working = _clone(FEET_NET, "feet-units")
    root = ET.parse(working).getroot()
    target = next(
        (
            l.get("no")
            for l in _link_elements(root)
            if not _is_connector(l) and len(l.findall("lanes/lane")) >= 2
            and len(l.findall(_POINT_XPATH)) >= 2
        ),
        None,
    )
    static_before = _static_link_shape(working, target)
    _log(f"\n    clone: {working}")
    _log(f"    target link {target}: persisted xy={static_before['xy']} "
         f"lane widths={[l.get('width') for l in static_before['lanes']]}")

    findings: dict[str, Any] = {
        "network": FEET_NET.name,
        "target_link": target,
        "static_before": static_before,
    }

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        unit = controller._net_unit_length()
        _log(f"\n[F.1] UnitLenShort = {unit!r} (the premise of this whole part)")
        findings["unit_len_short"] = unit

        link = net.Links.ItemByKey(int(target))
        com_points = _com_points(link)
        xml_xy = static_before["xy"]
        # THE read-direction question: is a point's COM X/Y the same number the
        # saved file carries (raw metres), or display-unit-scaled?
        ratios = [
            (round(c[0] / x[0], 6) if x[0] else None, round(c[1] / x[1], 6) if x[1] else None)
            for c, x in zip(com_points, xml_xy)
        ]
        _log(f"[F.2] COM points  : {com_points}")
        _log(f"      XML xy      : {xml_xy}")
        _log(f"      COM/XML ratio per coordinate: {ratios}  (1.0 = raw metres; "
             f"{1 / _M_PER_FT:.4f} = display-unit-scaled FEET)")
        findings.update({"com_points_read": com_points, "xml_xy": xml_xy, "read_ratios": ratios})

        length2d = float(link.AttValue("Length2D"))
        xml_length = _polyline_length_m(xml_xy)
        _log(f"      Length2D (COM) = {length2d}; polyline length from the XML = {xml_length:.4f} m; "
             f"ratio = {length2d / xml_length if xml_length else float('nan'):.6f}")
        findings.update({"length2d": length2d, "xml_polyline_length_m": xml_length,
                         "length_ratio": length2d / xml_length if xml_length else None})

        # THE write-direction question.
        points = list(link.LinkPolyPts.GetAll())
        probe_x = com_points[-1][0] + 100.0
        write = _try_write(points[-1], "X", probe_x)
        findings["write_last_x"] = write
        findings["write_last_x_requested"] = probe_x

        # AddLane's Width, the other length-shaped argument on this surface.
        lanes_before = _lane_indices(link)
        new_index = max(lanes_before) + 1
        raw_width = 3.5  # deliberately passed RAW (unconverted)
        add_error = ""
        try:
            link.Lanes.AddLane(new_index, raw_width)
        except Exception as exc:
            add_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        lanes_after = _lane_indices(link)
        widths_after = _lane_widths(link)
        _log(f"[F.3] AddLane({new_index}, {raw_width}) passed RAW: lanes {lanes_before} -> "
             f"{lanes_after}; widths={widths_after}  error={add_error!r}")
        findings.update(
            {
                "add_lane_raw_width": raw_width,
                "add_lane_error": add_error,
                "lanes_before": lanes_before,
                "lanes_after": lanes_after,
                "widths_after_com": widths_after,
            }
        )

        saved = working.with_name(working.stem + "_saved.inpx")
        controller.save_net_as(saved)
        _log(f"      saved: {saved}")
    finally:
        _finish(controller)
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    if saved is None:
        state["part_f"] = findings
        return False

    static_after = _static_link_shape(saved, target)
    saved_last_x = static_after["xy"][-1][0] if static_after["xy"] else None
    saved_widths = [l.get("width") for l in static_after["lanes"]]
    _log(f"\n[F.4] persisted xy after the write: {static_after['xy']}")
    _log(f"      requested X (via COM) = {findings['write_last_x_requested']}; "
         f"persisted x = {saved_last_x}  ratio = "
         f"{saved_last_x / findings['write_last_x_requested'] if saved_last_x else float('nan'):.6f}")
    _log(f"      persisted lane widths = {saved_widths} (the new lane is the last one; "
         f"{3.5 * _M_PER_FT:.4f} would mean the raw 3.5 was read as FEET)")
    findings.update(
        {
            "static_after": static_after,
            "persisted_last_x": saved_last_x,
            "write_ratio": (saved_last_x / findings["write_last_x_requested"]) if saved_last_x else None,
            "persisted_lane_widths": saved_widths,
        }
    )

    load_result = _load_check(saved)
    _log(f"\n[F.5] load_check on the saved file: {load_result}")
    findings["load_check"] = load_result

    state["part_f"] = findings
    return bool(findings.get("write_last_x", {}).get("changed"))


# ---------------------------------------------------------------------------
# Part G: the NULL CONTROL.
# ---------------------------------------------------------------------------


def part_g(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.tools_network import build_shadow_model

    _log("=" * 78)
    _log(f"[G] {_PART_LABELS['g']}")
    _log("=" * 78)

    working = _clone(METERS_NET, "null-control")
    xy_before = _all_point_xy(working)
    lanes_before = _all_lane_counts(working)
    endpoints_before = _connector_endpoints(working)
    shadow_before = build_shadow_model(working)
    _log(f"\n    clone: {working}")
    _log(f"    BEFORE: {len(lanes_before)} links, "
         f"{sum(len(v) for v in xy_before.values())} polyline points total")

    findings: dict[str, Any] = {"network": METERS_NET.name}

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        saved = working.with_name(working.stem + "_saved.inpx")
        controller.save_net_as(saved)
        _log(f"    loaded and saved with ZERO edits: {saved}")
    finally:
        _finish(controller)
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    if saved is None:
        state["part_g"] = findings
        return False

    xy_after = _all_point_xy(saved)
    lanes_after = _all_lane_counts(saved)
    endpoints_after = _connector_endpoints(saved)
    moved = _moved_links(xy_before, xy_after)
    lane_changes = {
        k: [lanes_before.get(k), v] for k, v in lanes_after.items() if lanes_before.get(k) != v
    }
    endpoint_changes = {
        k: [endpoints_before.get(k), v] for k, v in endpoints_after.items() if endpoints_before.get(k) != v
    }
    _log(f"\n[G.1] links whose geometry moved on an untouched round-trip: {len(moved)} "
         f"-- {dict(list(moved.items())[:6])}")
    _log(f"      lane-count changes: {lane_changes}")
    _log(f"      connector endpoint changes: {len(endpoint_changes)}")
    shadow_after = build_shadow_model(saved)
    _log("[G.2] diff_shadow_models on an untouched round-trip:")
    diff = _grouped_diff(shadow_before, shadow_after)
    findings.update(
        {
            "moved_links": moved,
            "moved_link_count": len(moved),
            "lane_changes": lane_changes,
            "endpoint_changes": endpoint_changes,
            "endpoint_change_count": len(endpoint_changes),
            "shadow_diff": diff,
        }
    )
    state["part_g"] = findings
    return not moved and not lane_changes and diff["total"] == 0


# ---------------------------------------------------------------------------
# Part H: curvature, the WRITE direction.
# ---------------------------------------------------------------------------


def part_h(state: dict[str, Any]) -> bool:
    import pythoncom

    _log("=" * 78)
    _log(f"[H] {_PART_LABELS['h']}")
    _log("=" * 78)

    working = _clone(METERS_NET, "curvature")
    root = ET.parse(working).getroot()
    # A link that GENUINELY curves: an intermediate point carrying a real
    # ``rad`` attribute in the persisted file. Chosen from the clone's own XML,
    # never assumed -- a straight link's points carry no rad at all, and
    # measuring "Rad is unwritable" on one of those would be a confident wrong
    # answer about curvature rather than a measurement of it.
    curved_no: str | None = None
    curved_points: list[dict[str, str]] = []
    for link in _link_elements(root):
        if _is_connector(link):
            continue
        points = [dict(p.attrib) for p in link.findall(_POINT_XPATH)]
        if len(points) >= 3 and any("rad" in p for p in points[1:-1]):
            curved_no = link.get("no")
            curved_points = points
            break
    _log(f"\n    clone: {working}")
    _log(f"    curved plain link chosen from the clone's own XML: {curved_no} "
         f"({len(curved_points)} points; persisted rad values="
         f"{[p.get('rad') for p in curved_points]})")

    findings: dict[str, Any] = {
        "network": METERS_NET.name,
        "curved_link": curved_no,
        "persisted_points_before": curved_points,
    }
    if curved_no is None:
        state["part_h"] = findings
        return False

    probe_rad_no_src = 111.0
    probe_rad_with_src = 222.0

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        link = net.Links.ItemByKey(int(curved_no))
        points = list(link.LinkPolyPts.GetAll())
        live_before = [_read_attrs(p, ("X", "Y", "Rad", "RadiusSrc", "RadiusEffect")) for p in points]
        _log(f"\n[H.1] live per-point curvature BEFORE:")
        for index, row in enumerate(live_before):
            _log(f"      point {index}: Rad={row['Rad']} RadiusSrc={row['RadiusSrc']} "
                 f"RadiusEffect={row['RadiusEffect']}")
        findings["live_before"] = live_before

        # -- (1) write Rad with the source left CALCULATED. The silent-failure
        #    question: accepted-and-ignored, or accepted-and-kept?
        target = points[1]
        write_no_src = _try_write(target, "Rad", probe_rad_no_src)
        findings["write_rad_source_calculated"] = write_no_src

        # -- (2) the same write AFTER switching the source to USERINPUT.
        write_src = _try_write(target, "RadiusSrc", "USERINPUT")
        write_with_src = _try_write(target, "Rad", probe_rad_with_src)
        findings["write_radius_src"] = write_src
        findings["write_rad_source_userinput"] = write_with_src

        length_after = float(link.AttValue("Length2D"))
        com_points_after = _com_points(link)
        _log(f"\n[H.2] Length2D after the curvature writes: {length_after}; "
             f"points unchanged = {com_points_after == [(p['X']['value'], p['Y']['value'], 0.0) for p in live_before]}")
        findings["length2d_after"] = length_after
        findings["com_points_after"] = com_points_after

        saved = working.with_name(working.stem + "_saved.inpx")
        controller.save_net_as(saved)
        _log(f"      saved: {saved}")
    finally:
        _finish(controller)
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    if saved is None:
        state["part_h"] = findings
        return False

    after = _static_link_shape(saved, curved_no)
    _log(f"\n[H.3] persisted points AFTER: {after['points']}")
    persisted_target = after["points"][1] if len(after["points"]) > 1 else {}
    _log(f"      the written point persisted as: rad={persisted_target.get('rad')!r} "
         f"radiusSrc={persisted_target.get('radiusSrc')!r} "
         f"(requested {probe_rad_with_src} with source USERINPUT)")
    findings.update(
        {
            "persisted_points_after": after["points"],
            "persisted_target_point": persisted_target,
            "persisted_rad": persisted_target.get("rad"),
            "persisted_radius_src": persisted_target.get("radiusSrc"),
        }
    )
    state["part_h"] = findings
    return True


# ---------------------------------------------------------------------------
# Part I: shortening a link whose connector is anchored beyond the new end.
# ---------------------------------------------------------------------------


def part_i(state: dict[str, Any]) -> bool:
    import pythoncom

    from services.agent.tools_check import _load_check
    from services.agent.tools_network import build_shadow_model

    _log("=" * 78)
    _log(f"[I] {_PART_LABELS['i']}")
    _log("=" * 78)

    working = _clone(METERS_NET, "shorten-under-connector")
    root = ET.parse(working).getroot()
    endpoints_before = _connector_endpoints(working)

    # Pick the plain link whose attached connector sits FURTHEST along it --
    # the case Part D missed, chosen by measurement rather than by hand.
    best: tuple[str, str, float] | None = None
    for connector_no, ends in endpoints_before.items():
        for side in ("from", "to"):
            link_no = ends[f"{side}_link"]
            try:
                pos = float(ends[f"{side}_pos"] or 0)
            except ValueError:
                continue
            if not link_no:
                continue
            if best is None or pos > best[2]:
                best = (link_no, connector_no, pos)
    if best is None:
        state["part_i"] = {"error": "no connector endpoints found at all"}
        return False
    target_link, connector_no, anchor_pos = best
    static_before = _static_link_shape(working, target_link)
    xy = static_before["xy"]
    length_before_m = _polyline_length_m(xy)
    anchored_before = _anchored_objects(working)
    shadow_before = build_shadow_model(working)

    first, last = xy[0], xy[-1]
    target_last = (first[0] + (last[0] - first[0]) * 0.5, first[1] + (last[1] - first[1]) * 0.5)
    _log(f"\n    clone: {working}")
    _log(f"    target link {target_link} (length {length_before_m:.3f} m) carries connector "
         f"{connector_no} anchored at pos {anchor_pos:.3f} m -- i.e. "
         f"{'BEYOND' if anchor_pos > length_before_m / 2 else 'BEFORE'} the planned new end "
         f"({length_before_m / 2:.3f} m). That inequality is the whole premise of this part.")

    heads_on_link = [
        h for h in anchored_before["signal_heads"] if (h["lane"] or "").split(" ")[0] == str(target_link)
    ]
    _log(f"    signal heads anchored on link {target_link}: "
         f"{[(h['no'], h['lane'], h['pos']) for h in heads_on_link]}")

    findings: dict[str, Any] = {
        "network": METERS_NET.name,
        "target_link": target_link,
        "connector_no": connector_no,
        "connector_anchor_pos_m": anchor_pos,
        "length_before_m": length_before_m,
        "planned_new_length_m": length_before_m / 2,
        "anchor_is_beyond_new_end": anchor_pos > length_before_m / 2,
        "heads_on_link_before": heads_on_link,
        "static_before": static_before,
    }

    pythoncom.CoInitialize()
    controller = _connect(working)
    saved: Path | None = None
    try:
        net = controller.vissim.Net
        link = net.Links.ItemByKey(int(target_link))
        length2d_before = float(link.AttValue("Length2D"))
        points = list(link.LinkPolyPts.GetAll())
        write_x = _try_write(points[-1], "X", target_last[0])
        write_y = _try_write(points[-1], "Y", target_last[1])
        length2d_after = float(link.AttValue("Length2D"))
        _log(f"\n[I.1] Length2D {length2d_before} -> {length2d_after}")
        findings.update(
            {"write_x": write_x, "write_y": write_y,
             "length2d_before": length2d_before, "length2d_after": length2d_after}
        )

        try:
            connector = net.Links.ItemByKey(int(connector_no))
            findings["connector_after_com"] = {
                "exists": True,
                "from_pos": float(connector.AttValue("FromPos")),
                "to_pos": float(connector.AttValue("ToPos")),
                "from_link": str(connector.AttValue("FromLink")),
                "to_link": str(connector.AttValue("ToLink")),
            }
        except Exception as exc:
            findings["connector_after_com"] = {"exists": False, "error": f"{type(exc).__name__}: {exc}"}
        _log(f"[I.2] the connector as COM sees it after the shortening: "
             f"{findings['connector_after_com']}")

        saved = working.with_name(working.stem + "_saved.inpx")
        controller.save_net_as(saved)
        _log(f"      saved: {saved}")
    finally:
        _finish(controller)
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    if saved is None:
        state["part_i"] = findings
        return False

    endpoints_after = _connector_endpoints(saved)
    endpoint_changes = {
        k: [endpoints_before.get(k), v] for k, v in endpoints_after.items() if endpoints_before.get(k) != v
    }
    anchored_after = _anchored_objects(saved)
    heads_after = [
        h for h in anchored_after["signal_heads"] if (h["lane"] or "").split(" ")[0] == str(target_link)
    ]
    conflict_before = {c["no"] for c in anchored_before["conflict_areas"]}
    conflict_after = {c["no"] for c in anchored_after["conflict_areas"]}
    static_after = _static_link_shape(saved, target_link)
    _log(f"\n[I.3] persisted link {target_link} length "
         f"{_polyline_length_m(static_after['xy']):.3f} m")
    _log(f"      connector {connector_no} endpoints: before={endpoints_before.get(connector_no)} "
         f"after={endpoints_after.get(connector_no)}")
    _log(f"      connector endpoint changes network-wide: {len(endpoint_changes)}")
    _log(f"      signal heads on the link: before="
         f"{[(h['no'], h['pos']) for h in heads_on_link]} after={[(h['no'], h['pos']) for h in heads_after]}")
    _log(f"      conflict areas {len(conflict_before)} -> {len(conflict_after)} "
         f"(dropped={sorted(conflict_before - conflict_after)[:10]})")
    findings.update(
        {
            "static_after": static_after,
            "persisted_length_m": _polyline_length_m(static_after["xy"]),
            "connector_endpoint_before": endpoints_before.get(connector_no),
            "connector_endpoint_after": endpoints_after.get(connector_no),
            "connector_endpoint_changes": endpoint_changes,
            "connector_survived": connector_no in endpoints_after,
            "heads_after": heads_after,
            "conflict_area_before": len(conflict_before),
            "conflict_area_after": len(conflict_after),
            "conflict_area_dropped": sorted(conflict_before - conflict_after),
            "conflict_area_new": sorted(conflict_after - conflict_before),
        }
    )

    shadow_after = build_shadow_model(saved)
    _log("\n[I.4] diff_shadow_models:")
    findings["shadow_diff"] = _grouped_diff(shadow_before, shadow_after)

    load_result = _load_check(saved)
    _log(f"\n[I.5] load_check on the saved file: {load_result}")
    findings["load_check"] = load_result

    state["part_i"] = findings
    return bool(findings.get("write_x", {}).get("changed"))


# ---------------------------------------------------------------------------
# Part J: the radius UNIT question, on a FEET network. Read-only.
# ---------------------------------------------------------------------------


def part_j(state: dict[str, Any]) -> bool:
    import pythoncom

    _log("=" * 78)
    _log(f"[J] {_PART_LABELS['j']}")
    _log("=" * 78)

    working = _clone(FEET_NET, "feet-radius")
    root = ET.parse(working).getroot()
    curved: tuple[str, int, float] | None = None
    for link in _link_elements(root):
        points = link.findall(_POINT_XPATH)
        for index, point in enumerate(points):
            if point.get("rad"):
                curved = (link.get("no") or "", index, float(point.get("rad") or 0))
                break
        if curved:
            break
    _log(f"\n    clone: {working}")
    _log(f"    curved point chosen from the clone's own XML: link {curved[0]} point index "
         f"{curved[1]}, persisted rad = {curved[2]} (the XML is always raw metres)")

    findings: dict[str, Any] = {
        "network": FEET_NET.name,
        "link": curved[0],
        "point_index": curved[1],
        "persisted_rad_xml": curved[2],
    }

    pythoncom.CoInitialize()
    controller = _connect(working)
    try:
        unit = controller._net_unit_length()
        link = controller.vissim.Net.Links.ItemByKey(int(curved[0]))
        point = list(link.LinkPolyPts.GetAll())[curved[1]]
        com_rad = point.AttValue("Rad")
        ratio = (float(com_rad) / curved[2]) if com_rad and curved[2] else None
        _log(f"\n[J.1] UnitLenShort={unit!r}; COM Rad={com_rad!r}; XML rad={curved[2]}; "
             f"ratio={ratio}")
        _log(f"      1.0 => raw metres like the point coordinates; "
             f"{1 / _M_PER_FT:.4f} => display-unit-scaled like Length2D/Pos/Width.")
        findings.update({"unit_len_short": unit, "com_rad": com_rad, "ratio": ratio})
    finally:
        _finish(controller)
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass

    state["part_j"] = findings
    return findings.get("ratio") is not None


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------

_PARTS = {
    "a": part_a, "b": part_b, "c": part_c, "d": part_d, "e": part_e, "f": part_f, "g": part_g,
    "h": part_h, "i": part_i, "j": part_j,
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
        state["lane_token_scan"] = static_lane_token_scan()
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
    parser = argparse.ArgumentParser(description="Q6 unit (1) link-geometry probe")
    parser.add_argument("--part", choices=_PART_ORDER)
    parser.add_argument("--only", help="comma-separated part list to re-run without clearing state")
    parser.add_argument("--state", type=Path)
    args = parser.parse_args()
    if args.part:
        if args.state is None:
            parser.error("--part requires --state")
        return _run_one_part(args.part, args.state)
    only = [p.strip() for p in args.only.split(",")] if args.only else None
    return _run_all(only)


if __name__ == "__main__":
    raise SystemExit(main())

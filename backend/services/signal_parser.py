"""Parse VISSIM signal controller timing and infer phase movements."""
from __future__ import annotations

import base64
import math
import os
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

CARDINAL_DIRECTIONS = ("north", "east", "south", "west")

# Standard Korean 8-SG convention: SG1-8 = N(1-2), E(3-4), S(5-6), W(7-8)
_STANDARD_4WAY_MOVEMENTS: dict[str, tuple[str, str, str]] = {
    "1": ("north", "south", "straight"),
    "2": ("north", "east",  "left"),
    "3": ("east",  "west",  "straight"),
    "4": ("east",  "south", "left"),
    "5": ("south", "north", "straight"),
    "6": ("south", "west",  "left"),
    "7": ("west",  "east",  "straight"),
    "8": ("west",  "north", "left"),
}


class PhaseMovement(TypedDict):
    from_direction: str
    to_direction: str
    turn: str


class PhaseInfo(TypedDict):
    start: int     # seconds from cycle start
    duration: int  # effective green duration in seconds
    signal_groups: list[str]
    signal_group_ids: list[str]
    movements: list[PhaseMovement]


class SignalInfo(TypedDict):
    sc_no: str
    sc_name: str
    cycle_time: int          # seconds; 0 = variable / unknown
    controller_type: str     # <signalController type="..."> verbatim (e.g. "FIXEDTIME",
                              # "FIXEDTIMESIMPLE", "VAP", "RINGBARRIERCONTROLLER",
                              # "RAILWAYCROSSING", "PEDESTRIANCROSSING",
                              # "TWOSTAGECONTROLLER"); "unknown" when the attribute is
                              # genuinely absent/blank (never guessed/inferred).
    num_signal_groups: int
    num_phases: int
    phases: list[PhaseInfo]


class ConnectorDef(TypedDict):
    to_link: str
    lane_pairs: list[tuple[int | None, int | None]]


class SignalTopology(TypedDict):
    sg_lanes: dict[str, dict[str, list[tuple[str, int]]]]
    connectors_by_from: dict[str, list[ConnectorDef]]
    link_points: dict[str, list[tuple[float, float]]]


def _parse_one_sc(
    sc_el: ET.Element,
    *,
    sg_names: dict[str, str],
    sg_head_counts: dict[str, int],
) -> SignalInfo | None:
    no = sc_el.get("no", "")
    name = sc_el.get("name", "")
    isd = sc_el.get("intSupplyData", "")
    # Empirically verified against all 25 real PTV Vissim 2026 "Examples Demo"
    # networks (2026-07-24): <signalController>'s own "type" attribute carries
    # the controller family verbatim -- FIXEDTIME/FIXEDTIMESIMPLE (COM-writable
    # per the M2.1 CycTm finding), VAP/RINGBARRIERCONTROLLER/RAILWAYCROSSING/
    # PEDESTRIANCROSSING/TWOSTAGECONTROLLER (program logic Vissim's COM API
    # cannot safely rewrite via CycTm/Offset -- see constraints.py #4). Never
    # inferred/guessed: a blank or missing attribute becomes the literal
    # string "unknown", which constraints.py #4 already refuses on.
    controller_type = (sc_el.get("type") or "").strip() or "unknown"
    if not isd:
        # Empirically verified (2026-07-24) against all 25 real example
        # networks: intSupplyData is populated ONLY on type="FIXEDTIME"
        # controllers. Every other real type -- including FIXEDTIMESIMPLE
        # (Air Taxi Vertiport) -- carries a real "type" attribute but NO
        # intSupplyData at all; dropping those entirely (the old behavior)
        # would make FIXEDTIMESIMPLE structurally invisible to the shadow
        # model, defeating the M2.1 finding that its cycle/offset ARE
        # COM-writable. Return a type-only, timing-blind SignalInfo instead
        # of None whenever the element carries a real "type" (existence +
        # controller family is still genuine, verifiable data) -- but keep
        # returning None when BOTH isd and type are absent (the synthetic
        # demo .inpx's <signalController no="1" name="..."/> shape, which
        # must stay invisible per test_agent_read_tools.py's pinned
        # empty-shadow-signals contract).
        if not sc_el.get("type"):
            return None
        return SignalInfo(
            sc_no=no,
            sc_name=name,
            cycle_time=0,
            controller_type=controller_type,
            num_signal_groups=0,
            num_phases=0,
            phases=[],
        )

    try:
        inner = ET.fromstring(base64.b64decode(isd).decode("utf-8"))
    except Exception:
        return None

    sgs_el = inner.find("sgs")
    num_sgs = len(list(sgs_el)) if sgs_el is not None else 0

    # Fallback for stage-based controllers (no <progs> blocks).
    stage_cycle_s, stage_phases = _parse_stage_program_phases(
        inner=inner,
        sg_names=sg_names,
        sg_head_counts=sg_head_counts,
    )

    progs_el = inner.find("progs")
    if progs_el is None or len(list(progs_el)) == 0:
        return SignalInfo(
            sc_no=no,
            sc_name=name,
            cycle_time=stage_cycle_s,
            controller_type=controller_type,
            num_signal_groups=num_sgs,
            num_phases=len(stage_phases),
            phases=stage_phases,
        )

    prog = list(progs_el)[0]
    cycle_ms = int(prog.get("cycletime", 0))
    cycle_s = cycle_ms // 1000

    # green_start_s -> {"duration": int, "groups": set[str], "group_ids": set[str]}
    green_windows: dict[int, dict[str, object]] = {}

    prog_sgs = prog.find("sgs")
    if prog_sgs is not None:
        for sg_index, sg_in_prog in enumerate(list(prog_sgs), start=1):
            cmds_el = sg_in_prog.find("cmds")
            if cmds_el is None:
                continue

            green_start: int | None = None
            red_start: int | None = None
            sg_no = (
                sg_in_prog.get("sg_id")
                or sg_in_prog.get("sg")
                or sg_in_prog.get("no")
                or str(sg_index)
            ).strip()
            if not sg_no:
                continue

            sg_name = sg_names.get(sg_no) or sg_in_prog.get("name") or f"SG{sg_no}"
            head_count = sg_head_counts.get(sg_no)
            sg_label = f"{sg_name} ({head_count} heads)" if head_count else sg_name
            for cmd in list(cmds_el):
                d = cmd.get("display")
                b = int(cmd.get("begin", 0)) // 1000
                if d == "3":
                    green_start = b
                elif d == "1":
                    red_start = b

            # Amber duration
            amber_s = 3
            fs_el = sg_in_prog.find("fixedstates")
            if fs_el is not None:
                for fs in list(fs_el):
                    if fs.get("display") == "4":
                        amber_s = int(fs.get("duration", 3000)) // 1000

            if green_start is not None and red_start is not None:
                dur = max(0, red_start - green_start - amber_s)
                if dur <= 0:
                    continue

                if green_start not in green_windows:
                    green_windows[green_start] = {
                        "duration": dur,
                        "groups": {sg_label},
                        "group_ids": {sg_no},
                    }
                    continue

                existing = green_windows[green_start]
                existing["duration"] = max(int(existing["duration"]), dur)
                existing_groups = existing["groups"]
                if isinstance(existing_groups, set):
                    existing_groups.add(sg_label)
                existing_ids = existing["group_ids"]
                if isinstance(existing_ids, set):
                    existing_ids.add(sg_no)

    # Cluster nearby start times (within 5 s) into phases
    sorted_starts = sorted(green_windows.keys())
    phases: list[PhaseInfo] = []
    visited: set[int] = set()

    for gs in sorted_starts:
        if gs in visited:
            continue
        cluster = [s for s in sorted_starts if abs(s - gs) <= 5]
        for s in cluster:
            visited.add(s)
        best_start = min(cluster)
        best_dur = max(int(green_windows[s]["duration"]) for s in cluster)
        group_names: set[str] = set()
        group_ids: set[str] = set()
        for s in cluster:
            groups = green_windows[s]["groups"]
            if isinstance(groups, set):
                group_names.update(str(item) for item in groups if item)
            ids = green_windows[s]["group_ids"]
            if isinstance(ids, set):
                group_ids.update(str(item) for item in ids if item)
        if best_dur > 0:
            phases.append(
                PhaseInfo(
                    start=best_start,
                    duration=best_dur,
                    signal_groups=sorted(group_names),
                    signal_group_ids=sorted(group_ids),
                    movements=[],
                )
            )

    return SignalInfo(
        sc_no=no,
        sc_name=name,
        cycle_time=cycle_s,
        controller_type=controller_type,
        num_signal_groups=num_sgs,
        num_phases=len(phases),
        phases=sorted(phases, key=lambda p: p["start"]),
    )


def parse_signal_controllers(inpx_path: Path) -> dict[str, SignalInfo]:
    """Return {sc_no: SignalInfo} for all signal controllers in the INPX."""
    root = ET.parse(inpx_path).getroot()
    head_index = _build_signal_head_index(root)
    result: dict[str, SignalInfo] = {}
    for sc_el in root.findall(".//signalController"):
        sc_no = sc_el.get("no", "")
        info = _parse_one_sc(
            sc_el,
            sg_names=_build_signal_group_name_map(sc_el),
            sg_head_counts=head_index.get(sc_no, {}),
        )
        if info is not None:
            result[info["sc_no"]] = info
    return result


def _build_signal_group_name_map(sc_el: ET.Element) -> dict[str, str]:
    names: dict[str, str] = {}
    sgs_el = sc_el.find("sgs")
    if sgs_el is None:
        return names

    for sg in sgs_el.findall("signalGroup"):
        no = sg.get("no", "").strip()
        if not no:
            continue
        names[no] = sg.get("name", "").strip() or f"SG{no}"
    return names


def _build_signal_head_index(root: ET.Element) -> dict[str, dict[str, int]]:
    """Return {sc_no: {sg_no: head_count}} using signalHead@sg='sc sg' references."""
    index: dict[str, dict[str, int]] = {}
    for head in root.findall(".//signalHead"):
        sg_ref = (head.get("sg") or "").strip()
        if not sg_ref:
            continue
        parts = sg_ref.split()
        if len(parts) != 2:
            continue
        sc_no, sg_no = parts[0], parts[1]
        if not sc_no or not sg_no:
            continue
        index.setdefault(sc_no, {})
        index[sc_no][sg_no] = index[sc_no].get(sg_no, 0) + 1
    return index


def _parse_stage_program_phases(
    *,
    inner: ET.Element,
    sg_names: dict[str, str],
    sg_head_counts: dict[str, int],
) -> tuple[int, list[PhaseInfo]]:
    stage_progs = inner.find("stageProgs")
    if stage_progs is None or not list(stage_progs):
        return 0, []

    first_prog = list(stage_progs)[0]
    try:
        cycle_ms = int(first_prog.get("cycletime", "0"))
    except (TypeError, ValueError):
        cycle_ms = 0
    cycle_s = max(0, cycle_ms // 1000)
    if cycle_s <= 0:
        return 0, []

    stage_groups = _build_stage_group_info(inner, sg_names, sg_head_counts)
    if not stage_groups:
        return cycle_s, []

    interstages = first_prog.find("interstages")
    transitions: list[tuple[str, int]] = []
    if interstages is not None:
        for item in list(interstages):
            stage_id = (item.get("display") or "").strip()
            if not stage_id:
                continue
            try:
                begin_s = int(item.get("begin", "0")) // 1000
            except (TypeError, ValueError):
                continue
            transitions.append((stage_id, max(0, begin_s)))

    phases: list[PhaseInfo] = []
    if transitions:
        transitions.sort(key=lambda pair: pair[1])
        for idx, (stage_id, start_s) in enumerate(transitions):
            next_start = transitions[(idx + 1) % len(transitions)][1]
            if idx == len(transitions) - 1:
                duration = (cycle_s - start_s) + next_start
            else:
                duration = next_start - start_s
            duration = max(0, duration)
            group_info = stage_groups.get(stage_id, {"labels": [], "ids": []})
            if duration > 0:
                phases.append(
                    PhaseInfo(
                        start=start_s,
                        duration=duration,
                        signal_groups=group_info["labels"],
                        signal_group_ids=group_info["ids"],
                        movements=[],
                    )
                )
    else:
        # Last-resort fallback when interstage transitions are unavailable.
        stage_ids = sorted(stage_groups.keys(), key=lambda s: int(s) if s.isdigit() else s)
        if not stage_ids:
            return cycle_s, []
        per_stage = max(1, cycle_s // len(stage_ids))
        for idx, stage_id in enumerate(stage_ids):
            group_info = stage_groups.get(stage_id, {"labels": [], "ids": []})
            phases.append(
                PhaseInfo(
                    start=idx * per_stage,
                    duration=per_stage,
                    signal_groups=group_info["labels"],
                    signal_group_ids=group_info["ids"],
                    movements=[],
                )
            )

    return cycle_s, sorted(phases, key=lambda p: p["start"])


def _build_stage_group_info(
    inner: ET.Element,
    sg_names: dict[str, str],
    sg_head_counts: dict[str, int],
) -> dict[str, dict[str, list[str]]]:
    stages_el = inner.find("stages")
    if stages_el is None:
        return {}

    result: dict[str, dict[str, list[str]]] = {}
    for stage in list(stages_el):
        stage_id = (stage.get("id") or "").strip()
        if not stage_id:
            continue
        activations = stage.find("activations")
        if activations is None:
            continue

        labels: list[str] = []
        sg_ids: list[str] = []
        for activation in list(activations):
            if (activation.get("activation") or "").upper() != "ON":
                continue
            sg_no = (activation.get("sg_id") or "").strip()
            if not sg_no:
                continue
            sg_ids.append(sg_no)
            sg_name = sg_names.get(sg_no) or f"SG{sg_no}"
            head_count = sg_head_counts.get(sg_no)
            label = f"{sg_name} ({head_count} heads)" if head_count else sg_name
            labels.append(label)

        result[stage_id] = {
            "labels": sorted(set(labels)),
            "ids": sorted(set(sg_ids)),
        }
    return result


def _normalize(s: str) -> str:
    return s.replace(" ", "").replace("-", "").lower()


def match_sc_to_nodes(
    sc_map: dict[str, SignalInfo],
    node_names: dict[str, str],
) -> dict[str, SignalInfo | None]:
    """Match signal controllers to nodes by normalized name."""
    sc_by_norm = {_normalize(v["sc_name"]): v for v in sc_map.values()}
    return {
        node_id: sc_by_norm.get(_normalize(name))
        for node_id, name in node_names.items()
    }


def match_sc_to_nodes_by_signal_heads(
    inpx_path: Path,
    sc_map: dict[str, SignalInfo],
) -> dict[str, SignalInfo]:
    """Match signal controllers to nodes geometrically.

    Name matching fails when nodes have no name (e.g. the SouthSt model), so
    instead map each controller to the node whose links host the most of its
    signal heads. A ``<signalHead>`` carries ``sg="<sc_no> <sg_no>"`` and
    ``lane="<link_id> <lane_idx>"``; a node lists its links via ``<linkSegs>``.
    """
    try:
        root = ET.parse(inpx_path).getroot()
    except ET.ParseError:
        return {}

    node_links: dict[str, set[str]] = {}
    for node in root.findall(".//nodes/node"):
        node_id = node.get("no", "")
        node_links[node_id] = {
            seg.get("link", "") for seg in node.findall(".//linkSegment") if seg.get("link")
        }

    votes: dict[str, dict[str, int]] = {}
    for head in root.findall(".//signalHead"):
        sg = head.get("sg", "")
        lane = head.get("lane", "")
        if not sg or not lane:
            continue
        sc_no = sg.split(" ")[0]
        link_id = lane.split(" ")[0]
        for node_id, links in node_links.items():
            if link_id in links:
                votes.setdefault(sc_no, {})[node_id] = votes.setdefault(sc_no, {}).get(node_id, 0) + 1
                break

    matched: dict[str, SignalInfo] = {}
    for sc_no, node_votes in votes.items():
        sc = sc_map.get(sc_no)
        if sc is None or not node_votes:
            continue
        best_node = max(node_votes, key=node_votes.get)
        matched[best_node] = sc
    return matched


def _infer_from_standard_sg_map(phases: list[PhaseInfo]) -> list[list[PhaseMovement]]:
    """Fallback for intersections without signal heads: use standard Korean 8-SG convention."""
    result: list[list[PhaseMovement]] = []
    for phase in phases:
        movs: list[PhaseMovement] = []
        for sg_id in phase.get("signal_group_ids", []):
            entry = _STANDARD_4WAY_MOVEMENTS.get(str(sg_id))
            if entry:
                movs.append(PhaseMovement(
                    from_direction=entry[0],
                    to_direction=entry[1],
                    turn=entry[2],
                ))
        result.append(movs)
    return result


def infer_phase_movements_for_controller(
    inpx_path: Path,
    *,
    sc_no: str,
    node_center: tuple[float, float],
    phases: list[PhaseInfo],
) -> list[list[PhaseMovement]]:
    """Infer phase movements using signalHead lane and connector link topology."""
    if not phases:
        return []

    try:
        mtime = os.path.getmtime(inpx_path)
    except OSError:
        return [[] for _ in phases]

    topology = _load_signal_topology(str(inpx_path), mtime)
    sg_lanes = topology["sg_lanes"].get(sc_no, {})
    connectors_by_from = topology["connectors_by_from"]
    link_points = topology["link_points"]

    if not sg_lanes:
        return _infer_from_standard_sg_map(phases)

    phase_movements: list[list[PhaseMovement]] = []
    for phase in phases:
        items: list[PhaseMovement] = []
        for sg_id in phase.get("signal_group_ids", []):
            heads = sg_lanes.get(str(sg_id), [])
            for from_link, from_lane in heads:
                outgoing = connectors_by_from.get(from_link, [])
                connector_hits = _expand_connector_pairs_for_lane(outgoing, from_lane)
                for to_link in connector_hits:
                    from_direction = _classify_link_direction(node_center, link_points.get(from_link, []))
                    to_direction = _classify_link_direction(node_center, link_points.get(to_link, []))
                    turn = _get_turn_kind(from_direction, to_direction)
                    items.append(
                        PhaseMovement(
                            from_direction=from_direction,
                            to_direction=to_direction,
                            turn=turn,
                        )
                    )

            standard_entry = _STANDARD_4WAY_MOVEMENTS.get(str(sg_id))
            if standard_entry:
                items.append(
                    PhaseMovement(
                        from_direction=standard_entry[0],
                        to_direction=standard_entry[1],
                        turn=standard_entry[2],
                    )
                )

        dedup: dict[tuple[str, str, str], PhaseMovement] = {}
        for item in items:
            key = (item["from_direction"], item["to_direction"], item["turn"])
            dedup[key] = item

        ordered = sorted(
            dedup.values(),
            key=lambda m: (
                CARDINAL_DIRECTIONS.index(m["from_direction"])
                if m["from_direction"] in CARDINAL_DIRECTIONS
                else 99,
                m["turn"],
                CARDINAL_DIRECTIONS.index(m["to_direction"])
                if m["to_direction"] in CARDINAL_DIRECTIONS
                else 99,
            ),
        )
        phase_movements.append(ordered)

    return phase_movements


@lru_cache(maxsize=8)
def _load_signal_topology(inpx_path: str, mtime: float) -> SignalTopology:
    del mtime  # cache-busting arg
    root = ET.parse(inpx_path).getroot()
    return SignalTopology(
        sg_lanes=_build_sg_lane_index(root),
        connectors_by_from=_build_connectors_by_from(root),
        link_points=_build_link_points(root),
    )


def _build_sg_lane_index(root: ET.Element) -> dict[str, dict[str, list[tuple[str, int]]]]:
    """Return {sc_no: {sg_no: [(link_id, lane_no), ...]}} from signalHead records."""
    result: dict[str, dict[str, list[tuple[str, int]]]] = {}
    for head in root.findall(".//signalHead"):
        sg_ref = (head.get("sg") or "").strip()
        lane_ref = (head.get("lane") or "").strip()
        if not sg_ref or not lane_ref:
            continue

        sg_parts = sg_ref.split()
        lane_parts = lane_ref.split()
        if len(sg_parts) != 2 or len(lane_parts) != 2:
            continue
        sc_no, sg_no = sg_parts[0], sg_parts[1]
        link_id = lane_parts[0]
        try:
            lane_no = int(float(lane_parts[1]))
        except ValueError:
            continue
        if not sc_no or not sg_no or not link_id:
            continue

        result.setdefault(sc_no, {})
        result[sc_no].setdefault(sg_no, [])
        result[sc_no][sg_no].append((link_id, lane_no))

    for sc_no, by_sg in result.items():
        for sg_no, values in by_sg.items():
            dedup: list[tuple[str, int]] = []
            seen: set[tuple[str, int]] = set()
            for value in values:
                if value in seen:
                    continue
                seen.add(value)
                dedup.append(value)
            result[sc_no][sg_no] = dedup
    return result


def _build_link_points(root: ET.Element) -> dict[str, list[tuple[float, float]]]:
    result: dict[str, list[tuple[float, float]]] = {}
    for link in root.findall(".//links/link"):
        link_id = (link.get("no") or "").strip()
        if not link_id:
            continue
        points: list[tuple[float, float]] = []
        pts_el = link.find("geometry/linkPolyPts")
        if pts_el is not None:
            for point in pts_el.findall("linkPolyPoint"):
                points.append((float(point.get("x", "0")), float(point.get("y", "0"))))
        result[link_id] = points
    return result


def _build_connectors_by_from(root: ET.Element) -> dict[str, list[ConnectorDef]]:
    result: dict[str, list[ConnectorDef]] = {}
    for link in root.findall(".//links/link"):
        from_end = link.find("fromLinkEndPt")
        to_end = link.find("toLinkEndPt")
        if from_end is None or to_end is None:
            continue

        from_link = (from_end.get("link") or "").strip()
        to_link = (to_end.get("link") or "").strip()
        if not from_link or not to_link:
            continue

        lane_pairs: list[tuple[int | None, int | None]] = []
        lanes_el = link.find("lanes")
        if lanes_el is not None:
            for lane in lanes_el.findall("lane"):
                from_lanes = _extract_lane_numbers(lane.find("fromLanes"))
                to_lanes = _extract_lane_numbers(lane.find("toLanes"))
                if not from_lanes and not to_lanes:
                    continue
                from_candidates = from_lanes or [None]
                to_candidates = to_lanes or [None]
                for from_lane in from_candidates:
                    for to_lane in to_candidates:
                        lane_pairs.append((from_lane, to_lane))

        dedup_pairs: list[tuple[int | None, int | None]] = []
        seen_pairs: set[tuple[int | None, int | None]] = set()
        for pair in lane_pairs:
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            dedup_pairs.append(pair)

        result.setdefault(from_link, [])
        result[from_link].append(ConnectorDef(to_link=to_link, lane_pairs=dedup_pairs))
    return result


def _extract_lane_numbers(lanes_el: ET.Element | None) -> list[int]:
    if lanes_el is None:
        return []
    lane_numbers: list[int] = []
    for ref in lanes_el.findall("objectRef"):
        parsed = _parse_lane_ref(ref.get("key", ""))
        if parsed is None:
            continue
        _, lane_no = parsed
        lane_numbers.append(lane_no)
    return lane_numbers


def _parse_lane_ref(key: str) -> tuple[str, int] | None:
    parts = key.strip().split()
    if len(parts) != 2:
        return None
    link_id, lane = parts[0], parts[1]
    if not link_id:
        return None
    try:
        lane_no = int(float(lane))
    except ValueError:
        return None
    return link_id, lane_no


def _expand_connector_pairs_for_lane(connectors: list[ConnectorDef], from_lane: int) -> list[str]:
    to_links: list[str] = []
    matched_any = False
    for connector in connectors:
        lane_pairs = connector["lane_pairs"]
        if not lane_pairs:
            matched_any = True
            to_links.append(connector["to_link"])
            continue

        matched = False
        for source_lane, _ in lane_pairs:
            if source_lane is None or source_lane == from_lane:
                matched = True
                break
        if matched:
            matched_any = True
            to_links.append(connector["to_link"])

    if not matched_any:
        to_links = [connector["to_link"] for connector in connectors]

    dedup: list[str] = []
    seen: set[str] = set()
    for to_link in to_links:
        if to_link in seen:
            continue
        seen.add(to_link)
        dedup.append(to_link)
    return dedup


def _classify_link_direction(node_center: tuple[float, float], points: list[tuple[float, float]]) -> str:
    if len(points) < 2:
        return "east"

    start = points[0]
    end = points[-1]
    start_dist = _distance(node_center, start)
    end_dist = _distance(node_center, end)
    far = end if start_dist <= end_dist else start

    dx = far[0] - node_center[0]
    dy = far[1] - node_center[1]
    if abs(dx) >= abs(dy):
        return "east" if dx >= 0 else "west"
    return "north" if dy >= 0 else "south"


def _get_turn_kind(from_direction: str, to_direction: str) -> str:
    if from_direction == to_direction:
        return "uturn"
    if from_direction not in CARDINAL_DIRECTIONS or to_direction not in CARDINAL_DIRECTIONS:
        return "other"

    from_index = CARDINAL_DIRECTIONS.index(from_direction)
    left_target = CARDINAL_DIRECTIONS[(from_index + 1) % 4]
    straight_target = CARDINAL_DIRECTIONS[(from_index + 2) % 4]
    right_target = CARDINAL_DIRECTIONS[(from_index + 3) % 4]

    if to_direction == left_target:
        return "left"
    if to_direction == straight_target:
        return "straight"
    if to_direction == right_target:
        return "right"
    return "other"


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])

"""Parse VISSIM .inpx XML to extract network geometry."""
import math
import os
from pathlib import Path
import xml.etree.ElementTree as ET


def parse_network(inpx_path: str) -> dict:
    tree = ET.parse(inpx_path)
    net = parse_network_root(tree.getroot())

    # Modifications 폴더에서 추가 노드 병합 + 기존 노드 이름 패치
    mods_dir = Path(inpx_path).parent / "Modifications"
    if mods_dir.is_dir():
        node_by_id = {n["id"]: n for n in net["nodes"]}
        extra_nodes: dict[str, dict] = {}
        for fname in sorted(mods_dir.iterdir()):
            if fname.suffix.lower() != ".trax":
                continue
            try:
                mod_root = ET.parse(fname).getroot()
                for node in mod_root.findall(".//node"):
                    nid = node.get("no", "")
                    if not nid:
                        continue
                    trax_name = node.get("name", "")
                    if nid in node_by_id:
                        # 기존 노드 이름이 비어있거나 fallback("Node N")이면 trax 이름으로 업데이트
                        existing_name = node_by_id[nid]["name"]
                        if trax_name and (not existing_name or existing_name == f"Node {nid}"):
                            node_by_id[nid]["name"] = trax_name
                        continue
                    # 새 노드: geometry/points가 있어야 추가
                    geom = node.find("geometry")
                    pts_el = geom.find("points") if geom is not None else None
                    pts = []
                    if pts_el is not None:
                        for pt in pts_el.findall("point"):
                            pts.append([float(pt.get("x", 0)), float(pt.get("y", 0))])
                    if not pts:
                        continue
                    cx = sum(p[0] for p in pts) / len(pts)
                    cy = sum(p[1] for p in pts) / len(pts)
                    extra_nodes[nid] = {
                        "id": nid,
                        "name": trax_name,
                        "x": round(cx, 4),
                        "y": round(cy, 4),
                        "polygon": pts,
                    }
            except Exception:
                continue
        net["nodes"].extend(extra_nodes.values())

    return net


def parse_network_root(root: ET.Element) -> dict:
    links = _parse_links(root)
    return {
        "links": links,
        "nodes": _parse_nodes(root, links),
        "detectors": _parse_named_elements(root, ".//dataCollectionMeasurements", "dataCollectionMeasurement"),
        "traveltime_sections": _parse_named_elements(root, ".//vehicleTravelTimeMeasurements", "vehicleTravelTimeMeasurement"),
        "elements": parse_network_elements(root, links),
    }


def _parse_links(root: ET.Element) -> list:
    links_el = root.find(".//links")
    if links_el is None:
        return []

    result = []
    for link in links_el.findall("link"):
        link_id = link.get("no", "")
        name = link.get("name", "")

        lanes_el = link.find("lanes")
        lane_count = len(lanes_el.findall("lane")) if lanes_el is not None else 1
        lane_widths = _parse_lane_widths(lanes_el, lane_count)
        width_total = round(sum(lane_widths), 2)

        geom = link.find("geometry")
        pts_el = geom.find("linkPolyPts") if geom is not None else None
        points = []
        if pts_el is not None:
            for pt in pts_el.findall("linkPolyPoint"):
                x = float(pt.get("x", 0))
                y = float(pt.get("y", 0))
                points.append([x, y])

        length = _polyline_length(points)

        from_pt_el = link.find("fromLinkEndPt")
        to_pt_el = link.find("toLinkEndPt")
        is_connector = from_pt_el is not None or to_pt_el is not None

        from_link, from_pos = _parse_connector_endpoint(from_pt_el)
        to_link, to_pos = _parse_connector_endpoint(to_pt_el)

        result.append({
            "id": link_id,
            "name": name,
            "points": points,
            "lanes": lane_count,
            "lane_widths": lane_widths,
            "width_total": width_total,
            "length": round(length, 2),
            "is_connector": is_connector,
            # Connector topology (Phase 3): the link id (as a string) each
            # endpoint attaches to, plus the arc-length position along that
            # link. ``None`` for both non-connectors and any endpoint we
            # couldn't parse — never absent, so callers can rely on the key.
            "from_link": from_link,
            "to_link": to_link,
            "from_pos": from_pos,
            "to_pos": to_pos,
        })
    return result


def _parse_lane_widths(lanes_el: ET.Element | None, lane_count: int) -> list:
    """Per-lane widths (meters) for a link, in Vissim's lane-1..N order.

    Each ``<lane width="...">`` contributes its own width; a missing/malformed
    ``width`` attribute falls back to Vissim's common default of 3.5 m rather
    than raising or dropping the lane. When the link has no ``<lanes>``
    element at all, ``_parse_links`` already defaults ``lane_count`` to 1, so
    this returns a single 3.5 m lane to match.
    """
    widths = []
    if lanes_el is not None:
        for lane_el in lanes_el.findall("lane"):
            raw = lane_el.get("width")
            try:
                width = float(raw) if raw is not None else 3.5
            except (TypeError, ValueError):
                width = 3.5
            widths.append(width)
    if not widths:
        widths = [3.5] * max(lane_count, 0)
    return widths


def _parse_connector_endpoint(end_pt: ET.Element | None) -> tuple[str | None, float | None]:
    """Extract ``(link_id, pos)`` from a connector's ``fromLinkEndPt``/``toLinkEndPt``.

    Vissim's convention is a ``lane`` attribute shaped ``"linkNo laneNo"``
    (e.g. ``lane="1234 1"``); some exports instead (or additionally) carry an
    explicit ``link`` attribute, which takes precedence when present. Never
    raises — any missing/malformed attribute yields ``None`` for that field.
    """
    if end_pt is None:
        return None, None

    link_id = end_pt.get("link")
    if not link_id:
        lane = end_pt.get("lane", "") or ""
        tokens = lane.strip().split()
        link_id = tokens[0] if tokens else None

    pos: float | None = None
    pos_raw = end_pt.get("pos")
    if pos_raw is not None:
        try:
            pos = float(pos_raw)
        except (TypeError, ValueError):
            pos = None

    return link_id, pos


def _parse_nodes(root: ET.Element, links: list | None = None) -> list:
    nodes_el = root.find(".//nodes")
    if nodes_el is None:
        return []

    link_pts = {link["id"]: link["points"] for link in (links or []) if link.get("points")}

    result = []
    for node in nodes_el.findall("node"):
        node_id = node.get("no", "")
        name = node.get("name", "")

        geom = node.find("geometry")
        pts_el = geom.find("points") if geom is not None else None
        points = []
        if pts_el is not None:
            for pt in pts_el.findall("point"):
                points.append([float(pt.get("x", 0)), float(pt.get("y", 0))])

        if points:
            cx = sum(p[0] for p in points) / len(points)
            cy = sum(p[1] for p in points) / len(points)
        else:
            # Many nodes carry no explicit polygon — they are defined only by the
            # links that cross the node area (<linkSegs>). Derive the centre from
            # the points where each connected link enters/leaves the node.
            cx, cy = _node_center_from_link_segments(node, link_pts)

        result.append({"id": node_id, "name": name, "x": round(cx, 4), "y": round(cy, 4), "polygon": points})
    return result


def _node_center_from_link_segments(node: ET.Element, link_pts: dict) -> tuple[float, float]:
    touches = []
    for seg in node.findall(".//linkSegment"):
        pts = link_pts.get(seg.get("link", ""))
        if not pts:
            continue
        length = _polyline_length(pts)
        for attr in ("fromPos", "internalToPos"):
            raw = seg.get(attr)
            if raw is None:
                continue
            try:
                dist = float(raw)
            except ValueError:
                continue
            if dist < 0:
                continue
            point = _point_at_distance(pts, min(dist, length))
            if point is not None:
                touches.append(point)
    if not touches:
        return 0.0, 0.0
    return (
        sum(p[0] for p in touches) / len(touches),
        sum(p[1] for p in touches) / len(touches),
    )


def _point_at_distance(points: list, dist: float):
    """Interpolate the point at arc-length ``dist`` along a polyline."""
    if not points:
        return None
    if dist <= 0:
        return points[0]
    acc = 0.0
    for i in range(1, len(points)):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        seg = math.hypot(x1 - x0, y1 - y0)
        if seg <= 0:
            continue
        if acc + seg >= dist:
            t = (dist - acc) / seg
            return [x0 + (x1 - x0) * t, y0 + (y1 - y0) * t]
        acc += seg
    return points[-1]


def _parse_named_elements(root: ET.Element, container_path: str, tag: str) -> list:
    container = root.find(container_path)
    if container is None:
        return []
    return [{"id": el.get("no", ""), "name": el.get("name", "")} for el in container.findall(tag)]


def _polyline_length(points: list) -> float:
    total = 0.0
    for i in range(1, len(points)):
        dx = points[i][0] - points[i - 1][0]
        dy = points[i][1] - points[i - 1][1]
        total += math.sqrt(dx * dx + dy * dy)
    return total


def _point_in_polygon(point: tuple, polygon: list) -> bool:
    """Ray-casting point-in-polygon. Boundary points count as inside.

    Stdlib only, deliberately: node membership is used by the genesis differ on
    every network the product ever reads, and a geometry dependency for a
    12-line algorithm is not a trade this module makes.
    """
    x, y = point
    inside = False
    count = len(polygon)
    if count < 3:
        return False
    j = count - 1
    for i in range(count):
        xi, yi = polygon[i][0], polygon[i][1]
        xj, yj = polygon[j][0], polygon[j][1]
        # On an edge (within float noise) counts as inside: a link that ends
        # exactly on the node boundary is a member of that node, and Vissim's
        # own polygons are drawn to touch the links they contain.
        if (
            min(xi, xj) - 1e-9 <= x <= max(xi, xj) + 1e-9
            and min(yi, yj) - 1e-9 <= y <= max(yi, yj) + 1e-9
            and abs((xj - xi) * (y - yi) - (yj - yi) * (x - xi)) <= 1e-9
        ):
            return True
        if (yi > y) != (yj > y):
            x_cross = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < x_cross:
                inside = not inside
        j = i
    return inside


def _node_link_ids(root: ET.Element) -> dict:
    """Map each node id to the set of link ids that belong to it.

    Vissim has TWO node-definition modes, and this helper honours both:

    * **Declared** — ``<linkSegment link=…>`` entries inside the node. The
      2026-08-06 live check over-generalised "Vissim never writes these" from
      PTV's Examples Demo and its own SaveNetAs outputs; the consultant's real
      ``current.inpx`` (authored in Vissim 2026.00-07) carries 139 of them
      across its 6 nodes, with NO node polygons. Where declarations exist for a
      node they are the authority and are read verbatim.
    * **Geometric** — a node whose ``<geometry><points>`` polygon encloses its
      members and declares nothing (every Examples-Demo network, and every
      genesis draft). Membership is computed: a link belongs when any vertex of
      its polyline lies inside (or on) the polygon.

    The two are resolved PER NODE — declarations first, polygon fallback — so a
    mixed network cannot silently lose either kind. A node with neither
    declarations nor a ≥3-point polygon has no members and gets an empty set;
    nothing is inferred from proximity. Geometric membership is tested at the
    polyline's VERTICES: a link that crosses a node without placing a vertex
    inside it is not a member (impossible for genesis drafts, whose links stop
    short of the junction; real polygon-mode networks draw their boxes around
    the links they contain).
    """
    nodes_el = root.find(".//nodes")
    mapping: dict[str, set] = {}
    if nodes_el is None:
        return mapping

    link_points: list[tuple[str, list]] = []
    links_el = root.find(".//links")
    if links_el is not None:
        for link in links_el.findall("link"):
            link_id = link.get("no", "")
            if not link_id:
                continue
            geom = link.find("geometry")
            pts_el = geom.find("linkPolyPts") if geom is not None else None
            if pts_el is None:
                continue
            points = []
            for pt in pts_el.findall("linkPolyPoint"):
                try:
                    points.append((float(pt.get("x", 0)), float(pt.get("y", 0))))
                except (TypeError, ValueError):
                    continue
            if points:
                link_points.append((link_id, points))

    for node in nodes_el.findall("node"):
        nid = node.get("no", "")
        # Declared membership wins: the consultant-style linkSegment entries
        # name their links outright, and a node that declares needs no polygon.
        declared = {
            seg.get("link", "")
            for seg in node.findall(".//linkSegment")
            if seg.get("link")
        }
        if declared:
            mapping[nid] = declared
            continue
        geom = node.find("geometry")
        pts_el = geom.find("points") if geom is not None else None
        polygon = []
        if pts_el is not None:
            for pt in pts_el.findall("point"):
                try:
                    polygon.append((float(pt.get("x", 0)), float(pt.get("y", 0))))
                except (TypeError, ValueError):
                    continue
        if len(polygon) < 3:
            mapping[nid] = set()
            continue
        # Bounding-box prefilter. A node box is metres across and a network is
        # kilometres, so nearly every (link, node) pair is rejected by four
        # comparisons instead of a full ray cast per vertex — 941 nodes against
        # the 5,197-link Murdoch draft is 12 s without it. Purely an
        # optimisation: the box strictly contains the polygon, so it can only
        # skip points the ray cast would have rejected anyway.
        min_x = min(p[0] for p in polygon) - 1e-9
        max_x = max(p[0] for p in polygon) + 1e-9
        min_y = min(p[1] for p in polygon) - 1e-9
        max_y = max(p[1] for p in polygon) + 1e-9
        members: set[str] = set()
        for link_id, points in link_points:
            for x, y in points:
                if not (min_x <= x <= max_x and min_y <= y <= max_y):
                    continue
                if _point_in_polygon((x, y), polygon):
                    members.add(link_id)
                    break
        mapping[nid] = members
    return mapping


def _representative_relflow(relflow: str) -> float | None:
    """A VISSIM relFlow string is 'vehComp time:value, ...' across time intervals;
    return the peak (max) relative-flow value as a single representative number."""
    values = []
    for part in (relflow or "").split(","):
        token = part.strip().split(":")[-1]
        try:
            values.append(float(token))
        except ValueError:
            continue
    return max(values) if values else None


def parse_routing_decisions(root: ET.Element) -> list:
    """Static vehicle routing decisions with per-route relative flows (turn ratios).

    Each decision sits on an approach ``link`` and splits flow across routes
    (often named RT/TH/LT) toward ``destLink`` exits; ``relFlow`` is the relative
    volume that drives the turn distribution — the main lever for fixing a
    movement-level GEH mismatch.
    """
    result = []
    for rd in root.iter("vehicleRoutingDecisionStatic"):
        routes = []
        for route in rd.iter("vehicleRouteStatic"):
            routes.append({
                "no": route.get("no", ""),
                "name": route.get("name", ""),
                "dest_link": route.get("destLink", ""),
                "rel_flow": _representative_relflow(route.get("relFlow", "")),
            })
        total = sum(r["rel_flow"] for r in routes if r["rel_flow"] is not None)
        for r in routes:
            r["share_pct"] = (
                round(r["rel_flow"] / total * 100, 1) if total and r["rel_flow"] is not None else None
            )
        result.append({
            "no": rd.get("no", ""),
            "name": rd.get("name", ""),
            "approach_link": rd.get("link", ""),
            "routes": routes,
        })
    return result


def routing_decisions_by_node(root: ET.Element) -> dict:
    """Group static routing decisions by the node they belong to.

    A routing decision sits on an approach link that ends at its target
    intersection, so each decision is assigned to the node geometrically nearest
    to that approach link's downstream end (robust to long corridor links that
    cross several nodes' segments)."""
    links = _parse_links(root)
    link_end = {lk["id"]: lk["points"][-1] for lk in links if lk.get("points")}
    nodes = _parse_nodes(root, links)
    centers = [(n["id"], n["x"], n["y"]) for n in nodes if n.get("x") or n.get("y")]

    def nearest_node(point):
        if point is None or not centers:
            return None
        best_id, best_dist = None, None
        for nid, nx, ny in centers:
            dist = (point[0] - nx) ** 2 + (point[1] - ny) ** 2
            if best_dist is None or dist < best_dist:
                best_id, best_dist = nid, dist
        return best_id

    by_node: dict[str, list] = {}
    for rd in parse_routing_decisions(root):
        nid = nearest_node(link_end.get(rd["approach_link"]))
        if nid is None:
            continue
        by_node.setdefault(nid, []).append(rd)
    return by_node


# ============================================================================
# Phase 3 Tier 1: geometry resolver + static network element extraction
# ============================================================================
#
# Vissim places every static network element (signal heads, detectors, PT
# stops, ...) by reference: a link id (+ optionally a lane number) and an
# arc-length ``pos`` along that link. None of that is an absolute (x, y) —
# resolving it requires walking the referenced link's centerline polyline.
# The helpers below do that resolution once, server-side, so the frontend
# NetworkViewer never has to reimplement polyline math in TypeScript.


def point_at_pos(points: list, pos) -> tuple[float, float, float] | None:
    """Resolve an absolute ``(x, y, angle_deg)`` placement at arc-length
    ``pos`` along a link's centerline polyline.

    ``pos`` is clamped into ``[0, length]`` — Vissim positions are always
    meant to lie on the link, but upstream data can carry float noise or go
    stale after an edit, and clamping is safer than refusing outright.
    ``angle_deg`` is the direction of travel of the polyline segment
    containing ``pos``, in the standard math convention (0 deg along +x,
    increasing counter-clockwise, range ``(-180, 180]``).

    Returns ``None`` when ``points`` has fewer than two vertices or ``pos``
    isn't a finite number — this function never raises.
    """
    if points is None or len(points) < 2:
        return None
    pos_f = _to_float(pos)
    if pos_f is None:
        return None

    length = _polyline_length(points)
    pos_f = max(0.0, min(pos_f, length))

    acc = 0.0
    last_point = (points[0][0], points[0][1])
    last_angle = 0.0
    for i in range(1, len(points)):
        x0, y0 = points[i - 1]
        x1, y1 = points[i]
        seg_len = math.hypot(x1 - x0, y1 - y0)
        if seg_len <= 0:
            continue
        angle = math.degrees(math.atan2(y1 - y0, x1 - x0))
        last_point = (x1, y1)
        last_angle = angle
        if acc + seg_len >= pos_f:
            t = max(0.0, min((pos_f - acc) / seg_len, 1.0))
            x = x0 + (x1 - x0) * t
            y = y0 + (y1 - y0) * t
            return (round(x, 4), round(y, 4), round(angle, 2))
        acc += seg_len

    # Every segment was zero-length (coincident points) or float slack left
    # ``pos_f`` a hair past the summed length: fall back to the last real
    # point/heading found rather than raising.
    return (round(last_point[0], 4), round(last_point[1], 4), round(last_angle, 2))


def lane_offset_point(points: list, pos, lane_no, lane_widths: list | None = None) -> tuple[float, float, float] | None:
    """Resolve an absolute ``(x, y, angle_deg)`` placement offset laterally
    from a link's centerline polyline to the middle of a specific lane.

    **Assumptions (Vissim-convention, not independently verified on a real
    Vissim box — see docs/phase2/DECISIONS.md):**
      - the link ``points`` polyline is the *centerline* of the whole lane
        group (not lane 1's own edge), and lanes sit side by side across
        that width with no gap between them;
      - lanes are numbered 1..N from the rightmost lane (relative to the
        link's direction of travel) to the leftmost — this matches Vissim's
        own lane-numbering convention (lane 1 = curb lane in a
        right-hand-drive-style network);
      - "right" is defined purely by rotating the local direction-of-travel
        vector -90 deg (clockwise) in the link's own (x, y) coordinate
        system — i.e. the segment normal, not a real-world compass
        direction. Whether that matches the true physical curb side on any
        given real network is unverified.

    ``lane_widths`` defaults to a single 3.5 m lane when omitted/empty.
    ``lane_no`` is clamped into ``[1, len(lane_widths)]`` (defensive, never
    raises) — an out-of-range lane number still gets a best-effort placement
    rather than being dropped. Returns ``None`` when the base centerline
    point can't be resolved (delegates to :func:`point_at_pos`) or
    ``lane_no`` isn't a positive integer at all.
    """
    base = point_at_pos(points, pos)
    if base is None:
        return None
    x, y, angle = base

    widths = [w for w in (lane_widths or []) if w is not None] or [3.5]

    try:
        lane_no_i = int(lane_no)
    except (TypeError, ValueError):
        return None
    if lane_no_i < 1:
        return None
    lane_no_i = min(lane_no_i, len(widths))

    total_width = sum(widths)
    offset_from_right_edge = sum(widths[: lane_no_i - 1]) + widths[lane_no_i - 1] / 2.0
    s_center = total_width / 2.0 - offset_from_right_edge  # signed; + = right of travel direction

    angle_rad = math.radians(angle)
    dx, dy = math.cos(angle_rad), math.sin(angle_rad)
    nx, ny = dy, -dx  # right-hand normal of the (dx, dy) travel direction

    ox = x + nx * s_center
    oy = y + ny * s_center
    return (round(ox, 4), round(oy, 4), round(angle, 2))


def _to_float(raw) -> float | None:
    """Best-effort ``float(raw)``; ``None``/malformed/non-finite -> ``None``."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _lane_ref(el: ET.Element) -> tuple[str | None, int | None]:
    """Extract ``(link_id, lane_no)`` from an element's lane reference.

    Vissim elements express "which lane of which link" in one of two shapes:
      (a) a single ``lane="LINKNO LANENO"`` attribute (the common form, also
          used by connector endpoints — see ``_parse_connector_endpoint``);
      (b) an explicit ``link="LINKNO"`` attribute plus a ``lane="LANENO"``
          attribute holding just the lane number.
    An explicit ``link`` attribute takes precedence and switches parsing
    into form (b); otherwise ``lane`` is parsed as form (a). Missing or
    malformed attributes yield ``None`` for the piece that failed — this
    function never raises, and callers skip the element rather than
    fabricate placement.
    """
    if el is None:
        return None, None

    link_id = el.get("link") or None
    lane_raw = (el.get("lane") or "").strip()

    if link_id:
        if not lane_raw:
            return link_id, None
        try:
            return link_id, int(float(lane_raw))
        except ValueError:
            return link_id, None

    tokens = lane_raw.split()
    if len(tokens) >= 2:
        tok_link = tokens[0] or None
        try:
            return tok_link, int(float(tokens[1]))
        except ValueError:
            return tok_link, None
    if len(tokens) == 1:
        return tokens[0] or None, None
    return None, None


def _sg_ref(el: ET.Element) -> tuple[str | None, str | None]:
    """Split a ``<signalHead>``'s ``sg="<sc_no> <sg_no>"`` attribute into
    ``(sc_no, sg_no)``. Same schema ``signal_parser._build_sg_lane_index``
    already relies on. Missing/malformed -> ``(None, None)``, never raises.
    """
    tokens = (el.get("sg") or "").strip().split()
    if len(tokens) != 2:
        return None, None
    return (tokens[0] or None), (tokens[1] or None)


def _element_length(el: ET.Element) -> float | None:
    """``length`` attribute, falling back to ``len`` (some exports use the
    shorter name); ``None`` when neither parses."""
    length = _to_float(el.get("length"))
    if length is None:
        length = _to_float(el.get("len"))
    return length


def _parse_lane_pos_elements(root: ET.Element, xpath: str, link_index: dict, *, with_length: bool = False) -> list:
    """Shared extraction for the "lane + pos" family of static elements
    (signal heads minus their sg/sc fields, reduced speed areas, desired
    speed decisions, detectors, data collection points, stop signs, PT
    stops). Any element whose link/lane/pos can't all be resolved — unknown
    link, missing/malformed lane or pos — is skipped outright rather than
    included with null coordinates."""
    result = []
    for el in root.findall(xpath):
        link_id, lane_no = _lane_ref(el)
        pos = _to_float(el.get("pos"))
        link = link_index.get(link_id) if link_id else None
        if link is None or lane_no is None or pos is None:
            continue
        resolved = lane_offset_point(link.get("points"), pos, lane_no, link.get("lane_widths"))
        if resolved is None:
            continue
        x, y, angle = resolved
        item = {
            "id": el.get("no", ""),
            "name": el.get("name", ""),
            "link": link_id,
            "lane": lane_no,
            "pos": pos,
            "x": x,
            "y": y,
            "angle": angle,
        }
        if with_length:
            length = _element_length(el)
            item["length"] = length
            if length is not None:
                end = lane_offset_point(link.get("points"), pos + length, lane_no, link.get("lane_widths"))
                if end is not None:
                    item["end_x"], item["end_y"] = end[0], end[1]
        result.append(item)
    return result


def _parse_link_pos_elements(root: ET.Element, xpath: str, link_index: dict) -> list:
    """Elements placed on a link (not a specific lane) via ``link``/``pos``
    attributes — currently just queue counters."""
    result = []
    for el in root.findall(xpath):
        link_id = el.get("link") or None
        pos = _to_float(el.get("pos"))
        link = link_index.get(link_id) if link_id else None
        if link is None or pos is None:
            continue
        resolved = point_at_pos(link.get("points"), pos)
        if resolved is None:
            continue
        x, y, angle = resolved
        result.append({
            "id": el.get("no", ""),
            "name": el.get("name", ""),
            "link": link_id,
            "pos": pos,
            "x": x,
            "y": y,
            "angle": angle,
        })
    return result


def vehicle_composition_names(root: ET.Element) -> dict:
    """``{composition_no: composition_name}`` from ``<vehicleComposition>``.

    Shared by this module's map-geometry vehicle-input parse and (via import)
    by ``services.agent.tools_network``'s shadow-model parse, so the two can
    never drift on how a ``vehComp`` number resolves to a human-readable
    class name."""
    return {c.get("no", ""): (c.get("name") or "") for c in root.findall(".//vehicleComposition")}


def vehicle_input_composition_fields(el: ET.Element, comp_names: dict) -> dict:
    """The composition roll-up for one ``<vehicleInput>``, as four fields.

    **Why a roll-up rather than a single field** (sibling-disambiguation unit,
    2026-07-28). ``vehComp`` does NOT live on ``<vehicleInput>`` itself — it
    lives on each ``<timeIntervalVehVolume>`` CHILD, so the schema permits one
    input to feed a different vehicle class in each time interval. Measured on
    the active real client model (Murdoch Drive, ``runtime/uploads/current/
    current.inpx``): 51 vehicle inputs, every one of them references EXACTLY
    ONE composition across all of its 6 intervals — 0 inputs vary. But
    "does not occur in this dataset" is not "cannot occur", so the varying
    case is represented rather than silently collapsed to the first interval:

    * ``composition_no`` / ``composition_name`` — the single composition when
      the input is uniform, ``""`` when it varies (or when no ``vehComp``
      attribute is present at all, e.g. the synthetic demo network).
    * ``composition_nos`` — every DISTINCT composition number, in interval
      order. ``[]`` when the element carries no ``vehComp`` anywhere.
    * ``composition_varies_by_interval`` — ``True`` only when
      ``len(composition_nos) > 1``.

    A consumer that wants one label reads ``composition_name`` and gets ``""``
    (a truthful "not a single class") rather than a confident wrong answer;
    a consumer that wants the whole truth reads ``composition_nos``.
    """
    nos: list = []
    for tv in el.findall(".//timeIntervalVehVolume"):
        comp_no = tv.get("vehComp")
        if not comp_no or comp_no in nos:
            continue
        nos.append(comp_no)
    single = nos[0] if len(nos) == 1 else ""
    return {
        "composition_no": single,
        "composition_name": comp_names.get(single, "") if single else "",
        "composition_nos": nos,
        "composition_varies_by_interval": len(nos) > 1,
    }


def _parse_vehicle_inputs(root: ET.Element, link_index: dict) -> list:
    comp_names = vehicle_composition_names(root)
    result = []
    for el in root.findall(".//vehicleInputs/vehicleInput"):
        link_id = el.get("link") or None
        link = link_index.get(link_id) if link_id else None
        if link is None:
            continue
        resolved = point_at_pos(link.get("points"), 0)
        if resolved is None:
            continue
        x, y, angle = resolved
        # Every vehicle input on a link resolves to the link's START point
        # (pos 0), so N inputs on one link land on ONE pixel. On the real
        # client model that is always 3 (one per vehicle class), which is why
        # the composition fields below are the ONLY thing that tells the map's
        # three stacked glyphs apart -- see vehicle_input_composition_fields.
        result.append({
            "id": el.get("no", ""),
            "name": el.get("name", ""),
            "link": link_id,
            "x": x,
            "y": y,
            "angle": angle,
            **vehicle_input_composition_fields(el, comp_names),
        })
    return result


def _parse_signal_heads(root: ET.Element, link_index: dict) -> list:
    result = []
    for el in root.findall(".//signalHeads/signalHead"):
        link_id, lane_no = _lane_ref(el)
        pos = _to_float(el.get("pos"))
        link = link_index.get(link_id) if link_id else None
        if link is None or lane_no is None or pos is None:
            continue
        resolved = lane_offset_point(link.get("points"), pos, lane_no, link.get("lane_widths"))
        if resolved is None:
            continue
        x, y, angle = resolved
        sc_no, sg_no = _sg_ref(el)
        result.append({
            "id": el.get("no", ""),
            "name": el.get("name", ""),
            "link": link_id,
            "lane": lane_no,
            "pos": pos,
            "x": x,
            "y": y,
            "angle": angle,
            "sg": sg_no,
            "sc": sc_no,
        })
    return result


def _resolve_tt_endpoint(el: ET.Element, child_tag: str, flat_link_attr: str, flat_pos_attr: str, link_index: dict) -> dict | None:
    """Resolve one end of a ``vehicleTravelTimeMeasurement``. The real
    attribute schema for this element is unverified on this machine (no
    travel-time-measurement-bearing .inpx available), so three plausible
    shapes are tried in order: a child element (``<start link="" pos=""/>``),
    flat attributes (``startLink``/``startPos``), and a combined
    ``start="LINK POS"`` string. Whichever resolves first wins; ``None`` when
    none do."""
    link_id = None
    pos = None

    child = el.find(child_tag)
    if child is not None:
        link_id = child.get("link") or None
        pos = _to_float(child.get("pos"))

    if link_id is None:
        link_id = el.get(flat_link_attr) or None
    if pos is None:
        pos = _to_float(el.get(flat_pos_attr))

    if link_id is None or pos is None:
        combined = (el.get(child_tag) or "").strip()
        tokens = combined.split()
        if len(tokens) >= 2:
            if link_id is None:
                link_id = tokens[0] or None
            if pos is None:
                pos = _to_float(tokens[1])

    if not link_id or pos is None:
        return None
    link = link_index.get(link_id)
    if link is None:
        return None
    resolved = point_at_pos(link.get("points"), pos)
    if resolved is None:
        return None
    x, y, angle = resolved
    return {"link": link_id, "pos": pos, "x": x, "y": y, "angle": angle}


def _parse_traveltime_sections_geo(root: ET.Element, link_index: dict) -> list:
    result = []
    for el in root.findall(".//vehicleTravelTimeMeasurements/vehicleTravelTimeMeasurement"):
        start = _resolve_tt_endpoint(el, "start", "startLink", "startPos", link_index)
        end = _resolve_tt_endpoint(el, "end", "endLink", "endPos", link_index)
        if start is None or end is None:
            continue
        result.append({
            "id": el.get("no", ""),
            "name": el.get("name", ""),
            "start": start,
            "end": end,
        })
    return result


def _parse_ped_areas(root: ET.Element) -> list:
    """Viswalk pedestrian areas: ``<areas><area no name><points><point x y/>
    ...</points></area></areas>``. Polygon coordinates are used verbatim —
    they're already absolute, no link/pos resolution needed."""
    result = []
    for el in root.findall(".//areas/area"):
        points_el = el.find("points")
        polygon = []
        if points_el is not None:
            for pt in points_el.findall("point"):
                x = _to_float(pt.get("x"))
                y = _to_float(pt.get("y"))
                if x is None or y is None:
                    continue
                polygon.append([x, y])
        if not polygon:
            continue
        result.append({"id": el.get("no", ""), "name": el.get("name", ""), "polygon": polygon})
    return result


def parse_network_elements(root: ET.Element, links: list | None = None) -> dict:
    """Extract every Tier-1 static network element type Vissim renders in its
    2D editor, resolved to absolute placement. Every list is always present
    (possibly empty); elements referencing unknown/unresolvable links are
    skipped rather than included with null coordinates. Never raises —
    malformed attributes on any single element just drop that element."""
    links = links if links is not None else _parse_links(root)
    link_index = {lk["id"]: lk for lk in links if lk.get("id")}

    return {
        "signal_heads": _parse_signal_heads(root, link_index),
        "vehicle_inputs": _parse_vehicle_inputs(root, link_index),
        "reduced_speed_areas": _parse_lane_pos_elements(root, ".//reducedSpeedAreas/reducedSpeedArea", link_index, with_length=True),
        "des_speed_decisions": _parse_lane_pos_elements(root, ".//desSpeedDecisions/desSpeedDecision", link_index),
        "detectors": _parse_lane_pos_elements(root, ".//detector", link_index, with_length=True),
        "data_collection_points": _parse_lane_pos_elements(root, ".//dataCollectionPoints/dataCollectionPoint", link_index),
        "traveltime_sections_geo": _parse_traveltime_sections_geo(root, link_index),
        "queue_counters": _parse_link_pos_elements(root, ".//queueCounters/queueCounter", link_index),
        "stop_signs": _parse_lane_pos_elements(root, ".//stopSigns/stopSign", link_index),
        "pt_stops": _parse_lane_pos_elements(root, ".//ptStops/ptStop", link_index, with_length=True),
        "ped_areas": _parse_ped_areas(root),
    }

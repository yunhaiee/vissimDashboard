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

        geom = link.find("geometry")
        pts_el = geom.find("linkPolyPts") if geom is not None else None
        points = []
        if pts_el is not None:
            for pt in pts_el.findall("linkPolyPoint"):
                x = float(pt.get("x", 0))
                y = float(pt.get("y", 0))
                points.append([x, y])

        length = _polyline_length(points)

        is_connector = (
            link.find("fromLinkEndPt") is not None
            or link.find("toLinkEndPt") is not None
        )
        result.append({
            "id": link_id,
            "name": name,
            "points": points,
            "lanes": lane_count,
            "length": round(length, 2),
            "is_connector": is_connector,
        })
    return result


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


def _node_link_ids(root: ET.Element) -> dict:
    """Map each node id to the set of link ids whose segments cross it."""
    nodes_el = root.find(".//nodes")
    mapping: dict[str, set] = {}
    if nodes_el is None:
        return mapping
    for node in nodes_el.findall("node"):
        nid = node.get("no", "")
        mapping[nid] = {seg.get("link", "") for seg in node.findall(".//linkSegment") if seg.get("link")}
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

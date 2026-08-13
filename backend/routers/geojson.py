"""Convert VISSIM network geometry to WGS84 GeoJSON for Leaflet.

TWO TRANSFORMS, AND WHICH DATASET GETS WHICH
--------------------------------------------
U-1-map (docs/ux-backlog.md, the user's detailed elaboration item #7). Reported
from live use: the dashboard MAP was dead for every genesis-published dataset
while the 도식 worked. The cause is below — this module was pinned to ONE
transform, fitted to one client's network:

* **A consultant's .inpx (unchanged).** Coordinates whose datum nobody recorded,
  so placing them on the earth means FITTING: the five ``DEFAULT_MAP_*``
  Helmert constants (least squares over 9 surveyed intersections, ~14 m max
  residual) on top of the file's own ``refPointMap``/``refPointNet`` anchor.
  Every byte of that path is as it was; a dataset with no recorded
  georeference takes it and nothing else.
* **A genesis-published network (new).** Built FROM WGS84 degrees by
  ``services/genesis/project.py``'s local tangent plane, whose ``inverse`` is
  exact, and whose origin ``services/genesis_jobs.py`` now records into the
  dataset's own metadata at publish. So this dataset carries its own transform
  and is inverted with it. It has to be: ``services/genesis/emit.py``
  deliberately writes NO ``refPointMap`` (it makes no absolute claim), so
  ``_load_transform`` 500s on one — and even with an anchor, Perth's constants
  would put a Daejeon network in the Indian Ocean.

The choice is made per request from ``datasets.get_active_dataset_georeference()``
and is never a guess: no record means the Helmert path, exactly as before.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import logging
import math
import os
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree as ET

from fastapi import APIRouter, HTTPException, Query

from services.datasets import get_active_dataset_georeference, get_active_inpx_path
from services.genesis.project import PROJECTION_KIND, LocalTangentPlane, is_valid_origin_deg
from services.inpx_parser import parse_network, parse_network_root
from services.scenario_project import build_scenario_tree, can_build_scenario_tree

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/geojson", tags=["geojson"])

_cache: dict[tuple[float, str, str, float, float, float, float, float], dict] = {}

# Georeferencing for the SouthSt (Murdoch, Perth) network. The inpx
# refPointMap/refPointNet anchor is imprecise (~11 km off) and there is no
# EPSG/projection metadata, so these values come from a Helmert (rotation +
# uniform scale + translation) least-squares fit to 9 surveyed intersection
# coordinates (max residual ~14 m). Override via the VISSIM_MAP_* env vars or
# the geojson query params to re-fit.
DEFAULT_MAP_OFFSET_X = float(os.getenv("VISSIM_MAP_OFFSET_X", "14427.6419"))
DEFAULT_MAP_OFFSET_Y = float(os.getenv("VISSIM_MAP_OFFSET_Y", "-4221.3566"))
DEFAULT_MAP_ROTATION_DEG = float(os.getenv("VISSIM_MAP_ROTATION_DEG", "-0.45759"))
DEFAULT_MAP_SCALE_X = float(os.getenv("VISSIM_MAP_SCALE_X", "1.178200"))
DEFAULT_MAP_SCALE_Y = float(os.getenv("VISSIM_MAP_SCALE_Y", "1.178200"))


@dataclass(frozen=True)
class GeoTransform:
    ref_map_x: float
    ref_map_y: float
    ref_net_x: float
    ref_net_y: float
    north_dir: float

    def to_wgs84(
        self,
        vx: float,
        vy: float,
        *,
        offset_x: float,
        offset_y: float,
        rotation_deg: float,
        scale_x: float,
        scale_y: float,
    ) -> tuple[float, float]:
        dx = vx - self.ref_net_x
        dy = vy - self.ref_net_y
        theta = math.radians(self.north_dir + rotation_deg)

        rx = dx * math.cos(theta) - dy * math.sin(theta)
        ry = dx * math.sin(theta) + dy * math.cos(theta)

        mx = self.ref_map_x + (rx * scale_x) + offset_x
        my = self.ref_map_y + (ry * scale_y) + offset_y

        lon = mx / 20037508.34 * 180
        lat = math.degrees(math.atan(math.sinh(math.pi * my / 20037508.34)))
        return lat, lon


@lru_cache(maxsize=16)
def _load_transform(mtime: float, inpx_path_str: str, scenario_id: str) -> GeoTransform:
    inpx_path = Path(inpx_path_str)
    if can_build_scenario_tree(scenario_id):
        root = build_scenario_tree(inpx_path, scenario_id).getroot()
    else:
        root = ET.parse(inpx_path).getroot()

    net_para = root.find(".//netPara")
    if net_para is None:
        raise HTTPException(status_code=500, detail="netPara not found in INPX")

    ref_map = net_para.find("refPointMap")
    ref_net = net_para.find("refPointNet")
    if ref_map is None or ref_net is None:
        raise HTTPException(status_code=500, detail="Reference points not found in INPX")

    return GeoTransform(
        ref_map_x=float(ref_map.get("x", "0")),
        ref_map_y=float(ref_map.get("y", "0")),
        ref_net_x=float(ref_net.get("x", "0")),
        ref_net_y=float(ref_net.get("y", "0")),
        north_dir=float(net_para.get("northDir", "0")),
    )


def _cache_key(mtime: float, inpx_path: str, scenario_id: str | None) -> tuple[float, str, str, float, float, float, float, float]:
    return (
        mtime,
        inpx_path,
        scenario_id or "",
        DEFAULT_MAP_OFFSET_X,
        DEFAULT_MAP_OFFSET_Y,
        DEFAULT_MAP_ROTATION_DEG,
        DEFAULT_MAP_SCALE_X,
        DEFAULT_MAP_SCALE_Y,
    )


#: One point in Vissim's own units → ``(lat, lon)``. Both transforms below are
#: reduced to this signature so the feature loops call ONE thing and cannot
#: quietly grow a third branch inside them.
PointToWgs84 = Callable[[float, float], "tuple[float, float]"]


def _dataset_plane() -> LocalTangentPlane | None:
    """The active dataset's OWN local tangent plane, or ``None``.

    ``None`` for every consultant upload and for the packaged demo — they carry
    no georeference record, and the Helmert path is their transform. The
    ``kind`` is checked rather than assumed: this function knows how to invert
    exactly one projection, and a record naming a different one must fall
    through to a named failure rather than be inverted with the wrong algebra.
    """
    record = get_active_dataset_georeference()
    if not record or record.get("kind") != PROJECTION_KIND:
        return None
    lat = record.get("origin_lat_deg")
    lon = record.get("origin_lon_deg")
    # Range-checked, not merely type-checked: a swapped pair is numeric on both
    # fields and would draw the whole network mirrored onto the far side of the
    # planet. `None` falls back to the Helmert path, which for a genesis dataset
    # then reports its missing anchor honestly instead of inventing a place.
    if not is_valid_origin_deg(lat, lon):
        return None
    return LocalTangentPlane(lat0_deg=float(lat), lon0_deg=float(lon))


def _georeference_signature() -> tuple:
    """What the cached FeatureCollection was built WITH, so a dataset swap that
    changes only the transform cannot be served from the previous one's entry."""
    plane = _dataset_plane()
    return () if plane is None else (plane.lat0_deg, plane.lon0_deg)


@router.get("/network")
def get_geojson_network(
    scenario_id: str | None = Query(None),
    offset_x: float = Query(DEFAULT_MAP_OFFSET_X),
    offset_y: float = Query(DEFAULT_MAP_OFFSET_Y),
    rotation_deg: float = Query(DEFAULT_MAP_ROTATION_DEG),
    scale_x: float = Query(DEFAULT_MAP_SCALE_X),
    scale_y: float = Query(DEFAULT_MAP_SCALE_Y),
):
    inpx_path = get_active_inpx_path()
    try:
        mtime = os.path.getmtime(inpx_path)
    except OSError:
        raise HTTPException(status_code=404, detail="INPX file not found")

    plane = _dataset_plane()
    cache_key = (
        mtime,
        str(inpx_path),
        scenario_id or "",
        offset_x,
        offset_y,
        rotation_deg,
        scale_x,
        scale_y,
    ) + _georeference_signature()
    if cache_key in _cache:
        return _cache[cache_key]

    if can_build_scenario_tree(scenario_id):
        logger.info("Building GeoJSON for scenario %s from %s", scenario_id, inpx_path)
        net = parse_network_root(build_scenario_tree(inpx_path, scenario_id).getroot())
    else:
        logger.info("Building GeoJSON from %s", inpx_path)
        net = parse_network(str(inpx_path))

    # Modifications 폴더에서 추가 노드 병합 + 기존 노드 이름 패치
    if not scenario_id:
        _node_by_id = {n["id"]: n for n in net["nodes"]}
        _extra: dict[str, dict] = {}
        _mods_dir = inpx_path.parent / "Modifications"
        if _mods_dir.is_dir():
            for _f in sorted(_mods_dir.iterdir()):
                if _f.suffix.lower() != ".trax":
                    continue
                try:
                    for _node in ET.parse(_f).getroot().findall(".//node"):
                        _nid = _node.get("no", "")
                        if not _nid:
                            continue
                        _trax_name = _node.get("name", "")
                        if _nid in _node_by_id:
                            _existing_name = _node_by_id[_nid].get("name", "")
                            if _trax_name and (not _existing_name or _existing_name == f"Node {_nid}"):
                                _node_by_id[_nid]["name"] = _trax_name
                            continue
                        if _nid in _extra:
                            continue
                        _geom = _node.find("geometry")
                        _pts_el = _geom.find("points") if _geom is not None else None
                        _pts = []
                        if _pts_el is not None:
                            for _pt in _pts_el.findall("point"):
                                _pts.append([float(_pt.get("x", 0)), float(_pt.get("y", 0))])
                        if not _pts:
                            continue
                        _cx = sum(p[0] for p in _pts) / len(_pts)
                        _cy = sum(p[1] for p in _pts) / len(_pts)
                        _extra[_nid] = {"id": _nid, "name": _trax_name, "x": _cx, "y": _cy}
                except Exception:
                    continue
        net["nodes"].extend(_extra.values())

    # THE FORK. A dataset that recorded its own plane is inverted with it; every
    # other dataset loads the Helmert anchor and takes the same five constants
    # it always did. ``_load_transform`` is not even CALLED on the genesis path,
    # which is the point: a genesis .inpx carries no refPointMap and that call
    # is what used to 500 the map for it.
    if plane is not None:
        to_wgs84: PointToWgs84 = plane.inverse
    else:
        transform = _load_transform(mtime, str(inpx_path), scenario_id or "")

        def to_wgs84(vx: float, vy: float) -> tuple[float, float]:
            return transform.to_wgs84(
                vx,
                vy,
                offset_x=offset_x,
                offset_y=offset_y,
                rotation_deg=rotation_deg,
                scale_x=scale_x,
                scale_y=scale_y,
            )

    features = []
    for link in net["links"]:
        if len(link["points"]) < 2:
            continue
        if link.get("is_connector"):
            continue
        coords = []
        for vx, vy in link["points"]:
            lat, lon = to_wgs84(vx, vy)
            coords.append([lon, lat])
        features.append({
            "type": "Feature",
            "id": link["id"],
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "id": link["id"],
                "name": link["name"],
                "lanes": link["lanes"],
                "length": link["length"],
                "type": "link",
            },
        })

    for node in net["nodes"]:
        lat, lon = to_wgs84(node["x"], node["y"])
        features.append({
            "type": "Feature",
            "id": f"node_{node['id']}",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "id": node["id"],
                "name": node["name"],
                "type": "node",
            },
        })

    result = {"type": "FeatureCollection", "features": features}
    _cache[cache_key] = result
    return result


@router.get("/signals")
def get_signals():
    """Return signal controller info matched to nodes (by name)."""
    from services.signal_parser import (
        infer_phase_movements_for_controller,
        match_sc_to_nodes,
        match_sc_to_nodes_by_signal_heads,
        parse_signal_controllers,
    )

    inpx_path = get_active_inpx_path()
    try:
        os.path.getmtime(inpx_path)
    except OSError:
        raise HTTPException(status_code=404, detail="INPX file not found")

    net = parse_network(str(inpx_path))

    # Build node_id → name lookup, including TRAX nodes
    node_names: dict[str, str] = {n["id"]: n["name"] for n in net["nodes"]}
    node_centers: dict[str, tuple[float, float]] = {
        n["id"]: (float(n.get("x", 0.0)), float(n.get("y", 0.0))) for n in net["nodes"]
    }
    mods_dir = inpx_path.parent / "Modifications"
    if mods_dir.is_dir():
        for _f in sorted(mods_dir.iterdir()):
            if _f.suffix.lower() != ".trax":
                continue
            try:
                for nd in ET.parse(_f).getroot().findall(".//node"):
                    nid = nd.get("no", "")
                    nname = nd.get("name", "")
                    if nid and nid not in node_names:
                        node_names[nid] = nname
                    elif nid and not node_names.get(nid):
                        node_names[nid] = nname
                    if nid and nid not in node_centers:
                        geom = nd.find("geometry")
                        pts_el = geom.find("points") if geom is not None else None
                        pts = []
                        if pts_el is not None:
                            for pt in pts_el.findall("point"):
                                pts.append((float(pt.get("x", "0")), float(pt.get("y", "0"))))
                        if pts:
                            cx = sum(p[0] for p in pts) / len(pts)
                            cy = sum(p[1] for p in pts) / len(pts)
                            node_centers[nid] = (cx, cy)
            except Exception:
                pass

    sc_map = parse_signal_controllers(inpx_path)
    matched = match_sc_to_nodes(sc_map, node_names)
    # Name matching fails when nodes have no name (SouthSt). Fill any gaps by
    # matching controllers to nodes via their signal-head links.
    for node_id, sc in match_sc_to_nodes_by_signal_heads(inpx_path, sc_map).items():
        if matched.get(node_id) is None:
            matched[node_id] = sc

    result = []
    for node_id in sorted(node_names.keys(), key=lambda x: int(x) if x.isdigit() else 999):
        node_name = node_names.get(node_id, f"Node {node_id}")
        sc = matched.get(node_id)
        phases = []
        if sc:
            phase_movements = infer_phase_movements_for_controller(
                inpx_path,
                sc_no=sc["sc_no"],
                node_center=node_centers.get(node_id, (0.0, 0.0)),
                phases=sc["phases"],
            )
            for idx, phase in enumerate(sc["phases"]):
                enriched = dict(phase)
                enriched["movements"] = phase_movements[idx] if idx < len(phase_movements) else []
                phases.append(enriched)

        result.append({
            "node_id": node_id,
            "node_name": node_name,
            "sc_no": sc["sc_no"] if sc else None,
            "sc_name": sc["sc_name"] if sc else None,
            "has_signal": sc is not None and sc["cycle_time"] > 0,
            "cycle_time": sc["cycle_time"] if sc else None,
            "num_signal_groups": sc["num_signal_groups"] if sc else None,
            "num_phases": sc["num_phases"] if sc else None,
            "phases": phases if sc else [],
        })

    return result


@router.get("/center")
def get_map_center(
    scenario_id: str | None = Query(None),
    offset_x: float = Query(DEFAULT_MAP_OFFSET_X),
    offset_y: float = Query(DEFAULT_MAP_OFFSET_Y),
    rotation_deg: float = Query(DEFAULT_MAP_ROTATION_DEG),
    scale_x: float = Query(DEFAULT_MAP_SCALE_X),
    scale_y: float = Query(DEFAULT_MAP_SCALE_Y),
):
    inpx_path = get_active_inpx_path()
    try:
        mtime = os.path.getmtime(inpx_path)
    except OSError:
        raise HTTPException(status_code=404, detail="INPX file not found")

    # Same fork as ``/network``, and for the same reason: a genesis .inpx has no
    # refPointMap for ``_load_transform`` to read. Its centre is the plane's own
    # ORIGIN, which is not an approximation — ``project.origin_for_bounds`` puts
    # the origin at the centre of the requested region's bounding box, so (0, 0)
    # in this dataset's metres IS the middle of the area somebody asked for.
    plane = _dataset_plane()
    if plane is not None:
        lat, lon = plane.inverse(0.0, 0.0)
        return {"lat": lat, "lon": lon, "zoom": 16}

    transform = _load_transform(mtime, str(inpx_path), scenario_id or "")
    lat, lon = transform.to_wgs84(
        transform.ref_net_x,
        transform.ref_net_y,
        offset_x=offset_x,
        offset_y=offset_y,
        rotation_deg=rotation_deg,
        scale_x=scale_x,
        scale_y=scale_y,
    )
    return {"lat": lat, "lon": lon, "zoom": 16}

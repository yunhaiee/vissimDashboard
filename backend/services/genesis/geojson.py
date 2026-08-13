"""A genesis draft network → WGS84 GeoJSON, computed on the SAME plane it was built on.

U-1-map (docs/ux-backlog.md, the user's detailed elaboration item #1). The
findings map drew ``genesis_json``'s raw local-tangent-plane metres into a bare
SVG with no basemap; the user's report from live use was that it is illegible.
The fix is a real basemap, and a basemap needs degrees.

**WHY THIS IS LEGITIMATE AND ``routers/geojson.py``'s Helmert fit is not the
model here.** A consultant's ``.inpx`` arrives with coordinates whose datum
nobody recorded, so placing it on the earth means FITTING a transform to
surveyed points — five constants with a ~14 m max residual. A genesis draft is
the opposite case: it was BUILT from WGS84 degrees by
:class:`services.genesis.project.LocalTangentPlane`, whose ``inverse`` is the
exact algebraic inverse of the ``forward`` that made the metres, and whose
origin is recorded in the draft's own metadata (``metadata.projection``:
``origin_lat_deg``/``origin_lon_deg``, written by ``draft.py``'s ``_metadata``
from ``plane.metadata()``). Reconstructing the plane from those two numbers and
inverting is not a georeference CLAIM — it is undoing a transform this program
performed, and it returns exactly the degrees OpenStreetMap handed us.

**WHAT THIS MODULE REFUSES TO DO.** It never guesses an origin. A draft whose
metadata carries no ``origin_lat_deg``/``origin_lon_deg`` pair raises
:class:`DraftGeoJsonRefused` rather than falling back to (0, 0), to the bounding
box's centre re-derived from the points, or to any other plausible-looking
number: a map centred off the coast of Ghana is a visible failure, but a map
centred on a re-derived origin is a SILENT one — every link would be drawn in
the right shape at the wrong place, which is precisely the class of wrong answer
this product exists to refuse.

``project.py``'s own "NOT A GEOREFERENCE" note still stands for the EMITTED
``.inpx``: that file carries no datum, no EPSG code and no ``refPointMap``, and
nothing here changes it. What this module states is narrower and true — where
the OSM ways these metres were derived from actually are.
"""
from __future__ import annotations

from typing import Any

from services.genesis.project import LocalTangentPlane, is_valid_origin_deg

#: The two metadata fields that ARE the transform. Named as constants because
#: the refusal message quotes them: a caller holding a draft that predates the
#: field needs to be told which key is missing, not merely that something is.
ORIGIN_LAT_KEY = "origin_lat_deg"
ORIGIN_LON_KEY = "origin_lon_deg"

#: Feature ``properties.kind`` values. Links and connectors are drawn from the
#: same LineString geometry but are NOT the same object, and the renderer's
#: finding-highlight semantics differ per kind — so the kind travels with the
#: feature rather than being inferred from an id range on the client.
KIND_LINK = "link"
KIND_CONNECTOR = "connector"


class DraftGeoJsonRefused(Exception):
    """This draft cannot be placed on the earth, with the reason named.

    Carries a stable ``code`` the way every other genesis refusal does, so the
    browser translates the code and prints the reason verbatim.
    """

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code
        self.reason = reason


def plane_for_draft(draft: dict[str, Any]) -> LocalTangentPlane:
    """Reconstruct the EXACT plane a draft was projected onto.

    Read from ``metadata.projection`` — ``draft.py``'s ``_metadata`` writes
    ``{**plane.metadata(), **check.as_dict()}`` there, and ``plane.metadata()``
    carries the origin rounded to 9 decimal places (~0.1 mm, four orders of
    magnitude below the projection's own 1 m budget). The radii in that same
    dict are DERIVED from the origin, so they are recomputed here rather than
    read back: two sources for one number is how a hand-edited artifact ends up
    with a plane that is internally inconsistent.
    """
    projection = (draft.get("metadata") or {}).get("projection") or {}
    lat = projection.get(ORIGIN_LAT_KEY)
    lon = projection.get(ORIGIN_LON_KEY)
    if lat is None or lon is None:
        raise DraftGeoJsonRefused(
            "genesis_draft_has_no_projection_origin",
            (
                "This generation's draft carries no recorded projection origin "
                f"(metadata.projection.{ORIGIN_LAT_KEY}/{ORIGIN_LON_KEY}), so its metres "
                "cannot be taken back to latitude/longitude. Nothing is guessed: an "
                "invented origin would draw every link in the right shape at the wrong "
                "place on the earth."
            ),
        )
    # PRESENT IS NOT USABLE. A recorded origin still has to be a coordinate: the
    # failure this catches is a SWAPPED pair (lat 115.836 / lon -32.07 — a Perth
    # coordinate written the wrong way round), which is numeric on both fields
    # and would sail through any is-it-a-number guard. `cos(115.836°)` is
    # negative, so east and west trade places and the whole network is mirrored
    # onto the far side of the planet with nothing on screen to say so. Refused
    # by range, with both values quoted, rather than transformed confidently.
    if not is_valid_origin_deg(lat, lon):
        raise DraftGeoJsonRefused(
            "genesis_draft_projection_origin_out_of_range",
            (
                "This generation's draft records a projection origin that is not a WGS84 "
                f"coordinate ({ORIGIN_LAT_KEY}={lat!r}, {ORIGIN_LON_KEY}={lon!r}; latitude "
                "must be within ±90 and longitude within ±180). The commonest cause is the "
                "two values being stored the wrong way round, which no arithmetic here can "
                "tell apart from a real coordinate — so it is refused rather than inverted "
                "into a network drawn on the far side of the planet."
            ),
        )
    return LocalTangentPlane(lat0_deg=float(lat), lon0_deg=float(lon))


def _line_string(
    points: Any, plane: LocalTangentPlane
) -> list[list[float]] | None:
    """One polyline's ``[[lon, lat], …]``, or ``None`` when it is not a line.

    A GeoJSON LineString needs two positions; a link with fewer is dropped from
    the FeatureCollection rather than emitted as a degenerate geometry — the
    gate's V7 already names a zero-geometry link as a finding, so silence here
    hides nothing that the verdict beside the map does not state.
    """
    if not isinstance(points, list) or len(points) < 2:
        return None
    coordinates: list[list[float]] = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            return None
        lat, lon = plane.inverse(float(point[0]), float(point[1]))
        coordinates.append([lon, lat])
    return coordinates


def draft_to_geojson(draft: dict[str, Any]) -> dict[str, Any]:
    """``DraftNetwork.model_dump(mode="json")`` → a WGS84 FeatureCollection.

    Links and connectors become LineStrings carrying ``id``, ``kind`` and
    ``name`` — the three properties the map's own highlight logic resolves
    against (``lib/genesisArtifacts.ts``'s ``resolveFindingHighlight`` matches a
    finding's subject by id or by exact name), and nothing else: a property the
    renderer does not read is a field that drifts.

    Nodes are deliberately NOT emitted. The map draws findings on links and
    connectors; a node's ``polygon`` is intersection paint, and shipping it
    would double the payload for geometry no highlight resolves against.

    The FeatureCollection carries two foreign members the renderer needs and
    must not re-derive: ``origin`` (the plane this was inverted with, so a
    reader can reproduce it) and the standard ``bbox``, so a map can fit the
    network without walking every coordinate itself.
    """
    plane = plane_for_draft(draft)

    features: list[dict[str, Any]] = []
    west = south = float("inf")
    east = north = float("-inf")

    def emit(items: Any, kind: str) -> None:
        nonlocal west, south, east, north
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            coordinates = _line_string(item.get("points"), plane)
            if coordinates is None:
                continue
            for lon, lat in coordinates:
                west = min(west, lon)
                east = max(east, lon)
                south = min(south, lat)
                north = max(north, lat)
            features.append(
                {
                    "type": "Feature",
                    "id": f"{kind}-{item.get('id')}",
                    "geometry": {"type": "LineString", "coordinates": coordinates},
                    "properties": {
                        "id": item.get("id"),
                        "kind": kind,
                        "name": item.get("name") or "",
                    },
                }
            )

    emit(draft.get("links"), KIND_LINK)
    emit(draft.get("connectors"), KIND_CONNECTOR)

    payload: dict[str, Any] = {
        "type": "FeatureCollection",
        "features": features,
        "origin": {"lat": plane.lat0_deg, "lon": plane.lon0_deg},
    }
    if features:
        payload["bbox"] = [west, south, east, north]
    return payload


__all__ = [
    "DraftGeoJsonRefused",
    "KIND_CONNECTOR",
    "KIND_LINK",
    "ORIGIN_LAT_KEY",
    "ORIGIN_LON_KEY",
    "draft_to_geojson",
    "plane_for_draft",
]

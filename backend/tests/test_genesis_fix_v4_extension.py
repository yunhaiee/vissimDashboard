"""Unit #40 — the boundary-attributed runway shortage gets a PROPOSED LASSO.

Unit #39 measured *whether* a ``v4_lane_drop_without_runway`` shortage belongs
to the requested model boundary. This unit derives the treatment that
attribution earns: ``genesis_jobs.derive_v4_boundary_extension`` takes the
attribution plus the job's own recorded requested polygon and returns the
user's own lasso with a FINGER spliced into the single ring edge the corridor
crosses — long enough to cover the measured deficit plus a stated margin, wide
enough for the corridor's own declared roadway plus that same margin.

Three properties carry the whole design, and each has its own test below:

* **it is a splice, never a hull.** Every corner the user drew survives, in
  order; the patch is inserted between the crossing edge's two ends; area the
  user deliberately excluded stays excluded (the convex hull of the same points
  would swallow it, which is why §1's last assertion exists);
* **the direction is measured, the length is required.** The corridor beyond
  the boundary is walked over the SOURCE's own nodes; when the source itself
  runs out — which is the real link-90 case, because the extract is clipped at
  the very boundary being extended — the remainder is drawn on the last
  measured bearing and reported as ``extrapolated_m``, never folded into the
  measured figure;
* **every refusal is NAMED.** An interior-attributed shortage is not eligible
  (it is the road's, and widening the region for it would be a lie in the shape
  of a polygon), and a splice that would self-intersect, straddle a corner,
  overflow its own edge or bust the region page's area cap refuses by name
  rather than emitting a broken ring for a human to discover on a map.

Fixture shape follows ``tests/test_genesis_fix_v4_boundary.py`` (unit #39):
a private tmp workspace per test, hand-written parent networks through
``_seed_job`` + the REAL gate where a stored verdict is needed, a hand-written
source ``.osm`` parsed by the REAL ``services.genesis.osm`` parser, the REAL
``services.osm_fetch`` region kernels judging the emitted shape, no COM
anywhere.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from conftest import admin_headers
from services import genesis_jobs, osm_fetch, workspaces
from services.agent import fixes as agent_fixes
from services.agent import genesis_fixes
from services.genesis.osm import _inside_polygon

from tests.test_genesis_gate import network, straight_pair
from tests.test_genesis_jobs import _seed_job

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Same shape as unit #39's fixture of the same name — duplicated rather
    than imported across test modules (the repo's existing convention only
    cross-imports plain helper FUNCTIONS)."""
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "legacy-uploads")
    default_id = workspaces.default_workspace_id()
    token = workspaces.set_current_workspace_id(default_id)
    try:
        yield tmp_path / "workspaces"
    finally:
        workspaces.reset_current_workspace_id(token)


@pytest.fixture()
def client(workspace_root: Path) -> TestClient:
    return TestClient(main.app)


# ---------------------------------------------------------------------------
# The synthetic site: a square lasso with one corridor entering from the WEST
# ---------------------------------------------------------------------------

_CODE = "v4_lane_drop_without_runway"
_SUBJECT = "1 (link 1)"

_WAY = 9007
_BOUNDARY_NODE = 999001
#: ``straight_pair(a_lanes=2, a_len=50.0)`` makes the REAL gate refuse with
#: required 200.0 / available 50.0 — the same fixture unit #39 pins — so every
#: derivation below closes a 150.0 m deficit.
_DEFICIT = 150.0
_MARGIN = 30.0  # the deriver's own round 20%, restated so the test can assert it
_NEEDED = 180.0

_CORRIDOR_LAT = 36.3520
_SOUTH, _NORTH = 36.3480, 36.3560
_WEST, _EAST = 127.3760, 127.3860

#: The lasso, in the order a user drew it: NW → NE → SE → SW. Its edge 3 (from
#: the SW corner up to the NW one) is the WEST edge the corridor crosses.
_SQUARE = [
    {"lat": _NORTH, "lon": _WEST},
    {"lat": _NORTH, "lon": _EAST},
    {"lat": _SOUTH, "lon": _EAST},
    {"lat": _SOUTH, "lon": _WEST},
]
_WEST_EDGE_INDEX = 3

#: Source nodes of the corridor way, west → east. The first two lie outside the
#: lasso and are what the trim cut.
_N_FAR, _N_OUT, _N_IN, _N_EAST = 5001, 5002, 5003, 5004
_LON_FAR, _LON_OUT, _LON_IN, _LON_EAST = 127.3720, 127.3750, 127.3800, 127.3840


def _ring(points: list[dict[str, float]]) -> tuple[tuple[float, float], ...]:
    return tuple((float(p["lat"]), float(p["lon"])) for p in points)


def _census_link(
    link_id: int,
    way_id: int,
    *,
    from_node: int,
    to_node: int,
    lanes: int = 2,
    forward: bool = True,
) -> dict[str, Any]:
    """One VALID ``DraftLink`` record. The 50.0 m polyline is the judge's own
    ``available_m`` for the seeded network, so unit #39's re-trace cross-check
    agrees and this unit gets a real attribution to work from."""
    return {
        "id": link_id,
        "name": f"link {link_id}",
        "osm_way_id": way_id,
        "segment_index": 0,
        "forward": forward,
        "lanes": lanes,
        "lane_width_m": 3.5,
        "points": [[0.0, 0.0], [50.0, 0.0]],
        "lane_provenance": {},
        "highway": "secondary",
        "from_node_osm_id": from_node,
        "to_node_osm_id": to_node,
        "side_offset_m": 0.0,
    }


def _way_trim(
    *,
    way_id: int = _WAY,
    end: str = "start",
    synthesized_node_id: int = _BOUNDARY_NODE,
    metres_cut: float = 358.0,
    nodes_cut: int = 2,
) -> dict[str, Any]:
    """One ``metadata['boundary']['way_trims']`` record in the generator's own
    recorded shape (verified against job 5e550c739aed's real census)."""
    return {
        "way_id": way_id,
        "end": end,
        "synthesized_node_id": synthesized_node_id,
        "metres_cut": metres_cut,
        "nodes_cut": nodes_cut,
        "highway": "secondary",
        "reason": "tail_outside_requested_bbox_cut",
        "detail": f"{nodes_cut} node(s) of this way's {end} lay outside the requested polygon",
    }


def _census(
    link_entries: list[dict[str, Any]],
    *,
    way_trims: list[dict[str, Any]] | None = None,
    polygon: list[dict[str, float]] | None = _SQUARE,
) -> dict[str, Any]:
    """A census that VALIDATES as a real ``DraftNetwork``, carrying the trim
    records unit #39 classifies against AND the requested polygon this unit
    splices into — both in the shapes the real generator records."""
    boundary: dict[str, Any] = {"way_trims": way_trims or []}
    if polygon is not None:
        boundary["requested_polygon_wgs84"] = [dict(point) for point in polygon]
        boundary["requested_bbox_wgs84"] = {
            "min_lat": min(p["lat"] for p in polygon),
            "max_lat": max(p["lat"] for p in polygon),
            "min_lon": min(p["lon"] for p in polygon),
            "max_lon": max(p["lon"] for p in polygon),
        }
    return {
        "name": "seeded",
        "source_osm": "seed.osm",
        "metadata": {
            "connectivity": {"dangling_approaches": []},
            "boundary": boundary,
        },
        "links": link_entries,
    }


def _osm_node(node_id: int, lat: float, lon: float) -> str:
    return f'  <node id="{node_id}" lat="{lat}" lon="{lon}"/>\n'


def _osm_way(
    way_id: int,
    node_ids: list[int],
    *,
    name: str | None = None,
    oneway: str | None = None,
    highway: str = "secondary",
) -> str:
    nds = "".join(f'    <nd ref="{n}"/>\n' for n in node_ids)
    tags = f'    <tag k="highway" v="{highway}"/>\n'
    if name is not None:
        tags += f'    <tag k="name" v="{name}"/>\n'
    if oneway is not None:
        tags += f'    <tag k="oneway" v="{oneway}"/>\n'
    return f'  <way id="{way_id}">\n{nds}{tags}  </way>\n'


def _osm(*elements: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<osm version="0.6" generator="unit40-test">\n' + "".join(elements) + "</osm>\n"
    )


def _corridor_source(*, nodes: list[tuple[int, float]] | None = None) -> str:
    """The corridor way, west → east, one-way eastbound (so a vehicle ARRIVES
    at the boundary travelling east and the road upstream of it is the part
    outside the lasso)."""
    entries = nodes or [
        (_N_FAR, _LON_FAR),
        (_N_OUT, _LON_OUT),
        (_N_IN, _LON_IN),
        (_N_EAST, _LON_EAST),
    ]
    return _osm(
        *[_osm_node(node_id, _CORRIDOR_LAT, lon) for node_id, lon in entries],
        _osm_way(_WAY, [node_id for node_id, _lon in entries], name="Grand Ave", oneway="yes"),
    )


def _attach_source_osm(record: dict[str, Any], source_xml: str) -> None:
    path = genesis_jobs.job_dir(record["id"]) / "source.osm"
    path.write_text(source_xml, encoding="utf-8")
    record["source_osm"] = {
        "filename": "source.osm",
        "bytes": path.stat().st_size,
        "sha256": "",
        "stored_as": "source.osm",
    }
    genesis_jobs._upsert(record)


def _seed_parent(
    census: dict[str, Any], source_xml: str | None, *, name: str = "v4_extension_parent"
) -> dict[str, Any]:
    """A seeded job WITHOUT a verdict — everything the deriver reads (census +
    source) and nothing it does not."""
    record = _seed_job(network(links=straight_pair(a_lanes=2, a_len=50.0)), census=census, name=name)
    if source_xml is not None:
        _attach_source_osm(record, source_xml)
    return record


def _seed_refused_parent(census: dict[str, Any], source_xml: str | None) -> dict[str, Any]:
    record = _seed_parent(census, source_xml)
    result = genesis_jobs.gate_and_publish(record["id"])
    assert result["status"] == genesis_jobs.STATUS_REFUSED, result
    return result


def _finding() -> dict[str, Any]:
    return {
        "code": _CODE,
        "subject": _SUBJECT,
        "context": {"lanes": 2, "dropped_lanes": [2], "required_m": 200.0, "available_m": 50.0},
    }


#: ``trim=_TRIMMED`` (the default) gives the attribution unit #39's own trim
#: record; ``trim=None`` gives it NO ``way_trim`` key at all, which is the
#: shape an untrimmed (dangling-approach) boundary is attributed with.
_TRIMMED = object()


def _attribution(
    *,
    status: str = "boundary_attributed",
    deficit_m: float = _DEFICIT,
    trim: Any = _TRIMMED,
    **extra: Any,
) -> dict[str, Any]:
    """A boundary attribution in EXACTLY unit #39's emitted shape."""
    record: dict[str, Any] = {
        "status": status,
        "way_id": _WAY,
        "boundary_node": _BOUNDARY_NODE,
        "deficit_m": deficit_m,
        "boundary_marker": "way_trim",
        "continuation_evidence": {"source_length_available_m": 358.0, "meets_deficit": True},
    }
    record.update(extra)
    if status == "boundary_attributed" and trim is not None:
        record["way_trim"] = _way_trim() if trim is _TRIMMED else trim
    return record


def _derive(
    job_id: str, attribution: dict[str, Any] | None, finding: dict[str, Any] | None = None
) -> dict[str, Any]:
    return genesis_jobs.derive_v4_boundary_extension(
        finding or _finding(),
        attribution,
        draft=genesis_jobs.job_draft_or_none(job_id),
        job_id=job_id,
    )


def _standard_census(**kwargs: Any) -> dict[str, Any]:
    return _census(
        [_census_link(1, _WAY, from_node=_BOUNDARY_NODE, to_node=_N_IN)],
        way_trims=[_way_trim()],
        **kwargs,
    )


def _metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    return genesis_jobs._latlon_metres(a, b)


# ---------------------------------------------------------------------------
# §1 The splice: the user's own ring, plus a finger, and nothing else
# ---------------------------------------------------------------------------


def test_the_extension_is_a_splice_and_not_a_hull(workspace_root: Path):
    """Every original corner survives IN ORDER, the finger's vertices are
    inserted at the crossing edge, the ring is simple, the original interior is
    contained — and the area a hull would have swallowed stays excluded."""
    parent = _seed_parent(_standard_census(), _corridor_source())
    extension = _derive(parent["id"], _attribution())
    assert "reason_code" not in extension, extension

    original = _ring(_SQUARE)
    extended = tuple((lat, lon) for lat, lon in extension["extended_polygon_wgs84"])

    # 1. every original vertex is present, VERBATIM, and in its original order.
    positions = [extended.index(vertex) for vertex in original]
    assert positions == sorted(positions), (positions, extended)
    assert len(extended) == len(original) + extension["corridor_patch"]["vertices_added"]

    # 2. the insertion is at the crossing edge -- everything added sits between
    #    that edge's two ends, which for this lasso is after the LAST corner.
    assert extension["corridor_patch"]["crossing_edge_index"] == _WEST_EDGE_INDEX
    assert extended[: len(original)] == original

    # 3. the ring is simple -- judged by the SAME constructor the fetch itself
    #    is held to, not by a second opinion written here.
    region = osm_fetch.RegionPolygon.from_sequence([list(v) for v in extended])
    assert len(region.vertices) == len(extended)

    # 4. the original interior is CONTAINED: sample points spread through the
    #    lasso the user drew are all inside the widened one.
    for lat in (36.3490, 36.3520, 36.3550):
        for lon in (127.3765, 127.3810, 127.3855):
            assert _inside_polygon(lat, lon, original) is True
            assert _inside_polygon(lat, lon, extended) is True, (lat, lon)

    # 5. THE ANTI-HULL ASSERTION. (36.3555, 127.3745) lies inside the CONVEX
    #    HULL of the lasso plus the finger -- a hull would have swallowed it --
    #    and the user excluded it. It stays excluded.
    assert _inside_polygon(36.3555, 127.3745, original) is False
    assert _inside_polygon(36.3555, 127.3745, extended) is False


def test_the_finger_lies_outside_the_original_and_inside_the_extended(workspace_root: Path):
    """The detour bulges OUTWARD: the corridor patch's own midpoint is outside
    the shape the user drew and inside the one this deriver proposes."""
    parent = _seed_parent(_standard_census(), _corridor_source())
    extension = _derive(parent["id"], _attribution())

    original = _ring(_SQUARE)
    extended = tuple((lat, lon) for lat, lon in extension["extended_polygon_wgs84"])
    centreline = extension["corridor_patch"]["centreline_wgs84"]
    mid_lat = (centreline[0][0] + centreline[-1][0]) / 2.0
    mid_lon = (centreline[0][1] + centreline[-1][1]) / 2.0

    assert _inside_polygon(mid_lat, mid_lon, original) is False
    assert _inside_polygon(mid_lat, mid_lon, extended) is True
    assert extension["added_area_km2"] > 0.0


# ---------------------------------------------------------------------------
# §2 Deficit coverage: the finger reaches past the shortage, by a stated margin
# ---------------------------------------------------------------------------


def test_the_finger_covers_the_deficit_plus_its_stated_margin(workspace_root: Path):
    parent = _seed_parent(_standard_census(), _corridor_source())
    extension = _derive(parent["id"], _attribution())

    patch = extension["corridor_patch"]
    provenance = extension["provenance"]
    assert provenance["deficit_m"] == _DEFICIT
    assert provenance["margin_m"] == _MARGIN
    assert "20%" in provenance["basis"]
    assert patch["length_m"] == pytest.approx(_NEEDED)

    # Measured on the emitted centreline itself, not read back off the field
    # that claims it: the proposed road really is deficit + margin long.
    centreline = [(point[0], point[1]) for point in patch["centreline_wgs84"]]
    walked = sum(
        _metres(centreline[i], centreline[i + 1]) for i in range(len(centreline) - 1)
    )
    assert walked >= _DEFICIT + _MARGIN - 0.5
    assert walked == pytest.approx(_NEEDED, abs=0.5)

    # ...and it is wide enough for the corridor's own declared roadway plus
    # that same margin on each side.
    assert patch["roadway_width_m"] == pytest.approx(7.0)
    assert patch["half_width_m"] == pytest.approx(7.0 / 2 + _MARGIN)


def test_a_source_clipped_at_the_same_lasso_names_its_extrapolation(workspace_root: Path):
    """The REAL link-90 shape: the extract is cut at the very boundary being
    extended, so the source runs out before the deficit does. The remainder is
    drawn on the last MEASURED bearing and reported as extrapolation — never
    folded into the measured figure."""
    trim = _way_trim(nodes_cut=1, metres_cut=89.7)
    parent = _seed_parent(
        _census(
            [_census_link(1, _WAY, from_node=_BOUNDARY_NODE, to_node=_N_IN)],
            way_trims=[trim],
        ),
        _corridor_source(
            nodes=[(_N_OUT, _LON_OUT), (_N_IN, _LON_IN), (_N_EAST, _LON_EAST)]
        ),
    )
    extension = _derive(parent["id"], _attribution(trim=trim))
    patch = extension["corridor_patch"]

    assert patch["extrapolated_m"] > 0.0
    assert patch["measured_source_m"] == pytest.approx(89.7, abs=1.0)
    assert patch["measured_source_m"] + patch["extrapolated_m"] == pytest.approx(
        patch["length_m"], abs=0.05
    )


def _clipped_extension(workspace_root: Path) -> dict[str, Any]:
    """The clipped-source shape above, as an extension payload — the real
    link-90 case, where the finger is longer than the road the source declares."""
    trim = _way_trim(nodes_cut=1, metres_cut=89.7)
    parent = _seed_parent(
        _census(
            [_census_link(1, _WAY, from_node=_BOUNDARY_NODE, to_node=_N_IN)],
            way_trims=[trim],
        ),
        _corridor_source(
            nodes=[(_N_OUT, _LON_OUT), (_N_IN, _LON_IN), (_N_EAST, _LON_EAST)]
        ),
    )
    extension = _derive(parent["id"], _attribution(trim=trim))
    assert extension["corridor_patch"]["extrapolated_m"] > 0.0, extension
    return extension


def test_the_rationale_itself_names_the_extrapolation_not_only_the_payload(
    workspace_root: Path,
):
    """The SENTENCE a reader is given must carry the split, in all three
    languages.

    ``length_m`` is what the finger asks for; ``measured_source_m`` is what the
    source declared. On the real link-90 case they differ by 50 m, and a
    rationale quoting only the 177 reads as a measurement of a road nobody
    measured. The payload has always carried both numbers — this pins that the
    PROSE does too, together with the distinction that makes an extrapolated
    finger honest: widening the lasso widens the EXTRACT REQUEST, and claims
    nothing about the road being there.
    """
    extension = _clipped_extension(workspace_root)
    patch = extension["corridor_patch"]
    proposal = genesis_fixes.propose_genesis_fix(
        _CODE,
        finding=_finding(),
        targets={
            "boundary_attribution": _attribution(trim=_way_trim(nodes_cut=1)),
            "boundary_extension": extension,
        },
    )

    for language in ("ko", "en", "ja"):
        rationale = getattr(proposal, f"rationale_{language}")
        assert f"{patch['measured_source_m']}" in rationale, (language, rationale)
        assert f"{patch['extrapolated_m']}" in rationale, (language, rationale)
        # ...and the total is still there, so the reader sees a SPLIT rather
        # than a substitution.
        assert f"{patch['length_m']}" in rationale, (language, rationale)
    # ...and each language says WHAT the extra metres are: a wider request, not
    # a road. Phrased per language rather than by a shared token, because the
    # sentence is what a reader actually reads.
    assert "추출을 요청" in proposal.rationale_ko
    assert "지어낸 것이 아니라" in proposal.rationale_ko
    assert "EXTRACT REQUEST" in proposal.rationale_en
    assert "claims nothing about the road being there" in proposal.rationale_en
    assert "抽出を要求" in proposal.rationale_ja
    # The refusal itself is untouched by the disclosure.
    assert proposal.outcome == agent_fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == genesis_fixes.RUNWAY_ENDS_AT_REQUESTED_BOUNDARY


def test_a_fully_measured_finger_claims_no_extrapolation(workspace_root: Path):
    """The other half of the pin: the disclosure is MEASURED, not boilerplate.
    When the source declares the whole finger there is nothing extrapolated, and
    the sentence that would name one is absent."""
    parent = _seed_parent(_standard_census(), _corridor_source())
    extension = _derive(parent["id"], _attribution())
    assert extension["corridor_patch"]["extrapolated_m"] == 0.0

    proposal = genesis_fixes.propose_genesis_fix(
        _CODE,
        finding=_finding(),
        targets={
            "boundary_attribution": _attribution(),
            "boundary_extension": extension,
        },
    )
    assert "추출을 요청" not in proposal.rationale_ko
    assert "EXTRACT REQUEST" not in proposal.rationale_en
    assert "抽出を要求" not in proposal.rationale_ja
    # ...but the treatment is still offered, in its own words.
    assert "widened version of the region you requested" in proposal.rationale_en


# ---------------------------------------------------------------------------
# §3 Named refusals — every one of them measured, none of them a silence
# ---------------------------------------------------------------------------


def test_an_interior_attributed_shortage_is_not_eligible(workspace_root: Path):
    """The shortage is the ROAD's. Widening the region would propose an extract
    that cannot contain the road it claims to add — a lie in the shape of a
    polygon."""
    parent = _seed_parent(_standard_census(), _corridor_source())
    extension = _derive(
        parent["id"],
        {"status": "interior", "terminus_node": _BOUNDARY_NODE, "deficit_m": _DEFICIT},
    )
    assert extension["reason_code"] == "interior_not_eligible"
    assert "the road's" in extension["detail"]


@pytest.mark.parametrize("attribution", [None, {}, {"status": "unmeasured"}])
def test_a_missing_attribution_refuses_by_name(workspace_root: Path, attribution: Any):
    parent = _seed_parent(_standard_census(), _corridor_source())
    extension = _derive(parent["id"], attribution)
    assert extension["reason_code"] == "no_boundary_attribution"


def test_a_corner_crossing_has_no_single_edge_to_splice_into(workspace_root: Path):
    """The corridor leaves exactly through a ring VERTEX: a finger there would
    straddle two edges of the user's own shape, so it refuses instead of
    picking one."""
    corner_ring = [
        {"lat": _NORTH, "lon": _WEST},
        {"lat": _NORTH, "lon": _EAST},
        {"lat": _SOUTH, "lon": _EAST},
        {"lat": _SOUTH, "lon": _WEST},
        {"lat": _CORRIDOR_LAT, "lon": _WEST},  # the crossing point, made a corner
    ]
    parent = _seed_parent(_standard_census(polygon=corner_ring), _corridor_source())
    extension = _derive(parent["id"], _attribution())
    assert extension["reason_code"] == "crossing_edge_unidentifiable"
    assert "CORNER" in extension["detail"]


def test_a_mouth_wider_than_its_own_edge_refuses_by_name(workspace_root: Path):
    """The crossing edge is an 11 m sliver of the west wall and the finger is
    67 m across: splicing it would cut across the neighbouring edges of the
    user's shape."""
    sliver_ring = [
        {"lat": _NORTH, "lon": _WEST},
        {"lat": _NORTH, "lon": _EAST},
        {"lat": _SOUTH, "lon": _EAST},
        {"lat": _SOUTH, "lon": _WEST},
        {"lat": _CORRIDOR_LAT - 0.00005, "lon": _WEST},
        {"lat": _CORRIDOR_LAT + 0.00005, "lon": _WEST},
    ]
    parent = _seed_parent(_standard_census(polygon=sliver_ring), _corridor_source())
    extension = _derive(parent["id"], _attribution())
    assert extension["reason_code"] == "patch_mouth_exceeds_crossing_edge"
    assert "m across does not fit on the" in extension["detail"]


def test_a_splice_that_would_cross_the_users_own_shape_refuses_by_name(workspace_root: Path):
    """A tentacle of the user's own lasso runs north-south across the corridor
    180 m out. Every finger VERTEX is still outside the lasso — so the
    outward-bulge check passes — but the finger's walls cross the tentacle, and
    a ring that crosses itself is refused rather than emitted."""
    tentacle_ring = [
        {"lat": _NORTH, "lon": _WEST},
        {"lat": _NORTH, "lon": _EAST},
        {"lat": _SOUTH, "lon": _EAST},
        {"lat": _SOUTH, "lon": _WEST},
        {"lat": 36.3550, "lon": _WEST},
        {"lat": 36.3550, "lon": 127.37455},
        {"lat": 36.3505, "lon": 127.37455},
        {"lat": 36.3505, "lon": 127.37450},
        {"lat": 36.3555, "lon": 127.37450},
        {"lat": 36.3555, "lon": _WEST},
    ]
    parent = _seed_parent(_standard_census(polygon=tentacle_ring), _corridor_source())
    extension = _derive(parent["id"], _attribution())
    assert extension["reason_code"] == "extension_polygon_self_intersects"
    assert "crosses itself" in extension["detail"]


def test_an_extension_over_the_area_cap_quotes_both_numbers(workspace_root: Path):
    """The cap is the region page's own (``osm_fetch.measure_region``), and the
    refusal states the widened measurement AND the limit it broke."""
    tall_ring = [
        {"lat": 36.3700, "lon": _WEST},
        {"lat": 36.3700, "lon": _EAST},
        {"lat": 36.3400, "lon": _EAST},
        {"lat": 36.3400, "lon": _WEST},
    ]
    parent = _seed_parent(_standard_census(polygon=tall_ring), _corridor_source())
    # A 3 km shortage: the finger reaches 3.6 km west and the widened shape's
    # half-extent leaves the projection's validity radius behind.
    extension = _derive(parent["id"], _attribution(deficit_m=3000.0))
    assert extension["reason_code"] == "extension_exceeds_region_cap"

    original = osm_fetch.measure_region(osm_fetch.RegionPolygon.from_sequence(
        [[point["lat"], point["lon"]] for point in tall_ring]
    ))
    assert original.within_region_cap is True, "the lasso itself must be legal"
    numbers = [
        float(token)
        for token in extension["detail"].replace("(", " ").replace(")", " ").split()
        if _is_number(token)
    ]
    assert len(numbers) >= 4, extension["detail"]
    assert "km²" in extension["detail"] and "validity radius" in extension["detail"]


def _is_number(token: str) -> bool:
    try:
        float(token)
    except ValueError:
        return False
    return True


def test_a_rectangle_region_is_refused_rather_than_drawn_as_a_lasso(workspace_root: Path):
    parent = _seed_parent(_standard_census(polygon=None), _corridor_source())
    extension = _derive(parent["id"], _attribution())
    assert extension["reason_code"] == "requested_region_not_a_polygon"
    assert "corners they never drew" in extension["detail"]


def test_a_boundary_node_the_source_cannot_place_refuses_by_name(workspace_root: Path):
    """The untrimmed (dangling-approach) boundary: the marker names a node the
    source holds no coordinate for, so the direction to widen in could only be
    guessed — and a guessed direction is exactly what this deriver refuses to
    draw."""
    parent = _seed_parent(
        _census([_census_link(1, _WAY, from_node=_BOUNDARY_NODE, to_node=_N_IN)]),
        _corridor_source(),
    )
    extension = _derive(
        parent["id"],
        _attribution(boundary_marker="dangling_approach", trim=None),
    )
    assert extension["reason_code"] == "no_measured_continuation_direction"


def test_a_corridor_that_never_leaves_the_lasso_refuses_by_name(workspace_root: Path):
    """Every node the source declares on this corridor is inside the region the
    user already drew: there is no road beyond it to extend toward, and a
    finger drawn anyway would be pure invention."""
    parent = _seed_parent(
        _census([_census_link(1, _WAY, from_node=_N_IN, to_node=_N_EAST)]),
        _corridor_source(
            nodes=[(_N_OUT, 127.3780), (_N_IN, _LON_IN), (_N_EAST, _LON_EAST)]
        ),
    )
    extension = _derive(
        parent["id"],
        _attribution(
            boundary_node=_N_IN, boundary_marker="dangling_approach", trim=None
        ),
    )
    assert extension["reason_code"] == "corridor_does_not_leave_requested_region"


# ---------------------------------------------------------------------------
# §4 The surfacing: the fixes listing carries the proposal, and the tier does
# not move because of it
# ---------------------------------------------------------------------------


def test_the_fixes_listing_surfaces_the_extension_without_moving_the_tier(
    workspace_root: Path, client: TestClient
):
    parent = _seed_refused_parent(_standard_census(), _corridor_source())
    listed = client.get(f"/api/genesis/jobs/{parent['id']}/fixes", headers=admin_headers())
    assert listed.status_code == 200, listed.text
    entries = [
        entry
        for entry in listed.json()["findings"]
        if entry["code"] == _CODE and entry["subject"] == _SUBJECT
    ]
    assert len(entries) == 1, entries
    disposition = entries[0]["disposition"]

    # The refusal is untouched: no verb, no params, NOT_AUTOMATABLE, and unit
    # #39's boundary reason still names the boundary.
    assert disposition["status"] == agent_fixes.NOT_AUTOMATABLE
    assert disposition["verb"] is None
    assert disposition["params"] is None
    assert disposition["reason_code"] == genesis_fixes.RUNWAY_ENDS_AT_REQUESTED_BOUNDARY
    assert "regenerate" not in disposition

    extension = disposition["boundary_extension"]
    assert extension["provenance"]["parent_job"] == parent["id"]
    assert extension["provenance"]["finding"]["subject"] == _SUBJECT
    assert extension["provenance"]["treatment"] == "new_generation_from_a_wider_extract"
    assert len(extension["extended_polygon_wgs84"]) == len(_SQUARE) + 4
    assert "widened version of the region you requested" in disposition["rationale"]["en"]
    assert "치료" in disposition["rationale"]["ko"]


def test_an_interior_finding_surfaces_no_extension_key_at_all(
    workspace_root: Path, client: TestClient
):
    """Key-ABSENT is the fact: an interior shortage has no region treatment,
    and the card must not be able to render a button for one."""
    parent = _seed_refused_parent(
        _census([_census_link(1, _WAY, from_node=_BOUNDARY_NODE, to_node=_N_IN)]),
        _corridor_source(),
    )
    listed = client.get(f"/api/genesis/jobs/{parent['id']}/fixes", headers=admin_headers())
    assert listed.status_code == 200, listed.text
    entry = next(
        entry
        for entry in listed.json()["findings"]
        if entry["code"] == _CODE and entry["subject"] == _SUBJECT
    )
    assert "boundary_extension" not in entry["disposition"]
    assert entry["disposition"]["status"] == agent_fixes.NOT_AUTOMATABLE


def test_the_deriver_never_mutates_the_parent_job(workspace_root: Path):
    """A proposal is a computation. Nothing about the parent may change."""
    parent = _seed_parent(_standard_census(), _corridor_source())
    before = genesis_jobs.get_job(parent["id"])
    census_path = genesis_jobs.artifact_path(parent["id"], "genesis_json")
    census_before = census_path.read_bytes()

    extension = _derive(parent["id"], _attribution())
    assert "extended_polygon_wgs84" in extension

    assert genesis_jobs.get_job(parent["id"]) == before
    assert census_path.read_bytes() == census_before


# ---------------------------------------------------------------------------
# §5 The geometry helpers, exercised directly where a shape is easier to state
# than to seed
# ---------------------------------------------------------------------------


def test_the_mouth_sits_on_the_crossing_edge(workspace_root: Path):
    """The two vertices where the finger meets the lasso lie ON the edge it
    opens onto — that is what keeps the ring simple and the original area
    contained, so it is asserted as geometry, not assumed."""
    parent = _seed_parent(_standard_census(), _corridor_source())
    extension = _derive(parent["id"], _attribution())
    extended = extension["extended_polygon_wgs84"]

    mouth_first, mouth_last = extended[len(_SQUARE)], extended[-1]
    for lat, lon in (mouth_first, mouth_last):
        assert lon == pytest.approx(_WEST, abs=1e-7)
        assert _SOUTH < lat < _NORTH
    # ...and they straddle the corridor, half a finger's width either side.
    assert min(mouth_first[0], mouth_last[0]) < _CORRIDOR_LAT < max(mouth_first[0], mouth_last[0])
    span = _metres((mouth_first[0], mouth_first[1]), (mouth_last[0], mouth_last[1]))
    assert span == pytest.approx(2 * extension["corridor_patch"]["half_width_m"], abs=0.5)


def test_the_simplifier_keeps_the_ends_and_drops_only_straight_middles():
    """Every centreline vertex becomes TWO ring vertices against a 64-corner
    cap, so a straight run must not spend them — and the ends must survive."""
    straight = [(0.0, 0.0), (50.0, 0.05), (100.0, 0.0), (150.0, 0.0)]
    kept = genesis_jobs._simplify_chain(straight, 1.0)
    assert kept[0] == straight[0] and kept[-1] == straight[-1]
    assert len(kept) == 2

    bent = [(0.0, 0.0), (50.0, 40.0), (100.0, 0.0)]
    assert len(genesis_jobs._simplify_chain(bent, 1.0)) == 3


def test_a_hairpin_corridor_is_refused_rather_than_mitered_into_nonsense():
    """A wall mitered around a hairpin overshoots far enough that the emitted
    shape stops resembling the road it claims to cover."""
    hairpin = [(0.0, 0.0), (100.0, 0.0), (5.0, 3.0)]
    assert genesis_jobs._corridor_walls(hairpin, 20.0) is None

    gentle = [(0.0, 0.0), (100.0, 0.0), (200.0, 20.0)]
    walls = genesis_jobs._corridor_walls(gentle, 20.0)
    assert walls is not None
    left, right, worst = walls
    assert len(left) == len(right) == 3
    assert worst >= 1.0
    # The two walls stay half_width away from their own centreline vertex.
    assert math.hypot(left[0][0] - 0.0, left[0][1] - 0.0) == pytest.approx(20.0)
    assert math.hypot(right[-1][0] - 200.0, right[-1][1] - 20.0) == pytest.approx(20.0)

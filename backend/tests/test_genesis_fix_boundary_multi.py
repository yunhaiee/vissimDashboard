"""Unit #48 — the boundary treatment learns the OTHER finding, and learns to
propose ONE lasso for all of them.

Two gaps closed, both found by the user driving the real product on
2026-08-19:

* **``v3_orphan_link`` had no boundary story at all.** Unit #36 could only ask
  the source about the orphan's DEPARTURE node, and where the lasso severed the
  link that node is one THIS GENERATOR synthesized on the ring — so the answer
  was always ``node_not_in_source``: true, and silent about the cause. The trim
  ledger knew: on the real job the orphan's way overhung the lasso by 3.9 m at
  its start and 5.4 m at its end, and the nodes cut off with those tails were
  the junctions at BOTH of its ends.
  :func:`genesis_jobs.derive_v3_orphan_boundary_attribution` measures that,
  cross-checks every ledger claim against the source's own coordinates, and
  REFUSES BY NAME for an orphan the source itself leaves unconnected — which is
  the case that must never be offered a region.

* **Two boundary findings meant two proposals that overwrote each other.**
  Each was derived from the same original polygon, so taking one discarded the
  other; and because their fingers open onto different ring edges, repeating
  the extension never healed the orphan (the user's own observation).
  :func:`genesis_jobs.derive_boundary_extension_for_findings` splices every
  finger into ONE ring.

The three properties that carry the design, each with its own test below:

* **the fingers are #40's, unchanged.** The per-corridor geometry moved into
  :func:`genesis_jobs._boundary_patch_from_corridor` and the splice into
  :func:`genesis_jobs._splice_patches_into_ring`; #40's own payload is
  byte-identical through them (its whole test module is the proof, and §1 here
  re-asserts the invariants directly on a multi-patch ring);
* **a collision is NAMED, never resolved quietly.** Two mouths wanting the
  same stretch of one ring edge refuse the COMPOSITE with both corridors named
  — and leave each finding's own proposal exactly where it was;
* **the merge is named too, and only where the shape was never in dispute:**
  two findings resolving to the identical corridor end are one finger, with
  both findings recorded on it.

Fixture shape follows ``tests/test_genesis_fix_v4_extension.py`` (unit #40):
a private tmp workspace per test, hand-written census records that VALIDATE as
a real ``DraftNetwork``, a hand-written source ``.osm`` parsed by the REAL
``services.genesis.osm`` parser, the REAL ``services.osm_fetch`` region kernels
judging the emitted shape, no COM anywhere.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from conftest import admin_headers
from services import genesis_jobs, workspaces
from services.agent import genesis_fixes
from services.genesis.osm import _inside_polygon

from tests.test_genesis_gate import network, straight_pair
from tests.test_genesis_jobs import _seed_job

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Same shape as unit #39/#40's fixture of the same name — duplicated
    rather than imported across test modules (the repo's existing convention
    only cross-imports plain helper FUNCTIONS)."""
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
# The synthetic site
#
#   * a square lasso, drawn NW -> NE -> SE -> SW (so edge 0 is the NORTH edge
#     and edge 3 is the WEST one);
#   * ONE runway corridor entering across the WEST edge — unit #40's own,
#     numbers and all, so this module's composite can be checked against a
#     shape #40's tests already pin;
#   * ONE orphan way arching over the NORTH edge: two ends sticking out, each
#     cut by the trim, each with a junction way waiting outside.
# ---------------------------------------------------------------------------

_V4_CODE = "v4_lane_drop_without_runway"
_V4_SUBJECT = "1 (link 1)"
_ORPHAN_CODE = "v3_orphan_link"
_ORPHAN_SUBJECT = "10 (tertiary_link [FWD] w9017s0)"

_SOUTH, _NORTH = 36.3480, 36.3560
_WEST, _EAST = 127.3760, 127.3860

_SQUARE = [
    {"lat": _NORTH, "lon": _WEST},
    {"lat": _NORTH, "lon": _EAST},
    {"lat": _SOUTH, "lon": _EAST},
    {"lat": _SOUTH, "lon": _WEST},
]
_NORTH_EDGE_INDEX = 0
_WEST_EDGE_INDEX = 3

# ---- the runway corridor (unit #40's), crossing the WEST edge --------------
_V4_WAY = 9007
_V4_BOUNDARY_NODE = 999001
_V4_DEFICIT = 150.0
_V4_MARGIN = 30.0
_CORRIDOR_LAT = 36.3520
_N_FAR, _N_OUT, _N_IN, _N_EAST = 5001, 5002, 5003, 5004
_LON_FAR, _LON_OUT, _LON_IN, _LON_EAST = 127.3720, 127.3750, 127.3800, 127.3840

# ---- the orphan way, arching over the NORTH edge ---------------------------
_ORPHAN_WAY = 9017
_ORPHAN_LINK_ID = 10
#: The two synthesized terminals the trim put ON the ring, one per end.
_ORPHAN_NODE_START, _ORPHAN_NODE_END = 999011, 999012
#: Source nodes, in way order: outside, inside, inside, outside.
_O_OUT_W, _O_IN_W, _O_IN_E, _O_OUT_E = 6001, 6002, 6003, 6004
_O_LON_W, _O_LON_E = 127.3790, 127.3820
#: 0.0003 deg of latitude past the ring — about 33.4 m, which is what makes the
#: two ends below straddle the deriver's measured/extrapolated branch.
_O_LAT_OUT = _NORTH + 0.0003
_O_LAT_IN = _NORTH - 0.0005
#: The junction ways waiting OUTSIDE the lasso — the roads the orphan met
#: before the trim cut their shared node away.
_JUNCTION_WAY_W, _JUNCTION_WAY_E = 9021, 9022
_J_W_FAR, _J_E_FAR = 6011, 6012

#: The ledger's own overhang for each end. 30.0 needs 36.0 m of finger against
#: ~33.4 m of declared road (so the tail is EXTRAPOLATED); 20.0 needs 24.0 m
#: (so the finger is cut short of the source's own last node and nothing is
#: extrapolated). One derivation, both branches.
_CUT_START_M = 30.0
_CUT_END_M = 20.0


def _census_link(
    link_id: int,
    way_id: int,
    *,
    from_node: int,
    to_node: int,
    lanes: int = 2,
    forward: bool = True,
    highway: str = "secondary",
) -> dict[str, Any]:
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
        "highway": highway,
        "from_node_osm_id": from_node,
        "to_node_osm_id": to_node,
        "side_offset_m": 0.0,
    }


def _way_trim(
    *,
    way_id: int,
    end: str,
    synthesized_node_id: int,
    metres_cut: float,
    nodes_cut: int = 1,
    highway: str = "secondary",
    reason: str = "tail_outside_requested_bbox_cut",
) -> dict[str, Any]:
    return {
        "way_id": way_id,
        "end": end,
        "synthesized_node_id": synthesized_node_id,
        "metres_cut": metres_cut,
        "nodes_cut": nodes_cut,
        "highway": highway,
        "reason": reason,
        "detail": f"{nodes_cut} node(s) of this way's {end} lay outside the requested polygon",
    }


def _v4_trim() -> dict[str, Any]:
    return _way_trim(
        way_id=_V4_WAY,
        end="start",
        synthesized_node_id=_V4_BOUNDARY_NODE,
        metres_cut=358.0,
        nodes_cut=2,
    )


def _orphan_trims(
    *, start_m: float = _CUT_START_M, end_m: float = _CUT_END_M
) -> list[dict[str, Any]]:
    return [
        _way_trim(
            way_id=_ORPHAN_WAY,
            end="start",
            synthesized_node_id=_ORPHAN_NODE_START,
            metres_cut=start_m,
            highway="tertiary_link",
        ),
        _way_trim(
            way_id=_ORPHAN_WAY,
            end="end",
            synthesized_node_id=_ORPHAN_NODE_END,
            metres_cut=end_m,
            highway="tertiary_link",
        ),
    ]


def _census(
    link_entries: list[dict[str, Any]],
    *,
    way_trims: list[dict[str, Any]] | None = None,
    polygon: list[dict[str, float]] | None = _SQUARE,
) -> dict[str, Any]:
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
        '<osm version="0.6" generator="unit48-test">\n' + "".join(elements) + "</osm>\n"
    )


def _source(
    *,
    orphan_lon_w: float = _O_LON_W,
    orphan_lon_e: float = _O_LON_E,
    orphan_out_lat: float = _O_LAT_OUT,
    with_orphan: bool = True,
    with_corridor: bool = True,
) -> str:
    """The whole site's source: the runway corridor west of the lasso, and the
    orphan arching over the north edge with a junction way waiting at each of
    its cut ends."""
    parts: list[str] = []
    if with_corridor:
        corridor = [
            (_N_FAR, _LON_FAR),
            (_N_OUT, _LON_OUT),
            (_N_IN, _LON_IN),
            (_N_EAST, _LON_EAST),
        ]
        parts += [_osm_node(nid, _CORRIDOR_LAT, lon) for nid, lon in corridor]
        parts.append(
            _osm_way(_V4_WAY, [nid for nid, _lon in corridor], name="Grand Ave", oneway="yes")
        )
    if with_orphan:
        parts += [
            _osm_node(_O_OUT_W, orphan_out_lat, orphan_lon_w),
            _osm_node(_O_IN_W, _O_LAT_IN, orphan_lon_w),
            _osm_node(_O_IN_E, _O_LAT_IN, orphan_lon_e),
            _osm_node(_O_OUT_E, orphan_out_lat, orphan_lon_e),
            # The two roads the orphan met, entirely outside the lasso.
            _osm_node(_J_W_FAR, orphan_out_lat + 0.0004, orphan_lon_w - 0.0004),
            _osm_node(_J_E_FAR, orphan_out_lat + 0.0004, orphan_lon_e + 0.0004),
            _osm_way(
                _ORPHAN_WAY,
                [_O_OUT_W, _O_IN_W, _O_IN_E, _O_OUT_E],
                highway="tertiary_link",
                oneway="yes",
            ),
            _osm_way(_JUNCTION_WAY_W, [_J_W_FAR, _O_OUT_W], name="West Link"),
            _osm_way(_JUNCTION_WAY_E, [_O_OUT_E, _J_E_FAR], name="East Link"),
        ]
    return _osm(*parts)


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
    census: dict[str, Any], source_xml: str | None, *, name: str = "unit48_parent"
) -> dict[str, Any]:
    record = _seed_job(
        network(links=straight_pair(a_lanes=2, a_len=50.0)), census=census, name=name
    )
    if source_xml is not None:
        _attach_source_osm(record, source_xml)
    return record


def _v4_finding() -> dict[str, Any]:
    return {
        "code": _V4_CODE,
        "subject": _V4_SUBJECT,
        "context": {"lanes": 2, "dropped_lanes": [2], "required_m": 200.0, "available_m": 50.0},
    }


def _orphan_finding(subject: str = _ORPHAN_SUBJECT) -> dict[str, Any]:
    return {"code": _ORPHAN_CODE, "subject": subject, "context": {}}


def _standard_links() -> list[dict[str, Any]]:
    """One runway-corridor link entering the WEST boundary node, and the orphan
    link whose two terminals are the trim's own synthesized nodes."""
    return [
        _census_link(1, _V4_WAY, from_node=_V4_BOUNDARY_NODE, to_node=_N_IN),
        _census_link(
            _ORPHAN_LINK_ID,
            _ORPHAN_WAY,
            from_node=_ORPHAN_NODE_START,
            to_node=_ORPHAN_NODE_END,
            lanes=1,
            highway="tertiary_link",
        ),
        # The junction ways ARE carried by this census (they have links of their
        # own further inside), so `junction_way_ids_in_census` is non-empty and
        # the attribution can say the road it met is one this model already has.
        _census_link(20, _JUNCTION_WAY_W, from_node=_J_W_FAR, to_node=_O_OUT_W),
        _census_link(21, _JUNCTION_WAY_E, from_node=_O_OUT_E, to_node=_J_E_FAR),
    ]


def _standard_census(**kwargs: Any) -> dict[str, Any]:
    return _census(
        _standard_links(),
        way_trims=[_v4_trim(), *_orphan_trims()],
        **kwargs,
    )


def _targets(job_id: str, finding: dict[str, Any]) -> dict[str, Any]:
    return genesis_jobs.derive_fix_targets(
        finding["code"],
        finding,
        draft=genesis_jobs.job_draft_or_none(job_id),
        params=None,
        job_id=job_id,
    )


def _combined(
    job_id: str, findings: list[dict[str, Any]]
) -> dict[str, Any]:
    return genesis_jobs.derive_boundary_extension_for_findings(
        [(finding, _targets(job_id, finding)) for finding in findings],
        draft=genesis_jobs.job_draft_or_none(job_id),
        job_id=job_id,
    )


def _metres(a: tuple[float, float], b: tuple[float, float]) -> float:
    return genesis_jobs._latlon_metres(a, b)


def _ring(points: list[dict[str, float]]) -> tuple[tuple[float, float], ...]:
    return tuple((float(p["lat"]), float(p["lon"])) for p in points)


# ---------------------------------------------------------------------------
# 1. The orphan's boundary attribution: measured, cross-checked, and REFUSED
#    for an orphan the source itself leaves alone.
# ---------------------------------------------------------------------------


def test_orphan_attribution_names_both_severed_ends(workspace_root: Path) -> None:
    """The ledger says the way was cut at both ends; the deriver re-derives
    that against the source and reports each end with the junction it lost."""
    record = _seed_parent(_standard_census(), _source())
    derived = genesis_jobs.derive_v3_orphan_boundary_attribution(
        _ORPHAN_CODE,
        _orphan_finding(),
        draft=genesis_jobs.job_draft_or_none(record["id"]),
        job_id=record["id"],
    )
    attribution = derived["orphan_boundary_attribution"]
    assert attribution is not None, derived["provenance"]
    assert attribution["status"] == "boundary_attributed"
    assert attribution["way_id"] == _ORPHAN_WAY
    assert attribution["link_id"] == str(_ORPHAN_LINK_ID)

    ends = {entry["end"]: entry for entry in attribution["severed_ends"]}
    assert set(ends) == {"start", "end"}
    assert ends["start"]["metres_cut"] == _CUT_START_M
    assert ends["end"]["metres_cut"] == _CUT_END_M
    assert ends["start"]["boundary_node"] == _ORPHAN_NODE_START
    assert ends["end"]["boundary_node"] == _ORPHAN_NODE_END
    assert ends["start"]["outermost_cut_node"] == _O_OUT_W
    assert ends["end"]["outermost_cut_node"] == _O_OUT_E
    # WHY the link is alone, measured: the road it met is on the other side of
    # the lasso, and this census already carries that road elsewhere.
    assert ends["start"]["junction_way_ids"] == [_JUNCTION_WAY_W]
    assert ends["end"]["junction_way_ids"] == [_JUNCTION_WAY_E]
    assert ends["start"]["junction_way_ids_in_census"] == [_JUNCTION_WAY_W]
    assert attribution["metres_cut_total"] == round(_CUT_START_M + _CUT_END_M, 2)
    assert derived["provenance"]["source_field"] == "metadata.boundary.way_trims + way/nd"
    assert derived["provenance"]["severed_ends"] == ["end", "start"]


def test_orphan_with_no_trim_is_refused_by_name_and_gets_no_region(
    workspace_root: Path,
) -> None:
    """THE DOCTRINE CASE. An orphan whose way the lasso never cut is the
    SOURCE's own isolation, and it must be offered no region at all — dressing
    it as the boundary's would propose an extract that cannot contain the
    connection it implies."""
    census = _census(_standard_links(), way_trims=[_v4_trim()])  # no orphan trims
    record = _seed_parent(census, _source())
    derived = genesis_jobs.derive_v3_orphan_boundary_attribution(
        _ORPHAN_CODE,
        _orphan_finding(),
        draft=genesis_jobs.job_draft_or_none(record["id"]),
        job_id=record["id"],
    )
    assert derived["orphan_boundary_attribution"] is None
    assert derived["provenance"]["reason_code"] == "no_boundary_trim"
    assert "not the boundary's" in derived["provenance"]["detail"]

    targets = _targets(record["id"], _orphan_finding())
    assert "orphan_boundary_attribution" not in targets
    assert "boundary_extension_patches" not in targets
    assert "boundary_extension_patches_refusal" not in targets
    # ...and the composite has nothing to cover either.
    combined = _combined(record["id"], [_orphan_finding()])
    assert combined["reason_code"] == "no_boundary_findings"


def test_orphan_trim_that_the_source_contradicts_is_reported_not_adjudicated(
    workspace_root: Path,
) -> None:
    """A ledger nobody re-derives is a ledger that can drift. Here the ledger
    claims the START was cut, but the source puts that node INSIDE the ring."""
    census = _census(
        _standard_links(),
        way_trims=[
            _way_trim(
                way_id=_ORPHAN_WAY,
                end="start",
                synthesized_node_id=_ORPHAN_NODE_START,
                metres_cut=_CUT_START_M,
                highway="tertiary_link",
            )
        ],
    )
    # Every orphan node inside the lasso: the trim record cannot be true.
    record = _seed_parent(census, _source(orphan_out_lat=_NORTH - 0.0007))
    derived = genesis_jobs.derive_v3_orphan_boundary_attribution(
        _ORPHAN_CODE,
        _orphan_finding(),
        draft=genesis_jobs.job_draft_or_none(record["id"]),
        job_id=record["id"],
    )
    assert derived["orphan_boundary_attribution"] is None
    assert derived["provenance"]["reason_code"] == "trim_disagrees_with_source"
    assert "not adjudicated" in derived["provenance"]["detail"]


def test_orphan_attribution_ignores_a_trim_on_another_link_of_the_same_way(
    workspace_root: Path,
) -> None:
    """A way decomposes into several links; a trim at the far end of a
    DIFFERENT link is not this link's boundary story. The synthesized node has
    to be one of THIS link's own two terminals."""
    links = _standard_links()
    for link in links:
        if link["id"] == _ORPHAN_LINK_ID:
            # Same way, but neither terminal is a synthesized boundary node.
            link["from_node_osm_id"] = _O_IN_W
            link["to_node_osm_id"] = _O_IN_E
    record = _seed_parent(_census(links, way_trims=[_v4_trim(), *_orphan_trims()]), _source())
    derived = genesis_jobs.derive_v3_orphan_boundary_attribution(
        _ORPHAN_CODE,
        _orphan_finding(),
        draft=genesis_jobs.job_draft_or_none(record["id"]),
        job_id=record["id"],
    )
    assert derived["orphan_boundary_attribution"] is None
    assert derived["provenance"]["reason_code"] == "no_boundary_trim"


# ---------------------------------------------------------------------------
# 2. The orphan's own proposal: ONE finger per severed end.
# ---------------------------------------------------------------------------


def test_orphan_proposal_draws_one_finger_per_severed_end(workspace_root: Path) -> None:
    record = _seed_parent(_standard_census(), _source())
    targets = _targets(record["id"], _orphan_finding())
    proposal = targets["boundary_extension_patches"]
    assert "reason_code" not in proposal

    patches = proposal["corridor_patches"]
    assert len(patches) == 2, patches
    assert {patch["boundary_node"] for patch in patches} == {
        _ORPHAN_NODE_START,
        _ORPHAN_NODE_END,
    }
    # BOTH open onto the NORTH edge — the one the way arches over — and neither
    # onto the WEST edge the runway corridor crosses. That difference is the
    # whole reason extending for the runway never healed the orphan.
    assert {patch["crossing_edge_index"] for patch in patches} == {_NORTH_EDGE_INDEX}

    by_node = {patch["boundary_node"]: patch for patch in patches}
    start = by_node[_ORPHAN_NODE_START]
    end = by_node[_ORPHAN_NODE_END]
    # The finger is sized by the metres the BOUNDARY CUT, plus the same round
    # 20% margin #40 states — and it says so, rather than borrowing the runway
    # deficit's word for a measurement that is not one.
    assert start["basis_kind"] == "boundary_cut_metres"
    assert start["basis_m"] == _CUT_START_M
    assert start["margin_m"] == round(_CUT_START_M * 0.20, 2)
    assert start["length_m"] == round(_CUT_START_M * 1.20, 2)
    assert end["length_m"] == round(_CUT_END_M * 1.20, 2)

    # THE SPLIT, both branches in one derivation: the 36.0 m finger reaches past
    # the ~33.4 m of road the source declares, so its tail is EXTRAPOLATED; the
    # 24.0 m one is cut short of the source's own last node, so nothing is.
    assert start["extrapolated_m"] > 0.0
    assert round(start["measured_source_m"] + start["extrapolated_m"], 2) == start["length_m"]
    assert end["extrapolated_m"] == 0.0
    assert end["measured_source_m"] == end["length_m"]

    cover = proposal["covers"]
    assert len(cover) == 1
    assert cover[0]["code"] == _ORPHAN_CODE
    assert cover[0]["patch_count"] == 2
    assert cover[0]["basis_kind"] == "boundary_cut_metres"
    assert proposal["provenance"]["patch_count"] == 2
    assert proposal["provenance"]["treatment"] == "new_generation_from_a_wider_extract"


def test_orphan_proposal_is_a_splice_and_not_a_hull(workspace_root: Path) -> None:
    """#40's three invariants, re-asserted on a TWO-finger ring: every corner
    the user drew survives verbatim and in order, every added vertex is outside
    or on the original shape, and the ring is simple."""
    record = _seed_parent(_standard_census(), _source())
    proposal = _targets(record["id"], _orphan_finding())["boundary_extension_patches"]
    original = _ring(_SQUARE)
    extended = [(float(lat), float(lon)) for lat, lon in proposal["extended_polygon_wgs84"]]

    # (a) BIT-IDENTICAL, IN ORDER. Not re-rounded and not re-derived: this is
    #     the user's shape, and the only thing the deriver may add is fingers.
    assert [point for point in extended if point in set(original)] == list(original)

    # (b) ANTI-HULL. Every vertex that is NOT one of the user's own lies outside
    #     the original ring, or on the edge the mouth was opened in (the mouths
    #     are rounded to 7 dp, so "on" is judged in metres, not in float bits).
    added = [point for point in extended if point not in set(original)]
    assert len(added) == 8, added  # four per finger
    for lat, lon in added:
        if not _inside_polygon(lat, lon, original):
            continue
        # An inside vertex is only acceptable if it is a MOUTH sitting on the
        # crossing edge within the coordinate rounding.
        nearest = min(
            _perpendicular_m((lat, lon), original[i], original[(i + 1) % len(original)])
            for i in range(len(original))
        )
        assert nearest <= 0.05, (lat, lon, nearest)

    # (c) SIMPLE. Judged by the region kernel the fetch itself is held to.
    from services.osm_fetch import RegionPolygon, measure_region

    measured = measure_region(RegionPolygon.from_sequence(extended))
    assert measured.within_region_cap
    assert proposal["added_area_km2"] > 0.0


def _perpendicular_m(
    point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    """Distance from ``point`` to segment ``a-b``, in metres. Local to this
    module: the product's own kernels are what is under test."""
    lat0 = math.radians((a[0] + b[0]) / 2.0)
    scale = 111320.0
    to_xy = lambda p: (p[1] * scale * math.cos(lat0), p[0] * scale)  # noqa: E731
    (ax, ay), (bx, by), (px, py) = to_xy(a), to_xy(b), to_xy(point)
    dx, dy = bx - ax, by - ay
    squared = dx * dx + dy * dy
    if squared <= 0.0:  # pragma: no cover - a degenerate ring edge
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / squared))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


# ---------------------------------------------------------------------------
# 3. The composite: every boundary finding on ONE ring.
# ---------------------------------------------------------------------------


def test_composite_covers_the_runway_and_the_orphan_on_one_ring(
    workspace_root: Path,
) -> None:
    """THE UNIT'S REASON FOR EXISTING. Two findings, three fingers, two
    different ring edges, ONE polygon — and the per-finding proposals both
    still say exactly what they said."""
    record = _seed_parent(_standard_census(), _source())
    findings = [_v4_finding(), _orphan_finding()]
    combined = _combined(record["id"], findings)
    assert "reason_code" not in combined, combined

    assert combined["provenance"]["patch_count"] == 3
    assert combined["provenance"]["finding_count"] == 2
    assert combined["provenance"]["merged_patches"] == 0
    edges = sorted(patch["crossing_edge_index"] for patch in combined["corridor_patches"])
    assert edges == [_NORTH_EDGE_INDEX, _NORTH_EDGE_INDEX, _WEST_EDGE_INDEX]

    covers = {entry["code"]: entry for entry in combined["covers"]}
    assert set(covers) == {_V4_CODE, _ORPHAN_CODE}
    assert covers[_ORPHAN_CODE]["patch_count"] == 2
    assert covers[_V4_CODE]["patch_count"] == 1
    assert covers[_V4_CODE]["basis_kind"] == "lane_change_runway_deficit"
    assert covers[_V4_CODE]["basis_m"] == _V4_DEFICIT
    assert covers[_V4_CODE]["length_m"] == round(_V4_DEFICIT + _V4_MARGIN, 2)
    # Every finger names its own measurement rather than one word covering both.
    assert {patch["basis_kind"] for patch in combined["corridor_patches"]} == {
        "lane_change_runway_deficit",
        "boundary_cut_metres",
    }

    # The user's corners survive a THREE-finger splice exactly as they survive
    # a one-finger one.
    original = _ring(_SQUARE)
    extended = [(float(lat), float(lon)) for lat, lon in combined["extended_polygon_wgs84"]]
    assert [point for point in extended if point in set(original)] == list(original)
    assert len(extended) == len(original) + 12

    # AND IT IS STRICTLY MORE than either proposal alone: the composite's added
    # area is the sum of what each finding's own proposal adds. That is the
    # arithmetic proof that taking one no longer discards the other.
    v4_only = _combined(record["id"], [_v4_finding()])
    orphan_only = _combined(record["id"], [_orphan_finding()])
    assert combined["added_area_km2"] == pytest.approx(
        v4_only["added_area_km2"] + orphan_only["added_area_km2"], abs=1e-6
    )


def test_composite_refuses_by_name_when_two_mouths_collide_on_one_edge(
    workspace_root: Path,
) -> None:
    """Two fingers wanting the same stretch of one ring edge: splicing both
    would grow one through the other's mouth, and choosing between them (or
    hulling them into a fatter finger neither corridor measured) is not this
    module's call. Refused, named — and every per-finding proposal survives."""
    # The orphan's two ends moved to within ~5 m of each other, while each
    # finger's mouth stays ~15 m wide.
    record = _seed_parent(
        _standard_census(), _source(orphan_lon_w=_O_LON_W, orphan_lon_e=_O_LON_W + 0.00006)
    )
    combined = _combined(record["id"], [_orphan_finding()])
    assert combined["reason_code"] == "patch_mouths_collide_on_edge", combined
    assert "overlap" in combined["detail"]
    assert "one at a time" in combined["detail"]
    assert str(_ORPHAN_WAY) in combined["detail"]

    # THE PROMISE THE REFUSAL MAKES: the runway corridor's own proposal, which
    # has nothing to do with this collision, is untouched.
    v4_only = _combined(record["id"], [_v4_finding()])
    assert "reason_code" not in v4_only
    assert v4_only["provenance"]["patch_count"] == 1


def test_two_findings_on_the_identical_corridor_end_merge_into_one_finger(
    workspace_root: Path,
) -> None:
    """THE NAMED MERGE, and the only case where two specs become one shape:
    two links carrying the SAME way from the SAME synthesized boundary node are
    literally the same road being asked for twice."""
    links = _standard_links()
    twin = _census_link(
        11,
        _ORPHAN_WAY,
        from_node=_ORPHAN_NODE_START,
        to_node=_O_IN_E,
        lanes=1,
        highway="tertiary_link",
    )
    census = _census(
        [*links, twin],
        way_trims=[
            _way_trim(
                way_id=_ORPHAN_WAY,
                end="start",
                synthesized_node_id=_ORPHAN_NODE_START,
                metres_cut=_CUT_START_M,
                highway="tertiary_link",
            )
        ],
    )
    record = _seed_parent(census, _source())
    twin_finding = _orphan_finding("11 (tertiary_link [FWD] w9017s1)")
    combined = _combined(record["id"], [_orphan_finding(), twin_finding])
    assert "reason_code" not in combined, combined

    assert combined["provenance"]["patch_count"] == 1
    assert combined["provenance"]["merged_patches"] == 1
    patch = combined["corridor_patches"][0]
    assert len(patch["covers"]) == 2
    assert {entry["subject"] for entry in patch["covers"]} == {
        _ORPHAN_SUBJECT,
        twin_finding["subject"],
    }
    # Both findings are still named in the per-finding roll-up, each pointing at
    # the one finger that answers for it.
    assert {entry["subject"] for entry in combined["covers"]} == {
        _ORPHAN_SUBJECT,
        twin_finding["subject"],
    }
    assert all(entry["patch_count"] == 1 for entry in combined["covers"])


def test_composite_refuses_when_a_covered_corridor_cannot_be_drawn(
    workspace_root: Path,
) -> None:
    """A composite that quietly covered fewer findings than its own label
    claims would be a lie in a button. It refuses instead, naming the corridor
    and the per-corridor reason.

    The corridor here is ATTRIBUTED (so its own card really does offer a
    proposal) but its finger cannot be drawn: a 3000 m cut asks for a mouth
    wider than the ring edge it would open onto."""
    census = _census(
        _standard_links(),
        way_trims=[_v4_trim(), *_orphan_trims(start_m=3000.0)],
    )
    record = _seed_parent(census, _source())
    combined = _combined(record["id"], [_v4_finding(), _orphan_finding()])
    assert combined["reason_code"] == "corridor_patch_undrawable", combined
    assert "patch_mouth_exceeds_crossing_edge" in combined["detail"]
    assert "own proposal is unaffected" in combined["detail"]
    assert str(_ORPHAN_WAY) in combined["detail"]

    # The V4 half really is unaffected.
    v4_only = _combined(record["id"], [_v4_finding()])
    assert "reason_code" not in v4_only


def test_a_finding_with_no_attribution_is_not_covered_and_not_refused_over(
    workspace_root: Path,
) -> None:
    """The counterpart to the test above, and the distinction that keeps the
    composite's label honest: a finding that earned NO finger is not "covered
    less" -- it is not covered at all, its own card offers nothing either, and
    ``covers`` says so by not naming it. Only a corridor whose card DOES offer
    a proposal can make the composite refuse."""
    # The orphan way is gone from the source, so the ledger's claim cannot be
    # cross-checked and no attribution is derived for it.
    record = _seed_parent(_standard_census(), _source(with_orphan=False))
    assert "boundary_extension_patches" not in _targets(record["id"], _orphan_finding())

    combined = _combined(record["id"], [_v4_finding(), _orphan_finding()])
    assert "reason_code" not in combined, combined
    assert [entry["code"] for entry in combined["covers"]] == [_V4_CODE]
    assert combined["provenance"]["finding_count"] == 1


# ---------------------------------------------------------------------------
# 4. The refusal PROSE and the route: the tier never moves, and the screen can
#    say what it is offering.
# ---------------------------------------------------------------------------


def test_orphan_refusal_states_the_boundary_and_names_its_treatment(
    workspace_root: Path,
) -> None:
    record = _seed_parent(_standard_census(), _source())
    finding = _orphan_finding()
    targets = _targets(record["id"], finding)
    proposal = genesis_fixes.propose_genesis_fix(_ORPHAN_CODE, finding=finding, targets=targets)

    # THE TIER NEVER MOVES. Connectivity is still not invented; what changed is
    # that the refusal can say whose fault the isolation is.
    assert proposal.reason_code == genesis_fixes.CONNECTIVITY_IS_NOT_INVENTED
    ko = proposal.rationale_ko
    assert "라쏘가 그 접속점을 잘라냈기 때문" in ko
    assert str(_ORPHAN_WAY) in ko
    assert "치료:" in ko
    assert "추출 버튼" in ko
    # MEASURED, and the reason `_km2` exists: an orphan's fingers are metres
    # long, so the area they add rounds to 0.00006 km² -- which Python renders
    # as `6e-05`. An exponent in a sentence is a number most readers will
    # misread by four orders of magnitude.
    assert "e-0" not in ko, ko
    for text in (proposal.rationale_en, proposal.rationale_ja):
        assert "e-0" not in text, text
    for text in (proposal.rationale_en, proposal.rationale_ja):
        assert str(_ORPHAN_WAY) in text
    # The evidence carries the measured payload, not only the prose.
    assert proposal.evidence["orphan_boundary_attribution"]["way_id"] == _ORPHAN_WAY
    assert len(proposal.evidence["boundary_extension_patches"]["corridor_patches"]) == 2


# ---------------------------------------------------------------------------
# REFUTER REGRESSIONS (unit #48). The affirmative sentence's first draft said,
# on every attributed orphan: "it is alone because the lasso cut its JUNCTION(S)
# away, NOT BECAUSE THE ROAD ENDS." Both halves can be false while the
# attribution itself is sound, and the four tests below are the four ways.
# ---------------------------------------------------------------------------


def _one_ended_trims() -> list[dict[str, Any]]:
    """The orphan cut at its START only -- its `end` terminal is untouched."""
    return [
        _way_trim(
            way_id=_ORPHAN_WAY,
            end="start",
            synthesized_node_id=_ORPHAN_NODE_START,
            metres_cut=_CUT_START_M,
            highway="tertiary_link",
        )
    ]


def _source_without_junctions() -> str:
    """The same orphan arching over the north edge, but the two nodes the trim
    cuts off meet NOTHING -- no junction way at either end."""
    return _osm(
        _osm_node(_O_OUT_W, _O_LAT_OUT, _O_LON_W),
        _osm_node(_O_IN_W, _O_LAT_IN, _O_LON_W),
        _osm_node(_O_IN_E, _O_LAT_IN, _O_LON_E),
        _osm_node(_O_OUT_E, _O_LAT_OUT, _O_LON_E),
        _osm_way(
            _ORPHAN_WAY,
            [_O_OUT_W, _O_IN_W, _O_IN_E, _O_OUT_E],
            highway="tertiary_link",
            oneway="yes",
        ),
    )


def test_a_one_ended_orphan_is_not_told_the_road_does_not_end(
    workspace_root: Path,
) -> None:
    """ONE severed end is reported as one. A link has two, and the un-cut one is
    a gap the boundary had nothing to do with -- unit #36's own testimony clause,
    which this clause is appended to, may have just said so in the same
    paragraph."""
    record = _seed_parent(
        _census(_standard_links(), way_trims=_one_ended_trims()), _source()
    )
    finding = _orphan_finding()
    targets = _targets(record["id"], finding)

    attribution = targets["orphan_boundary_attribution"]
    assert [entry["end"] for entry in attribution["severed_ends"]] == ["start"]
    # The payload states the whole link, not only the severed part of it, so no
    # reader has to assume `severed_ends` covers both ends.
    assert attribution["severed_end_count"] == 1
    assert attribution["link_end_count"] == 2
    assert "not because the road ends" not in attribution["reason"]
    assert "only ONE of this link's two ends" in attribution["reason"]

    proposal = genesis_fixes.propose_genesis_fix(
        _ORPHAN_CODE, finding=finding, targets=targets
    )
    # A region is still offered -- one finger for the one end that WAS cut.
    assert "치료:" in proposal.rationale_ko
    assert "boundary_extension_patches" in proposal.evidence
    assert len(proposal.evidence["boundary_extension_patches"]["corridor_patches"]) == 1
    # ...but the categorical claim is gone from all three languages, replaced by
    # the measured one.
    assert "도로가 끝나서가 아니라" not in proposal.rationale_ko
    assert "not because the road ends" not in proposal.rationale_en
    assert "道路が終わっているからではなく" not in proposal.rationale_ja
    assert "두 끝 중 start 쪽 한 곳뿐" in proposal.rationale_ko
    assert "Only ONE of this link's two ends was cut" in proposal.rationale_en
    assert "二つの端のうち start 側の一箇所だけ" in proposal.rationale_ja
    for text in (proposal.rationale_ko, proposal.rationale_en, proposal.rationale_ja):
        assert "경계 탓으로" in text or "not attributed to the boundary" in text or (
            "境界のせいだとはしません" in text
        )


def test_a_cut_that_met_no_junction_does_not_claim_a_junction(
    workspace_root: Path,
) -> None:
    """`junction_way_ids` is routinely EMPTY -- the outermost node of a cut tail
    is usually a plain geometry node. What the lasso took then is the road's
    continuation, and naming a junction the source never declared would invent
    the very evidence this module exists to measure."""
    record = _seed_parent(
        _census(
            [
                _census_link(
                    _ORPHAN_LINK_ID,
                    _ORPHAN_WAY,
                    from_node=_ORPHAN_NODE_START,
                    to_node=_ORPHAN_NODE_END,
                    lanes=1,
                    highway="tertiary_link",
                )
            ],
            way_trims=_orphan_trims(),
        ),
        _source_without_junctions(),
    )
    finding = _orphan_finding()
    targets = _targets(record["id"], finding)

    attribution = targets["orphan_boundary_attribution"]
    assert [entry["junction_way_ids"] for entry in attribution["severed_ends"]] == [[], []]
    assert "junction(s) away" not in attribution["reason"]
    assert "the road beyond it away" in attribution["reason"]
    # BOTH ends were severed here, so this half of the claim is still earned.
    assert "not because the road ends" in attribution["reason"]

    proposal = genesis_fixes.propose_genesis_fix(
        _ORPHAN_CODE, finding=finding, targets=targets
    )
    assert "접속점을 잘라냈기" not in proposal.rationale_ko
    assert "cut its junction(s) away" not in proposal.rationale_en
    assert "接続点を切り落とした" not in proposal.rationale_ja
    # ...and what the source does NOT say is said, rather than left to the
    # reader to notice that no way was named.
    assert "다른 way를 선언하지 않으므로" in proposal.rationale_ko
    assert "declares no other way at the cut node(s)" in proposal.rationale_en
    assert "他の way を宣言していないため" in proposal.rationale_ja


def test_the_japanese_boundary_clause_is_japanese(workspace_root: Path) -> None:
    """The ja clause interpolated the ENGLISH per-end fragment, so a Japanese
    reader got `20.0 m at its end (the cut node ... meet)` mid-sentence."""
    record = _seed_parent(_standard_census(), _source())
    finding = _orphan_finding()
    proposal = genesis_fixes.propose_genesis_fix(
        _ORPHAN_CODE, finding=finding, targets=_targets(record["id"], finding)
    )
    ja = proposal.rationale_ja
    assert "m at its" not in ja, ja
    assert "the cut node" not in ja, ja
    assert "側 20.0 m" in ja and "側 30.0 m" in ja
    assert "も接続すると宣言しています" in ja


def test_an_attributed_orphan_with_no_region_names_why(workspace_root: Path) -> None:
    """The silent card. A tail whose overhang measured 0 m earns no finger, so
    the composite refuses -- and before this fix the reader was told the lasso
    caused the isolation and then handed no button and no reason at all."""
    record = _seed_parent(
        _census(
            _standard_links(), way_trims=_orphan_trims(start_m=0.0, end_m=0.0)
        ),
        _source(),
    )
    finding = _orphan_finding()
    targets = _targets(record["id"], finding)
    assert "boundary_extension_patches" not in targets
    assert targets["boundary_extension_patches_refusal"]["reason_code"] == (
        "no_boundary_findings"
    )

    proposal = genesis_fixes.propose_genesis_fix(
        _ORPHAN_CODE, finding=finding, targets=targets
    )
    assert "치료:" not in proposal.rationale_ko
    assert "0 m로 측정되어" in proposal.rationale_ko
    assert "the overhang measured 0 m at every severed end" in proposal.rationale_en
    assert "0 m と測定されたため" in proposal.rationale_ja
    # The refusal payload rides on the evidence too, not only in the prose.
    assert proposal.evidence["boundary_extension_patches_refusal"]["reason_code"] == (
        "no_boundary_findings"
    )


def test_every_composite_refusal_code_reaches_the_orphan_card(
    workspace_root: Path,
) -> None:
    """Each reason the composite can hand an ATTRIBUTED orphan has a localized
    sentence, and an unknown one still quotes its code rather than going quiet.
    ``_V4_EXTENSION_REASONS`` is layered under, never copied."""
    shared = {
        "requested_region_not_a_polygon",
        "no_measured_continuation_direction",
        "extension_exceeds_region_cap",
        "extension_polygon_self_intersects",
    }
    own = {
        "no_boundary_findings",
        "corridor_patch_undrawable",
        "patch_mouths_collide_on_edge",
        "source_artifact_missing",
    }
    for code in shared | own:
        clause = genesis_fixes._v3_orphan_extension_refusal_clause({"reason_code": code})
        assert all(text.strip() for text in clause), code
        assert len(set(clause)) == 3, f"{code} must differ across ko/en/ja"
        assert code not in clause[0], f"{code} must be a sentence, not its own code"
    # The shared codes are NOT restated in #48's own table.
    assert not (shared & set(genesis_fixes._V3_ORPHAN_EXTENSION_REASONS))
    # An unknown code is quoted rather than swallowed.
    unknown = genesis_fixes._v3_orphan_extension_refusal_clause(
        {"reason_code": "something_new"}
    )
    assert all("something_new" in text for text in unknown)
    # No record at all keeps the exact pre-#48 answer.
    assert genesis_fixes._v3_orphan_extension_refusal_clause(None) == ("", "", "")


def test_source_orphan_refusal_says_no_region_is_proposed(workspace_root: Path) -> None:
    census = _census(_standard_links(), way_trims=[_v4_trim()])
    record = _seed_parent(census, _source())
    finding = _orphan_finding()
    proposal = genesis_fixes.propose_genesis_fix(
        _ORPHAN_CODE, finding=finding, targets=_targets(record["id"], finding)
    )
    assert proposal.reason_code == genesis_fixes.CONNECTIVITY_IS_NOT_INVENTED
    assert "잘린 적이 없습니다" in proposal.rationale_ko
    assert "제안하지 않습니다" in proposal.rationale_ko
    assert "치료:" not in proposal.rationale_ko
    assert "boundary_extension_patches" not in proposal.evidence


def test_fixes_route_offers_one_region_for_every_boundary_finding(
    client: TestClient, workspace_root: Path
) -> None:
    """End to end over the real route: the job's own verdict, its cards, and
    ONE all-at-once region beside them."""
    record = _seed_parent(_standard_census(), _source())
    gated = genesis_jobs.gate_and_publish(record["id"])
    assert gated["status"] == genesis_jobs.STATUS_REFUSED

    response = client.get(f"/api/genesis/jobs/{record['id']}/fixes", headers=admin_headers())
    assert response.status_code == 200, response.text
    payload = response.json()

    # The seeded network's own verdict raises the V4 runway finding; the orphan
    # finding is this module's synthetic subject, so the ROUTE's composite is
    # asserted on what the verdict actually contains.
    combined = payload.get("boundary_extension_all")
    assert combined is not None, payload.get("boundary_extension_all_refusal")
    assert combined["provenance"]["finding_count"] >= 1
    assert combined["corridor_patches"]
    codes = {entry["code"] for entry in combined["covers"]}
    assert _V4_CODE in codes
    # And each covered finding's own card still carries its own proposal.
    v4_entries = [entry for entry in payload["findings"] if entry["code"] == _V4_CODE]
    assert v4_entries
    assert "boundary_extension" in v4_entries[0]["disposition"]


def test_fixes_route_omits_the_composite_when_no_finding_earns_one(
    client: TestClient, workspace_root: Path
) -> None:
    """An ordinary job sees neither the option nor a refusal about it: a
    disclosure on every response would be noise, not a disclosure."""
    census = _census(_standard_links(), way_trims=[])
    record = _seed_parent(census, _source())
    genesis_jobs.gate_and_publish(record["id"])
    response = client.get(f"/api/genesis/jobs/{record['id']}/fixes", headers=admin_headers())
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "boundary_extension_all" not in payload
    assert "boundary_extension_all_refusal" not in payload


def test_gate_vocabulary_is_untouched_by_this_unit() -> None:
    """``gate.py`` is the JUDGE. Unit #48 is a treatment, and a treatment that
    quietly taught the judge a new word would make a refusal unrecognisable to
    every reader who learned the old one."""
    from services.genesis import gate

    assert "v3_orphan_link" in gate._TEXT
    for banned in (
        "orphan_boundary_attribution",
        "boundary_extension_patches",
        "boundary_cut_metres",
        "severed_ends",
    ):
        assert banned not in gate._TEXT
    # ...and the judge's own module never learned to import the treatment.
    source = Path(gate.__file__).read_text(encoding="utf-8")
    assert "derive_v3_orphan_boundary_attribution" not in source
    assert "derive_boundary_extension_for_findings" not in source

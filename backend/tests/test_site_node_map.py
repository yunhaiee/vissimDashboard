"""The SCATS site -> Vissim node join: what it resolves, and what it refuses.

Every fixture here is SYNTHETIC. Node positions are chosen in Vissim local
metres and the matching site coordinates are produced by pushing those metres
through the same georeference the module uses (``routers.geojson``'s
``GeoTransform``), so a test can place a site "40 m from node 2" without any
client file and without hard-coding a real intersection's coordinates.

The Helmert scale term is Web-Mercator latitude distortion, so one Vissim metre
is one ground metre to within ~0.2% at this latitude; assertions on distance
therefore use a tolerance of a metre rather than exact equality. That slack is
about the *frame*, not about the matching rule — every refusal boundary in these
tests is placed several metres clear of it.
"""
from __future__ import annotations

import math
from pathlib import Path

import pytest

from services.dataimport.schema import PositionHint, Provenance, SiteMetadata
from services.genesis.connectivity import NODE_MERGE_TOLERANCE_M
from services.genesis.diff import GEOREF_MAX_RESIDUAL_M, NOMINAL_LANE_WIDTH_M
from services.genesis.project import MAX_PLANIMETRIC_ERROR_M
from services.site_node_map import (
    COMPARISON_LEVEL,
    GEOREFERENCE_UNAVAILABLE,
    GEOREF_RESIDUAL_M,
    JUNCTION_CENTRE_ALLOWANCE_M,
    PROJECTION_BUDGET_M,
    SITE_NODE_AMBIGUOUS,
    SITE_NODE_CONFLICT,
    SITE_NODE_NO_CANDIDATE,
    SITE_NODE_TOLERANCE_M,
    SITE_POSITION_ABSENT,
    SitePosition,
    map_sites_to_nodes,
    site_positions_from_metadata,
    summarize,
)

# ---------------------------------------------------------------------------
# Fixture construction
# ---------------------------------------------------------------------------

#: Where the synthetic networks are anchored. It has to be a REAL latitude, not
#: the Web-Mercator origin: the Helmert fit's 1.1782 scale term IS Web-Mercator
#: latitude distortion (1/cos 31.93°), so it only cancels back to true ground
#: metres near the latitude it was fitted at. Anchored at the equator instead,
#: one Vissim metre would come out as 1.178 ground metres and every distance in
#: this file would be silently 18% too large — which is exactly what the first
#: run of these tests showed.
_ANCHOR_LAT_DEG = -32.0688
_ANCHOR_LON_DEG = 115.8426
_WEB_MERCATOR_HALF_CIRCUMFERENCE_M = 20037508.34
_REF_MAP_X = _ANCHOR_LON_DEG / 180.0 * _WEB_MERCATOR_HALF_CIRCUMFERENCE_M
_REF_MAP_Y = (
    math.log(math.tan(math.pi / 4.0 + math.radians(_ANCHOR_LAT_DEG) / 2.0))
    / math.pi
    * _WEB_MERCATOR_HALF_CIRCUMFERENCE_M
)


def _vissim_to_latlon(vx: float, vy: float) -> tuple[float, float]:
    """The module's own bridge, used forwards, to site a synthetic count site.

    Imported lazily for the same reason ``benchmark.helmert_bridge`` imports it
    lazily: ``routers.geojson`` pulls in FastAPI and the dataset layer.
    """
    from routers.geojson import (
        DEFAULT_MAP_OFFSET_X,
        DEFAULT_MAP_OFFSET_Y,
        DEFAULT_MAP_ROTATION_DEG,
        DEFAULT_MAP_SCALE_X,
        DEFAULT_MAP_SCALE_Y,
        GeoTransform,
    )

    transform = GeoTransform(
        ref_map_x=_REF_MAP_X, ref_map_y=_REF_MAP_Y, ref_net_x=0.0, ref_net_y=0.0, north_dir=0.0
    )
    return transform.to_wgs84(
        vx,
        vy,
        offset_x=DEFAULT_MAP_OFFSET_X,
        offset_y=DEFAULT_MAP_OFFSET_Y,
        rotation_deg=DEFAULT_MAP_ROTATION_DEG,
        scale_x=DEFAULT_MAP_SCALE_X,
        scale_y=DEFAULT_MAP_SCALE_Y,
    )


def _network(
    tmp_path: Path,
    nodes: dict[str, tuple[float, float]],
    *,
    name: str = "synthetic.inpx",
    georeferenced: bool = True,
    extra_nodes_xml: str = "",
) -> Path:
    """A minimal ``.inpx``: a georeference anchor plus square node polygons.

    Node centres are the polygon centroid (``inpx_parser._parse_nodes``), so a
    1 m square centred on the wanted point puts the centre exactly there while
    still exercising the real polygon path.
    """
    head = '<network><netPara northDir="0">'
    if georeferenced:
        head += (
            f'<refPointMap x="{_REF_MAP_X}" y="{_REF_MAP_Y}"/><refPointNet x="0" y="0"/>'
        )
    head += "</netPara><links></links><nodes>"
    body = []
    for node_id, (x, y) in nodes.items():
        points = "".join(
            f'<point x="{x + dx}" y="{y + dy}"/>'
            for dx, dy in ((-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5))
        )
        body.append(
            f'<node no="{node_id}" name="N{node_id}"><geometry><points>{points}</points>'
            "</geometry></node>"
        )
    path = tmp_path / name
    path.write_text(head + "".join(body) + extra_nodes_xml + "</nodes></network>", encoding="utf-8")
    return path


def _site(site_id: str, vx: float, vy: float) -> SitePosition:
    lat, lon = _vissim_to_latlon(vx, vy)
    return SitePosition(site_id=site_id, site_name=f"site {site_id}", lat=lat, lon=lon)


# ---------------------------------------------------------------------------
# 1. The tolerance is derived, not chosen
# ---------------------------------------------------------------------------


def test_the_tolerance_is_the_sum_of_three_constants_this_repo_already_owns():
    """29 m = 14 + 1 + 14, and each term IS its source constant.

    A literal typed here instead of an import would let the georeference get
    re-fitted (or the lane width corrected) without the tolerance following, so
    the identity is pinned rather than the number.
    """
    assert GEOREF_RESIDUAL_M == GEOREF_MAX_RESIDUAL_M == 14.0
    assert PROJECTION_BUDGET_M == MAX_PLANIMETRIC_ERROR_M == 1.0
    assert JUNCTION_CENTRE_ALLOWANCE_M == 2.0 * NODE_MERGE_TOLERANCE_M
    assert JUNCTION_CENTRE_ALLOWANCE_M == 4.0 * NOMINAL_LANE_WIDTH_M == 14.0
    assert SITE_NODE_TOLERANCE_M == 29.0
    assert (
        SITE_NODE_TOLERANCE_M
        == GEOREF_RESIDUAL_M + PROJECTION_BUDGET_M + JUNCTION_CENTRE_ALLOWANCE_M
    )


# ---------------------------------------------------------------------------
# 2. A resolvable site, and the evidence that resolved it
# ---------------------------------------------------------------------------


def test_a_site_on_a_node_matches_and_reports_the_runner_up_it_beat(tmp_path: Path):
    path = _network(tmp_path, {"1": (0.0, 0.0), "2": (400.0, 0.0)})
    mapping = map_sites_to_nodes(path, [_site("LMTEST1", 0.0, 0.0)])

    assert mapping.refusals == []
    assert mapping.site_nodes == {"LMTEST1": "1"}
    (match,) = mapping.matched
    assert match.node_id == "1"
    assert match.node_name == "N1"
    assert match.distance_m < 1.0
    # The runner-up rides along, so "why this node" never needs a re-run.
    assert match.runner_up_node_id == "2"
    assert match.runner_up_distance_m == pytest.approx(400.0, abs=2.0)
    assert match.margin_m == pytest.approx(SITE_NODE_TOLERANCE_M, abs=1.0)
    assert match.thin_margin is False


def test_matched_nodes_are_reported_with_the_min_node_separation_that_bounds_ambiguity(
    tmp_path: Path,
):
    """With the closest node pair further apart than twice the tolerance, an
    ambiguous match is arithmetically impossible — and the mapping says so
    rather than leaving a reader to check the pairwise distances by hand."""
    path = _network(tmp_path, {"1": (0.0, 0.0), "2": (400.0, 0.0), "3": (0.0, 250.0)})
    mapping = map_sites_to_nodes(path, [_site("LMTEST1", 3.0, 0.0)])

    assert mapping.min_node_separation_m == pytest.approx(250.0, abs=2.0)
    assert mapping.tolerance_cannot_reach_two_nodes is True
    assert mapping.nodes_considered == ["1", "2", "3"]


# ---------------------------------------------------------------------------
# 3. Refusal 1 — nothing within tolerance. Nearest does NOT win.
# ---------------------------------------------------------------------------


def test_a_site_beyond_the_tolerance_is_refused_and_never_snapped_to_the_nearest_node(
    tmp_path: Path,
):
    path = _network(tmp_path, {"1": (0.0, 0.0), "2": (400.0, 0.0)})
    # 60 m out: twice the tolerance, and far clear of the ~0.2% frame slack.
    mapping = map_sites_to_nodes(path, [_site("LMTEST1", 60.0, 0.0)])

    assert mapping.matched == []
    assert mapping.site_nodes == {}
    (refusal,) = mapping.refusals
    assert refusal.code == SITE_NODE_NO_CANDIDATE
    assert refusal.site_id == "LMTEST1"
    # The refusal has to carry the evidence a human needs to overrule it.
    assert refusal.context["nearest_node_id"] == "1"
    assert refusal.context["nearest_distance_m"] == pytest.approx(60.0, abs=1.0)
    assert "29" in refusal.message
    assert set(refusal.messages) == {"ko", "en", "ja"}


# ---------------------------------------------------------------------------
# 4. Refusal 2 — two candidates, and neither is picked
# ---------------------------------------------------------------------------


def test_two_nodes_inside_the_tolerance_make_the_site_ambiguous_and_none_is_chosen(
    tmp_path: Path,
):
    """The closest candidate does not win an ambiguous case.

    Node 1 is 5 m away and node 2 is 15 m away — an ordering that a
    "nearest wins" rule would resolve happily and wrongly. Both are inside 29 m,
    so both are named and the site is left unmapped.
    """
    path = _network(tmp_path, {"1": (0.0, 0.0), "2": (20.0, 0.0)})
    mapping = map_sites_to_nodes(path, [_site("LMTEST1", 5.0, 0.0)])

    assert mapping.matched == []
    assert mapping.site_nodes == {}
    (refusal,) = mapping.refusals
    assert refusal.code == SITE_NODE_AMBIGUOUS
    assert [candidate["node_id"] for candidate in refusal.context["candidates"]] == ["1", "2"]
    assert refusal.context["candidates"][0]["distance_m"] == pytest.approx(5.0, abs=1.0)
    assert refusal.context["candidates"][1]["distance_m"] == pytest.approx(15.0, abs=1.0)


# ---------------------------------------------------------------------------
# 5. Refusal 3 — two sites, one node
# ---------------------------------------------------------------------------


def test_two_sites_resolving_to_one_node_refuse_both_rather_than_preferring_one(
    tmp_path: Path,
):
    path = _network(tmp_path, {"1": (0.0, 0.0), "2": (400.0, 0.0)})
    mapping = map_sites_to_nodes(
        path, [_site("LMTEST1", 2.0, 0.0), _site("LMTEST2", -2.0, 0.0)]
    )

    assert mapping.matched == []
    assert mapping.site_nodes == {}
    assert {refusal.code for refusal in mapping.refusals} == {SITE_NODE_CONFLICT}
    assert sorted(refusal.site_id for refusal in mapping.refusals) == ["LMTEST1", "LMTEST2"]
    for refusal in mapping.refusals:
        assert refusal.context["node_id"] == "1"
        assert refusal.context["site_ids"] == ["LMTEST1", "LMTEST2"]


# ---------------------------------------------------------------------------
# 6. Refusal 4 — a site with no coordinate at all
# ---------------------------------------------------------------------------


def test_a_site_with_no_stated_coordinate_is_refused_before_any_arithmetic():
    provenance = Provenance(source_file="synthetic.xlsx", locator="Site!A1")
    positioned = SiteMetadata(
        provenance=provenance,
        site_id="LMTEST1",
        source_system="SCATS",
        position=PositionHint(x=115.84, y=-32.07),
    )
    bare = SiteMetadata(provenance=provenance, site_id="LMTEST2", source_system="SCATS")
    half = SiteMetadata(
        provenance=provenance,
        site_id="LMTEST3",
        source_system="SCATS",
        position=PositionHint(x=115.84),
    )

    positions, refusals = site_positions_from_metadata([positioned, bare, half])

    assert [position.site_id for position in positions] == ["LMTEST1"]
    # x is longitude and y is latitude — the schema's stated convention, and the
    # one place in the codebase that applies it.
    assert positions[0].lon == 115.84
    assert positions[0].lat == -32.07
    assert [(r.code, r.site_id) for r in refusals] == [
        (SITE_POSITION_ABSENT, "LMTEST2"),
        (SITE_POSITION_ABSENT, "LMTEST3"),
    ]


# ---------------------------------------------------------------------------
# 7. A thin margin is disclosed, not hidden
# ---------------------------------------------------------------------------


def test_a_match_whose_spare_budget_is_under_the_georeference_residual_is_flagged(
    tmp_path: Path,
):
    """Inside 29 m but with less than 14 m to spare: still a match, and still
    labelled as one that rests on the runner-up separation rather than on the
    absolute distance."""
    path = _network(tmp_path, {"1": (0.0, 0.0), "2": (400.0, 0.0)})

    comfortable = map_sites_to_nodes(path, [_site("LMTEST1", 5.0, 0.0)]).matched[0]
    assert comfortable.thin_margin is False
    assert comfortable.margin_m > GEOREF_RESIDUAL_M

    thin = map_sites_to_nodes(path, [_site("LMTEST1", 24.0, 0.0)]).matched[0]
    assert thin.node_id == "1"
    assert thin.thin_margin is True
    assert thin.margin_m < GEOREF_RESIDUAL_M
    # It is still resolved, because the runner-up is far away — and the ratio
    # that says so is on the match.
    assert thin.separation_ratio is not None and thin.separation_ratio > 10.0


# ---------------------------------------------------------------------------
# 8. No georeference anchor -> refuse, never a zero-offset guess
# ---------------------------------------------------------------------------


def test_a_network_without_reference_points_refuses_instead_of_assuming_zero_offset(
    tmp_path: Path,
):
    path = _network(tmp_path, {"1": (0.0, 0.0)}, georeferenced=False)
    mapping = map_sites_to_nodes(path, [_site("LMTEST1", 0.0, 0.0)])

    assert mapping.matched == []
    assert mapping.refusal_codes() == [GEOREFERENCE_UNAVAILABLE]
    assert mapping.nodes_considered == []


# ---------------------------------------------------------------------------
# 9. A node with no derivable centre is named, not parked at the origin
# ---------------------------------------------------------------------------


def test_a_node_with_no_derivable_centre_is_named_rather_than_placed_at_the_origin(
    tmp_path: Path,
):
    """``inpx_parser`` returns (0, 0) for a node it cannot place. Treating that
    as a position would make the local origin a magnet for every distant site,
    so such nodes are excluded and listed."""
    orphan = '<node no="9" name="N9"><linkSegs><linkSegment link="404" fromPos="0"/></linkSegs></node>'
    path = _network(tmp_path, {"1": (500.0, 500.0)}, extra_nodes_xml=orphan)

    mapping = map_sites_to_nodes(path, [_site("LMTEST1", 500.0, 500.0)])

    assert mapping.nodes_considered == ["1"]
    assert mapping.nodes_without_position == ["9"]
    assert mapping.site_nodes == {"LMTEST1": "1"}


# ---------------------------------------------------------------------------
# 10. The map never claims more than a site-level comparison
# ---------------------------------------------------------------------------


def test_every_surface_of_the_map_says_it_is_site_level_only(tmp_path: Path):
    """A previous review found a report hardcoding "aggregated turning counts"
    for a detector-only set. Nothing this map produces may read as movement
    level: the label is on the mapping, on every match, and in the summary."""
    path = _network(tmp_path, {"1": (0.0, 0.0), "2": (400.0, 0.0)})
    mapping = map_sites_to_nodes(path, [_site("LMTEST1", 0.0, 0.0)])

    assert mapping.comparison_level == COMPARISON_LEVEL == "site_total_vs_node_total"
    assert all(match.comparison_level == COMPARISON_LEVEL for match in mapping.matched)
    assert "LMA" in mapping.comparison_level_note
    assert summarize(mapping)["comparison_level"] == COMPARISON_LEVEL
    # The derivation travels with the number, so a reader of a stored map never
    # has to come back to this module to find out where 29 m came from.
    assert "14" in mapping.tolerance_basis and "MRWA" in mapping.tolerance_basis
    assert summarize(mapping)["tolerance_basis"] == mapping.tolerance_basis


# ---------------------------------------------------------------------------
# 11. Refused sites stay refused downstream
# ---------------------------------------------------------------------------


def test_a_refused_site_is_absent_from_site_nodes_so_observed_side_reports_it_unmapped(
    tmp_path: Path,
):
    """``observations.observed_side`` lists a site with no map entry under
    ``sites_without_node_mapping``. That only works if a refusal here produces
    an ABSENT key rather than a null one."""
    path = _network(tmp_path, {"1": (0.0, 0.0), "2": (400.0, 0.0)})
    mapping = map_sites_to_nodes(
        path, [_site("LMOK", 0.0, 0.0), _site("LMFAR", 200.0, 200.0)]
    )

    assert mapping.site_nodes == {"LMOK": "1"}
    assert "LMFAR" not in mapping.site_nodes
    assert [r.site_id for r in mapping.refusals] == ["LMFAR"]


def test_distances_are_symmetric_about_the_node_so_the_join_has_no_preferred_bearing(
    tmp_path: Path,
):
    """A site 25 m north and a site 25 m east of the same node must both land in
    the same bucket — otherwise the tolerance is really an axis-aligned box and
    the derivation above does not describe it."""
    path = _network(tmp_path, {"1": (0.0, 0.0), "2": (600.0, 0.0)})
    north = map_sites_to_nodes(path, [_site("LMN", 0.0, 25.0)])
    east = map_sites_to_nodes(path, [_site("LME", 25.0, 0.0)])

    assert north.matched[0].node_id == east.matched[0].node_id == "1"
    assert math.isclose(north.matched[0].distance_m, east.matched[0].distance_m, abs_tol=1.0)

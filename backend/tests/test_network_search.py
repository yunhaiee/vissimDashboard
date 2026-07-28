"""Tests for ``services.network_search`` + ``routers/search.py`` — the network
component search API (2026-07-28): given a name or a number, find the thing and
return everything needed to zoom + flash it on the diagram.

Three fixtures, following this repo's established precedent
(test_agent_approval_focus.py's docstring):

  1. The bundled synthetic demo network (``data/practice.inpx``) — 16 links,
     2 nodes, 6 vehicle inputs, 2 routing decisions, 6 routes, and (verified
     empirically) ZERO signal controllers, signal heads, conflict areas,
     detectors, PT stops or speed distributions. Enough for the endpoint
     smoke tests and the ranking basics; not enough for most families.
  2. A hand-built minimal .inpx (tmp_path) covering every family the demo
     network lacks, plus the two collision cases that only a purpose-built
     file can make unambiguous: two links sharing one display name (one of
     them a connector, exactly the real network's "South St EB" situation)
     and one id shared by two different families.
  3. The REAL active dataset (``runtime/uploads/current/current.inpx``, the
     Murdoch Drive production model) — skipped when absent, same
     ``pytest.mark.skipif`` precedent as test_signal_parser.py's Beijing
     fixture. This is the only fixture that proves the acceptance queries
     ("515", "South St", "murdoch", "TCS398", "2140") behave on a network
     with 312 links and 216 conflict areas.

The demo network is also the language regression guard: its object names are
pure ASCII, so any Hangul/kana in a response body could only have come from
this module hardcoding a localized string — the bug fixed in bd63874.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from main import app
from services import network_search

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_INPX = REPO_ROOT / "data" / "practice.inpx"
ACTIVE_REAL_INPX = REPO_ROOT / "runtime" / "uploads" / "current" / "current.inpx"

_requires_real_dataset = pytest.mark.skipif(
    not ACTIVE_REAL_INPX.is_file(),
    reason=f"real active dataset not present on this machine: {ACTIVE_REAL_INPX}",
)

# Hangul syllables/jamo + hiragana/katakana + CJK ideographs. Any of these in a
# response built from an ASCII-named network means a localized string leaked
# out of the backend instead of being composed by the UI's t() plumbing.
_NON_ASCII_SCRIPT = re.compile(r"[가-힣ᄀ-ᇿ぀-ヿ一-鿿]")


# ---------------------------------------------------------------------------
# Fixture (2): hand-built minimal network covering the families the demo lacks
# ---------------------------------------------------------------------------
#
# Geometry is chosen so every assertion below is arithmetic, not approximate:
#   link 10  "Broad St EB"     (0,0) -> (100,0)
#   link 20  "Broad St EB"     (100,0) -> (120,0), a CONNECTOR off link 10 —
#                              the real network's duplicate-display-name case
#   link 30  "Cross Ave NB"    (60,-50) -> (60,50), crosses link 10 at (60,0)
#   link 70  "Seventh Ave"     (200,200) -> (260,200) — id collides with
#                              speedDistribution 70, two families, one id
#   link 105 "No Geometry Rd"  no <geometry> at all -> searchable, bbox None
_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network version="2400">
  <links>
    <link no="10" name="Broad St EB">
      <geometry><linkPolyPts>
        <linkPolyPoint x="0" y="0" zOffset="0"/><linkPolyPoint x="100" y="0" zOffset="0"/>
      </linkPolyPts></geometry>
      <lanes><lane width="3.5"/></lanes>
    </link>
    <link no="20" name="Broad St EB">
      <geometry><linkPolyPts>
        <linkPolyPoint x="100" y="0" zOffset="0"/><linkPolyPoint x="120" y="0" zOffset="0"/>
      </linkPolyPts></geometry>
      <lanes><lane width="3.5"/></lanes>
      <fromLinkEndPt lane="10 1" pos="100.0"/>
      <toLinkEndPt lane="30 1" pos="0.0"/>
    </link>
    <link no="30" name="Cross Ave NB">
      <geometry><linkPolyPts>
        <linkPolyPoint x="60" y="-50" zOffset="0"/><linkPolyPoint x="60" y="50" zOffset="0"/>
      </linkPolyPts></geometry>
      <lanes><lane width="3.5"/></lanes>
    </link>
    <link no="70" name="Seventh Ave">
      <geometry><linkPolyPts>
        <linkPolyPoint x="200" y="200" zOffset="0"/><linkPolyPoint x="260" y="200" zOffset="0"/>
      </linkPolyPts></geometry>
      <lanes><lane width="3.5"/></lanes>
    </link>
    <link no="105" name="No Geometry Rd">
      <lanes><lane width="3.5"/></lanes>
    </link>
  </links>
  <signalHeads><signalHead no="7" name="SH North" lane="10 1" pos="90" sg="1 2"/></signalHeads>
  <conflictAreas><conflictArea no="900" linkA="10" linkB="30" status="PASSIVE"/></conflictAreas>
  <speedDistributions>
    <speedDistribution no="70" name="70 km/h Adjusted"/>
    <speedDistribution no="50" name="50 km/h"/>
  </speedDistributions>
  <ptStops><ptStop no="3" name="Broad St Stop" lane="10 1" pos="40" length="20"/></ptStops>
  <detectors><detector no="8" name="Loop A" lane="10 1" pos="20" length="2"/></detectors>
  <queueCounters><queueCounter no="4" name="QC Broad" link="10" pos="50"/></queueCounters>
  <dataCollectionPoints><dataCollectionPoint no="5" name="DCP Broad" lane="10 1" pos="30"/></dataCollectionPoints>
  <reducedSpeedAreas><reducedSpeedArea no="6" name="RSA Broad" lane="10 1" pos="60" length="10"/></reducedSpeedAreas>
  <vehicleInputs><vehicleInput no="1" name="Broad St EB input" link="10"/></vehicleInputs>
</network>
"""


@pytest.fixture()
def fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "search_fixture.inpx"
    path.write_text(_FIXTURE_INPX, encoding="utf-8")
    network_search.clear_cache()
    return path


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _ids(payload: dict[str, Any], obj_type: str | None = None) -> list[str]:
    return [hit["id"] for hit in payload["results"] if obj_type is None or hit["type"] == obj_type]


def _typed_ids(payload: dict[str, Any]) -> list[tuple[str, str]]:
    return [(hit["type"], hit["id"]) for hit in payload["results"]]


def _fields(payload: dict[str, Any]) -> list[str]:
    return [hit["match"]["field"] for hit in payload["results"]]


# ---------------------------------------------------------------------------
# Empty / degenerate queries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", ["", "   ", "\t\n", None])
def test_empty_query_returns_nothing_not_everything(fixture_inpx: Path, query):
    """A blank search box must not dump the entire network — the whole point
    of the bounded-output rule."""
    payload = network_search.search(query or "", inpx_path=fixture_inpx)
    assert payload["total"] == 0
    assert payload["results"] == []
    assert payload["truncated"] is False


def test_query_matching_nothing_is_an_empty_hit_list_not_an_error(fixture_inpx: Path):
    payload = network_search.search("zzz-no-such-component", inpx_path=fixture_inpx)
    assert payload["total"] == 0
    assert payload["returned"] == 0
    assert payload["results"] == []
    assert payload["truncated"] is False


# ---------------------------------------------------------------------------
# Matching + ranking
# ---------------------------------------------------------------------------


def test_exact_id_outranks_every_name_match(fixture_inpx: Path):
    payload = network_search.search("10", inpx_path=fixture_inpx)
    assert payload["results"][0]["type"] == "link"
    assert payload["results"][0]["id"] == "10"
    assert payload["results"][0]["match"]["field"] == "id"
    # link 105 shares the "10" id prefix and must come after the exact hit.
    assert ("link", "105") in _typed_ids(payload)
    assert _fields(payload).index("id") < _fields(payload).index("id_prefix")


def test_exact_name_outranks_name_prefix(fixture_inpx: Path):
    """Links 10/20 are named exactly "Broad St EB"; the vehicle input is
    "Broad St EB input", a prefix hit. Spelling a name out in full must not
    be buried under things that merely start with it."""
    payload = network_search.search("Broad St EB", inpx_path=fixture_inpx)
    assert _fields(payload) == ["name", "name", "name_prefix"]
    assert _typed_ids(payload) == [("link", "10"), ("link", "20"), ("vehicle_input", "1")]


def test_exact_name_is_matched_whole_not_by_prefix(fixture_inpx: Path):
    payload = network_search.search("Cross Ave NB", inpx_path=fixture_inpx)
    assert payload["results"][0]["match"]["field"] == "name"
    assert payload["results"][0]["id"] == "30"


def test_name_prefix_outranks_name_substring(fixture_inpx: Path):
    payload = network_search.search("Broad", inpx_path=fixture_inpx)
    fields = _fields(payload)
    assert fields[0] == "name_prefix"
    # "Broad St EB input" (vehicle input) and "QC Broad"/"RSA Broad"/"DCP
    # Broad" all contain "broad"; the prefix hits must all come first.
    assert fields == sorted(fields, key=lambda f: 0 if f == "name_prefix" else 1)
    assert {"link", "vehicle_input", "queue_counter", "reduced_speed_area", "data_collection_point"} <= {
        hit["type"] for hit in payload["results"]
    }


def test_name_matching_is_case_insensitive(fixture_inpx: Path):
    lower = network_search.search("broad st eb", inpx_path=fixture_inpx)
    upper = network_search.search("BROAD ST EB", inpx_path=fixture_inpx)
    assert _typed_ids(lower) == _typed_ids(upper)
    assert ("link", "10") in _typed_ids(lower)


def test_two_links_sharing_a_display_name_are_returned_distinctly(fixture_inpx: Path):
    """The real network's "South St EB" case: a link and its auto-generated
    connector carry an IDENTICAL display name. Both must come back as
    separate hits with their own ids and their own focus."""
    payload = network_search.search("Broad St EB", inpx_path=fixture_inpx)
    link_hits = [hit for hit in payload["results"] if hit["type"] == "link"]
    assert [hit["id"] for hit in link_hits] == ["10", "20"]
    assert all(hit["name"] == "Broad St EB" for hit in link_hits)
    assert link_hits[0]["label"]["detail"]["connector"] is False
    assert link_hits[1]["label"]["detail"]["connector"] is True
    # Distinct geometry, not one hit rendered twice.
    assert link_hits[0]["focus"]["bbox"] != link_hits[1]["focus"]["bbox"]


def test_one_id_shared_by_two_families_returns_both(fixture_inpx: Path):
    """Link 70 and speed distribution 70 are different objects that happen to
    share a number — a search must not collapse them."""
    payload = network_search.search("70", inpx_path=fixture_inpx)
    typed = _typed_ids(payload)
    assert ("link", "70") in typed
    assert ("des_speed_distribution", "70") in typed
    assert all(hit["match"]["field"] == "id" for hit in payload["results"] if hit["id"] == "70")
    # Family order is the stable tie-break: links are registered first.
    assert typed.index(("link", "70")) < typed.index(("des_speed_distribution", "70"))


def test_id_prefix_tier_depends_on_whether_the_query_is_digits(fixture_inpx: Path):
    """The id-prefix tier moves, because one fixed position is wrong for one
    of the two ways the box gets used.

    An ALL-DIGIT query is someone reaching for a number, and they arrive at
    it one digit at a time. Ranking id-prefix below name-substring buried
    them on the real network, where 216 conflict areas are named
    "Link <a> vs Link <b>" so every link number is also a substring of 216
    NAMES: measured live, `51` pushed the first link to result 17 and `11`
    returned no link at all in the first 20 rows. So digits promote
    id-prefix above both name tiers.

    Anything else is a lookup by name and cannot id-prefix-match while ids
    stay numeric, so id-prefix stays LAST there — if alphanumeric ids ever
    appear, a partial NAME hit is the stronger evidence of intent.

    Isolated as a direct ``_rank_for`` unit test (bypassing the fixture/
    index machinery) because the hand-built fixture has no query that
    produces a non-exact id-prefix hit and a name hit on DIFFERENT rows at
    once — constructing one row-by-row here is far more precise than
    stretching the shared fixture to coincidentally produce one."""
    id_prefix_row = network_search._Row(
        family="link", seq=0, obj_id="501", name="Nameless Rd",
        id_norm="501", name_norm="nameless rd", obj={},
    )
    name_prefix_row = network_search._Row(
        family="link", seq=1, obj_id="9", name="50th Street",
        id_norm="9", name_norm="50th street", obj={},
    )
    name_substring_row = network_search._Row(
        family="link", seq=2, obj_id="8", name="West 50th Ave",
        id_norm="8", name_norm="west 50th ave", obj={},
    )
    # Digits: the id-prefix row must beat BOTH name rows.
    assert (
        network_search._rank_for(id_prefix_row, "50", None, True)
        == network_search.RANK_ID_PREFIX_NUMERIC
    )
    assert (
        network_search._rank_for(id_prefix_row, "50", None, True)
        < network_search._rank_for(name_prefix_row, "50", None, True)
        < network_search._rank_for(name_substring_row, "50", None, True)
    )
    # Non-digits: unchanged — id-prefix is the last tier.
    assert network_search._rank_for(id_prefix_row, "50", None) == network_search.RANK_ID_PREFIX
    assert network_search._rank_for(name_prefix_row, "50", None) == network_search.RANK_NAME_PREFIX
    assert network_search._rank_for(name_substring_row, "50", None) == network_search.RANK_NAME_SUBSTRING
    assert (
        network_search.RANK_EXACT_ID
        < network_search.RANK_EXACT_NAME
        < network_search.RANK_TYPE
        < network_search.RANK_ID_PREFIX_NUMERIC
        < network_search.RANK_NAME_PREFIX
        < network_search.RANK_NAME_SUBSTRING
        < network_search.RANK_ID_PREFIX
    )
    # Both id-prefix tiers report the same wire-visible match field: the
    # distinction is ranking policy, not a different kind of match.
    assert (
        network_search._MATCH_FIELD_BY_RANK[network_search.RANK_ID_PREFIX_NUMERIC]
        == network_search._MATCH_FIELD_BY_RANK[network_search.RANK_ID_PREFIX]
        == "id_prefix"
    )


def test_ranking_is_stable_across_identical_queries(fixture_inpx: Path):
    first = network_search.search("Broad", inpx_path=fixture_inpx)
    second = network_search.search("Broad", inpx_path=fixture_inpx)
    assert _typed_ids(first) == _typed_ids(second)


# ---------------------------------------------------------------------------
# Class ("type name") queries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "expected_type", "expected_ids"),
    [
        ("signal head", "signal_head", ["7"]),
        ("신호등", "signal_head", ["7"]),
        ("信号灯", "signal_head", ["7"]),
        ("conflict area", "conflict_area", ["900"]),
        ("상충 영역", "conflict_area", ["900"]),
        ("pt stop", "pt_stop", ["3"]),
        ("정류장", "pt_stop", ["3"]),
        ("detector", "detector", ["8"]),
        ("검지기", "detector", ["8"]),
        ("queue counter", "queue_counter", ["4"]),
        ("data collection point", "data_collection_point", ["5"]),
        ("reduced speed area", "reduced_speed_area", ["6"]),
        ("speed distribution", "des_speed_distribution", ["70", "50"]),
    ],
)
def test_type_name_query_lists_that_class(fixture_inpx: Path, query, expected_type, expected_ids):
    payload = network_search.search(query, inpx_path=fixture_inpx)
    assert _ids(payload, expected_type) == expected_ids
    assert all(hit["match"]["field"] == "type" for hit in payload["results"] if hit["type"] == expected_type)


def test_type_alias_is_whitespace_insensitive(fixture_inpx: Path):
    spaced = network_search.search("signal head", inpx_path=fixture_inpx)
    squashed = network_search.search("signalhead", inpx_path=fixture_inpx)
    assert _typed_ids(spaced) == _typed_ids(squashed)


def test_class_query_outranks_incidental_name_noise(fixture_inpx: Path):
    """"link" is a class alias AND a substring of every conflict area's
    synthetic display name ("Link 10 vs Link 30"). The class must win."""
    payload = network_search.search("link", inpx_path=fixture_inpx, limit=50)
    assert payload["results"][0]["type"] == "link"
    types = [hit["type"] for hit in payload["results"]]
    assert types.index("link") < types.index("conflict_area")


def test_partial_type_alias_is_not_a_class_query(fixture_inpx: Path):
    """Only a WHOLE-query alias browses a class; "signal" must not become
    "signal head" by prefix."""
    payload = network_search.search("signal hea", inpx_path=fixture_inpx)
    assert [hit for hit in payload["results"] if hit["match"]["field"] == "type"] == []


# ---------------------------------------------------------------------------
# Focus geometry
# ---------------------------------------------------------------------------


def test_link_hit_focus_is_its_own_polyline_extent(fixture_inpx: Path):
    payload = network_search.search("10", inpx_path=fixture_inpx)
    hit = payload["results"][0]
    bbox = hit["focus"]["bbox"]
    assert bbox["x_min"] == pytest.approx(0.0)
    assert bbox["x_max"] == pytest.approx(100.0)
    # Degenerate y span floored by the accumulator's _MIN_SPAN_M.
    assert bbox["y_max"] - bbox["y_min"] == pytest.approx(15.0)
    assert {(e["type"], e["id"]) for e in hit["focus"]["elements"]} == {("link", "10")}


def test_conflict_area_focus_anchors_on_the_real_crossing_point(fixture_inpx: Path):
    """Reuses approval_focus's crossing_anchor: links 10 and 30 cross at
    (60, 0), so the bbox is that point plus the shared context margin — NOT
    the union of the two links' extents."""
    payload = network_search.search("900", inpx_path=fixture_inpx)
    hit = payload["results"][0]
    assert hit["type"] == "conflict_area"
    bbox = hit["focus"]["bbox"]
    centre = ((bbox["x_min"] + bbox["x_max"]) / 2, (bbox["y_min"] + bbox["y_max"]) / 2)
    assert centre == pytest.approx((60.0, 0.0))
    assert bbox["x_max"] - bbox["x_min"] == pytest.approx(100.0)
    assert {(e["type"], e["id"]) for e in hit["focus"]["elements"]} == {
        ("conflict_area", "900"),
        ("link", "10"),
        ("link", "30"),
    }


def test_point_element_focus_anchors_on_the_element_and_names_its_link(fixture_inpx: Path):
    payload = network_search.search("SH North", inpx_path=fixture_inpx)
    hit = payload["results"][0]
    assert hit["type"] == "signal_head"
    bbox = hit["focus"]["bbox"]
    # Signal head sits at pos=90 on link 10 ((0,0)->(100,0)) -> x=90.
    assert (bbox["x_min"] + bbox["x_max"]) / 2 == pytest.approx(90.0)
    assert {(e["type"], e["id"]) for e in hit["focus"]["elements"]} == {("signal_head", "7"), ("link", "10")}


def test_hit_without_geometry_returns_bbox_null_not_an_error(fixture_inpx: Path):
    """Link 105 carries no <geometry> at all — findable, not flashable."""
    payload = network_search.search("No Geometry Rd", inpx_path=fixture_inpx)
    assert payload["total"] == 1
    hit = payload["results"][0]
    assert hit["id"] == "105"
    assert hit["focus"] == {"elements": [], "bbox": None}


def test_family_with_no_geometry_anywhere_returns_bbox_null(fixture_inpx: Path):
    payload = network_search.search("70 km/h Adjusted", inpx_path=fixture_inpx)
    assert payload["total"] == 1
    hit = payload["results"][0]
    assert hit["type"] == "des_speed_distribution"
    assert hit["focus"] == {"elements": [], "bbox": None}


def test_focus_resolution_failure_degrades_to_bbox_null(fixture_inpx: Path, monkeypatch):
    """A geometry bug must cost the flash, never the search."""

    def _boom(index, obj):
        raise RuntimeError("synthetic geometry failure")

    broken = dataclasses.replace(network_search._FAMILY_BY_TYPE["link"], focus=_boom)
    monkeypatch.setitem(network_search._FAMILY_BY_TYPE, "link", broken)

    payload = network_search.search("Broad St EB", inpx_path=fixture_inpx)
    link_hits = [hit for hit in payload["results"] if hit["type"] == "link"]
    assert [hit["id"] for hit in link_hits] == ["10", "20"]
    assert all(hit["focus"] == {"elements": [], "bbox": None} for hit in link_hits)


def test_focus_shape_matches_the_frontend_url_codec_contract(fixture_inpx: Path):
    """``frontend/lib/networkFocus.ts`` consumes exactly ``{bbox, elements}``
    with ``{type, id}`` refs and four finite bbox numbers."""
    payload = network_search.search("Broad", inpx_path=fixture_inpx, limit=50)
    for hit in payload["results"]:
        focus = hit["focus"]
        assert set(focus) == {"elements", "bbox"}
        assert isinstance(focus["elements"], list)
        for element in focus["elements"]:
            assert set(element) == {"type", "id", "label"}
            assert ":" not in element["type"] and "," not in element["id"]
        if focus["bbox"] is not None:
            assert set(focus["bbox"]) == {"x_min", "y_min", "x_max", "y_max"}
            assert focus["bbox"]["x_max"] > focus["bbox"]["x_min"]
            assert focus["bbox"]["y_max"] > focus["bbox"]["y_min"]


# ---------------------------------------------------------------------------
# Bounded output
# ---------------------------------------------------------------------------


def test_total_counts_every_match_while_results_stay_capped(fixture_inpx: Path):
    full = network_search.search("link", inpx_path=fixture_inpx, limit=50)
    capped = network_search.search("link", inpx_path=fixture_inpx, limit=2)
    assert capped["total"] == full["total"] > 2
    assert capped["returned"] == 2
    assert capped["truncated"] is True
    assert full["truncated"] is False
    assert _typed_ids(capped) == _typed_ids(full)[:2]


@pytest.mark.parametrize(
    ("requested", "effective"),
    [(0, 1), (-5, 1), (1, 1), (20, 20), (50, 50), (51, 50), (100000, network_search.MAX_LIMIT)],
)
def test_limit_is_clamped_never_rejected(fixture_inpx: Path, requested, effective):
    payload = network_search.search("link", inpx_path=fixture_inpx, limit=requested)
    assert payload["limit"] == effective
    assert payload["returned"] <= effective


def test_default_limit_is_the_documented_page_size(fixture_inpx: Path):
    payload = network_search.search("link", inpx_path=fixture_inpx)
    assert payload["limit"] == network_search.DEFAULT_LIMIT == 20


# ---------------------------------------------------------------------------
# Caching (no reparse per request)
# ---------------------------------------------------------------------------


def test_index_is_built_once_per_mtime(fixture_inpx: Path, monkeypatch):
    network_search.clear_cache()
    calls: list[Path] = []
    real_builder = network_search.build_shadow_model

    def _counting_builder(path):
        calls.append(Path(path))
        return real_builder(path)

    monkeypatch.setattr(network_search, "build_shadow_model", _counting_builder)

    for _ in range(6):
        network_search.search("Broad", inpx_path=fixture_inpx)
    assert len(calls) == 1, "the searchable index must not be rebuilt per request"


def test_index_rebuilds_when_the_inpx_changes(fixture_inpx: Path):
    network_search.clear_cache()
    before = network_search.search("Broad St EB", inpx_path=fixture_inpx)
    assert before["total"] >= 2

    fixture_inpx.write_text(_FIXTURE_INPX.replace('name="Broad St EB"', 'name="Renamed St"'), encoding="utf-8")
    # Bump mtime explicitly: a rewrite inside one filesystem clock tick can
    # otherwise be indistinguishable from the original.
    stat = fixture_inpx.stat()
    os.utime(fixture_inpx, (stat.st_atime + 10, stat.st_mtime + 10))

    after = network_search.search("Broad St EB", inpx_path=fixture_inpx)
    assert [hit for hit in after["results"] if hit["type"] == "link"] == []
    assert network_search.search("Renamed St", inpx_path=fixture_inpx)["total"] >= 2


# ---------------------------------------------------------------------------
# Family catalogue integrity
# ---------------------------------------------------------------------------


def test_every_family_is_reachable_by_its_own_type_token():
    for family in network_search.searchable_families():
        assert network_search._TYPE_BY_ALIAS[network_search._norm_alias(family["type"])] == family["type"]


def test_no_alias_is_silently_claimed_by_two_families():
    seen: dict[str, str] = {}
    for family in network_search._FAMILIES:
        for alias in family.aliases:
            key = network_search._norm_alias(alias)
            assert key not in seen, f"alias {alias!r} claimed by both {seen.get(key)} and {family.type}"
            seen[key] = family.type


def test_family_catalogue_covers_the_named_component_classes():
    types = {family["type"] for family in network_search.searchable_families()}
    assert {
        "link",
        "node",
        "signal_controller",
        "signal_head",
        "vehicle_input",
        "detector",
        "des_speed_decision",
        "conflict_area",
        "routing_decision",
        "route",
        "pt_stop",
        "queue_counter",
        "data_collection_point",
        "traveltime_section",
        "reduced_speed_area",
        "vehicle_composition",
    } <= types


# ---------------------------------------------------------------------------
# HTTP endpoint (demo dataset — conftest pins the active dataset to data/)
# ---------------------------------------------------------------------------


def test_endpoint_returns_ranked_bounded_payload(client: TestClient):
    response = client.get("/api/network/search", params={"q": "Main St"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "Main St"
    assert payload["source"] == {"view": "active"}
    assert payload["total"] >= 1
    assert payload["returned"] == len(payload["results"])
    assert payload["truncated"] == (payload["total"] > payload["returned"])
    assert {"type", "id", "name", "label", "match", "focus"} <= set(payload["results"][0])


def test_endpoint_missing_query_param_returns_empty_not_422(client: TestClient):
    response = client.get("/api/network/search")
    assert response.status_code == 200
    assert response.json()["results"] == []


def test_endpoint_clamps_an_absurd_limit(client: TestClient):
    response = client.get("/api/network/search", params={"q": "link", "limit": 999999})
    assert response.status_code == 200
    assert response.json()["limit"] == network_search.MAX_LIMIT


def test_endpoint_payload_carries_no_hardcoded_localized_strings(client: TestClient):
    """The demo network's names are pure ASCII, so any Hangul/kana here could
    only come from this backend baking in a localized label — exactly the
    regression fixed in bd63874. Labels ship as RAW PARTS; the UI localizes."""
    for query in ["Main St", "link", "신호등", "1", "node"]:
        response = client.get("/api/network/search", params={"q": query, "limit": 50})
        body = json.dumps(response.json()["results"], ensure_ascii=False)
        assert not _NON_ASCII_SCRIPT.search(body), f"localized string leaked for q={query!r}"


def test_families_endpoint_lists_the_catalogue(client: TestClient):
    response = client.get("/api/network/search/families")
    assert response.status_code == 200
    families = response.json()["families"]
    assert {"type", "source", "id_field", "name_field", "aliases"} <= set(families[0])
    assert len(families) == len(network_search._FAMILIES)


def test_endpoint_does_not_shadow_the_existing_network_route(client: TestClient):
    """``/api/network/search`` shares a prefix with ``GET /api/network``;
    registering it must not have stolen that route."""
    assert client.get("/api/network").status_code == 200


# ---------------------------------------------------------------------------
# Real active dataset (Murdoch Drive) — the acceptance queries
# ---------------------------------------------------------------------------


@_requires_real_dataset
def test_real_dataset_exact_link_id_is_the_first_hit():
    payload = network_search.search("515", inpx_path=ACTIVE_REAL_INPX)
    top = payload["results"][0]
    assert (top["type"], top["id"], top["name"]) == ("link", "515", "South St EB")
    assert top["match"]["field"] == "id"
    assert top["focus"]["bbox"] is not None


@_requires_real_dataset
def test_real_dataset_link_and_connector_sharing_a_name_both_returned():
    """"South St EB" is carried by both real links and their auto-generated
    connectors on this network — every one must come back distinctly."""
    payload = network_search.search("South St EB", inpx_path=ACTIVE_REAL_INPX, limit=50)
    link_hits = [hit for hit in payload["results"] if hit["type"] == "link"]
    ids = [hit["id"] for hit in link_hits]
    assert "515" in ids and "10969" in ids
    assert len(ids) == len(set(ids))
    connectors = [hit for hit in link_hits if hit["label"]["detail"]["connector"]]
    plain = [hit for hit in link_hits if not hit["label"]["detail"]["connector"]]
    assert connectors and plain


@_requires_real_dataset
def test_real_dataset_street_name_prefix_search():
    payload = network_search.search("South St", inpx_path=ACTIVE_REAL_INPX)
    assert payload["total"] > payload["returned"]
    assert payload["truncated"] is True
    assert all("south st" in hit["name"].lower() for hit in payload["results"])


@_requires_real_dataset
def test_real_dataset_corridor_name_search_is_bounded():
    payload = network_search.search("murdoch", inpx_path=ACTIVE_REAL_INPX)
    assert payload["total"] > network_search.DEFAULT_LIMIT
    assert payload["returned"] == network_search.DEFAULT_LIMIT
    assert payload["truncated"] is True
    assert all("murdoch" in hit["name"].lower() for hit in payload["results"])


@_requires_real_dataset
def test_real_dataset_signal_controller_by_name():
    payload = network_search.search("TCS398", inpx_path=ACTIVE_REAL_INPX)
    assert payload["total"] == 1
    hit = payload["results"][0]
    assert hit["type"] == "signal_controller"
    assert hit["name"].startswith("TCS398")
    # Focus is the controller's own signal heads (approval_focus's policy).
    assert hit["focus"]["bbox"] is not None
    assert any(element["type"] == "signal_head" for element in hit["focus"]["elements"])


@_requires_real_dataset
def test_real_dataset_conflict_area_by_number():
    payload = network_search.search("2140", inpx_path=ACTIVE_REAL_INPX)
    top = payload["results"][0]
    assert (top["type"], top["id"]) == ("conflict_area", "2140")
    assert top["match"]["field"] == "id"
    assert top["label"]["detail"]["link_a"] == "515"
    bbox = top["focus"]["bbox"]
    assert bbox is not None
    # Anchored on the derived crossing point, not the two links' union.
    assert bbox["x_max"] - bbox["x_min"] == pytest.approx(2 * 50.0)
    assert {(e["type"], e["id"]) for e in top["focus"]["elements"]} >= {
        ("conflict_area", "2140"),
        ("link", "515"),
        ("link", "10969"),
    }


@_requires_real_dataset
def test_real_dataset_unknown_query_is_empty():
    payload = network_search.search("no-such-component-anywhere", inpx_path=ACTIVE_REAL_INPX)
    assert payload["total"] == 0
    assert payload["results"] == []

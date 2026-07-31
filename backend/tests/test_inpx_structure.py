"""Tests for v2b unit (1)'s XML-build spine (``services/inpx_structure.py``):
element construction/removal helpers, reparse verification, reference-
integrity verification (both directions -- refuse-on-referenced removal,
dangling-reference-on-add), and the structural proof pair
(``remove_last_lane``/``add_lane``) against synthetic fixtures. No live COM
anywhere in this file -- every ``apply_structural_edit`` call here passes
``run_load_check=False`` (component 4, the COM load oracle, is proven
separately by ``backend/tools/lane_removal_structural_live_check.py`` and
the live transcript pasted in this unit's report).

``_fixture_with_links`` derives a synthetic network by swapping ONLY
``data/practice.inpx``'s ``<links>...</links>`` section for hand-built test
content, keeping every other section (already proven parseable by the
existing ``test_build_shadow_model_has_exactly_the_contract_keys`` pin)
untouched -- this sidesteps having to hand-author a full, independently
valid .inpx from scratch for every edge case.
"""
from __future__ import annotations

import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from services import inpx_structure as struct

PRACTICE_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"

_LINKS_RE = re.compile(r"<links>.*?</links>", re.DOTALL)


def _fixture_with_links(tmp_path: Path, links_inner_xml: str, name: str = "fixture.inpx") -> Path:
    text = PRACTICE_INPX.read_text(encoding="utf-8")
    assert _LINKS_RE.search(text), "practice.inpx must contain a <links>...</links> section"
    new_text = _LINKS_RE.sub(f"<links>{links_inner_xml}</links>", text, count=1)
    path = tmp_path / name
    path.write_text(new_text, encoding="utf-8")
    return path


def _copy_practice(tmp_path: Path, name: str = "practice_copy.inpx") -> Path:
    dest = tmp_path / name
    shutil.copyfile(PRACTICE_INPX, dest)
    return dest


# ---------------------------------------------------------------------------
# Component 1: element construction/removal helpers
# ---------------------------------------------------------------------------


def test_get_or_create_top_level_section_returns_existing_untouched():
    root = ET.fromstring("<network><areaBehaviorTypes/><links/></network>")
    section = struct.get_or_create_top_level_section(root, "links")
    assert section is root.find("links")
    assert len(root) == 2  # nothing new inserted


def test_get_or_create_top_level_section_inserts_at_alphabetical_position():
    # areaBehaviorTypes, links already present; "conflictAreas" sorts
    # between them alphabetically.
    root = ET.fromstring("<network><areaBehaviorTypes/><links/></network>")
    section = struct.get_or_create_top_level_section(root, "conflictAreas")
    tags = [child.tag for child in root]
    assert tags == ["areaBehaviorTypes", "conflictAreas", "links"]
    assert section is root.find("conflictAreas")


def test_get_or_create_top_level_section_appends_when_it_sorts_last():
    root = ET.fromstring("<network><areaBehaviorTypes/></network>")
    struct.get_or_create_top_level_section(root, "zoo")
    assert [child.tag for child in root] == ["areaBehaviorTypes", "zoo"]


def test_next_id_max_plus_one():
    container = ET.fromstring('<links><link no="3"/><link no="7"/><link no="1"/></links>')
    assert struct.next_id(container, "link", "no") == "8"


def test_next_id_empty_container_starts_at_one():
    container = ET.fromstring("<links/>")
    assert struct.next_id(container, "link", "no") == "1"


def test_next_id_ignores_non_numeric_ids():
    container = ET.fromstring('<links><link no="abc"/><link no="4"/></links>')
    assert struct.next_id(container, "link", "no") == "5"


# ---------------------------------------------------------------------------
# Component 2: reparse verification
# ---------------------------------------------------------------------------


def test_verify_reparse_succeeds_on_practice_inpx(tmp_path: Path):
    path = _copy_practice(tmp_path)
    shadow = struct.verify_reparse(path)
    assert len(shadow["links"]) == 16


def test_verify_reparse_raises_on_malformed_xml(tmp_path: Path):
    path = tmp_path / "broken.inpx"
    path.write_text("<network><links><link no=\"1\">", encoding="utf-8")
    with pytest.raises(struct.ReparseVerificationError):
        struct.verify_reparse(path)


def test_verify_reparse_raises_when_expect_fails(tmp_path: Path):
    path = _copy_practice(tmp_path)
    with pytest.raises(struct.ReparseVerificationError):
        struct.verify_reparse(path, expect=lambda shadow: len(shadow["links"]) == 999)


def test_verify_reparse_passes_when_expect_succeeds(tmp_path: Path):
    path = _copy_practice(tmp_path)
    shadow = struct.verify_reparse(path, expect=lambda shadow: len(shadow["links"]) == 16)
    assert len(shadow["links"]) == 16


# ---------------------------------------------------------------------------
# Component 3a: reference integrity -- plain id (links referenced by
# connectors), against practice.inpx's REAL (fixture-convention) connectors:
# fromLinkEndPt lane="101 1", fromLinkEndPt lane="140 1", toLinkEndPt
# lane="140 1", toLinkEndPt lane="208 1".
# ---------------------------------------------------------------------------


def test_find_id_references_finds_connector_referencing_link():
    root = ET.parse(PRACTICE_INPX).getroot()
    referrers = struct.find_id_references(root, "links", "101")
    assert len(referrers) == 1
    assert referrers[0]["tag"] == "fromLinkEndPt"
    assert referrers[0]["value"] == "101 1"


def test_find_id_references_finds_link_referenced_by_both_endpoints():
    root = ET.parse(PRACTICE_INPX).getroot()
    referrers = struct.find_id_references(root, "links", "140")
    assert len(referrers) == 2
    tags = sorted(r["tag"] for r in referrers)
    assert tags == ["fromLinkEndPt", "toLinkEndPt"]


def test_find_id_references_empty_for_unreferenced_link():
    root = ET.parse(PRACTICE_INPX).getroot()
    assert struct.find_id_references(root, "links", "301") == []


# ---------------------------------------------------------------------------
# Component 3a continued -- Q4 unit (3)'s new "links" reference sites:
# route link sequence, priority rule / conflict marker intLink form,
# conflict area linkA/linkB. Each live-verified relevant by
# backend/tools/probe_link_closure_turn_bans.py before being added (see
# ID_REFERENCE_SITES["links"]'s own comment).
# ---------------------------------------------------------------------------


def test_find_id_references_finds_route_link_sequence_reference():
    root = ET.fromstring(
        '<network><links><link no="1"/><link no="9001"/><link no="2"/></links>'
        '<vehicleRoutingDecisionStatic no="1" link="1" name="">'
        '<vehicleRouteStatic no="1" destLink="2" destPos="10">'
        '<linkSeq><intObjectRef key="1"/><intObjectRef key="9001"/><intObjectRef key="2"/></linkSeq>'
        '</vehicleRouteStatic></vehicleRoutingDecisionStatic></network>'
    )
    referrers = struct.find_id_references(root, "links", "9001")
    assert len(referrers) == 1
    assert referrers[0]["site"] == "route link sequence"
    assert referrers[0]["tag"] == "intObjectRef"
    # A link NOT named in the sequence is genuinely unreferenced.
    assert struct.find_id_references(root, "links", "999") == []


def test_find_id_references_finds_priority_rule_intlink_reference():
    root = ET.fromstring(
        '<network><links><link no="613"/></links>'
        '<priorityRule no="143" intLink="613"><conflictMarker intLink="613"/></priorityRule>'
        '</network>'
    )
    referrers = struct.find_id_references(root, "links", "613")
    sites = sorted(r["site"] for r in referrers)
    assert sites == ["priority rule (intLink)", "priority rule conflict marker (intLink)"]


def test_find_id_references_finds_conflict_area_link_a_and_b():
    root = ET.fromstring(
        '<network><links><link no="4"/><link no="10002"/></links>'
        '<conflictAreas><conflictArea no="17" linkA="4" linkB="10002" status="PASSIVE"/></conflictAreas>'
        '</network>'
    )
    referrers_a = struct.find_id_references(root, "links", "4")
    referrers_b = struct.find_id_references(root, "links", "10002")
    assert len(referrers_a) == 1 and referrers_a[0]["site"] == "conflict area link A"
    assert len(referrers_b) == 1 and referrers_b[0]["site"] == "conflict area link B"


# ---------------------------------------------------------------------------
# Component 3b: reference integrity -- lane tokens
# ---------------------------------------------------------------------------


def test_find_lane_token_references_finds_connector_lane_reference():
    root = ET.parse(PRACTICE_INPX).getroot()
    referrers = struct.find_lane_token_references(root, "101", 1)
    assert len(referrers) == 1
    assert referrers[0]["site"] == "connector fromLinkEndPt (lane form)"


def test_find_lane_token_references_empty_for_unreferenced_lane():
    root = ET.parse(PRACTICE_INPX).getroot()
    # Link 101 has 3 lanes; only lane 1 is referenced (see the test above).
    assert struct.find_lane_token_references(root, "101", 2) == []
    assert struct.find_lane_token_references(root, "101", 3) == []


def test_find_lane_token_references_matches_nested_connector_objectref():
    # The REAL-Vissim-export shape (verified live against Urban Intersection
    # Beijing.CN, see the module docstring) -- a connector's own <lane> child
    # carries <fromLanes>/<toLanes><objectRef key="<link> <lane>"/>, distinct
    # from practice.inpx's simplified fromLinkEndPt-lane-attribute form.
    root = ET.fromstring(
        '<network><links>'
        '<link no="9"><lanes><lane/><lane/></lanes></link>'
        '<link no="14"><lanes><lane/></lanes></link>'
        '<link no="61"><fromLinkEndPt link="9" pos="1"/>'
        '<lanes><lane><fromLanes><objectRef key="9 2"/></fromLanes>'
        '<toLanes><objectRef key="14 1"/></toLanes></lane></lanes>'
        '<toLinkEndPt link="14" pos="0"/></link>'
        '</links></network>'
    )
    referrers = struct.find_lane_token_references(root, "9", 2)
    assert len(referrers) == 1
    assert referrers[0]["site"] == "connector fromLanes"
    referrers_to = struct.find_lane_token_references(root, "14", 1)
    assert len(referrers_to) == 1
    assert referrers_to[0]["site"] == "connector toLanes"


# ---------------------------------------------------------------------------
# Component 3c: reference integrity -- dangling references on ADD
# ---------------------------------------------------------------------------


def test_check_new_object_references_detects_dangling_reference():
    root = ET.fromstring(
        '<network><links>'
        '<link no="1"/>'
        '<link no="2"><fromLinkEndPt link="999" pos="0"/></link>'
        '</links></network>'
    )
    dangling = struct.check_new_object_references(
        root, referring_xpath=".//links/link/fromLinkEndPt", ref_attr="link", target_object_class="links"
    )
    assert dangling == [{"tag": "fromLinkEndPt", "attribute": "link", "value": "999"}]


def test_data_collection_points_is_id_bearing():
    """Q4 unit (2): the registry entry ADDs get validated against
    (check_new_object_references' target side)."""
    root = ET.fromstring(
        '<network><dataCollectionPoints>'
        '<dataCollectionPoint no="1" lane="10005 1" pos="3.836" />'
        '<dataCollectionPoint no="2" lane="10005 2" pos="3.836" />'
        '</dataCollectionPoints></network>'
    )
    xpath, id_attr = struct.ID_BEARING_ELEMENTS["data_collection_points"]
    ids = {el.get(id_attr) for el in root.findall(xpath)}
    assert ids == {"1", "2"}


def test_find_id_references_finds_measurement_referencing_data_collection_point():
    """Q4 unit (2): a dataCollectionMeasurement groups points via
    <dataCollectionPoints><intObjectRef key="<point_no>"/></dataCollectionPoints>
    -- the same convention _parse_link_lanes already found for
    blockedVehClasses (live-verified, backend/tools/probe_measurement_infra.py,
    Toll Plaza Camden.US measurement no=1 groups points 1/2/3)."""
    root = ET.fromstring(
        '<network><dataCollectionMeasurements>'
        '<dataCollectionMeasurement no="1" name="">'
        '<dataCollectionPoints><intObjectRef key="1"/><intObjectRef key="2"/></dataCollectionPoints>'
        '</dataCollectionMeasurement>'
        '</dataCollectionMeasurements></network>'
    )
    referrers = struct.find_id_references(root, "data_collection_points", "1")
    assert len(referrers) == 1
    assert referrers[0]["site"] == "data-collection measurement point ref"
    assert referrers[0]["tag"] == "intObjectRef"
    assert referrers[0]["value"] == "1"

    referrers_2 = struct.find_id_references(root, "data_collection_points", "2")
    assert len(referrers_2) == 1


def test_find_id_references_empty_for_unreferenced_data_collection_point():
    root = ET.fromstring(
        '<network><dataCollectionMeasurements>'
        '<dataCollectionMeasurement no="1" name="">'
        '<dataCollectionPoints><intObjectRef key="1"/></dataCollectionPoints>'
        '</dataCollectionMeasurement>'
        '</dataCollectionMeasurements></network>'
    )
    assert struct.find_id_references(root, "data_collection_points", "999") == []


def test_find_id_references_data_collection_point_referenced_by_two_measurements():
    """A point can legitimately belong to more than one measurement group --
    removal must name EVERY referrer, not just the first."""
    root = ET.fromstring(
        '<network><dataCollectionMeasurements>'
        '<dataCollectionMeasurement no="1" name="">'
        '<dataCollectionPoints><intObjectRef key="5"/></dataCollectionPoints>'
        '</dataCollectionMeasurement>'
        '<dataCollectionMeasurement no="2" name="">'
        '<dataCollectionPoints><intObjectRef key="5"/></dataCollectionPoints>'
        '</dataCollectionMeasurement>'
        '</dataCollectionMeasurements></network>'
    )
    referrers = struct.find_id_references(root, "data_collection_points", "5")
    assert len(referrers) == 2


def test_check_new_object_references_clean_when_target_exists():
    root = ET.fromstring(
        '<network><links>'
        '<link no="1"/>'
        '<link no="2"><fromLinkEndPt link="1" pos="0"/></link>'
        '</links></network>'
    )
    dangling = struct.check_new_object_references(
        root, referring_xpath=".//links/link/fromLinkEndPt", ref_attr="link", target_object_class="links"
    )
    assert dangling == []


# ---------------------------------------------------------------------------
# The proof pair: remove_last_lane / add_lane (no live COM -- run_load_
# check=False; the load oracle itself is proven live separately)
# ---------------------------------------------------------------------------

_UNREFERENCED_TWO_LANE_LINK = (
    '<link no="1" name="A"><geometry><linkPolyPts>'
    '<linkPolyPoint x="0" y="0" zOffset="0"/><linkPolyPoint x="100" y="0" zOffset="0"/>'
    '</linkPolyPts></geometry><lanes><lane width="3.5"/><lane width="3.66"/></lanes></link>'
)

_REFERENCED_LAST_LANE_FIXTURE = (
    '<link no="1" name="A"><geometry><linkPolyPts>'
    '<linkPolyPoint x="0" y="0" zOffset="0"/><linkPolyPoint x="100" y="0" zOffset="0"/>'
    '</linkPolyPts></geometry><lanes><lane width="3.5"/><lane width="3.5"/></lanes></link>'
    '<link no="2" name="connector"><fromLinkEndPt link="1" pos="10"/>'
    '<geometry><linkPolyPts><linkPolyPoint x="100" y="0" zOffset="0"/>'
    '<linkPolyPoint x="120" y="0" zOffset="0"/></linkPolyPts></geometry>'
    '<lanes><lane><fromLanes><objectRef key="1 2"/></fromLanes></lane></lanes>'
    '<toLinkEndPt link="3" pos="0"/></link>'
    '<link no="3" name="B"><geometry><linkPolyPts>'
    '<linkPolyPoint x="120" y="0" zOffset="0"/><linkPolyPoint x="200" y="0" zOffset="0"/>'
    '</linkPolyPts></geometry><lanes><lane width="3.5"/></lanes></link>'
)


def test_remove_last_lane_happy_path(tmp_path: Path):
    path = _fixture_with_links(tmp_path, _UNREFERENCED_TWO_LANE_LINK)
    result = struct.remove_last_lane(path, "1", run_load_check=False)

    assert result["metadata"] == {"link_no": "1", "removed_lane_index": 2, "removed_width": 3.66}
    assert result["load_check_result"] is None
    target_link = next(l for l in result["shadow_model"]["links"] if l["id"] == "1")
    assert target_link["lanes"] == 1

    # Byte-level: reparsing straight from disk (not the in-memory tree)
    # shows the same thing -- the save genuinely reached the file.
    fresh = struct.verify_reparse(path)
    fresh_link = next(l for l in fresh["links"] if l["id"] == "1")
    assert fresh_link["lanes"] == 1


def test_remove_last_lane_refuses_when_last_lane_is_referenced(tmp_path: Path):
    path = _fixture_with_links(tmp_path, _REFERENCED_LAST_LANE_FIXTURE)
    original_bytes = path.read_bytes()

    with pytest.raises(struct.ReferenceIntegrityError) as excinfo:
        struct.remove_last_lane(path, "1", run_load_check=False)

    assert excinfo.value.referrers[0]["tag"] == "objectRef"
    assert excinfo.value.referrers[0]["value"] == "1 2"
    # Refused BEFORE any write -- the file is untouched.
    assert path.read_bytes() == original_bytes


def test_remove_last_lane_refuses_on_a_links_last_remaining_lane(tmp_path: Path):
    path = _fixture_with_links(
        tmp_path,
        '<link no="1" name="A"><geometry><linkPolyPts>'
        '<linkPolyPoint x="0" y="0" zOffset="0"/><linkPolyPoint x="100" y="0" zOffset="0"/>'
        '</linkPolyPts></geometry><lanes><lane width="3.5"/></lanes></link>',
    )
    with pytest.raises(ValueError, match="last remaining lane"):
        struct.remove_last_lane(path, "1", run_load_check=False)


def test_remove_last_lane_raises_for_unknown_link(tmp_path: Path):
    path = _fixture_with_links(tmp_path, _UNREFERENCED_TWO_LANE_LINK)
    with pytest.raises(ValueError, match="not found"):
        struct.remove_last_lane(path, "999", run_load_check=False)


def test_add_lane_happy_path(tmp_path: Path):
    path = _fixture_with_links(tmp_path, _UNREFERENCED_TWO_LANE_LINK)
    result = struct.add_lane(path, "1", width=3.25, run_load_check=False)

    assert result["metadata"] == {"link_no": "1", "added_lane_index": 3, "width": 3.25}
    target_link = next(l for l in result["shadow_model"]["links"] if l["id"] == "1")
    assert target_link["lanes"] == 3
    assert target_link["lane_widths"][-1] == pytest.approx(3.25)


def test_remove_then_add_lane_round_trips_lane_count(tmp_path: Path):
    """The reopen half: remove the last lane, then add one back -- lane
    COUNT is restored (byte-for-byte restoration of the exact removed lane
    is rollback_last_edit's job, proven in test_agent_rollback_structural.py;
    this test only proves add_lane's own reparse-verified contract)."""
    path = _fixture_with_links(tmp_path, _UNREFERENCED_TWO_LANE_LINK)
    before = struct.verify_reparse(path)
    before_count = next(l for l in before["links"] if l["id"] == "1")["lanes"]

    struct.remove_last_lane(path, "1", run_load_check=False)
    struct.add_lane(path, "1", width=3.5, run_load_check=False)

    after = struct.verify_reparse(path)
    after_count = next(l for l in after["links"] if l["id"] == "1")["lanes"]
    assert after_count == before_count


# ---------------------------------------------------------------------------
# apply_structural_edit's own COM-load-oracle rollback contract, exercised
# with a fake oracle (no real COM) -- proves the rollback-on-load-failure
# path byte-for-byte without spending a Vissim licence seat.
# ---------------------------------------------------------------------------


def test_apply_structural_edit_reverts_bytes_when_load_oracle_fails(tmp_path: Path, monkeypatch):
    path = _fixture_with_links(tmp_path, _UNREFERENCED_TWO_LANE_LINK)
    original_bytes = path.read_bytes()

    monkeypatch.setattr(
        struct,
        "run_load_oracle",
        lambda p: {"load_ok": True, "load_error": None, "init_ok": False, "init_error": "boom", "err_content": None},
    )

    def _mutate(root: ET.Element):
        link = struct._find_link(root, "1")
        link.find("lanes").findall("lane")[-1].attrib["width"] = "99"
        return {}

    with pytest.raises(struct.StructuralEditRolledBackError) as excinfo:
        struct.apply_structural_edit(path, _mutate, run_load_check=True)

    assert excinfo.value.load_check_result["init_error"] == "boom"
    # The working copy is back to its EXACT pre-mutation bytes.
    assert path.read_bytes() == original_bytes


def test_apply_structural_edit_keeps_edit_when_load_oracle_passes(tmp_path: Path, monkeypatch):
    path = _fixture_with_links(tmp_path, _UNREFERENCED_TWO_LANE_LINK)

    monkeypatch.setattr(
        struct,
        "run_load_oracle",
        lambda p: {"load_ok": True, "load_error": None, "init_ok": True, "init_error": None, "err_content": None},
    )

    def _mutate(root: ET.Element):
        link = struct._find_link(root, "1")
        link.find("lanes").findall("lane")[-1].attrib["width"] = "9.0"
        return {"ok": True}

    result = struct.apply_structural_edit(path, _mutate, run_load_check=True)
    assert result["metadata"] == {"ok": True}
    assert result["load_check_result"]["init_ok"] is True
    fresh = struct.verify_reparse(path)
    target = next(l for l in fresh["links"] if l["id"] == "1")
    assert target["lane_widths"][-1] == pytest.approx(9.0)


# ---------------------------------------------------------------------------
# Q4 unit (3): remove_connector -- the true structural "movement removal"
# verb. No live COM anywhere in this section (run_load_check=False); the
# load oracle itself is proven live separately
# (backend/tools/probe_link_closure_turn_bans.py [1c] + the unit's own live
# transcript).
# ---------------------------------------------------------------------------


def test_remove_connector_happy_path_against_real_practice_inpx(tmp_path: Path):
    """Static XML test against the REAL data/practice.inpx (a copy, never
    the original -- see _copy_practice) rather than a synthetic fixture:
    connectors 301/302 are both genuinely unreferenced in the bundled demo
    network (verified by find_id_references/find_lane_token_references
    returning empty for both, every lane)."""
    path = _copy_practice(tmp_path)
    before = struct.verify_reparse(path)
    before_link_ids = {l["id"] for l in before["links"]}
    assert "301" in before_link_ids

    result = struct.remove_connector(path, "301", run_load_check=False)

    assert result["metadata"]["connector_no"] == "301"
    after_link_ids = {l["id"] for l in result["shadow_model"]["links"]}
    assert "301" not in after_link_ids
    assert "302" in after_link_ids  # the OTHER connector is untouched

    fresh = struct.verify_reparse(path)
    assert "301" not in {l["id"] for l in fresh["links"]}


_CONNECTOR_NETWORK_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<network>
<links>{links}</links>
{extra}
</network>
"""

#: link 1 -- connector 9001 -- link 2, a plain 2-link/1-connector chain.
#: Connector 9001 itself is UNREFERENCED by anything in this base set.
_BASE_CONNECTOR_LINKS = (
    '<link no="1" name="A"><geometry><linkPolyPts>'
    '<linkPolyPoint x="0" y="0" zOffset="0"/><linkPolyPoint x="100" y="0" zOffset="0"/>'
    '</linkPolyPts></geometry><lanes><lane width="3.5"/></lanes></link>'
    '<link no="9001" name="connector"><fromLinkEndPt link="1" pos="100"/>'
    '<toLinkEndPt link="2" pos="0"/>'
    '<geometry><linkPolyPts><linkPolyPoint x="100" y="0" zOffset="0"/>'
    '<linkPolyPoint x="120" y="0" zOffset="0"/></linkPolyPts></geometry>'
    '<lanes><lane width="3.5"/></lanes></link>'
    '<link no="2" name="B"><geometry><linkPolyPts>'
    '<linkPolyPoint x="120" y="0" zOffset="0"/><linkPolyPoint x="220" y="0" zOffset="0"/>'
    '</linkPolyPts></geometry><lanes><lane width="3.5"/></lanes></link>'
)


def _write_connector_network(
    tmp_path: Path, links_inner_xml: str = _BASE_CONNECTOR_LINKS, extra: str = "", name: str = "fixture.inpx"
) -> Path:
    path = tmp_path / name
    path.write_text(
        _CONNECTOR_NETWORK_TEMPLATE.format(links=links_inner_xml, extra=extra), encoding="utf-8"
    )
    return path


def test_remove_connector_happy_path(tmp_path: Path):
    path = _write_connector_network(tmp_path)
    result = struct.remove_connector(path, "9001", run_load_check=False)

    assert result["metadata"] == {"connector_no": "9001", "removed_lane_count": 1}
    assert result["load_check_result"] is None
    assert not any(l["id"] == "9001" for l in result["shadow_model"]["links"])

    # Byte-level: reparsing straight from disk (not the in-memory tree)
    # confirms the removal genuinely reached the file, and the OTHER two
    # links survived untouched.
    fresh = struct.verify_reparse(path)
    ids = {l["id"] for l in fresh["links"]}
    assert ids == {"1", "2"}


def test_remove_connector_raises_for_unknown_connector(tmp_path: Path):
    path = _write_connector_network(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        struct.remove_connector(path, "99999", run_load_check=False)


def test_remove_connector_raises_for_a_plain_link(tmp_path: Path):
    path = _write_connector_network(tmp_path)
    with pytest.raises(ValueError, match="not a connector"):
        struct.remove_connector(path, "1", run_load_check=False)


def test_remove_connector_refuses_when_referenced_by_a_route(tmp_path: Path):
    path = _write_connector_network(
        tmp_path,
        extra=(
            '<vehicleRoutingDecisionStatic no="1" link="1" name="">'
            '<vehicleRouteStatic no="1" destLink="2" destPos="10">'
            '<linkSeq><intObjectRef key="1"/><intObjectRef key="9001"/>'
            '<intObjectRef key="2"/></linkSeq>'
            '</vehicleRouteStatic></vehicleRoutingDecisionStatic>'
        ),
    )
    original_bytes = path.read_bytes()

    with pytest.raises(struct.ReferenceIntegrityError) as excinfo:
        struct.remove_connector(path, "9001", run_load_check=False)

    assert excinfo.value.referrers[0]["site"] == "route link sequence"
    # Refused BEFORE any write -- the file is untouched.
    assert path.read_bytes() == original_bytes


def test_remove_connector_refuses_when_referenced_by_a_conflict_area(tmp_path: Path):
    path = _write_connector_network(
        tmp_path,
        extra='<conflictAreas><conflictArea no="17" linkA="1" linkB="9001" status="PASSIVE"/></conflictAreas>',
    )
    with pytest.raises(struct.ReferenceIntegrityError) as excinfo:
        struct.remove_connector(path, "9001", run_load_check=False)
    assert excinfo.value.referrers[0]["site"] == "conflict area link B"


def test_remove_connector_refuses_when_referenced_by_a_priority_rule(tmp_path: Path):
    path = _write_connector_network(
        tmp_path, extra='<priorityRule no="1" intLink="9001"/>'
    )
    with pytest.raises(struct.ReferenceIntegrityError) as excinfo:
        struct.remove_connector(path, "9001", run_load_check=False)
    assert excinfo.value.referrers[0]["site"] == "priority rule (intLink)"


def test_remove_connector_refuses_when_chained_from_another_connector(tmp_path: Path):
    # Connector 9002 attaches its OWN fromLinkEndPt to connector 9001 --
    # removing 9001 would dangle 9002's endpoint.
    links = _BASE_CONNECTOR_LINKS + (
        '<link no="9002" name="chain"><fromLinkEndPt link="9001" pos="5"/>'
        '<toLinkEndPt link="2" pos="0"/>'
        '<geometry><linkPolyPts><linkPolyPoint x="105" y="0" zOffset="0"/>'
        '<linkPolyPoint x="120" y="5" zOffset="0"/></linkPolyPts></geometry>'
        '<lanes><lane width="3.5"/></lanes></link>'
    )
    path = _write_connector_network(tmp_path, links_inner_xml=links)
    with pytest.raises(struct.ReferenceIntegrityError) as excinfo:
        struct.remove_connector(path, "9001", run_load_check=False)
    assert excinfo.value.referrers[0]["tag"] == "fromLinkEndPt"


def test_remove_connector_refuses_when_a_lane_is_referenced_by_fromlanes(tmp_path: Path):
    # A DIFFERENT connector's own <lane><fromLanes><objectRef key="9001 1"/>
    # references connector 9001's lane 1 directly (the real-Vissim-export
    # shape, not the fromLinkEndPt lane-attribute form) -- attached from link
    # 2 (not 9001 itself) so this test isolates the LANE-token check from the
    # id-level fromLinkEndPt check already covered by the chain test above.
    links = _BASE_CONNECTOR_LINKS + (
        '<link no="9003" name="lane-ref"><fromLinkEndPt link="2" pos="0"/>'
        '<toLinkEndPt link="1" pos="0"/>'
        '<geometry><linkPolyPts><linkPolyPoint x="105" y="0" zOffset="0"/>'
        '<linkPolyPoint x="120" y="5" zOffset="0"/></linkPolyPts></geometry>'
        '<lanes><lane><fromLanes><objectRef key="9001 1"/></fromLanes></lane></lanes></link>'
    )
    path = _write_connector_network(tmp_path, links_inner_xml=links)
    with pytest.raises(struct.ReferenceIntegrityError) as excinfo:
        struct.remove_connector(path, "9001", run_load_check=False)
    sites = [r["site"] for r in excinfo.value.referrers]
    assert "connector fromLanes" in sites

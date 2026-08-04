"""Tests for M1.2's read tools: tools_network.py and tools_metrics.py.

These are the first real tools registered through the M0.4 registry
(services.agent.toolkit) — importing either module registers its tools onto
``default_registry`` at import time (see toolkit.py's module docstring), so
this file also carries the anti-drift guard extending M0.4's registry test to
real tools (docs/phase2/02-ROADMAP.md M1.2).
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

import services.agent.tools_check as tools_check  # noqa: F401 - import registers tools
import services.agent.tools_edit as tools_edit  # noqa: F401 - import registers tools
import services.agent.tools_metrics as tools_metrics  # noqa: F401 - import registers tools
import services.agent.tools_network as tools_network  # noqa: F401 - import registers tools
import services.agent.tools_run as tools_run  # noqa: F401 - import registers tools
import services.agent.tools_sweep as tools_sweep  # noqa: F401 - import registers tools
from services.agent.toolkit import default_registry, dispatch, render_contract

FIXTURES_DIR = Path(__file__).parent / "fixtures"
KOREAN_FIXTURE_INPX = FIXTURES_DIR / "korean_object_names.inpx"

# The active real client model, used ONLY by the sibling-disambiguation
# real-dataset regression test (FIX 1, 2026-07-28) -- everything else here is
# synthetic by design. Same skipif convention as test_agent_constraints.py /
# test_network_search.py.
ACTIVE_REAL_INPX = Path(__file__).resolve().parents[2] / "runtime" / "uploads" / "current" / "current.inpx"
_requires_real_dataset = pytest.mark.skipif(
    not ACTIVE_REAL_INPX.is_file(),
    reason=f"real active dataset not present on this machine: {ACTIVE_REAL_INPX}",
)

EXPECTED_TOOL_NAMES = {
    "get_network_overview",
    "resolve_object",
    "get_vehicle_inputs",
    "get_signal_controllers",
    "get_conflict_areas",
    "get_routing_decisions",
    "get_vehicle_compositions",
    # v2a unit ⑧ adds get_des_speed_decisions + get_des_speed_distributions.
    "get_des_speed_decisions",
    "get_des_speed_distributions",
    # v2a unit ⑨ adds get_signal_programs.
    "get_signal_programs",
    # QUEUE 3 unit 2 adds get_priority_rules.
    "get_priority_rules",
    # QUEUE (v2a) unit adds get_vehicle_types.
    "get_vehicle_types",
    # QUEUE (Q3, v2a) unit 5 adds get_time_distributions.
    "get_time_distributions",
    # QUEUE 3 unit 6 adds get_driving_behaviors.
    "get_driving_behaviors",
    "get_run_kpis",
    "compare_scenarios",
    # Q4 unit (7) adds get_pt_stops + get_pt_lines.
    "get_pt_stops",
    "get_pt_lines",
    # Q6 unit (2) adds get_parking_lots -- the paired reader for the three
    # parking write verbs below, and the first read tool over an object class
    # this codebase had NO representation of at all before that unit.
    "get_parking_lots",
    # Q6 unit (3): pedestrians (Viswalk).
    "get_pedestrian_inputs",
    "get_pedestrian_areas",
}

# M2.3's tools_edit.py registers these write-tier tools, and M2.4's
# tools_run.py registers the one run-tier tool below, onto the SAME
# default_registry, so the anti-drift test has to know about them too, or
# it would (correctly, but confusingly) fail on every run. Both modules are
# imported directly at the top of this file: an earlier revision relied on
# pytest's collection phase importing other test modules (which import
# services.agent.orchestrator, which imports tools_edit/tools_run for their
# registration side effect) -- that held for full-suite runs but made the
# three registry-shape tests fail whenever this file ran in isolation.
# Q5 unit (2) found that regression had come BACK, unnoticed since v2 unit
# (3): load_check was added to EXPECTED_RUN_TOOL_NAMES below, but its module
# (tools_check.py) was never added to the imports above -- so the pin was
# satisfied only by test_agent_orchestrator.py happening to import
# orchestrator.py (which imports tools_check) earlier in the same session.
# `pytest tests/test_agent_read_tools.py` alone failed 2 tests. The rule the
# paragraph above states is only as good as its last line: every module named
# in the expected sets below needs its own import here.
# v2 unit (2) adds set_reduced_speed_area (XML-injection, not COM), the sixth.
# v2 unit (4) adds set_conflict_area_status, the seventh.
# v2 unit (6) adds set_route_relative_flow, the eighth.
# v2 unit (7) adds set_vehicle_composition_share, the ninth.
# v2a unit ⑧ adds set_des_speed_decision, the tenth.
# v2a unit ⑨ adds set_signal_green_split, the eleventh.
# QUEUE 3 unit 2 adds set_priority_rule_gap_headway, the twelfth.
EXPECTED_WRITE_TOOL_NAMES = {
    "set_vehicle_input_volume",
    "set_signal_cycle",
    "set_signal_offset",
    "set_sim_duration",
    "set_random_seed",
    "set_reduced_speed_area",
    "set_conflict_area_status",
    "set_route_relative_flow",
    "set_vehicle_composition_share",
    "set_des_speed_decision",
    "set_signal_green_split",
    "set_priority_rule_gap_headway",
    "set_des_speed_distribution_points",
    "set_vehicle_type_reference",
    # QUEUE (Q3, v2a) unit 5
    "set_time_distribution_normal",
    "set_time_distribution_points",
    # QUEUE 3 unit 6: driving-behavior duplicate-then-edit, all three verbs.
    "duplicate_driving_behavior",
    "set_driving_behavior_parameter",
    "set_link_behavior_type_driving_behavior",
    # QUEUE 3 unit 7 adds rollback_last_edit (no COM).
    "rollback_last_edit",
    # QUEUE 3 unit 8 adds apply_edit_plan (batch/compound edits, no COM of
    # its own -- each step dispatches through its own already-registered
    # tool).
    "apply_edit_plan",
    # v2b unit (1) adds set_lane_closure (one setter, both closure/reopen
    # directions -- see tools_edit.py's own docstring).
    "set_lane_closure",
    # Q4 unit (2) adds 8 measurement-infrastructure placement verbs (queue
    # counters, data-collection points/measurements, travel-time sections --
    # COM CREATE/REMOVE, see tools_edit.py's own docstring).
    "add_queue_counter",
    "remove_queue_counter",
    "add_data_collection_point",
    "remove_data_collection_point",
    "add_data_collection_measurement",
    "remove_data_collection_measurement",
    "add_travel_time_section",
    "remove_travel_time_section",
    # Q4 unit (3): full link closure + turn bans (movement removal).
    "set_link_closure",
    "set_turn_ban",
    "remove_connector",
    # Q4 unit (4) adds 4 signal-head + detector placement verbs (COM
    # CREATE/REMOVE, see tools_edit.py's own docstring).
    "add_signal_head",
    "remove_signal_head",
    "add_detector",
    "remove_detector",
    # Q4 unit (5) adds 3 add/remove links & connectors verbs (COM
    # CREATE/REMOVE, see tools_edit.py's own docstring) -- remove_connector
    # already existed (Q4 unit (3)), not duplicated.
    "add_link",
    "remove_link",
    "add_connector",
    # Q4 unit (6) adds 4 stop-sign + priority-rule placement verbs (COM
    # CREATE/REMOVE, see tools_edit.py's own docstring) -- no verb for
    # conflict areas (confirmed non-creatable via COM).
    "add_stop_sign",
    "remove_stop_sign",
    "add_priority_rule",
    "remove_priority_rule",
    # Q4 unit (7) adds 5 PT stop + PT line placement verbs (COM CREATE/
    # REMOVE, see tools_edit.py's own docstring) -- add_pt_stop/remove_pt_
    # stop, add_pt_line/remove_pt_line, set_pt_line_stop_dwell (the BusDwell
    # story's missing verb, Q3 unit 5).
    "add_pt_stop",
    "remove_pt_stop",
    "add_pt_line",
    "remove_pt_line",
    "set_pt_line_stop_dwell",
    # Q5 unit (2) adds add_signal_controller -- signal-controller genesis via
    # duplicate-then-edit (COM DuplicateSignalController; plain
    # AddSignalController creates a non-functional skeleton and is
    # deliberately NOT wrapped, see tools_edit.py's own comment block). No
    # remove_signal_controller: RemoveSignalController exists and works
    # pre-run, but nothing in this unit needs it and a remove verb with no
    # reference-integrity story (signal heads, detectors and stop signs all
    # point at a controller) is exactly the kind of silent cascade this
    # layer exists to prevent -- a deliberate, recorded scope cut.
    "add_signal_controller",
    # Q5 unit (3) adds add_vehicle_input -- vehicle-input genesis, the LAST
    # item on the GENESIS GAP LIST, and the first creation verb here where
    # plain COM Add is the real path (AddPriorityRule was a silent no-op,
    # AddSignalController a non-functional skeleton) AND the first with no
    # zero-object family limitation at all: a network with zero vehicle
    # inputs genuinely can mint its first. No remove_vehicle_input:
    # RemoveVehicleInput exists, but nothing in this unit needs it and a
    # remove verb whose object is what makes a network carry traffic at all
    # deserves its own reference-integrity story -- a deliberate, recorded
    # scope cut, mirroring remove_signal_controller's.
    "add_vehicle_input",
    # Q6 unit (1) adds three link-geometry edit verbs for an EXISTING link:
    # set_link_lane_count (COM AddLane/RemoveLane -- a link's own NumLanes
    # attribute is read-only, measured), set_link_geometry (moves the polyline
    # points; the point COUNT cannot change, because COM exposes no way to add
    # or remove one), and set_link_point_radius (the per-point curve-radius
    # override, a two-step write because Vissim refuses the value while the
    # source is CALCULATED). No verb wraps RecalculateSpline/SplitLink/
    # InvertDirection/GenerateOppositeDirection/DuplicateLink: they exist on
    # the same interface but were not probed by that unit -- a deliberate,
    # recorded scope cut.
    "set_link_lane_count",
    "set_link_geometry",
    "set_link_point_radius",
    # Q6 unit (2) adds three parking-lot verbs: add_parking_lot (native COM
    # AddParkingLot -- a genuine create, NOT duplicate-then-edit),
    # set_parking_lot_capacity (which writes the lot's LENGTH, because
    # Vissim's own Capacity attribute is read-only and derived from
    # length/len_per_space -- measured), and remove_parking_lot (which
    # refuses when a parking route still references the lot, because COM's
    # own removal silently cascade-deletes that route). No verb wraps
    # DuplicateParkingLot, AddAbstractParkingLot, Zones.AddZone,
    # ParkLotGrps.*, or the parking ROUTING-decision containers: each exists
    # on the same interfaces and each is a deliberate, recorded scope cut --
    # see vissim_com.py's own parking-lot module comment.
    "add_parking_lot",
    "set_parking_lot_capacity",
    "remove_parking_lot",
    # Q6 unit (3): pedestrians (Viswalk).
    "add_pedestrian_input",
    "set_pedestrian_input_volume",
    "remove_pedestrian_input",
}

# M2.4's run-gate tool, plus v2 unit (3)'s load_check (docs/phase2/02-ROADMAP.md
# M2.4; docs/phase2/DECISIONS.md's v2 unit (3) entry), plus Phase 0.1's
# run_demand_stress_sweep (master-roadmap-2026-07.md S0.1, task #33). All
# three cost a real Vissim instance so all three are tier="run", but they are
# NOT all the same provider — see
# test_run_tools_are_tier_run_with_their_own_providers below.
EXPECTED_RUN_TOOL_NAMES = {
    "run_simulation",
    "load_check",
    "run_demand_stress_sweep",
}

# v2 unit (4) adds set_conflict_area_status: the seventh write-tier tool, and
# get_conflict_areas above: the seventh read-tier tool.


@pytest.fixture(autouse=True)
def _reset_active_inpx():
    """Every test starts from the real demo default and restores it after,
    so tests can't leak an override into one another regardless of order."""
    tools_network.set_active_inpx(None)
    yield
    tools_network.set_active_inpx(None)


def _dispatch(name: str, **params: Any) -> dict[str, Any]:
    return dispatch(name, params, registry=default_registry)


# ---------------------------------------------------------------------------
# Anti-drift: registry contains exactly these 6 tools, no phantoms
# ---------------------------------------------------------------------------


def test_default_registry_has_exactly_the_expected_read_write_and_run_tools():
    """M1.2's 6 read tools + M2.3/v2 unit (2)'s 6 write tools + M2.4's 1 run
    tool, no phantoms, nothing missing. Was "...exactly_the_six_read_tools"
    before M2.3 registered tools_edit.py's write tools, then
    "...the_expected_read_and_write_tools" before M2.4 registered
    tools_run.py's run tool, onto this same default_registry — renamed and
    widened each time rather than split, since a second anti-drift test over
    the same registry would just duplicate this one's headings/duplicate-
    check logic for no real isolation (all three modules' tools share one
    registry by design)."""
    contract = render_contract(registry=default_registry)
    headings = re.findall(r"^## (\S+)$", contract, flags=re.MULTILINE)

    assert len(headings) == len(set(headings))  # no duplicates
    assert set(headings) == EXPECTED_TOOL_NAMES | EXPECTED_WRITE_TOOL_NAMES | EXPECTED_RUN_TOOL_NAMES


def test_all_read_tools_are_tier_read():
    for name in EXPECTED_TOOL_NAMES:
        spec = default_registry.get(name)
        assert spec is not None, f"{name} not registered"
        assert spec.tier == "read"


def test_all_write_tools_are_tier_write():
    for name in EXPECTED_WRITE_TOOL_NAMES:
        spec = default_registry.get(name)
        assert spec is not None, f"{name} not registered"
        assert spec.tier == "write"


def test_run_tools_are_tier_run_with_their_own_providers():
    """Both run-tier tools cost a real Vissim instance, so both are
    tier="run" — but provider stays a per-MODULE label (same convention as
    tools_network's "inspect" vs tools_metrics' "metrics" below), and
    load_check is deliberately its OWN module (tools_check.py), not folded
    into tools_run.py, so it gets its own provider rather than inheriting
    run_simulation's "run"."""
    for name in EXPECTED_RUN_TOOL_NAMES:
        spec = default_registry.get(name)
        assert spec is not None, f"{name} not registered"
        assert spec.tier == "run"

    assert default_registry.get("run_simulation").provider == "run"
    assert default_registry.get("load_check").provider == "check"


def test_network_tools_use_inspect_provider_and_metrics_tools_use_metrics_provider():
    for name in (
        "get_network_overview",
        "resolve_object",
        "get_vehicle_inputs",
        "get_signal_controllers",
        "get_conflict_areas",
        "get_routing_decisions",
    ):
        assert default_registry.get(name).provider == "inspect"
    for name in ("get_run_kpis", "compare_scenarios"):
        assert default_registry.get(name).provider == "metrics"


# ---------------------------------------------------------------------------
# get_network_overview — real demo data (data/practice.inpx)
# ---------------------------------------------------------------------------


def test_get_network_overview_returns_counts_and_compact_lists_for_demo_data():
    envelope = _dispatch("get_network_overview")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    for key in ("links", "nodes", "vehicle_inputs", "signals", "conflict_areas", "routing_decisions"):
        assert key in data
        section = data[key]
        assert set(section.keys()) == {"count", "items", "truncated"}
        assert section["count"] == len(section["items"]) or section["truncated"]
        for item in section["items"]:
            assert set(item.keys()) == {"id", "name"}  # compact: no geometry/points/etc.

    # demo dataset facts (data/practice.inpx): 16 links (14 real + 2 Phase 3
    # connectors), 2 nodes, 6 vehicle inputs, 0 working signal controllers (no
    # intSupplyData in the demo file), 0 conflict areas (the demo .inpx carries
    # no <conflictArea> elements at all -- v2 unit ④), 2 static routing
    # decisions (v2 unit ⑥ -- unlike signals/conflict_areas, the demo .inpx
    # DOES carry these)
    assert data["links"]["count"] == 16
    assert data["nodes"]["count"] == 2
    assert data["vehicle_inputs"]["count"] == 6
    assert data["signals"]["count"] == 0
    assert data["conflict_areas"]["count"] == 0
    assert data["routing_decisions"]["count"] == 2
    assert data["links"]["truncated"] is False


def test_get_network_overview_rejects_unexpected_params():
    envelope = _dispatch("get_network_overview", bogus=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


# ---------------------------------------------------------------------------
# resolve_object — Korean acceptance test (synthetic fixture, per the brief)
# ---------------------------------------------------------------------------


def test_resolve_object_korean_query_ranks_exact_above_token_overlap():
    """'중앙로 교차로' must resolve BOTH Korean-named links, with the exact
    name match ranked strictly above the token-overlap-only match."""
    tools_network.set_active_inpx(KOREAN_FIXTURE_INPX)

    envelope = _dispatch("resolve_object", query="중앙로 교차로", object_type=None)

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["found"] is True
    ids = [c["id"] for c in data["candidates"]]
    assert ids == ["501", "502"]  # exact match (501) ranked above token-overlap-only (502)
    assert data["candidates"][0]["score"] > data["candidates"][1]["score"]
    assert data["candidates"][0]["name"] == "중앙로 교차로"
    assert data["candidates"][1]["name"] == "중앙로 남측 접근로"


def test_resolve_object_korean_query_filtered_by_object_type_link():
    tools_network.set_active_inpx(KOREAN_FIXTURE_INPX)

    envelope = _dispatch("resolve_object", query="중앙로 교차로", object_type="link")

    data = envelope["data"]
    assert data["found"] is True
    assert all(c["object_type"] == "link" for c in data["candidates"])


def test_resolve_object_ties_broken_by_object_id_ascending():
    """Two links (503, 504 — deliberately out of document order in the
    fixture) share an identical name, so both score an identical exact
    match (3.0). The tie must resolve to id ascending, not document/
    registration order."""
    tools_network.set_active_inpx(KOREAN_FIXTURE_INPX)

    envelope = _dispatch("resolve_object", query="테스트 동일링크", object_type="link")

    data = envelope["data"]
    assert data["found"] is True
    assert [c["id"] for c in data["candidates"]] == ["503", "504"]
    assert [c["score"] for c in data["candidates"]] == [3.0, 3.0]  # genuine tie, not a fluke


def test_resolve_object_whitespace_only_query_is_a_graceful_miss():
    """A whitespace-only query scores zero on every tier (nothing to match)
    and must come back as a well-formed miss, never raise."""
    envelope = _dispatch("resolve_object", query="   ", object_type=None)

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["found"] is False
    assert data["error_type"] == "object_not_found"
    assert isinstance(data["candidates"], list)


# ---------------------------------------------------------------------------
# resolve_object — English resolution + object_not_found miss (real demo data)
# ---------------------------------------------------------------------------


def test_resolve_object_english_typo_resolves_via_token_overlap():
    """'Central Main' (typo'd/partial, no '&') still resolves node 1 ('Central
    & Main') by shared lowercase tokens — a successful, ranked resolution."""
    envelope = _dispatch("resolve_object", query="Central Main", object_type="node")

    data = envelope["data"]
    assert data["found"] is True
    assert data["candidates"][0]["id"] == "1"
    assert data["candidates"][0]["name"] == "Central & Main"


def test_resolve_object_miss_returns_structured_object_not_found_with_candidates():
    """A query that scores zero on every tier (exact/substring/token overlap)
    against the whole demo network must come back as a structured miss whose
    fallback candidates (difflib over ids+names, resolved to owning ids) still
    surface the near match — node id '1' ('Central & Main'). Live-demo defect
    fix (2026-07-31, v2 queue unit 2): candidates are disambiguator dicts, not
    bare ids, so the match carries its name too."""
    envelope = _dispatch("resolve_object", query="Cntrall Man", object_type=None)

    assert envelope["status"] == "ok"  # a domain miss is still a successful tool call
    data = envelope["data"]
    assert data["found"] is False
    assert data["error_type"] == "object_not_found"
    assert all(isinstance(c, dict) for c in data["candidates"])
    match = next(c for c in data["candidates"] if c["id"] == "1")
    assert match["name"] == "Central & Main"
    assert match["object_type"] == "node"


def _vehicle_inputs_inpx(tmp_path: Path, tag: str, groups: list[tuple[str, list[str]]]) -> Path:
    """A minimal .inpx with one link (``620``) and one ``<vehicleInput>`` per
    (name, id) pair in ``groups`` -- e.g. ``[("Merricup", ["701", "702"])]``
    puts TWO vehicle inputs on link 620, both named "Merricup". Mirrors the
    sibling-disambiguation unit's inline fixture shape (no vehicleComposition/
    timeIntVehVols needed -- _parse_vehicle_inputs handles their absence the
    same way the demo .inpx's inputs do)."""
    inputs_xml = "".join(
        f'<vehicleInput no="{vid}" name="{name}" link="620"/>' for name, ids in groups for vid in ids
    )
    inpx = tmp_path / f"{tag}.inpx"
    inpx.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><network>'
        '<links><link no="620" name="L"><geometry><linkPolyPts>'
        '<linkPolyPoint x="0" y="0" zOffset="0"/><linkPolyPoint x="100" y="0" zOffset="0"/>'
        '</linkPolyPts></geometry><lanes><lane width="3.5"/></lanes></link></links>'
        f"<vehicleInputs>{inputs_xml}</vehicleInputs></network>",
        encoding="utf-8",
    )
    return inpx


# ---------------------------------------------------------------------------
# resolve_object MISS path -- fuzzy match on a NAME shared by several owners
# (v2 queue unit 1, 2026-07-29). Mirrors constraints.py's equivalent tests.
# ---------------------------------------------------------------------------


def test_resolve_object_miss_fuzzy_match_on_shared_name_yields_every_owner(tmp_path: Path):
    """The real Murdoch model has 17 groups of 3 vehicle inputs sharing one
    name (e.g. 107/207/307 all 'Jennalup ST_WB', one per vehicle class). A
    fuzzy NAME match on the MISS path must resolve to every owner of that
    name, not just the first-registered one (the pre-fix defect).

    Live-demo defect fix (2026-07-31, v2 queue unit 2): every owner's
    candidate is now a disambiguator dict, not a bare id -- carrying its
    name (and, since this fixture sets ``link``, that too) rather than
    leaving the caller to guess."""
    inpx = _vehicle_inputs_inpx(tmp_path, "shared", [("Merricup", ["701", "702", "703"])])
    tools_network.set_active_inpx(inpx)

    envelope = _dispatch("resolve_object", query="Merricop", object_type="vehicle_input")

    data = envelope["data"]
    assert data["found"] is False
    assert data["error_type"] == "object_not_found"
    candidates = data["candidates"]
    assert {c["id"] for c in candidates} == {"701", "702", "703"}
    assert {c["name"] for c in candidates} == {"Merricup"}
    assert all(c["object_type"] == "vehicle_input" for c in candidates)
    assert all(c["link"] == "620" for c in candidates)


def test_resolve_object_miss_fuzzy_match_on_unique_name_still_yields_one(tmp_path: Path):
    """Same MISS-path fallback, but a query fuzzy-matching a name that only
    ONE object carries must not leak owners from an unrelated shared-name
    group sitting in the same pool."""
    inpx = _vehicle_inputs_inpx(
        tmp_path, "mixed", [("Merricup", ["701", "702", "703"]), ("Qstreet", ["999"])]
    )
    tools_network.set_active_inpx(inpx)

    envelope = _dispatch("resolve_object", query="Qstreett", object_type="vehicle_input")

    data = envelope["data"]
    assert data["found"] is False
    assert data["candidates"] == [
        {"id": "999", "name": "Qstreet", "object_type": "vehicle_input", "link": "620"}
    ]


def test_resolve_object_miss_fuzzy_match_candidate_cap_bounds_multi_group_match(tmp_path: Path):
    """Bounded envelope (suggestion lists reach LLM prompts): a query that
    fuzzy-matches SEVERAL shared names at once (4 groups of 3 = 12 candidate
    owners) must not balloon the list unboundedly. _CANDIDATE_CAP (10) fits
    3 whole sibling groups plus one more owner before cutting off, rather
    than growing without bound or arbitrarily splitting every group evenly."""
    inpx = _vehicle_inputs_inpx(
        tmp_path,
        "cap",
        [
            ("Merricup", ["801", "802", "803"]),
            ("Berricup", ["811", "812", "813"]),
            ("Terricup", ["821", "822", "823"]),
            ("Kerricup", ["831", "832", "833"]),
        ],
    )
    tools_network.set_active_inpx(inpx)

    envelope = _dispatch("resolve_object", query="Nerricup", object_type="vehicle_input")

    data = envelope["data"]
    assert data["found"] is False
    candidates = data["candidates"]
    assert len(candidates) == 10  # capped, not the full 12
    assert all(isinstance(c, dict) for c in candidates)
    cand_ids = [c["id"] for c in candidates]
    assert len(set(cand_ids)) == len(cand_ids)  # no duplicate ids emitted
    # every candidate's own name matches the group its id belongs to -- the
    # enrichment did not scramble id<->name pairing under the cap.
    by_id_prefix = {"80": "Merricup", "81": "Berricup", "82": "Terricup", "83": "Kerricup"}
    for c in candidates:
        expected_name = by_id_prefix[c["id"][:2]]
        assert c["name"] == expected_name, c

    groups = [
        {"801", "802", "803"},
        {"811", "812", "813"},
        {"821", "822", "823"},
        {"831", "832", "833"},
    ]
    cand_set = set(cand_ids)
    full_groups = [g for g in groups if g <= cand_set]
    partial_groups = [g for g in groups if cand_set & g and not (g <= cand_set)]
    # every group is emitted whole until the cap is reached -- at most the
    # LAST one touched may be cut short.
    assert len(full_groups) == 3
    assert len(partial_groups) == 1


@_requires_real_dataset
def test_real_dataset_resolve_object_typo_miss_carries_names_for_both_shared_name_trios():
    """The exact live-demo defect repro (v2 queue unit 2, 2026-07-31): a user
    asked about vehicle input "Jenalup STWB" (typo'd). The miss's candidates
    used to be six bare ids -- ["107","207","307","111","211","311"] -- drawn
    from TWO different same-named trios on the real Murdoch model, giving the
    planner no way to tell "Jennalup ST_WB" (107/207/307) from "Jennalup St"
    (111/211/311) apart; it re-issued the identical call, hit the
    duplicate_call anti-loop refusal, repeated it four more times, and the
    turn died at the 6-iteration cap on an apology. Every candidate must now
    carry its name so the two trios read distinguishably."""
    tools_network.set_active_inpx(ACTIVE_REAL_INPX)
    try:
        envelope = _dispatch("resolve_object", query="Jenalup STWB", object_type="vehicle_input")

        data = envelope["data"]
        assert data["found"] is False
        assert data["error_type"] == "object_not_found"
        candidates = data["candidates"]
        assert {c["id"] for c in candidates} == {"107", "207", "307", "111", "211", "311"}
        names_by_id = {c["id"]: c["name"] for c in candidates}
        assert names_by_id["107"] == names_by_id["207"] == names_by_id["307"] == "Jennalup ST_WB"
        assert names_by_id["111"] == names_by_id["211"] == names_by_id["311"] == "Jennalup St"
        assert all(c["object_type"] == "vehicle_input" for c in candidates)
        assert all(c.get("link") for c in candidates)
    finally:
        tools_network.set_active_inpx(None)


def test_resolve_object_rejects_unknown_object_type():
    envelope = _dispatch("resolve_object", query="anything", object_type="banana")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


# ---------------------------------------------------------------------------
# get_vehicle_inputs / get_signal_controllers — extended shadow model sections
# ---------------------------------------------------------------------------


def test_get_vehicle_inputs_on_demo_data_has_no_volume_children():
    """The demo .inpx's <vehicleInput> elements carry no/name/link attrs and
    no volume children — volume must come back None, never crash. v2 unit ⑤:
    interval_count/volumes are now real fields too (0/[] here, not absent).

    FIX 1 (2026-07-28): ``composition_nos``/``composition_varies_by_interval``
    are now OMITTED (not present-but-empty) when the composition doesn't
    vary across intervals -- true of every input here (the demo network has
    no <vehicleComposition> and no vehComp attrs at all, so it trivially
    doesn't vary). See tools_network._parse_vehicle_inputs's "Omit-when-
    uniform" docstring section for the payload-bloat measurement this closes.
    """
    envelope = _dispatch("get_vehicle_inputs")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 6
    assert len(data["vehicle_inputs"]) == 6
    # Well under the default cap, so an unfiltered call still returns
    # everything -- truncated must say so honestly.
    assert data["truncated"] is False
    assert "hint" not in data
    for vi in data["vehicle_inputs"]:
        assert set(vi.keys()) == {
            "no",
            "name",
            "link",
            "volume",
            "interval_count",
            "volumes",
            # sibling-disambiguation unit (2026-07-28)
            "composition_no",
            "composition_name",
            # composition_nos/composition_varies_by_interval: OMITTED here,
            # not present-but-empty -- see FIX 1's docstring above.
        }
        assert vi["volume"] is None
        assert vi["interval_count"] == 0
        assert vi["volumes"] == []
        # The demo network has no <vehicleComposition> and no vehComp attrs:
        # empty, never a fabricated class.
        assert vi["composition_no"] == "" and vi["composition_name"] == ""
    first = next(vi for vi in data["vehicle_inputs"] if vi["no"] == "1")
    assert first["name"] == "Main St EB"
    assert first["link"] == "101"


def test_get_vehicle_inputs_carries_the_composition_that_distinguishes_same_named_siblings(
    tmp_path: Path,
):
    """Sibling-disambiguation unit (2026-07-28). The real client model puts
    three vehicle inputs on one link, all with the SAME name, differing only
    by vehicle class — so no/name/link alone gave the planner three
    indistinguishable objects. The composition is what tells them apart."""
    inpx = tmp_path / "siblings.inpx"
    inpx.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><network>'
        '<links><link no="620" name="Jennalup St WB"><geometry><linkPolyPts>'
        '<linkPolyPoint x="0" y="0" zOffset="0"/><linkPolyPoint x="100" y="0" zOffset="0"/>'
        '</linkPolyPts></geometry><lanes><lane width="3.5"/></lanes></link></links>'
        '<vehicleCompositions><vehicleComposition no="1" name="Class1"/>'
        '<vehicleComposition no="2" name="Class2-5"/></vehicleCompositions>'
        '<vehicleInputs>'
        '<vehicleInput no="107" name="Jennalup ST_WB" link="620"><timeIntVehVols>'
        '<timeIntervalVehVolume timeInt="1 0" volume="127.3" vehComp="1"/></timeIntVehVols></vehicleInput>'
        '<vehicleInput no="207" name="Jennalup ST_WB" link="620"><timeIntVehVols>'
        '<timeIntervalVehVolume timeInt="1 0" volume="19" vehComp="2"/></timeIntVehVols></vehicleInput>'
        "</vehicleInputs></network>",
        encoding="utf-8",
    )
    tools_network.set_active_inpx(inpx)

    data = _dispatch("get_vehicle_inputs")["data"]

    by_no = {vi["no"]: vi for vi in data["vehicle_inputs"]}
    assert by_no["107"]["name"] == by_no["207"]["name"]  # the whole problem
    assert (by_no["107"]["composition_no"], by_no["107"]["composition_name"]) == ("1", "Class1")
    assert (by_no["207"]["composition_no"], by_no["207"]["composition_name"]) == ("2", "Class2-5")
    # FIX 1 (2026-07-28): neither input's composition varies across its (one)
    # interval, so the per-interval composition_no key and the
    # composition_nos/composition_varies_by_interval keys are all OMITTED --
    # the input-level composition_no/composition_name above already say
    # everything there is to say. See _parse_vehicle_inputs's "Omit-when-
    # uniform" docstring section.
    assert "composition_no" not in by_no["107"]["volumes"][0]
    assert "composition_nos" not in by_no["107"]
    assert "composition_varies_by_interval" not in by_no["107"]

    # ...and resolve_object, which is how the planner finds them by name.
    candidates = _dispatch("resolve_object", query="Jennalup ST_WB", object_type="vehicle_input")["data"][
        "candidates"
    ]
    resolved = {c["id"]: c for c in candidates}
    assert resolved["107"]["composition_name"] == "Class1"
    assert resolved["207"]["composition_name"] == "Class2-5"
    assert resolved["107"]["link"] == resolved["207"]["link"] == "620"


def test_get_signal_controllers_on_demo_data_is_empty_but_well_formed():
    """practice.inpx's signalController elements carry no intSupplyData, so
    signal_parser.parse_signal_controllers yields none — the tool must return
    an empty, well-formed list rather than erroring."""
    envelope = _dispatch("get_signal_controllers")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data == {"count": 0, "signals": []}


def test_get_conflict_areas_on_demo_data_is_empty_but_well_formed():
    """The demo .inpx carries no <conflictArea> elements at all — the tool
    must return an empty, well-formed list rather than erroring (v2 unit ④).

    D1 live-demo fix (docs/phase2/DECISIONS.md, 2026-07-27): an unfiltered
    call now always carries an explicit ``truncated`` flag (false here — an
    empty result is trivially not truncated), no ``hint`` key when nothing
    was cut."""
    envelope = _dispatch("get_conflict_areas")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data == {"count": 0, "conflict_areas": [], "truncated": False}
    assert "hint" not in data


# ---------------------------------------------------------------------------
# get_conflict_areas / _parse_conflict_areas — synthetic fixture with real
# conflictArea elements (v2 unit ④; the demo network has none)
# ---------------------------------------------------------------------------

_CONFLICT_AREA_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links>
    <link no="3" name="Main St EB"><lanes><lane width="3.5"/></lanes></link>
    <link no="12" name="Side St N"><lanes><lane width="3.5"/></lanes></link>
    <link no="21" name="Side St S"><lanes><lane width="3.5"/></lanes></link>
  </links>
  <conflictAreas>
    <conflictArea no="3" linkA="3" linkB="12" status="PASSIVE"/>
    <conflictArea no="7" linkA="3" linkB="21" status="AHASRIGHTOFWAY"/>
  </conflictAreas>
</network>
"""


@pytest.fixture()
def conflict_area_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "conflict_areas.inpx"
    path.write_text(_CONFLICT_AREA_FIXTURE_INPX, encoding="utf-8")
    return path


def test_get_conflict_areas_on_real_conflict_area_elements(conflict_area_fixture_inpx: Path):
    tools_network.set_active_inpx(conflict_area_fixture_inpx)

    envelope = _dispatch("get_conflict_areas")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 2
    by_no = {ca["no"]: ca for ca in data["conflict_areas"]}
    assert by_no["3"] == {
        "no": "3",
        "link_a": "3",
        "link_b": "12",
        "link_pair": ("3", "12"),  # dispatch() is in-process, no JSON round-trip yet
        "status": "PASSIVE",
        "name": "Link 3 vs Link 12",
    }
    assert by_no["7"]["status"] == "AHASRIGHTOFWAY"
    assert by_no["7"]["link_pair"] == ("3", "21")
    assert data["truncated"] is False  # well under the 40-item cap
    assert "hint" not in data


# ---------------------------------------------------------------------------
# D1 live-demo fix (docs/phase2/DECISIONS.md, 2026-07-27): conflict_area_no/
# link_no server-side filters, and a bounded (capped + explicitly truncated)
# default for an unfiltered call on a large network -- so the model can
# narrow BEFORE the envelope is built instead of always getting (and having
# planner.py's renderer separately truncate) the whole unbounded list.
# ---------------------------------------------------------------------------


def test_get_conflict_areas_filters_by_conflict_area_no(conflict_area_fixture_inpx: Path):
    tools_network.set_active_inpx(conflict_area_fixture_inpx)

    envelope = _dispatch("get_conflict_areas", conflict_area_no="7")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 1
    assert data["conflict_areas"][0]["no"] == "7"
    assert data["conflict_areas"][0]["status"] == "AHASRIGHTOFWAY"
    # A filtered call is already bounded by the model's own query -- no cap
    # bookkeeping needed.
    assert "truncated" not in data
    assert "hint" not in data


def test_get_conflict_areas_filters_by_conflict_area_no_no_match_is_empty_not_an_error(
    conflict_area_fixture_inpx: Path,
):
    tools_network.set_active_inpx(conflict_area_fixture_inpx)

    envelope = _dispatch("get_conflict_areas", conflict_area_no="9999")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "conflict_areas": []}


def test_get_conflict_areas_filters_by_link_no_matches_either_side_of_the_pair(
    conflict_area_fixture_inpx: Path,
):
    tools_network.set_active_inpx(conflict_area_fixture_inpx)

    # link 3 is link_a on BOTH fixture conflict areas (no=3: 3 vs 12, no=7: 3
    # vs 21) -- link_no must match either side, not just link_a.
    envelope = _dispatch("get_conflict_areas", link_no="21")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 1
    assert data["conflict_areas"][0]["no"] == "7"


def test_get_conflict_areas_rejects_unexpected_params(conflict_area_fixture_inpx: Path):
    tools_network.set_active_inpx(conflict_area_fixture_inpx)

    envelope = _dispatch("get_conflict_areas", bogus=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


_MANY_CONFLICT_AREAS_INPX_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links></links>
  <conflictAreas>
{areas}
  </conflictAreas>
</network>
"""


def _many_conflict_areas_inpx(tmp_path: Path, count: int) -> Path:
    """A synthetic .inpx with ``count`` real ``<conflictArea>`` elements --
    parsing has no cross-reference to a matching ``<link>`` element (see
    ``_parse_conflict_areas``), so no ``<links>`` content is needed."""
    areas = "\n".join(
        f'    <conflictArea no="{i}" linkA="{i}" linkB="{i + 1000}" status="PASSIVE"/>'
        for i in range(1, count + 1)
    )
    path = tmp_path / f"many_conflict_areas_{count}.inpx"
    path.write_text(_MANY_CONFLICT_AREAS_INPX_TEMPLATE.format(areas=areas), encoding="utf-8")
    return path


def test_get_conflict_areas_unfiltered_call_on_large_network_is_capped_and_truncated(
    tmp_path: Path,
):
    tools_network.set_active_inpx(_many_conflict_areas_inpx(tmp_path, 224))

    envelope = _dispatch("get_conflict_areas")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 224  # the TRUE total, never hidden
    assert len(data["conflict_areas"]) == tools_network._CONFLICT_AREAS_DEFAULT_CAP
    assert data["truncated"] is True
    assert "40" in data["hint"] and "224" in data["hint"]
    # A filtered follow-up narrows to exactly what was asked, proving the
    # hint's own suggestion actually works.
    followup = _dispatch("get_conflict_areas", conflict_area_no="150")
    assert followup["data"]["count"] == 1
    assert followup["data"]["conflict_areas"][0]["no"] == "150"


def test_get_conflict_areas_unfiltered_call_under_cap_is_not_truncated(tmp_path: Path):
    tools_network.set_active_inpx(_many_conflict_areas_inpx(tmp_path, 10))

    envelope = _dispatch("get_conflict_areas")

    data = envelope["data"]
    assert data["count"] == 10
    assert len(data["conflict_areas"]) == 10
    assert data["truncated"] is False
    assert "hint" not in data


def test_build_shadow_model_parses_conflict_areas_from_real_xml(conflict_area_fixture_inpx: Path):
    shadow = tools_network.build_shadow_model(conflict_area_fixture_inpx)

    assert len(shadow["conflict_areas"]) == 2
    passive = [ca for ca in shadow["conflict_areas"] if ca["status"] == "PASSIVE"]
    assert len(passive) == 1
    assert passive[0]["link_pair"] == ("3", "12")  # a real tuple at the unit level (pre-JSON)


# ---------------------------------------------------------------------------
# get_priority_rules / _parse_priority_rules -- QUEUE 3 unit 2 (the demo
# network has no <priorityRule> elements; a synthetic fixture with real ones,
# mirroring the conflict-area fixture's shape immediately above)
# ---------------------------------------------------------------------------

_PRIORITY_RULE_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links>
    <link no="11467" name="Minor approach"><lanes><lane width="3.5"/></lanes></link>
    <link no="632" name="Major road"><lanes><lane width="3.5"/></lanes></link>
  </links>
  <priorityRules>
    <priorityRule no="132" name="" lane="11467 1" pos="5.045" sigState="GREEN"
                   affectDrivDir="FORWARD" slowDownDist="3">
      <conflictMarkers>
        <conflictMarker lane="632 1" pos="9" minGapTime="2.5" minClear="20" maxSpeed="180"
                         effectDrivDir="FORWARD"/>
        <conflictMarker lane="632 2" pos="9" minGapTime="2.5" minClear="20" maxSpeed="180"
                         effectDrivDir="FORWARD"/>
      </conflictMarkers>
    </priorityRule>
    <priorityRule no="134" name="" lane="608 2" pos="47.7" sigState="GREEN"
                   affectDrivDir="FORWARD" slowDownDist="3">
      <conflictMarkers>
        <conflictMarker lane="609 1" pos="21.5" minGapTime="0" minClear="15" maxSpeed="12"
                         effectDrivDir="FORWARD"/>
      </conflictMarkers>
    </priorityRule>
  </priorityRules>
</network>
"""


@pytest.fixture()
def priority_rule_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "priority_rules.inpx"
    path.write_text(_PRIORITY_RULE_FIXTURE_INPX, encoding="utf-8")
    return path


def test_get_priority_rules_on_demo_data_is_empty_but_well_formed():
    """The demo .inpx carries no <priorityRule> elements at all -- the tool
    must return an empty, well-formed list rather than erroring."""
    envelope = _dispatch("get_priority_rules")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "priority_rules": [], "truncated": False}
    assert "hint" not in envelope["data"]


def test_get_priority_rules_on_real_priority_rule_elements(priority_rule_fixture_inpx: Path):
    tools_network.set_active_inpx(priority_rule_fixture_inpx)

    envelope = _dispatch("get_priority_rules")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 2
    by_no = {pr["no"]: pr for pr in data["priority_rules"]}
    rule = by_no["132"]
    assert rule["link"] == "11467"
    assert rule["lane"] == "1"
    assert rule["link_ref"] == "lane"
    assert rule["sig_state"] == "GREEN"
    assert len(rule["conflict_markers"]) == 2
    marker1 = rule["conflict_markers"][0]
    assert marker1 == {
        "marker_index": 1,
        "link": "632",
        "lane": "1",
        "link_ref": "lane",
        "pos": 9.0,
        "gap_time_s": 2.5,
        "headway_m": 20.0,
        "max_speed": 180.0,
        "effect_driv_dir": "FORWARD",
    }
    assert rule["conflict_markers"][1]["marker_index"] == 2
    # rule 134's single marker legitimately has gap_time_s=0 -- real,
    # common data, not missing/absent.
    assert by_no["134"]["conflict_markers"][0]["gap_time_s"] == 0.0
    assert data["truncated"] is False
    assert "hint" not in data


def test_get_priority_rules_filters_by_priority_rule_no(priority_rule_fixture_inpx: Path):
    tools_network.set_active_inpx(priority_rule_fixture_inpx)

    envelope = _dispatch("get_priority_rules", priority_rule_no="134")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 1
    assert data["priority_rules"][0]["no"] == "134"
    assert "truncated" not in data
    assert "hint" not in data


def test_get_priority_rules_filters_by_priority_rule_no_no_match_is_empty_not_an_error(
    priority_rule_fixture_inpx: Path,
):
    tools_network.set_active_inpx(priority_rule_fixture_inpx)

    envelope = _dispatch("get_priority_rules", priority_rule_no="9999")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "priority_rules": []}


def test_get_priority_rules_filters_by_link_no_matches_rule_link(priority_rule_fixture_inpx: Path):
    tools_network.set_active_inpx(priority_rule_fixture_inpx)

    envelope = _dispatch("get_priority_rules", link_no="11467")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 1
    assert data["priority_rules"][0]["no"] == "132"


def test_get_priority_rules_filters_by_link_no_matches_conflict_marker_link(
    priority_rule_fixture_inpx: Path,
):
    tools_network.set_active_inpx(priority_rule_fixture_inpx)

    # link 632 is not rule 132's OWN link (that's 11467) -- it only appears
    # on this rule's conflict markers, matching get_conflict_areas'
    # either-side-of-the-pair filter precedent.
    envelope = _dispatch("get_priority_rules", link_no="632")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 1
    assert data["priority_rules"][0]["no"] == "132"


def test_get_priority_rules_rejects_unexpected_params(priority_rule_fixture_inpx: Path):
    tools_network.set_active_inpx(priority_rule_fixture_inpx)

    envelope = _dispatch("get_priority_rules", bogus=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


_MANY_PRIORITY_RULES_INPX_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links></links>
  <priorityRules>
{rules}
  </priorityRules>
</network>
"""


def _many_priority_rules_inpx(tmp_path: Path, count: int) -> Path:
    rules = "\n".join(
        f'    <priorityRule no="{i}" name="" lane="{i} 1" pos="1.0" sigState="GREEN" '
        f'affectDrivDir="FORWARD" slowDownDist="3">'
        f'<conflictMarkers><conflictMarker lane="{i + 1000} 1" pos="1" minGapTime="1" '
        f'minClear="5" maxSpeed="50" effectDrivDir="FORWARD"/></conflictMarkers></priorityRule>'
        for i in range(1, count + 1)
    )
    path = tmp_path / f"many_priority_rules_{count}.inpx"
    path.write_text(_MANY_PRIORITY_RULES_INPX_TEMPLATE.format(rules=rules), encoding="utf-8")
    return path


def test_get_priority_rules_unfiltered_call_on_large_network_is_capped_and_truncated(tmp_path: Path):
    tools_network.set_active_inpx(_many_priority_rules_inpx(tmp_path, 61))

    envelope = _dispatch("get_priority_rules")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 61  # the TRUE total, never hidden
    assert len(data["priority_rules"]) == tools_network._PRIORITY_RULES_DEFAULT_CAP
    assert data["truncated"] is True
    assert "40" in data["hint"] and "61" in data["hint"]
    followup = _dispatch("get_priority_rules", priority_rule_no="50")
    assert followup["data"]["count"] == 1
    assert followup["data"]["priority_rules"][0]["no"] == "50"


def test_get_priority_rules_unfiltered_call_under_cap_is_not_truncated(tmp_path: Path):
    tools_network.set_active_inpx(_many_priority_rules_inpx(tmp_path, 10))

    envelope = _dispatch("get_priority_rules")

    data = envelope["data"]
    assert data["count"] == 10
    assert len(data["priority_rules"]) == 10
    assert data["truncated"] is False
    assert "hint" not in data


def test_build_shadow_model_parses_priority_rules_from_real_xml(priority_rule_fixture_inpx: Path):
    shadow = tools_network.build_shadow_model(priority_rule_fixture_inpx)

    assert len(shadow["priority_rules"]) == 2
    flat = shadow["priority_rule_markers"]
    assert len(flat) == 3  # rule 132 has 2 markers, rule 134 has 1
    by_id = {m["id"]: m for m in flat}
    assert by_id["132.1"]["gap_headway"] == {"gap_time_s": 2.5, "headway_m": 20.0}
    assert by_id["132.2"]["gap_headway"] == {"gap_time_s": 2.5, "headway_m": 20.0}
    assert by_id["134.1"]["gap_headway"] == {"gap_time_s": 0.0, "headway_m": 15.0}
    assert by_id["132.1"]["priority_rule_no"] == "132"


def test_get_network_overview_includes_priority_rules_section(priority_rule_fixture_inpx: Path):
    tools_network.set_active_inpx(priority_rule_fixture_inpx)

    envelope = _dispatch("get_network_overview")

    data = envelope["data"]
    assert data["priority_rules"]["count"] == 2
    ids = {item["id"] for item in data["priority_rules"]["items"]}
    assert ids == {"132", "134"}


# Rule 143 on the real Murdoch model (runtime/uploads/current/current.inpx)
# references its link via intLink="613" -- NO lane attribute at all -- on
# BOTH the rule and its single conflict marker; it is the only intLink user
# among that model's 13 rules / 20 markers. intLink numbers ARE the regular
# <link no> numbering (see _parse_priority_rule_link_ref's docstring for the
# static verification). Pinned here as a synthetic fixture mirroring the real
# file's numbers/positions -- the live client file is read-only, not a test
# asset.
_INT_LINK_PRIORITY_RULE_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links>
    <link no="613" name=""><lanes><lane width="3.5"/><lane width="3.5"/></lanes></link>
    <link no="621" name=""><lanes><lane width="3.5"/><lane width="3.5"/><lane width="3.5"/></lanes></link>
  </links>
  <priorityRules>
    <priorityRule no="143" name="" intLink="613" pos="131.15585089862415" sigState="GREEN"
                   affectDrivDir="FORWARD" slowDownDist="3">
      <conflictMarkers>
        <conflictMarker intLink="613" pos="164.77089987910313" minGapTime="0" minClear="12"
                         maxSpeed="12" effectDrivDir="FORWARD"/>
      </conflictMarkers>
    </priorityRule>
    <priorityRule no="144" name="" lane="621 2" pos="79.53" sigState="GREEN"
                   affectDrivDir="FORWARD" slowDownDist="3">
      <conflictMarkers>
        <conflictMarker lane="613 1" pos="9.11" minGapTime="0" minClear="15" maxSpeed="12"
                         effectDrivDir="FORWARD"/>
      </conflictMarkers>
    </priorityRule>
  </priorityRules>
</network>
"""


@pytest.fixture()
def int_link_priority_rule_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "int_link_priority_rules.inpx"
    path.write_text(_INT_LINK_PRIORITY_RULE_FIXTURE_INPX, encoding="utf-8")
    return path


def test_get_priority_rules_resolves_int_link_reference_form(
    int_link_priority_rule_fixture_inpx: Path,
):
    """An intLink-referenced rule must come back with its link RESOLVED
    (link_ref="int_link", lane legitimately empty) -- not with blank
    link/lane fields that read as missing data."""
    tools_network.set_active_inpx(int_link_priority_rule_fixture_inpx)

    envelope = _dispatch("get_priority_rules", priority_rule_no="143")

    assert envelope["status"] == "ok"
    rule = envelope["data"]["priority_rules"][0]
    assert rule["link"] == "613"
    assert rule["lane"] == ""
    assert rule["link_ref"] == "int_link"
    assert rule["pos"] == pytest.approx(131.156, abs=0.001)
    assert rule["conflict_markers"] == [
        {
            "marker_index": 1,
            "link": "613",
            "lane": "",
            "link_ref": "int_link",
            "pos": pytest.approx(164.771, abs=0.001),
            "gap_time_s": 0.0,
            "headway_m": 12.0,
            "max_speed": 12.0,
            "effect_driv_dir": "FORWARD",
        }
    ]


def test_get_priority_rules_link_no_filter_matches_int_link_referenced_rule(
    int_link_priority_rule_fixture_inpx: Path,
):
    """link_no=613 must match rule 143 (its OWN link, via intLink) as well as
    rule 144 (whose conflict marker sits on lane "613 1") -- before intLink
    resolution, 143 was invisible to this filter."""
    tools_network.set_active_inpx(int_link_priority_rule_fixture_inpx)

    envelope = _dispatch("get_priority_rules", link_no="613")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 2
    assert {pr["no"] for pr in data["priority_rules"]} == {"143", "144"}
    by_no = {pr["no"]: pr for pr in data["priority_rules"]}
    assert by_no["144"]["link_ref"] == "lane"
    assert by_no["144"]["conflict_markers"][0]["link_ref"] == "lane"


def test_resolve_object_finds_conflict_area_by_display_name(conflict_area_fixture_inpx: Path):
    """The synthesized 'Link A vs Link B' display name makes conflict areas
    reachable through the same lexical resolution every other object type
    gets — part of "make conflict areas visible to the agent"."""
    tools_network.set_active_inpx(conflict_area_fixture_inpx)

    envelope = _dispatch("resolve_object", query="Link 3 vs Link 12", object_type="conflict_area")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["found"] is True
    assert data["candidates"][0]["id"] == "3"
    assert data["candidates"][0]["object_type"] == "conflict_area"


def test_get_network_overview_includes_conflict_areas_section(conflict_area_fixture_inpx: Path):
    tools_network.set_active_inpx(conflict_area_fixture_inpx)

    envelope = _dispatch("get_network_overview")

    data = envelope["data"]
    assert data["conflict_areas"]["count"] == 2
    ids = {item["id"] for item in data["conflict_areas"]["items"]}
    assert ids == {"3", "7"}


# ---------------------------------------------------------------------------
# vehicle_inputs — multi-interval volumes (v2 unit ⑤)
# ---------------------------------------------------------------------------

_MULTI_INTERVAL_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links>
    <link no="11" name="Border Rd"><lanes><lane width="3.5"/></lanes></link>
  </links>
  <vehicleInputs>
    <vehicleInput anmFlag="false" link="11" name="" no="2">
      <timeIntVehVols>
        <timeIntervalVehVolume cont="false" timeInt="1 0" vehComp="100" volType="EXACT" volume="1000"/>
        <timeIntervalVehVolume cont="true" timeInt="1 1200000" vehComp="100" volType="EXACT" volume="1000"/>
        <timeIntervalVehVolume cont="true" timeInt="1 1800000" vehComp="100" volType="EXACT" volume="1000"/>
      </timeIntVehVols>
    </vehicleInput>
    <vehicleInput anmFlag="false" link="11" name="single" no="9"/>
  </vehicleInputs>
</network>
"""


@pytest.fixture()
def multi_interval_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "multi_interval.inpx"
    path.write_text(_MULTI_INTERVAL_FIXTURE_INPX, encoding="utf-8")
    return path


def test_get_vehicle_inputs_parses_multi_interval_volumes(multi_interval_fixture_inpx: Path):
    # v2 unit ⑤: closes the vehicle_input_multi_interval_write_gap
    # CATALOG-GAP -- interval_count/volumes are now real, parsed straight
    # from every <timeIntervalVehVolume> child in document order.
    tools_network.set_active_inpx(multi_interval_fixture_inpx)

    envelope = _dispatch("get_vehicle_inputs")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    multi = next(vi for vi in data["vehicle_inputs"] if vi["no"] == "2")
    assert multi["volume"] == 1000.0  # backward-compat: interval 1's volume
    assert multi["interval_count"] == 3
    # FIX 1 (2026-07-28): all three intervals name the SAME composition (no
    # variation), so the per-interval composition_no key is OMITTED -- the
    # input-level composition_no/composition_name below already say it once.
    # See _parse_vehicle_inputs's "Omit-when-uniform" docstring section for
    # the payload-bloat measurement this closes (this exact repetition, ~7KB
    # over 306 intervals on the real 51-input model, was the single biggest
    # contributor).
    assert multi["volumes"] == [
        {"interval_no": 1, "from_s": 0.0, "volume": 1000.0, "continuous": False},
        {"interval_no": 2, "from_s": 1200.0, "volume": 1000.0, "continuous": True},
        {"interval_no": 3, "from_s": 1800.0, "volume": 1000.0, "continuous": True},
    ]
    # This fixture's network declares no <vehicleComposition> at all, so the
    # number survives and the name stays empty rather than being invented.
    assert multi["composition_no"] == "100"
    assert multi["composition_name"] == ""
    # Not varying -> composition_nos/composition_varies_by_interval OMITTED
    # entirely (present-but-empty would still be a truncation-budget cost for
    # zero information -- see FIX 1's docstring).
    assert "composition_nos" not in multi
    assert "composition_varies_by_interval" not in multi

    single = next(vi for vi in data["vehicle_inputs"] if vi["no"] == "9")
    assert single["volume"] is None
    assert single["interval_count"] == 0
    assert single["volumes"] == []
    assert "composition_nos" not in single


def test_get_vehicle_inputs_represents_varying_composition_honestly(tmp_path: Path):
    """FIX 1's other half of "omit when uniform": when a composition DOES
    vary across intervals (schema-legal even though 0/51 real inputs do it —
    see vehicle_input_composition_fields's own docstring), nothing is
    trimmed -- collapsing it would be exactly the silent lie this project
    exists to prevent, not a size optimization."""
    inpx = tmp_path / "varying.inpx"
    inpx.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><network>'
        '<links><link no="11" name="Border Rd"><lanes><lane width="3.5"/></lanes></link></links>'
        '<vehicleCompositions><vehicleComposition no="1" name="Class1"/>'
        '<vehicleComposition no="2" name="Class2"/></vehicleCompositions>'
        '<vehicleInputs><vehicleInput no="2" name="" link="11"><timeIntVehVols>'
        '<timeIntervalVehVolume cont="false" timeInt="1 0" vehComp="1" volume="1000"/>'
        '<timeIntervalVehVolume cont="false" timeInt="1 1200000" vehComp="2" volume="500"/>'
        "</timeIntVehVols></vehicleInput></vehicleInputs></network>",
        encoding="utf-8",
    )
    tools_network.set_active_inpx(inpx)

    data = _dispatch("get_vehicle_inputs")["data"]

    vi = next(v for v in data["vehicle_inputs"] if v["no"] == "2")
    # Varies -> input-level composition_no/composition_name are the honest
    # "not a single class" empty string (vehicle_input_composition_fields's
    # own contract), and ALL of the per-interval/_nos/varies data is present.
    assert vi["composition_no"] == "" and vi["composition_name"] == ""
    assert vi["composition_nos"] == ["1", "2"]
    assert vi["composition_varies_by_interval"] is True
    assert [iv["composition_no"] for iv in vi["volumes"]] == ["1", "2"]


# ---------------------------------------------------------------------------
# get_vehicle_inputs — filters, default cap, link-grouping (FIX 1, 2026-07-28)
#
# The bug this closes: the real .inpx declares every <vehicleInput> in
# composition-MAJOR order (every class-1 input, THEN every class-2, THEN
# every class-3), so a document-order prefix truncation shows one vehicle
# class network-wide and drops every other class's inputs entirely --
# reproduced synthetically here so the regression is pinned without the real
# dataset.
# ---------------------------------------------------------------------------


def _many_links_siblings_inpx(tmp_path: Path, link_count: int) -> Path:
    """A synthetic .inpx with ``link_count`` links, each carrying 3 vehicle
    inputs (one per composition class 1/2/3), declared in COMPOSITION-MAJOR
    document order -- ALL class-1 inputs first (one per link), THEN all
    class-2, THEN all class-3 -- mirroring the real Murdoch Drive model's own
    measured document order exactly (verified live: its first 18
    <vehicleInput> elements are composition_no "1", covering every one of
    its 17 links, before a single "2xx" appears)."""
    links_xml = "".join(
        f'<link no="{i}" name="L{i}"><lanes><lane width="3.5"/></lanes></link>' for i in range(1, link_count + 1)
    )
    inputs_xml = []
    for cls in (1, 2, 3):
        for i in range(1, link_count + 1):
            no = f"{cls}{i:02d}"
            inputs_xml.append(
                f'<vehicleInput no="{no}" name="N{i}" link="{i}"><timeIntVehVols>'
                f'<timeIntervalVehVolume timeInt="1 0" volume="{i}.0" vehComp="{cls}"/>'
                "</timeIntVehVols></vehicleInput>"
            )
    path = tmp_path / f"many_links_{link_count}.inpx"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><network>'
        f"<links>{links_xml}</links>"
        '<vehicleCompositions><vehicleComposition no="1" name="Class1"/>'
        '<vehicleComposition no="2" name="Class2"/><vehicleComposition no="3" name="Class3"/>'
        "</vehicleCompositions>"
        f"<vehicleInputs>{''.join(inputs_xml)}</vehicleInputs></network>",
        encoding="utf-8",
    )
    return path


def test_get_vehicle_inputs_unfiltered_call_groups_by_link_not_document_order(tmp_path: Path):
    """REGRESSION PIN for the measured live bug: with document-order
    truncation, a 17-group/51-input network's first N items are ALL class 1
    (the old, undeniable failure -- 15/51 shown, every one class "1", 207/307
    absent entirely). Link-grouping must show COMPLETE sibling groups
    instead, spanning every class, within the default cap."""
    tools_network.set_active_inpx(_many_links_siblings_inpx(tmp_path, 17))

    envelope = _dispatch("get_vehicle_inputs")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 51
    assert len(data["vehicle_inputs"]) == tools_network._VEHICLE_INPUTS_DEFAULT_CAP
    assert data["truncated"] is True
    assert "20" in data["hint"] and "51" in data["hint"]
    shown_classes = {vi["composition_no"] for vi in data["vehicle_inputs"]}
    # The whole point: more than just class "1" survives the cap.
    assert shown_classes == {"1", "2", "3"}
    # Link 6's own group (101 06/206/306 -- 3-digit-padded no) is fully
    # inside the first 20 grouped items (6th link, 3 items each -> position
    # 16-18), exactly like the real link-620 worked example.
    shown_nos = {vi["no"] for vi in data["vehicle_inputs"]}
    assert {"106", "206", "306"} <= shown_nos


def test_get_vehicle_inputs_filters_by_link_no_returns_the_whole_sibling_group(tmp_path: Path):
    tools_network.set_active_inpx(_many_links_siblings_inpx(tmp_path, 17))

    envelope = _dispatch("get_vehicle_inputs", link_no="6")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 3
    assert {vi["no"] for vi in data["vehicle_inputs"]} == {"106", "206", "306"}
    # A filtered call is already bounded by the model's own query -- no cap.
    assert "truncated" not in data
    assert "hint" not in data


def test_get_vehicle_inputs_filters_by_input_no_exact_match(tmp_path: Path):
    tools_network.set_active_inpx(_many_links_siblings_inpx(tmp_path, 17))

    envelope = _dispatch("get_vehicle_inputs", input_no="307")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 1
    assert data["vehicle_inputs"][0]["no"] == "307"
    assert data["vehicle_inputs"][0]["link"] == "7"


def test_get_vehicle_inputs_filters_by_composition_no(tmp_path: Path):
    tools_network.set_active_inpx(_many_links_siblings_inpx(tmp_path, 17))

    envelope = _dispatch("get_vehicle_inputs", composition_no="3")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 17
    assert all(vi["composition_no"] == "3" for vi in data["vehicle_inputs"])


def test_get_vehicle_inputs_unfiltered_call_under_cap_is_not_truncated():
    # The demo network (6 inputs) is well under the cap.
    envelope = _dispatch("get_vehicle_inputs")

    data = envelope["data"]
    assert data["count"] == 6
    assert len(data["vehicle_inputs"]) == 6
    assert data["truncated"] is False
    assert "hint" not in data


def test_get_vehicle_inputs_rejects_unexpected_params():
    envelope = _dispatch("get_vehicle_inputs", bogus=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


@_requires_real_dataset
def test_real_dataset_get_vehicle_inputs_unfiltered_call_surfaces_all_three_link_620_siblings():
    """The exact live acceptance case FIX 1 was built against: an unfiltered
    call, rendered under the planner's real 12000-char recent-observation
    budget, must show input 207 AND 307 -- not just 107 -- and every one of
    Murdoch Drive's three composition classes."""
    from services.agent import planner

    tools_network.set_active_inpx(ACTIVE_REAL_INPX)
    try:
        envelope = _dispatch("get_vehicle_inputs")

        assert envelope["status"] == "ok"
        assert envelope["data"]["count"] == 51

        rendered = planner._render_observation(envelope, 12000, "ko")
        assert len(rendered) <= 12000
        parsed = json.loads(rendered)
        shown = [vi for vi in parsed["data"]["vehicle_inputs"] if isinstance(vi, dict)]
        shown_nos = {vi["no"] for vi in shown}
        assert "207" in shown_nos, "the sibling-disambiguation unit's whole point: 207 must be visible"
        assert "307" in shown_nos, "the sibling-disambiguation unit's whole point: 307 must be visible"
        assert {vi["composition_no"] for vi in shown} == {"1", "2", "3"}
    finally:
        tools_network.set_active_inpx(None)


# ---------------------------------------------------------------------------
# get_routing_decisions / _parse_routing_decisions — v2 unit ⑥
# ---------------------------------------------------------------------------


def test_get_routing_decisions_on_demo_data():
    # Unlike signals/conflict_areas, the demo .inpx DOES carry static routing
    # decisions (2 decisions, 3 routes each) -- verified directly against
    # data/practice.inpx. Its <vehicleRouteStatic> elements carry no
    # <linkSeq> child at all (unlike a real Vissim export), so every route
    # parses as disconnected=False (vacuously — nothing to check) and
    # link_seq == [].
    envelope = _dispatch("get_routing_decisions")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 2
    first = next(d for d in data["routing_decisions"] if d["no"] == "1")
    assert first["link"] == "101"
    assert {r["route_no"] for r in first["routes"]} == {"1", "2", "3"}
    for route in first["routes"]:
        assert set(route.keys()) == {
            "route_no",
            "dest_link",
            "dest_pos",
            "rel_flow",
            "interval_count",
            "rel_flows",
            "link_seq",
            "disconnected",
        }
        assert route["link_seq"] == []
        assert route["disconnected"] is False
    route_1 = next(r for r in first["routes"] if r["route_no"] == "1")
    assert route_1["dest_link"] == "140"
    assert route_1["rel_flow"] == 780.0
    assert route_1["interval_count"] == 1
    assert route_1["rel_flows"] == [{"interval_no": 1, "from_s": 0.0, "rel_flow": 780.0}]


def test_get_routing_decisions_rejects_unexpected_params():
    envelope = _dispatch("get_routing_decisions", bogus=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


# Synthetic fixture with real <linkSeq> children (the demo .inpx has none) --
# covers multi-interval relFlow, a single-route decision, and a genuinely
# disconnected route (a linkSeq key that is NOT among the network's links).
_ROUTING_DECISION_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links>
    <link no="101" name="Approach"><lanes><lane width="3.5"/></lanes></link>
    <link no="140" name="Exit A"><lanes><lane width="3.5"/></lanes></link>
    <link no="141" name="Exit B"><lanes><lane width="3.5"/></lanes></link>
  </links>
  <timeIntervalSets>
    <timeIntervalSet no="VEHICLEROUTESTATIC">
      <timeIntervals>
        <timeInterval start="0"/>
        <timeInterval start="5400"/>
      </timeIntervals>
    </timeIntervalSet>
  </timeIntervalSets>
  <vehicleRoutingDecisionsStatic>
    <vehicleRoutingDecisionStatic no="1" name="Main decision" link="101" pos="1.0">
      <vehRoutSta>
        <vehicleRouteStatic no="1" name="" destLink="140" destPos="2.0" formula=""
          relFlow="2 0:45, 2 5400000:45">
          <linkSeq>
            <intObjectRef key="101"/>
            <intObjectRef key="140"/>
          </linkSeq>
        </vehicleRouteStatic>
        <vehicleRouteStatic no="2" name="" destLink="141" destPos="3.0" formula=""
          relFlow="2 0:55">
          <linkSeq>
            <intObjectRef key="101"/>
            <intObjectRef key="9999"/>
            <intObjectRef key="141"/>
          </linkSeq>
        </vehicleRouteStatic>
      </vehRoutSta>
    </vehicleRoutingDecisionStatic>
    <vehicleRoutingDecisionStatic no="2" name="Single-route decision" link="101" pos="4.0">
      <vehRoutSta>
        <vehicleRouteStatic no="1" name="" destLink="140" destPos="5.0" formula=""
          relFlow="2 0:568">
          <linkSeq>
            <intObjectRef key="101"/>
            <intObjectRef key="140"/>
          </linkSeq>
        </vehicleRouteStatic>
      </vehRoutSta>
    </vehicleRoutingDecisionStatic>
  </vehicleRoutingDecisionsStatic>
</network>
"""


@pytest.fixture()
def routing_decision_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "routing_decisions.inpx"
    path.write_text(_ROUTING_DECISION_FIXTURE_INPX, encoding="utf-8")
    return path


def test_get_routing_decisions_parses_multi_interval_rel_flow(routing_decision_fixture_inpx: Path):
    tools_network.set_active_inpx(routing_decision_fixture_inpx)

    envelope = _dispatch("get_routing_decisions")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 2
    decision_1 = next(d for d in data["routing_decisions"] if d["no"] == "1")
    route_1 = next(r for r in decision_1["routes"] if r["route_no"] == "1")
    assert route_1["rel_flow"] == 45.0  # interval 1's value (backward-compat top-level field)
    assert route_1["interval_count"] == 2
    assert route_1["rel_flows"] == [
        {"interval_no": 1, "from_s": 0.0, "rel_flow": 45.0},
        {"interval_no": 2, "from_s": 5400.0, "rel_flow": 45.0},
    ]
    assert route_1["disconnected"] is False


def test_get_routing_decisions_flags_a_route_referencing_a_missing_link(
    routing_decision_fixture_inpx: Path,
):
    # route no=2 on decision 1's linkSeq references link 9999, which does not
    # exist in the fixture's <links> section -- the exact "broken route"
    # shape route_integrity_unverifiable (constraints.py #31) needs.
    tools_network.set_active_inpx(routing_decision_fixture_inpx)

    envelope = _dispatch("get_routing_decisions")

    data = envelope["data"]
    decision_1 = next(d for d in data["routing_decisions"] if d["no"] == "1")
    route_2 = next(r for r in decision_1["routes"] if r["route_no"] == "2")
    assert route_2["link_seq"] == ["101", "9999", "141"]
    assert route_2["disconnected"] is True

    route_1 = next(r for r in decision_1["routes"] if r["route_no"] == "1")
    assert route_1["disconnected"] is False  # a real sibling stays unaffected


def test_get_routing_decisions_single_route_decision(routing_decision_fixture_inpx: Path):
    tools_network.set_active_inpx(routing_decision_fixture_inpx)

    envelope = _dispatch("get_routing_decisions")

    data = envelope["data"]
    decision_2 = next(d for d in data["routing_decisions"] if d["no"] == "2")
    assert len(decision_2["routes"]) == 1
    assert decision_2["routes"][0]["rel_flow"] == 568.0


def test_build_shadow_model_flattens_routes_with_synthesized_ids(
    routing_decision_fixture_inpx: Path,
):
    shadow = tools_network.build_shadow_model(routing_decision_fixture_inpx)

    assert len(shadow["routes"]) == 3
    by_id = {r["id"]: r for r in shadow["routes"]}
    assert set(by_id) == {"1.1", "1.2", "2.1"}
    assert by_id["1.2"]["decision_no"] == "1"
    assert by_id["1.2"]["route_no"] == "2"
    assert by_id["1.2"]["disconnected"] is True
    # v2 unit ⑥: manually_scaled_this_turn is a documented, permanently-open
    # gap (turn-scoped orchestrator state with no XML representation) --
    # always False from a pure reparse.
    assert all(r["manually_scaled_this_turn"] is False for r in shadow["routes"])


def test_route_integrity_unverifiable_fires_for_real_against_a_broken_link_seq(
    routing_decision_fixture_inpx: Path,
):
    """End-to-end proof that v2 unit ⑥ actually WAKES constraints.py's
    dormant route_integrity_unverifiable (#31) check, not just that
    tools_network.py computes `disconnected` in isolation: feed a REAL
    build_shadow_model() output (this module's own production code path,
    not a hand-rolled constraints.py fixture) into the real
    ``services.agent.constraints.run_checks``."""
    from services.agent import constraints

    shadow = tools_network.build_shadow_model(routing_decision_fixture_inpx)
    action = {"tool": "set_route_relative_flow", "params": {}}

    findings = constraints.run_checks("postwrite", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "route_integrity_unverifiable"]
    assert len(matches) == 1
    assert "1.2" in matches[0].messages["en"]  # the synthesized id of the broken route


def test_resolve_object_finds_routing_decision_by_name(routing_decision_fixture_inpx: Path):
    tools_network.set_active_inpx(routing_decision_fixture_inpx)

    envelope = _dispatch("resolve_object", query="Single-route decision", object_type="routing_decision")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["found"] is True
    assert data["candidates"][0]["id"] == "2"
    assert data["candidates"][0]["object_type"] == "routing_decision"


def test_get_network_overview_includes_routing_decisions_section(
    routing_decision_fixture_inpx: Path,
):
    tools_network.set_active_inpx(routing_decision_fixture_inpx)

    envelope = _dispatch("get_network_overview")

    data = envelope["data"]
    assert data["routing_decisions"]["count"] == 2
    ids = {item["id"] for item in data["routing_decisions"]["items"]}
    assert ids == {"1", "2"}


# ---------------------------------------------------------------------------
# get_vehicle_compositions / _parse_vehicle_compositions — v2 unit ⑦
# ---------------------------------------------------------------------------


def test_get_vehicle_compositions_on_demo_data_is_empty_but_well_formed():
    # The demo .inpx carries no <vehicleComposition> elements at all -- the
    # tool must return an empty, well-formed list rather than erroring, same
    # shape as get_conflict_areas on the demo network (v2 unit ④).
    envelope = _dispatch("get_vehicle_compositions")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "vehicle_compositions": [], "truncated": False}
    assert "hint" not in envelope["data"]


_VEHICLE_COMPOSITION_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links>
    <link no="11" name="Border Rd"><lanes><lane width="3.5"/></lanes></link>
  </links>
  <vehicleCompositions>
    <vehicleComposition no="1" name="5% HGV, 5% LGV, 5% Bus">
      <vehCompRelFlows>
        <vehicleCompositionRelativeFlow desSpeedDistr="65" relFlow="0.85" vehType="100"/>
        <vehicleCompositionRelativeFlow desSpeedDistr="50" relFlow="0.05" vehType="190"/>
        <vehicleCompositionRelativeFlow desSpeedDistr="50" relFlow="0.05" vehType="200"/>
        <vehicleCompositionRelativeFlow desSpeedDistr="50" relFlow="0.05" vehType="300"/>
      </vehCompRelFlows>
    </vehicleComposition>
    <vehicleComposition no="4" name="Cycle">
      <vehCompRelFlows>
        <vehicleCompositionRelativeFlow desSpeedDistr="15" relFlow="1" vehType="600"/>
      </vehCompRelFlows>
    </vehicleComposition>
  </vehicleCompositions>
  <vehicleInputs>
    <vehicleInput anmFlag="false" link="11" name="" no="1">
      <timeIntVehVols>
        <timeIntervalVehVolume cont="false" timeInt="1 0" vehComp="1" volType="STOCHASTIC" volume="1838"/>
      </timeIntVehVols>
    </vehicleInput>
    <vehicleInput anmFlag="false" link="11" name="" no="2">
      <timeIntVehVols>
        <timeIntervalVehVolume cont="false" timeInt="1 0" vehComp="1" volType="STOCHASTIC" volume="1446"/>
      </timeIntVehVols>
    </vehicleInput>
  </vehicleInputs>
</network>
"""


@pytest.fixture()
def vehicle_composition_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "vehicle_compositions.inpx"
    path.write_text(_VEHICLE_COMPOSITION_FIXTURE_INPX, encoding="utf-8")
    return path


def test_get_vehicle_compositions_on_real_elements(vehicle_composition_fixture_inpx: Path):
    # Ground truth mirrors Urban Intersection Beijing.CN's real compositions
    # 1 and 4, per the v2 unit ⑦ live probe (backend/tools/
    # probe_vehicle_compositions.py).
    tools_network.set_active_inpx(vehicle_composition_fixture_inpx)

    envelope = _dispatch("get_vehicle_compositions")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 2
    by_no = {c["no"]: c for c in data["vehicle_compositions"]}

    comp_1 = by_no["1"]
    assert comp_1["name"] == "5% HGV, 5% LGV, 5% Bus"
    assert comp_1["referencing_vehicle_input_nos"] == ["1", "2"]
    entries_by_veh_type = {e["veh_type"]: e for e in comp_1["entries"]}
    assert entries_by_veh_type["100"]["rel_flow"] == 0.85
    assert entries_by_veh_type["100"]["des_speed_distr"] == "65"
    # estimated_share_pct is DERIVED (rel_flow / sum of this composition's
    # entries), not read verbatim off relFlow -- 0.85 / 1.0 == 85%.
    assert entries_by_veh_type["100"]["estimated_share_pct"] == 85.0
    assert entries_by_veh_type["190"]["estimated_share_pct"] == 5.0

    comp_4 = by_no["4"]
    assert comp_4["referencing_vehicle_input_nos"] == []  # nothing references "Cycle" in this fixture
    assert comp_4["entries"][0]["estimated_share_pct"] == 100.0  # only entry -> always 100%

    assert data["truncated"] is False
    assert "hint" not in data


def test_get_vehicle_compositions_filters_by_composition_no(vehicle_composition_fixture_inpx: Path):
    tools_network.set_active_inpx(vehicle_composition_fixture_inpx)

    envelope = _dispatch("get_vehicle_compositions", composition_no="4")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 1
    assert data["vehicle_compositions"][0]["no"] == "4"
    # A filtered call is already bounded by the model's own query -- no cap
    # bookkeeping needed (matches get_conflict_areas' precedent).
    assert "truncated" not in data
    assert "hint" not in data


def test_get_vehicle_compositions_filters_by_composition_no_no_match_is_empty_not_an_error(
    vehicle_composition_fixture_inpx: Path,
):
    tools_network.set_active_inpx(vehicle_composition_fixture_inpx)

    envelope = _dispatch("get_vehicle_compositions", composition_no="9999")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "vehicle_compositions": []}


def test_get_vehicle_compositions_rejects_unexpected_params(vehicle_composition_fixture_inpx: Path):
    tools_network.set_active_inpx(vehicle_composition_fixture_inpx)

    envelope = _dispatch("get_vehicle_compositions", bogus=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


_MANY_VEHICLE_COMPOSITIONS_INPX_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links></links>
  <vehicleCompositions>
{compositions}
  </vehicleCompositions>
</network>
"""


def _many_vehicle_compositions_inpx(tmp_path: Path, count: int) -> Path:
    compositions = "\n".join(
        f'    <vehicleComposition no="{i}" name="Comp {i}">'
        f'<vehCompRelFlows><vehicleCompositionRelativeFlow desSpeedDistr="50" relFlow="1" '
        f'vehType="100"/></vehCompRelFlows></vehicleComposition>'
        for i in range(1, count + 1)
    )
    path = tmp_path / f"many_vehicle_compositions_{count}.inpx"
    path.write_text(_MANY_VEHICLE_COMPOSITIONS_INPX_TEMPLATE.format(compositions=compositions), encoding="utf-8")
    return path


def test_get_vehicle_compositions_unfiltered_call_on_large_network_is_capped_and_truncated(
    tmp_path: Path,
):
    tools_network.set_active_inpx(_many_vehicle_compositions_inpx(tmp_path, 60))

    envelope = _dispatch("get_vehicle_compositions")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 60  # the TRUE total, never hidden
    assert len(data["vehicle_compositions"]) == tools_network._VEHICLE_COMPOSITIONS_DEFAULT_CAP
    assert data["truncated"] is True
    assert "40" in data["hint"] and "60" in data["hint"]
    followup = _dispatch("get_vehicle_compositions", composition_no="50")
    assert followup["data"]["count"] == 1
    assert followup["data"]["vehicle_compositions"][0]["no"] == "50"


def test_get_vehicle_compositions_unfiltered_call_under_cap_is_not_truncated(tmp_path: Path):
    tools_network.set_active_inpx(_many_vehicle_compositions_inpx(tmp_path, 5))

    envelope = _dispatch("get_vehicle_compositions")

    data = envelope["data"]
    assert data["count"] == 5
    assert len(data["vehicle_compositions"]) == 5
    assert data["truncated"] is False
    assert "hint" not in data


def test_build_shadow_model_flattens_vehicle_composition_entries_with_synthesized_ids(
    vehicle_composition_fixture_inpx: Path,
):
    shadow = tools_network.build_shadow_model(vehicle_composition_fixture_inpx)

    assert len(shadow["vehicle_composition_entries"]) == 5  # 4 entries on comp 1 + 1 on comp 4
    by_id = {e["id"]: e for e in shadow["vehicle_composition_entries"]}
    assert set(by_id) == {"1.100", "1.190", "1.200", "1.300", "4.600"}
    assert by_id["1.100"]["composition_no"] == "1"
    assert by_id["1.100"]["veh_type"] == "100"
    assert by_id["1.100"]["rel_flow"] == 0.85


def test_resolve_object_finds_vehicle_composition_by_name(vehicle_composition_fixture_inpx: Path):
    tools_network.set_active_inpx(vehicle_composition_fixture_inpx)

    envelope = _dispatch("resolve_object", query="Cycle", object_type="vehicle_composition")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["found"] is True
    assert data["candidates"][0]["id"] == "4"
    assert data["candidates"][0]["object_type"] == "vehicle_composition"


def test_get_network_overview_includes_vehicle_compositions_section(
    vehicle_composition_fixture_inpx: Path,
):
    tools_network.set_active_inpx(vehicle_composition_fixture_inpx)

    envelope = _dispatch("get_network_overview")

    data = envelope["data"]
    assert data["vehicle_compositions"]["count"] == 2
    ids = {item["id"] for item in data["vehicle_compositions"]["items"]}
    assert ids == {"1", "4"}


# ---------------------------------------------------------------------------
# get_des_speed_decisions / get_des_speed_distributions /
# _parse_des_speed_decisions — v2a unit ⑧ (the demo network has none; ground
# truth mirrors Roundabout Billings.US's real decisions 6/7 and speed
# distributions 25/50, per the v2a unit ⑧ live probe,
# backend/tools/probe_des_speed_decisions.py)
# ---------------------------------------------------------------------------


def test_get_des_speed_decisions_on_demo_data_is_empty_but_well_formed():
    envelope = _dispatch("get_des_speed_decisions")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "des_speed_decisions": [], "truncated": False}
    assert "hint" not in envelope["data"]


def test_get_des_speed_distributions_on_demo_data_is_empty_but_well_formed():
    envelope = _dispatch("get_des_speed_distributions")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "des_speed_distributions": [], "truncated": False}
    assert "hint" not in envelope["data"]


_DES_SPEED_DECISION_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links>
    <link no="6" name="Ring"><lanes><lane width="3.5"/></lanes></link>
  </links>
  <vehicleClasses>
    <vehicleClass no="10" name="Car"/>
    <vehicleClass no="20" name="HGV"/>
  </vehicleClasses>
  <desSpeedDecisions>
    <desSpeedDecision anmFlag="false" lane="6 1" name="" no="6" pos="108.744" timeFrom="0" timeTo="2147483647">
      <vehClassDesSpeedDistr>
        <vehClassDesSpeedDistribution desSpeedDistr="50" vehClass="10"/>
        <vehClassDesSpeedDistribution desSpeedDistr="50" vehClass="20"/>
      </vehClassDesSpeedDistr>
    </desSpeedDecision>
    <desSpeedDecision anmFlag="false" lane="6 1" name="" no="7" pos="200.0" timeFrom="0" timeTo="2147483647">
      <vehClassDesSpeedDistr>
        <vehClassDesSpeedDistribution desSpeedDistr="25" vehClass="10"/>
        <vehClassDesSpeedDistribution desSpeedDistr="25" vehClass="20"/>
      </vehClassDesSpeedDistr>
    </desSpeedDecision>
    <desSpeedDecision anmFlag="false" lane="9 1" name="" no="8" pos="254.77" timeFrom="0" timeTo="2147483647">
      <vehClassDesSpeedDistr>
        <vehClassDesSpeedDistribution desSpeedDistr="50" vehClass="10"/>
      </vehClassDesSpeedDistr>
    </desSpeedDecision>
  </desSpeedDecisions>
  <speedDistributions>
    <speedDistribution name="25 mph" no="25"/>
    <speedDistribution name="55 mph" no="50"/>
  </speedDistributions>
</network>
"""


@pytest.fixture()
def des_speed_decision_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "des_speed_decisions.inpx"
    path.write_text(_DES_SPEED_DECISION_FIXTURE_INPX, encoding="utf-8")
    return path


def test_get_des_speed_decisions_on_real_elements(des_speed_decision_fixture_inpx: Path):
    tools_network.set_active_inpx(des_speed_decision_fixture_inpx)

    envelope = _dispatch("get_des_speed_decisions")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 3
    by_no = {d["no"]: d for d in data["des_speed_decisions"]}

    dec_6 = by_no["6"]
    assert dec_6["link"] == "6"
    assert dec_6["lane"] == "1"
    assert dec_6["pos"] == 108.744
    assignments = {a["veh_class_no"]: a for a in dec_6["veh_class_assignments"]}
    assert assignments["10"] == {
        "veh_class_no": "10",
        "veh_class_name": "Car",
        "des_speed_distr_no": "50",
        "des_speed_distr_name": "55 mph",
    }
    assert assignments["20"]["veh_class_name"] == "HGV"

    dec_8 = by_no["8"]
    assert len(dec_8["veh_class_assignments"]) == 1  # only vehClass 10 assigned

    assert data["truncated"] is False
    assert "hint" not in data


def test_get_des_speed_decisions_filters_by_decision_no(des_speed_decision_fixture_inpx: Path):
    tools_network.set_active_inpx(des_speed_decision_fixture_inpx)

    envelope = _dispatch("get_des_speed_decisions", decision_no="7")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 1
    assert data["des_speed_decisions"][0]["no"] == "7"
    assert "truncated" not in data
    assert "hint" not in data


def test_get_des_speed_decisions_filters_by_decision_no_no_match_is_empty_not_an_error(
    des_speed_decision_fixture_inpx: Path,
):
    tools_network.set_active_inpx(des_speed_decision_fixture_inpx)

    envelope = _dispatch("get_des_speed_decisions", decision_no="9999")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "des_speed_decisions": []}


def test_get_des_speed_decisions_filters_by_link_no(des_speed_decision_fixture_inpx: Path):
    tools_network.set_active_inpx(des_speed_decision_fixture_inpx)

    envelope = _dispatch("get_des_speed_decisions", link_no="9")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 1
    assert data["des_speed_decisions"][0]["no"] == "8"


def test_get_des_speed_decisions_rejects_unexpected_params(des_speed_decision_fixture_inpx: Path):
    tools_network.set_active_inpx(des_speed_decision_fixture_inpx)

    envelope = _dispatch("get_des_speed_decisions", bogus=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


_MANY_DES_SPEED_DECISIONS_INPX_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links></links>
  <desSpeedDecisions>
{decisions}
  </desSpeedDecisions>
</network>
"""


def _many_des_speed_decisions_inpx(tmp_path: Path, count: int) -> Path:
    decisions = "\n".join(
        f'    <desSpeedDecision no="{i}" name="" lane="1 1" pos="0" timeFrom="0" '
        f'timeTo="2147483647"><vehClassDesSpeedDistr>'
        f'<vehClassDesSpeedDistribution desSpeedDistr="50" vehClass="10"/>'
        f"</vehClassDesSpeedDistr></desSpeedDecision>"
        for i in range(1, count + 1)
    )
    path = tmp_path / f"many_des_speed_decisions_{count}.inpx"
    path.write_text(_MANY_DES_SPEED_DECISIONS_INPX_TEMPLATE.format(decisions=decisions), encoding="utf-8")
    return path


def test_get_des_speed_decisions_unfiltered_call_on_large_network_is_capped_and_truncated(
    tmp_path: Path,
):
    tools_network.set_active_inpx(_many_des_speed_decisions_inpx(tmp_path, 60))

    envelope = _dispatch("get_des_speed_decisions")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 60  # the TRUE total, never hidden
    assert len(data["des_speed_decisions"]) == tools_network._DES_SPEED_DECISIONS_DEFAULT_CAP
    assert data["truncated"] is True
    assert "40" in data["hint"] and "60" in data["hint"]
    followup = _dispatch("get_des_speed_decisions", decision_no="50")
    assert followup["data"]["count"] == 1
    assert followup["data"]["des_speed_decisions"][0]["no"] == "50"


def test_get_des_speed_distributions_on_real_elements(des_speed_decision_fixture_inpx: Path):
    tools_network.set_active_inpx(des_speed_decision_fixture_inpx)

    envelope = _dispatch("get_des_speed_distributions")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 2
    by_no = {d["no"]: d for d in data["des_speed_distributions"]}
    assert by_no["25"]["name"] == "25 mph"
    assert by_no["50"]["name"] == "55 mph"
    assert data["truncated"] is False
    assert "hint" not in data


def test_get_des_speed_distributions_filters_by_distribution_no(des_speed_decision_fixture_inpx: Path):
    tools_network.set_active_inpx(des_speed_decision_fixture_inpx)

    envelope = _dispatch("get_des_speed_distributions", distribution_no="25")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 1
    assert data["des_speed_distributions"][0]["name"] == "25 mph"
    assert "truncated" not in data


def test_get_des_speed_distributions_rejects_unexpected_params(des_speed_decision_fixture_inpx: Path):
    tools_network.set_active_inpx(des_speed_decision_fixture_inpx)

    envelope = _dispatch("get_des_speed_distributions", bogus=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_build_shadow_model_flattens_des_speed_decision_entries_with_synthesized_ids(
    des_speed_decision_fixture_inpx: Path,
):
    shadow = tools_network.build_shadow_model(des_speed_decision_fixture_inpx)

    assert len(shadow["des_speed_decision_entries"]) == 5  # 2+2+1 assignments
    by_id = {e["id"]: e for e in shadow["des_speed_decision_entries"]}
    assert set(by_id) == {"6.10", "6.20", "7.10", "7.20", "8.10"}
    assert by_id["6.10"]["decision_no"] == "6"
    assert by_id["6.10"]["veh_class_no"] == "10"
    assert by_id["6.10"]["des_speed_distr_no"] == "50"


def test_resolve_object_finds_des_speed_decision_by_id(des_speed_decision_fixture_inpx: Path):
    tools_network.set_active_inpx(des_speed_decision_fixture_inpx)

    envelope = _dispatch("resolve_object", query="7", object_type="des_speed_decision")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["found"] is True
    assert data["candidates"][0]["id"] == "7"
    assert data["candidates"][0]["object_type"] == "des_speed_decision"


def test_get_network_overview_includes_des_speed_decisions_section(
    des_speed_decision_fixture_inpx: Path,
):
    tools_network.set_active_inpx(des_speed_decision_fixture_inpx)

    envelope = _dispatch("get_network_overview")

    data = envelope["data"]
    assert data["des_speed_decisions"]["count"] == 3
    ids = {item["id"] for item in data["des_speed_decisions"]["items"]}
    assert ids == {"6", "7", "8"}


# ---------------------------------------------------------------------------
# get_vehicle_types / _parse_vehicle_types -- QUEUE (v2a) unit (the demo
# network has none; ground truth mirrors Roundabout Billings.US's real
# vehicle types 100/200/510, per the live probe,
# backend/tools/probe_vehicle_types.py). Car (100) and HGV (200) carry every
# reference field; Man (510, PEDESTRIAN) is missing weight_distr/power_distr/
# des_curve_speed_func entirely (the live-verified nullable-per-category
# finding) AND carries a model2D3DDistr (100) with NO matching
# <model2D3DDistribution> element in this fixture -- a deliberately
# UNRESOLVABLE reference, exercising _resolve_vehicle_type_reference's
# defensive "unresolved" branch.
# ---------------------------------------------------------------------------


def test_get_vehicle_types_on_demo_data_is_empty_but_well_formed():
    envelope = _dispatch("get_vehicle_types")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "vehicle_types": [], "truncated": False}
    assert "hint" not in envelope["data"]


_VEHICLE_TYPE_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <vehicleTypes>
    <vehicleType no="100" name="Car" category="CAR" capacity="0" capExact="false"
      model2D3DDistr="10" weightDistr="1" powerDistr="1"
      desAccelFunc="1" maxAccelFunc="1" desDecelFunc="1" maxDecelFunc="1" desCurveSpeedFunc="1"/>
    <vehicleType no="200" name="HGV" category="HGV" capacity="0" capExact="false"
      model2D3DDistr="20" weightDistr="2" powerDistr="2"
      desAccelFunc="2" maxAccelFunc="2" desDecelFunc="2" maxDecelFunc="2" desCurveSpeedFunc="11"/>
    <vehicleType no="510" name="Man" category="PEDESTRIAN" capacity="0" capExact="true"
      model2D3DDistr="100" desAccelFunc="5" maxAccelFunc="5" desDecelFunc="5" maxDecelFunc="5"/>
  </vehicleTypes>
  <vehicleCompositions>
    <vehicleComposition no="1" name="Mix">
      <vehCompRelFlows>
        <vehicleCompositionRelativeFlow vehType="100" desSpeedDistr="50" relFlow="0.9"/>
        <vehicleCompositionRelativeFlow vehType="200" desSpeedDistr="50" relFlow="0.1"/>
      </vehCompRelFlows>
    </vehicleComposition>
  </vehicleCompositions>
  <model2D3DDistributions>
    <model2D3DDistribution no="10" name="Car">
      <model2D3DDistrEl>
        <model2D3DDistributionElement model2D3D="1" share="0.6"/>
        <model2D3DDistributionElement model2D3D="2" share="0.4"/>
      </model2D3DDistrEl>
    </model2D3DDistribution>
    <model2D3DDistribution no="20" name="HGV">
      <model2D3DDistrEl>
        <model2D3DDistributionElement model2D3D="3" share="1"/>
      </model2D3DDistrEl>
    </model2D3DDistribution>
  </model2D3DDistributions>
  <models2D3D>
    <model2D3D no="1" name="Car A">
      <model2D3DSegs><model2D3DSegment length="4.5"/></model2D3DSegs>
    </model2D3D>
    <model2D3D no="2" name="Car B">
      <model2D3DSegs><model2D3DSegment length="5.5"/></model2D3DSegs>
    </model2D3D>
    <model2D3D no="3" name="Truck">
      <model2D3DSegs><model2D3DSegment length="12.0"/></model2D3DSegs>
    </model2D3D>
  </models2D3D>
  <weightDistributions>
    <weightDistribution no="1" name="">
      <distrDataPts><distributionDataPoint fx="0" x="700"/><distributionDataPoint fx="1" x="1500"/></distrDataPts>
    </weightDistribution>
    <weightDistribution no="2" name="">
      <distrDataPts><distributionDataPoint fx="0" x="2800"/><distributionDataPoint fx="1" x="40000"/></distrDataPts>
    </weightDistribution>
  </weightDistributions>
  <powerDistributions>
    <powerDistribution no="1" name="">
      <distrDataPts><distributionDataPoint fx="0" x="50"/><distributionDataPoint fx="1" x="120"/></distrDataPts>
    </powerDistribution>
    <powerDistribution no="2" name="">
      <distrDataPts><distributionDataPoint fx="0" x="100"/><distributionDataPoint fx="1" x="500"/></distrDataPts>
    </powerDistribution>
  </powerDistributions>
  <desAccelerationFunctions>
    <desAccelerationFunction no="1" name="Car"/>
    <desAccelerationFunction no="2" name="HGV"/>
    <desAccelerationFunction no="5" name="Ped"/>
  </desAccelerationFunctions>
  <maxAccelerationFunctions>
    <maxAccelerationFunction no="1" name="Car"/>
    <maxAccelerationFunction no="2" name="HGV"/>
    <maxAccelerationFunction no="5" name="Ped"/>
  </maxAccelerationFunctions>
  <desDecelerationFunctions>
    <desDecelerationFunction no="1" name="Car"/>
    <desDecelerationFunction no="2" name="HGV"/>
    <desDecelerationFunction no="5" name="Ped"/>
  </desDecelerationFunctions>
  <maxDecelerationFunctions>
    <maxDecelerationFunction no="1" name="Car"/>
    <maxDecelerationFunction no="2" name="HGV"/>
    <maxDecelerationFunction no="5" name="Ped"/>
  </maxDecelerationFunctions>
  <curvSpeedFuncs>
    <curvSpeedFunc no="1" name="Road. Car."/>
    <curvSpeedFunc no="11" name="HGV curve"/>
  </curvSpeedFuncs>
</network>
"""


@pytest.fixture()
def vehicle_type_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "vehicle_types.inpx"
    path.write_text(_VEHICLE_TYPE_FIXTURE_INPX, encoding="utf-8")
    return path


def test_get_vehicle_types_on_real_elements(vehicle_type_fixture_inpx: Path):
    tools_network.set_active_inpx(vehicle_type_fixture_inpx)

    envelope = _dispatch("get_vehicle_types")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 3
    by_no = {vt["no"]: vt for vt in data["vehicle_types"]}

    car = by_no["100"]
    assert car["name"] == "Car"
    assert car["category"] == "CAR"
    assert car["cap_exact"] is False
    assert car["weight_distr_no"] == "1"
    assert car["references"]["weight_distr"] == {
        "no": "1", "name": "", "min": 700.0, "max": 1500.0, "mean": 1100.0, "point_count": 2,
    }
    assert car["references"]["power_distr"] == {
        "no": "1", "name": "", "min": 50.0, "max": 120.0, "mean": 85.0, "point_count": 2,
    }
    assert car["references"]["des_accel_func"] == {"no": "1", "name": "Car"}
    assert car["references"]["des_curve_speed_func"] == {"no": "1", "name": "Road. Car."}
    model2d3d = car["references"]["model2d3d_distr"]
    assert model2d3d["no"] == "10"
    assert model2d3d["model_count"] == 2
    assert model2d3d["length_min_m"] == 4.5
    assert model2d3d["length_max_m"] == 5.5
    assert model2d3d["length_mean_m"] == 4.9  # (4.5*0.6 + 5.5*0.4) / 1.0
    # Car (vehType=100) is ALSO one of composition no=1's entries.
    assert car["referencing_composition_nos"] == {"count": 1, "ids": ["1"], "truncated": False}

    hgv = by_no["200"]
    assert hgv["references"]["model2d3d_distr"]["length_mean_m"] == 12.0
    assert hgv["references"]["des_curve_speed_func"] == {"no": "11", "name": "HGV curve"}
    assert hgv["referencing_composition_nos"] == {"count": 1, "ids": ["1"], "truncated": False}

    # QUEUE (v2a) unit's own nullable-per-category live finding: PEDESTRIAN
    # types carry no weight_distr/power_distr/des_curve_speed_func at all.
    man = by_no["510"]
    assert man["category"] == "PEDESTRIAN"
    assert man["cap_exact"] is True
    assert man["weight_distr_no"] is None
    assert man["references"]["weight_distr"] is None
    assert man["references"]["power_distr"] is None
    assert man["references"]["des_curve_speed_func"] is None
    # model2D3DDistr="100" has no matching <model2D3DDistribution> in this
    # fixture -- a dangling reference resolves to "unresolved", never a crash.
    assert man["references"]["model2d3d_distr"] == {"no": "100", "name": None, "unresolved": True}

    assert data["truncated"] is False
    assert "hint" not in data


def test_get_vehicle_types_filters_by_vehicle_type_no(vehicle_type_fixture_inpx: Path):
    tools_network.set_active_inpx(vehicle_type_fixture_inpx)

    envelope = _dispatch("get_vehicle_types", vehicle_type_no="200")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 1
    assert data["vehicle_types"][0]["no"] == "200"


def test_get_vehicle_types_filters_by_vehicle_type_no_no_match_is_empty_not_an_error(
    vehicle_type_fixture_inpx: Path,
):
    tools_network.set_active_inpx(vehicle_type_fixture_inpx)

    envelope = _dispatch("get_vehicle_types", vehicle_type_no="9999")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "vehicle_types": []}


def test_get_vehicle_types_rejects_unexpected_params(vehicle_type_fixture_inpx: Path):
    tools_network.set_active_inpx(vehicle_type_fixture_inpx)

    envelope = _dispatch("get_vehicle_types", bogus=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def _many_vehicle_types_inpx(tmp_path: Path, count: int) -> Path:
    types = "\n".join(
        f'<vehicleType no="{i}" name="Type{i}" category="CAR" capacity="0" capExact="false"/>'
        for i in range(1, count + 1)
    )
    xml = f"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<network>\n  <vehicleTypes>\n{types}\n  </vehicleTypes>\n</network>\n"
    path = tmp_path / f"many_vehicle_types_{count}.inpx"
    path.write_text(xml, encoding="utf-8")
    return path


def test_get_vehicle_types_unfiltered_call_on_large_network_is_capped_and_truncated(tmp_path: Path):
    tools_network.set_active_inpx(_many_vehicle_types_inpx(tmp_path, 60))

    envelope = _dispatch("get_vehicle_types")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 60
    assert len(data["vehicle_types"]) == tools_network._VEHICLE_TYPES_DEFAULT_CAP
    assert data["truncated"] is True
    assert "hint" in data

    followup = _dispatch("get_vehicle_types", vehicle_type_no="50")
    assert followup["data"]["vehicle_types"][0]["no"] == "50"


def test_resolve_object_finds_vehicle_type_by_name(vehicle_type_fixture_inpx: Path):
    tools_network.set_active_inpx(vehicle_type_fixture_inpx)

    envelope = _dispatch("resolve_object", query="HGV", object_type="vehicle_type")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["candidates"][0]["id"] == "200"
    assert data["candidates"][0]["object_type"] == "vehicle_type"


def test_get_network_overview_includes_vehicle_types_section(vehicle_type_fixture_inpx: Path):
    tools_network.set_active_inpx(vehicle_type_fixture_inpx)

    envelope = _dispatch("get_network_overview")

    data = envelope["data"]
    assert data["vehicle_types"]["count"] == 3
    ids = {item["id"] for item in data["vehicle_types"]["items"]}
    assert ids == {"100", "200", "510"}


def test_build_shadow_model_populates_all_eight_vehicle_type_target_collections(
    vehicle_type_fixture_inpx: Path,
):
    shadow = tools_network.build_shadow_model(vehicle_type_fixture_inpx)

    assert {d["no"] for d in shadow["model2d3d_distributions"]} == {"10", "20"}
    assert {d["no"] for d in shadow["weight_distributions"]} == {"1", "2"}
    assert {d["no"] for d in shadow["power_distributions"]} == {"1", "2"}
    assert {d["no"] for d in shadow["des_accel_functions"]} == {"1", "2", "5"}
    assert {d["no"] for d in shadow["max_accel_functions"]} == {"1", "2", "5"}
    assert {d["no"] for d in shadow["des_decel_functions"]} == {"1", "2", "5"}
    assert {d["no"] for d in shadow["max_decel_functions"]} == {"1", "2", "5"}
    assert {d["no"] for d in shadow["curve_speed_functions"]} == {"1", "11"}


# ---------------------------------------------------------------------------
# get_des_speed_distributions — points + reference counts, QUEUE 3 (v2a)
# unit 3's own extension of unit ⑧'s {no, name}-only shape. Fixture wires up
# ALL FOUR reference categories at once (des_speed_decisions,
# vehicle_compositions, reduced_speed_areas, "other" via <ptLine>), plus a
# genuinely unreferenced distribution (no=99) to prove reference_count == 0
# is reported honestly, not omitted.
# ---------------------------------------------------------------------------

_DES_SPEED_DISTRIBUTION_POINTS_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links>
    <link no="6" name="Ring"><lanes><lane width="3.5"/></lanes></link>
  </links>
  <vehicleClasses>
    <vehicleClass no="10" name="Car"/>
  </vehicleClasses>
  <desSpeedDecisions>
    <desSpeedDecision anmFlag="false" lane="6 1" name="" no="6" pos="108.744" timeFrom="0" timeTo="2147483647">
      <vehClassDesSpeedDistr>
        <vehClassDesSpeedDistribution desSpeedDistr="50" vehClass="10"/>
      </vehClassDesSpeedDistr>
    </desSpeedDecision>
  </desSpeedDecisions>
  <vehicleCompositions>
    <vehicleComposition no="1" name="Standard">
      <vehCompRelFlows>
        <vehicleCompositionRelativeFlow desSpeedDistr="50" relFlow="0.9" vehType="100"/>
      </vehCompRelFlows>
    </vehicleComposition>
  </vehicleCompositions>
  <reducedSpeedAreas>
    <reducedSpeedArea anmFlag="false" lane="6 1" length="10" name="rsa1" no="1" pos="0" timeFrom="0" timeTo="2147483647">
      <vehClassSpeedRed>
        <vehClassSpeedReduction decel="2" desSpeedDistr="25" vehClass="10"/>
      </vehClassSpeedRed>
    </reducedSpeedArea>
  </reducedSpeedAreas>
  <ptLine desSpeedDistr="50" name="Line 1" no="1"/>
  <speedDistributions>
    <speedDistribution name="25 mph" no="25">
      <speedDistrDatPts>
        <speedDistributionDataPoint fx="0" x="0"/>
        <speedDistributionDataPoint fx="0.5" x="20"/>
        <speedDistributionDataPoint fx="1" x="40.2"/>
      </speedDistrDatPts>
    </speedDistribution>
    <speedDistribution name="55 mph" no="50">
      <speedDistrDatPts>
        <speedDistributionDataPoint fx="0" x="0"/>
        <speedDistributionDataPoint fx="1" x="88.51"/>
      </speedDistrDatPts>
    </speedDistribution>
    <speedDistribution name="unused" no="99">
      <speedDistrDatPts>
        <speedDistributionDataPoint fx="0" x="0"/>
        <speedDistributionDataPoint fx="1" x="10"/>
      </speedDistrDatPts>
    </speedDistribution>
  </speedDistributions>
</network>
"""


@pytest.fixture()
def des_speed_distribution_points_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "des_speed_distribution_points.inpx"
    path.write_text(_DES_SPEED_DISTRIBUTION_POINTS_FIXTURE_INPX, encoding="utf-8")
    return path


def test_get_des_speed_distributions_includes_points(
    des_speed_distribution_points_fixture_inpx: Path,
):
    tools_network.set_active_inpx(des_speed_distribution_points_fixture_inpx)

    envelope = _dispatch("get_des_speed_distributions")

    assert envelope["status"] == "ok"
    by_no = {d["no"]: d for d in envelope["data"]["des_speed_distributions"]}

    dist_25 = by_no["25"]
    assert dist_25["points"] == [
        {"speed_kmh": 0.0, "cum_fraction": 0.0},
        {"speed_kmh": 20.0, "cum_fraction": 0.5},
        {"speed_kmh": 40.2, "cum_fraction": 1.0},
    ]
    assert dist_25["point_count"] == 3
    assert dist_25["points_truncated"] is False

    dist_50 = by_no["50"]
    assert dist_50["points"] == [
        {"speed_kmh": 0.0, "cum_fraction": 0.0},
        {"speed_kmh": 88.51, "cum_fraction": 1.0},
    ]
    assert dist_50["point_count"] == 2


def test_get_des_speed_distributions_reference_counts_all_four_categories(
    des_speed_distribution_points_fixture_inpx: Path,
):
    tools_network.set_active_inpx(des_speed_distribution_points_fixture_inpx)

    envelope = _dispatch("get_des_speed_distributions")
    by_no = {d["no"]: d for d in envelope["data"]["des_speed_distributions"]}

    # no=50: referenced by ONE desired-speed decision entry, ONE vehicle-
    # composition entry, and ONE "other" element (ptLine) -- zero reduced
    # speed areas.
    dist_50 = by_no["50"]
    refs_50 = dist_50["referenced_by"]
    assert refs_50["des_speed_decisions"] == {"count": 1, "ids": ["6.10"], "truncated": False}
    assert refs_50["vehicle_compositions"] == {"count": 1, "ids": ["1.100"], "truncated": False}
    assert refs_50["reduced_speed_areas"] == {"count": 0, "ids": [], "truncated": False}
    assert refs_50["other"] == {"count": 1, "by_tag": {"ptLine": 1}}
    assert dist_50["reference_count"] == 3

    # no=25: referenced by ONE reduced speed area only.
    dist_25 = by_no["25"]
    refs_25 = dist_25["referenced_by"]
    assert refs_25["reduced_speed_areas"] == {"count": 1, "ids": ["1"], "truncated": False}
    assert refs_25["des_speed_decisions"] == {"count": 0, "ids": [], "truncated": False}
    assert refs_25["vehicle_compositions"] == {"count": 0, "ids": [], "truncated": False}
    assert refs_25["other"] == {"count": 0, "by_tag": {}}
    assert dist_25["reference_count"] == 1

    # no=99: genuinely unreferenced -- reported honestly as zero, not omitted.
    dist_99 = by_no["99"]
    assert dist_99["reference_count"] == 0
    refs_99 = dist_99["referenced_by"]
    assert refs_99["des_speed_decisions"]["count"] == 0
    assert refs_99["vehicle_compositions"]["count"] == 0
    assert refs_99["reduced_speed_areas"]["count"] == 0
    assert refs_99["other"]["count"] == 0


def test_get_des_speed_distributions_points_are_capped_and_truncated_on_a_huge_curve(
    tmp_path: Path,
):
    many_points = "".join(
        f'<speedDistributionDataPoint fx="{i / 40:.4f}" x="{i}"/>' for i in range(41)
    )
    inpx = f"""<?xml version="1.0" encoding="UTF-8"?>
<network>
  <speedDistributions>
    <speedDistribution name="huge" no="1">
      <speedDistrDatPts>{many_points}</speedDistrDatPts>
    </speedDistribution>
  </speedDistributions>
</network>
"""
    path = tmp_path / "huge_distribution.inpx"
    path.write_text(inpx, encoding="utf-8")
    tools_network.set_active_inpx(path)

    envelope = _dispatch("get_des_speed_distributions")

    dist = envelope["data"]["des_speed_distributions"][0]
    assert dist["point_count"] == 41
    assert len(dist["points"]) == tools_network._SPEED_DISTRIBUTION_POINTS_CAP
    assert dist["points_truncated"] is True


def test_get_des_speed_distributions_backward_compatible_with_no_points_at_all(
    des_speed_decision_fixture_inpx: Path,
):
    # v2a unit ⑧'s ORIGINAL fixture has <speedDistribution> elements with NO
    # <speedDistrDatPts> child at all -- the extension must degrade to an
    # empty points list, never crash or omit the new keys.
    tools_network.set_active_inpx(des_speed_decision_fixture_inpx)

    envelope = _dispatch("get_des_speed_distributions")

    assert envelope["status"] == "ok"
    for dist in envelope["data"]["des_speed_distributions"]:
        assert dist["points"] == []
        assert dist["point_count"] == 0
        assert dist["points_truncated"] is False
        assert "referenced_by" in dist
        assert "reference_count" in dist


# ---------------------------------------------------------------------------
# build_shadow_model — the extended shadow model builder (unit-level)
# ---------------------------------------------------------------------------


def test_build_shadow_model_has_exactly_the_contract_keys():
    shadow = tools_network.build_shadow_model(Path(tools_network._active_inpx_path()))

    assert set(shadow.keys()) == {
        "links",
        "nodes",
        "vehicle_inputs",
        "signals",
        "conflict_areas",
        "routing_decisions",
        "routes",
        "vehicle_compositions",
        "vehicle_composition_entries",
        "des_speed_decisions",
        "des_speed_decision_entries",
        "des_speed_distributions",
        # v2a unit ⑨
        "signal_programs",
        "signal_program_splits",
        # health-check unit (2026-07-28): static evaluation-armed flags.
        "evaluation_config",
        # QUEUE 3 unit 2
        "priority_rules",
        "priority_rule_markers",
        # QUEUE (v2a) unit: vehicle type properties.
        "vehicle_types",
        "model2d3d_distributions",
        "weight_distributions",
        "power_distributions",
        "des_accel_functions",
        "max_accel_functions",
        "des_decel_functions",
        "max_decel_functions",
        "curve_speed_functions",
        # QUEUE (Q3, v2a) unit 5: dwell-time distributions.
        "time_distributions",
        # QUEUE 3 unit 6: driving-behavior duplicate-then-edit.
        "link_behavior_types",
        "driving_behaviors",
        "link_behavior_type_assignments",
        # v2b unit (1): flat per-lane closure state.
        "link_lanes",
        # Q4 unit (2): measurement-infrastructure placement.
        "queue_counters",
        "data_collection_points",
        "data_collection_measurements",
        "travel_time_sections",
        # Q4 unit (4): signal-head + detector placement.
        "signal_heads",
        "detectors",
        # Q4 unit (6): stop-sign placement.
        "stop_signs",
        # Q4 unit (7): PT stop + PT line placement.
        "pt_stops",
        "pt_lines",
        "pt_line_stops",
        # Q6 unit (2): parking lots. The representation half of that unit --
        # before it, build_shadow_model had no parking-lot class at all, so a
        # parking verb's postwrite diff could not see its own object (the Q4
        # unit (4) signal-head precedent: an object class the diff cannot see
        # makes the verb unverifiable by construction). Zones, parking-lot
        # groups and parking ROUTES are deliberately NOT shadow classes -- no
        # verb writes them, and an unwritten class only grows the
        # nothing-else-moved diff's false-positive surface; parking routes are
        # instead consulted straight from the XML by
        # inpx_structure.ID_REFERENCE_SITES["parking_lots"].
        "parking_lots",
        # Q6 unit (3): pedestrians (Viswalk). Two classes, because the
        # verbs write inputs and the refuse-tier guard reads areas.
        "pedestrian_inputs",
        "pedestrian_areas",
    }
    assert len(shadow["links"]) == 16  # 14 real + 2 Phase 3 connectors
    assert len(shadow["nodes"]) == 2
    assert len(shadow["vehicle_inputs"]) == 6
    assert shadow["signals"] == []
    # v2 unit ④: the demo .inpx carries no <conflictArea> elements at all —
    # the shadow key must still be present and well-formed (empty list, not
    # absent/None), the same "no signal controllers" shape signals already has.
    assert shadow["conflict_areas"] == []
    # health-check unit (2026-07-28): the demo .inpx carries no <evaluation>
    # element either -- a genuine, not-fabricated "no evaluation armed" fact
    # about the bundled demo network.
    assert shadow["evaluation_config"] == {"present": False}
    # v2 unit ⑥: unlike signals/conflict_areas, the demo .inpx DOES carry
    # static routing decisions (2 decisions, 3 routes each = 6 flat routes) —
    # verified directly against data/practice.inpx before pinning these
    # numbers.
    assert len(shadow["routing_decisions"]) == 2
    assert len(shadow["routes"]) == 6
    assert {r["route_no"] for r in shadow["routing_decisions"][0]["routes"]} == {"1", "2", "3"}
    # the demo .inpx's <vehicleRouteStatic> elements carry no <linkSeq> child
    # at all (unlike a real Vissim export) -- every route parses as NOT
    # disconnected (empty link_seq is vacuously "connected", not unknown).
    assert all(r["disconnected"] is False for r in shadow["routes"])
    # v2 unit ⑦: the demo .inpx carries no <vehicleComposition> elements at
    # all -- both shadow keys must still be present and well-formed (empty
    # list, not absent/None), same shape as signals/conflict_areas.
    assert shadow["vehicle_compositions"] == []
    assert shadow["vehicle_composition_entries"] == []
    # QUEUE (v2a) unit: the demo .inpx carries no <vehicleType> (or any of
    # the 8 target-collection) elements at all -- every one of these 9 shadow
    # keys must still be present and well-formed (empty list, not
    # absent/None), same shape as signals/conflict_areas/vehicle_compositions.
    assert shadow["vehicle_types"] == []
    assert shadow["model2d3d_distributions"] == []
    assert shadow["weight_distributions"] == []
    assert shadow["power_distributions"] == []
    assert shadow["des_accel_functions"] == []
    assert shadow["max_accel_functions"] == []
    assert shadow["des_decel_functions"] == []
    assert shadow["max_decel_functions"] == []
    assert shadow["curve_speed_functions"] == []
    # v2b unit (1): the demo .inpx's 16 links carry 40 lanes total (verified
    # directly against data/practice.inpx before pinning); none carry a
    # <blockedVehClasses> child (no real network ever does today — see
    # _parse_link_lanes's own docstring), so every entry's own field reads
    # the empty-list "open" default, never absent/None.
    assert len(shadow["link_lanes"]) == 40
    assert all(entry["blocked_vehicle_classes"] == [] for entry in shadow["link_lanes"])
    assert {entry["id"] for entry in shadow["link_lanes"] if entry["link_id"] == "101"} == {
        "101.1", "101.2", "101.3",
    }
    # Q4 unit (2): the demo .inpx carries no <queueCounters>/
    # <dataCollectionPoints>/<dataCollectionMeasurements> elements at all --
    # all three shadow keys must still be present and well-formed (empty
    # list, not absent/None), same shape as signals/conflict_areas/
    # vehicle_compositions. It DOES carry two <vehicleTravelTimeMeasurement>
    # placeholders (no="1"/"2", "Main St EB"/"Main St WB") -- pre-existing
    # fixture content, verified directly against data/practice.inpx before
    # pinning -- but each is a bare, childless element with no <start>/<end>
    # at all (unlike a real Vissim export), so every from_link/to_link/
    # from_pos/to_pos degrades to the empty/None "unresolvable" shape rather
    # than being fabricated.
    assert shadow["queue_counters"] == []
    assert shadow["data_collection_points"] == []
    assert shadow["data_collection_measurements"] == []
    assert [s["no"] for s in shadow["travel_time_sections"]] == ["1", "2"]
    assert all(
        s["from_link"] == "" and s["from_pos"] is None and s["to_link"] == "" and s["to_pos"] is None
        for s in shadow["travel_time_sections"]
    )
    # Q4 unit (4): the demo .inpx carries no <signalHeads>/<detectors>
    # elements at all -- both shadow keys must still be present and
    # well-formed (empty list, not absent/None), same shape as
    # signals/conflict_areas/queue_counters above.
    assert shadow["signal_heads"] == []
    assert shadow["detectors"] == []
    assert shadow["stop_signs"] == []


def test_build_shadow_model_is_mtime_cached_across_repeat_calls():
    path = tools_network._active_inpx_path()
    first = tools_network.build_shadow_model(path)
    second = tools_network.build_shadow_model(path)
    assert first is second  # same lru_cache hit, not just equal


# ---------------------------------------------------------------------------
# Q4 unit (2): measurement-infrastructure placement parsers, direct-tested
# against synthetic XML fixtures matching the REAL shapes confirmed live/
# statically across every real Vissim 2026 example network that uses one
# (backend/tools/probe_measurement_infra.py, Toll Plaza Camden.US -- e.g.
# ``<queueCounter link="10043" name="" no="1" pos="10.31" />``) -- the demo
# .inpx carries none of these (or, for travel-time sections, only degenerate
# placeholders -- see the pin test above), so a hand-built fixture is the
# only way to exercise the populated-list code paths at all.
# ---------------------------------------------------------------------------


def test_parse_queue_counters_reads_link_and_pos():
    root = ET.fromstring(
        '<network><queueCounters>'
        '<queueCounter link="10043" name="" no="1" pos="10.31" />'
        '<queueCounter link="6" name="Harbor Rd" no="2" pos="41.057" />'
        '</queueCounters></network>'
    )
    result = tools_network._parse_queue_counters(root)
    assert result == [
        {"no": "1", "name": "", "link_id": "10043", "pos": 10.31},
        {"no": "2", "name": "Harbor Rd", "link_id": "6", "pos": 41.057},
    ]


def test_parse_queue_counters_empty_when_section_absent():
    root = ET.fromstring("<network/>")
    assert tools_network._parse_queue_counters(root) == []


def test_parse_data_collection_points_reads_lane_token_and_pos():
    root = ET.fromstring(
        '<network><dataCollectionPoints>'
        '<dataCollectionPoint lane="10005 1" name="" no="1" pos="3.836" />'
        '<dataCollectionPoint lane="10005 2" name="Lane 2" no="2" pos="3.836" />'
        '</dataCollectionPoints></network>'
    )
    result = tools_network._parse_data_collection_points(root)
    assert result == [
        {"no": "1", "name": "", "link_id": "10005", "lane_index": 1, "pos": 3.836},
        {"no": "2", "name": "Lane 2", "link_id": "10005", "lane_index": 2, "pos": 3.836},
    ]


def test_parse_data_collection_points_lane_index_none_when_malformed():
    root = ET.fromstring(
        '<network><dataCollectionPoints>'
        '<dataCollectionPoint lane="10005" name="" no="1" pos="3.836" />'
        '</dataCollectionPoints></network>'
    )
    result = tools_network._parse_data_collection_points(root)
    assert result[0]["link_id"] == "10005"
    assert result[0]["lane_index"] is None


def test_parse_data_collection_measurements_reads_grouped_point_nos():
    # Camden's own real shape: one measurement groups 3 points.
    root = ET.fromstring(
        '<network><dataCollectionMeasurements>'
        '<dataCollectionMeasurement name="" no="1">'
        '<dataCollectionPoints>'
        '<intObjectRef key="1" /><intObjectRef key="2" /><intObjectRef key="3" />'
        '</dataCollectionPoints>'
        '</dataCollectionMeasurement>'
        '<dataCollectionMeasurement name="Solo" no="2">'
        '<dataCollectionPoints><intObjectRef key="120" /></dataCollectionPoints>'
        '</dataCollectionMeasurement>'
        '</dataCollectionMeasurements></network>'
    )
    result = tools_network._parse_data_collection_measurements(root)
    assert result == [
        {"no": "1", "name": "", "point_nos": ["1", "2", "3"]},
        {"no": "2", "name": "Solo", "point_nos": ["120"]},
    ]


def test_parse_data_collection_measurements_empty_point_nos_when_unwired():
    # COM's own AddDataCollectionMeasurement(Key) creates exactly this shape
    # before add_data_collection_measurement ever wires membership.
    root = ET.fromstring(
        '<network><dataCollectionMeasurements>'
        '<dataCollectionMeasurement name="" no="1"><dataCollectionPoints/></dataCollectionMeasurement>'
        '</dataCollectionMeasurements></network>'
    )
    assert tools_network._parse_data_collection_measurements(root) == [
        {"no": "1", "name": "", "point_nos": []}
    ]


def test_parse_travel_time_sections_reads_start_and_end():
    # Camden's own real shape (the ONLY child-element shape any real Vissim
    # export ever uses -- confirmed live, disproving inpx_parser.py's other
    # two guessed flat-attribute/combined-string shapes).
    root = ET.fromstring(
        '<network><vehicleTravelTimeMeasurements>'
        '<vehicleTravelTimeMeasurement name="" no="4">'
        '<end link="29" pos="66.753" /><start link="11" pos="8.748" />'
        '</vehicleTravelTimeMeasurement>'
        '</vehicleTravelTimeMeasurements></network>'
    )
    result = tools_network._parse_travel_time_sections(root)
    assert result == [
        {"no": "4", "name": "", "from_link": "11", "from_pos": 8.748, "to_link": "29", "to_pos": 66.753}
    ]


def test_parse_travel_time_sections_degrades_gracefully_when_childless():
    root = ET.fromstring(
        '<network><vehicleTravelTimeMeasurements>'
        '<vehicleTravelTimeMeasurement no="1" name="Main St EB" />'
        '</vehicleTravelTimeMeasurements></network>'
    )
    result = tools_network._parse_travel_time_sections(root)
    assert result == [
        {"no": "1", "name": "Main St EB", "from_link": "", "from_pos": None, "to_link": "", "to_pos": None}
    ]


def test_signal_to_contract_shape_carries_controller_type_but_omits_min_green_sum():
    """v2 unit ① closed the controller_type gap: the shadow model now carries
    it ("unknown" when the parsed info lacks it — constraint #4's refuse-on-
    unknown stance). min_green_intergreen_sum (#5) remains a separate, still-
    open gap and must stay OMITTED entirely (not set to None)."""
    synthetic_signal_info = {
        "sc_no": "1",
        "sc_name": "SC1",
        "cycle_time": 90,
        "num_signal_groups": 4,
        "num_phases": 2,
        "phases": [],
    }

    shaped = tools_network._signal_to_contract_shape(synthetic_signal_info)

    assert shaped == {
        "sc_no": "1",
        "sc_name": "SC1",
        "cycle_time": 90,
        "phases": [],
        "controller_type": "unknown",
    }
    assert "min_green_intergreen_sum" not in shaped


# ---------------------------------------------------------------------------
# Q4 unit (6): _parse_stop_signs -- direct-tested against synthetic XML
# fixtures matching the REAL shape confirmed live/statically across every
# real Vissim 2026 example network that carries one
# (backend/tools/probe_stop_signs_priority_placement.py -- e.g. Signalization
# RBC Boise.US's own ``<stopSign anmFlag="false" lane="10006 1" name=""
# no="1" pos="0.511785" sg="22 2" />``). The demo .inpx carries none (see the
# pin test above's own ``assert shadow["stop_signs"] == []``), so a
# hand-built fixture is the only way to exercise the populated-list code
# path at all -- mirrors the measurement-infra parser tests' own precedent.
# ---------------------------------------------------------------------------


def test_parse_stop_signs_reads_lane_pos_and_optional_sg():
    root = ET.fromstring(
        '<network><stopSigns>'
        '<stopSign anmFlag="false" lane="10006 1" name="" no="1" pos="0.511785" sg="22 2" />'
        '<stopSign anmFlag="false" lane="101 1" name="" no="2" pos="30.8" />'
        "</stopSigns></network>"
    )
    result = tools_network._parse_stop_signs(root)
    assert result == [
        {"no": "1", "name": "", "link": "10006", "lane_index": 1, "pos": 0.511785, "sc": "22", "sg": "2"},
        {"no": "2", "name": "", "link": "101", "lane_index": 1, "pos": 30.8, "sc": "", "sg": ""},
    ]


def test_parse_stop_signs_empty_when_section_absent():
    root = ET.fromstring("<network/>")
    assert tools_network._parse_stop_signs(root) == []


# ---------------------------------------------------------------------------
# get_run_kpis
# ---------------------------------------------------------------------------


def test_get_run_kpis_returns_compact_summary_for_known_scenario():
    envelope = _dispatch("get_run_kpis", scenario_id="S000001")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["found"] is True
    assert data["scenario_id"] == "S000001"
    assert data["scenario_name"] == "Base (AM Peak)"
    assert data["run_count"] == 3
    metrics = data["metrics"]
    assert set(metrics.keys()) <= {"avg_delay_s", "avg_speed_kmh", "avg_stops", "total_travel_time_s"}
    assert metrics["avg_delay_s"] == pytest.approx(32.556666666666665)
    assert metrics["avg_speed_kmh"] == pytest.approx(38.516666666666666)


def test_get_run_kpis_never_leaks_db_paths():
    envelope = _dispatch("get_run_kpis", scenario_id="S000001")

    rendered = str(envelope["data"])
    assert ".db" not in rendered
    assert "/" not in rendered
    assert "\\" not in rendered


def test_get_run_kpis_unknown_scenario_returns_structured_not_found():
    envelope = _dispatch("get_run_kpis", scenario_id="BOGUS_SCENARIO")

    assert envelope["status"] == "ok"  # a domain miss is still a successful tool call
    data = envelope["data"]
    assert data["found"] is False
    assert data["error_type"] == "scenario_not_found"
    assert "S000001" in data["candidates"]


def test_get_run_kpis_never_forwards_raw_result_fields_like_db_path(monkeypatch):
    """get_run_kpis hand-picks its output fields (scenario_id, scenario_name,
    run_count, metrics) from get_scenario_results' return value — a raw key
    like 'db_path' living alongside 'network'/'run_count' in that dict is
    simply never read, regardless of the sanitizer. This documents that
    behavior directly rather than exercising the sanitizer (see the test
    below for that)."""
    import services.agent.tools_metrics as tools_metrics_module

    def fake_get_scenario_results(scenario_id):
        return {
            "run_count": 1,
            "network": {"DELAYAVG": 12.0, "SPEEDAVG": 40.0},
            "db_path": "/Users/someone/data/Scenarios/S000001/run.db",
        }

    monkeypatch.setattr(
        tools_metrics_module.results_reader, "get_scenario_results", fake_get_scenario_results
    )

    envelope = _dispatch("get_run_kpis", scenario_id="S000001")

    assert "db_path" not in envelope["data"]
    assert "run.db" not in str(envelope["data"])


def test_get_run_kpis_sanitizer_strips_a_path_like_value_in_a_forwarded_field(monkeypatch):
    """Unlike the test above, this exercises the sanitizer for real: inject a
    genuine path into scenario_name — a field get_run_kpis DOES forward —
    via a patched list_scenarios(), and confirm _strip_path_like removes it
    rather than letting it reach the planner."""
    import services.agent.tools_metrics as tools_metrics_module

    poisoned_name = "/Users/someone/data/Scenarios/S000001/run1.db"

    def fake_list_scenarios():
        return [{"id": "S000001", "name": poisoned_name, "color": "#000000"}]

    monkeypatch.setattr(tools_metrics_module.results_reader, "list_scenarios", fake_list_scenarios)

    envelope = _dispatch("get_run_kpis", scenario_id="S000001")

    data = envelope["data"]
    assert data["found"] is True
    # the path-like scenario_name was dropped entirely by the sanitizer
    assert data.get("scenario_name") != poisoned_name
    assert poisoned_name not in str(data)


# ---------------------------------------------------------------------------
# compare_scenarios
# ---------------------------------------------------------------------------


def test_compare_scenarios_returns_network_metrics_and_leaders_and_rankings():
    envelope = _dispatch("compare_scenarios", scenario_ids=["S000001", "S000002", "S000003"])

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["scenario_ids"] == ["S000001", "S000002", "S000003"]
    assert set(data["network_metrics"].keys()) == {"delay", "speed", "stops", "total_travel_time"}
    assert set(data["metric_leaders"].keys()) == {"delay", "speed", "stops", "total_travel_time"}
    assert len(data["scenario_ranking"]) == 3
    assert len(data["top_congested_nodes"]) <= 10
    # UI-card bulk (dashboard tiles) is dropped — this feeds an LLM prompt, not a dashboard
    assert "summary_cards" not in data


def test_compare_scenarios_reports_invalid_ids_without_raising():
    envelope = _dispatch("compare_scenarios", scenario_ids=["S000001", "NOT_A_SCENARIO"])

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["invalid_ids"] == ["NOT_A_SCENARIO"]
    assert data["scenario_ids"] == ["S000001"]


def test_compare_scenarios_never_leaks_db_paths():
    envelope = _dispatch("compare_scenarios", scenario_ids=["S000001", "S000002"])

    assert ".db" not in str(envelope["data"])


def test_compare_scenarios_keeps_km_h_unit_text_and_ranking_reasons_intact_on_real_data():
    """The IMPORTANT-1 regression: a naive path filter that flags any string
    containing '/' would silently delete key_insights' message/unit fields
    and scenario_ranking's reason field, since normal traffic units
    ('km/h', 'veh/h') contain a slash. Assert the actual text, not just
    field presence, so a re-introduced over-broad filter fails loudly."""
    envelope = _dispatch("compare_scenarios", scenario_ids=["S000001", "S000002", "S000003"])

    data = envelope["data"]
    insights_by_id = {insight["id"]: insight for insight in data["key_insights"]}

    speed_leader = insights_by_id["speed-leader"]
    assert speed_leader["message"] == "Signal Optimization leads average network speed at 51.0 km/h."
    assert speed_leader["unit"] == "km/h"

    speed_spread = insights_by_id["speed-spread"]
    assert speed_spread["message"] == "Network average speed varies by 25.4 km/h across compared scenarios."
    assert speed_spread["unit"] == "km/h"

    reasons = {entry["scenario_id"]: entry["reason"] for entry in data["scenario_ranking"]}
    assert reasons == {
        "S000002": "Best normalized delay result at 23.7 s.",
        "S000001": "Lowest normalized total travel time at 51546.0 s.",
        "S000003": "Best normalized delay result at 47.8 s.",
    }


def test_compare_scenarios_strips_a_genuine_db_path_from_synthetic_payload(monkeypatch):
    """The sanitizer must still catch a real path when one does show up,
    proving IMPORTANT-1's fix narrowed the filter rather than disabling it."""
    import services.agent.tools_metrics as tools_metrics_module

    poisoned_path = "/Users/someone/data/Scenarios/S000001/run1.db"

    def fake_get_compare_summary(ids):
        return {
            "requested_ids": ids,
            "scenario_ids": ids,
            "invalid_ids": [],
            "missing_result_ids": [],
            "network_metrics": {},
            "metric_leaders": {},
            "health_scores": [],
            "scenario_ranking": [
                {
                    "scenario_id": "S000001",
                    "reason": "Fastest at 38.5 km/h.",  # legitimate unit text alongside the path
                    "evidence_path": poisoned_path,
                }
            ],
            "top_congested_nodes": [],
            "key_insights": [],
            "status_band": {},
        }

    monkeypatch.setattr(
        tools_metrics_module.results_reader, "get_compare_summary", fake_get_compare_summary
    )

    envelope = _dispatch("compare_scenarios", scenario_ids=["S000001"])

    ranking_entry = envelope["data"]["scenario_ranking"][0]
    assert "evidence_path" not in ranking_entry  # the path-like field was dropped
    assert ranking_entry["reason"] == "Fastest at 38.5 km/h."  # the unit text survived intact
    assert poisoned_path not in str(envelope["data"])


def test_compare_scenarios_empty_scenario_ids_is_graceful_not_a_crash():
    envelope = _dispatch("compare_scenarios", scenario_ids=[])

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["requested_ids"] == []
    assert data["scenario_ids"] == []
    assert data["status_band"]["tone"] == "error"  # "no comparison ready", not a crash


def test_compare_scenarios_rejects_unexpected_params():
    envelope = _dispatch("compare_scenarios", scenario_ids=["S000001"], bogus=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"



# ---------------------------------------------------------------------------
# get_signal_programs (v2a unit 9)
# ---------------------------------------------------------------------------

import base64 as _base64


_GREEN_SPLIT_BLOB = (
    '<sc version="202001" id="1" name="">'
    '<sgs><sg id="1" name="01"/></sgs>'
    "<progs>"
    '<prog id="1" cycletime="130000" name="P1"><sgs>'
    '<sg sg_id="1"><cmds><cmd display="1" begin="15000"/><cmd display="3" begin="130000"/></cmds>'
    '<fixedstates><fixedstate display="4" duration="3000"/></fixedstates></sg>'
    "</sgs></prog>"
    '<prog id="2" cycletime="130000" name="P2"><sgs>'
    '<sg sg_id="1"><cmds><cmd display="3" begin="1000"/><cmd display="1" begin="15000"/></cmds>'
    '<fixedstates><fixedstate display="4" duration="3000"/></fixedstates></sg>'
    "</sgs></prog>"
    "</progs></sc>"
)


def _signal_program_fixture_xml(extra_controllers: str = "") -> str:
    isd = _base64.b64encode(_GREEN_SPLIT_BLOB.encode("utf-8")).decode("ascii")
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<network>"
        "<signalControllers>"
        f'<signalController no="1" name="Main" type="FIXEDTIME" progNo="2" intSupplyData="{isd}">'
        '<sgs><signalGroup no="1" name="North"/></sgs>'
        "</signalController>"
        '<signalController no="2" name="Ext" type="VAP" progNo="1" supplyFile1="#data#x.vap"/>'
        f"{extra_controllers}"
        "</signalControllers>"
        "</network>"
    )


@pytest.fixture()
def signal_program_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "signal_programs.inpx"
    path.write_text(_signal_program_fixture_xml(), encoding="utf-8")
    return path


def test_get_signal_programs_reports_programs_and_active_marker(signal_program_fixture_inpx: Path):
    tools_network.set_active_inpx(signal_program_fixture_inpx)

    envelope = _dispatch("get_signal_programs")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 2
    controllers = {c["sc_no"]: c for c in data["signal_programs"]}

    sc1 = controllers["1"]
    assert sc1["controller_type"] == "FIXEDTIME"
    assert sc1["active_program_no"] == "2"
    progs = {p["prog_no"]: p for p in sc1["programs"]}
    # The ACTIVE program's split (green 1..15 s) -- NOT program 1's wrap.
    active_split = progs["2"]["sg_splits"][0]
    assert active_split["green_start_s"] == 1.0
    assert active_split["green_end_s"] == 15.0
    # Program 1 is still reported too (with its wrapping window).
    assert progs["1"]["sg_splits"][0]["green_start_s"] == 130.0

    # External controller: empty programs + an explicit note, never a guess.
    sc2 = controllers["2"]
    assert sc2["programs"] == []
    assert "not inspectable" in sc2["note"]


def test_get_signal_programs_controller_no_filter(signal_program_fixture_inpx: Path):
    tools_network.set_active_inpx(signal_program_fixture_inpx)

    envelope = _dispatch("get_signal_programs", controller_no="1")

    data = envelope["data"]
    assert data["count"] == 1
    assert len(data["signal_programs"]) == 1
    assert data["signal_programs"][0]["sc_no"] == "1"
    assert "truncated" not in data


def test_get_signal_programs_unfiltered_caps_with_truncated_flag(tmp_path: Path):
    extra = "".join(
        f'<signalController no="{no}" name="S{no}" type="FIXEDTIMESIMPLE" progNo="1" cycTm="60">'
        '<sgs><signalGroup no="1" endRed="10" endGreen="40" amber="3"/></sgs>'
        "</signalController>"
        for no in range(10, 21)
    )
    path = tmp_path / "many_controllers.inpx"
    path.write_text(_signal_program_fixture_xml(extra), encoding="utf-8")
    tools_network.set_active_inpx(path)

    envelope = _dispatch("get_signal_programs")

    data = envelope["data"]
    assert data["count"] == 13
    assert len(data["signal_programs"]) == 10
    assert data["truncated"] is True
    assert "controller_no" in data["hint"]


def test_get_signal_programs_shadow_model_flat_splits_shape(signal_program_fixture_inpx: Path):
    # The FLAT signal_program_splits list guardrails.py reads: three-part
    # "{sc}.{prog}.{sg}" ids, green_window as ONE dict, active marker.
    shadow = tools_network.build_shadow_model(signal_program_fixture_inpx)

    splits = {s["id"]: s for s in shadow["signal_program_splits"]}
    assert set(splits) == {"1.1.1", "1.2.1"}
    assert splits["1.2.1"]["green_window"] == {"green_start_s": 1.0, "green_end_s": 15.0}
    assert splits["1.2.1"]["active"] is True
    assert splits["1.1.1"]["active"] is False
    assert splits["1.1.1"]["green_window"] == {"green_start_s": 130.0, "green_end_s": 15.0}


def test_get_signal_programs_rejects_unexpected_params(signal_program_fixture_inpx: Path):
    tools_network.set_active_inpx(signal_program_fixture_inpx)

    envelope = _dispatch("get_signal_programs", bogus=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


# ---------------------------------------------------------------------------
# STAGE-BASED programs at the TOOL boundary — Murdoch Drive finding
# (2026-07-28). What the planner and the answer actually see: never a bare
# blank, never a bare implausible number.
# ---------------------------------------------------------------------------

_STAGE_BLOB_TOOL = (
    '<sc version="202001" id="6" name="" interstagesUsingMinDurations="true">'
    '<sgs><sg id="1" name="SG1"/><sg id="2" name="SG2"/></sgs>'
    "<progs/>"
    "<stages>"
    '<stage id="1" name="A"><activations>'
    '<activation sg_id="1" activation="ON"/><activation sg_id="2" activation="OFF"/>'
    "</activations></stage>"
    '<stage id="2" name="B"><activations>'
    '<activation sg_id="1" activation="OFF"/><activation sg_id="2" activation="ON"/>'
    "</activations></stage>"
    "</stages>"
    "<interstageProgs>"
    '<interstageProg id="1" cycletime="12000" fromStage="1" toStage="2"/>'
    '<interstageProg id="2" cycletime="12000" fromStage="2" toStage="1"/>'
    "</interstageProgs>"
    "<stageProgs>"
    '<stageProg id="41" cycletime="120000" offset="0" name="TMP2_AM_120s"><interstages>'
    '<interstage display="1" begin="94000"/><interstage display="2" begin="113000"/>'
    "</interstages></stageProg>"
    "</stageProgs></sc>"
)

_STUB_BLOB_TOOL = (
    '<sc version="202001" id="9" name="">'
    '<sgs><sg id="1" name="SG1"/></sgs>'
    "<progs>"
    '<prog id="11" cycletime="375000" offset="0" name="BaseAM"><sgs>'
    '<sg sg_id="1"><cmds><cmd display="3" begin="0"/><cmd display="1" begin="357000"/></cmds>'
    '<fixedstates><fixedstate display="4" duration="4000"/></fixedstates></sg>'
    "</sgs></prog></progs><stageProgs/></sc>"
)


@pytest.fixture()
def murdoch_shaped_inpx(tmp_path: Path) -> Path:
    stage_isd = _base64.b64encode(_STAGE_BLOB_TOOL.encode("utf-8")).decode("ascii")
    stub_isd = _base64.b64encode(_STUB_BLOB_TOOL.encode("utf-8")).decode("ascii")
    path = tmp_path / "murdoch_shaped.inpx"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><network><signalControllers>'
        '<signalController no="6" name="SC-Alpha" type="FIXEDTIME" progNo="41" '
        'cycTm="0" intSupplyData="%s"><sgs>'
        '<signalGroup no="1" name="SG1" amber="4" minGreen="5" endRed="0" endGreen="0"/>'
        '<signalGroup no="2" name="SG2" amber="4" minGreen="5" endRed="0" endGreen="0"/>'
        "</sgs></signalController>"
        '<signalController no="9" name="SC-Stub" type="FIXEDTIME" progNo="11" '
        'intSupplyData="%s"><sgs>'
        '<signalGroup no="1" name="SG1" amber="5" minGreen="10" endRed="0" endGreen="0"/>'
        "</sgs></signalController>"
        "</signalControllers>"
        '<signalHeads><signalHead no="1" sg="6 1" lane="10 1"/>'
        '<signalHead no="2" sg="6 2" lane="11 1"/>'
        '<signalHead no="3" sg="9 1" lane="12 1"/></signalHeads>'
        "</network>" % (stage_isd, stub_isd),
        encoding="utf-8",
    )
    return path


def test_get_signal_programs_stage_based_controller_is_not_a_silent_blank(
    murdoch_shaped_inpx: Path,
):
    """Before the fix this controller returned ``programs: []`` with NO note
    at all (the note branch only fired for non-FIXEDTIME types), so the agent
    reported nothing and explained nothing."""
    tools_network.set_active_inpx(murdoch_shaped_inpx)

    envelope = _dispatch("get_signal_programs", controller_no="6")

    controller = envelope["data"]["signal_programs"][0]
    program = controller["programs"][0]
    assert program["prog_no"] == "41"
    assert program["program_kind"] == "stage_based"
    assert program["cycle_s"] == 120.0
    assert program["sg_splits"] == []
    assert program["sg_splits_unavailable_reason"] == "STAGE_BASED_PROGRAM"
    # The reason must arrive in words the answer can quote, Korean included.
    assert "현시" in program["note_ko"]
    assert "STAGE-based" in program["note"]
    # ...and the useful data is there instead of the missing green windows.
    assert [s["to_stage"] for s in program["stage_sequence"]] == ["B", "A"]
    assert [s["signal_groups"] for s in program["stage_sequence"]] == [["2"], ["1"]]
    roster = {sg["sg_no"]: sg for sg in controller["signal_groups"]}
    assert roster["1"]["green_in_stages"] == ["A"]
    assert roster["2"]["min_green_s"] == 5.0


def test_get_signal_programs_implausible_cycle_ships_with_its_caveat(
    murdoch_shaped_inpx: Path,
):
    """375 s cycle / 357 s green on one signal group: reported verbatim (we
    never alter what the model says) but never as an unqualified fact."""
    tools_network.set_active_inpx(murdoch_shaped_inpx)

    envelope = _dispatch("get_signal_programs", controller_no="9")

    program = envelope["data"]["signal_programs"][0]["programs"][0]
    assert program["cycle_s"] == 375.0
    assert program["sg_splits"][0]["green_end_s"] == 357.0
    assert "CYCLE_ABOVE_TYPICAL_MAX" in program["data_quality"]
    assert "NEAR_PERMANENT_GREEN" in program["data_quality"]
    assert program["note_ko"] and program["note"]


def test_get_signal_programs_stage_based_adds_no_flat_split_targets(
    murdoch_shaped_inpx: Path,
):
    """A stage program has no editable (controller, program, signal group)
    triple, so it must contribute NOTHING to the flat list guardrails and
    the write path resolve against -- a target that cannot be written must
    not look writable."""
    shadow = tools_network.build_shadow_model(murdoch_shaped_inpx)

    ids = {s["id"] for s in shadow["signal_program_splits"]}
    assert ids == {"9.11.1"}


# ---------------------------------------------------------------------------
# get_time_distributions -- QUEUE (Q3, v2a) unit 5
# ---------------------------------------------------------------------------


def test_get_time_distributions_on_demo_data_is_empty_but_well_formed():
    envelope = _dispatch("get_time_distributions")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "time_distributions": [], "truncated": False}
    assert "hint" not in envelope["data"]


_TIME_DISTRIBUTION_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <timeDistributions>
    <timeDistribution no="1" name="0.5 s" type="NORMAL" mean="0.5" stdDev="0"/>
    <timeDistribution no="15" name="15 s" type="NORMAL" mean="15" stdDev="5"/>
    <timeDistribution no="25" name="25 s" type="NORMAL" mean="25" stdDev="5"/>
    <timeDistribution no="60" name="60 s" type="NORMAL" mean="60" stdDev="10"/>
    <timeDistribution no="200" name="2-4 s" type="EMPIRICAL" mean="3" stdDev="0.5">
      <durDistrDataPts>
        <durationDistributionDataPoint fx="0" x="2"/>
        <durationDistributionDataPoint fx="1" x="4"/>
      </durDistrDataPts>
    </timeDistribution>
    <timeDistribution no="999" name="unused" type="NORMAL" mean="10" stdDev="2"/>
  </timeDistributions>
  <drivingBehaviors>
    <drivingBehavior no="1" name="DB1" w99cc1Distr="1" zipperOvtTmDistr="200"/>
  </drivingBehaviors>
  <ptLines>
    <ptLine no="10" name="Bus 1" entryTmDistr="15">
      <lineStops>
        <ptLineStop ptStop="5" dwellTmDistr="25" active="true">
          <vehClassDwellTimeDistribution vehClass="100" dwellTmDistr="60"/>
        </ptLineStop>
      </lineStops>
    </ptLine>
  </ptLines>
</network>
"""


@pytest.fixture()
def time_distribution_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "time_distributions.inpx"
    path.write_text(_TIME_DISTRIBUTION_FIXTURE_INPX, encoding="utf-8")
    return path


def test_get_time_distributions_on_real_elements(time_distribution_fixture_inpx: Path):
    tools_network.set_active_inpx(time_distribution_fixture_inpx)

    envelope = _dispatch("get_time_distributions")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 6
    by_no = {td["no"]: td for td in data["time_distributions"]}

    # no=1: driving-behavior CC1 reference -- CONFIRMS the "CC1 is a
    # reference to a Time Distribution object" landmine end to end.
    cc1_target = by_no["1"]
    assert cc1_target["type"] == "NORMAL"
    assert cc1_target["mean_std_dev"] == {"mean_s": 0.5, "std_dev_s": 0.0}
    assert cc1_target["points"] == []
    assert cc1_target["point_count"] == 0
    assert cc1_target["referenced_by"]["driving_behaviors"] == {"count": 1, "ids": ["1:cc1"], "truncated": False}
    assert cc1_target["reference_count"] == 1

    # no=15: referenced only via ptLine.entryTmDistr -- a real site with no
    # shadow-model object of its own, counted generically under "other".
    entry_target = by_no["15"]
    assert entry_target["referenced_by"]["other"] == {"count": 1, "by_tag": {"ptLine": 1}}
    assert entry_target["reference_count"] == 1
    assert entry_target["referenced_by"]["pt_stops"] == {"count": 0, "ids": [], "truncated": False}

    # no=25: referenced via ptLineStop.dwellTmDistr, owner-id-ed by
    # "{pt_line_no}.{pt_stop_no}".
    dwell_target = by_no["25"]
    assert dwell_target["referenced_by"]["pt_stops"] == {"count": 1, "ids": ["10.5"], "truncated": False}
    assert dwell_target["reference_count"] == 1

    # no=60: referenced ONLY via the nested per-vehicle-class override.
    override_target = by_no["60"]
    assert override_target["referenced_by"]["pt_vehicle_class_overrides"] == {
        "count": 1, "ids": ["10.5.100"], "truncated": False,
    }
    assert override_target["reference_count"] == 1

    # no=200: EMPIRICAL -- points drive sampling, mean_std_dev is a frozen
    # informational summary (live-verified elsewhere it cannot be edited).
    empirical = by_no["200"]
    assert empirical["type"] == "EMPIRICAL"
    assert empirical["mean_std_dev"] == {"mean_s": 3.0, "std_dev_s": 0.5}
    assert empirical["points"] == [
        {"duration_s": 2.0, "cum_fraction": 0.0},
        {"duration_s": 4.0, "cum_fraction": 1.0},
    ]
    assert empirical["point_count"] == 2
    assert empirical["points_truncated"] is False
    assert empirical["referenced_by"]["driving_behaviors"] == {"count": 1, "ids": ["1:zipper"], "truncated": False}
    assert empirical["reference_count"] == 1

    # no=999: genuinely unreferenced -- every category silent, matching the
    # guardrail doctrine's "silent when there's nothing to warn about" norm.
    unused = by_no["999"]
    assert unused["reference_count"] == 0
    assert unused["referenced_by"]["pt_stops"] == {"count": 0, "ids": [], "truncated": False}
    assert unused["referenced_by"]["other"] == {"count": 0, "by_tag": {}}

    assert data["truncated"] is False
    assert "hint" not in data


def test_get_time_distributions_filters_by_time_distribution_no(time_distribution_fixture_inpx: Path):
    tools_network.set_active_inpx(time_distribution_fixture_inpx)

    envelope = _dispatch("get_time_distributions", time_distribution_no="200")

    assert envelope["status"] == "ok"
    assert envelope["data"]["count"] == 1
    assert envelope["data"]["time_distributions"][0]["no"] == "200"
    assert "truncated" not in envelope["data"]


def test_get_time_distributions_filters_by_time_distribution_no_no_match_is_empty_not_an_error(
    time_distribution_fixture_inpx: Path,
):
    tools_network.set_active_inpx(time_distribution_fixture_inpx)

    envelope = _dispatch("get_time_distributions", time_distribution_no="999999")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "time_distributions": []}


def test_get_time_distributions_rejects_unexpected_params(time_distribution_fixture_inpx: Path):
    tools_network.set_active_inpx(time_distribution_fixture_inpx)

    envelope = _dispatch("get_time_distributions", bogus_param="nope")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_get_time_distributions_unfiltered_call_on_large_network_is_capped_and_truncated(
    tmp_path: Path,
):
    entries = "".join(
        f'<timeDistribution no="{n}" name="d{n}" type="NORMAL" mean="{n}" stdDev="1"/>'
        for n in range(1, 90)
    )
    path = tmp_path / "many_time_distributions.inpx"
    path.write_text(f"<network><timeDistributions>{entries}</timeDistributions></network>", encoding="utf-8")
    tools_network.set_active_inpx(path)

    envelope = _dispatch("get_time_distributions")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 89
    assert len(data["time_distributions"]) == 60  # _TIME_DISTRIBUTIONS_DEFAULT_CAP
    assert data["truncated"] is True
    assert "time_distribution_no" in data["hint"]


# ---------------------------------------------------------------------------
# get_pt_stops / get_pt_lines -- Q4 unit (7): PT stop + PT line placement.
# Fixture uses the REAL nesting live-verified by backend/tools/
# probe_pt_stops_lines.py ("BRT Priority Texas.US") -- <ptStops>/<ptLines>
# top-level sections, a ptLine's own <lineStops>/<depTimes>/<linkSeq>
# children, and the real referrer shapes (a detector's own "ptStop"
# attribute, and a SEPARATE partialPTLineStop object family under
# vehicleRoutingDecisionsPartialPT) -- NOT the pre-existing
# time_distribution_fixture_inpx's own synthetic (and, per this unit's own
# probe, unrealistic) vehClassDwellTimeDistribution-inside-ptLineStop
# nesting, which this unit does not touch or rely on.
# ---------------------------------------------------------------------------


def test_get_pt_stops_on_demo_data_is_empty_but_well_formed():
    envelope = _dispatch("get_pt_stops")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "pt_stops": [], "truncated": False}
    assert "hint" not in envelope["data"]


def test_get_pt_lines_on_demo_data_is_empty_but_well_formed():
    envelope = _dispatch("get_pt_lines")

    assert envelope["status"] == "ok"
    assert envelope["data"] == {"count": 0, "pt_lines": [], "truncated": False}
    assert "hint" not in envelope["data"]


_PT_STOPS_LINES_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <ptStops>
    <ptStop no="99569" name="Main St Stop" lane="13270462 1" pos="41.75" length="15.24" anmid=""/>
    <ptStop no="13" name="" lane="56 2" pos="49.76" length="15.24" anmid=""/>
    <ptStop no="7" name="Unreferenced platform" lane="632 1" pos="10.0" length="15.24" anmid=""/>
  </ptStops>
  <ptLines>
    <ptLine no="52004" name="WB" vehType="300" desSpeedDistr="12" entryLink="13270462"
            destLink="12270123" destPos="189.13" entTmOffset="0" slackTmFrac="1">
      <depTimes>
        <departureTime dep="0" occup="40" teleCour="0"/>
        <departureTime dep="900" occup="40" teleCour="0"/>
      </depTimes>
      <lineStops>
        <ptLineStop ptStop="99569" dwellTmDistr="10" dwellTmDef="DISTRIBUTION" active="true"
                    alightL="true" alightR="true" boardL="true" boardR="true"/>
      </lineStops>
      <linkSeq>
        <intObjectRef key="13270462"/>
      </linkSeq>
    </ptLine>
    <ptLine no="52201" name="NB" vehType="300" desSpeedDistr="1" entryLink="10900"
            destLink="12270123" destPos="189.13" entTmOffset="0" slackTmFrac="1" entryTmDistr="15">
      <depTimes>
        <departureTime dep="840" occup="40" teleCour="0"/>
      </depTimes>
      <lineStops>
        <ptLineStop ptStop="99569" dwellTmDistr="10" dwellTmDef="DISTRIBUTION" active="true"/>
        <ptLineStop ptStop="13" dwellTmDistr="20" dwellTmDef="DISTRIBUTION" active="false"/>
      </lineStops>
      <linkSeq>
        <intObjectRef key="10900"/>
        <intObjectRef key="13270461"/>
      </linkSeq>
    </ptLine>
  </ptLines>
  <detectors>
    <detector no="541" name="Open boarding gate" lane="13270462 1" pos="23.81" length="0.4"
              ptStop="99569" sc="21" portNo="1" type="STANDARD"/>
  </detectors>
</network>
"""


@pytest.fixture()
def pt_stops_lines_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "pt_stops_lines.inpx"
    path.write_text(_PT_STOPS_LINES_FIXTURE_INPX, encoding="utf-8")
    return path


def test_get_pt_stops_on_real_elements(pt_stops_lines_fixture_inpx: Path):
    tools_network.set_active_inpx(pt_stops_lines_fixture_inpx)

    envelope = _dispatch("get_pt_stops")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 3
    by_no = {s["no"]: s for s in data["pt_stops"]}

    # no=99569: referenced by TWO real ptLineStops (lines 52004 and 52201)
    # AND the PT-boarding detector.
    stop = by_no["99569"]
    assert stop["name"] == "Main St Stop"
    assert stop["link"] == "13270462"
    assert stop["lane_index"] == 1
    assert stop["pos"] == 41.75
    assert stop["length"] == 15.24
    assert stop["referenced_by"]["pt_line_stops"] == {
        "count": 2, "ids": ["52004.99569", "52201.99569"], "truncated": False,
    }
    assert stop["referenced_by"]["detectors"] == {"count": 1, "ids": ["541"], "truncated": False}
    assert stop["reference_count"] == 3

    # no=13: referenced by ONE ptLineStop only.
    stop13 = by_no["13"]
    assert stop13["referenced_by"]["pt_line_stops"] == {"count": 1, "ids": ["52201.13"], "truncated": False}
    assert stop13["reference_count"] == 1

    # no=7: genuinely unreferenced.
    stop7 = by_no["7"]
    assert stop7["reference_count"] == 0
    assert stop7["referenced_by"]["pt_line_stops"] == {"count": 0, "ids": [], "truncated": False}

    assert data["truncated"] is False
    assert "hint" not in data


def test_get_pt_stops_filters_by_pt_stop_no(pt_stops_lines_fixture_inpx: Path):
    tools_network.set_active_inpx(pt_stops_lines_fixture_inpx)

    envelope = _dispatch("get_pt_stops", pt_stop_no="13")

    assert envelope["status"] == "ok"
    assert envelope["data"]["count"] == 1
    assert envelope["data"]["pt_stops"][0]["no"] == "13"


def test_get_pt_stops_filters_by_link_no(pt_stops_lines_fixture_inpx: Path):
    tools_network.set_active_inpx(pt_stops_lines_fixture_inpx)

    envelope = _dispatch("get_pt_stops", link_no="632")

    assert envelope["status"] == "ok"
    assert envelope["data"]["count"] == 1
    assert envelope["data"]["pt_stops"][0]["no"] == "7"


def test_get_pt_stops_rejects_unexpected_params(pt_stops_lines_fixture_inpx: Path):
    tools_network.set_active_inpx(pt_stops_lines_fixture_inpx)

    envelope = _dispatch("get_pt_stops", bogus_param="nope")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_get_pt_lines_on_real_elements(pt_stops_lines_fixture_inpx: Path):
    tools_network.set_active_inpx(pt_stops_lines_fixture_inpx)

    envelope = _dispatch("get_pt_lines")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 2
    by_no = {pl["no"]: pl for pl in data["pt_lines"]}

    line1 = by_no["52004"]
    assert line1["name"] == "WB"
    assert line1["veh_type"] == "300"
    assert line1["entry_link"] == "13270462"
    assert line1["dest_link"] == "12270123"
    assert line1["dest_pos"] == 189.13
    assert line1["entry_tm_distr"] is None  # absent on this line -- None, not ""
    assert line1["departure_count"] == 2
    assert line1["link_seq"] == ["13270462"]
    assert line1["link_seq_count"] == 1
    assert line1["link_seq_truncated"] is False
    assert line1["stops"] == [
        {"pt_stop_no": "99569", "dwell_tm_distr": "10", "dwell_tm_def": "DISTRIBUTION", "active": True}
    ]

    line2 = by_no["52201"]
    assert line2["entry_tm_distr"] == "15"  # a real, present optional reference
    assert line2["departure_count"] == 1
    assert line2["link_seq"] == ["10900", "13270461"]
    assert len(line2["stops"]) == 2
    assert line2["stops"][1] == {
        "pt_stop_no": "13", "dwell_tm_distr": "20", "dwell_tm_def": "DISTRIBUTION", "active": False
    }

    assert data["truncated"] is False
    assert "hint" not in data


def test_get_pt_lines_filters_by_pt_line_no(pt_stops_lines_fixture_inpx: Path):
    tools_network.set_active_inpx(pt_stops_lines_fixture_inpx)

    envelope = _dispatch("get_pt_lines", pt_line_no="52201")

    assert envelope["status"] == "ok"
    assert envelope["data"]["count"] == 1
    assert envelope["data"]["pt_lines"][0]["no"] == "52201"


def test_get_pt_lines_rejects_unexpected_params(pt_stops_lines_fixture_inpx: Path):
    tools_network.set_active_inpx(pt_stops_lines_fixture_inpx)

    envelope = _dispatch("get_pt_lines", bogus_param="nope")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_build_shadow_model_parses_pt_stops_and_lines_from_real_xml(pt_stops_lines_fixture_inpx: Path):
    shadow = tools_network.build_shadow_model(pt_stops_lines_fixture_inpx)

    assert len(shadow["pt_stops"]) == 3
    assert len(shadow["pt_lines"]) == 2
    # the FLAT pt_line_stops list guardrails.py's postwrite machinery reads.
    flat = shadow["pt_line_stops"]
    ids = {entry["id"] for entry in flat}
    assert ids == {"52004.99569", "52201.99569", "52201.13"}
    by_id = {entry["id"]: entry for entry in flat}
    assert by_id["52201.13"]["line_no"] == "52201"
    assert by_id["52201.13"]["stop_no"] == "13"
    assert by_id["52201.13"]["dwell_tm_distr"] == "20"


def test_get_pt_stops_unfiltered_call_on_large_network_is_capped_and_truncated(tmp_path: Path):
    entries = "".join(
        f'<ptStop no="{n}" name="s{n}" lane="101 1" pos="{n}.0" length="15"/>' for n in range(1, 90)
    )
    path = tmp_path / "many_pt_stops.inpx"
    path.write_text(f"<network><ptStops>{entries}</ptStops></network>", encoding="utf-8")
    tools_network.set_active_inpx(path)

    envelope = _dispatch("get_pt_stops")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 89
    assert len(data["pt_stops"]) == 60  # _PT_STOPS_DEFAULT_CAP
    assert data["truncated"] is True
    assert "pt_stop_no" in data["hint"]


# ---------------------------------------------------------------------------
# get_parking_lots -- Q6 unit (2): parking lots.
#
# The fixture carries BOTH real species (schema copied verbatim from the two
# corpus networks that have any -- 3D - Complex Intersection Karlsruhe.DE's
# REALPARKINGSPACE lots and Motorway Dyn Assign Mainz.DE's ZONECONNECTORs) plus
# enough one-space lots to cross the tool's own 30-item cap, because the real
# Karlsruhe network carries 573 of them and an unfiltered dump there is exactly
# what the cap exists to stop.
# ---------------------------------------------------------------------------

_PARKING_LOTS_FIXTURE_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links>
    <link no="91" name="Deck approach">
      <lanes><lane width="3.5"/></lanes>
      <geometry><linkPolyPts><linkPolyPoint x="0" y="0"/><linkPolyPoint x="500" y="0"/></linkPolyPts></geometry>
    </link>
    <link no="26" name="Zone stub">
      <lanes><lane width="3.5"/></lanes>
      <geometry><linkPolyPts><linkPolyPoint x="0" y="80"/><linkPolyPoint x="100" y="80"/></linkPolyPts></geometry>
    </link>
  </links>
  <parkingLots>
    <parkingLot no="14" name="Deck" type="REALPARKINGSPACE" lane="91 1" pos="1.79" length="36"
                capacity="6" lenPerSpc="6" attrac="70" attracLast="70" relFlow="1" parkTmMax="99999"
                parkFeeFlat="2.5" parkFeeHr="1.25" openFrom="0" openUntil="99999"
                parkDir="FORWARDINFORWARDOUT" desSpeedDistrDef="50" dirChgDurDist="9" zone="114"/>
    <parkingLot no="26" name="Zone connector" type="ZONECONNECTOR" intLink="26" zone="1000005"
                pos="22.54" length="3" capacity="10000" lenPerSpc="6" attrac="0" relFlow="0"
                parkTmMax="99999" openFrom="0" openUntil="99999" parkDir="FORWARDINFORWARDOUT"
                desSpeedDistrDef="65"/>
"""

_PARKING_LOTS_FIXTURE_TAIL = """  </parkingLots>
</network>
"""


@pytest.fixture()
def parking_lots_fixture_inpx(tmp_path: Path) -> Path:
    spaces = "".join(
        f'    <parkingLot no="{n}" name="" type="REALPARKINGSPACE" lane="91 1" pos="{n}" '
        f'length="6.2" capacity="1" lenPerSpc="6.2" attrac="0" relFlow="1" parkTmMax="99999" '
        f'openFrom="0" openUntil="99999" parkDir="FORWARDINREVERSEOUT" desSpeedDistrDef="15" '
        f'dirChgDurDist="9"/>\n'
        for n in range(101, 141)
    )
    path = tmp_path / "parking_lots.inpx"
    path.write_text(
        _PARKING_LOTS_FIXTURE_HEAD + spaces + _PARKING_LOTS_FIXTURE_TAIL, encoding="utf-8"
    )
    return path


def test_get_parking_lots_reads_both_species(parking_lots_fixture_inpx: Path):
    tools_network.set_active_inpx(parking_lots_fixture_inpx)

    envelope = _dispatch("get_parking_lots", parking_lot_no="14")

    assert envelope["status"] == "ok"
    (lot,) = envelope["data"]["parking_lots"]
    assert lot["type"] == "REALPARKINGSPACE"
    assert lot["link"] == "91"
    assert lot["lane_index"] == 1
    assert lot["capacity"] == 6
    assert lot["len_per_space"] == 6.0
    assert lot["park_fee_flat"] == 2.5
    assert lot["park_fee_hourly"] == 1.25
    assert lot["park_direction"] == "FORWARDINFORWARDOUT"
    assert lot["dir_change_dur_distr_no"] == "9"

    envelope = _dispatch("get_parking_lots", parking_lot_no="26")
    (zone_lot,) = envelope["data"]["parking_lots"]
    assert zone_lot["type"] == "ZONECONNECTOR"
    assert zone_lot["link"] == "26"
    # The zone-connector species genuinely has no lane -- never a fabricated 1.
    assert zone_lot["lane_index"] is None
    assert zone_lot["zone"] == "1000005"


def test_get_parking_lots_caps_an_unfiltered_call(parking_lots_fixture_inpx: Path):
    tools_network.set_active_inpx(parking_lots_fixture_inpx)

    envelope = _dispatch("get_parking_lots")

    data = envelope["data"]
    assert data["count"] == 42
    assert len(data["parking_lots"]) == 30
    assert data["truncated"] is True
    assert "42 parking lots" in data["hint"]


def test_get_parking_lots_filters_by_link_no(parking_lots_fixture_inpx: Path):
    tools_network.set_active_inpx(parking_lots_fixture_inpx)

    envelope = _dispatch("get_parking_lots", link_no="26")

    data = envelope["data"]
    assert data["count"] == 1
    assert data["parking_lots"][0]["no"] == "26"
    # A filtered call is never truncated -- it answers the exact question.
    assert "truncated" not in data


def test_get_parking_lots_on_demo_data_is_empty_but_well_formed():
    """Most real networks carry no <parkingLots> section at all (19 of the 21
    example networks) -- an absent section must read as zero lots, not as an
    error or a missing key."""
    envelope = _dispatch("get_parking_lots")

    assert envelope["status"] == "ok"
    assert envelope["data"]["count"] == 0
    assert envelope["data"]["parking_lots"] == []
    assert envelope["data"]["truncated"] is False


def test_get_parking_lots_rejects_unexpected_params(parking_lots_fixture_inpx: Path):
    tools_network.set_active_inpx(parking_lots_fixture_inpx)

    envelope = _dispatch("get_parking_lots", bogus_param="nope")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"

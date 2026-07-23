"""Tests for M1.2's read tools: tools_network.py and tools_metrics.py.

These are the first real tools registered through the M0.4 registry
(services.agent.toolkit) — importing either module registers its tools onto
``default_registry`` at import time (see toolkit.py's module docstring), so
this file also carries the anti-drift guard extending M0.4's registry test to
real tools (docs/phase2/02-ROADMAP.md M1.2).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

import services.agent.tools_metrics as tools_metrics  # noqa: F401 - import registers tools
import services.agent.tools_network as tools_network  # noqa: F401 - import registers tools
from services.agent.toolkit import default_registry, dispatch, render_contract

FIXTURES_DIR = Path(__file__).parent / "fixtures"
KOREAN_FIXTURE_INPX = FIXTURES_DIR / "korean_object_names.inpx"

EXPECTED_TOOL_NAMES = {
    "get_network_overview",
    "resolve_object",
    "get_vehicle_inputs",
    "get_signal_controllers",
    "get_run_kpis",
    "compare_scenarios",
}

# M2.3's tools_edit.py registers these write-tier tools onto the SAME
# default_registry. This file only imports tools_metrics/tools_network
# directly, but pytest's collection phase imports every test module up
# front -- including ones that import services.agent.orchestrator, which
# imports tools_edit.py purely for its registration side effect (see that
# module's docstring) -- so by the time any test in THIS file runs,
# default_registry already carries all 11 tools regardless of this file's
# own imports or test execution order. The anti-drift test below has to
# know about them too, or it would (correctly, but confusingly) fail on
# every run.
EXPECTED_WRITE_TOOL_NAMES = {
    "set_vehicle_input_volume",
    "set_signal_cycle",
    "set_signal_offset",
    "set_sim_duration",
    "set_random_seed",
}


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


def test_default_registry_has_exactly_the_expected_read_and_write_tools():
    """M1.2's 6 read tools + M2.3's 5 write tools, no phantoms, nothing
    missing. Was "...exactly_the_six_read_tools" before M2.3 registered
    tools_edit.py's write tools onto this same default_registry — renamed
    and widened rather than split, since a second anti-drift test over the
    same registry would just duplicate this one's headings/duplicate-check
    logic for no real isolation (both modules' tools share one registry by
    design)."""
    contract = render_contract(registry=default_registry)
    headings = re.findall(r"^## (\S+)$", contract, flags=re.MULTILINE)

    assert len(headings) == len(set(headings))  # no duplicates
    assert set(headings) == EXPECTED_TOOL_NAMES | EXPECTED_WRITE_TOOL_NAMES


def test_all_six_read_tools_are_tier_read():
    for name in EXPECTED_TOOL_NAMES:
        spec = default_registry.get(name)
        assert spec is not None, f"{name} not registered"
        assert spec.tier == "read"


def test_all_five_write_tools_are_tier_write():
    for name in EXPECTED_WRITE_TOOL_NAMES:
        spec = default_registry.get(name)
        assert spec is not None, f"{name} not registered"
        assert spec.tier == "write"


def test_network_tools_use_inspect_provider_and_metrics_tools_use_metrics_provider():
    for name in ("get_network_overview", "resolve_object", "get_vehicle_inputs", "get_signal_controllers"):
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
    for key in ("links", "nodes", "vehicle_inputs", "signals"):
        assert key in data
        section = data[key]
        assert set(section.keys()) == {"count", "items", "truncated"}
        assert section["count"] == len(section["items"]) or section["truncated"]
        for item in section["items"]:
            assert set(item.keys()) == {"id", "name"}  # compact: no geometry/points/etc.

    # demo dataset facts (data/practice.inpx): 14 links, 2 nodes, 6 vehicle
    # inputs, 0 working signal controllers (no intSupplyData in the demo file)
    assert data["links"]["count"] == 14
    assert data["nodes"]["count"] == 2
    assert data["vehicle_inputs"]["count"] == 6
    assert data["signals"]["count"] == 0
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
    surface the near match — node id '1' ('Central & Main')."""
    envelope = _dispatch("resolve_object", query="Cntrall Man", object_type=None)

    assert envelope["status"] == "ok"  # a domain miss is still a successful tool call
    data = envelope["data"]
    assert data["found"] is False
    assert data["error_type"] == "object_not_found"
    assert "1" in data["candidates"]


def test_resolve_object_rejects_unknown_object_type():
    envelope = _dispatch("resolve_object", query="anything", object_type="banana")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


# ---------------------------------------------------------------------------
# get_vehicle_inputs / get_signal_controllers — extended shadow model sections
# ---------------------------------------------------------------------------


def test_get_vehicle_inputs_on_demo_data_has_no_volume_children():
    """The demo .inpx's <vehicleInput> elements carry no/name/link attrs and
    no volume children — volume must come back None, never crash."""
    envelope = _dispatch("get_vehicle_inputs")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data["count"] == 6
    assert len(data["vehicle_inputs"]) == 6
    for vi in data["vehicle_inputs"]:
        assert set(vi.keys()) == {"no", "name", "link", "volume"}
        assert vi["volume"] is None
    first = next(vi for vi in data["vehicle_inputs"] if vi["no"] == "1")
    assert first["name"] == "Main St EB"
    assert first["link"] == "101"


def test_get_signal_controllers_on_demo_data_is_empty_but_well_formed():
    """practice.inpx's signalController elements carry no intSupplyData, so
    signal_parser.parse_signal_controllers yields none — the tool must return
    an empty, well-formed list rather than erroring."""
    envelope = _dispatch("get_signal_controllers")

    assert envelope["status"] == "ok"
    data = envelope["data"]
    assert data == {"count": 0, "signals": []}


# ---------------------------------------------------------------------------
# build_shadow_model — the extended shadow model builder (unit-level)
# ---------------------------------------------------------------------------


def test_build_shadow_model_has_exactly_the_contract_keys():
    shadow = tools_network.build_shadow_model(Path(tools_network._active_inpx_path()))

    assert set(shadow.keys()) == {"links", "nodes", "vehicle_inputs", "signals"}
    assert len(shadow["links"]) == 14
    assert len(shadow["nodes"]) == 2
    assert len(shadow["vehicle_inputs"]) == 6
    assert shadow["signals"] == []


def test_build_shadow_model_is_mtime_cached_across_repeat_calls():
    path = tools_network._active_inpx_path()
    first = tools_network.build_shadow_model(path)
    second = tools_network.build_shadow_model(path)
    assert first is second  # same lru_cache hit, not just equal


def test_signal_to_contract_shape_omits_controller_type_and_min_green_intergreen_sum():
    """docs/phase2/DECISIONS.md gap entry: signal_parser exposes neither field
    yet, so the shadow model must OMIT them entirely (not set to None)."""
    synthetic_signal_info = {
        "sc_no": "1",
        "sc_name": "SC1",
        "cycle_time": 90,
        "num_signal_groups": 4,
        "num_phases": 2,
        "phases": [],
    }

    shaped = tools_network._signal_to_contract_shape(synthetic_signal_info)

    assert shaped == {"sc_no": "1", "sc_name": "SC1", "cycle_time": 90, "phases": []}
    assert "controller_type" not in shaped
    assert "min_green_intergreen_sum" not in shaped


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

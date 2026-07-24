import random

import pytest
from fastapi.testclient import TestClient

import main
import services.simulation_runs as simulation_runs
from routers import simulations
from tools.make_demo_data import build_result_db


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def scenarios(client: TestClient) -> list[dict]:
    response = client.get("/api/scenarios")
    assert response.status_code == 200
    items = response.json()
    assert items, "Expected at least one scenario in the project database"
    return items


@pytest.fixture(scope="module")
def scenario_with_results(client: TestClient, scenarios: list[dict]) -> str:
    for scenario in scenarios:
        response = client.get(f"/api/scenarios/{scenario['id']}/results")
        if response.status_code == 200:
            return scenario["id"]
    pytest.skip("No scenario with readable results was available")


@pytest.fixture()
def runtime_job_id() -> str:
    """Synthesize a completed runtime job under the (conftest-isolated)
    ``SIMULATION_ROOT``, mirroring the seed_NN/working.results layout the job
    queue writes. An earlier revision scavenged whatever leftover job dirs the
    dev machine happened to hold — which always skipped on CI and failed
    outright on real leftovers from a network with no evaluation configured
    (Vissim silently writes a results db with no KPI tables)."""
    job_id = "smokejob1"
    results_dir = simulation_runs.job_dir(job_id) / "seed_00" / "working.results"
    results_dir.mkdir(parents=True, exist_ok=True)
    build_result_db(results_dir / "1.db", 1.0, 1.0, random.Random(1))
    return job_id


def test_root_endpoint(client: TestClient):
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "VISSIM Platform API"}


def test_scenario_results_and_compare_endpoints(client: TestClient, scenarios: list[dict], scenario_with_results: str):
    results_response = client.get(f"/api/scenarios/{scenario_with_results}/results")
    assert results_response.status_code == 200

    results_payload = results_response.json()
    assert results_payload["run_count"] >= 1
    assert isinstance(results_payload["network"], dict)
    assert isinstance(results_payload["movements"], list)
    assert isinstance(results_payload.get("node_directions"), list)

    compare_ids = ",".join([scenario["id"] for scenario in scenarios[:2]])
    compare_response = client.get("/api/scenarios/compare", params={"ids": compare_ids})
    assert compare_response.status_code == 200

    compare_payload = compare_response.json()
    assert scenario_with_results in compare_payload
    assert compare_payload[scenario_with_results]["run_count"] >= 1
    assert "_comparison" in compare_payload
    assert "missing_result_ids" in compare_payload["_comparison"]
    node_direction_summary = compare_payload["_comparison"]["node_directions"]
    assert node_direction_summary, "Expected intersection-level comparison rows"
    first_node_summary = node_direction_summary[0]
    assert {"node_id", "node_name", "scenario_rows", "totals", "total_average", "total_spread"}.issubset(
        first_node_summary.keys()
    )
    assert set(first_node_summary["scenario_rows"].keys()) == set(compare_payload["_comparison"]["scenario_ids"])
    sample_scenario_row = next(iter(first_node_summary["scenario_rows"].values()))
    assert {
        "node_id",
        "node_name",
        "present",
        "incoming",
        "outgoing",
        "incoming_lanes",
        "outgoing_lanes",
        "lane_flows",
        "incoming_total",
        "outgoing_total",
        "total",
    }.issubset(sample_scenario_row.keys())

    summary_targets = list(compare_payload["_comparison"]["scenario_ids"][:2]) or [scenario_with_results]
    compare_summary_response = client.get(
        "/api/scenarios/compare-summary",
        params={"ids": ",".join(summary_targets)},
    )
    assert compare_summary_response.status_code == 200

    summary_payload = compare_summary_response.json()
    assert summary_payload["requested_ids"]
    assert scenario_with_results in summary_payload["scenario_ids"]
    assert isinstance(summary_payload["invalid_ids"], list)
    assert isinstance(summary_payload["missing_result_ids"], list)

    network_metrics = summary_payload["network_metrics"]
    assert set(network_metrics.keys()) == {"delay", "speed", "stops", "total_travel_time"}
    for metric_payload in network_metrics.values():
        assert set(metric_payload.keys()) == {"network_key", "values", "average", "min", "max", "spread"}
        assert isinstance(metric_payload["values"], dict)

    metric_leaders = summary_payload["metric_leaders"]
    assert set(metric_leaders.keys()) == {"delay", "speed", "stops", "total_travel_time"}

    health_rows = summary_payload["health_scores"]
    assert health_rows, "Expected health score rows for scenarios with results"
    for row in health_rows:
        assert 0 <= row["score"] <= 100
        assert isinstance(row["label"], str) and row["label"]
        assert set(row["components"].keys()) == {"delay", "speed", "stops", "total_travel_time"}

    status_band = summary_payload["status_band"]
    expected_tone = "success" if len(summary_payload["scenario_ids"]) > 1 else "neutral"
    assert status_band["tone"] == expected_tone
    if expected_tone == "success":
        assert "ready" in status_band["label"].lower()
    else:
        assert "single" in status_band["label"].lower()
    assert isinstance(status_band["detail"], str) and status_band["detail"]

    summary_cards = summary_payload["summary_cards"]
    assert summary_cards, "Expected dashboard summary cards"
    summary_card_ids = {card["id"] for card in summary_cards}
    assert {"scenarios_ready", "tracked_intersections", "speed_spread"}.issubset(summary_card_ids)

    ranking = summary_payload["scenario_ranking"]
    assert len(ranking) == len(summary_payload["scenario_ids"])
    assert [item["rank"] for item in ranking] == list(range(1, len(ranking) + 1))
    assert all(item["scenario_id"] in summary_payload["scenario_ids"] for item in ranking)
    assert ranking == sorted(
        ranking,
        key=lambda item: ((item.get("score") or 0), item["scenario_id"]),
        reverse=True,
    )

    insights = summary_payload["key_insights"]
    assert insights, "Expected summary insight rows"
    insight_ids = {insight["id"] for insight in insights}
    assert {"speed-leader", "delay-leader", "hotspot-node"}.issubset(insight_ids)

    congested_nodes = summary_payload["top_congested_nodes"]
    assert congested_nodes, "Expected ranked congested nodes"
    first_congested_node = congested_nodes[0]
    assert set(first_congested_node["scenario_totals"].keys()) == set(summary_payload["scenario_ids"])
    assert {"scenario_id", "value"} == set(first_congested_node["leader"].keys())

    empty_summary_response = client.get("/api/scenarios/compare-summary", params={"ids": "INVALID_SCENARIO"})
    assert empty_summary_response.status_code == 200
    empty_summary_payload = empty_summary_response.json()
    assert empty_summary_payload["scenario_ids"] == []
    assert empty_summary_payload["invalid_ids"] == ["INVALID_SCENARIO"]
    assert empty_summary_payload["missing_result_ids"] == []
    assert empty_summary_payload["health_scores"] == []
    assert empty_summary_payload["top_congested_nodes"] == []
    assert set(empty_summary_payload["network_metrics"].keys()) == {"delay", "speed", "stops", "total_travel_time"}
    assert empty_summary_payload["status_band"]["tone"] == "error"
    assert empty_summary_payload["summary_cards"][0]["tone"] == "error"
    assert empty_summary_payload["scenario_ranking"] == []
    assert any(insight["id"] == "coverage-warning" for insight in empty_summary_payload["key_insights"])


def test_compare_summary_status_band_variants(client: TestClient, scenario_with_results: str):
    single_summary_response = client.get("/api/scenarios/compare-summary", params={"ids": scenario_with_results})
    assert single_summary_response.status_code == 200
    single_summary_payload = single_summary_response.json()
    assert single_summary_payload["status_band"]["tone"] == "neutral"
    assert "single" in single_summary_payload["status_band"]["label"].lower()

    mixed_summary_response = client.get(
        "/api/scenarios/compare-summary",
        params={"ids": f"{scenario_with_results},INVALID_SCENARIO"},
    )
    assert mixed_summary_response.status_code == 200
    mixed_summary_payload = mixed_summary_response.json()
    assert mixed_summary_payload["status_band"]["tone"] == "warning"
    assert mixed_summary_payload["invalid_ids"] == ["INVALID_SCENARIO"]
    assert any(insight["id"] == "coverage-warning" for insight in mixed_summary_payload["key_insights"])


def test_network_and_geojson_endpoints_return_map_data(client: TestClient, scenario_with_results: str):
    network_response = client.get("/api/network", params={"scenario_id": scenario_with_results})
    assert network_response.status_code == 200

    network_payload = network_response.json()
    assert network_payload["links"], "Expected scenario network to include links"
    assert network_payload["nodes"], "Expected scenario network to include nodes"
    assert all("length" in link and "lanes" in link for link in network_payload["links"])

    geojson_response = client.get("/api/geojson/network", params={"scenario_id": scenario_with_results})
    assert geojson_response.status_code == 200

    geojson_payload = geojson_response.json()
    assert geojson_payload["type"] == "FeatureCollection"
    geometry_types = {feature["geometry"]["type"] for feature in geojson_payload["features"]}
    assert {"LineString", "Point"}.issubset(geometry_types)

    center_response = client.get("/api/geojson/center", params={"scenario_id": scenario_with_results})
    assert center_response.status_code == 200
    center_payload = center_response.json()
    assert -90 <= center_payload["lat"] <= 90
    assert -180 <= center_payload["lon"] <= 180
    assert center_payload["zoom"] == 16


def test_simulation_results_endpoint_reads_runtime_outputs(
    client: TestClient,
    runtime_job_id: str,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setitem(
        simulations._jobs,
        runtime_job_id,
        {
            "id": runtime_job_id,
            "status": "done",
            "progress": 1.0,
            "params": {},
            "error": None,
        },
    )

    response = client.get(f"/api/simulations/{runtime_job_id}/results")
    assert response.status_code == 200

    payload = response.json()
    assert payload["job_id"] == runtime_job_id
    assert payload["status"] == "done"
    assert payload["results"]["run_count"] >= 1
    assert isinstance(payload["results"]["datacollection"], list)


def test_simulation_results_endpoint_rejects_incomplete_jobs(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setitem(
        simulations._jobs,
        "pendingjob",
        {
            "id": "pendingjob",
            "status": "running",
            "progress": 0.5,
            "params": {},
            "error": None,
        },
    )

    response = client.get("/api/simulations/pendingjob/results")
    assert response.status_code == 409
    assert response.json()["detail"] == "Simulation is not completed yet"

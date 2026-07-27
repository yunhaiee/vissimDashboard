from __future__ import annotations

import json

from fastapi.testclient import TestClient

import main
from routers import assistant as assistant_endpoint
from services.chart_catalog import DEFAULT_VISIBLE_IDS, KPI_METRIC_KEYS, VALID_CHART_IDS
from services.dashboard_curator import (
    build_curation_prompt,
    curate_dashboard,
    extract_json_object,
    fallback_spec,
    validate_spec,
)


class JsonProvider:
    def __init__(self, payload: str):
        self.payload = payload
        self.calls = 0
        self.last_prompt: str | None = None

    def status(self):
        return {"model": "test-model", "mode": "test", "state": "ready", "ready": True}

    def generate(self, prompt, on_token=None, think=False):
        self.calls += 1
        self.last_prompt = prompt
        return self.payload, {"mode": "test"}


def test_extract_json_object_handles_fences_and_prose():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('Here you go: {"a": {"b": 2}} hope it helps') == {"a": {"b": 2}}
    assert extract_json_object("no json here") is None
    assert extract_json_object(None) is None
    assert extract_json_object('{"broken": ') is None


def test_validate_spec_filters_unknown_charts_and_tones():
    spec = validate_spec(
        {
            "headline": "h",
            "charts": [
                "network-kpi-comparison",
                "not_a_chart",
                "network-kpi-comparison",
                "travel-time-comparison",
            ],
            "insight_cards": [
                {"title": "t", "body": "b", "tone": "angry", "related_chart": "nope"},
                {"title": "", "body": ""},
            ],
        }
    )
    assert spec["charts"] == ["network-kpi-comparison", "travel-time-comparison"]
    assert len(spec["insight_cards"]) == 1
    assert spec["insight_cards"][0]["tone"] == "info"
    assert spec["insight_cards"][0]["related_chart"] is None


def test_validate_spec_keeps_valid_kpi_metrics_subset():
    spec = validate_spec(
        {
            "headline": "h",
            "charts": ["network-kpi-comparison"],
            "kpi_metrics": ["SPEEDAVG", "delayavg", "SPEEDAVG"],
        }
    )
    # Case-normalized, deduped, order preserved.
    assert spec["kpi_metrics"] == ["SPEEDAVG", "DELAYAVG"]


def test_validate_spec_drops_unknown_kpi_metrics():
    spec = validate_spec(
        {
            "headline": "h",
            "charts": ["network-kpi-comparison"],
            "kpi_metrics": ["SPEEDAVG", "NOT_A_METRIC", "totally_bogus"],
        }
    )
    assert spec["kpi_metrics"] == ["SPEEDAVG"]


def test_validate_spec_kpi_metrics_absent_or_empty_is_empty_list():
    spec_absent = validate_spec({"headline": "h", "charts": ["network-kpi-comparison"]})
    assert spec_absent["kpi_metrics"] == []

    spec_empty = validate_spec(
        {"headline": "h", "charts": ["network-kpi-comparison"], "kpi_metrics": []}
    )
    assert spec_empty["kpi_metrics"] == []

    spec_all_unknown = validate_spec(
        {"headline": "h", "charts": ["network-kpi-comparison"], "kpi_metrics": ["nope", "still_no"]}
    )
    assert spec_all_unknown["kpi_metrics"] == []


def test_validate_spec_accepts_full_kpi_metric_vocabulary():
    spec = validate_spec(
        {"headline": "h", "charts": ["network-kpi-comparison"], "kpi_metrics": list(KPI_METRIC_KEYS)}
    )
    assert spec["kpi_metrics"] == list(KPI_METRIC_KEYS)


def test_fallback_spec_never_carries_kpi_metrics():
    spec = fallback_spec("boom")
    assert "kpi_metrics" not in spec
    assert spec["charts"] == list(DEFAULT_VISIBLE_IDS)


def test_curate_dashboard_happy_path():
    payload = json.dumps(
        {
            "headline": "Signal Optimization is fastest.",
            "charts": ["network-kpi-comparison", "travel-time-comparison"],
            "insight_cards": [
                {
                    "title": "Delay",
                    "body": "23.7s vs 32.6s",
                    "tone": "success",
                    "related_chart": "network-kpi-comparison",
                }
            ],
        }
    )
    provider = JsonProvider(payload)
    result = curate_dashboard(provider, "which scenario is best?", {"compare_summary": {}})
    assert result["fallback"] is False
    assert result["charts"] == ["network-kpi-comparison", "travel-time-comparison"]
    assert result["insight_cards"][0]["tone"] == "success"
    assert provider.calls == 1
    # The prompt must carry the catalog and the strict-JSON instruction.
    assert "Available charts:" in provider.last_prompt
    assert "ONLY a JSON object" in provider.last_prompt


def test_curate_dashboard_happy_path_with_kpi_metrics():
    payload = json.dumps(
        {
            "headline": "Average speed is lower because delay rose.",
            "charts": ["network-kpi-comparison"],
            "insight_cards": [],
            "kpi_metrics": ["SPEEDAVG", "DELAYAVG"],
        }
    )
    provider = JsonProvider(payload)
    result = curate_dashboard(provider, "왜 평균속도가 낮아?", {"compare_summary": {}})
    assert result["fallback"] is False
    assert result["kpi_metrics"] == ["SPEEDAVG", "DELAYAVG"]
    # The prompt must document the metric vocabulary + the optional field.
    assert "kpi_metrics" in provider.last_prompt
    assert "SPEEDAVG" in provider.last_prompt


def test_curate_dashboard_falls_back_on_garbage():
    provider = JsonProvider("I cannot answer that, sorry!")
    result = curate_dashboard(provider, "question", {})
    assert result["fallback"] is True
    assert result["charts"] == list(DEFAULT_VISIBLE_IDS)
    assert result["insight_cards"] == []
    assert "kpi_metrics" not in result


def test_prompt_language_and_audience_rules():
    plain_ko = build_curation_prompt("q", {}, "ko", "plain")
    assert "Korean" in plain_ko
    assert "non-expert" in plain_ko
    expert_en = build_curation_prompt("q", {}, "en", "expert")
    assert "English" in expert_en
    assert "traffic engineer" in expert_en


def test_curate_endpoint(monkeypatch):
    monkeypatch.setenv("QWEN_WORKER_AUTO_START", "0")
    payload = json.dumps({"headline": "ok", "charts": ["map-selection"], "insight_cards": []})
    monkeypatch.setattr(assistant_endpoint.assistant_router, "provider", JsonProvider(payload))
    monkeypatch.setattr(assistant_endpoint, "get_compare_summary", lambda ids: {"scenario_ids": ids})

    with TestClient(main.app) as client:
        response = client.post(
            "/api/assistant/curate",
            json={"message": "overview please", "selected_scenario_ids": ["S000001"]},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["charts"] == ["map-selection"]
        assert body["model"] == "test-model"
        assert body["fallback"] is False
        assert set(body["charts"]).issubset(VALID_CHART_IDS)

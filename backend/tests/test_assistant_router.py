from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

import main
from routers import assistant as assistant_endpoint
from services import results_reader
from services.assistant_router import (
    AssistantRequestContext,
    VissimAssistantRouter,
    classify_intent,
    clean_generated_answer,
    normalize_response_language,
    parse_prompt_simulation_command,
)
from services.rag_documents import get_rag_status, search_rag


class CaptureProvider:
    def __init__(self) -> None:
        self.last_prompt = ""
        self.calls = 0

    def status(self) -> dict[str, object]:
        return {
            "model": "Qwen3.5-9B 4bit",
            "model_path": "",
            "model_path_exists": True,
            "worker_available": True,
            "worker_running": False,
            "mode": "test",
            "ready": True,
        }

    def generate(
        self,
        prompt: str,
        on_token: "Callable[[str], None] | None" = None,
        think: bool = False,
    ) -> tuple[str, dict[str, object]]:
        self.calls += 1
        self.last_prompt = prompt
        return "formatted answer", {"mode": "test"}


class EmptyProvider(CaptureProvider):
    def generate(
        self,
        prompt: str,
        on_token: "Callable[[str], None] | None" = None,
        think: bool = False,
    ) -> tuple[str | None, dict[str, object]]:
        self.calls += 1
        self.last_prompt = prompt
        return None, {"mode": "test"}


def test_assistant_intent_classification():
    assert classify_intent("\ub108\ub294 \ubb58 \ud560 \uc218 \uc788\uc5b4") == "capabilities"
    assert classify_intent("\ud604\uc7ac VISSIM \uc0c1\ud0dc \uc54c\ub824\uc918") == "status"
    assert classify_intent("\uc624\ub958 \uc6d0\uc778\uc744 \uc9c4\ub2e8\ud574\uc918") == "diagnostic"
    assert classify_intent("\uc2e0\ud638 \uc8fc\uae30\ub97c \ubcc0\uacbd\ud574\uc918") == "settings"
    assert classify_intent("\uc774 \ub300\uc2dc\ubcf4\ub4dc\uc758 \ud575\uc2ec \uc694\uc57d") == "general"


def test_clean_generated_answer_removes_reasoning():
    raw = """The user wants a one-sentence summary.
I need to inspect the context.

Draft: ## **The uploaded dataset** is ready.
(Uploaded dataset is ready.)
"""

    cleaned = clean_generated_answer(raw)

    assert cleaned == "The uploaded dataset is ready."
    assert "**" not in cleaned
    assert "##" not in cleaned

    internal = "Dashboard recent_errors list is empty.\nThere are no recent simulation errors."
    assert clean_generated_answer(internal) == "There are no recent simulation errors."
    assert clean_generated_answer("comparison_summary says all good.") == ""


def test_short_latin_message_infers_english():
    assert normalize_response_language("auto", "hello") == "en"
    assert normalize_response_language(None, "hello") == "en"
    assert normalize_response_language("auto", "\ud604\uc7ac VISSIM \uc0c1\ud0dc") == "ko"


def test_short_english_greeting_uses_english_direct_answer():
    provider = CaptureProvider()
    router = VissimAssistantRouter(provider)  # type: ignore[arg-type]

    response = router.answer(
        AssistantRequestContext(
            message="hello",
            selected_scenario_ids=[],
            selected_node_ids=[],
        )
    )

    assert response["intent"] == "general"
    assert response["runtime"]["mode"] == "dashboard-context"
    assert response["sources"] == []
    assert provider.calls == 0
    assert response["answer"].startswith("Hello.")
    assert "\ub2f5\ubcc0" not in response["answer"]


def test_identity_and_model_questions_use_direct_answers():
    provider = CaptureProvider()
    router = VissimAssistantRouter(provider)  # type: ignore[arg-type]

    identity = router.answer(
        AssistantRequestContext(
            message="\ub10c \ub204\uad6c\uc57c",
            selected_scenario_ids=[],
            selected_node_ids=[],
            response_language="ko",
        )
    )

    assert identity["runtime"]["mode"] == "dashboard-context"
    assert identity["sources"] == []
    assert provider.calls == 0
    assert "VISSIM" in identity["answer"]
    assert "\uc5b4\uc2dc\uc2a4\ud134\ud2b8" in identity["answer"]

    model = router.answer(
        AssistantRequestContext(
            message="\ubb34\uc2a8 \ubaa8\ub378\uc774\uc57c",
            selected_scenario_ids=[],
            selected_node_ids=[],
            response_language="ko",
        )
    )

    assert model["runtime"]["mode"] == "dashboard-context"
    assert provider.calls == 0
    assert "Qwen3.5-9B 4bit" in model["answer"]
    assert model["tool_results"]["assistant_model"]["model"] == "Qwen3.5-9B 4bit"

    compact_model = router.answer(
        AssistantRequestContext(
            message="\ubb34\uc2a8\ubaa8\ub378\uc774\uc57c",
            selected_scenario_ids=[],
            selected_node_ids=[],
            response_language="ko",
        )
    )

    assert compact_model["runtime"]["mode"] == "dashboard-context"
    assert provider.calls == 0
    assert "Qwen3.5-9B 4bit" in compact_model["answer"]


def test_markdown_rag_prefers_structured_error_cases():
    status = get_rag_status()
    if not status.get("sections", {}).get("vissim_manual"):
        pytest.skip("RAG docs corpus is not present in this checkout (excluded from the source drop)")
    assert status["sections"]["vissim_manual"] >= 6
    assert status["sections"]["error_cases"] >= 4

    pedestrian_hits = search_rag("Vissim pedestrian route no path error", 3)
    assert pedestrian_hits[0]["kind"] == "error_cases"
    assert "Pedestrian" in pedestrian_hits[0]["title"]

    signal_hits = search_rag("Signal Controller Program number error", 3)
    assert signal_hits[0]["kind"] == "error_cases"
    assert "Program Number" in signal_hits[0]["title"]


def test_answer_prompt_uses_intent_specific_format():
    provider = CaptureProvider()
    router = VissimAssistantRouter(provider)  # type: ignore[arg-type]

    diagnostic = router.answer(
        AssistantRequestContext(
            message="Signal Controller Program number error",
            selected_scenario_ids=[],
            selected_node_ids=[],
        )
    )

    # English message -> English-localized heading, consistent with the English
    # answer-format prompt asserted just below (previously the heading was always
    # Korean regardless of language, the mixed-language bug).
    assert diagnostic["answer_format"][0]["heading"] == "Diagnostic summary"
    assert "Diagnostic summary: Start" in provider.last_prompt
    assert "Next steps: Give" in provider.last_prompt
    assert "Preferred answer shape" in provider.last_prompt
    assert "Avoid report-like over-structuring" in provider.last_prompt
    assert "Never mention internal names" in provider.last_prompt
    assert "recent_errors" in provider.last_prompt
    assert "Markdown emphasis markers" in provider.last_prompt
    assert "double asterisks" in provider.last_prompt
    assert "Markdown heading markers" in provider.last_prompt

    router.answer(
        AssistantRequestContext(
            message="\uc2e0\ud638 \uc8fc\uae30\ub97c 120\ucd08\ub85c \ubcc0\uacbd\ud574\uc918",
            selected_scenario_ids=[],
            selected_node_ids=[],
        )
    )

    assert "\uac00\ub2a5 \uc5ec\ubd80: Say" in provider.last_prompt
    assert "\ub2e4\uc74c \uc870\uce58: Suggest" in provider.last_prompt

    router.answer(
        AssistantRequestContext(
            message="Explain the selected dashboard state",
            selected_scenario_ids=[],
            selected_node_ids=[],
            response_language="en",
        )
    )

    assert "Answer in English." in provider.last_prompt

    calls_before = provider.calls
    router.answer(
        AssistantRequestContext(
            message="Explain dashboard behavior briefly",
            selected_scenario_ids=[],
            selected_node_ids=[],
        )
    )

    assert provider.calls == calls_before + 1
    assert "Answer in English." in provider.last_prompt


def test_capabilities_answer_omits_dashboard_metrics_and_sources():
    provider = CaptureProvider()
    router = VissimAssistantRouter(provider)  # type: ignore[arg-type]

    response = router.answer(
        AssistantRequestContext(
            message="\ub108\ub294 \ubb58 \ud560 \uc218 \uc788\uc5b4",
            selected_scenario_ids=[],
            selected_node_ids=[],
        )
    )

    assert response["intent"] == "capabilities"
    assert response["runtime"]["mode"] == "dashboard-context"
    assert response["answer_format"][0]["heading"] == "\ud560 \uc218 \uc788\ub294 \uc77c"
    assert response["sources"] == []
    assert "assistant_capabilities" in response["tool_results"]
    assert provider.calls == 0
    assert "S000001" not in response["answer"]
    assert "\ubb38\uc11c \uadfc\uac70:" not in response["answer"]
    assert "\ub300\uc2dc\ubcf4\ub4dc \ub370\uc774\ud130:" not in response["answer"]


def test_english_capabilities_question_uses_english_fallback():
    provider = CaptureProvider()
    router = VissimAssistantRouter(provider)  # type: ignore[arg-type]

    response = router.answer(
        AssistantRequestContext(
            message="What can you do for this VISSIM dashboard?",
            selected_scenario_ids=[],
            selected_node_ids=[],
        )
    )

    assert response["intent"] == "capabilities"
    assert response["runtime"]["mode"] == "dashboard-context"
    assert provider.calls == 0
    assert "Capabilities:" in response["answer"]
    assert "Required input:" in response["answer"]


def test_selected_node_details_aggregate_volume_delay_and_queue(monkeypatch):
    scenario_results = {
        "S1": {
            "node_directions": [
                {
                    "node_id": "101",
                    "node_name": "Node 101",
                    "incoming": {"north": 100, "east": 50, "south": 0, "west": 0},
                    "outgoing": {"north": 80, "east": 70, "south": 0, "west": 0},
                    "lane_flows": [
                        {
                            "from_direction": "north",
                            "to_direction": "east",
                            "from_lane": 1,
                            "to_lane": 1,
                            "volume": 90,
                            "estimated": False,
                        }
                    ],
                }
            ],
            "movements": [
                {"OBJECT_ID": "101-1-0-2-0", "VEHDELAY_MEAN": 20, "VEHS_MEAN": 100, "STOPS_MEAN": 5},
                {"OBJECT_ID": "101-3-0-4-0", "VEHDELAY_MEAN": 40, "VEHS_MEAN": 50, "STOPS_MEAN": 3},
            ],
            "queues": [{"OBJECT_ID": "101-1", "QLENMAX_MEAN": 12}],
        },
        "S2": {
            "node_directions": [
                {
                    "node_id": "101",
                    "node_name": "Node 101",
                    "incoming": {"north": 120, "east": 30, "south": 10, "west": 0},
                    "outgoing": {"north": 100, "east": 40, "south": 20, "west": 0},
                    "lane_flows": [],
                }
            ],
            "movements": [{"OBJECT_ID": "101-1-0-2-0", "VEHDELAY_MEAN": 35, "VEHS_MEAN": 120}],
            "queues": [{"OBJECT_ID": "101-1", "QLENMAX_MEAN": 18}],
        },
    }
    monkeypatch.setattr(
        results_reader,
        "_collect_compare_results",
        lambda ids: (ids, scenario_results, [], []),
    )
    monkeypatch.setattr(
        results_reader,
        "_scenario_meta_lookup",
        lambda: {"S1": {"name": "Base"}, "S2": {"name": "Alt"}},
    )

    details = results_reader.get_selected_node_details(["S1", "S2"], ["101"])
    node = details["nodes"][0]

    assert node["node_id"] == "101"
    assert node["scenarios"][0]["scenario_name"] == "Base"
    assert node["scenarios"][0]["avg_delay_sec"] == 26.7
    assert node["scenarios"][1]["delta_from_baseline"]["max_queue_length"] == 6.0
    assert node["comparison"]["highest_queue"]["scenario_name"] == "Alt"
    assert node["scenarios"][0]["top_approaches"][0]["direction"] == "north"


def test_selected_intersection_context_reaches_prompt_and_fallback(monkeypatch):
    provider = EmptyProvider()
    monkeypatch.setattr(
        "services.assistant_router.get_active_dataset",
        lambda: {
            "source": "uploaded",
            "kind": "inpx",
            "filename": "uploaded.inpx",
            "has_project_db": True,
            "has_scenarios": True,
            "has_real_counts": True,
        },
    )
    monkeypatch.setattr(
        "services.assistant_router.list_scenarios",
        lambda: [{"id": "S1", "name": "Base"}, {"id": "S2", "name": "Alt"}],
    )
    monkeypatch.setattr(
        "services.assistant_router.get_compare_summary",
        lambda ids: {"scenario_ids": ids, "status_band": {"title": "Ready"}},
    )
    monkeypatch.setattr(
        "services.assistant_router.get_selected_node_details",
        lambda ids, node_ids: {
            "selected_node_ids": node_ids,
            "scenario_ids": ids,
            "nodes": [
                {
                    "node_id": "101",
                    "node_name": "Node 101",
                    "scenarios": [
                        {
                            "scenario_id": "S1",
                            "scenario_name": "Base",
                            "present": True,
                            "total_volume": 150,
                            "top_approaches": [{"direction": "north", "volume": 100}],
                            "top_movements": [
                                {
                                    "from_direction": "north",
                                    "to_direction": "east",
                                    "from_lane": 1,
                                    "to_lane": 1,
                                    "volume": 90,
                                }
                            ],
                            "direction_delay_sec": {"north": 25.0},
                        },
                        {
                            "scenario_id": "S2",
                            "scenario_name": "Alt",
                            "present": True,
                            "total_volume": 180,
                            "avg_delay_sec": 35,
                            "max_queue_length": 18,
                            "top_approaches": [{"direction": "north", "volume": 120}],
                            "top_movements": [],
                            "direction_delay_sec": {"north": 40.0},
                        },
                    ],
                    "comparison": {
                        "highest_volume": {"scenario_id": "S2", "scenario_name": "Alt", "value": 180},
                        "highest_delay": {"scenario_id": "S2", "scenario_name": "Alt", "value": 35},
                        "lowest_delay": {"scenario_id": "S1", "scenario_name": "Base", "value": 20},
                        "highest_queue": {"scenario_id": "S2", "scenario_name": "Alt", "value": 18},
                    },
                }
            ],
            "missing_node_ids": [],
        },
    )

    router = VissimAssistantRouter(provider)  # type: ignore[arg-type]
    response = router.answer(
        AssistantRequestContext(
            message="\uc120\ud0dd\ud55c \uad50\ucc28\ub85c\ub97c \uc790\uc138\ud788 \uc124\uba85\ud574\uc918",
            selected_scenario_ids=["S1", "S2"],
            selected_node_ids=["101"],
        )
    )

    assert "selected_intersection_details" in provider.last_prompt
    assert response["tool_results"]["selected_intersection_details"]["nodes"][0]["node_id"] == "101"
    assert any(source["title"] == "Selected intersection dashboard details" for source in response["sources"])
    assert "Node 101" in response["answer"]
    assert "Alt" in response["answer"]
    assert "QLENMAX" in response["answer"]
    assert provider.calls == 1


def test_status_answer_includes_vissim_com_tool_context():
    provider = CaptureProvider()

    def fake_com_status() -> dict[str, object]:
        return {
            "available": True,
            "executed": True,
            "connected": True,
            "reason": None,
            "prog_id": "VISSIM.Vissim.2026",
            "launched": False,
            "source_inpx": "practice.inpx",
            "source_inpx_exists": True,
            "network": {"name": "practice"},
            "simulation": {"sim_sec": 12, "sim_period": 3600},
            "counts": {"links": 42, "signal_controllers": 3},
            "signal_controllers": [{"No": 1, "CycleTime": 90}],
            "vehicle_inputs": [{"No": 10, "Volume(1)": 1200}],
        }

    router = VissimAssistantRouter(provider, com_status_reader=fake_com_status)  # type: ignore[arg-type]
    response = router.answer(
        AssistantRequestContext(
            message="\ud604\uc7ac VISSIM COM \uc0c1\ud0dc \uc54c\ub824\uc918",
            selected_scenario_ids=[],
            selected_node_ids=[],
        )
    )

    assert response["tool_results"]["vissim_com"]["connected"] is True
    assert response["tool_results"]["vissim_com"]["counts"]["links"] == 42
    assert any(source["title"] == "Vissim COM Tool" for source in response["sources"])
    assert response["runtime"]["mode"] == "dashboard-context"
    assert provider.calls == 0
    assert "\uc0c1\ud0dc \uc694\uc57d:" in response["answer"]
    assert "practice.inpx" in response["answer"]
    assert "\ucc28\ub7c9 \uc785\ub825 \ubc88\ud638 \uc608\uc2dc: 10" in response["answer"]
    assert "\uc2e0\ud638\uc81c\uc5b4\uae30 \ubc88\ud638 \uc608\uc2dc: 1" in response["answer"]


def test_english_status_question_uses_english_fallback():
    provider = CaptureProvider()

    def fake_com_status() -> dict[str, object]:
        return {
            "available": True,
            "executed": True,
            "connected": True,
            "reason": None,
            "source_inpx": "practice.inpx",
            "simulation": {"sim_sec": 12, "sim_period": 3600},
            "counts": {"links": 42, "signal_controllers": 3},
            "signal_controllers": [{"No": 1, "CycleTime": 90}],
            "vehicle_inputs": [{"No": 10, "Volume(1)": 1200}],
        }

    router = VissimAssistantRouter(provider, com_status_reader=fake_com_status)  # type: ignore[arg-type]
    response = router.answer(
        AssistantRequestContext(
            message="Tell me the current VISSIM COM status",
            selected_scenario_ids=[],
            selected_node_ids=[],
        )
    )

    assert response["intent"] == "status"
    assert response["runtime"]["mode"] == "dashboard-context"
    assert provider.calls == 0
    assert "Status summary:" in response["answer"]
    assert "Vehicle input examples: 10" in response["answer"]


def test_status_answer_requires_uploaded_dataset(monkeypatch):
    provider = CaptureProvider()
    monkeypatch.setattr(
        "services.assistant_router.get_active_dataset",
        lambda: {
            "source": "default",
            "kind": "default",
            "filename": "practice.inpx",
            "has_project_db": True,
            "has_scenarios": True,
            "has_real_counts": False,
        },
    )
    monkeypatch.setattr("services.assistant_router.list_scenarios", lambda: [{"id": "S000001"}])
    monkeypatch.setattr(
        "services.assistant_router.get_compare_summary",
        lambda ids: {"scenario_ids": ids, "status_band": {"title": "Should not be used"}},
    )

    router = VissimAssistantRouter(provider)  # type: ignore[arg-type]
    response = router.answer(
        AssistantRequestContext(
            message="\ud604\uc7ac VISSIM \uc0c1\ud0dc\uc640 \ub370\uc774\ud130\uc14b \uc0c1\ud0dc \uc694\uc57d",
            selected_scenario_ids=[],
            selected_node_ids=[],
        )
    )

    assert response["intent"] == "status"
    assert response["runtime"]["mode"] == "dashboard-context"
    assert response["tool_results"]["scenario_count"] == 0
    assert response["tool_results"]["selected_scenario_ids"] == []
    assert response["tool_results"]["comparison_summary"]["scenario_ids"] == []
    assert provider.calls == 0
    assert "\uc5c5\ub85c\ub4dc\ud558\uae30 \uc804" in response["answer"]
    assert "S000001" not in response["answer"]
    assert "Should not be used" not in response["answer"]


def test_dashboard_diagnostic_answer_does_not_expose_internal_keys(monkeypatch):
    provider = CaptureProvider()
    monkeypatch.setattr(
        "services.assistant_router.get_active_dataset",
        lambda: {
            "source": "uploaded",
            "kind": "inpx",
            "filename": "uploaded.inpx",
            "has_project_db": True,
            "has_scenarios": True,
            "has_real_counts": True,
        },
    )
    monkeypatch.setattr("services.assistant_router.list_scenarios", lambda: [{"id": "S1"}, {"id": "S2"}])
    monkeypatch.setattr(
        "services.assistant_router.get_compare_summary",
        lambda ids: {
            "scenario_ids": ids,
            "invalid_ids": ["S404"],
            "missing_result_ids": ["S2"],
            "status_band": {"title": "\ud655\uc778 \ud544\uc694"},
        },
    )
    monkeypatch.setattr(
        "services.assistant_router.list_job_records",
        lambda: [
            {"id": "job-1", "name": "base", "status": "completed"},
            {"id": "job-2", "name": "variant", "status": "error", "error": "No path was found."},
        ],
    )

    def fake_com_status() -> dict[str, object]:
        return {
            "available": True,
            "executed": True,
            "connected": False,
            "reason": "VISSIM process is not running.",
        }

    router = VissimAssistantRouter(provider, com_status_reader=fake_com_status)  # type: ignore[arg-type]
    response = router.answer(
        AssistantRequestContext(
            message="\ucd5c\uadfc \uc2dc\ubbac\ub808\uc774\uc158 \uc624\ub958\ub098 \uc774\uc0c1 \uc9d5\ud6c4\ub97c \uc9c4\ub2e8\ud574\uc918",
            selected_scenario_ids=[],
            selected_node_ids=[],
        )
    )

    assert response["intent"] == "diagnostic"
    assert response["runtime"]["mode"] == "dashboard-context"
    assert provider.calls == 0
    assert "\uc9c4\ub2e8 \uc694\uc57d:" in response["answer"]
    assert "\uadfc\uac70:" in response["answer"]
    assert "\ub2e4\uc74c \uc870\uce58:" in response["answer"]
    for internal_name in [
        "simulation_jobs",
        "comparison_summary",
        "recent_errors",
        "vissim_com",
        "tool_results",
        "rag_chunks",
        "selected_scenario_ids",
    ]:
        assert internal_name not in response["answer"]


def test_english_diagnostic_answer_omits_korean_source_job_name(monkeypatch):
    provider = CaptureProvider()
    monkeypatch.setattr(
        "services.assistant_router.get_active_dataset",
        lambda: {
            "source": "uploaded",
            "kind": "inpx",
            "filename": "uploaded.inpx",
            "has_project_db": True,
            "has_scenarios": True,
            "has_real_counts": True,
        },
    )
    monkeypatch.setattr("services.assistant_router.list_scenarios", lambda: [{"id": "S1"}, {"id": "S2"}])
    monkeypatch.setattr(
        "services.assistant_router.get_compare_summary",
        lambda ids: {"scenario_ids": ids, "invalid_ids": [], "missing_result_ids": []},
    )
    monkeypatch.setattr(
        "services.assistant_router.list_job_records",
        lambda: [
            {
                "id": "job-ko",
                "name": "prompt: \uc2e0\ud638 1\ubc88 \uc8fc\uae30 90\ucd08, \ucc28\ub7c9 \uc785\ub825 10\ubc88 1200",
                "status": "error",
                "error": "Vehicle input 10 does not exist in the loaded VISSIM network.",
            }
        ],
    )

    router = VissimAssistantRouter(provider, com_status_reader=lambda: {"connected": False, "reason": "No running VISSIM COM instance was found."})  # type: ignore[arg-type]
    response = router.answer(
        AssistantRequestContext(
            message="diagnose recent simulation errors",
            selected_scenario_ids=[],
            selected_node_ids=[],
            response_language="en",
        )
    )

    assert response["runtime"]["mode"] == "dashboard-context"
    assert "Diagnostic summary:" in response["answer"]
    assert "`job-ko`" in response["answer"]
    assert "Vehicle input 10 does not exist" in response["answer"]
    assert not re.search(r"[\uac00-\ud7a3]", response["answer"])


def test_prompt_simulation_parser_extracts_whitelisted_params():
    command = parse_prompt_simulation_command(
        "신호 1번 주기 90초, 차량 입력 10번 1200, seed 77로 300초 실행해"
    )

    assert command["recognized"] is True
    assert command["params"]["sim_duration"] == 300
    assert command["params"]["random_seed"] == 77
    assert command["params"]["vehicle_inputs"] == {10: 1200.0}
    assert command["params"]["signal_cycles"] == {1: 90}
    assert command["params"]["signal_offsets"] == {}


def test_prompt_simulation_executes_with_allow_tool_execution(monkeypatch):
    provider = CaptureProvider()
    started: dict[str, object] = {}

    monkeypatch.setattr(
        "services.assistant_router.get_prompt_simulation_capabilities",
        lambda: {"can_run": True, "reason": None},
    )

    def fake_start(params: dict[str, object]) -> dict[str, object]:
        started.update(params)
        return {"id": "job123", "status": "queued", "scenario_result_id": "SIM_job123"}

    monkeypatch.setattr("services.assistant_router.start_prompt_simulation_job", fake_start)

    router = VissimAssistantRouter(provider)  # type: ignore[arg-type]
    response = router.answer(
        AssistantRequestContext(
            message="signal 1 cycle 90 and vehicle input 10 1200 for 300 seconds run",
            selected_scenario_ids=[],
            selected_node_ids=[],
            allow_tool_execution=True,
        )
    )

    prompt_simulation = response["tool_results"]["prompt_simulation"]
    assert response["intent"] == "settings"
    assert response["runtime"]["mode"] == "dashboard-context"
    assert response["safety"]["allowed"] is True
    assert prompt_simulation["executed"] is True
    assert prompt_simulation["job"]["id"] == "job123"
    assert started["sim_duration"] == 300
    assert started["signal_cycles"] == {1: 90}
    assert provider.calls == 0
    assert "Execution started:" in response["answer"]
    assert "job123" in response["answer"]


def test_assistant_status_and_query_endpoints(monkeypatch):
    monkeypatch.setenv("QWEN_WORKER_AUTO_START", "0")

    with TestClient(main.app) as client:
        status_response = client.get("/api/assistant/status")
        assert status_response.status_code == 200
        status_payload = status_response.json()
        # Provider is backend-selected (Claude API when a key is configured,
        # local Qwen otherwise) — assert the seam, not a specific model.
        assert status_payload["model"] == assistant_endpoint.assistant_router.provider.status()["model"]
        assert "worker_available" in status_payload
        assert {"general", "status", "diagnostic", "settings", "capabilities"}.issubset(status_payload["router"]["intents"])
        assert "vissim_com" in status_payload["tools"]

        tool_response = client.get("/api/assistant/tools/vissim-com/status")
        assert tool_response.status_code == 200
        assert "connected" in tool_response.json()

        query_response = client.post("/api/assistant/query", json={"message": "current VISSIM status", "response_language": "en"})
        assert query_response.status_code == 200
        query_payload = query_response.json()
        assert query_payload["intent"] == "status"
        assert query_payload["answer"]
        assert "Status summary:" in query_payload["answer"]
        # response_language="en" -> the answer_format heading is now localized to
        # match the body (previously this always returned Korean regardless of
        # language, which was the bug behind the mixed-language answer cards).
        assert query_payload["answer_format"][0]["heading"] == "Status summary"
        assert "model_status" in query_payload
        assert "vissim_com" in query_payload["tool_results"]

        capabilities_response = client.post("/api/assistant/query", json={"message": "\ub108\ub294 \ubb58 \ud560 \uc218 \uc788\uc5b4"})
        assert capabilities_response.status_code == 200
        capabilities_payload = capabilities_response.json()
        assert capabilities_payload["intent"] == "capabilities"
        assert capabilities_payload["sources"] == []
        assert capabilities_payload["answer_format"][0]["heading"] == "\ud560 \uc218 \uc788\ub294 \uc77c"

        stream_response = client.post("/api/assistant/query/stream", json={"message": "current VISSIM status", "response_language": "en"})
        assert stream_response.status_code == 200
        stream_events = [json.loads(line) for line in stream_response.text.splitlines() if line.strip()]
        assert [event["type"] for event in stream_events[:2]] == ["status", "status"]
        assert any(event["type"] == "metadata" for event in stream_events)
        assert any(event["type"] == "chunk" and event.get("text") for event in stream_events)
        done_event = stream_events[-1]
        assert done_event["type"] == "done"
        assert done_event["response"]["answer"]
        assert done_event["response"]["runtime"]["mode"] == "dashboard-context"


def test_query_endpoint_recovers_router_exceptions(monkeypatch):
    class BrokenRouter:
        def answer(self, request: AssistantRequestContext) -> dict[str, object]:
            raise RuntimeError("forced assistant failure")

        def status(self) -> dict[str, object]:
            return {
                "model": "Qwen3.5-9B 4bit",
                "model_path": "",
                "model_path_exists": True,
                "qwen_python_exists": True,
                "endpoint_configured": False,
                "in_process_enabled": False,
                "worker_available": True,
                "worker_running": False,
                "worker_error": "forced assistant failure",
                "mode": "stdio-worker",
                "state": "failed",
                "ready": False,
            }

    monkeypatch.setattr(assistant_endpoint, "assistant_router", BrokenRouter())

    with TestClient(main.app) as client:
        response = client.post(
            "/api/assistant/query",
            json={"message": "Explain dashboard behavior briefly", "response_language": "en"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime"]["mode"] == "error-fallback"
    assert payload["runtime"]["recoverable"] is True
    assert payload["runtime"]["error_id"]
    assert payload["tool_results"]["assistant_error"]["error_id"] == payload["runtime"]["error_id"]
    assert "server error" in payload["answer"].lower()
    assert "Internal Server Error" not in payload["answer"]


def test_stream_endpoint_recovers_router_exceptions(monkeypatch):
    class BrokenRouter:
        def answer(self, request: AssistantRequestContext) -> dict[str, object]:
            raise RuntimeError("forced assistant stream failure")

        def status(self) -> dict[str, object]:
            return {
                "model": "Qwen3.5-9B 4bit",
                "model_path": "",
                "model_path_exists": True,
                "qwen_python_exists": True,
                "endpoint_configured": False,
                "in_process_enabled": False,
                "worker_available": True,
                "worker_running": False,
                "worker_error": "forced assistant stream failure",
                "mode": "stdio-worker",
                "state": "failed",
                "ready": False,
            }

    monkeypatch.setattr(assistant_endpoint, "assistant_router", BrokenRouter())

    with TestClient(main.app) as client:
        response = client.post(
            "/api/assistant/query/stream",
            json={"message": "Explain dashboard behavior briefly", "response_language": "en"},
        )

    assert response.status_code == 200
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert events[0]["type"] == "status"
    assert events[-1]["type"] == "done"
    assert events[-1]["response"]["runtime"]["mode"] == "error-fallback"
    assert events[-1]["response"]["runtime"]["recoverable"] is True
    assert any(event["type"] == "chunk" and "fallback" in event.get("text", "").lower() for event in events)

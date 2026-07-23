"""Tests for M2.3's approval-pause wiring in orchestrator.py:
``ProposeWriteGate.authorize()`` raising ``ApprovalRequired`` -> the loop
preflights right away, then either returns ``kind="approval_request"``
(clean or warn-tier preflight) or feeds the planner the ordinary structured
refusal WITHOUT ever asking for approval (blocking preflight) — mirroring
test_agent_orchestrator_write_gate.py's fixtures and real-ArtifactManager-
over-the-demo-network pattern exactly, so this file focuses purely on the
NEW branch M2.3 adds.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from services.agent.gates import ProposeWriteGate
from services.agent.guardrails import GuardrailEngine
from services.agent.orchestrator import run_agent_turn
from services.agent.state import ArtifactManager
from services.agent.toolkit import ToolRegistry, tool
from services.agent.trace import read_trace

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"


class FakeStructuredProvider:
    """Same canned (action, meta) queue fixture as
    test_agent_orchestrator_write_gate.py's fixture of the same name."""

    def __init__(
        self,
        queue: list[tuple[dict[str, Any] | None, dict[str, Any]]],
        *,
        repeat_last: bool = False,
    ) -> None:
        self._queue = list(queue)
        self._repeat_last = repeat_last
        self.calls: list[dict[str, Any]] = []

    def generate_structured(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        self.calls.append(
            {"messages": messages, "tools": tools, "schema": schema, "system": system}
        )
        if not self._queue:
            raise AssertionError("FakeStructuredProvider queue exhausted unexpectedly")
        if self._repeat_last and len(self._queue) == 1:
            return self._queue[0]
        return self._queue.pop(0)


def _action(
    tool_name: str, *, summary: str = "확인합니다", **params: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "fake", "fallback_rung": 0}


def _register_rigged_write_tool(registry: ToolRegistry) -> dict[str, int]:
    """Same rigged ``set_vehicle_input_volume`` fake as
    test_agent_orchestrator_write_gate.py — the fake's own dispatch function
    must NEVER execute in any test in this file, since the whole point of
    the approval pause is stopping BEFORE dispatch."""
    calls = {"count": 0}

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")

        input_no: str
        volume: float

    @tool(
        name="set_vehicle_input_volume",
        tier="write",
        provider="edit",
        params=Params,
        registry=registry,
    )
    def _set_volume(params: Params) -> dict[str, Any]:
        """Set a vehicle input's volume (fake/test double)."""
        calls["count"] += 1
        return {"verification": {"written_value": params.volume, "readback_value": params.volume}}

    return calls


# ---------------------------------------------------------------------------
# (a) clean preflight -> approval_request, user_confirm traced, no dispatch
# ---------------------------------------------------------------------------


def test_propose_gate_pauses_with_approval_request_and_traces_user_confirm(tmp_path: Path):
    registry = ToolRegistry()
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()
    calls = _register_rigged_write_tool(registry)

    provider = FakeStructuredProvider(
        [
            _action(
                "set_vehicle_input_volume",
                summary="교통량을 600으로 변경 제안",
                input_no="1",
                volume=600,
            ),
        ]
    )

    result = run_agent_turn(
        "1번 입력 교통량을 600으로 바꿔줘",
        provider=provider,
        work_dir=work_dir,
        session_id="s-approval-clean",
        language="ko",
        registry=registry,
        gate=ProposeWriteGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    assert result.kind == "approval_request"
    assert calls["count"] == 0  # dispatch never happened
    assert artifact_manager.version == 0  # nothing was ever saved
    assert len(provider.calls) == 1  # the planner was asked exactly once

    assert result.pending_approval == {
        "tool": "set_vehicle_input_volume",
        "params": {"input_no": "1", "volume": 600.0},
        "summary": "교통량을 600으로 변경 제안",
        "warnings": [],
    }
    assert "set_vehicle_input_volume" in result.answer
    assert result.state.history == []  # nothing dispatched, nothing to log there

    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records] == ["plan", "preflight", "user_confirm"]
    assert records[1]["payload"]["blocking"] is False
    user_confirm_payload = records[2]["payload"]
    assert user_confirm_payload["stage"] == "requested"
    assert user_confirm_payload["tool"] == "set_vehicle_input_volume"
    assert user_confirm_payload["params"] == {"input_no": "1", "volume": 600.0}
    assert user_confirm_payload["warnings"] == []

    # The step narration includes a distinct pending status for the UI.
    assert result.steps[-1] == {
        "summary": "교통량을 600으로 변경 제안",
        "tool": "set_vehicle_input_volume",
        "status": "pending_approval",
    }


# ---------------------------------------------------------------------------
# (b) warn-tier preflight -> STILL approval_request, warnings attached
# ---------------------------------------------------------------------------


def test_propose_gate_warn_tier_preflight_still_pauses_with_warnings_attached(tmp_path: Path):
    # link 101 has 3 lanes -> rough capacity 3*1900 = 5700 veh/h; 6000
    # exceeds it -> latent_demand_overflow (warn, not refuse) — matches
    # test_agent_orchestrator_write_gate.py's identical fixture choice.
    registry = ToolRegistry()
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()
    calls = _register_rigged_write_tool(registry)

    provider = FakeStructuredProvider(
        [
            _action(
                "set_vehicle_input_volume",
                summary="교통량을 6000으로 변경 제안",
                input_no="1",
                volume=6000,
            ),
        ]
    )

    result = run_agent_turn(
        "1번 입력 교통량을 6000으로 바꿔줘",
        provider=provider,
        work_dir=work_dir,
        session_id="s-approval-warn",
        language="ko",
        registry=registry,
        gate=ProposeWriteGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    assert result.kind == "approval_request"
    assert calls["count"] == 0

    assert result.pending_approval["warnings"] == [
        {
            "constraint_id": "latent_demand_overflow",
            "message": result.pending_approval["warnings"][0]["message"],
        }
    ]
    assert "잠재 수요" in result.pending_approval["warnings"][0]["message"]
    # The proposal answer surfaces the warning too, not just the pending_approval dict.
    assert "잠재 수요" in result.answer

    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records] == ["plan", "preflight", "user_confirm"]
    assert records[2]["payload"]["warnings"][0]["constraint_id"] == "latent_demand_overflow"


# ---------------------------------------------------------------------------
# (c) blocking preflight -> refused WITHOUT ever requesting approval
# ---------------------------------------------------------------------------


def test_propose_gate_blocking_preflight_refuses_without_requesting_approval(tmp_path: Path):
    registry = ToolRegistry()
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()
    calls = _register_rigged_write_tool(registry)

    provider = FakeStructuredProvider(
        [
            _action(
                "set_vehicle_input_volume",
                summary="교통량을 수정합니다",
                input_no="999-bogus",
                volume=600,
            ),
            _action(
                "respond",
                summary="대상을 찾지 못했습니다",
                answer="999-bogus 입력을 찾을 수 없습니다.",
            ),
        ]
    )

    result = run_agent_turn(
        "999-bogus 입력 교통량을 600으로 바꿔줘",
        provider=provider,
        work_dir=work_dir,
        session_id="s-approval-blocked",
        language="ko",
        registry=registry,
        gate=ProposeWriteGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    # Never paused for approval -- would have been refused anyway.
    assert result.kind == "answer"
    assert result.pending_approval is None
    assert calls["count"] == 0
    assert artifact_manager.version == 0
    assert len(provider.calls) == 2  # the loop continued to a second decision

    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records] == ["plan", "preflight", "refusal", "plan"]
    assert not any(r["kind"] == "user_confirm" for r in records)
    assert records[1]["payload"]["blocking"] is True
    assert records[2]["payload"]["error_type"] == "object_not_found"

    assert result.state.history[0].observation["error_type"] == "object_not_found"
    assert "999-bogus" in result.answer


# ---------------------------------------------------------------------------
# (d) no artifact_manager supplied -> preflight against {} (never raises;
#     unlike the gate-approved-write path, a gate that only REQUESTS
#     approval does not require an ArtifactManager to exist yet)
# ---------------------------------------------------------------------------


def test_propose_gate_without_artifact_manager_preflights_against_empty_model_and_refuses(
    tmp_path: Path,
):
    registry = ToolRegistry()
    calls = _register_rigged_write_tool(registry)

    provider = FakeStructuredProvider(
        [
            _action(
                "set_vehicle_input_volume",
                summary="교통량을 수정합니다",
                input_no="1",
                volume=600,
            ),
            _action(
                "respond",
                summary="확인 불가 안내",
                answer="네트워크 정보를 확인할 수 없어 수정을 진행할 수 없습니다.",
            ),
        ]
    )

    result = run_agent_turn(
        "1번 입력 교통량을 600으로 바꿔줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-approval-no-am",
        language="ko",
        registry=registry,
        gate=ProposeWriteGate(),
        engine=GuardrailEngine(),
        # artifact_manager intentionally omitted -- must not raise.
    )

    assert calls["count"] == 0
    # Against an empty ({}) shadow model, "1" is never found -> refuse
    # (object_not_found), same conservative outcome as a real missing id.
    assert result.kind == "answer"
    assert result.pending_approval is None

    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records] == ["plan", "preflight", "refusal", "plan"]
    assert records[2]["payload"]["error_type"] == "object_not_found"

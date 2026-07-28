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

from services.agent.gates import PreapprovedWriteGate, ProposeWriteGate, signature
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


def _register_persisting_write_tool(
    registry: ToolRegistry, artifact_manager: ArtifactManager
) -> dict[str, int]:
    """A rigged ``set_vehicle_input_volume`` that ALSO persists the written
    value into the working copy's XML (same technique as
    test_agent_orchestrator_write_gate.py's identically-purposed fixture) --
    required by the section (e) test below, which (unlike every other test
    in this file) exercises a REAL successful dispatch through
    ``PreapprovedWriteGate`` and must survive the real
    ``GuardrailEngine.postwrite`` target-persisted check rather than stop
    before dispatch."""
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
        from eval.fake_edit_session import set_vehicle_input_volume_in_xml

        calls["count"] += 1
        working = artifact_manager.working_path
        working.write_text(
            set_vehicle_input_volume_in_xml(
                working.read_text(encoding="utf-8"), str(params.input_no), params.volume
            ),
            encoding="utf-8",
        )
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

    # Approval-card mini-map (v2 UX unit): popped out and checked separately
    # -- its OWN correctness (per-tool resolution, bbox math, the graceful
    # no-coordinates path) is test_agent_approval_focus.py's job; this test's
    # job is orchestrator WIRING, i.e. that a "focus" key reaches
    # pending_approval at all and points at the real target. Input 1 has a
    # real, resolvable location on the demo network (link 101), so the wired
    # call must find it.
    focus = result.pending_approval.pop("focus", None)
    assert result.pending_approval == {
        "tool": "set_vehicle_input_volume",
        "params": {"input_no": "1", "volume": 600.0},
        "summary": "교통량을 600으로 변경 제안",
        "warnings": [],
    }
    assert focus is not None and focus["bbox"] is not None
    assert {"type": "vehicle_input", "id": "1"} in [
        {"type": e["type"], "id": e["id"]} for e in focus["elements"]
    ]
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


# ---------------------------------------------------------------------------
# (e) post-incident regression: PreapprovedWriteGate re-run, planner
# re-issues the SAME already-dispatched action a second time in the SAME
# turn -- docs/phase2/DECISIONS.md 2026-07-24 live-demo bug ("one user
# approval authorized SIX executions of run_simulation"). First attempt must
# dispatch and postwrite-verify normally; the SECOND must be refused via the
# distinct approval_consumed message (never silently re-authorized, never
# re-dispatched); the turn must still end in a normal `respond` answer, not
# the forced K-cap completion.
# ---------------------------------------------------------------------------


def test_preapproved_gate_refuses_a_same_turn_reissue_of_an_already_consumed_action(
    tmp_path: Path,
):
    registry = ToolRegistry()
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()
    calls = _register_persisting_write_tool(registry, artifact_manager)

    # NOTE: decision.params never carries "summary" (planner.py pops it into
    # PlannerDecision.summary before the tool params dict is built -- see
    # the pending_approval assertions elsewhere in this file), and the raw
    # value type from the fake provider's queue (int 600, not float) is
    # exactly what reaches the gate's authorize() call -- signature()
    # distinguishes int/float (see gates.py's own docstring), so this must
    # match the `_action(..., volume=600)` call below byte-for-byte.
    approved_action = {
        "tool": "set_vehicle_input_volume",
        "params": {"input_no": "1", "volume": 600},
    }
    gate = PreapprovedWriteGate({signature(approved_action["tool"], approved_action["params"])})

    provider = FakeStructuredProvider(
        [
            _action(
                "set_vehicle_input_volume",
                summary="승인된 변경을 적용합니다",
                input_no="1",
                volume=600,
            ),
            _action(
                # The buggy behavior this fix closes: the planner reacts to
                # something (e.g. a KPI/warning-shaped observation) by
                # re-issuing the IDENTICAL already-approved write again,
                # instead of reporting the result to the user.
                "set_vehicle_input_volume",
                summary="다시 한 번 적용을 시도합니다",
                input_no="1",
                volume=600,
            ),
            _action(
                "respond",
                summary="완료 보고",
                answer="1번 입력 교통량을 600으로 변경했습니다.",
            ),
        ]
    )

    result = run_agent_turn(
        "1번 입력 교통량을 600으로 바꿔줘",
        provider=provider,
        work_dir=work_dir,
        session_id="s-approval-consumed",
        language="ko",
        registry=registry,
        gate=gate,
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    # Dispatched exactly once -- the re-issued second attempt never reached
    # the tool, and the turn resolved normally (not the K-cap).
    assert calls["count"] == 1
    assert result.kind == "answer"
    assert len(provider.calls) == 3
    assert "600" in result.answer

    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records] == [
        "plan",
        "preflight",
        "action_start",
        "action_end",
        "postwrite",
        "plan",
        "refusal",
        "plan",
    ]
    refusal_record = records[6]
    assert refusal_record["payload"]["error_type"] == "approval_consumed"
    assert refusal_record["payload"]["tool"] == "set_vehicle_input_volume"

    # The refused re-issue's structured observation reached the planner via
    # history (not silently dropped), and the distinct message -- not the
    # generic "later milestone" tier_tool_forbidden text -- is what it saw.
    reissue_observation = result.state.history[-1].observation
    assert reissue_observation["error_type"] == "approval_consumed"
    assert "이미 한 번 실행" in reissue_observation["message"]

    # The first (legitimate) dispatch still fully landed and postwrite-
    # verified: version advanced, and the volume is actually persisted.
    assert artifact_manager.version == 1

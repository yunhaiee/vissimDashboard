"""Tests for M1.3's orchestrator + planner: the read-only agent loop.

Scripted conversations use ``FakeStructuredProvider`` — a canned-queue
stand-in for the providers' ``generate_structured`` seam — so every test is
deterministic and makes NO paid API calls. Tool dispatches hit the REAL demo
network (``data/practice.inpx`` via ``config.SIMULATION_INPX_PATH``),
matching the M1.3 acceptance criteria in docs/phase2/02-ROADMAP.md: answers
must be grounded in actual tool envelopes, the full trace JSONL must be
written, and no write/run tool may be reachable (tier gate).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

import pytest

import services.agent.tools_network as tools_network
from services.agent.orchestrator import run_agent_turn
from services.agent.toolkit import ToolRegistry, tool
from services.agent.trace import read_trace

EXPECTED_READ_TOOLS = {
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
    "get_run_kpis",
    "compare_scenarios",
}

# M2.3: tools_edit.py's write tools are now offered to the planner too (see
# planner.py's _OFFERED_TIERS docstring) — write safety moved entirely to
# the dispatch-side WriteGate + guardrail engine, not to withholding the
# tool definition from the model. v2 unit (2) adds set_reduced_speed_area
# (XML-injection, not COM — see tools_edit.py's own docstring), the sixth.
# v2 unit (4) adds set_conflict_area_status, the seventh. v2 unit (6) adds
# set_route_relative_flow, the eighth.
EXPECTED_WRITE_TOOLS = {
    "set_vehicle_input_volume",
    "set_signal_cycle",
    "set_signal_offset",
    "set_sim_duration",
    "set_random_seed",
    "set_reduced_speed_area",
    "set_conflict_area_status",
    "set_route_relative_flow",
    "set_vehicle_composition_share",
    # v2a unit ⑧
    "set_des_speed_decision",
    # v2a unit ⑨
    "set_signal_green_split",
    # QUEUE 3 unit 2
    "set_priority_rule_gap_headway",
}

# M2.4: the run tool is offered too — same rationale as the write tools
# above (the dispatch-side gate + postrun detectors are the safety layer,
# not withholding the definition), and render_contract() already narrated
# it, so offering it also closes a prompt/capability mismatch. v2 unit (3)
# adds load_check (tier="run" -- costs a real Vissim instance, same as
# run_simulation, even though it never mutates the network; see
# tools_check.py's module docstring for why no COM network-consistency
# check exists to wrap instead).
EXPECTED_RUN_TOOLS = {"run_simulation", "load_check"}


@pytest.fixture(autouse=True)
def _reset_active_inpx():
    """Every test runs against the real demo default (data/practice.inpx)
    and restores it after, so no override can leak between tests."""
    tools_network.set_active_inpx(None)
    yield
    tools_network.set_active_inpx(None)


class FakeStructuredProvider:
    """Canned (action, meta) queue implementing the ``generate_structured`` seam.

    Records every call's (messages, tools, schema, system) so tests can
    assert what the planner actually offered and showed the provider. With
    ``repeat_last=True`` the final queue entry repeats forever — for the
    "provider never recovers" and "K-cap" scenarios.
    """

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
    """A canned clean-path (action, meta) pair, summary included per contract."""
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "fake", "fallback_rung": 0}


# ---------------------------------------------------------------------------
# Scenario 1 — "이 네트워크에 뭐가 있어?" → overview via tools → grounded answer
# ---------------------------------------------------------------------------


def test_overview_conversation_answers_from_tools_and_writes_trace(tmp_path):
    provider = FakeStructuredProvider(
        [
            _action("get_network_overview", summary="네트워크 개요를 확인합니다"),
            _action(
                "respond",
                summary="확인한 내용을 정리했습니다",
                answer="이 네트워크에는 링크 16개, 노드 2개, 차량 입력 6개, 신호기 0개가 있습니다.",
            ),
        ]
    )

    result = run_agent_turn(
        "이 네트워크에 뭐가 있어?",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-overview",
        language="ko",
    )

    assert result.kind == "answer"
    assert "링크 16개" in result.answer

    # The answer's facts are grounded in the ACTUAL envelope from demo data:
    # the dispatched overview really counted 16 links (14 real + 2 Phase 3
    # connectors — see data/practice.inpx's CONNECTORS in make_demo_data.py).
    overview = result.state.history[0].observation
    assert overview["status"] == "ok"
    assert overview["data"]["links"]["count"] == 16

    # Steps narrated, in the user's language, in order.
    assert [step["tool"] for step in result.steps] == ["get_network_overview", "respond"]
    assert result.steps[0] == {
        "summary": "네트워크 개요를 확인합니다",
        "tool": "get_network_overview",
        "status": "ok",
    }

    # The planner offered exactly the read tools + M2.3's write tools + the
    # two terminals, every def carrying the required `summary` param (write
    # tools are offered but stay unreachable through this default,
    # gate-less call — see test_default_write_gate_refuses_dispatch_even_
    # though_the_write_tool_is_offered below).
    offered = provider.calls[0]["tools"]
    assert {tool_def["name"] for tool_def in offered} == (
        EXPECTED_READ_TOOLS | EXPECTED_WRITE_TOOLS | EXPECTED_RUN_TOOLS | {"respond", "ask_user"}
    )
    for tool_def in offered:
        assert "summary" in tool_def["input_schema"]["properties"]
        assert "summary" in tool_def["input_schema"]["required"]

    # The system prompt reached the provider with the contract injected.
    system = provider.calls[0]["system"]
    assert "{TOOL_CONTRACT}" not in system
    assert "## get_network_overview" in system
    assert "## respond" in system

    # The second planner call saw the first envelope via the state block.
    assert "get_network_overview" in provider.calls[1]["messages"][0]["content"]

    # Trace: plan / action_start / action_end / plan, monotonic ts, and
    # read_trace round-trips the JSONL cleanly.
    records = read_trace(result.trace_path)
    assert [record["kind"] for record in records] == [
        "plan",
        "action_start",
        "action_end",
        "plan",
    ]
    timestamps = [record["ts"] for record in records]
    assert timestamps == sorted(timestamps)
    assert all(record["session_id"] == "s-overview" for record in records)


# ---------------------------------------------------------------------------
# Scenario 2 — "107번 입력 교통량 알려줘" → resolve + read → answer
# ---------------------------------------------------------------------------


def test_resolve_then_read_conversation_carries_both_envelopes(tmp_path):
    provider = FakeStructuredProvider(
        [
            _action("resolve_object", summary="107번 객체를 찾습니다", query="107"),
            _action("get_vehicle_inputs", summary="차량 입력 목록을 읽습니다"),
            _action(
                "respond",
                summary="교통량 정보를 정리했습니다",
                answer=(
                    "107번 객체는 네트워크에 없습니다. 차량 입력은 1~6번 6개이고, "
                    "데모 데이터에는 교통량 값이 들어 있지 않습니다."
                ),
            ),
        ]
    )

    result = run_agent_turn(
        "107번 입력 교통량 알려줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-resolve",
        language="ko",
    )

    assert result.kind == "answer"
    assert result.answer

    # History carries BOTH envelopes, in dispatch order.
    assert [record.action for record in result.state.history] == [
        "resolve_object",
        "get_vehicle_inputs",
    ]
    resolve_env, inputs_env = (record.observation for record in result.state.history)
    assert resolve_env["status"] == "ok"
    # Real demo behavior: no object "107" exists — a structured miss with
    # difflib candidates, proving the dispatch ran against real data.
    assert resolve_env["data"]["found"] is False
    assert resolve_env["data"]["error_type"] == "object_not_found"
    assert isinstance(resolve_env["data"]["candidates"], list)
    assert inputs_env["status"] == "ok"
    assert inputs_env["data"]["count"] == 6


# ---------------------------------------------------------------------------
# Scenario 3 — nonsense request → clarification, not hallucination
# ---------------------------------------------------------------------------


def test_nonsense_request_yields_clarification_after_one_iteration(tmp_path):
    provider = FakeStructuredProvider(
        [
            _action(
                "ask_user",
                summary="요청의 의도를 확인합니다",
                question="어떤 네트워크 객체나 지표를 확인하고 싶으신가요? 예: '107번 입력 교통량'",
            ),
        ]
    )

    result = run_agent_turn(
        "바나나 우주선을 발사해줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-nonsense",
        language="ko",
    )

    assert result.kind == "clarification"
    assert "어떤 네트워크 객체" in result.answer  # the question IS the surfaced answer
    assert len(provider.calls) == 1  # loop ended after exactly one iteration
    assert result.state.history == []  # no tool was dispatched
    assert [step["tool"] for step in result.steps] == ["ask_user"]

    records = read_trace(result.trace_path)
    assert [record["kind"] for record in records] == ["plan"]


# ---------------------------------------------------------------------------
# Scenario 4 — tier gate: the default gate refuses dispatch even though
# M2.3 now OFFERS write tools to the planner (pre-M2.3 this test was named
# "...offers_only_read_tools" and asserted write tools were never even
# offered; M2.3 deliberately moved that safety net from "never offer" to
# "offer, but refuse at dispatch time unless a permissive gate says
# otherwise" — see planner.py's _OFFERED_TIERS docstring and
# orchestrator.py's RefuseAllWriteGate. What still matters, and is still
# pinned here, is that with NO gate supplied (the default), a write tool
# the planner picks is refused before ever dispatching.
# ---------------------------------------------------------------------------


def test_default_write_gate_refuses_dispatch_even_though_the_write_tool_is_offered(tmp_path):
    executed = {"write": False}
    registry = ToolRegistry()

    class PeekParams(BaseModel):
        model_config = ConfigDict(extra="forbid")

    class SetVolumeParams(BaseModel):
        model_config = ConfigDict(extra="forbid")

    @tool(name="peek_network", tier="read", provider="inspect", params=PeekParams, registry=registry)
    def _peek(params: PeekParams) -> dict[str, Any]:
        """Peek at the network."""
        return {"value": 1}

    @tool(
        name="set_vehicle_input_volume",
        tier="write",
        provider="edit",
        params=SetVolumeParams,
        registry=registry,
    )
    def _set_volume(params: SetVolumeParams) -> dict[str, Any]:
        """Set a vehicle input volume."""
        executed["write"] = True
        return {"message": "wrote"}

    provider = FakeStructuredProvider(
        [
            _action("set_vehicle_input_volume", summary="교통량을 수정합니다"),
            _action(
                "respond",
                summary="수정이 불가함을 안내합니다",
                answer="지금 단계에서는 읽기 작업만 가능해서 교통량을 수정할 수 없습니다.",
            ),
        ]
    )

    result = run_agent_turn(
        "입력 교통량을 600으로 바꿔줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-tier",
        language="ko",
        registry=registry,
    )

    # The write tool's function was NEVER executed.
    assert executed["write"] is False

    # A structured refusal was traced, and nothing was dispatched.
    records = read_trace(result.trace_path)
    refusals = [record for record in records if record["kind"] == "refusal"]
    assert len(refusals) == 1
    assert refusals[0]["payload"]["error_type"] == "tool_tier_forbidden"
    assert not any(record["kind"] == "action_start" for record in records)

    # The refusal envelope entered history, so the planner's SECOND canned
    # decision was still consumed and the turn ended with a real answer.
    assert result.state.history[0].observation["error_type"] == "tool_tier_forbidden"
    assert result.kind == "answer"
    assert "수정할 수 없습니다" in result.answer

    # M2.3: native tool defs offered to the provider now include the
    # write-tier tool too (offered, but still refused at dispatch time by
    # the default RefuseAllWriteGate — proven above).
    offered = {tool_def["name"] for tool_def in provider.calls[0]["tools"]}
    assert offered == {"peek_network", "set_vehicle_input_volume", "respond", "ask_user"}


# ---------------------------------------------------------------------------
# Scenario 5 — rule-based fallback ladder: the loop can never wedge
# ---------------------------------------------------------------------------


def test_rule_fallback_ladder_never_wedges(tmp_path):
    give_up = (None, {"mode": "fake", "fallback_rung": 2, "error": "provider gave up"})
    provider = FakeStructuredProvider([give_up], repeat_last=True)

    result = run_agent_turn(
        "음... 그거 있잖아",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-fallback",
        language="ko",
    )

    # First fallback grounds itself with the safe overview read; the second
    # asks the user — the turn ends cleanly instead of wedging.
    assert result.kind == "clarification"
    assert result.answer  # a real clarification question, not empty text
    assert [step["tool"] for step in result.steps] == ["get_network_overview", "ask_user"]
    assert result.state.history[0].action == "get_network_overview"
    assert result.state.history[0].observation["status"] == "ok"

    records = read_trace(result.trace_path)
    fallback_records = [record for record in records if record["kind"] == "fallback_rung"]
    assert len(fallback_records) == 2
    for record in fallback_records:
        assert record["payload"]["provider_meta"]["fallback_rung"] == 2


# ---------------------------------------------------------------------------
# Scenario 6 — K=6 iteration cap: forced deterministic completion
# ---------------------------------------------------------------------------


def test_iteration_cap_forces_deterministic_completion(tmp_path):
    provider = FakeStructuredProvider(
        [_action("get_network_overview", summary="개요를 확인합니다")], repeat_last=True
    )

    result = run_agent_turn(
        "이 네트워크에 뭐가 있어?",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-cap",
        language="ko",
    )

    # Exactly 6 planner calls — never a 7th, no exception.
    assert len(provider.calls) == 6
    records = read_trace(result.trace_path)
    # FIX 2 (docs/phase2/DECISIONS.md, live-demo bug — duplicate-call guard):
    # the planner keeps re-proposing the IDENTICAL get_network_overview call
    # (no params to vary), so only the FIRST one actually dispatches; the
    # other 5 are refused as duplicate_call BEFORE toolkit.dispatch runs,
    # instead of the pre-fix behavior of re-running the same read tool 6
    # times pointlessly (the exact live-demo symptom this guard exists to
    # stop). state.history still gets one entry per iteration either way.
    assert sum(1 for record in records if record["kind"] == "action_start") == 1
    duplicate_refusals = [
        record
        for record in records
        if record["kind"] == "refusal" and record["payload"].get("error_type") == "duplicate_call"
    ]
    assert len(duplicate_refusals) == 5
    assert all(record["payload"].get("phase") == "dispatch" for record in duplicate_refusals)
    assert len(result.state.history) == 6

    # Forced completion: an apologetic deterministic answer listing what was
    # gathered (assembled without any LLM call).
    assert result.kind == "answer"
    assert result.answer
    assert "get_network_overview" in result.answer


# ---------------------------------------------------------------------------
# Scenario 7 — summary is defensively truncated to 80 chars in the planner
# ---------------------------------------------------------------------------


def test_planner_truncates_overlong_summary_to_80_chars(tmp_path):
    long_summary = "요약" * 100  # 200 chars, far past the 80-char contract
    provider = FakeStructuredProvider(
        [
            _action("get_network_overview", summary=long_summary),
            _action("respond", summary="정리했습니다", answer="확인을 마쳤습니다."),
        ]
    )

    result = run_agent_turn(
        "이 네트워크에 뭐가 있어?",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-summary",
        language="ko",
    )

    assert len(result.steps[0]["summary"]) == 80
    assert result.steps[0]["summary"] == long_summary[:80]

    plan_records = [
        record for record in read_trace(result.trace_path) if record["kind"] == "plan"
    ]
    assert len(plan_records[0]["payload"]["summary"]) == 80


# ---------------------------------------------------------------------------
# Scenario 8 — on_step: the M1.4 streaming hook
# ---------------------------------------------------------------------------


def test_on_step_fires_per_step_with_correct_payloads(tmp_path):
    provider = FakeStructuredProvider(
        [
            _action("get_network_overview", summary="네트워크 개요를 확인합니다"),
            _action("respond", summary="정리했습니다", answer="확인을 마쳤습니다."),
        ]
    )
    observed: list[dict[str, Any]] = []

    result = run_agent_turn(
        "이 네트워크에 뭐가 있어?",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-on-step",
        language="ko",
        on_step=observed.append,
    )

    # Fired once per narrated step, in order, carrying the same
    # summary/tool/status as result.steps plus a 0-based index.
    assert [
        {"summary": step["summary"], "tool": step["tool"], "status": step["status"]}
        for step in observed
    ] == result.steps
    assert [step["index"] for step in observed] == [0, 1]


def test_on_step_raising_does_not_break_the_turn(tmp_path):
    provider = FakeStructuredProvider(
        [
            _action("get_network_overview", summary="네트워크 개요를 확인합니다"),
            _action("respond", summary="정리했습니다", answer="확인을 마쳤습니다."),
        ]
    )

    def _bad_on_step(step: dict[str, Any]) -> None:
        raise RuntimeError("boom — a broken UI adapter must not wedge the agent")

    result = run_agent_turn(
        "이 네트워크에 뭐가 있어?",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-on-step-raise",
        language="ko",
        on_step=_bad_on_step,
    )

    assert result.kind == "answer"
    assert [step["tool"] for step in result.steps] == ["get_network_overview", "respond"]
    assert "확인을 마쳤습니다" in result.answer


# ---------------------------------------------------------------------------
# Scenario 9 — an empty terminal payload is never surfaced as a real answer
# (pre-merge review fix: a blank respond/ask_user used to reach the UI as an
# empty bubble; the planner now routes it through the same rule-fallback
# ladder as a provider failure).
# ---------------------------------------------------------------------------


def test_respond_with_empty_answer_falls_back_to_grounding_tool_on_empty_history(tmp_path):
    provider = FakeStructuredProvider(
        [
            _action("respond", summary="답변합니다", answer="   "),  # whitespace-only
            _action("respond", summary="정리했습니다", answer="실제 답변입니다."),
        ]
    )

    result = run_agent_turn(
        "이 네트워크에 뭐가 있어?",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-empty-respond-a",
        language="ko",
    )

    # The empty-answer decision never became a terminal "answer" — the
    # fallback ladder dispatched the safe grounding read instead.
    assert [step["tool"] for step in result.steps] == ["get_network_overview", "respond"]
    assert result.kind == "answer"
    assert result.answer == "실제 답변입니다."

    fallback_records = [
        record for record in read_trace(result.trace_path) if record["kind"] == "fallback_rung"
    ]
    assert len(fallback_records) == 1
    assert fallback_records[0]["payload"]["rung"] == 3


def test_respond_with_empty_answer_falls_back_to_ask_user_on_nonempty_history(tmp_path):
    provider = FakeStructuredProvider(
        [
            _action("get_network_overview", summary="개요를 확인합니다"),
            _action("respond", summary="정리했습니다", answer=""),  # empty string
        ]
    )

    result = run_agent_turn(
        "이 네트워크에 뭐가 있어?",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-empty-respond-b",
        language="ko",
    )

    # Never a blank "answer" bubble: with prior history the ladder's second
    # rung is ask_user, so the turn ends as a clarification instead.
    assert result.kind == "clarification"
    assert result.answer.strip()
    assert [step["tool"] for step in result.steps] == ["get_network_overview", "ask_user"]


def test_ask_user_with_empty_question_falls_back_to_grounding_tool_on_empty_history(tmp_path):
    provider = FakeStructuredProvider(
        [
            _action("ask_user", summary="질문합니다", question="   "),  # whitespace-only
            _action("respond", summary="정리했습니다", answer="완료된 답변입니다."),
        ]
    )

    result = run_agent_turn(
        "이 네트워크에 뭐가 있어?",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-empty-ask-a",
        language="ko",
    )

    assert [step["tool"] for step in result.steps] == ["get_network_overview", "respond"]
    assert result.kind == "answer"
    assert result.answer == "완료된 답변입니다."


def test_ask_user_with_empty_question_falls_back_to_ask_user_on_nonempty_history(tmp_path):
    provider = FakeStructuredProvider(
        [
            _action("get_network_overview", summary="개요를 확인합니다"),
            _action("ask_user", summary="질문합니다", question=""),  # empty string
        ]
    )

    result = run_agent_turn(
        "이 네트워크에 뭐가 있어?",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-empty-ask-b",
        language="ko",
    )

    # Still never an empty terminal payload — the rule fallback's own
    # deterministic clarification question is used instead of the blank one.
    assert result.kind == "clarification"
    assert result.answer.strip()
    assert [step["tool"] for step in result.steps] == ["get_network_overview", "ask_user"]

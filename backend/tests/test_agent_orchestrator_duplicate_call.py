"""Tests for FIX 2 (docs/phase2/DECISIONS.md, live-demo bug): the READ-tier
duplicate-call guard in orchestrator.py's ``run_agent_turn`` loop.

**The observed live-demo symptom.** A user asked for the full vehicle-input
list; the model's ANSWER claimed the tool result was truncated (planner.py's
rendering bug, fixed separately -- see test_agent_planner_rendering.py) and,
believing more data might appear on a retry, the planner re-called the EXACT
SAME tool with the EXACT SAME params six times straight, burning the entire
K=6 iteration cap on calls that could only ever reproduce the identical
observation already sitting in history.

**The fix.** A read tool's result is a pure function of the active network +
its own params -- the network cannot change mid-turn -- so a SECOND dispatch
of the identical (tool, canonicalized params) pair this turn is refused
BEFORE ``toolkit.dispatch`` runs, via a structured ``duplicate_call``
envelope that tells the planner the result is already in history and to
either answer from it or narrow the query. Gated tiers (write/run) are
DELIBERATELY EXEMPT -- see
test_agent_orchestrator_approval.py::test_preapproved_gate_refuses_a_same_turn_reissue_of_an_already_consumed_action,
which already proves the write-tier "same action re-issued after an approval
pause" scenario resolves via the SEPARATE `approval_consumed` mechanism, not
this one, and stays green with this guard in place.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from services.agent.orchestrator import run_agent_turn
from services.agent.toolkit import ToolRegistry, tool
from services.agent.trace import read_trace


class FakeStructuredProvider:
    """Canned (action, meta) queue implementing the ``generate_structured``
    seam -- see test_agent_orchestrator.py's identical fixture."""

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
        self.calls.append({"messages": messages, "tools": tools, "schema": schema, "system": system})
        if not self._queue:
            raise AssertionError("FakeStructuredProvider queue exhausted unexpectedly")
        if self._repeat_last and len(self._queue) == 1:
            return self._queue[0]
        return self._queue.pop(0)


def _action(tool_name: str, *, summary: str = "확인합니다", **params: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "fake", "fallback_rung": 0}


def _register_counting_read_tool(registry: ToolRegistry, name: str = "get_vehicle_inputs") -> dict[str, int]:
    """A read-tier tool that counts REAL dispatches (the fake's own body
    running), so "exactly ONE real dispatch" is asserted directly against
    execution, not just against trace records."""
    calls = {"count": 0}

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")

        input_no: str = "1"

    @tool(name=name, tier="read", provider="inspect", params=Params, registry=registry)
    def _get(params: Params) -> dict[str, Any]:
        """Synthetic counting read tool (test fixture)."""
        calls["count"] += 1
        return {"count": 12, "vehicle_inputs": [{"no": params.input_no, "volume": 1000.0}]}

    return calls


# ---------------------------------------------------------------------------
# Core scenario: provider repeats the SAME call 3x -- first dispatches,
# second/third get duplicate_call errors, turn concludes via respond.
# ---------------------------------------------------------------------------


def test_repeated_identical_read_call_dispatches_once_then_refuses_the_repeats(tmp_path):
    registry = ToolRegistry()
    calls = _register_counting_read_tool(registry)

    provider = FakeStructuredProvider(
        [
            _action("get_vehicle_inputs", summary="입력을 확인합니다", input_no="1"),
            _action("get_vehicle_inputs", summary="다시 확인합니다", input_no="1"),
            _action("get_vehicle_inputs", summary="한 번 더 확인합니다", input_no="1"),
            _action("respond", summary="정리했습니다", answer="1번 입력의 교통량은 1000입니다."),
        ]
    )

    result = run_agent_turn(
        "1번 입력 교통량을 알려줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-dup-basic",
        language="ko",
        registry=registry,
    )

    # Exactly ONE real dispatch -- the tool function itself only ran once.
    assert calls["count"] == 1
    assert result.kind == "answer"
    assert len(provider.calls) == 4  # the loop continued normally, no wedge

    records = read_trace(result.trace_path)
    assert sum(1 for r in records if r["kind"] == "action_start") == 1
    assert sum(1 for r in records if r["kind"] == "action_end") == 1

    duplicate_refusals = [
        r for r in records if r["kind"] == "refusal" and r["payload"].get("error_type") == "duplicate_call"
    ]
    assert len(duplicate_refusals) == 2
    for refusal in duplicate_refusals:
        assert refusal["payload"]["phase"] == "dispatch"
        assert refusal["payload"]["tool"] == "get_vehicle_inputs"
        assert "get_vehicle_inputs" in refusal["payload"]["message"]

    # state.history: real result, then two duplicate_call refusals, in order.
    assert len(result.state.history) == 3
    assert result.state.history[0].observation["status"] == "ok"
    assert result.state.history[1].observation["error_type"] == "duplicate_call"
    assert result.state.history[2].observation["error_type"] == "duplicate_call"

    # Steps narrated for the UI: all three show up (with the last two's
    # status reflecting the refusal), never silently dropped.
    assert [step["tool"] for step in result.steps] == [
        "get_vehicle_inputs",
        "get_vehicle_inputs",
        "get_vehicle_inputs",
        "respond",
    ]
    assert result.steps[0]["status"] == "ok"
    assert result.steps[1]["status"] == "error"
    assert result.steps[2]["status"] == "error"


# ---------------------------------------------------------------------------
# A DIFFERENT tool, or the SAME tool with DIFFERENT (canonicalized) params,
# is never treated as a duplicate.
# ---------------------------------------------------------------------------


def test_different_params_are_not_treated_as_a_duplicate_call(tmp_path):
    registry = ToolRegistry()
    calls = _register_counting_read_tool(registry)

    provider = FakeStructuredProvider(
        [
            _action("get_vehicle_inputs", summary="1번 확인", input_no="1"),
            _action("get_vehicle_inputs", summary="2번 확인", input_no="2"),
            _action("respond", summary="정리", answer="확인했습니다."),
        ]
    )

    result = run_agent_turn(
        "1번, 2번 입력을 알려줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-dup-different-params",
        language="ko",
        registry=registry,
    )

    assert calls["count"] == 2  # both genuinely dispatched
    records = read_trace(result.trace_path)
    assert sum(1 for r in records if r["kind"] == "action_start") == 2
    assert not any(r["kind"] == "refusal" and r["payload"].get("error_type") == "duplicate_call" for r in records)


def test_two_different_tools_are_not_treated_as_a_duplicate_call(tmp_path):
    registry = ToolRegistry()
    calls_a = _register_counting_read_tool(registry, name="get_vehicle_inputs")
    calls_b = _register_counting_read_tool(registry, name="get_signal_controllers")

    provider = FakeStructuredProvider(
        [
            _action("get_vehicle_inputs", summary="입력 확인", input_no="1"),
            _action("get_signal_controllers", summary="신호기 확인", input_no="1"),
            _action("respond", summary="정리", answer="확인했습니다."),
        ]
    )

    result = run_agent_turn(
        "입력이랑 신호기 알려줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-dup-different-tools",
        language="ko",
        registry=registry,
    )

    assert calls_a["count"] == 1
    assert calls_b["count"] == 1
    records = read_trace(result.trace_path)
    assert not any(r["kind"] == "refusal" and r["payload"].get("error_type") == "duplicate_call" for r in records)


# ---------------------------------------------------------------------------
# Repeated across the iteration cap -- the loop never wedges/crashes even
# when the SAME duplicate is proposed forever (repeat_last).
# ---------------------------------------------------------------------------


def test_duplicate_call_never_wedges_the_iteration_cap(tmp_path):
    registry = ToolRegistry()
    calls = _register_counting_read_tool(registry)

    provider = FakeStructuredProvider(
        [_action("get_vehicle_inputs", summary="확인합니다", input_no="1")],
        repeat_last=True,
    )

    result = run_agent_turn(
        "1번 입력 교통량을 알려줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-dup-cap",
        language="ko",
        registry=registry,
    )

    assert calls["count"] == 1  # still exactly one real dispatch across all 6 iterations
    assert len(provider.calls) == 6
    assert result.kind == "answer"  # forced K-cap completion, not a crash

    records = read_trace(result.trace_path)
    assert sum(1 for r in records if r["kind"] == "action_start") == 1
    duplicate_refusals = [
        r for r in records if r["kind"] == "refusal" and r["payload"].get("error_type") == "duplicate_call"
    ]
    assert len(duplicate_refusals) == 5

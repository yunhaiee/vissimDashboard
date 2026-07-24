"""Tests for M3.2's three-tier routing wired into orchestrator.py/planner.py:

- tier classification (services.agent.complexity) happens once per turn and
  is recorded on every "plan" trace record's ``meta.tier`` (docs/phase2/
  02-ROADMAP.md M3.2's accept criterion, verbatim);
- **simple** tier: a single verified call may end the turn immediately --
  nothing here diverts a first-decision `respond`;
- **complex**/**agentic** tiers: "mandatory preview" -- the planner may not
  end the turn with `respond` as its very first decision; the attempt is
  diverted into a grounding read instead (planner.py's
  ``_mandatory_preview_decision``), and the SAME scripted `respond` succeeds
  once real history exists;
- **agentic** tier's anti-exploit safety net: any accumulated postrun
  ``guardrail_warnings`` (e.g. ``vehicles_deleted_in_run``) is appended to
  the final answer verbatim, so a fired warning can never be silently absent
  from a claimed improvement -- the concrete, code-level form of the prompt
  rule "the agent may not exploit reported-delay improvements that coexist
  with fired constraint warnings".

Scripted with ``FakeStructuredProvider`` (same canned-queue fixture as
test_agent_orchestrator.py/test_agent_orchestrator_run_gate.py), so every
test is deterministic and makes no paid API calls. Write/run approval-pause
behavior itself is exercised elsewhere (test_agent_orchestrator_approval.py,
test_agent_orchestrator_run_gate.py) -- these tests use a permissive gate
where a write/run needs to land, since the point here is what happens AFTER
gating, not the gate itself.
"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

import services.agent.tools_network as tools_network
from services.agent.guardrails import GuardrailEngine
from services.agent.orchestrator import run_agent_turn
from services.agent.toolkit import ToolRegistry, tool
from services.agent.trace import read_trace


@pytest.fixture(autouse=True)
def _reset_active_inpx():
    """Tests that dispatch a real registry tool (get_network_overview) hit
    the real demo default (data/practice.inpx) -- same fixture as
    test_agent_orchestrator.py, restored after so no override leaks."""
    tools_network.set_active_inpx(None)
    yield
    tools_network.set_active_inpx(None)


class FakeStructuredProvider:
    """Canned (action, meta) queue implementing the ``generate_structured``
    seam. Copied from test_agent_orchestrator.py's fixture of the same name
    (no shared test-utilities module exists in this repo)."""

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


class _AllowAllGate:
    """Test-only permissive WriteGate: authorizes every gated-tier action
    (mirrors test_agent_orchestrator_run_gate.py's fixture of the same
    name) -- these tests are about post-gate behavior, not the gate itself."""

    def authorize(self, action: dict[str, Any], spec: Any) -> bool:
        return True


# ---------------------------------------------------------------------------
# Scripted request 1/3 -- simple tier: a bare `respond` may end the turn as
# the very FIRST decision (no mandatory-preview diversion applies).
# ---------------------------------------------------------------------------


def test_simple_tier_allows_respond_as_the_first_decision(tmp_path):
    provider = FakeStructuredProvider(
        [
            _action(
                "respond",
                summary="바로 답변합니다",
                answer="차량 입력은 1~6번 6개입니다.",
            ),
        ]
    )

    result = run_agent_turn(
        "이 네트워크에 뭐가 있어?",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-tier-simple",
        language="ko",
    )

    assert result.kind == "answer"
    assert result.answer == "차량 입력은 1~6번 6개입니다."
    assert len(provider.calls) == 1  # no forced diversion -- one planner call, done
    assert [step["tool"] for step in result.steps] == ["respond"]

    records = read_trace(result.trace_path)
    plan_records = [r for r in records if r["kind"] == "plan"]
    assert len(plan_records) == 1
    assert plan_records[0]["payload"]["meta"]["tier"] == "simple"
    assert "mandatory_preview_forced" not in plan_records[0]["payload"]["meta"]


# ---------------------------------------------------------------------------
# Scripted request 2/3 -- complex tier: the SAME shape of premature `respond`
# is diverted into a grounding read first; the tier is recorded on both plan
# records; the second (now-legitimate) `respond` succeeds.
# ---------------------------------------------------------------------------


def test_complex_tier_diverts_a_first_step_respond_into_a_grounding_read(tmp_path):
    provider = FakeStructuredProvider(
        [
            _action(
                "respond",
                summary="바로 답변합니다",
                answer="이 답변은 사용되지 않아야 합니다",
            ),
            _action(
                "respond",
                summary="정리했습니다",
                answer="두 시나리오를 비교했습니다: 링크 14개, 노드 2개.",
            ),
        ]
    )

    result = run_agent_turn(
        "시나리오를 비교해서 지연 차이를 설명해줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-tier-complex",
        language="ko",
    )

    assert result.kind == "answer"
    assert result.answer == "두 시나리오를 비교했습니다: 링크 14개, 노드 2개."
    # Only the SECOND respond (the one with real history behind it) ever
    # became the turn's answer -- the premature first one was discarded.
    assert "사용되지 않아야" not in result.answer
    assert len(provider.calls) == 2  # both queued decisions were consumed
    assert [step["tool"] for step in result.steps] == ["get_network_overview", "respond"]

    records = read_trace(result.trace_path)
    plan_records = [r for r in records if r["kind"] == "plan"]
    assert len(plan_records) == 2
    assert [r["payload"]["meta"]["tier"] for r in plan_records] == ["complex", "complex"]
    # The diversion is visible in the trace: iteration 0's "plan" record
    # actually dispatched get_network_overview, tagged as forced.
    assert plan_records[0]["payload"]["tool"] == "get_network_overview"
    assert plan_records[0]["payload"]["meta"]["mandatory_preview_forced"] is True
    assert "mandatory_preview_forced" not in plan_records[1]["payload"]["meta"]


# ---------------------------------------------------------------------------
# Scripted request 3/3 -- agentic tier: same mandatory-preview diversion as
# complex (the strictest tier is at least as strict), proving the gating
# difference from simple is real and tier-driven, not message-specific.
# ---------------------------------------------------------------------------


def test_agentic_tier_diverts_a_first_step_respond_into_a_grounding_read(tmp_path):
    provider = FakeStructuredProvider(
        [
            _action(
                "respond",
                summary="바로 답변합니다",
                answer="이 답변은 사용되지 않아야 합니다",
            ),
            _action(
                "respond",
                summary="정리했습니다",
                answer="근거를 확인한 뒤 답변합니다.",
            ),
        ]
    )

    result = run_agent_turn(
        "노드 1 지연을 최소화해줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-tier-agentic",
        language="ko",
    )

    assert result.kind == "answer"
    assert result.answer == "근거를 확인한 뒤 답변합니다."
    assert len(provider.calls) == 2
    assert [step["tool"] for step in result.steps] == ["get_network_overview", "respond"]

    records = read_trace(result.trace_path)
    plan_records = [r for r in records if r["kind"] == "plan"]
    assert [r["payload"]["meta"]["tier"] for r in plan_records] == ["agentic", "agentic"]
    assert plan_records[0]["payload"]["meta"]["mandatory_preview_forced"] is True


# ---------------------------------------------------------------------------
# Extra accept criterion -- agentic anti-exploit: a rigged fake where the
# "best" (second) config triggers vehicles_deleted_in_run must never let the
# final answer present that improvement without the fired warning attached.
# ---------------------------------------------------------------------------


def _register_dual_config_run_tool(registry: ToolRegistry) -> dict[str, int]:
    """A rigged ``run_simulation`` (real tool name, tier "run") that scripts
    exactly the M3.2 acceptance scenario: the FIRST call ("config A") lands
    clean; the SECOND call ("config B" -- the one the scripted provider goes
    on to claim is "best") fires ``vehicles_deleted_in_run`` via a nonzero
    ``deleted_vehicles`` count in its ``run_result`` -- the real Vissim
    silent-failure pattern this constraint exists to catch (demand vanished
    via DiffusTm deletions, so a shorter/lighter effective load can report a
    LOWER delay without the network actually having improved)."""
    calls = {"count": 0}

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")

        duration_s: int
        random_seeds: list[int] | None = None

    @tool(name="run_simulation", tier="run", provider="run", params=Params, registry=registry)
    def _run(params: Params) -> dict[str, Any]:
        """Run the network (fake/test double, two-config script)."""
        calls["count"] += 1
        # A second table beyond SIMULATIONRUN keeps results_missing_kpi_tables
        # from also firing here -- this scenario is isolating
        # vehicles_deleted_in_run specifically (see the module docstring).
        tables = [
            "SIMULATIONRUN",
            "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS",
        ]
        if calls["count"] == 1:
            run_result = {"tables": tables, "deleted_vehicles": 0}
        else:
            run_result = {"tables": tables, "deleted_vehicles": 7}
        return {
            "verification": {
                "job_id": f"fake-job-{calls['count']}",
                "duration_s": params.duration_s,
            },
            "run_result": run_result,
        }

    return calls


def test_agentic_tier_never_lets_a_fired_warning_go_missing_from_the_final_answer(tmp_path):
    registry = ToolRegistry()
    calls = _register_dual_config_run_tool(registry)

    # The scripted provider deliberately tries to exploit the warning: it
    # claims config B is the best result and recommends applying it, WITHOUT
    # ever mentioning that config B's run also deleted vehicles.
    exploit_claim = "설정 B가 지연을 가장 많이 줄였습니다. 설정 B 적용을 권장합니다."
    # random_seeds=[...] (>= the catalog's recommended minimum) keeps
    # single_seed_run_not_statistically_valid from also firing here -- this
    # scenario isolates vehicles_deleted_in_run specifically.
    five_seeds = [1, 2, 3, 4, 5]
    provider = FakeStructuredProvider(
        [
            _action(
                "run_simulation",
                summary="설정 A로 시뮬레이션합니다",
                duration_s=300,
                random_seeds=five_seeds,
            ),
            _action(
                "run_simulation",
                summary="설정 B로 시뮬레이션합니다",
                duration_s=600,
                random_seeds=five_seeds,
            ),
            _action("respond", summary="최적 설정을 안내합니다", answer=exploit_claim),
        ]
    )

    result = run_agent_turn(
        "노드 1 지연을 최소화해줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-agentic-exploit",
        language="ko",
        registry=registry,
        gate=_AllowAllGate(),
        engine=GuardrailEngine(),
    )

    assert calls["count"] == 2
    assert result.kind == "answer"

    records = read_trace(result.trace_path)
    plan_records = [r for r in records if r["kind"] == "plan"]
    assert all(r["payload"]["meta"]["tier"] == "agentic" for r in plan_records)

    # config B's postrun warning actually fired (the rigged trap worked).
    postrun_records = [r for r in records if r["kind"] == "postrun"]
    assert len(postrun_records) == 2
    assert postrun_records[0]["payload"]["findings"] == []
    assert postrun_records[1]["payload"]["findings"] == ["vehicles_deleted_in_run"]

    # THE ACCEPT CRITERION: the model's claim is still delivered (never
    # silently rewritten -- the user sees exactly what the agent concluded),
    # but the fired warning is ALWAYS attached too. The final plan can never
    # present the improvement in isolation from the warning that undermines
    # it -- the two can no longer be silently separated.
    assert exploit_claim in result.answer
    assert "삭제" in result.answer  # the ko vehicles_deleted_in_run message text
    assert "7" in result.answer  # the deleted-vehicle count rode along too
    assert result.answer.count("참고:") == 1  # the notice header, exactly once


def test_agentic_tier_answer_is_unchanged_when_no_warning_ever_fired(tmp_path):
    """Regression guard for the same safety net: with a clean run (no
    postrun findings at all), the final answer must be exactly what the
    model returned -- the notice is additive, never invented, never mutates
    a clean answer."""
    registry = ToolRegistry()
    calls = {"count": 0}

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")

        duration_s: int
        random_seeds: list[int] | None = None

    @tool(name="run_simulation", tier="run", provider="run", params=Params, registry=registry)
    def _run(params: Params) -> dict[str, Any]:
        """Run the network (fake/test double, always clean)."""
        calls["count"] += 1
        return {
            "verification": {"job_id": "fake-job", "duration_s": params.duration_s},
            "run_result": {
                "tables": [
                    "SIMULATIONRUN",
                    "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS",
                ],
                "deleted_vehicles": 0,
            },
        }

    clean_answer = "설정 A가 안전하게 지연을 줄였습니다."
    provider = FakeStructuredProvider(
        [
            _action(
                "run_simulation",
                summary="설정 A로 시뮬레이션합니다",
                duration_s=300,
                random_seeds=[1, 2, 3, 4, 5],
            ),
            _action("respond", summary="결과를 안내합니다", answer=clean_answer),
        ]
    )

    result = run_agent_turn(
        "노드 1 지연을 최소화해줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-agentic-clean",
        language="ko",
        registry=registry,
        gate=_AllowAllGate(),
        engine=GuardrailEngine(),
    )

    assert calls["count"] == 1
    assert result.kind == "answer"
    assert result.answer == clean_answer  # byte-identical -- no notice appended

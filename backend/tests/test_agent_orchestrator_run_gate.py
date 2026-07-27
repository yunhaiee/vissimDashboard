"""Tests for M2.4: run-tier decisions go through the EXACT SAME
ApprovalRequired/WriteGate/preflight machinery as write-tier
(docs/phase2/02-ROADMAP.md M2.4, docs/phase2/DECISIONS.md's M2.3
VissimEditSession entry: "M2.4 RUNS stay on the simulation job queue"),
diverging only in POST-DISPATCH handling: ``engine.postrun`` (not
``postwrite``), and no ``save_version``/``rollback_to`` at all — a run
cannot have moved the network. Mirrors
test_agent_orchestrator_write_gate.py's and
test_agent_orchestrator_approval.py's fixtures/patterns exactly, adapted to
a fake ``run_simulation``-shaped tool (real name, so constraints.py's
postrun catalog — which is tool-agnostic, ``applies_to=["*"]`` — applies
naturally).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from services.agent.gates import ProposeWriteGate
from services.agent.guardrails import GuardrailEngine
from services.agent.orchestrator import RefuseAllWriteGate, run_agent_turn
from services.agent.state import ArtifactManager
from services.agent.toolkit import ToolRegistry, tool
from services.agent.trace import read_trace

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"


class FakeStructuredProvider:
    """Canned (action, meta) queue — same fixture as the write-gate/approval
    test files (no shared test-utilities module exists in this repo)."""

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


class _AllowAllGate:
    """Test-only permissive WriteGate: authorizes every gated-tier action."""

    def authorize(self, action: dict[str, Any], spec: Any) -> bool:
        return True


def _register_rigged_run_tool(
    registry: ToolRegistry, *, run_result: dict[str, Any] | None
) -> dict[str, int]:
    """Registers a fake ``run_simulation`` (the real name, tier "run") that
    returns a canned ``run_result`` inside its envelope's ``data`` key —
    exactly the shape orchestrator.py extracts for ``engine.postrun``
    (mirrors tools_run.py's real return shape)."""
    calls = {"count": 0}

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")

        duration_s: int
        # Mirrors tools_run.py's real RunSimulationParams shape (optional,
        # default None) -- M3.1 added constraints.py's
        # single_seed_run_not_statistically_valid, which reads this field
        # from the action's own params at preflight; tests that need to
        # isolate OTHER postrun/gating behavior pass 5+ seeds so that new,
        # unrelated (but correct) warning doesn't also fire.
        random_seeds: list[int] | None = None

    @tool(name="run_simulation", tier="run", provider="run", params=Params, registry=registry)
    def _run(params: Params) -> dict[str, Any]:
        """Run the network (fake/test double)."""
        calls["count"] += 1
        result: dict[str, Any] = {
            "verification": {"job_id": "fake-job", "duration_s": params.duration_s},
        }
        if run_result is not None:
            result["run_result"] = run_result
        return result

    return calls


# ---------------------------------------------------------------------------
# (a) ProposeWriteGate pauses for run-tier exactly like write-tier
# ---------------------------------------------------------------------------


def test_propose_gate_pauses_for_run_tier_with_approval_request(tmp_path: Path):
    registry = ToolRegistry()
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()
    calls = _register_rigged_run_tool(registry, run_result={"tables": ["SIMULATIONRUN"], "deleted_vehicles": 0})

    # 5 seeds: enough to stay clear of constraints.py's (M3.1)
    # single_seed_run_not_statistically_valid, which is not what this test
    # is isolating (it's testing the ApprovalRequired pause mechanics).
    provider = FakeStructuredProvider(
        [
            _action(
                "run_simulation",
                summary="5분 시뮬레이션 실행 제안",
                duration_s=300,
                random_seeds=[1, 2, 3, 4, 5],
            )
        ]
    )

    result = run_agent_turn(
        "5분 시뮬레이션 돌려줘",
        provider=provider,
        work_dir=work_dir,
        session_id="s-run-approval",
        language="ko",
        registry=registry,
        gate=ProposeWriteGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    assert result.kind == "approval_request"
    assert calls["count"] == 0  # dispatch never happened
    assert artifact_manager.version == 0  # runs never save_version either

    assert result.pending_approval == {
        "tool": "run_simulation",
        "params": {"duration_s": 300, "random_seeds": [1, 2, 3, 4, 5]},
        "summary": "5분 시뮬레이션 실행 제안",
        "warnings": [],
    }
    assert "run_simulation" in result.answer

    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records] == ["plan", "preflight", "user_confirm"]
    user_confirm_payload = records[2]["payload"]
    assert user_confirm_payload["tool"] == "run_simulation"
    assert user_confirm_payload["params"] == {"duration_s": 300, "random_seeds": [1, 2, 3, 4, 5]}


def test_propose_gate_pauses_for_run_tier_without_artifact_manager(tmp_path: Path):
    """Unlike write, a run-tier ApprovalRequired pause never needed
    artifact_manager (it only supplies preflight's shadow model, and {} is
    fine) -- confirmed explicitly absent here."""
    registry = ToolRegistry()
    calls = _register_rigged_run_tool(registry, run_result=None)

    provider = FakeStructuredProvider(
        [_action("run_simulation", summary="시뮬레이션 실행 제안", duration_s=300)]
    )

    result = run_agent_turn(
        "시뮬레이션 돌려줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-run-approval-no-am",
        language="ko",
        registry=registry,
        gate=ProposeWriteGate(),
        engine=GuardrailEngine(),
        # artifact_manager intentionally omitted -- must not raise.
    )

    assert result.kind == "approval_request"
    assert calls["count"] == 0


# ---------------------------------------------------------------------------
# (b) allowed run lands cleanly -> postrun trace, no findings, no save/rollback
# ---------------------------------------------------------------------------


def test_allowed_run_dispatches_and_calls_postrun_cleanly(tmp_path: Path):
    registry = ToolRegistry()
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()
    calls = _register_rigged_run_tool(
        registry,
        run_result={
            "tables": ["SIMULATIONRUN", "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS"],
            "deleted_vehicles": 0,
        },
    )

    provider = FakeStructuredProvider(
        [
            _action(
                "run_simulation",
                summary="시뮬레이션을 실행합니다",
                duration_s=3600,
                random_seeds=[1, 2, 3, 4, 5],
            ),
            _action("respond", summary="실행을 완료했습니다", answer="시뮬레이션 실행을 완료했습니다."),
        ]
    )

    result = run_agent_turn(
        "1시간 시뮬레이션 돌려줘",
        provider=provider,
        work_dir=work_dir,
        session_id="s-run-ok",
        language="ko",
        registry=registry,
        gate=_AllowAllGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    assert result.kind == "answer"
    assert calls["count"] == 1

    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records] == [
        "plan",
        "preflight",
        "action_start",
        "action_end",
        "postrun",
        "plan",
    ]
    assert records[1]["payload"]["blocking"] is False  # preflight: no constraint applies_to run_simulation
    assert records[4]["payload"]["blocking"] is False  # postrun: clean run, no findings
    assert records[4]["payload"]["findings"] == []

    # A run must NEVER save_version()/rollback_to() -- it cannot have moved
    # the network.
    assert artifact_manager.version == 0


def test_allowed_run_never_requires_artifact_manager(tmp_path: Path):
    """Divergence from write's hard requirement (test_agent_orchestrator_
    write_gate.py's test_permissive_gate_without_artifact_manager_raises):
    a run-tier dispatch through an allow-all gate must NOT raise even when
    no artifact_manager was ever supplied."""
    registry = ToolRegistry()
    calls = _register_rigged_run_tool(
        registry, run_result={"tables": ["SIMULATIONRUN"], "deleted_vehicles": 0}
    )

    provider = FakeStructuredProvider(
        [
            _action("run_simulation", summary="시뮬레이션을 실행합니다", duration_s=300),
            _action("respond", summary="완료", answer="완료했습니다."),
        ]
    )

    result = run_agent_turn(
        "시뮬레이션 돌려줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-run-no-am",
        language="ko",
        registry=registry,
        gate=_AllowAllGate(),
        engine=GuardrailEngine(),
        # artifact_manager intentionally omitted -- must not raise.
    )

    assert calls["count"] == 1
    assert result.kind == "answer"


# ---------------------------------------------------------------------------
# (b.1) trajectory-replay step wiring: orchestrator's step-emission choke
# point reads trajectory_recorded/job_id off run_simulation's own returned
# data (never the LLM's summary text) -- see docs/phase2/DECISIONS.md's
# dated entry for the real-run-replay unit.
# ---------------------------------------------------------------------------


def test_run_simulation_step_carries_trajectory_recorded_and_job_id(tmp_path: Path):
    registry = ToolRegistry()
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()
    calls = _register_rigged_run_tool(
        registry,
        run_result={
            "tables": ["SIMULATIONRUN", "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS"],
            "deleted_vehicles": 0,
            "trajectory_recorded": True,
        },
    )

    provider = FakeStructuredProvider(
        [
            _action(
                "run_simulation",
                summary="시뮬레이션을 실행합니다",
                duration_s=3600,
                random_seeds=[1, 2, 3, 4, 5],
            ),
            _action("respond", summary="실행을 완료했습니다", answer="시뮬레이션 실행을 완료했습니다."),
        ]
    )

    result = run_agent_turn(
        "1시간 시뮬레이션 돌려줘",
        provider=provider,
        work_dir=work_dir,
        session_id="s-run-traj-step",
        language="ko",
        registry=registry,
        gate=_AllowAllGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    assert calls["count"] == 1
    run_step = next(step for step in result.steps if step["tool"] == "run_simulation")
    assert run_step["status"] == "ok"
    assert run_step["trajectory_recorded"] is True
    assert run_step["job_id"] == "fake-job"


def test_run_simulation_step_omits_trajectory_recorded_when_not_recorded(tmp_path: Path):
    """The default/degrade case (no trajectory_recorded in run_result at
    all, matching tools_run.py's real 'not recorded' shape) must still
    surface a deterministic False, never a truthy default."""
    registry = ToolRegistry()
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()
    calls = _register_rigged_run_tool(
        registry,
        run_result={"tables": ["SIMULATIONRUN"], "deleted_vehicles": 0},
    )

    provider = FakeStructuredProvider(
        [
            _action(
                "run_simulation",
                summary="시뮬레이션을 실행합니다",
                duration_s=3600,
                random_seeds=[1, 2, 3, 4, 5],
            ),
            _action("respond", summary="완료", answer="완료했습니다."),
        ]
    )

    result = run_agent_turn(
        "시뮬레이션 돌려줘",
        provider=provider,
        work_dir=work_dir,
        session_id="s-run-no-traj-step",
        language="ko",
        registry=registry,
        gate=_AllowAllGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    assert calls["count"] == 1
    run_step = next(step for step in result.steps if step["tool"] == "run_simulation")
    assert run_step["trajectory_recorded"] is False
    assert run_step["job_id"] == "fake-job"  # verification always carries job_id


# ---------------------------------------------------------------------------
# (c) postrun WARN findings attach to the observation (mirrors postwrite's
#     warning-attachment helper) — missing-KPI-tables and deleted-vehicles
# ---------------------------------------------------------------------------


def test_postrun_missing_kpi_tables_warning_attaches_to_observation(tmp_path: Path):
    registry = ToolRegistry()
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()
    calls = _register_rigged_run_tool(
        registry, run_result={"tables": ["SIMULATIONRUN"], "deleted_vehicles": 0}
    )

    provider = FakeStructuredProvider(
        [
            _action(
                "run_simulation",
                summary="5분 시뮬레이션을 실행합니다",
                duration_s=300,
                random_seeds=[1, 2, 3, 4, 5],
            ),
            _action("respond", summary="실행을 완료했습니다", answer="5분 실행을 완료했습니다."),
        ]
    )

    result = run_agent_turn(
        "5분 시뮬레이션 돌려줘",
        provider=provider,
        work_dir=work_dir,
        session_id="s-run-warn-kpi",
        language="ko",
        registry=registry,
        gate=_AllowAllGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    assert calls["count"] == 1
    assert result.kind == "answer"

    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records] == [
        "plan",
        "preflight",
        "action_start",
        "action_end",
        "postrun",
        "warning",
        "plan",
    ]
    assert records[4]["payload"]["findings"] == ["results_missing_kpi_tables"]
    assert records[5]["payload"]["phase"] == "postrun"
    assert records[5]["payload"]["findings"] == ["results_missing_kpi_tables"]

    # The warning rode along on the observation the planner saw next turn --
    # same mechanism (_attach_guardrail_warnings) as postwrite's.
    run_observation = result.state.history[0].observation
    assert run_observation["guardrail_warnings"][0]["constraint_id"] == "results_missing_kpi_tables"

    # Never rolled back or versioned -- a run cannot have moved the network.
    assert artifact_manager.version == 0


def test_postrun_vehicles_deleted_warning_attaches_to_observation(tmp_path: Path):
    registry = ToolRegistry()
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()
    calls = _register_rigged_run_tool(
        registry,
        run_result={
            "tables": ["SIMULATIONRUN", "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS"],
            "deleted_vehicles": 12,
        },
    )

    provider = FakeStructuredProvider(
        [
            _action(
                "run_simulation",
                summary="시뮬레이션을 실행합니다",
                duration_s=3600,
                random_seeds=[1, 2, 3, 4, 5],
            ),
            _action("respond", summary="완료", answer="실행을 완료했습니다."),
        ]
    )

    result = run_agent_turn(
        "시뮬레이션 돌려줘",
        provider=provider,
        work_dir=work_dir,
        session_id="s-run-warn-deleted",
        language="ko",
        registry=registry,
        gate=_AllowAllGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    records = read_trace(result.trace_path)
    postrun_record = next(r for r in records if r["kind"] == "postrun")
    assert postrun_record["payload"]["findings"] == ["vehicles_deleted_in_run"]

    run_observation = result.state.history[0].observation
    assert run_observation["guardrail_warnings"][0]["constraint_id"] == "vehicles_deleted_in_run"
    assert "12" in run_observation["guardrail_warnings"][0]["message"]


def test_postrun_none_run_result_produces_no_findings_and_no_crash(tmp_path: Path):
    """A run tool that (unlike tools_run.py's real one) omits run_result
    entirely must not crash the loop -- engine.postrun(action, None)
    degrades to zero findings (constraints.py: every postrun check returns
    None on a missing run_result)."""
    registry = ToolRegistry()
    calls = _register_rigged_run_tool(registry, run_result=None)

    provider = FakeStructuredProvider(
        [
            _action("run_simulation", summary="시뮬레이션을 실행합니다", duration_s=300),
            _action("respond", summary="완료", answer="완료했습니다."),
        ]
    )

    result = run_agent_turn(
        "시뮬레이션 돌려줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-run-no-result",
        language="ko",
        registry=registry,
        gate=_AllowAllGate(),
        engine=GuardrailEngine(),
    )

    assert calls["count"] == 1
    assert result.kind == "answer"
    records = read_trace(result.trace_path)
    postrun_record = next(r for r in records if r["kind"] == "postrun")
    assert postrun_record["payload"]["findings"] == []
    assert postrun_record["payload"]["blocking"] is False


# ---------------------------------------------------------------------------
# (d) default RefuseAllWriteGate -> run tool never dispatched (same as write)
# ---------------------------------------------------------------------------


def test_default_gate_refuses_run_tier_same_as_write_tier(tmp_path: Path):
    registry = ToolRegistry()
    calls = _register_rigged_run_tool(
        registry, run_result={"tables": ["SIMULATIONRUN"], "deleted_vehicles": 0}
    )

    provider = FakeStructuredProvider(
        [
            _action("run_simulation", summary="시뮬레이션을 실행합니다", duration_s=300),
            _action("respond", summary="실행 불가 안내", answer="지금은 실행할 수 없습니다."),
        ]
    )

    result = run_agent_turn(
        "시뮬레이션 돌려줘",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-run-default-gate",
        language="ko",
        registry=registry,
        # gate intentionally omitted -- must default to RefuseAllWriteGate.
    )

    assert calls["count"] == 0
    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records] == ["plan", "refusal", "plan"]
    assert records[1]["payload"]["error_type"] == "tool_tier_forbidden"
    assert result.kind == "answer"


def test_explicit_refuse_all_gate_refuses_run_tier():
    gate = RefuseAllWriteGate()

    assert gate.authorize({"tool": "run_simulation", "params": {}}, spec=None) is False

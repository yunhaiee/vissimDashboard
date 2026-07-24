"""Tests for M2.2: the injectable WriteGate + GuardrailEngine seam wired
into orchestrator.py's loop (preflight -> dispatch -> postwrite, and the
should_cancel cooperative-cancellation seam added in the same milestone).

Scripted with ``FakeStructuredProvider`` (same pattern as
test_agent_orchestrator.py) + a REAL ``ArtifactManager`` over the demo
network (data/practice.inpx) + a fake write tool registered in an isolated
``ToolRegistry``, so every test is deterministic, makes no paid API calls,
and never touches the production registry. No ``unittest.mock`` anywhere,
per repo precedent -- fakes are hand-rolled, and rollback/version assertions
check real on-disk state rather than spying on method calls.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from services.agent.guardrails import GuardrailEngine
from services.agent.orchestrator import RefuseAllWriteGate, run_agent_turn
from services.agent.state import ArtifactManager
from services.agent.toolkit import ToolRegistry, tool
from services.agent.trace import read_trace

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"


class FakeStructuredProvider:
    """Canned (action, meta) queue implementing the ``generate_structured`` seam.

    Copied from test_agent_orchestrator.py's helper of the same name (no
    shared test-utilities module exists in this repo -- see conftest.py).
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


class _AllowAllGate:
    """Test-only permissive WriteGate: authorizes every write-tier action."""

    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, Any], Any]] = []

    def authorize(self, action: dict[str, Any], spec: Any) -> bool:
        self.calls.append((action, spec))
        return True


def _mutate_link_101_name(working_path: Path, new_name: str) -> None:
    """Same technique as test_agent_state.py's _rename_link_101: a safe,
    parseable text mutation standing in for "the COM write touched
    something on disk" -- used to give the nothing-else-moved diff and
    rollback_to() an observable effect to detect/undo."""
    text = working_path.read_text(encoding="utf-8")
    text = text.replace(
        'link no="101" name="Main St EB approach"',
        f'link no="101" name="{new_name}"',
    )
    working_path.write_text(text, encoding="utf-8")


def _register_rigged_write_tool(
    registry: ToolRegistry,
    artifact_manager: ArtifactManager,
    *,
    readback_value: float,
    mutate: bool = False,
) -> dict[str, int]:
    """Registers a fake ``set_vehicle_input_volume`` WRITE tool (the real
    name -- so constraints.py's existing catalog, which keys off this tool
    name, applies naturally). Returns a ``{"count": n}`` dict tracking
    dispatch calls. When ``mutate`` is True, each call also rewrites link
    101's name on disk, simulating a COM write landing."""
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
        if mutate:
            _mutate_link_101_name(artifact_manager.working_path, "MUTATED BY FAKE WRITE")
        # Persist the written volume into the working copy, like a real write
        # tool's save path does — required since guardrails' target-persisted
        # check gained not_persisted semantics for volume (a None after a
        # write is now correctly refused as a dropped write).
        from eval.fake_edit_session import set_vehicle_input_volume_in_xml

        working = artifact_manager.working_path
        working.write_text(
            set_vehicle_input_volume_in_xml(
                working.read_text(encoding="utf-8"), str(params.input_no), params.volume
            ),
            encoding="utf-8",
        )
        return {
            "verification": {"written_value": params.volume, "readback_value": readback_value},
        }

    return calls


# ---------------------------------------------------------------------------
# WriteGate / RefuseAllWriteGate — unit-level
# ---------------------------------------------------------------------------


def test_refuse_all_write_gate_always_refuses():
    gate = RefuseAllWriteGate()

    assert gate.authorize({"tool": "set_vehicle_input_volume", "params": {}}, spec=None) is False


# ---------------------------------------------------------------------------
# (a) gate-approved write lands cleanly -> plan completes,
#     trace: preflight -> action -> postwrite
# ---------------------------------------------------------------------------


def test_gate_approved_write_lands_cleanly_and_plan_completes(tmp_path: Path):
    registry = ToolRegistry()
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()
    calls = _register_rigged_write_tool(registry, artifact_manager, readback_value=600, mutate=False)

    provider = FakeStructuredProvider(
        [
            _action(
                "set_vehicle_input_volume",
                summary="교통량을 수정합니다",
                input_no="1",
                volume=600,
            ),
            _action(
                "respond", summary="수정을 완료했습니다", answer="교통량을 600으로 수정했습니다."
            ),
        ]
    )

    result = run_agent_turn(
        "1번 입력 교통량을 600으로 바꿔줘",
        provider=provider,
        work_dir=work_dir,
        session_id="s-write-ok",
        language="ko",
        registry=registry,
        gate=_AllowAllGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    assert result.kind == "answer"
    assert "600" in result.answer
    assert calls["count"] == 1  # dispatched exactly once

    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records] == [
        "plan",
        "preflight",
        "action_start",
        "action_end",
        "postwrite",
        "plan",
    ]
    assert records[1]["payload"]["blocking"] is False
    assert records[4]["payload"]["blocking"] is False

    # the write actually advanced the version counter (a version was saved)
    assert artifact_manager.version == 1


def test_preflight_warning_proceeds_with_dispatch_and_attaches_warning_to_observation(
    tmp_path: Path,
):
    # link 101 has 3 lanes -> rough capacity 3*1900 = 5700 veh/h; 6000
    # exceeds it -> latent_demand_overflow (warn, not refuse).
    registry = ToolRegistry()
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()
    calls = _register_rigged_write_tool(registry, artifact_manager, readback_value=6000, mutate=False)

    provider = FakeStructuredProvider(
        [
            _action(
                "set_vehicle_input_volume",
                summary="교통량을 수정합니다",
                input_no="1",
                volume=6000,
            ),
            _action(
                "respond", summary="수정을 완료했습니다", answer="교통량을 6000으로 수정했습니다."
            ),
        ]
    )

    result = run_agent_turn(
        "1번 입력 교통량을 6000으로 바꿔줘",
        provider=provider,
        work_dir=work_dir,
        session_id="s-write-warn",
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
        "warning",
        "action_start",
        "action_end",
        "postwrite",
        "plan",
    ]
    assert records[2]["payload"]["findings"] == ["latent_demand_overflow"]

    # the warning rode along on the observation the planner saw next turn
    write_observation = result.state.history[0].observation
    assert write_observation["guardrail_warnings"][0]["constraint_id"] == "latent_demand_overflow"


# ---------------------------------------------------------------------------
# (b) mismatched readback -> write_verification_failed, rollback_to actually
#     called (version state restored), loop aborted, refusal answer returned
# ---------------------------------------------------------------------------


def test_postwrite_mismatch_rolls_back_and_aborts_the_whole_plan(tmp_path: Path):
    registry = ToolRegistry()
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()
    pristine_bytes = artifact_manager.working_path.read_bytes()

    # written=600, readback=500 -- deliberate mismatch. mutate=True also
    # gives the nothing-else-moved diff (and rollback_to) something visible
    # on disk to catch/undo.
    calls = _register_rigged_write_tool(registry, artifact_manager, readback_value=500, mutate=True)

    provider = FakeStructuredProvider(
        [
            _action(
                "set_vehicle_input_volume",
                summary="교통량을 수정합니다",
                input_no="1",
                volume=600,
            ),
        ]
    )

    result = run_agent_turn(
        "1번 입력 교통량을 600으로 바꿔줘",
        provider=provider,
        work_dir=work_dir,
        session_id="s-write-mismatch",
        language="ko",
        registry=registry,
        gate=_AllowAllGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    assert calls["count"] == 1  # dispatched exactly once, never retried
    assert len(provider.calls) == 1  # the planner was never asked for a second decision
    assert result.kind == "answer"
    assert "600" in result.answer
    assert "500" in result.answer

    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records] == [
        "plan",
        "preflight",
        "action_start",
        "action_end",
        "postwrite",
        "refusal",
    ]
    assert records[4]["payload"]["blocking"] is True
    assert "write_verification_failed" in records[4]["payload"]["findings"]
    assert records[5]["payload"]["error_type"] == "write_verification_failed"
    assert records[5]["payload"]["phase"] == "postwrite"

    # rollback_to() actually ran: the version counter still advanced (the
    # audit trail is never rewritten -- ArtifactManager's own contract) but
    # the WORKING COPY's content is restored to pristine.
    assert artifact_manager.version == 1
    assert artifact_manager.working_path.read_bytes() == pristine_bytes

    # the aborted write's own observation still landed in history
    assert result.state.history[0].observation["error_type"] == "write_verification_failed"


# ---------------------------------------------------------------------------
# (c) refuse-tier preflight (object_not_found on a bogus id) -> dispatch
#     never happens; the refusal is fed back to the planner (loop continues)
# ---------------------------------------------------------------------------


def test_preflight_refusal_skips_dispatch_and_feeds_the_planner(tmp_path: Path):
    registry = ToolRegistry()
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()
    calls = _register_rigged_write_tool(registry, artifact_manager, readback_value=600)

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
        session_id="s-preflight-refuse",
        language="ko",
        registry=registry,
        gate=_AllowAllGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    assert calls["count"] == 0  # dispatch never happened
    assert artifact_manager.version == 0  # nothing was ever saved

    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records] == ["plan", "preflight", "refusal", "plan"]
    assert not any(r["kind"] in ("action_start", "action_end", "postwrite") for r in records)
    assert records[1]["payload"]["blocking"] is True
    assert records[2]["payload"]["error_type"] == "object_not_found"
    assert records[2]["payload"]["phase"] == "preflight"

    # the refusal reached the planner as this step's observation, and the
    # loop CONTINUED (unlike postwrite's abort) to a normal final answer.
    assert result.state.history[0].observation["error_type"] == "object_not_found"
    assert result.kind == "answer"


# ---------------------------------------------------------------------------
# (d) default RefuseAllWriteGate -> write tool never dispatched
#     (pins M1 behavior unchanged, even with engine/artifact_manager set up)
# ---------------------------------------------------------------------------


def test_default_gate_still_refuses_write_even_with_engine_and_artifact_manager_supplied(
    tmp_path: Path,
):
    registry = ToolRegistry()
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()
    calls = _register_rigged_write_tool(registry, artifact_manager, readback_value=600)

    provider = FakeStructuredProvider(
        [
            _action(
                "set_vehicle_input_volume",
                summary="교통량을 수정합니다",
                input_no="1",
                volume=600,
            ),
            _action("respond", summary="수정 불가 안내", answer="지금은 수정할 수 없습니다."),
        ]
    )

    result = run_agent_turn(
        "1번 입력 교통량을 600으로 바꿔줘",
        provider=provider,
        work_dir=work_dir,
        session_id="s-default-gate",
        language="ko",
        registry=registry,
        # gate intentionally omitted -- must default to RefuseAllWriteGate,
        # even though engine/artifact_manager ARE supplied.
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    assert calls["count"] == 0
    assert artifact_manager.version == 0

    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records] == ["plan", "refusal", "plan"]
    assert records[1]["payload"]["error_type"] == "tool_tier_forbidden"
    assert result.kind == "answer"


def test_permissive_gate_without_artifact_manager_raises(tmp_path: Path):
    """Interface contract: a permissive gate REQUIRES an artifact_manager.
    This is a caller configuration error (fail loudly), not a guardrail
    refusal -- so it raises rather than degrading silently."""
    registry = ToolRegistry()
    _register_rigged_write_tool(
        registry, ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path / "unused"),
        readback_value=600,
    )

    provider = FakeStructuredProvider(
        [
            _action(
                "set_vehicle_input_volume",
                summary="교통량을 수정합니다",
                input_no="1",
                volume=600,
            ),
        ]
    )

    with pytest.raises(RuntimeError, match="artifact_manager"):
        run_agent_turn(
            "1번 입력 교통량을 600으로 바꿔줘",
            provider=provider,
            work_dir=tmp_path,
            session_id="s-no-artifact-manager",
            language="ko",
            registry=registry,
            gate=_AllowAllGate(),
            # artifact_manager intentionally omitted
        )


# ---------------------------------------------------------------------------
# (e) should_cancel firing -> "cancelled" result + trace record
# ---------------------------------------------------------------------------


def test_should_cancel_after_first_iteration_ends_turn_as_cancelled(tmp_path: Path):
    provider = FakeStructuredProvider(
        [_action("get_network_overview", summary="개요를 확인합니다")], repeat_last=True
    )
    call_count = {"n": 0}

    def _should_cancel() -> bool:
        call_count["n"] += 1
        return call_count["n"] > 1  # let iteration 0 run; cancel before iteration 1

    result = run_agent_turn(
        "이 네트워크에 뭐가 있어?",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-cancel",
        language="ko",
        should_cancel=_should_cancel,
    )

    assert result.kind == "cancelled"
    assert result.answer.strip()
    assert len(provider.calls) == 1  # iteration 0's planner call ran; no second one

    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records][-1] == "cancelled"
    assert records[-1]["payload"] == {"iteration": 1}


def test_should_cancel_on_first_iteration_never_calls_the_planner(tmp_path: Path):
    provider = FakeStructuredProvider([])  # empty queue -- must never be drawn from

    result = run_agent_turn(
        "이 네트워크에 뭐가 있어?",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-cancel-immediate",
        language="ko",
        should_cancel=lambda: True,
    )

    assert result.kind == "cancelled"
    assert result.steps == []
    assert result.state.history == []
    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records] == ["cancelled"]
    assert records[0]["payload"] == {"iteration": 0}


def test_should_cancel_none_default_never_cancels(tmp_path: Path):
    # Explicit pin: should_cancel's default (None) must behave exactly as
    # if the parameter did not exist at all.
    provider = FakeStructuredProvider(
        [_action("respond", summary="정리했습니다", answer="완료했습니다.")]
    )

    result = run_agent_turn(
        "이 네트워크에 뭐가 있어?",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-no-cancel",
        language="ko",
    )

    assert result.kind == "answer"
    records = read_trace(result.trace_path)
    assert not any(r["kind"] == "cancelled" for r in records)

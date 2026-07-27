"""Tests for M3.3's session persistence + clarification memory wiring in
services.agent.orchestrator.run_agent_turn (reload-by-session-id, the
accumulated-warnings cross-turn suffix, and the clarification-memory system
note) — orchestrator-level, same altitude as
test_agent_orchestrator_complexity_tiers.py. session_store.py's own
save/load/sanitization contract is covered directly in
test_agent_session_store.py; TraceWriter's side of the same session-id
sanitization blocking finding is covered in test_agent_trace.py.

The roadmap's (docs/phase2/02-ROADMAP.md M3.3) accept criterion is exercised
end to end by ``test_three_turn_conversation_...`` below: a request missing
a duration produces a clarification with a recommended default; the user's
confirmation carries the merged context into the plan, which proceeds and
fires a postrun warning; a third, unrelated follow-up turn still surfaces
that warning in its final answer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from services.agent import session_store
from services.agent.guardrails import GuardrailEngine
from services.agent.orchestrator import run_agent_turn
from services.agent.toolkit import ToolRegistry, tool
from services.agent.trace import read_trace


class FakeStructuredProvider:
    """Canned (action, meta) queue — see test_agent_orchestrator.py's
    identical fixture for the full rationale."""

    def __init__(self, queue: list[tuple[dict[str, Any] | None, dict[str, Any]]]) -> None:
        self._queue = list(queue)
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
        return self._queue.pop(0)


def _action(
    tool_name: str, *, summary: str = "확인합니다", **params: Any
) -> tuple[dict[str, Any], dict[str, Any]]:
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "fake", "fallback_rung": 0}


class _AllowAllGate:
    """Test-only permissive WriteGate — same fixture shape as
    test_agent_orchestrator_run_gate.py/complexity_tiers.py's identical
    class. Using it here (rather than exercising the real approval pause)
    keeps this file focused on session persistence, not approval mechanics
    — the task brief for this milestone explicitly leaves
    ``services.agent.gates``/pending_approval mechanics untouched."""

    def authorize(self, action: dict[str, Any], spec: Any) -> bool:
        return True


def _register_short_run_tool(registry: ToolRegistry) -> dict[str, int]:
    """A ``run_simulation`` fake whose result has only the SIMULATIONRUN
    table — fires ``results_missing_kpi_tables`` (warn, postrun), the same
    trigger shape as test_agent_orchestrator_run_gate.py's identical
    fixture."""
    calls = {"count": 0}

    class Params(BaseModel):
        model_config = ConfigDict(extra="forbid")

        duration_s: int
        random_seeds: list[int] | None = None

    @tool(name="run_simulation", tier="run", provider="run", params=Params, registry=registry)
    def _run(params: Params) -> dict[str, Any]:
        """Run the network (fake/test double, always a short/clean run)."""
        calls["count"] += 1
        return {
            "verification": {"job_id": "fake-job", "duration_s": params.duration_s},
            "run_result": {"tables": ["SIMULATIONRUN"], "deleted_vehicles": 0},
        }

    return calls


# ---------------------------------------------------------------------------
# Reload plumbing in isolation (no clarification/warnings involved)
# ---------------------------------------------------------------------------


def test_fresh_session_id_never_reloads_anything(tmp_path: Path):
    """A brand-new work_dir/session_id (no state.json yet) must behave
    byte-for-byte like before M3.3 — the reload is a no-op."""
    provider = FakeStructuredProvider(
        [_action("respond", summary="답변합니다", answer="네트워크 개요입니다.")]
    )

    result = run_agent_turn(
        "이 네트워크에 뭐가 있어?",
        provider=provider,
        work_dir=tmp_path,
        session_id="s-fresh-only",
        language="ko",
    )

    assert result.kind == "answer"
    assert result.answer == "네트워크 개요입니다."
    assert result.state.history == []


def test_second_call_on_the_same_session_reloads_history_and_context(tmp_path: Path):
    registry = ToolRegistry()
    _register_short_run_tool(registry)
    session_id = "s-reload-plain"

    provider_1 = FakeStructuredProvider(
        [_action("respond", summary="1차 답변", answer="첫 번째 답변입니다.")]
    )
    result_1 = run_agent_turn(
        "첫 번째 요청",
        provider=provider_1,
        work_dir=tmp_path,
        session_id=session_id,
        language="ko",
        registry=registry,
    )
    assert result_1.kind == "answer"

    provider_2 = FakeStructuredProvider(
        [_action("respond", summary="2차 답변", answer="두 번째 답변입니다.")]
    )
    result_2 = run_agent_turn(
        "두 번째 요청",
        provider=provider_2,
        work_dir=tmp_path,
        session_id=session_id,
        language="ko",
        registry=registry,
    )

    assert result_2.kind == "answer"
    # The second turn's own goal is what shows up in ITS prompt, not the
    # first turn's stale goal.
    second_call_prompt = provider_2.calls[0]["messages"][0]["content"]
    assert "Goal: 두 번째 요청" in second_call_prompt

    # state.json was written after BOTH turns (every terminal kind).
    persisted = session_store.load_state(tmp_path, session_id)
    assert persisted is not None
    assert persisted.goal == "두 번째 요청"


def test_malformed_session_id_raises_before_any_file_is_written(tmp_path: Path):
    """The M3.3 blocking finding, exercised end to end through
    run_agent_turn (not just TraceWriter/session_store in isolation) — a
    caller that reuses a client-suppliable session_id without validating it
    first still cannot path-traverse."""
    provider = FakeStructuredProvider([_action("respond", answer="이것은 절대 호출되지 않습니다.")])

    with pytest.raises(ValueError):
        run_agent_turn(
            "요청",
            provider=provider,
            work_dir=tmp_path,
            session_id="../../../escape",
            language="ko",
        )

    assert list(tmp_path.rglob("*")) == []
    assert provider.calls == []  # never even reached the planner


# ---------------------------------------------------------------------------
# Full 3-turn scripted acceptance scenario (docs/phase2/02-ROADMAP.md M3.3)
# ---------------------------------------------------------------------------


def test_three_turn_conversation_carries_clarification_and_warning_forward(tmp_path: Path):
    registry = ToolRegistry()
    calls = _register_short_run_tool(registry)
    work_dir = tmp_path / "session"
    session_id = "s-multi-turn"

    # --- turn 1: missing duration -> clarification with a recommended default ---
    clarification_question = "실행 시간이 없어 기본값인 300초로 진행해도 될까요?"
    provider_1 = FakeStructuredProvider(
        [_action("ask_user", summary="실행 시간을 확인합니다", question=clarification_question)]
    )

    result_1 = run_agent_turn(
        "시뮬레이션을 돌려줘",
        provider=provider_1,
        work_dir=work_dir,
        session_id=session_id,
        language="ko",
        registry=registry,
    )

    assert result_1.kind == "clarification"
    assert result_1.answer == clarification_question

    persisted_1 = session_store.load_state(work_dir, session_id)
    assert persisted_1 is not None
    assert persisted_1.context["pending_clarification"] == {
        "goal": "시뮬레이션을 돌려줘",
        "question": clarification_question,
    }

    # --- turn 2: user confirms -> the planner prompt carries BOTH the
    #     original goal and the new reply (merged in the prompt, not by
    #     regex), the run proceeds, and fires a postrun warning. ---
    provider_2 = FakeStructuredProvider(
        [
            _action(
                "run_simulation",
                summary="300초로 실행합니다",
                duration_s=300,
                random_seeds=[1, 2, 3, 4, 5],
            ),
            _action("respond", summary="실행을 완료했습니다", answer="300초 실행을 완료했습니다."),
        ]
    )

    result_2 = run_agent_turn(
        "네, 300초로 해주세요",
        provider=provider_2,
        work_dir=work_dir,
        session_id=session_id,
        language="ko",
        registry=registry,
        gate=_AllowAllGate(),
        engine=GuardrailEngine(),
    )

    assert calls["count"] == 1
    assert result_2.kind == "answer"

    # Clarification memory: the FIRST planner call this turn saw a system
    # note carrying the original goal + the question + this turn's own
    # reply — proving the merge happened in the prompt, not via regex.
    first_call_prompt = provider_2.calls[0]["messages"][0]["content"]
    assert "시뮬레이션을 돌려줘" in first_call_prompt
    assert clarification_question in first_call_prompt
    assert "네, 300초로 해주세요" in first_call_prompt

    # The postrun warning actually fired.
    postrun_records = [r for r in read_trace(result_2.trace_path) if r["kind"] == "postrun"]
    assert len(postrun_records) == 1
    assert postrun_records[0]["payload"]["findings"] == ["results_missing_kpi_tables"]

    # Turn 2's own answer is untouched: carried_forward_warnings is always a
    # snapshot from BEFORE this turn started, and this is the FIRST time the
    # warning ever fired (nothing to carry forward into turn 2 itself yet).
    assert result_2.answer == "300초 실행을 완료했습니다."

    persisted_2 = session_store.load_state(work_dir, session_id)
    assert persisted_2 is not None
    assert "pending_clarification" not in persisted_2.context  # resolved, cleared
    accumulated = persisted_2.context.get("accumulated_warnings")
    assert accumulated is not None and len(accumulated) == 1
    assert accumulated[0]["constraint_id"] == "results_missing_kpi_tables"
    assert "SIMULATIONRUN" in accumulated[0]["message"]

    # --- turn 3: an unrelated follow-up -- the turn-2 warning must still
    #     surface in this turn's final answer (the roadmap's literal accept
    #     criterion). ---
    provider_3 = FakeStructuredProvider(
        [_action("respond", summary="요약합니다", answer="시뮬레이션 결과를 요약했습니다.")]
    )

    result_3 = run_agent_turn(
        "결과를 요약해줘",
        provider=provider_3,
        work_dir=work_dir,
        session_id=session_id,
        language="ko",
        registry=registry,
    )

    assert result_3.kind == "answer"
    assert "시뮬레이션 결과를 요약했습니다." in result_3.answer
    assert "결과 데이터베이스" in result_3.answer  # the ko results_missing_kpi_tables text
    assert "SIMULATIONRUN" in result_3.answer
    assert "참고:" in result_3.answer  # the carried-forward-warnings header
    # Proves it is the M3.3 cross-turn mechanism, not M3.2's agentic-tier
    # notice, that put the warning here: turn 3's message ("결과를 요약해줘")
    # is "simple" tier, and _append_agentic_warning_notice only ever runs
    # inside `if tier == "agentic":` — a "simple"-tier turn never reaches
    # that code path at all, so the warning could only have arrived via
    # _append_carried_forward_warnings. History carries forward unchanged
    # from turn 2 (this turn dispatched no tool of its own).
    assert len(result_3.state.history) == 1
    assert result_3.state.history[0].action == "run_simulation"

    # The ledger keeps growing forward from turn 3 too (nothing lost, and no
    # accidental duplicate entry appended by the read-only turn).
    persisted_3 = session_store.load_state(work_dir, session_id)
    assert persisted_3.context["accumulated_warnings"] == accumulated


# ---------------------------------------------------------------------------
# D4 live-demo fix (docs/phase2/DECISIONS.md, 2026-07-27): the "참고: 이전
# 대화에서 발생한 경고" block (i) must collapse two instances of the SAME
# constraint (e.g. conflict_area_data_unavailable's Passive-count text before
# vs. after the write that changed it) into ONE, keeping the latest, and
# (ii) must not re-paste a warning's full text on every subsequent answer
# forever once it has already been shown once.
# ---------------------------------------------------------------------------


def test_record_accumulated_warnings_dedups_by_constraint_id_keeping_latest_message():
    """Direct, white-box test of _record_accumulated_warnings: the SAME
    constraint firing twice (across two separate dispatches, e.g. a
    conflict-area write's preflight-time and postwrite-time counts) with a
    DIFFERENT message must collapse to ONE ledger entry carrying the LATEST
    text — not two permanently-coexisting entries (the live incident: 145
    Passive pairs before the write, 144 after, both shown forever)."""
    from services.agent.constraints import Finding
    from services.agent.orchestrator import _record_accumulated_warnings
    from services.agent.state import AgentState

    state = AgentState(goal="x", artifacts={}, history=[], context={})

    before_finding = Finding(
        constraint_id="conflict_area_data_unavailable",
        tier="warn",
        phase="postwrite",
        messages={"ko": "충돌구간 145개가 Passive 상태입니다."},
    )
    _record_accumulated_warnings(state, [before_finding], "ko")

    after_finding = Finding(
        constraint_id="conflict_area_data_unavailable",
        tier="warn",
        phase="postwrite",
        messages={"ko": "충돌구간 144개가 Passive 상태입니다."},
    )
    _record_accumulated_warnings(state, [after_finding], "ko")

    accumulated = state.context["accumulated_warnings"]
    assert len(accumulated) == 1  # collapsed, not two entries
    assert accumulated[0] == {
        "constraint_id": "conflict_area_data_unavailable",
        "message": "충돌구간 144개가 Passive 상태입니다.",  # the LATEST, not the first
    }


def test_record_accumulated_warnings_keeps_stable_position_for_a_repeat_constraint():
    """A repeat constraint updates its message IN PLACE (stays at its
    original ledger position); a genuinely new constraint still appends at
    the end — the ledger's ordering is otherwise unaffected by the dedup
    fix."""
    from services.agent.constraints import Finding
    from services.agent.orchestrator import _record_accumulated_warnings
    from services.agent.state import AgentState

    state = AgentState(goal="x", artifacts={}, history=[], context={})
    _record_accumulated_warnings(
        state,
        [Finding(constraint_id="a", tier="warn", phase="postwrite", messages={"ko": "a1"})],
        "ko",
    )
    _record_accumulated_warnings(
        state,
        [Finding(constraint_id="b", tier="warn", phase="postwrite", messages={"ko": "b1"})],
        "ko",
    )
    _record_accumulated_warnings(
        state,
        [Finding(constraint_id="a", tier="warn", phase="postwrite", messages={"ko": "a2"})],
        "ko",
    )

    accumulated = state.context["accumulated_warnings"]
    assert [entry["constraint_id"] for entry in accumulated] == ["a", "b"]
    assert accumulated[0]["message"] == "a2"  # updated in place
    assert accumulated[1]["message"] == "b1"


def test_collect_history_warnings_dedups_by_constraint_id_keeping_latest_message():
    """The M3.2 agentic-tier intra-turn safety net's own source of truth
    gets the same latest-wins dedup discipline as the cross-turn ledger."""
    from services.agent.orchestrator import _collect_history_warnings
    from services.agent.state import StepRecord

    history = [
        StepRecord(
            thought="",
            action="set_vehicle_input_volume",
            params={},
            observation={
                "guardrail_warnings": [
                    {"constraint_id": "conflict_area_data_unavailable", "message": "145개"}
                ]
            },
        ),
        StepRecord(
            thought="",
            action="set_conflict_area_status",
            params={},
            observation={
                "guardrail_warnings": [
                    {"constraint_id": "conflict_area_data_unavailable", "message": "144개"}
                ]
            },
        ),
    ]

    collected = _collect_history_warnings(history)

    assert len(collected) == 1
    assert collected[0]["message"] == "144개"  # the LATEST, not the first-seen


def test_prior_turn_warning_shown_once_then_collapses_to_a_summary_line(tmp_path: Path):
    """D4(ii): a warning that already appeared verbatim in an earlier turn's
    answer must not be re-pasted in full on EVERY subsequent turn forever —
    only the first carry-forward shows the full text; later turns collapse
    it into a single summary bullet."""
    registry = ToolRegistry()
    calls = _register_short_run_tool(registry)
    work_dir = tmp_path / "session"
    session_id = "s-repeat-warning"

    # Turn 1: the run itself, firing results_missing_kpi_tables (nothing to
    # carry forward yet -- same as the 3-turn test above).
    provider_1 = FakeStructuredProvider(
        [
            # random_seeds carries 5 seeds (not 1) so single_seed_run_not_
            # statistically_valid does NOT also fire -- this test isolates
            # results_missing_kpi_tables alone, same discipline as the
            # 3-turn test above.
            _action(
                "run_simulation",
                summary="실행합니다",
                duration_s=300,
                random_seeds=[1, 2, 3, 4, 5],
            ),
            _action("respond", summary="완료", answer="실행을 완료했습니다."),
        ]
    )
    result_1 = run_agent_turn(
        "시뮬레이션을 300초 돌려줘",
        provider=provider_1,
        work_dir=work_dir,
        session_id=session_id,
        language="ko",
        registry=registry,
        gate=_AllowAllGate(),
        engine=GuardrailEngine(),
    )
    assert calls["count"] == 1
    assert "참고:" not in result_1.answer  # nothing carried forward into turn 1's own answer

    # Turn 2: unrelated follow-up -- FIRST carry-forward, full text shown.
    provider_2 = FakeStructuredProvider(
        [_action("respond", summary="요약", answer="결과를 요약했습니다.")]
    )
    result_2 = run_agent_turn(
        "결과를 요약해줘",
        provider=provider_2,
        work_dir=work_dir,
        session_id=session_id,
        language="ko",
        registry=registry,
    )
    assert "참고:" in result_2.answer
    assert "SIMULATIONRUN" in result_2.answer  # full warning text, first time shown

    # Turn 3: ANOTHER unrelated follow-up -- the SAME warning is still in the
    # ledger (nothing resolved it), but must NOT be re-pasted in full again.
    provider_3 = FakeStructuredProvider(
        [_action("respond", summary="추가 요약", answer="추가로 확인했습니다.")]
    )
    result_3 = run_agent_turn(
        "더 확인해줘",
        provider=provider_3,
        work_dir=work_dir,
        session_id=session_id,
        language="ko",
        registry=registry,
    )
    assert "참고:" in result_3.answer  # still noted...
    assert "SIMULATIONRUN" not in result_3.answer  # ...but NOT the full text again
    assert "1" in result_3.answer  # the "N other warning(s) still apply" count

    persisted_3 = session_store.load_state(work_dir, session_id)
    assert persisted_3.context["warnings_shown"] == ["results_missing_kpi_tables"]


def test_second_clarification_note_is_not_duplicated_with_the_first(tmp_path: Path):
    """A clarification note is a per-turn, ephemeral prompt hint (context
    key "warnings"), never an accumulating ledger — turn 3 must not still
    carry turn 1's already-resolved clarification note once turn 2 asked
    (and resolved) its own, DIFFERENT question."""
    work_dir = tmp_path / "session"
    session_id = "s-double-clarify"

    provider_1 = FakeStructuredProvider(
        [_action("ask_user", summary="확인 1", question="어떤 시나리오를 확인할까요?")]
    )
    run_agent_turn(
        "결과를 확인해줘",
        provider=provider_1,
        work_dir=work_dir,
        session_id=session_id,
        language="ko",
    )

    provider_2 = FakeStructuredProvider(
        [_action("ask_user", summary="확인 2", question="어느 기간을 확인할까요?")]
    )
    result_2 = run_agent_turn(
        "기본 시나리오요",
        provider=provider_2,
        work_dir=work_dir,
        session_id=session_id,
        language="ko",
    )
    assert result_2.kind == "clarification"

    # turn 2's own prompt carried turn 1's note (goal "결과를 확인해줘" +
    # question "어떤 시나리오를...").
    turn_2_prompt = provider_2.calls[0]["messages"][0]["content"]
    assert "결과를 확인해줘" in turn_2_prompt
    assert "어떤 시나리오를 확인할까요?" in turn_2_prompt

    provider_3 = FakeStructuredProvider(
        [_action("respond", summary="정리", answer="확인했습니다.")]
    )
    run_agent_turn(
        "지난 주요",
        provider=provider_3,
        work_dir=work_dir,
        session_id=session_id,
        language="ko",
    )

    turn_3_prompt = provider_3.calls[0]["messages"][0]["content"]
    # turn 3's note is about turn 2's question ("어느 기간을...") + this
    # turn's own reply ("지난 주요") -- turn 1's already-resolved question
    # must NOT still be present (would mean the note is accumulating
    # instead of being replaced each turn).
    assert "어느 기간을 확인할까요?" in turn_3_prompt
    assert "지난 주요" in turn_3_prompt
    assert "어떤 시나리오를 확인할까요?" not in turn_3_prompt

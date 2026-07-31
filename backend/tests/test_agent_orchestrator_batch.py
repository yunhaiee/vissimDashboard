"""End-to-end approval-flow integration for QUEUE 3 unit 8's
``apply_edit_plan``: the REAL registered tool (default_registry), driven
through a REAL ``run_agent_turn`` with a REAL ``ArtifactManager``/
``GuardrailEngine`` -- mirrors test_agent_orchestrator_rollback.py's own
house pattern (``FakeStructuredProvider``, the canned (action, meta) helper,
the ``data/practice.inpx`` + appended empty ``<speedDistributions>``/
``<reducedSpeedAreas>`` sections fixture) almost verbatim.

**Why ``set_reduced_speed_area`` stands in for "apply real edits" in the
applies-fully / mid-batch-failure transcripts**, rather than a COM verb:
this unit's own verification section asks for transcripts "on SYNTHETIC
data" -- a COM verb needs a live, licensed Vissim session
(docs/phase2/00-EXECUTION-GUIDE.md's working conventions forbid a non-COM
code path from requiring live Vissim). ``set_reduced_speed_area`` needs no
COM at all (tools_edit.py's own module docstring), and three of them,
targeting three different segments of the same link, compose freely as
independent batch steps (no resolvable write target at all, so neither
collision detection nor patched-shadow preflight applies to them -- exactly
the "documented gap, not a wrong answer" the batch module's own docstring
describes for an unmapped tool).

**Why the validation-only transcripts (collision / combined-effect /
recursion) need no live edit session at all**: a blocking preflight refuses
BEFORE ``ApprovalRequired``'s branch ever offers an approval card, so
``toolkit.dispatch`` — and therefore ``_require_session()`` — is never
reached. Only ``artifact_manager`` (for its real, parsed shadow model) is
needed.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

import services.agent.tools_edit as tools_edit  # registers apply_edit_plan + every write tool
import services.agent.tools_run as tools_run  # noqa: F401 - registers run_simulation
from services.agent.edit_session import clear_active_edit_session, set_active_edit_session
from services.agent.gates import PreapprovedWriteGate, ProposeWriteGate, signature
from services.agent.guardrails import GuardrailEngine
from services.agent.orchestrator import run_agent_turn
from services.agent.state import ArtifactManager
from services.agent.trace import read_trace

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"


class FakeStructuredProvider:
    """Canned (action, meta) queue implementing the ``generate_structured``
    seam. Copied from test_agent_orchestrator_rollback.py's helper of the
    same name (no shared test-utilities module exists in this repo)."""

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
        self.calls.append({"messages": messages, "tools": tools, "schema": schema, "system": system})
        if not self._queue:
            raise AssertionError("FakeStructuredProvider queue exhausted unexpectedly")
        return self._queue.pop(0)


def _action(tool_name: str, *, summary: str = "확인합니다", **params: Any):
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "fake", "fallback_rung": 0}


class _FakeWorkingInpxSession:
    """The minimal shape apply_edit_plan/set_reduced_speed_area need: a
    ``working_inpx`` path attribute, no COM methods at all. Copied from
    test_agent_orchestrator_rollback.py."""

    def __init__(self, working_inpx: Path) -> None:
        self.working_inpx = working_inpx


def _demo_source(tmp_path: Path, *, extra_vehicle_input: bool = False) -> Path:
    """``data/practice.inpx`` content, with empty ``<speedDistributions>``/
    ``<reducedSpeedAreas>`` sections appended (copied verbatim from
    test_agent_orchestrator_rollback.py's own ``_demo_source_with_speed_
    sections``) and, optionally, a SECOND ``<vehicleInput>`` injected onto
    link 101 alongside the existing one -- link 101 (3 lanes, rough capacity
    3 * 1900 = 5700 veh/h) would otherwise carry only ONE vehicle input, with
    nothing to share it with (the sibling-aware checks are silent with no
    siblings)."""
    content = DEMO_INPX.read_text(encoding="utf-8").rstrip()
    assert content.endswith("</network>")
    if extra_vehicle_input:
        original = '<vehicleInput no="1" name="Main St EB" link="101"/>'
        assert original in content
        content = content.replace(
            original, original + '<vehicleInput no="7" name="Main St EB 2" link="101"/>'
        )
    patched = (
        content[: -len("</network>")]
        + "<speedDistributions></speedDistributions>"
        + "<reducedSpeedAreas></reducedSpeedAreas></network>"
    )
    source = tmp_path / "source.inpx"
    source.write_text(patched, encoding="utf-8")
    return source


def _clone(tmp_path: Path, work_dir: Path, *, extra_vehicle_input: bool = False) -> ArtifactManager:
    source_inpx = _demo_source(tmp_path, extra_vehicle_input=extra_vehicle_input)
    artifact_manager = ArtifactManager(source_inpx=source_inpx, work_dir=work_dir)
    artifact_manager.clone()
    return artifact_manager


def _reduced_speed_step(from_m: float, length_m: float, speed_kmh: float = 40.0) -> dict[str, Any]:
    return {"tool": "set_reduced_speed_area", "params": {"link_id": "101", "speed_kmh": speed_kmh, "from_m": from_m, "length_m": length_m}}


def _three_step_plan() -> list[dict[str, Any]]:
    return [
        _reduced_speed_step(0.0, 10.0),
        _reduced_speed_step(50.0, 10.0),
        _reduced_speed_step(100.0, 10.0),
    ]


def _propose_and_approve_three_step_plan(
    tmp_path: Path, work_dir: Path, session_id: str
) -> tuple[ArtifactManager, Any]:
    """Shared propose -> approve flow for the 3-step reduced-speed-area
    plan, factored out so both the "applies fully" transcript and the
    rollback-after-a-successful-batch transcript can reuse it without
    duplicating the two-phase dance. Returns ``(approve_manager, approve_
    result)`` with the batch already fully applied (version == 1)."""
    artifact_manager = _clone(tmp_path, work_dir)
    plan_params = {"steps": _three_step_plan()}

    propose_provider = FakeStructuredProvider([_action("apply_edit_plan", **plan_params)])
    propose = run_agent_turn(
        "링크 101에 감속 구간 3개를 추가해줘",
        provider=propose_provider,
        work_dir=work_dir,
        session_id=session_id,
        language="ko",
        gate=ProposeWriteGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )
    assert propose.kind == "approval_request"

    approve_manager = ArtifactManager(source_inpx=artifact_manager.source_inpx, work_dir=work_dir)
    approve_manager.refresh_version()
    approve_manager.rollback_to(approve_manager.version)
    approved_sig = signature("apply_edit_plan", plan_params)

    approve_provider = FakeStructuredProvider(
        [
            _action("apply_edit_plan", **plan_params),
            _action("respond", summary="완료했습니다", answer="감속 구간 3개를 추가했습니다."),
        ]
    )
    session = _FakeWorkingInpxSession(approve_manager.working_path)
    set_active_edit_session(session)
    try:
        approve = run_agent_turn(
            "링크 101에 감속 구간 3개를 추가해줘",
            provider=approve_provider,
            work_dir=work_dir,
            session_id=session_id,
            language="ko",
            gate=PreapprovedWriteGate({approved_sig}),
            engine=GuardrailEngine(),
            artifact_manager=approve_manager,
        )
    finally:
        clear_active_edit_session()

    assert approve.kind == "answer"
    assert approve_manager.version == 1
    return approve_manager, approve


# ---------------------------------------------------------------------------
# (a) A 3-step plan that validates, gets approved (propose -> approve, the
#     real two-phase flow), and applies fully -- with per-step readbacks.
# ---------------------------------------------------------------------------


def test_apply_edit_plan_three_step_plan_validates_gets_approved_and_applies_fully(tmp_path: Path):
    work_dir = tmp_path / "session"
    session_id = "s-batch-ok"
    artifact_manager = _clone(tmp_path, work_dir)
    plan_params = {"steps": _three_step_plan()}

    # --- PROPOSE: preflight runs, the plan is not blocking, one approval
    #     card is returned (not three) ---
    propose_provider = FakeStructuredProvider(
        [_action("apply_edit_plan", summary="세 구간에 감속 구간을 추가합니다", **plan_params)]
    )
    propose = run_agent_turn(
        "링크 101에 감속 구간 3개를 추가해줘",
        provider=propose_provider,
        work_dir=work_dir,
        session_id=session_id,
        language="ko",
        gate=ProposeWriteGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    assert propose.kind == "approval_request"
    pending = propose.pending_approval
    assert pending["tool"] == "apply_edit_plan"
    assert len(pending["params"]["steps"]) == 3
    # Plan-overview + no refuse-tier finding rode on the card as a warning
    # (batch.validate_plan's always-present overview -- see that module).
    warning_ids = {w["constraint_id"] for w in pending["warnings"]}
    assert "apply_edit_plan_summary" in warning_ids
    overview = next(w for w in pending["warnings"] if w["constraint_id"] == "apply_edit_plan_summary")
    assert "set_reduced_speed_area" in overview["message"]
    # Focus: the same merged mini-map every step's own resolver would build
    # standalone (approval_focus._resolve_apply_edit_plan) -- link 101 shows
    # up (set_reduced_speed_area's own resolver anchors on the link + its
    # position along it).
    focus_types = {el["type"] for el in pending["focus"]["elements"]}
    assert "link" in focus_types
    assert pending["focus"]["bbox"] is not None

    # --- APPROVE: a fresh ArtifactManager over the SAME work dir (mirrors
    #     /agent/approve's own re-sync pattern), PreapprovedWriteGate scoped
    #     to EXACTLY the approved signature, a live-COM-free session ---
    approve_manager = ArtifactManager(source_inpx=artifact_manager.source_inpx, work_dir=work_dir)
    approve_manager.refresh_version()
    approve_manager.rollback_to(approve_manager.version)
    approved_sig = signature("apply_edit_plan", plan_params)

    approve_provider = FakeStructuredProvider(
        [
            _action("apply_edit_plan", summary="세 구간에 감속 구간을 추가합니다", **plan_params),
            _action("respond", summary="완료했습니다", answer="감속 구간 3개를 추가했습니다."),
        ]
    )
    session = _FakeWorkingInpxSession(approve_manager.working_path)
    set_active_edit_session(session)
    try:
        approve = run_agent_turn(
            "링크 101에 감속 구간 3개를 추가해줘",
            provider=approve_provider,
            work_dir=work_dir,
            session_id=session_id,
            language="ko",
            gate=PreapprovedWriteGate({approved_sig}),
            engine=GuardrailEngine(),
            artifact_manager=approve_manager,
        )
    finally:
        clear_active_edit_session()

    assert approve.kind == "answer"
    # Exactly ONE new saved version for the WHOLE batch (the design decision:
    # rollback_last_edit's one-level undo must undo the whole batch in a
    # single call -- see tools_edit.apply_edit_plan's own module comment).
    assert approve_manager.version == 1

    # --- Per-step readback shown in the action's own artifacts ---
    action_end_record = next(r for r in read_trace(approve.trace_path) if r["kind"] == "action_end" and r["payload"]["tool"] == "apply_edit_plan")
    assert action_end_record["payload"]["status"] == "ok"
    batch_step = next(r for r in approve.state.history if r.action == "apply_edit_plan")
    steps_artifacts = batch_step.observation["artifacts"]["steps"]
    assert [s["status"] for s in steps_artifacts] == ["applied", "applied", "applied"]
    assert all("verification" in s and s["verification"] for s in steps_artifacts)
    assert [s["tool"] for s in steps_artifacts] == ["set_reduced_speed_area"] * 3

    # --- Every step genuinely landed on disk ---
    saved = ET.parse(work_dir / "edit_001.inpx").getroot()
    areas = saved.findall(".//reducedSpeedAreas/reducedSpeedArea")
    # link 101 has 3 lanes in the demo dataset -- one <reducedSpeedArea> per
    # lane per injected area, so 3 steps * 3 lanes = 9.
    assert len(areas) == 9


# ---------------------------------------------------------------------------
# (b) A plan whose step 2 FAILS at apply time -> steps rolled back, byte-
#     identity of the restore shown, structured failure naming step 2.
# ---------------------------------------------------------------------------


def test_apply_edit_plan_step_2_fails_at_apply_time_rolls_back_and_reports_structured_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work_dir = tmp_path / "session"
    session_id = "s-batch-fail"
    artifact_manager = _clone(tmp_path, work_dir)
    plan_params = {"steps": _three_step_plan()}

    # Step 2's own underlying injector fails; step 1's own dispatch call
    # (which already ran, and already called session.persist()) succeeded
    # genuinely -- this proves the abort path restores a REAL mid-batch
    # divergence, not a no-op.
    real_inject = tools_edit.inject_reduced_speed_area
    call_count = {"n": 0}

    def flaky_inject(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("synthetic apply-time failure for step 2")
        return real_inject(*args, **kwargs)

    monkeypatch.setattr(tools_edit, "inject_reduced_speed_area", flaky_inject)

    pre_batch_bytes = artifact_manager.working_path.read_bytes()

    provider = FakeStructuredProvider(
        [
            _action("apply_edit_plan", summary="세 구간에 감속 구간을 추가합니다", **plan_params),
            _action("respond", summary="처리했습니다", answer="처리했습니다."),
        ]
    )
    session = _FakeWorkingInpxSession(artifact_manager.working_path)
    set_active_edit_session(session)
    try:
        result = run_agent_turn(
            "링크 101에 감속 구간 3개를 추가해줘",
            provider=provider,
            work_dir=work_dir,
            session_id=session_id,
            language="ko",
            gate=PreapprovedWriteGate({signature("apply_edit_plan", plan_params)}),
            engine=GuardrailEngine(),
            artifact_manager=artifact_manager,
        )
    finally:
        clear_active_edit_session()

    assert result.kind == "answer"
    assert call_count["n"] == 2  # step 3 was never attempted

    # --- toolkit.dispatch's own exception path -> never a false "ok"; no
    #     version was ever saved for this turn ---
    records = read_trace(result.trace_path)
    action_end = next(r for r in records if r["kind"] == "action_end" and r["payload"]["tool"] == "apply_edit_plan")
    assert action_end["payload"]["status"] == "error"
    assert action_end["payload"]["error_type"] == "tool_execution_error"
    assert artifact_manager.version == 0

    # --- byte-identity of the restore ---
    assert artifact_manager.working_path.read_bytes() == pre_batch_bytes
    # No leftover scratch snapshot file.
    assert not (work_dir / tools_edit._BATCH_SNAPSHOT_FILENAME).exists()

    # --- structured failure naming step 2, and the ledger (rolled_back /
    #     failed / never_reached) ---
    batch_step = next(r for r in result.state.history if r.action == "apply_edit_plan")
    observation = batch_step.observation
    assert observation["status"] == "error"
    assert "step 2/3" in observation["message"]
    assert "batch_step_apply_failed" in observation["message"]
    details = observation["artifacts"]
    assert details["failed_step_index"] == 2
    assert details["restore_verified"] is True
    statuses = {s["index"]: s["status"] for s in details["steps"]}
    assert statuses == {1: "rolled_back", 2: "failed", 3: "never_reached"}


# ---------------------------------------------------------------------------
# (c) A plan refused at validation: same-object-same-field collision.
# ---------------------------------------------------------------------------


def test_apply_edit_plan_refuses_at_validation_same_object_same_field_collision(tmp_path: Path):
    work_dir = tmp_path / "session"
    session_id = "s-batch-collision"
    artifact_manager = _clone(tmp_path, work_dir)
    plan_params = {
        "steps": [
            {"tool": "set_vehicle_input_volume", "params": {"input_no": "1", "volume": 1000.0}},
            {"tool": "set_vehicle_input_volume", "params": {"input_no": "1", "volume": 2000.0}},
        ]
    }

    provider = FakeStructuredProvider(
        [
            _action("apply_edit_plan", summary="같은 입력을 두 번 수정합니다", **plan_params),
            _action("respond", summary="확인했습니다", answer="계획을 확인했습니다."),
        ]
    )
    result = run_agent_turn(
        "차량 입력 1을 두 단계로 나눠서 수정해줘",
        provider=provider,
        work_dir=work_dir,
        session_id=session_id,
        language="ko",
        gate=ProposeWriteGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    assert result.kind == "answer"  # refused before an approval card was ever offered
    records = read_trace(result.trace_path)
    refusal = next(r for r in records if r["kind"] == "refusal")
    assert refusal["payload"]["phase"] == "preflight"
    assert refusal["payload"]["error_type"] == "apply_edit_plan_target_collision"
    assert "1" in refusal["payload"]["message"]
    assert "2" in refusal["payload"]["message"]
    assert artifact_manager.version == 0


# ---------------------------------------------------------------------------
# (d) A plan refused at validation: combined sibling volumes exceeding
#     capacity, where each step alone passes.
# ---------------------------------------------------------------------------


def test_apply_edit_plan_refuses_at_validation_combined_sibling_volumes_exceed_capacity(tmp_path: Path):
    work_dir = tmp_path / "session"
    session_id = "s-batch-combined"
    artifact_manager = _clone(tmp_path, work_dir, extra_vehicle_input=True)

    # Link 101: 3 lanes -> rough capacity 3 * 1900 = 5700 veh/h. Inputs 1 and
    # 7 both share it and both start with no known volume -- each step,
    # checked alone against the CURRENT model, is comfortably under capacity.
    plan_params = {
        "steps": [
            {"tool": "set_vehicle_input_volume", "params": {"input_no": "1", "volume": 4000.0}},
            {"tool": "set_vehicle_input_volume", "params": {"input_no": "7", "volume": 4000.0}},
        ]
    }

    provider = FakeStructuredProvider(
        [
            _action("apply_edit_plan", summary="두 진입로 볼륨을 함께 올립니다", **plan_params),
            _action("respond", summary="확인했습니다", answer="계획을 확인했습니다."),
        ]
    )
    result = run_agent_turn(
        "링크 101의 차량 입력 두 개를 각각 4000으로 올려줘",
        provider=provider,
        work_dir=work_dir,
        session_id=session_id,
        language="ko",
        gate=ProposeWriteGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    assert result.kind == "answer"
    records = read_trace(result.trace_path)
    preflight = next(r for r in records if r["kind"] == "preflight")
    assert preflight["payload"]["blocking"] is True
    assert "apply_edit_plan_combined_effect" in preflight["payload"]["findings"]
    refusal = next(r for r in records if r["kind"] == "refusal")
    assert refusal["payload"]["error_type"] == "apply_edit_plan_combined_effect"
    assert "latent_demand_overflow" in refusal["payload"]["message"] or "latent_demand_overflow" in str(
        refusal["payload"]
    )
    assert artifact_manager.version == 0


# ---------------------------------------------------------------------------
# (e) Recursion / non-write-tool refusals.
# ---------------------------------------------------------------------------


def test_apply_edit_plan_refuses_recursion_and_non_write_tools(tmp_path: Path):
    work_dir = tmp_path / "session"
    session_id = "s-batch-recursion"
    artifact_manager = _clone(tmp_path, work_dir)
    plan_params = {
        "steps": [
            {"tool": "apply_edit_plan", "params": {"steps": []}},
            {"tool": "run_simulation", "params": {"duration_s": 60}},
            {"tool": "get_network_overview", "params": {}},
            {"tool": "rollback_last_edit", "params": {}},
        ]
    }

    provider = FakeStructuredProvider(
        [
            _action("apply_edit_plan", summary="여러 도구를 섞어 봅니다", **plan_params),
            _action("respond", summary="확인했습니다", answer="계획을 확인했습니다."),
        ]
    )
    result = run_agent_turn(
        "이상한 계획을 실행해줘",
        provider=provider,
        work_dir=work_dir,
        session_id=session_id,
        language="ko",
        gate=ProposeWriteGate(),
        engine=GuardrailEngine(),
        artifact_manager=artifact_manager,
    )

    assert result.kind == "answer"
    records = read_trace(result.trace_path)
    preflight = next(r for r in records if r["kind"] == "preflight")
    assert preflight["payload"]["blocking"] is True
    findings = preflight["payload"]["findings"]
    # Every one of the four steps is individually named as ineligible.
    assert findings.count("apply_edit_plan_step_tool_ineligible") == 4
    assert artifact_manager.version == 0


# ---------------------------------------------------------------------------
# rollback_last_edit immediately after a SUCCESSFUL batch -- the documented
# design decision: one rollback_last_edit call undoes the WHOLE batch (all 3
# steps), matching a single-step edit's own undo semantics, because
# apply_edit_plan never calls ArtifactManager.save_version() itself -- only
# the orchestrator's own generic post-dispatch call does, exactly once, for
# the whole successful batch (see tools_edit.apply_edit_plan's own module
# comment for the full reasoning).
# ---------------------------------------------------------------------------


def test_rollback_last_edit_after_a_successful_batch_undoes_the_whole_batch_in_one_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work_dir = tmp_path / "session"
    session_id = "s-batch-rollback"
    approve_manager, _ = _propose_and_approve_three_step_plan(tmp_path, work_dir, session_id)
    assert approve_manager.version == 1
    # rollback_last_edit's target_version == 0 case restores from
    # get_active_inpx_path() (the same proxy production code builds
    # ArtifactManager(source_inpx=...) from) -- patched here so it resolves
    # to THIS test's own source, matching test_agent_orchestrator_rollback.py's
    # own established pattern.
    monkeypatch.setattr(tools_edit, "get_active_inpx_path", lambda: approve_manager.source_inpx)

    # The batch genuinely landed (9 = 3 areas * 3 lanes on link 101).
    before_rollback = ET.parse(approve_manager.working_path).getroot()
    assert len(before_rollback.findall(".//reducedSpeedAreas/reducedSpeedArea")) == 9

    rollback_provider = FakeStructuredProvider(
        [
            _action("rollback_last_edit", summary="방금 편집을 되돌립니다"),
            _action("respond", summary="처리했습니다", answer="처리했습니다."),
        ]
    )
    session = _FakeWorkingInpxSession(approve_manager.working_path)
    set_active_edit_session(session)
    try:
        rollback_result = run_agent_turn(
            "방금 배치 편집을 되돌려줘",
            provider=rollback_provider,
            work_dir=work_dir,
            session_id=session_id,
            language="ko",
            gate=PreapprovedWriteGate({signature("rollback_last_edit", {})}),
            engine=GuardrailEngine(),
            artifact_manager=approve_manager,
        )
    finally:
        clear_active_edit_session()

    assert rollback_result.kind == "answer"
    # rollback_last_edit is itself a write -- a NEW version was saved (never
    # rewrites history), version 2, whose CONTENT equals the pristine
    # pre-batch state (version 0 / the source .inpx).
    assert approve_manager.version == 2
    assert approve_manager.working_path.read_bytes() == approve_manager.source_inpx.read_bytes()

    # The WHOLE batch was undone in this ONE call -- every reduced-speed
    # area the batch added is genuinely gone, not just the last step's.
    after_rollback = ET.parse(approve_manager.working_path).getroot()
    assert after_rollback.findall(".//reducedSpeedAreas/reducedSpeedArea") == []

    # The ledger reads this as ONE undone thing: the undone tool is
    # "apply_edit_plan" itself (the whole plan), not one of its inner steps.
    rollback_step = next(r for r in rollback_result.state.history if r.action == "rollback_last_edit")
    assert rollback_step.observation["artifacts"]["undone_tool"] == "apply_edit_plan"
    assert rollback_step.observation["artifacts"]["restored_to_version"] == 0
    assert rollback_step.observation["artifacts"]["reverted_from_version"] == 1

    # And per unit 7's own one-level-undo boundary: a SECOND consecutive
    # rollback (nothing new applied in between) is refused, not a silent
    # partial "undo one more step of the batch" -- there is no such thing.
    second_provider = FakeStructuredProvider(
        [
            _action("rollback_last_edit", summary="다시 되돌립니다"),
            _action("respond", summary="처리했습니다", answer="처리했습니다."),
        ]
    )
    set_active_edit_session(session)
    try:
        second = run_agent_turn(
            "한 번 더 되돌려줘",
            provider=second_provider,
            work_dir=work_dir,
            session_id=session_id,
            language="ko",
            gate=PreapprovedWriteGate({signature("rollback_last_edit", {})}),
            engine=GuardrailEngine(),
            artifact_manager=approve_manager,
        )
    finally:
        clear_active_edit_session()

    assert second.kind == "answer"
    second_records = read_trace(second.trace_path)
    action_end = [r for r in second_records if r["kind"] == "action_end"][-1]
    assert action_end["payload"]["status"] == "error"
    assert action_end["payload"]["error_type"] == "tool_execution_error"
    assert approve_manager.version == 2  # unchanged -- refused before dispatch could save anything

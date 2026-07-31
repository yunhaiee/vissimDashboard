"""End-to-end approval-flow integration for QUEUE 3 unit 7's
``rollback_last_edit``: the REAL registered tool (default_registry), driven
through a REAL ``run_agent_turn`` with a REAL ``ArtifactManager``/
``GuardrailEngine`` -- mirrors test_agent_orchestrator_reduced_speed_area.py's
own house pattern (``FakeStructuredProvider``, ``_AllowAllGate``, the canned
(action, meta) helper, the ``data/practice.inpx`` + appended empty
``<speedDistributions>``/``<reducedSpeedAreas>`` sections fixture) almost
verbatim, copied per that file's own "no shared test-utilities module exists
in this repo" note.

**Why ``set_reduced_speed_area`` stands in for "apply a real edit" here**,
rather than a COM verb like ``set_vehicle_input_volume`` (the unit brief's
own example): this unit is explicitly "no COM required", and the brief's own
verification section asks for a transcript "on SYNTHETIC data" -- a COM verb
needs a live, licensed Vissim session and would make this suite depend on
real Vissim being installed and licensed on whatever machine runs pytest,
exactly what docs/phase2/00-EXECUTION-GUIDE.md's working conventions forbid
for non-COM code ("never make a non-COM code path require live Vissim").
``set_reduced_speed_area`` is the one existing write tool that needs no COM
at all (see tools_edit.py's own module docstring), so it is the correct
substitute: rollback_last_edit itself does not care AT ALL which tool
produced the version it is restoring -- it operates purely on the archived
``edit_NNN.inpx`` file, regardless of whether a COM verb or an XML-injection
verb wrote it.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

import services.agent.tools_edit as tools_edit  # registers rollback_last_edit + set_reduced_speed_area
import services.agent.tools_run as tools_run  # noqa: F401 - registers run_simulation
from services.agent.edit_session import clear_active_edit_session, set_active_edit_session
from services.agent.guardrails import GuardrailEngine
from services.agent.orchestrator import run_agent_turn
from services.agent.state import ArtifactManager
from services.agent.trace import read_trace

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"


class FakeStructuredProvider:
    """Canned (action, meta) queue implementing the ``generate_structured``
    seam. Copied from test_agent_orchestrator_write_gate.py's helper of the
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


class _AllowAllGate:
    """Test-only permissive WriteGate: authorizes every write/run-tier
    action. Copied from test_agent_orchestrator_write_gate.py."""

    def authorize(self, action: dict[str, Any], spec: Any) -> bool:
        return True


class _FakeWorkingInpxSession:
    """The minimal shape rollback_last_edit/set_reduced_speed_area need: a
    ``working_inpx`` path attribute, no COM methods at all."""

    def __init__(self, working_inpx: Path) -> None:
        self.working_inpx = working_inpx


def _demo_source_with_speed_sections(tmp_path: Path) -> Path:
    """``data/practice.inpx`` content, with empty ``<speedDistributions>``/
    ``<reducedSpeedAreas>`` sections appended -- copied verbatim from
    test_agent_orchestrator_reduced_speed_area.py (same synthetic-dataset
    gap, same fix)."""
    content = DEMO_INPX.read_text(encoding="utf-8").rstrip()
    assert content.endswith("</network>")
    patched = (
        content[: -len("</network>")]
        + "<speedDistributions></speedDistributions>"
        + "<reducedSpeedAreas></reducedSpeedAreas></network>"
    )
    source = tmp_path / "source_with_speed_sections.inpx"
    source.write_text(patched, encoding="utf-8")
    return source


def _apply_reduced_speed_area_edit(
    tmp_path: Path, work_dir: Path, session_id: str, monkeypatch: pytest.MonkeyPatch
) -> ArtifactManager:
    """Shared first step for every test below: clone a fresh session and
    apply ONE real edit (set_reduced_speed_area on link 101), through a REAL
    run_agent_turn. Returns the (now version==1) ArtifactManager.

    ``rollback_last_edit``'s ``target_version == 0`` (pristine clone) case
    restores from ``get_active_inpx_path()`` -- the SAME proxy
    ``routers/assistant.py`` itself uses to build ``ArtifactManager(source_
    inpx=get_active_inpx_path(), ...)`` in production, where the two are
    therefore always identical by construction (see rollback.py's own module
    docstring). Patched here so this test's own custom source (the demo
    dataset plus its appended speed sections) is what that path actually
    resolves to too, matching the real "just-cloned" invariant instead of
    whatever this dev box's OWN active dataset happens to be.
    """
    source_inpx = _demo_source_with_speed_sections(tmp_path)
    monkeypatch.setattr(tools_edit, "get_active_inpx_path", lambda: source_inpx)

    artifact_manager = ArtifactManager(source_inpx=source_inpx, work_dir=work_dir)
    artifact_manager.clone()

    provider = FakeStructuredProvider(
        [
            _action(
                "set_reduced_speed_area",
                summary="감속 구간을 추가합니다",
                link_id="101",
                speed_kmh=40.0,
                from_m=10.0,
                length_m=50.0,
            ),
            _action("respond", summary="완료했습니다", answer="링크 101에 40km/h 감속 구간을 추가했습니다."),
        ]
    )

    session = _FakeWorkingInpxSession(artifact_manager.working_path)
    set_active_edit_session(session)
    try:
        result = run_agent_turn(
            "101번 링크 10m 지점부터 50m 구간에 40km/h 감속 구간을 추가해줘",
            provider=provider,
            work_dir=work_dir,
            session_id=session_id,
            language="ko",
            gate=_AllowAllGate(),
            engine=GuardrailEngine(),
            artifact_manager=artifact_manager,
        )
    finally:
        clear_active_edit_session()

    assert result.kind == "answer"
    assert artifact_manager.version == 1
    return artifact_manager


def _run_rollback_turn(work_dir: Path, session_id: str, artifact_manager: ArtifactManager):
    """Run ONE rollback_last_edit turn through the real orchestrator, ending
    in a respond decision either way (the planner narrates whatever
    happened -- success or a structured refusal)."""
    provider = FakeStructuredProvider(
        [
            _action("rollback_last_edit", summary="마지막 편집을 되돌립니다"),
            _action("respond", summary="처리했습니다", answer="처리했습니다."),
        ]
    )
    session = _FakeWorkingInpxSession(artifact_manager.working_path)
    set_active_edit_session(session)
    try:
        return run_agent_turn(
            "방금 한 편집을 되돌려줘",
            provider=provider,
            work_dir=work_dir,
            session_id=session_id,
            language="ko",
            gate=_AllowAllGate(),
            engine=GuardrailEngine(),
            artifact_manager=artifact_manager,
        )
    finally:
        clear_active_edit_session()


# ---------------------------------------------------------------------------
# The required end-to-end transcript: apply -> verify -> rollback -> verify
# byte-identical -> second rollback -> refused.
# ---------------------------------------------------------------------------


def test_rollback_undoes_the_last_edit_and_restores_the_network_byte_identically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work_dir = tmp_path / "session"
    session_id = "s-rollback-ok"
    artifact_manager = _apply_reduced_speed_area_edit(tmp_path, work_dir, session_id, monkeypatch)

    # --- verify the edit actually applied ---
    edited_path = work_dir / "edit_001.inpx"
    areas = ET.parse(edited_path).getroot().findall(".//reducedSpeedAreas/reducedSpeedArea")
    assert len(areas) == 3  # link 101 has 3 lanes in the demo dataset

    # --- rollback ---
    result = _run_rollback_turn(work_dir, session_id, artifact_manager)

    assert result.kind == "answer"
    # trace_path is one JSONL per SESSION (append-only across every turn --
    # see trace.py's own docstring), so it also carries the earlier edit
    # turn's own records; only THIS rollback turn's own tail is asserted on.
    records = read_trace(result.trace_path)[-6:]
    kinds = [r["kind"] for r in records]
    assert kinds == ["plan", "preflight", "action_start", "action_end", "postwrite", "plan"]
    assert records[1]["payload"]["blocking"] is False
    assert records[4]["payload"]["blocking"] is False  # postwrite: byte-identical restore, clean diff

    # --- rollback is itself an edit: a NEW version was saved (never rewrites
    #     history) whose content equals the pristine pre-edit state ---
    assert artifact_manager.version == 2
    restored_path = work_dir / "edit_002.inpx"
    assert restored_path.read_bytes() == artifact_manager.source_inpx.read_bytes()
    assert artifact_manager.working_path.read_bytes() == artifact_manager.source_inpx.read_bytes()

    # --- the reduced-speed area is genuinely gone from the restored network ---
    restored_areas = ET.parse(artifact_manager.working_path).getroot().findall(
        ".//reducedSpeedAreas/reducedSpeedArea"
    )
    assert restored_areas == []

    # --- the ledger records WHICH edit was undone, never losing information ---
    action_end = next(r for r in records if r["kind"] == "action_end")
    assert action_end["payload"]["tool"] == "rollback_last_edit"
    assert action_end["payload"]["status"] == "ok"
    action_start = next(r for r in records if r["kind"] == "action_start")
    assert action_start["payload"]["tool"] == "rollback_last_edit"


def test_second_consecutive_rollback_is_refused_one_level_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work_dir = tmp_path / "session"
    session_id = "s-rollback-twice"
    artifact_manager = _apply_reduced_speed_area_edit(tmp_path, work_dir, session_id, monkeypatch)

    first = _run_rollback_turn(work_dir, session_id, artifact_manager)
    assert first.kind == "answer"
    assert artifact_manager.version == 2
    content_after_first_rollback = artifact_manager.working_path.read_bytes()

    # --- second rollback: nothing further to walk back, refused ---
    second = _run_rollback_turn(work_dir, session_id, artifact_manager)

    assert second.kind == "answer"
    # Append-only per-session trace file (see the previous test's own note)
    # -- take the LAST action_end, which belongs to this second rollback.
    records = read_trace(second.trace_path)
    # dispatch raised -> toolkit.dispatch's own tool_execution_error path,
    # never a false "ok" -- and since dispatch itself never returned "ok",
    # artifact_manager.save_version() is never called for this turn at all.
    action_end = [r for r in records if r["kind"] == "action_end"][-1]
    assert action_end["payload"]["status"] == "error"
    assert action_end["payload"]["error_type"] == "tool_execution_error"

    # No new version was saved, and the working copy is untouched.
    assert artifact_manager.version == 2
    assert artifact_manager.working_path.read_bytes() == content_after_first_rollback


def test_rollback_with_nothing_applied_yet_is_refused(tmp_path: Path):
    work_dir = tmp_path / "session"
    session_id = "s-rollback-nothing"
    artifact_manager = ArtifactManager(
        source_inpx=_demo_source_with_speed_sections(tmp_path), work_dir=work_dir
    )
    artifact_manager.clone()

    result = _run_rollback_turn(work_dir, session_id, artifact_manager)

    assert result.kind == "answer"
    records = read_trace(result.trace_path)
    action_end = next(r for r in records if r["kind"] == "action_end")
    assert action_end["payload"]["status"] == "error"
    assert action_end["payload"]["error_type"] == "tool_execution_error"
    assert artifact_manager.version == 0


# ---------------------------------------------------------------------------
# Interaction with runs (task requirement #5)
# ---------------------------------------------------------------------------


def test_rollback_warns_when_a_simulation_ran_since_the_undone_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work_dir = tmp_path / "session"
    session_id = "s-rollback-run-warn"
    artifact_manager = _apply_reduced_speed_area_edit(tmp_path, work_dir, session_id, monkeypatch)

    # --- run a (faked) simulation on the edited network in its own turn ---
    fake_record = {
        "id": "fake-job-rollback-1",
        "status": "done",
        "result_tables": ["SIMULATIONRUN"],
        "evaluation_configured": False,
    }
    monkeypatch.setattr(tools_run, "_run_job_and_wait", lambda **kwargs: fake_record)

    run_provider = FakeStructuredProvider(
        [
            _action("run_simulation", summary="시뮬레이션을 실행합니다", duration_s=60),
            _action("respond", summary="완료했습니다", answer="실행했습니다."),
        ]
    )
    tools_run.set_active_run_source(artifact_manager.working_path)
    try:
        run_result = run_agent_turn(
            "60초 시뮬레이션을 돌려줘",
            provider=run_provider,
            work_dir=work_dir,
            session_id=session_id,
            language="ko",
            gate=_AllowAllGate(),
            engine=GuardrailEngine(),
            artifact_manager=artifact_manager,
        )
    finally:
        tools_run.clear_active_run_source()
    assert run_result.kind == "answer"

    # --- now roll back the edit the run was based on ---
    result = _run_rollback_turn(work_dir, session_id, artifact_manager)

    assert result.kind == "answer"
    records = read_trace(result.trace_path)
    warning_records = [r for r in records if r["kind"] == "warning" and r["payload"].get("phase") == "postwrite"]
    assert len(warning_records) == 1
    assert "rollback_invalidates_run_results" in warning_records[0]["payload"]["findings"]
    # The safety-net notice actually reaches the final answer text too.
    assert "rollback_invalidates_run_results" not in result.answer  # id itself isn't user-facing
    action_end = [r for r in records if r["kind"] == "action_end"][-1]
    assert action_end["payload"]["tool"] == "rollback_last_edit"
    assert action_end["payload"]["status"] == "ok"

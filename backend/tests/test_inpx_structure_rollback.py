"""v2b unit (1), spine component 4's second half: proves ``rollback_last_
edit`` (the REAL registered tool, driven through a REAL ``run_agent_turn``)
correctly undoes a STRUCTURAL edit -- an object genuinely ADDED BACK
(``link_lanes`` gaining an entry), not merely a field-level value restored.

Mirrors ``test_agent_orchestrator_rollback.py``'s own house pattern
(``FakeStructuredProvider``, ``_AllowAllGate``, ``_FakeWorkingInpxSession``)
almost verbatim -- copied per that file's own "no shared test-utilities
module exists in this repo" note -- with ONE structural difference: the
edit being rolled back is applied directly via
``services.inpx_structure.remove_last_lane`` (not a registered tool; see
that module's own docstring for why the structural proof pair is not
planner-reachable in this unit) rather than through the planner. Only the
ROLLBACK step goes through ``run_agent_turn`` -- exactly what production
does: ``rollback_last_edit`` does not care AT ALL which mechanism produced
the version it is restoring, only that the archived ``edit_NNN.inpx`` bytes
exist (see ``rollback.py``'s own module docstring, "does not care which
tool produced a given version").

``data/practice.inpx``'s own link 103 (2 lanes, confirmed unreferenced by
either of the demo's two connectors -- see ``test_inpx_structure.py``'s
``test_find_id_references_empty_for_unreferenced_link``-style checks) is
used directly, no custom fixture needed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import services.agent.tools_edit as tools_edit  # noqa: F401 - registers rollback_last_edit
from services import inpx_structure as struct
from services.agent.edit_session import clear_active_edit_session, set_active_edit_session
from services.agent.guardrails import GuardrailEngine
from services.agent.orchestrator import run_agent_turn
from services.agent.state import ArtifactManager
from services.agent.tools_network import build_shadow_model
from services.agent.trace import read_trace

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"


class FakeStructuredProvider:
    def __init__(self, queue: list[tuple[dict[str, Any] | None, dict[str, Any]]]) -> None:
        self._queue = list(queue)

    def generate_structured(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if not self._queue:
            raise AssertionError("FakeStructuredProvider queue exhausted unexpectedly")
        return self._queue.pop(0)


def _action(tool_name: str, *, summary: str = "확인합니다", **params: Any):
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "fake", "fallback_rung": 0}


class _AllowAllGate:
    def authorize(self, action: dict[str, Any], spec: Any) -> bool:
        return True


class _FakeWorkingInpxSession:
    def __init__(self, working_inpx: Path) -> None:
        self.working_inpx = working_inpx


def test_rollback_restores_a_structurally_removed_lane_byte_identically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work_dir = tmp_path / "session"
    session_id = "s-structural-rollback"

    monkeypatch.setattr(tools_edit, "get_active_inpx_path", lambda: DEMO_INPX)

    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()

    before_shadow = build_shadow_model(artifact_manager.working_path)
    before_link = next(l for l in before_shadow["links"] if l["id"] == "103")
    assert before_link["lanes"] == 2  # ground truth, pinned by test_agent_read_tools.py too

    # --- the structural edit itself: NOT a registered tool (see the
    #     spine module's own docstring) -- applied directly to the working
    #     copy, then snapshotted exactly like any other successful write. ---
    result = struct.remove_last_lane(artifact_manager.working_path, "103", run_load_check=False)
    assert result["metadata"]["removed_lane_index"] == 2
    artifact_manager.save_version()
    assert artifact_manager.version == 1

    after_removal_shadow = build_shadow_model(artifact_manager.working_path)
    removed_link = next(l for l in after_removal_shadow["links"] if l["id"] == "103")
    assert removed_link["lanes"] == 1
    assert {e["id"] for e in after_removal_shadow["link_lanes"] if e["link_id"] == "103"} == {"103.1"}

    # --- rollback, through the REAL orchestrator + REAL rollback_last_edit ---
    provider = FakeStructuredProvider(
        [
            _action("rollback_last_edit", summary="마지막 편집을 되돌립니다"),
            _action("respond", summary="처리했습니다", answer="처리했습니다."),
        ]
    )
    session = _FakeWorkingInpxSession(artifact_manager.working_path)
    set_active_edit_session(session)
    try:
        turn_result = run_agent_turn(
            "방금 지운 차로를 되돌려줘",
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

    assert turn_result.kind == "answer"

    records = read_trace(turn_result.trace_path)[-6:]
    kinds = [r["kind"] for r in records]
    assert kinds == ["plan", "preflight", "action_start", "action_end", "postwrite", "plan"]
    assert records[1]["payload"]["blocking"] is False
    # THE key assertion this test exists for: postwrite's own diff-driven
    # dynamic exemption (built from rollback_last_edit's own reverted_
    # targets) must recognize the STRUCTURALLY RE-ADDED lane as expected,
    # not "something else moved" -- proving the general diff/exemption
    # machinery (guardrails._SHADOW_OBJECT_ID_FIELDS["link_lanes"], added
    # this unit) generalizes correctly to a structural add, not just
    # field-level value restores.
    assert records[4]["payload"]["blocking"] is False
    assert records[4]["payload"]["findings"] == []

    # --- byte-identical restoration, the tool's own core promise ---
    assert artifact_manager.version == 2
    assert artifact_manager.working_path.read_bytes() == DEMO_INPX.read_bytes()

    restored_shadow = build_shadow_model(artifact_manager.working_path)
    restored_link = next(l for l in restored_shadow["links"] if l["id"] == "103")
    assert restored_link["lanes"] == 2
    assert {e["id"] for e in restored_shadow["link_lanes"] if e["link_id"] == "103"} == {
        "103.1", "103.2",
    }


def test_rollback_last_edit_reverted_targets_expect_added_for_the_restored_lane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Lower-level companion: inspects the tool's own returned envelope
    (not just the trace) to confirm ``verification.reverted_targets``
    genuinely carries ``expect_added=True`` for the restored lane -- the
    exact mechanism ``GuardrailEngine.postwrite`` reads (see guardrails.py's
    own ``postwrite`` docstring, "QUEUE 3 unit 7: rollback_last_edit's own
    signal")."""
    from services.agent.toolkit import default_registry, dispatch

    work_dir = tmp_path / "session"
    monkeypatch.setattr(tools_edit, "get_active_inpx_path", lambda: DEMO_INPX)

    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()
    struct.remove_last_lane(artifact_manager.working_path, "103", run_load_check=False)
    artifact_manager.save_version()

    session = _FakeWorkingInpxSession(artifact_manager.working_path)
    set_active_edit_session(session)
    try:
        envelope = dispatch("rollback_last_edit", {}, registry=default_registry)
    finally:
        clear_active_edit_session()

    assert envelope["status"] == "ok"
    reverted = envelope["verification"]["reverted_targets"]
    lane_entries = [t for t in reverted if t["object_class"] == "link_lanes"]
    assert any(t["object_id"] == "103.2" and t["expect_added"] is True for t in lane_entries)

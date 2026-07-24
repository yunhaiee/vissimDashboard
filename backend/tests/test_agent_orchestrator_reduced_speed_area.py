"""Approval-flow integration for v2 unit (2)'s ``set_reduced_speed_area``:
the REAL registered tool (default_registry -- unlike
test_agent_orchestrator_write_gate.py's fake ``set_vehicle_input_volume`` in
an isolated registry, this tool needs no COM double at all) driven through a
REAL ``run_agent_turn`` with a REAL ``ArtifactManager``/``GuardrailEngine``,
reusing the write-gate test file's idioms (``FakeStructuredProvider``,
``_AllowAllGate``, canned (action, meta) helper -- copied per that file's own
"no shared test-utilities module exists in this repo" note).

The one twist versus that file: ``data/practice.inpx`` (the demo dataset)
carries no ``<speedDistributions>``/``<reducedSpeedAreas>`` sections at all
(a real, tracked gap -- see the M2.3 report's drafted DECISIONS.md entry), so
these tests clone the demo dataset's content with those two empty sections
appended, rather than pointing ``ArtifactManager`` at ``data/practice.inpx``
directly.

Also unlike that file: this tool needs an *active edit session* object for
its ``.working_inpx`` attribute (see ``tools_edit.py``'s module docstring),
so these tests call ``set_active_edit_session``/``clear_active_edit_session``
around ``run_agent_turn`` themselves -- exactly the idiom
``backend/tools/edit_tool_live_check.py``'s Phase B already uses for the
real COM tools, just with a COM-free fake session here.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import services.agent.tools_edit  # noqa: F401 - import registers set_reduced_speed_area
from services.agent.edit_session import clear_active_edit_session, set_active_edit_session
from services.agent.guardrails import GuardrailEngine
from services.agent.orchestrator import run_agent_turn
from services.agent.state import ArtifactManager
from services.agent.trace import read_trace

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"


class FakeStructuredProvider:
    """Canned (action, meta) queue implementing the ``generate_structured`` seam.

    Copied from test_agent_orchestrator_write_gate.py's helper of the same
    name (no shared test-utilities module exists in this repo -- see
    conftest.py).
    """

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
    """Test-only permissive WriteGate: authorizes every write-tier action.

    Copied from test_agent_orchestrator_write_gate.py (same "no shared
    test-utilities module" note).
    """

    def authorize(self, action: dict[str, Any], spec: Any) -> bool:
        return True


class _FakeWorkingInpxSession:
    """The minimal shape set_reduced_speed_area needs: a ``working_inpx``
    path attribute, no COM methods at all. Same class used in
    test_agent_tools_edit.py -- duplicated here rather than shared, per this
    repo's existing "no shared test-utilities module" convention."""

    def __init__(self, working_inpx: Path) -> None:
        self.working_inpx = working_inpx


def _demo_source_with_speed_sections(tmp_path: Path) -> Path:
    """``data/practice.inpx`` content, with empty ``<speedDistributions>``/
    ``<reducedSpeedAreas>`` sections appended -- what a real Vissim-exported
    .inpx always carries (see the real Air Taxi Vertiport example probed in
    the M2.3 report) but the synthetic demo dataset currently does not."""
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


# ---------------------------------------------------------------------------
# Clean write: propose -> (pre-approved gate) -> dispatch -> postwrite clean
# -> saved version -> reparse confirms the element is really on disk
# ---------------------------------------------------------------------------


def test_reduced_speed_area_write_lands_cleanly_through_a_real_agent_turn(tmp_path: Path):
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(
        source_inpx=_demo_source_with_speed_sections(tmp_path), work_dir=work_dir
    )
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
            _action(
                "respond",
                summary="완료했습니다",
                answer="링크 101에 40km/h 감속 구간을 추가했습니다.",
            ),
        ]
    )

    session = _FakeWorkingInpxSession(artifact_manager.working_path)
    set_active_edit_session(session)
    try:
        result = run_agent_turn(
            "101번 링크 10m 지점부터 50m 구간에 40km/h 감속 구간을 추가해줘",
            provider=provider,
            work_dir=work_dir,
            session_id="s-reduced-speed-ok",
            language="ko",
            gate=_AllowAllGate(),
            engine=GuardrailEngine(),
            artifact_manager=artifact_manager,
        )
    finally:
        clear_active_edit_session()

    assert result.kind == "answer"
    assert "40" in result.answer

    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records] == [
        "plan",
        "preflight",
        "action_start",
        "action_end",
        "postwrite",
        "plan",
    ]
    assert records[1]["payload"]["blocking"] is False  # preflight: link 101 exists
    assert records[4]["payload"]["blocking"] is False  # postwrite: reparse readback matched

    # the write actually advanced the version counter and persisted for real.
    assert artifact_manager.version == 1
    saved_path = work_dir / "edit_001.inpx"
    root = ET.parse(saved_path).getroot()
    areas = root.findall(".//reducedSpeedAreas/reducedSpeedArea")
    assert len(areas) == 3  # link 101 has 3 lanes in the demo dataset
    assert all(a.get("pos") == "10.000000000" for a in areas)
    assert all(a.get("length") == "50.000000000" for a in areas)


# ---------------------------------------------------------------------------
# Unknown link -> preflight refusal, dispatch never happens (object_not_found)
# ---------------------------------------------------------------------------


def test_reduced_speed_area_unknown_link_refuses_at_preflight_never_dispatches(tmp_path: Path):
    work_dir = tmp_path / "session"
    artifact_manager = ArtifactManager(
        source_inpx=_demo_source_with_speed_sections(tmp_path), work_dir=work_dir
    )
    artifact_manager.clone()

    provider = FakeStructuredProvider(
        [
            _action(
                "set_reduced_speed_area",
                summary="감속 구간을 추가합니다",
                link_id="999-bogus",
                speed_kmh=40.0,
                from_m=0.0,
                length_m=20.0,
            ),
            _action(
                "respond",
                summary="대상을 찾지 못했습니다",
                answer="999-bogus 링크를 찾을 수 없습니다.",
            ),
        ]
    )

    session = _FakeWorkingInpxSession(artifact_manager.working_path)
    set_active_edit_session(session)
    try:
        result = run_agent_turn(
            "999-bogus 링크에 40km/h 감속 구간을 추가해줘",
            provider=provider,
            work_dir=work_dir,
            session_id="s-reduced-speed-refuse",
            language="ko",
            gate=_AllowAllGate(),
            engine=GuardrailEngine(),
            artifact_manager=artifact_manager,
        )
    finally:
        clear_active_edit_session()

    assert artifact_manager.version == 0  # nothing was ever saved

    records = read_trace(result.trace_path)
    assert [r["kind"] for r in records] == ["plan", "preflight", "refusal", "plan"]
    assert records[1]["payload"]["blocking"] is True
    assert records[2]["payload"]["error_type"] == "object_not_found"
    assert result.kind == "answer"

    root = ET.parse(artifact_manager.working_path).getroot()
    assert root.findall(".//reducedSpeedAreas/reducedSpeedArea") == []

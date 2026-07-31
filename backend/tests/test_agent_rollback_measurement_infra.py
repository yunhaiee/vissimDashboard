"""Q4 unit (2)'s own rollback proof: ``rollback_last_edit`` (the REAL
registered tool, driven through a REAL ``run_agent_turn``) correctly undoes
an ``add_queue_counter`` edit -- byte-for-byte restoration of the working
copy, proven the same way ``test_inpx_structure_rollback.py`` proved it for
a structurally-removed lane. Mirrors that file's house pattern
(``FakeStructuredProvider``, ``_AllowAllGate``) almost verbatim, per its own
"no shared test-utilities module exists in this repo" note.

**One deliberate difference from the lane-closure precedent, stated
honestly:** that test's own headline assertion was that
``guardrails._SHADOW_OBJECT_ID_FIELDS["link_lanes"]`` lets the postwrite
diff POSITIVELY recognize the re-added lane (``reverted_targets`` names it,
``postwrite`` findings stay empty because of that explicit recognition, not
mere silence). This unit's ``queue_counters`` (and its three sibling object
classes) are deliberately NOT added to that table -- see
``guardrails._diff_object_list``'s own docstring: "Removals are NEVER
suppressed by [suppress_added] -- an unexpectedly vanished object stays
evidence regardless," and there is no per-tool "this tool legitimately
removes objects" mechanism at all (only rollback's own dynamic, id-specific
``exempt_removed_ids``, itself only ever consulted for object classes the
outer diff loop visits). Registering ``queue_counters`` there would make
this unit's OWN ``remove_queue_counter``/etc. tools' every ordinary,
successful removal register as unexplained "nothing else moved" evidence
against constraint #6 -- exactly the false-positive trap
``set_reduced_speed_area`` (v2 unit (2)) already avoids by staying out of
that same table. So: ``reverted_targets`` for a rolled-back
``add_queue_counter`` legitimately comes back WITHOUT a ``queue_counters``
entry (the diff never examines that object class at all -- neither as
evidence nor as something needing exemption), and this test proves the
byte-identical restoration + a clean (non-blocking) postwrite the same way
``set_reduced_speed_area``'s OWN verification story already does: a fresh
reparse of the restored file, not the generic object-list diff.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

import services.agent.tools_edit as tools_edit  # noqa: F401 - registers add_queue_counter/rollback_last_edit
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


class _FakeQueueCounterSession:
    """Duck-types VissimEditSession's ``add_queue_counter``/``persist``
    surface only (this file never rolls back a REMOVE) -- unlike
    ``FakeMeasurementInfraSession`` in test_agent_tools_measurement_infra.py
    (which rigs a return dict without touching the file), THIS fake's
    ``add_queue_counter`` genuinely appends a ``<queueCounter>`` element to
    ``working_inpx`` -- a rollback proof needs the file to ACTUALLY change,
    the same way a real COM ``AddQueueCounter`` + ``SaveNetAs`` would."""

    def __init__(self, working_inpx: Path) -> None:
        self.working_inpx = working_inpx

    def add_queue_counter(self, link_no: int, pos_m: float, name: str = "") -> dict[str, Any]:
        tree = ET.parse(self.working_inpx)
        root = tree.getroot()
        container = root.find("queueCounters")
        if container is None:
            container = ET.SubElement(root, "queueCounters")
        new_no = str(max((int(el.get("no", "0")) for el in container.findall("queueCounter")), default=0) + 1)
        ET.SubElement(
            container, "queueCounter",
            {"no": new_no, "link": str(link_no), "pos": f"{float(pos_m):.6f}", "name": name},
        )
        tree.write(self.working_inpx, encoding="UTF-8", xml_declaration=True)
        return {"no": int(new_no), "name": name, "link": str(link_no), "pos": float(pos_m)}

    def persist(self) -> None:
        # The write already reached working_inpx directly above (mirrors
        # set_reduced_speed_area's own "no COM session, the file write IS
        # the persist" shape) -- nothing further to do.
        pass


def test_rollback_restores_an_added_queue_counter_byte_identically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    work_dir = tmp_path / "session"
    session_id = "s-queue-counter-rollback"

    monkeypatch.setattr(tools_edit, "get_active_inpx_path", lambda: DEMO_INPX)

    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()

    before_shadow = build_shadow_model(artifact_manager.working_path)
    assert before_shadow["queue_counters"] == []  # ground truth: the demo .inpx has none

    # --- the edit itself, through the REAL orchestrator + REAL add_queue_counter ---
    edit_session = _FakeQueueCounterSession(artifact_manager.working_path)
    set_active_edit_session(edit_session)
    try:
        edit_provider = FakeStructuredProvider(
            [
                _action("add_queue_counter", summary="대기행렬 카운터를 추가합니다", link_no="101", pos_m=50.0),
                _action("respond", summary="처리했습니다", answer="처리했습니다."),
            ]
        )
        edit_result = run_agent_turn(
            "101번 링크에 대기행렬 카운터를 추가해줘",
            provider=edit_provider,
            work_dir=work_dir,
            session_id=session_id,
            language="ko",
            gate=_AllowAllGate(),
            engine=GuardrailEngine(),
            artifact_manager=artifact_manager,
        )
    finally:
        clear_active_edit_session()

    assert edit_result.kind == "answer"
    assert artifact_manager.version == 1

    after_add_shadow = build_shadow_model(artifact_manager.working_path)
    assert len(after_add_shadow["queue_counters"]) == 1
    assert after_add_shadow["queue_counters"][0]["link_id"] == "101"

    # --- rollback, through the REAL orchestrator + REAL rollback_last_edit ---
    rollback_session = _FakeQueueCounterSession(artifact_manager.working_path)
    set_active_edit_session(rollback_session)
    try:
        rollback_provider = FakeStructuredProvider(
            [
                _action("rollback_last_edit", summary="마지막 편집을 되돌립니다"),
                _action("respond", summary="처리했습니다", answer="처리했습니다."),
            ]
        )
        rollback_result = run_agent_turn(
            "방금 추가한 대기행렬 카운터를 되돌려줘",
            provider=rollback_provider,
            work_dir=work_dir,
            session_id=session_id,
            language="ko",
            gate=_AllowAllGate(),
            engine=GuardrailEngine(),
            artifact_manager=artifact_manager,
        )
    finally:
        clear_active_edit_session()

    assert rollback_result.kind == "answer"

    records = read_trace(rollback_result.trace_path)[-6:]
    kinds = [r["kind"] for r in records]
    assert kinds == ["plan", "preflight", "action_start", "action_end", "postwrite", "plan"]
    assert records[1]["payload"]["blocking"] is False
    # The postwrite "nothing else moved" diff never examines queue_counters
    # at all (see this file's own module docstring for why) -- so it stays
    # clean not because of a positive dynamic exemption (unlike link_lanes'
    # own precedent) but because that object class is simply outside the
    # diff's vocabulary, same as set_reduced_speed_area's des_speed_
    # distributions before v2a unit (9) added it. Still a genuine, honest
    # "nothing flagged" -- proven here, not assumed.
    assert records[4]["payload"]["blocking"] is False
    assert records[4]["payload"]["findings"] == []

    # --- byte-identical restoration, the tool's own core promise ---
    assert artifact_manager.version == 2
    assert artifact_manager.working_path.read_bytes() == DEMO_INPX.read_bytes()

    restored_shadow = build_shadow_model(artifact_manager.working_path)
    assert restored_shadow["queue_counters"] == []


def test_rollback_last_edit_reverted_targets_omits_queue_counters_by_design(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Lower-level companion, documenting the honest limitation this file's
    own module docstring explains: ``verification.reverted_targets`` does
    NOT name the queue counter (the generic diff never examines that object
    class) -- the byte-level restoration above is still complete and
    correct; only the DESCRIPTIVE reporting is narrower than link_lanes'
    own precedent."""
    from services.agent.toolkit import default_registry, dispatch

    work_dir = tmp_path / "session"
    monkeypatch.setattr(tools_edit, "get_active_inpx_path", lambda: DEMO_INPX)

    artifact_manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=work_dir)
    artifact_manager.clone()

    session = _FakeQueueCounterSession(artifact_manager.working_path)
    set_active_edit_session(session)
    try:
        add_envelope = dispatch(
            "add_queue_counter", {"link_no": "101", "pos_m": 50.0}, registry=default_registry
        )
        assert add_envelope["status"] == "ok"
        artifact_manager.save_version()

        rollback_envelope = dispatch("rollback_last_edit", {}, registry=default_registry)
    finally:
        clear_active_edit_session()

    assert rollback_envelope["status"] == "ok"
    reverted = rollback_envelope["verification"]["reverted_targets"]
    assert not any(t["object_class"] == "queue_counters" for t in reverted)
    # The restoration itself is still verifiably complete via the hash pair
    # (written_value/readback_value here are sha256 hex digests of the
    # restored file, per rollback_last_edit's own docstring -- not the
    # written_value/readback_value SHAPE every other write tool's
    # verification carries).
    assert (
        rollback_envelope["verification"]["written_value"]
        == rollback_envelope["verification"]["readback_value"]
    )
    assert artifact_manager.working_path.read_bytes() == DEMO_INPX.read_bytes()

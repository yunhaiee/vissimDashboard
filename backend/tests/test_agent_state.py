from __future__ import annotations

from pathlib import Path

import pytest

from services.agent.state import AgentState, ArtifactManager, StepRecord

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"


# ---------------------------------------------------------------------------
# StepRecord
# ---------------------------------------------------------------------------


def test_step_record_round_trips_through_dict():
    record = StepRecord(
        thought="check current signal timing",
        action="read_scenario",
        params={"scenario_id": "base"},
        observation={"cycle_len": 90},
        kpi={"delay_s": 12.3},
    )

    restored = StepRecord.from_dict(record.to_dict())

    assert restored == record


def test_step_record_kpi_defaults_to_none_when_absent():
    record = StepRecord(
        thought="inspect network",
        action="list_links",
        params={},
        observation=[1, 2, 3],
    )

    assert record.kpi is None
    restored = StepRecord.from_dict(record.to_dict())
    assert restored.kpi is None


# ---------------------------------------------------------------------------
# AgentState — JSON round trip
# ---------------------------------------------------------------------------


def test_agent_state_json_round_trip_preserves_all_fields_including_korean_text():
    state = AgentState(
        goal="신호 주기를 90초로 늘려줘",
        artifacts={"working_inpx": "sess-1/working.inpx", "version": 2},
        history=[
            StepRecord(
                thought="사용자 목표 확인",
                action="read_scenario",
                params={"scenario_id": "base"},
                observation={"cycle_len": 80},
                kpi=None,
            ),
            StepRecord(
                thought="신호 주기 변경 적용",
                action="edit_signal_timing",
                params={"cycle_len": 90},
                observation={"ok": True},
                kpi={"delay_s": 10.1},
            ),
        ],
        context={
            "dataset_id": "practice",
            "scenario_id": "base",
            "com_session_status": "disconnected",
            "pending_approval": False,
            "latest_kpis": {"delay_s": 10.1},
            "constraint_warnings": ["차로 수 변경은 승인 필요"],
        },
    )

    payload = state.to_json()
    restored = AgentState.from_json(payload)

    assert restored == state
    # ensure_ascii=False: Korean text must appear literally, not \uXXXX-escaped
    assert "신호" in payload


def test_agent_state_history_restores_step_record_instances():
    state = AgentState(
        goal="goal",
        artifacts={},
        history=[
            StepRecord(thought="t", action="a", params={}, observation="o"),
        ],
        context={},
    )

    restored = AgentState.from_json(state.to_json())

    assert isinstance(restored.history[0], StepRecord)


# ---------------------------------------------------------------------------
# ArtifactManager — clone
# ---------------------------------------------------------------------------


def test_clone_copies_source_into_work_dir_without_modifying_source(tmp_path: Path):
    original_bytes = DEMO_INPX.read_bytes()
    manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)

    manager.clone()

    assert DEMO_INPX.read_bytes() == original_bytes
    assert manager.working_path.exists()
    assert manager.working_path.read_bytes() == original_bytes


def test_clone_parses_initial_shadow_model(tmp_path: Path):
    manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)

    shadow = manager.clone()

    assert shadow is manager.shadow_model
    link_ids = {link["id"] for link in shadow["links"]}
    assert "101" in link_ids


# ---------------------------------------------------------------------------
# ArtifactManager — save_version
# ---------------------------------------------------------------------------


def _rename_link_101(working_path: Path, new_name: str) -> None:
    text = working_path.read_text(encoding="utf-8")
    text = text.replace(
        'link no="101" name="Main St EB approach"',
        f'link no="101" name="{new_name}"',
    )
    working_path.write_text(text, encoding="utf-8")


def test_save_version_creates_edit_001_then_edit_002(tmp_path: Path):
    manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    manager.clone()

    version_1 = manager.save_version()
    version_2 = manager.save_version()

    assert (tmp_path / "edit_001.inpx").exists()
    assert (tmp_path / "edit_002.inpx").exists()
    assert version_1 != version_2


def test_save_version_refreshes_shadow_model_after_mutation(tmp_path: Path):
    manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    manager.clone()

    _rename_link_101(manager.working_path, "Main St EB approach EDITED")
    manager.save_version()

    names = {link["id"]: link["name"] for link in manager.shadow_model["links"]}
    assert names["101"] == "Main St EB approach EDITED"


# ---------------------------------------------------------------------------
# ArtifactManager — rollback_to
# ---------------------------------------------------------------------------


def test_rollback_restores_byte_identical_prior_version(tmp_path: Path):
    manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    manager.clone()
    bytes_before_edit = manager.working_path.read_bytes()

    version_1 = manager.save_version()  # edit_001 == pristine clone

    _rename_link_101(manager.working_path, "Main St EB approach MUTATED")
    manager.save_version()  # edit_002 == mutated

    manager.rollback_to(version_1)

    assert manager.working_path.read_bytes() == bytes_before_edit


def test_rollback_refreshes_shadow_model(tmp_path: Path):
    manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    manager.clone()
    version_1 = manager.save_version()

    _rename_link_101(manager.working_path, "Main St EB approach MUTATED")
    manager.save_version()
    assert manager.shadow_model["links"][0]["name"] == "Main St EB approach MUTATED"

    manager.rollback_to(version_1)

    names = {link["id"]: link["name"] for link in manager.shadow_model["links"]}
    assert names["101"] == "Main St EB approach"


def test_rollback_then_save_creates_next_version_never_overwrites_history(tmp_path: Path):
    manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    manager.clone()

    version_1 = manager.save_version()  # edit_001
    _rename_link_101(manager.working_path, "second edit")
    manager.save_version()  # edit_002
    edit_002_bytes = (tmp_path / "edit_002.inpx").read_bytes()

    manager.rollback_to(version_1)
    _rename_link_101(manager.working_path, "third edit, post-rollback")
    version_3 = manager.save_version()

    assert (tmp_path / "edit_003.inpx").exists()
    assert version_3 not in (version_1,)
    # edit_002 must remain untouched — rollback + save is not a rewrite of history
    assert (tmp_path / "edit_002.inpx").read_bytes() == edit_002_bytes


def test_rollback_to_nonexistent_version_raises_clear_error(tmp_path: Path):
    manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    manager.clone()
    manager.save_version()

    with pytest.raises(Exception) as excinfo:
        manager.rollback_to(999)

    assert "999" in str(excinfo.value)


def test_new_manager_over_existing_work_dir_continues_numbering(tmp_path: Path):
    first = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    first.clone()
    first.save_version()  # edit_001

    second = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    second.clone()
    version = second.save_version()

    assert (tmp_path / "edit_002.inpx").exists()
    assert version != 1

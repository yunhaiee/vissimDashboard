from __future__ import annotations

import os
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

    # content-equal, but NOT the same object -- shadow_model is a defensive
    # deep copy on every access (see the dedicated test below).
    assert shadow == manager.shadow_model
    assert shadow is not manager.shadow_model
    link_ids = {link["id"] for link in shadow["links"]}
    assert "101" in link_ids


def test_clone_returns_extended_shadow_model_shape(tmp_path: Path):
    # M2.2 seam closure: ArtifactManager now builds its shadow model via
    # tools_network.build_shadow_model, the EXTENDED shape constraints.py
    # expects -- not the bare inpx_parser.parse_network output.
    manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)

    shadow = manager.clone()

    assert set(shadow.keys()) >= {"links", "nodes", "vehicle_inputs", "signals"}


# ---------------------------------------------------------------------------
# ArtifactManager — shadow_model defensive copying
# ---------------------------------------------------------------------------


def test_shadow_model_property_returns_independent_deep_copy_each_access(tmp_path: Path):
    manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    manager.clone()

    first = manager.shadow_model
    first["links"].append({"id": "fake", "name": "mutated", "lanes": 1, "length": 0.0})
    first["links"][0]["name"] = "mutated in place"

    second = manager.shadow_model

    assert first is not second
    assert all(link["id"] != "fake" for link in second["links"])
    assert second["links"][0]["name"] != "mutated in place"


def test_shadow_model_is_none_before_clone(tmp_path: Path):
    manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)

    assert manager.shadow_model is None


# ---------------------------------------------------------------------------
# ArtifactManager — version property
# ---------------------------------------------------------------------------


def test_version_property_starts_at_zero_and_tracks_saves(tmp_path: Path):
    manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    assert manager.version == 0

    manager.clone()
    assert manager.version == 0  # clone() alone never advances the version

    manager.save_version()
    assert manager.version == 1

    manager.save_version()
    assert manager.version == 2


def test_refresh_version_picks_up_a_save_made_by_a_different_instance(tmp_path: Path):
    """Bug 1 root cause (docs/phase2/DECISIONS.md): ``.version`` is
    snapshotted once at ``__init__`` and never automatically notices a
    version a DIFFERENT ``ArtifactManager`` instance over the SAME work_dir
    has since saved (e.g. a concurrent turn for the same session, still
    mid-write when this instance was constructed). ``refresh_version()`` is
    the caller-invoked escape hatch — routers/assistant.py's
    ``_ensure_working_copy`` calls it right after acquiring the per-session
    lock, once no turn can still be concurrently writing."""
    first = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    first.clone()

    # A second instance constructed BEFORE `first` saves anything -- this is
    # the exact "constructed synchronously in the request handler while an
    # earlier turn is still mid-write" moment.
    stale = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    assert stale.version == 0

    first.save_version()
    assert first.version == 1

    # `stale.version` does NOT update on its own...
    assert stale.version == 0
    # ...until refresh_version() re-scans the work dir.
    assert stale.refresh_version() == 1
    assert stale.version == 1


# ---------------------------------------------------------------------------
# ArtifactManager — clone-precondition guard (docs/phase2/DECISIONS.md,
# M2.2 deferred finding: "decide on shadow_model defensive copying" AND the
# clone-precondition-guard test)
# ---------------------------------------------------------------------------


def test_clone_raises_when_existing_working_copy_has_no_saved_version(tmp_path: Path):
    first = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    first.clone()  # working.inpx now exists; save_version() never called

    second = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    with pytest.raises(RuntimeError, match="working copy"):
        second.clone()

    # the original (unsaved) working copy must survive the failed clone untouched
    assert first.working_path.read_bytes() == DEMO_INPX.read_bytes()


def test_clone_raises_when_working_copy_diverged_from_latest_saved_version(tmp_path: Path):
    first = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    first.clone()
    first.save_version()  # edit_001 == pristine clone
    _rename_link_101(first.working_path, "unsaved in-flight edit")  # diverge, never saved

    diverged_bytes = first.working_path.read_bytes()

    second = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    with pytest.raises(RuntimeError, match="diverged"):
        second.clone()

    # the unsaved, diverged working copy must survive the failed clone
    # attempt byte-identically untouched (check the file directly -- `first`
    # never reparsed after the out-of-band write, so `first.shadow_model`
    # would only prove its own in-memory cache is stale, not what matters
    # here: that `second.clone()` did not touch the file on disk).
    assert first.working_path.read_bytes() == diverged_bytes


def test_clone_allowed_when_working_copy_matches_latest_saved_version(tmp_path: Path):
    # The "resume an existing session" case: a working copy that is still
    # byte-identical to its latest saved version is NOT "unsaved work" and
    # re-cloning over it must not raise (this is exactly what
    # test_new_manager_over_existing_work_dir_continues_numbering below
    # already exercises end-to-end; this test isolates the guard itself).
    first = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    first.clone()
    first.save_version()

    second = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    second.clone()  # must not raise

    assert second.working_path.read_bytes() == DEMO_INPX.read_bytes()


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


def test_shadow_model_refresh_survives_colliding_mtime_ticks(tmp_path: Path):
    # Deterministic regression test for build_shadow_model's mtime-keyed
    # lru_cache (tools_network.py): two back-to-back writes to the same path
    # have been observed to land on the SAME mtime on this filesystem, which
    # would otherwise make this reparse silently return the STALE cached
    # parse from before the mutation. Force that exact collision explicitly
    # (os.utime) rather than relying on timing luck to reproduce it.
    manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    manager.clone()
    frozen_mtime = manager.working_path.stat().st_mtime

    _rename_link_101(manager.working_path, "collided mtime edit")
    os.utime(manager.working_path, (frozen_mtime, frozen_mtime))  # force the collision
    assert manager.working_path.stat().st_mtime == frozen_mtime

    manager.save_version()

    names = {link["id"]: link["name"] for link in manager.shadow_model["links"]}
    assert names["101"] == "collided mtime edit"


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


def test_rollback_to_zero_restores_pristine_clone_even_without_any_saved_version(
    tmp_path: Path,
):
    # version=0 is special-cased: it restores from source_inpx directly,
    # since clone() never creates a numbered edit_000.inpx snapshot. This is
    # what lets the orchestrator roll back a session's very FIRST write
    # (before any save_version() has run) back to the untouched network.
    manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    manager.clone()
    pristine_bytes = manager.working_path.read_bytes()
    assert manager.version == 0  # no save_version() call yet

    _rename_link_101(manager.working_path, "unsaved mutation, never saved")

    manager.rollback_to(0)

    assert manager.working_path.read_bytes() == pristine_bytes
    names = {link["id"]: link["name"] for link in manager.shadow_model["links"]}
    assert names["101"] == "Main St EB approach"


def test_rollback_to_zero_before_clone_raises_clear_error(tmp_path: Path):
    manager = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)

    with pytest.raises(RuntimeError, match="clone"):
        manager.rollback_to(0)


def test_new_manager_over_existing_work_dir_continues_numbering(tmp_path: Path):
    first = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    first.clone()
    first.save_version()  # edit_001

    second = ArtifactManager(source_inpx=DEMO_INPX, work_dir=tmp_path)
    second.clone()
    version = second.save_version()

    assert (tmp_path / "edit_002.inpx").exists()
    assert version != 1

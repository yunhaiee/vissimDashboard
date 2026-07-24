"""Tests for M2.3's edit_session.py: the active-session module-global
context, and the parts of VissimEditSession that are safely exercisable
WITHOUT live Vissim COM (construction has no side effects; any verb raises
FileNotFoundError before ever touching COM when the working copy does not
exist yet; close() is a safe no-op on a session that never connected).

The REAL COM path (connect/set/get/persist against a live Vissim instance)
is verified by backend/tools/edit_live_probe.py and
backend/tools/edit_tool_live_check.py (both live-only, not run in pytest);
this file's job is the pure-Python contract pytest can actually check on
every machine, with or without a Vissim install.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from services.agent.edit_session import (
    MAX_CONCURRENT_EDIT_SESSIONS,
    VissimEditSession,
    clear_active_edit_session,
    get_active_edit_session,
    set_active_edit_session,
)


@pytest.fixture(autouse=True)
def _reset_active_edit_session():
    """Every test starts and ends with no active session, so a leaked
    module-global from one test can never leak into the next (same
    discipline as tools_network.py's own _reset_active_inpx fixture)."""
    clear_active_edit_session()
    yield
    clear_active_edit_session()


# ---------------------------------------------------------------------------
# Active-session context
# ---------------------------------------------------------------------------


def test_get_active_edit_session_defaults_to_none():
    assert get_active_edit_session() is None


def test_set_then_get_returns_the_same_object(tmp_path: Path):
    session = VissimEditSession(tmp_path / "working.inpx")

    set_active_edit_session(session)

    assert get_active_edit_session() is session


def test_clear_active_edit_session_resets_to_none(tmp_path: Path):
    set_active_edit_session(VissimEditSession(tmp_path / "working.inpx"))

    clear_active_edit_session()

    assert get_active_edit_session() is None


def test_set_active_edit_session_with_none_is_equivalent_to_clear(tmp_path: Path):
    set_active_edit_session(VissimEditSession(tmp_path / "working.inpx"))

    set_active_edit_session(None)

    assert get_active_edit_session() is None


def test_setting_a_new_session_replaces_the_previous_one(tmp_path: Path):
    first = VissimEditSession(tmp_path / "a.inpx")
    second = VissimEditSession(tmp_path / "b.inpx")

    set_active_edit_session(first)
    set_active_edit_session(second)

    assert get_active_edit_session() is second


# ---------------------------------------------------------------------------
# VissimEditSession — the pure-Python contract (no live COM required)
# ---------------------------------------------------------------------------


def test_construction_has_no_side_effects(tmp_path: Path):
    # No working.inpx exists at all -- constructing must not touch the
    # filesystem or COM (connection is lazy, "on first use" per the
    # module's own docstring).
    session = VissimEditSession(tmp_path / "does_not_exist.inpx")

    assert session.working_inpx == tmp_path / "does_not_exist.inpx"


def test_verb_call_raises_file_not_found_before_touching_com_when_working_copy_missing(
    tmp_path: Path,
):
    session = VissimEditSession(tmp_path / "missing.inpx")

    with pytest.raises(FileNotFoundError, match="missing.inpx"):
        session.get_vehicle_input_volume(1)


def test_conflict_area_verb_call_raises_file_not_found_before_touching_com_when_working_copy_missing(
    tmp_path: Path,
):
    # v2 unit (4): the newest paired verb gets the same pre-connect guard as
    # every other one -- a representative spot-check, not exhaustive
    # per-verb coverage (the test above already establishes the pattern).
    session = VissimEditSession(tmp_path / "missing.inpx")

    with pytest.raises(FileNotFoundError, match="missing.inpx"):
        session.get_conflict_area(3)


def test_persist_raises_file_not_found_before_touching_com_when_working_copy_missing(
    tmp_path: Path,
):
    session = VissimEditSession(tmp_path / "missing.inpx")

    with pytest.raises(FileNotFoundError):
        session.persist()


def test_close_on_a_never_connected_session_is_a_safe_no_op(tmp_path: Path):
    session = VissimEditSession(tmp_path / "missing.inpx")

    session.close()  # must not raise
    session.close()  # idempotent -- calling twice is also safe


def test_max_concurrent_edit_sessions_is_two():
    # docs/phase2/DECISIONS.md-style landmine pin: Vissim allows ~4
    # instances/machine and M2.4's runs need instances too, so edit
    # sessions get a conservative slice, not the whole budget.
    assert MAX_CONCURRENT_EDIT_SESSIONS == 2

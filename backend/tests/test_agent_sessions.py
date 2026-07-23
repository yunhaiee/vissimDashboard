"""Tests for the M2-ops startup sweep (docs/phase2/DECISIONS.md, "M2 ops"
deferred finding): ``runtime/agent_sessions`` had no cleanup/TTL.

All tests exercise ``sweep_stale_sessions`` directly against a ``tmp_path``
root -- no server, no real ``runtime/agent_sessions`` -- so they never touch
the repo's actual agent-session data.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import pytest

from services.agent.sessions import DEFAULT_TTL_HOURS, sweep_stale_sessions


def _touch(path: Path, *, content: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _age(path: Path, hours: float) -> None:
    """Back-date a file's (or directory's) mtime/atime by ``hours``."""
    old = time.time() - hours * 3600.0
    os.utime(path, (old, old))


def _make_stale_session(root: Path, name: str, *, age_hours: float) -> Path:
    """A session dir with one content file whose mtime is ``age_hours`` old."""
    session_dir = root / name
    file_path = session_dir / f"trace_{name}.jsonl"
    _touch(file_path)
    _age(file_path, age_hours)
    return session_dir


def _make_fresh_session(root: Path, name: str) -> Path:
    """A session dir with one content file at the current (fresh) mtime."""
    session_dir = root / name
    _touch(session_dir / f"trace_{name}.jsonl")
    return session_dir


@pytest.fixture(autouse=True)
def _clean_ttl_env(monkeypatch):
    """Every test starts with no ambient AGENT_SESSION_TTL_HOURS, so the
    default-TTL tests aren't at the mercy of the environment the suite
    happens to run in. Tests that need an override set it explicitly."""
    monkeypatch.delenv("AGENT_SESSION_TTL_HOURS", raising=False)


# ---------------------------------------------------------------------------
# Core sweep behavior
# ---------------------------------------------------------------------------


def test_stale_dir_removed_while_fresh_dir_kept(tmp_path: Path):
    root = tmp_path / "agent_sessions"
    stale = _make_stale_session(root, "stale_session", age_hours=DEFAULT_TTL_HOURS + 24)
    fresh = _make_fresh_session(root, "fresh_session")

    removed = sweep_stale_sessions(root=root)

    assert removed == ["stale_session"]
    assert not stale.exists()
    assert fresh.exists()


def test_missing_root_is_a_noop(tmp_path: Path):
    missing = tmp_path / "does_not_exist"

    removed = sweep_stale_sessions(root=missing)

    assert removed == []
    assert not missing.exists()


def test_empty_root_is_a_noop(tmp_path: Path):
    root = tmp_path / "agent_sessions"
    root.mkdir()

    removed = sweep_stale_sessions(root=root)

    assert removed == []
    assert root.exists()


# ---------------------------------------------------------------------------
# TTL resolution
# ---------------------------------------------------------------------------


def test_ttl_override_via_env_respects_shorter_window(tmp_path: Path, monkeypatch):
    root = tmp_path / "agent_sessions"
    # 2 hours old: survives the 168h default, but not a 1h override.
    two_hours_old = _make_stale_session(root, "two_hours_old", age_hours=2.0)

    kept = sweep_stale_sessions(root=root)
    assert kept == []
    assert two_hours_old.exists()

    monkeypatch.setenv("AGENT_SESSION_TTL_HOURS", "1")
    removed = sweep_stale_sessions(root=root)
    assert removed == ["two_hours_old"]
    assert not two_hours_old.exists()


def test_explicit_ttl_hours_param_overrides_env_var(tmp_path: Path, monkeypatch):
    root = tmp_path / "agent_sessions"
    session_dir = _make_stale_session(root, "two_hours_old", age_hours=2.0)

    # Env says "keep for a year"; the explicit argument says "1 hour" and
    # must win over it.
    monkeypatch.setenv("AGENT_SESSION_TTL_HOURS", "8760")
    removed = sweep_stale_sessions(root=root, ttl_hours=1.0)

    assert removed == ["two_hours_old"]
    assert not session_dir.exists()


def test_invalid_ttl_env_value_falls_back_to_default(tmp_path: Path, monkeypatch):
    root = tmp_path / "agent_sessions"
    # Well within the 168h default, so if the garbage env value were somehow
    # parsed as "very short", this would be (wrongly) removed.
    fresh = _make_stale_session(root, "recent", age_hours=1.0)

    monkeypatch.setenv("AGENT_SESSION_TTL_HOURS", "not-a-number")
    removed = sweep_stale_sessions(root=root)

    assert removed == []
    assert fresh.exists()


# ---------------------------------------------------------------------------
# Best-effort skip semantics (Windows lock / permission / vanished dir)
# ---------------------------------------------------------------------------


def test_locked_file_dir_is_skipped_without_raising_other_stale_dirs_still_removed(
    tmp_path: Path, caplog
):
    root = tmp_path / "agent_sessions"
    locked_dir = root / "locked_session"
    locked_dir.mkdir(parents=True)
    locked_path = locked_dir / "locked.txt"

    other_stale = _make_stale_session(root, "another_stale", age_hours=DEFAULT_TTL_HOURS + 24)
    fresh = _make_fresh_session(root, "fresh_session")

    handle = open(locked_path, "w", encoding="utf-8")
    try:
        handle.write("held open by the test -- Windows blocks deletion of this file")
        handle.flush()
        _age(locked_path, DEFAULT_TTL_HOURS + 24)  # stale by content mtime, but locked

        with caplog.at_level(logging.WARNING, logger="services.agent.sessions"):
            removed = sweep_stale_sessions(root=root)
    finally:
        handle.close()

    # The locked dir was skipped, not removed, and nothing raised.
    assert "locked_session" not in removed
    assert locked_dir.exists()
    assert locked_path.exists()

    # Other stale dirs in the same sweep pass were still removed.
    assert "another_stale" in removed
    assert not other_stale.exists()

    # Fresh dir untouched.
    assert fresh.exists()

    # A warning was logged for the skipped directory.
    assert any(
        "locked_session" in record.message and record.levelno == logging.WARNING
        for record in caplog.records
    )


def test_non_directory_entries_under_root_are_ignored(tmp_path: Path):
    root = tmp_path / "agent_sessions"
    root.mkdir()
    stray_file = root / "stray.txt"
    stray_file.write_text("not a session directory", encoding="utf-8")
    _age(stray_file, DEFAULT_TTL_HOURS + 24)

    removed = sweep_stale_sessions(root=root)

    assert removed == []
    assert stray_file.exists()


def test_empty_session_dir_falls_back_to_directory_mtime(tmp_path: Path):
    root = tmp_path / "agent_sessions"
    empty_dir = root / "empty_session"
    empty_dir.mkdir(parents=True)
    _age(empty_dir, DEFAULT_TTL_HOURS + 24)

    removed = sweep_stale_sessions(root=root)

    assert removed == ["empty_session"]
    assert not empty_dir.exists()


# ---------------------------------------------------------------------------
# Summary logging
# ---------------------------------------------------------------------------


def test_summary_line_is_logged_with_removed_kept_skipped_counts(tmp_path: Path, caplog):
    root = tmp_path / "agent_sessions"
    _make_stale_session(root, "stale_session", age_hours=DEFAULT_TTL_HOURS + 24)
    _make_fresh_session(root, "fresh_session")

    with caplog.at_level(logging.INFO, logger="services.agent.sessions"):
        sweep_stale_sessions(root=root)

    summary_records = [r for r in caplog.records if "removed=" in r.message]
    assert len(summary_records) == 1
    assert summary_records[0].levelno == logging.INFO
    assert "removed=1" in summary_records[0].message
    assert "kept=1" in summary_records[0].message
    assert "skipped=0" in summary_records[0].message

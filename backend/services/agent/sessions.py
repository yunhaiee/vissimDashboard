"""Startup TTL sweep for stale agent-session working directories.

Every agent turn gets a scratch directory under
``runtime/agent_sessions/<session_id>/`` (see ``_agent_session_work_dir`` in
``backend/routers/assistant.py``) holding its trace JSONL file
(``services/agent/trace.py``) and any ``ArtifactManager``-managed .inpx
working copies (``services/agent/state.py``). Nothing currently removes
them, so the directory grows without bound — see docs/phase2/DECISIONS.md,
"Deferred findings from the M0+M1 whole-branch review" (the "M2 ops" bullet).
This module adds a best-effort TTL sweep, wired into backend startup
(``main.py``), that removes any session directory whose newest content is
older than the TTL.

Deliberately conservative, per the guardrail doctrine's verification bias
(docs/phase2/00-EXECUTION-GUIDE.md §4): a directory that cannot be fully
inspected or removed — a Windows-locked file, a permission error, a
directory that vanishes mid-sweep because a session is still actively using
it — is left alone and merely logged, never treated as fatal. A cleanup
pass must never crash backend startup, and must never risk raising out of
a boot sequence over what is, at worst, some extra disk usage.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

from config import REPO_ROOT

logger = logging.getLogger(__name__)

#: Default time-to-live for an agent-session working directory, in hours
#: (7 days). Overridable per-process via the ``AGENT_SESSION_TTL_HOURS`` env
#: var (read fresh on every sweep call, not cached at import time).
DEFAULT_TTL_HOURS = 168.0

#: Mirrors ``services.simulation_runs.SIMULATION_ROOT``'s REPO_ROOT-relative
#: base resolution, and must stay in sync with
#: ``routers.assistant._agent_session_work_dir``'s path.
SESSIONS_ROOT = REPO_ROOT / "runtime" / "agent_sessions"


def _resolve_ttl_hours(ttl_hours: float | None) -> float:
    """Explicit argument wins; otherwise ``AGENT_SESSION_TTL_HOURS``;
    otherwise :data:`DEFAULT_TTL_HOURS`. An unparseable env value is logged
    and treated as unset rather than raised -- a malformed env var must
    never crash startup."""
    if ttl_hours is not None:
        return ttl_hours

    raw = os.getenv("AGENT_SESSION_TTL_HOURS", "").strip()
    if not raw:
        return DEFAULT_TTL_HOURS
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "Ignoring invalid AGENT_SESSION_TTL_HOURS=%r (not a number); using default %.1fh",
            raw,
            DEFAULT_TTL_HOURS,
        )
        return DEFAULT_TTL_HOURS


def _newest_mtime(session_dir: Path) -> float | None:
    """Newest mtime (epoch seconds) among all files under ``session_dir``,
    recursive. This is the freshness signal the sweep acts on -- NOT the
    directory's own mtime, which only moves when an entry is added/removed
    directly inside it, so it goes stale the moment a session stops creating
    new files while still rewriting an existing one (e.g. appending to its
    trace JSONL).

    Returns ``None`` when the directory has no readable file anywhere under
    it (empty, or every entry raised on stat); the caller falls back to the
    directory's own mtime in that case.

    A single file that raises ``OSError`` on stat (vanished, locked,
    permission denied) is skipped individually -- it does not abort the walk
    for the rest of the directory.
    """
    newest: float | None = None
    for path in session_dir.rglob("*"):
        try:
            if not path.is_file():
                continue
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if newest is None or mtime > newest:
            newest = mtime
    return newest


def sweep_stale_sessions(root: Path | None = None, ttl_hours: float | None = None) -> list[str]:
    """Remove agent-session directories whose newest content is older than the TTL.

    Best-effort and never raises out of the sweep: a directory that cannot
    be inspected or removed (Windows file lock, permission error, or it
    vanishes mid-sweep) is skipped with a logged warning rather than treated
    as fatal. A missing or empty ``root`` is a no-op.

    Args:
        root: sessions root directory to sweep. Defaults to
            :data:`SESSIONS_ROOT` (``runtime/agent_sessions`` under the repo
            root).
        ttl_hours: directories whose newest content is older than this many
            hours are removed. Defaults to the ``AGENT_SESSION_TTL_HOURS``
            env var, falling back to :data:`DEFAULT_TTL_HOURS` (168h / 7
            days).

    Returns:
        Names (not full paths) of the session directories that were
        removed, in sorted order.
    """
    sessions_root = root if root is not None else SESSIONS_ROOT
    ttl = _resolve_ttl_hours(ttl_hours)
    cutoff = time.time() - (ttl * 3600.0)

    try:
        if not sessions_root.is_dir():
            return []
        entries = sorted(sessions_root.iterdir())
    except OSError as exc:
        logger.warning(
            "Agent session sweep: cannot list %s (%s); skipping this sweep", sessions_root, exc
        )
        return []

    removed: list[str] = []
    kept = 0
    skipped = 0

    for entry in entries:
        try:
            if not entry.is_dir():
                continue  # stray file directly under the sessions root -- not a session, ignore

            newest = _newest_mtime(entry)
            if newest is None:
                # No readable content anywhere inside -- fall back to the
                # directory's own mtime rather than keeping an empty
                # directory forever.
                newest = entry.stat().st_mtime

            if newest >= cutoff:
                kept += 1
                continue

            shutil.rmtree(entry)
            removed.append(entry.name)
        except OSError as exc:
            # Windows file lock (an open handle blocks delete), permission
            # error, or the directory vanished underneath us (e.g. a session
            # still actively running) -- skip this directory, keep sweeping
            # the rest.
            logger.warning("Agent session sweep: skipping %s (%s)", entry, exc)
            skipped += 1
        except Exception as exc:  # noqa: BLE001 - one bad directory must not abort the sweep
            logger.warning(
                "Agent session sweep: skipping %s (unexpected %s: %s)",
                entry,
                type(exc).__name__,
                exc,
            )
            skipped += 1

    logger.info(
        "Agent session sweep: removed=%d kept=%d skipped=%d (ttl_hours=%.2f, root=%s)",
        len(removed),
        kept,
        skipped,
        ttl,
        sessions_root,
    )
    return removed

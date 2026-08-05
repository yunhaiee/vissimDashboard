"""Agent-session directories: where they live, who OWNS them, and the TTL sweep.

Two jobs, both keyed on the same per-session directory:

**Ownership (unit 1.4).** :func:`session_workspace_matches` is THE predicate
every session-scoped endpoint asks before serving anything —
``routers/network.py``'s working-copy view, ``routers/agent_downloads.py``
(single + listing), ``services/audit_trail.py``'s ledger reader, and
``routers/assistant.py``'s resume/approve paths. It answers from the session's
``session_meta.json`` sidecar and FAILS CLOSED: no readable owner means no
access, for everybody. The pre-1.4 population — sessions that existed before
sidecars did — is enumerated once by :func:`backfill_legacy_session_owners` and
stays reachable from the default workspace; nothing else does.

**Cleanup (M2 ops).** Every agent turn gets a scratch directory under
``runtime/agent_sessions/<session_id>/`` (see :func:`session_work_dir` below --
THE definition; ``routers/assistant.py``'s ``_agent_session_work_dir``
delegates to it) holding its trace JSONL file
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

import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import REPO_ROOT

logger = logging.getLogger(__name__)

#: Default time-to-live for an agent-session working directory, in hours
#: (7 days). Overridable per-process via the ``AGENT_SESSION_TTL_HOURS`` env
#: var (read fresh on every sweep call, not cached at import time).
DEFAULT_TTL_HOURS = 168.0

#: Mirrors ``services.simulation_runs.SIMULATION_ROOT``'s REPO_ROOT-relative
#: base resolution. THE definition of the sessions root: unit 1.4 removed the
#: second copy that used to live in ``routers.assistant._agent_session_work_dir``
#: (documented as a "must stay in sync" hazard), so that function now delegates
#: to :func:`session_work_dir` here.
SESSIONS_ROOT = REPO_ROOT / "runtime" / "agent_sessions"

#: Sidecar written once per session, next to ``state.json`` /
#: ``pending_approval.json`` (same established per-session-file pattern).
SESSION_META_FILENAME = "session_meta.json"

#: Written ONCE per sessions root, AT the root (never inside a session dir —
#: see :func:`backfill_legacy_session_owners` for why that placement is
#: load-bearing). Names every session directory that existed with no sidecar at
#: the moment this box upgraded to unit 1.4, and the workspace they all belong
#: to (the default one — before 1.4 there was exactly one dataset).
LEGACY_OWNERS_FILENAME = "legacy_session_owners.json"

#: ``str(root)`` -> ``(workspace_id, frozenset[session_id])``, the parsed
#: legacy-owner ledger. Doubles as the once-per-root flag: a key being present
#: means the backfill has already run for that root in THIS process. Keyed by
#: root so a test (or a tool) that repoints the root gets its own attempt,
#: exactly mirroring ``services.workspaces._migration_done``.
#:
#: This is what makes "no sidecar" mean two DIFFERENT things at two different
#: times, which is the whole point of the design:
#:   * a session named in the ledger — a pre-1.4 session that predates
#:     sidecars, legitimately the default workspace's;
#:   * any OTHER sidecar-less session — one whose owner was never recorded or
#:     whose sidecar was lost, which :func:`session_workspace_matches` denies to
#:     EVERYONE, including the default workspace.
_legacy_owners: dict[str, tuple[str, frozenset[str]]] = {}

#: Serializes the one-time backfill so two concurrent requests cannot both
#: walk the sessions root. Not a cross-process lock and does not claim to be
#: (same single-instance assumption every registry in this repo makes).
_BACKFILL_LOCK = threading.RLock()


def session_work_dir(session_id: str, root: Path | None = None) -> Path:
    """Per-session agent scratch dir. ``root`` exists so a caller can pass its
    OWN (test-patchable) ``SESSIONS_ROOT`` binding — the routers each import
    the constant by name and tests monkeypatch those module attributes."""
    return (root if root is not None else SESSIONS_ROOT) / session_id


def session_meta_path(work_dir: Path | str) -> Path:
    return Path(work_dir) / SESSION_META_FILENAME


def read_session_meta_dir(work_dir: Path | str) -> dict[str, Any]:
    """The session's frozen identity: ``{workspace_id, source_inpx,
    dataset_revision, created_at}``.

    Returns ``{}`` for a session whose sidecar is missing or unreadable. Never
    raises: an unreadable sidecar must not 500 a chat request.

    What ``{}`` MEANS depends on the caller, and the two callers differ on
    purpose:

    * :func:`session_workspace_matches` (ownership) treats it as **deny**,
      unless :func:`backfill_legacy_session_owners` recorded this session as
      pre-1.4. An absent or corrupt sidecar on any other session is an unowned
      session, and an unowned session belongs to nobody (it used to be silently
      awarded to the default workspace, which handed one project's working copy
      to any caller that sent no ``X-Workspace-Id`` header).
    * ``routers/assistant._session_source_inpx`` and
      ``services.agent.tools_edit._session_pristine_source`` (provenance) fall
      back to live dataset resolution, which is only ever a freshness question,
      never an access-control one.
    """
    path = session_meta_path(work_dir)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning(
            "sessions: unreadable %s (%s); the session has no readable owner and is denied to "
            "every workspace until it is repaired",
            path,
            exc,
        )
        return {}
    return payload if isinstance(payload, dict) else {}


def read_session_meta(session_id: str, root: Path | None = None) -> dict[str, Any]:
    return read_session_meta_dir(session_work_dir(session_id, root))


def write_session_meta(work_dir: Path | str, meta: dict[str, Any]) -> None:
    """Persist the session's frozen identity, atomically.

    Written ONCE, at session creation. Rewriting it later would defeat the
    whole point: the source .inpx recorded here is what a rollback-to-0
    restores from, so it must keep pointing at the dataset the session was
    cloned from even after the user swaps the workspace's active dataset.
    """
    target_dir = Path(work_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    final_path = session_meta_path(target_dir)
    tmp_path = final_path.with_name(final_path.name + ".tmp")
    tmp_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(final_path)


def legacy_owners_path(root: Path | None = None) -> Path:
    """The one-time legacy-owner ledger, AT the sessions root."""
    return (root if root is not None else SESSIONS_ROOT) / LEGACY_OWNERS_FILENAME


def read_legacy_owner_ledger(root: Path | None = None) -> dict[str, Any] | None:
    """Parse the ledger. ``None`` when it is absent, unreadable, or malformed.

    Never raises. A malformed ledger deliberately does NOT degrade to "empty
    ledger": returning ``None`` means the backfill has not successfully
    happened, so the caller keeps retrying and — until it succeeds — every
    sidecar-less session stays denied. Fail closed, then fix forward.
    """
    path = legacy_owners_path(root)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("sessions: unreadable legacy-owner ledger at %s (%s)", path, exc)
        return None
    if not isinstance(payload, dict) or not payload.get("workspace_id"):
        logger.warning("sessions: malformed legacy-owner ledger at %s", path)
        return None
    ids = payload.get("session_ids")
    if not isinstance(ids, list):
        logger.warning("sessions: legacy-owner ledger at %s has no session_ids list", path)
        return None
    return payload


def backfill_legacy_session_owners(
    workspace_id: str, root: Path | None = None
) -> dict[str, Any] | None:
    """Record, once and durably, which sessions predate unit 1.4 — and that they
    belong to ``workspace_id`` (in practice: the default workspace).

    THE documented pre-1.4 upgrade path, made explicit. Before unit 1.4 there
    was one dataset and no notion of ownership, so every session on the box was
    the default workspace's — the same place ``services.workspaces.
    _migrate_legacy_layout`` moves that dataset. Writing that fact down ONCE is
    what lets :func:`session_workspace_matches` fail CLOSED afterwards: with the
    legacy population enumerated, a session that STILL has no sidecar is not
    "old", it is unowned, and unowned must mean nobody — including the default
    workspace.

    **Why one ledger at the root rather than a ``session_meta.json`` stamped
    into each legacy directory** (the shape originally specified for this fix):
    the ownership predicate is consulted from READ paths — the download, audit
    and network-view endpoints — and ``routers/agent_downloads.py``'s module
    docstring promises, with three tests pinning it byte-for-byte
    (``test_agent_downloads.py::test_list_never_mutates_the_session_directory``,
    ``::test_download_never_mutates_the_session_directory``,
    ``test_agent_audit_endpoint.py::test_export_never_writes_into_the_session_directory``),
    that listing or downloading a session can never write inside its work dir.
    Per-directory stamping breaks that promise the first time anyone lists a
    legacy session — measured, all three tests fail. A single file at the root
    is outside every session directory, so the promise holds; it is skipped by
    :func:`sweep_stale_sessions` and by the download listing, both of which only
    consider entries that ``is_dir()``. The tradeoff accepted: ownership of a
    legacy session is no longer self-describing, so moving such a directory to
    another box loses it (it becomes unowned = denied, which is the safe
    direction). Recorded here rather than left implicit.

    Idempotent: an existing ledger is never rewritten, so a session directory
    created AFTER the upgrade can never be retro-added to the legacy set.
    Exclusive creation (``open(..., "x")``) rather than check-then-write, so
    that guarantee is enforced by the OS and survives two workers racing.

    Never raises. Returns the ledger (existing or newly written), or ``None``
    when it could not be established — the caller must then retry rather than
    treat "no ledger" as "no legacy sessions".
    """
    sessions_root = root if root is not None else SESSIONS_ROOT

    existing = read_legacy_owner_ledger(sessions_root)
    if existing is not None:
        return existing

    legacy_ids: list[str] = []
    try:
        if sessions_root.is_dir():
            for entry in sorted(sessions_root.iterdir()):
                try:
                    # A directory with no sidecar predates unit 1.4. A stray
                    # file directly under the root is not a session at all.
                    if entry.is_dir() and not session_meta_path(entry).exists():
                        legacy_ids.append(entry.name)
                except OSError as exc:
                    logger.warning("sessions: skipping %s during the legacy backfill (%s)", entry, exc)
    except OSError as exc:
        logger.warning(
            "sessions: cannot list %s for the legacy owner backfill (%s); will retry",
            sessions_root,
            exc,
        )
        return None

    payload: dict[str, Any] = {
        "workspace_id": str(workspace_id),
        "backfilled_at": datetime.now(timezone.utc).isoformat(),
        "session_ids": legacy_ids,
    }
    path = legacy_owners_path(sessions_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except FileExistsError:
        # Another worker won the race between the read above and this write.
        return read_legacy_owner_ledger(sessions_root)
    except OSError as exc:
        logger.warning(
            "sessions: could not write the legacy-owner ledger at %s (%s); will retry", path, exc
        )
        return None

    logger.info(
        "sessions: legacy owner backfill recorded %d pre-1.4 session(s) as workspace %s (root=%s)",
        len(legacy_ids),
        workspace_id,
        sessions_root,
    )
    return payload


def _ensure_legacy_sessions_backfilled(sessions_root: Path) -> None:
    """Run :func:`backfill_legacy_session_owners` once per root per process and
    cache the result in :data:`_legacy_owners`.

    Belt AND braces. The braces are the explicit call the workspace legacy
    migration makes (``ensure_default_workspace`` runs exactly once per box,
    which is the natural home for a one-time upgrade). The belt is this: the
    ownership predicate below triggers the backfill itself on first use, so the
    upgrade path cannot silently regress if that call is ever dropped,
    reordered, or never reached (a box whose default workspace already existed
    before this unit shipped never re-runs migration).

    Cheap after the first call: one lock-free dict membership test.

    On failure nothing is cached, so the next ownership check retries — a
    transient failure must not permanently strand the legacy population in the
    deny state, and must never be able to break a boot or a request.
    """
    key = str(sessions_root)
    if key in _legacy_owners:
        return
    with _BACKFILL_LOCK:
        if key in _legacy_owners:
            return
        try:
            # Local import: services.agent.* must not pull the workspace/
            # registry stack in at module-import time (tools modules are
            # imported by scripts that never touch HTTP).
            from services import workspaces

            default_id = workspaces.default_workspace_id()
        except Exception as exc:  # noqa: BLE001 - retried on the next check
            logger.warning(
                "sessions: cannot resolve the default workspace for the legacy backfill "
                "(%s: %s); will retry",
                type(exc).__name__,
                exc,
            )
            return
        ledger = backfill_legacy_session_owners(default_id, sessions_root)
        if ledger is None:
            return
        _legacy_owners[key] = (
            str(ledger["workspace_id"]),
            frozenset(str(entry) for entry in ledger["session_ids"]),
        )


def _legacy_session_owner(session_id: str, sessions_root: Path) -> str | None:
    """The workspace a PRE-1.4 session belongs to, or ``None`` if this is not
    one. Reads only the in-memory ledger cache — :func:`_ensure_legacy_sessions_backfilled`
    must have run first."""
    entry = _legacy_owners.get(str(sessions_root))
    if entry is None:
        return None
    owner, legacy_ids = entry
    return owner if session_id in legacy_ids else None


def session_workspace_matches(
    session_id: str, workspace_id: str, root: Path | None = None
) -> bool:
    """Whether ``session_id`` may be read from ``workspace_id``. Fails CLOSED.

    A session is owned by the workspace its ``session_meta.json`` names, and by
    nobody else. No sidecar, an unparseable sidecar, a sidecar with a blank
    ``workspace_id``, or no session directory at all → ``False`` for EVERY
    caller, the default workspace included.

    That last part is the fix for a measured fail-open hole: this predicate used
    to substitute ``workspaces.default_workspace_id()`` whenever the sidecar
    did not answer, so a session created by project B whose sidecar was missing
    or truncated became the DEFAULT project's — a caller sending no
    ``X-Workspace-Id`` header got B's working copy parsed and served by
    ``GET /api/network?session_id=``, B's edited .inpx from the download
    endpoints, and B's action ledger from the audit export, while B itself was
    locked out. It was also a positive existence oracle: ``view=working`` vs
    ``view=active`` distinguished "a real session exists here" from a ghost id.

    The pre-1.4 upgrade path survives explicitly rather than implicitly:
    :func:`backfill_legacy_session_owners` enumerates the sidecar-less
    population as the default workspace's ONCE, and
    :func:`_ensure_legacy_sessions_backfilled` guarantees that has happened
    before the first ownership answer this process gives. A session named in
    that ledger stays reachable from the default workspace exactly as it was
    before this unit; a sidecar-less session that is NOT named in it — because
    it appeared after the upgrade — is denied.
    """
    sessions_root = root if root is not None else SESSIONS_ROOT
    _ensure_legacy_sessions_backfilled(sessions_root)

    meta = read_session_meta(session_id, sessions_root)
    owner = meta.get("workspace_id") or _legacy_session_owner(session_id, sessions_root)
    if not owner:
        return False
    return str(owner) == str(workspace_id)


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

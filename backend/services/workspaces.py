"""Project workspaces — the access-control boundary uploads live inside.

Before this module the app had exactly ONE dataset: ``runtime/uploads/current``,
a process-wide singleton every reader resolved through
``services.datasets.get_active_*``. Two consultants (or two clients' networks)
could not coexist on one box, and an upload silently destroyed whatever the
previous one had put there. A **workspace** (UI label: 프로젝트) is the isolation
unit that fixes that: one directory tree under ``runtime/workspaces/<id>/``
owning its own ``uploads/current`` dataset, its own workbook-mapping staging
area, and its own remembered column mappings.

Why "workspace" and not "project" in code: ``services/results_reader.py``
already uses ``project_id`` for Vissim's OWN project concept (a
``.vissimpdb``'s scenario-management project), and colliding on that name in
the same codebase would be a permanent reading hazard. The Korean UI string
stays 프로젝트 — that is what a consultant calls it.

**Request scoping.** Every HTTP request carries an optional ``X-Workspace-Id``
header. ``main.WorkspaceScopeMiddleware`` resolves it (absent → the default
workspace; malformed or unknown → 404) and binds it to a
:class:`contextvars.ContextVar` for the life of the request, which is what
``services.datasets``' accessors read on every call. A ContextVar (not a
module global) because it is copied per asyncio task / threadpool hop, so two
concurrent requests can never observe each other's workspace.

**Outside a request** — a job-runner thread, a test, a ``backend/tools``
script — nothing binds the ContextVar, and :func:`current_workspace_id` RAISES
:class:`WorkspaceScopeError` rather than quietly defaulting. That is
deliberate and matches this project's doctrine: silently serving one client's
network to another client's report is exactly the failure the workspace
boundary exists to prevent, and an explicit
``with bind_workspace(ws_id): ...`` at every background entry point is a
one-line cost. Callers that legitimately want "whatever the default is" say so
by binding :func:`default_workspace_id` explicitly.

**Legacy migration** is lazy and idempotent: the first time a default
workspace has to be created, any pre-workspace ``runtime/uploads/{current,
staging,mapping_memory.json}`` is MOVED under it (check-then-move per item, so
a partial failure leaves the untouched items exactly where they were).
Nothing is ever copied-then-deleted, so a crash cannot lose the only copy of a
dataset. A migration that did not fully complete is **not** marked done, so
the very next :func:`ensure_default_workspace` call retries it — the flag used
to be set unconditionally, which made "retries on the next call" mean "retries
on the next BOOT". While incomplete the box is DEGRADED (the app serves the
``data/`` demo dataset while the real one still sits in ``runtime/uploads``);
:func:`legacy_migration_status` reports it, it is logged at WARNING, and
``services.datasets.get_active_dataset`` carries it to the UI so the state is
visible instead of silent.

Test seam: :data:`WORKSPACES_ROOT` and :data:`LEGACY_UPLOADS_ROOT` are the two
module-level constants ``tests/conftest.py`` redirects. Both MUST be patched
together — patching only the first would point migration at the developer's
REAL ``runtime/uploads`` and move their live dataset into a pytest tmp dir.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from config import REPO_ROOT

logger = logging.getLogger(__name__)

#: THE single test seam for everything workspace-scoped on disk.
WORKSPACES_ROOT = REPO_ROOT / "runtime" / "workspaces"

#: Pre-workspace layout, migrated once into the default workspace. Patched
#: alongside :data:`WORKSPACES_ROOT` in tests — see the module docstring.
LEGACY_UPLOADS_ROOT = REPO_ROOT / "runtime" / "uploads"

REGISTRY_FILENAME = "registry.json"

#: ko primary (this is a user-visible name, stored once at creation).
DEFAULT_WORKSPACE_NAME = "기본 프로젝트"

MAX_WORKSPACE_NAME_CHARS = 120

#: Deliberately hex-only and length-capped, verbatim the shape
#: ``routers/network.py``'s ``_SESSION_ID_RE`` uses for session ids: a plain,
#: unambiguous directory-name shape that cannot itself contain a path
#: separator or ".." segment, so a client-supplied id can never escape
#: :data:`WORKSPACES_ROOT` regardless of how the OS/pathlib normalizes it.
#: The resolve()+relative_to guard in :func:`workspace_dir` is the second,
#: independent check — never trust one alone against path construction.
_WORKSPACE_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")

#: Serializes registry read-modify-write inside this process. File-based
#: storage, single-instance app (same assumption every other registry in this
#: repo makes) — this is not a cross-process lock and does not claim to be.
_REGISTRY_LOCK = threading.RLock()

#: Roots whose legacy migration has COMPLETED (every item that existed was
#: moved). Keyed by ``str(WORKSPACES_ROOT)`` so a test that repoints the root
#: gets its own migration attempt. A root is added ONLY on completion — an
#: incomplete migration must be retried on the next call, not on the next
#: process boot.
_migration_done: set[str] = set()

#: Roots whose legacy migration is incomplete, mapped to the status
#: :func:`legacy_migration_status` reports. Keyed the same way; an entry is
#: dropped as soon as a later attempt completes.
_migration_degraded: dict[str, dict[str, Any]] = {}

_current_workspace_id: ContextVar[str] = ContextVar("prism_current_workspace_id")


class WorkspaceError(RuntimeError):
    """Base class for every failure this module raises."""


class WorkspaceNotFoundError(WorkspaceError):
    """A workspace id was malformed, escaped the root, or is not registered.

    Callers at the HTTP boundary turn this into a 404 with ``code:
    "workspace_not_found"`` — never a 500, and never a silent fallback to the
    default workspace (that would be the cross-client data leak this whole
    unit exists to close)."""


class WorkspaceScopeError(WorkspaceError):
    """:func:`current_workspace_id` was called with nothing bound.

    Always a wiring bug in a background entry point (a job thread, a script, a
    test), never a user error — the message names ``bind_workspace`` so
    whoever hits it knows the one-line fix."""


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def registry_path() -> Path:
    return WORKSPACES_ROOT / REGISTRY_FILENAME


def workspace_dir(workspace_id: str) -> Path:
    """The on-disk root of ``workspace_id``, validated twice.

    Does NOT require the directory to exist (it is created lazily by the first
    upload). Raises :class:`WorkspaceNotFoundError` for anything whose shape
    could escape :data:`WORKSPACES_ROOT`.
    """
    if not workspace_id or not _WORKSPACE_ID_RE.match(workspace_id):
        raise WorkspaceNotFoundError(f"Malformed workspace id: {workspace_id!r}")

    root = WORKSPACES_ROOT.resolve()
    target = (root / workspace_id).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        logger.warning("workspaces: id %r resolved outside WORKSPACES_ROOT; refusing", workspace_id)
        raise WorkspaceNotFoundError(f"Malformed workspace id: {workspace_id!r}") from None
    return target


def workspace_uploads_root(workspace_id: str) -> Path:
    """``<workspace>/uploads`` — parent of ``current``, the staging area, and
    the mapping-memory file. Everything an upload touches lives under here, so
    the stage-then-promote rename in ``services.datasets`` never crosses a
    filesystem boundary."""
    return workspace_dir(workspace_id) / "uploads"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _read_registry_payload() -> dict[str, Any]:
    path = registry_path()
    if not path.exists():
        return {"workspaces": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("workspaces: unreadable registry at %s (%s); treating as empty", path, exc)
        return {"workspaces": []}
    if not isinstance(payload, dict):
        return {"workspaces": []}
    entries = payload.get("workspaces")
    if not isinstance(entries, list):
        return {"workspaces": []}
    return {"workspaces": [entry for entry in entries if isinstance(entry, dict) and entry.get("id")]}


def _write_registry_payload(payload: dict[str, Any]) -> None:
    """Atomic temp-file-then-replace, same contract as
    ``services.agent.session_store.save_state`` — a reader must never observe
    a half-written registry."""
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_path.replace(path)


def list_workspaces() -> list[dict[str, Any]]:
    """Every registered workspace, default first, then creation order.

    Auto-creates (and migrates into) the default workspace on first touch, so
    a fresh checkout and a legacy install both answer this the same way.
    """
    ensure_default_workspace()
    entries = _read_registry_payload()["workspaces"]
    return sorted(entries, key=lambda entry: (not entry.get("is_default"), str(entry.get("created_at") or "")))


def get_workspace(workspace_id: str) -> dict[str, Any] | None:
    """The registry record for ``workspace_id``, or ``None`` when it is not
    registered. Never raises for a malformed id — callers that need the
    distinction use :func:`workspace_dir`."""
    if not workspace_id or not _WORKSPACE_ID_RE.match(workspace_id):
        return None
    for entry in _read_registry_payload()["workspaces"]:
        if entry.get("id") == workspace_id:
            return entry
    return None


def create_workspace(name: str) -> dict[str, Any]:
    """Register a new workspace and create its directory.

    Raises:
        ValueError: ``name`` is blank or longer than
            :data:`MAX_WORKSPACE_NAME_CHARS` (the router turns this into a
            422).
    """
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("Workspace name is required.")
    if len(cleaned) > MAX_WORKSPACE_NAME_CHARS:
        raise ValueError(f"Workspace name must be at most {MAX_WORKSPACE_NAME_CHARS} characters.")

    ensure_default_workspace()
    with _REGISTRY_LOCK:
        payload = _read_registry_payload()
        record = _new_record(cleaned, is_default=False)
        payload["workspaces"].append(record)
        _write_registry_payload(payload)
    workspace_dir(record["id"]).mkdir(parents=True, exist_ok=True)
    return record


def ensure_default_workspace() -> dict[str, Any]:
    """The default workspace record, creating it (and running the one-time
    legacy migration into it) if this box has never had one."""
    with _REGISTRY_LOCK:
        payload = _read_registry_payload()
        record = next((entry for entry in payload["workspaces"] if entry.get("is_default")), None)
        if record is None:
            record = _new_record(DEFAULT_WORKSPACE_NAME, is_default=True)
            payload["workspaces"].insert(0, record)
            _write_registry_payload(payload)
            workspace_dir(record["id"]).mkdir(parents=True, exist_ok=True)

        root_key = str(WORKSPACES_ROOT)
        if root_key not in _migration_done:
            # _migrate_legacy_layout never raises (an unbootable app is a worse
            # failure than a delayed migration), so its RETURN VALUE is the
            # only signal that it worked. Marking the flag unconditionally --
            # as this did -- made the "retried on the next call" promise false:
            # the flag is per PROCESS, so the real retry was the next BOOT,
            # and in between the app silently served the data/ demo dataset
            # while the user's own model sat unmigrated in runtime/uploads.
            failed, conflicted = _migrate_legacy_layout(record["id"])
            # Agent sessions that predate workspaces carry no ownership sidecar.
            # Claim them for the default workspace HERE, at the upgrade moment,
            # so the legacy set is frozen deterministically rather than at
            # whenever the first ownership check happens to run. The predicate
            # also calls this lazily, so this line is belt-and-braces; it is
            # idempotent and never raises. Imported locally: services.agent
            # imports workspaces, so a module-level import would be a cycle.
            from services.agent.sessions import backfill_legacy_session_owners

            backfill_legacy_session_owners(record["id"])
            if failed or conflicted:
                _record_degraded_migration(root_key, record["id"], failed, conflicted)
                # Only a hard failure is worth retrying: a CONFLICTED item
                # (source and destination both present) will conflict forever,
                # so retrying it on every request buys nothing. It stays in the
                # status either way, so it is still visible.
                if not failed:
                    _migration_done.add(root_key)
            else:
                _migration_degraded.pop(root_key, None)
                _migration_done.add(root_key)
    return record


def default_workspace_id() -> str:
    return str(ensure_default_workspace()["id"])


def _new_record(name: str, *, is_default: bool) -> dict[str, Any]:
    return {
        "id": uuid4().hex[:12],
        "name": name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "is_default": is_default,
    }


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------

#: ``runtime/uploads/<name>`` -> ``<workspace>/uploads/<name>``. The whole
#: pre-workspace surface: the dataset itself, the workbook-mapping staging
#: area, and the remembered column mappings (which were CROSS-dataset before
#: this unit — an inference channel between two clients' spreadsheets).
_LEGACY_ITEMS = ("current", "staging", "mapping_memory.json")


def _migrate_legacy_layout(workspace_id: str) -> tuple[list[str], list[str]]:
    """Move any pre-workspace ``runtime/uploads/*`` under ``workspace_id``.

    Idempotent and per-item: an item is moved only when the source exists AND
    the destination does not, so re-running after a partial failure completes
    the job instead of nesting directories. ``shutil.move`` on the same
    filesystem is a rename — a failure leaves the source intact and readable
    under the legacy layout, never a half-copied dataset.

    Never raises: a migration failure must not make the app unbootable (the
    legacy data is still there, and the log says exactly what did not move).
    Because it never raises, the CALLER cannot tell success from failure by
    exception, so it returns ``(failed, conflicted)`` instead:

    * ``failed`` — the move was attempted and blew up (a held handle, a
      permission problem). Worth retrying on the next call.
    * ``conflicted`` — source AND destination both exist, so we refuse to
      clobber. Retrying will conflict again forever; a human has to merge.

    Both lists empty means "nothing left in the legacy root", which is the only
    state that lets the caller mark the migration done.
    """
    legacy_root = LEGACY_UPLOADS_ROOT
    if not legacy_root.is_dir():
        return [], []

    destination_root = workspace_uploads_root(workspace_id)
    failed: list[str] = []
    conflicted: list[str] = []
    for item in _LEGACY_ITEMS:
        source = legacy_root / item
        destination = destination_root / item
        if not source.exists():
            continue
        if destination.exists():
            conflicted.append(item)
            continue
        try:
            destination_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            logger.info("workspaces: migrated legacy %s into workspace %s", source, workspace_id)
        except Exception as exc:  # noqa: BLE001 - see the docstring: never fatal
            failed.append(item)
            logger.warning(
                "workspaces: could not migrate legacy %s into workspace %s (%s: %s); "
                "leaving it in place for the next attempt",
                source,
                workspace_id,
                type(exc).__name__,
                exc,
            )
    return failed, conflicted


def _record_degraded_migration(
    root_key: str, workspace_id: str, failed: list[str], conflicted: list[str]
) -> None:
    """Remember an incomplete migration and log it — once per distinct state.

    Re-logging on every request would bury the box in identical warnings (this
    runs on every header-less request), so the WARNING fires when the set of
    unmigrated items CHANGES. The status itself is always current, and
    ``services.datasets.get_active_dataset`` reads it on every call, so the
    dashboard shows the degraded state continuously either way.
    """
    status = {
        "code": "legacy_migration_incomplete",
        "workspace_id": workspace_id,
        "legacy_root": str(LEGACY_UPLOADS_ROOT),
        "failed": sorted(failed),
        "conflicted": sorted(conflicted),
        "message": (
            "The one-time migration of the pre-project upload folder did not finish. "
            f"These items are still in {LEGACY_UPLOADS_ROOT}: "
            f"{', '.join(sorted(failed + conflicted))}. "
            "Until they move, this project shows the built-in demo dataset instead of the "
            "model that folder holds."
        ),
    }
    previous = _migration_degraded.get(root_key)
    _migration_degraded[root_key] = status
    if previous is None or (previous["failed"], previous["conflicted"]) != (
        status["failed"],
        status["conflicted"],
    ):
        logger.warning(
            "workspaces: legacy migration into workspace %s is INCOMPLETE — failed=%s "
            "conflicted=%s; those items are still under %s and the app is serving the demo "
            "dataset in their place",
            workspace_id,
            status["failed"] or "[]",
            status["conflicted"] or "[]",
            LEGACY_UPLOADS_ROOT,
        )


def legacy_migration_status() -> dict[str, Any] | None:
    """The current root's incomplete-migration report, or ``None`` when healthy.

    The visible half of fix 3: a degraded migration used to be a single
    ``logger.warning`` on a boot nobody was watching, after which the dashboard
    quietly showed the ``data/`` demo dataset as though that were the user's
    model. ``services.datasets.get_active_dataset`` attaches this to the
    dataset payload (and therefore to ``GET /api/datasets/active`` and the
    project banner's ``GET /api/workspaces/current``) whenever it is not
    ``None``, so the operator sees WHY they are looking at demo data.

    Cheap by construction — an in-memory dict lookup, no disk I/O — because it
    is on the read path of every dataset call.
    """
    return _migration_degraded.get(str(WORKSPACES_ROOT))


# ---------------------------------------------------------------------------
# Request/job scoping
# ---------------------------------------------------------------------------


def resolve_workspace_id(raw: str | None) -> str:
    """Turn a raw ``X-Workspace-Id`` header value into a real workspace id.

    Absent/blank → the default workspace (every pre-workspace client keeps
    working unchanged). Anything else must be a REGISTERED id; a malformed or
    unknown one raises :class:`WorkspaceNotFoundError` rather than falling
    back — a stale id in a client's localStorage must produce a visible error,
    not another workspace's data.
    """
    value = (raw or "").strip()
    if not value:
        return default_workspace_id()
    if get_workspace(value) is None:
        raise WorkspaceNotFoundError(f"Unknown workspace id: {value!r}")
    return value


def current_workspace_id() -> str:
    """The workspace bound to this request/job, raising when nothing is.

    See the module docstring for why this refuses to default silently.
    """
    try:
        return _current_workspace_id.get()
    except LookupError:
        raise WorkspaceScopeError(
            "No workspace is bound to this execution context. HTTP requests are scoped by "
            "main.WorkspaceScopeMiddleware; background threads, scripts and tests must wrap "
            "their work in `with workspaces.bind_workspace(<id>):` (use "
            "workspaces.default_workspace_id() when the default workspace is genuinely intended)."
        ) from None


def has_workspace_scope() -> bool:
    """Whether a workspace is currently bound. For diagnostics/tests only —
    production code should know which workspace it is in."""
    return _current_workspace_id.get(None) is not None


@contextmanager
def bind_workspace(workspace_id: str) -> Iterator[str]:
    """Bind ``workspace_id`` for the duration of the block.

    The explicit entry point for every non-request execution context: the
    simulation job-runner threads, the agent turn's worker thread, pytest, and
    ``backend/tools`` scripts. Restores the previous binding (usually "none")
    on exit, so a thread pool that reuses OS threads cannot leak one job's
    workspace into the next.
    """
    if not workspace_id or not _WORKSPACE_ID_RE.match(workspace_id):
        raise WorkspaceNotFoundError(f"Malformed workspace id: {workspace_id!r}")
    token = _current_workspace_id.set(workspace_id)
    try:
        yield workspace_id
    finally:
        _current_workspace_id.reset(token)


def set_current_workspace_id(workspace_id: str):
    """Low-level binding for the ASGI middleware, which cannot use a ``with``
    block across its ``await``. Returns the token :func:`reset_current_workspace_id`
    needs."""
    return _current_workspace_id.set(workspace_id)


def reset_current_workspace_id(token) -> None:
    _current_workspace_id.reset(token)


__all__ = [
    "DEFAULT_WORKSPACE_NAME",
    "LEGACY_UPLOADS_ROOT",
    "MAX_WORKSPACE_NAME_CHARS",
    "WORKSPACES_ROOT",
    "WorkspaceError",
    "WorkspaceNotFoundError",
    "WorkspaceScopeError",
    "bind_workspace",
    "create_workspace",
    "current_workspace_id",
    "default_workspace_id",
    "ensure_default_workspace",
    "get_workspace",
    "has_workspace_scope",
    "legacy_migration_status",
    "list_workspaces",
    "registry_path",
    "reset_current_workspace_id",
    "resolve_workspace_id",
    "set_current_workspace_id",
    "workspace_dir",
    "workspace_uploads_root",
]

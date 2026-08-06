"""The SQLite home for Prism's METADATA — and only its metadata.

**P③'s rule: files stay files.** Nothing that is a dataset, a result, an
.inpx working copy, a trace JSONL or a report artifact moves into this
database, now or later. Vissim's own outputs are files, a consultant's
deliverable is a file, and a corrupt 40 MB blob column is a worse failure than
a corrupt 40 MB file (which at least the operator can see, copy and mail).
What belongs here is the small, relational, concurrently-written bookkeeping
that a JSON file on disk keeps getting wrong: who a user is, whether a session
is still valid, and — in later units — which run belongs to which workspace.
``services/simulation_runs.py``'s module docstring names this migration as the
real answer to its lost-update problem; unit U7 opens the door, it does not
walk the runs registry through it.

**Scope of THIS unit (U7).** Exactly two tables: ``users`` and
``auth_sessions``. The runs-registry / workspace migration is a later unit and
must not be smuggled in here — a half-migrated registry with two sources of
truth is strictly worse than the file-only version that works today.

**Why a per-call connection, not a thread-local one.** Prism's endpoints are
mostly plain ``def`` (sync) functions, which Starlette runs in an anyio worker
threadpool: the OS thread that serves a request is not stable across requests
and workers are recycled, while a :class:`sqlite3.Connection` is bound to the
thread that created it (``check_same_thread`` defaults to True) and owns an OS
file handle plus, in WAL mode, a read snapshot. Caching one per worker thread
would leak a handle per worker for the life of the process, keep a stale
snapshot alive if any code path ever left a transaction open, and never be
closed when a worker dies. Opening a connection, in contrast, is a file open
and a header read — microseconds — against an auth request whose cost is
dominated by a ~60 ms scrypt. So every call site here opens, uses and closes
its own connection, and the concurrency question reduces to what SQLite is
already good at.

**Durability settings.** WAL (readers never block the writer, and a reader
never sees a torn page), ``busy_timeout`` so a concurrent writer waits instead
of raising ``database is locked`` immediately, ``foreign_keys=ON`` (SQLite
defaults it OFF per connection, so the ``auth_sessions.user_id`` reference is
only real if we say so every time), and ``synchronous=FULL``. FULL rather than
the usual WAL companion NORMAL because this file's write volume is a handful
of rows per login: paying an fsync per commit costs nothing measurable here
and buys "an operator who created an admin account and lost power still has
that admin account", which is the doctrine this project applies everywhere
else (docs/phase2/00-EXECUTION-GUIDE.md §4).

**Test seam.** :data:`DB_PATH` is a module constant read at CALL time, never
bound into a default argument, so ``tests/conftest.py`` redirects the whole
database with one ``monkeypatch.setattr``. It also honours ``PRISM_DB_PATH``
so an operator (or a CLI transcript) can point at a scratch database without
editing code. Nothing here writes outside that path's parent directory.

**Schema versioning.** ``schema_version`` holds one row. Migrations are a
dict of ``target version -> callable``; :func:`ensure_schema` applies every
target above the version on disk, in order, each inside its own transaction,
and records the new version in the same transaction. U7 ships exactly one
(version 1). The ladder shape is here from the start so U8's "add an
``approvals`` table" is a dict entry rather than a redesign.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from config import REPO_ROOT

logger = logging.getLogger(__name__)

#: THE seam. Auth is GLOBAL, not per-workspace, so this deliberately sits in
#: ``runtime/db/`` and NOT under ``runtime/workspaces/<id>/`` — see
#: ``routers/auth.py`` for why a login must not be scoped to a project.
DB_PATH = Path(os.getenv("PRISM_DB_PATH", str(REPO_ROOT / "runtime" / "db" / "app.db")))

#: The schema version this build knows how to run against.
SCHEMA_VERSION = 1

#: How long a statement waits for a competing writer before it gives up.
BUSY_TIMEOUT_MS = int(os.getenv("PRISM_DB_BUSY_TIMEOUT_MS", "5000"))

#: Guards the initialize-once bookkeeping below. Not a substitute for SQLite's
#: own locking — two PROCESSES are serialized by ``BEGIN IMMEDIATE`` plus
#: ``CREATE TABLE IF NOT EXISTS``, which is why the migration steps are
#: idempotent rather than merely guarded in Python.
_INIT_LOCK = threading.Lock()

#: Resolved database paths whose schema this process has already ensured.
#: Keyed by path string so a test that repoints :data:`DB_PATH` at its own tmp
#: file gets a fresh initialization instead of inheriting this one.
_initialized: set[str] = set()


class DatabaseError(RuntimeError):
    """Raised when the database cannot be opened or brought to a known schema."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def database_path() -> Path:
    """The database file this process would use right now.

    Reads :data:`DB_PATH` on every call — that is what makes the test seam a
    seam rather than a suggestion.
    """
    return Path(DB_PATH)


def _open(path: Path) -> sqlite3.Connection:
    """A configured connection to ``path``. Callers must close it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        timeout=BUSY_TIMEOUT_MS / 1000.0,
        # Autocommit: transactions are opened explicitly by
        # :func:`write_transaction` with BEGIN IMMEDIATE. sqlite3's legacy
        # implicit-BEGIN would open a DEFERRED transaction on the first DML,
        # which under WAL can fail with "database is locked" the moment it has
        # to upgrade a read to a write — the one case busy_timeout cannot
        # retry, because upgrading would risk a deadlock.
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    try:
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        if mode is not None and str(mode[0]).lower() != "wal":
            # Some filesystems (notably network shares) refuse WAL. Not fatal —
            # the app still works in the rollback journal it fell back to — but
            # the concurrency argument in this module's docstring no longer
            # holds, so say so instead of pretending.
            logger.warning(
                "db: WAL unavailable for %s (journal_mode=%s); "
                "readers will block writers on this filesystem.",
                path,
                mode[0],
            )
        conn.execute(f"PRAGMA busy_timeout={int(BUSY_TIMEOUT_MS)}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
    except Exception:
        conn.close()
        raise
    return conn


# ---------------------------------------------------------------------------
# Schema + migrations
# ---------------------------------------------------------------------------

def _migrate_1_auth_core(conn: sqlite3.Connection) -> None:
    """v1 — the auth core: ``users`` and ``auth_sessions``.

    ``username`` is ``COLLATE NOCASE``, so its UNIQUE index is case-insensitive
    too (SQLite builds a column-constraint index with the column's declared
    collation): "Yunha" and "yunha" are the same login, which is what a human
    typing their own name into a form expects.

    ``role`` is CHECK-constrained rather than validated only in Python: the CLI
    and the routers are not the only things that will ever write this table,
    and an unknown role reaching U8's approval gate must be impossible, not
    unlikely.

    ``auth_sessions.token_hash`` — never the token. See ``services/auth.py``.
    ``revoked_at`` NULL means "not revoked"; a revoked or expired row is kept
    rather than deleted so "this session was ended at 14:03" stays answerable.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT    NOT NULL COLLATE NOCASE UNIQUE,
            display_name  TEXT    NOT NULL,
            role          TEXT    NOT NULL CHECK (role IN ('modeler','reviewer','admin')),
            password_hash TEXT    NOT NULL,
            created_at    TEXT    NOT NULL,
            disabled      INTEGER NOT NULL DEFAULT 0 CHECK (disabled IN (0,1))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id           TEXT    PRIMARY KEY,
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash   TEXT    NOT NULL UNIQUE,
            created_at   TEXT    NOT NULL,
            expires_at   TEXT    NOT NULL,
            revoked_at   TEXT,
            last_seen_at TEXT
        )
        """
    )
    # Every session lookup is by token_hash (already indexed by UNIQUE); this
    # one serves "log this user out everywhere" and the expiry sweep.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id)")


#: ``target version -> migration``. Applied in ascending order for every target
#: greater than the version found on disk. Each callable must be idempotent
#: (``IF NOT EXISTS``) so a crash between the DDL and the version bump is
#: recoverable by simply running it again.
_MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {
    1: _migrate_1_auth_core,
}


def _read_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    return int(row["version"]) if row is not None else 0


def _ensure_schema_on(conn: sqlite3.Connection, path: Path) -> int:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            id         INTEGER PRIMARY KEY CHECK (id = 1),
            version    INTEGER NOT NULL,
            applied_at TEXT    NOT NULL
        )
        """
    )
    current = _read_version(conn)
    if current > SCHEMA_VERSION:
        # Downgrade: an older build pointed at a newer database. Refusing is
        # the only honest option — the tables it does not know about are the
        # ones it would silently ignore while writing rows that violate them.
        raise DatabaseError(
            f"{path} is at schema version {current}, newer than this build's {SCHEMA_VERSION}. "
            "Refusing to open it; upgrade Prism or point PRISM_DB_PATH elsewhere."
        )

    for target in sorted(_MIGRATIONS):
        if target <= current:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            _MIGRATIONS[target](conn)
            conn.execute(
                "INSERT INTO schema_version (id, version, applied_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET version = excluded.version, "
                "applied_at = excluded.applied_at",
                (target, _now_iso()),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        logger.info("db: applied schema migration -> v%d on %s", target, path)
        current = target

    return current


def ensure_schema(*, force: bool = False) -> int:
    """Create/upgrade the schema if needed and return the version on disk.

    Idempotent by construction: calling it twice on the same file is a no-op
    the second time (and cheap the first time it is already current). Pass
    ``force=True`` to skip this process's "already done" memo — only tests
    that delete the file underneath us need that.
    """
    path = database_path()
    key = str(path)
    with _INIT_LOCK:
        if not force and key in _initialized and path.exists():
            # The ``path.exists()`` half matters: a test (or an operator) that
            # deletes the file while the process lives must get a rebuilt
            # schema, not an AttributeError three layers down.
            return SCHEMA_VERSION
        conn = _open(path)
        try:
            version = _ensure_schema_on(conn, path)
        finally:
            conn.close()
        _initialized.add(key)
        return version


def schema_version() -> int:
    """The version recorded in the database file (0 when it has no ledger)."""
    path = database_path()
    if not path.exists():
        return 0
    conn = _open(path)
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        return _read_version(conn) if row is not None else 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------

@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """A schema-ensured connection, closed on exit. Autocommit — use
    :func:`write_transaction` when several statements must land together."""
    ensure_schema()
    conn = _open(database_path())
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def write_transaction() -> Iterator[sqlite3.Connection]:
    """A connection inside ``BEGIN IMMEDIATE``: committed on clean exit,
    rolled back on any exception.

    IMMEDIATE (not DEFERRED) because every caller here intends to write. It
    takes the write lock up front, so a competing writer waits out
    ``busy_timeout`` instead of failing at upgrade time — the difference
    between "two logins raced and both worked" and "one login got 500:
    database is locked".
    """
    ensure_schema()
    conn = _open(database_path())
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()

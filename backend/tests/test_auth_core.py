"""Unit U7 (roadmap 2.5) — the auth core: SQLite schema, password and token
discipline, sessions, and ``/api/auth``.

Every test points ``services.db.DB_PATH`` at its OWN tmp file. The session-wide
conftest fixture already redirects it away from the developer's
``runtime/db/app.db``; this per-test fixture goes further so no test can see
another test's accounts — an auth suite that shares a database is one ordering
change away from passing for the wrong reason.

scrypt's cost is lowered to N=2^8 for speed everywhere EXCEPT the tests that
are specifically about cost parameters. That is safe here precisely because the
parameters live inside the hash string: the production value is exercised by
``test_password_hash_uses_shipped_cost_by_default``.

The last test in this file is a scope guard pinning the EXACT set of
authenticated routes. It fails loudly the moment a route grows an auth
dependency, so widening the set is always a deliberate edit: U7 protected
nothing, U8 added ``POST /api/assistant/agent/approve`` and nothing else.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import main
from routers import auth as auth_router
from services import auth, db

GOOD_PASSWORD = "correct-horse-battery"
OTHER_PASSWORD = "another-valid-password"


@pytest.fixture(autouse=True)
def auth_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "db" / "app.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    # Cheap scrypt: this suite runs hundreds of derivations and none of them is
    # about how long one takes.
    monkeypatch.setattr(auth, "SCRYPT_N", 1 << 8)
    monkeypatch.setattr(auth, "_dummy_hash_cache", {})
    return path


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main.app)


def _make_user(username: str = "yunha", role: str = "admin", password: str = GOOD_PASSWORD) -> dict:
    return auth.create_user(username, password, role, display_name="이윤하")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def test_schema_is_created_idempotently(auth_db: Path):
    assert db.schema_version() == 0  # nothing on disk yet

    first = db.ensure_schema()
    second = db.ensure_schema(force=True)  # force skips the process memo

    # v1 = the auth core (U7), v2 = workspace_members (P④). The number is
    # asserted rather than merely compared so the ladder cannot silently skip a
    # step -- adding a migration means updating this line on purpose.
    assert first == second == db.SCHEMA_VERSION == 2
    assert db.schema_version() == 2
    with db.connect() as conn:
        rows = conn.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()
        assert rows["n"] == 1, "the version ledger must hold exactly one row"


def test_schema_holds_exactly_the_tables_a_unit_declared():
    """The exact table list, updated deliberately per unit.

    U7 owned two (``users``, ``auth_sessions``); P④ adds exactly one
    (``workspace_members``). The runs-registry migration is still a LATER unit
    — if a table appears here that no unit named, somebody smuggled it in, and
    a half-migrated registry with two sources of truth is strictly worse than
    the file-only version that works today (``services/db.py``'s docstring).
    """
    with db.connect() as conn:
        names = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
    assert names == {"schema_version", "users", "auth_sessions", "workspace_members"}


def test_wal_and_foreign_keys_are_on():
    with db.connect() as conn:
        assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
        assert int(conn.execute("PRAGMA foreign_keys").fetchone()[0]) == 1
        assert int(conn.execute("PRAGMA busy_timeout").fetchone()[0]) == db.BUSY_TIMEOUT_MS


def test_a_newer_database_is_refused_rather_than_opened(auth_db: Path):
    """The COLD path: the first open of a path this process has never seen."""
    db.ensure_schema()
    with db.connect() as conn:
        conn.execute("UPDATE schema_version SET version = 99 WHERE id = 1")
    with pytest.raises(db.DatabaseError, match="newer than this build"):
        db.ensure_schema(force=True)


def test_a_newer_database_is_refused_on_the_warm_path_too(auth_db: Path):
    """The refusal must survive the process memo — no ``force=`` anywhere.

    The shape this guards is a rolling upgrade: a live backend memoizes the
    file at v2, newer tooling (or a second backend) takes it to v3, and every
    later ``connect()`` in the OLD process used to return the cached constant
    and keep writing into a schema it knows nothing about. Silently: no error,
    no log, rows landing in a database whose constraints were never checked
    against them. ``force=True`` made the refusal fire, but nothing on a
    request path passes ``force=True``, so the guard protected only the very
    first open of the file.
    """
    db.ensure_schema()  # memoize this path at the current version
    with db.connect() as conn:
        conn.execute("UPDATE schema_version SET version = 3 WHERE id = 1")

    with pytest.raises(db.DatabaseError, match="newer than this build"):
        db.ensure_schema()

    with pytest.raises(db.DatabaseError, match="newer than this build"):
        with db.connect() as conn:
            conn.execute("SELECT 1")

    with pytest.raises(db.DatabaseError, match="newer than this build"):
        with db.write_transaction() as conn:
            conn.execute(
                "INSERT INTO users (username, display_name, role, password_hash, created_at, disabled) "
                "VALUES ('smuggled', 'smuggled', 'admin', 'x', 'now', 0)"
            )

    # The refusal is worth nothing if the write got through on the way out.
    conn = sqlite3.connect(str(auth_db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    finally:
        conn.close()


def test_the_memo_still_skips_the_migration_ladder(auth_db: Path, monkeypatch: pytest.MonkeyPatch):
    """The guard is a version READ, not a migration run.

    The memo exists so a login does not re-run migrations; re-validating the
    version costs ~4.9 µs on a connection that is already open, and that is the
    only thing the warm path is allowed to do. If this test starts failing, the
    guard has grown into a full ladder run on every connect().
    """
    db.ensure_schema()
    ran: list[int] = []
    real_migration = db._MIGRATIONS[db.SCHEMA_VERSION]

    def spy(conn):
        ran.append(db.SCHEMA_VERSION)
        real_migration(conn)

    monkeypatch.setitem(db._MIGRATIONS, db.SCHEMA_VERSION, spy)

    for _ in range(5):
        with db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        with db.write_transaction() as conn:
            conn.execute("SELECT 1").fetchone()
        db.ensure_schema()

    assert ran == [], "the warm path re-ran migrations instead of reading the version"


def test_a_database_deleted_underneath_a_live_process_is_rebuilt(auth_db: Path):
    """The memo must not outlive the file it describes.

    This used to be a ``path.exists()`` check inside ``ensure_schema``; it is
    now the ``recorded version < SCHEMA_VERSION`` branch, which covers the same
    case and additionally catches the file being REPLACED by an older one —
    something an existence check cannot see.
    """
    db.ensure_schema()
    _make_user()
    for leftover in auth_db.parent.glob("app.db*"):  # the -wal and -shm too
        leftover.unlink()

    assert db.ensure_schema() == db.SCHEMA_VERSION  # no force=

    with db.connect() as conn:
        names = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"users", "auth_sessions", "workspace_members"} <= names
        assert conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"] == 0


def test_write_transaction_re_checks_the_version_after_taking_the_write_lock(
    auth_db: Path, monkeypatch: pytest.MonkeyPatch
):
    """The NARROW race, closed separately from the wide one above.

    ``test_a_newer_database_is_refused_on_the_warm_path_too`` proves the wide
    version: a migration that already landed and COMMITTED before
    ``write_transaction()`` is even called is caught by the outer check
    (``_ensure_schema_using``, which runs before ``BEGIN IMMEDIATE``). That
    test does not exercise the narrower gap ``write_transaction``'s own
    docstring names: a migration that lands strictly BETWEEN the outer check
    returning and ``BEGIN IMMEDIATE`` acquiring the write lock. Before this
    fix, nothing re-read the version after that point, so a write issued in
    that gap landed against a schema this build never checked.

    Simulated here by making the outer check's return the trigger for the
    race: ``_ensure_schema_using`` is wrapped so that the instant it returns
    (schema confirmed current), a SEPARATE connection commits a version bump
    to v3 -- exactly the shape of a second process's migration racing this
    one. The write inside ``write_transaction`` must never land.
    """
    db.ensure_schema()
    real_ensure = db._ensure_schema_using

    def racing_ensure(conn, path, *, force=False):
        result = real_ensure(conn, path, force=force)
        other = sqlite3.connect(str(path))
        try:
            other.execute("UPDATE schema_version SET version = 3 WHERE id = 1")
            other.commit()
        finally:
            other.close()
        return result

    monkeypatch.setattr(db, "_ensure_schema_using", racing_ensure)

    with pytest.raises(db.DatabaseError, match="newer than this build"):
        with db.write_transaction() as conn:
            conn.execute(
                "INSERT INTO users (username, display_name, role, password_hash, created_at, disabled) "
                "VALUES ('smuggled', 'smuggled', 'admin', 'x', 'now', 0)"
            )

    # The refusal is worth nothing if the write got through on the way out.
    raw = sqlite3.connect(str(auth_db))
    try:
        assert raw.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    finally:
        raw.close()


def test_schema_version_refuses_a_newer_database_too(auth_db: Path):
    """``schema_version()`` is a public entry point (a diagnostic "is this
    database safe" pre-flight); it must run the SAME newer-than-this-build
    guard every other entry point in the module runs, not hand back the raw
    newer integer with no error while ``connect``/``write_transaction``/
    ``ensure_schema`` would all refuse the identical file."""
    db.ensure_schema()
    with db.connect() as conn:
        conn.execute("UPDATE schema_version SET version = 99 WHERE id = 1")

    with pytest.raises(db.DatabaseError, match="newer than this build"):
        db.schema_version()


def test_role_check_constraint_rejects_an_unknown_role():
    """The Python guard is not the only guard — direct SQL is refused too."""
    with pytest.raises(sqlite3.IntegrityError):
        with db.write_transaction() as conn:
            conn.execute(
                "INSERT INTO users (username, display_name, role, password_hash, created_at, disabled) "
                "VALUES ('x', 'x', 'superuser', 'x', 'now', 0)"
            )


def test_write_transaction_rolls_back_on_error():
    with pytest.raises(RuntimeError):
        with db.write_transaction() as conn:
            conn.execute(
                "INSERT INTO users (username, display_name, role, password_hash, created_at, disabled) "
                "VALUES ('ghost', 'ghost', 'admin', 'x', 'now', 0)"
            )
            raise RuntimeError("boom")
    assert auth.get_user_by_username("ghost") is None


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def test_password_round_trip_and_wrong_password_rejected():
    encoded = auth.hash_password(GOOD_PASSWORD)
    assert auth.verify_password(GOOD_PASSWORD, encoded) is True
    assert auth.verify_password(OTHER_PASSWORD, encoded) is False
    assert auth.verify_password("", encoded) is False


def test_hash_format_is_the_documented_parameterized_string():
    encoded = auth.hash_password(GOOD_PASSWORD, n=1 << 9, r=8, p=1)
    scheme, n_text, r_text, p_text, salt, digest = encoded.split("$")
    assert (scheme, n_text, r_text, p_text) == ("scrypt", "512", "8", "1")
    assert salt and digest and "$" not in salt and "$" not in digest
    parsed = auth.parse_password_hash(encoded)
    assert parsed["n"] == 512 and len(parsed["salt"]) == auth.SCRYPT_SALT_BYTES
    assert len(parsed["hash"]) == auth.SCRYPT_KEY_BYTES


def test_a_hash_written_with_different_cost_still_verifies():
    """Parameter evolution: the whole reason N/r/p live inside the string."""
    legacy = auth.hash_password(GOOD_PASSWORD, n=1 << 6, r=4, p=1)
    current = auth.hash_password(GOOD_PASSWORD, n=1 << 10, r=8, p=1)

    assert auth.verify_password(GOOD_PASSWORD, legacy) is True
    assert auth.verify_password(GOOD_PASSWORD, current) is True
    assert auth.verify_password(OTHER_PASSWORD, legacy) is False
    assert auth.needs_rehash(legacy) is True
    assert auth.needs_rehash(current) is False  # module policy is N=2^8 under test


def test_password_hash_uses_shipped_cost_by_default(monkeypatch: pytest.MonkeyPatch):
    """The ONE test that pays for real parameters, so the shipped policy is
    actually exercised instead of only the lowered test value."""
    monkeypatch.setattr(auth, "SCRYPT_N", 1 << 14)
    encoded = auth.hash_password(GOOD_PASSWORD)
    parsed = auth.parse_password_hash(encoded)
    assert (parsed["n"], parsed["r"], parsed["p"]) == (1 << 14, 8, 1)
    assert auth.verify_password(GOOD_PASSWORD, encoded) is True


def test_every_hash_gets_its_own_salt():
    first = auth.hash_password(GOOD_PASSWORD)
    second = auth.hash_password(GOOD_PASSWORD)
    assert first != second
    assert auth.parse_password_hash(first)["salt"] != auth.parse_password_hash(second)["salt"]


@pytest.mark.parametrize(
    "broken",
    ["", "not-a-hash", "scrypt$8$8$1$onlyfour", "bcrypt$1$1$1$c2FsdA$aGFzaA", "scrypt$x$8$1$c2E$aGE"],
)
def test_a_malformed_hash_fails_the_login_instead_of_raising(broken: str):
    assert auth.parse_password_hash(broken) is None
    assert auth.verify_password(GOOD_PASSWORD, broken) is False


def test_absurd_cost_parameters_are_refused_not_attempted():
    """A hand-edited row must not be able to allocate the box out of memory."""
    with pytest.raises(ValueError, match="ceiling"):
        auth.hash_password(GOOD_PASSWORD, n=1 << 24, r=8, p=1)
    assert auth.verify_password(GOOD_PASSWORD, f"scrypt${1 << 24}$8$1$c2FsdA$aGFzaA") is False


@pytest.mark.parametrize("bad", ["", "   ", "short", "a" * (auth.MIN_PASSWORD_CHARS - 1)])
def test_weak_passwords_are_refused(bad: str):
    with pytest.raises(auth.WeakPasswordError):
        auth.create_user("someone", bad, "modeler")
    assert auth.get_user_by_username("someone") is None


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def test_a_fresh_install_has_no_credential_of_any_kind():
    """The approved policy, asserted: an empty install lets NOBODY in."""
    assert auth.list_users() == []
    assert auth.count_admins() == 0
    for username, password in (("admin", "admin"), ("admin", "password"), ("prism", "prism")):
        assert auth.authenticate(username, password) is None


def test_usernames_are_case_insensitive_and_unique():
    _make_user("Yunha")
    assert auth.get_user_by_username("YUNHA")["username"] == "Yunha"
    with pytest.raises(auth.UserExistsError):
        auth.create_user("yunha", OTHER_PASSWORD, "modeler")
    assert len(auth.list_users()) == 1


@pytest.mark.parametrize("bad", ["", " ", "a", "한글아이디", "has space", "-leading", "x" * 65])
def test_invalid_usernames_are_refused(bad: str):
    with pytest.raises(auth.InvalidUsernameError):
        auth.create_user(bad, GOOD_PASSWORD, "modeler")


def test_unknown_role_is_refused_before_the_write():
    with pytest.raises(auth.InvalidRoleError):
        auth.create_user("someone", GOOD_PASSWORD, "superuser")
    assert auth.list_users() == []


def test_list_users_never_carries_a_password_hash():
    _make_user()
    users = auth.list_users()
    assert users and "password_hash" not in users[0]
    assert set(users[0]) == {"id", "username", "display_name", "role", "created_at", "disabled"}


def test_display_name_defaults_to_the_username_and_keeps_utf8():
    plain = auth.create_user("kim", GOOD_PASSWORD, "reviewer")
    assert plain["display_name"] == "kim"
    named = auth.create_user("lee", GOOD_PASSWORD, "reviewer", display_name="이윤하")
    assert named["display_name"] == "이윤하"


def test_authenticate_accepts_the_right_password_only():
    _make_user()
    assert auth.authenticate("yunha", GOOD_PASSWORD)["role"] == "admin"
    assert auth.authenticate("YUNHA", GOOD_PASSWORD)["username"] == "yunha"
    assert auth.authenticate("yunha", OTHER_PASSWORD) is None
    assert auth.authenticate("nobody", GOOD_PASSWORD) is None


def test_a_disabled_account_cannot_authenticate():
    # A MODELER, not the admin `_make_user` defaults to: P④ refuses to disable
    # the last enabled admin, and this test is about the disabled FLAG, not
    # about who is allowed to hold it.
    _make_user(role="modeler")
    auth.set_disabled("yunha", True)
    assert auth.authenticate("yunha", GOOD_PASSWORD) is None
    auth.set_disabled("yunha", False)
    assert auth.authenticate("yunha", GOOD_PASSWORD) is not None


def test_count_admins_ignores_disabled_admins():
    """``count_admins`` excludes disabled admins — including the 0 case.

    P④ made the 0 case unreachable THROUGH ``set_disabled`` (it refuses to
    disable the last enabled admin), so the final leg writes the row directly.
    That state still exists in the world: an install whose only admin was
    disabled before the guard shipped must still be able to bootstrap with
    ``manage_users.py create-admin``, and that command's refusal reads this
    count.
    """
    _make_user("boss", role="admin")
    _make_user("deputy", role="admin")
    assert auth.count_admins() == 2

    auth.set_disabled("boss", True)
    assert auth.count_admins() == 1
    assert auth.count_admins(include_disabled=True) == 2

    with db.write_transaction() as conn:
        conn.execute("UPDATE users SET disabled = 1 WHERE role = 'admin'")
    assert auth.count_admins() == 0, "an operator who disabled the last admin must be able to bootstrap"
    assert auth.count_admins(include_disabled=True) == 2


def test_the_last_enabled_admin_cannot_be_disabled_or_demoted():
    """P④: the admin UI is the only place membership can be granted, and it is
    behind the admin role — so an admin who removes the last admin has locked
    every account and project-access surface behind a role nobody holds. Both
    doors are guarded, and both open again the moment a second admin exists."""
    _make_user("boss", role="admin")

    with pytest.raises(auth.LastAdminError):
        auth.set_disabled("boss", True)
    with pytest.raises(auth.LastAdminError):
        auth.set_role("boss", "reviewer")
    assert auth.get_user_by_username("boss")["role"] == "admin"
    assert auth.get_user_by_username("boss")["disabled"] is False

    _make_user("deputy", role="admin")
    assert auth.set_role("boss", "reviewer")["role"] == "reviewer"
    # ...and now `deputy` is the last one, so it inherits the refusal.
    with pytest.raises(auth.LastAdminError):
        auth.set_disabled("deputy", True)


def test_set_role_validates_and_leaves_sessions_alive():
    """A role change takes effect on the next request because
    ``resolve_session`` re-reads ``users.role`` — so there is nothing to revoke,
    and revoking would log somebody out of the pages they may still read."""
    user = _make_user("boss", role="admin")
    _make_user("deputy", role="admin")
    token, _ = auth.create_session(user["id"])

    with pytest.raises(auth.InvalidRoleError):
        auth.set_role("boss", "superuser")
    with pytest.raises(auth.UnknownUserError):
        auth.set_role("ghost", "modeler")

    assert auth.set_role("boss", "modeler")["role"] == "modeler"
    resolved = auth.resolve_session(token)
    assert resolved is not None and resolved["user"]["role"] == "modeler"


# ---------------------------------------------------------------------------
# Tokens and sessions
# ---------------------------------------------------------------------------

def test_the_raw_token_is_nowhere_in_the_database_bytes(auth_db: Path):
    """A leaked app.db must not be a bag of usable bearer tokens."""
    user = _make_user()
    token, session = auth.create_session(user["id"])

    blobs = [path.read_bytes() for path in auth_db.parent.iterdir() if path.is_file()]
    assert blobs, "expected at least the database file"
    haystack = b"".join(blobs)
    assert token.encode() not in haystack, "the RAW token reached the database file"
    assert auth.hash_token(token).encode() in haystack, "the token HASH should be there"
    assert session["id"].encode() in haystack


def test_a_live_token_resolves_to_its_user_and_session():
    user = _make_user()
    token, session = auth.create_session(user["id"])
    resolved = auth.resolve_session(token)
    assert resolved["user"]["username"] == "yunha"
    assert resolved["user"]["display_name"] == "이윤하"
    assert resolved["session"]["id"] == session["id"]
    # The user's created_at, not the session's -- the join aliases both.
    assert resolved["user"]["created_at"] == auth.get_user_by_username("yunha")["created_at"]


@pytest.mark.parametrize("token", [None, "", "not-a-real-token", "x" * 43])
def test_a_bogus_token_resolves_to_nothing(token):
    _make_user()
    assert auth.resolve_session(token) is None


def test_expiry_closes_a_session():
    user = _make_user()
    token, _ = auth.create_session(user["id"], ttl_hours=-1)
    assert auth.resolve_session(token) is None


def test_revocation_closes_a_session():
    user = _make_user()
    token, _ = auth.create_session(user["id"])
    assert auth.resolve_session(token) is not None
    assert auth.revoke_session(token) is True
    assert auth.resolve_session(token) is None
    # Idempotent, and no oracle: a second revoke reports False like an unknown one.
    assert auth.revoke_session(token) is False
    assert auth.revoke_session("never-existed") is False


def test_disabling_a_user_closes_their_live_sessions():
    # Modeler, for the same reason as test_a_disabled_account_cannot_authenticate.
    user = _make_user(role="modeler")
    token, _ = auth.create_session(user["id"])
    auth.set_disabled("yunha", True)
    assert auth.resolve_session(token) is None


def test_resetting_a_password_revokes_every_session():
    user = _make_user()
    first, _ = auth.create_session(user["id"])
    second, _ = auth.create_session(user["id"])
    auth.set_password("yunha", OTHER_PASSWORD)
    assert auth.resolve_session(first) is None
    assert auth.resolve_session(second) is None
    assert auth.authenticate("yunha", OTHER_PASSWORD) is not None


def test_last_seen_is_throttled_then_refreshed(monkeypatch: pytest.MonkeyPatch):
    user = _make_user()
    token, _ = auth.create_session(user["id"])
    first = auth.resolve_session(token)["session"]["last_seen_at"]
    assert auth.resolve_session(token)["session"]["last_seen_at"] == first

    monkeypatch.setattr(auth, "LAST_SEEN_REFRESH_SECONDS", 0.0)
    auth.resolve_session(token)
    assert auth.resolve_session(token)["session"]["last_seen_at"] != first


def test_purge_expired_sessions_keeps_the_live_ones():
    user = _make_user()
    live, _ = auth.create_session(user["id"])
    auth.create_session(user["id"], ttl_hours=-48)
    assert auth.purge_expired_sessions(keep_hours=24.0) == 1
    assert auth.resolve_session(live) is not None


def test_unknown_users_and_wrong_passwords_both_burn_a_derivation():
    """Not a timing assertion (flaky by nature) — a behavioural one: the
    unknown-user path really does call the KDF, so the two branches cost the
    same order of magnitude."""
    _make_user()
    calls: list[tuple] = []
    real_derive = auth._derive

    def counting(*args, **kwargs):
        calls.append(args)
        return real_derive(*args, **kwargs)

    original = auth._derive
    auth._derive = counting
    try:
        assert auth.authenticate("nobody-here", GOOD_PASSWORD) is None
        unknown_user_calls = len(calls)
        assert auth.authenticate("yunha", OTHER_PASSWORD) is None
        wrong_password_calls = len(calls) - unknown_user_calls
    finally:
        auth._derive = original

    assert unknown_user_calls >= 1
    assert wrong_password_calls >= 1


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def test_login_sets_an_httponly_lax_cookie_and_whoami_reads_it(client: TestClient):
    _make_user()
    response = client.post("/api/auth/login", json={"username": "yunha", "password": GOOD_PASSWORD})
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "admin"
    assert "password" not in response.text and "hash" not in response.text

    raw_cookie = response.headers["set-cookie"]
    assert auth_router.SESSION_COOKIE_NAME in raw_cookie
    assert "HttpOnly" in raw_cookie
    assert "SameSite=lax" in raw_cookie.replace("samesite=lax", "SameSite=lax")
    assert "Secure" not in raw_cookie  # off by default; on behind TLS

    who = client.get("/api/auth/whoami")
    assert who.status_code == 200
    assert who.json()["user"]["display_name"] == "이윤하"
    assert who.json()["session"]["expires_at"]


def test_a_second_login_does_not_kill_the_first(client: TestClient):
    user = _make_user()
    desk_token, _ = auth.create_session(user["id"])
    response = client.post("/api/auth/login", json={"username": "yunha", "password": GOOD_PASSWORD})
    laptop_token = response.cookies[auth_router.SESSION_COOKIE_NAME]

    assert laptop_token != desk_token
    assert auth.resolve_session(desk_token) is not None
    assert auth.resolve_session(laptop_token) is not None


def test_unknown_user_and_wrong_password_are_the_same_401(client: TestClient):
    """The account-enumeration oracle test: identical status, identical body."""
    # Modeler: this test disables the account mid-way, and P④ refuses to
    # disable the last enabled admin.
    _make_user(role="modeler")
    unknown = client.post("/api/auth/login", json={"username": "ghost", "password": GOOD_PASSWORD})
    wrong = client.post("/api/auth/login", json={"username": "yunha", "password": OTHER_PASSWORD})
    auth.set_disabled("yunha", True)
    disabled = client.post("/api/auth/login", json={"username": "yunha", "password": GOOD_PASSWORD})

    assert unknown.status_code == wrong.status_code == disabled.status_code == 401
    assert unknown.json() == wrong.json() == disabled.json()
    assert "yunha" not in unknown.text and "ghost" not in unknown.text
    assert "set-cookie" not in {key.lower() for key in unknown.headers}


def test_the_401_message_follows_the_requested_language(client: TestClient):
    payload = {"username": "ghost", "password": GOOD_PASSWORD}
    korean = client.post("/api/auth/login", json=payload).json()["detail"]
    english = client.post("/api/auth/login", json={**payload, "language": "en"}).json()["detail"]
    japanese = client.post("/api/auth/login", json={**payload, "language": "ja"}).json()["detail"]
    assert korean == "아이디 또는 비밀번호가 올바르지 않습니다."
    assert english == "Incorrect username or password."
    assert japanese != korean and japanese != english
    # An unknown code falls back to Korean, like every other string in the product.
    assert client.post("/api/auth/login", json={**payload, "language": "de"}).json()["detail"] == korean


def test_credentials_must_arrive_as_a_json_body(client: TestClient):
    """Never form-encoded, never from the URL — a password in a query string
    lands in access logs, history and the next request's Referer."""
    _make_user()
    from_query = client.post(f"/api/auth/login?username=yunha&password={GOOD_PASSWORD}")
    from_form = client.post("/api/auth/login", data={"username": "yunha", "password": GOOD_PASSWORD})
    assert from_query.status_code == 422
    assert from_form.status_code == 422
    assert "set-cookie" not in {key.lower() for key in from_query.headers}


def test_whoami_is_401_without_a_session(client: TestClient):
    response = client.get("/api/auth/whoami")
    assert response.status_code == 401
    assert response.json()["detail"] == "로그인이 필요합니다."
    assert client.get("/api/auth/whoami?language=en").json()["detail"] == "Authentication is required."


def test_logout_revokes_the_session_and_whoami_goes_401(client: TestClient):
    _make_user()
    login = client.post("/api/auth/login", json={"username": "yunha", "password": GOOD_PASSWORD})
    token = login.cookies[auth_router.SESSION_COOKIE_NAME]
    assert client.get("/api/auth/whoami").status_code == 200

    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200 and logout.json() == {"revoked": True}
    assert auth.resolve_session(token) is None
    assert client.get("/api/auth/whoami").status_code == 401
    # Idempotent and non-oracular for a caller with nothing to revoke.
    assert client.post("/api/auth/logout").json() == {"revoked": False}


def test_a_bearer_token_authenticates_without_a_cookie(client: TestClient):
    user = _make_user()
    token, _ = auth.create_session(user["id"])
    response = client.get("/api/auth/whoami", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200 and response.json()["user"]["username"] == "yunha"
    assert client.get("/api/auth/whoami", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_login_works_without_a_workspace_header(client: TestClient):
    """Unit 1.4's middleware must not stand between a browser and the login
    page: an ABSENT X-Workspace-Id resolves to the default workspace, it does
    not 404."""
    _make_user()
    response = client.post(
        "/api/auth/login", json={"username": "yunha", "password": GOOD_PASSWORD}
    )
    assert response.status_code == 200
    assert client.get("/api/auth/whoami").status_code == 200


def test_a_stale_workspace_header_still_404s_before_auth(client: TestClient):
    """Documented, not fixed: the 1.4 middleware fails closed on a PRESENT but
    unknown workspace id, and that ordering predates this unit. U9's login
    screen has no reason to send the header at all."""
    _make_user()
    response = client.post(
        "/api/auth/login",
        json={"username": "yunha", "password": GOOD_PASSWORD},
        headers={"X-Workspace-Id": "deadbeefdead"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "workspace_not_found"


# ---------------------------------------------------------------------------
# The dependency U8 will enforce with — exercised on a throwaway app
# ---------------------------------------------------------------------------

@pytest.fixture()
def guarded_client() -> TestClient:
    """A disposable app that USES the dependencies. Nothing in the real app is
    wired to them in U7 — see the scope guard at the bottom of this file."""
    app = FastAPI()

    @app.get("/any")
    def any_user(user: dict = Depends(auth_router.current_user)):
        return {"username": user["username"]}

    @app.get("/admin-only")
    def admin_only(user: dict = Depends(auth_router.require_role("admin"))):
        return {"username": user["username"]}

    @app.get("/review")
    def review(user: dict = Depends(auth_router.require_role("reviewer", "admin"))):
        return {"username": user["username"]}

    @app.get("/maybe")
    def maybe(user: dict | None = Depends(auth_router.optional_current_user)):
        return {"user": user}

    return TestClient(app)


def _bearer(username: str, role: str) -> dict[str, str]:
    user = auth.create_user(username, GOOD_PASSWORD, role)
    token, _ = auth.create_session(user["id"])
    return {"Authorization": f"Bearer {token}"}


def test_current_user_dependency_gates_on_a_session(guarded_client: TestClient):
    assert guarded_client.get("/any").status_code == 401
    headers = _bearer("modeler1", "modeler")
    assert guarded_client.get("/any", headers=headers).json() == {"username": "modeler1"}


def test_require_role_allows_only_the_named_roles(guarded_client: TestClient):
    modeler = _bearer("modeler1", "modeler")
    reviewer = _bearer("reviewer1", "reviewer")
    admin = _bearer("admin1", "admin")

    assert guarded_client.get("/admin-only").status_code == 401
    assert guarded_client.get("/admin-only", headers=modeler).status_code == 403
    assert guarded_client.get("/admin-only", headers=reviewer).status_code == 403
    assert guarded_client.get("/admin-only", headers=admin).status_code == 200

    assert guarded_client.get("/review", headers=modeler).status_code == 403
    assert guarded_client.get("/review", headers=reviewer).status_code == 200
    # admin is allowed here only because the route NAMED it -- no implicit
    # superuser, or "who reviewed this" stops being answerable.
    assert guarded_client.get("/review", headers=admin).status_code == 200


def test_require_role_403_is_localized(guarded_client: TestClient):
    modeler = _bearer("modeler1", "modeler")
    korean = guarded_client.get("/admin-only", headers=modeler).json()["detail"]
    english = guarded_client.get("/admin-only?language=en", headers=modeler).json()["detail"]
    assert korean == "이 작업에 필요한 권한이 없습니다."
    assert english == "You do not have permission for this action."


def test_require_role_refuses_a_role_name_that_does_not_exist():
    """A typo'd role would otherwise produce a route nobody can ever call."""
    with pytest.raises(ValueError, match="Unknown role"):
        auth_router.require_role("superuser")


def test_optional_current_user_never_raises(guarded_client: TestClient):
    assert guarded_client.get("/maybe").json() == {"user": None}
    headers = _bearer("modeler1", "modeler")
    assert guarded_client.get("/maybe", headers=headers).json()["user"]["role"] == "modeler"


def test_a_role_dependency_costs_one_database_read(guarded_client: TestClient, monkeypatch):
    """The request-scoped memo: two dependencies on one route must not become
    two session lookups."""
    headers = _bearer("admin1", "admin")
    calls: list[str | None] = []
    real = auth.resolve_session
    monkeypatch.setattr(auth, "resolve_session", lambda token: (calls.append(token), real(token))[1])

    assert guarded_client.get("/admin-only", headers=headers).status_code == 200
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------

def test_two_threads_can_log_in_and_validate_against_one_wal_database():
    """WAL + busy_timeout + BEGIN IMMEDIATE, exercised rather than asserted in
    a docstring. The failure this guards against is ``sqlite3.OperationalError:
    database is locked`` under two concurrent logins."""
    db.ensure_schema()
    users = [
        auth.create_user(f"worker{index}", GOOD_PASSWORD, "modeler") for index in range(2)
    ]
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(users))

    def hammer(user: dict) -> None:
        try:
            barrier.wait(timeout=10)
            for _ in range(15):
                authenticated = auth.authenticate(user["username"], GOOD_PASSWORD)
                assert authenticated is not None
                token, _ = auth.create_session(authenticated["id"])
                resolved = auth.resolve_session(token)
                assert resolved is not None
                assert resolved["user"]["username"] == user["username"]
                assert auth.revoke_session(token) is True
        except BaseException as exc:  # noqa: BLE001 - re-raised on the main thread
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(user,)) for user in users]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive()

    assert not errors, f"concurrent auth failed: {errors[0]!r}"
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM auth_sessions").fetchone()["n"] == 30


# ---------------------------------------------------------------------------
# Scope guard — the ledger of what is authenticated, and when it became so
# ---------------------------------------------------------------------------

def _dependency_callables(dependant) -> set:
    found = {dependant.call} if dependant.call is not None else set()
    for child in dependant.dependencies:
        found |= _dependency_callables(child)
    return found


def test_the_authenticated_routes_are_exactly_the_ones_a_unit_declared():
    """The exact set of authenticated routes, updated deliberately per unit.

    (Renamed from ``test_no_existing_endpoint_is_protected_by_this_unit`` by
    U8, which is the first unit to protect one: the old name asserted "none",
    and a guard whose NAME is false is a guard nobody trusts. Same test, same
    mechanism, one more route.)

    U7 delivered the machinery and wired it NOWHERE: the only route that
    depended on ``current_user`` was ``/api/auth/whoami``, the machinery
    itself.

    **U8 (roadmap 2.5) adds exactly one: POST /api/assistant/agent/approve.**
    That is the whole unit — the approval gate demands a signed-in
    ``reviewer``/``admin`` (``routers/assistant.require_approver``) so the
    audit DOCX can name who approved a change. Nothing else was widened; in
    particular the audit EXPORT endpoints stay open, because reading the
    record of a decision is not making one.

    **P④ adds the whole ``/api/admin`` router plus ``/api/workspaces``.**

    * every ``/api/admin/*`` route, through ONE router-level
      ``require_role("admin")`` — user administration and workspace
      membership, which is the browser replacement for
      ``backend/tools/manage_users.py``;
    * ``/api/workspaces``, which is auth-AWARE rather than auth-REQUIRED: the
      GET takes ``optional_current_user`` so an anonymous caller still gets
      the default workspace (and only that), while the POST takes
      ``current_user`` because a project has to be created BY somebody in
      order to be granted to them.

    Note what is deliberately NOT in this set: the membership 403 on every
    other data route. That is enforced structurally in
    ``main.WorkspaceScopeMiddleware``, not by a route dependency — which is
    exactly why it covers routes nobody has written yet.
    ``tests/test_workspace_membership.py`` is the ledger for that half.

    When a later unit legitimately protects more, UPDATE this set — do not
    delete the test. The list is the record of what became authenticated and
    when.
    """
    guarded = set()
    for route in main.app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        calls = _dependency_callables(dependant)
        if auth_router.current_user in calls or auth_router.optional_current_user in calls:
            guarded.add(getattr(route, "path", str(route)))

    assert guarded == {
        "/api/auth/whoami",
        "/api/assistant/agent/approve",
        "/api/workspaces",
        "/api/admin/users",
        "/api/admin/users/{user_id}/password",
        "/api/admin/users/{user_id}/disabled",
        "/api/admin/users/{user_id}/role",
        "/api/admin/workspaces/{workspace_id}/members",
        "/api/admin/workspaces/{workspace_id}/members/{user_id}",
    }

"""Users, passwords and login sessions — the auth CORE (roadmap 2.5, unit U7).

This module owns the *machinery*. It protects nothing by itself: U7 ships the
users table, the password discipline, the session lifecycle and the endpoints
in ``routers/auth.py``, and wires them into exactly zero existing routes. U8
binds an authenticated name to the approval record (and to the audit DOCX's
approver column); U9 builds the login UI. Landing the machinery first is
deliberate — an enforcement switch flipped in the same change that introduces
the hashing format is a change nobody can review.

**Approved policy (user, 2026-08-06, recorded on roadmap row 2.5):** the only
bootstrap is an operator running ``backend/tools/manage_users.py create-admin``
and typing a password. There is NO default credential in this file, that CLI,
the schema, or any fixture — not "admin/admin", not a seeded demo user, not a
dev-only fallback. A product that ships a known password has no audit trail,
only the appearance of one.

**Password format.** ``scrypt$N$r$p$<salt>$<hash>`` — the cost parameters live
*in* the string, so raising them later is a per-user lazy upgrade rather than a
flag day: an old hash keeps verifying with the parameters it was written with,
and :func:`needs_rehash` says when a hash is below current policy. Salt and
hash are urlsafe-base64 without padding (the alphabet has no ``$``, so the
field split is unambiguous). Comparison is :func:`hmac.compare_digest`, never
``==``.

**Token format.** A login token is ``secrets.token_urlsafe(32)`` — 256 bits
from the OS CSPRNG. The database stores only its SHA-256 hex digest, so a
leaked ``app.db`` yields no usable bearer token. No salt and no KDF on this
one, deliberately: the input already has 256 bits of entropy, so there is
nothing for a dictionary attack to chew on, and a per-request scrypt would put
~60 ms on every authenticated call. (The PASSWORD path is the one that must be
slow, and it is.)

**Sessions.** Row-backed, so revocation is real: logout writes ``revoked_at``
and the next lookup fails closed. :func:`resolve_session` checks, in order,
that the token hash exists, that the session is not revoked, that it has not
expired, and that the owning user is not disabled — a disabled account's live
session dies on its next request rather than at its next expiry.

**Workspace independence.** Nothing here reads or binds a workspace. Auth is a
property of the person, not of the project they happen to be looking at; a
login that only worked inside one workspace would make U8's "who approved
this" answerable only in that workspace. See ``services/db.py`` for why the
file lives in ``runtime/db/`` and not under ``runtime/workspaces/<id>/``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from services import db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Policy constants (module-level so tests can lower the cost, and so an
# operator reading this file can see the whole policy in one screen)
# ---------------------------------------------------------------------------

#: Roles, in the order the UI should offer them. Mirrored by the CHECK
#: constraint in ``services/db.py``'s v1 migration — both, on purpose: the
#: constraint is the one that cannot be bypassed, this one produces a readable
#: error before the write is attempted.
ROLES: tuple[str, ...] = ("modeler", "reviewer", "admin")

#: scrypt cost. N=2^14 with r=8 costs ~16 MiB and ~60 ms on the dev box — the
#: usual interactive-login operating point. Written INTO every new hash, so
#: raising these numbers does not invalidate existing passwords.
SCRYPT_N = 1 << 14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_SALT_BYTES = 16
SCRYPT_KEY_BYTES = 32

#: Upper bound on the memory a *stored* hash may ask for when verifying.
#: Parameters come out of the database, and a row with an absurd N would
#: otherwise be a one-row denial of service against the login endpoint.
MAX_SCRYPT_MEMORY_BYTES = 512 * 1024 * 1024

#: Minimum password length. The operator picks the password (no default
#: credential exists), so this is the only floor there is.
MIN_PASSWORD_CHARS = 12

#: Login ids are ASCII by design. SQLite's NOCASE collation folds ASCII only,
#: so a non-ASCII username would have surprising uniqueness semantics ("Ä" and
#: "ä" would be two different accounts). The HUMAN name is ``display_name``,
#: which is free-form UTF-8 and is what the report and the UI show.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{1,63}$")
MAX_DISPLAY_NAME_CHARS = 120

#: Session lifetime. A consultant's working day, not a week: the token lives in
#: a browser cookie, and U8 is about to make it the thing that signs off a
#: parameter change.
SESSION_TTL_HOURS = float(os.getenv("PRISM_SESSION_TTL_HOURS", "12"))

#: ``last_seen_at`` is refreshed at most this often. Writing it on every
#: authenticated request would put an fsync (``synchronous=FULL``) and a write
#: lock on what is otherwise a pure read — for a field whose only consumer is
#: an operator asking "is this session in use".
LAST_SEEN_REFRESH_SECONDS = 60.0


class AuthError(Exception):
    """Base for every refusal in this module."""


class UserExistsError(AuthError):
    pass


class UnknownUserError(AuthError):
    pass


class WeakPasswordError(AuthError):
    pass


class InvalidUsernameError(AuthError):
    pass


class InvalidRoleError(AuthError):
    pass


class LastAdminError(AuthError):
    """The requested change would leave the box with no enabled admin.

    Added by unit P④, which is the first unit that lets a role or an enabled
    flag be changed from a BROWSER. The CLI has always guarded the other
    direction (``create-admin`` refuses to run when an admin exists), but
    nothing guarded the way OUT: an admin who demoted or disabled the last
    admin account would have locked every account-management surface — the
    admin UI and the ability to grant workspace membership — behind a role
    nobody holds, recoverable only by an operator with shell access to the
    SQLite file. Refusing is the whole doctrine of this project applied to
    accounts: a clear refusal beats a completed action with a wrong outcome.
    """


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _scrypt_maxmem(n: int, r: int, p: int) -> int:
    """The explicit ``maxmem`` for these parameters.

    Python defaults it to 0, which lets OpenSSL apply its own 32 MiB ceiling —
    fine for today's N, and a silent ``ValueError`` the day somebody raises it.
    Computing it from the parameters makes the cost knob actually turnable.
    """
    return 128 * r * n + 128 * r * p + (1 << 20)


def _check_cost(n: int, r: int, p: int) -> None:
    if n < 2 or (n & (n - 1)) != 0:
        raise ValueError(f"scrypt N must be a power of two greater than 1, got {n}")
    if r < 1 or p < 1:
        raise ValueError(f"scrypt r and p must be >= 1, got r={r} p={p}")
    memory = _scrypt_maxmem(n, r, p)
    if memory > MAX_SCRYPT_MEMORY_BYTES:
        raise ValueError(
            f"scrypt parameters N={n} r={r} p={p} would need {memory // (1 << 20)} MiB, "
            f"over the {MAX_SCRYPT_MEMORY_BYTES // (1 << 20)} MiB ceiling"
        )


def _derive(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    _check_cost(n, r, p)
    return hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=_scrypt_maxmem(n, r, p),
        dklen=SCRYPT_KEY_BYTES,
    )


def hash_password(
    password: str,
    *,
    n: int | None = None,
    r: int | None = None,
    p: int | None = None,
) -> str:
    """``scrypt$N$r$p$<salt>$<hash>`` for ``password``, with a fresh random salt.

    The parameters are explicit arguments (defaulting to the module policy) so
    a test can prove that a hash written with different costs still verifies —
    which is the whole point of storing them in the string.
    """
    n = SCRYPT_N if n is None else n
    r = SCRYPT_R if r is None else r
    p = SCRYPT_P if p is None else p
    salt = secrets.token_bytes(SCRYPT_SALT_BYTES)
    derived = _derive(password, salt, n, r, p)
    return f"scrypt${n}${r}${p}${_b64e(salt)}${_b64e(derived)}"


def parse_password_hash(encoded: str) -> dict[str, Any] | None:
    """The parameters inside an encoded hash, or ``None`` if it is not one.

    Returning ``None`` rather than raising: a malformed row must make the login
    FAIL, not 500 — an operator who hand-edited the database should not be able
    to turn one bad row into an outage.
    """
    try:
        scheme, n_text, r_text, p_text, salt_text, hash_text = encoded.split("$")
    except (AttributeError, ValueError):
        return None
    if scheme != "scrypt":
        return None
    try:
        return {
            "scheme": scheme,
            "n": int(n_text),
            "r": int(r_text),
            "p": int(p_text),
            "salt": _b64d(salt_text),
            "hash": _b64d(hash_text),
        }
    except Exception:  # noqa: BLE001 - any malformed field means "not a usable hash"
        return None


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time check of ``password`` against an encoded hash."""
    parsed = parse_password_hash(encoded)
    if parsed is None:
        logger.warning("auth: stored password hash is malformed; refusing the login")
        return False
    try:
        derived = _derive(password, parsed["salt"], parsed["n"], parsed["r"], parsed["p"])
    except ValueError:
        logger.warning("auth: stored password hash has unusable cost parameters; refusing")
        return False
    return hmac.compare_digest(derived, parsed["hash"])


def needs_rehash(encoded: str) -> bool:
    """Whether this hash is below current policy (weaker than the module
    constants). U7 only reports it; nobody upgrades hashes yet."""
    parsed = parse_password_hash(encoded)
    if parsed is None:
        return True
    return (parsed["n"], parsed["r"], parsed["p"]) < (SCRYPT_N, SCRYPT_R, SCRYPT_P)


#: A real hash of a value nobody can log in with, used to spend the same scrypt
#: time on an unknown username as on a known one. Rebuilt when the cost
#: constants change (a test lowering them must not keep paying full price).
_dummy_hash_cache: dict[tuple[int, int, int], str] = {}


def _burn_dummy_verify(password: str) -> None:
    """Spend one scrypt so an unknown username costs what a known one costs.

    Without this, "user does not exist" returns in microseconds while "wrong
    password" takes ~60 ms, and the uniform 401 body is undone by a stopwatch.
    Caveat, stated rather than papered over: the timings match only when the
    real user's stored hash uses the CURRENT parameters. A legacy hash with a
    lower N is still distinguishable in principle — that is inherent to
    parameter evolution, and the mitigation is :func:`needs_rehash`, not a
    fake-uniform delay.
    """
    key = (SCRYPT_N, SCRYPT_R, SCRYPT_P)
    encoded = _dummy_hash_cache.get(key)
    if encoded is None:
        encoded = hash_password("prism-nonexistent-account-placeholder")
        _dummy_hash_cache[key] = encoded
    verify_password(password, encoded)


def validate_password(password: str) -> str:
    """Return ``password`` if it clears policy, else raise :class:`WeakPasswordError`.

    Exception text in this module is DEVELOPER/log text and stays English on
    purpose. The user-facing ko/en/ja rendering happens where the human is —
    ``backend/tools/manage_users.py`` for the operator, ``routers/auth.py`` for
    the browser — both through ``pick_language_text``. Localizing here too
    would create a second table nobody keeps in sync.
    """
    if password is None or not password.strip():
        raise WeakPasswordError("A password is required.")
    if len(password) < MIN_PASSWORD_CHARS:
        raise WeakPasswordError(
            f"Password must be at least {MIN_PASSWORD_CHARS} characters."
        )
    return password


def normalize_username(username: str) -> str:
    cleaned = (username or "").strip()
    if not _USERNAME_RE.match(cleaned):
        raise InvalidUsernameError(
            "Username must be 2-64 ASCII characters starting with a letter or digit "
            "(. _ - @ allowed)."
        )
    return cleaned


def _validate_role(role: str) -> str:
    if role not in ROLES:
        raise InvalidRoleError(f"Unknown role {role!r}; expected one of {', '.join(ROLES)}.")
    return role


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _public_user(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    """The user shape everything outside this module is allowed to see.

    ``password_hash`` is not in it, and no caller may add it back: the CLI's
    ``list-users``, the login response and ``whoami`` all render this dict.
    """
    return {
        "id": int(row["id"]),
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "created_at": row["created_at"],
        "disabled": bool(row["disabled"]),
    }


def create_user(
    username: str,
    password: str,
    role: str,
    display_name: str | None = None,
) -> dict[str, Any]:
    """Create one user. Raises on a duplicate username, a bad role, or a
    password below policy — never silently overwrites an existing account."""
    username = normalize_username(username)
    _validate_role(role)
    validate_password(password)
    name = (display_name or username).strip()[:MAX_DISPLAY_NAME_CHARS] or username

    encoded = hash_password(password)
    with db.write_transaction() as conn:
        try:
            cursor = conn.execute(
                "INSERT INTO users (username, display_name, role, password_hash, created_at, disabled) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (username, name, role, encoded, _now_iso()),
            )
        except sqlite3.IntegrityError as exc:
            raise UserExistsError(f"User already exists: {username}") from exc
        row = conn.execute("SELECT * FROM users WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _public_user(row)


def get_user_by_username(username: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", ((username or "").strip(),)
        ).fetchone()
    return _public_user(row) if row is not None else None


def get_user(user_id: int) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
    return _public_user(row) if row is not None else None


def list_users() -> list[dict[str, Any]]:
    """Every account, oldest first. Hashes are not part of the shape."""
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
    return [_public_user(row) for row in rows]


def count_admins(*, include_disabled: bool = False) -> int:
    """How many admin accounts exist. The CLI's ``create-admin`` guard reads
    this; disabled admins are excluded by default, because an operator who
    disabled the only admin has locked themselves out and must be able to
    bootstrap again."""
    sql = "SELECT COUNT(*) AS n FROM users WHERE role = 'admin'"
    if not include_disabled:
        sql += " AND disabled = 0"
    with db.connect() as conn:
        return int(conn.execute(sql).fetchone()["n"])


def set_password(username: str, password: str) -> dict[str, Any]:
    """Replace a user's password AND revoke their live sessions.

    Revocation is not optional: the reason an operator resets a password is
    usually that the old one is compromised, and leaving the sessions it minted
    alive would make the reset cosmetic.
    """
    username = normalize_username(username)
    validate_password(password)
    encoded = hash_password(password)
    with db.write_transaction() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        if row is None:
            raise UnknownUserError(f"No such user: {username}")
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (encoded, row["id"]))
        conn.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (_now_iso(), row["id"]),
        )
    return _public_user(row)


def _other_enabled_admins(conn: sqlite3.Connection, user_id: int) -> int:
    """How many ENABLED admins exist besides ``user_id``.

    Counted on the caller's own connection, INSIDE its ``BEGIN IMMEDIATE``, so
    the check and the write cannot be separated by another writer: two admins
    demoting each other concurrently would otherwise both read "one other admin
    exists" and both succeed, leaving zero. ``db.write_transaction`` takes the
    write lock up front, which is what makes that impossible here.
    """
    return int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND disabled = 0 AND id != ?",
            (int(user_id),),
        ).fetchone()["n"]
    )


def set_disabled(username: str, disabled: bool) -> dict[str, Any]:
    """Disable (or re-enable) an account. Disabling also revokes live sessions.

    Refuses to disable the LAST enabled admin (:class:`LastAdminError`) — see
    that exception for why. Re-enabling is never refused: it can only ever
    increase the number of accounts that can administer the box.
    """
    username = normalize_username(username)
    with db.write_transaction() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        if row is None:
            raise UnknownUserError(f"No such user: {username}")
        if disabled and row["role"] == "admin" and not bool(row["disabled"]):
            if _other_enabled_admins(conn, int(row["id"])) == 0:
                raise LastAdminError(
                    f"Refusing to disable {row['username']}: it is the only enabled admin "
                    "account, and disabling it would leave nobody able to manage users or "
                    "project access."
                )
        conn.execute(
            "UPDATE users SET disabled = ? WHERE id = ?", (1 if disabled else 0, row["id"])
        )
        if disabled:
            conn.execute(
                "UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
                (_now_iso(), row["id"]),
            )
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
    return _public_user(updated)


def set_role(username: str, role: str) -> dict[str, Any]:
    """Change an account's role, refusing to demote the last enabled admin.

    Live sessions are deliberately NOT revoked: :func:`resolve_session` joins
    ``users`` and reads ``role`` fresh on every request, so a demotion takes
    effect on the demoted person's very next call without logging them out of
    the pages they are allowed to keep reading. (Contrast :func:`set_password`
    and :func:`set_disabled`, which DO revoke — there the point is precisely to
    invalidate what the old credential minted.)
    """
    username = normalize_username(username)
    _validate_role(role)
    with db.write_transaction() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        if row is None:
            raise UnknownUserError(f"No such user: {username}")
        if row["role"] == "admin" and role != "admin" and not bool(row["disabled"]):
            if _other_enabled_admins(conn, int(row["id"])) == 0:
                raise LastAdminError(
                    f"Refusing to change {row['username']} to {role}: it is the only enabled "
                    "admin account, and demoting it would leave nobody able to manage users "
                    "or project access."
                )
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, row["id"]))
        updated = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
    logger.info("auth: role of %s changed to %s", row["username"], role)
    return _public_user(updated)


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    """The user for these credentials, or ``None``.

    ONE return value for every failure — unknown username, wrong password,
    disabled account — because the caller must not be able to build an
    account-existence oracle out of the difference. Unknown usernames burn a
    real scrypt (:func:`_burn_dummy_verify`) so the timing does not leak what
    the status code does not, and a disabled user's password is still checked
    for the same reason.
    """
    cleaned = (username or "").strip()
    if not cleaned or password is None:
        _burn_dummy_verify(password or "")
        return None

    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (cleaned,)
        ).fetchone()

    if row is None:
        _burn_dummy_verify(password)
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    if bool(row["disabled"]):
        logger.info("auth: rejected a login for disabled account %s", row["username"])
        return None
    return _public_user(row)


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

def hash_token(token: str) -> str:
    """The SHA-256 hex digest the database stores instead of the token."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def create_session(user_id: int, *, ttl_hours: float | None = None) -> tuple[str, dict[str, Any]]:
    """Mint a session for ``user_id``. Returns ``(token, session)``.

    The raw token is returned ONCE, to the caller that will hand it to the
    browser; it is never stored and cannot be recovered afterwards.
    """
    ttl = SESSION_TTL_HOURS if ttl_hours is None else float(ttl_hours)
    token = secrets.token_urlsafe(32)
    created = _now()
    expires = created + timedelta(hours=ttl)
    session_id = uuid4().hex
    with db.write_transaction() as conn:
        conn.execute(
            "INSERT INTO auth_sessions (id, user_id, token_hash, created_at, expires_at, "
            "revoked_at, last_seen_at) VALUES (?, ?, ?, ?, ?, NULL, ?)",
            (
                session_id,
                int(user_id),
                hash_token(token),
                created.isoformat(),
                expires.isoformat(),
                created.isoformat(),
            ),
        )
    session = {
        "id": session_id,
        "user_id": int(user_id),
        "created_at": created.isoformat(),
        "expires_at": expires.isoformat(),
        "revoked_at": None,
        "last_seen_at": created.isoformat(),
    }
    return token, session


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def resolve_session(token: str | None) -> dict[str, Any] | None:
    """``{"user": ..., "session": ...}`` for a live token, else ``None``.

    Fails closed on every ambiguity: no token, no matching row, revoked,
    expired, an unparseable ``expires_at``, or a disabled owner. Comparison is
    done in Python rather than in SQL on the ISO strings — the strings do sort
    correctly today, but "correct because of how ``isoformat`` pads
    microseconds" is not a property worth betting a session check on.
    """
    if not token:
        return None
    token_hash = hash_token(token)
    with db.connect() as conn:
        # Every column is aliased explicitly rather than joining ``u.*``:
        # ``users`` and ``auth_sessions`` BOTH have a ``created_at``, and
        # ``sqlite3.Row`` resolves a duplicate name to the first match — which
        # would have silently reported the session's creation time as the
        # user's.
        row = conn.execute(
            "SELECT s.id AS session_id, s.created_at AS session_created_at, "
            "s.expires_at AS expires_at, s.revoked_at AS revoked_at, "
            "s.last_seen_at AS last_seen_at, "
            "u.id AS id, u.username AS username, u.display_name AS display_name, "
            "u.role AS role, u.created_at AS created_at, u.disabled AS disabled "
            "FROM auth_sessions s JOIN users u ON u.id = s.user_id "
            "WHERE s.token_hash = ?",
            (token_hash,),
        ).fetchone()

        if row is None or row["revoked_at"]:
            return None

        expires = _parse_ts(row["expires_at"])
        now = _now()
        if expires is None or expires <= now:
            return None
        if bool(row["disabled"]):
            return None

        last_seen = _parse_ts(row["last_seen_at"])
        if last_seen is None or (now - last_seen).total_seconds() >= LAST_SEEN_REFRESH_SECONDS:
            # Throttled write on a read path -- see LAST_SEEN_REFRESH_SECONDS.
            # Best-effort: a failure here must not fail an otherwise valid
            # request, since the field is diagnostic only.
            try:
                conn.execute(
                    "UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?",
                    (now.isoformat(), row["session_id"]),
                )
            except sqlite3.Error:  # pragma: no cover - defensive
                logger.debug("auth: could not refresh last_seen_at", exc_info=True)

    return {
        "user": _public_user(row),
        "session": {
            "id": row["session_id"],
            "created_at": row["session_created_at"],
            "expires_at": row["expires_at"],
            "last_seen_at": row["last_seen_at"],
        },
    }


def revoke_session(token: str | None) -> bool:
    """Revoke the session this token names. ``True`` when a live one was ended.

    Idempotent and non-oracular: an unknown, already-revoked or expired token
    returns ``False`` without telling the caller which of those it was.
    """
    if not token:
        return False
    with db.write_transaction() as conn:
        cursor = conn.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
            (_now_iso(), hash_token(token)),
        )
        return cursor.rowcount > 0


def revoke_sessions_for_user(user_id: int) -> int:
    """Log a user out everywhere. Returns how many sessions were live."""
    with db.write_transaction() as conn:
        cursor = conn.execute(
            "UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (_now_iso(), int(user_id)),
        )
        return cursor.rowcount


def purge_expired_sessions(*, keep_hours: float = 24.0) -> int:
    """Delete sessions that expired more than ``keep_hours`` ago.

    Not wired to anything in U7 (no scheduler owns it yet) — it exists so the
    table has a defined growth story, and so U8's ops pass has something to
    call. The grace window keeps a just-expired session readable for the
    "why was I logged out" question.
    """
    cutoff = (_now() - timedelta(hours=keep_hours)).isoformat()
    with db.write_transaction() as conn:
        cursor = conn.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (cutoff,))
        return cursor.rowcount

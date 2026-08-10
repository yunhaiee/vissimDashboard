"""Which people may see which 프로젝트 — the join between auth and workspaces.

Unit P④. Before this module Prism had two complete halves that had never been
introduced: ``services/workspaces.py`` isolated every client's model behind a
workspace boundary, and ``services/auth.py`` knew who a person was — and
NOTHING connected them. Any logged-in user (indeed any anonymous one) could
put another workspace's id in ``X-Workspace-Id`` and read that client's
network. This module is the missing edge, and :func:`user_can_access` is the
single predicate the whole rule reduces to.

**The access rule, stated once, enforced in one place.**

======================  ==================  ============================
caller                  default workspace   any NON-default workspace
======================  ==================  ============================
unauthenticated         allowed             **403**
authenticated, member   allowed             allowed
authenticated, other    allowed             **403**
``admin``               allowed             allowed
======================  ==================  ============================

Two of those rows are deliberate and worth defending:

* **The default workspace stays open to anonymous callers.** It is the demo /
  dev dataset, it is what a header-less request has always resolved to, and the
  rest of Prism is not behind a login. Closing it here would have made this
  unit an app-wide authentication switch smuggled in under a membership
  feature — and an enforcement flip nobody can review is exactly what
  ``routers/auth.py`` refused to do in U7.
* **A non-member gets 403, never a quiet fallback to the default workspace.**
  Serving one client's network to another client is the precise failure the
  workspace boundary exists to prevent, so the refusal is loud. This is the
  same doctrine ``WorkspaceNotFoundError`` already applies to an unknown id.

**Admins are allowed everywhere, and that is load-bearing rather than lazy.**
Every workspace that existed before this unit has ZERO members — the table did
not exist when they were created. If membership were the only key, the very
first deployment of this code would lock the operator out of their own live
projects with no way back in, because the only UI for granting membership is
itself behind the admin role. So: the admin role sees and grants everything,
the first admin already exists (created by ``backend/tools/manage_users.py``
long before this unit), and migrating existing workspaces to "admin-only until
granted" is therefore recoverable in the browser rather than a lockout.

**Enforcement lives in ``main.WorkspaceScopeMiddleware``**, not in a dependency
sprinkled over eighteen routers. The middleware is already the one place that
resolves ``X-Workspace-Id`` for every request, so putting the check there makes
"a new endpoint cannot forget the boundary" structural instead of a convention
— and ``backend/tests/test_workspace_membership.py`` pins that it is the
middleware doing it.

**Orphans.** ``workspace_members.workspace_id`` is plain TEXT: workspaces live
in a JSON registry on disk, so SQLite cannot foreign-key them (see
``services/db.py``'s v2 migration). A workspace that disappears from the
registry therefore leaves membership rows behind. Every LISTING here
intersects with the registry, so an orphaned row grants access to nothing and
is invisible; :func:`user_can_access` is reached only for an id the middleware
already resolved against that same registry.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from services import auth, db, workspaces

logger = logging.getLogger(__name__)

#: The ONE membership role anything branches on. Deliberately singular — see
#: ``services/db.py``'s v2 migration for why a richer set nobody checks would
#: be a permission system that only looks like one. The CHECK constraint in the
#: schema mirrors this tuple; both, on purpose (same arrangement as
#: ``auth.ROLES``).
MEMBER_ROLE = "member"
MEMBERSHIP_ROLES: tuple[str, ...] = (MEMBER_ROLE,)

#: The role on the USER record that is allowed into every workspace without a
#: membership row. Named here rather than spelled ``"admin"`` inline so the
#: rule has one definition.
ADMIN_ROLE = "admin"

#: ko primary, en/ja through ``pick_language_text`` — the refusal a caller sees
#: when they name a workspace they are not in. Lives here (not in ``main.py``)
#: because it belongs to the rule, and because the middleware that renders it
#: has no business owning product copy.
FORBIDDEN_TEXT: dict[str, str] = {
    "ko": "이 프로젝트에 접근할 권한이 없습니다. 관리자에게 참여 권한을 요청하세요.",
    "en": "You do not have access to this project. Ask an administrator to add you to it.",
    "ja": "このプロジェクトにアクセスする権限がありません。管理者に参加権限を依頼してください。",
}

#: The ``detail.code`` the 403 carries, alongside ``workspace_not_found``'s
#: existing 404 code. A client must be able to tell "no such project" from
#: "not your project" without parsing a localized sentence.
FORBIDDEN_CODE = "workspace_forbidden"


class MembershipError(RuntimeError):
    """Base class for every refusal in this module."""


class UnknownMemberUserError(MembershipError):
    """A grant named a user id that does not exist."""


class UnknownMemberWorkspaceError(MembershipError):
    """A grant named a workspace that is not in the registry."""


class InvalidMembershipRoleError(MembershipError):
    """A grant named a membership role outside :data:`MEMBERSHIP_ROLES`."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _public_member(row: sqlite3.Row) -> dict[str, Any]:
    """One member, in the shape the admin UI renders.

    Built from the same columns ``auth._public_user`` exposes plus the two
    membership fields — and, like that helper, it CANNOT carry
    ``password_hash``: the query below never selects it.
    """
    return {
        "user_id": int(row["user_id"]),
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["user_role"],
        "disabled": bool(row["disabled"]),
        "membership_role": row["membership_role"],
        "member_since": row["created_at"],
    }


# ---------------------------------------------------------------------------
# Grants
# ---------------------------------------------------------------------------


def grant(workspace_id: str, user_id: int, role: str = MEMBER_ROLE) -> dict[str, Any]:
    """Add ``user_id`` to ``workspace_id`` (idempotent).

    Both ends are validated against their own authority — the workspace against
    the registry, the user against the ``users`` table — so a typo produces a
    named refusal rather than a row that grants access to nothing (or, worse,
    a row nobody can find to revoke).

    Re-granting an existing membership is an UPSERT and keeps the original
    ``created_at``: "member since" is a fact about when they joined, and a
    second click of the same button must not rewrite history.
    """
    if role not in MEMBERSHIP_ROLES:
        raise InvalidMembershipRoleError(
            f"Unknown membership role {role!r}; expected one of {', '.join(MEMBERSHIP_ROLES)}."
        )
    if workspaces.get_workspace(workspace_id) is None:
        raise UnknownMemberWorkspaceError(f"Unknown workspace id: {workspace_id}")
    user = auth.get_user(int(user_id))
    if user is None:
        raise UnknownMemberUserError(f"No such user id: {user_id}")

    with db.write_transaction() as conn:
        conn.execute(
            "INSERT INTO workspace_members (workspace_id, user_id, role, created_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(workspace_id, user_id) DO UPDATE SET role = excluded.role",
            (workspace_id, int(user_id), role, _now_iso()),
        )
    logger.info(
        "membership: %s (id=%s) is now a %s of workspace %s",
        user["username"],
        user["id"],
        role,
        workspace_id,
    )
    return {
        "workspace_id": workspace_id,
        "user_id": int(user_id),
        "role": role,
    }


def revoke(workspace_id: str, user_id: int) -> bool:
    """Remove a membership. ``True`` when one was actually removed.

    Deliberately does NOT refuse to remove the last member of a workspace: an
    admin can always see and re-grant it (see the module docstring), so an
    empty workspace is recoverable, not a lockout. That is not true of the last
    ADMIN, which is why ``services/auth.py`` guards that one and this does not
    guard here.
    """
    with db.write_transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, int(user_id)),
        )
        removed = cursor.rowcount > 0
    if removed:
        logger.info("membership: revoked user id=%s from workspace %s", user_id, workspace_id)
    return removed


def revoke_all_for_workspace(workspace_id: str) -> int:
    """Drop every membership of ``workspace_id``. Returns how many went.

    Nothing calls this yet — there is no delete-workspace endpoint (see
    ``routers/workspaces.py``). It exists so that when one lands, the answer to
    "what happens to the memberships" is a function call rather than a
    forgotten table full of orphans.
    """
    with db.write_transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM workspace_members WHERE workspace_id = ?", (workspace_id,)
        )
        return cursor.rowcount


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def is_member(workspace_id: str, user_id: int) -> bool:
    """Whether a membership row exists. The raw table lookup — callers wanting
    the RULE (default workspace, admins) want :func:`user_can_access`."""
    if not workspace_id or user_id is None:
        return False
    with db.connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, int(user_id)),
        ).fetchone()
    return row is not None


def list_members(workspace_id: str) -> list[dict[str, Any]]:
    """Everyone in ``workspace_id``, oldest membership first.

    Columns are named one by one rather than ``SELECT *``: ``users`` and
    ``workspace_members`` both carry a ``created_at`` AND a ``role``, and
    ``sqlite3.Row`` resolves a duplicate name to the first match — the same
    trap ``auth.resolve_session`` documents. Aliasing makes "member since"
    and "membership role" unambiguous, and keeps ``password_hash`` out of the
    result by construction.
    """
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT m.workspace_id AS workspace_id, m.user_id AS user_id, "
            "m.role AS membership_role, m.created_at AS created_at, "
            "u.username AS username, u.display_name AS display_name, "
            "u.role AS user_role, u.disabled AS disabled "
            "FROM workspace_members m JOIN users u ON u.id = m.user_id "
            "WHERE m.workspace_id = ? ORDER BY m.created_at, m.user_id",
            (workspace_id,),
        ).fetchall()
    return [_public_member(row) for row in rows]


def list_workspace_ids_for_user(user_id: int) -> list[str]:
    """The ids this person holds a membership row for — raw, unfiltered.

    May include ids no longer in the registry (see the module docstring on
    orphans); :func:`visible_workspaces` is the one that intersects.
    """
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT workspace_id FROM workspace_members WHERE user_id = ? ORDER BY created_at",
            (int(user_id),),
        ).fetchall()
    return [str(row["workspace_id"]) for row in rows]


def visible_workspaces(user: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The workspace records ``user`` is allowed to see, default first.

    THE filter behind the 프로젝트 picker, and the read half of the same rule
    :func:`user_can_access` enforces on the write half — deliberately one
    function apart from it rather than one function containing both, because a
    listing that showed a workspace the middleware then 403s (or hid one it
    allows) is the kind of drift only a shared rule prevents. The
    membership tests assert the two agree.

    ``None`` means an anonymous caller: exactly the default workspace.
    """
    entries = workspaces.list_workspaces()
    if user is not None and user.get("role") == ADMIN_ROLE:
        return entries
    allowed: set[str] = set()
    if user is not None:
        allowed = set(list_workspace_ids_for_user(int(user["id"])))
    return [
        entry
        for entry in entries
        if bool(entry.get("is_default")) or str(entry.get("id")) in allowed
    ]


# ---------------------------------------------------------------------------
# THE predicate
# ---------------------------------------------------------------------------


def user_can_access(user_id: int | None, workspace_id: str) -> bool:
    """May this person read/write ``workspace_id``? The whole rule, one place.

    ``user_id`` is ``None`` for an unauthenticated caller — a real answer
    ("nobody"), not a missing argument, which is why it is accepted rather than
    pushed onto every call site as a branch.

    Fails closed on every ambiguity: an unregistered workspace, a user id that
    no longer resolves, and a disabled account all return ``False``. (A live
    session already cannot belong to a disabled user — ``auth.resolve_session``
    checks it — so the disabled branch here is the second, independent check,
    for the callers that pass an id from somewhere other than a session.)
    """
    if not workspace_id:
        return False

    record = workspaces.get_workspace(workspace_id)
    if record is None:
        # Unregistered: the HTTP boundary turns this into a 404 well before
        # here, so reaching it means a caller invented an id. Refuse.
        return False
    if bool(record.get("is_default")):
        # The demo/dev dataset. Open to everyone, including anonymous callers —
        # see the module docstring for why this stays open.
        return True

    if user_id is None:
        return False
    user = auth.get_user(int(user_id))
    if user is None or user.get("disabled"):
        return False
    if user.get("role") == ADMIN_ROLE:
        return True
    return is_member(workspace_id, int(user_id))


__all__ = [
    "ADMIN_ROLE",
    "FORBIDDEN_CODE",
    "FORBIDDEN_TEXT",
    "InvalidMembershipRoleError",
    "MEMBERSHIP_ROLES",
    "MEMBER_ROLE",
    "MembershipError",
    "UnknownMemberUserError",
    "UnknownMemberWorkspaceError",
    "grant",
    "is_member",
    "list_members",
    "list_workspace_ids_for_user",
    "revoke",
    "revoke_all_for_workspace",
    "user_can_access",
    "visible_workspaces",
]

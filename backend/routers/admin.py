"""``/api/admin`` — account and project-access administration (unit P④).

Until this unit the ONLY way to create a Prism account was an operator running
``backend/tools/manage_users.py`` in a terminal on the backend machine. For a
product whose users are traffic consultants that is not an administration
story, it is a reason to never add a second user — so every workspace stayed
open to everybody and the isolation boundary that ``services/workspaces.py``
built was never actually closed. This router is the browser half of the same
CLI, plus the one thing the CLI never had: **membership**, i.e. which people
may see which 프로젝트.

**Every route is admin-only, structurally.** The gate is declared once, on the
``APIRouter`` itself, so it applies to routes that do not exist yet — a new
endpoint added below inherits it without anybody remembering to. It reuses
``routers/auth.require_role``; there is no second implementation of "is this
person an admin" anywhere in the codebase, and
``tests/test_workspace_membership.py`` enumerates this router's routes to prove
each one really answers 401 to an anonymous caller and 403 to a non-admin.

**This router owns no policy.** Every user mutation goes through
``services/auth.py`` — including the invariants that live there (the last
enabled admin cannot be disabled or demoted), which the CLI gets for free by
calling the same functions. What DOES live here, because it needs the caller's
identity and the service layer deliberately does not know it, is the
self-targeting refusal: an admin may not disable or demote THEMSELVES. The
last-admin guard alone would not catch it (a second admin makes the change
"safe" by the service's reckoning) and an administrator locking themselves out
of the only surface that could undo it is not a state any confirmation dialog
makes acceptable.

**Nothing here can leak a credential.** Users are rendered through
``services/auth._public_user``, which has no ``password_hash`` field to fill;
members come back through ``services/membership._public_member``, whose query
never selects one. No endpoint returns a session token, and passwords travel in
a JSON body (never a query string, for the reason ``routers/auth.py`` gives).
No endpoint invents a password either: an admin creating an account types one,
exactly as the CLI has always required.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from routers.auth import current_user, require_role
from services import auth, membership, workspaces
from services.agent.planner import pick_language_text

logger = logging.getLogger(__name__)

#: Declared on the router, not per route: a forgotten decorator is a silent
#: privilege escalation, a router-level dependency cannot be forgotten.
router = APIRouter(
    prefix="/api/admin",
    tags=["admin"],
    dependencies=[Depends(require_role("admin"))],
)

_SUPPORTED_LANGUAGES = ("ko", "en", "ja")

#: ko primary, en/ja through ``pick_language_text`` — same table lookup every
#: other deterministic user-facing string in this codebase goes through.
_SELF_DISABLE = {
    "ko": "자기 자신의 계정은 비활성화할 수 없습니다. 다른 관리자에게 요청하세요.",
    "en": "You cannot disable your own account. Ask another administrator.",
    "ja": "自分自身のアカウントは無効化できません。他の管理者に依頼してください。",
}
_SELF_DEMOTE = {
    "ko": "자기 자신의 권한은 변경할 수 없습니다. 다른 관리자에게 요청하세요.",
    "en": "You cannot change your own role. Ask another administrator.",
    "ja": "自分自身の権限は変更できません。他の管理者に依頼してください。",
}


def _language(request: Request) -> str:
    """ko unless the caller clearly asked for en/ja. Same order as
    ``routers/auth.py``'s: explicit query parameter, then ``Accept-Language``."""
    requested = (request.query_params.get("language") or "").strip().lower()
    if requested in _SUPPORTED_LANGUAGES:
        return requested
    header = (request.headers.get("accept-language") or "").strip().lower()
    primary = header.split(",")[0].split("-")[0].strip()
    return primary if primary in _SUPPORTED_LANGUAGES else "ko"


def _http_error_for(exc: auth.AuthError) -> HTTPException:
    """One mapping from service refusal to HTTP status, for every route here.

    Exception TEXT from ``services/auth.py`` is developer/log English by that
    module's own rule; it is passed through because an admin screen is an
    operator surface and a precise English reason beats a vague localized one.
    The two refusals a NON-developer will actually hit — the self-targeting
    ones — are localized at their call sites below.
    """
    if isinstance(exc, auth.UserExistsError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, auth.LastAdminError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, auth.UnknownUserError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, (auth.WeakPasswordError, auth.InvalidUsernameError, auth.InvalidRoleError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _require_user(user_id: int) -> dict[str, Any]:
    user = auth.get_user(int(user_id))
    if user is None:
        raise HTTPException(status_code=404, detail=f"No such user id: {user_id}")
    return user


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    #: The floor is ``auth.MIN_PASSWORD_CHARS`` and it is enforced THERE (by
    #: ``validate_password``), not duplicated as a pydantic ``min_length``: two
    #: numbers for one policy is how they drift apart. The cap is a body-size
    #: guard, matching ``routers/auth.LoginRequest``.
    password: str = Field(min_length=1, max_length=1024)
    role: str
    display_name: str | None = None


class SetPasswordRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class SetDisabledRequest(BaseModel):
    disabled: bool


class SetRoleRequest(BaseModel):
    role: str


class GrantMemberRequest(BaseModel):
    user_id: int
    role: str = membership.MEMBER_ROLE


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@router.get("/users")
def list_users():
    """Every account, oldest first. Never a hash — ``_public_user`` has no
    field for one."""
    return {"users": auth.list_users(), "roles": list(auth.ROLES)}


@router.post("/users")
def create_user(payload: CreateUserRequest, admin: dict[str, Any] = Depends(current_user)):
    """Create an account with a password the admin typed.

    No generated password and no invite link: the operator-supplies-the-secret
    rule from ``services/auth.py``'s docstring is the same rule whether the
    operator is at a terminal or in a browser, and a password this server
    invented would be one this server had to transmit.
    """
    try:
        user = auth.create_user(
            payload.username, payload.password, payload.role, display_name=payload.display_name
        )
    except auth.AuthError as exc:
        raise _http_error_for(exc) from exc
    logger.info("admin: %s created account %s (%s)", admin["username"], user["username"], user["role"])
    return user


@router.post("/users/{user_id}/password")
def set_password(user_id: int, payload: SetPasswordRequest, admin: dict[str, Any] = Depends(current_user)):
    """Admin password reset. Revokes that user's live sessions — the revocation
    is ``services.auth.set_password``'s, not repeated here."""
    target = _require_user(user_id)
    try:
        auth.set_password(target["username"], payload.password)
    except auth.AuthError as exc:
        raise _http_error_for(exc) from exc
    logger.info("admin: %s reset the password of %s", admin["username"], target["username"])
    return auth.get_user(int(user_id))


@router.post("/users/{user_id}/disabled")
def set_disabled(
    user_id: int,
    payload: SetDisabledRequest,
    request: Request,
    admin: dict[str, Any] = Depends(current_user),
):
    """Disable or re-enable an account.

    Two refusals, in two places on purpose: "not the last enabled admin" is a
    property of the DATABASE and lives in ``services/auth.py`` (so the CLI
    inherits it); "not yourself" is a property of the CALLER and lives here,
    because the service layer has no caller.
    """
    target = _require_user(user_id)
    if payload.disabled and int(target["id"]) == int(admin["id"]):
        raise HTTPException(status_code=409, detail=pick_language_text(_SELF_DISABLE, _language(request)))
    try:
        updated = auth.set_disabled(target["username"], payload.disabled)
    except auth.AuthError as exc:
        raise _http_error_for(exc) from exc
    logger.info(
        "admin: %s %s account %s",
        admin["username"],
        "disabled" if payload.disabled else "re-enabled",
        target["username"],
    )
    return updated


@router.post("/users/{user_id}/role")
def set_role(
    user_id: int,
    payload: SetRoleRequest,
    request: Request,
    admin: dict[str, Any] = Depends(current_user),
):
    """Change an account's role. Same two-place split as ``set_disabled``."""
    target = _require_user(user_id)
    if int(target["id"]) == int(admin["id"]) and payload.role != target["role"]:
        raise HTTPException(status_code=409, detail=pick_language_text(_SELF_DEMOTE, _language(request)))
    try:
        updated = auth.set_role(target["username"], payload.role)
    except auth.AuthError as exc:
        raise _http_error_for(exc) from exc
    logger.info("admin: %s set %s to %s", admin["username"], target["username"], payload.role)
    return updated


# ---------------------------------------------------------------------------
# Workspace membership
# ---------------------------------------------------------------------------


def _require_workspace(workspace_id: str) -> dict[str, Any]:
    record = workspaces.get_workspace(workspace_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "workspace_not_found", "message": f"Unknown workspace id: {workspace_id}"},
        )
    return record


def _member_listing(workspace_id: str) -> dict[str, Any]:
    """The roster payload, built once and returned by all three member routes.

    A grant/revoke answers with the FULL new roster rather than just "ok", so
    the UI never has to re-derive the list it is about to render from a
    mutation response plus a stale copy — one shape, one source of truth, and
    no window where the screen and the server disagree.
    """
    record = _require_workspace(workspace_id)
    return {
        "workspace": record,
        "members": membership.list_members(workspace_id),
        # The default workspace is readable by anonymous callers by design
        # (services/membership.py's rule table). Reporting a flag rather than a
        # doctored roster: an empty member list there would tell the admin the
        # opposite of the truth.
        "open_to_everyone": bool(record.get("is_default")),
    }


@router.get("/workspaces/{workspace_id}/members")
def list_members(workspace_id: str):
    """Who may see this 프로젝트."""
    return _member_listing(workspace_id)


@router.post("/workspaces/{workspace_id}/members")
def grant_member(workspace_id: str, payload: GrantMemberRequest, admin: dict[str, Any] = Depends(current_user)):
    _require_workspace(workspace_id)
    try:
        granted = membership.grant(workspace_id, payload.user_id, payload.role)
    except membership.UnknownMemberUserError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except membership.UnknownMemberWorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except membership.InvalidMembershipRoleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info(
        "admin: %s granted user id=%s access to workspace %s",
        admin["username"],
        payload.user_id,
        workspace_id,
    )
    return {**_member_listing(workspace_id), "granted": granted}


@router.delete("/workspaces/{workspace_id}/members/{user_id}")
def revoke_member(workspace_id: str, user_id: int, admin: dict[str, Any] = Depends(current_user)):
    """Remove a membership. Idempotent: revoking a membership that is not there
    reports ``revoked: false`` rather than 404, because the caller's intent
    ("this person must not have access") is satisfied either way."""
    _require_workspace(workspace_id)
    revoked = membership.revoke(workspace_id, user_id)
    if revoked:
        logger.info(
            "admin: %s revoked user id=%s from workspace %s", admin["username"], user_id, workspace_id
        )
    return {**_member_listing(workspace_id), "revoked": revoked}

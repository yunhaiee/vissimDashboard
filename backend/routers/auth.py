"""``/api/auth`` — login, logout, whoami, and the dependency U8 will enforce with.

Unit U7 (roadmap 2.5, P③④). This router is **purely additive**: it adds three
routes and exports two dependencies, and it protects nothing. No existing
endpoint gains a ``Depends(current_user)`` in this unit, the approval flow is
untouched, and there is no login UI yet (U9). Shipping the machinery in one
change and the enforcement in the next is what makes both reviewable — and it
means that if the auth core turns out to be wrong, nothing that works today
stops working.

**Approved policy (user, 2026-08-06, roadmap row 2.5).** The only way an
account comes into existence is an operator running
``backend/tools/manage_users.py create-admin`` and supplying a password. This
file creates no user, seeds no fallback, and has no "if there are no users,
let everyone in" branch. An empty ``users`` table means nobody can log in,
which is the correct behaviour for a product whose next unit puts these names
in an audit document.

**Uniform failure.** Wrong password, unknown username and disabled account all
return the SAME 401 with the SAME body, and an unknown username still burns a
real scrypt (``services/auth.authenticate``) so a stopwatch does not answer
what the status code refuses to. Anything else is an account-enumeration
oracle on the front door of a consultancy's client list.

**Transport.** The token goes into an ``HttpOnly``, ``SameSite=Lax`` cookie —
HttpOnly so a scripted XSS cannot read it, Lax so a cross-site form POST
cannot ride it. Two consequences worth stating out loud:

* ``Secure`` is OFF by default because dev runs on plain http://localhost:8001.
  Set ``PRISM_AUTH_COOKIE_SECURE=1`` for any TLS deployment; roadmap 3.6
  (Windows service + single-origin TLS) is where that becomes the default.
* ``SameSite=Lax`` means the cookie is NOT sent on cross-SITE XHR. Today the
  frontend is :3000 and the backend :8001 — same site (both localhost), so
  ``credentials: "include"`` works; a deployment that splits them across
  different sites would need the single-origin reverse proxy that roadmap 3.6
  already plans, not ``SameSite=None``. U9 inherits this constraint.

An ``Authorization: Bearer <token>`` header is accepted as an alternative to
the cookie. That is not a second auth scheme — it is the same session token by
the other standard channel, so the job queue, ``backend/tools`` scripts and
tests can authenticate without a cookie jar. It adds no CSRF surface (a
cross-origin page cannot set request headers without a CORS preflight the
browser will not grant).

**Workspace independence (deliverable 5).** Auth is global: a person is the
same person in every project, and U8's "who approved this" must be answerable
from any workspace. So the database lives in ``runtime/db/`` and NOT under
``runtime/workspaces/<id>/`` (see ``services/db.py``), and nothing in this
module reads or binds a workspace.

Interaction with unit 1.4's ``main.WorkspaceScopeMiddleware``, verified rather
than assumed: the middleware runs on EVERY request, but an ABSENT
``X-Workspace-Id`` header resolves to the default workspace — it does not
404 — so a browser that has never seen a workspace picker can still reach
``POST /api/auth/login``. That is exactly the case a login page is in.
(A header that is PRESENT but names an unknown workspace still 404s before
this router is reached. That is the middleware's existing, deliberate
fail-closed behaviour, it predates this unit, and it is not changed here: a
client holding a stale workspace id gets a workspace error rather than a
login. Clearing the stale id is the client's fix, and U9's login screen has no
reason to send the header at all.)
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from services import auth
from services.agent.planner import pick_language_text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

#: The cookie the browser carries. Named for the product so it cannot collide
#: with another app on the same localhost during development.
SESSION_COOKIE_NAME = "prism_session"

def _cookie_secure() -> bool:
    """``Secure`` on the session cookie. Off by default so http://localhost
    works; MUST be on behind TLS — see the module docstring. Read per call, not
    at import, so a deployment can set it without a code change."""
    return os.getenv("PRISM_AUTH_COOKIE_SECURE", "0").strip().lower() in ("1", "true", "yes", "on")


_SUPPORTED_LANGUAGES = ("ko", "en", "ja")

#: ko primary, en/ja through ``pick_language_text`` — the same table lookup
#: every other deterministic user-facing string in this codebase goes through
#: (services/agent/planner.py). An unknown code falls back to Korean there.
_LOGIN_FAILED = {
    "ko": "아이디 또는 비밀번호가 올바르지 않습니다.",
    "en": "Incorrect username or password.",
    "ja": "ユーザーIDまたはパスワードが正しくありません。",
}
_NOT_AUTHENTICATED = {
    "ko": "로그인이 필요합니다.",
    "en": "Authentication is required.",
    "ja": "ログインが必要です。",
}
_FORBIDDEN = {
    "ko": "이 작업에 필요한 권한이 없습니다.",
    "en": "You do not have permission for this action.",
    "ja": "この操作に必要な権限がありません。",
}


def _language(request: Request, explicit: str | None = None) -> str:
    """ko unless the caller clearly asked for en/ja.

    Query parameter first (the convention ``routers/agent_audit.py`` already
    uses), then the first tag of ``Accept-Language``. Never guesses beyond the
    three languages the product ships.
    """
    if explicit in _SUPPORTED_LANGUAGES:
        return explicit
    requested = (request.query_params.get("language") or "").strip().lower()
    if requested in _SUPPORTED_LANGUAGES:
        return requested
    header = (request.headers.get("accept-language") or "").strip().lower()
    primary = header.split(",")[0].split("-")[0].strip()
    return primary if primary in _SUPPORTED_LANGUAGES else "ko"


class LoginRequest(BaseModel):
    """Credentials arrive as an explicit JSON body — never as query parameters
    and never form-encoded. A password in a URL ends up in access logs, in
    browser history and in the ``Referer`` header of the next request."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)
    language: str | None = None


def _token_from_request(request: Request) -> str | None:
    """The session token this request carries: cookie first, then bearer."""
    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie:
        return cookie
    header = request.headers.get("authorization") or ""
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return value.strip()
    return None


# ---------------------------------------------------------------------------
# Dependencies — built and tested here, WIRED NOWHERE (U8 does the wiring)
# ---------------------------------------------------------------------------

def resolve_request_session(request: Request) -> dict[str, Any] | None:
    """``{"user": ..., "session": ...}`` for this request, or ``None``.

    Memoized on ``request.state`` so a route that takes ``Depends(current_user)``
    AND reads the session itself (``whoami``) costs one database read, not two
    — and so a future stack of role dependencies on one route cannot turn into
    a query per dependency.
    """
    cached = getattr(request.state, "prism_auth", ...)
    if cached is not ...:
        return cached
    resolved = auth.resolve_session(_token_from_request(request))
    request.state.prism_auth = resolved
    return resolved


def optional_current_user(request: Request) -> dict[str, Any] | None:
    """The authenticated user, or ``None``. Never raises.

    For call sites that must behave differently for a signed-in user without
    demanding one — the shape U8 needs for the transition period, when an
    approval may still be recorded by an unauthenticated operator and the DOCX
    has to say "기록 없음(인증 이전)" instead of inventing a name.
    """
    resolved = resolve_request_session(request)
    return resolved["user"] if resolved else None


def current_user(request: Request) -> dict[str, Any]:
    """The authenticated user, or 401. THE dependency U8 enforces with."""
    user = optional_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail=pick_language_text(_NOT_AUTHENTICATED, _language(request)))
    return user


def require_role(*roles: str) -> Callable[..., dict[str, Any]]:
    """A dependency that also demands one of ``roles``.

    ``admin`` is NOT implicitly granted every other role: a reviewer gate that
    silently accepts an admin would make "who reviewed this" unanswerable in
    exactly the document U8 is building. Name every role a route accepts —
    ``require_role("reviewer", "admin")`` if that is what you mean.
    """
    unknown = [role for role in roles if role not in auth.ROLES]
    if unknown:
        # A typo'd role name would otherwise produce a route that can never be
        # called by anybody, and would look like a permissions bug forever.
        raise ValueError(f"Unknown role(s) {unknown}; expected from {auth.ROLES}")
    allowed = frozenset(roles)

    def _require_role(request: Request, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if user["role"] not in allowed:
            raise HTTPException(status_code=403, detail=pick_language_text(_FORBIDDEN, _language(request)))
        return user

    return _require_role


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response):
    """Exchange credentials for a session cookie.

    A second login does NOT invalidate the first: a consultant with the model
    open on the desk machine and the report open on a laptop is a normal day,
    and silently killing one of those sessions would be a bug reported as
    "Prism logs me out at random".
    """
    language = _language(request, payload.language)
    user = auth.authenticate(payload.username, payload.password)
    if user is None:
        # One message, one status, for every cause. See the module docstring.
        raise HTTPException(status_code=401, detail=pick_language_text(_LOGIN_FAILED, language))

    token, session = auth.create_session(user["id"])
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=int(auth.SESSION_TTL_HOURS * 3600),
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
        path="/",
    )
    logger.info("auth: %s (%s) signed in", user["username"], user["role"])
    return {
        "user": user,
        "session": {"expires_at": session["expires_at"]},
    }


@router.post("/logout")
def logout(request: Request, response: Response):
    """End this session and clear the cookie.

    Always 200, never 401: "log me out" must succeed for a caller whose token
    already expired, and the response must not report whether the token it
    carried was real.
    """
    revoked = auth.revoke_session(_token_from_request(request))
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
    )
    return {"revoked": bool(revoked)}


@router.get("/whoami")
def whoami(request: Request, user: dict[str, Any] = Depends(current_user)):
    """Who this request is, and how long it stays that way. 401 when nobody."""
    resolved = resolve_request_session(request)
    session = resolved["session"] if resolved else {}
    return {"user": user, "session": session}

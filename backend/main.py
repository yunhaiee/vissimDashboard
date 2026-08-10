import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.requests import HTTPConnection as StarletteConnection
from routers import assistant, datasets, network, network_diff, scenarios, search, simulations, geojson, validation, exports, report, trajectories, agent_audit, agent_downloads, health, observations
from routers import admin as admin_router
from routers import auth as auth_router
from routers import workspaces as workspaces_router
from services import auth as auth_service
from services import membership, workspaces
from services.agent.planner import pick_language_text
from services.agent.sessions import sweep_stale_sessions
from services.license_guard import license_required, license_status

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """M2 ops (docs/phase2/DECISIONS.md, "M2 ops" deferred finding):
    ``runtime/agent_sessions`` had no cleanup/TTL. Sweep it once per boot so
    the directory doesn't grow without bound. ``sweep_stale_sessions`` is
    already best-effort internally (a single locked/inaccessible session
    directory is skipped, not raised), but startup must survive even an
    unexpected failure in this module, so it is wrapped here too — a failed
    sweep must never prevent the backend from starting."""
    try:
        sweep_stale_sessions()
    except Exception:  # noqa: BLE001 - a cleanup failure must never block startup
        logger.exception("Agent session sweep failed at startup; continuing without cleanup.")
    yield


app = FastAPI(title="VISSIM Platform API", version="1.0.0", lifespan=lifespan)


_SUPPORTED_LANGUAGES = ("ko", "en", "ja")

#: Paths exempt from the workspace MEMBERSHIP check below (never from the
#: unknown-workspace 404, which predates this unit). Exactly one prefix, and it
#: is there to prevent a lockout loop rather than as a convenience: a browser
#: holding a stale non-default ``vissim-workspace-id`` in localStorage is
#: precisely the state a person who cannot sign in is likely to be in, and
#: 403-ing their login attempt because they are not a member of a project they
#: cannot leave would make the fix unreachable from the UI. ``lib/api.ts``
#: already strips the header from auth calls structurally; this is the
#: server-side half of the same promise, so the guarantee does not depend on
#: which client is talking.
_MEMBERSHIP_EXEMPT_PREFIXES = ("/api/auth/",)


def _request_language(headers: Headers) -> str:
    """ko unless ``Accept-Language`` clearly asks for en/ja.

    The middleware runs before routing, so it cannot use ``routers/auth.py``'s
    ``_language`` (which also honours a ``?language=`` query parameter on the
    routes that document one). Header-only is the honest subset here.
    """
    primary = (headers.get("accept-language") or "").split(",")[0].split("-")[0].strip().lower()
    return primary if primary in _SUPPORTED_LANGUAGES else "ko"


def _caller_user_id(scope) -> int | None:
    """The authenticated user's id for this ASGI scope, or ``None``.

    Reads the token through ``routers.auth.session_token_from_request`` so the
    middleware and the route dependencies agree on where a session token lives.
    A failure to resolve is ``None`` (anonymous), never an exception: this runs
    ahead of every route, and an auth-store hiccup must degrade to "we do not
    know who you are" — which then fails CLOSED at the membership check — not
    to a 500 on the whole API.

    ``HTTPConnection``, not ``Request``: the latter asserts ``scope["type"] ==
    "http"`` in its constructor, so it raised on every websocket scope and the
    blanket ``except`` below quietly turned that into "anonymous". Fail-closed,
    so not a hole — but it meant a genuine MEMBER's websocket could never be
    recognised either. ``HTTPConnection`` is the common base that carries
    ``.cookies`` and ``.headers`` for both scope types, which is all
    ``session_token_from_request`` reads.
    """
    try:
        request = StarletteConnection(scope)
        resolved = auth_service.resolve_session(auth_router.session_token_from_request(request))
    except Exception:  # noqa: BLE001 - see the docstring: never fatal, always anonymous
        logger.warning("workspace scope: could not resolve the session; treating as anonymous", exc_info=True)
        return None
    return int(resolved["user"]["id"]) if resolved else None


class WorkspaceScopeMiddleware:
    """Bind every request to the workspace named by ``X-Workspace-Id``.

    A raw ASGI middleware, NOT ``@app.middleware("http")``: Starlette's
    ``BaseHTTPMiddleware`` runs the downstream app in a separate anyio task, so
    a ``ContextVar`` set in its ``dispatch`` is not reliably visible to the
    endpoint. This class calls the downstream app directly, in the SAME task,
    which is what makes ``services.workspaces.current_workspace_id()`` work in
    both async endpoints (same context) and sync endpoints (Starlette's
    ``run_in_threadpool`` copies the context into the worker thread).

    Registered FIRST so it ends up INNERMOST — inside ``CORSMiddleware`` — so
    an unknown-workspace 404 still carries CORS headers and the browser can
    read the error instead of reporting an opaque CORS failure.

    Absent header → the default workspace, which is what keeps every
    pre-workspace client (and every existing test) working unchanged. A
    malformed or unregistered id → 404 with ``code:
    "workspace_not_found"``; never a silent fallback, because a stale id in a
    client's localStorage resolving to somebody else's project is precisely
    the leak this boundary exists to prevent.

    **Unit P④ adds the membership gate**, and this is the one place in the app
    that enforces it. A PRESENT header naming a NON-default workspace now also
    has to pass ``services.membership.user_can_access``: an anonymous caller,
    or a signed-in caller who is neither a member nor an admin, gets 403 with
    ``code: "workspace_forbidden"`` — again never a fallback to the default
    workspace, for exactly the reason the 404 above is not one. The default
    workspace itself stays open to anonymous callers; the rest of Prism is not
    behind a login and this unit does not put it there (the full rule, with the
    reasoning for each row, is the table in ``services/membership.py``).

    Why HERE and not a ``Depends`` on each router: this class is already the
    only code that resolves the workspace for every request, so the boundary
    holds for routes that do not exist yet. Eighteen routers each remembering
    to add a dependency is not a boundary, it is a habit.

    Cost: one SHA-256 plus one indexed SELECT, and ONLY on requests that
    actually name a non-default workspace. A request with no header takes the
    fast path below and does no auth work at all — which is every request the
    app made before workspaces existed.

    WebSockets are bound leniently (unknown id → default) rather than closed,
    because a raw-ASGI websocket cannot be answered with a 403 body the way an
    HTTP request can. What they are NOT is exempt from the membership rule.
    An earlier draft of this docstring claimed a websocket "could never carry"
    the header because the browser ``WebSocket`` API cannot set one; that is
    true of browsers and false of every other client (wscat, python-websockets,
    curl), so it was a browser limitation mistaken for a security property.
    A websocket that names a non-default workspace its caller may not enter is
    therefore bound to the DEFAULT workspace instead of the named one — the
    handshake still succeeds, but it never gets the binding it asked for.

    Cookies ride the websocket handshake like any other request header, so
    :meth:`_may_enter` reads the caller's session there exactly as it does over
    HTTP; no separate code path, and a member's websocket keeps working.

    Independent of this gate, ``/api/simulations/ws/{job_id}`` still resolves
    job ids through the GLOBAL registry rather than the workspace-scoped one
    (recorded at ``routers/simulations.py:142-147`` since unit 1.4). Binding
    that socket to the default workspace does not fix that; it only stops the
    membership boundary from handing out a binding it never checked.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        raw = Headers(scope=scope).get("x-workspace-id")
        try:
            workspace_id = workspaces.resolve_workspace_id(raw)
        except workspaces.WorkspaceNotFoundError:
            if scope["type"] == "websocket":
                workspace_id = workspaces.default_workspace_id()
            else:
                response = JSONResponse(
                    status_code=404,
                    content={
                        "detail": {
                            "code": "workspace_not_found",
                            "message": f"Unknown workspace id: {raw!r}",
                        }
                    },
                )
                await response(scope, receive, send)
                return

        # The membership gate. `raw` being falsy means "no header at all",
        # which resolve_workspace_id already turned into the default workspace
        # -- always open, so there is nothing to check and no reason to touch
        # the auth database on the app's most common request shape.
        if raw and not self._may_enter(scope, workspace_id):
            if scope["type"] == "websocket":
                # No 403 body is possible before the handshake, so refuse the
                # BINDING rather than the connection: fall back to the default
                # workspace, which is what an unauthenticated caller would have
                # got with no header at all. Never honour a non-default binding
                # the caller has not earned.
                workspace_id = workspaces.default_workspace_id()
            else:
                headers = Headers(scope=scope)
                response = JSONResponse(
                    status_code=403,
                    content={
                        "detail": {
                            "code": membership.FORBIDDEN_CODE,
                            "message": pick_language_text(
                                membership.FORBIDDEN_TEXT, _request_language(headers)
                            ),
                            "workspace_id": workspace_id,
                        }
                    },
                )
                await response(scope, receive, send)
                return

        token = workspaces.set_current_workspace_id(workspace_id)
        try:
            await self.app(scope, receive, send)
        finally:
            workspaces.reset_current_workspace_id(token)

    @staticmethod
    def _may_enter(scope, workspace_id: str) -> bool:
        """Whether this request may be scoped to ``workspace_id``.

        Split out so the exemption list and the predicate are one readable
        expression each, and so a test can drive the rule without an ASGI
        round trip.
        """
        path = scope.get("path") or ""
        if any(path.startswith(prefix) for prefix in _MEMBERSHIP_EXEMPT_PREFIXES):
            return True
        return membership.user_can_access(_caller_user_id(scope), workspace_id)


app.add_middleware(WorkspaceScopeMiddleware)


def _load_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]

    # Vercel preview/prod URLs can be injected automatically as VERCEL_URL.
    vercel_url = os.getenv("VERCEL_URL", "").strip()
    if vercel_url:
        origins.append(f"https://{vercel_url}")

    # Preserve insertion order while removing duplicates.
    deduped: list[str] = []
    seen: set[str] = set()
    for origin in origins:
        if origin not in seen:
            deduped.append(origin)
            seen.add(origin)
    return deduped


def _load_cors_origin_regex() -> str | None:
    return os.getenv("CORS_ORIGIN_REGEX", r"https://.*\.vercel\.app").strip() or None


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return False
    if origin in _load_cors_origins():
        return True

    origin_regex = _load_cors_origin_regex()
    return bool(origin_regex and re.fullmatch(origin_regex, origin))


app.add_middleware(
    CORSMiddleware,
    allow_origins=_load_cors_origins(),
    allow_origin_regex=_load_cors_origin_regex(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_private_network_access_header(request, call_next):
    private_network_preflight = (
        request.method == "OPTIONS"
        and request.headers.get("access-control-request-private-network") == "true"
    )

    if private_network_preflight:
        origin = request.headers.get("origin")
        if not _origin_allowed(origin):
            return Response("Disallowed CORS origin", status_code=400)

        request_method = request.headers.get("access-control-request-method", "GET")
        request_headers = request.headers.get("access-control-request-headers", "*")
        return Response(
            "OK",
            status_code=200,
            headers={
                "Access-Control-Allow-Origin": origin or "",
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Allow-Methods": request_method,
                "Access-Control-Allow-Headers": request_headers,
                "Access-Control-Allow-Private-Network": "true",
                "Access-Control-Max-Age": "600",
                "Vary": "Origin",
            },
        )

    response = await call_next(request)
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


@app.middleware("http")
async def require_activated_license(request, call_next):
    if request.method == "OPTIONS" or not license_required():
        return await call_next(request)

    status = license_status()
    if status["valid"]:
        return await call_next(request)

    return JSONResponse(
        status_code=403,
        content={
            "detail": "Dochak Prism license activation is required.",
            "message": status["message"],
            "machine_id": status["machine_id"],
        },
    )


app.include_router(network.router)
# Shares network.router's /api/network prefix but only claims the /search
# sub-paths — distinct, fully-specified routes, so registration order does not
# affect matching.
app.include_router(search.router)
# Roadmap row 2.4: /api/network/diff — two snapshots of one agent session's
# network, compared structurally. Another fully-specified sub-path of the
# /api/network space (same arrangement as search.router above), and purely
# ADDITIVE: no existing route changes.
app.include_router(network_diff.router)
app.include_router(datasets.router)
# Unit 1.4: project workspaces — the isolation boundary uploads live inside.
app.include_router(workspaces_router.router)
app.include_router(scenarios.router)
app.include_router(simulations.router)
app.include_router(geojson.router)
app.include_router(validation.router)
app.include_router(assistant.router)
app.include_router(exports.router)
app.include_router(report.router)
app.include_router(trajectories.router)
app.include_router(agent_downloads.router)
# Phase 1.3: /api/agent/audit — the action ledger as a .docx report section.
# Distinct prefix from agent_downloads' /api/agent/downloads, so registration
# order does not affect matching.
app.include_router(agent_audit.router)
# Unit U7 (roadmap 2.5): /api/auth — login, logout, whoami. Purely ADDITIVE:
# three new routes under a prefix nothing else claims, and no existing endpoint
# gains a dependency in this unit. Enforcement is U8's change.
app.include_router(auth_router.router)
# Unit P④: /api/admin — user administration and workspace membership, the
# browser half of backend/tools/manage_users.py. Every route is admin-only via
# a router-level require_role("admin"); a prefix nothing else claims.
app.include_router(admin_router.router)
# Phase 2 unit U10 (roadmap 2.1): /api/observations — the per-workspace store
# of ingested field counts that feeds the reality check's observed side.
# Purely ADDITIVE: a prefix nothing else claims, and no existing route changes.
app.include_router(observations.router)
app.include_router(health.router)


@app.get("/")
def root():
    return {"status": "ok", "service": "VISSIM Platform API"}

import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from routers import assistant, datasets, network, scenarios, search, simulations, geojson, validation, exports, report, trajectories, agent_downloads, health
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
app.include_router(datasets.router)
app.include_router(scenarios.router)
app.include_router(simulations.router)
app.include_router(geojson.router)
app.include_router(validation.router)
app.include_router(assistant.router)
app.include_router(exports.router)
app.include_router(report.router)
app.include_router(trajectories.router)
app.include_router(agent_downloads.router)
app.include_router(health.router)


@app.get("/")
def root():
    return {"status": "ok", "service": "VISSIM Platform API"}

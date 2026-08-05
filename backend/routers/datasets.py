"""Dataset upload endpoints for local Vissim network visualization.

Every route here is workspace-scoped: ``main.WorkspaceScopeMiddleware`` binds
the request's workspace before the handler runs, and
``services.datasets``' accessors resolve their paths from it. Uploading in one
workspace is invisible from another — that is unit 1.4's acceptance property.
"""
from __future__ import annotations

from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Request

from services.datasets import (
    DatasetStorageError,
    get_active_dataset,
    reset_active_dataset,
    save_active_dataset,
)

router = APIRouter(prefix="/api/datasets", tags=["datasets"])

#: Storage failures are 409, not 500. ``DatasetStorageError`` is only raised
#: from paths that have already guaranteed the previous dataset is intact, so
#: "conflict — nothing changed, resolve it and retry" is literally true, and on
#: Windows the overwhelmingly common cause (something holding
#: ``uploads/current/current.inpx`` open — Vissim, a results reader, an
#: explorer window) is something the user can actually fix. A 500 told them
#: nothing and made a normal-operation failure look like a crash.
_STORAGE_CONFLICT_STATUS = 409

#: Matches ``frontend/next.config.ts``'s ``proxyClientMaxBodySize`` (250 MB).
#: The proxy cap alone is not a backend guarantee — a client that talks to
#: :8001 directly (which ``lib/api.ts`` deliberately does whenever the proxy
#: is unreachable) bypasses it entirely, and ``await request.body()`` buffers
#: the whole upload in memory. Refused up front from Content-Length so a
#: hostile body is never read.
MAX_UPLOAD_BYTES = 250 * 1024 * 1024

_TOO_LARGE_DETAIL = (
    f"Uploaded dataset is larger than the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."
)


def _reject_oversized_body(request: Request) -> None:
    raw_length = request.headers.get("content-length")
    if not raw_length:
        return
    try:
        declared = int(raw_length)
    except ValueError:
        return
    if declared > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=_TOO_LARGE_DETAIL)


@router.get("/active")
def active_dataset():
    return get_active_dataset()


@router.post("/upload")
async def upload_dataset(request: Request, x_filename: str | None = Header(None)):
    query_filename = request.query_params.get("filename")
    filename = unquote(query_filename or x_filename or "uploaded.inpx")
    _reject_oversized_body(request)
    content = await request.body()
    # Second check on the bytes actually received: a chunked upload carries no
    # Content-Length at all, so the header check above cannot be the only gate.
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=_TOO_LARGE_DETAIL)
    try:
        return save_active_dataset(filename, content)
    except ValueError as exc:
        # Bad BYTES (malformed .inpx, not a zip, zip bomb) -- unchanged 400.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except DatasetStorageError as exc:
        raise HTTPException(status_code=_STORAGE_CONFLICT_STATUS, detail=str(exc)) from exc
    except OSError as exc:
        # Anything the disk refused while STAGING (out of space, a permission
        # problem under uploads/). The staging directory is already discarded
        # and the previous dataset never moved, so this is the same class of
        # answer -- it just never had a mapping and 500'd.
        raise HTTPException(
            status_code=_STORAGE_CONFLICT_STATUS,
            detail=(
                "Could not write the uploaded dataset to disk. The previous dataset was left "
                f"unchanged. [{type(exc).__name__}: {exc}]"
            ),
        ) from exc


@router.post("/upload-inpx")
async def upload_inpx_dataset(request: Request, x_filename: str | None = Header(None)):
    return await upload_dataset(request, x_filename)


@router.post("/reset")
def reset_dataset():
    try:
        return reset_active_dataset()
    except DatasetStorageError as exc:
        raise HTTPException(status_code=_STORAGE_CONFLICT_STATUS, detail=str(exc)) from exc

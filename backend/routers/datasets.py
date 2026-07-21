"""Dataset upload endpoints for local Vissim network visualization."""
from __future__ import annotations

from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Request

from services.datasets import get_active_dataset, reset_active_dataset, save_active_dataset

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


@router.get("/active")
def active_dataset():
    return get_active_dataset()


@router.post("/upload")
async def upload_dataset(request: Request, x_filename: str | None = Header(None)):
    query_filename = request.query_params.get("filename")
    filename = unquote(query_filename or x_filename or "uploaded.inpx")
    try:
        content = await request.body()
        return save_active_dataset(filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/upload-inpx")
async def upload_inpx_dataset(request: Request, x_filename: str | None = Header(None)):
    return await upload_dataset(request, x_filename)


@router.post("/reset")
def reset_dataset():
    return reset_active_dataset()

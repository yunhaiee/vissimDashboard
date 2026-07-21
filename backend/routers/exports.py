"""Download endpoints that build Excel exports of the model deliverables."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response

from services.exporters import EXPORTS, build_combined_export, export_media_type

router = APIRouter(prefix="/api/exports", tags=["exports"])

_XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("")
def list_exports():
    """List the available downloadable deliverables."""
    return [
        {"key": key, "label": meta["label"], "filename": meta["filename"]}
        for key, meta in EXPORTS.items()
    ]


@router.get("/combined")
def download_combined(keys: str = Query("", description="Comma-separated export keys")):
    """Build one .xlsx containing the selected exports, each as its own sheet(s)."""
    selected = [k.strip() for k in keys.split(",") if k.strip()]
    valid = [k for k in selected if k in EXPORTS]
    if not valid:
        raise HTTPException(status_code=400, detail="No valid export keys selected.")
    try:
        content = build_combined_export(valid)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to build combined export: {exc}") from exc

    filename = "vissim_export.xlsx"
    return Response(
        content=content,
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"},
    )


@router.get("/{key}")
def download_export(key: str):
    meta = EXPORTS.get(key)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"Unknown export: {key}")
    try:
        content = meta["builder"]()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to build export '{key}': {exc}") from exc

    filename = meta["filename"]
    return Response(
        content=content,
        media_type=export_media_type(key),
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"},
    )

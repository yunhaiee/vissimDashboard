"""Validation endpoints – compare simulation results against real-world counts."""
from __future__ import annotations

from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from services import workbook_mapping
from services.datasets import clear_active_real_counts_excel, get_active_real_counts_excel, save_active_real_counts_excel
from services.validation import (
    clear_validation_caches,
    create_base_workbook_template,
    geh,
    load_base_workbook,
    load_real_counts,
    pct_error,
)

router = APIRouter(prefix="/api/validation", tags=["validation"])


# --- Flexible workbook mapping (arbitrary spreadsheet -> Counts/TravelTimes) ---
# See services/workbook_mapping.py's module docstring for the full flow and
# the self-comparison trap this exists to catch. This is additive: the
# template-shaped POST /base-workbook above is completely unchanged, and the
# frontend is expected to try that path FIRST, only falling back to
# inspect/mapping when it comes back with warnings.

class WorkbookMappingSpec(BaseModel):
    sheet: str
    header_row: int = Field(ge=1)
    # canonical field name (e.g. "observed_volume") -> 0-indexed column.
    fields: dict[str, int]


class WorkbookMappingRequest(BaseModel):
    upload_token: str
    counts: WorkbookMappingSpec | None = None
    travel_times: WorkbookMappingSpec | None = None


def _to_mapping_spec(spec: WorkbookMappingSpec | None) -> workbook_mapping.MappingSpec | None:
    if spec is None:
        return None
    return {"sheet": spec.sheet, "header_row": spec.header_row, "fields": spec.fields}


@router.get("/real-counts")
def get_real_counts(period: str = ""):
    """Return real-world (observed) traffic counts per node from the reference Excel.

    ``period`` (``AM``/``PM``) selects the time period; empty auto-selects AM
    when present (backward-compatible).
    """
    data = load_real_counts(str(get_active_real_counts_excel()), period.upper() or None)
    return list(data.values())


@router.get("/base-workbook")
def get_base_workbook():
    """Return normalized base-model validation workbook data."""
    return load_base_workbook(str(get_active_real_counts_excel()))


@router.post("/base-workbook")
async def upload_base_workbook(request: Request, x_filename: str | None = Header(None)):
    """Upload a standalone standard validation workbook."""
    query_filename = request.query_params.get("filename")
    filename = unquote(query_filename or x_filename or "real_counts.xlsx")
    content = await request.body()
    try:
        save_active_real_counts_excel(filename, content)
        clear_validation_caches()
        return load_base_workbook(str(get_active_real_counts_excel()))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/base-workbook")
def reset_base_workbook():
    """Remove the uploaded validation workbook and return the empty/default workbook state."""
    clear_active_real_counts_excel()
    clear_validation_caches()
    return load_base_workbook(str(get_active_real_counts_excel()))


@router.get("/base-workbook/template")
def get_base_workbook_template():
    try:
        content = create_base_workbook_template()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="real_counts_template.xlsx"'},
    )


@router.post("/base-workbook/inspect")
async def inspect_base_workbook(request: Request, x_filename: str | None = Header(None)):
    """Upload an ARBITRARY workbook for inspection (no persistence). Returns,
    per sheet (bounded): the detected header row, headers, a few sample data
    rows, and an alias/remembered-mapping based proposed field mapping. The
    file is staged server-side under a returned ``upload_token`` so the
    mapping UI's later preview/import calls don't need to re-upload it."""
    query_filename = request.query_params.get("filename")
    filename = unquote(query_filename or x_filename or "upload.xlsx")
    content = await request.body()
    try:
        return workbook_mapping.inspect_workbook(filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/base-workbook/mapping/{token}/resolve")
def resolve_base_workbook_mapping(token: str, sheet: str, header_row: int = 1):
    """Recompute headers/sample rows/proposed mapping for one sheet at a
    user-chosen header row, against the ALREADY-staged workbook (no
    re-upload, no commit)."""
    try:
        return workbook_mapping.resolve_sheet_mapping(token, sheet, header_row)
    except workbook_mapping.StagedWorkbookNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/base-workbook/mapping/preview")
def preview_base_workbook_mapping(payload: WorkbookMappingRequest):
    """Parse the staged workbook with an EXPLICIT field mapping and return a
    bounded preview of the parsed rows plus any refusal reasons -- without
    committing anything. Lets the UI show real numbers (and catch a wrong
    column) before the user confirms the import."""
    if payload.counts is None and payload.travel_times is None:
        raise HTTPException(status_code=400, detail="At least one of counts or travel_times mapping must be provided.")
    try:
        return workbook_mapping.preview_mapping(
            payload.upload_token,
            _to_mapping_spec(payload.counts),
            _to_mapping_spec(payload.travel_times),
        )
    except workbook_mapping.StagedWorkbookNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/base-workbook/mapping/import")
def import_base_workbook_mapping(payload: WorkbookMappingRequest):
    """Commit an explicit field mapping: parses the staged workbook through
    the SAME row-building code the standard template importer uses, and
    REFUSES (400, with localized {code, ko, en, ja} reasons) rather than
    importing silently when a required field is unmapped, observed/model are
    mapped to the same column, the observed column has no usable numeric
    values, or observed and model are identical row-for-row (the
    model-compared-against-itself trap -- see services/workbook_mapping.py's
    module docstring). On success this persists a canonical Counts/
    TravelTimes workbook exactly like a template upload would, so
    GET /base-workbook keeps working unchanged, and remembers the mapping
    for future re-uploads of the same layout."""
    if payload.counts is None and payload.travel_times is None:
        raise HTTPException(status_code=400, detail="At least one of counts or travel_times mapping must be provided.")
    try:
        return workbook_mapping.import_mapping(
            payload.upload_token,
            _to_mapping_spec(payload.counts),
            _to_mapping_spec(payload.travel_times),
        )
    except workbook_mapping.MappingRefusedError as exc:
        raise HTTPException(status_code=400, detail={"issues": exc.issues}) from exc
    except workbook_mapping.StagedWorkbookNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/compare")
def get_validation_compare(scenario_totals: str = "", period: str = ""):
    """Compare scenario simulation totals against real-world counts.

    Query param ``scenario_totals`` is a JSON-encoded mapping
    ``{scenario_id: {node_id: volume}}`` – built by the frontend from
    the existing compare-summary response.  Returns per-node GEH and
    % error for every scenario.

    This endpoint is intentionally thin: heavy aggregation lives in
    the frontend to avoid tight coupling.
    """
    import json

    real = load_real_counts(str(get_active_real_counts_excel()), period.upper() or None)

    try:
        sim_map: dict[str, dict[str, float]] = json.loads(scenario_totals) if scenario_totals else {}
    except Exception:
        sim_map = {}

    rows = []
    for node_id, node_data in real.items():
        obs = node_data["total_real"]
        node_row: dict = {
            "node_id": node_id,
            "node_name": node_data["node_name"],
            "total_real": obs,
            "scenarios": {},
        }
        for sc_id, node_vols in sim_map.items():
            sim = node_vols.get(node_id)
            node_row["scenarios"][sc_id] = {
                "sim": sim,
                "pct_error": pct_error(sim, obs),
                "geh": geh(sim, obs) if sim is not None and obs is not None else None,
            }
        rows.append(node_row)

    return rows

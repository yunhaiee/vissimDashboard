"""Validation endpoints – compare simulation results against real-world counts."""
from __future__ import annotations

from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Request, Response

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

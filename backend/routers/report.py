"""Report builder endpoint — assemble selected sections into one Word file.

Also serves the report page's per-block "데이터 내보내기" CSV downloads (R4.1):
each ``/data/{block_type}`` route reuses the exact same row-computation
functions the corresponding .docx section renders from (see
``services/report_builder.py`` / ``services/report_exports.py``) so the CSV
numbers are guaranteed to match the exported document, never a second
computation of the same quantities.
"""
from __future__ import annotations

import csv
import io
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.report_builder import build_report, geh_validation_csv_rows
from services.report_exports import flow_geh_csv_rows, los_summary_csv_rows, travel_chart_specs

router = APIRouter(prefix="/api/report", tags=["report"])

_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_CSV_MEDIA = "text/csv; charset=utf-8"


class ReportBlock(BaseModel):
    type: str
    heading: str | None = None
    body: str | None = None


class ReportMeta(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    author: str | None = None
    date: str | None = None
    project: str | None = None
    revision: str | None = None
    description: str | None = None
    reviewer: str | None = None


class ReportStyle(BaseModel):
    """Company style profile (R4.2). Every field is optional; a field left
    unset (None) means "use today's pre-existing hardcoded value" all the way
    down into ``render_travel_chart`` -- this is what keeps the no-profile
    report byte-for-byte identical to before this feature existed.

    ``palette`` is positional-by-role (not a generic series list): index 0 is
    the model/simulation line, index 1 is the observed line. Defaults (when
    absent) are the report's pre-existing hardcoded hex colours.
    """

    palette: list[str] | None = None
    font_family: str | None = None
    font_size_title: int | None = None
    font_size_label: int | None = None
    figure_width_in: float | None = None
    figure_height_in: float | None = None


class ReportSpec(BaseModel):
    meta: ReportMeta = Field(default_factory=ReportMeta)
    blocks: list[ReportBlock] = Field(default_factory=list)
    filename: str | None = None
    style: ReportStyle | None = None


@router.get("/blocks")
def list_blocks():
    """Available report block types (for the builder UI)."""
    return [
        {"type": "cover", "label": "표지 + 메타"},
        {"type": "overview", "label": "개요 (자동 요약)"},
        {"type": "geh-validation", "label": "교통량 GEH 검증표"},
        {"type": "los-summary", "label": "LoS 요약표"},
        {"type": "flow-geh-validation", "label": "Flow / GEH 검증표"},
        {"type": "travel-time-charts", "label": "통행시간 차트"},
        {"type": "text", "label": "자유 텍스트 단락"},
    ]


@router.post("/build")
def build(spec: ReportSpec):
    payload = spec.model_dump()
    try:
        content = build_report(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to build report: {exc}") from exc

    raw = (spec.filename or "vissim_report").strip() or "vissim_report"
    filename = raw if raw.lower().endswith(".docx") else f"{raw}.docx"
    return _docx_response(content, filename)


def _docx_response(content: bytes, filename: str):
    from fastapi import Response

    return Response(
        content=content,
        media_type=_DOCX_MEDIA,
        headers={"Content-Disposition": f"attachment; filename=\"report.docx\"; filename*=UTF-8''{quote(filename)}"},
    )


# --- data extraction (R4.1) --------------------------------------------------
# CSV-only for v1: every source here is a flat table (one row per node/
# movement/route), which is the natural shape for CSV and needs no sheet
# grouping -- XLSX/multi-sheet is left for a block type that actually needs
# it (none of the report's current blocks do). See DECISIONS.md for the note.

_DATA_BLOCK_HEADERS: dict[str, list[tuple[str, str]]] = {
    # block type -> ordered [(row-dict key, CSV column header), ...]
    "geh-validation": [
        ("period", "Period"), ("node", "Node"), ("observed", "Observed"),
        ("modelled", "Modelled"), ("geh", "GEH"), ("grade", "Grade"),
    ],
    "los-summary": [
        ("node", "Node"), ("approach", "Approach"), ("turn", "Direction"),
        ("am_volume", "AM Volume"), ("am_delay", "AM Delay"), ("am_los", "AM LOS"), ("am_qmax", "AM Max Queue"),
        ("pm_volume", "PM Volume"), ("pm_delay", "PM Delay"), ("pm_los", "PM LOS"), ("pm_qmax", "PM Max Queue"),
        ("row_type", "Row Type"),
    ],
    "flow-geh-validation": [
        ("period", "Period"), ("node", "Node"), ("counts", "No. Counts"),
        ("observed", "Observed Flow"), ("modelled", "Modelled Flow"), ("pct_diff", "% Diff"),
        # GEH % is over "GEH Counts", NOT "No. Counts": rows with 0 observed
        # and 0 modelled have an undefined GEH and are excluded from both the
        # numerator and the denominator (unit 1.5 D3). Both numbers ship so the
        # reader can see the shrunken denominator.
        ("flow_pct", "Flow %"), ("geh_pct", "GEH %"),
        ("geh_counts", "GEH Counts"), ("geh_excluded", "GEH Excluded (0 obs / 0 model)"),
        ("row_type", "Row Type"),
    ],
    "travel-time-charts": [
        ("name", "Route"), ("period", "Period"), ("dist_km", "Distance (km)"),
        ("obs", "Observed (s)"), ("model", "Model (s)"), ("low", "-15% Band (s)"), ("high", "+15% Band (s)"),
    ],
}


def _data_rows_for(block_type: str) -> list[dict]:
    if block_type == "geh-validation":
        return geh_validation_csv_rows()
    if block_type == "los-summary":
        return los_summary_csv_rows()
    if block_type == "flow-geh-validation":
        return flow_geh_csv_rows()
    if block_type == "travel-time-charts":
        return travel_chart_specs()
    raise HTTPException(
        status_code=404,
        detail=f"No data export for block type '{block_type}' (it has no table/figure, or is unknown).",
    )


@router.get("/data/{block_type}")
def block_data(block_type: str):
    """CSV of exactly the rows/numbers the given report block renders.

    Reuses the same computation the .docx section uses (see the imports
    above) -- this never re-derives GEH/flow/queue numbers a second time.
    """
    columns = _DATA_BLOCK_HEADERS.get(block_type)
    if columns is None:
        raise HTTPException(status_code=404, detail=f"No data export for block type '{block_type}'.")
    rows = _data_rows_for(block_type)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([header for _key, header in columns])
    for row in rows:
        writer.writerow([row.get(key, "") if row.get(key) is not None else "" for key, _header in columns])

    from fastapi import Response

    filename = f"{block_type}.csv"
    return Response(
        # utf-8-sig prepends the UTF-8 BOM so Excel (incl. Korean-locale
        # builds) auto-detects UTF-8 instead of mojibake-ing the Korean
        # grade/label text.
        content=buf.getvalue().encode("utf-8-sig"),
        media_type=_CSV_MEDIA,
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"},
    )

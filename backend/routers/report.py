"""Report builder endpoint — assemble selected sections into one Word file.

Also serves the report page's per-block "데이터 내보내기" CSV downloads (R4.1):
each ``/data/{block_type}`` route reuses the exact same row-computation
functions the corresponding .docx section renders from (see
``services/report_builder.py`` / ``services/report_exports.py``) so the CSV
numbers are guaranteed to match the exported document, never a second
computation of the same quantities.

``/style-profiles/*`` (Track R unit R-4) is the DOCX style-profile extractor +
per-workspace store — see ``services/report/style_profile.py``'s module
docstring for the extraction/storage doctrine. This file only adds the HTTP
boundary over it.

**Auth decision for ``POST /style-profiles`` (and ``PATCH .../{id}``), and the
precedent it was measured against.** This backend does not apply one uniform
auth rule to every upload route — two real, differently-decided precedents
exist side by side:

* ``POST /api/genesis/phase-diagrams/upload`` (``routers/genesis.py``) takes
  ``Depends(current_user)``. Its own docstring states why: the upload becomes
  a REVIEW RECORD immediately, and "the uploader's name is [stamped as]
  provenance" — the record persists past the request, and "who built this"
  has to be answerable later.
* ``POST /api/observations/upload`` (``routers/observations.py``) takes NO
  auth dependency at all — checked directly, and confirmed by
  ``routers/observations.py``'s own module docstring: it only STAGES bytes
  under a bare token; nothing durable is created until a SEPARATE
  ``POST /ingest`` call (also unauthenticated in this build) names that
  token. There is no persisted, named record at upload time for an identity
  to attach to.

The rule that reconciles them, not stated explicitly anywhere before this
unit but consistent across both: **auth tracks whether the call durably
creates a named, provenance-bearing record, not whether the call is spelled
"upload".** ``POST /style-profiles`` is the phase-diagrams shape, not the
observations shape — it does not stage a token for a later step; it
extracts and WRITES a per-workspace registry entry (id, name, source file,
created_at) in the same request, exactly like a phase-diagram review. So it
takes ``Depends(current_user)``, consistent with that rule rather than
copied from either route by surface resemblance. ``PATCH .../{id}`` follows
for the identical reason ``StepAttestationRequest``/``DemandTargetRequest``
already establish in this codebase: a correction is stamped with WHO made
it, so it cannot be anonymous either. The two ``GET`` routes stay
unauthenticated, in parity with every other read already on this router
(``GET /spine``, ``GET /figures``) — reading a stored proposal is not making
one.
"""
from __future__ import annotations

import csv
import io
from typing import Any
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from routers.auth import current_user
from routers.datasets import MAX_UPLOAD_BYTES, _TOO_LARGE_DETAIL, _reject_oversized_body
from services.report import figures as report_figures
from services.report import style_profile
from services.report.spine import spine_payload
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


@router.get("/spine")
def spine():
    """The MRWA-required report section structure (Track R unit R-1).

    Read-only and parameterless: it returns the same declared structure on
    every call, because it IS a transcription of the guideline plus this
    build's honest availability audit — no run, dataset or workspace enters
    into it. ``services/report/spine.py`` owns the data; this route only
    serves it, so the future /report UI (R-5) and the DOCX builder read one
    spine rather than each keeping their own idea of the section list.

    Deliberately UNAUTHENTICATED, in parity with every other route on this
    router and with the genesis reads: it discloses the regulation's own
    sentences, the names of the modules that would fill each section, and
    which Table 5-15 outputs this product cannot extract. No client data, no
    project data, and no decision is made by reading it. (The workspace
    boundary in ``main.WorkspaceScopeMiddleware`` still applies, as it does to
    every route.)
    """
    return spine_payload()


@router.get("/figures")
def report_figures_route(
    scenario_id: str | None = Query(None, description="Run to score for the GEH node table"),
    period: str | None = Query(None, description="AM/PM window for the base counts workbook"),
    observation_set_id: str | None = Query(
        None, description="Score the GEH table against a stored observation set instead"
    ),
    observation_period: str | None = Query(None, description="Window within the observation set"),
    session_id: str | None = Query(
        None, description="Agent session to pull the approval-history table from"
    ),
    language: str = Query("ko", description="ko (default) / en / ja — gate check codes only"),
):
    """Track R unit R-2's four table figures (Track R layer 2), gathered in one
    call — the same "one section, one bundle" shape ``GET /spine`` (R-1) and
    ``GET /api/calibration/rail`` already use, so the future DOCX builder and
    the /report UI (R-5) read one list instead of assembling it themselves
    from four separate round trips.

    Each figure is gathered INDEPENDENTLY (``services.report.figures.
    all_figures``): a request naming no ``session_id`` still returns three
    real figures and one named absence for the approval-history table, never
    a 4xx for the whole set — a missing input is data on this route, exactly
    as it is on ``GET /api/calibration/reality-check``.

    Deliberately UNAUTHENTICATED, in parity with every other route already on
    this router: ``GET /spine`` states the parity rule for the section
    structure, and ``GET /data/{block_type}`` above it is the parity
    precedent for the DATA itself — it already serves this exact class of
    material (GEH validation rows, LoS rows, travel-time chart series) over
    HTTP with no auth dependency. These figures are new VIEWS of readers
    (``build_reality_check_report``, the audit ledger, the applied-rows
    verdicts, the gate payload) that ``/api/calibration``'s own GETs already
    expose unauthenticated too — ``tests/test_auth_core.py``'s route-scope
    test states outright that "the rail, the score, the Table 5-6 verdicts"
    stay open on that router. This route is not a first exposure of any of
    that data, so it follows the existing parity rather than gating fresh.
    """
    return {
        "figures": report_figures.all_figures(
            scenario_id=scenario_id,
            period=period,
            observation_set_id=observation_set_id,
            observation_period=observation_period,
            session_id=session_id,
            language=language,
        )
    }


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
        # Appended (section-5 validation pack): the trailing
        # ``row_type="criterion"`` rows carry the MRWA section 5.6.3 criteria
        # this export cannot evaluate -- signal timing (Table 5-14, doc p.216)
        # and saturation flow (5.6.3.5, doc p.216) have no observed-side ingest
        # path, so they publish "not assessable" rather than being omitted (an
        # omitted criterion reads as a criterion that passed). Node/Total rows
        # leave this cell blank.
        ("criterion_state", "Criterion State"),
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


# --- style profiles (R4.3) ---------------------------------------------------


class StyleProfileCorrectionRequest(BaseModel):
    """A human correction to an extracted style profile. Nothing else.

    ``extra="forbid"`` is load-bearing exactly as it is on
    ``routers.calibration.StepAttestationRequest``: a body carrying
    ``edited_by``/``edited_at`` is a 422 at the door rather than a field that
    quietly does nothing, so "the editor is the session and the clock is the
    server's" is a property a caller can verify rather than a promise.
    """

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any] = Field(
        ...,
        description=(
            "Partial profile fields to correct, deep-merged onto the current profile "
            "(e.g. {'palette': {'emphasis': 'FF0000'}}). Must not be empty."
        ),
    )
    reason: str | None = Field(default=None, max_length=style_profile.MAX_REASON_CHARS)

    @field_validator("fields")
    @classmethod
    def _fields_not_empty(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("fields must not be empty -- a correction that changes nothing is not one")
        return value


@router.post("/style-profiles", status_code=201)
async def upload_style_profile(
    request: Request,
    x_filename: str | None = Header(None),
    name: str | None = Query(None, description="Human label; default: the source filename's stem."),
    user: dict[str, Any] = Depends(current_user),
):
    """One styled DOCX -> one extracted, stored style-profile PROPOSAL.

    Raw body + ``X-Filename``/``?filename=``, the two-step-upload SHAPE (not a
    second step here -- extraction and storage happen in this one request)
    ``routers/observations.py`` and ``routers/genesis.py`` already document:
    this backend carries no ``python-multipart``, so ``multipart/form-data``
    is not an option. ``services.report.style_profile.extract_style_profile``
    bomb-guards the bytes before ``python-docx`` ever opens them -- see that
    module's docstring for the refusal order.

    Returns the FULL stored record (``GET .../{id}``'s shape), so the caller
    sees ``not_extracted`` in the same response rather than a second round
    trip -- the extraction is a proposal, and the proposal is what this
    returns.
    """
    filename = unquote(request.query_params.get("filename") or x_filename or "")
    if not filename:
        raise HTTPException(
            status_code=400,
            detail="No filename given (X-Filename header or ?filename= query param).",
        )
    _reject_oversized_body(request)
    content = await request.body()
    # Second check on the bytes actually received: a chunked upload carries no
    # Content-Length, so the header check cannot be the only gate.
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=_TOO_LARGE_DETAIL)

    try:
        summary = style_profile.store_profile(content=content, source_file=filename, name=name)
    except style_profile.StyleProfileRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.to_dict()) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Could not write the style profile to disk. Nothing was stored. "
                f"[{type(exc).__name__}: {exc}]"
            ),
        ) from exc

    return style_profile.get_profile(summary["id"])


@router.get("/style-profiles")
def list_style_profiles():
    """Every stored profile's index entry in this workspace, newest first.

    Deliberately UNAUTHENTICATED, in parity with every other read on this
    router (``GET /spine``, ``GET /figures``): reading the record of a
    decision is not making one. See this file's module docstring for the
    full auth reasoning.
    """
    return {"profiles": style_profile.list_profiles()}


@router.get("/style-profiles/{profile_id}")
def get_style_profile(profile_id: str):
    """One profile's full record: the untouched original extraction, every
    correction ever applied (never overwritten), and the resolved profile a
    report-assembly step would actually use today."""
    try:
        return style_profile.get_profile(profile_id)
    except style_profile.StyleProfileRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.to_dict()) from exc


@router.patch("/style-profiles/{profile_id}")
def correct_style_profile(
    profile_id: str,
    body: StyleProfileCorrectionRequest,
    user: dict[str, Any] = Depends(current_user),
):
    """Record ONE human correction. Appends a new revision; never rewrites
    the original extraction -- see ``services.report.style_profile``'s
    module docstring ("틀리면 사람이 고친다")."""
    try:
        return style_profile.apply_correction(
            profile_id,
            fields=body.fields,
            edited_by=str(user.get("username") or user.get("id") or "").strip(),
            reason=body.reason,
        )
    except style_profile.StyleProfileRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.to_dict()) from exc

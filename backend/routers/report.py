"""Report builder endpoint — assemble selected sections into one Word file."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.report_builder import build_report

router = APIRouter(prefix="/api/report", tags=["report"])

_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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


class ReportSpec(BaseModel):
    meta: ReportMeta = Field(default_factory=ReportMeta)
    blocks: list[ReportBlock] = Field(default_factory=list)
    filename: str | None = None


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

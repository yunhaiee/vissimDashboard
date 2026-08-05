"""Phase 1.3: download one agent session's audit trail as a Word document.

``GET /api/agent/audit/{session_id}/export.docx`` renders the session's action
ledger (``runtime/agent_sessions/<id>/trace_<id>.jsonl``) as the
"파라미터 변경 기록 + 근거" section MRWA's report spine requires — see
``services/audit_trail.py`` for what the ledger is and how a parameter change
is recognized inside it.

Deliberately its own router rather than a route on ``agent_downloads.py``:
that one serves .inpx artifacts (and its ``/{session_id}/{version}`` route
would have to grow a special case to keep a literal path segment from being
read as a version). Read-only by construction — every function here only ever
reads bytes out of a session work dir; nothing in this module (or in
``services/audit_trail.py``) writes to, deletes from, or otherwise mutates a
session, and no Vissim COM is ever touched.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response

from services.audit_trail import build_audit_trail_docx, read_ledger

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agent/audit", tags=["agent-audit"])

_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

#: ko primary, en/ja through the same ``pick_language_text`` table lookup every
#: other deterministic agent text goes through (services/agent/planner.py); an
#: unknown code falls back to Korean there, so this is a soft allowlist used
#: only to keep the response header honest.
_SUPPORTED_LANGUAGES = ("ko", "en", "ja")


@router.get("/{session_id}/summary")
def audit_summary(session_id: str):
    """Counts only — what a caller needs to decide whether an export is worth
    offering, without building a document. 404s exactly where the download
    does, so a UI can use either as its existence check."""
    ledger = read_ledger(session_id)
    if ledger is None:
        raise HTTPException(status_code=404, detail="This session has no action ledger.")
    return {
        "session_id": ledger.session_id,
        "records": ledger.record_count,
        "changes": len(ledger.changes),
        "applied": ledger.applied_count,
        "refusals": len(ledger.refusals),
        "with_rationale": ledger.reasoned_count,
        "is_empty": ledger.is_empty,
    }


@router.get("/{session_id}/export.docx")
def download_audit_trail(
    session_id: str,
    language: str = Query("ko", description="ko (default) / en / ja"),
):
    """Stream the session's audit-trail .docx.

    404 when the session directory or its ledger file does not exist. A
    session whose ledger holds no parameter change still gets a real document
    stating exactly that — "nothing was changed" is an audit finding, not an
    error.
    """
    ledger = read_ledger(session_id)
    if ledger is None:
        raise HTTPException(status_code=404, detail="This session has no action ledger.")

    lang = language if language in _SUPPORTED_LANGUAGES else "ko"
    try:
        content = build_audit_trail_docx(ledger, language=lang)
    except Exception as exc:  # noqa: BLE001 - mirrors routers/report.py's build endpoint
        logger.exception("audit_trail: failed to build the export for session %s", ledger.session_id)
        raise HTTPException(status_code=500, detail=f"Failed to build the audit trail: {exc}") from exc

    filename = f"audit_trail_{ledger.session_id[:8]}.docx"
    return Response(
        content=content,
        media_type=_DOCX_MEDIA,
        headers={
            "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}"
        },
    )

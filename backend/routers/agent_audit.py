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
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response

from services import audit_trail
from services.audit_trail import build_audit_trail_docx, format_ts, read_ledger

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agent/audit", tags=["agent-audit"])

_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

#: ko primary, en/ja through the same ``pick_language_text`` table lookup every
#: other deterministic agent text goes through (services/agent/planner.py); an
#: unknown code falls back to Korean there, so this is a soft allowlist used
#: only to keep the response header honest.
_SUPPORTED_LANGUAGES = ("ko", "en", "ja")

#: Same default/ceiling ``routers/agent_downloads.py``'s own unscoped listing
#: uses (``_DEFAULT_LIST_LIMIT``/``_MAX_LIST_LIMIT`` there) -- a DELIBERATE
#: local duplication, not an import, matching the small-constant-duplication
#: convention this file's module docstring and ``services/audit_trail.py``'s
#: ``_SESSION_ID_RE`` already follow. Measured 2026-08-25 (refuter): an
#: unbounded walk took 2.92s over 500 synthetic session directories on this
#: dev box -- a real cost on a route a screen calls on every /report load.
_DEFAULT_LIST_LIMIT = 20
_MAX_LIST_LIMIT = 200


@router.get("/sessions")
def list_sessions_with_approvals(
    limit: int = Query(_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT),
):
    """Sessions in THIS workspace whose action ledger holds at least one
    applied/rolled-back/failed parameter change -- exactly the rows Table 1
    (:func:`services.audit_trail.add_audit_trail_section`) and
    ``services.report.figures.approval_history_table`` render from
    ``ledger.changes`` when handed that session's id. ``approval_count`` is
    ``len(ledger.changes)`` -- ALL of those rows, not just the ``"applied"``
    outcome -- because that is exactly what ``approval_history_table`` renders
    for the session: a rolled-back or failed change is still a row in that
    table, and a count that silently dropped them would disagree with the
    figure this picker exists to feed.

    **Track R unit R-5c** (docs/phase2/DECISIONS.md, 2026-08-24 "HWP는 v1 제외
    확정 · 승인 이력 세션 픽커는 위임"). R-5b named the gap this route closes:
    ``/report`` had no ``session_id`` input at all, so the approval-history
    figure rendered a permanent named absence -- see
    ``services.report.figures.approval_history_table``'s own docstring. This
    route is the honest source for what a picker should offer.

    **Deliberately NOT the set ``GET /api/agent/downloads`` (no
    ``session_id``) already serves** -- that route lists sessions that saved a
    write (``edit_NNN.inpx``), a DIFFERENT set: a plan can dispatch and get
    postwrite-rolled-back with no surviving snapshot (a real change, no saved
    file), and a snapshot can exist from a session whose plan was never gated
    at all (a saved file, no change this section would ever print). Using the
    downloads set here would be exactly the invented mapping the same
    DECISIONS.md entry rules out by name: "발명 매핑 금지: fetchAgentDownloads
    (쓰기 저장 세션)와 다른 집합이므로 원장 존재를 서버가 직접 측정한다." Only
    :func:`services.audit_trail.read_ledger` -- THIS router's own existing
    per-session reader, the identical function ``GET /{session_id}/summary``
    and ``GET /{session_id}/export.docx`` below already call -- answers "does
    this session's ledger actually carry a change", so this route measures it
    directly by walking every session directory through that one reader,
    rather than reusing a different set under an assumed correspondence.

    **Home: this router, not ``routers/report.py``.** Same reader and the same
    open-read auth precedent as the two routes immediately below (reading
    which sessions exist is not a decision, and discloses no more than
    ``GET /{session_id}/summary`` already would for any id a caller guessed).
    ``routers/report.py`` owns none of the machinery this route needs
    (``services.agent.sessions``, workspace-scoped ``SESSIONS_ROOT`` walking)
    and has never imported it -- adding it there would duplicate what this
    router already has. The WALK shape (workspace-scoped
    ``SESSIONS_ROOT.iterdir()`` with per-entry ownership + id-shape filtering),
    the ``limit`` param and its bounds mirror ``routers/agent_downloads.py``'s
    own unscoped listing -- but the DISCLOSURE deliberately does NOT: that
    sibling returns a bare truncated list with no total, while this route
    returns ``total`` (the count BEFORE truncation) alongside the capped
    ``sessions`` list. A picker silently missing an older approval-bearing
    session because of an uncommunicated cap is exactly the kind of silent
    gap this product's validation layer exists to refuse (CLAUDE.md: "a
    refused edit with a clear reason beats a completed edit with wrong
    numbers" applies equally to a listing that hides rows with no reason).
    Nothing here reimplements the id/ownership filtering: every candidate id
    is handed to :func:`read_ledger`, which already runs the full
    ``resolve_session_dir`` chain (id-shape allowlist, containment check,
    workspace ownership) that walk documents -- so a malformed or
    out-of-workspace directory name is silently skipped, never a 500. A
    ``read_ledger`` call that RAISES for one candidate (a locked file, a
    permissions error) is caught and logged rather than left to blank the
    whole response -- the sessions that ARE readable must still be served.

    Newest first, by the latest applied change's own timestamp within each
    session (``None`` where no change carries one -- a synthetic-fixture-only
    shape -- sorts last), truncated to ``limit`` AFTER sorting so the cap
    always keeps the most recent sessions.
    """
    sessions: list[dict[str, Any]] = []

    root = audit_trail.SESSIONS_ROOT
    candidate_ids: list[str] = []
    if root.is_dir():
        try:
            candidate_ids = [entry.name for entry in root.iterdir() if entry.is_dir()]
        except OSError:
            logger.warning("agent_audit: cannot list %s", root)
            candidate_ids = []

    for session_id in candidate_ids:
        try:
            ledger = read_ledger(session_id)
        except Exception:  # noqa: BLE001 -- one unreadable ledger must not blank the whole list
            logger.warning("agent_audit: read_ledger raised while listing %s", session_id, exc_info=True)
            continue
        if ledger is None or not ledger.changes:
            # No ledger file, out-of-workspace, or a real ledger that simply
            # recorded no parameter change -- ``approval_history_table`` would
            # render its own empty-state sentence for the last case, not real
            # data, so it does not belong in a picker of sessions worth
            # picking.
            continue
        timestamps = [entry.ts for entry in ledger.changes if entry.ts is not None]
        sessions.append(
            {
                # ALL of ledger.changes, regardless of outcome -- see the
                # docstring's opening paragraph. NOT ledger.applied_count,
                # which would silently under-report a session whose changes
                # were rolled back or failed even though those rows are still
                # in approval_history_table.
                "session_id": ledger.session_id,
                "approval_count": len(ledger.changes),
                "last_approval_at": format_ts(max(timestamps)) if timestamps else None,
            }
        )

    sessions.sort(key=lambda entry: entry["last_approval_at"] or "", reverse=True)
    total = len(sessions)
    return {"sessions": sessions[:limit], "total": total}


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

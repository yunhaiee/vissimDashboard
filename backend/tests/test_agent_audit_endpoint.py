"""Phase 1.3: GET /api/agent/audit/... (backend/routers/agent_audit.py) and the
one-line approval rationale that feeds it (``reason`` on
POST /api/assistant/agent/approve).

Like test_agent_downloads.py, every test builds its OWN
``runtime/agent_sessions``-shaped tmp dir and points the code at it — the real
``runtime/agent_sessions`` on a dev box holds hundreds of stale sessions from
past eval runs and nothing here may read or write any of them.

The approval half deliberately exercises the DECLINE leg end-to-end: it is the
one branch of ``/agent/approve`` that resolves without a planner, an edit
session, or Vissim COM, so it proves the request field really is plumbed
through to the ledger at zero COM cost. The approve leg's identical call site
is covered by calling ``_record_approval_decision`` directly.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import main
from routers import agent_audit
from routers import assistant as assistant_endpoint
from services import audit_trail, auth

SESSION_ID = "abc123def456"
_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
BASE_TS = datetime(2026, 8, 4, 9, 0, 0, tzinfo=timezone.utc).timestamp()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def sessions_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "agent_sessions"
    root.mkdir()
    monkeypatch.setattr(audit_trail, "SESSIONS_ROOT", root)
    monkeypatch.setattr(
        assistant_endpoint, "_agent_session_work_dir", lambda session_id: root / session_id
    )
    return root


@pytest.fixture()
def audit_spy(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records every ``session_id`` string that actually reached the route
    handler's id-resolution entrypoint, still raw and undecoded.

    ``routers/agent_audit.py`` imports ``read_ledger`` by name, so patching the
    name ON that module is the handler's own door: nothing gets in without
    being seen here, and a request that 404s on route-not-found leaves the list
    empty. That distinction is the whole point — a malformed-id test that never
    reaches resolution code asserts nothing about the id allowlist.
    """
    seen: list[str] = []
    real = audit_trail.read_ledger

    def spy(session_id: str, *args, **kwargs):
        seen.append(session_id)
        return real(session_id, *args, **kwargs)

    monkeypatch.setattr(agent_audit, "read_ledger", spy)
    return seen


def _records(session_id: str = SESSION_ID) -> list[dict]:
    """One approved-and-applied write plus one preflight refusal, in the exact
    record shape the orchestrator writes (see test_audit_trail_export.py)."""
    out: list[dict] = []
    ts = BASE_TS

    def rec(kind: str, payload: dict) -> None:
        nonlocal ts
        ts += 1.0
        out.append({"ts": ts, "session_id": session_id, "kind": kind, "payload": payload})

    params = {"input_no": "1", "volume": 600.0}
    rec("plan", {"iteration": 1, "kind": "tool", "tool": "set_vehicle_input_volume", "summary": "제안", "params": params, "meta": {}})
    rec("preflight", {"iteration": 1, "tool": "set_vehicle_input_volume", "blocking": False, "disabled": [], "findings": []})
    rec("user_confirm", {"stage": "requested", "iteration": 1, "tool": "set_vehicle_input_volume", "params": params, "warnings": []})
    rec("user_confirm", {"stage": "approved", "approval_id": "aid", "tool": "set_vehicle_input_volume", "params": params, "reason": "TIA 시나리오 B 수요"})
    rec("plan", {"iteration": 1, "kind": "tool", "tool": "set_vehicle_input_volume", "summary": "적용", "params": params, "meta": {}})
    rec("preflight", {"iteration": 1, "tool": "set_vehicle_input_volume", "blocking": False, "disabled": [], "findings": []})
    rec("action_start", {"iteration": 1, "tool": "set_vehicle_input_volume", "params": params})
    rec("action_end", {"iteration": 1, "tool": "set_vehicle_input_volume", "status": "ok", "error_type": None})
    rec("postwrite", {"iteration": 1, "tool": "set_vehicle_input_volume", "blocking": False, "disabled": [], "findings": ["write_verification_failed"]})
    rec("plan", {"iteration": 2, "kind": "tool", "tool": "set_signal_cycle", "summary": "주기 400", "params": {"sc_no": "3", "cycle": 400}, "meta": {}})
    rec("preflight", {"iteration": 2, "tool": "set_signal_cycle", "blocking": True, "disabled": [], "findings": ["signal_cycle_out_of_range"]})
    rec("refusal", {"iteration": 2, "tool": "set_signal_cycle", "tier": "write", "phase": "preflight", "error_type": "guardrail_refused", "message": "주기 400s는 허용 범위를 벗어납니다."})
    return out


def _make_session(root: Path, session_id: str = SESSION_ID, records: list[dict] | None = None) -> Path:
    session_dir = root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in (records if records is not None else _records(session_id))]
    (session_dir / f"trace_{session_id}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return session_dir


def _doc_text(content: bytes) -> str:
    from docx import Document

    doc = Document(BytesIO(content))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Download endpoint
# ---------------------------------------------------------------------------


def test_export_streams_a_docx_naming_the_session(client, sessions_root):
    _make_session(sessions_root)

    resp = client.get(f"/api/agent/audit/{SESSION_ID}/export.docx")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == _DOCX_MEDIA
    assert 'filename="audit_trail_abc123de.docx"' in resp.headers["content-disposition"]
    text = _doc_text(resp.content)
    assert "파라미터 변경 기록 및 근거" in text
    assert "set_vehicle_input_volume" in text
    assert "기록 없음 → 600.0 veh/h" in text
    assert "TIA 시나리오 B 수요" in text          # the approval rationale
    assert "주기 400s는 허용 범위를 벗어납니다." in text  # the guardrail refusal


def test_export_honours_the_language_query_and_falls_back_to_korean(client, sessions_root):
    _make_session(sessions_root)

    en = client.get(f"/api/agent/audit/{SESSION_ID}/export.docx", params={"language": "en"})
    assert en.status_code == 200
    assert "PARAMETER CHANGE RECORD AND RATIONALE" in _doc_text(en.content)

    unknown = client.get(f"/api/agent/audit/{SESSION_ID}/export.docx", params={"language": "de"})
    assert unknown.status_code == 200
    assert "파라미터 변경 기록 및 근거" in _doc_text(unknown.content)


def test_export_of_a_session_with_no_changes_still_produces_a_document(client, sessions_root):
    _make_session(sessions_root, records=[])

    resp = client.get(f"/api/agent/audit/{SESSION_ID}/export.docx")

    assert resp.status_code == 200
    assert "이 세션에는 적용된 파라미터 변경 기록이 없습니다." in _doc_text(resp.content)


@pytest.mark.parametrize(
    "session_id,reaches_handler_as",
    [
        ("ffffffffffff", "ffffffffffff"),  # well-formed id, no such session
        ("NOTHEXAT!ALL", "NOTHEXAT!ALL"),  # fails the hex allowlist
        ("abc", "abc"),                    # too short for the allowlist
        # Traversal coverage, MEASURED 2026-08-05 rather than assumed. The
        # classic '../../../etc' used to sit in this list and asserted NOTHING:
        # httpx collapses dot segments client-side, so the request leaves as
        # `GET /etc/export.docx` and never names this route at all. The
        # percent-encoded variants die the same way one step later --
        # ``httpx.URL.path`` already percent-decodes, and Starlette's TestClient
        # unquotes it a SECOND time (testclient.py) before routing, so
        # '%2e%2e%2f...', '..%2f..%2fetc' and even the double-encoded
        # '%252e%252e%252f...' all get their separators back and stop matching
        # this single-segment route. Every one of them 404s on route-not-found
        # with the handler never running.
        # A BACKSLASH traversal is the shape that survives both normalizations
        # (a backslash is not a URL separator, so the segment stays whole) and
        # arrives at resolve_session_dir as a real traversal string -- and on
        # the Windows dev box a backslash IS a path separator, so this is the
        # platform's live attack shape, not a curiosity. The literal and the
        # percent-encoded form land as the same raw string:
        ("..\\..\\..\\etc", "..\\..\\..\\etc"),
        ("%2e%2e%5c%2e%2e%5c%2e%2e%5cetc", "..\\..\\..\\etc"),
    ],
)
def test_export_404s_for_unknown_or_malformed_sessions(
    client, sessions_root, audit_spy, session_id, reaches_handler_as
):
    resp = client.get(f"/api/agent/audit/{session_id}/export.docx")

    assert resp.status_code == 404
    # Non-vacuity guard: every id here must be shown to have REACHED id
    # resolution, undecoded. Add a case the client or the router normalizes
    # away and this fails instead of quietly passing on route-not-found.
    assert audit_spy == [reaches_handler_as]
    assert resp.json()["detail"] == "This session has no action ledger."


def test_a_dot_segment_traversal_id_is_refused_when_it_does_reach_the_handler(sessions_root, audit_spy):
    """'../../../etc' cannot be delivered over HTTP by this client (see the
    measurement in the parametrize block above), so the shape is covered where
    it CAN be delivered: calling the route function directly, exactly as a
    non-normalizing client, a proxy that forwards raw paths, or a future
    router change would hand it over. The id allowlist refuses it before any
    path is built, so the answer is the same 404."""
    with pytest.raises(HTTPException) as excinfo:
        agent_audit.download_audit_trail("../../../etc", language="ko")

    assert excinfo.value.status_code == 404
    assert audit_spy == ["../../../etc"]


def test_export_404s_for_a_session_dir_without_a_ledger(client, sessions_root):
    (sessions_root / SESSION_ID).mkdir()

    resp = client.get(f"/api/agent/audit/{SESSION_ID}/export.docx")
    assert resp.status_code == 404


def test_summary_endpoint_counts_without_building_a_document(client, sessions_root):
    _make_session(sessions_root)

    resp = client.get(f"/api/agent/audit/{SESSION_ID}/summary")

    assert resp.status_code == 200
    assert resp.json() == {
        "session_id": SESSION_ID,
        "records": 12,
        "changes": 1,
        "applied": 1,
        "refusals": 1,
        "with_rationale": 1,
        "is_empty": False,
    }


def test_summary_404s_where_the_download_does(client, sessions_root):
    assert client.get(f"/api/agent/audit/{SESSION_ID}/summary").status_code == 404


def test_export_never_writes_into_the_session_directory(client, sessions_root):
    session_dir = _make_session(sessions_root)
    before = {p.name: p.read_bytes() for p in session_dir.iterdir()}

    assert client.get(f"/api/agent/audit/{SESSION_ID}/export.docx").status_code == 200

    after = {p.name: p.read_bytes() for p in session_dir.iterdir()}
    assert after == before


# ---------------------------------------------------------------------------
# The one-line reason captured at approval
# ---------------------------------------------------------------------------


def _write_pending(work_dir: Path, *, approval_id: str = "pending-1") -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "pending_approval.json").write_text(
        json.dumps(
            {
                "approval_id": approval_id,
                "created_at": 0.0,
                "message": "1번 입력 교통량을 600으로 바꿔줘",
                "language": "ko",
                "signatures": ["sig"],
                "actions": [
                    {
                        "tool": "set_vehicle_input_volume",
                        "params": {"input_no": "1", "volume": 600},
                        "summary": "교통량을 600으로 변경 제안",
                    }
                ],
                "warnings": [],
                "focus": None,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_decline_with_a_reason_is_recorded_in_the_ledger_and_exported(client, sessions_root, approver_headers):
    work_dir = sessions_root / SESSION_ID
    _write_pending(work_dir)

    resp = client.post(
        "/api/assistant/agent/approve",
        json={
            "session_id": SESSION_ID,
            "approval_id": "pending-1",
            "approved": False,
            "reason": "관측 교통량과 불일치하여 반려",
        },
        headers=approver_headers,
    )
    assert resp.status_code == 200

    records = audit_trail.read_records(work_dir / f"trace_{SESSION_ID}.jsonl")
    assert len(records) == 1
    assert records[0]["kind"] == "user_confirm"
    # U8 added ``actor``: the AUTHENTICATED reviewer who declined, exactly the
    # four fields ``audit_trail.normalize_actor`` accepts, and carrying the
    # real account's id rather than anything the request could have supplied.
    # The whole payload is pinned (not just the new key) so a field can never
    # quietly appear in the append-only ledger without a test saying so.
    reviewer = auth.get_user_by_username("suite-reviewer")
    assert records[0]["payload"] == {
        "stage": "declined",
        "approval_id": "pending-1",
        "tool": "set_vehicle_input_volume",
        "params": {"input_no": "1", "volume": 600},
        "summary": "교통량을 600으로 변경 제안",
        "reason": "관측 교통량과 불일치하여 반려",
        "actor": {
            "user_id": reviewer["id"],
            "username": "suite-reviewer",
            "display_name": "검토 담당자",
            "role": "reviewer",
        },
    }

    text = _doc_text(client.get(f"/api/agent/audit/{SESSION_ID}/export.docx").content)
    assert "사용자 거절" in text
    assert "관측 교통량과 불일치하여 반려" in text
    assert "검토 담당자 (suite-reviewer · 검토자)" in text


def test_reason_is_optional_and_a_blank_one_is_stored_as_none(client, sessions_root, approver_headers):
    work_dir = sessions_root / SESSION_ID
    _write_pending(work_dir)

    # No `reason` key at all -- every client that predates this field.
    resp = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": SESSION_ID, "approval_id": "pending-1", "approved": False},
        headers=approver_headers,
    )
    assert resp.status_code == 200

    records = audit_trail.read_records(work_dir / f"trace_{SESSION_ID}.jsonl")
    assert records[0]["payload"]["reason"] is None

    _write_pending(work_dir, approval_id="pending-2")
    client.post(
        "/api/assistant/agent/approve",
        json={"session_id": SESSION_ID, "approval_id": "pending-2", "approved": False, "reason": "   "},
        headers=approver_headers,
    )
    records = audit_trail.read_records(work_dir / f"trace_{SESSION_ID}.jsonl")
    assert records[-1]["payload"]["reason"] is None


def test_an_over_long_reason_is_rejected_at_the_request_boundary(client, sessions_root, approver_headers):
    work_dir = sessions_root / SESSION_ID
    _write_pending(work_dir)

    resp = client.post(
        "/api/assistant/agent/approve",
        json={
            "session_id": SESSION_ID,
            "approval_id": "pending-1",
            "approved": False,
            "reason": "가" * (assistant_endpoint.MAX_APPROVAL_REASON_CHARS + 1),
        },
        headers=approver_headers,
    )

    assert resp.status_code == 422
    # Rejected before anything was consumed or written.
    assert (work_dir / "pending_approval.json").exists()
    assert not (work_dir / f"trace_{SESSION_ID}.jsonl").exists()


def test_a_stale_approval_id_404s_without_touching_the_ledger(client, sessions_root, approver_headers):
    work_dir = sessions_root / SESSION_ID
    _write_pending(work_dir)

    resp = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": SESSION_ID, "approval_id": "wrong", "approved": False, "reason": "x"},
        headers=approver_headers,
    )

    assert resp.status_code == 404
    assert not (work_dir / f"trace_{SESSION_ID}.jsonl").exists()


def test_recorded_approval_reason_attaches_to_the_write_that_follows_it(sessions_root):
    """The approve leg's own call site: ``_record_approval_decision`` writes
    the ledger record, and the export attaches it to the next dispatch of that
    tool — exactly one, mirroring PreapprovedWriteGate's one-approval-one-
    dispatch rule."""
    work_dir = sessions_root / SESSION_ID
    work_dir.mkdir(parents=True)
    pending = {
        "approval_id": "aid",
        "actions": [
            {"tool": "set_vehicle_input_volume", "params": {"input_no": "1", "volume": 600}, "summary": "s"}
        ],
    }
    assistant_endpoint._record_approval_decision(
        work_dir, SESSION_ID, pending, approved=True, reason="주간 첨두 보정 근거"
    )

    # ... and then the approved re-run appends its own records.
    approved_record = audit_trail.read_records(work_dir / f"trace_{SESSION_ID}.jsonl")
    assert approved_record[0]["payload"]["stage"] == "approved"

    with (work_dir / f"trace_{SESSION_ID}.jsonl").open("a", encoding="utf-8") as handle:
        for record in _records()[4:9]:  # plan/preflight/action_start/action_end/postwrite
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    ledger = audit_trail.read_ledger(SESSION_ID)
    assert ledger is not None
    assert [(e.tool, e.outcome, e.reason) for e in ledger.changes] == [
        ("set_vehicle_input_volume", "applied", "주간 첨두 보정 근거")
    ]


def test_a_failed_ledger_append_never_blocks_the_approval(client, sessions_root, monkeypatch, approver_headers):
    """The ledger write is best-effort: an unwritable trace must not turn a
    user's decision into a 500."""
    work_dir = sessions_root / SESSION_ID
    _write_pending(work_dir)

    def boom(*args, **kwargs):
        raise OSError("disk on fire")

    monkeypatch.setattr(assistant_endpoint, "TraceWriter", boom)

    resp = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": SESSION_ID, "approval_id": "pending-1", "approved": False, "reason": "x"},
        headers=approver_headers,
    )

    assert resp.status_code == 200
    assert not (work_dir / "pending_approval.json").exists()

"""Unit U8 (roadmap 2.5): the authenticated approver on the approval gate.

The Phase-2 exit clause this file discharges, end to end and without a shortcut
anywhere in the middle: **"리뷰어가 로그인해 승인 이력을 자기 이름으로
남김"** — a reviewer logs in through the real ``POST /api/auth/login``, resolves
a real pending approval through ``POST /api/assistant/agent/approve``, and their
name comes back out of the audit ``.docx`` that
``GET /api/agent/audit/{id}/export.docx`` renders from the session's on-disk
ledger. Nothing is stubbed between the login and the document except Vissim COM
itself (``_build_edit_session``) and the planner (``_default_provider``), both
by the same seams ``test_agent_approval_endpoint.py`` already uses; the account,
the password hash, the session token, the ledger append and the DOCX render are
all the shipped code.

The two negative proofs matter as much as the positive ones: a modeler (403)
and an anonymous caller (401) must both be refused **without consuming the
pending approval**. A gate that eats the approval it refuses would turn every
mis-authenticated click into a silently lost proposal.

Fixtures are kept local rather than cross-imported from
``test_agent_approval_endpoint.py``, matching that file's own stated
convention.
"""
from __future__ import annotations

import json
import re
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from routers import assistant as assistant_endpoint
from routers import auth as auth_router
from services import audit_trail, auth

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"

#: Clears ``auth.MIN_PASSWORD_CHARS`` and exists only inside the session-scoped
#: tmp database ``tests/conftest.py`` points ``services.db.DB_PATH`` at.
PASSWORD = "u8-approval-gate-password"


class FakeStructuredProvider:
    """Canned (action, meta) queue — see test_agent_endpoint.py's identical
    fixture for the full rationale."""

    def __init__(self, queue: list[tuple[dict[str, Any] | None, dict[str, Any]]]) -> None:
        self._queue = list(queue)

    def generate_structured(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        schema: dict[str, Any] | None = None,
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if not self._queue:
            raise AssertionError("FakeStructuredProvider queue exhausted unexpectedly")
        return self._queue.pop(0)


def _action(tool_name: str, *, summary: str = "확인합니다", **params: Any):
    params["summary"] = summary
    return {"tool": tool_name, "params": params}, {"mode": "fake", "fallback_rung": 0}


class FakeEditSession:
    """VissimEditSession's surface for one write verb, with an XML-mutating
    ``persist()`` so ``guardrails.py``'s postwrite target-persisted check
    positively verifies instead of silently degrading — the same fixture shape,
    and the same reason, as test_agent_approval_endpoint.py's."""

    def __init__(self, working_inpx: Path) -> None:
        self.working_inpx = Path(working_inpx)
        self._pending: dict[int, float] = {}
        self.closed = False

    def set_vehicle_input_volume(self, input_no: int, volume: float) -> None:
        self._pending[input_no] = volume

    def get_vehicle_input_volume(self, input_no: int) -> float:
        return 600.0

    def persist(self) -> None:
        if not self._pending:
            return
        text = self.working_inpx.read_text(encoding="utf-8")
        for input_no, volume in self._pending.items():
            pattern = re.compile(rf'(<vehicleInput no="{input_no}"[^>]*?)/>')
            replacement = rf'\1><timeIntervalVehVolume volume="{volume}"/></vehicleInput>'
            text, count = pattern.subn(replacement, text, count=1)
            assert count == 1, f"fixture: could not locate vehicleInput no={input_no}"
        self.working_inpx.write_text(text, encoding="utf-8")
        self._pending.clear()

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _pin_active_dataset_to_demo(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(assistant_endpoint, "get_active_inpx_path", lambda: DEMO_INPX)


@pytest.fixture(autouse=True)
def _reset_active_edit_session():
    from services.agent.edit_session import clear_active_edit_session

    clear_active_edit_session()
    yield
    clear_active_edit_session()


@pytest.fixture()
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def _account(username: str, role: str, display_name: str) -> dict[str, Any]:
    """The account, created on first use. ``services.db.DB_PATH`` is redirected
    once per pytest SESSION, so a re-run inside the same session finds the user
    already there."""
    existing = auth.get_user_by_username(username)
    if existing is not None:
        assert existing["role"] == role
        return existing
    return auth.create_user(username, PASSWORD, role, display_name=display_name)


def _login(client: TestClient, username: str, role: str, display_name: str) -> str:
    """Log in through the REAL endpoint and return the bearer token.

    The cookie the login sets is left in the client's jar on purpose (that is
    what a browser would carry); the token is returned as well so a test can
    prove the header channel independently.
    """
    _account(username, role, display_name)
    response = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    assert response.json()["user"]["role"] == role
    return response.cookies[auth_router.SESSION_COOKIE_NAME]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _stream_events(response) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def _propose(client: TestClient, monkeypatch: pytest.MonkeyPatch, provider) -> tuple[str, str]:
    """One propose turn -> ``(session_id, approval_id)`` with the pending file
    really on disk. Unauthenticated, deliberately: U8 gates the DECISION, not
    the proposal."""
    monkeypatch.setattr(assistant_endpoint, "_default_provider", lambda: provider)
    response = client.post(
        "/api/assistant/agent/query/stream",
        json={"message": "1번 입력 교통량을 600으로 바꿔줘", "response_language": "ko"},
    )
    assert response.status_code == 200
    done = _stream_events(response)[-1]
    assert done["response"]["kind"] == "approval_request"
    session_id = done["response"]["session_id"]
    approval_id = done["response"]["approval"]["approval_id"]
    assert (assistant_endpoint._agent_session_work_dir(session_id) / "pending_approval.json").is_file()
    return session_id, approval_id


def _write_proposal() -> FakeStructuredProvider:
    """Planner queue for propose(1) + the approved re-run(2) + its answer(3)."""
    return FakeStructuredProvider(
        [
            _action("set_vehicle_input_volume", summary="교통량 600 제안", input_no="1", volume=600),
            _action("set_vehicle_input_volume", summary="승인된 변경을 적용", input_no="1", volume=600),
            _action("respond", summary="완료", answer="1번 입력 교통량을 600으로 변경했습니다."),
        ]
    )


def _ledger_records(session_id: str) -> list[dict[str, Any]]:
    work_dir = assistant_endpoint._agent_session_work_dir(session_id)
    return audit_trail.read_records(work_dir / f"trace_{session_id}.jsonl")


def _decision_records(session_id: str) -> list[dict[str, Any]]:
    """Only the approve/decline records. The propose turn legitimately leaves
    ``plan``/``preflight``/``user_confirm(stage="requested")`` behind, so "no
    decision was recorded" has to be asserted against these, not against an
    empty file."""
    return [
        r for r in _ledger_records(session_id)
        if r.get("kind") == "user_confirm"
        and (r.get("payload") or {}).get("stage") in ("approved", "declined")
    ]


def _confirm_record(session_id: str, stage: str) -> dict[str, Any]:
    records = [
        r for r in _ledger_records(session_id)
        if r.get("kind") == "user_confirm" and (r.get("payload") or {}).get("stage") == stage
    ]
    assert len(records) == 1, f"expected exactly one {stage!r} record, got {len(records)}"
    return records[0]["payload"]


def _docx_text(content: bytes) -> str:
    from docx import Document

    doc = Document(BytesIO(content))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def _export_text(client: TestClient, session_id: str) -> str:
    response = client.get(f"/api/agent/audit/{session_id}/export.docx")
    assert response.status_code == 200
    return _docx_text(response.content)


# ---------------------------------------------------------------------------
# (1) APPROVE, end to end: login -> approve -> ledger -> DOCX names the human
# ---------------------------------------------------------------------------


def test_a_logged_in_reviewers_name_reaches_the_audit_document(client, monkeypatch):
    session_id, approval_id = _propose(client, monkeypatch, _write_proposal())
    work_dir = assistant_endpoint._agent_session_work_dir(session_id)
    monkeypatch.setattr(
        assistant_endpoint,
        "_build_edit_session",
        lambda working_path: FakeEditSession(work_dir / "working.inpx"),
    )

    token = _login(client, "u8-reviewer", "reviewer", "이윤하")
    response = client.post(
        "/api/assistant/agent/approve",
        json={
            "session_id": session_id,
            "approval_id": approval_id,
            "approved": True,
            "reason": "TIA 시나리오 B 수요",
        },
        headers=_bearer(token),
    )

    assert response.status_code == 200
    assert _stream_events(response)[-1]["response"]["kind"] == "answer"

    # The ledger records WHO, not just what.
    reviewer = auth.get_user_by_username("u8-reviewer")
    assert _confirm_record(session_id, "approved")["actor"] == {
        "user_id": reviewer["id"],
        "username": "u8-reviewer",
        "display_name": "이윤하",
        "role": "reviewer",
    }

    # ... and the rendered document says it in the 승인자 column, next to the
    # change that approval authorized.
    text = _export_text(client, session_id)
    assert "이윤하 (u8-reviewer · 검토자)" in text
    assert "TIA 시나리오 B 수요" in text
    assert "기록 없음(인증 이전)" not in text


# ---------------------------------------------------------------------------
# (2) DECLINE, end to end: the same, for the decision that changes nothing
# ---------------------------------------------------------------------------


def test_a_logged_in_reviewers_name_reaches_the_declined_row(client, monkeypatch):
    provider = FakeStructuredProvider(
        [_action("set_vehicle_input_volume", summary="교통량 600 제안", input_no="1", volume=600)]
    )
    session_id, approval_id = _propose(client, monkeypatch, provider)

    token = _login(client, "u8-reviewer", "reviewer", "이윤하")
    response = client.post(
        "/api/assistant/agent/approve",
        json={
            "session_id": session_id,
            "approval_id": approval_id,
            "approved": False,
            "reason": "관측 교통량과 불일치하여 반려",
        },
        headers=_bearer(token),
    )

    assert response.status_code == 200
    assert _confirm_record(session_id, "declined")["actor"]["username"] == "u8-reviewer"

    text = _export_text(client, session_id)
    assert "사용자 거절" in text
    assert "이윤하 (u8-reviewer · 검토자)" in text
    assert "관측 교통량과 불일치하여 반려" in text


def test_an_admin_may_also_resolve_an_approval_and_is_recorded_as_an_admin(client, monkeypatch):
    """``require_role("reviewer", "admin")`` NAMES admin — it is not an
    implicit superuser (routers/auth.py) — and the document says which of the
    two roles signed off."""
    provider = FakeStructuredProvider(
        [_action("set_vehicle_input_volume", summary="교통량 600 제안", input_no="1", volume=600)]
    )
    session_id, approval_id = _propose(client, monkeypatch, provider)

    token = _login(client, "u8-admin", "admin", "박관리")
    response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": approval_id, "approved": False},
        headers=_bearer(token),
    )

    assert response.status_code == 200
    assert _confirm_record(session_id, "declined")["actor"]["role"] == "admin"
    assert "박관리 (u8-admin · 관리자)" in _export_text(client, session_id)


# ---------------------------------------------------------------------------
# (3) A modeler is refused — and the proposal survives the refusal
# ---------------------------------------------------------------------------


def test_a_modeler_gets_403_and_the_pending_approval_is_not_consumed(client, monkeypatch):
    session_id, approval_id = _propose(client, monkeypatch, _write_proposal())
    pending_path = assistant_endpoint._agent_session_work_dir(session_id) / "pending_approval.json"
    pending_before = pending_path.read_bytes()

    token = _login(client, "u8-modeler", "modeler", "김모델")
    response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": approval_id, "approved": True},
        headers=_bearer(token),
    )

    assert response.status_code == 403
    # The detail NAMES the roles: the modeler who hits this needs to know who
    # to ask, not merely that something was forbidden.
    detail = response.json()["detail"]
    assert "reviewer, admin" in detail and detail.startswith("승인·반려는")

    # Nothing was consumed and nothing was recorded: a refused decision is not
    # a decision.
    assert pending_path.read_bytes() == pending_before
    assert _decision_records(session_id) == []

    # And the same reviewer flow still works on that untouched approval.
    monkeypatch.setattr(
        assistant_endpoint,
        "_build_edit_session",
        lambda working_path: FakeEditSession(
            assistant_endpoint._agent_session_work_dir(session_id) / "working.inpx"
        ),
    )
    reviewer_token = _login(client, "u8-reviewer", "reviewer", "이윤하")
    retry = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": approval_id, "approved": True},
        headers=_bearer(reviewer_token),
    )
    assert retry.status_code == 200
    assert not pending_path.exists()


def test_the_403_is_localized_like_every_other_user_facing_string(client, monkeypatch):
    session_id, approval_id = _propose(client, monkeypatch, _write_proposal())
    token = _login(client, "u8-modeler", "modeler", "김모델")
    body = {"session_id": session_id, "approval_id": approval_id, "approved": True}

    korean = client.post("/api/assistant/agent/approve", json=body, headers=_bearer(token))
    english = client.post(
        "/api/assistant/agent/approve?language=en", json=body, headers=_bearer(token)
    )
    japanese = client.post(
        "/api/assistant/agent/approve", json=body, headers={**_bearer(token), "Accept-Language": "ja"}
    )
    unknown = client.post(
        "/api/assistant/agent/approve?language=de", json=body, headers=_bearer(token)
    )

    assert korean.json()["detail"] == "승인·반려는 reviewer, admin 권한을 가진 사용자만 할 수 있습니다."
    assert english.json()["detail"] == (
        "Only a user with the reviewer, admin role may approve or decline this action."
    )
    assert "承認・却下" in japanese.json()["detail"]
    # An unknown code falls back to Korean, like every other string.
    assert unknown.json()["detail"] == korean.json()["detail"]


# ---------------------------------------------------------------------------
# (4) An anonymous caller is refused — and the proposal survives that too
# ---------------------------------------------------------------------------


def test_an_unauthenticated_caller_gets_401_and_leaves_the_pending_approval_intact(
    client, monkeypatch
):
    """This is also the frontend's behaviour until U9 ships the login screen:
    the approval card gets 401. Sequenced deliberately — an approval history
    that cannot name its approver is what this unit exists to end."""
    session_id, approval_id = _propose(client, monkeypatch, _write_proposal())
    pending_path = assistant_endpoint._agent_session_work_dir(session_id) / "pending_approval.json"
    pending_before = pending_path.read_bytes()

    for body in (
        {"session_id": session_id, "approval_id": approval_id, "approved": True},
        {"session_id": session_id, "approval_id": approval_id, "approved": False},
    ):
        response = client.post("/api/assistant/agent/approve", json=body)
        assert response.status_code == 401
        assert response.json()["detail"] == "로그인이 필요합니다."

    assert pending_path.read_bytes() == pending_before
    assert _decision_records(session_id) == []


def test_an_expired_or_revoked_session_is_as_anonymous_as_no_session_at_all(client, monkeypatch):
    session_id, approval_id = _propose(client, monkeypatch, _write_proposal())
    pending_path = assistant_endpoint._agent_session_work_dir(session_id) / "pending_approval.json"

    token = _login(client, "u8-reviewer", "reviewer", "이윤하")
    assert client.post("/api/auth/logout", headers=_bearer(token)).status_code == 200

    response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": approval_id, "approved": False},
        headers=_bearer(token),
    )

    assert response.status_code == 401
    assert pending_path.is_file()


# ---------------------------------------------------------------------------
# Provenance: the recorded name comes from the SESSION, never from the request
# ---------------------------------------------------------------------------


def test_a_client_supplied_actor_field_cannot_forge_the_approver(client, monkeypatch):
    """The one attack this column has to survive: a caller who simply tells the
    server whose name to print."""
    provider = FakeStructuredProvider(
        [_action("set_vehicle_input_volume", summary="교통량 600 제안", input_no="1", volume=600)]
    )
    session_id, approval_id = _propose(client, monkeypatch, provider)

    token = _login(client, "u8-reviewer", "reviewer", "이윤하")
    response = client.post(
        "/api/assistant/agent/approve",
        json={
            "session_id": session_id,
            "approval_id": approval_id,
            "approved": False,
            "actor": {
                "user_id": 999,
                "username": "not-a-real-user",
                "display_name": "사장님",
                "role": "admin",
            },
            "user": "not-a-real-user",
        },
        headers=_bearer(token),
    )

    assert response.status_code == 200
    assert _confirm_record(session_id, "declined")["actor"]["username"] == "u8-reviewer"
    text = _export_text(client, session_id)
    assert "이윤하 (u8-reviewer · 검토자)" in text
    assert "사장님" not in text and "not-a-real-user" not in text


def test_the_cookie_channel_authenticates_the_gate_too(client, monkeypatch):
    """U7 ships two channels for ONE session token (cookie for the browser,
    bearer for scripts and tests). The browser's is the one U9's login screen
    will use, so it is proven here rather than assumed."""
    provider = FakeStructuredProvider(
        [_action("set_vehicle_input_volume", summary="교통량 600 제안", input_no="1", volume=600)]
    )
    session_id, approval_id = _propose(client, monkeypatch, provider)

    _login(client, "u8-reviewer", "reviewer", "이윤하")  # leaves the cookie in the jar
    response = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": approval_id, "approved": False},
    )

    assert response.status_code == 200
    assert _confirm_record(session_id, "declined")["actor"]["username"] == "u8-reviewer"

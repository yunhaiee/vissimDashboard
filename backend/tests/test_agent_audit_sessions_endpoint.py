"""Track R unit R-5c: ``GET /api/agent/audit/sessions``.

The /report page's approval-history session picker names this route as its
ONLY honest source (docs/phase2/DECISIONS.md, 2026-08-24 "HWP는 v1 제외 확정 ·
승인 이력 세션 픽커는 위임"): a session belongs on the list only when its own
action ledger, read through ``services.audit_trail.read_ledger`` -- the same
reader ``GET /{session_id}/summary`` and the export route already use -- holds
at least one applied/rolled-back/failed parameter change. Nothing here may be
invented: a session that merely SAVED A WRITE (``GET /api/agent/downloads``'s
set) or merely exists as a bare directory must not appear.

Like ``tests/test_agent_audit_endpoint.py``, every test builds its OWN
``runtime/agent_sessions``-shaped tmp dir (via the ``sessions_root`` fixture)
rather than sharing the real one -- see that file's own docstring for why.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from services import audit_trail

BASE_TS = datetime(2026, 8, 20, 9, 0, 0, tzinfo=timezone.utc).timestamp()


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def sessions_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "agent_sessions"
    root.mkdir()
    monkeypatch.setattr(audit_trail, "SESSIONS_ROOT", root)
    return root


def _write_ledger(root: Path, session_id: str, records: list[dict]) -> None:
    session_dir = root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    (session_dir / f"trace_{session_id}.jsonl").write_text(
        ("\n".join(lines) + "\n") if lines else "", encoding="utf-8"
    )


def _applied_change_records(tool: str = "set_vehicle_input_volume", ts_start: float = BASE_TS) -> list[dict]:
    """One gated, dispatched, postwrite-ok change -- the minimal record shape
    ``services.audit_trail.build_ledger`` folds into one real ``ChangeEntry``
    (mirrors ``tests/test_agent_audit_endpoint.py``'s own fixture records)."""
    ts = ts_start
    out: list[dict] = []

    def rec(kind: str, payload: dict) -> None:
        nonlocal ts
        ts += 1.0
        out.append({"ts": ts, "kind": kind, "payload": payload})

    params = {"input_no": "1", "volume": 600.0}
    rec("plan", {"iteration": 1, "tool": tool, "summary": "제안", "params": params, "meta": {}})
    rec("preflight", {"iteration": 1, "tool": tool, "blocking": False, "disabled": [], "findings": []})
    rec("action_start", {"iteration": 1, "tool": tool, "params": params})
    rec("action_end", {"iteration": 1, "tool": tool, "status": "ok", "error_type": None})
    rec("postwrite", {"iteration": 1, "tool": tool, "blocking": False, "disabled": [], "findings": []})
    return out


def _rolled_back_change_records(tool: str = "set_signal_cycle", ts_start: float = BASE_TS) -> list[dict]:
    """A write that DISPATCHED ok but failed postwrite verification and was
    rolled back -- ``outcome == "rolled_back"``. Still exactly ONE row in
    ``ledger.changes`` (and in ``approval_history_table``'s Table 1), which is
    the fact Fix 1's cross-check test proves ``approval_count`` must honour."""
    ts = ts_start
    out: list[dict] = []

    def rec(kind: str, payload: dict) -> None:
        nonlocal ts
        ts += 1.0
        out.append({"ts": ts, "kind": kind, "payload": payload})

    params = {"sc_no": "3", "cycle": 120}
    rec("plan", {"iteration": 1, "tool": tool, "summary": "제안", "params": params, "meta": {}})
    rec("preflight", {"iteration": 1, "tool": tool, "blocking": False, "disabled": [], "findings": []})
    rec("action_start", {"iteration": 1, "tool": tool, "params": params})
    rec("action_end", {"iteration": 1, "tool": tool, "status": "ok", "error_type": None})
    rec("postwrite", {"iteration": 1, "tool": tool, "blocking": True, "disabled": [], "findings": ["write_verification_failed"]})
    rec("refusal", {"iteration": 1, "tool": tool, "phase": "postwrite", "error_type": "write_verification_failed", "message": "쓰기 검증에 실패해 롤백되었습니다."})
    return out


def _failed_change_records(tool: str = "set_vehicle_input_volume", ts_start: float = BASE_TS) -> list[dict]:
    """A write whose DISPATCH itself errored -- ``outcome == "failed"``. Still
    exactly ONE row in ``ledger.changes``, same reason as above."""
    ts = ts_start
    out: list[dict] = []

    def rec(kind: str, payload: dict) -> None:
        nonlocal ts
        ts += 1.0
        out.append({"ts": ts, "kind": kind, "payload": payload})

    params = {"input_no": "2", "volume": 900.0}
    rec("plan", {"iteration": 1, "tool": tool, "summary": "제안", "params": params, "meta": {}})
    rec("preflight", {"iteration": 1, "tool": tool, "blocking": False, "disabled": [], "findings": []})
    rec("action_start", {"iteration": 1, "tool": tool, "params": params})
    rec("action_end", {"iteration": 1, "tool": tool, "status": "error", "error_type": "dispatch_error"})
    return out


def _refusal_only_records() -> list[dict]:
    """A session that was only ever REFUSED -- zero applied changes. The
    ledger is real and non-empty (it holds a refusal), but ``ledger.changes``
    is empty, so it must be EXCLUDED: ``approval_history_table`` would render
    its own empty-state sentence for it, not real approval data."""
    return [
        {
            "ts": BASE_TS + 1,
            "kind": "plan",
            "payload": {"iteration": 1, "tool": "set_signal_cycle", "summary": "주기 400", "params": {"sc_no": "3", "cycle": 400}, "meta": {}},
        },
        {
            "ts": BASE_TS + 2,
            "kind": "preflight",
            "payload": {"iteration": 1, "tool": "set_signal_cycle", "blocking": True, "disabled": [], "findings": ["signal_cycle_out_of_range"]},
        },
        {
            "ts": BASE_TS + 3,
            "kind": "refusal",
            "payload": {"iteration": 1, "tool": "set_signal_cycle", "tier": "write", "phase": "preflight", "error_type": "guardrail_refused", "message": "허용 범위를 벗어납니다."},
        },
    ]


# ---------------------------------------------------------------------------
# The measured gate: ledger.changes >= 1, nothing invented
# ---------------------------------------------------------------------------


def test_lists_only_the_session_whose_ledger_holds_an_applied_change(client, sessions_root):
    _write_ledger(sessions_root, "aaaaaaaa1111", _applied_change_records())
    _write_ledger(sessions_root, "bbbbbbbb2222", _refusal_only_records())  # refusal-only: excluded
    (sessions_root / "cccccccc3333").mkdir()  # a bare dir, no ledger file at all: excluded

    resp = client.get("/api/agent/audit/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert [s["session_id"] for s in body["sessions"]] == ["aaaaaaaa1111"]
    assert body["sessions"][0]["approval_count"] == 1
    assert body["sessions"][0]["last_approval_at"] is not None


def test_a_bare_session_directory_with_no_ledger_file_never_appears(client, sessions_root):
    (sessions_root / "deadbeefdead").mkdir()

    resp = client.get("/api/agent/audit/sessions")

    assert resp.status_code == 200
    assert resp.json() == {"sessions": [], "total": 0}


def test_an_empty_ledger_file_is_excluded_too(client, sessions_root):
    _write_ledger(sessions_root, "0000ffff1111", [])

    resp = client.get("/api/agent/audit/sessions")

    assert resp.json() == {"sessions": [], "total": 0}


def test_no_sessions_root_at_all_returns_an_empty_list_not_an_error(client, tmp_path, monkeypatch):
    monkeypatch.setattr(audit_trail, "SESSIONS_ROOT", tmp_path / "does-not-exist")

    resp = client.get("/api/agent/audit/sessions")

    assert resp.status_code == 200
    assert resp.json() == {"sessions": [], "total": 0}


# ---------------------------------------------------------------------------
# Fix 1 (refuter): approval_count must count ALL of ledger.changes, not just
# the "applied" outcome -- cross-checked against approval_history_table
# itself, the figure this picker exists to feed.
# ---------------------------------------------------------------------------


def test_approval_count_matches_approval_history_table_rows_for_non_applied_outcomes(client, sessions_root):
    """MEASURED (refuter): mutating ``len(ledger.changes)`` to
    ``ledger.applied_count`` survives a suite whose fixtures were all
    applied-only, silently under-counting a session whose changes were rolled
    back or failed even though ``approval_history_table`` still renders those
    rows. Cross-checked directly against
    ``services.report.figures.approval_history_table`` -- not re-derived a
    second time through ``read_ledger`` -- so a drift between the count and
    the figure it describes cannot hide behind two paths agreeing with
    themselves."""
    from services.report import figures as report_figures

    _write_ledger(sessions_root, "aaaa5555bbbb", _rolled_back_change_records())
    _write_ledger(sessions_root, "cccc6666dddd", _failed_change_records())

    resp = client.get("/api/agent/audit/sessions")
    assert resp.status_code == 200
    by_id = {s["session_id"]: s for s in resp.json()["sessions"]}
    assert set(by_id) == {"aaaa5555bbbb", "cccc6666dddd"}

    for session_id in by_id:
        figure = report_figures.approval_history_table(session_id)
        assert figure["absent"] is False, f"{session_id}: expected approval_history_table to have real rows"
        assert by_id[session_id]["approval_count"] == len(figure["rows"]) == 1

    # And directly: the ledger's own outcome vocabulary for each session is
    # NOT "applied" -- the exact case an applied_count substitution would get
    # wrong.
    rolled_back_ledger = audit_trail.read_ledger("aaaa5555bbbb")
    assert [c.outcome for c in rolled_back_ledger.changes] == ["rolled_back"]
    assert rolled_back_ledger.applied_count == 0

    failed_ledger = audit_trail.read_ledger("cccc6666dddd")
    assert [c.outcome for c in failed_ledger.changes] == ["failed"]
    assert failed_ledger.applied_count == 0


# ---------------------------------------------------------------------------
# Fix 2 (refuter): the per-candidate try/except around read_ledger is live
# code -- one unreadable session must not blank the others.
# ---------------------------------------------------------------------------


def test_a_read_ledger_failure_for_one_session_does_not_blank_the_others(client, sessions_root, monkeypatch):
    """MEASURED (refuter): deleting the try/except around ``read_ledger``
    passes the whole suite as delivered, because nothing forced a real raise.
    This patches ``routers.agent_audit.read_ledger`` -- the NAME this router
    imported directly (``from services.audit_trail import ... read_ledger``)
    -- not ``services.audit_trail.read_ledger``: patching the service module
    would not reach this router's call site, which is the refuter's own
    measured trap."""
    from routers import agent_audit

    _write_ledger(sessions_root, "aaaa00001111", _applied_change_records())
    _write_ledger(sessions_root, "bbbb00002222", _applied_change_records(ts_start=BASE_TS + 500))

    real_read_ledger = agent_audit.read_ledger

    def flaky(session_id, *args, **kwargs):
        if session_id == "bbbb00002222":
            raise PermissionError("locked by another process")
        return real_read_ledger(session_id, *args, **kwargs)

    monkeypatch.setattr(agent_audit, "read_ledger", flaky)

    resp = client.get("/api/agent/audit/sessions")

    assert resp.status_code == 200
    assert [s["session_id"] for s in resp.json()["sessions"]] == ["aaaa00001111"]


# ---------------------------------------------------------------------------
# Fix 3 (refuter): the walk must cap at `limit` (like agent_downloads.py's
# own unscoped listing), keep the most recent, and DISCLOSE the true total
# rather than truncating silently.
# ---------------------------------------------------------------------------


def test_default_limit_caps_at_twenty_newest_first_and_discloses_the_true_total(client, sessions_root):
    """MEASURED (refuter): an unbounded walk cost 2.92s over 500 synthetic
    session directories on this dev box. The default cap is 20, applied AFTER
    sorting so the kept rows are always the most recent, and `total` (the
    pre-truncation count) must equal 25 here -- a bare truncated list with no
    total would be the silent-truncation shape the sibling listing route
    (``GET /api/agent/downloads``, no ``session_id``) is guilty of and this
    route explicitly does not repeat (see the route's own docstring)."""
    for i in range(25):
        session_id = f"{i:012x}"
        _write_ledger(sessions_root, session_id, _applied_change_records(ts_start=BASE_TS + i))

    resp = client.get("/api/agent/audit/sessions")

    assert resp.status_code == 200
    body = resp.json()
    assert len(body["sessions"]) == 20
    assert body["total"] == 25
    # ts_start = BASE_TS + i for i in 0..24 -- newest-first keeps i=24..5.
    kept_ids = {s["session_id"] for s in body["sessions"]}
    assert kept_ids == {f"{i:012x}" for i in range(5, 25)}


def test_limit_query_param_is_honoured_and_bounded(client, sessions_root):
    _write_ledger(sessions_root, "aaaa00001111", _applied_change_records())
    _write_ledger(sessions_root, "bbbb00002222", _applied_change_records(ts_start=BASE_TS + 10))

    capped = client.get("/api/agent/audit/sessions", params={"limit": 1})
    assert capped.status_code == 200
    body = capped.json()
    assert len(body["sessions"]) == 1
    assert body["total"] == 2
    assert body["sessions"][0]["session_id"] == "bbbb00002222"  # the newer one

    too_big = client.get("/api/agent/audit/sessions", params={"limit": 500})
    assert too_big.status_code == 422

    too_small = client.get("/api/agent/audit/sessions", params={"limit": 0})
    assert too_small.status_code == 422


def test_the_approval_count_and_timestamp_are_measured_from_the_real_rows(client, sessions_root):
    """Two applied changes in one session -- the exact rows
    ``approval_history_table`` would render for it -- must be counted, not
    guessed, and the reported timestamp must be the LATEST one, formatted the
    same way ``services.audit_trail.format_ts`` formats every other 시각 column
    in this product."""
    records = _applied_change_records(tool="set_vehicle_input_volume", ts_start=BASE_TS)
    records += _applied_change_records(tool="set_signal_cycle", ts_start=BASE_TS + 100)
    _write_ledger(sessions_root, "1234abcd5678", records)

    resp = client.get("/api/agent/audit/sessions")

    entry = resp.json()["sessions"][0]
    assert entry["approval_count"] == 2
    ledger = audit_trail.read_ledger("1234abcd5678")
    latest_ts = max(e.ts for e in ledger.changes)
    assert entry["last_approval_at"] == audit_trail.format_ts(latest_ts)


def test_newest_session_first_by_its_latest_applied_change(client, sessions_root):
    _write_ledger(sessions_root, "1111aaaa2222", _applied_change_records(ts_start=BASE_TS))
    _write_ledger(sessions_root, "3333bbbb4444", _applied_change_records(ts_start=BASE_TS + 1000))

    resp = client.get("/api/agent/audit/sessions")

    assert [s["session_id"] for s in resp.json()["sessions"]] == ["3333bbbb4444", "1111aaaa2222"]


# ---------------------------------------------------------------------------
# Hostile directory names never 500
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "session_id",
    [
        "NOTHEXAT!ALL",  # fails the hex allowlist
        "abc",  # too short for the allowlist
        "ABCDEF123456",  # uppercase: the allowlist is lowercase-hex only
    ],
)
def test_a_directory_name_the_id_allowlist_rejects_is_silently_skipped(client, sessions_root, session_id):
    """A directory the filesystem happens to hold but ``_SESSION_ID_RE``
    rejects (this codebase never creates one, but the filesystem enforces no
    such shape) must be silently absent from the list, never a 500 -- exactly
    the allowlist ``read_ledger``'s own ``resolve_session_dir`` already
    applies to every other session-scoped route."""
    _write_ledger(sessions_root, session_id, _applied_change_records())

    resp = client.get("/api/agent/audit/sessions")

    assert resp.status_code == 200
    assert session_id not in [s["session_id"] for s in resp.json()["sessions"]]


def test_a_torn_ledger_line_does_not_blank_the_whole_list(client, sessions_root):
    """``read_ledger``/``read_records`` already skip unparseable JSONL lines
    per-line (see ``services/audit_trail.py``'s own docstring); this asserts
    that behaviour holds through the listing route too, and that a session
    with only garbage lines contributes zero rows rather than crashing the
    others out of the response."""
    good = sessions_root / "aaaa00001111"
    good.mkdir()
    (good / "trace_aaaa00001111.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in _applied_change_records()) + "\n",
        encoding="utf-8",
    )

    torn = sessions_root / "bbbb00002222"
    torn.mkdir()
    (torn / "trace_bbbb00002222.jsonl").write_text("{ not json at all\n", encoding="utf-8")

    resp = client.get("/api/agent/audit/sessions")

    assert resp.status_code == 200
    assert [s["session_id"] for s in resp.json()["sessions"]] == ["aaaa00001111"]


# ---------------------------------------------------------------------------
# Workspace isolation -- the same boundary every other session-scoped surface
# in this codebase enforces (services/agent/sessions.py, tests/
# test_workspace_sessions.py)
# ---------------------------------------------------------------------------


def test_workspace_isolation_hides_another_projects_sessions(client, sessions_root):
    from conftest import admin_headers

    from services.agent import sessions as agent_sessions

    def _new_workspace(name: str) -> str:
        resp = client.post("/api/workspaces", json={"name": name}, headers=admin_headers())
        assert resp.status_code == 200, resp.text
        return resp.json()["id"]

    workspace_b = _new_workspace("R-5c-workspace-B")
    workspace_c = _new_workspace("R-5c-workspace-C")

    for session_id, owner in (("aaaa11112222", workspace_b), ("bbbb33334444", workspace_c)):
        _write_ledger(sessions_root, session_id, _applied_change_records())
        agent_sessions.write_session_meta(
            sessions_root / session_id, {"session_id": session_id, "workspace_id": owner}
        )

    resp_b = client.get(
        "/api/agent/audit/sessions", headers={**admin_headers(), "X-Workspace-Id": workspace_b}
    )
    assert [s["session_id"] for s in resp_b.json()["sessions"]] == ["aaaa11112222"]

    resp_c = client.get(
        "/api/agent/audit/sessions", headers={**admin_headers(), "X-Workspace-Id": workspace_c}
    )
    assert [s["session_id"] for s in resp_c.json()["sessions"]] == ["bbbb33334444"]

"""Unit 1.4 follow-up — the agent-session half of the workspace boundary.

Three measured defects are pinned here, all found by adversarial review of the
unit-1.4 build rather than by a failing test:

1. **Session ownership failed OPEN.** ``services.agent.sessions.
   session_workspace_matches`` substituted the DEFAULT workspace whenever the
   ``session_meta.json`` sidecar did not answer — and it does not answer for a
   missing OR a truncated/garbage sidecar. So a session created by project B
   whose sidecar never landed became the default project's: a caller sending no
   ``X-Workspace-Id`` header got B's working copy parsed and served by
   ``GET /api/network?session_id=``, B's edited .inpx from the download
   endpoints, B's action ledger from the audit export, and could resolve B's
   pending write approval — while B itself was locked out. ``view: "working"``
   vs ``view: "active"`` also made it a positive existence oracle no ghost
   session id could produce.

2. **The ``/agent/query/stream`` foreign-session branch was untested.** It
   404s a client-supplied ``session_id`` belonging to another project, and
   nothing in ``backend/tests`` asserted it (``_session_belongs_to_workspace``
   had zero hits under ``tests/``).

3. **The interactive-run TOCTOU fix was untested at the point of CONSUMPTION.**
   ``tests/test_workspaces.py`` monkeypatches ``_run_simulation`` away and
   asserts only the enqueue-side arguments, so a mutant that keeps the
   signature and merely re-resolves ``_simulation_source_inpx()`` at the top of
   ``_run_simulation_locked`` — the identical bug moved one line deeper —
   passes the whole suite. The test here lets the REAL
   ``_run_simulation_locked`` run with only the heavy externals (COM, the
   network builder) stubbed, and asserts on the bytes that actually reached the
   builder.

Everything is synthetic; nothing needs Vissim, COM, or Windows.
"""
from __future__ import annotations

import json
import types
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from fastapi.testclient import TestClient

import main
from routers import agent_downloads as agent_downloads_router
from routers import assistant as assistant_endpoint
from routers import network as network_router
from routers import simulations as simulations_router
from services import audit_trail, workspaces
from services.agent import sessions as agent_sessions

INPX_A = b'<network fromWorkspace="A"><links/></network>'
INPX_B = b'<network fromWorkspace="B"><links/><nodes/></network>'

#: A well-formed session id that names no directory anywhere. Every
#: "another project's session" response must be byte-identical to this one's,
#: or the endpoint is an existence oracle.
GHOST_SESSION_ID = "999888777666"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private workspaces root, with the legacy-migration source pointed at
    an empty dir so migration is a guaranteed no-op.

    BOTH constants, always: patching only ``WORKSPACES_ROOT`` would point the
    one-time migration at the developer's real ``runtime/uploads`` and MOVE
    their live dataset into a pytest tmp dir.
    """
    root = tmp_path / "workspaces"
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", root)
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "legacy-uploads")
    default_id = workspaces.default_workspace_id()
    token = workspaces.set_current_workspace_id(default_id)
    try:
        yield root
    finally:
        workspaces.reset_current_workspace_id(token)


@pytest.fixture()
def sessions_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private agent-sessions root on all FIVE seams.

    ``services.agent.sessions`` owns the constant; ``routers.assistant``,
    ``routers.network``, ``routers.agent_downloads`` and
    ``services.audit_trail`` each bind it BY NAME at import, so each of those
    module attributes is an independent copy that has to be redirected too.

    The per-root legacy-owner cache is dropped on the way out so this tmp root
    leaves nothing behind in the module's process-lifetime state.
    """
    root = tmp_path / "agent_sessions"
    root.mkdir()
    monkeypatch.setattr(agent_sessions, "SESSIONS_ROOT", root)
    monkeypatch.setattr(assistant_endpoint, "SESSIONS_ROOT", root)
    monkeypatch.setattr(network_router, "SESSIONS_ROOT", root)
    monkeypatch.setattr(agent_downloads_router, "SESSIONS_ROOT", root)
    monkeypatch.setattr(audit_trail, "SESSIONS_ROOT", root)
    try:
        yield root
    finally:
        agent_sessions._legacy_owners.pop(str(root), None)


@pytest.fixture()
def client(workspace_root: Path, sessions_root: Path) -> TestClient:
    return TestClient(main.app)


def _headers(workspace_id: str | None) -> dict[str, str]:
    return {"X-Workspace-Id": workspace_id} if workspace_id else {}


def _make_session(root: Path, session_id: str, *, owner: str | None) -> Path:
    """A session directory shaped like a real ``ArtifactManager`` work dir:
    a working copy, one saved version, and a one-record action ledger — so
    every session-scoped endpoint has something it COULD serve, and a denial
    is provably a denial rather than an empty session."""
    work_dir = root / session_id
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "working.inpx").write_bytes(INPX_A)
    (work_dir / "edit_001.inpx").write_bytes(INPX_A)
    (work_dir / f"trace_{session_id}.jsonl").write_text(
        json.dumps(
            {
                "kind": "action",
                "ts": "2026-08-04T09:00:00+00:00",
                "payload": {
                    "tool": "set_vehicle_input_volume",
                    "params": {"input_no": 1, "volume": 600},
                    "ok": True,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    if owner is not None:
        agent_sessions.write_session_meta(
            work_dir, {"session_id": session_id, "workspace_id": owner}
        )
    return work_dir


def _write_pending_approval(work_dir: Path, approval_id: str = "pending-1") -> None:
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
            }
        ),
        encoding="utf-8",
    )


def _all_session_surfaces(
    client: TestClient,
    session_id: str,
    headers: dict[str, str],
    approver_headers: dict[str, str],
):
    """Every HTTP surface that resolves a session id, as
    ``{name: response}`` — the full blast radius of the ownership predicate.

    ``routers/network.py``, ``routers/agent_downloads.py`` (single + list),
    ``routers/agent_audit.py`` (summary + export) and
    ``routers/assistant.py``'s ``/agent/approve`` all call the SAME predicate;
    a fix that only closed one of them would be no fix at all.

    Unit U8 authenticated the approve surface (and ONLY that one), so its call
    carries a real reviewer session on top of the workspace header. That is
    what keeps these tests measuring the WORKSPACE boundary: without it the
    request would stop at 401 and the ownership 404 below would never be
    exercised at all.
    """
    return {
        "network": client.get(f"/api/network?session_id={session_id}", headers=headers),
        "downloads_for_session": client.get(
            f"/api/agent/downloads?session_id={session_id}", headers=headers
        ),
        "downloads_list": client.get("/api/agent/downloads", headers=headers),
        "download_latest": client.get(
            f"/api/agent/downloads/{session_id}/latest", headers=headers
        ),
        "audit_summary": client.get(f"/api/agent/audit/{session_id}/summary", headers=headers),
        "audit_export": client.get(f"/api/agent/audit/{session_id}/export.docx", headers=headers),
        # The DECLINE leg deliberately: the ownership 404 is raised before the
        # approved/declined split, and declining resolves without a planner, an
        # edit session, or Vissim COM — so this surface stays deterministic and
        # free of a real agent turn while still proving the guard.
        "approve": client.post(
            "/api/assistant/agent/approve",
            json={"session_id": session_id, "approval_id": "pending-1", "approved": False},
            headers={**headers, **approver_headers},
        ),
    }


def _assert_fully_denied(responses: dict[str, Any], session_id: str) -> None:
    assert responses["network"].json()["source"] == {"view": "active"}
    assert responses["downloads_for_session"].json() == []
    assert session_id not in {
        entry["session_id"] for entry in responses["downloads_list"].json()
    }
    assert responses["download_latest"].status_code == 404
    assert responses["audit_summary"].status_code == 404
    assert responses["audit_export"].status_code == 404
    assert responses["approve"].status_code == 404


# ---------------------------------------------------------------------------
# (a) A sidecar-less session that appears AFTER the upgrade belongs to NOBODY
# ---------------------------------------------------------------------------


def test_a_sidecar_less_session_created_after_the_upgrade_is_denied_to_everyone(
    client: TestClient, sessions_root: Path, approver_headers: dict[str, str]
):
    """THE fail-open hole, measured end to end.

    ``routers/assistant.py`` used to write the sidecar inside ``try/except
    OSError`` and merely LOG on failure, so a live session with no recorded
    owner was reachable without any disk damage. Under the old predicate the
    default workspace was handed that session: ``/api/network?session_id=``
    answered ``{"view": "working"}`` with B's working copy parsed and served,
    while B itself — the actual owner — got ``{"view": "active"}``.

    Ordering is the whole point and is asserted, not assumed: the one-time
    legacy backfill runs FIRST (this is the box being upgraded), and only then
    does the sidecar-less session appear. "No sidecar" therefore means "never
    owned", not "old", and it is denied to the default workspace AND to B.
    """
    default_id = workspaces.default_workspace_id()
    workspace_b = client.post("/api/workspaces", json={"name": "B"}).json()["id"]

    # The upgrade happens on a box that has no legacy sessions yet...
    ledger = agent_sessions.backfill_legacy_session_owners(default_id, sessions_root)
    assert ledger is not None and ledger["session_ids"] == []

    # ...and only THEN does B's session appear, with no sidecar.
    session_id = "0011223344ff"
    _make_session(sessions_root, session_id, owner=None)
    _write_pending_approval(sessions_root / session_id)
    assert not agent_sessions.session_meta_path(sessions_root / session_id).exists()

    _assert_fully_denied(_all_session_surfaces(client, session_id, {}, approver_headers), session_id)
    _assert_fully_denied(
        _all_session_surfaces(client, session_id, _headers(workspace_b), approver_headers), session_id
    )

    # And the resume branch of the streaming endpoint refuses it too.
    for headers in ({}, _headers(workspace_b)):
        resumed = client.post(
            "/api/assistant/agent/query/stream",
            json={"message": "이어서", "session_id": session_id},
            headers=headers,
        )
        assert resumed.status_code == 404

    # The pending approval was NOT consumed by any of those refusals — a
    # denial must not have side effects on the session it refuses.
    assert (sessions_root / session_id / "pending_approval.json").is_file()


def test_a_sidecar_less_session_is_no_longer_an_existence_oracle(
    client: TestClient, sessions_root: Path
):
    """The leak was not only the payload: ``view: "working"`` vs
    ``view: "active"`` told an unauthenticated caller whether a given session
    id exists at all, which a ghost id can never produce."""
    default_id = workspaces.default_workspace_id()
    agent_sessions.backfill_legacy_session_owners(default_id, sessions_root)

    session_id = "0011223344ff"
    _make_session(sessions_root, session_id, owner=None)

    real = client.get(f"/api/network?session_id={session_id}")
    ghost = client.get(f"/api/network?session_id={GHOST_SESSION_ID}")

    assert real.status_code == ghost.status_code
    assert real.content == ghost.content
    assert dict(real.headers) == dict(ghost.headers)


# ---------------------------------------------------------------------------
# (b) A corrupt sidecar denies EVERYONE, the default workspace included
# ---------------------------------------------------------------------------


def test_a_truncated_sidecar_denies_the_default_workspace_and_the_true_owner(
    client: TestClient, sessions_root: Path, approver_headers: dict[str, str]
):
    """A sidecar torn by a crash mid-write parses as nothing. The old code
    read that as "legacy" and awarded the session to the default workspace.

    A session whose owner cannot be READ has no owner: the default workspace is
    refused, and so is B, which really did create it. Losing access to your own
    session is the correct direction to fail — the alternative is handing it to
    a project that never owned it.
    """
    workspace_b = client.post("/api/workspaces", json={"name": "B"}).json()["id"]

    session_id = "aabbccddeeff"
    work_dir = _make_session(sessions_root, session_id, owner=workspace_b)
    # Truncated exactly as an interrupted write leaves it.
    agent_sessions.session_meta_path(work_dir).write_text(
        '{"session_id": "aabbccddeeff", "workspace_i', encoding="utf-8"
    )
    _write_pending_approval(work_dir)

    _assert_fully_denied(_all_session_surfaces(client, session_id, {}, approver_headers), session_id)
    _assert_fully_denied(
        _all_session_surfaces(client, session_id, _headers(workspace_b), approver_headers), session_id
    )


def test_a_sidecar_naming_no_workspace_denies_everyone(
    client: TestClient, sessions_root: Path, approver_headers: dict[str, str]
):
    """Well-formed JSON, blank owner — the shape a partially-migrated or
    hand-edited sidecar takes. ``workspace_id`` present-but-falsy was the
    literal trigger of the old ``if not owner: owner = default`` branch."""
    session_id = "abcabcabc123"
    work_dir = _make_session(sessions_root, session_id, owner=None)
    agent_sessions.write_session_meta(work_dir, {"session_id": session_id, "workspace_id": ""})
    _write_pending_approval(work_dir)

    _assert_fully_denied(_all_session_surfaces(client, session_id, {}, approver_headers), session_id)


# ---------------------------------------------------------------------------
# (c) The pre-1.4 upgrade path must NOT regress
# ---------------------------------------------------------------------------


def test_a_pre_upgrade_session_stays_reachable_from_the_default_workspace(
    client: TestClient, sessions_root: Path, approver_headers: dict[str, str]
):
    """The documented legacy path, now explicit instead of implicit.

    A session that existed with no sidecar BEFORE the backfill is recorded as
    the default workspace's — the same place its dataset was migrated to — so
    chat histories, downloads and audit exports survive the upgrade. Another
    project still sees nothing.
    """
    session_id = "ffeeddccbbaa"
    work_dir = _make_session(sessions_root, session_id, owner=None)
    before = {path.name: path.read_bytes() for path in work_dir.iterdir()}

    other_id = client.post("/api/workspaces", json={"name": "other"}).json()["id"]

    owner_view = _all_session_surfaces(client, session_id, {}, approver_headers)
    assert owner_view["network"].json()["source"] == {"view": "working", "session_id": session_id}
    assert owner_view["downloads_for_session"].json()
    assert owner_view["download_latest"].status_code == 200
    assert owner_view["audit_summary"].status_code == 200
    assert owner_view["audit_export"].status_code == 200
    assert session_id in {entry["session_id"] for entry in owner_view["downloads_list"].json()}

    _assert_fully_denied(_all_session_surfaces(client, session_id, _headers(other_id), approver_headers), session_id)

    # The backfill records ownership AT THE ROOT, never inside the session
    # directory: ``routers/agent_downloads.py``'s module docstring promises a
    # listing or a download can never write into the work dir it reads, and
    # three tests pin that byte-for-byte.
    assert {path.name: path.read_bytes() for path in work_dir.iterdir()} == before
    assert agent_sessions.legacy_owners_path(sessions_root).is_file()


def test_the_backfill_is_idempotent_and_never_reclassifies_a_later_session(
    client: TestClient, sessions_root: Path
):
    """Re-running the upgrade must not widen the legacy set. A second call (a
    reboot, a retried migration) leaves the recorded population exactly as it
    was, so a sidecar-less session that appeared in between stays unowned."""
    default_id = workspaces.default_workspace_id()
    legacy_id = "ffeeddccbbaa"
    _make_session(sessions_root, legacy_id, owner=None)

    first = agent_sessions.backfill_legacy_session_owners(default_id, sessions_root)
    assert first is not None and first["session_ids"] == [legacy_id]

    later_id = "0011223344ff"
    _make_session(sessions_root, later_id, owner=None)

    second = agent_sessions.backfill_legacy_session_owners(default_id, sessions_root)
    assert second == first
    assert later_id not in second["session_ids"]

    assert agent_sessions.session_workspace_matches(legacy_id, default_id, sessions_root) is True
    assert agent_sessions.session_workspace_matches(later_id, default_id, sessions_root) is False


def test_the_backfill_never_overwrites_an_existing_sidecar(
    client: TestClient, sessions_root: Path
):
    """A session that already names its owner keeps naming it — including a
    corrupt sidecar, which must keep denying everyone rather than be silently
    re-awarded to the default workspace by a later upgrade pass."""
    default_id = workspaces.default_workspace_id()
    workspace_b = client.post("/api/workspaces", json={"name": "B"}).json()["id"]

    healthy = _make_session(sessions_root, "1111aaaa2222", owner=workspace_b)
    healthy_bytes = agent_sessions.session_meta_path(healthy).read_bytes()
    corrupt = _make_session(sessions_root, "3333bbbb4444", owner=workspace_b)
    agent_sessions.session_meta_path(corrupt).write_text("{ truncated", encoding="utf-8")

    agent_sessions.backfill_legacy_session_owners(default_id, sessions_root)

    assert agent_sessions.session_meta_path(healthy).read_bytes() == healthy_bytes
    assert agent_sessions.session_meta_path(corrupt).read_text(encoding="utf-8") == "{ truncated"
    assert agent_sessions.session_workspace_matches("1111aaaa2222", workspace_b, sessions_root)
    assert not agent_sessions.session_workspace_matches("3333bbbb4444", workspace_b, sessions_root)
    assert not agent_sessions.session_workspace_matches("3333bbbb4444", default_id, sessions_root)


def test_a_failed_backfill_denies_rather_than_caching_an_empty_legacy_set(
    client: TestClient, sessions_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """If the ledger cannot be established the predicate must keep denying and
    keep RETRYING — never cache "there are no legacy sessions", which would
    permanently strand a real upgrade."""
    default_id = workspaces.default_workspace_id()
    legacy_id = "ffeeddccbbaa"
    _make_session(sessions_root, legacy_id, owner=None)

    calls: list[int] = []
    real = agent_sessions.backfill_legacy_session_owners

    def failing(workspace_id, root=None):
        calls.append(1)
        return None

    monkeypatch.setattr(agent_sessions, "backfill_legacy_session_owners", failing)
    assert agent_sessions.session_workspace_matches(legacy_id, default_id, sessions_root) is False
    assert agent_sessions.session_workspace_matches(legacy_id, default_id, sessions_root) is False
    assert len(calls) == 2  # retried, not cached

    monkeypatch.setattr(agent_sessions, "backfill_legacy_session_owners", real)
    assert agent_sessions.session_workspace_matches(legacy_id, default_id, sessions_root) is True


# ---------------------------------------------------------------------------
# (d) A healthy sidecar: the owner works, and the intruder cannot tell the
#     session apart from one that does not exist
# ---------------------------------------------------------------------------


def test_a_healthy_session_serves_its_owner_and_is_indistinguishable_from_a_ghost(
    client: TestClient, sessions_root: Path, approver_headers: dict[str, str]
):
    """No existence oracle: an intruder's response must be byte-identical —
    body AND headers — to the same request for a session id that has never
    existed. Anything less lets one project enumerate another's session ids.
    """
    default_id = workspaces.default_workspace_id()
    intruder_id = client.post("/api/workspaces", json={"name": "intruder"}).json()["id"]

    session_id = "0123456789ab"
    work_dir = _make_session(sessions_root, session_id, owner=default_id)
    _write_pending_approval(work_dir)

    owner_view = _all_session_surfaces(client, session_id, {}, approver_headers)
    assert owner_view["network"].json()["source"] == {"view": "working", "session_id": session_id}
    assert owner_view["download_latest"].status_code == 200
    assert owner_view["download_latest"].content == INPX_A
    assert owner_view["audit_summary"].status_code == 200
    assert owner_view["audit_export"].status_code == 200
    # The owner's own (declining) approve call consumed the single-use pending
    # file, as it should. Re-arm it so the intruder's refusal below is checked
    # against a pending approval that really exists.
    _write_pending_approval(work_dir)

    intruder = _headers(intruder_id)
    denied = _all_session_surfaces(client, session_id, intruder, approver_headers)
    _assert_fully_denied(denied, session_id)

    ghost = _all_session_surfaces(client, GHOST_SESSION_ID, intruder, approver_headers)
    for surface in ("network", "downloads_for_session", "download_latest", "audit_summary",
                    "audit_export", "approve"):
        assert denied[surface].status_code == ghost[surface].status_code, surface
        assert denied[surface].content == ghost[surface].content, surface
        assert dict(denied[surface].headers) == dict(ghost[surface].headers), surface

    # The intruder's refusal did not consume the owner's pending approval.
    assert (work_dir / "pending_approval.json").is_file()


# ---------------------------------------------------------------------------
# Fix 2 — the untested /agent/query/stream foreign-session branch
# ---------------------------------------------------------------------------


def _fake_stream_turn(*args, **kwargs) -> AsyncIterator[str]:
    """Stand-in for ``_stream_agent_turn`` so the ACCEPTED leg of the resume
    branch can be asserted without a planner, a provider, or Vissim COM. The
    ownership check under test runs strictly before this is ever consumed."""

    async def generate() -> AsyncIterator[str]:
        yield json.dumps({"type": "done", "session_id": kwargs.get("session_id")}) + "\n"

    return generate()


def test_query_stream_refuses_a_session_id_owned_by_another_workspace(
    client: TestClient, sessions_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """``routers/assistant.py``'s resume guard, previously asserted nowhere.

    Resuming another project's session would point THIS request's
    ``ArtifactManager`` at that project's working copy and let the turn keep
    editing it. Refused with the same opaque 404 an unknown session gets.
    """
    monkeypatch.setattr(assistant_endpoint, "_stream_agent_turn", _fake_stream_turn)

    default_id = workspaces.default_workspace_id()
    intruder_id = client.post("/api/workspaces", json={"name": "intruder"}).json()["id"]

    session_id = "0123456789ab"
    _make_session(sessions_root, session_id, owner=default_id)

    refused = client.post(
        "/api/assistant/agent/query/stream",
        json={"message": "이어서 진행", "session_id": session_id},
        headers=_headers(intruder_id),
    )
    assert refused.status_code == 404
    assert refused.json()["detail"] == "No matching agent session."

    ghost = client.post(
        "/api/assistant/agent/query/stream",
        json={"message": "이어서 진행", "session_id": GHOST_SESSION_ID},
        headers=_headers(intruder_id),
    )
    assert ghost.status_code == 404
    assert ghost.content == refused.content

    # Discrimination: the SAME request from the owning workspace is accepted,
    # so the 404 above is the ownership branch and not a blanket refusal of
    # every client-supplied session_id.
    accepted = client.post(
        "/api/assistant/agent/query/stream",
        json={"message": "이어서 진행", "session_id": session_id},
        headers=_headers(default_id),
    )
    assert accepted.status_code == 200
    assert json.loads(accepted.text.splitlines()[0])["session_id"] == session_id


def test_query_stream_still_mints_a_fresh_session_without_a_session_id(
    client: TestClient, sessions_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """The guard is scoped to CLIENT-supplied ids. A request with no
    ``session_id`` mints one, and must not be caught by an ownership check
    against a session that does not exist yet."""
    monkeypatch.setattr(assistant_endpoint, "_stream_agent_turn", _fake_stream_turn)

    response = client.post("/api/assistant/agent/query/stream", json={"message": "안녕하세요"})

    assert response.status_code == 200
    minted = json.loads(response.text.splitlines()[0])["session_id"]
    assert minted and len(minted) == 12
    # ...and the fresh session got a sidecar naming its creator, which is what
    # makes it reachable on the NEXT turn under the fail-closed predicate.
    meta = agent_sessions.read_session_meta(minted, sessions_root)
    assert meta["workspace_id"] == workspaces.default_workspace_id()


# ---------------------------------------------------------------------------
# Fix 1, step 3 — an unowned session must never come into existence
# ---------------------------------------------------------------------------


def test_a_session_whose_sidecar_cannot_be_written_fails_the_turn(
    client: TestClient, sessions_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """The sidecar write used to be logged and swallowed, which is exactly how
    a LIVE session with no recorded owner got created without any disk damage —
    the reachable trigger for the fail-open hole above. Now the turn fails
    loudly: a refused turn with a clear reason beats a session nobody owns.
    """
    monkeypatch.setattr(assistant_endpoint, "_stream_agent_turn", _fake_stream_turn)

    def boom(work_dir, meta):
        raise OSError("disk full")

    monkeypatch.setattr(assistant_endpoint, "write_session_meta", boom)

    response = client.post("/api/assistant/agent/query/stream", json={"message": "안녕하세요"})

    assert response.status_code == 500
    assert response.json()["detail"] == "Could not start the agent session."

    # Nothing reachable was left behind: whatever directory the failed attempt
    # touched has no owner, so the predicate denies it to every workspace.
    default_id = workspaces.default_workspace_id()
    for entry in sessions_root.iterdir():
        if entry.is_dir():
            assert not agent_sessions.session_workspace_matches(
                entry.name, default_id, sessions_root
            )


# ---------------------------------------------------------------------------
# Fix 3 — the interactive-run TOCTOU freeze, asserted where it is CONSUMED
# ---------------------------------------------------------------------------


class _FakeVissimController:
    """Just enough of ``services.vissim_com.VissimController`` for
    ``_run_simulation_locked``'s single-seed loop to complete: one step, then
    ``SimSec`` at the requested duration."""

    def __init__(self) -> None:
        self.loaded: list[str] = []
        self._sim_sec = 0.0
        self._duration = 0.0

    def connect(self, visible: bool = False, attach_to_running: bool = True) -> None:
        assert attach_to_running is False  # the hard COM safety rule

    def load_network(self, path: str) -> None:
        self.loaded.append(path)

    def set_sim_duration(self, duration: int) -> None:
        self._duration = float(duration)

    def set_random_seed(self, seed: int) -> None:
        pass

    def get_sim_sec(self) -> float:
        return self._sim_sec

    def run_single_step(self) -> None:
        self._sim_sec = self._duration

    def is_sim_finished(self) -> bool:
        return self._sim_sec >= self._duration

    def disconnect(self) -> None:
        pass


class _DeferredThread:
    """Captures the worker thread instead of starting it, so the test controls
    the exact instant between "job accepted" and "worker starts" — which is
    the race window the TOCTOU fix exists to close."""

    started: "list[_DeferredThread]" = []

    def __init__(self, target=None, args=(), daemon=False, **kwargs) -> None:
        self.target = target
        self.args = args

    def start(self) -> None:
        _DeferredThread.started.append(self)

    def run_now(self) -> None:
        self.target(*self.args)


def test_the_run_worker_builds_from_the_source_frozen_at_enqueue(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """TOCTOU, asserted at the point of CONSUMPTION.

    ``tests/test_workspaces.py``'s existing TOCTOU test monkeypatches
    ``_run_simulation`` away and asserts only what the ENQUEUE side passed, so
    it cannot see a mutant that keeps the signature and re-resolves
    ``_simulation_source_inpx()`` at the top of ``_run_simulation_locked``.
    This test runs the REAL ``_run_simulation_locked`` with only the externals
    stubbed — COM and the network builder — swaps the active dataset while the
    worker is still parked, and asserts on the BYTES that actually reached
    ``build_simulation_network``.

    The swap is done at ``get_active_inpx_path``, the seam
    ``_simulation_source_inpx`` itself resolves through, so the function under
    test is untouched: a mutant that calls it late genuinely observes dataset
    B, and this test genuinely fails.
    """
    from services import simulation_network_builder, vissim_com

    dataset_a = tmp_path / "a" / "current.inpx"
    dataset_b = tmp_path / "b" / "current.inpx"
    dataset_a.parent.mkdir(parents=True)
    dataset_b.parent.mkdir(parents=True)
    dataset_a.write_bytes(INPX_A)
    dataset_b.write_bytes(INPX_B)

    active = {"path": dataset_a}
    monkeypatch.delenv("VISSIM_SIMULATION_INPX_PATH", raising=False)
    monkeypatch.setattr(simulations_router, "get_active_inpx_path", lambda: active["path"])
    monkeypatch.setattr(
        simulations_router, "_simulation_capabilities", lambda: {"can_run": True, "reason": None}
    )

    # COM is never available (and never wanted) under pytest; a stub module
    # keeps `_run_simulation`'s CoInitialize path cross-platform.
    fake_pythoncom = types.ModuleType("pythoncom")
    fake_pythoncom.CoInitialize = lambda: None
    fake_pythoncom.CoUninitialize = lambda: None
    monkeypatch.setitem(__import__("sys").modules, "pythoncom", fake_pythoncom)

    controller = _FakeVissimController()
    monkeypatch.setattr(vissim_com, "VissimController", lambda: controller)

    built: dict[str, Any] = {}

    def fake_build(source_inpx: Path, job_id: str, scenario_id=None, speed_limits=None):
        built["source_inpx"] = Path(source_inpx)
        built["bytes"] = Path(source_inpx).read_bytes()
        runtime_copy = tmp_path / f"runtime_{job_id}.inpx"
        runtime_copy.write_bytes(built["bytes"])
        return runtime_copy

    monkeypatch.setattr(simulation_network_builder, "build_simulation_network", fake_build)

    _DeferredThread.started = []
    monkeypatch.setattr(
        simulations_router, "threading", types.SimpleNamespace(Thread=_DeferredThread)
    )

    default_id = workspaces.default_workspace_id()
    with workspaces.bind_workspace(default_id):
        submitted = simulations_router.start_simulation_job(
            simulations_router.SimParams(name="toctou", sim_duration=60)
        )
    assert submitted["status"] == "queued"
    assert len(_DeferredThread.started) == 1

    # The dataset is swapped while the worker is still parked -- exactly the
    # window between "job accepted" (which already stamped dataset_revision)
    # and "worker starts Vissim".
    active["path"] = dataset_b
    with workspaces.bind_workspace(default_id):
        assert simulations_router._simulation_source_inpx() == dataset_b

    _DeferredThread.started[0].run_now()

    assert built["source_inpx"] == dataset_a
    assert built["bytes"] == INPX_A, "the worker built from the POST-swap dataset (TOCTOU)"
    assert built["bytes"] != INPX_B
    assert controller.loaded, "the run never reached load_network; the stub chain is wrong"

    job = simulations_router._job_snapshot(submitted["id"])
    assert job["status"] == "done", job.get("error")

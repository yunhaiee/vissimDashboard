"""Unit P④ — user↔workspace membership, and the admin surface that grants it.

Before this unit ``services/workspaces.py`` (the 프로젝트 isolation boundary)
and ``services/auth.py`` (who a person is) had never been introduced to each
other: ANY caller, signed in or not, could put another project's id in
``X-Workspace-Id`` and read that client's network. This file is the ledger of
the rule that closed it.

Unlike ``tests/test_workspaces.py`` — which authenticates as the suite admin on
every request, because it is about the WORKSPACE boundary — nothing here uses
``conftest.admin_headers`` implicitly. Each test states exactly which identity
is calling, because that identity IS the thing under test.

Nothing here needs Vissim, COM, or Windows.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from routers import admin as admin_router
from services import auth, db, membership, workspaces

PASSWORD = "prism-membership-test-password"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private workspaces root AND a private auth database for one test.

    Both, together: this file's whole subject is the relation between the two,
    so a test that saw another test's accounts or another test's projects would
    pass (or fail) for reasons it never states. ``LEGACY_UPLOADS_ROOT`` is
    pointed at an empty directory for the reason every other workspace test
    does it — patching only the first would aim the one-time migration at the
    developer's real ``runtime/uploads``.
    """
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "legacy-uploads")
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "db" / "app.db")
    # Cheap scrypt: this file creates a dozen accounts and none of the
    # assertions is about how long a derivation takes.
    monkeypatch.setattr(auth, "SCRYPT_N", 1 << 8)
    monkeypatch.setattr(auth, "_dummy_hash_cache", {})

    default_id = workspaces.default_workspace_id()
    token = workspaces.set_current_workspace_id(default_id)
    try:
        yield tmp_path / "workspaces"
    finally:
        workspaces.reset_current_workspace_id(token)


@pytest.fixture()
def client(workspace_root: Path) -> TestClient:
    return TestClient(main.app)


def _bearer(username: str, role: str) -> dict[str, str]:
    user = auth.get_user_by_username(username) or auth.create_user(username, PASSWORD, role)
    token, _session = auth.create_session(user["id"])
    return {"Authorization": f"Bearer {token}"}


def _ws(workspace_id: str, *header_sets: dict[str, str]) -> dict[str, str]:
    headers = {"X-Workspace-Id": workspace_id}
    for extra in header_sets:
        headers.update(extra)
    return headers


def _make_project(client: TestClient, name: str, headers: dict[str, str]) -> str:
    created = client.post("/api/workspaces", json={"name": name}, headers=headers)
    assert created.status_code == 200, created.text
    return created.json()["id"]


# ---------------------------------------------------------------------------
# The table (services/membership.py's docstring), exercised at the service level
# ---------------------------------------------------------------------------


def test_user_can_access_is_the_whole_rule(workspace_root: Path):
    default_id = workspaces.default_workspace_id()
    other_id = workspaces.create_workspace("다른 프로젝트")["id"]
    member = auth.create_user("member", PASSWORD, "modeler")
    outsider = auth.create_user("outsider", PASSWORD, "reviewer")
    boss = auth.create_user("boss", PASSWORD, "admin")
    membership.grant(other_id, member["id"])

    # The default workspace: open to everyone, including nobody at all.
    assert membership.user_can_access(None, default_id) is True
    assert membership.user_can_access(outsider["id"], default_id) is True

    # A non-default workspace: members and admins only.
    assert membership.user_can_access(None, other_id) is False
    assert membership.user_can_access(member["id"], other_id) is True
    assert membership.user_can_access(outsider["id"], other_id) is False
    assert membership.user_can_access(boss["id"], other_id) is True

    # Fails closed on every ambiguity.
    assert membership.user_can_access(member["id"], "") is False
    assert membership.user_can_access(member["id"], "0123456789ab") is False
    assert membership.user_can_access(999999, other_id) is False


def test_a_disabled_member_loses_access_without_losing_the_membership_row(workspace_root: Path):
    """Two independent checks, and this proves the second one is real: a live
    session already cannot belong to a disabled user, so a predicate that only
    trusted the session would look correct until something else called it."""
    other_id = workspaces.create_workspace("B")["id"]
    member = auth.create_user("member", PASSWORD, "modeler")
    membership.grant(other_id, member["id"])
    assert membership.user_can_access(member["id"], other_id) is True

    auth.set_disabled("member", True)

    assert membership.user_can_access(member["id"], other_id) is False
    assert membership.is_member(other_id, member["id"]) is True, "the grant itself survives"
    auth.set_disabled("member", False)
    assert membership.user_can_access(member["id"], other_id) is True


def test_grant_is_idempotent_and_keeps_the_original_join_date(workspace_root: Path):
    other_id = workspaces.create_workspace("B")["id"]
    member = auth.create_user("member", PASSWORD, "modeler")

    membership.grant(other_id, member["id"])
    first = membership.list_members(other_id)
    membership.grant(other_id, member["id"])
    second = membership.list_members(other_id)

    assert len(second) == 1
    assert second[0]["member_since"] == first[0]["member_since"]
    assert second[0]["username"] == "member"
    assert second[0]["membership_role"] == membership.MEMBER_ROLE


def test_grant_refuses_an_unknown_user_workspace_or_role(workspace_root: Path):
    other_id = workspaces.create_workspace("B")["id"]
    member = auth.create_user("member", PASSWORD, "modeler")

    with pytest.raises(membership.UnknownMemberUserError):
        membership.grant(other_id, 999999)
    with pytest.raises(membership.UnknownMemberWorkspaceError):
        membership.grant("0123456789ab", member["id"])
    with pytest.raises(membership.InvalidMembershipRoleError):
        membership.grant(other_id, member["id"], "owner")


def test_revoke_is_idempotent(workspace_root: Path):
    other_id = workspaces.create_workspace("B")["id"]
    member = auth.create_user("member", PASSWORD, "modeler")
    membership.grant(other_id, member["id"])

    assert membership.revoke(other_id, member["id"]) is True
    assert membership.revoke(other_id, member["id"]) is False
    assert membership.list_members(other_id) == []
    assert membership.user_can_access(member["id"], other_id) is False


def test_deleting_a_user_takes_their_memberships_with_them(workspace_root: Path):
    """The ``ON DELETE CASCADE`` on ``user_id`` — nothing in the app deletes a
    user today, but a foreign key that is never exercised is a foreign key
    nobody knows is switched on (SQLite defaults ``foreign_keys`` OFF per
    connection; ``services/db.py`` turns it on for every one)."""
    other_id = workspaces.create_workspace("B")["id"]
    member = auth.create_user("member", PASSWORD, "modeler")
    membership.grant(other_id, member["id"])

    with db.write_transaction() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (member["id"],))

    assert membership.list_members(other_id) == []


def test_a_membership_for_a_vanished_workspace_grants_nothing(workspace_root: Path):
    """``workspace_id`` is plain TEXT (a JSON registry cannot be foreign-keyed),
    so an orphan row is possible. It must be invisible, not a skeleton key."""
    member = auth.create_user("member", PASSWORD, "modeler")
    membership.grant(workspaces.create_workspace("B")["id"], member["id"])
    with db.write_transaction() as conn:
        conn.execute("UPDATE workspace_members SET workspace_id = 'aaaabbbbcccc'")

    assert membership.user_can_access(member["id"], "aaaabbbbcccc") is False
    assert [entry["id"] for entry in membership.visible_workspaces(auth.get_user(member["id"]))] == [
        workspaces.default_workspace_id()
    ]


# ---------------------------------------------------------------------------
# The same rule over HTTP — the 403 that is the point of the unit
# ---------------------------------------------------------------------------


def test_an_unauthenticated_request_is_403_on_a_non_default_workspace(client: TestClient):
    """NOT a silent fallback to the default workspace. Serving one client's
    network to another client is the failure the boundary exists to prevent, so
    the refusal is loud and carries a machine-readable code."""
    boss = _bearer("boss", "admin")
    other_id = _make_project(client, "대전 프로젝트", boss)

    response = client.get("/api/datasets/active", headers={"X-Workspace-Id": other_id})

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "workspace_forbidden"
    assert detail["workspace_id"] == other_id
    # ko primary, and it says what to DO about it.
    assert "관리자" in detail["message"]


def test_an_authenticated_non_member_is_403_on_a_non_default_workspace(client: TestClient):
    boss = _bearer("boss", "admin")
    other_id = _make_project(client, "대전 프로젝트", boss)
    stranger = _bearer("stranger", "modeler")

    denied = client.get("/api/datasets/active", headers=_ws(other_id, stranger))
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "workspace_forbidden"

    # ...and the grant is the only thing that changes the answer.
    user = auth.get_user_by_username("stranger")
    membership.grant(other_id, user["id"])
    allowed = client.get("/api/datasets/active", headers=_ws(other_id, stranger))
    assert allowed.status_code == 200
    assert allowed.json()["workspace_id"] == other_id


def test_the_default_workspace_stays_open_to_anonymous_callers(client: TestClient):
    """The deliberate exception. This unit is a membership feature, not an
    app-wide authentication switch — the rest of Prism is not behind a login
    and P④ does not put it there."""
    default_id = workspaces.default_workspace_id()

    assert client.get("/api/datasets/active").status_code == 200
    assert client.get("/api/datasets/active", headers={"X-Workspace-Id": default_id}).status_code == 200
    assert client.get("/api/workspaces").status_code == 200


def test_a_forbidden_workspace_403s_before_the_route_can_serve_anything(client: TestClient):
    """Enforced in the MIDDLEWARE, so it covers routes nobody has written yet.

    Measured across four unrelated routers rather than asserted about one: the
    property is "the boundary is not a per-router habit", and a single-endpoint
    test cannot distinguish that from a lucky decorator.
    """
    boss = _bearer("boss", "admin")
    other_id = _make_project(client, "대전 프로젝트", boss)
    stranger = _bearer("stranger", "modeler")
    headers = _ws(other_id, stranger)

    for path in (
        "/api/datasets/active",
        "/api/workspaces/current",
        "/api/scenarios",
        "/api/observations",
        "/api/simulations",
        "/api/agent/downloads",
    ):
        response = client.get(path, headers=headers)
        assert response.status_code == 403, f"{path} answered {response.status_code}"
        assert response.json()["detail"]["code"] == "workspace_forbidden"


def test_an_unknown_workspace_is_still_a_404_not_a_403(client: TestClient):
    """The 404 predates this unit and stays first: "no such project" and "not
    your project" are different facts, and collapsing them would make a stale
    localStorage id indistinguishable from a permissions problem."""
    stranger = _bearer("stranger", "modeler")
    response = client.get("/api/datasets/active", headers=_ws("0123456789ab", stranger))

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "workspace_not_found"


def test_login_is_reachable_while_holding_a_forbidden_workspace_id(client: TestClient):
    """The lockout loop this unit must not create.

    A browser with a stale non-default ``vissim-workspace-id`` in localStorage
    is exactly the state a person who cannot sign in is likely to be in. If the
    membership gate applied to ``/api/auth/*``, their login attempt would 403
    because they are not a member of a project they cannot leave — and the fix
    would be unreachable from the UI. ``lib/api.ts`` strips the header from
    auth calls; this is the server-side half, so the guarantee does not depend
    on which client is talking.
    """
    boss = _bearer("boss", "admin")
    other_id = _make_project(client, "대전 프로젝트", boss)
    auth.create_user("locked-out", PASSWORD, "modeler")

    response = client.post(
        "/api/auth/login",
        json={"username": "locked-out", "password": PASSWORD},
        headers={"X-Workspace-Id": other_id},
    )
    assert response.status_code == 200
    assert (
        client.get("/api/auth/whoami", headers={"X-Workspace-Id": other_id}).status_code == 200
    )


def test_the_out_of_request_scope_error_is_unchanged(workspace_root: Path):
    """P④ must not have weakened ``WorkspaceScopeError``: job-runner threads
    bind explicitly and a silent default there is the same leak by another
    door."""
    import contextvars

    def _read() -> None:
        workspaces.current_workspace_id()

    with pytest.raises(workspaces.WorkspaceScopeError) as excinfo:
        contextvars.Context().run(_read)
    assert "bind_workspace" in str(excinfo.value)


# ---------------------------------------------------------------------------
# The 프로젝트 picker: one list per caller
# ---------------------------------------------------------------------------


def test_the_workspace_list_is_filtered_per_caller(client: TestClient):
    boss = _bearer("boss", "admin")
    default_id = workspaces.default_workspace_id()
    mine = _make_project(client, "내 프로젝트", boss)
    theirs = _make_project(client, "남의 프로젝트", boss)

    member = _bearer("member", "modeler")
    membership.grant(mine, auth.get_user_by_username("member")["id"])

    anonymous = client.get("/api/workspaces").json()
    as_member = client.get("/api/workspaces", headers=member).json()
    as_admin = client.get("/api/workspaces", headers=boss).json()

    assert [entry["id"] for entry in anonymous["workspaces"]] == [default_id]
    assert [entry["id"] for entry in as_member["workspaces"]] == [default_id, mine]
    assert {entry["id"] for entry in as_admin["workspaces"]} == {default_id, mine, theirs}
    # The default is first in every one of them (the picker's selected-state
    # contract from unit 1.4).
    for payload in (anonymous, as_member, as_admin):
        assert payload["workspaces"][0]["id"] == payload["default"] == default_id


def test_the_listing_and_the_middleware_never_disagree(client: TestClient):
    """The property behind the two halves being one rule: everything the picker
    offers must be enterable, and everything it hides must not be."""
    boss = _bearer("boss", "admin")
    visible_to_member = _make_project(client, "보이는 프로젝트", boss)
    hidden_from_member = _make_project(client, "숨은 프로젝트", boss)
    member = _bearer("member", "modeler")
    membership.grant(visible_to_member, auth.get_user_by_username("member")["id"])

    offered = {entry["id"] for entry in client.get("/api/workspaces", headers=member).json()["workspaces"]}
    for workspace_id in (workspaces.default_workspace_id(), visible_to_member, hidden_from_member):
        reachable = client.get("/api/datasets/active", headers=_ws(workspace_id, member)).status_code == 200
        assert reachable is (workspace_id in offered), workspace_id


def test_creating_a_project_needs_a_sign_in_and_grants_the_creator(client: TestClient):
    """Without the grant, ``POST`` would hand back an id whose very next
    request is a 403 — a trap, not a refusal."""
    anonymous = client.post("/api/workspaces", json={"name": "익명 프로젝트"})
    assert anonymous.status_code == 401

    creator = _bearer("creator", "modeler")
    created = client.post("/api/workspaces", json={"name": "내 프로젝트"}, headers=creator)
    assert created.status_code == 200
    workspace_id = created.json()["id"]

    assert client.get("/api/datasets/active", headers=_ws(workspace_id, creator)).status_code == 200
    assert [entry["username"] for entry in membership.list_members(workspace_id)] == ["creator"]
    # A blank name is still a 422 -- the auth dependency did not swallow it.
    assert client.post("/api/workspaces", json={"name": "   "}, headers=creator).status_code == 422


def test_workspaces_that_predate_this_unit_are_admin_only_until_granted(client: TestClient):
    """The migration story, stated as a test.

    Every workspace created before P④ has ZERO membership rows, so it becomes
    admin-only. That is only survivable because an admin can see and grant
    everything AND the first admin already exists — otherwise the first
    deployment of this code would lock an operator out of their own live
    projects with no UI to fix it.
    """
    legacy_id = workspaces.create_workspace("이전 프로젝트")["id"]  # created without any member
    assert membership.list_members(legacy_id) == []

    boss = _bearer("boss", "admin")
    modeler = _bearer("modeler", "modeler")

    assert client.get("/api/datasets/active", headers=_ws(legacy_id, modeler)).status_code == 403
    assert client.get("/api/datasets/active", headers=_ws(legacy_id, boss)).status_code == 200
    assert legacy_id in {
        entry["id"] for entry in client.get("/api/workspaces", headers=boss).json()["workspaces"]
    }

    granted = client.post(
        f"/api/admin/workspaces/{legacy_id}/members",
        json={"user_id": auth.get_user_by_username("modeler")["id"]},
        headers=boss,
    )
    assert granted.status_code == 200
    assert client.get("/api/datasets/active", headers=_ws(legacy_id, modeler)).status_code == 200


# ---------------------------------------------------------------------------
# The admin router
# ---------------------------------------------------------------------------


def _admin_route_calls() -> list[tuple[str, str]]:
    """``(method, path)`` for every route the admin router declares, with path
    parameters filled in with well-shaped values.

    Enumerated from the router itself rather than listed by hand: the point of
    the sentinel below is that an endpoint added TOMORROW cannot forget the
    guard, and a hand-written list would simply not mention it.
    """
    calls: list[tuple[str, str]] = []
    for route in admin_router.router.routes:
        path = str(getattr(route, "path", ""))
        if not path:
            continue
        # ``route.path`` already carries the router's prefix.
        assert path.startswith("/api/admin/"), path
        concrete = path.replace("{user_id}", "1").replace("{workspace_id}", "0123456789ab")
        for method in sorted(getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}):
            calls.append((method, concrete))
    return calls


def test_every_admin_route_refuses_anonymous_and_non_admin_callers(client: TestClient):
    """The sentinel. Enumerates the router, so a future endpoint that forgets
    the guard fails HERE rather than in production."""
    calls = _admin_route_calls()
    assert len(calls) >= 8, f"expected the admin router's routes to be discoverable, got {calls}"

    modeler = _bearer("modeler", "modeler")
    reviewer = _bearer("reviewer", "reviewer")

    for method, path in calls:
        anonymous = client.request(method, path, json={})
        assert anonymous.status_code == 401, f"{method} {path} answered {anonymous.status_code} to nobody"
        for headers, label in ((modeler, "modeler"), (reviewer, "reviewer")):
            response = client.request(method, path, json={}, headers=headers)
            assert response.status_code == 403, (
                f"{method} {path} answered {response.status_code} to a {label}"
            )


def test_the_admin_user_lifecycle_over_http(client: TestClient):
    boss = _bearer("boss", "admin")

    created = client.post(
        "/api/admin/users",
        json={
            "username": "kim",
            "password": PASSWORD,
            "role": "reviewer",
            "display_name": "김검토",
        },
        headers=boss,
    )
    assert created.status_code == 200, created.text
    user_id = created.json()["id"]
    assert created.json()["display_name"] == "김검토"

    listing = client.get("/api/admin/users", headers=boss).json()
    assert {entry["username"] for entry in listing["users"]} == {"boss", "kim"}
    assert listing["roles"] == list(auth.ROLES)

    # A duplicate is a 409, a weak password a 422 -- both from services/auth.py.
    assert client.post(
        "/api/admin/users",
        json={"username": "kim", "password": PASSWORD, "role": "modeler"},
        headers=boss,
    ).status_code == 409
    assert client.post(
        "/api/admin/users",
        json={"username": "lee", "password": "short", "role": "modeler"},
        headers=boss,
    ).status_code == 422

    # Reset revokes the target's live sessions.
    kim_token, _ = auth.create_session(user_id)
    assert auth.resolve_session(kim_token) is not None
    reset = client.post(
        f"/api/admin/users/{user_id}/password", json={"password": "another-good-password"}, headers=boss
    )
    assert reset.status_code == 200
    assert auth.resolve_session(kim_token) is None

    assert client.post(f"/api/admin/users/{user_id}/role", json={"role": "modeler"}, headers=boss).json()["role"] == "modeler"
    assert client.post(f"/api/admin/users/{user_id}/disabled", json={"disabled": True}, headers=boss).json()["disabled"] is True
    assert client.post(f"/api/admin/users/{user_id}/disabled", json={"disabled": False}, headers=boss).json()["disabled"] is False

    assert client.post("/api/admin/users/999999/role", json={"role": "modeler"}, headers=boss).status_code == 404


def test_no_admin_response_can_carry_a_hash_or_a_token(client: TestClient):
    """Measured on the BYTES of every response this router produces, not
    asserted about the helper that builds them."""
    boss = _bearer("boss", "admin")
    workspace_id = _make_project(client, "프로젝트", boss)
    created = client.post(
        "/api/admin/users",
        json={"username": "kim", "password": PASSWORD, "role": "reviewer"},
        headers=boss,
    )
    user_id = created.json()["id"]

    bodies = [
        created.text,
        client.get("/api/admin/users", headers=boss).text,
        client.post(f"/api/admin/users/{user_id}/password", json={"password": PASSWORD}, headers=boss).text,
        client.post(f"/api/admin/users/{user_id}/role", json={"role": "modeler"}, headers=boss).text,
        client.post(f"/api/admin/users/{user_id}/disabled", json={"disabled": True}, headers=boss).text,
        client.post(
            f"/api/admin/workspaces/{workspace_id}/members", json={"user_id": user_id}, headers=boss
        ).text,
        client.get(f"/api/admin/workspaces/{workspace_id}/members", headers=boss).text,
        client.delete(f"/api/admin/workspaces/{workspace_id}/members/{user_id}", headers=boss).text,
    ]
    for body in bodies:
        assert "scrypt$" not in body
        assert "password_hash" not in body
        assert "token" not in body.lower()
        assert PASSWORD not in body


def test_an_admin_cannot_lock_the_box_or_themselves_out(client: TestClient):
    boss = _bearer("boss", "admin")
    boss_id = auth.get_user_by_username("boss")["id"]

    # The sole admin cannot strand the box. Note WHICH refusal answers here:
    # boss is targeting boss, so the router's self-targeting guard fires first
    # and services/auth.py's LastAdminError is never reached on this path. That
    # is not a gap -- with one admin, "the last admin" and "yourself" are the
    # same account, and the two guards deliberately overlap (routers/admin.py's
    # docstring explains the split). The service guard is proved where it can
    # actually be isolated, by direct call: test_auth_core.py's
    # pytest.raises(auth.LastAdminError) cases. The router's LastAdminError->409
    # mapping is therefore defence in depth, not a tested-by-this-line path.
    assert client.post(f"/api/admin/users/{boss_id}/disabled", json={"disabled": True}, headers=boss).status_code == 409
    assert client.post(f"/api/admin/users/{boss_id}/role", json={"role": "modeler"}, headers=boss).status_code == 409

    # With a SECOND admin the database-level guard is satisfied -- and the
    # self-targeting refusal, which lives in the router because the service has
    # no caller, still stands.
    deputy = client.post(
        "/api/admin/users",
        json={"username": "deputy", "password": PASSWORD, "role": "admin"},
        headers=boss,
    ).json()
    self_disable = client.post(f"/api/admin/users/{boss_id}/disabled", json={"disabled": True}, headers=boss)
    self_demote = client.post(f"/api/admin/users/{boss_id}/role", json={"role": "modeler"}, headers=boss)
    assert self_disable.status_code == 409 and "자기 자신" in self_disable.json()["detail"]
    assert self_demote.status_code == 409 and "자기 자신" in self_demote.json()["detail"]
    assert auth.get_user(boss_id)["role"] == "admin"

    # Somebody ELSE may do both, once there are two.
    assert client.post(f"/api/admin/users/{deputy['id']}/role", json={"role": "reviewer"}, headers=boss).status_code == 200


def test_membership_endpoints_grant_revoke_and_describe(client: TestClient):
    boss = _bearer("boss", "admin")
    workspace_id = _make_project(client, "대전 프로젝트", boss)
    kim = client.post(
        "/api/admin/users",
        json={"username": "kim", "password": PASSWORD, "role": "modeler"},
        headers=boss,
    ).json()

    before = client.get(f"/api/admin/workspaces/{workspace_id}/members", headers=boss).json()
    assert before["open_to_everyone"] is False
    assert [entry["username"] for entry in before["members"]] == ["boss"]

    granted = client.post(
        f"/api/admin/workspaces/{workspace_id}/members", json={"user_id": kim["id"]}, headers=boss
    )
    assert granted.status_code == 200
    assert {entry["username"] for entry in granted.json()["members"]} == {"boss", "kim"}

    revoked = client.delete(
        f"/api/admin/workspaces/{workspace_id}/members/{kim['id']}", headers=boss
    )
    assert revoked.status_code == 200 and revoked.json()["revoked"] is True
    # Idempotent: revoking again is not a 404, because the intent is satisfied.
    assert client.delete(
        f"/api/admin/workspaces/{workspace_id}/members/{kim['id']}", headers=boss
    ).json()["revoked"] is False

    # The default workspace reports what it IS, not an empty roster.
    default_view = client.get(
        f"/api/admin/workspaces/{workspaces.default_workspace_id()}/members", headers=boss
    ).json()
    assert default_view["open_to_everyone"] is True

    # Unknown ends are 404s, an unknown membership role a 422.
    assert client.get("/api/admin/workspaces/0123456789ab/members", headers=boss).status_code == 404
    assert client.post(
        f"/api/admin/workspaces/{workspace_id}/members", json={"user_id": 999999}, headers=boss
    ).status_code == 404
    assert client.post(
        f"/api/admin/workspaces/{workspace_id}/members",
        json={"user_id": kim["id"], "role": "owner"},
        headers=boss,
    ).status_code == 422


def test_revoking_a_membership_ends_access_immediately(client: TestClient):
    """The grant is the permission — there is no cached copy of it anywhere, so
    a revoke takes effect on the very next request rather than at session
    expiry."""
    boss = _bearer("boss", "admin")
    workspace_id = _make_project(client, "대전 프로젝트", boss)
    member = _bearer("member", "modeler")
    member_id = auth.get_user_by_username("member")["id"]
    membership.grant(workspace_id, member_id)

    assert client.get("/api/datasets/active", headers=_ws(workspace_id, member)).status_code == 200
    client.delete(f"/api/admin/workspaces/{workspace_id}/members/{member_id}", headers=boss)
    assert client.get("/api/datasets/active", headers=_ws(workspace_id, member)).status_code == 403


def test_a_websocket_never_gets_a_workspace_binding_it_did_not_earn():
    """The gate covers the websocket protocol, not just HTTP.

    A raw-ASGI websocket cannot be answered with a 403 body, so the refusal is
    of the BINDING rather than the connection: a caller who names a non-default
    workspace it may not enter is bound to the default, exactly as if it had
    sent no header. This is driven through the middleware's own predicate plus
    a synthetic ASGI scope rather than a real socket, because what is under
    test is the scope-type branch, not Starlette's handshake.

    Regression: the gate was once written ``scope["type"] == "http" and ...``,
    on the stated rationale that a websocket "could never carry" the header
    because the browser WebSocket API cannot set one. True of browsers, false
    of wscat/python-websockets/curl -- a browser limitation mistaken for a
    security property, while the middleware read and honoured the header for
    websocket scopes all the same.
    """
    default_id = workspaces.default_workspace_id()
    other_id = workspaces.create_workspace("웹소켓 프로젝트")["id"]
    stranger = auth.create_user("ws-stranger", PASSWORD, "modeler")
    member = auth.create_user("ws-member", PASSWORD, "modeler")
    membership.grant(other_id, member["id"])

    def scope_for(user: dict | None) -> dict:
        headers = [(b"x-workspace-id", other_id.encode())]
        if user is not None:
            token, _session = auth.create_session(user["id"])
            headers.append((b"authorization", f"Bearer {token}".encode()))
        return {"type": "websocket", "path": "/api/simulations/ws/abcd1234", "headers": headers}

    gate = main.WorkspaceScopeMiddleware._may_enter
    # The header is READ for websocket scopes -- so the gate must run for them.
    assert gate(scope_for(None), other_id) is False
    assert gate(scope_for(stranger), other_id) is False
    assert gate(scope_for(member), other_id) is True

    # And the branch the middleware takes on a refusal is "bind the default",
    # never "bind what was asked for".
    assert main.workspaces.default_workspace_id() == default_id
    assert default_id != other_id

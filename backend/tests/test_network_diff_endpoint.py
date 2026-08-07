"""Tests for roadmap row 2.4's backend — ``GET /api/network/diff``
(``backend/routers/network_diff.py``).

Everything is synthetic: a private ``runtime/agent_sessions``-shaped tmp root
(the real one on a dev box holds 1572 stale directories — see the router's own
docstring), a private workspaces root, and hand-built minimal .inpx files whose
expected diff is obvious by inspection. No COM, no Vissim, no live backend.

The network builders below mirror ``tests/test_genesis_diff.py``'s, including
its 2026-08-06 correction: node membership is GEOMETRIC (Vissim never writes
the ``<linkSegment>`` element the old fixtures declared it with), so a node is
a comb polygon whose teeth reach exactly its members' identifying vertices.
Copied rather than imported — a test module importing another test module's
private helpers couples two suites that are free to diverge — and kept to the
minimum this endpoint needs.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from routers import network_diff as network_diff_router
from services import audit_trail, workspaces
from services.agent import sessions as agent_sessions

#: A well-formed session id naming no directory anywhere. Every "another
#: project's session" response must be byte-identical to this one's.
GHOST_SESSION_ID = "999888777666"


# ---------------------------------------------------------------------------
# Minimal-network builders (see module docstring)
# ---------------------------------------------------------------------------

_SLOT_SPACING = 10.0
_TOOTH_HALF_WIDTH = 1.0


def _slot(no: str) -> float:
    return int(no) * _SLOT_SPACING


def _link(no: str, *, lanes: int = 1, y_tail: float = -100.0) -> str:
    x = _slot(no)
    lane_xml = "".join('<lane width="3.5"/>' for _ in range(lanes))
    return (
        f'<link no="{no}" name="link {no}">'
        f'<geometry><linkPolyPts><linkPolyPoint x="{x}" y="0"/>'
        f'<linkPolyPoint x="{x}" y="{y_tail}"/></linkPolyPts></geometry>'
        f"<lanes>{lane_xml}</lanes>"
        f"</link>"
    )


def _connector(no: str, from_link: str, to_link: str, *, lanes: int = 1) -> str:
    x = _slot(no)
    lane_xml = "".join('<lane width="3.5"/>' for _ in range(lanes))
    return (
        f'<link no="{no}" name="conn {no}">'
        f'<fromLinkEndPt link="{from_link}" pos="90"/>'
        f'<toLinkEndPt link="{to_link}" pos="0"/>'
        f'<geometry><linkPolyPts><linkPolyPoint x="{x}" y="0"/>'
        f'<linkPolyPoint x="{x}" y="-50"/></linkPolyPts></geometry>'
        f"<lanes>{lane_xml}</lanes>"
        f"</link>"
    )


def _node(no: str, member_links: list[str]) -> str:
    slots = sorted(_slot(lid) for lid in member_links)
    left = (slots[0] if slots else 0.0) - 5.0
    right = (slots[-1] if slots else 0.0) + 5.0
    corners: list[tuple[float, float]] = [(left, -2.0), (right, -2.0)]
    for x in reversed(slots):
        corners.extend(
            [
                (x + _TOOTH_HALF_WIDTH, -1.0),
                (x + _TOOTH_HALF_WIDTH, 1.0),
                (x - _TOOTH_HALF_WIDTH, 1.0),
                (x - _TOOTH_HALF_WIDTH, -1.0),
            ]
        )
    corners.append((left, -1.0))
    points = "".join(f'<point x="{x}" y="{y}"/>' for x, y in corners)
    return f'<node no="{no}" name="node {no}"><geometry><points>{points}</points></geometry></node>'


def _signal_head(no: str, link: str, lane: int = 1, pos: float = 80.0) -> str:
    return f'<signalHead no="{no}" lane="{link} {lane}" pos="{pos}" sg="1 1"/>'


def _network(*, links: str = "", nodes: str = "", signal_heads: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<network>"
        f"<links>{links}</links>"
        f"<nodes>{nodes}</nodes>"
        f"<signalHeads>{signal_heads}</signalHeads>"
        "</network>"
    )


#: v0 / the pristine source: two links, one connector, one node, one signal.
PRISTINE_XML = _network(
    links=_link("1", lanes=2) + _link("2", lanes=1) + _connector("9", "1", "2"),
    nodes=_node("10", ["9"]),
    signal_heads=_signal_head("100", "1"),
)

#: v1: link 1 lost a lane. Exactly one structural change vs PRISTINE_XML.
V1_XML = _network(
    links=_link("1", lanes=1) + _link("2", lanes=1) + _connector("9", "1", "2"),
    nodes=_node("10", ["9"]),
    signal_heads=_signal_head("100", "1"),
)

#: v2 / working: link 3 added and the connector rewired onto it. Two more
#: structural changes on top of v1.
V2_XML = _network(
    links=_link("1", lanes=1) + _link("2", lanes=1) + _link("3", lanes=1) + _connector("9", "1", "3"),
    nodes=_node("10", ["9"]),
    signal_heads=_signal_head("100", "1"),
)

#: A different network entirely, used as "the active dataset" so a test can
#: tell the `active` token apart from the `0` token.
ACTIVE_XML = _network(links=_link("77", lanes=4), nodes=_node("70", []))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private workspaces root, with the legacy-migration source pointed at
    an empty dir so migration is a guaranteed no-op.

    BOTH constants, always — patching only ``WORKSPACES_ROOT`` would point the
    one-time migration at the developer's real ``runtime/uploads`` and MOVE
    their live dataset into a pytest tmp dir (``test_workspace_sessions.py``'s
    own warning, copied deliberately).
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
    """A private agent-sessions root on the seams THIS endpoint reads through.

    ``services.agent.sessions`` owns the constant and ``services.audit_trail``
    binds it by name at import — and ``routers/network_diff.py`` resolves every
    session through ``audit_trail.resolve_session_dir``, so those two are the
    whole surface. The per-root legacy-owner cache is dropped on the way out so
    this tmp root leaves nothing in module state.
    """
    root = tmp_path / "agent_sessions"
    root.mkdir()
    monkeypatch.setattr(agent_sessions, "SESSIONS_ROOT", root)
    monkeypatch.setattr(audit_trail, "SESSIONS_ROOT", root)
    try:
        yield root
    finally:
        agent_sessions._legacy_owners.pop(str(root), None)


@pytest.fixture()
def active_dataset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A deterministic "active dataset" so the ``active`` token never depends
    on what this dev machine happens to have uploaded."""
    path = tmp_path / "active" / "current.inpx"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ACTIVE_XML, encoding="utf-8")
    monkeypatch.setattr(network_diff_router, "get_active_inpx_path", lambda: path)
    return path


@pytest.fixture(autouse=True)
def _empty_diff_cache():
    """The router's memo table is module state that outlives a test. Cleared on
    both sides so a cached payload can never leak between tests (and so the
    cache tests below measure their own calls, not a neighbour's)."""
    network_diff_router._cache.clear()
    yield
    network_diff_router._cache.clear()


@pytest.fixture()
def client(workspace_root: Path, sessions_root: Path, active_dataset: Path) -> TestClient:
    return TestClient(main.app)


def _headers(workspace_id: str | None) -> dict[str, str]:
    return {"X-Workspace-Id": workspace_id} if workspace_id else {}


def _make_session(
    root: Path,
    session_id: str,
    *,
    versions: list[str] | None = None,
    working: str | None = None,
    source_xml: str | None = None,
    owner: str | None = None,
    source_dir: Path | None = None,
) -> Path:
    """A session dir shaped like a real ``ArtifactManager`` work dir.

    ``versions`` is the XML of ``edit_001.inpx``, ``edit_002.inpx``, … in
    order; ``working`` is ``working.inpx``. ``source_xml`` writes the session's
    frozen source .inpx OUTSIDE the work dir (where a real one lives — it is
    the workspace's dataset) and records it in the ``session_meta.json``
    sidecar, which is also what gives the session an owner.
    """
    work_dir = root / session_id
    work_dir.mkdir(parents=True, exist_ok=True)

    for index, xml in enumerate(versions or [], start=1):
        (work_dir / f"edit_{index:03d}.inpx").write_text(xml, encoding="utf-8")
    if working is not None:
        (work_dir / "working.inpx").write_text(working, encoding="utf-8")

    meta: dict[str, str] = {"session_id": session_id}
    if source_xml is not None:
        assert source_dir is not None, "source_xml needs a source_dir to live in"
        source_dir.mkdir(parents=True, exist_ok=True)
        source_path = source_dir / f"source_{session_id}.inpx"
        source_path.write_text(source_xml, encoding="utf-8")
        meta["source_inpx"] = str(source_path)
    if owner is not None:
        meta["workspace_id"] = owner
    if len(meta) > 1:
        agent_sessions.write_session_meta(work_dir, meta)
    return work_dir


def _full_session(root: Path, tmp_path: Path, session_id: str, owner: str) -> Path:
    """The standard fixture session: pristine source + two saved versions + a
    working copy that matches v2."""
    return _make_session(
        root,
        session_id,
        versions=[V1_XML, V2_XML],
        working=V2_XML,
        source_xml=PRISTINE_XML,
        owner=owner,
        source_dir=tmp_path / "sources",
    )


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_two_saved_versions_report_exactly_the_changes_between_them(
    client: TestClient, sessions_root: Path, tmp_path: Path
):
    """v1 -> v2 adds link 3 and rewires connector 9 onto it, and changes
    nothing else. The endpoint must report those two and no phantoms."""
    owner = workspaces.default_workspace_id()
    _full_session(sessions_root, tmp_path, "aaaaaaaaaaaa", owner)

    resp = client.get("/api/network/diff", params={"session_id": "aaaaaaaaaaaa", "base": 1, "target": 2})
    assert resp.status_code == 200
    body = resp.json()

    diff = body["diff"]
    assert diff["refused"] is False
    assert diff["ok"] is True
    assert diff["has_changes"] is True
    assert diff["links"]["added"] == ["3"]
    assert diff["links"]["removed"] == []
    assert diff["links"]["lane_count_changed"] == []
    assert diff["connectors"]["added"] == [] and diff["connectors"]["removed"] == []
    assert [r["connector_id"] for r in diff["connectors"]["rewired"]] == ["9"]
    assert diff["connectors"]["rewired"][0]["changed_endpoints"] == ["to"]
    assert diff["connectors"]["rewired"][0]["base_to_link"] == "2"
    assert diff["connectors"]["rewired"][0]["target_to_link"] == "3"
    # 3 = the added link + the connector rewire + node 10, whose movement set
    # is defined BY that connector and so changes with it. `change_count` sums
    # links + connectors + reclassifications + changed nodes
    # (`StructuralDiff.change_count`), and the node row is a separate finding a
    # reviewer reads separately, not a double count of the rewire.
    assert diff["change_count"] == 3
    node_10 = next(n for n in diff["node_movements"] if n["node_id"] == "10")
    assert node_10["changed"] is True
    assert [r["connector_id"] for r in node_10["rewired"]] == ["9"]

    assert body["base"]["token"] == "1" and body["base"]["kind"] == "version"
    assert body["base"]["filename"] == "edit_001.inpx"
    assert body["target"]["token"] == "2" and body["target"]["filename"] == "edit_002.inpx"
    assert body["identical_sources"] is False


def test_default_tokens_compare_the_pristine_source_against_the_working_copy(
    client: TestClient, sessions_root: Path, tmp_path: Path
):
    """The default pair is the one available for EVERY session that has ever
    cloned — v0 vs working — and it must accumulate both edits."""
    owner = workspaces.default_workspace_id()
    _full_session(sessions_root, tmp_path, "aaaaaaaaaaaa", owner)

    resp = client.get("/api/network/diff", params={"session_id": "aaaaaaaaaaaa"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["base"]["token"] == "0"
    assert body["base"]["kind"] == "pristine"
    assert body["base"]["version"] == 0
    assert body["base"]["resolved_from"] == "session_meta"
    assert body["target"]["token"] == "working"
    assert body["target"]["filename"] == "working.inpx"

    diff = body["diff"]
    # The lane loss from v1 AND the link+rewire from v2.
    assert [c["link_id"] for c in diff["links"]["lane_count_changed"]] == ["1"]
    assert diff["links"]["lane_count_changed"][0]["base_lanes"] == 2
    assert diff["links"]["lane_count_changed"][0]["target_lanes"] == 1
    assert diff["links"]["lane_count_changed"][0]["delta"] == -1
    assert diff["links"]["added"] == ["3"]
    assert [r["connector_id"] for r in diff["connectors"]["rewired"]] == ["9"]
    # lane change + added link + connector rewire + node 10's movement change.
    assert diff["change_count"] == 4


def test_available_lists_the_snapshots_a_picker_may_offer(
    client: TestClient, sessions_root: Path, tmp_path: Path
):
    owner = workspaces.default_workspace_id()
    _full_session(sessions_root, tmp_path, "aaaaaaaaaaaa", owner)

    body = client.get("/api/network/diff", params={"session_id": "aaaaaaaaaaaa"}).json()

    assert body["available"] == {"versions": [1, 2], "has_working": True, "has_pristine": True}
    assert body["session_id_short"] == "aaaaaaaa"


def test_a_session_with_no_saved_version_can_still_be_diffed(
    client: TestClient, sessions_root: Path, tmp_path: Path
):
    """1078 of the 1572 session dirs on this box have zero ``edit_NNN.inpx``.
    A version-pair-only surface would have nothing to offer them; v0 vs
    working does."""
    owner = workspaces.default_workspace_id()
    _make_session(
        sessions_root,
        "bbbbbbbbbbbb",
        working=V1_XML,
        source_xml=PRISTINE_XML,
        owner=owner,
        source_dir=tmp_path / "sources",
    )

    resp = client.get("/api/network/diff", params={"session_id": "bbbbbbbbbbbb"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["available"]["versions"] == []
    assert body["diff"]["change_count"] == 1
    assert [c["link_id"] for c in body["diff"]["links"]["lane_count_changed"]] == ["1"]


def test_the_active_token_reads_the_workspace_dataset_not_the_session_source(
    client: TestClient, sessions_root: Path, tmp_path: Path, active_dataset: Path
):
    """``0`` and ``active`` are different files the moment a session's frozen
    source is not the currently active dataset — which is exactly the case a
    mid-session dataset swap produces."""
    owner = workspaces.default_workspace_id()
    _full_session(sessions_root, tmp_path, "aaaaaaaaaaaa", owner)

    body = client.get(
        "/api/network/diff",
        params={"session_id": "aaaaaaaaaaaa", "base": "active", "target": "working"},
    ).json()

    assert body["base"]["kind"] == "active"
    assert body["base"]["filename"] == active_dataset.name
    assert body["identical_sources"] is False
    # ACTIVE_XML shares no link id with the session's networks, so every link
    # on each side reads as added/removed. That is the honest answer, and it is
    # visibly different from the v0 comparison above.
    assert body["diff"]["links"]["removed"] == ["77"]
    assert sorted(body["diff"]["links"]["added"]) == ["1", "2", "3"]


def test_a_sidecar_less_session_says_its_v0_fell_back_to_the_active_dataset(
    client: TestClient, sessions_root: Path, active_dataset: Path
):
    """460 sessions on this box have saved versions but no sidecar. Their v0
    resolves through the same active-dataset fallback
    ``tools_edit._session_pristine_source`` uses — so ``0`` and ``active``
    collapse onto one file, and the payload says so rather than offering two
    pickers that secretly mean one thing."""
    _make_session(sessions_root, "cccccccccccc", versions=[V1_XML], working=V1_XML)

    body = client.get(
        "/api/network/diff", params={"session_id": "cccccccccccc", "base": "0", "target": "active"}
    ).json()

    assert body["base"]["resolved_from"] == "active_dataset_fallback"
    assert body["identical_sources"] is True
    assert body["diff"]["change_count"] == 0


# ---------------------------------------------------------------------------
# Empty / edge states
# ---------------------------------------------------------------------------


def test_the_same_token_on_both_sides_is_an_explicit_no_difference(
    client: TestClient, sessions_root: Path, tmp_path: Path
):
    """"차이 없음" has to be a stated answer, not an empty page. The diff runs
    for real (``ok``, every section present) and reports zero changes, and
    ``identical_sources`` says why."""
    owner = workspaces.default_workspace_id()
    _full_session(sessions_root, tmp_path, "aaaaaaaaaaaa", owner)

    resp = client.get(
        "/api/network/diff", params={"session_id": "aaaaaaaaaaaa", "base": 2, "target": 2}
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["identical_sources"] is True
    diff = body["diff"]
    assert diff["refused"] is False
    assert diff["ok"] is True
    assert diff["has_changes"] is False
    assert diff["change_count"] == 0
    # An empty diff is NOT a refusal: every section is a real (empty)
    # container, never None -- the distinction genesis/diff.py exists to keep.
    assert diff["links"] is not None and diff["connectors"] is not None
    assert diff["node_movements"] is not None and diff["signal_coverage"] is not None


def test_a_version_that_was_never_saved_is_404_with_its_own_reason(
    client: TestClient, sessions_root: Path, tmp_path: Path
):
    owner = workspaces.default_workspace_id()
    _full_session(sessions_root, tmp_path, "aaaaaaaaaaaa", owner)

    resp = client.get(
        "/api/network/diff", params={"session_id": "aaaaaaaaaaaa", "base": 1, "target": 9}
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No matching version for this session."


def test_a_session_with_no_working_copy_says_so_rather_than_404ing_the_session(
    client: TestClient, sessions_root: Path, tmp_path: Path
):
    owner = workspaces.default_workspace_id()
    _make_session(
        sessions_root,
        "dddddddddddd",
        versions=[V1_XML],
        source_xml=PRISTINE_XML,
        owner=owner,
        source_dir=tmp_path / "sources",
    )

    resp = client.get("/api/network/diff", params={"session_id": "dddddddddddd", "target": "working"})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "This session has no working copy yet."

    # ...and the session itself is plainly still reachable, so the 404 above is
    # about the snapshot and not about ownership.
    ok = client.get("/api/network/diff", params={"session_id": "dddddddddddd", "base": 0, "target": 1})
    assert ok.status_code == 200


@pytest.mark.parametrize("token", ["latest", "-1", "0.5", "edit_001", "working.inpx", "", "1000"])
def test_a_token_outside_the_space_is_a_422_not_a_404(
    client: TestClient, sessions_root: Path, tmp_path: Path, token: str
):
    """"this client is broken" and "pick a different version" are different
    answers, and a UI branches on them differently."""
    owner = workspaces.default_workspace_id()
    _full_session(sessions_root, tmp_path, "aaaaaaaaaaaa", owner)

    resp = client.get(
        "/api/network/diff", params={"session_id": "aaaaaaaaaaaa", "base": token, "target": "working"}
    )
    assert resp.status_code == 422


def test_an_unparseable_snapshot_refuses_rather_than_reporting_an_empty_diff(
    client: TestClient, sessions_root: Path, tmp_path: Path
):
    """``genesis/diff.py``'s central promise, carried through the wire format:
    a refusal has every section ``None``, so no consumer can read "we could not
    compare these" as "nothing changed"."""
    owner = workspaces.default_workspace_id()
    work_dir = _full_session(sessions_root, tmp_path, "aaaaaaaaaaaa", owner)
    (work_dir / "edit_002.inpx").write_text("<network><links><link no=", encoding="utf-8")

    resp = client.get(
        "/api/network/diff", params={"session_id": "aaaaaaaaaaaa", "base": 1, "target": 2}
    )
    assert resp.status_code == 200
    diff = resp.json()["diff"]

    assert diff["refused"] is True
    assert diff["ok"] is False
    assert diff["links"] is None and diff["connectors"] is None
    assert diff["node_movements"] is None and diff["signal_coverage"] is None
    assert "network_unparseable" in [f["code"] for f in diff["findings"]]


# ---------------------------------------------------------------------------
# Position — the refusal is part of the payload, and stays a count
# ---------------------------------------------------------------------------


def test_position_is_reported_as_a_count_and_a_stated_uncertainty_band(
    client: TestClient, sessions_root: Path, tmp_path: Path
):
    """The view is required to render the position section exactly as the
    payload states it. This pins what the payload actually promises: a flag
    that is always false, a COUNT of links whose polyline differs, and the
    band + basis behind the refusal — and no distance anywhere."""
    owner = workspaces.default_workspace_id()
    # A working copy identical to v2 in every structural respect but with link
    # 2's polyline moved: the one thing the diff counts but never measures.
    moved = _network(
        links=_link("1", lanes=1) + _link("2", lanes=1, y_tail=-140.0) + _link("3", lanes=1)
        + _connector("9", "1", "3"),
        nodes=_node("10", ["9"]),
        signal_heads=_signal_head("100", "1"),
    )
    _make_session(
        sessions_root,
        "aaaaaaaaaaaa",
        versions=[V2_XML],
        working=moved,
        source_xml=PRISTINE_XML,
        owner=owner,
        source_dir=tmp_path / "sources",
    )

    body = client.get(
        "/api/network/diff", params={"session_id": "aaaaaaaaaaaa", "base": 1, "target": "working"}
    ).json()
    diff = body["diff"]

    position = diff["position"]
    assert position["reported"] is False
    assert position["geometry_differs_count"] == 1
    assert position["max_residual_m"] == 14.0
    assert position["residual_lane_widths"] == 4.0
    assert position["scale_term"] == 1.1782
    assert "geojson.py" in position["basis"]

    # The moved link is NOT a structural change, and there is no delta field
    # anywhere that could be rendered as one.
    assert diff["change_count"] == 0
    assert set(position) == {
        "reported",
        "max_residual_m",
        "residual_lane_widths",
        "scale_term",
        "basis",
        "note",
        "geometry_differs_count",
    }

    codes = [f["code"] for f in diff["findings"]]
    assert "topology_only_scope" in codes
    assert "geometry_differs_not_measured" in codes
    warn = next(f for f in diff["findings"] if f["code"] == "geometry_differs_not_measured")
    assert warn["severity"] == "warn"
    assert warn["context"]["links"] == 1


def test_findings_carry_the_i18n_hook_even_though_diff_py_leaves_it_empty(
    client: TestClient, sessions_root: Path, tmp_path: Path
):
    """DISCLOSURE, pinned so it cannot rot silently: ``genesis/diff.py`` never
    populates ``DiffFinding.messages`` — every finding it emits has ``{}`` and
    English-only ``message`` prose (``benchmark.py``'s ``_finding_text``
    documents the same gap). The field is on the wire regardless, so the
    ko-primary view localises by ``code`` through ``t()`` and falls back to
    ``message``; if diff.py ever starts filling ``messages`` the view prefers
    it with no change here."""
    owner = workspaces.default_workspace_id()
    _full_session(sessions_root, tmp_path, "aaaaaaaaaaaa", owner)

    findings = client.get(
        "/api/network/diff", params={"session_id": "aaaaaaaaaaaa"}
    ).json()["diff"]["findings"]

    assert findings, "the scope finding is always emitted"
    for finding in findings:
        assert "messages" in finding
        assert finding["messages"] == {}
        assert finding["message"]
        assert finding["code"]


# ---------------------------------------------------------------------------
# Ownership — a foreign session is indistinguishable from a missing one
# ---------------------------------------------------------------------------


def test_a_foreign_session_is_byte_identical_to_a_session_that_never_existed(
    client: TestClient, sessions_root: Path, tmp_path: Path
):
    """The workspace boundary, at this endpoint. Anything less than
    byte-identical (body AND headers) lets one project enumerate another's
    session ids — the exact leak unit 1.4 closed everywhere else."""
    owner = workspaces.default_workspace_id()
    intruder = client.post("/api/workspaces", json={"name": "intruder"}).json()["id"]
    _full_session(sessions_root, tmp_path, "0123456789ab", owner)

    params = {"session_id": "0123456789ab", "base": 1, "target": 2}
    denied = client.get("/api/network/diff", params=params, headers=_headers(intruder))
    ghost = client.get(
        "/api/network/diff",
        params={**params, "session_id": GHOST_SESSION_ID},
        headers=_headers(intruder),
    )

    assert denied.status_code == 404
    assert denied.status_code == ghost.status_code
    assert denied.content == ghost.content
    assert dict(denied.headers) == dict(ghost.headers)

    # Discrimination: the SAME request from the owning workspace succeeds, so
    # the 404 is the ownership branch and not a blanket refusal.
    allowed = client.get("/api/network/diff", params=params, headers=_headers(owner))
    assert allowed.status_code == 200


def test_a_foreign_session_is_refused_before_any_version_is_resolved(
    client: TestClient, sessions_root: Path, tmp_path: Path
):
    """A version-shaped oracle would be just as good as a session-shaped one:
    if a bad version on a foreign session answered differently from a bad
    version on a ghost, the intruder could still probe. Ownership is checked
    first, so both are the plain session 404."""
    owner = workspaces.default_workspace_id()
    intruder = client.post("/api/workspaces", json={"name": "intruder"}).json()["id"]
    _full_session(sessions_root, tmp_path, "0123456789ab", owner)

    denied = client.get(
        "/api/network/diff",
        params={"session_id": "0123456789ab", "base": 1, "target": 99},
        headers=_headers(intruder),
    )
    assert denied.status_code == 404
    assert denied.json()["detail"] == "No matching session."


def test_a_sidecar_less_session_created_after_the_upgrade_is_denied_to_everyone(
    client: TestClient, sessions_root: Path
):
    """The fail-closed half of ``session_workspace_matches``, at this endpoint:
    "no readable owner" means nobody, including the default workspace."""
    default_id = workspaces.default_workspace_id()
    ledger = agent_sessions.backfill_legacy_session_owners(default_id, sessions_root)
    assert ledger is not None and ledger["session_ids"] == []

    _make_session(sessions_root, "0011223344ff", versions=[V1_XML], working=V2_XML)

    resp = client.get(
        "/api/network/diff", params={"session_id": "0011223344ff", "base": 1, "target": "working"}
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No matching session."


@pytest.mark.parametrize(
    "session_id", ["../../etc", "AAAAAAAAAAAA", "short", "0123456789abcdef" * 4, "12345678/../x"]
)
def test_a_session_id_outside_the_hex_shape_never_reaches_the_filesystem(
    client: TestClient, sessions_root: Path, session_id: str
):
    """Hex-only and length-capped, then re-checked with ``relative_to`` — the
    same defense in depth ``agent_downloads``/``audit_trail`` apply. Every
    rejection is the SAME opaque 404."""
    resp = client.get("/api/network/diff", params={"session_id": session_id})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "No matching session."


def test_the_workspace_header_selects_which_project_may_read_the_diff(
    client: TestClient, sessions_root: Path, tmp_path: Path
):
    """Header discipline, both directions: the owner named in the sidecar gets
    the diff whether it asks by header or by being the bound default, and a
    second real workspace never does."""
    default_id = workspaces.default_workspace_id()
    other_id = client.post("/api/workspaces", json={"name": "other"}).json()["id"]
    _full_session(sessions_root, tmp_path, "0123456789ab", default_id)

    params = {"session_id": "0123456789ab", "base": 1, "target": 2}
    assert client.get("/api/network/diff", params=params).status_code == 200
    assert client.get("/api/network/diff", params=params, headers=_headers(default_id)).status_code == 200
    assert client.get("/api/network/diff", params=params, headers=_headers(other_id)).status_code == 404

    # The same session, re-owned by `other`, flips exactly which header works.
    agent_sessions.write_session_meta(
        sessions_root / "0123456789ab",
        {"session_id": "0123456789ab", "workspace_id": other_id},
    )
    assert client.get("/api/network/diff", params=params, headers=_headers(other_id)).status_code == 200
    assert client.get("/api/network/diff", params=params, headers=_headers(default_id)).status_code == 404


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def test_a_repeated_view_does_not_re_parse_the_same_two_files(
    client: TestClient, sessions_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    owner = workspaces.default_workspace_id()
    _full_session(sessions_root, tmp_path, "aaaaaaaaaaaa", owner)

    calls: list[tuple[str, str]] = []
    real = network_diff_router.diff_network_files

    def counting(base_path, target_path, **kwargs):
        calls.append((str(base_path), str(target_path)))
        return real(base_path, target_path, **kwargs)

    monkeypatch.setattr(network_diff_router, "diff_network_files", counting)

    params = {"session_id": "aaaaaaaaaaaa", "base": 1, "target": 2}
    first = client.get("/api/network/diff", params=params).json()
    second = client.get("/api/network/diff", params=params).json()

    assert len(calls) == 1, "the second view re-parsed both .inpx files"
    assert first["diff"] == second["diff"]


def test_a_different_pair_is_not_served_from_another_pairs_cache_entry(
    client: TestClient, sessions_root: Path, tmp_path: Path
):
    """The key carries both paths, so two pairs sharing one side cannot
    collide — the failure ``routers/network.py``'s own cache comment warns
    about, in its two-file form."""
    owner = workspaces.default_workspace_id()
    _full_session(sessions_root, tmp_path, "aaaaaaaaaaaa", owner)

    one_two = client.get(
        "/api/network/diff", params={"session_id": "aaaaaaaaaaaa", "base": 1, "target": 2}
    ).json()["diff"]
    zero_two = client.get(
        "/api/network/diff", params={"session_id": "aaaaaaaaaaaa", "base": 0, "target": 2}
    ).json()["diff"]

    assert one_two["change_count"] == 3
    assert zero_two["change_count"] == 4
    assert one_two["links"]["lane_count_changed"] == []
    assert [c["link_id"] for c in zero_two["links"]["lane_count_changed"]] == ["1"]


def test_a_rewritten_working_copy_recomputes_instead_of_serving_a_stale_diff(
    client: TestClient, sessions_root: Path, tmp_path: Path
):
    """The exact hazard a path-only cache would have: an agent turn rewrites
    ``working.inpx`` in place, and the view must not keep showing the previous
    turn's answer. The mtime is bumped explicitly because a fast filesystem can
    write twice within one mtime tick — the same guard
    ``ArtifactManager._refresh_shadow_model`` applies for the same reason."""
    import os

    owner = workspaces.default_workspace_id()
    work_dir = _full_session(sessions_root, tmp_path, "aaaaaaaaaaaa", owner)
    working = work_dir / "working.inpx"

    params = {"session_id": "aaaaaaaaaaaa", "base": 0, "target": "working"}
    before = client.get("/api/network/diff", params=params).json()
    assert before["diff"]["change_count"] == 4

    stale_mtime = working.stat().st_mtime
    working.write_text(V1_XML, encoding="utf-8")
    bumped = stale_mtime + 2.0
    os.utime(working, (bumped, bumped))

    after = client.get("/api/network/diff", params=params).json()
    assert after["diff"]["change_count"] == 1
    assert after["diff"]["links"]["added"] == []


# ---------------------------------------------------------------------------
# Read-only
# ---------------------------------------------------------------------------


def test_viewing_a_diff_never_writes_into_the_session_directory(
    client: TestClient, sessions_root: Path, tmp_path: Path
):
    """The same promise ``agent_downloads``/``agent_audit`` make and pin: a
    read surface may not mutate the session it reads. Bytes AND names."""
    owner = workspaces.default_workspace_id()
    work_dir = _full_session(sessions_root, tmp_path, "aaaaaaaaaaaa", owner)
    before = {path.name: path.read_bytes() for path in work_dir.iterdir()}

    for params in (
        {"session_id": "aaaaaaaaaaaa", "base": 0, "target": "working"},
        {"session_id": "aaaaaaaaaaaa", "base": 1, "target": 2},
        {"session_id": "aaaaaaaaaaaa", "base": "active", "target": "working"},
        {"session_id": "aaaaaaaaaaaa", "base": 1, "target": 99},
    ):
        client.get("/api/network/diff", params=params)

    assert {path.name: path.read_bytes() for path in work_dir.iterdir()} == before

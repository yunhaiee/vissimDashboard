"""P2 (edited-network view + refresh): GET /api/network?session_id=<id>.

Covers routers/network.py's session-scoped "working copy" overlay: when a
client passes the CURRENT chat session's id, the endpoint should show that
session's in-progress edits (runtime/agent_sessions/<session_id>/working.inpx)
instead of the active uploaded/default dataset -- see
docs/phase2/DECISIONS.md's P2 entry for why this must be explicit rather than
"most recently modified working copy on disk" (runtime/agent_sessions/ holds
dozens of stale directories from old eval runs).
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from routers import network as network_router
from services.datasets import get_active_inpx_path

INJECTED_LINK_ID = "999999"
INJECTED_LINK_NAME = "P2 test injected link"


@pytest.fixture()
def client():
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _clear_network_cache():
    # The router's mtime-keyed cache is a module-level dict -- clear it
    # before and after every test so a cache hit from one test can never
    # leak a stale "source" value into another.
    network_router._cache.clear()
    yield
    network_router._cache.clear()


@pytest.fixture()
def isolated_sessions_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point routers.network's SESSIONS_ROOT at a fresh tmp dir instead of the
    real runtime/agent_sessions/ -- which on this dev machine holds ~60 stale
    session directories from old eval runs that must never be mistaken for
    "the current session"."""
    root = tmp_path / "agent_sessions"
    root.mkdir()
    monkeypatch.setattr(network_router, "SESSIONS_ROOT", root)
    return root


def _write_working_copy_with_extra_link(session_dir: Path) -> None:
    """Clone the active dataset's .inpx into ``session_dir/working.inpx``
    with one extra <link> appended, so the parsed network differs from the
    active view in a way a test can assert on directly."""
    active_inpx = get_active_inpx_path()
    tree = ET.parse(active_inpx)
    root = tree.getroot()
    links_el = root.find(".//links")
    assert links_el is not None, "active demo dataset is expected to carry <links>"

    template_link = links_el.find("link")
    assert template_link is not None
    injected = copy.deepcopy(template_link)
    injected.set("no", INJECTED_LINK_ID)
    injected.set("name", INJECTED_LINK_NAME)
    links_el.append(injected)

    session_dir.mkdir(parents=True, exist_ok=True)
    tree.write(session_dir / "working.inpx", encoding="utf-8", xml_declaration=True)


def test_no_session_id_param_uses_active_view(client: TestClient):
    response = client.get("/api/network")
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == {"view": "active"}


def test_valid_session_id_with_working_copy_overlays_edited_network(
    client: TestClient, isolated_sessions_root: Path
):
    session_id = "0123456789abcdef"
    _write_working_copy_with_extra_link(isolated_sessions_root / session_id)

    response = client.get("/api/network", params={"session_id": session_id})
    assert response.status_code == 200
    payload = response.json()

    assert payload["source"] == {"view": "working", "session_id": session_id}
    link_ids = {link["id"] for link in payload["links"]}
    assert INJECTED_LINK_ID in link_ids, "expected the working copy's injected link to appear"

    injected_link = next(link for link in payload["links"] if link["id"] == INJECTED_LINK_ID)
    assert injected_link["name"] == INJECTED_LINK_NAME

    # And the active (no session_id) view must NOT carry the injected link --
    # the uploaded/default dataset on disk was never touched.
    active_response = client.get("/api/network")
    assert active_response.status_code == 200
    active_payload = active_response.json()
    assert active_payload["source"] == {"view": "active"}
    assert INJECTED_LINK_ID not in {link["id"] for link in active_payload["links"]}


def test_nonexistent_session_id_falls_back_to_active(client: TestClient, isolated_sessions_root: Path):
    # A well-formed, hex session id, but no directory for it was ever created.
    response = client.get("/api/network", params={"session_id": "abcdef0123456789"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == {"view": "active"}


def test_session_id_with_no_working_inpx_yet_falls_back_to_active(
    client: TestClient, isolated_sessions_root: Path
):
    # A real session directory (e.g. one that has only asked read-only
    # questions so far) with no working.inpx snapshot yet.
    session_id = "abcdef0123456789"
    (isolated_sessions_root / session_id).mkdir()

    response = client.get("/api/network", params={"session_id": session_id})
    assert response.status_code == 200
    assert response.json()["source"] == {"view": "active"}


@pytest.mark.parametrize(
    "malicious_session_id",
    [
        "../uploads",
        "..",
        "../../uploads/current",
        "abc/../../secrets",
        "/etc/passwd",
        "a" * 33,  # one char over the 32-char cap
        "not-hex-chars!",
        "",
    ],
)
def test_malicious_or_malformed_session_id_falls_back_to_active_never_500(
    client: TestClient, isolated_sessions_root: Path, malicious_session_id: str
):
    response = client.get("/api/network", params={"session_id": malicious_session_id})
    assert response.status_code == 200
    assert response.json()["source"] == {"view": "active"}


@pytest.mark.parametrize(
    "malicious_session_id",
    [
        "../uploads",
        "..",
        "../../uploads/current",
        "abc/../../secrets",
        "/etc/passwd",
        "a" * 33,
        "not-hex-chars!",
        "",
        None,
    ],
)
def test_resolve_working_inpx_never_escapes_sessions_root(
    isolated_sessions_root: Path, malicious_session_id: str | None
):
    # Direct unit check of the resolver itself: every malicious/malformed
    # input must resolve to None (never raise, never construct a path that
    # escapes SESSIONS_ROOT).
    resolved = network_router._resolve_working_inpx(malicious_session_id)
    assert resolved is None


def test_resolve_working_inpx_accepts_a_real_working_copy(isolated_sessions_root: Path):
    session_id = "0123456789abcdef"
    _write_working_copy_with_extra_link(isolated_sessions_root / session_id)

    resolved = network_router._resolve_working_inpx(session_id)
    assert resolved is not None
    resolved_root = isolated_sessions_root.resolve()
    assert resolved.resolve().is_relative_to(resolved_root)
    assert resolved.name == "working.inpx"

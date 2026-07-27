"""Tests for P8 (edited-model download): GET /api/agent/downloads (list) and
GET /api/agent/downloads/{session_id}/{version} (download), backend/routers/
agent_downloads.py.

Every test builds its OWN fake ``runtime/agent_sessions``-shaped tmp_path and
monkeypatches ``routers.agent_downloads.SESSIONS_ROOT`` to point at it — the
real ``runtime/agent_sessions`` on a dev box accumulates hundreds of stale
directories across every eval run and manual session ever started (see that
module's own docstring), so nothing here may depend on or touch it. Also
monkeypatches ``routers.agent_downloads.get_active_inpx_path`` (the same
"active dataset" resolver ``services.datasets`` exposes) so the download
filename's dataset stem is deterministic and never depends on this dev
machine's actual uploaded/default dataset.
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from routers import agent_downloads

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"


@pytest.fixture()
def client() -> TestClient:
    return TestClient(main.app)


@pytest.fixture(autouse=True)
def _isolated_sessions_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "agent_sessions"
    root.mkdir()
    monkeypatch.setattr(agent_downloads, "SESSIONS_ROOT", root)
    monkeypatch.setattr(agent_downloads, "get_active_inpx_path", lambda: DEMO_INPX)
    return root


def _make_session(
    root: Path,
    session_id: str,
    *,
    versions: int = 0,
    siblings: dict[str, bytes] | None = None,
    working_only: bool = False,
) -> Path:
    """Build a fake session dir shaped like a real ArtifactManager work dir:
    ``working.inpx`` plus ``edit_NNN.inpx`` for ``versions`` in 1..N, each a
    distinguishable byte variant of DEMO_INPX so tests can tell which
    version's bytes actually got streamed back."""
    session_dir = root / session_id
    session_dir.mkdir()
    base = DEMO_INPX.read_bytes()

    if versions:
        for n in range(1, versions + 1):
            (session_dir / f"edit_{n:03d}.inpx").write_bytes(base + f"<!-- v{n} -->".encode())
        (session_dir / "working.inpx").write_bytes(base + f"<!-- v{versions} -->".encode())
    elif working_only:
        (session_dir / "working.inpx").write_bytes(base)

    for name, content in (siblings or {}).items():
        (session_dir / name).write_bytes(content)

    return session_dir


# ---------------------------------------------------------------------------
# List endpoint
# ---------------------------------------------------------------------------


def test_list_without_session_id_only_includes_sessions_with_saved_edits(client, _isolated_sessions_root):
    _make_session(_isolated_sessions_root, "aaaaaaaaaaaa", versions=2)
    _make_session(_isolated_sessions_root, "bbbbbbbbbbbb", working_only=True)  # never edited

    resp = client.get("/api/agent/downloads")
    assert resp.status_code == 200
    body = resp.json()

    session_ids = {entry["session_id"] for entry in body}
    assert session_ids == {"aaaaaaaaaaaa"}


def test_list_without_session_id_reports_latest_version_only(client, _isolated_sessions_root):
    _make_session(_isolated_sessions_root, "aaaaaaaaaaaa", versions=3)

    resp = client.get("/api/agent/downloads")
    assert resp.status_code == 200
    body = resp.json()

    assert len(body) == 1
    entry = body[0]
    assert entry["version"] == 3
    assert entry["is_latest"] is True
    assert entry["filename"] == "edit_003.inpx"
    assert entry["session_id_short"] == "aaaaaaaa"


def test_list_with_session_id_returns_full_retained_history_newest_first(client, _isolated_sessions_root):
    _make_session(_isolated_sessions_root, "aaaaaaaaaaaa", versions=3)

    resp = client.get("/api/agent/downloads", params={"session_id": "aaaaaaaaaaaa"})
    assert resp.status_code == 200
    body = resp.json()

    assert [entry["version"] for entry in body] == [3, 2, 1]
    assert [entry["is_latest"] for entry in body] == [True, False, False]
    assert [entry["filename"] for entry in body] == ["edit_003.inpx", "edit_002.inpx", "edit_001.inpx"]


def test_list_with_session_id_falls_back_to_unsaved_working_copy(client, _isolated_sessions_root):
    _make_session(_isolated_sessions_root, "cccccccccccc", working_only=True)

    resp = client.get("/api/agent/downloads", params={"session_id": "cccccccccccc"})
    assert resp.status_code == 200
    body = resp.json()

    assert len(body) == 1
    assert body[0]["version"] is None
    assert body[0]["filename"] == "working.inpx"


def test_list_entries_report_the_active_dataset_filename(client, _isolated_sessions_root):
    _make_session(_isolated_sessions_root, "aaaaaaaaaaaa", versions=1)

    resp = client.get("/api/agent/downloads")
    assert resp.json()[0]["dataset_filename"] == "practice.inpx"


def test_list_reports_has_siblings(client, _isolated_sessions_root):
    _make_session(
        _isolated_sessions_root,
        "aaaaaaaaaaaa",
        versions=1,
        siblings={"practice.layx": b"<layout/>"},
    )
    _make_session(_isolated_sessions_root, "dddddddddddd", versions=1)

    resp = client.get("/api/agent/downloads")
    by_id = {entry["session_id"]: entry for entry in resp.json()}
    assert by_id["aaaaaaaaaaaa"]["has_siblings"] is True
    assert by_id["dddddddddddd"]["has_siblings"] is False


@pytest.mark.parametrize("session_id", ["", "not-hex!!", "../../etc", "a" * 3, "a" * 200])
def test_list_with_malformed_or_unknown_session_id_degrades_to_empty_list(client, session_id):
    resp = client.get("/api/agent/downloads", params={"session_id": session_id})
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_with_unknown_but_well_formed_session_id_is_empty(client, _isolated_sessions_root):
    resp = client.get("/api/agent/downloads", params={"session_id": "0123456789ab"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_never_mutates_the_session_directory(client, _isolated_sessions_root):
    session_dir = _make_session(_isolated_sessions_root, "aaaaaaaaaaaa", versions=2)
    before = {p.name: p.read_bytes() for p in session_dir.iterdir()}

    client.get("/api/agent/downloads")
    client.get("/api/agent/downloads", params={"session_id": "aaaaaaaaaaaa"})

    after = {p.name: p.read_bytes() for p in session_dir.iterdir()}
    assert before == after


# ---------------------------------------------------------------------------
# Download endpoint
# ---------------------------------------------------------------------------


def test_download_bare_inpx_when_no_siblings(client, _isolated_sessions_root):
    _make_session(_isolated_sessions_root, "aaaaaaaaaaaa", versions=1)

    resp = client.get("/api/agent/downloads/aaaaaaaaaaaa/1")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/octet-stream"
    assert b"<!-- v1 -->" in resp.content
    disposition = resp.headers["content-disposition"]
    assert disposition.endswith('.inpx"') or ".inpx" in disposition
    assert "practice_edited_aaaaaaaa_001" in disposition


def test_download_zip_bundles_inpx_and_siblings(client, _isolated_sessions_root):
    _make_session(
        _isolated_sessions_root,
        "aaaaaaaaaaaa",
        versions=1,
        siblings={
            "practice.layx": b"<layout>a</layout>",
            "Signal control.vap": b"vap program bytes",
        },
    )

    resp = client.get("/api/agent/downloads/aaaaaaaaaaaa/1")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "practice_edited_aaaaaaaa_001.zip" in resp.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        names = set(archive.namelist())
        assert names == {"practice.inpx", "practice.layx", "Signal control.vap"}
        assert b"<!-- v1 -->" in archive.read("practice.inpx")
        assert archive.read("practice.layx") == b"<layout>a</layout>"
        assert archive.read("Signal control.vap") == b"vap program bytes"


def test_download_zip_excludes_persist_saving_leftover(client, _isolated_sessions_root):
    # Live finding while building this feature: VissimEditSession.persist()'s
    # SaveNetAs-to-tmp step leaves a same-stem "working_saving.layx" behind,
    # never renamed onto working.layx. A download must not ship that
    # confusingly-named orphan as if it were a real sibling asset.
    _make_session(
        _isolated_sessions_root,
        "aaaaaaaaaaaa",
        versions=1,
        siblings={
            "practice.layx": b"<layout>real</layout>",
            "working_saving.layx": b"<layout>leftover, must be excluded</layout>",
        },
    )

    resp = client.get("/api/agent/downloads/aaaaaaaaaaaa/1")
    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        assert "practice.layx" in archive.namelist()
        assert not any("working_saving" in name for name in archive.namelist())


def test_download_latest_resolves_highest_version(client, _isolated_sessions_root):
    _make_session(_isolated_sessions_root, "aaaaaaaaaaaa", versions=3)

    resp = client.get("/api/agent/downloads/aaaaaaaaaaaa/latest")
    assert resp.status_code == 200
    assert b"<!-- v3 -->" in resp.content
    assert "_003." in resp.headers["content-disposition"]


def test_download_latest_falls_back_to_unsaved_working_copy(client, _isolated_sessions_root):
    _make_session(_isolated_sessions_root, "cccccccccccc", working_only=True)

    resp = client.get("/api/agent/downloads/cccccccccccc/latest")
    assert resp.status_code == 200
    assert resp.content == DEMO_INPX.read_bytes()


def test_download_unknown_session_id_is_404(client, _isolated_sessions_root):
    resp = client.get("/api/agent/downloads/0123456789ab/1")
    assert resp.status_code == 404


@pytest.mark.parametrize(
    "session_id",
    [
        "../../../../etc/passwd",
        "..%2f..%2fsecrets",
        "a",
        "not-hex-at-all!",
        "0" * 200,
    ],
)
def test_download_rejects_path_traversal_and_malformed_session_ids(client, _isolated_sessions_root, session_id):
    resp = client.get(f"/api/agent/downloads/{session_id}/1")
    assert resp.status_code == 404


@pytest.mark.parametrize("version", ["0", "abc", "-1", "1.5", "999999999999999999"])
def test_download_rejects_malformed_version_tokens(client, _isolated_sessions_root, version):
    _make_session(_isolated_sessions_root, "aaaaaaaaaaaa", versions=2)

    resp = client.get(f"/api/agent/downloads/aaaaaaaaaaaa/{version}")
    assert resp.status_code == 404


def test_download_rejects_nonexistent_version_for_a_real_session(client, _isolated_sessions_root):
    _make_session(_isolated_sessions_root, "aaaaaaaaaaaa", versions=2)

    resp = client.get("/api/agent/downloads/aaaaaaaaaaaa/7")
    assert resp.status_code == 404


def test_download_never_mutates_the_session_directory(client, _isolated_sessions_root):
    session_dir = _make_session(
        _isolated_sessions_root,
        "aaaaaaaaaaaa",
        versions=2,
        siblings={"practice.layx": b"<layout/>"},
    )
    before = {p.name: p.read_bytes() for p in session_dir.iterdir()}

    client.get("/api/agent/downloads/aaaaaaaaaaaa/latest")
    client.get("/api/agent/downloads/aaaaaaaaaaaa/1")

    after = {p.name: p.read_bytes() for p in session_dir.iterdir()}
    assert before == after


# ---------------------------------------------------------------------------
# End-to-end sanity: real demo data + a fabricated session dir, list then
# download through the same TestClient, zip round-trips.
# ---------------------------------------------------------------------------


def test_end_to_end_list_then_download_round_trips_real_demo_data(client, _isolated_sessions_root):
    session_dir = _make_session(
        _isolated_sessions_root,
        "e2e0e2e0e2e0",
        versions=1,
        siblings={"practice.layx": b"<layout>e2e</layout>"},
    )
    # Make the "edited" version actually be the real demo .inpx bytes (not a
    # synthetic marker) so this exercises the real file, not just a fixture.
    (session_dir / "edit_001.inpx").write_bytes(DEMO_INPX.read_bytes())

    listing = client.get("/api/agent/downloads", params={"session_id": "e2e0e2e0e2e0"}).json()
    assert len(listing) == 1
    assert listing[0]["version"] == 1
    assert listing[0]["has_siblings"] is True

    download = client.get("/api/agent/downloads/e2e0e2e0e2e0/1")
    assert download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert archive.read("practice.inpx") == DEMO_INPX.read_bytes()
        assert archive.read("practice.layx") == b"<layout>e2e</layout>"

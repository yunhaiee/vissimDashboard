"""Phase 2 unit U10 — ``/api/observations``: upload, ingest, list, get, delete.

Synthetic workbooks only (roadmap 1.2's own fixture builders); the real client
drop never enters a test. Nothing here starts a simulation, opens COM, or
touches the active dataset.

The two properties this file exists for, beyond CRUD:

* a refused ingest answers 4xx **with the gate's findings as data** and stores
  nothing — a 400 with a sentence would make the refusal unactionable;
* the workspace boundary holds over HTTP, which is where a real cross-client
  leak would happen (``X-Workspace-Id``, the same header every other data
  route is scoped by).
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

import pytest
from conftest import admin_headers
from fastapi.testclient import TestClient

import main
from services import observations, workspaces
from tests.test_dataimport_daejeon import (
    WEDNESDAY,
    _approach_rows,
    _corrupt_a_row_past_the_header,
    _file_name,
    _write_workbook,
)

AM = {
    "name": "AM",
    "start": WEDNESDAY.isoformat(),
    "end": (WEDNESDAY + timedelta(hours=3)).isoformat(),
}


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
def client(workspace_root: Path):
    with TestClient(main.app) as test_client:
        yield test_client


def _headers(workspace_id: str | None) -> dict[str, str]:
    """Workspace scoping PLUS the suite admin's session.

    Unit P④ closed the workspace boundary: a NON-default workspace answers 403
    to any caller who is not a member or an admin, and creating one requires a
    signed-in user. This file is about the WORKSPACE boundary, not the
    membership rule, so every request here authenticates and a 404/200 below is
    still measuring what it always measured. The membership rule itself is
    tested in ``tests/test_workspace_membership.py``.
    """
    headers = dict(admin_headers())
    if workspace_id:
        headers["X-Workspace-Id"] = workspace_id
    return headers


def _workbook(root: Path, *, site: str = "가람네거리", hours: int = 3) -> Path:
    return _write_workbook(
        root,
        _approach_rows("가람방면", start=WEDNESDAY, hours=hours),
        file_name=_file_name(
            site, start=WEDNESDAY, last_start=WEDNESDAY + timedelta(hours=hours, minutes=-15)
        ),
    )


def _upload(client: TestClient, path: Path, *, workspace: str | None = None) -> str:
    """Upload one workbook the way a browser must: the name percent-encoded in
    ``?filename=``, not in ``X-Filename``.

    A real Daejeon file name is Korean, and an HTTP header value is
    byte-wise ASCII — httpx refuses to send one, and so would a browser. The
    router accepts both channels (``unquote(query or header)``, the same
    two-channel handling ``routers/datasets.py`` and ``routers/validation.py``
    already use); this is the one that works for the file names this adapter
    was built for.
    """
    response = client.post(
        f"/api/observations/upload?filename={quote(path.name)}",
        content=path.read_bytes(),
        headers=_headers(workspace),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["filename"] == path.name
    return body["token"]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_upload_then_ingest_then_read_back(client: TestClient, tmp_path: Path):
    token = _upload(client, _workbook(tmp_path))

    created = client.post(
        "/api/observations/ingest",
        json={"name": "대전 유성구 4월", "upload_tokens": [token], "periods": [AM]},
    )
    assert created.status_code == 201, created.text
    record = created.json()["set"]
    assert record["site_ids"] == ["가람네거리"]
    assert record["adapter"]["name"] == "daejeon_smart_intersection"
    assert record["adapter"]["version"]
    # Source files are NAMES only -- a set record is rendered into reports and
    # fed to a planner; a local path has no business in either.
    assert record["source_files"] == [Path(_workbook(tmp_path).name).name]
    assert record["aggregates"]["AM"]["가람네거리"]["total"] == pytest.approx(12 * 109.0)

    listing = client.get("/api/observations").json()
    assert [entry["id"] for entry in listing["sets"]] == [record["id"]]
    assert "aggregates" not in listing["sets"][0], "a listing stays bounded"
    assert listing["registry_health"] is None

    detail = client.get(f"/api/observations/{record['id']}")
    assert detail.status_code == 200
    assert detail.json()["aggregates"] == record["aggregates"]

    # The staged upload is cleaned up once it has been ingested.
    assert client.post(
        "/api/observations/ingest",
        json={"name": "다시", "upload_tokens": [token], "periods": [AM]},
    ).status_code == 404

    deleted = client.delete(f"/api/observations/{record['id']}")
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    assert client.get("/api/observations").json()["sets"] == []
    assert client.get(f"/api/observations/{record['id']}").status_code == 404
    assert client.delete(f"/api/observations/{record['id']}").status_code == 404


def test_keep_uploads_leaves_the_staged_file_for_a_second_ingest(
    client: TestClient, tmp_path: Path
):
    token = _upload(client, _workbook(tmp_path))
    first = client.post(
        "/api/observations/ingest",
        json={
            "name": "첫 번째",
            "upload_tokens": [token],
            "periods": [AM],
            "keep_uploads": True,
        },
    )
    assert first.status_code == 201
    second = client.post(
        "/api/observations/ingest",
        json={
            "name": "다른 시간대로 다시",
            "upload_tokens": [token],
            "periods": [
                {
                    "name": "AM1",
                    "start": WEDNESDAY.isoformat(),
                    "end": (WEDNESDAY + timedelta(hours=1)).isoformat(),
                }
            ],
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["set"]["aggregates"]["AM1"]["가람네거리"]["total"] == pytest.approx(
        4 * 109.0
    )


# ---------------------------------------------------------------------------
# Refusals are 4xx WITH the reason as data
# ---------------------------------------------------------------------------


def test_a_gate_refusal_is_a_422_carrying_the_gate_and_stores_nothing(
    client: TestClient, tmp_path: Path
):
    """One hour of survey is below MRWA's 3 h minimum."""
    token = _upload(client, _workbook(tmp_path, hours=1))

    response = client.post(
        "/api/observations/ingest",
        json={"name": "한 시간짜리", "upload_tokens": [token], "periods": [AM]},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["stored"] is False
    assert detail["reason_code"] == "observation_gate_refused"
    assert set(detail["message"]) == {"ko", "en", "ja"}
    assert "peak_coverage_below_minimum" in detail["gate"]["codes"]
    finding = next(
        issue for issue in detail["gate"]["issues"] if issue["code"] == "peak_coverage_below_minimum"
    )
    # The gate's own evidence, verbatim: severity, prose, subject, the authority
    # it cites, and the numbers behind the judgement.
    assert finding["severity"] == "refuse"
    assert "MRWA" in finding["citation"]
    assert finding["context"]["required_h"] == 3.0
    assert finding["subject"]

    assert client.get("/api/observations").json()["sets"] == []


def test_a_workbook_unreadable_only_during_the_full_parse_is_a_4xx_not_a_500(
    client: TestClient, tmp_path: Path
):
    """Regression: ``ingest_observation_set`` used to call ``adapter.parse``
    with no try/except at all, so a workbook that opens fine at upload time
    (``stage_workbook``'s readability check opens the workbook and closes it
    again, without ever reading a row) and sniffs fine (an adapter's
    ``sniff`` reads far less than a full parse -- this one only the first
    row) but is corrupt deeper in used to escape THIS endpoint as an
    unhandled 500 instead of the same kind of 4xx refusal every other
    unreadable-input path here already answers with.
    """
    path = _workbook(tmp_path)
    _corrupt_a_row_past_the_header(path)
    token = _upload(client, path)  # succeeds -- the upload-time check never reads a row

    response = client.post(
        "/api/observations/ingest",
        json={"name": "손상된 워크북", "upload_tokens": [token], "periods": [AM]},
    )

    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["stored"] is False
    assert detail["reason_code"] == "observation_workbook_unreadable"
    assert set(detail["message"]) == {"ko", "en", "ja"}
    # The underlying AdapterError's own text is carried, not swallowed.
    assert "could not be read as a worksheet" in detail["message"]["en"]
    assert path.name in detail["message"]["en"]

    assert client.get("/api/observations").json()["sets"] == []


def test_ingesting_with_no_period_is_a_422_naming_the_decision(
    client: TestClient, tmp_path: Path
):
    token = _upload(client, _workbook(tmp_path))
    response = client.post(
        "/api/observations/ingest",
        json={"name": "시간대 없음", "upload_tokens": [token], "periods": []},
    )
    # pydantic's own min_length refusal on the request model -- the modeller's
    # decision is required at the boundary, not defaulted behind it.
    assert response.status_code == 422


def test_an_unknown_upload_token_is_a_404(client: TestClient):
    response = client.post(
        "/api/observations/ingest",
        json={"name": "x", "upload_tokens": ["0" * 32], "periods": [AM]},
    )
    assert response.status_code == 404


@pytest.mark.parametrize("token", ["../../etc/passwd", "nothex", "a" * 31])
def test_a_malformed_upload_token_never_reaches_the_filesystem(client: TestClient, token: str):
    response = client.post(
        "/api/observations/ingest",
        json={"name": "x", "upload_tokens": [token], "periods": [AM]},
    )
    assert response.status_code == 404


def test_uploading_a_non_workbook_is_a_400_with_a_reason(client: TestClient):
    response = client.post(
        "/api/observations/upload",
        content=b"not a workbook",
        headers={"X-Filename": "notes.txt"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["reason_code"] == "observation_upload_not_a_workbook"
    assert set(detail["message"]) == {"ko", "en", "ja"}


def test_an_oversized_upload_is_refused_from_the_declared_length(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """The 413 guard is ``routers/datasets.py``'s, imported rather than copied,
    so the two endpoints cannot drift to different limits."""
    from routers import datasets as datasets_router
    from routers import observations as observations_router

    monkeypatch.setattr(datasets_router, "MAX_UPLOAD_BYTES", 16)
    monkeypatch.setattr(observations_router, "MAX_UPLOAD_BYTES", 16)

    response = client.post(
        "/api/observations/upload",
        content=b"x" * 64,
        headers={"X-Filename": "big.xlsx"},
    )
    assert response.status_code == 413


@pytest.mark.parametrize("set_id", ["../../etc/passwd", "NOTHEX", "abc", "z" * 12])
def test_a_malformed_set_id_is_a_404_not_a_path_traversal(client: TestClient, set_id: str):
    assert client.get(f"/api/observations/{set_id}").status_code == 404
    assert client.delete(f"/api/observations/{set_id}").status_code == 404


# ---------------------------------------------------------------------------
# The workspace boundary, over HTTP
# ---------------------------------------------------------------------------


def test_a_set_ingested_in_one_workspace_is_invisible_from_another(
    client: TestClient, tmp_path: Path
):
    workspace_a = client.get("/api/workspaces").json()["default"]
    workspace_b = client.post("/api/workspaces", json={"name": "B"}, headers=_headers(None)).json()["id"]

    token = _upload(client, _workbook(tmp_path), workspace=workspace_a)
    created = client.post(
        "/api/observations/ingest",
        json={"name": "A의 관측", "upload_tokens": [token], "periods": [AM]},
        headers=_headers(workspace_a),
    )
    assert created.status_code == 201, created.text
    set_id = created.json()["set"]["id"]

    assert client.get("/api/observations", headers=_headers(workspace_b)).json()["sets"] == []
    assert client.get(
        f"/api/observations/{set_id}", headers=_headers(workspace_b)
    ).status_code == 404
    assert client.delete(
        f"/api/observations/{set_id}", headers=_headers(workspace_b)
    ).status_code == 404

    # A's set is still there and untouched by B's attempt to delete it.
    assert [
        entry["id"] for entry in client.get("/api/observations", headers=_headers(workspace_a)).json()["sets"]
    ] == [set_id]


def test_a_staged_upload_is_not_visible_across_workspaces(client: TestClient, tmp_path: Path):
    """Staging lives inside the workspace too — a token minted in A cannot be
    redeemed from B, so an upload cannot be laundered across the boundary."""
    workspace_a = client.get("/api/workspaces").json()["default"]
    workspace_b = client.post("/api/workspaces", json={"name": "B"}, headers=_headers(None)).json()["id"]

    token = _upload(client, _workbook(tmp_path), workspace=workspace_a)

    assert client.post(
        "/api/observations/ingest",
        json={"name": "B가 가로채기", "upload_tokens": [token], "periods": [AM]},
        headers=_headers(workspace_b),
    ).status_code == 404


def test_a_quarantined_registry_is_disclosed_on_the_listing(client: TestClient, tmp_path: Path):
    token = _upload(client, _workbook(tmp_path))
    client.post(
        "/api/observations/ingest",
        json={"name": "곧 손상될 세트", "upload_tokens": [token], "periods": [AM]},
    )
    observations.registry_path().write_bytes(b"{ truncated")

    listing = client.get("/api/observations").json()

    assert listing["sets"] == []
    assert listing["registry_health"]["code"] == "registry_quarantined"
    assert listing["registry_health"]["lost_record_count"] == 1
    assert set(listing["registry_health"]["messages"]) == {"ko", "en", "ja"}

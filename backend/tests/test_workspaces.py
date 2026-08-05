"""Unit 1.4 — project workspaces: the access-control boundary around uploads.

Everything here is synthetic (a two-element ``<network/>`` document is a valid
enough .inpx for the upload path, which only checks that the bytes parse as
XML) and nothing requires Vissim, COM, or Windows.

The suite also closes a MEASURED zero-coverage gap: before this file, no
backend test exercised ``POST /api/datasets/upload`` at all — not the .inpx
path, not the .zip path, not the zip-slip guard, not what happens to the
previous dataset when an upload fails. The upload endpoint is the app's most
hostile surface (arbitrary bytes from a browser, straight onto disk) and it
was the least tested.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
import main
from services import datasets, simulation_runs, workspaces

INPX_A = b'<network fromMigration="A"><links/></network>'
INPX_B = b'<network fromMigration="B"><links/><nodes/></network>'


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private workspaces root for one test, with the legacy-migration
    source pointed somewhere empty so migration is a guaranteed no-op.

    Also re-binds the ContextVar: ``conftest.py``'s session fixture bound the
    SESSION-wide tmp root's default workspace id, which does not exist in this
    fresh registry. Requests get their binding from the middleware, but a test
    that calls a service function directly needs one too.
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
def client(workspace_root: Path):
    with TestClient(main.app) as test_client:
        yield test_client


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _counts_workbook(observed: int) -> bytes:
    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Counts"
    sheet.append(["period", "node_id", "node_name", "movement", "observed_volume", "model_volume"])
    sheet.append(["AM", "1", "Node One", "N-E", observed, observed])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _headers(workspace_id: str | None) -> dict[str, str]:
    return {"X-Workspace-Id": workspace_id} if workspace_id else {}


# ---------------------------------------------------------------------------
# Registry CRUD + id validation
# ---------------------------------------------------------------------------


def test_default_workspace_is_created_on_first_touch(workspace_root: Path):
    entries = workspaces.list_workspaces()

    assert len(entries) == 1
    assert entries[0]["is_default"] is True
    assert entries[0]["name"] == workspaces.DEFAULT_WORKSPACE_NAME
    assert (workspace_root / "registry.json").is_file()


def test_create_workspace_registers_and_creates_its_directory(workspace_root: Path):
    created = workspaces.create_workspace("대전 프로젝트")

    assert created["is_default"] is False
    assert workspaces.get_workspace(created["id"]) == created
    assert workspaces.workspace_dir(created["id"]).is_dir()
    # Default stays first in the listing regardless of creation order.
    assert workspaces.list_workspaces()[0]["is_default"] is True
    assert {entry["id"] for entry in workspaces.list_workspaces()} == {
        created["id"],
        workspaces.default_workspace_id(),
    }


@pytest.mark.parametrize("name", ["", "   ", "x" * (workspaces.MAX_WORKSPACE_NAME_CHARS + 1)])
def test_create_workspace_refuses_blank_and_overlong_names(workspace_root: Path, name: str):
    with pytest.raises(ValueError):
        workspaces.create_workspace(name)


@pytest.mark.parametrize(
    "workspace_id",
    [
        "..",
        "../../etc",
        "..\\..\\windows",
        "abc",  # too short
        "g" * 12,  # not hex
        "0123456789abcdef0123456789abcdef0",  # too long
        "/absolute",
        "0123456789ab/../..",
    ],
)
def test_workspace_dir_refuses_traversal_and_malformed_ids(workspace_root: Path, workspace_id: str):
    with pytest.raises(workspaces.WorkspaceNotFoundError):
        workspaces.workspace_dir(workspace_id)


def test_current_workspace_id_raises_when_nothing_is_bound(workspace_root: Path):
    """The deliberate no-silent-default rule — see services/workspaces.py."""
    token = workspaces._current_workspace_id.set("0123456789ab")
    workspaces._current_workspace_id.reset(token)

    # Simulate a bare background thread: a fresh context with nothing bound.
    import contextvars

    def _read() -> None:
        workspaces.current_workspace_id()

    fresh = contextvars.Context()
    with pytest.raises(workspaces.WorkspaceScopeError) as excinfo:
        fresh.run(_read)
    assert "bind_workspace" in str(excinfo.value)


def test_unknown_workspace_header_is_a_404_with_a_code(client: TestClient):
    response = client.get("/api/datasets/active", headers=_headers("0123456789ab"))

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "workspace_not_found"


def test_malformed_workspace_header_is_a_404(client: TestClient):
    response = client.get("/api/datasets/active", headers=_headers("../../etc"))

    assert response.status_code == 404


def test_absent_workspace_header_resolves_to_the_default(client: TestClient):
    response = client.get("/api/workspaces")

    assert response.status_code == 200
    payload = response.json()
    assert payload["current"] == payload["default"]
    assert len(payload["workspaces"]) == 1


def test_workspaces_router_create_and_current(client: TestClient):
    created = client.post("/api/workspaces", json={"name": "두 번째 프로젝트"})
    assert created.status_code == 200
    workspace_id = created.json()["id"]

    current = client.get("/api/workspaces/current", headers=_headers(workspace_id))
    assert current.status_code == 200
    assert current.json()["workspace"]["name"] == "두 번째 프로젝트"
    assert current.json()["dataset"]["workspace_id"] == workspace_id

    assert client.post("/api/workspaces", json={"name": "   "}).status_code == 422


# ---------------------------------------------------------------------------
# Upload through the router — the previously untested hostile surface
# ---------------------------------------------------------------------------


def test_upload_inpx_through_the_router(client: TestClient):
    response = client.post("/api/datasets/upload?filename=my%20model.inpx", content=INPX_A)

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] == "uploaded"
    assert payload["kind"] == "inpx"
    assert payload["filename"] == "my model.inpx"
    assert Path(payload["path"]).read_bytes() == INPX_A
    assert client.get("/api/datasets/active").json()["source"] == "uploaded"


def test_upload_zip_through_the_router_extracts_the_whole_project(client: TestClient):
    archive = _zip_bytes(
        {
            "project/net.inpx": INPX_A,
            "project/net.vissimpdb": b"pdb",
            "project/Scenarios/S1/input.txt": b"scenario",
            "project/Modifications/M1.txt": b"modification",
            "project/real_counts.xlsx": _counts_workbook(111),
        }
    )

    response = client.post("/api/datasets/upload?filename=drop.zip", content=archive)

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "zip"
    assert payload["has_project_db"] is True
    assert payload["has_scenarios"] is True
    assert payload["has_modifications"] is True
    assert payload["has_real_counts"] is True
    current = Path(payload["path"]).parent
    assert (current / "Scenarios" / "S1" / "input.txt").read_bytes() == b"scenario"
    assert (current / "Modifications" / "M1.txt").read_bytes() == b"modification"


def test_upload_zip_refuses_a_zip_slip_member_without_escaping_the_workspace(
    client: TestClient, workspace_root: Path, tmp_path: Path
):
    archive = _zip_bytes(
        {
            "net.inpx": INPX_A,
            "Scenarios/../../../escaped.txt": b"pwned",
            "Modifications/../../../../also_escaped.txt": b"pwned",
        }
    )

    response = client.post("/api/datasets/upload?filename=slip.zip", content=archive)

    assert response.status_code == 200
    current = Path(response.json()["path"]).parent
    # The traversal segments are stripped, so the members land INSIDE the
    # workspace's own Scenarios/Modifications directories -- never above them.
    assert (current / "Scenarios" / "escaped.txt").is_file()
    assert (current / "Modifications" / "also_escaped.txt").is_file()
    for stray in ("escaped.txt", "also_escaped.txt"):
        assert not (tmp_path / stray).exists()
        assert not (workspace_root / stray).exists()
        assert not (workspace_root.parent / stray).exists()


def test_upload_zip_refuses_too_many_members_and_keeps_the_previous_dataset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    assert client.post("/api/datasets/upload?filename=good.inpx", content=INPX_A).status_code == 200

    monkeypatch.setattr(datasets, "MAX_ZIP_MEMBERS", 3)
    bomb = _zip_bytes({"net.inpx": INPX_A, **{f"f{i}.txt": b"x" for i in range(5)}})

    response = client.post("/api/datasets/upload?filename=bomb.zip", content=bomb)

    assert response.status_code == 400
    assert "too many entries" in response.json()["detail"]
    assert client.get("/api/datasets/active").json()["kind"] == "inpx"
    assert Path(client.get("/api/datasets/active").json()["path"]).read_bytes() == INPX_A


def test_upload_zip_refuses_an_oversized_payload_and_keeps_the_previous_dataset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    assert client.post("/api/datasets/upload?filename=good.inpx", content=INPX_A).status_code == 200

    monkeypatch.setattr(datasets, "MAX_ZIP_TOTAL_BYTES", 64)
    bomb = _zip_bytes({"net.inpx": INPX_A, "payload.bin": b"0" * 4096})

    response = client.post("/api/datasets/upload?filename=bomb.zip", content=bomb)

    assert response.status_code == 400
    assert "too large" in response.json()["detail"]
    assert Path(client.get("/api/datasets/active").json()["path"]).read_bytes() == INPX_A


def test_upload_over_the_body_size_cap_is_a_413(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    from routers import datasets as datasets_router

    monkeypatch.setattr(datasets_router, "MAX_UPLOAD_BYTES", 16)

    response = client.post("/api/datasets/upload?filename=big.inpx", content=INPX_A * 10)

    assert response.status_code == 413


def test_a_failed_upload_leaves_the_previous_dataset_readable(client: TestClient):
    """Stage-then-promote: the previous dataset is destroyed only once the new
    one is complete on disk. The old code rmtree'd first, so a malformed
    upload left the user with NO dataset at all."""
    assert client.post("/api/datasets/upload?filename=good.inpx", content=INPX_A).status_code == 200
    before = client.get("/api/datasets/active").json()

    broken_zip = _zip_bytes({"net.inpx": b"<not-xml", "extra.txt": b"x"})
    failed = client.post("/api/datasets/upload?filename=broken.zip", content=broken_zip)

    assert failed.status_code == 400
    after = client.get("/api/datasets/active").json()
    assert after["source"] == "uploaded"
    assert after["filename"] == before["filename"]
    assert Path(after["path"]).read_bytes() == INPX_A
    # No staging leftovers.
    uploads_root = Path(after["path"]).parent.parent
    assert not [child for child in uploads_root.iterdir() if child.name.startswith("incoming-")]
    assert not [child for child in uploads_root.iterdir() if child.name.startswith("retired-")]


# ---------------------------------------------------------------------------
# Stage-then-promote failure modes (all four MEASURED in adversarial review)
# ---------------------------------------------------------------------------


def _leftovers(uploads_root: Path) -> list[str]:
    return sorted(
        child.name
        for child in uploads_root.iterdir()
        if child.name.startswith(("incoming-", "retired-"))
    )


def _uploads_root_of(client: TestClient) -> Path:
    return Path(client.get("/api/datasets/active").json()["path"]).parent.parent


def _break_renames_from(
    monkeypatch: pytest.MonkeyPatch, source_prefix: str, error: BaseException
) -> dict:
    """Make every ``Path.rename`` whose SOURCE name starts with ``source_prefix``
    raise ``error``, until the returned switch is flipped off.

    Persistent rather than "fail the Nth call", because that is what a real
    held handle is — and because the promote retries lock-class errors, a
    one-shot injection would simply be retried away. Selecting on the source
    name targets exactly one of the promote's renames: ``current`` is "retire
    the previous dataset", ``incoming-`` is "move the new one into place", and
    ``retired-`` is the restore, which must stay working.

    Never call ``monkeypatch.undo()`` to stop it: pytest hands the SAME
    monkeypatch instance to the test and to the ``workspace_root`` fixture, so
    undo() would also revert ``WORKSPACES_ROOT`` mid-test and silently move the
    assertions onto the session-wide ambient workspace.
    """
    real_rename = Path.rename
    switch = {"on": True}

    def _rename(self: Path, target):  # type: ignore[no-untyped-def]
        if switch["on"] and self.name.startswith(source_prefix):
            raise error
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", _rename)
    return switch


def _locked_error() -> PermissionError:
    """A real Windows sharing violation, shaped exactly as os.rename raises it."""
    return PermissionError(
        13,
        "The process cannot access the file because it is being used by another process",
        None,
        32,  # ERROR_SHARING_VIOLATION -- what _is_file_lock_error keys the message off
    )


def test_a_locked_current_dataset_is_a_409_not_a_500(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """MEASURED: an ordinary open handle on ``uploads/current/current.inpx``
    (this app opens it for validation, results reading and COM) makes the FIRST
    promote rename fail. That used to surface as an unhandled 500 and orphan
    ``incoming-<token>/`` forever, because the promote sat OUTSIDE the cleanup
    guard and nothing anywhere swept staging directories."""
    assert client.post("/api/datasets/upload?filename=good.inpx", content=INPX_A).status_code == 200
    uploads_root = _uploads_root_of(client)

    _break_renames_from(monkeypatch, "current", _locked_error())
    response = client.post("/api/datasets/upload?filename=next.inpx", content=INPX_B)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "still has the current model open" in detail
    assert "left unchanged" in detail
    # (a) previous dataset fully readable, (b) no leftovers.
    active = client.get("/api/datasets/active").json()
    assert active["source"] == "uploaded"
    assert active["filename"] == "good.inpx"
    assert Path(active["path"]).read_bytes() == INPX_A
    assert _leftovers(uploads_root) == []


def test_a_non_oserror_promote_failure_cannot_lose_the_dataset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """THE total-loss path. ``_promote_staged_upload`` caught only ``OSError``
    around the second rename, so any other exception type skipped the restore:
    measured ``current.inpx`` gone, the API flipping to ``source: "default"``,
    and both ``incoming-*`` and ``retired-*`` left behind. Stage-then-promote
    exists precisely to make this impossible."""
    assert client.post("/api/datasets/upload?filename=good.inpx", content=INPX_A).status_code == 200
    uploads_root = _uploads_root_of(client)

    _break_renames_from(monkeypatch, "incoming-", RuntimeError("not an OSError"))
    response = client.post("/api/datasets/upload?filename=next.inpx", content=INPX_B)

    assert response.status_code == 409
    assert "left unchanged" in response.json()["detail"]
    active = client.get("/api/datasets/active").json()
    assert active["source"] == "uploaded"
    assert active["filename"] == "good.inpx"
    assert Path(active["path"]).read_bytes() == INPX_A
    assert (Path(active["path"]).parent / "metadata.json").is_file()
    assert _leftovers(uploads_root) == []


def test_upload_sweeps_leftovers_from_an_earlier_crashed_attempt(client: TestClient):
    """Leftovers were NEVER collected: measured still present after three
    subsequent successful uploads, with no GC anywhere in backend/."""
    assert client.post("/api/datasets/upload?filename=good.inpx", content=INPX_A).status_code == 200
    uploads_root = _uploads_root_of(client)

    (uploads_root / "incoming-deadbeefcafe").mkdir()
    (uploads_root / "incoming-deadbeefcafe" / "current.inpx").write_bytes(INPX_B)
    (uploads_root / "retired-0011223344ff").mkdir()
    (uploads_root / "staging").mkdir()  # NOT a leftover -- workbook mapping lives here
    (uploads_root / "mapping_memory.json").write_text("{}", encoding="utf-8")
    assert len(_leftovers(uploads_root)) == 2

    assert client.post("/api/datasets/upload?filename=next.inpx", content=INPX_B).status_code == 200

    assert _leftovers(uploads_root) == []
    assert (uploads_root / "staging").is_dir()
    assert (uploads_root / "mapping_memory.json").is_file()
    assert (uploads_root / "current" / "current.inpx").read_bytes() == INPX_B


def test_the_sweep_never_touches_another_workspace(client: TestClient):
    other = client.post("/api/workspaces", json={"name": "other"}).json()["id"]
    assert (
        client.post("/api/datasets/upload?filename=b.inpx", content=INPX_B, headers=_headers(other)).status_code
        == 200
    )
    other_root = Path(
        client.get("/api/datasets/active", headers=_headers(other)).json()["path"]
    ).parent.parent
    (other_root / "incoming-000000000001").mkdir()

    assert client.post("/api/datasets/upload?filename=a.inpx", content=INPX_A).status_code == 200

    # The default workspace's upload swept its OWN uploads root only.
    assert _leftovers(other_root) == ["incoming-000000000001"]
    assert (other_root / "current" / "current.inpx").read_bytes() == INPX_B


def test_the_sweep_keeps_a_retired_copy_when_there_is_no_current_dataset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """The one leftover that must survive a sweep: if a promote's restore ALSO
    failed, ``retired-*`` holds the ONLY copy of the dataset (the CRITICAL log
    tells a human to rename it back). Collecting it because it looks like
    scratch would turn a recoverable state into permanent loss.

    It stops being the only copy the moment a new dataset is successfully
    installed, and only then is it collected — so leftovers still cannot
    accumulate.
    """
    assert client.post("/api/datasets/upload?filename=good.inpx", content=INPX_A).status_code == 200
    uploads_root = _uploads_root_of(client)

    # Hand-build the aftermath of a doubly-failed promote: no current at all.
    (uploads_root / "current").rename(uploads_root / "retired-aabbccddeeff")
    (uploads_root / "incoming-112233445566").mkdir()

    # An upload that runs the entry sweep and then fails at the promote.
    switch = _break_renames_from(monkeypatch, "incoming-", _locked_error())
    assert client.post("/api/datasets/upload?filename=next.inpx", content=INPX_B).status_code == 409

    assert _leftovers(uploads_root) == ["retired-aabbccddeeff"]  # incoming-* went, this stayed
    assert (uploads_root / "retired-aabbccddeeff" / "current.inpx").read_bytes() == INPX_A

    # A successful upload supersedes it, and only then is it collected.
    switch["on"] = False
    assert client.post("/api/datasets/upload?filename=next.inpx", content=INPX_B).status_code == 200
    assert _leftovers(uploads_root) == []
    assert (uploads_root / "current" / "current.inpx").read_bytes() == INPX_B


def test_concurrent_uploads_into_one_workspace_are_serialized(workspace_root: Path):
    """MEASURED before the per-workspace lock: 8 threads → 6 exceptions
    (FileExistsError WinError 183 / FileNotFoundError WinError 2) and 5
    leftover directories, because the two promotes interleaved on the same
    ``current``/``retired-*`` pair."""
    import threading

    workspace_id = workspaces.default_workspace_id()
    payloads = [b'<network thread="%d"><links/></network>' % index for index in range(8)]
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(payloads))

    def _upload(index: int) -> None:
        try:
            barrier.wait(timeout=10)
            with workspaces.bind_workspace(workspace_id):
                datasets.save_active_dataset(f"t{index}.inpx", payloads[index])
        except BaseException as exc:  # noqa: BLE001 - the whole point of the test
            errors.append(exc)

    threads = [threading.Thread(target=_upload, args=(index,)) for index in range(len(payloads))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    with workspaces.bind_workspace(workspace_id):
        uploads_root = datasets.uploads_root()
        active = datasets.get_active_dataset()
    assert active["source"] == "uploaded"
    # Exactly one upload won, whole and intact -- not a mixture of two.
    assert Path(active["path"]).read_bytes() in payloads
    assert _leftovers(uploads_root) == []


def test_reset_reports_a_locked_dataset_as_a_409(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Same failure mode on the sibling mutator: ``reset`` rmtree'd
    ``uploads/current`` with no error mapping at all, so a held handle was a
    500 there too."""
    assert client.post("/api/datasets/upload?filename=good.inpx", content=INPX_A).status_code == 200

    import shutil as real_shutil
    import types

    broken = {"on": True}

    def _rmtree(*args, **kwargs):
        if broken["on"]:
            raise _locked_error()
        return real_shutil.rmtree(*args, **kwargs)

    # Patch the NAME datasets.py resolves, not the shared shutil module -- and
    # flip a switch rather than monkeypatch.undo(), which would also revert the
    # workspace_root fixture's WORKSPACES_ROOT patch (same monkeypatch object).
    monkeypatch.setattr(datasets, "shutil", types.SimpleNamespace(rmtree=_rmtree))
    response = client.post("/api/datasets/reset")

    assert response.status_code == 409
    assert "still has it open" in response.json()["detail"]
    broken["on"] = False
    assert Path(client.get("/api/datasets/active").json()["path"]).read_bytes() == INPX_A


def test_reset_clears_only_the_requesting_workspace(client: TestClient):
    other = client.post("/api/workspaces", json={"name": "other"}).json()["id"]
    client.post("/api/datasets/upload?filename=a.inpx", content=INPX_A)
    client.post("/api/datasets/upload?filename=b.inpx", content=INPX_B, headers=_headers(other))

    assert client.post("/api/datasets/reset").status_code == 200

    assert client.get("/api/datasets/active").json()["source"] == "default"
    assert client.get("/api/datasets/active", headers=_headers(other)).json()["source"] == "uploaded"


# ---------------------------------------------------------------------------
# THE acceptance property: cross-workspace isolation
# ---------------------------------------------------------------------------


def test_upload_in_one_workspace_is_invisible_from_another(client: TestClient):
    workspace_a = client.get("/api/workspaces").json()["default"]
    workspace_b = client.post("/api/workspaces", json={"name": "B"}).json()["id"]

    uploaded = client.post(
        "/api/datasets/upload?filename=a.inpx", content=INPX_A, headers=_headers(workspace_a)
    )
    assert uploaded.status_code == 200

    from_b = client.get("/api/datasets/active", headers=_headers(workspace_b)).json()
    assert from_b["source"] == "default"
    assert from_b["workspace_id"] == workspace_b
    assert from_b["path"] != uploaded.json()["path"]

    # And the reverse: B's own upload does not disturb A's.
    client.post("/api/datasets/upload?filename=b.inpx", content=INPX_B, headers=_headers(workspace_b))
    assert Path(
        client.get("/api/datasets/active", headers=_headers(workspace_a)).json()["path"]
    ).read_bytes() == INPX_A
    assert Path(
        client.get("/api/datasets/active", headers=_headers(workspace_b)).json()["path"]
    ).read_bytes() == INPX_B


def test_workbook_mapping_memory_is_per_workspace(client: TestClient):
    from services import workbook_mapping

    workspace_b = client.post("/api/workspaces", json={"name": "B"}).json()["id"]
    default_id = workspaces.default_workspace_id()

    with workspaces.bind_workspace(default_id):
        path_a = workbook_mapping.mapping_memory_path()
        staging_a = workbook_mapping.staging_dir()
    with workspaces.bind_workspace(workspace_b):
        path_b = workbook_mapping.mapping_memory_path()
        staging_b = workbook_mapping.staging_dir()

    assert path_a != path_b
    assert staging_a != staging_b
    assert workspaces.workspace_dir(default_id) in path_a.parents
    assert workspaces.workspace_dir(workspace_b) in path_b.parents


# ---------------------------------------------------------------------------
# R1: the measured cross-dataset validation cache leak
# ---------------------------------------------------------------------------


def test_replacing_the_dataset_does_not_serve_the_previous_workbook(client: TestClient):
    """MEASURED bug: ``services.validation``'s ``lru_cache``es are keyed on the
    workbook PATH string, and within a workspace that string never changes. So
    uploading dataset B used to show dataset A's validation numbers forever
    (until a process restart), because only the validation ROUTER cleared the
    caches -- never ``save_active_dataset``."""
    first = _zip_bytes({"net.inpx": INPX_A, "real_counts.xlsx": _counts_workbook(111)})
    second = _zip_bytes({"net.inpx": INPX_B, "real_counts.xlsx": _counts_workbook(222)})

    assert client.post("/api/datasets/upload?filename=a.zip", content=first).status_code == 200
    before = client.get("/api/validation/base-workbook").json()
    assert [row["observed_volume"] for row in before["counts"]] == [111.0]

    assert client.post("/api/datasets/upload?filename=b.zip", content=second).status_code == 200
    after = client.get("/api/validation/base-workbook").json()
    assert [row["observed_volume"] for row in after["counts"]] == [222.0]


def test_resetting_the_dataset_clears_the_workbook_cache(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """The reset path must CLEAR the caches, not merely stop hitting them.

    This test used to assert only ``!= [333.0]`` after the reset, which passes
    with the clear deleted: reset removes ``uploads/current``, so the workbook
    path falls back to the ``data/`` demo file and the PATH-keyed ``lru_cache``
    misses on a different key anyway. Nothing about the assertion touched the
    behaviour it was named for. The spy below does — delete
    ``_clear_validation_caches()`` from ``reset_active_dataset`` and this fails.
    """
    from services import validation as validation_service

    archive = _zip_bytes({"net.inpx": INPX_A, "real_counts.xlsx": _counts_workbook(333)})
    client.post("/api/datasets/upload?filename=a.zip", content=archive)
    before = client.get("/api/validation/base-workbook").json()
    assert [row["observed_volume"] for row in before["counts"]] == [333.0]

    cleared: list[str] = []
    real_clear = validation_service.clear_validation_caches

    def _spy_clear() -> None:
        cleared.append("reset")
        real_clear()

    monkeypatch.setattr(validation_service, "clear_validation_caches", _spy_clear)

    assert client.post("/api/datasets/reset").status_code == 200

    assert cleared, "reset_active_dataset() did not clear the validation caches"
    after = client.get("/api/validation/base-workbook").json()
    assert [row["observed_volume"] for row in after["counts"]] != [333.0]


def test_reset_then_reupload_at_the_same_path_does_not_serve_the_stale_workbook(
    client: TestClient,
):
    """The path-STABLE half of the same property, with no spy at all.

    Reset and re-upload land the workbook back on the exact same absolute path
    inside this workspace, so the ``lru_cache`` key is unchanged across the
    mutation — the only thing that can produce 444 here is an actual cache
    clear on one of the two mutators.
    """
    first = _zip_bytes({"net.inpx": INPX_A, "real_counts.xlsx": _counts_workbook(333)})
    second = _zip_bytes({"net.inpx": INPX_A, "real_counts.xlsx": _counts_workbook(444)})

    assert client.post("/api/datasets/upload?filename=a.zip", content=first).status_code == 200
    first_path = client.get("/api/validation/base-workbook").json()
    assert [row["observed_volume"] for row in first_path["counts"]] == [333.0]

    assert client.post("/api/datasets/reset").status_code == 200
    assert client.post("/api/datasets/upload?filename=a.zip", content=second).status_code == 200

    workbook = datasets.get_active_real_counts_excel()
    assert workbook == datasets.active_real_counts_excel()  # same path as before the reset
    after = client.get("/api/validation/base-workbook").json()
    assert [row["observed_volume"] for row in after["counts"]] == [444.0]


# ---------------------------------------------------------------------------
# Jobs: workspace stamping, filtering, and the TOCTOU fix
# ---------------------------------------------------------------------------


def test_job_lists_are_filtered_by_workspace_and_legacy_records_are_default(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    registry_root = tmp_path / "simulations"
    monkeypatch.setattr(simulation_runs, "SIMULATION_ROOT", registry_root)
    monkeypatch.setattr(simulation_runs, "REGISTRY_PATH", registry_root / "simulation_runs.json")

    default_id = workspaces.default_workspace_id()
    other_id = client.post("/api/workspaces", json={"name": "other"}).json()["id"]

    with workspaces.bind_workspace(default_id):
        simulation_runs.create_job_record("jobdefault", {"name": "default job"})
    with workspaces.bind_workspace(other_id):
        simulation_runs.create_job_record("jobother", {"name": "other job"})

    # A record written before unit 1.4: no workspace_id at all.
    registry_payload = json.loads(simulation_runs.REGISTRY_PATH.read_text(encoding="utf-8"))
    registry_payload["jobs"]["joblegacy"] = {
        "id": "joblegacy",
        "name": "legacy job",
        "status": "queued",
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    simulation_runs.REGISTRY_PATH.write_text(json.dumps(registry_payload), encoding="utf-8")

    default_ids = {job["id"] for job in client.get("/api/simulations").json()}
    other_ids = {job["id"] for job in client.get("/api/simulations", headers=_headers(other_id)).json()}

    assert default_ids == {"jobdefault", "joblegacy"}
    assert other_ids == {"jobother"}

    # Detail-by-id is filtered the same way.
    assert client.get("/api/simulations/jobother").status_code == 404
    assert client.get("/api/simulations/jobother", headers=_headers(other_id)).status_code == 200
    assert client.get("/api/simulations/joblegacy").status_code == 200


def test_interactive_run_freezes_its_source_inpx_at_enqueue(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """TOCTOU regression. ``_simulation_source_inpx()`` used to be called
    INSIDE the worker thread, so a dataset change landing between "job
    accepted" (which stamps ``dataset_revision``) and "worker starts Vissim"
    made the job run a different network than its own record claims. The
    resolution now happens in the request handler and is handed to the thread.

    Residual, disclosed: freezing the PATH does not protect against an
    in-place replacement at the same path (uploading again into the same
    workspace). ``dataset_revision`` on the job record is what detects that,
    and physically snapshotting the source per job belongs with the
    per-workspace runtime layout move (roadmap P③).
    """
    from routers import simulations as simulations_router

    assert client.post("/api/datasets/upload?filename=a.inpx", content=INPX_A).status_code == 200

    captured: dict[str, Path] = {}

    def _fake_thread_target(job_id, params, source_inpx, workspace_id):
        captured["source_inpx"] = source_inpx
        captured["workspace_id"] = workspace_id

    monkeypatch.setattr(simulations_router, "_run_simulation", _fake_thread_target)
    monkeypatch.setattr(
        simulations_router, "_simulation_capabilities", lambda: {"can_run": True, "reason": None}
    )

    default_id = workspaces.default_workspace_id()
    with workspaces.bind_workspace(default_id):
        uploaded_source = simulations_router._simulation_source_inpx()
        submitted = simulations_router.start_simulation_job(
            simulations_router.SimParams(name="interactive")
        )

        # The dataset goes away AFTER the job was accepted -- exactly the race.
        assert client.post("/api/datasets/reset").status_code == 200
        assert simulations_router._simulation_source_inpx() == config.INPX_PATH

    assert submitted["status"] == "queued"
    assert captured["source_inpx"] == uploaded_source
    assert captured["source_inpx"] != config.INPX_PATH
    assert captured["workspace_id"] == default_id


# ---------------------------------------------------------------------------
# Agent sessions: frozen source + workspace ownership
# ---------------------------------------------------------------------------


def test_session_source_is_frozen_at_creation_not_re_resolved(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Rollback-to-version-0 regression: clone under dataset A, swap the
    active dataset to B, and the session must still restore A."""
    from routers import assistant as assistant_endpoint
    from services.agent import tools_edit
    from services.agent.state import ArtifactManager

    dataset_a = tmp_path / "a" / "current.inpx"
    dataset_b = tmp_path / "b" / "current.inpx"
    dataset_a.parent.mkdir(parents=True)
    dataset_b.parent.mkdir(parents=True)
    dataset_a.write_bytes(INPX_A)
    dataset_b.write_bytes(INPX_B)

    session_id = "abcdef012345"
    work_dir = tmp_path / "sessions" / session_id

    monkeypatch.setattr(assistant_endpoint, "get_active_inpx_path", lambda: dataset_a)
    first = assistant_endpoint._session_source_inpx(session_id, work_dir)
    assert first == dataset_a

    manager = ArtifactManager(source_inpx=first, work_dir=work_dir)
    manager.clone()
    assert manager.working_path.read_bytes() == INPX_A

    # The active dataset changes underneath the live session.
    monkeypatch.setattr(assistant_endpoint, "get_active_inpx_path", lambda: dataset_b)
    monkeypatch.setattr(tools_edit, "get_active_inpx_path", lambda: dataset_b)

    assert assistant_endpoint._session_source_inpx(session_id, work_dir) == dataset_a
    assert tools_edit._session_pristine_source(work_dir) == dataset_a

    # And an ArtifactManager rebuilt for a later turn still rolls back to A.
    later = ArtifactManager(
        source_inpx=assistant_endpoint._session_source_inpx(session_id, work_dir), work_dir=work_dir
    )
    later.working_path.write_bytes(b"<network edited-in-place=\"1\"/>")
    later.rollback_to(0)
    assert later.working_path.read_bytes() == INPX_A


def test_session_scoped_endpoints_404_across_workspaces(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from routers import agent_downloads as agent_downloads_router
    from routers import network as network_router
    from services import audit_trail
    from services.agent import sessions as agent_sessions

    sessions_root = tmp_path / "agent_sessions"
    monkeypatch.setattr(agent_sessions, "SESSIONS_ROOT", sessions_root)
    monkeypatch.setattr(agent_downloads_router, "SESSIONS_ROOT", sessions_root)
    monkeypatch.setattr(network_router, "SESSIONS_ROOT", sessions_root)
    monkeypatch.setattr(audit_trail, "SESSIONS_ROOT", sessions_root)

    owner_id = workspaces.default_workspace_id()
    intruder_id = client.post("/api/workspaces", json={"name": "intruder"}).json()["id"]

    session_id = "0011223344ff"
    work_dir = sessions_root / session_id
    work_dir.mkdir(parents=True)
    (work_dir / "working.inpx").write_bytes(INPX_A)
    (work_dir / "edit_001.inpx").write_bytes(INPX_A)
    (work_dir / f"trace_{session_id}.jsonl").write_text(
        json.dumps({"kind": "plan", "payload": {}}) + "\n", encoding="utf-8"
    )
    agent_sessions.write_session_meta(work_dir, {"session_id": session_id, "workspace_id": owner_id})

    # Owner sees everything.
    assert client.get(f"/api/agent/downloads?session_id={session_id}").json()
    assert client.get(f"/api/agent/downloads/{session_id}/latest").status_code == 200
    assert client.get(f"/api/agent/audit/{session_id}/summary").status_code == 200
    assert client.get(f"/api/network?session_id={session_id}").json()["source"]["view"] == "working"

    # Another project sees nothing at all.
    intruder = _headers(intruder_id)
    assert client.get(f"/api/agent/downloads?session_id={session_id}", headers=intruder).json() == []
    assert client.get(f"/api/agent/downloads/{session_id}/latest", headers=intruder).status_code == 404
    assert client.get(f"/api/agent/audit/{session_id}/summary", headers=intruder).status_code == 404
    assert client.get(f"/api/agent/audit/{session_id}/export.docx", headers=intruder).status_code == 404
    # The network view degrades to the intruder's OWN active dataset (this
    # endpoint's established shape for any unusable session) -- never the
    # other project's working copy.
    intruder_network = client.get(f"/api/network?session_id={session_id}", headers=intruder).json()
    assert intruder_network["source"] == {"view": "active"}

    approve = client.post(
        "/api/assistant/agent/approve",
        json={"session_id": session_id, "approval_id": "whatever", "approved": True},
        headers=intruder,
    )
    assert approve.status_code == 404


def test_legacy_sessions_without_a_sidecar_belong_to_the_default_workspace(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from routers import agent_downloads as agent_downloads_router
    from services.agent import sessions as agent_sessions

    sessions_root = tmp_path / "agent_sessions"
    monkeypatch.setattr(agent_sessions, "SESSIONS_ROOT", sessions_root)
    monkeypatch.setattr(agent_downloads_router, "SESSIONS_ROOT", sessions_root)

    session_id = "ffeeddccbbaa"
    work_dir = sessions_root / session_id
    work_dir.mkdir(parents=True)
    (work_dir / "edit_001.inpx").write_bytes(INPX_A)

    other_id = client.post("/api/workspaces", json={"name": "other"}).json()["id"]

    assert client.get(f"/api/agent/downloads/{session_id}/latest").status_code == 200
    assert (
        client.get(f"/api/agent/downloads/{session_id}/latest", headers=_headers(other_id)).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# Lazy legacy migration
# ---------------------------------------------------------------------------


def test_legacy_uploads_are_migrated_into_the_default_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    legacy = tmp_path / "runtime" / "uploads"
    (legacy / "current").mkdir(parents=True)
    (legacy / "current" / "current.inpx").write_bytes(INPX_A)
    (legacy / "current" / "metadata.json").write_text(
        json.dumps({"filename": "client.inpx", "kind": "inpx"}), encoding="utf-8"
    )
    (legacy / "staging" / "deadbeef").mkdir(parents=True)
    (legacy / "staging" / "deadbeef" / "source.xlsx").write_bytes(b"staged")
    (legacy / "mapping_memory.json").write_text(json.dumps({"counts": {}}), encoding="utf-8")

    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", tmp_path / "runtime" / "workspaces")
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", legacy)

    record = workspaces.ensure_default_workspace()
    uploads = workspaces.workspace_uploads_root(record["id"])

    assert (uploads / "current" / "current.inpx").read_bytes() == INPX_A
    assert (uploads / "staging" / "deadbeef" / "source.xlsx").read_bytes() == b"staged"
    assert (uploads / "mapping_memory.json").is_file()
    assert not (legacy / "current").exists()
    assert not (legacy / "staging").exists()
    assert not (legacy / "mapping_memory.json").exists()

    with workspaces.bind_workspace(record["id"]):
        dataset = datasets.get_active_dataset()
    assert dataset["source"] == "uploaded"
    assert dataset["filename"] == "client.inpx"

    # Idempotent: a second boot is a no-op, not an error and not a re-nest.
    workspaces._migration_done.discard(str(workspaces.WORKSPACES_ROOT))
    workspaces.ensure_default_workspace()
    assert (uploads / "current" / "current.inpx").read_bytes() == INPX_A
    assert not (uploads / "current" / "current").exists()


def test_migration_moves_only_the_items_that_exist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Check-then-move per item: a legacy layout with only some of the three
    entries migrates what is there and leaves the rest alone."""
    legacy = tmp_path / "runtime" / "uploads"
    (legacy / "current").mkdir(parents=True)
    (legacy / "current" / "current.inpx").write_bytes(INPX_B)

    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", tmp_path / "runtime" / "workspaces")
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", legacy)

    record = workspaces.ensure_default_workspace()
    uploads = workspaces.workspace_uploads_root(record["id"])

    assert (uploads / "current" / "current.inpx").read_bytes() == INPX_B
    assert not (uploads / "staging").exists()
    assert not (uploads / "mapping_memory.json").exists()


def test_a_degraded_migration_retries_on_the_next_call_and_is_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    """MEASURED: ``_migration_done`` was set UNCONDITIONALLY, so an item that
    failed to move was retried on the next BOOT, not the next call — while the
    dashboard silently showed the ``data/`` demo network as though it were the
    user's model, with the real one still sitting in ``runtime/uploads``."""
    import logging
    import types

    legacy = tmp_path / "runtime" / "uploads"
    (legacy / "current").mkdir(parents=True)
    (legacy / "current" / "current.inpx").write_bytes(INPX_A)
    (legacy / "current" / "metadata.json").write_text(
        json.dumps({"filename": "client.inpx", "kind": "inpx"}), encoding="utf-8"
    )
    (legacy / "mapping_memory.json").write_text(json.dumps({"counts": {}}), encoding="utf-8")

    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", tmp_path / "runtime" / "workspaces")
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", legacy)

    real_move = workspaces.shutil.move
    failing = {"on": True}

    def _move(source, destination):
        if failing["on"] and Path(source).name == "current":
            raise PermissionError(13, "current.inpx is open in Vissim")
        return real_move(source, destination)

    monkeypatch.setattr(workspaces, "shutil", types.SimpleNamespace(move=_move))

    caplog.set_level(logging.WARNING, logger="services.workspaces")
    record = workspaces.ensure_default_workspace()
    uploads = workspaces.workspace_uploads_root(record["id"])
    root_key = str(workspaces.WORKSPACES_ROOT)

    # The healthy item moved; the failed one stayed put, intact and readable.
    assert (uploads / "mapping_memory.json").is_file()
    assert (legacy / "current" / "current.inpx").read_bytes() == INPX_A
    assert root_key not in workspaces._migration_done

    # Degraded is VISIBLE: a WARNING naming what did not move...
    assert any(
        "legacy migration" in message and "INCOMPLETE" in message for message in caplog.messages
    ), caplog.messages
    status = workspaces.legacy_migration_status()
    assert status["code"] == "legacy_migration_incomplete"
    assert status["failed"] == ["current"]
    # ...and the same thing on the payload the dashboard and the project banner
    # both already read, next to the demo dataset it is explaining.
    with workspaces.bind_workspace(record["id"]):
        degraded = datasets.get_active_dataset()
    assert degraded["source"] == "default"
    assert degraded["warnings"][0]["code"] == "legacy_migration_incomplete"
    assert str(legacy) in degraded["warnings"][0]["message"]

    # THE fix: the retry is on the next CALL, not the next process.
    failing["on"] = False
    workspaces.ensure_default_workspace()

    assert (uploads / "current" / "current.inpx").read_bytes() == INPX_A
    assert not (legacy / "current").exists()
    assert root_key in workspaces._migration_done
    assert workspaces.legacy_migration_status() is None
    with workspaces.bind_workspace(record["id"]):
        healthy = datasets.get_active_dataset()
    assert healthy["source"] == "uploaded"
    assert "warnings" not in healthy


def test_a_conflicting_migration_item_is_reported_without_retrying_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Source AND destination both present: we refuse to clobber, and retrying
    would refuse identically forever — so it is marked done (no per-request
    retry) but still reported, because the legacy copy is still on disk."""
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", tmp_path / "runtime" / "workspaces")
    legacy = tmp_path / "runtime" / "uploads"
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", legacy)

    record = workspaces.ensure_default_workspace()
    uploads = workspaces.workspace_uploads_root(record["id"])
    (uploads / "current").mkdir(parents=True)
    (uploads / "current" / "current.inpx").write_bytes(INPX_B)

    # A legacy dataset appears afterwards (a restored backup, a rolled-back
    # deploy), and the destination is already occupied.
    (legacy / "current").mkdir(parents=True)
    (legacy / "current" / "current.inpx").write_bytes(INPX_A)
    workspaces._migration_done.discard(str(workspaces.WORKSPACES_ROOT))

    workspaces.ensure_default_workspace()

    status = workspaces.legacy_migration_status()
    assert status["conflicted"] == ["current"]
    assert status["failed"] == []
    assert str(workspaces.WORKSPACES_ROOT) in workspaces._migration_done
    # Neither copy was touched.
    assert (legacy / "current" / "current.inpx").read_bytes() == INPX_A
    assert (uploads / "current" / "current.inpx").read_bytes() == INPX_B


def test_no_legacy_layout_is_a_clean_no_op(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "nothing-here")

    record = workspaces.ensure_default_workspace()

    with workspaces.bind_workspace(record["id"]):
        assert datasets.get_active_dataset()["source"] == "default"

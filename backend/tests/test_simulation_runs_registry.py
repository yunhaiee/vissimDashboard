"""Unit U6: the simulation-run registry file is crash-safe and race-safe.

Every test here runs against a pytest ``tmp_path`` registry (both module
constants ``services.simulation_runs`` resolves per call are repointed), never
the developer's real ``runtime/simulations/``. No Vissim, no COM, no backend
process -- this is the storage layer on its own.

What each block pins:

* **hammer** -- N threads interleaving create/update/list lose nothing. Run
  against the pre-U6 implementation (no lock, ``write_text`` in place of
  temp+replace) the same loop loses records and raises; see the unit report.
* **quarantine** -- a registry that does not parse is moved aside with its
  bytes intact, a fresh one is started, and the loss stays DISCLOSED (in the
  new file's own payload, so it survives the restart that follows a crash).
* **crash window** -- ``os.replace`` failing mid-write leaves the previous
  registry byte-for-byte intact. The window is gone by construction: the live
  file is never opened for writing.
* **refusal** -- a registry whose bytes could not be read is never overwritten
  with an empty one. That is the failure that turns a transient share lock
  into permanent data loss.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

import services.simulation_runs as simulation_runs
from services import workspaces


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Repoint the registry at a tmp file and return its path.

    Both constants must move together -- ``SIMULATION_ROOT`` is what
    ``job_dir`` resolves work directories from, ``REGISTRY_PATH`` is the file
    itself -- exactly as ``tests/conftest.py`` does for the whole session.
    """
    root = tmp_path / "simulations"
    path = root / "simulation_runs.json"
    monkeypatch.setattr(simulation_runs, "SIMULATION_ROOT", root)
    monkeypatch.setattr(simulation_runs, "REGISTRY_PATH", path)
    yield path
    # The module's health/count caches are keyed by registry path, and every
    # test gets a unique tmp path, so they cannot leak between tests -- but
    # leaving entries behind would grow across a full suite run.
    simulation_runs._registry_health.pop(str(path), None)
    simulation_runs._last_known_record_count.pop(str(path), None)


def _record(job_id: str, **extra: Any) -> dict[str, Any]:
    record = {
        "id": job_id,
        "status": "queued",
        "created_at": "2026-08-06T00:00:00+00:00",
        "workspace_id": workspaces.current_workspace_id(),
    }
    record.update(extra)
    return record


def _payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _corrupt_siblings(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.corrupt-*"))


def _tmp_siblings(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"{path.name}.*.tmp"))


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------


def test_every_write_is_a_same_directory_temp_file_then_os_replace(
    registry: Path, monkeypatch: pytest.MonkeyPatch
):
    """The live registry is never opened for writing -- that is what makes the
    crash window disappear rather than merely shrink."""
    replaces: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src, dst):
        replaces.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    simulation_runs.upsert_job_record(_record("job-atomic"))
    monkeypatch.setattr(os, "replace", real_replace)

    assert len(replaces) == 1
    src, dst = replaces[0]
    assert src.endswith(".tmp")
    assert dst == str(registry)
    # Same directory: a cross-volume rename is not atomic (and raises on Windows).
    assert Path(src).parent == registry.parent
    # ...and nothing is left lying around afterwards.
    assert _tmp_siblings(registry) == []


def test_a_crash_between_the_temp_write_and_the_replace_leaves_the_registry_intact(
    registry: Path, monkeypatch: pytest.MonkeyPatch
):
    simulation_runs.upsert_job_record(_record("job-survivor"))
    before = registry.read_bytes()

    real_replace = os.replace

    def boom(src, dst):
        raise OSError("simulated crash after the temp file was written")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        simulation_runs.upsert_job_record(_record("job-never-lands"))

    assert registry.read_bytes() == before
    assert _tmp_siblings(registry) == [], "a half-written temp file was left behind"
    assert simulation_runs.get_job_record("job-survivor") is not None
    assert simulation_runs.get_job_record("job-never-lands") is None

    # And the registry is still writable once the fault clears.
    monkeypatch.setattr(os, "replace", real_replace)
    simulation_runs.upsert_job_record(_record("job-after-crash"))
    assert simulation_runs.get_job_record("job-after-crash") is not None
    assert simulation_runs.get_job_record("job-survivor") is not None


# ---------------------------------------------------------------------------
# Concurrency hammer
# ---------------------------------------------------------------------------


def test_concurrent_create_update_list_loses_nothing(registry: Path):
    """N threads interleaving create / update / list against one registry.

    Pre-U6 this loop lost records (two threads read the same payload, both
    wrote it back, the second write dropped the first record) and readers hit
    ``json.JSONDecodeError`` against a mid-``write_text`` truncated file. Now:
    zero exceptions, exact final count, every update present.
    """
    workers = 6
    iterations = 25
    workspace_id = workspaces.default_workspace_id()
    errors: list[BaseException] = []
    stop = threading.Event()
    counts_seen: list[int] = []
    counts_lock = threading.Lock()

    def worker(index: int) -> None:
        # A plain Thread does not inherit the request's ContextVars, so the
        # workspace is bound explicitly -- exactly what the real job runners do.
        try:
            with workspaces.bind_workspace(workspace_id):
                for i in range(iterations):
                    job_id = f"job-{index:02d}-{i:03d}"
                    simulation_runs.create_job_record(
                        job_id, {"name": job_id, "random_seeds": [1, 2]}
                    )
                    updated = simulation_runs.update_job_record(
                        job_id, status="done", phase="done", progress=1.0
                    )
                    assert updated is not None, f"update to {job_id} was not persisted"
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
            errors.append(exc)

    def reader() -> None:
        try:
            with workspaces.bind_workspace(workspace_id):
                while not stop.is_set():
                    records = simulation_runs.list_job_records()
                    with counts_lock:
                        counts_seen.append(len(records))
        except BaseException as exc:  # noqa: BLE001 - surfaced via `errors`
            errors.append(exc)

    writer_threads = [threading.Thread(target=worker, args=(n,)) for n in range(workers)]
    reader_threads = [threading.Thread(target=reader) for _ in range(3)]
    for t in reader_threads:
        t.start()
    for t in writer_threads:
        t.start()
    for t in writer_threads:
        t.join(timeout=120)
    stop.set()
    for t in reader_threads:
        t.join(timeout=30)

    assert not errors, f"concurrent registry access raised: {errors[:3]}"
    assert all(not t.is_alive() for t in writer_threads + reader_threads)

    # Exact final count: nothing was lost, nothing was duplicated.
    records = simulation_runs.list_job_records()
    assert len(records) == workers * iterations
    assert {record["id"] for record in records} == {
        f"job-{n:02d}-{i:03d}" for n in range(workers) for i in range(iterations)
    }
    # Every record carries BOTH its create-time fields and its update -- a lost
    # update would show up here as a record still sitting at "queued".
    assert all(record["status"] == "done" for record in records)
    assert all(record["phase"] == "done" for record in records)
    assert all(record["total_seeds"] == 2 for record in records)

    # Readers never observed the count going backwards (a torn read that fell
    # back to "no records", or a lost update, would show up as a regression).
    assert counts_seen == sorted(counts_seen), "a concurrent reader saw the registry shrink"

    # The file itself is well-formed, and the quarantine path never fired.
    assert len(_payload(registry)["jobs"]) == workers * iterations
    assert _corrupt_siblings(registry) == []
    assert _tmp_siblings(registry) == []
    assert simulation_runs.registry_health_status() is None


# ---------------------------------------------------------------------------
# Torn-file quarantine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "corrupt_bytes",
    [
        pytest.param(b"", id="zero-length"),
        pytest.param(b'{"jobs": {"job-old": {"id": "job-o', id="truncated-mid-write"),
        pytest.param(b"\x00\x01\x02\xff\xfe not json at all", id="garbage-bytes"),
        pytest.param(b'["not", "an", "object"]', id="parses-but-not-a-registry"),
        pytest.param(b'{"jobs": []}', id="jobs-is-not-a-mapping"),
    ],
)
def test_a_registry_that_does_not_parse_is_quarantined_not_swallowed(
    registry: Path, corrupt_bytes: bytes
):
    simulation_runs.upsert_job_record(_record("job-old"))
    assert simulation_runs.get_job_record("job-old") is not None

    registry.write_bytes(corrupt_bytes)

    # The read does not raise, does not return a silent [], and does not
    # pretend the history never existed.
    assert simulation_runs.list_job_records() == []

    quarantined = _corrupt_siblings(registry)
    assert len(quarantined) == 1, f"expected exactly one quarantine sibling, got {quarantined}"
    assert quarantined[0].read_bytes() == corrupt_bytes, "the corrupt bytes were not preserved"

    # A fresh, working registry took its place.
    fresh = _payload(registry)
    assert fresh["jobs"] == {}
    simulation_runs.upsert_job_record(_record("job-new"))
    assert simulation_runs.get_job_record("job-new") is not None

    # ...and the loss is disclosed, in all three UI languages, with the honest
    # count (this process had read exactly one record before the corruption).
    status = simulation_runs.registry_health_status()
    assert status is not None
    assert status["code"] == "registry_quarantined"
    assert status["lost_record_count"] == 1
    assert status["quarantine_path"] == str(quarantined[0])
    assert set(status["messages"]) == {"ko", "en", "ja"}
    assert "1" in status["messages"]["ko"]
    assert "1 record(s) were lost" in status["messages"]["en"]
    assert str(quarantined[0]) in status["messages"]["ja"]


def test_the_quarantine_disclosure_survives_a_process_restart(registry: Path):
    """The incident lives in the NEW registry's own payload, not only in
    memory -- a torn file is usually noticed by the process that starts after
    the crash, which never saw the records that were lost."""
    simulation_runs.upsert_job_record(_record("job-a"))
    simulation_runs.upsert_job_record(_record("job-b"))
    registry.write_bytes(b"{ truncated")
    assert simulation_runs.list_job_records() == []
    assert simulation_runs.registry_health_status()["lost_record_count"] == 2

    # Simulate a restart: drop every in-memory cache this module keeps.
    simulation_runs._registry_health.clear()
    simulation_runs._last_known_record_count.clear()
    assert simulation_runs.registry_health_status() is None  # nothing read yet

    assert simulation_runs.list_job_records() == []
    status = simulation_runs.registry_health_status()
    assert status is not None
    assert status["code"] == "registry_quarantined"
    assert status["lost_record_count"] == 2, "the persisted count did not survive the restart"


def test_an_unknown_loss_count_is_reported_as_unknown_never_as_zero(registry: Path):
    """No successful read in this process means we genuinely do not know how
    many records the corrupt file held. Counting ids out of unparseable bytes
    would be a made-up number -- the doctrine forbids it."""
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_bytes(b"}}}} not json")
    simulation_runs._last_known_record_count.pop(str(registry), None)

    assert simulation_runs.list_job_records() == []

    status = simulation_runs.registry_health_status()
    assert status is not None
    assert status["lost_record_count"] is None
    assert "알 수 없습니다" in status["messages"]["ko"]
    assert "unknown" in status["messages"]["en"]
    assert "不明" in status["messages"]["ja"]


def test_a_second_corruption_does_not_overwrite_the_first_quarantine(registry: Path):
    simulation_runs.upsert_job_record(_record("job-1"))
    registry.write_bytes(b"first corruption")
    assert simulation_runs.list_job_records() == []

    simulation_runs.upsert_job_record(_record("job-2"))
    registry.write_bytes(b"second corruption")
    assert simulation_runs.list_job_records() == []

    siblings = _corrupt_siblings(registry)
    assert len(siblings) == 2
    assert {path.read_bytes() for path in siblings} == {b"first corruption", b"second corruption"}
    assert simulation_runs.registry_health_status()["quarantine_count"] == 1
    # Both incidents are in the payload's history even though the disclosure
    # reports the newest one.
    assert len(_payload(registry)["incidents"]) == 1


def test_quarantine_refuses_when_the_corrupt_file_cannot_be_moved_aside(
    registry: Path, monkeypatch: pytest.MonkeyPatch
):
    """If the bytes cannot be renamed away, they are the only copy of the run
    history -- so nothing is written over them and the failure is surfaced."""
    simulation_runs.upsert_job_record(_record("job-1"))
    registry.write_bytes(b"corrupt but locked")

    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda src, dst: (_ for _ in ()).throw(OSError("locked")))

    # Read paths degrade (a dashboard must not 500) but never destroy.
    assert simulation_runs.list_job_records() == []
    # Write paths refuse outright.
    with pytest.raises(simulation_runs.RegistryUnavailableError):
        simulation_runs.upsert_job_record(_record("job-2"))

    assert registry.read_bytes() == b"corrupt but locked"
    assert _corrupt_siblings(registry) == []
    monkeypatch.setattr(os, "replace", real_replace)


# ---------------------------------------------------------------------------
# Unreadable (not corrupt) -- never overwrite what you could not read
# ---------------------------------------------------------------------------


def _fail_reads_of(path: Path, monkeypatch: pytest.MonkeyPatch, attempts: list[int]) -> None:
    real_read_bytes = Path.read_bytes

    def fake(self: Path):
        if self == path:
            attempts.append(1)
            raise OSError("simulated share-lock: the file is open in another process")
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", fake)


def test_an_unreadable_registry_is_never_overwritten_with_an_empty_one(registry: Path):
    simulation_runs.upsert_job_record(_record("job-precious"))
    before = registry.read_bytes()

    attempts: list[int] = []
    # Its OWN monkeypatch context: undoing it must not also undo the `registry`
    # fixture's repointing of the module constants.
    with pytest.MonkeyPatch.context() as mp:
        _fail_reads_of(registry, mp, attempts)

        with pytest.raises(simulation_runs.RegistryUnavailableError):
            simulation_runs.upsert_job_record(_record("job-new"))
        # An update of a RUNNING job refuses quietly instead (killing a live
        # Vissim run over a locked JSON file helps nobody) -- but still writes
        # nothing.
        assert simulation_runs.update_job_record("job-precious", status="done") is None
        # Read paths degrade to "no records" rather than raising at the endpoint.
        assert simulation_runs.get_job_record("job-precious") is None
        assert simulation_runs.list_job_records() == []

    assert registry.read_bytes() == before
    assert _corrupt_siblings(registry) == [], "an unreadable file must not be quarantined"
    assert _tmp_siblings(registry) == []
    # Every failing read was retried before being declared unavailable.
    assert len(attempts) == 4 * simulation_runs._READ_RETRY_ATTEMPTS
    assert simulation_runs.get_job_record("job-precious") is not None


# ---------------------------------------------------------------------------
# Payload normalization -- 1.4's legacy rule stays untouched
# ---------------------------------------------------------------------------


def test_legacy_records_without_workspace_id_survive_the_new_read_path(registry: Path):
    """Unit 1.4's rule: a record with no ``workspace_id`` predates workspaces
    and belongs to the default one. The U6 read path must not drop it,
    quarantine over it, or stamp it."""
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "jobs": {
                    "joblegacy": {
                        "id": "joblegacy",
                        "name": "legacy job",
                        "status": "done",
                        "created_at": "2026-01-01T00:00:00+00:00",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    record = simulation_runs.get_job_record("joblegacy")
    assert record is not None
    assert "workspace_id" not in record
    assert simulation_runs.record_workspace_id(record) == workspaces.default_workspace_id()
    assert simulation_runs.belongs_to_current_workspace(record)
    assert _corrupt_siblings(registry) == []
    assert simulation_runs.registry_health_status() is None


def test_unreadable_entries_are_dropped_without_condemning_the_whole_registry(registry: Path):
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "jobs": {
                    "good": {"id": "good", "status": "done"},
                    "junk": "this is not a record",
                    "idless": {"status": "queued"},
                }
            }
        ),
        encoding="utf-8",
    )

    assert simulation_runs.get_job_record("junk") is None
    assert simulation_runs.get_job_record("good") is not None
    # A record missing "id" gets it back from its own key -- without it the
    # listing endpoint (which indexes record["id"]) would raise a KeyError.
    idless = simulation_runs.get_job_record("idless")
    assert idless is not None and idless["id"] == "idless"
    # One bad entry is not a corrupt file: nothing was quarantined.
    assert _corrupt_siblings(registry) == []

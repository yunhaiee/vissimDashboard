"""Persistent registry for local VISSIM simulation runs.

**Crash- and race-safety (unit U6).** This file is the only durable record of
every simulation this box has ever run: the scenario picker, the agent's
``run_simulation`` tool, the results reader and the trajectory endpoints all
resolve a job through it. Until U6 it was a plain
``Path.write_text`` over a read-modify-write with no lock, which gave it three
distinct ways to lose *every* job record on the machine:

1. **Torn write.** ``write_text`` truncates first and writes second, so a
   crash (or a power loss) between those two steps left a half-written or
   zero-byte file. The reader's ``except Exception: return {}`` then reported
   the box as having no run history at all -- and the very next write
   persisted that emptiness.
2. **Lost update.** Two threads -- a job-runner thread advancing progress and
   a request handler creating a job -- each read the whole registry, mutated
   their own copy, and wrote it back. Interleave the reads and the second
   write silently drops the first writer's record. ``routers/simulations.py``
   has a ``_jobs_lock``, but it guards the in-memory mirror only and is
   explicitly released before the file write.
3. **Overwrite-after-unreadable.** A transient ``OSError`` on read (on Windows
   an antivirus scanner or the indexer holding a share lock is enough) also
   fell into ``return {}``, and the caller then wrote that ``{}`` over a
   perfectly good file.

All three are closed here, mirroring the house pattern unit 1.4 established in
``services/workspaces.py`` (atomic temp-then-replace + a module ``RLock``) and
``services/agent/session_store.py``:

* Every write goes through :func:`_write_payload` -- same-directory temp file,
  ``flush`` + ``fsync``, then ``os.replace``. Same directory matters: the
  rename is then same-volume, which is what makes it atomic on Windows too.
* Every read-modify-write goes through :func:`_mutate_registry`, which holds
  :data:`_REGISTRY_LOCK` across read -> mutate -> write. There is exactly one
  writer function; the lock and the atomicity cannot be bypassed by adding a
  call site.
* A registry that does not parse is **quarantined**, never swallowed: the
  original bytes are renamed aside to a timestamped ``.corrupt-*`` sibling, a
  fresh registry is started, and the incident is recorded *in the new
  registry's own payload* so the loss is still disclosable after a restart
  (:func:`registry_health_status`).

**Cross-PROCESS locking is deliberately out of scope for U6.** Prism today is
a single uvicorn process (the job queue, the agent turns and the request
handlers are all threads inside it), so a process-wide ``RLock`` covers every
writer that actually exists -- the same assumption ``services/workspaces.py``
and ``services/agent/sessions.py`` already make and say out loud. Two Prism
processes pointed at one ``runtime/`` would still be able to interleave
read-modify-write cycles; ``os.replace`` keeps each individual write atomic
(no reader ever sees a torn file) but cannot prevent a lost update. The real
answer to that is roadmap P③'s SQLite/WAL migration, not an advisory lock file
bolted on here -- a lock file adds its own crash-recovery problem (a stale
lock after a hard kill) and would still not give us transactions.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, TypeVar
from uuid import uuid4

from config import REPO_ROOT
from services import workspaces
from services.datasets import get_active_dataset

logger = logging.getLogger(__name__)

SIMULATION_ROOT = REPO_ROOT / "runtime" / "simulations"
REGISTRY_PATH = SIMULATION_ROOT / "simulation_runs.json"
RUNTIME_SCENARIO_PREFIX = "SIM_"

#: Serializes every read-modify-write of the registry FILE inside this process.
#: Re-entrant because the quarantine path writes a fresh registry from inside a
#: read, and :func:`_mutate_registry` calls both halves while already holding it.
#: Scope is read -> mutate -> write, never merely the write -- a lock that only
#: covered the write would still lose updates (see the module docstring).
#: Distinct from ``routers/simulations.py``'s ``_jobs_lock``, which guards the
#: in-memory mirror; that lock is always RELEASED before this module is called
#: (``_update_job`` persists outside it), so the two can never deadlock.
_REGISTRY_LOCK = threading.RLock()

#: A read that fails with ``OSError`` is retried briefly before it is treated
#: as unavailable: on Windows the common cause is another process holding a
#: transient share lock (antivirus, the search indexer, a backup agent), which
#: clears in milliseconds. Only after these attempts do we refuse -- and
#: refusing means raising :class:`RegistryUnavailableError`, never returning an
#: empty registry that a caller would then write back over the real file.
_READ_RETRY_ATTEMPTS = 3
_READ_RETRY_DELAY_S = 0.02

#: How many quarantine incidents the registry payload keeps. Bounded so a box
#: that repeatedly corrupts its registry cannot grow the file without limit;
#: newest last.
_MAX_INCIDENTS = 20

#: ``str(REGISTRY_PATH)`` -> the disclosure :func:`registry_health_status`
#: serves, or absent when that registry is healthy. Refreshed on every
#: successful read/write, so the accessor itself does no disk I/O (same shape
#: and the same reason as ``workspaces._migration_degraded``). Keyed by path so
#: a test that repoints the registry gets its own state.
_registry_health: dict[str, dict[str, Any]] = {}

#: ``str(REGISTRY_PATH)`` -> how many records the last SUCCESSFUL read/write of
#: that registry saw. This is the only honest source for "N records were lost"
#: when a file later fails to parse: we cannot count records in bytes that do
#: not parse, and guessing a number from them would be exactly the invented
#: figure this project's doctrine forbids. Absent (process restarted since the
#: last good read) means the count is reported as unknown, not as zero.
_last_known_record_count: dict[str, int] = {}

# Unit 1.4 scopes jobs LOGICALLY, not physically: every record is stamped with
# the workspace that submitted it and every listing filters on the requesting
# workspace, but the files still live in one flat ``runtime/simulations/<job_id>``
# tree. Moving them under ``runtime/workspaces/<id>/simulations/`` is roadmap
# P③'s job -- doing it here would strand every existing completed run.
#
# Records written before this unit carry no ``workspace_id`` at all. They are
# treated as belonging to the DEFAULT workspace (:func:`record_workspace_id`),
# which is exactly where their dataset was migrated to -- so a consultant's
# existing run history stays visible after upgrading instead of silently
# vanishing from the scenario picker.


class RegistryUnavailableError(RuntimeError):
    """The registry file exists but its current contents could not be read.

    Raised only after :data:`_READ_RETRY_ATTEMPTS` failed reads, or when a
    corrupt file could not be moved aside for quarantine. It exists so a WRITE
    can refuse: overwriting a file whose contents we could not read would turn
    a transient I/O problem into permanent data loss, which is the single worst
    thing this module can do. Read paths degrade to "no records" (a dashboard
    must not 500 over this) and disclose via :func:`registry_health_status`.
    """


def record_workspace_id(record: dict[str, Any]) -> str:
    """The workspace a job record belongs to, resolving legacy records.

    A record written before unit 1.4 has no ``workspace_id`` key; it predates
    workspaces entirely, so it belongs to the default workspace -- the same one
    its dataset was migrated into.
    """
    value = record.get("workspace_id")
    return str(value) if value else workspaces.default_workspace_id()


def belongs_to_current_workspace(record: dict[str, Any]) -> bool:
    return record_workspace_id(record) == workspaces.current_workspace_id()


def runtime_scenario_id(job_id: str) -> str:
    return f"{RUNTIME_SCENARIO_PREFIX}{job_id}"


def parse_runtime_scenario_id(scenario_id: str) -> str | None:
    if not scenario_id.startswith(RUNTIME_SCENARIO_PREFIX):
        return None
    job_id = scenario_id[len(RUNTIME_SCENARIO_PREFIX):].strip()
    return job_id or None


def job_dir(job_id: str) -> Path:
    return SIMULATION_ROOT / job_id


def create_job_record(job_id: str, params: dict[str, Any]) -> dict[str, Any]:
    dataset = get_active_dataset()
    # RUN-PROGRESS UI (2026-07-28): total_seeds is knowable up front from
    # params for BOTH job paths -- the agent multi-seed path always carries
    # a "random_seeds" list (services.agent.tools_run.run_simulation always
    # supplies at least one entry, possibly [None]), the interactive
    # single-run path never carries that key at all and is always exactly
    # one seed. Everything else starts at its honest "nothing has happened
    # yet" value and is advanced by the job-runner thread at SEED BOUNDARIES
    # only (routers/simulations.py's _run_simulation/_run_agent_simulation_job)
    # -- never per-simulation-step, never a SimBreakAt sampling hack.
    random_seeds = params.get("random_seeds")
    total_seeds = len(random_seeds) if isinstance(random_seeds, list) and random_seeds else 1
    record = {
        "id": job_id,
        "scenario_result_id": runtime_scenario_id(job_id),
        "name": params.get("name") or runtime_scenario_id(job_id),
        "status": "queued",
        "progress": 0.0,
        "params": params,
        "error": None,
        "created_at": _now_iso(),
        "started_at": None,
        "completed_at": None,
        "dataset_revision": dataset.get("revision"),
        "dataset_filename": dataset.get("filename"),
        # Unit 1.4: which project owns this run. Stamped at CREATION, in the
        # request handler's workspace scope -- never re-derived later from
        # whatever workspace happens to be bound when the worker thread
        # updates progress.
        "workspace_id": workspaces.current_workspace_id(),
        "total_seeds": total_seeds,
        "completed_seeds": 0,
        "current_seed": None,
        "phase": "queued",
        "per_seed_durations": [],
        # eta_s stays None until the job runner has at least one completed
        # seed's real duration to work from -- a guess extrapolated from zero
        # samples would just be a made-up number, not an estimate (this
        # project's "refused edit beats wrong numbers" doctrine, applied to
        # progress UI instead of a write).
        "eta_s": None,
        "last_update_at": None,
    }
    upsert_job_record(record)
    return record


def update_job_record(job_id: str, **updates: Any) -> dict[str, Any] | None:
    """Merge ``updates`` into an existing record. ``None`` when it was not
    persisted -- either the job id is unknown (the pre-U6 meaning) or the
    registry is currently unreadable.

    Unreadable is deliberately NOT fatal here, unlike in
    :func:`upsert_job_record`: this is the progress/terminal-status write of a
    simulation that is already running, and killing a live Vissim run because
    an antivirus scanner briefly locked a JSON file would help nobody. The run
    stays visible in ``routers/simulations.py``'s in-memory ``_jobs`` mirror
    for the life of the process; what is lost is the durable copy, and that
    loss is logged at ERROR and disclosed by :func:`registry_health_status`
    rather than swallowed. The one thing that must never happen -- writing an
    empty registry over the unreadable one -- cannot: the write is refused
    inside :func:`_mutate_registry`.
    """

    def _apply(jobs: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, bool]:
        record = jobs.get(job_id)
        if record is None:
            return None, False
        record.update(updates)
        jobs[job_id] = record
        return record, True

    try:
        return _mutate_registry(_apply)
    except RegistryUnavailableError as exc:
        logger.error(
            "simulation_runs: could not persist an update for job %s -- the registry at %s is "
            "unreadable (%s). The run itself is unaffected and stays in the in-memory job "
            "mirror, but this update is NOT on disk.",
            job_id,
            REGISTRY_PATH,
            exc,
        )
        return None


def upsert_job_record(record: dict[str, Any]) -> None:
    """Insert or replace one record.

    Propagates :class:`RegistryUnavailableError`: this is the CREATE path, and
    a job whose record cannot be written is a job with no history and no
    replayability. Refusing to start it with a clear reason beats starting it
    and silently losing it (docs/phase2/00-EXECUTION-GUIDE.md's doctrine).
    """

    def _apply(jobs: dict[str, dict[str, Any]]) -> tuple[None, bool]:
        jobs[str(record["id"])] = record
        return None, True

    _mutate_registry(_apply)


def get_job_record(job_id: str) -> dict[str, Any] | None:
    return _read_registry().get(job_id)


def list_job_records(*, workspace_scoped: bool = True) -> list[dict[str, Any]]:
    """Job records newest-first, restricted to the requesting workspace.

    ``workspace_scoped=False`` is for maintenance/diagnostics that genuinely
    need every job on the box (nothing user-facing does); the default is the
    isolating one so a new call site cannot leak another project's runs by
    forgetting a filter.

    An empty list here can mean two different things -- this box has no runs,
    or its registry was quarantined and the history is gone. Callers that show
    the list to a human should ask :func:`registry_health_status` and say
    which; it is answered from memory, so it costs nothing to ask.
    """
    records = list(_read_registry().values())
    if workspace_scoped:
        records = [record for record in records if belongs_to_current_workspace(record)]
    return sorted(records, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def list_completed_runtime_scenarios() -> list[dict[str, Any]]:
    active_revision = get_active_dataset().get("revision")
    scenarios: list[dict[str, Any]] = []
    for index, record in enumerate(list_job_records()):
        if record.get("status") != "done":
            continue
        if record.get("dataset_revision") != active_revision:
            continue
        scenario_id = record.get("scenario_result_id") or runtime_scenario_id(str(record["id"]))
        scenarios.append(
            {
                "id": scenario_id,
                "name": record.get("name") or scenario_id,
                "color": _runtime_color(index),
                "number": 900000 + index,
                "modifications": [],
                "source": "simulation",
                "job_id": record.get("id"),
            }
        )
    return scenarios


def get_runtime_result_dir_for_scenario(scenario_id: str) -> Path | None:
    job_id = parse_runtime_scenario_id(scenario_id)
    if job_id is None:
        return None
    record = get_job_record(job_id)
    if record is None or record.get("status") != "done":
        return None
    # Workspace check FIRST: a job from another project must be indistinguishable
    # from a job that does not exist, regardless of what its dataset revision is.
    if not belongs_to_current_workspace(record):
        return None
    if record.get("dataset_revision") != get_active_dataset().get("revision"):
        return None
    return job_dir(job_id)


# ---------------------------------------------------------------------------
# Disclosure
# ---------------------------------------------------------------------------


def registry_health_status() -> dict[str, Any] | None:
    """The current registry's data-loss disclosure, or ``None`` when healthy.

    The visible half of the torn-file recovery: a quarantined registry used to
    be indistinguishable from a box that had simply never run a simulation,
    because the reader answered a corrupt file with ``{}``. The event is now
    recorded in the fresh registry's own payload (so it survives the restart
    that follows a crash) and served from here, shaped like
    ``workspaces.legacy_migration_status()``: a ``code``, the machine-readable
    facts, and ``messages`` in ko/en/ja.

    ``lost_record_count`` is ``None`` when the number is genuinely unknown --
    the corrupt bytes cannot be counted, and inventing a figure for a
    consultant's missing run history would be worse than admitting the gap.

    Cheap by construction (an in-memory dict lookup, refreshed by every read
    and write), so a listing endpoint can call it on every request:
    ``routers/simulations.py``'s ``GET /api/simulations`` currently returns a
    bare JSON array that ``frontend/lib/api.ts``'s ``fetchSimulationJobs``
    types as ``SimulationJob[]``, so wrapping it in a disclosure envelope is a
    frontend-contract change and belongs with whoever owns that surface -- U6
    provides the state, not the envelope.
    """
    return _registry_health.get(str(REGISTRY_PATH))


# ---------------------------------------------------------------------------
# Registry I/O -- one lock, one writer, quarantine on corruption
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


def _mutate_registry(
    mutator: Callable[[dict[str, dict[str, Any]]], tuple[_T, bool]]
) -> _T:
    """THE read-modify-write funnel. Every mutation of the registry file goes
    through here; nothing else may call :func:`_write_payload`.

    ``mutator`` receives the live ``jobs`` mapping and returns
    ``(result, changed)``; the write happens only when ``changed`` is true, so
    a no-op update does not rewrite the file. The lock is held across the whole
    cycle -- read, mutate and write are one step, which is precisely what the
    pre-U6 code did not have.

    Raises:
        RegistryUnavailableError: the current contents could not be read, so
            writing would destroy them. Propagated, never converted into an
            empty registry.
    """
    with _REGISTRY_LOCK:
        payload = _read_payload()
        result, changed = mutator(payload["jobs"])
        if changed:
            _write_payload(payload)
        return result


def _read_registry() -> dict[str, dict[str, Any]]:
    """Job records for READ paths. Never raises.

    An unreadable registry degrades to "no records" here (a dashboard listing
    must not 500 over a transient share lock) -- but loudly, and the state is
    disclosed by :func:`registry_health_status`. Write paths use
    :func:`_mutate_registry`, which refuses instead.
    """
    try:
        return _read_payload()["jobs"]
    except RegistryUnavailableError as exc:
        logger.error(
            "simulation_runs: reading the registry at %s failed (%s); reporting NO run history "
            "for this call. This is a read-time degradation, not a deletion -- the file is "
            "untouched and nothing will be written over it.",
            REGISTRY_PATH,
            exc,
        )
        return {}


def _read_payload() -> dict[str, Any]:
    """Parse the registry into ``{"jobs": {...}, "incidents": [...]}``.

    Quarantines and restarts on unparseable content (see
    :func:`_quarantine_and_restart`); raises :class:`RegistryUnavailableError`
    when the bytes could not be read at all.
    """
    with _REGISTRY_LOCK:
        path = REGISTRY_PATH
        if not path.exists():
            return _empty_payload()

        raw: bytes | None = None
        last_error: OSError | None = None
        for attempt in range(_READ_RETRY_ATTEMPTS):
            try:
                raw = path.read_bytes()
                break
            except OSError as exc:
                last_error = exc
                if attempt + 1 < _READ_RETRY_ATTEMPTS:
                    time.sleep(_READ_RETRY_DELAY_S)
        if raw is None:
            raise RegistryUnavailableError(
                f"Could not read {path} after {_READ_RETRY_ATTEMPTS} attempts: "
                f"{type(last_error).__name__}: {last_error}"
            )

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            return _quarantine_and_restart(raw, exc)

        normalized = _normalize_payload(payload)
        if normalized is None:
            return _quarantine_and_restart(
                raw,
                ValueError("registry payload is not a {'jobs': {...}} object"),
            )

        _remember_state(path, normalized)
        return normalized


def _write_payload(payload: dict[str, Any]) -> None:
    """THE only writer. Same-directory temp file -> fsync -> ``os.replace``.

    Why same directory: ``os.replace`` is atomic only within one volume, and on
    Windows a cross-volume rename raises outright. The temp file is therefore a
    sibling of the registry, uniquely named so a leftover from a killed process
    can never be mistaken for (or collide with) a live one.

    Why fsync: without it the rename can reach the disk before the DATA does,
    so a power loss can leave a successfully-renamed but zero-length registry
    -- the exact torn file this unit exists to prevent, just moved one step
    later. It is affordable here because writes are rare and bounded: the job
    runners persist at status/phase/seed boundaries, not per simulation step
    (see ``routers/simulations.py``'s ``_update_job`` ``should_persist``), so a
    long run costs a handful of fsyncs, not thousands.

    The directory entry itself is NOT fsynced (Windows has no portable way to,
    and pathlib exposes no directory fd). The residual window is small and its
    failure mode is the safe one: after a crash the OLD registry survives.

    On ANY failure the half-written temp file is removed and the exception
    propagates -- the live registry still holds its last good bytes, because
    it was never opened for writing in the first place.
    """
    with _REGISTRY_LOCK:
        path = REGISTRY_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp")
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        try:
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                logger.warning(
                    "simulation_runs: could not remove the temp registry file %s after a failed "
                    "write; it is inert (the live registry is untouched) but should be cleaned up.",
                    tmp_path,
                )
            raise
        _remember_state(path, payload)


def _empty_payload() -> dict[str, Any]:
    return {"jobs": {}, "incidents": []}


def _normalize_payload(payload: Any) -> dict[str, Any] | None:
    """Coerce a parsed payload into this module's shape, or ``None`` when it is
    not a registry at all (which the caller treats as corruption).

    Entry-level repairs are deliberately non-destructive: a non-dict record is
    dropped (nothing downstream can read one), and a record missing ``id`` --
    which would ``KeyError`` in ``routers/simulations.py``'s listing -- gets it
    back from its own key. A record without ``workspace_id`` is left exactly as
    it is: that is unit 1.4's legacy-default rule (:func:`record_workspace_id`),
    not damage.
    """
    if not isinstance(payload, dict):
        return None
    jobs = payload.get("jobs")
    if not isinstance(jobs, dict):
        return None

    clean_jobs: dict[str, dict[str, Any]] = {}
    for key, record in jobs.items():
        if not isinstance(record, dict):
            logger.warning(
                "simulation_runs: dropping unreadable registry entry %r (%s, not an object)",
                key,
                type(record).__name__,
            )
            continue
        if not record.get("id"):
            record = {**record, "id": str(key)}
        clean_jobs[str(key)] = record

    incidents = payload.get("incidents")
    clean_incidents = (
        [entry for entry in incidents if isinstance(entry, dict)]
        if isinstance(incidents, list)
        else []
    )
    return {"jobs": clean_jobs, "incidents": clean_incidents[-_MAX_INCIDENTS:]}


def _quarantine_and_restart(raw: bytes, exc: Exception) -> dict[str, Any]:
    """Move a corrupt registry aside, start a fresh one, record the loss.

    Never returns silently-empty: the corrupt bytes are preserved verbatim at a
    ``.corrupt-<timestamp>`` sibling (so a human -- or a later recovery tool --
    can still mine them for records), the event is logged at ERROR, and it is
    written INTO the new registry so :func:`registry_health_status` can still
    disclose it after the process that saw the corruption is gone.

    Raises:
        RegistryUnavailableError: the corrupt file could not be moved aside.
            We then refuse to write anything, because writing a fresh registry
            over it would destroy the only copy of those bytes.
    """
    path = REGISTRY_PATH
    key = str(path)
    lost = _last_known_record_count.get(key)
    try:
        quarantine_path = _quarantine_registry_file(path)
    except OSError as move_exc:
        raise RegistryUnavailableError(
            f"The registry at {path} did not parse ({type(exc).__name__}: {exc}) and could not "
            f"be moved aside for quarantine ({type(move_exc).__name__}: {move_exc}). Refusing to "
            "overwrite it -- those bytes are the only copy of this box's run history."
        ) from move_exc

    incident = {
        "code": "registry_quarantined",
        "at": _now_iso(),
        "quarantine_path": str(quarantine_path),
        "corrupt_bytes": len(raw),
        # None means "unknown", never "zero" -- see _last_known_record_count.
        "lost_record_count": lost,
        "parse_error": f"{type(exc).__name__}: {exc}",
    }
    logger.error(
        "simulation_runs: the run registry at %s is CORRUPT (%s: %s, %d bytes). It has been "
        "quarantined at %s and a fresh registry started. Records lost: %s. Nothing was deleted "
        "-- the original bytes are intact at the quarantine path.",
        path,
        type(exc).__name__,
        exc,
        len(raw),
        quarantine_path,
        lost if lost is not None else "unknown (first read since process start)",
    )

    payload = _empty_payload()
    payload["incidents"] = [incident]
    _write_payload(payload)
    return payload


def _quarantine_registry_file(path: Path) -> Path:
    """Rename ``path`` aside to a timestamped sibling and return the new path.

    A sibling (not a subdirectory, not a temp dir) so the move is a same-volume
    rename that cannot half-copy. The ``-<n>`` suffix only appears when two
    quarantines land in the same second; without it ``os.replace`` would
    overwrite -- and destroy -- the earlier corrupt file.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.name}.corrupt-{stamp}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.corrupt-{stamp}-{counter}")
        counter += 1
    os.replace(path, candidate)
    return candidate


def _remember_state(path: Path, payload: dict[str, Any]) -> None:
    """Refresh the in-memory record count and disclosure for ``path``."""
    key = str(path)
    _last_known_record_count[key] = len(payload.get("jobs") or {})
    status = _health_from_incidents(payload.get("incidents") or [])
    if status is None:
        _registry_health.pop(key, None)
    else:
        _registry_health[key] = status


def _health_from_incidents(incidents: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Turn the newest quarantine incident into the ko/en/ja disclosure."""
    quarantines = [entry for entry in incidents if entry.get("code") == "registry_quarantined"]
    if not quarantines:
        return None
    latest = quarantines[-1]
    lost = latest.get("lost_record_count")
    lost = int(lost) if isinstance(lost, int) else None
    quarantine_path = latest.get("quarantine_path")

    if lost is None:
        count_ko = "몇 건이 사라졌는지는 알 수 없습니다"
        count_en = "how many records were lost is unknown"
        count_ja = "何件が失われたかは不明です"
    else:
        count_ko = f"기록 {lost}건을 잃었습니다"
        count_en = f"{lost} record(s) were lost"
        count_ja = f"{lost}件の記録が失われました"

    return {
        "code": "registry_quarantined",
        "at": latest.get("at"),
        "quarantine_path": quarantine_path,
        "lost_record_count": lost,
        "quarantine_count": len(quarantines),
        "messages": {
            "ko": (
                f"시뮬레이션 실행 기록 파일이 손상되어 격리했습니다. {count_ko}. "
                f"새 기록 파일로 다시 시작했으며, 손상된 원본은 {quarantine_path} 에 그대로 "
                "보관되어 있습니다."
            ),
            "en": (
                f"The simulation-run registry file was corrupt and has been quarantined; "
                f"{count_en}. A fresh registry was started and the original bytes are preserved "
                f"at {quarantine_path}."
            ),
            "ja": (
                f"シミュレーション実行履歴ファイルが破損していたため隔離しました。{count_ja}。"
                f"新しい履歴ファイルで再開し、破損した原本は {quarantine_path} にそのまま"
                "保存されています。"
            ),
        },
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_color(index: int) -> str:
    palette = ["#8b5cf6", "#0ea5e9", "#14b8a6", "#f59e0b", "#ef4444", "#64748b"]
    return palette[index % len(palette)]

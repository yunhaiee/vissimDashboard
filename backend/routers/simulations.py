"""Simulation execution endpoints using VISSIM COM API."""
import asyncio
import logging
import os
import shutil
import statistics
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from config import SIMULATION_INPX_PATH
from services import workspaces
from services.datasets import get_active_dataset, get_active_inpx_path
from services.simulation_runs import (
    belongs_to_current_workspace,
    create_job_record,
    get_job_record,
    job_dir,
    list_job_records,
    update_job_record,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/simulations", tags=["simulations"])

_jobs: dict[str, dict] = {}
# Guards every read-modify-write of `_jobs[job_id]` (the worker thread
# mutates it via _update_job; request handlers read it via _job_snapshot).
# Individual dict operations are already GIL-atomic, so this is not about
# preventing a crash -- it's about (a) never handing a request handler a
# LIVE reference into a dict the worker thread might mutate again before
# jsonable_encoder finishes serializing it, and (b) making the "update, then
# publish" sequence in _update_job a single atomic step instead of several.
_jobs_lock = threading.Lock()


class SimulationUnavailableError(RuntimeError):
    pass


class SimulationParameterError(ValueError):
    pass


class SimulationIncident(BaseModel):
    link_id: int
    type: Literal["lane_closure", "full_closure", "speed_limit"]
    closed_lanes: int = 0
    total_lanes: int = 0
    speed_limit: int = 0
    duration: int = Field(3600, ge=1, le=86400)


class SimParams(BaseModel):
    name: str = "interactive_scenario"
    scenario_id: str | None = None
    sim_duration: int = Field(3600, ge=60, le=86400)
    random_seed: int = Field(42, ge=0, le=2_147_483_647)
    vehicle_inputs: dict[int, float] = {}
    signal_cycles: dict[int, int] = {}
    signal_offsets: dict[int, int] = {}
    incidents: list[SimulationIncident] = []


@router.post("")
def create_simulation(params: SimParams):
    try:
        return start_simulation_job(params)
    except SimulationUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def start_simulation_job(params: SimParams) -> dict:
    capabilities = _simulation_capabilities()
    if not capabilities["can_run"]:
        raise SimulationUnavailableError(capabilities["reason"] or "Simulation cannot run in the current environment.")

    # TOCTOU fix (unit 1.4): the source .inpx is resolved HERE, on the request
    # thread, and handed to the worker thread frozen. It used to be resolved
    # INSIDE _run_simulation, so an upload landing between "job accepted" and
    # "worker thread starts Vissim" silently ran the NEW dataset while the job
    # record was already stamped with the OLD dataset_revision -- producing a
    # finished run that is invisible in the scenario picker (revision mismatch)
    # and un-replayable, with results that belong to neither dataset's history.
    # This is exactly the pattern start_agent_simulation_job already used.
    source_inpx = _simulation_source_inpx()
    workspace_id = workspaces.current_workspace_id()

    job_id = str(uuid.uuid4())[:8]
    record = create_job_record(job_id, params.model_dump())
    _jobs[job_id] = record
    t = threading.Thread(
        target=_run_simulation, args=(job_id, params, source_inpx, workspace_id), daemon=True
    )
    t.start()
    return {
        "id": job_id,
        "status": "queued",
        "scenario_result_id": record.get("scenario_result_id"),
    }


@router.get("")
def list_simulations():
    """Jobs belonging to THIS request's workspace only.

    Both halves are filtered: the persisted registry (``list_job_records`` is
    workspace-scoped by default) and the in-memory ``_jobs`` mirror, which
    holds live progress for jobs running right now regardless of who submitted
    them.
    """
    records = {record["id"]: record for record in list_job_records()}
    with _jobs_lock:
        in_memory_snapshot = {
            job_id: dict(record)
            for job_id, record in _jobs.items()
            if belongs_to_current_workspace(record)
        }
    records.update(in_memory_snapshot)
    return sorted(records.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True)


@router.get("/capabilities")
def get_simulation_capabilities():
    return _simulation_capabilities()


def _workspace_job_snapshot(job_id: str) -> dict | None:
    """:func:`_job_snapshot` restricted to the requesting workspace.

    A job belonging to another project is reported as simply not existing --
    same 404 shape as an unknown id, so the response cannot be used to probe
    which job ids exist on the box.

    NOT applied to the ``/ws/{job_id}`` progress socket: the browser
    ``WebSocket`` API cannot attach custom headers, so that route can never
    receive ``X-Workspace-Id`` and always resolves to the default workspace.
    Filtering it would break live progress for every non-default project. The
    hole is recorded rather than papered over; it exposes progress percentages
    for a job whose 8-hex id the caller already has.
    """
    job = _job_snapshot(job_id)
    if job is None or not belongs_to_current_workspace(job):
        return None
    return job


@router.get("/{job_id}")
def get_simulation(job_id: str):
    job = _workspace_job_snapshot(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/{job_id}/results")
def get_simulation_results(job_id: str):
    job = _workspace_job_snapshot(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail="Simulation is not completed yet")

    from services.results_reader import get_runtime_results

    results = get_runtime_results(job_dir(job_id))
    if results is None:
        raise HTTPException(status_code=404, detail="No readable result database found for this simulation")

    return {
        "job_id": job_id,
        "status": job["status"],
        "results": results,
    }


@router.websocket("/ws/{job_id}")
async def simulation_ws(websocket: WebSocket, job_id: str):
    await websocket.accept()
    try:
        while True:
            job = _job_snapshot(job_id)
            if not job:
                await websocket.send_json({"error": "Job not found"})
                break

            await websocket.send_json({
                "progress": job["progress"],
                "status": job["status"],
                "error": job.get("error"),
            })
            if job["status"] in ("done", "error"):
                break
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    finally:
        await websocket.close()


def _run_simulation(job_id: str, params: SimParams, source_inpx: Path, workspace_id: str):
    ctrl = None

    # COM requires CoInitialize on every thread that uses it
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except ImportError:
        _update_job(job_id, status="error", error="pywin32 is not installed, so VISSIM COM cannot run.")
        logger.error("pythoncom not available")
        return

    # A plain threading.Thread does not inherit the request's ContextVars, so
    # the submitting workspace is re-bound explicitly here -- anything this
    # thread reaches that resolves a dataset path (the job registry, the
    # results reader) must see the workspace that SUBMITTED the job, never
    # raise, and certainly never fall back to somebody else's.
    with workspaces.bind_workspace(workspace_id):
        _run_simulation_locked(job_id, params, source_inpx)


def _run_simulation_locked(job_id: str, params: SimParams, source_inpx: Path):
    """The body of :func:`_run_simulation`, running with COM initialized on
    this thread and the submitting workspace bound."""
    import pythoncom

    ctrl = None
    # Bound before the try so the except block below can always reach it, even
    # for a failure (e.g. FileNotFoundError) that happens before
    # build_simulation_network ever runs -- see _err_context_for_job.
    runtime_network_path: Path | None = None
    try:
        from services.vissim_com import VissimController
        from services.simulation_network_builder import build_simulation_network

        # RUN-PROGRESS UI (2026-07-28): this path is always exactly one seed
        # (SimParams.random_seed is a single int), so completed_seeds/eta_s
        # only ever move at the very end -- eta_s in particular stays None
        # for this entire run (no seed has completed until the run itself
        # is over), which is correct per the "never a guessed number"
        # doctrine, not a gap. The fine-grained 0..1 `progress` field below
        # (from the existing RunSingleStep loop, unchanged) is already
        # honest, live progress for this single-seed case.
        _update_job(
            job_id,
            status="running",
            progress=0.0,
            phase="arming",
            completed_seeds=0,
            current_seed=1,
            per_seed_durations=[],
            eta_s=None,
            last_update_at=_utc_now_iso(),
        )
        ctrl = VissimController()
        # attach_to_running=False is a hard safety rule for automation: the default
        # (GetActiveObject) can attach to a human's open Vissim GUI, and loading a
        # network into it would destroy their unsaved work. Jobs always get their
        # own instance. See docs/phase2/00-EXECUTION-GUIDE.md §1 rule 3.
        ctrl.connect(visible=False, attach_to_running=False)

        if not source_inpx.exists():
            raise FileNotFoundError(f"Simulation INPX not found: {source_inpx}")

        runtime_network_path = build_simulation_network(
            source_inpx=source_inpx,
            job_id=job_id,
            scenario_id=params.scenario_id,
            speed_limits=[
                incident.model_dump()
                for incident in params.incidents
                if incident.type == "speed_limit"
            ],
        )
        logger.info("Loading simulation network from %s", runtime_network_path)
        ctrl.load_network(str(runtime_network_path))
        _validate_simulation_params(ctrl, params)
        ctrl.set_sim_duration(params.sim_duration)
        ctrl.set_random_seed(params.random_seed)
        for link_no, vol in params.vehicle_inputs.items():
            ctrl.set_vehicle_input(link_no, vol)
        for sc_no, cycle in params.signal_cycles.items():
            ctrl.set_signal_cycle(sc_no, cycle)
        for sc_no, offset in params.signal_offsets.items():
            ctrl.set_signal_offset(sc_no, offset)

        seed_start_s = time.monotonic()
        _update_job(job_id, phase="running", last_update_at=_utc_now_iso())

        total = float(params.sim_duration)
        previous_sim_sec = ctrl.get_sim_sec()
        # Run simulation step-by-step to update progress in the same thread.
        while True:
            ctrl.run_single_step()
            current_sim_sec = ctrl.get_sim_sec()

            # VISSIM may reset SimSec to 0 immediately after the last step even with NumRuns=1.
            # Treat that reset as the completion signal instead of starting over.
            if current_sim_sec < previous_sim_sec:
                logger.info(
                    "Simulation %s reached completion after SimSec reset (%s -> %s)",
                    job_id,
                    previous_sim_sec,
                    current_sim_sec,
                )
                break

            if total > 0:
                _update_job(job_id, progress=min(current_sim_sec / total, 0.999))
            else:
                _update_job(job_id, progress=0.0)

            if current_sim_sec >= total or ctrl.is_sim_finished():
                break

            previous_sim_sec = current_sim_sec

        seed_duration_s = time.monotonic() - seed_start_s
        _update_job(
            job_id,
            progress=1.0,
            status="done",
            phase="done",
            completed_seeds=1,
            current_seed=None,
            per_seed_durations=[seed_duration_s],
            # Never a guessed number, even at the very end -- see this
            # function's own comment: with total_seeds=1, no seed EVER
            # completes before the job itself is terminal, so there is
            # nothing left to estimate. 0.0 here just means "nothing
            # remains", not an extrapolation.
            eta_s=0.0,
            last_update_at=_utc_now_iso(),
        )
        logger.info("Simulation %s completed", job_id)
    except Exception as exc:
        logger.exception("Simulation %s failed", job_id)
        _update_job(
            job_id,
            status="error",
            phase="error",
            error=_format_simulation_error(exc, runtime_network_path),
            last_update_at=_utc_now_iso(),
        )
    finally:
        try:
            if ctrl is not None:
                ctrl.disconnect()
        except Exception:
            pass
        pythoncom.CoUninitialize()


def _simulation_source_inpx() -> Path:
    if os.getenv("VISSIM_SIMULATION_INPX_PATH"):
        return SIMULATION_INPX_PATH
    return get_active_inpx_path()


def _simulation_capabilities() -> dict:
    dataset = get_active_dataset()
    # Unit #68: the runner only ever reads ONE artifact -- the resolved source
    # .inpx (see _run_simulation_locked -> build_simulation_network ->
    # build_scenario_tree, which parses base_inpx directly and touches
    # project.vissimpdb/Scenarios ONLY when a scenario_id is given, gracefully
    # via get_project_scenario -- never a hard requirement of the base run).
    # So the honest gate is "an UPLOADED dataset, any kind" -- zip or bare
    # inpx -- not "specifically a zip". A bare-.inpx upload used to be refused
    # here with a message that named .vissimpdb, which nothing in this path
    # ever reads for the default (no scenario_id) run.
    has_uploaded_dataset = dataset.get("source") == "uploaded"
    pywin32_available, pywin32_error = _check_pywin32()
    is_windows = _is_windows()
    can_prepare_simulation = is_windows and pywin32_available
    source_inpx = _simulation_source_inpx()
    source_exists = source_inpx.exists()
    reasons: list[str] = []

    if not has_uploaded_dataset:
        reasons.append("Upload a VISSIM dataset (INPX file or project ZIP) before running simulation.")
    if not is_windows:
        reasons.append("VISSIM COM can run only on Windows with PTV Vissim installed.")
    if not pywin32_available:
        reasons.append("pywin32/pythoncom is not available in this backend environment.")
    if not source_exists:
        reasons.append(f"Simulation INPX not found: {source_inpx}")

    return {
        "can_run": len(reasons) == 0,
        "can_prepare_simulation": can_prepare_simulation,
        "reason": " ".join(reasons) if reasons else None,
        "environment": sys.platform,
        "pywin32_available": pywin32_available,
        "pywin32_error": pywin32_error,
        "source_inpx": str(source_inpx),
        "source_inpx_exists": source_exists,
        "dataset_source": dataset.get("source"),
        "dataset_kind": dataset.get("kind"),
        "dataset_filename": dataset.get("filename"),
        # RENAMED from `requires_uploaded_zip` (unit #68): that field was
        # always `True` no matter what, but its NAME asserted "must be a zip"
        # specifically -- a lie once an uploaded bare inpx became runnable
        # too. This field still just means "an uploaded dataset is required",
        # true for both kinds; a client that wants to know WHICH kinds count
        # reads `dataset_kind` / `dataset_source` above.
        "requires_uploaded_dataset": True,
        "requires_local_windows_backend": True,
    }


def _is_windows() -> bool:
    """A dedicated seam for the "are we on Windows" capability check --
    mirrors ``_check_pywin32`` right below (also its own overridable seam,
    for the exact same reason: a test that needs to simulate "not Windows"
    should monkeypatch THIS function, never flip the real ``os.name``. The
    real ``os`` module is a process-wide singleton; on a live Windows box,
    ``os.name = "posix"`` also flips what ``pathlib.Path(...)`` builds
    everywhere else in the process (including inside pytest's own failure-
    report rendering), which crashes with
    ``NotImplementedError: cannot instantiate 'PosixPath'`` and obscures the
    real assertion diff -- measured 2026-08-26 (unit #68 refuter pass)."""
    return os.name == "nt"


def _check_pywin32() -> tuple[bool, str | None]:
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except Exception as exc:
        return False, str(exc)
    return True, None


def _validate_simulation_params(ctrl, params: SimParams) -> None:
    if params.vehicle_inputs:
        available_vehicle_inputs = ctrl.available_vehicle_input_nos()
        missing_vehicle_inputs = sorted(set(params.vehicle_inputs) - set(available_vehicle_inputs))
        if missing_vehicle_inputs:
            raise SimulationParameterError(
                "Vehicle input "
                f"{_format_number_list(missing_vehicle_inputs)} does not exist in the loaded VISSIM network. "
                f"Available vehicle inputs: {_format_number_list(available_vehicle_inputs)}. "
                "Change the prompt to use one of the available vehicle input numbers."
            )

    requested_signal_controllers = set(params.signal_cycles) | set(params.signal_offsets)
    if requested_signal_controllers:
        available_signal_controllers = ctrl.available_signal_controller_nos()
        missing_signal_controllers = sorted(requested_signal_controllers - set(available_signal_controllers))
        if missing_signal_controllers:
            raise SimulationParameterError(
                "Signal controller "
                f"{_format_number_list(missing_signal_controllers)} does not exist in the loaded VISSIM network. "
                f"Available signal controllers: {_format_number_list(available_signal_controllers)}. "
                "Change the prompt to use one of the available signal controller numbers."
            )


def _format_number_list(values, limit: int = 20) -> str:
    numbers = sorted(int(value) for value in values)
    if not numbers:
        return "none found"
    shown = numbers[:limit]
    suffix = f", ... (+{len(numbers) - limit} more)" if len(numbers) > limit else ""
    return ", ".join(str(value) for value in shown) + suffix


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _estimate_eta_s(per_seed_durations: list[float], remaining_seeds: int) -> float | None:
    """ETA (seconds) for the remaining seeds of a job, or ``None`` when no
    seed has completed yet.

    Honesty over false precision (this project's doctrine, applied to
    progress UI instead of a write): before the first seed finishes there is
    no real duration sample to extrapolate from, so this returns ``None``
    rather than a made-up number -- callers must render that as "calculating"
    text, never as a guessed value. Once at least one seed has completed,
    uses the MEDIAN of completed per-seed durations (robust to one
    unusually slow/fast seed -- e.g. a cold Vissim COM launch on the very
    first seed skewing a mean) times however many seeds remain.
    """
    if not per_seed_durations:
        return None
    if remaining_seeds <= 0:
        return 0.0
    typical_seed_s = statistics.median(per_seed_durations)
    return typical_seed_s * remaining_seeds


def _update_job(job_id: str, **updates):
    with _jobs_lock:
        record = _jobs.get(job_id) or get_job_record(job_id)
        if record is None:
            return
        if updates.get("status") == "running" and not record.get("started_at"):
            updates["started_at"] = _utc_now_iso()
        if updates.get("status") in {"done", "error"}:
            updates["completed_at"] = _utc_now_iso()
        # Every value this module ever passes in `updates` is a freshly
        # constructed object (a new list for per_seed_durations, a new str
        # for phase/last_update_at, ...), never a container mutated in
        # place -- so a snapshot taken by a concurrent reader right after
        # this dict.update() (see _job_snapshot) can safely hold onto
        # whatever nested list/dict it copied a reference to; a later
        # _update_job call here will only ever swap that key to a NEW
        # object, never mutate the old one out from under the reader.
        record.update(updates)
        _jobs[job_id] = record
        should_persist = (
            "status" in updates
            or "error" in updates
            or "started_at" in updates
            or "completed_at" in updates
            or "phase" in updates
            or "completed_seeds" in updates
            or updates.get("progress") in {0.0, 1.0}
        )
        persist_updates = dict(updates) if should_persist else None
    # File persistence happens outside the in-memory lock -- it's disk I/O
    # against a SEPARATE serialization (services.simulation_runs' own
    # read-JSON/write-JSON registry file), not the `_jobs` dict this lock
    # protects, so there is no correctness reason to hold `_jobs_lock` for
    # its duration.
    if persist_updates is not None:
        update_job_record(job_id, **persist_updates)


def _job_snapshot(job_id: str) -> dict | None:
    """Thread-safe read of one job record for a request handler: a shallow
    COPY when the job is still in the in-memory `_jobs` registry (the worker
    thread mutates that dict directly), or the registry-file record
    otherwise (services.simulation_runs.get_job_record already returns a
    freshly-parsed dict on every call, so no aliasing risk there).

    Returning a copy (not the live `_jobs[job_id]` dict itself) matters
    because FastAPI's response serialization happens AFTER this function
    returns, on the same request-handling thread but with no lock held --
    a live reference could be observed mid-mutation, or (worse, pre-fix)
    have `dict.update()` change its key set out from under
    `jsonable_encoder`'s iteration. See _update_job's own comment for why a
    shallow copy is sufficient (every value swap uses a fresh object).
    """
    with _jobs_lock:
        record = _jobs.get(job_id)
        if record is not None:
            return dict(record)
    return get_job_record(job_id)


#: How many of a failed run's own .err "Error"/"critical error" lines to fold
#: into the job's error field. The rest stay on disk at the path this quotes —
#: named rather than reproduced in full, the same "count the rest, never hide
#: it" rule this codebase already applies to skipped ways / duplicate OSM ways
#: (services/genesis/draft.py).
MAX_ERR_LINES_IN_JOB_ERROR = 20


def _err_error_lines(text: str) -> list[str]:
    """The lines of an .err PTV Vissim marks as a failure.

    Every line observed in a real .err (2026-08-27, SIM_bba7f4eb: 59 ``Error``
    lines plus one ``critical error`` summary line — see
    runtime/simulations/bba7f4eb/current.bba7f4eb.interactive_001.err) carries
    its severity as the first tab-separated field, right-padded with spaces.
    Matched on that field ending in "error" (case-insensitive) rather than on
    the whole line, so a stray ``Warning`` row — which did not stop the run —
    is not folded in here and does not bury the lines that did.
    """
    out = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        head = stripped.split("\t", 1)[0].strip().lower()
        if head.endswith("error"):
            out.append(stripped)
    return out


def _err_context_for_job(network_path: Path | None) -> str:
    """The failed run's own .err content, formatted for the job error field —
    or ``""`` when there is nothing to attach (no working network path yet,
    directory unreadable, no .err beside it, or an .err with no Error line in
    it).

    This is the ONLY consumer of the .err for an interactive run today: before
    this, the job's error field carried nothing but the raw COM exception text
    (``_format_simulation_error``), and the .err PTV Vissim itself writes
    beside the working .inpx — which names the actual reason, e.g. "Nodes 1:
    ... and 65: ... are overlapping." for a dense draft's node boxes — was
    silently discarded (2026-08-27, docs/phase2/DECISIONS.md "라이브 완주 검증
    적발 2호"). Reuses services.vissim_open_probe.read_err_files, the same
    verbatim-.err reader V1's LoadOracle probe already relies on, rather than a
    second .err reader that could drift from it.
    """
    if network_path is None:
        return ""
    directory = network_path.parent
    if not directory.is_dir():
        return ""
    from services.vissim_open_probe import read_err_files

    found = read_err_files(directory, pre_existing=set())
    stem = network_path.stem
    blocks: list[str] = []
    for entry in found.get("files", []):
        name = str(entry.get("name", ""))
        # Vissim names a run's .err "<network stem>_NNN.err" (NNN = run
        # number, always "001" for the single-seed runs this job type ever
        # submits) — restrict to files that are plausibly THIS run's, so an
        # unrelated .err left in the same job directory by an earlier attempt
        # is never attributed to this one.
        if not (name.startswith(f"{stem}_") and name.endswith(".err")):
            continue
        lines = _err_error_lines(str(entry.get("text", "")))
        if not lines:
            continue
        shown = lines[:MAX_ERR_LINES_IN_JOB_ERROR]
        remaining = len(lines) - len(shown)
        header = f"VISSIM error log ({name}, {directory / name}):"
        body = "\n".join(f"  {line}" for line in shown)
        block = f"{header}\n{body}"
        if remaining > 0:
            block += f"\n  ... and {remaining} more line(s) in the file above."
        blocks.append(block)
    return "\n\n".join(blocks)


def _format_simulation_error(exc: Exception, network_path: Path | None = None) -> str:
    message = str(exc)
    lowered = message.lower()

    if isinstance(exc, SimulationParameterError) or exc.__class__.__name__ == "VissimObjectNotFoundError":
        formatted = message
    elif "scenario management" in lowered and "edit scenario" in lowered:
        formatted = (
            "VISSIM still detected scenario-management mode in the generated runtime INPX. "
            "This source network likely requires a manually exported standalone scenario INPX. "
            "Point VISSIM_SIMULATION_INPX_PATH to that exported file if the problem continues. "
            f"Current simulation path: {_simulation_source_inpx()}"
        )
    elif isinstance(exc, FileNotFoundError):
        formatted = message
    elif "itembykey" in lowered and "object does not exist" in lowered:
        formatted = (
            "The prompt referenced a VISSIM object number that does not exist in the loaded network. "
            "Ask for the current VISSIM COM status or use one of the available vehicle input or signal controller numbers."
        )
    else:
        formatted = f"Simulation failed: {message}"

    err_context = _err_context_for_job(network_path)
    return f"{formatted}\n\n{err_context}" if err_context else formatted


# ---------------------------------------------------------------------------
# M2.4: agent-run job submission — RunContinuous, explicit source .inpx
#
# Distinct from start_simulation_job/_run_simulation above (which always
# resolves the source network from the globally ACTIVE dataset and runs it
# via a RunSingleStep loop for live interactive progress). An agent run
# submits the calling turn's OWN session working copy (never the active
# dataset — see services.agent.tools_run's module docstring) and uses
# RunContinuous throughout: docs/phase2/00-EXECUTION-GUIDE.md SS3.1,
# RunSingleStep over COM costs minutes for 60 sim-seconds and is reserved
# for a possible future live-streaming feature, never batch/agent runs.
# Reuses this module's existing job registry, _update_job bookkeeping, and
# _check_pywin32/_format_simulation_error helpers — the "existing simulation
# job queue" docs/phase2/DECISIONS.md's M2.3 entry says M2.4 runs must stay
# on, not a parallel one.
# ---------------------------------------------------------------------------


def start_agent_simulation_job(
    *, source_inpx: Path, duration_s: int, random_seeds: list
) -> dict:
    """Submit one agent-driven simulation job against an EXPLICIT source
    .inpx to the same job registry/threading pattern ``start_simulation_job``
    uses, but via ``RunContinuous``.

    ``random_seeds`` is ONE job with N sequential sub-runs (one per seed,
    each isolated in its own ``seed_NN`` subdirectory under the job's work
    dir) rather than N separate jobs — ``results_reader.get_runtime_results``
    already averages across every ``*.db`` it finds under a work dir
    (``rglob("*.db")``), so this aggregates multi-seed KPIs for free with no
    new aggregation logic. A ``None`` entry in ``random_seeds`` means "don't
    override — run with whatever RandSeed the source network already has."
    """
    pywin32_available, pywin32_error = _check_pywin32()
    if not (os.name == "nt" and pywin32_available):
        raise SimulationUnavailableError(
            pywin32_error or "VISSIM COM can run only on Windows with pywin32 available."
        )
    if not source_inpx.exists():
        raise SimulationUnavailableError(f"Source network not found: {source_inpx}")
    if not random_seeds:
        raise SimulationParameterError("random_seeds must contain at least one entry (or None).")

    # Same freeze-at-enqueue discipline as start_simulation_job: the caller
    # already supplies an explicit source .inpx (this turn's session working
    # copy), and the workspace is captured here on the SUBMITTING thread so
    # the runner thread can re-bind it (ContextVars do not cross threads).
    workspace_id = workspaces.current_workspace_id()

    job_id = str(uuid.uuid4())[:8]
    record = create_job_record(
        job_id,
        {
            "name": f"agent_run_{job_id}",
            "source": "agent",
            "sim_duration": duration_s,
            "random_seeds": random_seeds,
        },
    )
    _jobs[job_id] = record
    t = threading.Thread(
        target=_run_agent_simulation_job,
        args=(job_id, source_inpx, duration_s, random_seeds, workspace_id),
        daemon=True,
    )
    t.start()
    return {
        "id": job_id,
        "status": "queued",
        "scenario_result_id": record.get("scenario_result_id"),
    }


def _clear_stale_result_dbs(directory: Path) -> list[Path]:
    """Delete every ``*.db``/``*.fzp`` already present under ``directory``
    and return what was removed.

    A pre-existing result database (or, since the trajectory-replay unit,
    vehicle-record export) must never be read as THIS run's result
    (docs/phase2/02-ROADMAP.md M2.4). A fresh, uuid-keyed job/seed directory
    should never already contain one, but this is enforced rather than
    merely assumed — a stray leftover (e.g. a crashed prior attempt that
    reused a directory) is deleted before Vissim ever runs, not discovered
    (and silently misattributed) after.
    """
    removed: list[Path] = []
    if not directory.exists():
        return removed
    for pattern in ("*.db", "*.fzp"):
        for stale_file in directory.rglob(pattern):
            try:
                stale_file.unlink()
                removed.append(stale_file)
            except OSError:
                logger.warning("Could not remove stale result file %s", stale_file)
    if removed:
        logger.warning(
            "Removed %d stale result file(s) from %s before running", len(removed), directory
        )
    return removed


def _trajectory_max_s() -> float:
    """The duration cap (sim seconds) under which an agent run also arms
    vehicle-record (.fzp) output for trajectory replay, so a long batch
    study (e.g. a 2-hour, 5-seed run) never silently writes gigabytes of
    per-vehicle rows. Read fresh on every call (not cached at import time)
    so tests/ops can override ``AGENT_RUN_TRAJECTORY_MAX_S`` without a
    process restart, mirroring ``services/agent/structured.py``'s
    ``ladder_ceiling_seconds()`` / ``routers/assistant.py``'s
    ``_stream_ping_interval_seconds()`` env-fallback pattern.
    """
    raw = os.getenv("AGENT_RUN_TRAJECTORY_MAX_S", "").strip()
    if not raw:
        return 1800.0
    try:
        return float(raw)
    except ValueError:
        return 1800.0


def _trajectory_resolution() -> int:
    """Vehicle-record write-cadence divisor (every Nth simulation timestep
    is written -- see ``services.vissim_com``'s ``DEFAULT_VEHICLE_RECORD_RESOLUTION``
    and its module-level SIZE FINDING comment: a real, moderately busy
    network at the network's own default resolution produced a .fzp whose
    resolved JSON payload the browser outright aborted). Env-overridable
    (``AGENT_RUN_TRAJECTORY_RESOLUTION``) for the same reason
    :func:`_trajectory_max_s` is -- ops/tests can tune it without a process
    restart.
    """
    from services.vissim_com import DEFAULT_VEHICLE_RECORD_RESOLUTION

    raw = os.getenv("AGENT_RUN_TRAJECTORY_RESOLUTION", "").strip()
    if not raw:
        return DEFAULT_VEHICLE_RECORD_RESOLUTION
    try:
        return max(int(raw), 1)
    except ValueError:
        return DEFAULT_VEHICLE_RECORD_RESOLUTION


def _read_result_table_names(db_path: Path) -> set[str]:
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    return {str(row[0]).upper() for row in rows}


def _run_agent_simulation_job(
    job_id: str,
    source_inpx: Path,
    duration_s: int,
    random_seeds: list,
    workspace_id: str | None = None,
) -> None:
    """M2.4 job runner thread body: RunContinuous once per seed, each seed
    isolated in its own subdirectory so results_reader's existing multi-db
    averaging aggregates KPIs across seeds with no new logic. Marks the job
    "done" only after every seed's result database is verified to carry a
    SIMULATIONRUN row (the run-completed artifact — never SimSec, which
    resets to 0 the moment a run ends; see
    docs/phase2/00-EXECUTION-GUIDE.md SS3.1).

    Each seed is pinned to exactly ONE Vissim run: NumRuns is set to 1 and
    read back before RunContinuous, so "one seed" means one result database
    no matter what the network's own ``<simulation numRuns=...>`` said (see
    the measured finding quoted at that call site).

    ``workspace_id`` re-binds the SUBMITTING request's workspace on this
    thread (ContextVars do not cross ``threading.Thread`` boundaries) so the
    job registry writes and any dataset resolution below stay inside the
    project that asked for the run. Optional only so an existing test that
    calls this runner directly keeps working; production always passes it.
    """
    if workspace_id is not None:
        with workspaces.bind_workspace(workspace_id):
            _run_agent_simulation_job_locked(job_id, source_inpx, duration_s, random_seeds)
        return
    _run_agent_simulation_job_locked(job_id, source_inpx, duration_s, random_seeds)


def _run_agent_simulation_job_locked(
    job_id: str, source_inpx: Path, duration_s: int, random_seeds: list
) -> None:
    ctrl = None
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except ImportError:
        _update_job(
            job_id, status="error", error="pywin32 is not installed, so VISSIM COM cannot run."
        )
        logger.error("pythoncom not available")
        return

    try:
        from services.vissim_com import VissimController

        # RUN-PROGRESS UI (2026-07-28): progress bookkeeping is updated only
        # at SEED BOUNDARIES and phase transitions -- never by sampling
        # inside RunContinuous (there is no COM hook for that without
        # SimBreakAt-chunking the run, which docs/phase2/00-EXECUTION-GUIDE.md
        # SS3.1 and this milestone's brief both forbid: perturbing run
        # behavior for a progress bar is unacceptable). per_seed_durations
        # is local to this thread (only the worker thread ever appends to
        # it) and republished wholesale via _update_job on each boundary --
        # see _update_job's own comment on why that's safe to read
        # concurrently without a lock on the frontend-facing side.
        _update_job(
            job_id,
            status="running",
            progress=0.0,
            phase="arming",
            completed_seeds=0,
            current_seed=None,
            per_seed_durations=[],
            eta_s=None,
            last_update_at=_utc_now_iso(),
        )
        work_dir = job_dir(job_id)
        work_dir.mkdir(parents=True, exist_ok=True)
        all_tables: set[str] = set()
        trajectory_paths: list[str] = []
        trajectory_max_s = _trajectory_max_s()
        per_seed_durations: list[float] = []
        total_seeds = len(random_seeds)

        for index, seed in enumerate(random_seeds):
            seed_start_s = time.monotonic()
            _update_job(
                job_id,
                phase="arming",
                current_seed=index + 1,
                last_update_at=_utc_now_iso(),
            )
            seed_dir = work_dir / f"seed_{index:02d}"
            seed_dir.mkdir(parents=True, exist_ok=True)
            _clear_stale_result_dbs(seed_dir)

            run_inpx = seed_dir / source_inpx.name
            shutil.copyfile(source_inpx, run_inpx)

            ctrl = VissimController()
            try:
                # attach_to_running=False is a hard safety rule for automation:
                # the default (GetActiveObject) can attach to a human's open
                # Vissim GUI, destroying their unsaved work. See
                # docs/phase2/00-EXECUTION-GUIDE.md SS1 rule 3.
                ctrl.connect(visible=False, attach_to_running=False)
                ctrl.load_network(str(run_inpx))
                ctrl.set_sim_duration(duration_s)
                if seed is not None:
                    ctrl.set_random_seed(seed)

                # NumRuns=1 -- the third member of the run contract THIS
                # runner owns, alongside SimPeriod and RandSeed above.
                #
                # MEASURED 2026-08-07 (runtime/calibration-loop-live-check/
                # artifacts/FINDING_agent_run_numRuns_blocker.json): the real
                # consultant export carries <simulation numRuns="5"
                # randSeed="42" randSeedIncr="123">, RunContinuous honours
                # NumRuns, and ONE seed directory came back with FIVE result
                # databases (seeds 42/165/288/411/534 = randSeed +
                # randSeedIncr) while the tool envelope still said
                # random_seeds=[42]. Setting RandSeed on the line above while
                # leaving NumRuns at 5 does not merely add runs: it makes the
                # seed this job DECLARES a lie, and the evidence readers sum
                # volumes across every .db in the run dir, so the KPIs come
                # back N-fold wrong rather than merely noisier.
                #
                # services/agent/state.py's _force_single_run normalizes this
                # in the working copy at clone time and is the primary seam
                # (it is offline-verifiable and covers every consumer of the
                # working copy, not just runs). This is the SECOND,
                # unconditional seam, kept because this runner accepts ANY
                # source .inpx -- a raw path from a live-check tool, a future
                # non-ArtifactManager caller, or a file whose XML omits
                # numRuns entirely and inherits it from elsewhere -- and is
                # the last point before RunContinuous.
                #
                # Fail-closed on a bad set or read-back, exactly like the
                # evaluation configuration below and unlike the best-effort
                # trajectory arming: a run with the wrong NumRuns produces
                # silently wrong numbers, which docs/phase2/
                # 00-EXECUTION-GUIDE.md SS4 ranks strictly worse than a
                # refused run.
                try:
                    ctrl.vissim.Simulation.SetAttValue("NumRuns", 1)
                    num_runs_readback = int(ctrl.vissim.Simulation.AttValue("NumRuns"))
                except Exception as exc:
                    raise RuntimeError(
                        "Could not force NumRuns=1 before running seed "
                        f"{seed!r} (index {index}): {exc}"
                    ) from exc
                if num_runs_readback != 1:
                    raise RuntimeError(
                        f"NumRuns read back as {num_runs_readback} after being set to 1 for "
                        f"seed {seed!r} (index {index}) -- refusing to run: Vissim would write "
                        f"{num_runs_readback} result databases for this ONE requested run and "
                        "their volumes would be summed into wrong KPIs."
                    )

                # Evaluations are not retroactive (docs/phase2/
                # 00-EXECUTION-GUIDE.md SS3.1) -- must run after load_network,
                # before RunContinuous, and once per seed: each seed loop
                # iteration gets its OWN VissimController/load_network (never
                # a persisted-across-seeds instance), so a loaded network's
                # evaluation config never carries over from a prior seed.
                # Without this, a completed run's result .db contains ONLY
                # the SIMULATIONRUN table (live-verified on this network at
                # every duration tried) -- a silent KPI-less "success" is
                # exactly the failure mode the guardrail doctrine forbids, so
                # a configuration failure here is never swallowed: it
                # propagates out of this try block as a real job error
                # (caught by the outer except below), never a silent run.
                try:
                    eval_config = ctrl.configure_network_performance_evaluation(
                        duration_s=duration_s
                    )
                except Exception as exc:
                    raise RuntimeError(
                        "Could not configure network-performance evaluation for seed "
                        f"{seed!r} (index {index}): {exc}"
                    ) from exc
                logger.info(
                    "Seed %d (job %s): network-performance evaluation configured: %s",
                    index,
                    job_id,
                    eval_config,
                )

                # Vehicle-record (.fzp) output -- the trajectory-REPLAY data
                # source (extends Phase 3's playback to real completed agent
                # runs), separate from the four KPI evaluation types above.
                # GUARDED by duration (AGENT_RUN_TRAJECTORY_MAX_S, default
                # 1800s) so a long batch study never silently writes
                # gigabytes of per-vehicle rows (~1.46MB/60s observed live
                # against Urban Intersection Beijing.CN via
                # tools/vehicle_record_probe.py -- see docs/phase2/
                # DECISIONS.md). UNLIKE network-performance evaluation above,
                # a configuration failure here is deliberately NON-FATAL:
                # KPIs are this job's primary deliverable, trajectory replay
                # is a best-effort bonus -- log a warning and continue with
                # trajectory_configured=False rather than failing the whole
                # run.
                trajectory_configured = False
                if duration_s <= trajectory_max_s:
                    try:
                        vehrec_config = ctrl.configure_vehicle_record_evaluation(
                            duration_s=duration_s, resolution=_trajectory_resolution()
                        )
                        trajectory_configured = True
                        logger.info(
                            "Seed %d (job %s): vehicle-record evaluation configured: %s",
                            index,
                            job_id,
                            vehrec_config,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Seed %d (job %s): could not arm vehicle-record (.fzp) output -- "
                            "continuing without trajectory recording for this seed: %s",
                            index,
                            job_id,
                            exc,
                        )
                else:
                    logger.info(
                        "Seed %d (job %s): duration_s=%s exceeds AGENT_RUN_TRAJECTORY_MAX_S=%s -- "
                        "skipping trajectory recording for this seed.",
                        index,
                        job_id,
                        duration_s,
                        trajectory_max_s,
                    )

                # RunContinuous is a single COM call that runs to SimPeriod
                # internally (~7.3x realtime observed) — RunSingleStep in a
                # Python loop costs minutes for 60 sim-seconds and is
                # reserved for a future live-streaming feature, never
                # batch/agent runs (docs/phase2/00-EXECUTION-GUIDE.md SS3.1).
                # phase="running" brackets the ONE COM call that actually
                # advances sim time -- there is no honest way to sample
                # progress WITHIN it (see this function's own comment above
                # the seed loop), so "running" simply covers "somewhere
                # between 0% and 100% of this seed", nothing finer.
                _update_job(job_id, phase="running", last_update_at=_utc_now_iso())
                ctrl.vissim.Simulation.RunContinuous()
                _update_job(job_id, phase="collecting", last_update_at=_utc_now_iso())

                db_paths = sorted(seed_dir.rglob("*.db"), key=lambda p: p.stat().st_mtime)
                if not db_paths:
                    raise RuntimeError(
                        f"RunContinuous finished but no results .db was written for seed "
                        f"{seed!r} (index {index})."
                    )
                tables = _read_result_table_names(db_paths[-1])
                if "SIMULATIONRUN" not in tables:
                    raise RuntimeError(
                        f"Results database for seed {seed!r} (index {index}) has no "
                        "SIMULATIONRUN row -- the run did not complete."
                    )
                all_tables |= tables

                if trajectory_configured:
                    fzp_paths = sorted(seed_dir.rglob("*.fzp"), key=lambda p: p.stat().st_mtime)
                    if fzp_paths:
                        trajectory_paths.append(str(fzp_paths[-1]))
                    else:
                        # Armed but nothing materialized -- treat as "not
                        # recorded" for this seed rather than raising: the
                        # run itself (proven above via the SIMULATIONRUN
                        # row) is still a genuine success, and KPIs remain
                        # available regardless.
                        logger.warning(
                            "Seed %d (job %s): vehicle-record output was armed but no .fzp "
                            "file was found after the run -- not recorded for this seed.",
                            index,
                            job_id,
                        )
            finally:
                try:
                    ctrl.disconnect()
                except Exception:
                    logger.exception(
                        "Error disconnecting agent-run Vissim instance (job %s, seed index %d)",
                        job_id,
                        index,
                    )
                ctrl = None

            # Seed boundary: this seed is genuinely finished (COM already
            # disconnected above) -- the one honest, zero-perturbation point
            # to record its real duration and recompute eta_s from actual
            # completed-seed samples (never a guess; see _estimate_eta_s).
            seed_duration_s = time.monotonic() - seed_start_s
            per_seed_durations.append(seed_duration_s)
            completed_seeds = index + 1
            remaining_seeds = total_seeds - completed_seeds
            _update_job(
                job_id,
                progress=completed_seeds / total_seeds,
                completed_seeds=completed_seeds,
                current_seed=None,
                per_seed_durations=list(per_seed_durations),
                eta_s=_estimate_eta_s(per_seed_durations, remaining_seeds),
                last_update_at=_utc_now_iso(),
            )

        _update_job(
            job_id,
            status="done",
            progress=1.0,
            phase="done",
            current_seed=None,
            eta_s=0.0,
            last_update_at=_utc_now_iso(),
            result_tables=sorted(all_tables),
            # Reachable only when every seed's configure_network_performance_evaluation
            # call above succeeded (any failure raises out of the seed loop and lands
            # in the except-block below as a job error, never a silent "done") -- so
            # this is trivially True here, not a fresh COM read. Kept explicit on the
            # job record (rather than implied by "status == done") so tools_run.py's
            # run_simulation can state it in the tool envelope without re-deriving it.
            evaluation_configured=True,
            # trajectory_recorded/trajectory_paths: the vehicle-trajectory
            # replay data source (extends Phase 3 playback to real completed
            # agent runs). Unlike evaluation_configured, this is genuinely
            # best-effort -- False whenever duration_s exceeded
            # AGENT_RUN_TRAJECTORY_MAX_S or arming/writing the .fzp failed
            # for every seed (logged as warnings above, never a job
            # failure). trajectory_paths carries one absolute path per seed
            # that actually recorded one; the frames endpoint
            # (routers/trajectories.py) reads only trajectory_paths[0] --
            # trajectories across seeds cannot be meaningfully averaged the
            # way KPI tables are, so replay shows one representative run.
            trajectory_recorded=bool(trajectory_paths),
            trajectory_paths=trajectory_paths,
        )
        logger.info("Agent simulation job %s completed (%d seed run(s))", job_id, len(random_seeds))
    except Exception as exc:
        logger.exception("Agent simulation job %s failed", job_id)
        _update_job(
            job_id,
            status="error",
            phase="error",
            error=_format_simulation_error(exc),
            last_update_at=_utc_now_iso(),
        )
    finally:
        try:
            if ctrl is not None:
                ctrl.disconnect()
        except Exception:
            pass
        pythoncom.CoUninitialize()

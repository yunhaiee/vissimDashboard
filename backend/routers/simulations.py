"""Simulation execution endpoints using VISSIM COM API."""
import asyncio
import logging
import os
import shutil
import sys
import threading
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from config import SIMULATION_INPX_PATH
from services.datasets import get_active_dataset, get_active_inpx_path
from services.simulation_runs import (
    create_job_record,
    get_job_record,
    job_dir,
    list_job_records,
    update_job_record,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/simulations", tags=["simulations"])

_jobs: dict[str, dict] = {}


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

    job_id = str(uuid.uuid4())[:8]
    record = create_job_record(job_id, params.model_dump())
    _jobs[job_id] = record
    t = threading.Thread(target=_run_simulation, args=(job_id, params), daemon=True)
    t.start()
    return {
        "id": job_id,
        "status": "queued",
        "scenario_result_id": record.get("scenario_result_id"),
    }


@router.get("")
def list_simulations():
    records = {record["id"]: record for record in list_job_records()}
    records.update(_jobs)
    return sorted(records.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True)


@router.get("/capabilities")
def get_simulation_capabilities():
    return _simulation_capabilities()


@router.get("/{job_id}")
def get_simulation(job_id: str):
    job = _jobs.get(job_id) or get_job_record(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/{job_id}/results")
def get_simulation_results(job_id: str):
    job = _jobs.get(job_id) or get_job_record(job_id)
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
            job = _jobs.get(job_id) or get_job_record(job_id)
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


def _run_simulation(job_id: str, params: SimParams):
    ctrl = None

    # COM requires CoInitialize on every thread that uses it
    try:
        import pythoncom

        pythoncom.CoInitialize()
    except ImportError:
        _update_job(job_id, status="error", error="pywin32 is not installed, so VISSIM COM cannot run.")
        logger.error("pythoncom not available")
        return

    try:
        from services.vissim_com import VissimController
        from services.simulation_network_builder import build_simulation_network

        _update_job(job_id, status="running", progress=0.0)
        ctrl = VissimController()
        # attach_to_running=False is a hard safety rule for automation: the default
        # (GetActiveObject) can attach to a human's open Vissim GUI, and loading a
        # network into it would destroy their unsaved work. Jobs always get their
        # own instance. See docs/phase2/00-EXECUTION-GUIDE.md §1 rule 3.
        ctrl.connect(visible=False, attach_to_running=False)

        source_inpx = _simulation_source_inpx()
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

        _update_job(job_id, progress=1.0, status="done")
        logger.info("Simulation %s completed", job_id)
    except Exception as exc:
        logger.exception("Simulation %s failed", job_id)
        _update_job(job_id, status="error", error=_format_simulation_error(exc))
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
    has_uploaded_zip = dataset.get("source") == "uploaded" and dataset.get("kind") == "zip"
    pywin32_available, pywin32_error = _check_pywin32()
    is_windows = os.name == "nt"
    can_prepare_simulation = is_windows and pywin32_available
    source_inpx = _simulation_source_inpx()
    source_exists = source_inpx.exists()
    reasons: list[str] = []

    if not has_uploaded_zip:
        reasons.append("Upload a VISSIM zip dataset before running simulation.")
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
        "requires_uploaded_zip": True,
        "requires_local_windows_backend": True,
    }


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


def _update_job(job_id: str, **updates):
    record = _jobs.get(job_id) or get_job_record(job_id)
    if record is None:
        return
    if updates.get("status") == "running" and not record.get("started_at"):
        from datetime import datetime, timezone

        updates["started_at"] = datetime.now(timezone.utc).isoformat()
    if updates.get("status") in {"done", "error"}:
        from datetime import datetime, timezone

        updates["completed_at"] = datetime.now(timezone.utc).isoformat()
    record.update(updates)
    _jobs[job_id] = record
    should_persist = (
        "status" in updates
        or "error" in updates
        or "started_at" in updates
        or "completed_at" in updates
        or updates.get("progress") in {0.0, 1.0}
    )
    if should_persist:
        update_job_record(job_id, **updates)


def _format_simulation_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()

    if isinstance(exc, SimulationParameterError) or exc.__class__.__name__ == "VissimObjectNotFoundError":
        return message

    if "scenario management" in lowered and "edit scenario" in lowered:
        return (
            "VISSIM still detected scenario-management mode in the generated runtime INPX. "
            "This source network likely requires a manually exported standalone scenario INPX. "
            "Point VISSIM_SIMULATION_INPX_PATH to that exported file if the problem continues. "
            f"Current simulation path: {_simulation_source_inpx()}"
        )

    if isinstance(exc, FileNotFoundError):
        return message

    if "itembykey" in lowered and "object does not exist" in lowered:
        return (
            "The prompt referenced a VISSIM object number that does not exist in the loaded network. "
            "Ask for the current VISSIM COM status or use one of the available vehicle input or signal controller numbers."
        )

    return f"Simulation failed: {message}"


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
        args=(job_id, source_inpx, duration_s, random_seeds),
        daemon=True,
    )
    t.start()
    return {
        "id": job_id,
        "status": "queued",
        "scenario_result_id": record.get("scenario_result_id"),
    }


def _clear_stale_result_dbs(directory: Path) -> list[Path]:
    """Delete every ``*.db`` already present under ``directory`` and return
    what was removed.

    A pre-existing result database must never be read as THIS run's result
    (docs/phase2/02-ROADMAP.md M2.4). A fresh, uuid-keyed job/seed directory
    should never already contain one, but this is enforced rather than
    merely assumed — a stray leftover (e.g. a crashed prior attempt that
    reused a directory) is deleted before Vissim ever runs, not discovered
    (and silently misattributed) after.
    """
    removed: list[Path] = []
    if not directory.exists():
        return removed
    for stale_db in directory.rglob("*.db"):
        try:
            stale_db.unlink()
            removed.append(stale_db)
        except OSError:
            logger.warning("Could not remove stale result db %s", stale_db)
    if removed:
        logger.warning(
            "Removed %d stale result db(s) from %s before running", len(removed), directory
        )
    return removed


def _read_result_table_names(db_path: Path) -> set[str]:
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        conn.close()
    return {str(row[0]).upper() for row in rows}


def _run_agent_simulation_job(
    job_id: str, source_inpx: Path, duration_s: int, random_seeds: list
) -> None:
    """M2.4 job runner thread body: RunContinuous once per seed, each seed
    isolated in its own subdirectory so results_reader's existing multi-db
    averaging aggregates KPIs across seeds with no new logic. Marks the job
    "done" only after every seed's result database is verified to carry a
    SIMULATIONRUN row (the run-completed artifact — never SimSec, which
    resets to 0 the moment a run ends; see
    docs/phase2/00-EXECUTION-GUIDE.md SS3.1)."""
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

        _update_job(job_id, status="running", progress=0.0)
        work_dir = job_dir(job_id)
        work_dir.mkdir(parents=True, exist_ok=True)
        all_tables: set[str] = set()

        for index, seed in enumerate(random_seeds):
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

                # RunContinuous is a single COM call that runs to SimPeriod
                # internally (~7.3x realtime observed) — RunSingleStep in a
                # Python loop costs minutes for 60 sim-seconds and is
                # reserved for a future live-streaming feature, never
                # batch/agent runs (docs/phase2/00-EXECUTION-GUIDE.md SS3.1).
                ctrl.vissim.Simulation.RunContinuous()

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

            _update_job(job_id, progress=(index + 1) / len(random_seeds))

        _update_job(job_id, status="done", progress=1.0, result_tables=sorted(all_tables))
        logger.info("Agent simulation job %s completed (%d seed run(s))", job_id, len(random_seeds))
    except Exception as exc:
        logger.exception("Agent simulation job %s failed", job_id)
        _update_job(job_id, status="error", error=_format_simulation_error(exc))
    finally:
        try:
            if ctrl is not None:
                ctrl.disconnect()
        except Exception:
            pass
        pythoncom.CoUninitialize()

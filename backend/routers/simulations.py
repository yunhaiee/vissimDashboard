"""Simulation execution endpoints using VISSIM COM API."""
import asyncio
import logging
import os
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

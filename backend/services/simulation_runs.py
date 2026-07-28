"""Persistent registry for local VISSIM simulation runs."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from config import REPO_ROOT
from services.datasets import get_active_dataset

SIMULATION_ROOT = REPO_ROOT / "runtime" / "simulations"
REGISTRY_PATH = SIMULATION_ROOT / "simulation_runs.json"
RUNTIME_SCENARIO_PREFIX = "SIM_"


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
    records = _read_registry()
    record = records.get(job_id)
    if record is None:
        return None
    record.update(updates)
    records[job_id] = record
    _write_registry(records)
    return record


def upsert_job_record(record: dict[str, Any]) -> None:
    records = _read_registry()
    records[str(record["id"])] = record
    _write_registry(records)


def get_job_record(job_id: str) -> dict[str, Any] | None:
    return _read_registry().get(job_id)


def list_job_records() -> list[dict[str, Any]]:
    records = list(_read_registry().values())
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
    if record.get("dataset_revision") != get_active_dataset().get("revision"):
        return None
    return job_dir(job_id)


def _read_registry() -> dict[str, dict[str, Any]]:
    if not REGISTRY_PATH.exists():
        return {}
    try:
        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    records = payload.get("jobs", {})
    return records if isinstance(records, dict) else {}


def _write_registry(records: dict[str, dict[str, Any]]) -> None:
    SIMULATION_ROOT.mkdir(parents=True, exist_ok=True)
    payload = {"jobs": records}
    REGISTRY_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _runtime_color(index: int) -> str:
    palette = ["#8b5cf6", "#0ea5e9", "#14b8a6", "#f59e0b", "#ef4444", "#64748b"]
    return palette[index % len(palette)]

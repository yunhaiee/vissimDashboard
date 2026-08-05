"""Persistent registry for local VISSIM simulation runs."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from config import REPO_ROOT
from services import workspaces
from services.datasets import get_active_dataset

SIMULATION_ROOT = REPO_ROOT / "runtime" / "simulations"
REGISTRY_PATH = SIMULATION_ROOT / "simulation_runs.json"
RUNTIME_SCENARIO_PREFIX = "SIM_"

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


def list_job_records(*, workspace_scoped: bool = True) -> list[dict[str, Any]]:
    """Job records newest-first, restricted to the requesting workspace.

    ``workspace_scoped=False`` is for maintenance/diagnostics that genuinely
    need every job on the box (nothing user-facing does); the default is the
    isolating one so a new call site cannot leak another project's runs by
    forgetting a filter.
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

"""The single run gate (M2.4): ``run_simulation``, tier ``"run"``.

Unlike ``tools_edit.py`` (which drives a live :class:`~services.agent.
edit_session.VissimEditSession` on the agent turn's OWN worker thread — the
M2.3 exception to "all COM work goes through the job queue" documented in
docs/phase2/DECISIONS.md), a run is long-running batch work and stays on the
EXISTING simulation job queue (``routers.simulations``/
``services.simulation_runs`` — the same file-based job registry +
background-thread pattern ``POST /api/simulations`` already uses), per that
same DECISIONS.md entry: "M2.4 RUNS stay on the simulation job queue."

**Active run source.** Mirrors ``edit_session.py``'s active-session context
pattern exactly (module-global "current turn" slot, set by
``routers/assistant.py`` for the duration of a turn, read by this module's
tool function): :func:`set_active_run_source` / :func:`get_active_run_source`
/ :func:`clear_active_run_source`. This is a SEPARATE module-global from
``tools_network.set_active_inpx`` (deliberately not read from there — see the
drafted DECISIONS.md entry in the M2.4 report) even though both are set to
the same ``ArtifactManager.working_path`` for a given turn: mirroring
``edit_session.py``'s own pattern keeps this module self-contained and
independently testable without needing ``tools_network``'s state set up too,
and avoids reaching into a sibling module's private (underscore-prefixed)
resolver function.

**Job submission is a thin, injectable seam.** :func:`_run_job_and_wait`
submits to ``routers.simulations.start_agent_simulation_job`` (RunContinuous,
multi-seed capable, process-unique work dir, its own job record in the
existing registry) and blocks — polling ``services.simulation_runs.
get_job_record`` — until the job reaches a terminal state or a duration-based
timeout elapses. Named and shaped like ``routers/assistant.py``'s
``_build_edit_session`` specifically so tests can
``monkeypatch.setattr(tools_run, "_run_job_and_wait", ...)`` with a fake
runner that returns a canned terminal job record synchronously — no real
Vissim COM, no real background thread, no real filesystem I/O.

**Verification, not trust.** The job runner itself already refuses to mark a
job "done" unless the newest result .db carries a SIMULATIONRUN row (the
run-completed artifact per docs/phase2/00-EXECUTION-GUIDE.md SS3.1 — never
SimSec) and already deletes any pre-existing ``*.db`` in its work dir before
running. This tool defensively RE-CHECKS the SIMULATIONRUN row is present in
the reported table set before treating a "done" job as a real completed run
— a "done" status string is claimed state, the SIMULATIONRUN row is the
verifiable artifact, and the guardrail doctrine (docs/phase2/
00-EXECUTION-GUIDE.md SS4) says verify, not trust.

**Postrun guardrail call site.** This tool does NOT call
``GuardrailEngine.postrun`` itself — it returns ``run_result`` (``{"tables",
"deleted_vehicles"}``) inside its envelope's ``data`` key, and
``orchestrator.py`` calls ``engine.postrun`` after a successful run-tier
dispatch, mirroring exactly how it (not ``tools_edit.py``) calls
``engine.postwrite`` after a write dispatch. A tool function only ever
receives its own validated params (``toolkit.dispatch``'s contract) — it has
no seam to receive the orchestrator's specific ``GuardrailEngine`` instance,
and constructing a second, independent engine here would silently bypass the
ablation flag's single-instance-per-turn semantics. See the drafted
DECISIONS.md entry in the M2.4 report.

**deleted_vehicles is always None today.** Vissim records ``DiffusTm``-driven
vehicle removals only in the run's ``.err`` text log
(docs/reference/vissim-constraints.html), never in any SQLite result table —
there is no result-.db column to read this from. ``vehicles_deleted_in_run``
(constraints.py #8) therefore cannot fire yet; ``results_missing_kpi_tables``
(#7) is the detector this milestone's live acceptance check exercises. See
the drafted DECISIONS.md entry.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from services import results_reader
from services.agent.toolkit import tool

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Active run source (mirrors edit_session.py's active-session pattern)
# ---------------------------------------------------------------------------

_active_run_source: Path | None = None


def set_active_run_source(path: Path | str | None) -> None:
    """Set (or clear, with ``None``) the process-wide "current turn" network
    source :func:`run_simulation` submits to the job queue.

    Module-global by design, mirroring ``edit_session.py``'s
    ``set_active_edit_session`` pattern and its same acceptable-limitation
    caveat: not safe for multiple concurrent agent turns in one process. The
    caller (``routers/assistant.py``) sets this to the turn's
    ``ArtifactManager.working_path`` before invoking ``run_agent_turn`` and
    clears it in a ``finally`` block.
    """
    global _active_run_source
    _active_run_source = Path(path) if path is not None else None


def get_active_run_source() -> Path | None:
    """The current turn's active run source, or ``None`` if none is set."""
    return _active_run_source


def clear_active_run_source() -> None:
    """Equivalent to ``set_active_run_source(None)`` — named separately so
    call sites read as intent ("clear"), matching ``edit_session.py``'s
    ``clear_active_edit_session``."""
    global _active_run_source
    _active_run_source = None


class NoActiveRunSourceError(RuntimeError):
    """No active run source for this turn. Raised (never returned as a dict
    — see ``tools_edit.py``'s identical rationale: a returned dict is always
    wrapped ``status: "ok"`` by ``toolkit.dispatch``, which would make the
    orchestrator treat a never-submitted run as a completed one) so
    ``toolkit.dispatch`` reports a genuine ``status: "error"`` envelope."""


def _require_active_run_source() -> Path:
    source = get_active_run_source()
    if source is None:
        raise NoActiveRunSourceError(
            "No active network source for this turn (error_type: no_active_run_source). "
            "run_simulation only runs during an agent turn with a working copy set up."
        )
    if not source.exists():
        raise FileNotFoundError(f"Active run source does not exist: {source}")
    return source


# ---------------------------------------------------------------------------
# Job submission + blocking wait — a thin, injectable seam
# ---------------------------------------------------------------------------

_POLL_INTERVAL_S = 0.5

# Conservative per-seed timeout budget: launch + load + save/exit overhead
# (Vissim startup is the dominant, most variable cost — com_smoke_test.py
# and edit_tool_live_check.py both show multi-second to multi-ten-second
# connect/load times) plus simulated duration at a DELIBERATELY conservative
# realtime factor (docs/phase2/00-EXECUTION-GUIDE.md SS3.1 observed ~7.3x on
# one network; 3x leaves headroom for a slower one) so a cold launch or a
# heavier network doesn't false-positive a timeout mid-run.
_PER_RUN_OVERHEAD_S = 240.0
_ASSUMED_REALTIME_FACTOR = 3.0


def _job_timeout_s(duration_s: int, num_seeds: int) -> float:
    """Duration-based timeout ceiling for one ``run_simulation`` call
    (possibly multiple sequential seed sub-runs inside one job)."""
    per_run = _PER_RUN_OVERHEAD_S + (max(duration_s, 0) / _ASSUMED_REALTIME_FACTOR)
    return per_run * max(num_seeds, 1)


def _run_job_and_wait(
    *,
    source_inpx: Path,
    duration_s: int,
    random_seeds: list[int | None],
    timeout_s: float,
) -> dict[str, Any]:
    """Submit one agent run to the EXISTING simulation job queue and block
    until it reaches a terminal state ("done"/"error") or ``timeout_s``
    elapses.

    A thin, separately-named seam — mirrors ``routers/assistant.py``'s
    ``_build_edit_session`` — so tests can
    ``monkeypatch.setattr(tools_run, "_run_job_and_wait", ...)`` with a fake
    runner returning a canned terminal job record synchronously, without
    touching real Vissim COM, a real background thread, or real job-registry
    files.

    Imports are deliberately local (not module-level): keeps this module
    importable/testable without pulling in ``routers.simulations``' FastAPI
    surface, and matches this codebase's established lazy-import convention
    for COM-adjacent modules (``edit_session.py``'s ``pythoncom``,
    ``claude_provider.py``'s ``anthropic``).
    """
    from routers.simulations import start_agent_simulation_job
    from services.simulation_runs import get_job_record

    submitted = start_agent_simulation_job(
        source_inpx=source_inpx, duration_s=duration_s, random_seeds=random_seeds
    )
    job_id = submitted["id"]

    deadline = time.monotonic() + timeout_s
    while True:
        record = get_job_record(job_id)
        if record is not None and record.get("status") in ("done", "error"):
            return record
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Simulation job {job_id!r} did not reach a terminal state within "
                f"{timeout_s:.0f}s."
            )
        time.sleep(_POLL_INTERVAL_S)


# ---------------------------------------------------------------------------
# KPI summary — via results_reader, degrading gracefully
# ---------------------------------------------------------------------------

# Mirrors tools_metrics.py's private _NETWORK_METRIC_KEYS (not imported —
# that name is private to that module; same mirror-not-reach-into-a-sibling-
# module's-private-symbol precedent as guardrails.py's
# _WRITE_TARGET_FIELD_SPEC mirroring constraints.py's _TOOL_TARGET_SPEC).
_NETWORK_METRIC_KEYS: dict[str, str] = {
    "avg_delay_s": "DELAYAVG",
    "avg_speed_kmh": "SPEEDAVG",
    "avg_stops": "STOPSAVG",
    "total_travel_time_s": "TRAVTMTOT",
}


def _summarize_kpis(work_dir: Path) -> dict[str, Any] | None:
    """A compact KPI summary via ``results_reader``, or ``None`` when no KPI
    data is readable (e.g. a run too short for evaluation intervals to have
    produced anything beyond SIMULATIONRUN — the exact scenario this
    milestone's live acceptance check exercises).

    ``results_reader.get_runtime_results`` already degrades gracefully on
    its own (each per-db read is wrapped in its own try/except, so a db with
    only SIMULATIONRUN simply contributes no readable run and the aggregate
    returns ``None`` if no db contributed one) — the try/except here is
    defense in depth, not a bet that it currently raises.
    """
    try:
        result = results_reader.get_runtime_results(work_dir)
    except Exception:
        logger.exception(
            "run_simulation: results_reader.get_runtime_results raised; degrading to no KPI summary"
        )
        return None
    if not result:
        return None
    network = result.get("network") or {}
    metrics = {
        name: network[key]
        for name, key in _NETWORK_METRIC_KEYS.items()
        if network.get(key) is not None
    }
    if not metrics:
        return None
    return {"run_count": result.get("run_count"), "metrics": metrics}


# ---------------------------------------------------------------------------
# run_simulation(duration_s, random_seeds)
# ---------------------------------------------------------------------------


class RunSimulationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_s: int
    random_seeds: list[int] | None = None


@tool(name="run_simulation", tier="run", provider="run", params=RunSimulationParams)
def run_simulation(params: RunSimulationParams) -> dict[str, Any]:
    """Run the session's edited network through the simulation job queue (RunContinuous) and report KPIs with post-run evidence."""
    source_inpx = _require_active_run_source()
    seeds: list[int | None] = list(params.random_seeds) if params.random_seeds else [None]
    timeout_s = _job_timeout_s(params.duration_s, len(seeds))

    record = _run_job_and_wait(
        source_inpx=source_inpx,
        duration_s=params.duration_s,
        random_seeds=seeds,
        timeout_s=timeout_s,
    )

    job_id = record.get("id")
    if record.get("status") != "done":
        raise RuntimeError(
            f"Simulation job {job_id!r} did not complete successfully: "
            f"{record.get('error') or 'unknown error'}"
        )

    tables = {str(t).upper() for t in (record.get("result_tables") or [])}
    if "SIMULATIONRUN" not in tables:
        # Defense in depth (see module docstring): never trust a "done"
        # status string alone as the run-completed artifact — the
        # SIMULATIONRUN row is. The job runner already enforces this before
        # marking a job "done"; this re-check protects against a future
        # regression there from silently becoming a lie here too.
        raise RuntimeError(
            f"Simulation job {job_id!r} reported done, but its result database carries no "
            "SIMULATIONRUN row -- refusing to treat this as a completed run."
        )

    # The job runner (routers.simulations._run_agent_simulation_job) now
    # configures Vissim's network-performance evaluation itself, once per
    # seed, before RunContinuous -- and hard-fails the WHOLE job (never
    # reaches "done") if that configuration fails, so a job record read here
    # with status == "done" always carries evaluation_configured == True in
    # production. The ``.get(..., False)`` default is defensive, not a live
    # branch: a job record written before this field existed, or a future
    # regression in the job runner that starts returning "done" without
    # setting it, must read as "not proven configured" here rather than
    # silently defaulting to True and hiding the gap from
    # evaluation_not_configured_before_run's postrun check below.
    evaluation_configured = bool(record.get("evaluation_configured", False))

    run_result = {
        "tables": sorted(tables),
        # Not obtainable from the result .db schema today — see this
        # module's docstring and the drafted DECISIONS.md entry.
        "deleted_vehicles": None,
        "evaluation_configured": evaluation_configured,
    }

    kpis: dict[str, Any] | None = None
    if job_id:
        from services.simulation_runs import job_dir

        kpis = _summarize_kpis(job_dir(job_id))

    tables_text = ", ".join(run_result["tables"]) or "none"
    eval_text = "configured" if evaluation_configured else "NOT configured (KPIs may be unavailable)"
    return {
        "message": (
            f"Simulation ran for {params.duration_s}s across {len(seeds)} seed(s); "
            f"result tables: {tables_text}; network-performance evaluation {eval_text}."
        ),
        "verification": {
            "job_id": job_id,
            "simulationrun_verified": True,
            "duration_s": params.duration_s,
            "random_seeds": seeds,
            "tables": run_result["tables"],
            "evaluation_configured": evaluation_configured,
        },
        "run_result": run_result,
        "kpis": kpis,
    }


__all__ = [
    "NoActiveRunSourceError",
    "RunSimulationParams",
    "clear_active_run_source",
    "get_active_run_source",
    "run_simulation",
    "set_active_run_source",
]

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
``GuardrailEngine.postrun`` itself — it returns ``run_result`` inside its
envelope's ``data`` key, and ``orchestrator.py`` calls ``engine.postrun``
after a successful run-tier
dispatch, mirroring exactly how it (not ``tools_edit.py``) calls
``engine.postwrite`` after a write dispatch. A tool function only ever
receives its own validated params (``toolkit.dispatch``'s contract) — it has
no seam to receive the orchestrator's specific ``GuardrailEngine`` instance,
and constructing a second, independent engine here would silently bypass the
ablation flag's single-instance-per-turn semantics. See the drafted
DECISIONS.md entry in the M2.4 report.

**run_result's contract (unit U1).** Four keys are always present —
``tables``, ``deleted_vehicles``, ``evaluation_configured``,
``trajectory_recorded`` — plus ``evidence``, the tri-state descriptor from
``services/results_evidence.py``. The evidence-bearing keys
(``queue_results``, ``queue_definition``, ``los_by_movement``,
``movement_volumes``, ``delay_segments``, ``evaluation_interval_s``) are
CONDITIONALLY present, and their absence is itself the message: a key is
OMITTED when the table backing it does not exist in the result .db at all,
and present-but-empty (an ``EmptyEvidence*`` marker) when the evaluation was
armed and collected nothing. See :func:`_collect_run_evidence`.

**deleted_vehicles is always None today.** Vissim records ``DiffusTm``-driven
vehicle removals only in the run's ``.err`` text log
(docs/reference/vissim-constraints.html), never in any SQLite result table —
there is no result-.db column to read this from. ``vehicles_deleted_in_run``
(constraints.py #8) therefore cannot fire yet; ``results_missing_kpi_tables``
(#7) is the detector this milestone's live acceptance check exercises. See
the drafted DECISIONS.md entry. The same holds for
``latent_demand_vehicles``.
"""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict

from services import results_evidence, results_reader
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


# ---------------------------------------------------------------------------
# RUN-PROGRESS UI (2026-07-28): a "job just got submitted" notification hook
# ---------------------------------------------------------------------------
#
# run_simulation (below) BLOCKS inside _run_job_and_wait's poll loop for the
# entire run — for a multi-seed batch, that can be 10+ minutes — before this
# tool function returns anything toolkit.dispatch/orchestrator can turn into
# an on_step/NDJSON event. Without a separate hook, the frontend has no way
# to learn the job_id (and therefore start polling GET /api/simulations/{id}
# for seed-boundary progress) until the ENTIRE run is already over, which is
# exactly the "Preparing answer... for 10 minutes" bug this milestone fixes.
#
# Mirrors set_active_run_source's module-global "current turn" pattern
# exactly, rather than threading a callback parameter through
# toolkit.dispatch's fixed (tool, params) -> envelope contract (which has no
# seam for it — see this module's own docstring on why _active_run_source
# is a module-global in the first place). routers/assistant.py sets this to
# a queue-pushing closure for the duration of a turn and clears it in a
# `finally`, exactly like tools_network.set_active_inpx and this module's
# own set_active_run_source/clear_active_run_source.
_on_run_started: Callable[[str], None] | None = None


def set_run_started_callback(callback: Callable[[str], None] | None) -> None:
    """Set (or clear, with ``None``) the callback invoked with a job's id
    the moment it is submitted to the simulation job queue — BEFORE
    :func:`_run_job_and_wait` starts polling for completion."""
    global _on_run_started
    _on_run_started = callback


def clear_run_started_callback() -> None:
    """Equivalent to ``set_run_started_callback(None)`` — named separately
    so call sites read as intent, matching this module's
    clear_active_run_source."""
    global _on_run_started
    _on_run_started = None


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

    # RUN-PROGRESS UI: fire the "a job now exists" notification BEFORE the
    # poll loop below blocks for the run's real duration. Best-effort, same
    # swallow-and-log discipline as orchestrator.py's _emit_step's on_step
    # call — a broken/raising callback must never fail the run itself.
    if _on_run_started is not None:
        try:
            _on_run_started(job_id)
        except Exception:
            logger.exception("run_started callback raised; ignoring")

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
# Post-run evidence (unit U1) — the run_result keys the postrun catalogue
# already asks for
# ---------------------------------------------------------------------------
#
# NINE postrun constraints in constraints.py (#21-#29) were written to read
# run_result fields that nothing produced, so none of them could ever fire on
# a real run. This section supplies the fields for THREE of them
# (gridlock_reports_los_a, queue_length_definition_not_portable,
# delay_measurement_truncated_by_queue) plus half of a fourth
# (evaluation_interval_multiple_of_cycle still wants signal_cycle_s), from
# artefacts the run already has on disk: the result .db (via
# services/results_evidence.py) and the run's own source .inpx. No COM, no
# new Vissim call, nothing that can fail a completed run. The remaining five
# keep their CATALOG-GAP markers — see each constraint's own comment for what
# is genuinely unobtainable (latent_demand_vehicles, deleted_vehicles) versus
# merely not built yet (vissim_version, error_log_archived,
# simulation_resolution, evaluation_warmup_starts, signal_cycle_s).
#
# The .inpx side matters as much as the .db side. queue_length_definition_
# not_portable fires exactly when queue NUMBERS are present and their
# DEFINING PARAMETERS are not — so shipping queue_results without
# queue_definition would manufacture a permanent false positive on every
# single run. They travel together or the results stay unshipped.

# Attributes of the .inpx ``<evaluation>`` element that define what Vissim
# counts as "queued". Verified against real networks in this checkout: the
# vendor examples carry queueBeginSpeed="3" queueEndSpeed="6"
# queueMaxClear="10" queueMaxLength="500", the customer model carries
# queueBeginSpeed="5" queueEndSpeed="12.5" queueMaxClear="20"
# queueMaxLength="2000" — i.e. two models whose queue numbers are NOT
# comparable, which is precisely the constraint's point.
_QUEUE_DEFINITION_ATTRS: dict[str, str] = {
    "begin_speed": "queueBeginSpeed",
    "end_speed": "queueEndSpeed",
    "max_clear": "queueMaxClear",
    "max_length": "queueMaxLength",
    "consider_adjacent_lanes": "queueConsiderAdjacentLanes",
}

# The upstream distance from a node over which Vissim counts delay. Delay
# from a queue that stretches back further than this simply is not counted,
# so the most congested intersections are the ones whose delay is most
# under-reported — delay_measurement_truncated_by_queue's whole subject.
# Both the vendor examples and the customer model carry the 100 m default.
_DELAY_SEGMENT_ATTR = "nodesDelaySegmentStart"


def _local_name(tag: object) -> str:
    """XML tag without any ``{namespace}`` prefix."""
    return str(tag).rsplit("}", 1)[-1]


def _as_float(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _as_bool(raw: object) -> bool | None:
    text = str(raw).strip().lower() if raw is not None else ""
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    return None


def _read_evaluation_element(inpx_path: Path) -> dict[str, Any]:
    """ONE ElementTree pass over the run's .inpx, stopping at
    ``</evaluation>``.

    ``iterparse`` rather than ``ET.parse`` deliberately: ``<evaluation>``
    sits about a fifth of the way into a real network file (measured: byte
    239188 of 1375367 on the customer model) and the largest .inpx in this
    checkout is 8.4 MB, so there is no reason to build the whole tree to read
    one element's attributes.

    Returns ``{}`` — never raises — when the file is missing, unparseable, or
    simply has no ``<evaluation>`` element. A run that completed must not be
    turned into a failure by a provenance read.
    """
    try:
        for _event, element in ET.iterparse(str(inpx_path), events=("end",)):
            if _local_name(element.tag) != "evaluation":
                continue
            return _parse_evaluation_element(element)
    except (ET.ParseError, OSError):
        logger.exception(
            "run_simulation: could not read <evaluation> from %s; "
            "run_result will carry no queue definition",
            inpx_path,
        )
    return {}


def _parse_evaluation_element(element: ET.Element) -> dict[str, Any]:
    queue_definition: dict[str, Any] = {}
    for field, attribute in _QUEUE_DEFINITION_ATTRS.items():
        raw = element.get(attribute)
        if raw is None:
            continue
        value = _as_bool(raw) if field == "consider_adjacent_lanes" else _as_float(raw)
        if value is None:
            continue
        queue_definition[field] = value

    node_results_interval_s: float | None = None
    for child in element:
        if _local_name(child.tag) != "nodeResults":
            continue
        # "Armed" is collectData="true". A dormant <nodeResults> carries a
        # placeholder interval (999999 on the untouched vendor examples)
        # that describes nothing the network would actually measure.
        if _as_bool(child.get("collectData")):
            node_results_interval_s = _as_float(child.get("interval"))
        break

    return {
        # Only when at least one defining parameter was actually found. An
        # all-None dict would read as "definition attached" to
        # queue_length_definition_not_portable and silence a check that
        # SHOULD fire when the definition is genuinely unavailable.
        "queue_definition": queue_definition or None,
        "delay_segment_start_m": _as_float(element.get(_DELAY_SEGMENT_ATTR)),
        "node_results_interval_s": node_results_interval_s,
    }


def _delay_segments(
    queue_results: dict[str, dict[str, Any]], segment_length_m: float
) -> list[dict[str, Any]]:
    """Pair each node's worst observed queue against the delay segment.

    Node id comes from the movement OBJECT_ID's leading token (Vissim writes
    both per-movement rows like ``1-508-100.779980-510-62.109716`` and a
    node-level aggregate row like ``1``; both fold onto the same node).
    """
    worst_by_node: dict[str, float] = {}
    for object_id, row in queue_results.items():
        qlenmax = row.get("qlenmax")
        if qlenmax is None:
            continue
        node = results_evidence.node_token(object_id)
        worst_by_node[node] = max(worst_by_node.get(node, float(qlenmax)), float(qlenmax))

    return [
        {
            "node": node,
            "segment_length_m": segment_length_m,
            "observed_queue_m": observed,
        }
        for node, observed in sorted(
            worst_by_node.items(), key=lambda item: (0, int(item[0])) if item[0].isdigit() else (1, item[0])
        )
    ]


def _collect_run_evidence(
    work_dir: Path | None,
    source_inpx: Path,
    *,
    duration_s: int,
    evaluation_configured: bool,
) -> dict[str, Any]:
    """The evidence-bearing ``run_result`` keys, plus the ``evidence``
    descriptor that explains every one of them.

    The tri-state policy, which is the whole point of the descriptor:

    * ``TABLE_ABSENT`` — the key is **omitted**. Not ``None``, not ``[]``:
      the run has no opinion, and a defaulted value would read as one.
    * ``ARMED_BUT_EMPTY`` — the key is **present** carrying an empty-with-
      reason marker, so the system can say "queue evidence was requested and
      none was collected" instead of silently implying no anomaly.
    * ``PRESENT(n)`` — the key carries the actual rows.

    Every branch degrades rather than raises: a completed run must never be
    reported as failed because a provenance read hiccuped.
    """
    evidence: dict[str, Any] = {}

    descriptor = (
        results_evidence.describe_result_dir(work_dir)
        if work_dir is not None
        else {table: results_evidence.TABLE_ABSENT for table in results_evidence.EVALUATION_TABLES}
    )
    evidence["evidence"] = descriptor

    db_paths = results_evidence.result_db_paths(work_dir) if work_dir is not None else []
    evaluation = _read_evaluation_element(source_inpx)

    queue_table = results_evidence.MOVEMENT_INTERVAL_TABLE
    class_table = results_evidence.MOVEMENT_INTERVAL_CLASS_TABLE
    queue_state = descriptor.get(queue_table, results_evidence.TABLE_ABSENT)
    class_state = descriptor.get(class_table, results_evidence.TABLE_ABSENT)

    # --- queue_results + its mandatory definition ---------------------------
    queue_results: dict[str, dict[str, Any]] | None = None
    if queue_state == results_evidence.ARMED_BUT_EMPTY:
        evidence["queue_results"] = results_evidence.armed_but_empty_mapping(queue_table)
    elif results_evidence.is_present(queue_state):
        queue_results = results_evidence.read_queue_rows(db_paths)
        evidence["queue_results"] = queue_results

    queue_definition = evaluation.get("queue_definition")
    if queue_definition:
        evidence["queue_definition"] = queue_definition

    # --- los_by_movement + movement_volumes --------------------------------
    if class_state == results_evidence.ARMED_BUT_EMPTY:
        evidence["los_by_movement"] = results_evidence.armed_but_empty_list(class_table)
        evidence["movement_volumes"] = results_evidence.armed_but_empty_mapping(class_table)
    elif results_evidence.is_present(class_state):
        rows = results_evidence.read_movement_class_rows(db_paths)
        evidence["los_by_movement"] = [
            {
                "movement": object_id,
                "los": results_evidence.los_band(row.get("vehdelay")),
                "volume": row.get("vehs"),
                # Not read by gridlock_reports_los_a, carried because that
                # constraint's own message is "never report a LOS letter
                # without the number it came from next to it".
                "delay_s": row.get("vehdelay"),
            }
            for object_id, row in rows.items()
        ]
        # The node token is exposed on every entry on purpose: this is the
        # key that later lets a reality-check compare the RUN against field
        # observations without re-reading a workbook.
        evidence["movement_volumes"] = {
            object_id: {
                "vehs": row.get("vehs"),
                "node": results_evidence.node_token(object_id),
            }
            for object_id, row in rows.items()
        }

    # --- delay_segments -----------------------------------------------------
    # Needs BOTH sides: the queue lengths from the .db and the segment start
    # from the .inpx. Without the segment there is nothing to compare against,
    # so the key is omitted rather than invented at Vissim's 100 m default.
    segment_length_m = evaluation.get("delay_segment_start_m")
    if segment_length_m is not None:
        if queue_state == results_evidence.ARMED_BUT_EMPTY:
            evidence["delay_segments"] = results_evidence.armed_but_empty_list(queue_table)
        elif queue_results is not None:
            evidence["delay_segments"] = _delay_segments(queue_results, segment_length_m)

    # --- evaluation_interval_s ---------------------------------------------
    # DELIBERATELY not read straight off the source .inpx. For an agent run,
    # routers/simulations.py calls VissimController.
    # configure_network_performance_evaluation, which overwrites every
    # evaluation type's interval with `max(int(duration_s), 1)` (one bucket
    # spanning the whole run) before RunContinuous — so the .inpx's own
    # <nodeResults interval=...> is NOT the interval this run used, and
    # reporting it would be a quiet lie. evaluation_configured is exactly the
    # flag that says whether that re-arming happened; the network's own armed
    # interval is the honest answer only when it did not.
    if evaluation_configured:
        evidence["evaluation_interval_s"] = float(max(int(duration_s), 1))
    elif evaluation.get("node_results_interval_s") is not None:
        evidence["evaluation_interval_s"] = evaluation["node_results_interval_s"]

    return evidence


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
    # trajectory_recorded: best-effort vehicle-trajectory replay data (see
    # routers/simulations.py's _run_agent_simulation_job docstring). Unlike
    # evaluation_configured, a "done" job can legitimately carry False here
    # (duration exceeded AGENT_RUN_TRAJECTORY_MAX_S, or recording failed for
    # every seed) -- this is never a run failure, just replay unavailability.
    trajectory_recorded = bool(record.get("trajectory_recorded", False))

    run_result = {
        "tables": sorted(tables),
        # Not obtainable from the result .db schema today — see this
        # module's docstring and the drafted DECISIONS.md entry.
        "deleted_vehicles": None,
        "evaluation_configured": evaluation_configured,
        "trajectory_recorded": trajectory_recorded,
    }

    work_dir: Path | None = None
    if job_id:
        from services.simulation_runs import job_dir

        work_dir = job_dir(job_id)

    # Unit U1: the postrun catalogue's evidence. Wrapped because a run that
    # genuinely completed must never be reported as failed by a read of its
    # own leftovers — _collect_run_evidence degrades internally too, this is
    # the outer belt.
    try:
        run_result.update(
            _collect_run_evidence(
                work_dir,
                source_inpx,
                duration_s=params.duration_s,
                evaluation_configured=evaluation_configured,
            )
        )
    except Exception:
        logger.exception(
            "run_simulation: post-run evidence collection failed; run_result carries "
            "the four base keys only"
        )

    kpis: dict[str, Any] | None = None
    if work_dir is not None:
        kpis = _summarize_kpis(work_dir)

    tables_text = ", ".join(run_result["tables"]) or "none"
    eval_text = "configured" if evaluation_configured else "NOT configured (KPIs may be unavailable)"
    trajectory_text = (
        "recorded (vehicle-by-vehicle replay is available in the network preview)"
        if trajectory_recorded
        else "not recorded"
    )
    return {
        "message": (
            f"Simulation ran for {params.duration_s}s across {len(seeds)} seed(s); "
            f"result tables: {tables_text}; network-performance evaluation {eval_text}; "
            f"vehicle trajectories {trajectory_text}."
        ),
        "verification": {
            "job_id": job_id,
            "simulationrun_verified": True,
            "duration_s": params.duration_s,
            "random_seeds": seeds,
            "tables": run_result["tables"],
            "evaluation_configured": evaluation_configured,
            "trajectory_recorded": trajectory_recorded,
        },
        "run_result": run_result,
        "kpis": kpis,
    }


__all__ = [
    "NoActiveRunSourceError",
    "RunSimulationParams",
    "clear_active_run_source",
    "clear_run_started_callback",
    "get_active_run_source",
    "run_simulation",
    "set_active_run_source",
    "set_run_started_callback",
]

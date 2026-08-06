"""Read tools over simulation results: KPI summaries, scenario comparisons,
and the reality check against observed counts.

These wrap ``services.results_reader`` (which does the actual .db aggregation
and cross-scenario computation) — this module's job is compacting that output
for an LLM prompt: headline network averages only, computed leader/ranking
sections rather than raw per-node bulk, and NEVER a filesystem path (a tool's
data feeds the planner directly; a local .db path has no business there).

Domain misses (an unknown scenario id) are surfaced as successful tool calls
whose data says ``found: false`` + ``error_type`` + ``candidates`` — the
planner reads that and decides what to do next; this module never raises for
a bad id (see services.agent.toolkit's dispatch() docstring on that split).

``run_reality_check`` (Phase 2 unit U5) is the third tool here and the only
one that reads two sources at once: the run's own evidence and the observed
counts workbook. All of its logic lives in ``services.agent.reality_check``;
what lives HERE is the registry contract and the resolution of "which run" —
the two things that are this layer's job. It is read-tier and applies
nothing: the proposals it returns go to the human through the existing
approval loop, exactly like any other proposal.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from services import datasets, results_evidence, results_reader, simulation_runs, validation
from services.agent import reality_check
from services.agent.toolkit import tool

# planner-facing metric name -> the raw VEHICLENETWORKPERFORMANCEMEASUREMENT
# column results_reader aggregates it from (see results_reader.CORE_NETWORK_METRICS).
_NETWORK_METRIC_KEYS: dict[str, str] = {
    "avg_delay_s": "DELAYAVG",
    "avg_speed_kmh": "SPEEDAVG",
    "avg_stops": "STOPSAVG",
    "total_travel_time_s": "TRAVTMTOT",
}

# Defense-in-depth only: results_reader's aggregates never carry path strings
# today, but this module feeds an LLM prompt directly and must never leak a
# local .db path even if that assumption changes upstream.
#
# This must match genuine filesystem paths ONLY — natural-language content
# like "38.5 km/h" or "1,200 veh/h" (a single "/") is exactly what the
# planner needs and must survive. A single stray separator is not enough
# evidence of a path; require one of:
#   - a ".db" suffix (case-insensitive), anywhere a raw db path would end
#   - an absolute path: leading "/", a Windows drive ("C:\" or "C:/"), or a
#     UNC prefix ("\\\\host\...")
#   - a relative path with 2+ separators AND a trailing file extension
#     (a lone "unit/unit" pair never has an extension, so this can't catch
#     "km/h" no matter how it's phrased)
_ENDS_WITH_DB_RE = re.compile(r"\.db$", re.IGNORECASE)
_ABSOLUTE_PATH_RE = re.compile(r"^(?:/|[A-Za-z]:[\\/]|\\\\)")
_MULTI_SEGMENT_PATH_WITH_EXTENSION_RE = re.compile(r"(?:[^/\\]+[/\\]){2,}[^/\\]+\.[A-Za-z0-9]{1,8}$")


def _looks_like_path(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    return bool(
        _ENDS_WITH_DB_RE.search(text)
        or _ABSOLUTE_PATH_RE.match(text)
        or _MULTI_SEGMENT_PATH_WITH_EXTENSION_RE.search(text)
    )


def _strip_path_like(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_path_like(v) for k, v in value.items() if not _looks_like_path(v)}
    if isinstance(value, list):
        return [_strip_path_like(v) for v in value if not _looks_like_path(v)]
    return value


# ---------------------------------------------------------------------------
# get_run_kpis
# ---------------------------------------------------------------------------


class GetRunKpisParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str


@tool(name="get_run_kpis", tier="read", provider="metrics", params=GetRunKpisParams)
def get_run_kpis(params: GetRunKpisParams) -> dict[str, Any]:
    """Return a compact KPI summary (avg delay/speed/stops, total travel time) for one scenario run."""
    result = results_reader.get_scenario_results(params.scenario_id)
    scenarios = results_reader.list_scenarios()

    if result is None:
        return {
            "found": False,
            "error_type": "scenario_not_found",
            "candidates": [scenario["id"] for scenario in scenarios],
        }

    scenario_name = next(
        (scenario["name"] for scenario in scenarios if scenario["id"] == params.scenario_id),
        params.scenario_id,
    )
    network = result.get("network") or {}
    metrics = {
        metric_name: network[raw_key]
        for metric_name, raw_key in _NETWORK_METRIC_KEYS.items()
        if network.get(raw_key) is not None
    }

    return _strip_path_like(
        {
            "found": True,
            "scenario_id": params.scenario_id,
            "scenario_name": scenario_name,
            "run_count": result.get("run_count"),
            "metrics": metrics,
        }
    )


# ---------------------------------------------------------------------------
# compare_scenarios
# ---------------------------------------------------------------------------


class CompareScenariosParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_ids: list[str]


@tool(name="compare_scenarios", tier="read", provider="metrics", params=CompareScenariosParams)
def compare_scenarios(params: CompareScenariosParams) -> dict[str, Any]:
    """Return a compact cross-scenario comparison: network metric summary, leaders, and rankings."""
    summary = results_reader.get_compare_summary(params.scenario_ids)

    # Deliberately excludes "summary_cards" (dashboard-tile UI data) and any
    # per-node bulk beyond the already-top-5-capped top_congested_nodes —
    # this feeds an LLM prompt, not a chart.
    return _strip_path_like(
        {
            "requested_ids": summary.get("requested_ids", []),
            "scenario_ids": summary.get("scenario_ids", []),
            "invalid_ids": summary.get("invalid_ids", []),
            "missing_result_ids": summary.get("missing_result_ids", []),
            "network_metrics": summary.get("network_metrics", {}),
            "metric_leaders": summary.get("metric_leaders", {}),
            "health_scores": summary.get("health_scores", []),
            "scenario_ranking": summary.get("scenario_ranking", []),
            "top_congested_nodes": summary.get("top_congested_nodes", []),
            "key_insights": summary.get("key_insights", []),
            "status_band": summary.get("status_band", {}),
        }
    )


# ---------------------------------------------------------------------------
# run_reality_check (Phase 2 unit U5)
# ---------------------------------------------------------------------------


class RealityCheckNodeTarget(BaseModel):
    """One node's edit targets — what an accepted proposal would actually touch.

    Supplied by the CALLER, never guessed: which vehicle input feeds a node,
    and which routing decision governs a turn, are properties of the NETWORK
    (``get_vehicle_inputs`` / ``get_routing_decisions`` answer both), not of
    the run. Omitting them is safe and honest: the mapper then refuses with
    ``NO_TARGET_OBJECT`` and its rationale says exactly what was missing,
    instead of proposing an edit to an object nobody named.
    """

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(description="The counts-workbook node id this target belongs to.")
    vehicle_input_no: str | None = Field(
        default=None,
        description="The vehicle input whose volume drives this node's total (get_vehicle_inputs).",
    )
    vehicle_input_current_volume: float | None = Field(
        default=None,
        description="That input's CURRENT volume in veh/h; the proposal scales it by observed/modelled.",
    )
    movement: str | None = Field(
        default=None,
        description=(
            "The workbook movement/turn token a routing target is for, e.g. 'W-E'. Required "
            "for the routing fields below and for movement_object_id."
        ),
    )
    movement_object_id: str | None = Field(
        default=None,
        description=(
            "The run's movement OBJECT_ID (e.g. '2-207-250.0-141-5.0') for that turn, so its "
            "split is graded against THIS RUN rather than the workbook's pasted model column. "
            "Without it the movement is reported ungradable, never graded."
        ),
    )
    decision_no: str | None = Field(
        default=None, description="Static vehicle routing decision governing that turn."
    )
    route_no: str | None = Field(default=None, description="The route within that decision.")
    current_rel_flow: float | None = Field(
        default=None, description="That route's current relFlow (a RAW weight, not a percentage)."
    )
    sibling_rel_flow_total: float | None = Field(
        default=None,
        description=(
            "Sum of the OTHER routes' relFlow in the same decision; Vissim never renormalizes "
            "them on write."
        ),
    )
    decision_movements: list[str] | None = Field(
        default=None,
        description=(
            "EVERY workbook movement token that decision governs, e.g. ['E-W','E-N','E-S'] for a "
            "decision on one approach (get_routing_decisions gives the approach link and each "
            "route's destination). The observed share a new relFlow is derived from is taken "
            "against THESE movements' counted volumes — a decision governing the whole "
            "intersection simply lists all of its turns. Without it the routing fix is REFUSED "
            "rather than scaled against the node total, which would understate the route."
        ),
    )
    interval_no: int = Field(
        default=1, ge=1, description="The time interval the counts were measured in."
    )


class RunRealityCheckParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(
        description=(
            "The run to check (an id from get_run_kpis / compare_scenarios, or the run just "
            "executed)."
        )
    )
    period: Literal["AM", "PM"] | None = Field(
        default=None,
        description=(
            "Which counts period to compare against. Omitted = the workbook's AM rows if it has "
            "any, else its first period."
        ),
    )
    targets: list[RealityCheckNodeTarget] | None = Field(
        default=None,
        description=(
            "Per-node edit targets. Without them every miss still comes back — as a refusal "
            "naming what was missing."
        ),
    )


def _resolve_result_dir(scenario_id: str) -> Path | None:
    """The directory holding one scenario's result databases, or ``None``.

    The same two-source resolution ``results_reader.get_scenario_results``
    makes: an agent run (a runtime scenario, workspace- and revision-checked
    inside ``simulation_runs``) or a project scenario under the active dataset.
    """
    runtime_dir = simulation_runs.get_runtime_result_dir_for_scenario(scenario_id)
    if runtime_dir is not None:
        return runtime_dir
    scenario_dir = datasets.get_active_scenarios_dir() / scenario_id / f"{scenario_id}.results"
    return scenario_dir if scenario_dir.is_dir() else None


def _planner_safe_warnings(warnings: list[str]) -> list[str]:
    """The loader's own warnings, path-redacted but never dropped.

    This payload goes straight into an LLM prompt (see the module docstring),
    and ``_strip_path_like`` would DELETE a warning that embeds a local path —
    swallowing the observed side's own complaint, which is precisely what unit
    U5 must not do. So the warning survives; only its text is withheld.
    """
    return [
        warning
        if not _looks_like_path(warning)
        else "<workbook warning withheld: it names a local file path>"
        for warning in warnings
    ]


def _reality_check_targets(
    entries: list[RealityCheckNodeTarget] | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, str]]]:
    """Params -> the two caller-supplied maps ``reality_check`` takes.

    ``targets`` is U4's own target payload; ``movement_ids`` is the workbook
    movement -> run OBJECT_ID join. Both are assembled here rather than in
    ``reality_check`` so that pure function keeps ONE input shape whatever the
    caller happens to be.
    """
    targets: dict[str, dict[str, Any]] = {}
    movement_ids: dict[str, dict[str, str]] = {}
    for entry in entries or []:
        node = targets.setdefault(entry.node_id, {})
        if entry.vehicle_input_no is not None:
            node["vehicle_input"] = {
                "input_no": entry.vehicle_input_no,
                "current_volume": entry.vehicle_input_current_volume,
                "interval_no": entry.interval_no,
            }
        if entry.movement is not None:
            if entry.movement_object_id is not None:
                movement_ids.setdefault(entry.node_id, {})[entry.movement] = (
                    entry.movement_object_id
                )
            if entry.decision_no is not None or entry.route_no is not None:
                node.setdefault("routes", {})[entry.movement] = {
                    "decision_no": entry.decision_no,
                    "route_no": entry.route_no,
                    "current_rel_flow": entry.current_rel_flow,
                    "sibling_rel_flow_total": entry.sibling_rel_flow_total,
                    # The decision's SCOPE, passed straight through: U4 takes
                    # the observed share against these movements and refuses
                    # when they are absent (fixes._build_routing_proposal).
                    "decision_movements": (
                        list(entry.decision_movements)
                        if entry.decision_movements is not None
                        else None
                    ),
                    "interval_no": entry.interval_no,
                }
    return targets, movement_ids


def build_reality_check_report(
    scenario_id: str,
    *,
    period: str | None = None,
    targets: list[RealityCheckNodeTarget] | None = None,
) -> dict[str, Any]:
    """Resolve the run, load the counts, run the check. No COM, no writes."""
    result_dir = _resolve_result_dir(scenario_id)
    db_paths = results_evidence.result_db_paths(result_dir) if result_dir is not None else []
    if not db_paths:
        return reality_check.not_checkable(
            reality_check.RUN_NOT_FOUND,
            scenario_id=scenario_id,
            detail={
                "scenario_id": scenario_id,
                "candidates": [scenario["id"] for scenario in results_reader.list_scenarios()],
            },
        )
    if len(db_paths) > 1:
        # U1's read_movement_class_rows SUMS VEHS across every result database
        # in a run, so an N-seed run carries N runs' worth of vehicles. That is
        # the right aggregation for "how many vehicles did this job move" and
        # the WRONG one for "how does one hour compare to one hour" -- so this
        # refuses WITH the number rather than proposing an N-fold demand
        # correction. (The demo dataset's project scenarios are 3-seed; an
        # agent run_simulation with default seeds is single-seed.)
        return reality_check.not_checkable(
            reality_check.MULTI_SEED_VOLUMES_NOT_COMPARABLE,
            count=len(db_paths),
            detail={"scenario_id": scenario_id, "result_db_count": len(db_paths)},
        )

    # Imported here, not at module scope: services.agent.orchestrator imports
    # this module BEFORE tools_run, and a top-level import would flip the two
    # tools' registration order in default_registry (which render_contract and
    # native_tool_defs both reflect). Same lazy-and-guarded pattern
    # fixes._ensure_verbs_imported uses, for the same anti-side-effect reason.
    from services.agent.tools_run import _collect_run_evidence

    run_result = _collect_run_evidence(
        result_dir,
        datasets.get_active_inpx_path(),
        # This tool did not run the simulation and must not claim to know how
        # it was armed: evaluation_configured=False makes _collect_run_evidence
        # report the NETWORK's own armed interval rather than asserting one.
        # Neither field is read by the reality check.
        duration_s=1,
        evaluation_configured=False,
    )
    observed = validation.load_base_workbook(str(datasets.get_active_real_counts_excel()))
    target_map, movement_ids = _reality_check_targets(targets)

    report = reality_check.check_run_against_counts(
        run_result,
        observed,
        period=period,
        targets=target_map,
        movement_ids=movement_ids,
    )
    report["workbook_warnings"] = _planner_safe_warnings(report["workbook_warnings"])
    report["detail"] = {**report["detail"], "scenario_id": scenario_id, "result_db_count": 1}
    return report


@tool(
    name="run_reality_check",
    tier="read",
    provider="metrics",
    params=RunRealityCheckParams,
)
def run_reality_check(params: RunRealityCheckParams) -> dict[str, Any]:
    """Compare a completed run against the observed counts workbook node by node (GEH) and return, per node, either a concrete fix proposal or an evidenced refusal -- the modelled side is read from the RUN's own movement volumes, never from the workbook's pasted model column, and every node id that fails to join is named rather than dropped. Read-only: it applies nothing, and any proposal it returns still goes through the normal approval loop. Supply targets (the vehicle input / routing decision per node, from get_vehicle_inputs and get_routing_decisions) to get concrete proposals; without them a real miss still comes back, as a refusal naming what was missing."""
    report = build_reality_check_report(
        params.scenario_id, period=params.period, targets=params.targets
    )
    # The envelope message is the planner-facing one, so it is English like
    # every other tool's; the ko/en/ja triple rides in the report itself
    # (report["message"]) and in every proposal's own rationale, which is what
    # the UI and the audit trail render.
    return {"message": report["message"]["en"], "reality_check": report}

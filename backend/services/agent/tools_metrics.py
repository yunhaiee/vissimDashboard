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

Unit U10 added a second thing to resolve, of exactly the same kind: "which
OBSERVED counts". With ``observation_set_id`` the observed side is a stored
observation set (``services/observations.py``) instead of the base workbook,
and the site-id ↔ node-id map that makes such a set joinable is a tool
PARAMETER — survey site ids are not Vissim node ids, so pairing them is the
caller's knowledge, like ``movement_object_id`` and ``decision_movements``
already are. Resolution and translation live here; the comparison itself did
not change.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from services import (
    datasets,
    observations,
    results_evidence,
    results_reader,
    simulation_runs,
    validation,
)
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


class ObservationSiteNode(BaseModel):
    """One ``observation site id -> Vissim node id`` pair (unit U10).

    Supplied by the CALLER for exactly the reason ``movement_object_id`` is: an
    observation site id is a survey label — the Daejeon set's are intersection
    names read out of the file names, roadmap 1.2's documented deviation — and
    it does not match a Vissim node number. Pairing them by position, ordering
    or string similarity would produce a confidently wrong comparison, so an
    absent map is a refusal that names the sites still needing one.
    """

    model_config = ConfigDict(extra="forbid")

    site_id: str = Field(description="The site id as the observation set stores it.")
    node_id: str = Field(description="The Vissim node id that site was counted at.")


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
            "any, else its first period. Ignored when observation_set_id is given — a stored "
            "set's windows are named by the modeller, so use observation_period there."
        ),
    )
    targets: list[RealityCheckNodeTarget] | None = Field(
        default=None,
        description=(
            "Per-node edit targets. Without them every miss still comes back — as a refusal "
            "naming what was missing."
        ),
    )
    observation_set_id: str | None = Field(
        default=None,
        description=(
            "Compare against a STORED observation set (unit U10) instead of the base counts "
            "workbook. Requires site_nodes; the set's own periods are named by the modeller at "
            "ingest, so pick one with observation_period."
        ),
    )
    observation_period: str | None = Field(
        default=None,
        description=(
            "Which of the observation set's declared periods to compare against. Optional only "
            "when the set declares exactly one; otherwise the check refuses rather than picking."
        ),
    )
    site_nodes: list[ObservationSiteNode] | None = Field(
        default=None,
        description=(
            "The observation-site -> Vissim-node map. Required with observation_set_id: survey "
            "site ids are not node ids and are never matched by guesswork."
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


#: ``services.observations``' neutral problem codes -> this layer's report
#: reasons. The store must not import ``services.agent`` (it is consumed by the
#: non-agent import layer too), and ``reality_check`` owns the reason
#: vocabulary, so the translation lives here — in the layer whose job is
#: already "resolve which run and which counts", one dict, greppable from both
#: sides.
_OBSERVATION_PROBLEM_REASONS = {
    observations.SET_NOT_FOUND: reality_check.OBSERVATION_SET_NOT_FOUND,
    observations.PERIOD_ABSENT: reality_check.OBSERVATION_SET_PERIOD_ABSENT,
    observations.SITE_NODE_MAP_MISSING: reality_check.OBSERVATION_SITE_NODE_MAP_MISSING,
    observations.NO_MAPPED_SITES: reality_check.OBSERVATION_NO_MAPPED_SITES,
}


#: ``observations.py``'s ``count_basis`` values (see its ~1189-1218) -> the
#: phrase this label uses to describe what the observed side actually is.
#: Reading ``count_basis`` here is the whole fix: before this, EVERY
#: observation-store-sourced report claimed "aggregated turning counts" even
#: when the source (e.g. the SCATS/WA adapter) emits detector-loop totals
#: with no turning movement at all — the most visible provenance field in the
#: response naming a movement-level number the data never carried.
_COUNT_BASIS_PHRASES = {
    "turning_counts": "aggregated turning counts",
    "detector_counts": (
        "aggregated detector-loop volumes (site totals only — no turning movement in the source)"
    ),
}


def _count_basis_summary(count_basis_values: Any) -> str:
    """One or many ``count_basis`` values -> the truthful phrase for the label.

    ``count_basis_values`` may be a single string, an iterable of strings (one
    per node a report spans), or ``None``/empty when the basis was never
    resolved (e.g. the join failed before any node was read). A set that
    mixes both bases must say so — describing it as either alone would be
    exactly the false-precision this fix exists to remove.
    """
    if count_basis_values is None:
        values: set[str] = set()
    elif isinstance(count_basis_values, str):
        values = {count_basis_values}
    else:
        values = {v for v in count_basis_values if v}
    if not values:
        return "observed counts (basis not resolved)"
    if len(values) == 1:
        (only,) = values
        return _COUNT_BASIS_PHRASES.get(only, f"observed counts (basis: {only})")
    return "a mix of turning counts and detector-loop volumes across nodes (see each node's count_basis)"


def _observed_source_label(set_id: str, period: str | None, count_basis: Any = None) -> str:
    return (
        f"services.observations set {set_id!r} period {period or '-'!r} "
        f"-> {_count_basis_summary(count_basis)} (unit U10, ingested through the dataimport "
        "adapter + quality gate)"
    )


def _bounded_names(names: Any, limit: int = 12) -> str:
    """A comma-joined, BOUNDED name list for a refusal message.

    Bounded because a refusal's sentence is rendered straight into a chat
    bubble and the list it names tracks SITE count; "-" rather than an empty
    string so the sentence never reads as if the list were absent.
    """
    items = [str(item) for item in (names or [])]
    if not items:
        return "-"
    if len(items) <= limit:
        return ", ".join(items)
    return ", ".join(items[:limit]) + f", ... +{len(items) - limit} more"


def _observed_from_store(
    set_id: str,
    *,
    observation_period: str | None,
    site_nodes: list[ObservationSiteNode] | None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """``({node_id: NodeRealCounts}, site coverage)`` from a stored set.

    Returns ``(None, ...)`` when the observed side is unusable; the caller then
    builds a NOT_CHECKABLE report from the coverage's ``report`` entry. Every
    site that failed to join is NAMED in the coverage either way — it is built
    before any node is emitted, so a happy path cannot skip it.
    """
    mapping = {entry.site_id: entry.node_id for entry in (site_nodes or [])}
    side = observations.observed_side(set_id, period=observation_period, site_nodes=mapping)
    coverage = {
        "observation_set_id": set_id,
        "observation_period": side["period"],
        "observation_periods_available": side["periods"],
        **side["coverage"],
    }
    problem = side["problem"]
    if problem is None:
        return side["nodes"], coverage

    reason = _OBSERVATION_PROBLEM_REASONS.get(problem, reality_check.OBSERVATION_SET_NOT_FOUND)
    report = reality_check.not_checkable(
        reason,
        detail={"observation_set_id": set_id, "observation_period": observation_period},
        observation_set_id=set_id,
        period=side["period"] or observation_period or "-",
        periods=_bounded_names(side["periods"]),
        sites=_bounded_names(side["coverage"]["sites_without_node_mapping"])
        if side["coverage"]["sites_without_node_mapping"]
        else _bounded_names(side["coverage"]["observation_sites"]),
        unmapped=_bounded_names(side["coverage"]["sites_without_node_mapping"]),
        absent=_bounded_names(side["coverage"]["mapped_sites_absent_from_observations"]),
    )
    report["period"] = side["period"] or observation_period
    # Private helper, read-only, same package -- the one-way reuse
    # reality_check's own docstring describes (it borrows anomalies' private
    # state helpers for the same reason): one spelling of the provenance block
    # beats a second copy of it here that could drift.
    report["sources"] = reality_check._sources(
        _observed_source_label(set_id, side["period"] or observation_period)
    )
    report["coverage"]["observation_sites"] = coverage
    coverage["report"] = report
    return None, coverage


def build_reality_check_report(
    scenario_id: str,
    *,
    period: str | None = None,
    targets: list[RealityCheckNodeTarget] | None = None,
    observation_set_id: str | None = None,
    observation_period: str | None = None,
    site_nodes: list[ObservationSiteNode] | None = None,
) -> dict[str, Any]:
    """Resolve the run, load the counts, run the check. No COM, no writes.

    ``observation_set_id`` swaps the OBSERVED side from the base counts
    workbook to a stored observation set (unit U10). Everything else is
    unchanged: the modelled side still comes from THIS run's
    ``movement_volumes``, the grading is still U4's, and the site-level join is
    still reported in both directions rather than inner-joined away.
    """
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
    site_coverage: dict[str, Any] | None = None
    observed_source: str | None = None
    if observation_set_id:
        observed, site_coverage = _observed_from_store(
            observation_set_id,
            observation_period=observation_period,
            site_nodes=site_nodes,
        )
        if observed is None:
            report = site_coverage.pop("report")
            report["detail"] = {
                **report["detail"],
                "scenario_id": scenario_id,
                "result_db_count": 1,
            }
            return report
        # ``observed`` is {node_id: NodeRealCounts}, and each node already
        # carries ITS OWN count_basis (observations.observed_side, ~1189-1218)
        # — read straight from it rather than assuming every node shares one
        # basis, so a set that mixes turning-count and detector-only sites is
        # described as mixed instead of silently collapsed to either label.
        observed_source = _observed_source_label(
            observation_set_id,
            site_coverage["observation_period"],
            count_basis={node.get("count_basis") for node in observed.values()},
        )
        # The stored aggregates ARE a {node_id: NodeRealCounts} mapping, which
        # observed_nodes takes as-is and for which the period argument means
        # nothing (the window was already applied at ingest). Passing the
        # OBSERVATION period keeps report["period"] naming the window actually
        # compared instead of an AM/PM label that had no part in it.
        period = site_coverage["observation_period"]
    else:
        observed = validation.load_base_workbook(str(datasets.get_active_real_counts_excel()))
    target_map, movement_ids = _reality_check_targets(targets)

    report = reality_check.check_run_against_counts(
        run_result,
        observed,
        period=period,
        targets=target_map,
        movement_ids=movement_ids,
        observed_source=observed_source,
    )
    report["workbook_warnings"] = _planner_safe_warnings(report["workbook_warnings"])
    if site_coverage is not None:
        # The site-level join, carried into the same coverage block the node
        # join lives in. reality_check joins on NODE ids and cannot see sites;
        # dropping the site names here would leave "which count site never made
        # it into the comparison" answerable only by re-deriving it.
        report["coverage"]["observation_sites"] = site_coverage
    report["detail"] = {**report["detail"], "scenario_id": scenario_id, "result_db_count": 1}
    return report


@tool(
    name="run_reality_check",
    tier="read",
    provider="metrics",
    params=RunRealityCheckParams,
)
def run_reality_check(params: RunRealityCheckParams) -> dict[str, Any]:
    """Compare a completed run against the observed counts node by node (GEH) and return, per node, either a concrete fix proposal or an evidenced refusal -- the modelled side is read from the RUN's own movement volumes, never from the workbook's pasted model column, and every node id that fails to join is named rather than dropped. The observed side is the base counts workbook by default, or a stored observation set when observation_set_id is given (that path also needs site_nodes, because survey site ids are not Vissim node ids and are never matched by guesswork). Read-only: it applies nothing, and any proposal it returns still goes through the normal approval loop. Supply targets (the vehicle input / routing decision per node, from get_vehicle_inputs and get_routing_decisions) to get concrete proposals; without them a real miss still comes back, as a refusal naming what was missing."""
    report = build_reality_check_report(
        params.scenario_id,
        period=params.period,
        targets=params.targets,
        observation_set_id=params.observation_set_id,
        observation_period=params.observation_period,
        site_nodes=params.site_nodes,
    )
    # The envelope message is the planner-facing one, so it is English like
    # every other tool's; the ko/en/ja triple rides in the report itself
    # (report["message"]) and in every proposal's own rationale, which is what
    # the UI and the audit trail render.
    return {"message": report["message"]["en"], "reality_check": report}

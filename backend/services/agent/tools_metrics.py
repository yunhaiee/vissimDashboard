"""Read tools over simulation results: KPI summaries and scenario comparisons.

These wrap ``services.results_reader`` (which does the actual .db aggregation
and cross-scenario computation) — this module's job is compacting that output
for an LLM prompt: headline network averages only, computed leader/ranking
sections rather than raw per-node bulk, and NEVER a filesystem path (a tool's
data feeds the planner directly; a local .db path has no business there).

Domain misses (an unknown scenario id) are surfaced as successful tool calls
whose data says ``found: false`` + ``error_type`` + ``candidates`` — the
planner reads that and decides what to do next; this module never raises for
a bad id (see services.agent.toolkit's dispatch() docstring on that split).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict

from services import results_reader
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

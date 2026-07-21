"""Read VISSIM SQLite result databases and aggregate across runs."""
from __future__ import annotations

from functools import lru_cache
import logging
from pathlib import Path
import re
import sqlite3
import statistics
import xml.etree.ElementTree as ET

from services.datasets import get_active_dataset, get_active_inpx_path, get_active_modifications_dir, get_active_scenarios_dir
from services.inpx_parser import _node_center_from_link_segments
from services.scenario_project import list_project_scenarios
from services.simulation_runs import get_runtime_result_dir_for_scenario, list_completed_runtime_scenarios

logger = logging.getLogger(__name__)
CARDINAL_DIRECTIONS = ("north", "east", "south", "west")
CORE_NETWORK_METRICS = {
    "delay": {"network_key": "DELAYAVG", "higher_is_better": False},
    "speed": {"network_key": "SPEEDAVG", "higher_is_better": True},
    "stops": {"network_key": "STOPSAVG", "higher_is_better": False},
    "total_travel_time": {"network_key": "TRAVTMTOT", "higher_is_better": False},
}
CONGESTED_NODE_LIMIT = 5
SELECTED_NODE_DETAIL_LIMIT = 6
SELECTED_NODE_MOVEMENT_LIMIT = 6


def list_scenarios() -> list[dict]:
    project_scenarios = [
        {
            "id": scenario["id"],
            "name": scenario["name"],
            "color": scenario["color"],
        }
        for scenario in list_project_scenarios()
    ]
    runtime_scenarios = [
        {
            "id": scenario["id"],
            "name": scenario["name"],
            "color": scenario["color"],
        }
        for scenario in list_completed_runtime_scenarios()
    ]
    return project_scenarios + runtime_scenarios


def get_scenario_results(scenario_id: str) -> dict | None:
    if scenario_id not in _valid_scenario_ids():
        return None
    runtime_result_dir = get_runtime_result_dir_for_scenario(scenario_id)
    if runtime_result_dir is not None:
        return get_runtime_results(runtime_result_dir)
    return _aggregate_runs(scenario_id)


def get_compare(ids: list[str]) -> dict:
    requested_ids, scenario_results, invalid_ids, missing_result_ids = _collect_compare_results(ids)
    if not requested_ids or not scenario_results:
        return {}

    compare_payload = dict(scenario_results)
    compare_payload["_comparison"] = {
        "requested_ids": requested_ids,
        "scenario_ids": list(scenario_results.keys()),
        "invalid_ids": invalid_ids,
        "missing_result_ids": missing_result_ids,
        "network": _build_compare_network_summary(scenario_results),
        "node_directions": _build_compare_node_direction_summary(scenario_results),
    }
    return compare_payload


def get_compare_summary(ids: list[str]) -> dict:
    requested_ids, scenario_results, invalid_ids, missing_result_ids = _collect_compare_results(ids)
    scenario_ids = list(scenario_results.keys())
    scenario_meta = _scenario_meta_lookup()
    network_metrics = _build_core_network_metric_summary(scenario_results)
    node_directions = _build_compare_node_direction_summary(scenario_results)
    metric_leaders = _build_core_metric_leaders(network_metrics)
    health_scores = _build_scenario_health_scores(scenario_ids, network_metrics)
    top_congested_nodes = _build_top_congested_nodes(node_directions)

    return {
        "requested_ids": requested_ids,
        "scenario_ids": scenario_ids,
        "invalid_ids": invalid_ids,
        "missing_result_ids": missing_result_ids,
        "network_metrics": network_metrics,
        "metric_leaders": metric_leaders,
        "health_scores": health_scores,
        "top_congested_nodes": top_congested_nodes,
        "status_band": _build_status_band(
            requested_ids=requested_ids,
            scenario_ids=scenario_ids,
            invalid_ids=invalid_ids,
            missing_result_ids=missing_result_ids,
        ),
        "summary_cards": _build_summary_cards(
            requested_ids=requested_ids,
            scenario_ids=scenario_ids,
            network_metrics=network_metrics,
            node_directions=node_directions,
            top_congested_nodes=top_congested_nodes,
            health_scores=health_scores,
            scenario_meta=scenario_meta,
        ),
        "scenario_ranking": _build_scenario_ranking(
            health_scores=health_scores,
            network_metrics=network_metrics,
            scenario_meta=scenario_meta,
        ),
        "key_insights": _build_key_insights(
            metric_leaders=metric_leaders,
            network_metrics=network_metrics,
            top_congested_nodes=top_congested_nodes,
            invalid_ids=invalid_ids,
            missing_result_ids=missing_result_ids,
            scenario_meta=scenario_meta,
        ),
    }


def get_selected_node_details(ids: list[str], node_ids: list[str]) -> dict:
    requested_node_ids = _normalize_node_ids(node_ids)
    if not requested_node_ids:
        return {"selected_node_ids": [], "scenario_ids": [], "nodes": [], "missing_node_ids": []}

    requested_ids, scenario_results, invalid_ids, missing_result_ids = _collect_compare_results(ids)
    scenario_ids = list(scenario_results.keys())
    if not scenario_results:
        return {
            "selected_node_ids": requested_node_ids,
            "scenario_ids": scenario_ids,
            "nodes": [],
            "missing_node_ids": requested_node_ids,
            "invalid_ids": invalid_ids,
            "missing_result_ids": missing_result_ids,
        }

    scenario_meta = _scenario_meta_lookup()
    node_summaries = {
        str(row.get("node_id")): row
        for row in _build_compare_node_direction_summary(scenario_results)
        if row.get("node_id") is not None
    }

    nodes: list[dict] = []
    missing_node_ids: list[str] = []
    for node_id in requested_node_ids[:SELECTED_NODE_DETAIL_LIMIT]:
        summary = node_summaries.get(node_id)
        if summary is None:
            missing_node_ids.append(node_id)
            continue
        nodes.append(_build_selected_node_detail(node_id, summary, scenario_ids, scenario_results, scenario_meta))

    return {
        "selected_node_ids": requested_node_ids,
        "scenario_ids": scenario_ids,
        "nodes": nodes,
        "missing_node_ids": missing_node_ids,
        "invalid_ids": invalid_ids,
        "missing_result_ids": missing_result_ids,
    }


def get_runtime_results(job_dir: Path) -> dict | None:
    db_paths = sorted(job_dir.rglob("*.db"))
    return _aggregate_db_paths(db_paths)


def _query_rows_with_fallback(cur: sqlite3.Cursor, queries: list[str]) -> list[dict]:
    for query in queries:
        try:
            cur.execute(query)
            return [dict(record) for record in cur.fetchall()]
        except sqlite3.OperationalError:
            continue
    return []


def _read_db(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        data = {}

        cur.execute(
            """
            SELECT DELAYAVG, STOPSAVG, SPEEDAVG, DELAYSTOPAVG,
                   DISTTOT, TRAVTMTOT, DELAYTOT, VEHACT, VEHARR
            FROM VEHICLENETWORKPERFORMANCEMEASUREMENT_EvaluationTimeIntervalClass
            WHERE ARG_VEHICLECLASS = 'ALL' LIMIT 1
            """
        )
        row = cur.fetchone()
        if row:
            data["network"] = dict(row)

        cur.execute(
            """
            SELECT OBJECT_ID, VEHS, VEHDELAY, STOPDELAY, STOPS
            FROM MOVEMENT_EvaluationTimeIntervalClass
            WHERE ARG_VEHICLECLASS = 'ALL'
            """
        )
        data["movements"] = [dict(record) for record in cur.fetchall()]
        data["movement_interval_class"] = _query_rows_with_fallback(
            cur,
            [
                """
                SELECT OBJECT_ID, ARG_EVALUATIONTIMEINTERVAL AS ARG_INTERVAL, ARG_VEHICLECLASS,
                       VEHS, VEHDELAY, STOPDELAY, STOPS
                FROM MOVEMENT_EvaluationTimeIntervalClass
                """,
                """
                SELECT OBJECT_ID, ARG_EVALUATIONINTERVAL AS ARG_INTERVAL, ARG_VEHICLECLASS,
                       VEHS, VEHDELAY, STOPDELAY, STOPS
                FROM MOVEMENT_EvaluationTimeIntervalClass
                """,
            ],
        )

        cur.execute(
            """
            SELECT OBJECT_ID, VEHS, TRAVTM, DISTTRAV
            FROM VEHICLETRAVELTIMEMEASUREMENT_EvaluationTimeIntervalClass
            WHERE ARG_VEHICLECLASS = 'ALL'
            """
        )
        data["traveltime"] = [dict(record) for record in cur.fetchall()]
        data["traveltime_interval_class"] = _query_rows_with_fallback(
            cur,
            [
                """
                SELECT OBJECT_ID, ARG_TIMEINTERVAL AS ARG_INTERVAL, ARG_VEHICLECLASS,
                       VEHS, TRAVTM, DISTTRAV
                FROM VEHICLETRAVELTIMEMEASUREMENT_EvaluationTimeIntervalClass
                """,
                """
                SELECT OBJECT_ID, ARG_EVALUATIONTIMEINTERVAL AS ARG_INTERVAL, ARG_VEHICLECLASS,
                       VEHS, TRAVTM, DISTTRAV
                FROM VEHICLETRAVELTIMEMEASUREMENT_EvaluationTimeIntervalClass
                """,
            ],
        )

        # Data collection point measurements are optional — some models (e.g. the
        # SouthSt base) don't enable them, so the table may be absent. Fall back to
        # an empty list instead of failing the whole run.
        data["datacollection"] = _query_rows_with_fallback(
            cur,
            [
                """
                SELECT OBJECT_ID, VEHS, SPEEDAVGARITH, SPEEDAVGHARM, QUEUEDELAY
                FROM DATACOLLECTIONMEASUREMENT_EvaluationTimeIntervalClass
                WHERE ARG_VEHICLECLASS = 'ALL'
                """
            ],
        )

        cur.execute(
            """
            SELECT OBJECT_ID, QLEN, QLENMAX, SUMDELAYS, SUMSTOPS
            FROM MOVEMENT_EvaluationTimeInterval
            """
        )
        data["queues"] = [dict(record) for record in cur.fetchall()]

        return data
    finally:
        conn.close()


def _safe_mean(values: list) -> float | None:
    filtered = [value for value in values if value is not None]
    return statistics.mean(filtered) if filtered else None


def _aggregate_runs(scenario_id: str) -> dict | None:
    results_dir = get_active_scenarios_dir() / scenario_id / f"{scenario_id}.results"
    db_paths = sorted(results_dir.glob("*.db"), key=lambda path: _numeric_sort_key(path.stem))
    return _aggregate_db_paths(db_paths)


def _aggregate_db_paths(db_paths: list[Path]) -> dict | None:
    runs = []
    for db_path in db_paths:
        if db_path.exists():
            try:
                runs.append(_read_db(db_path))
            except Exception:
                logger.exception("Failed to read %s", db_path)

    if not runs:
        return None

    network_keys = [
        "DELAYAVG",
        "STOPSAVG",
        "SPEEDAVG",
        "DELAYSTOPAVG",
        "DISTTOT",
        "TRAVTMTOT",
        "DELAYTOT",
        "VEHACT",
        "VEHARR",
    ]
    network_agg = {}
    for key in network_keys:
        values = [
            run["network"][key]
            for run in runs
            if run.get("network") and run["network"].get(key) is not None
        ]
        network_agg[key] = _safe_mean(values)
        if len(values) > 1:
            network_agg[f"{key}_STDEV"] = statistics.stdev(values)

    def agg_by_id(key: str, fields: list[str]) -> list[dict]:
        by_id: dict[str, list] = {}
        for run in runs:
            for row in run.get(key, []):
                by_id.setdefault(row["OBJECT_ID"], []).append(row)

        result = []
        for object_id, rows in by_id.items():
            entry = {"OBJECT_ID": object_id}
            for field in fields:
                values = [row.get(field) for row in rows if row.get(field) is not None]
                entry[f"{field}_MEAN"] = _safe_mean(values)
                if len(values) > 1:
                    entry[f"{field}_STDEV"] = statistics.stdev(values)
            result.append(entry)
        return result

    def agg_by_dimensions(key: str, group_fields: list[str], fields: list[str]) -> list[dict]:
        buckets: dict[tuple, list] = {}
        for run in runs:
            for row in run.get(key, []):
                group_key = tuple(row.get(field) for field in group_fields)
                buckets.setdefault(group_key, []).append(row)

        result = []
        for group_key, rows in buckets.items():
            entry = {field: group_key[index] for index, field in enumerate(group_fields)}
            for field in fields:
                values = [row.get(field) for row in rows if row.get(field) is not None]
                entry[f"{field}_MEAN"] = _safe_mean(values)
                if len(values) > 1:
                    entry[f"{field}_STDEV"] = statistics.stdev(values)
            result.append(entry)
        return result

    movements_agg = agg_by_id("movements", ["VEHDELAY", "STOPDELAY", "STOPS", "VEHS"])

    return {
        "run_count": len(runs),
        "network": network_agg,
        "traveltime": agg_by_id("traveltime", ["TRAVTM", "DISTTRAV", "VEHS"]),
        "traveltime_interval_class": agg_by_dimensions(
            "traveltime_interval_class",
            ["OBJECT_ID", "ARG_INTERVAL", "ARG_VEHICLECLASS"],
            ["TRAVTM", "DISTTRAV", "VEHS"],
        ),
        "datacollection": agg_by_id("datacollection", ["SPEEDAVGARITH", "SPEEDAVGHARM", "QUEUEDELAY", "VEHS"]),
        "movements": movements_agg,
        "movement_interval_class": agg_by_dimensions(
            "movement_interval_class",
            ["OBJECT_ID", "ARG_INTERVAL", "ARG_VEHICLECLASS"],
            ["VEHS", "VEHDELAY", "STOPDELAY", "STOPS"],
        ),
        "queues": agg_by_id("queues", ["QLEN", "QLENMAX", "SUMDELAYS"]),
        "node_directions": _build_node_directions(movements_agg),
    }


def _normalize_requested_ids(ids: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_id in ids:
        scenario_id = raw_id.strip()
        if not scenario_id or scenario_id in seen:
            continue
        seen.add(scenario_id)
        normalized.append(scenario_id)
    return normalized


def _collect_compare_results(ids: list[str]) -> tuple[list[str], dict[str, dict], list[str], list[str]]:
    requested_ids = _normalize_requested_ids(ids)
    if not requested_ids:
        return [], {}, [], []

    valid_ids = _valid_scenario_ids()
    invalid_ids = [scenario_id for scenario_id in requested_ids if scenario_id not in valid_ids]

    scenario_results: dict[str, dict] = {}
    missing_result_ids: list[str] = []
    for scenario_id in requested_ids:
        if scenario_id not in valid_ids:
            continue
        aggregated = get_scenario_results(scenario_id)
        if aggregated is None:
            missing_result_ids.append(scenario_id)
            continue
        scenario_results[scenario_id] = aggregated

    return requested_ids, scenario_results, invalid_ids, missing_result_ids


def _build_compare_network_summary(results_by_scenario: dict[str, dict]) -> dict[str, dict]:
    metric_keys = sorted(
        {
            metric
            for payload in results_by_scenario.values()
            for metric in payload.get("network", {}).keys()
        }
    )

    summary: dict[str, dict] = {}
    for metric in metric_keys:
        values_by_scenario = {
            scenario_id: _coerce_float(payload.get("network", {}).get(metric))
            for scenario_id, payload in results_by_scenario.items()
        }
        present_values = [value for value in values_by_scenario.values() if value is not None]
        summary[metric] = {
            "values": values_by_scenario,
            "average": _safe_mean(present_values),
            "min": min(present_values) if present_values else None,
            "max": max(present_values) if present_values else None,
            "spread": (max(present_values) - min(present_values)) if present_values else None,
        }
    return summary


def _build_core_network_metric_summary(results_by_scenario: dict[str, dict]) -> dict[str, dict]:
    scenario_ids = list(results_by_scenario.keys())
    summary: dict[str, dict] = {}
    for metric_name, config in CORE_NETWORK_METRICS.items():
        values_by_scenario = {
            scenario_id: _coerce_float(
                results_by_scenario.get(scenario_id, {}).get("network", {}).get(config["network_key"])
            )
            for scenario_id in scenario_ids
        }
        present_values = [value for value in values_by_scenario.values() if value is not None]
        summary[metric_name] = {
            "network_key": config["network_key"],
            "values": values_by_scenario,
            "average": _safe_mean(present_values),
            "min": min(present_values) if present_values else None,
            "max": max(present_values) if present_values else None,
            "spread": (max(present_values) - min(present_values)) if present_values else None,
        }
    return summary


def _build_core_metric_leaders(network_metrics: dict[str, dict]) -> dict[str, dict]:
    leaders: dict[str, dict] = {}
    for metric_name, config in CORE_NETWORK_METRICS.items():
        metric_summary = network_metrics.get(metric_name) or {}
        values = metric_summary.get("values") or {}
        present_values = [(scenario_id, value) for scenario_id, value in values.items() if value is not None]

        leader_id: str | None = None
        leader_value: float | None = None
        if present_values:
            comparator = max if config["higher_is_better"] else min
            leader_value = comparator(value for _, value in present_values)
            candidate_ids = [scenario_id for scenario_id, value in present_values if value == leader_value]
            leader_id = min(candidate_ids) if candidate_ids else None

        leaders[metric_name] = {
            "scenario_id": leader_id,
            "value": leader_value,
            "higher_is_better": config["higher_is_better"],
        }
    return leaders


def _build_scenario_health_scores(scenario_ids: list[str], network_metrics: dict[str, dict]) -> list[dict]:
    health_scores: list[dict] = []
    for scenario_id in scenario_ids:
        components: dict[str, float | None] = {}
        for metric_name, config in CORE_NETWORK_METRICS.items():
            metric_summary = network_metrics.get(metric_name) or {}
            metric_value = _coerce_float((metric_summary.get("values") or {}).get(scenario_id))
            min_value = _coerce_float(metric_summary.get("min"))
            max_value = _coerce_float(metric_summary.get("max"))

            if metric_value is None or min_value is None or max_value is None:
                components[metric_name] = None
                continue

            spread = max_value - min_value
            if spread <= 0:
                components[metric_name] = 100.0
                continue

            if config["higher_is_better"]:
                normalized = ((metric_value - min_value) / spread) * 100
            else:
                normalized = ((max_value - metric_value) / spread) * 100
            components[metric_name] = _clamp(normalized, 0.0, 100.0)

        available_scores = [value for value in components.values() if value is not None]
        score = _safe_mean(available_scores) or 0.0
        health_scores.append(
            {
                "scenario_id": scenario_id,
                "score": score,
                "label": _health_label(score),
                "components": components,
            }
        )
    return health_scores


def _health_label(score: float) -> str:
    if score >= 85:
        return "excellent"
    if score >= 70:
        return "good"
    if score >= 50:
        return "watch"
    if score >= 30:
        return "poor"
    return "critical"


def _build_top_congested_nodes(node_summaries: list[dict]) -> list[dict]:
    ranked_rows = sorted(
        node_summaries,
        key=lambda row: (
            _coerce_float(row.get("total_average")) or 0.0,
            _coerce_float(row.get("total_max")) or 0.0,
            -(_coerce_float(row.get("total_min")) or 0.0),
        ),
        reverse=True,
    )

    top_nodes: list[dict] = []
    for row in ranked_rows[:CONGESTED_NODE_LIMIT]:
        totals = row.get("totals") or {}
        present_totals = [(scenario_id, value) for scenario_id, value in totals.items() if value is not None]
        leader_id = None
        leader_value = None
        if present_totals:
            leader_value = max(value for _, value in present_totals)
            leader_candidates = [scenario_id for scenario_id, value in present_totals if value == leader_value]
            leader_id = min(leader_candidates) if leader_candidates else None

        top_nodes.append(
            {
                "node_id": row.get("node_id"),
                "node_name": row.get("node_name"),
                "average_total": row.get("total_average"),
                "min_total": row.get("total_min"),
                "max_total": row.get("total_max"),
                "spread": row.get("total_spread"),
                "scenario_totals": totals,
                "leader": {"scenario_id": leader_id, "value": leader_value},
            }
        )
    return top_nodes


def _build_status_band(
    *,
    requested_ids: list[str],
    scenario_ids: list[str],
    invalid_ids: list[str],
    missing_result_ids: list[str],
) -> dict:
    loaded_count = len(scenario_ids)
    requested_count = len(requested_ids)

    if loaded_count == 0:
        return {
            "tone": "error",
            "label": "No comparison ready",
            "title": "No scenario results were available.",
            "message": "Check the requested scenario ids and confirm result databases exist before comparing.",
            "detail": (
                f"Requested {requested_count}, invalid {len(invalid_ids)}, missing results {len(missing_result_ids)}."
            ),
        }

    if invalid_ids or missing_result_ids:
        return {
            "tone": "warning",
            "label": "Partial comparison",
            "title": f"{loaded_count} of {requested_count} scenarios are ready for comparison.",
            "message": "Some requested scenarios were skipped because they were invalid or missing result databases.",
            "detail": (
                f"Loaded {loaded_count}, invalid {len(invalid_ids)}, missing results {len(missing_result_ids)}."
            ),
        }

    if loaded_count == 1:
        return {
            "tone": "neutral",
            "label": "Single scenario loaded",
            "title": "One scenario is ready.",
            "message": "Select at least one additional scenario to unlock delta-based comparison insights.",
            "detail": f"Loaded {loaded_count} scenario with valid result data.",
        }

    return {
        "tone": "success",
        "label": "Comparison ready",
        "title": f"{loaded_count} scenarios are ready for side-by-side analysis.",
        "message": "Intersection, corridor, and network metrics are available for cross-scenario comparison.",
        "detail": f"All {requested_count} requested scenarios loaded successfully.",
    }


def _build_summary_cards(
    *,
    requested_ids: list[str],
    scenario_ids: list[str],
    network_metrics: dict[str, dict],
    node_directions: list[dict],
    top_congested_nodes: list[dict],
    health_scores: list[dict],
    scenario_meta: dict[str, dict],
) -> list[dict]:
    cards: list[dict] = [
        {
            "id": "scenarios_ready",
            "label": "Scenarios ready",
            "value": len(scenario_ids),
            "unit": "of requested",
            "description": f"{len(requested_ids)} requested scenario ids",
            "delta": len(scenario_ids) - len(requested_ids),
            "trend": "flat",
            "tone": "success" if scenario_ids else "error",
        },
        {
            "id": "tracked_intersections",
            "label": "Tracked intersections",
            "value": len(node_directions),
            "description": "Nodes with directional movement totals",
            "tone": "neutral",
        },
    ]

    best_score = max(health_scores, key=lambda row: row.get("score") or 0, default=None)
    if best_score is not None:
        scenario = scenario_meta.get(best_score["scenario_id"], {})
        cards.append(
            {
                "id": "best_health",
                "label": "Best health score",
                "value": round(best_score["score"], 1),
                "unit": "pts",
                "description": scenario.get("name") or best_score["scenario_id"],
                "tone": "success" if best_score["score"] >= 70 else "warning",
            }
        )

    speed_spread = _coerce_float((network_metrics.get("speed") or {}).get("spread"))
    cards.append(
        {
            "id": "speed_spread",
            "label": "Speed spread",
            "value": round(speed_spread, 1) if speed_spread is not None else "-",
            "unit": "km/h" if speed_spread is not None else None,
            "description": "Difference between fastest and slowest scenario",
            "tone": "warning" if (speed_spread or 0) > 0 else "neutral",
        }
    )

    if top_congested_nodes:
        busiest = top_congested_nodes[0]
        cards.append(
            {
                "id": "top_hotspot",
                "label": "Top hotspot",
                "value": busiest.get("node_name") or busiest.get("node_id") or "-",
                "description": f"Average total volume {round(_coerce_float(busiest.get('average_total')) or 0):,.0f}",
                "tone": "warning",
            }
        )

    return cards


def _build_scenario_ranking(
    *,
    health_scores: list[dict],
    network_metrics: dict[str, dict],
    scenario_meta: dict[str, dict],
) -> list[dict]:
    if not health_scores:
        return []

    ranking: list[dict] = []
    sorted_scores = sorted(
        health_scores,
        key=lambda row: (row.get("score") or 0, row.get("scenario_id") or ""),
        reverse=True,
    )
    for index, row in enumerate(sorted_scores, start=1):
        scenario_id = row["scenario_id"]
        meta = scenario_meta.get(scenario_id, {})
        best_metric = _best_component_metric(row.get("components") or {})
        reason = _ranking_reason(
            best_metric=best_metric,
            network_metrics=network_metrics,
            scenario_id=scenario_id,
        )
        ranking.append(
            {
                "scenario_id": scenario_id,
                "scenario_name": meta.get("name") or scenario_id,
                "color": meta.get("color"),
                "rank": index,
                "score": row.get("score"),
                "score_label": row.get("label"),
                "reason": reason,
            }
        )
    return ranking


def _build_key_insights(
    *,
    metric_leaders: dict[str, dict],
    network_metrics: dict[str, dict],
    top_congested_nodes: list[dict],
    invalid_ids: list[str],
    missing_result_ids: list[str],
    scenario_meta: dict[str, dict],
) -> list[dict]:
    insights: list[dict] = []

    speed_leader = metric_leaders.get("speed") or {}
    speed_scenario_id = speed_leader.get("scenario_id")
    speed_value = _coerce_float(speed_leader.get("value"))
    if speed_scenario_id and speed_value is not None:
        scenario_name = scenario_meta.get(speed_scenario_id, {}).get("name") or speed_scenario_id
        insights.append(
            {
                "id": "speed-leader",
                "title": "Fastest network scenario",
                "message": f"{scenario_name} leads average network speed at {speed_value:.1f} km/h.",
                "tone": "success",
                "scenario_id": speed_scenario_id,
                "metric": "speed",
                "value": speed_value,
                "unit": "km/h",
            }
        )

    delay_leader = metric_leaders.get("delay") or {}
    delay_scenario_id = delay_leader.get("scenario_id")
    delay_value = _coerce_float(delay_leader.get("value"))
    if delay_scenario_id and delay_value is not None:
        scenario_name = scenario_meta.get(delay_scenario_id, {}).get("name") or delay_scenario_id
        insights.append(
            {
                "id": "delay-leader",
                "title": "Lowest network delay",
                "message": f"{scenario_name} has the lowest average delay at {delay_value:.1f} s.",
                "tone": "success",
                "scenario_id": delay_scenario_id,
                "metric": "delay",
                "value": delay_value,
                "unit": "s",
            }
        )

    if top_congested_nodes:
        hotspot = top_congested_nodes[0]
        hotspot_name = hotspot.get("node_name") or hotspot.get("node_id") or "Unknown node"
        hotspot_spread = _coerce_float(hotspot.get("spread")) or 0.0
        hotspot_average = _coerce_float(hotspot.get("average_total")) or 0.0
        insights.append(
            {
                "id": "hotspot-node",
                "title": "Primary intersection hotspot",
                "message": (
                    f"{hotspot_name} is the busiest compared node, averaging {hotspot_average:.0f} total volume "
                    f"with a {hotspot_spread:.0f} spread across scenarios."
                ),
                "tone": "warning",
                "metric": "intersection_volume",
                "value": hotspot_average,
                "unit": "veh",
            }
        )

    if invalid_ids or missing_result_ids:
        insights.append(
            {
                "id": "coverage-warning",
                "title": "Comparison coverage warning",
                "message": (
                    f"Skipped {len(invalid_ids)} invalid ids and {len(missing_result_ids)} scenarios without result data."
                ),
                "tone": "warning",
            }
        )

    speed_spread = _coerce_float((network_metrics.get("speed") or {}).get("spread"))
    if speed_spread is not None and speed_spread > 0:
        insights.append(
            {
                "id": "speed-spread",
                "title": "Speed variance to review",
                "message": f"Network average speed varies by {speed_spread:.1f} km/h across compared scenarios.",
                "tone": "neutral",
                "metric": "speed_spread",
                "value": speed_spread,
                "unit": "km/h",
            }
        )

    return insights


def _best_component_metric(components: dict[str, float | None]) -> str | None:
    available = [(metric, value) for metric, value in components.items() if value is not None]
    if not available:
        return None
    return max(available, key=lambda item: item[1])[0]


def _ranking_reason(
    *,
    best_metric: str | None,
    network_metrics: dict[str, dict],
    scenario_id: str,
) -> str:
    if best_metric is None:
        return "Insufficient metric coverage for a reasoned ranking."

    metric_summary = network_metrics.get(best_metric) or {}
    metric_value = _coerce_float((metric_summary.get("values") or {}).get(scenario_id))
    if metric_value is None:
        return f"Balanced overall health score with limited {best_metric} coverage."

    if best_metric == "speed":
        return f"Strongest normalized speed performance at {metric_value:.1f} km/h."
    if best_metric == "delay":
        return f"Best normalized delay result at {metric_value:.1f} s."
    if best_metric == "stops":
        return f"Fewest normalized stop events at {metric_value:.1f}."
    if best_metric == "total_travel_time":
        return f"Lowest normalized total travel time at {metric_value:.1f} s."
    return f"Best normalized {best_metric} performance."


def _scenario_meta_lookup() -> dict[str, dict]:
    return {scenario["id"]: scenario for scenario in list_scenarios()}


def _build_compare_node_direction_summary(results_by_scenario: dict[str, dict]) -> list[dict]:
    scenario_ids = list(results_by_scenario.keys())
    node_rows_by_id: dict[str, dict[str, dict]] = {}
    node_names: dict[str, str] = {}

    for scenario_id, payload in results_by_scenario.items():
        for row in payload.get("node_directions") or []:
            node_id = str(row.get("node_id", "")).strip()
            if not node_id:
                continue
            node_rows_by_id.setdefault(node_id, {})[scenario_id] = row
            candidate_name = str(row.get("node_name") or "").strip()
            if candidate_name:
                node_names[node_id] = candidate_name

    summaries: list[dict] = []
    for node_id in sorted(node_rows_by_id, key=_numeric_sort_key):
        node_name = node_names.get(node_id, f"Node {node_id}")
        scenario_rows = {
            scenario_id: _normalize_node_direction_row(
                node_id=node_id,
                node_name=node_name,
                row=node_rows_by_id[node_id].get(scenario_id),
            )
            for scenario_id in scenario_ids
        }
        total_values = [row["total"] for row in scenario_rows.values()]
        summaries.append(
            {
                "node_id": node_id,
                "node_name": node_name,
                "scenario_rows": scenario_rows,
                "totals": {
                    scenario_id: row["total"] for scenario_id, row in scenario_rows.items()
                },
                "total_average": _safe_mean(total_values),
                "total_min": min(total_values) if total_values else 0.0,
                "total_max": max(total_values) if total_values else 0.0,
                "total_spread": (max(total_values) - min(total_values)) if total_values else 0.0,
            }
        )
    return summaries


def _build_selected_node_detail(
    node_id: str,
    summary: dict,
    scenario_ids: list[str],
    results_by_scenario: dict[str, dict],
    scenario_meta: dict[str, dict],
) -> dict:
    scenario_details: list[dict] = []
    for scenario_id in scenario_ids:
        result = results_by_scenario.get(scenario_id) or {}
        row = (summary.get("scenario_rows") or {}).get(scenario_id) or {}
        movement_metrics = _build_node_movement_metrics(result, node_id)
        volume = _directional_volume(row)
        direction_delays = _estimate_direction_delays(movement_metrics.get("avg_delay_sec"), volume["directions"])
        scenario_details.append(
            {
                "scenario_id": scenario_id,
                "scenario_name": scenario_meta.get(scenario_id, {}).get("name") or scenario_id,
                "present": bool(row.get("present")),
                "total_volume": _round_metric(row.get("total")),
                "incoming_total": _round_metric(row.get("incoming_total")),
                "outgoing_total": _round_metric(row.get("outgoing_total")),
                "incoming": _compact_direction_map(row.get("incoming")),
                "outgoing": _compact_direction_map(row.get("outgoing")),
                "top_approaches": _rank_direction_map(volume["directions"]),
                "top_movements": _compact_lane_flows(row.get("lane_flows")),
                "avg_delay_sec": _round_metric(movement_metrics.get("avg_delay_sec")),
                "worst_movement_delay_sec": _round_metric(movement_metrics.get("worst_movement_delay_sec")),
                "max_queue_length": _round_metric(movement_metrics.get("max_queue_length")),
                "stops_total": _round_metric(movement_metrics.get("stops_total")),
                "movement_count": movement_metrics.get("movement_count", 0),
                "direction_delay_estimates": direction_delays,
                "direction_delay_is_estimated": bool(direction_delays),
            }
        )

    _attach_baseline_deltas(scenario_details)

    return {
        "node_id": node_id,
        "node_name": summary.get("node_name") or f"Node {node_id}",
        "total_average": _round_metric(summary.get("total_average")),
        "total_min": _round_metric(summary.get("total_min")),
        "total_max": _round_metric(summary.get("total_max")),
        "total_spread": _round_metric(summary.get("total_spread")),
        "scenarios": scenario_details,
        "comparison": _build_selected_node_comparison(scenario_details),
    }


def _build_node_movement_metrics(result: dict, node_id: str) -> dict:
    rows = [row for row in result.get("movements") or [] if _object_belongs_to_node(row.get("OBJECT_ID"), node_id)]
    weighted_delay = 0.0
    weight_total = 0.0
    delays: list[float] = []
    stops_total = 0.0
    stops_seen = False

    for row in rows:
        delay = _coerce_float(_metric_value(row, "VEHDELAY_MEAN", "VEHDELAY"))
        vehs = _coerce_float(_metric_value(row, "VEHS_MEAN", "VEHS"))
        stops = _coerce_float(_metric_value(row, "STOPS_MEAN", "STOPS"))
        if delay is not None:
            delays.append(delay)
            weight = vehs if vehs is not None and vehs > 0 else 1.0
            weighted_delay += delay * weight
            weight_total += weight
        if stops is not None:
            stops_total += stops
            stops_seen = True

    queue_values = [
        value
        for row in result.get("queues") or []
        if _object_belongs_to_node(row.get("OBJECT_ID"), node_id)
        for value in (
            _coerce_float(_metric_value(row, "QLENMAX_MEAN", "QLENMAX")),
            _coerce_float(_metric_value(row, "QLEN_MEAN", "QLEN")),
        )
        if value is not None
    ]

    return {
        "avg_delay_sec": weighted_delay / weight_total if weight_total > 0 else None,
        "worst_movement_delay_sec": max(delays) if delays else None,
        "max_queue_length": max(queue_values) if queue_values else None,
        "stops_total": stops_total if stops_seen else None,
        "movement_count": len(rows),
    }


def _directional_volume(row: dict) -> dict:
    incoming = _compact_direction_map(row.get("incoming"))
    outgoing = _compact_direction_map(row.get("outgoing"))
    incoming_total = sum(incoming.values())
    if incoming_total > 0:
        return {"source": "incoming", "directions": incoming, "total": incoming_total}
    return {"source": "outgoing", "directions": outgoing, "total": sum(outgoing.values())}


def _estimate_direction_delays(avg_delay: object, direction_volumes: dict[str, float]) -> dict[str, float | None]:
    delay = _coerce_float(avg_delay)
    if delay is None:
        return {}
    max_volume = max(direction_volumes.values(), default=0.0)
    if max_volume <= 0:
        return {direction: _round_metric(delay) for direction in CARDINAL_DIRECTIONS}
    return {
        direction: _round_metric(delay * (0.75 + (volume / max_volume) * 0.5)) if volume > 0 else None
        for direction, volume in direction_volumes.items()
    }


def _build_selected_node_comparison(scenarios: list[dict]) -> dict:
    return {
        "highest_volume": _scenario_extreme(scenarios, "total_volume", prefer_max=True),
        "lowest_delay": _scenario_extreme(scenarios, "avg_delay_sec", prefer_max=False),
        "highest_delay": _scenario_extreme(scenarios, "avg_delay_sec", prefer_max=True),
        "highest_queue": _scenario_extreme(scenarios, "max_queue_length", prefer_max=True),
        "largest_volume_change_from_baseline": _largest_delta(scenarios, "total_volume"),
        "largest_delay_change_from_baseline": _largest_delta(scenarios, "avg_delay_sec"),
    }


def _scenario_extreme(scenarios: list[dict], metric: str, *, prefer_max: bool) -> dict | None:
    candidates = [
        (scenario, _coerce_float(scenario.get(metric)))
        for scenario in scenarios
        if _coerce_float(scenario.get(metric)) is not None
    ]
    if not candidates:
        return None
    scenario, value = (max if prefer_max else min)(candidates, key=lambda item: item[1])
    return {
        "scenario_id": scenario.get("scenario_id"),
        "scenario_name": scenario.get("scenario_name"),
        "metric": metric,
        "value": _round_metric(value),
    }


def _largest_delta(scenarios: list[dict], metric: str) -> dict | None:
    candidates = []
    for scenario in scenarios:
        delta = ((scenario.get("delta_from_baseline") or {}).get(metric))
        value = _coerce_float(delta)
        if value is not None:
            candidates.append((scenario, value))
    if not candidates:
        return None
    scenario, value = max(candidates, key=lambda item: abs(item[1]))
    return {
        "scenario_id": scenario.get("scenario_id"),
        "scenario_name": scenario.get("scenario_name"),
        "metric": metric,
        "value": _round_metric(value),
    }


def _attach_baseline_deltas(scenarios: list[dict]) -> None:
    baseline = next((scenario for scenario in scenarios if scenario.get("present")), None)
    if baseline is None:
        return
    for scenario in scenarios:
        deltas: dict[str, float | None] = {}
        for metric in ("total_volume", "incoming_total", "outgoing_total", "avg_delay_sec", "max_queue_length", "stops_total"):
            current_value = _coerce_float(scenario.get(metric))
            baseline_value = _coerce_float(baseline.get(metric))
            deltas[metric] = _round_metric(current_value - baseline_value) if current_value is not None and baseline_value is not None else None
        scenario["baseline_scenario_id"] = baseline.get("scenario_id")
        scenario["delta_from_baseline"] = deltas


def _compact_direction_map(values: dict | None) -> dict[str, float]:
    return {
        direction: _round_metric((values or {}).get(direction)) or 0.0
        for direction in CARDINAL_DIRECTIONS
    }


def _rank_direction_map(values: dict[str, float], limit: int = 4) -> list[dict]:
    return [
        {"direction": direction, "volume": _round_metric(volume)}
        for direction, volume in sorted(values.items(), key=lambda item: item[1], reverse=True)[:limit]
        if volume > 0
    ]


def _compact_lane_flows(flows: list[dict] | None) -> list[dict]:
    ranked = sorted(flows or [], key=lambda flow: _coerce_float(flow.get("volume")) or 0.0, reverse=True)
    return [
        {
            "from_direction": flow.get("from_direction"),
            "to_direction": flow.get("to_direction"),
            "from_lane": flow.get("from_lane"),
            "to_lane": flow.get("to_lane"),
            "volume": _round_metric(flow.get("volume")),
            "estimated": bool(flow.get("estimated")),
        }
        for flow in ranked[:SELECTED_NODE_MOVEMENT_LIMIT]
    ]


def _normalize_node_ids(node_ids: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_id in node_ids:
        node_id = str(raw_id).strip()
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        normalized.append(node_id)
    return normalized


def _object_belongs_to_node(object_id: object, node_id: str) -> bool:
    text = str(object_id or "").strip()
    normalized_node_id = str(node_id).strip()
    return text == normalized_node_id or text.startswith(f"{normalized_node_id}-") or text.startswith(f"{normalized_node_id}:")


def _round_metric(value: object, digits: int = 1) -> float | None:
    number = _coerce_float(value)
    if number is None:
        return None
    return round(number, digits)


def _metric_value(row: dict, mean_key: str, raw_key: str) -> object:
    value = row.get(mean_key)
    return value if value is not None else row.get(raw_key)


def _normalize_node_direction_row(*, node_id: str, node_name: str, row: dict | None) -> dict:
    incoming = _normalize_direction_map((row or {}).get("incoming"))
    outgoing = _normalize_direction_map((row or {}).get("outgoing"))
    incoming_lanes = _normalize_lane_count_map((row or {}).get("incoming_lanes"))
    outgoing_lanes = _normalize_lane_count_map((row or {}).get("outgoing_lanes"))
    lane_flows = [_normalize_lane_flow(flow) for flow in (row or {}).get("lane_flows", [])]
    incoming_total = sum(incoming.values())
    outgoing_total = sum(outgoing.values())

    return {
        "node_id": node_id,
        "node_name": node_name,
        "present": row is not None,
        "incoming": incoming,
        "outgoing": outgoing,
        "incoming_lanes": incoming_lanes,
        "outgoing_lanes": outgoing_lanes,
        "lane_flows": lane_flows,
        "incoming_total": incoming_total,
        "outgoing_total": outgoing_total,
        "total": incoming_total + outgoing_total,
    }


def _normalize_direction_map(values: dict | None) -> dict[str, float]:
    return {
        direction: _coerce_float((values or {}).get(direction)) or 0.0
        for direction in CARDINAL_DIRECTIONS
    }


def _normalize_lane_count_map(values: dict | None) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for direction in CARDINAL_DIRECTIONS:
        value = _coerce_float((values or {}).get(direction))
        normalized[direction] = max(0, int(round(value or 0)))
    return normalized


def _normalize_lane_flow(flow: dict) -> dict:
    return {
        "from_direction": str(flow.get("from_direction") or ""),
        "to_direction": str(flow.get("to_direction") or ""),
        "from_lane": max(1, int(round(_coerce_float(flow.get("from_lane")) or 1))),
        "to_lane": max(1, int(round(_coerce_float(flow.get("to_lane")) or 1))),
        "volume": _coerce_float(flow.get("volume")) or 0.0,
        "estimated": bool(flow.get("estimated", False)),
    }


def _coerce_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _numeric_sort_key(value: str) -> tuple[int, object]:
    return (0, int(value)) if value.isdigit() else (1, value)


def _valid_scenario_ids() -> set[str]:
    project_ids = {scenario["id"] for scenario in list_project_scenarios()}
    runtime_ids = {scenario["id"] for scenario in list_completed_runtime_scenarios()}
    return project_ids | runtime_ids


@lru_cache(maxsize=8)
def _load_movement_metadata(inpx_path_str: str, modifications_dir_str: str, revision: str) -> dict[str, dict]:
    inpx_path = Path(inpx_path_str)
    modifications_dir = Path(modifications_dir_str)
    tree = ET.parse(inpx_path)
    root = tree.getroot()

    node_lookup: dict[str, tuple[float, float, str]] = {}
    nodes_el = root.find(".//nodes")
    if nodes_el is not None:
        for node in nodes_el.findall("node"):
            points = []
            points_el = node.find("geometry/points")
            if points_el is not None:
                for point in points_el.findall("point"):
                    points.append((float(point.get("x", "0")), float(point.get("y", "0"))))
            if points:
                cx = sum(x for x, _ in points) / len(points)
                cy = sum(y for _, y in points) / len(points)
            else:
                cx = cy = 0.0
            node_lookup[node.get("no", "")] = (cx, cy, node.get("name", "") or f"Node {node.get('no', '')}")

    link_lookup: dict[str, list[tuple[float, float]]] = {}
    link_name_lookup: dict[str, str] = {}
    link_lane_lookup: dict[str, int] = {}
    connector_lane_lookup: dict[tuple[str, str], list[tuple[int | None, int | None]]] = {}
    links_el = root.find(".//links")
    if links_el is not None:
        for link in links_el.findall("link"):
            link_id = link.get("no", "")
            pts = []
            pts_el = link.find("geometry/linkPolyPts")
            if pts_el is not None:
                for point in pts_el.findall("linkPolyPoint"):
                    pts.append((float(point.get("x", "0")), float(point.get("y", "0"))))
            link_lookup[link_id] = pts
            link_name_lookup[link_id] = link.get("name", "") or ""
            lanes_el = link.find("lanes")
            link_lane_lookup[link_id] = len(lanes_el.findall("lane")) if lanes_el is not None else 1

            from_end = link.find("fromLinkEndPt")
            to_end = link.find("toLinkEndPt")
            from_link_id = from_end.get("link", "") if from_end is not None else ""
            to_link_id = to_end.get("link", "") if to_end is not None else ""
            if not from_link_id or not to_link_id:
                continue

            raw_pairs: list[tuple[int | None, int | None]] = []
            if lanes_el is not None:
                for lane in lanes_el.findall("lane"):
                    from_lanes = _extract_lane_numbers(lane.find("fromLanes"))
                    to_lanes = _extract_lane_numbers(lane.find("toLanes"))
                    if not from_lanes and not to_lanes:
                        continue
                    from_candidates = from_lanes or [None]
                    to_candidates = to_lanes or [None]
                    for from_lane in from_candidates:
                        for to_lane in to_candidates:
                            raw_pairs.append((from_lane, to_lane))

            if raw_pairs:
                deduped: list[tuple[int | None, int | None]] = []
                seen_pairs: set[tuple[int | None, int | None]] = set()
                for pair in raw_pairs:
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    deduped.append(pair)
                connector_lane_lookup.setdefault((from_link_id, to_link_id), []).extend(deduped)

    # Modifications 폴더의 .trax 파일에서 추가 노드 로드 + 기존 노드 이름 패치
    if modifications_dir.is_dir():
        for trax_file in sorted(modifications_dir.iterdir()):
            if trax_file.suffix.lower() != ".trax":
                continue
            try:
                trax_root = ET.parse(trax_file).getroot()
                for trax_node in trax_root.findall(".//node"):
                    nid = trax_node.get("no", "")
                    if not nid:
                        continue
                    trax_name = trax_node.get("name", "")
                    if nid in node_lookup:
                        # 기존 노드 이름이 비어있거나 fallback("Node N")이면 trax 이름으로 업데이트
                        cx, cy, existing_name = node_lookup[nid]
                        if trax_name and (not existing_name or existing_name == f"Node {nid}"):
                            node_lookup[nid] = (cx, cy, trax_name)
                        continue
                    # 새 노드: geometry/points 직속 자식만 인식
                    pts_el = trax_node.find("geometry/points")
                    if pts_el is None:
                        continue
                    pts = [
                        (float(pt.get("x", "0")), float(pt.get("y", "0")))
                        for pt in pts_el.findall("point")
                    ]
                    if not pts:
                        continue
                    cx = sum(x for x, _ in pts) / len(pts)
                    cy = sum(y for _, y in pts) / len(pts)
                    node_lookup[nid] = (cx, cy, trax_name or f"Node {nid}")
            except Exception:
                logger.exception("Failed to load nodes from %s", trax_file)

    # Base nodes carry no <geometry><points> (they are defined by the links that
    # cross them via <linkSegs>), so the centroid above defaulted to (0, 0).
    # Recover a real centre from the linkSeg crossing points — otherwise every
    # movement gets classified into the same compass direction.
    if nodes_el is not None:
        for node in nodes_el.findall("node"):
            nid = node.get("no", "")
            entry = node_lookup.get(nid)
            if entry is None or entry[0] != 0.0 or entry[1] != 0.0:
                continue
            cx, cy = _node_center_from_link_segments(node, link_lookup)
            if (cx, cy) != (0.0, 0.0):
                node_lookup[nid] = (cx, cy, entry[2])

    metadata: dict[str, dict] = {}

    # Movement object ids only appear in the DB; prepare helpers for interpreting them.
    for node_id, (node_x, node_y, node_name) in node_lookup.items():
        metadata[node_id] = {
            "node_id": node_id,
            "node_name": node_name,
            "center": (node_x, node_y),
            "links": link_lookup,
            "link_names": link_name_lookup,
            "link_lanes": link_lane_lookup,
            "connector_lane_map": connector_lane_lookup,
        }

    return metadata


def _build_node_directions(movements: list[dict]) -> list[dict]:
    node_meta = _load_movement_metadata(
        str(get_active_inpx_path()),
        str(get_active_modifications_dir()),
        str(get_active_dataset().get("revision") or ""),
    )
    by_node: dict[str, dict] = {}
    directions = CARDINAL_DIRECTIONS

    for movement in movements:
        object_id = str(movement.get("OBJECT_ID", ""))
        match = re.match(r"^(?P<node>\d+)-(?P<from>\d+)-[-\d.]+-(?P<to>\d+)-[-\d.]+$", object_id)
        if not match:
            continue

        node_id = match.group("node")
        meta = node_meta.get(node_id)
        if meta is None:
            continue

        incoming_dir = _classify_link_direction(meta["center"], meta["links"].get(match.group("from"), []))
        outgoing_dir = _classify_link_direction(meta["center"], meta["links"].get(match.group("to"), []))
        vehs = float(movement.get("VEHS_MEAN") or 0)
        from_link_id = match.group("from")
        to_link_id = match.group("to")

        entry = by_node.setdefault(
            node_id,
            {
                "node_id": node_id,
                "node_name": meta["node_name"],
                "incoming": {"north": 0.0, "east": 0.0, "south": 0.0, "west": 0.0},
                "outgoing": {"north": 0.0, "east": 0.0, "south": 0.0, "west": 0.0},
                "incoming_lanes": {"north": 0, "east": 0, "south": 0, "west": 0},
                "outgoing_lanes": {"north": 0, "east": 0, "south": 0, "west": 0},
                "_incoming_lane_tokens": {direction: set() for direction in directions},
                "_outgoing_lane_tokens": {direction: set() for direction in directions},
                "_lane_flow_map": {},
            },
        )
        entry["incoming"][incoming_dir] += vehs
        entry["outgoing"][outgoing_dir] += vehs

        from_lane_count = int(meta["link_lanes"].get(from_link_id, 1))
        to_lane_count = int(meta["link_lanes"].get(to_link_id, 1))
        connector_pairs = _expand_connector_lane_pairs(
            meta["connector_lane_map"].get((from_link_id, to_link_id), []),
            from_lane_count,
            to_lane_count,
        )
        estimated = False
        if not connector_pairs:
            connector_pairs = _fallback_lane_pairs(from_lane_count, to_lane_count)
            estimated = True

        share = vehs / max(len(connector_pairs), 1)
        for from_lane, to_lane in connector_pairs:
            from_token = (from_link_id, int(from_lane))
            to_token = (to_link_id, int(to_lane))
            entry["_incoming_lane_tokens"][incoming_dir].add(from_token)
            entry["_outgoing_lane_tokens"][outgoing_dir].add(to_token)
            flow_key = (incoming_dir, outgoing_dir, from_token, to_token, estimated)
            entry["_lane_flow_map"][flow_key] = entry["_lane_flow_map"].get(flow_key, 0.0) + share

    result = sorted(by_node.values(), key=lambda item: int(item["node_id"]))
    for entry in result:
        incoming_slot_map: dict[str, dict[tuple[str, int], int]] = {}
        outgoing_slot_map: dict[str, dict[tuple[str, int], int]] = {}
        for direction in directions:
            in_tokens = sorted(
                list(entry["_incoming_lane_tokens"][direction]),
                key=lambda token: (int(token[0]), token[1]),
            )
            out_tokens = sorted(
                list(entry["_outgoing_lane_tokens"][direction]),
                key=lambda token: (int(token[0]), token[1]),
            )
            incoming_slot_map[direction] = {
                token: index + 1 for index, token in enumerate(in_tokens)
            }
            outgoing_slot_map[direction] = {
                token: index + 1 for index, token in enumerate(out_tokens)
            }
            entry["incoming_lanes"][direction] = len(in_tokens)
            entry["outgoing_lanes"][direction] = len(out_tokens)

        lane_flow_slot_map: dict[tuple[str, str, int, int, bool], float] = {}
        for (from_dir, to_dir, from_token, to_token, estimated), volume in entry["_lane_flow_map"].items():
            from_slot = incoming_slot_map[from_dir].get(from_token, 1)
            to_slot = outgoing_slot_map[to_dir].get(to_token, 1)
            slot_key = (from_dir, to_dir, from_slot, to_slot, estimated)
            lane_flow_slot_map[slot_key] = lane_flow_slot_map.get(slot_key, 0.0) + volume

        entry["lane_flows"] = [
            {
                "from_direction": from_dir,
                "to_direction": to_dir,
                "from_lane": from_slot,
                "to_lane": to_slot,
                "volume": volume,
                "estimated": estimated,
            }
            for (from_dir, to_dir, from_slot, to_slot, estimated), volume in sorted(
                lane_flow_slot_map.items(),
                key=lambda item: item[1],
                reverse=True,
            )
        ]

        entry.pop("_incoming_lane_tokens", None)
        entry.pop("_outgoing_lane_tokens", None)
        entry.pop("_lane_flow_map", None)
    return result


def _extract_lane_numbers(lanes_el: ET.Element | None) -> list[int]:
    if lanes_el is None:
        return []
    lane_numbers: list[int] = []
    for ref in lanes_el.findall("objectRef"):
        parsed = _parse_lane_ref(ref.get("key", ""))
        if parsed is None:
            continue
        _, lane_no = parsed
        lane_numbers.append(lane_no)
    return lane_numbers


def _parse_lane_ref(key: str) -> tuple[str, int] | None:
    parts = key.strip().split()
    if len(parts) != 2:
        return None
    link_id, lane = parts[0], parts[1]
    if not link_id.isdigit():
        return None
    try:
        lane_no = int(float(lane))
    except ValueError:
        return None
    return link_id, lane_no


def _expand_connector_lane_pairs(
    raw_pairs: list[tuple[int | None, int | None]],
    from_lane_count: int,
    to_lane_count: int,
) -> list[tuple[int, int]]:
    expanded: list[tuple[int, int]] = []
    for from_lane, to_lane in raw_pairs:
        from_candidates = [from_lane] if from_lane is not None else list(range(1, from_lane_count + 1))
        to_candidates = [to_lane] if to_lane is not None else list(range(1, to_lane_count + 1))
        for from_candidate in from_candidates:
            for to_candidate in to_candidates:
                if from_candidate is None or to_candidate is None:
                    continue
                expanded.append((int(from_candidate), int(to_candidate)))

    deduped: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for pair in expanded:
        if pair in seen:
            continue
        seen.add(pair)
        deduped.append(pair)
    return deduped


def _fallback_lane_pairs(from_lane_count: int, to_lane_count: int) -> list[tuple[int, int]]:
    if from_lane_count <= 0 or to_lane_count <= 0:
        return [(1, 1)]

    pair_count = max(from_lane_count, to_lane_count)
    return [
        ((index % from_lane_count) + 1, (index % to_lane_count) + 1)
        for index in range(pair_count)
    ]


def _classify_link_direction(node_center: tuple[float, float], points: list[tuple[float, float]]) -> str:
    if len(points) < 2:
        return "east"

    node_x, node_y = node_center
    start = points[0]
    end = points[-1]
    start_dist = _distance(node_center, start)
    end_dist = _distance(node_center, end)
    far = end if start_dist <= end_dist else start

    dx = far[0] - node_x
    dy = far[1] - node_y
    if abs(dx) >= abs(dy):
        return "east" if dx >= 0 else "west"
    return "north" if dy >= 0 else "south"


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

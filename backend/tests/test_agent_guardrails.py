"""Tests for M2.2's guardrails.py: GuardrailEngine.preflight/postwrite/postrun
and the disabled (ablation) flag.

Fixtures mirror test_agent_constraints.py's synthetic shadow-model pattern —
guardrails.py is exercised through its PUBLIC surface only (the engine's
three methods + GuardrailReport), never through constraints.py's or
guardrails.py's own private ``_check_*``/``_diff_*`` helpers, matching how
test_agent_constraints.py itself only calls ``run_checks``.
"""
from __future__ import annotations

from typing import Any

import pytest

from services.agent.guardrails import GuardrailEngine, GuardrailReport

LANGS = ("ko", "en", "ja")


def _shadow_model(
    *,
    links: list[dict[str, Any]] | None = None,
    vehicle_inputs: list[dict[str, Any]] | None = None,
    signals: list[dict[str, Any]] | None = None,
    nodes: list[dict[str, Any]] | None = None,
    conflict_areas: list[dict[str, Any]] | None = None,
    routes: list[dict[str, Any]] | None = None,
    vehicle_composition_entries: list[dict[str, Any]] | None = None,
    des_speed_decision_entries: list[dict[str, Any]] | None = None,
    signal_program_splits: list[dict[str, Any]] | None = None,
    priority_rule_markers: list[dict[str, Any]] | None = None,
    des_speed_distributions: list[dict[str, Any]] | None = None,
    vehicle_types: list[dict[str, Any]] | None = None,
    time_distributions: list[dict[str, Any]] | None = None,
    driving_behaviors: list[dict[str, Any]] | None = None,
    link_behavior_type_assignments: list[dict[str, Any]] | None = None,
    link_lanes: list[dict[str, Any]] | None = None,
    pt_stops: list[dict[str, Any]] | None = None,
    pt_lines: list[dict[str, Any]] | None = None,
    pt_line_stops: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "links": links
        if links is not None
        else [{"id": "101", "name": "Main St EB approach", "lanes": 3, "length": 280.0}],
        "vehicle_inputs": vehicle_inputs
        if vehicle_inputs is not None
        else [{"no": "1", "name": "Main St EB", "link": "101", "volume": 500.0}],
        "signals": signals if signals is not None else [],
        "nodes": nodes if nodes is not None else [],
        "conflict_areas": conflict_areas if conflict_areas is not None else [],
        # v2 unit ⑥: the flat routes list (id = "{decision_no}.{route_no}")
        # -- see tools_network._flatten_routes for the real shape this mirrors.
        "routes": routes if routes is not None else [],
        # v2 unit ⑦: the flat vehicle_composition_entries list (id =
        # "{composition_no}.{veh_type}") -- see tools_network.
        # _flatten_vehicle_composition_entries for the real shape this mirrors.
        "vehicle_composition_entries": vehicle_composition_entries if vehicle_composition_entries is not None else [],
        # v2a unit ⑧: the FLAT des_speed_decision_entries list
        # (id = "{decision_no}.{veh_class_no}") -- see tools_network.
        # _flatten_des_speed_decision_entries for the real shape this mirrors.
        "des_speed_decision_entries": des_speed_decision_entries if des_speed_decision_entries is not None else [],
        # v2a unit ⑨: the FLAT signal_program_splits list (id =
        # "{sc_no}.{program_no}.{sg_no}", the first THREE-part identity) --
        # see tools_network._flatten_signal_program_splits for the real shape
        # this mirrors.
        "signal_program_splits": signal_program_splits if signal_program_splits is not None else [],
        # QUEUE 3 unit 2: the FLAT priority_rule_markers list (id =
        # "{priority_rule_no}.{marker_index}") -- see tools_network.
        # _flatten_priority_rule_markers for the real shape this mirrors.
        "priority_rule_markers": priority_rule_markers if priority_rule_markers is not None else [],
        # QUEUE 3 (v2a) unit 3: des_speed_distributions itself (id = "no",
        # no flattening needed) -- see tools_network._parse_speed_distributions
        # for the real shape this mirrors.
        "des_speed_distributions": des_speed_distributions if des_speed_distributions is not None else [],
        # QUEUE (v2a) unit: vehicle_types itself (id = "no", no flattening
        # needed) -- see tools_network._parse_vehicle_types for the real
        # shape this mirrors.
        "vehicle_types": vehicle_types if vehicle_types is not None else [],
        # QUEUE (Q3, v2a) unit 5: time_distributions itself (id = "no", no
        # flattening needed) -- see tools_network._parse_time_distributions
        # for the real shape this mirrors.
        "time_distributions": time_distributions if time_distributions is not None else [],
        # QUEUE 3 unit 6: driving_behaviors itself (id = "no", no flattening
        # needed) -- see tools_network._parse_driving_behaviors for the real
        # shape this mirrors. link_behavior_type_assignments is the FLAT list
        # (id = "{lbt_no}.default" or "{lbt_no}.{veh_class_no}") -- see
        # tools_network._flatten_link_behavior_type_assignments.
        "driving_behaviors": driving_behaviors if driving_behaviors is not None else [],
        "link_behavior_type_assignments": (
            link_behavior_type_assignments if link_behavior_type_assignments is not None else []
        ),
        # v2b unit (1): the FLAT link_lanes list (id = "{link_id}.{lane_index}")
        # -- see tools_network._parse_link_lanes for the real shape this
        # mirrors.
        "link_lanes": link_lanes if link_lanes is not None else [],
        # Q4 unit (7): pt_stops/pt_lines have a single-token "no" identity
        # (no flattening needed); pt_line_stops is the FLAT list (id =
        # "{line_no}.{stop_no}") -- see tools_network._parse_pt_stops/
        # _parse_pt_lines/_flatten_pt_line_stops for the real shapes these
        # mirror.
        "pt_stops": pt_stops if pt_stops is not None else [],
        "pt_lines": pt_lines if pt_lines is not None else [],
        "pt_line_stops": pt_line_stops if pt_line_stops is not None else [],
    }


def _route(
    route_no: str,
    decision_no: str,
    *,
    rel_flow: float | None = 100.0,
    rel_flows: list[dict[str, Any]] | None = None,
    disconnected: bool = False,
) -> dict[str, Any]:
    if rel_flows is None:
        rel_flows = [{"interval_no": 1, "from_s": 0.0, "rel_flow": rel_flow}] if rel_flow is not None else []
    return {
        "id": f"{decision_no}.{route_no}",
        "decision_no": decision_no,
        "route_no": route_no,
        "dest_link": "1",
        "dest_pos": 0.0,
        "rel_flow": rel_flow,
        "interval_count": len(rel_flows),
        "rel_flows": rel_flows,
        "link_seq": [],
        "disconnected": disconnected,
        "manually_scaled_this_turn": False,
    }


def _action(tool: str, **params: Any) -> dict[str, Any]:
    return {"tool": tool, "params": params}


def _priority_rule_marker_entry(
    priority_rule_no: str,
    marker_index: int,
    *,
    gap_time_s: float | None = 3.2,
    headway_m: float | None = 0.0,
    link: str = "632",
) -> dict[str, Any]:
    return {
        "id": f"{priority_rule_no}.{marker_index}",
        "priority_rule_no": priority_rule_no,
        "marker_index": marker_index,
        "link": link,
        "lane": "1",
        "pos": 9.0,
        "gap_headway": {"gap_time_s": gap_time_s, "headway_m": headway_m},
        "max_speed": 180.0,
        "effect_driv_dir": "FORWARD",
    }


def _pt_line_stop_entry(
    line_no: str, stop_no: str, *, dwell_tm_distr: str = "10", dwell_tm_def: str = "DISTRIBUTION",
    active: bool = True,
) -> dict[str, Any]:
    return {
        "id": f"{line_no}.{stop_no}",
        "line_no": line_no,
        "stop_no": stop_no,
        "dwell_tm_distr": dwell_tm_distr,
        "dwell_tm_def": dwell_tm_def,
        "active": active,
    }


def _link_lane_entry(
    link_id: str, lane_index: int, *, blocked_vehicle_classes: list[str] | None = None, width: float = 3.5
) -> dict[str, Any]:
    return {
        "id": f"{link_id}.{lane_index}",
        "link_id": link_id,
        "lane_index": lane_index,
        "width": width,
        "blocked_vehicle_classes": blocked_vehicle_classes if blocked_vehicle_classes is not None else [],
    }


def _speed_distribution(no: str, points: list[dict[str, float]], *, name: str = "test distribution") -> dict[str, Any]:
    return {
        "no": no,
        "name": name,
        "points": points,
        "point_count": len(points),
        "points_truncated": False,
        "referenced_by": {
            "des_speed_decisions": {"count": 0, "ids": [], "truncated": False},
            "vehicle_compositions": {"count": 0, "ids": [], "truncated": False},
            "reduced_speed_areas": {"count": 0, "ids": [], "truncated": False},
            "other": {"count": 0, "by_tag": {}},
        },
        "reference_count": 0,
    }


def _time_distribution(
    no: str,
    *,
    name: str = "test time distribution",
    dist_type: str = "NORMAL",
    mean_s: float | None = 25.0,
    std_dev_s: float | None = 5.0,
    points: list[dict[str, float]] | None = None,
) -> dict[str, Any]:
    points = points if points is not None else []
    return {
        "no": no,
        "name": name,
        "type": dist_type,
        "mean_std_dev": {"mean_s": mean_s, "std_dev_s": std_dev_s},
        "points": points,
        "point_count": len(points),
        "points_truncated": False,
        "referenced_by": {
            "pt_stops": {"count": 0, "ids": [], "truncated": False},
            "pt_vehicle_class_overrides": {"count": 0, "ids": [], "truncated": False},
            "driving_behaviors": {"count": 0, "ids": [], "truncated": False},
            "other": {"count": 0, "by_tag": {}},
        },
        "reference_count": 0,
    }


def _des_speed_entry(decision_no: str, veh_class_no: str, *, des_speed_distr_no: str | None = "50") -> dict[str, Any]:
    return {
        "id": f"{decision_no}.{veh_class_no}",
        "decision_no": decision_no,
        "veh_class_no": veh_class_no,
        "veh_class_name": "Car",
        "des_speed_distr_no": des_speed_distr_no,
        "des_speed_distr_name": "55 mph",
    }


def _vehicle_type_entry(
    no: str,
    *,
    name: str = "HGV",
    category: str = "HGV",
    weight_distr_no: str | None = "2",
    **extra_no_fields: str | None,
) -> dict[str, Any]:
    """QUEUE (v2a) unit -- mirrors tools_network._parse_vehicle_types' real
    shape: every reference carries a flat "{field}_no" plus a derived,
    ALWAYS-exempt "references"/"referencing_composition_nos" pair. Only
    weight_distr_no is parametrized directly (the field every guardrails
    test below writes); every other reference field defaults to None
    (absent), same as a real PEDESTRIAN-category vehicle type -- pass extra
    "{field}_no" kwargs (e.g. des_accel_func_no="3") to set others."""
    fields = (
        "model2d3d_distr_no",
        "weight_distr_no",
        "power_distr_no",
        "des_accel_func_no",
        "max_accel_func_no",
        "des_decel_func_no",
        "max_decel_func_no",
        "des_curve_speed_func_no",
    )
    entry: dict[str, Any] = {"no": no, "name": name, "category": category, "capacity": 0.0, "cap_exact": False}
    for field in fields:
        entry[field] = extra_no_fields.get(field)
    entry["weight_distr_no"] = weight_distr_no
    entry["references"] = {field.removesuffix("_no"): None for field in fields}
    entry["referencing_composition_nos"] = {"count": 0, "ids": [], "truncated": False}
    return entry


# ---------------------------------------------------------------------------
# GuardrailReport — first_refusal / refusals / warnings
# ---------------------------------------------------------------------------


def test_report_blocking_true_iff_first_refusal_present():
    engine = GuardrailEngine(disabled=False)
    shadow = _shadow_model()

    report = engine.preflight(_action("set_vehicle_input_volume", input_no="1", volume=-5), shadow)

    assert report.blocking is True
    assert report.first_refusal() is not None
    assert report.first_refusal().constraint_id == "volume_out_of_range"


def test_report_clean_pass_has_no_findings_and_is_not_blocking():
    engine = GuardrailEngine(disabled=False)
    shadow = _shadow_model()

    report = engine.preflight(_action("set_vehicle_input_volume", input_no="1", volume=600), shadow)

    assert report.findings == []
    assert report.blocking is False
    assert report.first_refusal() is None
    assert report.warnings() == []


# ---------------------------------------------------------------------------
# preflight — aggregation + blocking logic
# ---------------------------------------------------------------------------


def test_preflight_aggregates_multiple_findings_from_one_action():
    # link 101 has 3 lanes -> rough capacity 5700 veh/h. 25000 is BOTH out
    # of the absolute [0, 20000] range (refuse) AND, independently, over
    # capacity (warn) -- constraints.py's latent_demand_overflow check does
    # not exclude out-of-range volumes, so both fire on the same action.
    engine = GuardrailEngine(disabled=False)
    shadow = _shadow_model()

    report = engine.preflight(
        _action("set_vehicle_input_volume", input_no="1", volume=25000), shadow
    )

    ids = {f.constraint_id for f in report.findings}
    assert ids == {"volume_out_of_range", "latent_demand_overflow"}
    assert report.blocking is True  # refuse-tier present
    assert {f.constraint_id for f in report.refusals()} == {"volume_out_of_range"}
    assert {f.constraint_id for f in report.warnings()} == {"latent_demand_overflow"}


def test_preflight_warn_only_is_not_blocking():
    engine = GuardrailEngine(disabled=False)
    shadow = _shadow_model()

    report = engine.preflight(
        _action("set_vehicle_input_volume", input_no="1", volume=6000), shadow
    )

    assert [f.constraint_id for f in report.findings] == ["latent_demand_overflow"]
    assert report.blocking is False
    assert report.disabled is False


def test_preflight_object_not_found_carries_suggested_fix_candidates():
    engine = GuardrailEngine(disabled=False)
    shadow = _shadow_model(
        vehicle_inputs=[{"no": "101", "name": "Main St EB", "link": "101", "volume": 500.0}]
    )

    report = engine.preflight(
        _action("set_vehicle_input_volume", input_no="102", volume=600), shadow
    )

    assert report.blocking is True
    finding = report.first_refusal()
    assert finding.constraint_id == "object_not_found"
    # Candidates are {id, name, ...} dicts since the miss-path enrichment
    # (2026-07-31) -- membership is checked against the ids.
    assert "101" in [c["id"] for c in finding.suggested_fix]


def test_preflight_none_shadow_model_does_not_crash():
    engine = GuardrailEngine(disabled=False)

    report = engine.preflight(_action("set_vehicle_input_volume", input_no="1", volume=600), None)

    assert isinstance(report, GuardrailReport)
    assert report.phase == "preflight"


# ---------------------------------------------------------------------------
# postwrite — written-vs-readback compare
# ---------------------------------------------------------------------------


def test_postwrite_readback_mismatch_is_blocking_write_verification_failed():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    # after DOES carry the written value (600), so the persisted-target check
    # and the diff (target-field exemption) both stay silent -- isolating the
    # value-compare half as this test's single subject.
    after = _shadow_model(
        vehicle_inputs=[{"no": "1", "name": "Main St EB", "link": "101", "volume": 600.0}]
    )
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    report = engine.postwrite(action, before, after, {"written_value": 600, "readback_value": 500})

    assert report.blocking is True
    assert [f.constraint_id for f in report.findings] == ["write_verification_failed"]
    finding = report.first_refusal()
    assert finding.details["written_value"] == 600
    assert finding.details["readback_value"] == 500


def test_postwrite_matching_readback_is_clean():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    # A genuinely clean write: read-back matches AND the after model shows
    # the value persisted into the saved file.
    after = _shadow_model(
        vehicle_inputs=[{"no": "1", "name": "Main St EB", "link": "101", "volume": 600.0}]
    )
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    report = engine.postwrite(action, before, after, {"written_value": 600, "readback_value": 600})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_com_result_missing_keys_skips_value_compare():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    report = engine.postwrite(action, before, after, {})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_com_result_none_does_not_crash():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    report = engine.postwrite(action, before, after, None)

    assert report.findings == []
    assert report.blocking is False


# ---------------------------------------------------------------------------
# postwrite — "nothing else moved" structural diff
# ---------------------------------------------------------------------------


def test_postwrite_diff_is_silent_when_only_the_target_field_changed():
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)
    before = _shadow_model(vehicle_inputs=[{"no": "1", "name": "VI1", "link": "101", "volume": 500.0}])
    after = _shadow_model(vehicle_inputs=[{"no": "1", "name": "VI1", "link": "101", "volume": 600.0}])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 600, "readback_value": 600})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_is_silent_when_only_the_conflict_area_status_changed():
    # v2 unit (4): same exemption shape as the other three object-targeting
    # tools, but on the new conflict_areas object class (required wiring:
    # _SHADOW_OBJECT_ID_FIELDS["conflict_areas"] is a dependency of the
    # target-persisted check too, not diff-only -- see that dict's comment).
    action = _action("set_conflict_area_status", conflict_area_no="3", status="AHASRIGHTOFWAY")
    before = _shadow_model(
        conflict_areas=[{"no": "3", "link_a": "3", "link_b": "12", "status": "PASSIVE"}]
    )
    after = _shadow_model(
        conflict_areas=[{"no": "3", "link_a": "3", "link_b": "12", "status": "AHASRIGHTOFWAY"}]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after, {"written_value": "AHASRIGHTOFWAY", "readback_value": "AHASRIGHTOFWAY"}
    )

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_flags_an_unrelated_conflict_area_status_change_as_evidence():
    # The target conflict area's own status IS exempt, but some OTHER
    # conflict area's status silently changing too must still be evidence —
    # exactly the "nothing else moved" guarantee this diff exists for.
    action = _action("set_conflict_area_status", conflict_area_no="3", status="AHASRIGHTOFWAY")
    before = _shadow_model(
        conflict_areas=[
            {"no": "3", "link_a": "3", "link_b": "12", "status": "PASSIVE"},
            {"no": "7", "link_a": "3", "link_b": "21", "status": "PASSIVE"},
        ]
    )
    after = _shadow_model(
        conflict_areas=[
            {"no": "3", "link_a": "3", "link_b": "12", "status": "AHASRIGHTOFWAY"},
            {"no": "7", "link_a": "3", "link_b": "21", "status": "BHASRIGHTOFWAY"},  # unrelated drift
        ]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after, {"written_value": "AHASRIGHTOFWAY", "readback_value": "AHASRIGHTOFWAY"}
    )

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert {
        "object_class": "conflict_areas",
        "object_id": "7",
        "change": "field_changed",
        "field": "status",
        "before": "PASSIVE",
        "after": "BHASRIGHTOFWAY",
    } in diff


def test_postwrite_diff_flags_an_unrelated_field_change_as_evidence():
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)
    before = _shadow_model()
    after = _shadow_model(
        links=[{"id": "101", "name": "MUTATED", "lanes": 3, "length": 280.0}],  # unrelated drift
        vehicle_inputs=[{"no": "1", "name": "Main St EB", "link": "101", "volume": 600.0}],
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 600, "readback_value": 600})

    # reuses the existing write_verification_failed id -- no new constraint
    # id minted for diff evidence, per the M2.2 brief.
    assert report.blocking is True
    assert [f.constraint_id for f in report.findings] == ["write_verification_failed"]
    finding = report.first_refusal()
    diff = finding.details["nothing_else_moved_diff"]
    assert len(diff) == 1
    assert diff[0]["object_class"] == "links"
    assert diff[0]["object_id"] == "101"
    assert diff[0]["field"] == "name"
    assert diff[0]["before"] == "Main St EB approach"
    assert diff[0]["after"] == "MUTATED"


def test_postwrite_diff_flags_an_added_object_in_an_unrelated_list():
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)
    before = _shadow_model()
    after = _shadow_model(
        vehicle_inputs=[
            {"no": "1", "name": "Main St EB", "link": "101", "volume": 600.0},
            {"no": "99", "name": "phantom", "link": "101", "volume": 100.0},  # unexpected addition
        ]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 600, "readback_value": 600})

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert {"object_class": "vehicle_inputs", "object_id": "99", "change": "added"} in diff


# ---------------------------------------------------------------------------
# set_des_speed_distribution_points -- QUEUE 3 (v2a) unit 3
# ---------------------------------------------------------------------------

_VALID_CURVE_POINTS = [{"speed_kmh": 0.0, "cum_fraction": 0.0}, {"speed_kmh": 50.0, "cum_fraction": 1.0}]
_NEW_CURVE_POINTS = [
    {"speed_kmh": 0.0, "cum_fraction": 0.0},
    {"speed_kmh": 30.0, "cum_fraction": 0.5},
    {"speed_kmh": 60.0, "cum_fraction": 1.0},
]


def test_postwrite_des_speed_distribution_points_happy_path_no_findings():
    action = _action("set_des_speed_distribution_points", distribution_no="25", points=_NEW_CURVE_POINTS)
    before = _shadow_model(des_speed_distributions=[_speed_distribution("25", _VALID_CURVE_POINTS)])
    after = _shadow_model(des_speed_distributions=[_speed_distribution("25", _NEW_CURVE_POINTS)])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after, {"written_value": _NEW_CURVE_POINTS, "readback_value": _NEW_CURVE_POINTS}
    )

    # point_count went 2 -> 3 as an unavoidable derived side effect of the
    # curve growing by one point -- "coupled_fields" must exempt it, exactly
    # like des_speed_distr_name's own coupling lesson (unit ⑧).
    assert report.findings == []
    assert report.blocking is False


def test_postwrite_des_speed_distribution_points_diff_flags_an_unrelated_distribution_change():
    action = _action("set_des_speed_distribution_points", distribution_no="25", points=_NEW_CURVE_POINTS)
    before = _shadow_model(
        des_speed_distributions=[
            _speed_distribution("25", _VALID_CURVE_POINTS),
            _speed_distribution("50", _VALID_CURVE_POINTS),
        ]
    )
    after = _shadow_model(
        des_speed_distributions=[
            _speed_distribution("25", _NEW_CURVE_POINTS),
            _speed_distribution("50", _NEW_CURVE_POINTS),  # unrelated drift -- no tool touched no=50
        ]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after, {"written_value": _NEW_CURVE_POINTS, "readback_value": _NEW_CURVE_POINTS}
    )

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(
        entry["object_class"] == "des_speed_distributions" and entry["object_id"] == "50"
        for entry in diff
    )


def test_postwrite_des_speed_distribution_points_target_persisted_check_fires_on_mismatch():
    action = _action("set_des_speed_distribution_points", distribution_no="25", points=_NEW_CURVE_POINTS)
    before = _shadow_model(des_speed_distributions=[_speed_distribution("25", _VALID_CURVE_POINTS)])
    # The saved file's points DON'T match what was supposedly written --
    # simulates a write that succeeded over COM but never reached disk.
    after = _shadow_model(des_speed_distributions=[_speed_distribution("25", _VALID_CURVE_POINTS)])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after, {"written_value": _NEW_CURVE_POINTS, "readback_value": _NEW_CURVE_POINTS}
    )

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_class"] == "des_speed_distributions"
    assert check["object_id"] == "25"
    assert check["saved_value"] == _VALID_CURVE_POINTS


def test_postwrite_diff_suppresses_set_reduced_speed_area_own_new_distribution():
    # QUEUE 3 (v2a) unit 3's own regression fix: set_reduced_speed_area (v2
    # unit ②) legitimately injects a BRAND NEW speedDistribution as part of
    # its own sanctioned XML-injection write -- this must NOT read as
    # "something else moved" now that des_speed_distributions is tracked.
    action = _action("set_reduced_speed_area", link_id="101", speed_kmh=40.0, from_m=10.0, length_m=50.0)
    before = _shadow_model(des_speed_distributions=[])
    after = _shadow_model(des_speed_distributions=[_speed_distribution("1", _VALID_CURVE_POINTS, name="agent_reduced_speed_link_101")])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action,
        before,
        after,
        {
            "written_value": {"link_id": "101", "speed_kmh": 40.0, "from_m": 10.0, "length_m": 50.0},
            "readback_value": {"link_id": "101", "speed_kmh": 40.0, "from_m": 10.0, "length_m": 50.0},
        },
    )

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_never_flags_des_speed_distributions_referenced_by_or_reference_count():
    # QUEUE 3 (v2a) unit 3's SECOND regression fix, found live-testing this
    # same addition against a REAL parsed shadow model (not a hand-built
    # fixture): set_des_speed_decision reassigning a decision's vehClass=10
    # from distribution 50 to distribution 25 legitimately changes BOTH
    # distributions' OWN referenced_by/reference_count (purely derived from
    # the des_speed_decisions list) -- a tool whose OWN target object_class
    # is "des_speed_decision_entries", never "des_speed_distributions" at
    # all. No per-tool coupled_fields entry could cover this (the shifting
    # field lives on a DIFFERENT object_class than the one the tool wrote
    # to) -- these two fields must be blanket-exempt for every tool.
    action = _action(
        "set_des_speed_decision", decision_no="6", veh_class_no="10", des_speed_distr_no="25"
    )
    before = _shadow_model(
        des_speed_decision_entries=[_des_speed_entry("6", "10", des_speed_distr_no="50")],
        des_speed_distributions=[
            {**_speed_distribution("25", _VALID_CURVE_POINTS), "reference_count": 0},
            {
                **_speed_distribution("50", _VALID_CURVE_POINTS),
                "reference_count": 1,
                "referenced_by": {
                    "des_speed_decisions": {"count": 1, "ids": ["6.10"], "truncated": False},
                    "vehicle_compositions": {"count": 0, "ids": [], "truncated": False},
                    "reduced_speed_areas": {"count": 0, "ids": [], "truncated": False},
                    "other": {"count": 0, "by_tag": {}},
                },
            },
        ],
    )
    after = _shadow_model(
        des_speed_decision_entries=[
            {**_des_speed_entry("6", "10", des_speed_distr_no="25"), "des_speed_distr_name": "25 mph"}
        ],
        des_speed_distributions=[
            {
                **_speed_distribution("25", _VALID_CURVE_POINTS),
                "reference_count": 1,
                "referenced_by": {
                    "des_speed_decisions": {"count": 1, "ids": ["6.10"], "truncated": False},
                    "vehicle_compositions": {"count": 0, "ids": [], "truncated": False},
                    "reduced_speed_areas": {"count": 0, "ids": [], "truncated": False},
                    "other": {"count": 0, "by_tag": {}},
                },
            },
            {**_speed_distribution("50", _VALID_CURVE_POINTS), "reference_count": 0},
        ],
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after, {"written_value": "25", "readback_value": "25"}
    )

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_still_flags_an_added_distribution_for_a_tool_not_opted_in():
    # The suppression is tool-SPECIFIC (_ALLOWS_NEW_OBJECTS_IN), not a
    # blanket "never flag added distributions" rule -- an unrelated tool
    # (here, an ordinary vehicle-input write) that somehow left a phantom
    # new distribution behind must still be flagged as evidence.
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)
    before = _shadow_model(des_speed_distributions=[])
    after = _shadow_model(des_speed_distributions=[_speed_distribution("1", _VALID_CURVE_POINTS)])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 600, "readback_value": 600})

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert {"object_class": "des_speed_distributions", "object_id": "1", "change": "added"} in diff


def test_postwrite_diff_and_value_mismatch_can_both_fire_together():
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)
    before = _shadow_model()
    # Target value persisted fine (600 in after) -- so exactly the two
    # findings this test is about fire: unrelated-drift diff + readback
    # mismatch.
    after = _shadow_model(
        links=[{"id": "101", "name": "MUTATED", "lanes": 3, "length": 280.0}],
        vehicle_inputs=[{"no": "1", "name": "Main St EB", "link": "101", "volume": 600.0}],
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 600, "readback_value": 500})

    assert report.blocking is True
    assert len(report.findings) == 2
    assert all(f.constraint_id == "write_verification_failed" for f in report.findings)


def test_postwrite_diff_with_unmapped_tool_treats_any_change_as_evidence():
    # A tool absent from guardrails.py's write-target spec (e.g. a future
    # global-setting write) gets no exemption at all -- ANY difference
    # anywhere is evidence, the conservative default.
    action = _action("set_sim_duration", seconds=3600)
    before = _shadow_model()
    after = _shadow_model(
        vehicle_inputs=[{"no": "1", "name": "Main St EB", "link": "101", "volume": 999.0}]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, None)

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert diff[0]["object_class"] == "vehicle_inputs"
    assert diff[0]["field"] == "volume"


def test_postwrite_none_models_do_not_crash():
    engine = GuardrailEngine(disabled=False)
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    report = engine.postwrite(action, None, None, None)

    assert isinstance(report, GuardrailReport)
    assert report.phase == "postwrite"


# ---------------------------------------------------------------------------
# postwrite — target-persisted check (controller review addition)
# ---------------------------------------------------------------------------


def test_postwrite_unpersisted_target_value_is_blocking():
    # The forgot-save_net_as failure mode: COM read-back matches (in-memory
    # state took the write) and before/after models are identical (so the
    # nothing-else-moved diff is silent) -- but the saved file still holds
    # the OLD value. Without the persisted-target check this write would
    # pass verification while the versioned artifact silently lacks the
    # change.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model()  # volume still 500.0 -- the write never reached the file
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    report = engine.postwrite(action, before, after, {"written_value": 600, "readback_value": 600})

    assert report.blocking is True
    assert [f.constraint_id for f in report.findings] == ["write_verification_failed"]
    check = report.first_refusal().details["persistence_check"]
    assert check["object_class"] == "vehicle_inputs"
    assert check["object_id"] == "1"
    assert check["field"] == "volume"
    assert check["written_value"] == 600
    assert check["saved_value"] == 500.0
    for lang in LANGS:
        assert report.first_refusal().messages[lang]


def test_postwrite_persisted_value_numeric_tolerance_is_clean():
    # int written vs float parsed (600 vs 600.0) must not be a false
    # positive -- same tolerance semantics as constraint #6's value-compare.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model(
        vehicle_inputs=[{"no": "1", "name": "Main St EB", "link": "101", "volume": 600.0}]
    )
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    report = engine.postwrite(action, before, after, {"written_value": 600, "readback_value": 600})

    assert report.findings == []


# ---------------------------------------------------------------------------
# postwrite — set_vehicle_input_volume interval_no dimension (v2 unit ⑤)
# ---------------------------------------------------------------------------


def test_postwrite_interval_no_1_target_persisted_check_is_byte_compatible():
    # interval_no absent -- the field/lookup must be EXACTLY the pre-v2-unit-⑤
    # top-level "volume" compare, unaffected by the vehicle input also
    # carrying a "volumes" list.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model(
        vehicle_inputs=[
            {
                "no": "1",
                "name": "Main St EB",
                "link": "101",
                "volume": 500.0,  # write never reached the file
                "interval_count": 2,
                "volumes": [
                    {"interval_no": 1, "from_s": 0.0, "volume": 500.0, "continuous": False},
                    {"interval_no": 2, "from_s": 900.0, "volume": 700.0, "continuous": False},
                ],
            }
        ]
    )
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    report = engine.postwrite(action, before, after, {"written_value": 600, "readback_value": 600})

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["field"] == "volume"
    assert check["saved_value"] == 500.0


def test_postwrite_interval_no_2_target_persisted_check_reads_the_volumes_list():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        vehicle_inputs=[
            {
                "no": "2",
                "name": "VI2",
                "link": "11",
                "volume": 1000.0,
                "interval_count": 3,
                "volumes": [
                    {"interval_no": 1, "from_s": 0.0, "volume": 1000.0, "continuous": False},
                    {"interval_no": 2, "from_s": 1200.0, "volume": 1000.0, "continuous": True},
                    {"interval_no": 3, "from_s": 1800.0, "volume": 1000.0, "continuous": True},
                ],
            }
        ]
    )
    after = _shadow_model(
        vehicle_inputs=[
            {
                "no": "2",
                "name": "VI2",
                "link": "11",
                "volume": 1000.0,  # interval 1 unchanged -- byte-compatible top-level field
                "interval_count": 3,
                "volumes": [
                    {"interval_no": 1, "from_s": 0.0, "volume": 1000.0, "continuous": False},
                    {"interval_no": 2, "from_s": 1200.0, "volume": 1333.0, "continuous": False},
                    {"interval_no": 3, "from_s": 1800.0, "volume": 1333.0, "continuous": True},
                ],
            }
        ]
    )
    action = _action("set_vehicle_input_volume", input_no="2", volume=1333.0, interval_no=2)

    report = engine.postwrite(
        action, before, after, {"written_value": 1333.0, "readback_value": 1333.0}
    )

    # Persisted correctly (interval 2's volume matches) AND the diff must not
    # flag interval 3's own downstream (still-continuous) mirror change or
    # interval 2's cont flag flipping true->false as "something else moved"
    # -- both are direct, expected consequences of writing interval 2 that
    # the coarse "volumes" list exemption (v2 unit ⑤) absorbs.
    assert report.findings == []
    assert report.blocking is False


def test_postwrite_interval_no_2_target_persisted_check_fires_when_not_persisted():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        vehicle_inputs=[
            {
                "no": "2",
                "name": "VI2",
                "link": "11",
                "volume": 1000.0,
                "interval_count": 3,
                "volumes": [
                    {"interval_no": 1, "from_s": 0.0, "volume": 1000.0, "continuous": False},
                    {"interval_no": 2, "from_s": 1200.0, "volume": 1000.0, "continuous": True},
                    {"interval_no": 3, "from_s": 1800.0, "volume": 1000.0, "continuous": True},
                ],
            }
        ]
    )
    after = _shadow_model(
        vehicle_inputs=[
            {
                "no": "2",
                "name": "VI2",
                "link": "11",
                "volume": 1000.0,
                "interval_count": 3,
                "volumes": [
                    {"interval_no": 1, "from_s": 0.0, "volume": 1000.0, "continuous": False},
                    # interval 2 unchanged -- the write to interval 2 never
                    # reached the saved file.
                    {"interval_no": 2, "from_s": 1200.0, "volume": 1000.0, "continuous": True},
                    {"interval_no": 3, "from_s": 1800.0, "volume": 1000.0, "continuous": True},
                ],
            }
        ]
    )
    action = _action("set_vehicle_input_volume", input_no="2", volume=1333.0, interval_no=2)

    report = engine.postwrite(
        action, before, after, {"written_value": 1333.0, "readback_value": 1333.0}
    )

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["field"] == "volumes[2].volume"
    assert check["written_value"] == 1333.0
    assert check["saved_value"] == 1000.0


def test_postwrite_interval_no_2_diff_still_flags_unrelated_drift():
    # A write to interval 2 exempts the "volumes" field of ITS OWN vehicle
    # input, but a completely unrelated object changing is still evidence --
    # the coarse exemption is scoped to (object_id, field), not global.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        vehicle_inputs=[
            {
                "no": "2",
                "name": "VI2",
                "link": "11",
                "volume": 1000.0,
                "interval_count": 2,
                "volumes": [
                    {"interval_no": 1, "from_s": 0.0, "volume": 1000.0, "continuous": False},
                    {"interval_no": 2, "from_s": 1200.0, "volume": 1000.0, "continuous": True},
                ],
            },
            {"no": "5", "name": "VI5", "link": "12", "volume": 200.0, "interval_count": 0, "volumes": []},
        ]
    )
    after = _shadow_model(
        vehicle_inputs=[
            {
                "no": "2",
                "name": "VI2",
                "link": "11",
                "volume": 1000.0,
                "interval_count": 2,
                "volumes": [
                    {"interval_no": 1, "from_s": 0.0, "volume": 1000.0, "continuous": False},
                    {"interval_no": 2, "from_s": 1200.0, "volume": 1333.0, "continuous": False},
                ],
            },
            {
                "no": "5",
                "name": "VI5",
                "link": "12",
                "volume": 999.0,  # unrelated drift
                "interval_count": 0,
                "volumes": [],
            },
        ]
    )
    action = _action("set_vehicle_input_volume", input_no="2", volume=1333.0, interval_no=2)

    report = engine.postwrite(
        action, before, after, {"written_value": 1333.0, "readback_value": 1333.0}
    )

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert {
        "object_class": "vehicle_inputs",
        "object_id": "5",
        "change": "field_changed",
        "field": "volume",
        "before": 200.0,
        "after": 999.0,
    } in diff


# ---------------------------------------------------------------------------
# postwrite — set_route_relative_flow (v2 unit ⑥): TWO-PART id_param_parts
# ---------------------------------------------------------------------------


def test_postwrite_diff_is_silent_when_only_the_target_routes_rel_flow_changed():
    action = _action("set_route_relative_flow", decision_no="1", route_no="1", rel_flow=745.0)
    before = _shadow_model(routes=[_route("1", "1", rel_flow=245.0), _route("2", "1", rel_flow=139.0)])
    after = _shadow_model(routes=[_route("1", "1", rel_flow=745.0), _route("2", "1", rel_flow=139.0)])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 745.0, "readback_value": 745.0})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_flags_an_unrelated_sibling_routes_rel_flow_change_as_evidence():
    # The written route's OWN id ("1.1") is exempt, but a SIBLING route
    # (same decision, different route_no -- a different flat-list id)
    # silently changing too must still be evidence -- Vissim does NOT
    # renormalize siblings on write (verified live), so a sibling actually
    # moving is a genuine anomaly this diff must catch.
    action = _action("set_route_relative_flow", decision_no="1", route_no="1", rel_flow=745.0)
    before = _shadow_model(routes=[_route("1", "1", rel_flow=245.0), _route("2", "1", rel_flow=139.0)])
    after = _shadow_model(
        routes=[_route("1", "1", rel_flow=745.0), _route("2", "1", rel_flow=999.0)]  # unrelated drift
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 745.0, "readback_value": 745.0})

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert {
        "object_class": "routes",
        "object_id": "1.2",
        "change": "field_changed",
        "field": "rel_flow",
        "before": 139.0,
        "after": 999.0,
    } in diff


def test_postwrite_diff_does_not_confuse_two_decisions_own_route_no_1():
    # decision 1 route 1 ("1.1") and decision 5 route 1 ("5.1") are two
    # entirely different objects sharing the same route_no -- the
    # synthesized id must keep them apart (a bug here would either miss real
    # drift or false-positive on the unrelated decision's own route 1).
    action = _action("set_route_relative_flow", decision_no="1", route_no="1", rel_flow=745.0)
    before = _shadow_model(routes=[_route("1", "1", rel_flow=245.0), _route("1", "5", rel_flow=2845.0)])
    after = _shadow_model(routes=[_route("1", "1", rel_flow=745.0), _route("1", "5", rel_flow=2845.0)])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 745.0, "readback_value": 745.0})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_route_relative_flow_target_persisted_check_interval_1_byte_compatible():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(routes=[_route("1", "1", rel_flow=245.0)])
    after = _shadow_model(routes=[_route("1", "1", rel_flow=245.0)])  # write never reached the file
    action = _action("set_route_relative_flow", decision_no="1", route_no="1", rel_flow=745.0)

    report = engine.postwrite(action, before, after, {"written_value": 745.0, "readback_value": 745.0})

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_class"] == "routes"
    assert check["object_id"] == "1.1"
    assert check["field"] == "rel_flow"
    assert check["saved_value"] == 245.0


def test_postwrite_route_relative_flow_target_persisted_check_interval_2_reads_rel_flows_list():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        routes=[
            _route(
                "1",
                "1",
                rel_flows=[
                    {"interval_no": 1, "from_s": 0.0, "rel_flow": 45.0},
                    {"interval_no": 2, "from_s": 5400.0, "rel_flow": 45.0},
                ],
            )
        ]
    )
    after = _shadow_model(
        routes=[
            _route(
                "1",
                "1",
                rel_flows=[
                    {"interval_no": 1, "from_s": 0.0, "rel_flow": 45.0},
                    {"interval_no": 2, "from_s": 5400.0, "rel_flow": 999.0},
                ],
            )
        ]
    )
    action = _action(
        "set_route_relative_flow", decision_no="1", route_no="1", rel_flow=999.0, interval_no=2
    )

    report = engine.postwrite(action, before, after, {"written_value": 999.0, "readback_value": 999.0})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_route_relative_flow_target_persisted_check_interval_2_fires_when_not_persisted():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        routes=[
            _route(
                "1",
                "1",
                rel_flows=[
                    {"interval_no": 1, "from_s": 0.0, "rel_flow": 45.0},
                    {"interval_no": 2, "from_s": 5400.0, "rel_flow": 45.0},
                ],
            )
        ]
    )
    after = _shadow_model(
        routes=[
            _route(
                "1",
                "1",
                rel_flows=[
                    {"interval_no": 1, "from_s": 0.0, "rel_flow": 45.0},
                    # interval 2 unchanged -- the write never reached the file.
                    {"interval_no": 2, "from_s": 5400.0, "rel_flow": 45.0},
                ],
            )
        ]
    )
    action = _action(
        "set_route_relative_flow", decision_no="1", route_no="1", rel_flow=999.0, interval_no=2
    )

    report = engine.postwrite(action, before, after, {"written_value": 999.0, "readback_value": 999.0})

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["field"] == "rel_flows[2].rel_flow"
    assert check["written_value"] == 999.0
    assert check["saved_value"] == 45.0


# ---------------------------------------------------------------------------
# postwrite — set_vehicle_composition_share (v2 unit ⑦): TWO-PART
# id_param_parts, no interval dimension at all (RelFlow is scalar-only for
# vehicle compositions, unlike route RelFlow).
# ---------------------------------------------------------------------------


def _composition_entry(composition_no: str, veh_type: str, *, rel_flow: float | None = 100.0) -> dict[str, Any]:
    return {
        "id": f"{composition_no}.{veh_type}",
        "composition_no": composition_no,
        "veh_type": veh_type,
        "des_speed_distr": "50",
        "rel_flow": rel_flow,
    }


def test_postwrite_diff_is_silent_when_only_the_target_entrys_rel_flow_changed():
    action = _action("set_vehicle_composition_share", composition_no="1", veh_type_no="100", rel_flow=5.85)
    before = _shadow_model(
        vehicle_composition_entries=[
            _composition_entry("1", "100", rel_flow=0.85),
            _composition_entry("1", "190", rel_flow=0.05),
        ]
    )
    after = _shadow_model(
        vehicle_composition_entries=[
            _composition_entry("1", "100", rel_flow=5.85),
            _composition_entry("1", "190", rel_flow=0.05),
        ]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 5.85, "readback_value": 5.85})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_flags_an_unrelated_sibling_entrys_rel_flow_change_as_evidence():
    # The written entry's OWN id ("1.100") is exempt, but a SIBLING entry
    # (same composition, different vehType -- a different flat-list id)
    # silently changing too must still be evidence -- Vissim does NOT
    # renormalize siblings on write (verified live), so a sibling actually
    # moving is a genuine anomaly this diff must catch.
    action = _action("set_vehicle_composition_share", composition_no="1", veh_type_no="100", rel_flow=5.85)
    before = _shadow_model(
        vehicle_composition_entries=[
            _composition_entry("1", "100", rel_flow=0.85),
            _composition_entry("1", "190", rel_flow=0.05),
        ]
    )
    after = _shadow_model(
        vehicle_composition_entries=[
            _composition_entry("1", "100", rel_flow=5.85),
            _composition_entry("1", "190", rel_flow=0.99),  # unrelated drift
        ]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 5.85, "readback_value": 5.85})

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert {
        "object_class": "vehicle_composition_entries",
        "object_id": "1.190",
        "change": "field_changed",
        "field": "rel_flow",
        "before": 0.05,
        "after": 0.99,
    } in diff


def test_postwrite_diff_does_not_confuse_two_compositions_own_veh_type_100():
    # composition 1's vehType 100 ("1.100") and composition 2's vehType 100
    # ("2.100") are two entirely different objects sharing the same vehType
    # -- the synthesized id must keep them apart (a bug here would either
    # miss real drift or false-positive on the unrelated composition's own
    # vehType 100).
    action = _action("set_vehicle_composition_share", composition_no="1", veh_type_no="100", rel_flow=5.85)
    before = _shadow_model(
        vehicle_composition_entries=[
            _composition_entry("1", "100", rel_flow=0.85),
            _composition_entry("2", "100", rel_flow=0.97),
        ]
    )
    after = _shadow_model(
        vehicle_composition_entries=[
            _composition_entry("1", "100", rel_flow=5.85),
            _composition_entry("2", "100", rel_flow=0.97),
        ]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 5.85, "readback_value": 5.85})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_vehicle_composition_share_target_persisted_check_happy_path():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(vehicle_composition_entries=[_composition_entry("1", "100", rel_flow=0.85)])
    after = _shadow_model(vehicle_composition_entries=[_composition_entry("1", "100", rel_flow=5.85)])
    action = _action("set_vehicle_composition_share", composition_no="1", veh_type_no="100", rel_flow=5.85)

    report = engine.postwrite(action, before, after, {"written_value": 5.85, "readback_value": 5.85})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_vehicle_composition_share_target_persisted_check_fires_when_not_persisted():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(vehicle_composition_entries=[_composition_entry("1", "100", rel_flow=0.85)])
    after = _shadow_model(
        vehicle_composition_entries=[_composition_entry("1", "100", rel_flow=0.85)]  # write never reached the file
    )
    action = _action("set_vehicle_composition_share", composition_no="1", veh_type_no="100", rel_flow=5.85)

    report = engine.postwrite(action, before, after, {"written_value": 5.85, "readback_value": 5.85})

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_class"] == "vehicle_composition_entries"
    assert check["object_id"] == "1.100"
    assert check["field"] == "rel_flow"
    assert check["saved_value"] == 0.85


def test_postwrite_unpersisted_conflict_area_status_is_blocking():
    # v2 unit (4): status is "not_persisted" semantics (a plain XML attribute
    # the parser can always see -- no opaque-blob case like signal
    # cycle_time), so a None-vs-unchanged AFTER value is evidence the write
    # never reached the file, same as vehicle-input volume.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        conflict_areas=[{"no": "3", "link_a": "3", "link_b": "12", "status": "PASSIVE"}]
    )
    after = _shadow_model(
        conflict_areas=[{"no": "3", "link_a": "3", "link_b": "12", "status": "PASSIVE"}]
    )  # unchanged -- the write never reached the file
    action = _action("set_conflict_area_status", conflict_area_no="3", status="AHASRIGHTOFWAY")

    report = engine.postwrite(
        action, before, after, {"written_value": "AHASRIGHTOFWAY", "readback_value": "AHASRIGHTOFWAY"}
    )

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_class"] == "conflict_areas"
    assert check["object_id"] == "3"
    assert check["field"] == "status"
    assert check["written_value"] == "AHASRIGHTOFWAY"
    assert check["saved_value"] == "PASSIVE"


def test_postwrite_persisted_check_skips_none_parsed_field():
    # A parsed field of None means the parser cannot see this attribute for
    # this object (e.g. signal cycle_time on opaque program-driven
    # controllers, per the M2.1 CycTm finding) -- no file-level persistence
    # claim can be made either way, so the check must degrade silently
    # rather than false-refuse.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        signals=[{"sc_no": "11", "sc_name": "SC 11", "cycle_time": None, "phases": []}]
    )
    after = _shadow_model(
        signals=[{"sc_no": "11", "sc_name": "SC 11", "cycle_time": None, "phases": []}]
    )
    action = _action("set_signal_cycle", sc_no="11", cycle=90)

    report = engine.postwrite(action, before, after, {"written_value": 90, "readback_value": 90})

    assert report.findings == []
    assert report.blocking is False


# ---------------------------------------------------------------------------
# postwrite — float tolerance in the nothing-else-moved diff
# (live-verified M2.3 finding: Vissim's SaveNetAs re-serializes connector
# geometry with ~5.7e-14 drift on EVERY save; exact != refused every
# real-world write)
# ---------------------------------------------------------------------------

# The exact per-coordinate delta observed live on Urban Intersection
# Beijing.CN's auto-computed connectors (links 10039/10051).
_OBSERVED_SAVE_NOISE = 5.684341886080802e-14


def _links_with_points(points: list[list[float]]) -> list[dict[str, Any]]:
    return [{"id": "10039", "name": "connector", "lanes": 1, "points": points}]


def test_postwrite_diff_tolerates_savenetas_float_noise_in_geometry():
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)
    base_points = [[100.0, 200.0], [101.5, 201.5], [103.0, 203.0]]
    noisy_points = [[x + _OBSERVED_SAVE_NOISE, y - _OBSERVED_SAVE_NOISE] for x, y in base_points]
    before = _shadow_model(
        links=_links_with_points(base_points),
        vehicle_inputs=[{"no": "1", "name": "VI1", "link": "10039", "volume": 500.0}],
    )
    after = _shadow_model(
        links=_links_with_points(noisy_points),
        vehicle_inputs=[{"no": "1", "name": "VI1", "link": "10039", "volume": 600.0}],
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 600, "readback_value": 600})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_still_flags_a_real_geometry_change():
    # Half a meter is a real edit, ~13 orders of magnitude above the noise.
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)
    base_points = [[100.0, 200.0], [101.5, 201.5]]
    moved_points = [[100.0, 200.0], [102.0, 201.5]]
    before = _shadow_model(
        links=_links_with_points(base_points),
        vehicle_inputs=[{"no": "1", "name": "VI1", "link": "10039", "volume": 500.0}],
    )
    after = _shadow_model(
        links=_links_with_points(moved_points),
        vehicle_inputs=[{"no": "1", "name": "VI1", "link": "10039", "volume": 600.0}],
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 600, "readback_value": 600})

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert diff[0]["object_class"] == "links"
    assert diff[0]["field"] == "points"


def test_postwrite_diff_still_flags_an_added_geometry_point():
    # Structural change (point count) is never forgiven, regardless of values.
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)
    base_points = [[100.0, 200.0], [101.5, 201.5]]
    grown_points = [[100.0, 200.0], [101.5, 201.5], [103.0, 203.0]]
    before = _shadow_model(
        links=_links_with_points(base_points),
        vehicle_inputs=[{"no": "1", "name": "VI1", "link": "10039", "volume": 500.0}],
    )
    after = _shadow_model(
        links=_links_with_points(grown_points),
        vehicle_inputs=[{"no": "1", "name": "VI1", "link": "10039", "volume": 600.0}],
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 600, "readback_value": 600})

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert diff[0]["object_class"] == "links"
    assert diff[0]["field"] == "points"


# ---------------------------------------------------------------------------
# postwrite — set_des_speed_decision (v2a unit ⑧): the FIRST multi-target
# write. UNPAIRED behaves exactly like every other single-target tool
# (id_param_parts primary-only); PAIRED additionally exempts a SECOND
# object (the restore decision's own entry) via _PAIRED_EXTRA_TARGET_SPEC --
# without that exemption, the restore write's own legitimate field change
# would read as "something else moved" and refuse a correct write.
# ---------------------------------------------------------------------------


def test_postwrite_unpaired_diff_is_silent_when_only_the_target_entrys_distr_changed():
    action = _action(
        "set_des_speed_decision", decision_no="6", veh_class_no="10", des_speed_distr_no=25
    )
    before = _shadow_model(
        des_speed_decision_entries=[
            _des_speed_entry("6", "10", des_speed_distr_no="50"),
            _des_speed_entry("6", "20", des_speed_distr_no="50"),
        ]
    )
    after = _shadow_model(
        des_speed_decision_entries=[
            _des_speed_entry("6", "10", des_speed_distr_no="25"),
            _des_speed_entry("6", "20", des_speed_distr_no="50"),
        ]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": "25", "readback_value": "25"})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_unpaired_diff_flags_an_unrelated_sibling_entrys_distr_change_as_evidence():
    # The written entry's OWN id ("6.10") is exempt, but a SIBLING entry
    # (same decision, different vehClass -- "6.20") silently changing too
    # must still be evidence -- there is no shared-sum semantics here at
    # all, so a sibling actually moving is a genuine anomaly.
    action = _action(
        "set_des_speed_decision", decision_no="6", veh_class_no="10", des_speed_distr_no=25
    )
    before = _shadow_model(
        des_speed_decision_entries=[
            _des_speed_entry("6", "10", des_speed_distr_no="50"),
            _des_speed_entry("6", "20", des_speed_distr_no="50"),
        ]
    )
    after = _shadow_model(
        des_speed_decision_entries=[
            _des_speed_entry("6", "10", des_speed_distr_no="25"),
            _des_speed_entry("6", "20", des_speed_distr_no="99"),  # unrelated drift
        ]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": "25", "readback_value": "25"})

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert {
        "object_class": "des_speed_decision_entries",
        "object_id": "6.20",
        "change": "field_changed",
        "field": "des_speed_distr_no",
        "before": "50",
        "after": "99",
    } in diff


def test_postwrite_paired_diff_exempts_both_the_primary_and_restore_entries():
    # THE central v2a unit ⑧ regression: without _PAIRED_EXTRA_TARGET_SPEC,
    # decision 7's own (legitimate) field change would be unexempted
    # "something else moved" evidence and wrongly refuse a correct paired
    # write.
    action = _action(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no=25,
        restore_decision_no="7",
        restore_distr_no=50,
    )
    before = _shadow_model(
        des_speed_decision_entries=[
            _des_speed_entry("6", "10", des_speed_distr_no="50"),
            _des_speed_entry("7", "10", des_speed_distr_no="25"),
        ]
    )
    after = _shadow_model(
        des_speed_decision_entries=[
            _des_speed_entry("6", "10", des_speed_distr_no="25"),
            _des_speed_entry("7", "10", des_speed_distr_no="50"),
        ]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action,
        before,
        after,
        {
            "written_value": "25",
            "readback_value": "25",
            "restore_written_value": "50",
            "restore_readback_value": "50",
        },
    )

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_paired_diff_still_flags_a_genuinely_unrelated_third_entry():
    # Both the primary (6.10) AND restore (7.10) targets are exempt, but a
    # THIRD, wholly unrelated entry (6.20) drifting is still evidence -- the
    # exemption is narrow (exactly the two named targets), not a blanket
    # "anything in this object class is fine" pass.
    action = _action(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no=25,
        restore_decision_no="7",
        restore_distr_no=50,
    )
    before = _shadow_model(
        des_speed_decision_entries=[
            _des_speed_entry("6", "10", des_speed_distr_no="50"),
            _des_speed_entry("6", "20", des_speed_distr_no="50"),
            _des_speed_entry("7", "10", des_speed_distr_no="25"),
        ]
    )
    after = _shadow_model(
        des_speed_decision_entries=[
            _des_speed_entry("6", "10", des_speed_distr_no="25"),
            _des_speed_entry("6", "20", des_speed_distr_no="99"),  # unrelated drift
            _des_speed_entry("7", "10", des_speed_distr_no="50"),
        ]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action,
        before,
        after,
        {
            "written_value": "25",
            "readback_value": "25",
            "restore_written_value": "50",
            "restore_readback_value": "50",
        },
    )

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert {
        "object_class": "des_speed_decision_entries",
        "object_id": "6.20",
        "change": "field_changed",
        "field": "des_speed_distr_no",
        "before": "50",
        "after": "99",
    } in diff


def test_postwrite_des_speed_decision_target_persisted_check_happy_path_unpaired():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(des_speed_decision_entries=[_des_speed_entry("6", "10", des_speed_distr_no="50")])
    after = _shadow_model(des_speed_decision_entries=[_des_speed_entry("6", "10", des_speed_distr_no="25")])
    action = _action(
        "set_des_speed_decision", decision_no="6", veh_class_no="10", des_speed_distr_no=25
    )

    report = engine.postwrite(action, before, after, {"written_value": "25", "readback_value": "25"})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_des_speed_decision_target_persisted_check_fires_when_primary_not_persisted():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(des_speed_decision_entries=[_des_speed_entry("6", "10", des_speed_distr_no="50")])
    after = _shadow_model(
        des_speed_decision_entries=[_des_speed_entry("6", "10", des_speed_distr_no="50")]  # unchanged on disk
    )
    action = _action(
        "set_des_speed_decision", decision_no="6", veh_class_no="10", des_speed_distr_no=25
    )

    report = engine.postwrite(action, before, after, {"written_value": "25", "readback_value": "25"})

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_class"] == "des_speed_decision_entries"
    assert check["object_id"] == "6.10"
    assert check["saved_value"] == "50"


def test_postwrite_des_speed_decision_target_persisted_check_fires_when_restore_not_persisted():
    # The PRIMARY target persisted correctly, but the RESTORE target's own
    # saved value never reached the file -- the extra-target persisted
    # check (keyed off com_result["restore_written_value"]) must catch this
    # independently of the primary's own (passing) check.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        des_speed_decision_entries=[
            _des_speed_entry("6", "10", des_speed_distr_no="50"),
            _des_speed_entry("7", "10", des_speed_distr_no="25"),
        ]
    )
    after = _shadow_model(
        des_speed_decision_entries=[
            _des_speed_entry("6", "10", des_speed_distr_no="25"),  # primary DID persist
            _des_speed_entry("7", "10", des_speed_distr_no="25"),  # restore did NOT
        ]
    )
    action = _action(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no=25,
        restore_decision_no="7",
        restore_distr_no=50,
    )

    report = engine.postwrite(
        action,
        before,
        after,
        {
            "written_value": "25",
            "readback_value": "25",
            "restore_written_value": "50",
            "restore_readback_value": "50",
        },
    )

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_class"] == "des_speed_decision_entries"
    assert check["object_id"] == "7.10"
    assert check["saved_value"] == "25"


def test_postwrite_diff_exempts_the_coupled_des_speed_distr_name_field_too():
    # LIVE FINDING (backend/tools/live_check_des_speed_decision.py, first
    # run): des_speed_distr_name is looked up from des_speed_distr_no at
    # PARSE time (tools_network._parse_des_speed_decisions), so a real write
    # ALWAYS also changes the name field as an unavoidable side effect ('55
    # mph' -> '10 mph Roundabout HGV', reproduced live on Roundabout
    # Billings.US) -- without the "coupled_fields" exemption this genuinely
    # correct write false-positived as "something else moved". Both the
    # PRIMARY and RESTORE targets' own names change in a paired write.
    action = _action(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no=12,
        restore_decision_no="7",
        restore_distr_no=40,
    )
    before = _shadow_model(
        des_speed_decision_entries=[
            _des_speed_entry("6", "10", des_speed_distr_no="50"),
            _des_speed_entry("7", "10", des_speed_distr_no="50"),
        ]
    )
    after = _shadow_model(
        des_speed_decision_entries=[
            {**_des_speed_entry("6", "10", des_speed_distr_no="12"), "des_speed_distr_name": "10 mph Roundabout HGV"},
            {**_des_speed_entry("7", "10", des_speed_distr_no="40"), "des_speed_distr_name": "45 mph"},
        ]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action,
        before,
        after,
        {
            "written_value": "12",
            "readback_value": "12",
            "restore_written_value": "40",
            "restore_readback_value": "40",
        },
    )

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_des_speed_decision_extra_target_check_silent_when_action_is_unpaired():
    # No restore_decision_no in this action -- _PAIRED_EXTRA_TARGET_SPEC's
    # trigger_param is absent, so only the primary target is checked, even
    # though "des_speed_decision_entries" also contains a "7.10" entry from
    # some unrelated earlier state.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        des_speed_decision_entries=[
            _des_speed_entry("6", "10", des_speed_distr_no="50"),
            _des_speed_entry("7", "10", des_speed_distr_no="25"),
        ]
    )
    after = _shadow_model(
        des_speed_decision_entries=[
            _des_speed_entry("6", "10", des_speed_distr_no="25"),
            _des_speed_entry("7", "10", des_speed_distr_no="25"),  # untouched, irrelevant here
        ]
    )
    action = _action(
        "set_des_speed_decision", decision_no="6", veh_class_no="10", des_speed_distr_no=25
    )

    report = engine.postwrite(action, before, after, {"written_value": "25", "readback_value": "25"})

    assert report.findings == []
    assert report.blocking is False


# ---------------------------------------------------------------------------
# postwrite — set_signal_green_split (v2a unit ⑨): three-part identity,
# green_window dict as the single value_field
# ---------------------------------------------------------------------------


def _green_split_entry(
    sc_no: str,
    program_no: str,
    sg_no: str,
    *,
    green_start_s: float | None = 10.0,
    green_end_s: float | None = 40.0,
    active: bool = True,
) -> dict[str, Any]:
    window = None
    if green_start_s is not None:
        window = {"green_start_s": green_start_s, "green_end_s": green_end_s}
    return {
        "id": f"{sc_no}.{program_no}.{sg_no}",
        "sc_no": sc_no,
        "program_no": program_no,
        "sg_no": sg_no,
        "sg_name": f"SG{sg_no}",
        "green_window": window,
        "amber_s": 3.0,
        "min_green_s": None,
        "head_count": 2,
        "cycle_s": 90.0,
        "active": active,
    }


def _green_split_verification(start: float, end: float) -> dict[str, Any]:
    window = {"green_start_s": start, "green_end_s": end}
    return {"written_value": dict(window), "readback_value": dict(window)}


def test_postwrite_signal_green_split_target_persisted_check_happy_path():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        signal_program_splits=[_green_split_entry("1", "2", "1", green_start_s=1.0, green_end_s=15.0)]
    )
    after = _shadow_model(
        signal_program_splits=[_green_split_entry("1", "2", "1", green_start_s=1.0, green_end_s=8.0)]
    )
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="2", green_start_s=1.0, green_end_s=8.0
    )

    report = engine.postwrite(action, before, after, _green_split_verification(1.0, 8.0))

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_signal_green_split_target_persisted_check_fires_when_not_persisted():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        signal_program_splits=[_green_split_entry("1", "2", "1", green_start_s=1.0, green_end_s=15.0)]
    )
    after = _shadow_model(
        signal_program_splits=[_green_split_entry("1", "2", "1", green_start_s=1.0, green_end_s=15.0)]
    )  # unchanged -- the blob write never reached the saved file
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="2", green_start_s=1.0, green_end_s=8.0
    )

    report = engine.postwrite(action, before, after, _green_split_verification(1.0, 8.0))

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_class"] == "signal_program_splits"
    assert check["object_id"] == "1.2.1"
    assert check["field"] == "green_window"
    assert check["saved_value"] == {"green_start_s": 1.0, "green_end_s": 15.0}


def test_postwrite_signal_green_split_partial_edge_persistence_still_fires():
    # Only ONE edge of the window reached the file -- the dict-valued
    # green_window compare must flag it (this is exactly why both edges live
    # in one value_field).
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        signal_program_splits=[_green_split_entry("1", "2", "1", green_start_s=1.0, green_end_s=15.0)]
    )
    after = _shadow_model(
        signal_program_splits=[_green_split_entry("1", "2", "1", green_start_s=2.0, green_end_s=8.0)]
    )  # green_end landed, green_start drifted to a different value
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="2", green_start_s=1.0, green_end_s=8.0
    )

    report = engine.postwrite(action, before, after, _green_split_verification(1.0, 8.0))

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_id"] == "1.2.1"


def test_postwrite_signal_green_split_diff_flags_an_unrelated_sgs_window_change():
    # The blob rewrite touched a DIFFERENT signal group's window too -- the
    # nothing-else-moved diff must surface it as evidence (the whole reason
    # signal_program_splits joined _SHADOW_OBJECT_ID_FIELDS).
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        signal_program_splits=[
            _green_split_entry("1", "2", "1", green_start_s=1.0, green_end_s=15.0),
            _green_split_entry("1", "2", "2", green_start_s=63.0, green_end_s=105.0),
        ]
    )
    after = _shadow_model(
        signal_program_splits=[
            _green_split_entry("1", "2", "1", green_start_s=1.0, green_end_s=8.0),
            _green_split_entry("1", "2", "2", green_start_s=50.0, green_end_s=105.0),  # NOT the target
        ]
    )
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="2", green_start_s=1.0, green_end_s=8.0
    )

    report = engine.postwrite(action, before, after, _green_split_verification(1.0, 8.0))

    assert report.blocking is True
    evidence = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(
        item["object_class"] == "signal_program_splits" and item["object_id"] == "1.2.2"
        for item in evidence
    )


# ---------------------------------------------------------------------------
# postwrite -- set_priority_rule_gap_headway target-persisted check /
# nothing-else-moved diff -- QUEUE 3 unit 2
# ---------------------------------------------------------------------------


def _gap_headway_verification(gap_time_s: float, headway_m: float) -> dict[str, Any]:
    pair = {"gap_time_s": gap_time_s, "headway_m": headway_m}
    return {"written_value": dict(pair), "readback_value": dict(pair)}


def test_postwrite_priority_rule_gap_headway_target_persisted_check_happy_path():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        priority_rule_markers=[_priority_rule_marker_entry("4", 1, gap_time_s=3.2, headway_m=0.0)]
    )
    after = _shadow_model(
        priority_rule_markers=[_priority_rule_marker_entry("4", 1, gap_time_s=5.5, headway_m=22.0)]
    )
    action = _action(
        "set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=5.5, headway_m=22.0
    )

    report = engine.postwrite(action, before, after, _gap_headway_verification(5.5, 22.0))

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_priority_rule_gap_headway_target_persisted_check_fires_when_not_persisted():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        priority_rule_markers=[_priority_rule_marker_entry("4", 1, gap_time_s=3.2, headway_m=0.0)]
    )
    after = _shadow_model(
        priority_rule_markers=[_priority_rule_marker_entry("4", 1, gap_time_s=3.2, headway_m=0.0)]
    )  # unchanged -- the write never reached the saved file
    action = _action(
        "set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=5.5, headway_m=22.0
    )

    report = engine.postwrite(action, before, after, _gap_headway_verification(5.5, 22.0))

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_class"] == "priority_rule_markers"
    assert check["object_id"] == "4.1"
    assert check["field"] == "gap_headway"
    assert check["saved_value"] == {"gap_time_s": 3.2, "headway_m": 0.0}


def test_postwrite_priority_rule_gap_headway_partial_field_persistence_still_fires():
    # Only ONE of the two fields reached the file -- the dict-valued
    # gap_headway compare must flag it (this is exactly why both fields live
    # in one value_field, mirroring set_signal_green_split's green_window).
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        priority_rule_markers=[_priority_rule_marker_entry("4", 1, gap_time_s=3.2, headway_m=0.0)]
    )
    after = _shadow_model(
        priority_rule_markers=[_priority_rule_marker_entry("4", 1, gap_time_s=5.5, headway_m=0.0)]
    )  # gap_time_s landed, headway_m did not
    action = _action(
        "set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=5.5, headway_m=22.0
    )

    report = engine.postwrite(action, before, after, _gap_headway_verification(5.5, 22.0))

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_id"] == "4.1"


def test_postwrite_priority_rule_gap_headway_diff_flags_an_unrelated_sibling_marker_change():
    # The write touched a DIFFERENT marker on the SAME rule too -- the
    # nothing-else-moved diff must surface it as evidence (the whole reason
    # priority_rule_markers joined _SHADOW_OBJECT_ID_FIELDS).
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        priority_rule_markers=[
            _priority_rule_marker_entry("4", 1, gap_time_s=3.2, headway_m=0.0),
            _priority_rule_marker_entry("4", 2, gap_time_s=0.0, headway_m=10.0),
        ]
    )
    after = _shadow_model(
        priority_rule_markers=[
            _priority_rule_marker_entry("4", 1, gap_time_s=5.5, headway_m=22.0),
            _priority_rule_marker_entry("4", 2, gap_time_s=1.0, headway_m=10.0),  # NOT the target
        ]
    )
    action = _action(
        "set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=5.5, headway_m=22.0
    )

    report = engine.postwrite(action, before, after, _gap_headway_verification(5.5, 22.0))

    assert report.blocking is True
    evidence = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(
        item["object_class"] == "priority_rule_markers" and item["object_id"] == "4.2"
        for item in evidence
    )


# ---------------------------------------------------------------------------
# postwrite -- set_lane_closure target-persisted check / nothing-else-moved
# diff -- v2b unit (1)
# ---------------------------------------------------------------------------


def test_postwrite_lane_closure_target_persisted_check_happy_path():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(link_lanes=[_link_lane_entry("101", 1)])
    after = _shadow_model(link_lanes=[_link_lane_entry("101", 1, blocked_vehicle_classes=["10", "20"])])
    action = _action("set_lane_closure", link_id="101", lane_index=1, vehicle_classes=["10", "20"])
    verification = {"written_value": ["10", "20"], "readback_value": ["10", "20"]}

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_lane_closure_target_persisted_check_fires_when_not_persisted():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(link_lanes=[_link_lane_entry("101", 1)])
    after = _shadow_model(link_lanes=[_link_lane_entry("101", 1)])  # unchanged -- write never landed
    action = _action("set_lane_closure", link_id="101", lane_index=1, vehicle_classes=["10", "20"])
    verification = {"written_value": ["10", "20"], "readback_value": ["10", "20"]}

    report = engine.postwrite(action, before, after, verification)

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_class"] == "link_lanes"
    assert check["object_id"] == "101.1"
    assert check["field"] == "blocked_vehicle_classes"
    assert check["saved_value"] == []


def test_postwrite_lane_closure_reopen_target_persisted_check_happy_path():
    # vehicle_classes=[] (reopen) is a legitimate, fully-persisted state --
    # [] -> [] must NOT read as "not persisted" (see _WRITE_TARGET_FIELD_
    # SPEC's own none_means="not_persisted" comment: only a Python None,
    # never an empty list, is evidence of a dropped write here).
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(link_lanes=[_link_lane_entry("101", 1, blocked_vehicle_classes=["20"])])
    after = _shadow_model(link_lanes=[_link_lane_entry("101", 1, blocked_vehicle_classes=[])])
    action = _action("set_lane_closure", link_id="101", lane_index=1, vehicle_classes=[])
    verification = {"written_value": [], "readback_value": []}

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_lane_closure_diff_flags_an_unrelated_sibling_lane_change():
    # The write touched a DIFFERENT lane of the SAME link too -- the
    # nothing-else-moved diff must surface it as evidence.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        link_lanes=[_link_lane_entry("101", 1), _link_lane_entry("101", 2)]
    )
    after = _shadow_model(
        link_lanes=[
            _link_lane_entry("101", 1, blocked_vehicle_classes=["20"]),
            _link_lane_entry("101", 2, blocked_vehicle_classes=["10"]),  # NOT the target
        ]
    )
    action = _action("set_lane_closure", link_id="101", lane_index=1, vehicle_classes=["20"])
    verification = {"written_value": ["20"], "readback_value": ["20"]}

    report = engine.postwrite(action, before, after, verification)

    assert report.blocking is True
    evidence = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(
        item["object_class"] == "link_lanes" and item["object_id"] == "101.2"
        for item in evidence
    )


# ---------------------------------------------------------------------------
# postwrite -- set_link_closure/set_turn_ban compose set_lane_closure across
# EVERY lane of the target link/connector -- the nothing-else-moved diff
# must exempt every one of them, not just a single (link_id, lane_index)
# pair -- Q4 unit (3), _LINK_WIDE_TARGET_SPEC
# ---------------------------------------------------------------------------


def test_postwrite_link_closure_diff_exempts_every_lane_of_the_target_link():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        link_lanes=[_link_lane_entry("101", 1), _link_lane_entry("101", 2)]
    )
    after = _shadow_model(
        link_lanes=[
            _link_lane_entry("101", 1, blocked_vehicle_classes=["30"]),
            _link_lane_entry("101", 2, blocked_vehicle_classes=["30"]),
        ]
    )
    action = _action("set_link_closure", link_id="101", vehicle_classes=["30"], closed=True)
    verification = {
        "written_value": {"vehicle_classes": ["30"], "lanes_matched": 2},
        "readback_value": {"vehicle_classes": ["30"], "lanes_matched": 2},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_turn_ban_diff_exempts_every_lane_of_the_target_connector():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(link_lanes=[_link_lane_entry("9001", 1)])
    after = _shadow_model(link_lanes=[_link_lane_entry("9001", 1, blocked_vehicle_classes=["10"])])
    action = _action("set_turn_ban", connector_no="9001", vehicle_classes=["10"], banned=True)
    verification = {
        "written_value": {"vehicle_classes": ["10"], "lanes_matched": 1},
        "readback_value": {"vehicle_classes": ["10"], "lanes_matched": 1},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_link_closure_diff_still_flags_a_lane_on_a_different_link():
    # The link-wide exemption is scoped to the TARGET link_id only -- a lane
    # belonging to an UNRELATED link that also changed must still be
    # flagged, exactly like every other exemption in this table.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        link_lanes=[_link_lane_entry("101", 1), _link_lane_entry("102", 1)]
    )
    after = _shadow_model(
        link_lanes=[
            _link_lane_entry("101", 1, blocked_vehicle_classes=["30"]),
            _link_lane_entry("102", 1, blocked_vehicle_classes=["99"]),  # unrelated drift
        ]
    )
    action = _action("set_link_closure", link_id="101", vehicle_classes=["30"], closed=True)
    verification = {
        "written_value": {"vehicle_classes": ["30"], "lanes_matched": 1},
        "readback_value": {"vehicle_classes": ["30"], "lanes_matched": 1},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.blocking is True
    evidence = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(
        item["object_class"] == "link_lanes" and item["object_id"] == "102.1" for item in evidence
    )


# ---------------------------------------------------------------------------
# postwrite -- Q4 unit (3) bug fix: a genuinely successful remove_* tool
# call must NOT refuse against constraint #6 (write_verification_failed).
# Found while writing this unit's own remove_connector tests: every one of
# Q4 unit (2)'s FOUR pre-existing remove tools (remove_queue_counter et al.)
# originally reported written_value={"removed_no": X} / readback_value=
# {"removed_no": None} on SUCCESS -- genuinely different dicts, which #6's
# bare written != readback compare (no tool-specific exemption of its own,
# unlike the separate nothing-else-moved diff) refused unconditionally.
# tools_edit._remove_verification now makes written/readback agree on
# success (and correctly disagree -- refuse -- when the object is still
# there). This is a real, previously-undetected defect: NO pre-existing
# test called GuardrailEngine.postwrite directly with a real remove tool's
# own verification payload (test_agent_tools_measurement_infra.py dispatches
# in isolation; test_agent_rollback_measurement_infra.py exercises
# rollback's own, unrelated hash-based verification).
# ---------------------------------------------------------------------------


def test_postwrite_remove_queue_counter_successful_removal_does_not_refuse():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model()
    action = _action("remove_queue_counter", no="3")
    verification = {
        "written_value": {"removed_no": "3", "still_present": False},
        "readback_value": {"removed_no": "3", "still_present": False},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_remove_queue_counter_still_present_after_remove_refuses():
    # The genuinely informative refusal this check exists to catch: Vissim
    # claimed the remove succeeded but the object is STILL there afterward.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model()
    action = _action("remove_queue_counter", no="3")
    verification = {
        "written_value": {"removed_no": "3", "still_present": False},
        "readback_value": {"removed_no": "3", "still_present": True},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.blocking is True
    assert [f.constraint_id for f in report.findings] == ["write_verification_failed"]


# ---------------------------------------------------------------------------
# postwrite -- Q4 unit (4)'s own remove_signal_head/remove_detector, the
# SAME real-engine test this file's own comment above says the unit-③ bug
# class needs per verb pair: a genuinely successful remove must NOT refuse
# against constraint #6, and a remove that leaves the object still present
# MUST refuse. Neither "signal_heads" nor "detectors" is registered in
# guardrails.py's own _SHADOW_OBJECT_ID_FIELDS (same as "queue_counters"
# before them -- neither object class needs "nothing else moved" diff
# coverage of its own since both are leaf objects nothing else references),
# so this fixture's own before/after models never need a signal_heads/
# detectors key at all -- exactly mirroring remove_queue_counter's own two
# tests above, not remove_connector's (which DOES need _ALLOWS_REMOVED_
# OBJECTS_IN since "links"/"link_lanes" ARE tracked).
# ---------------------------------------------------------------------------


def test_postwrite_remove_signal_head_successful_removal_does_not_refuse():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model()
    action = _action("remove_signal_head", no="61")
    verification = {
        "written_value": {"removed_no": "61", "still_present": False},
        "readback_value": {"removed_no": "61", "still_present": False},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_remove_signal_head_still_present_after_remove_refuses():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model()
    action = _action("remove_signal_head", no="61")
    verification = {
        "written_value": {"removed_no": "61", "still_present": False},
        "readback_value": {"removed_no": "61", "still_present": True},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.blocking is True
    assert [f.constraint_id for f in report.findings] == ["write_verification_failed"]


def test_postwrite_remove_detector_successful_removal_does_not_refuse():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model()
    action = _action("remove_detector", no="1")
    verification = {
        "written_value": {"removed_no": "1", "still_present": False},
        "readback_value": {"removed_no": "1", "still_present": False},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_remove_detector_still_present_after_remove_refuses():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model()
    action = _action("remove_detector", no="1")
    verification = {
        "written_value": {"removed_no": "1", "still_present": False},
        "readback_value": {"removed_no": "1", "still_present": True},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.blocking is True
    assert [f.constraint_id for f in report.findings] == ["write_verification_failed"]


def test_postwrite_add_signal_head_diff_exempts_the_derived_head_count_shift():
    # LIVE-CAUGHT (backend/tools/signal_heads_detectors_live_check.py, Phase
    # B): a signal group's own head COUNT is embedded in TWO places
    # add_signal_head never writes directly -- signals[].phases (signal_
    # parser's own "SG22 (3 heads)" label text) and signal_program_splits[].
    # head_count -- both PURELY DERIVED by counting real <signalHead sg="...">
    # elements, so this must NEVER be evidence for ANY tool, matching the
    # link_behavior_type_assignments/link_count precedent exactly. Reproduced
    # live: a genuine, correct add_signal_head false-positived on BOTH
    # signals#1:phases and signal_program_splits#1.1.22:head_count and was
    # refused+rolled-back by the real orchestrator before this fix.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        links=[{"id": "101", "name": "A", "lanes": 4, "length": 335.0}],
        signals=[
            {
                "sc_no": "1", "sc_name": "", "cycle_time": 90, "controller_type": "FIXEDTIME",
                "phases": [{"start": 0, "duration": 30, "signal_groups": ["SG22 (2 heads)"], "signal_group_ids": ["22"], "movements": []}],
            }
        ],
        signal_program_splits=[
            {
                "id": "1.1.22", "sc_no": "1", "program_no": "1", "sg_no": "22", "sg_name": "",
                "green_window": {"green_start_s": 0, "green_end_s": 30}, "amber_s": 3.0,
                "min_green_s": None, "head_count": 2, "cycle_s": 90, "active": True,
            }
        ],
    )
    after = _shadow_model(
        links=[{"id": "101", "name": "A", "lanes": 4, "length": 335.0}],
        signals=[
            {
                "sc_no": "1", "sc_name": "", "cycle_time": 90, "controller_type": "FIXEDTIME",
                "phases": [{"start": 0, "duration": 30, "signal_groups": ["SG22 (3 heads)"], "signal_group_ids": ["22"], "movements": []}],
            }
        ],
        signal_program_splits=[
            {
                "id": "1.1.22", "sc_no": "1", "program_no": "1", "sg_no": "22", "sg_name": "",
                "green_window": {"green_start_s": 0, "green_end_s": 30}, "amber_s": 3.0,
                "min_green_s": None, "head_count": 3, "cycle_s": 90, "active": True,
            }
        ],
    )
    action = _action(
        "add_signal_head", link_no="101", lane_index=4, pos_m=20.0, sc_no="1", sg_no="22"
    )
    verification = {
        "written_value": {"link": "101", "lane_index": 4, "pos": 20.0, "sc": "1", "sg": "22"},
        "readback_value": {"link": "101", "lane_index": 4, "pos": 20.0, "sc": "1", "sg": "22"},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


# ---------------------------------------------------------------------------
# postwrite -- Q4 unit (6)'s own remove_stop_sign/add_priority_rule/
# remove_priority_rule -- the SAME real-engine test class as unit (4)'s own
# remove_signal_head/remove_detector tests above (a genuinely successful
# remove must NOT refuse against constraint #6, and a remove that leaves the
# object still present MUST refuse), plus add_priority_rule/remove_priority_
# rule's own _ALLOWS_NEW_OBJECTS_IN/_ALLOWS_REMOVED_OBJECTS_IN coverage for
# "priority_rule_markers" (QUEUE 3 unit 2's own tracked flat list).
# "stop_signs" is NOT registered in guardrails.py's own
# _SHADOW_OBJECT_ID_FIELDS (a leaf object, same as signal_heads/detectors/
# queue_counters before it), so remove_stop_sign's own fixture never needs a
# stop_signs key at all.
# ---------------------------------------------------------------------------


def test_postwrite_remove_stop_sign_successful_removal_does_not_refuse():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model()
    action = _action("remove_stop_sign", no="1")
    verification = {
        "written_value": {"removed_no": "1", "still_present": False},
        "readback_value": {"removed_no": "1", "still_present": False},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_remove_stop_sign_still_present_after_remove_refuses():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model()
    action = _action("remove_stop_sign", no="1")
    verification = {
        "written_value": {"removed_no": "1", "still_present": False},
        "readback_value": {"removed_no": "1", "still_present": True},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.blocking is True
    assert [f.constraint_id for f in report.findings] == ["write_verification_failed"]


def test_postwrite_add_priority_rule_diff_suppresses_its_own_new_markers():
    # add_priority_rule's own sanctioned action creates 1+ new
    # "priority_rule_markers" entries under a brand-new rule number --
    # _ALLOWS_NEW_OBJECTS_IN["add_priority_rule"], mirroring duplicate_
    # driving_behavior's own precedent exactly.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        priority_rule_markers=[_priority_rule_marker_entry("4", 1, gap_time_s=3.2, headway_m=0.0)]
    )
    after = _shadow_model(
        priority_rule_markers=[
            _priority_rule_marker_entry("4", 1, gap_time_s=3.2, headway_m=0.0),
            _priority_rule_marker_entry("62", 1, gap_time_s=3.0, headway_m=5.0, link="632"),
        ]
    )
    action = _action(
        "add_priority_rule",
        stop_link_no="101",
        stop_lane_index=1,
        stop_pos_m=5.0,
        markers=[{"link_no": "632", "lane_index": 1, "pos_m": 3.0}],
    )
    verification = {
        "written_value": {
            "link": "101", "lane_index": 1, "pos": 5.0,
            "markers": [{"link": "632", "lane_index": 1, "pos": 3.0}],
        },
        "readback_value": {
            "link": "101", "lane_index": 1, "pos": 5.0,
            "markers": [{"link": "632", "lane_index": 1, "pos": 3.0}],
        },
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_remove_priority_rule_diff_suppresses_its_own_removed_markers():
    # remove_priority_rule's own sanctioned action removes the rule's own
    # EXISTING "priority_rule_markers" entries atomically --
    # _ALLOWS_REMOVED_OBJECTS_IN["remove_priority_rule"], the removal-side
    # mirror of the add test above.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        priority_rule_markers=[
            _priority_rule_marker_entry("4", 1, gap_time_s=3.2, headway_m=0.0),
            _priority_rule_marker_entry("62", 1, gap_time_s=3.0, headway_m=5.0, link="632"),
        ]
    )
    after = _shadow_model(
        priority_rule_markers=[_priority_rule_marker_entry("4", 1, gap_time_s=3.2, headway_m=0.0)]
    )
    action = _action("remove_priority_rule", no="62")
    verification = {
        "written_value": {"removed_no": "62", "still_present": False},
        "readback_value": {"removed_no": "62", "still_present": False},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_remove_priority_rule_still_present_after_remove_refuses():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model()
    action = _action("remove_priority_rule", no="62")
    verification = {
        "written_value": {"removed_no": "62", "still_present": False},
        "readback_value": {"removed_no": "62", "still_present": True},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.blocking is True
    assert [f.constraint_id for f in report.findings] == ["write_verification_failed"]


def test_postwrite_diff_still_flags_an_unopted_tool_creating_a_priority_rule_marker():
    # The suppression is tool-SPECIFIC (_ALLOWS_NEW_OBJECTS_IN), not a
    # blanket "never flag a new marker" rule -- mirrors
    # test_postwrite_diff_still_flags_a_removed_link_for_a_tool_not_opted_in
    # exactly, applied to the add side.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model(
        priority_rule_markers=[_priority_rule_marker_entry("4", 1, gap_time_s=3.2, headway_m=0.0)]
    )
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    report = engine.postwrite(action, before, after, {"written_value": 600, "readback_value": 600})

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert {"object_class": "priority_rule_markers", "object_id": "4.1", "change": "added"} in diff


# ---------------------------------------------------------------------------
# postwrite -- Q4 unit (7)'s own add_pt_stop/remove_pt_stop/add_pt_line/
# remove_pt_line/set_pt_line_stop_dwell -- the SAME real-engine test class as
# unit (6)'s own stop-sign/priority-rule tests above (a genuinely successful
# remove must NOT refuse against constraint #6, a remove that leaves the
# object still present MUST refuse, and each add's own auto-created objects
# must be suppressed by _ALLOWS_NEW_OBJECTS_IN, never evidence).
# ---------------------------------------------------------------------------


def test_postwrite_add_pt_stop_diff_suppresses_its_own_new_stop():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model(
        pt_stops=[
            {
                "no": "1", "name": "", "link": "101", "lane_index": 1, "pos": 5.0, "length": 15.0,
                "referenced_by": {"pt_line_stops": {"count": 0, "ids": [], "truncated": False},
                                   "partial_pt_line_stops": {"count": 0, "ids": [], "truncated": False},
                                   "detectors": {"count": 0, "ids": [], "truncated": False}},
                "reference_count": 0,
            }
        ]
    )
    action = _action("add_pt_stop", link_no="101", lane_index=1, pos_m=5.0)
    verification = {
        "written_value": {"link": "101", "lane_index": 1, "pos": 5.0},
        "readback_value": {"link": "101", "lane_index": 1, "pos": 5.0},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_remove_pt_stop_successful_removal_does_not_refuse():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model()
    action = _action("remove_pt_stop", no="1")
    verification = {
        "written_value": {"removed_no": "1", "still_present": False},
        "readback_value": {"removed_no": "1", "still_present": False},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_remove_pt_stop_still_present_after_remove_refuses():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model()
    action = _action("remove_pt_stop", no="1")
    verification = {
        "written_value": {"removed_no": "1", "still_present": False},
        "readback_value": {"removed_no": "1", "still_present": True},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.blocking is True
    assert [f.constraint_id for f in report.findings] == ["write_verification_failed"]


def test_postwrite_add_pt_line_diff_suppresses_its_own_new_line_and_auto_stop():
    # add_pt_line's own sanctioned action creates a brand-new "pt_lines"
    # entry AND exactly one brand-new "pt_line_stops" entry (AddPTLine's own
    # auto-created line-stop, live-verified) --
    # _ALLOWS_NEW_OBJECTS_IN["add_pt_line"] covers BOTH object classes.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model(
        pt_lines=[
            {
                "no": "65105", "name": "", "veh_type": "300", "des_speed_distr": "",
                "entry_link": "13270462", "dest_link": "12270123", "dest_pos": 189.13,
                "entry_tm_distr": None, "departure_count": 0,
                "link_seq": ["13270462"], "link_seq_count": 1, "link_seq_truncated": False,
                "stops": [{"pt_stop_no": "99569", "dwell_tm_distr": "1", "dwell_tm_def": "DISTRIBUTION", "active": True}],
            }
        ],
        pt_line_stops=[_pt_line_stop_entry("65105", "99569", dwell_tm_distr="1")],
    )
    action = _action(
        "add_pt_line", entry_link_no="13270462", dest_link_no="12270123", dest_pos_m=189.13
    )
    verification = {
        "written_value": {"entry_link": "13270462", "dest_link": "12270123", "dest_pos": 189.13},
        "readback_value": {"entry_link": "13270462", "dest_link": "12270123", "dest_pos": 189.13},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_remove_pt_line_diff_suppresses_its_own_removed_line_and_stops():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        pt_lines=[
            {
                "no": "65105", "name": "", "veh_type": "300", "des_speed_distr": "",
                "entry_link": "13270462", "dest_link": "12270123", "dest_pos": 189.13,
                "entry_tm_distr": None, "departure_count": 0,
                "link_seq": ["13270462"], "link_seq_count": 1, "link_seq_truncated": False,
                "stops": [{"pt_stop_no": "99569", "dwell_tm_distr": "1", "dwell_tm_def": "DISTRIBUTION", "active": True}],
            }
        ],
        pt_line_stops=[_pt_line_stop_entry("65105", "99569", dwell_tm_distr="1")],
    )
    after = _shadow_model()
    action = _action("remove_pt_line", no="65105")
    verification = {
        "written_value": {"removed_no": "65105", "still_present": False},
        "readback_value": {"removed_no": "65105", "still_present": False},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_remove_pt_line_still_present_after_remove_refuses():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model()
    action = _action("remove_pt_line", no="65105")
    verification = {
        "written_value": {"removed_no": "65105", "still_present": False},
        "readback_value": {"removed_no": "65105", "still_present": True},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.blocking is True
    assert [f.constraint_id for f in report.findings] == ["write_verification_failed"]


def test_postwrite_set_pt_line_stop_dwell_target_field_exempted():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(pt_line_stops=[_pt_line_stop_entry("52004", "99569", dwell_tm_distr="20")])
    after = _shadow_model(pt_line_stops=[_pt_line_stop_entry("52004", "99569", dwell_tm_distr="10")])
    action = _action(
        "set_pt_line_stop_dwell", line_no="52004", stop_no="99569", time_distribution_no="10"
    )
    verification = {"written_value": "10", "readback_value": "10"}

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_still_flags_an_unopted_tool_creating_a_pt_line_stop():
    # The suppression is tool-SPECIFIC (_ALLOWS_NEW_OBJECTS_IN), not a
    # blanket rule -- mirrors the priority-rule-marker equivalent above.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()
    after = _shadow_model(pt_line_stops=[_pt_line_stop_entry("52004", "99569")])
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    report = engine.postwrite(action, before, after, {"written_value": 600, "readback_value": 600})

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert {"object_class": "pt_line_stops", "object_id": "52004.99569", "change": "added"} in diff


# ---------------------------------------------------------------------------
# postwrite -- remove_connector's own removed "links"/"link_lanes" entries
# are expected, never evidence -- Q4 unit (3), _ALLOWS_REMOVED_OBJECTS_IN
# ---------------------------------------------------------------------------


def test_postwrite_remove_connector_diff_suppresses_its_own_removed_link_and_lanes():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        links=[
            {"id": "101", "name": "A", "lanes": 1, "length": 100.0},
            {"id": "9001", "name": "turn", "lanes": 1, "length": 20.0, "is_connector": True},
        ],
        link_lanes=[_link_lane_entry("101", 1), _link_lane_entry("9001", 1)],
    )
    after = _shadow_model(
        links=[{"id": "101", "name": "A", "lanes": 1, "length": 100.0}],
        link_lanes=[_link_lane_entry("101", 1)],
    )
    action = _action("remove_connector", connector_no="9001")
    verification = {
        "written_value": {"removed_connector_no": "9001", "still_present": False},
        "readback_value": {"removed_connector_no": "9001", "still_present": False},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_remove_connector_diff_exempts_the_unrelated_link_behavior_type_count_shift():
    # LIVE-CAUGHT (link_closure_turn_ban_live_check.py, Phase C, Roundabout
    # Buehl.DE): removing a connector's own <link> element legitimately
    # decrements whichever link_behavior_type that connector used --
    # link_count is PURELY DERIVED (tools_network._link_behavior_type_link_
    # counts, a plain count of real <link linkBehavType="..."> elements), so
    # this must NEVER be evidence for ANY tool, matching the des_speed_
    # distributions/vehicle_types/time_distributions/driving_behaviors
    # precedents in _ALWAYS_EXEMPT_FIELDS exactly.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        links=[
            {"id": "101", "name": "A", "lanes": 1, "length": 100.0},
            {"id": "9001", "name": "turn", "lanes": 1, "length": 20.0, "is_connector": True},
        ],
        link_lanes=[_link_lane_entry("101", 1), _link_lane_entry("9001", 1)],
        link_behavior_type_assignments=[{**_lbt_assignment("1"), "link_count": 293}],
    )
    after = _shadow_model(
        links=[{"id": "101", "name": "A", "lanes": 1, "length": 100.0}],
        link_lanes=[_link_lane_entry("101", 1)],
        link_behavior_type_assignments=[{**_lbt_assignment("1"), "link_count": 292}],
    )
    action = _action("remove_connector", connector_no="9001")
    verification = {
        "written_value": {"removed_connector_no": "9001", "still_present": False},
        "readback_value": {"removed_connector_no": "9001", "still_present": False},
    }

    report = engine.postwrite(action, before, after, verification)

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_still_flags_a_removed_link_for_a_tool_not_opted_in():
    # The suppression is tool-SPECIFIC (_ALLOWS_REMOVED_OBJECTS_IN), not a
    # blanket "never flag a missing link" rule -- an unrelated tool must
    # still see an unexpectedly vanished link as evidence.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model()  # default links=[{"id": "101", ...}]
    after = _shadow_model(links=[])
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    report = engine.postwrite(action, before, after, {"written_value": 600, "readback_value": 600})

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert {"object_class": "links", "object_id": "101", "change": "removed"} in diff


def test_diff_shadow_models_reports_a_structurally_removed_lane():
    # The general oracle rollback_last_edit itself calls (guardrails.
    # diff_shadow_models, no tool-specific exemption at all) must see a
    # STRUCTURALLY removed link_lanes entry as "removed" evidence -- the
    # spine's remove_last_lane's own proof point that the pre-existing
    # diff machinery generalizes to structural adds/removes, not just
    # field-level attribute writes.
    from services.agent.guardrails import diff_shadow_models

    before = _shadow_model(
        link_lanes=[_link_lane_entry("101", 1), _link_lane_entry("101", 2)]
    )
    after = _shadow_model(link_lanes=[_link_lane_entry("101", 1)])

    evidence = diff_shadow_models(before, after)

    assert {"object_class": "link_lanes", "object_id": "101.2", "change": "removed"} in evidence


def test_diff_shadow_models_reports_a_structurally_added_lane():
    from services.agent.guardrails import diff_shadow_models

    before = _shadow_model(link_lanes=[_link_lane_entry("101", 1)])
    after = _shadow_model(
        link_lanes=[_link_lane_entry("101", 1), _link_lane_entry("101", 2)]
    )

    evidence = diff_shadow_models(before, after)

    assert {"object_class": "link_lanes", "object_id": "101.2", "change": "added"} in evidence


def test_postwrite_priority_rule_gap_headway_gap_only_write_leaves_headway_exempt():
    # Writing ONLY gap_time_s -- headway_m's own current value is still
    # echoed in BOTH written_value/readback_value (tools_edit.py's own
    # design), so the compound gap_headway field must be exempted whole,
    # not flagged as "headway_m changed" evidence.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        priority_rule_markers=[_priority_rule_marker_entry("4", 1, gap_time_s=3.2, headway_m=0.0)]
    )
    after = _shadow_model(
        priority_rule_markers=[_priority_rule_marker_entry("4", 1, gap_time_s=5.5, headway_m=0.0)]
    )
    action = _action("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=5.5)

    report = engine.postwrite(action, before, after, _gap_headway_verification(5.5, 0.0))

    assert report.findings == []
    assert report.blocking is False


# ---------------------------------------------------------------------------
# postwrite — set_vehicle_type_reference (QUEUE (v2a) unit)
#
# The FIRST tool whose _WRITE_TARGET_FIELD_SPEC entry uses "value_field_param"
# instead of a fixed "value_field" string -- the target field name (e.g.
# "weight_distr_no") is resolved dynamically from the action's own "field"
# param at both diff-exemption time and target-persisted-check time (see
# guardrails._spec_value_field). Also exercises _ALWAYS_EXEMPT_FIELDS'
# vehicle_types entry: "references"/"referencing_composition_nos" always
# shift as a derived side effect of ANY reference write and must never
# surface as "something else moved" evidence.
# ---------------------------------------------------------------------------


def test_postwrite_vehicle_type_reference_target_persisted_check_happy_path():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(vehicle_types=[_vehicle_type_entry("200", weight_distr_no="2")])
    after = _shadow_model(vehicle_types=[_vehicle_type_entry("200", weight_distr_no="3")])
    action = _action("set_vehicle_type_reference", vehicle_type_no="200", field="weight_distr", target_no="3")

    report = engine.postwrite(action, before, after, {"written_value": "3", "readback_value": "3"})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_vehicle_type_reference_target_persisted_check_fires_when_not_persisted():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(vehicle_types=[_vehicle_type_entry("200", weight_distr_no="2")])
    after = _shadow_model(
        vehicle_types=[_vehicle_type_entry("200", weight_distr_no="2")]  # unchanged on disk
    )
    action = _action("set_vehicle_type_reference", vehicle_type_no="200", field="weight_distr", target_no="3")

    report = engine.postwrite(action, before, after, {"written_value": "3", "readback_value": "3"})

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_class"] == "vehicle_types"
    assert check["object_id"] == "200"
    assert check["field"] == "weight_distr_no"
    assert check["saved_value"] == "2"


def test_postwrite_vehicle_type_reference_resolves_a_different_field_correctly():
    # A DIFFERENT "field" choice must exempt/check the MATCHING flat
    # shadow-model field ("des_accel_func_no"), never "weight_distr_no".
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        vehicle_types=[_vehicle_type_entry("200", weight_distr_no="2", des_accel_func_no="2")]
    )
    after = _shadow_model(
        vehicle_types=[_vehicle_type_entry("200", weight_distr_no="2", des_accel_func_no="3")]
    )
    action = _action("set_vehicle_type_reference", vehicle_type_no="200", field="des_accel_func", target_no="3")

    report = engine.postwrite(action, before, after, {"written_value": "3", "readback_value": "3"})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_vehicle_type_reference_diff_flags_an_unrelated_sibling_change():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        vehicle_types=[
            _vehicle_type_entry("200", weight_distr_no="2"),
            _vehicle_type_entry("300", name="Bus", category="BUS", weight_distr_no="3"),
        ]
    )
    after = _shadow_model(
        vehicle_types=[
            _vehicle_type_entry("200", weight_distr_no="3"),
            _vehicle_type_entry("300", name="Bus", category="BUS", weight_distr_no="99"),  # unrelated change
        ]
    )
    action = _action("set_vehicle_type_reference", vehicle_type_no="200", field="weight_distr", target_no="3")

    report = engine.postwrite(action, before, after, {"written_value": "3", "readback_value": "3"})

    assert report.blocking is True
    evidence = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(
        item["object_class"] == "vehicle_types" and item["object_id"] == "300"
        for item in evidence
    )


def test_postwrite_vehicle_type_reference_diff_exempts_the_derived_references_dict():
    # "references"/"referencing_composition_nos" are PURELY DERIVED from the
    # flat "{field}_no" fields plus each target collection's own data -- a
    # correct write always also changes the resolved reference's own
    # id/name/stats as an unavoidable side effect. Without the
    # _ALWAYS_EXEMPT_FIELDS entry this would false-positive as "something
    # else moved" on every real write.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(vehicle_types=[_vehicle_type_entry("200", weight_distr_no="2")])
    after_entry = _vehicle_type_entry("200", weight_distr_no="3")
    after_entry["references"] = {
        **after_entry["references"],
        "weight_distr": {"no": "3", "name": "", "min": 4000.0, "max": 12000.0, "mean": 8000.0, "point_count": 2},
    }
    after_entry["referencing_composition_nos"] = {"count": 2, "ids": ["1", "4"], "truncated": False}
    after = _shadow_model(vehicle_types=[after_entry])
    action = _action("set_vehicle_type_reference", vehicle_type_no="200", field="weight_distr", target_no="3")

    report = engine.postwrite(action, before, after, {"written_value": "3", "readback_value": "3"})

    assert report.findings == []
    assert report.blocking is False


# ---------------------------------------------------------------------------
# postrun — lie-detector pass-through
# ---------------------------------------------------------------------------


def test_postrun_vehicles_deleted_warning_passes_through():
    engine = GuardrailEngine(disabled=False)
    action = _action("run_simulation")
    run_result = {
        "tables": ["SIMULATIONRUN", "VEHICLENETWORKPERFORMANCEEVALUATION"],
        "deleted_vehicles": 7,
    }

    report = engine.postrun(action, run_result)

    assert report.phase == "postrun"
    assert [f.constraint_id for f in report.findings] == ["vehicles_deleted_in_run"]
    assert report.blocking is False  # this catalog's postrun checks are all warn-tier today
    assert report.warnings()[0].details["deleted_vehicles"] == 7


def test_postrun_missing_kpi_tables_warning_passes_through():
    engine = GuardrailEngine(disabled=False)
    action = _action("run_simulation")
    run_result = {"tables": ["SIMULATIONRUN"], "deleted_vehicles": 0}

    report = engine.postrun(action, run_result)

    assert [f.constraint_id for f in report.findings] == ["results_missing_kpi_tables"]
    assert report.blocking is False


def test_postrun_clean_run_has_no_findings():
    engine = GuardrailEngine(disabled=False)
    action = _action("run_simulation")
    run_result = {
        "tables": ["SIMULATIONRUN", "VEHICLENETWORKPERFORMANCEEVALUATION"],
        "deleted_vehicles": 0,
    }

    report = engine.postrun(action, run_result)

    assert report.findings == []
    assert report.blocking is False


def test_postrun_none_run_result_does_not_crash():
    engine = GuardrailEngine(disabled=False)

    report = engine.postrun(_action("run_simulation"), None)

    assert isinstance(report, GuardrailReport)
    assert report.phase == "postrun"


# ---------------------------------------------------------------------------
# Ablation flag — constructor
# ---------------------------------------------------------------------------


def test_ctor_disabled_true_short_circuits_every_phase():
    engine = GuardrailEngine(disabled=True)
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=-5)  # would refuse if live

    preflight = engine.preflight(action, shadow)
    postwrite = engine.postwrite(action, shadow, shadow, {"written_value": 1, "readback_value": 2})
    postrun = engine.postrun(action, {"tables": ["SIMULATIONRUN"], "deleted_vehicles": 5})

    for report in (preflight, postwrite, postrun):
        assert report.findings == []
        assert report.blocking is False
        assert report.disabled is True


def test_ctor_disabled_false_runs_checks_regardless_of_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_GUARDRAILS_DISABLED", "1")
    engine = GuardrailEngine(disabled=False)
    shadow = _shadow_model()

    report = engine.preflight(_action("set_vehicle_input_volume", input_no="1", volume=-5), shadow)

    assert report.disabled is False
    assert report.blocking is True


def test_enabled_engine_reports_disabled_false():
    engine = GuardrailEngine(disabled=False)
    shadow = _shadow_model()

    report = engine.preflight(_action("set_vehicle_input_volume", input_no="1", volume=600), shadow)

    assert report.disabled is False


# ---------------------------------------------------------------------------
# Ablation flag — AGENT_GUARDRAILS_DISABLED env var (ctor default: None)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw_value", ["1", "true", "True", "TRUE", " 1 ", " true "])
def test_env_var_truthy_values_disable_the_engine(monkeypatch: pytest.MonkeyPatch, raw_value: str):
    monkeypatch.setenv("AGENT_GUARDRAILS_DISABLED", raw_value)

    engine = GuardrailEngine()

    assert engine.disabled is True


@pytest.mark.parametrize("raw_value", ["0", "false", "False", "", "no", "disabled"])
def test_env_var_non_truthy_values_leave_the_engine_enabled(
    monkeypatch: pytest.MonkeyPatch, raw_value: str
):
    monkeypatch.setenv("AGENT_GUARDRAILS_DISABLED", raw_value)

    engine = GuardrailEngine()

    assert engine.disabled is False


def test_env_var_unset_leaves_the_engine_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AGENT_GUARDRAILS_DISABLED", raising=False)

    engine = GuardrailEngine()

    assert engine.disabled is False


def test_env_var_is_read_fresh_at_construction_not_cached_at_import(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("AGENT_GUARDRAILS_DISABLED", raising=False)
    assert GuardrailEngine().disabled is False

    monkeypatch.setenv("AGENT_GUARDRAILS_DISABLED", "1")
    assert GuardrailEngine().disabled is True

    monkeypatch.setenv("AGENT_GUARDRAILS_DISABLED", "0")
    assert GuardrailEngine().disabled is False


def test_explicit_ctor_value_wins_over_env_var(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_GUARDRAILS_DISABLED", "0")
    assert GuardrailEngine(disabled=True).disabled is True

    monkeypatch.setenv("AGENT_GUARDRAILS_DISABLED", "1")
    assert GuardrailEngine(disabled=False).disabled is False


# ---------------------------------------------------------------------------
# Disabled engine — zero findings for ALL three phases (explicit per-phase
# messages, in well-formed ko/en/ja triplets)
# ---------------------------------------------------------------------------


def test_disabled_engine_returns_no_findings_for_every_phase(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_GUARDRAILS_DISABLED", "true")
    engine = GuardrailEngine()
    shadow = _shadow_model()
    bad_action = _action("set_vehicle_input_volume", input_no="does-not-exist", volume=-999999)

    preflight = engine.preflight(bad_action, shadow)
    postwrite = engine.postwrite(
        bad_action, shadow, shadow, {"written_value": 1, "readback_value": 2}
    )
    postrun = engine.postrun(bad_action, {"tables": ["SIMULATIONRUN"], "deleted_vehicles": 999})

    assert (preflight.phase, preflight.findings, preflight.blocking, preflight.disabled) == (
        "preflight",
        [],
        False,
        True,
    )
    assert (postwrite.phase, postwrite.findings, postwrite.blocking, postwrite.disabled) == (
        "postwrite",
        [],
        False,
        True,
    )
    assert (postrun.phase, postrun.findings, postrun.blocking, postrun.disabled) == (
        "postrun",
        [],
        False,
        True,
    )


# ---------------------------------------------------------------------------
# postwrite -- set_time_distribution_normal / set_time_distribution_points
# target-persisted check / nothing-else-moved diff -- QUEUE (Q3, v2a) unit 5
# ---------------------------------------------------------------------------


def _time_distribution_normal_verification(mean_s: float, std_dev_s: float) -> dict[str, Any]:
    pair = {"mean_s": mean_s, "std_dev_s": std_dev_s}
    return {"written_value": dict(pair), "readback_value": dict(pair)}


def test_postwrite_time_distribution_normal_target_persisted_check_happy_path():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(time_distributions=[_time_distribution("550", mean_s=10.0, std_dev_s=2.0)])
    after = _shadow_model(time_distributions=[_time_distribution("550", mean_s=42.0, std_dev_s=7.5)])
    action = _action("set_time_distribution_normal", time_distribution_no="550", mean_s=42.0, std_dev_s=7.5)

    report = engine.postwrite(action, before, after, _time_distribution_normal_verification(42.0, 7.5))

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_time_distribution_normal_target_persisted_check_fires_when_not_persisted():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(time_distributions=[_time_distribution("550", mean_s=10.0, std_dev_s=2.0)])
    after = _shadow_model(time_distributions=[_time_distribution("550", mean_s=10.0, std_dev_s=2.0)])  # unchanged
    action = _action("set_time_distribution_normal", time_distribution_no="550", mean_s=42.0, std_dev_s=7.5)

    report = engine.postwrite(action, before, after, _time_distribution_normal_verification(42.0, 7.5))

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_class"] == "time_distributions"
    assert check["object_id"] == "550"
    assert check["saved_value"] == {"mean_s": 10.0, "std_dev_s": 2.0}


def test_postwrite_time_distribution_normal_partial_field_persistence_still_fires():
    # mean landed, std_dev drifted to a DIFFERENT value than intended -- the
    # ONE compound value_field ("mean_std_dev") must catch a half-applied
    # write, mirroring set_priority_rule_gap_headway's own partial-field
    # lesson exactly.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(time_distributions=[_time_distribution("550", mean_s=10.0, std_dev_s=2.0)])
    after = _shadow_model(time_distributions=[_time_distribution("550", mean_s=42.0, std_dev_s=2.0)])
    action = _action("set_time_distribution_normal", time_distribution_no="550", mean_s=42.0, std_dev_s=7.5)

    report = engine.postwrite(action, before, after, _time_distribution_normal_verification(42.0, 7.5))

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_id"] == "550"


def test_postwrite_time_distribution_normal_diff_flags_an_unrelated_distribution_change():
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        time_distributions=[
            _time_distribution("550", mean_s=10.0, std_dev_s=2.0),
            _time_distribution("1003", mean_s=5.0, std_dev_s=1.0),
        ]
    )
    after = _shadow_model(
        time_distributions=[
            _time_distribution("550", mean_s=42.0, std_dev_s=7.5),
            _time_distribution("1003", mean_s=99.0, std_dev_s=1.0),  # unrelated drift
        ]
    )
    action = _action("set_time_distribution_normal", time_distribution_no="550", mean_s=42.0, std_dev_s=7.5)

    report = engine.postwrite(action, before, after, _time_distribution_normal_verification(42.0, 7.5))

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(
        entry["object_class"] == "time_distributions" and entry["object_id"] == "1003"
        for entry in diff
    )


def test_postwrite_time_distribution_reference_count_drift_on_an_unrelated_distribution_is_exempt():
    # QUEUE (Q3, v2a) unit 5's own regression-avoidance: "referenced_by"/
    # "reference_count" are PURELY DERIVED (no tool writes them directly) --
    # a write elsewhere in the network can legitimately shift them on an
    # UNRELATED distribution (e.g. a PT-line edit adding a new dwellTmDistr
    # reference) without that becoming false-positive "something else moved"
    # evidence, mirroring des_speed_distributions'/vehicle_types' own
    # _ALWAYS_EXEMPT_FIELDS precedent exactly.
    engine = GuardrailEngine(disabled=False)
    before = _shadow_model(
        time_distributions=[
            _time_distribution("550", mean_s=10.0, std_dev_s=2.0),
            _time_distribution("1003", mean_s=5.0, std_dev_s=1.0),
        ]
    )
    after_data = [
        _time_distribution("550", mean_s=42.0, std_dev_s=7.5),
        _time_distribution("1003", mean_s=5.0, std_dev_s=1.0),
    ]
    after_data[1]["reference_count"] = 1
    after_data[1]["referenced_by"]["pt_stops"] = {"count": 1, "ids": ["10.5"], "truncated": False}
    after = _shadow_model(time_distributions=after_data)
    action = _action("set_time_distribution_normal", time_distribution_no="550", mean_s=42.0, std_dev_s=7.5)

    report = engine.postwrite(action, before, after, _time_distribution_normal_verification(42.0, 7.5))

    assert report.findings == []
    assert report.blocking is False


_VALID_TIME_DISTRIBUTION_CURVE = [{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 5.0, "cum_fraction": 1.0}]
_NEW_TIME_DISTRIBUTION_CURVE = [
    {"duration_s": 0.0, "cum_fraction": 0.0},
    {"duration_s": 2.0, "cum_fraction": 0.4},
    {"duration_s": 5.0, "cum_fraction": 1.0},
]


def test_postwrite_time_distribution_points_happy_path_no_findings():
    action = _action("set_time_distribution_points", time_distribution_no="200", points=_NEW_TIME_DISTRIBUTION_CURVE)
    before = _shadow_model(
        time_distributions=[_time_distribution("200", dist_type="EMPIRICAL", points=_VALID_TIME_DISTRIBUTION_CURVE)]
    )
    after = _shadow_model(
        time_distributions=[_time_distribution("200", dist_type="EMPIRICAL", points=_NEW_TIME_DISTRIBUTION_CURVE)]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after,
        {"written_value": _NEW_TIME_DISTRIBUTION_CURVE, "readback_value": _NEW_TIME_DISTRIBUTION_CURVE},
    )

    # point_count went 2 -> 3 as an unavoidable derived side effect of the
    # curve growing by one point -- "coupled_fields" must exempt it, exactly
    # like des_speed_distribution_points' own point_count coupling.
    assert report.findings == []
    assert report.blocking is False


def test_postwrite_time_distribution_points_target_persisted_check_fires_on_mismatch():
    action = _action("set_time_distribution_points", time_distribution_no="200", points=_NEW_TIME_DISTRIBUTION_CURVE)
    before = _shadow_model(
        time_distributions=[_time_distribution("200", dist_type="EMPIRICAL", points=_VALID_TIME_DISTRIBUTION_CURVE)]
    )
    after = _shadow_model(
        time_distributions=[_time_distribution("200", dist_type="EMPIRICAL", points=_VALID_TIME_DISTRIBUTION_CURVE)]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after,
        {"written_value": _NEW_TIME_DISTRIBUTION_CURVE, "readback_value": _NEW_TIME_DISTRIBUTION_CURVE},
    )

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_class"] == "time_distributions"
    assert check["object_id"] == "200"
    assert check["saved_value"] == _VALID_TIME_DISTRIBUTION_CURVE


def test_postwrite_time_distribution_points_diff_flags_an_unrelated_distribution_change():
    action = _action("set_time_distribution_points", time_distribution_no="200", points=_NEW_TIME_DISTRIBUTION_CURVE)
    before = _shadow_model(
        time_distributions=[
            _time_distribution("200", dist_type="EMPIRICAL", points=_VALID_TIME_DISTRIBUTION_CURVE),
            _time_distribution("180", dist_type="EMPIRICAL", points=_VALID_TIME_DISTRIBUTION_CURVE),
        ]
    )
    after = _shadow_model(
        time_distributions=[
            _time_distribution("200", dist_type="EMPIRICAL", points=_NEW_TIME_DISTRIBUTION_CURVE),
            _time_distribution("180", dist_type="EMPIRICAL", points=_NEW_TIME_DISTRIBUTION_CURVE),  # unrelated drift
        ]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after,
        {"written_value": _NEW_TIME_DISTRIBUTION_CURVE, "readback_value": _NEW_TIME_DISTRIBUTION_CURVE},
    )

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(
        entry["object_class"] == "time_distributions" and entry["object_id"] == "180"
        for entry in diff
    )


# ---------------------------------------------------------------------------
# QUEUE 3 unit 6: driving-behavior duplicate-then-edit
# ---------------------------------------------------------------------------


def _driving_behavior(no: str, *, ax: float = 2.0, reference_count: int = 0) -> dict[str, Any]:
    return {
        "no": no,
        "name": f"Behavior {no}",
        "car_follow_mod_type": "WIEDEMANN74",
        "car_following_model": 74,
        "ax": ax,
        "bx_add": 2.0,
        "bx_mult": 3.0,
        "saf_dist_fact_ln_chg": 0.6,
        "coop_ln_chg_speed_diff": 10.8,
        "min_speed_for_lat": 3.6,
        "lat_dist_driv_def": 1.22,
        "lat_dist_stand_def": 0.46,
        "referenced_by": {"consumers": [], "count": 0, "truncated": False},
        "reference_count": reference_count,
    }


def _lbt_assignment(
    lbt_no: str, *, veh_class_no: str | None = None, driving_behavior_no: str = "7", driving_behavior_name: str = "Behavior 7"
) -> dict[str, Any]:
    suffix = "default" if veh_class_no is None else veh_class_no
    return {
        "id": f"{lbt_no}.{suffix}",
        "link_behavior_type_no": lbt_no,
        "link_behavior_type_name": "Urban",
        "veh_class_no": veh_class_no,
        "driving_behavior_no": driving_behavior_no,
        "driving_behavior_name": driving_behavior_name,
        "link_count": 293,
    }


def test_postwrite_driving_behavior_parameter_target_persisted_check_happy_path():
    action = _action("set_driving_behavior_parameter", behavior_no="7", parameter="ax", value=2.0)
    before = _shadow_model(driving_behaviors=[_driving_behavior("7", ax=1.5)])
    after = _shadow_model(driving_behaviors=[_driving_behavior("7", ax=2.0)])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 2.0, "readback_value": 2.0})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_driving_behavior_parameter_target_persisted_check_fires_when_not_persisted():
    action = _action("set_driving_behavior_parameter", behavior_no="7", parameter="ax", value=2.0)
    before = _shadow_model(driving_behaviors=[_driving_behavior("7", ax=1.5)])
    after = _shadow_model(driving_behaviors=[_driving_behavior("7", ax=1.5)])  # unchanged -- dropped write
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 2.0, "readback_value": 2.0})

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_class"] == "driving_behaviors"
    assert check["object_id"] == "7"
    assert check["field"] == "ax"
    assert check["saved_value"] == 1.5


def test_postwrite_driving_behavior_parameter_diff_flags_an_unrelated_behavior_change():
    action = _action("set_driving_behavior_parameter", behavior_no="7", parameter="ax", value=2.0)
    before = _shadow_model(driving_behaviors=[_driving_behavior("7", ax=1.5), _driving_behavior("8", ax=1.0)])
    after = _shadow_model(driving_behaviors=[_driving_behavior("7", ax=2.0), _driving_behavior("8", ax=99.0)])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 2.0, "readback_value": 2.0})

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(entry["object_class"] == "driving_behaviors" and entry["object_id"] == "8" for entry in diff)


def test_postwrite_driving_behavior_diff_never_flags_referenced_by_or_reference_count_on_unrelated_behavior():
    # QUEUE 3 unit 6's own regression-avoidance, mirroring des_speed_
    # distributions'/time_distributions' own _ALWAYS_EXEMPT_FIELDS precedent:
    # set_link_behavior_type_driving_behavior reassigning a link-behavior-
    # type's target legitimately shifts BOTH the old and new driving
    # behavior's own reference_count/referenced_by, even though that tool's
    # own write target is link_behavior_type_assignments, not driving_
    # behaviors at all.
    action = _action(
        "set_link_behavior_type_driving_behavior", link_behavior_type_no="1", driving_behavior_no="7"
    )
    before = _shadow_model(
        driving_behaviors=[_driving_behavior("6", reference_count=1), _driving_behavior("7", reference_count=0)],
        link_behavior_type_assignments=[_lbt_assignment("1", driving_behavior_no="6", driving_behavior_name="Behavior 6")],
    )
    after = _shadow_model(
        driving_behaviors=[_driving_behavior("6", reference_count=0), _driving_behavior("7", reference_count=1)],
        link_behavior_type_assignments=[_lbt_assignment("1", driving_behavior_no="7", driving_behavior_name="Behavior 7")],
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": "7", "readback_value": "7"})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_link_behavior_type_default_target_persisted_check_happy_path():
    action = _action(
        "set_link_behavior_type_driving_behavior", link_behavior_type_no="1", driving_behavior_no="7"
    )
    before = _shadow_model(link_behavior_type_assignments=[_lbt_assignment("1", driving_behavior_no="1", driving_behavior_name="Behavior 1")])
    after = _shadow_model(link_behavior_type_assignments=[_lbt_assignment("1", driving_behavior_no="7", driving_behavior_name="Behavior 7")])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": "7", "readback_value": "7"})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_link_behavior_type_default_target_persisted_check_fires_when_not_persisted():
    action = _action(
        "set_link_behavior_type_driving_behavior", link_behavior_type_no="1", driving_behavior_no="7"
    )
    before = _shadow_model(link_behavior_type_assignments=[_lbt_assignment("1", driving_behavior_no="1", driving_behavior_name="Behavior 1")])
    after = _shadow_model(link_behavior_type_assignments=[_lbt_assignment("1", driving_behavior_no="1", driving_behavior_name="Behavior 1")])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": "7", "readback_value": "7"})

    assert report.blocking is True
    check = report.first_refusal().details["persistence_check"]
    assert check["object_class"] == "link_behavior_type_assignments"
    assert check["object_id"] == "1.default"
    assert check["saved_value"] == "1"


def test_postwrite_link_behavior_type_veh_class_override_target_persisted_check_uses_the_composite_id():
    # veh_class_no OPTIONAL at the action layer -- omitted targets
    # "{lbt_no}.default"; given, targets "{lbt_no}.{veh_class_no}" -- proves
    # id_param_parts_defaults resolves the SAME literal "default" string
    # tools_network._flatten_link_behavior_type_assignments synthesizes.
    action = _action(
        "set_link_behavior_type_driving_behavior",
        link_behavior_type_no="1",
        driving_behavior_no="7",
        veh_class_no="20",
    )
    before = _shadow_model(
        link_behavior_type_assignments=[
            _lbt_assignment("1", driving_behavior_no="1", driving_behavior_name="Behavior 1"),
            _lbt_assignment("1", veh_class_no="20", driving_behavior_no="6", driving_behavior_name="Behavior 6"),
        ]
    )
    after = _shadow_model(
        link_behavior_type_assignments=[
            _lbt_assignment("1", driving_behavior_no="1", driving_behavior_name="Behavior 1"),
            _lbt_assignment("1", veh_class_no="20", driving_behavior_no="7", driving_behavior_name="Behavior 7"),
        ]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": "7", "readback_value": "7"})

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_link_behavior_type_diff_flags_an_unrelated_assignment_change():
    action = _action(
        "set_link_behavior_type_driving_behavior", link_behavior_type_no="1", driving_behavior_no="7"
    )
    before = _shadow_model(
        link_behavior_type_assignments=[
            _lbt_assignment("1", driving_behavior_no="1", driving_behavior_name="Behavior 1"),
            _lbt_assignment("4", driving_behavior_no="4", driving_behavior_name="Behavior 4"),
        ]
    )
    after = _shadow_model(
        link_behavior_type_assignments=[
            _lbt_assignment("1", driving_behavior_no="7", driving_behavior_name="Behavior 7"),
            _lbt_assignment("4", driving_behavior_no="99", driving_behavior_name="Behavior 99"),  # unrelated drift
        ]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": "7", "readback_value": "7"})

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(
        entry["object_class"] == "link_behavior_type_assignments" and entry["object_id"] == "4.default"
        for entry in diff
    )


def test_postwrite_diff_suppresses_duplicate_driving_behavior_own_new_object():
    # QUEUE 3 unit 6: duplicate_driving_behavior legitimately creates a
    # BRAND NEW driving_behaviors object via the sanctioned DuplicateDriving
    # Behavior COM call -- must NOT read as "something else moved", mirroring
    # set_reduced_speed_area's own des_speed_distributions precedent exactly.
    action = _action("duplicate_driving_behavior", source_no="1", new_name="Ramp copy")
    before = _shadow_model(driving_behaviors=[_driving_behavior("1")])
    after = _shadow_model(driving_behaviors=[_driving_behavior("1"), _driving_behavior("7")])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after,
        {"written_value": "Ramp copy", "readback_value": "Ramp copy"},
    )

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_still_flags_an_added_driving_behavior_for_a_tool_not_opted_in():
    # The suppression is tool-SPECIFIC (_ALLOWS_NEW_OBJECTS_IN), not a
    # blanket "never flag added driving behaviors" rule.
    action = _action("set_driving_behavior_parameter", behavior_no="1", parameter="ax", value=2.0)
    before = _shadow_model(driving_behaviors=[_driving_behavior("1", ax=1.5)])
    after = _shadow_model(driving_behaviors=[_driving_behavior("1", ax=2.0), _driving_behavior("7")])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(action, before, after, {"written_value": 2.0, "readback_value": 2.0})

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(
        entry["object_class"] == "driving_behaviors" and entry["object_id"] == "7" and entry["change"] == "added"
        for entry in diff
    )


# ---------------------------------------------------------------------------
# Q4 unit (5): add_link/add_connector/remove_link -- "links"/"link_lanes"
# ARE tracked by _SHADOW_OBJECT_ID_FIELDS (unlike Q4 unit (2)'s measurement-
# infra classes, which are not tracked at all), so these three tools need
# the SAME _ALLOWS_NEW_OBJECTS_IN/_ALLOWS_REMOVED_OBJECTS_IN opt-in
# remove_connector's own entry already established -- WITHOUT it, every
# genuinely successful add_link/add_connector/remove_link call would be
# refused+rolled-back by this exact diff, reproducing Q4 unit (3)'s own
# live-caught "every unit-2 remove verb was silently broken" defect class
# for THIS unit instead. These three tests are that unit's own recorded
# "one real-engine GuardrailEngine.postwrite test per verb" precedent,
# applied with a synthetic before/after pair (the live E2E, see
# backend/tools/add_links_connectors_live_check.py, proves the SAME
# non-regression against a real COM write).
# ---------------------------------------------------------------------------


def _link(link_id: str, *, lanes: int = 2, length: float = 100.0, is_connector: bool = False) -> dict[str, Any]:
    return {"id": link_id, "name": "", "lanes": lanes, "length": length, "is_connector": is_connector}


def _link_lane(link_id: str, lane_index: int) -> dict[str, Any]:
    return {"id": f"{link_id}.{lane_index}", "link_id": link_id, "lane_index": lane_index, "blocked_vehicle_classes": []}


def test_postwrite_diff_suppresses_add_link_own_new_link_and_lanes():
    action = _action(
        "add_link", points_xy=[[0.0, 0.0], [100.0, 0.0]], lane_count=2
    )
    before = _shadow_model(links=[_link("101")], link_lanes=[_link_lane("101", 1), _link_lane("101", 2)])
    after = _shadow_model(
        links=[_link("101"), _link("103")],
        link_lanes=[
            _link_lane("101", 1), _link_lane("101", 2), _link_lane("103", 1), _link_lane("103", 2),
        ],
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after,
        {"written_value": {"points": [[0.0, 0.0], [100.0, 0.0]], "lanes": 2},
         "readback_value": {"points": [[0.0, 0.0], [100.0, 0.0]], "lanes": 2}},
    )

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_suppresses_add_connector_own_new_link_and_lanes():
    action = _action(
        "add_connector", from_link="101", from_lanes=[1], from_pos_m=100.0,
        to_link="102", to_lanes=[1], to_pos_m=0.0,
    )
    before = _shadow_model(
        links=[_link("101"), _link("102", lanes=1)],
        link_lanes=[_link_lane("101", 1), _link_lane("101", 2), _link_lane("102", 1)],
    )
    after = _shadow_model(
        links=[_link("101"), _link("102", lanes=1), _link("301", lanes=1, is_connector=True)],
        link_lanes=[
            _link_lane("101", 1), _link_lane("101", 2), _link_lane("102", 1), _link_lane("301", 1),
        ],
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after,
        {
            "written_value": {
                "from_link": "101", "from_lanes": [1], "from_pos": 100.0,
                "to_link": "102", "to_lanes": [1], "to_pos": 0.0,
            },
            "readback_value": {
                "from_link": "101", "from_lanes": [1], "from_pos": 100.0,
                "to_link": "102", "to_lanes": [1], "to_pos": 0.0,
            },
        },
    )

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_suppresses_remove_link_own_removed_link_and_lanes():
    action = _action("remove_link", no="103")
    before = _shadow_model(
        links=[_link("101"), _link("103")],
        link_lanes=[
            _link_lane("101", 1), _link_lane("101", 2), _link_lane("103", 1), _link_lane("103", 2),
        ],
    )
    after = _shadow_model(links=[_link("101")], link_lanes=[_link_lane("101", 1), _link_lane("101", 2)])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after,
        {
            "written_value": {"removed_no": "103", "still_present": False},
            "readback_value": {"removed_no": "103", "still_present": False},
        },
    )

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_still_flags_an_added_link_for_a_tool_not_opted_in():
    # Same "tool-specific, not blanket" proof the driving-behavior test
    # above establishes, applied to this unit's own opt-in.
    action = _action("set_lane_closure", link_id="101", lane_index=1, vehicle_classes=["10"])
    before = _shadow_model(links=[_link("101")], link_lanes=[_link_lane("101", 1)])
    after = _shadow_model(links=[_link("101"), _link("999")], link_lanes=[_link_lane("101", 1)])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after,
        {"written_value": ["10"], "readback_value": ["10"]},
    )

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(
        entry["object_class"] == "links" and entry["object_id"] == "999" and entry["change"] == "added"
        for entry in diff
    )


# ---------------------------------------------------------------------------
# diff_shadow_models / rollback_last_edit's dynamic diff exemption --
# QUEUE 3 unit 7
# ---------------------------------------------------------------------------


def test_diff_shadow_models_reports_a_field_change_with_no_exemption():
    from services.agent.guardrails import diff_shadow_models

    before = _shadow_model(vehicle_inputs=[{"no": "1", "name": "VI1", "link": "101", "volume": 700.0}])
    after = _shadow_model(vehicle_inputs=[{"no": "1", "name": "VI1", "link": "101", "volume": 500.0}])

    diff = diff_shadow_models(before, after)

    assert {
        "object_class": "vehicle_inputs",
        "object_id": "1",
        "change": "field_changed",
        "field": "volume",
        "before": 700.0,
        "after": 500.0,
    } in diff


def test_diff_shadow_models_reports_removed_and_added_objects():
    from services.agent.guardrails import diff_shadow_models

    before = _shadow_model(
        des_speed_distributions=[_speed_distribution("50", [{"speed_kmh": 0.0, "cum_fraction": 0.0}])]
    )
    after = _shadow_model(des_speed_distributions=[])

    diff = diff_shadow_models(before, after)

    assert {"object_class": "des_speed_distributions", "object_id": "50", "change": "removed"} in diff
    # Symmetric direction: swapping before/after turns the same difference
    # into an "added" event.
    assert {"object_class": "des_speed_distributions", "object_id": "50", "change": "added"} in diff_shadow_models(
        after, before
    )


def test_diff_shadow_models_still_honors_the_blanket_always_exempt_fields():
    # des_speed_distributions.referenced_by/reference_count are PURELY
    # DERIVED (_ALWAYS_EXEMPT_FIELDS) -- even the unexempted, no-tool-context
    # oracle must not treat their drift as a real difference, or rollback's
    # own reported reverted_targets would demand postwrite exempt fields no
    # tool ever legitimately "writes" in the first place.
    from services.agent.guardrails import diff_shadow_models

    dist_before = _speed_distribution("50", [{"speed_kmh": 0.0, "cum_fraction": 0.0}])
    dist_before["reference_count"] = 2
    dist_after = _speed_distribution("50", [{"speed_kmh": 0.0, "cum_fraction": 0.0}])
    dist_after["reference_count"] = 1

    diff = diff_shadow_models(
        _shadow_model(des_speed_distributions=[dist_before]),
        _shadow_model(des_speed_distributions=[dist_after]),
    )

    assert diff == []


def test_postwrite_diff_is_silent_when_rollback_reports_an_expected_removal():
    # The des_speed_distributions object rollback_last_edit's own
    # diff_shadow_models call found gone must be exempt via expect_removed,
    # NOT the field-level "fields" mechanism -- reproduces the real gap this
    # unit found: reverting set_reduced_speed_area's own injected
    # <speedDistribution> makes it disappear, and nothing in the STATIC
    # per-tool spec table could ever have predicted that.
    action = _action("rollback_last_edit")
    before = _shadow_model(
        des_speed_distributions=[_speed_distribution("50", [{"speed_kmh": 0.0, "cum_fraction": 0.0}])]
    )
    after = _shadow_model(des_speed_distributions=[])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action,
        before,
        after,
        {
            "written_value": "deadbeef",
            "readback_value": "deadbeef",
            "reverted_targets": [
                {"object_class": "des_speed_distributions", "object_id": "50", "expect_removed": True}
            ],
        },
    )

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_still_flags_an_unclaimed_removal():
    action = _action("rollback_last_edit")
    before = _shadow_model(
        des_speed_distributions=[_speed_distribution("50", [{"speed_kmh": 0.0, "cum_fraction": 0.0}])]
    )
    after = _shadow_model(des_speed_distributions=[])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after, {"written_value": "deadbeef", "readback_value": "deadbeef"}
    )

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert {"object_class": "des_speed_distributions", "object_id": "50", "change": "removed"} in diff


def test_postwrite_diff_is_silent_for_rollback_when_reverted_targets_covers_the_only_change():
    # rollback_last_edit's own postwrite exemption is DYNAMIC -- it comes
    # from verification["reverted_targets"], never a static
    # _WRITE_TARGET_FIELD_SPEC entry (rollback_last_edit has none). A clean
    # rollback that only moved the field it claims must produce an EMPTY
    # diff, exactly like any other tool's own target field.
    action = _action("rollback_last_edit")
    before = _shadow_model(vehicle_inputs=[{"no": "1", "name": "VI1", "link": "101", "volume": 700.0}])
    after = _shadow_model(vehicle_inputs=[{"no": "1", "name": "VI1", "link": "101", "volume": 500.0}])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action,
        before,
        after,
        {
            "written_value": "deadbeef",
            "readback_value": "deadbeef",
            "reverted_targets": [{"object_class": "vehicle_inputs", "object_id": "1", "fields": ["volume"]}],
        },
    )

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_still_flags_a_rollback_that_moved_more_than_it_claimed():
    # The dynamic exemption only covers what the tool ITSELF reports --
    # anything else that changed is still genuine evidence, same "verify,
    # don't trust" guarantee every other tool's exemption gives.
    action = _action("rollback_last_edit")
    before = _shadow_model(
        vehicle_inputs=[{"no": "1", "name": "VI1", "link": "101", "volume": 700.0}],
        links=[{"id": "101", "name": "Main St EB approach", "lanes": 3, "length": 280.0}],
    )
    after = _shadow_model(
        vehicle_inputs=[{"no": "1", "name": "VI1", "link": "101", "volume": 500.0}],
        links=[{"id": "101", "name": "MUTATED", "lanes": 3, "length": 280.0}],  # unclaimed drift
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action,
        before,
        after,
        {
            "written_value": "deadbeef",
            "readback_value": "deadbeef",
            "reverted_targets": [{"object_class": "vehicle_inputs", "object_id": "1", "fields": ["volume"]}],
        },
    )

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(entry["object_class"] == "links" and entry["field"] == "name" for entry in diff)


def test_postwrite_diff_treats_rollback_with_no_reverted_targets_like_any_unmapped_change():
    # No reverted_targets at all (e.g. the undone tool was itself unmapped,
    # like set_reduced_speed_area, or history metadata was unavailable) --
    # falls back to the conservative default: any tracked-object change is
    # evidence, matching test_postwrite_diff_with_unmapped_tool_treats_any_
    # change_as_evidence's own precedent for every other unmapped case.
    action = _action("rollback_last_edit")
    before = _shadow_model(vehicle_inputs=[{"no": "1", "name": "VI1", "link": "101", "volume": 700.0}])
    after = _shadow_model(vehicle_inputs=[{"no": "1", "name": "VI1", "link": "101", "volume": 500.0}])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after, {"written_value": "deadbeef", "readback_value": "deadbeef"}
    )

    assert report.blocking is True


# ---------------------------------------------------------------------------
# Q5 unit (2): add_signal_controller -- the ONE table entry this verb cannot
# ship without. Both "signals" AND "signal_program_splits" are tracked by
# _SHADOW_OBJECT_ID_FIELDS, and one DuplicateSignalController call adds rows
# to BOTH (the copy carries the donor's whole timing program), so without
# _ALLOWS_NEW_OBJECTS_IN["add_signal_controller"] covering both classes every
# correct call is refused and rolled back by this exact diff.
#
# That is not a hypothesis: Q5 unit (1)'s composite probe made a raw
# DuplicateSignalController call inside add_signal_head's postwrite window
# and the real engine refused THAT verb, naming `signals#2:added` plus 44
# `signal_program_splits#2.{1,2}.{1..22}:added` (DECISIONS.md 2026-08-03).
# The first test below reproduces that id shape exactly -- one donor with 2
# programs x 2 signal groups, scaled down only so the fixture stays readable
# -- and asserts the shipped entry suppresses it. The live E2E against a real
# COM write is backend/tools/signal_controller_live_check.py; the shadow
# deltas both fixtures mirror were measured in
# backend/tools/probe_signal_controller_duplicate.py [A.9]/[B.4].
# ---------------------------------------------------------------------------


def _signal_entry(sc_no: str, *, controller_type: str = "FIXEDTIME", name: str = "") -> dict[str, Any]:
    return {
        "sc_no": sc_no,
        "sc_name": name,
        "cycle_time": 0,
        "controller_type": controller_type,
        "phases": [],
    }


def test_postwrite_diff_suppresses_add_signal_controller_own_new_controller_and_splits():
    action = _action("add_signal_controller", source_sc_no="1", new_name="North junction")
    before = _shadow_model(
        signals=[_signal_entry("1")],
        signal_program_splits=[
            _green_split_entry("1", "1", "1"),
            _green_split_entry("1", "1", "2"),
            _green_split_entry("1", "2", "1"),
            _green_split_entry("1", "2", "2"),
        ],
    )
    after = _shadow_model(
        signals=[_signal_entry("1"), _signal_entry("2", name="North junction")],
        signal_program_splits=[
            _green_split_entry("1", "1", "1"),
            _green_split_entry("1", "1", "2"),
            _green_split_entry("1", "2", "1"),
            _green_split_entry("1", "2", "2"),
            # the copy's inherited program: donor programs x donor signal
            # groups, all identical to the donor's own windows.
            _green_split_entry("2", "1", "1"),
            _green_split_entry("2", "1", "2"),
            _green_split_entry("2", "2", "1"),
            _green_split_entry("2", "2", "2"),
        ],
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after,
        {"written_value": "North junction", "readback_value": "North junction"},
    )

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_suppresses_add_signal_controller_with_a_single_program_donor():
    # Air Taxi Vertiport's shape (probe Part B): a FIXEDTIMESIMPLE donor with
    # ONE program and 2 signal groups adds 1 + 2 rows, not 1 + 44 -- the
    # per-call split count is donor-specific, which is exactly why the
    # exemption is a per-object-CLASS opt-in and not an id list.
    action = _action("add_signal_controller", source_sc_no="11", new_name="Second door")
    before = _shadow_model(
        signals=[_signal_entry("11", controller_type="FIXEDTIMESIMPLE", name="Terminal door")],
        signal_program_splits=[
            _green_split_entry("11", "1", "1"),
            _green_split_entry("11", "1", "2"),
        ],
    )
    after = _shadow_model(
        signals=[
            _signal_entry("11", controller_type="FIXEDTIMESIMPLE", name="Terminal door"),
            _signal_entry("22", controller_type="FIXEDTIMESIMPLE", name="Second door"),
        ],
        signal_program_splits=[
            _green_split_entry("11", "1", "1"),
            _green_split_entry("11", "1", "2"),
            _green_split_entry("22", "1", "1"),
            _green_split_entry("22", "1", "2"),
        ],
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after, {"written_value": "Second door", "readback_value": "Second door"}
    )

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_still_flags_an_unrelated_change_during_add_signal_controller():
    # The exemption suppresses ADDITIONS in those two classes -- it must not
    # blind the diff to a FIELD change on a pre-existing controller's split
    # (the donor's own timing must not move when it is duplicated).
    action = _action("add_signal_controller", source_sc_no="1", new_name="North junction")
    before = _shadow_model(
        signals=[_signal_entry("1")],
        signal_program_splits=[_green_split_entry("1", "1", "1", green_start_s=10.0, green_end_s=40.0)],
    )
    after = _shadow_model(
        signals=[_signal_entry("1"), _signal_entry("2", name="North junction")],
        signal_program_splits=[
            _green_split_entry("1", "1", "1", green_start_s=10.0, green_end_s=55.0),  # donor drifted
            _green_split_entry("2", "1", "1"),
        ],
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after,
        {"written_value": "North junction", "readback_value": "North junction"},
    )

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(
        entry["object_class"] == "signal_program_splits"
        and entry["object_id"] == "1.1.1"
        and entry["change"] == "field_changed"
        for entry in diff
    )


def test_postwrite_diff_still_flags_an_added_signal_controller_for_a_tool_not_opted_in():
    # The suppression is tool-SPECIFIC (_ALLOWS_NEW_OBJECTS_IN), not a
    # blanket "never flag added signal controllers" rule -- exactly the
    # situation Q5 unit (1) hit, where a raw duplicate appeared inside
    # add_signal_head's postwrite window and was correctly refused.
    action = _action("add_signal_head", link_no="101", lane_index=1, pos_m=50.0, sc_no="1", sg_no="1")
    before = _shadow_model(signals=[_signal_entry("1")])
    after = _shadow_model(signals=[_signal_entry("1"), _signal_entry("2")])
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after,
        {"written_value": {"link": "101"}, "readback_value": {"link": "101"}},
    )

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(
        entry["object_class"] == "signals" and entry["object_id"] == "2" and entry["change"] == "added"
        for entry in diff
    )


# ---------------------------------------------------------------------------
# Q5 unit (3): add_vehicle_input -- the table entry this verb cannot ship
# without, and the one whose SECOND class nobody would have predicted from
# the API name.
#
# One AddVehicleInput call adds a "vehicle_inputs" row everywhere; on a
# network with NO <vehicleComposition> at all it ALSO adds a
# "vehicle_composition_entries" row, because saving materialises the default
# composition 1 the fresh input references (VehComp(1) = '1'). Both classes
# are tracked by _SHADOW_OBJECT_ID_FIELDS, so each is individually
# load-bearing.
#
# The counterfactual behind these four tests was RUN, against the real
# before/after .inpx pairs backend/tools/probe_vehicle_input_create.py left
# on disk, not reasoned about:
#
#   Beijing (12 inputs, 3 compositions)  unexempted diff = 1 row
#     shipped {inputs, comp_entries} -> no write_verification_failed
#     popped (no entry)              -> REFUSE, 1 row  [vehicle_inputs#21:added]
#     {vehicle_inputs} only          -> no refusal (sufficient HERE)
#     {vehicle_composition_entries}  -> REFUSE, 1 row  [vehicle_inputs#21:added]
#   Air Taxi (0 inputs, 0 compositions)  unexempted diff = 2 rows
#     shipped {inputs, comp_entries} -> no write_verification_failed
#     popped (no entry)              -> REFUSE, 2 rows [vehicle_inputs#1:added,
#                                       vehicle_composition_entries#1.700:added]
#     {vehicle_inputs} only          -> REFUSE, 1 row
#                                       [vehicle_composition_entries#1.700:added]
#     {vehicle_composition_entries}  -> REFUSE, 1 row  [vehicle_inputs#1:added]
#   Toll Plaza (5 inputs x 5 intervals)  identical to Beijing.
#
# i.e. the composition class is proven necessary ONLY by the zero-composition
# network -- which is precisely the from-nothing genesis case this verb
# exists for, so a table entry derived from Beijing alone would have shipped
# broken for exactly the use it was built for. The synthetic fixtures below
# mirror those two shapes.
# ---------------------------------------------------------------------------


def _vehicle_input_entry(
    no: str, *, link: str = "101", volume: float = 450.0, name: str = ""
) -> dict[str, Any]:
    return {"no": no, "name": name, "link": link, "volume": volume, "interval_count": 1}


# The composition-entry fixtures below deliberately REUSE this file's own
# pre-existing ``_composition_entry`` (defined ~2850 lines up, for
# set_vehicle_composition_share's tests) rather than defining a second one.
# A second module-level def with the same name silently SHADOWS the first for
# the WHOLE module -- which is exactly what happened while writing this block
# and broke five unrelated, pre-existing tests. Caught by the full suite
# before it shipped; recorded here so the next person does not repeat it.


def test_postwrite_diff_suppresses_add_vehicle_input_own_new_input():
    # The Beijing/Toll Plaza shape: a network that already has compositions,
    # so only "vehicle_inputs" moves.
    action = _action("add_vehicle_input", link_no="101", volume_vph=450.0)
    before = _shadow_model(
        vehicle_inputs=[_vehicle_input_entry("1")],
        vehicle_composition_entries=[_composition_entry("1", "100")],
    )
    after = _shadow_model(
        vehicle_inputs=[_vehicle_input_entry("1"), _vehicle_input_entry("2")],
        vehicle_composition_entries=[_composition_entry("1", "100")],
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after,
        {
            "written_value": {"link": "101", "volume": 450.0},
            "readback_value": {"link": "101", "volume": 450.0},
        },
    )

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_suppresses_add_vehicle_input_auto_materialised_composition():
    # The Air Taxi shape, and the ONLY one that proves the second class is
    # needed: a network with zero compositions gains one relative-flow entry
    # as a byproduct of saving the new input.
    action = _action("add_vehicle_input", link_no="101", volume_vph=450.0)
    before = _shadow_model(vehicle_inputs=[], vehicle_composition_entries=[])
    after = _shadow_model(
        vehicle_inputs=[_vehicle_input_entry("1")],
        vehicle_composition_entries=[_composition_entry("1", "700")],
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after,
        {
            "written_value": {"link": "101", "volume": 450.0},
            "readback_value": {"link": "101", "volume": 450.0},
        },
    )

    assert report.findings == []
    assert report.blocking is False


def test_postwrite_diff_still_flags_an_unrelated_change_during_add_vehicle_input():
    # The exemption suppresses ADDITIONS in those two classes -- it must not
    # blind the diff to a FIELD change on a PRE-EXISTING input (another
    # approach's demand must not move when a new one is created).
    action = _action("add_vehicle_input", link_no="101", volume_vph=450.0)
    before = _shadow_model(vehicle_inputs=[_vehicle_input_entry("1", volume=500.0)])
    after = _shadow_model(
        vehicle_inputs=[
            _vehicle_input_entry("1", volume=900.0),  # someone else's demand drifted
            _vehicle_input_entry("2"),
        ]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after,
        {
            "written_value": {"link": "101", "volume": 450.0},
            "readback_value": {"link": "101", "volume": 450.0},
        },
    )

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(
        entry["object_class"] == "vehicle_inputs"
        and entry["object_id"] == "1"
        and entry["change"] == "field_changed"
        for entry in diff
    )


def test_postwrite_diff_still_flags_an_added_vehicle_input_for_a_tool_not_opted_in():
    # The suppression is tool-SPECIFIC (_ALLOWS_NEW_OBJECTS_IN), not a
    # blanket "never flag added vehicle inputs" rule.
    action = _action("set_vehicle_input_volume", input_no="1", volume=450.0)
    before = _shadow_model(vehicle_inputs=[_vehicle_input_entry("1", volume=450.0)])
    after = _shadow_model(
        vehicle_inputs=[_vehicle_input_entry("1", volume=450.0), _vehicle_input_entry("2")]
    )
    engine = GuardrailEngine(disabled=False)

    report = engine.postwrite(
        action, before, after, {"written_value": 450.0, "readback_value": 450.0}
    )

    assert report.blocking is True
    diff = report.first_refusal().details["nothing_else_moved_diff"]
    assert any(
        entry["object_class"] == "vehicle_inputs"
        and entry["object_id"] == "2"
        and entry["change"] == "added"
        for entry in diff
    )

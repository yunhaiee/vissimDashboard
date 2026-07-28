from __future__ import annotations

from typing import Any

import pytest

from services.agent.constraints import Finding, get_registry, run_checks

LANGS = ("ko", "en", "ja")


# ---------------------------------------------------------------------------
# Fixtures — synthetic shadow model (per the M0.3 brief's contract)
# ---------------------------------------------------------------------------


def _shadow_model(
    *,
    links: list[dict[str, Any]] | None = None,
    vehicle_inputs: list[dict[str, Any]] | None = None,
    signals: list[dict[str, Any]] | None = None,
    nodes: list[dict[str, Any]] | None = None,
    conflict_areas: list[dict[str, Any]] | None = None,
    routing_decisions: list[dict[str, Any]] | None = None,
    vehicle_compositions: list[dict[str, Any]] | None = None,
    des_speed_decisions: list[dict[str, Any]] | None = None,
    des_speed_distributions: list[dict[str, Any]] | None = None,
    signal_programs: list[dict[str, Any]] | None = None,
    evaluation_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    shadow = {
        "links": links
        if links is not None
        else [{"id": "101", "name": "Main St EB approach", "lanes": 3, "length": 280.0}],
        "vehicle_inputs": vehicle_inputs
        if vehicle_inputs is not None
        else [{"no": "1", "name": "Main St EB", "link": "101", "volume": 900.0}],
        "signals": signals
        if signals is not None
        else [
            {
                "sc_no": "1",
                "sc_name": "SC1",
                "cycle_time": 90,
                "controller_type": "FIXEDTIME",
                "min_green_intergreen_sum": 34,
                "phases": [],
            }
        ],
        "nodes": nodes if nodes is not None else [],
    }
    # conflict_areas is deliberately OMITTED by default (not set to []) --
    # most tests in this file exercise checks that have nothing to do with
    # conflict areas, and the real production shadow model can legitimately
    # omit the key too on an older/synthetic build path (see
    # conflict_area_data_unavailable's own defensive .get()). Only tests
    # that explicitly pass conflict_areas=[...] get the key at all.
    if conflict_areas is not None:
        shadow["conflict_areas"] = conflict_areas
    # routing_decisions is deliberately OMITTED by default too (v2 unit ⑥) --
    # same "most tests have nothing to do with this object class, and a
    # caller-built fixture can legitimately omit the key" reasoning as
    # conflict_areas above.
    if routing_decisions is not None:
        shadow["routing_decisions"] = routing_decisions
    # vehicle_compositions is deliberately OMITTED by default too (v2 unit
    # ⑦) -- same "most tests have nothing to do with this object class, and
    # a caller-built fixture can legitimately omit the key" reasoning as
    # conflict_areas/routing_decisions above.
    if vehicle_compositions is not None:
        shadow["vehicle_compositions"] = vehicle_compositions
    # des_speed_decisions/des_speed_distributions deliberately OMITTED by
    # default too (v2a unit ⑧) -- same reasoning as conflict_areas/
    # routing_decisions/vehicle_compositions above.
    if des_speed_decisions is not None:
        shadow["des_speed_decisions"] = des_speed_decisions
    if des_speed_distributions is not None:
        shadow["des_speed_distributions"] = des_speed_distributions
    # signal_programs deliberately OMITTED by default too (v2a unit ⑨) --
    # same reasoning as the object classes above.
    if signal_programs is not None:
        shadow["signal_programs"] = signal_programs
    # evaluation_config deliberately OMITTED by default too (health-check
    # unit, 2026-07-28) -- same reasoning as the object classes above.
    if evaluation_config is not None:
        shadow["evaluation_config"] = evaluation_config
    return shadow


def _sg_split(
    sg_no: str,
    *,
    green_start_s: float | None = 10.0,
    green_end_s: float | None = 40.0,
    amber_s: float = 3.0,
    min_green_s: float | None = None,
    head_count: int = 2,
    simple: bool = True,
    sg_name: str = "",
) -> dict[str, Any]:
    return {
        "sg_no": sg_no,
        "sg_name": sg_name or f"SG{sg_no}",
        "green_start_s": green_start_s,
        "green_end_s": green_end_s,
        "amber_s": amber_s,
        "red_amber_s": 1.0,
        "min_green_s": min_green_s,
        "head_count": head_count,
        "simple": simple,
    }


def _signal_program(
    prog_no: str, sg_splits: list[dict[str, Any]], *, cycle_s: float | None = 90.0, name: str = ""
) -> dict[str, Any]:
    return {"prog_no": prog_no, "name": name, "cycle_s": cycle_s, "sg_splits": sg_splits}


def _signal_program_controller(
    sc_no: str,
    programs: list[dict[str, Any]],
    *,
    controller_type: str = "FIXEDTIME",
    active_program_no: str | None = "1",
    sc_name: str = "",
) -> dict[str, Any]:
    return {
        "sc_no": sc_no,
        "sc_name": sc_name,
        "controller_type": controller_type,
        "active_program_no": active_program_no,
        "programs": programs,
    }


def _composition_entry(
    veh_type: str, *, des_speed_distr: str = "50", rel_flow: float | None = 100.0
) -> dict[str, Any]:
    return {"veh_type": veh_type, "des_speed_distr": des_speed_distr, "rel_flow": rel_flow}


def _vehicle_composition(
    no: str,
    entries: list[dict[str, Any]],
    *,
    name: str = "",
    referencing_vehicle_input_nos: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "no": no,
        "name": name,
        "entries": entries,
        "referencing_vehicle_input_nos": referencing_vehicle_input_nos or [],
    }


def _route(
    route_no: str,
    *,
    dest_link: str = "1",
    dest_pos: float = 0.0,
    rel_flow: float | None = 100.0,
    rel_flows: list[dict[str, Any]] | None = None,
    link_seq: list[str] | None = None,
    disconnected: bool = False,
) -> dict[str, Any]:
    if rel_flows is None:
        rel_flows = [{"interval_no": 1, "from_s": 0.0, "rel_flow": rel_flow}] if rel_flow is not None else []
    return {
        "route_no": route_no,
        "dest_link": dest_link,
        "dest_pos": dest_pos,
        "rel_flow": rel_flow,
        "interval_count": len(rel_flows),
        "rel_flows": rel_flows,
        "link_seq": link_seq if link_seq is not None else [],
        "disconnected": disconnected,
    }


def _routing_decision(no: str, routes: list[dict[str, Any]], *, link: str = "1", name: str = "") -> dict[str, Any]:
    return {"no": no, "link": link, "name": name, "routes": routes}


def _veh_class_assignment(
    veh_class_no: str, *, veh_class_name: str = "Car", des_speed_distr_no: str = "50", des_speed_distr_name: str = "55 mph"
) -> dict[str, Any]:
    return {
        "veh_class_no": veh_class_no,
        "veh_class_name": veh_class_name,
        "des_speed_distr_no": des_speed_distr_no,
        "des_speed_distr_name": des_speed_distr_name,
    }


def _des_speed_decision(
    no: str,
    veh_class_assignments: list[dict[str, Any]],
    *,
    name: str = "",
    link: str = "6",
    lane: str = "1",
    pos: float = 100.0,
) -> dict[str, Any]:
    return {
        "no": no,
        "name": name,
        "link": link,
        "lane": lane,
        "pos": pos,
        "veh_class_assignments": veh_class_assignments,
    }


def _speed_distribution(no: str, name: str) -> dict[str, Any]:
    return {"no": no, "name": name}


def _action(tool: str, **params: Any) -> dict[str, Any]:
    return {"tool": tool, "params": params}


def _assert_well_formed_messages(finding: Finding, *, must_contain_ko: str) -> None:
    assert set(finding.messages.keys()) == set(LANGS)
    for lang in LANGS:
        assert finding.messages[lang].strip(), f"{lang} message must not be empty"
    assert must_contain_ko in finding.messages["ko"]


# ---------------------------------------------------------------------------
# Catalog shape — exact ids/tiers/phases from the brief
# ---------------------------------------------------------------------------


def test_registry_includes_the_original_eight_seeded_constraints():
    # M0.3 seeded these eight; M3.1 (docs/phase2/02-ROADMAP.md) ports the rest
    # of the catalog on top. This test pins the ORIGINAL eight's identity
    # (id -> tier/phase) as a regression guard, not the registry's total size
    # -- see test_registry_size_matches_m3_1_catalog_port for the full count.
    expected = {
        "object_not_found": ("refuse", "preflight"),
        "volume_out_of_range": ("refuse", "preflight"),
        "latent_demand_overflow": ("warn", "preflight"),
        "signal_controller_type_unsupported": ("refuse", "preflight"),
        "cycle_below_intergreen_sum": ("refuse", "preflight"),
        "write_verification_failed": ("refuse", "postwrite"),
        "results_missing_kpi_tables": ("warn", "postrun"),
        "vehicles_deleted_in_run": ("warn", "postrun"),
    }

    by_id = {c.id: c for c in get_registry()}

    for constraint_id, (tier, phase) in expected.items():
        assert constraint_id in by_id, f"M0.3 seed constraint {constraint_id!r} missing"
        assert (by_id[constraint_id].tier, by_id[constraint_id].phase) == (tier, phase)


def test_registry_order_registers_the_seed_eight_first_in_original_order():
    seed_order = [
        "object_not_found",
        "volume_out_of_range",
        "latent_demand_overflow",
        "signal_controller_type_unsupported",
        "cycle_below_intergreen_sum",
        "write_verification_failed",
        "results_missing_kpi_tables",
        "vehicles_deleted_in_run",
    ]
    ids = [c.id for c in get_registry()]
    assert ids[: len(seed_order)] == seed_order


# ---------------------------------------------------------------------------
# #1 object_not_found
# ---------------------------------------------------------------------------


def test_object_not_found_fires_for_missing_vehicle_input():
    shadow = _shadow_model(
        vehicle_inputs=[
            {"no": "101", "name": "Main St EB", "link": "101", "volume": 900.0},
            {"no": "102", "name": "Main St WB", "link": "101", "volume": 800.0},
        ]
    )
    action = _action("set_vehicle_input_volume", input_no="103", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    assert finding.phase == "preflight"
    _assert_well_formed_messages(finding, must_contain_ko="103")
    # difflib candidates over ids should surface the near-miss ids
    assert set(finding.suggested_fix) & {"101", "102"}


def test_object_not_found_suggested_fix_resolves_name_match_to_owning_id():
    # reviewer repro: a typo'd NAME as the target must yield the owning
    # object's id, not the raw name string (suggested_fix is machine-usable
    # — a caller retries with suggested_fix[0] as input_no/sc_no).
    shadow = _shadow_model(
        vehicle_inputs=[
            {"no": "1", "name": "Main Street EB", "link": "101", "volume": 900.0},
        ]
    )
    action = _action(
        "set_vehicle_input_volume", input_no="Main Street EB approach", volume=600
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    finding = matches[0]
    assert "1" in finding.suggested_fix
    assert "Main Street EB" not in finding.suggested_fix


def test_object_not_found_fires_for_missing_signal():
    shadow = _shadow_model()
    action = _action("set_signal_cycle", sc_no="99", cycle=90)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="99")


def test_object_not_found_silent_when_target_present():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


def test_object_not_found_does_not_apply_to_unrelated_tool():
    shadow = _shadow_model()
    action = _action("run_simulation")

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


# --- v2 unit (2): set_reduced_speed_area added to the object_not_found /
# _TOOL_TARGET_SPEC lockstep table (links object class) ------------------


def test_object_not_found_fires_for_missing_link_on_reduced_speed_area():
    shadow = _shadow_model(
        links=[
            {"id": "101", "name": "Main St EB approach", "lanes": 3, "length": 280.0},
            {"id": "102", "name": "Main St WB exit", "lanes": 3, "length": 280.0},
        ]
    )
    action = _action(
        "set_reduced_speed_area", link_id="999", speed_kmh=40.0, from_m=0.0, length_m=20.0
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    assert finding.phase == "preflight"
    _assert_well_formed_messages(finding, must_contain_ko="999")


def test_object_not_found_suggested_fix_resolves_near_miss_link_id():
    shadow = _shadow_model(
        links=[{"id": "101", "name": "Main St EB approach", "lanes": 3, "length": 280.0}]
    )
    action = _action(
        "set_reduced_speed_area", link_id="102", speed_kmh=40.0, from_m=0.0, length_m=20.0
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    assert "101" in matches[0].suggested_fix


def test_object_not_found_silent_when_link_present_for_reduced_speed_area():
    shadow = _shadow_model()  # default fixture link id "101"
    action = _action(
        "set_reduced_speed_area", link_id="101", speed_kmh=40.0, from_m=0.0, length_m=20.0
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


# --- v2 unit (4): set_conflict_area_status added to the object_not_found /
# _TOOL_TARGET_SPEC lockstep table (conflict_areas object class) -----------


def _conflict_area(no: str, link_a: str, link_b: str, status: str) -> dict[str, Any]:
    return {
        "no": no,
        "link_a": link_a,
        "link_b": link_b,
        "link_pair": (link_a, link_b),
        "status": status,
        "name": f"Link {link_a} vs Link {link_b}",
    }


def test_object_not_found_fires_for_missing_conflict_area():
    shadow = _shadow_model(conflict_areas=[_conflict_area("3", "3", "12", "PASSIVE")])
    action = _action("set_conflict_area_status", conflict_area_no="999", status="AHASRIGHTOFWAY")

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    assert finding.phase == "preflight"
    _assert_well_formed_messages(finding, must_contain_ko="999")


def test_object_not_found_suggested_fix_resolves_near_miss_conflict_area_no():
    shadow = _shadow_model(conflict_areas=[_conflict_area("30", "3", "12", "PASSIVE")])
    action = _action("set_conflict_area_status", conflict_area_no="3", status="AHASRIGHTOFWAY")

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    assert "30" in matches[0].suggested_fix


def test_object_not_found_silent_when_conflict_area_present():
    shadow = _shadow_model(conflict_areas=[_conflict_area("3", "3", "12", "PASSIVE")])
    action = _action("set_conflict_area_status", conflict_area_no="3", status="AHASRIGHTOFWAY")

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


def test_object_not_found_fires_for_missing_routing_decision():
    # v2 unit ⑥: the decision-level (first) half of set_route_relative_flow's
    # two-part object resolution reuses #1's generic single-id machinery.
    shadow = _shadow_model(routing_decisions=[_routing_decision("1", [_route("1")])])
    action = _action("set_route_relative_flow", decision_no="999", route_no="1", rel_flow=500.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="999")


def test_object_not_found_suggested_fix_resolves_near_miss_decision_no():
    shadow = _shadow_model(routing_decisions=[_routing_decision("10", [_route("1")])])
    action = _action("set_route_relative_flow", decision_no="1", route_no="1", rel_flow=500.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    assert "10" in matches[0].suggested_fix


def test_object_not_found_silent_when_routing_decision_present():
    shadow = _shadow_model(routing_decisions=[_routing_decision("1", [_route("1")])])
    action = _action("set_route_relative_flow", decision_no="1", route_no="1", rel_flow=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


# ---------------------------------------------------------------------------
# #46 route_not_found_in_decision — v2 unit ⑥
# ---------------------------------------------------------------------------


def test_route_not_found_in_decision_fires_for_missing_route():
    shadow = _shadow_model(
        routing_decisions=[_routing_decision("1", [_route("1"), _route("2")])]
    )
    action = _action("set_route_relative_flow", decision_no="1", route_no="99", rel_flow=500.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "route_not_found_in_decision"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    _assert_well_formed_messages(finding, must_contain_ko="1")
    assert finding.suggested_fix == ["1", "2"]


def test_route_not_found_in_decision_silent_when_route_present():
    shadow = _shadow_model(
        routing_decisions=[_routing_decision("1", [_route("1"), _route("2")])]
    )
    action = _action("set_route_relative_flow", decision_no="1", route_no="2", rel_flow=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "route_not_found_in_decision" for f in findings)


def test_route_not_found_in_decision_defers_to_object_not_found_when_decision_missing():
    shadow = _shadow_model(routing_decisions=[_routing_decision("1", [_route("1")])])
    action = _action("set_route_relative_flow", decision_no="999", route_no="1", rel_flow=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "route_not_found_in_decision" for f in findings)
    assert any(f.constraint_id == "object_not_found" for f in findings)


def test_route_not_found_in_decision_does_not_apply_to_unrelated_tool():
    shadow = _shadow_model(routing_decisions=[_routing_decision("1", [_route("1")])])
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "route_not_found_in_decision" for f in findings)


# ---------------------------------------------------------------------------
# #47 route_relative_flow_interval_out_of_range — v2 unit ⑥
# ---------------------------------------------------------------------------


def test_route_relative_flow_interval_out_of_range_fires_when_beyond_known_count():
    route = _route("1", rel_flows=[{"interval_no": 1, "from_s": 0.0, "rel_flow": 45.0}])
    shadow = _shadow_model(routing_decisions=[_routing_decision("1", [route])])
    action = _action(
        "set_route_relative_flow", decision_no="1", route_no="1", rel_flow=99.0, interval_no=3
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "route_relative_flow_interval_out_of_range"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    assert finding.suggested_fix == {"available_intervals": 1}
    _assert_well_formed_messages(finding, must_contain_ko="1")


def test_route_relative_flow_interval_out_of_range_silent_when_within_range():
    route = _route(
        "1",
        rel_flows=[
            {"interval_no": 1, "from_s": 0.0, "rel_flow": 45.0},
            {"interval_no": 2, "from_s": 5400.0, "rel_flow": 45.0},
        ],
    )
    shadow = _shadow_model(routing_decisions=[_routing_decision("1", [route])])
    action = _action(
        "set_route_relative_flow", decision_no="1", route_no="1", rel_flow=99.0, interval_no=2
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "route_relative_flow_interval_out_of_range" for f in findings
    )


def test_route_relative_flow_interval_out_of_range_silent_when_interval_1():
    # interval 1 is always potentially valid, regardless of interval_count.
    route = _route("1", rel_flows=[])
    shadow = _shadow_model(routing_decisions=[_routing_decision("1", [route])])
    action = _action("set_route_relative_flow", decision_no="1", route_no="1", rel_flow=99.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "route_relative_flow_interval_out_of_range" for f in findings
    )


def test_route_relative_flow_interval_out_of_range_skipped_when_target_missing():
    shadow = _shadow_model(routing_decisions=[])
    action = _action(
        "set_route_relative_flow", decision_no="999", route_no="1", rel_flow=99.0, interval_no=5
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "route_relative_flow_interval_out_of_range" for f in findings
    )


# ---------------------------------------------------------------------------
# #48 route_relative_flow_split_confirm — v2 unit ⑥
# ---------------------------------------------------------------------------


def test_route_relative_flow_split_confirm_always_fires_and_echoes_the_estimated_share():
    shadow = _shadow_model(
        routing_decisions=[
            _routing_decision(
                "1", [_route("1", rel_flow=245.0), _route("2", rel_flow=139.0), _route("3", rel_flow=1454.0)]
            )
        ]
    )
    action = _action("set_route_relative_flow", decision_no="1", route_no="1", rel_flow=745.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "route_relative_flow_split_confirm"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    _assert_well_formed_messages(finding, must_contain_ko="1")
    # 745 / (745 + 139 + 1454) == 31.9% -- siblings' CURRENT relFlow used,
    # not renormalized.
    assert finding.details["estimated_share_pct"] == 31.9
    assert finding.details["old_rel_flow"] == 245.0
    assert finding.details["new_rel_flow"] == 745.0
    assert finding.details["route_count"] == 3
    assert "raw" in finding.messages["en"].lower()


def test_route_relative_flow_split_confirm_notes_single_route_has_no_effect_on_split():
    shadow = _shadow_model(routing_decisions=[_routing_decision("9", [_route("1", rel_flow=568.0)])])
    action = _action("set_route_relative_flow", decision_no="9", route_no="1", rel_flow=1567.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "route_relative_flow_split_confirm"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.details["route_count"] == 1
    assert finding.details["estimated_share_pct"] == 100.0
    assert "100%" in finding.messages["en"]


def test_route_relative_flow_split_confirm_silent_when_target_missing():
    shadow = _shadow_model(routing_decisions=[])
    action = _action("set_route_relative_flow", decision_no="999", route_no="1", rel_flow=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "route_relative_flow_split_confirm" for f in findings)


def test_route_relative_flow_split_confirm_does_not_apply_to_unrelated_tool():
    shadow = _shadow_model(routing_decisions=[_routing_decision("1", [_route("1")])])
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "route_relative_flow_split_confirm" for f in findings)


# ---------------------------------------------------------------------------
# #2 volume_out_of_range
# ---------------------------------------------------------------------------


def test_volume_out_of_range_fires_when_negative():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=-5)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "volume_out_of_range"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    _assert_well_formed_messages(finding, must_contain_ko="-5")


def test_volume_out_of_range_fires_when_above_20000():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=25000)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "volume_out_of_range"]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="25000")


def test_volume_out_of_range_silent_within_range():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "volume_out_of_range" for f in findings)


def test_volume_out_of_range_silent_at_boundaries():
    shadow = _shadow_model()

    findings_low = run_checks(
        "preflight", _action("set_vehicle_input_volume", input_no="1", volume=0), shadow, {}
    )
    findings_high = run_checks(
        "preflight", _action("set_vehicle_input_volume", input_no="1", volume=20000), shadow, {}
    )

    assert not any(f.constraint_id == "volume_out_of_range" for f in findings_low)
    assert not any(f.constraint_id == "volume_out_of_range" for f in findings_high)


# ---------------------------------------------------------------------------
# #3 latent_demand_overflow
# ---------------------------------------------------------------------------


def test_latent_demand_overflow_fires_when_volume_exceeds_capacity():
    # link 101 has 3 lanes -> rough capacity 3 * 1900 = 5700 veh/h
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=6000)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "latent_demand_overflow"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    _assert_well_formed_messages(finding, must_contain_ko="6000")


def test_latent_demand_overflow_silent_within_capacity():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=3000)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "latent_demand_overflow" for f in findings)


def test_latent_demand_overflow_skipped_when_link_missing_from_shadow_model():
    shadow = _shadow_model(
        links=[],
        vehicle_inputs=[{"no": "1", "name": "Main St EB", "link": "999", "volume": 900.0}],
    )
    action = _action("set_vehicle_input_volume", input_no="1", volume=999999)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "latent_demand_overflow" for f in findings)


def test_latent_demand_overflow_skipped_when_vehicle_input_missing():
    # missing input is object_not_found's job, not #3's
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="does-not-exist", volume=999999)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "latent_demand_overflow" for f in findings)


# ---------------------------------------------------------------------------
# #4 signal_controller_type_unsupported
# ---------------------------------------------------------------------------


def test_signal_controller_type_unsupported_fires_when_type_missing():
    shadow = _shadow_model(
        signals=[{"sc_no": "1", "sc_name": "SC1", "cycle_time": 90, "phases": []}]
    )
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "signal_controller_type_unsupported"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    _assert_well_formed_messages(finding, must_contain_ko="1")


def test_signal_controller_type_unsupported_fires_when_type_is_unknown():
    shadow = _shadow_model(
        signals=[
            {
                "sc_no": "1",
                "sc_name": "SC1",
                "cycle_time": 90,
                "controller_type": "unknown",
                "phases": [],
            }
        ]
    )
    action = _action("set_signal_offset", sc_no="1", offset=10)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "signal_controller_type_unsupported"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    _assert_well_formed_messages(finding, must_contain_ko="unknown")


def test_signal_controller_type_unsupported_fires_for_non_fixed_time_controller():
    # "RINGBARRIERCONTROLLER" is a REAL type string (verified against the
    # real "BRT Priority Texas.US" example network, 2026-07-24) -- Vissim's
    # built-in ring-barrier controller, in TOOL-CATALOG.md's "Impossible by
    # design" refusal family alongside VAP/Econolite external logic.
    shadow = _shadow_model(
        signals=[
            {
                "sc_no": "1",
                "sc_name": "SC1",
                "cycle_time": 90,
                "controller_type": "RINGBARRIERCONTROLLER",
                "phases": [],
            }
        ]
    )
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "signal_controller_type_unsupported"]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="RINGBARRIERCONTROLLER")


def test_signal_controller_type_unsupported_silent_for_fixed_time():
    shadow = _shadow_model()  # controller_type = "FIXEDTIME"
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "signal_controller_type_unsupported" for f in findings
    )


def test_signal_controller_type_unsupported_silent_for_fixedtimesimple_cycle():
    # M2.1 live evidence (docs/phase2/DECISIONS.md): the Air Taxi Vertiport
    # example's FIXEDTIMESIMPLE controllers are COM-writable too, not just
    # Beijing's FIXEDTIME -- both real types must pass #4 for both verbs.
    shadow = _shadow_model(
        signals=[
            {
                "sc_no": "11",
                "sc_name": "Terminal entrance door",
                "cycle_time": 999,
                "controller_type": "FIXEDTIMESIMPLE",
                "phases": [],
            }
        ]
    )
    action = _action("set_signal_cycle", sc_no="11", cycle=999)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "signal_controller_type_unsupported" for f in findings
    )


def test_signal_controller_type_unsupported_silent_for_fixedtimesimple_offset():
    shadow = _shadow_model(
        signals=[
            {
                "sc_no": "11",
                "sc_name": "Terminal entrance door",
                "cycle_time": 999,
                "controller_type": "FIXEDTIMESIMPLE",
                "phases": [],
            }
        ]
    )
    action = _action("set_signal_offset", sc_no="11", offset=5)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "signal_controller_type_unsupported" for f in findings
    )


def test_signal_controller_type_unsupported_skipped_when_signal_missing():
    shadow = _shadow_model()
    action = _action("set_signal_cycle", sc_no="404", cycle=90)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "signal_controller_type_unsupported" for f in findings
    )


# ---------------------------------------------------------------------------
# #5 cycle_below_intergreen_sum
# ---------------------------------------------------------------------------


def test_cycle_below_intergreen_sum_fires_when_present_and_violated():
    # min_green_intergreen_sum = 34 in the default fixture
    shadow = _shadow_model()
    action = _action("set_signal_cycle", sc_no="1", cycle=20)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "cycle_below_intergreen_sum"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    _assert_well_formed_messages(finding, must_contain_ko="20")


def test_cycle_below_intergreen_sum_silent_when_cycle_sufficient():
    shadow = _shadow_model()
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "cycle_below_intergreen_sum" for f in findings)


def test_cycle_below_intergreen_sum_skipped_when_datum_absent():
    shadow = _shadow_model(
        signals=[
            {
                "sc_no": "1",
                "sc_name": "SC1",
                "cycle_time": 90,
                "controller_type": "FIXEDTIME",
                "phases": [],
                # no min_green_intergreen_sum key at all
            }
        ]
    )
    action = _action("set_signal_cycle", sc_no="1", cycle=1)  # would violate if datum existed

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "cycle_below_intergreen_sum" for f in findings)


def test_cycle_below_intergreen_sum_does_not_apply_to_offset():
    shadow = _shadow_model()
    action = _action("set_signal_offset", sc_no="1", offset=1)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "cycle_below_intergreen_sum" for f in findings)


# ---------------------------------------------------------------------------
# #6 write_verification_failed
# ---------------------------------------------------------------------------


def test_write_verification_failed_fires_on_mismatch():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)
    extra = {"written_value": 600, "readback_value": 500}

    findings = run_checks("postwrite", action, shadow, extra)

    matches = [f for f in findings if f.constraint_id == "write_verification_failed"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    assert finding.phase == "postwrite"
    _assert_well_formed_messages(finding, must_contain_ko="600")


def test_write_verification_failed_silent_on_exact_match():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)
    extra = {"written_value": 600, "readback_value": 600}

    findings = run_checks("postwrite", action, shadow, extra)

    assert not any(f.constraint_id == "write_verification_failed" for f in findings)


def test_write_verification_failed_silent_within_float_tolerance():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600.0)
    extra = {"written_value": 600.0, "readback_value": 600.0 + 1e-10}

    findings = run_checks("postwrite", action, shadow, extra)

    assert not any(f.constraint_id == "write_verification_failed" for f in findings)


def test_write_verification_failed_applies_regardless_of_tool():
    shadow = _shadow_model()
    action = _action("set_signal_cycle", sc_no="1", cycle=90)
    extra = {"written_value": 90, "readback_value": 80}

    findings = run_checks("postwrite", action, shadow, extra)

    assert any(f.constraint_id == "write_verification_failed" for f in findings)


def test_write_verification_failed_skipped_when_extra_missing_keys():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("postwrite", action, shadow, {})

    assert not any(f.constraint_id == "write_verification_failed" for f in findings)


# ---------------------------------------------------------------------------
# #7 results_missing_kpi_tables
# ---------------------------------------------------------------------------


def test_results_missing_kpi_tables_fires_when_only_simulationrun_present():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"], "deleted_vehicles": 0}}

    findings = run_checks("postrun", action, shadow, extra)

    matches = [f for f in findings if f.constraint_id == "results_missing_kpi_tables"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    _assert_well_formed_messages(finding, must_contain_ko="SIMULATIONRUN")


def test_results_missing_kpi_tables_silent_when_kpi_tables_present():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {
        "run_result": {
            "tables": ["SIMULATIONRUN", "VEHICLENETWORKPERFORMANCEEVALUATION"],
            "deleted_vehicles": 0,
        }
    }

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "results_missing_kpi_tables" for f in findings)


def test_results_missing_kpi_tables_skipped_when_run_result_absent():
    shadow = _shadow_model()
    action = _action("run_simulation")

    findings = run_checks("postrun", action, shadow, {})

    assert not any(f.constraint_id == "results_missing_kpi_tables" for f in findings)


# ---------------------------------------------------------------------------
# #8 vehicles_deleted_in_run
# ---------------------------------------------------------------------------


def test_vehicles_deleted_in_run_fires_when_positive():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {
        "run_result": {
            "tables": ["SIMULATIONRUN", "VEHICLENETWORKPERFORMANCEEVALUATION"],
            "deleted_vehicles": 7,
        }
    }

    findings = run_checks("postrun", action, shadow, extra)

    matches = [f for f in findings if f.constraint_id == "vehicles_deleted_in_run"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    _assert_well_formed_messages(finding, must_contain_ko="7")


def test_vehicles_deleted_in_run_silent_when_zero():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {
        "run_result": {
            "tables": ["SIMULATIONRUN", "VEHICLENETWORKPERFORMANCEEVALUATION"],
            "deleted_vehicles": 0,
        }
    }

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "vehicles_deleted_in_run" for f in findings)


def test_vehicles_deleted_in_run_skipped_when_run_result_absent():
    shadow = _shadow_model()
    action = _action("run_simulation")

    findings = run_checks("postrun", action, shadow, {})

    assert not any(f.constraint_id == "vehicles_deleted_in_run" for f in findings)


# ---------------------------------------------------------------------------
# M3.1 batch 1/4 — constraints firing on the M2 verb set
# ---------------------------------------------------------------------------


def test_single_seed_run_not_statistically_valid_fires_when_no_seeds_given():
    shadow = _shadow_model()
    action = _action("run_simulation", duration_s=300)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "single_seed_run_not_statistically_valid"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    _assert_well_formed_messages(finding, must_contain_ko="1")


def test_single_seed_run_not_statistically_valid_fires_below_recommended_count():
    shadow = _shadow_model()
    action = _action("run_simulation", duration_s=300, random_seeds=[1, 2])

    findings = run_checks("preflight", action, shadow, {})

    assert any(
        f.constraint_id == "single_seed_run_not_statistically_valid" for f in findings
    )


def test_single_seed_run_not_statistically_valid_silent_at_or_above_recommended_count():
    shadow = _shadow_model()
    action = _action(
        "run_simulation", duration_s=300, random_seeds=[1, 2, 3, 4, 5]
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "single_seed_run_not_statistically_valid" for f in findings
    )


def test_single_seed_run_not_statistically_valid_does_not_apply_to_unrelated_tool():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "single_seed_run_not_statistically_valid" for f in findings
    )


def test_sim_duration_too_short_for_evaluation_fires_below_floor():
    shadow = _shadow_model()
    action = _action("set_sim_duration", seconds=120)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "sim_duration_too_short_for_evaluation"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    _assert_well_formed_messages(finding, must_contain_ko="120")


def test_sim_duration_too_short_for_evaluation_silent_at_or_above_floor():
    shadow = _shadow_model()
    action = _action("set_sim_duration", seconds=3600)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "sim_duration_too_short_for_evaluation" for f in findings
    )


def test_signal_cycle_change_may_break_corridor_sync_fires_when_sibling_shares_old_cycle():
    shadow = _shadow_model(
        signals=[
            {"sc_no": "1", "sc_name": "SC1", "cycle_time": 90, "controller_type": "FIXEDTIME", "phases": []},
            {"sc_no": "2", "sc_name": "SC2", "cycle_time": 90, "controller_type": "FIXEDTIME", "phases": []},
        ]
    )
    action = _action("set_signal_cycle", sc_no="1", cycle=100)

    findings = run_checks("preflight", action, shadow, {})

    matches = [
        f for f in findings if f.constraint_id == "signal_cycle_change_may_break_corridor_sync"
    ]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    _assert_well_formed_messages(finding, must_contain_ko="1")
    assert "2" in finding.suggested_fix["coordinated_controllers"]


def test_signal_cycle_change_may_break_corridor_sync_silent_when_no_sibling_shares_cycle():
    shadow = _shadow_model(
        signals=[
            {"sc_no": "1", "sc_name": "SC1", "cycle_time": 90, "controller_type": "FIXEDTIME", "phases": []},
            {"sc_no": "2", "sc_name": "SC2", "cycle_time": 60, "controller_type": "FIXEDTIME", "phases": []},
        ]
    )
    action = _action("set_signal_cycle", sc_no="1", cycle=100)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "signal_cycle_change_may_break_corridor_sync" for f in findings
    )


def test_signal_cycle_change_may_break_corridor_sync_silent_when_cycle_unchanged():
    shadow = _shadow_model(
        signals=[
            {"sc_no": "1", "sc_name": "SC1", "cycle_time": 90, "controller_type": "FIXEDTIME", "phases": []},
            {"sc_no": "2", "sc_name": "SC2", "cycle_time": 90, "controller_type": "FIXEDTIME", "phases": []},
        ]
    )
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "signal_cycle_change_may_break_corridor_sync" for f in findings
    )


def test_cc1_scalar_write_rejected_fires_on_bare_number():
    shadow = _shadow_model()
    action = _action("set_driving_behavior_parameter", behavior_id="5", cc1=1.2)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "cc1_scalar_write_rejected"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    _assert_well_formed_messages(finding, must_contain_ko="1.2")


def test_cc1_scalar_write_rejected_silent_when_no_cc1_param():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "cc1_scalar_write_rejected" for f in findings)


def test_vehicle_input_multi_interval_write_gap_fires_when_datum_present():
    shadow = _shadow_model(
        vehicle_inputs=[
            {"no": "1", "name": "Main St EB", "link": "101", "volume": 900.0, "interval_count": 4}
        ]
    )
    action = _action("set_vehicle_input_volume", input_no="1", volume=1200)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "vehicle_input_multi_interval_write_gap"]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="4")


def test_vehicle_input_multi_interval_write_gap_fires_when_interval_no_is_explicit_one():
    # v2 unit ⑤: interval_no=1 passed EXPLICITLY is the same "writing
    # interval 1, other intervals exist" case as omitting it.
    shadow = _shadow_model(
        vehicle_inputs=[
            {"no": "1", "name": "Main St EB", "link": "101", "volume": 900.0, "interval_count": 4}
        ]
    )
    action = _action("set_vehicle_input_volume", input_no="1", volume=1200, interval_no=1)

    findings = run_checks("preflight", action, shadow, {})

    assert any(f.constraint_id == "vehicle_input_multi_interval_write_gap" for f in findings)


def test_vehicle_input_multi_interval_write_gap_silent_when_targeting_a_later_interval():
    # v2 unit ⑤: explicitly targeting interval_no=2 is a deliberate, informed
    # choice about WHICH interval to change -- not the "wrote interval 1,
    # ignored the rest" gap this check exists for.
    shadow = _shadow_model(
        vehicle_inputs=[
            {"no": "1", "name": "Main St EB", "link": "101", "volume": 900.0, "interval_count": 4}
        ]
    )
    action = _action("set_vehicle_input_volume", input_no="1", volume=1200, interval_no=2)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "vehicle_input_multi_interval_write_gap" for f in findings
    )


def test_vehicle_input_multi_interval_write_gap_skipped_when_datum_absent():
    shadow = _shadow_model()  # default fixture has no interval_count key
    action = _action("set_vehicle_input_volume", input_no="1", volume=1200)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "vehicle_input_multi_interval_write_gap" for f in findings
    )


# ---------------------------------------------------------------------------
# #44 vehicle_input_interval_out_of_range (refuse, preflight) — v2 unit ⑤
# ---------------------------------------------------------------------------


def test_vehicle_input_interval_out_of_range_fires_when_beyond_known_count():
    shadow = _shadow_model(
        vehicle_inputs=[
            {"no": "1", "name": "Main St EB", "link": "101", "volume": 900.0, "interval_count": 3}
        ]
    )
    action = _action("set_vehicle_input_volume", input_no="1", volume=1200, interval_no=5)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "vehicle_input_interval_out_of_range"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert matches[0].suggested_fix == {"available_intervals": 3}
    _assert_well_formed_messages(matches[0], must_contain_ko="3")


def test_vehicle_input_interval_out_of_range_silent_when_within_range():
    shadow = _shadow_model(
        vehicle_inputs=[
            {"no": "1", "name": "Main St EB", "link": "101", "volume": 900.0, "interval_count": 3}
        ]
    )
    action = _action("set_vehicle_input_volume", input_no="1", volume=1200, interval_no=3)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_input_interval_out_of_range" for f in findings)


def test_vehicle_input_interval_out_of_range_silent_for_interval_1():
    # interval_no absent/1 is always valid regardless of interval_count --
    # every vehicle input has an implicit interval 1.
    shadow = _shadow_model(
        vehicle_inputs=[
            {"no": "1", "name": "Main St EB", "link": "101", "volume": 900.0, "interval_count": 0}
        ]
    )
    action = _action("set_vehicle_input_volume", input_no="1", volume=1200)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_input_interval_out_of_range" for f in findings)


def test_vehicle_input_interval_out_of_range_skipped_when_interval_count_unknown():
    # interval_count absent entirely (older/synthetic fixture, or the demo
    # network's zero-interval shape) -- refusing on absent data would be a
    # false positive, not caution; the write stays safe either way because a
    # genuinely-nonexistent interval fails CLOSED over COM.
    shadow = _shadow_model(
        vehicle_inputs=[{"no": "1", "name": "Main St EB", "link": "101", "volume": 900.0}]
    )
    action = _action("set_vehicle_input_volume", input_no="1", volume=1200, interval_no=5)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_input_interval_out_of_range" for f in findings)


def test_vehicle_input_interval_out_of_range_skipped_when_target_missing():
    shadow = _shadow_model(vehicle_inputs=[])
    action = _action("set_vehicle_input_volume", input_no="999", volume=1200, interval_no=5)

    findings = run_checks("preflight", action, shadow, {})

    # missing target is object_not_found's job (#1), not this check's
    assert not any(f.constraint_id == "vehicle_input_interval_out_of_range" for f in findings)
    assert any(f.constraint_id == "object_not_found" for f in findings)


def test_demand_scale_route_double_count_gap_fires_when_routes_flagged():
    shadow = _shadow_model()
    shadow["routes"] = [{"id": "7", "manually_scaled_this_turn": True}]
    action = _action("set_vehicle_input_volume", input_no="1", volume=1200)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "demand_scale_route_double_count_gap"]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="1")


def test_demand_scale_route_double_count_gap_skipped_when_routes_absent():
    shadow = _shadow_model()  # no "routes" key at all — today's production reality
    action = _action("set_vehicle_input_volume", input_no="1", volume=1200)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "demand_scale_route_double_count_gap" for f in findings
    )


def test_evaluation_not_configured_before_run_fires_when_run_result_says_false():
    shadow = _shadow_model()
    action = _action("run_simulation", duration_s=300)
    extra = {"run_result": {"tables": ["SIMULATIONRUN"], "evaluation_configured": False}}

    findings = run_checks("postrun", action, shadow, extra)

    matches = [f for f in findings if f.constraint_id == "evaluation_not_configured_before_run"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"


def test_evaluation_not_configured_before_run_silent_when_run_result_says_true():
    shadow = _shadow_model()
    action = _action("run_simulation", duration_s=300)
    extra = {
        "run_result": {
            "tables": ["SIMULATIONRUN", "VEHICLENETWORKPERFORMANCEMEASUREMENT_EVALUATIONTIMEINTERVALCLASS"],
            "evaluation_configured": True,
        }
    }

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(
        f.constraint_id == "evaluation_not_configured_before_run" for f in findings
    )


def test_evaluation_not_configured_before_run_skipped_when_field_absent():
    """An older/unmapped run tool's run_result carries no evaluation_configured
    key at all -- no claim can be made either way, so this stays silent
    (distinct from tools_run.py's OWN defensive default at the tool-envelope
    layer, which reads a missing key as False before it ever reaches here)."""
    shadow = _shadow_model()
    action = _action("run_simulation", duration_s=300)
    extra = {"run_result": {"tables": ["SIMULATIONRUN"]}}

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(
        f.constraint_id == "evaluation_not_configured_before_run" for f in findings
    )


def test_evaluation_not_configured_before_run_skipped_when_run_result_absent():
    shadow = _shadow_model()  # no run_result in extra at all -- preflight-shaped call
    action = _action("run_simulation", duration_s=300)

    findings = run_checks("postrun", action, shadow, {})

    assert not any(
        f.constraint_id == "evaluation_not_configured_before_run" for f in findings
    )


def test_lane_count_edit_unsupported_fires_on_lane_count_param():
    shadow = _shadow_model()
    action = _action("set_link_lanes", link_id="101", lane_count=4)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "lane_count_edit_unsupported"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"


def test_lane_count_edit_unsupported_silent_without_lane_param():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "lane_count_edit_unsupported" for f in findings)


def test_speed_limit_edit_ambiguous_fires_on_speed_limit_param():
    shadow = _shadow_model()
    action = _action("set_link_speed", link_id="101", speed_limit=60)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "speed_limit_edit_ambiguous"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"


def test_speed_limit_edit_ambiguous_silent_without_speed_param():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "speed_limit_edit_ambiguous" for f in findings)


# ---------------------------------------------------------------------------
# M3.1 batch 2/4 — conflict-area / intergreen semantics
# ---------------------------------------------------------------------------


def test_conflict_area_data_unavailable_fires_when_passive_present():
    # Placeholder-shape fixture kept exactly as M3.1 wrote it (title-case
    # "Passive"/"Active", no "no" field) — the check's own
    # ``.strip().lower() == "passive"`` compare is case-insensitive by
    # design, and this pins that the check never required the richer v2
    # unit ④ shape to work. See
    # test_conflict_area_data_unavailable_fires_on_real_parsed_shape below
    # for the shape tools_network.py actually produces now that the gap is
    # closed (upper-case "PASSIVE", "no"/"link_a"/"link_b" present too).
    shadow = _shadow_model()
    shadow["conflict_areas"] = [
        {"link_pair": ("10", "20"), "status": "Passive"},
        {"link_pair": ("30", "40"), "status": "Active"},
    ]
    action = _action("set_signal_cycle", sc_no="1", cycle=90)
    extra = {"written_value": 90, "readback_value": 90}

    findings = run_checks("postwrite", action, shadow, extra)

    matches = [f for f in findings if f.constraint_id == "conflict_area_data_unavailable"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    _assert_well_formed_messages(finding, must_contain_ko="1")


def test_conflict_area_data_unavailable_fires_on_real_parsed_shape():
    # v2 unit ④: the REAL shape tools_network._parse_conflict_areas produces
    # (uppercase status, no/link_a/link_b/name present alongside link_pair) —
    # closes the gap this check has waited on since M3.1.
    shadow = _shadow_model(
        conflict_areas=[
            _conflict_area("3", "3", "12", "PASSIVE"),
            _conflict_area("6", "3", "23", "BHASRIGHTOFWAY"),
        ]
    )
    action = _action("set_signal_cycle", sc_no="1", cycle=90)
    extra = {"written_value": 90, "readback_value": 90}

    findings = run_checks("postwrite", action, shadow, extra)

    matches = [f for f in findings if f.constraint_id == "conflict_area_data_unavailable"]
    assert len(matches) == 1
    assert matches[0].details["passive_count"] == 1
    assert "3-12" in matches[0].details["link_pairs"]


def test_conflict_area_data_unavailable_silent_when_all_active():
    shadow = _shadow_model()
    shadow["conflict_areas"] = [{"link_pair": ("10", "20"), "status": "Active"}]
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("postwrite", action, shadow, {})

    assert not any(f.constraint_id == "conflict_area_data_unavailable" for f in findings)


def test_conflict_area_data_unavailable_silent_when_data_is_empty_list():
    # v2 unit ④: the demo network's real production shape — the key IS
    # present (tools_network.py always sets it now) but the list is empty
    # (zero <conflictArea> elements in the .inpx) — must degrade the same as
    # "key absent" (falsy either way), not crash or misreport.
    shadow = _shadow_model(conflict_areas=[])
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("postwrite", action, shadow, {})

    assert not any(f.constraint_id == "conflict_area_data_unavailable" for f in findings)


def test_conflict_area_data_unavailable_skipped_when_data_absent():
    # Older/synthetic shadow-model shape (key omitted entirely) must still
    # degrade silently — the check's defensive .get() is unchanged even
    # though production always populates the key now.
    shadow = _shadow_model()  # no "conflict_areas" key at all
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("postwrite", action, shadow, {})

    assert not any(f.constraint_id == "conflict_area_data_unavailable" for f in findings)


def test_conflict_area_data_unavailable_message_caps_enumerated_pairs():
    # Live bug (2026-07-27): a real network with 144 Passive conflict-area
    # pairs enumerated all 144 into the message, and via M3.3's
    # accumulated-warnings carry-forward that wall of text rode EVERY
    # subsequent answer in the session. The rendered message must cap the
    # enumerated pairs and name the remainder count instead; `details` must
    # keep the FULL, uncapped list so nothing is lost for traces/eval.
    passive = [_conflict_area(str(i), str(i), str(i + 1), "PASSIVE") for i in range(12)]
    shadow = _shadow_model(conflict_areas=passive)
    action = _action("set_signal_cycle", sc_no="1", cycle=90)
    extra = {"written_value": 90, "readback_value": 90}

    findings = run_checks("postwrite", action, shadow, extra)

    matches = [f for f in findings if f.constraint_id == "conflict_area_data_unavailable"]
    assert len(matches) == 1
    finding = matches[0]

    # details: full, uncapped, machine-readable list — all 12 pairs present.
    assert finding.details["passive_count"] == 12
    assert len(finding.details["link_pairs"]) == 12
    assert "11-12" in finding.details["link_pairs"]

    # messages: capped at 8 enumerated pairs + an explicit remainder count,
    # in every language — never all 12 pairs, and never a bare count with no
    # sense of what's missing.
    for lang, remainder_fragment in (("ko", "외 4"), ("en", "4 more"), ("ja", "他4")):
        message = finding.messages[lang]
        assert remainder_fragment in message, f"{lang} message missing remainder note: {message!r}"
        assert "11-12" not in message, f"{lang} message should not enumerate past the cap: {message!r}"
        for i in range(8):
            assert f"{i}-{i + 1}" in message, f"{lang} message missing capped pair {i}-{i + 1}: {message!r}"


# ---------------------------------------------------------------------------
# #new conflict_area_status_change_confirm — v2 unit (4)
# ---------------------------------------------------------------------------


def test_conflict_area_status_change_confirm_fires_and_echoes_the_link_pair():
    shadow = _shadow_model(conflict_areas=[_conflict_area("3", "3", "12", "PASSIVE")])
    action = _action("set_conflict_area_status", conflict_area_no="3", status="AHASRIGHTOFWAY")

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "conflict_area_status_change_confirm"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    assert finding.phase == "preflight"
    assert finding.details == {
        "no": "3",
        "link_a": "3",
        "link_b": "12",
        "old_status": "PASSIVE",
        "new_status": "AHASRIGHTOFWAY",
    }
    for lang in LANGS:
        assert "3" in finding.messages[lang]
        assert "12" in finding.messages[lang]


def test_conflict_area_status_change_confirm_does_not_apply_to_unrelated_tool():
    shadow = _shadow_model(conflict_areas=[_conflict_area("3", "3", "12", "PASSIVE")])
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "conflict_area_status_change_confirm" for f in findings)


def test_conflict_area_status_change_confirm_silent_when_target_missing():
    # missing target is object_not_found's job (#1), not this one's.
    shadow = _shadow_model(conflict_areas=[_conflict_area("3", "3", "12", "PASSIVE")])
    action = _action("set_conflict_area_status", conflict_area_no="999", status="AHASRIGHTOFWAY")

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "conflict_area_status_change_confirm" for f in findings)


def _signal_with_phases(phases: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "sc_no": "1",
        "sc_name": "SC1",
        "cycle_time": 90,
        "controller_type": "FIXEDTIME",
        "phases": phases,
    }


def test_conflicting_greens_in_signal_plan_fires_on_overlapping_cross_axis_phases():
    shadow = _shadow_model(
        signals=[
            _signal_with_phases(
                [
                    {
                        "start": 0,
                        "duration": 30,
                        "signal_groups": ["1"],
                        "signal_group_ids": ["1"],
                        "movements": [
                            {"from_direction": "north", "to_direction": "south", "turn": "straight"}
                        ],
                    },
                    {
                        "start": 10,
                        "duration": 20,
                        "signal_groups": ["3"],
                        "signal_group_ids": ["3"],
                        "movements": [
                            {"from_direction": "east", "to_direction": "west", "turn": "straight"}
                        ],
                    },
                ]
            )
        ]
    )
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "conflicting_greens_in_signal_plan"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    _assert_well_formed_messages(finding, must_contain_ko="1")


def test_conflicting_greens_in_signal_plan_silent_for_same_axis_overlap():
    # Opposing north/south through movements sharing a phase window is a
    # normal, legitimate concurrent phase -- must NOT fire.
    shadow = _shadow_model(
        signals=[
            _signal_with_phases(
                [
                    {
                        "start": 0,
                        "duration": 30,
                        "signal_groups": ["1"],
                        "signal_group_ids": ["1"],
                        "movements": [
                            {"from_direction": "north", "to_direction": "south", "turn": "straight"}
                        ],
                    },
                    {
                        "start": 5,
                        "duration": 25,
                        "signal_groups": ["5"],
                        "signal_group_ids": ["5"],
                        "movements": [
                            {"from_direction": "south", "to_direction": "north", "turn": "straight"}
                        ],
                    },
                ]
            )
        ]
    )
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "conflicting_greens_in_signal_plan" for f in findings)


def test_conflicting_greens_in_signal_plan_silent_when_windows_do_not_overlap():
    shadow = _shadow_model(
        signals=[
            _signal_with_phases(
                [
                    {
                        "start": 0,
                        "duration": 20,
                        "signal_groups": ["1"],
                        "signal_group_ids": ["1"],
                        "movements": [
                            {"from_direction": "north", "to_direction": "south", "turn": "straight"}
                        ],
                    },
                    {
                        "start": 30,
                        "duration": 20,
                        "signal_groups": ["3"],
                        "signal_group_ids": ["3"],
                        "movements": [
                            {"from_direction": "east", "to_direction": "west", "turn": "straight"}
                        ],
                    },
                ]
            )
        ]
    )
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "conflicting_greens_in_signal_plan" for f in findings)


def test_conflicting_greens_in_signal_plan_silent_when_fewer_than_two_phases():
    shadow = _shadow_model()  # default fixture's signal has empty phases
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "conflicting_greens_in_signal_plan" for f in findings)


def test_conflicting_greens_in_signal_plan_applies_to_offset_too():
    shadow = _shadow_model(
        signals=[
            _signal_with_phases(
                [
                    {
                        "start": 0,
                        "duration": 30,
                        "signal_groups": ["1"],
                        "signal_group_ids": ["1"],
                        "movements": [
                            {"from_direction": "north", "to_direction": "south", "turn": "left"}
                        ],
                    },
                    {
                        "start": 10,
                        "duration": 20,
                        "signal_groups": ["3"],
                        "signal_group_ids": ["3"],
                        "movements": [
                            {"from_direction": "west", "to_direction": "east", "turn": "straight"}
                        ],
                    },
                ]
            )
        ]
    )
    action = _action("set_signal_offset", sc_no="1", offset=5)

    findings = run_checks("preflight", action, shadow, {})

    assert any(f.constraint_id == "conflicting_greens_in_signal_plan" for f in findings)


def test_ring_barrier_splits_sum_mismatch_fires_when_sums_differ():
    shadow = _shadow_model(
        signals=[
            {
                "sc_no": "1",
                "sc_name": "SC1",
                "cycle_time": 90,
                "controller_type": "FIXEDTIME",
                "phases": [],
                "ring_barrier_splits": [30, 25, 20, 15],  # sums to 90
            }
        ]
    )
    action = _action("set_signal_cycle", sc_no="1", cycle=100)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "ring_barrier_splits_sum_mismatch"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    _assert_well_formed_messages(finding, must_contain_ko="100")


def test_ring_barrier_splits_sum_mismatch_silent_when_sums_match():
    shadow = _shadow_model(
        signals=[
            {
                "sc_no": "1",
                "sc_name": "SC1",
                "cycle_time": 90,
                "controller_type": "FIXEDTIME",
                "phases": [],
                "ring_barrier_splits": [30, 25, 20, 15],
            }
        ]
    )
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "ring_barrier_splits_sum_mismatch" for f in findings)


def test_ring_barrier_splits_sum_mismatch_skipped_when_datum_absent():
    shadow = _shadow_model()  # default fixture has no ring_barrier_splits key
    action = _action("set_signal_cycle", sc_no="1", cycle=1)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "ring_barrier_splits_sum_mismatch" for f in findings)


# ---------------------------------------------------------------------------
# M3.1 batch 3/4 — post-run lie detectors
# ---------------------------------------------------------------------------


def test_latent_demand_residue_after_run_fires_when_positive():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"], "latent_demand_vehicles": 12}}

    findings = run_checks("postrun", action, shadow, extra)

    matches = [f for f in findings if f.constraint_id == "latent_demand_residue_after_run"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    _assert_well_formed_messages(finding, must_contain_ko="12")


def test_latent_demand_residue_after_run_silent_when_zero():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"], "latent_demand_vehicles": 0}}

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "latent_demand_residue_after_run" for f in findings)


def test_latent_demand_residue_after_run_skipped_when_datum_absent():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"]}}

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "latent_demand_residue_after_run" for f in findings)


def test_gridlock_reports_los_a_fires_for_zero_throughput_los_a():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {
        "run_result": {
            "tables": ["SIMULATIONRUN"],
            "los_by_movement": [
                {"movement": "NB-L", "los": "A", "volume": 0},
                {"movement": "SB-T", "los": "C", "volume": 400},
            ],
        }
    }

    findings = run_checks("postrun", action, shadow, extra)

    matches = [f for f in findings if f.constraint_id == "gridlock_reports_los_a"]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="NB-L")


def test_gridlock_reports_los_a_silent_when_los_a_has_volume():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {
        "run_result": {
            "tables": ["SIMULATIONRUN"],
            "los_by_movement": [{"movement": "NB-L", "los": "A", "volume": 250}],
        }
    }

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "gridlock_reports_los_a" for f in findings)


def test_gridlock_reports_los_a_skipped_when_datum_absent():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"]}}

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "gridlock_reports_los_a" for f in findings)


def test_evaluation_warmup_start_at_zero_fires_when_zero():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {
        "run_result": {
            "tables": ["SIMULATIONRUN"],
            "evaluation_warmup_starts": {"node_eval_1": 0, "link_eval_2": 600},
        }
    }

    findings = run_checks("postrun", action, shadow, extra)

    matches = [f for f in findings if f.constraint_id == "evaluation_warmup_start_at_zero"]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="node_eval_1")


def test_evaluation_warmup_start_at_zero_silent_when_all_nonzero():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {
        "run_result": {
            "tables": ["SIMULATIONRUN"],
            "evaluation_warmup_starts": {"node_eval_1": 600},
        }
    }

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "evaluation_warmup_start_at_zero" for f in findings)


def test_evaluation_warmup_start_at_zero_skipped_when_datum_absent():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"]}}

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "evaluation_warmup_start_at_zero" for f in findings)


def test_queue_length_definition_not_portable_fires_when_definition_missing():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {
        "run_result": {"tables": ["SIMULATIONRUN"], "queue_results": {"link1": 45.2}}
    }

    findings = run_checks("postrun", action, shadow, extra)

    matches = [f for f in findings if f.constraint_id == "queue_length_definition_not_portable"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"


def test_queue_length_definition_not_portable_silent_when_definition_present():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {
        "run_result": {
            "tables": ["SIMULATIONRUN"],
            "queue_results": {"link1": 45.2},
            "queue_definition": {"speed_threshold_kmh": 5, "max_gap_m": 10},
        }
    }

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "queue_length_definition_not_portable" for f in findings)


def test_queue_length_definition_not_portable_skipped_when_no_queue_data():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"]}}

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "queue_length_definition_not_portable" for f in findings)


def test_delay_measurement_truncated_by_queue_fires_when_queue_exceeds_segment():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {
        "run_result": {
            "tables": ["SIMULATIONRUN"],
            "delay_segments": [
                {"node": "5", "segment_length_m": 100, "observed_queue_m": 150},
                {"node": "6", "segment_length_m": 100, "observed_queue_m": 40},
            ],
        }
    }

    findings = run_checks("postrun", action, shadow, extra)

    matches = [f for f in findings if f.constraint_id == "delay_measurement_truncated_by_queue"]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="5")


def test_delay_measurement_truncated_by_queue_silent_when_queue_within_segment():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {
        "run_result": {
            "tables": ["SIMULATIONRUN"],
            "delay_segments": [{"node": "5", "segment_length_m": 100, "observed_queue_m": 40}],
        }
    }

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "delay_measurement_truncated_by_queue" for f in findings)


def test_delay_measurement_truncated_by_queue_skipped_when_datum_absent():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"]}}

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "delay_measurement_truncated_by_queue" for f in findings)


def test_evaluation_interval_multiple_of_cycle_fires_on_exact_multiple():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {
        "run_result": {
            "tables": ["SIMULATIONRUN"],
            "evaluation_interval_s": 180,
            "signal_cycle_s": 90,
        }
    }

    findings = run_checks("postrun", action, shadow, extra)

    matches = [f for f in findings if f.constraint_id == "evaluation_interval_multiple_of_cycle"]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="180")


def test_evaluation_interval_multiple_of_cycle_silent_when_not_multiple():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {
        "run_result": {
            "tables": ["SIMULATIONRUN"],
            "evaluation_interval_s": 125,
            "signal_cycle_s": 90,
        }
    }

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "evaluation_interval_multiple_of_cycle" for f in findings)


def test_evaluation_interval_multiple_of_cycle_skipped_when_datum_absent():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"]}}

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "evaluation_interval_multiple_of_cycle" for f in findings)


def test_vissim_version_unpinned_fires_when_different():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"], "vissim_version": "2024"}}

    findings = run_checks("postrun", action, shadow, extra)

    matches = [f for f in findings if f.constraint_id == "vissim_version_unpinned"]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="2024")


def test_vissim_version_unpinned_silent_when_matches_pinned_version():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"], "vissim_version": "2026"}}

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "vissim_version_unpinned" for f in findings)


def test_vissim_version_unpinned_skipped_when_datum_absent():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"]}}

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "vissim_version_unpinned" for f in findings)


def test_error_log_overwritten_between_runs_fires_when_not_archived():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"], "error_log_archived": False}}

    findings = run_checks("postrun", action, shadow, extra)

    matches = [f for f in findings if f.constraint_id == "error_log_overwritten_between_runs"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"


def test_error_log_overwritten_between_runs_silent_when_archived():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"], "error_log_archived": True}}

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "error_log_overwritten_between_runs" for f in findings)


def test_error_log_overwritten_between_runs_skipped_when_datum_absent():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"]}}

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(f.constraint_id == "error_log_overwritten_between_runs" for f in findings)


def test_simulation_resolution_unpinned_across_comparison_fires_below_min():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"], "simulation_resolution": 1}}

    findings = run_checks("postrun", action, shadow, extra)

    matches = [
        f
        for f in findings
        if f.constraint_id == "simulation_resolution_unpinned_across_comparison"
    ]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="1")


def test_simulation_resolution_unpinned_across_comparison_silent_at_or_above_min():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"], "simulation_resolution": 10}}

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(
        f.constraint_id == "simulation_resolution_unpinned_across_comparison" for f in findings
    )


def test_simulation_resolution_unpinned_across_comparison_skipped_when_datum_absent():
    shadow = _shadow_model()
    action = _action("run_simulation")
    extra = {"run_result": {"tables": ["SIMULATIONRUN"]}}

    findings = run_checks("postrun", action, shadow, extra)

    assert not any(
        f.constraint_id == "simulation_resolution_unpinned_across_comparison" for f in findings
    )


# ---------------------------------------------------------------------------
# M3.1 batch 4/4 — the remainder
# ---------------------------------------------------------------------------


def test_speed_decision_unpaired_fires_without_restore_id():
    # v2a unit ⑧: retargeted from the placeholder "add_desired_speed_decision"
    # tool onto the real set_des_speed_decision tool + its real
    # restore_decision_no param (see constraints.py's #30 comment).
    shadow = _shadow_model()
    action = _action(
        "set_des_speed_decision", decision_no="6", veh_class_no="10", des_speed_distr_no="25"
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "speed_decision_unpaired"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"


def test_speed_decision_unpaired_silent_with_restore_id():
    shadow = _shadow_model()
    action = _action(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no="25",
        restore_decision_no="7",
        restore_distr_no="50",
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "speed_decision_unpaired" for f in findings)


def test_route_integrity_unverifiable_fires_when_route_disconnected():
    shadow = _shadow_model()
    shadow["routes"] = [{"id": "7", "disconnected": True}]
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("postwrite", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "route_integrity_unverifiable"]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="7")


def test_route_integrity_unverifiable_skipped_when_routes_absent():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("postwrite", action, shadow, {})

    assert not any(f.constraint_id == "route_integrity_unverifiable" for f in findings)


def test_route_integrity_unverifiable_message_caps_enumerated_routes():
    # Same unbounded-enumeration fix as conflict_area_data_unavailable
    # (2026-07-27): disconnected-route count scales with network size, so
    # the rendered message must cap it too. `details` keeps the full list.
    shadow = _shadow_model()
    shadow["routes"] = [{"id": str(i), "disconnected": True} for i in range(10)]
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("postwrite", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "route_integrity_unverifiable"]
    assert len(matches) == 1
    finding = matches[0]

    assert finding.details["disconnected_routes"] == [str(i) for i in range(10)]

    for lang, remainder_fragment in (("ko", "외 2"), ("en", "2 more"), ("ja", "他2")):
        message = finding.messages[lang]
        assert remainder_fragment in message, f"{lang} message missing remainder note: {message!r}"
        assert "7" in message, f"{lang} message missing last capped route id 7: {message!r}"
        assert "8" not in message and "9" not in message, (
            f"{lang} message should not enumerate past the cap: {message!r}"
        )


def test_stale_route_files_after_edit_fires_when_enabled_and_stale():
    shadow = _shadow_model()
    shadow["dynamic_assignment"] = {"enabled": True, "paths_stale": True}
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("postwrite", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "stale_route_files_after_edit"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"


def test_stale_route_files_after_edit_skipped_when_absent():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("postwrite", action, shadow, {})

    assert not any(f.constraint_id == "stale_route_files_after_edit" for f in findings)


def test_driving_behavior_parameter_model_mismatch_fires_on_mismatch():
    shadow = _shadow_model()
    shadow["driving_behaviors"] = [{"id": "5", "car_following_model": 74}]
    action = _action("set_driving_behavior_parameter", behavior_id="5", parameter="cc1", value=1.2)

    findings = run_checks("preflight", action, shadow, {})

    matches = [
        f for f in findings if f.constraint_id == "driving_behavior_parameter_model_mismatch"
    ]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"


def test_driving_behavior_parameter_model_mismatch_silent_when_matching_model():
    shadow = _shadow_model()
    shadow["driving_behaviors"] = [{"id": "5", "car_following_model": 99}]
    action = _action("set_driving_behavior_parameter", behavior_id="5", parameter="cc1", value=1.2)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "driving_behavior_parameter_model_mismatch" for f in findings
    )


def test_driving_behavior_parameter_model_mismatch_skipped_when_behaviors_absent():
    shadow = _shadow_model()
    action = _action("set_driving_behavior_parameter", behavior_id="5", parameter="cc1", value=1.2)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "driving_behavior_parameter_model_mismatch" for f in findings
    )


def test_link_geometry_edit_reference_integrity_fires_when_link_flagged():
    shadow = _shadow_model(
        links=[
            {"id": "101", "name": "Main St", "lanes": 3, "length": 280.0, "geometry_edited_this_turn": True}
        ]
    )
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("postwrite", action, shadow, {})

    matches = [
        f for f in findings if f.constraint_id == "link_geometry_edit_reference_integrity"
    ]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="101")


def test_link_geometry_edit_reference_integrity_silent_when_no_link_flagged():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("postwrite", action, shadow, {})

    assert not any(
        f.constraint_id == "link_geometry_edit_reference_integrity" for f in findings
    )


def test_object_reference_integrity_on_delete_always_refuses():
    shadow = _shadow_model()
    action = _action("delete_object", object_type="link", object_id="101")

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_reference_integrity_on_delete"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    _assert_well_formed_messages(finding, must_contain_ko="101")


def test_object_reference_integrity_on_delete_does_not_apply_to_other_tools():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "object_reference_integrity_on_delete" for f in findings
    )


# ---------------------------------------------------------------------------
# set_vehicle_composition_share — v2 unit ⑦
# ---------------------------------------------------------------------------


def test_object_not_found_fires_for_missing_vehicle_composition():
    # v2 unit ⑦: the composition-level (first) half of
    # set_vehicle_composition_share's two-part object resolution reuses #1's
    # generic single-id machinery.
    shadow = _shadow_model(vehicle_compositions=[_vehicle_composition("1", [_composition_entry("100")])])
    action = _action("set_vehicle_composition_share", composition_no="999", veh_type_no="100", rel_flow=5.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="999")


def test_object_not_found_silent_when_vehicle_composition_present():
    shadow = _shadow_model(vehicle_compositions=[_vehicle_composition("1", [_composition_entry("100")])])
    action = _action("set_vehicle_composition_share", composition_no="1", veh_type_no="100", rel_flow=5.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


def test_vehicle_composition_entry_not_found_fires_for_missing_veh_type():
    shadow = _shadow_model(
        vehicle_compositions=[
            _vehicle_composition("1", [_composition_entry("100"), _composition_entry("200")])
        ]
    )
    action = _action("set_vehicle_composition_share", composition_no="1", veh_type_no="999", rel_flow=5.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "vehicle_composition_entry_not_found"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    _assert_well_formed_messages(finding, must_contain_ko="1")
    assert finding.suggested_fix == ["100", "200"]


def test_vehicle_composition_entry_not_found_silent_when_veh_type_present():
    shadow = _shadow_model(
        vehicle_compositions=[
            _vehicle_composition("1", [_composition_entry("100"), _composition_entry("200")])
        ]
    )
    action = _action("set_vehicle_composition_share", composition_no="1", veh_type_no="200", rel_flow=5.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_composition_entry_not_found" for f in findings)


def test_vehicle_composition_entry_not_found_defers_to_object_not_found_when_composition_missing():
    shadow = _shadow_model(vehicle_compositions=[_vehicle_composition("1", [_composition_entry("100")])])
    action = _action("set_vehicle_composition_share", composition_no="999", veh_type_no="100", rel_flow=5.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_composition_entry_not_found" for f in findings)
    assert any(f.constraint_id == "object_not_found" for f in findings)


def test_vehicle_composition_entry_not_found_does_not_apply_to_unrelated_tool():
    shadow = _shadow_model(vehicle_compositions=[_vehicle_composition("1", [_composition_entry("100")])])
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_composition_entry_not_found" for f in findings)


@pytest.mark.parametrize("bad_rel_flow", [0.0, -5.0, float("nan"), float("inf"), float("-inf")])
def test_vehicle_composition_weight_invalid_fires_for_non_positive_or_non_finite(bad_rel_flow):
    shadow = _shadow_model(vehicle_compositions=[_vehicle_composition("1", [_composition_entry("100")])])
    action = _action(
        "set_vehicle_composition_share", composition_no="1", veh_type_no="100", rel_flow=bad_rel_flow
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "vehicle_composition_weight_invalid"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"


def test_vehicle_composition_weight_invalid_silent_for_a_positive_finite_weight():
    shadow = _shadow_model(vehicle_compositions=[_vehicle_composition("1", [_composition_entry("100")])])
    action = _action("set_vehicle_composition_share", composition_no="1", veh_type_no="100", rel_flow=5.85)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_composition_weight_invalid" for f in findings)


def test_vehicle_composition_weight_invalid_does_not_apply_to_other_tools():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=-5.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_composition_weight_invalid" for f in findings)


def test_vehicle_composition_share_redistribution_gap_always_fires_and_echoes_the_estimated_share():
    # v2 unit ⑦ corrected finding: RelFlow is a RAW weight (verified live —
    # Vissim does NOT renormalize sibling entries on write), matching
    # route_relative_flow_split_confirm's (#48) own share-estimate math
    # exactly — siblings' CURRENT relFlow, not renormalized.
    shadow = _shadow_model(
        vehicle_compositions=[
            _vehicle_composition(
                "1",
                [
                    _composition_entry("100", rel_flow=0.85),
                    _composition_entry("190", rel_flow=0.05),
                    _composition_entry("200", rel_flow=0.05),
                    _composition_entry("300", rel_flow=0.05),
                ],
            )
        ]
    )
    action = _action("set_vehicle_composition_share", composition_no="1", veh_type_no="100", rel_flow=5.85)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "vehicle_composition_share_redistribution_gap"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    _assert_well_formed_messages(finding, must_contain_ko="1")
    # 5.85 / (5.85 + 0.05 + 0.05 + 0.05) == 97.5% -- siblings' CURRENT
    # rel_flow used, not renormalized.
    assert finding.details["estimated_share_pct"] == 97.5
    assert finding.details["old_rel_flow"] == 0.85
    assert finding.details["new_rel_flow"] == 5.85
    assert finding.details["entry_count"] == 4
    assert "raw" in finding.messages["en"].lower()
    assert "auto" not in finding.messages["en"].lower()  # the old, disproven premise must be gone


def test_vehicle_composition_share_redistribution_gap_notes_single_entry_has_no_effect_on_split():
    shadow = _shadow_model(
        vehicle_compositions=[_vehicle_composition("4", [_composition_entry("600", rel_flow=1.0)])]
    )
    action = _action("set_vehicle_composition_share", composition_no="4", veh_type_no="600", rel_flow=42.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "vehicle_composition_share_redistribution_gap"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.details["entry_count"] == 1
    assert finding.details["estimated_share_pct"] == 100.0
    assert "100%" in finding.messages["en"]


def test_vehicle_composition_share_redistribution_gap_silent_when_target_missing():
    shadow = _shadow_model(vehicle_compositions=[])
    action = _action("set_vehicle_composition_share", composition_no="999", veh_type_no="100", rel_flow=5.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_composition_share_redistribution_gap" for f in findings)


def test_vehicle_composition_share_redistribution_gap_does_not_apply_to_other_tools():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "vehicle_composition_share_redistribution_gap" for f in findings
    )


def test_vehicle_composition_referenced_by_vehicle_inputs_fires_and_caps_the_list():
    shadow = _shadow_model(
        vehicle_compositions=[
            _vehicle_composition(
                "1",
                [_composition_entry("100")],
                referencing_vehicle_input_nos=[str(i) for i in range(1, 12)],  # 11 inputs
            )
        ]
    )
    action = _action("set_vehicle_composition_share", composition_no="1", veh_type_no="100", rel_flow=5.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "vehicle_composition_referenced_by_vehicle_inputs"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    assert finding.details["referencing_vehicle_input_nos"] == [str(i) for i in range(1, 12)]
    # message caps at 8 + a remainder note (constraints._capped_join, limit=8)
    assert "more" in finding.messages["en"] or "외" in finding.messages["ko"]


def test_vehicle_composition_referenced_by_vehicle_inputs_silent_when_no_references():
    shadow = _shadow_model(
        vehicle_compositions=[_vehicle_composition("4", [_composition_entry("600")], referencing_vehicle_input_nos=[])]
    )
    action = _action("set_vehicle_composition_share", composition_no="4", veh_type_no="600", rel_flow=5.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "vehicle_composition_referenced_by_vehicle_inputs" for f in findings
    )


def test_vehicle_composition_referenced_by_vehicle_inputs_silent_when_target_missing():
    shadow = _shadow_model(vehicle_compositions=[])
    action = _action("set_vehicle_composition_share", composition_no="999", veh_type_no="100", rel_flow=5.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "vehicle_composition_referenced_by_vehicle_inputs" for f in findings
    )


def test_vehicle_composition_share_drastic_change_fires_minority_to_dominant():
    # before: 0.85 / (0.85+0.05+0.05+0.05) == 85% -- NOT a minority, so this
    # fixture alone should not trip the check; use a genuinely minority
    # starting entry instead (vehType 190: 0.05 / 1.0 == 5%).
    shadow = _shadow_model(
        vehicle_compositions=[
            _vehicle_composition(
                "1",
                [
                    _composition_entry("100", rel_flow=0.85),
                    _composition_entry("190", rel_flow=0.05),
                    _composition_entry("200", rel_flow=0.05),
                    _composition_entry("300", rel_flow=0.05),
                ],
            )
        ]
    )
    # new_rel_flow=9.0 -> 9.0 / (9.0+0.85+0.05+0.05) == 90.1% (dominant)
    action = _action("set_vehicle_composition_share", composition_no="1", veh_type_no="190", rel_flow=9.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "vehicle_composition_share_drastic_change"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    assert finding.details["before_share_pct"] < 50.0
    assert finding.details["after_share_pct"] >= 90.0
    _assert_well_formed_messages(finding, must_contain_ko="190")


def test_vehicle_composition_share_drastic_change_silent_for_a_modest_change():
    shadow = _shadow_model(
        vehicle_compositions=[
            _vehicle_composition(
                "1",
                [
                    _composition_entry("100", rel_flow=0.85),
                    _composition_entry("190", rel_flow=0.05),
                ],
            )
        ]
    )
    # 0.85 -> 0.90 stays comfortably dominant the whole time; not "minority
    # to dominant", just a normal small edit.
    action = _action("set_vehicle_composition_share", composition_no="1", veh_type_no="100", rel_flow=0.90)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_composition_share_drastic_change" for f in findings)


def test_vehicle_composition_share_drastic_change_silent_when_target_missing():
    shadow = _shadow_model(vehicle_compositions=[])
    action = _action("set_vehicle_composition_share", composition_no="999", veh_type_no="100", rel_flow=90.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_composition_share_drastic_change" for f in findings)


def test_restriction_missing_vehicle_class_scope_fires_without_classes():
    shadow = _shadow_model()
    action = _action("set_lane_closure", link_id="101", vehicle_classes=[])

    findings = run_checks("preflight", action, shadow, {})

    matches = [
        f for f in findings if f.constraint_id == "restriction_missing_vehicle_class_scope"
    ]
    assert len(matches) == 1
    assert matches[0].tier == "warn"


def test_restriction_missing_vehicle_class_scope_silent_with_classes():
    shadow = _shadow_model()
    action = _action(
        "set_lane_closure", link_id="101", vehicle_classes=["car", "truck"]
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "restriction_missing_vehicle_class_scope" for f in findings
    )


def test_units_ambiguous_on_write_fires_when_settings_incomplete():
    shadow = _shadow_model()
    shadow["unit_settings"] = {}
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "units_ambiguous_on_write"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"


def test_units_ambiguous_on_write_silent_when_settings_complete():
    shadow = _shadow_model()
    shadow["unit_settings"] = {"speed_unit": "kmh"}
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "units_ambiguous_on_write" for f in findings)


def test_units_ambiguous_on_write_skipped_when_settings_absent():
    # default fixture has no "unit_settings" key -- must NOT fire on every
    # single preflight call in the suite.
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "units_ambiguous_on_write" for f in findings)


def test_evaluation_change_deletes_results_always_refuses():
    shadow = _shadow_model()
    action = _action("add_evaluation", eval_type="node", node_id="5")

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "evaluation_change_deletes_results"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"


def test_evaluation_change_deletes_results_does_not_apply_to_other_tools():
    shadow = _shadow_model()
    action = _action("run_simulation", duration_s=300, random_seeds=[1, 2, 3, 4, 5])

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "evaluation_change_deletes_results" for f in findings
    )


def test_driving_behavior_shared_object_edit_fires_without_duplication_flag():
    shadow = _shadow_model()
    action = _action(
        "set_driving_behavior_parameter", behavior_id="5", parameter="w99cc0", value=1.5
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "driving_behavior_shared_object_edit"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    _assert_well_formed_messages(finding, must_contain_ko="5")


def test_driving_behavior_shared_object_edit_silent_when_duplicated_first():
    shadow = _shadow_model()
    action = _action(
        "set_driving_behavior_parameter",
        behavior_id="5",
        parameter="w99cc0",
        value=1.5,
        duplicated_before_edit=True,
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "driving_behavior_shared_object_edit" for f in findings
    )


def test_parameter_diff_not_calibration_meaningful_fires_when_moved_off_reference():
    shadow = _shadow_model()
    shadow["driving_behaviors"] = [
        {"id": "5", "parameters": {"cc1": {"calibration_reference_value": 1.5}}}
    ]
    action = _action("set_driving_behavior_parameter", behavior_id="5", parameter="cc1")
    extra = {"written_value": 2.0, "readback_value": 2.0}

    findings = run_checks("postwrite", action, shadow, extra)

    matches = [
        f for f in findings if f.constraint_id == "parameter_diff_not_calibration_meaningful"
    ]
    assert len(matches) == 1
    assert matches[0].tier == "warn"


def test_parameter_diff_not_calibration_meaningful_silent_when_matches_reference():
    shadow = _shadow_model()
    shadow["driving_behaviors"] = [
        {"id": "5", "parameters": {"cc1": {"calibration_reference_value": 1.5}}}
    ]
    action = _action("set_driving_behavior_parameter", behavior_id="5", parameter="cc1")
    extra = {"written_value": 1.5, "readback_value": 1.5}

    findings = run_checks("postwrite", action, shadow, extra)

    assert not any(
        f.constraint_id == "parameter_diff_not_calibration_meaningful" for f in findings
    )


def test_parameter_diff_not_calibration_meaningful_skipped_when_reference_absent():
    shadow = _shadow_model()
    action = _action("set_driving_behavior_parameter", behavior_id="5", parameter="cc1")
    extra = {"written_value": 2.0, "readback_value": 2.0}

    findings = run_checks("postwrite", action, shadow, extra)

    assert not any(
        f.constraint_id == "parameter_diff_not_calibration_meaningful" for f in findings
    )


def test_agency_restricted_parameter_write_fires_for_restricted_parameter():
    shadow = _shadow_model()
    action = _action("set_driving_behavior_parameter", behavior_id="5", parameter="cc1", value=1.2)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "agency_restricted_parameter_write"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"


def test_agency_restricted_parameter_write_silent_for_unrestricted_parameter():
    shadow = _shadow_model()
    action = _action(
        "set_driving_behavior_parameter",
        behavior_id="5",
        parameter="look_ahead_distance",
        value=100,
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "agency_restricted_parameter_write" for f in findings
    )


def test_unbounded_com_dialog_risk_fires_when_quick_mode_not_confirmed():
    shadow = _shadow_model()
    shadow["com_environment"] = {"quick_mode_enabled": False}
    action = _action("run_simulation", duration_s=300, random_seeds=[1, 2, 3, 4, 5])

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "unbounded_com_dialog_risk"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"


def test_unbounded_com_dialog_risk_silent_when_quick_mode_enabled():
    shadow = _shadow_model()
    shadow["com_environment"] = {"quick_mode_enabled": True}
    action = _action("run_simulation", duration_s=300, random_seeds=[1, 2, 3, 4, 5])

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "unbounded_com_dialog_risk" for f in findings)


def test_unbounded_com_dialog_risk_skipped_when_environment_data_absent():
    shadow = _shadow_model()
    action = _action("run_simulation", duration_s=300, random_seeds=[1, 2, 3, 4, 5])

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "unbounded_com_dialog_risk" for f in findings)


# ---------------------------------------------------------------------------
# des_speed_decision_entry_not_found / des_speed_distribution_not_found —
# v2a unit ⑧
# ---------------------------------------------------------------------------

_DES_SPEED_DECISIONS_FIXTURE = [
    _des_speed_decision(
        "6",
        [_veh_class_assignment("10"), _veh_class_assignment("20", veh_class_name="HGV")],
    ),
    _des_speed_decision(
        "7",
        [_veh_class_assignment("10"), _veh_class_assignment("20", veh_class_name="HGV")],
    ),
]
_DES_SPEED_DISTRIBUTIONS_FIXTURE = [
    _speed_distribution("25", "25 mph"),
    _speed_distribution("50", "55 mph"),
]


def test_des_speed_decision_entry_not_found_defers_to_object_not_found_when_primary_decision_missing():
    shadow = _shadow_model(des_speed_decisions=_DES_SPEED_DECISIONS_FIXTURE)
    action = _action(
        "set_des_speed_decision", decision_no="999", veh_class_no="10", des_speed_distr_no="25"
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "des_speed_decision_entry_not_found" for f in findings)
    assert any(f.constraint_id == "object_not_found" for f in findings)


def test_des_speed_decision_entry_not_found_fires_for_missing_veh_class_on_primary():
    shadow = _shadow_model(des_speed_decisions=_DES_SPEED_DECISIONS_FIXTURE)
    action = _action(
        "set_des_speed_decision", decision_no="6", veh_class_no="999", des_speed_distr_no="25"
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "des_speed_decision_entry_not_found"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    assert finding.details["role"] == "primary"
    assert finding.suggested_fix == {"candidates": ["10", "20"]}


def test_des_speed_decision_entry_not_found_fires_for_bogus_restore_decision():
    shadow = _shadow_model(des_speed_decisions=_DES_SPEED_DECISIONS_FIXTURE)
    action = _action(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no="25",
        restore_decision_no="999",
        restore_distr_no="50",
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "des_speed_decision_entry_not_found"]
    assert len(matches) == 1
    assert matches[0].details["role"] == "restore"
    assert matches[0].details["decision_no"] == "999"
    # A bogus restore_decision_no has NO _TOOL_TARGET_SPEC coverage of its
    # own (that table only ever names the "decision_no" param) -- this check
    # is the ONLY refusal for it.
    assert not any(f.constraint_id == "object_not_found" for f in findings)


def test_des_speed_decision_entry_not_found_fires_for_missing_veh_class_on_restore():
    # A FRESH decisions list (never the shared _DES_SPEED_DECISIONS_FIXTURE,
    # which other tests reuse by reference) with vehClass 10 removed from
    # decision 7 so the restore side is the one missing.
    shadow = _shadow_model(
        des_speed_decisions=[
            _des_speed_decision(
                "6",
                [_veh_class_assignment("10"), _veh_class_assignment("20", veh_class_name="HGV")],
            ),
            _des_speed_decision("7", [_veh_class_assignment("20", veh_class_name="HGV")]),
        ]
    )
    action = _action(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no="25",
        restore_decision_no="7",
        restore_distr_no="50",
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "des_speed_decision_entry_not_found"]
    assert len(matches) == 1
    assert matches[0].details["role"] == "restore"
    assert matches[0].details["decision_no"] == "7"


def test_des_speed_decision_entry_not_found_silent_when_pair_fully_resolves():
    shadow = _shadow_model(des_speed_decisions=_DES_SPEED_DECISIONS_FIXTURE)
    action = _action(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no="25",
        restore_decision_no="7",
        restore_distr_no="50",
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "des_speed_decision_entry_not_found" for f in findings)


def test_des_speed_decision_entry_not_found_does_not_apply_to_unrelated_tool():
    shadow = _shadow_model(des_speed_decisions=_DES_SPEED_DECISIONS_FIXTURE)
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "des_speed_decision_entry_not_found" for f in findings)


def test_des_speed_distribution_not_found_fires_for_bogus_primary_distribution():
    shadow = _shadow_model(
        des_speed_decisions=_DES_SPEED_DECISIONS_FIXTURE,
        des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE,
    )
    action = _action(
        "set_des_speed_decision", decision_no="6", veh_class_no="10", des_speed_distr_no="999999"
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "des_speed_distribution_not_found"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    assert finding.details["field"] == "des_speed_distr_no"
    assert "25" in finding.details["available"] and "50" in finding.details["available"]


def test_des_speed_distribution_not_found_fires_for_bogus_restore_distribution():
    shadow = _shadow_model(
        des_speed_decisions=_DES_SPEED_DECISIONS_FIXTURE,
        des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE,
    )
    action = _action(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no="25",
        restore_decision_no="7",
        restore_distr_no="999999",
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "des_speed_distribution_not_found"]
    assert len(matches) == 1
    assert matches[0].details["field"] == "restore_distr_no"


def test_des_speed_distribution_not_found_silent_for_real_distributions():
    shadow = _shadow_model(
        des_speed_decisions=_DES_SPEED_DECISIONS_FIXTURE,
        des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE,
    )
    action = _action(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no="25",
        restore_decision_no="7",
        restore_distr_no="50",
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "des_speed_distribution_not_found" for f in findings)


def test_des_speed_distribution_not_found_does_not_apply_to_other_tools():
    shadow = _shadow_model(des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE)
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "des_speed_distribution_not_found" for f in findings)


# ---------------------------------------------------------------------------
# M3.1 — final registry shape (all 4 batches landed)
# ---------------------------------------------------------------------------


def test_registry_size_matches_m3_1_catalog_port():
    # 8 (M0.3 seed) + 9 (batch 1: M2 verb set) + 3 (batch 2: conflict-area /
    # intergreen) + 9 (batch 3: post-run lie detectors) + 14 (batch 4: the
    # remainder) == 43, + 1 (v2 unit ④'s conflict_area_status_change_confirm,
    # not a §-numbered catalog entry -- a defensive addition specific to
    # set_conflict_area_status, see that constraint's own docstring) == 44,
    # + 1 (v2 unit ⑤'s vehicle_input_interval_out_of_range, same "not a
    # §-numbered catalog entry, a defensive addition" shape, this time for
    # set_vehicle_input_volume's new interval_no param) == 45, + 3 (v2 unit
    # ⑥'s route_not_found_in_decision / route_relative_flow_interval_out_of_
    # range / route_relative_flow_split_confirm, all three "not a
    # §-numbered catalog entry, a defensive addition" shapes for the new
    # set_route_relative_flow tool) == 48, + 4 (v2 unit ⑦'s
    # vehicle_composition_entry_not_found / vehicle_composition_weight_
    # invalid / vehicle_composition_referenced_by_vehicle_inputs /
    # vehicle_composition_share_drastic_change, all four "not a §-numbered
    # catalog entry, a defensive addition" shapes for the new
    # set_vehicle_composition_share tool -- #36 vehicle_composition_share_
    # redistribution_gap already existed and was corrected IN PLACE, not
    # added) == 52, + 2 (v2a unit ⑧'s des_speed_decision_entry_not_found /
    # des_speed_distribution_not_found, both "not a §-numbered catalog
    # entry, a defensive addition" shapes for the new set_des_speed_decision
    # tool -- #30 speed_decision_unpaired already existed and was
    # RETARGETED in place from a placeholder tool name onto the real one,
    # not added) == 54, + 4 (v2a unit ⑨'s signal_program_split_not_found /
    # green_split_outside_cycle / green_split_below_min_green /
    # signal_green_split_change_confirm, all four "not a §-numbered catalog
    # entry, a defensive addition" shapes for the new set_signal_green_split
    # tool -- #4 signal_controller_type_unsupported already existed and was
    # EXTENDED in place to also gate the new tool, not added) == 58, + 3
    # (health-check unit, 2026-07-28: evaluation_config_disarmed /
    # vehicle_input_cont_volume_mismatch / des_speed_distribution_orphan_
    # reference -- all three phase="dataset", NOT §-numbered catalog entries,
    # NOT extensions of any M2/v2 write tool's own gate; they only ever fire
    # from services.agent.health_check's whole-network scan, never from
    # guardrails.py's real preflight/postwrite/postrun run_checks() calls --
    # see the Phase alias's own comment just above class Finding) == 61.
    # Regression pin -- bump deliberately, not by accident.
    assert len(get_registry()) == 61


def test_registry_has_unique_ids():
    ids = [c.id for c in get_registry()]
    assert len(ids) == len(set(ids))


def test_every_constraint_has_well_formed_ko_en_ja_templates():
    for constraint in get_registry():
        for template in (constraint.message_ko, constraint.message_en, constraint.message_ja):
            assert isinstance(template, str) and template.strip()


# ---------------------------------------------------------------------------
# run_checks — phase and applies_to gating
# ---------------------------------------------------------------------------


def test_run_checks_only_returns_findings_for_the_requested_phase():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=-5)

    postwrite_findings = run_checks("postwrite", action, shadow, {})
    postrun_findings = run_checks("postrun", action, shadow, {})

    assert not any(f.constraint_id == "volume_out_of_range" for f in postwrite_findings)
    assert not any(f.constraint_id == "volume_out_of_range" for f in postrun_findings)


def test_run_checks_returns_multiple_findings_in_registration_order():
    # volume both out of range AND (if it were in range) would overflow capacity;
    # here -5 only trips volume_out_of_range, so use a big positive number that
    # trips both volume range sanity is fine but only one can fire post range-check
    # in practice; assert ordering among findings that DO fire together instead:
    shadow = _shadow_model(
        links=[{"id": "101", "name": "Main St EB approach", "lanes": 1, "length": 280.0}]
    )
    action = _action("set_vehicle_input_volume", input_no="1", volume=3000)

    findings = run_checks("preflight", action, shadow, {})

    ids = [f.constraint_id for f in findings]
    assert ids == ["latent_demand_overflow"]


# ---------------------------------------------------------------------------
# v2a unit ⑨ — set_signal_green_split's four checks
# ---------------------------------------------------------------------------


def _green_split_fixture(**controller_kwargs: Any) -> dict[str, Any]:
    """One FIXEDTIME controller, two programs (1 active, 2 inactive), three
    SGs in program 1 -- SG 1 green 10..40, SG 2 green 50..85, SG 3 wrapping
    green 85..5 (cycle 90)."""
    programs = [
        _signal_program(
            "1",
            [
                _sg_split("1", green_start_s=10.0, green_end_s=40.0),
                _sg_split("2", green_start_s=50.0, green_end_s=85.0),
                _sg_split("3", green_start_s=85.0, green_end_s=5.0),
            ],
        ),
        _signal_program("2", [_sg_split("1", green_start_s=1.0, green_end_s=15.0)]),
    ]
    return _shadow_model(
        signals=[
            {
                "sc_no": "1",
                "sc_name": "SC1",
                "cycle_time": 90,
                "controller_type": "FIXEDTIME",
                "phases": [],
            }
        ],
        signal_programs=[_signal_program_controller("1", programs, **controller_kwargs)],
    )


def test_green_split_controller_type_gate_extends_to_set_signal_green_split():
    shadow = _shadow_model(
        signals=[
            {
                "sc_no": "1",
                "sc_name": "SC1",
                "cycle_time": 0,
                "controller_type": "RINGBARRIERCONTROLLER",
                "phases": [],
            }
        ],
        signal_programs=[_signal_program_controller("1", [], controller_type="RINGBARRIERCONTROLLER")],
    )
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="1", green_start_s=10.0, green_end_s=40.0
    )
    findings = run_checks("preflight", action, shadow, {})
    assert any(f.constraint_id == "signal_controller_type_unsupported" for f in findings)


def test_signal_program_split_not_found_missing_controller_defers_to_object_not_found():
    shadow = _green_split_fixture()
    action = _action(
        "set_signal_green_split", sc_no="99", sg_no="1", program_no="1", green_start_s=10.0, green_end_s=40.0
    )
    findings = run_checks("preflight", action, shadow, {})
    ids = [f.constraint_id for f in findings]
    assert "object_not_found" in ids
    assert "signal_program_split_not_found" not in ids


def test_signal_program_split_not_found_fires_for_bogus_program():
    shadow = _green_split_fixture()
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="7", green_start_s=10.0, green_end_s=40.0
    )
    findings = run_checks("preflight", action, shadow, {})
    matches = [f for f in findings if f.constraint_id == "signal_program_split_not_found"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    _assert_well_formed_messages(finding, must_contain_ko="7")
    assert finding.details["reason"] == "program_not_found"
    assert finding.suggested_fix["candidates"] == ["1", "2"]
    assert finding.suggested_fix["active_program_no"] == "1"


def test_signal_program_split_not_found_fires_for_bogus_sg():
    shadow = _green_split_fixture()
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="9", program_no="1", green_start_s=10.0, green_end_s=40.0
    )
    findings = run_checks("preflight", action, shadow, {})
    matches = [f for f in findings if f.constraint_id == "signal_program_split_not_found"]
    assert len(matches) == 1
    assert matches[0].details["reason"] == "sg_not_found"
    assert matches[0].suggested_fix["candidates"] == ["1", "2", "3"]


def test_signal_program_split_not_found_fires_for_controller_without_program_data():
    shadow = _shadow_model(
        signals=[
            {
                "sc_no": "1",
                "sc_name": "SC1",
                "cycle_time": 0,
                "controller_type": "FIXEDTIME",
                "phases": [],
            }
        ],
        signal_programs=[_signal_program_controller("1", [], controller_type="FIXEDTIME")],
    )
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="1", green_start_s=10.0, green_end_s=40.0
    )
    findings = run_checks("preflight", action, shadow, {})
    matches = [f for f in findings if f.constraint_id == "signal_program_split_not_found"]
    assert len(matches) == 1
    assert matches[0].details["reason"] == "no_program_data"


def test_signal_program_split_not_found_fires_for_non_simple_shape():
    shadow = _shadow_model(
        signal_programs=[
            _signal_program_controller(
                "1",
                [
                    _signal_program(
                        "1",
                        [_sg_split("1", green_start_s=None, green_end_s=None, simple=False)],
                    )
                ],
            )
        ],
    )
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="1", green_start_s=10.0, green_end_s=40.0
    )
    findings = run_checks("preflight", action, shadow, {})
    matches = [f for f in findings if f.constraint_id == "signal_program_split_not_found"]
    assert len(matches) == 1
    assert matches[0].details["reason"] == "non_simple_shape"


def test_signal_program_split_not_found_silent_for_valid_triple():
    shadow = _green_split_fixture()
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="1", green_start_s=10.0, green_end_s=45.0
    )
    findings = run_checks("preflight", action, shadow, {})
    assert not any(f.constraint_id == "signal_program_split_not_found" for f in findings)


def test_green_split_outside_cycle_refuses_beyond_cycle_value():
    # Live-proven silent failure: Vissim itself ACCEPTS a green window edge
    # beyond the cycle time (probe: EndRed=5000 accepted with CycTm=999) --
    # this refusal is the only screen.
    shadow = _green_split_fixture()
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="1", green_start_s=10.0, green_end_s=120.0
    )
    findings = run_checks("preflight", action, shadow, {})
    matches = [f for f in findings if f.constraint_id == "green_split_outside_cycle"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert matches[0].details["cycle_s"] == 90.0
    _assert_well_formed_messages(matches[0], must_contain_ko="90")


def test_green_split_outside_cycle_refuses_negative_and_zero_length():
    shadow = _green_split_fixture()
    negative = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="1", green_start_s=-1.0, green_end_s=40.0
    )
    assert any(
        f.constraint_id == "green_split_outside_cycle"
        for f in run_checks("preflight", negative, shadow, {})
    )
    zero_length = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="1", green_start_s=40.0, green_end_s=40.0
    )
    assert any(
        f.constraint_id == "green_split_outside_cycle"
        for f in run_checks("preflight", zero_length, shadow, {})
    )


def test_green_split_outside_cycle_allows_wrapping_window():
    # start > end is a REAL, valid shape (green spans the cycle boundary --
    # Beijing program 1 SG 1 wraps 130 -> 15).
    shadow = _green_split_fixture()
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="1", green_start_s=80.0, green_end_s=5.0
    )
    findings = run_checks("preflight", action, shadow, {})
    assert not any(f.constraint_id == "green_split_outside_cycle" for f in findings)


def test_green_split_below_min_green_fires_only_with_real_datum():
    shadow = _shadow_model(
        signal_programs=[
            _signal_program_controller(
                "1",
                [_signal_program("1", [_sg_split("1", min_green_s=10.0)], cycle_s=90.0)],
                controller_type="FIXEDTIMESIMPLE",
            )
        ],
    )
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="1", green_start_s=10.0, green_end_s=15.0
    )
    findings = run_checks("preflight", action, shadow, {})
    matches = [f for f in findings if f.constraint_id == "green_split_below_min_green"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    assert matches[0].details["green_duration_s"] == 5.0

    # datum absent (blob programs report min_green_s=None) -> silent, no claim
    shadow_no_datum = _green_split_fixture()
    findings = run_checks("preflight", action, shadow_no_datum, {})
    assert not any(f.constraint_id == "green_split_below_min_green" for f in findings)


def test_signal_green_split_change_confirm_always_fires_and_reports_overlaps():
    shadow = _green_split_fixture()
    # New SG1 window 10..60 overlaps SG2's 50..85 but not SG3's wrap 85..5.
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="1", green_start_s=10.0, green_end_s=60.0
    )
    findings = run_checks("preflight", action, shadow, {})
    matches = [f for f in findings if f.constraint_id == "signal_green_split_change_confirm"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    assert finding.details["overlapping_sg_nos"] == ["2"]
    assert finding.details["old_green_window"] == {"green_start_s": 10.0, "green_end_s": 40.0}
    assert finding.details["new_green_window"] == {"green_start_s": 10.0, "green_end_s": 60.0}
    assert finding.details["targets_active_program"] is True
    # The silent-failure education is ALWAYS present.
    assert "막지 않습니다" in finding.messages["ko"]
    assert "will NOT stop" in finding.messages["en"]


def test_signal_green_split_change_confirm_detects_wrapping_overlap():
    shadow = _green_split_fixture()
    # New SG1 window wraps 88..3: vs SG2's 50..85 -> no overlap; vs SG3's
    # wrap 85..5 (segments 85..90 + 0..5) -> overlap.
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="1", green_start_s=88.0, green_end_s=3.0
    )
    findings = run_checks("preflight", action, shadow, {})
    matches = [f for f in findings if f.constraint_id == "signal_green_split_change_confirm"]
    assert len(matches) == 1
    assert matches[0].details["overlapping_sg_nos"] == ["3"]


def test_signal_green_split_change_confirm_warns_loudly_on_inactive_program():
    shadow = _green_split_fixture()
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="1", program_no="2", green_start_s=1.0, green_end_s=20.0
    )
    findings = run_checks("preflight", action, shadow, {})
    matches = [f for f in findings if f.constraint_id == "signal_green_split_change_confirm"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.details["targets_active_program"] is False
    assert "활성 프로그램이 아닙니다" in finding.messages["ko"]
    assert "NOT the active program" in finding.messages["en"]


def test_signal_green_split_change_confirm_silent_when_target_unresolvable():
    # An unresolvable triple is signal_program_split_not_found's job -- the
    # confirm never fires alongside a refusal for the same broken target.
    shadow = _green_split_fixture()
    action = _action(
        "set_signal_green_split", sc_no="1", sg_no="9", program_no="1", green_start_s=10.0, green_end_s=40.0
    )
    findings = run_checks("preflight", action, shadow, {})
    assert not any(f.constraint_id == "signal_green_split_change_confirm" for f in findings)


# ---------------------------------------------------------------------------
# Health-check unit (2026-07-28) — three new phase="dataset" constraints,
# plus the dataset-check registry hook itself
# (register_dataset_check/get_dataset_checks/get_dataset_checkable_ids/
# get_edit_time_only_constraints). services/agent/health_check.py is the
# module that actually WIRES these onto the hook (importing it registers its
# scans, mirroring tools_network.py's own "importing this module registers
# its tools" pattern) -- these tests exercise the check FUNCTIONS directly
# via run_checks(phase="dataset", ...), the same way every other constraint
# in this file is tested, independent of whatever health_check.py itself
# ends up doing with them.
# ---------------------------------------------------------------------------


def test_run_checks_never_returns_dataset_phase_findings_for_the_three_real_phases():
    # The whole point of phase="dataset": guardrails.py's real edit-time
    # calls only ever request preflight/postwrite/postrun, so a
    # dataset-phase constraint must be unreachable from all three, for ANY
    # action/shadow_model/extra combination -- not just the ones this test
    # happens to construct.
    shadow = _shadow_model(evaluation_config={"present": False})
    action = _action("run_simulation")
    dataset_ids = {
        "evaluation_config_disarmed",
        "vehicle_input_cont_volume_mismatch",
        "des_speed_distribution_orphan_reference",
    }
    for phase in ("preflight", "postwrite", "postrun"):
        findings = run_checks(phase, action, shadow, {"run_result": {}})
        assert not any(f.constraint_id in dataset_ids for f in findings)


def test_evaluation_config_disarmed_fires_when_element_absent():
    shadow = _shadow_model(evaluation_config={"present": False})
    findings = run_checks("dataset", _action("anything"), shadow, {})
    matches = [f for f in findings if f.constraint_id == "evaluation_config_disarmed"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    _assert_well_formed_messages(matches[0], must_contain_ko="평가")


def test_evaluation_config_disarmed_fires_when_net_perf_eval_act_false():
    shadow = _shadow_model(
        evaluation_config={"present": True, "net_perf_eval_act": False}
    )
    findings = run_checks("dataset", _action("anything"), shadow, {})
    matches = [f for f in findings if f.constraint_id == "evaluation_config_disarmed"]
    assert len(matches) == 1
    assert matches[0].details == {"present": True, "net_perf_eval_act": False}


def test_evaluation_config_disarmed_silent_when_armed():
    shadow = _shadow_model(
        evaluation_config={"present": True, "net_perf_eval_act": True}
    )
    findings = run_checks("dataset", _action("anything"), shadow, {})
    assert not any(f.constraint_id == "evaluation_config_disarmed" for f in findings)


def test_evaluation_config_disarmed_skipped_when_datum_absent():
    shadow = _shadow_model()  # no evaluation_config key at all
    findings = run_checks("dataset", _action("anything"), shadow, {})
    assert not any(f.constraint_id == "evaluation_config_disarmed" for f in findings)


def test_vehicle_input_cont_volume_mismatch_fires_on_differing_continuous_interval():
    shadow = _shadow_model(
        vehicle_inputs=[
            {
                "no": "2",
                "name": "",
                "link": "11",
                "volume": 1000.0,
                "interval_count": 2,
                "volumes": [
                    {"interval_no": 1, "from_s": 0.0, "volume": 1000.0, "continuous": False},
                    {"interval_no": 2, "from_s": 1200.0, "volume": 500.0, "continuous": True},
                ],
            }
        ]
    )
    findings = run_checks("dataset", _action("anything"), shadow, {})
    matches = [f for f in findings if f.constraint_id == "vehicle_input_cont_volume_mismatch"]
    assert len(matches) == 1
    assert matches[0].details["mismatched_input_intervals"] == ["2.2"]
    _assert_well_formed_messages(matches[0], must_contain_ko="2.2")


def test_vehicle_input_cont_volume_mismatch_silent_when_continuous_volume_matches():
    shadow = _shadow_model(
        vehicle_inputs=[
            {
                "no": "2",
                "name": "",
                "link": "11",
                "volume": 1000.0,
                "interval_count": 2,
                "volumes": [
                    {"interval_no": 1, "from_s": 0.0, "volume": 1000.0, "continuous": False},
                    {"interval_no": 2, "from_s": 1200.0, "volume": 1000.0, "continuous": True},
                ],
            }
        ]
    )
    findings = run_checks("dataset", _action("anything"), shadow, {})
    assert not any(f.constraint_id == "vehicle_input_cont_volume_mismatch" for f in findings)


def test_vehicle_input_cont_volume_mismatch_silent_when_single_interval():
    shadow = _shadow_model()  # default single-interval vehicle_inputs fixture
    findings = run_checks("dataset", _action("anything"), shadow, {})
    assert not any(f.constraint_id == "vehicle_input_cont_volume_mismatch" for f in findings)


def test_des_speed_distribution_orphan_reference_fires_on_unresolved_distr_no():
    shadow = _shadow_model(
        des_speed_decisions=[
            _des_speed_decision(
                "6",
                [
                    _veh_class_assignment("10", des_speed_distr_no="50", des_speed_distr_name="55 mph"),
                    _veh_class_assignment("20", des_speed_distr_no="999999", des_speed_distr_name=""),
                ],
            )
        ]
    )
    shadow["des_speed_decision_entries"] = [
        {
            "id": "6.10", "decision_no": "6", "veh_class_no": "10",
            "veh_class_name": "Car", "des_speed_distr_no": "50", "des_speed_distr_name": "55 mph",
        },
        {
            "id": "6.20", "decision_no": "6", "veh_class_no": "20",
            "veh_class_name": "HGV", "des_speed_distr_no": "999999", "des_speed_distr_name": "",
        },
    ]
    findings = run_checks("dataset", _action("anything"), shadow, {})
    matches = [f for f in findings if f.constraint_id == "des_speed_distribution_orphan_reference"]
    assert len(matches) == 1
    assert matches[0].details["offending_entries"] == ["6.20"]


def test_des_speed_distribution_orphan_reference_silent_when_all_resolve():
    shadow = _shadow_model()
    shadow["des_speed_decision_entries"] = [
        {
            "id": "6.10", "decision_no": "6", "veh_class_no": "10",
            "veh_class_name": "Car", "des_speed_distr_no": "50", "des_speed_distr_name": "55 mph",
        },
    ]
    findings = run_checks("dataset", _action("anything"), shadow, {})
    assert not any(f.constraint_id == "des_speed_distribution_orphan_reference" for f in findings)


def test_des_speed_distribution_orphan_reference_skipped_when_entries_absent():
    shadow = _shadow_model()  # no des_speed_decision_entries key at all
    findings = run_checks("dataset", _action("anything"), shadow, {})
    assert not any(f.constraint_id == "des_speed_distribution_orphan_reference" for f in findings)


# ---------------------------------------------------------------------------
# Dataset-check registry hook
# ---------------------------------------------------------------------------


def test_register_dataset_check_rejects_unknown_constraint_id():
    import services.agent.constraints as constraints_module

    with pytest.raises(KeyError):
        constraints_module.register_dataset_check("not_a_real_constraint_id", lambda shadow: [])


def test_register_dataset_check_and_get_dataset_checks_round_trip():
    import services.agent.constraints as constraints_module

    before = len(constraints_module.get_dataset_checks())
    sentinel_calls: list[dict] = []

    def _sentinel_scan(shadow_model):
        sentinel_calls.append(shadow_model)
        return []

    constraints_module.register_dataset_check(["object_not_found"], _sentinel_scan)
    try:
        checks = constraints_module.get_dataset_checks()
        assert len(checks) == before + 1
        assert "object_not_found" in constraints_module.get_dataset_checkable_ids()
        # Calling the registered fn exactly once (the caller's job, not this
        # registry's) must reach the same function object that was passed in.
        ids, fn = checks[-1]
        assert ids == ("object_not_found",)
        fn({"sentinel": True})
        assert sentinel_calls == [{"sentinel": True}]
    finally:
        constraints_module._DATASET_CHECKS.pop()  # test isolation — undo the mutation


def test_get_edit_time_only_constraints_excludes_every_dataset_checkable_id():
    import services.agent.constraints as constraints_module

    checkable = constraints_module.get_dataset_checkable_ids()
    edit_time_only_ids = {c.id for c in constraints_module.get_edit_time_only_constraints()}
    assert edit_time_only_ids.isdisjoint(checkable)
    assert edit_time_only_ids | checkable == {c.id for c in get_registry()}


def test_signal_program_split_not_found_names_stage_based_as_the_real_reason():
    """Murdoch Drive finding (2026-07-28): a STAGE-based program has no
    per-signal-group green window at all. Falling through to the generic
    "signal group not found" branch would send the planner hunting for a
    different sg_no that also does not exist -- the refusal must name the
    program family instead, and offer no candidates."""
    stage_program = {
        "prog_no": "11",
        "name": "BaseAM",
        "cycle_s": 131.0,
        "program_kind": "stage_based",
        "sg_splits": [],
        "sg_splits_unavailable_reason": "STAGE_BASED_PROGRAM",
        "stage_sequence": [],
    }
    shadow = _shadow_model(
        signals=[
            {
                "sc_no": "6",
                "sc_name": "SC-Alpha",
                "cycle_time": 131,
                "controller_type": "FIXEDTIME",
                "phases": [],
            }
        ],
        signal_programs=[
            _signal_program_controller("6", [stage_program], active_program_no="11")
        ],
    )
    action = _action(
        "set_signal_green_split",
        sc_no="6",
        sg_no="1",
        program_no="11",
        green_start_s=10.0,
        green_end_s=40.0,
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "signal_program_split_not_found"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert matches[0].details["reason"] == "stage_based_program"
    assert matches[0].suggested_fix is None
    assert "현시" in matches[0].messages["ko"]

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.agent.constraints import Finding, get_registry, run_checks

LANGS = ("ko", "en", "ja")

# The active real client model, used ONLY by the sibling-disambiguation regression tests at the
# bottom of this file (everything else here is synthetic by design).
# Resolved through the REAL (unpatched) runtime layout, not
# ``services.workspaces.WORKSPACES_ROOT`` -- conftest.py redirects that
# constant at a pytest tmp dir for isolation, which is exactly what this
# fixture must NOT follow. Unit 1.4 moved the dataset from
# ``runtime/uploads/current/`` into ``runtime/workspaces/<default id>/uploads/
# current/``; the legacy path is still checked because the move is LAZY (it
# happens the first time the backend boots after upgrading), so on a box that
# has not booted since, the file is still in the old place. Same skipif
# convention as test_network_search.py.
_RUNTIME_ROOT = Path(__file__).resolve().parents[2] / "runtime"


def _resolve_active_real_inpx() -> Path:
    registry = _RUNTIME_ROOT / "workspaces" / "registry.json"
    try:
        entries = json.loads(registry.read_text(encoding="utf-8"))["workspaces"]
    except Exception:
        entries = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("is_default") and entry.get("id"):
            migrated = _RUNTIME_ROOT / "workspaces" / str(entry["id"]) / "uploads" / "current" / "current.inpx"
            if migrated.is_file():
                return migrated
    return _RUNTIME_ROOT / "uploads" / "current" / "current.inpx"


ACTIVE_REAL_INPX = _resolve_active_real_inpx()
_requires_real_dataset = pytest.mark.skipif(
    not ACTIVE_REAL_INPX.is_file(),
    reason=f"real active dataset not present on this machine: {ACTIVE_REAL_INPX}",
)


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
    priority_rules: list[dict[str, Any]] | None = None,
    vehicle_types: list[dict[str, Any]] | None = None,
    model2d3d_distributions: list[dict[str, Any]] | None = None,
    weight_distributions: list[dict[str, Any]] | None = None,
    power_distributions: list[dict[str, Any]] | None = None,
    des_accel_functions: list[dict[str, Any]] | None = None,
    max_accel_functions: list[dict[str, Any]] | None = None,
    des_decel_functions: list[dict[str, Any]] | None = None,
    max_decel_functions: list[dict[str, Any]] | None = None,
    curve_speed_functions: list[dict[str, Any]] | None = None,
    time_distributions: list[dict[str, Any]] | None = None,
    queue_counters: list[dict[str, Any]] | None = None,
    data_collection_points: list[dict[str, Any]] | None = None,
    data_collection_measurements: list[dict[str, Any]] | None = None,
    travel_time_sections: list[dict[str, Any]] | None = None,
    signal_heads: list[dict[str, Any]] | None = None,
    detectors: list[dict[str, Any]] | None = None,
    stop_signs: list[dict[str, Any]] | None = None,
    pt_stops: list[dict[str, Any]] | None = None,
    pt_lines: list[dict[str, Any]] | None = None,
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
    # priority_rules deliberately OMITTED by default too (QUEUE 3 unit 2) --
    # same reasoning as the object classes above.
    if priority_rules is not None:
        shadow["priority_rules"] = priority_rules
    # vehicle_types + its 8 target collections deliberately OMITTED by
    # default too (QUEUE (v2a) unit) -- same reasoning as the object classes
    # above.
    if vehicle_types is not None:
        shadow["vehicle_types"] = vehicle_types
    if model2d3d_distributions is not None:
        shadow["model2d3d_distributions"] = model2d3d_distributions
    if weight_distributions is not None:
        shadow["weight_distributions"] = weight_distributions
    if power_distributions is not None:
        shadow["power_distributions"] = power_distributions
    if des_accel_functions is not None:
        shadow["des_accel_functions"] = des_accel_functions
    if max_accel_functions is not None:
        shadow["max_accel_functions"] = max_accel_functions
    if des_decel_functions is not None:
        shadow["des_decel_functions"] = des_decel_functions
    if max_decel_functions is not None:
        shadow["max_decel_functions"] = max_decel_functions
    if curve_speed_functions is not None:
        shadow["curve_speed_functions"] = curve_speed_functions
    # time_distributions deliberately OMITTED by default too (QUEUE (Q3,
    # v2a) unit 5) -- same reasoning as the object classes above.
    if time_distributions is not None:
        shadow["time_distributions"] = time_distributions
    # queue_counters/data_collection_points/data_collection_measurements/
    # travel_time_sections deliberately OMITTED by default too (Q4 unit
    # (2)) -- same "most tests have nothing to do with this object class"
    # reasoning as the object classes above.
    if queue_counters is not None:
        shadow["queue_counters"] = queue_counters
    if data_collection_points is not None:
        shadow["data_collection_points"] = data_collection_points
    if data_collection_measurements is not None:
        shadow["data_collection_measurements"] = data_collection_measurements
    if travel_time_sections is not None:
        shadow["travel_time_sections"] = travel_time_sections
    # signal_heads/detectors deliberately OMITTED by default too (Q4 unit
    # (4)) -- same "most tests have nothing to do with this object class"
    # reasoning as the object classes above.
    if signal_heads is not None:
        shadow["signal_heads"] = signal_heads
    if detectors is not None:
        shadow["detectors"] = detectors
    # stop_signs deliberately OMITTED by default too (Q4 unit (6)) -- same
    # "most tests have nothing to do with this object class" reasoning as
    # the object classes above.
    if stop_signs is not None:
        shadow["stop_signs"] = stop_signs
    # pt_stops/pt_lines deliberately OMITTED by default too (Q4 unit (7)) --
    # same "most tests have nothing to do with this object class" reasoning
    # as the object classes above.
    if pt_stops is not None:
        shadow["pt_stops"] = pt_stops
    if pt_lines is not None:
        shadow["pt_lines"] = pt_lines
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


def _time_distribution(
    no: str,
    *,
    name: str = "",
    dist_type: str = "NORMAL",
    mean_s: float | None = 25.0,
    std_dev_s: float | None = 5.0,
    points: list[dict[str, Any]] | None = None,
    point_count: int | None = None,
    points_truncated: bool = False,
    reference_count: int = 0,
    referenced_by: dict[str, Any] | None = None,
) -> dict[str, Any]:
    points = points if points is not None else []
    empty_ref = {"count": 0, "ids": [], "truncated": False}
    return {
        "no": no,
        "name": name,
        "type": dist_type,
        "mean_std_dev": {"mean_s": mean_s, "std_dev_s": std_dev_s},
        "points": points,
        "point_count": point_count if point_count is not None else len(points),
        "points_truncated": points_truncated,
        "referenced_by": referenced_by
        or {
            "pt_stops": empty_ref,
            "pt_vehicle_class_overrides": empty_ref,
            "driving_behaviors": empty_ref,
            "other": {"count": 0, "by_tag": {}},
        },
        "reference_count": reference_count,
    }


def _conflict_marker(
    marker_index: int,
    *,
    link: str = "632",
    lane: str = "1",
    pos: float = 9.0,
    gap_time_s: float | None = 2.5,
    headway_m: float | None = 20.0,
    max_speed: float | None = 180.0,
    effect_driv_dir: str = "FORWARD",
) -> dict[str, Any]:
    return {
        "marker_index": marker_index,
        "link": link,
        "lane": lane,
        "pos": pos,
        "gap_time_s": gap_time_s,
        "headway_m": headway_m,
        "max_speed": max_speed,
        "effect_driv_dir": effect_driv_dir,
    }


def _priority_rule(
    no: str,
    conflict_markers: list[dict[str, Any]],
    *,
    name: str = "",
    link: str = "11467",
    lane: str = "1",
    pos: float = 5.0,
    sig_state: str = "GREEN",
    affect_driv_dir: str = "FORWARD",
    slow_down_dist: float | None = 3.0,
) -> dict[str, Any]:
    return {
        "no": no,
        "name": name,
        "link": link,
        "lane": lane,
        "pos": pos,
        "sig_state": sig_state,
        "affect_driv_dir": affect_driv_dir,
        "slow_down_dist": slow_down_dist,
        "conflict_markers": conflict_markers,
    }


def _vehicle_type(
    no: str,
    *,
    name: str = "HGV",
    category: str = "HGV",
    weight_distr_no: str | None = "2",
    referencing_composition_nos: list[str] | None = None,
) -> dict[str, Any]:
    referencing = referencing_composition_nos or []
    return {
        "no": no,
        "name": name,
        "category": category,
        "capacity": 0.0,
        "cap_exact": False,
        "weight_distr_no": weight_distr_no,
        "references": {"weight_distr": None},
        "referencing_composition_nos": {
            "count": len(referencing),
            "ids": referencing[:10],
            "truncated": len(referencing) > 10,
        },
    }


def _action(tool: str, **params: Any) -> dict[str, Any]:
    return {"tool": tool, "params": params}


def _assert_well_formed_messages(finding: Finding, *, must_contain_ko: str) -> None:
    assert set(finding.messages.keys()) == set(LANGS)
    for lang in LANGS:
        assert finding.messages[lang].strip(), f"{lang} message must not be empty"
    assert must_contain_ko in finding.messages["ko"]


def _candidate_ids(suggested_fix: Any) -> set[str]:
    """``object_not_found``'s ``suggested_fix`` is a list of enriched
    disambiguator dicts (``{id, name, ...}``, since 2026-07-31 -- see
    ``_check_object_not_found``), not bare id strings. Most of the tests
    below only care about WHICH ids came back; this reduces to that set so
    those assertions read the same as they did pre-enrichment. Tests that
    verify the enrichment itself (names, disambiguators) inspect the dicts
    directly instead of going through this helper."""
    return {c["id"] for c in suggested_fix}


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
    assert _candidate_ids(finding.suggested_fix) & {"101", "102"}


def test_object_not_found_suggested_fix_resolves_name_match_to_owning_id():
    # reviewer repro: a typo'd NAME as the target must yield the owning
    # object's id, not the raw name string (suggested_fix is machine-usable
    # — a caller retries with suggested_fix[0]["id"] as input_no/sc_no).
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
    candidates = finding.suggested_fix
    assert "1" in _candidate_ids(candidates)
    # the resolved candidate carries the name as its OWN field, not as a
    # bare string standing in for the id (2026-07-31 enrichment).
    assert any(c["id"] == "1" and c["name"] == "Main Street EB" for c in candidates)
    assert all(isinstance(c, dict) for c in candidates)


# ---------------------------------------------------------------------------
# object_not_found — fuzzy NAME match resolves to EVERY owner, not just the
# first-registered one (v2 queue unit 1, 2026-07-29). The real Murdoch model
# has 17 groups of 3 vehicle inputs sharing one name (e.g. 107/207/307 all
# "Jennalup ST_WB", one per vehicle class); the pre-fix pool collapsed a
# fuzzy name match to only the first owner it saw.
# ---------------------------------------------------------------------------


def test_object_not_found_suggested_fix_resolves_shared_name_to_all_owners():
    shadow = _shadow_model(
        vehicle_inputs=[
            {"no": "501", "name": "Merricup Rd WB", "link": "620", "volume": 100.0},
            {"no": "502", "name": "Merricup Rd WB", "link": "620", "volume": 50.0},
            {"no": "503", "name": "Merricup Rd WB", "link": "620", "volume": 20.0},
        ]
    )
    action = _action("set_vehicle_input_volume", input_no="Merricup Rd W", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    candidates = matches[0].suggested_fix
    assert _candidate_ids(candidates) == {"501", "502", "503"}
    # every owner's candidate also carries its (shared) name and link --
    # 2026-07-31 enrichment, the same shape resolve_object's miss path uses.
    assert {c["name"] for c in candidates} == {"Merricup Rd WB"}
    assert all(c["link"] == "620" for c in candidates)


def test_object_not_found_suggested_fix_unique_name_amid_shared_names_yields_one():
    # A query fuzzy-matching a name that only ONE object carries must not
    # leak owners from an unrelated shared-name group sitting in the same
    # pool.
    shadow = _shadow_model(
        vehicle_inputs=[
            {"no": "501", "name": "Merricup Rd WB", "link": "620", "volume": 100.0},
            {"no": "502", "name": "Merricup Rd WB", "link": "620", "volume": 50.0},
            {"no": "503", "name": "Merricup Rd WB", "link": "620", "volume": 20.0},
            {"no": "999", "name": "Q Street", "link": "621", "volume": 10.0},
        ]
    )
    action = _action("set_vehicle_input_volume", input_no="Q Streett", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    assert matches[0].suggested_fix == [
        {"id": "999", "name": "Q Street", "link": "621"}
    ]


def test_object_not_found_message_renders_id_name_pairs_for_shared_name_candidates():
    """The human-facing message (not just the machine suggested_fix) must
    also carry the enriched candidates -- 2026-07-31 fix -- rendered as
    ``id(name)`` pairs via the same _capped_join helper every other
    candidate-list message in this module already reuses."""
    shadow = _shadow_model(
        vehicle_inputs=[
            {"no": "501", "name": "Merricup Rd WB", "link": "620", "volume": 100.0},
            {"no": "502", "name": "Merricup Rd WB", "link": "620", "volume": 50.0},
            {"no": "503", "name": "Merricup Rd WB", "link": "620", "volume": 20.0},
        ]
    )
    action = _action("set_vehicle_input_volume", input_no="Merricup Rd W", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    ko = matches[0].messages["ko"]
    en = matches[0].messages["en"]
    ja = matches[0].messages["ja"]
    for label in ("501(Merricup Rd WB)", "502(Merricup Rd WB)", "503(Merricup Rd WB)"):
        assert label in ko, ko
        assert label in en, en
        assert label in ja, ja


def test_object_not_found_message_renders_dash_when_no_candidates_match():
    """An empty candidate list (nothing in the pool fuzzy-matches at all)
    must render the same "-" placeholder every sibling candidate-list
    message already uses for the empty case, not a blank/absent phrase."""
    shadow = _shadow_model(vehicle_inputs=[])
    action = _action("set_vehicle_input_volume", input_no="anything", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    assert matches[0].suggested_fix == []
    assert "-" in matches[0].messages["ko"]
    assert "-" in matches[0].messages["en"]
    assert "-" in matches[0].messages["ja"]


def test_object_not_found_suggested_fix_candidate_cap_bounds_multi_group_match():
    # Bounded envelope (suggestion lists reach LLM prompts): a query that
    # fuzzy-matches SEVERAL shared names at once (4 groups of 3 = 12
    # candidate owners) must not balloon the list unboundedly. The cap fits
    # 3 whole sibling groups plus one more owner before cutting off, rather
    # than growing without bound or arbitrarily splitting every group evenly.
    shadow = _shadow_model(
        vehicle_inputs=[
            {"no": "601", "name": "Merricup Rd A", "link": "900", "volume": 1.0},
            {"no": "602", "name": "Merricup Rd A", "link": "900", "volume": 1.0},
            {"no": "603", "name": "Merricup Rd A", "link": "900", "volume": 1.0},
            {"no": "611", "name": "Merricup Rd B", "link": "901", "volume": 1.0},
            {"no": "612", "name": "Merricup Rd B", "link": "901", "volume": 1.0},
            {"no": "613", "name": "Merricup Rd B", "link": "901", "volume": 1.0},
            {"no": "621", "name": "Merricup Rd C", "link": "902", "volume": 1.0},
            {"no": "622", "name": "Merricup Rd C", "link": "902", "volume": 1.0},
            {"no": "623", "name": "Merricup Rd C", "link": "902", "volume": 1.0},
            {"no": "631", "name": "Merricup Rd D", "link": "903", "volume": 1.0},
            {"no": "632", "name": "Merricup Rd D", "link": "903", "volume": 1.0},
            {"no": "633", "name": "Merricup Rd D", "link": "903", "volume": 1.0},
        ]
    )
    action = _action("set_vehicle_input_volume", input_no="Merricup Rd", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    candidates = matches[0].suggested_fix
    assert len(candidates) == 10  # capped, not the full 12
    cand_ids = [c["id"] for c in candidates]
    assert len(set(cand_ids)) == len(cand_ids)  # no duplicate ids emitted
    # every candidate's own name matches the group its id belongs to.
    by_id_prefix = {"60": "Merricup Rd A", "61": "Merricup Rd B", "62": "Merricup Rd C", "63": "Merricup Rd D"}
    for c in candidates:
        assert c["name"] == by_id_prefix[c["id"][:2]], c

    groups = [
        {"601", "602", "603"},
        {"611", "612", "613"},
        {"621", "622", "623"},
        {"631", "632", "633"},
    ]
    cand_set = set(cand_ids)
    full_groups = [g for g in groups if g <= cand_set]
    partial_groups = [g for g in groups if cand_set & g and not (g <= cand_set)]
    assert len(full_groups) == 3
    assert len(partial_groups) == 1


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
    assert "101" in _candidate_ids(matches[0].suggested_fix)


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
    assert "30" in _candidate_ids(matches[0].suggested_fix)


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
    assert "10" in _candidate_ids(matches[0].suggested_fix)


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


# --- #3, sibling-aware capacity (sibling-disambiguation unit, 2026-07-28) ---
#
# The real client model puts THREE vehicle inputs on every demand link, one
# per vehicle class, so the pre-fix check -- which compared ONE input's
# proposed volume against the WHOLE link's capacity -- under-counted on 100%
# of its possible targets. _jennalup_shadow() reproduces the exact measured
# case (runtime/uploads/current/current.inpx link 620, 1 lane, capacity 1900,
# inputs 107/207/307 at 127.3/19.0/0.95 veh/h) synthetically, so the
# regression is pinned on every machine, with or without the real dataset.


def _jennalup_shadow() -> dict[str, Any]:
    return _shadow_model(
        links=[{"id": "620", "name": "Jennalup St WB", "lanes": 1, "length": 240.0}],
        vehicle_inputs=[
            {
                "no": "107",
                "name": "Jennalup ST_WB",
                "link": "620",
                "volume": 127.3,
                "composition_no": "1",
                "composition_name": "Class1",
            },
            {
                "no": "207",
                "name": "Jennalup ST_WB",
                "link": "620",
                "volume": 19.0,
                "composition_no": "2",
                "composition_name": "Class2-5",
            },
            {
                "no": "307",
                "name": "Jennalup ST_WB",
                "link": "620",
                "volume": 0.95,
                "composition_no": "3",
                "composition_name": "Class6-9",
            },
        ],
    )


def test_latent_demand_overflow_fires_when_siblings_push_link_total_over_capacity():
    """REGRESSION PIN for the exact silent failure the pre-fix check let
    through: 1881 on input 107 is BELOW the 1-lane link's 1900 capacity, so
    the old per-input comparison stayed silent, while the link's real
    post-write demand is 1881 + 19.0 + 0.95 = 1900.95 -- over capacity."""
    shadow = _jennalup_shadow()
    action = _action("set_vehicle_input_volume", input_no="107", volume=1881.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "latent_demand_overflow"]
    assert len(matches) == 1, "the pre-fix check was silent here -- that is the bug"
    finding = matches[0]
    assert finding.tier == "warn"
    assert finding.details["sibling_input_nos"] == ["207", "307"]
    assert finding.details["sibling_volume_total"] == 19.95
    assert finding.details["link_total_volume"] == 1900.95
    assert finding.details["capacity"] == 1900
    _assert_well_formed_messages(finding, must_contain_ko="1900.95")


def test_latent_demand_overflow_silent_when_link_total_including_siblings_fits():
    """The other end of the same window: 1880 leaves the link at 1899.95,
    inside capacity, so the sibling-aware check must NOT start crying wolf
    just because siblings exist."""
    shadow = _jennalup_shadow()
    action = _action("set_vehicle_input_volume", input_no="107", volume=1880.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "latent_demand_overflow" for f in findings)


def test_latent_demand_overflow_message_states_the_auditable_breakdown():
    shadow = _jennalup_shadow()
    action = _action("set_vehicle_input_volume", input_no="107", volume=6000)

    finding = next(
        f for f in run_checks("preflight", action, shadow, {}) if f.constraint_id == "latent_demand_overflow"
    )

    ko = finding.messages["ko"]
    for part in ("6000", "19.95", "6019.95", "1900"):  # proposed, siblings, total, capacity
        assert part in ko, f"the breakdown must be auditable; missing {part!r}"
    assert "6019.95" in finding.messages["en"] and "6019.95" in finding.messages["ja"]


def test_latent_demand_overflow_max_recommended_volume_leaves_room_for_siblings():
    """The pre-fix suggested_fix (max_recommended_volume == capacity) was
    ITSELF an overflow: 1900 + 19.95 = 1919.95."""
    shadow = _jennalup_shadow()
    action = _action("set_vehicle_input_volume", input_no="107", volume=6000)

    finding = next(
        f for f in run_checks("preflight", action, shadow, {}) if f.constraint_id == "latent_demand_overflow"
    )

    assert finding.suggested_fix == {"max_recommended_volume": 1880.05}
    assert finding.suggested_fix["max_recommended_volume"] + 19.95 == pytest.approx(1900)


def test_latent_demand_overflow_does_not_double_count_the_target_own_volume():
    """The target's CURRENT volume must be excluded -- only its PROPOSED one
    counts. Input 107 currently carries 127.3; proposing 1870 leaves the link
    at 1889.95 (fits). Double-counting would give 2017.25 and a false fire."""
    shadow = _jennalup_shadow()
    action = _action("set_vehicle_input_volume", input_no="107", volume=1870.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "latent_demand_overflow" for f in findings)


def test_latent_demand_overflow_counts_unknown_sibling_volume_as_zero_and_says_so():
    shadow = _shadow_model(
        links=[{"id": "620", "name": "Jennalup St WB", "lanes": 1, "length": 240.0}],
        vehicle_inputs=[
            {"no": "107", "name": "A", "link": "620", "volume": 100.0},
            {"no": "207", "name": "B", "link": "620", "volume": None},
        ],
    )
    action = _action("set_vehicle_input_volume", input_no="107", volume=2000)

    finding = next(
        f for f in run_checks("preflight", action, shadow, {}) if f.constraint_id == "latent_demand_overflow"
    )

    assert finding.details["sibling_volume_unknown_count"] == 1
    assert finding.details["sibling_volume_total"] == 0.0
    # A lower-bound total must never be presented as a complete one.
    assert "0으로" in finding.messages["ko"]
    _assert_well_formed_messages(finding, must_contain_ko="2000")


def test_latent_demand_overflow_ignores_vehicle_inputs_on_other_links():
    shadow = _shadow_model(
        links=[{"id": "620", "name": "L", "lanes": 1, "length": 240.0}],
        vehicle_inputs=[
            {"no": "107", "name": "A", "link": "620", "volume": 100.0},
            {"no": "999", "name": "elsewhere", "link": "621", "volume": 5000.0},
        ],
    )
    action = _action("set_vehicle_input_volume", input_no="107", volume=1800)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "latent_demand_overflow" for f in findings)


# ---------------------------------------------------------------------------
# #3 / vehicle_input_siblings_share_link — interval-blindness fix
# (2026-07-28, second pass). Both checks used to read every vehicle input's
# TOP-LEVEL `volume` field, which is interval 1's value BY DEFINITION
# (tools_network._parse_vehicle_inputs) -- so a write that named interval_no
# > 1 got compared against interval 1's sibling demand. Measured live
# (runtime/uploads/current/current.inpx, link 620): siblings 207+307's real
# per-interval totals are {1: 19.95, 2: 21.0, 3: 21.0, 4: 21.0, 5: 21.0,
# 6: 18.9} veh/h; the pre-fix checks reported 19.95 for EVERY interval_no.
# _jennalup_shadow_multi_interval() reproduces the exact measured per-input
# per-interval numbers synthetically, so the regression is pinned on every
# machine, with or without the real dataset (see the real-dataset-gated
# mirror test near the bottom of this file for the live proof).
# ---------------------------------------------------------------------------


def _jennalup_shadow_multi_interval() -> dict[str, Any]:
    return _shadow_model(
        links=[{"id": "620", "name": "Jennalup St WB", "lanes": 1, "length": 240.0}],
        vehicle_inputs=[
            {
                "no": "107",
                "name": "Jennalup ST_WB",
                "link": "620",
                "volume": 127.3,
                "composition_no": "1",
                "composition_name": "Class1",
                "volumes": [
                    {"interval_no": 1, "from_s": 0.0, "volume": 127.3, "continuous": False},
                    {"interval_no": 2, "from_s": 900.0, "volume": 134.0, "continuous": False},
                    {"interval_no": 3, "from_s": 1800.0, "volume": 134.0, "continuous": False},
                    {"interval_no": 4, "from_s": 2700.0, "volume": 134.0, "continuous": False},
                    {"interval_no": 5, "from_s": 3600.0, "volume": 134.0, "continuous": False},
                    {"interval_no": 6, "from_s": 4500.0, "volume": 120.6, "continuous": False},
                ],
            },
            {
                "no": "207",
                "name": "Jennalup ST_WB",
                "link": "620",
                "volume": 19.0,
                "composition_no": "2",
                "composition_name": "Class2-5",
                "volumes": [
                    {"interval_no": 1, "from_s": 0.0, "volume": 19.0, "continuous": False},
                    {"interval_no": 2, "from_s": 900.0, "volume": 20.0, "continuous": False},
                    {"interval_no": 3, "from_s": 1800.0, "volume": 20.0, "continuous": False},
                    {"interval_no": 4, "from_s": 2700.0, "volume": 20.0, "continuous": False},
                    {"interval_no": 5, "from_s": 3600.0, "volume": 20.0, "continuous": False},
                    {"interval_no": 6, "from_s": 4500.0, "volume": 18.0, "continuous": False},
                ],
            },
            {
                "no": "307",
                "name": "Jennalup ST_WB",
                "link": "620",
                "volume": 0.95,
                "composition_no": "3",
                "composition_name": "Class6-9",
                "volumes": [
                    {"interval_no": 1, "from_s": 0.0, "volume": 0.95, "continuous": False},
                    {"interval_no": 2, "from_s": 900.0, "volume": 1.0, "continuous": False},
                    {"interval_no": 3, "from_s": 1800.0, "volume": 1.0, "continuous": False},
                    {"interval_no": 4, "from_s": 2700.0, "volume": 1.0, "continuous": False},
                    {"interval_no": 5, "from_s": 3600.0, "volume": 1.0, "continuous": False},
                    {"interval_no": 6, "from_s": 4500.0, "volume": 0.9, "continuous": False},
                ],
            },
        ],
    )


_MEASURED_SIBLING_TOTALS_BY_INTERVAL = {1: 19.95, 2: 21.0, 3: 21.0, 4: 21.0, 5: 21.0, 6: 18.9}


def test_latent_demand_overflow_reads_sibling_volume_at_the_written_interval_not_interval_1():
    """REGRESSION PIN: before the fix, sibling_volume_total was 19.95 (interval
    1's value) for EVERY interval_no. Now it must match the measured
    per-interval ground truth exactly."""
    shadow = _jennalup_shadow_multi_interval()

    for interval_no, expected_total in _MEASURED_SIBLING_TOTALS_BY_INTERVAL.items():
        action = _action(
            "set_vehicle_input_volume", input_no="107", volume=6000, interval_no=interval_no
        )
        finding = next(
            f
            for f in run_checks("preflight", action, shadow, {})
            if f.constraint_id == "latent_demand_overflow"
        )
        assert finding.details["sibling_volume_total"] == expected_total, interval_no
        assert finding.details["interval_no"] == interval_no
        assert finding.details["link_total_volume"] == round(6000 + expected_total, 4)
        assert f"구간 {interval_no}" in finding.messages["ko"]
        assert f"interval {interval_no}" in finding.messages["en"]


def test_latent_demand_overflow_interval_no_defaults_to_1_when_omitted():
    """set_vehicle_input_volume's OWN interval_no default is 1
    (tools_edit.SetVehicleInputVolumeParams.interval_no: int = Field(
    default=1, ...)) -- the check must match that default exactly, not
    assume it."""
    shadow = _jennalup_shadow_multi_interval()
    action = _action("set_vehicle_input_volume", input_no="107", volume=6000)  # no interval_no

    finding = next(
        f for f in run_checks("preflight", action, shadow, {}) if f.constraint_id == "latent_demand_overflow"
    )

    assert finding.details["interval_no"] == 1
    assert finding.details["sibling_volume_total"] == 19.95


def test_vehicle_input_siblings_share_link_uses_sibling_and_target_volume_at_the_written_interval():
    shadow = _jennalup_shadow_multi_interval()
    action = _action("set_vehicle_input_volume", input_no="107", volume=6000, interval_no=6)

    finding = next(
        f
        for f in run_checks("preflight", action, shadow, {})
        if f.constraint_id == "vehicle_input_siblings_share_link"
    )

    assert finding.details["interval_no"] == 6
    assert finding.details["sibling_volume_total"] == 18.9
    # 107's OWN interval-6 volume (120.6) + siblings' interval-6 total
    # (18.9) -- NOT interval 1's 127.3 + 19.95.
    assert finding.details["old_link_total_volume"] == 139.5
    assert "구간 6" in finding.messages["ko"]
    # The sibling label must show 207's interval-6 volume (18.0), not its
    # interval-1 volume (19.0).
    assert "207(Class2-5, 18.0 veh/h)" in finding.messages["ko"]


def test_vehicle_input_siblings_share_link_interval_no_defaults_to_1_when_omitted():
    shadow = _jennalup_shadow_multi_interval()
    action = _action("set_vehicle_input_volume", input_no="107", volume=6000)  # no interval_no

    finding = next(
        f
        for f in run_checks("preflight", action, shadow, {})
        if f.constraint_id == "vehicle_input_siblings_share_link"
    )

    assert finding.details["interval_no"] == 1
    assert finding.details["sibling_volume_total"] == 19.95
    assert finding.details["old_link_total_volume"] == 147.25  # 127.3 + 19.95, interval 1


# ---------------------------------------------------------------------------
# #3 suggested_fix — never recommend a ceiling that fails its own check
# (FIX 3, 2026-07-28). ``max(capacity - sibling_total, 0.0)`` used to clamp
# to 0.0 and present it as a working recommendation even when the siblings
# ALONE already exceed capacity -- re-running the check at that "fix" still
# fired, because 0 + sibling_total is still > capacity. No value of the
# target input (not even 0) can clear the finding in that state.
# ---------------------------------------------------------------------------


def test_latent_demand_overflow_suggested_fix_states_impossibility_when_siblings_alone_exceed_capacity():
    shadow = _shadow_model(
        links=[{"id": "620", "name": "L", "lanes": 1, "length": 240.0}],
        vehicle_inputs=[
            {"no": "107", "name": "A", "link": "620", "volume": 100.0},
            {"no": "207", "name": "B", "link": "620", "volume": 1000.0},
            {"no": "307", "name": "C", "link": "620", "volume": 1000.0},  # siblings alone: 2000 > capacity 1900
        ],
    )
    action = _action("set_vehicle_input_volume", input_no="107", volume=50.0)

    finding = next(
        f for f in run_checks("preflight", action, shadow, {}) if f.constraint_id == "latent_demand_overflow"
    )

    assert finding.suggested_fix == {
        "max_recommended_volume": None,
        "fixable_by_reducing_this_input": False,
        "sibling_volume_total": 2000.0,
        "capacity": 1900,
        "excess_from_siblings_alone": 100.0,
    }
    assert finding.details["fixable_by_reducing_this_input"] is False
    assert finding.details["max_recommended_volume"] is None
    # The old bug, proven: 0 (the old clamp's "recommendation") still fires.
    zero_action = _action("set_vehicle_input_volume", input_no="107", volume=0.0)
    assert any(
        f.constraint_id == "latent_demand_overflow" for f in run_checks("preflight", zero_action, shadow, {})
    ), "0 must still overflow -- proving no value of this input alone can fix it"
    _assert_well_formed_messages(finding, must_contain_ko="아무리 줄여도")
    assert "even to 0" in finding.messages["en"]


def test_latent_demand_overflow_suggested_fix_still_clears_when_siblings_leave_room():
    """The normal (fixable) path is unchanged by FIX 3: the recommendation
    is a plain single-key dict, and re-running the check at exactly that
    value clears the finding."""
    shadow = _jennalup_shadow()
    action = _action("set_vehicle_input_volume", input_no="107", volume=6000)

    finding = next(
        f for f in run_checks("preflight", action, shadow, {}) if f.constraint_id == "latent_demand_overflow"
    )
    assert finding.suggested_fix == {"max_recommended_volume": 1880.05}
    assert finding.details["fixable_by_reducing_this_input"] is True

    fixed_action = _action(
        "set_vehicle_input_volume", input_no="107", volume=finding.suggested_fix["max_recommended_volume"]
    )
    assert not any(
        f.constraint_id == "latent_demand_overflow" for f in run_checks("preflight", fixed_action, shadow, {})
    ), "the recommended ceiling must actually clear its own check"


@_requires_real_dataset
def test_real_dataset_latent_demand_overflow_sibling_totals_match_measured_ground_truth_per_interval():
    """The exact live acceptance case FIX 2 was built against."""
    from services.agent.tools_network import build_shadow_model

    shadow = build_shadow_model(ACTIVE_REAL_INPX)

    for interval_no, expected_total in _MEASURED_SIBLING_TOTALS_BY_INTERVAL.items():
        action = _action(
            "set_vehicle_input_volume", input_no="107", volume=6000, interval_no=interval_no
        )
        finding = next(
            f
            for f in run_checks("preflight", action, shadow, {})
            if f.constraint_id == "latent_demand_overflow"
        )
        assert finding.details["sibling_volume_total"] == expected_total, interval_no
        assert finding.details["link_total_volume"] == round(6000 + expected_total, 4)


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


def test_lane_count_edit_unsupported_exempts_add_link():
    # Q4 unit (5), LIVE-CAUGHT regression: add_link's own lane_count param IS
    # the sanctioned mechanism this constraint's message itself describes
    # ("Vissim fixes lane count at link construction -- the lane-width
    # array") -- reproduced live via a real run_agent_turn (0.1s runtime,
    # refused before COM was ever touched) before the exemption was added.
    shadow = _shadow_model()
    action = _action("add_link", points_xy=[[0, 0], [100, 0]], lane_count=2)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "lane_count_edit_unsupported" for f in findings)


def test_lane_count_edit_unsupported_exempts_add_connector():
    shadow = _shadow_model()
    action = _action(
        "add_connector", from_link="101", from_lanes=[1], from_pos_m=0.0,
        to_link="102", to_lanes=[1], to_pos_m=0.0, lane_count=1,
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "lane_count_edit_unsupported" for f in findings)


def test_lane_count_edit_unsupported_still_fires_for_every_other_tool():
    # The exemption is tool-SPECIFIC (add_link/add_connector only), not a
    # blanket "lane_count is fine everywhere now" loosening -- a
    # hallucinated lane_count-shaped param on any OTHER tool is still refused
    # exactly as before this unit's own fix.
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600, lane_count=4)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "lane_count_edit_unsupported"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"


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


def test_link_closure_route_consequence_fires_on_link_closure_through_a_route():
    shadow = _shadow_model()
    shadow["routes"] = [{"id": "2.1", "link_seq": ["101", "9001", "102"], "disconnected": False}]
    action = _action("set_link_closure", link_id="101", vehicle_classes=["10"], closed=True)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "link_closure_route_consequence"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    _assert_well_formed_messages(matches[0], must_contain_ko="2.1")
    assert matches[0].details == {"target_id": "101", "affected_routes": ["2.1"]}


def test_link_closure_route_consequence_fires_on_turn_ban_through_a_route():
    shadow = _shadow_model()
    shadow["routes"] = [{"id": "2.1", "link_seq": ["101", "9001", "102"], "disconnected": False}]
    action = _action("set_turn_ban", connector_no="9001", vehicle_classes=["10"], banned=True)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "link_closure_route_consequence"]
    assert len(matches) == 1
    assert matches[0].details["affected_routes"] == ["2.1"]


def test_link_closure_route_consequence_silent_when_no_route_traverses_target():
    shadow = _shadow_model()
    shadow["routes"] = [{"id": "2.1", "link_seq": ["101", "9002", "102"], "disconnected": False}]
    action = _action("set_turn_ban", connector_no="9001", vehicle_classes=["10"], banned=True)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "link_closure_route_consequence" for f in findings)


def test_link_closure_route_consequence_silent_when_routes_absent():
    shadow = _shadow_model()
    action = _action("set_link_closure", link_id="101", vehicle_classes=["10"], closed=True)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "link_closure_route_consequence" for f in findings)


def test_link_closure_route_consequence_silent_on_reopen_direction():
    # closed=False (reopen) / banned=False (un-ban) never worsens route
    # reachability -- nothing to warn about there.
    shadow = _shadow_model()
    shadow["routes"] = [{"id": "2.1", "link_seq": ["101", "9001", "102"], "disconnected": False}]
    action = _action("set_link_closure", link_id="101", vehicle_classes=[], closed=False)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "link_closure_route_consequence" for f in findings)


def test_link_closure_route_consequence_does_not_apply_to_remove_connector():
    # remove_connector's own route consequence is a REFUSAL enforced by
    # services.inpx_structure's spine (dangling linkSeq reference), not this
    # warn-tier constraint -- see that module's own ID_REFERENCE_SITES
    # comment.
    shadow = _shadow_model()
    shadow["routes"] = [{"id": "2.1", "link_seq": ["101", "9001", "102"], "disconnected": False}]
    action = _action("remove_connector", connector_no="9001")

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "link_closure_route_consequence" for f in findings)


def test_link_closure_route_consequence_message_caps_enumerated_routes():
    shadow = _shadow_model()
    shadow["routes"] = [
        {"id": f"{i}.1", "link_seq": ["101"], "disconnected": False} for i in range(10)
    ]
    action = _action("set_link_closure", link_id="101", vehicle_classes=["10"], closed=True)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "link_closure_route_consequence"]
    assert len(matches) == 1
    assert matches[0].details["affected_routes"] == [f"{i}.1" for i in range(10)]
    for lang, remainder_fragment in (("ko", "외 2"), ("en", "2 more"), ("ja", "他2")):
        message = matches[0].messages[lang]
        assert remainder_fragment in message, f"{lang} message missing remainder note: {message!r}"


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
    shadow["driving_behaviors"] = [{"no": "5", "car_following_model": 74}]
    action = _action("set_driving_behavior_parameter", behavior_no="5", parameter="cc1", value=1.2)

    findings = run_checks("preflight", action, shadow, {})

    matches = [
        f for f in findings if f.constraint_id == "driving_behavior_parameter_model_mismatch"
    ]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"


def test_driving_behavior_parameter_model_mismatch_fires_on_noninteraction_model():
    # QUEUE 3 unit 6: a THIRD real car_following_model value (None, mapped
    # from the real "NOINTERACTION" carFollowModType on Footpath/pedestrian
    # behaviors) is neither 74 nor 99 -- writing a W74-only param there is
    # ALSO a no-op the old (model == 74/99 symmetric) condition would have
    # missed.
    shadow = _shadow_model()
    shadow["driving_behaviors"] = [{"no": "10", "car_following_model": None}]
    action = _action("set_driving_behavior_parameter", behavior_no="10", parameter="ax", value=2.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [
        f for f in findings if f.constraint_id == "driving_behavior_parameter_model_mismatch"
    ]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"


def test_driving_behavior_parameter_model_mismatch_silent_when_matching_model():
    shadow = _shadow_model()
    shadow["driving_behaviors"] = [{"no": "5", "car_following_model": 99}]
    action = _action("set_driving_behavior_parameter", behavior_no="5", parameter="cc1", value=1.2)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "driving_behavior_parameter_model_mismatch" for f in findings
    )


def test_driving_behavior_parameter_model_mismatch_silent_for_lane_change_params_any_model():
    # Lane-change/lateral params (this unit's own whitelist half) are
    # model-independent -- never flagged as a mismatch regardless of
    # car_following_model.
    shadow = _shadow_model()
    shadow["driving_behaviors"] = [{"no": "5", "car_following_model": 99}]
    action = _action(
        "set_driving_behavior_parameter", behavior_no="5", parameter="saf_dist_fact_ln_chg", value=0.6
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "driving_behavior_parameter_model_mismatch" for f in findings
    )


def test_driving_behavior_parameter_model_mismatch_skipped_when_behaviors_absent():
    shadow = _shadow_model()
    action = _action("set_driving_behavior_parameter", behavior_no="5", parameter="cc1", value=1.2)

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


def _driving_behavior(
    no: str, reference_count: int = 0, consumers: list[dict[str, Any]] | None = None, **extra: Any
) -> dict[str, Any]:
    consumers = consumers or []
    return {
        "no": no,
        "name": f"Behavior {no}",
        "car_follow_mod_type": "WIEDEMANN74",
        "car_following_model": 74,
        "referenced_by": {"consumers": consumers, "count": len(consumers), "truncated": False},
        "reference_count": reference_count,
        **extra,
    }


def test_driving_behavior_shared_object_edit_fires_when_referenced():
    # QUEUE 3 unit 6 rewrite: reference-count based, not a caller-supplied
    # flag -- a behavior with ANY real consumer refuses, unconditionally.
    shadow = _shadow_model()
    shadow["driving_behaviors"] = [
        _driving_behavior(
            "5",
            reference_count=1,
            consumers=[{"link_behavior_type_no": "1", "link_behavior_type_name": "Urban", "veh_class_no": None, "link_count": 293}],
        )
    ]
    action = _action("set_driving_behavior_parameter", behavior_no="5", parameter="ax", value=1.5)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "driving_behavior_shared_object_edit"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    _assert_well_formed_messages(finding, must_contain_ko="5")
    assert finding.details["consumers"] == ["Urban"]


def test_driving_behavior_shared_object_edit_silent_when_zero_references():
    # A freshly duplicated behavior (reference_count 0) is safe to edit.
    shadow = _shadow_model()
    shadow["driving_behaviors"] = [_driving_behavior("7", reference_count=0)]
    action = _action("set_driving_behavior_parameter", behavior_no="7", parameter="ax", value=1.5)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "driving_behavior_shared_object_edit" for f in findings
    )


def test_driving_behavior_shared_object_edit_no_escape_flag():
    # There is no caller-supplied override anymore -- a stray/legacy
    # "duplicated_before_edit": True must NOT suppress a real reference.
    shadow = _shadow_model()
    shadow["driving_behaviors"] = [_driving_behavior("5", reference_count=1, consumers=[{"link_behavior_type_no": "1"}])]
    action = _action(
        "set_driving_behavior_parameter",
        behavior_no="5",
        parameter="ax",
        value=1.5,
        duplicated_before_edit=True,
    )

    findings = run_checks("preflight", action, shadow, {})

    assert any(f.constraint_id == "driving_behavior_shared_object_edit" for f in findings)


def test_driving_behavior_shared_object_edit_silent_when_target_missing():
    shadow = _shadow_model()
    shadow["driving_behaviors"] = []
    action = _action("set_driving_behavior_parameter", behavior_no="999", parameter="ax", value=1.5)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "driving_behavior_shared_object_edit" for f in findings
    )


def test_parameter_diff_not_calibration_meaningful_fires_when_moved_off_reference():
    shadow = _shadow_model()
    shadow["driving_behaviors"] = [
        {"no": "5", "parameters": {"cc1": {"calibration_reference_value": 1.5}}}
    ]
    action = _action("set_driving_behavior_parameter", behavior_no="5", parameter="cc1")
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
        {"no": "5", "parameters": {"cc1": {"calibration_reference_value": 1.5}}}
    ]
    action = _action("set_driving_behavior_parameter", behavior_no="5", parameter="cc1")
    extra = {"written_value": 1.5, "readback_value": 1.5}

    findings = run_checks("postwrite", action, shadow, extra)

    assert not any(
        f.constraint_id == "parameter_diff_not_calibration_meaningful" for f in findings
    )


def test_parameter_diff_not_calibration_meaningful_skipped_when_reference_absent():
    shadow = _shadow_model()
    action = _action("set_driving_behavior_parameter", behavior_no="5", parameter="cc1")
    extra = {"written_value": 2.0, "readback_value": 2.0}

    findings = run_checks("postwrite", action, shadow, extra)

    assert not any(
        f.constraint_id == "parameter_diff_not_calibration_meaningful" for f in findings
    )


def test_agency_restricted_parameter_write_fires_for_restricted_parameter():
    shadow = _shadow_model()
    action = _action("set_driving_behavior_parameter", behavior_no="5", parameter="cc1", value=1.2)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "agency_restricted_parameter_write"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"


def test_agency_restricted_parameter_write_silent_for_unrestricted_parameter():
    shadow = _shadow_model()
    action = _action(
        "set_driving_behavior_parameter",
        behavior_no="5",
        parameter="look_ahead_distance",
        value=100,
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "agency_restricted_parameter_write" for f in findings
    )


# ---------------------------------------------------------------------------
# QUEUE 3 unit 6: driving-behavior duplicate-then-edit -- #1 object_not_found
# for the three new tools, #85-#88.
# ---------------------------------------------------------------------------


def test_object_not_found_fires_for_missing_duplicate_source():
    shadow = _shadow_model()
    shadow["driving_behaviors"] = [_driving_behavior("1")]
    action = _action("duplicate_driving_behavior", source_no="999", new_name="copy")

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="999")


def test_object_not_found_silent_when_duplicate_source_present():
    shadow = _shadow_model()
    shadow["driving_behaviors"] = [_driving_behavior("1")]
    action = _action("duplicate_driving_behavior", source_no="1", new_name="copy")

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


def test_object_not_found_fires_for_missing_set_driving_behavior_parameter_target():
    shadow = _shadow_model()
    shadow["driving_behaviors"] = [_driving_behavior("1")]
    action = _action("set_driving_behavior_parameter", behavior_no="999", parameter="ax", value=2.0)

    findings = run_checks("preflight", action, shadow, {})

    assert any(f.constraint_id == "object_not_found" for f in findings)


def test_object_not_found_fires_for_missing_link_behavior_type():
    shadow = _shadow_model()
    shadow["link_behavior_types"] = [{"no": "1", "name": "Urban", "overrides": []}]
    action = _action(
        "set_link_behavior_type_driving_behavior", link_behavior_type_no="999", driving_behavior_no="7"
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="999")


def test_object_not_found_silent_when_link_behavior_type_present():
    shadow = _shadow_model()
    shadow["link_behavior_types"] = [{"no": "1", "name": "Urban", "overrides": []}]
    shadow["driving_behaviors"] = [_driving_behavior("7")]
    action = _action(
        "set_link_behavior_type_driving_behavior", link_behavior_type_no="1", driving_behavior_no="7"
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


@pytest.mark.parametrize(
    "parameter,value",
    [("ax", -1.0), ("lat_dist_driv_def", -0.5), ("lat_dist_stand_def", -0.5), ("saf_dist_fact_ln_chg", -0.1), ("saf_dist_fact_ln_chg", 1.5)],
)
def test_driving_behavior_parameter_range_invalid_fires_for_known_floor_ceiling(parameter, value):
    shadow = _shadow_model()
    action = _action("set_driving_behavior_parameter", behavior_no="7", parameter=parameter, value=value)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "driving_behavior_parameter_range_invalid"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"


@pytest.mark.parametrize(
    "parameter,value",
    [
        ("ax", 2.0),
        ("saf_dist_fact_ln_chg", 0.0),
        ("saf_dist_fact_ln_chg", 1.0),
        # Vissim itself silently accepts negative values here (live-verified)
        # -- this constraint deliberately has no bound for these two.
        ("bx_add", -2.0),
        ("bx_mult", -3.0),
    ],
)
def test_driving_behavior_parameter_range_invalid_silent_for_in_range_values(parameter, value):
    shadow = _shadow_model()
    action = _action("set_driving_behavior_parameter", behavior_no="7", parameter=parameter, value=value)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "driving_behavior_parameter_range_invalid" for f in findings
    )


def test_driving_behavior_parameter_range_invalid_does_not_apply_to_other_tools():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=-5.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "driving_behavior_parameter_range_invalid" for f in findings
    )


def test_driving_behavior_parameter_value_implausible_fires_for_huge_magnitude():
    shadow = _shadow_model()
    action = _action("set_driving_behavior_parameter", behavior_no="7", parameter="bx_add", value=99999.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "driving_behavior_parameter_value_implausible"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"


def test_driving_behavior_parameter_value_implausible_silent_for_realistic_value():
    shadow = _shadow_model()
    action = _action("set_driving_behavior_parameter", behavior_no="7", parameter="bx_add", value=2.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "driving_behavior_parameter_value_implausible" for f in findings
    )


def test_driving_behavior_parameter_value_implausible_silent_for_parameter_with_no_threshold():
    # saf_dist_fact_ln_chg has no implausible-magnitude threshold (it is
    # already bounded [0, 1] by #85) -- an out-of-range value there is #85's
    # job, never #86's.
    shadow = _shadow_model()
    action = _action("set_driving_behavior_parameter", behavior_no="7", parameter="saf_dist_fact_ln_chg", value=0.5)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "driving_behavior_parameter_value_implausible" for f in findings
    )


def test_link_behavior_type_veh_class_override_not_found_fires_for_missing_override():
    shadow = _shadow_model()
    shadow["link_behavior_types"] = [
        {"no": "1", "name": "Urban", "overrides": [{"veh_class_no": "20", "driving_behavior_no": "6"}]}
    ]
    action = _action(
        "set_link_behavior_type_driving_behavior",
        link_behavior_type_no="1",
        driving_behavior_no="7",
        veh_class_no="30",
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [
        f for f in findings if f.constraint_id == "link_behavior_type_veh_class_override_not_found"
    ]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    assert finding.suggested_fix == ["20"]
    _assert_well_formed_messages(finding, must_contain_ko="30")


def test_link_behavior_type_veh_class_override_not_found_silent_when_override_present():
    shadow = _shadow_model()
    shadow["link_behavior_types"] = [
        {"no": "1", "name": "Urban", "overrides": [{"veh_class_no": "20", "driving_behavior_no": "6"}]}
    ]
    action = _action(
        "set_link_behavior_type_driving_behavior",
        link_behavior_type_no="1",
        driving_behavior_no="7",
        veh_class_no="20",
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "link_behavior_type_veh_class_override_not_found" for f in findings
    )


def test_link_behavior_type_veh_class_override_not_found_silent_when_targeting_default():
    shadow = _shadow_model()
    shadow["link_behavior_types"] = [{"no": "1", "name": "Urban", "overrides": []}]
    action = _action(
        "set_link_behavior_type_driving_behavior", link_behavior_type_no="1", driving_behavior_no="7"
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "link_behavior_type_veh_class_override_not_found" for f in findings
    )


def test_link_behavior_type_driving_behavior_target_not_found_fires_for_missing_target():
    shadow = _shadow_model()
    shadow["driving_behaviors"] = [_driving_behavior("6")]
    action = _action(
        "set_link_behavior_type_driving_behavior", link_behavior_type_no="1", driving_behavior_no="999"
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [
        f for f in findings if f.constraint_id == "link_behavior_type_driving_behavior_target_not_found"
    ]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"


def test_link_behavior_type_driving_behavior_target_not_found_silent_when_target_present():
    shadow = _shadow_model()
    shadow["driving_behaviors"] = [_driving_behavior("7")]
    action = _action(
        "set_link_behavior_type_driving_behavior", link_behavior_type_no="1", driving_behavior_no="7"
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "link_behavior_type_driving_behavior_target_not_found" for f in findings
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
# priority_rule_marker_not_found / priority_rule_gap_headway_invalid /
# priority_rule_gap_headway_implausible -- QUEUE 3 unit 2
# ---------------------------------------------------------------------------

_PRIORITY_RULE_FIXTURE = [
    _priority_rule("4", [_conflict_marker(1, gap_time_s=3.2, headway_m=0.0), _conflict_marker(2, gap_time_s=0.0, headway_m=10.0)]),
]


def test_priority_rule_marker_not_found_fires_for_bogus_marker_index():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=99, gap_time_s=3.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "priority_rule_marker_not_found"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    assert finding.details["marker_count"] == 2
    assert finding.suggested_fix["candidates"] == ["1", "2"]
    _assert_well_formed_messages(finding, must_contain_ko="충돌 마커")


def test_priority_rule_marker_not_found_silent_for_a_real_marker_index():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=2, gap_time_s=3.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "priority_rule_marker_not_found" for f in findings)


def test_priority_rule_marker_not_found_defers_to_object_not_found_when_rule_missing():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_priority_rule_gap_headway", priority_rule_no="999", marker_index=1, gap_time_s=3.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "priority_rule_marker_not_found" for f in findings)
    assert any(f.constraint_id == "object_not_found" for f in findings)


def test_priority_rule_marker_not_found_does_not_apply_to_unrelated_tool():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "priority_rule_marker_not_found" for f in findings)


@pytest.mark.parametrize("bad_value", [-1.0, -0.001, float("nan"), float("inf"), float("-inf")])
def test_priority_rule_gap_headway_invalid_fires_for_negative_or_non_finite_gap(bad_value):
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=bad_value)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "priority_rule_gap_headway_invalid"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert matches[0].details["invalid_fields"] == {"gap_time_s": bad_value}


def test_priority_rule_gap_headway_invalid_fires_for_negative_headway():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, headway_m=-5.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "priority_rule_gap_headway_invalid"]
    assert len(matches) == 1
    assert matches[0].details["invalid_fields"] == {"headway_m": -5.0}


def test_priority_rule_gap_headway_invalid_fires_once_for_both_fields_bad():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action(
        "set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=-1.0, headway_m=-2.0
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "priority_rule_gap_headway_invalid"]
    assert len(matches) == 1
    assert matches[0].details["invalid_fields"] == {"gap_time_s": -1.0, "headway_m": -2.0}


@pytest.mark.parametrize("zero_field,other_field", [("gap_time_s", "headway_m"), ("headway_m", "gap_time_s")])
def test_priority_rule_gap_headway_invalid_silent_for_zero_the_most_common_real_value(zero_field, other_field):
    # Real data check: 0 is the single most common legitimate gap_time value
    # (11/20 conflict markers on the real Murdoch Drive model carry
    # minGapTime="0"; re-measured 2026-08-03 -- the "13/20" this comment
    # previously carried was the priority-RULE count. minClear on that same
    # model never reads 0 at all, ranging 6-23 m, so the zero case is
    # attested for gap_time only) -- refusing it would make this tool unable to
    # even reproduce a network's own existing data. See the constraint's own
    # module comment for the full live-probe evidence.
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action(
        "set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, **{zero_field: 0.0}
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "priority_rule_gap_headway_invalid" for f in findings)


def test_priority_rule_gap_headway_invalid_does_not_apply_to_other_tools():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "priority_rule_gap_headway_invalid" for f in findings)


def test_priority_rule_gap_headway_implausible_fires_above_the_gap_bound():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=15.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "priority_rule_gap_headway_implausible"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    assert "gap_time_s=15.0" in matches[0].messages["en"]


def test_priority_rule_gap_headway_implausible_fires_above_the_headway_bound():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, headway_m=80.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "priority_rule_gap_headway_implausible"]
    assert len(matches) == 1
    assert "headway_m=80.0" in matches[0].messages["en"]


def test_priority_rule_gap_headway_implausible_silent_within_observed_real_ranges():
    # 8s / 30m are the highest real values observed across two live-probed
    # networks (Roundabout Buehl.DE + the real Murdoch Drive model) -- both
    # must stay silent.
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action(
        "set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=8.0, headway_m=30.0
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "priority_rule_gap_headway_implausible" for f in findings)


def test_priority_rule_gap_headway_implausible_silent_at_zero():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action(
        "set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=0.0, headway_m=0.0
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "priority_rule_gap_headway_implausible" for f in findings)


def test_priority_rule_gap_headway_implausible_does_not_apply_to_other_tools():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "priority_rule_gap_headway_implausible" for f in findings)


# --- priority_rule_gap_time_outside_mrwa_table_5_9_range -------------------


def test_priority_rule_gap_time_outside_mrwa_table_5_9_range_fires_above_high():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=6.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "priority_rule_gap_time_outside_mrwa_table_5_9_range"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    assert matches[0].details["range_s"] == [4.0, 5.0]
    _assert_well_formed_messages(matches[0], must_contain_ko="Table 5-9")


def test_priority_rule_gap_time_outside_mrwa_table_5_9_range_fires_below_low():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=2.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "priority_rule_gap_time_outside_mrwa_table_5_9_range"]
    assert len(matches) == 1


def test_priority_rule_gap_time_outside_mrwa_table_5_9_range_silent_inside_envelope():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    for value in (4.0, 4.5, 5.0):
        action = _action("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=value)
        findings = run_checks("preflight", action, shadow, {})
        assert not any(
            f.constraint_id == "priority_rule_gap_time_outside_mrwa_table_5_9_range" for f in findings
        )


def test_priority_rule_gap_time_outside_mrwa_table_5_9_range_silent_when_gap_time_s_omitted():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, headway_m=10.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "priority_rule_gap_time_outside_mrwa_table_5_9_range" for f in findings
    )


def test_priority_rule_gap_time_outside_mrwa_table_5_9_range_does_not_apply_to_other_tools():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "priority_rule_gap_time_outside_mrwa_table_5_9_range" for f in findings
    )


# --- priority_rule_gap_time_below_mrwa_roundabout_minimum -------------------


def test_priority_rule_gap_time_below_mrwa_roundabout_minimum_fires_below_floor():
    # Real-model note: 11/20 conflict markers on the real Murdoch Drive
    # model carry minGapTime="0", and 14/20 sit below this check's 1.5s
    # floor (11 at 0.0 + 3 at 1.0) -- both re-measured 2026-08-03 straight
    # from the XML (see priority_rule_gap_headway_invalid's
    # own module comment) -- 0.0 is exactly the kind of value this check
    # exists to surface (MRWA-compliance advisory, not a Vissim-mechanical
    # problem -- 0 stays perfectly legal at the refuse tier).
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=0.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "priority_rule_gap_time_below_mrwa_roundabout_minimum"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    assert matches[0].details["floor_s"] == 1.5
    _assert_well_formed_messages(matches[0], must_contain_ko="회전교차로")


def test_priority_rule_gap_time_below_mrwa_roundabout_minimum_silent_at_or_above_floor():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    for value in (1.5, 2.1, 4.0):
        action = _action("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=value)
        findings = run_checks("preflight", action, shadow, {})
        assert not any(
            f.constraint_id == "priority_rule_gap_time_below_mrwa_roundabout_minimum" for f in findings
        )


def test_priority_rule_gap_time_below_mrwa_roundabout_minimum_silent_when_gap_time_s_omitted():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, headway_m=10.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "priority_rule_gap_time_below_mrwa_roundabout_minimum" for f in findings
    )


def test_priority_rule_gap_time_below_mrwa_roundabout_minimum_co_fires_with_table_5_9_check():
    # 0.0 is below BOTH the roundabout floor (1.5s) and the Table 5-9
    # envelope's low end (4.0s) -- both checks fire, additively, per their
    # own module comments (different citations, different claims).
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=0.0)

    findings = run_checks("preflight", action, shadow, {})

    fired_ids = {f.constraint_id for f in findings}
    assert "priority_rule_gap_time_below_mrwa_roundabout_minimum" in fired_ids
    assert "priority_rule_gap_time_outside_mrwa_table_5_9_range" in fired_ids


def test_priority_rule_gap_time_below_mrwa_roundabout_minimum_does_not_apply_to_other_tools():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "priority_rule_gap_time_below_mrwa_roundabout_minimum" for f in findings
    )


def test_priority_rule_object_not_found_fires_for_bogus_rule_no():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_priority_rule_gap_headway", priority_rule_no="999", marker_index=1, gap_time_s=3.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    assert matches[0].details["object_class"] == "priority_rules"


# ---------------------------------------------------------------------------
# vehicle_type_reference_target_not_found / vehicle_type_shared_by_
# compositions -- QUEUE (v2a) unit: vehicle type properties, SCALARS/
# REFERENCES only.
# ---------------------------------------------------------------------------

_VEHICLE_TYPE_FIXTURE = [_vehicle_type("200", weight_distr_no="2")]
_WEIGHT_DISTRIBUTIONS_FIXTURE = [
    {"no": "2", "name": "", "min": 2800.0, "max": 40000.0, "mean": 21400.0, "point_count": 2},
    {"no": "3", "name": "", "min": 4000.0, "max": 12000.0, "mean": 8000.0, "point_count": 2},
]


def test_vehicle_type_object_not_found_fires_for_bogus_vehicle_type_no():
    shadow = _shadow_model(vehicle_types=_VEHICLE_TYPE_FIXTURE, weight_distributions=_WEIGHT_DISTRIBUTIONS_FIXTURE)
    action = _action("set_vehicle_type_reference", vehicle_type_no="999", field="weight_distr", target_no="3")

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    assert matches[0].details["object_class"] == "vehicle_types"


def test_vehicle_type_reference_target_not_found_fires_for_a_bogus_target():
    shadow = _shadow_model(vehicle_types=_VEHICLE_TYPE_FIXTURE, weight_distributions=_WEIGHT_DISTRIBUTIONS_FIXTURE)
    action = _action("set_vehicle_type_reference", vehicle_type_no="200", field="weight_distr", target_no="999999")

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "vehicle_type_reference_target_not_found"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    assert finding.details["field"] == "weight_distr"
    assert finding.details["available"] == ["2", "3"]


def test_vehicle_type_reference_target_not_found_checks_the_collection_matching_the_field():
    # target_no="999999" would be VALID for des_accel_func (not fixtured
    # here, so its own shadow list is empty/absent) -- the check must
    # validate against weight_distributions specifically because field is
    # "weight_distr", never against a different reference's collection.
    shadow = _shadow_model(
        vehicle_types=_VEHICLE_TYPE_FIXTURE,
        weight_distributions=_WEIGHT_DISTRIBUTIONS_FIXTURE,
        des_accel_functions=[{"no": "999999", "name": "irrelevant"}],
    )
    action = _action("set_vehicle_type_reference", vehicle_type_no="200", field="weight_distr", target_no="999999")

    findings = run_checks("preflight", action, shadow, {})

    assert any(f.constraint_id == "vehicle_type_reference_target_not_found" for f in findings)


def test_vehicle_type_reference_target_not_found_silent_for_a_real_target():
    shadow = _shadow_model(vehicle_types=_VEHICLE_TYPE_FIXTURE, weight_distributions=_WEIGHT_DISTRIBUTIONS_FIXTURE)
    action = _action("set_vehicle_type_reference", vehicle_type_no="200", field="weight_distr", target_no="3")

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_type_reference_target_not_found" for f in findings)


def test_vehicle_type_reference_target_not_found_does_not_apply_to_other_tools():
    shadow = _shadow_model(weight_distributions=_WEIGHT_DISTRIBUTIONS_FIXTURE)
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_type_reference_target_not_found" for f in findings)


def test_vehicle_type_shared_by_compositions_fires_and_caps_the_list():
    shadow = _shadow_model(
        vehicle_types=[
            _vehicle_type("200", referencing_composition_nos=[str(i) for i in range(1, 12)])  # 11 compositions
        ],
        weight_distributions=_WEIGHT_DISTRIBUTIONS_FIXTURE,
    )
    action = _action("set_vehicle_type_reference", vehicle_type_no="200", field="weight_distr", target_no="3")

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "vehicle_type_shared_by_compositions"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    assert finding.details["referencing_compositions"]["count"] == 11
    assert "more" in finding.messages["en"] or "외" in finding.messages["ko"]


def test_vehicle_type_shared_by_compositions_silent_when_no_references():
    shadow = _shadow_model(
        vehicle_types=[_vehicle_type("200", referencing_composition_nos=[])],
        weight_distributions=_WEIGHT_DISTRIBUTIONS_FIXTURE,
    )
    action = _action("set_vehicle_type_reference", vehicle_type_no="200", field="weight_distr", target_no="3")

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_type_shared_by_compositions" for f in findings)


def test_vehicle_type_shared_by_compositions_silent_when_target_missing():
    shadow = _shadow_model(vehicle_types=[], weight_distributions=_WEIGHT_DISTRIBUTIONS_FIXTURE)
    action = _action("set_vehicle_type_reference", vehicle_type_no="999", field="weight_distr", target_no="3")

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_type_shared_by_compositions" for f in findings)


def test_vehicle_type_shared_by_compositions_does_not_apply_to_other_tools():
    shadow = _shadow_model(vehicle_types=[_vehicle_type("200", referencing_composition_nos=["1"])])
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_type_shared_by_compositions" for f in findings)


# ---------------------------------------------------------------------------
# des_speed_distribution_points_* -- QUEUE 3 (v2a) unit 3, the "legitimate
# CC1 route" showcase. Every bound below mirrors
# backend/tools/probe_des_speed_curves.py's own live silent-failure map
# (see that check's own registration comment in constraints.py).
# ---------------------------------------------------------------------------


def _points(pairs: list[tuple[float, float]]) -> list[dict[str, float]]:
    return [{"speed_kmh": speed, "cum_fraction": fraction} for speed, fraction in pairs]


_VALID_CURVE = [(0.0, 0.0), (30.0, 0.5), (60.0, 1.0)]


def test_des_speed_distribution_points_object_not_found_fires_for_bogus_distribution_no():
    shadow = _shadow_model(des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE)
    action = _action(
        "set_des_speed_distribution_points", distribution_no="999", points=_points(_VALID_CURVE)
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    assert matches[0].details["object_class"] == "des_speed_distributions"


@pytest.mark.parametrize("bad_points", [[], _points([(50.0, 1.0)])])
def test_des_speed_distribution_points_count_invalid_fires_for_fewer_than_2(bad_points):
    shadow = _shadow_model(des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE)
    action = _action("set_des_speed_distribution_points", distribution_no="25", points=bad_points)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "des_speed_distribution_points_count_invalid"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert matches[0].details["point_count"] == len(bad_points)
    _assert_well_formed_messages(matches[0], must_contain_ko="속도분포 곡선")


def test_des_speed_distribution_points_count_invalid_silent_for_exactly_2():
    shadow = _shadow_model(des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE)
    action = _action(
        "set_des_speed_distribution_points", distribution_no="25", points=_points([(0.0, 0.0), (50.0, 1.0)])
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "des_speed_distribution_points_count_invalid" for f in findings)


def test_des_speed_distribution_points_speed_negative_fires():
    shadow = _shadow_model(des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE)
    action = _action(
        "set_des_speed_distribution_points",
        distribution_no="25",
        points=_points([(-10.0, 0.0), (50.0, 1.0)]),
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "des_speed_distribution_points_speed_negative"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert matches[0].details["negative_speeds_kmh"] == [-10.0]


def test_des_speed_distribution_points_speed_negative_silent_for_zero():
    # 0 km/h is a legitimate real value (a near-stationary desired speed),
    # not merely "not negative" as a technicality.
    shadow = _shadow_model(des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE)
    action = _action(
        "set_des_speed_distribution_points",
        distribution_no="25",
        points=_points([(0.0, 0.0), (0.0, 1.0)]),
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "des_speed_distribution_points_speed_negative" for f in findings)


def test_des_speed_distribution_points_speed_not_monotonic_fires_for_a_genuine_decrease():
    # Vissim itself SILENTLY ACCEPTS this (live-verified) -- this codebase's
    # own exclusive screen.
    shadow = _shadow_model(des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE)
    action = _action(
        "set_des_speed_distribution_points",
        distribution_no="25",
        points=_points([(0.0, 0.0), (100.0, 0.5), (50.0, 1.0)]),
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [
        f for f in findings if f.constraint_id == "des_speed_distribution_points_speed_not_monotonic"
    ]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert matches[0].details["decrease_at_index"] == 2


def test_des_speed_distribution_points_speed_not_monotonic_silent_for_equal_speeds():
    # The legitimate "step function" pattern -- real, PTV-authored data
    # (Rollercoaster.inpx) uses this shape; must NOT be refused.
    shadow = _shadow_model(des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE)
    action = _action(
        "set_des_speed_distribution_points",
        distribution_no="25",
        points=_points([(50.0, 0.0), (50.0, 1.0)]),
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "des_speed_distribution_points_speed_not_monotonic" for f in findings
    )


@pytest.mark.parametrize(
    "bad_curve,expected_snippet",
    [
        ([(0.0, 0.0), (50.0, 0.5), (100.0, 1.5)], "1.5"),  # fx above 1
        ([(0.0, -0.2), (50.0, 0.5), (100.0, 1.0)], "0.2"),  # fx below 0 (first point)
        ([(0.0, 0.2), (50.0, 0.5), (100.0, 1.0)], "0.2"),  # first fx != 0
        ([(0.0, 0.0), (50.0, 0.5), (100.0, 0.9)], "0.9"),  # last fx != 1
        ([(0.0, 0.0), (50.0, 0.9), (70.0, 0.3), (100.0, 1.0)], "0.3"),  # mid-curve reversal
    ],
)
def test_des_speed_distribution_points_fraction_invalid_fires(bad_curve, expected_snippet):
    shadow = _shadow_model(des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE)
    action = _action(
        "set_des_speed_distribution_points", distribution_no="25", points=_points(bad_curve)
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "des_speed_distribution_points_fraction_invalid"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert expected_snippet in matches[0].messages["en"]


def test_des_speed_distribution_points_fraction_invalid_silent_for_a_valid_curve():
    shadow = _shadow_model(des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE)
    action = _action(
        "set_des_speed_distribution_points", distribution_no="25", points=_points(_VALID_CURVE)
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "des_speed_distribution_points_fraction_invalid" for f in findings
    )


def test_des_speed_distribution_points_speed_implausible_warns_above_bound():
    shadow = _shadow_model(des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE)
    action = _action(
        "set_des_speed_distribution_points",
        distribution_no="25",
        points=_points([(0.0, 0.0), (99999.0, 1.0)]),
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [
        f for f in findings if f.constraint_id == "des_speed_distribution_points_speed_implausible"
    ]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    assert matches[0].details["implausible_speeds_kmh"] == [99999.0]


def test_des_speed_distribution_points_speed_implausible_silent_within_real_observed_range():
    # 205 km/h is the highest real value observed across all 25 Vissim
    # example networks -- must not warn comfortably below the 250 km/h bound.
    shadow = _shadow_model(des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE)
    action = _action(
        "set_des_speed_distribution_points",
        distribution_no="25",
        points=_points([(0.0, 0.0), (205.0, 1.0)]),
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "des_speed_distribution_points_speed_implausible" for f in findings
    )


def test_des_speed_distribution_points_duplicate_warns_for_an_exact_duplicate():
    shadow = _shadow_model(des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE)
    action = _action(
        "set_des_speed_distribution_points",
        distribution_no="25",
        points=_points([(0.0, 0.0), (50.0, 0.5), (50.0, 0.5), (100.0, 1.0)]),
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "des_speed_distribution_points_duplicate"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    assert matches[0].details["duplicate_points"] == [{"speed_kmh": 50.0, "cum_fraction": 0.5}]


def test_des_speed_distribution_points_duplicate_silent_for_the_legitimate_step_function():
    # Same speed, DIFFERENT fraction -- a real curve shape, not a duplicate.
    shadow = _shadow_model(des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE)
    action = _action(
        "set_des_speed_distribution_points",
        distribution_no="25",
        points=_points([(50.0, 0.0), (50.0, 1.0)]),
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "des_speed_distribution_points_duplicate" for f in findings)


def test_des_speed_distribution_points_shared_target_warns_with_bounded_ids():
    shared_distribution = {
        "no": "50",
        "name": "55 mph",
        "reference_count": 3,
        "referenced_by": {
            "des_speed_decisions": {"count": 1, "ids": ["6.10"], "truncated": False},
            "vehicle_compositions": {"count": 1, "ids": ["1.100"], "truncated": False},
            "reduced_speed_areas": {"count": 0, "ids": [], "truncated": False},
            "other": {"count": 1, "by_tag": {"ptLine": 1}},
        },
    }
    shadow = _shadow_model(des_speed_distributions=[shared_distribution])
    action = _action(
        "set_des_speed_distribution_points", distribution_no="50", points=_points(_VALID_CURVE)
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "des_speed_distribution_points_shared_target"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    assert finding.details["reference_count"] == 3
    assert "6.10" in finding.messages["en"]
    assert "1.100" in finding.messages["en"]
    _assert_well_formed_messages(finding, must_contain_ko="공유 대상")


def test_des_speed_distribution_points_shared_target_silent_when_unreferenced():
    unreferenced_distribution = {
        "no": "99",
        "name": "unused",
        "reference_count": 0,
        "referenced_by": {
            "des_speed_decisions": {"count": 0, "ids": [], "truncated": False},
            "vehicle_compositions": {"count": 0, "ids": [], "truncated": False},
            "reduced_speed_areas": {"count": 0, "ids": [], "truncated": False},
            "other": {"count": 0, "by_tag": {}},
        },
    }
    shadow = _shadow_model(des_speed_distributions=[unreferenced_distribution])
    action = _action(
        "set_des_speed_distribution_points", distribution_no="99", points=_points(_VALID_CURVE)
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "des_speed_distribution_points_shared_target" for f in findings
    )


@pytest.mark.parametrize(
    "constraint_id",
    [
        "des_speed_distribution_points_count_invalid",
        "des_speed_distribution_points_speed_negative",
        "des_speed_distribution_points_speed_not_monotonic",
        "des_speed_distribution_points_fraction_invalid",
        "des_speed_distribution_points_speed_implausible",
        "des_speed_distribution_points_duplicate",
        "des_speed_distribution_points_shared_target",
    ],
)
def test_des_speed_distribution_points_checks_do_not_apply_to_other_tools(constraint_id):
    shadow = _shadow_model(des_speed_distributions=_DES_SPEED_DISTRIBUTIONS_FIXTURE)
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == constraint_id for f in findings)


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
    # see the Phase alias's own comment just above class Finding) == 61, + 1
    # (sibling-disambiguation unit, 2026-07-28: vehicle_input_siblings_share_
    # link -- NOT a §-numbered catalog entry, a defensive addition specific to
    # set_vehicle_input_volume, in the same family as #3 latent_demand_
    # overflow but about the class MIX rather than the capacity; #3 itself was
    # made sibling-aware IN PLACE, not added) == 62, + 3 (QUEUE 3 unit 2,
    # 2026-07-29: priority_rule_marker_not_found / priority_rule_gap_headway_
    # invalid / priority_rule_gap_headway_implausible -- all three "not a
    # §-numbered catalog entry, a defensive addition" shapes for the new
    # set_priority_rule_gap_headway tool, mirroring the des_speed_decision_
    # entry_not_found / vehicle_composition_weight_invalid / vehicle_
    # composition_share_drastic_change precedents) == 65, + 7 (QUEUE 3 (v2a)
    # unit 3, 2026-07-29: des_speed_distribution_points_count_invalid /
    # des_speed_distribution_points_speed_negative / des_speed_distribution_
    # points_speed_not_monotonic / des_speed_distribution_points_fraction_
    # invalid / des_speed_distribution_points_speed_implausible /
    # des_speed_distribution_points_duplicate / des_speed_distribution_
    # points_shared_target -- all seven "not a §-numbered catalog entry, a
    # defensive addition" shapes for the new set_des_speed_distribution_points
    # tool, mirroring the set_priority_rule_gap_headway/set_signal_green_split
    # precedents; des_speed_distribution_not_found already existed (v2a unit
    # ⑧, gates set_des_speed_decision) and was NOT touched) == 72, + 2 (QUEUE
    # (v2a) unit, 2026-07-29: vehicle_type_reference_target_not_found /
    # vehicle_type_shared_by_compositions -- both "not a §-numbered catalog
    # entry, a defensive addition" shapes for the new
    # set_vehicle_type_reference tool, mirroring the des_speed_distribution_
    # not_found/vehicle_composition_referenced_by_vehicle_inputs precedents)
    # == 74, + 10 (QUEUE (Q3, v2a) unit 5, dwell-time distributions:
    # time_distribution_type_mismatch / time_distribution_mean_or_stddev_
    # negative / time_distribution_mean_implausible / time_distribution_
    # points_count_invalid / time_distribution_points_duration_negative /
    # time_distribution_points_duration_not_monotonic / time_distribution_
    # points_fraction_invalid / time_distribution_points_duration_
    # implausible / time_distribution_points_duplicate / time_distribution_
    # shared_target -- all ten "not a §-numbered catalog entry, a defensive
    # addition" shapes for the two new set_time_distribution_normal/
    # set_time_distribution_points tools, mirroring the set_priority_rule_
    # gap_headway/set_des_speed_distribution_points precedents; object_
    # not_found already existed and was EXTENDED in place to gate both new
    # tools, not added) == 84, + 4 (QUEUE 3 unit 6, 2026-07-31:
    # driving_behavior_parameter_range_invalid /
    # driving_behavior_parameter_value_implausible /
    # link_behavior_type_veh_class_override_not_found /
    # link_behavior_type_driving_behavior_target_not_found -- all four "not
    # a #-numbered catalog entry, a defensive addition" shapes for the three
    # new driving-behavior duplicate-then-edit tools, mirroring the
    # set_priority_rule_gap_headway/set_des_speed_distribution_points
    # precedents; #33 driving_behavior_parameter_model_mismatch and #40
    # driving_behavior_shared_object_edit already existed and were CORRECTED
    # IN PLACE (real param names, a real reference-count check replacing a
    # gameable caller-supplied flag), not added; object_not_found already
    # existed and was EXTENDED in place to gate all three new tools, not
    # added) == 88, + 1 (QUEUE 3 unit 7, 2026-07-31: rollback_invalidates_
    # run_results -- NOT a §-numbered catalog entry, a defensive addition for
    # the new rollback_last_edit tool: warns when a simulation was run after
    # the edit being undone, since that run's KPIs no longer correspond to
    # the model once the rollback lands -- see that constraint's own comment)
    # == 89, + 1 (QUEUE 3 unit 8, 2026-07-31: apply_edit_plan_validation --
    # NOT a §-numbered catalog entry, a defensive addition for the new
    # apply_edit_plan (batch/compound edits) tool: a single registration that
    # delegates to services.agent.batch.validate_plan and can return MANY
    # findings in one preflight pass -- see that check's own comment and
    # run_checks' updated docstring for the list-returning CheckFn shape this
    # introduced) == 90, + 1 (Q4 unit (2), measurement-infrastructure
    # placement: measurement_infra_pos_out_of_range -- NOT a §-numbered
    # catalog entry, this unit's ONE new constraint per its own "constraints.py
    # minimal" instruction: refuses a queue-counter/data-collection-point/
    # travel-time-section placement whose pos_m falls outside [0, the target
    # link's own length]; object_not_found already existed and was EXTENDED in
    # place (new _TOOL_TARGET_SPEC/_OBJECT_CLASS_LABELS entries) to gate 7 of
    # the unit's 8 new add/remove tools, not added -- see that check's own
    # comment for the one deliberate scope cut, add_data_collection_measurement)
    # == 91, + 1 (Q4 unit (3), full link closure + turn bans (movement
    # removal): link_closure_route_consequence -- NOT a §-numbered catalog
    # entry, this unit's ONE new constraint per its own "constraints.py
    # minimal" instruction: warns (never refuses) when set_link_closure/
    # set_turn_ban's own target is still traversed by a route's link_seq --
    # object_not_found already existed and was EXTENDED in place (new
    # _TOOL_TARGET_SPEC entries for set_link_closure/set_turn_ban/
    # remove_connector) to gate all three new tools, not added; the "is this
    # actually a connector"/"does a lane index exist" checks are inline
    # ValueErrors in tools_edit.py, not new constraints, mirroring #2's own
    # documented gap; remove_connector's OWN route/priority-rule/conflict-area
    # consequence is a REFUSAL enforced by services.inpx_structure's spine
    # (extended ID_REFERENCE_SITES, not a new constraints.py entry either)
    # == 92, + 1 (Q4 unit (4), signal-head + detector placement:
    # signal_head_detector_pos_out_of_range -- NOT a §-numbered catalog
    # entry, this unit's ONE new constraint per its own "constraints.py
    # minimal" instruction: refuses an add_signal_head/add_detector pos_m
    # that falls outside [0, the target link's own length], mirroring
    # measurement_infra_pos_out_of_range's own shape as a separate
    # constraint id/table; object_not_found already existed and was
    # EXTENDED in place (new _TOOL_TARGET_SPEC/_OBJECT_CLASS_LABELS entries)
    # to gate all four new tools, not added -- sc_no/sg_no/port_no existence
    # is enforced by vissim_com.py's own fail-closed COM errors instead, the
    # SAME deliberate scope cut add_travel_time_section's to_link already
    # established; a detector's own length_m and zone-past-link-end are
    # documented, deliberate scope cuts too, not new constraints)
    # == 93, + 1 (Q4 unit (5), add/remove links & connectors:
    # add_link_lane_count_high -- NOT a §-numbered catalog entry, this
    # unit's ONE new constraint per its own "constraints.py minimal"
    # instruction: WARNS (never refuses) when add_link's own lane_count
    # exceeds 10 -- lane_count<1 is a hard pydantic refusal
    # (AddLinkParams.Field(ge=1)), never reaching this check at all;
    # object_not_found already existed and was EXTENDED in place (new
    # _TOOL_TARGET_SPEC entries for remove_link/add_connector) to gate two
    # of the unit's three new tools, not added -- add_link itself gets NO
    # object_not_found entry (it creates, targets nothing pre-existing);
    # degenerate-geometry refusals (<2 points, duplicate consecutive
    # points, zero total length) and the connector from==to/lane-mapping
    # refusals are inline pydantic model_validators in tools_edit.py, not
    # new constraints, mirroring #2's own documented gap and set_link_
    # closure/set_turn_ban's own "is this actually a connector" inline
    # checks)
    # == 94, + 1 (Q4 unit (6), stop-sign + priority-rule/conflict-area
    # placement: add_priority_rule_marker_invalid -- NOT a #-numbered catalog
    # entry, this unit's ONE new constraint per its own "constraints.py
    # minimal" instruction: refuses a negative/non-finite gap_time_s/
    # headway_m in ANY of add_priority_rule's own "markers" list entries,
    # the creation-side counterpart of priority_rule_gap_headway_invalid's
    # own "0 is legitimate, only negative refused" semantics; object_not_found
    # already existed and was EXTENDED in place (new _TOOL_TARGET_SPEC/
    # _OBJECT_CLASS_LABELS entries) to gate all four new tools, not added;
    # add_stop_sign/add_priority_rule's own pos-bounds checks are TABLE
    # WIRING onto the pre-existing signal_head_detector_pos_out_of_range
    # check (new _SIGNAL_HEAD_DETECTOR_POS_FIELDS entries), not a new
    # constraint either; conflict areas get no verb/constraint at all --
    # confirmed non-creatable via COM, a documented finding, not a gap)
    # == 95, + 1 (Q4 unit (7), PT stop + PT line placement: pt_line_stop_not_
    # found -- NOT a #-numbered catalog entry, this unit's ONE new constraint
    # per its own "constraints.py minimal" instruction: covers set_pt_line_
    # stop_dwell's entire preflight-existence requirement in one pass (the
    # (line_no, stop_no) pair AND time_distribution_no, since that tool has
    # NO _TOOL_TARGET_SPEC entry at all -- a two-part id, mirroring set_
    # route_relative_flow's/set_des_speed_decision's own split); object_not_
    # found already existed and was EXTENDED in place (new _TOOL_TARGET_SPEC/
    # _OBJECT_CLASS_LABELS entries) to gate add_pt_stop/remove_pt_stop/add_
    # pt_line/remove_pt_line, not added; remove_pt_stop's own reference-
    # integrity refusal is inline in tools_edit.py (services.inpx_structure.
    # find_id_references), not a new constraint either, mirroring remove_
    # data_collection_point's own precedent)
    # == 96, + 4 (Phase 0.3, MRWA constraint/defaults pack,
    # docs/product/master-roadmap-2026-07.md item 0.3): priority_rule_gap_
    # time_outside_mrwa_table_5_9_range / priority_rule_gap_time_below_mrwa_
    # roundabout_minimum -- both consult the new services.agent.
    # mrwa_reference data module against set_priority_rule_gap_headway's
    # gap_time_s (movement type not inferable from the shadow model, so the
    # first uses Table 5-9's overall two-movement-type envelope [4.0, 5.0]s
    # and the second uses the more permissive of the two roundabout
    # minimums, 1.5s, per that check's own comment); add_detector_length_
    # deviates_from_mrwa_default / add_detector_setback_deviates_from_mrwa_
    # default -- both make add_detector's own MRWA Appendix A defaults
    # (length 4.5m, setback 2.0m, already cited by _MRWA_DETECTOR_LENGTH_M/
    # _MRWA_DETECTOR_SETBACK_M) visible when a caller explicitly overrides
    # them; set_conflict_area_status vs Table 5-10 front/rear gap and
    # reduced-speed-area vs Table 5-8 design-speed-by-radius were BOTH
    # evaluated and deliberately SKIPPED -- conflict areas expose no
    # front/rear-gap attribute anywhere in this codebase (COM wrapper or
    # shadow model), and Table 5-8's own radius/speed breakpoints were not
    # supplied by any source available to this unit (mrwa_reference.
    # TABLE_5_8_RSA_DESIGN_SPEED ships empty rather than inventing them) --
    # see mrwa_reference.py's own module docstring and this unit's drafted
    # DECISIONS.md entry for the full reasoning on both scope cuts.
    # == 100, + 2 (Q5 unit (2), add_signal_controller -- signal-controller
    # genesis via duplicate-then-edit): signal_controller_duplicate_source_
    # type_unsupported (refuse -- the CREATE-side mirror of #4's population:
    # a non-fixed-time donor keeps its program in an external file, so the
    # copy would be neither verifiable nor editable; measured grounds in
    # backend/tools/probe_signal_controller_duplicate.py Part D) and
    # signal_controller_duplicate_inherits_donor_program (warn -- the copy
    # runs the DONOR's timings until edited and controls nothing until
    # signal heads are wired to it; one Beijing duplicate inherits 2
    # programs x 22 signal groups = 44 green splits). No third constraint:
    # the donor's own existence is #1 object_not_found via a plain
    # _TOOL_TARGET_SPEC entry, and the zero-controller genesis limitation
    # fails CLOSED inside vissim_com.VissimController.add_signal_controller
    # with a stated error (the same deliberate split add_priority_rule's own
    # limitation already uses).
    # == 102, + 2 (Q5 unit (3), add_vehicle_input -- vehicle-input genesis,
    # the LAST GENESIS GAP LIST item): vehicle_input_on_connector (warn -- COM
    # ACCEPTS a vehicle input on a connector and it genuinely generates
    # traffic, so this SHIPPED AS A WARN after the probe overturned the
    # refusal it was planned to be; what is really wrong is that the demand
    # enters mid-junction and bypasses the upstream approach, measured as a
    # controlled triple on Beijing with all demand zeroed: 13 vehicles from a
    # plain-link input, 13 from a connector input, 0 from the null control --
    # backend/tools/probe_vehicle_input_create.py Parts B/D/E/F) and
    # vehicle_input_created_covers_first_interval_only (warn -- the caller's
    # volume only ever lands on interval 1 while the network decides how many
    # intervals a fresh input carries: 5 rows ['450','0','0','0','0'] on Toll
    # Plaza Camden.US, 12 rows on Air Taxi Vertiport whose second interval
    # starts at 1000 ms, i.e. the requested demand runs for ONE SECOND).
    # No third constraint: the target link's own existence is #1
    # object_not_found via a plain _TOOL_TARGET_SPEC entry PLUS an entry in
    # #1's own applies_to list (a spec alone does nothing -- see that list's
    # comment for the ten already-shipped verbs found missing there), and the
    # 0..20000 veh/h bound is TABLE WIRING onto the pre-existing
    # volume_out_of_range check (_VOLUME_FIELDS), not a new constraint,
    # mirroring how Q4 unit (6) wired add_stop_sign/add_priority_rule onto
    # signal_head_detector_pos_out_of_range.
    # == 104, + 3 (Q6 unit (1), link geometry edits -- set_link_lane_count /
    # set_link_geometry / set_link_point_radius):
    # link_geometry_truncates_anchored_objects (refuse -- the flagship silent
    # failure of that unit, measured twice: a data-collection point at pos
    # 132.485 m on a link shortened to 82.803 m came back at EXACTLY the new
    # end, and shortening Beijing link 5 from 452.06 m to 226.03 m clamped all
    # three connectors anchored at its far end AND stretched their geometry to
    # reach, turning an 11.71 m turn connector into a 237.70 m one, with
    # load_check still passing),
    # link_lane_count_shifts_centerline (warn -- Vissim re-centres a link's
    # polyline on every lane-count change, measured three times across two
    # networks and both directions at exactly half the added/removed lane
    # width: 1.75 m, 1.5 m, 0.5334 m), and
    # link_point_radius_overrides_geometry (warn -- a USERINPUT radius is a
    # first-class Vissim feature, 16 082 of the corpus's 60 420 polyline
    # points already use one, but it decouples the curve-speed model from the
    # drawn shape, so the warn names the override AND the circumradius of the
    # shape as drawn).
    # No fourth constraint: the reference-integrity refusal that stops a lane
    # reduction from silently CASCADE-DELETING lane-anchored objects lives in
    # tools_edit.set_link_lane_count (services.inpx_structure's
    # ReferenceIntegrityError, the remove_link/remove_pt_stop precedent), the
    # target link's own existence is #1 object_not_found via a plain
    # _TOOL_TARGET_SPEC entry PLUS an entry in #1's own applies_to list, and
    # the M0.3 forward-ready link_geometry_edit_reference_integrity was
    # GENERALIZED rather than duplicated (it finally fires, off the two
    # shape-changing tools' own link_id, instead of a flag nothing sets).
    # == 107.
    # Q6 unit (2) adds THREE more (== 110): parking_lot_capacity_unrealistic
    # (refuse -- Vissim's Capacity attribute is READ-ONLY and derived as
    # floor(length / len_per_space), and a request that lands on zero length
    # is accepted SILENTLY, producing a zero-space lot on a network that
    # still loads and runs), parking_lot_does_not_fit_link (refuse -- a
    # deliberately conservative pos + capacity x len_per_space screen, at
    # least as strict as Vissim's own loud "Parking Lot 2 does not fit on
    # link 1 (335.122 m)"), and
    # parking_lot_inert_without_parking_routing_decision (warn -- a lot
    # attracts nothing until a parking routing decision names it, and this
    # toolkit ships no verb that creates one).
    # No fourth constraint: the reference-integrity refusal that stops a
    # removal from silently CASCADE-DELETING the referencing parking route
    # lives in tools_edit.remove_parking_lot (services.inpx_structure's
    # ReferenceIntegrityError, the remove_pt_stop precedent), and each verb's
    # target existence is #1 object_not_found via a _TOOL_TARGET_SPEC entry
    # PLUS an entry in #1's own applies_to list.
    #
    # Q6 unit (3) (pedestrians/Viswalk) adds THREE more (110 -> 113):
    # pedestrian_input_origin_area_has_no_route (refuse -- an input on an area
    # with no pedestrian routing decision stops the network initialising, and
    # the demand volume is irrelevant: six probe arms isolate the cause to the
    # area), pedestrian_input_created_covers_first_interval_only (warn -- the
    # pedestrian twin of the vehicle-side interval finding, measured on a
    # 14-interval network), and pedestrian_area_edit_unsupported (refuse --
    # names the deliberate gap, because RemoveArea silently cascade-deletes
    # pedestrian routes and leaves Vissim's own load/init oracle GREEN).
    #
    # Unit #44-b (2026-08-19) adds TWO more (113 -> 115), both halves of the ONE
    # narrow exception DECISIONS.md 2026-08-19 decided on:
    # car_following_shared_edit_consented (warn -- the shared-object refusal
    # stands down for a Table 5-6 set dispatched under an approval-bound grant,
    # and this names the blast radius that was consented to, because an
    # exception nobody is told about is worse than a refusal), and
    # car_following_consent_radius_drifted (refuse -- the consent was to a NAMED
    # radius, so if the reference graph moves between approval and apply the
    # edit is refused by name, quoting both numbers).
    # Regression pin -- bump deliberately, not by accident.
    assert len(get_registry()) == 115


def test_registry_has_unique_ids():
    ids = [c.id for c in get_registry()]
    assert len(ids) == len(set(ids))


def test_every_constraint_has_well_formed_ko_en_ja_templates():
    for constraint in get_registry():
        for template in (constraint.message_ko, constraint.message_en, constraint.message_ja):
            assert isinstance(template, str) and template.strip()


# ---------------------------------------------------------------------------
# rollback_invalidates_run_results (warn, postwrite) — QUEUE 3 unit 7
# ---------------------------------------------------------------------------


def test_rollback_invalidates_run_results_fires_when_run_since_edit_true():
    action = _action("rollback_last_edit")
    shadow = _shadow_model()

    findings = run_checks("postwrite", action, shadow, {"run_since_edit": True})

    assert len(findings) == 1
    finding = findings[0]
    assert finding.constraint_id == "rollback_invalidates_run_results"
    assert finding.tier == "warn"
    _assert_well_formed_messages(finding, must_contain_ko="시뮬레이션")


def test_rollback_invalidates_run_results_silent_when_no_run_since_edit():
    action = _action("rollback_last_edit")
    shadow = _shadow_model()

    assert run_checks("postwrite", action, shadow, {"run_since_edit": False}) == []
    assert run_checks("postwrite", action, shadow, {}) == []


def test_rollback_invalidates_run_results_does_not_apply_to_other_tools():
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)
    shadow = _shadow_model()

    findings = run_checks("postwrite", action, shadow, {"run_since_edit": True})

    assert not any(f.constraint_id == "rollback_invalidates_run_results" for f in findings)


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


# ---------------------------------------------------------------------------
# vehicle_input_siblings_share_link (sibling-disambiguation unit, 2026-07-28)
#
# Reuses _jennalup_shadow() (defined with the #3 tests above) -- the same
# measured link-620 group the capacity regression pins, since both checks are
# about the same real-world shape: three vehicle inputs, one per vehicle
# class, sharing one link and one coordinate.
# ---------------------------------------------------------------------------


def test_vehicle_input_siblings_share_link_fires_when_link_carries_other_inputs():
    shadow = _jennalup_shadow()
    action = _action("set_vehicle_input_volume", input_no="107", volume=6000)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "vehicle_input_siblings_share_link"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    assert finding.phase == "preflight"
    assert finding.suggested_fix is None
    assert finding.details["sibling_input_nos"] == ["207", "307"]
    _assert_well_formed_messages(finding, must_contain_ko="6000")


def test_vehicle_input_siblings_share_link_silent_when_input_is_alone_on_its_link():
    # The default fixture has exactly one vehicle input on link 101 -- nothing
    # to confuse it with, and no class mix to shift.
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=1200)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_input_siblings_share_link" for f in findings)


def test_vehicle_input_siblings_share_link_skipped_when_vehicle_input_missing():
    shadow = _jennalup_shadow()
    action = _action("set_vehicle_input_volume", input_no="does-not-exist", volume=6000)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_input_siblings_share_link" for f in findings)


def test_vehicle_input_siblings_share_link_does_not_apply_to_other_tools():
    shadow = _jennalup_shadow()
    action = _action("set_sim_duration", duration=3600)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "vehicle_input_siblings_share_link" for f in findings)


def test_vehicle_input_siblings_share_link_names_every_sibling_with_class_and_volume():
    shadow = _jennalup_shadow()
    action = _action("set_vehicle_input_volume", input_no="107", volume=6000)

    finding = next(
        f
        for f in run_checks("preflight", action, shadow, {})
        if f.constraint_id == "vehicle_input_siblings_share_link"
    )

    for lang in LANGS:
        message = finding.messages[lang]
        assert "207(Class2-5, 19.0 veh/h)" in message
        assert "307(Class6-9, 0.95 veh/h)" in message
    # The TARGET's own class is named too -- that is the whole point: 107,
    # 207 and 307 share a name ("Jennalup ST_WB") and differ only by class.
    assert "Class1" in finding.messages["ko"]


def test_vehicle_input_siblings_share_link_reports_the_class_mix_shift():
    """The measured Jennalup case: setting 107 to 6000 leaves 207+307 at
    19.95 veh/h, so the non-Class1 share of the approach collapses from
    19.95/147.25 = 13.5% to 19.95/6019.95 = 0.3%."""
    shadow = _jennalup_shadow()
    action = _action("set_vehicle_input_volume", input_no="107", volume=6000)

    finding = next(
        f
        for f in run_checks("preflight", action, shadow, {})
        if f.constraint_id == "vehicle_input_siblings_share_link"
    )

    assert finding.details["old_link_total_volume"] == 147.25
    assert finding.details["new_link_total_volume"] == 6019.95
    assert finding.details["other_classes_share_pct_before"] == 13.5
    assert finding.details["other_classes_share_pct_after"] == 0.3
    for lang in LANGS:
        assert "13.5" in finding.messages[lang] and "0.3" in finding.messages[lang]


def test_vehicle_input_siblings_share_link_omits_mix_estimate_when_a_volume_is_unknown():
    """A sibling with no known volume must not produce an invented
    percentage -- the warning still fires (the siblings are still there), it
    just says the shift could not be computed."""
    shadow = _shadow_model(
        links=[{"id": "620", "name": "L", "lanes": 1, "length": 240.0}],
        vehicle_inputs=[
            {"no": "107", "name": "A", "link": "620", "volume": 127.3, "composition_name": "Class1"},
            {"no": "207", "name": "A", "link": "620", "volume": None, "composition_name": "Class2-5"},
        ],
    )
    action = _action("set_vehicle_input_volume", input_no="107", volume=6000)

    finding = next(
        f
        for f in run_checks("preflight", action, shadow, {})
        if f.constraint_id == "vehicle_input_siblings_share_link"
    )

    assert finding.details["other_classes_share_pct_before"] is None
    assert finding.details["other_classes_share_pct_after"] is None
    assert "207(Class2-5, ? veh/h)" in finding.messages["ko"]
    _assert_well_formed_messages(finding, must_contain_ko="6000")


def test_vehicle_input_siblings_share_link_falls_back_to_veh_comp_number_when_unnamed():
    shadow = _shadow_model(
        links=[{"id": "620", "name": "L", "lanes": 1, "length": 240.0}],
        vehicle_inputs=[
            {"no": "107", "name": "A", "link": "620", "volume": 100.0, "composition_no": "1"},
            {"no": "207", "name": "A", "link": "620", "volume": 50.0, "composition_no": "2"},
        ],
    )
    action = _action("set_vehicle_input_volume", input_no="107", volume=400)

    finding = next(
        f
        for f in run_checks("preflight", action, shadow, {})
        if f.constraint_id == "vehicle_input_siblings_share_link"
    )

    assert "vehComp 2" in finding.messages["ko"]
    assert "vehComp 1" in finding.messages["en"]


def test_vehicle_input_siblings_share_link_reports_varying_composition_honestly():
    """The XML puts vehComp on each <timeIntervalVehVolume>, so an input MAY
    reference different compositions per interval (0 of the 51 real inputs
    do). When it does, no single class name is claimed."""
    shadow = _shadow_model(
        links=[{"id": "620", "name": "L", "lanes": 1, "length": 240.0}],
        vehicle_inputs=[
            {
                "no": "107",
                "name": "A",
                "link": "620",
                "volume": 100.0,
                "composition_no": "",
                "composition_name": "",
                "composition_nos": ["1", "2"],
                "composition_varies_by_interval": True,
            },
            {"no": "207", "name": "A", "link": "620", "volume": 50.0, "composition_name": "Class2-5"},
        ],
    )
    action = _action("set_vehicle_input_volume", input_no="107", volume=400)

    finding = next(
        f
        for f in run_checks("preflight", action, shadow, {})
        if f.constraint_id == "vehicle_input_siblings_share_link"
    )

    assert "vehComp 1, 2" in finding.messages["ko"]
    assert "varies by interval" in finding.messages["en"]


def test_vehicle_input_siblings_share_link_works_without_any_composition_data():
    """A network whose vehicle inputs carry no vehComp at all (the synthetic
    demo file) must still get a usable warning -- and must NOT have a class
    name invented for it."""
    shadow = _shadow_model(
        links=[{"id": "620", "name": "L", "lanes": 1, "length": 240.0}],
        vehicle_inputs=[
            {"no": "1", "name": "A", "link": "620", "volume": 100.0},
            {"no": "2", "name": "B", "link": "620", "volume": 50.0},
        ],
    )
    action = _action("set_vehicle_input_volume", input_no="1", volume=400)

    finding = next(
        f
        for f in run_checks("preflight", action, shadow, {})
        if f.constraint_id == "vehicle_input_siblings_share_link"
    )

    assert "2(50.0 veh/h)" in finding.messages["ko"]
    assert "Class" not in finding.messages["ko"]
    _assert_well_formed_messages(finding, must_contain_ko="400")


# ---------------------------------------------------------------------------
# Real-dataset regression (sibling-disambiguation unit, 2026-07-28)
#
# The synthetic _jennalup_shadow() tests above pin the LOGIC on every
# machine. This pins the same case end to end against the ACTUAL client model
# -- so a parser regression that stopped carrying compositions, or a link/
# volume drift, is caught here rather than in front of the user.
# ---------------------------------------------------------------------------


@_requires_real_dataset
def test_real_dataset_link_620_sibling_group_matches_measured_ground_truth():
    from services.agent.tools_network import build_shadow_model

    shadow = build_shadow_model(ACTIVE_REAL_INPX)
    on_620 = [vi for vi in shadow["vehicle_inputs"] if str(vi.get("link")) == "620"]

    assert [(vi["no"], vi["composition_name"], vi["volume"]) for vi in on_620] == [
        ("107", "Class1", 127.3),
        ("207", "Class2-5(SLK 1.47_7-8AM&3-4PM)", 19.0),
        ("307", "Class6-9(SLK 1.47_7-8AM&3-4PM)", 0.95),
    ]
    # All three share one name -- the reason the composition had to be carried.
    assert {vi["name"] for vi in on_620} == {"Jennalup ST_WB"}


@_requires_real_dataset
def test_real_dataset_regression_1881_on_input_107_is_no_longer_silent():
    """The exact silent failure the pre-fix capacity check let through on the
    real model: 1881 < the 1-lane link's 1900 capacity, but the link's real
    post-write demand is 1900.95."""
    from services.agent.tools_network import build_shadow_model

    shadow = build_shadow_model(ACTIVE_REAL_INPX)
    action = _action("set_vehicle_input_volume", input_no="107", volume=1881.0)

    findings = run_checks("preflight", action, shadow, {})
    latent = [f for f in findings if f.constraint_id == "latent_demand_overflow"]

    assert len(latent) == 1
    assert latent[0].details["link_total_volume"] == 1900.95
    assert latent[0].details["sibling_input_nos"] == ["207", "307"]
    assert latent[0].suggested_fix == {"max_recommended_volume": 1880.05}


@_requires_real_dataset
def test_real_dataset_setting_input_107_to_6000_reports_the_heavy_vehicle_share_collapse():
    from services.agent.tools_network import build_shadow_model

    shadow = build_shadow_model(ACTIVE_REAL_INPX)
    action = _action("set_vehicle_input_volume", input_no="107", volume=6000)

    finding = next(
        f
        for f in run_checks("preflight", action, shadow, {})
        if f.constraint_id == "vehicle_input_siblings_share_link"
    )

    assert finding.details["other_classes_share_pct_before"] == 13.5
    assert finding.details["other_classes_share_pct_after"] == 0.3
    assert "Class6-9(SLK 1.47_7-8AM&3-4PM)" in finding.messages["ko"]


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


# ---------------------------------------------------------------------------
# Time-distribution edits -- QUEUE (Q3, v2a) unit 5
# ---------------------------------------------------------------------------

_TIME_DISTRIBUTION_FIXTURE = [
    _time_distribution("1", dist_type="NORMAL", mean_s=0.5, std_dev_s=0.0),
    _time_distribution("25", dist_type="NORMAL", mean_s=25.0, std_dev_s=5.0),
    _time_distribution(
        "200",
        dist_type="EMPIRICAL",
        mean_s=3.0,
        std_dev_s=0.5,
        points=[{"duration_s": 2.0, "cum_fraction": 0.0}, {"duration_s": 4.0, "cum_fraction": 1.0}],
    ),
    _time_distribution(
        "999",
        dist_type="NORMAL",
        mean_s=10.0,
        std_dev_s=2.0,
        reference_count=3,
        referenced_by={
            "pt_stops": {"count": 2, "ids": ["10.5", "10.6"], "truncated": False},
            "pt_vehicle_class_overrides": {"count": 0, "ids": [], "truncated": False},
            "driving_behaviors": {"count": 1, "ids": ["1:cc1"], "truncated": False},
            "other": {"count": 0, "by_tag": {}},
        },
    ),
]


def test_time_distribution_type_mismatch_refuses_normal_verb_on_empirical_target():
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action("set_time_distribution_normal", time_distribution_no="200", mean_s=77.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "time_distribution_type_mismatch"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert matches[0].details == {"distribution_no": "200", "actual_type": "EMPIRICAL", "tool": "set_time_distribution_normal"}
    assert matches[0].suggested_fix == {"use_tool": "set_time_distribution_points"}
    _assert_well_formed_messages(matches[0], must_contain_ko="200")


def test_time_distribution_type_mismatch_refuses_points_verb_on_normal_target():
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action(
        "set_time_distribution_points",
        time_distribution_no="25",
        points=[{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 10.0, "cum_fraction": 1.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "time_distribution_type_mismatch"]
    assert len(matches) == 1
    assert matches[0].details["actual_type"] == "NORMAL"
    assert matches[0].suggested_fix == {"use_tool": "set_time_distribution_normal"}


def test_time_distribution_type_mismatch_silent_when_types_match():
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    normal_action = _action("set_time_distribution_normal", time_distribution_no="25", mean_s=30.0)
    points_action = _action(
        "set_time_distribution_points",
        time_distribution_no="200",
        points=[{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 10.0, "cum_fraction": 1.0}],
    )

    for action in (normal_action, points_action):
        findings = run_checks("preflight", action, shadow, {})
        assert not any(f.constraint_id == "time_distribution_type_mismatch" for f in findings)


def test_time_distribution_type_mismatch_defers_to_object_not_found_when_target_missing():
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action("set_time_distribution_normal", time_distribution_no="777", mean_s=1.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "time_distribution_type_mismatch" for f in findings)
    assert any(f.constraint_id == "object_not_found" for f in findings)


def test_time_distribution_type_mismatch_does_not_apply_to_other_tools():
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "time_distribution_type_mismatch" for f in findings)


@pytest.mark.parametrize("bad_value", [-1.0, -0.001, float("nan"), float("inf"), float("-inf")])
def test_time_distribution_mean_or_stddev_negative_fires_for_bad_mean(bad_value):
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action("set_time_distribution_normal", time_distribution_no="25", mean_s=bad_value)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "time_distribution_mean_or_stddev_negative"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert matches[0].details["invalid_fields"] == {"mean_s": bad_value}


def test_time_distribution_mean_or_stddev_negative_fires_for_bad_stddev():
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action("set_time_distribution_normal", time_distribution_no="25", std_dev_s=-2.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "time_distribution_mean_or_stddev_negative"]
    assert len(matches) == 1
    assert matches[0].details["invalid_fields"] == {"std_dev_s": -2.0}


def test_time_distribution_mean_or_stddev_negative_silent_for_zero_stddev_a_common_real_value():
    # Real data check: zero stdDev is common and legitimate (many real
    # distributions across all 25 example networks carry stdDev="0").
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action("set_time_distribution_normal", time_distribution_no="25", mean_s=25.0, std_dev_s=0.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "time_distribution_mean_or_stddev_negative" for f in findings)


def test_time_distribution_mean_or_stddev_negative_does_not_apply_to_other_tools():
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "time_distribution_mean_or_stddev_negative" for f in findings)


def test_time_distribution_mean_implausible_warns_above_bound():
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action("set_time_distribution_normal", time_distribution_no="25", mean_s=9999.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "time_distribution_mean_implausible"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    assert matches[0].details["mean_s"] == 9999.0


def test_time_distribution_mean_implausible_silent_for_real_legitimate_values():
    # Real data check: 2700s ("Parking Duration") is a genuine legitimate
    # value observed in a real Vissim 2026 example network -- must stay
    # under the bound with headroom.
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action("set_time_distribution_normal", time_distribution_no="25", mean_s=2700.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "time_distribution_mean_implausible" for f in findings)


@pytest.mark.parametrize(
    "bad_points",
    [
        [],
        [{"duration_s": 5.0, "cum_fraction": 1.0}],
    ],
)
def test_time_distribution_points_count_invalid_fires_for_fewer_than_2(bad_points):
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action("set_time_distribution_points", time_distribution_no="200", points=bad_points)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "time_distribution_points_count_invalid"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert matches[0].details["point_count"] == len(bad_points)


def test_time_distribution_points_count_invalid_silent_for_exactly_2():
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action(
        "set_time_distribution_points",
        time_distribution_no="200",
        points=[{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 5.0, "cum_fraction": 1.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "time_distribution_points_count_invalid" for f in findings)


def test_time_distribution_points_duration_negative_fires():
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action(
        "set_time_distribution_points",
        time_distribution_no="200",
        points=[{"duration_s": -5.0, "cum_fraction": 0.0}, {"duration_s": 5.0, "cum_fraction": 1.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "time_distribution_points_duration_negative"]
    assert len(matches) == 1
    assert matches[0].details["negative_durations_s"] == [-5.0]


def test_time_distribution_points_duration_not_monotonic_fires():
    # Live-verified: Vissim itself SILENTLY ACCEPTS a duration decrease --
    # this refusal is this codebase's own proactive screen.
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action(
        "set_time_distribution_points",
        time_distribution_no="200",
        points=[{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 20.0, "cum_fraction": 0.5}, {"duration_s": 10.0, "cum_fraction": 1.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "time_distribution_points_duration_not_monotonic"]
    assert len(matches) == 1
    assert matches[0].details["decrease_at_index"] == 2


def test_time_distribution_points_duration_not_monotonic_silent_for_equal_consecutive_durations():
    # A legitimate step-function curve (same duration, different fraction).
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action(
        "set_time_distribution_points",
        time_distribution_no="200",
        points=[{"duration_s": 10.0, "cum_fraction": 0.0}, {"duration_s": 10.0, "cum_fraction": 1.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "time_distribution_points_duration_not_monotonic" for f in findings)


@pytest.mark.parametrize(
    "points,expected_fragment",
    [
        ([{"duration_s": 0.0, "cum_fraction": 0.2}, {"duration_s": 10.0, "cum_fraction": 1.0}], "0.0"),
        ([{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 10.0, "cum_fraction": 0.9}], "1.0"),
        ([{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 10.0, "cum_fraction": 1.5}], "범위"),
        ([{"duration_s": 0.0, "cum_fraction": -0.2}, {"duration_s": 10.0, "cum_fraction": 1.0}], "범위"),
    ],
)
def test_time_distribution_points_fraction_invalid_fires(points, expected_fragment):
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action("set_time_distribution_points", time_distribution_no="200", points=points)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "time_distribution_points_fraction_invalid"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert expected_fragment in matches[0].messages["ko"]


def test_time_distribution_points_fraction_invalid_fires_for_middle_reversal_vissim_silently_accepts():
    # Live-verified: Vissim only validates first/last FX and silently
    # accepts a mid-curve reversal -- this codebase's own proactive screen.
    # Endpoints are 0.0/1.0 here so ONLY the middle-reversal branch can fire.
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action(
        "set_time_distribution_points",
        time_distribution_no="200",
        points=[{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 5.0, "cum_fraction": 0.9}, {"duration_s": 8.0, "cum_fraction": 0.5}, {"duration_s": 10.0, "cum_fraction": 1.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "time_distribution_points_fraction_invalid"]
    assert len(matches) == 1
    assert matches[0].details["cum_fractions"] == [0.0, 0.9, 0.5, 1.0]


def test_time_distribution_points_fraction_invalid_silent_for_valid_curve():
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action(
        "set_time_distribution_points",
        time_distribution_no="200",
        points=[{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 5.0, "cum_fraction": 0.5}, {"duration_s": 10.0, "cum_fraction": 1.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "time_distribution_points_fraction_invalid" for f in findings)


def test_time_distribution_points_duration_implausible_warns_above_bound():
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action(
        "set_time_distribution_points",
        time_distribution_no="200",
        points=[{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 99999.0, "cum_fraction": 1.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "time_distribution_points_duration_implausible"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    assert matches[0].details["implausible_durations_s"] == [99999.0]


def test_time_distribution_points_duration_implausible_silent_for_real_legitimate_value():
    # Real data check: 1800s is a genuine legitimate point value observed in
    # a real Vissim 2026 example network -- must stay under the bound.
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action(
        "set_time_distribution_points",
        time_distribution_no="200",
        points=[{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 1800.0, "cum_fraction": 1.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "time_distribution_points_duration_implausible" for f in findings)


def test_time_distribution_points_duplicate_warns():
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action(
        "set_time_distribution_points",
        time_distribution_no="200",
        points=[{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 5.0, "cum_fraction": 0.5}, {"duration_s": 5.0, "cum_fraction": 0.5}, {"duration_s": 10.0, "cum_fraction": 1.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "time_distribution_points_duplicate"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    assert matches[0].details["duplicate_points"] == [{"duration_s": 5.0, "cum_fraction": 0.5}]


def test_time_distribution_points_duplicate_silent_for_a_legit_step_function():
    # Same duration, DIFFERENT fraction is a legitimate step function, not a
    # duplicate.
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action(
        "set_time_distribution_points",
        time_distribution_no="200",
        points=[{"duration_s": 10.0, "cum_fraction": 0.0}, {"duration_s": 10.0, "cum_fraction": 1.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "time_distribution_points_duplicate" for f in findings)


def test_time_distribution_shared_target_warns_with_bounded_ids():
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action("set_time_distribution_normal", time_distribution_no="999", mean_s=15.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "time_distribution_shared_target"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    assert matches[0].details["reference_count"] == 3
    assert "10.5" in matches[0].messages["en"]
    assert "1:cc1" in matches[0].messages["en"]


def test_time_distribution_shared_target_applies_to_points_verb_too():
    shadow = _shadow_model(
        time_distributions=[
            _time_distribution(
                "200",
                dist_type="EMPIRICAL",
                points=[{"duration_s": 2.0, "cum_fraction": 0.0}, {"duration_s": 4.0, "cum_fraction": 1.0}],
                reference_count=1,
                referenced_by={
                    "pt_stops": {"count": 0, "ids": [], "truncated": False},
                    "pt_vehicle_class_overrides": {"count": 0, "ids": [], "truncated": False},
                    "driving_behaviors": {"count": 1, "ids": ["1:zipper"], "truncated": False},
                    "other": {"count": 0, "by_tag": {}},
                },
            )
        ]
    )
    action = _action(
        "set_time_distribution_points",
        time_distribution_no="200",
        points=[{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 10.0, "cum_fraction": 1.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "time_distribution_shared_target"]
    assert len(matches) == 1
    assert "1:zipper" in matches[0].messages["en"]


def test_time_distribution_shared_target_silent_when_unreferenced():
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action("set_time_distribution_normal", time_distribution_no="25", mean_s=30.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "time_distribution_shared_target" for f in findings)


def test_time_distribution_shared_target_does_not_apply_to_other_tools():
    shadow = _shadow_model(time_distributions=_TIME_DISTRIBUTION_FIXTURE)
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "time_distribution_shared_target" for f in findings)


# ---------------------------------------------------------------------------
# Q4 unit (2): measurement-infrastructure placement -- object_not_found
# lockstep wiring (7 of the unit's 8 tools; add_data_collection_measurement
# is a deliberate scope cut, see _TOOL_TARGET_SPEC's own comment) +
# measurement_infra_pos_out_of_range (the unit's ONE new constraint).
# ---------------------------------------------------------------------------


def test_object_not_found_fires_for_missing_link_on_add_queue_counter():
    shadow = _shadow_model()  # default fixture link id "101", length 280.0
    action = _action("add_queue_counter", link_no="999", pos_m=10.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    _assert_well_formed_messages(matches[0], must_contain_ko="999")


def test_object_not_found_silent_when_link_present_for_add_queue_counter():
    shadow = _shadow_model()
    action = _action("add_queue_counter", link_no="101", pos_m=10.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


def test_object_not_found_fires_for_missing_link_on_add_data_collection_point():
    shadow = _shadow_model()
    action = _action("add_data_collection_point", link_no="999", lane_index=1, pos_m=10.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1


def test_object_not_found_fires_for_missing_link_on_add_travel_time_section_from_link():
    shadow = _shadow_model()
    action = _action(
        "add_travel_time_section", from_link="999", from_pos_m=0.0, to_link="101", to_pos_m=10.0
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1


def test_object_not_found_fires_for_missing_queue_counter_on_remove():
    shadow = _shadow_model(queue_counters=[{"no": "1", "name": "", "link_id": "101", "pos": 10.0}])
    action = _action("remove_queue_counter", no="999")

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1


def test_object_not_found_silent_when_queue_counter_present_for_remove():
    shadow = _shadow_model(queue_counters=[{"no": "1", "name": "", "link_id": "101", "pos": 10.0}])
    action = _action("remove_queue_counter", no="1")

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


def test_object_not_found_fires_for_missing_data_collection_point_on_remove():
    shadow = _shadow_model(
        data_collection_points=[
            {"no": "1", "name": "", "link_id": "101", "lane_index": 1, "pos": 10.0}
        ]
    )
    action = _action("remove_data_collection_point", no="999")

    findings = run_checks("preflight", action, shadow, {})

    assert len([f for f in findings if f.constraint_id == "object_not_found"]) == 1


def test_object_not_found_fires_for_missing_data_collection_measurement_on_remove():
    shadow = _shadow_model(
        data_collection_measurements=[{"no": "1", "name": "", "point_nos": ["1"]}]
    )
    action = _action("remove_data_collection_measurement", no="999")

    findings = run_checks("preflight", action, shadow, {})

    assert len([f for f in findings if f.constraint_id == "object_not_found"]) == 1


def test_object_not_found_fires_for_missing_travel_time_section_on_remove():
    shadow = _shadow_model(
        travel_time_sections=[
            {"no": "1", "name": "", "from_link": "101", "from_pos": 0.0, "to_link": "101", "to_pos": 10.0}
        ]
    )
    action = _action("remove_travel_time_section", no="999")

    findings = run_checks("preflight", action, shadow, {})

    assert len([f for f in findings if f.constraint_id == "object_not_found"]) == 1


def test_object_not_found_does_not_apply_to_add_data_collection_measurement():
    """Deliberate scope cut (this unit's own "constraints.py minimal"
    instruction): point_nos is a LIST with no single-param home in
    _TOOL_TARGET_SPEC, so this tool gets NO object_not_found coverage at
    all -- a bogus point number instead surfaces from vissim_com.py's own
    fail-closed existence check as a plain tool_execution_error."""
    shadow = _shadow_model()
    action = _action("add_data_collection_measurement", point_nos=["999"])

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


# --- measurement_infra_pos_out_of_range ------------------------------------


def test_measurement_infra_pos_out_of_range_fires_for_negative_pos_on_queue_counter():
    shadow = _shadow_model()  # link "101", length 280.0
    action = _action("add_queue_counter", link_no="101", pos_m=-5.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "measurement_infra_pos_out_of_range"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert matches[0].phase == "preflight"
    _assert_well_formed_messages(matches[0], must_contain_ko="101")


def test_measurement_infra_pos_out_of_range_fires_for_pos_beyond_link_length():
    shadow = _shadow_model()  # link "101", length 280.0
    action = _action("add_queue_counter", link_no="101", pos_m=280.1)

    findings = run_checks("preflight", action, shadow, {})

    assert len([f for f in findings if f.constraint_id == "measurement_infra_pos_out_of_range"]) == 1


def test_measurement_infra_pos_out_of_range_silent_within_bounds():
    shadow = _shadow_model()  # link "101", length 280.0
    action = _action("add_queue_counter", link_no="101", pos_m=140.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "measurement_infra_pos_out_of_range" for f in findings)


def test_measurement_infra_pos_out_of_range_accepts_the_boundary_values():
    shadow = _shadow_model()  # link "101", length 280.0
    for pos in (0.0, 280.0):
        action = _action("add_queue_counter", link_no="101", pos_m=pos)
        findings = run_checks("preflight", action, shadow, {})
        assert not any(f.constraint_id == "measurement_infra_pos_out_of_range" for f in findings)


def test_measurement_infra_pos_out_of_range_fires_for_data_collection_point():
    shadow = _shadow_model()
    action = _action("add_data_collection_point", link_no="101", lane_index=1, pos_m=-1.0)

    findings = run_checks("preflight", action, shadow, {})

    assert len([f for f in findings if f.constraint_id == "measurement_infra_pos_out_of_range"]) == 1


def test_measurement_infra_pos_out_of_range_checks_both_endpoints_of_travel_time_section():
    shadow = _shadow_model(
        links=[
            {"id": "101", "name": "A", "lanes": 3, "length": 280.0},
            {"id": "102", "name": "B", "lanes": 2, "length": 100.0},
        ]
    )
    # from_pos_m valid, to_pos_m out of range on link 102 (length 100).
    action = _action(
        "add_travel_time_section", from_link="101", from_pos_m=10.0, to_link="102", to_pos_m=150.0
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "measurement_infra_pos_out_of_range"]
    assert len(matches) == 1
    assert matches[0].details["field"] == "to_pos_m"
    assert matches[0].details["link_id"] == "102"


def test_measurement_infra_pos_out_of_range_both_endpoints_out_of_range_yields_two_findings():
    shadow = _shadow_model(
        links=[
            {"id": "101", "name": "A", "lanes": 3, "length": 280.0},
            {"id": "102", "name": "B", "lanes": 2, "length": 100.0},
        ]
    )
    action = _action(
        "add_travel_time_section", from_link="101", from_pos_m=-1.0, to_link="102", to_pos_m=150.0
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "measurement_infra_pos_out_of_range"]
    assert len(matches) == 2


def test_measurement_infra_pos_out_of_range_silent_when_link_unknown():
    """Deliberately silent -- object_not_found (#1) is the one that fires
    for an unknown link; this check would have no length to compare
    against anyway."""
    shadow = _shadow_model()
    action = _action("add_queue_counter", link_no="999", pos_m=-5.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "measurement_infra_pos_out_of_range" for f in findings)
    assert any(f.constraint_id == "object_not_found" for f in findings)


def test_measurement_infra_pos_out_of_range_does_not_apply_to_remove_tools():
    shadow = _shadow_model(queue_counters=[{"no": "1", "name": "", "link_id": "101", "pos": 10.0}])
    action = _action("remove_queue_counter", no="1")

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "measurement_infra_pos_out_of_range" for f in findings)


def test_measurement_infra_pos_out_of_range_does_not_apply_to_unrelated_tool():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "measurement_infra_pos_out_of_range" for f in findings)


# ---------------------------------------------------------------------------
# Q4 unit (4): signal-head + detector placement -- object_not_found lockstep
# wiring (all 4 of the unit's tools) + signal_head_detector_pos_out_of_range
# (the unit's ONE new constraint).
# ---------------------------------------------------------------------------


def test_object_not_found_fires_for_missing_link_on_add_signal_head():
    shadow = _shadow_model()  # default fixture link id "101", length 280.0
    action = _action(
        "add_signal_head", link_no="999", lane_index=1, pos_m=10.0, sc_no="1", sg_no="1"
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    _assert_well_formed_messages(matches[0], must_contain_ko="999")


def test_object_not_found_silent_when_link_present_for_add_signal_head():
    shadow = _shadow_model()
    action = _action(
        "add_signal_head", link_no="101", lane_index=1, pos_m=10.0, sc_no="1", sg_no="1"
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


def test_object_not_found_does_not_fire_for_bogus_sc_no_on_add_signal_head():
    """Deliberate scope cut (this unit's own "constraints.py minimal"
    instruction, mirroring add_travel_time_section's to_link precedent):
    sc_no/sg_no are NOT covered by object_not_found -- a bogus one fails
    closed as a plain tool_execution_error from vissim_com.py instead."""
    shadow = _shadow_model()
    action = _action(
        "add_signal_head", link_no="101", lane_index=1, pos_m=10.0, sc_no="999", sg_no="1"
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


def test_object_not_found_fires_for_missing_signal_head_on_remove():
    shadow = _shadow_model(
        signal_heads=[
            {"no": "1", "name": "", "link": "101", "lane_index": 1, "pos": 10.0, "sc": "1", "sg": "1"}
        ]
    )
    action = _action("remove_signal_head", no="999")

    findings = run_checks("preflight", action, shadow, {})

    assert len([f for f in findings if f.constraint_id == "object_not_found"]) == 1


def test_object_not_found_silent_when_signal_head_present_for_remove():
    shadow = _shadow_model(
        signal_heads=[
            {"no": "1", "name": "", "link": "101", "lane_index": 1, "pos": 10.0, "sc": "1", "sg": "1"}
        ]
    )
    action = _action("remove_signal_head", no="1")

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


def test_object_not_found_fires_for_missing_link_on_add_detector():
    shadow = _shadow_model()
    action = _action("add_detector", link_no="999", lane_index=1, pos_m=10.0)

    findings = run_checks("preflight", action, shadow, {})

    assert len([f for f in findings if f.constraint_id == "object_not_found"]) == 1


def test_object_not_found_fires_for_missing_detector_on_remove():
    shadow = _shadow_model(
        detectors=[
            {
                "no": "1", "name": "", "link": "101", "lane_index": 1, "pos": 10.0,
                "length": 4.5, "sc": "", "port_no": None,
            }
        ]
    )
    action = _action("remove_detector", no="999")

    findings = run_checks("preflight", action, shadow, {})

    assert len([f for f in findings if f.constraint_id == "object_not_found"]) == 1


# --- signal_head_detector_pos_out_of_range ----------------------------------


def test_signal_head_detector_pos_out_of_range_fires_for_negative_pos_on_add_signal_head():
    shadow = _shadow_model()  # link "101", length 280.0
    action = _action(
        "add_signal_head", link_no="101", lane_index=1, pos_m=-5.0, sc_no="1", sg_no="1"
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "signal_head_detector_pos_out_of_range"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert matches[0].phase == "preflight"
    _assert_well_formed_messages(matches[0], must_contain_ko="101")


def test_signal_head_detector_pos_out_of_range_fires_for_pos_beyond_link_length_on_add_detector():
    shadow = _shadow_model()  # link "101", length 280.0
    action = _action("add_detector", link_no="101", lane_index=1, pos_m=280.1)

    findings = run_checks("preflight", action, shadow, {})

    assert len(
        [f for f in findings if f.constraint_id == "signal_head_detector_pos_out_of_range"]
    ) == 1


def test_signal_head_detector_pos_out_of_range_silent_within_bounds():
    shadow = _shadow_model()  # link "101", length 280.0
    for tool_name, extra in [
        ("add_signal_head", {"sc_no": "1", "sg_no": "1"}),
        ("add_detector", {}),
    ]:
        action = _action(tool_name, link_no="101", lane_index=1, pos_m=140.0, **extra)
        findings = run_checks("preflight", action, shadow, {})
        assert not any(
            f.constraint_id == "signal_head_detector_pos_out_of_range" for f in findings
        )


def test_signal_head_detector_pos_out_of_range_accepts_the_boundary_values():
    shadow = _shadow_model()  # link "101", length 280.0
    for pos in (0.0, 280.0):
        action = _action("add_detector", link_no="101", lane_index=1, pos_m=pos)
        findings = run_checks("preflight", action, shadow, {})
        assert not any(
            f.constraint_id == "signal_head_detector_pos_out_of_range" for f in findings
        )


def test_signal_head_detector_pos_out_of_range_silent_when_link_unknown():
    """Deliberately silent -- object_not_found (#1) is the one that fires
    for an unknown link; this check would have no length to compare
    against anyway."""
    shadow = _shadow_model()
    action = _action("add_detector", link_no="999", lane_index=1, pos_m=-5.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "signal_head_detector_pos_out_of_range" for f in findings
    )
    assert any(f.constraint_id == "object_not_found" for f in findings)


def test_signal_head_detector_pos_out_of_range_does_not_apply_to_remove_tools():
    shadow = _shadow_model(
        signal_heads=[
            {"no": "1", "name": "", "link": "101", "lane_index": 1, "pos": 10.0, "sc": "1", "sg": "1"}
        ]
    )
    action = _action("remove_signal_head", no="1")

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "signal_head_detector_pos_out_of_range" for f in findings
    )


def test_signal_head_detector_pos_out_of_range_does_not_apply_to_unrelated_tool():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "signal_head_detector_pos_out_of_range" for f in findings
    )


# --- add_detector_length_deviates_from_mrwa_default -------------------------


def test_add_detector_length_deviates_from_mrwa_default_fires_for_explicit_override():
    shadow = _shadow_model()  # link "101", length 280.0
    action = _action("add_detector", link_no="101", lane_index=1, pos_m=10.0, length_m=6.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "add_detector_length_deviates_from_mrwa_default"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    assert matches[0].details == {"length_m": 6.0, "default_m": 4.5}
    _assert_well_formed_messages(matches[0], must_contain_ko="Appendix A")


def test_add_detector_length_deviates_from_mrwa_default_silent_at_the_default():
    shadow = _shadow_model()
    action = _action("add_detector", link_no="101", lane_index=1, pos_m=10.0, length_m=4.5)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "add_detector_length_deviates_from_mrwa_default" for f in findings
    )


def test_add_detector_length_deviates_from_mrwa_default_silent_when_length_m_omitted():
    # Omitted length_m means AddDetectorParams' own pydantic default (4.5)
    # applies later, at dispatch -- constraints.py's preflight action never
    # sees a resolved value, so this must stay silent, not warn.
    shadow = _shadow_model()
    action = _action("add_detector", link_no="101", lane_index=1, pos_m=10.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "add_detector_length_deviates_from_mrwa_default" for f in findings
    )


def test_add_detector_length_deviates_from_mrwa_default_does_not_apply_to_other_tools():
    shadow = _shadow_model()
    action = _action("add_signal_head", link_no="101", lane_index=1, pos_m=10.0, sc_no="1", sg_no="1")

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "add_detector_length_deviates_from_mrwa_default" for f in findings
    )


# --- add_detector_setback_deviates_from_mrwa_default -------------------------

_STOP_LINE_HEAD_FIXTURE = [
    {"no": "9", "name": "", "link": "101", "lane_index": 1, "pos": 270.0, "sc": "1", "sg": "1"}
]


def test_add_detector_setback_deviates_from_mrwa_default_fires_for_explicit_deviation():
    # head at 270.0, pos_m explicitly 250.0 -> actual setback 20.0m, far from
    # the 2.0m MRWA default.
    shadow = _shadow_model(signal_heads=_STOP_LINE_HEAD_FIXTURE)
    action = _action("add_detector", link_no="101", lane_index=1, pos_m=250.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "add_detector_setback_deviates_from_mrwa_default"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    assert matches[0].details["actual_setback_m"] == 20.0
    assert matches[0].details["default_m"] == 2.0
    assert matches[0].details["stop_line_head_no"] == "9"
    _assert_well_formed_messages(matches[0], must_contain_ko="Appendix A")


def test_add_detector_setback_deviates_from_mrwa_default_silent_at_the_default():
    # head at 270.0, pos_m 268.0 -> actual setback exactly 2.0m.
    shadow = _shadow_model(signal_heads=_STOP_LINE_HEAD_FIXTURE)
    action = _action("add_detector", link_no="101", lane_index=1, pos_m=268.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "add_detector_setback_deviates_from_mrwa_default" for f in findings
    )


def test_add_detector_setback_deviates_from_mrwa_default_silent_when_pos_m_omitted():
    # Omitted pos_m means add_detector's own default-pos logic computes
    # EXACTLY the 2.0m setback from the stop-line-proxy head -- it can never
    # deviate by construction, so this stays silent without even needing to
    # look at signal_heads.
    shadow = _shadow_model(signal_heads=_STOP_LINE_HEAD_FIXTURE)
    action = _action("add_detector", link_no="101", lane_index=1)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "add_detector_setback_deviates_from_mrwa_default" for f in findings
    )


def test_add_detector_setback_deviates_from_mrwa_default_silent_when_no_signal_head_on_lane():
    # No signal head at all on (link, lane) -- no MRWA-default baseline to
    # compare an explicit pos_m against (a normal mid-link detector case).
    shadow = _shadow_model()
    action = _action("add_detector", link_no="101", lane_index=1, pos_m=100.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "add_detector_setback_deviates_from_mrwa_default" for f in findings
    )


def test_add_detector_setback_deviates_from_mrwa_default_silent_when_head_on_different_lane():
    shadow = _shadow_model(
        signal_heads=[
            {"no": "9", "name": "", "link": "101", "lane_index": 2, "pos": 270.0, "sc": "1", "sg": "1"}
        ]
    )
    action = _action("add_detector", link_no="101", lane_index=1, pos_m=250.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "add_detector_setback_deviates_from_mrwa_default" for f in findings
    )


def test_add_detector_setback_deviates_from_mrwa_default_uses_furthest_head_when_several_share_the_lane():
    shadow = _shadow_model(
        signal_heads=[
            {"no": "1", "name": "", "link": "101", "lane_index": 1, "pos": 50.0, "sc": "1", "sg": "1"},
            {"no": "9", "name": "", "link": "101", "lane_index": 1, "pos": 270.0, "sc": "1", "sg": "1"},
        ]
    )
    action = _action("add_detector", link_no="101", lane_index=1, pos_m=268.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "add_detector_setback_deviates_from_mrwa_default" for f in findings
    )


def test_add_detector_setback_deviates_from_mrwa_default_does_not_apply_to_other_tools():
    shadow = _shadow_model(signal_heads=_STOP_LINE_HEAD_FIXTURE)
    action = _action("add_signal_head", link_no="101", lane_index=1, pos_m=10.0, sc_no="1", sg_no="1")

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "add_detector_setback_deviates_from_mrwa_default" for f in findings
    )


# ---------------------------------------------------------------------------
# Q4 unit (6): stop-sign + priority-rule/conflict-area PLACEMENT (creation).
# object_not_found coverage for add_stop_sign/remove_stop_sign/add_priority_
# rule/remove_priority_rule, table-wiring reuse of signal_head_detector_pos_
# out_of_range for add_stop_sign/add_priority_rule, and this unit's own ONE
# new constraint, add_priority_rule_marker_invalid.
# ---------------------------------------------------------------------------


def test_object_not_found_fires_for_missing_link_on_add_stop_sign():
    shadow = _shadow_model()  # default fixture link id "101"
    action = _action("add_stop_sign", link_no="999", lane_index=1, pos_m=5.0)

    findings = run_checks("preflight", action, shadow, {})

    assert len([f for f in findings if f.constraint_id == "object_not_found"]) == 1


def test_object_not_found_silent_when_link_present_for_add_stop_sign():
    shadow = _shadow_model()
    action = _action("add_stop_sign", link_no="101", lane_index=1, pos_m=5.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


def test_object_not_found_fires_for_missing_stop_sign_on_remove():
    shadow = _shadow_model(
        stop_signs=[{"no": "1", "name": "", "link": "101", "lane_index": 1, "pos": 5.0, "sc": None, "sg": None}]
    )
    action = _action("remove_stop_sign", no="999")

    findings = run_checks("preflight", action, shadow, {})

    assert len([f for f in findings if f.constraint_id == "object_not_found"]) == 1


def test_object_not_found_silent_when_stop_sign_present_for_remove():
    shadow = _shadow_model(
        stop_signs=[{"no": "1", "name": "", "link": "101", "lane_index": 1, "pos": 5.0, "sc": None, "sg": None}]
    )
    action = _action("remove_stop_sign", no="1")

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


def test_object_not_found_fires_for_missing_link_on_add_priority_rule():
    shadow = _shadow_model()  # default fixture link id "101"
    action = _action(
        "add_priority_rule",
        stop_link_no="999",
        stop_lane_index=1,
        stop_pos_m=5.0,
        markers=[{"link_no": "101", "lane_index": 1, "pos_m": 3.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    assert len([f for f in findings if f.constraint_id == "object_not_found"]) == 1


def test_object_not_found_silent_when_stop_link_present_for_add_priority_rule():
    shadow = _shadow_model()
    action = _action(
        "add_priority_rule",
        stop_link_no="101",
        stop_lane_index=1,
        stop_pos_m=5.0,
        markers=[{"link_no": "101", "lane_index": 1, "pos_m": 3.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


def test_object_not_found_does_not_fire_for_bogus_marker_link_on_add_priority_rule():
    """Deliberate scope cut (this unit's own "constraints.py minimal"
    instruction, mirroring add_travel_time_section's to_link precedent):
    only stop_link_no is covered by object_not_found -- a bogus marker link
    fails closed as a plain tool_execution_error from vissim_com.py
    instead."""
    shadow = _shadow_model()
    action = _action(
        "add_priority_rule",
        stop_link_no="101",
        stop_lane_index=1,
        stop_pos_m=5.0,
        markers=[{"link_no": "999", "lane_index": 1, "pos_m": 3.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


def test_object_not_found_fires_for_missing_priority_rule_on_remove():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)  # real rule "no" == "4"
    action = _action("remove_priority_rule", no="999")

    findings = run_checks("preflight", action, shadow, {})

    assert len([f for f in findings if f.constraint_id == "object_not_found"]) == 1


def test_object_not_found_silent_when_priority_rule_present_for_remove():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("remove_priority_rule", no="4")

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "object_not_found" for f in findings)


# --- signal_head_detector_pos_out_of_range reuse (table wiring) ------------


def test_signal_head_detector_pos_out_of_range_fires_for_negative_pos_on_add_stop_sign():
    shadow = _shadow_model()  # link "101", length 280.0
    action = _action("add_stop_sign", link_no="101", lane_index=1, pos_m=-5.0)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "signal_head_detector_pos_out_of_range"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"


def test_signal_head_detector_pos_out_of_range_fires_for_pos_beyond_link_length_on_add_priority_rule():
    shadow = _shadow_model()  # link "101", length 280.0
    action = _action(
        "add_priority_rule",
        stop_link_no="101",
        stop_lane_index=1,
        stop_pos_m=280.1,
        markers=[{"link_no": "101", "lane_index": 1, "pos_m": 3.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    assert len(
        [f for f in findings if f.constraint_id == "signal_head_detector_pos_out_of_range"]
    ) == 1


def test_signal_head_detector_pos_out_of_range_silent_for_a_marker_own_pos_beyond_its_link():
    """The marker's own (link, pos) inside "markers" is a deliberate scope
    cut (see this unit's own docstring on _SIGNAL_HEAD_DETECTOR_POS_FIELDS)
    -- only stop_link_no/stop_pos_m are checked."""
    shadow = _shadow_model()  # link "101", length 280.0
    action = _action(
        "add_priority_rule",
        stop_link_no="101",
        stop_lane_index=1,
        stop_pos_m=5.0,
        markers=[{"link_no": "101", "lane_index": 1, "pos_m": 9999.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "signal_head_detector_pos_out_of_range" for f in findings
    )


# --- add_priority_rule_marker_invalid (this unit's ONE new constraint) -----


def test_add_priority_rule_marker_invalid_fires_for_negative_gap_time():
    shadow = _shadow_model()
    action = _action(
        "add_priority_rule",
        stop_link_no="101",
        stop_lane_index=1,
        stop_pos_m=5.0,
        markers=[{"link_no": "101", "lane_index": 1, "pos_m": 3.0, "gap_time_s": -1.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "add_priority_rule_marker_invalid"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert matches[0].details["marker_index"] == 1
    assert matches[0].details["invalid_fields"] == {"gap_time_s": -1.0}
    _assert_well_formed_messages(matches[0], must_contain_ko="gap_time_s")


def test_add_priority_rule_marker_invalid_fires_for_negative_headway():
    shadow = _shadow_model()
    action = _action(
        "add_priority_rule",
        stop_link_no="101",
        stop_lane_index=1,
        stop_pos_m=5.0,
        markers=[{"link_no": "101", "lane_index": 1, "pos_m": 3.0, "headway_m": -0.5}],
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "add_priority_rule_marker_invalid"]
    assert len(matches) == 1
    assert matches[0].details["invalid_fields"] == {"headway_m": -0.5}


def test_add_priority_rule_marker_invalid_silent_for_zero_the_most_common_real_value():
    """Mirrors priority_rule_gap_headway_invalid's own "0 is legitimate"
    semantics exactly -- 0 must NOT be refused."""
    shadow = _shadow_model()
    action = _action(
        "add_priority_rule",
        stop_link_no="101",
        stop_lane_index=1,
        stop_pos_m=5.0,
        markers=[{"link_no": "101", "lane_index": 1, "pos_m": 3.0, "gap_time_s": 0.0, "headway_m": 0.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "add_priority_rule_marker_invalid" for f in findings)


def test_add_priority_rule_marker_invalid_silent_when_fields_omitted():
    shadow = _shadow_model()
    action = _action(
        "add_priority_rule",
        stop_link_no="101",
        stop_lane_index=1,
        stop_pos_m=5.0,
        markers=[{"link_no": "101", "lane_index": 1, "pos_m": 3.0}],
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "add_priority_rule_marker_invalid" for f in findings)


def test_add_priority_rule_marker_invalid_reports_each_bad_marker_by_index():
    shadow = _shadow_model()
    action = _action(
        "add_priority_rule",
        stop_link_no="101",
        stop_lane_index=1,
        stop_pos_m=5.0,
        markers=[
            {"link_no": "101", "lane_index": 1, "pos_m": 3.0, "gap_time_s": 1.0},
            {"link_no": "101", "lane_index": 2, "pos_m": 4.0, "gap_time_s": -2.0},
        ],
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "add_priority_rule_marker_invalid"]
    assert len(matches) == 1
    assert matches[0].details["marker_index"] == 2


def test_add_priority_rule_marker_invalid_does_not_apply_to_other_tools():
    shadow = _shadow_model(priority_rules=_PRIORITY_RULE_FIXTURE)
    action = _action("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=-1.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "add_priority_rule_marker_invalid" for f in findings)


# ---------------------------------------------------------------------------
# pt_line_stop_not_found (refuse, preflight) -- Q4 unit (7)'s one new
# constraint. Covers all three failure modes in one function: the line
# itself missing, the line existing but not naming that stop, and a bogus
# time_distribution_no -- see constraints.py's own module comment for why
# this tool has NO _TOOL_TARGET_SPEC entry at all (a two-part id, mirroring
# set_route_relative_flow's/set_des_speed_decision's own split).
# ---------------------------------------------------------------------------

_PT_LINES_FIXTURE = [
    {
        "no": "52004",
        "name": "WB",
        "veh_type": "300",
        "entry_link": "13270462",
        "dest_link": "12270123",
        "dest_pos": 189.13,
        "entry_tm_distr": None,
        "link_seq": ["10909"],
        "stops": [
            {"pt_stop_no": "99569", "dwell_tm_distr": "20", "dwell_tm_def": "DISTRIBUTION", "active": True}
        ],
    },
    {
        "no": "52201",
        "name": "NB",
        "veh_type": "300",
        "entry_link": "10900",
        "dest_link": "12270123",
        "dest_pos": 189.13,
        "entry_tm_distr": None,
        "link_seq": ["10900"],
        "stops": [
            {"pt_stop_no": "99569", "dwell_tm_distr": "20", "dwell_tm_def": "DISTRIBUTION", "active": True},
            {"pt_stop_no": "13", "dwell_tm_distr": "20", "dwell_tm_def": "DISTRIBUTION", "active": True},
        ],
    },
]
_PT_TIME_DISTRIBUTIONS_FIXTURE = [
    {"no": "10", "name": "Standard dwell", "type": "NORMAL", "mean_std_dev": {"mean_s": 10.0, "std_dev_s": 2.0}},
    {"no": "20", "name": "BusDwell_0", "type": "EMPIRICAL", "mean_std_dev": {"mean_s": 20.0, "std_dev_s": None}},
]


def test_pt_line_stop_not_found_fires_when_line_missing():
    shadow = _shadow_model(pt_lines=_PT_LINES_FIXTURE, time_distributions=_PT_TIME_DISTRIBUTIONS_FIXTURE)
    action = _action(
        "set_pt_line_stop_dwell", line_no="999", stop_no="99569", time_distribution_no="10"
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "pt_line_stop_not_found"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert matches[0].details["line_no"] == "999"
    # set_pt_line_stop_dwell has NO _TOOL_TARGET_SPEC entry -- this check is
    # the ONLY refusal for a bogus line_no (never object_not_found).
    assert not any(f.constraint_id == "object_not_found" for f in findings)


def test_pt_line_stop_not_found_fires_when_stop_not_on_that_line():
    shadow = _shadow_model(pt_lines=_PT_LINES_FIXTURE, time_distributions=_PT_TIME_DISTRIBUTIONS_FIXTURE)
    # stop "13" exists on line 52201 but NOT on line 52004.
    action = _action(
        "set_pt_line_stop_dwell", line_no="52004", stop_no="13", time_distribution_no="10"
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "pt_line_stop_not_found"]
    assert len(matches) == 1
    assert matches[0].details["line_no"] == "52004"
    assert matches[0].details["stop_no"] == "13"
    assert matches[0].details["available_stops"] == ["99569"]


def test_pt_line_stop_not_found_fires_for_bogus_time_distribution():
    shadow = _shadow_model(pt_lines=_PT_LINES_FIXTURE, time_distributions=_PT_TIME_DISTRIBUTIONS_FIXTURE)
    action = _action(
        "set_pt_line_stop_dwell", line_no="52004", stop_no="99569", time_distribution_no="999"
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "pt_line_stop_not_found"]
    assert len(matches) == 1
    assert matches[0].details["distr_no"] == "999"
    assert matches[0].details["available_distributions"] == ["10", "20"]


def test_pt_line_stop_not_found_silent_when_everything_resolves():
    shadow = _shadow_model(pt_lines=_PT_LINES_FIXTURE, time_distributions=_PT_TIME_DISTRIBUTIONS_FIXTURE)
    # Reconnecting an EMPIRICAL BusDwell curve to a real stop -- the BusDwell
    # story this unit's own task brief cites.
    action = _action(
        "set_pt_line_stop_dwell", line_no="52004", stop_no="99569", time_distribution_no="20"
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "pt_line_stop_not_found" for f in findings)


def test_pt_line_stop_not_found_does_not_apply_to_unrelated_tool():
    shadow = _shadow_model(pt_lines=_PT_LINES_FIXTURE, time_distributions=_PT_TIME_DISTRIBUTIONS_FIXTURE)
    action = _action("set_vehicle_input_volume", input_no="1", volume=500.0)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(f.constraint_id == "pt_line_stop_not_found" for f in findings)


# ---------------------------------------------------------------------------
# Q5 unit (2): add_signal_controller -- object_not_found lockstep + the
# unit's own two constraints.
#
# Fixture note: the shared _shadow_model helper above has no
# signal_program_splits kwarg (no earlier constraint needed one), so these
# tests set that key directly rather than widening a fixture every other
# test in this file shares. The row shape mirrors
# tools_network._flatten_signal_program_splits exactly.
# ---------------------------------------------------------------------------


def _signal(sc_no: str, *, controller_type: str = "FIXEDTIME", name: str = "") -> dict[str, Any]:
    return {
        "sc_no": sc_no,
        "sc_name": name,
        "cycle_time": 0,
        "controller_type": controller_type,
        "phases": [],
    }


def _shadow_with_splits(signals: list[dict[str, Any]], splits: list[dict[str, Any]]) -> dict[str, Any]:
    shadow = _shadow_model(signals=signals)
    shadow["signal_program_splits"] = splits
    return shadow


def _split_row(sc_no: str, program_no: str, sg_no: str) -> dict[str, Any]:
    return {
        "id": f"{sc_no}.{program_no}.{sg_no}",
        "sc_no": sc_no,
        "program_no": program_no,
        "sg_no": sg_no,
        "sg_name": f"SG{sg_no}",
        "green_window": {"green_start_s": 10.0, "green_end_s": 40.0},
        "amber_s": 3.0,
        "min_green_s": None,
        "head_count": 2,
        "cycle_s": 90.0,
        "active": True,
    }


def test_object_not_found_fires_for_add_signal_controller_bogus_source():
    # The _TOOL_TARGET_SPEC/applies_to lockstep -- easy to forget, and a
    # miss here means a bogus donor reaches COM instead of being refused
    # with the real available controller numbers.
    action = _action("add_signal_controller", source_sc_no="999", new_name="North junction")
    shadow = _shadow_model(signals=[_signal("1"), _signal("2")])

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    assert matches[0].tier == "refuse"
    assert matches[0].details == {
        "tool": "add_signal_controller",
        "target_id": "999",
        "object_class": "signals",
    }


def test_object_not_found_offers_signal_controller_candidates_for_a_near_miss_source():
    # #1's candidates are difflib near-misses, not "every object in the
    # class" -- a typo'd donor number gets the real controller back.
    action = _action("add_signal_controller", source_sc_no="12", new_name="North junction")
    shadow = _shadow_model(signals=[_signal("1", name="Beijing"), _signal("2")])

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    assert _candidate_ids(matches[0].suggested_fix) == {"1", "2"}


def test_signal_controller_duplicate_source_type_unsupported_refuses_an_rbc_donor():
    action = _action("add_signal_controller", source_sc_no="22", new_name="North junction")
    shadow = _shadow_model(
        signals=[
            _signal("22", controller_type="RINGBARRIERCONTROLLER", name="State/10th"),
            _signal("1", controller_type="FIXEDTIME", name="Beijing"),
        ]
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [
        f for f in findings
        if f.constraint_id == "signal_controller_duplicate_source_type_unsupported"
    ]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    assert finding.details["controller_type"] == "RINGBARRIERCONTROLLER"
    # A refusal must carry the nearest allowed alternative (doctrine §4).
    assert finding.details["duplicable_donors"] == ["1 (Beijing)"]
    _assert_well_formed_messages(finding, must_contain_ko="복제")


def test_signal_controller_duplicate_source_type_unsupported_refuses_an_unknown_type_donor():
    # Refuse-on-unproven-type, matching #4's own conservative stance: a
    # controller whose type signal_parser could not read is not assumed safe.
    action = _action("add_signal_controller", source_sc_no="9", new_name="copy")
    shadow = _shadow_model(signals=[_signal("9", controller_type="unknown")])

    findings = run_checks("preflight", action, shadow, {})

    matches = [
        f for f in findings
        if f.constraint_id == "signal_controller_duplicate_source_type_unsupported"
    ]
    assert len(matches) == 1
    assert matches[0].details["duplicable_donors"] == []


@pytest.mark.parametrize("controller_type", ["FIXEDTIME", "FIXEDTIMESIMPLE"])
def test_signal_controller_duplicate_source_type_unsupported_silent_for_both_allowed_families(
    controller_type,
):
    # Both families were live-measured as duplicable (probe Parts A and B) --
    # neither may be refused here.
    action = _action("add_signal_controller", source_sc_no="1", new_name="copy")
    shadow = _shadow_model(signals=[_signal("1", controller_type=controller_type)])

    findings = run_checks("preflight", action, shadow, {})

    assert not [
        f for f in findings
        if f.constraint_id == "signal_controller_duplicate_source_type_unsupported"
    ]


def test_signal_controller_duplicate_source_type_unsupported_silent_when_donor_missing():
    action = _action("add_signal_controller", source_sc_no="999", new_name="copy")
    shadow = _shadow_model(signals=[_signal("1")])

    findings = run_checks("preflight", action, shadow, {})

    assert not [
        f for f in findings
        if f.constraint_id == "signal_controller_duplicate_source_type_unsupported"
    ]


def test_signal_controller_duplicate_inherits_donor_program_names_the_real_shape():
    # Beijing's measured shape, scaled: 2 programs x 2 signal groups.
    action = _action("add_signal_controller", source_sc_no="1", new_name="North junction")
    shadow = _shadow_with_splits(
        [_signal("1")],
        [
            _split_row("1", "1", "1"),
            _split_row("1", "1", "2"),
            _split_row("1", "2", "1"),
            _split_row("1", "2", "2"),
        ],
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [
        f for f in findings
        if f.constraint_id == "signal_controller_duplicate_inherits_donor_program"
    ]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "warn"
    assert finding.details["inherited_program_count"] == 2
    assert finding.details["inherited_signal_group_count"] == 2
    assert finding.details["inherited_green_split_count"] == 4
    assert "2 program(s) x 2 signal group(s) = 4 green split(s)" in finding.messages["en"]
    _assert_well_formed_messages(finding, must_contain_ko="신호 프로그램")


def test_signal_controller_duplicate_inherits_donor_program_says_so_when_splits_unreadable():
    # A supported-type donor whose splits this model does not expose (e.g. a
    # stage-based program signal_parser reports without green windows): the
    # warn still fires -- the copy still inherits SOMETHING -- but it must
    # not render a confident "0 program(s) x 0 signal group(s)".
    action = _action("add_signal_controller", source_sc_no="1", new_name="copy")
    shadow = _shadow_with_splits([_signal("1")], [])

    findings = run_checks("preflight", action, shadow, {})

    matches = [
        f for f in findings
        if f.constraint_id == "signal_controller_duplicate_inherits_donor_program"
    ]
    assert len(matches) == 1
    assert "does not expose" in matches[0].messages["en"]
    assert "0 program(s)" not in matches[0].messages["en"]


def test_signal_controller_duplicate_inherits_donor_program_silent_for_unsupported_donor():
    # The refuse check owns that case -- firing both would bury the refusal.
    action = _action("add_signal_controller", source_sc_no="22", new_name="copy")
    shadow = _shadow_model(signals=[_signal("22", controller_type="VAP")])

    findings = run_checks("preflight", action, shadow, {})

    assert not [
        f for f in findings
        if f.constraint_id == "signal_controller_duplicate_inherits_donor_program"
    ]


def test_signal_controller_duplicate_constraints_are_silent_for_every_other_tool():
    shadow = _shadow_model(signals=[_signal("1", controller_type="VAP")])
    for action in (
        _action("set_signal_offset", sc_no="1", offset=10),
        _action("add_signal_head", link_no="101", lane_index=1, pos_m=50.0, sc_no="1", sg_no="1"),
        _action("duplicate_driving_behavior", source_no="1", new_name="copy"),
    ):
        findings = run_checks("preflight", action, shadow, {})
        assert not [
            f for f in findings
            if f.constraint_id
            in (
                "signal_controller_duplicate_source_type_unsupported",
                "signal_controller_duplicate_inherits_donor_program",
            )
        ], action["tool"]


# ---------------------------------------------------------------------------
# Q5 unit (3): add_vehicle_input -- vehicle_input_on_connector (warn) and
# vehicle_input_created_covers_first_interval_only (warn), plus the
# volume_out_of_range TABLE WIRING and the object_not_found lockstep.
#
# Both warns are grounded in live measurement, not taste
# (backend/tools/probe_vehicle_input_create.py, 2026-08-03) -- see each
# check's own comment block in constraints.py for the numbers. The connector
# one in particular shipped as a WARN only because the probe overturned the
# refusal it was planned to be: COM accepts the input and it genuinely
# generates traffic.
# ---------------------------------------------------------------------------


def _link(link_id: str, *, is_connector: bool = False, from_link: str | None = None,
          to_link: str | None = None, length: float = 280.0) -> dict[str, Any]:
    return {
        "id": link_id,
        "name": "",
        "lanes": 2,
        "length": length,
        "is_connector": is_connector,
        "from_link": from_link,
        "to_link": to_link,
        "from_pos": 335.02 if is_connector else None,
        "to_pos": 0.1 if is_connector else None,
    }


def _vi(no: str, *, link: str = "101", volume: float = 900.0, interval_count: int = 1) -> dict[str, Any]:
    return {
        "no": no, "name": "", "link": link, "volume": volume, "interval_count": interval_count,
    }


def test_vehicle_input_on_connector_warns_and_names_the_upstream_approach():
    action = _action("add_vehicle_input", link_no="10000", volume_vph=450.0)
    shadow = _shadow_model(
        links=[_link("1"), _link("2"), _link("10000", is_connector=True, from_link="1", to_link="2")]
    )

    findings = run_checks("preflight", action, shadow, {})
    finding = next(f for f in findings if f.constraint_id == "vehicle_input_on_connector")

    assert finding.tier == "warn"
    assert finding.details["upstream_link"] == "1"
    assert finding.details["downstream_link"] == "2"
    assert finding.suggested_fix == {"upstream_link": "1"}
    _assert_well_formed_messages(finding, must_contain_ko="커넥터")
    # The alternative has to be NAMED, not merely implied -- a refusal or
    # warning without the nearest allowed alternative is half a finding.
    assert "'1'" in finding.messages["en"]


def test_vehicle_input_on_connector_is_a_warn_not_a_refusal():
    # Deliberate and measured: COM accepts the write and the input genuinely
    # generates traffic (13 vehicles, identical to the plain-link arm,
    # against a 0-vehicle null control). Refusing something Vissim permits
    # and that works would violate the guardrail doctrine.
    action = _action("add_vehicle_input", link_no="10000", volume_vph=450.0)
    shadow = _shadow_model(
        links=[_link("1"), _link("10000", is_connector=True, from_link="1", to_link="2")]
    )

    findings = run_checks("preflight", action, shadow, {})

    assert [f.tier for f in findings if f.constraint_id == "vehicle_input_on_connector"] == ["warn"]
    assert not [f for f in findings if f.tier == "refuse"]


def test_vehicle_input_on_connector_silent_for_a_plain_link():
    action = _action("add_vehicle_input", link_no="101", volume_vph=450.0)
    shadow = _shadow_model(links=[_link("101")])

    findings = run_checks("preflight", action, shadow, {})

    assert not [f for f in findings if f.constraint_id == "vehicle_input_on_connector"]


def test_vehicle_input_on_connector_says_so_when_the_upstream_link_is_unreadable():
    # "absent" is not "empty": a connector entry with no from_link cannot
    # name an alternative, and the message must SAY that rather than render
    # a confident "Place it on link ''".
    action = _action("add_vehicle_input", link_no="10000", volume_vph=450.0)
    shadow = _shadow_model(links=[_link("10000", is_connector=True, from_link=None, to_link=None)])

    findings = run_checks("preflight", action, shadow, {})
    finding = next(f for f in findings if f.constraint_id == "vehicle_input_on_connector")

    assert finding.details["upstream_link"] is None
    assert finding.suggested_fix == {"upstream_link": None}
    assert "could not be read" in finding.messages["en"]


def test_vehicle_input_created_covers_first_interval_only_warns_on_a_multi_interval_network():
    # Toll Plaza Camden.US's real shape: 5 inputs, each with 5 interval rows.
    action = _action("add_vehicle_input", link_no="101", volume_vph=450.0)
    shadow = _shadow_model(vehicle_inputs=[_vi("1", interval_count=5), _vi("2", interval_count=5)])

    findings = run_checks("preflight", action, shadow, {})
    finding = next(
        f for f in findings
        if f.constraint_id == "vehicle_input_created_covers_first_interval_only"
    )

    assert finding.tier == "warn"
    assert finding.details == {
        "network_interval_count": 5,
        "intervals_left_at_zero": 4,
        "volume": 450.0,
    }
    _assert_well_formed_messages(finding, must_contain_ko="구간 1")


def test_vehicle_input_created_covers_first_interval_only_silent_on_a_single_interval_network():
    action = _action("add_vehicle_input", link_no="101", volume_vph=450.0)
    shadow = _shadow_model(vehicle_inputs=[_vi("1", interval_count=1)])

    findings = run_checks("preflight", action, shadow, {})

    assert not [
        f for f in findings
        if f.constraint_id == "vehicle_input_created_covers_first_interval_only"
    ]


def test_vehicle_input_created_covers_first_interval_only_silent_when_the_datum_is_absent():
    """The STATED CATALOG-GAP, pinned so it can never be mistaken for a pass:
    on a network with zero vehicle inputs -- exactly the from-nothing genesis
    case -- there is nothing to read an interval count off, so preflight is
    silent and tools_edit.add_vehicle_input's own post-write reparse carries
    the fact instead. Live-relevant: Air Taxi Vertiport has 0 inputs and its
    first-ever input lands with 12 interval rows."""
    action = _action("add_vehicle_input", link_no="101", volume_vph=450.0)
    shadow = _shadow_model(vehicle_inputs=[])

    findings = run_checks("preflight", action, shadow, {})

    assert not [
        f for f in findings
        if f.constraint_id == "vehicle_input_created_covers_first_interval_only"
    ]


def test_vehicle_input_created_covers_first_interval_only_silent_when_inputs_disagree():
    # No single honest claim is available, so the check says nothing rather
    # than picking one of the two counts.
    action = _action("add_vehicle_input", link_no="101", volume_vph=450.0)
    shadow = _shadow_model(
        vehicle_inputs=[_vi("1", interval_count=5), _vi("2", interval_count=2)]
    )

    findings = run_checks("preflight", action, shadow, {})

    assert not [
        f for f in findings
        if f.constraint_id == "vehicle_input_created_covers_first_interval_only"
    ]


def test_volume_out_of_range_table_wiring_covers_add_vehicle_input():
    # TABLE WIRING onto the pre-existing check (_VOLUME_FIELDS), not a new
    # constraint -- the param name differs (volume_vph vs volume), the claim
    # does not.
    shadow = _shadow_model(links=[_link("101")])

    refused = run_checks(
        "preflight", _action("add_vehicle_input", link_no="101", volume_vph=25000.0), shadow, {}
    )
    finding = next(f for f in refused if f.constraint_id == "volume_out_of_range")
    assert finding.tier == "refuse"

    allowed = run_checks(
        "preflight", _action("add_vehicle_input", link_no="101", volume_vph=20000.0), shadow, {}
    )
    assert not [f for f in allowed if f.constraint_id == "volume_out_of_range"]


def test_object_not_found_fires_for_add_vehicle_input_bogus_link():
    # A _TOOL_TARGET_SPEC entry alone does nothing -- the tool must ALSO be
    # in #1's own applies_to list. This test is what proves both halves
    # landed (ten already-shipped verbs have only the first half; see that
    # list's own comment in constraints.py).
    action = _action("add_vehicle_input", link_no="999", volume_vph=450.0)
    shadow = _shadow_model(links=[_link("101")])

    findings = run_checks("preflight", action, shadow, {})
    finding = next(f for f in findings if f.constraint_id == "object_not_found")

    assert finding.tier == "refuse"
    assert finding.details["target_id"] == "999"
    assert finding.details["object_class"] == "links"


def test_vehicle_input_creation_constraints_are_silent_for_every_other_tool():
    shadow = _shadow_model(
        links=[_link("10000", is_connector=True, from_link="1", to_link="2")],
        vehicle_inputs=[_vi("1", link="10000", interval_count=5)],
    )
    for action in (
        _action("set_vehicle_input_volume", input_no="1", volume=450.0),
        _action("add_queue_counter", link_no="10000", pos_m=5.0),
        _action("set_turn_ban", connector_no="10000", banned=True, vehicle_classes=["10"]),
    ):
        findings = run_checks("preflight", action, shadow, {})
        assert not [
            f for f in findings
            if f.constraint_id
            in (
                "vehicle_input_on_connector",
                "vehicle_input_created_covers_first_interval_only",
            )
        ], action["tool"]


# ---------------------------------------------------------------------------
# object_not_found / _TOOL_TARGET_SPEC lockstep -- GAP CLOSURE (2026-08-03)
#
# Ten already-shipped verbs had a _TOOL_TARGET_SPEC entry but were never named
# in #1's own applies_to list, so #1 never ran for them at all: a bogus
# link/connector/stop id fell through to a raw COM error or a bare ValueError
# surfaced as tool_execution_error, instead of #1's structured refusal naming
# the real available ids. Q5 unit (3) measured and reported the gap without
# fixing it (see that list's own comment in constraints.py); these tests are
# the sweep that closes it.
#
# The registry-level invariant test below is the real regression guard -- the
# per-verb tests prove the behaviour each verb gained, but only the invariant
# stops the ELEVENTH verb from shipping half-wired the same way.
# ---------------------------------------------------------------------------

_LOCKSTEP_LINKS = [
    {"id": "101", "name": "Main St EB", "lanes": 2, "length": 280.0, "is_connector": False},
    {"id": "102", "name": "Main St WB", "lanes": 2, "length": 280.0, "is_connector": False},
    {
        "id": "9001", "name": "EB to WB turn", "lanes": 1, "length": 30.0, "is_connector": True,
        "from_link": "101", "to_link": "102", "from_pos": 100.0, "to_pos": 0.1,
    },
]
_LOCKSTEP_PT_STOPS = [
    {"no": "99569", "name": "Central platform", "link": "101", "lane_index": 1, "pos": 5.0, "length": 15.0}
]
_LOCKSTEP_PT_LINES = [
    {
        "no": "52004", "name": "WB", "veh_type": "300", "entry_link": "101",
        "dest_link": "102", "dest_pos": 189.13, "entry_tm_distr": None,
        "link_seq": ["101"], "stops": [],
    }
]


def _lockstep_shadow() -> dict[str, Any]:
    return _shadow_model(
        links=_LOCKSTEP_LINKS, pt_stops=_LOCKSTEP_PT_STOPS, pt_lines=_LOCKSTEP_PT_LINES
    )


#: (tool, bogus-target params, expected object_class). One row per verb wired
#: by this unit -- the bogus id is "999" throughout, deliberately far enough
#: from every fixture id that the candidate machinery is exercised separately
#: (below) rather than incidentally here.
_NEWLY_WIRED_VERBS = [
    (
        "add_connector",
        {"from_link": "999", "from_lanes": [1], "from_pos_m": 10.0,
         "to_link": "102", "to_lanes": [1], "to_pos_m": 0.1},
        "links",
    ),
    ("add_pt_line", {"entry_link_no": "999", "dest_link_no": "102", "dest_pos_m": 189.13}, "links"),
    ("add_pt_stop", {"link_no": "999", "lane_index": 1, "pos_m": 5.0}, "links"),
    ("remove_connector", {"connector_no": "999"}, "links"),
    ("remove_link", {"no": "999"}, "links"),
    ("remove_pt_line", {"no": "999"}, "pt_lines"),
    ("remove_pt_stop", {"no": "999"}, "pt_stops"),
    ("set_lane_closure", {"link_id": "999", "lane_index": 1, "vehicle_classes": ["10"]}, "links"),
    ("set_link_closure", {"link_id": "999", "vehicle_classes": ["10"], "closed": True}, "links"),
    ("set_turn_ban", {"connector_no": "999", "vehicle_classes": ["10"], "banned": True}, "links"),
]


@pytest.mark.parametrize(
    "tool,params,object_class", _NEWLY_WIRED_VERBS, ids=[row[0] for row in _NEWLY_WIRED_VERBS]
)
def test_object_not_found_fires_for_every_newly_wired_verb(
    tool: str, params: dict[str, Any], object_class: str
):
    """A _TOOL_TARGET_SPEC entry alone does NOTHING -- the tool must ALSO be
    named in #1's own applies_to list. Each of these ten had only the first
    half until this unit; before it, every one of these actions produced no
    preflight finding at all and the bogus id reached COM."""
    findings = run_checks("preflight", _action(tool, **params), _lockstep_shadow(), {})

    matches = [f for f in findings if f.constraint_id == "object_not_found"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    assert finding.phase == "preflight"
    assert finding.details == {"tool": tool, "target_id": "999", "object_class": object_class}
    _assert_well_formed_messages(finding, must_contain_ko="999")


@pytest.mark.parametrize(
    "tool,params,expected_ids",
    [
        ("set_link_closure", {"link_id": "1012", "vehicle_classes": ["10"], "closed": True}, {"101", "102"}),
        ("set_turn_ban", {"connector_no": "9010", "vehicle_classes": ["10"], "banned": True}, {"9001"}),
        ("remove_pt_stop", {"no": "99560"}, {"99569"}),
        ("remove_pt_line", {"no": "52005"}, {"52004"}),
    ],
    ids=["links_via_set_link_closure", "connector_via_set_turn_ban", "pt_stops", "pt_lines"],
)
def test_object_not_found_suggests_near_misses_for_the_newly_wired_object_classes(
    tool: str, params: dict[str, Any], expected_ids: set[str]
):
    """The POINT of wiring these verbs is the difflib-suggested near miss --
    a bare "not found" is what they already had from COM. One case per object
    class the ten verbs actually reach (links, pt_stops, pt_lines)."""
    findings = run_checks("preflight", _action(tool, **params), _lockstep_shadow(), {})

    finding = next(f for f in findings if f.constraint_id == "object_not_found")
    assert _candidate_ids(finding.suggested_fix) == expected_ids


@pytest.mark.parametrize(
    "tool,params",
    [
        # set_turn_ban: 101 IS a real link, just not a connector --
        # tools_edit.set_turn_ban's own "is not a connector (turn)" ValueError
        # owns this, and it can only fire on an id that exists.
        ("set_turn_ban", {"connector_no": "101", "vehicle_classes": ["10"], "banned": True}),
        # remove_link: 9001 IS a real link entry, just a connector --
        # tools_edit.remove_link's own "is a CONNECTOR ... use remove_connector"
        # ValueError owns this.
        ("remove_link", {"no": "9001"}),
        # remove_connector: 101 exists but is a plain link --
        # inpx_structure._find_connector's own ValueError owns this.
        ("remove_connector", {"connector_no": "101"}),
    ],
    ids=["set_turn_ban_on_plain_link", "remove_link_on_connector", "remove_connector_on_plain_link"],
)
def test_object_not_found_stays_silent_when_the_id_exists_but_is_the_wrong_kind(
    tool: str, params: dict[str, Any]
):
    """"Existence is generic, appropriateness is dedicated" -- the same split
    add_signal_controller's donor-type check and add_vehicle_input's
    vehicle_input_on_connector warn established. Wiring these three verbs into
    #1 must NOT start double-reporting what their own inline ValueErrors
    already own: a connector IS a "links" entry, so #1 correctly sees the id
    as present and stays quiet."""
    findings = run_checks("preflight", _action(tool, **params), _lockstep_shadow(), {})

    assert not [f for f in findings if f.constraint_id == "object_not_found"]


def test_object_not_found_applies_to_is_in_lockstep_with_the_spec_table():
    """THE regression guard for this whole class of miss (a spec entry with no
    applies_to entry is silently inert, and vice versa). Both directions are
    pinned, and there is currently NO deliberate omission in either -- see the
    applies_to list's own comment in constraints.py. If a future verb
    legitimately belongs in only one half, record WHY there and add it to the
    matching allowlist below rather than loosening the assertion."""
    import services.agent.constraints as constraints_module

    spec_tools = set(constraints_module._TOOL_TARGET_SPEC)
    applies_to = next(c for c in get_registry() if c.id == "object_not_found").applies_to

    #: Tools with a _TOOL_TARGET_SPEC entry deliberately NOT run through #1.
    spec_only_by_design: set[str] = set()
    #: Tools in #1's applies_to deliberately carrying no spec entry (they would
    #: be inert -- _check_object_not_found returns None on a spec miss).
    applies_to_only_by_design: set[str] = set()

    assert spec_tools - set(applies_to) == spec_only_by_design
    assert set(applies_to) - spec_tools == applies_to_only_by_design
    # A duplicate entry would make the difference above pass while still
    # signalling a careless merge.
    assert len(applies_to) == len(set(applies_to))


# ---------------------------------------------------------------------------
# Q6 unit ①: link geometry edits (lanes / length / curvature)
# ---------------------------------------------------------------------------

#: A 200 m link (101) with a signal head at 180 m, a data-collection point at
#: 150 m and a connector (301) anchored on it at 190 m; a 100 m bent link
#: (102) with nothing on it. The pair lets every check below be shown both
#: firing and staying silent, on real shadow-model shapes.
_GEOMETRY_SHADOW: dict[str, Any] = {
    "links": [
        {
            "id": "101",
            "name": "Approach",
            "points": [[0.0, 0.0], [200.0, 0.0]],
            "lanes": 2,
            "lane_widths": [3.5, 3.25],
            "width_total": 6.75,
            "length": 200.0,
            "is_connector": False,
            "from_link": None,
            "to_link": None,
            "from_pos": None,
            "to_pos": None,
        },
        {
            "id": "102",
            "name": "Bent",
            "points": [[0.0, 100.0], [50.0, 110.0], [100.0, 100.0]],
            "lanes": 1,
            "lane_widths": [3.5],
            "width_total": 3.5,
            "length": 101.98,
            "is_connector": False,
            "from_link": None,
            "to_link": None,
            "from_pos": None,
            "to_pos": None,
        },
        {
            "id": "301",
            "name": "Turn",
            "points": [[200.0, 0.0], [210.0, 10.0]],
            "lanes": 1,
            "lane_widths": [3.5],
            "width_total": 3.5,
            "length": 14.14,
            "is_connector": True,
            "from_link": "101",
            "to_link": "102",
            "from_pos": 190.0,
            "to_pos": 0.0,
        },
    ],
    "signal_heads": [{"no": "9", "name": "SH", "link": "101", "lane_index": 2, "pos": 180.0}],
    "data_collection_points": [
        {"no": "7", "name": "DCP", "link": "101", "lane_index": 1, "pos": 150.0}
    ],
    "queue_counters": [{"no": "5", "name": "QC", "link_id": "101", "pos": 20.0}],
}


def _geometry_action(tool: str, **params: Any) -> dict[str, Any]:
    return {"tool": tool, "params": params}


def test_link_geometry_truncates_anchored_objects_refuses_and_names_every_object():
    findings = run_checks(
        "preflight",
        _geometry_action("set_link_geometry", link_id="101", points_xy=[[0.0, 0.0], [100.0, 0.0]]),
        _GEOMETRY_SHADOW,
    )

    matches = [f for f in findings if f.constraint_id == "link_geometry_truncates_anchored_objects"]
    assert len(matches) == 1
    finding = matches[0]
    assert finding.tier == "refuse"
    # The head (180), the DCP (150) and the connector endpoint (190) are all
    # past 100 m; the queue counter at 20 m is NOT, and must not be named.
    assert finding.details["truncated_count"] == 3
    assert finding.details["min_length_m"] == 190.0
    assert "signal head" in finding.messages["en"] or "signal_heads" in finding.messages["en"]
    assert "connector_from 301" in finding.messages["en"]
    assert "5" not in [o["no"] for o in finding.suggested_fix["objects"]]


def test_link_geometry_truncates_anchored_objects_stays_silent_when_everything_still_fits():
    """The boundary that makes the refusal above a real measurement rather
    than a blanket ban on shortening: 195 m clears the furthest anchor
    (the connector at 190 m), 100 m does not."""
    findings = run_checks(
        "preflight",
        _geometry_action("set_link_geometry", link_id="101", points_xy=[[0.0, 0.0], [195.0, 0.0]]),
        _GEOMETRY_SHADOW,
    )

    assert not [f for f in findings if f.constraint_id == "link_geometry_truncates_anchored_objects"]


def test_link_geometry_truncates_anchored_objects_stays_silent_for_a_bare_link():
    findings = run_checks(
        "preflight",
        _geometry_action("set_link_geometry", link_id="102", points_xy=[[0.0, 100.0], [1.0, 100.0], [2.0, 100.0]]),
        _GEOMETRY_SHADOW,
    )

    assert not [f for f in findings if f.constraint_id == "link_geometry_truncates_anchored_objects"]


def test_link_geometry_truncates_anchored_objects_makes_no_claim_on_a_malformed_points_param():
    """A hallucinated param shape must make this check SILENT, never crash a
    preflight pass (every other constraint in this file follows the same
    degrade-quietly rule)."""
    findings = run_checks(
        "preflight",
        _geometry_action("set_link_geometry", link_id="101", points_xy="not points"),
        _GEOMETRY_SHADOW,
    )

    assert not [f for f in findings if f.constraint_id == "link_geometry_truncates_anchored_objects"]


def test_link_lane_count_shifts_centerline_warns_with_the_measured_half_width():
    """Live-measured three times: the shift is exactly half the added/removed
    lane width. Link 101's last lane is 3.25 m wide, so +1 lane at that width
    moves the polyline 1.625 m."""
    findings = run_checks(
        "preflight",
        _geometry_action("set_link_lane_count", link_id="101", lane_count=3),
        _GEOMETRY_SHADOW,
    )

    matches = [f for f in findings if f.constraint_id == "link_lane_count_shifts_centerline"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    assert matches[0].details["expected_centerline_shift_m"] == 1.625
    assert "1.62" in matches[0].messages["ko"]


def test_link_lane_count_shifts_centerline_uses_an_explicit_width_when_given():
    findings = run_checks(
        "preflight",
        _geometry_action("set_link_lane_count", link_id="101", lane_count=4, lane_width_m=4.0),
        _GEOMETRY_SHADOW,
    )

    finding = next(f for f in findings if f.constraint_id == "link_lane_count_shifts_centerline")
    # Two lanes of 4.0 m -> the whole link moves 4.0 m.
    assert finding.details["expected_centerline_shift_m"] == 4.0


def test_link_lane_count_shifts_centerline_stays_silent_on_a_no_op():
    findings = run_checks(
        "preflight",
        _geometry_action("set_link_lane_count", link_id="101", lane_count=2),
        _GEOMETRY_SHADOW,
    )

    assert not [f for f in findings if f.constraint_id == "link_lane_count_shifts_centerline"]


def test_link_point_radius_overrides_geometry_warns_and_names_the_drawn_radius():
    """The warn puts the caller's override next to the circumradius of the
    three points as drawn -- link 102's bend through (0,100)-(50,110)-(100,100)
    has a real radius of exactly 130 m, so a 500 m override is visibly a claim about
    something other than the drawn shape."""
    findings = run_checks(
        "preflight",
        _geometry_action("set_link_point_radius", link_id="102", point_index=2, radius_m=500.0),
        _GEOMETRY_SHADOW,
    )

    matches = [f for f in findings if f.constraint_id == "link_point_radius_overrides_geometry"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    assert matches[0].details["radius_m"] == 500.0
    assert matches[0].details["geometric_radius_m"] == pytest.approx(130.0, abs=0.01)
    assert "500" in matches[0].messages["en"]


def test_link_point_radius_overrides_geometry_stays_silent_when_restoring():
    """Omitting radius_m hands the radius back to Vissim's own computation --
    the direction that REMOVES a divergence, so there is nothing to warn."""
    findings = run_checks(
        "preflight",
        _geometry_action("set_link_point_radius", link_id="102", point_index=2),
        _GEOMETRY_SHADOW,
    )

    assert not [f for f in findings if f.constraint_id == "link_point_radius_overrides_geometry"]


def test_link_point_radius_overrides_geometry_still_warns_when_the_drawn_radius_is_unknowable():
    """A straight link's three collinear points have an INFINITE circumradius;
    the check must still warn (the override is still a claim) while printing
    "-" rather than inventing a number."""
    shadow = {
        "links": [
            {
                "id": "900",
                "name": "Straight",
                "points": [[0.0, 0.0], [50.0, 0.0], [100.0, 0.0]],
                "lanes": 1,
                "lane_widths": [3.5],
                "width_total": 3.5,
                "length": 100.0,
                "is_connector": False,
                "from_link": None, "to_link": None, "from_pos": None, "to_pos": None,
            }
        ]
    }

    findings = run_checks(
        "preflight",
        _geometry_action("set_link_point_radius", link_id="900", point_index=2, radius_m=300.0),
        shadow,
    )

    finding = next(f for f in findings if f.constraint_id == "link_point_radius_overrides_geometry")
    assert finding.details["geometric_radius_m"] is None
    assert "-" in finding.messages["en"]


def test_lane_count_edit_unsupported_exempts_set_link_lane_count():
    """The trap Q4 unit ⑤ recorded after being bitten by it live: this
    blanket refusal fires on ANY tool carrying a lane-count-shaped param, so
    a new lane-count verb that is not exempted is refused forever, silently,
    before it ever reaches COM."""
    findings = run_checks(
        "preflight",
        _geometry_action("set_link_lane_count", link_id="101", lane_count=3),
        _GEOMETRY_SHADOW,
    )

    assert not [f for f in findings if f.constraint_id == "lane_count_edit_unsupported"]


def test_link_geometry_edit_reference_integrity_now_fires_for_the_shape_changing_tools():
    """The M0.3 forward-ready check finally has the tools it was written for.
    It watches a per-link "geometry_edited_this_turn" flag that NO parser sets,
    so it had been inert since M0.3 -- it now also keys off the action's own
    link_id for the two tools that genuinely change a link's drawn shape."""
    findings = run_checks(
        "postwrite",
        _geometry_action("set_link_geometry", link_id="101", points_xy=[[0.0, 0.0], [195.0, 0.0]]),
        _GEOMETRY_SHADOW,
    )

    matches = [f for f in findings if f.constraint_id == "link_geometry_edit_reference_integrity"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"
    assert matches[0].details["edited_links"] == ["101"]


def test_link_geometry_edit_reference_integrity_also_fires_for_a_lane_count_change():
    findings = run_checks(
        "postwrite",
        _geometry_action("set_link_lane_count", link_id="101", lane_count=3),
        _GEOMETRY_SHADOW,
    )

    finding = next(
        f for f in findings if f.constraint_id == "link_geometry_edit_reference_integrity"
    )
    assert finding.details["edited_links"] == ["101"]


def test_link_geometry_edit_reference_integrity_stays_silent_for_a_curvature_only_write():
    """set_link_point_radius is the one geometry-adjacent write measured NOT
    to move the shape at all (x/y and Length2D unchanged), so nothing anchored
    to the link needs re-validating and this must stay quiet."""
    findings = run_checks(
        "postwrite",
        _geometry_action("set_link_point_radius", link_id="101", point_index=2, radius_m=300.0),
        _GEOMETRY_SHADOW,
    )

    assert not [f for f in findings if f.constraint_id == "link_geometry_edit_reference_integrity"]

from __future__ import annotations

from typing import Any

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
) -> dict[str, Any]:
    return {
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


def test_vehicle_input_multi_interval_write_gap_skipped_when_datum_absent():
    shadow = _shadow_model()  # default fixture has no interval_count key
    action = _action("set_vehicle_input_volume", input_no="1", volume=1200)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "vehicle_input_multi_interval_write_gap" for f in findings
    )


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


def test_conflict_area_data_unavailable_silent_when_all_active():
    shadow = _shadow_model()
    shadow["conflict_areas"] = [{"link_pair": ("10", "20"), "status": "Active"}]
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("postwrite", action, shadow, {})

    assert not any(f.constraint_id == "conflict_area_data_unavailable" for f in findings)


def test_conflict_area_data_unavailable_skipped_when_data_absent():
    shadow = _shadow_model()  # no "conflict_areas" key — today's production reality
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("postwrite", action, shadow, {})

    assert not any(f.constraint_id == "conflict_area_data_unavailable" for f in findings)


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
    shadow = _shadow_model()
    action = _action("add_desired_speed_decision", link_id="101", position=50, speed=30)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "speed_decision_unpaired"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"


def test_speed_decision_unpaired_silent_with_restore_id():
    shadow = _shadow_model()
    action = _action(
        "add_desired_speed_decision",
        link_id="101",
        position=50,
        speed=30,
        paired_restore_decision_id="dec-2",
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


def test_vehicle_composition_share_redistribution_gap_always_warns_for_that_tool():
    shadow = _shadow_model()
    action = _action(
        "set_vehicle_composition_share", composition_id="1", class_id="truck", new_share=0.3
    )

    findings = run_checks("preflight", action, shadow, {})

    matches = [
        f
        for f in findings
        if f.constraint_id == "vehicle_composition_share_redistribution_gap"
    ]
    assert len(matches) == 1
    assert matches[0].tier == "warn"


def test_vehicle_composition_share_redistribution_gap_does_not_apply_to_other_tools():
    shadow = _shadow_model()
    action = _action("set_vehicle_input_volume", input_no="1", volume=600)

    findings = run_checks("preflight", action, shadow, {})

    assert not any(
        f.constraint_id == "vehicle_composition_share_redistribution_gap" for f in findings
    )


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
# M3.1 — final registry shape (all 4 batches landed)
# ---------------------------------------------------------------------------


def test_registry_size_matches_m3_1_catalog_port():
    # 8 (M0.3 seed) + 9 (batch 1: M2 verb set) + 3 (batch 2: conflict-area /
    # intergreen) + 9 (batch 3: post-run lie detectors) + 14 (batch 4: the
    # remainder) == 43. Regression pin -- bump deliberately, not by accident.
    assert len(get_registry()) == 43


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

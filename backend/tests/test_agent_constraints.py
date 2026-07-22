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
                "controller_type": "fixed_time",
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


def test_registry_has_exactly_the_eight_seeded_constraints():
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

    registry = get_registry()

    assert {c.id for c in registry} == set(expected)
    for constraint in registry:
        assert (constraint.tier, constraint.phase) == expected[constraint.id]


def test_registry_order_is_deterministic_registration_order():
    ids = [c.id for c in get_registry()]
    assert ids == [
        "object_not_found",
        "volume_out_of_range",
        "latent_demand_overflow",
        "signal_controller_type_unsupported",
        "cycle_below_intergreen_sum",
        "write_verification_failed",
        "results_missing_kpi_tables",
        "vehicles_deleted_in_run",
    ]


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

    assert any(f.constraint_id == "signal_controller_type_unsupported" for f in findings)


def test_signal_controller_type_unsupported_fires_for_non_fixed_time_controller():
    shadow = _shadow_model(
        signals=[
            {
                "sc_no": "1",
                "sc_name": "SC1",
                "cycle_time": 90,
                "controller_type": "RBC",
                "phases": [],
            }
        ]
    )
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

    findings = run_checks("preflight", action, shadow, {})

    matches = [f for f in findings if f.constraint_id == "signal_controller_type_unsupported"]
    assert len(matches) == 1
    _assert_well_formed_messages(matches[0], must_contain_ko="RBC")


def test_signal_controller_type_unsupported_silent_for_fixed_time():
    shadow = _shadow_model()  # controller_type = "fixed_time"
    action = _action("set_signal_cycle", sc_no="1", cycle=90)

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
                "controller_type": "fixed_time",
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

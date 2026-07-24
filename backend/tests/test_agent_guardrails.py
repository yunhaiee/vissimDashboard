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
    }


def _action(tool: str, **params: Any) -> dict[str, Any]:
    return {"tool": tool, "params": params}


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
    assert "101" in finding.suggested_fix


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

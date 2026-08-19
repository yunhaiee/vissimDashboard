"""Unit #44 — measured saturation flow → MRWA Table 5-6 → car-following params.

What these tests are for
------------------------
1. **The table is the guideline's, exactly.** All six published rows, both
   boundaries, and — the part that is OUR policy rather than MRWA's — what
   happens between rows and outside the range. The between-rows choice is
   pinned in the direction it was argued for (DOWN, so capacity is never
   overstated), because a silent flip of that policy would change what every
   proposal claims while every other test stayed green.

2. **The refusals are reachable and distinct.** Nine reason codes, each with a
   test that gets to it. A refusal nobody can reach is a comment.

3. **The derivation stays refused for the reasons measured, not asserted.**
   ``derive_measurement`` must name BOTH blockers when green time is missing,
   and must still name the PCU one when green time is fully available — that
   second case is the one that proves the two blockers are independent, and it
   is the case no artifact in this checkout can produce, so it is built here.

4. **The doctrine holds at the edges.** No proposal ever touches a measured
   entrance input (D4), and no proposal can carry a W99 ``cc*`` parameter.

Deliberately NOT tested against ``client-data/``: those artifacts live on the
dev machine only, and commit c066efe already established that a dev-machine
file must not be able to vote in this suite. The real-model measurements that
justify this module's design are recorded in its own docstring, and the
availability reader is exercised here against synthetic payloads built in the
SHAPE that measurement found (stage-based programs carrying
``STAGE_BASED_PROGRAM``, and a signal-group program carrying real splits).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.agent import car_following_grant, constraints, fixes  # noqa: E402
from services.agent import geh_ladder, guardrails, mrwa_reference, tools_network  # noqa: E402
from services.agent import saturation_flow as sf  # noqa: E402
from services.agent.gates import PreapprovedWriteGate, signature  # noqa: E402
from test_agent_geh_ladder import _diagnosis, _report  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _capacity_rung() -> dict[str, Any]:
    """A REAL ``CAPACITY_LIMIT`` diagnosis, from the real ladder.

    Built through ``reality_check.check_run_against_counts`` (unit #43's own
    test helpers) rather than hand-written, so these tests cannot pass against
    a rung shape the ladder has stopped producing.
    """
    diagnosis = _diagnosis(_report())
    assert diagnosis["rung"] == geh_ladder.CAPACITY_LIMIT, "fixture no longer reaches the rung"
    return diagnosis


def _measurement(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "saturation_flow_pcu": 1950,
        "method": "stop-line discharge headway, on-site (Appendix B §B.3)",
        "measured_at": "2026-08-14 07:30-08:30",
        "attribution": "Dochak survey team",
        "samples": 8,
        "vehicles_per_sample": 11,
        "intersection": "Murdoch Dr / South St",
        "approach_link": "501",
    }
    payload.update(overrides)
    return sf.observed_measurement(**payload)


def _behavior(reference_count: int = 0) -> dict[str, Any]:
    return {"no": "2", "name": "Urban (motorized)", "reference_count": reference_count}


def _behavior_with_refs(reference_count: int = 0) -> dict[str, Any]:
    """``_behavior`` plus the reference graph, i.e. the same entry :func:`_shadow`
    publishes. Unit #44-b's grant fingerprints this into the proposal's
    ``consent_radius``, so the approved-path half of the forecast matrix needs a target
    whose named radius actually equals the one in the shadow model it is checked
    against."""
    return {**_behavior(reference_count), **{
        "referenced_by": _shadow(reference_count)["driving_behaviors"][0]["referenced_by"]
    }}


def _truncated_behavior(reference_count: int = 13) -> dict[str, Any]:
    """``_behavior_with_refs`` past ``tools_network``'s 10-name consumer cap, capped by
    that module's own helper so the truncation is real rather than hand-set."""
    behavior = _behavior_with_refs(reference_count)
    behavior["referenced_by"] = tools_network._bounded_driving_behavior_consumers(
        list(behavior["referenced_by"]["consumers"])
    )
    return behavior


def _shadow(reference_count: int = 0) -> dict[str, Any]:
    return {
        "driving_behaviors": [
            {
                "no": "2",
                "name": "Urban (motorized)",
                "car_following_model": 74,
                "reference_count": reference_count,
                "referenced_by": {
                    "consumers": [
                        {"link_behavior_type_no": "1", "link_behavior_type_name": "Urban"}
                    ]
                    * reference_count
                },
            }
        ]
    }


def _propose(**overrides: Any) -> tuple[fixes.FixProposal, ...]:
    kwargs: dict[str, Any] = {
        "rung": _capacity_rung(),
        "measurement": _measurement(),
        "behavior": _behavior(),
    }
    kwargs.update(overrides)
    return sf.propose_car_following_parameters(**kwargs)


# ---------------------------------------------------------------------------
# 1. Table 5-6 — every published row, both boundaries
# ---------------------------------------------------------------------------


def test_table_5_6_carries_the_six_published_rows_in_ascending_order():
    rows = mrwa_reference.TABLE_5_6_ROWS
    assert rows == (
        (1630.0, 2.75, 3.75),
        (1780.0, 2.50, 3.50),
        (1950.0, 2.25, 3.25),
        (2050.0, 2.00, 3.00),
        (2100.0, 1.75, 2.75),
        (2200.0, 1.50, 2.50),
    )
    assert [row[0] for row in rows] == sorted(row[0] for row in rows)
    # The relationship the between-rows policy's whole argument rests on: as
    # saturation flow rises, BOTH safety-distance parts fall. If a future
    # transcription broke that monotonicity, "round down = conservative" would
    # stop being true and the policy would need re-arguing, not re-running.
    for earlier, later in zip(rows, rows[1:]):
        assert later[1] < earlier[1], "additive part must fall as saturation flow rises"
        assert later[2] < earlier[2], "multiplicative part must fall as saturation flow rises"


@pytest.mark.parametrize("saturation_flow,additive,multiplicative", mrwa_reference.TABLE_5_6_ROWS)
def test_every_published_row_looks_up_exactly(saturation_flow, additive, multiplicative):
    result = sf.lookup_table_5_6(saturation_flow)

    assert result["status"] == sf.EXACT
    assert result["row"] == {
        "saturation_flow_pcu": saturation_flow,
        "additive": additive,
        "multiplicative": multiplicative,
    }
    # An exact hit made no between-rows choice, so it reports no bracket.
    assert result["bracket_lower"] is None
    assert result["bracket_upper"] is None


def test_both_table_boundaries_are_inclusive():
    low, high = mrwa_reference.table_5_6_saturation_flow_range()

    assert sf.lookup_table_5_6(low)["status"] == sf.EXACT
    assert sf.lookup_table_5_6(high)["status"] == sf.EXACT
    # One pcu outside either boundary is no longer covered by the table.
    assert sf.lookup_table_5_6(low - 1)["status"] == sf.OUT_OF_RANGE_LOW
    assert sf.lookup_table_5_6(high + 1)["status"] != sf.EXACT


def test_the_table_and_the_structured_rows_cannot_drift_apart():
    """``TABLE_5_6_SAFETY_DISTANCE_VS_SATURATION_FLOW`` is BUILT from
    ``TABLE_5_6_ROWS``; this pins that it still is, so the self-citing form the
    integrity test reads and the structured form the lookup reads stay the same
    six rows."""
    entries = mrwa_reference.TABLE_5_6_SAFETY_DISTANCE_VS_SATURATION_FLOW.entries
    assert len(entries) == 2 * len(mrwa_reference.TABLE_5_6_ROWS)

    values = [entry.value for entry in entries]
    expected: list[float] = []
    for _saturation_flow, additive, multiplicative in mrwa_reference.TABLE_5_6_ROWS:
        expected.extend((additive, multiplicative))
    assert values == expected
    assert mrwa_reference.TABLE_5_6_SAFETY_DISTANCE_VS_SATURATION_FLOW.complete is True


def test_table_5_6_entries_are_not_encoded_as_ranges():
    """The pair is (additive, multiplicative) — two different parameters, not
    the ends of one band. The pre-#44 encoding said RANGE, which a generic
    reader would have read as low..high."""
    for entry in mrwa_reference.TABLE_5_6_SAFETY_DISTANCE_VS_SATURATION_FLOW.entries:
        assert entry.kind == "DEFAULT"
        assert not isinstance(entry.value, tuple)
        # No unit is invented for a column the source prints unitless.
        assert entry.unit == ""
        assert "no unit is invented" in entry.note


# ---------------------------------------------------------------------------
# 2. Between rows and outside the range — OUR policy, pinned
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "measured,expected_row_pcu,expected_upper_pcu",
    [
        (1700, 1630.0, 1780.0),
        (1900, 1780.0, 1950.0),
        (1949, 1780.0, 1950.0),
        (1951, 1950.0, 2050.0),
        (2199, 2100.0, 2200.0),
    ],
)
def test_a_measurement_between_rows_rounds_down_and_reports_both_neighbours(
    measured, expected_row_pcu, expected_upper_pcu
):
    result = sf.lookup_table_5_6(measured)

    assert result["status"] == sf.BRACKETED
    assert result["policy"] == sf.BETWEEN_ROWS_POLICY == "ROUND_DOWN_TO_PUBLISHED_ROW"
    assert result["row"]["saturation_flow_pcu"] == expected_row_pcu
    # Both neighbours are published so the choice is auditable and reversible.
    assert result["bracket_lower"]["saturation_flow_pcu"] == expected_row_pcu
    assert result["bracket_upper"]["saturation_flow_pcu"] == expected_upper_pcu
    # And the row applied is genuinely the more conservative of the two.
    assert result["bracket_lower"]["additive"] > result["bracket_upper"]["additive"]


def test_no_interpolated_pair_is_ever_produced():
    """Whatever comes back must be a row that appears in the source table."""
    published = {
        (row[1], row[2]) for row in mrwa_reference.TABLE_5_6_ROWS
    }
    for measured in range(1630, 2201, 7):
        row = sf.lookup_table_5_6(measured)["row"]
        assert row is not None
        assert (row["additive"], row["multiplicative"]) in published


def test_below_the_table_but_plausible_is_its_own_named_status():
    """800..1630 is a real measurement the table simply does not cover — a
    different situation from an implausible number, and it gets a different
    answer. (Appendix B's floor is 800; the table's is 1630.)"""
    for measured in (800, 1200, 1629):
        result = sf.lookup_table_5_6(measured)
        assert result["status"] == sf.OUT_OF_RANGE_LOW
        assert result["row"] is None, "the table is never extrapolated downward"
        assert result["bracket_upper"]["saturation_flow_pcu"] == 1630.0


def test_above_the_table_coincides_with_appendix_b_implausibility():
    """Table 5-6's top row (2200) and the plausibility screen's ceiling (2200)
    are the same number, so anything above the table is also caught by the
    screen — and is reported as the data-quality finding, which is the more
    useful of the two.

    CORRECTED 2026-08-18: this docstring used to call those "two independently
    sourced MRWA documents". They are not. Table 5-6's 2200 is Main Roads'
    (p.187); the screen's 2200 is Prism's own drafted candidate (see
    ``test_the_plausibility_band_is_never_attributed_to_main_roads``) and most
    likely came FROM this table. The assertions below are unchanged — only the
    story told about them was wrong."""
    _, table_high = mrwa_reference.table_5_6_saturation_flow_range()
    band_low, band_high = sf.lookup_table_5_6(2000)["plausibility_band_pcu"]

    assert table_high == band_high == 2200.0
    assert band_low == 800.0
    assert sf.lookup_table_5_6(2201)["status"] == sf.IMPLAUSIBLE


@pytest.mark.parametrize("bad", [0, -5, 799, 2201, 100000, None, "not a number", float("nan")])
def test_implausible_and_unparseable_values_never_select_a_row(bad):
    result = sf.lookup_table_5_6(bad)

    assert result["status"] == sf.IMPLAUSIBLE
    assert result["row"] is None


# ---------------------------------------------------------------------------
# 3. Green-time availability, and why the derivation stays refused
# ---------------------------------------------------------------------------


def _stage_based_controller(programs: int = 2) -> dict[str, Any]:
    """The shape EVERY real controller in this checkout actually has."""
    return {
        "sc_no": "6",
        "controller_type": "FIXEDTIME",
        "programs": [
            {
                "prog_no": str(10 + n),
                "program_kind": "stage_based",
                "cycle_s": 131.0,
                "sg_splits": [],
                "sg_splits_unavailable_reason": "STAGE_BASED_PROGRAM",
                "data_quality": [],
            }
            for n in range(programs)
        ],
    }


def _signal_group_controller(flags: list[str] | None = None) -> dict[str, Any]:
    return {
        "sc_no": "9",
        "controller_type": "FIXEDTIME",
        "programs": [
            {
                "prog_no": "12",
                "program_kind": "signal_group",
                "cycle_s": 80.0,
                "sg_splits": [
                    {"sg_no": "1", "green_start_s": 0.0, "green_end_s": 55.0, "amber_s": 4.0}
                ],
                "data_quality": list(flags or []),
            }
        ],
    }


def test_stage_based_programs_report_no_green_time_and_quote_the_parsers_reason():
    availability = sf.green_time_availability({"signal_programs": [_stage_based_controller(8)]})

    assert availability["state"] == sf.GREEN_TIME_NONE
    assert availability["programs"] == 8
    assert availability["programs_with_sg_splits"] == 0
    # The parser's own machine-readable code, counted rather than restated.
    assert availability["unavailable_reasons"] == {"STAGE_BASED_PROGRAM": 8}


def test_a_model_with_no_signal_controllers_at_all_reads_as_none():
    """The genesis-built networks' actual state — no controllers, no heads."""
    for payload in ({"signal_programs": []}, {}, None):
        availability = sf.green_time_availability(payload)
        assert availability["state"] == sf.GREEN_TIME_NONE
        assert availability["programs"] == 0


def test_mixed_controllers_read_as_partial_and_carry_the_quality_flags():
    availability = sf.green_time_availability(
        {
            "signal_programs": [
                _stage_based_controller(2),
                _signal_group_controller(["SINGLE_SIGNAL_GROUP_PROGRAM", "NEAR_PERMANENT_GREEN"]),
            ]
        }
    )

    assert availability["state"] == sf.GREEN_TIME_PARTIAL
    assert availability["programs"] == 3
    assert availability["programs_with_sg_splits"] == 1
    assert availability["sg_split_rows"] == 1
    assert availability["unavailable_reasons"] == {"STAGE_BASED_PROGRAM": 2}
    # The flags that disowned the one real-model program that DID parse.
    assert availability["data_quality_flags"]["SINGLE_SIGNAL_GROUP_PROGRAM"] == 1
    assert availability["data_quality_flags"]["NEAR_PERMANENT_GREEN"] == 1


def test_derivation_names_both_blockers_when_green_time_is_missing():
    result = sf.derive_measurement(
        shadow_model={"signal_programs": [_stage_based_controller()]},
        run_result={"movement_volumes": {}},
    )

    assert result["ok"] is False
    assert result["reason_code"] == sf.SATURATION_FLOW_NOT_DERIVABLE
    assert set(result["blockers"]) == set(sf.DERIVATION_BLOCKERS)
    assert result["saturation_flow_pcu"] is None
    assert sf.derivation_is_still_blocked(result)


def test_the_pcu_blocker_stands_even_when_green_time_is_fully_available():
    """THE independence test. No artifact in this checkout can produce a fully
    parsed set of green splits, so the case is constructed — and it is the case
    that proves the two blockers are not one blocker wearing two names. A unit
    that fixes only the signal side does not unlock this derivation."""
    result = sf.derive_measurement(
        shadow_model={"signal_programs": [_signal_group_controller()]},
        run_result={"movement_volumes": {}},
    )

    assert result["green_time"]["state"] == sf.GREEN_TIME_FULL
    assert result["ok"] is False
    assert result["blockers"] == [sf.PCU_DECOMPOSITION_UNAVAILABLE]
    assert sf.GREEN_TIME_UNAVAILABLE not in result["blockers"]
    # And it says WHY in a sentence a reader can check: VEHS is vehicles, the
    # table is pcu, and the class rows never arrive.
    assert "ARG_VEHICLECLASS" in result["pcu"]["why"]


def test_a_derived_measurement_refuses_the_proposal_and_reports_the_evidence():
    actions = _propose(
        measurement=sf.derive_measurement(
            shadow_model={"signal_programs": [_stage_based_controller()]}
        )
    )

    assert len(actions) == 1
    assert actions[0].outcome == fixes.REFUSAL
    assert actions[0].reason_code == sf.SATURATION_FLOW_NOT_DERIVABLE
    assert actions[0].evidence["derivation"]["blockers"]
    for language in ("ko", "en", "ja"):
        assert actions[0].rationale()[language]


# ---------------------------------------------------------------------------
# 4. The measurement record — ask-first, and MRWA's own sampling gate
# ---------------------------------------------------------------------------


def test_a_well_attributed_measurement_is_accepted():
    record = _measurement()

    assert record["ok"] is True
    assert record["provenance"] == sf.OBSERVED
    assert record["reason_code"] is None
    assert record["unit"] == "pcu/h of green"


def test_a_number_with_no_method_or_attribution_is_not_a_measurement():
    for missing in ("method", "attribution"):
        record = _measurement(**{missing: None})
        assert record["ok"] is False
        assert record["reason_code"] == sf.MEASUREMENT_PROVENANCE_MISSING


def test_sampling_below_appendix_b_minimums_is_refused_not_flagged():
    minimums = mrwa_reference.SATURATION_FLOW_SAMPLE_MINIMUMS
    assert minimums.complete, "the gate reads this table; it must still be complete"

    too_few_samples = _measurement(samples=3)
    assert too_few_samples["ok"] is False
    assert too_few_samples["reason_code"] == sf.SAMPLE_SIZE_BELOW_MRWA_MINIMUM

    too_few_vehicles = _measurement(vehicles_per_sample=4)
    assert too_few_vehicles["ok"] is False
    assert too_few_vehicles["reason_code"] == sf.SAMPLE_SIZE_BELOW_MRWA_MINIMUM

    # Exactly at the published minimums is enough — the gate is not stricter
    # than the authority it cites.
    assert _measurement(samples=6, vehicles_per_sample=5)["ok"] is True


def test_sampling_counts_are_optional_but_never_assumed_sufficient():
    """A measurement that reports no sample count is not blocked on the sample
    gate (nothing to test against) — but the absence is visible in the record
    rather than filled in with a flattering default."""
    record = _measurement(samples=None, vehicles_per_sample=None)

    assert record["ok"] is True
    assert record["samples"] is None
    assert record["vehicles_per_sample"] is None


# ---------------------------------------------------------------------------
# 5. The proposal
# ---------------------------------------------------------------------------


def test_capacity_limit_plus_a_measurement_proposes_the_three_parameters():
    actions = _propose()

    assert len(actions) == 3
    assert [action.params["parameter"] for action in actions] == ["ax", "bx_add", "bx_mult"]
    for action in actions:
        assert action.outcome == fixes.PROPOSAL
        assert action.verb == "set_driving_behavior_parameter"
        assert action.scope == fixes.CAR_FOLLOWING_LEVEL
        assert action.confidence == sf.CONFIDENCE_TABLE_LOOKUP
        assert action.reason_code is None
        assert action.params["behavior_no"] == "2"

    # The 1950 row, exactly — plus §5.2.5.1's flat 2.5 m standstill distance.
    assert [action.params["value"] for action in actions] == [2.5, 2.25, 3.25]


def test_the_rationale_cites_the_table_the_measurement_and_its_source():
    actions = _propose()
    rationale = actions[1].rationale()  # bx_add

    for language in ("ko", "en", "ja"):
        text = rationale[language]
        assert mrwa_reference.TABLE_5_6_CITATION in text, f"{language}: no Table 5-6 citation"
        assert "1950" in text, f"{language}: the measured pcu is not named"
        assert "Dochak survey team" in text, f"{language}: the measurement's source is not named"
        assert mrwa_reference.CAR_FOLLOWING_FROM_SATURATION_FLOW_CITATION in text


def test_the_between_rows_choice_is_stated_in_the_rationale_when_one_was_made():
    bracketed = _propose(measurement=_measurement(saturation_flow_pcu=1900))
    exact = _propose(measurement=_measurement(saturation_flow_pcu=1950))

    assert bracketed[1].params["value"] == 2.50  # the 1780 row, rounded DOWN
    for language in ("ko", "en", "ja"):
        assert "1780" in bracketed[1].rationale()[language]
        assert "1950" in bracketed[1].rationale()[language]
    # An exact hit made no choice, so it does not narrate one.
    assert "1780" not in exact[1].rationale()["en"]


def test_every_proposal_says_the_three_parameters_are_one_set():
    """Partial approval leaves the behaviour matching no published row, so the
    coupling is on every action, not just the first."""
    for action in _propose():
        assert set(action.evidence["coordinated_with"]) == {"ax", "bx_add", "bx_mult"} - {
            action.params["parameter"]
        }
        assert "one set" in action.rationale()["en"] or "one row" in action.rationale()["en"]


def test_the_shared_evidence_carries_the_lookup_and_the_measurement():
    action = _propose()[0]

    assert action.evidence["rung"] == geh_ladder.CAPACITY_LIMIT
    assert action.evidence["lookup"]["status"] == sf.EXACT
    assert action.evidence["measurement"]["provenance"] == sf.OBSERVED
    assert action.evidence["citation"]["table"] == mrwa_reference.TABLE_5_6_CITATION
    assert "§2.12.1 step 7" in action.evidence["citation"]["step"]


def test_the_assumptions_name_what_the_guideline_requires_but_nothing_proves():
    action = _propose()[0]

    assert sf.ASSUME_STRAIGHT_MOVEMENT in action.assumptions
    assert sf.ASSUME_BEHAVIOR_IS_URBAN in action.assumptions
    assert sf.ASSUME_KEY_INTERSECTION_REPRESENTS_NETWORK in action.assumptions
    assert sf.ASSUME_CHANGE_IS_NETWORK_WIDE in action.assumptions


# ---------------------------------------------------------------------------
# 6. Every refusal is reachable
# ---------------------------------------------------------------------------


def test_no_measurement_refuses_by_name_and_does_not_guess_a_row():
    actions = _propose(measurement=None)

    assert len(actions) == 1
    assert actions[0].outcome == fixes.REFUSAL
    assert actions[0].reason_code == sf.NO_SATURATION_FLOW_MEASUREMENT
    assert actions[0].verb is None
    assert actions[0].params is None
    assert actions[0].confidence == fixes.CONFIDENCE_NONE
    # It asks for the measurement, citing the step that asks for it.
    assert "Table 5-6" in actions[0].rationale()["en"]


def test_a_non_capacity_diagnosis_refuses_before_anything_else_is_considered():
    """§5.2.5.1's remedy answers a capacity finding. Applied to any other rung
    it would be tuning by feel with a citation stapled on — so this is checked
    FIRST, even when a perfectly good measurement is in hand."""
    for rung_code in (geh_ladder.DEMAND_LEVEL, geh_ladder.ROUTING_SPLIT, geh_ladder.UNCLASSIFIABLE,
                      geh_ladder.SIGNAL_TIMING):
        actions = sf.propose_car_following_parameters(
            rung={"rung": rung_code, "evidence": {"node_id": "5"}},
            measurement=_measurement(),
            behavior=_behavior(),
        )
        assert len(actions) == 1
        assert actions[0].reason_code == sf.RUNG_IS_NOT_CAPACITY_LIMIT, rung_code


def test_a_missing_diagnosis_refuses_rather_than_defaulting_to_capacity():
    for rung in (None, {}, {"rung": ""}):
        actions = sf.propose_car_following_parameters(
            rung=rung, measurement=_measurement(), behavior=_behavior()
        )
        assert actions[0].reason_code == sf.RUNG_IS_NOT_CAPACITY_LIMIT


@pytest.mark.parametrize(
    "measured,expected",
    [
        (1200, sf.SATURATION_FLOW_BELOW_TABLE),
        (1629, sf.SATURATION_FLOW_BELOW_TABLE),
        (2201, sf.SATURATION_FLOW_IMPLAUSIBLE),
        (400, sf.SATURATION_FLOW_IMPLAUSIBLE),
    ],
)
def test_a_measurement_the_table_cannot_cover_refuses_by_its_own_name(measured, expected):
    actions = _propose(measurement=_measurement(saturation_flow_pcu=measured))

    assert len(actions) == 1
    assert actions[0].reason_code == expected
    assert actions[0].verb is None


def test_no_named_behaviour_refuses_after_the_row_is_settled():
    """The lookup already succeeded, so the refusal can say what the answer
    WOULD be — an ask for a target, not a dead end."""
    actions = _propose(behavior=None)

    assert len(actions) == 1
    assert actions[0].reason_code == sf.NO_DRIVING_BEHAVIOR_TARGET
    assert "2.25" in actions[0].rationale()["en"]


def test_every_declared_reason_code_is_reachable():
    """A registry of refusals nobody can reach is a comment. Each code below is
    produced by one of the tests in this file; this pins the registry itself so
    a new code cannot be added without a path to it."""
    reached = {
        sf.NO_SATURATION_FLOW_MEASUREMENT,
        sf.SATURATION_FLOW_NOT_DERIVABLE,
        sf.MEASUREMENT_PROVENANCE_MISSING,
        sf.SAMPLE_SIZE_BELOW_MRWA_MINIMUM,
        sf.SATURATION_FLOW_IMPLAUSIBLE,
        sf.SATURATION_FLOW_BELOW_TABLE,
        sf.SATURATION_FLOW_ABOVE_TABLE,
        sf.RUNG_IS_NOT_CAPACITY_LIMIT,
        sf.NO_DRIVING_BEHAVIOR_TARGET,
    }
    assert reached == set(sf.REASON_CODES)


def test_above_table_is_reachable_if_the_plausibility_band_ever_widens(monkeypatch):
    """``SATURATION_FLOW_ABOVE_TABLE`` is unreachable today only because the
    table's ceiling and the plausibility screen's coincide at 2200 (one MRWA
    number and one of ours, not two of theirs). Pinned as a real path rather
    than deleted, so the answer stays correct if either moves."""
    monkeypatch.setattr(sf, "_plausibility_band", lambda: (800.0, 3000.0))
    result = sf.lookup_table_5_6(2500)

    assert result["status"] == sf.OUT_OF_RANGE_HIGH
    assert result["row"] is None
    assert result["bracket_lower"]["saturation_flow_pcu"] == 2200.0

    actions = _propose(measurement=_measurement(saturation_flow_pcu=2500))
    assert actions[0].reason_code == sf.SATURATION_FLOW_ABOVE_TABLE


# ---------------------------------------------------------------------------
# 7. Scope, and the guardrail collision it creates
# ---------------------------------------------------------------------------


def test_an_unreadable_scope_hint_falls_back_to_the_mrwa_default():
    """Never the other way round: a garbled hint must not silently narrow a
    network calibration to a handful of links."""
    for value in (None, "", "nonsense", "NETWORK_WIDE", 7):
        assert sf.normalize_behavior_scope(value) == sf.SCOPE_NETWORK_WIDE
    for value in ("LOCALIZED", "localized", "local", "per_link"):
        assert sf.normalize_behavior_scope(value) == sf.SCOPE_LOCALIZED


def test_the_shared_object_guardrail_is_forecast_and_the_forecast_is_true():
    """The forecast is only worth carrying if it matches what the constraint
    engine actually does, so both are run and compared."""
    for reference_count in (0, 4):
        actions = _propose(behavior=_behavior(reference_count))
        forecast = actions[0].evidence["preflight_forecast"]
        findings = fixes.preflight_findings(actions[0], shadow_model=_shadow(reference_count))
        refused = [f for f in findings if f.constraint_id == "driving_behavior_shared_object_edit"]

        assert forecast["constraint_id"] == "driving_behavior_shared_object_edit"
        assert forecast["expected_to_refuse"] is bool(refused), reference_count
        assert forecast["reference_count"] == reference_count


def test_an_unshared_behaviour_passes_preflight_clean():
    for action in _propose(behavior=_behavior(0)):
        assert fixes.preflight_findings(action, shadow_model=_shadow(0)) == []


def test_a_localized_scope_must_be_asked_for_and_says_what_it_changes():
    default = _propose()[0]
    localized = _propose(behavior_scope=sf.SCOPE_LOCALIZED)[0]

    assert default.evidence["behavior_scope"] == sf.SCOPE_NETWORK_WIDE
    assert localized.evidence["behavior_scope"] == sf.SCOPE_LOCALIZED
    # Localising a network calibration changes what the numbers represent, and
    # the proposal says so rather than letting it pass as a detail.
    assert "network-wide" in localized.rationale()["en"]
    assert sf.ASSUME_CHANGE_IS_NETWORK_WIDE not in localized.assumptions


# ---------------------------------------------------------------------------
# 8. The two standing bans, still standing
# ---------------------------------------------------------------------------


def test_no_proposal_ever_names_a_w99_parameter():
    banned = {f"cc{n}" for n in range(10)}
    for measured in (1630, 1700, 1950, 2200):
        for action in _propose(measurement=_measurement(saturation_flow_pcu=measured)):
            assert action.params["parameter"] not in banned
            assert action.params["parameter"] in {"ax", "bx_add", "bx_mult"}


def test_the_w99_ban_itself_is_still_a_refusal_in_the_constraint_engine():
    """Not this module's code — but this module is the first thing in the repo
    to propose car-following writes at all, so the ban it relies on is pinned
    beside it."""
    for parameter in ("cc0", "cc1", "cc9"):
        findings = constraints.run_checks(
            "preflight",
            {"tool": "set_driving_behavior_parameter",
             "params": {"behavior_no": "2", "parameter": parameter, "value": 1.5}},
            {"driving_behaviors": [{"no": "2", "car_following_model": 99, "reference_count": 0}]},
        )
        ids = {(f.constraint_id, f.tier) for f in findings}
        assert ("agency_restricted_parameter_write", "refuse") in ids, parameter


def test_no_proposal_ever_touches_a_measured_entrance_input():
    """D4's structural refusal, respected from the other side: this module's
    whole premise is that the entrance volume is measured DATA and therefore
    not ours to edit. It must never reach for the demand or routing verbs."""
    forbidden = {"set_vehicle_input_volume", "set_route_relative_flow"}
    for measured in (1630, 1900, 1950, 2200):
        for action in _propose(measurement=_measurement(saturation_flow_pcu=measured)):
            assert action.verb not in forbidden
            assert "input_no" not in (action.params or {})
            assert "rel_flow" not in (action.params or {})


def test_the_new_scope_is_registered_where_every_consumer_validates():
    """``CAR_FOLLOWING_LEVEL`` lives in ``fixes`` rather than in this module
    because ``fixes.SCOPES`` is the one list every consumer validates against
    (the catalogue's own scope check, and the coverage test's
    ``outcome.scope in fixes.SCOPES``). A sibling module that minted its own
    scope string would silently fail those."""
    assert fixes.CAR_FOLLOWING_LEVEL in fixes.SCOPES
    assert "CAR_FOLLOWING_LEVEL" in fixes.__all__
    for action in _propose():
        assert action.scope in fixes.SCOPES


# ---------------------------------------------------------------------------
# 9. Adversarial review, 2026-08-18 — four confirmed defects, sealed
#
# Every test below reproduces a break an adversarial pass actually achieved
# against the unit as written, not a hypothetical. Each names what it caught.
# ---------------------------------------------------------------------------


def test_the_plausibility_band_is_never_attributed_to_main_roads():
    """BROKEN → FIXED. The IMPLAUSIBLE refusal told the client, in all three
    languages, that its 800–2200 PCU/h band was "MRWA Appendix B's own
    published plausibility band". It is not. ``docs/product/reference/
    mrwa-appendix-b-extract.md`` carries it under the heading "Constraint-engine
    candidates (drafted from the rules above — 0.3 implements/curates)", at
    **Warn** tier, written "~800–2200 PCU/h" with a tilde; and the number
    appears nowhere in ``client-data/operational-modelling-v2.pdf``.
    ``mrwa_reference`` labels it honestly (``source`` = "Appendix B extract's
    own 'Constraint-engine candidates' section", ``PAGE_CITE_PENDING``) — the
    consumer was promoting a draft of ours into a Main Roads publication.

    A refusal citing a fabricated authority is the worse failure mode of the two
    this project's hard rules name: it is both an invented citation and the kind
    of wrong number a client would take to a regulator."""
    actions = _propose(measurement=_measurement(saturation_flow_pcu=2500))
    assert actions[0].reason_code == sf.SATURATION_FLOW_IMPLAUSIBLE

    rationale = actions[0].rationale()
    for language in ("ko", "en", "ja"):
        text = rationale[language]
        assert "800" in text and "2200" in text, f"{language}: the band is not stated"
        # The band may be NAMED alongside Appendix B — that is where we took it
        # from — but never as something Main Roads published or "presented".
        for forbidden in (
            "Appendix B's own plausibility band",
            "MRWA Appendix B's own plausibility band",
            "Appendix B가 제시한 타당 범위",
            "Appendix B が示す妥当範囲",
        ):
            assert forbidden not in text, f"{language}: re-attributes the band to MRWA"

    # And the reference pack still records the honest provenance the refusal
    # text now defers to, so the two cannot drift apart again.
    band = mrwa_reference.SATURATION_FLOW_PLAUSIBILITY_BANDS
    assert "Constraint-engine candidates" in band.source
    entry = next(e for e in band.entries if e.unit == "PCU/h")
    assert entry.citation == mrwa_reference.PAGE_CITE_PENDING


def test_the_forecast_never_says_clean_where_the_engine_refuses():
    """BROKEN → FIXED. ``_preflight_forecast`` ANDed its verdict with
    ``scope == SCOPE_NETWORK_WIDE``, so a LOCALIZED request against a shared
    behaviour forecast "No in-place-edit collision expected … the duplicate-
    and-retarget path applies" while ``constraints.driving_behavior_shared_
    object_edit`` refused it — because nothing had duplicated anything. The
    params are byte-identical under either scope (``_build_actions`` varies the
    rationale, never the payload), so scope could never have changed the
    verdict.

    The pre-existing forecast test swept ``reference_count`` but only at the
    default scope, which is exactly the half of the matrix that held. This
    sweeps BOTH axes, and asserts the safe direction explicitly: a forecast may
    be pessimistic, never optimistic.

    UNCHANGED IN MEANING by unit #44-b, which added a second path: the
    top-level ``expected_to_refuse`` still answers "what happens if this write
    is issued on its own", which is what ``fixes.preflight_findings`` (no
    approval context, ever) measures. The approved path is a separate key and
    has its own half of the matrix in
    ``test_the_forecast_and_the_engine_agree_on_both_paths``."""
    for scope in (None, sf.SCOPE_NETWORK_WIDE, sf.SCOPE_LOCALIZED, "local", "nonsense"):
        for reference_count in (0, 1, 4):
            actions = _propose(behavior=_behavior(reference_count), behavior_scope=scope)
            forecast = actions[0].evidence["preflight_forecast"]
            findings = fixes.preflight_findings(
                actions[0], shadow_model=_shadow(reference_count)
            )
            refused = any(
                f.constraint_id == "driving_behavior_shared_object_edit" for f in findings
            )
            assert forecast["expected_to_refuse"] == refused, (scope, reference_count)
            assert forecast["paths"][sf.PATH_DIRECT_EDIT]["expected_to_refuse"] == refused
            # The direction that matters, stated as its own claim.
            assert not (refused and not forecast["expected_to_refuse"]), (scope, reference_count)

    # A localized ask against a shared behaviour must say WHY it still refuses,
    # rather than implying the duplication already happened.
    localized = _propose(behavior=_behavior(4), behavior_scope=sf.SCOPE_LOCALIZED)[0]
    why = localized.evidence["preflight_forecast"]["why"]
    assert "does NOT avoid this refusal" in why
    assert "duplicate_driving_behavior" in why


def test_the_forecast_and_the_engine_agree_on_both_paths():
    """Unit #44-b's extension of the invariant above, on the axis it added.

    There are now two answers to "will this be refused" — the write issued on its
    own, and the write dispatched through the approval machinery under this set's
    grant — and the forecast carries both. The refuter's rule has to hold on each
    independently, so both are measured against the REAL engine, with each path's
    own provenance supplied exactly the way ``orchestrator._preflight_extra``
    supplies it: from the gate, never from the payload.

    Reference counts 0/1/4 cover the three cases that behave differently: nothing
    to excuse, the smallest sharing that already refused before #44-b, and the
    multi-consumer case the real consultant model looks like.
    """
    for reference_count in (0, 1, 4):
        actions = _propose(behavior=_behavior_with_refs(reference_count))
        forecast = actions[0].evidence["preflight_forecast"]
        shadow = _shadow(reference_count)
        payload = {"tool": actions[0].verb, "params": dict(actions[0].params)}
        engine = guardrails.GuardrailEngine(disabled=False)

        # (a) direct edit — no gate, no grant, no approval.
        direct = engine.preflight(payload, shadow)
        assert forecast["paths"][sf.PATH_DIRECT_EDIT]["expected_to_refuse"] == direct.blocking

        # (b) approved set — the gate that a human's click produced.
        gate = PreapprovedWriteGate(
            {signature(a.verb, dict(a.params)) for a in actions},
            grant=sf.grant_from_proposals(actions),
        )
        assert gate.authorize(payload, None) is True
        approved = engine.preflight(
            payload, shadow, {car_following_grant.PROVENANCE_KEY: gate.write_provenance(payload)}
        )
        assert forecast["paths"][sf.PATH_APPROVED_SET]["expected_to_refuse"] == approved.blocking
        assert approved.blocking is False, reference_count

        # The direction that matters, on both paths at once: a path that says
        # clean must be a path the engine does not refuse.
        for path, report in (
            (sf.PATH_DIRECT_EDIT, direct),
            (sf.PATH_APPROVED_SET, approved),
        ):
            assert not (
                report.blocking and not forecast["paths"][path]["expected_to_refuse"]
            ), (path, reference_count)


def test_the_approved_path_forecast_names_the_exception_it_rides():
    """"Clean" is not enough on its own: a modeller reading the forecast has to be
    able to see WHY the shared-object refusal will not fire, and that the exception
    is bound to this approved set rather than to car-following writes in general."""
    approved = _propose(behavior=_behavior_with_refs(4))[0].evidence["preflight_forecast"][
        "paths"
    ][sf.PATH_APPROVED_SET]

    assert approved["expected_to_refuse"] is False
    assert approved["constraint_id"] == "car_following_shared_edit_consented"
    assert approved["refuses_if_radius_drifts"] == "car_following_consent_radius_drifted"
    assert "DECISIONS.md 2026-08-19" in approved["why"]
    # And the direct path still says, in its own words, that it is still refused.
    direct = _propose(behavior=_behavior_with_refs(4))[0].evidence["preflight_forecast"]["paths"][
        sf.PATH_DIRECT_EDIT
    ]
    assert direct["expected_to_refuse"] is True
    assert "still refused" in direct["why"]


def test_the_approved_path_forecast_stops_saying_clean_when_the_radius_is_partial():
    """BROKEN → FIXED (refuter, #44-b). The approved path reported
    ``expected_to_refuse: False`` unconditionally, so once ``evaluate`` started refusing a
    radius it could not name in full, the forecast was optimistic exactly where the
    guardrail bites — the one direction this forecast has never been allowed to be wrong
    in. Measured against the REAL engine on both shapes the fix covers."""
    for behavior, complete in (
        (_behavior_with_refs(4), True),                       # every consumer named
        (_truncated_behavior(13), False),                     # past tools_network's cap
        (_behavior(7), False),                                # counted, never named
    ):
        # The live entry is the SAME behaviour, carrying the rest of the shadow-model
        # shape (car_following_model) so no unrelated constraint decides the verdict.
        live = {**_shadow(0)["driving_behaviors"][0], **behavior}
        live.pop("referenced_by", None)
        live.update(behavior)

        actions = _propose(behavior=behavior)
        approved = actions[0].evidence["preflight_forecast"]["paths"][sf.PATH_APPROVED_SET]
        assert approved["expected_to_refuse"] is not complete, behavior

        payload = {"tool": actions[0].verb, "params": dict(actions[0].params)}
        gate = PreapprovedWriteGate(
            {signature(a.verb, dict(a.params)) for a in actions},
            grant=sf.grant_from_proposals(actions),
        )
        gate.authorize(payload, None)
        report = guardrails.GuardrailEngine(disabled=False).preflight(
            payload, {"driving_behaviors": [live]},
            {car_following_grant.PROVENANCE_KEY: gate.write_provenance(payload)},
        )
        assert report.blocking == approved["expected_to_refuse"], behavior
        if not complete:
            assert "cannot be named in full" in approved["why"]


def test_an_unshared_behaviour_does_not_credit_the_exception_for_being_clean():
    """A behaviour with no consumers was always editable. Saying the approved path is
    clean "via the exception" there would misreport what the guardrail is doing and
    make the exception look broader than it is."""
    approved = _propose(behavior=_behavior_with_refs(0))[0].evidence["preflight_forecast"][
        "paths"
    ][sf.PATH_APPROVED_SET]

    assert approved["expected_to_refuse"] is False
    assert "NOT via the exception" in approved["why"]


def test_a_measurement_cannot_declare_itself_valid():
    """BROKEN → FIXED. ``propose_car_following_parameters`` read
    ``measurement["ok"]`` and believed it, so every data-quality gate in
    ``observed_measurement`` was opt-in. A hand-built
    ``{"saturation_flow_pcu": 1950, "ok": True}`` — no method, no attribution,
    no samples — produced three fully-cited PROPOSALs whose rationale read
    "Measurement source: on-site measurement (-) at -, -, submitted by -".

    This repo already fixed this exact anti-pattern once, on the very guardrail
    this module forecasts: ``constraints._check_driving_behavior_shared_object_
    edit`` used to trust a caller-supplied ``duplicated_before_edit`` boolean
    and was rewritten to measure the real reference graph, because a
    self-reported flag "any planner call could simply always set True" gives
    zero real protection. Same defect, same fix."""
    forged = {"provenance": sf.OBSERVED, "saturation_flow_pcu": 1950, "ok": True}
    actions = _propose(measurement=forged)
    assert len(actions) == 1
    assert actions[0].outcome == fixes.REFUSAL
    assert actions[0].reason_code == sf.MEASUREMENT_PROVENANCE_MISSING
    assert actions[0].verb is None

    # Truthy-but-not-True, and the sample gates too.
    understaffed = {
        "provenance": sf.OBSERVED, "saturation_flow_pcu": 1950, "ok": 1,
        "method": "eyeball", "attribution": "someone", "samples": 2, "vehicles_per_sample": 2,
    }
    actions = _propose(measurement=understaffed)
    assert actions[0].reason_code == sf.SAMPLE_SIZE_BELOW_MRWA_MINIMUM

    # A provenance that cannot reach a proposal cannot claim it has.
    actions = _propose(measurement={"provenance": sf.DERIVED, "saturation_flow_pcu": 1950,
                                    "ok": True, "method": "m", "attribution": "a"})
    assert actions[0].reason_code == sf.SATURATION_FLOW_NOT_DERIVABLE

    # And the honest path is untouched: re-validating a real record is a no-op.
    good = _measurement()
    assert sf._authoritative_measurement(dict(good)) == good
    assert len(_propose(measurement=good)) == 3


def test_a_partially_applied_set_is_detectable_and_named():
    """BROKEN → FIXED. The three writes were coupled by a warning sentence and
    nothing else. Measured: ``PreapprovedWriteGate`` authorises the three
    signatures independently (approve only ``bx_add`` and it returns True for
    that one, False for the others — its documented behaviour), no field on
    ``FixProposal`` groups them, and ``constraints.run_checks`` returns no
    finding on the resulting state. A behaviour on the 1630 row that received
    only the ``bx_add`` half of the 1950 row therefore ended at
    ``(2.5, 2.25, 3.75)`` — matching no published row, indistinguishable from
    hand-tuning, and undetectable.

    Locking the set would mean changing approval machinery every other unit
    shares, so the least-invasive close was a detection path: the state is now
    nameable by ``verify_applied_row``, and the proposals carry the set identity
    and the complete expected end-state as data."""
    actions = _propose()  # the 1950 row
    row = actions[0].evidence["applied_row"]

    # The set is now data, identically on all three, not only prose.
    assert len({a.evidence["set_id"] for a in actions}) == 1
    for action in actions:
        assert action.evidence["set_members"] == ["ax", "bx_add", "bx_mult"]
        assert action.evidence["expected_after_apply"] == {
            "ax": 2.5, "bx_add": 2.25, "bx_mult": 3.25
        }

    # The exact partial state the attack produced is now named.
    partial = {"ax": 2.5, "bx_add": 2.25, "bx_mult": 3.75}  # 1950's add, 1630's mult
    verdict = sf.verify_applied_row(partial, expected_row=row)
    assert verdict["status"] == sf.ROW_UNMATCHED
    assert verdict["row"] is None
    assert verdict["per_parameter_rows"] == {"bx_add": [1950.0], "bx_mult": [1630.0]}
    assert "PARTIALLY APPLIED" in verdict["why"]

    # The fully applied set verifies clean, so the check cannot cry wolf.
    complete = dict(actions[0].evidence["expected_after_apply"])
    assert sf.verify_applied_row(complete, expected_row=row)["status"] == sf.ROW_MATCHED
    # Float noise off an .inpx round-trip must not read as a mismatch.
    noisy = {"ax": 2.5, "bx_add": 2.2500000001, "bx_mult": 3.2499999999}
    assert sf.verify_applied_row(noisy, expected_row=row)["status"] == sf.ROW_MATCHED

    # A published row that is the WRONG published row is its own answer.
    other = sf.verify_applied_row({"ax": 2.5, "bx_add": 2.75, "bx_mult": 3.75}, expected_row=row)
    assert other["status"] == sf.ROW_MATCHED
    assert "NOT the 1950 pcu row" in other["why"]

    # Missing readings are reported as missing, never as a mismatch.
    for unreadable in ({}, None, {"ax": 2.5}, {"bx_add": "n/a", "bx_mult": None}):
        assert sf.verify_applied_row(unreadable)["status"] == sf.ROW_UNREADABLE


def test_the_real_models_hand_tuned_behaviours_are_what_the_check_catches():
    """The module docstring names three behaviours in the real consultant model
    — "flyt updated 2.4 3.0", "2.2 2.8", "2.3 3.0" — whose pairs appear in no
    row of Table 5-6, and calls that "precisely the state this unit exists to
    replace". Until the adversarial pass there was no code that could say so
    about any of them. Now there is, so the docstring's claim is executable."""
    for bx_add, bx_mult in ((2.4, 3.0), (2.2, 2.8), (2.3, 3.0)):
        verdict = sf.verify_applied_row({"ax": 2.5, "bx_add": bx_add, "bx_mult": bx_mult})
        assert verdict["status"] == sf.ROW_UNMATCHED, (bx_add, bx_mult)
        assert verdict["row"] is None
        assert mrwa_reference.TABLE_5_6_CITATION in verdict["citation"]

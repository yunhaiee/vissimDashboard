"""Tests for the data-quality gate (R2.5, services/dataimport/quality.py).

Every check gets both halves: it fires on the defect it exists for, and it
stays silent on clean data. A gate that cries wolf is a gate people switch off,
and a gate that never fires is decoration.

The refuse/warn split is asserted explicitly per check, because that split is
the argued design decision the gate stands on (see the module docstring in
quality.py and docs/product/adapter-contract.md §"Refuse vs warn").
"""
from __future__ import annotations

from datetime import datetime, timedelta

from services.dataimport.base import AdapterInfo, ImportResult, IssueSeverity
from services.dataimport.quality import (
    QUALITY_CHECKS,
    QualityThresholds,
    apply_quality_gate,
    check_approach_total_consistency,
    check_class_coverage,
    check_controller_completeness,
    check_counts_present,
    check_detector_count_intervals,
    check_interval_overlap,
    check_interval_regularity,
    check_minimum_observations,
    check_neutral_day,
    check_peak_coverage,
    check_program_cycle_consistency,
    check_signal_reference_integrity,
    check_volume_sanity,
    run_quality_gate,
    series_key,
)
from services.dataimport.schema import (
    CanonicalVehicleClass,
    ClassificationScheme,
    DetectorCountData,
    DetectorData,
    MovementRef,
    ApproachRef,
    PedestrianPhaseData,
    PhaseData,
    ProgramSourceKind,
    Provenance,
    SignalControllerData,
    SignalGroupData,
    SignalGroupWindow,
    SignalProgramData,
    TimeWindow,
    TurnType,
    TurningCountData,
    VehicleClassRef,
    VolumeUnit,
)

ADAPTER = AdapterInfo(name="test", region="XX", source_system="Test", version="0")
PROV = Provenance(source_file="counts.csv", locator="row 2")
WEDNESDAY = datetime(2026, 3, 4, 8, 0)  # neutral mid-week day
MONDAY = datetime(2026, 3, 2, 8, 0)
THRESHOLDS = QualityThresholds()

LIGHT = VehicleClassRef(
    scheme=ClassificationScheme.AUSTROADS, code="1", canonical=CanonicalVehicleClass.LIGHT_VEHICLE
)
TOTALS = VehicleClassRef(
    scheme=ClassificationScheme.UNCLASSIFIED,
    code="all",
    canonical=CanonicalVehicleClass.ALL_VEHICLES,
)


def _count(
    start: datetime,
    *,
    minutes: int = 15,
    volume: float = 120,
    movement: MovementRef | None = None,
    vehicle_class: VehicleClassRef | None = None,
    leg: str = "N",
    site: str = "359",
) -> TurningCountData:
    return TurningCountData(
        provenance=PROV,
        site_id=site,
        approach=ApproachRef(leg_id=leg),
        movement=movement or MovementRef(turn=TurnType.THROUGH),
        vehicle_class=vehicle_class or LIGHT,
        interval_start=start,
        interval_end=start + timedelta(minutes=minutes),
        volume=volume,
        volume_unit=VolumeUnit.VEHICLES,
    )


def _clean_counts(start: datetime = WEDNESDAY, intervals: int = 12) -> list[TurningCountData]:
    """3 hours of contiguous 15-minute bins on a neutral day — passes every check."""
    return [_count(start + timedelta(minutes=15 * i)) for i in range(intervals)]


def _result(**kwargs) -> ImportResult:
    return ImportResult(adapter=ADAPTER, **kwargs)


def _codes(issues, severity=None) -> list[str]:
    return [issue.code for issue in issues if severity is None or issue.severity is severity]


def _run(check, **kwargs):
    return check(_result(**kwargs), THRESHOLDS)


# ---------------------------------------------------------------------------
# The clean baseline: nothing refuses, nothing warns
# ---------------------------------------------------------------------------


def test_clean_count_data_produces_no_refusals_and_no_warnings():
    issues = run_quality_gate(_result(counts=_clean_counts()))
    assert _codes(issues, IssueSeverity.REFUSE) == []
    assert _codes(issues, IssueSeverity.WARN) == []
    # INFO is still emitted — the gate says what it could NOT verify.
    assert "peak_period_definition_unavailable" in _codes(issues, IssueSeverity.INFO)
    assert "neutral_day_holiday_status_unverified" in _codes(issues, IssueSeverity.INFO)


# ---------------------------------------------------------------------------
# counts_present
# ---------------------------------------------------------------------------


def test_no_counts_is_information_not_a_failure():
    issues = _run(check_counts_present)
    assert _codes(issues) == ["no_count_data"]
    assert issues[0].severity is IssueSeverity.INFO


def test_detector_only_import_says_routing_cannot_be_seeded():
    detector = DetectorCountData(
        provenance=PROV,
        site_id="359",
        detector_id="D1",
        interval_start=WEDNESDAY,
        interval_end=WEDNESDAY + timedelta(minutes=15),
        volume=50,
        volume_unit=VolumeUnit.VEHICLES,
    )
    issues = _run(check_counts_present, detector_counts=[detector])
    assert "routing cannot be seeded" in issues[0].message


def test_counts_present_is_silent_when_counts_exist():
    assert _run(check_counts_present, counts=_clean_counts()) == []


# ---------------------------------------------------------------------------
# interval overlap (REFUSE) and regularity (WARN)
# ---------------------------------------------------------------------------


def test_overlapping_intervals_refuse_because_the_totals_are_provably_wrong():
    counts = _clean_counts()
    counts.append(_count(WEDNESDAY + timedelta(minutes=7)))
    issues = _run(check_interval_overlap, counts=counts)
    assert _codes(issues) == ["count_intervals_overlap"]
    assert issues[0].severity is IssueSeverity.REFUSE
    assert issues[0].context["overlaps"]


def test_adjacent_intervals_do_not_count_as_overlapping():
    assert _run(check_interval_overlap, counts=_clean_counts()) == []


def test_non_fifteen_minute_bins_warn_and_cite_the_guideline():
    counts = [_count(WEDNESDAY + timedelta(minutes=5 * i), minutes=5) for i in range(12)]
    issues = _run(check_interval_regularity, counts=counts)
    assert _codes(issues) == ["count_interval_not_preferred_length"]
    assert issues[0].severity is IssueSeverity.WARN
    assert "p.59" in issues[0].citation


def test_mixed_bin_lengths_warn():
    counts = [_count(WEDNESDAY), _count(WEDNESDAY + timedelta(minutes=15), minutes=30)]
    issues = _run(check_interval_regularity, counts=counts)
    assert "count_interval_duration_mixed" in _codes(issues)


def test_a_hole_in_a_series_warns_and_says_it_is_not_zero_demand():
    counts = _clean_counts()
    del counts[4]
    issues = _run(check_interval_regularity, counts=counts)
    assert "count_interval_gaps" in _codes(issues, IssueSeverity.WARN)
    gap_issue = next(issue for issue in issues if issue.code == "count_interval_gaps")
    assert "not be read as zero demand" in gap_issue.message


def test_regularity_is_silent_on_a_clean_fifteen_minute_series():
    assert _run(check_interval_regularity, counts=_clean_counts()) == []


# ---------------------------------------------------------------------------
# peak coverage (REFUSE — an explicit authority minimum)
# ---------------------------------------------------------------------------


def test_coverage_below_three_hours_refuses():
    issues = _run(check_peak_coverage, counts=_clean_counts(intervals=4))  # 1 hour
    refusals = [issue for issue in issues if issue.severity is IssueSeverity.REFUSE]
    assert [issue.code for issue in refusals] == ["peak_coverage_below_minimum"]
    assert refusals[0].context["longest_block_h"] == 1.0
    assert "docs/meeting-brief-tk-mrwa.md" in refusals[0].citation


def test_exactly_three_hours_passes():
    issues = _run(check_peak_coverage, counts=_clean_counts(intervals=12))
    assert _codes(issues, IssueSeverity.REFUSE) == []


def test_two_separate_three_hour_peaks_pass():
    counts = _clean_counts(intervals=12) + _clean_counts(
        start=WEDNESDAY + timedelta(hours=8), intervals=12
    )
    assert _codes(_run(check_peak_coverage, counts=counts), IssueSeverity.REFUSE) == []


def test_coverage_check_admits_it_cannot_verify_the_block_is_a_peak():
    issues = _run(check_peak_coverage, counts=_clean_counts())
    assert _codes(issues, IssueSeverity.INFO) == ["peak_period_definition_unavailable"]


# ---------------------------------------------------------------------------
# neutral day (WARN — unrepresentative, not wrong)
# ---------------------------------------------------------------------------


def test_a_monday_survey_warns_rather_than_refusing():
    issues = _run(check_neutral_day, counts=_clean_counts(start=MONDAY))
    warn = [issue for issue in issues if issue.severity is IssueSeverity.WARN]
    assert [issue.code for issue in warn] == ["non_neutral_survey_day"]
    assert "2026-03-02" in warn[0].message


def test_a_wednesday_survey_does_not_warn():
    issues = _run(check_neutral_day, counts=_clean_counts())
    assert _codes(issues, IssueSeverity.WARN) == []


def test_holiday_status_is_reported_as_unverified_rather_than_assumed():
    issues = _run(check_neutral_day, counts=_clean_counts())
    info = next(issue for issue in issues if issue.severity is IssueSeverity.INFO)
    assert info.code == "neutral_day_holiday_status_unverified"
    assert "no holiday calendar" in info.message


# ---------------------------------------------------------------------------
# minimum observations
# ---------------------------------------------------------------------------


def test_a_thin_movement_series_warns():
    issues = _run(check_minimum_observations, counts=_clean_counts(intervals=3))
    assert _codes(issues) == ["series_observations_below_minimum"]
    assert issues[0].severity is IssueSeverity.WARN


def test_a_full_series_does_not_warn():
    assert _run(check_minimum_observations, counts=_clean_counts()) == []


# ---------------------------------------------------------------------------
# approach total vs movement sums
# ---------------------------------------------------------------------------


def _total_and_parts(total_volume: float) -> list[TurningCountData]:
    return [
        _count(WEDNESDAY, movement=MovementRef(is_approach_total=True), volume=total_volume),
        _count(WEDNESDAY, movement=MovementRef(turn=TurnType.LEFT), volume=50),
        _count(WEDNESDAY, movement=MovementRef(turn=TurnType.THROUGH), volume=100),
        _count(WEDNESDAY, movement=MovementRef(turn=TurnType.RIGHT), volume=50),
    ]


def test_an_approach_total_that_contradicts_its_movements_refuses():
    issues = _run(check_approach_total_consistency, counts=_total_and_parts(300))
    assert _codes(issues) == ["approach_total_mismatch"]
    assert issues[0].severity is IssueSeverity.REFUSE
    assert issues[0].context["stated_total"] == 300
    assert issues[0].context["movement_sum"] == 200


def test_a_rounding_sized_difference_only_warns():
    issues = _run(check_approach_total_consistency, counts=_total_and_parts(202))
    assert _codes(issues) == ["approach_total_rounding_difference"]
    assert issues[0].severity is IssueSeverity.WARN


def test_a_consistent_approach_total_is_silent():
    assert _run(check_approach_total_consistency, counts=_total_and_parts(200)) == []


def test_movements_without_a_stated_total_are_not_second_guessed():
    counts = [record for record in _total_and_parts(200) if not record.movement.is_approach_total]
    assert _run(check_approach_total_consistency, counts=counts) == []


# ---------------------------------------------------------------------------
# class coverage
# ---------------------------------------------------------------------------


def test_totals_only_counts_warn_that_composition_cannot_be_seeded():
    counts = [_count(WEDNESDAY + timedelta(minutes=15 * i), vehicle_class=TOTALS) for i in range(12)]
    issues = _run(check_class_coverage, counts=counts)
    assert _codes(issues, IssueSeverity.WARN) == ["class_coverage_totals_only"]
    assert "p.19" in issues[0].citation


def test_an_unmapped_class_code_is_reported_as_information_not_guessed():
    unknown = VehicleClassRef(scheme=ClassificationScheme.SOURCE_SPECIFIC, code="HV")
    counts = [_count(WEDNESDAY, vehicle_class=unknown)]
    issues = _run(check_class_coverage, counts=counts)
    assert _codes(issues, IssueSeverity.INFO) == ["vehicle_class_unmapped"]
    assert "declined to guess" in issues[0].message


def test_fully_classified_counts_are_silent():
    assert _run(check_class_coverage, counts=_clean_counts()) == []


# ---------------------------------------------------------------------------
# volume sanity
# ---------------------------------------------------------------------------


def test_an_implausible_flow_rate_warns():
    issues = _run(check_volume_sanity, counts=[_count(WEDNESDAY, volume=5000)])
    assert _codes(issues) == ["volume_rate_implausible"]
    assert issues[0].severity is IssueSeverity.WARN
    assert issues[0].provenance is PROV


def test_an_approach_total_gets_the_wider_band():
    total = _count(WEDNESDAY, movement=MovementRef(is_approach_total=True), volume=1000)
    assert _run(check_volume_sanity, counts=[total]) == []


def test_ordinary_volumes_are_silent():
    assert _run(check_volume_sanity, counts=_clean_counts()) == []


# ---------------------------------------------------------------------------
# detector count intervals
# ---------------------------------------------------------------------------


def _detector_count(start: datetime, minutes: int = 15, detector: str = "D1") -> DetectorCountData:
    return DetectorCountData(
        provenance=PROV,
        site_id="359",
        detector_id=detector,
        interval_start=start,
        interval_end=start + timedelta(minutes=minutes),
        volume=60,
        volume_unit=VolumeUnit.VEHICLES,
    )


def test_overlapping_detector_intervals_refuse():
    records = [_detector_count(WEDNESDAY), _detector_count(WEDNESDAY + timedelta(minutes=5))]
    issues = _run(check_detector_count_intervals, detector_counts=records)
    assert "detector_count_intervals_overlap" in _codes(issues, IssueSeverity.REFUSE)


def test_detector_gaps_warn():
    records = [_detector_count(WEDNESDAY), _detector_count(WEDNESDAY + timedelta(minutes=45))]
    issues = _run(check_detector_count_intervals, detector_counts=records)
    assert "detector_count_interval_gaps" in _codes(issues, IssueSeverity.WARN)


def test_clean_detector_series_is_silent():
    records = [_detector_count(WEDNESDAY + timedelta(minutes=15 * i)) for i in range(4)]
    assert _run(check_detector_count_intervals, detector_counts=records) == []


# ---------------------------------------------------------------------------
# Signal-side checks
# ---------------------------------------------------------------------------


def _controller(**kwargs) -> SignalControllerData:
    payload = {"provenance": PROV, "controller_id": "359"}
    payload.update(kwargs)
    return SignalControllerData(**payload)


def _program(cycle: float | None, phases: list[PhaseData], **kwargs) -> SignalProgramData:
    return SignalProgramData(
        provenance=PROV,
        program_id="p1",
        source_kind=kwargs.pop("source_kind", ProgramSourceKind.DECLARED),
        cycle_time_s=cycle,
        phases=phases,
        **kwargs,
    )


def _phase(phase_id: str, green: float | None, intergreen: float | None = 6) -> PhaseData:
    return PhaseData(
        provenance=PROV, phase_id=phase_id, green_s=green, intergreen_s=intergreen
    )


def test_a_signal_group_pointing_at_a_phase_nobody_declares_refuses():
    controller = _controller(
        programs=[_program(60, [_phase("A", 54)])],
        signal_groups=[SignalGroupData(provenance=PROV, signal_group_id="1", phase_ids=["Z"])],
    )
    issues = _run(check_signal_reference_integrity, controllers=[controller])
    assert _codes(issues) == ["signal_reference_dangling"]
    assert issues[0].severity is IssueSeverity.REFUSE
    assert "phase 'Z'" in issues[0].context["problems"][0]


def test_a_green_window_for_an_undeclared_group_refuses():
    program = _program(60, [_phase("A", 54)])
    program.signal_group_windows.append(
        SignalGroupWindow(provenance=PROV, signal_group_id="7", green_duration_s=30)
    )
    controller = _controller(programs=[program])
    assert _codes(_run(check_signal_reference_integrity, controllers=[controller])) == [
        "signal_reference_dangling"
    ]


def test_a_detector_or_walk_pointing_at_a_missing_group_refuses():
    controller = _controller(
        detectors=[DetectorData(provenance=PROV, detector_id="D1", signal_group_id="9")],
        pedestrian_phases=[
            PedestrianPhaseData(provenance=PROV, pedestrian_phase_id="1", signal_group_id="8")
        ],
    )
    issues = _run(check_signal_reference_integrity, controllers=[controller])
    assert len(issues[0].context["problems"]) == 2


def test_resolvable_signal_references_are_silent():
    controller = _controller(
        programs=[_program(60, [_phase("A", 54)])],
        signal_groups=[SignalGroupData(provenance=PROV, signal_group_id="1", phase_ids=["A"])],
    )
    assert _run(check_signal_reference_integrity, controllers=[controller]) == []


def test_phases_that_do_not_add_up_to_the_stated_cycle_refuse():
    controller = _controller(programs=[_program(90, [_phase("A", 30), _phase("B", 20)])])
    issues = _run(check_program_cycle_consistency, controllers=[controller])
    assert _codes(issues) == ["program_phase_sum_mismatch"]
    assert issues[0].severity is IssueSeverity.REFUSE


def test_a_scats_round_up_sized_residue_only_warns():
    # Phases sum to 61 s against a stated 62 s cycle: 1 s of SCATS round-up.
    controller = _controller(programs=[_program(62, [_phase("A", 30), _phase("B", 19)])])
    issues = _run(check_program_cycle_consistency, controllers=[controller])
    assert _codes(issues) == ["program_phase_sum_rounding"]
    assert issues[0].severity is IssueSeverity.WARN


def test_an_exact_cycle_is_silent():
    controller = _controller(programs=[_program(60, [_phase("A", 30), _phase("B", 18)])])
    assert _run(check_program_cycle_consistency, controllers=[controller]) == []


def test_an_incomplete_program_is_skipped_with_information_not_refused():
    controller = _controller(
        programs=[
            _program(
                100,
                [PhaseData(provenance=PROV, phase_id="A", observed_total_duration_s=240)],
                source_kind=ProgramSourceKind.OBSERVED_HISTORY,
                observation_window=TimeWindow(
                    start=WEDNESDAY, end=WEDNESDAY + timedelta(hours=1)
                ),
            )
        ]
    )
    issues = _run(check_program_cycle_consistency, controllers=[controller])
    assert _codes(issues) == ["program_cycle_check_skipped"]
    assert issues[0].severity is IssueSeverity.INFO


def test_a_controller_with_nothing_on_it_warns():
    issues = _run(check_controller_completeness, controllers=[_controller()])
    assert _codes(issues) == ["controller_without_timings"]


def test_observed_history_without_a_window_warns():
    controller = _controller(
        programs=[
            _program(None, [_phase("A", 30)], source_kind=ProgramSourceKind.OBSERVED_HISTORY)
        ]
    )
    assert "observed_program_without_window" in _codes(
        _run(check_controller_completeness, controllers=[controller])
    )


def test_a_complete_controller_is_silent():
    controller = _controller(
        programs=[_program(60, [_phase("A", 54)])],
        signal_groups=[SignalGroupData(provenance=PROV, signal_group_id="1", phase_ids=["A"])],
    )
    assert _run(check_controller_completeness, controllers=[controller]) == []


# ---------------------------------------------------------------------------
# Registry + runner behaviour
# ---------------------------------------------------------------------------


def test_check_ids_are_unique_and_every_check_declares_its_tiers():
    ids = [check.id for check in QUALITY_CHECKS]
    assert len(ids) == len(set(ids))
    assert all(check.tiers and callable(check.fn) for check in QUALITY_CHECKS)


def test_no_check_emits_a_severity_it_did_not_declare():
    """Guards the doc against the code: quality.py's tier table is what
    docs/product/adapter-contract.md publishes."""
    messy = _result(
        counts=_clean_counts(start=MONDAY, intervals=3)
        + [_count(MONDAY, volume=9000, vehicle_class=TOTALS)],
        detector_counts=[_detector_count(MONDAY), _detector_count(MONDAY + timedelta(minutes=5))],
        controllers=[
            _controller(
                programs=[_program(90, [_phase("A", 30)])],
                signal_groups=[
                    SignalGroupData(provenance=PROV, signal_group_id="1", phase_ids=["Z"])
                ],
            )
        ],
    )
    for check in QUALITY_CHECKS:
        emitted = {issue.severity for issue in check.fn(messy, THRESHOLDS)}
        assert emitted <= set(check.tiers), f"{check.id} emitted {emitted}, declares {check.tiers}"


def test_run_quality_gate_does_not_mutate_the_result():
    result = _result(counts=_clean_counts(intervals=2))
    issues = run_quality_gate(result)
    assert issues
    assert result.issues == []


def test_apply_quality_gate_appends_and_flips_ok():
    result = _result(counts=_clean_counts(intervals=2))
    assert result.ok is True
    returned = apply_quality_gate(result)
    assert returned is result
    assert result.ok is False  # 30 minutes of coverage refuses
    assert "peak_coverage_below_minimum" in result.issue_codes(IssueSeverity.REFUSE)


def test_thresholds_are_overridable_for_a_jurisdiction_with_different_minimums():
    lenient = QualityThresholds(min_peak_coverage_h=0.5, min_intervals_per_series=2)
    result = _result(counts=_clean_counts(intervals=2))
    issues = run_quality_gate(result, lenient)
    assert _codes(issues, IssueSeverity.REFUSE) == []


def test_series_key_separates_totals_from_turns_and_classes():
    total = _count(WEDNESDAY, movement=MovementRef(is_approach_total=True))
    left = _count(WEDNESDAY, movement=MovementRef(turn=TurnType.LEFT))
    assert series_key(total) != series_key(left)
    assert series_key(total)[2] == "TOTAL"
    assert series_key(_count(WEDNESDAY, vehicle_class=TOTALS)) != series_key(_count(WEDNESDAY))

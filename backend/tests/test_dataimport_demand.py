"""Tests for the demand bridge (services/dataimport/demand.py, R1.4).

The bridge turns neutral counts into the parameters Prism's shipped edit verbs
already take (``set_vehicle_input_volume``, ``set_vehicle_composition_share``).
Two properties carry the weight and get the most attention:

* **it never double-counts** — the neutral model has two independent kinds of
  "this row is a sum of other rows", and a consumer that misses either inflates
  demand silently; and
* **it converts explicitly** — counts per bin become veh/h here, with the
  source figure kept beside the rate so a report can show the arithmetic.

All fixtures are synthetic. The Korean class codes appear because they are the
shape the first real adapter emits, not because any real value is used.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from services.dataimport.base import AdapterInfo, ImportResult, IssueSeverity
from services.dataimport.demand import build_demand_seeding, seedable_counts
from services.dataimport.schema import (
    ApproachRef,
    CanonicalVehicleClass,
    ClassificationScheme,
    MovementRef,
    Provenance,
    TurnType,
    TurningCountData,
    VehicleClassRef,
    VolumeUnit,
)

ADAPTER = AdapterInfo(name="test", region="XX", source_system="Test", version="0")
PROV = Provenance(source_file="counts.xlsx", locator="Sheet1!row 4")
WEDNESDAY = datetime(2026, 4, 8, 7, 0)

CAR = VehicleClassRef(
    scheme=ClassificationScheme.KOREA_SMART_INTERSECTION_6,
    code="승용",
    canonical=CanonicalVehicleClass.LIGHT_VEHICLE,
)
SMALL_TRUCK = VehicleClassRef(
    scheme=ClassificationScheme.KOREA_SMART_INTERSECTION_6, code="화물_소형"
)
ALL_CLASSES = VehicleClassRef(
    scheme=ClassificationScheme.UNCLASSIFIED,
    code="합계",
    canonical=CanonicalVehicleClass.ALL_VEHICLES,
)


def _count(
    start: datetime,
    *,
    minutes: int = 15,
    volume: float = 30,
    turn: TurnType | None = TurnType.THROUGH,
    approach_total: bool = False,
    vehicle_class: VehicleClassRef | None = None,
    leg: str = "가람방면",
    site: str = "가람네거리",
    unit: VolumeUnit = VolumeUnit.VEHICLES,
) -> TurningCountData:
    movement = (
        MovementRef(is_approach_total=True) if approach_total else MovementRef(turn=turn)
    )
    return TurningCountData(
        provenance=PROV,
        site_id=site,
        approach=ApproachRef(leg_id=leg),
        movement=movement,
        vehicle_class=vehicle_class or CAR,
        interval_start=start,
        interval_end=start + timedelta(minutes=minutes),
        volume=volume,
        volume_unit=unit,
    )


def _result(counts) -> ImportResult:
    return ImportResult(adapter=ADAPTER, counts=list(counts))


def _clean_counts(intervals: int = 4):
    """Two movements × two classes per bin — 90 veh per bin, 360 veh/h."""
    counts = []
    for index in range(intervals):
        start = WEDNESDAY + timedelta(minutes=15 * index)
        counts.append(_count(start, turn=TurnType.THROUGH, volume=30, vehicle_class=CAR))
        counts.append(_count(start, turn=TurnType.THROUGH, volume=10, vehicle_class=SMALL_TRUCK))
        counts.append(_count(start, turn=TurnType.LEFT, volume=40, vehicle_class=CAR))
        counts.append(_count(start, turn=TurnType.LEFT, volume=10, vehicle_class=SMALL_TRUCK))
        # the source's own all-class total for each movement — a sum of the above
        counts.append(_count(start, turn=TurnType.THROUGH, volume=40, vehicle_class=ALL_CLASSES))
        counts.append(_count(start, turn=TurnType.LEFT, volume=50, vehicle_class=ALL_CLASSES))
    return counts


# ---------------------------------------------------------------------------
# seedable_counts — the double-count guard, stated once
# ---------------------------------------------------------------------------


def test_seedable_counts_drops_both_kinds_of_total():
    counts = [
        _count(WEDNESDAY, volume=10, vehicle_class=CAR),
        _count(WEDNESDAY, volume=99, vehicle_class=ALL_CLASSES),
        _count(WEDNESDAY, volume=99, approach_total=True),
    ]
    kept = seedable_counts(counts)
    assert [count.volume for count in kept] == [10]


def test_seedable_counts_keeps_a_classified_movement_row():
    counts = [_count(WEDNESDAY, volume=10, vehicle_class=SMALL_TRUCK)]
    assert seedable_counts(counts) == counts


# ---------------------------------------------------------------------------
# Vehicle-input profiles
# ---------------------------------------------------------------------------


def test_a_profile_is_one_interval_per_bin_numbered_from_one():
    seeding = build_demand_seeding(_result(_clean_counts()))
    assert seeding.ok, seeding.issue_codes(IssueSeverity.REFUSE)
    profile = seeding.profile_for("가람네거리", "가람방면")
    assert profile is not None
    assert [interval.interval_no for interval in profile.intervals] == [1, 2, 3, 4]
    assert profile.intervals[0].start == WEDNESDAY
    assert profile.intervals[0].end == WEDNESDAY + timedelta(minutes=15)


def test_counts_per_bin_become_vehicles_per_hour_with_the_arithmetic_attached():
    seeding = build_demand_seeding(_result(_clean_counts()))
    interval = seeding.profile_for("가람네거리", "가람방면").intervals[0]
    assert interval.counted_volume == 90  # 30 + 10 + 40 + 10, totals excluded
    assert interval.interval_minutes == 15
    assert interval.volume_vph == 360
    assert interval.contributing_records == 4


def test_the_stated_totals_are_excluded_and_the_exclusion_is_reported():
    seeding = build_demand_seeding(_result(_clean_counts()))
    assert "demand_totals_excluded_from_sum" in seeding.issue_codes(IssueSeverity.INFO)
    issue = next(i for i in seeding.issues if i.code == "demand_totals_excluded_from_sum")
    assert issue.context["excluded_records"] == 8  # 2 movements × 4 bins
    assert issue.context["summed_records"] == 16


def test_including_a_total_would_have_inflated_the_demand():
    """The regression this guard exists for, stated as a number."""
    seeding = build_demand_seeding(_result(_clean_counts()))
    profile = seeding.profile_for("가람네거리", "가람방면")
    naive = sum(count.volume for count in _clean_counts()[:6])
    assert naive == 180
    assert profile.intervals[0].counted_volume == 90


def test_each_approach_becomes_its_own_profile():
    counts = _clean_counts() + [
        _count(WEDNESDAY + timedelta(minutes=15 * i), leg="나루방면", volume=12)
        for i in range(4)
    ]
    seeding = build_demand_seeding(_result(counts))
    assert {profile.approach_leg_id for profile in seeding.profiles} == {"가람방면", "나루방면"}
    assert seeding.profile_for("가람네거리", "나루방면").peak_volume_vph == 48


def test_a_window_restricts_the_profile_and_records_the_choice():
    seeding = build_demand_seeding(
        _result(_clean_counts(intervals=8)),
        window=(WEDNESDAY, WEDNESDAY + timedelta(minutes=30)),
    )
    profile = seeding.profile_for("가람네거리", "가람방면")
    assert len(profile.intervals) == 2
    assert "demand_window_selected" in seeding.issue_codes(IssueSeverity.INFO)


def test_an_empty_window_refuses_rather_than_seeding_zero_demand():
    seeding = build_demand_seeding(
        _result(_clean_counts()),
        window=(WEDNESDAY + timedelta(days=1), WEDNESDAY + timedelta(days=2)),
    )
    assert seeding.ok is False
    assert "demand_no_counts_in_window" in seeding.issue_codes(IssueSeverity.REFUSE)
    assert seeding.profiles == []


def test_mixed_interval_lengths_refuse_rather_than_being_rebinned():
    counts = _clean_counts() + [
        _count(WEDNESDAY + timedelta(hours=1), minutes=60, volume=200, vehicle_class=CAR)
    ]
    seeding = build_demand_seeding(_result(counts))
    assert seeding.ok is False
    assert "demand_mixed_interval_lengths" in seeding.issue_codes(IssueSeverity.REFUSE)
    assert seeding.profiles == []


def test_a_pcu_weighted_count_refuses_because_the_factors_are_not_carried():
    counts = [_count(WEDNESDAY, volume=30, unit=VolumeUnit.PCU)]
    seeding = build_demand_seeding(_result(counts))
    assert seeding.ok is False
    assert "demand_volume_unit_not_vehicles" in seeding.issue_codes(IssueSeverity.REFUSE)


def test_totals_only_data_refuses_because_nothing_can_be_summed():
    counts = [
        _count(WEDNESDAY + timedelta(minutes=15 * i), vehicle_class=ALL_CLASSES) for i in range(4)
    ]
    seeding = build_demand_seeding(_result(counts))
    assert seeding.ok is False
    assert "demand_only_totals_available" in seeding.issue_codes(IssueSeverity.REFUSE)


def test_a_five_minute_source_converts_on_its_own_bin_length():
    counts = [
        _count(WEDNESDAY + timedelta(minutes=5 * i), minutes=5, volume=10) for i in range(4)
    ]
    seeding = build_demand_seeding(_result(counts))
    assert seeding.profile_for("가람네거리", "가람방면").intervals[0].volume_vph == 120


# ---------------------------------------------------------------------------
# Composition shares
# ---------------------------------------------------------------------------


def test_composition_weights_are_the_raw_counts_vissim_normalises():
    seeding = build_demand_seeding(_result(_clean_counts()))
    composition = seeding.compositions[0]
    weights = {share.vehicle_class.code: share.rel_flow for share in composition.shares}
    assert weights == {"승용": 280.0, "화물_소형": 80.0}
    shares = {share.vehicle_class.code: share.share for share in composition.shares}
    assert abs(shares["승용"] - 280 / 360) < 1e-12
    assert abs(shares["화물_소형"] - 80 / 360) < 1e-12
    assert abs(sum(shares.values()) - 1.0) < 1e-12


def test_the_all_class_total_never_becomes_a_composition_entry():
    seeding = build_demand_seeding(_result(_clean_counts()))
    codes = {
        share.vehicle_class.code
        for composition in seeding.compositions
        for share in composition.shares
    }
    assert "합계" not in codes


def test_an_unmapped_class_needs_a_modeller_to_pick_the_vehicle_type():
    seeding = build_demand_seeding(_result(_clean_counts()))
    assert seeding.compositions[0].unmapped_class_codes == ["화물_소형"]
    issue = next(
        i for i in seeding.issues if i.code == "demand_vehicle_type_mapping_required"
    )
    assert issue.severity is IssueSeverity.INFO
    assert "화물_소형" in issue.context["codes"]


def test_a_composition_is_produced_per_site():
    counts = _clean_counts() + [
        _count(WEDNESDAY + timedelta(minutes=15 * i), site="나루삼거리", volume=5)
        for i in range(4)
    ]
    seeding = build_demand_seeding(_result(counts))
    assert {composition.site_id for composition in seeding.compositions} == {
        "가람네거리",
        "나루삼거리",
    }


# ---------------------------------------------------------------------------
# End to end from the Daejeon adapter
# ---------------------------------------------------------------------------


def test_the_daejeon_adapter_output_seeds_a_vehicle_input_profile(tmp_path):
    """The unit's whole sentence in one test: 대전 카운트 → 중립 스키마 → 입력 모델."""
    from tests.test_dataimport_daejeon import _approach_rows, _write_workbook
    from services.dataimport.adapters.daejeon import DaejeonCountAdapter

    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    result = DaejeonCountAdapter().parse([path])
    seeding = build_demand_seeding(result)

    assert seeding.ok, seeding.issue_codes(IssueSeverity.REFUSE)
    profile = seeding.profile_for("가람네거리", "가람방면")
    assert len(profile.intervals) == 12
    # QUARTER sums to 24 and BUSY to 61; 좌회전 + 직 진 + 우회전 = 109 per bin.
    assert profile.intervals[0].counted_volume == 109
    assert profile.intervals[0].volume_vph == 436
    assert "합계" not in {
        share.vehicle_class.code
        for composition in seeding.compositions
        for share in composition.shares
    }

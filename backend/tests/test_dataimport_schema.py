"""Tests for the neutral domain model (roadmap 0.7, services/dataimport/schema.py).

The schema's job is to make three sins impossible or loud: inventing a value
the source never carried, losing track of where a value came from, and letting
"absent" and "zero" blur together. These tests pin exactly those behaviours,
plus the enum-vs-free-string split.

Pure Pydantic — no Vissim, no files, no COM.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from services.dataimport.schema import (
    CanonicalVehicleClass,
    ClassificationScheme,
    CompassDirection,
    ControlType,
    DetectorCountData,
    MovementRef,
    ApproachRef,
    PedestrianPhaseData,
    PhaseData,
    PositionHint,
    ProgramSourceKind,
    Provenance,
    SignalControllerData,
    SignalGroupData,
    SignalProgramData,
    SiteMetadata,
    TimeWindow,
    TurnType,
    TurningCountData,
    VehicleClassRef,
    VolumeUnit,
    neutral_field_path_exists,
)

PROV = Provenance(source_file="counts.csv", locator="row 2")
START = datetime(2026, 3, 4, 8, 0)
END = START + timedelta(minutes=15)


def _count(**overrides) -> TurningCountData:
    payload = {
        "provenance": PROV,
        "site_id": "359",
        "approach": ApproachRef(leg_id="N"),
        "movement": MovementRef(turn=TurnType.THROUGH),
        "vehicle_class": VehicleClassRef(scheme=ClassificationScheme.UNCLASSIFIED, code="all"),
        "interval_start": START,
        "interval_end": END,
        "volume": 120,
        "volume_unit": VolumeUnit.VEHICLES,
    }
    payload.update(overrides)
    return TurningCountData(**payload)


# ---------------------------------------------------------------------------
# Provenance: every record cites its source
# ---------------------------------------------------------------------------


def test_provenance_rejects_a_record_that_cites_nothing():
    with pytest.raises(ValidationError, match="must cite its source"):
        Provenance(source_file="counts.csv")


def test_provenance_accepts_a_read_record_via_locator_or_native_id():
    assert Provenance(source_file="a.csv", locator="row 7").locator == "row 7"
    assert Provenance(source_file="a.csv", native_id="SG3").native_id == "SG3"


def test_provenance_accepts_a_computed_record_only_when_it_says_how():
    derived = Provenance(source_file="phase_history.csv", derived_by="MRWA A.2 avg cycle")
    assert derived.derived_by == "MRWA A.2 avg cycle"


def test_provenance_requires_a_non_empty_source_file():
    with pytest.raises(ValidationError):
        Provenance(source_file="", locator="row 1")


def test_provenance_is_frozen_so_a_citation_cannot_be_rewritten_downstream():
    with pytest.raises(ValidationError):
        PROV.source_file = "somewhere_else.csv"


def test_every_neutral_record_requires_provenance():
    with pytest.raises(ValidationError):
        SignalControllerData(controller_id="359")


# ---------------------------------------------------------------------------
# Absence is data, and claims of absence must be true
# ---------------------------------------------------------------------------


def test_absent_must_name_a_real_field():
    with pytest.raises(ValidationError, match="does not name a field"):
        SignalControllerData(
            provenance=PROV, controller_id="359", absent={"cylce_time_s": "typo"}
        )


def test_absent_cannot_claim_a_field_that_actually_has_a_value():
    with pytest.raises(ValidationError, match="claims the source lacks this field"):
        SignalControllerData(
            provenance=PROV,
            controller_id="359",
            cycle_time_s=90,
            absent={"cycle_time_s": "not supplied"},
        )


def test_absent_requires_a_non_empty_reason():
    with pytest.raises(ValidationError, match="non-empty reason"):
        SignalControllerData(provenance=PROV, controller_id="359", absent={"cycle_time_s": "  "})


def test_absent_accepts_an_empty_container_as_absence():
    controller = SignalControllerData(
        provenance=PROV, controller_id="359", absent={"programs": "no timing files supplied"}
    )
    assert controller.programs == []


def test_mark_absent_records_the_reason():
    controller = SignalControllerData(provenance=PROV, controller_id="359")
    controller.mark_absent("cycle_time_s", "SCATS site has no single nominal cycle")
    assert controller.absent["cycle_time_s"].startswith("SCATS site")


def test_mark_absent_rejects_a_false_claim_and_leaves_the_record_untouched():
    controller = SignalControllerData(provenance=PROV, controller_id="359", cycle_time_s=90)
    with pytest.raises(ValidationError):
        controller.mark_absent("cycle_time_s", "not supplied")
    assert controller.absent == {}
    assert controller.cycle_time_s == 90


def test_extra_fields_are_forbidden_so_an_adapter_typo_is_loud():
    with pytest.raises(ValidationError):
        SignalControllerData(provenance=PROV, controller_id="359", cycletime=90)


# ---------------------------------------------------------------------------
# Optionals stay optional; None never becomes a number
# ---------------------------------------------------------------------------


def test_a_controller_needs_only_an_id_and_everything_else_stays_none():
    controller = SignalControllerData(provenance=PROV, controller_id="359")
    assert controller.name is None
    assert controller.control_type is None
    assert controller.control_type_raw is None
    assert controller.cycle_time_s is None
    assert controller.cycle_min_s is None
    assert controller.cycle_max_s is None
    assert controller.coordination is None
    assert controller.position is None
    # Container fields are empty, not None-filled with placeholders.
    assert controller.programs == []
    assert controller.signal_groups == []
    assert controller.detectors == []
    assert controller.pedestrian_phases == []


def test_a_phase_with_no_timings_reports_nothing_rather_than_zero():
    phase = PhaseData(provenance=PROV, phase_id="A")
    assert (phase.green_s, phase.amber_s, phase.all_red_s, phase.intergreen_s) == (
        None,
        None,
        None,
        None,
    )
    assert phase.frequency is None and phase.is_stretch_phase is None


def test_signal_group_membership_distinguishes_unknown_from_empty():
    unknown = SignalGroupData(provenance=PROV, signal_group_id="1")
    stated_empty = SignalGroupData(provenance=PROV, signal_group_id="2", phase_ids=[])
    assert unknown.phase_ids is None
    assert stated_empty.phase_ids == []


def test_site_metadata_keeps_the_sources_extraction_time_separate_from_ours():
    site = SiteMetadata(provenance=PROV, site_id="359", source_system="SCATS")
    assert site.source_extracted_at is None
    assert site.imported_at.tzinfo == timezone.utc


# ---------------------------------------------------------------------------
# Enums: closed domains reject junk, open ones keep the raw token
# ---------------------------------------------------------------------------


def test_control_type_rejects_a_value_outside_the_closed_domain():
    with pytest.raises(ValidationError):
        SignalControllerData(provenance=PROV, controller_id="1", control_type="adaptive_martian")
    accepted = SignalControllerData(
        provenance=PROV, controller_id="1", control_type=ControlType.ADAPTIVE_SCATS
    )
    assert accepted.control_type is ControlType.ADAPTIVE_SCATS
    # No UNKNOWN member: "the source did not say" is None, not an enum value.
    assert not any(member.value == "unknown" for member in ControlType)


def test_an_unmappable_control_type_survives_as_a_raw_string():
    controller = SignalControllerData(
        provenance=PROV, controller_id="1", control_type_raw="감응 (semi-actuated)"
    )
    assert controller.control_type is None
    assert controller.control_type_raw == "감응 (semi-actuated)"


def test_turn_type_and_compass_reject_junk():
    with pytest.raises(ValidationError):
        MovementRef(turn="hard_left")
    with pytest.raises(ValidationError):
        ApproachRef(leg_id="N", bearing="NNE")
    assert ApproachRef(leg_id="N", bearing=CompassDirection.NE).bearing is CompassDirection.NE


def test_program_source_kind_is_required_because_read_and_derived_differ():
    with pytest.raises(ValidationError):
        SignalProgramData(provenance=PROV, program_id="p1")
    program = SignalProgramData(
        provenance=PROV, program_id="p1", source_kind=ProgramSourceKind.OBSERVED_HISTORY
    )
    assert program.source_kind is ProgramSourceKind.OBSERVED_HISTORY


def test_vehicle_class_keeps_the_source_code_and_leaves_canonical_unset_when_unsure():
    ref = VehicleClassRef(scheme=ClassificationScheme.SOURCE_SPECIFIC, code="중형화물차")
    assert ref.canonical is None
    assert ref.code == "중형화물차"


def test_unclassified_totals_cannot_masquerade_as_a_specific_class():
    with pytest.raises(ValidationError, match="can only map to canonical=all_vehicles"):
        VehicleClassRef(
            scheme=ClassificationScheme.UNCLASSIFIED,
            code="all",
            canonical=CanonicalVehicleClass.HEAVY_VEHICLE,
        )


def test_volume_unit_has_no_default_so_pcu_can_never_be_read_as_vehicles():
    with pytest.raises(ValidationError):
        _count(volume_unit=None)
    assert _count(volume_unit=VolumeUnit.PCU).volume_unit is VolumeUnit.PCU


# ---------------------------------------------------------------------------
# Cross-field invariants
# ---------------------------------------------------------------------------


def test_count_interval_must_move_forwards():
    with pytest.raises(ValidationError, match="must be after"):
        _count(interval_end=START)


def test_count_interval_minutes_is_computed_not_assumed():
    assert _count().interval_minutes == 15.0
    assert _count(interval_end=START + timedelta(minutes=5)).interval_minutes == 5.0


def test_negative_volume_is_impossible():
    with pytest.raises(ValidationError):
        _count(volume=-1)


def test_approach_total_cannot_also_be_a_turn():
    with pytest.raises(ValidationError, match="cannot also name a turn"):
        MovementRef(is_approach_total=True, turn=TurnType.LEFT)
    assert MovementRef(is_approach_total=True).turn is None


def test_time_window_must_move_forwards():
    with pytest.raises(ValidationError, match="must be after"):
        TimeWindow(start=END, end=START)
    assert TimeWindow(start=START, end=END).duration_s == 900.0


def test_position_hint_with_no_information_is_rejected():
    with pytest.raises(ValidationError, match="carries no information"):
        PositionHint()
    assert PositionHint(lane_no=2).lane_no == 2


def test_min_green_above_max_green_is_rejected():
    with pytest.raises(ValidationError, match="exceeds max_green_s"):
        PhaseData(provenance=PROV, phase_id="A", min_green_s=40, max_green_s=20)


def test_cycle_bounds_must_be_ordered():
    with pytest.raises(ValidationError, match="exceeds cycle_max_s"):
        SignalControllerData(provenance=PROV, controller_id="1", cycle_min_s=140, cycle_max_s=60)


def test_phase_frequency_above_one_is_legal_for_repeat_right_turn_phasing():
    """MRWA Appendix-A A.4 runs a repeat right-turn phase twice per cycle, so
    f_i > 1 is real data. An upper bound here would have forced an adapter to
    clamp — i.e. to report a wrong number."""
    phase = PhaseData(provenance=PROV, phase_id="D", frequency=2.0)
    assert phase.frequency == 2.0
    ped = PedestrianPhaseData(provenance=PROV, pedestrian_phase_id="1", demand_frequency=1.5)
    assert ped.demand_frequency == 1.5
    with pytest.raises(ValidationError):
        PhaseData(provenance=PROV, phase_id="D", frequency=-0.1)


# ---------------------------------------------------------------------------
# Detector counts: the record type Appendix A forced into existence
# ---------------------------------------------------------------------------


def test_detector_counts_exist_separately_because_a_loop_has_no_movement():
    record = DetectorCountData(
        provenance=PROV,
        site_id="359",
        detector_id="D1",
        interval_start=START,
        interval_end=END,
        volume=88,
        volume_unit=VolumeUnit.VEHICLES,
    )
    assert record.vehicle_class is None
    assert record.interval_minutes == 15.0
    assert not hasattr(record, "movement")


def test_detector_count_interval_must_move_forwards():
    with pytest.raises(ValidationError, match="must be after"):
        DetectorCountData(
            provenance=PROV,
            site_id="359",
            detector_id="D1",
            interval_start=END,
            interval_end=START,
            volume=1,
            volume_unit=VolumeUnit.VEHICLES,
        )


# ---------------------------------------------------------------------------
# Membership inversion + the field-path resolver the field maps rely on
# ---------------------------------------------------------------------------


def test_phase_membership_inversion_ignores_groups_that_never_said():
    program = SignalProgramData(
        provenance=PROV, program_id="p1", source_kind=ProgramSourceKind.DECLARED
    )
    groups = [
        SignalGroupData(provenance=PROV, signal_group_id="1", phase_ids=["A"]),
        SignalGroupData(provenance=PROV, signal_group_id="2", phase_ids=["B"]),
        SignalGroupData(provenance=PROV, signal_group_id="3"),  # unknown membership
    ]
    assert program.signal_group_ids_for_phase("A", groups) == ["1"]
    assert program.signal_group_ids_for_phase("Z", groups) == []


def test_neutral_field_path_resolver_walks_nested_models_and_lists():
    assert neutral_field_path_exists("SignalControllerData.controller_id")
    assert neutral_field_path_exists("SignalControllerData.programs.phases.green_s")
    assert neutral_field_path_exists("SignalProgramData.coordination.offsets_s")
    assert neutral_field_path_exists("TurningCountData.vehicle_class.canonical")
    assert not neutral_field_path_exists("SignalControllerData.does_not_exist")
    assert not neutral_field_path_exists("NoSuchModel.field")
    assert not neutral_field_path_exists("")

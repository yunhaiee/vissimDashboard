"""Tests for the SCATS/WA adapter stub — the contract's falsification suite.

Two jobs:

1. **Prove the neutral schema can actually hold MRWA Appendix-A's data.**
   ``test_every_documented_appendix_a_field_lands_on_a_real_schema_field``
   resolves every declared mapping against ``schema.py``. If Appendix A's real
   fields did not fit, this fails and the schema — not the map — is wrong.
   (Writing the map falsified the schema three times; see
   docs/product/adapter-contract.md §"What Appendix A changed".)
2. **Prove the adapter refuses rather than guesses.** Each "missing input"
   test removes one file and asserts the adapter produces an ImportIssue and a
   ``None`` field with a recorded reason — never a plausible number.

Fixtures are synthetic CSVs written from the shapes Appendix A describes. No
Vissim, no network, no licence.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from services.dataimport.adapters.scats_wa import (
    PROTECTION_TYPE_MAP,
    SCATS_FIELD_MAP,
    SCATSWAAdapter,
)
from services.dataimport.base import (
    IssueSeverity,
    SNIFF_CONFIDENCE_THRESHOLD,
    check_adapter_conformance,
)
from services.dataimport.quality import apply_quality_gate
from services.dataimport.schema import (
    ControlType,
    CoordinationPoint,
    DriveSide,
    PedestrianProtectionType,
    ProgramSourceKind,
    neutral_field_path_exists,
)

# ---------------------------------------------------------------------------
# Synthetic fixture, built from MRWA Appendix-A A.1's described shapes.
#
# Phase History: 4 cycles. A runs every cycle (the A.2 stretch phase, 4x),
# B 3x, C 2x, so the stretch phase is unambiguous and n_cycles = 4.
# Total activation 4*60 + 3*40 + 2*20 = 400 s, so A.2's c̄ = 400/4 = 100 s.
# ---------------------------------------------------------------------------

SITE_CSV = """TCS,Name,Region
359,Test Road / Example Street,Metropolitan
"""

TIMINGS_CSV = """SG,Phase,LateStart,EarlyCutOff,MinGreen,MaxGreen,Yellow,AllRed,Walk,PedWalk,PedClearance,PedDelay,Protection
1,A,0,0,10,60,4,2,,,,,
2,B,3,7,8,40,4,2,,,,,
3,,,,,,,,1,6,12,2,exclusive phase
"""

LINK_OFFSET_CSV = """Plan,Subsystem,CoordinatedPhase,CycleMin,CycleMax,Offsets
1,SS1,^A,60,120,10
3,SS1,B,60,140,20;30
"""

_PHASE_SEQUENCE = [
    ("A", 60),
    ("B", 40),
    ("C", 20),
    ("A", 60),
    ("B", 40),
    ("A", 60),
    ("B", 40),
    ("C", 20),
    ("A", 60),
]


def _phase_history_csv() -> str:
    lines = ["Date,Phase,Duration,Start,End"]
    clock = datetime(2026, 3, 4, 8, 0, 0)
    for phase, duration in _PHASE_SEQUENCE:
        end = clock + timedelta(seconds=duration)
        lines.append(f"2026-03-04,{phase},{duration},{clock.isoformat()},{end.isoformat()}")
        clock = end
    return "\n".join(lines) + "\n"


EVENT_HISTORY_CSV = """Timestamp,Event
2026-03-04T08:00:00,SG1=On
2026-03-04T08:00:30,SG1=Off
2026-03-04T08:00:05,Walk 1:Demand=On
2026-03-04T08:00:10,Walk 1:Demand=Off Active=On
2026-03-04T08:00:16,Walk 1:Active=Off
2026-03-04T08:02:00,SG1=On
2026-03-04T08:02:34,SG1=Off
2026-03-04T08:02:05,Walk 1:Demand=Off Active=On
2026-03-04T08:02:11,Walk 1:Active=Off
2026-03-04T08:01:00,SG2=On
2026-03-04T08:01:20,SG2=Off
"""


def _detector_csv() -> str:
    lines = ["Detector,IntervalStart,IntervalEnd,Volume"]
    clock = datetime(2026, 3, 4, 8, 0)
    for index in range(4):
        start = clock + timedelta(minutes=15 * index)
        end = start + timedelta(minutes=15)
        lines.append(f"D1,{start.isoformat()},{end.isoformat()},{100 + index}")
    return "\n".join(lines) + "\n"


FIXTURE_FILES: dict[str, str] = {
    "site.csv": SITE_CSV,
    "timings.csv": TIMINGS_CSV,
    "link_offset_plans.csv": LINK_OFFSET_CSV,
    "phase_history.csv": _phase_history_csv(),
    "event_history.csv": EVENT_HISTORY_CSV,
    "detector_volumes.csv": _detector_csv(),
}


def _write_fixture(root: Path, *, omit: tuple[str, ...] = (), **overrides: str) -> list[Path]:
    paths: list[Path] = []
    contents = dict(FIXTURE_FILES)
    contents.update(overrides)
    for name, text in contents.items():
        if name in omit:
            continue
        path = root / name
        path.write_text(text, encoding="utf-8")
        paths.append(path)
    return paths


@pytest.fixture()
def adapter() -> SCATSWAAdapter:
    return SCATSWAAdapter()


@pytest.fixture()
def files(tmp_path: Path) -> list[Path]:
    return _write_fixture(tmp_path)


def _codes(result, severity=None) -> list[str]:
    return result.issue_codes(severity)


# ---------------------------------------------------------------------------
# 1. Falsification: does Appendix A fit the neutral schema?
# ---------------------------------------------------------------------------


def test_every_documented_appendix_a_field_lands_on_a_real_schema_field():
    unresolved = [
        mapping.neutral_path
        for mapping in SCATS_FIELD_MAP
        if not neutral_field_path_exists(mapping.neutral_path)
    ]
    assert unresolved == [], (
        "these Appendix-A fields have nowhere to go in the neutral model — "
        "the SCHEMA is wrong, not the map"
    )


def test_the_field_map_covers_every_source_structure_appendix_a_lists():
    structures = {mapping.source_structure for mapping in SCATS_FIELD_MAP}
    for expected in (
        "SCATS Phase History",
        "SCATS Event History",
        "Signal Data spreadsheet / Site tab",
        "Signal Data spreadsheet / Timings tab",
        "Signal Data spreadsheet / Link & Offset Plans tab",
        "Detector Volume Data",
        "Phase Sequence Chart (.doc)",
        "Strategic Monitor graph",
        "LMA drawing",
    ):
        assert expected in structures


def test_every_mapping_cites_its_authority_and_declares_stub_coverage():
    for mapping in SCATS_FIELD_MAP:
        assert mapping.citation.startswith("MRWA Appendix A")
        assert isinstance(mapping.parsed_by_stub, bool)
    # The stub is honest about what it does NOT read (drawings, .doc, Special Times).
    assert any(not mapping.parsed_by_stub for mapping in SCATS_FIELD_MAP)


def test_detector_volumes_map_to_detector_counts_not_turning_counts():
    """The mapping that falsified the original schema: a loop volume has no
    movement, so it cannot be a TurningCountData."""
    detector_paths = {
        mapping.neutral_path
        for mapping in SCATS_FIELD_MAP
        if mapping.source_structure == "Detector Volume Data"
    }
    assert any(path.startswith("DetectorCountData") for path in detector_paths)
    assert not any(path.startswith("TurningCountData") for path in detector_paths)


# ---------------------------------------------------------------------------
# 2. Protocol conformance + sniffing
# ---------------------------------------------------------------------------


def test_the_adapter_conforms_to_the_protocol(adapter):
    assert check_adapter_conformance(adapter) == []
    assert adapter.info.region == "AU-WA"
    assert adapter.info.source_system == "SCATS"


def test_sniff_recognises_the_full_appendix_a_drop(adapter, files):
    sniffed = adapter.sniff(files)
    assert sniffed.confidence >= SNIFF_CONFIDENCE_THRESHOLD
    assert "corroborated" in sniffed.reason
    assert "site.csv" in sniffed.matched_files


def test_sniff_returns_zero_rather_than_raising_on_unrelated_files(adapter, tmp_path):
    unrelated = tmp_path / "budget_2026.xlsx"
    unrelated.write_text("nothing to see", encoding="utf-8")
    sniffed = adapter.sniff([unrelated])
    assert sniffed.confidence == 0.0
    assert "no file name matches" in sniffed.reason


def test_sniff_on_names_alone_is_less_confident_than_on_corroborated_content(adapter, tmp_path):
    named_only = tmp_path / "timings.csv"
    named_only.write_text("A,B\n1,2\n", encoding="utf-8")
    assert adapter.sniff([named_only]).confidence < adapter.sniff(_write_fixture(tmp_path)).confidence


# ---------------------------------------------------------------------------
# 3. End-to-end mapping of the synthetic fixture
# ---------------------------------------------------------------------------


def test_the_full_fixture_parses_into_neutral_objects_without_refusing(adapter, files):
    result = adapter.parse(files)
    assert result.ok, result.issue_codes(IssueSeverity.REFUSE)
    assert result.adapter.name == "scats_wa"
    assert len(result.sites) == 1 and len(result.controllers) == 1


def test_site_tab_maps_to_site_metadata_and_controller_identity(adapter, files):
    result = adapter.parse(files)
    site, controller = result.sites[0], result.controllers[0]
    assert site.site_id == "359" and site.source_system == "SCATS"
    assert site.site_name == "Test Road / Example Street"
    assert site.drive_side is DriveSide.LEFT  # adapter-declared regional fact
    assert site.coordinate_reference is None
    assert "coordinate_reference" in site.absent
    assert controller.controller_id == "359"
    assert controller.control_type is ControlType.ADAPTIVE_SCATS
    assert controller.control_type_raw == "SCATS"
    assert controller.cycle_time_s is None and "cycle_time_s" in controller.absent


def test_timings_tab_maps_to_signal_groups_and_a_declared_program(adapter, files):
    controller = adapter.parse(files).controllers[0]
    group = next(g for g in controller.signal_groups if g.signal_group_id == "2")
    assert (group.late_start_s, group.early_cut_off_s, group.min_green_s) == (3.0, 7.0, 8.0)
    assert group.phase_ids == ["B"]

    declared = controller.program_by_id("declared_timings")
    assert declared.source_kind is ProgramSourceKind.DECLARED
    phase_a = declared.phase_by_id("A")
    assert (phase_a.amber_s, phase_a.all_red_s) == (4.0, 2.0)
    assert (phase_a.min_green_s, phase_a.max_green_s) == (10.0, 60.0)
    # Green is adaptive on a SCATS site: absent, with the reason recorded.
    assert phase_a.green_s is None and "green_s" in phase_a.absent


def test_pedestrian_timings_and_protection_type_map(adapter, files):
    controller = adapter.parse(files).controllers[0]
    walk = controller.pedestrian_phases[0]
    assert walk.pedestrian_phase_id == "1"
    assert (walk.walk_s, walk.clearance_1_s, walk.delay_s) == (6.0, 12.0, 2.0)
    assert walk.protection_type is PedestrianProtectionType.EXCLUSIVE_PHASE
    assert walk.protection_type_raw == "exclusive phase"
    # A.3's WA clearance-2 rule is NOT applied at import time.
    assert walk.clearance_2_s is None and "clearance_2_s" in walk.absent


def test_link_and_offset_plans_map_to_cycle_bounds_and_coordination(adapter, files):
    controller = adapter.parse(files).controllers[0]
    assert (controller.cycle_min_s, controller.cycle_max_s) == (60.0, 140.0)
    assert controller.coordination.subsystem == "SS1"

    plan_1 = controller.program_by_id("plan_1")
    assert plan_1.coordination.coordination_point is CoordinationPoint.START_OF_PHASE
    assert plan_1.coordination.coordinated_phase_id == "A"
    assert plan_1.coordination.offsets_s == [10.0]
    assert plan_1.coordination.plan_number == 1

    plan_3 = controller.program_by_id("plan_3")
    assert plan_3.coordination.coordination_point is CoordinationPoint.END_OF_PHASE
    assert plan_3.coordination.offsets_s == [20.0, 30.0]
    assert plan_3.phases == [] and "phases" in plan_3.absent


def test_phase_history_maps_to_an_observed_program_with_a2_derivations(adapter, files):
    controller = adapter.parse(files).controllers[0]
    observed = controller.program_by_id("observed_history")
    assert observed.source_kind is ProgramSourceKind.OBSERVED_HISTORY
    assert observed.observed_cycle_count == 4
    assert observed.cycle_time_s == pytest.approx(100.0)  # A.2: 400 s / 4 cycles
    assert observed.observation_window.duration_s == 400.0

    phase_a = observed.phase_by_id("A")
    assert phase_a.is_stretch_phase is True
    assert phase_a.observation_count == 4
    assert phase_a.observed_total_duration_s == 240.0
    assert phase_a.frequency == pytest.approx(1.0)
    assert observed.phase_by_id("B").frequency == pytest.approx(0.75)
    assert observed.phase_by_id("C").frequency == pytest.approx(0.5)


def test_a_phase_history_duration_is_never_recorded_as_green(adapter, files):
    """A.2: a Phase History duration is green PLUS its intergreen. Writing it
    into green_s would be a wrong number nobody would notice."""
    observed = adapter.parse(files).controllers[0].program_by_id("observed_history")
    for phase in observed.phases:
        assert phase.green_s is None
        assert "green_s" in phase.absent


def test_derived_values_carry_a_derived_by_citation(adapter, files):
    observed = adapter.parse(files).controllers[0].program_by_id("observed_history")
    assert "A.2" in observed.provenance.derived_by
    assert "A.2" in observed.phase_by_id("A").provenance.derived_by
    # A value that was READ instead points at its row.
    declared = adapter.parse(files).controllers[0].program_by_id("declared_timings")
    assert declared.phase_by_id("A").provenance.locator.startswith("row ")
    assert declared.phase_by_id("A").provenance.derived_by is None


def test_event_history_maps_signal_group_greens_without_inventing_cycle_positions(adapter, files):
    observed = adapter.parse(files).controllers[0].program_by_id("observed_history")
    window = next(w for w in observed.signal_group_windows if w.signal_group_id == "1")
    assert window.green_duration_s == pytest.approx(32.0)  # mean of 30 s and 34 s
    assert window.observation_count == 2
    assert window.start_s is None and "start_s" in window.absent
    assert "stretch-phase timeline" in window.absent["start_s"]


def test_walk_events_map_to_demand_frequency_per_appendix_a_p11(adapter, files):
    controller = adapter.parse(files).controllers[0]
    walk = next(p for p in controller.pedestrian_phases if p.pedestrian_phase_id == "1")
    assert walk.demand_frequency == pytest.approx(0.5)  # 2 demands met / 4 cycles


def test_detector_volumes_map_to_detector_counts_and_detector_records(adapter, files):
    result = adapter.parse(files)
    assert len(result.detector_counts) == 4
    assert result.counts == []  # SCATS supplies no turning counts
    record = result.detector_counts[0]
    assert (record.detector_id, record.volume, record.interval_minutes) == ("D1", 100.0, 15.0)
    assert record.vehicle_class is None and "vehicle_class" in record.absent
    detector = result.controllers[0].detectors[0]
    assert detector.controller_id == "359"
    assert detector.function is None and "LMA drawing" in detector.absent["function"]
    assert "scats_no_turning_counts_in_source" in _codes(result, IssueSeverity.INFO)


def test_every_parsed_record_can_name_the_file_it_came_from(adapter, files):
    result = adapter.parse(files)
    controller = result.controllers[0]
    records = (
        [controller, *controller.signal_groups, *controller.programs, *controller.detectors]
        + list(controller.pedestrian_phases)
        + result.detector_counts
        + [phase for program in controller.programs for phase in program.phases]
    )
    for record in records:
        assert record.provenance.source_file.endswith(".csv")
        assert (
            record.provenance.locator
            or record.provenance.native_id
            or record.provenance.derived_by
        )


# ---------------------------------------------------------------------------
# 4. The never-invent rule, proven one missing input at a time
# ---------------------------------------------------------------------------


def test_without_a_site_tab_the_adapter_refuses_instead_of_naming_the_site_itself(
    adapter, tmp_path
):
    files = _write_fixture(tmp_path, omit=("site.csv",))
    result = adapter.parse(files)  # returns; does not raise
    assert result.ok is False
    assert _codes(result, IssueSeverity.REFUSE) == ["scats_site_identity_unavailable"]
    assert result.controllers == [] and result.sites == []
    assert "will not derive a site id from a file name" in result.refusals[0].message


def test_an_empty_site_tab_refuses_rather_than_inventing_a_tcs_number(adapter, tmp_path):
    files = _write_fixture(tmp_path, **{"site.csv": "TCS,Name,Region\n"})
    result = adapter.parse(files)
    assert _codes(result, IssueSeverity.REFUSE) == ["scats_tcs_number_missing"]


def test_without_a_timings_tab_yellow_and_all_red_stay_unknown(adapter, tmp_path):
    """A.3's yellow/all-red formulae need approach speed, grade and clearance
    distance. None of those are in these files, so the adapter must not
    compute them — it says so instead."""
    files = _write_fixture(tmp_path, omit=("timings.csv",))
    result = adapter.parse(files)
    assert "intergreen_split_unavailable" in _codes(result, IssueSeverity.WARN)

    observed = result.controllers[0].program_by_id("observed_history")
    for phase in observed.phases:
        assert phase.amber_s is None and phase.all_red_s is None
        assert "approach speed" in phase.absent["amber_s"]
        assert "clearance distance" in phase.absent["all_red_s"]


def test_without_phase_history_ped_demand_frequency_is_reported_not_estimated(adapter, tmp_path):
    """A.2 p.11's frequency is demands-met ÷ complete cycles. With no Phase
    History there is no cycle count, so there is no frequency — and the
    adapter must not substitute one."""
    files = _write_fixture(tmp_path, omit=("phase_history.csv",))
    result = adapter.parse(files)
    assert "ped_demand_frequency_not_computable" in _codes(result, IssueSeverity.WARN)
    walk = next(
        p for p in result.controllers[0].pedestrian_phases if p.pedestrian_phase_id == "1"
    )
    assert walk.demand_frequency is None
    assert "complete-cycle count" in walk.absent["demand_frequency"]


def test_an_ambiguous_stretch_phase_blocks_every_derivation_that_depends_on_it(
    adapter, tmp_path
):
    """A.2's cycle count comes from the stretch phase. If two phases run
    equally often, the stretch phase is not identifiable, so cycle count,
    frequency and average cycle are all left unset."""
    tied = "Date,Phase,Duration,Start,End\n"
    clock = datetime(2026, 3, 4, 8, 0)
    for phase in ("A", "B", "A", "B"):
        end = clock + timedelta(seconds=50)
        tied += f"2026-03-04,{phase},50,{clock.isoformat()},{end.isoformat()}\n"
        clock = end
    files = _write_fixture(tmp_path, **{"phase_history.csv": tied})
    result = adapter.parse(files)

    assert "stretch_phase_ambiguous" in _codes(result, IssueSeverity.WARN)
    observed = result.controllers[0].program_by_id("observed_history")
    assert observed.observed_cycle_count is None
    assert observed.cycle_time_s is None and "cycle_time_s" in observed.absent
    for phase in observed.phases:
        assert phase.frequency is None and "frequency" in phase.absent
        assert phase.is_stretch_phase is None


def test_conflicting_timings_values_are_left_unset_rather_than_picked(adapter, tmp_path):
    conflicting = (
        "SG,Phase,LateStart,EarlyCutOff,MinGreen,MaxGreen,Yellow,AllRed\n"
        "1,A,0,0,10,60,4,2\n"
        "2,A,0,0,10,60,5,2\n"
    )
    files = _write_fixture(tmp_path, **{"timings.csv": conflicting})
    result = adapter.parse(files)
    assert "timings_value_conflict" in _codes(result, IssueSeverity.WARN)
    phase = result.controllers[0].program_by_id("declared_timings").phase_by_id("A")
    assert phase.amber_s is None
    assert "will not choose between them" in phase.absent["amber_s"]


def test_an_unmappable_protection_type_keeps_the_raw_token(adapter, tmp_path):
    exotic = TIMINGS_CSV.replace("exclusive phase", '"scramble crossing, local variant"')
    files = _write_fixture(tmp_path, **{"timings.csv": exotic})
    result = adapter.parse(files)
    assert "pedestrian_protection_type_unmapped" in _codes(result, IssueSeverity.INFO)
    walk = result.controllers[0].pedestrian_phases[0]
    assert walk.protection_type is None
    assert walk.protection_type_raw == "scramble crossing, local variant"


def test_a_ragged_row_is_survived_rather_than_raised(adapter, tmp_path):
    """Hand-maintained survey files have short and over-long rows. A parse that
    crashed on one would be an exception standing in for a data finding."""
    ragged = SITE_CSV + "360,Extra Site,Metro,unexpected,extra,columns\n"
    files = _write_fixture(tmp_path, **{"site.csv": ragged})
    result = adapter.parse(files)  # must not raise
    assert result.sites[0].site_id == "359"


def test_contradictory_cycle_bounds_refuse_rather_than_being_reordered(adapter, tmp_path):
    bad = "Plan,Subsystem,CoordinatedPhase,CycleMin,CycleMax,Offsets\n1,SS1,^A,200,120,10\n"
    files = _write_fixture(tmp_path, **{"link_offset_plans.csv": bad})
    result = adapter.parse(files)
    assert "cycle_bounds_contradictory" in _codes(result, IssueSeverity.REFUSE)
    controller = result.controllers[0]
    assert controller.cycle_min_s is None and controller.cycle_max_s is None


def test_a_conflicting_subsystem_is_left_unset(adapter, tmp_path):
    two = (
        "Plan,Subsystem,CoordinatedPhase,CycleMin,CycleMax,Offsets\n"
        "1,SS1,^A,60,120,10\n"
        "3,SS2,B,60,140,20\n"
    )
    files = _write_fixture(tmp_path, **{"link_offset_plans.csv": two})
    result = adapter.parse(files)
    assert "multiple_subsystems_declared" in _codes(result, IssueSeverity.WARN)
    assert result.controllers[0].coordination is None


def test_unusable_rows_are_reported_and_skipped_not_defaulted(adapter, tmp_path):
    broken = "Date,Phase,Duration,Start,End\n2026-03-04,,,,\n" + _phase_history_csv().split("\n", 1)[1]
    files = _write_fixture(tmp_path, **{"phase_history.csv": broken})
    result = adapter.parse(files)
    assert "phase_history_row_unusable" in _codes(result, IssueSeverity.WARN)
    assert result.controllers[0].program_by_id("observed_history").observed_cycle_count == 4


def test_the_stub_says_the_fixed_time_conversion_is_not_its_job(adapter, files):
    result = adapter.parse(files)
    info = next(i for i in result.issues if i.code == "fixed_time_conversion_deferred")
    assert "roadmap 1.6" in info.message


# ---------------------------------------------------------------------------
# 5. Adapter output through the quality gate
# ---------------------------------------------------------------------------


def test_the_parsed_fixture_passes_the_quality_gate(adapter, files):
    result = apply_quality_gate(adapter.parse(files))
    assert result.ok, result.issue_codes(IssueSeverity.REFUSE)
    assert "no_count_data" in result.issue_codes(IssueSeverity.INFO)
    assert result.issue_codes(IssueSeverity.WARN) == []


def test_the_gate_catches_a_double_counted_detector_interval(adapter, tmp_path):
    doubled = _detector_csv() + "D1,2026-03-04T08:05:00,2026-03-04T08:20:00,55\n"
    files = _write_fixture(tmp_path, **{"detector_volumes.csv": doubled})
    result = apply_quality_gate(adapter.parse(files))
    assert "detector_count_intervals_overlap" in result.issue_codes(IssueSeverity.REFUSE)


def test_all_six_appendix_a_protection_types_are_representable():
    assert set(PROTECTION_TYPE_MAP.values()) == set(PedestrianProtectionType)

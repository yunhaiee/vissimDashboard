"""Tests for the SCATS/WA adapter's REAL ``.xlsx`` reader (the workbook-shaped
Site + Hourly/Quarter Hourly Volume path, as opposed to
``test_dataimport_scats_wa.py``'s hand-authored CSV-subset stub path).

**Every fixture here is synthetic**, built with ``openpyxl`` to reproduce the
*shape* measured against the 3 real WA SCATS workbooks in
``client-data/Traffic signal data.zip`` (never committed, never read by a
test, never copied into this file): the Site tab's key/value rows, the
'Detector' group-label row, the ``Day, Date, Hour|Qtr Hour, <detector
numbers>`` header, and the row-per-(day, interval) data grid. Site ids, road
names and every volume figure below are invented for this test and share no
value with the real drop — they are chosen to make the arithmetic legible,
not to resemble anything counted at Murdoch.

What this file proves, in order:

1. the happy path: Site tab -> SiteMetadata (including the fields the CSV
   subset never carried: road, road name, SLK, lat/lon) and Quarter Hourly
   Volume -> DetectorCountData, with the hand-derived sums stated in the test;
2. refusal: no Site tab, and a Site tab with no 'Traffic Signal' value —
   never a site id derived from the file name;
3. blank detector cells produce no record, not a zero;
4. the Hourly <-> Quarter Hourly cross-check: corroboration is an INFO, a
   disagreement is a REFUSE, and only the finer (quarter-hourly) tab is ever
   emitted as records — proven by an overlap that would otherwise occur;
5. degraded-input handling: only Hourly Volume present (emitted directly),
   only Quarter Hourly Volume present (no cross-check possible), neither tab
   present (WARN, zero records);
6. sniff() recognises the real shape at high confidence;
7. the detector -> movement mapping helper: refuses (by naming exactly which
   (site, detector) pairs) when the caller supplies no map, and lifts records
   correctly when the caller does.

Offline by construction: openpyxl and the neutral model, no Vissim, no COM.
"""
from __future__ import annotations

import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from services.dataimport.adapters.scats_wa import (
    DetectorMovement,
    HOURLY_VOLUME_SHEET,
    QUARTER_HOURLY_VOLUME_SHEET,
    SCATSWAAdapter,
    SITE_SHEET,
    build_turning_counts_from_detector_map,
)
from services.dataimport.base import AdapterError, IssueSeverity
from services.dataimport.quality import apply_quality_gate
from services.dataimport.schema import ApproachRef, MovementRef, TurnType

# ---------------------------------------------------------------------------
# Synthetic fixture builder — reproduces the REAL WORKBOOK SHAPE, invented data
# ---------------------------------------------------------------------------

SITE_ID = "T00001"
ROAD_REF = "X999"
ROAD_NAME = "Fixture Ave"
SLK = "3.45"
LATITUDE = "-31.500000"
LONGITUDE = "115.900000"
INTERSECTION = "Fixture Ave & Sample St"

#: Synthetic Monday, deliberately a different month/year shape than the real
#: February 2026 drop so nobody could mistake this for real data at a glance.
DAY0 = datetime(2025, 6, 2)

#: Quarter-hour volumes for 08:00-09:00, two detectors. Chosen so the hourly
#: total is easy to hand-check: D1 sums to 40, D2 sums to 60.
QUARTER_D1 = [10, 10, 10, 10]
QUARTER_D2 = [15, 15, 15, 15]
HOURLY_D1 = 40
HOURLY_D2 = 60
QUARTER_TIMES = ["08:00", "08:15", "08:30", "08:45"]


def _site_rows(
    *,
    site_id: str = SITE_ID,
    road_ref: str | None = ROAD_REF,
    road_name: str | None = ROAD_NAME,
    slk: str | None = SLK,
    latitude: str | None = LATITUDE,
    longitude: str | None = LONGITUDE,
    intersection: str | None = INTERSECTION,
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = [("Traffic Signal Data",)]
    if site_id is not None:
        rows.append(("Traffic Signal", site_id))
    if road_ref is not None:
        rows.append(("Road", road_ref))
    if road_name is not None:
        rows.append(("Road Name", road_name))
    if slk is not None:
        rows.append(("SLK", slk))
    if latitude is not None:
        rows.append(("Latitude", latitude))
    if longitude is not None:
        rows.append(("Longitude", longitude))
    if intersection is not None:
        rows.append(("Intersection", intersection))
    return rows


def _write_workbook(
    path: Path,
    *,
    site_rows: list[tuple[Any, ...]] | None = _site_rows(),
    quarter_grid: dict[str, list[Any]] | None = None,
    hourly_grid: dict[str, list[Any]] | None = None,
    extra_sheets: tuple[str, ...] = (),
) -> Path:
    """One synthetic workbook. ``*_grid`` is ``{"HH:MM": [detector1, detector2, ...]}``
    for the single synthetic day ``DAY0``; ``None`` entries are blank cells.
    ``site_rows=None`` omits the Site tab entirely (the no-Site-tab refusal case).
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    if site_rows is not None:
        ws = wb.create_sheet(SITE_SHEET)
        for row in site_rows:
            ws.append(list(row))

    def _write_volume_sheet(sheet_name: str, interval_label: str, grid: dict[str, list[Any]]) -> None:
        ws = wb.create_sheet(sheet_name)
        ws.append(["Traffic Signal Vehicle Counts"])
        ws.append(["Traffic Signal", SITE_ID])
        ws.append(["From", DAY0])
        ws.append(["To", DAY0])
        n_det = len(next(iter(grid.values()))) if grid else 2
        ws.append([None, None, None, "Detector"] + [None] * max(0, n_det - 1))
        ws.append(["Day", "Date", interval_label] + [i + 1 for i in range(n_det)])
        for clock, values in grid.items():
            ws.append([DAY0.strftime("%A"), DAY0, clock] + list(values))

    if quarter_grid is not None:
        _write_volume_sheet(QUARTER_HOURLY_VOLUME_SHEET, "Qtr Hour", quarter_grid)
    if hourly_grid is not None:
        _write_volume_sheet(HOURLY_VOLUME_SHEET, "Hour", hourly_grid)
    for name in extra_sheets:
        wb.create_sheet(name).append(["placeholder"])

    wb.save(path)
    return path


def _default_quarter_grid() -> dict[str, list[Any]]:
    return {
        clock: [QUARTER_D1[i], QUARTER_D2[i]] for i, clock in enumerate(QUARTER_TIMES)
    }


def _default_hourly_grid() -> dict[str, list[Any]]:
    return {"08:00": [HOURLY_D1, HOURLY_D2]}


@pytest.fixture()
def adapter() -> SCATSWAAdapter:
    return SCATSWAAdapter()


def _codes(result, severity=None) -> list[str]:
    return result.issue_codes(severity)


# ---------------------------------------------------------------------------
# 1. The happy path
# ---------------------------------------------------------------------------


def test_the_full_synthetic_workbook_parses_without_refusing(adapter, tmp_path):
    path = _write_workbook(
        tmp_path / "fixture.xlsx",
        quarter_grid=_default_quarter_grid(),
        hourly_grid=_default_hourly_grid(),
    )
    result = adapter.parse([path])
    assert result.ok, result.issue_codes(IssueSeverity.REFUSE)
    assert len(result.sites) == 1 and len(result.controllers) == 1


def test_site_tab_maps_to_the_fields_the_csv_subset_never_carried(adapter, tmp_path):
    path = _write_workbook(tmp_path / "fixture.xlsx", quarter_grid=_default_quarter_grid())
    result = adapter.parse([path])
    site = result.sites[0]

    assert site.site_id == SITE_ID
    assert site.site_name == INTERSECTION
    assert site.road_ref == ROAD_REF
    assert site.road_name == ROAD_NAME
    assert site.stationing_km == pytest.approx(3.45)
    assert site.position is not None
    assert site.position.y == pytest.approx(-31.5)  # y = latitude
    assert site.position.x == pytest.approx(115.9)  # x = longitude
    # Never asserted without the workbook stating a CRS name.
    assert site.coordinate_reference is None
    assert "coordinate_reference" in site.absent

    controller = result.controllers[0]
    assert controller.controller_id == SITE_ID
    assert controller.control_type_raw == "SCATS"
    assert controller.cycle_time_s is None and "cycle_time_s" in controller.absent


def test_quarter_hourly_volume_maps_to_detector_counts_with_hand_derived_sums(adapter, tmp_path):
    path = _write_workbook(tmp_path / "fixture.xlsx", quarter_grid=_default_quarter_grid())
    result = adapter.parse([path])

    assert result.counts == []  # SCATS supplies no turning counts, ever
    assert len(result.detector_counts) == 8  # 2 detectors x 4 quarter-hour bins
    by_detector: dict[str, float] = {}
    for record in result.detector_counts:
        by_detector[record.detector_id] = by_detector.get(record.detector_id, 0.0) + record.volume
    assert by_detector == {"1": pytest.approx(40.0), "2": pytest.approx(60.0)}

    one = result.detector_counts[0]
    assert one.site_id == SITE_ID
    assert one.controller_id == SITE_ID
    assert one.volume_unit.value == "vehicles"
    assert one.vehicle_class is None and "vehicle_class" in one.absent
    assert one.interval_minutes == 15.0

    detectors = {d.detector_id for d in result.controllers[0].detectors}
    assert detectors == {"1", "2"}
    detector = result.controllers[0].detectors[0]
    assert detector.function is None and "LMA drawing" in detector.absent["function"]

    assert "scats_no_turning_counts_in_source" in _codes(result, IssueSeverity.INFO)


# ---------------------------------------------------------------------------
# 2. Refusal — never a site id from the file name
# ---------------------------------------------------------------------------


def test_a_workbook_with_no_site_tab_refuses_instead_of_naming_the_site_from_the_filename(
    adapter, tmp_path
):
    path = _write_workbook(
        tmp_path / "LM99999-looks-real.xlsx",
        site_rows=None,
        quarter_grid=_default_quarter_grid(),
    )
    result = adapter.parse([path])  # must return, not raise
    assert result.ok is False
    assert _codes(result, IssueSeverity.REFUSE) == ["scats_site_identity_unavailable"]
    assert result.sites == [] and result.controllers == []
    assert "will not derive a site id from the file name" in result.refusals[0].message


def test_a_site_tab_with_no_traffic_signal_value_refuses_rather_than_inventing_one(
    adapter, tmp_path
):
    path = _write_workbook(
        tmp_path / "fixture.xlsx",
        site_rows=_site_rows(site_id=None),
        quarter_grid=_default_quarter_grid(),
    )
    result = adapter.parse([path])
    assert _codes(result, IssueSeverity.REFUSE) == ["scats_tcs_number_missing"]
    assert result.sites == []


def test_missing_optional_site_fields_are_marked_absent_not_defaulted(adapter, tmp_path):
    path = _write_workbook(
        tmp_path / "fixture.xlsx",
        site_rows=_site_rows(
            road_ref=None, road_name=None, slk=None, latitude=None, longitude=None,
            intersection=None,
        ),
        quarter_grid=_default_quarter_grid(),
    )
    result = adapter.parse([path])
    site = result.sites[0]
    assert site.road_ref is None and "road_ref" in site.absent
    assert site.road_name is None and "road_name" in site.absent
    assert site.stationing_km is None and "stationing_km" in site.absent
    assert site.position is None and "position" in site.absent
    # site_name falls back to road_name when Intersection is missing too, and
    # both are absent here — the falsy fallback chain must not fabricate one.
    assert site.site_name is None


def test_site_name_falls_back_to_road_name_when_intersection_is_absent(adapter, tmp_path):
    path = _write_workbook(
        tmp_path / "fixture.xlsx",
        site_rows=_site_rows(intersection=None),
        quarter_grid=_default_quarter_grid(),
    )
    result = adapter.parse([path])
    assert result.sites[0].site_name == ROAD_NAME


# ---------------------------------------------------------------------------
# 3. Blank detector cells produce no record, never a zero
# ---------------------------------------------------------------------------


def test_a_blank_detector_cell_produces_no_record_and_is_reported(adapter, tmp_path):
    grid = _default_quarter_grid()
    grid["08:15"] = [QUARTER_D1[1], None]  # D2 blank at 08:15
    path = _write_workbook(tmp_path / "fixture.xlsx", quarter_grid=grid)
    result = adapter.parse([path])

    assert len(result.detector_counts) == 7  # 8 - 1 blank cell
    d2_starts = {r.interval_start for r in result.detector_counts if r.detector_id == "2"}
    assert datetime(2025, 6, 2, 8, 15) not in d2_starts

    blank_issue = next(i for i in result.issues if i.code == "scats_xlsx_volume_blank_cells")
    assert blank_issue.context["blank_cells"] == 1

    gated = apply_quality_gate(result)
    assert "detector_count_interval_gaps" in _codes(gated, IssueSeverity.WARN)


# ---------------------------------------------------------------------------
# 4. Hourly <-> Quarter Hourly cross-check + the no-double-count guarantee
# ---------------------------------------------------------------------------


def test_hourly_corroborates_quarter_hourly_when_both_agree(adapter, tmp_path):
    path = _write_workbook(
        tmp_path / "fixture.xlsx",
        quarter_grid=_default_quarter_grid(),
        hourly_grid=_default_hourly_grid(),
    )
    result = adapter.parse([path])
    assert result.ok, result.issue_codes(IssueSeverity.REFUSE)
    assert "scats_xlsx_hourly_corroborates_quarter_hourly" in _codes(result, IssueSeverity.INFO)

    # THE critical guarantee: only the quarter-hourly (finer) records are
    # emitted. If the hourly rows were ALSO emitted, this site/detector would
    # carry two different interval lengths for overlapping spans and the gate
    # would (correctly) refuse it — proven below, not merely asserted.
    assert len(result.detector_counts) == 8
    assert {r.interval_minutes for r in result.detector_counts} == {15.0}

    gated = apply_quality_gate(result)
    assert "detector_count_intervals_overlap" not in gated.issue_codes(IssueSeverity.REFUSE)


def test_a_hourly_quarter_hourly_disagreement_refuses_the_whole_workbook(adapter, tmp_path):
    hourly = _default_hourly_grid()
    hourly["08:00"] = [HOURLY_D1 + 1, HOURLY_D2]  # D1 now disagrees with its 4 quarter bins
    path = _write_workbook(
        tmp_path / "fixture.xlsx",
        quarter_grid=_default_quarter_grid(),
        hourly_grid=hourly,
    )
    result = adapter.parse([path])
    assert "scats_xlsx_hourly_disagrees_with_quarter_hourly" in _codes(result, IssueSeverity.REFUSE)
    mismatch = next(
        i for i in result.issues if i.code == "scats_xlsx_hourly_disagrees_with_quarter_hourly"
    )
    assert mismatch.context["total_mismatches"] == 1
    # Still only the quarter-hourly records were emitted — a REFUSE blocks
    # STORAGE downstream (observations.ingest_observation_set), it does not
    # change what this parse produced.
    assert len(result.detector_counts) == 8


# ---------------------------------------------------------------------------
# 5. Degraded input: one volume tab, or neither
# ---------------------------------------------------------------------------


def test_hourly_only_is_emitted_directly_when_quarter_hourly_is_absent(adapter, tmp_path):
    path = _write_workbook(tmp_path / "fixture.xlsx", hourly_grid=_default_hourly_grid())
    result = adapter.parse([path])
    assert result.ok, result.issue_codes(IssueSeverity.REFUSE)
    assert len(result.detector_counts) == 2  # 2 detectors x 1 hourly bin
    assert {r.interval_minutes for r in result.detector_counts} == {60.0}
    assert "scats_xlsx_hourly_volume_used_no_quarter_hourly" in _codes(result, IssueSeverity.INFO)


def test_quarter_hourly_only_cannot_be_cross_checked(adapter, tmp_path):
    path = _write_workbook(tmp_path / "fixture.xlsx", quarter_grid=_default_quarter_grid())
    result = adapter.parse([path])
    assert result.ok, result.issue_codes(IssueSeverity.REFUSE)
    assert len(result.detector_counts) == 8
    assert "scats_xlsx_hourly_volume_tab_absent" in _codes(result, IssueSeverity.INFO)


def test_neither_volume_tab_present_yields_zero_records_not_a_crash(adapter, tmp_path):
    path = _write_workbook(tmp_path / "fixture.xlsx", quarter_grid=None, hourly_grid=None)
    result = adapter.parse([path])
    assert result.ok  # a missing volume tab is a WARN, not a REFUSE
    assert result.detector_counts == []
    assert "scats_xlsx_no_volume_tab" in _codes(result, IssueSeverity.WARN)


def test_unparsed_real_sheets_are_named_not_silently_dropped(adapter, tmp_path):
    path = _write_workbook(
        tmp_path / "fixture.xlsx",
        quarter_grid=_default_quarter_grid(),
        extra_sheets=("Timings", "SCATS Phase History"),
    )
    result = adapter.parse([path])
    info = next(i for i in result.issues if i.code == "scats_xlsx_sheet_present_not_parsed")
    assert set(info.context["sheets_not_parsed"]) == {"Timings", "SCATS Phase History"}


# ---------------------------------------------------------------------------
# 5a. The header row is found STRUCTURALLY, never trusted at a fixed index
# ---------------------------------------------------------------------------
#
# Finding [0]: the volume tabs' header/data rows used to be hardcoded 0-indexed
# constants with no check that the row they pointed at actually looked like a
# header. REPRODUCED against a synthetic workbook with ONE extra metadata row
# (an ordinary export change): the OLD code read the 'Detector' group-label
# row as if it were the header, fabricated a single detector literally named
# 'Detector', silently dropped the real header row (and every real detector's
# column) as a generic "row unusable" WARN, and reported result.ok == True —
# a clean-looking one-detector import built from data that was actually two
# detectors. These tests build that shift (and its cousins) directly with
# openpyxl rather than going through ``_write_workbook``, because the point is
# to control exactly which row holds what.


def _write_volume_workbook_raw(
    path: Path, header_rows: list[list[Any]], data_rows: list[list[Any]]
) -> Path:
    """A ``Site`` tab (the fixture default) plus ONE ``Quarter Hourly Volume``
    tab whose rows are exactly ``header_rows + data_rows`` — full manual
    control of row position, used to prove the header is found by CONTENT
    rather than trusted at ``_VOLUME_HEADER_ROW``."""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    ws_site = wb.create_sheet(SITE_SHEET)
    for row in _site_rows():
        ws_site.append(list(row))

    ws = wb.create_sheet(QUARTER_HOURLY_VOLUME_SHEET)
    for row in header_rows + data_rows:
        ws.append(row)

    wb.save(path)
    return path


def test_a_shifted_header_row_is_found_structurally_not_lost(adapter, tmp_path):
    """ONE extra metadata row (an ordinary export change) pushes the real
    header from its nominal row 5 down to row 6. The adapter must still find
    it — by content, not position — and read BOTH real detectors correctly,
    never fabricate a single detector named 'Detector' from the group-label
    row the old fixed-index code would have misread as the header.
    """
    path = _write_volume_workbook_raw(
        tmp_path / "shifted.xlsx",
        header_rows=[
            ["Traffic Signal Vehicle Counts"],
            ["Traffic Signal", SITE_ID],
            ["From", DAY0],
            ["To", DAY0],
            ["Collected under revised export template v7"],  # the extra row
            [None, None, None, "Detector", None],  # group label, now row 6
            ["Day", "Date", "Qtr Hour", 1, 2],  # the real header, now row 7
        ],
        data_rows=[
            [DAY0.strftime("%A"), DAY0, clock, *values]
            for clock, values in _default_quarter_grid().items()
        ],
    )

    result = adapter.parse([path])
    assert result.ok, result.issue_codes(IssueSeverity.REFUSE)

    detectors = {d.detector_id for d in result.controllers[0].detectors}
    assert detectors == {"1", "2"}  # NOT a fabricated single {"Detector"}

    by_detector: dict[str, float] = {}
    for record in result.detector_counts:
        by_detector[record.detector_id] = by_detector.get(record.detector_id, 0.0) + record.volume
    assert by_detector == {"1": pytest.approx(40.0), "2": pytest.approx(60.0)}


def test_no_recognisable_header_in_the_search_window_refuses_by_name(adapter, tmp_path):
    """A tab that never carries the 'Day, Date, Hour|Qtr Hour' shape at all —
    a corrupted export, or simply not this layout — must REFUSE naming the
    problem, not silently emit zero volumes or guess from position."""
    path = _write_volume_workbook_raw(
        tmp_path / "no_header.xlsx",
        header_rows=[
            ["Traffic Signal Vehicle Counts"],
            ["Traffic Signal", SITE_ID],
            ["From", DAY0],
            ["To", DAY0],
        ],
        # 25 rows, none of which is remotely the expected shape -- exceeds
        # the search window on purpose.
        data_rows=[["not", "a", "header", "row"] for _ in range(25)],
    )

    result = adapter.parse([path])
    assert result.ok is False
    assert "scats_xlsx_volume_header_not_found" in _codes(result, IssueSeverity.REFUSE)
    assert result.detector_counts == []


def test_an_implausible_detector_id_in_the_header_refuses_the_tab(adapter, tmp_path):
    """The header row itself is found correctly (Day/Date/Qtr Hour all
    present), but one of its detector-column cells reads 'Detector' — the
    exact corrupted-header value the [0] finding's repro produced — instead
    of a bare number. A cell that does not look like a detector id must not
    be accepted as one."""
    path = _write_volume_workbook_raw(
        tmp_path / "bad_detector_id.xlsx",
        header_rows=[
            ["Traffic Signal Vehicle Counts"],
            ["Traffic Signal", SITE_ID],
            ["From", DAY0],
            ["To", DAY0],
            [None, None, None, "Detector"],
            ["Day", "Date", "Qtr Hour", "Detector", 2],  # column 3 is not a number
        ],
        data_rows=[
            [DAY0.strftime("%A"), DAY0, clock, *values]
            for clock, values in _default_quarter_grid().items()
        ],
    )

    result = adapter.parse([path])
    assert result.ok is False
    assert "scats_xlsx_volume_implausible_detector_id" in _codes(result, IssueSeverity.REFUSE)
    assert result.detector_counts == []


# ---------------------------------------------------------------------------
# 6. sniff()
# ---------------------------------------------------------------------------


def test_sniff_recognises_the_real_workbook_shape_at_high_confidence(adapter, tmp_path):
    path = _write_workbook(tmp_path / "fixture.xlsx", quarter_grid=_default_quarter_grid())
    sniffed = adapter.sniff([path])
    assert sniffed.confidence == 1.0
    assert path.name in sniffed.matched_files


def test_sniff_scores_a_site_only_workbook_below_the_selection_threshold(adapter, tmp_path):
    path = _write_workbook(tmp_path / "fixture.xlsx", quarter_grid=None, hourly_grid=None)
    sniffed = adapter.sniff([path])
    assert sniffed.confidence < 0.5


def test_sniff_never_raises_on_a_corrupt_xlsx_extension(adapter, tmp_path):
    fake = tmp_path / "not_really.xlsx"
    fake.write_text("nothing to see", encoding="utf-8")
    sniffed = adapter.sniff([fake])
    assert sniffed.confidence == 0.0


def test_parse_raises_adapter_error_for_genuinely_corrupt_bytes(adapter, tmp_path):
    fake = tmp_path / "not_really.xlsx"
    fake.write_text("nothing to see", encoding="utf-8")
    with pytest.raises(AdapterError):
        adapter.parse([fake])


# ---------------------------------------------------------------------------
# 7. The detector -> movement mapping helper
# ---------------------------------------------------------------------------


def _parsed_detector_counts(tmp_path: Path) -> list:
    path = _write_workbook(tmp_path / "fixture.xlsx", quarter_grid=_default_quarter_grid())
    return SCATSWAAdapter().parse([path]).detector_counts


def test_no_supplied_map_refuses_every_detector_by_name(tmp_path):
    detector_counts = _parsed_detector_counts(tmp_path)
    lifted, issues = build_turning_counts_from_detector_map(
        detector_counts, detector_movements={}, mapping_source="test: no map"
    )
    assert lifted == []
    refusal = next(i for i in issues if i.code == "scats_detector_movement_mapping_missing")
    pairs = {(p["site_id"], p["detector_id"]) for p in refusal.context["unmapped_pairs"]}
    assert pairs == {(SITE_ID, "1"), (SITE_ID, "2")}
    assert refusal.context["unmapped_record_count"] == len(detector_counts)


def test_mapping_source_must_be_stated(tmp_path):
    detector_counts = _parsed_detector_counts(tmp_path)
    with pytest.raises(AdapterError):
        build_turning_counts_from_detector_map(
            detector_counts, detector_movements={}, mapping_source=""
        )


def test_a_full_map_lifts_every_record_with_correct_provenance(tmp_path):
    detector_counts = _parsed_detector_counts(tmp_path)
    mapping = {
        (SITE_ID, "1"): DetectorMovement(
            approach=ApproachRef(leg_id="N"),
            movement=MovementRef(turn=TurnType.THROUGH, to_leg_id="S"),
        ),
        (SITE_ID, "2"): DetectorMovement(
            approach=ApproachRef(leg_id="N"),
            movement=MovementRef(turn=TurnType.LEFT, to_leg_id="W"),
        ),
    }
    lifted, issues = build_turning_counts_from_detector_map(
        detector_counts,
        detector_movements=mapping,
        mapping_source="test: hand-authored map",
    )
    assert issues == []
    assert len(lifted) == len(detector_counts)
    total_by_turn: dict[str, float] = {}
    for count in lifted:
        assert count.site_id == SITE_ID
        assert count.approach.leg_id == "N"
        assert count.vehicle_class.code == "all"
        assert "test: hand-authored map" in count.provenance.derived_by
        turn = count.movement.turn.value
        total_by_turn[turn] = total_by_turn.get(turn, 0.0) + count.volume
    assert total_by_turn == {"through": pytest.approx(40.0), "left": pytest.approx(60.0)}


def test_a_partial_map_lifts_what_it_covers_and_refuses_the_rest_by_name(tmp_path):
    detector_counts = _parsed_detector_counts(tmp_path)
    mapping = {
        (SITE_ID, "1"): DetectorMovement(
            approach=ApproachRef(leg_id="N"),
            movement=MovementRef(turn=TurnType.THROUGH, to_leg_id="S"),
        ),
    }
    lifted, issues = build_turning_counts_from_detector_map(
        detector_counts, detector_movements=mapping, mapping_source="test: partial map"
    )
    assert len(lifted) == 4  # detector 1's 4 quarter-hour records only
    refusal = next(i for i in issues if i.code == "scats_detector_movement_mapping_missing")
    pairs = {(p["site_id"], p["detector_id"]) for p in refusal.context["unmapped_pairs"]}
    assert pairs == {(SITE_ID, "2")}


# ---------------------------------------------------------------------------
# 8. Decompression-bomb guard (adversarial finding, 2026-08-12/13) -- wired
# into ``_open_scats_workbook`` via ``services.dataimport.xlsx_guard``. Full
# guard coverage (each cap in isolation, "lying header" handling, clean
# pass-through) lives in test_xlsx_guard.py; this only proves the WIRING:
# this adapter's own AdapterError fires, carrying the guard's measured
# numbers, and a genuinely small real-shaped workbook is unaffected.
# ---------------------------------------------------------------------------

_BOMB_SST_HEADER = (
    b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    b'count="1" uniqueCount="1"><si><t xml:space="preserve">'
)
_BOMB_SST_FOOTER = b"</t></si></sst>"
_BOMB_SHARED_STRINGS_OVERRIDE = (
    b'<Override PartName="/xl/sharedStrings.xml" '
    b'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
)


def _bomb_workbook(path: Path, *, declared_bytes: int = 5_000_000) -> Path:
    """A real .xlsx decompression bomb reusing this file's own
    ``_write_workbook`` as the base (so it is otherwise a completely normal
    SCATS-shaped export): ``xl/sharedStrings.xml`` is replaced by a single
    repeated-character run that declares ``declared_bytes`` -- ~1000:1
    compressed on disk, tripping the guard's ratio cap (60:1) at production
    thresholds without needing a multi-hundred-MB fixture. See
    services/dataimport/xlsx_guard.py's module docstring for how the
    Content_Types Override is required for openpyxl to even look at this
    part.
    """
    base_path = path.parent / f"__bomb_base_{path.name}"
    _write_workbook(base_path, quarter_grid=_default_quarter_grid())
    src = zipfile.ZipFile(base_path)
    sst = _BOMB_SST_HEADER + b"A" * declared_bytes + _BOMB_SST_FOOTER

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for info in src.infolist():
            if info.filename == "[Content_Types].xml":
                data = src.read(info.filename)
                if b"sharedStrings" not in data:
                    data = data.replace(b"</Types>", _BOMB_SHARED_STRINGS_OVERRIDE + b"</Types>")
                archive.writestr(info, data)
            else:
                archive.writestr(info, src.read(info.filename))
        archive.writestr("xl/sharedStrings.xml", sst)
    src.close()
    return path


def test_a_decompression_bomb_is_refused_by_adapter_error_with_the_measured_numbers(
    adapter, tmp_path
):
    path = _bomb_workbook(tmp_path / "bomb.xlsx", declared_bytes=5_000_000)
    with pytest.raises(AdapterError) as exc_info:
        adapter.parse([path])
    message = str(exc_info.value)
    assert path.name in message
    assert "5,000," in message  # the declared byte count, verbatim


def test_the_smallest_real_shaped_fixture_still_parses_cleanly(adapter, tmp_path):
    """The guard must clear ordinary SCATS-shaped workbooks with room to
    spare -- reuses this file's own smallest fixture builder (Site tab +
    one hour of Quarter Hourly Volume) rather than copying anything from
    client-data/."""
    path = _write_workbook(tmp_path / "fixture.xlsx", quarter_grid=_default_quarter_grid())
    result = adapter.parse([path])
    assert result.ok, result.issue_codes(IssueSeverity.REFUSE)


def test_a_lying_declared_size_is_refused_by_the_existing_corrupt_file_path(adapter, tmp_path):
    """A member whose CENTRAL DIRECTORY declares a small size while its
    recorded CRC is for a much larger stream: the guard's header precheck
    sees nothing to refuse (small declared size), defers to the real
    reader, and openpyxl/zipfile discover the inconsistency themselves
    (BadZipFile) -- caught by this adapter's EXISTING generic ``except
    Exception`` in ``_open_scats_workbook``, same as any other corrupt
    file. Nothing is silently admitted at full size either way. See
    services/dataimport/xlsx_guard.py's module docstring for the full
    "does zipfile trust the header" investigation.
    """
    import struct
    import zlib

    base_path = tmp_path / "lying_base.xlsx"
    _write_workbook(base_path, quarter_grid=_default_quarter_grid())
    src = zipfile.ZipFile(base_path)
    sst = _BOMB_SST_HEADER + b"B" * 200_000 + _BOMB_SST_FOOTER
    crc_full = zlib.crc32(sst) & 0xFFFFFFFF

    out_path = tmp_path / "lying.xlsx"
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for info in src.infolist():
            if info.filename == "[Content_Types].xml":
                data = src.read(info.filename)
                if b"sharedStrings" not in data:
                    data = data.replace(b"</Types>", _BOMB_SHARED_STRINGS_OVERRIDE + b"</Types>")
                archive.writestr(info, data)
            else:
                archive.writestr(info, src.read(info.filename))
        archive.writestr("xl/sharedStrings.xml", sst)
    src.close()

    raw = bytearray(out_path.read_bytes())
    idx = raw.find(b"xl/sharedStrings.xml")
    patched = False
    while idx != -1:
        central_start = idx - 46
        if central_start >= 0 and raw[central_start : central_start + 4] == b"PK\x01\x02":
            struct.pack_into("<I", raw, central_start + 16, crc_full)
            struct.pack_into("<I", raw, central_start + 24, 1000)  # lie: declare only 1000 bytes
            patched = True
        idx = raw.find(b"xl/sharedStrings.xml", idx + 1)
    assert patched, "fixture bug: did not find the central directory record to patch"
    out_path.write_bytes(bytes(raw))

    with pytest.raises(AdapterError):
        adapter.parse([out_path])


def test_an_encrypted_member_with_no_size_violation_never_leaks_a_raw_runtimeerror(
    adapter, tmp_path
):
    """Regression (adversarial review, 2026-08-13) -- see
    test_dataimport_daejeon.py's identically-named test for the full story:
    a member with the encryption general-purpose flag bit set (no size
    violation) made the guard's streaming re-measurement raise a raw
    ``RuntimeError`` that ``except zipfile.BadZipFile`` alone did not catch.
    Fixed by catching ``(BadZipFile, RuntimeError, NotImplementedError)``
    explicitly in services/dataimport/xlsx_guard.py. This adapter must
    produce its normal parse outcome, never a raw ``RuntimeError``.
    """
    import struct

    import openpyxl

    path = tmp_path / "encrypted_member.xlsx"
    _write_workbook(path, quarter_grid=_default_quarter_grid())
    raw = bytearray(path.read_bytes())
    target = b"docProps/app.xml"
    idx = raw.find(target)
    patched = 0
    while idx != -1:
        local_start = idx - 30
        if local_start >= 0 and raw[local_start : local_start + 4] == b"PK\x03\x04":
            flags = struct.unpack_from("<H", raw, local_start + 6)[0]
            struct.pack_into("<H", raw, local_start + 6, flags | 0x1)
            patched += 1
        central_start = idx - 46
        if central_start >= 0 and raw[central_start : central_start + 4] == b"PK\x01\x02":
            flags = struct.unpack_from("<H", raw, central_start + 8)[0]
            struct.pack_into("<H", raw, central_start + 8, flags | 0x1)
            patched += 1
        idx = raw.find(target, idx + 1)
    assert patched == 2, "fixture bug: expected to patch local+central headers for docProps/app.xml"
    path.write_bytes(bytes(raw))

    # Fixture sanity: plain openpyxl must be unaffected (never touches this member).
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    assert wb.sheetnames

    result = adapter.parse([path])
    assert result.ok, result.issue_codes(IssueSeverity.REFUSE)

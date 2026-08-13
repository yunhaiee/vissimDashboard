"""Tests for the 대전 스마트교차로 count adapter (roadmap 1.2, Track D).

**Every fixture here is synthetic.** The real client drop lives in the
gitignored ``client-data/`` and never enters the repo: these workbooks invent
intersection names, approach labels and volumes, and reproduce only the
*shape* recorded in docs/product/data-intake-inventory-2026-08-03.md §1 — the
three-row header, the six class columns plus 합계, the interleaved 소계 rows,
the vertically merged 유입부 column and the bogus ``<dimension ref="A1"/>``
that ten of the eleven real workbooks declare.

The missing-input tests come first (contract §8 checklist item 6): for each
thing the format can lack or contradict, the adapter must produce an issue and
a ``None``/absence, never a number.
"""
from __future__ import annotations

import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from openpyxl import Workbook

from services.dataimport.adapters.daejeon import (
    CLASS_SCHEME,
    DAEJEON_FIELD_MAP,
    TOTAL_CLASS_CODE,
    DaejeonCountAdapter,
)
from services.dataimport.base import (
    SNIFF_CONFIDENCE_THRESHOLD,
    AdapterError,
    IssueSeverity,
    check_adapter_conformance,
)
from services.dataimport.quality import apply_quality_gate, is_all_class_total
from services.dataimport.schema import (
    CanonicalVehicleClass,
    ClassificationScheme,
    DriveSide,
    TurnType,
    VolumeUnit,
    neutral_field_path_exists,
)

# ---------------------------------------------------------------------------
# Synthetic fixture builders — invented data in the real drop's shape
# ---------------------------------------------------------------------------

WEDNESDAY = datetime(2026, 4, 8, 7, 0)  # neutral mid-week day, 07:00
MONDAY = datetime(2026, 4, 6, 7, 0)

MOVEMENT_HEADERS = ("좌회전", "직 진", "우회전", "유 턴")

#: 화물 대형/중형/소형 · 버스 대형/소형 · 승용 — the six classes, summing to 24.
QUARTER = (0, 1, 2, 0, 1, 20)
#: A busier through movement, summing to 61.
BUSY = (1, 2, 3, 1, 4, 50)


def _block(values: tuple[int, ...] | None) -> list[str]:
    """One movement's seven columns: six classes then the stated 합계."""
    if values is None:
        return [""] * 7
    return [str(value) for value in values] + [str(sum(values))]


def _scale(values: tuple[int, ...] | None, factor: int) -> tuple[int, ...] | None:
    return None if values is None else tuple(value * factor for value in values)


def _fmt(when: datetime) -> str:
    """The real files store every cell as text, timestamps included."""
    return when.strftime("%Y-%m-%d %H:%M")


def _header_rows(movements: tuple[str, ...] = MOVEMENT_HEADERS) -> list[list[str]]:
    row1: list[str] = ["유입부", "시간대"]
    row2: list[str] = ["", ""]
    row3: list[str] = ["", ""]
    for movement in movements:
        row1 += [movement, "", "", "", "", "", ""]
        row2 += ["화물", "", "", "버스", "", "승용", "합계"]
        row3 += ["대형", "중형", "소형", "대형", "소형", "", ""]
    return [row1, row2, row3]


def _row(approach: str | None, when: str, per_movement) -> list[str]:
    cells: list[str] = [approach or "", when]
    for values in per_movement:
        cells += _block(values)
    return cells


def _approach_rows(
    approach: str,
    *,
    start: datetime = WEDNESDAY,
    hours: int = 3,
    per_movement=(QUARTER, BUSY, QUARTER, None),
    label_every_row: bool = False,
    with_subtotals: bool = True,
) -> list[list[str]]:
    """One approach block: 15-minute rows with an hourly 소계 after each four.

    ``label_every_row=False`` reproduces the vertically merged 유입부 column of
    the real export — only the first row of the block carries the name.
    """
    rows: list[list[str]] = []
    first = True
    for hour in range(hours):
        hour_start = start + timedelta(hours=hour)
        for quarter in range(4):
            when = hour_start + timedelta(minutes=15 * quarter)
            label = approach if (label_every_row or first) else None
            rows.append(_row(label, _fmt(when), per_movement))
            first = False
        if with_subtotals:
            rows.append(_row(None, "소계", [_scale(m, 4) for m in per_movement]))
    return rows


def _file_name(
    site: str = "가람네거리",
    *,
    minutes: int = 15,
    start: datetime = WEDNESDAY,
    last_start: datetime | None = None,
) -> str:
    last = last_start if last_start is not None else start + timedelta(hours=3, minutes=-15)
    return (
        f"[테스트 제출용] {start:%Y-%m-%d %H}_{start:%M}_"
        f"{last:%Y-%m-%d %H}_{last:%M}_{site}_{minutes}분_집계 주기.xlsx"
    )


def _write_workbook(root: Path, rows: list[list[str]], *, file_name: str | None = None) -> Path:
    path = root / (file_name or _file_name())
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    for row in _header_rows():
        sheet.append(row)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    workbook.close()
    return path


def _write_merged_workbook(
    root: Path, blocks: list[list[list[str]]], *, file_name: str | None = None
) -> Path:
    """:func:`_write_workbook`, except the 유입부 column is *genuinely* merged.

    Every other fixture here writes only the merge's observable — the label on
    the block's first row, blank below — which is what openpyxl hands the
    adapter either way. This one writes the construct itself, one
    ``<mergeCell>`` per approach block.
    """
    path = root / (file_name or _file_name())
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Sheet1"
    for row in _header_rows():
        sheet.append(row)
    for block in blocks:
        first_row = sheet.max_row + 1
        for row in block:
            sheet.append(row)
        sheet.merge_cells(start_row=first_row, start_column=1, end_row=sheet.max_row, end_column=1)
    workbook.save(path)
    workbook.close()
    return path


def _merged_cell_refs(path: Path) -> list[str]:
    """The ``<mergeCell>`` ranges the saved workbook actually declares.

    Read at zip level, like :func:`_break_declared_dimension`, so a fixture
    that claims to be merged cannot silently degrade into a plain
    label-on-first-row sheet if openpyxl's merge handling ever changes.
    """
    with zipfile.ZipFile(path) as archive:
        sheet_xml = archive.read("xl/worksheets/sheet1.xml").decode("utf-8")
    return re.findall(r'<mergeCell[^>]*\bref="([^"]+)"', sheet_xml)


def _break_declared_dimension(path: Path) -> None:
    """Rewrite the sheet's declared dimension to ``A1``.

    Ten of the eleven real workbooks do exactly this, and openpyxl's read-only
    mode believes the declaration: without ``reset_dimensions()`` the adapter
    would read one cell and report a clean, empty import.
    """
    with zipfile.ZipFile(path) as archive:
        members = [(info.filename, archive.read(info.filename)) for info in archive.infolist()]
    patched = False
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            if name == "xl/worksheets/sheet1.xml":
                data, count = re.subn(rb'<dimension ref="[^"]*"/>', b'<dimension ref="A1"/>', data, count=1)
                patched = bool(count)
            archive.writestr(name, data)
    assert patched, "openpyxl no longer writes a <dimension> element — fixture needs updating"


def _corrupt_a_row_past_the_header(path: Path) -> None:
    """Mangle the LAST ``<row>`` element's XML into an unclosed tag.

    Two things read less of the sheet than a full ``parse()`` does, and both
    must keep working on the result: ``services.observations.stage_workbook``'s
    upload-time readability check opens the workbook and closes it again
    without ever reading a row, and this adapter's own ``sniff`` (via
    ``_peek_header``) reads exactly the FIRST row before it has enough
    evidence to decide. openpyxl's read-only ``iter_rows`` is a lazy
    generator, so corrupting only the sheet's LAST row is invisible to both —
    it only surfaces once something walks the WHOLE sheet, which is exactly
    what a full ``adapter.parse()`` does. Used by
    ``tests/test_observations_store.py`` and
    ``tests/test_observations_endpoint.py`` to reproduce the gap
    ``services.observations.ingest_observation_set`` closes: a workbook that
    clears both of those cheaper checks and only fails deep inside the real
    parse.
    """
    with zipfile.ZipFile(path) as archive:
        members = {info.filename: archive.read(info.filename) for info in archive.infolist()}
    xml = members["xl/worksheets/sheet1.xml"].decode("utf-8")
    matches = list(re.finditer(r"<row\b[^>]*>.*?</row>", xml, flags=re.S))
    assert len(matches) > 3, "fixture bug: expected 3 header rows + at least one data row"
    last = matches[-1]
    opening_tag = re.match(r"<row\b[^>]*>", last.group(0)).group(0)
    mangled = xml[: last.start()] + opening_tag + '<c r="UNCLOSED' + xml[last.end() :]
    members["xl/worksheets/sheet1.xml"] = mangled.encode("utf-8")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


@pytest.fixture()
def adapter() -> DaejeonCountAdapter:
    return DaejeonCountAdapter()


def _parse(adapter: DaejeonCountAdapter, *paths: Path):
    return adapter.parse(list(paths))


def _codes(result, severity=None) -> list[str]:
    return result.issue_codes(severity)


# ---------------------------------------------------------------------------
# 1. Contract conformance: the field map resolves, the adapter is an adapter
# ---------------------------------------------------------------------------


def test_every_declared_neutral_path_resolves_against_the_schema():
    unresolved = [
        mapping.neutral_path
        for mapping in DAEJEON_FIELD_MAP
        if not neutral_field_path_exists(mapping.neutral_path)
    ]
    assert unresolved == [], (
        "these Daejeon source fields have nowhere to go in the neutral model — "
        "the SCHEMA is wrong, not the map"
    )


def test_the_field_map_records_what_the_adapter_deliberately_does_not_parse():
    unparsed = {mapping.source_field for mapping in DAEJEON_FIELD_MAP if not mapping.parsed}
    assert "hourly subtotal row" in unparsed
    assert "signal timings / phasing" in unparsed


def test_adapter_conforms_structurally(adapter):
    assert check_adapter_conformance(adapter) == []
    assert adapter.info.region == "KR-Daejeon"


# ---------------------------------------------------------------------------
# 2. Missing / contradictory inputs — written before the happy path
# ---------------------------------------------------------------------------


def test_no_workbook_at_all_refuses_and_still_returns(adapter, tmp_path):
    stray = tmp_path / "notes.txt"
    stray.write_text("not a workbook", encoding="utf-8")
    result = _parse(adapter, stray)
    assert "daejeon_no_workbook_supplied" in _codes(result, IssueSeverity.REFUSE)
    assert result.counts == []
    assert result.ok is False


def test_an_unrecognised_header_refuses_rather_than_guessing_columns(adapter, tmp_path):
    path = tmp_path / _file_name()
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Approach", "Time", "Left", "Through"])
    sheet.append(["a", "b", "1", "2"])
    sheet.append(["a", "b", "1", "2"])
    sheet.append(["a", "b", "1", "2"])
    workbook.save(path)
    workbook.close()
    result = _parse(adapter, path)
    assert "daejeon_header_not_recognised" in _codes(result, IssueSeverity.REFUSE)
    assert result.counts == []


def test_a_file_name_without_the_export_pattern_refuses_to_invent_a_site_id(adapter, tmp_path):
    path = _write_workbook(tmp_path, _approach_rows("가람방면"), file_name="counts.xlsx")
    result = _parse(adapter, path)
    assert "daejeon_site_identity_unavailable" in _codes(result, IssueSeverity.REFUSE)
    assert result.counts == []


def test_an_explicit_site_id_mapping_replaces_the_file_name_fallback(tmp_path):
    path = _write_workbook(tmp_path, _approach_rows("가람방면"), file_name="counts.xlsx")
    adapter = DaejeonCountAdapter(site_ids={"counts.xlsx": "KR-DJ-0001"})
    result = _parse(adapter, path)
    assert result.ok, _codes(result, IssueSeverity.REFUSE)
    assert {count.site_id for count in result.counts} == {"KR-DJ-0001"}
    assert "daejeon_site_id_from_file_name" not in _codes(result)


def test_the_file_name_fallback_is_used_but_warns_that_the_identity_is_unverified(
    adapter, tmp_path
):
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    result = _parse(adapter, path)
    assert "daejeon_site_id_from_file_name" in _codes(result, IssueSeverity.WARN)
    assert {count.site_id for count in result.counts} == {"가람네거리"}


def test_the_fallback_can_be_switched_off_entirely(tmp_path):
    adapter = DaejeonCountAdapter()
    adapter.site_id_from_file_name = False
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    result = _parse(adapter, path)
    assert "daejeon_site_identity_unavailable" in _codes(result, IssueSeverity.REFUSE)


def test_a_declared_period_that_contradicts_the_row_spacing_refuses(adapter, tmp_path):
    """The bin length is read from two independent statements; when they
    disagree the adapter writes no interval at all rather than pick one."""
    rows: list[list[str]] = []
    for index in range(12):
        when = WEDNESDAY + timedelta(minutes=5 * index)
        rows.append(_row("가람방면" if index == 0 else None, _fmt(when), (QUARTER, BUSY, QUARTER, None)))
    path = _write_workbook(tmp_path, rows)  # file name still declares 15분
    result = _parse(adapter, path)
    assert "daejeon_interval_length_contradicts_declaration" in _codes(result, IssueSeverity.REFUSE)
    assert result.counts == []


def test_a_subtotal_that_disagrees_with_its_intervals_refuses(adapter, tmp_path):
    rows = _approach_rows("가람방면")
    subtotal_index = next(index for index, row in enumerate(rows) if row[1] == "소계")
    rows[subtotal_index] = _row(None, "소계", (_scale(QUARTER, 5), _scale(BUSY, 4), _scale(QUARTER, 4), None))
    path = _write_workbook(tmp_path, rows)
    result = _parse(adapter, path)
    assert "daejeon_subtotal_disagrees_with_intervals" in _codes(result, IssueSeverity.REFUSE)
    issue = next(i for i in result.issues if i.code == "daejeon_subtotal_disagrees_with_intervals")
    assert issue.context["examples"]


def test_a_repeated_interval_row_refuses_and_is_not_emitted_twice(adapter, tmp_path):
    rows = _approach_rows("가람방면")
    rows.insert(1, _row(None, _fmt(WEDNESDAY), (QUARTER, BUSY, QUARTER, None)))
    path = _write_workbook(tmp_path, rows)
    result = _parse(adapter, path)
    assert "daejeon_duplicate_interval_row" in _codes(result, IssueSeverity.REFUSE)
    first_bin = [
        count
        for count in result.counts
        if count.interval_start == WEDNESDAY
        and count.movement.turn is TurnType.THROUGH
        and count.vehicle_class.code == "승용"
    ]
    assert len(first_bin) == 1, "the repeat must not be emitted — it would double-count"


def test_a_non_numeric_count_cell_is_skipped_not_read_as_zero(adapter, tmp_path):
    rows = _approach_rows("가람방면")
    # columns: 0 유입부, 1 시간대, 2–8 좌회전, 9–15 직 진 → index 9 is 직 진 / 화물 대형
    rows[0][9] = "n/a"
    # the stated 합계 no longer matches its parts, which is the gate's business;
    # what this test pins is that the adapter did not turn "n/a" into 0.
    path = _write_workbook(tmp_path, rows)
    result = _parse(adapter, path)
    assert "daejeon_non_numeric_count_cell" in _codes(result, IssueSeverity.WARN)
    offending = [
        count
        for count in result.counts
        if count.interval_start == WEDNESDAY
        and count.movement.turn is TurnType.THROUGH
        and count.vehicle_class.code == "화물_대형"
    ]
    assert offending == []


def test_unmapped_movement_and_class_headers_are_reported_not_guessed(adapter, tmp_path):
    path = tmp_path / _file_name()
    workbook = Workbook()
    sheet = workbook.active
    # 대각선횡단 is a movement we do not know; 이륜 is a class we do not know
    # inside a movement we do. Both must be skipped and reported separately.
    sheet.append(["유입부", "시간대", "대각선횡단", "", "직 진", "", "", "", "", "", "", ""])
    sheet.append(["", "", "화물", "합계", "화물", "", "", "버스", "", "승용", "합계", "이륜"])
    sheet.append(["", "", "초대형", "", "대형", "중형", "소형", "대형", "소형", "", "", ""])
    for index in range(12):
        when = WEDNESDAY + timedelta(minutes=15 * index)
        sheet.append(
            ["가람방면" if index == 0 else "", _fmt(when), "1", "1"] + _block(BUSY) + ["3"]
        )
    workbook.save(path)
    workbook.close()
    result = _parse(adapter, path)
    assert "daejeon_unmapped_movement_column" in _codes(result, IssueSeverity.WARN)
    assert "daejeon_unmapped_class_column" in _codes(result, IssueSeverity.WARN)
    assert {count.movement.turn for count in result.counts} == {TurnType.THROUGH}
    assert "이륜" not in {count.vehicle_class.code for count in result.counts}


# ---------------------------------------------------------------------------
# 3. The happy path, and what it produces
# ---------------------------------------------------------------------------


def test_a_clean_workbook_parses_and_passes_the_quality_gate(tmp_path):
    adapter = DaejeonCountAdapter(site_ids={})
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    adapter_result = _parse(adapter, path)
    result = apply_quality_gate(adapter_result)
    assert result.ok, _codes(result, IssueSeverity.REFUSE)
    # The only warning a clean file earns is the site-identity one, which the
    # export's own lack of a site id makes unavoidable.
    assert _codes(result, IssueSeverity.WARN) == ["daejeon_site_id_from_file_name"]
    assert len(result.sites) == 1


def test_all_four_movements_including_the_u_turn_are_representable(adapter, tmp_path):
    path = _write_workbook(
        tmp_path, _approach_rows("가람방면", per_movement=(QUARTER, BUSY, QUARTER, QUARTER))
    )
    result = _parse(adapter, path)
    assert {count.movement.turn for count in result.counts} == {
        TurnType.LEFT,
        TurnType.THROUGH,
        TurnType.RIGHT,
        TurnType.U_TURN,
    }
    u_turns = [c for c in result.counts if c.movement.turn is TurnType.U_TURN]
    assert {c.movement.turn_raw for c in u_turns} == {"유 턴"}, "the source spelling is kept"


def test_all_six_classes_plus_the_stated_total_are_emitted(adapter, tmp_path):
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    result = _parse(adapter, path)
    through = [c for c in result.counts if c.movement.turn is TurnType.THROUGH]
    codes = {count.vehicle_class.code for count in through}
    assert codes == {
        "화물_대형",
        "화물_중형",
        "화물_소형",
        "버스_대형",
        "버스_소형",
        "승용",
        TOTAL_CLASS_CODE,
    }
    classified = [c for c in through if c.vehicle_class.code != TOTAL_CLASS_CODE]
    assert {c.vehicle_class.scheme for c in classified} == {CLASS_SCHEME}
    assert CLASS_SCHEME is ClassificationScheme.KOREA_SMART_INTERSECTION_6


def test_the_total_column_is_marked_as_a_total_so_consumers_cannot_double_count(
    adapter, tmp_path
):
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    result = _parse(adapter, path)
    totals = [c for c in result.counts if c.vehicle_class.code == TOTAL_CLASS_CODE]
    assert totals
    assert all(is_all_class_total(count) for count in totals)
    assert {c.vehicle_class.canonical for c in totals} == {CanonicalVehicleClass.ALL_VEHICLES}
    assert not any(is_all_class_total(c) for c in result.counts if c.vehicle_class.code == "승용")


def test_only_defensible_canonical_buckets_are_filled(adapter, tmp_path):
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    result = _parse(adapter, path)
    canonical = {
        count.vehicle_class.code: count.vehicle_class.canonical for count in result.counts
    }
    assert canonical["화물_대형"] is CanonicalVehicleClass.HEAVY_VEHICLE
    assert canonical["화물_중형"] is CanonicalVehicleClass.HEAVY_VEHICLE
    assert canonical["버스_대형"] is CanonicalVehicleClass.BUS
    assert canonical["버스_소형"] is CanonicalVehicleClass.BUS
    assert canonical["승용"] is CanonicalVehicleClass.LIGHT_VEHICLE
    assert canonical["화물_소형"] is None, (
        "a ~1 t Korean 소형화물차 is not obviously light or heavy — the adapter "
        "must decline rather than decide the client's question for them"
    )


def test_the_unmapped_class_is_reported_by_the_gate_rather_than_guessed(tmp_path):
    adapter = DaejeonCountAdapter()
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    result = apply_quality_gate(_parse(adapter, path))
    unmapped = next(i for i in result.issues if i.code == "vehicle_class_unmapped")
    assert unmapped.severity is IssueSeverity.INFO
    assert any("화물_소형" in code for code in unmapped.context["codes"])


def test_subtotal_rows_are_never_emitted_as_records(adapter, tmp_path):
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    result = _parse(adapter, path)
    assert result.counts
    assert {count.interval_minutes for count in result.counts} == {15.0}, (
        "an hourly 소계 emitted as a record would put two bin lengths in one series"
    )
    info = next(i for i in result.issues if i.code == "daejeon_workbook_parsed")
    assert info.context["subtotal_rows"] == 3


def test_the_gate_refuses_a_workbook_whose_stated_total_column_is_wrong(adapter, tmp_path):
    """The inventory says the gate *should* catch a totals disagreement; this
    proves it end to end rather than assuming it.

    The 합계 column is emitted as a record precisely so that judging it belongs
    to the region-agnostic gate instead of to this adapter.
    """
    rows = _approach_rows("가람방면")
    rows[0][15] = str(sum(BUSY) + 40)  # 직 진 / 합계, inflated
    path = _write_workbook(tmp_path, rows)
    result = apply_quality_gate(_parse(adapter, path))
    assert result.ok is False
    assert "class_total_mismatch" in _codes(result, IssueSeverity.REFUSE)
    refusal = next(i for i in result.issues if i.code == "class_total_mismatch")
    assert refusal.context["stated_total"] == sum(BUSY) + 40
    assert refusal.context["class_sum"] == sum(BUSY)


def test_emitting_the_hourly_subtotal_would_have_been_refused_as_a_double_count(
    adapter, tmp_path
):
    """The negative control behind the "never emit 소계" decision.

    Adding the hourly aggregate to the same series is what a naive reader does;
    the gate calls it what it is. That refusal is why the adapter drops the row
    and checks it at the point of discard instead.
    """
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    result = _parse(adapter, path)
    sample = next(
        count
        for count in result.counts
        if count.interval_start == WEDNESDAY and count.vehicle_class.code == "승용"
    )
    result.counts.append(
        sample.model_copy(
            update={
                "interval_end": WEDNESDAY + timedelta(hours=1),
                "volume": sample.volume * 4,
            }
        )
    )
    gated = apply_quality_gate(result)
    assert "count_intervals_overlap" in _codes(gated, IssueSeverity.REFUSE)


def test_the_merged_approach_column_is_forward_filled(adapter, tmp_path):
    rows = _approach_rows("가람방면") + _approach_rows("나루방면")
    path = _write_workbook(tmp_path, rows)
    result = _parse(adapter, path)
    assert {count.approach.leg_id for count in result.counts} == {"가람방면", "나루방면"}
    assert all(count.approach.raw == count.approach.leg_id for count in result.counts)


def test_a_really_merged_approach_column_parses_identically_to_the_flat_fixture(
    adapter, tmp_path
):
    """The merge as a file construct, not just as its observable.

    The forward-fill above is exercised by fixtures that merely *look* merged
    (label on the first row, blank below). Ten of the eleven real workbooks
    carry an actual vertical merge, so this builds one, proves at zip level
    that the ``<mergeCell>`` elements are there, and then requires the parse to
    be identical record for record to the flat equivalent.
    """
    blocks = [_approach_rows("가람방면"), _approach_rows("나루방면")]
    merged_dir = tmp_path / "merged"
    flat_dir = tmp_path / "flat"
    merged_dir.mkdir()
    flat_dir.mkdir()
    merged = _write_merged_workbook(merged_dir, blocks)
    flat = _write_workbook(flat_dir, [row for block in blocks for row in block])

    # (i) the fixture really is merged: three header rows, then 15 rows a block
    # (twelve 15-minute rows plus three hourly 소계).
    assert set(_merged_cell_refs(merged)) == {"A4:A18", "A19:A33"}
    assert _merged_cell_refs(flat) == [], "the flat control must NOT be merged"

    # (ii) and the merge changes nothing downstream — same records, same legs.
    merged_result = _parse(adapter, merged)
    flat_result = _parse(adapter, flat)
    assert merged_result.ok, _codes(merged_result, IssueSeverity.REFUSE)
    assert {count.approach.leg_id for count in merged_result.counts} == {"가람방면", "나루방면"}
    assert len(merged_result.counts) == 2 * 12 * 3 * 7
    assert [count.model_dump() for count in merged_result.counts] == [
        count.model_dump() for count in flat_result.counts
    ]


def test_a_sheet_that_starts_mid_merge_refuses_rather_than_attributing_by_guess(
    adapter, tmp_path
):
    rows = _approach_rows("가람방면")
    rows[0][0] = ""  # the label the merge would have carried is gone
    path = _write_workbook(tmp_path, rows)
    result = _parse(adapter, path)
    assert "daejeon_approach_missing_before_first_label" in _codes(result, IssueSeverity.REFUSE)
    assert result.counts == []


def test_a_workbook_declaring_a_bogus_a1_dimension_is_still_read_in_full(adapter, tmp_path):
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    _break_declared_dimension(path)
    result = _parse(adapter, path)
    assert len(result.counts) == 12 * 3 * 7, (
        "reset_dimensions() is what stops openpyxl reporting a clean empty import "
        "for the ten real workbooks that declare <dimension ref='A1'/>"
    )


def test_a_blank_movement_block_produces_no_records_and_is_reported(adapter, tmp_path):
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))  # 유 턴 blank throughout
    result = _parse(adapter, path)
    assert not [c for c in result.counts if c.movement.turn is TurnType.U_TURN]
    info = next(i for i in result.issues if i.code == "daejeon_movement_not_counted")
    assert info.severity is IssueSeverity.INFO
    assert any("유 턴" in block for block in info.context["blocks"])


def test_a_blank_interval_row_becomes_a_hole_not_a_zero(adapter, tmp_path):
    """A row whose cells are all blank is an unobserved interval.

    The real drop has 496 of them. No record is emitted, the hour's 소계 sums
    only the rows that carry data (as the real files do), and the gate turns
    the hole into a warning rather than anyone reading it as zero demand.
    """
    blank_at = WEDNESDAY + timedelta(hours=1)
    per_movement = (QUARTER, BUSY, QUARTER, None)
    rows: list[list[str]] = []
    for hour in range(4):
        hour_start = WEDNESDAY + timedelta(hours=hour)
        observed = 0
        for quarter in range(4):
            when = hour_start + timedelta(minutes=15 * quarter)
            label = "가람방면" if (hour == 0 and quarter == 0) else None
            if when == blank_at:
                rows.append(_row(label, _fmt(when), (None, None, None, None)))
            else:
                observed += 1
                rows.append(_row(label, _fmt(when), per_movement))
        rows.append(_row(None, "소계", [_scale(m, observed) for m in per_movement]))

    path = _write_workbook(
        tmp_path, rows, file_name=_file_name(last_start=WEDNESDAY + timedelta(hours=3, minutes=45))
    )
    result = apply_quality_gate(_parse(adapter, path))
    assert not [c for c in result.counts if c.interval_start == blank_at]
    assert "daejeon_subtotal_disagrees_with_intervals" not in _codes(result, IssueSeverity.REFUSE)
    assert "count_interval_gaps" in _codes(result, IssueSeverity.WARN)
    assert "count_intervals_overlap" not in _codes(result, IssueSeverity.REFUSE)


def test_midnight_rollover_lands_the_last_bin_on_the_next_day(adapter, tmp_path):
    start = datetime(2026, 4, 8, 22, 0)
    rows = _approach_rows("가람방면", start=start, hours=2)
    path = _write_workbook(
        tmp_path,
        rows,
        file_name=_file_name(start=start, last_start=start + timedelta(hours=1, minutes=45)),
    )
    result = _parse(adapter, path)
    last = max(count.interval_end for count in result.counts)
    assert last == datetime(2026, 4, 9, 0, 0), "23:45 + 15 min is the next calendar day"
    site = result.sites[0]
    assert site.collection_period.end == datetime(2026, 4, 9, 0, 0)


def test_a_multi_week_survey_keeps_every_day_and_flags_the_non_neutral_ones(
    adapter, tmp_path
):
    rows: list[list[str]] = []
    days = 15
    for day in range(days):
        rows += _approach_rows("가람방면", start=MONDAY + timedelta(days=day), hours=3)
    path = _write_workbook(
        tmp_path,
        rows,
        file_name=_file_name(
            start=MONDAY,
            last_start=MONDAY + timedelta(days=days - 1, hours=2, minutes=45),
        ),
    )
    result = apply_quality_gate(_parse(adapter, path))
    surveyed = {count.interval_start.date() for count in result.counts}
    assert len(surveyed) == days
    site = result.sites[0]
    assert site.collection_period.start == MONDAY
    non_neutral = next(i for i in result.issues if i.code == "non_neutral_survey_day")
    assert non_neutral.severity is IssueSeverity.WARN
    # Mon + Fri/Sat/Sun across 15 days starting on a Monday.
    assert len(non_neutral.context["dates"]) == 9
    assert "peak_coverage_below_minimum" not in _codes(result, IssueSeverity.REFUSE)


def test_a_survey_shorter_than_the_authority_minimum_is_refused_by_the_gate(
    adapter, tmp_path
):
    """R2.5's one hard numeric minimum, reached through a real workbook."""
    path = _write_workbook(
        tmp_path,
        _approach_rows("가람방면", hours=1),
        file_name=_file_name(last_start=WEDNESDAY + timedelta(minutes=45)),
    )
    result = apply_quality_gate(_parse(adapter, path))
    assert result.ok is False
    assert "peak_coverage_below_minimum" in _codes(result, IssueSeverity.REFUSE)


def test_an_implausible_flow_rate_warns_without_blocking(adapter, tmp_path):
    """A decimal-point slip is coherent data, so it warns; the modeller decides.

    Real Daejeon 15-minute figures top out well inside the band, so this can
    only fire on a genuine transcription error.
    """
    absurd = (0, 1, 2, 0, 1, 900)  # 900 cars in 15 min = 3600/h
    path = _write_workbook(
        tmp_path, _approach_rows("가람방면", per_movement=(QUARTER, absurd, QUARTER, None))
    )
    result = apply_quality_gate(_parse(adapter, path))
    assert "volume_rate_implausible" in _codes(result, IssueSeverity.WARN)
    assert "volume_rate_implausible" not in _codes(result, IssueSeverity.REFUSE)


def test_a_declared_window_wider_than_the_data_warns(adapter, tmp_path):
    path = _write_workbook(
        tmp_path,
        _approach_rows("가람방면"),
        file_name=_file_name(last_start=WEDNESDAY + timedelta(hours=9)),
    )
    result = _parse(adapter, path)
    assert "daejeon_declared_window_not_covered" in _codes(result, IssueSeverity.WARN)


# ---------------------------------------------------------------------------
# 4. Never invent — the properties that make the records trustworthy
# ---------------------------------------------------------------------------


def test_no_record_carries_a_bearing_or_exit_leg_the_source_never_stated(adapter, tmp_path):
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    result = _parse(adapter, path)
    assert all(count.approach.bearing is None for count in result.counts)
    assert all(count.movement.to_leg_id is None for count in result.counts)
    assert all(count.movement.to_bearing is None for count in result.counts)


def test_the_site_records_the_regional_constant_and_the_absences(adapter, tmp_path):
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    site = _parse(adapter, path).sites[0]
    assert site.drive_side is DriveSide.RIGHT  # a declared regional constant
    assert site.timezone is None and "timezone" in site.absent
    assert site.coordinate_reference is None and "coordinate_reference" in site.absent
    assert site.jurisdiction == "대전광역시"
    assert site.source_system == "스마트교차로"


def test_every_record_cites_the_file_and_the_row_it_came_from(adapter, tmp_path):
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    result = _parse(adapter, path)
    for count in result.counts:
        assert count.provenance.source_file == path.name
        assert count.provenance.locator.startswith("Sheet1!row ")
        assert count.provenance.derived_by is None, "nothing here is computed"


def test_the_unit_is_declared_explicitly_because_the_source_never_states_one(
    adapter, tmp_path
):
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    result = _parse(adapter, path)
    assert {count.volume_unit for count in result.counts} == {VolumeUnit.VEHICLES}
    assert "daejeon_volume_unit_assumed_vehicles" in _codes(result, IssueSeverity.INFO)
    assert "daejeon_approach_label_orientation_unverified" in _codes(result, IssueSeverity.INFO)


def test_volumes_survive_verbatim(adapter, tmp_path):
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    result = _parse(adapter, path)
    through_car = [
        count
        for count in result.counts
        if count.movement.turn is TurnType.THROUGH and count.vehicle_class.code == "승용"
    ]
    assert {count.volume for count in through_car} == {float(BUSY[5])}


# ---------------------------------------------------------------------------
# 5. sniff
# ---------------------------------------------------------------------------


def test_sniff_recognises_the_export_header_with_full_confidence(adapter, tmp_path):
    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
    sniffed = adapter.sniff([path])
    assert sniffed.confidence == 1.0
    assert sniffed.matched_files == [path.name]


def test_sniff_scores_a_matching_name_with_an_unreadable_body_below_the_threshold(
    adapter, tmp_path
):
    path = tmp_path / _file_name()
    path.write_bytes(b"not a workbook at all")
    sniffed = adapter.sniff([path])
    assert 0.0 < sniffed.confidence < SNIFF_CONFIDENCE_THRESHOLD


def test_sniff_says_not_mine_for_someone_elses_files(adapter, tmp_path):
    other = tmp_path / "phase_history.csv"
    other.write_text("Date,Phase,Duration\n", encoding="utf-8")
    assert adapter.sniff([other]).confidence == 0.0


def test_sniff_never_raises_on_a_missing_file(adapter, tmp_path):
    assert adapter.sniff([tmp_path / "gone.xlsx"]).confidence == 0.0


# ---------------------------------------------------------------------------
# 6. Multi-file drops — the real export is one workbook per intersection
# ---------------------------------------------------------------------------


def test_a_multi_site_drop_keeps_the_sites_apart(adapter, tmp_path):
    first = _write_workbook(tmp_path, _approach_rows("가람방면"), file_name=_file_name("가람네거리"))
    second = _write_workbook(tmp_path, _approach_rows("나루방면"), file_name=_file_name("나루삼거리"))
    result = _parse(adapter, first, second)
    assert {site.site_id for site in result.sites} == {"가람네거리", "나루삼거리"}
    assert {count.site_id for count in result.counts} == {"가람네거리", "나루삼거리"}
    assert len(result.source_files) == 2


# ---------------------------------------------------------------------------
# 7. Decompression-bomb guard (adversarial finding, 2026-08-12/13) -- wired
# into ``_open_worksheet`` via ``services.dataimport.xlsx_guard``. Full guard
# coverage (each cap in isolation, "lying header" handling, clean
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


def _bomb_workbook(root: Path, *, declared_bytes: int = 5_000_000, file_name: str | None = None) -> Path:
    """A real .xlsx decompression bomb reusing this adapter's own
    ``_write_workbook`` fixture as the base (so it is otherwise a completely
    normal Daejeon-shaped export): ``xl/sharedStrings.xml`` is replaced by a
    single repeated-character run that declares ``declared_bytes`` --
    ~1000:1 compressed on disk, tripping the guard's ratio cap (60:1) at
    production thresholds without needing a multi-hundred-MB fixture. See
    services/dataimport/xlsx_guard.py's module docstring for how the
    Content_Types Override is required for openpyxl to even look at this
    part (a base workbook alone never carries it).
    """
    base_path = _write_workbook(root, _approach_rows("가람방면"), file_name="__bomb_base__.xlsx")
    src = zipfile.ZipFile(base_path)
    sst = _BOMB_SST_HEADER + b"A" * declared_bytes + _BOMB_SST_FOOTER

    out_path = root / (file_name or _file_name())
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
    return out_path


def test_a_decompression_bomb_is_refused_by_adapter_error_with_the_measured_numbers(
    adapter, tmp_path
):
    path = _bomb_workbook(tmp_path, declared_bytes=5_000_000, file_name=_file_name())
    with pytest.raises(AdapterError) as exc_info:
        adapter.parse([path])
    message = str(exc_info.value)
    assert path.name in message
    assert "5,000," in message  # the declared byte count, verbatim
    assert "cap 60:1" in message or "cap" in message


def test_the_smallest_real_shaped_fixture_still_parses_cleanly(adapter, tmp_path):
    """The guard must clear ordinary Daejeon-shaped workbooks with room to
    spare -- reuses this file's own smallest fixture builder rather than
    copying anything from client-data/."""
    path = _write_workbook(tmp_path, _approach_rows("가람방면", hours=1))
    result = _parse(adapter, path)
    assert result.counts, "guard false-positived on a genuinely tiny real-shaped workbook"


def test_a_lying_declared_size_is_refused_by_the_existing_corrupt_file_path(adapter, tmp_path):
    """A member whose CENTRAL DIRECTORY declares a small size while its
    recorded CRC is for a much larger stream: the guard's header precheck
    sees nothing to refuse (small declared size), defers to the real
    reader, and openpyxl/zipfile discover the inconsistency themselves
    (BadZipFile) -- caught by this adapter's EXISTING generic ``except
    Exception`` in ``_open_worksheet``, same as any other corrupt file.
    Nothing is silently admitted at full size either way. See
    services/dataimport/xlsx_guard.py's module docstring for the full
    "does zipfile trust the header" investigation.
    """
    import struct
    import zlib

    base_path = _write_workbook(tmp_path, _approach_rows("가람방면"), file_name="lying_base.xlsx")
    src = zipfile.ZipFile(base_path)
    sst = _BOMB_SST_HEADER + b"B" * 200_000 + _BOMB_SST_FOOTER
    crc_full = zlib.crc32(sst) & 0xFFFFFFFF

    out_path = tmp_path / _file_name()
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
    """Regression (adversarial review, 2026-08-13): a member with the
    encryption general-purpose flag bit set (no size violation at all) made
    the guard's streaming re-measurement pass raise a raw ``RuntimeError``
    ("... is encrypted, password required ...") from ``archive.open(info)``
    that the guard's own ``except zipfile.BadZipFile`` did not catch --
    escaping past ``_open_worksheet``'s existing ``except Exception`` too
    (a bare ``RuntimeError`` IS an ``Exception``, so that part was fine; the
    bug was entirely inside the guard). Confirmed regression: the exact same
    file opens with ZERO exceptions under plain
    ``openpyxl.load_workbook(read_only=True)`` -- it never reads
    ``docProps/app.xml`` at all -- so the guard was stricter than the real
    reader on a file the real reader considers perfectly fine. Fixed in
    services/dataimport/xlsx_guard.py by catching
    ``(BadZipFile, RuntimeError, NotImplementedError)`` explicitly, never a
    bare ``except Exception``. This adapter must now produce its NORMAL
    parse outcome (whatever that is for this fixture), never crash with a
    raw ``RuntimeError``.
    """
    import struct

    path = _write_workbook(tmp_path, _approach_rows("가람방면"))
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
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    assert wb.sheetnames

    # The regression: this must not raise RuntimeError, only ever AdapterError
    # (if anything) or a normal ImportResult.
    result = adapter.parse([path])
    assert result.counts, "the adapter's normal happy-path outcome for this fixture"

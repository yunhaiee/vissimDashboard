import zipfile
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import openpyxl
import pytest
from fastapi.testclient import TestClient

import main
from services import datasets, workspaces
from services.validation import (
    create_base_workbook_template,
    load_base_workbook,
    load_base_workbook_bytes,
    load_real_counts,
)


def test_base_workbook_template_can_be_parsed():
    content = create_base_workbook_template()
    workbook = openpyxl.load_workbook(BytesIO(content), data_only=True)

    assert {"Counts", "TravelTimes"}.issubset(set(workbook.sheetnames))
    # A "Period" column makes AM/PM explicit (a raw second-based VISSIM TimeInt
    # cannot be auto-classified into a period), and a separate "Direction" column
    # mirrors the VISSIM Node Results export (Movement = node id+name, Direction =
    # the turn). The template ships both AM and PM example rows.
    assert [cell.value for cell in workbook["Counts"][1]] == [
        "SimRun",
        "TimeInt",
        "Period",
        "Movement",
        "Direction",
        "QLen",
        "QLenMax",
        "Vehs(All)",
        "Observed",
    ]
    counts_periods = {row[2] for row in workbook["Counts"].iter_rows(min_row=2, values_only=True) if row[2]}
    assert {"AM", "PM"}.issubset(counts_periods)
    counts_directions = {row[4] for row in workbook["Counts"].iter_rows(min_row=2, values_only=True) if row[4]}
    assert {"N-E", "Total"}.issubset(counts_directions)
    # TravelTimes mirrors the Vissim "Vehicle Travel Time Measurement" export:
    # stacked AM/PM section headers, TravelTime as the model value, an Observed
    # column for the real-world travel time, plus the four validation-band
    # columns (TomTom avg / +/-15% / model output) used by the travel-time chart.
    assert workbook["TravelTimes"]["A1"].value == "AM"
    assert [cell.value for cell in workbook["TravelTimes"][2]] == [
        "SimRun",
        "TimeInt",
        "VehicleTravelTimeMeasurement",
        "Vehs(All)",
        "TravelTime",
        "DisTrav(All)",
        "Observed",
        "TomTom Avg -15%",
        "model output",
        "TomTom avg",
        "TomTom Avg 15%",
    ]


def test_base_workbook_parser_reads_counts_and_travel_times():
    workbook = openpyxl.Workbook()
    counts = workbook.active
    counts.title = "Counts"
    counts.append(["period", "node_id", "node_name", "movement", "observed_volume", "model_volume"])
    counts.append(["AM", "10", "Main", "N-E", 100, 98])
    counts.append(["PM", "10", "Main", "Total", 220, None])

    travel_times = workbook.create_sheet("TravelTimes")
    travel_times.append([
        "period",
        "route_id",
        "route_name",
        "sample_order",
        "time_label",
        "observed_travel_time_sec",
        "model_travel_time_sec",
    ])
    travel_times.append(["AM", "2", "Route 2", 1, "07:00", 90, 94])

    temp_dir = Path(__file__).resolve().parent / ".tmp_validation" / uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = temp_dir / "real_counts.xlsx"
    try:
        workbook.save(path)
        payload = load_base_workbook(str(path))
    finally:
        path.unlink(missing_ok=True)
        temp_dir.rmdir()

    assert payload["exists"] is True
    assert payload["warnings"] == []
    assert payload["counts"] == [
        {
            "period": "AM",
            "node_id": "10",
            "node_name": "Main",
            "movement": "N-E",
            "observed_volume": 100.0,
            "model_volume": 98.0,
        },
        {
            "period": "PM",
            "node_id": "10",
            "node_name": "Main",
            "movement": "Total",
            "observed_volume": 220.0,
            "model_volume": None,
        },
    ]
    assert payload["travel_times"][0]["route_id"] == "2"
    assert payload["travel_times"][0]["observed_travel_time_sec"] == 90.0


def test_base_workbook_parser_reads_simple_counts_format():
    workbook = openpyxl.Workbook()
    counts = workbook.active
    counts.title = "Counts"
    counts.append(["stat_type", "time_range", "node_name", "movement", "model_volume", "observed_volume"])
    counts.append(["Average", "900-4500", "1: 도서관사거리", "-N-E", 73, 72])
    counts.append(["Average", "900-4500", "1: 도서관사거리", "Total", 1723, 1731])
    counts.append([None, None, None, None, None, None])

    travel_times = workbook.create_sheet("TravelTimes")
    travel_times.append([
        "period",
        "route_id",
        "route_name",
        "sample_order",
        "time_label",
        "observed_travel_time_sec",
        "model_travel_time_sec",
    ])

    temp_dir = Path(__file__).resolve().parent / ".tmp_validation" / uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = temp_dir / "real_counts.xlsx"
    try:
        workbook.save(path)
        payload = load_base_workbook(str(path))
    finally:
        path.unlink(missing_ok=True)
        temp_dir.rmdir()

    assert payload["warnings"] == []
    assert payload["counts"] == [
        {
            "period": "AM",
            "node_id": "1",
            "node_name": "도서관사거리",
            "movement": "N-E",
            "observed_volume": 72.0,
            "model_volume": 73.0,
        },
        {
            "period": "AM",
            "node_id": "1",
            "node_name": "도서관사거리",
            "movement": "Total",
            "observed_volume": 1731.0,
            "model_volume": 1723.0,
        },
    ]


def test_base_workbook_parser_reads_vissim_node_results_format():
    workbook = openpyxl.Workbook()
    counts = workbook.active
    counts.title = "Counts"
    counts.append(["SimRun", "TimeInt", "Movement", "QLen", "QLenMax", "Vehs(All)", "Pers(All)", "Observed"])
    counts.append(["Average", "900-4500", "1: 도서관사거리 -N-E", 5.91, 27.03, 72, 64, 72])
    counts.append(["Average", "900-4500", "1: 도서관사거리 Total", 4.49, 86.73, 1722, 1706, 1731])

    travel_times = workbook.create_sheet("TravelTimes")
    travel_times.append([
        "period",
        "route_id",
        "route_name",
        "sample_order",
        "time_label",
        "observed_travel_time_sec",
        "model_travel_time_sec",
    ])

    temp_dir = Path(__file__).resolve().parent / ".tmp_validation" / uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = temp_dir / "real_counts.xlsx"
    try:
        workbook.save(path)
        payload = load_base_workbook(str(path))
    finally:
        path.unlink(missing_ok=True)
        temp_dir.rmdir()

    assert payload["warnings"] == []
    assert payload["counts"] == [
        {
            "period": "AM",
            "node_id": "1",
            "node_name": "도서관사거리",
            "movement": "N-E",
            "observed_volume": 72.0,
            "model_volume": 72.0,
        },
        {
            "period": "AM",
            "node_id": "1",
            "node_name": "도서관사거리",
            "movement": "Total",
            "observed_volume": 1731.0,
            "model_volume": 1722.0,
        },
    ]


def test_base_workbook_parser_reads_split_vissim_node_results_format():
    workbook = openpyxl.Workbook()
    counts = workbook.active
    counts.title = "Counts"
    counts.append([
        "SimRun",
        "TimeInt",
        "Movement",
        "Movement\\Direction",
        "QLen",
        "QLenMax",
        "Vehs(All)",
        "Pers(All)",
        "Observed",
    ])
    counts.append(["Average", "900-4500", "1: 도서관사거리 - 59@17.3 - 90@50.5", "N-E", 5.91, 27.03, 72, 64, 72])
    counts.append(["Average", "900-4500", "1: 도서관사거리", "Total", 4.49, 86.73, 1722, 1706, 1731])

    travel_times = workbook.create_sheet("TravelTimes")
    travel_times.append([
        "period",
        "route_id",
        "route_name",
        "sample_order",
        "time_label",
        "observed_travel_time_sec",
        "model_travel_time_sec",
    ])

    temp_dir = Path(__file__).resolve().parent / ".tmp_validation" / uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = temp_dir / "real_counts.xlsx"
    try:
        workbook.save(path)
        payload = load_base_workbook(str(path))
    finally:
        path.unlink(missing_ok=True)
        temp_dir.rmdir()

    assert payload["warnings"] == []
    assert payload["counts"] == [
        {
            "period": "AM",
            "node_id": "1",
            "node_name": "도서관사거리",
            "movement": "N-E",
            "observed_volume": 72.0,
            "model_volume": 72.0,
        },
        {
            "period": "AM",
            "node_id": "1",
            "node_name": "도서관사거리",
            "movement": "Total",
            "observed_volume": 1731.0,
            "model_volume": 1722.0,
        },
    ]


def test_base_workbook_parser_reads_vissim_travel_time_format():
    """The TravelTimes sheet accepts a raw Vissim travel-time export: AM/PM
    section headers, TravelTime as the model value, Observed as the real value.
    The period must come from the section header because TimeInt is identical
    for AM and PM."""
    workbook = openpyxl.Workbook()
    counts = workbook.active
    counts.title = "Counts"
    counts.append(["SimRun", "TimeInt", "Movement", "QLen", "QLenMax", "Vehs(All)", "Pers(All)", "Observed"])

    travel_times = workbook.create_sheet("TravelTimes")
    travel_times.append(["AM"])
    travel_times.append(["SimRun", "TimeInt", "VehicleTravelTimeMeasurement", "Vehs(All)", "TravelTime", "DisTrav(All)", "Observed"])
    travel_times.append(["Average", "900-4500", "1: RoeHwy,MurdochDr_NB", 342, 225.062166, 2137.159562, 230.0])
    travel_times.append(["Average", "900-4500", "2: MurdochDr,RoeHwy_SB", 63, 263.484780, 2067.991296, None])
    travel_times.append(["PM"])
    # PM block intentionally omits a repeated header to mirror the raw paste.
    travel_times.append(["Average", "900-4500", "1: RoeHwy,MurdochDr_NB", 118, 208.231189, 2137.159562, None])

    temp_dir = Path(__file__).resolve().parent / ".tmp_validation" / uuid4().hex
    temp_dir.mkdir(parents=True, exist_ok=True)
    path = temp_dir / "real_counts.xlsx"
    try:
        workbook.save(path)
        payload = load_base_workbook(str(path))
    finally:
        path.unlink(missing_ok=True)
        temp_dir.rmdir()

    assert payload["warnings"] == []
    assert payload["travel_times"] == [
        {
            "period": "AM",
            "route_id": "1",
            "route_name": "RoeHwy,MurdochDr_NB",
            "sample_order": None,
            "time_label": "900-4500",
            "observed_travel_time_sec": 230.0,
            "model_travel_time_sec": 225.062166,
            "dist_m": 2137.159562,
            "tomtom_avg_sec": None,
            "model_output_sec": None,
            "tomtom_low_sec": None,
            "tomtom_high_sec": None,
        },
        {
            "period": "AM",
            "route_id": "2",
            "route_name": "MurdochDr,RoeHwy_SB",
            "sample_order": None,
            "time_label": "900-4500",
            "observed_travel_time_sec": None,
            "model_travel_time_sec": 263.484780,
            "dist_m": 2067.991296,
            "tomtom_avg_sec": None,
            "model_output_sec": None,
            "tomtom_low_sec": None,
            "tomtom_high_sec": None,
        },
        {
            "period": "PM",
            "route_id": "1",
            "route_name": "RoeHwy,MurdochDr_NB",
            "sample_order": None,
            "time_label": "900-4500",
            "observed_travel_time_sec": None,
            "model_travel_time_sec": 208.231189,
            "dist_m": 2137.159562,
            "tomtom_avg_sec": None,
            "model_output_sec": None,
            "tomtom_low_sec": None,
            "tomtom_high_sec": None,
        },
    ]


def test_base_workbook_endpoint_reports_missing_or_current_workbook():
    with TestClient(main.app) as client:
        response = client.get("/api/validation/base-workbook")

    assert response.status_code == 200
    payload = response.json()
    assert {"exists", "filename", "counts", "travel_times", "warnings"}.issubset(payload.keys())
    assert isinstance(payload["counts"], list)
    assert isinstance(payload["travel_times"], list)


def test_base_workbook_template_endpoint():
    with TestClient(main.app) as client:
        response = client.get("/api/validation/base-workbook/template")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    workbook = openpyxl.load_workbook(BytesIO(response.content), data_only=True)
    assert {"Counts", "TravelTimes"}.issubset(set(workbook.sheetnames))


def test_base_workbook_reset_endpoint_removes_only_uploaded_workbook(tmp_path, monkeypatch):
    # Unit 1.4: the seven ``services.datasets`` path constants this test used to
    # monkeypatch are gone -- every path is derived per call from the workspace
    # bound to the request. Redirecting ``workspaces.WORKSPACES_ROOT`` (and its
    # migration source) is now the one seam, and the accessors below read the
    # same derived paths the endpoint does.
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "legacy-uploads")
    monkeypatch.setattr(datasets, "REAL_COUNTS_EXCEL", tmp_path / "missing_default_real_counts.xlsx")

    with workspaces.bind_workspace(workspaces.default_workspace_id()):
        with TestClient(main.app) as client:
            upload_response = client.post(
                "/api/validation/base-workbook?filename=uploaded.xlsx",
                content=create_base_workbook_template(),
            )
            reset_response = client.delete("/api/validation/base-workbook")

        assert upload_response.status_code == 200
        assert reset_response.status_code == 200
        assert reset_response.json()["exists"] is False
        assert not datasets.active_real_counts_excel().exists()
        assert "real_counts_filename" not in datasets.metadata_path().read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Decompression-bomb guard (adversarial finding, 2026-08-12/13) -- wired into
# services/validation.py's three openpyxl.load_workbook() call sites via
# services.dataimport.xlsx_guard. Full guard coverage (each cap in
# isolation, "lying header" handling, clean pass-through) lives in
# test_xlsx_guard.py; this only proves the WIRING into each of
# load_base_workbook_bytes / load_base_workbook / load_real_counts and the
# POST /api/validation/base-workbook route, using this file's own smallest
# real-shaped fixture (create_base_workbook_template()) -- nothing copied
# from client-data/.
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


def _bomb_bytes(*, declared_bytes: int = 5_000_000) -> bytes:
    """A real .xlsx decompression bomb built on top of THIS module's own
    standard template (``create_base_workbook_template()``), so it is
    otherwise a completely normal base validation workbook:
    ``xl/sharedStrings.xml`` is replaced by a single repeated-character run
    that declares ``declared_bytes`` -- ~1000:1 compressed on disk, tripping
    the guard's ratio cap (60:1) at production thresholds without needing a
    multi-hundred-MB fixture. See services/dataimport/xlsx_guard.py's
    module docstring for how the Content_Types Override is required for
    openpyxl to even look at this part (the template alone never carries
    it).
    """
    src = zipfile.ZipFile(BytesIO(create_base_workbook_template()))
    sst = _BOMB_SST_HEADER + b"A" * declared_bytes + _BOMB_SST_FOOTER
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for info in src.infolist():
            if info.filename == "[Content_Types].xml":
                data = src.read(info.filename)
                if b"sharedStrings" not in data:
                    data = data.replace(b"</Types>", _BOMB_SHARED_STRINGS_OVERRIDE + b"</Types>")
                archive.writestr(info, data)
            else:
                archive.writestr(info, src.read(info.filename))
        archive.writestr("xl/sharedStrings.xml", sst)
    return out.getvalue()


def _lying_header_bytes() -> bytes:
    """A member whose CENTRAL DIRECTORY declares a small size while its
    recorded CRC is for a much larger stream -- see
    services/dataimport/xlsx_guard.py's module docstring for the "does
    zipfile trust the header" investigation this proves: the guard's header
    precheck sees nothing to refuse and defers, and openpyxl/zipfile then
    discover the inconsistency themselves (BadZipFile) when actually read.
    """
    import struct
    import zlib

    src = zipfile.ZipFile(BytesIO(create_base_workbook_template()))
    sst = _BOMB_SST_HEADER + b"B" * 200_000 + _BOMB_SST_FOOTER
    crc_full = zlib.crc32(sst) & 0xFFFFFFFF
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for info in src.infolist():
            if info.filename == "[Content_Types].xml":
                data = src.read(info.filename)
                if b"sharedStrings" not in data:
                    data = data.replace(b"</Types>", _BOMB_SHARED_STRINGS_OVERRIDE + b"</Types>")
                archive.writestr(info, data)
            else:
                archive.writestr(info, src.read(info.filename))
        archive.writestr("xl/sharedStrings.xml", sst)

    raw = bytearray(out.getvalue())
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
    return bytes(raw)


def _write_bytes_to_disk(tmp_path: Path, content: bytes, name: str = "workbook.xlsx") -> Path:
    path = tmp_path / name
    path.write_bytes(content)
    return path


def test_load_base_workbook_bytes_refuses_the_bomb_by_name_with_the_numbers():
    result = load_base_workbook_bytes("bomb.xlsx", _bomb_bytes(declared_bytes=5_000_000))
    assert result["exists"] is False
    assert len(result["warnings"]) == 1
    assert "5,000," in result["warnings"][0]
    assert "cap" in result["warnings"][0]


def test_load_base_workbook_bytes_still_parses_the_smallest_real_fixture():
    result = load_base_workbook_bytes("real_counts.xlsx", create_base_workbook_template())
    assert result["warnings"] == []
    assert result["counts"]


def test_load_base_workbook_bytes_refuses_a_lying_declared_size():
    result = load_base_workbook_bytes("lying.xlsx", _lying_header_bytes())
    assert result["exists"] is False
    assert len(result["warnings"]) == 1  # openpyxl's own BadZipFile, via the existing except Exception


def test_load_base_workbook_disk_refuses_the_bomb_by_name_with_the_numbers(tmp_path):
    path = _write_bytes_to_disk(tmp_path, _bomb_bytes(declared_bytes=5_000_000), "bomb.xlsx")
    result = load_base_workbook(str(path))
    assert result["exists"] is True
    assert len(result["warnings"]) == 1
    assert "5,000," in result["warnings"][0]


def test_load_base_workbook_disk_still_parses_the_smallest_real_fixture(tmp_path):
    path = _write_bytes_to_disk(tmp_path, create_base_workbook_template(), "real_counts.xlsx")
    result = load_base_workbook(str(path))
    assert result["warnings"] == []
    assert result["counts"]


def test_load_real_counts_raises_on_the_bomb_with_the_numbers(tmp_path):
    """Unlike load_base_workbook{,_bytes}, this call site has no existing
    try/except around openpyxl.load_workbook() -- the guard's typed
    XlsxBombError propagates uncaught, exactly as any other openpyxl
    exception already would here. Still a clear, named, non-silent
    failure, just not folded into a warnings list like its siblings."""
    from services.dataimport.xlsx_guard import XlsxBombError

    path = _write_bytes_to_disk(tmp_path, _bomb_bytes(declared_bytes=5_000_000), "bomb.xlsx")
    with pytest.raises(XlsxBombError) as exc_info:
        load_real_counts(str(path))
    assert "5,000," in str(exc_info.value)


def test_load_real_counts_still_parses_the_smallest_real_fixture(tmp_path):
    path = _write_bytes_to_disk(tmp_path, create_base_workbook_template(), "real_counts.xlsx")
    real_counts = load_real_counts(str(path))
    assert real_counts  # the template's own sample rows resolve to at least one node


def test_post_base_workbook_endpoint_refuses_the_bomb_with_400():
    with TestClient(main.app) as client:
        response = client.post(
            "/api/validation/base-workbook?filename=bomb.xlsx",
            content=_bomb_bytes(declared_bytes=5_000_000),
        )
    assert response.status_code == 400
    assert "5,000," in response.text

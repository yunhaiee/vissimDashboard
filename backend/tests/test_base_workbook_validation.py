from io import BytesIO
from pathlib import Path
from uuid import uuid4

import openpyxl
from fastapi.testclient import TestClient

import main
from services import datasets, workspaces
from services.validation import create_base_workbook_template, load_base_workbook


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

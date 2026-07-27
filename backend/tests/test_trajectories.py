"""Tests for Phase 3 P3's vehicle-trajectory data layer: the .fzp parser +
geometry resolver in ``services/trajectory_reader.py`` and the
``GET /api/trajectories`` endpoint in ``routers/trajectories.py``.

No real ``.fzp`` file exists on this machine (no COM, no lab box) -- the
parser is written from Vissim's documented convention and validated here with
inline fixtures covering header-driven column mapping (including reordered
columns), malformed-row tolerance, and the lane-unknown fallback. See
``docs/phase2/DECISIONS.md``'s dated entry for this unit.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
import services.trajectory_reader as trajectory_reader
from routers import trajectories as trajectories_router

BASIC_HEADER = "$VEHICLE:SIMSEC;NO;LINK;LANE;POS;SPEED"


def _write_fzp(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# parse_fzp: header-driven column mapping
# ---------------------------------------------------------------------------


def test_parse_fzp_reads_header_driven_column_order(tmp_path: Path):
    path = tmp_path / "reordered.fzp"
    _write_fzp(
        path,
        [
            "* Vissim-style comment header",
            "$VEHICLE:LANE;POS;NO;LINK;SIMSEC;SPEED",
            "1;5.00;10;101;0.0;42.5",
            "2;12.50;11;101;0.5;40.0",
        ],
    )

    result = trajectory_reader.parse_fzp(path)

    assert result["meta"]["skipped_rows"] == 0
    assert result["meta"]["vehicle_count"] == 2
    assert result["meta"]["t0"] == 0.0
    assert result["meta"]["t1"] == 0.5
    assert result["meta"]["step"] == 0.5
    assert len(result["frames"]) == 2

    frame0 = result["frames"][0]
    assert frame0["t"] == 0.0
    assert frame0["vehicles"] == [{"id": 10, "link": "101", "lane": 1, "pos": 5.0, "speed": 42.5}]

    frame1 = result["frames"][1]
    assert frame1["vehicles"] == [{"id": 11, "link": "101", "lane": 2, "pos": 12.5, "speed": 40.0}]


def test_parse_fzp_without_vehicle_header_skips_every_data_line(tmp_path: Path):
    path = tmp_path / "no_header.fzp"
    _write_fzp(path, ["* no $VEHICLE header at all", "0.0;1;101;1;10.0;40.0"])

    result = trajectory_reader.parse_fzp(path)

    assert result["frames"] == []
    assert result["meta"]["vehicle_count"] == 0
    assert result["meta"]["skipped_rows"] == 1


def test_parse_fzp_tolerates_other_dollar_lines_between_header_and_data(tmp_path: Path):
    path = tmp_path / "extra_dollar.fzp"
    _write_fzp(
        path,
        [
            "* header block",
            BASIC_HEADER,
            "$SIMULATION: SomeUnrelatedExportSection",
            "0.0;1;101;1;10.0;40.0",
        ],
    )

    result = trajectory_reader.parse_fzp(path)

    assert result["meta"]["skipped_rows"] == 0
    assert result["meta"]["vehicle_count"] == 1


# ---------------------------------------------------------------------------
# parse_fzp: malformed rows are skipped and counted, never raise
# ---------------------------------------------------------------------------


def test_parse_fzp_skips_malformed_rows_but_keeps_unknown_lane(tmp_path: Path):
    path = tmp_path / "malformed.fzp"
    _write_fzp(
        path,
        [
            BASIC_HEADER,
            "0.0;1;101;1;10.0;40.0",           # good
            "not-a-number;2;101;1;5.0;30.0",   # bad SIMSEC -> skipped
            "0.5;3;101;;20.0;35.0",            # blank LANE -> lane=None, row kept
            "1.0;4;101;1;bad-pos;20.0",        # bad POS -> skipped
            "1.5;5",                           # too few fields -> skipped
            "2.0;;101;1;15.0;25.0",            # blank NO -> skipped
        ],
    )

    result = trajectory_reader.parse_fzp(path)

    assert result["meta"]["skipped_rows"] == 4
    assert result["meta"]["vehicle_count"] == 2

    veh3 = next(v for frame in result["frames"] for v in frame["vehicles"] if v["id"] == 3)
    assert veh3["lane"] is None
    assert veh3["pos"] == 20.0


def test_parse_fzp_cache_invalidates_on_mtime_change(tmp_path: Path):
    path = tmp_path / "cache.fzp"
    _write_fzp(path, [BASIC_HEADER, "0.0;1;101;1;10.0;40.0"])
    os.utime(path, (1_700_000_000, 1_700_000_000))

    first = trajectory_reader.parse_fzp(path)
    assert first["meta"]["vehicle_count"] == 1

    _write_fzp(path, [BASIC_HEADER, "0.0;1;101;1;10.0;40.0", "0.0;2;101;1;20.0;40.0"])
    os.utime(path, (1_700_000_100, 1_700_000_100))

    second = trajectory_reader.parse_fzp(path)
    assert second["meta"]["vehicle_count"] == 2


# ---------------------------------------------------------------------------
# resolve_frames: lane offset, fallback to centerline, unknown-link skipping
# ---------------------------------------------------------------------------


def test_resolve_frames_uses_lane_offset_and_falls_back_when_lane_unknown():
    links = [
        {"id": "1", "points": [[0.0, 0.0], [100.0, 0.0]], "lane_widths": [3.5, 3.5]},
    ]
    frames = [
        {
            "t": 0.0,
            "vehicles": [
                {"id": 1, "link": "1", "lane": 1, "pos": 50.0, "speed": 40.0},
                {"id": 2, "link": "1", "lane": None, "pos": 50.0, "speed": 40.0},
                {"id": 3, "link": "999", "lane": 1, "pos": 10.0, "speed": 10.0},  # unknown link
            ],
        },
    ]

    resolved, skipped = trajectory_reader.resolve_frames(frames, links)

    assert skipped == 1
    assert len(resolved) == 1  # the frame itself is never dropped
    by_id = {v["id"]: v for v in resolved[0]["vehicles"]}
    assert set(by_id) == {1, 2}

    # Lane 1 of 2 (each 3.5m) offsets off the centerline; lane-unknown falls
    # back to the plain centerline point.
    assert by_id[1]["x"] == 50.0
    assert by_id[1]["y"] == -1.75
    assert by_id[2]["x"] == 50.0
    assert by_id[2]["y"] == 0.0
    assert by_id[1]["angle"] == 0.0
    assert by_id[2]["angle"] == 0.0


def test_resolve_frames_keeps_empty_frame_when_every_vehicle_unresolvable():
    frames = [{"t": 1.0, "vehicles": [{"id": 1, "link": "999", "lane": 1, "pos": 0.0, "speed": 0.0}]}]

    resolved, skipped = trajectory_reader.resolve_frames(frames, [])

    assert skipped == 1
    assert resolved == [{"t": 1.0, "vehicles": []}]


# ---------------------------------------------------------------------------
# GET /api/trajectories
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture()
def demo_trajectory_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the router's demo-file fallback at a small inline fixture.

    The ambient dataset isolation (conftest.py) keeps ``get_active_inpx_path``
    resolving to the real ``data/practice.inpx`` demo network during tests --
    link ``101`` ("Main St EB approach") genuinely exists there, so resolution
    against the real parser is exercised end-to-end, not mocked away. Link
    ``999`` does not exist, exercising the unresolved-vehicle count.
    """
    path = tmp_path / "demo_trajectories.fzp"
    _write_fzp(
        path,
        [
            "* synthetic test fixture",
            BASIC_HEADER,
            "0.0;1;101;1;10.00;40.0",
            "0.0;2;999;1;5.00;30.0",
            "0.5;1;101;1;15.00;40.0",
            "1.0;1;101;1;20.00;40.0",
            "1.5;1;101;1;25.00;40.0",
        ],
    )
    monkeypatch.setattr(trajectories_router, "DEMO_TRAJECTORY_PATH", path)
    return path


def test_trajectories_endpoint_smoke(client: TestClient, demo_trajectory_file: Path):
    response = client.get("/api/trajectories")
    assert response.status_code == 200
    payload = response.json()

    assert payload["meta"]["source_file"] == demo_trajectory_file.name
    assert payload["meta"]["vehicle_count"] == 2
    assert payload["meta"]["skipped_rows"] == 0
    assert payload["meta"]["skipped_unresolved_vehicles"] == 1
    assert payload["meta"]["returned_frame_count"] == len(payload["frames"])
    assert payload["meta"]["stride"] == 1
    assert len(payload["frames"]) == 4

    frame0 = payload["frames"][0]
    assert frame0["t"] == 0.0
    veh_ids = {v["id"] for v in frame0["vehicles"]}
    assert veh_ids == {1}  # vehicle 2 (unknown link 999) was dropped

    veh1 = frame0["vehicles"][0]
    assert {"x", "y", "angle"}.issubset(veh1.keys())


def test_trajectories_endpoint_window_and_stride_params(client: TestClient, demo_trajectory_file: Path):
    windowed = client.get("/api/trajectories", params={"t0": 0.5, "t1": 1.0})
    assert windowed.status_code == 200
    windowed_payload = windowed.json()
    assert [f["t"] for f in windowed_payload["frames"]] == [0.5, 1.0]
    assert windowed_payload["meta"]["t0_filter"] == 0.5
    assert windowed_payload["meta"]["t1_filter"] == 1.0

    strided = client.get("/api/trajectories", params={"stride": 2})
    assert strided.status_code == 200
    strided_payload = strided.json()
    assert [f["t"] for f in strided_payload["frames"]] == [0.0, 1.0]
    assert strided_payload["meta"]["stride"] == 2


def test_trajectories_endpoint_404_when_no_file_exists(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(trajectories_router, "DEMO_TRAJECTORY_PATH", tmp_path / "does_not_exist.fzp")

    response = client.get("/api/trajectories")

    assert response.status_code == 404
    assert "trajectory" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Real Vissim export format (backend/tools/vehicle_record_probe.py, live-
# verified against Vissim 2026 -- see docs/phase2/DECISIONS.md): a UTF-8 BOM
# glued to a "$VISION" marker line, and column names
# "LANE\LINK\NO"/"LANE\INDEX" instead of bare "LINK"/"LANE", with no SPEED
# column in the default attribute selection at all.
# ---------------------------------------------------------------------------

REAL_VEHICLE_HEADER = "$VEHICLE:SIMSEC;NO;LANE\\LINK\\NO;LANE\\INDEX;POS"


def _write_real_format_fzp(path: Path) -> None:
    """A canned fixture mirroring the EXACT verbatim structure captured live
    (backend/tools/vehicle_record_probe.py's saved probe output): BOM before
    $VISION, comment block, $VEHICLE: header using the real column names, no
    SPEED column."""
    content = (
        "﻿$VISION\n"
        "* File:\t\tC:\\some\\path\\Intersection Beijing.inpx\n"
        "* Comment:\tUrban intersection Beijing\n"
        "* Table: Vehicles In Network\n"
        "*\n"
        f"{REAL_VEHICLE_HEADER}\n"
        "0.90;1;101;1;0.18\n"
        "1.00;1;101;1;0.56\n"
        "1.00;2;999;1;5.00\n"  # unknown link -- exercises resolver drop
    )
    path.write_bytes(content.encode("utf-8"))


def test_parse_fzp_reads_the_real_vissim_export_format(tmp_path: Path):
    """Proves format DETECTION (not a second pipeline): the real column
    names + BOM-glued $VISION line + absent SPEED all parse correctly."""
    path = tmp_path / "real_format.fzp"
    _write_real_format_fzp(path)

    result = trajectory_reader.parse_fzp(path)

    assert result["meta"]["skipped_rows"] == 0
    assert result["meta"]["vehicle_count"] == 2
    assert len(result["frames"]) == 2

    frame0 = result["frames"][0]
    assert frame0["t"] == 0.9
    assert frame0["vehicles"] == [{"id": 1, "link": "101", "lane": 1, "pos": 0.18, "speed": None}]

    frame1 = result["frames"][1]
    veh_ids = {v["id"] for v in frame1["vehicles"]}
    assert veh_ids == {1, 2}
    veh1 = next(v for v in frame1["vehicles"] if v["id"] == 1)
    assert veh1 == {"id": 1, "link": "101", "lane": 1, "pos": 0.56, "speed": None}


def test_trajectories_endpoint_serves_the_real_format_fixture(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """End-to-end (parser + resolver + endpoint) proof against the real
    Vissim column-name shape, not just the synthetic/original one."""
    path = tmp_path / "real_format.fzp"
    _write_real_format_fzp(path)
    monkeypatch.setattr(trajectories_router, "DEMO_TRAJECTORY_PATH", path)

    response = client.get("/api/trajectories")

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["skipped_rows"] == 0
    assert payload["meta"]["skipped_unresolved_vehicles"] == 1  # link 999
    frame0 = payload["frames"][0]
    veh1 = frame0["vehicles"][0]
    assert veh1["speed"] is None
    assert {"x", "y", "angle"}.issubset(veh1.keys())  # still resolved via LINK alias


# ---------------------------------------------------------------------------
# GET /api/trajectories?job_id=... -- replaying a completed agent-run job's
# own recorded trajectory (routers/simulations.py's trajectory_recorded).
# ---------------------------------------------------------------------------


def test_trajectories_endpoint_job_id_404_when_job_not_found(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(trajectories_router, "get_job_record", lambda job_id: None)

    response = client.get("/api/trajectories", params={"job_id": "does-not-exist"})

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_trajectories_endpoint_job_id_409_when_job_not_done(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        trajectories_router, "get_job_record", lambda job_id: {"id": job_id, "status": "running"}
    )

    response = client.get("/api/trajectories", params={"job_id": "job1"})

    assert response.status_code == 409


def test_trajectories_endpoint_job_id_404_when_trajectory_not_recorded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        trajectories_router,
        "get_job_record",
        lambda job_id: {"id": job_id, "status": "done", "trajectory_recorded": False},
    )

    response = client.get("/api/trajectories", params={"job_id": "job1"})

    assert response.status_code == 404
    assert "no recorded vehicle trajectory" in response.json()["detail"].lower()


def test_trajectories_endpoint_job_id_409_on_dataset_revision_mismatch(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        trajectories_router,
        "get_job_record",
        lambda job_id: {
            "id": job_id,
            "status": "done",
            "trajectory_recorded": True,
            "trajectory_paths": ["/some/path.fzp"],
            "dataset_revision": "old-revision",
        },
    )
    monkeypatch.setattr(trajectories_router, "get_active_dataset", lambda: {"revision": "new-revision"})

    response = client.get("/api/trajectories", params={"job_id": "job1"})

    assert response.status_code == 409
    assert "different dataset revision" in response.json()["detail"].lower()


def test_trajectories_endpoint_job_id_serves_the_recorded_file(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fzp_path = tmp_path / "job_run_001.fzp"
    _write_real_format_fzp(fzp_path)

    revision = "matching-revision"
    monkeypatch.setattr(
        trajectories_router,
        "get_job_record",
        lambda job_id: {
            "id": job_id,
            "status": "done",
            "trajectory_recorded": True,
            "trajectory_paths": [str(fzp_path)],
            "dataset_revision": revision,
        },
    )
    monkeypatch.setattr(trajectories_router, "get_active_dataset", lambda: {"revision": revision})

    response = client.get("/api/trajectories", params={"job_id": "job1"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["meta"]["source_file"] == fzp_path.name
    assert payload["meta"]["job_id"] == "job1"
    assert payload["meta"]["skipped_rows"] == 0

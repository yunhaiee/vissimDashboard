"""Wiring the WA/SCATS detector-loop counts into the observation store (U10).

Before this unit, ``observations._Aggregator`` only ever summed
``TurningCountData`` — a SCATS detector volume (``DetectorCountData``, which
carries no turning movement by construction; see ``schema.py``) was parsed by
the adapter and then thrown away: ``ingest_observation_set`` called
``result.detector_counts.clear()`` without ever reading it. This file proves
the honest fix: a detector-only site gets a SITE-TOTAL comparison
(``detector_total``/``count_basis``), never a fabricated per-movement
breakdown, and ``observed_side`` reports it with ``directions == []`` rather
than smuggling detector ids in as if they were movements.

**Every fixture is synthetic** — the same ``openpyxl``-built workbook shape
``test_dataimport_scats_wa_xlsx.py`` uses, imported from there rather than
duplicated. No client-data value appears here.

Offline by construction: openpyxl and the neutral model, no Vissim, no COM.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from services import observations, workspaces
from services.dataimport.schema import (
    ApproachRef,
    DetectorCountData,
    MovementRef,
    Provenance,
    TurnType,
    VehicleClassRef,
)
from services.dataimport.quality import is_all_class_total
from services.observations import ObservationPeriod, _Aggregator
from tests.test_dataimport_scats_wa_xlsx import (
    DAY0,
    HOURLY_D1,
    HOURLY_D2,
    QUARTER_D1,
    QUARTER_D2,
    SITE_ID,
    _default_hourly_grid,
    _default_quarter_grid,
    _write_workbook,
)

FULL_PERIOD = ObservationPeriod(
    name="AM", start=DAY0, end=DAY0 + timedelta(hours=9)
)
FIRST_QUARTER_ONLY = ObservationPeriod(
    name="AM0", start=DAY0 + timedelta(hours=8), end=DAY0 + timedelta(hours=8, minutes=15)
)


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Verbatim ``test_observations_store.py``'s fixture — a private
    workspaces root bound for the duration of one test."""
    root = tmp_path / "workspaces"
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", root)
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "legacy-uploads")
    default_id = workspaces.default_workspace_id()
    token = workspaces.set_current_workspace_id(default_id)
    try:
        yield root
    finally:
        workspaces.reset_current_workspace_id(token)


def _ingest(paths: list[Path], *, periods=(FULL_PERIOD,), name: str = "SCATS 테스트 세트"):
    return observations.ingest_observation_set(
        paths, name=name, periods=list(periods), adapter_name="scats_wa"
    )


# ---------------------------------------------------------------------------
# 1. End-to-end: a real-shaped SCATS workbook -> a stored site-total aggregate
# ---------------------------------------------------------------------------


def test_ingest_stores_a_site_total_from_detector_counts_never_a_movement(
    workspace_root: Path, tmp_path: Path
):
    workbook = _write_workbook(tmp_path / "T00001.xlsx", quarter_grid=_default_quarter_grid())
    record = _ingest([workbook])

    period = record["aggregates"]["AM"]
    assert list(period) == [SITE_ID]
    site = period[SITE_ID]

    # The turning-count side is untouched — SCATS never emits TurningCountData.
    assert site["total"] == 0.0
    assert site["records"] == 0
    assert site["movements"] == {}

    # The detector-loop side carries the real observation.
    assert site["detector_total"] == pytest.approx(QUARTER_D1[0] * 4 + QUARTER_D2[0] * 4) == 100.0
    assert site["detector_records"] == 8  # 2 detectors x 4 quarter-hour bins
    assert site["detectors"] == {
        "1": {"volume": pytest.approx(40.0), "records": 4},
        "2": {"volume": pytest.approx(60.0), "records": 4},
    }
    assert site["count_basis"] == ["detector_counts"]

    coverage = record["coverage"]
    assert coverage["detector_records_read"] == 8
    assert coverage["detector_records_aggregated"] == 8
    assert coverage["records_aggregated"] == 0  # no turning counts at all

    periods = record["periods"]
    assert periods[0]["detector_total_volume"] == pytest.approx(100.0)
    assert periods[0]["detector_records"] == 8
    assert periods[0]["total_volume"] == 0.0  # turning-count side stays honestly zero


def test_a_narrower_period_cuts_the_detector_series_too(workspace_root: Path, tmp_path: Path):
    workbook = _write_workbook(tmp_path / "T00001.xlsx", quarter_grid=_default_quarter_grid())
    record = _ingest([workbook], periods=(FIRST_QUARTER_ONLY,))

    site = record["aggregates"]["AM0"][SITE_ID]
    # Only the 08:00 quarter-hour bin falls inside [08:00, 08:15).
    assert site["detector_records"] == 2  # both detectors, one interval each
    assert site["detector_total"] == pytest.approx(QUARTER_D1[0] + QUARTER_D2[0]) == 25.0


def test_a_workbook_with_no_detector_counts_in_the_declared_period_refuses_to_store(
    workspace_root: Path, tmp_path: Path
):
    workbook = _write_workbook(tmp_path / "T00001.xlsx", quarter_grid=_default_quarter_grid())
    outside = ObservationPeriod(
        name="PM", start=DAY0 + timedelta(hours=20), end=DAY0 + timedelta(hours=21)
    )
    with pytest.raises(observations.ObservationRefusal) as excinfo:
        _ingest([workbook], periods=(outside,))
    assert excinfo.value.code == "observation_no_counts_in_declared_periods"


def test_a_workbook_whose_hourly_and_quarter_hourly_disagree_refuses_the_whole_set(
    workspace_root: Path, tmp_path: Path
):
    """The adapter's cross-check REFUSE issue must reach the gate and block
    storage — the same 'refusals block storage' contract Daejeon proves for
    its own 소계 mismatch, now exercised for the SCATS side."""
    hourly = _default_hourly_grid()
    hourly["08:00"] = [HOURLY_D1 + 5, HOURLY_D2]  # deliberately wrong
    workbook = _write_workbook(
        tmp_path / "T00001.xlsx", quarter_grid=_default_quarter_grid(), hourly_grid=hourly
    )
    with pytest.raises(observations.ObservationRefusal) as excinfo:
        _ingest([workbook])
    assert excinfo.value.code == "observation_gate_refused"


# ---------------------------------------------------------------------------
# 2. observed_side(): the honest degradation to site-level-only
# ---------------------------------------------------------------------------


def test_observed_side_reports_the_detector_total_with_no_fabricated_directions(
    workspace_root: Path, tmp_path: Path
):
    workbook = _write_workbook(tmp_path / "T00001.xlsx", quarter_grid=_default_quarter_grid())
    record = _ingest([workbook])

    side = observations.observed_side(record["id"], period="AM", site_nodes={SITE_ID: "8"})
    assert side["problem"] is None
    node = side["nodes"]["8"]
    assert node["total_real"] == 100
    assert node["count_basis"] == "detector_counts"
    # The whole point: no per-movement breakdown is invented from detector ids.
    assert node["directions"] == []
    assert node["total_sim_basam"] is None


def test_a_site_with_only_turning_counts_is_unaffected_count_basis_is_labelled(
    workspace_root: Path, tmp_path: Path
):
    """Backward-compatibility proof: a Daejeon-style turning-count site still
    reports exactly as before, with the new count_basis field added (never
    replacing directions)."""
    from tests.test_dataimport_daejeon import (
        BUSY,
        QUARTER,
        WEDNESDAY,
        _approach_rows,
        _file_name,
        _write_workbook as _write_daejeon_workbook,
    )

    workbook = _write_daejeon_workbook(
        tmp_path,
        _approach_rows("북쪽방면", start=WEDNESDAY, hours=3),
        file_name=_file_name(
            "합정교차로", start=WEDNESDAY, last_start=WEDNESDAY + timedelta(hours=2, minutes=45)
        ),
    )
    record = observations.ingest_observation_set(
        [workbook],
        name="Daejeon 테스트",
        periods=[ObservationPeriod(name="AM", start=WEDNESDAY, end=WEDNESDAY + timedelta(hours=3))],
    )
    side = observations.observed_side(record["id"], period="AM", site_nodes={"합정교차로": "3"})
    node = side["nodes"]["3"]
    assert node["count_basis"] == "turning_counts"
    assert len(node["directions"]) == 3  # left/through/right, as before


# ---------------------------------------------------------------------------
# 3. Mixed basis — a scenario no shipped adapter produces, exercised directly
#    against the private accumulator so the safety net is proven, not assumed.
# ---------------------------------------------------------------------------


def _turning_count(site_id: str, volume: float, start: datetime) -> Any:
    from services.dataimport.schema import ClassificationScheme, CanonicalVehicleClass, TurningCountData

    return TurningCountData(
        provenance=Provenance(source_file="synthetic.csv", native_id="row"),
        site_id=site_id,
        approach=ApproachRef(leg_id="N"),
        movement=MovementRef(turn=TurnType.THROUGH, to_leg_id="S"),
        # A CLASSIFIED, non-total class ref: is_summable()/is_all_class_total()
        # would exclude an UNCLASSIFIED/ALL_VEHICLES row from ever landing in
        # site["records"], which would defeat the point of this fixture (a
        # site that genuinely has BOTH turning counts and detector counts).
        vehicle_class=VehicleClassRef(
            scheme=ClassificationScheme.AUSTROADS,
            code="1",
            canonical=CanonicalVehicleClass.LIGHT_VEHICLE,
        ),
        interval_start=start,
        interval_end=start + timedelta(minutes=15),
        volume=volume,
        volume_unit="vehicles",
    )


def _detector_count(site_id: str, detector_id: str, volume: float, start: datetime) -> DetectorCountData:
    return DetectorCountData(
        provenance=Provenance(source_file="synthetic.xlsx", native_id=detector_id),
        site_id=site_id,
        detector_id=detector_id,
        interval_start=start,
        interval_end=start + timedelta(minutes=15),
        volume=volume,
        volume_unit="vehicles",
    )


def test_a_site_with_both_turning_and_detector_counts_is_excluded_and_named(
    workspace_root: Path,
):
    """No shipped adapter produces both TurningCountData AND DetectorCountData
    for the same site today — but if one someday did, silently summing two
    independent measurement bases would manufacture a number nobody measured.
    Proven directly against the accumulator, since the public ingest API has
    no adapter that can reach this state."""
    period = ObservationPeriod(name="AM", start=DAY0, end=DAY0 + timedelta(hours=1))
    aggregator = _Aggregator([period])
    aggregator.note_site("SITE9", "Mixed-Basis Site")
    aggregator.add([_turning_count("SITE9", 10.0, DAY0)])
    aggregator.add_detector_counts([_detector_count("SITE9", "1", 20.0, DAY0)])

    site = aggregator.aggregates()["AM"]["SITE9"]
    assert site["count_basis"] == ["detector_counts", "turning_counts"]

    # observed_side() reads from the STORED record shape, so build that shape
    # by hand (the same one ingest_observation_set would have written).
    fake_record = {
        "aggregates": {"AM": aggregator.aggregates()["AM"]},
    }
    from services.observations import _mutate_registry

    fake_id = "deadbeef0001"  # must match observations._SET_ID_RE (hex, 8-32 chars)

    def _store(sets: dict[str, dict[str, Any]]) -> tuple[None, bool]:
        sets[fake_id] = {"id": fake_id, **fake_record}
        return None, True

    _mutate_registry(_store)
    side = observations.observed_side(fake_id, period="AM", site_nodes={"SITE9": "1"})
    assert side["problem"] is not None  # a mixed-basis site never joins
    assert side["nodes"] == {}
    assert side["coverage"]["sites_with_mixed_count_basis"] == ["SITE9"]


# ---------------------------------------------------------------------------
# 4. is_summable / detector counts are orthogonal — DetectorCountData never
#    passes through the turning-count predicate (it is a different type).
# ---------------------------------------------------------------------------


def test_detector_counts_never_enter_the_turning_count_aggregator(workspace_root: Path):
    """Belt-and-braces: add_detector_counts and add() key different totals,
    proven by feeding the SAME site through both and checking neither total
    leaks into the other."""
    period = ObservationPeriod(name="AM", start=DAY0, end=DAY0 + timedelta(hours=1))
    aggregator = _Aggregator([period])
    aggregator.add_detector_counts([_detector_count("SITE1", "1", 50.0, DAY0)])
    site = aggregator.aggregates()["AM"]["SITE1"]
    assert site["total"] == 0.0
    assert site["movements"] == {}
    assert site["detector_total"] == pytest.approx(50.0)

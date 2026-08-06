"""Unit U4a: the synthetic OBSERVED side of the demo dataset.

``backend/tools/make_demo_data.py`` used to emit result databases (the MODEL
side) and no counts workbook at all, so CLAUDE.md's synthetic-data rule ("non-
COM code must work against ``make_demo_data.py``'s output") could not actually
be honoured for anything on the validation / calibration surface — there was
nothing observed to compare a run against. U4a adds ``real_counts.xlsx``.

What this file pins, and why each pin is the one that matters:

* the workbook parses through the **REAL** loader
  (``services.validation.load_base_workbook``) with **zero warnings** — a
  fixture that only its own bespoke reader can read is not a fixture;
* each designed node lands in its **designed GEH band** via the real
  ``services.validation.geh``, so all three of unit U4's outcomes
  (demand proposal / routing proposal / refusal) are reachable from this
  fixture alone;
* the observed values are **derived from the modelled ones**, so the two sides
  cannot drift apart — the test recomputes the transform from the result .db's
  own rows rather than trusting a literal;
* regenerating produces **byte-identical** output, including the pre-existing
  practice.inpx / .vissimpdb / result .dbs (U4a is additive, and a fixture that
  churns is a fixture nobody can diff) — and that is proven ACROSS A REAL
  WALL-CLOCK GAP, because the two generations landing in the same second is
  exactly how the workbook's live ``dcterms:modified`` stamp hid for months.
"""
from __future__ import annotations

import datetime
import sqlite3
import sys
import time
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from services import validation
from tools import make_demo_data


# ---------------------------------------------------------------------------
# One generated dataset for the whole module (regeneration is ~1s but there is
# no reason to pay it per test).
# ---------------------------------------------------------------------------


def _generate(out: Path) -> Path:
    argv = sys.argv
    sys.argv = ["make_demo_data.py", "--out", str(out)]
    try:
        make_demo_data.main()
    finally:
        sys.argv = argv
    return out


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _generate(tmp_path_factory.mktemp("demo-data"))


@pytest.fixture(scope="module")
def workbook(demo_dir: Path) -> dict:
    # load_base_workbook is @lru_cache'd on its path argument; a module-scoped
    # tmp dir is unique per run so there is nothing stale to clear.
    return validation.load_base_workbook(str(demo_dir / "real_counts.xlsx"))


@pytest.fixture(scope="module")
def baseline_movement_vehs(demo_dir: Path) -> dict[str, float]:
    """The MODELLED per-movement volumes the workbook was derived from, read
    back out of the .db itself — never re-derived here."""
    db = demo_dir / "Scenarios" / "S000001" / "S000001.results" / "run_001.db"
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT OBJECT_ID, VEHS FROM MOVEMENT_EvaluationTimeIntervalClass "
            "WHERE ARG_VEHICLECLASS = 'ALL'"
        ).fetchall()
    finally:
        conn.close()
    return {str(object_id): float(vehs) for object_id, vehs in rows}


def _nodes(workbook: dict, period: str) -> dict[str, validation.NodeRealCounts]:
    """Aggregate the loader's normalized count rows into per-node totals.

    The same call ``load_real_counts`` makes internally — used directly here so
    the test reads the loader's OWN aggregation rather than re-summing rows.
    """
    return validation._real_counts_from_count_rows(workbook["counts"], period)


# ---------------------------------------------------------------------------
# It exists, and the REAL loader accepts it
# ---------------------------------------------------------------------------


def test_workbook_is_written_next_to_the_rest_of_the_demo_data(demo_dir: Path):
    path = demo_dir / "real_counts.xlsx"
    assert path.is_file()
    # config.REAL_COUNTS_EXCEL defaults to <data dir>/real_counts.xlsx, so the
    # filename is not cosmetic: it is what services.datasets falls back to when
    # no workbook has been uploaded through the dashboard.
    assert path.name == "real_counts.xlsx"


def test_real_loader_parses_the_workbook_with_no_warnings(workbook: dict):
    assert workbook["exists"] is True
    assert workbook["warnings"] == []
    assert workbook["sheets"] == ["Counts", "TravelTimes"]
    assert workbook["periods"] == ["AM", "PM"]
    assert workbook["counts"]
    assert workbook["travel_times"]


def test_counts_rows_carry_the_standard_normalized_contract(workbook: dict):
    row = workbook["counts"][0]
    assert set(row) == {
        "period", "node_id", "node_name", "movement", "observed_volume", "model_volume",
    }
    assert {r["period"] for r in workbook["counts"]} == {"AM", "PM"}
    # Every node carries an explicit Total row (the loader prefers it over
    # summing directions).
    for period in ("AM", "PM"):
        totals = {r["node_id"] for r in workbook["counts"]
                  if r["period"] == period and r["movement"] == "Total"}
        assert totals == {"1", "2", "3"}


def test_travel_times_parse_through_the_vissim_export_branch(workbook: dict):
    rows = workbook["travel_times"]
    assert {r["period"] for r in rows} == {"AM", "PM"}
    assert {r["route_id"] for r in rows} == {"1", "2"}
    for row in rows:
        assert row["model_travel_time_sec"] is not None
        assert row["observed_travel_time_sec"] is not None
        assert row["dist_m"] == 1000.0
        # The four mm:ss validation columns survive as seconds.
        assert row["tomtom_avg_sec"] == pytest.approx(row["observed_travel_time_sec"], abs=1.0)
        assert row["tomtom_low_sec"] < row["tomtom_avg_sec"] < row["tomtom_high_sec"]


# ---------------------------------------------------------------------------
# The three designed GEH bands — computed with the REAL statistic
# ---------------------------------------------------------------------------


def _movement_geh(node: validation.NodeRealCounts) -> dict[str, float]:
    return {
        direction["direction"]: validation.geh(direction["sim_basam"], direction["real"])
        for direction in node["directions"]
    }


def test_am_node_1_is_a_node_total_miss(workbook: dict):
    """(a) demand proposal: the TOTAL misses and no single movement does."""
    node = _nodes(workbook, "AM")["1"]
    total_geh = validation.geh(node["total_sim_basam"], node["total_real"])
    assert total_geh is not None
    assert total_geh >= validation.GEH_GOOD_LT
    assert 7.0 <= total_geh <= 8.0, total_geh
    worst = max(_movement_geh(node).values())
    assert worst < validation.GEH_GOOD_LT, worst


def test_am_node_2_is_a_split_miss_with_a_reconciling_total(workbook: dict):
    """(b) routing proposal: total reconciles, exactly one movement misses."""
    node = _nodes(workbook, "AM")["2"]
    total_geh = validation.geh(node["total_sim_basam"], node["total_real"])
    assert total_geh is not None
    assert total_geh < validation.GEH_GOOD_LT
    assert node["total_real"] == node["total_sim_basam"]  # preserved exactly

    per_movement = _movement_geh(node)
    missing = {name: value for name, value in per_movement.items()
               if value >= validation.GEH_GOOD_LT}
    # The E-W leg is the WB through movement (207 -> 141), the one
    # AM_NODE2_SPLIT_MOVEMENT drains.
    assert list(missing) == ["E-W"], per_movement
    assert 8.0 <= missing["E-W"] <= 9.0, missing


@pytest.mark.parametrize("node_id", ["1", "2"])
def test_pm_nodes_reconcile_everywhere(workbook: dict, node_id: str):
    """(c) refusal: the model already reproduces observed behaviour."""
    node = _nodes(workbook, "PM")[node_id]
    total_geh = validation.geh(node["total_sim_basam"], node["total_real"])
    assert total_geh is not None
    assert total_geh < validation.GEH_GOOD_LT
    for name, value in _movement_geh(node).items():
        assert value is not None, name
        assert value < validation.GEH_GOOD_LT, (name, value)


def test_observed_only_node_has_counts_but_no_model_column(workbook: dict):
    """The deliberate unjoinable node: observed, and absent from every run.

    Its Vehs(All) cells are EMPTY rather than zero — "the model has no opinion
    here" and "the model says zero" are different facts, and unit U5's reality
    check has to be able to report the first.
    """
    for period in ("AM", "PM"):
        rows = [r for r in workbook["counts"]
                if r["period"] == period and r["node_id"] == str(make_demo_data.OBSERVED_ONLY_NODE)]
        assert rows
        assert all(r["model_volume"] is None for r in rows)
        assert all(r["observed_volume"] is not None for r in rows)
        node = _nodes(workbook, period)[str(make_demo_data.OBSERVED_ONLY_NODE)]
        assert node["total_sim_basam"] is None
        assert node["total_real"] > 0
        assert node["node_name"] == make_demo_data.OBSERVED_ONLY_NODE_NAME


# ---------------------------------------------------------------------------
# Derived, not hand-written: the workbook's numbers ARE the run's numbers
# ---------------------------------------------------------------------------


def test_model_column_is_the_baseline_runs_own_movement_volumes(
    workbook: dict, baseline_movement_vehs: dict[str, float]
):
    """Every modelled cell is scenario 1 / run 1's VEHS, rounded — nothing else.

    This is the anti-drift pin: change a base volume in ``MOVEMENTS`` and both
    sides of the fixture move together, because there is only one source.
    """
    for period in ("AM", "PM"):
        for node_id in (1, 2):
            node = _nodes(workbook, period)[str(node_id)]
            by_direction = {d["direction"]: d["sim_basam"] for d in node["directions"]}
            for key in make_demo_data.node_movements(node_id):
                direction = make_demo_data.movement_direction(*key)
                object_id = make_demo_data.movement_object_id(*key)
                assert by_direction[direction] == round(baseline_movement_vehs[object_id])


def test_am_node_1_observed_is_the_modelled_values_scaled(
    workbook: dict, baseline_movement_vehs: dict[str, float]
):
    node = _nodes(workbook, "AM")["1"]
    by_direction = {d["direction"]: d["real"] for d in node["directions"]}
    for key in make_demo_data.node_movements(1):
        direction = make_demo_data.movement_direction(*key)
        modelled = baseline_movement_vehs[make_demo_data.movement_object_id(*key)]
        expected = round(modelled * make_demo_data.AM_NODE1_DEMAND_FACTOR)
        assert by_direction[direction] == expected


def test_am_node_2_shift_moves_vehicles_without_changing_the_total(
    workbook: dict, baseline_movement_vehs: dict[str, float]
):
    node = _nodes(workbook, "AM")["2"]
    by_direction = {d["direction"]: d["real"] for d in node["directions"]}
    drained = make_demo_data.movement_direction(*make_demo_data.AM_NODE2_SPLIT_MOVEMENT)
    modelled = baseline_movement_vehs[
        make_demo_data.movement_object_id(*make_demo_data.AM_NODE2_SPLIT_MOVEMENT)
    ]
    assert by_direction[drained] == round(modelled) - make_demo_data.AM_NODE2_SPLIT_SHIFT_VEHS
    # ... and the vehicles went somewhere, they were not deleted.
    assert sum(by_direction.values()) == node["total_real"] == node["total_sim_basam"]


def test_direction_tokens_are_unique_per_node_and_well_formed():
    """The Direction column is the workbook's movement identity — a duplicate
    token would silently merge two movements into one row."""
    for node_id in (1, 2):
        tokens = [make_demo_data.movement_direction(*key)
                  for key in make_demo_data.node_movements(node_id)]
        assert len(tokens) == len(set(tokens)) == 12
        for token in tokens:
            assert validation._normalize_movement(token) == token


# ---------------------------------------------------------------------------
# Idempotence
# ---------------------------------------------------------------------------


def test_regenerating_produces_identical_bytes(demo_dir: Path, tmp_path: Path):
    """Two runs, byte for byte — including the workbook.

    openpyxl stamps every zip member and docProps/core.xml with the wall clock,
    which would make the workbook differ on every regeneration for no semantic
    reason; ``make_demo_data._stable_xlsx_bytes`` normalises both away.

    NOTE this test alone does NOT prove the clock is neutralised: back to back
    generations usually land inside the same second, so it passed for months
    while the ``dcterms:modified`` stamp was still live (a ~3% time flake that
    did fire in a full-suite run). The proof is the next test, which forces the
    gap.
    """
    second = _generate(tmp_path / "again")
    for name in (
        "practice.inpx",
        "practice.vissimpdb",
        "real_counts.xlsx",
        "Scenarios/S000001/S000001.results/run_001.db",
        "Scenarios/S000003/S000003.results/run_003.db",
    ):
        assert (demo_dir / name).read_bytes() == (second / name).read_bytes(), name


def test_the_workbook_is_stable_across_a_real_clock_gap(tmp_path: Path):
    """The byte-identity claim, proven the hard way: a REAL >= 2 s gap.

    MEASURED failure this pins: ``wb.properties.modified = stamp`` does not
    survive ``save()`` — openpyxl's ``save_workbook`` overwrites it with
    ``datetime.now(utc)`` (openpyxl/writer/excel.py) — and
    ``_stable_xlsx_bytes`` used to copy member CONTENT verbatim, so
    ``docProps/core.xml`` carried the wall clock into the zip. Two generations
    in the same wall-clock second matched anyway, which is why the sibling test
    above passed while the workbook was not actually stable.

    The sleep is deliberate and is the cheapest honest instrument available:
    monkeypatching openpyxl's writer would test the mock, not the generator.
    ~2 s is paid once for the one guarantee this fixture makes to every other
    test file that regenerates it.
    """
    first = _generate(tmp_path / "t0")
    time.sleep(2.1)
    second = _generate(tmp_path / "t1")

    first_bytes = (first / "real_counts.xlsx").read_bytes()
    assert first_bytes == (second / "real_counts.xlsx").read_bytes()

    # ... and the REASON, so a future regression is diagnosable rather than
    # just red: both document dates carry the pinned stamp, not today's date.
    with zipfile.ZipFile(BytesIO(first_bytes)) as archive:
        core = archive.read("docProps/core.xml").decode("utf-8")
    assert "<dcterms:modified" in core, core
    # Both dates carry the pinned stamp — created (openpyxl kept it) and
    # modified (openpyxl overwrote it; _stable_xlsx_bytes put it back).
    assert core.count(make_demo_data._XLSX_EPOCH_W3CDTF) == 2, core
    # No other date survives anywhere in the member: with every pinned stamp
    # removed, today's date must not appear.
    assert datetime.date.today().isoformat() not in core.replace(
        make_demo_data._XLSX_EPOCH_W3CDTF, ""
    ), core

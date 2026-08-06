"""Phase 2 unit U10 — the reality check reading a STORED observation set.

Unit U5 compares a run against the base counts workbook. U10 lets the observed
side come from ``services/observations.py`` instead, and the whole risk of that
change is the JOIN: an observation site id is a survey label (the Daejeon set's
are intersection names read out of file names, roadmap 1.2's documented
deviation) and it is **not** a Vissim node id. A tool that paired them by
position, ordering or string similarity would produce a confidently wrong
comparison — the exact failure mode this project exists to prevent — so this
file's core assertions are that the map is required, that both directions of
the join are named, and that the answer is otherwise identical to the workbook
path on equivalent numbers.

The run side here is a synthetic ``run_result`` mapping, deliberately: what is
under test is the OBSERVED side and the resolution around it. U1's real
evidence collector is exercised end to end by
``tests/test_agent_reality_check.py``, which owns that property and reads a
real result database to prove it.

Offline: synthetic workbooks, no Vissim, no COM, no result database.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from services import observations, workspaces
from services.agent import fixes, reality_check, tools_metrics
from services.observations import ObservationPeriod
from tests.test_dataimport_daejeon import WEDNESDAY, _approach_rows, _file_name, _write_workbook

APPROACH = "가람방면"
SITE_A = "가람네거리"
SITE_B = "한밭네거리"

#: 12 bins x 109 veh -- the hand-derived total stated in
#: tests/test_observations_store.py, reused here so the two files cannot drift.
SITE_TOTAL = 12 * 109.0
LEFT = 12 * 24.0
THROUGH = 12 * 61.0
RIGHT = 12 * 24.0

AM = ObservationPeriod(name="AM", start=WEDNESDAY, end=WEDNESDAY + timedelta(hours=3))


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "workspaces"
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", root)
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "legacy-uploads")
    default_id = workspaces.default_workspace_id()
    token = workspaces.set_current_workspace_id(default_id)
    try:
        yield root
    finally:
        workspaces.reset_current_workspace_id(token)


def _workbook(root: Path, *, site: str, approach: str = APPROACH) -> Path:
    return _write_workbook(
        root,
        _approach_rows(approach, start=WEDNESDAY, hours=3),
        file_name=_file_name(
            site, start=WEDNESDAY, last_start=WEDNESDAY + timedelta(hours=2, minutes=45)
        ),
    )


@pytest.fixture()
def stored_set(workspace_root: Path, tmp_path: Path) -> dict[str, Any]:
    """Two sites, one 3-hour AM period, ingested through the real adapter+gate."""
    return observations.ingest_observation_set(
        [
            _workbook(tmp_path, site=SITE_A),
            _workbook(tmp_path, site=SITE_B, approach="한밭방면"),
        ],
        name="대전 관측 세트",
        periods=[AM],
    )


def _run_result(node_totals: dict[str, float]) -> dict[str, Any]:
    """U1's ``movement_volumes`` shape: ``{OBJECT_ID: {"vehs", "node"}}``.

    One movement per node, so the node total is unambiguous; the per-movement
    splits are deliberately left unmapped, which is what makes the movement
    grading come back "ungradable and named" rather than invented.
    """
    return {
        "movement_volumes": {
            f"{node}-1-0.0-2-0.0": {"vehs": vehs, "node": node}
            for node, vehs in node_totals.items()
        }
    }


# ---------------------------------------------------------------------------
# The join is required, and it is named in both directions
# ---------------------------------------------------------------------------


def test_without_a_site_to_node_map_the_check_refuses_and_names_the_sites(
    stored_set: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    report = _build(stored_set["id"], site_nodes=None, monkeypatch=monkeypatch, tmp_path=tmp_path)

    assert report["status"] == reality_check.NOT_CHECKABLE
    assert report["reason_code"] == reality_check.OBSERVATION_SITE_NODE_MAP_MISSING
    for lang in ("ko", "en", "ja"):
        assert SITE_A in report["message"][lang]
        assert SITE_B in report["message"][lang]
    assert report["nodes"] == []
    assert report["proposals"] == []
    sites = report["coverage"]["observation_sites"]
    assert sites["sites_without_node_mapping"] == [SITE_A, SITE_B]
    assert sites["observation_set_id"] == stored_set["id"]


def test_a_partial_map_compares_what_joined_and_names_what_did_not(
    stored_set: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The classic lie in this class of tool is an inner join reported as a
    clean '1 of 1 nodes match'. Site B is counted and never mapped; node 9 is
    simulated and never counted. Both are named."""
    report = _build(
        stored_set["id"],
        site_nodes=[{"site_id": SITE_A, "node_id": "1"}],
        run=_run_result({"1": SITE_TOTAL, "9": 500.0}),
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )

    assert report["status"] == reality_check.CHECKED
    assert report["coverage"]["nodes_compared"] == ["1"]
    assert report["coverage"]["run_nodes_without_observed"] == ["9"]
    assert report["coverage"]["observation_sites"]["sites_joined"] == [f"{SITE_A}->1"]
    assert report["coverage"]["observation_sites"]["sites_without_node_mapping"] == [SITE_B]


def test_an_unknown_observation_set_refuses_by_name(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    report = _build(
        "deadbeef1234",
        site_nodes=[{"site_id": SITE_A, "node_id": "1"}],
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    assert report["reason_code"] == reality_check.OBSERVATION_SET_NOT_FOUND
    assert "deadbeef1234" in report["message"]["en"]


def test_a_period_the_set_does_not_carry_refuses_and_lists_the_ones_it_does(
    stored_set: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    report = _build(
        stored_set["id"],
        site_nodes=[{"site_id": SITE_A, "node_id": "1"}],
        observation_period="PM",
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    assert report["reason_code"] == reality_check.OBSERVATION_SET_PERIOD_ABSENT
    assert "AM" in report["message"]["en"]


def test_a_map_that_joins_nothing_refuses_rather_than_reporting_an_empty_success(
    stored_set: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    report = _build(
        stored_set["id"],
        site_nodes=[{"site_id": "존재하지 않는 교차로", "node_id": "1"}],
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    assert report["reason_code"] == reality_check.OBSERVATION_NO_MAPPED_SITES
    sites = report["coverage"]["observation_sites"]
    assert sites["mapped_sites_absent_from_observations"] == ["존재하지 않는 교차로"]
    assert sites["sites_without_node_mapping"] == [SITE_A, SITE_B]


# ---------------------------------------------------------------------------
# The three outcomes, and equivalence with the workbook path
# ---------------------------------------------------------------------------


def _observed_workbook_payload(node_totals: dict[str, float]) -> dict[str, Any]:
    """``load_base_workbook``'s payload shape carrying the same numbers.

    Movement rows use the store's own movement ids so both paths grade the
    identical set of movements; the model column is filled with a deliberately
    WRONG number to prove neither path reads it (U5 blanks it either way).
    """
    rows = []
    for node_id, total in node_totals.items():
        rows.append(
            {
                "period": "AM",
                "node_id": node_id,
                "node_name": SITE_A,
                "movement": "Total",
                "observed_volume": total,
                "model_volume": 999999.0,
            }
        )
        for movement, volume in (
            (f"{APPROACH}|left", LEFT),
            (f"{APPROACH}|right", RIGHT),
            (f"{APPROACH}|through", THROUGH),
        ):
            rows.append(
                {
                    "period": "AM",
                    "node_id": node_id,
                    "node_name": SITE_A,
                    "movement": movement,
                    "observed_volume": volume,
                    "model_volume": 999999.0,
                }
            )
    return {"counts": rows, "warnings": [], "periods": ["AM"], "exists": True}


@pytest.mark.parametrize(
    "modelled, expected_outcome",
    [
        # A big miss with a target to act on -> a concrete proposal.
        pytest.param(SITE_TOTAL * 2, fixes.PROPOSAL, id="miss -> proposal"),
        # The model already reproduces the count -> a refusal, not a no-op edit.
        pytest.param(SITE_TOTAL, fixes.REFUSAL, id="match -> refusal"),
    ],
)
def test_the_store_path_reaches_the_same_outcome_as_the_workbook_path(
    stored_set: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    modelled: float,
    expected_outcome: str,
):
    """Same numbers, same target, two observed origins -> the same verdict.

    This is the property that makes the new path safe to use: U10 changed where
    the observed side comes from and nothing about how it is graded.
    """
    run = _run_result({"1": modelled})
    targets = [
        tools_metrics.RealityCheckNodeTarget(
            node_id="1", vehicle_input_no="7", vehicle_input_current_volume=800.0
        )
    ]

    from_store = _build(
        stored_set["id"],
        site_nodes=[{"site_id": SITE_A, "node_id": "1"}],
        run=run,
        targets=targets,
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )
    from_workbook = reality_check.check_run_against_counts(
        run,
        _observed_workbook_payload({"1": SITE_TOTAL}),
        period="AM",
        targets={"1": {"vehicle_input": {"input_no": "7", "current_volume": 800.0, "interval_no": 1}}},
    )

    assert from_store["status"] == from_workbook["status"] == reality_check.CHECKED
    store_node = from_store["nodes"][0]
    workbook_node = from_workbook["nodes"][0]
    assert store_node["observed_total"] == workbook_node["observed_total"] == SITE_TOTAL
    assert store_node["node_total_geh"] == workbook_node["node_total_geh"]
    assert store_node["outcome"]["outcome"] == workbook_node["outcome"]["outcome"]
    assert store_node["outcome"]["outcome"] == expected_outcome
    # And neither path used the workbook's 999999 model column.
    assert store_node["modelled_total"] == workbook_node["modelled_total"] == modelled


def test_the_report_names_the_store_as_the_observed_source(
    stored_set: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The ``sources`` block's whole job is provenance; leaving the base
    workbook's sentence in place while reading a stored set would be a lie in
    exactly the field a reader consults to find the file."""
    report = _build(
        stored_set["id"],
        site_nodes=[{"site_id": SITE_A, "node_id": "1"}],
        run=_run_result({"1": SITE_TOTAL}),
        monkeypatch=monkeypatch,
        tmp_path=tmp_path,
    )

    assert "services.observations" in report["sources"]["observed"]
    assert stored_set["id"] in report["sources"]["observed"]
    assert report["period"] == "AM"
    # The modelled half of the provenance is untouched by U10.
    assert report["sources"]["modelled"] == "run_result['movement_volumes'] (unit U1)"
    assert "Vehs(All)" in report["sources"]["modelled_not_used"]


def test_the_default_path_still_reads_the_base_workbook(
    workspace_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """No ``observation_set_id`` -> nothing about U5's behaviour changed,
    including the provenance line."""
    called: list[str] = []

    def _fake_load(path: str) -> dict[str, Any]:
        called.append(path)
        return _observed_workbook_payload({"1": SITE_TOTAL})

    monkeypatch.setattr(tools_metrics.validation, "load_base_workbook", _fake_load)
    monkeypatch.setattr(
        tools_metrics.datasets, "get_active_real_counts_excel", lambda: tmp_path / "real.xlsx"
    )
    report = _build(None, site_nodes=None, run=_run_result({"1": SITE_TOTAL}), monkeypatch=monkeypatch, tmp_path=tmp_path)

    assert called, "the base workbook loader was not called"
    assert report["status"] == reality_check.CHECKED
    assert report["sources"]["observed"].startswith("validation.load_base_workbook")
    assert "observation_sites" not in report["coverage"]


# ---------------------------------------------------------------------------
# The tool registration itself
# ---------------------------------------------------------------------------


def test_the_new_params_are_on_the_registered_tool_and_still_forbid_extras():
    from services.agent import tools_metrics as metrics  # noqa: F401 - registers the tool
    from services.agent.toolkit import default_registry

    spec = default_registry.get("run_reality_check")
    fields = spec.params_model.model_fields
    assert {"observation_set_id", "observation_period", "site_nodes"} <= set(fields)
    # Optional: the workbook path must keep working with the old params only.
    for name in ("observation_set_id", "observation_period", "site_nodes"):
        assert not fields[name].is_required()
    assert spec.tier == "read"
    assert spec.params_model.model_config.get("extra") == "forbid"
    assert "site_nodes" in spec.description


def test_the_tool_dispatches_the_store_path_end_to_end(
    stored_set: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from services.agent.toolkit import dispatch

    _patch_run(monkeypatch, tmp_path, _run_result({"1": SITE_TOTAL * 2}))

    envelope = dispatch(
        "run_reality_check",
        {
            "scenario_id": "S000001",
            "observation_set_id": stored_set["id"],
            "observation_period": "AM",
            "site_nodes": [{"site_id": SITE_A, "node_id": "1"}],
            "targets": [
                {"node_id": "1", "vehicle_input_no": "7", "vehicle_input_current_volume": 800.0}
            ],
        },
    )

    assert envelope["status"] == "ok", envelope
    report = envelope["data"]["reality_check"]
    assert report["status"] == reality_check.CHECKED
    assert report["nodes"][0]["outcome"]["outcome"] == fixes.PROPOSAL
    assert report["nodes"][0]["outcome"]["verb"] == "set_vehicle_input_volume"
    assert report["coverage"]["observation_sites"]["sites_joined"] == [f"{SITE_A}->1"]


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _patch_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, run: dict[str, Any] | None
) -> None:
    """Stand in for the RUN side only.

    ``build_reality_check_report``'s job is to resolve a run and some counts;
    resolving the run needs a result directory with exactly one .db in it,
    which is U1's territory and is proven for real in
    ``tests/test_agent_reality_check.py``. Here it is a single fake path so the
    OBSERVED side is what the test is actually about.
    """
    from services.agent import tools_run

    result_dir = tmp_path / "run-work"
    result_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(tools_metrics, "_resolve_result_dir", lambda scenario_id: result_dir)
    monkeypatch.setattr(
        tools_metrics.results_evidence, "result_db_paths", lambda directory: [result_dir / "run_001.db"]
    )
    monkeypatch.setattr(
        tools_metrics.datasets, "get_active_inpx_path", lambda: tmp_path / "network.inpx"
    )
    monkeypatch.setattr(
        tools_run,
        "_collect_run_evidence",
        lambda *args, **kwargs: dict(run or {}),
    )


def _build(
    set_id: str | None,
    *,
    site_nodes: list[dict[str, str]] | None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    run: dict[str, Any] | None = None,
    observation_period: str | None = "AM",
    targets: list[tools_metrics.RealityCheckNodeTarget] | None = None,
) -> dict[str, Any]:
    _patch_run(monkeypatch, tmp_path, run if run is not None else _run_result({"1": SITE_TOTAL}))
    return tools_metrics.build_reality_check_report(
        "S000001",
        targets=targets,
        observation_set_id=set_id,
        observation_period=observation_period if set_id else None,
        site_nodes=(
            [tools_metrics.ObservationSiteNode(**entry) for entry in site_nodes]
            if site_nodes
            else None
        ),
    )

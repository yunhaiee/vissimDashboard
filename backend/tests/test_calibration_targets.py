"""The calibration screen's edit targets: derived from the network, or refused.

WHAT WAS BROKEN (measured 2026-08-21, chip ``task_93c5b5dd``)
-------------------------------------------------------------
``routers/calibration.py`` called ``build_reality_check_report`` at three sites
and passed ``targets=None`` at every one of them, so every real miss on this
screen came back ``NO_TARGET_OBJECT`` — a true refusal that nothing on the
server was ever going to lift, because no producer of targets existed. This file
covers the producer (``services.agent.reality_check_targets``) and the three
seams it now feeds.

WHAT IS REAL HERE
-----------------
Almost everything, and deliberately so — this unit's whole risk is a join that
looks right against a hand-built fixture and wrong against a network:

* the NETWORK is ``data/practice.inpx``, parsed by the real
  ``tools_network.build_shadow_model`` (its vehicle inputs and its two static
  routing decisions are what the derivation joins against);
* the OBSERVED side is a real ``.xlsx``, written here and parsed by
  ``validation.load_base_workbook`` — headers, movement normalization, period
  selection and node aggregation all run for real;
* the RUN side is a real result database — sqlite, the real
  ``MOVEMENT_EvaluationTimeIntervalClass`` table, read by the real
  ``results_evidence.read_movement_class_rows``;
* and the report is built by the real ``build_reality_check_report``, reached
  through the real HTTP routes.

The ONE seam faked is ``tools_metrics._resolve_result_dir`` — "which directory
holds this scenario's results", the same seam ``test_calibration_loop_e2e``
redirects. No COM, no ``make_demo_data``.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from conftest import admin_headers
from fastapi.testclient import TestClient

import main
from routers import calibration as calibration_endpoint
from services import datasets, demand_target_map, validation, workspaces
from services.agent import fixes, reality_check, reality_check_targets, tools_calibration, tools_metrics, tools_network
from services.agent.toolkit import default_registry, dispatch

openpyxl = pytest.importorskip("openpyxl")

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"

SCENARIO_ID = "SIM_targets"

#: Vehicle input 1 with an explicit interval volume. The demo network's
#: ``<vehicleInput>`` elements are self-closing with no
#: ``<timeIntervalVehVolume>`` child (``interval_count`` 0, ``volume`` None), so
#: this injects the one a real Vissim SaveNetAs would materialize — the same
#: thing ``test_calibration_loop_e2e``'s edit fixture does, for the same reason:
#: without it there is no current volume for a demand target to carry.
_INPUT_1_WITH_VOLUME = (
    '<vehicleInput no="1" name="Main St EB" link="101">'
    '<timeIntervalVehVolume volume="990"/></vehicleInput>'
)

#: node, from link, from position, to link, to position — the OBJECT_ID shape
#: Vissim writes and ``fixes.movement_from_link``/``movement_to_link`` read.
#:
#: The four nodes are the four states this unit has to tell apart:
#:
#: * **1** — counted on ONE approach (link 101), which carries exactly one
#:   vehicle input (no. 1) and exactly one routing decision (no. 1). Its total
#:   misses while every gradable movement passes, so unit D4's refusal is the
#:   whole answer and stands alone.
#: * **2** — its total reconciles but one movement's split misses, and the
#:   decision governing that turn is derivable. The routing proposal.
#: * **3** — counted with workbook TURN TOKENS, not OBJECT_IDs. Its link is not
#:   recoverable from anything, so neither half is derivable.
#: * **4** — counted on TWO approaches, each with its own vehicle input. Two
#:   candidates, no evidence that says which, so no demand target.
#: * **5** — one approach (link 105, vehicle input 4) whose total AND whose
#:   worst movement both miss. ``map_node_counts`` arm (b) therefore HOLDS the
#:   D4 refusal and grading continues, so this node's MEASURED verdict ends up
#:   on ``also_refused`` — the displaced shape, which is a real production shape
#:   and the one a reader that only looks at ``outcome.evidence.target`` misses.
_COUNTS: list[tuple[str, str, float]] = [
    # node 1 — one approach, D4 refusal stands alone
    ("1", "1-101-280.0-140-0.0", 700.0),
    ("1", "1-101-280.0-104-0.0", 120.0),
    ("1", "1-101-280.0-106-0.0", 90.0),
    ("1", "Total", 910.0),
    # node 2 — total reconciles, the through movement's split does not
    ("2", "2-207-280.0-141-0.0", 600.0),
    ("2", "2-207-280.0-204-0.0", 200.0),
    ("2", "2-203-280.0-208-0.0", 300.0),
    ("2", "Total", 1100.0),
    # node 3 — turn tokens, no link recoverable
    ("3", "E-W", 700.0),
    ("3", "Total", 700.0),
    # node 4 — two counted approaches, two vehicle inputs
    ("4", "4-205-280.0-208-0.0", 500.0),
    ("4", "4-203-280.0-206-0.0", 400.0),
    ("4", "Total", 900.0),
    # node 5 — total AND split both miss, so D4's refusal is displaced
    ("5", "5-105-280.0-208-0.0", 700.0),
    ("5", "5-105-280.0-206-0.0", 300.0),
    ("5", "Total", 1000.0),
]

#: Per-movement control delay, for the two movements of node 4 and nothing else
#: (unit #54). 95 s is LOS F on ``results_evidence.los_band``'s HCM signalised
#: bands (E>55, F>80), which is what lets node 4 — once a human has NAMED its
#: vehicle input — reach ``CAPACITY_LIMIT`` instead of stopping at "this run
#: carries no delay for that node". That is the whole point of the unit stated
#: as a fixture: the harness reaches CAPACITY_LIMIT on the real node 8 only
#: because a person supplied the mapping, and every other node here keeps its
#: delay-less shape so the tests above are untouched.
_RUN_DELAYS: dict[str, float] = {
    "4-205-280.0-208-0.0": 95.0,
    "4-203-280.0-206-0.0": 95.0,
}

#: What the RUN produced, per movement OBJECT_ID. Node 1's right turn is absent
#: on purpose: an ungradable movement is what lets a node total miss while every
#: movement this run CAN grade passes.
_RUN_VEHS: dict[str, float] = {
    "1-101-280.0-140-0.0": 640.0,
    "1-101-280.0-104-0.0": 100.0,
    "2-207-280.0-141-0.0": 480.0,
    "2-207-280.0-204-0.0": 210.0,
    "2-203-280.0-208-0.0": 300.0,
    # Node 3's run row IS a real OBJECT_ID entering from link 103 — a link with
    # exactly one vehicle input. Its observed rows are turn tokens, so a
    # derivation that read the MERGED movements would hand node 3 that input.
    "3-103-280.0-104-0.0": 500.0,
    "4-205-280.0-208-0.0": 300.0,
    "4-203-280.0-206-0.0": 250.0,
    "5-105-280.0-208-0.0": 480.0,
    "5-105-280.0-206-0.0": 200.0,
}


# ---------------------------------------------------------------------------
# Fixtures — a real network, a real workbook, a real result database
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def network(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("targets-network") / "calibration.inpx"
    text = DEMO_INPX.read_text(encoding="utf-8")
    replaced = text.replace(
        '<vehicleInput no="1" name="Main St EB" link="101"/>', _INPUT_1_WITH_VOLUME
    )
    assert replaced != text, "fixture: vehicle input 1 was not found in the demo network"
    out.write_text(replaced, encoding="utf-8")
    return out


@pytest.fixture(scope="module")
def workbook(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("targets-counts") / "real_counts.xlsx"
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Counts"
    sheet.append(
        ["period", "node_id", "node_name", "movement", "observed_volume", "model_volume"]
    )
    for node_id, movement, observed in _COUNTS:
        # The model column is written and then IGNORED: reality_check blanks the
        # workbook's own modelled numbers (``_observed_side_only``) so the only
        # modelled value is the run's. It is here because a real workbook has it.
        sheet.append(["AM", node_id, f"Node {node_id}", movement, observed, observed])
    wb.save(path)
    return path


@pytest.fixture(scope="module")
def result_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One real Vissim-shaped result database.

    Single-seed on purpose: ``read_movement_class_rows`` SUMS across every .db
    in a directory, and ``build_reality_check_report`` refuses a multi-seed
    directory outright rather than manufacturing an N-fold demand error.
    """
    work = tmp_path_factory.mktemp("targets-results")
    conn = sqlite3.connect(work / "run_001.db")
    try:
        conn.execute("CREATE TABLE SIMULATIONRUN (RUNINDEX INTEGER)")
        conn.execute("INSERT INTO SIMULATIONRUN VALUES (1)")
        conn.execute(
            'CREATE TABLE "MOVEMENT_EvaluationTimeIntervalClass" '
            "(OBJECT_ID TEXT, VEHS REAL, VEHDELAY REAL, ARG_VEHICLECLASS TEXT)"
        )
        conn.executemany(
            'INSERT INTO "MOVEMENT_EvaluationTimeIntervalClass" VALUES (?, ?, ?, ?)',
            [
                (object_id, vehs, _RUN_DELAYS.get(object_id), "ALL")
                for object_id, vehs in _RUN_VEHS.items()
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return work


@pytest.fixture()
def wired(
    monkeypatch: pytest.MonkeyPatch, network: Path, workbook: Path, result_dir: Path
) -> None:
    """Point every reader at the three artifacts above — and nothing else.

    The gate/health/phase-diagram gatherers are silenced because they are the
    rail's OTHER steps and this file is about step ⑩ and the report; the
    reality-check path itself is untouched.
    """
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: network)
    monkeypatch.setattr(datasets, "get_active_real_counts_excel", lambda: workbook)
    monkeypatch.setattr(calibration_endpoint, "get_active_inpx_path", lambda: network)
    monkeypatch.setattr(tools_metrics, "_resolve_result_dir", lambda scenario_id: result_dir)
    monkeypatch.setattr(calibration_endpoint, "_gate_for_active_dataset", lambda language: None)
    monkeypatch.setattr(calibration_endpoint, "_health_report", lambda: None)
    monkeypatch.setattr(calibration_endpoint, "_phase_diagram_index", lambda: None)


@pytest.fixture()
def client(wired: None):
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def shadow(network: Path) -> dict[str, Any]:
    return tools_network.build_shadow_model(network)


@pytest.fixture(scope="module")
def observed(workbook: Path) -> dict[str, dict[str, Any]]:
    side = reality_check.observed_nodes(
        validation.load_base_workbook(str(workbook)), period="AM"
    )
    assert side["problem"] is None, side
    return side["nodes"]


@pytest.fixture(scope="module")
def derived(observed, shadow) -> dict[str, Any]:
    return reality_check_targets.derive_targets(observed=observed, shadow_model=shadow)


def _by_node(derived: dict[str, Any], node_id: str) -> list[Any]:
    return [target for target in derived["targets"] if target.node_id == node_id]


def _node(report: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(entry for entry in report["nodes"] if entry["node_id"] == node_id)


# ===========================================================================
# 1. The derivation — what it will say, and what it refuses to
# ===========================================================================


def test_the_fixture_network_is_the_one_the_join_is_written_against(shadow):
    """A fixture assertion, first, because every claim below rests on it: the
    demo network really does put ONE vehicle input and ONE routing decision on
    link 101, and a second decision on link 207."""
    inputs_on_101 = [vi for vi in shadow["vehicle_inputs"] if vi["link"] == "101"]
    assert [vi["no"] for vi in inputs_on_101] == ["1"]
    assert inputs_on_101[0]["volume"] == 990.0, "fixture: the injected interval volume"
    decisions = {d["link"]: d["no"] for d in shadow["routing_decisions"]}
    assert decisions == {"101": "1", "207": "2"}


def test_one_vehicle_input_on_the_counted_approach_becomes_the_demand_target(derived):
    """Node 1 is counted entering from link 101 and exactly one input feeds it."""
    demand = [target for target in _by_node(derived, "1") if target.vehicle_input_no]
    assert len(demand) == 1
    target = demand[0]
    assert target.vehicle_input_no == "1"
    assert target.vehicle_input_link_no == "101"
    assert target.vehicle_input_current_volume == 990.0
    assert derived["nodes"]["1"]["vehicle_input"]["reason"] == (
        reality_check_targets.SINGLE_VEHICLE_INPUT
    )


def test_two_candidates_produce_no_target_and_no_dominance_pick(derived):
    """Node 4 is counted on two approaches, each with its own vehicle input.

    THE DECISION THIS PINS: there is no evidence here that says which of the two
    carries the shortfall. A share-based pick ("input 205 moves more, so it is
    the one") is a human's judgement — the calibration harness's own 69% pick was
    hand-derived — and automating it would hide that judgement behind a proposal
    the approval card presents as derived. Two candidates, no target.
    """
    assert [t.vehicle_input_no for t in _by_node(derived, "4") if t.vehicle_input_no] == []
    why = derived["nodes"]["4"]["vehicle_input"]
    assert why["reason"] == reality_check_targets.MULTIPLE_VEHICLE_INPUT_CANDIDATES
    assert why["chosen"] is None
    assert sorted(why["candidates"]) == ["5", "6"], "both really were candidates"


def test_several_inputs_on_ONE_link_are_the_same_refusal(observed, shadow):
    """The real client model's shape: link 620 carries vehicle inputs 107/207/307,
    one per vehicle class, all named the same (tools_network._parse_vehicle_inputs).
    Sibling inputs on a single link are the same ambiguity as two approaches and
    get the same answer."""
    crowded = {
        **shadow,
        "vehicle_inputs": [
            {"no": "107", "link": "101", "volume": 500.0},
            {"no": "207", "link": "101", "volume": 300.0},
            {"no": "307", "link": "101", "volume": 200.0},
        ],
    }
    derived = reality_check_targets.derive_targets(observed=observed, shadow_model=crowded)
    assert [t for t in derived["targets"] if t.vehicle_input_no] == []
    assert derived["nodes"]["1"]["vehicle_input"]["reason"] == (
        reality_check_targets.MULTIPLE_VEHICLE_INPUT_CANDIDATES
    )


def test_a_workbook_keyed_by_turn_tokens_is_structurally_excluded(derived):
    """Node 3's rows are ``E-W``, not OBJECT_IDs, so no row resolves to a link.

    This is the same absence ``fixes.entrance_measurement`` names as
    ``ENTRANCE_NO_LINK_KEYED_ROWS``, and this module does not get to guess what
    that function refuses to. Note what the run DOES carry for node 3: a real
    OBJECT_ID entering from link 103, which vehicle input 3 feeds. Reading the
    MERGED movements instead of the observed ones would hand node 3 that input
    as its "single candidate" — a demand target for a node whose counts cannot
    be resolved to any link at all.
    """
    assert _by_node(derived, "3") == []
    assert derived["nodes"]["3"]["object_id_rows"] == 0
    assert derived["nodes"]["3"]["vehicle_input"]["reason"] == (
        reality_check_targets.NO_OBJECT_ID_ROWS
    )
    assert _RUN_VEHS["3-103-280.0-104-0.0"], "fixture: the run row that would be borrowed"


def test_leg_keyed_observation_store_rows_are_excluded_too(shadow):
    """``services.observations`` aggregates turning counts under
    ``"<approach leg>|<turn>"`` (its own MOVEMENT_ID_SEPARATOR). There is no
    link↔leg edge anywhere in this system, so a store-sourced node resolves to
    no link — the named follow-up ``entrance_measurement`` already carries."""
    store_node = {
        "node_id": "1",
        "node_name": "1: 도서관사거리",
        "total_real": 910,
        "total_sim_basam": None,
        "count_basis": "turning_counts",
        "directions": [
            {"direction": "북측방면|left", "real": 700, "sim_basam": 0},
            {"direction": "북측방면|through", "real": 210, "sim_basam": 0},
        ],
    }
    derived = reality_check_targets.derive_targets(
        observed={"1": store_node}, shadow_model=shadow
    )
    assert derived["targets"] == []
    assert derived["nodes"]["1"]["vehicle_input"]["reason"] == (
        reality_check_targets.NO_OBJECT_ID_ROWS
    )


def test_a_row_filed_under_the_wrong_node_does_not_lend_it_an_approach(shadow):
    """An OBJECT_ID carries its OWN node number, and it must be this node's.

    Measured here rather than assumed, because it is the guard with the smallest
    surface: node 1 below is counted on link 101 only, but one of its rows is
    node 9's. Without the check, link 205 becomes one of node 1's "approaches",
    vehicle input 6 becomes a second candidate, and the node loses the target it
    should have had — or, with one row instead of two, gains one that belongs to
    another intersection entirely.
    """
    misfiled = {
        "node_id": "1",
        "node_name": "1: misfiled",
        "total_real": 910,
        "total_sim_basam": None,
        "directions": [
            {"direction": "1-101-280.0-140-0.0", "real": 700, "sim_basam": 0},
            {"direction": "9-205-280.0-208-0.0", "real": 210, "sim_basam": 0},
        ],
    }
    derived = reality_check_targets.derive_targets(
        observed={"1": misfiled}, shadow_model=shadow
    )
    assert derived["nodes"]["1"]["approach_links"] == ["101"]
    assert [t.vehicle_input_no for t in derived["targets"] if t.vehicle_input_no] == ["1"]


def test_the_routing_target_is_the_decision_on_the_approach_and_its_own_scope(derived):
    """Decision 2 sits on link 207; the movement exits by link 141; exactly one
    of that decision's routes ends there. Everything the proposal's arithmetic
    needs comes from those two facts plus the decision's own relFlows."""
    routes = {t.movement: t for t in _by_node(derived, "2") if t.movement}
    target = routes["2-207-280.0-141-0.0"]
    assert (target.decision_no, target.route_no) == ("2", "1")
    assert target.current_rel_flow == 680.0
    # The OTHER two routes of decision 2. Vissim never renormalizes siblings on
    # write, so their sum is the weight the closed form scales:
    # sibling_total * p / (1 - p).
    assert target.sibling_rel_flow_total == 190.0
    # The decision governs THIS approach, so its scope is this node's rows
    # entering from link 207 — not the node's other approach, and not the node.
    assert target.decision_movements == [
        "2-207-280.0-141-0.0",
        "2-207-280.0-204-0.0",
    ]


def test_an_approach_with_no_routing_decision_gets_no_route_target(derived):
    """Node 2's other approach is link 203 and nothing decides routes there."""
    assert "2-203-280.0-208-0.0" not in {t.movement for t in _by_node(derived, "2")}
    assert derived["nodes"]["2"]["routing"]["203"]["reason"] == (
        reality_check_targets.NO_DECISION_ON_APPROACH
    )


def _with_decisions(shadow: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    return {**shadow, "routing_decisions": decisions}


def _decision_1(shadow: dict[str, Any]) -> dict[str, Any]:
    return next(d for d in shadow["routing_decisions"] if d["no"] == "1")


def test_two_routes_to_the_same_exit_link_produce_no_target(observed, shadow):
    """The routing mirror of the vehicle-input refusal.

    Two of a decision's routes ending on the link this movement exits by is an
    AMBIGUITY, not a shortlist: nothing in the counts says which of the two the
    counted turn used, and rewriting one route's relFlow while the other keeps
    its own would silently reassign traffic between them. Taking ``matches[0]``
    would make that choice by list order — the network's document order, which
    is not evidence about anything.
    """
    decision = _decision_1(shadow)
    tied = _with_decisions(
        shadow,
        [
            {
                **decision,
                "routes": [
                    {**route, "dest_link": "140"} if route["route_no"] in ("1", "2") else route
                    for route in decision["routes"]
                ],
            }
        ],
    )
    derived = reality_check_targets.derive_targets(observed=observed, shadow_model=tied)
    routes = derived["nodes"]["1"]["routing"]["101"]["routes"]
    assert routes["1-101-280.0-140-0.0"]["reason"] == reality_check_targets.ROUTE_NOT_IDENTIFIED
    assert "1-101-280.0-140-0.0" not in {t.movement for t in derived["targets"]}


def test_two_decisions_on_one_approach_produce_no_target(observed, shadow):
    """The same refusal one level up. Two static decisions sitting on link 101
    both govern turns off it, and which one governs THIS turn is not something
    the approach link alone can answer — so no route is named at all."""
    decision = _decision_1(shadow)
    twinned = _with_decisions(shadow, [decision, {**decision, "no": "77"}])
    derived = reality_check_targets.derive_targets(observed=observed, shadow_model=twinned)
    link_why = derived["nodes"]["1"]["routing"]["101"]
    assert link_why["reason"] == reality_check_targets.MULTIPLE_DECISIONS_ON_APPROACH
    assert link_why["decision_no"] is None
    assert link_why["routes"] == {}
    assert [t for t in _by_node(derived, "1") if t.movement] == []
    # ...and the DEMAND half is untouched by the routing ambiguity.
    assert [t.vehicle_input_no for t in _by_node(derived, "1") if t.vehicle_input_no] == ["1"]


def test_an_unreadable_sibling_rel_flow_produces_no_target(observed, shadow):
    """The proposal's value is ``sibling_total * p / (1 - p)``. A decision with a
    relFlow this product cannot read has no sibling total, so naming the route as
    a target would only move the refusal downstream — into the builder, where its
    sentence is about missing evidence rather than about the network."""
    decision = _decision_1(shadow)
    unreadable = _with_decisions(
        shadow,
        [
            {
                **decision,
                "routes": [
                    {**route, "rel_flow": None} if route["route_no"] == "3" else route
                    for route in decision["routes"]
                ],
            }
        ],
    )
    derived = reality_check_targets.derive_targets(observed=observed, shadow_model=unreadable)
    routes = derived["nodes"]["1"]["routing"]["101"]["routes"]
    assert {entry["reason"] for entry in routes.values()} == {
        reality_check_targets.REL_FLOW_UNREADABLE
    }
    assert [t for t in _by_node(derived, "1") if t.movement] == []


def test_an_approach_no_vehicle_input_feeds_is_named_as_that(observed, shadow):
    """No candidate at all is its own state, and a different one from "several".
    A modeller reading the reason needs to know whether to disambiguate or to go
    looking for a missing input."""
    without = {**shadow, "vehicle_inputs": [vi for vi in shadow["vehicle_inputs"] if vi["link"] != "101"]}
    derived = reality_check_targets.derive_targets(observed=observed, shadow_model=without)
    why = derived["nodes"]["1"]["vehicle_input"]
    assert why["reason"] == reality_check_targets.NO_VEHICLE_INPUT_ON_APPROACH
    assert why["candidates"] == []
    assert [t.vehicle_input_no for t in _by_node(derived, "1") if t.vehicle_input_no] == []
    # The routing half still stands: the two are derived independently.
    assert {t.movement for t in _by_node(derived, "1") if t.movement}


def test_a_route_that_ends_somewhere_else_is_not_matched_by_path(observed, shadow):
    """The identity is ``destLink`` IS the exit link — not "the exit link appears
    somewhere along the route". Moving decision 1's through route to end further
    downstream leaves that turn with no identifiable route rather than with the
    route whose path happens to include link 140."""
    moved = {
        **shadow,
        "routing_decisions": [
            {
                **decision,
                "routes": [
                    {**route, "dest_link": "208" if route["dest_link"] == "140" else route["dest_link"]}
                    for route in decision["routes"]
                ],
            }
            for decision in shadow["routing_decisions"]
        ],
    }
    derived = reality_check_targets.derive_targets(observed=observed, shadow_model=moved)
    routes = derived["nodes"]["1"]["routing"]["101"]["routes"]
    assert routes["1-101-280.0-140-0.0"]["reason"] == reality_check_targets.ROUTE_NOT_IDENTIFIED
    assert "1-101-280.0-140-0.0" not in {t.movement for t in derived["targets"]}


def test_nothing_is_derived_without_both_sides():
    """Either half missing leaves the caller exactly where it was before this
    module existed: no targets, and therefore NO_TARGET_OBJECT refusals."""
    assert reality_check_targets.derive_targets(observed={}, shadow_model={})["targets"] == []
    assert reality_check_targets.derive_targets(observed=None, shadow_model=None)["targets"] == []


# ===========================================================================
# 2. The report — what a derived target actually changes
# ===========================================================================


def _report(client: TestClient) -> dict[str, Any]:
    response = client.get(
        "/api/calibration/reality-check",
        params={"scenario_id": SCENARIO_ID, "period": "AM"},
        headers=admin_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "CHECKED", body["message"]["en"]
    return body


def test_the_screen_now_names_the_entrance_it_may_not_edit(client: TestClient):
    """Node 1, end to end: the demand target is derived, and the FIRST thing it
    buys is a refusal.

    That is not a disappointment, it is the doctrine. The join that finds the
    input — an observed OBJECT_ID whose from-link is the input's own link — is
    the same join unit D4 uses to prove the counts were taken AT that entrance.
    So a target derived this way is MEASURED by construction, and the product
    refuses to rewrite the measurement, by name, instead of returning the
    contentless ``NO_TARGET_OBJECT`` it returned before this unit.
    """
    outcome = _node(_report(client), "1")["outcome"]
    assert outcome["outcome"] == "REFUSAL"
    assert outcome["reason_code"] == fixes.INPUT_VOLUME_IS_OBSERVED_DATA
    entrance = outcome["evidence"]["target"]["entrance_measurement"]
    assert entrance["status"] == fixes.ENTRANCE_MEASURED
    assert entrance["why"] == fixes.ENTRANCE_ROW_ON_INPUT_LINK
    assert (entrance["input_no"], entrance["link_no"]) == ("1", "101")
    # Every counted row on that link, not just the example named beside it.
    assert entrance["observed_on_input_link"] == 910.0
    assert "1" in outcome["rationale"]["en"] and "101" in outcome["rationale"]["en"]


def test_a_derived_route_target_produces_the_turn_split_proposal(client: TestClient):
    """Node 2: the total reconciles, one movement's split does not, and the
    decision governing that turn is derivable — so the screen gets the concrete
    edit instead of "no routing decision was named as the target"."""
    outcome = _node(_report(client), "2")["outcome"]
    assert outcome["outcome"] == "PROPOSAL"
    assert outcome["verb"] == "set_route_relative_flow"
    assert outcome["params"]["decision_no"] == "2"
    assert outcome["params"]["route_no"] == "1"
    # 600 of the 800 vehicles the decision governs, with siblings unchanged:
    # 190 * 0.75 / 0.25. NOT 190 * (600/1100) / (1 - 600/1100) = 253.3, which is
    # what taking the share against the NODE total would have produced.
    assert outcome["params"]["rel_flow"] == pytest.approx(570.0)
    assert outcome["evidence"]["observed_decision_total"] == 800.0
    assert fixes.ASSUME_DECISION_MOVEMENTS_COMPLETE in outcome["assumptions"]


def test_the_nodes_with_no_derivable_target_keep_the_honest_refusal(client: TestClient):
    """The states this unit must NOT paper over: a node counted with turn tokens
    and a node with two candidate inputs both still say NO_TARGET_OBJECT."""
    report = _report(client)
    for node_id in ("3", "4"):
        outcome = _node(report, node_id)["outcome"]
        assert outcome["outcome"] == "REFUSAL"
        assert outcome["reason_code"] == fixes.NO_TARGET_OBJECT


def test_a_derived_target_still_admits_its_assumption_where_it_is_used(derived, observed):
    """The demand target this module produces is an OBJECT name, not a claim
    that the object explains the miss.

    On the report above that admission never has to be printed, because D4
    refuses first. Handed the same target where the counts do NOT reach its link
    — the shape an AGENT-supplied target has — the proposal that comes out is
    labelled for exactly what it is: a demand ESTIMATE under
    ``INPUT_DRIVES_NODE_TOTAL``, from an entrance nothing verified.
    """
    target = next(t for t in _by_node(derived, "1") if t.vehicle_input_no)
    elsewhere = fixes.node_evidence_from_real_counts(observed["1"])
    outcome = fixes.map_node_counts(
        {**elsewhere, "modelled_total": 740.0},
        targets={
            "vehicle_input": {
                "input_no": target.vehicle_input_no,
                "current_volume": target.vehicle_input_current_volume,
                # The one thing changed: a link this node's counts never reach.
                "link_no": "999",
                "interval_no": 1,
            }
        },
    ).to_dict()
    assert outcome["outcome"] == "PROPOSAL"
    assert outcome["verb"] == "set_vehicle_input_volume"
    assert fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL in outcome["assumptions"]
    assert fixes.ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED in outcome["assumptions"]
    assert outcome["evidence"]["target"]["entrance_measurement"]["status"] == (
        fixes.ENTRANCE_UNVERIFIED
    )


# ===========================================================================
# 3. One derivation, one opinion — the rail reads what the report says
# ===========================================================================


def _entrance_statuses(report: dict[str, Any]) -> list[str]:
    """Every D4 verdict in the report, from BOTH places production puts one.

    Spelled out here rather than borrowed from ``geh_ladder.entrance_verdict``
    on purpose: a test that reads through the very helper the code reads through
    cannot notice a consumer that bypasses it, which is exactly the defect the
    rail had.
    """
    statuses: list[str] = []
    for entry in report["nodes"]:
        outcome = entry["outcome"]
        for block in (outcome, outcome.get("also_refused")):
            verdict = (((block or {}).get("evidence") or {}).get("target") or {}).get(
                "entrance_measurement"
            )
            if verdict:
                statuses.append(verdict["status"])
    return statuses


def test_the_displaced_d4_verdict_is_a_real_shape_this_fixture_produces(client: TestClient):
    """Node 5, end to end: total misses, split misses, entrance measured.

    ``map_node_counts`` holds the demand refusal and continues into the routing
    arm, so the MEASURED verdict comes back on ``also_refused`` — NOT under the
    outcome's own ``evidence.target``, which on this node carries nothing about
    the entrance at all. Pinned because the rail's step ⑩ was blind to precisely
    this shape until 2026-08-24.
    """
    outcome = _node(_report(client), "5")["outcome"]
    assert "entrance_measurement" not in (outcome.get("evidence") or {}).get("target", {})
    displaced = outcome["also_refused"]
    assert displaced["reason_code"] == fixes.INPUT_VOLUME_IS_OBSERVED_DATA
    entrance = displaced["evidence"]["target"]["entrance_measurement"]
    assert entrance["status"] == fixes.ENTRANCE_MEASURED
    assert (entrance["input_no"], entrance["link_no"]) == ("4", "105")


def test_the_rail_sees_the_same_entrance_verdicts_the_report_does(client: TestClient):
    """THE TWO-OPINIONS TEST.

    Step ⑩ counts D4 verdicts and pending demand proposals out of the report.
    Two ways it could lie, and both are covered here:

    * ``GET /rail`` building its report WITHOUT the targets ``GET
      /reality-check`` passes — the same workspace then shows a named MEASURED
      entrance on one screen and "no D4 verdict at all" on the rail beside it;
    * the rail reading only ``outcome.evidence.target`` — it then sees node 1's
      verdict and not node 5's displaced one, and reports one where the report
      carries two.

    The rail is therefore asserted against the report's own content, counted
    independently, never against a constant.
    """
    report = _report(client)
    verdicts = _entrance_statuses(report)
    assert verdicts == [fixes.ENTRANCE_MEASURED] * 2, (
        "fixture: node 1 (direct) and node 5 (displaced) are the two D4 verdicts"
    )

    rail = client.get(
        "/api/calibration/rail",
        params={"scenario_id": SCENARIO_ID, "period": "AM"},
        headers=admin_headers(),
    )
    assert rail.status_code == 200, rail.text
    step_10 = next(step for step in rail.json()["steps"] if step["number"] == 10)
    assert step_10["detail"]["entrance_measured"] == verdicts.count(fixes.ENTRANCE_MEASURED)
    assert step_10["detail"]["entrance_unverified"] == 0
    assert step_10["status"] == "DONE"
    assert "fixes.entrance_measurement" in step_10["source"]


def test_the_derivation_is_skipped_for_a_stored_observation_set(shadow):
    """No route here accepts a ``site_nodes`` map and the store refuses without
    one, so such a request is NOT_CHECKABLE with no nodes to target either way.
    Deriving would cost a workbook read to answer a question nobody asked."""
    assert (
        calibration_endpoint._derived_targets(shadow, period="AM", observation_set_id="obs-1")
        is None
    )


def test_an_unreadable_workbook_is_no_targets_rather_than_a_broken_screen(
    monkeypatch: pytest.MonkeyPatch, shadow
):
    def boom(_path):
        raise RuntimeError("the workbook is a directory")

    monkeypatch.setattr(validation, "load_base_workbook", boom)
    assert (
        calibration_endpoint._derived_targets(shadow, period="AM", observation_set_id=None)
        is None
    )


def test_no_network_is_no_targets(workbook: Path):
    assert calibration_endpoint._derived_targets(None, period="AM", observation_set_id=None) is None


# ===========================================================================
# 4. Unit #54 — the demand target a PERSON names
#
# The refusal above ("two candidates, no target, and the modeller names one")
# was, until this unit, a sentence with nowhere to be carried out. Node 4 is the
# real client shape in miniature: TWO input-bearing approach links, so the
# derivation refuses, the report says NO_TARGET_OBJECT and the ladder stops at
# NO_DEMAND_TARGET — while the calibration harness, which hardcodes one human's
# graph analysis for node 8, reaches CAPACITY_LIMIT on the same kind of node.
# ===========================================================================


def _candidate_rows(derived: dict[str, Any], node_id: str) -> list[dict[str, Any]]:
    return derived["nodes"][node_id]["vehicle_input"]["candidate_inputs"]


def _name_input(
    derived: dict[str, Any],
    node_id: str,
    input_no: str,
    link_no: str,
    *,
    by: str = "yunha",
    reason: str = "링크 205가 이 노드 유입의 대부분을 나릅니다 — 현장 조사로 확인",
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Record ONE mapping through the REAL store, with the REAL candidate list.

    The candidates come from the derivation itself rather than from a literal
    here, which is the property under test as much as any assertion below: a
    picker that offered something the derivation did not vouch for would be
    refused, and a test that passed its own list would never notice.
    """
    return demand_target_map.record_mapping(
        node_id=node_id,
        vehicle_input_no=input_no,
        link_no=link_no,
        reason=reason,
        named_by=by,
        candidates=_candidate_rows(derived, node_id) if candidates is None else candidates,
    )


def _derive_with_store(observed, shadow) -> dict[str, Any]:
    """The derivation as the ROUTER runs it: fresh, unrevoked mappings only."""
    return reality_check_targets.derive_targets(
        observed=observed,
        shadow_model=shadow,
        human_mappings=demand_target_map.fresh_index_payload(),
    )


def test_the_candidate_list_a_picker_needs_is_the_derivations_own(derived):
    """Node 4's six-candidate cousin, as ROWS rather than as bare ids.

    The store checks a modeller's choice against this exact list, and the picker
    renders it. One computation, so a screen cannot offer a choice the store
    would then refuse — which would be the two-opinions failure in its most
    user-hostile form.
    """
    rows = _candidate_rows(derived, "4")
    assert sorted((row["vehicle_input_no"], row["link_no"]) for row in rows) == [
        ("5", "203"),
        ("6", "205"),
    ]
    # The volume rides along so the picker shows real numbers — and ``None``
    # rides along unchanged where the .inpx never stated one, rather than being
    # defaulted to a zero a reader would take for a measurement.
    assert {row["current_volume"] for row in rows} == {None}
    assert _candidate_rows(derived, "1")[0] == {
        "vehicle_input_no": "1",
        "link_no": "101",
        "current_volume": 990.0,
        "name": "Main St EB",
    }
    # A node whose rows are turn tokens has no approach links, so it has no
    # candidates and nothing can be named for it — the store's own refusal.
    assert _candidate_rows(derived, "3") == []


def test_a_named_input_wins_over_the_refusal_for_its_node(wired, derived, observed, shadow):
    """THE UNIT. Node 4 stops being 분류 불가 because a person answered.

    Three things have to be true at once, and each is a separate way this could
    have gone wrong: the target EXISTS, it is labelled as a human's claim rather
    than as a derivation, and the candidates it was chosen from are still on the
    record — "six inputs were candidates and Yunha picked one" is a different
    statement from "the network says 113", and the screen must be able to make
    the first one.
    """
    _name_input(derived, "4", "6", "205")
    with_human = _derive_with_store(observed, shadow)

    demand = [t for t in _by_node(with_human, "4") if t.vehicle_input_no]
    assert len(demand) == 1
    assert (demand[0].vehicle_input_no, demand[0].vehicle_input_link_no) == ("6", "205")
    assert demand[0].vehicle_input_named_by == "yunha"
    assert demand[0].vehicle_input_named_at

    why = with_human["nodes"]["4"]["vehicle_input"]
    assert why["reason"] == reality_check_targets.HUMAN_NAMED_VEHICLE_INPUT
    assert why["chosen"] == "6"
    assert why["named_by"] == "yunha"
    assert why["mapping_reason"].startswith("링크 205")
    # A SIGNER DOES NOT MAKE IT PROVEN — the assumption travels with the name.
    assert why["assumption"] == fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL
    # ...and the alternatives are not erased by the choice.
    assert sorted(why["candidates"]) == ["5", "6"]


def test_naming_one_node_leaves_every_other_node_byte_identical(
    wired, derived, observed, shadow
):
    """A per-node override, not a mode.

    Compared field by field against the derivation with no mappings at all,
    because "node 4 changed" is only half the claim: the other half is that
    nothing else did, and a human mapping that quietly widened (or narrowed) any
    other node's answer would be a change to the report this unit never asked
    for.
    """
    _name_input(derived, "4", "6", "205")
    with_human = _derive_with_store(observed, shadow)

    for node_id in ("1", "2", "3", "5"):
        assert with_human["nodes"][node_id] == derived["nodes"][node_id], node_id
        assert [t.model_dump() for t in _by_node(with_human, node_id)] == [
            t.model_dump() for t in _by_node(derived, node_id)
        ], node_id


def test_a_stale_mapping_is_ignored_and_the_node_keeps_its_honest_refusal(
    wired, derived, observed, shadow, network: Path
):
    """The network moved under the claim, so the claim stops being one.

    Executed through the REAL staleness key — the ``.inpx``'s own bytes — rather
    than by setting a flag: the mapping says input 6 feeds an approach node 4 was
    counted entering from, and that is a statement about a topology which may no
    longer exist.
    """
    _name_input(derived, "4", "6", "205")
    assert _derive_with_store(observed, shadow)["nodes"]["4"]["vehicle_input"]["reason"] == (
        reality_check_targets.HUMAN_NAMED_VEHICLE_INPUT
    )

    network.write_text(network.read_text(encoding="utf-8") + "<!-- a lane was added -->", "utf-8")
    with_stale = _derive_with_store(observed, shadow)
    why = with_stale["nodes"]["4"]["vehicle_input"]
    assert why["reason"] == reality_check_targets.MULTIPLE_VEHICLE_INPUT_CANDIDATES
    assert why["chosen"] is None
    assert [t for t in _by_node(with_stale, "4") if t.vehicle_input_no] == []


def test_the_derivation_ignores_a_stale_mapping_even_if_it_is_handed_one(
    derived, observed, shadow
):
    """THE SECOND GUARD. The router filters; this re-checks.

    Two guards on purpose (``calibration_rail`` makes the same choice about
    revoked attestations, for the same stated reason): one of them being deleted
    must not silently restore a claim the network no longer supports.
    """
    stale = {
        "id": "a" * 12,
        "node_id": "4",
        "vehicle_input_no": "6",
        "link_no": "205",
        "named_by": "yunha",
        "named_at": "2026-08-24T09:00:00+00:00",
        "reason": "r",
        "stale": True,
        "revoked": False,
    }
    with_stale = reality_check_targets.derive_targets(
        observed=observed, shadow_model=shadow, human_mappings={"4": stale}
    )
    why = with_stale["nodes"]["4"]["vehicle_input"]
    assert why["reason"] == reality_check_targets.MULTIPLE_VEHICLE_INPUT_CANDIDATES
    assert why["human_mapping_ignored"] == reality_check_targets.HUMAN_MAPPING_STALE
    assert [t for t in _by_node(with_stale, "4") if t.vehicle_input_no] == []


def test_the_derivation_ignores_a_revoked_mapping_even_if_it_is_handed_one(
    derived, observed, shadow
):
    """The same second guard, for the other flag. A withdrawn claim is history."""
    revoked = {
        "id": "b" * 12,
        "node_id": "4",
        "vehicle_input_no": "6",
        "link_no": "205",
        "named_by": "yunha",
        "named_at": "2026-08-24T09:00:00+00:00",
        "reason": "r",
        "stale": False,
        "revoked": True,
    }
    with_revoked = reality_check_targets.derive_targets(
        observed=observed, shadow_model=shadow, human_mappings={"4": revoked}
    )
    why = with_revoked["nodes"]["4"]["vehicle_input"]
    assert why["reason"] == reality_check_targets.MULTIPLE_VEHICLE_INPUT_CANDIDATES
    assert why["human_mapping_ignored"] == reality_check_targets.HUMAN_MAPPING_REVOKED
    assert [t for t in _by_node(with_revoked, "4") if t.vehicle_input_no] == []


def test_a_revocation_through_the_store_takes_the_target_away(
    wired, derived, observed, shadow
):
    record = _name_input(derived, "4", "6", "205")
    assert _derive_with_store(observed, shadow)["nodes"]["4"]["vehicle_input"]["chosen"] == "6"

    demand_target_map.revoke_mapping(record["id"], revoked_by="minji", reason="아니었음")
    why = _derive_with_store(observed, shadow)["nodes"]["4"]["vehicle_input"]
    assert why["reason"] == reality_check_targets.MULTIPLE_VEHICLE_INPUT_CANDIDATES
    assert why["chosen"] is None


def test_withdrawing_the_latest_mapping_does_not_resurrect_the_one_it_replaced(
    wired, derived, observed, shadow, monkeypatch: pytest.MonkeyPatch
):
    """F1(a) AT THE DERIVATION, not only in the store.

    AUTHORITY: the refuter, 2026-08-24. The store's selection is what the
    derivation consumes, so the resurrection hole showed up HERE as the thing
    that actually matters: a node whose modeller withdrew their answer silently
    keeping an older, un-re-affirmed input as its demand target — and the report
    beside it presenting that as a live claim under its first author's name.
    """
    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T09:00:00+00:00")
    _name_input(derived, "4", "6", "205", by="yunha", reason="A: 205쪽")
    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T11:00:00+00:00")
    second = _name_input(derived, "4", "5", "203", by="minji", reason="B: 다시 보니 203쪽")
    assert _derive_with_store(observed, shadow)["nodes"]["4"]["vehicle_input"]["chosen"] == "5"

    demand_target_map.revoke_mapping(second["id"], revoked_by="minji", reason="B도 아니었음")

    why = _derive_with_store(observed, shadow)["nodes"]["4"]["vehicle_input"]
    assert why["reason"] == reality_check_targets.MULTIPLE_VEHICLE_INPUT_CANDIDATES
    assert why["chosen"] is None
    assert "named_by" not in why, "the withdrawn node must not credit anybody"
    assert [t for t in _by_node(_derive_with_store(observed, shadow), "4") if t.vehicle_input_no] == []


def test_a_re_named_input_stands_again_as_a_new_record(
    wired, derived, observed, shadow, monkeypatch: pytest.MonkeyPatch
):
    """F1(b) — the ONLY way an older mapping comes back, end to end.

    Not the old record waking up: a new one, with its own author, its own server
    stamp and its own stated reason. That is the whole point of refusing to
    resurrect — the claim on screen is always one somebody made on purpose.
    """
    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T09:00:00+00:00")
    first = _name_input(derived, "4", "6", "205", by="yunha", reason="A")
    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T11:00:00+00:00")
    second = _name_input(derived, "4", "5", "203", by="minji", reason="B")
    demand_target_map.revoke_mapping(second["id"], revoked_by="minji", reason="B 아니었음")

    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T13:00:00+00:00")
    again = _name_input(derived, "4", "6", "205", by="minji", reason="A가 맞았다 — 다시 확인")

    why = _derive_with_store(observed, shadow)["nodes"]["4"]["vehicle_input"]
    assert why["reason"] == reality_check_targets.HUMAN_NAMED_VEHICLE_INPUT
    assert why["chosen"] == "6"
    assert why["mapping_id"] == again["id"] != first["id"]
    assert why["named_by"] == "minji"
    assert why["named_at"] == "2026-08-24T13:00:00+00:00"


def test_withdrawing_a_superseded_mapping_leaves_the_standing_one_alone(
    wired, derived, observed, shadow, monkeypatch: pytest.MonkeyPatch
):
    """F1(c) — name A, withdraw A, name B: B decides the node."""
    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T09:00:00+00:00")
    first = _name_input(derived, "4", "6", "205", reason="A")
    demand_target_map.revoke_mapping(first["id"], revoked_by="minji", reason="A 아니었음")
    monkeypatch.setattr(demand_target_map, "_now_iso", lambda: "2026-08-24T11:00:00+00:00")
    second = _name_input(derived, "4", "5", "203", reason="B")

    why = _derive_with_store(observed, shadow)["nodes"]["4"]["vehicle_input"]
    assert why["reason"] == reality_check_targets.HUMAN_NAMED_VEHICLE_INPUT
    assert why["chosen"] == "5"
    assert why["mapping_id"] == second["id"]


def test_a_mapping_on_a_node_the_network_already_resolved_does_not_decide_it(
    wired, derived, observed, shadow
):
    """F3 — MACHINE OUTRANKS TESTIMONY, this codebase's #53 rule applied here.

    Node 1 has exactly ONE vehicle input on its counted approach, so the network
    has already answered and there was nothing for a person to choose between.
    The mapping cannot even disagree — with one candidate the store's own check
    guarantees it names that same input — so reporting HUMAN_NAMED would
    attribute a derivation to a modeller and put a name on a fact that did not
    need one.

    It is RECORDED rather than dropped: the audit block keeps the mapping and the
    code saying why it did not decide, so "I named an input and the screen
    credits the network" has an answer.
    """
    record = _name_input(derived, "1", "1", "101", reason="여기가 맞다고 생각함")

    why = _derive_with_store(observed, shadow)["nodes"]["1"]["vehicle_input"]
    assert why["reason"] == reality_check_targets.SINGLE_VEHICLE_INPUT
    assert why["chosen"] == "1"
    assert why["human_mapping_ignored"] == (
        reality_check_targets.HUMAN_MAPPING_DERIVATION_ALREADY_UNIQUE
    )
    # The mapping is visible, and it is visible as something that did NOT decide.
    assert why["mapping_id"] == record["id"]
    assert why["named_by"] == "yunha"
    assert "assumption" not in why
    # ...and the target the network produced carries no name at all.
    target = next(t for t in _by_node(_derive_with_store(observed, shadow), "1") if t.vehicle_input_no)
    assert target.vehicle_input_no == "1"
    assert target.vehicle_input_named_by is None
    assert target.vehicle_input_named_at is None


def test_a_mapping_whose_input_is_no_longer_a_candidate_is_ignored(observed, shadow):
    """The network can move WITHOUT its bytes changing under this process — a
    workspace switch, a workbook that stopped resolving a link — so the triple is
    re-checked against the LIVE candidate list here as well as at naming time."""
    orphan = {
        "id": "c" * 12,
        "node_id": "4",
        "vehicle_input_no": "999",
        "link_no": "205",
        "named_by": "yunha",
        "named_at": "2026-08-24T09:00:00+00:00",
        "reason": "r",
        "stale": False,
        "revoked": False,
    }
    derived = reality_check_targets.derive_targets(
        observed=observed, shadow_model=shadow, human_mappings={"4": orphan}
    )
    why = derived["nodes"]["4"]["vehicle_input"]
    assert why["human_mapping_ignored"] == reality_check_targets.HUMAN_MAPPING_NOT_A_CANDIDATE
    assert why["reason"] == reality_check_targets.MULTIPLE_VEHICLE_INPUT_CANDIDATES


def test_a_mapping_for_a_node_with_no_object_id_rows_reaches_nothing(observed, shadow):
    """Node 3's rows are turn tokens: the derivation never gets as far as the
    demand half, so a mapping filed against it cannot resurrect a link the
    counts do not resolve to. The absence ``fixes.entrance_measurement`` names is
    not one a signature can fill."""
    derived = reality_check_targets.derive_targets(
        observed=observed,
        shadow_model=shadow,
        human_mappings={
            "3": {
                "id": "d" * 12,
                "node_id": "3",
                "vehicle_input_no": "3",
                "link_no": "103",
                "named_by": "yunha",
                "named_at": "2026-08-24T09:00:00+00:00",
                "reason": "r",
                "stale": False,
                "revoked": False,
            }
        },
    )
    assert derived["nodes"]["3"]["vehicle_input"]["reason"] == (
        reality_check_targets.NO_OBJECT_ID_ROWS
    )
    assert _by_node(derived, "3") == []


def test_a_named_target_carries_the_assumption_and_the_name_into_the_proposal(
    wired, derived, observed, shadow
):
    """Where the demand ESTIMATE path is reachable, the card says whose it is.

    Same construction as the derived-target version of this test above: the link
    is moved somewhere the counts never reach, which is the only shape that gets
    past unit D4's refusal. What the proposal then carries is
    ``ASSUME_INPUT_DRIVES_NODE_TOTAL`` — unchanged, because a signature does not
    prove it — beside the name of the person who made the assumption.
    """
    _name_input(derived, "4", "6", "205")
    target = next(
        t for t in _by_node(_derive_with_store(observed, shadow), "4") if t.vehicle_input_no
    )
    elsewhere = fixes.node_evidence_from_real_counts(observed["4"])
    outcome = fixes.map_node_counts(
        {**elsewhere, "modelled_total": 550.0},
        targets={
            "vehicle_input": {
                "input_no": target.vehicle_input_no,
                "current_volume": 400.0,
                "link_no": "999",
                "interval_no": 1,
                "named_by": target.vehicle_input_named_by,
                "named_at": target.vehicle_input_named_at,
            }
        },
    ).to_dict()
    assert outcome["outcome"] == "PROPOSAL"
    assert fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL in outcome["assumptions"]
    named = outcome["evidence"]["target"]["human_named"]
    assert named["named_by"] == "yunha"
    assert named["assumption"] == fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL


def test_a_derived_target_carries_no_name_at_all(derived, observed):
    """The asymmetry that makes the block mean something: a target the NETWORK
    vouched for has ``human_named: None``, so a non-None value means exactly one
    thing and cannot be mistaken for a default."""
    target = next(t for t in _by_node(derived, "1") if t.vehicle_input_no)
    assert target.vehicle_input_named_by is None
    outcome = fixes.map_node_counts(
        {**fixes.node_evidence_from_real_counts(observed["1"]), "modelled_total": 740.0},
        targets={
            "vehicle_input": {
                "input_no": target.vehicle_input_no,
                "current_volume": target.vehicle_input_current_volume,
                "link_no": "999",
                "interval_no": 1,
            }
        },
    ).to_dict()
    assert outcome["evidence"]["target"]["human_named"] is None


# ===========================================================================
# 5. #54 end to end — the screen, the routes, and the ONE derivation
# ===========================================================================


def _post_target(client: TestClient, **body):
    payload = {
        "node_id": "4",
        "vehicle_input_no": "6",
        "link_no": "205",
        "reason": "링크 205가 이 노드 유입의 대부분입니다 — 현장 조사로 확인",
        "period": "AM",
    }
    payload.update(body)
    return client.post(
        "/api/calibration/demand-targets", json=payload, headers=admin_headers()
    )


def test_the_node_that_refuses_auto_derivation_is_reclassified_once_a_human_names_one(
    client: TestClient,
):
    """THE WHOLE UNIT, end to end, through the REAL ``_reality_check`` seam.

    BEFORE: node 4 has two input-bearing approach links, so the derivation
    refuses, ``map_node_counts`` answers ``NO_TARGET_OBJECT``, and unit #43's
    ladder reports UNCLASSIFIABLE / ``NO_DEMAND_TARGET`` — the 분류 불가 the user
    saw on node 8 of the real client model on 2026-08-24.

    AFTER: the same run, the same workbook, the same network. One named person
    has chosen one of the candidates, and the ladder can finally establish what
    every rung below it rests on — that this entrance's volume is measured data —
    and descends to CAPACITY_LIMIT, which is the rung the calibration harness
    reaches on node 8 because it hardcodes exactly this mapping.
    """
    before = _node(_report(client), "4")
    assert before["outcome"]["reason_code"] == fixes.NO_TARGET_OBJECT
    assert before["diagnosis"]["rung"] == "UNCLASSIFIABLE"
    assert "NO_DEMAND_TARGET" in before["diagnosis"]["missing_evidence"]

    created = _post_target(client)
    assert created.status_code == 200, created.text
    assert created.json()["named_by"] == "suite-admin"
    # The POST decides no status: it returns the RECORD, and there is no rung,
    # no diagnosis and no verdict anywhere on it.
    assert not {"status", "rung", "diagnosis"} & set(created.json())

    after = _node(_report(client), "4")
    assert "NO_DEMAND_TARGET" not in after["diagnosis"]["missing_evidence"]
    assert after["diagnosis"]["rung"] == "CAPACITY_LIMIT"
    # And the D4 verdict that unlocked it names the input the human chose.
    entrance = after["outcome"]["also_refused"]["evidence"]["target"]["entrance_measurement"]
    assert (entrance["input_no"], entrance["link_no"]) == ("6", "205")
    assert entrance["status"] == fixes.ENTRANCE_MEASURED
    # ...beside the name of the person whose assumption it is.
    named = after["outcome"]["also_refused"]["evidence"]["target"]["human_named"]
    assert named["named_by"] == "suite-admin"
    assert named["assumption"] == fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL


def test_the_rail_sees_the_named_target_the_report_sees(client: TestClient):
    """THE TWO-OPINIONS TEST, extended to the human mapping.

    ``GET /rail`` and ``GET /reality-check`` build their reports from the SAME
    ``_derived_targets`` — including its human mappings. A mapping threaded into
    one and not the other would put a named MEASURED entrance on one screen and
    "no D4 verdict at all" on the rail beside it, which is exactly the shape the
    targets unit's own version of this test was written against.
    """
    assert _post_target(client).status_code == 200
    report = _report(client)
    verdicts = _entrance_statuses(report)
    assert verdicts == [fixes.ENTRANCE_MEASURED] * 3, (
        "fixture: node 1 (direct), node 4 (human-named) and node 5 (displaced)"
    )

    rail = client.get(
        "/api/calibration/rail",
        params={"scenario_id": SCENARIO_ID, "period": "AM"},
        headers=admin_headers(),
    )
    assert rail.status_code == 200, rail.text
    step_10 = next(step for step in rail.json()["steps"] if step["number"] == 10)
    assert step_10["detail"]["entrance_measured"] == verdicts.count(fixes.ENTRANCE_MEASURED)


def test_the_candidates_route_serves_the_derivations_own_list(client: TestClient):
    response = client.get(
        "/api/calibration/demand-targets/candidates",
        params={"period": "AM"},
        headers=admin_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    node_4 = body["nodes"]["4"]
    assert node_4["reason"] == reality_check_targets.MULTIPLE_VEHICLE_INPUT_CANDIDATES
    assert sorted(
        (row["vehicle_input_no"], row["link_no"]) for row in node_4["candidates"]
    ) == [("5", "203"), ("6", "205")]
    assert node_4["mapping"] is None
    # A node the derivation resolved on its own needs no picker, and says so by
    # carrying its own reason rather than by carrying no candidates.
    assert body["nodes"]["1"]["reason"] == reality_check_targets.SINGLE_VEHICLE_INPUT
    assert body["nodes"]["3"]["candidates"] == []

    assert _post_target(client).status_code == 200
    after = client.get(
        "/api/calibration/demand-targets/candidates",
        params={"period": "AM"},
        headers=admin_headers(),
    ).json()
    standing = after["nodes"]["4"]["mapping"]
    assert (standing["vehicle_input_no"], standing["link_no"]) == ("6", "205")
    assert standing["named_by"] == "suite-admin"
    assert standing["stale"] is False
    assert after["nodes"]["4"]["reason"] == reality_check_targets.HUMAN_NAMED_VEHICLE_INPUT
    assert after["nodes"]["4"]["human_mapping_ignored"] is None


def test_a_stale_mapping_is_still_SHOWN_with_the_reason_it_was_ignored(
    client: TestClient, network: Path
):
    """The screen and the derivation deliberately disagree about what to DO with
    a stale mapping, and that is the point.

    The derivation ignores it and the node goes back to 분류 불가 — a claim about
    a network that moved is not a claim about this one. But a modeller who
    answered this node once must be told THAT, not shown an empty picker as if
    they had never answered: the record, its ``stale`` flag and the ignore code
    all survive on this payload.
    """
    assert _post_target(client).status_code == 200
    network.write_text(network.read_text(encoding="utf-8") + "<!-- a lane was added -->", "utf-8")

    body = client.get(
        "/api/calibration/demand-targets/candidates",
        params={"period": "AM"},
        headers=admin_headers(),
    ).json()
    node_4 = body["nodes"]["4"]
    assert node_4["mapping"]["stale"] is True
    assert node_4["mapping"]["named_by"] == "suite-admin"
    assert node_4["human_mapping_ignored"] == reality_check_targets.HUMAN_MAPPING_STALE
    assert node_4["reason"] == reality_check_targets.MULTIPLE_VEHICLE_INPUT_CANDIDATES
    # ...and the node really has fallen back, on the report the screen reads.
    assert "NO_DEMAND_TARGET" in _node(_report(client), "4")["diagnosis"]["missing_evidence"]


def test_an_anonymous_caller_can_neither_name_nor_revoke(client: TestClient):
    """A mapping nobody can be named for is an assumption laundered into a
    derivation — so it is a 401 before any of the store's own rules run."""
    anonymous = client.post(
        "/api/calibration/demand-targets",
        json={"node_id": "4", "vehicle_input_no": "6", "link_no": "205", "reason": "x"},
    )
    assert anonymous.status_code == 401

    created = _post_target(client).json()
    assert (
        client.post(
            f"/api/calibration/demand-targets/{created['id']}/revoke", json={"reason": "아니었음"}
        ).status_code
        == 401
    )
    # ...and the claim still stands.
    node_4 = _node(_report(client), "4")
    assert "NO_DEMAND_TARGET" not in node_4["diagnosis"]["missing_evidence"]


@pytest.mark.parametrize(
    "forged", [{"named_by": "somebody-else"}, {"named_at": "1999-01-01T00:00:00Z"}]
)
def test_the_namer_comes_from_the_session_and_a_body_cannot_claim_one(
    client: TestClient, forged: dict
):
    """``extra="forbid"`` means an invented ``named_by`` is a 422 at the door,
    not a field that quietly does nothing — the same door
    ``StepAttestationRequest`` and ``ParameterSearchStartRequest`` close."""
    assert _post_target(client, **forged).status_code == 422


def test_naming_an_input_no_candidate_list_vouches_for_is_a_named_422(client: TestClient):
    response = _post_target(client, vehicle_input_no="999")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["reason_code"] == "demand_target_unverifiable_triple"
    assert set(detail["message"]) == {"ko", "en", "ja"}
    assert {entry["vehicle_input_no"] for entry in detail["candidates"]} == {"5", "6"}


def test_naming_a_real_input_on_the_wrong_link_is_refused_over_http(client: TestClient):
    """Input 6 feeds link 205, not 203. Both halves exist; the TRIPLE does not."""
    response = _post_target(client, vehicle_input_no="6", link_no="203")
    assert response.status_code == 422
    assert response.json()["detail"]["reason_code"] == "demand_target_unverifiable_triple"


def test_naming_for_a_node_whose_candidate_list_is_EMPTY_is_refused_at_the_router(
    client: TestClient,
):
    """F5 — the empty list is not the missing list, and both are refusals.

    AUTHORITY: the refuter, 2026-08-24 — the store's version of this was covered
    and the ROUTER's was not. Node 3's rows are workbook turn tokens, so it
    resolves to no approach link and its ``candidate_inputs`` is ``[]`` (not
    ``None``: the node IS in the derivation, it simply has nothing to offer). The
    422 has to come back with the candidates it could have had, which is the
    empty list — a 409 here would say "this server could not compute a list" and
    that is a different, untrue sentence.
    """
    response = _post_target(client, node_id="3", vehicle_input_no="3", link_no="103")
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["reason_code"] == "demand_target_unverifiable_triple"
    assert detail["candidates"] == []
    assert set(detail["message"]) == {"ko", "en", "ja"}


def test_the_candidate_list_is_the_same_with_and_without_mappings(
    client: TestClient, wired, derived, observed, shadow
):
    """F4 — the invariant the POST's validation quietly rests on.

    A candidate list is the inputs feeding a node's counted approach links, and
    no signature moves a link, so it is mapping-independent BY CONSTRUCTION. The
    POST now threads the mappings like every other site on this router, which
    makes that construction the only thing keeping the validation list and the
    picker's list identical — so it is pinned here rather than left as a property
    a refactor could break in silence.
    """
    _name_input(derived, "4", "6", "205")
    standing = demand_target_map.index_payload()

    def candidates(mappings):
        answer = calibration_endpoint._derive_all(
            shadow, period="AM", observation_set_id=None, human_mappings=mappings
        )
        return {
            node: why["vehicle_input"]["candidate_inputs"]
            for node, why in answer["nodes"].items()
        }

    assert candidates(standing) == candidates(None)
    # And the ROUTE, which is the thing that actually validates, still refuses
    # the same triple with a mapping standing as it did without one.
    assert _post_target(client, vehicle_input_no="999").status_code == 422


def test_naming_for_a_node_the_derivation_never_saw_is_a_named_409(client: TestClient):
    """No candidate list at all — not an empty one — because the node is not in
    the workbook. 409 rather than 404: the mapping was refused, and the reason is
    about what this server could compute, not about a missing resource."""
    response = _post_target(client, node_id="77")
    assert response.status_code == 409
    assert response.json()["detail"]["reason_code"] == "demand_target_no_candidates"


@pytest.mark.parametrize("reason", ["   \n\t ", ""])
def test_a_whitespace_only_reason_is_refused_over_http(client: TestClient, reason: str):
    """``{"reason": "   "}`` clears pydantic (it IS a string) and is caught by the
    store's own strip — the case a body-shape check alone would let through."""
    response = _post_target(client, reason=reason)
    assert response.status_code == 422
    assert response.json()["detail"]["reason_code"] == "demand_target_reason_required"


def test_a_whitespace_only_reason_on_a_REVOCATION_is_refused_over_http(client: TestClient):
    created = _post_target(client).json()
    response = client.post(
        f"/api/calibration/demand-targets/{created['id']}/revoke",
        json={"reason": "   \n\t "},
        headers=admin_headers(),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["reason_code"] == "demand_target_reason_required"
    # Nothing was withdrawn.
    assert "NO_DEMAND_TARGET" not in _node(_report(client), "4")["diagnosis"]["missing_evidence"]


def test_a_revocation_with_no_reason_field_is_a_422(client: TestClient):
    created = _post_target(client).json()
    response = client.post(
        f"/api/calibration/demand-targets/{created['id']}/revoke",
        json={},
        headers=admin_headers(),
    )
    assert response.status_code == 422


def test_revoking_twice_is_a_named_409_over_http(client: TestClient):
    created = _post_target(client).json()
    first = client.post(
        f"/api/calibration/demand-targets/{created['id']}/revoke",
        json={"reason": "아니었음"},
        headers=admin_headers(),
    )
    assert first.status_code == 200
    second = client.post(
        f"/api/calibration/demand-targets/{created['id']}/revoke",
        json={"reason": "또 아니었음"},
        headers=admin_headers(),
    )
    assert second.status_code == 409
    assert second.json()["detail"]["reason_code"] == "demand_target_already_revoked"


@pytest.mark.parametrize("mapping_id", ["deadbeefcafe", "not-an-id", "0" * 32])
def test_revoking_an_unknown_mapping_is_a_named_404(client: TestClient, mapping_id: str):
    """404 for all three shapes of "no such record": a malformed id and an id
    that simply is not here must be indistinguishable, or a caller could
    enumerate which ids exist in a project they cannot otherwise read."""
    response = client.post(
        f"/api/calibration/demand-targets/{mapping_id}/revoke",
        json={"reason": "x"},
        headers=admin_headers(),
    )
    assert response.status_code == 404, response.text
    assert response.json()["detail"]["reason_code"] == "demand_target_not_found"


def test_revoking_over_http_puts_the_node_back_where_it_was(client: TestClient):
    created = _post_target(client).json()
    revoked = client.post(
        f"/api/calibration/demand-targets/{created['id']}/revoke",
        json={"reason": "다시 보니 203쪽이었음"},
        headers=admin_headers(),
    )
    assert revoked.status_code == 200
    assert [event["event"] for event in revoked.json()["history"]] == ["named", "revoked"]

    node_4 = _node(_report(client), "4")
    assert node_4["outcome"]["reason_code"] == fixes.NO_TARGET_OBJECT
    assert "NO_DEMAND_TARGET" in node_4["diagnosis"]["missing_evidence"]


@pytest.fixture(autouse=True)
def _network_bytes_restored(network: Path):
    """The ``network`` fixture is MODULE scoped and two tests above rewrite its
    bytes on purpose — that IS the #54 staleness key. Restore them after every
    test so a deliberate mutation cannot leak into whatever runs next and make
    this file's results depend on its own order."""
    original = network.read_bytes()
    try:
        yield
    finally:
        network.write_bytes(original)


@pytest.fixture(autouse=True)
def _workspace(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """The routes are workspace-scoped; bind one the way every other endpoint
    test in this suite does."""
    root = tmp_path_factory.mktemp("targets-workspaces")
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", root / "workspaces")
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", root / "uploads")
    token = workspaces.set_current_workspace_id(workspaces.default_workspace_id())
    try:
        yield
    finally:
        workspaces.reset_current_workspace_id(token)


# ===========================================================================
# 5. Unit #56 (c) — the derivation-audit AGENT TOOL reads the SAME derivation
#
# THE GAP: an assistant asked to explain or fix a target-less node had only
# get_vehicle_inputs, which lists every input in the network and cannot say
# which feed THIS node's counted approaches or why none/several were
# chosen — so it re-issued the identical call, hit the read-tier
# duplicate-call guard, and burned the whole iteration cap. This tool reads
# reality_check_targets.derive_targets_for_active_dataset -- the SAME
# extraction routers/calibration.py's `_derive_all` now forwards to -- so the
# tool and the screen's picker can never disagree about one node's audit.
# ===========================================================================


@pytest.fixture()
def network_active(wired: None, network: Path):
    """#56 (c)'s tool reads the network through ``tools_network``'s OWN
    active-inpx override (``_current_shadow_model``) -- a SEPARATE seam from
    ``datasets.get_active_inpx_path``, which ``wired`` above already points at
    ``network`` for the router path. Both must name the same file, or the
    tool and the router would grade two different networks.
    """
    tools_network.set_active_inpx(network)
    try:
        yield
    finally:
        tools_network.set_active_inpx(None)


def test_the_tool_registers_read_tier(network_active: None):
    spec = default_registry.get("get_demand_target_derivation")
    assert spec is not None
    assert spec.tier == "read"


def test_the_tool_reads_the_same_audit_the_picker_reads(network_active: None):
    """Node 1 (one candidate, resolved) and node 4 (two candidates, no
    dominance pick) must read IDENTICALLY to ``_demand_target_payload``'s own
    account -- the same derivation, never a second opinion."""
    envelope = dispatch("get_demand_target_derivation", {"period": "AM"})
    assert envelope["status"] == "ok", envelope
    audit = envelope["data"]["demand_target_derivation"]
    assert audit["available"] is True
    assert audit["reason"] is None

    node1 = audit["nodes"]["1"]
    assert node1["reason"] == reality_check_targets.SINGLE_VEHICLE_INPUT
    assert node1["chosen"] == "1"
    assert node1["assumption"] == fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL
    assert node1["mapping"] is None

    node4 = audit["nodes"]["4"]
    assert node4["reason"] == reality_check_targets.MULTIPLE_VEHICLE_INPUT_CANDIDATES
    assert node4["chosen"] is None
    # THE MUTATION SURFACE: the tool must never invent a candidate, and must
    # never drop a real one. "5" and "6" are the two inputs the module-level
    # fixture docstring names as node 4's two counted-approach candidates
    # (test_two_candidates_produce_no_target_and_no_dominance_pick pins the
    # same pair against the derivation directly).
    assert sorted(candidate["vehicle_input_no"] for candidate in node4["candidates"]) == ["5", "6"]
    assert node4["assumption"] is None, "nothing was chosen, so no assumption is carried"


def test_the_tool_filters_to_one_node(network_active: None):
    envelope = dispatch("get_demand_target_derivation", {"period": "AM", "node_id": "4"})
    audit = envelope["data"]["demand_target_derivation"]
    assert set(audit["nodes"]) == {"4"}


def test_the_tool_names_the_known_nodes_for_an_unknown_one(network_active: None):
    envelope = dispatch(
        "get_demand_target_derivation", {"period": "AM", "node_id": "not-a-real-node"}
    )
    assert envelope["status"] == "ok", envelope
    audit = envelope["data"]["demand_target_derivation"]
    assert audit["reason"] == "NODE_NOT_FOUND"
    assert audit["nodes"] == {}
    assert "1" in audit["known_node_ids"]
    assert "4" in audit["known_node_ids"]


def test_the_tool_surfaces_a_standing_54_mapping(network_active: None, derived, observed, shadow):
    """A human's #54 naming on node 4 must appear on THIS tool's answer too --
    the same store the screen's picker reads, read through the same field
    name (``mapping``) the router's own ``_demand_target_payload`` uses."""
    _name_input(derived, "4", "6", "205")

    envelope = dispatch("get_demand_target_derivation", {"period": "AM", "node_id": "4"})
    node4 = envelope["data"]["demand_target_derivation"]["nodes"]["4"]
    assert node4["mapping"] is not None
    assert node4["mapping"]["vehicle_input_no"] == "6"
    assert node4["mapping"]["named_by"] == "yunha"
    assert node4["mapping"]["stale"] is False


def test_the_tool_degrades_to_no_derivation_over_an_unreadable_workbook(
    network_active: None, monkeypatch: pytest.MonkeyPatch
):
    def boom(_path):
        raise RuntimeError("the workbook is a directory")

    monkeypatch.setattr(validation, "load_base_workbook", boom)
    envelope = dispatch("get_demand_target_derivation", {"period": "AM"})
    assert envelope["status"] == "ok", envelope
    audit = envelope["data"]["demand_target_derivation"]
    assert audit["available"] is False
    assert audit["reason"] == "NO_DERIVATION"
    assert audit["nodes"] == {}

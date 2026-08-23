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
from services import datasets, validation, workspaces
from services.agent import fixes, reality_check, reality_check_targets, tools_metrics, tools_network

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
            [(object_id, vehs, None, "ALL") for object_id, vehs in _RUN_VEHS.items()],
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

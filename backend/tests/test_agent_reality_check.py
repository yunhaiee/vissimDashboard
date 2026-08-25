"""Unit U5: the reality check — the RUN versus the observed counts.

This file carries roadmap row 2.1's **exit demonstration**, and that test is
the reason the rest exists: end to end, entirely offline, with zero COM, the
loop must **auto-propose one real fix AND correctly refuse one anomaly as
genuine behaviour**. Nothing in that demonstration is hand-written:

* the dataset is ``tools/make_demo_data.py``'s output (unit U4a);
* the observed side is parsed by the REAL loader
  (``validation.load_base_workbook``);
* the modelled side is built by U1's REAL ``_collect_run_evidence`` over that
  dataset's own result database — no fabricated ``run_result`` dict;
* the proposals and refusals are U4's ``map_node_counts``, reached through
  ``reality_check.check_run_against_counts``;
* the proposals' params are validated against the LIVE tool registry's own
  pydantic models.

The rest of the file pins the honesty properties the report claims: the join
names both of its failure directions, the workbook's own model column is
never used as the modelled side, and every sad path is a refusal with a
reason rather than an empty success.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from services import results_evidence, validation
from services.agent import fixes, reality_check
from services.agent.tools_run import _collect_run_evidence
from tools import make_demo_data


# ---------------------------------------------------------------------------
# The fixture dataset (unit U4a), generated once
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("demo-data")
    argv = sys.argv
    sys.argv = ["make_demo_data.py", "--out", str(out)]
    try:
        make_demo_data.main()
    finally:
        sys.argv = argv
    return out


@pytest.fixture(scope="module")
def run_work_dir(demo_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A work dir holding ONE result database.

    Deliberately one, not the scenario's three: U1's
    ``read_movement_class_rows`` SUMS VEHS across every .db it is given, so a
    3-seed directory would carry three runs' worth of vehicles. That is why
    ``tools_metrics.build_reality_check_report`` refuses a multi-seed run
    outright (test below) — here we hand it the single-seed shape a
    ``run_simulation`` with default seeds produces.
    """
    work = tmp_path_factory.mktemp("run-work")
    shutil.copy(
        demo_dir / "Scenarios" / "S000001" / "S000001.results" / "run_001.db",
        work / "run_001.db",
    )
    return work


@pytest.fixture(scope="module")
def run_result(demo_dir: Path, run_work_dir: Path) -> dict:
    """U1's REAL evidence collector over the fixture's own result database."""
    return _collect_run_evidence(
        run_work_dir,
        demo_dir / "practice.inpx",
        duration_s=3600,
        evaluation_configured=False,
    )


@pytest.fixture(scope="module")
def observed(demo_dir: Path) -> dict:
    return validation.load_base_workbook(str(demo_dir / "real_counts.xlsx"))


# ---------------------------------------------------------------------------
# What the SHADOW MODEL would supply — read out of the network, not invented
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def routing_decisions(demo_dir: Path) -> dict[str, dict[str, float]]:
    """``{decision_no: {route_no: relFlow}}`` parsed from the demo .inpx.

    The shadow model is where a caller really gets this
    (``get_routing_decisions``); the test reads the same network file so the
    numbers are the network's, not the test author's.
    """
    root = ET.parse(demo_dir / "practice.inpx").getroot()
    return {
        decision.get("no"): {
            route.get("no"): float(route.get("relFlow").split(":")[1]) for route in decision
        }
        for decision in root.find("vehicleRoutingDecisionsStatic")
    }


@pytest.fixture(scope="module")
def decision_movements(demo_dir: Path) -> dict[str, list[str]]:
    """``{decision_no: [workbook turn token, ...]}`` — what each decision governs.

    A static routing decision names its APPROACH link and each of its routes
    names a destination link, so the turns it governs are exactly
    ``(node, approach link, destLink)``. That is what a caller reads out of
    ``get_routing_decisions``; the test parses the same .inpx rather than
    hand-listing turns, so the scope cannot drift from the network.

    For the demo network this yields decision 1 -> node 1's three W-approach
    turns and decision 2 -> node 2's three E-approach turns. Neither decision
    governs its whole intersection (12 turns each), which is precisely why the
    denominator matters.
    """
    root = ET.parse(demo_dir / "practice.inpx").getroot()
    scopes: dict[str, list[str]] = {}
    for decision in root.find("vehicleRoutingDecisionsStatic"):
        from_link = int(decision.get("link"))
        node = next(n for n, f, _t, _v, _d in make_demo_data.MOVEMENTS if f == from_link)
        scopes[decision.get("no")] = [
            make_demo_data.movement_direction(node, from_link, int(route.get("destLink")))
            for route in decision
        ]
    return scopes


@pytest.fixture(scope="module")
def movement_ids() -> dict[str, dict[str, str]]:
    """``{node_id: {workbook turn token: run OBJECT_ID}}``.

    The join the run evidence cannot supply by itself: an OBJECT_ID says which
    links a movement connects, the workbook says which turn was counted, and
    only the network knows they are the same thing.
    """
    return {
        str(node): {
            make_demo_data.movement_direction(*key): make_demo_data.movement_object_id(*key)
            for key in make_demo_data.node_movements(node)
        }
        for node in (1, 2)
    }


@pytest.fixture(scope="module")
def targets(
    routing_decisions: dict[str, dict[str, float]], decision_movements: dict[str, list[str]]
) -> dict[str, dict]:
    """U4's target payload for both demo nodes.

    * node 1's demand target is vehicle input 1 (link 101, the EB approach);
      its current volume is the demand that approach carries, i.e. the sum of
      its three movements' base volumes. Its LINK is passed too (unit D4): it
      is what lets the mapping ask whether the counts were taken AT that
      entrance. On this fixture the answer is "cannot tell" — the demo
      workbook records turns as tokens (``W-E``), not as link-bearing
      OBJECT_IDs — which is a NAMED absence, not a pass; see
      ``test_exit_1_...`` below.
    * node 2's routing target is decision 2 route 1 — the WB through movement
      (207 -> 141), which is the E-W turn the fixture drains — WITH the scope
      that decision governs (its three E-approach turns), because the observed
      share is taken against those, not against node 2's whole demand.
    """
    eb_approach_volume = float(
        sum(volume for node, from_link, _to, volume, _delay in make_demo_data.MOVEMENTS
            if node == 1 and from_link == 101)
    )
    siblings = sum(
        rel_flow for route_no, rel_flow in routing_decisions["2"].items() if route_no != "1"
    )
    return {
        "1": {
            "vehicle_input": {
                "input_no": "1",
                "current_volume": eb_approach_volume,
                "link_no": "101",
                "interval_no": 1,
            }
        },
        "2": {
            "routes": {
                "E-W": {
                    "decision_no": "2",
                    "route_no": "1",
                    "current_rel_flow": routing_decisions["2"]["1"],
                    "sibling_rel_flow_total": siblings,
                    "decision_movements": decision_movements["2"],
                    "interval_no": 1,
                }
            }
        },
    }


def _node(report: dict, node_id: str) -> dict:
    return next(entry for entry in report["nodes"] if entry["node_id"] == node_id)


def _keys_anywhere(payload: Any) -> set[str]:
    """Every mapping key in a nested payload, at any depth."""
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.add(str(key))
            found |= _keys_anywhere(value)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            found |= _keys_anywhere(item)
    return found


# ===========================================================================
# THE EXIT DEMONSTRATION (roadmap row 2.1)
# ===========================================================================


@pytest.fixture(scope="module")
def am_report(run_result: dict, observed: dict, targets: dict, movement_ids: dict) -> dict:
    return reality_check.check_run_against_counts(
        run_result, observed, period="AM", targets=targets, movement_ids=movement_ids
    )


@pytest.fixture(scope="module")
def pm_report(run_result: dict, observed: dict, targets: dict, movement_ids: dict) -> dict:
    return reality_check.check_run_against_counts(
        run_result, observed, period="PM", targets=targets, movement_ids=movement_ids
    )


def test_the_run_evidence_is_u1s_own_and_carries_the_node_token(run_result: dict):
    """The premise of the whole unit: the modelled side is the RUN's."""
    assert run_result["evidence"][results_evidence.MOVEMENT_INTERVAL_CLASS_TABLE].startswith(
        "PRESENT("
    )
    volumes = run_result["movement_volumes"]
    assert len(volumes) == len(make_demo_data.MOVEMENTS)
    for object_id, row in volumes.items():
        assert row["node"] == results_evidence.node_token(object_id)
        assert row["vehs"] is not None


def test_exit_1_demand_miss_yields_a_schema_valid_vehicle_input_proposal(am_report: dict):
    """(1) auto-propose one real fix: the node TOTAL misses -> demand.

    UNIT D4 (2026-08-12): this entrance is UNVERIFIED, so the proposal stands —
    and stands LABELLED. The demo workbook records its turns as tokens
    (``W-E``) rather than as link-bearing Vissim OBJECT_IDs, so even with
    input 1's link supplied there is no row that can be resolved to link 101,
    and the mapping cannot tell whether the counts were taken at that
    entrance. Policy for that state is ask-the-user-first and the approval
    card is the ask, so the fix is still proposed — as a NAMED demand
    estimate, with the reason it could not be verified travelling in the
    evidence. (The MEASURED case, where the workbook DOES carry OBJECT_IDs and
    the demand edit is refused outright, is pinned in test_agent_fixes.py's
    D4 section against the real demo intersection's numbers.)
    """
    assert am_report["status"] == reality_check.CHECKED

    node = _node(am_report, "1")
    outcome = node["outcome"]
    assert outcome["outcome"] == fixes.PROPOSAL
    assert outcome["scope"] == fixes.DEMAND_LEVEL
    assert outcome["verb"] == "set_vehicle_input_volume"

    # The LIVE registry's own pydantic model, not a copy of the field list.
    ok, error = fixes.validate_params(outcome["verb"], outcome["params"])
    assert ok, error

    # The value is the stated arithmetic, re-checkable from the evidence.
    evidence = outcome["evidence"]
    assert evidence["node_total_geh"] >= validation.GEH_GOOD_LT
    assert 7.0 <= evidence["node_total_geh"] <= 8.0, evidence
    expected = round(
        evidence["target"]["current_volume"]
        * evidence["observed_total"]
        / evidence["modelled_total"],
        2,
    )
    assert outcome["params"]["volume"] == expected
    assert outcome["params"]["input_no"] == "1"
    assert outcome["confidence"] == fixes.CONFIDENCE_DEMAND_ESTIMATE

    # ... and the D4 label rides all the way into the report a human reads.
    assert fixes.ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED in outcome["assumptions"]
    entrance = evidence["target"]["entrance_measurement"]
    assert entrance["status"] == fixes.ENTRANCE_UNVERIFIED
    assert entrance["why"] == fixes.ENTRANCE_NO_LINK_KEYED_ROWS
    assert entrance["link_no"] == "101"
    assert "DEMAND ESTIMATE" in outcome["rationale"]["en"]
    assert "수요 추정" in outcome["rationale"]["ko"]
    # (Whether it survives the approval loop's own preflight gate is pinned
    # separately, by test_proposals_are_preflight_clean below.)


def _proposal_kwargs(outcome: dict) -> dict:
    """Rebuild a FixProposal from its dict form (what crosses a JSON boundary)."""
    return {
        "key": outcome["key"],
        "key_kind": outcome["key_kind"],
        "outcome": outcome["outcome"],
        "scope": outcome["scope"],
        "verb": outcome["verb"],
        "params": outcome["params"],
        "evidence": outcome["evidence"],
        "rationale_ko": outcome["rationale"]["ko"],
        "rationale_en": outcome["rationale"]["en"],
        "rationale_ja": outcome["rationale"]["ja"],
        "confidence": outcome["confidence"],
        "reason_code": outcome["reason_code"],
    }


def test_exit_2_split_miss_yields_a_schema_valid_route_relative_flow_proposal(am_report: dict):
    """(2) the total reconciles but a movement's SPLIT misses -> routing."""
    node = _node(am_report, "2")
    outcome = node["outcome"]
    assert outcome["outcome"] == fixes.PROPOSAL
    assert outcome["scope"] == fixes.ROUTING_SPLIT_LEVEL
    assert outcome["verb"] == "set_route_relative_flow"

    ok, error = fixes.validate_params(outcome["verb"], outcome["params"])
    assert ok, error

    evidence = outcome["evidence"]
    assert evidence["node_total_geh"] < validation.GEH_GOOD_LT       # demand is NOT the problem
    assert evidence["movement_geh"] >= validation.GEH_GOOD_LT        # the split is
    assert evidence["movement_id"] == "E-W"
    # ... and the demand-level refusal it displaced is carried, not dropped.
    assert outcome["also_refused"]["reason_code"] == fixes.NODE_TOTAL_RECONCILES

    # THE NUMBER, re-derived here from the fixture's own workbook counts and
    # the .inpx's own relFlows -- not copied from the implementation.
    #
    #   decision 2 sits on link 207 and governs that approach's three turns,
    #   E-W / E-N / E-S. Their AM observed counts are 471 + 100 + 107 = 678,
    #   so this route's observed share WITHIN its decision is
    #       p = 471/678 = 0.6946902...
    #   Its siblings (routes 2 and 3) carry relFlow 90 + 100 = 190 and Vissim
    #   does not renormalize them on write, so the weight that reproduces p is
    #       190 * p/(1-p) = 190 * 471/(678-471) = 89490/207
    #                     = 432.31884... -> 432.32,
    #   and the resulting share is 432.32/(190+432.32) = 69.5%, the observed
    #   one. Against node 2's total instead (2542 veh) the same closed form
    #   would give 190 * 471/(2542-471) = 43.21 -- a tenth of the answer, and
    #   the number this test used to pin.
    assert evidence["decision_movements"] == ["E-W", "E-N", "E-S"]
    assert evidence["observed_by_decision_movement"] == {"E-W": 471.0, "E-N": 100.0, "E-S": 107.0}
    decision_total = evidence["observed_decision_total"]
    observed_movement = evidence["observed_movement"]
    siblings = evidence["target"]["sibling_rel_flow_total"]
    assert (decision_total, observed_movement, siblings) == (678.0, 471.0, 190.0)
    assert outcome["params"]["rel_flow"] == round(
        siblings * observed_movement / (decision_total - observed_movement), 2
    ) == 432.32
    assert evidence["resulting_share_pct"] == pytest.approx(
        observed_movement / decision_total * 100, abs=0.1
    )
    # The node-total denominator's answer must NOT be what shipped.
    assert outcome["params"]["rel_flow"] != round(
        siblings * observed_movement / (evidence["observed_total"] - observed_movement), 2
    )
    # And the rationale states whose share 69.5% is, in all three languages.
    for language in ("ko", "en", "ja"):
        text = outcome["rationale"][language]
        assert "69.5%" in text, language
        assert "E-W, E-N, E-S" in text, language


def test_a_routing_target_without_its_decision_scope_is_refused(
    run_result: dict, observed: dict, targets: dict, movement_ids: dict
):
    """The same target with its scope withheld: a refusal, not a wrong number.

    Which movements a decision governs is network knowledge the run cannot
    supply. Without it the observed share could only be taken against node 2's
    whole demand (2542 veh instead of the decision's 678), which understates
    this route by an order of magnitude — so U4 refuses and says what is
    missing. This is the U5 half of the pin: the tool's own params object can
    carry the scope, and when the caller leaves it out nothing numeric is
    invented downstream.
    """
    scopeless = {
        "2": {
            "routes": {
                "E-W": {
                    key: value
                    for key, value in targets["2"]["routes"]["E-W"].items()
                    if key != "decision_movements"
                }
            }
        }
    }
    report = reality_check.check_run_against_counts(
        run_result, observed, period="AM", targets=scopeless, movement_ids=movement_ids
    )

    outcome = _node(report, "2")["outcome"]
    assert outcome["outcome"] == fixes.REFUSAL
    assert outcome["reason_code"] == fixes.EVIDENCE_INSUFFICIENT
    assert outcome["verb"] is None and outcome["params"] is None
    # The displaced demand refusal still rides along.
    assert outcome["also_refused"]["reason_code"] == fixes.NODE_TOTAL_RECONCILES
    for language in ("ko", "en", "ja"):
        assert "decision_movements" in outcome["rationale"][language], language
        assert "get_routing_decisions" in outcome["rationale"][language], language

    # COUNTERFACTUAL: no relFlow number anywhere in the payload — no proposed
    # value, no params, no arithmetic string, and not 43.21, the number the
    # node-total denominator would have produced.
    assert {"rel_flow", "proposed_rel_flow", "arithmetic"} & _keys_anywhere(outcome) == set()
    assert "43.21" not in json.dumps(outcome, ensure_ascii=False)
    assert report["proposals"] == [] or all(
        entry["node_id"] != "2" for entry in report["proposals"]
    )


def test_exit_3_reconciling_nodes_are_refused_naming_geh_and_the_threshold(pm_report: dict):
    """(3) correctly refuse: the model already reproduces observed behaviour."""
    assert pm_report["status"] == reality_check.CHECKED
    assert pm_report["proposals"] == []
    assert {entry["node_id"] for entry in pm_report["refusals"]} == {"1", "2"}

    for node_id in ("1", "2"):
        outcome = _node(pm_report, node_id)["outcome"]
        assert outcome["outcome"] == fixes.REFUSAL
        assert outcome["reason_code"] == fixes.MODEL_REPRODUCES_OBSERVED
        assert outcome["verb"] is None and outcome["params"] is None

        evidence = outcome["evidence"]
        assert evidence["node_total_geh"] < validation.GEH_GOOD_LT
        assert evidence["worst_movement_geh"] < validation.GEH_GOOD_LT
        assert evidence["geh_threshold"] == validation.GEH_GOOD_LT == 5.0

        # The rationale must NAME the numbers it relied on, in all three
        # languages -- a refusal without its evidence is just a shrug.
        for language in ("ko", "en", "ja"):
            text = outcome["rationale"][language]
            assert str(evidence["node_total_geh"]) in text
            assert str(evidence["worst_movement_geh"]) in text
            assert "5.0" in text


def test_exit_4_the_summary_discloses_the_unjoined_node(am_report: dict):
    """(4) coverage is honest: the observed-only node is NAMED, not dropped."""
    coverage = am_report["coverage"]
    assert coverage["nodes_compared"] == ["1", "2"]
    assert coverage["observed_nodes_absent_from_run"] == [
        str(make_demo_data.OBSERVED_ONLY_NODE)
    ]
    assert coverage["run_nodes_without_observed"] == []
    assert coverage["counts"]["observed_nodes"] == 3
    assert coverage["counts"]["compared"] == 2
    # Every movement on both compared nodes was gradable, because the caller
    # supplied the OBJECT_ID join for both.
    assert coverage["counts"]["movements_compared"] == len(make_demo_data.MOVEMENTS)
    assert coverage["counts"]["movements_ungradable"] == 0


def test_proposals_are_preflight_clean(am_report: dict, demo_dir: Path):
    """Both proposals would survive the approval loop's own guardrail gate.

    Checked against the REAL shadow model of the demo network (the same
    ``build_shadow_model`` the write tools use), because half of what preflight
    refuses is "that object does not exist" — a proposal that names a
    non-existent input would pass a shadow-model-free check and fail at the
    approval card.
    """
    from services.agent.tools_network import build_shadow_model

    shadow_model = build_shadow_model(demo_dir / "practice.inpx")
    assert am_report["proposals"], "the exit demonstration needs proposals to check"
    for entry in am_report["proposals"]:
        proposal = fixes.FixProposal(**_proposal_kwargs(entry["outcome"]))
        refusals = [
            finding
            for finding in fixes.preflight_findings(proposal, shadow_model)
            if finding.tier == "refuse"
        ]
        assert refusals == [], [finding.constraint_id for finding in refusals]


# ===========================================================================
# The modelled side comes from the RUN, never from the workbook
# ===========================================================================


def test_the_workbook_model_column_is_not_used_as_the_modelled_side(
    run_result: dict, observed: dict, movement_ids: dict
):
    """R2.3's whole point, pinned by making the two sources disagree.

    The workbook's ``Vehs(All)`` column is a pasted export. Here it is
    deliberately overwritten with an absurd value; the report must not move,
    because it reads the run.
    """
    poisoned = dict(observed)
    poisoned["counts"] = [
        {**row, "model_volume": 99999.0 if row["model_volume"] is not None else None}
        for row in observed["counts"]
    ]

    honest = reality_check.check_run_against_counts(
        run_result, observed, period="AM", movement_ids=movement_ids
    )
    poisoned_report = reality_check.check_run_against_counts(
        run_result, poisoned, period="AM", movement_ids=movement_ids
    )

    for node_id in ("1", "2"):
        assert _node(honest, node_id)["modelled_total"] == _node(poisoned_report, node_id)["modelled_total"]
        assert _node(honest, node_id)["node_total_geh"] == _node(poisoned_report, node_id)["node_total_geh"]

    assert honest["sources"]["modelled"].startswith("run_result['movement_volumes']")
    assert "pasted export" in honest["sources"]["modelled_not_used"]


def test_a_movement_the_run_cannot_name_is_ungradable_not_graded(
    run_result: dict, observed: dict
):
    """No OBJECT_ID join -> the splits are reported ungradable, and the node
    TOTAL is still compared in full. Nothing is graded against the workbook's
    own model column to fill the gap."""
    report = reality_check.check_run_against_counts(run_result, observed, period="AM")

    node = _node(report, "2")
    # The total still compares (it comes from the run's own movement sum).
    assert node["modelled_total"] is not None
    assert node["node_total_geh"] < validation.GEH_GOOD_LT
    # ... but with no join every movement on both sides is ungradable: on each
    # of the two nodes the 12 counted turns have no modelled number and the 12
    # simulated movements have no observed one.
    counts = report["coverage"]["counts"]
    assert counts["movements_compared"] == 0
    assert counts["movements_ungradable"] == 2 * len(make_demo_data.MOVEMENTS)
    # The named list is bounded (it tracks NETWORK size), and says so rather
    # than silently ending -- the count above is the unbounded truth.
    named = report["coverage"]["ungradable_movements"]
    assert len(named) == 21
    assert named[-1] == f"... +{counts['movements_ungradable'] - 20} more"
    assert all(item["has_observed"] != item["has_modelled"] for item in named[:20])
    # And with no gradable split, U4 refuses instead of proposing a routing fix.
    assert node["outcome"]["outcome"] == fixes.REFUSAL
    assert node["outcome"]["reason_code"] == fixes.MODEL_REPRODUCES_OBSERVED


def test_an_ambiguous_movement_alias_is_reported_not_silently_applied(
    run_result: dict, observed: dict
):
    """Two counted turns claiming the same simulated movement, and one naming
    a movement this run does not carry."""
    real_id = make_demo_data.movement_object_id(2, 207, 141)
    report = reality_check.check_run_against_counts(
        run_result,
        observed,
        period="AM",
        movement_ids={"2": {"E-W": real_id, "N-S": real_id, "S-E": "2-999-0.0-998-0.0"}},
    )
    rejected = report["coverage"]["rejected_movement_aliases"]
    assert any("already claimed by E-W" in item for item in rejected), rejected
    assert any("not in this run" in item for item in rejected), rejected
    # The one unambiguous alias still worked.
    assert report["coverage"]["counts"]["movements_compared"] == 1


# ===========================================================================
# The join reports BOTH failure directions
# ===========================================================================


def test_run_nodes_without_observed_data_are_named(observed: dict, run_result: dict):
    extra = {
        **run_result,
        "movement_volumes": {
            **run_result["movement_volumes"],
            "7-101-250.0-140-5.0": {"vehs": 500.0, "node": "7"},
        },
    }
    report = reality_check.check_run_against_counts(extra, observed, period="AM")
    coverage = report["coverage"]
    assert coverage["run_nodes_without_observed"] == ["7"]
    assert coverage["unjoined_run_object_ids"] == ["7-101-250.0-140-5.0"]
    assert coverage["observed_nodes_absent_from_run"] == ["3"]


def test_nothing_joining_at_all_is_a_refusal_that_names_both_sides(observed: dict):
    report = reality_check.check_run_against_counts(
        {"movement_volumes": {"91-1-0.0-2-0.0": {"vehs": 10.0, "node": "91"}}},
        observed,
        period="AM",
    )
    assert report["status"] == reality_check.NOT_CHECKABLE
    assert report["reason_code"] == reality_check.NO_JOINABLE_NODES
    assert report["nodes"] == []
    assert report["coverage"]["run_nodes_without_observed"] == ["91"]
    assert report["coverage"]["observed_nodes_absent_from_run"] == ["1", "2", "3"]
    for language in ("ko", "en", "ja"):
        assert "91" in report["message"][language]


# ===========================================================================
# Sad paths are first-class outcomes, never empty successes
# ===========================================================================


def test_no_counts_workbook_refuses_with_a_reason(run_result: dict, tmp_path: Path):
    missing = validation.load_base_workbook(str(tmp_path / "nope.xlsx"))
    report = reality_check.check_run_against_counts(run_result, missing)

    assert report["status"] == reality_check.NOT_CHECKABLE
    assert report["reason_code"] == reality_check.NO_OBSERVED_COUNTS
    assert report["nodes"] == [] and report["proposals"] == []
    assert set(report["message"]) == {"ko", "en", "ja"}
    assert all(report["message"].values())
    # The loader's own words survive.
    assert report["workbook_warnings"] == missing["warnings"]


def test_a_workbook_the_loader_rejects_surfaces_the_loaders_own_error(
    run_result: dict, tmp_path: Path
):
    """The quality loader's complaint is passed through, never swallowed."""
    openpyxl = pytest.importorskip("openpyxl")
    path = tmp_path / "wrong_shape.xlsx"
    wb = openpyxl.Workbook()
    sheet = wb.active
    sheet.title = "Counts"
    sheet.append(["not", "the", "right", "columns"])
    sheet.append([1, 2, 3, 4])
    wb.save(path)

    payload = validation.load_base_workbook(str(path))
    report = reality_check.check_run_against_counts(run_result, payload)

    assert report["status"] == reality_check.NOT_CHECKABLE
    assert report["reason_code"] == reality_check.OBSERVED_WORKBOOK_UNREADABLE
    assert report["workbook_warnings"] == payload["warnings"]
    assert any("Counts sheet is missing required columns" in w for w in report["workbook_warnings"])
    for language in ("ko", "en", "ja"):
        assert "Counts sheet is missing required columns" in report["message"][language]


def test_a_period_the_workbook_does_not_carry_is_a_refusal(run_result: dict, observed: dict):
    trimmed = {**observed, "counts": [r for r in observed["counts"] if r["period"] == "AM"],
               "periods": ["AM"]}
    report = reality_check.check_run_against_counts(run_result, trimmed, period="PM")
    assert report["reason_code"] == reality_check.OBSERVED_PERIOD_ABSENT
    for language in ("ko", "en", "ja"):
        assert "PM" in report["message"][language]


def test_no_run_result_at_all_refuses_and_will_not_fall_back_to_the_workbook(observed: dict):
    report = reality_check.check_run_against_counts(None, observed, period="AM")
    assert report["status"] == reality_check.NOT_CHECKABLE
    assert report["reason_code"] == reality_check.NO_RUN_RESULT
    assert report["nodes"] == []
    for language in ("ko", "en", "ja"):
        assert report["message"][language]


def _write_db(path: Path, *, movement_rows: bool, class_table: bool = True) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE SIMULATIONRUN (NO INTEGER)")
        conn.execute("INSERT INTO SIMULATIONRUN VALUES (1)")
        if class_table:
            conn.execute(
                f'CREATE TABLE "{results_evidence.MOVEMENT_INTERVAL_CLASS_TABLE}" '
                "(OBJECT_ID TEXT, ARG_EVALUATIONTIMEINTERVAL TEXT, ARG_VEHICLECLASS TEXT, "
                "VEHS REAL, VEHDELAY REAL, STOPDELAY REAL, STOPS REAL)"
            )
            if movement_rows:
                conn.execute(
                    f'INSERT INTO "{results_evidence.MOVEMENT_INTERVAL_CLASS_TABLE}" '
                    "VALUES ('1-101-250.0-140-5.0', '0-3600', 'ALL', 800.0, 20.0, 10.0, 1.0)"
                )
        conn.commit()
    finally:
        conn.close()


def test_armed_but_empty_movement_evidence_is_not_checkable_in_u3s_words(
    observed: dict, demo_dir: Path, tmp_path: Path
):
    """The table was armed and collected nothing — measured absence of DATA,
    which is not absence of a mismatch."""
    work = tmp_path / "armed-empty"
    work.mkdir()
    _write_db(work / "run_001.db", movement_rows=False)
    payload = _collect_run_evidence(
        work, demo_dir / "practice.inpx", duration_s=60, evaluation_configured=False
    )
    assert payload["evidence"][results_evidence.MOVEMENT_INTERVAL_CLASS_TABLE] == (
        results_evidence.ARMED_BUT_EMPTY
    )

    report = reality_check.check_run_against_counts(payload, observed, period="AM")
    assert report["status"] == reality_check.NOT_CHECKABLE
    assert report["reason_code"] == reality_check.RUN_EVIDENCE_ARMED_BUT_EMPTY
    assert report["run_evidence"]["state"] == results_evidence.ARMED_BUT_EMPTY
    # U3's sentence, attached verbatim rather than paraphrased.
    assert "measured absence of DATA" in report["message"]["en"]
    assert "확인 불가" in report["message"]["ko"]
    # The observed side is still disclosed: those nodes were not checked.
    assert report["coverage"]["observed_nodes_absent_from_run"] == ["1", "2", "3"]


def test_absent_movement_table_is_not_checkable_with_the_table_absent_reason(
    observed: dict, demo_dir: Path, tmp_path: Path
):
    """U1 OMITS the key when the table was never armed — the run holds no
    opinion, which is a third thing again."""
    work = tmp_path / "table-absent"
    work.mkdir()
    _write_db(work / "run_001.db", movement_rows=False, class_table=False)
    payload = _collect_run_evidence(
        work, demo_dir / "practice.inpx", duration_s=60, evaluation_configured=False
    )
    assert reality_check.MOVEMENT_VOLUMES_KEY not in payload

    report = reality_check.check_run_against_counts(payload, observed, period="AM")
    assert report["reason_code"] == reality_check.RUN_EVIDENCE_TABLE_ABSENT
    assert "never armed" in report["message"]["en"]


# ===========================================================================
# The registry tool
# ===========================================================================


def test_the_tool_is_registered_read_tier_and_applies_nothing():
    from services.agent import tools_metrics  # noqa: F401 - import registers the tool
    from services.agent.toolkit import default_registry

    spec = default_registry.get("run_reality_check")
    assert spec is not None
    assert spec.tier == "read"
    assert spec.provider == "metrics"
    assert spec.params_model.model_config.get("extra") == "forbid"
    assert "applies nothing" in spec.description


def test_the_tool_params_translate_into_u4s_target_shape():
    from services.agent.tools_metrics import RealityCheckNodeTarget, _reality_check_targets

    targets, movement_ids = _reality_check_targets(
        [
            RealityCheckNodeTarget(
                node_id="1",
                vehicle_input_no="7",
                vehicle_input_current_volume=800.0,
                vehicle_input_link_no="620",
            ),
            RealityCheckNodeTarget(
                node_id="2",
                movement="E-W",
                movement_object_id="2-207-250.0-141-5.0",
                decision_no="2",
                route_no="1",
                current_rel_flow=680.0,
                sibling_rel_flow_total=190.0,
                decision_movements=["E-W", "E-N", "E-S"],
            ),
        ]
    )
    # Unit D4: the input's LINK travels with the rest of the demand target —
    # it is the join fixes.entrance_measurement uses to tell an entrance the
    # counts were taken at from one they were not.
    #
    # Unit #54 added the provenance pair, and they are asserted here as EXPLICIT
    # None for the same reason `link_no` is below: an agent-supplied target has
    # no human mapping behind it, and "nobody named this" must stay
    # distinguishable from "the field was dropped on the way through" — `fixes`
    # renders the block only when `named_by` is truthy, so a silently absent key
    # and a None read identically downstream and this is the only place the
    # difference can be executed.
    assert targets["1"]["vehicle_input"] == {
        "input_no": "7",
        "current_volume": 800.0,
        "link_no": "620",
        "interval_no": 1,
        "named_by": None,
        "named_at": None,
    }
    # ... and a caller that omits it gets an explicit None, the same way
    # decision_movements does: "not supplied" must stay distinguishable from
    # "supplied as nothing", because D4 reads the first as UNVERIFIED.
    linkless, _ = _reality_check_targets(
        [RealityCheckNodeTarget(node_id="1", vehicle_input_no="7")]
    )
    assert linkless["1"]["vehicle_input"]["link_no"] is None
    assert targets["2"]["routes"]["E-W"]["decision_no"] == "2"
    # The decision's SCOPE survives the params -> U4 target translation; it is
    # the denominator U4's routing arithmetic refuses to proceed without.
    assert targets["2"]["routes"]["E-W"]["decision_movements"] == ["E-W", "E-N", "E-S"]
    assert movement_ids == {"2": {"E-W": "2-207-250.0-141-5.0"}}
    # ... and a caller that omits it produces an explicit None, never a
    # silently absent key that would read as "scope unknown, carry on".
    scopeless, _ = _reality_check_targets(
        [RealityCheckNodeTarget(node_id="2", movement="E-W", decision_no="2", route_no="1")]
    )
    assert scopeless["2"]["routes"]["E-W"]["decision_movements"] is None


def test_the_tool_refuses_a_run_it_cannot_resolve(monkeypatch: pytest.MonkeyPatch):
    from services.agent import tools_metrics

    monkeypatch.setattr(tools_metrics, "_resolve_result_dir", lambda scenario_id: None)
    report = tools_metrics.build_reality_check_report("S999999")
    assert report["status"] == reality_check.NOT_CHECKABLE
    assert report["reason_code"] == reality_check.RUN_NOT_FOUND
    assert report["detail"]["scenario_id"] == "S999999"


# ---------------------------------------------------------------------------
# #56 (a) — the vocabulary bridge: a run picker's own DISPLAY NAME is not the
# id every tool speaks (DECISIONS.md "화면은 실행의 name을, 도구는 id를
# 쓴다"). ONE match -> accept and disclose; zero or several -> refuse, but
# name the ids that DO exist rather than leaving the reader (or the
# assistant relaying the refusal) to guess at them.
# ---------------------------------------------------------------------------


def test_resolve_scenario_reference_takes_the_callers_string_as_an_id_first(
    monkeypatch: pytest.MonkeyPatch,
):
    """The common case, and the only one the run picker itself ever sends:
    an id that resolves needs no name lookup and reports no translation."""
    from services.agent import tools_metrics

    marker = Path("resolved/dir")
    monkeypatch.setattr(
        tools_metrics, "_resolve_result_dir", lambda scenario_id: marker if scenario_id == "SIM_x" else None
    )
    resolution = tools_metrics._resolve_scenario_reference("SIM_x")
    assert resolution == {
        "scenario_id": "SIM_x",
        "result_dir": marker,
        "translated_from": None,
        "candidates": None,
    }


def test_resolve_scenario_reference_a_display_name_that_equals_another_runs_id_never_shadows_the_direct_id(
    monkeypatch: pytest.MonkeyPatch,
):
    """DEFECT 1 (refuter): id-before-name priority, pinned directly.

    Run X's DISPLAY NAME happens to equal run Y's own id (``"SIM_y"``) — a
    coincidence nothing prevents (a name is free text; #54's own naming form
    has no format constraint on it either). Calling with the literal string
    ``"SIM_y"`` must resolve to Y through the DIRECT id path — no name search
    even runs, because the id check comes first — never to X via a name
    search that treated "SIM_y" as a name to look up. Before this test, a
    mutation that tried the name search BEFORE the direct id lookup survived
    the entire suite: it would have found X's name == "SIM_y", "translated"
    to X's real id, and returned X's directory under a claim of exactness.
    """
    from services.agent import tools_metrics

    x_dir = Path("dir/x")
    y_dir = Path("dir/y")
    monkeypatch.setattr(
        tools_metrics,
        "_resolve_result_dir",
        lambda scenario_id: {"SIM_x": x_dir, "SIM_y": y_dir}.get(scenario_id),
    )
    monkeypatch.setattr(
        tools_metrics.results_reader,
        "list_scenarios",
        lambda: [
            # X's display name IS Y's real id -- the coincidence this test exists for.
            {"id": "SIM_x", "name": "SIM_y", "color": "sky"},
            {"id": "SIM_y", "name": "agent_run_y", "color": "amber"},
        ],
    )

    resolution = tools_metrics._resolve_scenario_reference("SIM_y")

    assert resolution["scenario_id"] == "SIM_y", "the literal string must resolve as Y's OWN id"
    assert resolution["result_dir"] == y_dir
    assert resolution["translated_from"] is None, "the direct id path discloses no translation"


def test_resolve_scenario_reference_translates_a_unique_display_name(
    monkeypatch: pytest.MonkeyPatch,
):
    from services.agent import tools_metrics

    marker = Path("resolved/dir")
    monkeypatch.setattr(
        tools_metrics, "_resolve_result_dir", lambda scenario_id: marker if scenario_id == "SIM_fe342b88" else None
    )
    monkeypatch.setattr(
        tools_metrics.results_reader,
        "list_scenarios",
        lambda: [{"id": "SIM_fe342b88", "name": "agent_run_fe342b88", "color": "sky"}],
    )
    resolution = tools_metrics._resolve_scenario_reference("agent_run_fe342b88")
    assert resolution["scenario_id"] == "SIM_fe342b88"
    assert resolution["result_dir"] == marker
    assert resolution["translated_from"] == "agent_run_fe342b88"
    assert resolution["candidates"] is None


def test_resolve_scenario_reference_never_guesses_among_several_same_named_runs(
    monkeypatch: pytest.MonkeyPatch,
):
    """MUTATION GUARD: ``_resolve_result_dir`` resolves ONE of the two
    same-named candidates (``SIM_a``) — a mutation that guesses the first
    match instead of requiring uniqueness would find that directory and
    return it. Requiring both to fail proves nothing; this proves the
    refusal is deliberate, not incidental."""
    from services.agent import tools_metrics

    marker = Path("resolved/dir")
    monkeypatch.setattr(
        tools_metrics, "_resolve_result_dir", lambda scenario_id: marker if scenario_id == "SIM_a" else None
    )
    monkeypatch.setattr(
        tools_metrics.results_reader,
        "list_scenarios",
        lambda: [
            {"id": "SIM_a", "name": "dup", "color": "sky"},
            {"id": "SIM_b", "name": "dup", "color": "amber"},
        ],
    )
    resolution = tools_metrics._resolve_scenario_reference("dup")
    assert resolution["result_dir"] is None
    assert resolution["translated_from"] is None
    assert sorted(resolution["candidates"]) == ["SIM_a", "SIM_b"]


def test_resolve_scenario_reference_zero_matches_names_every_known_id(
    monkeypatch: pytest.MonkeyPatch,
):
    from services.agent import tools_metrics

    monkeypatch.setattr(tools_metrics, "_resolve_result_dir", lambda scenario_id: None)
    monkeypatch.setattr(
        tools_metrics.results_reader,
        "list_scenarios",
        lambda: [{"id": "SIM_a", "name": "run a", "color": "sky"}],
    )
    resolution = tools_metrics._resolve_scenario_reference("no such thing")
    assert resolution["result_dir"] is None
    assert resolution["translated_from"] is None
    assert resolution["candidates"] == ["SIM_a"]


def test_the_run_not_found_refusal_names_the_candidates_in_its_message(
    monkeypatch: pytest.MonkeyPatch,
):
    """DECISIONS.md's #56 (a) measurement: the refusal already carried
    ``detail.candidates`` without SAYING them. A mutation that drops the
    naming from the message (while leaving ``detail`` alone) must die here."""
    from services.agent import tools_metrics

    monkeypatch.setattr(tools_metrics, "_resolve_result_dir", lambda scenario_id: None)
    monkeypatch.setattr(
        tools_metrics.results_reader,
        "list_scenarios",
        lambda: [
            {"id": "SIM_a", "name": "run a", "color": "sky"},
            {"id": "S000002", "name": "run b", "color": "amber"},
        ],
    )
    report = tools_metrics.build_reality_check_report("agent_run_typo")
    assert report["reason_code"] == reality_check.RUN_NOT_FOUND
    assert report["detail"]["candidates"] == ["SIM_a", "S000002"]
    assert "scenario_id_translated_from" not in report["detail"]
    for language in ("ko", "en", "ja"):
        assert "SIM_a" in report["message"][language], f"[{language}] must name the valid ids"
        assert "S000002" in report["message"][language], f"[{language}] must name the valid ids"


def test_a_unique_display_name_is_accepted_and_the_translation_disclosed(
    demo_dir: Path, run_work_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """The exact user scenario DECISIONS.md measured: a display name that
    names exactly one real run is ACCEPTED, and the report says so — never a
    silent substitution."""
    from services import datasets
    from services.agent import tools_metrics

    monkeypatch.setattr(
        tools_metrics,
        "_resolve_result_dir",
        lambda scenario_id: run_work_dir if scenario_id == "SIM_real" else None,
    )
    monkeypatch.setattr(
        tools_metrics.results_reader,
        "list_scenarios",
        lambda: [{"id": "SIM_real", "name": "agent_run_real", "color": "sky"}],
    )
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: demo_dir / "practice.inpx")
    monkeypatch.setattr(
        datasets, "get_active_real_counts_excel", lambda: demo_dir / "real_counts.xlsx"
    )

    report = tools_metrics.build_reality_check_report("agent_run_real", period="AM")

    assert report["status"] == reality_check.CHECKED, report
    assert report["detail"]["scenario_id"] == "SIM_real"
    assert report["detail"]["scenario_id_translated_from"] == "agent_run_real"
    for language in ("ko", "en", "ja"):
        assert "agent_run_real" in report["message"][language], f"[{language}] must disclose what was sent"
        assert "SIM_real" in report["message"][language], f"[{language}] must disclose what it resolved to"


def test_an_ambiguous_display_name_refuses_rather_than_guessing_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
):
    """The REPORT-shape half of the ambiguous-name guarantee (RUN_NOT_FOUND,
    both candidates named, no translation disclosed). The "does the resolver
    actually refuse to guess" half is
    ``test_resolve_scenario_reference_never_guesses_among_several_same_named_runs``,
    which arms one of the two same-named candidates with a REAL resolvable
    directory so a mutation that picks it anyway has something to be caught
    picking."""
    from services.agent import tools_metrics

    monkeypatch.setattr(tools_metrics, "_resolve_result_dir", lambda scenario_id: None)
    monkeypatch.setattr(
        tools_metrics.results_reader,
        "list_scenarios",
        lambda: [
            {"id": "SIM_a", "name": "dup", "color": "sky"},
            {"id": "SIM_b", "name": "dup", "color": "amber"},
        ],
    )
    report = tools_metrics.build_reality_check_report("dup")
    assert report["reason_code"] == reality_check.RUN_NOT_FOUND
    assert "scenario_id_translated_from" not in report["detail"]
    assert sorted(report["detail"]["candidates"]) == ["SIM_a", "SIM_b"]


def test_the_tool_refuses_a_multi_seed_run_rather_than_comparing_summed_seeds(
    demo_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """The demo dataset's project scenarios carry three seeds, and U1 SUMS
    their VEHS — so comparing them against one period of counts would
    manufacture a 3x demand error. Refused, with the number."""
    from services.agent import tools_metrics

    results_dir = demo_dir / "Scenarios" / "S000001" / "S000001.results"
    assert len(results_evidence.result_db_paths(results_dir)) == 3
    monkeypatch.setattr(tools_metrics, "_resolve_result_dir", lambda scenario_id: results_dir)

    report = tools_metrics.build_reality_check_report("S000001")
    assert report["reason_code"] == reality_check.MULTI_SEED_VOLUMES_NOT_COMPARABLE
    assert report["detail"]["result_db_count"] == 3
    for language in ("ko", "en", "ja"):
        assert "3" in report["message"][language]


def test_the_tool_end_to_end_over_the_fixture(
    demo_dir: Path, run_work_dir: Path, monkeypatch: pytest.MonkeyPatch
):
    """The whole tool path, offline: resolve -> collect U1 evidence -> load the
    real workbook -> U4's mapping, with the params the planner would send."""
    from services import datasets
    from services.agent import tools_metrics
    from services.agent.toolkit import dispatch

    monkeypatch.setattr(tools_metrics, "_resolve_result_dir", lambda scenario_id: run_work_dir)
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: demo_dir / "practice.inpx")
    monkeypatch.setattr(
        datasets, "get_active_real_counts_excel", lambda: demo_dir / "real_counts.xlsx"
    )

    envelope = dispatch(
        "run_reality_check",
        {
            "scenario_id": "S000001",
            "period": "AM",
            "targets": [
                {
                    "node_id": "1",
                    "vehicle_input_no": "1",
                    "vehicle_input_current_volume": 990.0,
                }
            ],
        },
    )
    assert envelope["status"] == "ok", envelope
    report = envelope["data"]["reality_check"]
    assert report["status"] == reality_check.CHECKED
    assert report["detail"]["result_db_count"] == 1

    node_1 = _node(report, "1")["outcome"]
    assert node_1["outcome"] == fixes.PROPOSAL
    assert node_1["verb"] == "set_vehicle_input_volume"

    # Node 2's split cannot be graded without the OBJECT_ID join, so it comes
    # back as a refusal -- not as an invented routing proposal.
    node_2 = _node(report, "2")["outcome"]
    assert node_2["outcome"] == fixes.REFUSAL
    assert report["coverage"]["observed_nodes_absent_from_run"] == ["3"]
    # No filesystem path reaches the planner.
    assert "detail" in report and "path" not in str(report["detail"]).lower()

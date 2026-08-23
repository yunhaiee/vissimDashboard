"""Tests for unit U4's fix mapping (``services/agent/fixes.py``).

Roadmap row 2.1's chain is anomaly catalogue (U3) → fix mapping (this unit) →
approval loop → reality check (U5). This file pins the four things that
would make this unit a lie if they rotted:

1. **The three-outcome branch is graded on outcome (c).** A node whose total
   reconciles and whose movements all reconcile must produce a REFUSAL whose
   rationale names the GEH value and the threshold it relied on — that is the
   half of the Phase-2 exit criterion this project is scored on. The numbers
   below are chosen NEAR the GEH 5 boundary (4.87 / 5.16 / 4.07 / 6.32), not
   comfortably far from it, and every one is derived by hand in the test's
   own docstring and cross-checked against ``services.validation.geh``.

2. **A proposal's params are valid against the REAL verb schema.** Not a
   copy of the field list: ``toolkit.default_registry``'s own
   ``ToolSpec.params_model``, the exact model ``dispatch`` validates against,
   plus the real preflight constraint catalog. The discriminating case is
   tested too — a deliberately malformed payload must FAIL, or the assertion
   proves nothing.

3. **The mapping is derived, not tabulated.** Removing (or re-tiering) a
   mapped verb in a scratchpad registry copy must flip the entry to
   ``VERB_UNREGISTERED`` and make ``propose_fix`` refuse; a test that only
   asserted "this key maps to that verb name" would pass against a hardcoded
   dict and would therefore have tested nothing.

4bis. **An observed entrance's volume is never proposed as an edit** (unit
   D4, 2026-08-12). Where the counts were taken AT the entrance a vehicle
   input feeds, the demand edit is refused by name and the loop keeps grading
   so the knobs that ARE ours still get proposed; where measured-ness cannot
   be verified, the proposal survives but is labelled a demand ESTIMATE. The
   regression case is the real demo intersection's own numbers (node 8, 922
   observed vs 707 modelled, input 113 on link 617), because that exact
   configuration produced the historic 2.1 demand proposal this doctrine
   reverses — so the test asserts the old number appears NOWHERE.

4. **A routing proposal's DENOMINATOR is the decision's, or there is no
   proposal.** The observed share a relFlow is derived from must be taken
   against the movements the decision actually governs
   (``decision_movements``), never against the node total — the two differ by
   an order of magnitude on a decision that governs one approach. The scope
   is pinned three ways: it changes the emitted number (same node, two
   scopes, two answers), its absence is a refusal, and the refusal carries no
   relFlow value anywhere in its payload.

No COM, no live Vissim, no backend boot, no network reads. Every payload here
is synthetic.
"""
from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

import pytest

import services.agent.anomalies as anomalies
import services.agent.fixes as fixes
import services.results_evidence as results_evidence
from routers import assistant as assistant_endpoint
from services import validation
from services.agent import constraints, tools_edit  # noqa: F401 - registers the verbs
# Unit #45: the D4 refusal's tail makes a claim about what #43 diagnoses and
# what #44 proposes, so the test that pins it reads those modules rather than
# restating them. ``genesis_fixes`` is read for the opposite reason: it is the
# module that makes the tail's "in this loop" qualifier necessary.
from services.agent import geh_ladder, genesis_fixes, saturation_flow
from services.agent.toolkit import ToolRegistry, default_registry


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------


def _node(
    *,
    observed_total: float,
    modelled_total: float,
    movements: list[tuple[str, float, float]],
    node_id: str = "1",
) -> dict[str, Any]:
    """A node evidence payload in :func:`fixes.node_evidence_from_real_counts`'s
    shape. ``movements`` is ``[(id, observed, modelled), ...]``."""
    return {
        "node_id": node_id,
        "node_name": f"Node {node_id}",
        "observed_total": observed_total,
        "modelled_total": modelled_total,
        "movements": [
            {"id": mid, "direction": mid, "observed": observed, "modelled": modelled}
            for mid, observed, modelled in movements
        ],
        "sources": {
            "observed": "validation_workbook",
            "modelled": "run_result",
            "run_result_keys": ["movement_volumes"],
        },
    }


#: The demand target as most of this file uses it: NO ``link_no``. Unit D4
#: reads that as an UNVERIFIED entrance — the loop cannot check whether the
#: counts were taken there — so the demand proposal survives, relabelled as a
#: named demand ESTIMATE. That is the branch these fixtures exercise; the
#: MEASURED branch (which refuses) needs a link and OBJECT_ID-keyed observed
#: rows, and has its own fixtures at the end of this file.
_VEHICLE_INPUT_TARGET = {"input_no": "7", "current_volume": 800.0, "interval_no": 1}
#: The routing target, WITH the scope the observed share is taken against.
#: ``decision_movements`` names every movement decision 3 governs — here both
#: of the node's movements, i.e. a decision that genuinely governs the whole
#: node. That is expressed by listing them, not by a "node-wide" flag, and it
#: is what makes 300/1000 the right denominator in this fixture.
_ROUTE_TARGET = {
    "decision_no": "3",
    "route_no": "2",
    "current_rel_flow": 42.0,
    "sibling_rel_flow_total": 58.0,
    "decision_movements": ["m1", "m2"],
    "interval_no": 1,
}
#: The same target with its scope withheld — the caller who named a route but
#: never said what the decision governs.
_ROUTE_TARGET_NO_SCOPE = {
    key: value for key, value in _ROUTE_TARGET.items() if key != "decision_movements"
}


def _shadow_model() -> dict[str, Any]:
    """A shadow model carrying BOTH proposal targets, so the real preflight
    catalog can be run against a proposal without ``object_not_found`` firing
    for a reason that has nothing to do with the proposed value.

    Two lanes on link 620 => the rough capacity ``latent_demand_overflow``
    uses is 2 * 1900 = 3800 veh/h, well clear of the volumes proposed here.
    """
    return {
        "links": [{"id": "620", "name": "Approach", "lanes": 2, "length": 300.0}],
        "vehicle_inputs": [
            {"no": "7", "name": "VI 7", "link": "620", "volume": 800.0},
        ],
        "routing_decisions": [
            {
                "no": "3",
                "name": "RD 3",
                "link": "620",
                "routes": [
                    {"route_no": "2", "rel_flow": 42.0, "dest_link": "621"},
                    {"route_no": "5", "rel_flow": 58.0, "dest_link": "622"},
                ],
            }
        ],
    }


def _registry_copy(*, drop: str | None = None, retier: tuple[str, str] | None = None) -> ToolRegistry:
    """A scratchpad copy of the LIVE tool registry, optionally with one verb
    removed or re-tiered. Never mutates ``default_registry`` — a mutation
    test that polluted the production registry would leak into every other
    test in the suite."""
    copy = ToolRegistry()
    for spec in default_registry.specs():
        if drop is not None and spec.name == drop:
            continue
        if retier is not None and spec.name == retier[0]:
            spec = dataclasses.replace(spec, tier=retier[1])
        copy.register(spec)
    return copy


def _refuse_tier(findings: list[constraints.Finding]) -> list[constraints.Finding]:
    return [f for f in findings if f.tier == "refuse"]


# ---------------------------------------------------------------------------
# THE THREE OUTCOMES — hand-derived numbers, boundary values near GEH 5
#
# GEH = sqrt(2*(model-observed)^2/(model+observed)); <5 good (services.
# validation.GEH_GOOD_LT). Every number below is worked out in the test's own
# docstring and then re-derived through services.validation.geh so the
# arithmetic and the implementation can never quietly disagree.
# ---------------------------------------------------------------------------


def test_outcome_a_node_total_miss_yields_a_demand_side_proposal():
    """(a) The node TOTAL misses -> set_vehicle_input_volume.

    Hand arithmetic:
      observed total 1000, modelled total 1170, d = 170.
      GEH = sqrt(2 * 170^2 / (1170 + 1000))
          = sqrt(2 * 28900 / 2170) = sqrt(57800 / 2170) = sqrt(26.6359)
          = 5.1610  ->  >= 5, so the TOTAL misses. Deliberately just OVER
      the boundary: the exact GEH-5 modelled total here is 1164.487
      (2d^2 = 25(2000+d) -> d = 164.487), so 1170 clears it by 5.5 veh/h.

      Proposed volume = current * observed/modelled
                      = 800 * 1000/1170 = 800000/1170 = 683.7607 -> 683.76.

    The movements are scaled by the same 1.17 so the split is NOT the story:
    300/351 and 700/819 each carry GEH 2.83 and 4.32 (both < 5). The mapping
    must still answer at the DEMAND level, because the total is what missed.

    UNIT D4 (2026-08-12) — why this is still a proposal, and what changed.
    The entrance here is UNVERIFIED, not measured: the target names no link
    and the movement ids are workbook tokens (``m1``/``m2``) rather than
    OBJECT_IDs, so nothing in this evidence can say whether the counts were
    taken at input 7's entrance. Policy for that state is ask-the-user-first,
    and the approval card IS the ask — so the proposal survives, with its
    arithmetic unchanged, but relabelled as a named demand ESTIMATE:
    ``CONFIDENCE_DEMAND_ESTIMATE`` instead of ``CONFIDENCE_ARITHMETIC``, the
    ``ENTRANCE_NOT_VERIFIED_MEASURED`` token, the verdict in the evidence, and
    a rationale sentence telling the approver to reject it and supply the real
    count if they have one.
    """
    total_geh = validation.geh(1170.0, 1000.0)
    assert total_geh == pytest.approx(5.1610, abs=5e-4)
    assert total_geh >= validation.GEH_GOOD_LT

    node = _node(
        observed_total=1000.0,
        modelled_total=1170.0,
        movements=[("m1", 300.0, 351.0), ("m2", 700.0, 819.0)],
    )
    proposal = fixes.map_node_counts(node, targets={"vehicle_input": _VEHICLE_INPUT_TARGET})

    assert proposal.outcome == fixes.PROPOSAL
    assert proposal.scope == fixes.DEMAND_LEVEL
    assert proposal.key == fixes.NODE_TOTAL_VOLUME_MISS
    assert proposal.verb == "set_vehicle_input_volume"
    assert proposal.params == {"input_no": "7", "volume": 683.76, "interval_no": 1}
    assert proposal.evidence["arithmetic"] == "800 * 1000 / 1170 = 683.76"
    assert proposal.evidence["node_total_geh"] == 5.16
    assert proposal.evidence["geh_threshold"] == validation.GEH_GOOD_LT
    # U1's key name is echoed so the audit trail records what was relied on.
    assert proposal.evidence["run_result_keys"] == ["movement_volumes"]
    assert fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL in proposal.assumptions

    # -- unit D4: the same number, labelled as the estimate it is ------------
    assert proposal.confidence == fixes.CONFIDENCE_DEMAND_ESTIMATE
    assert fixes.ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED in proposal.assumptions
    entrance = proposal.evidence["target"]["entrance_measurement"]
    assert entrance["status"] == fixes.ENTRANCE_UNVERIFIED
    assert entrance["why"] == fixes.ENTRANCE_NO_LINK_ON_TARGET
    assert entrance["basis"]
    for language, text in proposal.rationale().items():
        assert ("수요 추정" if language == "ko" else "推定" if language == "ja"
                else "DEMAND ESTIMATE") in text, language
        assert ("거절" if language == "ko" else "拒否" if language == "ja"
                else "REJECT") in text, language


def test_a_demand_proposal_built_from_an_observation_store_node_carries_the_real_origin():
    """End-to-end companion to the two ``node_evidence_from_real_counts``
    unit tests: ``_build_demand_proposal`` folds ``node.get("sources")``
    VERBATIM into ``evidence["sources"]`` (fixes.py, arm (a)), so the origin
    string and ``count_basis`` set at the normalization step must survive all
    the way into the proposal an approver actually reads — not just into the
    intermediate node dict. Same observed/modelled/movement numbers as
    ``test_outcome_a_node_total_miss_yields_a_demand_side_proposal`` (GEH
    5.16, so the total still misses), but built through
    ``node_evidence_from_real_counts`` with ``count_basis="detector_counts"``
    instead of the hand-authored ``_node()`` fixture, so the origin actually
    comes from the code path being tested.
    """
    node = fixes.node_evidence_from_real_counts(
        {
            "node_id": "1",
            "node_name": "Test",
            "total_real": 1000.0,
            "total_sim_basam": 1170.0,
            "directions": [
                {"direction": "m1", "real": 300.0, "sim_basam": 351.0},
                {"direction": "m2", "real": 700.0, "sim_basam": 819.0},
            ],
            "count_basis": "detector_counts",
        }
    )
    proposal = fixes.map_node_counts(node, targets={"vehicle_input": _VEHICLE_INPUT_TARGET})

    assert proposal.outcome == fixes.PROPOSAL
    assert proposal.key == fixes.NODE_TOTAL_VOLUME_MISS
    assert proposal.evidence["sources"]["observed"] != "validation_workbook"
    assert "services.observations" in proposal.evidence["sources"]["observed"]
    assert proposal.evidence["sources"]["count_basis"] == "detector_counts"

    # UNIT D4: a store-sourced node is UNVERIFIED by construction — its
    # movement ids are approach-leg LABELS with no link↔leg edge anywhere in
    # this system — so the proposal survives and says so, and the verdict
    # carries the observed origin it was judged against.
    entrance = proposal.evidence["target"]["entrance_measurement"]
    assert entrance["status"] == fixes.ENTRANCE_UNVERIFIED
    assert "services.observations" in entrance["observed_source"]
    assert entrance["count_basis"] == "detector_counts"
    assert fixes.ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED in proposal.assumptions
    assert proposal.confidence == fixes.CONFIDENCE_DEMAND_ESTIMATE


def test_outcome_b_split_miss_with_reconciling_total_yields_a_routing_proposal():
    """(b) The total reconciles, one movement's SPLIT misses -> relFlow.

    Hand arithmetic:
      Node total: observed 1000, modelled 1160, d = 160.
        GEH = sqrt(2 * 160^2 / 2160) = sqrt(51200/2160) = sqrt(23.7037)
            = 4.8686  ->  < 5, the total RECONCILES. Deliberately just UNDER
        the boundary (GEH 5 needs modelled 1164.487).
      Movement m1 (the miss): observed 300, modelled 420, d = 120.
        GEH = sqrt(2 * 120^2 / 720) = sqrt(28800/720) = sqrt(40) = 6.3246
            ->  >= 5, this split MISSES.
      Movement m2: observed 700, modelled 740, d = 40.
        GEH = sqrt(2 * 40^2 / 1440) = sqrt(3200/1440) = sqrt(2.2222) = 1.4907
            ->  < 5, fine. (300+700 = 1000 and 420+740 = 1160: the movement
        sums agree with the node totals, so nothing is smuggled in.)

      relFlow is a RAW weight and siblings are NOT renormalized on write, so
      the value producing the observed share p with the siblings left alone is
      sibling_total * p/(1-p).

      p is the share WITHIN THE DECISION, and the decision's scope is the
      caller's ``decision_movements`` -- here ["m1", "m2"], i.e. decision 3
      governs both of this node's movements, so its observed total is
      300 + 700 = 1000 and p = 300/1000 = 0.3:
        sibling_total * p/(1-p) = 58 * 0.3/0.7 = 58 * 3/7 = 174/7
                                = 24.857142... -> 24.86,
      and the resulting share is 24.857/(58+24.857) = 30.0% -- the observed
      one. (Scaling the old relFlow by 420/300 instead would give 58.8 and a
      share of 50.3%, which is why the closed form is used.)

      Had decision 3 governed only m1, the same closed form over ITS total
      (p = 300/300 = 1) could not be expressed as a relFlow at all and the
      mapping would refuse -- which is the point of taking the denominator
      from the decision rather than from the node.
    """
    assert validation.geh(1160.0, 1000.0) == pytest.approx(4.8686, abs=5e-4)
    assert validation.geh(420.0, 300.0) == pytest.approx(6.3246, abs=5e-4)
    assert validation.geh(740.0, 700.0) == pytest.approx(1.4907, abs=5e-4)

    node = _node(
        observed_total=1000.0,
        modelled_total=1160.0,
        movements=[("m1", 300.0, 420.0), ("m2", 700.0, 740.0)],
    )
    proposal = fixes.map_node_counts(
        node,
        targets={"vehicle_input": _VEHICLE_INPUT_TARGET, "routes": {"m1": _ROUTE_TARGET}},
    )

    assert proposal.outcome == fixes.PROPOSAL
    assert proposal.scope == fixes.ROUTING_SPLIT_LEVEL
    assert proposal.key == fixes.MOVEMENT_SPLIT_MISS
    assert proposal.verb == "set_route_relative_flow"
    assert proposal.params == {
        "decision_no": "3",
        "route_no": "2",
        "rel_flow": 24.86,
        "interval_no": 1,
    }
    assert proposal.evidence["resulting_share_pct"] == 30.0
    assert proposal.evidence["movement_geh"] == 6.32
    assert proposal.evidence["node_total_geh"] == 4.87
    assert fixes.ASSUME_SIBLING_ROUTES_UNCHANGED in proposal.assumptions

    # The DENOMINATOR is the decision's, and it is stated: which movements
    # were summed, what they summed to, and the share that came out.
    assert proposal.evidence["decision_movements"] == ["m1", "m2"]
    assert proposal.evidence["observed_decision_total"] == 1000.0
    assert proposal.evidence["observed_by_decision_movement"] == {"m1": 300.0, "m2": 700.0}
    assert proposal.evidence["observed_share"] == 0.3
    assert proposal.evidence["observed_share_scope"] == "decision_movements"
    assert proposal.evidence["arithmetic"] == "58 * (300/1000) / (1 - 300/1000) = 24.86"
    assert fixes.ASSUME_DECISION_MOVEMENTS_COMPLETE in proposal.assumptions
    # ... and the rationale says whose share 30% is, in all three languages.
    for language, text in proposal.rationale().items():
        assert "30.0%" in text, language
        assert "m1, m2" in text, language


def test_outcome_b_must_not_overstate_into_a_demand_change():
    """The doctrine, pinned: a split miss NEVER proposes a demand change, and
    the demand-level refusal it displaced rides along rather than vanishing.

    This is the approved refusal shape recorded on roadmap row 2.1 (user
    decision 2026-08-06): "node total reconciles within GEH<5 -> routing-split
    question, NOT demand error".
    """
    node = _node(
        observed_total=1000.0,
        modelled_total=1160.0,
        movements=[("m1", 300.0, 420.0), ("m2", 700.0, 740.0)],
    )
    proposal = fixes.map_node_counts(
        node,
        # The vehicle input IS offered as a target. It must still not be used.
        targets={"vehicle_input": _VEHICLE_INPUT_TARGET, "routes": {"m1": _ROUTE_TARGET}},
    )

    assert proposal.verb != "set_vehicle_input_volume"
    displaced = proposal.also_refused
    assert displaced is not None
    assert displaced.outcome == fixes.REFUSAL
    assert displaced.reason_code == fixes.NODE_TOTAL_RECONCILES
    assert displaced.verb is None
    for language, text in displaced.rationale().items():
        assert "4.87" in text, language
        assert str(validation.GEH_GOOD_LT) in text, language


def test_outcome_c_reconciling_node_yields_a_refusal_naming_geh_and_threshold():
    """(c) THE graded refusal: the model already reproduces observed behaviour.

    Hand arithmetic (every value deliberately just UNDER the GEH 5 boundary,
    so a mapping that rounded the wrong way would fail this test):
      Node total: observed 1000, modelled 1160 -> GEH 4.8686 (see (b)) < 5.
      Movement m1: observed 300, modelled 348, d = 48.
        GEH = sqrt(2 * 48^2 / 648) = sqrt(4608/648) = sqrt(7.1111) = 2.6667.
      Movement m2: observed 700, modelled 812, d = 112.
        GEH = sqrt(2 * 112^2 / 1512) = sqrt(25088/1512) = sqrt(16.5926)
            = 4.0734  ->  < 5, but only by 0.93. (348 + 812 = 1160.)

    Worst movement GEH 4.07 < 5 AND node total GEH 4.87 < 5 => no defensible
    edit. Both numbers and the threshold must appear in all three languages.
    """
    assert validation.geh(348.0, 300.0) == pytest.approx(2.6667, abs=5e-4)
    assert validation.geh(812.0, 700.0) == pytest.approx(4.0734, abs=5e-4)

    node = _node(
        observed_total=1000.0,
        modelled_total=1160.0,
        movements=[("m1", 300.0, 348.0), ("m2", 700.0, 812.0)],
    )
    proposal = fixes.map_node_counts(
        node,
        # Both targets offered; neither may be used.
        targets={"vehicle_input": _VEHICLE_INPUT_TARGET, "routes": {"m1": _ROUTE_TARGET}},
    )

    assert proposal.outcome == fixes.REFUSAL
    assert proposal.reason_code == fixes.MODEL_REPRODUCES_OBSERVED
    assert proposal.verb is None and proposal.params is None
    assert proposal.scope == fixes.NOT_APPLICABLE
    assert proposal.evidence["node_total_geh"] == 4.87
    assert proposal.evidence["worst_movement_geh"] == 4.07
    assert proposal.evidence["geh_threshold"] == validation.GEH_GOOD_LT

    for language, text in proposal.rationale().items():
        assert "4.87" in text, f"{language} rationale must state the node-total GEH it relied on"
        assert "4.07" in text, f"{language} rationale must state the worst movement GEH"
        assert str(validation.GEH_GOOD_LT) in text, f"{language} rationale must state the threshold"


def test_the_boundary_actually_discriminates():
    """One veh/h either side of the GEH-5 node total flips (a) into (c).

    modelled 1164.49 -> GEH 5.0001 (a demand proposal);
    modelled 1164.48 -> GEH 4.9999 (a refusal). Same observed total, same
    movements, opposite answers -- the branch reads the threshold, it does
    not approximate it.
    """
    over = validation.geh(1164.49, 1000.0)
    under = validation.geh(1164.48, 1000.0)
    assert under < validation.GEH_GOOD_LT <= over

    movements = [("m1", 300.0, 349.35), ("m2", 700.0, 815.13)]
    hot = fixes.map_node_counts(
        _node(observed_total=1000.0, modelled_total=1164.49, movements=movements),
        targets={"vehicle_input": _VEHICLE_INPUT_TARGET},
    )
    cold = fixes.map_node_counts(
        _node(observed_total=1000.0, modelled_total=1164.48, movements=movements),
        targets={"vehicle_input": _VEHICLE_INPUT_TARGET},
    )
    assert hot.outcome == fixes.PROPOSAL and hot.scope == fixes.DEMAND_LEVEL
    assert cold.outcome == fixes.REFUSAL and cold.reason_code == fixes.MODEL_REPRODUCES_OBSERVED


def test_undefined_geh_is_evidence_insufficient_not_a_pass():
    """A 0-observed/0-modelled node has an UNDEFINED GEH (services.validation.
    geh returns None, unit 1.5 D1). Undefined must refuse for lack of
    evidence -- never fall through to "the model reproduces observation"."""
    node = _node(observed_total=0.0, modelled_total=0.0, movements=[])
    proposal = fixes.map_node_counts(node, targets={"vehicle_input": _VEHICLE_INPUT_TARGET})
    assert proposal.outcome == fixes.REFUSAL
    assert proposal.reason_code == fixes.EVIDENCE_INSUFFICIENT
    assert proposal.reason_code != fixes.MODEL_REPRODUCES_OBSERVED


def test_a_split_miss_with_no_named_route_refuses_rather_than_guessing():
    node = _node(
        observed_total=1000.0,
        modelled_total=1160.0,
        movements=[("m1", 300.0, 420.0), ("m2", 700.0, 740.0)],
    )
    proposal = fixes.map_node_counts(node, targets={})
    assert proposal.outcome == fixes.REFUSAL
    assert proposal.reason_code == fixes.NO_TARGET_OBJECT
    # Even when it cannot propose, it still records that a demand change was
    # considered and refused.
    assert proposal.also_refused is not None
    assert proposal.also_refused.reason_code == fixes.NODE_TOTAL_RECONCILES


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


def test_a_route_target_without_its_decision_scope_is_refused_not_scaled_to_the_node():
    """THE refusal this fix exists for: no ``decision_movements``, no proposal.

    A routing decision usually governs ONE approach, not the whole
    intersection. Without the caller saying which movements it governs, the
    only available denominator is the node total — and a share taken against
    the node total understates the route whenever the decision is narrower,
    producing a relFlow far below what the counts support. House rule: a
    refused edit with a clear reason beats a completed edit with wrong
    numbers.
    """
    node = _node(
        observed_total=1000.0,
        modelled_total=1160.0,
        movements=[("m1", 300.0, 420.0), ("m2", 700.0, 740.0)],
    )
    proposal = fixes.map_node_counts(
        node,
        targets={
            "vehicle_input": _VEHICLE_INPUT_TARGET,
            "routes": {"m1": _ROUTE_TARGET_NO_SCOPE},
        },
    )

    assert proposal.outcome == fixes.REFUSAL
    assert proposal.reason_code == fixes.EVIDENCE_INSUFFICIENT
    assert proposal.verb is None and proposal.params is None
    # The demand-level refusal still rides along -- the total still reconciles.
    assert proposal.also_refused is not None
    assert proposal.also_refused.reason_code == fixes.NODE_TOTAL_RECONCILES

    # The rationale names WHAT is missing and WHERE to get it, in ko/en/ja.
    for language, text in proposal.rationale().items():
        assert "decision_movements" in text, language
        assert "get_routing_decisions" in text, language
        assert "3" in text, language  # the decision whose scope is unknown

    # COUNTERFACTUAL: no relFlow number reaches the payload. Not a derived
    # value, not a params dict, not an arithmetic string -- and in particular
    # not 24.86, the number the node-total denominator would have produced.
    payload = proposal.to_dict()
    assert {"rel_flow", "proposed_rel_flow", "arithmetic"} & _keys_anywhere(payload) == set()
    assert "24.86" not in json.dumps(payload, ensure_ascii=False)


def test_the_decision_scope_is_the_denominator_and_it_changes_the_number():
    """The discriminating case: same node, same route, two decision scopes.

    Hand arithmetic (node total observed 1000 / modelled 1160 -> GEH 4.8686 <
    5, so the total reconciles; m1 observed 300 / modelled 420 -> GEH 6.3246
    >= 5, the split that misses; m2 400/400 -> GEH 0; m3 observed 300 /
    modelled 340 -> GEH sqrt(2*40^2/640) = sqrt(5) = 2.2361 < 5):

      * decision governs m1+m2+m3 (the whole node): p = 300/1000 = 0.3,
        relFlow = 58 * 0.3/0.7 = 24.857... -> 24.86;
      * decision governs m1+m3 only (one approach): p = 300/600 = 0.5,
        relFlow = 58 * 0.5/0.5 = 58.0.

    Same evidence, denominators 1000 and 600, answers 24.86 and 58.0. A
    mapping that quietly used the node total would return 24.86 for both, so
    this is the assertion that catches it.
    """
    node = _node(
        observed_total=1000.0,
        modelled_total=1160.0,
        movements=[("m1", 300.0, 420.0), ("m2", 400.0, 400.0), ("m3", 300.0, 340.0)],
    )
    assert validation.geh(340.0, 300.0) == pytest.approx(2.2361, abs=5e-4)

    def _relflow(*scope: str) -> fixes.FixProposal:
        return fixes.map_node_counts(
            node,
            targets={"routes": {"m1": {**_ROUTE_TARGET, "decision_movements": list(scope)}}},
        )

    node_wide = _relflow("m1", "m2", "m3")
    one_approach = _relflow("m1", "m3")

    assert node_wide.params["rel_flow"] == 24.86
    assert node_wide.evidence["observed_decision_total"] == 1000.0
    assert one_approach.params["rel_flow"] == 58.0
    assert one_approach.evidence["observed_decision_total"] == 600.0
    assert one_approach.evidence["resulting_share_pct"] == 50.0


def test_a_decision_scope_the_observed_counts_cannot_resolve_is_refused():
    """A scope naming a movement this node has no count for has an incomplete
    denominator, and an incomplete denominator inflates the route's share. The
    unresolvable token is NAMED rather than treated as zero."""
    node = _node(
        observed_total=1000.0,
        modelled_total=1160.0,
        movements=[("m1", 300.0, 420.0), ("m2", 700.0, 740.0)],
    )
    proposal = fixes.map_node_counts(
        node,
        targets={"routes": {"m1": {**_ROUTE_TARGET, "decision_movements": ["m1", "m9"]}}},
    )
    assert proposal.outcome == fixes.REFUSAL
    assert proposal.reason_code == fixes.EVIDENCE_INSUFFICIENT
    assert proposal.evidence["unresolved_decision_movements"] == ["m9"]
    for language, text in proposal.rationale().items():
        assert "m9" in text, language


def test_a_decision_scope_that_excludes_the_missing_movement_is_refused():
    """The caller named a decision that does not govern the turn being fixed —
    its share of that decision is not defined, so nothing is proposed."""
    node = _node(
        observed_total=1000.0,
        modelled_total=1160.0,
        movements=[("m1", 300.0, 420.0), ("m2", 700.0, 740.0)],
    )
    proposal = fixes.map_node_counts(
        node,
        targets={"routes": {"m1": {**_ROUTE_TARGET, "decision_movements": ["m2"]}}},
    )
    assert proposal.outcome == fixes.REFUSAL
    assert proposal.reason_code == fixes.EVIDENCE_INSUFFICIENT
    assert proposal.evidence["movement_in_decision_scope"] is False


def test_a_single_route_decision_cannot_be_fixed_with_relflow():
    """``route_relative_flow_split_confirm`` says a lone route's share is
    always 100% whatever relFlow is written. So a split miss on a decision
    whose siblings total 0 is refused, not "fixed" with a number that cannot
    move the split."""
    node = _node(
        observed_total=1000.0,
        modelled_total=1160.0,
        movements=[("m1", 300.0, 420.0), ("m2", 700.0, 740.0)],
    )
    proposal = fixes.map_node_counts(
        node,
        targets={"routes": {"m1": {**_ROUTE_TARGET, "sibling_rel_flow_total": 0.0}}},
    )
    assert proposal.outcome == fixes.REFUSAL
    assert proposal.reason_code == fixes.FIX_CANNOT_HELP


# ---------------------------------------------------------------------------
# Params validate against the REAL registry schema (and the real preflight)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verb, node, targets",
    [
        (
            "set_vehicle_input_volume",
            _node(
                observed_total=1000.0,
                modelled_total=1170.0,
                movements=[("m1", 300.0, 351.0), ("m2", 700.0, 819.0)],
            ),
            {"vehicle_input": _VEHICLE_INPUT_TARGET},
        ),
        (
            "set_route_relative_flow",
            _node(
                observed_total=1000.0,
                modelled_total=1160.0,
                movements=[("m1", 300.0, 420.0), ("m2", 700.0, 740.0)],
            ),
            {"routes": {"m1": _ROUTE_TARGET}},
        ),
    ],
)
def test_proposals_validate_against_the_live_verb_params_model(verb, node, targets):
    """The REAL model from the REAL registry -- ``ToolSpec.params_model``, the
    exact object ``toolkit.dispatch`` validates against -- not a copy of the
    field list."""
    proposal = fixes.map_node_counts(node, targets=targets)
    assert proposal.verb == verb

    spec = default_registry.get(verb)
    assert spec is not None, "the verb this unit maps must exist in the live registry"
    # Raises ValidationError if the payload is wrong in any way; every params
    # model forbids extra keys (toolkit._require_forbids_extra).
    validated = spec.params_model.model_validate(proposal.params)
    assert validated.model_dump() == proposal.params


@pytest.mark.parametrize(
    "verb, malformed",
    [
        # An invented param name -- the anti-drift case extra="forbid" exists for.
        ("set_vehicle_input_volume", {"input_number": "7", "volume": 683.76}),
        # A volume that is not a number at all.
        ("set_vehicle_input_volume", {"input_no": "7", "volume": "not-a-number"}),
        # interval_no violates the ge=1 field constraint.
        ("set_route_relative_flow", {"decision_no": "3", "route_no": "2", "rel_flow": 24.86,
                                     "interval_no": 0}),
        # A required id is simply missing.
        ("set_route_relative_flow", {"route_no": "2", "rel_flow": 24.86}),
    ],
)
def test_the_schema_check_discriminates_a_malformed_payload(verb, malformed):
    """Proof the assertion above is not vacuous: these payloads must FAIL the
    very same live schema the proposals pass."""
    ok, error = fixes.validate_params(verb, malformed)
    assert ok is False
    assert error


def test_a_builder_emitting_bad_params_is_downgraded_to_a_refusal():
    """The module polices itself: params the live schema rejects never leave
    here as a PROPOSAL, they come back as a refusal naming the schema error."""
    result = fixes._emit(
        key="synthetic",
        key_kind=fixes.KEY_VALIDATION,
        scope=fixes.DEMAND_LEVEL,
        verb="set_vehicle_input_volume",
        params={"input_no": "7", "volume": 683.76, "typo_param": 1},
        evidence={},
        ko="ko",
        en="en",
        ja="ja",
        confidence=fixes.CONFIDENCE_ARITHMETIC,
        assumptions=(),
    )
    assert result.outcome == fixes.REFUSAL
    assert result.reason_code == fixes.PARAMS_REJECTED_BY_SCHEMA
    assert result.verb is None and result.params is None


@pytest.mark.parametrize(
    "node, targets",
    [
        (
            _node(
                observed_total=1000.0,
                modelled_total=1170.0,
                movements=[("m1", 300.0, 351.0), ("m2", 700.0, 819.0)],
            ),
            {"vehicle_input": _VEHICLE_INPUT_TARGET},
        ),
        (
            _node(
                observed_total=1000.0,
                modelled_total=1160.0,
                movements=[("m1", 300.0, 420.0), ("m2", 700.0, 740.0)],
            ),
            {"routes": {"m1": _ROUTE_TARGET}},
        ),
    ],
)
def test_proposals_survive_the_real_preflight_constraint_catalog(node, targets):
    """Schema-shaped is not enough: the value itself must clear the REAL
    preflight checks (``constraints.run_checks("preflight", ...)``, the same
    call ``GuardrailEngine.preflight`` makes) against a shadow model that
    actually contains the target."""
    proposal = fixes.map_node_counts(node, targets=targets)
    findings = fixes.preflight_findings(proposal, _shadow_model())
    assert _refuse_tier(findings) == []


def test_preflight_helper_would_catch_an_out_of_range_volume():
    """Proof the preflight assertion above is not vacuous: 25000 veh/h trips
    the real ``volume_out_of_range`` refuse-tier constraint."""
    bad = fixes._emit(
        key="synthetic",
        key_kind=fixes.KEY_VALIDATION,
        scope=fixes.DEMAND_LEVEL,
        verb="set_vehicle_input_volume",
        params={"input_no": "7", "volume": 25000.0, "interval_no": 1},
        evidence={},
        ko="ko",
        en="en",
        ja="ja",
        confidence=fixes.CONFIDENCE_ARITHMETIC,
        assumptions=(),
    )
    assert bad.outcome == fixes.PROPOSAL  # schema-valid, but not check-valid
    ids = {f.constraint_id for f in _refuse_tier(fixes.preflight_findings(bad, _shadow_model()))}
    assert "volume_out_of_range" in ids


# ---------------------------------------------------------------------------
# Derived liveness — the mutation evidence
# ---------------------------------------------------------------------------


def test_every_mapped_verb_resolves_in_the_live_registry_today():
    coverage = fixes.get_fix_coverage()
    automatable = {k: v for k, v in coverage.items() if v["declared_status"] == fixes.AUTOMATABLE}
    assert automatable, "the catalogue must map at least one verb"
    for key, entry in automatable.items():
        assert entry["status"] == fixes.AUTOMATABLE, (key, entry["detail"])


@pytest.mark.parametrize("verb", ["set_vehicle_input_volume", "set_route_relative_flow"])
def test_removing_a_mapped_verb_flags_the_mapping_instead_of_passing_silently(verb):
    """MUTATION EVIDENCE. Drop the verb from a scratchpad registry copy: every
    entry that maps it must flip to VERB_UNREGISTERED. The unmutated copy is
    checked in the same test so the assertion cannot pass for the wrong
    reason (e.g. an empty registry)."""
    intact = _registry_copy()
    mutated = _registry_copy(drop=verb)

    affected = [m for m in fixes.get_fix_registry() if m.verb == verb]
    assert affected, f"no mapping references {verb}; this mutation would prove nothing"

    for mapping in affected:
        before, _ = fixes.effective_status(mapping, registry=intact)
        after, detail = fixes.effective_status(mapping, registry=mutated)
        assert before == fixes.AUTOMATABLE, mapping.key
        assert after == fixes.VERB_UNREGISTERED, mapping.key
        assert detail["verb_resolution"]["why"] == "NOT_REGISTERED"

    coverage = fixes.get_fix_coverage(registry=mutated)
    assert {
        key for key, entry in coverage.items() if entry["status"] == fixes.VERB_UNREGISTERED
    } == {m.key for m in affected}


def test_retiering_a_mapped_verb_also_flags_the_mapping():
    """A verb that survives a rename but stops being a WRITE can never be an
    edit proposal, so it must not keep counting as coverage either."""
    mutated = _registry_copy(retier=("set_vehicle_input_volume", "read"))
    mapping = fixes.get_fix(fixes.NODE_TOTAL_VOLUME_MISS)
    status, detail = fixes.effective_status(mapping, registry=mutated)
    assert status == fixes.VERB_UNREGISTERED
    assert detail["verb_resolution"]["why"] == "WRONG_TIER"


def test_a_vanished_verb_makes_propose_fix_refuse_not_emit():
    """The teeth: the derived check is not merely reported, it changes the
    answer. A proposal naming a verb that does not exist is never emitted."""
    node = _node(
        observed_total=1000.0,
        modelled_total=1170.0,
        movements=[("m1", 300.0, 351.0), ("m2", 700.0, 819.0)],
    )
    mutated = _registry_copy(drop="set_vehicle_input_volume")
    result = fixes.map_node_counts(
        node, targets={"vehicle_input": _VEHICLE_INPUT_TARGET}, registry=mutated
    )
    assert result.outcome == fixes.REFUSAL
    assert result.reason_code == fixes.VERB_NOT_IN_REGISTRY
    assert result.verb is None

    keyed = fixes.propose_fix(
        fixes.NODE_TOTAL_VOLUME_MISS,
        node=node,
        targets={"vehicle_input": _VEHICLE_INPUT_TARGET},
        registry=mutated,
    )
    assert keyed.outcome == fixes.REFUSAL
    assert keyed.reason_code == fixes.VERB_NOT_IN_REGISTRY


def test_a_no_fix_claim_predicated_on_a_stale_arming_is_flagged(monkeypatch):
    """The other direction of the same doctrine: ``starved_link``'s "nothing
    detects it yet, so there is nothing to fix from" is predicated on U3
    reporting NOT_YET_ARMED. Change what U3 reports and the claim must be
    flagged, not silently kept."""
    original = anomalies.get_anomaly("starved_link")
    assert original is not None
    before, _ = fixes.effective_status(fixes.get_fix("starved_link"))
    assert before == fixes.NOT_AUTOMATABLE

    monkeypatch.setitem(
        anomalies._REGISTRY,
        "starved_link",
        dataclasses.replace(
            original,
            arming=anomalies.ARMED,
            checks=(
                anomalies.IndexedCheck(
                    constraint_id="gridlock_reports_los_a",
                    required_run_result_keys=("los_by_movement",),
                ),
            ),
        ),
    )
    after, detail = fixes.effective_status(fixes.get_fix("starved_link"))
    assert after == fixes.ANOMALY_ARMING_DRIFTED
    assert detail["anomaly_arming"] == anomalies.ARMED
    assert detail["expected_anomaly_arming"] == anomalies.NOT_YET_ARMED

    outcome = fixes.propose_fix("starved_link")
    assert outcome.outcome == fixes.NO_AUTOMATED_FIX
    for text in outcome.rationale().values():
        assert anomalies.ARMED in text


# ---------------------------------------------------------------------------
# Alignment with U3's catalogue — nothing falls between
# ---------------------------------------------------------------------------


def test_every_anomaly_in_u3s_catalogue_has_a_fix_or_an_explicit_no_fix_entry():
    """Derived from ``anomalies.get_anomaly_registry()`` live, so a new
    anomaly added to U3 without a decision here shows up rather than being
    silently unfixable."""
    assert fixes.get_unmapped_anomalies() == ()


def test_every_armed_anomaly_is_answered_by_name():
    armed = [
        a
        for a in anomalies.get_anomaly_registry()
        if anomalies.effective_arming(a)[0] == anomalies.ARMED
    ]
    assert armed, "U3 declares at least two ARMED anomalies; this test would be vacuous otherwise"
    for anomaly in armed:
        mapping = fixes.get_fix(anomaly.id)
        assert mapping is not None, anomaly.id
        assert mapping.status in (fixes.AUTOMATABLE, fixes.NOT_AUTOMATABLE)
        if mapping.status == fixes.NOT_AUTOMATABLE:
            assert mapping.reason_code, anomaly.id


def test_not_measurable_anomaly_yields_an_explicit_no_fix_with_the_attached_reason():
    """``latent_demand_diffusion`` is U3's one true gap. The entry must SAY
    so, and must attach U1's verified reason (read out of ``tools_run``'s own
    docstring at call time) rather than restating it -- a copy could drift."""
    outcome = fixes.propose_fix("latent_demand_diffusion")

    assert outcome.outcome == fixes.NO_AUTOMATED_FIX
    assert outcome.reason_code == fixes.NOT_MEASURABLE
    assert outcome.verb is None and outcome.params is None
    assert outcome.scope == fixes.NOT_APPLICABLE

    verbatim = anomalies.unobtainable_from_result_db_reason()
    assert outcome.evidence["reason_verbatim"] == verbatim
    for language, text in outcome.rationale().items():
        assert verbatim in text, language


@pytest.mark.parametrize(
    "anomaly_id, reason_code",
    [
        ("ghost_queue", fixes.NO_REGISTERED_VERB),
        ("blocked_connector", fixes.EVIDENCE_CANNOT_DISCRIMINATE),
        ("starved_link", fixes.NO_DETECTOR_YET),
        ("latent_demand_diffusion", fixes.NOT_MEASURABLE),
    ],
)
def test_each_no_fix_entry_states_a_distinct_measured_reason(anomaly_id, reason_code):
    outcome = fixes.propose_fix(anomaly_id)
    assert outcome.outcome == fixes.NO_AUTOMATED_FIX
    assert outcome.reason_code == reason_code
    for language, text in outcome.rationale().items():
        assert len(text) > 40, f"{anomaly_id}/{language} must explain, not just label"


def test_ghost_queues_no_verb_claim_matches_the_live_registry():
    """The claim is "no shipped write verb reaches the queue definition or
    the delay-measurement segment length". Checked against the live registry
    rather than trusted: the day such a verb ships, this fails and the entry
    gets revisited."""
    write_names = {s.name for s in default_registry.specs() if s.tier == "write"}
    assert not [
        name
        for name in write_names
        if "queue_definition" in name or "delay_measurement" in name or "delay_segment" in name
    ]
    # add_queue_counter DOES ship -- the entry names it explicitly as the
    # thing that is NOT the definition, so it must still be there.
    assert "add_queue_counter" in write_names
    assert "add_queue_counter" in fixes.get_fix("ghost_queue").reason_en


# ---------------------------------------------------------------------------
# The constraint-keyed entry: reading suggested_fix, never writing it
# ---------------------------------------------------------------------------


def _latent_demand_finding(shadow: dict[str, Any], **params: Any) -> constraints.Finding:
    action = {"tool": "set_vehicle_input_volume", "params": params}
    return next(
        f
        for f in constraints.run_checks("preflight", action, shadow, {})
        if f.constraint_id == "latent_demand_overflow"
    )


def test_latent_demand_overflow_proposal_reuses_the_constraints_own_ceiling():
    """The one-way read of the ``suggested_fix`` channel: the constraint
    already computed the defensible number against the link's capacity and
    its siblings' volumes, so this mapping uses it verbatim and does not
    re-derive it. The finding here is produced by the REAL check, not
    hand-built.

    Link 620 has 1 lane => rough capacity 1900 veh/h; sibling input 207
    carries 500, so the ceiling is 1900 - 500 = 1400 veh/h.
    """
    shadow = {
        "links": [{"id": "620", "name": "L", "lanes": 1, "length": 240.0}],
        "vehicle_inputs": [
            {"no": "107", "name": "A", "link": "620", "volume": 100.0},
            {"no": "207", "name": "B", "link": "620", "volume": 500.0},
        ],
    }
    finding = _latent_demand_finding(shadow, input_no="107", volume=6000.0)
    assert finding.suggested_fix == {"max_recommended_volume": 1400.0}

    proposal = fixes.propose_for_finding(
        finding, action={"tool": "set_vehicle_input_volume", "params": {"input_no": "107",
                                                                       "volume": 6000.0}}
    )
    assert proposal.outcome == fixes.PROPOSAL
    assert proposal.scope == fixes.DEMAND_LEVEL
    assert proposal.verb == "set_vehicle_input_volume"
    assert proposal.params == {"input_no": "107", "volume": 1400.0, "interval_no": 1}
    assert proposal.confidence == fixes.CONFIDENCE_CONSTRAINT_CEILING
    # And the number it borrowed actually clears the check it came from.
    assert not [
        f
        for f in fixes.preflight_findings(proposal, shadow)
        if f.constraint_id == "latent_demand_overflow"
    ]


def test_latent_demand_overflow_refuses_when_the_constraint_says_no_volume_can_help():
    """``fixable_by_reducing_this_input: False`` means even 0 leaves the link
    over capacity. Proposing a volume there would fail the very check it came
    from, so the mapping refuses instead -- the "no defensible proposal"
    outcome on a constraint key rather than a validation one.

    Siblings 207 + 307 carry 1000 + 1000 = 2000 > capacity 1900 on their own.
    """
    shadow = {
        "links": [{"id": "620", "name": "L", "lanes": 1, "length": 240.0}],
        "vehicle_inputs": [
            {"no": "107", "name": "A", "link": "620", "volume": 100.0},
            {"no": "207", "name": "B", "link": "620", "volume": 1000.0},
            {"no": "307", "name": "C", "link": "620", "volume": 1000.0},
        ],
    }
    finding = _latent_demand_finding(shadow, input_no="107", volume=50.0)
    assert finding.suggested_fix["fixable_by_reducing_this_input"] is False

    proposal = fixes.propose_for_finding(
        finding, action={"tool": "set_vehicle_input_volume", "params": {"input_no": "107",
                                                                       "volume": 50.0}}
    )
    assert proposal.outcome == fixes.REFUSAL
    assert proposal.reason_code == fixes.FIX_CANNOT_HELP
    assert proposal.params is None


def test_an_unmapped_finding_comes_back_as_a_stated_gap_not_an_exception():
    finding = constraints.Finding(
        constraint_id="results_missing_kpi_tables",
        tier="warn",
        phase="postrun",
        messages={"ko": "k", "en": "e", "ja": "j"},
    )
    outcome = fixes.propose_for_finding(finding)
    assert outcome.outcome == fixes.NO_AUTOMATED_FIX
    assert outcome.reason_code == fixes.NO_MAPPING_REGISTERED
    assert outcome.verb is None


# ---------------------------------------------------------------------------
# Evidence helpers — U1's key names and its ARMED_BUT_EMPTY doctrine
# ---------------------------------------------------------------------------


def test_node_evidence_from_real_counts_normalizes_the_workbook_shape():
    node = fixes.node_evidence_from_real_counts(
        {
            "node_id": "1",
            "node_name": "Test",
            "total_real": 1000,
            "total_sim_basam": 1160,
            "directions": [
                {"direction": "W-E", "real": 300, "sim_basam": 420},
                {"direction": "W-N", "real": 700, "sim_basam": 740},
            ],
        }
    )
    assert node["observed_total"] == 1000.0
    assert node["modelled_total"] == 1160.0
    assert [m["id"] for m in node["movements"]] == ["W-E", "W-N"]
    assert node["sources"]["modelled"] == "validation_workbook"
    # No count_basis key on this row (the base workbook shape) -> the origin
    # stays the workbook, never manufactured as something it is not.
    assert node["sources"]["observed"] == "validation_workbook"
    assert node["sources"]["count_basis"] is None


def test_node_evidence_from_real_counts_names_the_observation_store_when_count_basis_is_present():
    """A node from ``services.observations.observed_side`` (unit U10) always
    carries ``count_basis`` — ``services.validation``'s workbook rows never do.
    Before this fix, ``sources.observed`` was hardcoded to
    ``"validation_workbook"`` regardless, so a SCATS/WA detector-loop-sourced
    proposal's audit trail named the wrong origin AND dropped count_basis, so
    an approver could not tell the number might be a site-level aggregate
    rather than a movement count.
    """
    node = fixes.node_evidence_from_real_counts(
        {
            "node_id": "1",
            "node_name": "Test",
            "total_real": 100,
            "total_sim_basam": 120,
            "directions": [],
            "count_basis": "detector_counts",
        }
    )
    assert node["sources"]["observed"] != "validation_workbook"
    assert "services.observations" in node["sources"]["observed"]
    assert node["sources"]["count_basis"] == "detector_counts"


def test_modelled_from_run_result_reads_u1s_movement_volumes_and_drops_the_node_row():
    run_result = {
        "movement_volumes": {
            "1": {"vehs": 1160.0, "node": "1"},  # Vissim's node-level aggregate
            "1-508-100.779980-510-62.109716": {"vehs": 420.0, "node": "1"},
            "1-509-88.412000-511-40.550000": {"vehs": 740.0, "node": "1"},
            "2-600-10.0-601-20.0": {"vehs": 99.0, "node": "2"},
        }
    }
    modelled = fixes.modelled_from_run_result(run_result, "1")
    assert modelled is not None
    assert modelled["total"] == 1160.0  # 420 + 740, NOT 2320
    assert set(modelled["movements"]) == {
        "1-508-100.779980-510-62.109716",
        "1-509-88.412000-511-40.550000",
    }


@pytest.mark.parametrize(
    "run_result",
    [
        {},
        {"movement_volumes": {}},
        {"movement_volumes": results_evidence.armed_but_empty_mapping("MOVEMENT_x")},
    ],
)
def test_armed_but_empty_movement_volumes_never_becomes_a_zero(run_result):
    """U1/U3's doctrine, carried into the fix layer: "collection was armed and
    returned nothing" is not "the node carried no traffic". A fix derived
    from a fabricated zero would be that failure with an edit attached."""
    assert fixes.modelled_from_run_result(run_result, "1") is None

    node = _node(observed_total=1000.0, modelled_total=1160.0, movements=[("m1", 300.0, 420.0)])
    merged = fixes.merge_modelled_from_run_result(node, run_result)
    # The workbook's own modelled numbers survive; nothing is blanked to 0.
    assert merged["modelled_total"] == 1160.0
    assert merged["movements"][0]["modelled"] == 420.0


def test_merge_modelled_from_run_result_replaces_the_modelled_side_only():
    node = _node(
        observed_total=1000.0,
        modelled_total=1160.0,
        movements=[("1-508-100.779980-510-62.109716", 300.0, 420.0)],
    )
    merged = fixes.merge_modelled_from_run_result(
        node,
        {"movement_volumes": {"1-508-100.779980-510-62.109716": {"vehs": 555.0, "node": "1"}}},
    )
    assert merged["movements"][0]["observed"] == 300.0
    assert merged["movements"][0]["modelled"] == 555.0
    assert merged["modelled_total"] == 555.0
    assert merged["sources"]["run_result_keys"] == ["movement_volumes"]


# ---------------------------------------------------------------------------
# Shape / language contracts every consumer relies on
# ---------------------------------------------------------------------------


def test_every_registered_mapping_carries_ko_en_ja():
    for mapping in fixes.get_fix_registry():
        for language, text in {**mapping.label(), **mapping.reason()}.items():
            assert text and text.strip(), (mapping.key, language)


def test_every_outcome_serializes_to_one_shape():
    """U5 and row 3.7 render one shape, never two: a refusal has the same keys
    a proposal does, with ``verb``/``params`` set to None."""
    expected = {
        "key", "key_kind", "outcome", "scope", "verb", "params", "evidence",
        "rationale", "confidence", "reason_code", "assumptions", "also_refused",
    }
    outcomes = [
        fixes.map_node_counts(
            _node(observed_total=1000.0, modelled_total=1170.0,
                  movements=[("m1", 300.0, 351.0), ("m2", 700.0, 819.0)]),
            targets={"vehicle_input": _VEHICLE_INPUT_TARGET},
        ),
        fixes.map_node_counts(
            _node(observed_total=1000.0, modelled_total=1160.0,
                  movements=[("m1", 300.0, 420.0), ("m2", 700.0, 740.0)]),
            targets={"routes": {"m1": _ROUTE_TARGET}},
        ),
        fixes.map_node_counts(
            _node(observed_total=1000.0, modelled_total=1160.0,
                  movements=[("m1", 300.0, 348.0), ("m2", 700.0, 812.0)]),
        ),
        fixes.propose_fix("latent_demand_diffusion"),
    ]
    assert {o.outcome for o in outcomes} == {
        fixes.PROPOSAL, fixes.REFUSAL, fixes.NO_AUTOMATED_FIX
    }
    for outcome in outcomes:
        payload = outcome.to_dict()
        assert set(payload) == expected, outcome.key
        assert set(payload["rationale"]) == {"ko", "en", "ja"}
        assert outcome.outcome in fixes.OUTCOMES
        assert outcome.scope in fixes.SCOPES
        if outcome.outcome != fixes.PROPOSAL:
            assert payload["verb"] is None and payload["params"] is None


def test_build_fix_report_carries_the_node_verdict_the_findings_and_the_edges():
    report = fixes.build_fix_report(
        node=_node(observed_total=1000.0, modelled_total=1160.0,
                   movements=[("m1", 300.0, 348.0), ("m2", 700.0, 812.0)]),
        findings=[
            constraints.Finding(
                constraint_id="queue_length_definition_not_portable",
                tier="warn",
                phase="postrun",
                messages={"ko": "k", "en": "e", "ja": "j"},
            )
        ],
    )
    assert report["node_outcome"]["reason_code"] == fixes.MODEL_REPRODUCES_OBSERVED
    assert report["finding_outcomes"][0]["outcome"] == fixes.NO_AUTOMATED_FIX
    assert report["unmapped_anomalies"] == []
    assert set(report["coverage"]) == {m.key for m in fixes.get_fix_registry()}
    assert report["geh_threshold"] == validation.GEH_GOOD_LT


# ---------------------------------------------------------------------------
# UNIT D4 — an observed entrance is DATA TO MATCH, not a knob
#
# The numbers below are the REAL demo intersection's, read out of
# client-data/데모-교차로/DEMO_실측데이터_2026-08-07.xlsx (node 8's five movement
# rows and its Total, node 5's total) and DEMO_캘리브레이션_보정전_GEH753.inpx
# (vehicle input 113: link 617, volume 2063.4). They are transcribed rather
# than loaded because that workbook is client data and is git-ignored — the
# regression must run on any checkout, including CI.
# ---------------------------------------------------------------------------

#: Node 8 as the demo workbook writes it: the Direction column carries the
#: run's own movement OBJECT_ID, so every row names the link it enters from.
_DEMO_NODE_8 = {
    "node_id": "8",
    "node_name": "8: DEMO 합성 실측 노드8",
    "observed_total": 922.0,
    "modelled_total": 707.0,
    "movements": [
        {"id": "8-12075-168.594386-12075-286.637577", "direction": None,
         "observed": 249.0, "modelled": 191.0},
        {"id": "8-12075-168.594386-758-13.532567", "direction": None,
         "observed": 17.0, "modelled": 13.0},
        {"id": "8-617-1229.801336-617-1286.072980", "direction": None,
         "observed": 649.0, "modelled": 498.0},
        {"id": "8-759-297.829013-12075-286.637577", "direction": None,
         "observed": 3.0, "modelled": 2.0},
        {"id": "8-759-297.829013-617-1286.072980", "direction": None,
         "observed": 4.0, "modelled": 3.0},
    ],
    "sources": {"observed": "validation_workbook", "modelled": "run_result",
                "run_result_keys": ["movement_volumes"]},
}

#: Node 5, the control: observed IS the modelled value, and link 617 does not
#: reach it at all (a forward walk of the connector graph from 617 reaches
#: nodes 3, 4 and 8 — never 5).
_DEMO_NODE_5 = {
    "node_id": "5",
    "node_name": "5: DEMO 합성 실측 노드5",
    "observed_total": 593.0,
    "modelled_total": 593.0,
    "movements": [
        {"id": "5-754-67.884582-773-5.167527", "direction": None,
         "observed": 84.0, "modelled": 84.0},
        {"id": "5-772-639.253155-774-8.441768", "direction": None,
         "observed": 170.0, "modelled": 170.0},
        {"id": "5-776-537.864515-773-5.167527", "direction": None,
         "observed": 312.0, "modelled": 312.0},
    ],
    "sources": {"observed": "validation_workbook", "modelled": "run_result",
                "run_result_keys": ["movement_volumes"]},
}

#: What ``get_vehicle_inputs`` says about input 113 in the demo network, plus
#: the link — which is the whole join unit D4's predicate rests on.
_DEMO_INPUT_113 = {
    "input_no": "113",
    "current_volume": 2063.4,
    "link_no": "617",
    "interval_no": 1,
}

#: The volume the pre-D4 mapping proposed for input 113 on this evidence:
#: 2063.4 * 922/707. The historic live demo applied exactly this
#: (runtime/calibration-loop-live-check/artifacts/before_after_geh.json).
_HISTORIC_PROPOSED_VOLUME = "2690.88"


def test_d4_regression_the_demo_intersections_measured_entrance_is_refused():
    """THE unit D4 regression: the proposal that made the historic live demo.

    Node 8's TOTAL misses badly (observed 922 vs modelled 707, GEH 7.53 — over
    the 5.0 threshold), and vehicle input 113 was named as the demand target,
    so the pre-D4 mapping emitted ``set_vehicle_input_volume`` at 2690.88
    veh/h and the live check applied it through real COM.

    That input feeds link 617 — and the workbook's own row
    ``8-617-1229.801336-617-1286.072980`` says 649 vehicles were COUNTED
    entering node 8 from link 617. So the number the edit would overwrite is
    the measurement. Rewriting it so the model's downstream totals agree is
    calibration run backwards, and it is now refused structurally: no
    threshold, no confidence knob, no override.
    """
    total_geh = validation.geh(707.0, 922.0)
    assert total_geh == pytest.approx(7.5334, abs=5e-4)
    assert total_geh >= validation.GEH_GOOD_LT, "the total must MISS or this proves nothing"

    # The predicate bites on the workbook's own shape, and names the row.
    verdict = fixes.entrance_measurement(_DEMO_NODE_8, _DEMO_INPUT_113)
    assert verdict["status"] == fixes.ENTRANCE_MEASURED
    assert verdict["why"] == fixes.ENTRANCE_ROW_ON_INPUT_LINK
    assert verdict["observed_row"]["movement_id"] == "8-617-1229.801336-617-1286.072980"
    assert verdict["observed_row"]["observed"] == 649.0
    assert verdict["link_no"] == "617"

    outcome = fixes.map_node_counts(_DEMO_NODE_8, targets={"vehicle_input": _DEMO_INPUT_113})

    # No demand edit reaches the approval card, in any shape.
    assert outcome.outcome == fixes.REFUSAL
    payload = outcome.to_dict()
    text = json.dumps(payload, ensure_ascii=False)
    assert "set_vehicle_input_volume" not in text
    assert _HISTORIC_PROPOSED_VOLUME not in text, "the historic 2.1 proposal must not survive"
    assert "proposed_volume" not in _keys_anywhere(payload)

    # The refusal is the D4 one, and it is reachable from the report: here it
    # rides on ``also_refused`` because grading fell through to the split.
    d4 = outcome if outcome.reason_code == fixes.INPUT_VOLUME_IS_OBSERVED_DATA else outcome.also_refused
    assert d4 is not None and d4.reason_code == fixes.INPUT_VOLUME_IS_OBSERVED_DATA
    assert d4.verb is None and d4.params is None
    assert d4.scope == fixes.NOT_APPLICABLE
    assert d4.evidence["target"]["entrance_measurement"]["status"] == fixes.ENTRANCE_MEASURED

    # The rationale states the doctrine POSITIVELY and lists what remains, in
    # all three languages, naming the counted row it relied on.
    #
    # RETARGETED by unit #45 (2026-08-18): this loop used to be the only pin on
    # the refusal's tail, and "relFlow" was commented "the knob that IS still
    # ours" — singular, which was true of the CODE but not of the sentence
    # beside it, which promised four levers. The tail's full capability claim
    # now has a test of its own (below); what stays here is the part this test
    # is about — the counted row, its number, and the fact that a knob IS still
    # named. relFlow is still that knob, so the assertion itself is unchanged.
    for language, message in d4.rationale().items():
        assert "8-617-1229.801336-617-1286.072980" in message, language
        assert "649" in message, language
        assert "relFlow" in message, language  # a knob that IS still ours


def test_d4_the_refusal_tail_is_a_capability_claim_that_matches_the_builders():
    """Unit #45. The tail of the D4 refusal tells the modeller what the loop
    can still do, and in this repository prose that claims more than the code
    is a defect.

    Until 2026-08-18 the tail read "what this loop CAN still change: the turn
    distribution, driving behaviour and desired speeds, signal timing, and link
    geometry/lanes" — four levers, when exactly ONE of them had a proposal
    builder. Units #43 (the diagnosis ladder) and #44 (measured saturation flow
    → MRWA Table 5-6) then changed what is true, so the sentence now separates
    three states, and each is pinned here AGAINST THE CODE THAT MAKES IT TRUE
    rather than against itself:

    (1) PROPOSED automatically — the turn distribution, and only it.
    (2) PROPOSED ONCE SUPPLIED — the car-following set, gated on a
        ``CAPACITY_LIMIT`` diagnosis AND a measured saturation flow. Short of
        the measurement #44 REQUESTS one instead of proposing, which is exactly
        what the sentence promises; both gates are exercised below, so the
        promise cannot outlive them.
    (3) HAND-EDITED — desired speeds and link geometry/lanes. Their write verbs
        are registered and COM-verified; no proposal builder emits them, which
        is asserted by reading the verbs the two builder modules can emit at
        all.

    Delete any one of the three from the sentence and this test dies. Add a
    builder for a lever the sentence calls manual and it dies too.
    """
    outcome = fixes.map_node_counts(_DEMO_NODE_8, targets={"vehicle_input": _DEMO_INPUT_113})
    d4 = outcome if outcome.reason_code == fixes.INPUT_VOLUME_IS_OBSERVED_DATA else outcome.also_refused
    assert d4.reason_code == fixes.INPUT_VOLUME_IS_OBSERVED_DATA
    text = d4.rationale()

    # -- THE SENTENCES THAT MUST NOT COME BACK -------------------------------
    # The exact over-promise, in the exact words it was written in. These are
    # the strings this unit exists to remove.
    #
    # Checked against a WHITESPACE-COLLAPSED copy, because these are the only
    # negative pins in the file and an exact-substring negative is evaded by
    # any respacing — "What this loop CAN  still change", with the two spaces a
    # re-wrapped source line produces, passed the first version of this test.
    collapsed = {language: re.sub(r"\s+", " ", body) for language, body in text.items()}
    assert "이 루프가 여전히 바꿀 수 있는 것" not in collapsed["ko"]
    assert "What this loop CAN still change" not in collapsed["en"]
    assert "依然として変更できるもの" not in collapsed["ja"]

    # -- (1) automatic: the turn distribution, stated as the single one ------
    # The COUNT and the SCOPE are pinned together, in all three languages,
    # because the count is only true inside the scope. This loop's flagship
    # automatic proposal is ``set_vehicle_input_volume`` — the demand estimate
    # this very refusal points at another entrance in its closing sentence — so
    # "proposes one thing" unqualified is false. It is "to close the same gap
    # [at this refused input]" that makes relFlow the only one. ko and ja
    # carried that clause from the start; en shipped without it.
    assert "relFlow" in text["ko"] and "하나입니다" in text["ko"]
    assert "같은 불일치를 좁히기 위해" in text["ko"]
    assert "relFlow" in text["en"] and "proposes one thing" in text["en"]
    assert "To close the same gap" in text["en"]
    assert "relFlow" in text["ja"] and "一つだけ" in text["ja"]
    assert "同じ乖離を縮めるために" in text["ja"]

    # -- (2) conditional: name the supply, and say what happens without it ---
    # The DIAGNOSIS half of the condition is pinned too. Without it a refuter
    # could invert the sentence into "even when this node is NOT diagnosed as a
    # capacity limit" and leave every other mark below standing.
    for language, marks in {
        "ko": ("포화교통류", "Table 5-6", "측정이 없으면 제안 대신 측정을 요청",
               "이 노드가 용량 한계로 진단되고"),
        "en": ("saturation flow", "Table 5-6", "requests one instead of proposing",
               "Given a CAPACITY diagnosis for this node"),
        # The ja condition mark carries its trailing "、" on purpose: without it
        # the pin is a PREFIX of "…と診断されなくても、", so the negation the
        # other two languages reject would have slipped through in Japanese.
        "ja": ("飽和交通流", "Table 5-6", "測定がなければ提案ではなく測定を要求",
               "このノードが容量限界と診断され、"),
    }.items():
        for mark in marks:
            assert mark in text[language], (language, mark)

    # ... and the MODAL is pinned, because the two conditions the sentence
    # names are NECESSARY, not SUFFICIENT (see (2'') below). "It proposes"
    # would be an over-promise of exactly the species this unit removes.
    assert "제안할 수 있으며" in text["ko"]
    assert "it can also propose" in text["en"]
    assert "提案でき" in text["ja"]

    # -- (2') and the gate the promise rests on, read from #44 itself --------
    # No CAPACITY_LIMIT diagnosis → no proposal, by name.
    without_rung = saturation_flow.propose_car_following_parameters(
        rung={"rung": geh_ladder.ROUTING_SPLIT, "evidence": {"node_id": "8"}},
        measurement=None,
    )
    assert len(without_rung) == 1
    assert without_rung[0].outcome == fixes.REFUSAL
    assert without_rung[0].reason_code == saturation_flow.RUNG_IS_NOT_CAPACITY_LIMIT

    # The diagnosis but no measurement → the measurement is REQUESTED, which is
    # the half of the sentence that would otherwise be an unkept promise.
    without_measurement = saturation_flow.propose_car_following_parameters(
        rung={"rung": geh_ladder.CAPACITY_LIMIT, "evidence": {"node_id": "8"}},
        measurement=None,
    )
    assert len(without_measurement) == 1
    assert without_measurement[0].outcome == fixes.REFUSAL
    assert without_measurement[0].reason_code == saturation_flow.NO_SATURATION_FLOW_MEASUREMENT

    # -- (2'') WHY THE SENTENCE SAYS "CAN" -----------------------------------
    # The two conditions it names are necessary, not sufficient: #44 has two
    # more hard stops behind them. Pinned so nobody "tightens" the sentence
    # back into a promise the builder does not keep.
    measured = saturation_flow.observed_measurement(
        saturation_flow_pcu=1800.0,
        method="on-site measurement",
        attribution="unit #45 regression",
        measured_at="2026-08-18",
        samples=8,
        vehicles_per_sample=6,
    )
    assert measured["ok"] is True, measured

    named_nothing = saturation_flow.propose_car_following_parameters(
        rung={"rung": geh_ladder.CAPACITY_LIMIT, "evidence": {"node_id": "8"}},
        measurement=measured,
    )
    assert named_nothing[0].outcome == fixes.REFUSAL
    assert named_nothing[0].reason_code == saturation_flow.NO_DRIVING_BEHAVIOR_TARGET

    off_the_table = saturation_flow.propose_car_following_parameters(
        rung={"rung": geh_ladder.CAPACITY_LIMIT, "evidence": {"node_id": "8"}},
        measurement=saturation_flow.observed_measurement(
            saturation_flow_pcu=2600.0,
            method="on-site measurement",
            attribution="unit #45 regression",
            measured_at="2026-08-18",
            samples=8,
            vehicles_per_sample=6,
        ),
        behavior={"no": "3", "name": "Urban", "reference_count": 0},
    )
    assert off_the_table[0].outcome == fixes.REFUSAL
    assert off_the_table[0].reason_code == saturation_flow.SATURATION_FLOW_IMPLAUSIBLE

    # -- (3) diagnosis-only: signal timing stops at the review ---------------
    assert "신호 시간" in text["ko"] and "제안은 만들지 않습니다" in text["ko"]
    assert "Signal timing" in text["en"] and "stops there" in text["en"]
    assert "信号時間" in text["ja"] and "提案は作りません" in text["ja"]

    # -- (4) manual: desired speeds and link geometry/lanes ------------------
    # "IN THIS LOOP" is pinned as hard as the rest of the sentence, because the
    # unqualified claim is FALSE and shipped that way: genesis_fixes proposes
    # two of these four verbs (see below). A refuter deleting the qualifier is
    # re-introducing the exact over-promise this unit exists to remove.
    assert "희망 속도와 링크 기하·차로" in text["ko"]
    assert "이 루프에 제안 빌더가 없어" in text["ko"]
    assert "Desired speeds and link geometry/lanes" in text["en"]
    assert "no proposal builder in this loop" in text["en"]
    assert "希望速度とリンク幾何・車線数" in text["ja"]
    assert "このループに提案ビルダーがなく" in text["ja"]

    # ... and that IS the state of the code: the verbs exist and are
    # registered, and neither of THIS LOOP's proposal modules can emit one.
    manual = {
        "set_des_speed_decision",
        "set_des_speed_distribution_points",
        "set_link_lane_count",
        "set_link_geometry",
    }
    for verb in manual:
        assert default_registry.get(verb) is not None, f"{verb} is supposed to be a real verb"

    def _emitted_verbs(module: Any) -> set[str]:
        source = Path(module.__file__).read_text(encoding="utf-8")
        return set(re.findall(r'verb\s*=\s*"([a-z0-9_]+)"', source))

    emitted: set[str] = set()
    for module in (fixes, saturation_flow):
        emitted |= _emitted_verbs(module)
    assert emitted == {
        "set_vehicle_input_volume",
        "set_route_relative_flow",
        "set_driving_behavior_parameter",
    }, emitted
    assert not (emitted & manual), "a manual lever grew a builder — the refusal must say so"

    # THE QUALIFIER IS EARNED, MEASURED RATHER THAN ASSERTED. The scan above
    # deliberately reads two modules; this reads the third, and it is the whole
    # reason the sentence cannot be written unqualified. genesis_fixes DOES
    # propose values for two of the four "manual" levers — the Genesis BUILD
    # gate widening a link to the lane count its own connector maps (V4) and
    # resetting a polyline to census points (V7). Neither is a lever for
    # closing a GEH gap, which is what the refusal is about; both make
    # "nothing proposes values for them" false as written.
    #
    # Bidirectional on purpose: if genesis_fixes ever stops emitting them, this
    # dies and somebody must decide whether the qualifier is still earned,
    # rather than leaving a hedge nobody can explain.
    genesis_emitted = _emitted_verbs(genesis_fixes)
    assert {"set_link_lane_count", "set_link_geometry"} <= genesis_emitted, genesis_emitted
    assert not (genesis_emitted & {"set_des_speed_decision",
                                   "set_des_speed_distribution_points"}), genesis_emitted


def test_d4_the_600_char_note_carries_the_measurement_it_claims_to_carry():
    """Unit #45's note at ``fixes.py``'s ``_ENTRANCE_WHY_PHRASES`` says the
    demand-ESTIMATE rationale it rides in is 385/365/647 chars, and calls that
    a MEASUREMENT.

    It first shipped 380/360/642 — off by five in every language at once, which
    no fixture difference can produce and which the word "Measured" promised
    could not happen. The lengths are the demo loop's, they are asserted
    against the live cap at ``test_calibration_loop_e2e.py``'s stage 4, and its
    module docstring is where the numbers themselves are written down. There
    were already three copies of this one measurement in the tree disagreeing
    with each other; this pins the fourth to the recorded one so it cannot
    quietly become a fifth.
    """
    note = Path(fixes.__file__).read_text(encoding="utf-8")
    claimed = re.search(
        r"runs (\d+) \(ko\) /\s*#:\s*(\d+) \(ja\) / (\d+) \(en\) chars", note
    )
    assert claimed, "the note stopped stating the three lengths — re-point this pin"

    e2e = Path(fixes.__file__).parent.parent.parent / "tests" / "test_calibration_loop_e2e.py"
    recorded = e2e.read_text(encoding="utf-8")
    ko, ja, en = (int(g) for g in claimed.groups())
    assert f"put ko at {ko} and ja at {ja}" in recorded, (ko, ja)
    assert f"**en, at {en}, still overflows**" in recorded, en

    # And the conclusion the note draws from them still follows: en is over the
    # cap, ko and ja are under it with room, and basis is small enough that
    # inlining it would push ko/ja most of the way to the cap.
    cap = assistant_endpoint.MAX_APPROVAL_REASON_CHARS
    assert en > cap >= max(ko, ja), (ko, ja, en, cap)

    # The note's OTHER measured number, the ``basis`` range, is pinned at both
    # ends on the demo fixture — it shipped as "98-160" when the floor is 86.
    # These two ``why`` values are the extremes: the fullest basis (the row was
    # found ON the input link) and the tersest (rows exist, none carries a
    # count).
    fullest = fixes.entrance_measurement(_DEMO_NODE_8, _DEMO_INPUT_113)
    assert fullest["why"] == fixes.ENTRANCE_ROW_ON_INPUT_LINK
    tersest = fixes.entrance_measurement(
        {**_DEMO_NODE_8, "movements": [
            dict(row, observed=None) if str(row["id"]).startswith("8-617-") else row
            for row in _DEMO_NODE_8["movements"]
        ]},
        _DEMO_INPUT_113,
    )
    assert tersest["why"] == fixes.ENTRANCE_ROW_HAS_NO_OBSERVED
    floor, ceiling = len(tersest["basis"]), len(fullest["basis"])
    assert f"``basis`` runs {floor} (``ROW_HAS_NO_OBSERVED``) to {ceiling}" in note, (floor, ceiling)


def test_d4_the_refusal_does_not_stop_the_grading_the_split_is_still_examined():
    """The fall-through: refusing the demand edit must not mean refusing all.

    Before D4 a node-total miss RETURNED from arm (a), so movement grading
    never ran on it. With the demand side off the table that would have made
    "the demand is not ours to change" mean "nothing is" — false, and the
    opposite of what the doctrine says. Node 8's 617 through-movement misses
    on its own (649 counted vs 498 modelled, GEH 6.31), and the report must
    show that it was graded.
    """
    assert validation.geh(498.0, 649.0) == pytest.approx(6.3054, abs=5e-4)

    outcome = fixes.map_node_counts(_DEMO_NODE_8, targets={"vehicle_input": _DEMO_INPUT_113})

    # Grading ran: the answer is keyed on the SPLIT, with the movement's own
    # GEH in evidence -- reachable only past the arm (a) return D4 removed.
    assert outcome.key == fixes.MOVEMENT_SPLIT_MISS
    assert outcome.evidence["movement_id"] == "8-617-1229.801336-617-1286.072980"
    assert outcome.evidence["movement_geh"] == 6.31
    # No routing target was named here, so the split answer is itself a
    # refusal -- naming what was missing, and still carrying the D4 refusal.
    assert outcome.reason_code == fixes.NO_TARGET_OBJECT
    assert outcome.also_refused.reason_code == fixes.INPUT_VOLUME_IS_OBSERVED_DATA


def test_d4_the_control_node_is_untouched_by_any_of_this():
    """Node 5 reconciles (593 == 593) and link 617 never reaches it. Its
    answer must be the same MODEL_REPRODUCES_OBSERVED refusal it always was —
    a doctrine that changed the verdict on a node it does not apply to would
    be a regression dressed as a fix."""
    outcome = fixes.map_node_counts(_DEMO_NODE_5, targets={"vehicle_input": _DEMO_INPUT_113})

    assert outcome.outcome == fixes.REFUSAL
    assert outcome.reason_code == fixes.MODEL_REPRODUCES_OBSERVED
    assert outcome.evidence["node_total_geh"] == 0.0
    assert outcome.also_refused is None

    # And the predicate itself says the honest thing about this node: its rows
    # exist and are link-keyed, and none of them is link 617.
    verdict = fixes.entrance_measurement(_DEMO_NODE_5, _DEMO_INPUT_113)
    assert verdict["status"] == fixes.ENTRANCE_UNVERIFIED
    assert verdict["why"] == fixes.ENTRANCE_NO_ROW_ON_INPUT_LINK
    assert "617" not in verdict["node_from_links"]


def test_d4_a_routing_proposal_carries_the_measured_entrance_refusal():
    """(e) The fall-through's product: a real non-input proposal, with the
    demand refusal riding on ``also_refused`` — the NODE_TOTAL_RECONCILES
    precedent, reused for a refusal that means something different.

    Hand arithmetic: observed total 1000 vs modelled 1170 → GEH 5.16 ≥ 5, so
    the total MISSES (which is what makes this the D4 branch and not the
    classic arm (b)). The link-620 entrance carries a counted row, so the
    demand edit refuses. Movement A (300 counted vs 420 modelled) has GEH
    6.32 ≥ 5, decision 3 governs both of the approach's movements
    (300 + 700 = 1000), so p = 0.3 and relFlow = 58 * 0.3/0.7 = 24.86.
    """
    node = {
        "node_id": "9",
        "observed_total": 1000.0,
        "modelled_total": 1170.0,
        "movements": [
            {"id": "9-620-10.0-621-20.0", "direction": None, "observed": 300.0, "modelled": 420.0},
            {"id": "9-620-10.0-622-30.0", "direction": None, "observed": 700.0, "modelled": 750.0},
        ],
        "sources": {"observed": "validation_workbook", "modelled": "run_result"},
    }
    assert validation.geh(1170.0, 1000.0) >= validation.GEH_GOOD_LT
    assert validation.geh(420.0, 300.0) >= validation.GEH_GOOD_LT

    outcome = fixes.map_node_counts(
        node,
        targets={
            "vehicle_input": {"input_no": "5", "current_volume": 800.0, "link_no": "620",
                              "interval_no": 1},
            "routes": {
                "9-620-10.0-621-20.0": {
                    "decision_no": "3",
                    "route_no": "2",
                    "current_rel_flow": 42.0,
                    "sibling_rel_flow_total": 58.0,
                    "decision_movements": ["9-620-10.0-621-20.0", "9-620-10.0-622-30.0"],
                    "interval_no": 1,
                }
            },
        },
    )

    assert outcome.outcome == fixes.PROPOSAL
    assert outcome.verb == "set_route_relative_flow"
    assert outcome.params["rel_flow"] == 24.86
    assert outcome.also_refused.reason_code == fixes.INPUT_VOLUME_IS_OBSERVED_DATA

    # THE SENTENCE THAT MUST NOT BE WRITTEN: the pre-D4 routing rationale
    # opened with "the node total reconciles", which is FALSE here (GEH 5.16).
    # A proposal whose first clause is a lie is worse than no proposal.
    for language, message in outcome.rationale().items():
        assert "5.16" in message, language
        assert "4.87" not in message, language
    assert "reconciles with observation" not in outcome.rationale_en
    assert "실측과 합치하지만" not in outcome.rationale_ko
    assert "実測と一致していますが" not in outcome.rationale_ja


def test_d4_an_unverified_entrance_is_labelled_an_estimate_not_silently_edited():
    """(c) The store path's shape: leg-keyed movement labels (U10's
    ``"<approach leg>|<turn>"``) that no link can be resolved from. The
    proposal survives — the approval card IS the ask-the-user step — but it
    says what it is, in the token, the confidence, the evidence and the
    prose."""
    node = {
        "node_id": "1",
        "observed_total": 1000.0,
        "modelled_total": 1170.0,
        "movements": [
            {"id": "북측방면|left", "direction": "북측방면|left", "observed": 300.0,
             "modelled": 351.0},
            {"id": "북측방면|through", "direction": "북측방면|through", "observed": 700.0,
             "modelled": 819.0},
        ],
        "sources": {"observed": "services.observations (count_basis=turning_counts)",
                    "count_basis": "turning_counts"},
    }
    outcome = fixes.map_node_counts(
        node,
        targets={"vehicle_input": {"input_no": "7", "current_volume": 800.0, "link_no": "620",
                                   "interval_no": 1}},
    )

    assert outcome.outcome == fixes.PROPOSAL
    assert outcome.verb == "set_vehicle_input_volume"
    assert outcome.confidence == fixes.CONFIDENCE_DEMAND_ESTIMATE
    assert fixes.ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED in outcome.assumptions

    verdict = outcome.evidence["target"]["entrance_measurement"]
    assert verdict["status"] == fixes.ENTRANCE_UNVERIFIED
    # The link WAS supplied — this is the store's own named absence, not a
    # caller mistake, and the two must not be conflated.
    assert verdict["why"] == fixes.ENTRANCE_NO_LINK_KEYED_ROWS
    assert verdict["link_no"] == "620"
    assert "OBJECT_ID" in verdict["basis"]

    for language, message in outcome.rationale().items():
        assert ("수요 추정" if language == "ko" else "需要推定" if language == "ja"
                else "DEMAND ESTIMATE") in message, language


@pytest.mark.parametrize(
    "case, node, target, status, why",
    [
        (
            "workbook OBJECT_ID row on the input's own link",
            _DEMO_NODE_8,
            _DEMO_INPUT_113,
            fixes.ENTRANCE_MEASURED,
            fixes.ENTRANCE_ROW_ON_INPUT_LINK,
        ),
        (
            "U10 store: leg-keyed labels, no link<->leg edge exists anywhere",
            {"node_id": "1", "movements": [{"id": "북측방면|left", "observed": 300.0}],
             "sources": {"count_basis": "turning_counts"}},
            {"input_no": "7", "link_no": "620"},
            fixes.ENTRANCE_UNVERIFIED,
            fixes.ENTRANCE_NO_LINK_KEYED_ROWS,
        ),
        (
            "detector basis: directions [] by construction, so no rows at all",
            {"node_id": "1", "movements": [], "sources": {"count_basis": "detector_counts"}},
            {"input_no": "7", "link_no": "620"},
            fixes.ENTRANCE_UNVERIFIED,
            fixes.ENTRANCE_NO_MOVEMENT_ROWS,
        ),
        (
            "link-keyed rows, but none of them on THIS input's link",
            _DEMO_NODE_8,
            {"input_no": "999", "link_no": "12345"},
            fixes.ENTRANCE_UNVERIFIED,
            fixes.ENTRANCE_NO_ROW_ON_INPUT_LINK,
        ),
        (
            "the target named no link, so there is nothing to match against",
            _DEMO_NODE_8,
            {"input_no": "113"},
            fixes.ENTRANCE_UNVERIFIED,
            fixes.ENTRANCE_NO_LINK_ON_TARGET,
        ),
        (
            "a row on the link exists but carries no observed count",
            {"node_id": "8", "movements": [{"id": "8-617-1.0-617-2.0", "observed": None}]},
            {"input_no": "113", "link_no": "617"},
            fixes.ENTRANCE_UNVERIFIED,
            fixes.ENTRANCE_ROW_HAS_NO_OBSERVED,
        ),
    ],
)
def test_entrance_measurement_states_its_verdict_and_why(case, node, target, status, why):
    """The predicate's own unit test. Every UNVERIFIED case is a DIFFERENT
    named absence: a function that collapsed them into one "unknown" would be
    unable to tell "we have no link↔leg edge yet" (a scheduled unit) from
    "this caller forgot a field" (a caller bug) from "there really is no row
    here" (the data's own shape)."""
    verdict = fixes.entrance_measurement(node, target)
    assert verdict["status"] == status, case
    assert verdict["why"] == why, case
    assert verdict["basis"], case
    assert verdict["status"] in fixes.ENTRANCE_STATUSES
    # "unverified" is never spelled as "unmeasured" anywhere in the payload:
    # the whole point is that this function does not make that second claim.
    assert "unmeasured" not in json.dumps(verdict, ensure_ascii=False)


@pytest.mark.parametrize(
    "supplied_link",
    ["617", "617.0", " 617 ", "00617", "0617"],
)
def test_a_link_id_spelled_differently_still_finds_the_measurement(supplied_link):
    """THE FALSE-NEGATIVE THAT WOULD UNDO THIS UNIT.

    The join is a string comparison between two numbers that arrive by
    different roads, and COM hands numeric attributes back as floats often
    enough that ``vissim_com`` already casts the sibling ``No`` field through
    ``int()`` for exactly this reason. If ``"617.0"`` read as a different link
    from ``"617"``, the verdict would fall to UNVERIFIED — and UNVERIFIED is
    the branch that ALLOWS the demand edit as a labelled estimate. So a
    spelling mismatch would not lose a refusal, it would silently license
    overwriting a measurement: the exact inversion D4 exists to refuse.
    """
    verdict = fixes.entrance_measurement(
        _DEMO_NODE_8, {**_DEMO_INPUT_113, "link_no": supplied_link}
    )
    assert verdict["status"] == fixes.ENTRANCE_MEASURED, supplied_link
    assert verdict["observed_on_input_link"] == 649.0
    # The join used the canonical spelling; what the caller actually handed
    # over is kept beside it rather than silently rewritten.
    assert verdict["link_no"] == "617"
    assert verdict["link_no_supplied"] == supplied_link.strip()

    outcome = fixes.map_node_counts(
        _DEMO_NODE_8,
        targets={"vehicle_input": {**_DEMO_INPUT_113, "link_no": supplied_link}},
    )
    d4 = outcome if outcome.reason_code == fixes.INPUT_VOLUME_IS_OBSERVED_DATA else outcome.also_refused
    assert d4 is not None and d4.reason_code == fixes.INPUT_VOLUME_IS_OBSERVED_DATA


@pytest.mark.parametrize(
    "value, expected",
    [
        ("617", "617"),
        ("617.0", "617"),
        ("00617", "617"),
        (617, "617"),
        (617.0, "617"),
        ("  617  ", "617"),
        # NOT truncated: inventing a match is worse than missing one, because
        # a false MEASURED verdict refuses an edit that was legitimate.
        ("617.5", "617.5"),
        # A genuinely non-numeric id still compares as itself.
        ("Murdoch Dr_NB", "Murdoch Dr_NB"),
        ("", ""),
        (None, ""),
    ],
)
def test_canonical_link_id_normalizes_only_what_it_was_written_for(value, expected):
    assert fixes.canonical_link_id(value) == expected


def test_the_cited_count_is_every_row_on_the_link_not_the_first():
    """An approach fans out into several movements, and citing one of them
    understates the measurement the refusal is defending. The demo node's link
    12075 carries two counted rows — 249 + 17 — so the number quoted must be
    266, with the row count stated and every row id kept in evidence.

    (Link 617, the actual D4 regression target, happens to carry ONE row, which
    is why the single-row path alone would have looked correct forever.)
    """
    verdict = fixes.entrance_measurement(_DEMO_NODE_8, {"input_no": "X", "link_no": "12075"})

    assert verdict["status"] == fixes.ENTRANCE_MEASURED
    assert verdict["observed_on_input_link"] == 266.0
    assert len(verdict["observed_rows"]) == 2
    assert verdict["rows_on_input_link"] == [
        "8-12075-168.594386-12075-286.637577",
        "8-12075-168.594386-758-13.532567",
    ]
    assert "266" in verdict["basis"] and "2 observed movement rows" in verdict["basis"]

    # ... and the refusal QUOTES the sum, in all three languages, saying that
    # it is a sum rather than presenting it as one row's count.
    outcome = fixes.map_node_counts(
        _DEMO_NODE_8,
        targets={"vehicle_input": {"input_no": "X", "current_volume": 500.0, "link_no": "12075"}},
    )
    d4 = outcome if outcome.reason_code == fixes.INPUT_VOLUME_IS_OBSERVED_DATA else outcome.also_refused
    assert d4.reason_code == fixes.INPUT_VOLUME_IS_OBSERVED_DATA
    for language, message in d4.rationale().items():
        assert "266" in message, language
        assert "2" in message, language  # the row count is stated, not implied
        assert "249" not in message, f"{language} must not quote one row as the entrance total"


@pytest.mark.parametrize(
    "movement_id, expected",
    [
        ("8-617-1229.801336-617-1286.072980", "617"),
        ("1-508-100.779980-510-62.109716", "508"),
        # Positions can be negative, which is exactly why splitting on "-" is
        # not safe and the leading integer runs are read instead.
        ("2-207-250.0-141--5.0", "207"),
        ("E-W", None),          # a workbook turn token
        ("북측방면|left", None),   # a U10 store leg label
        ("8", None),            # Vissim's node-level aggregate row
        ("", None),
        (None, None),
    ],
)
def test_movement_from_link_reads_object_ids_and_refuses_labels(movement_id, expected):
    assert fixes.movement_from_link(movement_id) == expected


@pytest.mark.parametrize(
    "movement_id, expected",
    [
        ("8-617-1229.801336-617-1286.072980", "617"),
        ("1-508-100.779980-510-62.109716", "510"),
        ("8-12075-168.594386-758-13.532567", "758"),
        # Integral positions, and a negative one — the same two shapes the
        # from-link reader is parametrized over, because the exit link is read
        # out of the SAME five fields.
        ("2-207-250-141-5", "141"),
        ("2-207-250.0-141--5.0", "141"),
        # A token that merely STARTS like an OBJECT_ID. The exit link is the
        # fourth field, so an unanchored read would answer "141" here — a link
        # number pulled out of a string it never finished checking. The
        # from-link reader may match a prefix (its answer is the second field
        # and is complete by then); this one may not.
        ("2-207-250.0-141-5.0-and-then-some", None),
        ("2-207-250.0-141", None),  # truncated: no exit position at all
        ("E-W", None),
        ("북측방면|left", None),
        ("8", None),
        ("", None),
        (None, None),
    ],
)
def test_movement_to_link_reads_the_whole_object_id_or_nothing(movement_id, expected):
    assert fixes.movement_to_link(movement_id) == expected


def test_d4_latent_demand_overflow_labels_its_ceiling_an_estimate_too():
    """The mapping's OTHER ``set_vehicle_input_volume`` proposal is under the
    same doctrine — and this builder's evidence can never contain an observed
    row (a preflight finding has no counts and no run behind it), so its
    verdict is ALWAYS unverified. It says so instead of implying the check ran
    and passed."""
    shadow = {
        "links": [{"id": "620", "name": "L", "lanes": 1, "length": 240.0}],
        "vehicle_inputs": [
            {"no": "107", "name": "A", "link": "620", "volume": 100.0},
            {"no": "207", "name": "B", "link": "620", "volume": 500.0},
        ],
    }
    finding = _latent_demand_finding(shadow, input_no="107", volume=6000.0)
    proposal = fixes.propose_for_finding(
        finding,
        action={"tool": "set_vehicle_input_volume", "params": {"input_no": "107",
                                                              "volume": 6000.0}},
    )

    assert proposal.outcome == fixes.PROPOSAL
    assert proposal.params["volume"] == 1400.0  # still the constraint's own ceiling
    verdict = proposal.evidence["entrance_measurement"]
    assert verdict["status"] == fixes.ENTRANCE_UNVERIFIED
    assert verdict["why"] == fixes.ENTRANCE_NO_MOVEMENT_ROWS
    assert verdict["link_no"] == "620"  # the constraint DID resolve the link
    assert fixes.ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED in proposal.assumptions
    for language, message in proposal.rationale().items():
        assert ("수요 추정" if language == "ko" else "需要推定" if language == "ja"
                else "DEMAND ESTIMATE") in message, language


# ---------------------------------------------------------------------------
# Registration guards
# ---------------------------------------------------------------------------


def test_register_fix_rejects_an_automatable_entry_with_no_verb():
    with pytest.raises(ValueError, match="names no verb"):
        fixes.register_fix(
            fixes.FixMapping(
                key="synthetic_no_verb",
                key_kind=fixes.KEY_CONSTRAINT,
                status=fixes.AUTOMATABLE,
                scope=fixes.DEMAND_LEVEL,
                label_ko="k", label_en="e", label_ja="j",
                reason_ko="k", reason_en="e", reason_ja="j",
            )
        )
    assert fixes.get_fix("synthetic_no_verb") is None


def test_register_fix_rejects_a_derived_status_being_declared():
    with pytest.raises(ValueError, match="declarable values"):
        fixes.register_fix(
            fixes.FixMapping(
                key="synthetic_derived",
                key_kind=fixes.KEY_CONSTRAINT,
                status=fixes.VERB_UNREGISTERED,
                scope=fixes.NOT_APPLICABLE,
                label_ko="k", label_en="e", label_ja="j",
                reason_ko="k", reason_en="e", reason_ja="j",
            )
        )


def test_register_fix_rejects_a_no_fix_entry_with_no_reason_code():
    with pytest.raises(ValueError, match="reason_code"):
        fixes.register_fix(
            fixes.FixMapping(
                key="synthetic_no_reason",
                key_kind=fixes.KEY_ANOMALY,
                status=fixes.NOT_AUTOMATABLE,
                scope=fixes.NOT_APPLICABLE,
                label_ko="k", label_en="e", label_ja="j",
                reason_ko="k", reason_en="e", reason_ja="j",
            )
        )

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
from typing import Any

import pytest

import services.agent.anomalies as anomalies
import services.agent.fixes as fixes
import services.results_evidence as results_evidence
from services import validation
from services.agent import constraints, tools_edit  # noqa: F401 - registers the verbs
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
    assert proposal.confidence == fixes.CONFIDENCE_ARITHMETIC
    assert fixes.ASSUME_INPUT_DRIVES_NODE_TOTAL in proposal.assumptions


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

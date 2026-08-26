"""Tests for the genesis fix-mapping registry (roadmap B5-2 stage 1,
``services/agent/genesis_fixes.py``).

Offline, pure Python: no COM, no server, no ``runtime/`` dependency (CLAUDE.md
— everything must work against synthetic data). The two REAL gate-finding
fixtures below are copied, minimized, out of two actual refused
``gate.json`` artifacts this module's author read at
``runtime/workspaces/7486428cfede/genesis/jobs/d31961abd950/gate.json`` and
``runtime/workspaces/bfc69251bd04/genesis/jobs/f57cf27e33f5/gate.json`` — the
values are committed here so the test never touches ``runtime/`` at run time.

What this file pins, in order:

(a) The real ``one_way_dead_end_unfixable`` V3 findings resolve
    NOT_AUTOMATABLE and their rationale carries the cause verbatim.
(b) ``v4_lane_drop_without_runway`` splits on evidence: boundary-traced ->
    ``RUNWAY_ENDS_AT_REQUESTED_BOUNDARY``, non-boundary ->
    ``NO_TOOL_WITHOUT_FABRICATION``.
(c) A MEASURED-entrance V6 fixture produces the structural refusal, and NO
    proposal object with a volume param exists anywhere in the output —
    absence is asserted, not just the presence of a refusal outcome.
(d) An UNVERIFIED V6 fixture produces a proposal carrying the demand-estimate
    confidence and assumption token.
(e) The mirror check: zero unmapped codes, AND the registry's own
    ``GATE_FINDING_CODES`` is cross-checked against a SOURCE-DERIVED
    extraction that walks ``gate.py``'s own AST — so a new finding code added
    to a check function fails this test rather than silently falling through
    the mirror. The same technique the auth-route registry test pins routes
    with.
(f) Every AUTOMATABLE entry's params validate against its verb's REAL
    pydantic model, and a deliberately broken param payload fails.
(g) Registry liveness: dropping a mapped verb from a scratchpad copy of the
    tool registry flips the entry's DERIVED status to VERB_UNREGISTERED —
    never a declared, hand-asserted fact.
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import json

import pytest

import services.agent.fixes as fixes
import services.agent.genesis_fixes as gf
import services.genesis.gate as gate
from services.agent import tools_edit  # noqa: F401 - registers the write verbs
from services.agent.toolkit import ToolRegistry, default_registry


def _registry_copy(*, drop: str | None = None, retier: tuple[str, str] | None = None) -> ToolRegistry:
    """A scratchpad copy of the LIVE tool registry, optionally with one verb
    removed or re-tiered. Never mutates ``default_registry`` (the same helper
    ``test_agent_fixes.py`` uses, for the same reason: a mutation test that
    polluted the production registry would leak into the rest of the suite).
    """
    copy = ToolRegistry()
    for spec in default_registry.specs():
        if drop is not None and spec.name == drop:
            continue
        if retier is not None and spec.name == retier[0]:
            spec = dataclasses.replace(spec, tier=retier[1])
        copy.register(spec)
    return copy


# ---------------------------------------------------------------------------
# (e) source-derived mirror: re-derive gate.py's own GateFinding codes from
# its AST, independent of GATE_FINDING_CODES, and compare.
# ---------------------------------------------------------------------------


def _gate_finding_codes_from_source() -> frozenset[str]:
    """Every ``code`` a ``GateFinding(...)`` call in ``gate.py`` can carry.

    Handles both shapes the source actually uses: a literal
    (``GateFinding(code="v1_load_refused", ...)``) and a variable assigned a
    string constant earlier in the SAME function and then passed as
    ``code=code`` (``gate._check_v3``'s zone classification). A function-
    scoped reaching-definition scan is enough here: every occurrence in the
    source is one or the other, and this test's own assertion (that the
    result equals ``GATE_FINDING_CODES``) is exactly what would catch a third
    shape being introduced without this extractor learning it.
    """
    source = inspect.getsource(gate)
    tree = ast.parse(source)
    codes: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        assigned: dict[str, set[str]] = {}
        calls: list[ast.Call] = []
        for child in ast.walk(node):
            if isinstance(child, ast.Assign) and isinstance(child.value, ast.Constant) \
                    and isinstance(child.value.value, str):
                for target in child.targets:
                    if isinstance(target, ast.Name):
                        assigned.setdefault(target.id, set()).add(child.value.value)
            if isinstance(child, ast.Call) and isinstance(child.func, ast.Name) \
                    and child.func.id == "GateFinding":
                calls.append(child)
        for call in calls:
            for kw in call.keywords:
                if kw.arg != "code":
                    continue
                if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    codes.add(kw.value.value)
                elif isinstance(kw.value, ast.Name) and kw.value.id in assigned:
                    codes.update(assigned[kw.value.id])
                else:  # pragma: no cover - would mean a THIRD code shape appeared
                    raise AssertionError(
                        f"gate.py: a GateFinding(code=...) call's code argument is neither a "
                        f"string literal nor a locally-assigned variable ({ast.dump(kw.value)}) "
                        "-- this extractor needs to learn the new shape"
                    )
    return frozenset(codes)


def test_gate_finding_codes_are_source_derived_not_stale():
    """gate.py's own AST is the ground truth; GATE_FINDING_CODES must equal
    it exactly, both ways -- neither missing a real code nor claiming one
    that does not exist."""
    from_source = _gate_finding_codes_from_source()
    assert from_source == gf.GATE_FINDING_CODES, (
        f"missing from GATE_FINDING_CODES: {sorted(from_source - gf.GATE_FINDING_CODES)}; "
        f"stale in GATE_FINDING_CODES: {sorted(gf.GATE_FINDING_CODES - from_source)}"
    )


def test_mirror_check_zero_unmapped_finding_codes():
    """Every code the gate can emit has an entry in this catalogue -- either
    a fix or an explicit no-fix. Nothing falls between the two."""
    assert gf.get_unmapped_finding_codes() == ()


def test_every_registered_code_is_a_real_gate_code():
    """The reverse direction: nothing is registered under a code gate.py does
    not actually emit."""
    registered = {m.code for m in gf.get_genesis_fix_registry()}
    assert registered == gf.GATE_FINDING_CODES


# ---------------------------------------------------------------------------
# (a) real one_way_dead_end_unfixable V3 fixtures -> NOT_AUTOMATABLE, cause
# quoted verbatim. Minimized from two REAL refused gate.json artifacts.
# ---------------------------------------------------------------------------

#: runtime/workspaces/7486428cfede/genesis/jobs/d31961abd950/gate.json, V3
#: finding #1 (of 2), verbatim.
_REAL_V3_FINDING_JOB_D3196 = {
    "code": "v3_silent_departure_point",
    "subject": "도안동로 [FWD] w45473814s1",
    "context": {"cause": "one_way_dead_end_unfixable", "link_id": 2},
}

#: runtime/workspaces/bfc69251bd04/genesis/jobs/f57cf27e33f5/gate.json, V3
#: finding #1 (of 10), verbatim.
_REAL_V3_FINDING_JOB_F57CF = {
    "code": "v3_silent_departure_point",
    "subject": "primary_link [FWD] w1293843658s1",
    "context": {"cause": "one_way_dead_end_unfixable", "link_id": 1602},
}


@pytest.mark.parametrize(
    "real_finding", [_REAL_V3_FINDING_JOB_D3196, _REAL_V3_FINDING_JOB_F57CF]
)
def test_real_one_way_dead_end_findings_resolve_not_automatable_with_cause(real_finding):
    proposal = gf.propose_for_gate_finding(real_finding)
    assert proposal.outcome == fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == gf.CONNECTIVITY_IS_NOT_INVENTED
    assert proposal.verb is None
    assert proposal.params is None
    for text in (proposal.rationale_ko, proposal.rationale_en, proposal.rationale_ja):
        assert "one_way_dead_end_unfixable" in text


def test_v3_finding_with_no_cause_still_refuses_cleanly():
    """The generic cause-quoting path degrades gracefully when a V3 finding
    (e.g. v3_orphan_link, which carries no 'cause' context at all) has none
    to quote."""
    finding = {"code": "v3_orphan_link", "subject": "42", "context": {}}
    proposal = gf.propose_for_gate_finding(finding)
    assert proposal.outcome == fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == gf.CONNECTIVITY_IS_NOT_INVENTED


# ---------------------------------------------------------------------------
# (b) v4_lane_drop_without_runway: boundary-traced vs non-boundary
# ---------------------------------------------------------------------------

_LANE_DROP_FINDING = {
    "code": "v4_lane_drop_without_runway",
    "subject": "620 (South Street)",
    "context": {"lanes": 2, "dropped_lanes": [2], "required_m": 200.0, "available_m": 41.5},
}


def test_lane_drop_traced_to_synthesized_boundary_node_refuses_by_name():
    census = {
        "connectivity": {
            "dangling_approaches": [
                {"link_id": "620", "cause": "model_boundary_correct"},
            ]
        }
    }
    proposal = gf.propose_for_gate_finding(_LANE_DROP_FINDING, census=census)
    assert proposal.outcome == fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == gf.RUNWAY_ENDS_AT_REQUESTED_BOUNDARY
    assert proposal.verb is None
    assert "620" in proposal.rationale_en
    assert proposal.evidence["boundary_entry"] is not None


def test_lane_drop_not_traced_to_boundary_is_no_tool_without_fabrication():
    census = {
        "connectivity": {
            "dangling_approaches": [
                # Same link, but a DIFFERENT cause -- not the model boundary,
                # so the boundary explanation does not apply here.
                {"link_id": "620", "cause": "unmerged_node_of_another_way"},
            ]
        }
    }
    proposal = gf.propose_for_gate_finding(_LANE_DROP_FINDING, census=census)
    assert proposal.outcome == fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == gf.NO_TOOL_WITHOUT_FABRICATION
    assert proposal.evidence["boundary_entry"] is None


def test_lane_drop_with_no_census_at_all_defaults_to_no_tool_without_fabrication():
    """Absence of evidence is never read as evidence of the boundary -- only
    a POSITIVE census match softens the verdict."""
    proposal = gf.propose_for_gate_finding(_LANE_DROP_FINDING, census=None)
    assert proposal.outcome == fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == gf.NO_TOOL_WITHOUT_FABRICATION


def test_lane_drop_uses_caller_supplied_upstream_trace_over_the_findings_own_link():
    """Which link the runway shortfall traces back to is network-walk
    knowledge; when the caller supplies it explicitly it is used INSTEAD of
    the lane-dropping link's own id."""
    census = {
        "connectivity": {
            "dangling_approaches": [
                {"link_id": "999", "cause": "model_boundary_correct"},
            ]
        }
    }
    proposal = gf.propose_for_gate_finding(
        _LANE_DROP_FINDING, census=census, targets={"upstream_boundary_link": "999"}
    )
    assert proposal.reason_code == gf.RUNWAY_ENDS_AT_REQUESTED_BOUNDARY
    assert proposal.evidence["traced_link"] == "999"
    assert proposal.evidence["traced_link_supplied"] is True


def test_v4_lane_drop_declared_not_automatable_and_never_emits_a_proposal():
    mapping = gf.get_genesis_fix("v4_lane_drop_without_runway")
    assert mapping is not None
    assert mapping.status == fixes.NOT_AUTOMATABLE
    assert mapping.verb is None
    assert mapping.builder is not None  # evidence-dependent reason selection


# ---------------------------------------------------------------------------
# (c) / (d) V6 entrance-volume: MEASURED refuses, UNVERIFIED proposes an
# estimate. Synthetic fixtures in fixes.node_evidence_from_real_counts shape.
# ---------------------------------------------------------------------------

#: A node whose observed row for movement "8-617-100.0-617-200.0" enters
#: from link 617 -- exactly the join fixes.entrance_measurement resolves.
_MEASURED_NODE = {
    "node_id": "8",
    "movements": [
        {"id": "8-617-100.0-617-200.0", "direction": None, "observed": 300.0, "modelled": None},
    ],
    "sources": {"observed": "validation_workbook"},
}

_V6_FINDING = {
    "code": "v6_input_without_volume",
    "subject": "113",
    "context": {"link": "617"},
}


def test_measured_entrance_v6_finding_refuses_structurally_with_no_volume_param():
    proposal = gf.propose_for_gate_finding(
        _V6_FINDING, targets={"node": _MEASURED_NODE, "link_no": "617"}
    )
    assert proposal.outcome == fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == fixes.INPUT_VOLUME_IS_OBSERVED_DATA
    assert proposal.verb is None
    assert proposal.params is None

    # Absence, not just a refusal outcome: NO volume value exists ANYWHERE in
    # the emitted payload, including nested inside evidence.
    dumped = json.dumps(proposal.to_dict())
    assert '"volume"' not in dumped
    assert "300.0" not in dumped or "observed" in dumped  # the 300 that IS present is the count, not a proposed volume
    assert proposal.assumptions == ()


def test_unverified_entrance_v6_finding_proposes_a_named_demand_estimate():
    proposal = gf.propose_for_gate_finding(
        _V6_FINDING, targets={"node": None, "proposed_volume": 450.0, "interval_no": 1}
    )
    assert proposal.outcome == fixes.PROPOSAL
    assert proposal.verb == "set_vehicle_input_volume"
    assert proposal.params == {"input_no": "113", "volume": 450.0, "interval_no": 1}
    assert proposal.confidence == fixes.CONFIDENCE_DEMAND_ESTIMATE
    assert fixes.ASSUME_ENTRANCE_NOT_VERIFIED_MEASURED in proposal.assumptions
    assert proposal.evidence["entrance_measurement"]["status"] == fixes.ENTRANCE_UNVERIFIED


def test_unverified_entrance_with_no_proposed_volume_refuses_rather_than_guess():
    proposal = gf.propose_for_gate_finding(_V6_FINDING, targets={"node": None})
    assert proposal.outcome == fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == fixes.EVIDENCE_INSUFFICIENT
    assert proposal.params is None


def test_a_whitespace_only_v6_subject_is_refused_by_name_and_never_proposed():
    """The FIRST sibling site round 2's id normalization did not reach (unit
    #59, refuter round 3, R3-3).

    MEASURED on the round-3 build, by running this builder with the id
    normalization reverted to identity: a ``"   "`` subject came back
    ``PROPOSAL`` carrying ``params={"input_no": "   ", ...}``. Whitespace is
    truthy, so the old two-valued gate (``in (None, "")``) did not ask, and
    ``_emit``'s live-schema gate does not catch it either — ``validate_params``
    accepts that string (measured). It reached an approval card and would have
    died in ``tools_edit._coerce_id`` on dispatch, after a human signed it:
    defect D1 itself, one module over.

    The refusal is now NAMED — and it names the RIGHT thing. Round 2's single
    gate answered "no input" and "no estimate" with one ``EVIDENCE_INSUFFICIENT``
    sentence, so a target with a perfectly good 450 veh/h estimate and no input
    number was told no estimate was supplied.
    """
    finding = {"code": "v6_input_without_volume", "subject": "   ", "context": {"link": "617"}}
    proposal = gf.propose_for_gate_finding(
        finding, targets={"node": None, "proposed_volume": 450.0, "interval_no": 1}
    )

    assert proposal.outcome == fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == fixes.NO_TARGET_OBJECT
    assert proposal.verb is None
    assert proposal.params is None
    # The three spaces never appear as an ID anywhere in the payload -- but they
    # ARE still recorded as what arrived, which is what makes this refusal
    # answerable ("why does it say no input was named? because THIS came in").
    assert proposal.evidence["input_no"] is None
    assert proposal.evidence["input_no_supplied"] == "   "
    assert proposal.evidence["target"]["input_no"] is None
    # ...and the refusal says so in all three languages, not just ko.
    for language, message in proposal.rationale().items():
        assert message.strip(), language

    # The estimate WAS supplied here, so the older reason would have been a
    # false sentence: this is the pin that keeps the two causes apart.
    no_estimate = gf.propose_for_gate_finding(
        {"code": "v6_input_without_volume", "subject": "113", "context": {"link": "617"}},
        targets={"node": None},
    )
    assert no_estimate.reason_code == fixes.EVIDENCE_INSUFFICIENT


def test_the_v6_builder_goes_THROUGH_fixes_normalize_input_no(monkeypatch):
    """The delegation, executed — the cross-module half (unit #59, R3-3).

    This module's builder and ``fixes``' two are claimed to spell an input id by
    ONE rule. A claim like that is worth exactly as much as a test that fails
    when a ``.strip()`` is re-inlined here, so the rule is replaced with a spy
    no local copy could consult.
    """
    monkeypatch.setattr(fixes, "normalize_input_no", lambda raw: "SPY")
    proposal = gf.propose_for_gate_finding(
        {"code": "v6_input_without_volume", "subject": "113", "context": {"link": "617"}},
        targets={"node": None, "proposed_volume": 450.0, "interval_no": 1},
    )
    assert proposal.params["input_no"] == "SPY"


def test_a_padded_v6_subject_spells_one_id_one_way_everywhere_it_appears():
    """The quieter half of the same defect (unit #59, refuter round 3, R3-3).

    MEASURED with the normalization reverted: ``" 113 "`` produced a proposal
    whose params wrote ``" 113 "`` while the evidence's ``entrance_measurement``
    block — which has normalized since round 2 — said ``"113"``. One evidence
    payload, one input, two spellings: whichever a reader believed, the other
    was a claim about an object that does not exist.
    """
    finding = {"code": "v6_input_without_volume", "subject": " 113 ", "context": {"link": "617"}}
    proposal = gf.propose_for_gate_finding(
        finding, targets={"node": None, "proposed_volume": 450.0, "interval_no": 1}
    )

    assert proposal.outcome == fixes.PROPOSAL
    assert proposal.params == {"input_no": "113", "volume": 450.0, "interval_no": 1}
    assert proposal.evidence["input_no"] == "113"
    assert proposal.evidence["target"]["input_no"] == "113"
    assert proposal.evidence["entrance_measurement"]["input_no"] == "113"
    # ...and the padding is kept as provenance rather than as an identity.
    assert proposal.evidence["input_no_supplied"] == " 113 "
    # The whole payload carries no padded spelling of the id at all.
    assert " 113 " not in json.dumps(proposal.params or {})


def test_v6_input_volume_is_zero_shares_the_same_d4_gate():
    finding = {"code": "v6_input_volume_is_zero", "subject": "113", "context": {"link": "617"}}
    measured = gf.propose_for_gate_finding(
        finding, targets={"node": _MEASURED_NODE, "link_no": "617"}
    )
    assert measured.outcome == fixes.NO_AUTOMATED_FIX
    assert measured.reason_code == fixes.INPUT_VOLUME_IS_OBSERVED_DATA


# ---------------------------------------------------------------------------
# (f) AUTOMATABLE entries' params validate against the verb's real schema;
# a deliberately broken payload fails.
# ---------------------------------------------------------------------------


def test_v4_more_mapped_lanes_params_validate_and_a_broken_payload_fails():
    finding = {
        "code": "v4_more_mapped_lanes_than_link_has",
        "subject": "77",
        "context": {"side": "from", "mapped_lanes": 3, "link": "620", "link_lanes": 2},
    }
    proposal = gf.propose_for_gate_finding(finding)
    assert proposal.outcome == fixes.PROPOSAL
    ok, _err = fixes.validate_params(proposal.verb, proposal.params)
    assert ok

    # Deliberately broken: SetLinkLaneCountParams requires lane_count >= 1.
    broken_ok, broken_err = fixes.validate_params(
        proposal.verb, {**proposal.params, "lane_count": 0}
    )
    assert not broken_ok
    assert broken_err


def test_v9_queue_counter_params_validate_and_a_broken_payload_fails():
    finding = {
        "code": "v9_stop_line_without_queue_counter",
        "subject": "620 (South Street)",
        "context": {"heads": 1},
    }
    proposal = gf.propose_for_gate_finding(finding, targets={"link_length_m": 150.0})
    assert proposal.outcome == fixes.PROPOSAL
    ok, _err = fixes.validate_params(proposal.verb, proposal.params)
    assert ok

    # Deliberately broken: AddQueueCounterParams forbids extra keys.
    broken_ok, broken_err = fixes.validate_params(
        proposal.verb, {**proposal.params, "not_a_real_field": 1}
    )
    assert not broken_ok
    assert broken_err


def test_v7_geometry_fix_params_validate_and_a_degenerate_payload_fails():
    finding = {
        "code": "v7_zero_length_link",
        "subject": "50",
        "context": {"length_m": 0.0},
    }
    proposal = gf.propose_for_gate_finding(
        finding, targets={"corrected_points_xy": [(0.0, 0.0), (10.0, 5.0)]}
    )
    assert proposal.outcome == fixes.PROPOSAL
    ok, _err = fixes.validate_params(proposal.verb, proposal.params)
    assert ok

    # Deliberately broken: SetLinkGeometryParams refuses a degenerate
    # (duplicate-point, zero-length) polyline.
    broken_ok, broken_err = fixes.validate_params(
        proposal.verb, {"link_id": "50", "points_xy": [(0.0, 0.0), (0.0, 0.0)]}
    )
    assert not broken_ok
    assert broken_err


def test_v7_geometry_fix_without_a_corrected_polyline_refuses_rather_than_invent_one():
    finding = {"code": "v7_links_share_one_polyline", "subject": "50", "context": {"links": ["50", "51"]}}
    proposal = gf.propose_for_gate_finding(finding, targets=None)
    assert proposal.outcome == fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == gf.NO_TOOL_WITHOUT_FABRICATION


def test_every_automatable_entry_names_a_verb_registered_at_write_tier():
    for mapping in gf.get_genesis_fix_registry():
        if mapping.status != fixes.AUTOMATABLE:
            continue
        resolution = fixes.resolve_verb(mapping.verb)
        assert resolution["resolved"], f"{mapping.code}: verb {mapping.verb!r} does not resolve"
        assert resolution["tier"] == "write"


# ---------------------------------------------------------------------------
# (h) B5-2 stage-3 fix round: subject<->params addressing. VERB_ADDRESSING_FIELD
# must cover every AUTOMATABLE verb (drift protection, the mirror check's own
# shape), and expected_fix_target must read the RIGHT object id off a real
# finding for every code family that has an addressing convention at all.
# ---------------------------------------------------------------------------


def test_every_automatable_verb_has_an_addressing_field():
    """The drift protection the spec asks for: an AUTOMATABLE mapping whose
    verb has no entry in VERB_ADDRESSING_FIELD would let
    create_genesis_fix_job's subject/params cross-check silently no-op for
    that verb (``addressing_field`` would be falsy and the check would be
    skipped) -- this must fail loudly instead."""
    automatable_verbs = {m.verb for m in gf.get_genesis_fix_registry() if m.status == fixes.AUTOMATABLE}
    assert automatable_verbs, "the registry must have at least one AUTOMATABLE entry for this to mean anything"
    assert automatable_verbs <= set(gf.VERB_ADDRESSING_FIELD), (
        f"AUTOMATABLE verb(s) with no addressing field: "
        f"{automatable_verbs - set(gf.VERB_ADDRESSING_FIELD)}"
    )


def test_expected_fix_target_reads_the_addressed_object_per_code_family():
    v6_finding = {"code": "v6_input_volume_is_zero", "subject": "113", "context": {"link": "617"}}
    assert gf.expected_fix_target("v6_input_volume_is_zero", v6_finding) == "113"

    v7_finding = {"code": "v7_zero_length_link", "subject": "620 (South Street)", "context": {}}
    assert gf.expected_fix_target("v7_zero_length_link", v7_finding) == "620"

    v9_finding = {"code": "v9_stop_line_without_queue_counter", "subject": "42", "context": {"heads": 2}}
    assert gf.expected_fix_target("v9_stop_line_without_queue_counter", v9_finding) == "42"

    v4_finding = {
        "code": "v4_more_mapped_lanes_than_link_has",
        "subject": "connector 10001",
        "context": {"link": "620", "mapped_lanes": 2, "link_lanes": 1},
    }
    assert gf.expected_fix_target("v4_more_mapped_lanes_than_link_has", v4_finding) == "620"

    # NOT_AUTOMATABLE codes, and an unknown code, have no addressing
    # convention at all -- None, not a guess.
    assert gf.expected_fix_target("v3_orphan_link", {"code": "v3_orphan_link", "subject": "1", "context": {}}) is None
    assert gf.expected_fix_target("v0_not_a_real_code", {"subject": "1"}) is None

    # An absent/empty subject or context field is also None, never "".
    assert gf.expected_fix_target("v6_input_volume_is_zero", {"subject": "", "context": {}}) is None
    assert (
        gf.expected_fix_target(
            "v4_more_mapped_lanes_than_link_has", {"subject": "x", "context": {}}
        )
        is None
    )


# ---------------------------------------------------------------------------
# (g) registry liveness: derived, not declared.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verb",
    sorted({m.verb for m in gf.get_genesis_fix_registry() if m.status == fixes.AUTOMATABLE}),
)
def test_removing_a_mapped_verb_flags_every_dependent_entry(verb):
    intact = _registry_copy()
    mutated = _registry_copy(drop=verb)

    affected = [m for m in gf.get_genesis_fix_registry() if m.verb == verb]
    assert affected, f"no genesis fix mapping references {verb}; this mutation proves nothing"

    for mapping in affected:
        before, _ = gf.effective_status(mapping, registry=intact)
        after, detail = gf.effective_status(mapping, registry=mutated)
        assert before == fixes.AUTOMATABLE, mapping.code
        assert after == fixes.VERB_UNREGISTERED, mapping.code
        assert detail["verb_resolution"]["why"] == "NOT_REGISTERED"

    coverage = gf.get_genesis_fix_coverage(registry=mutated)
    assert {
        code for code, entry in coverage.items() if entry["status"] == fixes.VERB_UNREGISTERED
    } == {m.code for m in affected}


def test_a_vanished_verb_makes_propose_genesis_fix_refuse_not_emit():
    mutated = _registry_copy(drop="set_link_lane_count")
    finding = {
        "code": "v4_more_mapped_lanes_than_link_has",
        "subject": "77",
        "context": {"side": "from", "mapped_lanes": 3, "link": "620", "link_lanes": 2},
    }
    proposal = gf.propose_for_gate_finding(finding, registry=mutated)
    assert proposal.outcome == fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == fixes.VERB_NOT_IN_REGISTRY
    assert proposal.verb is None


def test_retiering_a_mapped_verb_also_flags_the_mapping():
    mutated = _registry_copy(retier=("add_queue_counter", "read"))
    mapping = gf.get_genesis_fix("v9_stop_line_without_queue_counter")
    status, detail = gf.effective_status(mapping, registry=mutated)
    assert status == fixes.VERB_UNREGISTERED
    assert detail["verb_resolution"]["why"] == "WRONG_TIER"


# ---------------------------------------------------------------------------
# Registry validation itself (register_genesis_fix's own guardrails)
# ---------------------------------------------------------------------------


def test_register_genesis_fix_rejects_a_duplicate_code():
    """B5-2 stage-3 fix round item 5: a dedicated test for the duplicate-code
    guard, which previously relied on an import-time crash (every real code
    IS already registered at import, so any accidental second registration
    anywhere would already blow up the whole backend) as an accidental
    backstop rather than being exercised directly. Uses a real, already
    -registered code -- ``register_genesis_fix`` itself, not this test, is
    what decides that is a duplicate -- and proves the ORIGINAL entry
    survives the rejected attempt untouched."""
    code = "v3_orphan_link"
    before = gf.get_genesis_fix(code)
    assert before is not None

    duplicate = gf.GenesisFixMapping(
        code=code,
        status=fixes.NOT_AUTOMATABLE,
        check_id="V3",
        label_ko="dup", label_en="dup", label_ja="dup",
        reason_ko="dup", reason_en="dup", reason_ja="dup",
        reason_code=gf.CONNECTIVITY_IS_NOT_INVENTED,
    )
    with pytest.raises(ValueError, match="duplicate code"):
        gf.register_genesis_fix(duplicate)

    after = gf.get_genesis_fix(code)
    assert after is before, "a rejected duplicate registration must not replace the original entry"


def test_register_genesis_fix_rejects_a_code_gate_py_does_not_emit():
    with pytest.raises(ValueError, match="not one of gate.py's own finding codes"):
        gf.register_genesis_fix(
            gf.GenesisFixMapping(
                code="v99_not_a_real_code",
                status=fixes.NOT_AUTOMATABLE,
                check_id="V99",
                label_ko="x", label_en="x", label_ja="x",
                reason_ko="x", reason_en="x", reason_ja="x",
                reason_code="X",
            )
        )


def test_register_genesis_fix_rejects_automatable_with_no_builder():
    # The real catalogue already registers this code (register_genesis_fix's
    # duplicate check would otherwise fire first and prove nothing about the
    # check under test) -- pop it, attempt the bad re-registration, restore.
    code = "v9_stop_line_without_queue_counter"
    saved = gf._REGISTRY.pop(code)
    try:
        with pytest.raises(ValueError, match="has no builder"):
            gf.register_genesis_fix(
                gf.GenesisFixMapping(
                    code=code,
                    status=fixes.AUTOMATABLE,
                    check_id="V9",
                    label_ko="x", label_en="x", label_ja="x",
                    reason_ko="x", reason_en="x", reason_ja="x",
                    verb="add_queue_counter",
                )
            )
    finally:
        gf._REGISTRY[code] = saved


def test_register_genesis_fix_rejects_not_automatable_naming_a_verb():
    code = "v1_load_refused"
    saved = gf._REGISTRY.pop(code)
    try:
        with pytest.raises(ValueError, match="is NOT_AUTOMATABLE but names verb"):
            gf.register_genesis_fix(
                gf.GenesisFixMapping(
                    code=code,
                    status=fixes.NOT_AUTOMATABLE,
                    check_id="V1",
                    label_ko="x", label_en="x", label_ja="x",
                    reason_ko="x", reason_en="x", reason_ja="x",
                    verb="set_link_lane_count",
                    reason_code="X",
                )
            )
    finally:
        gf._REGISTRY[code] = saved


def test_not_automatable_builder_that_emitted_a_proposal_would_be_caught():
    """The assertion inside propose_genesis_fix (a NOT_AUTOMATABLE entry's
    builder must never emit a PROPOSAL) is exercised directly here, against a
    deliberately misbehaving stand-in mapping, rather than only trusted to
    hold because none of the real ones happen to trip it."""

    def _misbehaving_builder(**_kwargs):
        return fixes.FixProposal(
            key="v9_stop_line_without_queue_counter",
            key_kind=gf.KEY_GATE_FINDING,
            outcome=fixes.PROPOSAL,
            scope=fixes.NOT_APPLICABLE,
            verb="add_queue_counter",
            params={"link_no": "1", "pos_m": 1.0, "name": ""},
            evidence={},
            rationale_ko="x", rationale_en="x", rationale_ja="x",
            confidence=fixes.CONFIDENCE_NONE,
        )

    rogue = gf.GenesisFixMapping(
        code="__test_rogue__",
        status=fixes.NOT_AUTOMATABLE,
        check_id="V9",
        label_ko="x", label_en="x", label_ja="x",
        reason_ko="x", reason_en="x", reason_ja="x",
        builder=_misbehaving_builder,
    )
    # Bypass register_genesis_fix's own code-membership check (this mapping
    # is a test double, not a real gate code) and drive propose_genesis_fix's
    # dispatch directly against it.
    gf._REGISTRY["__test_rogue__"] = rogue
    try:
        with pytest.raises(AssertionError, match="emitted a PROPOSAL"):
            gf.propose_genesis_fix("__test_rogue__")
    finally:
        del gf._REGISTRY["__test_rogue__"]


# ---------------------------------------------------------------------------
# An unregistered code refuses cleanly, never raises, never returns None.
# ---------------------------------------------------------------------------


def test_unregistered_code_refuses_cleanly():
    proposal = gf.propose_genesis_fix("v0_does_not_exist")
    assert proposal.outcome == fixes.NO_AUTOMATED_FIX
    assert proposal.reason_code == fixes.NO_MAPPING_REGISTERED


def test_coverage_reports_every_registered_code():
    coverage = gf.get_genesis_fix_coverage()
    assert set(coverage) == {m.code for m in gf.get_genesis_fix_registry()}
    for code, entry in coverage.items():
        assert entry["declared_status"] in (fixes.AUTOMATABLE, fixes.NOT_AUTOMATABLE)

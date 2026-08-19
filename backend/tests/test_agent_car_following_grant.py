"""Unit #44-b — the ONE narrow exception to ``driving_behavior_shared_object_edit``.

What these tests are for
------------------------
DECISIONS.md 2026-08-19 relaxed a refusal. Every test here exists because a relaxed
refusal is only as good as the things it still refuses, and this repo has twice shipped a
guard that a caller could talk its way past by asserting compliance (the guardrail's own
``duplicated_before_edit`` flag, and ``saturation_flow``'s ``measurement["ok"]``).

1. **The chain is real, end to end.** A proposal that the builder actually produced mints
   a grant; that grant, bound to the approval machinery, opens the guardrail once per
   member; a reused approval does not.

2. **The exception is a per-action match, not a mode.** Everything the grant does not name
   by canonical signature is refused while the grant is right there in ``extra``: a
   different value, a different behaviour, one of the other five whitelisted parameters,
   a W99 ``cc*`` name. This is the whole of "파라미터 한정" — there is no separate
   allow-list check to drift out of sync with it.

3. **Consent is to a NAMED radius.** If the reference graph moves between approval and
   apply, the edit is refused BY NAME in all three languages, quoting both numbers.

4. **The channel cannot be reached from a payload.** The provenance lives in the
   constraint engine's ``extra``, written only by ``orchestrator.py`` from the write gate.
   A planner-shaped payload that puts grant-looking data in its own params changes
   nothing, and a hand-built ``extra`` that is not a real grant object is ignored.

The forecast/engine agreement matrix (reference counts × both paths) lives in
``test_agent_saturation_flow.py`` beside the forecast it constrains.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.agent import car_following_grant as cfg  # noqa: E402
from services import vissim_com  # noqa: E402
from services.agent import constraints, fixes, geh_ladder, guardrails  # noqa: E402
from services.agent import tools_network  # noqa: E402
from services.agent import saturation_flow as sf  # noqa: E402
from services.agent.gates import (  # noqa: E402
    ApprovalRequired,
    PreapprovedWriteGate,
    ProposeWriteGate,
    signature,
)
from test_agent_geh_ladder import _diagnosis, _report  # noqa: E402

SHARED_OBJECT = "driving_behavior_shared_object_edit"
CONSENTED = "car_following_shared_edit_consented"
DRIFTED = "car_following_consent_radius_drifted"


# ---------------------------------------------------------------------------
# Fixtures — a behaviour whose reference graph is NAMED, not just counted
# ---------------------------------------------------------------------------


def _behavior(reference_count: int = 4, *, truncated: bool = False) -> dict[str, Any]:
    """The shadow-model shape ``tools_network._parse_driving_behaviors`` publishes.

    Each consumer gets its own link-behavior-type number and link count, because a
    fingerprint over N identical entries would pass a drift check that a real network
    change would fail.
    """
    consumers = [
        {
            "link_behavior_type_no": str(i + 1),
            "link_behavior_type_name": f"Urban {i + 1}",
            "veh_class_no": None,
            "link_count": 10 + i,
        }
        for i in range(reference_count)
    ]
    return {
        "no": "2",
        "name": "Urban (motorized) - SatFlow (1,950)",
        "car_following_model": 74,
        "reference_count": reference_count,
        "referenced_by": {
            "count": reference_count,
            "consumers": consumers,
            "truncated": truncated,
        },
    }


def _shadow(reference_count: int = 4, **kwargs: Any) -> dict[str, Any]:
    """The target behaviour plus a SECOND shared W74 behaviour it is not the grant for.

    The neighbour exists so "borrow the approval and point it at a different behaviour"
    is a real test rather than one that trips over #1 object_not_found before the
    exception is ever consulted.
    """
    neighbour = {**_behavior(reference_count, **kwargs), "no": "3", "name": "Urban (merging)"}
    return {"driving_behaviors": [_behavior(reference_count, **kwargs), neighbour]}


def _measurement() -> dict[str, Any]:
    return sf.observed_measurement(
        saturation_flow_pcu=1950,
        method="stop-line discharge headway, on-site (MRWA Appendix B §B.3)",
        measured_at="2026-08-14 07:30-08:30",
        attribution="Dochak survey team",
        samples=8,
        vehicles_per_sample=11,
        intersection="Murdoch Dr / South St",
        approach_link="501",
    )


def _proposals(reference_count: int = 4) -> tuple[fixes.FixProposal, ...]:
    rung = _diagnosis(_report())
    assert rung["rung"] == geh_ladder.CAPACITY_LIMIT, "fixture no longer reaches the rung"
    actions = sf.propose_car_following_parameters(
        rung=rung, measurement=_measurement(), behavior=_behavior(reference_count)
    )
    assert [a.outcome for a in actions] == [fixes.PROPOSAL] * 3
    return actions


def _propose_over(behavior: dict[str, Any]) -> tuple[fixes.FixProposal, ...]:
    """:func:`_proposals` for a caller-supplied behaviour shape rather than a count."""
    actions = sf.propose_car_following_parameters(
        rung=_diagnosis(_report()), measurement=_measurement(), behavior=behavior
    )
    assert [a.outcome for a in actions] == [fixes.PROPOSAL] * 3
    return actions


def _shadow_of(behavior: dict[str, Any]) -> dict[str, Any]:
    """:func:`_shadow` around one already-built behaviour entry."""
    neighbour = {**behavior, "no": "3", "name": "Urban (merging)"}
    return {"driving_behaviors": [behavior, neighbour]}


def _payloads(proposals: tuple[fixes.FixProposal, ...]) -> list[dict[str, Any]]:
    return [{"tool": p.verb, "params": dict(p.params or {})} for p in proposals]


def _preflight(
    payload: dict[str, Any], shadow: dict[str, Any], gate: Any = None
) -> guardrails.GuardrailReport:
    """The REAL engine, fed provenance exactly the way ``orchestrator.py`` does.

    Mirrors ``orchestrator._preflight_extra``: the gate is asked, and nothing else can
    contribute. A ``None`` gate is the direct path — a plain tool call.
    """
    extra: dict[str, Any] = {}
    if gate is not None:
        provenance_fn = getattr(gate, "write_provenance", None)
        provenance = provenance_fn(payload) if provenance_fn is not None else None
        if provenance:
            extra = {cfg.PROVENANCE_KEY: provenance}
    return guardrails.GuardrailEngine(disabled=False).preflight(payload, shadow, extra)


def _ids(report: guardrails.GuardrailReport) -> set[tuple[str, str]]:
    return {(f.constraint_id, f.tier) for f in report.findings}


def _approved_gate(
    proposals: tuple[fixes.FixProposal, ...], grant: cfg.CarFollowingGrant | None = None
) -> PreapprovedWriteGate:
    payloads = _payloads(proposals)
    return PreapprovedWriteGate(
        {signature(p["tool"], p["params"]) for p in payloads},
        grant=grant if grant is not None else sf.grant_from_proposals(proposals),
    )


# ---------------------------------------------------------------------------
# 1. The chain: proposal → grant → gate → engine → consumed
# ---------------------------------------------------------------------------


def test_the_whole_chain_opens_the_guardrail_once_per_member_and_then_stops():
    """Proposal → grant → approval gate → constraint engine → consumed.

    The claim DECISIONS.md 2026-08-19 makes is that #44's live application stops being
    blocked at the guardrail. This walks that path with the real builder, the real gate
    and the real engine, and then walks one step past the end of it.
    """
    proposals = _proposals(reference_count=4)
    grant = sf.grant_from_proposals(proposals)
    payloads = _payloads(proposals)
    shadow = _shadow(4)
    gate = _approved_gate(proposals, grant)

    for payload in payloads:
        assert gate.authorize(payload, None) is True
        report = _preflight(payload, shadow, gate)
        assert report.blocking is False, payload
        # The refusal stood down, and the exception said so rather than going quiet.
        assert (SHARED_OBJECT, "refuse") not in _ids(report)
        assert (CONSENTED, "warn") in _ids(report)

    # One step past the end: an approved signature is good for exactly ONE dispatch, and
    # the refusal happens at the GATE — the engine is never asked to excuse a reuse.
    # ``test_agent_orchestrator_approval.py``'s own trace assertion pins the consequence
    # at turn level: the re-issue produces `plan` → `refusal` with no `preflight` between.
    assert gate.authorize(payloads[0], None) is False
    assert gate.refusal_reason(payloads[0], None) == "approval_consumed"


def test_the_grant_carries_the_measurement_that_justified_it():
    """A grant with no measurement behind it would be an exception granted on a hunch,
    which is the failure mode the whole ladder exists to prevent. The refusal text the
    exception emits cites the number and who took it, so it has to travel."""
    grant = sf.grant_from_proposals(_proposals())

    assert grant.measurement["provenance"] == sf.OBSERVED
    assert grant.measurement["saturation_flow_pcu"] == 1950
    assert grant.measurement["attribution"] == "Dochak survey team"
    assert grant.saturation_flow_pcu == 1950.0
    assert grant.values == {"ax": 2.5, "bx_add": 2.25, "bx_mult": 3.25}

    finding = next(
        f
        for f in _preflight(
            _payloads(_proposals())[0], _shadow(4), _approved_gate(_proposals())
        ).findings
        if f.constraint_id == CONSENTED
    )
    for language in ("ko", "en", "ja"):
        assert "1950" in finding.messages[language]
        assert "Dochak survey team" in finding.messages[language]


def test_the_exception_names_the_radius_in_all_three_languages():
    """The decision's safety argument IS the sentence: "행태 N을 참조하는 링크 M곳에 함께
    적용됩니다". An exception that fired silently would have removed a refusal and put
    nothing in its place."""
    report = _preflight(_payloads(_proposals())[0], _shadow(4), _approved_gate(_proposals()))
    finding = next(f for f in report.findings if f.constraint_id == CONSENTED)

    assert finding.tier == "warn"
    assert finding.details["consented_radius"]["reference_count"] == 4
    assert finding.details["consented_radius"]["link_total"] == 46  # 10+11+12+13
    assert finding.details["decision"] == "DECISIONS.md 2026-08-19"
    for language, consumers, links in (("ko", "4곳", "46개"), ("en", "4 consumer(s)", "46 link"),
                                       ("ja", "4件", "46本")):
        text = finding.messages[language]
        assert consumers in text and links in text, language
        assert "2026-08-19" in text, language


def test_an_unshared_behaviour_announces_no_exception_because_none_was_needed():
    """A behaviour with zero references was always editable in place. Emitting the
    consent warning there would credit the exception for an edit that never needed it —
    and would train a reader to ignore the one sentence that matters."""
    proposals = _proposals(reference_count=0)
    report = _preflight(_payloads(proposals)[0], _shadow(0), _approved_gate(proposals))

    assert report.blocking is False
    assert report.findings == []


# ---------------------------------------------------------------------------
# 2. Everything the exception still refuses
# ---------------------------------------------------------------------------


def test_a_direct_tool_call_is_refused_exactly_as_before():
    """No gate, no grant, no approval — the guardrail's original job, untouched. This is
    the path ``fixes.preflight_findings``, the health check and every planner tool call
    that did not come through an approval take."""
    for payload in _payloads(_proposals()):
        report = _preflight(payload, _shadow(4))
        assert report.blocking is True
        assert (SHARED_OBJECT, "refuse") in _ids(report)
        assert (CONSENTED, "warn") not in _ids(report)


#: The five whitelisted driving-behaviour parameters §5.2.5.1 does NOT name, read from
#: the write layer's own list rather than retyped, so a future addition to the whitelist
#: automatically has to prove it stays outside the exception.
_UNGRANTED_WHITELIST = tuple(
    name
    for name in vissim_com.VissimController.DRIVING_BEHAVIOR_PARAM_FIELDS
    if name not in cfg.GRANTED_PARAMETERS
)


def test_the_exception_covers_three_of_the_eight_whitelisted_parameters():
    """The arithmetic the sweep below depends on, stated once: eight parameters are
    writable, the exception reaches exactly the three §5.2.5.1 names."""
    assert set(cfg.GRANTED_PARAMETERS) == {"ax", "bx_add", "bx_mult"}
    assert len(_UNGRANTED_WHITELIST) == 5
    assert set(cfg.GRANTED_PARAMETERS).isdisjoint(_UNGRANTED_WHITELIST)


@pytest.mark.parametrize(
    "params,why",
    [
        (
            {"behavior_no": "2", "parameter": "ax", "value": 1.0},
            "a granted parameter at a value no Table 5-6 row publishes",
        ),
        (
            {"behavior_no": "2", "parameter": "ax", "value": 2.6},
            "a granted parameter one decimal place off the approved value",
        ),
        (
            {"behavior_no": "2", "parameter": "bx_add", "value": 3.5},
            "a granted parameter at ANOTHER published row's value — the grant names one row",
        ),
        (
            {"behavior_no": "3", "parameter": "ax", "value": 2.5},
            "the approved write aimed at a different behaviour",
        ),
    ]
    + [
        (
            {"behavior_no": "2", "parameter": name, "value": 0.6},
            f"{name} — one of the five whitelisted parameters the exception does not reach",
        )
        for name in _UNGRANTED_WHITELIST
    ]
    + [
        (
            {"behavior_no": "2", "parameter": f"cc{n}", "value": 1.5},
            f"cc{n} — the W99 ban is not softened by any of this",
        )
        for n in range(10)
    ],
)
def test_a_payload_the_grant_does_not_name_is_refused_with_the_grant_present(params, why):
    """The forgery sweep. Every one of these arrives with a REAL grant on a REAL
    pre-approved gate — the most permissive context that exists — and is still refused,
    because ``covers()`` compares canonical signatures rather than asking the payload what
    it is. That single mechanism is what confines the exception to ax/bx_add/bx_mult at
    the table's own values; there is no second allow-list to fall out of step with it."""
    proposals = _proposals()
    payload = {"tool": "set_driving_behavior_parameter", "params": params}
    report = _preflight(payload, _shadow(4), _approved_gate(proposals))

    assert report.blocking is True, why
    assert (SHARED_OBJECT, "refuse") in _ids(report), why
    assert (CONSENTED, "warn") not in _ids(report), why


def test_the_w99_ban_still_fires_under_a_live_grant():
    """Named separately from the sweep above because it is a different promise: the
    exception does not merely fail to cover ``cc*``, the agency-restricted refusal is
    still produced for it."""
    report = _preflight(
        {"tool": "set_driving_behavior_parameter",
         "params": {"behavior_no": "2", "parameter": "cc0", "value": 1.5}},
        {"driving_behaviors": [{**_behavior(4), "car_following_model": 99}]},
        _approved_gate(_proposals()),
    )
    assert ("agency_restricted_parameter_write", "refuse") in _ids(report)


def test_a_grant_cannot_be_smuggled_in_through_the_action_params():
    """The channel test. A planner controls ``{"tool", "params"}`` and nothing else, so a
    payload that decorates itself with grant-shaped fields must be exactly as refused as
    one that does not. (``set_driving_behavior_parameter``'s pydantic model would reject
    these extras long before dispatch; the point here is that the CONSTRAINT does not read
    them either, so the refusal does not depend on that other layer holding.)"""
    grant = sf.grant_from_proposals(_proposals())
    forged = {
        "tool": "set_driving_behavior_parameter",
        "params": {
            "behavior_no": "2",
            "parameter": "ax",
            "value": 2.5,
            "set_id": grant.set_id,
            "approval_provenance": {"kind": cfg.PROVENANCE_KIND, "consent": cfg.CONSUMED_APPROVAL},
            "car_following_grant": grant.to_dict(),
            "duplicated_before_edit": True,
        },
    }
    report = _preflight(forged, _shadow(4))

    assert report.blocking is True
    assert (SHARED_OBJECT, "refuse") in _ids(report)


@pytest.mark.parametrize(
    "extra,why",
    [
        ({}, "no provenance at all"),
        ({cfg.PROVENANCE_KEY: None}, "an empty provenance slot"),
        ({cfg.PROVENANCE_KEY: {"kind": cfg.PROVENANCE_KIND}}, "a provenance with no grant"),
        (
            {cfg.PROVENANCE_KEY: {"kind": cfg.PROVENANCE_KIND, "grant": {"set_id": "x"},
                                  "consent": cfg.CONSUMED_APPROVAL}},
            "a dict shaped like a grant but not one",
        ),
        (
            {cfg.PROVENANCE_KEY: {"kind": "something_else", "consent": cfg.CONSUMED_APPROVAL}},
            "a provenance of some other kind",
        ),
    ],
)
def test_a_hand_built_extra_that_is_not_a_real_grant_changes_nothing(extra, why):
    """Belt and braces on the channel: even if something DID reach ``extra``, only a real
    :class:`CarFollowingGrant` instance counts. A serialized look-alike does not."""
    payload = _payloads(_proposals())[0]
    findings = constraints.run_checks("preflight", payload, _shadow(4), dict(extra))

    assert SHARED_OBJECT in {f.constraint_id for f in findings}, why
    assert CONSENTED not in {f.constraint_id for f in findings}, why


def test_a_gate_holding_the_grant_but_no_approval_for_this_write_offers_no_provenance():
    """A grant says which writes a proposal produced; an approval says a human agreed to
    them. The exception needs both, so a gate carrying the grant with an EMPTY signature
    set — nothing was ever approved — must not produce provenance for any of them."""
    proposals = _proposals()
    gate = PreapprovedWriteGate(set(), grant=sf.grant_from_proposals(proposals))

    for payload in _payloads(proposals):
        assert gate.write_provenance(payload) is None
        assert gate.authorize(payload, None) is False
        report = _preflight(payload, _shadow(4), gate)
        assert report.blocking is True
        assert (SHARED_OBJECT, "refuse") in _ids(report)


def test_a_gate_with_no_grant_at_all_is_the_unchanged_world():
    """Every other unit's approvals. The gate implements ``write_provenance`` but carries
    no grant, so a shared-behaviour edit it approves is refused exactly as it was before
    #44-b — the exception did not become a property of being approved."""
    proposals = _proposals()
    payloads = _payloads(proposals)
    gate = PreapprovedWriteGate({signature(p["tool"], p["params"]) for p in payloads})

    assert gate.authorize(payloads[0], None) is True
    assert gate.write_provenance(payloads[0]) is None
    report = _preflight(payloads[0], _shadow(4), gate)
    assert report.blocking is True
    assert (SHARED_OBJECT, "refuse") in _ids(report)


# ---------------------------------------------------------------------------
# 3. Consent was to a NAMED radius
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "now,why",
    [
        (5, "a consumer was ADDED after approval — the edit reaches more links than shown"),
        (3, "a consumer was REMOVED — the sentence the human read is no longer true either"),
        (0, "every consumer went away; the write is harmless now, but the model moved"),
    ],
)
def test_a_drifted_blast_radius_is_refused_by_name(now, why):
    """Approval was given against a 4-consumer radius. Anything else at apply time is a
    refusal that QUOTES both numbers — "동의는 N개 링크에 대한 것이었는데 지금은 M개" —
    rather than a generic "something changed"."""
    proposals = _proposals(reference_count=4)
    payload = _payloads(proposals)[0]
    gate = _approved_gate(proposals)
    gate.authorize(payload, None)

    report = _preflight(payload, _shadow(now), gate)

    assert report.blocking is True, why
    assert (DRIFTED, "refuse") in _ids(report), why
    finding = next(f for f in report.findings if f.constraint_id == DRIFTED)
    assert finding.details["consented_radius"]["reference_count"] == 4
    assert finding.details["current_radius"]["reference_count"] == now
    for language in ("ko", "en", "ja"):
        text = finding.messages[language]
        assert "4" in text and str(now) in text, language


def test_a_drift_is_refused_twice_not_once():
    """Defence in depth, chosen deliberately: ``driving_behavior_shared_object_edit`` does
    NOT stand down on a drifted radius, so the write is refused by the original guardrail
    as well as by the named one. A bug in the new drift check therefore cannot silently
    re-open the old hole."""
    proposals = _proposals(reference_count=4)
    payload = _payloads(proposals)[0]
    gate = _approved_gate(proposals)
    gate.authorize(payload, None)

    ids = _ids(_preflight(payload, _shadow(5), gate))
    assert (SHARED_OBJECT, "refuse") in ids
    assert (DRIFTED, "refuse") in ids


def test_a_consumer_swap_that_keeps_the_count_is_still_drift():
    """The count alone is not the radius. Approval named WHICH link-behavior-types are
    touched, so replacing one with another — same number of consumers, different links —
    has to be caught."""
    proposals = _proposals(reference_count=4)
    payload = _payloads(proposals)[0]
    gate = _approved_gate(proposals)
    gate.authorize(payload, None)

    swapped = _shadow(4)
    swapped["driving_behaviors"][0]["referenced_by"]["consumers"][2] = {
        "link_behavior_type_no": "97",
        "link_behavior_type_name": "Freeway ramp",
        "veh_class_no": None,
        "link_count": 12,
    }
    assert (DRIFTED, "refuse") in _ids(_preflight(payload, swapped, gate))


def test_a_rename_is_not_drift():
    """The other direction, and the reason the fingerprint excludes display names:
    renaming a link behaviour type moves no links. Refusing there would block a correct,
    approved edit for a cosmetic change — a false refusal is a product defect too."""
    proposals = _proposals(reference_count=4)
    payload = _payloads(proposals)[0]
    gate = _approved_gate(proposals)
    gate.authorize(payload, None)

    renamed = _shadow(4)
    for consumer in renamed["driving_behaviors"][0]["referenced_by"]["consumers"]:
        consumer["link_behavior_type_name"] = "renamed by the modeller"
    report = _preflight(payload, renamed, gate)

    assert report.blocking is False
    assert (CONSENTED, "warn") in _ids(report)


def test_the_named_list_being_truncated_is_itself_part_of_the_radius():
    """``tools_network`` caps the NAMED consumer list at 10 while keeping the count real.
    The cap being crossed changes how much of the radius a human could actually read, so
    it is part of the fingerprint rather than an invisible detail."""
    assert cfg.consent_radius(_behavior(4, truncated=True)) != cfg.consent_radius(_behavior(4))

    proposals = _proposals(reference_count=4)
    payload = _payloads(proposals)[0]
    gate = _approved_gate(proposals)
    gate.authorize(payload, None)
    assert (DRIFTED, "refuse") in _ids(_preflight(payload, _shadow(4, truncated=True), gate))


def test_a_target_named_without_its_reference_graph_cannot_excuse_anything():
    """The safe direction, pinned. A caller that names a behaviour but omits
    ``referenced_by`` consents to an EMPTY radius, which cannot match a live model that
    has consumers — so the edit is refused rather than waved through on a radius nobody
    was shown. This is why ``calibration_loop_live_check._shadow_driving_behavior`` now
    carries the field."""
    rung = _diagnosis(_report())
    proposals = sf.propose_car_following_parameters(
        rung=rung,
        measurement=_measurement(),
        behavior={"no": "2", "name": "Urban (motorized)", "reference_count": 4},
    )
    payload = _payloads(proposals)[0]
    gate = _approved_gate(proposals)
    gate.authorize(payload, None)

    assert proposals[0].evidence["consent_radius"]["consumers"] == []
    assert (DRIFTED, "refuse") in _ids(_preflight(payload, _shadow(4), gate))


# ---------------------------------------------------------------------------
# 3b. BROKEN → FIXED (refuter, #44-b): a radius that was never whole
#
# All three findings below are the same defect seen from three sides:
# ``consent_radius`` summed ``link_total`` over the NAMED consumers only, while
# presenting itself — to the drift check and to the human — as the behaviour's
# reference graph.
# ---------------------------------------------------------------------------


def _many(reference_count: int) -> dict[str, Any]:
    """A behaviour past ``tools_network``'s 10-name cap, capped the way that module
    caps it, so ``truncated`` and the short ``consumers`` list are real rather than
    hand-set."""
    behavior = _behavior(reference_count)
    consumers = behavior["referenced_by"]["consumers"]
    behavior["referenced_by"] = tools_network._bounded_driving_behavior_consumers(consumers)
    return behavior


def test_an_unnamed_consumer_cannot_change_by_any_amount_behind_the_consent():
    """BROKEN → FIXED. Measured before the fix: a behaviour with 13 consumers named 10 of
    them, and ``link_total`` summed only those 10 (145 of a real 208). The three consumers
    the fingerprint never named could then grow from 20/21/22 links to 5000 each — a real
    radius of 208 → 15145 links — and ``evaluate`` still returned GRANTED, because nothing
    it compared had moved.

    The exception is not allowed to cover what it cannot watch, so a radius that is not
    ``complete`` is refused outright — and refused by the ORIGINAL guardrail, whose
    message already explains itself, rather than by a new name."""
    proposals = _propose_over(_many(13))
    grant = sf.grant_from_proposals(proposals)
    payload = _payloads(proposals)[0]
    gate = PreapprovedWriteGate({signature(payload["tool"], payload["params"])}, grant=grant)
    gate.authorize(payload, None)

    # Even with NOTHING changed, the exception does not apply.
    unchanged = _shadow_of(_many(13))
    assert cfg.evaluate(payload, unchanged["driving_behaviors"][0],
                        {cfg.PROVENANCE_KEY: gate.write_provenance(payload)})["verdict"] == (
        cfg.RADIUS_NOT_FULLY_NAMED
    )
    report = _preflight(payload, unchanged, gate)
    assert report.blocking is True
    assert (SHARED_OBJECT, "refuse") in _ids(report)
    assert (CONSENTED, "warn") not in _ids(report)

    # And the specific escape that was measured: the un-named tail explodes.
    exploded = _many(13)
    for consumer in _behavior(13)["referenced_by"]["consumers"][10:]:
        consumer["link_count"] = 5000
    assert _preflight(payload, _shadow_of(exploded), gate).blocking is True


def test_a_partial_radius_is_never_printed_as_if_it_were_the_whole_one():
    """BROKEN → FIXED. The decision's entire safety argument is the SENTENCE ("링크 M곳에
    함께 적용됩니다"), so M being a partial sum dressed as a total is not cosmetic. The
    phrase now reads as the lower bound it is, in every language and in the harness's own
    one-line form."""
    radius = cfg.consent_radius(_many(13))
    assert radius["complete"] is False
    assert radius["link_total"] == 145  # 10..19, the named ten
    assert "at least" in cfg.radius_phrase(radius)

    ko, en, ja = constraints._radius_phrases(radius)
    assert "이상" in ko and "at least" in en and "以上" in ja
    # A complete radius still says the plain number, with no hedge.
    complete = cfg.consent_radius(_behavior(4))
    assert complete["complete"] is True
    assert constraints._radius_phrases(complete)[1] == "4 consumer(s) · 46 link(s)"
    assert "at least" not in cfg.radius_phrase(complete)


def test_two_equally_blind_radii_do_not_add_up_to_consent():
    """BROKEN → FIXED. ``saturation_flow`` claimed a behaviour named without its reference
    graph "will NOT match a live shadow model", so the drift check would refuse. Measured:
    when the live model was ALSO reference-graph-less, the two empty radii compared EQUAL
    and the write was granted — announcing "참조 7곳 · 링크 0개" for an edit landing on
    seven consumers. Two blind sides agreeing is not consent."""
    bare = {"no": "2", "name": "Urban (motorized)", "car_following_model": 74,
            "reference_count": 7}
    proposals = _propose_over(bare)
    grant = sf.grant_from_proposals(proposals)
    payload = _payloads(proposals)[0]
    gate = PreapprovedWriteGate({signature(payload["tool"], payload["params"])}, grant=grant)
    gate.authorize(payload, None)

    report = _preflight(payload, {"driving_behaviors": [dict(bare)]}, gate)
    assert report.blocking is True
    assert (SHARED_OBJECT, "refuse") in _ids(report)
    assert (CONSENTED, "warn") not in _ids(report)


def test_the_completeness_gate_does_not_touch_the_case_the_exception_is_for():
    """The seal above must not quietly become "the exception never applies". A behaviour
    whose consumers are all named — the case DECISIONS.md 2026-08-19 actually decided on —
    is unaffected, at the cap boundary as well as below it."""
    for reference_count in (1, 4, 10):
        proposals = _propose_over(_many(reference_count))
        payload = _payloads(proposals)[0]
        gate = _approved_gate(proposals, sf.grant_from_proposals(proposals))
        gate.authorize(payload, None)
        report = _preflight(payload, _shadow_of(_many(reference_count)), gate)
        assert report.blocking is False, reference_count
        assert (CONSENTED, "warn") in _ids(report), reference_count

    # 11 is the first count the cap truncates, and the first the exception refuses.
    proposals = _propose_over(_many(11))
    payload = _payloads(proposals)[0]
    gate = _approved_gate(proposals, sf.grant_from_proposals(proposals))
    gate.authorize(payload, None)
    assert _preflight(payload, _shadow_of(_many(11)), gate).blocking is True


# ---------------------------------------------------------------------------
# 4. Minting — the grant has exactly one door, and it is narrow
# ---------------------------------------------------------------------------


def test_a_refusal_cannot_be_minted_into_a_grant():
    """The most direct attack on the whole design: take the builder's REFUSAL output —
    which is what you get with no measurement — and try to turn it into an authorization."""
    refusals = sf.propose_car_following_parameters(
        rung=_diagnosis(_report()), measurement=None, behavior=_behavior(4)
    )
    assert refusals[0].reason_code == sf.NO_SATURATION_FLOW_MEASUREMENT

    with pytest.raises(ValueError, match="complete set"):
        sf.grant_from_proposals(refusals)


def test_a_partial_set_cannot_be_minted_into_a_grant():
    """``propose_car_following_parameters``'s own docstring: a partially approved set
    "leaves the behaviour matching no published row". A grant over two of the three would
    authorize exactly that state, so minting refuses it."""
    proposals = _proposals()
    for partial in (proposals[:1], proposals[:2], proposals + proposals[:1]):
        with pytest.raises(ValueError, match="complete set"):
            sf.grant_from_proposals(partial)


def test_a_set_whose_writes_disagree_with_its_own_expected_state_cannot_be_minted():
    """``expected_after_apply`` is what ``verify_applied_row`` reads back afterwards. A
    grant whose writes do not land on it would authorize a set its own verifier would then
    call a partial application — so the two are cross-checked at mint time."""
    proposals = list(_proposals())
    tampered = proposals[0]
    tampered.params["value"] = 9.9

    with pytest.raises(ValueError, match="expected_after_apply"):
        sf.grant_from_proposals(tuple(proposals))


def test_a_set_carrying_no_accepted_measurement_cannot_be_minted():
    """Belt and braces on the builder's own gate: even if a proposal set were assembled
    with a measurement that did not pass, minting refuses rather than issuing an
    authorization backed by a number nobody stood behind."""
    proposals = list(_proposals())
    for proposal in proposals:
        proposal.evidence["measurement"] = {"ok": False, "reason_code": sf.MEASUREMENT_PROVENANCE_MISSING}

    with pytest.raises(ValueError, match="no accepted measurement"):
        sf.grant_from_proposals(tuple(proposals))


def test_the_grant_names_exactly_the_three_writes_and_nothing_else():
    proposals = _proposals()
    grant = sf.grant_from_proposals(proposals)

    assert len(grant.signatures) == 3
    for payload in _payloads(proposals):
        assert grant.covers(payload)
    assert set(grant.values) == set(cfg.GRANTED_PARAMETERS)
    # A different tool with byte-identical params is not covered: the tool name is part
    # of the canonicalization, so the grant cannot be borrowed by another verb.
    assert not grant.covers({"tool": "set_link_behavior_type_driving_behavior",
                             "params": _payloads(proposals)[0]["params"]})


# ---------------------------------------------------------------------------
# 5. The card stage exists, and nothing can be written from it
# ---------------------------------------------------------------------------


def test_the_approval_card_stage_is_non_blocking_so_the_human_is_actually_asked():
    """DECISIONS 2026-08-18's dead end: the orchestrator refuses instead of showing a card
    when the approval-time preflight blocks, so a guardrail that fired there meant the
    human was never asked at all. The card-stage gate carries the grant for exactly this
    reason."""
    proposals = _proposals()
    report = _preflight(_payloads(proposals)[0], _shadow(4), ProposeWriteGate(grant=sf.grant_from_proposals(proposals)))

    assert report.blocking is False
    assert (CONSENTED, "warn") in _ids(report)


def test_nothing_can_dispatch_from_the_card_stage():
    """The card stage's leniency is safe because of a structural property, not a promise:
    ``ProposeWriteGate.authorize`` never returns a verdict at all. Pinned here so the
    leniency and the thing that makes it safe cannot drift apart."""
    proposals = _proposals()
    gate = ProposeWriteGate(grant=sf.grant_from_proposals(proposals))

    for payload in _payloads(proposals):
        assert gate.write_provenance(payload)["consent"] == cfg.AWAITING_APPROVAL
        with pytest.raises(ApprovalRequired):
            gate.authorize(payload, None)


def test_the_dispatch_stage_reports_a_consumed_click_not_a_pending_one():
    proposals = _proposals()
    payload = _payloads(proposals)[0]
    gate = _approved_gate(proposals)

    assert gate.write_provenance(payload)["consent"] == cfg.AWAITING_APPROVAL
    gate.authorize(payload, None)
    assert gate.write_provenance(payload)["consent"] == cfg.CONSUMED_APPROVAL


def test_an_unrecognised_consent_state_refuses():
    """``NOT_CONSENTED`` is reachable, not decorative: a grant is never self-sufficient,
    so provenance carrying a real grant but no consent the gates could have produced is
    refused like any ungranted write."""
    proposals = _proposals()
    payload = _payloads(proposals)[0]
    verdict = cfg.evaluate(
        payload,
        _behavior(4),
        {cfg.PROVENANCE_KEY: {"kind": cfg.PROVENANCE_KIND,
                              "grant": sf.grant_from_proposals(proposals),
                              "consent": "APPROVED_TRUST_ME"}},
    )
    assert verdict["verdict"] == cfg.NOT_CONSENTED

    findings = constraints.run_checks(
        "preflight",
        payload,
        _shadow(4),
        {cfg.PROVENANCE_KEY: {"kind": cfg.PROVENANCE_KIND,
                              "grant": sf.grant_from_proposals(proposals),
                              "consent": "APPROVED_TRUST_ME"}},
    )
    assert SHARED_OBJECT in {f.constraint_id for f in findings}


def test_every_verdict_code_is_reachable():
    """A verdict nobody can reach is a comment. Each of the six is produced here by the
    condition it names."""
    proposals = _proposals()
    payload = _payloads(proposals)[0]
    grant = sf.grant_from_proposals(proposals)
    truncated_grant = sf.grant_from_proposals(
        sf.propose_car_following_parameters(
            rung=_diagnosis(_report()),
            measurement=_measurement(),
            behavior=_behavior(4, truncated=True),
        )
    )

    def _provenance(consent: str = cfg.CONSUMED_APPROVAL) -> dict[str, Any]:
        return {cfg.PROVENANCE_KEY: {"kind": cfg.PROVENANCE_KIND, "grant": grant, "consent": consent}}

    reached = {
        # RADIUS_NOT_FULLY_NAMED: consented and current agree exactly, and both are
        # partial pictures — nothing drifted, there was just never a whole radius.
        cfg.evaluate(
            payload,
            _behavior(4, truncated=True),
            {cfg.PROVENANCE_KEY: {"kind": cfg.PROVENANCE_KIND, "grant": truncated_grant,
                                  "consent": cfg.CONSUMED_APPROVAL}},
        )["verdict"],
        cfg.evaluate(payload, _behavior(4), {})["verdict"],
        cfg.evaluate(
            {"tool": "set_driving_behavior_parameter",
             "params": {"behavior_no": "2", "parameter": "ax", "value": 9.9}},
            _behavior(4),
            _provenance(),
        )["verdict"],
        cfg.evaluate(payload, _behavior(4), _provenance("nonsense"))["verdict"],
        cfg.evaluate(payload, _behavior(5), _provenance())["verdict"],
        cfg.evaluate(payload, _behavior(4), _provenance())["verdict"],
    }
    assert reached == set(cfg.VERDICTS)

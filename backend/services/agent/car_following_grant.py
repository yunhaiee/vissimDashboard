"""Unit #44-b — the ONE narrow exception to ``driving_behavior_shared_object_edit``.

Why this module exists (the decision it implements)
---------------------------------------------------
``docs/phase2/DECISIONS.md``, **2026-08-19** ("사용자 결정: #44 가드레일 충돌은 (a) 좁은
예외로 해소한다"), quoted because the exception must be able to explain itself where it
lives:

    ``driving_behavior_shared_object_edit`` 가드레일에 **Table 5-6 차량추종 제안 경로에
    한정된** 예외를 낸다. 근거: ask-first 승인 카드가 적용 범위를 문장으로 명시하고
    ("행태 N을 참조하는 링크 M곳에 함께 적용됩니다") 사람이 읽고 승인하는 구조이므로,
    이는 가드레일이 막으려던 "모르고 저지르는 광역 편집"이 아니라 "알고 승인하는 광역
    편집"이다. 가드레일의 원래 목적(의도치 않은 공유 객체 편집 방지)은 예외 밖 전
    경로에서 그대로 유지된다.

The collision it resolves is recorded the day before (DECISIONS 2026-08-18, #44): MRWA
§5.2.5.1 calibrates the urban driving behaviour NETWORK-WIDE from one measured stop-line
saturation flow, while the guardrail refuses any in-place edit of a behaviour with
``reference_count > 0`` because its premise is a LOCAL intent. Every W74 behaviour in the
real consultant model is referenced at least once, so the guardrail refused exactly the
edit the guideline prescribes.

The key is STRUCTURE, not a flag
--------------------------------
This repository has twice shipped a guard that a caller could talk its way past by
asserting compliance, and has twice had to rewrite it:

* ``constraints._check_driving_behavior_shared_object_edit`` — the very guardrail this
  module makes an exception to — originally trusted a caller-supplied
  ``duplicated_before_edit`` boolean, "a self-reported flag any planner call could simply
  always set ``True``, giving zero real protection" (its own section comment).
* ``saturation_flow._authoritative_measurement`` — ``propose_car_following_parameters``
  read ``measurement["ok"]`` and believed it, so every data-quality gate was opt-in.

So the exception here is deliberately NOT a field on the action, NOT a flag in the params,
and NOT a claim any payload can make about itself. It is a :class:`CarFollowingGrant`:

1. **Minted only from a real proposal.** ``saturation_flow.grant_from_proposals`` is the
   only minting path, and it accepts only a complete, ``PROPOSAL``-outcome set of three
   (``ax``/``bx_add``/``bx_mult``) sharing one ``set_id`` — which
   ``propose_car_following_parameters`` emits only after the CAPACITY_LIMIT rung, a
   provenance-checked OBSERVED measurement and a real Table 5-6 row have all been
   established. There is no constructor that takes "trust me".
2. **It names the writes byte-exactly.** :attr:`CarFollowingGrant.signatures` holds the
   three ``gates.signature`` canonicalizations of the approved actions — the same
   canonicalization ``PreapprovedWriteGate`` and ``pending_approval.json`` already agree
   on. A payload that changes the behaviour number, the parameter, or the value by any
   amount produces a different signature and is simply not covered. That single mechanism
   is what confines the exception to ``ax``/``bx_add``/``bx_mult`` at the table's own
   values: the other five whitelisted driving-behaviour parameters and every W99 CC0–CC9
   name are unreachable because no grant ever names them.
3. **It reaches the constraint engine through a channel the planner cannot write to.**
   The grant travels on the write GATE (``services.agent.gates``), and the orchestrator
   hands the gate's own provenance to ``GuardrailEngine.preflight`` as ``extra``. The
   planner only ever emits ``{"tool", "params"}``; it has no way to put anything into
   ``extra``, and no Python-level call is available to it at all. A direct tool call
   therefore arrives with no provenance and is refused exactly as before.
4. **A human's click is what turns it into a dispatch.** ``PreapprovedWriteGate`` reports
   :data:`CONSUMED_APPROVAL` for an action only after its own one-shot ``authorize()``
   granted that action's approved signature. A second attempt at the same write never
   reaches preflight at all — the gate refuses it first — so approval reuse stays
   refused without this module doing anything.

The drift check: consent is to a NAMED radius
---------------------------------------------
The decision's whole safety argument is that a human read a sentence naming what the edit
touches. That argument only holds if the radius at APPLY time is the radius that was
named at APPROVAL time. :func:`consent_radius` fingerprints the behaviour's reference
graph — the real (untruncated) reference count, every named consumer as
``(link_behavior_type_no, veh_class_no, link_count)``, and the total link count — and
:func:`evaluate` compares the fingerprint carried on the grant against one re-derived
from the live shadow model at apply time. Different → :data:`RADIUS_DRIFTED`, a named
refusal ("동의는 N개 링크에 대한 것이었는데 지금은 M개"), never a silent pass.

``tools_network._bounded_driving_behavior_consumers`` caps the NAMED consumer list at 10
(the count is always real and untruncated), and a caller may name a behaviour by
``reference_count`` alone. Either way the fingerprint then describes less than the
behaviour actually reaches — and ``link_total`` sums only what was named, so an un-named
consumer's links can change by any amount without moving it. That is not a limit the
exception can absorb: it breaks both halves of the decision at once (the sentence the
human read understates the radius, and the drift check cannot watch the part it never
named). So :func:`consent_radius` records whether the picture is ``complete``, and
:func:`evaluate` returns :data:`RADIUS_NOT_FULLY_NAMED` — refusing — when it is not. What
survives is the case where every consumer is named, which is where the human could
actually read the sentence the decision rests on.

This module is pure data and pure functions: no I/O, no COM, no state. It imports only
``gates.signature`` so that the one canonicalization the approval machinery already uses
is not duplicated here — ``constraints.py`` is stdlib-only by policy and this keeps it so
(``gates`` itself imports nothing but ``json`` at runtime).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from services.agent.gates import AWAITING_APPROVAL, CONSUMED_APPROVAL, signature

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

#: The only tool a grant can ever authorize past the shared-object guardrail.
GRANT_TOOL = "set_driving_behavior_parameter"

#: The only three parameters §5.2.5.1 names, and therefore the only three a grant can
#: cover. Enforced structurally (a grant's signatures name exactly these three writes at
#: exactly the table's values), not by re-checking this tuple at apply time — it is here
#: so the minting side can refuse to build a grant over anything else, and so the limit
#: is readable in one place. The other five entries of
#: ``vissim_com.DRIVING_BEHAVIOR_PARAM_FIELDS`` and every W99 ``cc*`` name are outside the
#: exception: on a shared behaviour they are refused exactly as before.
GRANTED_PARAMETERS = ("ax", "bx_add", "bx_mult")

#: The key :func:`evaluate` reads out of the constraint engine's ``extra`` dict. Written
#: only by ``orchestrator.py`` from the write gate's own ``write_provenance``; absent on
#: every other path, including every direct ``constraints.run_checks`` call.
PROVENANCE_KEY = "approval_provenance"

#: ``provenance["kind"]`` — namespaces the payload so a future gate extension carrying
#: some other kind of provenance can never be mistaken for this one.
PROVENANCE_KIND = "table_5_6_car_following"

# ---------------------------------------------------------------------------
# Consent states — what the GATE says about the human's click
#
# Defined in ``gates`` (the module that reports them; this one imports it for
# :func:`signature`, so the dependency can only run that way) and re-exported here so a
# reader of the exception does not have to go looking:
#
# * :data:`AWAITING_APPROVAL` — the approval card is being built. The gate paused the turn
#   and nothing can dispatch through it (``ProposeWriteGate.authorize`` never returns a
#   verdict — it always raises ``ApprovalRequired``). The exception applies here so the
#   card can actually be SHOWN with its radius sentence; a preflight refusal at this
#   moment would mean the human is never asked, which is the dead end DECISIONS
#   2026-08-18 recorded and 2026-08-19 decided to end.
# * :data:`CONSUMED_APPROVAL` — a human clicked: ``PreapprovedWriteGate.authorize``
#   matched this exact action against the signature set persisted at proposal time and
#   CONSUMED it. The only state a write actually dispatches in.
# ---------------------------------------------------------------------------

CONSENT_STATES = (AWAITING_APPROVAL, CONSUMED_APPROVAL)

# ---------------------------------------------------------------------------
# Verdicts — what :func:`evaluate` tells the constraint engine
# ---------------------------------------------------------------------------

#: No grant reached the engine at all: a direct tool call, a plain planner write, any
#: ``constraints.run_checks``/``fixes.preflight_findings`` call. The guardrail refuses,
#: byte-for-byte as it did before this unit.
NO_GRANT = "NO_GRANT"

#: A grant is present but does not name THIS write. A forged or drifted payload — a
#: different behaviour, a different parameter, a value that is not the table's, a W99
#: ``cc*`` name. Refused identically to :data:`NO_GRANT`; the exception is not a mood the
#: engine gets into, it is a per-action match.
NOT_COVERED = "NOT_COVERED"

#: The grant names this write and the radius still matches, but the gate reports no
#: consent state this module recognises. Refused — a grant is never self-sufficient.
NOT_CONSENTED = "NOT_CONSENTED"

#: The grant names this write, but the blast radius re-derived from the live shadow model
#: differs from the one named when consent was given. Refused BY NAME.
RADIUS_DRIFTED = "RADIUS_DRIFTED"

#: The radius did not move — but it was never a whole picture to begin with, so
#: "it did not move" is a claim about only the part that was named
#: (``consent_radius(...)["complete"] is False``). Refused, and NOT by a new name: the
#: original ``driving_behavior_shared_object_edit`` refusal simply never stands down,
#: which is exactly the pre-#44-b behaviour and already explains itself in three
#: languages. The exception's premise is that a human read where the edit lands; here
#: that sentence could not be written truthfully, so there is nothing to have read.
RADIUS_NOT_FULLY_NAMED = "RADIUS_NOT_FULLY_NAMED"

#: Every structural condition holds. The shared-object refusal steps aside and a warn-tier
#: finding names the consented radius instead.
GRANTED = "GRANTED"

VERDICTS = (
    NO_GRANT,
    NOT_COVERED,
    NOT_CONSENTED,
    RADIUS_DRIFTED,
    RADIUS_NOT_FULLY_NAMED,
    GRANTED,
)


# ---------------------------------------------------------------------------
# The consented blast radius, canonicalized
# ---------------------------------------------------------------------------


def consent_radius(behavior: Mapping[str, Any] | None) -> dict[str, Any]:
    """Fingerprint one driving behaviour's reference graph — the radius a human consents to.

    Reads the shape ``tools_network._parse_driving_behaviors`` publishes:
    ``reference_count`` (the real, untruncated total) plus
    ``referenced_by = {count, consumers: [{link_behavior_type_no, link_behavior_type_name,
    veh_class_no, link_count}], truncated}``.

    Canonical and order-insensitive: consumers are reduced to
    ``[link_behavior_type_no, veh_class_no, link_count]`` triples and sorted, so a parser
    that re-orders its output is not mistaken for a network that changed. The display
    NAME is deliberately excluded — renaming a link behaviour type does not move a single
    link, and treating it as drift would refuse a correct edit.

    Returns a plain JSON-shaped dict so a grant can be persisted alongside
    ``pending_approval.json`` without a custom encoder. Its ``complete`` flag says
    whether this is the WHOLE reference graph or only the part the shadow model named;
    ``link_total`` is a lower bound whenever it is not, and :func:`evaluate` refuses
    rather than GRANTing on a radius that was never whole.
    """
    payload = behavior if isinstance(behavior, Mapping) else {}
    referenced_by = payload.get("referenced_by")
    referenced_by = referenced_by if isinstance(referenced_by, Mapping) else {}

    consumers: list[list[Any]] = []
    for entry in referenced_by.get("consumers") or ():
        if not isinstance(entry, Mapping):
            continue
        veh_class_no = entry.get("veh_class_no")
        consumers.append(
            [
                str(entry.get("link_behavior_type_no") or ""),
                "" if veh_class_no in (None, "") else str(veh_class_no),
                _int(entry.get("link_count")),
            ]
        )
    consumers.sort(key=lambda item: (item[0], item[1], item[2]))

    reference_count = payload.get("reference_count")
    if reference_count is None:
        reference_count = referenced_by.get("count")
    reference_count = _int(reference_count)
    truncated = bool(referenced_by.get("truncated"))

    return {
        "reference_count": reference_count,
        "consumers": consumers,
        "truncated": truncated,
        # SUM OVER THE NAMED CONSUMERS ONLY. When ``complete`` is False this is a
        # LOWER BOUND, never the total -- see below, and see every caller that
        # prints it (they must say "이상"/"at least", not a bare number).
        "link_total": sum(item[2] for item in consumers),
        # Is this radius a whole picture, or only part of one? (refuter, #44-b)
        #
        # ``tools_network._bounded_driving_behavior_consumers`` caps the NAMED
        # consumer list at 10 while keeping ``count`` real, and a caller can hand
        # in a behaviour that carries ``reference_count`` with no ``referenced_by``
        # at all. In BOTH cases the fields above describe less than the behaviour
        # actually reaches: ``link_total`` sums only what was named, so an un-named
        # consumer's link count can change by any amount -- measured: 13 consumers,
        # tail link counts 20/21/22 -> 5000 each, real total 208 -> 15145 -- without
        # moving one byte of this fingerprint. Both halves of the decision break
        # there: the sentence the human reads understates the blast radius (145 vs a
        # real 208), and the drift check cannot see the part it never named.
        #
        # So completeness is recorded rather than assumed, and :func:`evaluate`
        # refuses to return GRANTED without it. The exception exists because a human
        # read WHERE the edit lands; where that cannot be stated in full, there is
        # nothing to have read and the original guardrail refuses as it always did.
        "complete": not truncated and len(consumers) == reference_count,
    }


def radius_phrase(radius: Mapping[str, Any] | None) -> str:
    """``"3 consumer(s) / 47 link(s)"`` — the radius as one comparable, printable clause.

    An INCOMPLETE radius (see :func:`consent_radius`) is printed as a lower bound and
    says so: its link total covers only the consumers that were actually named.
    """
    payload = radius if isinstance(radius, Mapping) else {}
    reference_count = _int(payload.get("reference_count"))
    link_total = _int(payload.get("link_total"))
    if payload.get("complete"):
        return f"{reference_count} consumer(s) / {link_total} link(s)"
    return (
        f"{reference_count} consumer(s) / at least {link_total} link(s) "
        "(named consumers only — the full list could not be read)"
    )


# ---------------------------------------------------------------------------
# The grant
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CarFollowingGrant:
    """One approved Table 5-6 row's three writes, and the radius they were consented to.

    Constructed only by ``saturation_flow.grant_from_proposals`` (see this module's
    docstring for why there is no "trust me" constructor). Frozen because a grant that
    could be widened after the human read it would defeat its own purpose.

    ``signatures`` is what makes the exception a per-action match rather than a mode: the
    three ``gates.signature`` canonicalizations of the exact writes the proposal emitted.
    """

    #: ``saturation_flow``'s own set identity: key:node:behaviour:row.
    set_id: str
    #: The driving behaviour the three writes target, cross-checked at apply time.
    behavior_no: str
    #: ``{"ax": 2.5, "bx_add": 3.5, "bx_mult": 3.25}`` — for the message, not the match.
    values: Mapping[str, float]
    #: The Table 5-6 row's own saturation flow, in pcu.
    saturation_flow_pcu: float
    #: Who measured it, how, and when — carried so a refusal or a warning can cite the
    #: measurement that justified the edit, not merely assert one existed.
    measurement: Mapping[str, Any]
    #: :func:`consent_radius` at proposal time. The drift check's left-hand side.
    consent_radius: Mapping[str, Any]
    #: The three canonical write signatures this grant authorizes, and nothing else.
    signatures: frozenset[str]

    @property
    def kind(self) -> str:
        """:data:`PROVENANCE_KIND`. Read by ``gates._write_provenance`` so that module
        never has to know what kinds of grant exist."""
        return PROVENANCE_KIND

    def covers(self, action: Mapping[str, Any] | None) -> bool:
        """Does this grant name THIS exact write?

        Tool, behaviour number, parameter name and value all have to match, and they are
        checked as one: the action's canonical signature must be one of the three the
        proposal emitted. A payload that differs anywhere — a different behaviour, a
        sixth-decimal-place value, ``cc0`` instead of ``ax`` — canonicalizes differently
        and is not covered.
        """
        payload = action if isinstance(action, Mapping) else {}
        if payload.get("tool") != GRANT_TOOL:
            return False
        params = payload.get("params")
        params = dict(params) if isinstance(params, Mapping) else {}
        return signature(GRANT_TOOL, params) in self.signatures

    def to_dict(self) -> dict[str, Any]:
        """JSON-shaped, for traces and for persistence beside ``pending_approval.json``."""
        return {
            "kind": PROVENANCE_KIND,
            "set_id": self.set_id,
            "behavior_no": self.behavior_no,
            "values": dict(self.values),
            "saturation_flow_pcu": self.saturation_flow_pcu,
            "measurement": dict(self.measurement),
            "consent_radius": dict(self.consent_radius),
            "signatures": sorted(self.signatures),
        }


def build_grant(
    *,
    set_id: str,
    behavior_no: str,
    values: Mapping[str, float],
    saturation_flow_pcu: float,
    measurement: Mapping[str, Any],
    consent_radius_payload: Mapping[str, Any],
    signatures: Iterable[str],
) -> CarFollowingGrant:
    """Freeze a grant. Called by ``saturation_flow.grant_from_proposals``, nowhere else.

    Kept here rather than in ``saturation_flow`` so the dataclass and its one constructor
    live together, but deliberately NOT given any validation of its own beyond shape: the
    thing that makes a grant trustworthy is that the only caller derives every field from
    a completed proposal set, and duplicating half of that check here would invite the
    belief that this function is safe to call from anywhere. It is not.
    """
    return CarFollowingGrant(
        set_id=str(set_id),
        behavior_no=str(behavior_no),
        values={str(k): float(v) for k, v in dict(values).items()},
        saturation_flow_pcu=float(saturation_flow_pcu),
        measurement=dict(measurement),
        consent_radius=dict(consent_radius_payload),
        signatures=frozenset(str(s) for s in signatures),
    )


# ---------------------------------------------------------------------------
# The verdict the constraint engine acts on
# ---------------------------------------------------------------------------


def provenance_from(extra: Mapping[str, Any] | None) -> dict[str, Any]:
    """The gate-written provenance payload out of a constraint ``extra`` dict, or ``{}``.

    Defensive on every field: this returns ``{}`` for anything that is not a mapping
    carrying our own :data:`PROVENANCE_KIND`, so an unrelated future ``extra`` key, a
    half-built payload, or a gate that does not implement the optional protocol all
    degrade to "no grant" — which is the pre-#44-b behaviour, i.e. refuse.
    """
    payload = extra if isinstance(extra, Mapping) else {}
    provenance = payload.get(PROVENANCE_KEY)
    if not isinstance(provenance, Mapping):
        return {}
    if provenance.get("kind") != PROVENANCE_KIND:
        return {}
    if not isinstance(provenance.get("grant"), CarFollowingGrant):
        return {}
    return dict(provenance)


def evaluate(
    action: Mapping[str, Any] | None,
    behavior: Mapping[str, Any] | None,
    extra: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Structural verdict for one proposed write against one live behaviour.

    ``behavior`` is the shadow model's OWN entry for the target, re-read at apply time —
    the drift check's right-hand side is never taken from the action or the grant.

    Never raises: every failure mode collapses to a verdict that refuses. Returns
    ``{"verdict", "grant", "consent", "consented_radius", "current_radius"}``; only
    :data:`GRANTED` lets the shared-object refusal step aside.
    """
    provenance = provenance_from(extra)
    if not provenance:
        return _verdict(NO_GRANT)

    grant: CarFollowingGrant = provenance["grant"]
    if not grant.covers(action):
        return _verdict(NOT_COVERED, grant=grant, consent=provenance.get("consent"))

    consent = provenance.get("consent")
    if consent not in CONSENT_STATES:
        return _verdict(NOT_CONSENTED, grant=grant, consent=consent)

    current = consent_radius(behavior)
    consented = dict(grant.consent_radius)
    if current != consented:
        return _verdict(
            RADIUS_DRIFTED,
            grant=grant,
            consent=consent,
            consented_radius=consented,
            current_radius=current,
        )

    # Equal — but equal to WHAT? A radius that never named everything the behaviour
    # reaches cannot support either half of the decision (the sentence the human read,
    # and the promise that the radius has not moved since). Checked after the drift
    # compare on purpose: when the two genuinely differ, "그 반경이 바뀌었다" is the more
    # specific and more useful refusal, and it quotes real numbers on both sides.
    if not (consented.get("complete") and current.get("complete")):
        return _verdict(
            RADIUS_NOT_FULLY_NAMED,
            grant=grant,
            consent=consent,
            consented_radius=consented,
            current_radius=current,
        )

    return _verdict(
        GRANTED,
        grant=grant,
        consent=consent,
        consented_radius=consented,
        current_radius=current,
    )


def _verdict(
    verdict: str,
    *,
    grant: CarFollowingGrant | None = None,
    consent: Any = None,
    consented_radius: Mapping[str, Any] | None = None,
    current_radius: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "verdict": verdict,
        "grant": grant,
        "consent": consent if consent in CONSENT_STATES else None,
        "consented_radius": dict(consented_radius) if consented_radius else None,
        "current_radius": dict(current_radius) if current_radius else None,
    }


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


__all__ = [
    "AWAITING_APPROVAL",
    "CONSENT_STATES",
    "CONSUMED_APPROVAL",
    "CarFollowingGrant",
    "GRANTED",
    "GRANTED_PARAMETERS",
    "GRANT_TOOL",
    "NOT_CONSENTED",
    "NOT_COVERED",
    "NO_GRANT",
    "PROVENANCE_KEY",
    "PROVENANCE_KIND",
    "RADIUS_DRIFTED",
    "RADIUS_NOT_FULLY_NAMED",
    "VERDICTS",
    "build_grant",
    "consent_radius",
    "evaluate",
    "provenance_from",
    "radius_phrase",
]

"""Unit #51-b — the SECOND narrow exception to ``driving_behavior_shared_object_edit``.

Why a second grant species rather than a wider first one
--------------------------------------------------------
``docs/phase2/DECISIONS.md``, **2026-08-20** ("사용자 결정: #51 탐색 산출물에도 가드레일
예외를 (a) 확장한다"), quoted because an exception must be able to explain itself where
it lives:

    열쇠는 구조: grant는 **완료된 탐색**(승자 벡터 + 원장 + ``HOLDOUT_PASSED`` + 전제조건
    기록)에서만 발급. 서명이 행태·파라미터·값을 바이트 고정, 1회 소모 승인 게이트 결속,
    반경 전부 명명 가능할 때만(캡 초과 행태는 여전히 거부).
    […] ``CarFollowingGrant``의 검증 약화 금지 — 별도 grant 종(또는 가산적 일반화)으로,
    Table 5-6 경로와 서명 공간이 섞이지 않게.

The collision is #51's own (``parameter_search``'s module docstring §5): every parameter
the search moves is written with ``set_driving_behavior_parameter``, every W74 behaviour
in a real consultant model is referenced at least once, and #44-b's
:class:`~services.agent.car_following_grant.CarFollowingGrant` covers
``ax``/``bx_add``/``bx_mult`` at Table 5-6's own values BY SIGNATURE — on purpose, so it
cannot reach here.

So this module is a separate species, not a relaxation of that one:

* a different :data:`PROVENANCE_KIND`, and
* a different dataclass, which ``provenance_from`` checks by ``isinstance``.

Both discriminators have to agree before either species' ``evaluate`` will look at a
payload, so a Table 5-6 grant can never authorize a searched write and a searched grant
can never authorize a Table 5-6 write — proved both ways in
``test_agent_searched_behaviour_grant.py``. ``car_following_grant.py`` is not modified by
this unit.

What IS shared, and why sharing it is the safer choice
------------------------------------------------------
:func:`consent_radius` and :func:`radius_phrase` are re-exported from
``car_following_grant`` rather than re-implemented. The blast-radius fingerprint is a
property of the MODEL (which link-behavior-types point at a driving behaviour, and how
many links each carries), not of a grant species; two implementations of it would be two
things to keep in step, and this repository's own record is that the copy drifts. The
drift check is the entire safety argument of both decisions, so it gets exactly one
implementation. Sharing a pure fingerprint function mixes no signature space.

The key is STRUCTURE, and here the structure is a FINISHED SEARCH
-----------------------------------------------------------------
#44-b's key is "a complete, measurement-backed Table 5-6 proposal set". This one's is
"a finished, held-out, ledgered search", and the only minting path is
:func:`services.agent.parameter_search.grant_from_search`, which refuses unless all of
the following are real AND mutually consistent (see there — every one of them is a
``ValueError``, never a silently narrower grant):

1. the search ``status`` is ``COMPLETE`` — a partial search's best row is a snapshot of
   an interrupted process, not a winner;
2. the acceptance is ``HOLDOUT_PASSED`` **re-derived from the ledger**, not read off the
   ``code`` field: the two ``HOLDOUT`` rows must be there, at a seed the search never
   used, scored on exactly the same held-out nodes as each other, and the winner must
   really beat the baseline on them;
3. the calibration/holdout split re-derives to the one ``split_nodes`` produces from the
   two sides' union (the #51 refuter's wall — a holdout a caller may choose is not one),
   and no calibration row scored a held-out node;
4. the winner's vector is inside the published MRWA bands, names only searched
   parameters, and is exactly the vector the acceptance ran;
5. the proposed writes are exactly the parameters that MOVED from the baseline — a
   partial application of a searched vector is a different vector, and would carry none
   of the evidence the ledger holds.

Everything after that is #44-b's shape, deliberately: the grant names the writes by
canonical ``gates.signature``, it reaches the engine only through the write gate's
``extra`` channel, it needs a consent state from that gate, it needs the radius to be
nameable in FULL, and it needs that radius to still equal the one consent was given for.

The one thing this species checks that #44-b's does not
-------------------------------------------------------
**The published band is re-checked on every arrival** (:func:`evaluate`). #44-b's values
are a table LOOKUP — a row either is Table 5-6's or is not, and the signature settles it.
A searched value is a value this product CHOSE inside a band, so the band is the whole of
what makes it defensible, and the #51 refuter's finding was precisely that a range
checked only where a value is generated is not checked at all (a resumed ledger put
``lat_dist_stand_def = 0.05`` against a published 0.1–0.4 into a proposal). Here that wall
is repeated at the last possible moment: :data:`VALUE_OUT_OF_PUBLISHED_RANGE` and
:data:`PARAMETER_NOT_SEARCHABLE` are refusals by name, read from
``mrwa_reference.merging_behaviour_range`` — the same source ``parameter_search``'s own
bands come from, never a copy.

The evidence is a RANK BELOW Table 5-6, and says so
---------------------------------------------------
DECISIONS.md 2026-08-20: "근거의 격이 Table 5-6(공표 표 조회)보다 한 단계 낮다는 사실은
근거문이 스스로 말한다 — 탐색 원장과 홀드아웃 증명이 그 자리를 채우는 것이 이 결정의
본질." :data:`EVIDENCE_RANK` is that sentence in three languages; the grant carries it,
``parameter_search.rationale`` prints it, and the constraint that announces this
exception prints it too. An exception whose own message overstated its evidence would be
worse than no exception.

Pure data and pure functions: no I/O, no COM, no state. Imports only ``gates`` (for the
one canonicalization the approval machinery already uses), ``mrwa_reference`` (for the
published bands) and ``car_following_grant`` (for the one radius fingerprint), plus
stdlib ``copy`` — see :func:`build_grant` for what a shallow copy cost.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from services.agent import mrwa_reference
from services.agent.car_following_grant import (
    PROVENANCE_KEY as _APPROVAL_PROVENANCE_KEY,
    consent_radius,
    radius_phrase,
)
from services.agent.gates import AWAITING_APPROVAL, CONSUMED_APPROVAL, signature

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

#: The only tool a grant of this species can ever authorize past the shared-object
#: guardrail — the one verb every searched parameter is written with.
GRANT_TOOL = "set_driving_behavior_parameter"

#: The parameters this exception can reach: exactly those for which op §5.2.5.1 p.188
#: publishes a band AND this codebase has a write verb, DERIVED from the reference pack
#: rather than retyped. That is the same rule ``parameter_search.search_space()`` applies
#: to build the search space, read from the same rows, so "what was searched" and "what
#: the exception covers" cannot drift into two different lists (a test pins the two
#: against each other). The cooperative-braking bullet is excluded here for the reason it
#: is excluded there: it carries a band but no Vissim write verb, so it is keyed to
#: ``None`` in :data:`mrwa_reference.MERGING_BEHAVIOUR_ROWS`.
GRANTED_PARAMETERS: tuple[str, ...] = tuple(
    parameter for parameter, *_rest in mrwa_reference.MERGING_BEHAVIOUR_ROWS if parameter
)

#: ``provenance["kind"]`` — the first of the two discriminators that keep this species'
#: signature space separate from #44-b's. ``car_following_grant.provenance_from`` requires
#: ``"table_5_6_car_following"`` and an actual ``CarFollowingGrant`` instance, so a
#: payload carrying THIS kind is invisible to it, and vice versa.
PROVENANCE_KIND = "searched_behaviour_parameters"

#: Deliberately the SAME ``extra`` key #44-b uses (imported, not retyped). The key names
#: the CHANNEL — "what the approval machinery knows about this write" — and
#: ``orchestrator._preflight_extra`` writes exactly one provenance payload per action from
#: the gate that carried it. Two keys would mean two channels to keep honest, and would
#: have required editing the orchestrator; one key with two kinds means the discriminator
#: is where the type check already is.
PROVENANCE_KEY = _APPROVAL_PROVENANCE_KEY

# ---------------------------------------------------------------------------
# Consent states — what the GATE says about the human's click. Same two states
# #44-b uses, re-exported from ``gates`` so both species read one definition.
# ---------------------------------------------------------------------------

CONSENT_STATES = (AWAITING_APPROVAL, CONSUMED_APPROVAL)

# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

#: No grant of this species reached the engine: a direct tool call, a plain planner
#: write, any ``constraints.run_checks``/``fixes.preflight_findings`` call — and every
#: Table 5-6 grant, which is a different kind carrying a different type. The guardrail
#: refuses byte-for-byte as it did before this unit.
NO_GRANT = "NO_GRANT"

#: A grant is present but does not name THIS write. A different behaviour, a different
#: parameter, a value that is not the accepted winner's. Refused identically to
#: :data:`NO_GRANT` — the exception is a per-action match, never a mode.
NOT_COVERED = "NOT_COVERED"

#: The action names a driving-behaviour parameter the guideline publishes no band for —
#: every W99 ``cc*`` name, ``ax``/``bx_add``/``bx_mult`` (Table 5-6's, whose exception is
#: #44-b's and whose values are measured, not searched), and the two whitelisted
#: parameters §5.2.5.1 lists no range for. Unreachable through a minted grant; named
#: rather than folded into :data:`NOT_COVERED` so a refusal says which wall was hit.
PARAMETER_NOT_SEARCHABLE = "PARAMETER_NOT_SEARCHABLE"

#: The parameter is searchable but the arriving VALUE is outside its published band.
#: The band is the entire licence for a searched number, so it is re-read from the
#: reference pack at every arrival rather than trusted from mint time.
VALUE_OUT_OF_PUBLISHED_RANGE = "VALUE_OUT_OF_PUBLISHED_RANGE"

#: The grant names this write and the radius still matches, but the gate reports no
#: consent state this module recognises. Refused — a grant is never self-sufficient.
NOT_CONSENTED = "NOT_CONSENTED"

#: The blast radius re-derived from the live shadow model differs from the one named when
#: consent was given. Refused BY NAME.
RADIUS_DRIFTED = "RADIUS_DRIFTED"

#: The radius did not move, but it was never a whole picture (a truncated or absent
#: consumer list), so "it did not move" is a claim about only the part that was named.
#: Refused, and NOT by a new name: the original ``driving_behavior_shared_object_edit``
#: refusal simply never stands down, exactly as in #44-b.
RADIUS_NOT_FULLY_NAMED = "RADIUS_NOT_FULLY_NAMED"

#: Every structural condition holds. The shared-object refusal steps aside and a warn-tier
#: finding names the consented radius and the search behind it instead.
GRANTED = "GRANTED"

VERDICTS = (
    NO_GRANT,
    NOT_COVERED,
    PARAMETER_NOT_SEARCHABLE,
    VALUE_OUT_OF_PUBLISHED_RANGE,
    NOT_CONSENTED,
    RADIUS_DRIFTED,
    RADIUS_NOT_FULLY_NAMED,
    GRANTED,
)

# ---------------------------------------------------------------------------
# The sentence that keeps the evidence honest
# ---------------------------------------------------------------------------

#: DECISIONS.md 2026-08-20's closing paragraph, as text the product prints. Carried on
#: every grant and printed by every consumer: this value's evidence is one rank BELOW
#: Table 5-6's, and what fills that place is the ledger and the holdout.
EVIDENCE_RANK = {
    "ko": (
        "이 값의 근거는 Table 5-6(공표된 표를 측정값으로 조회)보다 한 단계 낮습니다 — 측정된 "
        "값이 아니라 공표 범위 안에서 탐색해 고른 값입니다. 그 자리를 채우는 것은 탐색 원장과 "
        "홀드아웃 증명입니다(DECISIONS.md 2026-08-20)."
    ),
    "en": (
        "This value's evidence is one rank BELOW Table 5-6's (a published table read with a "
        "measured number): it was chosen by a search inside a published band, not measured. What "
        "fills that place is the search ledger and the holdout proof (DECISIONS.md 2026-08-20)."
    ),
    "ja": (
        "この値の根拠はTable 5-6(測定値で公表表を参照)より一段低いものです — 測定された値では"
        "なく、公表範囲の中を探索して選んだ値です。その位置を埋めるのは探索台帳とホールドアウト"
        "の証明です(DECISIONS.md 2026-08-20)。"
    ),
}


def published_band(parameter: Any) -> tuple[float, float] | None:
    """``(low, high)`` for one searchable parameter, or ``None``.

    Read from ``mrwa_reference`` on every call — the same function
    ``parameter_search.search_space()`` builds its dimensions from. If a band ever leaves
    the reference pack, this exception stops covering that parameter in the same breath
    that the search stops proposing it, instead of the two disagreeing.
    """
    name = str(parameter or "")
    if name not in GRANTED_PARAMETERS:
        return None
    return mrwa_reference.merging_behaviour_range(name)


def band_verdict(action: Mapping[str, Any] | None) -> str | None:
    """The published-band verdict for ONE arriving write, or ``None`` when it is fine.

    Separated out so the arrival check has a name of its own and can be exercised
    directly. Defensive on every field: an unreadable parameter or value refuses.
    """
    payload = action if isinstance(action, Mapping) else {}
    params = payload.get("params")
    params = params if isinstance(params, Mapping) else {}
    band = published_band(params.get("parameter"))
    if band is None:
        return PARAMETER_NOT_SEARCHABLE
    value = _number(params.get("value"))
    low, high = band
    if value is None or not (low - 1e-9 <= value <= high + 1e-9):
        return VALUE_OUT_OF_PUBLISHED_RANGE
    return None


# ---------------------------------------------------------------------------
# The grant
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchedBehaviourGrant:
    """One accepted search winner's writes, and the radius they were consented to.

    Constructed only by ``parameter_search.grant_from_search`` — see this module's
    docstring for the five things that function establishes first, and
    ``car_following_grant``'s for why there is no "trust me" constructor anywhere in
    this design. Frozen: a grant that could be widened after the human read it would
    defeat its own purpose.
    """

    #: The search's own id — also the reference ``pending_approval.json`` may carry.
    search_id: str
    #: The driving behaviour every write targets, cross-checked at apply time.
    behavior_no: str
    #: ``{parameter: value}`` for the writes this grant authorizes — for the message,
    #: not for the match (the match is :attr:`signatures`).
    values: Mapping[str, float]
    #: Where the value came from: candidate count, ledger reference, holdout seed and
    #: numbers, the bands searched inside, the acceptance code, the evidence rank.
    #: Everything a rationale needs in order to cite the search rather than assert it.
    provenance: Mapping[str, Any]
    #: :func:`consent_radius` at proposal time. The drift check's left-hand side.
    consent_radius: Mapping[str, Any]
    #: The canonical write signatures this grant authorizes, and nothing else.
    signatures: frozenset[str]

    @property
    def kind(self) -> str:
        """:data:`PROVENANCE_KIND`. Read by ``gates._write_provenance`` so that module
        never has to know what kinds of grant exist."""
        return PROVENANCE_KIND

    def covers(self, action: Mapping[str, Any] | None) -> bool:
        """Does this grant name THIS exact write?

        Tool, behaviour number, parameter name and value are checked as ONE: the
        action's canonical signature must be one of the ones the accepted proposal
        emitted. A payload that differs anywhere — a different behaviour, a
        sixth-decimal-place value, a fourth parameter — canonicalizes differently and is
        not covered.
        """
        payload = action if isinstance(action, Mapping) else {}
        if payload.get("tool") != GRANT_TOOL:
            return False
        params = payload.get("params")
        params = dict(params) if isinstance(params, Mapping) else {}
        return signature(GRANT_TOOL, params) in self.signatures

    def to_dict(self) -> dict[str, Any]:
        """JSON-shaped, for traces. NOT for persistence: nothing that can rebuild a
        grant is ever written to disk (``searched_behaviour_broker``'s docstring)."""
        return {
            "kind": PROVENANCE_KIND,
            "search_id": self.search_id,
            "behavior_no": self.behavior_no,
            "values": dict(self.values),
            "provenance": dict(self.provenance),
            "consent_radius": dict(self.consent_radius),
            "signatures": sorted(self.signatures),
        }


def build_grant(
    *,
    search_id: str,
    behavior_no: str,
    values: Mapping[str, float],
    provenance: Mapping[str, Any],
    consent_radius_payload: Mapping[str, Any],
    signatures: Iterable[str],
) -> SearchedBehaviourGrant:
    """Freeze a grant. Called by ``parameter_search.grant_from_search``, nowhere else.

    Kept here so the dataclass and its one constructor live together, and deliberately
    given no validation of its own beyond shape — for the reason
    ``car_following_grant.build_grant`` records: the thing that makes a grant
    trustworthy is that its only caller derives every field from a finished search, and
    duplicating half of that here would invite the belief that this function is safe to
    call from anywhere. It is not.

    **DEEP copies, not ``dict()`` (refuter, #51-b).** ``frozen=True`` freezes the
    ATTRIBUTES; it does nothing to the containers behind them, and a shallow ``dict()``
    left ``consent_radius["consumers"]`` as the very list object the caller's proposal
    payload holds. Mutating that list after minting moved the drift check's LEFT-hand
    side — a consumer swap that keeps the count and the link total (link type 3 out,
    link type 7 in) is exactly the drift the check exists to catch, and it could be
    spelled away from outside the grant. That is #47's refuter finding in this species,
    so the grant takes a private copy of everything it will later be compared against.
    The payloads are JSON-shaped throughout (``consent_radius`` is
    ``car_following_grant.consent_radius``'s own output, ``provenance`` is built from
    the reference pack and the ledger), so the copy is total.
    """
    return SearchedBehaviourGrant(
        search_id=str(search_id),
        behavior_no=str(behavior_no),
        values={str(k): float(v) for k, v in dict(values).items()},
        provenance=copy.deepcopy(dict(provenance)),
        consent_radius=copy.deepcopy(dict(consent_radius_payload)),
        signatures=frozenset(str(s) for s in signatures),
    )


# ---------------------------------------------------------------------------
# The verdict the constraint engine acts on
# ---------------------------------------------------------------------------


def provenance_from(extra: Mapping[str, Any] | None) -> dict[str, Any]:
    """This species' provenance payload out of a constraint ``extra`` dict, or ``{}``.

    The two discriminators, both required: ``kind`` must be :data:`PROVENANCE_KIND`, and
    ``grant`` must be an actual :class:`SearchedBehaviourGrant` INSTANCE. A dict that
    quacks like one — the shape a persisted-and-rehydrated grant would have — is not one,
    which is what makes a hand-written file worthless here (#47's argument, unchanged).
    A Table 5-6 provenance payload fails both checks and degrades to "no grant", i.e.
    refuse.
    """
    payload = extra if isinstance(extra, Mapping) else {}
    provenance = payload.get(PROVENANCE_KEY)
    if not isinstance(provenance, Mapping):
        return {}
    if provenance.get("kind") != PROVENANCE_KIND:
        return {}
    if not isinstance(provenance.get("grant"), SearchedBehaviourGrant):
        return {}
    return dict(provenance)


def evaluate(
    action: Mapping[str, Any] | None,
    behavior: Mapping[str, Any] | None,
    extra: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Structural verdict for one proposed write against one live behaviour.

    ``behavior`` is the shadow model's OWN entry for the target, re-read at apply time —
    the drift check's right-hand side is never taken from the action or from the grant.

    Never raises: every failure mode collapses to a verdict that refuses. Returns
    ``{"verdict", "grant", "consent", "consented_radius", "current_radius"}``; only
    :data:`GRANTED` lets the shared-object refusal step aside.
    """
    provenance = provenance_from(extra)
    if not provenance:
        return _verdict(NO_GRANT)

    grant: SearchedBehaviourGrant = provenance["grant"]
    if not grant.covers(action):
        return _verdict(NOT_COVERED, grant=grant, consent=provenance.get("consent"))

    # THE PUBLISHED BAND, RE-READ AT ARRIVAL (the #51 refuter's wall, repeated at the
    # last possible moment). The signature match above already pins the value byte-for-
    # byte, so on every path that mints honestly this cannot fire — which is exactly why
    # it is here rather than only at mint time: the band is the entire licence for a
    # searched number, and a licence checked only where the number is produced is a
    # licence checked in one place. Reads mrwa_reference, not a copy carried on the
    # grant, so a grant cannot vouch for its own bounds.
    band = band_verdict(action)
    if band is not None:
        return _verdict(band, grant=grant, consent=provenance.get("consent"))

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

    # Equal — but equal to WHAT? Checked after the drift compare for #44-b's reason:
    # when the two genuinely differ, "그 반경이 바뀌었다" is the more specific refusal and
    # it quotes real numbers on both sides.
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
    grant: SearchedBehaviourGrant | None = None,
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


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "AWAITING_APPROVAL",
    "CONSENT_STATES",
    "CONSUMED_APPROVAL",
    "EVIDENCE_RANK",
    "GRANTED",
    "GRANTED_PARAMETERS",
    "GRANT_TOOL",
    "NOT_CONSENTED",
    "NOT_COVERED",
    "NO_GRANT",
    "PARAMETER_NOT_SEARCHABLE",
    "PROVENANCE_KEY",
    "PROVENANCE_KIND",
    "RADIUS_DRIFTED",
    "RADIUS_NOT_FULLY_NAMED",
    "SearchedBehaviourGrant",
    "VALUE_OUT_OF_PUBLISHED_RANGE",
    "VERDICTS",
    "band_verdict",
    "build_grant",
    "consent_radius",
    "evaluate",
    "provenance_from",
    "published_band",
    "radius_phrase",
]

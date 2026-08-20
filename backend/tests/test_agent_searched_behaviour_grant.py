"""Unit #51-b — the SECOND narrow exception to ``driving_behavior_shared_object_edit``.

What these tests are for
------------------------
DECISIONS.md 2026-08-20 relaxed a refusal for a second time, for #51's searched
parameters. A relaxed refusal is worth exactly what it still refuses, so the file is
organised around the ways this one could be talked past:

1. **The chain is real, end to end.** A search the engine actually ran, a winner that
   actually survived a holdout at an unseen seed, a grant minted from those, a gate
   bound to a human's click, the real constraint engine — and the registry diff at the
   moment it fires is exactly ONE exchange: the shared-object refusal out, the
   consented-radius warning in.

2. **The key is a FINISHED SEARCH, and the ledger is re-derived, not believed.** A
   forged acceptance code, a partial search, a chosen split, a leaked holdout node, a
   winner nothing ever ran, a value outside the published band, a fourth parameter, a
   partial application of the winner vector — every one of them is a named refusal.

3. **No file can produce a grant.** The only carrier is an in-memory broker;
   ``pending_approval.json`` may name a search id and nothing else. A hand-written
   ledger file — the failure DECISIONS.md 2026-08-20 names by name — cannot reach the
   minting path at all, because no file is ever an input to it.

4. **The two species do not mix.** A Table 5-6 grant cannot authorize a searched write
   and a searched grant cannot authorize a Table 5-6 write, proved both ways on both
   discriminators (``kind`` and ``isinstance``).

5. **The forecast agrees with the engine on both paths.** #44's refuter invariant,
   re-run over this unit's grant species: a forecast may be pessimistic, never
   optimistic.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from services.agent import car_following_grant as cfg  # noqa: E402
from services.agent import constraints, guardrails  # noqa: E402
from services.agent import parameter_search as ps  # noqa: E402
from services.agent import saturation_flow as sf  # noqa: E402
from services.agent import searched_behaviour_broker as broker  # noqa: E402
from services.agent import searched_behaviour_grant as sbg  # noqa: E402
from services.agent.gates import (  # noqa: E402
    PreapprovedWriteGate,
    ProposeWriteGate,
    chain_grant_providers,
    signature,
)
from services import vissim_com  # noqa: E402

from test_agent_car_following_grant import (  # noqa: E402
    _approved_gate as _table_gate,
    _behavior,
    _payloads as _table_payloads,
    _proposals as _table_proposals,
    _shadow,
)
from test_agent_parameter_search import (  # noqa: E402
    BASELINE,
    _Clock,
    _open_gate,
    _runner,
)

SHARED_OBJECT = "driving_behavior_shared_object_edit"
CONSENTED = "searched_behaviour_shared_edit_consented"
DRIFTED = "searched_behaviour_consent_radius_drifted"

#: The Table 5-6 exception's two constraint ids, so the cross-species tests can assert
#: that NEITHER of them fires on a searched write.
TABLE_CONSENTED = "car_following_shared_edit_consented"
TABLE_DRIFTED = "car_following_consent_radius_drifted"


# ---------------------------------------------------------------------------
# Fixtures — a real finished search over the synthetic surface
# ---------------------------------------------------------------------------


def _finished(behavior: dict[str, Any] | None = None, *, search_id: str = "s-1"):
    """A COMPLETE search, its holdout acceptance, and the proposal built from both.

    The order matters and is the order a real caller must use: ``accept_winner``
    appends the two HOLDOUT rows to the live state, so the ``result`` a grant is minted
    from has to be snapshotted AFTER acceptance — otherwise the ledger it carries does
    not contain the evidence the grant rests on. ``grant_from_search`` refuses the other
    order by name rather than minting a grant whose ledger cannot support it.
    """
    target = behavior if behavior is not None else _behavior(4)
    gate = _open_gate()
    state = ps.start(
        search_id=search_id,
        preconditions=gate,
        baseline_vector=BASELINE,
        seed=11,
        budget_s=3600.0,
    )
    search = ps.ParameterSearch(state=state, runner=_runner(), clock=_Clock())
    search.run()
    acceptance = ps.accept_winner(search, _runner(), unseen_seed=99)
    assert acceptance["code"] == ps.HOLDOUT_PASSED, "fixture no longer passes its holdout"
    result = search.result(ps.STATUS_COMPLETE)
    proposal = ps.propose(result=result, acceptance=acceptance, behavior=target)
    assert proposal["outcome"] == "PROPOSAL", "fixture no longer proposes"
    return {
        "preconditions": gate,
        "result": result,
        "acceptance": acceptance,
        "proposal": proposal,
    }


def _grant(record: dict[str, Any] | None = None) -> sbg.SearchedBehaviourGrant:
    return ps.grant_from_search(**(record or _finished()))


def _payloads(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(action) for action in record["proposal"]["actions"]]


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
            extra = {sbg.PROVENANCE_KEY: provenance}
    return guardrails.GuardrailEngine(disabled=False).preflight(payload, shadow, extra)


def _ids(report: guardrails.GuardrailReport) -> set[tuple[str, str]]:
    return {(f.constraint_id, f.tier) for f in report.findings}


def _approved_gate(record: dict[str, Any], grant: Any = None) -> PreapprovedWriteGate:
    payloads = _payloads(record)
    return PreapprovedWriteGate(
        {signature(p["tool"], p["params"]) for p in payloads},
        grant=grant if grant is not None else _grant(record),
    )


@pytest.fixture(autouse=True)
def _isolate_broker():
    broker.reset()
    yield
    broker.reset()


# ===========================================================================
# 1. The chain: search → acceptance → grant → gate → engine → consumed
# ===========================================================================


def test_the_whole_chain_opens_the_guardrail_once_per_member_and_then_stops():
    """Search → holdout → grant → approval gate → constraint engine → consumed.

    The claim DECISIONS.md 2026-08-20 makes is that a searched winner can actually be
    applied. This walks that path with the real engine, the real optimizer over a real
    (synthetic) GEH surface, the real gate and the real constraint engine, and then one
    step past the end of it.
    """
    record = _finished()
    grant = _grant(record)
    shadow = _shadow(4)
    gate = _approved_gate(record, grant)

    assert len(record["proposal"]["actions"]) >= 1
    for payload in _payloads(record):
        assert gate.authorize(payload, None) is True
        report = _preflight(payload, shadow, gate)
        assert report.blocking is False, payload
        assert (SHARED_OBJECT, "refuse") not in _ids(report)
        assert (CONSENTED, "warn") in _ids(report)

    # One step past the end: an approved signature is good for exactly ONE dispatch, and
    # the refusal happens at the GATE — the engine is never asked to excuse a reuse.
    first = _payloads(record)[0]
    assert gate.authorize(first, None) is False
    assert gate.refusal_reason(first, None) == "approval_consumed"


def test_the_registry_diff_is_exactly_one_exchange():
    """The whole of what the exception does to the engine's verdict, as a diff.

    Not "fewer refusals" and not "a quieter report": exactly ONE constraint leaves
    (the shared-object refusal) and exactly ONE arrives (the consented-radius warning).
    Anything else in that diff would mean the grant had silenced something it was never
    decided to silence.
    """
    record = _finished()
    shadow = _shadow(4)
    gate = _approved_gate(record)

    for payload in _payloads(record):
        gate.authorize(payload, None)
        without = _ids(_preflight(payload, shadow))
        with_grant = _ids(_preflight(payload, shadow, gate))
        assert without - with_grant == {(SHARED_OBJECT, "refuse")}, payload
        assert with_grant - without == {(CONSENTED, "warn")}, payload


def test_the_exception_names_the_radius_the_search_and_its_own_evidence_rank():
    """The decision's safety argument IS the sentence, and this species has to say two
    things in it: where the edit lands, and that its evidence ranks BELOW Table 5-6's."""
    record = _finished()
    gate = _approved_gate(record)
    payload = _payloads(record)[0]
    gate.authorize(payload, None)
    report = _preflight(payload, _shadow(4), gate)
    finding = next(f for f in report.findings if f.constraint_id == CONSENTED)

    assert finding.tier == "warn"
    assert finding.details["consented_radius"]["reference_count"] == 4
    assert finding.details["consented_radius"]["link_total"] == 46  # 10+11+12+13
    assert finding.details["decision"] == "DECISIONS.md 2026-08-20"
    assert finding.details["evidence_rank_below"] == "TABLE_5_6_LOOKUP"

    provenance = finding.details["provenance"]
    ledger = provenance["ledger"]
    for language in ("ko", "en", "ja"):
        text = finding.messages[language]
        assert str(provenance["candidates"]) in text, language        # 후보 수
        assert ledger["digest"] in text, language                     # 원장 참조
        assert str(provenance["unseen_seed"]) in text, language       # 홀드아웃 seed
        assert "0.25-0.45" in text or "0.2-1" in text, language       # MRWA 범위 X–Y
        assert "2026-08-20" in text, language
        assert sbg.EVIDENCE_RANK[language] in text, language          # 근거의 격


def test_an_unshared_behaviour_announces_no_exception_because_none_was_needed():
    """A behaviour with zero references was always editable in place. Crediting the
    exception there would train a reader to ignore the one sentence that matters."""
    record = _finished(_behavior(0))
    report = _preflight(_payloads(record)[0], _shadow(0), _approved_gate(record))

    assert report.blocking is False
    assert report.findings == []


def test_a_direct_tool_call_is_refused_exactly_as_before():
    """No gate, no grant, no approval — the guardrail's original job, untouched. The
    path ``fixes.preflight_findings``, the health check and every planner tool call
    that did not come through an approval take."""
    record = _finished()
    for payload in _payloads(record):
        report = _preflight(payload, _shadow(4))
        assert report.blocking is True
        assert (SHARED_OBJECT, "refuse") in _ids(report)
        assert (CONSENTED, "warn") not in _ids(report)


def test_the_approval_card_stage_is_non_blocking_so_the_human_is_actually_asked():
    """A refuse-tier finding while the card is being BUILT means the human is never
    asked at all — the dead end DECISIONS.md 2026-08-18 recorded. Nothing can dispatch
    from this stage: ``ProposeWriteGate.authorize`` never returns a verdict."""
    record = _finished()
    gate = ProposeWriteGate(grant=_grant(record))
    payload = _payloads(record)[0]

    provenance = gate.write_provenance(payload)
    assert provenance["consent"] == sbg.AWAITING_APPROVAL
    assert _preflight(payload, _shadow(4), gate).blocking is False


# ===========================================================================
# 2. The forgery sweep — every way to get a grant you did not earn
# ===========================================================================


def test_a_tampered_ledger_value_cannot_become_a_grant():
    """DECISIONS.md 2026-08-20's named failure: 원장 파일을 손으로 고쳐 다른 값의 grant를
    얻으면 실패입니다. Tampering with what the writes say, or with what the acceptance
    claims, is caught by cross-checking them against the ledger."""
    record = _finished()
    winner = dict(record["proposal"]["actions"][0]["params"])

    # (a) the WRITE is edited to a different, still in-band value.
    tampered = json.loads(json.dumps(record))
    tampered["proposal"]["actions"][0]["params"]["value"] = round(
        float(winner["value"]) + 0.01, 4
    )
    with pytest.raises(ValueError, match="the accepted winner is"):
        ps.grant_from_search(**tampered)

    # (b) the ACCEPTANCE is edited to claim a different winner, and the writes with it.
    tampered = json.loads(json.dumps(record))
    moved = tampered["acceptance"]["holdout"]["winner"]["params"]
    moved[winner["parameter"]] = round(float(winner["value"]) + 0.01, 4)
    tampered["proposal"]["actions"][0]["params"]["value"] = moved[winner["parameter"]]
    with pytest.raises(ValueError, match="the ledger's holdout run was over"):
        ps.grant_from_search(**tampered)

    # (c) the CALIBRATION ledger row is edited so the winner was never actually run.
    tampered = json.loads(json.dumps(record))
    for row in tampered["result"]["state"]["ledger"]:
        if row["node_set"] == "CALIBRATION":
            row["params"] = dict(BASELINE)
    with pytest.raises(ValueError, match="no calibration ledger row ever ran the winner"):
        ps.grant_from_search(**tampered)


def test_a_forged_acceptance_code_is_re_derived_from_the_ledger_and_refused():
    """HOLDOUT_PASSED is never believed. The two HOLDOUT rows have to be there, at a
    seed the search never used, and the winner has to really beat the baseline on them."""
    record = _finished()

    # The code says PASSED; the ledger's holdout rows are gone.
    tampered = json.loads(json.dumps(record))
    tampered["result"]["state"]["ledger"] = [
        row for row in tampered["result"]["state"]["ledger"] if row["stage"] != ps.STAGE_HOLDOUT
    ]
    with pytest.raises(ValueError, match="HOLDOUT row"):
        ps.grant_from_search(**tampered)

    # The code says PASSED; the ledger says the winner was WORSE out of sample.
    tampered = json.loads(json.dumps(record))
    rows = [r for r in tampered["result"]["state"]["ledger"] if r["stage"] == ps.STAGE_HOLDOUT]
    rows[-2]["objective"] = rows[-1]["objective"] + 1.0
    with pytest.raises(ValueError, match="did NOT improve out of sample"):
        ps.grant_from_search(**tampered)

    # The code says PASSED; the winner passes fewer held-out nodes than the baseline.
    tampered = json.loads(json.dumps(record))
    rows = [r for r in tampered["result"]["state"]["ledger"] if r["stage"] == ps.STAGE_HOLDOUT]
    rows[-2]["pass_count"] = 0
    rows[-1]["pass_count"] = 2
    with pytest.raises(ValueError, match="passes fewer held-out nodes"):
        ps.grant_from_search(**tampered)


def test_a_holdout_that_did_not_pass_cannot_be_minted():
    record = _finished()
    tampered = json.loads(json.dumps(record))
    tampered["acceptance"]["accepted"] = False
    tampered["acceptance"]["code"] = ps.HOLDOUT_NOT_IMPROVED
    with pytest.raises(ValueError, match="not accepted"):
        ps.grant_from_search(**tampered)


def test_a_partial_search_cannot_be_minted():
    """"완료된 탐색에서만" — a partial result's best row is a snapshot of an
    interrupted process, not a winner."""
    record = _finished()
    for status in (ps.STATUS_PARTIAL_BUDGET_EXHAUSTED, ps.STATUS_PARTIAL_INTERRUPTED, ""):
        tampered = json.loads(json.dumps(record))
        tampered["result"]["status"] = status
        with pytest.raises(ValueError, match="not COMPLETE"):
            ps.grant_from_search(**tampered)


def test_the_holdout_seed_must_be_one_the_search_never_used():
    record = _finished()
    tampered = json.loads(json.dumps(record))
    tampered["acceptance"]["holdout"]["unseen_seed"] = tampered["result"]["state"]["seed"]
    with pytest.raises(ValueError, match="the search's own seed"):
        ps.grant_from_search(**tampered)


def test_a_chosen_split_cannot_ride_in_on_the_precondition_record():
    """The #51 refuter's wall, re-run at mint time: a holdout a caller may choose is not
    a holdout, and the record a grant cites has to be the one that opened THIS search."""
    record = _finished()

    tampered = json.loads(json.dumps(record))
    tampered["preconditions"]["split"] = {
        "available": True,
        "calibration": ["3", "4", "5", "6", "7", "8"],
        "holdout": ["3", "4", "5", "6", "7", "8"],
    }
    with pytest.raises(ValueError, match="not the one split_nodes derives"):
        ps.grant_from_search(**tampered)

    tampered = json.loads(json.dumps(record))
    tampered["preconditions"]["ok"] = False
    with pytest.raises(ValueError, match="no passing precondition record"):
        ps.grant_from_search(**tampered)

    # The state's own split is re-derived too, not read.
    tampered = json.loads(json.dumps(record))
    tampered["result"]["state"]["holdout_nodes"] = list(
        tampered["result"]["state"]["calibration_nodes"]
    )
    with pytest.raises(ValueError, match="not the one split_nodes derives"):
        ps.grant_from_search(**tampered)


def test_a_calibration_row_that_scored_a_held_out_node_kills_the_grant():
    """The holdout withholds the DATA. A ledger that scored it is not a holdout, whatever
    its ``node_set`` column says."""
    record = _finished()
    tampered = json.loads(json.dumps(record))
    held = tampered["result"]["state"]["holdout_nodes"][0]
    for row in tampered["result"]["state"]["ledger"]:
        if row["node_set"] == "CALIBRATION":
            row["geh_by_node"][held] = 0.5
            break
    with pytest.raises(ValueError, match="carries held-out node"):
        ps.grant_from_search(**tampered)


def test_both_holdout_legs_must_be_scored_on_the_same_held_out_nodes():
    """The acceptance is a COMPARISON, so the two legs have to be comparable (refuter).

    ``grant_from_search`` only asked that each leg's scored nodes be a SUBSET of the
    held-out set, independently — which let a winner scored on one easy held-out node
    "beat" a baseline scored on both, and let the two legs score disjoint nodes entirely.
    Every other check passed on those payloads: the vectors were the real ones, the seed
    was unseen, the winner really ran. An improvement measured on different data is not
    an improvement, and it is the same objection the baseline-VECTOR check already makes
    one line earlier. ``_Objective.score`` keys every node it was asked for (an ungradable
    one carries ``None``), so an honest holdout row always names the whole set.
    """
    record = _finished()
    held = record["result"]["state"]["holdout_nodes"]
    assert len(held) >= 2, "fixture must hold out more than one node"

    def _legs(payload):
        rows = [r for r in payload["result"]["state"]["ledger"] if r["stage"] == ps.STAGE_HOLDOUT]
        return rows[-2], rows[-1]

    # (a) the winner leg is scored on ONE easy held-out node, the baseline on both.
    tampered = json.loads(json.dumps(record))
    winner_row, baseline_row = _legs(tampered)
    winner_row["geh_by_node"] = {held[0]: 0.4}
    winner_row["objective"], winner_row["pass_count"], winner_row["graded"] = 0.4, 1, 1
    baseline_row["geh_by_node"] = {node: 9.0 for node in held}
    baseline_row["objective"], baseline_row["pass_count"] = 9.0, 0
    with pytest.raises(ValueError, match="not the held-out set"):
        ps.grant_from_search(**tampered)

    # (b) the two legs are scored on DISJOINT held-out nodes.
    tampered = json.loads(json.dumps(record))
    winner_row, baseline_row = _legs(tampered)
    winner_row["geh_by_node"] = {held[0]: 1.0}
    winner_row["objective"], winner_row["pass_count"] = 1.0, 1
    baseline_row["geh_by_node"] = {held[1]: 8.0}
    baseline_row["objective"], baseline_row["pass_count"] = 8.0, 0
    with pytest.raises(ValueError, match="not the held-out set"):
        ps.grant_from_search(**tampered)


def test_a_baseline_the_ledger_never_ran_cannot_decide_which_writes_are_covered():
    """The PARTIAL-application wall, attacked from the other side (refuter).

    What MOVED is measured from ``state.baseline_vector``, but the holdout's baseline leg
    is pinned to the ledger's own BASELINE row — and nothing compared the two. Setting the
    declared baseline of one moved parameter to the WINNER's value re-labelled it
    "unchanged", so dropping its write no longer looked partial and a grant covering half
    the searched vector minted cleanly. Moving the ledger row instead breaks the
    baseline-leg comparison, so the two now have to move together or not at all.
    """
    record = _finished()
    assert len(record["proposal"]["actions"]) >= 2, "fixture must move more than one parameter"
    tampered = json.loads(json.dumps(record))
    dropped = tampered["proposal"]["actions"][-1]["params"]
    tampered["result"]["state"]["baseline_vector"][dropped["parameter"]] = dropped["value"]
    tampered["proposal"]["actions"] = tampered["proposal"]["actions"][:-1]
    with pytest.raises(ValueError, match="declares its baseline as"):
        ps.grant_from_search(**tampered)

    # And moving the ledger's BASELINE row with it does not rescue the forgery.
    for row in tampered["result"]["state"]["ledger"]:
        if row["stage"] == ps.STAGE_BASELINE:
            row["params"][dropped["parameter"]] = dropped["value"]
    with pytest.raises(ValueError, match="the holdout's baseline leg ran"):
        ps.grant_from_search(**tampered)


def test_a_calibration_row_that_graded_nothing_did_not_run_the_winner():
    """"The winner was really run" has to mean it produced a NUMBER (refuter).

    The check asked only for ``ok``, so appending one row carrying the winner's params,
    ``ok: True``, no objective and an empty ``geh_by_node`` satisfied it — a claim that
    the vector was evaluated, offered in place of evidence that it was. It also walks
    straight past the held-out-node leak check, which only reads ``geh_by_node``.
    """
    record = _finished()
    tampered = json.loads(json.dumps(record))
    winner_params = tampered["acceptance"]["holdout"]["winner"]["params"]
    for row in tampered["result"]["state"]["ledger"]:
        if row["node_set"] == "CALIBRATION":
            row["params"] = dict(BASELINE)
    tampered["result"]["state"]["ledger"].append(
        {
            "index": len(tampered["result"]["state"]["ledger"]),
            "stage": ps.STAGE_OPTIMIZATION,
            "params": dict(winner_params),
            "seed": tampered["result"]["state"]["seed"],
            "node_set": "CALIBRATION",
            "objective": None,
            "pass_count": 0,
            "graded": 0,
            "geh_by_node": {},
            "ok": True,
            "reason": None,
            "started_at_s": None,
            "elapsed_s": 0.0,
        }
    )
    with pytest.raises(ValueError, match="to a graded objective"):
        ps.grant_from_search(**tampered)


def test_a_minted_grant_owns_its_radius_and_cannot_be_edited_from_outside():
    """#47's refuter exploit, at the GRANT rather than at the broker (refuter).

    ``frozen=True`` freezes the attributes, not the containers behind them, and
    ``build_grant`` took shallow ``dict()`` copies — so ``consent_radius["consumers"]``
    stayed the very list the caller's proposal payload holds. That list is the drift
    check's LEFT-hand side. A consumer swap that keeps the count and the link total (one
    link behavior type out, another in) is precisely the drift the check exists to catch,
    and while the list was shared it could be spelled away from outside the grant, after
    the human had read the sentence it names.
    """
    record = _finished()
    grant = _grant(record)
    radius = record["proposal"]["consent_radius"]
    assert grant.consent_radius["consumers"] is not radius["consumers"]
    assert grant.provenance["bands"] is not record["proposal"].get("bands")

    # The live model is UNCHANGED; only the payload the grant was minted from is edited.
    swapped = [list(entry) for entry in radius["consumers"]]
    swapped[0][0] = "99"  # same count, same link total, a different consumer
    radius["consumers"][:] = swapped
    radius["reference_count"] = 99
    grant.provenance["candidates"] = 0

    payload = _payloads(record)[0]
    extra = {sbg.PROVENANCE_KEY: {"kind": sbg.PROVENANCE_KIND, "grant": grant,
                                  "consent": sbg.CONSUMED_APPROVAL}}
    verdict = sbg.evaluate(payload, _behavior(4), extra)
    assert verdict["verdict"] == sbg.GRANTED
    assert verdict["consented_radius"] == sbg.consent_radius(_behavior(4))
    assert grant.provenance["candidates"] == 0  # the caller edited its OWN copy only

    # ...and the swap is still seen as drift when it happens in the MODEL, which is the
    # half of the check the shared list was quietly disarming.
    moved = _behavior(4)
    moved["referenced_by"]["consumers"][0]["link_behavior_type_no"] = "99"
    assert sbg.evaluate(payload, moved, extra)["verdict"] == sbg.RADIUS_DRIFTED


def test_a_vector_outside_the_published_band_is_refused_at_mint_and_at_arrival():
    """The band is the whole licence for a searched value, so it is checked in BOTH
    places: nothing can be minted over an out-of-band winner, and nothing out of band is
    honoured on arrival even with a grant present."""
    record = _finished()
    parameter = record["proposal"]["actions"][0]["params"]["parameter"]

    tampered = json.loads(json.dumps(record))
    for target in (
        tampered["acceptance"]["holdout"]["winner"]["params"],
        *[
            row["params"]
            for row in tampered["result"]["state"]["ledger"]
        ],
    ):
        if parameter in target:
            target[parameter] = 9.9
    tampered["proposal"]["actions"][0]["params"]["value"] = 9.9
    with pytest.raises(ValueError, match="published MRWA band"):
        ps.grant_from_search(**tampered)

    # And at arrival: a grant built (through the low-level constructor, which is the
    # only way to get one at all) over an out-of-band write is refused BY NAME.
    action = {
        "tool": sbg.GRANT_TOOL,
        "params": {"behavior_no": "2", "parameter": parameter, "value": 9.9},
    }
    rogue = sbg.build_grant(
        search_id="rogue",
        behavior_no="2",
        values={parameter: 9.9},
        provenance={},
        consent_radius_payload=sbg.consent_radius(_behavior(4)),
        signatures=[signature(sbg.GRANT_TOOL, action["params"])],
    )
    verdict = sbg.evaluate(
        action,
        _behavior(4),
        {sbg.PROVENANCE_KEY: {"kind": sbg.PROVENANCE_KIND, "grant": rogue,
                              "consent": sbg.CONSUMED_APPROVAL}},
    )
    assert verdict["verdict"] == sbg.VALUE_OUT_OF_PUBLISHED_RANGE
    findings = constraints.run_checks(
        "preflight",
        action,
        _shadow(4),
        {sbg.PROVENANCE_KEY: {"kind": sbg.PROVENANCE_KIND, "grant": rogue,
                              "consent": sbg.CONSUMED_APPROVAL}},
    )
    # Refused through #40's own message: "this grant does not license this write" is
    # exactly what that constraint already says, so no fourth id is invented for it.
    assert {(f.constraint_id, f.tier) for f in findings} >= {(SHARED_OBJECT, "refuse")}
    assert CONSENTED not in {f.constraint_id for f in findings}


#: The whitelisted driving-behaviour parameters this exception does NOT name, read from
#: the write layer's own list rather than retyped, so a future addition to the whitelist
#: automatically has to prove it stays outside the exception.
_UNGRANTED_WHITELIST = tuple(
    name
    for name in vissim_com.VissimController.DRIVING_BEHAVIOR_PARAM_FIELDS
    if name not in sbg.GRANTED_PARAMETERS
)


def test_the_exception_covers_exactly_the_three_searched_parameters():
    """The arithmetic the sweep below depends on, stated once — and the two lists that
    must never drift: what the SEARCH may move, and what the EXCEPTION may cover."""
    assert set(sbg.GRANTED_PARAMETERS) == set(ps.SEARCHABLE_PARAMETERS)
    assert set(sbg.GRANTED_PARAMETERS) == {
        dimension.parameter for dimension in ps.search_space()
    }
    assert len(_UNGRANTED_WHITELIST) == 5
    assert set(sbg.GRANTED_PARAMETERS).isdisjoint(cfg.GRANTED_PARAMETERS)
    assert set(sbg.GRANTED_PARAMETERS).isdisjoint(_UNGRANTED_WHITELIST)
    assert not any(name.startswith("cc") for name in sbg.GRANTED_PARAMETERS)


@pytest.mark.parametrize(
    "params,why",
    [
        ({"behavior_no": "3", "parameter": "lat_dist_driv_def", "value": 0.55},
         "a different behaviour"),
        ({"behavior_no": "2", "parameter": "lat_dist_driv_def", "value": 0.9},
         "a different value"),
        ({"behavior_no": "2", "parameter": "cc0", "value": 1.5}, "a W99 name"),
        ({"behavior_no": "2", "parameter": "ax", "value": 2.5}, "Table 5-6's own"),
        ({"behavior_no": "2", "parameter": "look_ahead_dist_max", "value": 250.0},
         "another whitelisted parameter"),
        ({"behavior_no": "2", "parameter": "min_speed_for_lat", "value": 3.0},
         "a whitelisted parameter with no published band"),
    ],
)
def test_a_payload_the_grant_does_not_name_is_refused_with_the_grant_present(params, why):
    """The exception is a per-action match, not a mode. Everything the grant does not
    name by canonical signature is refused while the grant is right there in ``extra``."""
    record = _finished()
    grant = _grant(record)
    payload = {"tool": sbg.GRANT_TOOL, "params": params}
    gate = PreapprovedWriteGate({signature(payload["tool"], payload["params"])}, grant=grant)
    gate.authorize(payload, None)

    report = _preflight(payload, _shadow(4), gate)
    assert report.blocking is True, why
    assert (SHARED_OBJECT, "refuse") in _ids(report), why
    assert (CONSENTED, "warn") not in _ids(report), why


def test_a_fourth_parameter_cannot_be_added_to_the_proposal_either():
    """Not merely uncovered at arrival — unmintable. A write the winner does not name
    cannot get into a grant's signature set in the first place."""
    record = _finished()
    tampered = json.loads(json.dumps(record))
    tampered["proposal"]["actions"].append(
        {"tool": ps.SEARCH_TOOL, "params": {"behavior_no": "2", "parameter": "cc0", "value": 1.5}}
    )
    with pytest.raises(ValueError, match="published-band check"):
        ps.grant_from_search(**tampered)


def test_a_partial_application_of_the_winner_vector_cannot_be_minted():
    """A searched vector applied in part is a different vector — nothing was ever scored
    at it, so it carries none of the evidence the ledger holds."""
    record = _finished()
    assert len(record["proposal"]["actions"]) >= 2, "fixture must move more than one parameter"
    tampered = json.loads(json.dumps(record))
    tampered["proposal"]["actions"] = tampered["proposal"]["actions"][:1]
    with pytest.raises(ValueError, match="PARTIAL application"):
        ps.grant_from_search(**tampered)


def test_a_grant_cannot_be_smuggled_in_through_the_action_params():
    """The channel is the write GATE. A planner emits only ``{"tool", "params"}`` and
    there is no field in a payload that reaches the engine's ``extra``."""
    record = _finished()
    grant = _grant(record)
    payload = _payloads(record)[0]
    smuggled = {
        "tool": payload["tool"],
        "params": {
            **payload["params"],
            "approval_provenance": {"kind": sbg.PROVENANCE_KIND, "grant": grant,
                                    "consent": sbg.CONSUMED_APPROVAL},
            "searched_grant": grant.to_dict(),
        },
    }
    report = _preflight(smuggled, _shadow(4))
    assert report.blocking is True
    assert (SHARED_OBJECT, "refuse") in _ids(report)


@pytest.mark.parametrize(
    "extra,why",
    [
        ({}, "no provenance at all"),
        ({sbg.PROVENANCE_KEY: {"kind": sbg.PROVENANCE_KIND, "consent": "CONSUMED_APPROVAL"}},
         "no grant object"),
        ({sbg.PROVENANCE_KEY: {"kind": "something_else", "consent": "CONSUMED_APPROVAL"}},
         "a foreign kind"),
        ({sbg.PROVENANCE_KEY: "a string"}, "not a mapping"),
    ],
)
def test_a_hand_built_extra_that_is_not_a_real_grant_changes_nothing(extra, why):
    record = _finished()
    payload = _payloads(record)[0]
    assert sbg.evaluate(payload, _behavior(4), extra)["verdict"] == sbg.NO_GRANT, why


def test_a_serialized_grant_is_not_a_grant():
    """The ``isinstance`` half of the discriminator, stated on its own: a dict that
    quacks like a grant — exactly the shape a persisted-and-rehydrated one would have —
    is not one. This is what makes persistence pointless and therefore absent."""
    record = _finished()
    grant = _grant(record)
    payload = _payloads(record)[0]
    rehydrated = {
        sbg.PROVENANCE_KEY: {
            "kind": sbg.PROVENANCE_KIND,
            "grant": grant.to_dict(),
            "consent": sbg.CONSUMED_APPROVAL,
        }
    }
    assert sbg.evaluate(payload, _behavior(4), rehydrated)["verdict"] == sbg.NO_GRANT


def test_an_unrecognised_consent_state_refuses():
    """A grant is never self-sufficient: it says which writes a search produced, an
    approval says a human agreed to them, and the exception needs both."""
    record = _finished()
    payload = _payloads(record)[0]
    for consent in (None, "SOMETHING_ELSE", "APPROVED"):
        extra = {
            sbg.PROVENANCE_KEY: {
                "kind": sbg.PROVENANCE_KIND,
                "grant": _grant(record),
                "consent": consent,
            }
        }
        assert sbg.evaluate(payload, _behavior(4), extra)["verdict"] == sbg.NOT_CONSENTED


def test_a_gate_holding_the_grant_but_no_approval_for_this_write_offers_no_provenance():
    record = _finished()
    payload = _payloads(record)[0]
    gate = PreapprovedWriteGate(set(), grant=_grant(record))
    assert gate.write_provenance(payload) is None
    assert _preflight(payload, _shadow(4), gate).blocking is True


# ---------------------------------------------------------------------------
# 2b. Consent is to a NAMED radius
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "now,why",
    [
        (_behavior(5), "a consumer was added"),
        (_behavior(3), "a consumer was removed"),
    ],
)
def test_a_drifted_blast_radius_is_refused_by_name(now, why):
    record = _finished(_behavior(4))
    gate = _approved_gate(record)
    payload = _payloads(record)[0]
    gate.authorize(payload, None)

    report = _preflight(payload, {"driving_behaviors": [now]}, gate)
    assert report.blocking is True, why
    ids = _ids(report)
    # Refused TWICE: once generically (the exception never stands down), once by name.
    assert (SHARED_OBJECT, "refuse") in ids, why
    assert (DRIFTED, "refuse") in ids, why
    assert (CONSENTED, "warn") not in ids, why

    finding = next(f for f in report.findings if f.constraint_id == DRIFTED)
    for language in ("ko", "en", "ja"):
        assert str(finding.details["consented_radius"]["reference_count"]) in (
            finding.messages[language]
        ), language


def test_a_radius_that_was_never_whole_cannot_be_consented_to():
    """``tools_network`` caps the NAMED consumer list at 10 while keeping the count real,
    so an un-named consumer's links can change by any amount behind the consent. The
    exception is simply unavailable there — the original refusal never stands down, and
    no new name is invented for it."""
    truncated = _behavior(4, truncated=True)
    record = _finished(truncated)
    gate = _approved_gate(record)
    payload = _payloads(record)[0]
    gate.authorize(payload, None)

    report = _preflight(payload, {"driving_behaviors": [truncated]}, gate)
    assert report.blocking is True
    ids = _ids(report)
    assert (SHARED_OBJECT, "refuse") in ids
    assert (CONSENTED, "warn") not in ids
    assert (DRIFTED, "refuse") not in ids


def test_a_target_named_without_its_reference_graph_cannot_excuse_anything():
    counted_only = {"no": "2", "reference_count": 4}
    record = _finished(counted_only)
    gate = _approved_gate(record)
    payload = _payloads(record)[0]
    gate.authorize(payload, None)

    report = _preflight(payload, {"driving_behaviors": [counted_only]}, gate)
    assert (SHARED_OBJECT, "refuse") in _ids(report)
    assert (CONSENTED, "warn") not in _ids(report)


def test_a_proposal_over_an_unreadable_behaviour_cannot_be_minted_at_all():
    """No behaviour, no radius, no sentence for a human to have read."""
    record = _finished()
    tampered = json.loads(json.dumps(record))
    tampered["proposal"].pop("consent_radius")
    with pytest.raises(ValueError, match="no consent radius"):
        ps.grant_from_search(**tampered)

    assert ps.propose(
        result=record["result"], acceptance=record["acceptance"], behavior=None
    )["consent_radius"] is None


# ===========================================================================
# 3. The two species do not mix
# ===========================================================================


def test_a_table_5_6_grant_cannot_authorize_a_searched_write():
    record = _finished()
    payload = _payloads(record)[0]
    table_grant = sf.grant_from_proposals(_table_proposals(4))
    extra = {
        cfg.PROVENANCE_KEY: {
            "kind": cfg.PROVENANCE_KIND,
            "grant": table_grant,
            "consent": cfg.CONSUMED_APPROVAL,
        }
    }
    # Wrong kind AND wrong type: invisible to this species.
    assert sbg.evaluate(payload, _behavior(4), extra)["verdict"] == sbg.NO_GRANT
    # ...and the Table 5-6 species does not name the searched write either.
    assert cfg.evaluate(payload, _behavior(4), extra)["verdict"] == cfg.NOT_COVERED

    report = _preflight(payload, _shadow(4), _table_gate(_table_proposals(4)))
    assert report.blocking is True
    assert (SHARED_OBJECT, "refuse") in _ids(report)


def test_a_searched_grant_cannot_authorize_a_table_5_6_write():
    record = _finished()
    grant = _grant(record)
    table_payload = _table_payloads(_table_proposals(4))[0]
    extra = {
        sbg.PROVENANCE_KEY: {
            "kind": sbg.PROVENANCE_KIND,
            "grant": grant,
            "consent": sbg.CONSUMED_APPROVAL,
        }
    }
    assert sbg.evaluate(table_payload, _behavior(4), extra)["verdict"] == sbg.NOT_COVERED
    assert cfg.evaluate(table_payload, _behavior(4), extra)["verdict"] == cfg.NO_GRANT

    gate = PreapprovedWriteGate(
        {signature(table_payload["tool"], table_payload["params"])}, grant=grant
    )
    gate.authorize(table_payload, None)
    report = _preflight(table_payload, _shadow(4), gate)
    assert report.blocking is True
    assert (SHARED_OBJECT, "refuse") in _ids(report)


def test_each_species_announces_only_its_own_exception():
    """Both exceptions live on the same tool and the same key. A searched write must
    never produce the Table 5-6 sentence, and vice versa — the messages cite different
    evidence, and citing the wrong one would be a fabricated authority claim."""
    record = _finished()
    gate = _approved_gate(record)
    payload = _payloads(record)[0]
    gate.authorize(payload, None)
    ids = {constraint_id for constraint_id, _ in _ids(_preflight(payload, _shadow(4), gate))}
    assert CONSENTED in ids
    assert TABLE_CONSENTED not in ids and TABLE_DRIFTED not in ids

    table_proposals = _table_proposals(4)
    table_gate = _table_gate(table_proposals)
    table_payload = _table_payloads(table_proposals)[0]
    table_gate.authorize(table_payload, None)
    table_ids = {
        constraint_id
        for constraint_id, _ in _ids(_preflight(table_payload, _shadow(4), table_gate))
    }
    assert TABLE_CONSENTED in table_ids
    assert CONSENTED not in table_ids and DRIFTED not in table_ids


def test_one_gate_can_carry_both_species_through_a_chained_provider():
    """What a router wiring both brokers gets. The two signature spaces are disjoint by
    parameter, so at most one provider can ever answer for a given write."""
    record = _finished()
    grant = _grant(record)
    table_grant = sf.grant_from_proposals(_table_proposals(4))
    provider = chain_grant_providers(
        lambda action: table_grant if table_grant.covers(action) else None,
        lambda action: grant if grant.covers(action) else None,
        None,
    )

    searched_payload = _payloads(record)[0]
    table_payload = _table_payloads(_table_proposals(4))[0]
    assert provider(searched_payload) is grant
    assert provider(table_payload) is table_grant
    assert provider({"tool": sbg.GRANT_TOOL, "params": {"behavior_no": "2",
                                                        "parameter": "cc0", "value": 1.0}}) is None

    # A provider that raises is "no grant", never a failed turn — and it must not hide
    # the healthy arm behind it.
    def _explodes(action):
        raise RuntimeError("broker is down")

    assert chain_grant_providers(_explodes, lambda a: grant)(searched_payload) is grant


# ===========================================================================
# 4. The broker — the joint, and why no file can forge it
# ===========================================================================


def test_a_remembered_search_re_mints_a_grant_against_the_live_model():
    record = _finished()
    broker.set_active_session("sess-1")
    search_id = broker.remember(**record)
    assert search_id == "s-1"

    verdict = broker.mint(search_id, _shadow(4))
    assert verdict["reason"] == broker.GRANTED
    assert isinstance(verdict["grant"], sbg.SearchedBehaviourGrant)
    assert verdict["consented_radius"] == verdict["current_radius"]


def test_the_broker_never_answers_for_a_search_it_did_not_remember():
    """The whole of what a hand-edited ``pending_approval.json`` buys: nothing."""
    broker.set_active_session("sess-1")
    assert broker.recall("s-1") is None
    verdict = broker.mint("s-1", _shadow(4))
    assert verdict["grant"] is None and verdict["reason"] == broker.UNKNOWN_SEARCH
    assert broker.mint("s-1", _shadow(4), session_id=None)["reason"] in broker.MINT_REASONS


def test_a_hand_written_ledger_file_can_never_produce_a_grant(tmp_path: Path):
    """DECISIONS.md 2026-08-20's named failure mode, as a test.

    A perfectly self-consistent forged record is written to disk — the strongest form of
    the attack, one that would survive every cross-check ``grant_from_search`` performs —
    and it still buys nothing, because NO FILE IS AN INPUT. The broker holds only what
    this process remembered; a gate built from a file's ``search_id`` gets no grant, and
    the ordinary shared-object guardrail refuses.
    """
    record = _finished()
    forged = json.loads(json.dumps(record))
    forged["result"]["search_id"] = "forged-1"
    ledger_file = tmp_path / "search_state.json"
    ledger_file.write_text(json.dumps(forged), encoding="utf-8")
    pending = tmp_path / "pending_approval.json"
    pending.write_text(json.dumps({"searched_search_id": "forged-1"}), encoding="utf-8")

    broker.set_active_session("sess-1")
    named = json.loads(pending.read_text(encoding="utf-8"))["searched_search_id"]
    provider = broker.grant_provider(lambda: _shadow(4), session_id="sess-1", search_id=named)

    payload = _payloads(record)[0]
    assert provider(payload) is None
    gate = PreapprovedWriteGate(
        {signature(payload["tool"], payload["params"])}, grant_provider=provider
    )
    gate.authorize(payload, None)
    report = _preflight(payload, _shadow(4), gate)
    assert report.blocking is True
    assert (SHARED_OBJECT, "refuse") in _ids(report)

    # And the module itself never opens anything: the property above is structural, not
    # a fact about this particular test's file.
    source = Path(broker.__file__).read_text(encoding="utf-8")
    body = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    for forbidden in ("open(", "json.load", "json.dump", "write_text", "read_text", "Path("):
        assert forbidden not in body, forbidden


def test_the_store_is_an_island_no_reference_can_reach():
    """#47's refuter exploit, re-run: mutate what you handed in, or what you got back,
    and the grant must not move. These payloads are plain dicts, which callers pass
    around and mutate far more casually than dataclasses."""
    record = _finished()
    broker.set_active_session("sess-1")
    search_id = broker.remember(**record)
    before = broker.mint(search_id, _shadow(4))["grant"]

    record["proposal"]["actions"][0]["params"]["value"] = 9.9
    record["result"]["state"]["ledger"] = []
    handed_back = broker.recall(search_id)
    handed_back["proposal"]["actions"] = []

    after = broker.mint(search_id, _shadow(4))["grant"]
    assert after is not None
    assert after.signatures == before.signatures
    assert after.values == before.values


def test_one_session_cannot_spend_another_sessions_search():
    record = _finished()
    broker.set_active_session("sess-1")
    search_id = broker.remember(**record)

    broker.set_active_session("sess-2")
    assert broker.recall(search_id) is None
    assert broker.mint(search_id, _shadow(4))["reason"] == broker.UNKNOWN_SEARCH
    assert broker.search_id_covering(_payloads(record)[0]) is None

    broker.set_active_session("sess-1")
    assert broker.search_id_covering(_payloads(record)[0]) == search_id


def test_the_broker_refuses_to_remember_anything_it_could_not_mint():
    """Validation by ACTUALLY MINTING — not a second copy of the same rules."""
    record = _finished()
    broker.set_active_session("sess-1")
    tampered = json.loads(json.dumps(record))
    tampered["acceptance"]["code"] = ps.HOLDOUT_NOT_IMPROVED
    tampered["acceptance"]["accepted"] = False
    assert broker.remember(**tampered) is None
    assert broker.recall("s-1") is None


def test_the_radius_is_re_derived_at_mint_time_and_a_moved_one_gets_no_grant():
    record = _finished(_behavior(4))
    broker.set_active_session("sess-1")
    search_id = broker.remember(**record)

    moved = broker.mint(search_id, {"driving_behaviors": [_behavior(5)]})
    assert moved["grant"] is None and moved["reason"] == broker.RADIUS_DRIFTED

    truncated = broker.mint(search_id, {"driving_behaviors": [_behavior(4, truncated=True)]})
    assert truncated["grant"] is None
    assert truncated["reason"] in (broker.RADIUS_DRIFTED, broker.RADIUS_NOT_FULLY_NAMED)

    missing = broker.mint(search_id, {"driving_behaviors": []})
    assert missing["grant"] is None and missing["reason"] == broker.BEHAVIOR_NOT_IN_MODEL


def test_no_session_means_no_memory_and_no_grant():
    record = _finished()
    broker.reset()
    assert broker.remember(**record) is None
    assert broker.mint("s-1", _shadow(4))["reason"] == broker.NO_SESSION


def test_a_declined_card_forgets_its_search():
    record = _finished()
    broker.set_active_session("sess-1")
    search_id = broker.remember(**record)
    broker.forget(search_id)
    assert broker.mint(search_id, _shadow(4))["reason"] == broker.UNKNOWN_SEARCH


def test_the_provider_resolves_late_and_survives_a_model_read_that_raises():
    record = _finished()
    broker.set_active_session("sess-1")
    search_id = broker.remember(**record)

    def _explodes():
        raise RuntimeError("shadow model not parsed yet")

    provider = broker.grant_provider(_explodes, session_id="sess-1", search_id=search_id)
    assert provider(_payloads(record)[0]) is None

    live = broker.grant_provider(lambda: _shadow(4), session_id="sess-1")
    assert live(_payloads(record)[0]) is not None


# ===========================================================================
# 5. The forecast agrees with the engine — both species, both paths
# ===========================================================================


def _engine_refuses(payload: dict[str, Any], behavior: dict[str, Any], gate: Any) -> bool:
    report = _preflight(payload, {"driving_behaviors": [behavior]}, gate)
    return report.blocking


@pytest.mark.parametrize(
    "behavior",
    [
        _behavior(0),
        _behavior(1),
        _behavior(4),
        _behavior(4, truncated=True),
        {"no": "2", "reference_count": 3},
    ],
    ids=["unreferenced", "one-consumer", "four-named", "truncated", "counted-only"],
)
def test_the_forecast_never_says_clean_where_the_engine_refuses(behavior):
    """#44's refuter invariant, on this unit's grant species and on BOTH paths.

    A forecast may be pessimistic; it may never be optimistic. Every cell here runs the
    REAL engine with that path's own provenance and compares.
    """
    record = _finished(behavior)
    forecast = ps.preflight_forecast(behavior)
    payload = _payloads(record)[0]

    direct = forecast["paths"][ps.PATH_DIRECT_EDIT]
    direct_refused = _engine_refuses(payload, behavior, None)
    assert direct["expected_to_refuse"] or not direct_refused, "direct forecast was optimistic"
    assert direct["expected_to_refuse"] == direct_refused

    gate = _approved_gate(record)
    gate.authorize(payload, None)
    approved = forecast["paths"][ps.PATH_APPROVED_SET]
    approved_refused = _engine_refuses(payload, behavior, gate)
    assert approved["expected_to_refuse"] or not approved_refused, (
        "approved-path forecast was optimistic"
    )
    assert approved["expected_to_refuse"] == approved_refused


def test_the_forecasts_top_level_verdict_still_describes_the_direct_path():
    """Existing readers of ``verdict`` asked "what happens if this write is issued on its
    own". #51-b did not change that answer, it added the other path beside it."""
    assert ps.preflight_forecast(_behavior(4))["verdict"] == ps.GUARDRAIL_WILL_REFUSE
    assert ps.preflight_forecast(_behavior(0))["verdict"] == ps.GUARDRAIL_CLEAR
    assert ps.preflight_forecast(None)["verdict"] == ps.GUARDRAIL_UNKNOWN
    # UNKNOWN is pessimistic on BOTH paths — with no behaviour there is no radius.
    unknown = ps.preflight_forecast(None)["paths"]
    assert unknown[ps.PATH_DIRECT_EDIT]["expected_to_refuse"] is True
    assert unknown[ps.PATH_APPROVED_SET]["expected_to_refuse"] is True


def test_the_approved_path_names_the_two_constraints_it_will_meet():
    approved = ps.preflight_forecast(_behavior(4))["paths"][ps.PATH_APPROVED_SET]
    assert approved["expected_to_refuse"] is False
    assert approved["constraint_id"] == CONSENTED
    assert approved["refuses_if_radius_drifts"] == DRIFTED
    for language in ("ko", "en", "ja"):
        assert approved["why"][language].strip()


def test_the_resolution_now_reads_decided_and_localization_stays_rejected():
    """#51 offered two human calls. 2026-08-20 answered: (a) taken, (b) rejected. The
    forecast reports the state of the decision, and still auto-selects neither."""
    resolutions = {item["key"]: item for item in ps.preflight_forecast(_behavior(4))["resolutions"]}
    assert set(resolutions) == {"widen_exception", "localize_duplicate"}
    assert all(item["auto_selectable"] is False for item in resolutions.values())

    widen = resolutions["widen_exception"]
    assert widen["status"] == "DECIDED" and widen["landed_in"] == "#51-b"
    assert widen["decision"] == ps.DECISION_2026_08_20
    localize = resolutions["localize_duplicate"]
    assert localize["status"] == "REJECTED_BY_USER"
    for item in resolutions.values():
        for language in ("ko", "en", "ja"):
            assert item[language].strip()


# ===========================================================================
# 6. The rationale carries the search's own provenance
# ===========================================================================


def test_the_rationale_carries_the_search_provenance_and_its_own_evidence_rank():
    record = _finished()
    reasons = record["proposal"]["rationale"]
    ledger = ps.ledger_reference(record["result"])
    for language in ("ko", "en", "ja"):
        text = reasons[language]
        assert str(record["result"]["candidates"]) in text, language   # 후보 수
        assert ledger["digest"] in text, language                      # 원장 참조
        assert ledger["search_id"] in text, language
        assert "99" in text, language                                  # 홀드아웃 seed
        assert "0.25-0.45" in text, language                           # MRWA 범위 X–Y
        assert sbg.EVIDENCE_RANK[language] in text, language           # 근거의 격


def test_the_ledger_reference_moves_when_the_ledger_does():
    """A citation aid that never changed would be worse than none."""
    record = _finished()
    first = ps.ledger_reference(record["result"])
    mutated = json.loads(json.dumps(record["result"]))
    mutated["state"]["ledger"][0]["objective"] = 99.0
    assert ps.ledger_reference(mutated)["digest"] != first["digest"]
    assert first["rows"] == len(record["result"]["state"]["ledger"])


def test_every_verdict_code_is_reachable_and_every_name_in_dunder_all_exists():
    record = _finished()
    grant = _grant(record)
    payload = _payloads(record)[0]
    behavior = _behavior(4)

    def _extra(consent=sbg.CONSUMED_APPROVAL, target=grant):
        return {sbg.PROVENANCE_KEY: {"kind": sbg.PROVENANCE_KIND, "grant": target,
                                     "consent": consent}}

    # RADIUS_NOT_FULLY_NAMED needs a grant whose OWN radius was never whole and a live
    # model that still matches it — an incomplete radius that also MOVED is reported as
    # the drift, which is the more specific of the two (it quotes both sides).
    blind = _behavior(4, truncated=True)
    blind_grant = _grant(_finished(blind, search_id="s-blind"))

    seen = {
        sbg.evaluate(payload, behavior, {})["verdict"],
        sbg.evaluate({"tool": sbg.GRANT_TOOL, "params": {"behavior_no": "9",
                                                         "parameter": "lat_dist_driv_def",
                                                         "value": 0.5}},
                     behavior, _extra())["verdict"],
        sbg.evaluate(payload, behavior, _extra(consent="NOPE"))["verdict"],
        sbg.evaluate(payload, _behavior(5), _extra())["verdict"],
        sbg.evaluate(payload, blind, _extra(target=blind_grant))["verdict"],
        sbg.evaluate(payload, behavior, _extra())["verdict"],
    }
    assert seen == {
        sbg.NO_GRANT,
        sbg.NOT_COVERED,
        sbg.NOT_CONSENTED,
        sbg.RADIUS_DRIFTED,
        sbg.RADIUS_NOT_FULLY_NAMED,
        sbg.GRANTED,
    }
    assert sbg.band_verdict({"params": {"parameter": "cc0", "value": 1.0}}) == (
        sbg.PARAMETER_NOT_SEARCHABLE
    )
    assert sbg.band_verdict({"params": {"parameter": "lat_dist_driv_def", "value": 9.9}}) == (
        sbg.VALUE_OUT_OF_PUBLISHED_RANGE
    )
    assert set(sbg.VERDICTS) >= seen
    for name in sbg.__all__:
        assert hasattr(sbg, name), name
    for name in broker.__all__:
        assert hasattr(broker, name), name

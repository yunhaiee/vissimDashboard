"""Unit #50: the 13-step flow rail's status derivation, step by step.

The rail is the calibration workspace's skeleton, and the ONE thing it must
never do is show a step as finished when nothing says so. So this file is
organised around that: for each step with an evidence source there is a test
for evidence-present, a test for evidence-absent, and — where the network can
prove a step does not apply — a test for that too. On top of them sits a
GLOBAL pin (:func:`test_no_step_reaches_done_from_an_empty_bundle`) which walks
the whole rail on an empty bundle and asserts that not one of the thirteen
steps reached a positive status. That test is the false-completion guard; the
per-step tests exist so that when it fails, the failure has an address.

Two traps are pinned here in particular, because both produce a GREEN rail on
an uncalibrated network if the obvious rule is written:

* **The Vissim default coincidence.** Stock Wiedemann-74 carries
  ``bx_add=2.00, bx_mult=3.00``, which IS MRWA Table 5-6's 2050 pcu row, so
  ``verify_applied_row`` answers ``MATCHES_PUBLISHED_ROW`` on a network nobody
  has touched. Steps 7 and 12 must not read that as an application.
* **Silence as success.** A reality check that raises no route-choice proposal
  has not confirmed route choice; it has said nothing about it. Step 11 must
  report that as NO_EVIDENCE with a sentence, never as DONE.
"""
from __future__ import annotations

import pytest

from services import calibration_rail
from services.agent import geh_ladder, mrwa_reference, parameter_search, saturation_flow
from services.genesis import gate


# ===========================================================================
# Helpers — the evidence bundles, built the shape the router gathers them
# ===========================================================================

#: The gate's own word for a pass, never a literal. REFUTER 2026-08-19: these
#: bundles used to say ``"passed"``, which ``GateVerdict`` cannot emit, so every
#: test that "proved" the gate branch was actually proving the fallback. Read
#: from the enum, exactly as ``calibration_rail`` now does — the two cannot
#: disagree, and :func:`test_step_1_reads_the_gates_own_pass_word_not_a_hand_typed_one`
#: proves the enum itself is what a real passing gate produces.
_PASSED_VERDICT = gate.GateVerdict.PASS.value


def _rail(**evidence):
    return {step["number"]: step for step in calibration_rail.derive_rail(evidence)["steps"]}


def _behavior(no: str, ax, bx_add, bx_mult, *, name: str = "b"):
    """One entry of the ``applied_rows`` bundle, with the REAL verdict inside.

    The verdict is produced by ``saturation_flow.verify_applied_row`` rather
    than hand-written, so these tests cannot drift from #44's own answers — if
    that function changes its verdict for a parameter triple, the rail's tests
    move with it instead of pinning a stale copy.
    """
    return {
        "behavior_no": no,
        "behavior_name": name,
        "verdict": saturation_flow.verify_applied_row(
            {"ax": ax, "bx_add": bx_add, "bx_mult": bx_mult}
        ),
    }


#: Vissim's own Wiedemann-74 defaults. ``ax`` is 2.0 m — NOT §5.2.5.1's 2.5 m —
#: while the safety-distance pair coincides exactly with a published row.
_VISSIM_DEFAULT = (2.0, 2.00, 3.00)
#: The 1950 pcu row of Table 5-6 plus the guideline standstill distance: what
#: #44's proposal set actually writes.
_GUIDELINE_1950 = (2.5, 2.25, 3.25)


def _reality_check(*, nodes, by_rung=None, compared=None, status="CHECKED"):
    return {
        "status": status,
        "reason_code": None,
        "nodes": nodes,
        "diagnosis": {"by_rung": by_rung or {}},
        "coverage": {"counts": {"compared": compared if compared is not None else len(nodes)}},
    }


def _node(node_id, *, outcome="REFUSAL", scope="NOT_APPLICABLE", entrance=None):
    evidence = {}
    if entrance is not None:
        evidence["target"] = {"entrance_measurement": {"status": entrance}}
    return {
        "node_id": node_id,
        "node_name": f"node {node_id}",
        "outcome": {"outcome": outcome, "scope": scope, "evidence": evidence},
    }


# ===========================================================================
# The global guard — the reason this whole screen is trustworthy
# ===========================================================================


def test_no_step_reaches_done_from_an_empty_bundle():
    """Thirteen steps, no evidence, and not one tick.

    The single most important assertion in this file. A rail that decorated an
    empty workspace with completed steps would be worse than no rail: it would
    be a consultant-facing claim that calibration happened, produced by a
    screen that read nothing.
    """
    rail = calibration_rail.derive_rail({})
    assert len(rail["steps"]) == 13
    for step in rail["steps"]:
        assert step["status"] == calibration_rail.NO_EVIDENCE, (
            f"step {step['number']} ({step['label']}) reached {step['status']} on an empty "
            "bundle — no evidence may produce anything but NO_EVIDENCE"
        )
    assert rail["counts"][calibration_rail.NO_EVIDENCE] == 13
    assert rail["counts"][calibration_rail.DONE] == 0


def test_every_step_carries_a_basis_sentence_in_three_languages():
    """A status with no sentence behind it is exactly the decoration this
    screen exists to refuse — so the payload cannot produce one."""
    rail = calibration_rail.derive_rail({})
    for step in rail["steps"]:
        for language in ("ko", "en", "ja"):
            assert step["basis"][language].strip(), (
                f"step {step['number']} has no {language} basis sentence"
            )
        assert step["source"], f"step {step['number']} names no evidence source"


def test_the_rail_is_the_guidelines_own_order_and_labels():
    rail = calibration_rail.derive_rail({})
    assert [step["number"] for step in rail["steps"]] == list(range(1, 14))
    assert [step["label"] for step in rail["steps"]] == [
        label for _n, label, _c in mrwa_reference.CALIBRATION_STEPS_2_12_1
    ]
    assert rail["citation"] == mrwa_reference.CALIBRATION_STEPS_CITATION
    # ko/ja are TRANSLATIONS and the payload says so, so nobody downstream can
    # quote them back as the authority's words.
    for step in rail["steps"]:
        assert "translation" in step["label_source"]


def test_the_status_vocabulary_has_exactly_five_words():
    assert calibration_rail.STATUSES == (
        calibration_rail.DONE,
        calibration_rail.IN_PROGRESS,
        calibration_rail.NEXT,
        calibration_rail.NOT_APPLICABLE,
        calibration_rail.NO_EVIDENCE,
    )
    rail = calibration_rail.derive_rail({})
    assert set(rail["counts"]) == set(calibration_rail.STATUSES)


# ===========================================================================
# The seven steps with no evidence source — permanently honest
# ===========================================================================


@pytest.mark.parametrize("number", calibration_rail.STEPS_WITHOUT_EVIDENCE_SOURCE)
def test_a_manual_area_step_stays_no_evidence_whatever_else_is_in_the_bundle(number):
    """Fill the bundle with every artifact this product has; ②③④⑤⑧⑬ do not move.

    They are not "not started" and not "unknown" — they are MANUAL-CHECK areas,
    and the rail says so rather than leaving a gap a reader fills in with an
    assumption. (Step 9 left this list in #50's follow-up and is pinned
    separately below — see the "Step 9" section.)
    """
    rail = _rail(
        gate={"verdict": _PASSED_VERDICT, "counts": {"pass": 9, "fail": 0}},
        health={"findings": []},
        phase_diagrams=[{"confirmed": True}],
        signal_controller_count=4,
        pt_line_count=3,
        applied_rows=[_behavior("1", *_GUIDELINE_1950)],
        reality_check=_reality_check(nodes=[_node("5", entrance="measured")]),
    )
    step = rail[number]
    assert step["status"] == calibration_rail.NO_EVIDENCE
    assert step["manual_area"] is True
    assert "수동 확인" in step["basis"]["ko"]


def test_manual_area_list_matches_the_steps_with_no_deriver():
    """The data and the branches cannot drift apart: a step is a manual area if
    and only if nothing in this module derives it."""
    derived = set(calibration_rail._DERIVERS)
    manual = set(calibration_rail.STEPS_WITHOUT_EVIDENCE_SOURCE)
    assert derived & manual == set()
    assert derived | manual == set(range(1, 14))


# ===========================================================================
# Step 1 — basic network coding
# ===========================================================================


def test_step_1_done_from_a_passed_generation_gate():
    step = _rail(gate={"verdict": _PASSED_VERDICT, "counts": {"pass": 9, "fail": 0}})[1]
    assert step["status"] == calibration_rail.DONE
    assert step["source"] == "genesis_gate"
    assert "V1" in step["basis"]["ko"]


def test_step_1_reads_the_gates_own_pass_word_not_a_hand_typed_one():
    """REFUTER 2026-08-19 — the regression that made the branch above dead.

    The rail compared the verdict against the literal ``"passed"``, which
    ``GateVerdict`` cannot produce, so a generation that passed V1–V9 came back
    ``IN_PROGRESS`` under the sentence "생성 게이트 판정: pass". The tests above
    hard-coded the same wrong literal and therefore agreed with the bug.

    This test does not name any string. It builds a REAL all-passing
    ``GateResult`` out of ``gate.py``'s own machinery and feeds the exact two
    fields ``genesis_jobs.gate_payload`` puts on the wire, so the only way to
    break it again is to break the producer too.
    """
    checks = [gate.CheckResult.build(check_id, tier) for check_id, tier in gate._CHECK_TIERS.items()]
    result = gate.GateResult(
        network="refuter.inpx",
        network_sha256="0" * 64,
        verdict=gate._verdict(checks),
        checks=checks,
    )
    assert result.verdict is gate.GateVerdict.PASS, "fixture no longer builds a passing gate"

    wire = {"verdict": result.verdict.value, "counts": result.counts()}
    step = _rail(gate=wire)[1]
    assert step["status"] == calibration_rail.DONE, (
        f"a gate that passed V1–V9 must tick step ①; got {step['status']} / {step['basis']['en']}"
    )
    assert step["source"] == "genesis_gate"
    assert step["detail"]["verdict"] == result.verdict.value


def test_step_1_in_progress_from_a_refused_gate_never_done():
    step = _rail(
        gate={"verdict": "refused", "counts": {"pass": 2, "fail": 2, "not_checkable": 5}}
    )[1]
    assert step["status"] == calibration_rail.IN_PROGRESS
    assert "refused" in step["basis"]["en"]


def test_step_1_falls_back_to_the_model_inspection_when_no_gate_exists():
    """A consultant .inpx nobody generated here still has an evidence source."""
    clean = _rail(health={"findings": []})[1]
    assert clean["status"] == calibration_rail.DONE
    assert clean["source"] == "health_check"

    dirty = _rail(health={"findings": [{"id": "C1"}, {"id": "C2"}]})[1]
    assert dirty["status"] == calibration_rail.IN_PROGRESS
    assert dirty["detail"]["findings"] == 2


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"summary": {"checked_n": 40}}, id="findings-key-missing"),
        pytest.param({"findings": {"C1": 3}}, id="findings-as-a-mapping"),
        pytest.param({"findings": "three findings"}, id="findings-as-a-string"),
        pytest.param({"findings": None}, id="findings-null"),
    ],
)
def test_step_1_never_reads_an_unreadable_inspection_as_zero_findings(payload):
    """REFUTER 2026-08-19 — DONE from an ABSENCE, the one branch the module
    docstring swears does not exist.

    ``_seq`` answers ``[]`` for everything that is not a sequence, so a health
    payload whose findings list was renamed, nulled, or shaped differently read
    as "the model inspection reports zero findings" and ticked step ① green.
    An inspection nobody could read is MISSING EVIDENCE — the same answer as no
    inspection at all — and it must say which of the two it is.
    """
    step = _rail(health=payload)[1]
    assert step["status"] == calibration_rail.NO_EVIDENCE
    assert step["status"] not in calibration_rail.POSITIVE_STATUSES
    assert step["source"] == "health_check"
    # And it must not make the DONE branch's claim — "reports zero findings" —
    # about a list it never read. Saying zero is not the same as counting zero.
    assert "reports zero findings" not in step["basis"]["en"]
    assert "no readable findings list" in step["basis"]["en"]


def test_step_1_prefers_the_gate_over_the_inspection():
    """Both present: the gate wins, because it is the verdict that decided
    whether the artifact was allowed to be published at all."""
    step = _rail(
        gate={"verdict": "refused", "counts": {"pass": 1, "fail": 3, "not_checkable": 5}},
        health={"findings": []},
    )[1]
    assert step["status"] == calibration_rail.IN_PROGRESS
    assert step["source"] == "genesis_gate"


def test_step_1_no_evidence_when_neither_artifact_exists():
    step = _rail()[1]
    assert step["status"] == calibration_rail.NO_EVIDENCE


# ===========================================================================
# Step 6 — signalised intersections (C1-b attestation)
# ===========================================================================


def test_step_6_done_when_every_phase_diagram_is_attested():
    step = _rail(phase_diagrams=[{"confirmed": True}, {"confirmed": True}])[6]
    assert step["status"] == calibration_rail.DONE
    assert step["detail"] == {"reviews": 2, "confirmed": 2, "signal_controllers": None}


def test_step_6_in_progress_when_only_some_are_attested():
    step = _rail(phase_diagrams=[{"confirmed": True}, {"confirmed": False}])[6]
    assert step["status"] == calibration_rail.IN_PROGRESS


def test_step_6_next_when_reviews_exist_and_none_is_attested():
    """A stale confirmation reads as unconfirmed — the store already collapses
    "nobody confirmed" and "the confirmation went stale" into ``confirmed:
    False``, and the rail does not second-guess it."""
    step = _rail(phase_diagrams=[{"confirmed": False, "stale_reasons": ["이미지가 바뀜"]}])[6]
    assert step["status"] == calibration_rail.NEXT


def test_step_6_not_applicable_only_when_the_network_proves_it_has_no_signals():
    """The one 해당 없음 the rail can honestly reach: a parsed network with
    zero signal controllers."""
    step = _rail(phase_diagrams=[], signal_controller_count=0)[6]
    assert step["status"] == calibration_rail.NOT_APPLICABLE
    assert step["detail"]["signal_controllers"] == 0


def test_step_6_no_evidence_when_signals_exist_but_nobody_reviewed_a_phase_diagram():
    """The failure mode this branch exists for: signals present, no attestation
    — which must NOT collapse into the same answer as "no signals"."""
    step = _rail(phase_diagrams=[], signal_controller_count=4)[6]
    assert step["status"] == calibration_rail.NO_EVIDENCE
    assert "4" in step["basis"]["ko"]


def test_step_6_no_evidence_when_the_network_could_not_be_read():
    step = _rail(phase_diagrams=[])[6]
    assert step["status"] == calibration_rail.NO_EVIDENCE


# ===========================================================================
# Step 9 — public transport (#50 follow-up, 2026-08-19 user decision)
#
# Narrowed from "always NO_EVIDENCE" to step 6's own rule: a PROVABLE zero
# earns NOT_APPLICABLE; a positive count or an absent count both stay
# NO_EVIDENCE, because this product has no artifact anywhere that attests a
# PT line was actually calibrated — a network WITH PT lines must never reach
# anything better than NO_EVIDENCE on this evidence alone.
# ===========================================================================


def test_step_9_not_applicable_only_when_the_network_proves_it_has_no_pt_lines():
    step = _rail(pt_line_count=0)[9]
    assert step["status"] == calibration_rail.NOT_APPLICABLE
    assert step["source"] == "shadow_model.pt_lines"
    assert step["detail"]["pt_lines"] == 0


def test_step_9_no_evidence_when_pt_lines_are_present():
    """The trap this unit exists to avoid: PT lines present is NOT evidence
    that they were calibrated, so this must not tick past NO_EVIDENCE."""
    step = _rail(pt_line_count=3)[9]
    assert step["status"] == calibration_rail.NO_EVIDENCE
    assert "3" in step["basis"]["ko"]
    assert step["detail"]["pt_lines"] == 3


def test_step_9_no_evidence_when_pt_line_count_is_absent():
    step = _rail()[9]
    assert step["status"] == calibration_rail.NO_EVIDENCE
    assert step["detail"]["pt_lines"] is None


def test_step_9_is_no_longer_a_manual_area():
    step = _rail(pt_line_count=3)[9]
    assert step["manual_area"] is False
    assert 9 not in calibration_rail.STEPS_WITHOUT_EVIDENCE_SOURCE


# ===========================================================================
# Steps 7 and 12 — Table 5-6, and the default-coincidence trap
# ===========================================================================


def test_the_vissim_default_really_does_sit_on_a_published_row():
    """The premise the next two tests defend. Measured, not assumed: if this
    ever stops being true the guard below is over-strict and should be
    revisited — but silently, it never can, because this fails first."""
    verdict = saturation_flow.verify_applied_row(
        {"ax": _VISSIM_DEFAULT[0], "bx_add": _VISSIM_DEFAULT[1], "bx_mult": _VISSIM_DEFAULT[2]}
    )
    assert verdict["status"] == saturation_flow.ROW_MATCHED
    assert verdict["row"]["saturation_flow_pcu"] == 2050.0
    assert verdict["standstill_matches_guideline"] is False
    assert calibration_rail._DEFAULT_COINCIDENT_ROW_PCU == 2050.0


def test_step_7_refuses_to_call_the_vissim_default_an_applied_measurement():
    """The false-completion pin for step 7.

    An untouched network matches a published row. If the rail read that as "a
    measured saturation flow was submitted and applied", every fresh upload in
    the product would show step 7 green.
    """
    step = _rail(applied_rows=[_behavior("1", *_VISSIM_DEFAULT)])[7]
    assert step["status"] == calibration_rail.NO_EVIDENCE
    assert "2050" in step["basis"]["ko"]
    assert step["detail"]["default_coincident"] == 1


def test_step_7_done_only_with_the_guideline_standstill_distance():
    step = _rail(applied_rows=[_behavior("1", *_GUIDELINE_1950)])[7]
    assert step["status"] == calibration_rail.DONE
    assert "1950" in step["basis"]["en"]
    assert step["citation"] == mrwa_reference.TABLE_5_6_CITATION


def test_step_7_next_when_no_behaviour_sits_on_any_published_row():
    step = _rail(applied_rows=[_behavior("1", 2.5, 2.25, 3.75)])[7]
    assert step["status"] == calibration_rail.NEXT
    assert step["detail"]["off_published_row"] == 1


def test_step_7_no_evidence_when_no_behaviour_could_be_read():
    unreadable = {"behavior_no": "1", "behavior_name": "b", "verdict": saturation_flow.verify_applied_row({})}
    assert unreadable["verdict"]["status"] == saturation_flow.ROW_UNREADABLE
    assert _rail(applied_rows=[unreadable])[7]["status"] == calibration_rail.NO_EVIDENCE
    assert _rail()[7]["status"] == calibration_rail.NO_EVIDENCE


def test_step_12_names_a_partially_applied_set_rather_than_ticking():
    """#44's headline detection, surfaced on the rail.

    One behaviour on the guideline row and one carrying two halves of different
    rows: the set is partially applied, and step 12 must say so instead of
    reporting the half that landed as a completed step.
    """
    step = _rail(
        applied_rows=[
            _behavior("1", *_GUIDELINE_1950),
            _behavior("2", 2.5, 2.25, 3.75),
        ]
    )[12]
    assert step["status"] == calibration_rail.IN_PROGRESS
    assert step["detail"]["off_published_row"] == 1
    assert step["detail"]["guideline_standstill"] == 1


def test_step_12_next_when_nothing_carries_the_guideline_set():
    """An untouched network: every behaviour on the default-coincident row.
    Step 12 says the work remains — it does not say it is done."""
    step = _rail(applied_rows=[_behavior("1", *_VISSIM_DEFAULT)])[12]
    assert step["status"] == calibration_rail.NEXT
    assert "0" in step["basis"]["ko"]


def test_step_12_done_only_when_every_readable_behaviour_is_on_a_published_row():
    step = _rail(
        applied_rows=[_behavior("1", *_GUIDELINE_1950), _behavior("2", 2.5, 2.75, 3.75)]
    )[12]
    assert step["status"] == calibration_rail.DONE
    assert step["detail"]["readable"] == 2


def test_step_12_carries_the_locked_unit_51_track_and_promises_nothing():
    step = _rail()[12]
    track = step["tracks"][0]
    assert track["locked"] is True
    assert track["unit"] == "#51"
    for language in ("ko", "en", "ja"):
        assert track["why"][language].strip()


def test_only_step_12_carries_a_second_track():
    rail = calibration_rail.derive_rail({})
    with_tracks = [step["number"] for step in rail["steps"] if "tracks" in step]
    assert with_tracks == [12]


# ---------------------------------------------------------------------------
# Unit #51 — the second track's lock is the precondition gate, not a constant
# ---------------------------------------------------------------------------


def _search_evidence(*, entrance="measured", rung="CAPACITY_LIMIT", signals="DONE", nodes=6):
    """A bundle that opens #51's gate, with one knob per way of shutting it."""
    node_ids = [str(n) for n in range(1, nodes + 1)]
    return {
        "reality_check": {
            "status": "CHECKED",
            "geh_threshold": 5.0,
            "nodes": [
                {
                    "node_id": node_id,
                    "node_total_geh": 8.0,
                    # The REAL place a D4 verdict rides — the same one this
                    # file's own ``_node`` helper uses. It said
                    # ``evidence["entrance"]`` until 2026-08-23, a key nothing
                    # writes, which is why the ⑫ track's ENTRANCE_NOT_MEASURED
                    # assertion below passed against a gate leg that could not
                    # fire on a real report.
                    "outcome": {
                        "evidence": {"target": {"entrance_measurement": {"status": entrance}}}
                    },
                    "diagnosis": {"rung": rung, "missing_evidence": []},
                }
                for node_id in node_ids
            ],
        },
        "applied_rows": [_behavior("1", *_GUIDELINE_1950)],
        "signal_controller_count": 1,
        # Step 6 reads DONE from the phase-diagram evidence the rail already uses.
        "phase_diagrams": [{"confirmed": True}] if signals == "DONE" else [],
    }


def _search_track(evidence):
    return calibration_rail.derive_rail(evidence)["steps"][11]["tracks"][0]


def test_the_search_track_is_locked_with_named_refusals_when_the_gate_is_shut():
    track = _search_track({})
    assert track["locked"] is True
    assert track["card"] is None
    # No reality check at all: the track still says what would open it, which is
    # the sentence a modeller can act on.
    for language in ("ko", "en", "ja"):
        assert track["why"][language].strip()


def test_a_shut_gate_surfaces_the_precondition_codes_on_the_track():
    track = _search_track(_search_evidence(entrance="unverified"))
    codes = {refusal["code"] for refusal in track["refusals"]}
    assert parameter_search.ENTRANCE_NOT_MEASURED in codes
    for refusal in track["refusals"]:
        for language in ("ko", "en", "ja"):
            assert refusal["why"][language].strip()
            assert refusal["opens_when"][language].strip()


def test_too_few_nodes_locks_the_track_on_the_holdout_principle():
    track = _search_track(_search_evidence(nodes=2))
    assert track["locked"] is True
    assert parameter_search.HOLDOUT_NOT_AVAILABLE in {
        refusal["code"] for refusal in track["refusals"]
    }


def test_the_track_unlocks_with_an_ask_first_card_when_the_ladder_is_exhausted():
    track = _search_track(_search_evidence())
    assert track["locked"] is False
    assert track["refusals"] == []
    card = track["card"]
    assert card["ask_first"] is True and card["auto_start"] is False
    assert card["plan"]["holdout_nodes"] and card["plan"]["calibration_nodes"]


def test_the_track_states_the_guardrail_and_the_users_decision_in_both_states():
    """Unit #51-c: the collision's resolution is a fact about the product, so it rides
    on the TRACK — locked or open — rather than only on a card a search has to produce.

    A locked track is what this screen shows most of the time, and it is where a reader
    learns that the door, once opened, leads somewhere: refused if the write is issued
    directly, clean through the approval machinery. Nothing here forecasts: no behaviour
    has been chosen at this point, so the approved arm carries a CONDITION.
    """
    for evidence, expected_lock in ((_search_evidence(nodes=2), True), (_search_evidence(), False)):
        track = _search_track(evidence)
        assert track["locked"] is expected_lock
        guardrail = track["guardrail"]
        assert guardrail == parameter_search.guardrail_policy()
        assert guardrail["paths"][parameter_search.PATH_DIRECT_EDIT]["expected_to_refuse"] is True
        assert (
            guardrail["paths"][parameter_search.PATH_APPROVED_SET]["expected_to_refuse"] is None
        )
        statuses = {item["key"]: item["status"] for item in guardrail["resolutions"]}
        assert statuses == {"widen_exception": "DECIDED", "localize_duplicate": "REJECTED_BY_USER"}

    # ... and on the degenerate bundle too, where there is no reality check to read.
    assert _search_track({})["guardrail"]["decision"] == parameter_search.DECISION_2026_08_20


def test_the_track_reads_this_rails_own_step_6_rather_than_a_second_opinion():
    """One screen, one verdict about signals: the gate is handed the steps this
    rail just built, so the ⑥ chip and the ⑫ lock cannot disagree."""
    rail = calibration_rail.derive_rail(_search_evidence(signals="MISSING"))
    step_6 = rail["steps"][5]
    track = rail["steps"][11]["tracks"][0]
    assert step_6["status"] != calibration_rail.DONE
    assert parameter_search.SIGNAL_STEP_UNVERIFIED in {
        refusal["code"] for refusal in track["refusals"]
    }


# ===========================================================================
# Steps 10 and 11 — what the run's own reality check says
# ===========================================================================


def test_step_10_next_when_a_demand_proposal_is_waiting():
    """The D4 demo scene's own shape: one node refuses (the model reproduces
    the counts), one proposes a demand edit off an unverified entrance."""
    step = _rail(
        reality_check=_reality_check(
            nodes=[
                _node("5", outcome="REFUSAL"),
                _node("8", outcome="PROPOSAL", scope="DEMAND_LEVEL", entrance="unverified"),
            ]
        )
    )[10]
    assert step["status"] == calibration_rail.NEXT
    assert step["detail"] == {
        "demand_proposals": 1,
        "entrance_measured": 0,
        "entrance_unverified": 1,
        "nodes_compared": 2,
    }


def test_step_10_says_unverified_never_unmeasured():
    """D4's own doctrine, carried onto the rail: the two are not the same claim
    and this product is not permitted to make the second one."""
    step = _rail(
        reality_check=_reality_check(nodes=[_node("8", entrance="unverified")])
    )[10]
    assert step["status"] == calibration_rail.IN_PROGRESS
    assert "확인되지 않" in step["basis"]["ko"]
    assert "unmeasured" in step["basis"]["en"]


def test_step_10_done_only_with_a_measured_entrance_and_no_pending_proposal():
    step = _rail(reality_check=_reality_check(nodes=[_node("8", entrance="measured")]))[10]
    assert step["status"] == calibration_rail.DONE


def _displaced_node(node_id, *, entrance="measured"):
    """The shape ``fixes.map_node_counts`` arm (b) really produces.

    When the entrance IS measured, D4's refusal is HELD rather than returned:
    grading continues into the movement split, a routing proposal is emitted,
    and the refusal — with its verdict — rides on that proposal's
    ``also_refused``. So the verdict is NOT under the outcome's own
    ``evidence.target``; the outcome's target there is the ROUTING one.
    """
    return {
        "node_id": node_id,
        "node_name": f"node {node_id}",
        "outcome": {
            "outcome": "PROPOSAL",
            "scope": "ROUTING_SPLIT",
            "evidence": {
                "node_id": node_id,
                "target": {"decision_no": "2", "route_no": "1"},
            },
            "also_refused": {
                "outcome": "REFUSAL",
                "reason_code": "INPUT_VOLUME_IS_OBSERVED_DATA",
                "evidence": {"target": {"entrance_measurement": {"status": entrance}}},
            },
        },
    }


def test_step_10_sees_a_measured_verdict_a_routing_proposal_displaced():
    """REGRESSION (refuter, 2026-08-24). This step spelled its own lookup and
    only ever looked at ``outcome.evidence.target``, so a MEASURED verdict that
    arm (b) moved onto ``also_refused`` was invisible.

    The damage was not a missing number, it was a FALSE CLAIM: with this as the
    only graded node the step fell through to "no entrance carries a D4 measured
    verdict either" — about a report that carries exactly one.
    """
    step = _rail(reality_check=_reality_check(nodes=[_displaced_node("8")]))[10]
    assert step["detail"]["entrance_measured"] == 1
    assert step["status"] == calibration_rail.DONE
    assert "no entrance carries a D4 measured verdict" not in step["basis"]["en"]
    assert "한 건도 없습니다" not in step["basis"]["ko"]


def test_step_10_counts_displaced_and_undisplaced_verdicts_alike():
    """The mixed case, where the old reader did not lie outright — it just said
    one where the truth was two."""
    step = _rail(
        reality_check=_reality_check(
            nodes=[_node("5", entrance="measured"), _displaced_node("8")]
        )
    )[10]
    assert step["detail"]["entrance_measured"] == 2
    assert step["detail"]["entrance_unverified"] == 0


def test_step_10_will_not_tick_when_nodes_merely_reproduce_the_counts():
    """No proposal is not the same fact as a verified entrance. A node that
    matches its counts says nothing about whether its INPUT was measured, so
    this may not reach DONE."""
    step = _rail(reality_check=_reality_check(nodes=[_node("5"), _node("6")]))[10]
    assert step["status"] == calibration_rail.IN_PROGRESS
    assert step["detail"]["entrance_measured"] == 0


def test_step_10_and_11_no_evidence_without_a_scored_run():
    for number in (10, 11):
        assert _rail()[number]["status"] == calibration_rail.NO_EVIDENCE
        not_checked = _rail(
            reality_check={"status": "NOT_CHECKABLE", "reason_code": "RUN_NOT_FOUND"}
        )[number]
        assert not_checked["status"] == calibration_rail.NO_EVIDENCE
        assert "RUN_NOT_FOUND" in not_checked["basis"]["en"]


def test_step_11_next_on_a_pending_route_split_proposal():
    step = _rail(
        reality_check=_reality_check(
            nodes=[_node("8", outcome="PROPOSAL", scope="ROUTING_SPLIT")]
        )
    )[11]
    assert step["status"] == calibration_rail.NEXT
    assert step["detail"]["routing_proposals"] == 1


def test_step_11_in_progress_from_the_diagnosis_ladders_own_rung():
    step = _rail(
        reality_check=_reality_check(nodes=[_node("8")], by_rung={"ROUTING_SPLIT": ["8"]})
    )[11]
    assert step["status"] == calibration_rail.IN_PROGRESS
    assert step["source"] == "geh_ladder"


def test_step_11_reports_silence_as_silence_not_as_success():
    """The second false-completion pin. A check that raised no route-choice
    finding has not confirmed route choice, and the sentence says exactly
    that."""
    step = _rail(reality_check=_reality_check(nodes=[_node("5")]))[11]
    assert step["status"] == calibration_rail.NO_EVIDENCE
    assert "증거는 아닙니다" in step["basis"]["ko"]


def test_the_rails_scope_tokens_are_the_proposal_layers_own_spellings():
    """This module quotes ``DEMAND_LEVEL``/``ROUTING_SPLIT`` rather than
    importing the proposal machinery. Pinned equal so a rename over there
    cannot leave the rail silently reading a scope nobody emits."""
    assert calibration_rail._SCOPE_DEMAND == geh_ladder.DEMAND_LEVEL
    assert calibration_rail._SCOPE_ROUTING == geh_ladder.ROUTING_SPLIT


def test_entrance_tokens_are_the_d4_modules_own():
    from services.agent import fixes

    assert calibration_rail._ENTRANCE_MEASURED == fixes.ENTRANCE_MEASURED
    assert calibration_rail._ENTRANCE_UNVERIFIED == fixes.ENTRANCE_UNVERIFIED


# ===========================================================================
# Navigation — the rail is a control, not only a board
# ===========================================================================


def test_every_step_names_a_section_to_scroll_to():
    rail = calibration_rail.derive_rail({})
    for step in rail["steps"]:
        assert step["anchor"].startswith("station-"), (
            f"step {step['number']} has no work area to click through to"
        )

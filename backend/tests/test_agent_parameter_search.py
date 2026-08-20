"""Unit #51 — the range-bounded parameter search.

The tests are grouped the way the module's own risks are:

1. the SEARCH SPACE is exactly what was measured (and the exclusions are named),
2. the PRECONDITION GATE refuses by name and cannot be talked past,
3. the ENGINE stays inside the published bands, ledgers everything, stops on
   budget and resumes exactly,
4. the HOLDOUT is structurally unreachable from the optimizer, and
5. the OUTPUT tells the truth about the guardrail that will refuse it.
"""

from __future__ import annotations

import json

import pytest

from services import validation
from services.agent import mrwa_reference, parameter_search as ps, searched_behaviour_grant


# ===========================================================================
# Fixtures — a synthetic GEH surface with a KNOWN optimum
# ===========================================================================

#: The true optimum of :func:`_surface`. Two parameters matter; the third is
#: deliberately INERT so the sensitivity screen has something real to reject.
TRUE_OPTIMUM = {
    "saf_dist_fact_ln_chg": 0.34,
    "lat_dist_driv_def": 0.55,
    "lat_dist_stand_def": 0.25,
}
_WEIGHTS = {"saf_dist_fact_ln_chg": 90.0, "lat_dist_driv_def": 12.0, "lat_dist_stand_def": 0.0}
_NODE_BIAS = {"3": 0.0, "4": 0.4, "5": -0.3, "6": 0.2, "7": 0.1, "8": -0.1}

NODES = ["3", "4", "5", "6", "7", "8"]
BASELINE = {"saf_dist_fact_ln_chg": 0.25, "lat_dist_driv_def": 0.2, "lat_dist_stand_def": 0.1}


def _surface(vector, seed, node_id):
    """A quadratic bowl: the shape a calibration objective actually has."""
    value = 3.2 + _NODE_BIAS.get(node_id, 0.0)
    for name, weight in _WEIGHTS.items():
        value += weight * (vector.get(name, 0.0) - TRUE_OPTIMUM[name]) ** 2
    value += 0.05 * ((seed * 37 + int(node_id)) % 7 - 3) / 3.0
    return round(value, 4)


class _Clock:
    """A clock that only moves when the test says so — the wall-clock budget is
    charged from the runner's reported elapsed time, so tests never sleep."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def _runner(cost_s: float = 30.0, **kwargs):
    return ps.SyntheticRunner(surface=_surface, cost_s=cost_s, **kwargs)


def _node(node_id, *, geh=7.5, rung="CAPACITY_LIMIT", entrance="measured", missing=()):
    return {
        "node_id": node_id,
        "node_total_geh": geh,
        "outcome": {"evidence": {"entrance": {"status": entrance}}},
        "diagnosis": None if rung is None else {"rung": rung, "missing_evidence": list(missing)},
    }


def _report(nodes=None, **overrides):
    payload = {
        "status": "CHECKED",
        "geh_threshold": validation.GEH_GOOD_LT,
        "nodes": nodes if nodes is not None else [_node(n) for n in NODES],
    }
    payload.update(overrides)
    return payload


APPLIED_OK = [
    {
        "behavior_no": "1",
        "verdict": {"status": "MATCHES_PUBLISHED_ROW", "standstill_matches_guideline": True},
    }
]
RAIL_SIGNALS_DONE = {"steps": [{"number": 6, "status": "DONE"}]}


def _open_gate(**overrides):
    kwargs = {
        "reality_check": _report(),
        "applied_rows": APPLIED_OK,
        "rail": RAIL_SIGNALS_DONE,
    }
    kwargs.update(overrides)
    return ps.evaluate_preconditions(**kwargs)


def _search(budget_s=3600.0, runner=None, samples=ps.DEFAULT_SCREENING_SAMPLES, seed=11):
    state = ps.start(
        search_id="t",
        preconditions=_open_gate(),
        baseline_vector=BASELINE,
        seed=seed,
        budget_s=budget_s,
        screening_samples=samples,
    )
    return ps.ParameterSearch(
        state=state, runner=runner or _runner(), clock=_Clock()
    )


# ===========================================================================
# 1. The search space is the MEASURED one
# ===========================================================================


def test_the_space_is_exactly_the_three_parameters_with_a_verb_and_a_band():
    parameters = [dimension.parameter for dimension in ps.search_space()]
    assert parameters == ["saf_dist_fact_ln_chg", "lat_dist_driv_def", "lat_dist_stand_def"]


def test_every_band_is_read_from_the_reference_pack_not_hardcoded_here():
    """The bounds must BE the reference pack's, not a copy that can drift."""
    for dimension in ps.search_space():
        assert mrwa_reference.merging_behaviour_range(dimension.parameter) == (
            dimension.low,
            dimension.high,
        )
        assert dimension.citation == mrwa_reference.MERGING_BEHAVIOUR_CITATION


def test_the_table_5_6_trio_is_excluded_because_a_measured_path_exists():
    for parameter in ("ax", "bx_add", "bx_mult"):
        assert parameter not in ps.SEARCHABLE_PARAMETERS
    codes = {row[0]: row[1] for row in ps.EXCLUSIONS}
    assert codes["ax / bx_add / bx_mult"] == ps.EXCLUDED_MEASURED_PATH


def test_w99_cc_parameters_are_not_reachable_from_the_space_at_all():
    """The institutional W99 ban is upheld by construction, not by a check."""
    parameters = {dimension.parameter for dimension in ps.search_space()}
    assert not any(name.startswith("cc") for name in parameters)


def test_table_5_10_is_excluded_for_having_no_write_verb_and_says_so():
    row = next(item for item in ps.EXCLUSIONS if "Table 5-10" in item[0])
    assert row[1] == ps.EXCLUDED_NO_WRITE_VERB
    for language_index in (2, 3, 4):
        assert row[language_index].strip()


def test_table_5_9_is_excluded_for_publishing_defaults_rather_than_a_band():
    row = next(item for item in ps.EXCLUSIONS if "Table 5-9" in item[0])
    assert row[1] == ps.EXCLUDED_NO_PER_INSTANCE_RANGE


def test_the_two_whitelisted_parameters_with_no_band_are_excluded_by_name():
    excluded = {row[0]: row[1] for row in ps.EXCLUSIONS}
    assert excluded["coop_ln_chg_speed_diff"] == ps.EXCLUDED_NO_PUBLISHED_RANGE
    assert excluded["min_speed_for_lat"] == ps.EXCLUDED_NO_PUBLISHED_RANGE
    for parameter in ("coop_ln_chg_speed_diff", "min_speed_for_lat"):
        assert mrwa_reference.merging_behaviour_range(parameter) is None


def test_every_exclusion_carries_a_known_code_and_three_languages():
    for parameter, code, ko, en, ja in ps.EXCLUSIONS:
        assert code in ps.EXCLUSION_CODES, parameter
        assert ko.strip() and en.strip() and ja.strip(), parameter


def test_a_parameter_whose_band_vanishes_from_the_pack_leaves_the_space(monkeypatch):
    """The band is the licence. Without it the parameter is not searched — it
    does NOT fall back to bounds invented here."""
    monkeypatch.setattr(
        mrwa_reference,
        "merging_behaviour_range",
        lambda parameter: None if parameter == "lat_dist_driv_def"
        else mrwa_reference.merging_behaviour_range.__wrapped__(parameter)
        if hasattr(mrwa_reference.merging_behaviour_range, "__wrapped__")
        else {"saf_dist_fact_ln_chg": (0.25, 0.45), "lat_dist_stand_def": (0.1, 0.4)}.get(parameter),
    )
    assert "lat_dist_driv_def" not in [d.parameter for d in ps.search_space()]


# ===========================================================================
# 2. The precondition gate
# ===========================================================================


def test_the_gate_opens_on_an_exhausted_ladder():
    gate = _open_gate()
    assert gate["ok"] is True
    assert gate["refusals"] == []
    assert gate["exhaustion"]["exhausted"] is True


def test_no_scored_run_refuses_by_name():
    gate = ps.evaluate_preconditions(reality_check=None)
    assert gate["ok"] is False
    assert [item["code"] for item in gate["refusals"]] == [ps.NO_REALITY_CHECK]


def test_an_unverified_entrance_refuses_and_names_the_node():
    nodes = [_node(n) for n in NODES]
    nodes[2] = _node("5", entrance="unverified")
    gate = _open_gate(reality_check=_report(nodes))
    refusal = _refusal(gate, ps.ENTRANCE_NOT_MEASURED)
    assert refusal["detail"]["nodes"] == ["5"]
    assert "5" in refusal["why"]["en"]


def test_a_cheaper_rung_anywhere_blocks_the_whole_search():
    """The guideline's order is an order. One node with a demand fix available
    stops a behaviour search at every node."""
    nodes = [_node(n) for n in NODES]
    nodes[0] = _node("3", rung="DEMAND_LEVEL")
    gate = _open_gate(reality_check=_report(nodes))
    refusal = _refusal(gate, ps.LADDER_NOT_EXHAUSTED)
    assert refusal["detail"]["cheaper_rung_nodes"] == ["3"]


def test_an_unapplied_table_5_6_prescription_refuses_by_name():
    unapplied = [
        {"behavior_no": "1", "verdict": {"status": "OFF_PUBLISHED_ROW", "standstill_matches_guideline": False}}
    ]
    gate = _open_gate(applied_rows=unapplied)
    refusal = _refusal(gate, ps.TABLE_5_6_NOT_APPLIED)
    assert refusal["detail"]["citation"] == mrwa_reference.TABLE_5_6_CITATION


def test_an_unverified_signal_step_refuses_even_when_everything_else_is_ready():
    gate = _open_gate(rail={"steps": [{"number": 6, "status": "NEXT"}]})
    assert _refusal(gate, ps.SIGNAL_STEP_UNVERIFIED)
    # And an ABSENT rail is not the same as a passing one.
    assert _refusal(_open_gate(rail=None), ps.SIGNAL_STEP_UNVERIFIED)


def test_too_few_nodes_to_split_a_holdout_refuses_and_cites_the_principle():
    """The 2-node demo fixture lands here, and that is the correct answer."""
    gate = _open_gate(reality_check=_report([_node("1"), _node("2")]))
    refusal = _refusal(gate, ps.HOLDOUT_NOT_AVAILABLE)
    assert refusal["detail"]["minimum"] == ps.MIN_NODES_FOR_HOLDOUT
    assert (
        refusal["detail"]["citation"]
        == mrwa_reference.CALIBRATION_PRINCIPLE_INDEPENDENT_DATA_CITATION
    )


def test_a_model_that_already_passes_refuses_rather_than_searching_anyway():
    gate = _open_gate(reality_check=_report([_node(n, geh=1.2, rung=None) for n in NODES]))
    assert _refusal(gate, ps.GEH_ALREADY_PASSES)


def test_every_refusal_names_what_would_open_it_in_three_languages():
    gate = ps.evaluate_preconditions(
        reality_check=_report([_node("1", entrance="unverified"), _node("2", rung="ROUTING_SPLIT")]),
        applied_rows=[],
        rail=None,
    )
    assert gate["refusals"]
    for refusal in gate["refusals"]:
        assert refusal["code"] in ps.REFUSAL_CODES
        for language in ("ko", "en", "ja"):
            assert refusal["why"][language].strip()
            assert refusal["opens_when"][language].strip()


def test_refusals_are_collected_not_short_circuited():
    """A modeller three doors away learns about all three now."""
    gate = ps.evaluate_preconditions(
        reality_check=_report([_node("1", entrance="unverified")]), applied_rows=[], rail=None
    )
    codes = {item["code"] for item in gate["refusals"]}
    assert {ps.ENTRANCE_NOT_MEASURED, ps.HOLDOUT_NOT_AVAILABLE} <= codes


def test_start_refuses_to_build_a_state_from_a_failed_gate():
    """There is no override, no force, and no caller-supplied boolean."""
    gate = ps.evaluate_preconditions(reality_check=None)
    with pytest.raises(ValueError, match="precondition gate did not pass"):
        ps.start(search_id="t", preconditions=gate, baseline_vector=BASELINE, seed=1)


def test_a_forged_ok_flag_still_cannot_manufacture_a_usable_split():
    """A caller that hands in ``{"ok": True}`` gets a state with NO nodes, so the
    search scores nothing rather than silently scoring everything."""
    state = ps.start(
        search_id="t", preconditions={"ok": True}, baseline_vector=BASELINE, seed=1
    )
    assert state.calibration_nodes == () and state.holdout_nodes == ()


# ===========================================================================
# 3. Exhaustion is measured, not assumed
# ===========================================================================


def test_capacity_limit_with_the_prescription_applied_is_exhaustion():
    measured = ps.measure_exhaustion(_report(), APPLIED_OK)
    assert measured["exhausted"] is True
    assert measured["exhausted_nodes"] == NODES


def test_capacity_limit_without_the_prescription_is_pending_not_exhausted():
    measured = ps.measure_exhaustion(_report(), [])
    assert measured["exhausted"] is False
    assert measured["prescription_pending_nodes"] == NODES


def test_unclassifiable_with_closable_evidence_is_waiting_not_exhausted():
    nodes = [_node(n, rung="UNCLASSIFIABLE", missing=["NO_QUEUE_OR_DELAY_EVIDENCE"]) for n in NODES]
    measured = ps.measure_exhaustion(_report(nodes), APPLIED_OK)
    assert measured["exhausted"] is False
    assert measured["evidence_missing_nodes"] == NODES


def test_unclassifiable_at_a_ladder_dead_end_is_exhaustion():
    nodes = [_node(n, rung="UNCLASSIFIABLE", missing=["NO_CONGESTION_AT_THIS_NODE"]) for n in NODES]
    measured = ps.measure_exhaustion(_report(nodes), APPLIED_OK)
    assert measured["exhausted"] is True


def test_an_unreadable_network_is_not_an_applied_prescription():
    unreadable = [{"behavior_no": "1", "verdict": {"status": "ROW_UNREADABLE"}}]
    assert ps.table_5_6_fully_applied(unreadable)["applied"] is False
    assert ps.table_5_6_fully_applied([])["applied"] is False


# ===========================================================================
# 4. The engine — bands, ledger, budget, resume
# ===========================================================================


def test_every_candidate_the_search_ever_runs_is_inside_the_published_band():
    search = _search()
    search.run()
    for row in search.state.ledger:
        assert ps.in_published_range(row["params"]), row


def test_an_out_of_range_candidate_cannot_be_run_at_all():
    search = _search()
    with pytest.raises(ValueError, match="outside the published MRWA range"):
        search._evaluate({"saf_dist_fact_ln_chg": 9.9}, ps.STAGE_OPTIMIZATION)


def test_the_latin_hypercube_never_leaves_the_bands():
    space = ps.search_space()
    for vector in ps.latin_hypercube(space, 40, seed=7):
        assert ps.in_published_range(vector)


def test_the_screen_finds_the_two_real_parameters_and_rejects_the_inert_one():
    search = _search()
    search.run()
    selected = set(search.state.selected_parameters)
    assert selected == {"saf_dist_fact_ln_chg", "lat_dist_driv_def"}
    inert = next(
        item
        for item in search.state.sensitivity["per_parameter"]
        if item["parameter"] == "lat_dist_stand_def"
    )
    assert inert["significant"] is False


def test_spearman_alone_would_have_missed_them_which_is_why_it_is_not_the_gate():
    """The measurement that demoted rho: on a U-shaped surface the ranks cancel
    and every parameter looks insignificant, including the dominant one."""
    search = _search()
    search.run()
    for item in search.state.sensitivity["per_parameter"]:
        assert abs(item["spearman_rho"] or 0.0) < 0.5
    assert search.state.selected_parameters  # the main effect saw them anyway


def test_the_search_improves_on_the_baseline_and_approaches_the_true_optimum():
    search = _search()
    result = search.run()
    assert result["status"] == ps.STATUS_COMPLETE
    assert result["best"]["objective"] < result["baseline"]["objective"]
    for parameter in ("saf_dist_fact_ln_chg", "lat_dist_driv_def"):
        assert abs(result["best"]["params"][parameter] - TRUE_OPTIMUM[parameter]) < 0.1


def test_a_parameter_the_screen_rejected_is_returned_to_baseline_never_proposed():
    """An LHS row moves every dimension; carrying its value for a parameter the
    screen just showed does nothing would be hunch tuning with a ledger."""
    search = _search()
    result = search.run()
    assert result["best"]["params"]["lat_dist_stand_def"] == BASELINE["lat_dist_stand_def"]


def test_every_ledger_row_carries_the_documented_schema():
    search = _search()
    search.run()
    for row in search.state.ledger:
        assert set(ledger_keys()) <= set(row), row
        assert row["stage"] in ps.STAGES
        assert row["seed"] == search.state.seed


def ledger_keys():
    return ps.ledger_schema().keys()


def test_the_ledger_index_is_monotonic_and_gapless():
    search = _search()
    search.run()
    assert [row["index"] for row in search.state.ledger] == list(range(len(search.state.ledger)))


def test_an_exhausted_budget_returns_a_named_partial_result_with_its_best_so_far():
    search = _search(budget_s=180.0)
    result = search.run()
    assert result["status"] == ps.STATUS_PARTIAL_BUDGET_EXHAUSTED
    assert result["partial"] is True
    assert result["best"] is not None  # a partial result is still a result
    assert result["consumed_s"] <= result["budget_s"] + 30.0


def test_a_stopped_search_resumes_and_continues_the_same_ledger():
    stopped = _search(budget_s=180.0)
    partial = stopped.run()
    assert partial["status"] == ps.STATUS_PARTIAL_BUDGET_EXHAUSTED
    resumed = ps.resume(
        partial["state"], _runner(), clock=_Clock(), additional_budget_s=3600.0
    )
    finished = resumed.run()
    assert finished["status"] == ps.STATUS_COMPLETE
    assert finished["candidates"] > partial["candidates"]
    assert [row["index"] for row in finished["state"]["ledger"]] == list(
        range(len(finished["state"]["ledger"]))
    )


def test_a_resumed_search_reaches_the_same_answer_as_an_uninterrupted_one():
    """The optimizer draws no random numbers, so a stop is not a perturbation."""
    straight = _search()
    straight_result = straight.run()
    stopped = _search(budget_s=210.0)
    partial = stopped.run()
    resumed = ps.resume(partial["state"], _runner(), clock=_Clock(), additional_budget_s=3600.0)
    resumed_result = resumed.run()
    assert resumed_result["best"]["params"] == straight_result["best"]["params"]


def test_a_state_from_another_version_is_refused_not_reinterpreted():
    search = _search(budget_s=120.0)
    state = search.run()["state"]
    state["version"] = 999
    with pytest.raises(ValueError, match="cannot be resumed"):
        ps.resume(state, _runner())


def test_two_runs_at_the_same_seed_produce_identical_ledgers():
    first = _search()
    first.run()
    second = _search()
    second.run()
    assert [row["params"] for row in first.state.ledger] == [
        row["params"] for row in second.state.ledger
    ]


def test_a_failing_runner_is_ledgered_as_a_named_failure_not_a_passing_score():
    runner = _runner(fail_on=lambda vector, seed: "COM_TIMEOUT")
    search = _search(runner=runner, budget_s=120.0)
    search.run()
    assert all(row["ok"] is False and row["reason"] == "COM_TIMEOUT" for row in search.state.ledger)
    assert search.best() is None


def test_an_ungradable_node_stays_none_and_is_never_counted_as_a_pass():
    outcome = ps.RunOutcome(ok=True, geh_by_node={"3": None, "4": 2.0})
    score = ps.score_holdout(outcome, ["3", "4"], validation.GEH_GOOD_LT)
    assert score.geh_by_node["3"] is None
    assert score.graded == 1 and score.pass_count == 1


# ===========================================================================
# 5. The holdout — the principle, pinned structurally
# ===========================================================================


def test_the_two_node_sets_are_disjoint_and_both_are_real():
    split = ps.split_nodes(NODES)
    assert split["available"] is True
    assert set(split["calibration"]) & set(split["holdout"]) == set()
    assert len(split["holdout"]) >= 2 and len(split["calibration"]) >= 2
    assert sorted(split["calibration"] + split["holdout"], key=int) == NODES


def test_the_split_is_deterministic_from_the_node_list_alone():
    assert ps.split_nodes(NODES) == ps.split_nodes(list(reversed(NODES)))


def test_the_split_strides_rather_than_slicing_so_neither_side_is_one_corner():
    split = ps.split_nodes(NODES)
    assert split["holdout"] != NODES[-len(split["holdout"]) :]


def test_the_optimizer_never_scores_a_held_out_node():
    """THE principle, as a structural fact rather than a promise: no row the
    search itself produced carries a holdout node, in its node set or its GEH
    map."""
    search = _search()
    search.run()
    held_out = set(search.state.holdout_nodes)
    assert held_out
    for row in search.state.ledger:
        assert row["node_set"] == "CALIBRATION"
        assert set(row["geh_by_node"]) & held_out == set()


def test_the_objective_object_itself_cannot_see_a_holdout_node():
    search = _search()
    assert set(search._objective.node_ids) == set(search.state.calibration_nodes)
    assert set(search._objective.node_ids) & set(search.state.holdout_nodes) == set()


def test_a_winner_is_accepted_only_by_improving_on_the_baseline_out_of_sample():
    search = _search()
    search.run()
    acceptance = ps.accept_winner(search, _runner(), unseen_seed=99)
    assert acceptance["accepted"] is True
    assert acceptance["code"] == ps.HOLDOUT_PASSED
    holdout = acceptance["holdout"]
    assert holdout["winner_holdout"]["objective"] < holdout["baseline_holdout"]["objective"]
    assert (
        holdout["principle_citation"]
        == mrwa_reference.CALIBRATION_PRINCIPLE_INDEPENDENT_DATA_CITATION
    )


def test_a_candidate_that_does_not_beat_the_baseline_out_of_sample_is_refused():
    """An absolute holdout GEH proves nothing — the held-out nodes might be easy.
    Acceptance is a comparison, so a winner that only fits the calibration nodes
    is caught here."""
    search = _search()
    search.run()

    def overfit(vector, seed, node_id):
        # Good on the calibration nodes, bad on everything else.
        return 1.0 if node_id in search.state.calibration_nodes else 12.0

    flipped = ps.SyntheticRunner(
        surface=lambda vector, seed, node: overfit(vector, seed, node)
        if vector != dict(search.state.baseline_vector)
        else 2.0,
        cost_s=30.0,
    )
    acceptance = ps.accept_winner(search, flipped, unseen_seed=99)
    assert acceptance["accepted"] is False
    assert acceptance["code"] == ps.HOLDOUT_NOT_IMPROVED


def test_the_holdout_must_use_a_seed_the_search_never_used():
    search = _search()
    search.run()
    acceptance = ps.accept_winner(search, _runner(), unseen_seed=search.state.seed)
    assert acceptance["accepted"] is False
    assert acceptance["code"] == ps.HOLDOUT_SEED_NOT_HELD


def test_the_holdout_rows_are_ledgered_and_labelled_as_holdout():
    search = _search()
    search.run()
    ps.accept_winner(search, _runner(), unseen_seed=99)
    holdout_rows = [row for row in search.state.ledger if row["stage"] == ps.STAGE_HOLDOUT]
    assert len(holdout_rows) == 2  # the winner and the baseline
    for row in holdout_rows:
        assert row["node_set"] == "HOLDOUT"
        assert set(row["geh_by_node"]) == set(search.state.holdout_nodes)


def test_a_search_with_no_candidate_at_all_is_refused_not_forced():
    runner = _runner(fail_on=lambda vector, seed: "COM_TIMEOUT")
    search = _search(runner=runner, budget_s=120.0)
    search.run()
    acceptance = ps.accept_winner(search, runner, unseen_seed=99)
    assert acceptance["code"] == ps.NO_CANDIDATE


# ===========================================================================
# 6. Output — the rationale, the proposal, and the guardrail it will hit
# ===========================================================================


SHARED_BEHAVIOR = {
    "no": "1",
    "reference_count": 3,
    "referenced_by": {"count": 3, "consumers": [], "truncated": False},
}


def _accepted_proposal():
    search = _search()
    result = search.run()
    acceptance = ps.accept_winner(search, _runner(), unseen_seed=99)
    return ps.propose(result=result, acceptance=acceptance, behavior=SHARED_BEHAVIOR), result


def test_a_passing_search_proposes_only_the_parameters_that_actually_moved():
    proposal, _ = _accepted_proposal()
    assert proposal["outcome"] == "PROPOSAL"
    moved = {action["params"]["parameter"] for action in proposal["actions"]}
    assert moved == {"saf_dist_fact_ln_chg", "lat_dist_driv_def"}
    assert proposal["unchanged_parameters"] == ["lat_dist_stand_def"]


def test_every_proposed_action_is_the_one_verb_at_an_in_band_value():
    proposal, _ = _accepted_proposal()
    for action in proposal["actions"]:
        assert action["tool"] == ps.SEARCH_TOOL
        assert ps.in_published_range(
            {action["params"]["parameter"]: action["params"]["value"]}
        )


def test_the_rationale_names_the_band_its_page_the_count_and_the_holdout():
    proposal, result = _accepted_proposal()
    for language in ("ko", "en", "ja"):
        text = proposal["rationale"][language]
        assert mrwa_reference.MERGING_BEHAVIOUR_CITATION in text
        assert "0.25" in text and "0.45" in text
        assert str(result["candidates"]) in text
        assert mrwa_reference.MERGING_RANGE_OBSERVATION_NOTE in text
        assert mrwa_reference.MERGING_RANGE_SCOPE_NOTE in text


def test_a_failed_holdout_proposes_nothing_and_still_explains_itself():
    search = _search()
    result = search.run()
    refused = ps.propose(
        result=result,
        acceptance={"accepted": False, "code": ps.HOLDOUT_NOT_IMPROVED, "holdout": {}},
        behavior=SHARED_BEHAVIOR,
    )
    assert refused["outcome"] == "REFUSAL"
    assert refused["actions"] == []
    assert refused["reason_code"] == ps.HOLDOUT_NOT_IMPROVED
    for language in ("ko", "en", "ja"):
        assert refused["rationale"][language].strip()


def test_the_guardrail_collision_is_forecast_before_the_human_is_asked():
    proposal, _ = _accepted_proposal()
    forecast = proposal["preflight_forecast"]
    assert forecast["verdict"] == ps.GUARDRAIL_WILL_REFUSE
    assert forecast["constraint_id"] == ps.SHARED_BEHAVIOR_REFUSAL
    for language in ("ko", "en", "ja"):
        assert forecast["why"][language].strip()


def test_the_forecast_never_auto_selects_localization():
    """Duplicating a network-wide behaviour changes what the model means. Both
    resolutions are offered; neither is taken."""
    forecast = ps.preflight_forecast(SHARED_BEHAVIOR)
    assert {item["key"] for item in forecast["resolutions"]} == {
        "widen_exception",
        "localize_duplicate",
    }
    assert all(item["auto_selectable"] is False for item in forecast["resolutions"])


def test_the_standing_policy_states_the_rule_and_forecasts_nothing():
    """Unit #51-c: the ⑫ track's guardrail block, where no behaviour has been chosen.

    ``preflight_forecast`` gives a verdict because it has a behaviour to read.
    :func:`guardrail_policy` has none — it is what the track shows BEFORE a search is
    even started — so the direct arm asserts its unconditional refusal and the approved
    arm asserts nothing at all, naming its CONDITION instead. A ``False`` there would be
    an optimistic forecast over a radius nobody has read, which is the one direction #44's
    refuter invariant forbids.
    """
    policy = ps.guardrail_policy()
    assert policy["conditional"] is True
    assert policy["decision"] == ps.DECISION_2026_08_20

    direct = policy["paths"][ps.PATH_DIRECT_EDIT]
    approved = policy["paths"][ps.PATH_APPROVED_SET]
    assert direct["expected_to_refuse"] is True
    assert direct["constraint_id"] == ps.SHARED_BEHAVIOR_REFUSAL
    assert approved["expected_to_refuse"] is None, "a policy must not forecast an outcome"
    assert approved["condition"] == "RADIUS_NAMED_IN_FULL"
    assert approved["constraint_id"] == ps.SEARCHED_CONSENTED_CONSTRAINT
    assert approved["refuses_if_radius_drifts"] == ps.SEARCHED_RADIUS_DRIFTED_CONSTRAINT
    for path in (direct, approved):
        for language in ("ko", "en", "ja"):
            assert path["why"][language].strip()

    # The user's two answers ride along, with the decision that made them.
    resolutions = {item["key"]: item for item in policy["resolutions"]}
    assert resolutions["widen_exception"]["status"] == "DECIDED"
    assert resolutions["widen_exception"]["landed_in"] == "#51-b"
    assert resolutions["localize_duplicate"]["status"] == "REJECTED_BY_USER"
    assert all(item["auto_selectable"] is False for item in resolutions.values())


def _card_record():
    """The four payloads a card (and a grant) are built from, in the order that works."""
    gate = _open_gate()
    search = _search()
    search.run()
    acceptance = ps.accept_winner(search, _runner(), unseen_seed=99)
    result = search.result(ps.STATUS_COMPLETE)
    proposal = ps.propose(result=result, acceptance=acceptance, behavior=SHARED_BEHAVIOR)
    return {
        "result": result,
        "acceptance": acceptance,
        "proposal": proposal,
        "preconditions": gate,
    }


def test_the_card_payload_quotes_the_proposal_and_carries_the_evidence_rank():
    """Unit #51-c: what the approval card reads, built from the same four payloads
    ``grant_from_search`` takes — so a card can never describe a search the grant could
    not be minted from."""
    record = _card_record()
    proposal = record["proposal"]
    payload = ps.card_payload(**record)

    assert payload["status"] == "PROPOSED"
    assert payload["search_id"] == record["result"]["search_id"]
    assert payload["behavior_no"] == str(SHARED_BEHAVIOR["no"])
    # Exactly the parameters the winner MOVED, at exactly the proposed values.
    assert payload["searched_parameters"] == [
        action["params"]["parameter"] for action in proposal["actions"]
    ]
    assert payload["expected_after_apply"] == {
        action["params"]["parameter"]: action["params"]["value"] for action in proposal["actions"]
    }
    # Quoted, never re-written.
    assert payload["rationale"] == proposal["rationale"]
    assert payload["preflight_forecast"] == proposal["preflight_forecast"]
    assert payload["consent_radius"] == proposal["consent_radius"]
    # DECISIONS.md 2026-08-20's closing requirement, as its OWN field so the card can
    # render it without unfolding a rationale.
    assert payload["evidence_rank"] == searched_behaviour_grant.EVIDENCE_RANK
    # The evidence behind the number: the ledger it came from and the holdout that
    # accepted it, both re-readable from the payload.
    assert payload["ledger"] == ps.ledger_reference(record["result"])
    assert payload["holdout"]["accepted"] is True
    assert payload["holdout"]["unseen_seed"] == record["acceptance"]["holdout"]["unseen_seed"]
    assert payload["holdout"]["holdout_nodes"] == record["result"]["holdout_nodes"]
    assert payload["consent_radius_phrase"]
    # A payload is never a grant.
    assert "signatures" not in json.dumps(payload, default=str)


def test_a_refused_search_produces_a_refused_card_not_a_proposal():
    """A holdout that did not pass has nothing to approve, and the card says so."""
    search = _search()
    result = search.run()
    acceptance = {"accepted": False, "code": ps.HOLDOUT_NOT_IMPROVED, "holdout": {}}
    proposal = ps.propose(result=result, acceptance=acceptance, behavior=SHARED_BEHAVIOR)
    payload = ps.card_payload(
        result=result, acceptance=acceptance, proposal=proposal, preconditions=_open_gate()
    )
    assert payload["status"] == "REFUSED"
    assert payload["searched_parameters"] == []
    assert payload["reason_code"] == ps.HOLDOUT_NOT_IMPROVED
    assert payload["holdout"]["accepted"] is False
    # Still carries the rank sentence: a refusal a human reads is still a statement
    # about searched evidence.
    assert payload["evidence_rank"] == searched_behaviour_grant.EVIDENCE_RANK


def test_an_unreferenced_behaviour_forecasts_clear_and_an_unreadable_one_unknown():
    assert ps.preflight_forecast({"no": "9", "reference_count": 0})["verdict"] == ps.GUARDRAIL_CLEAR
    assert ps.preflight_forecast(None)["verdict"] == ps.GUARDRAIL_UNKNOWN


def test_the_runner_seam_states_the_spec_whichever_way_the_machine_answers(monkeypatch):
    """``wired`` stopped being a constant when the live runner landed (2026-08-21).

    It is now a MEASUREMENT of the box the backend runs on, so this asserts the two
    things that must hold on EITHER box rather than a value that would make the
    suite pass on a Mac and fail on the Windows dev machine (or the reverse):

    * the ``requires`` spec is stated in full either way — an unwired card still
      owes its reader what running a search would take;
    * ``why_not_here`` is a real sentence exactly when ``wired`` is False, and
      ``None`` when it is True (a "why not" beside a wired runner would be a
      sentence contradicting the flag beside it).

    ``test_agent_search_job.py`` pins both directions against a pinned probe.
    """
    from services.agent import search_runner

    for available, reason in ((True, None), (False, "pywin32 is not available.")):
        monkeypatch.setattr(search_runner, "runner_availability", lambda: (available, reason))
        requirement = ps.runner_requirement()
        assert requirement["wired"] is available
        assert requirement["requires"] == list(ps.RUNNER_REQUIRES)
        if available:
            assert requirement["why_not_here"] is None
        else:
            assert reason in requirement["why_not_here"]


# ===========================================================================
# 7. The trigger card — ask-first, never an automatic start
# ===========================================================================


def test_no_card_is_offered_while_the_gate_is_shut():
    assert ps.trigger_card(ps.evaluate_preconditions(reality_check=None)) is None
    assert ps.trigger_card(_open_gate(applied_rows=[])) is None


def test_an_open_gate_offers_a_question_never_a_started_search():
    card = ps.trigger_card(_open_gate())
    assert card is not None
    assert card["ask_first"] is True and card["auto_start"] is False
    for language in ("ko", "en", "ja"):
        assert card["question"][language].strip().endswith(("?", "?"))


def test_the_card_says_what_was_exhausted_and_what_remains():
    card = ps.trigger_card(_open_gate())
    for language in ("ko", "en", "ja"):
        assert card["because"][language].strip()
    assert card["plan"]["calibration_nodes"] and card["plan"]["holdout_nodes"]
    assert card["plan"]["budget_s"] == ps.DEFAULT_BUDGET_S
    assert card["plan"]["resumable"] is True
    assert card["plan"]["algorithm_citation"] == (
        mrwa_reference.CALIBRATION_PRINCIPLE_ONE_PARAMETER_CITATION
    )


def test_the_card_carries_the_guidelines_own_preference_for_observation():
    card = ps.trigger_card(_open_gate())
    assert mrwa_reference.MERGING_RANGE_OBSERVATION_NOTE in card["caveats"]
    # The runner's state rides on the card and is the SAME object
    # ``runner_requirement`` builds — never a second opinion composed here. Its
    # value is the machine's answer (see that function's own test); what this
    # pins is that the card carries the whole seam rather than a boolean.
    assert card["runner"] == ps.runner_requirement()
    # ...and the card still starts nothing by itself, wired or not.
    assert card["auto_start"] is False and card["ask_first"] is True


# ===========================================================================
# 8. Adversarial review, 2026-08-19 — the four seals a refuter broke open
#
# Every test below reproduces an attack that SUCCEEDED against the first cut of
# this module. They are regression pins, not hypotheticals.
# ===========================================================================


def test_a_report_with_no_status_field_cannot_open_a_gate_on_an_unexhausted_ladder():
    """BROKEN (fixed): GEH_ALREADY_PASSES was gated on ``status == "CHECKED"``
    and LADDER_NOT_EXHAUSTED on ``missing`` being non-empty. A report that
    simply omitted ``status`` and had every node passing therefore produced NO
    refusal at all — an open gate on a ladder where all six nodes still had a
    demand fix waiting."""
    passing = [_node(n, geh=1.0, rung="DEMAND_LEVEL") for n in NODES]
    gate = ps.evaluate_preconditions(
        reality_check={"geh_threshold": 5.0, "nodes": passing},  # no "status" key
        applied_rows=APPLIED_OK,
        rail=RAIL_SIGNALS_DONE,
    )
    assert gate["ok"] is False
    codes = {item["code"] for item in gate["refusals"]}
    assert ps.GEH_ALREADY_PASSES in codes
    assert ps.LADDER_NOT_EXHAUSTED in codes
    assert ps.trigger_card(gate) is None


def test_exhaustion_is_a_positive_requirement_not_merely_uncontradicted():
    """The gate may never open unless ``measure_exhaustion`` said True."""
    for nodes in (
        [_node(n, geh=1.0, rung=None) for n in NODES],  # nothing diagnosed at all
        [_node(n, geh=7.5, rung="ROUTING_SPLIT") for n in NODES],
    ):
        gate = _open_gate(reality_check=_report(nodes))
        assert gate["exhaustion"]["exhausted"] is False
        assert gate["ok"] is False
        assert ps.LADDER_NOT_EXHAUSTED in {item["code"] for item in gate["refusals"]}


def test_a_ladder_dead_end_must_be_named_never_inferred_from_silence():
    """BROKEN (fixed): an UNCLASSIFIABLE node was declared EXHAUSTED whenever no
    CLOSABLE code was present — so an unrecognised (or absent) missing-evidence
    code opened the search. A dead end now requires a
    :data:`LADDER_DEAD_END_EVIDENCE` code by name."""
    for missing in ([], ["SOME_FUTURE_LADDER_CODE"], ["NODE_CONTROL_UNKNOWN"]):
        nodes = [_node(n, rung="UNCLASSIFIABLE", missing=missing) for n in NODES]
        measured = ps.measure_exhaustion(_report(nodes), APPLIED_OK)
        assert measured["exhausted"] is False, missing
        assert measured["evidence_missing_nodes"] == NODES, missing
    # and the positively-named dead end still is one
    named = [_node(n, rung="UNCLASSIFIABLE", missing=["NO_THROUGHPUT_SHORTFALL"]) for n in NODES]
    assert ps.measure_exhaustion(_report(named), APPLIED_OK)["exhausted"] is True


def test_a_caller_cannot_choose_the_holdout_split():
    """CRITICAL (fixed): ``start`` trusted the ``split`` beside ``ok``. A payload
    naming the calibration nodes as the holdout produced a search that scored
    every "held-out" node inside its own objective and then reported
    HOLDOUT_PASSED on it."""
    leaking = {**_open_gate(), "split": {"available": True, "calibration": NODES, "holdout": NODES}}
    with pytest.raises(ValueError, match="not the one split_nodes derives"):
        ps.start(search_id="t", preconditions=leaking, baseline_vector=BASELINE, seed=1)

    # ...and a merely FAVOURABLE split is refused too: the honest one is 5/8.
    picked = {
        **_open_gate(),
        "split": {"available": True, "calibration": ["3", "5", "7", "8"], "holdout": ["4", "6"]},
    }
    assert ps.split_nodes(NODES)["holdout"] == ["5", "8"]
    with pytest.raises(ValueError, match="not the one split_nodes derives"):
        ps.start(search_id="t", preconditions=picked, baseline_vector=BASELINE, seed=1)

    # The honest payload still starts, and its two sides stay disjoint.
    state = ps.start(search_id="t", preconditions=_open_gate(), baseline_vector=BASELINE, seed=1)
    assert set(state.calibration_nodes).isdisjoint(state.holdout_nodes)
    assert len(state.holdout_nodes) >= 2


def _tampered_state(out_of_band_value):
    """A finished search whose ledger has been given one out-of-band winner.

    ``lat_dist_stand_def`` is the surface's INERT dimension, so the injected row
    genuinely wins the holdout comparison — the attack does not rely on the
    scorer being fooled, only on the band never being re-checked.
    """
    search = _search(samples=6)
    search.run()
    payload = search.state.to_dict()
    payload["ledger"].append(
        {
            "index": len(payload["ledger"]),
            "stage": ps.STAGE_OPTIMIZATION,
            "params": {
                "saf_dist_fact_ln_chg": TRUE_OPTIMUM["saf_dist_fact_ln_chg"],
                "lat_dist_driv_def": TRUE_OPTIMUM["lat_dist_driv_def"],
                "lat_dist_stand_def": out_of_band_value,
            },
            "seed": payload["seed"],
            "node_set": "CALIBRATION",
            "objective": 0.0001,
            "pass_count": 4,
            "graded": 4,
            "geh_by_node": {node: 0.0001 for node in payload["calibration_nodes"]},
            "ok": True,
            "reason": None,
            "started_at_s": 0.0,
            "elapsed_s": 0.0,
        }
    )
    payload["selected_parameters"] = list(ps.SEARCHABLE_PARAMETERS)
    return payload


def test_a_resumed_ledger_row_outside_the_band_can_never_become_a_proposal():
    """MAJOR (fixed): ``in_published_range`` was consulted only in ``_evaluate``,
    so a row that arrived through ``SearchState.from_dict`` was never checked.
    ``best()`` returned it and ``propose`` emitted
    ``set_driving_behavior_parameter lat_dist_stand_def = 0.05`` against a
    published band of 0.1-0.4."""
    assert not ps.in_published_range({"lat_dist_stand_def": 0.05})
    resumed = ps.resume(_tampered_state(0.05), _runner(), clock=_Clock())

    assert ps.in_published_range(resumed.best()["params"])
    assert ps.in_published_range(resumed.best_measured()["params"])

    acceptance = ps.accept_winner(resumed, _runner(), unseen_seed=99)
    proposal = ps.propose(
        result=resumed.result(ps.STATUS_COMPLETE), acceptance=acceptance, behavior=SHARED_BEHAVIOR
    )
    for action in proposal["actions"]:
        assert ps.in_published_range({action["params"]["parameter"]: action["params"]["value"]})


def test_an_out_of_band_vector_is_never_handed_to_a_runner():
    """The holdout re-run bypasses ``_evaluate`` entirely, so it needs its own
    guard: a real runner would have WRITTEN the out-of-band value to Vissim."""
    seen: list[dict] = []

    class _Spy:
        def __init__(self):
            self._inner = _runner()

        def run(self, vector, seed, node_ids):
            seen.append(dict(vector))
            return self._inner.run(vector, seed, node_ids)

    resumed = ps.resume(_tampered_state(0.05), _Spy(), clock=_Clock())
    ps.accept_winner(resumed, _Spy(), unseen_seed=99)
    assert all(ps.in_published_range(vector) for vector in seen), seen

    # And a state whose BASELINE is out of band refuses by name rather than
    # running it — baseline_vector is restored verbatim, never clipped.
    payload = _tampered_state(0.05)
    payload["baseline_vector"] = {**BASELINE, "lat_dist_stand_def": 0.05}
    payload["ledger"] = [row for row in payload["ledger"] if row["stage"] != ps.STAGE_BASELINE]
    acceptance = ps.accept_winner(ps.resume(payload, _runner(), clock=_Clock()), _runner(), unseen_seed=99)
    assert acceptance["accepted"] is False
    assert acceptance["code"] == ps.HOLDOUT_VECTOR_OUT_OF_RANGE
    assert acceptance["code"] in ps.ACCEPTANCE_CODES


def test_every_name_in_dunder_all_actually_exists():
    """BROKEN (fixed): ``__all__`` still named the rho-based floors the module
    had replaced with a binned main effect, so ``from ... import *`` raised
    AttributeError — the public surface named two constants that did not exist."""
    for name in ps.__all__:
        assert hasattr(ps, name), name


# ===========================================================================
# helpers
# ===========================================================================


def _refusal(gate, code):
    matches = [item for item in gate["refusals"] if item["code"] == code]
    assert matches, f"expected a {code} refusal, got {[i['code'] for i in gate['refusals']]}"
    return matches[0]

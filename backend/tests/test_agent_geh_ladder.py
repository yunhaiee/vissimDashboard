"""Unit #43: the GEH-miss diagnosis LADDER.

Two halves, and the first one is the reason the second is allowed to exist:

1. **The availability measurement.** Every rung below rests on the run
   carrying queue and delay evidence. So this file first PROVES that evidence
   is really there — against ``make_demo_data``'s own result database through
   U1's own ``_collect_run_evidence``, i.e. the same objects
   ``reality_check`` gets — and pins the three states the ladder must tell
   apart (both present, delay missing, nothing at all). A rung that fired on
   evidence nobody had checked for would be the exact failure this unit was
   written to prevent.

2. **Firing and NOT firing.** ``CAPACITY_LIMIT`` must fire only on the full
   conjunction (measured entrance + throughput shortfall + graded congestion),
   must NOT fire when the entrance is merely unverified (that is a demand
   story and stays one), and every way the conjunction breaks must degrade to
   a NAMED ``UNCLASSIFIABLE`` rather than to silence.

The synthetic node here is deliberately small and hand-built: the demo network
is not congested (measured — its worst movement delay is ~47 s, LOS D), so it
cannot exercise the congested rungs, and inventing a congested demo dataset
would change a fixture forty other tests depend on.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from services import results_evidence, validation
from services.agent import fixes, geh_ladder, mrwa_reference, reality_check, reality_check_targets
from services.agent.tools_run import _collect_run_evidence
from tools import make_demo_data


# ===========================================================================
# 1. THE AVAILABILITY MEASUREMENT
# ===========================================================================


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("ladder-demo")
    argv = sys.argv
    sys.argv = ["make_demo_data.py", "--out", str(out)]
    try:
        make_demo_data.main()
    finally:
        sys.argv = argv
    return out


@pytest.fixture(scope="module")
def demo_run_result(demo_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> dict:
    """U1's REAL evidence collector over ONE of the demo's result databases."""
    work = tmp_path_factory.mktemp("ladder-run")
    shutil.copy(
        demo_dir / "Scenarios" / "S000001" / "S000001.results" / "run_001.db",
        work / "run_001.db",
    )
    return _collect_run_evidence(
        work, demo_dir / "practice.inpx", duration_s=3600, evaluation_configured=False
    )


def test_the_run_really_carries_queue_and_delay(demo_run_result: dict):
    """The measurement the whole unit rests on, taken rather than assumed.

    Both keys come off the SAME ``run_result`` the reality check already
    receives, and both are keyed by the same movement OBJECT_ID
    ``movement_volumes`` is — which is why the ladder needs no new plumbing to
    reach them.
    """
    evidence = geh_ladder.run_congestion_evidence(demo_run_result)

    assert evidence["availability"] == geh_ladder.QUEUE_AND_DELAY
    assert evidence["queue"]["table"] == results_evidence.MOVEMENT_INTERVAL_TABLE
    assert evidence["delay"]["table"] == results_evidence.MOVEMENT_INTERVAL_CLASS_TABLE
    assert evidence["queue"]["table_state"].startswith("PRESENT(")
    assert evidence["delay"]["table_state"].startswith("PRESENT(")
    # Every movement of the demo network, on both sides.
    assert evidence["queue"]["rows_with_a_length"] == len(make_demo_data.MOVEMENTS)
    assert evidence["delay"]["rows_with_a_delay"] == len(make_demo_data.MOVEMENTS)

    # The run_result key the ladder reads is the one U5 names, spelled once.
    assert geh_ladder.VOLUMES_KEY == reality_check.MOVEMENT_VOLUMES_KEY


def test_availability_degrades_by_which_half_is_missing(demo_run_result: dict):
    delay_only = {k: v for k, v in demo_run_result.items() if k != geh_ladder.QUEUE_KEY}
    queue_only = {k: v for k, v in demo_run_result.items() if k != geh_ladder.LOS_KEY}
    neither = {
        k: v
        for k, v in demo_run_result.items()
        if k not in (geh_ladder.QUEUE_KEY, geh_ladder.LOS_KEY)
    }

    assert geh_ladder.run_congestion_evidence(delay_only)["availability"] == geh_ladder.DELAY_ONLY
    assert geh_ladder.run_congestion_evidence(queue_only)["availability"] == geh_ladder.QUEUE_ONLY
    assert geh_ladder.run_congestion_evidence(neither)["availability"] == geh_ladder.NEITHER
    assert geh_ladder.run_congestion_evidence(None)["availability"] == geh_ladder.NEITHER


def test_node_congestion_groups_by_approach_and_excludes_the_node_row(demo_run_result: dict):
    """The approach grouping is the movement OBJECT_ID's FROM-link, and the
    node's own aggregate row is evidence THAT the node exists, not a movement."""
    congestion = geh_ladder.node_congestion(demo_run_result, "1")

    node_1_movements = [m for m in make_demo_data.MOVEMENTS if m[0] == 1]
    assert congestion["movements"] == len(node_1_movements)
    assert sorted(entry["approach_link"] for entry in congestion["approaches"]) == sorted(
        {str(from_link) for _n, from_link, _to, _v, _d in node_1_movements}
    )
    assert congestion["availability"] == geh_ladder.QUEUE_AND_DELAY


def test_the_demo_network_is_not_congested_so_no_rung_may_claim_it_is(demo_run_result: dict):
    """A measured negative, pinned so a future threshold change is visible.

    The demo network's worst movement delay is 48 s before jitter — LOS D, one
    band below the congested test. If someone later lowers the band, this test
    fails and says so, rather than the demo dataset quietly starting to report
    a capacity limit that its own generator never put there.
    """
    for node_id in ("1", "2"):
        congestion = geh_ladder.node_congestion(demo_run_result, node_id)
        assert congestion["congested_movements"] == 0
        assert congestion["worst_los"] not in geh_ladder.CONGESTED_LOS_BANDS
        assert congestion["congested_approaches"] == []


_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_DB = (
    _REPO_ROOT
    / "runtime/workspaces/bfc69251bd04/uploads/current/Scenarios/S000001/S000001.results/1.db"
)


def test_the_availability_holds_on_a_real_customer_result_database():
    """The same measurement, against the real consultant model rather than the
    generator's output — read-only, ``immutable=1``, skipped when the database
    is not on this machine (the precedent
    ``test_agent_tools_run.py`` already sets for these files).

    It also pins the discrimination the SIGNAL_TIMING rung depends on, which a
    synthetic fixture cannot establish: on this network the congestion is NOT
    uniform. Nodes 1/2/3 have every approach in the congested band, node 4 has
    none, and nodes 5 and 8 have exactly one — so "concentrated" separates real
    intersections rather than being a distinction only a hand-built fixture can
    show.
    """
    if not _REAL_DB.exists():
        pytest.skip(f"real result database not present on this machine: {_REAL_DB}")

    rows = results_evidence.read_movement_class_rows([_REAL_DB], immutable=True)
    run_result = {
        "evidence": results_evidence.describe_result_db(_REAL_DB, immutable=True),
        geh_ladder.VOLUMES_KEY: {
            object_id: {"vehs": row["vehs"], "node": results_evidence.node_token(object_id)}
            for object_id, row in rows.items()
        },
        geh_ladder.QUEUE_KEY: results_evidence.read_queue_rows([_REAL_DB], immutable=True),
        geh_ladder.LOS_KEY: [
            {
                "movement": object_id,
                "los": results_evidence.los_band(row["vehdelay"]),
                "volume": row["vehs"],
                "delay_s": row["vehdelay"],
            }
            for object_id, row in rows.items()
        ],
    }

    evidence = geh_ladder.run_congestion_evidence(run_result)
    assert evidence["availability"] == geh_ladder.QUEUE_AND_DELAY
    assert evidence["queue"]["rows_with_a_length"] == 63
    # 58 of 63, not 63: the five without a delay are zero-volume movements
    # Vissim graded not at all, and read_movement_class_rows keeps them null
    # rather than inventing 0.0 (which would fabricate a LOS A).
    assert evidence["delay"]["rows_with_a_delay"] == 58

    uniform = {node_id: geh_ladder.node_congestion(run_result, node_id) for node_id in ("1", "2", "3")}
    for node_id, congestion in uniform.items():
        assert congestion["congested_movements"] > 0, node_id
        assert congestion["concentrated"] is False, node_id

    free = geh_ladder.node_congestion(run_result, "4")
    assert free["congested_movements"] == 0
    assert free["worst_los"] == "D"

    for node_id in ("5", "8"):
        congestion = geh_ladder.node_congestion(run_result, node_id)
        assert len(congestion["congested_approaches"]) == 1, node_id
        assert congestion["concentrated"] is True, node_id


# ===========================================================================
# 2. THE SYNTHETIC NODE — one intersection, four approaches
# ===========================================================================
#
# Movement ids are real Vissim OBJECT_ID shapes ("<node>-<from link>-<pos>-<to
# link>-<pos>") because that is what unit D4's entrance join reads: the
# workbook of the real client model records its turns this way (measured on the
# demo intersection's node 8), and a token like "W-E" is exactly the case that
# CANNOT be resolved to a link — which this file also pins, further down.

_NODE_ID = "5"
_APPROACHES = ("501", "503", "505", "507")


def _movement_id(approach: str, to_link: str) -> str:
    return f"{_NODE_ID}-{approach}-100.0-{to_link}-20.0"


def _observed(total: float, per_approach: float) -> dict[str, Any]:
    return {
        _NODE_ID: {
            "node_id": _NODE_ID,
            "node_name": "Synthetic five-way",
            "total_real": total,
            "total_sim_basam": None,
            "directions": [
                {
                    "direction": _movement_id(approach, "600"),
                    "real": per_approach,
                    "sim_basam": None,
                }
                for approach in _APPROACHES
            ],
        }
    }


def _run_result(
    *,
    modelled_per_approach: float,
    delays: dict[str, float] | None,
    queues: dict[str, float] | None,
) -> dict[str, Any]:
    """A ``run_result`` in exactly ``_collect_run_evidence``'s own shape."""
    movement_ids = {approach: _movement_id(approach, "600") for approach in _APPROACHES}
    payload: dict[str, Any] = {
        "evidence": {
            results_evidence.MOVEMENT_INTERVAL_TABLE: results_evidence.present(len(_APPROACHES)),
            results_evidence.MOVEMENT_INTERVAL_CLASS_TABLE: results_evidence.present(
                len(_APPROACHES)
            ),
        },
        geh_ladder.VOLUMES_KEY: {
            movement_id: {"vehs": modelled_per_approach, "node": _NODE_ID}
            for movement_id in movement_ids.values()
        },
    }
    if queues is not None:
        payload[geh_ladder.QUEUE_KEY] = {
            movement_ids[approach]: {"qlen": length / 2, "qlenmax": length, "samples": 1}
            for approach, length in queues.items()
        }
    if delays is not None:
        payload[geh_ladder.LOS_KEY] = [
            {
                "movement": movement_ids[approach],
                "los": results_evidence.los_band(delay),
                "volume": modelled_per_approach,
                "delay_s": delay,
            }
            for approach, delay in delays.items()
        ]
    return payload


#: Every approach at LOS F — a uniformly oversaturated intersection.
_ALL_CONGESTED = {approach: 120.0 for approach in _APPROACHES}
#: One approach at LOS F, three free — the shape a green-split problem makes.
_ONE_CONGESTED = {"501": 120.0, "503": 8.0, "505": 9.0, "507": 7.0}
#: Nothing above LOS A.
_NONE_CONGESTED = {approach: 8.0 for approach in _APPROACHES}
_QUEUES = {approach: 90.0 for approach in _APPROACHES}

#: The vehicle input that feeds approach 501 — with its LINK, which is what
#: makes the entrance checkable at all (unit D4).
_MEASURED_TARGET = {
    _NODE_ID: {
        "vehicle_input": {
            "input_no": "9",
            "current_volume": 500.0,
            "link_no": "501",
            "interval_no": 1,
        }
    }
}


#: "argument not given", so that an explicit ``None`` can mean "this half of
#: the run evidence is ABSENT" — the case three of the tests below are about.
_DEFAULT = object()


def _report(
    *,
    observed_total: float = 2000.0,
    observed_per_approach: float = 500.0,
    modelled_per_approach: float = 350.0,
    delays: Any = _DEFAULT,
    queues: Any = _DEFAULT,
    targets: Any = _DEFAULT,
    node_control: dict[str, str] | None = None,
    demand_target_reasons: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return reality_check.check_run_against_counts(
        _run_result(
            modelled_per_approach=modelled_per_approach,
            delays=_ALL_CONGESTED if delays is _DEFAULT else delays,
            queues=_QUEUES if queues is _DEFAULT else queues,
        ),
        _observed(observed_total, observed_per_approach),
        targets=_MEASURED_TARGET if targets is _DEFAULT else targets,
        node_control=node_control,
        demand_target_reasons=demand_target_reasons,
    )


def _diagnosis(report: dict[str, Any]) -> dict[str, Any]:
    entry = next(item for item in report["nodes"] if item["node_id"] == _NODE_ID)
    assert entry["diagnosis"] is not None
    return entry["diagnosis"]


def test_the_synthetic_entrance_really_reads_as_measured():
    """The premise of every capacity assertion below, checked at its source.

    If this join ever stops working the capacity tests would go green for the
    wrong reason — they would be exercising the UNVERIFIED path — so the
    verdict is pinned here on its own.

    It also pins WHERE D4's refusal rides in the case this unit exists for: the
    node total misses, so U4 built the demand refusal, then fell through to its
    routing arm, found no decision named, and refused there too — carrying the
    demand refusal on ``also_refused``. A ladder that only recognised the
    top-level ``reason_code`` would miss the very shape it was written for.
    """
    report = _report()
    entry = next(item for item in report["nodes"] if item["node_id"] == _NODE_ID)
    outcome = entry["outcome"]

    assert outcome["outcome"] == fixes.REFUSAL
    assert outcome["reason_code"] == fixes.NO_TARGET_OBJECT           # the routing arm
    assert outcome["also_refused"]["reason_code"] == fixes.INPUT_VOLUME_IS_OBSERVED_DATA

    entrance = outcome["also_refused"]["evidence"]["target"]["entrance_measurement"]
    assert entrance["status"] == fixes.ENTRANCE_MEASURED
    assert entrance["why"] == fixes.ENTRANCE_ROW_ON_INPUT_LINK
    # ... and the ladder read the same verdict, not a second derivation of it.
    assert entry["diagnosis"]["evidence"]["entrance_measurement"] == entrance


# ---------------------------------------------------------------------------
# CAPACITY_LIMIT — fires only on the full conjunction
# ---------------------------------------------------------------------------


def test_capacity_limit_fires_on_measured_input_plus_shortfall_plus_congestion():
    diagnosis = _diagnosis(_report())

    assert diagnosis["rung"] == geh_ladder.CAPACITY_LIMIT
    assert diagnosis["missing_evidence"] == []

    evidence = diagnosis["evidence"]
    assert evidence["throughput_shortfall"] is True
    assert evidence["throughput_shortfall_veh"] == 2000.0 - 4 * 350.0
    assert evidence["node_total_geh"] >= validation.GEH_GOOD_LT
    assert evidence["congestion"]["worst_los"] == "F"
    assert evidence["congestion"]["congested_movements"] == len(_APPROACHES)
    # Queue rides along as corroboration and is never thresholded.
    assert evidence["congestion"]["worst_qlenmax_m"] == 90.0


def test_capacity_limit_next_action_is_mrwa_step_7_with_the_saturation_flow_citation():
    """The acceptance criterion: no proposal, and the modeller still knows what
    to do next — in three languages, with the authority's own step number."""
    action = _diagnosis(_report())["next_action"]

    assert action["code"] == geh_ladder.MEASURE_SATURATION_FLOW
    assert action["mrwa_step"] == 7
    assert mrwa_reference.CALIBRATION_STEPS_CITATION in action["citation"]
    assert (
        mrwa_reference.CAR_FOLLOWING_FROM_SATURATION_FLOW_CITATION in action["citation"]
    )
    assert set(action["text"]) == {"ko", "en", "ja"}
    assert all(action["text"][language].strip() for language in ("ko", "en", "ja"))
    assert "포화교통류" in action["text"]["ko"]
    assert "saturation flow" in action["text"]["en"]
    assert "飽和交通流" in action["text"]["ja"]


def test_the_capacity_rung_says_the_input_is_data_in_all_three_languages():
    why = _diagnosis(_report())["why"]

    assert "용량" in why["ko"] and "실측" in why["ko"]
    assert "capacity" in why["en"] and "measured" in why["en"]
    assert "容量" in why["ja"]


def test_an_unverified_entrance_does_not_reach_the_capacity_rung():
    """The doctrine test. An entrance this system could not VERIFY as measured
    is not thereby free, but neither is it proof the demand is fixed — so the
    ladder stays on the demand rung (where U4 still proposes a labelled
    estimate) instead of announcing a capacity limit."""
    observed = _observed(2000.0, 500.0)
    # Turn tokens rather than OBJECT_IDs: the shape that carries no link, and
    # therefore the shape D4 cannot resolve to the input's entrance.
    for index, direction in enumerate(observed[_NODE_ID]["directions"]):
        direction["direction"] = f"leg{index}-through"

    report = reality_check.check_run_against_counts(
        _run_result(modelled_per_approach=350.0, delays=_ALL_CONGESTED, queues=_QUEUES),
        observed,
        targets=_MEASURED_TARGET,
    )
    diagnosis = _diagnosis(report)

    assert diagnosis["rung"] == geh_ladder.DEMAND_LEVEL
    assert diagnosis["next_action"]["mrwa_step"] == 10
    assert report["diagnosis"]["by_rung"] == {geh_ladder.DEMAND_LEVEL: [_NODE_ID]}


# ---------------------------------------------------------------------------
# SIGNAL_TIMING — concentration plus caller-supplied control
# ---------------------------------------------------------------------------


def test_signal_timing_fires_when_delay_is_concentrated_and_the_node_is_signalised():
    report = _report(
        delays=_ONE_CONGESTED, node_control={_NODE_ID: geh_ladder.CONTROL_SIGNALIZED}
    )
    diagnosis = _diagnosis(report)

    assert diagnosis["rung"] == geh_ladder.SIGNAL_TIMING
    assert diagnosis["evidence"]["congestion"]["concentrated"] is True
    assert diagnosis["evidence"]["congestion"]["congested_approaches"] == ["501"]
    assert diagnosis["next_action"]["code"] == geh_ladder.REVIEW_SIGNAL_TIMING
    # MRWA's own order: signals (6) come BEFORE saturation flow (7), and the
    # rung says so rather than leaving the reader to know it.
    assert diagnosis["next_action"]["mrwa_step"] == 6
    assert "⑦" in diagnosis["next_action"]["text"]["ko"]
    assert "step 7" in diagnosis["next_action"]["text"]["en"]


def test_uniform_oversaturation_is_capacity_not_signal_timing():
    """Every approach at LOS F is not a green-split story: there is no split
    that frees one approach without starving another."""
    report = _report(
        delays=_ALL_CONGESTED, node_control={_NODE_ID: geh_ladder.CONTROL_SIGNALIZED}
    )
    diagnosis = _diagnosis(report)

    assert diagnosis["rung"] == geh_ladder.CAPACITY_LIMIT
    assert diagnosis["evidence"]["congestion"]["concentrated"] is False


def test_concentration_without_a_control_answer_is_capacity_with_a_named_caveat():
    """Nothing in a run says whether a node has signals. So the rung that CAN
    be supported fires, and the one that cannot is named as a caveat rather
    than either asserted or silently dropped."""
    diagnosis = _diagnosis(_report(delays=_ONE_CONGESTED))

    assert diagnosis["rung"] == geh_ladder.CAPACITY_LIMIT
    assert diagnosis["missing_evidence"] == [geh_ladder.NODE_CONTROL_UNKNOWN]
    assert diagnosis["evidence"]["node_control"] == geh_ladder.CONTROL_UNKNOWN
    assert "⑥" in diagnosis["why"]["ko"]
    assert "step 6" in diagnosis["why"]["en"]


def test_an_unsignalised_node_does_not_get_the_signal_rung():
    diagnosis = _diagnosis(
        _report(delays=_ONE_CONGESTED, node_control={_NODE_ID: geh_ladder.CONTROL_UNSIGNALIZED})
    )

    assert diagnosis["rung"] == geh_ladder.CAPACITY_LIMIT
    # Control was ANSWERED, so there is no caveat to carry.
    assert diagnosis["missing_evidence"] == []


def test_an_unrecognised_control_hint_degrades_to_unknown_rather_than_raising():
    assert geh_ladder.normalize_control("SIGNALISED") == geh_ladder.CONTROL_SIGNALIZED
    assert geh_ladder.normalize_control("traffic lights?") == geh_ladder.CONTROL_UNKNOWN
    assert geh_ladder.normalize_control(None) == geh_ladder.CONTROL_UNKNOWN


# ---------------------------------------------------------------------------
# Delay COVERAGE — an ungraded approach is unknown, never free
# ---------------------------------------------------------------------------
#
# REGRESSION (refuter, 2026-08-18). ``concentrated`` was
# ``0 < congested_approaches <= approaches / 2``, taken over EVERY approach
# including the ones the run graded not at all. Vissim grades a zero-volume
# movement not at all -- this file's own availability test measures 5 such
# movements in the real client database -- and an approach that passed zero
# vehicles at an oversaturated intersection is the one most likely to be
# blocked. So the old test fired hardest in exactly the gridlock case the
# capacity rung exists for: one graded congested approach out of four made
# "concentrated" true, the rung became SIGNAL_TIMING, and the report told a
# client in three languages that "the delay sits on 1 of 4 approaches" -- a
# distribution nobody measured -- and sent them to MRWA step 6 instead of 7.

#: One congested approach GRADED; the other three passed no vehicles, so the
#: run carries no delay for them at all. The gridlock signature.
_ONE_CONGESTED_THREE_BLIND = {"501": 120.0}


def test_an_ungraded_approach_is_never_counted_as_a_free_one():
    """The synthesised misclassification, pinned at its source."""
    congestion = geh_ladder.node_congestion(
        _run_result(
            modelled_per_approach=350.0, delays=_ONE_CONGESTED_THREE_BLIND, queues=_QUEUES
        ),
        _NODE_ID,
    )

    assert congestion["movements"] == len(_APPROACHES)
    assert congestion["movements_with_delay"] == 1
    assert congestion["movements_without_delay"] == len(_APPROACHES) - 1
    assert congestion["approaches_without_delay"] == ["503", "505", "507"]
    assert congestion["congested_approaches"] == ["501"]
    # 1 congested + 3 unknown is not 1-of-4: it is anywhere from 1 to 4 of 4.
    assert congestion["concentrated"] is False


def test_a_starved_intersection_is_capacity_not_signal_timing():
    """Even told the node is signalised, the ladder may not claim a green-split
    story it has no delay distribution for."""
    diagnosis = _diagnosis(
        _report(
            delays=_ONE_CONGESTED_THREE_BLIND,
            node_control={_NODE_ID: geh_ladder.CONTROL_SIGNALIZED},
        )
    )

    assert diagnosis["rung"] == geh_ladder.CAPACITY_LIMIT
    assert diagnosis["next_action"]["mrwa_step"] == 7
    # The gap is NAMED, not silently swallowed...
    assert diagnosis["missing_evidence"] == [geh_ladder.DELAY_COVERAGE_INCOMPLETE]
    assert geh_ladder.DELAY_COVERAGE_INCOMPLETE in geh_ladder.MISSING_EVIDENCE_CODES
    # ... in all three languages, and it says how the blind approaches were
    # treated rather than only that they exist.
    assert "503, 505, 507" in diagnosis["why"]["en"]
    assert "UNKNOWN, not as free" in diagnosis["why"]["en"]
    assert "알 수 없음" in diagnosis["why"]["ko"]
    assert "不明" in diagnosis["why"]["ja"]
    # And the sentence that was false is gone.
    assert "1 of 4 approaches" not in diagnosis["why"]["en"]


def test_partial_grading_still_allows_concentration_it_cannot_overturn():
    """Not an over-refusal: with three of four approaches graded and one
    congested, the fourth being congested too would still leave 2 of 4 — at or
    under half — so the concentration claim survives its own worst case and the
    signal rung fires, carrying the coverage caveat."""
    diagnosis = _diagnosis(
        _report(
            delays={"501": 120.0, "503": 8.0, "505": 9.0},
            node_control={_NODE_ID: geh_ladder.CONTROL_SIGNALIZED},
        )
    )

    assert diagnosis["rung"] == geh_ladder.SIGNAL_TIMING
    assert diagnosis["evidence"]["congestion"]["approaches_without_delay"] == ["507"]
    assert diagnosis["missing_evidence"] == [geh_ladder.DELAY_COVERAGE_INCOMPLETE]


def test_a_fully_graded_node_carries_no_coverage_caveat():
    """The caveat must be genuinely absent when the run graded everything --
    otherwise it becomes noise on every report and stops being read."""
    for kwargs, control in (
        ({"delays": _ALL_CONGESTED}, None),
        ({"delays": _ONE_CONGESTED}, {_NODE_ID: geh_ladder.CONTROL_SIGNALIZED}),
        ({"delays": _ONE_CONGESTED}, {_NODE_ID: geh_ladder.CONTROL_UNSIGNALIZED}),
    ):
        diagnosis = _diagnosis(_report(node_control=control, **kwargs))
        assert diagnosis["evidence"]["congestion"]["approaches_without_delay"] == []
        assert geh_ladder.DELAY_COVERAGE_INCOMPLETE not in diagnosis["missing_evidence"]


def test_no_congestion_here_is_stated_only_of_the_movements_that_were_graded():
    """The same conflation on the other side of the ladder: "this node is not
    holding the traffic back" was asserted from one graded movement out of
    four, and sent the modeller upstream (step 11) on evidence that covered a
    quarter of the intersection."""
    diagnosis = _diagnosis(_report(delays={"501": 8.0}))

    assert diagnosis["rung"] == geh_ladder.UNCLASSIFIABLE
    assert diagnosis["missing_evidence"] == [
        geh_ladder.NO_CONGESTION_AT_THIS_NODE,
        geh_ladder.DELAY_COVERAGE_INCOMPLETE,
    ]
    assert "this node is not holding the traffic back" not in diagnosis["why"]["en"]
    assert "3 movement(s) carry no delay" in diagnosis["why"]["en"]
    for language in ("ko", "en", "ja"):
        assert diagnosis["why"][language].strip()
        assert diagnosis["next_action"]["text"][language].strip()


def test_the_no_evidence_rung_speaks_of_this_node_not_of_the_whole_run():
    """A node whose only run row is its own aggregate row joins with zero
    movements, so it reaches this rung on a run whose queue and delay tables are
    armed and full. "This run carries no queue or delay data" would then
    contradict the coverage block in the same report."""
    diagnosis = _diagnosis(_report(delays=None, queues=None))

    assert diagnosis["missing_evidence"] == [geh_ladder.NO_QUEUE_OR_DELAY_EVIDENCE]
    assert f"FOR NODE {_NODE_ID}" in diagnosis["why"]["en"]
    assert f"노드({_NODE_ID})" in diagnosis["why"]["ko"]
    assert f"ノード({_NODE_ID})" in diagnosis["why"]["ja"]


def test_the_coverage_block_names_WHICH_hcm_table_grades_the_delay():
    """The bands are the HCM SIGNALISED control-delay bands and are applied to
    every node, signalised or not. The unsignalised bands are stricter, so the
    grading under-reports congestion there — which is the conservative
    direction, but a reader of the report has to be able to see it rather than
    infer it from a module comment."""
    graded_by = _report()["coverage"]["congestion_evidence"]["graded_by"]

    assert "SIGNALISED" in graded_by
    assert "regardless of node_control" in graded_by
    assert "UNDER-reports" in graded_by


# ---------------------------------------------------------------------------
# UNCLASSIFIABLE — every way the conjunction breaks is NAMED
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        pytest.param(
            {"delays": None, "queues": None},
            geh_ladder.NO_QUEUE_OR_DELAY_EVIDENCE,
            id="no queue and no delay",
        ),
        pytest.param(
            {"delays": None},
            geh_ladder.DELAY_NOT_GRADABLE,
            id="queue only -- no cited numeric queue threshold exists",
        ),
        pytest.param(
            {"delays": _NONE_CONGESTED},
            geh_ladder.NO_CONGESTION_AT_THIS_NODE,
            id="nothing reaches the congested band",
        ),
        pytest.param(
            {"modelled_per_approach": 700.0},
            geh_ladder.NO_THROUGHPUT_SHORTFALL,
            id="the model moves MORE than was counted",
        ),
        pytest.param(
            {"targets": {}},
            geh_ladder.NO_DEMAND_TARGET,
            id="no vehicle input named, so the entrance was never checked",
        ),
    ],
)
def test_each_broken_precondition_degrades_to_a_named_unclassifiable(
    kwargs: dict, expected: str
):
    diagnosis = _diagnosis(_report(**kwargs))

    assert diagnosis["rung"] == geh_ladder.UNCLASSIFIABLE
    assert diagnosis["missing_evidence"] == [expected]
    assert expected in geh_ladder.MISSING_EVIDENCE_CODES
    # An unclassifiable rung is not a shrug: it still tells the reader what to
    # do, in three languages, and it does NOT borrow MRWA's authority for an
    # instruction MRWA did not give.
    for language in ("ko", "en", "ja"):
        assert diagnosis["why"][language].strip()
        assert diagnosis["next_action"]["text"][language].strip()


# ---------------------------------------------------------------------------
# #56 (b) — the target-less rung branches on WHY, never sends a 0-candidate
# node chasing get_vehicle_inputs
#
# DECISIONS.md, "같은 날 추가 측정 — CTA는 후보 0 노드에 불가능한 행동을
# 지시한다": before this unit every NO_DEMAND_TARGET node got the identical
# "name this node's vehicle input with get_vehicle_inputs" sentence, even a
# node whose counted approaches carry ZERO vehicle inputs -- an instruction
# with no possible answer, which is what sent the assistant back to
# get_vehicle_inputs on a loop and burned the iteration cap (the duplicate-
# call guard, orchestrator.py FIX 2).
# ---------------------------------------------------------------------------


def test_zero_candidate_reason_never_names_get_vehicle_inputs():
    """0 candidates (``NO_VEHICLE_INPUT_ON_APPROACH``): no input can be named,
    so the sentence must not tell a reader — human or assistant — to name
    one. It names the REAL fix instead: observation points / site→node
    mapping."""
    diagnosis = _diagnosis(
        _report(
            targets={},
            demand_target_reasons={
                _NODE_ID: {
                    "reason": reality_check_targets.NO_VEHICLE_INPUT_ON_APPROACH,
                    "candidate_count": 0,
                }
            },
        )
    )

    assert diagnosis["rung"] == geh_ladder.UNCLASSIFIABLE
    assert diagnosis["missing_evidence"] == [geh_ladder.NO_DEMAND_TARGET]
    next_action_text = diagnosis["next_action"]["text"]
    for language in ("ko", "en", "ja"):
        assert next_action_text[language].strip()
        assert "get_vehicle_inputs" not in next_action_text[language], (
            f"[{language}] a 0-candidate node's next action must not point at a tool that has "
            "nothing to name"
        )
    # The real fix, named, in each language.
    assert "관측 지점" in next_action_text["ko"] or "site" in next_action_text["ko"].lower()
    assert "observation" in next_action_text["en"].lower()
    assert "観測" in next_action_text["ja"]


def test_multiple_candidate_reason_names_the_picker_and_the_count():
    """2+ candidates (``MULTIPLE_VEHICLE_INPUT_CANDIDATES``): the sentence
    must say a PERSON chooses (the #54 picker / #56 (c)'s audit tool), never
    that this product names one automatically, and must state the count."""
    diagnosis = _diagnosis(
        _report(
            targets={},
            demand_target_reasons={
                _NODE_ID: {
                    "reason": reality_check_targets.MULTIPLE_VEHICLE_INPUT_CANDIDATES,
                    "candidate_count": 6,
                }
            },
        )
    )

    assert diagnosis["rung"] == geh_ladder.UNCLASSIFIABLE
    assert diagnosis["missing_evidence"] == [geh_ladder.NO_DEMAND_TARGET]
    next_action_text = diagnosis["next_action"]["text"]
    for language in ("ko", "en", "ja"):
        assert next_action_text[language].strip()
        assert "6" in next_action_text[language], f"[{language}] the candidate count must be named"
        assert "get_vehicle_inputs" not in next_action_text[language], (
            f"[{language}] several candidates still means no automatic pick — this must not send "
            "the reader to name one directly either"
        )
    assert "get_demand_target_derivation" in next_action_text["ko"]
    assert "get_demand_target_derivation" in next_action_text["en"]
    assert "get_demand_target_derivation" in next_action_text["ja"]


def test_multiple_candidate_reason_with_a_contradictory_count_falls_back():
    """``candidate_count`` under 2 contradicts
    ``MULTIPLE_VEHICLE_INPUT_CANDIDATES`` — guards against a mutation that
    drops the ``candidate_count >= 2`` guard and fires the picker branch on
    an internally-inconsistent audit."""
    diagnosis = _diagnosis(
        _report(
            targets={},
            demand_target_reasons={
                _NODE_ID: {
                    "reason": reality_check_targets.MULTIPLE_VEHICLE_INPUT_CANDIDATES,
                    "candidate_count": 1,
                }
            },
        )
    )

    assert diagnosis["next_action"]["text"]["en"] == (
        "Name this node's vehicle input (number, current volume, and LINK) from "
        "get_vehicle_inputs and re-check — the link is what makes the entrance "
        "checkable."
    )


def test_missing_demand_target_reason_keeps_the_pre_56_sentence_verbatim():
    """No audit threaded (``demand_target_reasons=None``, the default): the
    sentence is EXACTLY what it was before #56 -- the honest fallback for
    "why is not known here", never a guess at which of the two branches
    applies."""
    diagnosis = _diagnosis(_report(targets={}))

    next_action_text = diagnosis["next_action"]["text"]
    assert next_action_text["en"] == (
        "Name this node's vehicle input (number, current volume, and LINK) from "
        "get_vehicle_inputs and re-check — the link is what makes the entrance "
        "checkable."
    )


def test_an_unrecognised_reason_code_also_falls_back_rather_than_guessing():
    """A reason this branch does not recognise (or a candidate_count that
    contradicts MULTIPLE_VEHICLE_INPUT_CANDIDATES, e.g. < 2) must not be
    silently mapped to either named branch -- it is exactly the same "why is
    not known here" case as no audit at all."""
    diagnosis = _diagnosis(
        _report(
            targets={},
            demand_target_reasons={_NODE_ID: {"reason": "SOME_FUTURE_CODE", "candidate_count": 1}},
        )
    )

    assert diagnosis["next_action"]["text"]["en"] == (
        "Name this node's vehicle input (number, current volume, and LINK) from "
        "get_vehicle_inputs and re-check — the link is what makes the entrance "
        "checkable."
    )


def test_an_unverified_entrance_with_no_derivable_value_names_that_gap():
    """The other half of the D4 fall-through: the entrance could not be
    verified AND the demand arithmetic could not run either (no current
    volume). "Not verified as measured" is not "not measured", so the ladder
    refuses to descend and says which fact would let it."""
    observed = _observed(2000.0, 500.0)
    for index, direction in enumerate(observed[_NODE_ID]["directions"]):
        direction["direction"] = f"leg{index}-through"

    report = reality_check.check_run_against_counts(
        _run_result(modelled_per_approach=350.0, delays=_ALL_CONGESTED, queues=_QUEUES),
        observed,
        targets={_NODE_ID: {"vehicle_input": {"input_no": "9", "current_volume": None}}},
    )
    diagnosis = _diagnosis(report)

    assert diagnosis["rung"] == geh_ladder.UNCLASSIFIABLE
    assert diagnosis["missing_evidence"] == [geh_ladder.ENTRANCE_NOT_VERIFIED_MEASURED]
    assert diagnosis["evidence"]["entrance_measurement"]["status"] == fixes.ENTRANCE_UNVERIFIED


def test_an_undefined_geh_is_a_named_gap_not_a_pass():
    report = reality_check.check_run_against_counts(
        _run_result(modelled_per_approach=0.0, delays=_ALL_CONGESTED, queues=_QUEUES),
        _observed(0.0, 0.0),
        targets=_MEASURED_TARGET,
    )
    diagnosis = _diagnosis(report)

    assert diagnosis["rung"] == geh_ladder.UNCLASSIFIABLE
    assert diagnosis["missing_evidence"] == [geh_ladder.GEH_UNDEFINED]
    assert diagnosis["next_action"]["mrwa_step"] is None


def test_a_reconciling_node_gets_no_diagnosis_at_all():
    """No miss, no next action. A rung on a node that already reproduces the
    observed behaviour would be a to-do item invented out of nothing."""
    report = _report(modelled_per_approach=500.0, delays=_NONE_CONGESTED)
    entry = next(item for item in report["nodes"] if item["node_id"] == _NODE_ID)

    assert entry["outcome"]["reason_code"] == fixes.MODEL_REPRODUCES_OBSERVED
    assert entry["diagnosis"] is None
    assert report["diagnosis"]["by_rung"] == {}


# ---------------------------------------------------------------------------
# The report contract
# ---------------------------------------------------------------------------


def test_the_report_publishes_the_availability_measurement_either_way():
    """Whether or not a rung fired, the reader can tell "did not fire" from
    "could not look" — which is the whole reason this block is published."""
    fired = _report()
    blind = _report(delays=None, queues=None)

    assert fired["coverage"]["congestion_evidence"]["availability"] == geh_ladder.QUEUE_AND_DELAY
    assert blind["coverage"]["congestion_evidence"]["availability"] == geh_ladder.NEITHER
    # ... and on a report that never got as far as a run, too.
    absent = reality_check.not_checkable(reality_check.NO_RUN_RESULT)
    assert absent["coverage"]["congestion_evidence"]["availability"] == geh_ladder.NEITHER
    assert absent["diagnosis"]["by_rung"] == {}


def test_the_checked_message_gains_the_next_action_when_a_rung_names_one():
    report = _report()

    for language in ("ko", "en", "ja"):
        # The existing sentence is untouched...
        assert "movement_volumes" in report["message"][language]
        # ... and the ladder's is appended after it.
        assert "§2.12.1" in report["message"][language]
    assert "포화교통류" in report["message"]["ko"]
    assert "saturation flow" in report["message"]["en"]


def test_a_report_with_no_fired_rung_says_nothing_extra():
    """The clause is additive, so it must be genuinely absent when there is no
    concrete next action to name — an empty promise in the summary line is
    worse than no summary line."""
    report = _report(modelled_per_approach=500.0, delays=_NONE_CONGESTED)

    for language in ("ko", "en", "ja"):
        assert "§2.12.1" not in report["message"][language]


def test_the_ladder_never_proposes_anything():
    """#43 is diagnosis. Treatment is #44's, and this is the pin that keeps the
    boundary from eroding: no rung may carry a verb, params, or a confidence."""
    for kwargs in ({}, {"delays": _ONE_CONGESTED}, {"delays": None, "queues": None}):
        diagnosis = _diagnosis(_report(**kwargs))
        assert set(diagnosis) == {"rung", "why", "next_action", "missing_evidence", "evidence"}
        assert "verb" not in diagnosis and "params" not in diagnosis
        assert diagnosis["rung"] in geh_ladder.RUNGS


def test_the_diagnosis_never_contradicts_the_outcome_it_travels_with():
    """The ladder READS ``outcome``; it does not re-grade. So a node whose
    outcome is a proposal can only land on that proposal's own scope."""
    scope_by_rung = {
        geh_ladder.DEMAND_LEVEL: fixes.DEMAND_LEVEL,
        geh_ladder.ROUTING_SPLIT: fixes.ROUTING_SPLIT_LEVEL,
    }
    for kwargs in ({}, {"delays": _ONE_CONGESTED}, {"targets": {}}):
        report = _report(**kwargs)
        for entry in report["nodes"]:
            diagnosis = entry["diagnosis"]
            if diagnosis is None or entry["outcome"]["outcome"] != fixes.PROPOSAL:
                continue
            assert entry["outcome"]["scope"] == scope_by_rung[diagnosis["rung"]]


def test_every_rung_a_report_names_is_indexed_in_the_summary():
    report = _report()
    indexed = {
        node_id for node_ids in report["diagnosis"]["by_rung"].values() for node_id in node_ids
    }
    named = {
        entry["node_id"] for entry in report["nodes"] if entry["diagnosis"] is not None
    }

    assert indexed == named
    assert set(report["diagnosis"]["by_rung"]) <= set(geh_ladder.RUNGS)


# ---------------------------------------------------------------------------
# The citations
# ---------------------------------------------------------------------------


def test_every_cited_step_exists_in_the_reference_pack():
    """A next action's authority claim must resolve to a real §2.12.1 step —
    ``calibration_step`` raises rather than inventing one, and this walks every
    rung the ladder can emit."""
    seen: set[int] = set()
    for kwargs, control in (
        ({}, None),
        ({"delays": _ONE_CONGESTED}, {_NODE_ID: geh_ladder.CONTROL_SIGNALIZED}),
        ({"modelled_per_approach": 700.0}, None),
        ({"delays": _NONE_CONGESTED}, None),
    ):
        action = _diagnosis(_report(node_control=control, **kwargs))["next_action"]
        if action["mrwa_step"] is None:
            assert "no MRWA step applies" in action["citation"]
            continue
        step, label, citation = mrwa_reference.calibration_step(action["mrwa_step"])
        seen.add(step)
        assert label in action["citation"]
        assert citation in action["citation"]

    assert {6, 7, 11} <= seen


def test_every_citable_step_has_a_localized_label_and_the_documents_page_form():
    """The ko/ja citations must read as this project's documents write them —
    ``MRWA §2.12.1 ⑦(정지선 포화교통류), 문서 p.34-42`` — and "문서 p.N" must be
    the guideline's own printed page, not the source PDF's. A step that lost its
    label would silently fall back to English inside a Korean sentence, so the
    coverage is pinned rather than trusted."""
    for step in sorted(geh_ladder._STEP_LABELS):
        korean = geh_ladder._step_cite_ko(step)
        japanese = geh_ladder._step_cite_ja(step)
        assert geh_ladder._STEP_LABELS[step]["ko"] in korean
        assert geh_ladder._STEP_LABELS[step]["ja"] in japanese
        assert "문서 p.34-42" in korean
        assert "文書 p.34-42" in japanese
        # The English label must NOT leak into the localized string.
        _n, english_label, _citation = mrwa_reference.calibration_step(step)
        assert english_label not in korean

    # Every step the ladder can actually cite is covered.
    for kwargs, control in (
        ({}, None),
        ({"delays": _ONE_CONGESTED}, {_NODE_ID: geh_ladder.CONTROL_SIGNALIZED}),
        ({"modelled_per_approach": 700.0}, None),
        ({"delays": _NONE_CONGESTED}, None),
    ):
        step = _diagnosis(_report(node_control=control, **kwargs))["next_action"]["mrwa_step"]
        assert step is None or step in geh_ladder._STEP_LABELS


def test_the_reference_pack_carries_all_thirteen_steps_in_order():
    numbers = [number for number, _label, _citation in mrwa_reference.CALIBRATION_STEPS_2_12_1]

    assert numbers == list(range(1, 14))
    assert mrwa_reference.calibration_step(7)[1] == "stop-line saturation flow"
    assert mrwa_reference.calibration_step(6)[1] == "signalised intersections"
    with pytest.raises(KeyError):
        mrwa_reference.calibration_step(14)

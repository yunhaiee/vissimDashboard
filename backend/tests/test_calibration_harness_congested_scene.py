"""Unit #49 — the live harness's CONGESTED SCENE, verified without Vissim.

``tools/calibration_loop_live_check.py`` grew a second demo scene because the
first one cannot reach the rung it needs. Measured 2026-08-19: the as-built demo
model satisfies two of ``geh_ladder.CAPACITY_LIMIT``'s three preconditions (a
MEASURED entrance and a throughput shortfall) and fails the third — its worst
movement delay is LOS D, so nothing is in the congested band and
``--expect car-following`` stops by name. That stop is CORRECT (unit #43 pins
it as a negative test: an unblocked model must never be called a capacity
problem), which is exactly why the scene, and not the doctrine, is what had to
change.

What this file proves, all of it offline:

1. **The dataset edit is one input and nothing else.** ``scale_vehicle_input_volumes``
   multiplies one ``<vehicleInput>``'s own interval volumes, leaves every other
   input alone, and leaves a file Vissim's own parser still reads.
2. **The workbook arithmetic reaches the rung.** The designed observed side, put
   through the REAL loader, the REAL comparator and the REAL ladder over a
   synthetic congested run, produces: entrance MEASURED → unit D4's
   ``INPUT_VOLUME_IS_OBSERVED_DATA`` refusal → ``CAPACITY_LIMIT`` → MRWA step 7
   → three Table 5-6 car-following PROPOSALs. That is the whole live sequence
   ``--expect car-following`` walks, with only the run faked.
3. **The design refuses itself when it would not reach the rung**, naming the
   arithmetic rather than shipping a scene the live lane would misread.
4. **The as-built scene is untouched.** Same observed values, same rows, same
   labels as before #49 — asserted against the arithmetic they are defined by,
   not against a golden file (the workbook embeds a generation timestamp, so
   its bytes are not stable by construction).

No COM, no HTTP, no ``runtime/`` writes: every function exercised here is pure,
which is why they were factored out of the stage bodies in the first place.
"""
from __future__ import annotations

import inspect
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from services import results_evidence, validation
from services.agent import fixes, geh_ladder, reality_check, saturation_flow
from services.agent.tools_network import build_shadow_model
from tools import calibration_loop_live_check as harness


# ===========================================================================
# The synthetic congested run — node 8's real movement-id shapes
# ===========================================================================
#
# OBJECT_IDs are "<node>-<from link>-<pos>-<to link>-<pos>" because that is the
# only shape ``fixes.movement_from_link`` can resolve, and resolving the FROM
# link is what makes the entrance checkable at all (unit D4). The entrance id
# below is the real client workbook's own row for node 8's link-617 approach.

_DEMAND_NODE = harness.DEMAND_NODE
_REFUSAL_NODE = harness.REFUSAL_NODE
_ENTRANCE_LINK = "617"

#: Two movements enter node 8 from link 617 (the vehicle input's own link)...
_ENTRANCE_ROWS = {
    "8-617-1229.801336-617-1286.072980": 400.0,
    "8-617-1229.801336-620-10.0": 100.0,
}
#: ...and four enter from somewhere else. The scene designs NO deviation on
#: these, so the node's whole GEH miss is the entrance shortfall.
_OTHER_ROWS = {
    "8-12075-40.0-617-1286.072980": 120.0,
    "8-12075-40.0-759-15.0": 30.0,
    "8-759-12.0-617-1286.072980": 80.0,
    "8-759-12.0-620-10.0": 20.0,
}
_DEMAND_ROWS = {**_ENTRANCE_ROWS, **_OTHER_ROWS}
#: The control node: the model reproduces it, so it must be diagnosed not at all.
_REFUSAL_ROWS = {
    "5-501-10.0-600-20.0": 210.0,
    "5-503-10.0-600-20.0": 190.0,
}

#: The scene's parameters. 2063.4 veh/h is the as-built volume of vehicle input
#: 113 in the consultant network; x2.0 is ``DEFAULT_DEMAND_FACTOR``.
_AS_BUILT_VOLUME = 2063.4
_RAISED_VOLUME = _AS_BUILT_VOLUME * harness.DEFAULT_DEMAND_FACTOR
_DURATION_S = 900


def _run_result(*, congested: bool = True) -> dict[str, Any]:
    """A ``run_result`` in ``_collect_run_evidence``'s own shape.

    ``congested=False`` is the as-built model's measured picture — every
    movement graded, worst band LOS D — and is the case the ladder must NOT
    call a capacity problem.
    """
    rows = {**_DEMAND_ROWS, **_REFUSAL_ROWS}
    entrance_delay = 120.0 if congested else 47.0  # LOS F vs the measured LOS D
    delays = {
        object_id: (entrance_delay if object_id in _ENTRANCE_ROWS else 20.0)
        for object_id in rows
    }
    return {
        "evidence": {
            results_evidence.MOVEMENT_INTERVAL_TABLE: results_evidence.present(len(rows)),
            results_evidence.MOVEMENT_INTERVAL_CLASS_TABLE: results_evidence.present(len(rows)),
        },
        geh_ladder.VOLUMES_KEY: {
            object_id: {
                "vehs": vehs,
                "node": _DEMAND_NODE if object_id in _DEMAND_ROWS else _REFUSAL_NODE,
            }
            for object_id, vehs in rows.items()
        },
        geh_ladder.QUEUE_KEY: {
            object_id: {"qlen": 45.0, "qlenmax": 90.0, "samples": 1} for object_id in rows
        },
        geh_ladder.LOS_KEY: [
            {
                "movement": object_id,
                "los": results_evidence.los_band(delay),
                "volume": rows[object_id],
                "delay_s": delay,
            }
            for object_id, delay in delays.items()
        ],
    }


def _design(volume_vph: float = _RAISED_VOLUME) -> dict[str, Any]:
    return harness.design_congested_observed(
        demand_rows=_DEMAND_ROWS,
        entrance_link=_ENTRANCE_LINK,
        released_veh=harness.released_vehicles(volume_vph, _DURATION_S),
    )


def _workbook_bytes(design_block: dict[str, Any]) -> bytes:
    design: dict[str, Any] = {
        "time_int": f"0-{_DURATION_S}",
        "scene": harness.SCENE_CONGESTED,
        "demand_factor": harness.DEFAULT_DEMAND_FACTOR,
        "duration_s": _DURATION_S,
        "congested": design_block,
        "source_job_id": "offline-test",
        "source_db": None,
    }
    return harness._build_demo_workbook(
        _DEMAND_ROWS,
        _REFUSAL_ROWS,
        1.0,
        design,
        scene=harness.SCENE_CONGESTED,
        demand_observed=design_block["observed_by_id"],
    )


def _report(content: bytes, *, congested: bool = True) -> dict[str, Any]:
    """The REAL loader + the REAL comparator + the REAL ladder.

    The workbook goes in as BYTES through ``load_base_workbook_bytes`` — the
    same call ``POST /api/validation/base-workbook`` makes before it persists
    anything — so this test grades what the upload route would have accepted.
    """
    loaded = validation.load_base_workbook_bytes("DEMO_congested.xlsx", content)
    assert not loaded.get("warnings"), loaded.get("warnings")
    return reality_check.check_run_against_counts(
        _run_result(congested=congested),
        loaded,
        period="AM",
        targets={
            _DEMAND_NODE: {
                "vehicle_input": {
                    "input_no": harness.DEMAND_INPUT_NO,
                    "current_volume": _RAISED_VOLUME,
                    "link_no": _ENTRANCE_LINK,
                    "interval_no": 1,
                }
            }
        },
    )


def _node(report: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(item for item in report["nodes"] if str(item["node_id"]) == str(node_id))


# ===========================================================================
# 1. THE DATASET EDIT — one input, and a file Vissim still reads
# ===========================================================================


@pytest.fixture(scope="module")
def consultant_text() -> str:
    if not harness.CONSULTANT_INPX.is_file():
        pytest.skip(f"consultant network absent on this machine: {harness.CONSULTANT_INPX}")
    return harness.CONSULTANT_INPX.read_text(encoding="utf-8")


def test_scale_vehicle_input_volumes_touches_one_input_only(
    consultant_text: str, tmp_path: Path
) -> None:
    scaled, changes = harness.scale_vehicle_input_volumes(
        consultant_text, harness.DEMAND_INPUT_NO, 2.0
    )
    assert changes, "the scene must scale at least one interval"
    assert all(
        change["after"] == pytest.approx(change["before"] * 2.0) for change in changes
    )

    # Every differing line is one of the intervals it reported changing, so
    # "one input and nothing else" is shown rather than claimed -- the same
    # standard stage 0's numRuns preparation is held to.
    diff = harness._one_line_diff(consultant_text, scaled)
    changed_lines = [line for line in diff if line.startswith("line ")]
    assert len(changed_lines) == len(changes)

    path = tmp_path / "scaled.inpx"
    path.write_text(scaled, encoding="utf-8")
    ET.parse(str(path))  # still well-formed XML, not just string-substituted

    before = {str(v["no"]): v["volume"] for v in build_shadow_model(harness.CONSULTANT_INPX)["vehicle_inputs"]}
    after = {str(v["no"]): v["volume"] for v in build_shadow_model(path)["vehicle_inputs"]}
    assert after[harness.DEMAND_INPUT_NO] == pytest.approx(
        before[harness.DEMAND_INPUT_NO] * 2.0
    )
    assert {no: vol for no, vol in after.items() if no != harness.DEMAND_INPUT_NO} == {
        no: vol for no, vol in before.items() if no != harness.DEMAND_INPUT_NO
    }


def test_scale_vehicle_input_volumes_refuses_an_absent_input(consultant_text: str) -> None:
    # A scene that silently scaled nothing would run, look uncongested, and be
    # diagnosed as a product bug rather than as a mis-typed input number.
    with pytest.raises(ValueError, match="not found"):
        harness.scale_vehicle_input_volumes(consultant_text, "999999", 2.0)


#: A childless ``<vehicleInput>`` is written SELF-CLOSING -- the shape this
#: repo's own synthetic networks emit (``tools/make_demo_data.py``,
#: ``tools/make_showcase_network.py``), and the shape
#: ``scale_vehicle_input_volumes``'s docstring claims to handle.
_SELF_CLOSING_FIRST = """<vehicleInputs>
  <vehicleInput link="600" name="EMPTY" no="113"/>
  <vehicleInput link="700" name="OTHER" no="900">
    <timeIntVehVols>
      <timeIntervalVehVolume timeInt="1 0" volType="EXACT" volume="500"/>
      <timeIntervalVehVolume timeInt="1 900000" volType="EXACT" volume="600"/>
    </timeIntVehVols>
  </vehicleInput>
</vehicleInputs>"""

_SELF_CLOSING_BEFORE_TARGET = """<vehicleInputs>
  <vehicleInput link="600" name="EMPTY" no="112"/>
  <vehicleInput link="617" name="Murdoch Dr_NB" no="113">
    <timeIntVehVols>
      <timeIntervalVehVolume timeInt="1 0" volType="EXACT" volume="2063.4"/>
    </timeIntVehVols>
  </vehicleInput>
</vehicleInputs>"""


def test_scale_vehicle_input_volumes_never_crosses_an_element_boundary() -> None:
    """REGRESSION (refuter, 2026-08-19). A self-closing ``<vehicleInput/>``
    used to make the element pattern run past its own ``/>`` and swallow the
    NEXT input, so the scene rewrote a DIFFERENT input's volumes and reported
    them as the target's -- silently, with a full textual diff that looked
    exactly as convincing as a correct one. Both directions are pinned here.
    """
    # (a) the target is childless: nothing of ITS is scalable, and the input
    #     that follows it must not be touched or reported in its place.
    with pytest.raises(ValueError, match="no <timeIntervalVehVolume>"):
        harness.scale_vehicle_input_volumes(_SELF_CLOSING_FIRST, "113", 2.0)

    # (b) a childless input sits BEFORE the target: the target is still found
    #     and is still the only thing scaled.
    scaled, changes = harness.scale_vehicle_input_volumes(
        _SELF_CLOSING_BEFORE_TARGET, "113", 2.0
    )
    assert [(c["before"], c["after"]) for c in changes] == [(2063.4, 4126.8)]
    assert 'volume="4126.8"' in scaled
    # the input that is NOT the target keeps its own bytes exactly
    assert _SELF_CLOSING_BEFORE_TARGET.splitlines()[1] == scaled.splitlines()[1]

    # (c) the pattern isolates every element, self-closing or not -- one block
    #     per element, which is the property the two cases above rest on.
    #     ``<vehicleInputs>`` (the container) must not be counted as one.
    starts = re.compile(r"<vehicleInput\b")
    for document in (_SELF_CLOSING_FIRST, _SELF_CLOSING_BEFORE_TARGET):
        blocks = harness._VEHICLE_INPUT_RE.findall(document)
        assert len(blocks) == len(starts.findall(document)) == 2
        assert all(len(starts.findall(block)) == 1 for block in blocks)


def test_scale_vehicle_input_volumes_refuses_a_span_it_cannot_isolate() -> None:
    # An unclosed <vehicleInput> is the one shape the anchored pattern cannot
    # separate. Scaling it would rewrite the following input's volumes under
    # this input's name, so it refuses instead -- with the count in the message.
    malformed = """<vehicleInputs>
  <vehicleInput link="617" name="UNCLOSED" no="113">
    <timeIntVehVols>
      <timeIntervalVehVolume timeInt="1 0" volType="EXACT" volume="2063.4"/>
    </timeIntVehVols>
  <vehicleInput link="700" name="OTHER" no="900">
    <timeIntVehVols>
      <timeIntervalVehVolume timeInt="1 0" volType="EXACT" volume="500"/>
    </timeIntVehVols>
  </vehicleInput>
</vehicleInputs>"""
    with pytest.raises(ValueError, match="could not be isolated"):
        harness.scale_vehicle_input_volumes(malformed, "113", 2.0)


# ===========================================================================
# 2. THE ARITHMETIC — the entrance count IS the raised demand
# ===========================================================================


def test_entrance_observed_equals_what_the_raised_input_releases() -> None:
    design = _design()
    released = harness.released_vehicles(_RAISED_VOLUME, _DURATION_S)

    assert design["entrance_rows"] == sorted(_ENTRANCE_ROWS)
    assert design["other_rows"] == sorted(_OTHER_ROWS)
    # The identity the whole scene rests on: the workbook's entrance count and
    # the vehicle input's own demand are the same number, so D4 reads a
    # measurement rather than a knob. Rounding must not move it -- the parts are
    # apportioned by largest remainder precisely so they sum exactly.
    assert design["entrance_observed_total"] == round(released)
    assert design["released_veh"] == pytest.approx(released, abs=0.01)

    # No deviation is designed anywhere else.
    for object_id, modelled in _OTHER_ROWS.items():
        assert design["observed_by_id"][object_id] == round(modelled)

    assert design["shortfall_veh"] == pytest.approx(
        design["entrance_observed_total"] - design["entrance_modelled_total"]
    )
    assert design["node_total_geh"] >= validation.GEH_GOOD_LT


def _released_by_hand(intervals: list[dict[str, Any]], duration_s: float) -> float:
    """The released count, integrated independently of the harness's own code.

    Deliberately a second implementation: a regression that re-broke the
    schedule by calling the harness's helper from both sides would agree with
    itself and prove nothing.
    """
    total = 0.0
    for index, interval in enumerate(intervals):
        start = float(interval["from_s"])
        end = float(intervals[index + 1]["from_s"]) if index + 1 < len(intervals) else duration_s
        low, high = max(start, 0.0), min(end, duration_s)
        if high > low:
            total += float(interval["volume"]) * (high - low) / 3600.0
    return total


def test_released_vehicles_follows_the_inputs_own_interval_schedule() -> None:
    """REGRESSION (refuter, 2026-08-19). A Vissim vehicle input is a STEP
    FUNCTION, and ``build_shadow_model`` reports ``volume`` as interval 1's
    alone. Reading the released count as ``interval-1 volume x duration`` is
    right only while the run stops inside interval 1 -- ``DEFAULT_DURATION_S``
    is 900 s and interval 1 ends at exactly 900 s, so the default hid it. At
    ``--duration 1800`` the entrance count the workbook reports as COUNTED was
    54.3 vehicles short of what the input actually releases, and the identity
    the notes sheet asserts in ko/en was false by that much.
    """
    schedule = [
        {"from_s": 0.0, "volume": 4126.8},      # the as-built 2063.4, x2
        {"from_s": 900.0, "volume": 4344.0},    # x2 of 2172 -- a DIFFERENT rate
        {"from_s": 5400.0, "volume": 3909.6},   # x2 of 1954.8
    ]
    # Inside interval 1 the flat reading and the schedule agree, which is why
    # the default invocation was correct and the defect stayed invisible.
    for duration in (600, 900):
        assert harness.released_vehicles(4126.8, duration, intervals=schedule) == pytest.approx(
            harness.released_vehicles(4126.8, duration)
        )
    # Past it they must not, and the schedule is the one that is right.
    for duration in (1200, 1800, 3600, 5400, 7200):
        integrated = harness.released_vehicles(4126.8, duration, intervals=schedule)
        assert integrated == pytest.approx(_released_by_hand(schedule, duration))
        assert integrated > harness.released_vehicles(4126.8, duration)
    assert harness.released_vehicles(4126.8, 1800, intervals=schedule) == pytest.approx(2117.7)

    # An input with one interval, or none readable, still means what it meant.
    assert harness.released_vehicles(4126.8, 1800, intervals=[]) == pytest.approx(2063.4)
    assert harness.released_vehicles(
        4126.8, 1800, intervals=[{"from_s": 0.0, "volume": 4126.8}]
    ) == pytest.approx(2063.4)
    assert harness.released_vehicles(
        4126.8, 1800, intervals=[{"from_s": None, "volume": None}]
    ) == pytest.approx(2063.4)
    # A schedule that starts after the run ends releases nothing into it.
    assert harness.released_vehicles(
        4126.8, 300, intervals=[{"from_s": 900.0, "volume": 4126.8}]
    ) == pytest.approx(0.0)


def test_the_congested_stage_reads_the_schedule_and_not_just_interval_one() -> None:
    """The wiring, pinned. ``released_vehicles`` being schedule-aware buys
    nothing if stage 2 keeps handing it one number -- and that call is only
    reachable live, so it is pinned at the source the way the frontend contract
    test pins its own seams."""
    source = inspect.getsource(harness._stage_2_congested)
    assert "intervals=schedule" in source
    assert 'schedule = vi.get("volumes")' in source


def test_the_real_consultant_input_is_the_multi_interval_shape_this_guards(
    consultant_text: str,
) -> None:
    # Not a hypothetical: the network the scene actually runs carries six
    # intervals at three different rates.
    vi = harness._shadow_vehicle_input(harness.CONSULTANT_INPX, harness.DEMAND_INPUT_NO)
    assert vi is not None
    assert vi["interval_count"] > 1
    assert len({interval["volume"] for interval in vi["volumes"]}) > 1
    # ...and interval 1 ends exactly where the default duration does, which is
    # why the default invocation was right and every longer one was not.
    assert vi["volumes"][0]["volume"] == pytest.approx(_AS_BUILT_VOLUME)
    assert vi["volumes"][1]["from_s"] == pytest.approx(harness.DEFAULT_DURATION_S)
    assert harness.released_vehicles(
        _AS_BUILT_VOLUME, harness.DEFAULT_DURATION_S, intervals=vi["volumes"]
    ) == pytest.approx(
        harness.released_vehicles(_AS_BUILT_VOLUME, harness.DEFAULT_DURATION_S)
    )


def test_apportionment_sums_exactly_even_when_the_model_passed_nothing() -> None:
    # Total gridlock is a state this scene can genuinely produce, and it is the
    # one where a weight-proportional split has no weights to use.
    stuck = harness._apportion(1032, {"a": 0.0, "b": 0.0, "c": 0.0})
    assert sum(stuck.values()) == 1032
    assert max(stuck.values()) - min(stuck.values()) <= 1

    weighted = harness._apportion(1031, {"a": 400.0, "b": 100.0})
    assert sum(weighted.values()) == 1031
    assert weighted["a"] > weighted["b"]


# ===========================================================================
# 3. THE WHOLE PATH — workbook → MEASURED → CAPACITY_LIMIT → three proposals
# ===========================================================================


def test_congested_scene_reaches_the_capacity_rung_and_its_treatment() -> None:
    design = _design()
    assert harness.congested_design_problems(design, geh_threshold=validation.GEH_GOOD_LT) == []

    report = _report(_workbook_bytes(design))
    node = _node(report, _DEMAND_NODE)
    outcome = node["outcome"]

    # (a) the entrance is MEASURED -- because the workbook carries a counted row
    #     on the very link the vehicle input feeds.
    d4 = harness._d4_refusal(outcome)
    assert d4 is not None, outcome
    assert d4["reason_code"] == fixes.INPUT_VOLUME_IS_OBSERVED_DATA
    entrance = ((d4.get("evidence") or {}).get("target") or {}).get("entrance_measurement") or {}
    assert entrance["status"] == fixes.ENTRANCE_MEASURED
    assert entrance["why"] == fixes.ENTRANCE_ROW_ON_INPUT_LINK
    assert entrance["observed_on_input_link"] == design["entrance_observed_total"]

    # (b) the node total misses, and the model is short of the count.
    diagnosis = node["diagnosis"]
    assert diagnosis["rung"] == geh_ladder.CAPACITY_LIMIT, diagnosis
    assert diagnosis["evidence"]["throughput_shortfall"] is True
    assert diagnosis["evidence"]["node_total_geh"] >= validation.GEH_GOOD_LT

    # (c) the run's own delay puts a movement in the congested band.
    congestion = diagnosis["evidence"]["congestion"]
    assert congestion["congested_movements"] == len(_ENTRANCE_ROWS)
    assert congestion["worst_los"] in geh_ladder.CONGESTED_LOS_BANDS

    # (d) the ladder hands off to MRWA step 7, which is unit #44's entry point.
    assert diagnosis["next_action"]["code"] == geh_ladder.MEASURE_SATURATION_FLOW
    assert diagnosis["next_action"]["mrwa_step"] == 7
    assert report["diagnosis"]["by_rung"][geh_ladder.CAPACITY_LIMIT] == [_DEMAND_NODE]

    # (e) the control node reconciles, so it is diagnosed not at all.
    assert _node(report, _REFUSAL_NODE)["diagnosis"] is None

    # (f) #44's treatment builds on that rung: ask-first without a measurement,
    #     three cited Table 5-6 writes with one.
    behavior = {"no": "2", "name": "Urban", "reference_count": 0, "referenced_by": {}}
    refused = saturation_flow.propose_car_following_parameters(
        rung=diagnosis, measurement=None, behavior=behavior
    )
    assert [action.reason_code for action in refused] == [
        saturation_flow.NO_SATURATION_FLOW_MEASUREMENT
    ]

    measurement = saturation_flow.observed_measurement(
        saturation_flow_pcu=1900,
        method="MRWA Appendix B stop-line count",
        attribution="offline test fixture",
        samples=10,
        vehicles_per_sample=8,
        intersection=node.get("node_name") or _DEMAND_NODE,
    )
    assert measurement["ok"], measurement
    actions = saturation_flow.propose_car_following_parameters(
        rung=diagnosis, measurement=measurement, behavior=behavior
    )
    assert [action.outcome for action in actions] == [fixes.PROPOSAL] * 3
    assert [action.params["parameter"] for action in actions] == ["ax", "bx_add", "bx_mult"]


def test_the_same_scene_is_not_a_capacity_finding_when_the_model_is_not_congested() -> None:
    # The negative half, and the reason unit #49 exists: identical workbook,
    # identical shortfall, LOS D instead of LOS F. The ladder must refuse to
    # call it capacity and must NAME what it was waiting for -- which is exactly
    # what the as-built scene produced live on 2026-08-19.
    report = _report(_workbook_bytes(_design()), congested=False)
    diagnosis = _node(report, _DEMAND_NODE)["diagnosis"]
    assert diagnosis["rung"] == geh_ladder.UNCLASSIFIABLE
    assert geh_ladder.NO_CONGESTION_AT_THIS_NODE in diagnosis["missing_evidence"]

    # ...and the harness reads that back for the operator instead of a silence.
    block = harness._congested_rung_diagnostics(report)[_DEMAND_NODE]
    assert block["rung"] == geh_ladder.UNCLASSIFIABLE
    assert block["worst_los"] == "D"
    assert block["congested_movements"] == 0
    assert block["entrance_status"] == fixes.ENTRANCE_MEASURED


# ===========================================================================
# 4. THE DESIGN REFUSES ITSELF rather than ship a scene that cannot fire
# ===========================================================================


def test_a_factor_too_small_to_starve_the_node_is_refused_with_the_arithmetic() -> None:
    # A raised demand the model DOES digest: the released count barely exceeds
    # what the node passed, so there is no miss to diagnose and the live lane
    # would read the resulting UNCLASSIFIABLE as a product defect.
    design = _design(volume_vph=(sum(_ENTRANCE_ROWS.values()) + 2) * 3600 / _DURATION_S)
    problems = harness.congested_design_problems(design, geh_threshold=validation.GEH_GOOD_LT)
    assert problems
    assert any("--demand-factor" in problem for problem in problems)


def test_an_entrance_with_no_counted_row_is_refused_by_name() -> None:
    design = harness.design_congested_observed(
        demand_rows=_OTHER_ROWS,  # nothing enters from link 617
        entrance_link=_ENTRANCE_LINK,
        released_veh=1031.7,
    )
    problems = harness.congested_design_problems(design, geh_threshold=validation.GEH_GOOD_LT)
    assert any("entrance_measurement" in problem for problem in problems)


def test_the_adjustment_guidance_names_the_spillback_ceiling() -> None:
    # The one failure mode that raising the factor cannot fix, because Vissim
    # holds the overflow OUTSIDE the network where it carries no delay at all.
    guidance = "\n".join(harness._congested_adjustment_guidance(2.0))
    assert "--demand-factor" in guidance
    assert "outside the network" in guidance
    assert "NO_QUEUE_OR_DELAY_EVIDENCE" in guidance


# ===========================================================================
# 5. NO REGRESSION on the as-built scene
# ===========================================================================


def _counts_rows(content: bytes) -> list[list[Any]]:
    wb = openpyxl.load_workbook(io.BytesIO(content))
    return [list(row) for row in wb["Counts"].iter_rows(values_only=True)]


def test_as_built_scene_still_scales_every_row_by_the_one_factor() -> None:
    # The default scene's rule, unchanged since before #49: observed = round(
    # modelled x f) at the demand node, observed = round(modelled) at the
    # control node, both nodes labelled "DEMO 합성 실측". Asserted against the
    # arithmetic rather than a golden file: the workbook embeds its own
    # generation timestamp, so its bytes are not stable by construction.
    design: dict[str, Any] = {
        "time_int": f"0-{_DURATION_S}",
        "target_before_geh": harness.TARGET_BEFORE_GEH,
        "source_job_id": "offline-test",
        "source_db": None,
    }
    factor = 1.3043
    content = harness._build_demo_workbook(_DEMAND_ROWS, _REFUSAL_ROWS, factor, design)

    assert design["demand"]["per_movement"] == {
        object_id: {"modelled": modelled, "observed": int(round(modelled * factor))}
        for object_id, modelled in _DEMAND_ROWS.items()
    }
    assert design["refusal"]["per_movement"] == {
        object_id: {"modelled": modelled, "observed": int(round(modelled))}
        for object_id, modelled in _REFUSAL_ROWS.items()
    }
    labels = {row[3] for row in _counts_rows(content)[1:] if row[3]}
    assert labels == {
        f"{_DEMAND_NODE}: DEMO 합성 실측 노드{_DEMAND_NODE}",
        f"{_REFUSAL_NODE}: DEMO 합성 실측 노드{_REFUSAL_NODE}",
    }


def test_congested_workbook_says_what_it_is_on_its_own_notes_sheet() -> None:
    content = _workbook_bytes(_design())
    wb = openpyxl.load_workbook(io.BytesIO(content))
    notes = "\n".join(
        str(cell) for row in wb["DEMO_NOTES"].iter_rows(values_only=True) for cell in row if cell
    )
    # A reader who opens only the workbook must still be told that the demand
    # was raised BY DESIGN and that no measurement was rewritten to fit a model.
    assert harness.SCENE_CONGESTED in notes
    assert "보정이 아니라 모델 설계" in notes
    assert "not a calibration" in notes
    assert "D4" in notes

    labels = {row[3] for row in _counts_rows(content)[1:] if row[3]}
    assert labels == {
        f"{_DEMAND_NODE}: DEMO 합성 혼잡장면 노드{_DEMAND_NODE}",
        f"{_REFUSAL_NODE}: DEMO 합성 혼잡장면 노드{_REFUSAL_NODE}",
    }


# ===========================================================================
# 6. THE CLI GUARDS
# ===========================================================================


def test_cli_defaults_to_the_as_built_scene() -> None:
    args = harness.build_parser().parse_args([])
    assert args.scene == harness.SCENE_AS_BUILT
    assert args.demand_factor is None


@pytest.mark.parametrize(
    "argv",
    [
        ["--demand-factor", "2.0"],  # a factor with no scene would be ignored
        ["--scene", "congested", "--demand-factor", "1.0"],  # designs nothing
        ["--scene", "congested", "--demand-factor", "0.5"],  # designs the opposite
    ],
)
def test_cli_refuses_a_demand_factor_that_designs_no_scene(argv: list[str]) -> None:
    parser = harness.build_parser()
    with pytest.raises(SystemExit):
        harness.validate_args(parser, parser.parse_args(argv))

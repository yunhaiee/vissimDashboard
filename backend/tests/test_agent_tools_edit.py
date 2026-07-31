"""Tests for M2.3/v2 unit (2)'s tools_edit.py: the six write-tier tools,
dispatched through the REAL toolkit.dispatch (so pydantic validation + the
envelope machinery are exercised exactly like production), against a
hand-rolled ``FakeEditSession`` — no real Vissim COM anywhere in this file.

Covers: happy-path verification envelopes for all six tools, the
no-active-session structured error, a Vissim-refusal structured error
(never an exception leak), string-id -> int COM-key coercion, and that
``persist()`` is only ever called after a successful set+get (never on a
refused/raised write). ``set_reduced_speed_area`` (the sixth, XML-injection
tool -- see its own docstring) gets its own section near the bottom: it
never calls any ``FakeEditSession`` COM method at all, only reads
``.working_inpx``, and its own injection/reparse machinery is unit-tested
separately in test_simulation_network_builder.py -- what THIS file covers
for it is the dispatch-level wiring (params validation, no-session error,
the verification envelope shape, artifacts).
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

import services.agent.tools_edit as tools_edit  # noqa: F401 - import registers the 6 write tools
from services.agent.edit_session import clear_active_edit_session, set_active_edit_session
from services.agent.toolkit import default_registry, dispatch


class FakeEditSession:
    """Duck-types VissimEditSession's surface: records every call, returns
    rigged read-back values, optionally raises on a named op to simulate a
    live Vissim refusal (e.g. a read-only CycTm). Never touches real COM.
    """

    def __init__(
        self,
        readback: dict[str, Any] | None = None,
        raise_on: dict[str, Exception] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._readback = readback or {}
        self._raise_on = raise_on or {}
        self.persist_calls = 0
        self.closed = False

    def _record(self, op: str, **kwargs: Any) -> None:
        self.calls.append({"op": op, **kwargs})
        if op not in self._raise_on:
            return
        rule = self._raise_on[op]
        if isinstance(rule, list):
            # v2a unit ⑧: per-CALL sequence -- lets a test rig the FIRST call
            # to an op to succeed and a LATER one to raise (needed for
            # set_des_speed_decision's paired write, which calls
            # set_des_speed_decision twice: once for the primary decision,
            # once for the restore decision). A `None` entry means "succeed
            # this call"; the list is consumed in order and a call past the
            # end of the list never raises. Every EXISTING test passes a
            # bare Exception here (raises on every call to that op),
            # unchanged.
            if rule:
                exc = rule.pop(0)
                if exc is not None:
                    raise exc
        else:
            raise rule

    def set_vehicle_input_volume(self, input_no: int, volume: float, interval_no: int = 1) -> None:
        if interval_no == 1:
            self._record("set_vehicle_input_volume", input_no=input_no, volume=volume)
        else:
            self._record(
                "set_vehicle_input_volume", input_no=input_no, volume=volume, interval_no=interval_no
            )

    def get_vehicle_input_volume(self, input_no: int, interval_no: int = 1) -> float:
        if interval_no == 1:
            self._record("get_vehicle_input_volume", input_no=input_no)
        else:
            self._record("get_vehicle_input_volume", input_no=input_no, interval_no=interval_no)
        return self._readback["vehicle_input_volume"]

    def get_vehicle_input_continuous(self, input_no: int, interval_no: int) -> bool:
        self._record("get_vehicle_input_continuous", input_no=input_no, interval_no=interval_no)
        return self._readback.get("vehicle_input_continuous", False)

    def set_signal_cycle(self, sc_no: int, cycle: int) -> None:
        self._record("set_signal_cycle", sc_no=sc_no, cycle=cycle)

    def get_signal_cycle(self, sc_no: int) -> int:
        self._record("get_signal_cycle", sc_no=sc_no)
        return self._readback["signal_cycle"]

    def set_signal_offset(self, sc_no: int, offset: int) -> None:
        self._record("set_signal_offset", sc_no=sc_no, offset=offset)

    def get_signal_offset(self, sc_no: int) -> int:
        self._record("get_signal_offset", sc_no=sc_no)
        return self._readback["signal_offset"]

    def set_sim_duration(self, seconds: int) -> None:
        self._record("set_sim_duration", seconds=seconds)

    def get_sim_duration(self) -> int:
        self._record("get_sim_duration")
        return self._readback["sim_duration"]

    def set_random_seed(self, seed: int) -> None:
        self._record("set_random_seed", seed=seed)

    def get_random_seed(self) -> int:
        self._record("get_random_seed")
        return self._readback["random_seed"]

    def set_conflict_area_status(self, conflict_area_no: int, status: str) -> None:
        self._record("set_conflict_area_status", conflict_area_no=conflict_area_no, status=status)

    def get_conflict_area(self, conflict_area_no: int) -> dict[str, Any]:
        self._record("get_conflict_area", conflict_area_no=conflict_area_no)
        return self._readback["conflict_area"]

    def set_route_relative_flow(
        self, decision_no: int, route_no: int, rel_flow: float, interval_no: int = 1
    ) -> None:
        if interval_no == 1:
            self._record(
                "set_route_relative_flow", decision_no=decision_no, route_no=route_no, rel_flow=rel_flow
            )
        else:
            self._record(
                "set_route_relative_flow",
                decision_no=decision_no,
                route_no=route_no,
                rel_flow=rel_flow,
                interval_no=interval_no,
            )

    def get_route_relative_flow(self, decision_no: int, route_no: int, interval_no: int = 1) -> float:
        if interval_no == 1:
            self._record("get_route_relative_flow", decision_no=decision_no, route_no=route_no)
        else:
            self._record(
                "get_route_relative_flow",
                decision_no=decision_no,
                route_no=route_no,
                interval_no=interval_no,
            )
        return self._readback["route_relative_flow"]

    def set_vehicle_composition_share(
        self, composition_no: int, veh_type_no: int, rel_flow: float
    ) -> None:
        self._record(
            "set_vehicle_composition_share",
            composition_no=composition_no,
            veh_type_no=veh_type_no,
            rel_flow=rel_flow,
        )

    def get_vehicle_composition_share(self, composition_no: int, veh_type_no: int) -> float:
        self._record(
            "get_vehicle_composition_share", composition_no=composition_no, veh_type_no=veh_type_no
        )
        return self._readback["vehicle_composition_share"]

    def set_des_speed_decision(self, decision_no: int, veh_class_no: int, des_speed_distr_no: int) -> None:
        self._record(
            "set_des_speed_decision",
            decision_no=decision_no,
            veh_class_no=veh_class_no,
            des_speed_distr_no=des_speed_distr_no,
        )

    def get_des_speed_decision(self, decision_no: int, veh_class_no: int) -> int:
        self._record("get_des_speed_decision", decision_no=decision_no, veh_class_no=veh_class_no)
        readbacks = self._readback.get("des_speed_decision")
        if isinstance(readbacks, dict):
            # {(decision_no, veh_class_no): value} -- lets a test give the
            # PRIMARY and RESTORE reads two different rigged values (needed
            # for the atomic-pair happy-path/failure tests below).
            return readbacks[(decision_no, veh_class_no)]
        return readbacks

    def set_signal_green_split(
        self,
        sc_no: int,
        sg_no: int,
        green_start_s: float,
        green_end_s: float,
        program_no: int | None = None,
    ) -> dict[str, Any]:
        self._record(
            "set_signal_green_split",
            sc_no=sc_no,
            sg_no=sg_no,
            green_start_s=green_start_s,
            green_end_s=green_end_s,
            program_no=program_no,
        )
        # Mirrors VissimController.set_signal_green_split's return shape
        # (resolved controller_type + program actually written).
        return self._readback.get(
            "signal_green_split_written",
            {"controller_type": "FIXEDTIME", "program_no": program_no},
        )

    def get_signal_green_split(
        self, sc_no: int, sg_no: int, program_no: int | None = None
    ) -> dict[str, Any]:
        self._record("get_signal_green_split", sc_no=sc_no, sg_no=sg_no, program_no=program_no)
        return self._readback["signal_green_split"]

    def set_priority_rule_gap_headway(
        self,
        priority_rule_no: int,
        marker_index: int,
        gap_time_s: float | None,
        headway_m: float | None,
    ) -> dict[str, float]:
        self._record(
            "set_priority_rule_gap_headway",
            priority_rule_no=priority_rule_no,
            marker_index=marker_index,
            gap_time_s=gap_time_s,
            headway_m=headway_m,
        )
        # Mirrors VissimController.set_priority_rule_gap_headway's return
        # shape (the "in effect after this call" pair) -- a separate key
        # from the "get" readback below (default: same dict) lets a test
        # rig a genuine mismatch, same as set_signal_green_split's own
        # "_written" / plain key split.
        return self._readback.get("priority_rule_gap_headway_written", self._readback.get(
            "priority_rule_gap_headway"
        ))

    def get_priority_rule_gap_headway(self, priority_rule_no: int, marker_index: int) -> dict[str, float]:
        self._record(
            "get_priority_rule_gap_headway", priority_rule_no=priority_rule_no, marker_index=marker_index
        )
        return self._readback["priority_rule_gap_headway"]

    def set_des_speed_distribution_points(
        self, distribution_no: int, points: list[dict[str, float]]
    ) -> list[dict[str, float]]:
        self._record(
            "set_des_speed_distribution_points", distribution_no=distribution_no, points=points
        )
        # Mirrors VissimController.set_des_speed_distribution_points's own
        # return (an immediate post-write readback) -- a separate
        # "..._set_return" key (default: falls back to the plain readback
        # key) lets a test rig a genuine mismatch, same as
        # set_priority_rule_gap_headway's own "_written"/plain key split.
        # The TOOL ITSELF ignores this return value (see tools_edit.py --
        # written_value is always the caller's own intended points, never
        # whatever the setter call returns), so it exists here only for
        # completeness/symmetry with the real session's own method shape.
        return self._readback.get(
            "des_speed_distribution_points_set_return",
            self._readback.get("des_speed_distribution_points"),
        )

    def get_des_speed_distribution_points(self, distribution_no: int) -> list[dict[str, float]]:
        self._record("get_des_speed_distribution_points", distribution_no=distribution_no)
        return self._readback["des_speed_distribution_points"]

    def set_vehicle_type_reference(self, vehicle_type_no: int, field: str, target_no: int) -> None:
        self._record(
            "set_vehicle_type_reference", vehicle_type_no=vehicle_type_no, field=field, target_no=target_no
        )

    def get_vehicle_type_reference(self, vehicle_type_no: int, field: str) -> str | None:
        self._record("get_vehicle_type_reference", vehicle_type_no=vehicle_type_no, field=field)
        return self._readback["vehicle_type_reference"]

    def set_time_distribution_normal(
        self, time_distribution_no: int, mean_s: float | None, std_dev_s: float | None
    ) -> dict[str, Any]:
        self._record(
            "set_time_distribution_normal",
            time_distribution_no=time_distribution_no,
            mean_s=mean_s,
            std_dev_s=std_dev_s,
        )
        # Mirrors VissimController.set_time_distribution_normal's own return
        # (an immediate post-write readback of the full scalar dict) -- a
        # separate "..._set_return" key (default: falls back to the plain
        # readback key) lets a test rig a genuine mismatch, same as
        # set_priority_rule_gap_headway's own "_written"/plain key split.
        return self._readback.get(
            "time_distribution_normal_set_return", self._readback.get("time_distribution")
        )

    def get_time_distribution(self, time_distribution_no: int) -> dict[str, Any]:
        self._record("get_time_distribution", time_distribution_no=time_distribution_no)
        return self._readback["time_distribution"]

    def set_time_distribution_points(
        self, time_distribution_no: int, points: list[dict[str, float]]
    ) -> list[dict[str, float]]:
        self._record(
            "set_time_distribution_points", time_distribution_no=time_distribution_no, points=points
        )
        return self._readback.get(
            "time_distribution_points_set_return", self._readback.get("time_distribution_points")
        )

    def get_time_distribution_points(self, time_distribution_no: int) -> list[dict[str, float]]:
        self._record("get_time_distribution_points", time_distribution_no=time_distribution_no)
        return self._readback["time_distribution_points"]

    # QUEUE 3 unit 6: driving-behavior duplicate-then-edit.

    def duplicate_driving_behavior(self, source_no: int, new_name: str) -> dict[str, Any]:
        self._record("duplicate_driving_behavior", source_no=source_no, new_name=new_name)
        return self._readback["duplicate_driving_behavior"]

    def get_driving_behavior(self, behavior_no: int) -> dict[str, Any]:
        self._record("get_driving_behavior", behavior_no=behavior_no)
        return self._readback["driving_behavior"]

    def set_driving_behavior_parameter(self, behavior_no: int, parameter: str, value: float) -> dict[str, Any]:
        self._record(
            "set_driving_behavior_parameter", behavior_no=behavior_no, parameter=parameter, value=value
        )
        return self._readback.get(
            "driving_behavior_set_return", self._readback.get("driving_behavior")
        )

    def set_link_behavior_type_driving_behavior(
        self, link_behavior_type_no: int, driving_behavior_no: int, veh_class_no: int | None = None
    ) -> dict[str, Any]:
        self._record(
            "set_link_behavior_type_driving_behavior",
            link_behavior_type_no=link_behavior_type_no,
            driving_behavior_no=driving_behavior_no,
            veh_class_no=veh_class_no,
        )
        return self._readback.get(
            "link_behavior_type_assignment_set_return",
            self._readback.get("link_behavior_type_assignment"),
        )

    def get_link_behavior_type_driving_behavior(
        self, link_behavior_type_no: int, veh_class_no: int | None = None
    ) -> dict[str, Any]:
        self._record(
            "get_link_behavior_type_driving_behavior",
            link_behavior_type_no=link_behavior_type_no,
            veh_class_no=veh_class_no,
        )
        return self._readback["link_behavior_type_assignment"]

    def persist(self) -> None:
        self.persist_calls += 1

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_active_edit_session():
    clear_active_edit_session()
    yield
    clear_active_edit_session()


def _dispatch(name: str, **params: Any) -> dict[str, Any]:
    return dispatch(name, params, registry=default_registry)


# ---------------------------------------------------------------------------
# No active session -> structured error, never a crash
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name,params",
    [
        ("set_vehicle_input_volume", {"input_no": "1", "volume": 600}),
        ("set_signal_cycle", {"sc_no": "1", "cycle": 90}),
        ("set_signal_offset", {"sc_no": "1", "offset": 10}),
        ("set_sim_duration", {"seconds": 600}),
        ("set_random_seed", {"seed": 42}),
        (
            "set_reduced_speed_area",
            {"link_id": "101", "speed_kmh": 40.0, "from_m": 0.0, "length_m": 20.0},
        ),
        ("set_conflict_area_status", {"conflict_area_no": "3", "status": "AHASRIGHTOFWAY"}),
        (
            "set_route_relative_flow",
            {"decision_no": "1", "route_no": "1", "rel_flow": 500.0},
        ),
        (
            "set_vehicle_composition_share",
            {"composition_no": "1", "veh_type_no": "100", "rel_flow": 5.85},
        ),
        (
            "set_des_speed_decision",
            {"decision_no": "6", "veh_class_no": "10", "des_speed_distr_no": "25"},
        ),
        (
            "set_signal_green_split",
            {
                "sc_no": "1",
                "sg_no": "1",
                "program_no": "2",
                "green_start_s": 1.0,
                "green_end_s": 20.0,
            },
        ),
        (
            "set_priority_rule_gap_headway",
            {"priority_rule_no": "4", "marker_index": 1, "gap_time_s": 5.0},
        ),
        (
            "set_des_speed_distribution_points",
            {
                "distribution_no": "25",
                "points": [{"speed_kmh": 0.0, "cum_fraction": 0.0}, {"speed_kmh": 50.0, "cum_fraction": 1.0}],
            },
        ),
        (
            "set_vehicle_type_reference",
            {"vehicle_type_no": "200", "field": "weight_distr", "target_no": "3"},
        ),
    ],
)
def test_no_active_session_yields_structured_error_never_a_crash(tool_name, params):
    # No set_active_edit_session() call -- the module default is None.
    envelope = _dispatch(tool_name, **params)

    assert envelope["status"] == "error"
    # toolkit.dispatch can only ever report "tool_execution_error" for a
    # raised exception (see tools_edit.py's module docstring for why a
    # literal error_type: "no_edit_session" field is not reachable given
    # toolkit.py's fixed dispatch() mechanics) -- the case is still
    # unambiguously identifiable via the message text.
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]


# ---------------------------------------------------------------------------
# Vissim's own refusal -> structured error with Vissim's reason, no leak
# ---------------------------------------------------------------------------


def test_vissim_refusal_on_set_surfaces_as_structured_error_with_reason():
    session = FakeEditSession(
        raise_on={
            "set_signal_cycle": RuntimeError(
                "Could not set signal controller 5 cycle to 90: Attribute Cycle time cannot be changed"
            )
        }
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_signal_cycle", sc_no="5", cycle=90)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "Attribute Cycle time cannot be changed" in envelope["message"]
    # persist() must never be reached when the set() itself failed.
    assert session.persist_calls == 0


def test_vissim_refusal_never_leaks_a_raw_traceback():
    session = FakeEditSession(raise_on={"set_vehicle_input_volume": RuntimeError("boom")})
    set_active_edit_session(session)

    envelope = _dispatch("set_vehicle_input_volume", input_no="1", volume=600)

    assert envelope["status"] == "error"
    assert "Traceback" not in envelope["message"]
    assert "\n" not in envelope["message"]  # toolkit's exception sanitizer collapses newlines


# ---------------------------------------------------------------------------
# Happy path: set -> get (readback) -> persist -> verification envelope
# ---------------------------------------------------------------------------


def test_set_vehicle_input_volume_happy_path():
    session = FakeEditSession(readback={"vehicle_input_volume": 600.0})
    set_active_edit_session(session)

    envelope = _dispatch("set_vehicle_input_volume", input_no="1", volume=600.0)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 600.0, "readback_value": 600.0}
    assert session.persist_calls == 1
    assert session.calls == [
        {"op": "set_vehicle_input_volume", "input_no": 1, "volume": 600.0},
        {"op": "get_vehicle_input_volume", "input_no": 1},
    ]
    # input_no was coerced str -> int at the COM boundary.
    assert isinstance(session.calls[0]["input_no"], int)


def test_set_vehicle_input_volume_readback_mismatch_still_reports_ok_envelope():
    # The tool's OWN job is only to report both values -- comparing them and
    # refusing on mismatch is guardrails.py's postwrite job (constraint #6),
    # not tools_edit.py's. A rigged mismatch here must still flow through as
    # a normal "ok" envelope carrying BOTH values for that downstream check.
    session = FakeEditSession(readback={"vehicle_input_volume": 500.0})
    set_active_edit_session(session)

    envelope = _dispatch("set_vehicle_input_volume", input_no="1", volume=600.0)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 600.0, "readback_value": 500.0}


# ---------------------------------------------------------------------------
# set_vehicle_input_volume — interval_no (v2 unit ⑤)
# ---------------------------------------------------------------------------


def test_set_vehicle_input_volume_interval_1_is_byte_compatible_with_no_interval_no_param():
    # interval_no is not passed at all -- pydantic defaults it to 1. The
    # session must see EXACTLY the same 2-arg calls as before v2 unit ⑤ (no
    # interval_no anywhere, no get_vehicle_input_continuous call at all).
    session = FakeEditSession(readback={"vehicle_input_volume": 600.0})
    set_active_edit_session(session)

    envelope = _dispatch("set_vehicle_input_volume", input_no="1", volume=600.0)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 600.0, "readback_value": 600.0}
    assert session.calls == [
        {"op": "set_vehicle_input_volume", "input_no": 1, "volume": 600.0},
        {"op": "get_vehicle_input_volume", "input_no": 1},
    ]


def test_set_vehicle_input_volume_interval_no_2_happy_path():
    session = FakeEditSession(
        readback={"vehicle_input_volume": 1333.0, "vehicle_input_continuous": False}
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_vehicle_input_volume", input_no="2", volume=1333.0, interval_no=2)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": 1333.0,
        "readback_value": 1333.0,
        "cont_after": False,
    }
    assert "interval 2" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [
        {"op": "set_vehicle_input_volume", "input_no": 2, "volume": 1333.0, "interval_no": 2},
        {"op": "get_vehicle_input_volume", "input_no": 2, "interval_no": 2},
        {"op": "get_vehicle_input_continuous", "input_no": 2, "interval_no": 2},
    ]
    # input_no was coerced str -> int at the COM boundary, same as interval 1.
    assert isinstance(session.calls[0]["input_no"], int)


def test_set_vehicle_input_volume_interval_no_default_is_explicit_one():
    # interval_no=1 passed EXPLICITLY must take the exact same byte-compatible
    # path as omitting it entirely.
    session = FakeEditSession(readback={"vehicle_input_volume": 600.0})
    set_active_edit_session(session)

    envelope = _dispatch("set_vehicle_input_volume", input_no="1", volume=600.0, interval_no=1)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 600.0, "readback_value": 600.0}
    assert session.calls == [
        {"op": "set_vehicle_input_volume", "input_no": 1, "volume": 600.0},
        {"op": "get_vehicle_input_volume", "input_no": 1},
    ]


def test_set_vehicle_input_volume_interval_no_zero_is_rejected_at_param_layer():
    session = FakeEditSession(readback={"vehicle_input_volume": 600.0})
    set_active_edit_session(session)

    envelope = _dispatch("set_vehicle_input_volume", input_no="1", volume=600.0, interval_no=0)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_set_signal_cycle_happy_path():
    session = FakeEditSession(readback={"signal_cycle": 90})
    set_active_edit_session(session)

    envelope = _dispatch("set_signal_cycle", sc_no="5", cycle=90)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 90, "readback_value": 90}
    assert session.persist_calls == 1
    assert session.calls[0] == {"op": "set_signal_cycle", "sc_no": 5, "cycle": 90}


def test_set_signal_offset_happy_path():
    session = FakeEditSession(readback={"signal_offset": 15})
    set_active_edit_session(session)

    envelope = _dispatch("set_signal_offset", sc_no="5", offset=15)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 15, "readback_value": 15}
    assert session.persist_calls == 1
    assert session.calls[0] == {"op": "set_signal_offset", "sc_no": 5, "offset": 15}


def test_set_sim_duration_happy_path():
    session = FakeEditSession(readback={"sim_duration": 3600})
    set_active_edit_session(session)

    envelope = _dispatch("set_sim_duration", seconds=3600)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 3600, "readback_value": 3600}
    assert session.persist_calls == 1
    assert session.calls[0] == {"op": "set_sim_duration", "seconds": 3600}


def test_set_random_seed_happy_path():
    session = FakeEditSession(readback={"random_seed": 7})
    set_active_edit_session(session)

    envelope = _dispatch("set_random_seed", seed=7)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 7, "readback_value": 7}
    assert session.persist_calls == 1
    assert session.calls[0] == {"op": "set_random_seed", "seed": 7}


# ---------------------------------------------------------------------------
# set_conflict_area_status — v2 unit (4)
# ---------------------------------------------------------------------------


def test_set_conflict_area_status_happy_path():
    session = FakeEditSession(
        readback={"conflict_area": {"no": 3, "link_a": "3", "link_b": "12", "status": "AHASRIGHTOFWAY"}}
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_conflict_area_status", conflict_area_no="3", status="AHASRIGHTOFWAY")

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": "AHASRIGHTOFWAY", "readback_value": "AHASRIGHTOFWAY"}
    assert envelope["artifacts"] == {"link_a": "3", "link_b": "12"}
    assert "link 3 vs link 12" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [
        {"op": "set_conflict_area_status", "conflict_area_no": 3, "status": "AHASRIGHTOFWAY"},
        {"op": "get_conflict_area", "conflict_area_no": 3},
    ]
    # conflict_area_no was coerced str -> int at the COM boundary.
    assert isinstance(session.calls[0]["conflict_area_no"], int)


def test_set_conflict_area_status_readback_mismatch_still_reports_ok_envelope():
    # Same discipline as the other tools: comparing written vs read-back is
    # guardrails.py's postwrite job, not this tool's.
    session = FakeEditSession(
        readback={"conflict_area": {"no": 3, "link_a": "3", "link_b": "12", "status": "PASSIVE"}}
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_conflict_area_status", conflict_area_no="3", status="AHASRIGHTOFWAY")

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": "AHASRIGHTOFWAY", "readback_value": "PASSIVE"}


def test_set_conflict_area_status_rejects_a_status_outside_the_verified_vocabulary():
    # Empirically-verified vocabulary (backend/tools/conflict_area_probe.py,
    # all 25 real example networks): PASSIVE/AHASRIGHTOFWAY/BHASRIGHTOFWAY/
    # UNDETERMINED only -- anything else never reaches COM.
    session = FakeEditSession(
        readback={"conflict_area": {"no": 3, "link_a": "3", "link_b": "12", "status": "PASSIVE"}}
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_conflict_area_status", conflict_area_no="3", status="ACTIVE")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


# ---------------------------------------------------------------------------
# D3 live-demo fix (docs/phase2/DECISIONS.md, 2026-07-27): the set_
# conflict_area_status result the MODEL sees must stay compact -- the
# target's own read-back, a one-line verdict, and warning MESSAGES (already
# capped via constraints.py's _capped_join), never a warning's full,
# uncapped `details` (e.g. conflict_area_data_unavailable's 144-pair
# `link_pairs` list). Root-cause tracing (the live incident's actual
# trace/state.json) showed this was ALREADY true at dispatch time -- the
# reported "result too large to display" was actually planner.py's renderer
# demoting the write's own (already-compact) result out of the generous
# "most recent" budget, fixed separately in D2. This test pins the
# envelope-construction contract itself (toolkit.dispatch's leftover-keys
# rule + orchestrator._attach_guardrail_warnings's constraint_id+message-only
# rule) so a future change can't silently reintroduce the bloat, going
# through the REAL guardrail engine (not a hand-built Finding) against the
# exact real-network numbers from the live incident: 145 Passive conflict
# areas before the write, 144 after (conflict area 3 flips PASSIVE ->
# AHASRIGHTOFWAY).
# ---------------------------------------------------------------------------


def test_set_conflict_area_status_write_envelope_stays_compact_with_guardrail_warnings_attached():
    from services.agent.guardrails import GuardrailEngine
    from services.agent.orchestrator import _attach_guardrail_warnings

    session = FakeEditSession(
        readback={"conflict_area": {"no": 3, "link_a": "3", "link_b": "12", "status": "AHASRIGHTOFWAY"}}
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_conflict_area_status", conflict_area_no="3", status="AHASRIGHTOFWAY")
    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": "AHASRIGHTOFWAY",
        "readback_value": "AHASRIGHTOFWAY",
    }

    # The real shadow-model shape constraints.py expects: 145 Passive
    # conflict areas BEFORE the write (including #3 itself), 144 AFTER (#3
    # flipped) -- the exact live-incident numbers.
    def _conflict_areas(passive_count: int, area_3_status: str) -> list[dict[str, Any]]:
        areas = [
            {
                "no": "3",
                "link_a": "3",
                "link_b": "12",
                "link_pair": ("3", "12"),
                "status": area_3_status,
            }
        ]
        areas += [
            {
                "no": str(100 + i),
                "link_a": str(i),
                "link_b": str(i + 1000),
                "link_pair": (str(i), str(i + 1000)),
                "status": "PASSIVE",
            }
            for i in range(1, passive_count)  # passive_count total, #3 is one of them
        ]
        return areas

    before_model = {"conflict_areas": _conflict_areas(145, "PASSIVE")}
    after_model = {"conflict_areas": _conflict_areas(144, "AHASRIGHTOFWAY")}
    action = {
        "tool": "set_conflict_area_status",
        "params": {"conflict_area_no": "3", "status": "AHASRIGHTOFWAY"},
    }

    report = GuardrailEngine().postwrite(
        action, before_model, after_model, envelope["verification"]
    )
    warnings = report.warnings()
    constraint_ids = {finding.constraint_id for finding in warnings}
    assert "conflict_area_data_unavailable" in constraint_ids

    enriched = _attach_guardrail_warnings(envelope, warnings, "ko")

    # The target's own read-back must survive untouched.
    assert enriched["verification"] == {
        "written_value": "AHASRIGHTOFWAY",
        "readback_value": "AHASRIGHTOFWAY",
    }

    # No `details` (the full, uncapped 144-pair link_pairs list) ever rides
    # into the observation the planner renders -- only constraint_id +
    # the already-capped message.
    for warning in enriched["guardrail_warnings"]:
        assert set(warning.keys()) == {"constraint_id", "message"}
        if warning["constraint_id"] == "conflict_area_data_unavailable":
            # The message enumerates at most 8 pairs + a capped remainder
            # note (constraints.py's _capped_join, limit=8) -- never all 143
            # non-#3 pairs spelled out.
            assert "외" in warning["message"] or "more" in warning["message"]

    enriched_size = len(json.dumps(enriched, ensure_ascii=False, separators=(",", ":")))
    # Comfortably inside even the OLDER-entry snippet budget's generous
    # multiple -- nowhere near the ~6KB+ an uncapped 144-pair enumeration
    # would cost, let alone the tens-of-KB a full `details` dump would add.
    assert enriched_size < 2500, f"write envelope unexpectedly bloated: {enriched_size} chars"


# ---------------------------------------------------------------------------
# set_route_relative_flow — v2 unit (6)
# ---------------------------------------------------------------------------


def test_set_route_relative_flow_happy_path_interval_1_is_byte_compatible():
    session = FakeEditSession(readback={"route_relative_flow": 745.0})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_route_relative_flow", decision_no="1", route_no="1", rel_flow=745.0
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 745.0, "readback_value": 745.0}
    assert "raw weight" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [
        {"op": "set_route_relative_flow", "decision_no": 1, "route_no": 1, "rel_flow": 745.0},
        {"op": "get_route_relative_flow", "decision_no": 1, "route_no": 1},
    ]
    # decision_no/route_no were coerced str -> int at the COM boundary.
    assert isinstance(session.calls[0]["decision_no"], int)
    assert isinstance(session.calls[0]["route_no"], int)


def test_set_route_relative_flow_interval_no_2_happy_path():
    session = FakeEditSession(readback={"route_relative_flow": 999.0})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_route_relative_flow", decision_no="1", route_no="1", rel_flow=999.0, interval_no=2
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 999.0, "readback_value": 999.0}
    assert "interval 2" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [
        {
            "op": "set_route_relative_flow",
            "decision_no": 1,
            "route_no": 1,
            "rel_flow": 999.0,
            "interval_no": 2,
        },
        {"op": "get_route_relative_flow", "decision_no": 1, "route_no": 1, "interval_no": 2},
    ]


def test_set_route_relative_flow_readback_mismatch_still_reports_ok_envelope():
    # Same discipline as every other write tool: comparing written vs
    # read-back is guardrails.py's postwrite job, not this tool's.
    session = FakeEditSession(readback={"route_relative_flow": 139.0})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_route_relative_flow", decision_no="1", route_no="1", rel_flow=745.0
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 745.0, "readback_value": 139.0}


def test_set_route_relative_flow_interval_no_zero_is_rejected_at_param_layer():
    session = FakeEditSession(readback={"route_relative_flow": 745.0})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_route_relative_flow",
        decision_no="1",
        route_no="1",
        rel_flow=745.0,
        interval_no=0,
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_set_route_relative_flow_vissim_refusal_surfaces_as_structured_error():
    session = FakeEditSession(
        raise_on={
            "set_route_relative_flow": RuntimeError(
                "Could not set decision 1 route 99 interval 1 relative flow to 500.0: "
                "CCOMVehicleRouteStaticContainerBase::get_ItemByKey failed: object does not exist"
            )
        }
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_route_relative_flow", decision_no="1", route_no="99", rel_flow=500.0)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "object does not exist" in envelope["message"]
    assert session.persist_calls == 0


def test_set_route_relative_flow_extra_param_is_rejected_before_the_tool_ever_runs():
    session = FakeEditSession(readback={"route_relative_flow": 745.0})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_route_relative_flow",
        decision_no="1",
        route_no="1",
        rel_flow=745.0,
        extra_field="nope",
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_set_route_relative_flow_non_numeric_route_no_is_rejected_not_a_crash():
    session = FakeEditSession(readback={"route_relative_flow": 745.0})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_route_relative_flow", decision_no="1", route_no="not-a-number", rel_flow=745.0
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert session.persist_calls == 0


# ---------------------------------------------------------------------------
# set_vehicle_composition_share — v2 unit ⑦
# ---------------------------------------------------------------------------


def test_set_vehicle_composition_share_happy_path():
    session = FakeEditSession(readback={"vehicle_composition_share": 5.85})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_vehicle_composition_share", composition_no="1", veh_type_no="100", rel_flow=5.85
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 5.85, "readback_value": 5.85}
    assert "raw weight" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [
        {
            "op": "set_vehicle_composition_share",
            "composition_no": 1,
            "veh_type_no": 100,
            "rel_flow": 5.85,
        },
        {"op": "get_vehicle_composition_share", "composition_no": 1, "veh_type_no": 100},
    ]
    # composition_no/veh_type_no were coerced str -> int at the COM boundary.
    assert isinstance(session.calls[0]["composition_no"], int)
    assert isinstance(session.calls[0]["veh_type_no"], int)


def test_set_vehicle_composition_share_readback_mismatch_still_reports_ok_envelope():
    # Same discipline as every other write tool: comparing written vs
    # read-back is guardrails.py's postwrite job, not this tool's.
    session = FakeEditSession(readback={"vehicle_composition_share": 0.85})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_vehicle_composition_share", composition_no="1", veh_type_no="100", rel_flow=5.85
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 5.85, "readback_value": 0.85}


def test_set_vehicle_composition_share_vissim_refusal_surfaces_as_structured_error():
    session = FakeEditSession(
        raise_on={
            "set_vehicle_composition_share": RuntimeError(
                "Vehicle type 999 does not exist in this vehicle composition's relative-flow "
                "entries. Available vehicle type numbers: 100, 190, 200, 300."
            )
        }
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_vehicle_composition_share", composition_no="1", veh_type_no="999", rel_flow=5.0
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "does not exist" in envelope["message"]
    assert session.persist_calls == 0


def test_set_vehicle_composition_share_extra_param_is_rejected_before_the_tool_ever_runs():
    session = FakeEditSession(readback={"vehicle_composition_share": 5.85})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_vehicle_composition_share",
        composition_no="1",
        veh_type_no="100",
        rel_flow=5.85,
        extra_field="nope",
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_set_vehicle_composition_share_non_numeric_veh_type_no_is_rejected_not_a_crash():
    session = FakeEditSession(readback={"vehicle_composition_share": 5.85})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_vehicle_composition_share", composition_no="1", veh_type_no="not-a-number", rel_flow=5.85
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert session.persist_calls == 0


def test_set_vehicle_composition_share_missing_required_param_is_rejected():
    session = FakeEditSession(readback={"vehicle_composition_share": 5.85})
    set_active_edit_session(session)

    envelope = _dispatch("set_vehicle_composition_share", composition_no="1", veh_type_no="100")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


# ---------------------------------------------------------------------------
# set_des_speed_decision — v2a unit ⑧: UNPAIRED happy path (matches every
# other write tool's precedent exactly -- persist unconditionally, a
# readback mismatch is guardrails.py's postwrite job, not this tool's).
# ---------------------------------------------------------------------------


def test_set_des_speed_decision_unpaired_happy_path():
    # written_value/readback_value are STRINGS (an id, matching COM's own
    # AttValue read-back form and the shadow model's string-typed field --
    # see tools_edit.py's own comment at this tool) -- unlike route/vehicle-
    # composition RelFlow, which are floats.
    session = FakeEditSession(readback={"des_speed_decision": "25"})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_des_speed_decision", decision_no="6", veh_class_no="10", des_speed_distr_no="25"
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": "25", "readback_value": "25"}
    assert "restore_written_value" not in envelope["verification"]
    assert session.persist_calls == 1
    assert session.calls == [
        {"op": "set_des_speed_decision", "decision_no": 6, "veh_class_no": 10, "des_speed_distr_no": 25},
        {"op": "get_des_speed_decision", "decision_no": 6, "veh_class_no": 10},
    ]
    # decision_no/veh_class_no/des_speed_distr_no were coerced str -> int at
    # the COM boundary (SetAttValue), even though the ENVELOPE's own
    # written_value/readback_value stay strings.
    assert isinstance(session.calls[0]["decision_no"], int)
    assert isinstance(session.calls[0]["veh_class_no"], int)
    assert isinstance(session.calls[0]["des_speed_distr_no"], int)


def test_set_des_speed_decision_unpaired_readback_mismatch_still_reports_ok_envelope():
    # Same discipline as every OTHER single-target write tool in this module
    # (route/vehicle-composition/etc precedent): comparing written vs
    # read-back for the UNPAIRED case is guardrails.py's postwrite job, not
    # this tool's -- the atomic proactive check only activates when PAIRED
    # (see the paired tests below).
    session = FakeEditSession(readback={"des_speed_decision": "50"})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_des_speed_decision", decision_no="6", veh_class_no="10", des_speed_distr_no="25"
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": "25", "readback_value": "50"}
    assert session.persist_calls == 1


def test_set_des_speed_decision_vissim_refusal_surfaces_as_structured_error():
    session = FakeEditSession(
        raise_on={
            "set_des_speed_decision": RuntimeError(
                "AttValue failed: Attribute Desired speed distribution: Object 999999 of type "
                "Speed Distribution does not exist."
            )
        }
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_des_speed_decision", decision_no="6", veh_class_no="10", des_speed_distr_no="999999"
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "does not exist" in envelope["message"]
    assert session.persist_calls == 0


# ---------------------------------------------------------------------------
# set_des_speed_decision — ATOMIC PAIRED write (v2a unit ⑧'s central new
# behavior): both writes + both read-backs must succeed, or the whole
# action raises BEFORE persist() is ever called.
# ---------------------------------------------------------------------------


def test_set_des_speed_decision_paired_happy_path():
    session = FakeEditSession(
        readback={"des_speed_decision": {(6, 10): "25", (7, 10): "50"}}
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no="25",
        restore_decision_no="7",
        restore_distr_no="50",
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": "25",
        "readback_value": "25",
        "restore_written_value": "50",
        "restore_readback_value": "50",
    }
    assert "restore decision" in envelope["message"].lower() or "복원" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [
        {"op": "set_des_speed_decision", "decision_no": 6, "veh_class_no": 10, "des_speed_distr_no": 25},
        {"op": "get_des_speed_decision", "decision_no": 6, "veh_class_no": 10},
        {"op": "set_des_speed_decision", "decision_no": 7, "veh_class_no": 10, "des_speed_distr_no": 50},
        {"op": "get_des_speed_decision", "decision_no": 7, "veh_class_no": 10},
    ]


def test_set_des_speed_decision_paired_primary_readback_mismatch_aborts_before_restore_write():
    # The PRIMARY write's own readback disagrees -- unlike the unpaired case,
    # a paired write proactively checks this BEFORE ever touching the
    # restore decision or calling persist().
    session = FakeEditSession(readback={"des_speed_decision": {(6, 10): "50", (7, 10): "50"}})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no="25",  # written 25, but readback (rigged) reports 50
        restore_decision_no="7",
        restore_distr_no="50",
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "aborting" in envelope["message"].lower()
    # the restore decision was NEVER touched
    assert session.calls == [
        {"op": "set_des_speed_decision", "decision_no": 6, "veh_class_no": 10, "des_speed_distr_no": 25},
        {"op": "get_des_speed_decision", "decision_no": 6, "veh_class_no": 10},
    ]
    # nothing was persisted -- the whole action failed atomically.
    assert session.persist_calls == 0


def test_set_des_speed_decision_paired_restore_readback_mismatch_aborts_and_nothing_persists():
    # THE central atomicity test: the PRIMARY write succeeds and reads back
    # correctly, but the RESTORE write's own readback disagrees -- the whole
    # action must still fail, and persist() must NEVER be called, so the
    # primary write (already correct in COM's live memory) is not silently
    # saved as a half-paired edit.
    session = FakeEditSession(readback={"des_speed_decision": {(6, 10): "25", (7, 10): "25"}})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no="25",
        restore_decision_no="7",
        restore_distr_no="50",  # written 50, but readback (rigged) reports 25
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "restore" in envelope["message"].lower() or "복원" in envelope["message"]
    assert "not persisted" in envelope["message"].lower() or "atomic" in envelope["message"].lower()
    # BOTH sets were attempted (the tool doesn't know restore will mismatch
    # until it reads back), but persist() is the one thing that must NEVER
    # be reached.
    assert session.calls == [
        {"op": "set_des_speed_decision", "decision_no": 6, "veh_class_no": 10, "des_speed_distr_no": 25},
        {"op": "get_des_speed_decision", "decision_no": 6, "veh_class_no": 10},
        {"op": "set_des_speed_decision", "decision_no": 7, "veh_class_no": 10, "des_speed_distr_no": 50},
        {"op": "get_des_speed_decision", "decision_no": 7, "veh_class_no": 10},
    ]
    assert session.persist_calls == 0


def test_set_des_speed_decision_paired_restore_write_itself_raises_aborts_before_persist():
    # Vissim itself refuses the RESTORE write (e.g. a bogus restore_distr_no
    # that slipped past preflight) -- same atomic guarantee: raises, no
    # persist. The FIRST set_des_speed_decision call (primary) succeeds;
    # only the SECOND (restore) raises -- a per-call rigged sequence (v2a
    # unit ⑧'s FakeEditSession.raise_on list form).
    session = FakeEditSession(
        readback={"des_speed_decision": {(6, 10): "25"}},
        raise_on={
            "set_des_speed_decision": [None, RuntimeError("Restore decision write refused by Vissim")]
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no="25",
        restore_decision_no="7",
        restore_distr_no="50",
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "Restore decision write refused" in envelope["message"]
    assert session.persist_calls == 0
    # the restore write WAS attempted (that's what raised); persist() never ran.
    assert session.calls == [
        {"op": "set_des_speed_decision", "decision_no": 6, "veh_class_no": 10, "des_speed_distr_no": 25},
        {"op": "get_des_speed_decision", "decision_no": 6, "veh_class_no": 10},
        {"op": "set_des_speed_decision", "decision_no": 7, "veh_class_no": 10, "des_speed_distr_no": 50},
    ]


# ---------------------------------------------------------------------------
# set_des_speed_decision — param validation
# ---------------------------------------------------------------------------


def test_set_des_speed_decision_half_specified_restore_pair_is_rejected_as_invalid_params():
    session = FakeEditSession(readback={"des_speed_decision": "25"})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no="25",
        restore_decision_no="7",
        # restore_distr_no omitted -- half-specified pair.
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_set_des_speed_decision_half_specified_restore_pair_other_side_is_also_rejected():
    session = FakeEditSession(readback={"des_speed_decision": "25"})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no="25",
        restore_distr_no="50",
        # restore_decision_no omitted -- half-specified pair, the other way.
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_set_des_speed_decision_extra_param_is_rejected_before_the_tool_ever_runs():
    session = FakeEditSession(readback={"des_speed_decision": "25"})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_des_speed_decision",
        decision_no="6",
        veh_class_no="10",
        des_speed_distr_no="25",
        extra_field="nope",
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_set_des_speed_decision_non_numeric_veh_class_no_is_rejected_not_a_crash():
    session = FakeEditSession(readback={"des_speed_decision": "25"})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_des_speed_decision", decision_no="6", veh_class_no="not-a-number", des_speed_distr_no="25"
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert session.persist_calls == 0


def test_set_des_speed_decision_missing_required_param_is_rejected():
    session = FakeEditSession(readback={"des_speed_decision": "25"})
    set_active_edit_session(session)

    envelope = _dispatch("set_des_speed_decision", decision_no="6", veh_class_no="10")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


# ---------------------------------------------------------------------------
# set_signal_green_split — v2a unit ⑨. Verification carries the WHOLE green
# window as one dict on each side (the set_reduced_speed_area same-key-set
# precedent); program_no is REQUIRED (three-part identity, see tools_edit.py's
# own comment at this tool).
# ---------------------------------------------------------------------------


def test_set_signal_green_split_happy_path():
    session = FakeEditSession(
        readback={
            "signal_green_split": {
                "green_start_s": 1.0,
                "green_end_s": 8.0,
                "controller_type": "FIXEDTIME",
                "program_no": 2,
            },
            "signal_green_split_written": {"controller_type": "FIXEDTIME", "program_no": 2},
        }
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_signal_green_split",
        sc_no="1",
        sg_no="1",
        program_no="2",
        green_start_s=1.0,
        green_end_s=8.0,
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"green_start_s": 1.0, "green_end_s": 8.0},
        "readback_value": {"green_start_s": 1.0, "green_end_s": 8.0},
    }
    assert envelope["artifacts"] == {"controller_type": "FIXEDTIME", "program_no": 2}
    assert session.persist_calls == 1
    assert session.calls == [
        {
            "op": "set_signal_green_split",
            "sc_no": 1,
            "sg_no": 1,
            "green_start_s": 1.0,
            "green_end_s": 8.0,
            "program_no": 2,
        },
        {"op": "get_signal_green_split", "sc_no": 1, "sg_no": 1, "program_no": 2},
    ]
    # sc_no/sg_no/program_no coerced str -> int at the COM boundary.
    assert isinstance(session.calls[0]["sc_no"], int)
    assert isinstance(session.calls[0]["sg_no"], int)
    assert isinstance(session.calls[0]["program_no"], int)


def test_set_signal_green_split_vissim_refusal_surfaces_as_structured_error():
    # The exact live refusal shape from the probe: a program-driven
    # controller's per-SG attribute write path raising -- the wrapper must
    # surface it structurally and never persist.
    session = FakeEditSession(
        raise_on={
            "set_signal_green_split": RuntimeError(
                "AttValue failed: Object 1 - 1: 01: Attribute End of green cannot be changed."
            )
        }
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_signal_green_split",
        sc_no="1",
        sg_no="1",
        program_no="2",
        green_start_s=1.0,
        green_end_s=8.0,
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "cannot be changed" in envelope["message"]
    assert session.persist_calls == 0


def test_set_signal_green_split_missing_program_no_is_rejected():
    # program_no is REQUIRED -- a two-part call must be rejected at the
    # pydantic layer, never guessed to mean "the active program" silently.
    session = FakeEditSession(readback={"signal_green_split": {}})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_signal_green_split", sc_no="1", sg_no="1", green_start_s=1.0, green_end_s=8.0
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


# ---------------------------------------------------------------------------
# set_priority_rule_gap_headway -- QUEUE 3 unit 2
# ---------------------------------------------------------------------------


def test_set_priority_rule_gap_headway_both_fields_happy_path():
    session = FakeEditSession(
        readback={"priority_rule_gap_headway": {"gap_time_s": 5.5, "headway_m": 22.0}}
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_priority_rule_gap_headway",
        priority_rule_no="4",
        marker_index=1,
        gap_time_s=5.5,
        headway_m=22.0,
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"gap_time_s": 5.5, "headway_m": 22.0},
        "readback_value": {"gap_time_s": 5.5, "headway_m": 22.0},
    }
    assert session.persist_calls == 1
    assert session.calls == [
        {
            "op": "set_priority_rule_gap_headway",
            "priority_rule_no": 4,
            "marker_index": 1,
            "gap_time_s": 5.5,
            "headway_m": 22.0,
        },
        {"op": "get_priority_rule_gap_headway", "priority_rule_no": 4, "marker_index": 1},
    ]
    # priority_rule_no was coerced str -> int at the COM boundary.
    assert isinstance(session.calls[0]["priority_rule_no"], int)
    assert "gap time -> 5.5s" in envelope["message"]
    assert "headway/clearance -> 22.0m" in envelope["message"]


def test_set_priority_rule_gap_headway_gap_only_still_reports_the_full_pair():
    # headway_m not given -- the tool must still pass None through (no write
    # to that attribute) but the resulting envelope always carries BOTH
    # fields (the unchanged headway_m read back at its current value).
    session = FakeEditSession(
        readback={"priority_rule_gap_headway": {"gap_time_s": 5.5, "headway_m": 0.0}}
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=5.5
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"gap_time_s": 5.5, "headway_m": 0.0},
        "readback_value": {"gap_time_s": 5.5, "headway_m": 0.0},
    }
    assert session.calls[0] == {
        "op": "set_priority_rule_gap_headway",
        "priority_rule_no": 4,
        "marker_index": 1,
        "gap_time_s": 5.5,
        "headway_m": None,
    }
    assert "gap time -> 5.5s" in envelope["message"]
    assert "headway/clearance" not in envelope["message"]


def test_set_priority_rule_gap_headway_headway_only():
    session = FakeEditSession(
        readback={"priority_rule_gap_headway": {"gap_time_s": 3.2, "headway_m": 22.0}}
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, headway_m=22.0
    )

    assert envelope["status"] == "ok"
    assert session.calls[0] == {
        "op": "set_priority_rule_gap_headway",
        "priority_rule_no": 4,
        "marker_index": 1,
        "gap_time_s": None,
        "headway_m": 22.0,
    }
    assert "headway/clearance -> 22.0m" in envelope["message"]
    assert "gap time ->" not in envelope["message"]


def test_set_priority_rule_gap_headway_neither_field_rejected_at_param_layer():
    session = FakeEditSession(readback={"priority_rule_gap_headway": {}})
    set_active_edit_session(session)

    envelope = _dispatch("set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_set_priority_rule_gap_headway_readback_mismatch_still_reports_ok_envelope():
    # The tool's OWN job is only to report both values -- comparing them and
    # refusing on mismatch is guardrails.py's postwrite job (constraint #6).
    session = FakeEditSession(
        readback={
            "priority_rule_gap_headway_written": {"gap_time_s": 5.5, "headway_m": 22.0},
            "priority_rule_gap_headway": {"gap_time_s": 3.2, "headway_m": 22.0},
        }
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=5.5
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"gap_time_s": 5.5, "headway_m": 22.0},
        "readback_value": {"gap_time_s": 3.2, "headway_m": 22.0},
    }


def test_set_priority_rule_gap_headway_vissim_refusal_surfaces_as_structured_error():
    # The exact live refusal shape from the probe: a negative value raises.
    session = FakeEditSession(
        raise_on={
            "set_priority_rule_gap_headway": RuntimeError(
                "AttValue failed: Value -1 s is lower than minimum value of attribute Minimum "
                "gap time (Min: 0 s)."
            )
        }
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_priority_rule_gap_headway", priority_rule_no="4", marker_index=1, gap_time_s=-1.0
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "lower than minimum value" in envelope["message"]
    assert session.persist_calls == 0


def test_set_priority_rule_gap_headway_marker_index_zero_rejected_at_param_layer():
    # marker_index is 1-based (ge=1) -- 0 is never a valid ordinal.
    session = FakeEditSession(readback={"priority_rule_gap_headway": {}})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_priority_rule_gap_headway", priority_rule_no="4", marker_index=0, gap_time_s=5.0
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


# ---------------------------------------------------------------------------
# set_des_speed_distribution_points -- QUEUE 3 (v2a) unit 3
# ---------------------------------------------------------------------------


def test_set_des_speed_distribution_points_happy_path():
    new_points = [
        {"speed_kmh": 0.0, "cum_fraction": 0.0},
        {"speed_kmh": 30.0, "cum_fraction": 0.5},
        {"speed_kmh": 60.0, "cum_fraction": 1.0},
    ]
    session = FakeEditSession(readback={"des_speed_distribution_points": new_points})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_des_speed_distribution_points",
        distribution_no="25",
        points=new_points,
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": new_points,
        "readback_value": new_points,
    }
    assert session.persist_calls == 1
    assert session.calls == [
        {"op": "set_des_speed_distribution_points", "distribution_no": 25, "points": new_points},
        {"op": "get_des_speed_distribution_points", "distribution_no": 25},
    ]
    # distribution_no was coerced str -> int at the COM boundary.
    assert isinstance(session.calls[0]["distribution_no"], int)
    assert "3 point(s)" in envelope["message"]
    # D3 live-demo fix: the message states the point COUNT but never inlines
    # the full readback list itself (unbounded in curve length) -- the full
    # curve lives in verification.readback_value, asserted above.
    assert "speed_kmh" not in envelope["message"]
    assert "cum_fraction" not in envelope["message"]


def test_set_des_speed_distribution_points_readback_mismatch_still_reports_ok_envelope():
    # The tool's OWN job is only to report both values -- comparing them and
    # refusing on mismatch is guardrails.py's postwrite job (constraint #6).
    written_points = [{"speed_kmh": 0.0, "cum_fraction": 0.0}, {"speed_kmh": 50.0, "cum_fraction": 1.0}]
    saved_points = [{"speed_kmh": 0.0, "cum_fraction": 0.0}, {"speed_kmh": 49.0, "cum_fraction": 1.0}]
    session = FakeEditSession(readback={"des_speed_distribution_points": saved_points})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_des_speed_distribution_points", distribution_no="25", points=written_points
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": written_points,
        "readback_value": saved_points,
    }


def test_set_des_speed_distribution_points_vissim_refusal_surfaces_as_structured_error():
    # The exact live refusal shape from the probe: fewer than 2 points.
    session = FakeEditSession(
        raise_on={
            "set_des_speed_distribution_points": RuntimeError(
                "Container SpeedDistributionDataPoints must contain at least 2 item(s)."
            )
        }
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_des_speed_distribution_points",
        distribution_no="25",
        points=[{"speed_kmh": 50.0, "cum_fraction": 1.0}],
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "at least 2 item" in envelope["message"]
    assert session.persist_calls == 0


def test_set_des_speed_distribution_points_empty_list_is_schema_legal_refused_by_constraints_not_pydantic():
    # An empty points list is NOT rejected at the pydantic layer (no
    # min_length on the model) -- per this unit's own design, the ">= 2
    # points" invariant is entirely constraints.py's job (bilingual,
    # structured), not a bare pydantic ValidationError. Exercised here only
    # to confirm dispatch-level plumbing accepts the shape; the actual
    # refusal is covered in test_agent_constraints.py.
    session = FakeEditSession(raise_on={
        "set_des_speed_distribution_points": RuntimeError(
            "Container SpeedDistributionDataPoints must contain at least 2 item(s)."
        )
    })
    set_active_edit_session(session)

    envelope = _dispatch("set_des_speed_distribution_points", distribution_no="25", points=[])

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"


# ---------------------------------------------------------------------------
# set_vehicle_type_reference(vehicle_type_no, field, target_no) -- QUEUE
# (v2a) unit
# ---------------------------------------------------------------------------


def test_set_vehicle_type_reference_happy_path():
    session = FakeEditSession(readback={"vehicle_type_reference": "3"})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_vehicle_type_reference", vehicle_type_no="200", field="weight_distr", target_no="3"
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": "3", "readback_value": "3"}
    assert "weight_distr" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [
        {"op": "set_vehicle_type_reference", "vehicle_type_no": 200, "field": "weight_distr", "target_no": 3},
        {"op": "get_vehicle_type_reference", "vehicle_type_no": 200, "field": "weight_distr"},
    ]
    # vehicle_type_no/target_no were coerced str -> int at the COM boundary.
    assert isinstance(session.calls[0]["vehicle_type_no"], int)
    assert isinstance(session.calls[0]["target_no"], int)


def test_set_vehicle_type_reference_a_different_field_dispatches_that_field():
    session = FakeEditSession(readback={"vehicle_type_reference": "2"})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_vehicle_type_reference", vehicle_type_no="200", field="des_accel_func", target_no="2"
    )

    assert envelope["status"] == "ok"
    assert session.calls[0]["field"] == "des_accel_func"


def test_set_vehicle_type_reference_readback_mismatch_still_reports_ok_envelope():
    session = FakeEditSession(readback={"vehicle_type_reference": "2"})  # write was 3, readback shows 2
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_vehicle_type_reference", vehicle_type_no="200", field="weight_distr", target_no="3"
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": "3", "readback_value": "2"}


def test_set_vehicle_type_reference_vissim_refusal_surfaces_as_structured_error():
    session = FakeEditSession(
        raise_on={
            "set_vehicle_type_reference": RuntimeError(
                "Could not set vehicle type 200 weight_distr to 999999: Object 999999 of type "
                "Weight Distribution does not exist."
            )
        }
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_vehicle_type_reference", vehicle_type_no="200", field="weight_distr", target_no="999999"
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "Weight Distribution does not exist" in envelope["message"]
    assert session.persist_calls == 0


def test_set_vehicle_type_reference_invalid_field_is_rejected_at_param_layer():
    session = FakeEditSession(readback={"vehicle_type_reference": "3"})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_vehicle_type_reference", vehicle_type_no="200", field="not_a_real_field", target_no="3"
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_set_vehicle_type_reference_extra_param_is_rejected_before_the_tool_ever_runs():
    session = FakeEditSession(readback={"vehicle_type_reference": "3"})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_vehicle_type_reference",
        vehicle_type_no="200",
        field="weight_distr",
        target_no="3",
        extra_field="nope",
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_set_vehicle_type_reference_non_numeric_target_no_is_rejected_not_a_crash():
    session = FakeEditSession(readback={"vehicle_type_reference": "3"})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_vehicle_type_reference", vehicle_type_no="200", field="weight_distr", target_no="not-a-number"
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert session.calls == []


def test_set_vehicle_type_reference_missing_required_param_is_rejected():
    session = FakeEditSession(readback={"vehicle_type_reference": "3"})
    set_active_edit_session(session)

    envelope = _dispatch("set_vehicle_type_reference", vehicle_type_no="200", field="weight_distr")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


# ---------------------------------------------------------------------------
# set_time_distribution_normal(time_distribution_no, mean_s, std_dev_s) --
# QUEUE (Q3, v2a) unit 5
# ---------------------------------------------------------------------------


def test_set_time_distribution_normal_both_fields_happy_path():
    session = FakeEditSession(
        readback={"time_distribution": {"no": "550", "name": "550 s", "type": "NORMAL", "mean_s": 42.0, "std_dev_s": 7.5}}
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_time_distribution_normal", time_distribution_no="550", mean_s=42.0, std_dev_s=7.5
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"mean_s": 42.0, "std_dev_s": 7.5},
        "readback_value": {"mean_s": 42.0, "std_dev_s": 7.5},
    }
    assert session.persist_calls == 1
    assert session.calls == [
        {"op": "set_time_distribution_normal", "time_distribution_no": 550, "mean_s": 42.0, "std_dev_s": 7.5},
        {"op": "get_time_distribution", "time_distribution_no": 550},
    ]
    # time_distribution_no was coerced str -> int at the COM boundary.
    assert isinstance(session.calls[0]["time_distribution_no"], int)
    assert "mean -> 42.0s" in envelope["message"]
    assert "std dev -> 7.5s" in envelope["message"]


def test_set_time_distribution_normal_mean_only_still_reports_the_full_pair():
    session = FakeEditSession(
        readback={"time_distribution": {"no": "550", "name": "550 s", "type": "NORMAL", "mean_s": 42.0, "std_dev_s": 0.0}}
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_time_distribution_normal", time_distribution_no="550", mean_s=42.0)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"mean_s": 42.0, "std_dev_s": 0.0},
        "readback_value": {"mean_s": 42.0, "std_dev_s": 0.0},
    }
    assert session.calls[0] == {
        "op": "set_time_distribution_normal",
        "time_distribution_no": 550,
        "mean_s": 42.0,
        "std_dev_s": None,
    }
    assert "mean -> 42.0s" in envelope["message"]
    assert "std dev ->" not in envelope["message"]


def test_set_time_distribution_normal_neither_field_rejected_at_param_layer():
    session = FakeEditSession(readback={"time_distribution": {}})
    set_active_edit_session(session)

    envelope = _dispatch("set_time_distribution_normal", time_distribution_no="550")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_set_time_distribution_normal_readback_mismatch_still_reports_ok_envelope():
    # The tool's OWN job is only to report both values -- comparing them and
    # refusing on mismatch is guardrails.py's postwrite job (constraint #6).
    # The SET call's own return (used for written_value) is DELIBERATELY a
    # SEPARATE readback key from the plain GET (used for readback_value) --
    # confirms the tool never trusts the set call's own return as "proof".
    session = FakeEditSession(
        readback={
            "time_distribution_normal_set_return": {"no": "550", "mean_s": 42.0, "std_dev_s": 7.5},
            "time_distribution": {"no": "550", "mean_s": 41.9, "std_dev_s": 7.5},
        }
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_time_distribution_normal", time_distribution_no="550", mean_s=42.0)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"mean_s": 42.0, "std_dev_s": 7.5},
        "readback_value": {"mean_s": 41.9, "std_dev_s": 7.5},
    }


def test_set_time_distribution_normal_vissim_refusal_surfaces_as_structured_error():
    # The exact live refusal shape from the probe: an EMPIRICAL target raises.
    session = FakeEditSession(
        raise_on={
            "set_time_distribution_normal": RuntimeError(
                "Could not set time distribution 200 mean_s=77.0 std_dev_s=None: AttValue failed: "
                "Object 200: 2-4 s: Attribute Mean cannot be changed."
            )
        }
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_time_distribution_normal", time_distribution_no="200", mean_s=77.0)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "Attribute Mean cannot be changed" in envelope["message"]
    assert session.persist_calls == 0


def test_set_time_distribution_normal_extra_param_is_rejected_before_the_tool_ever_runs():
    session = FakeEditSession(readback={"time_distribution": {"mean_s": 1.0, "std_dev_s": 1.0}})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_time_distribution_normal", time_distribution_no="550", mean_s=1.0, extra_field="nope"
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_set_time_distribution_normal_no_active_session_is_a_structured_error():
    envelope = _dispatch("set_time_distribution_normal", time_distribution_no="550", mean_s=1.0)

    assert envelope["status"] == "error"
    assert "no_edit_session" in envelope["message"]


# ---------------------------------------------------------------------------
# set_time_distribution_points(time_distribution_no, points) -- QUEUE
# (Q3, v2a) unit 5
# ---------------------------------------------------------------------------


def test_set_time_distribution_points_happy_path():
    session = FakeEditSession(
        readback={"time_distribution_points": [{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 5.0, "cum_fraction": 1.0}]}
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_time_distribution_points",
        time_distribution_no="200",
        points=[{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 5.0, "cum_fraction": 1.0}],
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": [{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 5.0, "cum_fraction": 1.0}],
        "readback_value": [{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 5.0, "cum_fraction": 1.0}],
    }
    assert session.persist_calls == 1
    assert session.calls == [
        {
            "op": "set_time_distribution_points",
            "time_distribution_no": 200,
            "points": [{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 5.0, "cum_fraction": 1.0}],
        },
        {"op": "get_time_distribution_points", "time_distribution_no": 200},
    ]
    assert isinstance(session.calls[0]["time_distribution_no"], int)
    # D3 live-demo fix: bounded message (point count only), same as
    # set_des_speed_distribution_points -- never the full readback list.
    assert "2 point(s)" in envelope["message"]
    assert "duration_s" not in envelope["message"]
    assert "cum_fraction" not in envelope["message"]


def test_set_time_distribution_points_readback_mismatch_still_reports_ok_envelope():
    session = FakeEditSession(
        readback={"time_distribution_points": [{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 4.0, "cum_fraction": 1.0}]}
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_time_distribution_points",
        time_distribution_no="200",
        points=[{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 5.0, "cum_fraction": 1.0}],
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"]["written_value"] != envelope["verification"]["readback_value"]


def test_set_time_distribution_points_vissim_refusal_surfaces_as_structured_error():
    session = FakeEditSession(
        raise_on={
            "set_time_distribution_points": RuntimeError(
                "Could not replace time distribution 200 points: Container "
                "DurationDistributionDataPoints must contain at least 2 item(s)."
            )
        }
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_time_distribution_points",
        time_distribution_no="200",
        points=[{"duration_s": 5.0, "cum_fraction": 1.0}],
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "at least 2 item" in envelope["message"]
    assert session.persist_calls == 0


def test_set_time_distribution_points_empty_list_is_schema_legal_refused_by_constraints_not_pydantic():
    # An empty points list is NOT rejected at the pydantic layer (no
    # min_length on the model) -- per this unit's own design (mirroring
    # set_des_speed_distribution_points exactly), the ">= 2 points"
    # invariant is entirely constraints.py's job. Exercised here only to
    # confirm dispatch-level plumbing accepts the shape; the actual refusal
    # is covered in test_agent_constraints.py.
    session = FakeEditSession(
        raise_on={
            "set_time_distribution_points": RuntimeError(
                "Container DurationDistributionDataPoints must contain at least 2 item(s)."
            )
        }
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_time_distribution_points", time_distribution_no="200", points=[])

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"


def test_set_time_distribution_points_no_active_session_is_a_structured_error():
    envelope = _dispatch(
        "set_time_distribution_points",
        time_distribution_no="200",
        points=[{"duration_s": 0.0, "cum_fraction": 0.0}, {"duration_s": 5.0, "cum_fraction": 1.0}],
    )

    assert envelope["status"] == "error"
    assert "no_edit_session" in envelope["message"]


# ---------------------------------------------------------------------------
# duplicate_driving_behavior / set_driving_behavior_parameter /
# set_link_behavior_type_driving_behavior -- QUEUE 3 unit 6
# ---------------------------------------------------------------------------

_DUPLICATE_READBACK = {
    "no": "7",
    "name": "Ramp copy",
    "car_follow_mod_type": "WIEDEMANN74",
    "car_following_model": 74,
    "ax": 2.0,
    "bx_add": 2.0,
    "bx_mult": 3.0,
    "saf_dist_fact_ln_chg": 0.6,
    "coop_ln_chg_speed_diff": 10.8,
    "min_speed_for_lat": 3.6,
    "lat_dist_driv_def": 1.22,
    "lat_dist_stand_def": 0.46,
}


def test_duplicate_driving_behavior_happy_path():
    session = FakeEditSession(readback={"duplicate_driving_behavior": _DUPLICATE_READBACK})
    set_active_edit_session(session)

    envelope = _dispatch("duplicate_driving_behavior", source_no="1", new_name="Ramp copy")

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": "Ramp copy", "readback_value": "Ramp copy"}
    assert envelope["artifacts"]["new_no"] == "7"
    assert session.calls == [{"op": "duplicate_driving_behavior", "source_no": 1, "new_name": "Ramp copy"}]
    assert session.persist_calls == 1


def test_duplicate_driving_behavior_no_active_session_is_a_structured_error():
    envelope = _dispatch("duplicate_driving_behavior", source_no="1", new_name="Ramp copy")

    assert envelope["status"] == "error"
    assert "no_edit_session" in envelope["message"]


def test_duplicate_driving_behavior_vissim_refusal_surfaces_as_structured_error():
    session = FakeEditSession(raise_on={"duplicate_driving_behavior": RuntimeError("bogus source")})
    set_active_edit_session(session)

    envelope = _dispatch("duplicate_driving_behavior", source_no="999", new_name="x")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert session.persist_calls == 0


def test_duplicate_driving_behavior_rejects_empty_name_at_param_layer():
    session = FakeEditSession(readback={"duplicate_driving_behavior": _DUPLICATE_READBACK})
    set_active_edit_session(session)

    envelope = _dispatch("duplicate_driving_behavior", source_no="1", new_name="")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_set_driving_behavior_parameter_happy_path():
    session = FakeEditSession(readback={"driving_behavior": _DUPLICATE_READBACK})
    set_active_edit_session(session)

    envelope = _dispatch("set_driving_behavior_parameter", behavior_no="7", parameter="ax", value=2.0)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 2.0, "readback_value": 2.0}
    assert session.calls == [
        {"op": "set_driving_behavior_parameter", "behavior_no": 7, "parameter": "ax", "value": 2.0}
    ]
    assert session.persist_calls == 1


def test_set_driving_behavior_parameter_readback_mismatch_still_reports_ok_envelope():
    # Mismatch detection is guardrails.py's job (constraint #6), covered in
    # test_agent_guardrails.py -- the tool layer just reports what it saw.
    mismatched = dict(_DUPLICATE_READBACK, ax=999.0)
    session = FakeEditSession(readback={"driving_behavior": mismatched})
    set_active_edit_session(session)

    envelope = _dispatch("set_driving_behavior_parameter", behavior_no="7", parameter="ax", value=2.0)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 2.0, "readback_value": 999.0}


def test_set_driving_behavior_parameter_vissim_refusal_surfaces_as_structured_error():
    session = FakeEditSession(
        raise_on={
            "set_driving_behavior_parameter": RuntimeError(
                "AttValue failed: Value -1.5 ft is lower than minimum value ... (Min: 0 ft)."
            )
        }
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_driving_behavior_parameter", behavior_no="7", parameter="ax", value=-1.5)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert session.persist_calls == 0


def test_set_driving_behavior_parameter_rejects_unwhitelisted_parameter_at_param_layer():
    # W99 CC0-CC9 (e.g. "cc0") and any other non-whitelisted name are NOT
    # legal Literal values -- rejected before the tool function ever runs,
    # never reaching the agency_restricted_parameter_write constraint (that
    # constraint's own coverage is for defense-in-depth against a future/
    # hypothetical tool, per its own docstring).
    session = FakeEditSession(readback={"driving_behavior": _DUPLICATE_READBACK})
    set_active_edit_session(session)

    envelope = _dispatch("set_driving_behavior_parameter", behavior_no="7", parameter="cc0", value=1.5)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_set_driving_behavior_parameter_no_active_session_is_a_structured_error():
    envelope = _dispatch("set_driving_behavior_parameter", behavior_no="7", parameter="ax", value=2.0)

    assert envelope["status"] == "error"
    assert "no_edit_session" in envelope["message"]


_LBT_DEFAULT_READBACK = {"link_behavior_type_no": "1", "veh_class_no": None, "driving_behavior_no": "7"}
_LBT_OVERRIDE_READBACK = {"link_behavior_type_no": "1", "veh_class_no": "20", "driving_behavior_no": "7"}


def test_set_link_behavior_type_driving_behavior_default_happy_path():
    session = FakeEditSession(readback={"link_behavior_type_assignment": _LBT_DEFAULT_READBACK})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_link_behavior_type_driving_behavior", link_behavior_type_no="1", driving_behavior_no="7"
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": "7", "readback_value": "7"}
    assert session.calls == [
        {
            "op": "set_link_behavior_type_driving_behavior",
            "link_behavior_type_no": 1,
            "driving_behavior_no": 7,
            "veh_class_no": None,
        }
    ]
    assert session.persist_calls == 1


def test_set_link_behavior_type_driving_behavior_veh_class_override_happy_path():
    session = FakeEditSession(readback={"link_behavior_type_assignment": _LBT_OVERRIDE_READBACK})
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_link_behavior_type_driving_behavior",
        link_behavior_type_no="1",
        driving_behavior_no="7",
        veh_class_no="20",
    )

    assert envelope["status"] == "ok"
    assert session.calls == [
        {
            "op": "set_link_behavior_type_driving_behavior",
            "link_behavior_type_no": 1,
            "driving_behavior_no": 7,
            "veh_class_no": 20,
        }
    ]


def test_set_link_behavior_type_driving_behavior_vissim_refusal_surfaces_as_structured_error():
    session = FakeEditSession(
        raise_on={
            "set_link_behavior_type_driving_behavior": RuntimeError(
                "Object 999999 of type Driving Behavior does not exist."
            )
        }
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_link_behavior_type_driving_behavior", link_behavior_type_no="1", driving_behavior_no="999999"
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert session.persist_calls == 0


def test_set_link_behavior_type_driving_behavior_no_active_session_is_a_structured_error():
    envelope = _dispatch(
        "set_link_behavior_type_driving_behavior", link_behavior_type_no="1", driving_behavior_no="7"
    )

    assert envelope["status"] == "error"
    assert "no_edit_session" in envelope["message"]


# ---------------------------------------------------------------------------
# Param validation (strict, via toolkit.dispatch's pydantic layer)
# ---------------------------------------------------------------------------


def test_extra_param_is_rejected_before_the_tool_ever_runs():
    session = FakeEditSession(readback={"vehicle_input_volume": 600.0})
    set_active_edit_session(session)

    envelope = _dispatch("set_vehicle_input_volume", input_no="1", volume=600.0, extra_field="nope")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []  # never reached the tool function


def test_missing_required_param_is_rejected():
    session = FakeEditSession(readback={"signal_cycle": 90})
    set_active_edit_session(session)

    envelope = _dispatch("set_signal_cycle", sc_no="5")  # cycle missing

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_non_numeric_id_string_is_rejected_with_a_clear_error_not_a_crash():
    session = FakeEditSession(readback={"vehicle_input_volume": 600.0})
    set_active_edit_session(session)

    envelope = _dispatch("set_vehicle_input_volume", input_no="not-a-number", volume=600.0)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert session.persist_calls == 0


# ---------------------------------------------------------------------------
# set_reduced_speed_area — the sixth (XML-injection, non-COM) write tool
# ---------------------------------------------------------------------------

_SYNTH_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network version="2400">
  <links>
    <link no="101" name="Main St EB approach">
      <lanes><lane width="3.5"/><lane width="3.5"/></lanes>
    </link>
  </links>
  <speedDistributions></speedDistributions>
  <reducedSpeedAreas></reducedSpeedAreas>
</network>
"""


class _FakeWorkingInpxSession:
    """The minimal shape ``set_reduced_speed_area`` actually needs from an
    edit session: a ``working_inpx`` PATH attribute, nothing else. Unlike
    ``FakeEditSession`` above, this class exposes NO COM-shaped methods at
    all -- if the tool ever tried to call one (a regression that would mean
    it started touching COM), this would raise ``AttributeError``, not
    silently succeed.
    """

    def __init__(self, working_inpx: Path) -> None:
        self.working_inpx = working_inpx


@pytest.fixture()
def synthetic_working_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "working.inpx"
    path.write_text(_SYNTH_INPX, encoding="utf-8")
    return path


def test_set_reduced_speed_area_happy_path(synthetic_working_inpx: Path):
    set_active_edit_session(_FakeWorkingInpxSession(synthetic_working_inpx))

    envelope = _dispatch(
        "set_reduced_speed_area",
        link_id="101",
        speed_kmh=40.0,
        from_m=5.0,
        length_m=20.0,
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"]["written_value"] == {
        "link_id": "101",
        "speed_kmh": 40.0,
        "from_m": 5.0,
        "length_m": 20.0,
    }
    assert envelope["verification"]["readback_value"] == envelope["verification"]["written_value"]
    assert envelope["artifacts"]["lane_count"] == 2
    assert len(envelope["artifacts"]["area_nos"]) == 2

    # the write actually landed on the working copy on disk (no separate
    # persist() step exists for this tool -- inject_reduced_speed_area IS
    # the persistence step).
    root = ET.parse(synthetic_working_inpx).getroot()
    assert len(root.findall(".//reducedSpeedAreas/reducedSpeedArea")) == 2


def test_set_reduced_speed_area_never_touches_a_com_shaped_method(
    synthetic_working_inpx: Path,
):
    # _FakeWorkingInpxSession exposes no set_/get_/persist methods -- a
    # happy-path dispatch succeeding at all (previous test) plus this
    # session object never raising AttributeError already proves the tool
    # stayed off COM; this test just makes the intent explicit and pins it
    # against a future regression that adds a stray session.persist() call.
    session = _FakeWorkingInpxSession(synthetic_working_inpx)
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_reduced_speed_area",
        link_id="101",
        speed_kmh=30.0,
        from_m=0.0,
        length_m=10.0,
    )

    assert envelope["status"] == "ok"
    assert not hasattr(session, "persist_calls")  # never gained a persist-tracking attribute


def test_set_reduced_speed_area_link_not_found_surfaces_as_tool_execution_error(
    synthetic_working_inpx: Path,
):
    # At the dispatch level (bypassing constraints.py's preflight, which is
    # what actually catches this in production -- see
    # test_agent_constraints.py's object_not_found coverage for
    # set_reduced_speed_area), inject_reduced_speed_area's own ValueError
    # must still surface as a structured error, never a crash.
    set_active_edit_session(_FakeWorkingInpxSession(synthetic_working_inpx))

    envelope = _dispatch(
        "set_reduced_speed_area",
        link_id="999-does-not-exist",
        speed_kmh=40.0,
        from_m=0.0,
        length_m=10.0,
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "999-does-not-exist" in envelope["message"]
    # nothing was written for a refused injection
    root = ET.parse(synthetic_working_inpx).getroot()
    assert root.findall(".//reducedSpeedAreas/reducedSpeedArea") == []


@pytest.mark.parametrize(
    "params,expected_error_type",
    [
        (
            {"link_id": "101", "speed_kmh": 40.0, "from_m": 0.0, "length_m": 20.0, "extra": "nope"},
            "invalid_params",
        ),
        ({"link_id": "101", "speed_kmh": 40.0, "from_m": 0.0}, "invalid_params"),  # length_m missing
        (
            {"link_id": "101", "speed_kmh": "fast", "from_m": 0.0, "length_m": 20.0},
            "invalid_params",
        ),  # wrong type
    ],
)
def test_set_reduced_speed_area_malformed_params_rejected(
    synthetic_working_inpx: Path, params, expected_error_type
):
    set_active_edit_session(_FakeWorkingInpxSession(synthetic_working_inpx))

    envelope = _dispatch("set_reduced_speed_area", **params)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == expected_error_type
    # never reached the tool function -- nothing written
    root = ET.parse(synthetic_working_inpx).getroot()
    assert root.findall(".//reducedSpeedAreas/reducedSpeedArea") == []


# ---------------------------------------------------------------------------
# Defect 1 live-demo fix (docs/phase2/DECISIONS.md, 2026-07-31): the message-
# bounding helpers rollback_last_edit and apply_edit_plan now use to keep
# `message` -- the one field planner.py's history-rendering truncation
# guarantees survives -- genuinely bounded regardless of how much a single
# undone edit (or an applied batch) actually touched.
# ---------------------------------------------------------------------------


def test_compact_diff_value_never_inlines_a_full_list_or_dict():
    big_points = [{"speed_kmh": float(i), "cum_fraction": i / 100} for i in range(50)]
    rendered = tools_edit._compact_diff_value(big_points)
    assert rendered == "[50 item(s)]"
    assert "speed_kmh" not in rendered  # never the raw repr of the list's own contents

    big_dict = {f"field_{i}": i for i in range(20)}
    rendered_dict = tools_edit._compact_diff_value(big_dict)
    assert rendered_dict == "{20 field(s)}"

    # A long scalar (e.g. a pathological name) is still capped, defensively.
    long_text = "x" * 500
    rendered_scalar = tools_edit._compact_diff_value(long_text)
    assert len(rendered_scalar) <= tools_edit._DIFF_VALUE_REPR_MAX_CHARS
    assert rendered_scalar.endswith("…")

    # An ordinary scalar renders exactly as repr() would, unbounded reason to
    # differ from the old behavior for the common case.
    assert tools_edit._compact_diff_value(500.0) == "500.0"


def test_describe_diff_entry_field_changed_summarizes_list_valued_fields():
    entry = {
        "object_class": "des_speed_distributions",
        "object_id": "5",
        "change": "field_changed",
        "field": "points",
        "before": [{"speed_kmh": 0.0, "cum_fraction": 0.0}] * 30,
        "after": [{"speed_kmh": 10.0, "cum_fraction": 0.0}] * 10,
    }
    described = tools_edit._describe_diff_entry(entry)
    assert described == "des_speed_distributions#5.points: [30 item(s)] → [10 item(s)]"
    assert len(described) < 100  # bounded regardless of how many points either side had


def test_describe_diff_entry_removed_and_added_are_unchanged():
    removed = {"object_class": "des_speed_distributions", "object_id": "9", "change": "removed"}
    added = {"object_class": "des_speed_distributions", "object_id": "9", "change": "added"}
    assert "removed" in tools_edit._describe_diff_entry(removed)
    assert "re-added" in tools_edit._describe_diff_entry(added)


def test_cap_message_leaves_short_text_untouched_and_truncates_long_text():
    short = "short and sweet"
    assert tools_edit._cap_message(short) == short

    long_text = "y" * 1000
    capped = tools_edit._cap_message(long_text)
    assert len(capped) == tools_edit._MESSAGE_MAX_CHARS
    assert capped.endswith("…")


def test_build_apply_edit_plan_message_bounds_step_lines_and_names_a_saved_version():
    applied = [
        {"index": i, "tool": f"set_signal_offset_{i}" if i > 1 else "set_signal_offset"}
        for i in range(1, 11)
    ]  # a full 10-step batch (batch.MAX_STEPS)
    message = tools_edit._build_apply_edit_plan_message(applied, total_steps=10)

    assert len(message) <= tools_edit._MESSAGE_MAX_CHARS
    assert "readback ok" in message
    assert "+7 more" in message  # 10 steps, only the first 3 inlined
    assert "version" in message.lower()


def test_build_apply_edit_plan_message_small_plan_names_every_step():
    applied = [
        {"index": 1, "tool": "set_signal_cycle"},
        {"index": 2, "tool": "set_signal_offset"},
    ]
    message = tools_edit._build_apply_edit_plan_message(applied, total_steps=2)

    assert "1. set_signal_cycle: applied, readback ok" in message
    assert "2. set_signal_offset: applied, readback ok" in message
    assert "more" not in message  # both steps fit -- no truncation needed

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
        if op in self._raise_on:
            raise self._raise_on[op]

    def set_vehicle_input_volume(self, input_no: int, volume: float) -> None:
        self._record("set_vehicle_input_volume", input_no=input_no, volume=volume)

    def get_vehicle_input_volume(self, input_no: int) -> float:
        self._record("get_vehicle_input_volume", input_no=input_no)
        return self._readback["vehicle_input_volume"]

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

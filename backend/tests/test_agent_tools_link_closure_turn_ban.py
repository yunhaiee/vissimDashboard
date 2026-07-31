"""Tests for Q4 unit (3)'s tools_edit.py verbs: set_link_closure/
set_turn_ban (composition over unit (1)'s set_lane_closure across EVERY
lane of a link/connector) and remove_connector (structural XML removal via
services.inpx_structure.remove_connector) -- dispatched through the REAL
toolkit.dispatch (so pydantic validation + the envelope machinery are
exercised exactly like production), against hand-rolled fakes. No real
Vissim COM anywhere in this file.

``FakeLinkClosureSession`` simulates a genuine per-lane BlockedVehClasses
store (a dict keyed by ``(link_no, lane_index)``), unlike
test_agent_tools_edit.py's single-lane ``FakeEditSession`` (one static
rigged value for every call) -- a multi-lane composition needs each lane's
own readback to be independently controllable, including rigging a
PARTIAL-application mismatch (one lane's readback deliberately diverges) the
same way a real COM refusal on a middle lane would.

``remove_connector`` needs no COM methods on its fake at all -- it calls
``services.inpx_structure.remove_connector`` directly against the fake's own
real on-disk ``working_inpx``, exactly like ``set_reduced_speed_area``'s
``_FakeWorkingInpxSession`` precedent (test_agent_tools_edit.py). The load
oracle is stubbed via monkeypatching ``services.inpx_structure.
run_load_oracle`` -- the SAME technique test_inpx_structure.py's own
``apply_structural_edit`` tests use.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

import services.agent.tools_edit as tools_edit  # noqa: F401 - import registers tools
import services.inpx_structure as inpx_structure
from services.agent.edit_session import clear_active_edit_session, set_active_edit_session
from services.agent.toolkit import default_registry, dispatch

_NETWORK_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<network>
<links>{links}</links>
{extra}
</network>
"""


def _write_working_inpx(tmp_path: Path, links: str, extra: str = "") -> Path:
    path = tmp_path / "working.inpx"
    path.write_text(_NETWORK_TEMPLATE.format(links=links, extra=extra), encoding="utf-8")
    return path


_ONE_LANE_LINK = '<link no="101" name="A"><lanes><lane width="3.5"/></lanes></link>'
_TWO_LANE_LINK = (
    '<link no="101" name="A"><lanes><lane width="3.5"/><lane width="3.5"/></lanes></link>'
)
_ONE_LANE_CONNECTOR = (
    '<link no="101" name="A"><lanes><lane width="3.5"/></lanes></link>'
    '<link no="9001" name="turn"><fromLinkEndPt link="101" pos="10"/>'
    '<toLinkEndPt link="102" pos="0"/><lanes><lane width="3.5"/></lanes></link>'
    '<link no="102" name="B"><lanes><lane width="3.5"/></lanes></link>'
)


@pytest.fixture(autouse=True)
def _reset_active_edit_session():
    clear_active_edit_session()
    yield
    clear_active_edit_session()


@pytest.fixture(autouse=True)
def _stub_load_oracle(monkeypatch):
    """No real COM anywhere in this file -- remove_connector's own dispatch
    below stubs the spine's load oracle to a canned PASS (inert for
    set_link_closure/set_turn_ban, which never touch inpx_structure)."""
    monkeypatch.setattr(
        inpx_structure,
        "run_load_oracle",
        lambda p: {
            "load_ok": True, "load_error": None, "init_ok": True, "init_error": None, "err_content": None
        },
    )


def _dispatch(tool_name: str, **params: Any) -> dict[str, Any]:
    return dispatch(tool_name, params, registry=default_registry)


class FakeLinkClosureSession:
    """Duck-types VissimEditSession's set_lane_closure/get_lane_closure
    surface with a genuine per-lane store."""

    def __init__(self, working_inpx: Path, mismatch_lanes: set[int] | None = None) -> None:
        self.working_inpx = working_inpx
        self.calls: list[dict[str, Any]] = []
        self._lane_state: dict[tuple[int, int], list[int]] = {}
        # Lane indices whose readback is deliberately WRONG (simulates a
        # live Vissim partial-application/refusal on one lane of a
        # multi-lane link).
        self._mismatch_lanes = mismatch_lanes or set()
        self.persist_calls = 0
        self.closed = False

    def set_lane_closure(self, link_no: int, lane_index: int, vehicle_classes: list[int]) -> None:
        self.calls.append(
            {
                "op": "set_lane_closure",
                "link_no": link_no,
                "lane_index": lane_index,
                "vehicle_classes": vehicle_classes,
            }
        )
        self._lane_state[(link_no, lane_index)] = list(vehicle_classes)

    def get_lane_closure(self, link_no: int, lane_index: int) -> list[int]:
        self.calls.append({"op": "get_lane_closure", "link_no": link_no, "lane_index": lane_index})
        if lane_index in self._mismatch_lanes:
            return [999]  # deliberately wrong readback
        return self._lane_state.get((link_no, lane_index), [])

    def persist(self) -> None:
        self.persist_calls += 1

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# set_link_closure
# ---------------------------------------------------------------------------


def test_set_link_closure_happy_path_single_lane(tmp_path: Path):
    working = _write_working_inpx(tmp_path, _ONE_LANE_LINK)
    session = FakeLinkClosureSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_link_closure", link_id="101", vehicle_classes=["10", "20"], closed=True)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"vehicle_classes": ["10", "20"], "lanes_matched": 1},
        "readback_value": {"vehicle_classes": ["10", "20"], "lanes_matched": 1},
    }
    assert "closed to vehicle class(es) 10, 20" in envelope["message"]
    assert session.persist_calls == 1
    set_calls = [c for c in session.calls if c["op"] == "set_lane_closure"]
    assert set_calls == [
        {"op": "set_lane_closure", "link_no": 101, "lane_index": 1, "vehicle_classes": [10, 20]}
    ]
    assert isinstance(set_calls[0]["link_no"], int)


def test_set_link_closure_composes_across_every_lane(tmp_path: Path):
    working = _write_working_inpx(tmp_path, _TWO_LANE_LINK)
    session = FakeLinkClosureSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_link_closure", link_id="101", vehicle_classes=["30"], closed=True)

    assert envelope["status"] == "ok"
    assert envelope["verification"]["readback_value"] == {"vehicle_classes": ["30"], "lanes_matched": 2}
    set_calls = [c for c in session.calls if c["op"] == "set_lane_closure"]
    assert {c["lane_index"] for c in set_calls} == {1, 2}
    assert envelope["artifacts"]["per_lane"] == {"1": ["30"], "2": ["30"]}


def test_set_link_closure_reopen_clears_every_lane(tmp_path: Path):
    working = _write_working_inpx(tmp_path, _TWO_LANE_LINK)
    session = FakeLinkClosureSession(working)
    # Pre-seed both lanes as closed, as if a prior closure already landed.
    session._lane_state[(101, 1)] = [30]
    session._lane_state[(101, 2)] = [30]
    set_active_edit_session(session)

    envelope = _dispatch("set_link_closure", link_id="101", vehicle_classes=[], closed=False)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"vehicle_classes": [], "lanes_matched": 2},
        "readback_value": {"vehicle_classes": [], "lanes_matched": 2},
    }
    assert "reopened to all vehicle classes" in envelope["message"]


def test_set_link_closure_partial_mismatch_readback_reported(tmp_path: Path):
    working = _write_working_inpx(tmp_path, _TWO_LANE_LINK)
    session = FakeLinkClosureSession(working, mismatch_lanes={2})
    set_active_edit_session(session)

    envelope = _dispatch("set_link_closure", link_id="101", vehicle_classes=["30"], closed=True)

    # The tool itself always reports ok when nothing raised -- postwrite's
    # write_verification_failed (#6) is what catches this mismatch, not the
    # tool. But the verification payload itself must show the disagreement.
    assert envelope["status"] == "ok"
    assert envelope["verification"]["readback_value"]["vehicle_classes"] is None
    assert envelope["verification"]["readback_value"]["lanes_matched"] == 1


def test_set_link_closure_rejects_closed_with_no_classes():
    envelope = _dispatch("set_link_closure", link_id="101", closed=True)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert "at least one vehicle_classes" in envelope["message"]


def test_set_link_closure_rejects_reopen_with_classes():
    envelope = _dispatch("set_link_closure", link_id="101", vehicle_classes=["10"], closed=False)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert "does not take vehicle_classes" in envelope["message"]


def test_set_link_closure_no_active_session_yields_structured_error():
    envelope = _dispatch("set_link_closure", link_id="101", vehicle_classes=["10"])
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]


def test_set_link_closure_unknown_link_surfaces_as_tool_execution_error(tmp_path: Path):
    working = _write_working_inpx(tmp_path, _ONE_LANE_LINK)
    session = FakeLinkClosureSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_link_closure", link_id="999", vehicle_classes=["10"])

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "not found" in envelope["message"]


# ---------------------------------------------------------------------------
# set_turn_ban
# ---------------------------------------------------------------------------


def test_set_turn_ban_happy_path(tmp_path: Path):
    working = _write_working_inpx(tmp_path, _ONE_LANE_CONNECTOR)
    session = FakeLinkClosureSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_turn_ban", connector_no="9001", vehicle_classes=["10"], banned=True)

    assert envelope["status"] == "ok"
    assert "Turn (connector 9001) banned for vehicle class(es) 10" in envelope["message"]
    assert envelope["verification"]["readback_value"] == {"vehicle_classes": ["10"], "lanes_matched": 1}
    set_calls = [c for c in session.calls if c["op"] == "set_lane_closure"]
    assert set_calls == [
        {"op": "set_lane_closure", "link_no": 9001, "lane_index": 1, "vehicle_classes": [10]}
    ]


def test_set_turn_ban_unban(tmp_path: Path):
    working = _write_working_inpx(tmp_path, _ONE_LANE_CONNECTOR)
    session = FakeLinkClosureSession(working)
    session._lane_state[(9001, 1)] = [10]
    set_active_edit_session(session)

    envelope = _dispatch("set_turn_ban", connector_no="9001", vehicle_classes=[], banned=False)

    assert envelope["status"] == "ok"
    assert "un-banned" in envelope["message"]


def test_set_turn_ban_refuses_a_plain_link(tmp_path: Path):
    working = _write_working_inpx(tmp_path, _ONE_LANE_CONNECTOR)
    session = FakeLinkClosureSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_turn_ban", connector_no="101", vehicle_classes=["10"])

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "not a connector" in envelope["message"]


def test_set_turn_ban_rejects_banned_with_no_classes():
    envelope = _dispatch("set_turn_ban", connector_no="9001", banned=True)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_set_turn_ban_no_active_session_yields_structured_error():
    envelope = _dispatch("set_turn_ban", connector_no="9001", vehicle_classes=["10"])
    assert envelope["status"] == "error"
    assert "no_edit_session" in envelope["message"]


# ---------------------------------------------------------------------------
# remove_connector
# ---------------------------------------------------------------------------


class FakeStructuralSession:
    """remove_connector needs only working_inpx -- it calls
    services.inpx_structure.remove_connector directly against the real file,
    like set_reduced_speed_area's own _FakeWorkingInpxSession precedent
    (test_agent_tools_edit.py) -- no COM method surface at all."""

    def __init__(self, working_inpx: Path) -> None:
        self.working_inpx = working_inpx
        self.persist_calls = 0
        self.closed = False

    def persist(self) -> None:
        self.persist_calls += 1

    def close(self) -> None:
        self.closed = True


def test_remove_connector_happy_path(tmp_path: Path):
    working = _write_working_inpx(tmp_path, _ONE_LANE_CONNECTOR)
    session = FakeStructuralSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_connector", connector_no="9001")

    assert envelope["status"] == "ok"
    # written/readback both describe the SAME claim ("9001 should no longer
    # be present") so they AGREE on a genuinely successful removal -- see
    # tools_edit._remove_verification's own comment for why this shape
    # matters (a naive "readback goes to None on success" shape makes
    # constraints.py's #6 write_verification_failed refuse every successful
    # remove).
    assert envelope["verification"] == {
        "written_value": {"removed_connector_no": "9001", "still_present": False},
        "readback_value": {"removed_connector_no": "9001", "still_present": False},
    }
    assert "structurally removed" in envelope["message"]
    assert "No un-remove exists" in envelope["message"]
    # session.persist() is never called -- remove_connector writes directly
    # to working_inpx via the spine, exactly like set_reduced_speed_area.
    assert session.persist_calls == 0
    root = ET.parse(working).getroot()
    assert root.findall('.//links/link[@no="9001"]') == []


def test_remove_connector_refuses_when_referenced(tmp_path: Path):
    working = _write_working_inpx(
        tmp_path,
        _ONE_LANE_CONNECTOR,
        extra='<conflictAreas><conflictArea no="1" linkA="101" linkB="9001" status="PASSIVE"/></conflictAreas>',
    )
    original_bytes = working.read_bytes()
    session = FakeStructuralSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_connector", connector_no="9001")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "conflict area link B" in envelope["message"]
    assert working.read_bytes() == original_bytes


def test_remove_connector_no_active_session_yields_structured_error():
    envelope = _dispatch("remove_connector", connector_no="9001")
    assert envelope["status"] == "error"
    assert "no_edit_session" in envelope["message"]

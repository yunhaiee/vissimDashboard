"""Tests for Q4 unit (4)'s tools_edit.py signal-head + detector placement
verbs: add_signal_head/remove_signal_head, add_detector/remove_detector --
dispatched through the REAL toolkit.dispatch (so pydantic validation + the
envelope machinery are exercised exactly like production), against a
hand-rolled ``FakeSignalHeadDetectorSession`` -- no real Vissim COM anywhere
in this file.

House shape = test_agent_tools_measurement_infra.py (the newest Q4 unit's
own test file): the fake owns a real on-disk ``working_inpx`` since
add_detector's own stop-line-proxy default (no COM at all -- it reads
``services.agent.tools_network.build_shadow_model`` straight off the working
copy) and every remove tool's post-persist "is it really gone" readback
(``tools_edit._still_present_after_remove``) both need genuinely-changed
file content, not a rigged in-memory value.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

import services.agent.tools_edit as tools_edit  # noqa: F401 - import registers tools
from services.agent.edit_session import clear_active_edit_session, set_active_edit_session
from services.agent.toolkit import default_registry, dispatch

_NETWORK_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<network>
<links>
<link no="101" name="A"><lanes><lane width="3.5"/><lane width="3.5"/></lanes></link>
<link no="102" name="B"><lanes><lane width="3.5"/></lanes></link>
</links>
{extra}
</network>
"""


def _write_working_inpx(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "working.inpx"
    path.write_text(_NETWORK_TEMPLATE.format(extra=extra), encoding="utf-8")
    return path


def _remove_by_no(path: Path, container_tag: str, child_tag: str, no: Any) -> None:
    """Simulate a real COM Remove* + persist(): mutate the on-disk working
    copy so a subsequent reparse (this unit's own post-remove readback) sees
    the genuinely-removed state, not a stale fixture."""
    tree = ET.parse(path)
    root = tree.getroot()
    container = root.find(container_tag)
    if container is None:
        return
    for el in list(container.findall(child_tag)):
        if el.get("no") == str(no):
            container.remove(el)
    tree.write(path, encoding="UTF-8", xml_declaration=True)


class FakeSignalHeadDetectorSession:
    """Duck-types VissimEditSession's signal-head/detector surface: records
    every call, returns rigged readback values for ADD calls, and mutates
    the real on-disk ``working_inpx`` for REMOVE calls (see module
    docstring). Optionally raises on a named op to simulate a live Vissim
    refusal."""

    def __init__(
        self,
        working_inpx: Path,
        readback: dict[str, Any] | None = None,
        raise_on: dict[str, Exception] | None = None,
    ) -> None:
        self.working_inpx = working_inpx
        self.calls: list[dict[str, Any]] = []
        self._readback = readback or {}
        self._raise_on = raise_on or {}
        self.persist_calls = 0
        self.closed = False

    def _record(self, op: str, **kwargs: Any) -> None:
        self.calls.append({"op": op, **kwargs})
        if op in self._raise_on:
            raise self._raise_on[op]

    def add_signal_head(
        self, link_no: int, lane_index: int, pos_m: float, sc_no: int, sg_no: int, name: str = ""
    ) -> dict[str, Any]:
        self._record(
            "add_signal_head",
            link_no=link_no, lane_index=lane_index, pos_m=pos_m, sc_no=sc_no, sg_no=sg_no, name=name,
        )
        return self._readback["signal_head"]

    def remove_signal_head(self, no: int) -> None:
        self._record("remove_signal_head", no=no)
        _remove_by_no(self.working_inpx, "signalHeads", "signalHead", no)

    def add_detector(
        self,
        link_no: int,
        lane_index: int,
        pos_m: float,
        length_m: float = 4.5,
        sc_no: int | None = None,
        port_no: int | None = None,
        name: str = "",
    ) -> dict[str, Any]:
        self._record(
            "add_detector",
            link_no=link_no, lane_index=lane_index, pos_m=pos_m, length_m=length_m,
            sc_no=sc_no, port_no=port_no, name=name,
        )
        return self._readback["detector"]

    def remove_detector(self, no: int) -> None:
        self._record("remove_detector", no=no)
        _remove_by_no(self.working_inpx, "detectors", "detector", no)

    def persist(self) -> None:
        self.persist_calls += 1

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_active_edit_session():
    clear_active_edit_session()
    yield
    clear_active_edit_session()


def _dispatch(tool_name: str, **params: Any) -> dict[str, Any]:
    return dispatch(tool_name, params, registry=default_registry)


# ---------------------------------------------------------------------------
# add_signal_head / remove_signal_head
# ---------------------------------------------------------------------------


def test_add_signal_head_happy_path(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeSignalHeadDetectorSession(
        working,
        readback={
            "signal_head": {
                "no": 61, "name": "", "link": "101", "lane_index": 1, "pos": 15.0, "sc": "1", "sg": "22",
            }
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_signal_head", link_no="101", lane_index=1, pos_m=15.0, sc_no="1", sg_no="22"
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"link": "101", "lane_index": 1, "pos": 15.0, "sc": "1", "sg": "22"},
        "readback_value": {"link": "101", "lane_index": 1, "pos": 15.0, "sc": "1", "sg": "22"},
    }
    assert envelope["artifacts"] == {"no": 61, "name": ""}
    assert "Signal head 61 added on link 101" in envelope["message"]
    assert "SC 1 SG 22" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [
        {
            "op": "add_signal_head", "link_no": 101, "lane_index": 1, "pos_m": 15.0,
            "sc_no": 1, "sg_no": 22, "name": "",
        }
    ]
    assert isinstance(session.calls[0]["sc_no"], int)
    assert isinstance(session.calls[0]["sg_no"], int)


def test_add_signal_head_no_active_session_yields_structured_error():
    envelope = _dispatch(
        "add_signal_head", link_no="101", lane_index=1, pos_m=15.0, sc_no="1", sg_no="22"
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]


def test_add_signal_head_bogus_sg_no_surfaces_as_tool_execution_error(tmp_path: Path):
    """sc_no/sg_no are NOT covered by the object_not_found preflight (this
    unit's own documented scope cut, tools_edit.py's module comment) -- a
    bogus signal group fails closed as a tool_execution_error instead."""
    working = _write_working_inpx(tmp_path)
    session = FakeSignalHeadDetectorSession(
        working,
        raise_on={
            "add_signal_head": RuntimeError(
                "Signal controller 1 signal group 999 does not exist. Available signal group "
                "numbers: 1, 2, 3."
            )
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_signal_head", link_no="101", lane_index=1, pos_m=15.0, sc_no="1", sg_no="999"
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "999" in envelope["message"]


def test_remove_signal_head_happy_path(tmp_path: Path):
    working = _write_working_inpx(
        tmp_path,
        extra=(
            '<signalHeads><signalHead no="61" name="" lane="101 1" pos="15" sg="1 22"/>'
            "</signalHeads>"
        ),
    )
    session = FakeSignalHeadDetectorSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_signal_head", no="61")

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"removed_no": "61", "still_present": False},
        "readback_value": {"removed_no": "61", "still_present": False},
    }
    assert "Signal head 61 removed" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [{"op": "remove_signal_head", "no": 61}]
    root = ET.parse(working).getroot()
    assert root.findall(".//signalHeads/signalHead") == []


def test_remove_signal_head_vissim_refusal_surfaces_as_tool_execution_error(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeSignalHeadDetectorSession(
        working, raise_on={"remove_signal_head": RuntimeError("Signal head 99 does not exist")}
    )
    set_active_edit_session(session)

    envelope = _dispatch("remove_signal_head", no="99")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "does not exist" in envelope["message"]
    assert session.persist_calls == 0


# ---------------------------------------------------------------------------
# add_detector / remove_detector
# ---------------------------------------------------------------------------


def test_add_detector_happy_path_explicit_pos(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeSignalHeadDetectorSession(
        working,
        readback={
            "detector": {
                "no": 1, "name": "", "link": "101", "lane_index": 1, "pos": 20.0, "length": 4.5,
                "sc": "1", "port_no": 501,
            }
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_detector", link_no="101", lane_index=1, pos_m=20.0, sc_no="1", port_no=501
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"link": "101", "lane_index": 1, "pos": 20.0, "length": 4.5, "sc": "1", "port_no": 501},
        "readback_value": {"link": "101", "lane_index": 1, "pos": 20.0, "length": 4.5, "sc": "1", "port_no": 501},
    }
    assert envelope["artifacts"] == {"no": 1, "name": ""}
    assert "Detector 1 added on link 101" in envelope["message"]
    assert "length 4.5 m" in envelope["message"]
    assert session.calls == [
        {
            "op": "add_detector", "link_no": 101, "lane_index": 1, "pos_m": 20.0, "length_m": 4.5,
            "sc_no": 1, "port_no": 501, "name": "",
        }
    ]


def test_add_detector_default_length_is_mrwa_4_5m(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeSignalHeadDetectorSession(
        working,
        readback={
            "detector": {
                "no": 1, "name": "", "link": "101", "lane_index": 1, "pos": 20.0, "length": 4.5,
                "sc": "", "port_no": None,
            }
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch("add_detector", link_no="101", lane_index=1, pos_m=20.0)

    assert envelope["status"] == "ok"
    assert session.calls[0]["length_m"] == 4.5
    assert session.calls[0]["sc_no"] is None
    assert session.calls[0]["port_no"] is None


def test_add_detector_omitted_sc_and_port_never_become_a_verification_claim(tmp_path: Path):
    """Live-caught regression (backend/tools/signal_heads_detectors_live_check.py,
    Phase A): when sc_no/port_no are omitted, Vissim's own AddDetector
    silently assigns its OWN default (observed live: SC=1/PortNo=1) rather
    than leaving them blank. Comparing the caller's own ""/None against that
    surprise readback would make constraint #6 (write_verification_failed)
    refuse+rollback a genuinely correct add -- so sc/port_no must be ABSENT
    from both written_value/readback_value entirely when the caller never
    asked to wire them, even though the fake session's own readback (mirroring
    the real live finding) reports a non-empty sc/port_no."""
    working = _write_working_inpx(tmp_path)
    session = FakeSignalHeadDetectorSession(
        working,
        readback={
            # Mirrors the live finding EXACTLY: Vissim assigned SC=1/PortNo=1
            # even though the caller never asked for controller wiring.
            "detector": {
                "no": 1, "name": "", "link": "101", "lane_index": 1, "pos": 20.0, "length": 4.5,
                "sc": "1", "port_no": 1,
            }
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch("add_detector", link_no="101", lane_index=1, pos_m=20.0)

    assert envelope["status"] == "ok"
    assert "sc" not in envelope["verification"]["written_value"]
    assert "sc" not in envelope["verification"]["readback_value"]
    assert "port_no" not in envelope["verification"]["written_value"]
    assert "port_no" not in envelope["verification"]["readback_value"]


def test_add_detector_sc_no_without_port_no_rejected_at_param_layer(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeSignalHeadDetectorSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("add_detector", link_no="101", lane_index=1, pos_m=20.0, sc_no="1")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []  # never reached the tool body


def test_add_detector_port_no_without_sc_no_rejected_at_param_layer(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeSignalHeadDetectorSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("add_detector", link_no="101", lane_index=1, pos_m=20.0, port_no=501)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_add_detector_rejects_lane_index_below_one_at_param_layer(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeSignalHeadDetectorSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("add_detector", link_no="101", lane_index=0, pos_m=20.0)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


# --- the "quality target": default pos from an existing signal head -------


def test_add_detector_omitted_pos_defaults_to_signal_head_minus_mrwa_setback(tmp_path: Path):
    """THE quality-target test the task brief asks for: pos_m omitted, a
    signal head exists on the SAME (link, lane) at pos=15.0 -- the detector
    defaults to 15.0 - 2.0 = 13.0 m, and the result message says so."""
    working = _write_working_inpx(
        tmp_path,
        extra='<signalHeads><signalHead no="1" name="" lane="101 1" pos="15.0" sg="1 1"/></signalHeads>',
    )
    session = FakeSignalHeadDetectorSession(
        working,
        readback={
            "detector": {
                "no": 1, "name": "", "link": "101", "lane_index": 1, "pos": 13.0, "length": 4.5,
                "sc": "", "port_no": None,
            }
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch("add_detector", link_no="101", lane_index=1)

    assert envelope["status"] == "ok"
    assert session.calls[0]["pos_m"] == pytest.approx(13.0)
    assert "pos defaulted to 13.000 m" in envelope["message"]
    assert "signal head 1" in envelope["message"]
    assert "13.0 m" in envelope["message"] or "13.000 m" in envelope["message"]


def test_add_detector_omitted_pos_picks_the_furthest_head_when_several_share_the_lane(tmp_path: Path):
    working = _write_working_inpx(
        tmp_path,
        extra=(
            '<signalHeads>'
            '<signalHead no="1" name="" lane="101 1" pos="10.0" sg="1 1"/>'
            '<signalHead no="2" name="" lane="101 1" pos="25.0" sg="1 1"/>'
            "</signalHeads>"
        ),
    )
    session = FakeSignalHeadDetectorSession(
        working,
        readback={
            "detector": {
                "no": 1, "name": "", "link": "101", "lane_index": 1, "pos": 23.0, "length": 4.5,
                "sc": "", "port_no": None,
            }
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch("add_detector", link_no="101", lane_index=1)

    assert envelope["status"] == "ok"
    # 25.0 (the furthest-along head) - 2.0 MRWA setback = 23.0
    assert session.calls[0]["pos_m"] == pytest.approx(23.0)
    assert "signal head 2" in envelope["message"]


def test_add_detector_omitted_pos_with_no_signal_head_on_that_lane_refuses(tmp_path: Path):
    """Never guess silently -- no head on this (link, lane) means pos_m must
    be given explicitly."""
    working = _write_working_inpx(tmp_path)
    session = FakeSignalHeadDetectorSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("add_detector", link_no="101", lane_index=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "pos_m was not given" in envelope["message"]
    assert session.calls == []
    assert session.persist_calls == 0


def test_add_detector_omitted_pos_ignores_a_head_on_a_different_lane(tmp_path: Path):
    working = _write_working_inpx(
        tmp_path,
        extra='<signalHeads><signalHead no="1" name="" lane="101 2" pos="15.0" sg="1 1"/></signalHeads>',
    )
    session = FakeSignalHeadDetectorSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("add_detector", link_no="101", lane_index=1)

    assert envelope["status"] == "error"
    assert "pos_m was not given" in envelope["message"]


def test_add_detector_omitted_pos_refuses_when_default_would_be_negative(tmp_path: Path):
    working = _write_working_inpx(
        tmp_path,
        extra='<signalHeads><signalHead no="1" name="" lane="101 1" pos="1.0" sg="1 1"/></signalHeads>',
    )
    session = FakeSignalHeadDetectorSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("add_detector", link_no="101", lane_index=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "negative" in envelope["message"]
    assert session.calls == []


def test_remove_detector_happy_path(tmp_path: Path):
    working = _write_working_inpx(
        tmp_path,
        extra='<detectors><detector no="1" name="" lane="101 1" pos="20" length="4.5" sc="1" portNo="501"/></detectors>',
    )
    session = FakeSignalHeadDetectorSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_detector", no="1")

    assert envelope["status"] == "ok"
    assert envelope["verification"]["readback_value"] == {"removed_no": "1", "still_present": False}
    assert session.calls == [{"op": "remove_detector", "no": 1}]
    root = ET.parse(working).getroot()
    assert root.findall(".//detectors/detector") == []


def test_remove_detector_vissim_refusal_surfaces_as_tool_execution_error(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeSignalHeadDetectorSession(
        working, raise_on={"remove_detector": RuntimeError("Detector 99 does not exist")}
    )
    set_active_edit_session(session)

    envelope = _dispatch("remove_detector", no="99")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert session.persist_calls == 0


# ---------------------------------------------------------------------------
# No active session -> structured error for every one of this unit's 4 tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name,params",
    [
        (
            "add_signal_head",
            {"link_no": "101", "lane_index": 1, "pos_m": 10.0, "sc_no": "1", "sg_no": "1"},
        ),
        ("remove_signal_head", {"no": "1"}),
        ("add_detector", {"link_no": "101", "lane_index": 1, "pos_m": 10.0}),
        ("remove_detector", {"no": "1"}),
    ],
)
def test_no_active_session_yields_structured_error_never_a_crash(tool_name, params):
    envelope = _dispatch(tool_name, **params)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]

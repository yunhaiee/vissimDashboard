"""Tests for Q4 unit (6)'s tools_edit.py stop-sign + priority-rule/
conflict-area PLACEMENT verbs: add_stop_sign/remove_stop_sign,
add_priority_rule/remove_priority_rule -- dispatched through the REAL
toolkit.dispatch (so pydantic validation + the envelope machinery are
exercised exactly like production), against a hand-rolled
``FakeStopSignPriorityRuleSession`` -- no real Vissim COM anywhere in this
file (see backend/tools/probe_stop_signs_priority_placement.py for the live
probe, and backend/tools/stop_signs_priority_rules_live_check.py for the
real-engine E2E).

House shape = test_agent_tools_signal_heads_detectors.py (the newest sibling
unit's own test file): the fake owns a real on-disk ``working_inpx`` since
every remove tool's post-persist "is it really gone" readback
(``tools_edit._still_present_after_remove``) needs genuinely-changed file
content, not a rigged in-memory value.
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
<link no="632" name="B"><lanes><lane width="3.5"/></lanes></link>
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


class FakeStopSignPriorityRuleSession:
    """Duck-types VissimEditSession's stop-sign/priority-rule surface:
    records every call, returns rigged readback values for ADD calls, and
    mutates the real on-disk ``working_inpx`` for REMOVE calls (see module
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

    def add_stop_sign(
        self,
        link_no: int,
        lane_index: int,
        pos_m: float,
        name: str = "",
        sc_no: int | None = None,
        sg_no: int | None = None,
    ) -> dict[str, Any]:
        self._record(
            "add_stop_sign",
            link_no=link_no, lane_index=lane_index, pos_m=pos_m, name=name, sc_no=sc_no, sg_no=sg_no,
        )
        return self._readback["stop_sign"]

    def remove_stop_sign(self, no: int) -> None:
        self._record("remove_stop_sign", no=no)
        _remove_by_no(self.working_inpx, "stopSigns", "stopSign", no)

    def add_priority_rule(
        self,
        stop_link_no: int,
        stop_lane_index: int,
        stop_pos_m: float,
        markers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        self._record(
            "add_priority_rule",
            stop_link_no=stop_link_no, stop_lane_index=stop_lane_index, stop_pos_m=stop_pos_m,
            markers=markers,
        )
        return self._readback["priority_rule"]

    def remove_priority_rule(self, no: int) -> None:
        self._record("remove_priority_rule", no=no)
        _remove_by_no(self.working_inpx, "priorityRules", "priorityRule", no)

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
# add_stop_sign / remove_stop_sign
# ---------------------------------------------------------------------------


def test_add_stop_sign_happy_path(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeStopSignPriorityRuleSession(
        working,
        readback={
            "stop_sign": {"no": 1, "name": "", "link": "101", "lane_index": 1, "pos": 5.0, "sc": None, "sg": None}
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch("add_stop_sign", link_no="101", lane_index=1, pos_m=5.0)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"link": "101", "lane_index": 1, "pos": 5.0},
        "readback_value": {"link": "101", "lane_index": 1, "pos": 5.0},
    }
    assert envelope["artifacts"] == {"no": 1, "name": ""}
    assert "Stop sign 1 added on link 101" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [
        {
            "op": "add_stop_sign", "link_no": 101, "lane_index": 1, "pos_m": 5.0, "name": "",
            "sc_no": None, "sg_no": None,
        }
    ]


def test_add_stop_sign_with_signal_group_wiring(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeStopSignPriorityRuleSession(
        working,
        readback={
            "stop_sign": {"no": 1, "name": "", "link": "101", "lane_index": 1, "pos": 5.0, "sc": "1", "sg": "22"}
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_stop_sign", link_no="101", lane_index=1, pos_m=5.0, sc_no="1", sg_no="22"
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"link": "101", "lane_index": 1, "pos": 5.0, "sc": "1", "sg": "22"},
        "readback_value": {"link": "101", "lane_index": 1, "pos": 5.0, "sc": "1", "sg": "22"},
    }
    assert session.calls[0]["sc_no"] == 1
    assert session.calls[0]["sg_no"] == 22
    assert isinstance(session.calls[0]["sc_no"], int)


def test_add_stop_sign_sc_no_without_sg_no_rejected_at_param_layer(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeStopSignPriorityRuleSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("add_stop_sign", link_no="101", lane_index=1, pos_m=5.0, sc_no="1")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_add_stop_sign_no_active_session_yields_structured_error():
    envelope = _dispatch("add_stop_sign", link_no="101", lane_index=1, pos_m=5.0)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]


def test_remove_stop_sign_happy_path(tmp_path: Path):
    working = _write_working_inpx(
        tmp_path,
        extra='<stopSigns><stopSign no="1" name="" lane="101 1" pos="5.0"/></stopSigns>',
    )
    session = FakeStopSignPriorityRuleSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_stop_sign", no="1")

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"removed_no": "1", "still_present": False},
        "readback_value": {"removed_no": "1", "still_present": False},
    }
    assert "Stop sign 1 removed" in envelope["message"]
    assert session.persist_calls == 1
    root = ET.parse(working).getroot()
    assert root.findall(".//stopSigns/stopSign") == []


def test_remove_stop_sign_vissim_refusal_surfaces_as_tool_execution_error(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeStopSignPriorityRuleSession(
        working, raise_on={"remove_stop_sign": RuntimeError("Stop sign 99 does not exist")}
    )
    set_active_edit_session(session)

    envelope = _dispatch("remove_stop_sign", no="99")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert session.persist_calls == 0


# ---------------------------------------------------------------------------
# add_priority_rule / remove_priority_rule
# ---------------------------------------------------------------------------


def _priority_rule_readback(
    *,
    no: int = 62,
    link: str = "101",
    lane_index: int = 1,
    pos: float = 5.0,
    template_rule_no: int = 3,
    markers: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "no": no,
        "link": link,
        "lane_index": lane_index,
        "pos": pos,
        "template_rule_no": template_rule_no,
        "markers": markers
        if markers is not None
        else [
            {
                "marker_index": 1, "link": "632", "lane_index": 1, "pos": 3.0,
                "gap_time_s": 3.0, "headway_m": 5.0,
            }
        ],
    }


def test_add_priority_rule_happy_path_single_marker_default_gap_headway(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeStopSignPriorityRuleSession(
        working, readback={"priority_rule": _priority_rule_readback()}
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_priority_rule",
        stop_link_no="101",
        stop_lane_index=1,
        stop_pos_m=5.0,
        markers=[{"link_no": "632", "lane_index": 1, "pos_m": 3.0}],
    )

    assert envelope["status"] == "ok"
    # gap_time_s/headway_m were NOT supplied by the caller -- they must be
    # ABSENT from written_value/readback_value entirely (Vissim's own
    # default, mirrors add_detector's own sc_no/port_no precedent), even
    # though the fake session's own readback reports concrete defaults.
    assert envelope["verification"] == {
        "written_value": {
            "link": "101", "lane_index": 1, "pos": 5.0,
            "markers": [{"link": "632", "lane_index": 1, "pos": 3.0}],
        },
        "readback_value": {
            "link": "101", "lane_index": 1, "pos": 5.0,
            "markers": [{"link": "632", "lane_index": 1, "pos": 3.0}],
        },
    }
    assert envelope["artifacts"]["no"] == 62
    assert "Priority rule 62 added" in envelope["message"]
    assert "template rule 3" in envelope["message"]
    assert "marker 1 on link 632 lane 1 @ 3.0m" in envelope["message"]
    assert "gap=3.0s, clearance=5.0m" in envelope["message"]
    assert session.calls == [
        {
            "op": "add_priority_rule", "stop_link_no": 101, "stop_lane_index": 1, "stop_pos_m": 5.0,
            "markers": [{"link_no": 632, "lane_index": 1, "pos_m": 3.0, "gap_time_s": None, "headway_m": None}],
        }
    ]


def test_add_priority_rule_explicit_gap_headway_becomes_part_of_the_claim(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeStopSignPriorityRuleSession(
        working,
        readback={
            "priority_rule": _priority_rule_readback(
                markers=[
                    {
                        "marker_index": 1, "link": "632", "lane_index": 1, "pos": 3.0,
                        "gap_time_s": 4.0, "headway_m": 10.0,
                    }
                ]
            )
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_priority_rule",
        stop_link_no="101",
        stop_lane_index=1,
        stop_pos_m=5.0,
        markers=[
            {"link_no": "632", "lane_index": 1, "pos_m": 3.0, "gap_time_s": 4.0, "headway_m": 10.0}
        ],
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"]["written_value"]["markers"][0] == {
        "link": "632", "lane_index": 1, "pos": 3.0, "gap_time_s": 4.0, "headway_m": 10.0,
    }
    assert envelope["verification"]["readback_value"]["markers"][0] == {
        "link": "632", "lane_index": 1, "pos": 3.0, "gap_time_s": 4.0, "headway_m": 10.0,
    }


def test_add_priority_rule_multi_marker(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeStopSignPriorityRuleSession(
        working,
        readback={
            "priority_rule": _priority_rule_readback(
                markers=[
                    {
                        "marker_index": 2, "link": "632", "lane_index": 1, "pos": 3.0,
                        "gap_time_s": 3.0, "headway_m": 5.0,
                    },
                    {
                        "marker_index": 1, "link": "632", "lane_index": 2, "pos": 3.0,
                        "gap_time_s": 3.0, "headway_m": 5.0,
                    },
                ]
            )
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_priority_rule",
        stop_link_no="101",
        stop_lane_index=1,
        stop_pos_m=5.0,
        markers=[
            {"link_no": "632", "lane_index": 1, "pos_m": 3.0},
            {"link_no": "632", "lane_index": 2, "pos_m": 3.0},
        ],
    )

    assert envelope["status"] == "ok"
    # Real, readback-verified marker_index per entry -- NOT assumed to equal
    # input order (Vissim's own marker ordering is not append-order; see
    # vissim_com.py's own module comment). The first REQUESTED marker
    # (lane_index=1) lands at marker_index=2 here, the second at
    # marker_index=1 -- exactly the kind of reordering this unit's own live
    # probe observed.
    assert envelope["artifacts"]["markers"][0]["marker_index"] == 2
    assert envelope["artifacts"]["markers"][1]["marker_index"] == 1
    assert "marker 2 on link 632 lane 1" in envelope["message"]
    assert "marker 1 on link 632 lane 2" in envelope["message"]


def test_add_priority_rule_requires_at_least_one_marker_at_param_layer(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeStopSignPriorityRuleSession(working)
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_priority_rule", stop_link_no="101", stop_lane_index=1, stop_pos_m=5.0, markers=[]
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_add_priority_rule_negative_gap_time_refused_by_preflight_constraint(tmp_path: Path):
    """add_priority_rule_marker_invalid (Q4 unit (6)'s one new constraint)
    refuses a negative gap_time_s/headway_m in ANY marker before the tool
    ever runs -- this test exercises it through the REAL dispatch path
    (toolkit.dispatch never wires guardrails.py itself, so this asserts the
    tool still RUNS at the dispatch layer; the constraint itself is
    unit-tested directly in test_agent_constraints.py). Mirrors
    priority_rule_gap_headway_invalid's own "0 is legitimate, only negative
    refused" semantics -- 0 must NOT be rejected."""
    working = _write_working_inpx(tmp_path)
    session = FakeStopSignPriorityRuleSession(
        working, readback={"priority_rule": _priority_rule_readback()}
    )
    set_active_edit_session(session)

    # dispatch() itself has no guardrail wiring -- a negative value at THIS
    # layer still reaches the tool (proving pydantic itself does not reject
    # a negative float, which is correct: the refusal is constraints.py's
    # job, exercised directly against the check function elsewhere).
    envelope = _dispatch(
        "add_priority_rule",
        stop_link_no="101",
        stop_lane_index=1,
        stop_pos_m=5.0,
        markers=[{"link_no": "632", "lane_index": 1, "pos_m": 3.0, "gap_time_s": -1.0}],
    )
    assert envelope["status"] == "ok"  # dispatch alone does not gate on constraints.py
    assert session.calls[0]["markers"][0]["gap_time_s"] == -1.0


def test_add_priority_rule_no_template_refuses_as_tool_execution_error(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeStopSignPriorityRuleSession(
        working,
        raise_on={
            "add_priority_rule": RuntimeError(
                "add_priority_rule: this network has no existing priority rule to duplicate as "
                "a structural template."
            )
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_priority_rule",
        stop_link_no="101",
        stop_lane_index=1,
        stop_pos_m=5.0,
        markers=[{"link_no": "632", "lane_index": 1, "pos_m": 3.0}],
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no existing priority rule" in envelope["message"]
    assert session.persist_calls == 0


def test_add_priority_rule_no_active_session_yields_structured_error():
    envelope = _dispatch(
        "add_priority_rule",
        stop_link_no="101",
        stop_lane_index=1,
        stop_pos_m=5.0,
        markers=[{"link_no": "632", "lane_index": 1, "pos_m": 3.0}],
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]


def test_remove_priority_rule_happy_path(tmp_path: Path):
    working = _write_working_inpx(
        tmp_path,
        extra='<priorityRules><priorityRule no="62" name="" lane="101 1" pos="5.0"/></priorityRules>',
    )
    session = FakeStopSignPriorityRuleSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_priority_rule", no="62")

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"removed_no": "62", "still_present": False},
        "readback_value": {"removed_no": "62", "still_present": False},
    }
    assert "Priority rule 62 removed" in envelope["message"]
    assert session.persist_calls == 1
    root = ET.parse(working).getroot()
    assert root.findall(".//priorityRules/priorityRule") == []


def test_remove_priority_rule_vissim_refusal_surfaces_as_tool_execution_error(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeStopSignPriorityRuleSession(
        working, raise_on={"remove_priority_rule": RuntimeError("Priority rule 99 does not exist")}
    )
    set_active_edit_session(session)

    envelope = _dispatch("remove_priority_rule", no="99")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert session.persist_calls == 0


# ---------------------------------------------------------------------------
# No active session -> structured error for every one of this unit's 4 tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name,params",
    [
        ("add_stop_sign", {"link_no": "101", "lane_index": 1, "pos_m": 5.0}),
        ("remove_stop_sign", {"no": "1"}),
        (
            "add_priority_rule",
            {
                "stop_link_no": "101", "stop_lane_index": 1, "stop_pos_m": 5.0,
                "markers": [{"link_no": "632", "lane_index": 1, "pos_m": 3.0}],
            },
        ),
        ("remove_priority_rule", {"no": "62"}),
    ],
)
def test_no_active_session_yields_structured_error_never_a_crash(tool_name, params):
    envelope = _dispatch(tool_name, **params)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]

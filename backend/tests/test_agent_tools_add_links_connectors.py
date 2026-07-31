"""Tests for Q4 unit (5)'s tools_edit.py add/remove links & connectors
verbs: add_link/remove_link, add_connector -- dispatched through the REAL
toolkit.dispatch (so pydantic validation + the envelope machinery are
exercised exactly like production), against a hand-rolled
``FakeLinkConnectorSession`` -- no real Vissim COM anywhere in this file.

House shape = test_agent_tools_signal_heads_detectors.py (the newest Q4
unit's own test file): the fake owns a real on-disk ``working_inpx`` since
remove_link's own reference-integrity preflight (services.inpx_structure.
find_id_references/find_lane_token_references) and every remove tool's
post-persist "is it really gone" readback (tools_edit._still_present_
after_remove) both need genuinely-changed file content, not a rigged
in-memory value.
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
<link no="103" name="C (unreferenced)"><lanes><lane width="3.5"/></lanes></link>
<link no="301" name="A-to-B connector">
<fromLinkEndPt link="101" pos="100"/>
<lanes><lane><fromLanes><objectRef key="101 1"/></fromLanes><toLanes><objectRef key="102 1"/></toLanes></lane></lanes>
<toLinkEndPt link="102" pos="0"/>
</link>
</links>
{extra}
</network>
"""


def _write_working_inpx(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "working.inpx"
    path.write_text(_NETWORK_TEMPLATE.format(extra=extra), encoding="utf-8")
    return path


def _remove_link_by_no(path: Path, no: Any) -> None:
    """Simulate a real COM RemoveLink + persist(): mutate the on-disk
    working copy so a subsequent reparse (this unit's own post-remove
    readback) sees the genuinely-removed state, not a stale fixture."""
    tree = ET.parse(path)
    root = tree.getroot()
    links_el = root.find("links")
    if links_el is None:
        return
    for el in list(links_el.findall("link")):
        if el.get("no") == str(no):
            links_el.remove(el)
    tree.write(path, encoding="UTF-8", xml_declaration=True)


class FakeLinkConnectorSession:
    """Duck-types VissimEditSession's link/connector surface: records every
    call, returns rigged readback values for ADD calls, and mutates the real
    on-disk ``working_inpx`` for REMOVE calls (see module docstring).
    Optionally raises on a named op to simulate a live Vissim refusal."""

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

    def add_link(
        self,
        points_xy: list[tuple[float, float]],
        lane_count: int,
        behavior_type: int | None = None,
        display_type: int | None = None,
        name: str = "",
    ) -> dict[str, Any]:
        self._record(
            "add_link",
            points_xy=points_xy, lane_count=lane_count, behavior_type=behavior_type,
            display_type=display_type, name=name,
        )
        return self._readback["link"]

    def remove_link(self, no: int) -> None:
        self._record("remove_link", no=no)
        _remove_link_by_no(self.working_inpx, no)

    def add_connector(
        self,
        from_link_no: int,
        from_lane_indices: list[int],
        from_pos_m: float,
        to_link_no: int,
        to_lane_indices: list[int],
        to_pos_m: float,
        name: str = "",
    ) -> dict[str, Any]:
        self._record(
            "add_connector",
            from_link_no=from_link_no, from_lane_indices=from_lane_indices, from_pos_m=from_pos_m,
            to_link_no=to_link_no, to_lane_indices=to_lane_indices, to_pos_m=to_pos_m, name=name,
        )
        return self._readback["connector"]

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
# add_link
# ---------------------------------------------------------------------------


def test_add_link_happy_path(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeLinkConnectorSession(
        working,
        readback={
            "link": {
                "no": 103, "name": "", "points": [(0.0, 0.0), (100.0, 0.0)], "lanes": 2,
                "length": 100.0, "is_connector": False, "behavior_type": "1", "display_type": "1",
            }
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_link", points_xy=[[0.0, 0.0], [100.0, 0.0]], lane_count=2
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"points": [[0.0, 0.0], [100.0, 0.0]], "lanes": 2},
        "readback_value": {"points": [[0.0, 0.0], [100.0, 0.0]], "lanes": 2},
    }
    assert envelope["artifacts"] == {"no": 103, "name": ""}
    assert "Link 103 added" in envelope["message"]
    assert "2 point(s), 2 lane(s)" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [
        {
            "op": "add_link", "points_xy": [(0.0, 0.0), (100.0, 0.0)], "lane_count": 2,
            "behavior_type": None, "display_type": None, "name": "",
        }
    ]


def test_add_link_behavior_and_display_type_claimed_only_when_supplied(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeLinkConnectorSession(
        working,
        readback={
            "link": {
                "no": 103, "name": "", "points": [(0.0, 0.0), (100.0, 0.0)], "lanes": 2,
                "length": 100.0, "is_connector": False, "behavior_type": "3", "display_type": "2",
            }
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_link", points_xy=[[0.0, 0.0], [100.0, 0.0]], lane_count=2, behavior_type=3, display_type=2
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"]["written_value"]["behavior_type"] == "3"
    assert envelope["verification"]["readback_value"]["behavior_type"] == "3"
    assert envelope["verification"]["written_value"]["display_type"] == "2"
    assert envelope["verification"]["readback_value"]["display_type"] == "2"


def test_add_link_omitted_behavior_and_display_type_never_become_a_verification_claim(tmp_path: Path):
    """Mirrors add_detector's own live-caught landmine (Q4 unit (4)): COM
    silently assigns its OWN default (observed live: "1"/"1") when these are
    omitted -- comparing the caller's own None against that surprise default
    would false-positive constraint #6. The fake readback mirrors the live
    finding exactly (behavior_type/display_type both "1" even though the
    caller never asked)."""
    working = _write_working_inpx(tmp_path)
    session = FakeLinkConnectorSession(
        working,
        readback={
            "link": {
                "no": 103, "name": "", "points": [(0.0, 0.0), (100.0, 0.0)], "lanes": 2,
                "length": 100.0, "is_connector": False, "behavior_type": "1", "display_type": "1",
            }
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch("add_link", points_xy=[[0.0, 0.0], [100.0, 0.0]], lane_count=2)

    assert envelope["status"] == "ok"
    assert "behavior_type" not in envelope["verification"]["written_value"]
    assert "behavior_type" not in envelope["verification"]["readback_value"]
    assert "display_type" not in envelope["verification"]["written_value"]
    assert "display_type" not in envelope["verification"]["readback_value"]


def test_add_link_rejects_fewer_than_two_points_at_param_layer():
    envelope = _dispatch("add_link", points_xy=[[0.0, 0.0]], lane_count=1)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_add_link_rejects_duplicate_consecutive_points():
    envelope = _dispatch(
        "add_link", points_xy=[[0.0, 0.0], [0.0, 0.0], [100.0, 0.0]], lane_count=1
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert "duplicate consecutive points" in envelope["message"]


def test_add_link_rejects_lane_count_below_one_at_param_layer():
    envelope = _dispatch("add_link", points_xy=[[0.0, 0.0], [100.0, 0.0]], lane_count=0)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_add_link_lane_count_above_ten_is_a_warning_not_a_refusal(tmp_path: Path):
    """Exercises the unit's own ONE new constraint (add_link_lane_count_high)
    through the real preflight machinery, not just pydantic -- the write
    still lands (allowed-with-warning tier), it does not error."""
    from services.agent import constraints

    findings = constraints.run_checks(
        "preflight",
        {"tool": "add_link", "params": {"points_xy": [[0, 0], [100, 0]], "lane_count": 12}},
        {"links": []},
    )
    matches = [f for f in findings if f.constraint_id == "add_link_lane_count_high"]
    assert len(matches) == 1
    assert matches[0].tier == "warn"


def test_add_link_no_active_session_yields_structured_error():
    envelope = _dispatch("add_link", points_xy=[[0.0, 0.0], [100.0, 0.0]], lane_count=1)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]


# ---------------------------------------------------------------------------
# remove_link
# ---------------------------------------------------------------------------


def test_remove_link_happy_path_when_unreferenced(tmp_path: Path):
    # Link 102 is referenced by connector 301's toLinkEndPt (base template);
    # link 103 is the base template's own deliberately unreferenced link.
    working = _write_working_inpx(tmp_path)
    session = FakeLinkConnectorSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_link", no="103")

    assert envelope["status"] == "ok"
    assert envelope["verification"]["readback_value"] == {"removed_no": "103", "still_present": False}
    assert "Link 103 removed (1 lane(s))" in envelope["message"]
    assert session.calls == [{"op": "remove_link", "no": 103}]
    assert session.persist_calls == 1
    root = ET.parse(working).getroot()
    assert [l.get("no") for l in root.findall(".//links/link")] == ["101", "102", "301"]


def test_remove_link_refuses_when_referenced_by_a_connector(tmp_path: Path):
    """THE flagship reference-integrity test: link 101 is the FROM-link of
    connector 301 (fromLinkEndPt link="101") -- removing it would leave the
    connector dangling. COM itself does NOT refuse this (vissim_com.py's own
    live-verified landmine 5b), so this tool's own preflight check is the
    only thing standing in the way."""
    working = _write_working_inpx(tmp_path)
    session = FakeLinkConnectorSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_link", no="101")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "still referenced by" in envelope["message"]
    assert "fromLinkEndPt" in envelope["message"]
    assert session.calls == []
    assert session.persist_calls == 0


def test_remove_link_refuses_when_a_lane_is_referenced_by_a_reduced_speed_area(tmp_path: Path):
    working = _write_working_inpx(
        tmp_path,
        extra='<reducedSpeedAreas><reducedSpeedArea no="1" lane="102 1" pos="5" length="10" name=""/>'
        "</reducedSpeedAreas>",
    )
    session = FakeLinkConnectorSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_link", no="102")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "still referenced by" in envelope["message"]
    assert "reduced speed area" in envelope["message"]
    assert session.calls == []


def test_remove_link_refuses_a_connector_target_and_points_at_remove_connector(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeLinkConnectorSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_link", no="301")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "CONNECTOR" in envelope["message"]
    assert "remove_connector" in envelope["message"]
    assert session.calls == []


def test_remove_link_unknown_link_surfaces_as_tool_execution_error(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeLinkConnectorSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_link", no="9999")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "not found" in envelope["message"]


def test_remove_link_vissim_refusal_surfaces_as_tool_execution_error(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeLinkConnectorSession(
        working, raise_on={"remove_link": RuntimeError("Link 102 could not be removed")}
    )
    set_active_edit_session(session)

    envelope = _dispatch("remove_link", no="102")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert session.persist_calls == 0


def test_remove_link_no_active_session_yields_structured_error():
    envelope = _dispatch("remove_link", no="102")
    assert envelope["status"] == "error"
    assert "no_edit_session" in envelope["message"]


# ---------------------------------------------------------------------------
# add_connector
# ---------------------------------------------------------------------------


def test_add_connector_happy_path(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeLinkConnectorSession(
        working,
        readback={
            "connector": {
                "no": 302, "name": "", "points": [(0.0, 0.0), (10.0, 0.0)], "lanes": 1,
                "length": 10.0, "is_connector": True, "behavior_type": "1", "display_type": "1",
                "from_link": "101", "to_link": "102", "from_pos": 100.0, "to_pos": 0.0,
                "from_lanes": [1], "to_lanes": [1],
            }
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_connector", from_link="101", from_lanes=[1], from_pos_m=100.0,
        to_link="102", to_lanes=[1], to_pos_m=0.0,
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {
            "from_link": "101", "from_lanes": [1], "from_pos": 100.0,
            "to_link": "102", "to_lanes": [1], "to_pos": 0.0,
        },
        "readback_value": {
            "from_link": "101", "from_lanes": [1], "from_pos": 100.0,
            "to_link": "102", "to_lanes": [1], "to_pos": 0.0,
        },
    }
    assert envelope["artifacts"] == {"no": 302, "name": ""}
    assert "Connector 302 added" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [
        {
            "op": "add_connector", "from_link_no": 101, "from_lane_indices": [1], "from_pos_m": 100.0,
            "to_link_no": 102, "to_lane_indices": [1], "to_pos_m": 0.0, "name": "",
        }
    ]


def test_add_connector_multi_lane_shift_mapping(tmp_path: Path):
    """The lane-SHIFT case this unit's own probe live-verified against real
    COM (from_lanes=[2,3] -> to_lanes=[1,2]) -- both anchors independent,
    contiguous ascending runs of equal length."""
    working = _write_working_inpx(tmp_path)
    session = FakeLinkConnectorSession(
        working,
        readback={
            "connector": {
                "no": 302, "name": "", "points": [], "lanes": 2, "length": 10.0, "is_connector": True,
                "behavior_type": "1", "display_type": "1", "from_link": "101", "to_link": "102",
                "from_pos": 100.0, "to_pos": 0.0, "from_lanes": [2, 3], "to_lanes": [1, 2],
            }
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_connector", from_link="101", from_lanes=[2, 3], from_pos_m=100.0,
        to_link="102", to_lanes=[1, 2], to_pos_m=0.0,
    )

    assert envelope["status"] == "ok"
    assert session.calls[0]["from_lane_indices"] == [2, 3]
    assert session.calls[0]["to_lane_indices"] == [1, 2]


def test_add_connector_rejects_from_link_equals_to_link():
    envelope = _dispatch(
        "add_connector", from_link="101", from_lanes=[1], from_pos_m=0.0,
        to_link="101", to_lanes=[1], to_pos_m=10.0,
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert "must differ" in envelope["message"]


def test_add_connector_rejects_non_contiguous_from_lanes():
    envelope = _dispatch(
        "add_connector", from_link="101", from_lanes=[1, 3], from_pos_m=0.0,
        to_link="102", to_lanes=[1, 2], to_pos_m=0.0,
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert "contiguous ascending run" in envelope["message"]


def test_add_connector_rejects_mismatched_lane_list_lengths():
    envelope = _dispatch(
        "add_connector", from_link="101", from_lanes=[1, 2], from_pos_m=0.0,
        to_link="102", to_lanes=[1], to_pos_m=0.0,
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert "same length" in envelope["message"]


def test_add_connector_rejects_lane_count_mismatch_with_from_lanes():
    envelope = _dispatch(
        "add_connector", from_link="101", from_lanes=[1], from_pos_m=0.0,
        to_link="102", to_lanes=[1], to_pos_m=0.0, lane_count=2,
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert "does not match" in envelope["message"]


def test_add_connector_bogus_lane_surfaces_as_tool_execution_error(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeLinkConnectorSession(
        working,
        raise_on={
            "add_connector": RuntimeError(
                "Lane 5 does not exist on link 101 in the loaded VISSIM network."
            )
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_connector", from_link="101", from_lanes=[5], from_pos_m=0.0,
        to_link="102", to_lanes=[1], to_pos_m=0.0,
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "Lane 5" in envelope["message"]


def test_add_connector_no_active_session_yields_structured_error():
    envelope = _dispatch(
        "add_connector", from_link="101", from_lanes=[1], from_pos_m=0.0,
        to_link="102", to_lanes=[1], to_pos_m=0.0,
    )
    assert envelope["status"] == "error"
    assert "no_edit_session" in envelope["message"]


# ---------------------------------------------------------------------------
# No active session -> structured error for every one of this unit's 3 tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name,params",
    [
        ("add_link", {"points_xy": [[0.0, 0.0], [100.0, 0.0]], "lane_count": 2}),
        ("remove_link", {"no": "101"}),
        (
            "add_connector",
            {
                "from_link": "101", "from_lanes": [1], "from_pos_m": 0.0,
                "to_link": "102", "to_lanes": [1], "to_pos_m": 0.0,
            },
        ),
    ],
)
def test_no_active_session_yields_structured_error_never_a_crash(tool_name, params):
    envelope = _dispatch(tool_name, **params)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]

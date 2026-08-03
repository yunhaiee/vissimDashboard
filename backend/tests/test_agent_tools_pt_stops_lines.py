"""Tests for Q4 unit (7)'s tools_edit.py PT stop + PT line PLACEMENT verbs:
add_pt_stop/remove_pt_stop, add_pt_line/remove_pt_line,
set_pt_line_stop_dwell -- dispatched through the REAL toolkit.dispatch (so
pydantic validation + the envelope machinery are exercised exactly like
production), against a hand-rolled ``FakePtStopLineSession`` -- no real
Vissim COM anywhere in this file (see backend/tools/probe_pt_stops_lines.py
for the live probe, and backend/tools/pt_stops_lines_live_check.py for the
real-engine E2E).

House shape = test_agent_tools_stop_signs_priority_rules.py (the newest
sibling unit's own test file): the fake owns a real on-disk ``working_inpx``
since every remove tool's post-persist "is it really gone" readback
(``tools_edit._still_present_after_remove``) needs genuinely-changed file
content, not a rigged in-memory value; the reference-integrity refusal test
(``remove_pt_stop``) needs a genuinely-parseable XML fixture too.
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


class FakePtStopLineSession:
    """Duck-types VissimEditSession's PT stop/PT line surface: records every
    call, returns rigged readback values for ADD/SET calls, and mutates the
    real on-disk ``working_inpx`` for REMOVE calls (see module docstring).
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

    def add_pt_stop(
        self, link_no: int, lane_index: int, pos_m: float, length_m: float | None = None, name: str = ""
    ) -> dict[str, Any]:
        self._record(
            "add_pt_stop", link_no=link_no, lane_index=lane_index, pos_m=pos_m, length_m=length_m, name=name
        )
        return self._readback["pt_stop"]

    def remove_pt_stop(self, no: int) -> None:
        self._record("remove_pt_stop", no=no)
        _remove_by_no(self.working_inpx, "ptStops", "ptStop", no)

    def add_pt_line(
        self,
        entry_link_no: int,
        dest_link_no: int,
        dest_pos_m: float,
        veh_type_no: int | None = None,
        pt_stop_no: int | None = None,
        name: str = "",
    ) -> dict[str, Any]:
        self._record(
            "add_pt_line",
            entry_link_no=entry_link_no, dest_link_no=dest_link_no, dest_pos_m=dest_pos_m,
            veh_type_no=veh_type_no, pt_stop_no=pt_stop_no, name=name,
        )
        return self._readback["pt_line"]

    def remove_pt_line(self, no: int) -> None:
        self._record("remove_pt_line", no=no)
        _remove_by_no(self.working_inpx, "ptLines", "ptLine", no)

    def set_pt_line_stop_dwell(self, line_no: int, stop_no: int, time_distribution_no: int) -> dict[str, Any]:
        self._record(
            "set_pt_line_stop_dwell", line_no=line_no, stop_no=stop_no,
            time_distribution_no=time_distribution_no,
        )
        return self._readback.get("pt_line_stop_dwell", {})

    def get_pt_line_stop_dwell(self, line_no: int, stop_no: int) -> dict[str, Any]:
        self._record("get_pt_line_stop_dwell", line_no=line_no, stop_no=stop_no)
        return self._readback["pt_line_stop_dwell"]

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
# add_pt_stop / remove_pt_stop
# ---------------------------------------------------------------------------


def test_add_pt_stop_happy_path_no_length(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakePtStopLineSession(
        working,
        readback={
            "pt_stop": {"no": 1, "name": "", "link": "101", "lane_index": 1, "pos": 5.0, "length": 15.0}
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch("add_pt_stop", link_no="101", lane_index=1, pos_m=5.0)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"link": "101", "lane_index": 1, "pos": 5.0},
        "readback_value": {"link": "101", "lane_index": 1, "pos": 5.0},
    }
    assert envelope["artifacts"] == {"no": 1, "name": "", "length": 15.0}
    assert "PT stop 1 added on link 101" in envelope["message"]
    assert "15.0 m" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [
        {"op": "add_pt_stop", "link_no": 101, "lane_index": 1, "pos_m": 5.0, "length_m": None, "name": ""}
    ]


def test_add_pt_stop_with_explicit_length_becomes_part_of_the_claim(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakePtStopLineSession(
        working,
        readback={
            "pt_stop": {"no": 1, "name": "", "link": "101", "lane_index": 1, "pos": 5.0, "length": 12.0}
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch("add_pt_stop", link_no="101", lane_index=1, pos_m=5.0, length_m=12.0)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"link": "101", "lane_index": 1, "pos": 5.0, "length": 12.0},
        "readback_value": {"link": "101", "lane_index": 1, "pos": 5.0, "length": 12.0},
    }
    assert session.calls[0]["length_m"] == 12.0


def test_add_pt_stop_no_active_session_yields_structured_error():
    envelope = _dispatch("add_pt_stop", link_no="101", lane_index=1, pos_m=5.0)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]


def test_remove_pt_stop_happy_path_when_unreferenced(tmp_path: Path):
    working = _write_working_inpx(
        tmp_path,
        extra='<ptStops><ptStop no="1" name="" lane="101 1" pos="5.0" length="15.0"/></ptStops>',
    )
    session = FakePtStopLineSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_pt_stop", no="1")

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"removed_no": "1", "still_present": False},
        "readback_value": {"removed_no": "1", "still_present": False},
    }
    assert "PT stop 1 removed" in envelope["message"]
    assert session.persist_calls == 1
    root = ET.parse(working).getroot()
    assert root.findall(".//ptStops/ptStop") == []


def test_remove_pt_stop_refuses_when_referenced_by_a_pt_line_stop(tmp_path: Path):
    """THE reference-integrity test the task brief asks for: removing a PT
    stop still referenced by a real ptLineStop REFUSES naming it -- COM
    itself would NOT refuse this (live-verified,
    backend/tools/probe_pt_stops_lines.py [1b.15b]: RemovePTStop silently
    CASCADE-DELETES the referencing ptLineStop entry instead of refusing),
    so this tool's own preflight check (services.inpx_structure.
    find_id_references) is the only thing standing in the way."""
    working = _write_working_inpx(
        tmp_path,
        extra=(
            '<ptStops><ptStop no="99569" name="" lane="101 1" pos="5.0" length="15.0"/></ptStops>'
            '<ptLines><ptLine no="52004" name="WB"><lineStops>'
            '<ptLineStop ptStop="99569" dwellTmDistr="20" dwellTmDef="DISTRIBUTION" active="true"/>'
            "</lineStops></ptLine></ptLines>"
        ),
    )
    session = FakePtStopLineSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_pt_stop", no="99569")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "still referenced by" in envelope["message"]
    assert "ptLineStop" in envelope["message"]
    # Refused BEFORE ever calling COM at all, and before persist().
    assert session.calls == []
    assert session.persist_calls == 0


@pytest.mark.parametrize("spelling", ["99569", "099569", " 99569", "+99569", "99569 "])
def test_remove_pt_stop_refuses_every_spelling_of_a_referenced_id(tmp_path: Path, spelling: str):
    """The Q6 (2) remove_parking_lot bypass, pinned here too: the XML guard
    compares strings while _coerce_id normalises for COM, so before the
    canonicalisation fix every non-canonical spelling sailed past the
    referrer refusal straight into COM's silent cascade-delete."""
    working = _write_working_inpx(
        tmp_path,
        extra=(
            '<ptStops><ptStop no="99569" name="" lane="101 1" pos="5.0" length="15.0"/></ptStops>'
            '<ptLines><ptLine no="52004" name="WB"><lineStops>'
            '<ptLineStop ptStop="99569" dwellTmDistr="20" dwellTmDef="DISTRIBUTION" active="true"/>'
            "</lineStops></ptLine></ptLines>"
        ),
    )
    session = FakePtStopLineSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_pt_stop", no=spelling)

    assert envelope["status"] == "error"
    assert "still referenced by" in envelope["message"]
    assert session.calls == []
    assert session.persist_calls == 0


def test_remove_pt_stop_refuses_when_referenced_by_a_pt_boarding_detector(tmp_path: Path):
    """The SECOND real referrer site this unit's own corpus-wide scan found
    (a PT-boarding-gate detector's own "ptStop" attribute) -- previously
    unregistered anywhere in this codebase."""
    working = _write_working_inpx(
        tmp_path,
        extra=(
            '<ptStops><ptStop no="5" name="" lane="101 1" pos="5.0" length="15.0"/></ptStops>'
            '<detectors><detector no="541" lane="101 1" pos="10" length="0.4" ptStop="5"/></detectors>'
        ),
    )
    session = FakePtStopLineSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_pt_stop", no="5")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "still referenced by" in envelope["message"]
    assert "detector" in envelope["message"]
    assert session.calls == []
    assert session.persist_calls == 0


def test_remove_pt_stop_vissim_refusal_surfaces_as_tool_execution_error(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakePtStopLineSession(
        working, raise_on={"remove_pt_stop": RuntimeError("PT stop 99 does not exist")}
    )
    set_active_edit_session(session)

    envelope = _dispatch("remove_pt_stop", no="99")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert session.persist_calls == 0


# ---------------------------------------------------------------------------
# add_pt_line / remove_pt_line
# ---------------------------------------------------------------------------


def _pt_line_readback(
    *,
    no: int = 65105,
    name: str = "",
    veh_type: str | None = "300",
    entry_link: str = "13270462",
    dest_link: str = "12270123",
    dest_pos: float = 189.13,
    link_seq: list[str] | None = None,
    stops: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "no": no,
        "name": name,
        "veh_type": veh_type,
        "entry_link": entry_link,
        "dest_link": dest_link,
        "dest_pos": dest_pos,
        "entry_tm_distr": None,
        "link_seq": link_seq if link_seq is not None else [entry_link],
        "stops": stops if stops is not None else [
            {"pt_stop_no": "99569", "dwell_tm_distr": "1", "active": True}
        ],
    }


def test_add_pt_line_happy_path_default_stop(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakePtStopLineSession(working, readback={"pt_line": _pt_line_readback()})
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_pt_line", entry_link_no="13270462", dest_link_no="12270123", dest_pos_m=189.13
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"entry_link": "13270462", "dest_link": "12270123", "dest_pos": 189.13},
        "readback_value": {"entry_link": "13270462", "dest_link": "12270123", "dest_pos": 189.13},
    }
    assert envelope["artifacts"]["no"] == 65105
    assert "PT line 65105 added" in envelope["message"]
    assert "minimal default route" in envelope["message"]
    assert "Auto-created stop: PT stop 99569" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [
        {
            "op": "add_pt_line", "entry_link_no": 13270462, "dest_link_no": 12270123,
            "dest_pos_m": 189.13, "veh_type_no": None, "pt_stop_no": None, "name": "",
        }
    ]


def test_add_pt_line_with_veh_type_and_pt_stop_becomes_part_of_the_claim(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakePtStopLineSession(
        working,
        readback={
            "pt_line": _pt_line_readback(
                veh_type="300",
                stops=[{"pt_stop_no": "13", "dwell_tm_distr": "1", "active": True}],
            )
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_pt_line",
        entry_link_no="13270462",
        dest_link_no="12270123",
        dest_pos_m=189.13,
        veh_type_no="300",
        pt_stop_no="13",
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {
            "entry_link": "13270462", "dest_link": "12270123", "dest_pos": 189.13,
            "veh_type": "300", "pt_stop_no": "13",
        },
        "readback_value": {
            "entry_link": "13270462", "dest_link": "12270123", "dest_pos": 189.13,
            "veh_type": "300", "pt_stop_no": "13",
        },
    }
    assert session.calls[0]["veh_type_no"] == 300
    assert session.calls[0]["pt_stop_no"] == 13
    assert isinstance(session.calls[0]["veh_type_no"], int)


def test_add_pt_line_no_active_session_yields_structured_error():
    envelope = _dispatch(
        "add_pt_line", entry_link_no="13270462", dest_link_no="12270123", dest_pos_m=189.13
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]


def test_remove_pt_line_happy_path(tmp_path: Path):
    working = _write_working_inpx(
        tmp_path,
        extra='<ptLines><ptLine no="65105" name=""><lineStops/></ptLine></ptLines>',
    )
    session = FakePtStopLineSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_pt_line", no="65105")

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"removed_no": "65105", "still_present": False},
        "readback_value": {"removed_no": "65105", "still_present": False},
    }
    assert "PT line 65105 removed" in envelope["message"]
    assert session.persist_calls == 1
    root = ET.parse(working).getroot()
    assert root.findall(".//ptLines/ptLine") == []


def test_remove_pt_line_vissim_refusal_surfaces_as_tool_execution_error(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakePtStopLineSession(
        working, raise_on={"remove_pt_line": RuntimeError("PT line 99 does not exist")}
    )
    set_active_edit_session(session)

    envelope = _dispatch("remove_pt_line", no="99")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert session.persist_calls == 0


# ---------------------------------------------------------------------------
# set_pt_line_stop_dwell
# ---------------------------------------------------------------------------


def test_set_pt_line_stop_dwell_happy_path(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakePtStopLineSession(
        working,
        readback={"pt_line_stop_dwell": {"line_no": "52004", "stop_no": "99569", "dwell_tm_distr": "10"}},
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_pt_line_stop_dwell", line_no="52004", stop_no="99569", time_distribution_no="10"
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": "10", "readback_value": "10"}
    assert "PT line 52004 stop 99569 dwell-time distribution set to 10" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [
        {"op": "set_pt_line_stop_dwell", "line_no": 52004, "stop_no": 99569, "time_distribution_no": 10},
        {"op": "get_pt_line_stop_dwell", "line_no": 52004, "stop_no": 99569},
    ]


def test_set_pt_line_stop_dwell_vissim_refusal_surfaces_as_tool_execution_error(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakePtStopLineSession(
        working,
        raise_on={
            "set_pt_line_stop_dwell": RuntimeError(
                "PT line 52004 has no line-stop referencing PT stop 999."
            )
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_pt_line_stop_dwell", line_no="52004", stop_no="999", time_distribution_no="10"
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert session.persist_calls == 0


def test_set_pt_line_stop_dwell_no_active_session_yields_structured_error():
    envelope = _dispatch(
        "set_pt_line_stop_dwell", line_no="52004", stop_no="99569", time_distribution_no="10"
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]


# ---------------------------------------------------------------------------
# No active session -> structured error for every one of this unit's 5 tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name,params",
    [
        ("add_pt_stop", {"link_no": "101", "lane_index": 1, "pos_m": 5.0}),
        ("remove_pt_stop", {"no": "1"}),
        (
            "add_pt_line",
            {"entry_link_no": "13270462", "dest_link_no": "12270123", "dest_pos_m": 189.13},
        ),
        ("remove_pt_line", {"no": "65105"}),
        (
            "set_pt_line_stop_dwell",
            {"line_no": "52004", "stop_no": "99569", "time_distribution_no": "10"},
        ),
    ],
)
def test_no_active_session_yields_structured_error_never_a_crash(tool_name, params):
    envelope = _dispatch(tool_name, **params)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]

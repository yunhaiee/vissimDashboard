"""Tests for Q4 unit (2)'s tools_edit.py measurement-infrastructure verbs:
add_queue_counter/remove_queue_counter, add_data_collection_point/
remove_data_collection_point, add_data_collection_measurement/
remove_data_collection_measurement, add_travel_time_section/
remove_travel_time_section -- dispatched through the REAL toolkit.dispatch
(so pydantic validation + the envelope machinery are exercised exactly like
production), against a hand-rolled ``FakeMeasurementInfraSession`` -- no real
Vissim COM anywhere in this file.

Unlike test_agent_tools_edit.py's ``FakeEditSession`` (COM calls only), this
fake ALSO owns a real on-disk ``working_inpx``: two of this unit's own
verbs read the file directly rather than going through the fake session's
methods -- ``remove_data_collection_point``'s own reference-integrity check
(``services.inpx_structure.find_id_references``) and every remove tool's
own post-persist "is it really gone" readback
(``tools_edit._still_present_after_remove``, itself a fresh
``build_shadow_model`` reparse). The fake's own ``remove_*`` methods mutate
that file directly (simulating what a real COM ``Remove*`` + ``persist()``
would leave on disk), so those two real reads see genuinely-changed content,
not a rigged in-memory value.
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
    copy so a subsequent reparse (this unit's own post-remove readback /
    reference-integrity check) sees the genuinely-removed state, not a
    stale fixture."""
    tree = ET.parse(path)
    root = tree.getroot()
    container = root.find(container_tag)
    if container is None:
        return
    for el in list(container.findall(child_tag)):
        if el.get("no") == str(no):
            container.remove(el)
    tree.write(path, encoding="UTF-8", xml_declaration=True)


class FakeMeasurementInfraSession:
    """Duck-types VissimEditSession's measurement-infra surface: records
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

    def add_queue_counter(self, link_no: int, pos_m: float, name: str = "") -> dict[str, Any]:
        self._record("add_queue_counter", link_no=link_no, pos_m=pos_m, name=name)
        return self._readback["queue_counter"]

    def remove_queue_counter(self, no: int) -> None:
        self._record("remove_queue_counter", no=no)
        _remove_by_no(self.working_inpx, "queueCounters", "queueCounter", no)

    def add_data_collection_point(
        self, link_no: int, lane_index: int, pos_m: float, name: str = ""
    ) -> dict[str, Any]:
        self._record(
            "add_data_collection_point", link_no=link_no, lane_index=lane_index, pos_m=pos_m, name=name
        )
        return self._readback["data_collection_point"]

    def remove_data_collection_point(self, no: int) -> None:
        self._record("remove_data_collection_point", no=no)
        _remove_by_no(self.working_inpx, "dataCollectionPoints", "dataCollectionPoint", no)

    def add_data_collection_measurement(self, point_nos: list[int], name: str = "") -> dict[str, Any]:
        self._record("add_data_collection_measurement", point_nos=point_nos, name=name)
        return self._readback["data_collection_measurement"]

    def remove_data_collection_measurement(self, no: int) -> None:
        self._record("remove_data_collection_measurement", no=no)
        _remove_by_no(self.working_inpx, "dataCollectionMeasurements", "dataCollectionMeasurement", no)

    def add_travel_time_section(
        self, name: str, from_link: int, from_pos_m: float, to_link: int, to_pos_m: float
    ) -> dict[str, Any]:
        self._record(
            "add_travel_time_section",
            name=name,
            from_link=from_link,
            from_pos_m=from_pos_m,
            to_link=to_link,
            to_pos_m=to_pos_m,
        )
        return self._readback["travel_time_section"]

    def remove_travel_time_section(self, no: int) -> None:
        self._record("remove_travel_time_section", no=no)
        _remove_by_no(
            self.working_inpx, "vehicleTravelTimeMeasurements", "vehicleTravelTimeMeasurement", no
        )

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
    # NOT named "name" -- add_travel_time_section's own param is called
    # "name" and a **params kwarg collision would shadow this function's
    # own first positional argument.
    return dispatch(tool_name, params, registry=default_registry)


# ---------------------------------------------------------------------------
# add_queue_counter / remove_queue_counter
# ---------------------------------------------------------------------------


def test_add_queue_counter_happy_path(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeMeasurementInfraSession(
        working, readback={"queue_counter": {"no": 3, "name": "", "link": "101", "pos": 20.0}}
    )
    set_active_edit_session(session)

    envelope = _dispatch("add_queue_counter", link_no="101", pos_m=20.0)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"link": "101", "pos": 20.0},
        "readback_value": {"link": "101", "pos": 20.0},
    }
    assert envelope["artifacts"] == {"no": 3, "name": ""}
    assert "Queue counter 3 added on link 101" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [{"op": "add_queue_counter", "link_no": 101, "pos_m": 20.0, "name": ""}]
    assert isinstance(session.calls[0]["link_no"], int)


def test_add_queue_counter_readback_mismatch_still_reports_ok_envelope(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeMeasurementInfraSession(
        working, readback={"queue_counter": {"no": 3, "name": "", "link": "101", "pos": 19.999999999999996}}
    )
    set_active_edit_session(session)

    envelope = _dispatch("add_queue_counter", link_no="101", pos_m=20.0)

    # Not this tool's job to compare -- guardrails.py's postwrite (#6) does
    # that against the SAME written_value/readback_value pair; the tool
    # itself always reports ok when the COM call didn't raise.
    assert envelope["status"] == "ok"
    assert envelope["verification"]["readback_value"]["pos"] == pytest.approx(20.0)


def test_add_queue_counter_no_active_session_yields_structured_error():
    envelope = _dispatch("add_queue_counter", link_no="101", pos_m=20.0)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]


def test_remove_queue_counter_happy_path(tmp_path: Path):
    working = _write_working_inpx(
        tmp_path, extra='<queueCounters><queueCounter no="3" link="101" pos="20" name=""/></queueCounters>'
    )
    session = FakeMeasurementInfraSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_queue_counter", no="3")

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"removed_no": "3"},
        "readback_value": {"removed_no": None},
    }
    assert "Queue counter 3 removed" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [{"op": "remove_queue_counter", "no": 3}]
    # Genuinely gone from the saved file, not just claimed.
    root = ET.parse(working).getroot()
    assert root.findall(".//queueCounters/queueCounter") == []


def test_remove_queue_counter_vissim_refusal_surfaces_as_tool_execution_error(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeMeasurementInfraSession(
        working, raise_on={"remove_queue_counter": RuntimeError("Queue counter 99 does not exist")}
    )
    set_active_edit_session(session)

    envelope = _dispatch("remove_queue_counter", no="99")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "does not exist" in envelope["message"]
    assert session.persist_calls == 0


# ---------------------------------------------------------------------------
# add_data_collection_point / remove_data_collection_point
# ---------------------------------------------------------------------------


def test_add_data_collection_point_happy_path(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeMeasurementInfraSession(
        working,
        readback={
            "data_collection_point": {"no": 5, "name": "", "link": "101", "lane_index": 1, "pos": 10.0}
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch("add_data_collection_point", link_no="101", lane_index=1, pos_m=10.0)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"link": "101", "lane_index": 1, "pos": 10.0},
        "readback_value": {"link": "101", "lane_index": 1, "pos": 10.0},
    }
    assert envelope["artifacts"] == {"no": 5, "name": ""}
    assert session.calls == [
        {"op": "add_data_collection_point", "link_no": 101, "lane_index": 1, "pos_m": 10.0, "name": ""}
    ]


def test_add_data_collection_point_rejects_lane_index_below_one_at_param_layer(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeMeasurementInfraSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("add_data_collection_point", link_no="101", lane_index=0, pos_m=10.0)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []  # never reached the tool body


def test_remove_data_collection_point_happy_path_when_unreferenced(tmp_path: Path):
    working = _write_working_inpx(
        tmp_path,
        extra='<dataCollectionPoints><dataCollectionPoint no="7" lane="101 1" pos="10" name=""/>'
        "</dataCollectionPoints>",
    )
    session = FakeMeasurementInfraSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_data_collection_point", no="7")

    assert envelope["status"] == "ok"
    assert envelope["verification"]["readback_value"] == {"removed_no": None}
    assert session.calls == [{"op": "remove_data_collection_point", "no": 7}]
    root = ET.parse(working).getroot()
    assert root.findall(".//dataCollectionPoints/dataCollectionPoint") == []


def test_remove_data_collection_point_refuses_when_referenced_by_a_measurement(tmp_path: Path):
    """THE reference-integrity test the task brief asks for: removing a
    data-collection point still referenced by a measurement REFUSES naming
    it -- COM itself would NOT refuse this (live-verified,
    backend/tools/probe_measurement_infra_followup.py), so this tool's own
    preflight check (services.inpx_structure.find_id_references) is the
    only thing standing in the way."""
    working = _write_working_inpx(
        tmp_path,
        extra=(
            '<dataCollectionPoints><dataCollectionPoint no="7" lane="101 1" pos="10" name=""/>'
            "</dataCollectionPoints>"
            '<dataCollectionMeasurements><dataCollectionMeasurement no="1" name="">'
            '<dataCollectionPoints><intObjectRef key="7"/></dataCollectionPoints>'
            "</dataCollectionMeasurement></dataCollectionMeasurements>"
        ),
    )
    session = FakeMeasurementInfraSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_data_collection_point", no="7")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "still referenced by" in envelope["message"]
    assert "intObjectRef" in envelope["message"]
    # Refused BEFORE ever calling COM at all, and before persist().
    assert session.calls == []
    assert session.persist_calls == 0
    # The point is still there -- refused, not silently cascaded.
    root = ET.parse(working).getroot()
    assert len(root.findall(".//dataCollectionPoints/dataCollectionPoint")) == 1


def test_remove_data_collection_point_succeeds_once_the_measurement_is_removed_first(tmp_path: Path):
    """The honest undo path: remove the referencing measurement, THEN the
    point removal succeeds -- proves the refusal is a real, recoverable
    ordering constraint, not a permanent lock."""
    working = _write_working_inpx(
        tmp_path,
        extra=(
            '<dataCollectionPoints><dataCollectionPoint no="7" lane="101 1" pos="10" name=""/>'
            "</dataCollectionPoints>"
            '<dataCollectionMeasurements><dataCollectionMeasurement no="1" name="">'
            '<dataCollectionPoints><intObjectRef key="7"/></dataCollectionPoints>'
            "</dataCollectionMeasurement></dataCollectionMeasurements>"
        ),
    )
    session = FakeMeasurementInfraSession(working)
    set_active_edit_session(session)

    blocked = _dispatch("remove_data_collection_point", no="7")
    assert blocked["status"] == "error"

    freed = _dispatch("remove_data_collection_measurement", no="1")
    assert freed["status"] == "ok"

    now_allowed = _dispatch("remove_data_collection_point", no="7")
    assert now_allowed["status"] == "ok"


# ---------------------------------------------------------------------------
# add_data_collection_measurement / remove_data_collection_measurement
# ---------------------------------------------------------------------------


def test_add_data_collection_measurement_happy_path(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeMeasurementInfraSession(
        working,
        readback={"data_collection_measurement": {"no": 2, "name": "", "point_nos": [1, 2]}},
    )
    set_active_edit_session(session)

    envelope = _dispatch("add_data_collection_measurement", point_nos=["1", "2"])

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"point_nos": ["1", "2"]},
        "readback_value": {"point_nos": ["1", "2"]},
    }
    assert envelope["artifacts"] == {"no": 2, "name": ""}
    assert session.calls == [
        {"op": "add_data_collection_measurement", "point_nos": [1, 2], "name": ""}
    ]


def test_add_data_collection_measurement_rejects_empty_point_nos_at_param_layer(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeMeasurementInfraSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("add_data_collection_measurement", point_nos=[])

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert session.calls == []


def test_add_data_collection_measurement_bogus_point_surfaces_as_tool_execution_error(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeMeasurementInfraSession(
        working,
        raise_on={
            "add_data_collection_measurement": RuntimeError(
                "Could not add a data collection measurement grouping points [999]: "
                "Data collection point 999 does not exist"
            )
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch("add_data_collection_measurement", point_nos=["999"])

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "999" in envelope["message"]


def test_remove_data_collection_measurement_happy_path(tmp_path: Path):
    working = _write_working_inpx(
        tmp_path,
        extra=(
            '<dataCollectionMeasurements><dataCollectionMeasurement no="1" name="">'
            "<dataCollectionPoints/></dataCollectionMeasurement></dataCollectionMeasurements>"
        ),
    )
    session = FakeMeasurementInfraSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_data_collection_measurement", no="1")

    assert envelope["status"] == "ok"
    assert envelope["verification"]["readback_value"] == {"removed_no": None}
    root = ET.parse(working).getroot()
    assert root.findall(".//dataCollectionMeasurements/dataCollectionMeasurement") == []


# ---------------------------------------------------------------------------
# add_travel_time_section / remove_travel_time_section
# ---------------------------------------------------------------------------


def test_add_travel_time_section_happy_path(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeMeasurementInfraSession(
        working,
        readback={
            "travel_time_section": {
                "no": 3, "name": "Test section", "from_link": "101", "from_pos": 0.0,
                "to_link": "102", "to_pos": 15.0,
            }
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_travel_time_section",
        name="Test section",
        from_link="101",
        from_pos_m=0.0,
        to_link="102",
        to_pos_m=15.0,
    )

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"from_link": "101", "from_pos": 0.0, "to_link": "102", "to_pos": 15.0},
        "readback_value": {"from_link": "101", "from_pos": 0.0, "to_link": "102", "to_pos": 15.0},
    }
    assert envelope["artifacts"] == {"no": 3, "name": "Test section"}
    assert session.calls == [
        {
            "op": "add_travel_time_section",
            "name": "Test section",
            "from_link": 101,
            "from_pos_m": 0.0,
            "to_link": 102,
            "to_pos_m": 15.0,
        }
    ]


def test_add_travel_time_section_bogus_to_link_surfaces_as_tool_execution_error(tmp_path: Path):
    """to_link is NOT covered by the object_not_found preflight (this
    unit's own documented scope cut, tools_edit.py's module comment) -- a
    bogus to_link fails closed as a tool_execution_error instead of a
    richer, difflib-suggested refusal."""
    working = _write_working_inpx(tmp_path)
    session = FakeMeasurementInfraSession(
        working,
        raise_on={
            "add_travel_time_section": RuntimeError(
                "Could not add a travel time section from link 101@0.0 to link 999@15.0: "
                "Link 999 does not exist in the loaded VISSIM network."
            )
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch(
        "add_travel_time_section", name="", from_link="101", from_pos_m=0.0, to_link="999", to_pos_m=15.0
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "999" in envelope["message"]


def test_remove_travel_time_section_happy_path(tmp_path: Path):
    working = _write_working_inpx(
        tmp_path,
        extra=(
            '<vehicleTravelTimeMeasurements><vehicleTravelTimeMeasurement no="3" name="">'
            '<start link="101" pos="0"/><end link="102" pos="15"/>'
            "</vehicleTravelTimeMeasurement></vehicleTravelTimeMeasurements>"
        ),
    )
    session = FakeMeasurementInfraSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_travel_time_section", no="3")

    assert envelope["status"] == "ok"
    assert envelope["verification"]["readback_value"] == {"removed_no": None}
    root = ET.parse(working).getroot()
    assert root.findall(".//vehicleTravelTimeMeasurements/vehicleTravelTimeMeasurement") == []


# ---------------------------------------------------------------------------
# No active session -> structured error for every one of this unit's 8 tools
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name,params",
    [
        ("add_queue_counter", {"link_no": "101", "pos_m": 10.0}),
        ("remove_queue_counter", {"no": "1"}),
        ("add_data_collection_point", {"link_no": "101", "lane_index": 1, "pos_m": 10.0}),
        ("remove_data_collection_point", {"no": "1"}),
        ("add_data_collection_measurement", {"point_nos": ["1"]}),
        ("remove_data_collection_measurement", {"no": "1"}),
        (
            "add_travel_time_section",
            {"from_link": "101", "from_pos_m": 0.0, "to_link": "102", "to_pos_m": 10.0},
        ),
        ("remove_travel_time_section", {"no": "1"}),
    ],
)
def test_no_active_session_yields_structured_error_never_a_crash(tool_name, params):
    envelope = _dispatch(tool_name, **params)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]

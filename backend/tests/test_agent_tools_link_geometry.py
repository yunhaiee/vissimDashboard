"""Tests for Q6 unit ①'s tools_edit.py link-geometry verbs:
``set_link_lane_count`` / ``set_link_geometry`` / ``set_link_point_radius``,
dispatched through the REAL ``toolkit.dispatch`` (so pydantic validation and
the envelope machinery are exercised exactly like production) against a
hand-rolled ``FakeLinkGeometrySession`` -- no real Vissim COM anywhere in
this file.

House shape = ``test_agent_tools_add_links_connectors.py`` (Q4 unit ⑤'s own
file): the fake owns a real on-disk ``working_inpx``, because every refusal
in this unit is computed from that file rather than from a rigged in-memory
value -- the lane-token reference-integrity preflight
(services.inpx_structure.find_lane_token_references), the
anchored-object-truncation preflight, the connector-target check, the
point-count check, and ``set_link_point_radius``'s own post-persist reparse
readback all parse it.

Live truth for every behaviour these tests encode comes from
``backend/tools/probe_link_geometry.py``.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

import services.agent.tools_edit as tools_edit  # noqa: F401 - import registers tools
from services.agent.edit_session import clear_active_edit_session, set_active_edit_session
from services.agent.toolkit import default_registry, dispatch

#: A 200 m two-lane link (101) carrying a signal head on lane 2 at 180 m and
#: a data-collection point on lane 1 at 150 m; a connector (301) running from
#: link 101 at pos 190 m to link 103 at pos 0; a clean three-lane, three-point
#: link (102) with nothing on it; and link 104, which is the ONLY link here
#: with nothing anchored on it at all (103 carries the connector's own
#: to-endpoint, which is exactly why it is not the "nothing anchored" fixture).
#: Every position is chosen so a specific refusal can be triggered AND its
#: complement can be shown not to fire.
_NETWORK_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<network>
<links>
<link no="101" name="Approach">
<geometry><linkPolyPts>
<linkPolyPoint x="0" y="0" zOffset="0" radiusSrc="CALCULATED" radiusEffect="BRAKE"/>
<linkPolyPoint x="200" y="0" zOffset="0" radiusSrc="CALCULATED" radiusEffect="BRAKE"/>
</linkPolyPts></geometry>
<lanes><lane width="3.5"/><lane width="3.25"/></lanes>
</link>
<link no="102" name="Clean three-point">
<geometry><linkPolyPts>
<linkPolyPoint x="0" y="100" zOffset="0" radiusSrc="CALCULATED" radiusEffect="BRAKE"/>
<linkPolyPoint x="50" y="110" zOffset="0" radiusSrc="CALCULATED" radiusEffect="BRAKE" rad="130.5"/>
<linkPolyPoint x="100" y="100" zOffset="0" radiusSrc="CALCULATED" radiusEffect="BRAKE"/>
</linkPolyPts></geometry>
<lanes><lane width="3.5"/><lane width="3.5"/><lane width="3.5"/></lanes>
</link>
<link no="103" name="Connector destination">
<geometry><linkPolyPts>
<linkPolyPoint x="0" y="200" zOffset="0" radiusSrc="CALCULATED" radiusEffect="BRAKE"/>
<linkPolyPoint x="80" y="200" zOffset="0" radiusSrc="CALCULATED" radiusEffect="BRAKE"/>
</linkPolyPts></geometry>
<lanes><lane width="3.5"/></lanes>
</link>
<link no="104" name="Bare (nothing anchored at all)">
<geometry><linkPolyPts>
<linkPolyPoint x="0" y="300" zOffset="0" radiusSrc="CALCULATED" radiusEffect="BRAKE"/>
<linkPolyPoint x="80" y="300" zOffset="0" radiusSrc="CALCULATED" radiusEffect="BRAKE"/>
</linkPolyPts></geometry>
<lanes><lane width="3.5"/></lanes>
</link>
<link no="301" name="Turn">
<fromLinkEndPt link="101" pos="190"/>
<geometry><linkPolyPts>
<linkPolyPoint x="200" y="0" zOffset="0" radiusSrc="CALCULATED" radiusEffect="BRAKE"/>
<linkPolyPoint x="210" y="10" zOffset="0" radiusSrc="CALCULATED" radiusEffect="BRAKE"/>
</linkPolyPts></geometry>
<lanes><lane><fromLanes><objectRef key="101 1"/></fromLanes><toLanes><objectRef key="103 1"/></toLanes></lane></lanes>
<toLinkEndPt link="103" pos="0"/>
</link>
</links>
<signalHeads><signalHead no="9" name="SH" lane="101 2" pos="180" sg="1 1"/></signalHeads>
<dataCollectionPoints><dataCollectionPoint no="7" name="DCP" lane="101 1" pos="150"/></dataCollectionPoints>
</network>
"""


def _write_working_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "working.inpx"
    path.write_text(_NETWORK_TEMPLATE, encoding="utf-8")
    return path


def _apply_lane_count(path: Path, link_no: str, lane_count: int, width: float) -> None:
    """Simulate a real COM AddLane/RemoveLane + persist(): mutate the on-disk
    working copy so a subsequent reparse sees the genuinely-changed state."""
    tree = ET.parse(path)
    root = tree.getroot()
    for link in root.findall(".//links/link"):
        if link.get("no") != str(link_no):
            continue
        lanes = link.find("lanes")
        current = lanes.findall("lane")
        while len(lanes.findall("lane")) > lane_count:
            lanes.remove(lanes.findall("lane")[-1])
        while len(lanes.findall("lane")) < lane_count:
            ET.SubElement(lanes, "lane", {"width": f"{width:g}"})
        del current
    tree.write(path, encoding="UTF-8", xml_declaration=True)


def _apply_points(path: Path, link_no: str, points: list[tuple[float, float]]) -> None:
    tree = ET.parse(path)
    root = tree.getroot()
    for link in root.findall(".//links/link"):
        if link.get("no") != str(link_no):
            continue
        for element, (x, y) in zip(link.findall("geometry/linkPolyPts/linkPolyPoint"), points):
            element.set("x", repr(x))
            element.set("y", repr(y))
    tree.write(path, encoding="UTF-8", xml_declaration=True)


def _apply_radius(path: Path, link_no: str, point_index: int, radius_m: float | None) -> None:
    tree = ET.parse(path)
    root = tree.getroot()
    for link in root.findall(".//links/link"):
        if link.get("no") != str(link_no):
            continue
        element = link.findall("geometry/linkPolyPts/linkPolyPoint")[point_index - 1]
        if radius_m is None:
            element.set("radiusSrc", "CALCULATED")
        else:
            element.set("radiusSrc", "USERINPUT")
            element.set("rad", f"{radius_m:g}")
    tree.write(path, encoding="UTF-8", xml_declaration=True)


def _link_geometry_from_file(path: Path, link_no: str) -> dict[str, Any]:
    """What a real ``get_link_geometry`` would return for the CURRENT on-disk
    state -- so the fake's readback is derived from the file it just mutated,
    never from a value handed to it (a rigged readback would make every
    verification assertion in this file a tautology)."""
    root = ET.parse(path).getroot()
    link = next(el for el in root.findall(".//links/link") if el.get("no") == str(link_no))
    points = []
    for index, element in enumerate(link.findall("geometry/linkPolyPts/linkPolyPoint"), start=1):
        rad = element.get("rad")
        points.append(
            {
                "index": index,
                "x": float(element.get("x", 0)),
                "y": float(element.get("y", 0)),
                "z": float(element.get("zOffset", 0) or 0),
                "radius": float(rad) if rad else None,
                "radius_src": element.get("radiusSrc", ""),
                "radius_effect": element.get("radiusEffect", ""),
            }
        )
    widths = [float(l.get("width", 3.5) or 3.5) for l in link.findall("lanes/lane")]
    length = sum(
        ((points[i]["x"] - points[i - 1]["x"]) ** 2 + (points[i]["y"] - points[i - 1]["y"]) ** 2) ** 0.5
        for i in range(1, len(points))
    )
    return {
        "no": int(link_no),
        "name": link.get("name", ""),
        "is_connector": link.find("fromLinkEndPt") is not None,
        "lanes": len(widths),
        "lane_widths": widths,
        "length": length,
        "point_count": len(points),
        "points": points,
    }


class FakeLinkGeometrySession:
    """Duck-types VissimEditSession's link-geometry surface: records every
    call, MUTATES the real on-disk working copy (so the tools' own reparse
    checks see genuinely-changed state), and derives its readback from that
    file rather than from a rigged constant."""

    def __init__(self, working_inpx: Path, raise_on: dict[str, Exception] | None = None) -> None:
        self.working_inpx = working_inpx
        self.calls: list[dict[str, Any]] = []
        self._raise_on = raise_on or {}
        self.persist_calls = 0

    def _record(self, op: str, **kwargs: Any) -> None:
        self.calls.append({"op": op, **kwargs})
        if op in self._raise_on:
            raise self._raise_on[op]

    def set_link_lane_count(
        self, link_no: int, lane_count: int, lane_width_m: float | None = None
    ) -> dict[str, Any]:
        self._record(
            "set_link_lane_count", link_no=link_no, lane_count=lane_count, lane_width_m=lane_width_m
        )
        current = _link_geometry_from_file(self.working_inpx, str(link_no))
        width = lane_width_m if lane_width_m is not None else current["lane_widths"][-1]
        _apply_lane_count(self.working_inpx, str(link_no), lane_count, width)
        result = _link_geometry_from_file(self.working_inpx, str(link_no))
        # The measured centreline recompute: every point moves sideways by
        # half the added/removed width. Reproduced here so the tool's own
        # reported shift is a real derivation, not a constant.
        shift = (lane_count - current["lanes"]) * width / 2.0
        for point in result["points"]:
            point["y"] += shift
        return result

    def set_link_geometry(
        self, link_no: int, points_xy: list[tuple[float, float]]
    ) -> dict[str, Any]:
        self._record("set_link_geometry", link_no=link_no, points_xy=points_xy)
        _apply_points(self.working_inpx, str(link_no), points_xy)
        return _link_geometry_from_file(self.working_inpx, str(link_no))

    def set_link_point_radius(
        self, link_no: int, point_index: int, radius_m: float | None
    ) -> dict[str, Any]:
        self._record(
            "set_link_point_radius", link_no=link_no, point_index=point_index, radius_m=radius_m
        )
        _apply_radius(self.working_inpx, str(link_no), point_index, radius_m)
        return _link_geometry_from_file(self.working_inpx, str(link_no))

    def persist(self) -> None:
        self.persist_calls += 1


@pytest.fixture(autouse=True)
def _reset_active_edit_session():
    clear_active_edit_session()
    yield
    clear_active_edit_session()


def _dispatch(tool_name: str, **params: Any) -> dict[str, Any]:
    return dispatch(tool_name, params, registry=default_registry)


# ---------------------------------------------------------------------------
# set_link_lane_count
# ---------------------------------------------------------------------------


def test_set_link_lane_count_widens_and_reports_the_measured_centerline_shift(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeLinkGeometrySession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_link_lane_count", link_id="104", lane_count=3)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 3, "readback_value": 3}
    assert session.persist_calls == 1
    assert session.calls == [
        {"op": "set_link_lane_count", "link_no": 104, "lane_count": 3, "lane_width_m": None}
    ]
    # Two 3.5 m lanes added -> the polyline moves 3.5 m sideways.
    assert envelope["artifacts"]["centerline_shift_m"] == pytest.approx(3.5)
    assert "widened" in envelope["message"]
    assert "moved 3.500 m sideways" in envelope["message"]


def test_set_link_lane_count_narrows_a_link_with_nothing_on_the_removed_lane(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeLinkGeometrySession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_link_lane_count", link_id="102", lane_count=2)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {"written_value": 2, "readback_value": 2}
    assert "narrowed" in envelope["message"]


def test_set_link_lane_count_refuses_when_the_removed_lane_still_carries_a_signal_head(tmp_path: Path):
    """The measured cascade-delete (probe Part C): Vissim's RemoveLane simply
    deletes the head, with no error and a file that still loads. Link 101's
    lane 2 carries signal head 9, so shrinking to 1 lane must refuse and NAME
    it -- nothing is written."""
    working = _write_working_inpx(tmp_path)
    session = FakeLinkGeometrySession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_link_lane_count", link_id="101", lane_count=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "SILENTLY DELETE" in envelope["message"]
    assert "signal head" in envelope["message"]
    assert session.calls == []
    assert session.persist_calls == 0


def test_set_link_lane_count_refusal_also_covers_a_data_collection_point(tmp_path: Path):
    """The lane-token registry gap this unit closed: a data-collection point
    anchors on ``lane="101 1"`` and was NOT a registered reference site
    before, so a lane removal underneath it went unchallenged even though the
    probe watched exactly that object get destroyed."""
    working = _write_working_inpx(tmp_path)
    # Remove the signal head so the DCP is the ONLY thing that can refuse --
    # otherwise this test would pass on the head alone.
    tree = ET.parse(working)
    root = tree.getroot()
    root.remove(root.find("signalHeads"))
    # Move the DCP onto lane 2 (the lane a shrink to 1 would delete).
    root.find("dataCollectionPoints/dataCollectionPoint").set("lane", "101 2")
    tree.write(working, encoding="UTF-8", xml_declaration=True)

    session = FakeLinkGeometrySession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_link_lane_count", link_id="101", lane_count=1)

    assert envelope["status"] == "error"
    assert "data collection point" in envelope["message"]
    assert session.calls == []


def test_set_link_lane_count_growing_never_triggers_the_reference_check(tmp_path: Path):
    """Growth cannot dangle anything -- nothing can reference a lane index
    that does not exist yet -- so link 101's referenced lane 2 must not block
    a widen."""
    working = _write_working_inpx(tmp_path)
    session = FakeLinkGeometrySession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_link_lane_count", link_id="101", lane_count=4)

    assert envelope["status"] == "ok"
    assert envelope["verification"]["readback_value"] == 4


def test_set_link_lane_count_refuses_a_connector_target(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeLinkGeometrySession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_link_lane_count", link_id="301", lane_count=2)

    assert envelope["status"] == "error"
    assert "is a CONNECTOR" in envelope["message"]
    assert session.calls == []


def test_set_link_lane_count_refuses_a_missing_link(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    set_active_edit_session(FakeLinkGeometrySession(working))

    envelope = _dispatch("set_link_lane_count", link_id="999", lane_count=2)

    assert envelope["status"] == "error"
    assert "not found" in envelope["message"]


def test_set_link_lane_count_rejects_zero_lanes_at_the_param_layer():
    envelope = _dispatch("set_link_lane_count", link_id="101", lane_count=0)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_set_link_lane_count_no_active_session_yields_structured_error():
    envelope = _dispatch("set_link_lane_count", link_id="101", lane_count=2)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]


# ---------------------------------------------------------------------------
# set_link_geometry
# ---------------------------------------------------------------------------


def test_set_link_geometry_happy_path_when_nothing_is_anchored(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeLinkGeometrySession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_link_geometry", link_id="104", points_xy=[[0.0, 300.0], [40.0, 300.0]])

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": [[0.0, 300.0], [40.0, 300.0]],
        "readback_value": [[0.0, 300.0], [40.0, 300.0]],
    }
    assert envelope["artifacts"]["length_m"] == pytest.approx(40.0)
    assert "Nothing is anchored on this link" in envelope["message"]
    assert session.persist_calls == 1


def test_set_link_geometry_allows_a_shortening_that_still_clears_every_anchor(tmp_path: Path):
    """Link 101's furthest anchor is connector 301 at 190 m, so 195 m is
    legal and 185 m is not -- the pair is what makes the refusal below a
    real boundary rather than a blanket ban on shortening."""
    working = _write_working_inpx(tmp_path)
    session = FakeLinkGeometrySession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_link_geometry", link_id="101", points_xy=[[0.0, 0.0], [195.0, 0.0]])

    assert envelope["status"] == "ok"
    assert "furthest at 190.000 m" in envelope["message"]


def test_set_link_geometry_refuses_a_shortening_that_would_relocate_anchored_objects(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeLinkGeometrySession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_link_geometry", link_id="101", points_xy=[[0.0, 0.0], [100.0, 0.0]])

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "SILENTLY relocate each one to it" in envelope["message"]
    assert "keep it at least 190.000 m" in envelope["message"]
    # Every kind is named, connector endpoints included.
    assert "signal head 9" in envelope["message"]
    assert "data collection point 7" in envelope["message"]
    assert "connector from-endpoint 301" in envelope["message"]
    assert session.calls == []
    assert session.persist_calls == 0


def test_set_link_geometry_refuses_a_different_point_count(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeLinkGeometrySession(working)
    set_active_edit_session(session)

    envelope = _dispatch(
        "set_link_geometry", link_id="104", points_xy=[[0.0, 300.0], [40.0, 300.0], [80.0, 300.0]]
    )

    assert envelope["status"] == "error"
    assert "has 2 polyline point(s), not 3" in envelope["message"]
    assert "cannot add or remove" in envelope["message"]
    assert session.calls == []


def test_set_link_geometry_refuses_a_connector_target(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeLinkGeometrySession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_link_geometry", link_id="301", points_xy=[[200.0, 0.0], [220.0, 20.0]])

    assert envelope["status"] == "error"
    assert "SNAPS a connector" in envelope["message"]
    assert session.calls == []


def test_set_link_geometry_rejects_duplicate_consecutive_points():
    envelope = _dispatch(
        "set_link_geometry", link_id="101", points_xy=[[0.0, 0.0], [0.0, 0.0], [100.0, 0.0]]
    )
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"
    assert "duplicate consecutive points" in envelope["message"]


def test_set_link_geometry_rejects_fewer_than_two_points():
    envelope = _dispatch("set_link_geometry", link_id="101", points_xy=[[0.0, 0.0]])
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_set_link_geometry_verification_survives_the_persistence_tolerance(tmp_path: Path):
    """The written_value this tool reports is the SAME object guardrails
    compares against the full-precision points reparsed from the saved file
    (1e-9 tolerance), so its rounding must stay far finer than that -- a
    1e-6 rounding, which every ``pos``-shaped verification in tools_edit uses,
    would refuse this correct write from the persistence side."""
    working = _write_working_inpx(tmp_path)
    session = FakeLinkGeometrySession(working)
    set_active_edit_session(session)
    x = 12.345678901234567

    envelope = _dispatch("set_link_geometry", link_id="104", points_xy=[[0.0, 300.0], [x, 300.0]])

    written = envelope["verification"]["written_value"][1][0]
    assert abs(written - x) < 1e-9
    assert envelope["verification"]["written_value"] == envelope["verification"]["readback_value"]


# ---------------------------------------------------------------------------
# set_link_point_radius
# ---------------------------------------------------------------------------


def test_set_link_point_radius_overrides_and_verifies_against_the_persisted_file(tmp_path: Path):
    """The shadow model carries no per-point curvature, so this tool's own
    verification is a reparse of the JUST-PERSISTED working copy. The assert
    below therefore proves the value reached DISK, not merely COM."""
    working = _write_working_inpx(tmp_path)
    session = FakeLinkGeometrySession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_link_point_radius", link_id="102", point_index=2, radius_m=250.0)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"radius_src": "USERINPUT", "radius_m": 250.0},
        "readback_value": {"radius_src": "USERINPUT", "radius_m": 250.0},
    }
    saved = ET.parse(working).getroot()
    point = saved.findall(".//links/link[@no='102']/geometry/linkPolyPts/linkPolyPoint")[1]
    assert point.get("radiusSrc") == "USERINPUT"
    assert float(point.get("rad")) == 250.0
    assert session.persist_calls == 1


def test_set_link_point_radius_restores_the_calculated_source_when_omitted(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeLinkGeometrySession(working)
    set_active_edit_session(session)
    _dispatch("set_link_point_radius", link_id="102", point_index=2, radius_m=250.0)

    envelope = _dispatch("set_link_point_radius", link_id="102", point_index=2)

    assert envelope["status"] == "ok"
    assert envelope["verification"]["written_value"] == {"radius_src": "CALCULATED", "radius_m": None}
    assert envelope["verification"]["readback_value"]["radius_src"] == "CALCULATED"
    assert "handed back to Vissim" in envelope["message"]


def test_set_link_point_radius_refuses_the_last_point(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeLinkGeometrySession(working)
    set_active_edit_session(session)

    envelope = _dispatch("set_link_point_radius", link_id="102", point_index=3, radius_m=250.0)

    assert envelope["status"] == "error"
    assert "INTERIOR points are 2..2" in envelope["message"]
    assert session.calls == []


def test_set_link_point_radius_rejects_point_index_one_at_the_param_layer():
    """A polyline's FIRST point is refused by the params model itself
    (ge=2), before any session is needed."""
    envelope = _dispatch("set_link_point_radius", link_id="102", point_index=1, radius_m=250.0)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_set_link_point_radius_rejects_a_non_positive_radius():
    envelope = _dispatch("set_link_point_radius", link_id="102", point_index=2, radius_m=0.0)
    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_set_link_point_radius_leaves_the_geometry_untouched(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    before = ET.tostring(
        ET.parse(working).getroot().find(".//links/link[@no='102']/geometry")
    )
    session = FakeLinkGeometrySession(working)
    set_active_edit_session(session)

    _dispatch("set_link_point_radius", link_id="102", point_index=2, radius_m=250.0)

    after_root = ET.parse(working).getroot()
    coords_before = [
        (e.get("x"), e.get("y"))
        for e in ET.fromstring(before).findall("linkPolyPts/linkPolyPoint")
    ]
    coords_after = [
        (e.get("x"), e.get("y"))
        for e in after_root.findall(".//links/link[@no='102']/geometry/linkPolyPts/linkPolyPoint")
    ]
    assert coords_before == coords_after


# ---------------------------------------------------------------------------
# The guardrail exemption tables these verbs depend on. Every entry below was
# derived from a MEASURED before/after pair and each one's counterfactual was
# RUN through the real engine (backend/tools/link_geometry_live_check.py
# Phases A/B: popping an entry turns a correct call into
# write_verification_failed with named evidence rows). These tests pin the
# tables themselves so a later refactor cannot quietly widen or drop one.
# ---------------------------------------------------------------------------


def _link(link_id: str, *, lanes: int = 2, points: list[list[float]] | None = None) -> dict[str, Any]:
    points = points or [[0.0, 0.0], [100.0, 0.0]]
    return {
        "id": link_id, "name": "", "points": points, "lanes": lanes,
        "lane_widths": [3.5] * lanes, "width_total": 3.5 * lanes, "length": 100.0,
        "is_connector": False, "from_link": None, "to_link": None,
        "from_pos": None, "to_pos": None,
    }


def _lane(link_id: str, index: int) -> dict[str, Any]:
    return {"id": f"{link_id}.{index}", "link_id": link_id, "lane_index": index,
            "width": 3.5, "blocked_vehicle_classes": []}


def _conflict(no: str) -> dict[str, Any]:
    return {"no": no, "name": f"CA {no}", "status": "PASSIVE", "link_a": "1", "link_b": "2"}


def test_lane_count_exemptions_are_exactly_the_measured_classes():
    from services.agent import guardrails

    assert guardrails._ALLOWS_NEW_OBJECTS_IN["set_link_lane_count"] == frozenset(
        {"link_lanes", "conflict_areas"}
    )
    assert guardrails._ALLOWS_REMOVED_OBJECTS_IN["set_link_lane_count"] == frozenset({"link_lanes"})
    # A reshape adds no links and no lanes -- only Vissim's own recomputed
    # conflict areas (live-caught: lengthening Beijing link 1 by 10 m created
    # three of them).
    assert guardrails._ALLOWS_NEW_OBJECTS_IN["set_link_geometry"] == frozenset({"conflict_areas"})
    # ...and NO removal-side entry for either geometry verb: Vissim SILENTLY
    # DROPS a dangling conflict area (Q4 unit ③), which is the one thing that
    # must stay loud evidence rather than an assumed exemption.
    assert "set_link_geometry" not in guardrails._ALLOWS_REMOVED_OBJECTS_IN


def test_conflict_area_exemption_suppresses_added_rows_but_never_removed_ones():
    """The asymmetry that makes the exemption safe. Vissim both CREATES
    conflict areas on a junction reshape (measured: 905/906/907 appeared when
    link 1 grew 10 m) and SILENTLY DROPS dangling ones on recompute (Q4 unit
    ③). Opting the class into ``_ALLOWS_NEW_OBJECTS_IN`` must therefore excuse
    exactly the first and none of the second."""
    from services.agent.guardrails import _nothing_else_moved_diff

    action = {"tool": "set_link_geometry", "params": {"link_id": "1", "points_xy": [[0, 0], [110, 0]]}}
    before = {"links": [_link("1")], "conflict_areas": [_conflict("900")]}
    added = {
        "links": [_link("1", points=[[0.0, 0.0], [110.0, 0.0]])],
        "conflict_areas": [_conflict("900"), _conflict("905")],
    }
    removed = {"links": [_link("1", points=[[0.0, 0.0], [110.0, 0.0]])], "conflict_areas": []}

    assert _nothing_else_moved_diff(action, before, added) == []

    evidence = _nothing_else_moved_diff(action, before, removed)
    assert [(e["object_class"], e["object_id"], e["change"]) for e in evidence] == [
        ("conflict_areas", "900", "removed")
    ]


def test_lane_count_diff_exempts_the_four_fields_a_lane_change_moves_together():
    """Measured on two networks and in both directions: a lane-count change
    moves ``lanes``, ``lane_widths``, ``width_total`` AND ``points`` on the
    target link in one step (the last because Vissim re-centres the polyline).
    Exempting ``lanes`` alone would refuse every correct call."""
    from services.agent.guardrails import _nothing_else_moved_diff

    action = {"tool": "set_link_lane_count", "params": {"link_id": "1", "lane_count": 3}}
    before = {"links": [_link("1", lanes=2)], "link_lanes": [_lane("1", 1), _lane("1", 2)]}
    after = {
        "links": [_link("1", lanes=3, points=[[0.0, 1.75], [100.0, 1.75]])],
        "link_lanes": [_lane("1", 1), _lane("1", 2), _lane("1", 3)],
    }

    assert _nothing_else_moved_diff(action, before, after) == []


def test_lane_count_diff_still_reports_a_change_to_a_different_link():
    """The exemption is per-object, not per-class: a second link that moved is
    still evidence."""
    from services.agent.guardrails import _nothing_else_moved_diff

    action = {"tool": "set_link_lane_count", "params": {"link_id": "1", "lane_count": 3}}
    before = {"links": [_link("1", lanes=2), _link("2", lanes=1)], "link_lanes": []}
    after = {
        "links": [_link("1", lanes=3), _link("2", lanes=2)],
        "link_lanes": [],
    }

    evidence = _nothing_else_moved_diff(action, before, after)
    assert {e["object_id"] for e in evidence} == {"2"}


def test_set_link_point_radius_has_no_write_target_spec_entry_by_design():
    """A documented blind spot, pinned so it cannot become an accidental one:
    the shadow model carries no per-point curvature, so guardrails cannot see
    a radius write in either direction. The tool verifies itself by reparsing
    the persisted file instead (see its own test above). If a later unit
    teaches the parser about per-point radius, this assertion is the reminder
    to add the entry."""
    from services.agent import guardrails

    assert "set_link_point_radius" not in guardrails._WRITE_TARGET_FIELD_SPEC
    assert "set_link_point_radius" not in guardrails._ALLOWS_NEW_OBJECTS_IN
    assert "set_link_point_radius" not in guardrails._ALLOWS_REMOVED_OBJECTS_IN

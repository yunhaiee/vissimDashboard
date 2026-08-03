"""Tests for services.vissim_com's link-geometry quartet (Q6 unit ①):
``get_link_geometry`` / ``set_link_lane_count`` / ``set_link_geometry`` /
``set_link_point_radius``.

Fake COM stubs stand in for real COM (COM tasks need the real Vissim on this
machine per docs/phase2/00-EXECUTION-GUIDE.md §5 -- this file proves the pure
reader/writer LOGIC; live truth comes from
``backend/tools/probe_link_geometry.py``, whose ten parts measured every
behaviour these stubs imitate against a real Vissim 2026 instance BEFORE this
module was written around them: NumLanes is read-only, AddLane/RemoveLane
work on an existing link, the centreline moves by half a lane width,
RemoveLane silently cascade-deletes lane-anchored objects, the point count
cannot change, Rad is refused while RadiusSrc is CALCULATED, and x/y are raw
metres while Length2D/Width/Rad/ZOffset are display-unit-scaled).

The first test is the echo-bite guard the last two units both had to ship: a
reader that returns its own argument as ``no`` makes every setter's identity
claim unfalsifiable (Q5 unit ②'s ``get_signal_controller`` defect). The
FEET-network tests are the other half of this file's job -- the unit
conversions here are asymmetric in a way no other surface in this codebase
is, so each direction is pinned separately rather than assumed from the
others.
"""
from __future__ import annotations

import math
from typing import Any

import pytest

from services.vissim_com import VissimController, VissimObjectNotFoundError

_M_PER_FT = 0.3048


class _FakeLane:
    def __init__(self, index: int, width: float) -> None:
        self._values: dict[str, Any] = {"Index": index, "Width": width}

    def AttValue(self, attr_id: str) -> Any:
        return self._values[attr_id]

    def SetAttValue(self, attr_id: str, value: Any) -> None:
        self._values[attr_id] = value


class _FakeLaneContainer:
    """``ILaneContainer``: ItemByKey/GetAll plus the two methods that are the
    ONLY way to change a link's lane count (measured -- ``NumLanes`` itself
    raises "the attribute NUMLANES is not editable")."""

    def __init__(self, widths: list[float]) -> None:
        self._lanes = {i: _FakeLane(i, w) for i, w in enumerate(widths, start=1)}
        self.add_calls: list[tuple[int, float]] = []
        self.remove_calls: list[int] = []

    def GetAll(self) -> list[_FakeLane]:
        return [self._lanes[k] for k in sorted(self._lanes)]

    def ItemByKey(self, key: int) -> _FakeLane:
        if key not in self._lanes:
            raise RuntimeError(f"get_ItemByKey failed: lane {key} does not exist")
        return self._lanes[key]

    def AddLane(self, key: int, width: float) -> _FakeLane:
        self.add_calls.append((int(key), float(width)))
        lane = _FakeLane(int(key), float(width))
        self._lanes[int(key)] = lane
        return lane

    def RemoveLane(self, lane: _FakeLane) -> None:
        index = int(lane.AttValue("Index"))
        self.remove_calls.append(index)
        del self._lanes[index]


class _FakePolyPoint:
    """``ILinkPolyPoint``. ``Rad`` is REFUSED while ``RadiusSrc`` is
    CALCULATED -- the live-measured behaviour ("AttValue failed: Object :
    Attribute Radius cannot be changed"), reproduced here so a regression that
    wrote Rad first would fail this file rather than only the live check."""

    def __init__(self, x: float, y: float, *, z: float = 0.0, rad: Any = None,
                 radius_src: str = "CALCULATED", radius_effect: str = "BRAKE") -> None:
        self._values: dict[str, Any] = {
            "X": x, "Y": y, "ZOffset": z, "Rad": rad,
            "RadiusSrc": radius_src, "RadiusEffect": radius_effect,
        }
        self.set_calls: list[tuple[str, Any]] = []

    def AttValue(self, attr_id: str) -> Any:
        if attr_id not in self._values:
            raise RuntimeError(f"LINKPOLYPOINT: Could not find attribute with ID {attr_id.upper()}.")
        return self._values[attr_id]

    def SetAttValue(self, attr_id: str, value: Any) -> None:
        if attr_id == "Rad" and self._values["RadiusSrc"] != "USERINPUT":
            raise RuntimeError("AttValue failed: Object : Attribute Radius cannot be changed.")
        self.set_calls.append((attr_id, value))
        self._values[attr_id] = value


class _FakePolyPointContainer:
    def __init__(self, points: list[_FakePolyPoint]) -> None:
        self._points = list(points)

    def GetAll(self) -> list[_FakePolyPoint]:
        return list(self._points)


class _FakeLink:
    """``ILink``. ``NumLanes``/``Length2D`` are DERIVED here (not stored), so
    a test asserting them cannot pass against a stub that merely remembers
    what it was told -- ``NumLanes`` counts real lanes and ``Length2D`` is
    computed from the real polyline, in the network's DISPLAY unit (measured:
    Camden reads Length2D 200.09 for a 60.99 m polyline)."""

    def __init__(self, no: Any, points: list[_FakePolyPoint], widths: list[float],
                 *, name: str = "", is_conn: int = 0, unit: str = "METERS") -> None:
        self._no = no
        self._name = name
        self._is_conn = is_conn
        self._unit = unit
        self.LinkPolyPts = _FakePolyPointContainer(points)
        self.Lanes = _FakeLaneContainer(widths)
        self.set_calls: list[tuple[str, Any]] = []

    def AttValue(self, attr_id: str) -> Any:
        if attr_id == "No":
            return self._no
        if attr_id == "Name":
            return self._name
        if attr_id == "IsConn":
            return self._is_conn
        if attr_id == "NumLanes":
            return len(self.Lanes.GetAll())
        if attr_id == "Length2D":
            points = self.LinkPolyPts.GetAll()
            metres = sum(
                math.hypot(
                    points[i].AttValue("X") - points[i - 1].AttValue("X"),
                    points[i].AttValue("Y") - points[i - 1].AttValue("Y"),
                )
                for i in range(1, len(points))
            )
            return metres / _M_PER_FT if self._unit == "FEET" else metres
        raise RuntimeError(f"LINK: Could not find attribute with ID {attr_id.upper()}.")

    def SetAttValue(self, attr_id: str, value: Any) -> None:
        # Measured: a link's own lane count is NOT settable.
        if attr_id == "NumLanes":
            raise RuntimeError("put_AttValue failed - the attribute NUMLANES is not editable")
        self.set_calls.append((attr_id, value))


class _FakeCollection:
    def __init__(self, items: dict[int, Any]) -> None:
        self._items = dict(items)

    def ItemByKey(self, key: int) -> Any:
        if key not in self._items:
            raise RuntimeError(f"get_ItemByKey failed: object {key} does not exist")
        return self._items[key]

    def GetAll(self) -> list[Any]:
        return [self._items[k] for k in sorted(self._items)]


class _FakeNetPara:
    def __init__(self, unit: str) -> None:
        self._unit = unit

    def AttValue(self, attr_id: str) -> Any:
        if attr_id == "UnitLenShort":
            return self._unit
        raise RuntimeError(attr_id)


class _FakeNet:
    def __init__(self, links: _FakeCollection, unit: str) -> None:
        self.Links = links
        self.NetPara = _FakeNetPara(unit)


class _FakeVissim:
    def __init__(self, net: _FakeNet) -> None:
        self.Net = net


def _controller_with(links: dict[int, _FakeLink], unit: str = "METERS") -> VissimController:
    controller = VissimController()
    controller.vissim = _FakeVissim(_FakeNet(_FakeCollection(links), unit))
    return controller


def _straight_link(no: Any, *, length_m: float = 100.0, lanes: int = 2, unit: str = "METERS",
                   name: str = "") -> _FakeLink:
    points = [_FakePolyPoint(0.0, 0.0), _FakePolyPoint(length_m, 0.0)]
    return _FakeLink(no, points, [3.5] * lanes, name=name, unit=unit)


def _curved_link(no: Any, unit: str = "METERS") -> _FakeLink:
    """Three points with a genuine bend, the middle one carrying a real Rad
    (mirrors Beijing link 17, whose middle point reads 24292.24)."""
    points = [
        _FakePolyPoint(0.0, 0.0),
        _FakePolyPoint(50.0, 10.0, rad=24292.235828170615),
        _FakePolyPoint(100.0, 0.0),
    ]
    return _FakeLink(no, points, [3.5], unit=unit)


# ---------------------------------------------------------------------------
# get_link_geometry -- the reader must READ, not echo
# ---------------------------------------------------------------------------


def test_get_link_geometry_reads_no_back_instead_of_echoing_the_argument():
    """The regression guard (Q5 unit ②'s ``get_signal_controller`` defect). A
    fake whose stored ``No`` disagrees with the key it is filed under cannot
    happen through a real ``ItemByKey``, so this asserts the SOURCE of the
    value: an implementation that echoed ``no`` would return 7 here."""
    controller = _controller_with({7: _straight_link(42)})

    assert controller.get_link_geometry(7)["no"] == 42


def test_get_link_geometry_normalises_a_float_no_to_an_int():
    controller = _controller_with({13: _straight_link(13.0)})

    result = controller.get_link_geometry(13)

    assert result["no"] == 13
    assert isinstance(result["no"], int)


def test_get_link_geometry_returns_the_full_contract_shape():
    controller = _controller_with({3: _curved_link(3)})

    result = controller.get_link_geometry(3)

    assert result["no"] == 3
    assert result["is_connector"] is False
    assert result["lanes"] == 1
    assert result["point_count"] == 3
    assert result["points"][1] == {
        "index": 2,
        "x": 50.0,
        "y": 10.0,
        "z": 0.0,
        "radius": pytest.approx(24292.235828170615),
        "radius_src": "CALCULATED",
        "radius_effect": "BRAKE",
    }
    assert result["points"][0]["radius"] is None


def test_get_link_geometry_raises_structured_not_found_for_a_bogus_number():
    controller = _controller_with({3: _straight_link(3)})

    with pytest.raises(VissimObjectNotFoundError, match="Link 99 does not exist"):
        controller.get_link_geometry(99)


def test_get_link_geometry_keeps_xy_raw_but_converts_length_width_and_radius_on_a_feet_network():
    """The asymmetry that makes this surface unlike every other one in this
    codebase, measured on Toll Plaza Camden.US: a point's COM X/Y equal the
    persisted metres EXACTLY (ratio 1.0), while Length2D, lane Width, Rad and
    ZOffset all read in FEET (ratio 3.280840). A reader that converted x/y
    -- or that failed to convert the rest -- would pass on a METERS network
    and lie on a FEET one."""
    link = _FakeLink(
        1,
        [_FakePolyPoint(0.0, 0.0), _FakePolyPoint(30.48, 0.0, z=6.706 / _M_PER_FT,
                                                  rad=286.802 / _M_PER_FT)],
        [12.0],
        unit="FEET",
    )
    controller = _controller_with({1: link}, unit="FEET")

    result = controller.get_link_geometry(1)

    # x/y: raw metres, untouched.
    assert result["points"][1]["x"] == 30.48
    # length/width/radius/z: converted back to metres.
    assert result["length"] == pytest.approx(30.48)
    assert result["lane_widths"][0] == pytest.approx(12.0 * _M_PER_FT)
    assert result["points"][1]["radius"] == pytest.approx(286.802)
    assert result["points"][1]["z"] == pytest.approx(6.706)


# ---------------------------------------------------------------------------
# set_link_lane_count
# ---------------------------------------------------------------------------


def test_set_link_lane_count_grows_by_calling_add_lane_at_the_next_index():
    controller = _controller_with({4: _straight_link(4, lanes=2)})

    result = controller.set_link_lane_count(4, 4)

    link = controller.vissim.Net.Links.ItemByKey(4)
    assert link.Lanes.add_calls == [(3, 3.5), (4, 3.5)]
    assert link.Lanes.remove_calls == []
    assert result["lanes"] == 4


def test_set_link_lane_count_shrinks_from_the_highest_index_down():
    """Highest-index-first is what makes the operation expressible: a lane has
    no identity besides its ordinal, so removing an interior one would
    silently renumber every lane above it."""
    controller = _controller_with({4: _straight_link(4, lanes=4)})

    result = controller.set_link_lane_count(4, 2)

    link = controller.vissim.Net.Links.ItemByKey(4)
    assert link.Lanes.remove_calls == [4, 3]
    assert link.Lanes.add_calls == []
    assert result["lanes"] == 2


def test_set_link_lane_count_never_writes_the_read_only_numlanes_attribute():
    """Measured: ``SetAttValue("NumLanes", n)`` raises "the attribute NUMLANES
    is not editable" and creates nothing. The stub raises the same thing, so
    an implementation that tried the attribute route would fail here."""
    controller = _controller_with({4: _straight_link(4, lanes=2)})

    controller.set_link_lane_count(4, 3)

    link = controller.vissim.Net.Links.ItemByKey(4)
    assert [attr for attr, _v in link.set_calls] == []


def test_set_link_lane_count_defaults_a_new_lane_to_the_links_own_last_width():
    link = _FakeLink(4, [_FakePolyPoint(0.0, 0.0), _FakePolyPoint(100.0, 0.0)], [3.25, 3.1])
    controller = _controller_with({4: link})

    controller.set_link_lane_count(4, 3)

    assert link.Lanes.add_calls == [(3, 3.1)]


def test_set_link_lane_count_converts_an_explicit_width_to_display_units():
    """``AddLane``'s Width IS display-unit-scaled -- live-proven by passing a
    RAW 3.5 on Camden and getting a persisted ``width="1.0668"`` (3.5 FEET).
    So the caller's metres must be converted, unlike x/y."""
    link = _FakeLink(1, [_FakePolyPoint(0.0, 0.0), _FakePolyPoint(30.0, 0.0)], [12.0], unit="FEET")
    controller = _controller_with({1: link}, unit="FEET")

    controller.set_link_lane_count(1, 2, lane_width_m=3.5)

    assert link.Lanes.add_calls == [(2, pytest.approx(3.5 / _M_PER_FT))]


def test_set_link_lane_count_is_a_no_op_when_the_count_already_matches():
    controller = _controller_with({4: _straight_link(4, lanes=3)})

    result = controller.set_link_lane_count(4, 3)

    link = controller.vissim.Net.Links.ItemByKey(4)
    assert link.Lanes.add_calls == [] and link.Lanes.remove_calls == []
    assert result["lanes"] == 3


def test_set_link_lane_count_raises_structured_not_found_for_a_bogus_link():
    controller = _controller_with({4: _straight_link(4)})

    with pytest.raises(VissimObjectNotFoundError, match="Link 99 does not exist"):
        controller.set_link_lane_count(99, 3)


# ---------------------------------------------------------------------------
# set_link_geometry
# ---------------------------------------------------------------------------


def test_set_link_geometry_writes_raw_metres_and_length_follows():
    controller = _controller_with({4: _straight_link(4, length_m=100.0)})

    result = controller.set_link_geometry(4, [(0.0, 0.0), (50.0, 0.0)])

    assert result["points"][1]["x"] == 50.0
    assert result["length"] == pytest.approx(50.0)


def test_set_link_geometry_does_not_convert_xy_on_a_feet_network():
    """x/y are the ONE length-shaped COM value never display-unit-scaled
    (measured in the WRITE direction on Camden, closing the half Q4 unit ⑤
    could only infer). A conversion here would silently move the link by a
    factor of 3.28."""
    link = _FakeLink(1, [_FakePolyPoint(0.0, 0.0), _FakePolyPoint(30.0, 0.0)], [12.0], unit="FEET")
    controller = _controller_with({1: link}, unit="FEET")

    controller.set_link_geometry(1, [(0.0, 0.0), (100.0, 0.0)])

    assert link.LinkPolyPts.GetAll()[1].AttValue("X") == 100.0


def test_set_link_geometry_refuses_a_different_point_count_and_writes_nothing():
    """COM exposes no way to add or remove a polyline point at all
    (ILinkPolyPointContainer has no Add/Remove; a link has no writable WKT
    attribute) -- so a mismatched count is refused rather than applied to a
    prefix."""
    link = _straight_link(4)
    controller = _controller_with({4: link})

    with pytest.raises(ValueError, match="cannot add or remove"):
        controller.set_link_geometry(4, [(0.0, 0.0), (50.0, 0.0), (100.0, 0.0)])

    assert link.LinkPolyPts.GetAll()[1].AttValue("X") == 100.0
    assert link.LinkPolyPts.GetAll()[0].set_calls == []


def test_set_link_geometry_raises_structured_not_found_for_a_bogus_link():
    controller = _controller_with({4: _straight_link(4)})

    with pytest.raises(VissimObjectNotFoundError, match="Link 99 does not exist"):
        controller.set_link_geometry(99, [(0.0, 0.0), (1.0, 0.0)])


# ---------------------------------------------------------------------------
# set_link_point_radius
# ---------------------------------------------------------------------------


def test_set_link_point_radius_sets_the_source_before_the_value():
    """The two-step order is not stylistic: Vissim REFUSES the Rad write while
    RadiusSrc is CALCULATED (measured), and the stub raises the same thing --
    so an implementation that wrote Rad first would fail here."""
    link = _curved_link(4)
    controller = _controller_with({4: link})

    controller.set_link_point_radius(4, 2, 250.0)

    point = link.LinkPolyPts.GetAll()[1]
    assert point.set_calls == [("RadiusSrc", "USERINPUT"), ("Rad", 250.0)]


def test_set_link_point_radius_converts_the_radius_to_display_units():
    """``Rad`` reads 940.95 against a persisted 286.802 m on Camden (ratio
    3.280840) -- display-unit-scaled, unlike x/y."""
    points = [_FakePolyPoint(0.0, 0.0), _FakePolyPoint(50.0, 10.0, rad=100.0),
              _FakePolyPoint(100.0, 0.0)]
    link = _FakeLink(1, points, [12.0], unit="FEET")
    controller = _controller_with({1: link}, unit="FEET")

    controller.set_link_point_radius(1, 2, 300.0)

    assert points[1].set_calls[-1] == ("Rad", pytest.approx(300.0 / _M_PER_FT))


def test_set_link_point_radius_restores_the_calculated_source_when_radius_is_none():
    link = _curved_link(4)
    controller = _controller_with({4: link})
    controller.set_link_point_radius(4, 2, 250.0)

    controller.set_link_point_radius(4, 2, None)

    point = link.LinkPolyPts.GetAll()[1]
    assert point.set_calls[-1] == ("RadiusSrc", "CALCULATED")
    assert point.AttValue("RadiusSrc") == "CALCULATED"


def test_set_link_point_radius_refuses_an_end_point():
    """A polyline's first and last point carry no curve radius at all
    (measured: Rad reads None on every endpoint probed)."""
    link = _curved_link(4)
    controller = _controller_with({4: link})

    with pytest.raises(ValueError, match="END point"):
        controller.set_link_point_radius(4, 3, 250.0)

    assert link.LinkPolyPts.GetAll()[2].set_calls == []


def test_set_link_point_radius_raises_structured_not_found_for_a_bogus_point():
    controller = _controller_with({4: _curved_link(4)})

    with pytest.raises(VissimObjectNotFoundError, match="no polyline point 9"):
        controller.set_link_point_radius(4, 9, 250.0)


def test_set_link_point_radius_leaves_the_shape_and_length_alone():
    """Measured: a curvature write changes neither x/y nor Length2D -- which
    is exactly why the constraint layer warns that the drawn shape and the
    modelled radius can now disagree."""
    link = _curved_link(4)
    controller = _controller_with({4: link})
    before = controller.get_link_geometry(4)

    after = controller.set_link_point_radius(4, 2, 250.0)

    assert [(p["x"], p["y"]) for p in after["points"]] == [(p["x"], p["y"]) for p in before["points"]]
    assert after["length"] == before["length"]

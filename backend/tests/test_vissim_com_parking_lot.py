"""Tests for services.vissim_com's parking-lot quartet (Q6 unit ②):
``get_parking_lot`` / ``add_parking_lot`` / ``set_parking_lot_capacity`` /
``remove_parking_lot``.

Fake COM stubs stand in for real COM (COM tasks need the real Vissim on this
machine per docs/phase2/00-EXECUTION-GUIDE.md §5 -- this file proves the pure
reader/writer LOGIC; live truth comes from
``backend/tools/probe_parking_lot.py``, whose ten parts measured every
behaviour these stubs imitate against a real Vissim 2026 instance BEFORE this
module was written around them):

* ``Capacity`` is READ-ONLY and DERIVED as ``floor(Length / LenPerSpc)``
  (Part I: ``SetAttValue("Capacity", 37)`` raises "Attribute Capacity cannot
  be changed"; Length 6->60 takes capacity 1->10; LenPerSpc 6->4 then takes it
  10->15) -- so :class:`_FakeParkingLot` DERIVES ``Capacity`` rather than
  storing it, and a test asserting a capacity cannot pass against a stub that
  merely remembers what it was told.
* ``LenPerSpc`` larger than the CURRENT ``Length`` is SILENTLY CLAMPED to
  Length, with no exception at all (Part I: 1000.0 written against a 60 m lot
  came back 60.0, and the capacity fell 15 -> 1). Reproduced here so a
  regression that wrote the bay size before the length would fail this file
  rather than only the live check.
* ``Pos``/``Length``/``LenPerSpc`` are DISPLAY-unit values in COM while the
  persisted XML stores metres (Part F, Toll Plaza Camden.US: COM/XML ratio
  3.280840 on all three) -- the opposite of a link polyline's raw-metre X/Y,
  so both directions are pinned separately rather than assumed.
* COM's ``Lane`` reads HYPHENATED (``"1-1"``), not the XML's space-separated
  ``"1 1"``.

The first test is the echo-bite guard the last two units both had to ship: a
reader that returns its own argument as ``no`` makes every setter's identity
claim unfalsifiable (Q5 unit ②'s ``get_signal_controller`` defect).
"""
from __future__ import annotations

from typing import Any

import pytest

from services.vissim_com import (
    VissimController,
    VissimObjectNotFoundError,
    VissimParkingLotTypeError,
    _format_available_keys,
)

_M_PER_FT = 0.3048


class _FakeParkingLot:
    """``IParkingLot``. ``Capacity`` is DERIVED from Length/LenPerSpc (never
    stored) and REFUSES writes; ``LenPerSpc`` silently clamps to the current
    ``Length`` -- both measured live."""

    def __init__(
        self,
        no: Any,
        *,
        name: str = "",
        lot_type: str = "REALPARKINGSPACE",
        lane: str | None = "1-1",
        link: str = "1",
        zone: str | None = None,
        pos: float = 0.0,
        length: float = 6.0,
        len_per_space: float = 6.0,
        attractivity: float = 0.0,
        max_park_time_s: float = 99999.0,
        des_speed_distr_no: str = "15",
        stored_capacity: int = 10000,
    ) -> None:
        self._values: dict[str, Any] = {
            "No": no,
            "Name": name,
            "Type": lot_type,
            "Lane": lane,
            "Link": link,
            "Zone": zone,
            "Pos": pos,
            "Length": length,
            "LenPerSpc": len_per_space,
            "Attrac": attractivity,
            "ParkTmMax": max_park_time_s,
            "DesSpeedDistrDef": des_speed_distr_no,
        }
        #: Independently-stored capacity for the non-REALPARKINGSPACE species
        #: (10000 is the corpus ZONECONNECTOR sentinel; 100 is what a live
        #: ABSTRACTPARKINGLOT came back with).
        self._stored_capacity = stored_capacity
        #: Every SetAttValue in call order -- the ORDER is load-bearing (see
        #: the silent-clamp test below), not just the final state.
        self.set_calls: list[tuple[str, Any]] = []

    def AttValue(self, attr_id: str) -> Any:
        if attr_id == "Capacity":
            # TYPE-SCOPED, measured: the derivation holds on 573/573
            # REALPARKINGSPACE corpus lots and is violated by 56/56
            # ZONECONNECTORs (capacity="10000" beside length="3") and by the
            # probe's own ABSTRACTPARKINGLOT (6/6 -> 100). The other species
            # store capacity independently, so this fake does too.
            if self._values["Type"] != "REALPARKINGSPACE":
                return self._stored_capacity
            per_space = float(self._values["LenPerSpc"])
            if per_space <= 0:
                return 0
            return int(float(self._values["Length"]) // per_space)
        if attr_id not in self._values:
            raise RuntimeError(f"PARKINGLOT: Could not find attribute with ID {attr_id.upper()}.")
        return self._values[attr_id]

    def SetAttValue(self, attr_id: str, value: Any) -> None:
        if attr_id == "Capacity":
            raise RuntimeError(
                f"AttValue failed: Object {self._values['No']}: Attribute Capacity cannot be "
                "changed."
            )
        self.set_calls.append((attr_id, value))
        if attr_id == "LenPerSpc":
            # Measured: silently clamped to the CURRENT length, no exception.
            value = min(float(value), float(self._values["Length"]))
        self._values[attr_id] = value


class _FakeParkingLotContainer:
    def __init__(self, lots: dict[int, _FakeParkingLot]) -> None:
        self._lots = dict(lots)
        self.add_calls: list[tuple[int, Any, float]] = []
        self.remove_calls: list[int] = []

    def GetAll(self) -> list[_FakeParkingLot]:
        return [self._lots[k] for k in sorted(self._lots)]

    def ItemByKey(self, key: int) -> _FakeParkingLot:
        if key not in self._lots:
            raise RuntimeError(
                "CCOMParkingLotContainerBase<CParkingLotManager>::get_ItemByKey failed: object "
                f"{key} does not exist"
            )
        return self._lots[key]

    def AddParkingLot(self, key: int, lane: Any, pos: float) -> _FakeParkingLot:
        self.add_calls.append((int(key), lane, float(pos)))
        lot = _FakeParkingLot(
            int(key), lane=f"{lane.link_no}-{lane.index}", link=str(lane.link_no), pos=float(pos)
        )
        self._lots[int(key)] = lot
        return lot

    def RemoveParkingLot(self, lot: _FakeParkingLot) -> None:
        no = int(lot.AttValue("No"))
        self.remove_calls.append(no)
        del self._lots[no]


class _FakeLane:
    def __init__(self, link_no: int, index: int) -> None:
        self.link_no = link_no
        self.index = index

    def AttValue(self, attr_id: str) -> Any:
        if attr_id == "Index":
            return self.index
        raise RuntimeError(attr_id)


class _FakeLaneContainer:
    def __init__(self, link_no: int, count: int) -> None:
        self._lanes = {i: _FakeLane(link_no, i) for i in range(1, count + 1)}

    def GetAll(self) -> list[_FakeLane]:
        return [self._lanes[k] for k in sorted(self._lanes)]

    def ItemByKey(self, key: int) -> _FakeLane:
        if key not in self._lanes:
            raise RuntimeError(f"get_ItemByKey failed: lane {key} does not exist")
        return self._lanes[key]


class _FakeLink:
    def __init__(self, no: int, lane_count: int = 2) -> None:
        self._no = no
        self.Lanes = _FakeLaneContainer(no, lane_count)

    def AttValue(self, attr_id: str) -> Any:
        if attr_id == "No":
            return self._no
        raise RuntimeError(attr_id)


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
    def __init__(self, lots: _FakeParkingLotContainer, links: _FakeCollection, unit: str) -> None:
        self.ParkingLots = lots
        self.Links = links
        self.NetPara = _FakeNetPara(unit)


class _FakeVissim:
    def __init__(self, net: _FakeNet) -> None:
        self.Net = net


def _controller_with(
    lots: dict[int, _FakeParkingLot] | None = None,
    links: dict[int, _FakeLink] | None = None,
    unit: str = "METERS",
) -> VissimController:
    controller = VissimController()
    controller.vissim = _FakeVissim(
        _FakeNet(
            _FakeParkingLotContainer(lots or {}),
            _FakeCollection(links if links is not None else {1: _FakeLink(1)}),
            unit,
        )
    )
    return controller


# ---------------------------------------------------------------------------
# get_parking_lot -- the reader must READ, not echo
# ---------------------------------------------------------------------------


def test_get_parking_lot_reads_no_back_instead_of_echoing_the_argument():
    """The regression guard (Q5 unit ②'s ``get_signal_controller`` defect). A
    fake whose stored ``No`` disagrees with the key it is filed under cannot
    happen through a real ``ItemByKey``, so this asserts the SOURCE of the
    value: an implementation that echoed ``no`` would return "7" here."""
    controller = _controller_with({7: _FakeParkingLot(42)})

    assert controller.get_parking_lot(7)["no"] == "42"


def test_get_parking_lot_normalises_a_float_no_to_an_int_string():
    """``int()`` before ``str()``: a "1.0" would miss every shadow-model id
    keyed "1" (``_collection_nos``'s own guard, and Q5 unit ②'s finding that
    some collections hand ``No`` back as a float)."""
    controller = _controller_with({1: _FakeParkingLot(1.0)})

    assert controller.get_parking_lot(1)["no"] == "1"


def test_get_parking_lot_returns_the_full_contract_shape():
    controller = _controller_with(
        {
            3: _FakeParkingLot(
                3,
                name="North deck",
                lane="12-2",
                link="12",
                pos=25.0,
                length=60.0,
                len_per_space=6.0,
                attractivity=42.5,
                des_speed_distr_no="15",
            )
        }
    )

    result = controller.get_parking_lot(3)

    assert result == {
        "no": "3",
        "name": "North deck",
        "type": "REALPARKINGSPACE",
        "link": "12",
        "lane_index": 2,
        "zone": "",
        "pos": 25.0,
        "length": 60.0,
        "len_per_space": 6.0,
        "capacity": 10,
        "attractivity": 42.5,
        "max_park_time_s": 99999.0,
        "des_speed_distr_no": "15",
    }


def test_get_parking_lot_reports_no_lane_index_for_a_zone_connector():
    """The ZONECONNECTOR/ABSTRACTPARKINGLOT species anchors on a whole LINK
    and genuinely has no lane (measured: ``Lane`` reads None on one) -- the
    reader must say None, never fabricate lane 1."""
    controller = _controller_with(
        {5: _FakeParkingLot(5, lot_type="ZONECONNECTOR", lane=None, link="9", zone="1000005")}
    )

    result = controller.get_parking_lot(5)

    assert result["lane_index"] is None
    assert result["link"] == "9"
    assert result["zone"] == "1000005"
    assert result["type"] == "ZONECONNECTOR"


def test_get_parking_lot_raises_structured_not_found_for_a_bogus_number():
    controller = _controller_with({3: _FakeParkingLot(3), 8: _FakeParkingLot(8)})

    with pytest.raises(VissimObjectNotFoundError) as excinfo:
        controller.get_parking_lot(99)

    message = str(excinfo.value)
    assert "Parking lot 99 does not exist" in message
    assert "3, 8" in message


# ---------------------------------------------------------------------------
# add_parking_lot -- capacity is realised as a LENGTH write
# ---------------------------------------------------------------------------


def test_add_parking_lot_realises_capacity_by_writing_the_length():
    controller = _controller_with(links={4: _FakeLink(4, lane_count=3)})

    result = controller.add_parking_lot(4, 2, 30.0, capacity=12)

    assert result["capacity"] == 12
    assert result["length"] == pytest.approx(72.0)  # 12 x Vissim's own 6.0 default
    assert result["len_per_space"] == pytest.approx(6.0)
    assert result["link"] == "4"
    assert result["lane_index"] == 2
    assert result["pos"] == pytest.approx(30.0)


def test_add_parking_lot_never_attempts_a_direct_capacity_write():
    """Vissim refuses ``SetAttValue("Capacity", n)`` outright (measured). An
    implementation that tried it anyway would raise here -- but the stronger
    claim this pins is that it is never even ATTEMPTED, so the verb cannot
    start depending on a Vissim version that allows it."""
    controller = _controller_with(links={1: _FakeLink(1)})

    controller.add_parking_lot(1, 1, 10.0, capacity=5)

    lot = controller.vissim.Net.ParkingLots.ItemByKey(1)
    assert "Capacity" not in [attr for attr, _ in lot.set_calls]


def test_add_parking_lot_applies_an_explicit_len_per_space():
    controller = _controller_with(links={1: _FakeLink(1)})

    result = controller.add_parking_lot(1, 1, 0.0, capacity=8, len_per_space_m=5.0)

    assert result["len_per_space"] == pytest.approx(5.0)
    assert result["length"] == pytest.approx(40.0)
    assert result["capacity"] == 8


def test_add_parking_lot_writes_length_before_a_GROWING_len_per_space():
    """The silent-clamp guard. ``LenPerSpc`` larger than the CURRENT ``Length``
    is silently clamped with no exception (measured: 1000 requested against a
    60 m lot came back 60.0). A fresh lot is 6 m long, so writing a 9 m bay
    size first would clamp it to 6 and hand back a 1-space lot while reporting
    success. Order, not just the end state, is what this pins."""
    controller = _controller_with(links={1: _FakeLink(1)})

    result = controller.add_parking_lot(1, 1, 0.0, capacity=4, len_per_space_m=9.0)

    lot = controller.vissim.Net.ParkingLots.ItemByKey(1)
    written = [attr for attr, _ in lot.set_calls if attr in ("Length", "LenPerSpc")]
    assert written == ["Length", "LenPerSpc"]
    assert result["len_per_space"] == pytest.approx(9.0)
    assert result["capacity"] == 4


def test_add_parking_lot_orders_on_target_length_not_bay_direction():
    """The ordering rule is TARGET LENGTH vs CURRENT LENGTH, not "did the bay
    grow". Here the bay SHRINKS (6 -> 2) while the target length equals the
    current one (3 x 2 == 6), so Length still goes first -- and that is
    correct: 2 <= 6 means the bay write cannot clamp either way."""
    controller = _controller_with(links={1: _FakeLink(1)})

    result = controller.add_parking_lot(1, 1, 0.0, capacity=3, len_per_space_m=2.0)

    lot = controller.vissim.Net.ParkingLots.ItemByKey(1)
    written = [attr for attr, _ in lot.set_calls if attr in ("Length", "LenPerSpc")]
    assert written == ["Length", "LenPerSpc"]
    assert result["capacity"] == 3
    assert result["length"] == pytest.approx(6.0)
    assert result["len_per_space"] == pytest.approx(2.0)


def test_write_capacity_orders_len_per_space_first_when_the_lot_shrinks():
    """The other branch: a target length SMALLER than the current one writes
    the bay first, while the lot is still long enough to hold it."""
    controller = _controller_with({1: _FakeParkingLot(1, length=120.0, len_per_space=6.0)})

    result = controller.set_parking_lot_capacity(1, 2, len_per_space_m=3.0)

    lot = controller.vissim.Net.ParkingLots.ItemByKey(1)
    written = [attr for attr, _ in lot.set_calls if attr in ("Length", "LenPerSpc")]
    assert written == ["LenPerSpc", "Length"]
    assert result["capacity"] == 2
    assert result["len_per_space"] == pytest.approx(3.0)


def test_write_capacity_recovers_from_the_degenerate_zero_length_state():
    """The hole the target-length rule closes. Vissim's own ``Length = 0``
    landmine can leave a lot at {Length 0, LenPerSpc 1} outside these verbs.
    Asking for 10 spaces of 0.5 m looks like a SHRINKING bay (0.5 < 1), so the
    original bay-direction branch wrote LenPerSpc first, it was clamped to 0.0
    against the zero length, and the lot ended at capacity 0. Ordering on
    target length (5.0 >= 0.0) picks Length first and lands the right pair."""
    controller = _controller_with({1: _FakeParkingLot(1, length=0.0, len_per_space=1.0)})

    result = controller.set_parking_lot_capacity(1, 10, len_per_space_m=0.5)

    lot = controller.vissim.Net.ParkingLots.ItemByKey(1)
    written = [attr for attr, _ in lot.set_calls if attr in ("Length", "LenPerSpc")]
    assert written == ["Length", "LenPerSpc"]
    assert result["capacity"] == 10
    assert result["length"] == pytest.approx(5.0)
    assert result["len_per_space"] == pytest.approx(0.5)


def test_write_capacity_recovers_from_the_degenerate_state_without_a_bay_size():
    """Same degenerate start, no explicit bay size: the lot's own LenPerSpc
    (1.0) is reused and the length follows."""
    controller = _controller_with({1: _FakeParkingLot(1, length=0.0, len_per_space=1.0)})

    result = controller.set_parking_lot_capacity(1, 7)

    assert result["capacity"] == 7
    assert result["length"] == pytest.approx(7.0)


def test_write_capacity_refuses_a_sub_one_capacity_at_the_com_layer():
    """pydantic's ge=1 means production cannot reach this, but a zero target
    length would re-create the degenerate state above, so the COM helper is
    safe standalone rather than trusting its only current caller."""
    controller = _controller_with({1: _FakeParkingLot(1)})

    with pytest.raises(ValueError, match="at least 1 space"):
        controller.set_parking_lot_capacity(1, 0)


# ---------------------------------------------------------------------------
# Species scoping -- capacity is only derived on a REALPARKINGSPACE
# ---------------------------------------------------------------------------


def test_set_parking_lot_capacity_refuses_a_zone_connector_by_name():
    """Measured: 56/56 corpus ZONECONNECTORs store capacity independently
    (10000 beside a 3 m length). Writing a length there changes nothing, so the
    refusal names the actual species instead of letting the readback compare
    refuse opaquely."""
    controller = _controller_with(
        {
            5: _FakeParkingLot(
                5, lot_type="ZONECONNECTOR", lane=None, link="9", length=3.0,
                len_per_space=6.0, stored_capacity=10000,
            )
        }
    )

    with pytest.raises(VissimParkingLotTypeError) as excinfo:
        controller.set_parking_lot_capacity(5, 50)

    message = str(excinfo.value)
    assert "ZONECONNECTOR" in message
    assert "REALPARKINGSPACE" in message
    # toolkit.dispatch truncates a raised exception's text at 300 chars.
    assert len(message) < 300
    # Nothing was written.
    lot = controller.vissim.Net.ParkingLots.ItemByKey(5)
    assert lot.set_calls == []


def test_set_parking_lot_capacity_refuses_an_abstract_parking_lot():
    """The third species, which the 629-lot corpus never shows but this unit's
    own probe created via AddAbstractParkingLot (length=6, lenPerSpc=6,
    capacity=100 -- the derivation would have said 1)."""
    controller = _controller_with(
        {
            2: _FakeParkingLot(
                2, lot_type="ABSTRACTPARKINGLOT", lane=None, link="1", length=6.0,
                len_per_space=6.0, stored_capacity=100,
            )
        }
    )

    with pytest.raises(VissimParkingLotTypeError) as excinfo:
        controller.set_parking_lot_capacity(2, 30)

    assert "ABSTRACTPARKINGLOT" in str(excinfo.value)


def test_set_parking_lot_capacity_still_serves_a_real_parking_space():
    controller = _controller_with({1: _FakeParkingLot(1, length=6.0, len_per_space=6.0)})

    assert controller.set_parking_lot_capacity(1, 12)["capacity"] == 12


def test_get_parking_lot_reports_the_species_so_a_caller_can_tell():
    controller = _controller_with(
        {5: _FakeParkingLot(5, lot_type="ZONECONNECTOR", lane=None, link="9")}
    )

    assert controller.get_parking_lot(5)["type"] == "ZONECONNECTOR"


# ---------------------------------------------------------------------------
# _format_available_keys -- bounded by LENGTH, not only by count
# ---------------------------------------------------------------------------


def test_available_keys_message_stays_under_the_dispatch_cap_with_wide_ids():
    """The count cap alone left 20 EIGHT-DIGIT ids rendering a 313-char
    str(exc), over toolkit.dispatch's 300-char truncation -- cutting the
    message mid-number, which our own ReferenceIntegrityError docstring calls
    strictly worse than a controlled tail."""
    wide = {10000000 + i: _FakeParkingLot(10000000 + i) for i in range(573)}
    controller = _controller_with(wide)

    with pytest.raises(VissimObjectNotFoundError) as excinfo:
        controller.get_parking_lot(99)

    message = str(excinfo.value)
    assert len(message) < 300
    assert "more)" in message


def test_format_available_keys_reports_the_true_overflow_count():
    keys = list(range(10000000, 10000020))

    rendered = _format_available_keys(keys)

    shown = rendered.split(", ... ")[0].split(", ")
    assert len(shown) < len(keys)
    assert f"(+{len(keys) - len(shown)} more)" in rendered


def test_format_available_keys_is_unchanged_for_short_lists():
    assert _format_available_keys([3, 8]) == "3, 8"
    assert _format_available_keys([]) == "none found"


def test_add_parking_lot_allocates_max_plus_one():
    controller = _controller_with({4: _FakeParkingLot(4), 9: _FakeParkingLot(9)}, {1: _FakeLink(1)})

    result = controller.add_parking_lot(1, 1, 0.0, capacity=1)

    assert result["no"] == "10"


def test_add_parking_lot_allocates_1_on_a_network_with_none():
    controller = _controller_with(links={1: _FakeLink(1)})

    assert controller.add_parking_lot(1, 1, 0.0, capacity=1)["no"] == "1"


def test_add_parking_lot_sets_the_name_when_given():
    controller = _controller_with(links={1: _FakeLink(1)})

    assert controller.add_parking_lot(1, 1, 0.0, capacity=1, name="P1")["name"] == "P1"


def test_add_parking_lot_raises_structured_not_found_for_a_bogus_lane():
    controller = _controller_with(links={1: _FakeLink(1, lane_count=2)})

    with pytest.raises(VissimObjectNotFoundError) as excinfo:
        controller.add_parking_lot(1, 7, 0.0, capacity=1)

    assert "Lane 7 does not exist on link 1" in str(excinfo.value)


def test_add_parking_lot_raises_structured_not_found_for_a_bogus_link():
    controller = _controller_with(links={1: _FakeLink(1)})

    with pytest.raises(VissimObjectNotFoundError) as excinfo:
        controller.add_parking_lot(77, 1, 0.0, capacity=1)

    assert "Link 77 does not exist" in str(excinfo.value)


# ---------------------------------------------------------------------------
# set_parking_lot_capacity
# ---------------------------------------------------------------------------


def test_set_parking_lot_capacity_rewrites_the_length_and_reads_capacity_back():
    controller = _controller_with({2: _FakeParkingLot(2, length=6.0, len_per_space=6.0)})

    result = controller.set_parking_lot_capacity(2, 25)

    assert result["capacity"] == 25
    assert result["length"] == pytest.approx(150.0)
    assert result["no"] == "2"


def test_set_parking_lot_capacity_keeps_the_existing_len_per_space_when_omitted():
    controller = _controller_with({2: _FakeParkingLot(2, length=12.0, len_per_space=4.0)})

    result = controller.set_parking_lot_capacity(2, 5)

    assert result["len_per_space"] == pytest.approx(4.0)
    assert result["length"] == pytest.approx(20.0)
    assert result["capacity"] == 5


def test_set_parking_lot_capacity_raises_structured_not_found():
    controller = _controller_with({2: _FakeParkingLot(2)})

    with pytest.raises(VissimObjectNotFoundError):
        controller.set_parking_lot_capacity(404, 3)


# ---------------------------------------------------------------------------
# remove_parking_lot
# ---------------------------------------------------------------------------


def test_remove_parking_lot_calls_com_remove_and_the_lot_is_gone():
    controller = _controller_with({2: _FakeParkingLot(2), 3: _FakeParkingLot(3)})

    controller.remove_parking_lot(2)

    container = controller.vissim.Net.ParkingLots
    assert container.remove_calls == [2]
    assert controller.available_parking_lot_nos() == [3]


def test_remove_parking_lot_raises_structured_not_found_for_a_bogus_number():
    controller = _controller_with({2: _FakeParkingLot(2)})

    with pytest.raises(VissimObjectNotFoundError) as excinfo:
        controller.remove_parking_lot(99)

    assert "Parking lot 99 does not exist" in str(excinfo.value)


# ---------------------------------------------------------------------------
# FEET networks -- pos/length/len_per_space are DISPLAY units in COM
# ---------------------------------------------------------------------------


def test_get_parking_lot_converts_display_feet_back_to_metres():
    """Measured on Toll Plaza Camden.US: COM ``Pos`` 30.0 corresponds to a
    persisted 9.144 m (ratio 3.280840). The reader hands back METRES."""
    controller = _controller_with(
        {1: _FakeParkingLot(1, pos=30.0, length=60.0, len_per_space=6.0)}, unit="FEET"
    )

    result = controller.get_parking_lot(1)

    assert result["pos"] == pytest.approx(9.144)
    assert result["length"] == pytest.approx(60.0 * _M_PER_FT)
    assert result["len_per_space"] == pytest.approx(6.0 * _M_PER_FT)
    # Capacity is a COUNT -- dimensionless, never converted.
    assert result["capacity"] == 10


def test_add_parking_lot_converts_metres_to_display_feet_on_the_way_in():
    controller = _controller_with(links={1: _FakeLink(1)}, unit="FEET")

    result = controller.add_parking_lot(1, 1, 9.144, capacity=4, len_per_space_m=3.048)

    lot = controller.vissim.Net.ParkingLots.ItemByKey(1)
    # Written in DISPLAY units: 9.144 m == 30 ft, 3.048 m == 10 ft, so the
    # length is 4 x 10 == 40 ft.
    assert float(lot.AttValue("Pos")) == pytest.approx(30.0)
    assert float(lot.AttValue("LenPerSpc")) == pytest.approx(10.0)
    assert float(lot.AttValue("Length")) == pytest.approx(40.0)
    # ...and read back out in METRES, with the capacity unaffected by units.
    assert result["pos"] == pytest.approx(9.144)
    assert result["len_per_space"] == pytest.approx(3.048)
    assert result["capacity"] == 4


def test_set_parking_lot_capacity_round_trips_units_on_a_feet_network():
    controller = _controller_with(
        {1: _FakeParkingLot(1, length=20.0, len_per_space=10.0)}, unit="FEET"
    )

    result = controller.set_parking_lot_capacity(1, 6)

    lot = controller.vissim.Net.ParkingLots.ItemByKey(1)
    assert float(lot.AttValue("Length")) == pytest.approx(60.0)  # 6 x 10 ft
    assert result["capacity"] == 6
    assert result["length"] == pytest.approx(60.0 * _M_PER_FT)


def test_available_parking_lot_nos_is_sorted_and_deduplicated():
    controller = _controller_with({9: _FakeParkingLot(9), 2: _FakeParkingLot(2)})

    assert controller.available_parking_lot_nos() == [2, 9]

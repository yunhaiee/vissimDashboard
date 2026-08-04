"""Tests for services.vissim_com's pedestrian quartet (Q6 unit ③):
``get_pedestrian_input`` / ``add_pedestrian_input`` /
``set_pedestrian_input_volume`` / ``remove_pedestrian_input``.

Fake COM stubs stand in for real COM (COM tasks need the real Vissim on this
machine per docs/phase2/00-EXECUTION-GUIDE.md §5 -- this file proves the pure
reader/writer LOGIC; live truth comes from
``backend/tools/probe_pedestrian.py``, whose eighteen parts measured every
behaviour these stubs imitate against a real Vissim 2026 instance BEFORE this
module was written around them).

THE LICENCE QUESTION was settled first and the answer was yes -- this box
creates and simulates Viswalk objects, so these are tests of real write verbs
rather than of a licence-gap refusal. No licence/module attribute exists on
the Vissim COM object at all (eight candidates all raise "Could not find
attribute with ID"), so the verdict is behavioural and lives in the probe.

What the stubs deliberately reproduce, each from a measured live behaviour:

* A FRESH input's defaults are ``Name=''``, ``Volume(1)=0.0``,
  ``PedComp(1)='1'``, ``VolType(1)='STOCHASTIC'``, ``Cont(1)=0`` -- so a
  created-and-left-alone input generates NOTHING, which is why ``volume_pph``
  is a required parameter and why :class:`_FakePedestrianInput` starts at 0.0
  rather than at whatever the caller asked for.
* ``No`` comes back as a python ``int``, ``Area``/``PedComp(1)``/
  ``VolType(1)`` as ``str`` -- the int/str split is real and is why the reader
  coerces rather than trusting.
* ``Cont(1)`` REFUSES writes ("Attribute Continued (0-MAX) cannot be
  changed"), and so does a BOGUS ``PedComp(1)`` ("Object N of type Pedestrian
  Composition does not exist"). Nothing in this family fails silently.
* ``Volume(2)`` on a 1-interval network RAISES ("Object 2 not found") rather
  than creating an interval -- the interval structure belongs to the network.

The first test is the echo-bite guard the last three units each had to ship: a
reader that returns its own argument as ``no`` makes every setter's identity
claim unfalsifiable (Q5 unit ②'s ``get_signal_controller`` defect, Q5 unit ③'s
``get_vehicle_input`` guard, and the eleven further instances Q6 unit ②'s AST
sweep turned up).
"""
from __future__ import annotations

from typing import Any

import pytest

from services.vissim_com import VissimController, VissimObjectNotFoundError


class _FakePedestrianInput:
    """``IPedestrianInput``. Interval-indexed attributes are stored under
    their literal ``Volume(1)``-style ids, exactly as COM addresses them."""

    def __init__(
        self,
        no: Any,
        *,
        name: str = "",
        area: str = "1",
        volume: float = 0.0,
        composition_no: str = "1",
        volume_type: str = "STOCHASTIC",
        interval_count: int = 1,
        known_composition_nos: tuple[int, ...] = (1,),
    ) -> None:
        self._values: dict[str, Any] = {
            "No": no,
            "Name": name,
            "Area": area,
            "Volume(1)": volume,
            "PedComp(1)": composition_no,
            "VolType(1)": volume_type,
            "Cont(1)": 0,
            # posOnArea, exposed by COM as plain X/Y and RELATIVE to the area
            # (a created input persists at exactly 0/0; the largest |pos| over
            # Beijing's 8 real inputs is 0.5518 against centroids of ~566).
            "X": 0.0,
            "Y": 0.0,
        }
        self._interval_count = interval_count
        self._known_composition_nos = set(known_composition_nos)
        #: Every SetAttValue in call order -- order is load-bearing for the
        #: "volume is written last, after the composition" pin below.
        self.set_calls: list[tuple[str, Any]] = []

    def _interval_of(self, attr_id: str) -> int | None:
        if "(" not in attr_id or not attr_id.endswith(")"):
            return None
        try:
            return int(attr_id[attr_id.index("(") + 1 : -1])
        except ValueError:
            return None

    def AttValue(self, attr_id: str) -> Any:
        interval = self._interval_of(attr_id)
        if interval is not None and interval > self._interval_count:
            raise RuntimeError(
                f"The subattribute no. 1 '{interval}' of type 'Time Interval' of the attribute "
                f"'{attr_id.split('(')[0].upper()}({interval})' is invalid: Object {interval} "
                "not found"
            )
        if attr_id not in self._values:
            raise RuntimeError(
                f"PEDESTRIANINPUT: Could not find attribute with ID {attr_id.upper()}."
            )
        return self._values[attr_id]

    def SetAttValue(self, attr_id: str, value: Any) -> None:
        interval = self._interval_of(attr_id)
        if interval is not None and interval > self._interval_count:
            raise RuntimeError(
                f"The subattribute no. 1 '{interval}' of type 'Time Interval' of the attribute "
                f"'{attr_id.split('(')[0].upper()}({interval})' is invalid: Object {interval} "
                "not found"
            )
        if attr_id.startswith("Cont("):
            raise RuntimeError(
                f"AttValue failed: Object {self._values['No']}: {self._values['Name']}: "
                "Attribute Continued (0-MAX) cannot be changed."
            )
        if attr_id.startswith("PedComp(") and int(value) not in self._known_composition_nos:
            raise RuntimeError(
                f"AttValue failed: Attribute Pedestrian composition: Object {value} of type "
                "Pedestrian Composition does not exist."
            )
        self.set_calls.append((attr_id, value))
        # COM hands interval-indexed reference ids back as str even when an
        # int went in -- measured ('1' -> write 2 -> reads back '2').
        self._values[attr_id] = str(value) if attr_id.startswith("PedComp(") else value


class _FakePedestrianInputContainer:
    def __init__(self, inputs: dict[int, _FakePedestrianInput], *, interval_count: int = 1) -> None:
        self._inputs = dict(inputs)
        self._interval_count = interval_count
        self.add_calls: list[tuple[int, Any]] = []
        self.remove_calls: list[int] = []

    def GetAll(self) -> list[_FakePedestrianInput]:
        return [self._inputs[k] for k in sorted(self._inputs)]

    def ItemByKey(self, key: int) -> _FakePedestrianInput:
        if key not in self._inputs:
            raise RuntimeError(
                "CCOMPedestrianInputContainerBase::get_ItemByKey failed: object "
                f"{key} does not exist"
            )
        return self._inputs[key]

    def AddPedestrianInput(self, key: int, area: Any) -> _FakePedestrianInput:
        # The Area argument must be a real COM OBJECT, not an int -- the same
        # pywin32 marshalling landmine AddVehicleInput's Link has.
        if not isinstance(area, _FakeArea):
            raise TypeError("The Python instance can not be converted to a COM object")
        self.add_calls.append((int(key), area))
        created = _FakePedestrianInput(
            int(key),
            area=str(area.AttValue("No")),
            interval_count=self._interval_count,
            known_composition_nos=(1, 2, 3, 12),
        )
        self._inputs[int(key)] = created
        return created

    def RemovePedestrianInput(self, pedestrian_input: _FakePedestrianInput) -> None:
        no = int(pedestrian_input.AttValue("No"))
        self.remove_calls.append(no)
        del self._inputs[no]


class _FakeArea:
    def __init__(self, no: int) -> None:
        self._no = no

    def AttValue(self, attr_id: str) -> Any:
        if attr_id == "No":
            return self._no
        raise RuntimeError(f"AREA: Could not find attribute with ID {attr_id.upper()}.")


class _FakeAreaContainer:
    def __init__(self, areas: dict[int, _FakeArea]) -> None:
        self._areas = dict(areas)

    def GetAll(self) -> list[_FakeArea]:
        return [self._areas[k] for k in sorted(self._areas)]

    def ItemByKey(self, key: int) -> _FakeArea:
        if key not in self._areas:
            raise RuntimeError(
                f"CCOMAreaContainerBase::get_ItemByKey failed: object {key} does not exist"
            )
        return self._areas[key]


class _FakeNet:
    def __init__(
        self, inputs: _FakePedestrianInputContainer, areas: _FakeAreaContainer
    ) -> None:
        self.PedestrianInputs = inputs
        self.Areas = areas


class _FakeVissim:
    def __init__(self, net: _FakeNet) -> None:
        self.Net = net


def _controller_with(
    inputs: dict[int, _FakePedestrianInput] | None = None,
    areas: tuple[int, ...] = (1, 2, 4),
    *,
    interval_count: int = 1,
) -> tuple[VissimController, _FakePedestrianInputContainer]:
    container = _FakePedestrianInputContainer(inputs or {}, interval_count=interval_count)
    controller = VissimController()
    controller.vissim = _FakeVissim(
        _FakeNet(container, _FakeAreaContainer({no: _FakeArea(no) for no in areas}))
    )
    return controller, container


# ---------------------------------------------------------------------------
# The echo-bite guard.
# ---------------------------------------------------------------------------


def test_get_pedestrian_input_reads_no_back_and_does_not_echo_its_argument():
    """THE regression this codebase has now had to fix four separate times.

    The stub deliberately reports a DIFFERENT ``No`` than the key it was
    looked up under. A reader that echoes its argument passes silently; only
    one that genuinely reads ``AttValue("No")`` can tell the truth here -- and
    since ``add_pedestrian_input`` returns this dict for a number it COMPUTED,
    an echoing reader would make the verb's own identity claim unfalsifiable.
    """
    controller, _ = _controller_with({7: _FakePedestrianInput(99, area="4", volume=120.0)})

    result = controller.get_pedestrian_input(7)

    assert result["no"] == "99", "reader echoed its argument instead of reading AttValue('No')"
    assert result["area"] == "4"
    assert result["volume"] == 120.0


def test_get_pedestrian_input_coerces_a_float_no_to_a_clean_int_string():
    """``_collection_nos``' own guard, mirrored: a float-typed ``No`` must
    never render as "9.0" and miss every shadow-model id keyed "9"."""
    controller, _ = _controller_with({9: _FakePedestrianInput(9.0)})

    assert controller.get_pedestrian_input(9)["no"] == "9"


def test_get_pedestrian_input_returns_the_full_documented_key_set():
    controller, _ = _controller_with(
        {3: _FakePedestrianInput(3, name="North kerb", area="2", volume=450.0)}
    )

    assert controller.get_pedestrian_input(3) == {
        "no": "3",
        "name": "North kerb",
        "area": "2",
        "volume": 450.0,
        "composition_no": "1",
        "volume_type": "STOCHASTIC",
    }


def test_get_pedestrian_input_refuses_a_bogus_number_and_names_the_real_ones():
    controller, _ = _controller_with(
        {1: _FakePedestrianInput(1), 5: _FakePedestrianInput(5)}
    )

    with pytest.raises(VissimObjectNotFoundError) as excinfo:
        controller.get_pedestrian_input(42)

    message = str(excinfo.value)
    assert "42" in message
    assert "1" in message and "5" in message
    # Refusals must survive toolkit.dispatch's 300-char cap whole (Q6 unit ①).
    assert len(message) < 300


# ---------------------------------------------------------------------------
# add_pedestrian_input
# ---------------------------------------------------------------------------


def test_add_pedestrian_input_picks_max_plus_one_and_writes_the_volume():
    controller, container = _controller_with(
        {1: _FakePedestrianInput(1), 4: _FakePedestrianInput(4)}
    )

    result = controller.add_pedestrian_input(area_no=2, volume_pph=750.0)

    assert result["no"] == "5"
    assert result["area"] == "2"
    assert result["volume"] == 750.0
    assert container.add_calls[0][0] == 5


def test_add_pedestrian_input_on_an_empty_network_starts_at_one():
    """No family limitation: a network with zero pedestrian inputs CAN mint
    its first (live-proven twice, on two networks that also had zero areas)."""
    controller, _ = _controller_with({})

    assert controller.add_pedestrian_input(area_no=1, volume_pph=300.0)["no"] == "1"


def test_add_pedestrian_input_passes_a_real_area_object_not_an_int():
    """The pywin32 marshalling landmine: the fake raises TypeError for a raw
    int, exactly as real COM does, so a regression that stopped resolving the
    area to an object would fail here rather than only live."""
    controller, container = _controller_with({})

    controller.add_pedestrian_input(area_no=4, volume_pph=100.0)

    assert isinstance(container.add_calls[0][1], _FakeArea)


def test_add_pedestrian_input_refuses_a_bogus_area_before_creating_anything():
    controller, container = _controller_with({1: _FakePedestrianInput(1)}, areas=(1, 2))

    with pytest.raises(VissimObjectNotFoundError) as excinfo:
        controller.add_pedestrian_input(area_no=77, volume_pph=500.0)

    assert "77" in str(excinfo.value)
    assert container.add_calls == [], "an object was created despite the bogus area"


def test_add_pedestrian_input_defaults_to_zero_volume_until_it_is_written():
    """The reason ``volume_pph`` is required, not optional: a fresh input's own
    ``Volume(1)`` default is 0.0, i.e. an input created and left alone
    generates nothing at all."""
    controller, container = _controller_with({})

    controller.add_pedestrian_input(area_no=1, volume_pph=640.0)

    created = container.ItemByKey(1)
    assert ("Volume(1)", 640.0) in created.set_calls
    # And the write really is what moved it off the default.
    assert created.set_calls[-1] == ("Volume(1)", 640.0)


def test_add_pedestrian_input_applies_an_explicit_composition_before_the_volume():
    controller, container = _controller_with({})

    result = controller.add_pedestrian_input(
        area_no=1, volume_pph=200.0, name="Plaza", composition_no=2
    )

    created = container.ItemByKey(1)
    written = [attr for attr, _ in created.set_calls]
    assert written == ["Name", "PedComp(1)", "Volume(1)"]
    assert result["composition_no"] == "2"
    assert result["name"] == "Plaza"


def test_add_pedestrian_input_surfaces_a_bogus_composition_rather_than_swallowing_it():
    """Vissim refuses a non-existent composition LOUDLY ("Object 1011 of type
    Pedestrian Composition does not exist"); nothing here fails silently, and
    the wrapper must not turn that into a success."""
    controller, _ = _controller_with({})

    with pytest.raises(RuntimeError) as excinfo:
        controller.add_pedestrian_input(area_no=1, volume_pph=200.0, composition_no=1011)

    assert "does not exist" in str(excinfo.value)


def test_add_pedestrian_input_omits_the_composition_write_when_not_asked():
    controller, container = _controller_with({})

    controller.add_pedestrian_input(area_no=1, volume_pph=200.0)

    written = [attr for attr, _ in container.ItemByKey(1).set_calls]
    assert "PedComp(1)" not in written
    assert written == ["Volume(1)"]


# ---------------------------------------------------------------------------
# set_pedestrian_input_volume
# ---------------------------------------------------------------------------


def test_set_pedestrian_input_volume_writes_interval_one_and_reads_back():
    controller, container = _controller_with({2: _FakePedestrianInput(2, volume=450.0)})

    result = controller.set_pedestrian_input_volume(2, 1200.0)

    assert result["volume"] == 1200.0
    assert container.ItemByKey(2).set_calls == [("Volume(1)", 1200.0)]


def test_set_pedestrian_input_volume_result_is_read_back_not_echoed():
    controller, _ = _controller_with({2: _FakePedestrianInput(88, volume=450.0)})

    assert controller.set_pedestrian_input_volume(2, 10.0)["no"] == "88"


def test_set_pedestrian_input_volume_refuses_a_bogus_number():
    controller, _ = _controller_with({2: _FakePedestrianInput(2)})

    with pytest.raises(VissimObjectNotFoundError):
        controller.set_pedestrian_input_volume(404, 100.0)


def test_only_interval_one_is_ever_addressed():
    """The interval STRUCTURE belongs to the network: ``Volume(2)`` on a
    1-interval network raises rather than creating one, so this module writes
    interval 1 and nothing else. The fake raises for any interval beyond its
    own count, so a regression that started addressing interval 2 would fail
    here."""
    controller, container = _controller_with({1: _FakePedestrianInput(1)}, interval_count=1)

    controller.set_pedestrian_input_volume(1, 99.0)

    assert all(attr.endswith("(1)") for attr, _ in container.ItemByKey(1).set_calls)


def test_a_multi_interval_network_still_only_gets_interval_one_written():
    """Measured on the 14-interval Air Taxi Vertiport network: the created
    input arrives pre-stocked with all 14 intervals, and only interval 1 is
    written. The warn about the other 13 is the agent layer's job, not this
    module's."""
    controller, container = _controller_with({}, interval_count=14)

    controller.add_pedestrian_input(area_no=1, volume_pph=200.0)

    assert container.ItemByKey(1).set_calls == [("Volume(1)", 200.0)]


# ---------------------------------------------------------------------------
# remove_pedestrian_input
# ---------------------------------------------------------------------------


def test_remove_pedestrian_input_removes_exactly_that_object():
    controller, container = _controller_with(
        {1: _FakePedestrianInput(1), 2: _FakePedestrianInput(2), 3: _FakePedestrianInput(3)}
    )

    controller.remove_pedestrian_input(2)

    assert container.remove_calls == [2]
    assert [int(i.AttValue("No")) for i in container.GetAll()] == [1, 3]


def test_remove_pedestrian_input_refuses_a_bogus_number_and_removes_nothing():
    controller, container = _controller_with({1: _FakePedestrianInput(1)})

    with pytest.raises(VissimObjectNotFoundError):
        controller.remove_pedestrian_input(9)

    assert container.remove_calls == []


# ---------------------------------------------------------------------------
# available_* enumerations
# ---------------------------------------------------------------------------


def test_available_pedestrian_input_and_area_nos_are_sorted_ints():
    controller, _ = _controller_with(
        {5: _FakePedestrianInput(5), 1: _FakePedestrianInput(1)}, areas=(4, 1, 2)
    )

    assert controller.available_pedestrian_input_nos() == [1, 5]
    assert controller.available_pedestrian_area_nos() == [1, 2, 4]


def test_available_nos_are_empty_on_a_network_with_no_viswalk_layer():
    """A network with no pedestrian layer is normal data, not an error --
    17 of the corpus's 25 files have no ``<pedestrianInputs>`` section at
    all."""
    controller, _ = _controller_with({}, areas=())

    assert controller.available_pedestrian_input_nos() == []
    assert controller.available_pedestrian_area_nos() == []

"""Tests for services.vissim_com's signal-controller genesis pair:
``get_signal_controller`` / ``add_signal_controller`` (Q5 unit ②).

Fake COM stubs stand in for real COM (COM tasks need the real Vissim on this
machine per docs/phase2/00-EXECUTION-GUIDE.md §5 -- this file proves the pure
reader/duplicator LOGIC; live truth comes from
``backend/tools/probe_signal_controller_duplicate.py``, which verified
``DuplicateSignalController``'s argument shapes, the name inheritance and the
zero-controller limitation against a real Vissim 2026 instance before this
module was written around them).

The specific thing these tests exist to pin is narrow and was a real defect:
``get_signal_controller`` originally returned ``"no": str(sc_no)`` -- an ECHO
of its own argument. ``add_signal_controller`` returns this dict for a number
it COMPUTED (``max(existing) + 1``) and the agent layer lifts
``artifacts.new_sc_no`` straight out of it, so an echo makes the verb's
identity claim unfalsifiable: it would report success on the number it
picked rather than the number Vissim actually minted. That is precisely the
verify-by-state-diff-never-return-value rule this layer exists to enforce, so
it gets a test rather than only a comment.
"""
from __future__ import annotations

from typing import Any

import pytest

from services.vissim_com import VissimController, VissimObjectNotFoundError


class _FakeSignalController:
    """AttValue/SetAttValue stub for one COM SignalController object.

    ``no_value`` is deliberately settable to a float, because that is what
    real COM does on some collections -- ``_collection_nos`` already carries
    an ``int()`` for the same reason.
    """

    def __init__(self, no_value: Any, name: str, *, sg_nos: tuple[int, ...] = (1, 2)) -> None:
        self._values: dict[str, Any] = {
            "No": no_value,
            "Name": name,
            "Type": "FIXEDTIME",
            "CycTm": 90.0,
            "Offset": 0.0,
            "ProgNo": "1",
        }
        self.SGs = _FakeCollection({n: _FakeSignalGroup(n) for n in sg_nos})
        self.set_calls: list[tuple[str, Any]] = []

    def AttValue(self, attr_id: str) -> Any:
        return self._values[attr_id]

    def SetAttValue(self, attr_id: str, value: Any) -> None:
        self.set_calls.append((attr_id, value))
        self._values[attr_id] = value


class _FakeSignalGroup:
    def __init__(self, no: int) -> None:
        self._no = no

    def AttValue(self, attr_id: str) -> Any:
        if attr_id == "No":
            return self._no
        raise KeyError(attr_id)


class _FakeCollection:
    """Minimal ItemByKey/GetAll container mirroring a Vissim COM collection."""

    def __init__(self, items: dict[int, Any]) -> None:
        self._items = dict(items)
        self.duplicate_calls: list[tuple[Any, Any]] = []

    def ItemByKey(self, key: int) -> Any:
        if key not in self._items:
            raise RuntimeError(f"get_ItemByKey failed: object {key} does not exist")
        return self._items[key]

    def GetAll(self) -> list[Any]:
        return [self._items[k] for k in sorted(self._items)]

    def DuplicateSignalController(self, source: Any, new_key: Any) -> Any:
        self.duplicate_calls.append((source, new_key))
        donor = self._items[int(source)]
        # Mirrors the live finding: an exact copy, INCLUDING the donor's name.
        copy = _FakeSignalController(int(new_key), donor.AttValue("Name"))
        self._items[int(new_key)] = copy
        return copy


class _FakeNet:
    def __init__(self, signal_controllers: _FakeCollection) -> None:
        self.SignalControllers = signal_controllers


class _FakeVissim:
    def __init__(self, net: _FakeNet) -> None:
        self.Net = net


def _controller_with(items: dict[int, Any]) -> tuple[VissimController, _FakeCollection]:
    collection = _FakeCollection(items)
    controller = VissimController()
    controller.vissim = _FakeVissim(_FakeNet(collection))
    return controller, collection


# ---------------------------------------------------------------------------
# get_signal_controller -- the reader must READ, not echo
# ---------------------------------------------------------------------------


def test_get_signal_controller_reads_no_back_instead_of_echoing_the_argument():
    """The regression guard. A fake whose stored ``No`` disagrees with the
    key it is filed under cannot occur through a real ``ItemByKey``, so this
    asserts the SOURCE of the value, not a reachable production state: an
    implementation that echoed ``sc_no`` would return "7" here, and the real
    reader returns the object's own attribute."""
    controller, _ = _controller_with({7: _FakeSignalController(42, "Reads its own No")})

    result = controller.get_signal_controller(7)

    assert result["no"] == "42"


def test_get_signal_controller_normalises_a_float_no_to_a_bare_integer_string():
    """COM hands ``No`` back as a float on some collections. A "2.0" here
    would silently miss every shadow-model id keyed "2", which is how the
    guardrail matches added objects -- so the reader ints() first."""
    controller, _ = _controller_with({2: _FakeSignalController(2.0, "Float No")})

    result = controller.get_signal_controller(2)

    assert result["no"] == "2"


def test_get_signal_controller_returns_the_full_contract_shape():
    controller, _ = _controller_with({3: _FakeSignalController(3, "Main St")})

    result = controller.get_signal_controller(3)

    assert result == {
        "no": "3",
        "name": "Main St",
        "type": "FIXEDTIME",
        "cycle_time_s": 90.0,
        "offset_s": 0.0,
        "program_no": "1",
        "signal_group_nos": ["1", "2"],
    }


def test_get_signal_controller_raises_structured_not_found_for_a_bogus_number():
    controller, _ = _controller_with({3: _FakeSignalController(3, "Main St")})

    with pytest.raises(VissimObjectNotFoundError, match="Signal controller 99 does not exist"):
        controller.get_signal_controller(99)


# ---------------------------------------------------------------------------
# add_signal_controller -- duplicate-then-edit, and the genesis limitation
# ---------------------------------------------------------------------------


def test_add_signal_controller_returns_the_number_vissim_minted_not_the_one_it_computed():
    """End-to-end version of the reader test above: the verb picks
    ``max(existing) + 1`` and must report the copy's OWN number."""
    controller, collection = _controller_with(
        {1: _FakeSignalController(1, "Donor"), 4: _FakeSignalController(4, "Other")}
    )

    result = controller.add_signal_controller(1, "New SC")

    assert collection.duplicate_calls == [(1, 5)]
    assert result["no"] == "5"
    assert result["name"] == "New SC"


def test_add_signal_controller_renames_the_copy_because_it_inherits_the_donors_name():
    """Live finding: ``DuplicateSignalController`` copies the donor's Name
    too, so without the rename a network ends up with two identically-named
    controllers and no way to tell them apart in the UI or a report."""
    controller, collection = _controller_with({1: _FakeSignalController(1, "Donor")})

    controller.add_signal_controller(1, "Renamed copy")

    copy = collection.ItemByKey(2)
    assert copy.set_calls == [("Name", "Renamed copy")]
    assert collection.ItemByKey(1).AttValue("Name") == "Donor", "donor must not be renamed"


def test_add_signal_controller_refuses_a_bogus_source_before_calling_duplicate():
    """The source is existence-checked first, so the error names the real
    available numbers instead of surfacing a raw COM failure -- and no
    half-object is created."""
    controller, collection = _controller_with({1: _FakeSignalController(1, "Donor")})

    with pytest.raises(VissimObjectNotFoundError, match="Signal controller 99 does not exist"):
        controller.add_signal_controller(99, "Never created")

    assert collection.duplicate_calls == []
    assert sorted(collection._items) == [1]


def test_add_signal_controller_states_the_genesis_limitation_on_an_empty_network():
    """Proven live on Roundabout Billings.US (0 controllers): there is no
    source to duplicate and plain AddSignalController yields a controller
    with no real timing program, which makes the simulation fail outright.
    Refused with a reason, never silently half-done."""
    controller, collection = _controller_with({})

    with pytest.raises(RuntimeError, match="no existing signal controller to duplicate"):
        controller.add_signal_controller(1, "First ever")

    assert collection.duplicate_calls == []

"""Tests for services.vissim_com's vehicle-input genesis pair:
``get_vehicle_input`` / ``add_vehicle_input`` (Q5 unit ③).

Fake COM stubs stand in for real COM (COM tasks need the real Vissim on this
machine per docs/phase2/00-EXECUTION-GUIDE.md §5 -- this file proves the pure
reader/creator LOGIC; live truth comes from
``backend/tools/probe_vehicle_input_create.py``, which verified
``AddVehicleInput``'s argument shapes, a fresh input's defaults, the absence
of any zero-input family limitation and the shadow-model delta against a real
Vissim 2026 instance before this module was written around them).

The specific thing the first two tests exist to pin is narrow and was a real
defect one unit ago: Q5 unit ②'s ``get_signal_controller`` originally returned
``"no": str(sc_no)`` -- an ECHO of its own argument. ``add_vehicle_input``
returns this dict for a number it COMPUTED (``max(existing) + 1``) and the
agent layer lifts ``artifacts.new_vehicle_input_no`` straight out of it, so an
echo makes the verb's identity claim unfalsifiable: it would report success on
the number it picked rather than the number Vissim actually minted. That is
precisely the verify-by-state-diff-never-return-value rule this layer exists
to enforce, so it gets a test rather than only a comment.
"""
from __future__ import annotations

from typing import Any

import pytest

from services.vissim_com import VissimController, VissimObjectNotFoundError


class _FakeVehicleInput:
    """AttValue/SetAttValue stub for one COM VehicleInput object.

    Defaults mirror what the probe measured off a genuinely fresh input on
    three real networks (``Name=''``, ``Volume(1)=0.0``, ``VehComp(1)='1'``,
    ``VolType(1)='STOCHASTIC'``) -- in particular ``Volume(1)`` starts at
    ZERO, which is why the verb must always write it.

    ``no_value`` is deliberately settable to a float. Real ``VehicleInputs``
    hands ``No`` back as an ``int`` on this Vissim version (measured), but
    ``SignalControllers`` hands back a float and ``_collection_nos`` already
    carries an ``int()`` for that reason -- this stub lets the reader's own
    normalisation be tested rather than assumed.
    """

    def __init__(self, no_value: Any, link: str, *, name: str = "") -> None:
        self._values: dict[str, Any] = {
            "No": no_value,
            "Name": name,
            "Link": link,
            "Volume(1)": 0.0,
            "VehComp(1)": "1",
            "VolType(1)": "STOCHASTIC",
        }
        self.set_calls: list[tuple[str, Any]] = []

    def AttValue(self, attr_id: str) -> Any:
        return self._values[attr_id]

    def SetAttValue(self, attr_id: str, value: Any) -> None:
        self.set_calls.append((attr_id, value))
        # Mirrors the live finding that VehComp(1) accepts an int and reads
        # back the STRING form -- so a test asserting the returned dict
        # cannot accidentally pass by comparing int to int.
        self._values[attr_id] = str(value) if attr_id == "VehComp(1)" else value


class _FakeLink:
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

    def ItemByKey(self, key: int) -> Any:
        if key not in self._items:
            raise RuntimeError(f"get_ItemByKey failed: object {key} does not exist")
        return self._items[key]

    def GetAll(self) -> list[Any]:
        return [self._items[k] for k in sorted(self._items)]


class _FakeVehicleInputContainer(_FakeCollection):
    def __init__(self, items: dict[int, Any]) -> None:
        super().__init__(items)
        self.add_calls: list[tuple[Any, Any]] = []

    def AddVehicleInput(self, key: Any, link: Any) -> Any:
        # The live matrix: a raw int/str/None/Lane for ``Link`` all RAISE and
        # create nothing. Enforced here so a regression that stopped passing
        # a real Link object would fail this file, not only the live check.
        if not isinstance(link, _FakeLink):
            raise TypeError("The Python instance can not be converted to a COM object")
        self.add_calls.append((key, link))
        created = _FakeVehicleInput(int(key), str(link.AttValue("No")))
        self._items[int(key)] = created
        return created


class _FakeNet:
    def __init__(self, vehicle_inputs: _FakeVehicleInputContainer, links: _FakeCollection) -> None:
        self.VehicleInputs = vehicle_inputs
        self.Links = links


class _FakeVissim:
    def __init__(self, net: _FakeNet) -> None:
        self.Net = net


def _controller_with(
    inputs: dict[int, Any], link_nos: tuple[int, ...] = (1, 2)
) -> tuple[VissimController, _FakeVehicleInputContainer]:
    container = _FakeVehicleInputContainer(inputs)
    links = _FakeCollection({n: _FakeLink(n) for n in link_nos})
    controller = VissimController()
    controller.vissim = _FakeVissim(_FakeNet(container, links))
    return controller, container


# ---------------------------------------------------------------------------
# get_vehicle_input -- the reader must READ, not echo
# ---------------------------------------------------------------------------


def test_get_vehicle_input_reads_no_back_instead_of_echoing_the_argument():
    """The regression guard. A fake whose stored ``No`` disagrees with the
    key it is filed under cannot occur through a real ``ItemByKey``, so this
    asserts the SOURCE of the value, not a reachable production state: an
    implementation that echoed ``input_no`` would return "7" here, and the
    real reader returns the object's own attribute."""
    controller, _ = _controller_with({7: _FakeVehicleInput(42, "1")})

    result = controller.get_vehicle_input(7)

    assert result["no"] == "42"


def test_get_vehicle_input_normalises_a_float_no_to_a_bare_integer_string():
    """``VehicleInputs`` hands ``No`` back as an int on this Vissim version
    (measured), but ``SignalControllers`` hands back a float, so the
    normalisation is a house convention rather than a fix for something
    observed here. It still has to WORK: a "13.0" would silently miss every
    shadow-model id keyed "13", which is how the guardrail matches added
    objects."""
    controller, _ = _controller_with({13: _FakeVehicleInput(13.0, "1")})

    result = controller.get_vehicle_input(13)

    assert result["no"] == "13"


def test_get_vehicle_input_normalises_a_float_link_the_same_way():
    """The ``No`` guard above had a sibling gap: ``Link`` was passed through as
    ``str(...)``, so a float-typed link rendered "617.0".

    That stopped being cosmetic with unit D4 (2026-08-12). This link is what
    ``services.agent.fixes.entrance_measurement`` joins against a movement
    OBJECT_ID's own integer from-link to decide whether the observed counts
    were taken AT this entrance. "617.0" != "617" would fall to UNVERIFIED —
    the branch that ALLOWS the demand edit as a labelled estimate — so the
    mismatch would silently license overwriting a measurement rather than
    merely losing a refusal.
    """
    controller, _ = _controller_with({13: _FakeVehicleInput(13, 617.0)})

    result = controller.get_vehicle_input(13)

    assert result["link"] == "617"


@pytest.mark.parametrize(
    "raw, expected",
    [
        (617, "617"),
        (617.0, "617"),
        ("617", "617"),
        # Not truncated and not invented: a non-integral or non-numeric id
        # compares as itself rather than being coerced into a false match.
        (617.5, "617.5"),
        ("Murdoch Dr_NB", "Murdoch Dr_NB"),
        (None, ""),
    ],
)
def test_id_text_normalises_only_integral_numbers(raw, expected):
    from services.vissim_com import _id_text

    assert _id_text(raw) == expected


def test_get_vehicle_input_returns_the_full_contract_shape():
    controller, _ = _controller_with({3: _FakeVehicleInput(3, "5", name="North approach")})

    result = controller.get_vehicle_input(3)

    assert result == {
        "no": "3",
        "name": "North approach",
        "link": "5",
        "volume": 0.0,
        "composition_no": "1",
        "volume_type": "STOCHASTIC",
    }


def test_get_vehicle_input_raises_structured_not_found_for_a_bogus_number():
    controller, _ = _controller_with({3: _FakeVehicleInput(3, "1")})

    with pytest.raises(VissimObjectNotFoundError, match="Vehicle input 99 does not exist"):
        controller.get_vehicle_input(99)


# ---------------------------------------------------------------------------
# add_vehicle_input -- numbering, the always-write-the-volume rule, and the
# genesis case that has NO family limitation
# ---------------------------------------------------------------------------


def test_add_vehicle_input_returns_the_number_vissim_minted_not_the_one_it_computed():
    """End-to-end version of the reader test above: the verb picks
    ``max(existing) + 1`` and must report the new object's OWN number."""
    controller, container = _controller_with(
        {1: _FakeVehicleInput(1, "1"), 4: _FakeVehicleInput(4, "2")}
    )

    result = controller.add_vehicle_input(1, 450.0)

    assert [key for key, _link in container.add_calls] == [5]
    assert result["no"] == "5"
    assert result["link"] == "1"


def test_add_vehicle_input_mints_number_one_on_a_network_with_no_inputs_at_all():
    """The headline finding: unlike add_priority_rule (Q4 unit 6) and
    add_signal_controller (Q5 unit 2), both duplicate-based and genuinely
    stuck without a donor, this verb has NO zero-object family limitation --
    live-proven on Air Taxi Vertiport (GetAll() [] -> [1], saved file
    reloads load_ok/init_ok)."""
    controller, container = _controller_with({})

    result = controller.add_vehicle_input(2, 900.0)

    assert [key for key, _link in container.add_calls] == [1]
    assert result["no"] == "1"
    assert result["link"] == "2"
    assert result["volume"] == 900.0


def test_add_vehicle_input_always_writes_the_volume_because_the_default_is_zero():
    """A fresh input's own Volume(1) default is 0.0 (live-measured), i.e. an
    input created and left alone is a real object that generates nothing --
    the exact silent-success shape this layer exists to prevent. So the
    volume write is unconditional, even for 0.0."""
    controller, container = _controller_with({})

    controller.add_vehicle_input(1, 0.0)

    created = container.ItemByKey(1)
    assert ("Volume(1)", 0.0) in created.set_calls


def test_add_vehicle_input_writes_name_and_composition_only_when_given():
    controller, container = _controller_with({})

    controller.add_vehicle_input(1, 450.0)

    created = container.ItemByKey(1)
    assert [attr for attr, _v in created.set_calls] == ["Volume(1)"]


def test_add_vehicle_input_writes_name_and_composition_when_supplied():
    controller, container = _controller_with({})

    result = controller.add_vehicle_input(1, 450.0, name="North approach", composition_no=2)

    created = container.ItemByKey(1)
    assert created.set_calls == [
        ("Name", "North approach"),
        ("VehComp(1)", 2),
        ("Volume(1)", 450.0),
    ]
    assert result["name"] == "North approach"
    assert result["composition_no"] == "2"


def test_add_vehicle_input_refuses_a_bogus_link_before_calling_add():
    """The link is resolved through _link_object_by_no first, so a bogus
    number produces this module's structured not-found error naming the real
    available links instead of a raw COM failure -- and nothing is created."""
    controller, container = _controller_with({}, link_nos=(1, 2))

    with pytest.raises(VissimObjectNotFoundError, match="Link 99 does not exist"):
        controller.add_vehicle_input(99, 450.0)

    assert container.add_calls == []
    assert container.GetAll() == []

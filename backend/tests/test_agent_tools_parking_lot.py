"""Tests for Q6 unit (2)'s parking-lot surface: ``tools_edit``'s
add_parking_lot / set_parking_lot_capacity / remove_parking_lot, dispatched
through the REAL toolkit.dispatch (so pydantic validation + the envelope
machinery are exercised exactly like production), plus the three constraints
and the shadow-model parser those verbs stand on.

House shape = test_agent_tools_pt_stops_lines.py (the closest sibling: a
create/remove pair whose removal must refuse on reference integrity). The fake
session owns a real on-disk ``working_inpx`` because ``remove_parking_lot``'s
post-persist "is it really gone" readback
(``tools_edit._still_present_after_remove``) reparses that file, and because
the reference-integrity refusal needs a genuinely-parseable XML fixture.

No real Vissim COM anywhere in this file. Live truth comes from
``backend/tools/probe_parking_lot.py`` (ten parts) and
``backend/tools/parking_lot_live_check.py`` (the real-engine E2E +
counterfactuals).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

import services.agent.tools_edit as tools_edit  # noqa: F401 - import registers tools
import services.agent.tools_network as tools_network
from services.agent import constraints
from services.agent.edit_session import clear_active_edit_session, set_active_edit_session
from services.agent.guardrails import (
    _ALLOWS_NEW_OBJECTS_IN,
    _ALLOWS_REMOVED_OBJECTS_IN,
    _SHADOW_OBJECT_ID_FIELDS,
    _WRITE_TARGET_FIELD_SPEC,
)
from services.agent.toolkit import default_registry, dispatch
from services.inpx_structure import ID_REFERENCE_SITES, find_id_references

_NETWORK_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<network>
<links>
<link no="1" name="Approach"><lanes><lane width="3.5"/><lane width="3.5"/></lanes>
<geometry><linkPolyPts><linkPolyPoint x="0" y="0"/><linkPolyPoint x="400" y="0"/></linkPolyPts></geometry>
</link>
<link no="9" name="Short"><lanes><lane width="3.5"/></lanes>
<geometry><linkPolyPts><linkPolyPoint x="0" y="50"/><linkPolyPoint x="20" y="50"/></linkPolyPts></geometry>
</link>
</links>
{extra}
</network>
"""


def _write_working_inpx(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "working.inpx"
    path.write_text(_NETWORK_TEMPLATE.format(extra=extra), encoding="utf-8")
    return path


def _remove_by_no(path: Path, container_tag: str, child_tag: str, no: Any) -> None:
    """Simulate a real COM Remove* + persist(): mutate the on-disk working copy
    so a subsequent reparse (the post-remove readback) sees the genuinely
    removed state, not a stale fixture."""
    tree = ET.parse(path)
    root = tree.getroot()
    container = root.find(container_tag)
    if container is None:
        return
    for el in list(container.findall(child_tag)):
        if el.get("no") == str(no):
            container.remove(el)
    tree.write(path, encoding="UTF-8", xml_declaration=True)


class FakeParkingSession:
    """Duck-types VissimEditSession's parking surface: records every call,
    returns a rigged readback for add/set, mutates the real on-disk working
    copy for remove."""

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

    def add_parking_lot(
        self,
        link_no: int,
        lane_index: int,
        pos_m: float,
        capacity: int,
        name: str = "",
        len_per_space_m: float | None = None,
    ) -> dict[str, Any]:
        self._record(
            "add_parking_lot",
            link_no=link_no, lane_index=lane_index, pos_m=pos_m, capacity=capacity,
            name=name, len_per_space_m=len_per_space_m,
        )
        return self._readback["parking_lot"]

    def set_parking_lot_capacity(
        self, parking_lot_no: int, capacity: int, len_per_space_m: float | None = None
    ) -> dict[str, Any]:
        self._record(
            "set_parking_lot_capacity",
            parking_lot_no=parking_lot_no, capacity=capacity, len_per_space_m=len_per_space_m,
        )
        return self._readback["parking_lot"]

    def remove_parking_lot(self, no: int) -> None:
        self._record("remove_parking_lot", no=no)
        _remove_by_no(self.working_inpx, "parkingLots", "parkingLot", no)

    def persist(self) -> None:
        self.persist_calls += 1

    def close(self) -> None:
        self.closed = True


def _lot_readback(**overrides: Any) -> dict[str, Any]:
    base = {
        "no": "1",
        "name": "",
        "type": "REALPARKINGSPACE",
        "link": "1",
        "lane_index": 1,
        "zone": "",
        "pos": 100.0,
        "length": 60.0,
        "len_per_space": 6.0,
        "capacity": 10,
        "attractivity": 0.0,
        "max_park_time_s": 99999.0,
        "des_speed_distr_no": "15",
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _reset_active_edit_session():
    clear_active_edit_session()
    yield
    clear_active_edit_session()


def _dispatch(tool_name: str, **params: Any) -> dict[str, Any]:
    return dispatch(tool_name, params, registry=default_registry)


# ---------------------------------------------------------------------------
# The parser -- representation came first, because a diff that cannot see the
# object makes the verb unverifiable (Q4 unit (4)'s precedent).
# ---------------------------------------------------------------------------

_REAL_PARKING_SPACE_XML = (
    '<parkingLots><parkingLot no="14" name="Deck" type="REALPARKINGSPACE" lane="91 1" '
    'pos="1.7868043458532479" length="36" capacity="6" lenPerSpc="6" attrac="70" relFlow="1" '
    'parkTmMax="99999" parkFeeFlat="0" parkFeeHr="0" openFrom="0" openUntil="99999" '
    'parkDir="FORWARDINFORWARDOUT" desSpeedDistrDef="50" dirChgDurDist="9" zone="114"/>'
    "</parkingLots>"
)

_ZONE_CONNECTOR_XML = (
    '<parkingLots><parkingLot no="26" name="" type="ZONECONNECTOR" intLink="26" zone="1000005" '
    'pos="22.536777855399016" length="3" capacity="10000" lenPerSpc="6" attrac="0" relFlow="0" '
    'parkTmMax="99999" openFrom="0" openUntil="99999" parkDir="FORWARDINFORWARDOUT" '
    'desSpeedDistrDef="65"/></parkingLots>'
)


def test_parse_parking_lots_reads_a_real_parking_space():
    """Field values copied verbatim from 3D - Complex Intersection
    Karlsruhe.DE's own lot 14 (the one real REALPARKINGSPACE the corpus scan
    reports carrying a zone and a non-1 capacity)."""
    root = ET.fromstring(f"<network>{_REAL_PARKING_SPACE_XML}</network>")

    (lot,) = tools_network._parse_parking_lots(root)

    assert lot["no"] == "14"
    assert lot["type"] == "REALPARKINGSPACE"
    assert lot["link"] == "91"
    assert lot["lane_index"] == 1
    assert lot["capacity"] == 6
    assert lot["length"] == pytest.approx(36.0)
    assert lot["len_per_space"] == pytest.approx(6.0)
    assert lot["attractivity"] == pytest.approx(70.0)
    assert lot["zone"] == "114"
    assert lot["dir_change_dur_distr_no"] == "9"
    assert lot["des_speed_distr_no"] == "50"


def test_parse_parking_lots_resolves_a_zone_connector_off_intLink():
    """The second species anchors on a WHOLE link (``intLink``), not a lane --
    values from Motorway Dyn Assign Mainz.DE's lot 26. ``lane_index`` must be
    None, never a fabricated 1."""
    root = ET.fromstring(f"<network>{_ZONE_CONNECTOR_XML}</network>")

    (lot,) = tools_network._parse_parking_lots(root)

    assert lot["type"] == "ZONECONNECTOR"
    assert lot["link"] == "26"
    assert lot["lane_index"] is None
    assert lot["zone"] == "1000005"
    assert lot["capacity"] == 10000


def test_parse_parking_lots_empty_when_section_absent():
    """Most real networks have no ``<parkingLots>`` section at all (19 of the
    21 example networks) -- an absent section is zero lots, never an error."""
    root = ET.fromstring("<network><links/></network>")

    assert tools_network._parse_parking_lots(root) == []


def test_shadow_model_carries_parking_lots(tmp_path: Path):
    working = _write_working_inpx(tmp_path, extra=_REAL_PARKING_SPACE_XML)

    shadow = tools_network.build_shadow_model(working)

    assert [lot["no"] for lot in shadow["parking_lots"]] == ["14"]


# ---------------------------------------------------------------------------
# Reference integrity -- the registry entry, and the cascade it guards
# ---------------------------------------------------------------------------

_PARKING_ROUTE_XML = (
    '<vehicleRoutingDecisionsParking><vehicleRoutingDecisionParking no="40" link="1" pos="10">'
    '<vehRoutPark><vehicleRouteParking no="114" parkLot="14" relFlowSameParkLot="1"/></vehRoutPark>'
    "</vehicleRoutingDecisionParking></vehicleRoutingDecisionsParking>"
)


def test_parking_lots_is_registered_as_a_referenced_object_class():
    assert "parking_lots" in ID_REFERENCE_SITES
    (site,) = ID_REFERENCE_SITES["parking_lots"]
    assert site.attr == "parkLot"


def test_find_id_references_names_the_parking_route_that_uses_a_lot():
    root = ET.fromstring(f"<network>{_REAL_PARKING_SPACE_XML}{_PARKING_ROUTE_XML}</network>")

    referrers = find_id_references(root, "parking_lots", "14")

    assert len(referrers) == 1
    assert referrers[0]["site"] == "parking route"
    assert referrers[0]["value"] == "14"


def test_find_id_references_is_silent_for_an_unreferenced_lot():
    root = ET.fromstring(f"<network>{_REAL_PARKING_SPACE_XML}</network>")

    assert find_id_references(root, "parking_lots", "14") == []


# ---------------------------------------------------------------------------
# add_parking_lot
# ---------------------------------------------------------------------------


def test_add_parking_lot_happy_path(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeParkingSession(working, readback={"parking_lot": _lot_readback()})
    set_active_edit_session(session)

    envelope = _dispatch("add_parking_lot", link_no="1", lane_index=1, pos_m=100.0, capacity=10)

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"link": "1", "lane_index": 1, "pos": 100.0, "capacity": 10},
        "readback_value": {"link": "1", "lane_index": 1, "pos": 100.0, "capacity": 10},
    }
    assert envelope["artifacts"]["no"] == "1"
    assert envelope["artifacts"]["capacity"] == 10
    assert envelope["artifacts"]["des_speed_distr_no"] == "15"
    assert "Parking lot 1 added on link 1 lane 1" in envelope["message"]
    assert "10 space(s)" in envelope["message"]
    assert "attracts no traffic" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [
        {
            "op": "add_parking_lot", "link_no": 1, "lane_index": 1, "pos_m": 100.0,
            "capacity": 10, "name": "", "len_per_space_m": None,
        }
    ]


def test_add_parking_lot_verifies_the_DERIVED_capacity_not_the_request(tmp_path: Path):
    """The core of this unit: Vissim computes ``Capacity`` itself, so the
    readback half of the claim must be Vissim's number. A COM layer that
    echoed the request would make the two sides agree by construction; here
    they DISAGREE (Vissim came back with 9), and constraint #6's
    written != readback compare is what must then refuse."""
    working = _write_working_inpx(tmp_path)
    session = FakeParkingSession(
        working, readback={"parking_lot": _lot_readback(capacity=9, length=54.0)}
    )
    set_active_edit_session(session)

    envelope = _dispatch("add_parking_lot", link_no="1", lane_index=1, pos_m=100.0, capacity=10)

    assert envelope["verification"]["written_value"]["capacity"] == 10
    assert envelope["verification"]["readback_value"]["capacity"] == 9


def test_add_parking_lot_len_per_space_only_becomes_part_of_the_claim_when_asked(tmp_path: Path):
    """Mirrors add_pt_stop's own length_m precedent: an omitted field, where
    Vissim silently fills its own default, must never become an unverifiable
    "the caller asked for this exact value" claim."""
    working = _write_working_inpx(tmp_path)
    session = FakeParkingSession(working, readback={"parking_lot": _lot_readback()})
    set_active_edit_session(session)

    omitted = _dispatch("add_parking_lot", link_no="1", lane_index=1, pos_m=100.0, capacity=10)
    assert "len_per_space" not in omitted["verification"]["written_value"]

    given = _dispatch(
        "add_parking_lot", link_no="1", lane_index=1, pos_m=100.0, capacity=10, len_per_space_m=6.0
    )
    assert given["verification"]["written_value"]["len_per_space"] == 6.0
    assert given["verification"]["readback_value"]["len_per_space"] == 6.0


def test_add_parking_lot_never_claims_the_desired_speed_distribution(tmp_path: Path):
    """Vissim picks ``DesSpeedDistrDef`` itself (measured: the network's lowest
    speed-distribution id -- 15 on Beijing, 1 on Camden). It is REPORTED so the
    caller knows, but it is not the caller's choice, so it must stay out of the
    verified pair (Q4 unit (4)'s "claim only what the caller supplied")."""
    working = _write_working_inpx(tmp_path)
    session = FakeParkingSession(working, readback={"parking_lot": _lot_readback()})
    set_active_edit_session(session)

    envelope = _dispatch("add_parking_lot", link_no="1", lane_index=1, pos_m=100.0, capacity=10)

    assert "des_speed_distr" not in str(envelope["verification"])
    assert envelope["artifacts"]["des_speed_distr_no"] == "15"
    assert "distribution 15" in envelope["message"]


def test_add_parking_lot_passes_name_and_len_per_space_through(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    session = FakeParkingSession(working, readback={"parking_lot": _lot_readback(name="P1")})
    set_active_edit_session(session)

    _dispatch(
        "add_parking_lot", link_no="1", lane_index=1, pos_m=100.0, capacity=10,
        len_per_space_m=5.5, name="P1",
    )

    assert session.calls[0]["len_per_space_m"] == 5.5
    assert session.calls[0]["name"] == "P1"


def test_add_parking_lot_rejects_capacity_below_one():
    """A zero-length lot is SILENTLY ACCEPTED by Vissim (measured: capacity 0,
    load_check still green), so the floor is enforced before anything runs."""
    envelope = _dispatch("add_parking_lot", link_no="1", lane_index=1, pos_m=10.0, capacity=0)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_add_parking_lot_rejects_lane_index_below_one():
    envelope = _dispatch("add_parking_lot", link_no="1", lane_index=0, pos_m=10.0, capacity=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_add_parking_lot_rejects_unexpected_params():
    envelope = _dispatch(
        "add_parking_lot", link_no="1", lane_index=1, pos_m=10.0, capacity=1, bogus="nope"
    )

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


def test_add_parking_lot_no_active_session_yields_structured_error():
    envelope = _dispatch("add_parking_lot", link_no="1", lane_index=1, pos_m=10.0, capacity=1)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "tool_execution_error"
    assert "no_edit_session" in envelope["message"]


# ---------------------------------------------------------------------------
# set_parking_lot_capacity
# ---------------------------------------------------------------------------


def test_set_parking_lot_capacity_happy_path(tmp_path: Path):
    working = _write_working_inpx(tmp_path, extra=_REAL_PARKING_SPACE_XML)
    session = FakeParkingSession(
        working, readback={"parking_lot": _lot_readback(no="14", capacity=20, length=120.0)}
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_parking_lot_capacity", parking_lot_no="14", capacity=20)

    assert envelope["status"] == "ok"
    # BARE SCALARS: this tool has a _WRITE_TARGET_FIELD_SPEC entry, so the
    # target-persisted check compares written_value against the shadow model's
    # own int ``capacity`` field -- a dict could never equal it, and every
    # correct call was refused until that was fixed (live-caught,
    # parking_lot_live_check.py Phase B.2).
    assert envelope["verification"] == {"written_value": 20, "readback_value": 20}
    assert envelope["artifacts"]["capacity"] == 20
    assert "20 space(s)" in envelope["message"]
    assert session.persist_calls == 1
    assert session.calls == [
        {
            "op": "set_parking_lot_capacity", "parking_lot_no": 14, "capacity": 20,
            "len_per_space_m": None,
        }
    ]


def test_set_parking_lot_capacity_surfaces_a_disagreeing_readback(tmp_path: Path):
    working = _write_working_inpx(tmp_path, extra=_REAL_PARKING_SPACE_XML)
    session = FakeParkingSession(
        working, readback={"parking_lot": _lot_readback(no="14", capacity=19)}
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_parking_lot_capacity", parking_lot_no="14", capacity=20)

    assert envelope["verification"]["written_value"] == 20
    assert envelope["verification"]["readback_value"] == 19


def test_set_parking_lot_capacity_rejects_capacity_below_one():
    envelope = _dispatch("set_parking_lot_capacity", parking_lot_no="14", capacity=0)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


# ---------------------------------------------------------------------------
# remove_parking_lot -- and the cascade refusal that is its whole point
# ---------------------------------------------------------------------------


def test_remove_parking_lot_happy_path_when_unreferenced(tmp_path: Path):
    working = _write_working_inpx(tmp_path, extra=_REAL_PARKING_SPACE_XML)
    session = FakeParkingSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_parking_lot", no="14")

    assert envelope["status"] == "ok"
    assert envelope["verification"] == {
        "written_value": {"removed_no": "14", "still_present": False},
        "readback_value": {"removed_no": "14", "still_present": False},
    }
    assert "Parking lot 14 removed" in envelope["message"]
    assert session.persist_calls == 1


def test_remove_parking_lot_refuses_when_a_parking_route_still_references_it(tmp_path: Path):
    """The flagship refusal. Vissim's own RemoveParkingLot does NOT dangle the
    reference -- it SILENTLY CASCADE-DELETES the referencing route (measured on
    3D - Complex Intersection Karlsruhe.DE: persisted counts moved
    {'parking_lots': -1, 'parking_routes': -1}, no exception, load_check
    green). Nothing must reach COM here."""
    working = _write_working_inpx(
        tmp_path, extra=_REAL_PARKING_SPACE_XML + _PARKING_ROUTE_XML
    )
    session = FakeParkingSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_parking_lot", no="14")

    assert envelope["status"] == "error"
    assert "still referenced by" in envelope["message"]
    assert "parking route" in envelope["message"]
    # The refusal happened BEFORE COM and BEFORE any persist.
    assert session.calls == []
    assert session.persist_calls == 0
    # ...and the lot is still there.
    root = ET.parse(working).getroot()
    assert [el.get("no") for el in root.findall(".//parkingLots/parkingLot")] == ["14"]


def test_remove_parking_lot_refusal_stays_under_the_dispatch_cap(tmp_path: Path):
    """toolkit.dispatch truncates exception text at 300 chars (Q6 unit (1)'s
    measured cap), so a refusal has to fit whole or lose its actionable half
    mid-word."""
    working = _write_working_inpx(
        tmp_path, extra=_REAL_PARKING_SPACE_XML + _PARKING_ROUTE_XML
    )
    session = FakeParkingSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_parking_lot", no="14")

    assert len(envelope["message"]) < 300


def test_remove_parking_lot_rejects_unexpected_params():
    envelope = _dispatch("remove_parking_lot", no="14", bogus="nope")

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "invalid_params"


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


def _shadow(links: list[dict[str, Any]] | None = None, lots: list[dict[str, Any]] | None = None):
    return {
        "links": links if links is not None else [{"id": "1", "name": "Approach", "length": 400.0}],
        "parking_lots": lots or [],
    }


def _action(tool: str, **params: Any) -> dict[str, Any]:
    return {"tool": tool, "params": params}


@pytest.mark.parametrize("capacity", [0, -3, 10001, 999999])
def test_parking_lot_capacity_unrealistic_refuses_out_of_range(capacity: int):
    findings = constraints.run_checks(
        "preflight",
        _action("add_parking_lot", link_no="1", lane_index=1, pos_m=0.0, capacity=capacity),
        _shadow(),
        {},
    )
    ids = {f.constraint_id: f for f in findings}

    assert "parking_lot_capacity_unrealistic" in ids
    assert ids["parking_lot_capacity_unrealistic"].tier == "refuse"
    assert ids["parking_lot_capacity_unrealistic"].suggested_fix["max_capacity"] == 10000


@pytest.mark.parametrize("capacity", [1, 10, 10000])
def test_parking_lot_capacity_unrealistic_stays_silent_in_range(capacity: int):
    findings = constraints.run_checks(
        "preflight",
        _action("add_parking_lot", link_no="1", lane_index=1, pos_m=0.0, capacity=capacity),
        _shadow(links=[{"id": "1", "name": "", "length": 1_000_000.0}]),
        {},
    )

    assert "parking_lot_capacity_unrealistic" not in {f.constraint_id for f in findings}


def test_parking_lot_capacity_unrealistic_applies_to_the_setter_too():
    findings = constraints.run_checks(
        "preflight",
        _action("set_parking_lot_capacity", parking_lot_no="14", capacity=0),
        _shadow(lots=[{"no": "14", "link": "1", "pos": 0.0}]),
        {},
    )

    assert "parking_lot_capacity_unrealistic" in {f.constraint_id for f in findings}


def test_parking_lot_does_not_fit_link_refuses_and_names_the_largest_that_does():
    """400 m link, lot starting at 100 m, 6 m per space -> 50 spaces fit."""
    findings = constraints.run_checks(
        "preflight",
        _action("add_parking_lot", link_no="1", lane_index=1, pos_m=100.0, capacity=60),
        _shadow(),
        {},
    )
    ids = {f.constraint_id: f for f in findings}

    assert "parking_lot_does_not_fit_link" in ids
    finding = ids["parking_lot_does_not_fit_link"]
    assert finding.tier == "refuse"
    assert finding.suggested_fix["max_capacity"] == 50
    assert finding.details["required_m"] == pytest.approx(460.0)
    assert finding.details["link_length_m"] == pytest.approx(400.0)
    assert "400.0 m" in finding.messages["en"]


def test_parking_lot_does_not_fit_link_stays_silent_when_it_fits():
    findings = constraints.run_checks(
        "preflight",
        _action("add_parking_lot", link_no="1", lane_index=1, pos_m=100.0, capacity=50),
        _shadow(),
        {},
    )

    assert "parking_lot_does_not_fit_link" not in {f.constraint_id for f in findings}


def test_parking_lot_does_not_fit_link_uses_an_explicit_len_per_space():
    findings = constraints.run_checks(
        "preflight",
        _action(
            "add_parking_lot", link_no="1", lane_index=1, pos_m=0.0, capacity=100,
            len_per_space_m=5.0,
        ),
        _shadow(),
        {},
    )
    ids = {f.constraint_id: f for f in findings}

    assert "parking_lot_does_not_fit_link" in ids
    assert ids["parking_lot_does_not_fit_link"].suggested_fix["max_capacity"] == 80


def test_parking_lot_does_not_fit_link_reads_the_setters_own_lot_position():
    """For the SETTER the position and link come from the EXISTING lot, not
    from params -- a lot 380 m along a 400 m link has room for 3 more 6 m
    spaces, not 66."""
    findings = constraints.run_checks(
        "preflight",
        _action("set_parking_lot_capacity", parking_lot_no="14", capacity=10),
        _shadow(lots=[{"no": "14", "name": "", "link": "1", "pos": 380.0}]),
        {},
    )
    ids = {f.constraint_id: f for f in findings}

    assert "parking_lot_does_not_fit_link" in ids
    assert ids["parking_lot_does_not_fit_link"].suggested_fix["max_capacity"] == 3


def test_parking_lot_does_not_fit_link_is_silent_for_an_unknown_link():
    """object_not_found (#1) owns a bogus link -- this check must not double up
    with a second, confusing refusal."""
    findings = constraints.run_checks(
        "preflight",
        _action("add_parking_lot", link_no="404", lane_index=1, pos_m=0.0, capacity=5),
        _shadow(),
        {},
    )

    assert "parking_lot_does_not_fit_link" not in {f.constraint_id for f in findings}


def test_parking_lot_inert_warns_on_a_network_with_no_parking_lots():
    findings = constraints.run_checks(
        "preflight",
        _action("add_parking_lot", link_no="1", lane_index=1, pos_m=0.0, capacity=5),
        _shadow(),
        {},
    )
    ids = {f.constraint_id: f for f in findings}

    assert "parking_lot_inert_without_parking_routing_decision" in ids
    finding = ids["parking_lot_inert_without_parking_routing_decision"]
    assert finding.tier == "warn"
    assert "vehicleRoutingDecisionParking" in finding.messages["en"]
    assert finding.messages["ko"]
    assert finding.messages["ja"]


def test_parking_lot_inert_stays_silent_when_the_network_already_has_lots():
    findings = constraints.run_checks(
        "preflight",
        _action("add_parking_lot", link_no="1", lane_index=1, pos_m=0.0, capacity=5),
        _shadow(lots=[{"no": "14", "name": "", "link": "1", "pos": 0.0}]),
        {},
    )

    assert "parking_lot_inert_without_parking_routing_decision" not in {
        f.constraint_id for f in findings
    }


def test_object_not_found_fires_for_a_bogus_parking_lot_id():
    """The #1 wiring, at birth (Q5 unit (3)'s gap-closure lesson: a
    _TOOL_TARGET_SPEC entry alone does NOTHING without the applies_to entry)."""
    findings = constraints.run_checks(
        "preflight",
        _action("set_parking_lot_capacity", parking_lot_no="999", capacity=5),
        _shadow(lots=[{"no": "14", "name": "Deck", "link": "1", "pos": 0.0}]),
        {},
    )
    ids = {f.constraint_id: f for f in findings}

    assert "object_not_found" in ids
    assert ids["object_not_found"].details["object_class"] == "parking_lots"


@pytest.mark.parametrize(
    "tool,params",
    [
        ("add_parking_lot", {"link_no": "404", "lane_index": 1, "pos_m": 0.0, "capacity": 5}),
        ("set_parking_lot_capacity", {"parking_lot_no": "999", "capacity": 5}),
        ("remove_parking_lot", {"no": "999"}),
    ],
)
def test_every_parking_verb_is_wired_into_object_not_found(tool: str, params: dict[str, Any]):
    findings = constraints.run_checks(
        "preflight",
        _action(tool, **params),
        _shadow(lots=[{"no": "14", "name": "Deck", "link": "1", "pos": 0.0}]),
        {},
    )

    assert "object_not_found" in {f.constraint_id for f in findings}


@pytest.mark.parametrize(
    "tool,params",
    [
        ("add_parking_lot", {"link_no": "1", "lane_index": 1, "pos_m": 0.0, "capacity": 5}),
        ("set_parking_lot_capacity", {"parking_lot_no": "14", "capacity": 5}),
        ("remove_parking_lot", {"no": "14"}),
    ],
)
def test_object_not_found_stays_silent_for_real_parking_ids(tool: str, params: dict[str, Any]):
    findings = constraints.run_checks(
        "preflight",
        _action(tool, **params),
        _shadow(lots=[{"no": "14", "name": "Deck", "link": "1", "pos": 0.0}]),
        {},
    )

    assert "object_not_found" not in {f.constraint_id for f in findings}


# ---------------------------------------------------------------------------
# Guardrail table wiring
# ---------------------------------------------------------------------------


def test_parking_lots_is_a_tracked_shadow_object_class():
    assert _SHADOW_OBJECT_ID_FIELDS["parking_lots"] == "no"


def test_set_parking_lot_capacity_targets_the_derived_capacity_field():
    spec = _WRITE_TARGET_FIELD_SPEC["set_parking_lot_capacity"]

    assert spec["object_class"] == "parking_lots"
    assert spec["id_param"] == "parking_lot_no"
    assert spec["value_field"] == "capacity"
    # length/len_per_space necessarily move with it -- measured, and exempting
    # capacity alone would false-positive every correct call.
    assert set(spec["coupled_fields"]) == {"length", "len_per_space"}


def test_add_and_remove_opt_in_exactly_the_parking_lots_class():
    """Measured as exactly one row in each direction (probe Parts A and D):
    no byproduct class, unlike add_signal_controller's 44 splits or
    add_vehicle_input's materialised composition entry. In particular "zones"
    is NOT opted in -- a Type write mints one, but no verb here writes Type,
    so a zone appearing during a parking write must stay loud evidence."""
    assert _ALLOWS_NEW_OBJECTS_IN["add_parking_lot"] == frozenset({"parking_lots"})
    assert _ALLOWS_REMOVED_OBJECTS_IN["remove_parking_lot"] == frozenset({"parking_lots"})
    assert "zones" not in _ALLOWS_NEW_OBJECTS_IN["add_parking_lot"]
    assert "parking_routes" not in _ALLOWS_REMOVED_OBJECTS_IN["remove_parking_lot"]


def test_parking_verbs_are_write_tier():
    tiers = {
        spec.name: spec.tier
        for spec in default_registry.specs()
        if spec.name.endswith("parking_lot") or "parking_lot" in spec.name
    }

    assert tiers["add_parking_lot"] == "write"
    assert tiers["set_parking_lot_capacity"] == "write"
    assert tiers["remove_parking_lot"] == "write"
    assert tiers["get_parking_lots"] == "read"


# ---------------------------------------------------------------------------
# REAL-ENGINE postwrite -- the "one real-engine postwrite test per verb unit"
# pattern Q4 unit (3) recorded after every unit-② remove verb shipped broken
# because no test ever drove GuardrailEngine.postwrite with a real payload.
#
# The capacity test below is not hypothetical: it FAILS against the shape this
# unit first shipped (``written_value={"capacity": 25}``), because the
# target-persisted check compares that against the shadow model's own int
# ``capacity`` and a dict can never equal an int. Live-caught by
# backend/tools/parking_lot_live_check.py Phase B.2 on a real Beijing clone.
# ---------------------------------------------------------------------------


def _shadow_with_lots(lots: list[dict[str, Any]]) -> dict[str, Any]:
    """A shadow model carrying every class the postwrite diff tracks that this
    unit can touch, so a difference in ``parking_lots`` is the only thing the
    engine can possibly see."""
    return {
        "links": [{"id": "1", "name": "Approach", "length": 400.0, "lanes": 2}],
        "parking_lots": lots,
    }


def _full_lot(no: str, capacity: int, length: float, len_per_space: float = 6.0) -> dict[str, Any]:
    return _lot_readback(no=no, capacity=capacity, length=length, len_per_space=len_per_space)


def _engine():
    from services.agent.guardrails import GuardrailEngine

    return GuardrailEngine(disabled=False)


def test_real_engine_postwrite_accepts_a_correct_add():
    before = _shadow_with_lots([])
    after = _shadow_with_lots([_full_lot("1", 10, 60.0)])
    action = _action("add_parking_lot", link_no="1", lane_index=1, pos_m=100.0, capacity=10)
    verification = {
        "written_value": {"link": "1", "lane_index": 1, "pos": 100.0, "capacity": 10},
        "readback_value": {"link": "1", "lane_index": 1, "pos": 100.0, "capacity": 10},
    }

    report = _engine().postwrite(action, before, after, verification)

    assert report.blocking is False


def test_real_engine_postwrite_refuses_an_add_when_the_exemption_is_popped():
    """The counterfactual, in-process: without
    ``_ALLOWS_NEW_OBJECTS_IN['add_parking_lot']`` the brand-new lot is
    unexplained evidence and a CORRECT call is refused. Run live too
    (parking_lot_live_check.py A.4) -- pinned here so a table edit cannot
    silently drop it."""
    import services.agent.guardrails as guardrails_module

    before = _shadow_with_lots([])
    after = _shadow_with_lots([_full_lot("1", 10, 60.0)])
    action = _action("add_parking_lot", link_no="1", lane_index=1, pos_m=100.0, capacity=10)
    verification = {
        "written_value": {"link": "1", "lane_index": 1, "pos": 100.0, "capacity": 10},
        "readback_value": {"link": "1", "lane_index": 1, "pos": 100.0, "capacity": 10},
    }

    table = guardrails_module._ALLOWS_NEW_OBJECTS_IN
    original = table.pop("add_parking_lot")
    try:
        report = _engine().postwrite(action, before, after, verification)
    finally:
        table["add_parking_lot"] = original

    assert report.blocking is True
    assert "write_verification_failed" in {f.constraint_id for f in report.findings}


def test_real_engine_postwrite_accepts_a_correct_capacity_change():
    """The regression guard for the live-caught dict-vs-scalar defect: a
    ``written_value`` that is not directly comparable to the shadow model's own
    ``capacity`` int makes the target-persisted check refuse every correct
    call."""
    before = _shadow_with_lots([_full_lot("1", 10, 60.0)])
    after = _shadow_with_lots([_full_lot("1", 25, 150.0)])
    action = _action("set_parking_lot_capacity", parking_lot_no="1", capacity=25)
    verification = {"written_value": 25, "readback_value": 25}

    report = _engine().postwrite(action, before, after, verification)

    assert report.blocking is False


def test_real_engine_postwrite_refuses_a_capacity_change_the_file_did_not_take():
    """The other half: the saved file showing a DIFFERENT capacity than the one
    written is exactly what this check exists to catch."""
    before = _shadow_with_lots([_full_lot("1", 10, 60.0)])
    after = _shadow_with_lots([_full_lot("1", 10, 60.0)])
    action = _action("set_parking_lot_capacity", parking_lot_no="1", capacity=25)
    verification = {"written_value": 25, "readback_value": 25}

    report = _engine().postwrite(action, before, after, verification)

    assert report.blocking is True
    assert "write_verification_failed" in {f.constraint_id for f in report.findings}


def test_real_engine_postwrite_refuses_a_capacity_change_without_its_coupled_fields():
    """Length necessarily moves with capacity (it is what the tool actually
    writes), so exempting ``capacity`` alone turns every correct call into
    "something else moved" -- run live too (parking_lot_live_check.py B.3)."""
    import services.agent.guardrails as guardrails_module

    before = _shadow_with_lots([_full_lot("1", 10, 60.0)])
    after = _shadow_with_lots([_full_lot("1", 25, 150.0)])
    action = _action("set_parking_lot_capacity", parking_lot_no="1", capacity=25)
    verification = {"written_value": 25, "readback_value": 25}

    spec = guardrails_module._WRITE_TARGET_FIELD_SPEC["set_parking_lot_capacity"]
    original = spec["coupled_fields"]
    try:
        spec["coupled_fields"] = ()
        report = _engine().postwrite(action, before, after, verification)
    finally:
        spec["coupled_fields"] = original

    assert report.blocking is True


def test_real_engine_postwrite_accepts_a_correct_removal():
    before = _shadow_with_lots([_full_lot("1", 10, 60.0)])
    after = _shadow_with_lots([])
    action = _action("remove_parking_lot", no="1")
    verification = {
        "written_value": {"removed_no": "1", "still_present": False},
        "readback_value": {"removed_no": "1", "still_present": False},
    }

    report = _engine().postwrite(action, before, after, verification)

    assert report.blocking is False


def test_real_engine_postwrite_refuses_a_removal_when_the_exemption_is_popped():
    import services.agent.guardrails as guardrails_module

    before = _shadow_with_lots([_full_lot("1", 10, 60.0)])
    after = _shadow_with_lots([])
    action = _action("remove_parking_lot", no="1")
    verification = {
        "written_value": {"removed_no": "1", "still_present": False},
        "readback_value": {"removed_no": "1", "still_present": False},
    }

    table = guardrails_module._ALLOWS_REMOVED_OBJECTS_IN
    original = table.pop("remove_parking_lot")
    try:
        report = _engine().postwrite(action, before, after, verification)
    finally:
        table["remove_parking_lot"] = original

    assert report.blocking is True


def test_the_capacity_tools_OWN_verification_payload_survives_the_real_engine(tmp_path: Path):
    """The test that actually bites the live-caught defect: it takes the
    verification payload the TOOL itself produces (not a hand-written one) and
    feeds it to the real GuardrailEngine. Restoring the ``{"capacity": 25}``
    dict shape in tools_edit makes this fail with write_verification_failed,
    because the target-persisted check compares that payload against the shadow
    model's own int ``capacity``."""
    working = _write_working_inpx(tmp_path, extra=_REAL_PARKING_SPACE_XML)
    session = FakeParkingSession(
        working, readback={"parking_lot": _lot_readback(no="14", capacity=25, length=150.0)}
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_parking_lot_capacity", parking_lot_no="14", capacity=25)
    assert envelope["status"] == "ok"

    before = _shadow_with_lots([_full_lot("14", 10, 60.0)])
    after = _shadow_with_lots([_full_lot("14", 25, 150.0)])
    action = _action("set_parking_lot_capacity", parking_lot_no="14", capacity=25)

    report = _engine().postwrite(action, before, after, envelope["verification"])

    assert report.blocking is False, [f.constraint_id for f in report.findings]


# ---------------------------------------------------------------------------
# ID NORMALISATION -- the guard must see the same value the action does
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["0014", " 14", "+14", "14 ", "14"])
def test_remove_parking_lot_refuses_every_spelling_of_a_referenced_id(
    spelling: str, tmp_path: Path
):
    """The measured bypass. ``find_id_references`` compares
    ``value == str(object_id)``, so passing the caller's RAW string made
    "0014"/" 14"/"+14"/"14 " miss the referrer lookup entirely, sail past the
    refusal, reach COM, and trigger the silent cascade-delete of the
    referencing parking route -- the exact thing this guard exists to stop.
    Preflight's object_not_found shields the orchestrated pipeline, but a
    direct dispatch has no such shield."""
    working = _write_working_inpx(tmp_path, extra=_REAL_PARKING_SPACE_XML + _PARKING_ROUTE_XML)
    session = FakeParkingSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_parking_lot", no=spelling)

    assert envelope["status"] == "error"
    assert "still referenced by" in envelope["message"]
    assert "parking route" in envelope["message"]
    # Nothing reached COM, nothing was persisted, the lot is still there.
    assert session.calls == []
    assert session.persist_calls == 0
    root = ET.parse(working).getroot()
    assert [el.get("no") for el in root.findall(".//parkingLots/parkingLot")] == ["14"]


@pytest.mark.parametrize("spelling", ["0014", " 14", "14"])
def test_remove_parking_lot_reports_the_canonical_id_for_any_spelling(
    spelling: str, tmp_path: Path
):
    """An unreferenced lot removes cleanly whatever the spelling, and the
    message/verification carry the CANONICAL id -- so a "0014" call cannot
    produce a verification payload keyed on a string the shadow model has
    never heard of."""
    working = _write_working_inpx(tmp_path, extra=_REAL_PARKING_SPACE_XML)
    session = FakeParkingSession(working)
    set_active_edit_session(session)

    envelope = _dispatch("remove_parking_lot", no=spelling)

    assert envelope["status"] == "ok"
    assert envelope["message"] == "Parking lot 14 removed."
    assert envelope["verification"]["written_value"]["removed_no"] == "14"
    assert session.calls == [{"op": "remove_parking_lot", "no": 14}]


def test_add_parking_lot_claims_the_canonical_link_id(tmp_path: Path):
    """COM reads "1" back for a "01" argument, so an uncanonicalised
    written_value would make constraint #6 refuse a correct write over a
    formatting difference."""
    working = _write_working_inpx(tmp_path)
    session = FakeParkingSession(working, readback={"parking_lot": _lot_readback()})
    set_active_edit_session(session)

    envelope = _dispatch("add_parking_lot", link_no="01", lane_index=1, pos_m=100.0, capacity=10)

    assert envelope["status"] == "ok"
    assert envelope["verification"]["written_value"]["link"] == "1"
    assert envelope["verification"]["readback_value"]["link"] == "1"
    assert "on link 1 lane 1" in envelope["message"]


def test_set_parking_lot_capacity_reports_the_canonical_id(tmp_path: Path):
    working = _write_working_inpx(tmp_path, extra=_REAL_PARKING_SPACE_XML)
    session = FakeParkingSession(
        working, readback={"parking_lot": _lot_readback(no="14", capacity=20, length=120.0)}
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_parking_lot_capacity", parking_lot_no="0014", capacity=20)

    assert envelope["status"] == "ok"
    assert envelope["message"].startswith("Parking lot 14 ")
    assert session.calls[0]["parking_lot_no"] == 14


def test_set_parking_lot_capacity_surfaces_the_species_refusal(tmp_path: Path):
    """The COM layer refuses a non-REALPARKINGSPACE by name; the verb must let
    that reach the caller as a clear structured error rather than swallowing
    it, and nothing may be persisted."""
    from services.vissim_com import VissimParkingLotTypeError

    working = _write_working_inpx(tmp_path, extra=_ZONE_CONNECTOR_XML)
    session = FakeParkingSession(
        working,
        readback={"parking_lot": _lot_readback(no="26")},
        raise_on={
            "set_parking_lot_capacity": VissimParkingLotTypeError(
                "Parking lot 26 is a ZONECONNECTOR, not a REALPARKINGSPACE: its capacity is "
                "stored independently, not derived from length/length-per-space, so this verb "
                "cannot change it. Edit that species in the Vissim GUI."
            )
        },
    )
    set_active_edit_session(session)

    envelope = _dispatch("set_parking_lot_capacity", parking_lot_no="26", capacity=50)

    assert envelope["status"] == "error"
    assert "ZONECONNECTOR" in envelope["message"]
    assert len(envelope["message"]) < 300
    assert session.persist_calls == 0

"""Tests for Q6 unit (3)'s pedestrian (Viswalk) surface: ``tools_edit``'s
add_pedestrian_input / set_pedestrian_input_volume / remove_pedestrian_input,
dispatched through the REAL toolkit.dispatch (so pydantic validation + the
envelope machinery are exercised exactly like production), plus the two read
tools, the three constraints, and the shadow-model parsers those verbs stand
on.

House shape = test_agent_tools_parking_lot.py (the closest sibling: a
create/remove pair on a class that had no representation at all before its
unit). The fake session owns a real on-disk ``working_inpx`` because
``remove_pedestrian_input``'s post-persist "is it really gone" readback
(``tools_edit._still_present_after_remove``) reparses that file, and because
``add_pedestrian_input``'s routing guard and its interval-count report both
read the working copy's genuine XML.

THE LICENCE QUESTION was settled before any of this was written: this box
creates and simulates Viswalk objects, so these are tests of real write verbs.
No real Vissim COM anywhere in this file -- live truth comes from
``backend/tools/probe_pedestrian.py`` (eighteen parts, null control, --audit)
and ``backend/tools/pedestrian_live_check.py`` (the real-engine E2E +
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

# A miniature but STRUCTURALLY REAL Viswalk layer, shaped exactly like the
# corpus: areas carry <geometry><points>, pedestrian inputs carry <location
# area=..><posOnArea/></location> plus a <timeIntPedVols> child, and routing
# decisions carry their own <location area=..>. Area 1 is routed, area 2 is
# NOT -- the same split Beijing has (8 routed, 8 unrouted), which is what
# makes the refuse-tier constraint testable on a shipped-and-connected area
# rather than only on a freshly created one.
_NETWORK_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<network>
<links>
<link no="1" name="Approach"><lanes><lane width="3.5"/></lanes>
<geometry><linkPolyPts><linkPolyPoint x="0" y="0"/><linkPolyPoint x="400" y="0"/></linkPolyPts></geometry>
</link>
</links>
<areas>
<area no="1" name="North kerb" level="1" isQueue="false" alwUseAsOrig="false" alwUseAsDest="false">
<geometry><points><point x="-10" y="0"/><point x="-8" y="0"/><point x="-8" y="2"/><point x="-10" y="2"/></points></geometry>
</area>
<area no="2" name="Island" level="1" isQueue="false" alwUseAsOrig="false" alwUseAsDest="false">
<geometry><points><point x="10" y="0"/><point x="12" y="0"/><point x="12" y="2"/><point x="10" y="2"/></points></geometry>
</area>
<area no="7" name="South kerb" level="1" isQueue="true" alwUseAsOrig="true" alwUseAsDest="true" areaBehavType="2" tmDistr="5" elevGrp="3">
<geometry><points><point x="0" y="-10"/><point x="2" y="-10"/><point x="2" y="-8"/></points></geometry>
</area>
</areas>
<pedestrianRoutingDecisionsStatic>
<pedestrianRoutingDecisionStatic no="1" name=""><location area="1"><posOnArea x="0" y="0"/></location></pedestrianRoutingDecisionStatic>
<pedestrianRoutingDecisionStatic no="2" name=""><location area="7"><posOnArea x="0" y="0"/></location></pedestrianRoutingDecisionStatic>
</pedestrianRoutingDecisionsStatic>
<pedestrianInputs>
{inputs}
</pedestrianInputs>
</network>
"""

_ONE_INTERVAL_INPUT = """<pedestrianInput no="3" name="Existing">
<location area="1"><posOnArea x="0.25" y="-0.5"/></location>
<timeIntPedVols><timeIntervalPedVolume cont="false" pedComp="1" timeInt="5 0" volType="STOCHASTIC" volume="450"/></timeIntPedVols>
</pedestrianInput>"""

_FOURTEEN_INTERVAL_INPUT = (
    '<pedestrianInput no="3" name="Existing">\n'
    '<location area="1"><posOnArea x="0" y="0"/></location>\n'
    "<timeIntPedVols>"
    + "".join(
        f'<timeIntervalPedVolume cont="false" pedComp="1" timeInt="5 {i * 100}" '
        f'volType="STOCHASTIC" volume="{450 if i == 0 else 0}"/>'
        for i in range(14)
    )
    + "</timeIntPedVols>\n</pedestrianInput>"
)


def _write_working_inpx(tmp_path: Path, inputs: str = _ONE_INTERVAL_INPUT) -> Path:
    path = tmp_path / "working.inpx"
    path.write_text(_NETWORK_TEMPLATE.format(inputs=inputs), encoding="utf-8")
    return path


def _remove_input_from_disk(path: Path, no: Any) -> None:
    """Simulate a real COM RemovePedestrianInput + persist(): mutate the
    on-disk working copy so the post-remove readback sees genuinely removed
    state, not a stale fixture."""
    tree = ET.parse(path)
    root = tree.getroot()
    container = root.find("pedestrianInputs")
    if container is None:
        return
    for el in list(container.findall("pedestrianInput")):
        if el.get("no") == str(no):
            container.remove(el)
    tree.write(path, encoding="UTF-8", xml_declaration=True)


def _add_input_to_disk(path: Path, no: Any, area: Any, volume: float, intervals: int) -> None:
    """Simulate a real COM AddPedestrianInput + persist(). ``intervals``
    reproduces the measured behaviour that the NETWORK's interval structure is
    inherited whole by a created input: interval 1 carries the requested
    volume, the rest arrive at zero."""
    tree = ET.parse(path)
    root = tree.getroot()
    container = root.find("pedestrianInputs")
    if container is None:
        container = ET.SubElement(root, "pedestrianInputs")
    el = ET.SubElement(container, "pedestrianInput", {"no": str(no), "name": ""})
    location = ET.SubElement(el, "location", {"area": str(area)})
    ET.SubElement(location, "posOnArea", {"x": "0", "y": "0"})
    vols = ET.SubElement(el, "timeIntPedVols")
    for index in range(intervals):
        ET.SubElement(
            vols,
            "timeIntervalPedVolume",
            {
                "cont": "false",
                "pedComp": "1",
                "timeInt": f"5 {index * 100}",
                "volType": "STOCHASTIC",
                "volume": str(volume if index == 0 else 0),
            },
        )
    tree.write(path, encoding="UTF-8", xml_declaration=True)


class FakePedestrianSession:
    """Duck-types VissimEditSession's pedestrian surface: records every call,
    returns a rigged readback for add/set, mutates the real on-disk working
    copy for both add and remove so every post-persist reparse sees truth."""

    def __init__(
        self,
        working_inpx: Path,
        readback: dict[str, Any] | None = None,
        raise_on: dict[str, Exception] | None = None,
        network_intervals: int = 1,
    ) -> None:
        self.working_inpx = working_inpx
        self.calls: list[dict[str, Any]] = []
        self._readback = readback or {}
        self._raise_on = raise_on or {}
        self._network_intervals = network_intervals
        self.persist_calls = 0
        self.closed = False

    def _record(self, op: str, **kwargs: Any) -> None:
        self.calls.append({"op": op, **kwargs})
        if op in self._raise_on:
            raise self._raise_on[op]

    def add_pedestrian_input(
        self,
        area_no: int,
        volume_pph: float,
        name: str = "",
        composition_no: int | None = None,
    ) -> dict[str, Any]:
        self._record(
            "add_pedestrian_input",
            area_no=area_no,
            volume_pph=volume_pph,
            name=name,
            composition_no=composition_no,
        )
        new_no = self._readback.get("no", "9")
        _add_input_to_disk(
            self.working_inpx, new_no, area_no, volume_pph, self._network_intervals
        )
        return {
            "no": str(new_no),
            "name": name,
            "area": str(area_no),
            "volume": float(volume_pph),
            "composition_no": str(composition_no or 1),
            "volume_type": "STOCHASTIC",
            **{k: v for k, v in self._readback.items() if k != "no"},
        }

    def set_pedestrian_input_volume(self, no: int, volume_pph: float) -> dict[str, Any]:
        self._record("set_pedestrian_input_volume", no=no, volume_pph=volume_pph)
        return {
            "no": str(no),
            "name": "Existing",
            "area": "1",
            "volume": float(self._readback.get("volume", volume_pph)),
            "composition_no": "1",
            "volume_type": "STOCHASTIC",
        }

    def remove_pedestrian_input(self, no: int) -> None:
        self._record("remove_pedestrian_input", no=no)
        _remove_input_from_disk(self.working_inpx, no)

    def persist(self) -> None:
        self.persist_calls += 1


@pytest.fixture
def session(tmp_path: Path):
    working = _write_working_inpx(tmp_path)
    fake = FakePedestrianSession(working)
    set_active_edit_session(fake)
    tools_network.set_active_inpx(working)
    yield fake
    clear_active_edit_session()
    tools_network.set_active_inpx(None)


# ---------------------------------------------------------------------------
# Representation -- the parsers, which had to land before any verb.
# ---------------------------------------------------------------------------


def test_shadow_model_carries_both_pedestrian_classes(tmp_path: Path):
    """Before this unit the shadow model had no pedestrian class of any kind,
    and probe parts A/E/G/H/J each recorded an empty diff for edits whose
    persisted counts demonstrably moved."""
    model = tools_network.build_shadow_model(_write_working_inpx(tmp_path))

    assert [entry["no"] for entry in model["pedestrian_inputs"]] == ["3"]
    assert [entry["no"] for entry in model["pedestrian_areas"]] == ["1", "2", "7"]


def test_pedestrian_input_parser_flattens_location_and_interval_one(tmp_path: Path):
    model = tools_network.build_shadow_model(_write_working_inpx(tmp_path))
    entry = model["pedestrian_inputs"][0]

    assert entry["area"] == "1"
    assert entry["pos_x"] == 0.25 and entry["pos_y"] == -0.5
    assert entry["volume"] == 450.0
    assert entry["composition_no"] == "1"
    assert entry["volume_type"] == "STOCHASTIC"
    assert entry["continuous"] is False
    assert entry["interval_count"] == 1
    assert entry["volumes"] == [
        {
            "interval_no": 1,
            "volume": 450.0,
            "composition_no": "1",
            "volume_type": "STOCHASTIC",
            "continuous": False,
        }
    ]


def test_pedestrian_input_parser_keeps_every_interval_not_just_the_first(tmp_path: Path):
    """Measured on the 14-interval Air Taxi Vertiport network: collapsing to
    interval 1 would be exactly the silent lie this project exists to
    prevent."""
    model = tools_network.build_shadow_model(
        _write_working_inpx(tmp_path, _FOURTEEN_INTERVAL_INPUT)
    )
    entry = model["pedestrian_inputs"][0]

    assert entry["interval_count"] == 14
    assert len(entry["volumes"]) == 14
    assert entry["volumes"][0]["volume"] == 450.0
    assert all(v["volume"] == 0.0 for v in entry["volumes"][1:])


def test_pedestrian_area_parser_reports_polygon_and_routing_status(tmp_path: Path):
    model = tools_network.build_shadow_model(_write_working_inpx(tmp_path))
    by_no = {entry["no"]: entry for entry in model["pedestrian_areas"]}

    assert by_no["1"]["polygon"] == [[-10.0, 0.0], [-8.0, 0.0], [-8.0, 2.0], [-10.0, 2.0]]
    assert by_no["1"]["point_count"] == 4
    # Point count is preserved as given -- a 3-point polygon stays 3 points,
    # it is NOT auto-closed into a 4th vertex (measured, probe part F).
    assert by_no["7"]["point_count"] == 3
    # Routed vs unrouted, the split the refuse-tier constraint keys off.
    assert by_no["1"]["has_routing_decision"] is True
    assert by_no["7"]["has_routing_decision"] is True
    assert by_no["2"]["has_routing_decision"] is False


def test_pedestrian_area_parser_emits_conditional_attributes_as_empty_strings(tmp_path: Path):
    """A stable key set matters: the nothing-else-moved diff compares field by
    field, and a key that appears and disappears reads as a change."""
    model = tools_network.build_shadow_model(_write_working_inpx(tmp_path))
    by_no = {entry["no"]: entry for entry in model["pedestrian_areas"]}

    assert by_no["1"]["area_behavior_type_no"] == ""
    assert by_no["1"]["time_distribution_no"] == ""
    assert by_no["1"]["elevator_group_no"] == ""
    assert by_no["7"]["area_behavior_type_no"] == "2"
    assert by_no["7"]["time_distribution_no"] == "5"
    assert by_no["7"]["elevator_group_no"] == "3"
    assert set(by_no["1"]) == set(by_no["7"])


def test_a_network_with_no_viswalk_layer_parses_to_empty_lists_not_an_error(tmp_path: Path):
    """17 of the corpus's 25 files carry no ``<pedestrianInputs>`` section at
    all; that is normal data."""
    path = tmp_path / "plain.inpx"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><network><links>'
        '<link no="1"><lanes><lane width="3.5"/></lanes></link></links></network>',
        encoding="utf-8",
    )
    model = tools_network.build_shadow_model(path)

    assert model["pedestrian_inputs"] == []
    assert model["pedestrian_areas"] == []


def test_routed_area_nos_counts_STATIC_decisions_only_not_partial_ones(tmp_path: Path):
    """MEASURED, and it overturned this unit's own first answer. The guard
    originally counted partial decisions too -- a permissive inference, i.e.
    the dangerous direction. Probe parts Q/R settled it as a controlled pair
    on the one corpus network that has partial decisions at all (Cableways,
    26 partial vs 25 static, 24 areas routed ONLY partially): an input on
    partial-only area 142 gives init_ok=False, an input on STATIC-routed area
    102 of the SAME network gives init_ok=True."""
    path = tmp_path / "partial.inpx"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><network>'
        '<pedestrianRoutingDecisionsPartial><pedestrianRoutingDecisionPartial no="1">'
        '<location area="42"><posOnArea x="0" y="0"/></location>'
        "</pedestrianRoutingDecisionPartial></pedestrianRoutingDecisionsPartial>"
        '<pedestrianRoutingDecisionsStatic><pedestrianRoutingDecisionStatic no="1">'
        '<location area="7"><posOnArea x="0" y="0"/></location>'
        "</pedestrianRoutingDecisionStatic></pedestrianRoutingDecisionsStatic></network>",
        encoding="utf-8",
    )
    root = ET.parse(path).getroot()

    # 42 is partial-only and must NOT qualify; 7 is static and must.
    assert tools_network._routed_area_nos(root) == {"7"}


def test_an_area_routed_only_partially_is_refused_end_to_end(tmp_path: Path):
    """The tightened guard, exercised through the real verb rather than only
    at the helper."""
    working = tmp_path / "partial_only.inpx"
    working.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><network><areas>'
        '<area no="42"><geometry><points><point x="0" y="0"/></points></geometry></area>'
        "</areas>"
        '<pedestrianRoutingDecisionsPartial><pedestrianRoutingDecisionPartial no="1">'
        '<location area="42"><posOnArea x="0" y="0"/></location>'
        "</pedestrianRoutingDecisionPartial></pedestrianRoutingDecisionsPartial>"
        "<pedestrianInputs></pedestrianInputs></network>",
        encoding="utf-8",
    )
    fake = FakePedestrianSession(working)
    set_active_edit_session(fake)
    tools_network.set_active_inpx(working)
    try:
        envelope = dispatch("add_pedestrian_input", {"area_no": "42", "volume_pph": 750.0})
    finally:
        clear_active_edit_session()
        tools_network.set_active_inpx(None)

    assert envelope["status"] == "error"
    assert "routing decision" in envelope["message"]
    assert fake.calls == []


# ---------------------------------------------------------------------------
# add_pedestrian_input
# ---------------------------------------------------------------------------


def test_add_pedestrian_input_creates_on_a_routed_area(session):
    envelope = dispatch("add_pedestrian_input", {"area_no": "1", "volume_pph": 750.0})

    assert envelope["status"] == "ok"
    assert session.calls[0]["op"] == "add_pedestrian_input"
    assert session.calls[0]["area_no"] == 1
    assert session.calls[0]["volume_pph"] == 750.0
    assert session.persist_calls == 1
    assert envelope["artifacts"]["new_pedestrian_input_no"] == "9"


def test_add_pedestrian_input_verification_sides_are_scalars_with_the_same_key_set(session):
    """Q6 unit (2) shipped a DICT on the written side against a scalar
    readback, so constraint #6's structural compare refused every correct
    call. Both sides here carry identical keys and comparable values."""
    envelope = dispatch("add_pedestrian_input", {"area_no": "1", "volume_pph": 750.0})
    verification = envelope["verification"]

    assert set(verification["written_value"]) == set(verification["readback_value"])
    assert verification["written_value"] == verification["readback_value"]
    for value in verification["written_value"].values():
        assert isinstance(value, (str, int, float))


def test_add_pedestrian_input_refuses_an_area_with_no_routing_decision(session):
    """THE decisive finding: an input on a route-less origin area stops the
    network initialising, regardless of demand volume. Area 2 exists and is
    perfectly real -- it simply has no routing decision."""
    envelope = dispatch("add_pedestrian_input", {"area_no": "2", "volume_pph": 750.0})

    assert envelope["status"] == "error"
    message = envelope["message"]
    assert "2" in message
    assert "routing decision" in message
    # The nearest allowed alternative is NAMED, per the guardrail doctrine.
    assert "1" in message and "7" in message
    assert session.calls == [], "COM was reached despite the refusal"
    assert session.persist_calls == 0


def test_add_pedestrian_input_refuses_a_routeless_area_even_at_zero_volume(session):
    """L vs M: the demand volume is IRRELEVANT -- volume 0 fails identically.
    A guard that only fired on positive demand would let the broken network
    through."""
    envelope = dispatch("add_pedestrian_input", {"area_no": "2", "volume_pph": 0.0})

    assert envelope["status"] == "error"
    assert session.calls == []


@pytest.mark.parametrize("spelling", ["2", "02", " 2", "+2", "2 "])
def test_the_routing_guard_sees_the_same_canonical_id_as_the_action(session, spelling):
    """Commit 3316e76's pattern / Q6 unit (2)'s defect 1: the guard consults
    the XML and MUST see the id the action will use. A guard comparing the raw
    string lets '02' and ' 2' sail past the refusal into a broken network.

    THIS TEST HAD TO BE MADE DISCRIMINATING (review finding). Asserting only
    "it was refused" proves nothing here: a raw-id guard ALSO refuses '02' --
    for the wrong reason (it matches no area at all, so the routed-set lookup
    misses) -- so the whole group survived a full raw-id revert. The refusal
    must be shown to be the ROUTING refusal about the CANONICAL area, which is
    what flips under a revert.
    """
    envelope = dispatch("add_pedestrian_input", {"area_no": spelling, "volume_pph": 100.0})

    assert envelope["status"] == "error"
    message = envelope["message"]
    assert "routing decision" in message
    # The canonical spelling, not the caller's -- a raw-id guard would name
    # '02'/' 2'/'+2' back at the user here.
    assert "Pedestrian area 2 has no" in message, f"refusal did not canonicalise: {message}"
    assert session.calls == []


@pytest.mark.parametrize("spelling", ["2", "02", " 2", "+2", "2 "])
def test_the_routing_constraint_finding_also_names_the_canonical_area(session, spelling):
    """The same discrimination one layer down: the structured Finding the
    orchestrated path renders must carry the canonical id in ``details``, not
    the caller's spelling."""
    shadow = tools_network.build_shadow_model(session.working_inpx)
    finding = constraints._check_pedestrian_input_origin_area_has_no_route(
        {"tool": "add_pedestrian_input", "params": {"area_no": spelling}}, shadow, {}
    )

    assert finding is not None
    assert finding.details["area_no"] == "2"
    assert finding.details["areas_with_routing_decision"] == ["1", "7"]


@pytest.mark.parametrize("spelling", ["1", "01", " 1", "+1", "1 "])
def test_every_spelling_of_a_routed_area_is_accepted(session, spelling):
    """The mirror of the guard test: canonicalisation must not turn a LEGAL
    id into a refusal either."""
    envelope = dispatch("add_pedestrian_input", {"area_no": spelling, "volume_pph": 100.0})

    assert envelope["status"] == "ok"
    assert session.calls[0]["area_no"] == 1


@pytest.mark.parametrize("spelling", ["1", "01", " 1", "+1", "1 ", "0001"])
def test_a_correct_call_survives_POSTWRITE_for_every_legal_spelling(session, spelling):
    """THE defect this pins, which the accept-direction test above could not
    see: ``add_pedestrian_input`` built ``written_value`` from the RAW caller
    string while ``readback_value`` carried COM's canonicalised area, and
    ``_check_write_verification_failed`` compares the two dicts through
    ``_values_differ`` -- whose numeric tolerance does NOT reach inside dict
    members. So '01' passed the routing guard, passed the action, and was then
    REFUSED at postwrite: 5 of 6 legal spellings blocked.

    The accept-direction test only inspects the DISPATCH envelope, which is
    ok in both worlds. This one feeds the real verification dict to the real
    postwrite constraint, which is where the mismatch actually bit.
    """
    envelope = dispatch("add_pedestrian_input", {"area_no": spelling, "volume_pph": 750.0})
    assert envelope["status"] == "ok"

    verification = envelope["verification"]
    finding = constraints._check_write_verification_failed(
        {"tool": "add_pedestrian_input", "params": {"area_no": spelling}},
        {},
        verification,
    )

    assert finding is None, (
        f"a CORRECT call spelled {spelling!r} was refused at postwrite: "
        f"{verification['written_value']} vs {verification['readback_value']}"
    )
    assert verification["written_value"]["area"] == "1"


def test_add_pedestrian_input_reports_untouched_intervals_on_a_multi_interval_network(
    tmp_path: Path,
):
    """Measured: a created input inherits the NETWORK's whole interval
    structure -- 14 on Air Taxi Vertiport -- with only interval 1 carrying the
    requested volume."""
    working = _write_working_inpx(tmp_path, _FOURTEEN_INTERVAL_INPUT)
    fake = FakePedestrianSession(working, network_intervals=14)
    set_active_edit_session(fake)
    tools_network.set_active_inpx(working)
    try:
        envelope = dispatch("add_pedestrian_input", {"area_no": "1", "volume_pph": 200.0})
    finally:
        clear_active_edit_session()
        tools_network.set_active_inpx(None)

    artifacts = envelope["artifacts"]
    assert artifacts["interval_count"] == 14
    assert artifacts["intervals_left_at_zero"] == 13
    assert "interval 1 only" in envelope["message"]


def test_add_pedestrian_input_says_nothing_about_intervals_on_a_single_interval_network(session):
    envelope = dispatch("add_pedestrian_input", {"area_no": "1", "volume_pph": 200.0})

    assert envelope["artifacts"]["intervals_left_at_zero"] == 0
    assert "interval 1 only" not in envelope["message"]


def test_the_routing_refusal_stays_under_the_dispatch_cap_with_many_routed_areas(tmp_path: Path):
    """Refusals must survive toolkit.dispatch's 300-char cap WHOLE (Q6 unit
    (1): a 12-name ReferenceIntegrityError measured 613 chars and lost most
    names mid-word). Measured live on Beijing, 8 routed areas render this at
    268 chars wrapped -- 32 under the cap. This pins the WORST realistic
    case instead: 40 routed areas with 3-digit ids, which renders at 294 (measured through the real dispatch path, prefix included)."""
    areas = "".join(
        f'<area no="{n}"><geometry><points><point x="0" y="0"/></points></geometry></area>'
        for n in range(100, 141)
    )
    decisions = "".join(
        f'<pedestrianRoutingDecisionStatic no="{n}"><location area="{n}">'
        f'<posOnArea x="0" y="0"/></location></pedestrianRoutingDecisionStatic>'
        for n in range(100, 140)  # 140 is deliberately left UNROUTED
    )
    working = tmp_path / "many.inpx"
    working.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><network>'
        f"<areas>{areas}</areas>"
        f"<pedestrianRoutingDecisionsStatic>{decisions}</pedestrianRoutingDecisionsStatic>"
        "<pedestrianInputs></pedestrianInputs></network>",
        encoding="utf-8",
    )
    fake = FakePedestrianSession(working)
    set_active_edit_session(fake)
    tools_network.set_active_inpx(working)
    try:
        envelope = dispatch("add_pedestrian_input", {"area_no": "140", "volume_pph": 100.0})
    finally:
        clear_active_edit_session()
        tools_network.set_active_inpx(None)

    assert envelope["status"] == "error"
    message = envelope["message"]
    assert len(message) < 300, f"refusal is {len(message)} chars and will truncate mid-word"
    # Honest about what it left out, rather than silently short.
    assert "more)" in message
    assert session_free_marker(message)


def session_free_marker(message: str) -> bool:
    """The refusal still names real alternatives, not just a count."""
    return "100" in message


def test_add_pedestrian_input_rejects_unknown_params(session):
    envelope = dispatch("add_pedestrian_input", {"area_no": "1", "volume_pph": 1.0, "nope": 1})

    assert envelope["status"] == "error"
    assert session.calls == []


# ---------------------------------------------------------------------------
# set_pedestrian_input_volume / remove_pedestrian_input
# ---------------------------------------------------------------------------


def test_set_pedestrian_input_volume_writes_and_verifies(session):
    envelope = dispatch(
        "set_pedestrian_input_volume", {"pedestrian_input_no": "3", "volume_pph": 900.0}
    )

    assert envelope["status"] == "ok"
    assert session.calls[0] == {
        "op": "set_pedestrian_input_volume",
        "no": 3,
        "volume_pph": 900.0,
    }
    verification = envelope["verification"]
    assert verification["written_value"] == 900.0
    assert verification["readback_value"] == 900.0


def test_set_pedestrian_input_volume_surfaces_a_readback_mismatch(tmp_path: Path):
    """The verification must be able to FAIL: a session whose readback
    disagrees with the request produces mismatched sides for constraint #6."""
    working = _write_working_inpx(tmp_path)
    fake = FakePedestrianSession(working, readback={"volume": 12.0})
    set_active_edit_session(fake)
    tools_network.set_active_inpx(working)
    try:
        envelope = dispatch(
            "set_pedestrian_input_volume", {"pedestrian_input_no": "3", "volume_pph": 900.0}
        )
    finally:
        clear_active_edit_session()
        tools_network.set_active_inpx(None)

    verification = envelope["verification"]
    assert verification["written_value"] != verification["readback_value"]


def test_remove_pedestrian_input_verifies_absence_by_reparsing_the_working_copy(session):
    envelope = dispatch("remove_pedestrian_input", {"pedestrian_input_no": "3"})

    assert envelope["status"] == "ok"
    assert session.calls[0] == {"op": "remove_pedestrian_input", "no": 3}
    assert session.persist_calls == 1
    # And it really is gone from the file, not just from the return value.
    model = tools_network.build_shadow_model(session.working_inpx)
    assert model["pedestrian_inputs"] == []


def test_remove_pedestrian_input_reports_still_present_when_the_object_survives(tmp_path: Path):
    """A remove whose object is still in the file after persist() must not
    report success -- the absence IS the verification."""
    working = _write_working_inpx(tmp_path)

    class _NoOpRemoveSession(FakePedestrianSession):
        def remove_pedestrian_input(self, no: int) -> None:
            self._record("remove_pedestrian_input", no=no)  # deliberately does not mutate

    fake = _NoOpRemoveSession(working)
    set_active_edit_session(fake)
    tools_network.set_active_inpx(working)
    try:
        envelope = dispatch("remove_pedestrian_input", {"pedestrian_input_no": "3"})
    finally:
        clear_active_edit_session()
        tools_network.set_active_inpx(None)

    verification = envelope["verification"]
    assert verification["written_value"] != verification["readback_value"]


# ---------------------------------------------------------------------------
# Read tools.
# ---------------------------------------------------------------------------


def test_get_pedestrian_inputs_lists_and_filters(session):
    envelope = dispatch("get_pedestrian_inputs", {})
    assert envelope["data"]["count"] == 1
    assert envelope["data"]["truncated"] is False

    envelope = dispatch("get_pedestrian_inputs", {"area_no": "2"})
    assert envelope["data"]["count"] == 0


def test_get_pedestrian_areas_reports_the_usable_origin_count(session):
    envelope = dispatch("get_pedestrian_areas", {})

    assert envelope["data"]["count"] == 3
    assert envelope["data"]["usable_as_origin_count"] == 2


def test_get_pedestrian_areas_usable_as_origin_only_matches_what_the_verb_accepts(session):
    """The read tool and the write guard must agree exactly -- both read
    ``has_routing_decision``, which the PARSER computes once."""
    envelope = dispatch("get_pedestrian_areas", {"usable_as_origin_only": True})
    usable = {area["no"] for area in envelope["data"]["pedestrian_areas"]}

    assert usable == {"1", "7"}
    for area_no in usable:
        assert (
            dispatch("add_pedestrian_input", {"area_no": area_no, "volume_pph": 1.0})["status"]
            == "ok"
        )


def test_get_pedestrian_areas_caps_a_long_polygon_and_says_so(tmp_path: Path):
    """Corpus polygons run to 226 points; an unfiltered dump must not spend
    the planner's whole observation budget on one outline."""
    points = "".join(f'<point x="{i}" y="0"/>' for i in range(40))
    path = tmp_path / "big.inpx"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><network><areas>'
        f'<area no="1" name="Big"><geometry><points>{points}</points></geometry></area>'
        "</areas></network>",
        encoding="utf-8",
    )
    tools_network.set_active_inpx(path)
    try:
        envelope = dispatch("get_pedestrian_areas", {})
    finally:
        tools_network.set_active_inpx(None)

    area = envelope["data"]["pedestrian_areas"][0]
    assert area["point_count"] == 40, "the REAL count must survive the clipping"
    assert len(area["polygon"]) == 12
    assert area["polygon_truncated"] is True
    assert "vertices" in envelope["data"]["hint"]


def test_get_pedestrian_areas_filtered_by_no_returns_the_polygon_whole(tmp_path: Path):
    points = "".join(f'<point x="{i}" y="0"/>' for i in range(40))
    path = tmp_path / "big.inpx"
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><network><areas>'
        f'<area no="1" name="Big"><geometry><points>{points}</points></geometry></area>'
        "</areas></network>",
        encoding="utf-8",
    )
    tools_network.set_active_inpx(path)
    try:
        envelope = dispatch("get_pedestrian_areas", {"area_no": "1"})
    finally:
        tools_network.set_active_inpx(None)

    assert len(envelope["data"]["pedestrian_areas"][0]["polygon"]) == 40


# ---------------------------------------------------------------------------
# Guardrail + registry wiring.
# ---------------------------------------------------------------------------


def test_both_pedestrian_classes_are_diff_visible():
    """An object class the diff cannot see makes its verb unverifiable by
    construction (Q4 unit (4)'s doctrine)."""
    assert _SHADOW_OBJECT_ID_FIELDS["pedestrian_inputs"] == "no"
    assert _SHADOW_OBJECT_ID_FIELDS["pedestrian_areas"] == "no"


def test_add_and_remove_are_the_only_pedestrian_object_movers():
    assert _ALLOWS_NEW_OBJECTS_IN["add_pedestrian_input"] == frozenset({"pedestrian_inputs"})
    assert _ALLOWS_REMOVED_OBJECTS_IN["remove_pedestrian_input"] == frozenset(
        {"pedestrian_inputs"}
    )
    # Areas are deliberately NOT excused on either side: no verb here creates
    # or removes one, so an area moving during a pedestrian write is
    # unexplained drift and must stay loud evidence.
    assert "pedestrian_areas" not in _ALLOWS_NEW_OBJECTS_IN["add_pedestrian_input"]
    assert "pedestrian_areas" not in _ALLOWS_REMOVED_OBJECTS_IN["remove_pedestrian_input"]


def test_set_volume_exempts_its_own_target_and_the_derived_list():
    spec = _WRITE_TARGET_FIELD_SPEC["set_pedestrian_input_volume"]

    assert spec["object_class"] == "pedestrian_inputs"
    assert spec["id_param"] == "pedestrian_input_no"
    assert spec["value_field"] == "volume"
    # "volume" is derived from volumes[0] at parse time, so writing one always
    # moves the other; both must be exempted together or a correct write
    # false-positives as "something else moved".
    assert "volumes" in spec["coupled_fields"]


def test_every_pedestrian_verb_is_wired_into_object_not_found():
    """Q5 unit (3) found TEN shipped verbs with a _TOOL_TARGET_SPEC entry that
    were never named in object_not_found's applies_to, so check #1 silently
    never ran for them. Wired at BIRTH here, and pinned."""
    check = next(c for c in constraints.get_registry() if c.id == "object_not_found")

    for tool_name in (
        "add_pedestrian_input",
        "set_pedestrian_input_volume",
        "remove_pedestrian_input",
    ):
        assert tool_name in constraints._TOOL_TARGET_SPEC
        assert tool_name in check.applies_to


def test_pedestrian_target_specs_name_the_right_object_class():
    spec = constraints._TOOL_TARGET_SPEC
    assert spec["add_pedestrian_input"]["object_class"] == "pedestrian_areas"
    assert spec["add_pedestrian_input"]["param"] == "area_no"
    assert spec["set_pedestrian_input_volume"]["object_class"] == "pedestrian_inputs"
    assert spec["remove_pedestrian_input"]["object_class"] == "pedestrian_inputs"


def test_both_pedestrian_object_classes_have_ko_en_ja_labels():
    for object_class in ("pedestrian_inputs", "pedestrian_areas"):
        labels = constraints._OBJECT_CLASS_LABELS[object_class]
        assert set(labels) == {"ko", "en", "ja"}
        assert all(labels.values())


def test_the_three_verbs_and_two_read_tools_are_registered():
    names = set(default_registry.names())

    assert {"add_pedestrian_input", "set_pedestrian_input_volume", "remove_pedestrian_input"} <= names
    assert {"get_pedestrian_inputs", "get_pedestrian_areas"} <= names


# ---------------------------------------------------------------------------
# Constraints.
# ---------------------------------------------------------------------------


def _shadow_with_areas(routed: set[str], all_areas: tuple[str, ...]) -> dict[str, Any]:
    return {
        "pedestrian_areas": [
            {"no": no, "name": "", "has_routing_decision": no in routed} for no in all_areas
        ],
        "pedestrian_inputs": [],
    }


def test_route_constraint_refuses_an_unrouted_area_and_names_the_alternatives():
    finding = constraints._check_pedestrian_input_origin_area_has_no_route(
        {"tool": "add_pedestrian_input", "params": {"area_no": "2"}},
        _shadow_with_areas({"1", "7"}, ("1", "2", "7")),
        {},
    )

    assert finding is not None
    assert finding.tier == "refuse"
    assert finding.phase == "preflight"
    assert finding.details["areas_with_routing_decision"] == ["1", "7"]
    for language in ("ko", "en", "ja"):
        assert "2" in finding.messages[language]


def test_route_constraint_is_silent_on_a_routed_area():
    assert (
        constraints._check_pedestrian_input_origin_area_has_no_route(
            {"tool": "add_pedestrian_input", "params": {"area_no": "1"}},
            _shadow_with_areas({"1"}, ("1", "2")),
            {},
        )
        is None
    )


def test_route_constraint_stays_silent_for_an_unknown_area_so_check_one_owns_it():
    """Existence is check #1's business; double-reporting helps nobody."""
    assert (
        constraints._check_pedestrian_input_origin_area_has_no_route(
            {"tool": "add_pedestrian_input", "params": {"area_no": "999"}},
            _shadow_with_areas({"1"}, ("1", "2")),
            {},
        )
        is None
    )


@pytest.mark.parametrize("spelling", ["2", "02", " 2", "+2", "2 "])
def test_route_constraint_canonicalises_every_id_spelling(spelling):
    finding = constraints._check_pedestrian_input_origin_area_has_no_route(
        {"tool": "add_pedestrian_input", "params": {"area_no": spelling}},
        _shadow_with_areas({"1"}, ("1", "2")),
        {},
    )

    assert finding is not None
    assert finding.details["area_no"] == "2"


def test_route_constraint_ignores_other_tools():
    assert (
        constraints._check_pedestrian_input_origin_area_has_no_route(
            {"tool": "add_vehicle_input", "params": {"area_no": "2"}},
            _shadow_with_areas({"1"}, ("1", "2")),
            {},
        )
        is None
    )


def test_interval_constraint_warns_only_on_a_multi_interval_network():
    action = {"tool": "add_pedestrian_input", "params": {}}
    extra = {
        "result": {
            "artifacts": {
                "new_pedestrian_input_no": "9",
                "interval_count": 14,
                "intervals_left_at_zero": 13,
            }
        }
    }

    finding = constraints._check_pedestrian_input_first_interval_only(action, {}, extra)
    assert finding is not None
    assert finding.tier == "warn"
    assert finding.phase == "postwrite"
    assert finding.details["intervals_left_at_zero"] == 13
    for language in ("ko", "en", "ja"):
        assert "14" in finding.messages[language]

    single = {
        "result": {
            "artifacts": {
                "new_pedestrian_input_no": "9",
                "interval_count": 1,
                "intervals_left_at_zero": 0,
            }
        }
    }
    assert constraints._check_pedestrian_input_first_interval_only(action, {}, single) is None


@pytest.mark.parametrize(
    "tool_name",
    [
        "add_pedestrian_area",
        "remove_pedestrian_area",
        "set_pedestrian_area_geometry",
        "add_pedestrian_routing_decision",
        "add_pedestrian_route",
    ],
)
def test_area_edit_constraint_refuses_every_unsupported_pedestrian_verb(tool_name):
    finding = constraints._check_pedestrian_area_edit_unsupported(
        {"tool": tool_name, "params": {}}, {}, {}
    )

    assert finding is not None
    assert finding.tier == "refuse"
    assert finding.suggested_fix["supported"] == [
        "add_pedestrian_input",
        "set_pedestrian_input_volume",
        "remove_pedestrian_input",
    ]
    for language in ("ko", "en", "ja"):
        assert tool_name in finding.messages[language]


def test_area_edit_constraint_is_silent_for_the_verbs_that_do_ship():
    for tool_name in (
        "add_pedestrian_input",
        "set_pedestrian_input_volume",
        "remove_pedestrian_input",
    ):
        assert (
            constraints._check_pedestrian_area_edit_unsupported(
                {"tool": tool_name, "params": {}}, {}, {}
            )
            is None
        )


def test_the_three_pedestrian_constraints_are_registered_with_the_expected_tiers():
    by_id = {c.id: c for c in constraints.get_registry()}

    assert by_id["pedestrian_input_origin_area_has_no_route"].tier == "refuse"
    assert by_id["pedestrian_input_created_covers_first_interval_only"].tier == "warn"
    assert by_id["pedestrian_area_edit_unsupported"].tier == "refuse"


# ---------------------------------------------------------------------------
# The unsupported-tool DELIVERY PATH (review finding: the constraint was dead
# code -- preflight only runs for REGISTERED gated tools, and none of these
# names is registered anywhere, so a planner got a bare "Unknown tool").
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name",
    [
        "add_pedestrian_area",
        "remove_pedestrian_area",
        "set_pedestrian_area_geometry",
        "add_pedestrian_routing_decision",
        "add_pedestrian_route",
    ],
)
def test_explain_unsupported_tool_returns_the_rendered_refusal(tool_name):
    explanation = constraints.explain_unsupported_tool(tool_name)

    assert explanation is not None
    assert explanation["constraint_id"] == "pedestrian_area_edit_unsupported"
    assert tool_name in explanation["message"]
    assert explanation["suggested_fix"]["supported"] == [
        "add_pedestrian_input",
        "set_pedestrian_input_volume",
        "remove_pedestrian_input",
    ]


def test_explain_unsupported_tool_speaks_ko_en_and_ja():
    ko = constraints.explain_unsupported_tool("add_pedestrian_area")["message"]
    en = constraints.explain_unsupported_tool("add_pedestrian_area", "en")["message"]
    ja = constraints.explain_unsupported_tool("add_pedestrian_area", "ja")["message"]

    assert ko != en != ja
    # ko is the house default when no language is named.
    assert constraints.explain_unsupported_tool("add_pedestrian_area", "ko")["message"] == ko
    assert "not supported" in en
    # An unknown language falls back to ko rather than raising or blanking.
    assert constraints.explain_unsupported_tool("add_pedestrian_area", "xx")["message"] == ko


def test_explain_unsupported_tool_returns_none_for_a_genuine_typo():
    """It must never dress a misspelling up as a deliberate refusal -- the
    orchestrator still owes those callers a plain unknown_tool."""
    assert constraints.explain_unsupported_tool("get_pedestrain_inputs") is None
    assert constraints.explain_unsupported_tool("flurb_the_widget") is None
    # And it stays silent for the verbs that DO ship.
    assert constraints.explain_unsupported_tool("add_pedestrian_input") is None


def test_the_orchestrator_envelope_carries_the_explanation_not_unknown_tool():
    from services.agent.orchestrator import _unsupported_tool_envelope

    explanation = constraints.explain_unsupported_tool("remove_pedestrian_area", "en")
    envelope = _unsupported_tool_envelope("remove_pedestrian_area", "en", explanation)

    assert envelope["status"] == "error"
    assert envelope["error_type"] == "unsupported_tool"
    assert envelope["error_type"] != "unknown_tool"
    assert "SILENTLY deletes" in envelope["message"]
    assert envelope["suggested_fix"]["supported"][0] == "add_pedestrian_input"
    assert envelope["data"]["constraint_id"] == "pedestrian_area_edit_unsupported"
    assert set(envelope) == {
        "status", "error_type", "message", "artifacts", "suggested_fix", "verification", "data",
    }


def test_unsupported_tool_is_traced_as_a_refusal_not_swallowed():
    """A deliberate refusal must trace like every other refusal-with-reason,
    or it scores as an undetected lie in eval (DECISIONS 2026-07-24, M5.4)."""
    from services.agent.orchestrator import _DISPATCH_REFUSAL_ERROR_TYPES

    assert "unsupported_tool" in _DISPATCH_REFUSAL_ERROR_TYPES


def test_none_of_the_refused_names_is_a_registered_tool():
    """The premise of the whole delivery path: if one of these ever became a
    real tool, its constraint entry would start shadowing it and this test is
    what says so."""
    registered = set(default_registry.names())

    assert not (constraints._UNSUPPORTED_PEDESTRIAN_AREA_TOOLS & registered)

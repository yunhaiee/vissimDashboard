"""Roadmap 3.2 Genesis (c) — demand: the join, the arithmetic, the round trip.

What this file proves, in the order it matters:

1. **The 1.2 chain end-to-end.** Synthetic counts → ``build_demand_seeding`` →
   ``generate_demand`` → emitted ``.inpx`` → parsed back, with the recovered
   volumes, relative flows and shares equal to the counts' own numbers. Every
   expected figure is derived by hand in this file's own comments, from a
   fixture chosen so that no figure has a rounding residual.
2. **Zero invented numbers.** Every volume traces to a counted figure and a bin
   length, every relative flow to an observed movement, every position to the
   draft's own geometry.
3. **The refusals fire**, one test per named code, each carrying ko/en/ja.
4. **The demand-less path is byte-identical** to what the emitter produced
   before this module existed — and so is the signals-only path.
5. **The schema constants are the Vissim-saved ones** — transcribed verbatim
   here, the same way ``test_genesis_signals.py`` pins the signal attribute
   sets, so the check does not depend on the (git-ignored) reference networks
   being present.
6. **The citations quote the §5 extract**, with the mutation counterfactual that
   proves the comparison bites.
7. **What round-trips and what does not** is asserted rather than described, so
   the module docstring's "one named gap" table cannot rot.

The fixture — one T-junction, four 15-minute bins, two classes
--------------------------------------------------------------
Chosen so every derived number is exact::

    per 15-minute bin, north approach     through   left    bin total
      car                                     30      40           70
      hgv                                     10      10           20
      bin total                               40      50           90

    four identical bins → hourly counted total   90 x 4 = 360 vehicles
    vehicle input volume  = 90 x 60 / 15         = 360 veh/h   (flat profile)
    composition relFlow   car = 70 x 4           = 280
                          hgv = 20 x 4           =  80   (sum 360)
    composition share     car = 280 / 360        = 7/9
                          hgv =  80 / 360        = 2/9
    route relFlow         through = 40 x 4       = 160
                          left    = 50 x 4       = 200   (sum 360)
    route share           through = 160 / 360    = 4/9
                          left    = 200 / 360    = 5/9

Every one of those is a fraction with denominator 9 or an integer, so the
assertions below compare exact values, not "close to".
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from services.dataimport.base import AdapterInfo, ImportResult, IssueSeverity
from services.dataimport.demand import build_demand_seeding, seedable_counts
from services.dataimport.schema import (
    ApproachRef,
    CanonicalVehicleClass,
    ClassificationScheme,
    MovementRef,
    Provenance,
    TurnType,
    TurningCountData,
    VehicleClassRef,
    VolumeUnit,
)
from services.genesis import demand as demand_module
from services.genesis import emit, schema
from services.genesis.demand import (
    NO_DEMAND,
    REDUCTION_MEAN,
    REDUCTION_PEAK,
    REFUSE_CODES,
    DemandSettings,
    DraftDemand,
    VehicleTypeRef,
    demand_containers,
    entry_links,
    generate_demand,
    link_exits,
    route_share,
)
from services.genesis.diff import diff_network_files
from services.genesis.draft import (
    ComplianceState,
    DraftConnector,
    DraftLink,
    DraftNetwork,
    DraftNode,
    link_table_fingerprint,
)
from services.genesis.osm import EXTRACT
from services.inpx_parser import (
    parse_network,
    parse_network_elements,
    parse_routing_decisions,
    vehicle_composition_names,
)

# The citation comparison is reused, never re-implemented — one implementation
# means it cannot be weakened here without also weakening it for unit 1.6, for
# the OSM draft and for the signals layer.
from tests.test_dataimport_fixed_time import (  # noqa: E402
    _citation_constants,
    _citation_traceability_failures,
    _normalise_prose,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

ADAPTER = AdapterInfo(name="test", region="XX", source_system="Test", version="0")
PROV = Provenance(source_file="counts.xlsx", locator="Sheet1!row 4")
WEDNESDAY = datetime(2026, 4, 8, 7, 0)

SITE = "가람네거리"
LEG_NORTH = "가람방면"

CAR = VehicleClassRef(
    scheme=ClassificationScheme.KOREA_SMART_INTERSECTION_6,
    code="승용",
    canonical=CanonicalVehicleClass.LIGHT_VEHICLE,
)
TRUCK = VehicleClassRef(
    scheme=ClassificationScheme.KOREA_SMART_INTERSECTION_6, code="화물_소형"
)
ALL_CLASSES = VehicleClassRef(
    scheme=ClassificationScheme.UNCLASSIFIED,
    code="합계",
    canonical=CanonicalVehicleClass.ALL_VEHICLES,
)

#: The hand-derived figures the fixture is built to produce. Written here so an
#: assertion that drifts fails against the docstring's table, not against
#: whatever the code happens to compute.
EXPECTED_INPUT_VPH = 360.0
EXPECTED_CAR_REL_FLOW = 280.0
EXPECTED_TRUCK_REL_FLOW = 80.0
EXPECTED_THROUGH_REL_FLOW = 160.0
EXPECTED_LEFT_REL_FLOW = 200.0

VEHICLE_TYPES = {
    "승용": VehicleTypeRef(veh_type_no=100, des_speed_distr_no=50),
    "화물_소형": VehicleTypeRef(veh_type_no=200, des_speed_distr_no=50),
}

APPROACH_LINKS = {SITE: {LEG_NORTH: 1}}

APPROACH_LENGTH_M = 150.0


# ---------------------------------------------------------------------------
# 0. Fixtures
# ---------------------------------------------------------------------------


def _count(
    start: datetime,
    *,
    minutes: int = 15,
    volume: float = 30,
    turn: TurnType | None = TurnType.THROUGH,
    approach_total: bool = False,
    vehicle_class: VehicleClassRef | None = None,
    leg: str = LEG_NORTH,
    site: str = SITE,
    unit: VolumeUnit = VolumeUnit.VEHICLES,
) -> TurningCountData:
    """The same builder shape ``test_dataimport_demand.py`` uses, so the two
    files' fixtures cannot drift on what a neutral count looks like."""
    movement = (
        MovementRef(is_approach_total=True) if approach_total else MovementRef(turn=turn)
    )
    return TurningCountData(
        provenance=PROV,
        site_id=site,
        approach=ApproachRef(leg_id=leg),
        movement=movement,
        vehicle_class=vehicle_class or CAR,
        interval_start=start,
        interval_end=start + timedelta(minutes=minutes),
        volume=volume,
        volume_unit=unit,
    )


def _link(link_id: int, name: str, points, from_node: int, to_node: int, lanes: int = 1):
    return DraftLink(
        id=link_id,
        name=name,
        osm_way_id=link_id,
        segment_index=0,
        forward=True,
        lanes=lanes,
        lane_width_m=3.5,
        points=points,
        lane_provenance={"source": "test"},
        highway="residential",
        from_node_osm_id=from_node,
        to_node_osm_id=to_node,
        side_offset_m=0.0,
    )


def _connector(connector_id: int, from_link: int, to_link: int, turn: str, angle: float):
    return DraftConnector(
        id=connector_id,
        name=f"{from_link}->{to_link} ({turn})",
        node_osm_id=1,
        node_id=1,
        from_link=from_link,
        to_link=to_link,
        from_pos_m=APPROACH_LENGTH_M,
        to_pos_m=0.0,
        lanes=1,
        lane_width_m=3.5,
        points=[(0.0, 0.0), (1.0, 1.0)],
        turn=turn,
        heading_change_deg=angle,
        lane_change="same",
        from_lanes=[1],
        to_lanes=[1],
    )


@pytest.fixture
def tee():
    """One node: link 1 arrives, links 3 and 5 leave. Link 1 is the only entry.

    Deliberately minimal, and deliberately NOT symmetric: with exactly one
    approach the entry/approach classification and the two-route split are both
    unambiguous, and every observed number lands on one object.
    """
    links = [
        _link(1, "North approach", [(0.0, APPROACH_LENGTH_M), (0.0, 0.0)], 101, 1),
        _link(3, "South exit", [(0.0, 0.0), (0.0, -APPROACH_LENGTH_M)], 1, 103),
        _link(5, "East exit", [(0.0, 0.0), (APPROACH_LENGTH_M, 0.0)], 1, 105),
    ]
    connectors = [
        _connector(10001, 1, 3, "through", 0.0),
        _connector(10002, 1, 5, "left", 90.0),
    ]
    node = DraftNode(
        id=1,
        name="n1",
        osm_id=1,
        x=0.0,
        y=0.0,
        polygon=[(-15.0, -15.0), (15.0, -15.0), (15.0, 15.0), (-15.0, 15.0)],
    )
    return DraftNetwork(
        name="tee",
        source_osm="tee.osm",
        links=links,
        nodes=[node],
        connectors=connectors,
    )


@pytest.fixture
def two_entry_draft(tee):
    """The T-junction plus a second approach (link 7), so two entry links exist."""
    return tee.model_copy(
        update={
            "links": tee.links
            + [_link(7, "South approach", [(0.0, -APPROACH_LENGTH_M), (0.0, 0.0)], 107, 1)],
            "connectors": tee.connectors + [_connector(10003, 7, 5, "right", -90.0)],
        }
    )


def _counts(intervals: int = 4, minutes: int = 15):
    """The docstring's table, plus the two stated-total rows that must NOT be
    summed — so every arithmetic assertion is simultaneously a double-count test."""
    rows = []
    for index in range(intervals):
        start = WEDNESDAY + timedelta(minutes=minutes * index)
        rows.append(_count(start, minutes=minutes, turn=TurnType.THROUGH, volume=30, vehicle_class=CAR))
        rows.append(_count(start, minutes=minutes, turn=TurnType.THROUGH, volume=10, vehicle_class=TRUCK))
        rows.append(_count(start, minutes=minutes, turn=TurnType.LEFT, volume=40, vehicle_class=CAR))
        rows.append(_count(start, minutes=minutes, turn=TurnType.LEFT, volume=10, vehicle_class=TRUCK))
        # the source's own all-class totals per movement — sums of the above
        rows.append(
            _count(start, minutes=minutes, turn=TurnType.THROUGH, volume=40, vehicle_class=ALL_CLASSES)
        )
        rows.append(
            _count(start, minutes=minutes, turn=TurnType.LEFT, volume=50, vehicle_class=ALL_CLASSES)
        )
        # and the approach-total row, the OTHER axis of "already a sum"
        rows.append(
            _count(start, minutes=minutes, approach_total=True, volume=90, vehicle_class=ALL_CLASSES)
        )
    return rows


@pytest.fixture
def counts():
    return _counts()


@pytest.fixture
def result(counts):
    return ImportResult(adapter=ADAPTER, counts=list(counts))


@pytest.fixture
def flat_result():
    """One 60-minute bucket carrying the same hourly totals — the source shape
    that needs no interval reduction at all."""
    rows = [
        _count(WEDNESDAY, minutes=60, turn=TurnType.THROUGH, volume=120, vehicle_class=CAR),
        _count(WEDNESDAY, minutes=60, turn=TurnType.THROUGH, volume=40, vehicle_class=TRUCK),
        _count(WEDNESDAY, minutes=60, turn=TurnType.LEFT, volume=160, vehicle_class=CAR),
        _count(WEDNESDAY, minutes=60, turn=TurnType.LEFT, volume=40, vehicle_class=TRUCK),
    ]
    return ImportResult(adapter=ADAPTER, counts=rows)


def _generate(draft, result, **kwargs):
    kwargs.setdefault("approach_links", APPROACH_LINKS)
    kwargs.setdefault("vehicle_types", VEHICLE_TYPES)
    kwargs.setdefault("settings", DemandSettings(single_interval_reduction=REDUCTION_PEAK))
    return generate_demand(draft, result, **kwargs)


@pytest.fixture
def seeded(tee, result):
    return _generate(tee, result)


# ---------------------------------------------------------------------------
# 1. The 1.2 bridge really does produce the profile this file claims
# ---------------------------------------------------------------------------


def test_the_fixture_is_1_2s_own_seeding(result):
    """Before any network is touched: the BRIDGE's own output is the table in
    this module's docstring. If 1.2 ever changes, this fails here rather than
    silently changing what the round-trip test is proving."""
    seeding = build_demand_seeding(result)
    assert seeding.ok
    profile = seeding.profile_for(SITE, LEG_NORTH)
    assert profile is not None
    # four 15-minute bins, 90 counted vehicles each → 90 * 60 / 15 = 360 veh/h
    assert [interval.counted_volume for interval in profile.intervals] == [90.0] * 4
    assert [interval.volume_vph for interval in profile.intervals] == [EXPECTED_INPUT_VPH] * 4
    composition = seeding.compositions[0]
    assert {share.vehicle_class.code: share.rel_flow for share in composition.shares} == {
        "승용": EXPECTED_CAR_REL_FLOW,
        "화물_소형": EXPECTED_TRUCK_REL_FLOW,
    }


def test_stated_totals_are_excluded_from_every_arithmetic_arm(counts):
    """Both axes of "this row is already a sum" are excluded by the SAME
    predicate the profile arm uses — 28 rows in, 16 summable."""
    assert len(counts) == 28
    summable = seedable_counts(counts)
    assert len(summable) == 16
    assert all(not row.movement.is_approach_total for row in summable)
    assert all(row.vehicle_class.code != "합계" for row in summable)


# ---------------------------------------------------------------------------
# 2. The plan — the hand-derived arithmetic
# ---------------------------------------------------------------------------


def test_one_vehicle_input_on_the_only_entry_link(tee, seeded):
    assert seeded.ok, seeded.issue_codes(IssueSeverity.REFUSE)
    assert [link.id for link in entry_links(tee)] == [1]
    assert [(plan.no, plan.link_id) for plan in seeded.inputs] == [(1, 1)]
    plan = seeded.inputs[0]
    assert plan.site_id == SITE and plan.approach_leg_id == LEG_NORTH
    assert plan.emitted_volume_vph == EXPECTED_INPUT_VPH


def test_the_whole_profile_is_kept_even_though_one_interval_is_written(seeded):
    """The difference between "this file writes one interval" and "this survey
    only had one" is the reason ``intervals`` is not truncated."""
    plan = seeded.inputs[0]
    assert plan.profiled is True
    assert len(plan.intervals) == 4
    assert sum(interval.emitted for interval in plan.intervals) == 1
    assert plan.total_counted == 360.0
    assert plan.total_minutes == 60.0
    # peak and mean coincide on a flat profile, which is what makes this
    # fixture safe to assert exactly; the divergence is tested separately.
    assert plan.peak_volume_vph == plan.mean_volume_vph == EXPECTED_INPUT_VPH


def test_peak_and_mean_diverge_on_a_peaked_profile(tee):
    """30/60/90/120 counted in four 15-minute bins → rates 120/240/360/480 and a
    mean of (30+60+90+120) * 60 / 60 = 300. The two reductions are different
    numbers and the caller says which."""
    rows = []
    for index, volume in enumerate([30, 60, 90, 120]):
        rows.append(
            _count(WEDNESDAY + timedelta(minutes=15 * index), volume=volume, turn=TurnType.THROUGH)
        )
    result = ImportResult(adapter=ADAPTER, counts=rows)
    types = {"승용": VehicleTypeRef(100, 50)}

    peak = _generate(tee, result, vehicle_types=types)
    mean = _generate(
        tee,
        result,
        vehicle_types=types,
        settings=DemandSettings(single_interval_reduction=REDUCTION_MEAN),
    )
    assert peak.inputs[0].emitted_volume_vph == 480.0
    assert mean.inputs[0].emitted_volume_vph == 300.0
    # peak names a real interval; mean names none rather than the nearest bin
    assert [i.index for i in peak.inputs[0].intervals if i.emitted] == [4]
    assert [i.index for i in mean.inputs[0].intervals if i.emitted] == []


def test_composition_relative_flows_are_the_raw_counts(seeded):
    assert [plan.no for plan in seeded.compositions] == [1]
    flows = {flow.class_code: flow for flow in seeded.compositions[0].flows}
    assert flows["승용"].rel_flow == EXPECTED_CAR_REL_FLOW
    assert flows["화물_소형"].rel_flow == EXPECTED_TRUCK_REL_FLOW
    # 280/360 = 7/9 and 80/360 = 2/9 — exact, and they sum to exactly 1
    assert flows["승용"].share == pytest.approx(7 / 9)
    assert flows["화물_소형"].share == pytest.approx(2 / 9)
    assert seeded.compositions[0].share_sum == pytest.approx(1.0, abs=1e-12)
    # the caller's numbers, carried verbatim
    assert (flows["승용"].veh_type_no, flows["승용"].des_speed_distr_no) == (100, 50)
    assert (flows["화물_소형"].veh_type_no, flows["화물_소형"].des_speed_distr_no) == (200, 50)


def test_routing_relative_flows_reproduce_the_observed_split(tee, seeded):
    assert [connector.id for connector in link_exits(tee, 1)] == [10001, 10002]
    assert len(seeded.decisions) == 1
    decision = seeded.decisions[0]
    assert (decision.no, decision.link_id, decision.pos_m) == (1, 1, 0.0)
    assert decision.observed_total == 360.0
    by_turn = {route.turn: route for route in decision.routes}
    assert by_turn["through"].rel_flow == EXPECTED_THROUGH_REL_FLOW
    assert by_turn["left"].rel_flow == EXPECTED_LEFT_REL_FLOW
    # 160/360 = 4/9, 200/360 = 5/9 — the observed split, exactly
    assert by_turn["through"].share == pytest.approx(4 / 9)
    assert by_turn["left"].share == pytest.approx(5 / 9)
    # the draft's own turn topology names the routes
    assert (by_turn["through"].connector_id, by_turn["through"].dest_link_id) == (10001, 3)
    assert (by_turn["left"].connector_id, by_turn["left"].dest_link_id) == (10002, 5)
    # destPos is the exit link's own length, not a number chosen here
    assert by_turn["through"].dest_pos_m == round(tee.link_by_id(3).length_m, 3)


def test_route_share_is_the_identity_fixes_py_inverts():
    """The closed form ``sibling_total * p / (1 - p)`` applied to demand this
    module seeded returns the value already written — worked on the fixture's
    own numbers.

    through: p = 160/360 = 4/9, siblings = 200
        200 * (4/9) / (1 - 4/9) = 200 * (4/9) / (5/9) = 200 * 4/5 = 160
    left:    p = 200/360 = 5/9, siblings = 160
        160 * (5/9) / (1 - 5/9) = 160 * (5/9) / (4/9) = 160 * 5/4 = 200
    """
    assert route_share(160.0, 200.0) == pytest.approx(4 / 9)
    assert route_share(200.0, 160.0) == pytest.approx(5 / 9)

    def fixes_closed_form(sibling_total: float, share: float) -> float:
        return sibling_total * share / (1.0 - share)

    assert fixes_closed_form(200.0, route_share(160.0, 200.0)) == pytest.approx(160.0)
    assert fixes_closed_form(160.0, route_share(200.0, 160.0)) == pytest.approx(200.0)
    # and the degenerate case does not divide by zero
    assert route_share(0.0, 0.0) == 0.0


def test_a_single_bucket_source_needs_no_reduction(tee, flat_result):
    """One 60-minute bin: 320 counted vehicles → 320 * 60 / 60 = 320 veh/h, and
    the plan says the SOURCE was not profiled."""
    demand = generate_demand(
        tee,
        flat_result,
        approach_links=APPROACH_LINKS,
        vehicle_types=VEHICLE_TYPES,
        settings=DemandSettings(),
    )
    assert demand.ok, demand.issue_codes(IssueSeverity.REFUSE)
    plan = demand.inputs[0]
    assert plan.profiled is False
    assert plan.reduction is None
    assert plan.emitted_volume_vph == 360.0
    assert "genesis_demand_profile_reduced_to_single_interval" not in demand.issue_codes()


# ---------------------------------------------------------------------------
# 3. Coverage — both directions of every join, never a silent drop
# ---------------------------------------------------------------------------


def test_coverage_names_both_sides_of_the_site_join(seeded):
    entries = seeded.coverage.sorted_entries()
    assert entries["sites_observed"] == [SITE]
    assert entries["sites_joined"] == [SITE]
    assert entries["approaches_joined"] == [f"{SITE}|{LEG_NORTH}"]
    assert entries["links_seeded_input_and_routing"] == [f"{SITE}|{LEG_NORTH}->link 1"]
    assert entries["classes_observed"] == sorted(["승용", "화물_소형"])
    assert entries["sites_without_link_map"] == []


def test_an_unmapped_site_is_named_not_refused(tee, result):
    """A count site the caller did not map at all is outside the draft. It is
    reported, and it does not stop the sites that ARE mapped."""
    extra = list(result.counts) + [
        _count(WEDNESDAY, site="다른네거리", leg="A", volume=10),
        _count(WEDNESDAY, site="다른네거리", leg="A", volume=10, turn=TurnType.LEFT),
    ]
    demand = _generate(tee, ImportResult(adapter=ADAPTER, counts=extra))
    assert demand.ok, demand.issue_codes(IssueSeverity.REFUSE)
    assert demand.coverage.sorted_entries()["sites_without_link_map"] == ["다른네거리"]
    assert [plan.link_id for plan in demand.inputs] == [1]


def test_a_drafted_turn_nobody_counted_gets_no_route_and_is_named(tee):
    """Only the through movement was surveyed; the drafted left turn is left out
    and NAMED. A route written at zero and a route left out both mean "no
    vehicles", but only one of them says so."""
    rows = [
        _count(WEDNESDAY + timedelta(minutes=15 * i), turn=TurnType.THROUGH, volume=40)
        for i in range(4)
    ]
    demand = _generate(
        tee, ImportResult(adapter=ADAPTER, counts=rows), vehicle_types={"승용": VehicleTypeRef(100, 50)}
    )
    assert demand.ok, demand.issue_codes(IssueSeverity.REFUSE)
    assert [route.turn for route in demand.decisions[0].routes] == ["through"]
    named = demand.coverage.sorted_entries()["turns_drafted_not_observed"]
    assert named == [f"site {SITE} / approach {LEG_NORTH} / link 1 / left"]
    assert "genesis_demand_turn_not_matched" in demand.issue_codes(IssueSeverity.WARN)


def test_an_observed_turn_the_draft_lacks_is_named(tee):
    """A right turn was surveyed but the draft has no connector for it. It gets
    no route — its vehicles would otherwise be reassigned to its siblings — and
    the coverage says so from the other direction."""
    rows = []
    for index in range(4):
        start = WEDNESDAY + timedelta(minutes=15 * index)
        rows.append(_count(start, turn=TurnType.THROUGH, volume=40))
        rows.append(_count(start, turn=TurnType.LEFT, volume=50))
        rows.append(_count(start, turn=TurnType.RIGHT, volume=10))
    demand = _generate(
        tee, ImportResult(adapter=ADAPTER, counts=rows), vehicle_types={"승용": VehicleTypeRef(100, 50)}
    )
    assert demand.ok, demand.issue_codes(IssueSeverity.REFUSE)
    assert sorted(route.turn for route in demand.decisions[0].routes) == ["left", "through"]
    assert demand.coverage.sorted_entries()["turns_observed_not_drafted"] == [
        f"site {SITE} / approach {LEG_NORTH} / link 1 / right"
    ]


def test_a_movement_with_no_mapped_turn_is_named_not_merged(tee):
    """An adapter that could not map a movement token leaves ``turn=None``.
    Such a record cannot be joined to a connector, so it is named rather than
    folded into whichever route happened to be first."""
    rows = []
    for index in range(4):
        start = WEDNESDAY + timedelta(minutes=15 * index)
        rows.append(_count(start, turn=TurnType.THROUGH, volume=40))
        rows.append(_count(start, turn=TurnType.LEFT, volume=50))
        rows.append(
            TurningCountData(
                provenance=PROV,
                site_id=SITE,
                approach=ApproachRef(leg_id=LEG_NORTH),
                movement=MovementRef(turn=None, turn_raw="유턴?"),
                vehicle_class=CAR,
                interval_start=start,
                interval_end=start + timedelta(minutes=15),
                volume=7,
                volume_unit=VolumeUnit.VEHICLES,
            )
        )
    demand = _generate(
        tee, ImportResult(adapter=ADAPTER, counts=rows), vehicle_types={"승용": VehicleTypeRef(100, 50)}
    )
    assert demand.ok, demand.issue_codes(IssueSeverity.REFUSE)
    assert demand.coverage.sorted_entries()["movements_without_turn"] == [
        f"{SITE}|{LEG_NORTH}|유턴?"
    ]
    # 40 and 50 per bin only — the 7 unmapped vehicles are named, not routed
    assert {route.rel_flow for route in demand.decisions[0].routes} == {160.0, 200.0}


def test_an_interior_approach_gets_routing_but_no_vehicle_input(tee):
    """A mapped link that is NOT an entry gets a routing decision and no input:
    its traffic entered upstream, and a second input there would double it."""
    # make link 3 an interior approach by giving it an onward connector
    extended = tee.model_copy(
        update={
            "links": tee.links
            + [_link(7, "Far exit", [(0.0, -APPROACH_LENGTH_M), (0.0, -300.0)], 103, 107)],
            "connectors": tee.connectors + [_connector(10003, 3, 7, "through", 0.0)],
        }
    )
    rows = []
    for index in range(4):
        start = WEDNESDAY + timedelta(minutes=15 * index)
        rows.append(_count(start, turn=TurnType.THROUGH, volume=40))
        rows.append(_count(start, turn=TurnType.LEFT, volume=50))
        rows.append(_count(start, turn=TurnType.THROUGH, volume=40, leg="남측"))
    demand = _generate(
        extended,
        ImportResult(adapter=ADAPTER, counts=rows),
        approach_links={SITE: {LEG_NORTH: 1, "남측": 3}},
        vehicle_types={"승용": VehicleTypeRef(100, 50)},
    )
    assert demand.ok, demand.issue_codes(IssueSeverity.REFUSE)
    assert [plan.link_id for plan in demand.inputs] == [1]
    assert sorted(seed.link_id for seed in demand.decisions) == [1, 3]
    entries = demand.coverage.sorted_entries()
    assert entries["links_seeded_input_and_routing"] == [f"{SITE}|{LEG_NORTH}->link 1"]
    assert entries["links_seeded_routing_only"] == [f"{SITE}|남측->link 3"]


# ---------------------------------------------------------------------------
# 4. Refusals — one test per named code, each carrying ko/en/ja
# ---------------------------------------------------------------------------


def _refusal(demand: DraftDemand, code: str):
    matches = [issue for issue in demand.refusals if issue.code == code]
    assert matches, f"{code} not raised; got {demand.issue_codes(IssueSeverity.REFUSE)}"
    return matches[0]


def _assert_refused_and_empty(demand: DraftDemand, code: str):
    assert not demand.ok
    issue = _refusal(demand, code)
    assert set(issue.messages) == {"ko", "en", "ja"}
    assert all(issue.messages[language].strip() for language in ("ko", "en", "ja"))
    # all-or-nothing: a refused demand produces no object at all
    assert demand.inputs == () and demand.compositions == () and demand.decisions == ()
    assert demand.emitting is False
    assert demand_containers(demand) == {}
    return issue


def test_no_approach_link_map_refuses_and_names_the_sites(tee, result):
    demand = _generate(tee, result, approach_links={})
    issue = _assert_refused_and_empty(demand, "genesis_demand_no_approach_link_map")
    assert issue.context["sites_needing_mapping"] == [SITE]
    assert demand.coverage.sorted_entries()["sites_without_link_map"] == [SITE]


def test_a_map_naming_an_unknown_link_refuses(tee, result):
    demand = _generate(tee, result, approach_links={SITE: {LEG_NORTH: 999}})
    issue = _assert_refused_and_empty(demand, "genesis_demand_map_names_unknown_link")
    assert issue.context["unknown_links"] == [f"{SITE}|{LEG_NORTH}->link 999"]


def test_a_map_naming_a_site_the_counts_lack_refuses(tee, result):
    demand = _generate(
        tee, result, approach_links={SITE: {LEG_NORTH: 1}, "없는곳": {"A": 3}}
    )
    _assert_refused_and_empty(demand, "genesis_demand_map_names_unknown_site")


def test_a_map_naming_an_approach_the_counts_lack_refuses(tee, result):
    demand = _generate(tee, result, approach_links={SITE: {LEG_NORTH: 1, "없는접근": 3}})
    _assert_refused_and_empty(demand, "genesis_demand_map_names_unknown_approach")


def test_two_approaches_claiming_one_link_refuses(tee):
    rows = []
    for index in range(4):
        start = WEDNESDAY + timedelta(minutes=15 * index)
        rows.append(_count(start, turn=TurnType.THROUGH, volume=40))
        rows.append(_count(start, turn=TurnType.THROUGH, volume=40, leg="남측"))
    demand = _generate(
        tee,
        ImportResult(adapter=ADAPTER, counts=rows),
        approach_links={SITE: {LEG_NORTH: 1, "남측": 1}},
        vehicle_types={"승용": VehicleTypeRef(100, 50)},
    )
    _assert_refused_and_empty(demand, "genesis_demand_duplicate_link_mapping")


def test_a_half_mapped_site_refuses(tee):
    """The refusal the brief cares most about: a mapped intersection with an
    approach left out would load part of its demand and drop the rest."""
    rows = []
    for index in range(4):
        start = WEDNESDAY + timedelta(minutes=15 * index)
        rows.append(_count(start, turn=TurnType.THROUGH, volume=40))
        rows.append(_count(start, turn=TurnType.THROUGH, volume=40, leg="남측"))
    demand = _generate(
        tee,
        ImportResult(adapter=ADAPTER, counts=rows),
        approach_links={SITE: {LEG_NORTH: 1}},
        vehicle_types={"승용": VehicleTypeRef(100, 50)},
    )
    issue = _assert_refused_and_empty(demand, "genesis_demand_mapped_site_missing_approaches")
    assert issue.context["unmapped_approaches"] == [f"{SITE}|남측"]


def test_no_vehicle_type_map_refuses_and_names_the_classes(tee, result):
    demand = _generate(tee, result, vehicle_types={})
    issue = _assert_refused_and_empty(demand, "genesis_demand_no_vehicle_type_map")
    assert issue.context["classes_needing_mapping"] == sorted(["승용", "화물_소형"])
    assert issue.citation == demand_module.CIT_S523_VEHICLE_TYPES


def test_a_class_with_no_vehicle_type_refuses(tee, result):
    demand = _generate(tee, result, vehicle_types={"승용": VehicleTypeRef(100, 50)})
    issue = _assert_refused_and_empty(demand, "genesis_demand_class_without_vehicle_type")
    assert issue.context["classes_without_vehicle_type"] == ["화물_소형"]
    assert demand.coverage.sorted_entries()["classes_without_vehicle_type"] == ["화물_소형"]


def test_a_vehicle_type_map_naming_an_unseen_class_refuses(tee, result):
    demand = _generate(
        tee, result, vehicle_types={**VEHICLE_TYPES, "버스": VehicleTypeRef(300, 50)}
    )
    issue = _assert_refused_and_empty(
        demand, "genesis_demand_vehicle_type_map_names_unknown_class"
    )
    assert issue.context["unknown_classes"] == ["버스"]


def test_a_profiled_source_without_a_stated_reduction_refuses(tee, result):
    demand = _generate(tee, result, settings=DemandSettings())
    issue = _assert_refused_and_empty(
        demand, "genesis_demand_profile_needs_interval_reduction"
    )
    assert issue.context["intervals"] == 4
    assert issue.citation == demand_module.CIT_S5541_FIFTEEN_MINUTE_INTERVALS


def test_a_wrong_bin_length_refuses_when_one_was_required(tee, flat_result):
    """§5.5.4.1's 15-minute analysis made a hard requirement: a 60-minute source
    refuses rather than being re-binned, because re-binning changes the peak."""
    demand = generate_demand(
        tee,
        flat_result,
        approach_links=APPROACH_LINKS,
        vehicle_types=VEHICLE_TYPES,
        settings=DemandSettings(expected_interval_minutes=15.0),
    )
    issue = _assert_refused_and_empty(demand, "genesis_demand_interval_length_mismatch")
    assert issue.context["observed_interval_minutes"] == [60.0]


def test_a_site_with_no_traffic_at_all_refuses_on_the_composition_sum(tee):
    """Every count zero: the class shares cannot sum to 1 because there is no
    whole to be a part of, and that is caught before any input is built."""
    rows = [
        _count(WEDNESDAY + timedelta(minutes=15 * i), turn=TurnType.THROUGH, volume=0)
        for i in range(4)
    ]
    demand = _generate(
        tee, ImportResult(adapter=ADAPTER, counts=rows), vehicle_types={"승용": VehicleTypeRef(100, 50)}
    )
    issue = _assert_refused_and_empty(demand, "genesis_demand_composition_shares_do_not_sum")
    assert issue.context["share_sum"] == 0.0


def test_a_zero_observed_entry_refuses_and_names_it(two_entry_draft):
    """One approach of a busy site was counted at zero. The site's composition
    is fine — the other approach carries traffic — so this reaches the entry
    check: an input written at zero would assert there is no demand here, and
    the survey asserts nothing of the kind."""
    rows = []
    for index in range(4):
        start = WEDNESDAY + timedelta(minutes=15 * index)
        rows.append(_count(start, turn=TurnType.THROUGH, volume=40))
        rows.append(_count(start, turn=TurnType.THROUGH, volume=0, leg="남측"))
    demand = _generate(
        two_entry_draft,
        ImportResult(adapter=ADAPTER, counts=rows),
        approach_links={SITE: {LEG_NORTH: 1, "남측": 7}},
        vehicle_types={"승용": VehicleTypeRef(100, 50)},
    )
    issue = _assert_refused_and_empty(demand, "genesis_demand_zero_observed_total")
    assert issue.context["counted_total"] == 0.0
    assert "link 7" in issue.subject


def test_an_ambiguous_turn_refuses_rather_than_splitting_it(tee):
    """Two connectors for one drafted turn: how a surveyed movement divides
    between them is not in the counts, so it is not invented."""
    ambiguous = tee.model_copy(
        update={
            "links": tee.links
            + [_link(7, "Second east exit", [(0.0, 0.0), (APPROACH_LENGTH_M, 10.0)], 1, 107)],
            "connectors": tee.connectors + [_connector(10003, 1, 7, "left", 85.0)],
        }
    )
    demand = _generate(ambiguous, ImportResult(adapter=ADAPTER, counts=_counts()))
    issue = _assert_refused_and_empty(demand, "genesis_demand_turn_has_multiple_connectors")
    assert issue.context["ambiguous_turns"] == ["left"]


def test_a_refused_1_2_seeding_refuses_here_too(tee):
    """PCU-weighted counts make 1.2 refuse; this module does not paper over it."""
    rows = [
        _count(WEDNESDAY, turn=TurnType.THROUGH, volume=40, unit=VolumeUnit.PCU),
    ]
    demand = _generate(tee, ImportResult(adapter=ADAPTER, counts=rows))
    issue = _assert_refused_and_empty(demand, "genesis_demand_seeding_refused")
    assert "demand_volume_unit_not_vehicles" in issue.context["seeding_refusals"]


# ---------------------------------------------------------------------------
# 4b. The link-table fingerprint — CRITICAL fix (adversarial pass, b3318ab)
#
# Draft link ids are a sequential counter draft.py resets on every build
# (``next_link_id = 1``, walking ``ways`` sorted by OSM way id), so any bbox
# change or re-extract renumbers them. A ``genesis.demand.v1`` document's
# ``approach_links`` carries bare link ids with NO reference to the generation
# they were read off — proven below by literally reproducing the bug (swap two
# link ids and watch the old mapping load one approach's demand onto the
# other), then proving the fingerprint check refuses exactly that case while
# leaving a matching or absent fingerprint alone.
# ---------------------------------------------------------------------------


def _swap_link_ids(draft: DraftNetwork, a: int, b: int) -> DraftNetwork:
    """A draft identical to ``draft`` except link ids ``a``/``b`` are swapped —
    the shape a bbox change (draft.py's ``next_link_id`` counter, reset on
    every build) produces when it changes which OSM way sorts first."""

    def swap(link_id: int) -> int:
        if link_id == a:
            return b
        if link_id == b:
            return a
        return link_id

    links = [link.model_copy(update={"id": swap(link.id)}) for link in draft.links]
    connectors = [
        connector.model_copy(
            update={"from_link": swap(connector.from_link), "to_link": swap(connector.to_link)}
        )
        for connector in draft.connectors
    ]
    return draft.model_copy(update={"links": links, "connectors": connectors})


def test_swapped_link_ids_load_the_wrong_approachs_demand_without_a_fingerprint(
    two_entry_draft, result
):
    """The exact adversarial repro the CRITICAL fix targets: applying a
    mapping read off one generation's numbering to a DIFFERENT generation
    whose ids were reassigned attaches one approach's observed demand to a
    different link — silently, with ``ok=True`` — unless a fingerprint is
    supplied. ``APPROACH_LINKS`` (link 1 = the north leg) was implicitly "read
    off" the ORIGINAL ``two_entry_draft``; after swapping ids 1 and 7, link 1
    is the SOUTH approach instead, and this test calls ``generate_demand``
    with no ``built_against_*`` at all — today's behaviour before this fix
    existed, and still legal (unverifiable, not refused) after it."""
    renumbered = _swap_link_ids(two_entry_draft, 1, 7)
    demand = _generate(renumbered, result)
    assert demand.ok, demand.issue_codes(IssueSeverity.REFUSE)
    plan = demand.input_for(1)
    assert plan is not None
    # The NORTH leg's 360 veh/h, seeded onto link 1 — which is now the SOUTH
    # approach's link. Wrong link, no refusal, no warning: the bug.
    assert plan.emitted_volume_vph == EXPECTED_INPUT_VPH


def test_a_fingerprint_matching_the_draft_permits_generation(two_entry_draft, result):
    fingerprint = link_table_fingerprint(two_entry_draft)
    demand = _generate(
        two_entry_draft,
        result,
        built_against_job="job-a",
        built_against_link_table_sha256=fingerprint,
    )
    assert demand.ok, demand.issue_codes(IssueSeverity.REFUSE)
    assert demand.input_for(1) is not None
    # Verified, so the "could not check" disclosure must NOT fire.
    assert "genesis_demand_link_numbering_unverified" not in demand.issue_codes(IssueSeverity.INFO)


def test_a_stale_fingerprint_refuses_and_blocks_the_wrong_load(two_entry_draft, result):
    """The fix applied to the exact repro above: the SAME renumbered draft and
    the SAME old mapping — but now stamped with the ORIGINAL draft's
    fingerprint — refuses instead of silently mis-seeding link 1."""
    fingerprint = link_table_fingerprint(two_entry_draft)
    renumbered = _swap_link_ids(two_entry_draft, 1, 7)
    demand = _generate(
        renumbered,
        result,
        built_against_job="job-a",
        built_against_link_table_sha256=fingerprint,
        target_job_id="job-b",
    )
    issue = _assert_refused_and_empty(demand, "genesis_demand_stale_link_numbering")
    assert issue.context["built_against_job"] == "job-a"
    assert issue.context["target_job_id"] == "job-b"
    assert issue.context["built_against_link_table_sha256"] == fingerprint
    assert issue.context["draft_link_table_sha256"] == link_table_fingerprint(renumbered)
    assert issue.context["draft_link_table_sha256"] != fingerprint
    assert "job-a" in issue.message and "job-b" in issue.message


def test_no_fingerprint_at_all_still_generates_but_names_the_gap(two_entry_draft, result):
    """Absence is not a refusal — an adapter/API document that never looked at
    a specific draft (WA SCATS, Track G staging) legitimately has none — but
    it is never silent: an INFO names exactly what could not be checked, on
    every successful run (the module's existing always-on disclosure
    channel, ``_disclosures``)."""
    demand = _generate(two_entry_draft, result)
    assert demand.ok, demand.issue_codes(IssueSeverity.REFUSE)
    infos = [
        issue for issue in demand.infos if issue.code == "genesis_demand_link_numbering_unverified"
    ]
    assert infos, demand.issue_codes(IssueSeverity.INFO)
    assert set(infos[0].messages) == {"ko", "en", "ja"}
    assert all(infos[0].messages[language].strip() for language in ("ko", "en", "ja"))


def test_link_table_fingerprint_is_deterministic_and_sensitive_to_renumbering(two_entry_draft):
    """Pure function contract: the same draft twice gives the same digest, a
    renumbered draft gives a DIFFERENT one, and it is a real sha256 hex
    digest — the identity ``approach_links`` relies on without restating it."""
    first = link_table_fingerprint(two_entry_draft)
    second = link_table_fingerprint(two_entry_draft)
    assert first == second
    assert len(first) == 64
    assert all(c in "0123456789abcdef" for c in first)

    renumbered = _swap_link_ids(two_entry_draft, 1, 7)
    assert link_table_fingerprint(renumbered) != first


def test_every_declared_refuse_code_has_a_test_above():
    """``REFUSE_CODES`` is data so this claim is testable rather than prose:
    every code the module declares is exercised by one of the tests in this
    section, and no code is declared that nothing can raise."""
    raised = {
        "genesis_demand_seeding_refused",
        "genesis_demand_no_approach_link_map",
        "genesis_demand_map_names_unknown_link",
        "genesis_demand_map_names_unknown_site",
        "genesis_demand_map_names_unknown_approach",
        "genesis_demand_duplicate_link_mapping",
        "genesis_demand_mapped_site_missing_approaches",
        "genesis_demand_no_vehicle_type_map",
        "genesis_demand_class_without_vehicle_type",
        "genesis_demand_vehicle_type_map_names_unknown_class",
        "genesis_demand_composition_shares_do_not_sum",
        "genesis_demand_zero_observed_total",
        "genesis_demand_profile_needs_interval_reduction",
        "genesis_demand_interval_length_mismatch",
        "genesis_demand_turn_has_multiple_connectors",
        "genesis_demand_stale_link_numbering",
    }
    assert raised == REFUSE_CODES


# ---------------------------------------------------------------------------
# 5. Always-on disclosures
# ---------------------------------------------------------------------------


def test_the_undeclared_vehicle_type_reference_is_disclosed_on_every_run(seeded):
    """The one reference this emission does not declare, said out loud."""
    codes = seeded.issue_codes(IssueSeverity.INFO)
    assert "genesis_demand_vehicle_types_not_declared_here" in codes
    issue = next(
        i for i in seeded.issues if i.code == "genesis_demand_vehicle_types_not_declared_here"
    )
    assert issue.context["veh_type_nos"] == [100, 200]
    assert issue.context["des_speed_distr_nos"] == [50]


def test_the_layout_decisions_are_disclosed_on_every_run(seeded):
    codes = seeded.issue_codes(IssueSeverity.INFO)
    assert "genesis_demand_composition_is_per_site" in codes
    assert "genesis_demand_routing_decision_at_link_start" in codes
    assert "genesis_demand_profile_reduced_to_single_interval" in codes


# ---------------------------------------------------------------------------
# 6. Emission — the measured schema, transcribed here
# ---------------------------------------------------------------------------


@pytest.fixture
def emitted(tee, seeded):
    return ET.fromstring(emit.to_xml_text(tee, None, seeded))


def test_time_interval_sets_are_the_ten_vissim_writes(emitted):
    """Transcribed from ``ref_lht``/``ref_nodes`` and from Vissim's own save of
    this generator's crossroads — all three agree, and all three carry exactly
    one interval per set, which is why a profile cannot be written."""
    container = emitted.find("timeIntervalSets")
    assert [element.get("no") for element in container] == [
        "VEHICLEINPUT",
        "VEHICLEROUTESTATIC",
        "VEHICLEROUTEPARTIAL",
        "VEHICLEROUTEPARKING",
        "PEDESTRIANINPUT",
        "PEDESTRIANROUTEPARTIAL",
        "VEHICLEROUTEPARTIALPT",
        "AREABEHAVIORTYPE",
        "MANAGEDLANESFACILITY",
        "PEDESTRIANROUTESTATIC",
    ]
    for element in container:
        intervals = element.find("timeInts")
        assert [child.attrib for child in intervals] == [{"start": "0"}]
    # the positional keys the volumes and flows reference
    assert schema.TIME_INT_VEHICLE_INPUT == "1 0"
    assert schema.REL_FLOW_KEY_VEHICLE_ROUTE_STATIC == "2 0:"


def test_vehicle_input_element_matches_the_vissim_saved_shape(emitted):
    inputs = emitted.find("vehicleInputs")
    assert len(inputs) == 1
    element = inputs[0]
    assert element.attrib == {"anmFlag": "false", "link": "1", "name": "", "no": "1"}
    volumes = element.find("timeIntVehVols")
    assert [child.attrib for child in volumes] == [
        {
            "cont": "false",
            "timeInt": "1 0",
            "vehComp": "1",
            "volType": "EXACT",
            "volume": "360",
        }
    ]


def test_vehicle_composition_element_matches_the_vissim_saved_shape(emitted):
    compositions = emitted.find("vehicleCompositions")
    assert len(compositions) == 1
    assert compositions[0].attrib == {"name": SITE, "no": "1"}
    flows = compositions[0].find("vehCompRelFlows")
    assert [child.attrib for child in flows] == [
        {"desSpeedDistr": "50", "relFlow": "280", "vehType": "100"},
        {"desSpeedDistr": "50", "relFlow": "80", "vehType": "200"},
    ]


def test_routing_decision_element_matches_the_vissim_saved_shape(emitted):
    decisions = emitted.find("vehicleRoutingDecisionsStatic")
    assert len(decisions) == 1
    decision = decisions[0]
    assert decision.attrib == {
        "allVehTypes": "true",
        "anmFlag": "false",
        "combineStaRoutDec": "true",
        "link": "1",
        "name": "",
        "no": "1",
        "pos": "0.000",
        "routeChoiceMeth": "STATIC",
    }
    # ``allVehTypes="true"`` is the measured form that carries NO vehClasses
    # child — the whole reason it is written, since this file declares no
    # vehicleClasses container for such a reference to resolve into.
    assert decision.find("vehClasses") is None
    routes = decision.find("vehRoutSta")
    assert [child.attrib for child in routes] == [
        {
            "destLink": "3",
            "destPos": "150.000",
            "formula": "",
            "name": "",
            "no": "1",
            "relFlow": "2 0:160",
        },
        {
            "destLink": "5",
            "destPos": "150.000",
            "formula": "",
            "name": "",
            "no": "2",
            "relFlow": "2 0:200",
        },
    ]
    for route, connector_id in zip(routes, ["10001", "10002"]):
        sequence = route.find("linkSeq")
        assert [child.attrib for child in sequence] == [{"key": connector_id}]


def test_the_demand_containers_sit_in_vissims_own_order(emitted):
    tags = [child.tag for child in emitted if isinstance(child.tag, str)]
    assert tags == [tag for tag in schema.CONTAINER_ORDER if tag in tags]
    assert tags[-4:] == [
        "timeIntervalSets",
        "vehicleCompositions",
        "vehicleInputs",
        "vehicleRoutingDecisionsStatic",
    ]


def test_the_provenance_comment_states_the_undeclared_reference(tee, seeded):
    text = emit.to_xml_text(tee, None, seeded)
    comment = text[text.index("<!--") : text.index("-->")]
    assert "GEOMETRY + DEMAND" in comment
    assert "THAT THIS FILE DOES NOT DECLARE" in comment
    assert "volType EXACT" in comment
    assert "written as ONE interval" in comment


# ---------------------------------------------------------------------------
# 7. Additivity — the demand-less and signals-only paths are byte-identical
# ---------------------------------------------------------------------------


def test_demand_less_draft_bytes_are_unchanged(tee, seeded):
    """The whole additivity guarantee, pinned rather than asserted: three ways
    of saying "no demand" all produce the file the emitter produced before this
    module existed."""
    baseline = emit.to_xml_text(tee)
    assert emit.to_xml_text(tee, None, None) == baseline
    assert emit.to_xml_text(tee, None, NO_DEMAND) == baseline
    refused = _generate(tee, ImportResult(adapter=ADAPTER, counts=[]))
    assert not refused.ok
    assert emit.to_xml_text(tee, None, refused) == baseline
    # and the demand-carrying file really is different, so the pin is not vacuous
    assert emit.to_xml_text(tee, None, seeded) != baseline
    assert "GEOMETRY ONLY" in baseline


def test_the_emission_is_deterministic(tee, result):
    first = emit.to_xml_text(tee, None, _generate(tee, result))
    second = emit.to_xml_text(tee, None, _generate(tee, result))
    assert first == second


def test_demand_containers_is_empty_for_every_no_demand_shape(tee, result):
    assert demand_containers(None) == {}
    assert demand_containers(NO_DEMAND) == {}
    assert demand_containers(_generate(tee, result, approach_links={})) == {}


def test_demand_perturbs_no_geometry_measured_by_the_diff_oracle(tee, seeded, tmp_path):
    """The emitter's own oracle, pointed at the additivity claim: a
    demand-carrying file and a demand-less one are structurally IDENTICAL.

    ``genesis.diff`` compares topology — links, lanes, connector wiring, node
    membership — so an empty diff between the two is the strongest offline
    statement that the demand layer added objects and moved nothing.
    """
    plain = emit.emit_inpx(tee, tmp_path / "plain.inpx")
    loaded = emit.emit_inpx(tee, tmp_path / "loaded.inpx", None, seeded)
    assert plain.read_bytes() != loaded.read_bytes()

    structural = diff_network_files(str(plain), str(loaded))
    # ``topology_only_scope`` is the diff's own always-on scope INFO, not a
    # difference between the two files; anything above INFO would be one.
    assert [finding.code for finding in structural.findings] == ["topology_only_scope"]
    assert not structural.refused
    assert (structural.links.added, structural.links.removed) == ([], [])
    assert (structural.connectors.added, structural.connectors.removed) == ([], [])
    assert structural.connectors.rewired == []
    assert structural.reclassified == []
    assert all(diff.added == [] and diff.removed == [] for diff in structural.node_movements)


def test_the_emitted_file_parses_from_disk(tee, seeded, tmp_path):
    """``emit_inpx``'s new keyword really reaches the file, and the file this
    repo's own reader opens carries the demand."""
    path = emit.emit_inpx(tee, tmp_path / "demand.inpx", None, seeded)
    parsed = parse_network(str(path))
    assert [link["id"] for link in parsed["links"]] == ["1", "3", "5", "10001", "10002"]
    root = ET.parse(path).getroot()
    assert len(root.find("vehicleInputs")) == 1
    assert len(root.find("vehicleRoutingDecisionsStatic")) == 1


# ---------------------------------------------------------------------------
# 8. The round trip — what this repo's readers recover, and what they do not
# ---------------------------------------------------------------------------


def test_the_routing_arm_round_trips_through_inpx_parser(emitted):
    """``parse_routing_decisions`` returns the seeded relative flows and derives
    the same split this module wrote: 160/360 = 44.4%, 200/360 = 55.6%."""
    decisions = parse_routing_decisions(emitted)
    assert len(decisions) == 1
    assert decisions[0]["approach_link"] == "1"
    routes = {route["dest_link"]: route for route in decisions[0]["routes"]}
    assert routes["3"]["rel_flow"] == EXPECTED_THROUGH_REL_FLOW
    assert routes["5"]["rel_flow"] == EXPECTED_LEFT_REL_FLOW
    assert routes["3"]["share_pct"] == 44.4
    assert routes["5"]["share_pct"] == 55.6


def test_the_vehicle_input_and_composition_identity_round_trips(emitted):
    elements = parse_network_elements(emitted)
    assert vehicle_composition_names(emitted) == {"1": SITE}
    inputs = elements["vehicle_inputs"]
    assert len(inputs) == 1
    assert inputs[0]["id"] == "1"
    assert inputs[0]["link"] == "1"
    assert inputs[0]["composition_no"] == "1"
    assert inputs[0]["composition_name"] == SITE
    assert inputs[0]["composition_varies_by_interval"] is False


def test_the_volume_does_not_round_trip_through_inpx_parser_and_that_is_named(emitted):
    """The module docstring's ONE named gap, asserted so it cannot rot.

    ``inpx_parser`` is the map-geometry reader: it resolves a vehicle input to a
    point and a composition label, which is what a map glyph needs. The volume
    is not in what it returns — so the volume is proved by the direct XML round
    trip in the next test instead, and teaching the parser to read it is a named
    follow-up.
    """
    recovered = parse_network_elements(emitted)["vehicle_inputs"][0]
    assert "volume" not in recovered
    assert "volume_vph" not in recovered
    # …and neither the composition's flows nor a route's destPos/linkSeq come
    # back through the parser's own models.
    assert set(vehicle_composition_names(emitted).values()) == {SITE}
    route = parse_routing_decisions(emitted)[0]["routes"][0]
    assert set(route) == {"no", "name", "dest_link", "rel_flow", "share_pct"}


def test_the_full_chain_counts_to_xml_to_numbers_equal_to_the_source(tee, result):
    """counts → build_demand_seeding/seedable_counts → generate_demand → XML →
    parsed back → the source's own figures. The end-to-end claim, in one test."""
    demand = _generate(tee, result)
    root = ET.fromstring(emit.to_xml_text(tee, None, demand))

    # the counted source, computed here from the fixture rather than from the code
    summable = seedable_counts(result.counts)
    counted_total = sum(row.volume for row in summable)
    minutes = 60.0
    assert counted_total == 360.0

    volume = root.find("vehicleInputs/vehicleInput/timeIntVehVols/timeIntervalVehVolume")
    assert float(volume.get("volume")) == counted_total * 60.0 / minutes

    per_class: dict[str, float] = {}
    for row in summable:
        per_class[row.vehicle_class.code] = per_class.get(row.vehicle_class.code, 0.0) + row.volume
    emitted_flows = {
        element.get("vehType"): float(element.get("relFlow"))
        for element in root.findall(
            "vehicleCompositions/vehicleComposition/vehCompRelFlows/"
            "vehicleCompositionRelativeFlow"
        )
    }
    assert emitted_flows == {
        "100": per_class["승용"],
        "200": per_class["화물_소형"],
    }

    per_turn: dict[str, float] = {}
    for row in summable:
        key = row.movement.turn.value
        per_turn[key] = per_turn.get(key, 0.0) + row.volume
    emitted_routes = {
        element.get("destLink"): float(element.get("relFlow").split(":")[-1])
        for element in root.findall(
            "vehicleRoutingDecisionsStatic/vehicleRoutingDecisionStatic/vehRoutSta/"
            "vehicleRouteStatic"
        )
    }
    assert emitted_routes == {"3": per_turn["through"], "5": per_turn["left"]}
    # and the two arms describe the same hour: the routes sum to the input's
    # counted total, which is the invariant a wrong join would break
    assert sum(emitted_routes.values()) == counted_total
    assert sum(emitted_flows.values()) == counted_total


# ---------------------------------------------------------------------------
# 9. Compliance — three states, three languages, one report
# ---------------------------------------------------------------------------


def test_the_expected_demand_rules_are_graded(seeded):
    graded = {item.rule_id: item.state for item in seeded.compliance}
    assert graded == {
        "5.2.2-time-period-settings": ComplianceState.NOT_CHECKABLE,
        "5.2.3-vehicle-types-declared": ComplianceState.NOT_CHECKABLE,
        "5.5.1-composition-from-classified-counts": ComplianceState.APPLIED,
        "5.5.4.1-hourly-volume-recalculation": ComplianceState.APPLIED,
        "5.5.4.1-exact-volumes": ComplianceState.APPLIED,
        "5.5.4.1-fifteen-minute-profiles": ComplianceState.NOT_CHECKABLE,
        "5.5.4.2-routing-decision-position": ComplianceState.NOT_CHECKABLE,
        "5.5.4.2-combine-static-routing-decisions": ComplianceState.APPLIED,
        "5.5.4.2-routes-per-vehicle-class": ComplianceState.NOT_CHECKABLE,
        "5.6.1.3-demand-input-check": ComplianceState.APPLIED,
    }


def test_stochastic_volumes_and_uncombined_decisions_are_named_violations(tee, result):
    demand = _generate(
        tee,
        result,
        settings=DemandSettings(
            single_interval_reduction=REDUCTION_PEAK,
            volume_type=schema.VOL_TYPE_STOCHASTIC,
            combine_static_routing_decisions=False,
        ),
    )
    graded = {item.rule_id: item.state for item in demand.compliance}
    assert graded["5.5.4.1-exact-volumes"] is ComplianceState.VIOLATED
    assert graded["5.5.4.2-combine-static-routing-decisions"] is ComplianceState.VIOLATED
    root = ET.fromstring(emit.to_xml_text(tee, None, demand))
    volume = root.find("vehicleInputs/vehicleInput/timeIntVehVols/timeIntervalVehVolume")
    assert volume.get("volType") == schema.VOL_TYPE_STOCHASTIC
    decision = root.find("vehicleRoutingDecisionsStatic/vehicleRoutingDecisionStatic")
    assert decision.get("combineStaRoutDec") == "false"


def test_an_unknown_volume_type_is_rejected_at_the_settings_boundary():
    """``volType`` reaches the file verbatim, so a third value is stopped where
    it is typed rather than written into an .inpx and discovered in Vissim."""
    with pytest.raises(ValueError, match="volume_type"):
        DemandSettings(volume_type="APPROXIMATE")
    assert DemandSettings(volume_type=schema.VOL_TYPE_STOCHASTIC).volume_type == "STOCHASTIC"


def test_generation_warnings_reach_the_compliance_report(tee):
    rows = [
        _count(WEDNESDAY + timedelta(minutes=15 * i), turn=TurnType.THROUGH, volume=40)
        for i in range(4)
    ]
    demand = _generate(
        tee, ImportResult(adapter=ADAPTER, counts=rows), vehicle_types={"승용": VehicleTypeRef(100, 50)}
    )
    warnings_item = next(
        item for item in demand.compliance if item.rule_id == "5.5-demand-generation-warnings"
    )
    assert warnings_item.state is ComplianceState.VIOLATED
    assert "genesis_demand_turn_not_matched" in warnings_item.subjects


def test_every_demand_compliance_item_renders_in_all_three_languages(seeded):
    """A rule with no title in ``draft._RULE_TITLE`` renders as its own id, and a
    reason with no label raises — so this is the registration test."""
    for item in seeded.compliance:
        for language in ("ko", "en", "ja"):
            rendered = item.render(language)
            assert item.title(language) != item.rule_id, item.rule_id
            assert item.citation in rendered


def test_demand_items_join_the_drafts_own_report(tee, seeded):
    """U3 style: one report, not two."""
    report = tee.compliance
    before = report.counts()
    report.items.extend(seeded.compliance)
    after = report.counts()
    assert sum(after.values()) == sum(before.values()) + len(seeded.compliance)
    assert any(item.section.startswith("§5.5") for item in report.items)
    assert tee.render_compliance("ko")
    assert tee.render_compliance("en")
    assert tee.render_compliance("ja")


# ---------------------------------------------------------------------------
# 10. Citation traceability, and the counterfactual that proves it bites
# ---------------------------------------------------------------------------

MINIMUM_CITATIONS = 10


def _extract_text() -> str:
    return _normalise_prose((REPO_ROOT / EXTRACT).read_text(encoding="utf-8"))


def test_demand_citations_quote_the_section_5_extract_verbatim():
    constants = _citation_constants(demand_module)
    assert len(constants) >= MINIMUM_CITATIONS
    for name, citation in sorted(constants.items()):
        assert EXTRACT in citation, f"{name} does not name the file it cites"

    unquotable, missing = _citation_traceability_failures(
        demand_module, EXTRACT, _extract_text()
    )
    assert unquotable == [], f"citations quoting nothing checkable: {unquotable}"
    assert missing == [], f"fragments not found in the extract: {missing}"


def test_demand_citation_locators_name_a_section_and_a_doc_page():
    import re

    for name, citation in sorted(_citation_constants(demand_module).items()):
        locator = citation.split(f"({EXTRACT})")[0]
        assert "MRWA OMG v2.0" in locator, name
        assert re.search(r"doc p\.\d+", locator), name
        assert "Figure" not in locator, f"{name} names a Figure; §5 mislabels its Tables"


def _citations_from_source(path: Path, name: str) -> ModuleType:
    """Load only the ``CIT_*`` assignments out of a source file, so the
    counterfactual can corrupt one without importing the whole module."""
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    namespace: dict[str, str] = {"EXTRACT": EXTRACT}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id.startswith("CIT_"):
                exec(  # noqa: S102 - a literal f-string over one known name
                    compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"),
                    namespace,
                )
    module = ModuleType(name)
    for key, value in namespace.items():
        setattr(module, key, value)
    return module


def test_a_digit_swapped_demand_citation_fails_the_traceability_check(tmp_path):
    """The counterfactual: corrupt one quoted number and the check must bite.

    §5.5.4.1's worked example is the perfect target — "an analysed 15 minute
    flow is 200 vehicles, then the value for the vehicle input must be 800" is
    exactly the arithmetic this module implements, so a citation that no longer
    matches the document is a citation that no longer defends the code.
    """
    source = Path(demand_module.__file__)
    honest_copy = tmp_path / "demand_honest.py"
    honest_copy.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    honest = _citations_from_source(honest_copy, "demand_honest")
    assert len(_citation_constants(honest)) >= MINIMUM_CITATIONS
    assert _citation_traceability_failures(honest, EXTRACT, _extract_text()) == ([], [])

    # The corruption has to land on a CONTIGUOUS string literal — the citation
    # is written as adjacent literals, and the module docstring quotes the same
    # sentence across a line break, so this fragment occurs exactly once.
    original = source.read_text(encoding="utf-8")
    target = "an analysed 15 minute flow is 200 vehicles"
    assert original.count(target) == 1
    typo_copy = tmp_path / "demand_typo.py"
    typo_copy.write_text(
        original.replace(target, "an analysed 15 minute flow is 250 vehicles"),
        encoding="utf-8",
    )
    corrupted = _citations_from_source(typo_copy, "demand_typo")
    unquotable, missing = _citation_traceability_failures(
        corrupted, EXTRACT, _extract_text()
    )
    assert unquotable == []
    assert [name for name, _ in missing] == ["CIT_S5541_HOURLY_VOLUMES"]

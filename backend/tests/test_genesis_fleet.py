"""The §5.2.3 vehicle-type layer — the MRWA constants, the map, the compose chain.

What this file proves, in the order it matters:

1. **The MRWA §5.2.3 tables are the extract's own numbers** — Table 5-1's type
   set, Table 5-2's heavy desired acceleration, Table 5-3's power/weight and
   Table 5-4's colours, spot-checked against hand-copied values so a transcription
   drift fails here.
2. **The canonical → type map is defensible, and the class that does not map is
   NAMED** — LIGHT→1, HEAVY→3 (a setting), BUS→30; the Daejeon ``화물_소형`` 1.2
   left canonical=None, and a motorcycle bucket §5.2.3 has no type for, are named
   in the coverage, never force-fitted (refused under full coverage).
3. **The compose chain end to end** — synthetic counts → ``build_fleet`` →
   ``generate_demand(vehicle_types=fleet.map)`` → emit BOTH → parse back → the
   composition rows now resolve to vehicle types the SAME file declares. demand.py's
   disclosure #2 is MUST-NOT-TOUCH, so it still fires; what changed is that the
   reference now RESOLVES, which is asserted directly.
4. **Every reference resolves in-file** — no dangling ``vehType`` / ``desSpeedDistr``
   / colour / accel-decel / 2D3D reference, the failure class this project exists
   to catch.
5. **The type-less path is byte-identical** to what the emitter produced before
   this layer existed.
6. **The refusals fire**, one per named code, each carrying ko/en/ja.
7. **The citations quote the §5 extract**, with the mutation counterfactual.
8. **What round-trips and what does not** is asserted, so the module docstring's
   named gap cannot rot.

The fixture — one T-junction, four 15-minute bins, three canonical classes
------------------------------------------------------------------------
``car`` (LIGHT_VEHICLE), ``truck`` (HEAVY_VEHICLE) and ``bus`` (BUS) — the three
KOREA_SMART_INTERSECTION_6 canonicals that DO map, so the compose chain resolves
end to end. ``화물_소형`` (canonical None) is added only in the coverage tests.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from services.dataimport.base import AdapterInfo, ImportResult, IssueSeverity
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
from services.genesis import emit, schema
from services.genesis import fleet as fleet_module
from services.genesis.demand import DemandSettings, REDUCTION_PEAK, VehicleTypeRef, generate_demand
from services.genesis.draft import (
    ComplianceState,
    DraftConnector,
    DraftLink,
    DraftNetwork,
    DraftNode,
)
from services.genesis.fleet import (
    MRWA_FLEET,
    NO_FLEET,
    REFUSE_CODES,
    TABLE_5_2_HEAVY_DES_ACCEL,
    TABLE_5_2_SPEED_KMH,
    TABLE_5_3_POWER_WEIGHT,
    TABLE_5_4_COLOURS,
    DraftFleet,
    FleetSettings,
    build_fleet,
    fleet_containers,
    observed_vehicle_classes,
)
from services.genesis.osm import EXTRACT
from services.inpx_parser import parse_network_elements, vehicle_composition_names

# One implementation of the citation comparison, reused (never re-implemented).
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
LEG = "가람방면"
APPROACH_LENGTH_M = 150.0


def _class(code: str, canonical: CanonicalVehicleClass | None) -> VehicleClassRef:
    return VehicleClassRef(
        scheme=ClassificationScheme.KOREA_SMART_INTERSECTION_6, code=code, canonical=canonical
    )


CAR = _class("승용", CanonicalVehicleClass.LIGHT_VEHICLE)
TRUCK = _class("화물_대형", CanonicalVehicleClass.HEAVY_VEHICLE)
BUS = _class("버스_소형", CanonicalVehicleClass.BUS)
SMALL_TRUCK = _class("화물_소형", None)  # 1.2 left this canonical=None
ALL_CLASSES = VehicleClassRef(
    scheme=ClassificationScheme.UNCLASSIFIED, code="합계", canonical=CanonicalVehicleClass.ALL_VEHICLES
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _count(start, turn, volume, vehicle_class, *, minutes=15):
    return TurningCountData(
        provenance=PROV,
        site_id=SITE,
        approach=ApproachRef(leg_id=LEG),
        movement=MovementRef(turn=turn),
        vehicle_class=vehicle_class,
        interval_start=start,
        interval_end=start + timedelta(minutes=minutes),
        volume=volume,
        volume_unit=VolumeUnit.VEHICLES,
    )


def _counts():
    rows = []
    for index in range(4):
        start = WEDNESDAY + timedelta(minutes=15 * index)
        for vc, vol in ((CAR, 30), (TRUCK, 10), (BUS, 5)):
            rows.append(_count(start, TurnType.THROUGH, vol, vc))
            rows.append(_count(start, TurnType.LEFT, vol, vc))
    return rows


@pytest.fixture
def result():
    return ImportResult(adapter=ADAPTER, counts=_counts())


def _link(link_id, name, points, from_node, to_node):
    return DraftLink(
        id=link_id, name=name, osm_way_id=link_id, segment_index=0, forward=True, lanes=1,
        lane_width_m=3.5, points=points, lane_provenance={"source": "test"}, highway="residential",
        from_node_osm_id=from_node, to_node_osm_id=to_node, side_offset_m=0.0,
    )


def _connector(connector_id, from_link, to_link, turn, angle):
    return DraftConnector(
        id=connector_id, name=f"{from_link}->{to_link}", node_osm_id=1, node_id=1,
        from_link=from_link, to_link=to_link, from_pos_m=APPROACH_LENGTH_M, to_pos_m=0.0, lanes=1,
        lane_width_m=3.5, points=[(0.0, 0.0), (1.0, 1.0)], turn=turn, heading_change_deg=angle,
        lane_change="same", from_lanes=[1], to_lanes=[1],
    )


@pytest.fixture
def tee():
    links = [
        _link(1, "North approach", [(0.0, APPROACH_LENGTH_M), (0.0, 0.0)], 101, 1),
        _link(3, "South exit", [(0.0, 0.0), (0.0, -APPROACH_LENGTH_M)], 1, 103),
        _link(5, "East exit", [(0.0, 0.0), (APPROACH_LENGTH_M, 0.0)], 1, 105),
    ]
    connectors = [_connector(10001, 1, 3, "through", 0.0), _connector(10002, 1, 5, "left", 90.0)]
    node = DraftNode(
        id=1, name="n1", osm_id=1, x=0.0, y=0.0,
        polygon=[(-15.0, -15.0), (15.0, -15.0), (15.0, 15.0), (-15.0, 15.0)],
    )
    return DraftNetwork(name="tee", source_osm="tee.osm", links=links, nodes=[node], connectors=connectors)


@pytest.fixture
def fleet(result):
    return build_fleet(observed_vehicle_classes(result))


@pytest.fixture
def demand(tee, result, fleet):
    return generate_demand(
        tee, result, approach_links={SITE: {LEG: 1}}, vehicle_types=dict(fleet.map),
        settings=DemandSettings(single_interval_reduction=REDUCTION_PEAK),
    )


@pytest.fixture
def emitted(tee, demand, fleet):
    return ET.fromstring(emit.to_xml_text(tee, None, demand, fleet))


# ---------------------------------------------------------------------------
# 1. The MRWA §5.2.3 tables are the extract's own numbers
# ---------------------------------------------------------------------------


def test_table_5_1_type_set():
    by_no = {t.no: t for t in MRWA_FLEET}
    assert set(by_no) == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 30}
    assert (by_no[1].name, by_no[1].category) == ("Short", "CAR")
    assert (by_no[3].name, by_no[3].category) == ("Two axle truck", "HGV")
    assert (by_no[12].name, by_no[12].category) == ("Triple road train", "HGV")
    assert (by_no[30].name, by_no[30].category) == ("Bus", "BUS")
    # only heavy types (3..12) carry Table 5-2 desired acceleration
    assert {t.no for t in MRWA_FLEET if t.heavy_desired_accel} == {3, 4, 5, 6, 7, 8, 9, 10, 11, 12}


def test_table_5_2_heavy_desired_acceleration_values():
    """Spot-checked against the extract's Table 5-2 (doc p.184)."""
    assert TABLE_5_2_SPEED_KMH == (0, 32, 40, 48, 56, 64, 72, 80, 88, 96, 120)
    # types 1, 2 are NOT in the table (§5.2.3.2 scopes it to heavy)
    assert set(TABLE_5_2_HEAVY_DES_ACCEL) == {3, 4, 5, 6, 7, 8, 9, 10, 11, 12}
    row3 = TABLE_5_2_HEAVY_DES_ACCEL[3]
    assert row3["y"][0] == 1.053 and row3["yMin"][0] == 0.664 and row3["yMax"][0] == 1.930
    assert row3["y"][-1] == 0.0  # 120 km/h terminal anchor
    # every series is an 11-tuple aligned to the speed axis
    for series in TABLE_5_2_HEAVY_DES_ACCEL.values():
        for key in ("yMin", "y", "yMax"):
            assert len(series[key]) == len(TABLE_5_2_SPEED_KMH)


def test_table_5_2_inverted_envelope_is_emitted_verbatim_and_named(fleet):
    """Table 5-2's envelope is inverted (yMin > yMax) at 96 km/h for every heavy
    type. The values are MRWA's own: they are emitted VERBATIM (never silently
    swapped — that would be inventing), and the anomaly is NAMED with a WARN."""
    # the finding is real and on the default path (type 3)
    anomalies3 = fleet_module.table_5_2_envelope_anomalies(3)
    assert anomalies3 == [(96, 0.098, 0.043, 0.031)]  # yMin > y > yMax
    # a fleet with a heavy type warns about it
    assert "genesis_fleet_table_5_2_envelope_inverted" in fleet.issue_codes(IssueSeverity.WARN)
    warn = next(i for i in fleet.warnings if i.code == "genesis_fleet_table_5_2_envelope_inverted")
    assert 3 in warn.context["inverted_points"]
    assert set(warn.messages) == {"ko", "en", "ja"}
    # a light-only fleet does not warn (no Table 5-2 type present)
    assert "genesis_fleet_table_5_2_envelope_inverted" not in build_fleet(
        [CAR, BUS]
    ).issue_codes(IssueSeverity.WARN)


def test_the_inverted_envelope_reaches_the_xml_unswapped(emitted):
    """The malformed point is written exactly as Table 5-2 states it — the point
    of the WARN is that the emission does not quietly repair the source."""
    fn = emitted.find("desAccelerationFunctions/desAccelerationFunction[@no='503']")  # type 3
    at96 = [p for p in fn.iter("accelerationFunctionDataPoint") if p.get("x") == "96"][0]
    assert (at96.get("yMin"), at96.get("y"), at96.get("yMax")) == ("0.098", "0.043", "0.031")


def test_table_5_3_power_weight_values():
    assert TABLE_5_3_POWER_WEIGHT["300 hp"] == (225.0, 7.5, 12.2, 32.1)
    assert TABLE_5_3_POWER_WEIGHT["600 hp"] == (450.0, 15.0, 24.3, 64.3)


def test_table_5_4_colours_and_bus_is_green():
    assert TABLE_5_4_COLOURS[1] == ("Short", (0, 255, 255))  # Aqua
    assert TABLE_5_4_COLOURS[6] == ("Bus", (38, 127, 0))  # §5.2.3.4 buses are green
    assert fleet_module._colour_hex((0, 255, 255)) == "ff00ffff"
    assert fleet_module._colour_hex((38, 127, 0)) == "ff267f00"


# ---------------------------------------------------------------------------
# 2. The canonical → MRWA-type map, and the class that does not map
# ---------------------------------------------------------------------------


def test_the_canonical_classes_that_map(fleet):
    assert fleet.ok
    assert {code: ref.veh_type_no for code, ref in fleet.map.items()} == {
        "승용": 1,  # LIGHT_VEHICLE → Table 5-1 type 1 Short
        "화물_대형": 3,  # HEAVY_VEHICLE → representative heavy (default type 3)
        "버스_소형": 30,  # BUS → Table 5-4 row 6 type 30 Bus
    }
    assert all(ref.des_speed_distr_no == 50 for ref in fleet.map.values())
    assert {t.no for t in fleet.types} == {1, 3, 30}


def test_the_heavy_representative_type_is_a_setting():
    """The coarse HEAVY_VEHICLE bucket cannot name a specific Austroads heavy
    type; which one represents it is a setting, and the default is 3."""
    default = build_fleet([TRUCK])
    assert default.map["화물_대형"].veh_type_no == 3
    six = build_fleet([TRUCK], settings=FleetSettings(heavy_representative_type_no=6))
    assert six.map["화물_대형"].veh_type_no == 6
    # and type 6 carries its OWN Table 5-2 desired acceleration
    root = ET.fromstring(emit.to_xml_text(_min_draft(), None, None, six))
    heavy_func = six.types[0].des_accel_func_no
    fn = root.find(f"desAccelerationFunctions/desAccelerationFunction[@no='{heavy_func}']")
    p0 = fn.find("accelFuncDataPts/accelerationFunctionDataPoint")
    assert p0.get("y") == "0.761"  # Table 5-2 type 6 at 0 km/h


def test_a_class_with_no_mrwa_type_is_named_not_mapped():
    """Mirrors 1.2's own call: ``화물_소형`` (canonical=None) and a motorcycle
    bucket §5.2.3 has no type for are named, never force-fitted."""
    moto = _class("오토바이", CanonicalVehicleClass.MOTORCYCLE)
    fleet = build_fleet([CAR, SMALL_TRUCK, moto])
    assert fleet.ok
    assert "화물_소형" not in fleet.map and "오토바이" not in fleet.map
    assert fleet.map["승용"].veh_type_no == 1
    cov = fleet.coverage.sorted_entries()
    assert cov["classes_unmapped"] == ["오토바이", "화물_소형"]
    assert cov["canonicals_uncovered"] == ["motorcycle"]  # None carries no canonical name
    assert "genesis_fleet_class_not_covered" in fleet.issue_codes(IssueSeverity.INFO)


def test_the_all_class_total_is_never_a_vehicle_type(result):
    """The 합계 aggregate 1.2 emits is not a class and carries no type."""
    classes = observed_vehicle_classes(ImportResult(adapter=ADAPTER, counts=result.counts + [
        _count(WEDNESDAY, TurnType.THROUGH, 45, ALL_CLASSES)
    ]))
    assert "합계" not in {c.code for c in classes}
    fleet = build_fleet(classes)
    assert "합계" not in fleet.map


# ---------------------------------------------------------------------------
# 3. The compose chain — counts → fleet → demand → emit → resolves in-file
# ---------------------------------------------------------------------------


def test_the_composition_resolves_to_types_the_same_file_declares(emitted):
    """The whole point of this unit: geometry + demand + fleet compose into ONE
    self-contained draft, and the composition rows resolve to declared types."""
    declared = {vt.get("no") for vt in emitted.findall("vehicleTypes/vehicleType")}
    referenced = {
        rf.get("vehType")
        for rf in emitted.findall(
            "vehicleCompositions/vehicleComposition/vehCompRelFlows/vehicleCompositionRelativeFlow"
        )
    }
    assert declared == {"1", "3", "30"}
    assert referenced == declared  # every composition target is declared here
    # the desired-speed distribution the composition references is declared here too
    speed_declared = {s.get("no") for s in emitted.findall("speedDistributions/speedDistribution")}
    speed_ref = {
        rf.get("desSpeedDistr")
        for rf in emitted.findall(
            "vehicleCompositions/vehicleComposition/vehCompRelFlows/vehicleCompositionRelativeFlow"
        )
    }
    assert speed_ref == {"50"} and speed_ref <= speed_declared


def test_every_fleet_reference_resolves_in_file(emitted):
    """No dangling reference — the failure class this project exists to catch.

    A vehicleType names five references and each is declared in the same file;
    the composition names two; the model distribution names a model. All resolve.
    """
    def declared(path):
        return {e.get("no") for e in emitted.findall(path)}

    def referenced(path, attr):
        return {e.get(attr) for e in emitted.findall(path)} - {None}

    checks = [
        ("vehicleTypes/vehicleType", "colorDistr1", "colorDistributions/colorDistribution"),
        ("vehicleTypes/vehicleType", "desAccelFunc", "desAccelerationFunctions/desAccelerationFunction"),
        ("vehicleTypes/vehicleType", "maxAccelFunc", "maxAccelerationFunctions/maxAccelerationFunction"),
        ("vehicleTypes/vehicleType", "desDecelFunc", "desDecelerationFunctions/desDecelerationFunction"),
        ("vehicleTypes/vehicleType", "maxDecelFunc", "maxDecelerationFunctions/maxDecelerationFunction"),
        ("vehicleTypes/vehicleType", "model2D3DDistr", "model2D3DDistributions/model2D3DDistribution"),
    ]
    for ref_path, attr, decl_path in checks:
        assert referenced(ref_path, attr) <= declared(decl_path), attr
    # the model distribution's own reference into models2D3D resolves
    model_refs = referenced(
        "model2D3DDistributions/model2D3DDistribution/model2D3DDistrEl/model2D3DDistributionElement",
        "model2D3D",
    )
    assert model_refs <= declared("models2D3D/model2D3D")


def test_the_demand_disclosure_still_fires_but_the_reference_now_resolves(tee, result, fleet):
    """demand.py is MUST-NOT-TOUCH, so its always-on INFO about the undeclared
    reference still fires; the fleet does not silence it, it RESOLVES it."""
    demand = generate_demand(
        tee, result, approach_links={SITE: {LEG: 1}}, vehicle_types=dict(fleet.map),
        settings=DemandSettings(single_interval_reduction=REDUCTION_PEAK),
    )
    assert "genesis_demand_vehicle_types_not_declared_here" in demand.issue_codes(IssueSeverity.INFO)
    # but the fleet declares exactly the types that INFO names as needed
    info = next(
        i for i in demand.issues if i.code == "genesis_demand_vehicle_types_not_declared_here"
    )
    assert set(info.context["veh_type_nos"]) == fleet.declared_type_nos()
    # and the cross-check confirms the compose is consistent (no absent target)
    assert fleet.check_composition_targets(demand) == []


def test_the_emitted_vehicle_type_matches_the_vissim_saved_shape(emitted):
    car = emitted.find("vehicleTypes/vehicleType[@no='1']")
    assert car.get("category") == "CAR"
    assert car.get("name") == "Short"
    assert car.get("colorDistr1") == "1"  # Table 5-4 "1 Short" Aqua
    assert car.get("model2D3DDistr") == "10"
    # the boilerplate Vissim defaults are present, the shrunk attrs are absent
    assert car.get("desCurveSpeedFunc") is None and car.get("occupDistr") is None
    assert car.get("powerDistr") is None and car.get("weightDistr") is None
    assert car.find("parkLotSelPars") is not None
    # the Table 5-4 colour is emitted as ff00ffff at share 1
    colour = emitted.find("colorDistributions/colorDistribution[@no='1']")
    el = colour.find("colorDistrEl/colorDistributionElement")
    assert el.attrib == {"color": "ff00ffff", "share": "1"}
    # the 2D/3D model carries its install-asset file3D verbatim
    seg = emitted.find("models2D3D/model2D3D[@no='1']/model2D3DSegs/model2D3DSegment")
    assert seg.get("file3D").endswith("Car - Fiat 500 (2018).u3dm")


# ---------------------------------------------------------------------------
# 4. Refusals — one per named code, each carrying ko/en/ja
# ---------------------------------------------------------------------------


def _assert_refused_and_empty(fleet: DraftFleet, code: str):
    assert not fleet.ok
    matches = [i for i in fleet.refusals if i.code == code]
    assert matches, f"{code} not raised; got {fleet.issue_codes(IssueSeverity.REFUSE)}"
    issue = matches[0]
    assert set(issue.messages) == {"ko", "en", "ja"}
    assert all(issue.messages[language].strip() for language in ("ko", "en", "ja"))
    assert fleet.types == () and dict(fleet.map) == {}
    assert fleet.emitting is False
    assert fleet_containers(fleet) == {}
    return issue


def test_a_class_without_a_type_refuses_under_full_coverage():
    fleet = build_fleet([CAR, SMALL_TRUCK], settings=FleetSettings(require_full_coverage=True))
    issue = _assert_refused_and_empty(fleet, "genesis_fleet_class_without_mrwa_type")
    assert issue.context["unmapped_classes"] == ["화물_소형"]
    assert issue.citation == fleet_module.CIT_S523_VEHICLE_TYPES


def test_a_speed_above_60_needs_main_roads():
    fleet = build_fleet([CAR], settings=FleetSettings(desired_speed_kmh=80))
    issue = _assert_refused_and_empty(fleet, "genesis_fleet_high_speed_needs_main_roads")
    assert issue.context["desired_speed_kmh"] == 80
    assert issue.citation == fleet_module.CIT_S5253_CONSULT


def test_an_unmeasured_speed_distribution_refuses():
    fleet = build_fleet([CAR], settings=FleetSettings(desired_speed_kmh=45))
    issue = _assert_refused_and_empty(fleet, "genesis_fleet_desired_speed_distribution_not_built")
    assert issue.context["available"] == [50, 60]


def test_a_composition_target_absent_from_the_fleet_refuses(tee, result):
    """A demand whose composition references a type the fleet did not declare —
    the compose-time consistency guard."""
    car_only = build_fleet([CAR])
    demand = generate_demand(
        tee, result, approach_links={SITE: {LEG: 1}},
        # the caller hands generate_demand a type map that includes a heavy type
        # the car-only fleet never declared
        vehicle_types={"승용": VehicleTypeRef(1, 50), "화물_대형": VehicleTypeRef(3, 50),
                       "버스_소형": VehicleTypeRef(30, 50)},
        settings=DemandSettings(single_interval_reduction=REDUCTION_PEAK),
    )
    issues = car_only.check_composition_targets(demand)
    codes = {i.code for i in issues}
    assert "genesis_fleet_composition_target_absent" in codes
    absent = next(i for i in issues if i.context.get("veh_type_no") == 3)
    assert set(absent.messages) == {"ko", "en", "ja"}


def test_every_declared_refuse_code_has_a_test_above():
    """``REFUSE_CODES`` is data, so this claim is testable rather than prose."""
    raised = {
        "genesis_fleet_class_without_mrwa_type",
        "genesis_fleet_high_speed_needs_main_roads",
        "genesis_fleet_desired_speed_distribution_not_built",
        "genesis_fleet_composition_target_absent",
    }
    assert raised == REFUSE_CODES


# ---------------------------------------------------------------------------
# 5. Additivity — the type-less path is byte-identical
# ---------------------------------------------------------------------------


def _min_draft():
    return DraftNetwork(
        name="tee", source_osm="tee.osm",
        links=[
            _link(1, "N", [(0.0, APPROACH_LENGTH_M), (0.0, 0.0)], 101, 1),
            _link(3, "S", [(0.0, 0.0), (0.0, -APPROACH_LENGTH_M)], 1, 103),
            _link(5, "E", [(0.0, 0.0), (APPROACH_LENGTH_M, 0.0)], 1, 105),
        ],
        nodes=[DraftNode(id=1, name="n1", osm_id=1, x=0.0, y=0.0,
                         polygon=[(-15.0, -15.0), (15.0, -15.0), (15.0, 15.0), (-15.0, 15.0)])],
        connectors=[_connector(10001, 1, 3, "through", 0.0), _connector(10002, 1, 5, "left", 90.0)],
    )


def test_type_less_draft_bytes_are_unchanged(tee, fleet):
    """The additivity guarantee, pinned rather than asserted: three ways of
    saying "no fleet" all produce the file the emitter produced before this layer."""
    baseline = emit.to_xml_text(tee)
    assert emit.to_xml_text(tee, None, None, None) == baseline
    assert emit.to_xml_text(tee, None, None, NO_FLEET) == baseline
    refused = build_fleet([CAR], settings=FleetSettings(desired_speed_kmh=99))
    assert not refused.ok
    assert emit.to_xml_text(tee, None, None, refused) == baseline
    # and a fleet-carrying file really is different, so the pin is not vacuous
    assert emit.to_xml_text(tee, None, None, fleet) != baseline
    assert "GEOMETRY ONLY" in baseline


def test_a_signals_and_demand_only_file_is_unchanged_by_the_new_param(tee, demand):
    """The existing signals/demand byte-identical pins still hold with fleet=None."""
    assert emit.to_xml_text(tee, None, demand) == emit.to_xml_text(tee, None, demand, None)
    assert emit.to_xml_text(tee, None, demand, None) == emit.to_xml_text(tee, None, demand, NO_FLEET)


def test_the_emission_is_deterministic(tee, result):
    first = build_fleet(observed_vehicle_classes(result))
    second = build_fleet(observed_vehicle_classes(result))
    assert emit.to_xml_text(tee, None, None, first) == emit.to_xml_text(tee, None, None, second)


def test_fleet_containers_is_empty_for_every_no_fleet_shape():
    assert fleet_containers(None) == {}
    assert fleet_containers(NO_FLEET) == {}
    assert fleet_containers(build_fleet([CAR], settings=FleetSettings(desired_speed_kmh=99))) == {}
    assert fleet_containers(build_fleet([])) == {}  # no classes → nothing to write


def test_the_containers_sit_in_vissims_own_order(emitted):
    tags = [child.tag for child in emitted if isinstance(child.tag, str)]
    assert tags == [tag for tag in schema.CONTAINER_ORDER if tag in tags]
    assert "vehicleTypes" in tags and tags[-1] == "vehicleTypes"


def test_the_provenance_comment_states_the_install_asset_boundary(tee, demand, fleet):
    text = emit.to_xml_text(tee, None, demand, fleet)
    comment = text[text.index("<!--"): text.index("-->")]
    assert "GEOMETRY + DEMAND + TYPES" in comment
    assert "VISSIM-INSTALL" in comment and ".u3dm" in comment


# ---------------------------------------------------------------------------
# 6. Compliance — three states, three languages, one report
# ---------------------------------------------------------------------------


def test_the_expected_fleet_rules_are_graded(fleet):
    graded = {item.rule_id: item.state for item in fleet.compliance}
    assert graded == {
        "5.2.3-vehicle-types-defined": ComplianceState.APPLIED,
        "5.2.3.1-2d3d-model": ComplianceState.NOT_CHECKABLE,
        "5.2.3.2-heavy-desired-acceleration": ComplianceState.APPLIED,  # a heavy type is present
        "5.2.3.3-power-weight": ComplianceState.NOT_CHECKABLE,
        "5.2.3.4-colour-distributions": ComplianceState.APPLIED,
        "5.2.4-vehicle-classes": ComplianceState.NOT_CHECKABLE,
        "5.2.5.3-desired-speed-profile": ComplianceState.APPLIED,
    }


def test_heavy_acceleration_is_not_checkable_when_no_heavy_type_present():
    light_only = build_fleet([CAR, BUS])
    graded = {item.rule_id: item.state for item in light_only.compliance}
    assert graded["5.2.3.2-heavy-desired-acceleration"] is ComplianceState.NOT_CHECKABLE


def test_every_fleet_compliance_item_renders_in_all_three_languages(fleet):
    """Every rule_id is registered in ``draft._RULE_TITLE`` (so its title is not
    just its id), and every item carries its citation, in all three languages."""
    for item in fleet.compliance:
        for language in ("ko", "en", "ja"):
            rendered = item.render(language)
            assert item.title(language) != item.rule_id, item.rule_id
            assert item.citation in rendered


def test_fleet_items_join_the_drafts_own_report(tee, fleet):
    """One report, not two — the U3 style demand.py also follows."""
    report = tee.compliance
    before = report.counts()
    report.items.extend(fleet.compliance)
    after = report.counts()
    assert sum(after.values()) == sum(before.values()) + len(fleet.compliance)
    assert any(item.section.startswith("§5.2.3") for item in report.items)
    assert tee.render_compliance("ko") and tee.render_compliance("en") and tee.render_compliance("ja")


# ---------------------------------------------------------------------------
# 7. The round trip — what this repo's readers recover, and what they do not
# ---------------------------------------------------------------------------


def test_the_composition_identity_round_trips_but_the_fleet_does_not(emitted):
    """The module docstring's named gap, asserted so it cannot rot.

    ``inpx_parser`` — the map-geometry reader — recovers the composition no→name
    and the input→composition reference, and reads NONE of the vehicle types,
    functions or distributions this module emits. The fleet is proved by the
    direct XML round trip above; teaching a reader to surface it is a follow-up.
    """
    assert vehicle_composition_names(emitted) == {"1": SITE}
    elements = parse_network_elements(emitted)
    # the parser returns geometry/placement lists — no fleet container among them
    assert "vehicle_types" not in elements
    assert "speed_distributions" not in elements
    # the vehicleTypes container is really in the file, the parser just does not read it
    assert len(emitted.find("vehicleTypes")) == 3


def test_the_emitted_file_parses_from_disk(tee, demand, fleet, tmp_path):
    path = emit.emit_inpx(tee, tmp_path / "fleet.inpx", None, demand, fleet)
    root = ET.parse(path).getroot()
    assert {vt.get("no") for vt in root.findall("vehicleTypes/vehicleType")} == {"1", "3", "30"}
    # the file this repo's own reader opens still carries the demand
    assert len(root.find("vehicleInputs")) == 1


# ---------------------------------------------------------------------------
# 8. Citation traceability, and the counterfactual that proves it bites
# ---------------------------------------------------------------------------

MINIMUM_CITATIONS = 10


def _extract_text() -> str:
    return _normalise_prose((REPO_ROOT / EXTRACT).read_text(encoding="utf-8"))


def test_fleet_citations_quote_the_section_5_extract_verbatim():
    constants = _citation_constants(fleet_module)
    assert len(constants) >= MINIMUM_CITATIONS
    for name, citation in sorted(constants.items()):
        assert EXTRACT in citation, f"{name} does not name the file it cites"
    unquotable, missing = _citation_traceability_failures(fleet_module, EXTRACT, _extract_text())
    assert unquotable == [], f"citations quoting nothing checkable: {unquotable}"
    assert missing == [], f"fragments not found in the extract: {missing}"


def test_fleet_citation_locators_name_a_section_and_a_doc_page():
    import re

    for name, citation in sorted(_citation_constants(fleet_module).items()):
        locator = citation.split(f"({EXTRACT})")[0]
        assert "MRWA OMG v2.0" in locator, name
        assert re.search(r"doc p\.\d+", locator), name
        assert "Figure" not in locator, f"{name} names a Figure; §5 mislabels its Tables"


def _citations_from_source(path: Path, name: str) -> ModuleType:
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    namespace: dict[str, str] = {"EXTRACT": EXTRACT}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id.startswith("CIT_"):
                exec(  # noqa: S102
                    compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace
                )
    module = ModuleType(name)
    for key, value in namespace.items():
        setattr(module, key, value)
    return module


def test_a_word_swapped_fleet_citation_fails_the_traceability_check(tmp_path):
    """The counterfactual: corrupt one quoted word and the check must bite.

    §5.2.3.4's "The recommended colour for buses is green." is exactly the rule
    this module applies (Table 5-4's Bus colour), so a citation that no longer
    matches the document is one that no longer defends the code.
    """
    source = Path(fleet_module.__file__)
    honest = _citations_from_source(source, "fleet_honest")
    assert len(_citation_constants(honest)) >= MINIMUM_CITATIONS
    assert _citation_traceability_failures(honest, EXTRACT, _extract_text()) == ([], [])

    original = source.read_text(encoding="utf-8")
    target = "The recommended colour for buses is green"
    assert original.count(target) == 1
    typo_copy = tmp_path / "fleet_typo.py"
    typo_copy.write_text(
        original.replace(target, "The recommended colour for buses is blue"), encoding="utf-8"
    )
    corrupted = _citations_from_source(typo_copy, "fleet_typo")
    unquotable, missing = _citation_traceability_failures(corrupted, EXTRACT, _extract_text())
    assert unquotable == []
    assert [name for name, _ in missing] == ["CIT_S5234_BUS_GREEN"]

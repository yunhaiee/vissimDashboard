"""Tests for services.agent.health_check.HealthCheckRunner and
routers/health.py's GET /api/health-check — the "모델 건강검진" (model
health-check) one-click integrity report over the active dataset, no COM.

Three fixtures exercised, per the task brief:
  1. The bundled synthetic demo network (data/practice.inpx) — must work
     with zero Vissim/COM, and the demo's OWN genuine gap (no <evaluation>
     element at all) is a real, not-fabricated finding.
  2. A hand-built "unhealthy" .inpx combining fabricated defects across every
     dataset-checkable category: an over-capacity vehicle input, a Cont-chain
     volume mismatch, a Passive conflict area, a green split beyond its own
     cycle AND one below its own signal group's minimum green, a disarmed
     evaluation, and an orphaned desired-speed-distribution reference.
  3. The real Urban Intersection Beijing.CN_31360 example network, copied
     into services.datasets' active-dataset slot (an actual "uploaded
     dataset" simulation, not just a direct HealthCheckRunner call) —
     skipped when the example isn't present on this machine (same
     ``pytest.mark.skipif`` precedent as test_signal_parser.py's own Beijing
     fixture).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
import services.agent.tools_network as tools_network  # noqa: F401 - import registers tools
from routers import health as health_router
from services.agent.health_check import HealthCheckRunner
from services.datasets import get_active_dataset

DEMO_INPX = Path(__file__).resolve().parents[2] / "data" / "practice.inpx"
_BEIJING_INPX = (
    Path(__file__).resolve().parents[2]
    / "runtime"
    / "com-smoke"
    / "Urban Intersection Beijing.CN_31360"
    / "Intersection Beijing.inpx"
)
_requires_beijing = pytest.mark.skipif(
    not _BEIJING_INPX.is_file(), reason=f"real example network not present: {_BEIJING_INPX}"
)


# ---------------------------------------------------------------------------
# 1. Demo network — must work fully with no COM/live Vissim
# ---------------------------------------------------------------------------


def test_health_check_runner_against_demo_dataset():
    report = HealthCheckRunner().run(DEMO_INPX, dataset_meta=None)

    assert report["summary"]["checked_n"] == 14
    # 48, not 47: the sibling-disambiguation unit (2026-07-28) added
    # vehicle_input_siblings_share_link, a preflight-only constraint that no
    # dataset scan opts into -- so it lands in edit_time_only, by design.
    # 51, not 48: QUEUE 3 unit 2 (2026-07-29) added three more preflight-only
    # constraints for the new set_priority_rule_gap_headway tool
    # (priority_rule_marker_not_found / priority_rule_gap_headway_invalid /
    # priority_rule_gap_headway_implausible) -- all three need a proposed
    # action's params, so none opts into the dataset scan either, same shape
    # as vehicle_input_siblings_share_link above.
    # 58, not 51: QUEUE 3 (v2a) unit 3 (2026-07-29) added seven more
    # preflight-only constraints for the new
    # set_des_speed_distribution_points tool (des_speed_distribution_points_
    # count_invalid / _speed_negative / _speed_not_monotonic / _fraction_
    # invalid / _speed_implausible / _duplicate / _shared_target) -- all
    # seven need a proposed action's own "points" param, so none opts into
    # the dataset scan either, same shape as the priority-rule additions
    # above.
    # 60, not 58: the QUEUE (v2a) unit (2026-07-29) added two more
    # preflight-only constraints for the new set_vehicle_type_reference tool
    # (vehicle_type_reference_target_not_found / vehicle_type_shared_by_
    # compositions) -- both need a proposed action's own "field"/"target_no"
    # params, so neither opts into the dataset scan either, same shape as
    # every addition above.
    # 70, not 60: QUEUE (Q3, v2a) unit 5 (dwell-time distributions) added ten
    # more preflight-only constraints for the two new
    # set_time_distribution_normal/set_time_distribution_points tools
    # (time_distribution_type_mismatch / _mean_or_stddev_negative /
    # _mean_implausible / points_count_invalid / points_duration_negative /
    # points_duration_not_monotonic / points_fraction_invalid /
    # points_duration_implausible / points_duplicate / shared_target) -- all
    # ten need a proposed action's own params (time_distribution_no plus
    # mean_s/std_dev_s/points), so none opts into the dataset scan either,
    # same shape as every addition above.
    # 74, not 70: QUEUE 3 unit 6 (2026-07-31) added four more preflight-only
    # constraints for the three new driving-behavior duplicate-then-edit
    # tools (driving_behavior_parameter_range_invalid /
    # driving_behavior_parameter_value_implausible /
    # link_behavior_type_veh_class_override_not_found /
    # link_behavior_type_driving_behavior_target_not_found) -- all four need
    # a proposed action's own params, so none opts into the dataset scan
    # either, same shape as every addition above.
    # 75, not 74: QUEUE 3 unit 7 (2026-07-31) added one postwrite-only
    # constraint for the new rollback_last_edit tool
    # (rollback_invalidates_run_results) -- it needs a proposed rollback
    # action's own postwrite extra (run_since_edit, sourced from the
    # session's history), which a whole-network dataset scan has no
    # equivalent of, so it does not opt into the dataset scan either, same
    # shape as every addition above.
    # 76, not 75: QUEUE 3 unit 8 (2026-07-31) added one preflight-only
    # constraint for the new apply_edit_plan (batch/compound edits) tool
    # (apply_edit_plan_validation) -- it needs a proposed batch action's own
    # "steps" param (services.agent.batch.validate_plan), which a whole-
    # network dataset scan has no equivalent of, so it does not opt into the
    # dataset scan either, same shape as every addition above.
    # 77, not 76: Q4 unit (2) (measurement-infrastructure placement) added one
    # preflight-only constraint, measurement_infra_pos_out_of_range, for the
    # unit's new add_queue_counter/add_data_collection_point/
    # add_travel_time_section tools -- it needs a proposed action's own
    # link/pos params, which a whole-network dataset scan has no equivalent
    # of, so it does not opt into the dataset scan either, same shape as
    # every addition above.
    # 78, not 77: Q4 unit (3) (full link closure + turn bans) added one
    # preflight-only constraint, link_closure_route_consequence, for the new
    # set_link_closure/set_turn_ban tools -- it needs a proposed action's own
    # link_id/connector_no + closed/banned params, which a whole-network
    # dataset scan has no equivalent of, so it does not opt into the dataset
    # scan either, same shape as every addition above.
    # 79, not 78: Q4 unit (4) (signal-head + detector placement) added one
    # preflight-only constraint, signal_head_detector_pos_out_of_range, for the
    # unit's new add_signal_head/add_detector tools -- it needs a proposed
    # action's own link/pos params, which a whole-network dataset scan has no
    # equivalent of, so it does not opt into the dataset scan either, same
    # shape as every addition above.
    # 80, not 79: Q4 unit (5) (add/remove links & connectors) added one
    # preflight-only constraint, add_link_lane_count_high, for the new
    # add_link tool -- it needs a proposed action's own lane_count param,
    # which a whole-network dataset scan has no equivalent of, so it does
    # not opt into the dataset scan either, same shape as every addition
    # above.
    # 81, not 80: Q4 unit (6) (stop-sign + priority-rule/conflict-area
    # placement) added one preflight-only constraint,
    # add_priority_rule_marker_invalid, for the new add_priority_rule tool --
    # it needs a proposed action's own "markers" param, which a whole-network
    # dataset scan has no equivalent of, so it does not opt into the dataset
    # scan either, same shape as every addition above.
    # 82, not 81: Q4 unit (7) (PT stop + PT line placement) added one
    # preflight-only constraint, pt_line_stop_not_found, for the new
    # set_pt_line_stop_dwell tool -- it needs a proposed action's own
    # line_no/stop_no/time_distribution_no params, which a whole-network
    # dataset scan has no equivalent of, so it does not opt into the dataset
    # scan either, same shape as every addition above.
    # 86, not 82: Phase 0.3 (MRWA constraint/defaults pack) added four
    # preflight-only constraints -- priority_rule_gap_time_outside_mrwa_
    # table_5_9_range / priority_rule_gap_time_below_mrwa_roundabout_minimum
    # (both need a proposed set_priority_rule_gap_headway action's own
    # gap_time_s param) and add_detector_length_deviates_from_mrwa_default /
    # add_detector_setback_deviates_from_mrwa_default (both need a proposed
    # add_detector action's own length_m/pos_m params) -- none has a
    # whole-network dataset-scan equivalent, so all four land in
    # edit_time_only, same shape as every addition above.
    # 88, not 86: Q5 unit (2) (signal-controller genesis) added two
    # preflight-only constraints for the new add_signal_controller tool --
    # signal_controller_duplicate_source_type_unsupported and
    # signal_controller_duplicate_inherits_donor_program. Both key off a
    # PROPOSED action's own source_sc_no (which controller is being
    # duplicated right now), and a whole-network dataset scan has no
    # equivalent of that -- "this model contains a fixed-time controller" is
    # not a finding, it is just a fact -- so both land in edit_time_only,
    # same shape as every addition above.
    # 90, not 88: Q5 unit (3) (vehicle-input genesis) added two
    # preflight-only constraints for the new add_vehicle_input tool --
    # vehicle_input_on_connector and
    # vehicle_input_created_covers_first_interval_only. Both key off a
    # PROPOSED action's own link_no/volume_vph (which link demand is being
    # placed on right now, and how much), and a whole-network dataset scan
    # has no equivalent of that -- "this model contains connectors" and
    # "this model has 5 time intervals" are not findings, they are just
    # facts -- so both land in edit_time_only, same shape as every addition
    # above.
    assert report["summary"]["edit_time_only_n"] == 90
    # The demo's <evaluation> gap is genuine (see test_agent_read_tools.py's
    # own pinned shadow["evaluation_config"] == {"present": False}) -- one
    # real finding, not fabricated.
    ids = {f["constraint_id"] for f in report["findings"]}
    assert ids == {"evaluation_config_disarmed"}
    assert report["summary"]["findings_by_severity"] == {"issue": 0, "warning": 1, "info": 0}
    assert report["stats"] == {
        "links": 16,
        "nodes": 2,
        "vehicle_inputs": 6,
        "signal_controllers": 0,
        "conflict_areas": 0,
        "routing_decisions": 2,
        "routes": 6,
        "vehicle_compositions": 0,
        "des_speed_decisions": 0,
    }
    assert report["dataset"]["name"] == "practice.inpx"


def test_health_check_runner_edit_time_only_titles_and_ids_cover_the_rest_of_the_catalog():
    import services.agent.constraints as constraints_module

    report = HealthCheckRunner().run(DEMO_INPX)
    edit_time_ids = {e["constraint_id"] for e in report["edit_time_only"]}
    assert edit_time_ids == {c.id for c in constraints_module.get_edit_time_only_constraints()}
    for entry in report["edit_time_only"]:
        assert set(entry["title"].keys()) == {"ko", "en", "ja"}
        assert entry["tier"] in ("refuse", "warn")


# ---------------------------------------------------------------------------
# 2. Fabricated "unhealthy" fixture — one of every dataset-checkable defect
# ---------------------------------------------------------------------------

_UNHEALTHY_FIXTURE_INPX = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links>
    <link no="41" name="Approach"><lanes><lane width="3.5"/></lanes></link>
    <link no="99" name="Cross street"><lanes><lane width="3.5"/></lanes></link>
  </links>
  <vehicleInputs>
    <vehicleInput anmFlag="false" link="41" name="Over-capacity" no="1">
      <timeIntVehVols>
        <timeIntervalVehVolume cont="false" timeInt="1 0" vehComp="100" volType="EXACT" volume="3000"/>
      </timeIntVehVols>
    </vehicleInput>
    <vehicleInput anmFlag="false" link="41" name="Cont mismatch" no="2">
      <timeIntVehVols>
        <timeIntervalVehVolume cont="false" timeInt="1 0" vehComp="100" volType="EXACT" volume="500"/>
        <timeIntervalVehVolume cont="true" timeInt="1 1200000" vehComp="100" volType="EXACT" volume="900"/>
      </timeIntVehVols>
    </vehicleInput>
  </vehicleInputs>
  <conflictAreas>
    <conflictArea no="3" linkA="41" linkB="99" status="PASSIVE"/>
  </conflictAreas>
  <signalControllers>
    <signalController no="1" name="Main" type="FIXEDTIMESIMPLE" cycTm="60">
      <sgs>
        <signalGroup no="1" name="Beyond" endRed="10" endGreen="200" amber="3" redAmber="1" minGreen="5"/>
        <signalGroup no="2" name="TooShort" endRed="10" endGreen="12" amber="3" redAmber="1" minGreen="10"/>
      </sgs>
    </signalController>
  </signalControllers>
  <vehicleClasses>
    <vehicleClass no="10" name="Car"/>
  </vehicleClasses>
  <speedDistributions>
    <speedDistribution no="50" name="55 mph"/>
  </speedDistributions>
  <desSpeedDecisions>
    <desSpeedDecision no="6" name="" lane="41 1" pos="50">
      <vehClassDesSpeedDistribution vehClass="10" desSpeedDistr="999999"/>
    </desSpeedDecision>
  </desSpeedDecisions>
</network>
"""


@pytest.fixture()
def unhealthy_fixture_inpx(tmp_path: Path) -> Path:
    path = tmp_path / "unhealthy.inpx"
    path.write_text(_UNHEALTHY_FIXTURE_INPX, encoding="utf-8")
    return path


def test_health_check_runner_catches_every_fabricated_defect(unhealthy_fixture_inpx: Path):
    report = HealthCheckRunner().run(unhealthy_fixture_inpx)

    by_id: dict[str, list[dict[str, Any]]] = {}
    for f in report["findings"]:
        by_id.setdefault(f["constraint_id"], []).append(f)

    assert set(by_id) == {
        "latent_demand_overflow",
        "vehicle_input_cont_volume_mismatch",
        "conflict_area_data_unavailable",
        "green_split_outside_cycle",
        "green_split_below_min_green",
        "evaluation_config_disarmed",
        "des_speed_distribution_orphan_reference",
    }

    # 7 warnings, not 6, since the sibling-disambiguation unit (2026-07-28)
    # made latent_demand_overflow sibling-aware: link 41 carries BOTH vehicle
    # inputs (3000 + 500 = 3500 veh/h against a 1-lane 1900 capacity), so the
    # per-object scan now flags both as participating in the overflow. Input
    # "2" (500 veh/h on its own) was previously invisible here despite being
    # part of the same over-subscribed link.
    assert report["summary"]["findings_by_severity"] == {"issue": 1, "warning": 7, "info": 0}

    assert len(by_id["latent_demand_overflow"]) == 2
    latent = by_id["latent_demand_overflow"][0]
    assert latent["severity"] == "warning"
    assert latent["details"]["link_id"] == "41"
    assert latent["object_refs"] == ["41"]
    # Both findings agree on the LINK's total, which is the auditable number.
    for entry in by_id["latent_demand_overflow"]:
        assert entry["details"]["link_total_volume"] == 3500.0

    cont = by_id["vehicle_input_cont_volume_mismatch"][0]
    assert cont["details"]["mismatched_input_intervals"] == ["2.2"]

    passive = by_id["conflict_area_data_unavailable"][0]
    assert passive["details"]["passive_count"] == 1
    assert passive["details"]["link_pairs"] == ["41-99"]

    outside = by_id["green_split_outside_cycle"][0]
    assert outside["severity"] == "issue"  # refuse tier
    assert outside["details"]["sg_no"] == "1"
    assert outside["details"]["cycle_s"] == 60.0

    below_min = by_id["green_split_below_min_green"][0]
    assert below_min["details"]["sg_no"] == "2"

    disarmed = by_id["evaluation_config_disarmed"][0]
    assert disarmed["details"] == {"present": False}

    orphan = by_id["des_speed_distribution_orphan_reference"][0]
    assert orphan["details"]["offending_entries"] == ["6.10"]

    for finding in report["findings"]:
        for lang in ("ko", "en", "ja"):
            assert finding["message"][lang].strip()
        assert set(finding["title"].keys()) == {"ko", "en", "ja"}


# ---------------------------------------------------------------------------
# Bounded details (2026-07-27 observation lesson) — a hit list that scales
# with network size must never flood the report, machine-readable `details`
# included, not just the human-facing message.
# ---------------------------------------------------------------------------

_MANY_PASSIVE_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links></links>
  <conflictAreas>
{areas}
  </conflictAreas>
</network>
"""


@pytest.fixture()
def many_passive_conflict_areas_inpx(tmp_path: Path) -> Path:
    areas = "\n".join(
        f'    <conflictArea no="{i}" linkA="{i}" linkB="{i + 1000}" status="PASSIVE"/>' for i in range(1, 61)
    )
    path = tmp_path / "many_passive.inpx"
    path.write_text(_MANY_PASSIVE_TEMPLATE.format(areas=areas), encoding="utf-8")
    return path


def test_health_check_runner_bounds_details_list_length(many_passive_conflict_areas_inpx: Path):
    report = HealthCheckRunner().run(many_passive_conflict_areas_inpx)

    matches = [f for f in report["findings"] if f["constraint_id"] == "conflict_area_data_unavailable"]
    assert len(matches) == 1
    details = matches[0]["details"]
    # The TRUE count is never hidden...
    assert details["passive_count"] == 60
    # ...but the enumerated list is capped, with an explicit truncation marker.
    assert len(details["link_pairs"]) <= 20
    assert details["link_pairs_truncated_from"] == 60
    # ...and the severity summary reflects the true total finding count too
    # (one Finding here, since conflict_area_data_unavailable reports as one
    # finding covering every passive area -- unlike the per-object scans
    # below, which really do produce one Finding per offending object). This
    # minimal fixture also carries no <evaluation> element (same genuine gap
    # the demo network has), so evaluation_config_disarmed also legitimately
    # fires alongside it.
    assert {f["constraint_id"] for f in report["findings"]} == {
        "conflict_area_data_unavailable",
        "evaluation_config_disarmed",
    }
    assert report["summary"]["findings_by_severity"]["warning"] == 2


_MANY_OVER_CAPACITY_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<network>
  <links>
    <link no="1" name="L"><lanes><lane width="3.5"/></lanes></link>
  </links>
  <vehicleInputs>
{inputs}
  </vehicleInputs>
</network>
"""


@pytest.fixture()
def many_over_capacity_inputs_inpx(tmp_path: Path) -> Path:
    inputs = "\n".join(
        f'    <vehicleInput anmFlag="false" link="1" name="" no="{i}">'
        f'<timeIntVehVols><timeIntervalVehVolume cont="false" timeInt="1 0" '
        f'vehComp="100" volType="EXACT" volume="5000"/></timeIntVehVols></vehicleInput>'
        for i in range(1, 26)
    )
    path = tmp_path / "many_over_capacity.inpx"
    path.write_text(_MANY_OVER_CAPACITY_TEMPLATE.format(inputs=inputs), encoding="utf-8")
    return path


def test_health_check_runner_caps_findings_per_constraint_with_truncation_marker(
    many_over_capacity_inputs_inpx: Path,
):
    report = HealthCheckRunner().run(many_over_capacity_inputs_inpx)

    entries = [f for f in report["findings"] if f["constraint_id"] == "latent_demand_overflow"]
    # 25 offending vehicle inputs: capped enumeration + one truncation marker entry.
    real_findings = [e for e in entries if e.get("message") is not None]
    marker = [e for e in entries if e.get("message") is None]
    assert len(real_findings) == 20
    assert len(marker) == 1
    assert marker[0]["truncated_additional_count"] == 5
    # The severity summary still counts the TRUE total (25), not just what's
    # enumerated -- plus one more from evaluation_config_disarmed (this
    # minimal fixture also carries no <evaluation> element, same genuine gap
    # as the demo network).
    assert report["summary"]["findings_by_severity"]["warning"] == 26


# ---------------------------------------------------------------------------
# 3. GET /api/health-check — FastAPI TestClient
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    with TestClient(main.app) as test_client:
        yield test_client


def test_health_check_endpoint_against_demo_dataset(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(health_router, "get_active_inpx_path", lambda: DEMO_INPX)
    monkeypatch.setattr(health_router, "get_active_dataset", lambda: get_active_dataset())

    response = client.get("/api/health-check")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["checked_n"] == 14
    assert {f["constraint_id"] for f in body["findings"]} == {"evaluation_config_disarmed"}
    assert "generated_at" in body


def test_health_check_endpoint_json_download_variant_sets_attachment_header(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(health_router, "get_active_inpx_path", lambda: DEMO_INPX)
    monkeypatch.setattr(health_router, "get_active_dataset", lambda: get_active_dataset())

    response = client.get("/api/health-check", params={"format": "json"})

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["summary"]["checked_n"] == 14


# ---------------------------------------------------------------------------
# suggested_action classification (2026-07-28 "AI로 수정하기" handoff unit) —
# every finding's suggested_action.kind is DERIVED from the constraint's own
# applies_to + the CURRENT write-tool registry (services.agent.health_check.
# _derive_kind_and_tool), not a hand-maintained id->kind map, so it cannot
# silently drift as new write verbs ship. Tests target that derivation
# directly (covers all 14 dataset-checkable ids, including the two --
# cycle_below_intergreen_sum, ring_barrier_splits_sum_mismatch -- whose
# CATALOG-GAP datum no real .inpx populates today, so no fixture can make
# them actually FIRE) plus the end-to-end shape on findings that DO fire.
# ---------------------------------------------------------------------------

# The full expected table: constraint id -> (kind, tool). Deliberately
# exhaustive (asserted against constraints.get_dataset_checkable_ids() below)
# so a future dataset-checkable constraint that forgets to update this table
# fails loudly here, mirroring health_check.py's own "coverage honesty"
# principle for edit_time_only.
_EXPECTED_SUGGESTED_ACTION_KIND: dict[str, tuple[str, str | None]] = {
    "volume_out_of_range": ("fix", "set_vehicle_input_volume"),
    "latent_demand_overflow": ("fix", "set_vehicle_input_volume"),
    "cycle_below_intergreen_sum": ("fix", "set_signal_cycle"),
    "ring_barrier_splits_sum_mismatch": ("fix", "set_signal_cycle"),
    "conflicting_greens_in_signal_plan": ("inspect", None),  # applies_to names 2 tools -> ambiguous
    "green_split_outside_cycle": ("fix", "set_signal_green_split"),
    "green_split_below_min_green": ("fix", "set_signal_green_split"),
    "units_ambiguous_on_write": ("inspect", None),  # applies_to=["*"], no single verb
    "stale_route_files_after_edit": ("inspect", None),  # applies_to=["*"], no regenerate-route-file verb
    "conflict_area_data_unavailable": ("inspect", None),  # applies_to=["*"]; aggregate finding, no single target
    "route_integrity_unverifiable": ("inspect", None),  # applies_to=["*"]
    "evaluation_config_disarmed": ("none", None),  # override: documented always-refuse, no tool at all
    "vehicle_input_cont_volume_mismatch": ("inspect", None),  # applies_to=["*"]
    "des_speed_distribution_orphan_reference": ("inspect", None),  # applies_to=["*"]
}


def test_suggested_action_kind_derivation_covers_every_dataset_checkable_constraint():
    import services.agent.constraints as constraints_module
    import services.agent.health_check as health_check_module

    checkable_ids = constraints_module.get_dataset_checkable_ids()
    # The expected table is exhaustive over exactly this id set -- if a new
    # dataset-check constraint ships without a table entry, this fails here
    # rather than silently defaulting somewhere in the frontend.
    assert set(_EXPECTED_SUGGESTED_ACTION_KIND) == checkable_ids

    write_tools = health_check_module._write_tool_names()
    constraint_by_id = {c.id: c for c in constraints_module.get_registry()}
    for constraint_id, (expected_kind, expected_tool) in _EXPECTED_SUGGESTED_ACTION_KIND.items():
        kind, tool = health_check_module._derive_kind_and_tool(
            constraint_id, constraint_by_id.get(constraint_id), write_tools
        )
        assert (kind, tool) == (expected_kind, expected_tool), constraint_id

    # TWO hand-maintained overrides as of Q5 unit (3) -- every other id's
    # kind/tool above still comes purely from applies_to + the write
    # registry, so the derivation (not a second hardcoded table) is doing the
    # real work for 12 of the 14 ids.
    #
    # volume_out_of_range joined the list because Q5 unit (3) wired
    # add_vehicle_input onto that same constraint (identical 0..20000 veh/h
    # claim, only the param name differs). That made applies_to two-long,
    # and the arity rule then silently degraded this dataset finding from
    # ("fix", "set_vehicle_input_volume") to ("inspect", None) -- the health
    # page would have lost its Fix button for an out-of-range volume. THIS
    # test caught it. The override states the true remedy explicitly rather
    # than depending on an arity accident: the finding is always about an
    # EXISTING input the dataset scan just read, so the setter is the verb
    # that resolves it; add_vehicle_input creates a new input and could never
    # fix an existing one's volume.
    assert set(health_check_module._OVERRIDE_KIND) == {
        "evaluation_config_disarmed",
        "volume_out_of_range",
    }


def test_suggested_action_never_returns_fix_without_a_real_write_tool():
    """Structural invariant over the FULL constraint catalog (not just the
    14 dataset-checkable ids): kind="fix" must always carry a tool that is
    both (a) currently registered as a write-tier tool and (b) actually
    named in that constraint's own applies_to. Guards against a future
    constraint/tool rename silently producing a dead "Fix with AI" button."""
    import services.agent.constraints as constraints_module
    import services.agent.health_check as health_check_module

    write_tools = health_check_module._write_tool_names()
    assert "set_vehicle_input_volume" in write_tools  # sanity: registry is really populated
    for constraint in constraints_module.get_registry():
        kind, tool = health_check_module._derive_kind_and_tool(constraint.id, constraint, write_tools)
        if kind == "fix":
            assert tool is not None
            assert tool in write_tools
            assert tool in constraint.applies_to
        else:
            assert tool is None


def test_suggested_action_end_to_end_shape_on_unhealthy_fixture(unhealthy_fixture_inpx: Path):
    """The three classes as they actually appear on one report — a live
    check that _fix_target/_inspect_target produce grounded, non-empty
    payloads, not just that the classification table is right in isolation."""
    report = HealthCheckRunner().run(unhealthy_fixture_inpx)
    by_id = {f["constraint_id"]: f for f in report["findings"]}

    # Class A (fix): grounded with a concrete object id + the value(s)
    # needed to act, sourced from the scan's own action params
    # (input_no/sc_no are NOT in these constraints' own `details` — see
    # _fix_target's docstring) merged with the check's own details.
    #
    # Sibling-disambiguation unit (2026-07-28): link 41 now yields one
    # latent_demand_overflow per participating input, so this picks input "1"
    # explicitly rather than relying on dict-collapse order. Its recommended
    # ceiling is 1900 - 500 (the sibling's volume) = 1400, NOT the raw
    # capacity — the old answer would itself have left the link at 2400.
    fix = next(
        f for f in report["findings"]
        if f["constraint_id"] == "latent_demand_overflow" and f["suggested_action"]["target"]["input_no"] == "1"
    )["suggested_action"]
    assert fix["kind"] == "fix"
    assert fix["tool"] == "set_vehicle_input_volume"
    assert fix["target"]["input_no"] == "1"
    assert fix["target"]["volume"] == 3000.0
    assert fix["target"]["suggested_fix"] == {"max_recommended_volume": 1400.0}
    # FIX 4 (2026-07-28): _FIX_TARGET_FIELDS now carries the sibling/link-
    # total fields the check itself computes, so the handoff prompt can state
    # the TRUE inequality (link total exceeds capacity) instead of falsely
    # asserting this one input's own volume does.
    assert fix["target"]["sibling_volume_total"] == 500.0
    assert fix["target"]["link_total_volume"] == 3500.0
    assert fix["target"]["interval_no"] == 1

    # The exact false-inequality case FIX 4 closes: input "2"'s OWN volume
    # (500) does NOT exceed capacity (1900) -- only the LINK total (3500,
    # input "1"'s 3000 + this input's own 500) does. Before this fix, the
    # frontend handoff prompt had no way to say that (no sibling/link-total
    # fields in the target at all) and stated "500 exceeds 1900", which is
    # false.
    fix2 = next(
        f for f in report["findings"]
        if f["constraint_id"] == "latent_demand_overflow" and f["suggested_action"]["target"]["input_no"] == "2"
    )["suggested_action"]
    assert fix2["target"]["volume"] == 500.0
    assert fix2["target"]["capacity"] == 1900
    assert fix2["target"]["volume"] < fix2["target"]["capacity"]  # the false inequality FIX 4 closes
    assert fix2["target"]["link_total_volume"] == 3500.0
    assert fix2["target"]["link_total_volume"] > fix2["target"]["capacity"]  # what's actually true

    fix_green = by_id["green_split_outside_cycle"]["suggested_action"]
    assert fix_green["kind"] == "fix"
    assert fix_green["tool"] == "set_signal_green_split"
    assert fix_green["target"]["sc_no"] == "1"
    assert fix_green["target"]["sg_no"] == "1"
    assert fix_green["target"]["green_start_s"] == 10.0
    assert fix_green["target"]["green_end_s"] == 200.0
    assert fix_green["target"]["cycle_s"] == 60.0

    # Class B (inspect): no tool, but a bounded count+examples target.
    inspect = by_id["conflict_area_data_unavailable"]["suggested_action"]
    assert inspect == {"kind": "inspect", "tool": None, "target": {"count": 1, "examples": ["41-99"]}}

    inspect_cont = by_id["vehicle_input_cont_volume_mismatch"]["suggested_action"]
    assert inspect_cont["kind"] == "inspect"
    assert inspect_cont["target"]["examples"] == ["2.2"]

    # Class C (none): no button, no target of any use.
    none_action = by_id["evaluation_config_disarmed"]["suggested_action"]
    assert none_action == {"kind": "none", "tool": None, "target": {}}


def test_suggested_action_inspect_target_stays_bounded_never_the_full_list(
    many_passive_conflict_areas_inpx: Path,
):
    """The 2026-07-27 observation-flood lesson applied to the handoff prompt
    itself: 60 Passive conflict areas must never hand the agent (or a
    prompt string built from this) the full 60-item list — only a true
    count plus a FEW example ids, independent of the UI-facing `details`
    list's own (looser) 20-item cap."""
    report = HealthCheckRunner().run(many_passive_conflict_areas_inpx)
    finding = next(f for f in report["findings"] if f["constraint_id"] == "conflict_area_data_unavailable")
    action = finding["suggested_action"]

    assert action["kind"] == "inspect"
    assert action["target"]["count"] == 60  # true count, never hidden
    assert len(action["target"]["examples"]) <= 3  # tight prompt-grounding cap, well under the 20-cap on `details`


def test_health_check_endpoint_404s_when_active_inpx_missing(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(health_router, "get_active_inpx_path", lambda: Path("/no/such/file.inpx"))

    response = client.get("/api/health-check")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 4. Real example network, as an "uploaded dataset" (not a direct file path)
# ---------------------------------------------------------------------------


@_requires_beijing
def test_health_check_endpoint_against_real_beijing_example_as_uploaded_dataset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # Stand in for services.datasets.save_active_dataset's real upload path
    # (bytes copied to disk, resolved through the SAME get_active_inpx_path/
    # get_active_dataset seam routers/health.py itself calls) without
    # mutating the session-scoped ambient dataset state every other test
    # relies on (see conftest.py's _isolate_ambient_machine_state).
    uploaded_path = tmp_path / "Intersection Beijing.inpx"
    uploaded_path.write_bytes(_BEIJING_INPX.read_bytes())
    monkeypatch.setattr(health_router, "get_active_inpx_path", lambda: uploaded_path)
    monkeypatch.setattr(
        health_router,
        "get_active_dataset",
        lambda: {"source": "uploaded", "filename": "Intersection Beijing.inpx", "revision": "test-rev"},
    )

    response = client.get("/api/health-check")

    assert response.status_code == 200
    body = response.json()
    assert body["dataset"] == {"name": "Intersection Beijing.inpx", "revision": "test-rev", "source": "uploaded"}

    by_id = {f["constraint_id"]: f for f in body["findings"]}
    # Live-verified findings on the real network (backend/services/agent/
    # health_check.py's own investigation run, 2026-07-28) -- a real,
    # pre-existing defect this report surfaces with no fabrication:
    # controller 1 program 1 signal group 6's green window [131s -> 15s] is
    # outside its own 130s cycle.
    assert by_id["green_split_outside_cycle"]["severity"] == "issue"
    assert by_id["green_split_outside_cycle"]["details"]["sc_no"] == "1"
    assert by_id["green_split_outside_cycle"]["details"]["sg_no"] == "6"
    assert by_id["conflict_area_data_unavailable"]["details"]["passive_count"] == 145
    assert by_id["latent_demand_overflow"]["details"]["link_id"] == "41"
    assert body["stats"]["conflict_areas"] == 224

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
    assert report["summary"]["edit_time_only_n"] == 47
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

    assert report["summary"]["findings_by_severity"] == {"issue": 1, "warning": 6, "info": 0}

    latent = by_id["latent_demand_overflow"][0]
    assert latent["severity"] == "warning"
    assert latent["details"]["link_id"] == "41"
    assert latent["object_refs"] == ["41"]

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

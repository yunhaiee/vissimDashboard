"""Roadmap v2 §5.B — demand/fleet/signals wired into the genesis job pipeline.

Before this unit ``genesis_jobs.py`` imported osm/draft/emit/gate and nothing
else, so every generation a browser could start was GEOMETRY-ONLY — and the
gate's own measurement of a geometry-only network is that FOUR refuse-tier
checks cannot run (V1 no live evidence, V3 no vehicle inputs, V5 no signal
layer, V6 no demand layer), which makes ``published`` structurally unreachable.
That was recorded as a stated limit of B1, and it is what these tests move.

Two claims, and they pull in opposite directions on purpose:

* **A generation nobody added data to is unchanged.** Not "looks the same" —
  byte-identical to what the pre-existing call produced, asserted against a file
  emitted through the old signature.
* **A generation somebody DID add data to is composed, gated as composed, and
  refuses as a whole.** A side input that cannot build its layer refuses the
  generation with every reason named; there is no emit-what-we-can path,
  because a caller who uploads counts and receives a network with no demand has
  had their data silently dropped.

No COM anywhere: the composed artifact is still ``provisional``, because V1 is
still the check no offline run can satisfy.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import admin_headers
from services import datasets, genesis_jobs
from services.dataimport.base import AdapterInfo, ImportResult
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
from services.genesis import side_inputs as si
from services.genesis.draft import build_draft
from services.genesis.emit import emit_inpx
from services.genesis.osm import parse_osm_file

from tests.test_genesis_jobs import (  # noqa: F401 - imported fixtures
    client,
    crossroads_osm_file,
    workspace_root,
)
from tests.test_genesis_side_inputs import observed_history_controller

#: The crossroads ``.osm`` fixture drafts four approaches into ONE node.
#: ``entry_links``/``node_approaches`` both return these, and the leg names are
#: this file's own labels for them — a survey's labels, joined to link ids by
#: the map the caller supplies and nothing else.
LEG_OF_LINK = {1: "서측", 4: "동측", 5: "남측", 8: "북측"}
SITE = "십자로"
APPROACH_LINKS = {SITE: {leg: link for link, leg in LEG_OF_LINK.items()}}
APPROACH_PHASES = {1: "A", 4: "A", 5: "B", 8: "C"}

#: Each entry link's own dead-end exit in the crossroads draft (links 2/3/6/7
#: arrive FROM 1/4/5/8 respectively and carry no connector of their own — see
#: ``test_genesis_osm_draft.crossroads_osm``). Each is a link that genuinely
#: exists in the draft, so mapping a leg to it never trips the pre-existing
#: "unknown link" refusal — but it is neither an entry (no vehicle input) nor
#: does it have a departing connector (no routing decision), which is exactly
#: the CRITICAL silent-drop shape.
EXIT_OF_ENTRY = {1: 2, 4: 3, 5: 6, 8: 7}


# ---------------------------------------------------------------------------
# Fixtures — real neutral records for the crossroads the .osm fixture drafts
# ---------------------------------------------------------------------------


def _counts() -> ImportResult:
    """Four 15-minute bins per approach, all three turns, three classes.

    Flat, so peak == mean and every emitted figure is exact — the same choice
    ``genesis_fullstack_live_check`` makes, for the same reason.
    """

    def cls(code: str, canonical: CanonicalVehicleClass) -> VehicleClassRef:
        return VehicleClassRef(
            scheme=ClassificationScheme.KOREA_SMART_INTERSECTION_6,
            code=code,
            canonical=canonical,
        )

    classes = (
        (cls("승용", CanonicalVehicleClass.LIGHT_VEHICLE), 30),
        (cls("화물_대형", CanonicalVehicleClass.HEAVY_VEHICLE), 6),
        (cls("버스_소형", CanonicalVehicleClass.BUS), 3),
    )
    provenance = Provenance(source_file="counts.xlsx", locator="Sheet1!row 4")
    base = datetime(2026, 4, 8, 7, 0)
    rows: list[TurningCountData] = []
    for leg in LEG_OF_LINK.values():
        for index in range(4):
            start = base + timedelta(minutes=15 * index)
            for turn in (TurnType.LEFT, TurnType.THROUGH, TurnType.RIGHT):
                for vehicle_class, volume in classes:
                    rows.append(
                        TurningCountData(
                            provenance=provenance,
                            site_id=SITE,
                            approach=ApproachRef(leg_id=leg),
                            movement=MovementRef(turn=turn),
                            vehicle_class=vehicle_class,
                            interval_start=start,
                            interval_end=start + timedelta(minutes=15),
                            volume=volume,
                            volume_unit=VolumeUnit.VEHICLES,
                        )
                    )
    return ImportResult(
        adapter=AdapterInfo(
            name="test", region="KR-Daejeon", source_system="Test", version="0"
        ),
        counts=rows,
    )


def demand_document(**overrides) -> dict:
    document = {
        "kind": si.DEMAND_DOC_KIND,
        "counts": _counts().model_dump(mode="json"),
        "approach_links": APPROACH_LINKS,
        "demand_settings": {"single_interval_reduction": "peak"},
    }
    document.update(overrides)
    return document


def signals_document(**overrides) -> dict:
    controllers = ImportResult(
        adapter=AdapterInfo(name="test", region="AU-WA", source_system="SCATS", version="0"),
        controllers=[observed_history_controller()],
    )
    document = {
        "kind": si.SIGNALS_DOC_KIND,
        "controllers": controllers.model_dump(mode="json"),
        "assignments": [
            {
                "node_id": 1,
                "plan": {"source": "observed_history", "controller_id": "TCS999"},
                "approach_phases": {str(k): v for k, v in APPROACH_PHASES.items()},
            }
        ],
    }
    document.update(overrides)
    return document


def write_document(tmp_path: Path, name: str, document: dict) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def generate(osm_path: Path, **kwargs) -> dict:
    """Submit and run ONE generation synchronously — the same
    create+run pair ``start_genesis_job`` does, minus the thread."""
    record = genesis_jobs.create_genesis_job(osm_path=osm_path, **kwargs)
    return genesis_jobs.run_genesis_job(record["id"], record["workspace_id"])


def gate_states(job_id: str) -> dict[str, str]:
    payload = genesis_jobs.gate_payload(job_id, language="ko")
    return {check["id"]: check["state"] for check in payload["checks"]}


def artifact_text(record: dict) -> str:
    work_dir = genesis_jobs.job_dir(record["id"])
    return (work_dir / record["artifact"]["inpx"]).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# §1 A generation nobody added data to is unchanged
# ---------------------------------------------------------------------------


def test_a_generation_without_side_inputs_is_byte_identical_to_the_old_path(
    workspace_root: Path, crossroads_osm_file: Path, tmp_path: Path
):
    """The compatibility claim, at the only level that means anything.

    The right-hand side is the emitter call the pipeline made before side inputs
    existed — three positional layers omitted entirely. If composing changed one
    byte of a geometry-only generation, every stored gate verdict, every
    published dataset hash and the Murdoch benchmark would all be about a
    different file.
    """
    record = generate(crossroads_osm_file)
    work_dir = genesis_jobs.job_dir(record["id"])
    produced = work_dir / record["artifact"]["inpx"]

    # Re-drafted from the job's OWN frozen ``.osm``: the emitter writes the
    # source's name into the file's provenance comment, so drafting from the
    # caller's copy would differ for a reason that has nothing to do with
    # composition.
    draft = build_draft(
        parse_osm_file(work_dir / record["source_osm"]["stored_as"]), name=record["name"]
    )
    expected = emit_inpx(draft, tmp_path / "old-path.inpx")

    assert produced.read_bytes() == expected.read_bytes()
    assert record["side_inputs"] is None
    assert record["layers"] is None
    assert not (genesis_jobs.job_dir(record["id"]) / genesis_jobs.LAYERS_JSON_FILENAME).exists()
    assert record["status"] == genesis_jobs.STATUS_PROVISIONAL


def test_a_geometry_only_generation_still_cannot_reach_published(
    workspace_root: Path, crossroads_osm_file: Path
):
    """The limit this unit exists to move, measured rather than asserted: FOUR
    refuse-tier checks cannot run on a geometry-only network, so no live
    attestation could ever make the verdict a pass."""
    record = generate(crossroads_osm_file)
    states = gate_states(record["id"])

    assert states["V3"] == "not_checkable"
    assert states["V5"] == "not_checkable"
    assert states["V6"] == "not_checkable"
    assert set(record["gate"]["blocking"]) >= {"V1", "V3", "V5", "V6"}


# ---------------------------------------------------------------------------
# §2 A composed generation
# ---------------------------------------------------------------------------


def test_demand_and_signals_compose_into_one_gated_artifact(
    workspace_root: Path, crossroads_osm_file: Path, tmp_path: Path
):
    """The acceptance sentence: side inputs in, ONE .inpx out, the gate judging
    THAT file, and the checks that could not run on geometry alone running."""
    record = generate(
        crossroads_osm_file,
        demand_input=write_document(tmp_path, "demand.json", demand_document()),
        signals_input=write_document(tmp_path, "signals.json", signals_document()),
    )

    assert record["status"] == genesis_jobs.STATUS_PROVISIONAL, record.get("refusal")
    xml = artifact_text(record)
    for container in (
        "<vehicleTypes",
        "<vehicleInputs",
        "<vehicleCompositions",
        "<vehicleRoutingDecisionsStatic",
        "<signalControllers",
        "<signalHeads",
    ):
        assert container in xml, container

    states = gate_states(record["id"])
    assert states["V3"] == "pass"
    assert states["V5"] == "pass"
    assert states["V6"] == "pass"
    # V1 is the only refuse-tier check still blocking, and it is the one a job
    # may not satisfy: a generation never opens COM.
    assert record["gate"]["blocking"] == ["V1"]
    assert record["gate"]["verdict"] == "incomplete"


def test_the_layers_the_caller_asked_for_are_reported_by_count(
    workspace_root: Path, crossroads_osm_file: Path, tmp_path: Path
):
    record = generate(
        crossroads_osm_file,
        demand_input=write_document(tmp_path, "demand.json", demand_document()),
        signals_input=write_document(tmp_path, "signals.json", signals_document()),
    )
    layers = record["layers"]

    assert sorted(layers["requested"]) == ["demand", "fleet", "signals"]
    assert layers["fleet"]["type_nos"] == [1, 3, 30]
    assert layers["demand"]["vehicle_inputs"] == 4
    assert layers["demand"]["routing_decisions"] == 4
    assert layers["signals"]["controllers"] == 1
    assert layers["signals"]["signal_groups"] == 4
    assert layers["compose_refusals"] == []


def test_demand_alone_still_leaves_the_signal_check_unrunnable(
    workspace_root: Path, crossroads_osm_file: Path, tmp_path: Path
):
    """Stated honestly rather than papered over: demand makes V3 and V6
    runnable, and V5 stays NOT_CHECKABLE because the file genuinely has no
    signal layer. The gate cannot tell "unsignalised" from "signals were never
    imported", and this unit does not teach it to guess."""
    record = generate(
        crossroads_osm_file,
        demand_input=write_document(tmp_path, "demand.json", demand_document()),
    )
    states = gate_states(record["id"])

    assert record["status"] == genesis_jobs.STATUS_PROVISIONAL
    assert (states["V3"], states["V6"]) == ("pass", "pass")
    assert states["V5"] == "not_checkable"
    assert set(record["gate"]["blocking"]) == {"V1", "V5"}
    assert record["layers"]["requested"] == ["fleet", "demand"]
    assert "signals" not in record["layers"]


def test_no_layer_set_can_reach_published_offline(
    workspace_root: Path, crossroads_osm_file: Path, tmp_path: Path
):
    """The safeguard that makes composing an un-LoadNet-ed layer set safe.

    Only geometry-alone and all-four-together have been opened by a real Vissim
    (``compose.py``'s docstring says which). The intermediate sets rest on the
    emitter's additivity — and it does not matter, because V1 is refuse-tier, a
    generation job never opens COM, and every one of these lands
    ``provisional`` with V1 blocking. ``published`` needs an operator's
    attestation about that specific artifact, whatever its layers.
    """
    documents = {
        "geometry": {},
        "demand_only": {"demand_input": write_document(tmp_path, "d1.json", demand_document())},
        "signals_only": {
            "signals_input": write_document(tmp_path, "s1.json", signals_document())
        },
        "all_layers": {
            "demand_input": write_document(tmp_path, "d2.json", demand_document()),
            "signals_input": write_document(tmp_path, "s2.json", signals_document()),
        },
    }
    for label, kwargs in documents.items():
        record = generate(crossroads_osm_file, name=f"set_{label}", **kwargs)
        assert record["status"] == genesis_jobs.STATUS_PROVISIONAL, (label, record)
        assert record["gate"]["verdict"] == "incomplete", label
        assert "V1" in record["gate"]["blocking"], label


def test_the_layers_report_lands_beside_the_artifact(
    workspace_root: Path, crossroads_osm_file: Path, tmp_path: Path
):
    record = generate(
        crossroads_osm_file,
        demand_input=write_document(tmp_path, "demand.json", demand_document()),
    )
    stored = genesis_jobs.job_dir(record["id"]) / genesis_jobs.LAYERS_JSON_FILENAME
    payload = json.loads(stored.read_text(encoding="utf-8"))

    assert payload["summary"] == record["layers"]
    assert payload["language"] == "ko"
    assert {item["layer"] for item in payload["compliance"]} == {"fleet", "demand"}
    assert payload["coverage"]["demand"]["sites_joined"] == [SITE]
    assert genesis_jobs.artifact_path(record["id"], "layers_json") == stored
    # The verdict payload carries the same summary, so a reader of "V6 passed"
    # can see what passed without a second request.
    assert genesis_jobs.gate_payload(record["id"])["layers"] == record["layers"]


def test_a_side_input_is_frozen_into_the_job_and_recorded_without_a_server_path(
    workspace_root: Path, crossroads_osm_file: Path, tmp_path: Path
):
    """Frozen at enqueue like the ``.osm``: what a generation was judged on has
    to still be there when somebody asks six months later. And the record
    crosses the HTTP boundary, so it carries a name and a digest and never a
    path."""
    source = write_document(tmp_path, "demand.json", demand_document())
    record = generate(crossroads_osm_file, demand_input=source)
    frozen = genesis_jobs.job_dir(record["id"]) / "demand.input.json"

    assert frozen.is_file()
    assert frozen.read_bytes() == source.read_bytes()
    entry = record["side_inputs"]["demand"]
    assert entry["filename"] == "demand.json"
    assert entry["stored_as"] == "demand.input.json"
    assert len(entry["sha256"]) == 64
    assert "path" not in entry
    assert str(tmp_path) not in json.dumps(record)

    # Deleting the ORIGINAL cannot change the generation: it is repeatable from
    # the frozen copy alone.
    source.unlink()
    assert genesis_jobs.gate_and_publish(record["id"])["status"] in genesis_jobs.GATED_STATUSES


# ---------------------------------------------------------------------------
# §3 A side input that refuses refuses the generation
# ---------------------------------------------------------------------------


def test_a_join_naming_a_link_the_draft_lacks_refuses_the_whole_generation(
    workspace_root: Path, crossroads_osm_file: Path, tmp_path: Path
):
    """No emit-what-we-can path. The caller asked for demand; a network without
    it is not a smaller success, it is their data silently dropped."""
    broken = demand_document(approach_links={SITE: dict(APPROACH_LINKS[SITE], 북측=9999)})
    record = generate(
        crossroads_osm_file,
        demand_input=write_document(tmp_path, "demand.json", broken),
    )

    assert record["status"] == genesis_jobs.STATUS_ERROR
    assert record["refusal"]["code"] == "side_inputs_refused"
    assert record["artifact"] is None
    assert any("9999" in reason for reason in record["refusal"]["reasons"])
    assert datasets.get_active_dataset()["source"] == "default"


def test_a_site_mapped_entirely_to_non_entry_links_refuses_the_whole_generation(
    workspace_root: Path, crossroads_osm_file: Path, tmp_path: Path
):
    """Required test #1 for the CRITICAL fix (Track G adversarial pass).

    Pre-fix, this reproduced the shipped defect exactly: every leg of ``SITE``
    joined to a link that exists in the draft but is not an entry and has no
    departing connector, and the composition read ``layers.ok: True`` with 0
    vehicle inputs and 1 orphaned composition — a job that would have landed
    ``provisional`` with the caller's demand silently absent from the
    artifact. Post-fix it must refuse the whole generation, the same as every
    other side-input refusal: there is no emit-what-we-can path."""
    broken = {SITE: {leg: EXIT_OF_ENTRY[link] for link, leg in LEG_OF_LINK.items()}}
    record = generate(
        crossroads_osm_file,
        demand_input=write_document(
            tmp_path, "demand.json", demand_document(approach_links=broken)
        ),
    )

    assert record["status"] == genesis_jobs.STATUS_ERROR
    assert record["refusal"]["code"] == "side_inputs_refused"
    assert record["artifact"] is None
    reasons = " ".join(record["refusal"]["reasons"])
    assert SITE in reasons, reasons
    for leg, link_id in broken[SITE].items():
        assert leg in reasons, reasons
        assert f"link {link_id}" in reasons, reasons
    assert datasets.get_active_dataset()["source"] == "default"


def test_a_refused_generation_still_explains_itself_in_the_layers_report(
    workspace_root: Path, crossroads_osm_file: Path, tmp_path: Path
):
    """A refusal is a deliverable. ``layers.json`` uses a FIXED name precisely so
    it exists for a generation that produced no artifact — the case where the
    coverage tables matter most."""
    broken = demand_document(approach_links={SITE: dict(APPROACH_LINKS[SITE], 북측=9999)})
    record = generate(
        crossroads_osm_file,
        demand_input=write_document(tmp_path, "demand.json", broken),
    )
    payload = json.loads(
        genesis_jobs.artifact_path(record["id"], "layers_json").read_text(encoding="utf-8")
    )

    assert record["layers"]["demand"]["emitted"] is False
    assert "genesis_demand_map_names_unknown_link" in record["layers"]["demand"]["refusals"]
    assert any(
        issue["code"] == "genesis_demand_map_names_unknown_link" for issue in payload["issues"]
    )


def test_a_signal_plan_that_cannot_be_converted_refuses_the_generation(
    workspace_root: Path, crossroads_osm_file: Path, tmp_path: Path
):
    """A node whose plan failed to convert would otherwise be quietly
    unsignalised in an otherwise successful network."""
    document = signals_document()
    document["assignments"][0]["plan"]["controller_id"] = "TCS000"
    record = generate(
        crossroads_osm_file,
        signals_input=write_document(tmp_path, "signals.json", document),
    )

    assert record["status"] == genesis_jobs.STATUS_ERROR
    assert record["refusal"]["code"] == "side_inputs_refused"
    assert any("TCS000" in reason for reason in record["refusal"]["reasons"])


def test_the_refusal_reasons_are_korean(
    workspace_root: Path, crossroads_osm_file: Path, tmp_path: Path
):
    broken = demand_document(approach_links={SITE: dict(APPROACH_LINKS[SITE], 북측=9999)})
    record = generate(
        crossroads_osm_file,
        demand_input=write_document(tmp_path, "demand.json", broken),
    )
    reason = record["refusal"]["reasons"][0]
    assert any("가" <= ch <= "힣" for ch in reason), reason


# ---------------------------------------------------------------------------
# §4 Over HTTP — the staged entrance, and nothing beside it
# ---------------------------------------------------------------------------


def _await_terminal(client: TestClient, job_id: str, timeout_s: float = 30.0) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        record = client.get(f"/api/genesis/jobs/{job_id}").json()
        if record.get("status") in genesis_jobs.TERMINAL_STATUSES:
            return record
        time.sleep(0.05)
    raise AssertionError(f"genesis job {job_id} never reached a terminal status")


def _stage(client: TestClient, kind: str, document: dict, headers: dict) -> str:
    response = client.post(
        f"/api/genesis/side-inputs?kind={kind}",
        content=json.dumps(document, ensure_ascii=False).encode("utf-8"),
        headers={"X-Filename": f"{kind}.json", **headers},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def test_end_to_end_a_composed_generation_over_http(
    client: TestClient, crossroads_osm_file: Path
):
    """B1's acceptance sentence with the layers on: one signed-in caller stages
    an ``.osm`` and two side documents, and the workspace ends up holding a
    composed network the gate judged as composed."""
    headers = admin_headers()

    staged = client.post(
        "/api/genesis/uploads",
        content=crossroads_osm_file.read_bytes(),
        headers={"X-Filename": "cross.osm", **headers},
    )
    assert staged.status_code == 200, staged.text

    demand_token = _stage(client, "demand", demand_document(), headers)
    signals_token = _stage(client, "signals", signals_document(), headers)

    started = client.post(
        "/api/genesis/jobs",
        json={
            "upload_token": staged.json()["token"],
            "name": "composed",
            "demand_token": demand_token,
            "signals_token": signals_token,
        },
        headers=headers,
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["id"]
    assert sorted(started.json()["side_inputs"]) == ["demand", "signals"]

    record = _await_terminal(client, job_id)
    assert record["status"] == genesis_jobs.STATUS_PROVISIONAL, record
    assert record["layers"]["signals"]["controllers"] == 1

    gate = client.get(f"/api/genesis/jobs/{job_id}/gate").json()
    states = {check["id"]: check["state"] for check in gate["checks"]}
    assert (states["V5"], states["V6"]) == ("pass", "pass")

    layers = client.get(
        f"/api/genesis/jobs/{job_id}/artifact/layers_json", headers=headers
    )
    assert layers.status_code == 200
    assert layers.json()["summary"] == record["layers"]

    assert client.get("/api/datasets/active").json()["filename"] == "composed.inpx"


def test_the_staging_response_counts_what_it_read(client: TestClient):
    """Before a generation is spent, a caller can compare "the counts I sent"
    with "the counts that arrived"."""
    headers = admin_headers()
    response = client.post(
        "/api/genesis/side-inputs?kind=demand",
        content=json.dumps(demand_document(), ensure_ascii=False).encode("utf-8"),
        headers={"X-Filename": "counts.json", **headers},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["kind"] == "demand"
    assert body["filename"] == "counts.json"
    assert len(body["sha256"]) == 64
    assert body["summary"]["counts"] == 4 * 4 * 3 * 3
    assert body["summary"]["sites_mapped"] == [SITE]


def test_staging_a_side_input_requires_a_session(client: TestClient):
    """A side input writes into a project's tree and steers what its dataset
    becomes — the same reason ``POST /uploads`` is authenticated."""
    assert (
        client.post("/api/genesis/side-inputs?kind=demand", content=b"{}").status_code == 401
    )


def test_a_non_member_cannot_stage_a_side_input_into_another_project(client: TestClient):
    """The workspace boundary is the middleware's, so it covers this route
    without the route asking — but a side input lands in a project's tree, so
    the ledger records that it really is covered."""
    from conftest import bearer_headers

    created = client.post(
        "/api/workspaces", json={"name": "다른 프로젝트"}, headers=admin_headers()
    )
    assert created.status_code == 200, created.text
    headers = {
        "X-Workspace-Id": created.json()["id"],
        **bearer_headers("layers-outsider", "modeler"),
    }
    assert (
        client.post(
            "/api/genesis/side-inputs?kind=demand", content=b"{}", headers=headers
        ).status_code
        == 403
    )


def test_an_unknown_side_input_kind_is_refused(client: TestClient):
    response = client.post(
        "/api/genesis/side-inputs?kind=fleet", content=b"{}", headers=admin_headers()
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "genesis_side_input_unknown_kind"


def test_a_document_that_cannot_build_a_layer_is_refused_at_upload(client: TestClient):
    """The ``stage_osm`` discipline: a caller finds out while they are still
    looking at the upload, not two steps later inside a job."""
    response = client.post(
        "/api/genesis/side-inputs?kind=demand",
        content=json.dumps(demand_document(approach_links={})).encode("utf-8"),
        headers={"X-Filename": "demand.json", **admin_headers()},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "genesis_side_input_invalid_demand"


def test_a_token_given_as_the_wrong_kind_is_refused(
    client: TestClient, crossroads_osm_file: Path
):
    """A demand document handed to the signals builder would refuse for a reason
    ("no assignments") that says nothing about the real mistake."""
    headers = admin_headers()
    staged = client.post(
        "/api/genesis/uploads",
        content=crossroads_osm_file.read_bytes(),
        headers={"X-Filename": "cross.osm", **headers},
    )
    demand_token = _stage(client, "demand", demand_document(), headers)

    response = client.post(
        "/api/genesis/jobs",
        json={"upload_token": staged.json()["token"], "signals_token": demand_token},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "genesis_side_input_kind_mismatch"


def test_an_unknown_side_input_token_is_a_404(
    client: TestClient, crossroads_osm_file: Path
):
    headers = admin_headers()
    staged = client.post(
        "/api/genesis/uploads",
        content=crossroads_osm_file.read_bytes(),
        headers={"X-Filename": "cross.osm", **headers},
    )
    response = client.post(
        "/api/genesis/jobs",
        json={"upload_token": staged.json()["token"], "demand_token": "0" * 32},
        headers=headers,
    )
    assert response.status_code == 404


def test_an_oversized_side_input_is_refused_with_the_limit(client: TestClient):
    headers = admin_headers()
    oversized = b'{"kind":"genesis.demand.v1",' + b" " * (si.MAX_SIDE_INPUT_BYTES + 1) + b"}"
    response = client.post(
        "/api/genesis/side-inputs?kind=demand",
        content=oversized,
        headers={"X-Filename": "demand.json", **headers},
    )
    assert response.status_code == 413
    assert str(si.MAX_SIDE_INPUT_BYTES // (1024 * 1024)) in response.json()["detail"]


def test_a_started_job_discards_its_staged_side_inputs_by_default(
    client: TestClient, crossroads_osm_file: Path
):
    """The staged originals go once the job owns its frozen copies — and
    ``keep_side_inputs`` is the explicit way to keep them, exactly as
    ``keep_upload`` is for the ``.osm``."""
    headers = admin_headers()
    staged = client.post(
        "/api/genesis/uploads",
        content=crossroads_osm_file.read_bytes(),
        headers={"X-Filename": "cross.osm", **headers},
    )
    demand_token = _stage(client, "demand", demand_document(), headers)

    started = client.post(
        "/api/genesis/jobs",
        json={"upload_token": staged.json()["token"], "demand_token": demand_token},
        headers=headers,
    )
    assert started.status_code == 202, started.text
    _await_terminal(client, started.json()["id"])

    with pytest.raises(genesis_jobs.StagedOsmNotFoundError):
        genesis_jobs.staged_side_input(demand_token)


def test_a_request_naming_no_side_input_is_the_old_request(
    client: TestClient, crossroads_osm_file: Path
):
    """Every new field is optional, and the geometry-only request body that
    worked before this unit still works unchanged."""
    headers = admin_headers()
    staged = client.post(
        "/api/genesis/uploads",
        content=crossroads_osm_file.read_bytes(),
        headers={"X-Filename": "cross.osm", **headers},
    )
    started = client.post(
        "/api/genesis/jobs",
        json={"upload_token": staged.json()["token"], "name": "plain"},
        headers=headers,
    )
    assert started.status_code == 202, started.text
    record = _await_terminal(client, started.json()["id"])

    assert record["side_inputs"] is None
    assert record["layers"] is None
    assert (
        client.get(
            f"/api/genesis/jobs/{record['id']}/artifact/layers_json", headers=headers
        ).status_code
        == 404
    )

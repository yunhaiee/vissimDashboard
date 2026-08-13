"""Roadmap v2 §5.B B1 + §5.A A4 — genesis as a job, with the gate as its exit door.

The unit's whole claim is one sentence: **a generated network can only become a
usable dataset by going through the gate.** So the tests are written the same way
round as the gate's own suite — for every way a caller might get past the gate
there is a test that must fail, and the three that matter most get their own
family:

* **§1 The exit door is structural, not documentary.** ``gate_and_publish``
  takes no verdict and no skip flag; ``_register_as_active_dataset`` has exactly
  ONE call site in the module, inside that function, after the REFUSED branch;
  and a gate that cannot run publishes nothing. The first two are asserted
  against the module's own SOURCE, because "we always remember to gate first" is
  precisely the convention this unit exists to replace.
* **§3 A REFUSED verdict never registers a dataset**, and a refusal is still a
  deliverable: the artifact and the verdict both stay on disk.
* **§4 V1's evidence is admitted by ``gate.py``, never manufactured here.**
  Evidence about a different build does not upgrade anything, and evidence that
  the load FAILED downgrades — including revoking a publication that had already
  landed.

Fixtures are synthetic: the 4-way crossroads ``.osm`` from
``tests/test_genesis_osm_draft.py`` (the real generator's own test input) and
the hand-written networks from ``tests/test_genesis_gate.py``. No client
geometry and no COM anywhere — a generation job may not open Vissim, which is
exactly why a fresh generation lands ``provisional``.
"""
from __future__ import annotations

import inspect
import json
import re
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import main
from conftest import admin_headers, bearer_headers
from services import datasets, genesis_jobs, osm_fetch, workspaces
from services.datasets import DatasetStorageError
from services.genesis.gate import CheckState, GateVerdict, Provenance

from tests.test_genesis_gate import (
    connector,
    link,
    network,
    node,
    straight_pair,
    write,
)
from tests.test_genesis_gate import _DEMAND_SUPPORT as demand_support
from tests.test_genesis_gate import _SUPPORT as gate_support
from tests.test_genesis_gate import _census as census_for_network
from tests.test_genesis_gate import _demand as demand_layer
from tests.test_genesis_gate import _signals as signal_layer


def census_for(inpx_xml: str) -> dict:
    """The census the generator would have written beside this artifact."""
    return census_for_network([], inpx_xml)
from tests.test_genesis_osm_draft import crossroads_osm

GENESIS_JOBS_SOURCE = Path(genesis_jobs.__file__).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private workspaces root for one test, with its default workspace bound.

    ``LEGACY_UPLOADS_ROOT`` is repointed alongside it for the reason every other
    workspace test states: patching only the first would aim the one-time
    migration at the developer's real ``runtime/uploads``.
    """
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "legacy-uploads")
    default_id = workspaces.default_workspace_id()
    token = workspaces.set_current_workspace_id(default_id)
    try:
        yield tmp_path / "workspaces"
    finally:
        workspaces.reset_current_workspace_id(token)


@pytest.fixture()
def client(workspace_root: Path) -> TestClient:
    return TestClient(main.app)


@pytest.fixture()
def crossroads_osm_file(tmp_path: Path) -> Path:
    path = tmp_path / "cross.osm"
    path.write_text(crossroads_osm(), encoding="utf-8", newline="")
    return path


def _generate(osm_path: Path, **kwargs) -> dict:
    """Submit and run ONE generation synchronously.

    ``create_genesis_job`` + ``run_genesis_job`` is exactly what
    ``start_genesis_job`` does, minus the thread — so a test asserts on the
    generation itself and never on a sleep. The HTTP end-to-end below
    deliberately takes the threaded path instead.
    """
    record = genesis_jobs.create_genesis_job(osm_path=osm_path, **kwargs)
    return genesis_jobs.run_genesis_job(record["id"], record["workspace_id"])


def _seed_job(inpx_xml: str, *, census: dict | None = None, name: str = "seeded") -> dict:
    """A job record whose artifact is a HAND-WRITTEN network.

    The gate must be able to refuse a genesis artifact, and — since the
    2026-08-11 isolation pass — the real generator no longer emits one that V3
    refuses (that is A1's whole result). So the refusable artifact is written by
    hand into a real job directory, through the module's own registry, and the
    REAL gate is what judges it. Nothing about the gate is stubbed.
    """
    job_id = uuid4().hex[:12]
    work_dir = genesis_jobs.job_dir(job_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / f"{name}.inpx").write_text(inpx_xml, encoding="utf-8", newline="")
    (work_dir / f"{name}.genesis.json").write_text(
        json.dumps(census or {"metadata": {"connectivity": {"dangling_approaches": []}}}),
        encoding="utf-8",
    )
    record = {
        "id": job_id,
        "workspace_id": workspaces.current_workspace_id(),
        "name": name,
        "status": genesis_jobs.STATUS_RUNNING,
        "phase": genesis_jobs.PHASE_GATING,
        "progress": 0.85,
        "created_at": "2026-08-11T00:00:00+00:00",
        "started_at": "2026-08-11T00:00:00+00:00",
        "completed_at": None,
        "submitted_by": "tester",
        "options": {"exclude_service": False},
        "source_osm": {"filename": "seed.osm", "bytes": 0, "sha256": "", "path": ""},
        "network": None,
        "artifact": {
            "inpx": f"{name}.inpx",
            "compliance_md": f"{name}.compliance.md",
            "genesis_json": f"{name}.genesis.json",
            "sha256": "",
            "bytes": 0,
        },
        "gate": None,
        "publication": None,
        "refusal": None,
        "error": None,
    }
    genesis_jobs._upsert(record)
    return record


def _probe_state(network_path: Path, *, links: int, connectors: int, nodes: int, **overrides) -> dict:
    """One run in ``tools/genesis_open_live_check.py``'s own ``state.json`` shape.

    Built with the artifact's REAL element counts, because V1 asserts the COM
    census against the file's own counts — the 2026-08-06 empty-load trap stated
    as a check. A hard-coded census here would make the test pass for a reason
    the product does not rely on.
    """
    com = {
        "network": str(network_path),
        "load_ok": True,
        "save_ok": True,
        "err_after_load": {"files": [], "new_files": []},
        "com_topology": {
            "link_collection_count": links + connectors,
            "is_conn_counts": {"plain_links": links, "connectors": connectors},
            "nodes_count": nodes,
        },
        "version": {"att_values": {"Version": "PTV Vissim Test"}},
    }
    com.update(overrides)
    return {
        "runs": {
            "live_a": {
                "label": "live_a",
                "started_utc": "2026-08-11T09:00:00+00:00",
                "network": str(network_path),
                "run": {"com": com},
            }
        }
    }


def _island_network() -> str:
    """A drivable pair plus one link nothing can enter or leave — V3's fault."""
    links = [
        link(1, [(0.0, 0.0), (100.0, 0.0)]),
        link(2, [(104.0, 0.0), (204.0, 0.0)]),
        connector(10001, 1, 2, [(100.0, 0.0), (104.0, 0.0)], from_pos=100.0),
        link(3, [(0.0, 400.0), (100.0, 400.0)], name="island"),
    ]
    return network(links=links)


# ---------------------------------------------------------------------------
# §1 The exit door is structural — A4
# ---------------------------------------------------------------------------


def test_exactly_one_call_site_can_register_a_generated_dataset():
    """A4 as a property of the source.

    ``_register_as_active_dataset`` must appear exactly twice in the module: its
    definition and ONE call. A second call site would mean a second road from a
    generated artifact to a project's dataset slot, and the gate would once again
    be something a code path can forget.
    """
    # ``name(`` rather than the bare name, so prose mentions in the docstrings
    # (which are allowed, and are how the rule is explained) are not counted as
    # call sites. Two matches: ``def _register_as_active_dataset(`` and the one
    # invocation.
    occurrences = re.findall(r"_register_as_active_dataset\(", GENESIS_JOBS_SOURCE)
    assert len(occurrences) == 2, (
        "expected exactly one definition and one call site of "
        f"_register_as_active_dataset, found {len(occurrences)} invocations"
    )
    body = inspect.getsource(genesis_jobs.gate_and_publish)
    assert "_register_as_active_dataset(" in body, (
        "the single call site must be inside gate_and_publish -- that is what makes "
        "'no publish without a gate' structural"
    )


def test_the_publish_funnel_cannot_be_handed_a_verdict():
    """It computes the verdict; it never accepts one.

    A ``verdict=``/``skip_gate=``/``already_gated=`` parameter is the exact shape
    A4 forbids: it would let a caller assert the answer the gate exists to
    determine.

    The set is pinned EXACTLY, so adding a parameter is a deliberate act rather
    than a drift. ``attestation`` was added that way: it names the human whose
    claim ``live_oracle`` rests on, and it is pure provenance — nothing reads it
    to decide a verdict, a state or whether to publish. The forbidden shapes are
    asserted absent separately, so the intent survives even if this set grows
    again for another provenance field.
    """
    parameters = set(inspect.signature(genesis_jobs.gate_and_publish).parameters)
    assert parameters == {"job_id", "live_oracle", "live_oracle_gap", "attestation"}
    for forbidden in ("verdict", "skip_gate", "already_gated", "gate_result", "force"):
        assert forbidden not in parameters
    assert "run_gate(" in inspect.getsource(genesis_jobs.gate_and_publish)


def test_the_verdict_and_its_report_are_recorded_before_any_branch():
    """The third source pin for A4's exit-door claim.

    The function's own docstring states it as a property, not a behaviour:
    "the full result and its ko report are written beside the artifact
    unconditionally, before any branch, so a refusal is as durable as a
    pass." The two pins above check WHO may register a dataset and WHAT the
    funnel's signature is; neither can catch a refactor that reordered the
    write past the ``REFUSED`` branch, because a behavioural test of the
    REFUSED path alone cannot distinguish "written before the branch" from
    "written inside the branch, on this path, by coincidence". This pins the
    ordering directly.

    Pinned on the identifiers the write and the branch use
    (``GATE_JSON_FILENAME`` / ``GATE_REPORT_FILENAME`` / the literal
    ``GateVerdict.REFUSED`` comparison), never on line numbers or
    whitespace, so reformatting the function cannot break it — only moving
    the write past the branch can.
    """
    body = inspect.getsource(genesis_jobs.gate_and_publish)
    json_write_index = body.index("GATE_JSON_FILENAME")
    report_write_index = body.index("GATE_REPORT_FILENAME")
    refused_branch_index = body.index("GateVerdict.REFUSED")

    assert json_write_index < refused_branch_index, (
        "gate.json must be written before the REFUSED branch is even tested"
    )
    assert report_write_index < refused_branch_index, (
        "gate.report.md must be written before the REFUSED branch is even tested"
    )


def test_a_gate_that_cannot_run_publishes_nothing(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """If the gate itself blows up, the artifact stays unregistered.

    The publish call is downstream of ``run_gate`` in one straight line, so a
    gate failure can never be stepped over. Asserted with a real generation and
    a spy on the dataset writer rather than by reading the code, so a future
    refactor that reorders the two is caught here as well as in the source pin.
    """
    calls: list[str] = []
    monkeypatch.setattr(
        datasets, "save_active_dataset", lambda *a, **k: calls.append("registered")
    )

    def exploding_gate(*args, **kwargs):
        raise RuntimeError("gate unavailable")

    monkeypatch.setattr(genesis_jobs, "run_gate", exploding_gate)

    record = _generate(crossroads_osm_file)
    assert record["status"] == genesis_jobs.STATUS_ERROR
    assert calls == [], "a generation was registered as a dataset without a gate verdict"
    assert datasets.get_active_dataset()["source"] == "default"


# ---------------------------------------------------------------------------
# §2 A geometry-only generation, end to end
# ---------------------------------------------------------------------------


def test_a_geometry_only_generation_publishes_as_provisional(
    workspace_root: Path, crossroads_osm_file: Path
):
    """The normal path, and the reason ``provisional`` exists.

    A generation job may not open COM, so V1 has no live evidence and reports
    NOT_CHECKABLE — which R1.5 says is never a pass. The artifact is good enough
    to use and the gate has not finished proving it, and the record says exactly
    that instead of showing a green tick.
    """
    record = _generate(crossroads_osm_file)

    assert record["status"] == genesis_jobs.STATUS_PROVISIONAL
    assert record["gate"]["verdict"] == GateVerdict.INCOMPLETE.value
    assert "V1" in record["gate"]["blocking"]
    assert record["gate"]["live_evidence"] is None
    assert record["gate"]["live_evidence_gap"] == "no_live_check_evidence_attached"
    assert record["phase"] == genesis_jobs.PHASE_DONE
    assert record["progress"] == 1.0

    dataset = datasets.get_active_dataset()
    assert dataset["source"] == "uploaded"
    assert dataset["filename"] == "cross.inpx"
    assert record["publication"]["replaced"]["source"] == "default"


def test_every_artifact_lands_in_the_jobs_own_work_dir(
    workspace_root: Path, crossroads_osm_file: Path
):
    """Five files, all under ``<workspace>/genesis/jobs/<id>``: the network, the
    §5 compliance report, the generator's census, the gate's machine result and
    the gate's ko report. Nothing genesis writes escapes the workspace tree."""
    record = _generate(crossroads_osm_file)
    work_dir = genesis_jobs.job_dir(record["id"])

    assert work_dir.is_relative_to(workspace_root)
    for filename in (
        "cross.inpx",
        "cross.compliance.md",
        "cross.genesis.json",
        genesis_jobs.GATE_JSON_FILENAME,
        genesis_jobs.GATE_REPORT_FILENAME,
    ):
        assert (work_dir / filename).is_file(), filename

    stored = json.loads((work_dir / genesis_jobs.GATE_JSON_FILENAME).read_text(encoding="utf-8"))
    assert [check["id"] for check in stored["checks"]] == [f"V{n}" for n in range(1, 10)]
    assert stored["verdict"] == "incomplete"


def test_the_stored_verdict_is_about_the_bytes_that_shipped(
    workspace_root: Path, crossroads_osm_file: Path
):
    """The gate hashes the artifact itself, and that digest is what the record
    carries — so "which file was this verdict about" is never a guess."""
    record = _generate(crossroads_osm_file)
    work_dir = genesis_jobs.job_dir(record["id"])
    stored = json.loads((work_dir / genesis_jobs.GATE_JSON_FILENAME).read_text(encoding="utf-8"))

    assert stored["network_sha256"] == record["artifact"]["sha256"]
    assert record["gate"]["network_sha256"] == record["artifact"]["sha256"]


def test_the_gate_payload_renders_every_check_in_korean(
    workspace_root: Path, crossroads_osm_file: Path
):
    """B3's rendering contract: nine checks, each with a state, a ko title, and
    its gaps/findings as sentences — the gate's own words, not new ones."""
    record = _generate(crossroads_osm_file)
    payload = genesis_jobs.gate_payload(record["id"], language="ko")

    assert payload["verdict"] == "incomplete"
    assert [check["id"] for check in payload["checks"]] == [f"V{n}" for n in range(1, 10)]
    v1 = next(check for check in payload["checks"] if check["id"] == "V1")
    assert v1["state"] == CheckState.NOT_CHECKABLE.value
    assert v1["blocks"] is True
    assert v1["gaps"] and v1["gaps"][0]["code"] == "v1_no_live_evidence"
    assert "확인 불가" in payload["report_markdown"]
    # en/ja go through the gate's own table, not a second dictionary here.
    assert "NOT CHECKABLE" in genesis_jobs.gate_payload(record["id"], language="en")["report_markdown"]


def test_an_unreadable_osm_is_an_error_not_a_refused_network(
    workspace_root: Path, tmp_path: Path
):
    """"There is no artifact" and "the artifact has a fault" are different
    answers. Collapsing them would leave a client unable to tell an unusable
    extract from a network with named defects."""
    broken = tmp_path / "broken.osm"
    broken.write_text("<osm><way></osm>", encoding="utf-8")
    record = _generate(broken)

    assert record["status"] == genesis_jobs.STATUS_ERROR
    assert record["status"] != genesis_jobs.STATUS_REFUSED
    assert record["refusal"]["code"] in {"osm_unreadable", "draft_refused"}
    assert record["refusal"]["reasons"]
    assert record["artifact"] is None
    assert datasets.get_active_dataset()["source"] == "default"


def test_exclude_service_reaches_the_parser(workspace_root: Path, tmp_path: Path):
    """The option is not decoration: with it set, a service way must be named as
    a skip rather than modelled."""
    from tests.test_genesis_osm_draft import _osm_text, LAT0, LON0

    osm = _osm_text(
        [(1, LAT0, LON0 - 0.003), (2, LAT0, LON0), (3, LAT0, LON0 + 0.003), (5, LAT0 + 0.002, LON0)],
        [
            (101, [1, 2, 3], {"highway": "secondary", "name": "Main St"}),
            (102, [2, 5], {"highway": "service", "name": "Back Lane"}),
        ],
    )
    path = tmp_path / "svc.osm"
    path.write_text(osm, encoding="utf-8", newline="")

    kept = _generate(path, name="kept")
    excluded = _generate(path, name="excluded", exclude_service=True)

    assert excluded["network"]["links"] < kept["network"]["links"]
    census = json.loads(
        (genesis_jobs.job_dir(excluded["id"]) / "excluded.genesis.json").read_text(encoding="utf-8")
    )
    assert any(skip.get("way_id") == 102 for skip in census["skipped_ways"])


# ---------------------------------------------------------------------------
# §3 A REFUSED verdict never registers a dataset
# ---------------------------------------------------------------------------


def test_a_refused_verdict_never_becomes_a_workspace_dataset(workspace_root: Path):
    """The rule the whole unit is for.

    The artifact has a pocket nothing can enter or leave — V3's refuse-tier
    fault, judged by the real gate — so it lands ``refused`` and the project's
    dataset slot is untouched.
    """
    record = _seed_job(_island_network())
    result = genesis_jobs.gate_and_publish(record["id"])

    assert result["status"] == genesis_jobs.STATUS_REFUSED
    assert result["gate"]["verdict"] == GateVerdict.REFUSED.value
    assert "V3" in result["gate"]["blocking"]
    assert result["publication"] is None
    assert datasets.get_active_dataset()["source"] == "default"


def _headless_group_network() -> str:
    """A genesis-shaped artifact with a REAL refuse-tier defect in its phasing.

    Signal group 2 is displayed by no head: green comes and goes and no vehicle
    ever sees it. Every top-level container here is one ``services.genesis.emit``
    writes, so nothing in the file is evidence that somebody else built it — the
    only thing that could prove WE did is the census.
    """
    return network(
        links=straight_pair(),
        nodes=[node(1, [(95.0, -10.0), (110.0, -10.0), (110.0, 10.0), (95.0, 10.0)])],
        extra=signal_layer(groups=(1, 2), heads=[(1, 1, 1, 100.0, 1, 1)]) + demand_layer(),
        support=gate_support + demand_support,
    )


def test_a_lost_census_does_not_let_a_defect_into_the_workspace(workspace_root: Path):
    """The publication guarantee, pinned at the seam that decides it.

    ``gate_and_publish`` reads the census by sibling filename and sets
    ``metadata=None`` when the file is missing or unparseable — a case its own
    CLI help calls normal. While provenance was ``bool(draft_metadata)``, that
    absence DEMOTED V5's refuse-tier finding to a warning, which turned REFUSED
    into INCOMPLETE, and this function registers everything that is not REFUSED.
    So the defective network became the workspace's live dataset because a JSON
    file was missing.

    The gate-level test (``test_genesis_gate.py``) pins the verdict; this one
    pins what the verdict is FOR. Asserting ``verdict is not PASS`` there would
    have left this hole wide open, because nothing in the pipeline asks that.
    """
    record = _seed_job(_headless_group_network(), name="lost")
    (genesis_jobs.job_dir(record["id"]) / "lost.genesis.json").unlink()

    result = genesis_jobs.gate_and_publish(record["id"])

    assert result["gate"]["provenance"]["state"] == Provenance.UNKNOWN.value
    assert result["gate"]["provenance"]["census"] == "absent"
    assert result["gate"]["verdict"] == GateVerdict.REFUSED.value
    # V5's headless signal group is the ONLY refuse-tier fault in the artifact,
    # so this refusal is about provenance and about nothing else.
    payload = genesis_jobs.gate_payload(record["id"])
    failed = [
        c["id"]
        for c in payload["checks"]
        if c["state"] == CheckState.FAIL.value and c["tier"] == "refuse"
    ]
    assert failed == ["V5"]
    assert "V5" in result["gate"]["blocking"]
    assert result["status"] == genesis_jobs.STATUS_REFUSED
    assert result["publication"] is None
    assert datasets.get_active_dataset()["source"] == "default"


def test_the_job_summary_says_which_tier_the_gate_applied(workspace_root: Path):
    """``counts`` means two different things depending on provenance, so the
    compact summary a job list reads carries the provenance decision itself —
    not only the full ``gate.json`` a dashboard is unlikely to fetch."""
    record = _seed_job(_headless_group_network(), name="tiered")
    result = genesis_jobs.gate_and_publish(record["id"])

    provenance = result["gate"]["provenance"]
    assert provenance["census"] == "declares_no_elements"
    assert provenance["state"] == Provenance.UNKNOWN.value
    assert provenance["foreign_containers"] == []
    payload = genesis_jobs.gate_payload(record["id"])
    v5 = next(check for check in payload["checks"] if check["id"] == "V5")
    assert v5["evidence"]["coverage_tier"] == "refuse"
    # The stored verdict round-trips its provenance: reading gate.json back must
    # not turn a proven decision into "unproven" in the rendered report.
    assert payload["provenance"] == provenance
    assert "provenance UNPROVEN" in genesis_jobs.gate_payload(
        record["id"], language="en"
    )["report_markdown"]

    # The same artifact WITH its census: proven ours, and it survives the trip
    # through gate.json rather than reading back as the model's default.
    artifact = _headless_group_network()
    proven = _seed_job(artifact, census=census_for(artifact), name="proven")
    genesis_jobs.gate_and_publish(proven["id"])
    stored = genesis_jobs.gate_payload(proven["id"], language="en")
    assert stored["provenance"]["state"] == Provenance.OURS.value
    assert stored["provenance"]["census"] == "matches_this_network"
    assert "built by this generator" in stored["report_markdown"]


def test_a_refusal_is_a_deliverable_not_a_deleted_file(workspace_root: Path):
    """A refused artifact stays downloadable with its verdict beside it: a
    client fixing a base map needs the network AND the reason, and a gate that
    threw the evidence away would be unusable as a report."""
    record = _seed_job(_island_network())
    genesis_jobs.gate_and_publish(record["id"])
    work_dir = genesis_jobs.job_dir(record["id"])

    assert (work_dir / "seeded.inpx").is_file()
    assert (work_dir / genesis_jobs.GATE_JSON_FILENAME).is_file()
    payload = genesis_jobs.gate_payload(record["id"])
    assert payload["verdict"] == "refused"
    v3 = next(check for check in payload["checks"] if check["id"] == "V3")
    assert v3["state"] == CheckState.FAIL.value
    assert v3["findings"], "a refusal with no named subject is not actionable"


def test_a_long_refusal_states_how_many_findings_it_omitted(workspace_root: Path):
    """Bounded payload, never a silent drop.

    ``docs/product/master-roadmap-v2`` §2.3.3: discarding is allowed, discarding
    WITHOUT LEAVING A NAME is not. The UI payload caps findings per check and
    says how many it left out; ``gate.json`` on disk always holds all of them.
    """
    islands = [
        link(1, [(0.0, 0.0), (100.0, 0.0)]),
        link(2, [(104.0, 0.0), (204.0, 0.0)]),
        connector(10001, 1, 2, [(100.0, 0.0), (104.0, 0.0)], from_pos=100.0),
    ]
    count = genesis_jobs.MAX_FINDINGS_IN_PAYLOAD + 5
    for index in range(count):
        offset = 400.0 + index * 40.0
        islands.append(link(100 + index, [(0.0, offset), (60.0, offset)], name=f"island {index}"))
    record = _seed_job(network(links=islands))
    genesis_jobs.gate_and_publish(record["id"])

    payload = genesis_jobs.gate_payload(record["id"])
    v3 = next(check for check in payload["checks"] if check["id"] == "V3")
    assert len(v3["findings"]) == genesis_jobs.MAX_FINDINGS_IN_PAYLOAD
    assert v3["findings_omitted"] > 0

    stored = json.loads(
        (genesis_jobs.job_dir(record["id"]) / genesis_jobs.GATE_JSON_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    stored_v3 = next(check for check in stored["checks"] if check["id"] == "V3")
    assert len(stored_v3["findings"]) == len(v3["findings"]) + v3["findings_omitted"]


# ---------------------------------------------------------------------------
# §4 V1's live evidence — admitted by gate.py, never manufactured here
# ---------------------------------------------------------------------------


def test_evidence_about_another_build_upgrades_nothing(
    workspace_root: Path, crossroads_osm_file: Path, tmp_path: Path
):
    """The sha-identity rule, reached through the app's own attach path.

    A ``state.json`` from a probe that loaded a DIFFERENT file is not weak
    evidence; V1 stays NOT_CHECKABLE with both digests named, and the job stays
    provisional.
    """
    record = _generate(crossroads_osm_file)
    other = write(tmp_path, network(links=[link(1, [(0.0, 0.0), (100.0, 0.0)])]), "other.inpx")

    updated = genesis_jobs.attach_live_check(
        record["id"], _probe_state(other, links=1, connectors=0, nodes=0)
    )

    assert updated["status"] == genesis_jobs.STATUS_PROVISIONAL
    assert updated["gate"]["live_evidence"]["admitted"] is False
    assert updated["gate"]["live_evidence"]["gap_codes"] == ["v1_oracle_names_another_network"]
    payload = genesis_jobs.gate_payload(record["id"])
    v1 = next(check for check in payload["checks"] if check["id"] == "V1")
    assert v1["state"] == CheckState.NOT_CHECKABLE.value


def test_matching_evidence_turns_v1_green_but_geometry_only_stays_provisional(
    workspace_root: Path, crossroads_osm_file: Path
):
    """The honest upgrade, and its honest ceiling.

    Matching evidence makes V1 a genuine PASS — a real Vissim opened this file
    and held the network afterwards. It does NOT make the verdict a pass, because
    a geometry-only draft declares no controllers and no vehicle inputs, so V5
    and V6 remain NOT_CHECKABLE. ``published`` is reachable only for a network
    that carries signals and demand, which is the truth and not a limitation of
    the wiring.
    """
    record = _generate(crossroads_osm_file)
    artifact = genesis_jobs.job_dir(record["id"]) / record["artifact"]["inpx"]
    counts = record["network"]

    updated = genesis_jobs.attach_live_check(
        record["id"],
        _probe_state(
            artifact,
            links=counts["links"],
            connectors=counts["connectors"],
            nodes=counts["nodes"],
        ),
    )

    assert updated["gate"]["live_evidence"]["admitted"] is True
    assert updated["gate"]["live_evidence"]["vissim_version"] == "PTV Vissim Test"
    payload = genesis_jobs.gate_payload(record["id"])
    v1 = next(check for check in payload["checks"] if check["id"] == "V1")
    assert v1["state"] == CheckState.PASS.value
    assert updated["status"] == genesis_jobs.STATUS_PROVISIONAL
    # V1 leaves the blocking list; V3/V5/V6 stay, and each for a stated reason —
    # no vehicle input to ask the reachability question about, no controller, no
    # demand. A geometry-only draft is exactly that, and the gate says so.
    assert "V1" not in updated["gate"]["blocking"]
    assert set(updated["gate"]["blocking"]) == {"V3", "V5", "V6"}


def test_evidence_that_the_load_failed_refuses_and_revokes_the_publication(
    workspace_root: Path, crossroads_osm_file: Path
):
    """A downgrade takes the same road as an upgrade.

    The probe recorded that Vissim refused to open the very file this job
    published. V1 becomes a FAIL, the verdict becomes REFUSED, and the network is
    removed from the project rather than left registered beside a job that now
    says it is refused.
    """
    record = _generate(crossroads_osm_file)
    assert datasets.get_active_dataset()["source"] == "uploaded"
    artifact = genesis_jobs.job_dir(record["id"]) / record["artifact"]["inpx"]
    counts = record["network"]

    updated = genesis_jobs.attach_live_check(
        record["id"],
        _probe_state(
            artifact,
            links=counts["links"],
            connectors=counts["connectors"],
            nodes=counts["nodes"],
            load_ok=False,
            load_error="The network file could not be loaded.",
        ),
    )

    assert updated["status"] == genesis_jobs.STATUS_REFUSED
    assert updated["publication"] is None
    assert updated["revocation"]["removed_from_workspace"] is True
    assert datasets.get_active_dataset()["source"] == "default"


def test_a_revocation_never_deletes_somebody_elses_dataset(
    workspace_root: Path, crossroads_osm_file: Path
):
    """Bookkeeping must not destroy data.

    If the project's active dataset has moved on since this job published, the
    revocation is RECORDED and nothing on disk is touched — but it is still
    recorded, because a revocation nobody can see is the silent drop this
    project forbids.
    """
    record = _generate(crossroads_osm_file)
    artifact = genesis_jobs.job_dir(record["id"]) / record["artifact"]["inpx"]
    counts = record["network"]

    # Somebody uploads their own network afterwards.
    datasets.save_active_dataset(
        "hand_uploaded.inpx", network(links=[link(1, [(0.0, 0.0), (50.0, 0.0)])]).encode("utf-8")
    )

    updated = genesis_jobs.attach_live_check(
        record["id"],
        _probe_state(
            artifact,
            links=counts["links"],
            connectors=counts["connectors"],
            nodes=counts["nodes"],
            load_ok=False,
            load_error="The network file could not be loaded.",
        ),
    )

    assert updated["status"] == genesis_jobs.STATUS_REFUSED
    assert updated["revocation"]["removed_from_workspace"] is False
    assert datasets.get_active_dataset()["filename"] == "hand_uploaded.inpx"


def test_live_evidence_is_only_ever_built_by_the_probe_reader():
    """``LoadOracle`` is never constructed field by field in this module.

    That is what makes "we cannot fake V1" checkable: the only way an oracle
    exists here is ``LoadOracle.from_probe_state``, which is the function that
    computes the sha256 of the file the probe actually loaded.
    """
    assert "LoadOracle.from_probe_state(" in GENESIS_JOBS_SOURCE
    assert not re.search(r"LoadOracle\(", GENESIS_JOBS_SOURCE)


def test_attaching_evidence_to_a_job_with_no_artifact_is_refused(
    workspace_root: Path, tmp_path: Path
):
    broken = tmp_path / "broken.osm"
    broken.write_text("<osm><way></osm>", encoding="utf-8")
    record = _generate(broken)
    with pytest.raises(genesis_jobs.GenesisJobError):
        genesis_jobs.attach_live_check(record["id"], {"runs": {}})


# ---------------------------------------------------------------------------
# §5 The HTTP surface: auth, workspace, and the TestClient end-to-end
# ---------------------------------------------------------------------------


def test_anonymous_callers_cannot_start_a_generation(client: TestClient):
    """Every MUTATION needs a signed-in user: a generation replaces the
    project's active dataset, and the audit document has to be able to name who
    built the network."""
    assert client.post("/api/genesis/uploads?filename=a.osm", content=b"<osm/>").status_code == 401
    assert client.post("/api/genesis/jobs", json={"upload_token": "x" * 32}).status_code == 401
    assert (
        client.post("/api/genesis/jobs/abcdef123456/live-check", json={"state": {}}).status_code
        == 401
    )


def test_reads_stay_open_like_the_rest_of_the_app(client: TestClient):
    """Parity with ``/api/simulations`` and the audit exports: reading the record
    of a decision is not making one."""
    listing = client.get("/api/genesis/jobs")
    assert listing.status_code == 200
    assert listing.json() == {"jobs": []}


def test_a_non_member_is_403_on_another_projects_generations(client: TestClient):
    """The workspace boundary is enforced by the middleware, so it covers these
    routes without any of them asking for it."""
    owner = admin_headers()
    created = client.post("/api/workspaces", json={"name": "다른 프로젝트"}, headers=owner)
    assert created.status_code == 200, created.text
    workspace_id = created.json()["id"]

    outsider = bearer_headers("genesis-outsider", "modeler")
    headers = {"X-Workspace-Id": workspace_id, **outsider}
    assert client.get("/api/genesis/jobs", headers=headers).status_code == 403
    assert (
        client.post("/api/genesis/jobs", json={"upload_token": "x" * 32}, headers=headers).status_code
        == 403
    )
    # Anonymous is refused for the same reason, before auth is even considered.
    assert client.get("/api/genesis/jobs", headers={"X-Workspace-Id": workspace_id}).status_code == 403


def test_end_to_end_upload_generate_gate_publish(client: TestClient, crossroads_osm_file: Path):
    """The B1 acceptance sentence, over HTTP: one generation started by a signed-in
    caller lands in the workspace as a loadable ``.inpx`` — via the gate, and
    labelled ``provisional`` because V1 has no live evidence yet."""
    headers = admin_headers()

    staged = client.post(
        "/api/genesis/uploads",
        content=crossroads_osm_file.read_bytes(),
        headers={"X-Filename": "cross.osm", **headers},
    )
    assert staged.status_code == 200, staged.text
    token = staged.json()["token"]

    started = client.post(
        "/api/genesis/jobs", json={"upload_token": token, "name": "e2e"}, headers=headers
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["id"]
    assert started.json()["submitted_by"] == "suite-admin"

    record = _await_terminal(client, job_id)
    assert record["status"] == genesis_jobs.STATUS_PROVISIONAL, record
    assert record["gate"]["verdict"] == "incomplete"

    gate = client.get(f"/api/genesis/jobs/{job_id}/gate")
    assert gate.status_code == 200, gate.text
    body = gate.json()
    assert body["verdict"] == "incomplete"
    assert len(body["checks"]) == 9
    assert body["status"] == genesis_jobs.STATUS_PROVISIONAL

    # The artifact download is AUTHENTICATED, unlike this router's other reads:
    # it returns the network itself, and the app's pre-existing
    # /api/datasets/upload is un-gated, so anonymous access here would let a
    # REFUSED network be laundered into the active dataset.
    assert client.get(f"/api/genesis/jobs/{job_id}/artifact/inpx").status_code == 401
    artifact = client.get(f"/api/genesis/jobs/{job_id}/artifact/inpx", headers=headers)
    assert artifact.status_code == 200
    assert artifact.content.startswith(b"<?xml")
    assert b"<links>" in artifact.content

    active = client.get("/api/datasets/active")
    assert active.json()["source"] == "uploaded"
    assert active.json()["filename"] == "e2e.inpx"


def test_attaching_live_evidence_over_http_regates(client: TestClient, crossroads_osm_file: Path):
    headers = admin_headers()
    record = _generate(crossroads_osm_file)
    artifact = genesis_jobs.job_dir(record["id"]) / record["artifact"]["inpx"]
    counts = record["network"]

    response = client.post(
        f"/api/genesis/jobs/{record['id']}/live-check",
        json={
            "state": _probe_state(
                artifact,
                links=counts["links"],
                connectors=counts["connectors"],
                nodes=counts["nodes"],
            )
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["gate"]["live_evidence"]["admitted"] is True


def test_a_job_that_has_not_been_gated_yet_is_409_not_404(client: TestClient, workspace_root: Path):
    """"This job does not exist" and "this job has no verdict YET" are different
    answers, and a UI that polls has to be able to tell them apart."""
    record = _seed_job(_island_network())
    response = client.get(f"/api/genesis/jobs/{record['id']}/gate")
    assert response.status_code == 409, response.text
    assert genesis_jobs.STATUS_RUNNING in response.json()["detail"]


def test_a_publish_that_the_disk_refuses_keeps_the_verdict_and_names_the_cause(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """On Windows a held handle on ``uploads/current`` is normal operation, not
    an exotic error (services/datasets.py). The job must not be left stuck at
    ``publishing`` with its verdict thrown away — the gate result survives and
    the storage cause is named."""

    def refuse(*args, **kwargs):
        raise DatasetStorageError("something still has the model open")

    monkeypatch.setattr(datasets, "save_active_dataset", refuse)
    record = _generate(crossroads_osm_file)

    assert record["status"] == genesis_jobs.STATUS_ERROR
    assert record["phase"] == genesis_jobs.PHASE_DONE
    assert record["refusal"]["code"] == "publish_failed"
    assert record["gate"]["verdict"] == "incomplete", "the gate verdict must survive a publish failure"
    assert record["publication"] is None


def test_an_unknown_job_and_an_unknown_artifact_kind_are_both_404(client: TestClient):
    headers = bearer_headers("genesis-reader", "modeler")
    assert client.get("/api/genesis/jobs/deadbeef0000").status_code == 404
    # Authenticated: the artifact route is guarded, so an anonymous call answers
    # 401 and would never reach the 404 this test is about.
    assert (
        client.get("/api/genesis/jobs/deadbeef0000/artifact/inpx", headers=headers).status_code
        == 404
    )
    assert (
        client.get("/api/genesis/jobs/deadbeef0000/artifact/passwd", headers=headers).status_code
        == 404
    )
    # And the guard itself, so "unknown job" can never be the reason it passes.
    assert client.get("/api/genesis/jobs/deadbeef0000/artifact/inpx").status_code == 401


def test_the_source_osm_artifact_downloads_the_frozen_upload(
    client: TestClient, crossroads_osm_file: Path
):
    """B5/G-fix: ``artifact_path`` used to resolve this kind through
    ``source.get("path")`` — a key ``create_genesis_job`` never writes (it only
    ever writes ``filename``/``bytes``/``sha256``/``stored_as``, by the module's
    own design note: the record crosses the HTTP boundary and must not carry a
    server path). Every call therefore hit an empty ``Path("")``, was never a
    file, and ``download_artifact`` 404'd unconditionally — despite
    ``source_osm`` being a real entry in ``_ARTIFACT_MEDIA_TYPES`` and the
    frontend's ``GenesisArtifactKind`` union. It must resolve the same way the
    runner itself does: ``job_dir(job_id) / stored_as``.
    """
    headers = admin_headers()
    staged = client.post(
        "/api/genesis/uploads",
        content=crossroads_osm_file.read_bytes(),
        headers={"X-Filename": "cross.osm", **headers},
    )
    assert staged.status_code == 200, staged.text
    token = staged.json()["token"]

    started = client.post(
        "/api/genesis/jobs", json={"upload_token": token, "name": "srcdl"}, headers=headers
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["id"]
    _await_terminal(client, job_id)

    # Authenticated like every other artifact kind — same reasoning as inpx.
    assert client.get(f"/api/genesis/jobs/{job_id}/artifact/source_osm").status_code == 401

    response = client.get(f"/api/genesis/jobs/{job_id}/artifact/source_osm", headers=headers)
    assert response.status_code == 200, response.text
    assert response.content == crossroads_osm_file.read_bytes()
    # The honest download name is the original upload's filename, not the
    # fixed internal name (``source.osm``) the file is frozen under on disk —
    # that name means nothing to the caller who uploaded ``cross.osm``.
    assert 'filename="cross.osm"' in response.headers["content-disposition"]


def test_a_bad_upload_is_refused_before_a_token_exists(client: TestClient):
    headers = admin_headers()
    for filename, body in (
        ("network.zip", b"PK\x03\x04"),
        ("empty.osm", b"   "),
        ("not-osm.osm", b"<network><links/></network>"),
    ):
        response = client.post(
            "/api/genesis/uploads", content=body, headers={"X-Filename": filename, **headers}
        )
        assert response.status_code == 400, (filename, response.text)
        assert response.json()["detail"]["code"].startswith("genesis_upload_")


def test_a_name_that_could_escape_the_job_dir_is_refused(client: TestClient, crossroads_osm_file: Path):
    headers = admin_headers()
    staged = client.post(
        "/api/genesis/uploads",
        content=crossroads_osm_file.read_bytes(),
        headers={"X-Filename": "cross.osm", **headers},
    )
    response = client.post(
        "/api/genesis/jobs",
        json={"upload_token": staged.json()["token"], "name": "../../escape"},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "genesis_bad_name"


def _await_terminal(client: TestClient, job_id: str, timeout_s: float = 30.0) -> dict:
    """Poll one job to a terminal state.

    The runner is a daemon thread (the house pattern ``routers/simulations.py``
    uses), so the HTTP end-to-end has to poll exactly as a browser does. Every
    other test in this file drives ``run_genesis_job`` synchronously instead;
    this one is deliberately the real asynchronous path.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        response = client.get(f"/api/genesis/jobs/{job_id}")
        assert response.status_code == 200, response.text
        record = response.json()
        if record["status"] in genesis_jobs.TERMINAL_STATUSES:
            return record
        time.sleep(0.05)
    raise AssertionError(f"genesis job {job_id} did not finish within {timeout_s}s")


# ---------------------------------------------------------------------------
# §6 Workspace isolation and registry discipline
# ---------------------------------------------------------------------------


def test_a_job_in_one_project_does_not_exist_in_another(
    workspace_root: Path, crossroads_osm_file: Path
):
    """Physically isolated, not filtered: the registry itself lives inside the
    workspace, so another project cannot even read the id."""
    first = _generate(crossroads_osm_file)
    other = workspaces.create_workspace("두 번째 프로젝트")

    with workspaces.bind_workspace(other["id"]):
        assert genesis_jobs.list_jobs() == []
        with pytest.raises(genesis_jobs.GenesisJobNotFoundError):
            genesis_jobs.get_job(first["id"])
        # And the other project's dataset slot was never touched.
        assert datasets.get_active_dataset()["source"] == "default"


def test_the_job_runner_binds_the_submitting_workspace(
    workspace_root: Path, crossroads_osm_file: Path
):
    """A ``threading.Thread`` does not inherit the request's ContextVars, so the
    runner re-binds explicitly — without it, ``current_workspace_id()`` raises
    (by design) and the job could never write anything."""
    assert "with workspaces.bind_workspace(workspace_id):" in GENESIS_JOBS_SOURCE
    record = genesis_jobs.create_genesis_job(osm_path=crossroads_osm_file, name="bound")
    other = workspaces.create_workspace("세 번째 프로젝트")

    # Run it while a DIFFERENT workspace is bound: the record must still land in
    # the workspace that submitted it.
    with workspaces.bind_workspace(other["id"]):
        finished = genesis_jobs.run_genesis_job(record["id"], record["workspace_id"])
    assert finished["workspace_id"] == record["workspace_id"]
    assert genesis_jobs.get_job(record["id"])["status"] == genesis_jobs.STATUS_PROVISIONAL


def test_a_malformed_job_id_can_never_address_a_directory(workspace_root: Path):
    for bad in ("../evil", "a/b", "..", "", "NOTHEX!!"):
        with pytest.raises(genesis_jobs.GenesisJobNotFoundError):
            genesis_jobs.job_dir(bad)


def test_an_unreadable_registry_refuses_to_be_overwritten(
    workspace_root: Path, crossroads_osm_file: Path
):
    """``services/simulation_runs.py``'s U6 lesson, applied: a registry we could
    not read is never written over — that is how a transient I/O problem becomes
    permanent data loss."""
    _generate(crossroads_osm_file)
    corrupt = b"{not json at all"
    genesis_jobs.registry_path().write_bytes(corrupt)

    with pytest.raises(genesis_jobs.GenesisRegistryError):
        genesis_jobs.start_genesis_job(osm_path=crossroads_osm_file, name="second")
    assert genesis_jobs.registry_path().read_bytes() == corrupt
    # A LISTING degrades instead of 500-ing, and says nothing rather than lying.
    assert genesis_jobs.list_jobs() == []


def test_staging_rejects_a_traversal_token(workspace_root: Path):
    for bad in ("../../etc", "a/b", "NOTHEX"):
        with pytest.raises(genesis_jobs.StagedOsmNotFoundError):
            genesis_jobs.staged_osm_path(bad)


# ---------------------------------------------------------------------------
# What attached evidence actually proves — and what it does not
# ---------------------------------------------------------------------------


def test_attached_evidence_is_recorded_as_an_attestation_not_as_proof(
    workspace_root: Path, crossroads_osm_file: Path
):
    """V1 can go green on a human's claim, and that must never read like proof.

    An adversarial pass showed the hole this pins: the ``state.json`` arrives
    over HTTP, and a caller who knows the artifact — its own sha is in the job
    record — can hand-write ``load_ok: true`` about it. The sha256 rule
    establishes the evidence is ABOUT this file; nothing can establish that
    Vissim ran. The module docstring used to claim "nothing here can fake it",
    which was false, and a false safety claim is a defect in its own right.

    So the resolution is honesty rather than a stronger lock: the evidence is
    stamped ``operator_attested`` with WHO said it and WHEN, and the verdict
    carries ``rests_on_attestation`` so no reader — API, report or UI — has to
    infer which kind of V1 they are looking at.
    """
    record = _generate(crossroads_osm_file)
    artifact = genesis_jobs.job_dir(record["id"]) / record["artifact"]["inpx"]
    counts = record["network"]

    updated = genesis_jobs.attach_live_check(
        record["id"],
        _probe_state(
            artifact,
            links=counts["links"],
            connectors=counts["connectors"],
            nodes=counts["nodes"],
        ),
        attested_by="yunha",
    )

    evidence = updated["gate"]["live_evidence"]
    assert evidence["admitted"] is True
    assert evidence["basis"] == genesis_jobs.ATTESTATION_BASIS == "operator_attested"
    assert evidence["attested_by"] == "yunha"
    assert evidence["attested_at"], "an attestation without a time is not accountable"
    # V1 IS green — the claim is admitted — and the verdict says what it rests on.
    assert updated["gate"]["rests_on_attestation"] is True

    # A fresh generation, with nobody attesting anything, must not claim it.
    fresh = _generate(crossroads_osm_file)
    assert fresh["gate"]["rests_on_attestation"] is False
    assert fresh["gate"]["live_evidence"] is None


def test_the_job_record_never_carries_an_absolute_server_path(
    workspace_root: Path, crossroads_osm_file: Path
):
    """The record crosses the HTTP boundary; the server's filesystem must not.

    ``GET /api/genesis/jobs/{id}`` returns the record verbatim, so an absolute
    path there publishes the data root, the workspace id and the work-dir naming
    scheme — to anonymous callers, on the default workspace. Everything the
    runner needs is re-derivable from the job id via :func:`job_dir` /
    :func:`artifact_path`, so nothing has to carry a path to find its own files.
    """
    record = _generate(crossroads_osm_file)
    blob = json.dumps(record)

    assert "path" not in record["source_osm"]
    assert record["source_osm"]["stored_as"] == "source.osm"
    # No absolute path anywhere in the serialised record, on either separator.
    assert str(workspace_root) not in blob
    assert "\\\\" not in blob.replace("\\n", ""), "a Windows path leaked into the record"
    assert not re.search(r"[A-Za-z]:[\\/]", blob), "a drive-letter path leaked into the record"


def test_artifact_path_resolves_source_osm_via_stored_as(
    workspace_root: Path, crossroads_osm_file: Path
):
    """Direct unit coverage of ``artifact_path("source_osm")`` itself, apart
    from the HTTP layer: the record carries no ``path`` key (only ``stored_as``,
    per the test just above), so this kind must resolve exactly like
    ``_run_genesis_job_locked`` resolves the same file — ``job_dir(job_id) /
    stored_as`` — not through a key the record never had.
    """
    record = _generate(crossroads_osm_file)

    resolved = genesis_jobs.artifact_path(record["id"], "source_osm")
    assert resolved == genesis_jobs.job_dir(record["id"]) / "source.osm"
    assert resolved.read_bytes() == crossroads_osm_file.read_bytes()


def test_artifact_path_refuses_a_source_osm_with_no_stored_as(workspace_root: Path):
    """The not-found error survives the fix for a record that predates
    ``stored_as`` or otherwise never got one — mirroring the ``else`` branch's
    own "no {kind} artifact" guard for a missing named artifact.
    """
    job_id = uuid4().hex[:12]
    genesis_jobs.job_dir(job_id).mkdir(parents=True, exist_ok=True)
    genesis_jobs._upsert(
        {
            "id": job_id,
            "workspace_id": workspaces.current_workspace_id(),
            "name": "no-stored-as",
            "status": genesis_jobs.STATUS_RUNNING,
            "phase": genesis_jobs.PHASE_GATING,
            "progress": 0.5,
            "created_at": "2026-08-11T00:00:00+00:00",
            "started_at": "2026-08-11T00:00:00+00:00",
            "completed_at": None,
            "submitted_by": "tester",
            "options": {"exclude_service": False},
            "source_osm": {"filename": "seed.osm", "bytes": 0, "sha256": ""},
            "network": None,
            "artifact": None,
            "gate": None,
            "publication": None,
            "refusal": None,
            "error": None,
        }
    )
    with pytest.raises(genesis_jobs.GenesisJobNotFoundError):
        genesis_jobs.artifact_path(job_id, "source_osm")


# ---------------------------------------------------------------------------
# ``requested_region`` — what the 생성 이력 list needs to tell two generations
# of the same city apart (U-1-quick, docs/ux-backlog.md item #5)
# ---------------------------------------------------------------------------
#
# Three answers, never two, and the distinction is carried by the JSON itself:
# an OBJECT (this .osm was fetched for a region), ``null`` (the file was read
# and carries no fetch provenance — an upload), and the key being ABSENT (a
# record written before the field existed). The client words all three and
# invents a region for none of them; these tests are what stop the second and
# third from collapsing into each other, which is the only way "—" could start
# reading as "uploaded".

#: A rectangle comfortably containing every node of ``crossroads_osm`` — the
#: trim is real (``parse_osm_file`` is handed this box), so a window that clipped
#: the fixture would fail these tests for a reason that has nothing to do with
#: what they assert.
_FETCH_BBOX = {
    "south": -32.0800,
    "west": 115.8260,
    "north": -32.0600,
    "east": 115.8460,
}

#: Five vertices, deliberately not four: a count that matched the number of
#: sides of the bounding box could pass while reading the wrong list.
_FETCH_POLYGON = [
    [_FETCH_BBOX["south"], _FETCH_BBOX["west"]],
    [_FETCH_BBOX["south"], _FETCH_BBOX["east"]],
    [_FETCH_BBOX["north"], _FETCH_BBOX["east"]],
    [_FETCH_BBOX["north"], 115.8360],
    [_FETCH_BBOX["north"], _FETCH_BBOX["west"]],
]


def _fetched_osm(tmp_path: Path, *, polygon: list | None = None) -> Path:
    """``crossroads_osm`` carrying a region fetch's own provenance comment.

    Written with ``osm_fetch.annotate`` — the exact function a real fetch stages
    its bytes through — rather than a hand-typed comment, so these tests read the
    same document shape production does and cannot pass against a format the
    fetcher no longer writes.
    """
    provenance: dict = {"source": "overpass", "requested_region": dict(_FETCH_BBOX)}
    if polygon is not None:
        provenance["requested_polygon"] = {"vertices": polygon}
    path = tmp_path / f"fetched-{uuid4().hex[:6]}.osm"
    path.write_bytes(
        osm_fetch.annotate(crossroads_osm().encode("utf-8"), provenance)
    )
    return path


def test_a_generation_records_the_region_its_extract_was_fetched_for(
    workspace_root: Path, tmp_path: Path
):
    """A rectangle fetch: the box on the record, in the provenance's own words."""
    record = _generate(_fetched_osm(tmp_path))

    assert record["requested_region"] == {
        "bbox": _FETCH_BBOX,
        "polygon_vertices": None,
    }


def test_a_polygon_fetch_records_the_vertex_count_beside_its_bounding_box(
    workspace_root: Path, tmp_path: Path
):
    """A lasso: the COUNT of vertices, and the box the build was judged against.

    Both, not one — the box is what the trim and the boundary ladder actually
    used (``osm_fetch`` writes a ring's own bounding box as ``requested_region``
    for exactly that reason), and the count is the only thing that tells a
    reader the shape they drew was not a rectangle. The ring itself stays in the
    artifact's metadata; a history row does not redraw it.
    """
    record = _generate(_fetched_osm(tmp_path, polygon=_FETCH_POLYGON))

    assert record["requested_region"]["bbox"] == _FETCH_BBOX
    assert record["requested_region"]["polygon_vertices"] == len(_FETCH_POLYGON)


def test_an_upload_records_a_null_region_and_a_legacy_record_records_none_at_all(
    client: TestClient, crossroads_osm_file: Path
):
    """The two absences that must not read alike.

    An UPLOAD gets ``requested_region: null`` — the runner opened the frozen
    ``.osm`` and it said nothing about a region, which is a measurement. A
    record written before this field existed has no such key, and the client
    renders that as "—". Collapsing them would let a legacy generation claim it
    was uploaded, which nothing here knows.
    """
    uploaded = _generate(crossroads_osm_file)
    assert "requested_region" in uploaded
    assert uploaded["requested_region"] is None

    legacy = _seed_job(_island_network(), name="legacy")
    assert "requested_region" not in legacy

    # And both survive the HTTP listing the 생성 이력 page reads, still apart.
    listed = client.get("/api/genesis/jobs")
    assert listed.status_code == 200, listed.text
    by_id = {job["id"]: job for job in listed.json()["jobs"]}
    assert by_id[uploaded["id"]]["requested_region"] is None
    assert "requested_region" not in by_id[legacy["id"]]


def test_a_crash_while_re_gating_leaves_a_coherent_terminal_record(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """The re-gate path needs the worker thread's guarantee, and had none.

    ``_run_genesis_job_locked`` wraps its whole body so a crash always reaches a
    terminal, recorded state. ``attach_live_check`` did not: a throw inside
    ``run_gate`` left ``phase=gating`` beside a stale terminal status — a record
    simultaneously claiming to be mid-run and finished, which no other path
    produces and nothing ever cleans up — and surfaced as a raw 500 because the
    router catches only the three named genesis errors.
    """
    record = _generate(crossroads_osm_file)
    before = genesis_jobs.get_job(record["id"])["status"]

    def boom(*args, **kwargs):
        raise RuntimeError("simulated crash inside run_gate")

    monkeypatch.setattr(genesis_jobs, "run_gate", boom)

    with pytest.raises(genesis_jobs.GenesisJobError, match="keeps its previous verdict"):
        genesis_jobs.attach_live_check(record["id"], {"runs": {}}, attested_by="yunha")

    after = genesis_jobs.get_job(record["id"])
    assert after["phase"] == genesis_jobs.PHASE_DONE, "phase must not be left mid-run"
    assert after["status"] == before, "a failed re-gate must not change the verdict"
    assert after["regate_error"]["code"] == "regate_failed"
    assert after["regate_error"]["attested_by"] == "yunha"


def test_jobs_a_dead_process_left_running_are_terminated_at_startup(
    workspace_root: Path, crossroads_osm_file: Path
):
    """A generation runs in a thread inside THIS process, so a restart orphans it.

    Nothing else will ever advance a ``queued``/``running`` record: the thread
    that owned it died with the old process, and the UI polls it forever. They
    become ``error`` — never a gated status — because no verdict was reached, so
    there is no artifact anyone should trust.
    """
    record = _generate(crossroads_osm_file)
    genesis_jobs._update(
        record["id"], status=genesis_jobs.STATUS_RUNNING, phase=genesis_jobs.PHASE_DRAFTING
    )

    reconciled = genesis_jobs.reconcile_interrupted_jobs()

    assert any(record["id"] in entry for entry in reconciled), reconciled
    after = genesis_jobs.get_job(record["id"])
    assert after["status"] == genesis_jobs.STATUS_ERROR
    assert after["phase"] == genesis_jobs.PHASE_DONE
    assert after["error"]["code"] == "interrupted_by_restart"

    # Idempotent: a second boot must not re-sweep an already-terminal job.
    assert genesis_jobs.reconcile_interrupted_jobs() == []

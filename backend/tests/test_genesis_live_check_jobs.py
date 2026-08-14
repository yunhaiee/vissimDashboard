"""Unit #33 — the LIVE-CHECK JOB: V1's evidence produced by the app, on the queue.

Until this unit, closing V1 in the product meant an operator running
``backend/tools/genesis_open_live_check.py`` at a console and posting its
``state.json`` back — so every published-with-V1 verdict rested on an
attestation (``basis: "operator_attested"``). This job does the same probe from
a button: clone the parent's ``.inpx`` into a NEW job's work dir, open THAT in a
real Vissim on the job queue's worker thread, write the record in exactly the
shape ``LoadOracle.from_probe_state`` reads, and re-gate the parent through the
UNMODIFIED gate entry point.

**What every family below is really guarding.** This feature can change a
published verdict, so its failure modes are all one shape — a verdict that
changed for a reason nobody can reconstruct:

* §1 **Eligibility is a named refusal**, never a silently disabled control, and
  a refusal creates nothing on disk.
* §2 **The evidence file is the CLI's own shape**, proven by round-tripping it
  through ``LoadOracle.from_probe_state`` rather than by eyeballing keys.
* §3 **The judge is untouched.** A matching, loaded probe flips V1 to PASS; a
  probe about other bytes does NOT, and says why; ``load_ok: false`` refuses and
  revokes. Every one of those verdicts is ``gate.py``'s, read back off the
  parent.
* §4 **The previous verdict survives.** A re-gate archives the exact prior
  ``gate.json`` bytes and records what caused the replacement.
* §5 **The parent's own directory is never written to**, and a probe that never
  answered never re-gates anything.

No live Vissim: ``genesis_jobs._build_live_check_probe`` — the ONE line that
opens COM — is monkeypatched to return a canned probe record, exactly the seam
discipline ``tests/test_genesis_fix_jobs.py`` uses for ``_build_fix_edit_session``.
Everything else in the runner (the clone, the state file, the sha256
admissibility rule, the REAL R1.5 gate, publication and revocation, the archive)
runs for real.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from conftest import admin_headers
from services import datasets, genesis_jobs, workspaces
from services.genesis.gate import CheckState, LoadOracle

from tests.test_genesis_jobs import _generate, _island_network, _seed_job
from tests.test_genesis_osm_draft import crossroads_osm

GENESIS_JOBS_SOURCE = Path(genesis_jobs.__file__).read_text(encoding="utf-8")
ROUTER_SOURCE = (Path(genesis_jobs.__file__).parent.parent / "routers" / "genesis.py").read_text(
    encoding="utf-8"
)


# ---------------------------------------------------------------------------
# Fixtures — duplicated, not imported (this repo cross-imports plain helper
# FUNCTIONS between genesis test modules, never fixtures)
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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


# ---------------------------------------------------------------------------
# The fake probe — COM's only stand-in in this file
# ---------------------------------------------------------------------------


def _fake_probe(*, links: int, connectors: int, nodes: int, **overrides: Any):
    """A probe callable in ``services.vissim_open_probe.probe_open``'s shape.

    The census is the REAL artifact's, passed in by the caller, because V1
    grades the loaded model's counts against the file's own — a hard-coded
    census would make these tests pass for a reason the product does not rely
    on (``test_genesis_jobs._probe_state`` carries the same note).
    """

    def _probe(net_path: Path, label: str, **_kwargs: Any) -> dict[str, Any]:
        record: dict[str, Any] = {
            "label": label,
            "network": str(net_path),
            "started_utc": "2026-08-14T09:00:00+00:00",
            "finished_utc": "2026-08-14T09:00:20+00:00",
            "connected": True,
            "prog_id": "VISSIM.Vissim.Fake",
            "load_ok": True,
            "load_error": None,
            "save_ok": True,
            "save_error": None,
            "err_files_before_load": [],
            "err_after_load": {"files": [], "new_files": []},
            "com_topology": {
                "link_collection_count": links + connectors,
                "is_conn_counts": {"plain_links": links, "connectors": connectors},
                "nodes_count": nodes,
            },
            "version": {"att_values": {"Version": "PTV Vissim Fake 2026"}},
        }
        record.update(overrides)
        return record

    return _probe


def _install_probe(monkeypatch: pytest.MonkeyPatch, probe) -> None:
    monkeypatch.setattr(genesis_jobs, "_build_live_check_probe", lambda: probe)


def _live_check(parent_id: str, monkeypatch: pytest.MonkeyPatch, probe) -> dict[str, Any]:
    """Create + run ONE live-check job synchronously.

    ``create_live_check_job`` + ``run_live_check_job`` is exactly what
    ``start_live_check_job`` does minus the thread — the same split every other
    job family in this suite uses so a test asserts on the run, never a sleep.
    """
    _install_probe(monkeypatch, probe)
    record = genesis_jobs.create_live_check_job(parent_id, requested_by="yunha")
    return genesis_jobs.run_live_check_job(record["id"], record["workspace_id"])


def _probe_for(record: dict[str, Any], **overrides: Any):
    counts = record["network"]
    return _fake_probe(
        links=counts["links"],
        connectors=counts["connectors"],
        nodes=counts["nodes"],
        **overrides,
    )


def _v1_state(job_id: str) -> str:
    payload = genesis_jobs.gate_payload(job_id)
    return next(check for check in payload["checks"] if check["id"] == "V1")["state"]


def _tree_digest(root: Path) -> dict[str, str]:
    """Every file under ``root``, by relative path → sha256 of its bytes."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# ---------------------------------------------------------------------------
# §1 Eligibility is NAMED — a refusal is information, never a greyed control
# ---------------------------------------------------------------------------


def test_a_job_with_no_gated_artifact_is_refused_by_name(workspace_root: Path):
    """A generation that has not reached a verdict has no file to open, and the
    refusal says exactly that instead of the button being absent."""
    running = _seed_job(_island_network(), name="still_running")

    with pytest.raises(genesis_jobs.GenesisRefused) as caught:
        genesis_jobs.create_live_check_job(running["id"], requested_by="yunha")

    assert caught.value.code == "genesis_live_check_parent_not_gated"
    assert genesis_jobs.STATUS_RUNNING in caught.value.reasons[0]


def test_a_refusal_creates_no_job_and_no_directory(workspace_root: Path):
    """Nothing on disk, nothing in the registry: the id is minted only after
    eligibility passes."""
    running = _seed_job(_island_network(), name="still_running")
    before = {job["id"] for job in genesis_jobs.list_jobs()}

    with pytest.raises(genesis_jobs.GenesisRefused):
        genesis_jobs.create_live_check_job(running["id"], requested_by="yunha")

    assert {job["id"] for job in genesis_jobs.list_jobs()} == before
    assert sorted(p.name for p in genesis_jobs.jobs_root().iterdir()) == [running["id"]]


def test_a_gated_job_whose_artifact_is_gone_is_refused_by_a_different_name(
    workspace_root: Path, crossroads_osm_file: Path
):
    """"Not gated yet" and "gated, but the file is missing" are different
    answers and get different codes — a reader must be able to tell an
    unfinished generation from a broken work directory."""
    parent = _generate(crossroads_osm_file)
    (genesis_jobs.job_dir(parent["id"]) / parent["artifact"]["inpx"]).unlink()

    with pytest.raises(genesis_jobs.GenesisRefused) as caught:
        genesis_jobs.create_live_check_job(parent["id"], requested_by="yunha")

    assert caught.value.code == "genesis_live_check_artifact_missing"


def test_a_live_check_job_cannot_itself_be_live_checked(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """It holds a clone but has no verdict of its own, and ``checked`` is not a
    gated status — so the same named refusal covers it with no special case."""
    parent = _generate(crossroads_osm_file)
    _install_probe(monkeypatch, _probe_for(parent))
    child = genesis_jobs.create_live_check_job(parent["id"], requested_by="yunha")
    finished = genesis_jobs.run_live_check_job(child["id"], child["workspace_id"])
    assert finished["status"] == genesis_jobs.STATUS_CHECKED

    with pytest.raises(genesis_jobs.GenesisRefused) as caught:
        genesis_jobs.create_live_check_job(child["id"], requested_by="yunha")
    assert caught.value.code == "genesis_live_check_parent_not_gated"


# ---------------------------------------------------------------------------
# §2 The evidence file is the probe's own shape — proven by round trip
# ---------------------------------------------------------------------------


def test_the_written_state_round_trips_into_an_admissible_oracle(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """The record this job writes is read back by ``gate.py``'s own reader, and
    the sha256 it computes is the PARENT artifact's — that identity is the whole
    reason the evidence counts."""
    parent = _generate(crossroads_osm_file)
    _install_probe(monkeypatch, _probe_for(parent))
    child = genesis_jobs.create_live_check_job(parent["id"], requested_by="yunha")
    genesis_jobs.run_live_check_job(child["id"], child["workspace_id"])

    state_path = genesis_jobs.job_dir(child["id"]) / genesis_jobs.LIVE_CHECK_STATE_FILENAME
    state = json.loads(state_path.read_text(encoding="utf-8"))
    label = f"live_check_{child['id']}"
    assert set(state["runs"]) == {label}
    assert state["parent_job_id"] == parent["id"]

    oracle, gap = LoadOracle.from_probe_state(state, label=label)
    assert gap is None
    assert oracle is not None
    assert oracle.load_ok is True
    assert oracle.network_sha256 == genesis_jobs.get_job(parent["id"])["gate"]["network_sha256"]


def test_a_state_naming_an_unknown_label_is_refused_by_the_reader(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """The run is read back BY NAME, so this job can never pick up another
    run's evidence — ``from_probe_state``'s newest-run fallback is never used
    here, and asking for the wrong label is a named gap rather than a silent
    substitution."""
    parent = _generate(crossroads_osm_file)
    _install_probe(monkeypatch, _probe_for(parent))
    child = genesis_jobs.create_live_check_job(parent["id"], requested_by="yunha")
    genesis_jobs.run_live_check_job(child["id"], child["workspace_id"])

    state = json.loads(
        (genesis_jobs.job_dir(child["id"]) / genesis_jobs.LIVE_CHECK_STATE_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    oracle, gap = LoadOracle.from_probe_state(state, label="live_check_somebodyelse")
    assert oracle is None
    assert gap == "label_absent:live_check_somebodyelse"


# ---------------------------------------------------------------------------
# §3 The judge is gate.py's, unmodified
# ---------------------------------------------------------------------------


def test_a_matching_load_turns_v1_green_on_the_parent(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """The unit's headline. V1 leaves NOT_CHECKABLE and the parent's verdict is
    re-decided — and it is still not a PASS, because a geometry-only draft
    leaves V3/V5/V6 open. The gate's ceiling, not this unit's."""
    parent = _generate(crossroads_osm_file)
    assert _v1_state(parent["id"]) == CheckState.NOT_CHECKABLE.value

    finished = _live_check(parent["id"], monkeypatch, _probe_for(parent))

    assert finished["status"] == genesis_jobs.STATUS_CHECKED
    assert finished["live_check"]["load_ok"] is True
    assert finished["live_check"]["admitted"] is True
    assert finished["live_check"]["vissim_version"] == "PTV Vissim Fake 2026"

    regated = genesis_jobs.get_job(parent["id"])
    assert _v1_state(parent["id"]) == CheckState.PASS.value
    assert "V1" not in regated["gate"]["blocking"]
    assert regated["status"] == genesis_jobs.STATUS_PROVISIONAL


def test_an_app_observed_pass_is_not_labelled_as_an_operators_claim(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """The basis is the difference between "somebody says Vissim opened this"
    and "this process opened it". A V1 green on THIS road is ``app_observed``
    and does not set ``rests_on_attestation`` — while an attested one still
    does, so the weaker claim keeps its label too."""
    parent = _generate(crossroads_osm_file)
    _live_check(parent["id"], monkeypatch, _probe_for(parent))

    gate = genesis_jobs.get_job(parent["id"])["gate"]
    assert gate["live_evidence"]["basis"] == genesis_jobs.LIVE_CHECK_BASIS
    assert gate["live_evidence"]["attested_by"] == "yunha"
    assert gate["rests_on_attestation"] is False

    # The same artifact, attested the old way: the label and the flag both flip.
    counts = parent["network"]
    artifact = genesis_jobs.job_dir(parent["id"]) / parent["artifact"]["inpx"]
    attested = genesis_jobs.attach_live_check(
        parent["id"],
        {
            "runs": {
                "live_a": {
                    "label": "live_a",
                    "started_utc": "2026-08-14T10:00:00+00:00",
                    "network": str(artifact),
                    "run": {
                        "com": {
                            "network": str(artifact),
                            "load_ok": True,
                            "save_ok": True,
                            "err_after_load": {"files": [], "new_files": []},
                            "com_topology": {
                                "link_collection_count": counts["links"] + counts["connectors"],
                                "is_conn_counts": {
                                    "plain_links": counts["links"],
                                    "connectors": counts["connectors"],
                                },
                                "nodes_count": counts["nodes"],
                            },
                            "version": {"att_values": {"Version": "PTV Vissim Test"}},
                        }
                    },
                }
            }
        },
        attested_by="somebody",
    )
    assert attested["gate"]["live_evidence"]["basis"] == genesis_jobs.ATTESTATION_BASIS
    assert attested["gate"]["rests_on_attestation"] is True


def test_evidence_about_other_bytes_does_not_flip_v1(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """The artifact changed under the job between the clone and the run, so the
    probe's file and the gated file are no longer the same bytes. That is not
    weak evidence, it is evidence about a different network — V1 stays
    NOT_CHECKABLE and NAMES the mismatch."""
    parent = _generate(crossroads_osm_file)
    artifact = genesis_jobs.job_dir(parent["id"]) / parent["artifact"]["inpx"]

    _install_probe(monkeypatch, _probe_for(parent))
    child = genesis_jobs.create_live_check_job(parent["id"], requested_by="yunha")
    # A trailing comment: still valid XML and still the same network, but NOT
    # the same bytes the clone carries.
    artifact.write_text(
        artifact.read_text(encoding="utf-8") + "\n<!-- edited after the clone -->\n",
        encoding="utf-8",
        newline="",
    )
    finished = genesis_jobs.run_live_check_job(child["id"], child["workspace_id"])

    assert finished["status"] == genesis_jobs.STATUS_CHECKED
    assert finished["live_check"]["admitted"] is False
    assert finished["live_check"]["gap_codes"] == ["v1_oracle_names_another_network"]

    regated = genesis_jobs.get_job(parent["id"])
    assert _v1_state(parent["id"]) == CheckState.NOT_CHECKABLE.value
    assert "V1" in regated["gate"]["blocking"]
    assert regated["status"] == genesis_jobs.STATUS_PROVISIONAL
    assert regated["gate"]["live_evidence"]["admitted"] is False


def test_a_refused_load_refuses_the_parent_and_revokes_its_publication(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """A downgrade takes the same road as an upgrade, and the JOB still
    succeeded: the probe answered, so it lands ``checked``. Only the parent's
    verdict got worse, which is the judge's call and not a failure of this
    runner."""
    parent = _generate(crossroads_osm_file)
    assert datasets.get_active_dataset()["source"] == "uploaded"

    finished = _live_check(
        parent["id"],
        monkeypatch,
        _probe_for(
            parent,
            load_ok=False,
            load_error="The network file could not be loaded.",
            save_ok=None,
        ),
    )

    assert finished["status"] == genesis_jobs.STATUS_CHECKED
    assert finished["live_check"]["load_ok"] is False
    assert finished["live_check"]["parent_status"] == genesis_jobs.STATUS_REFUSED

    regated = genesis_jobs.get_job(parent["id"])
    assert regated["status"] == genesis_jobs.STATUS_REFUSED
    assert _v1_state(parent["id"]) == CheckState.FAIL.value
    assert regated["publication"] is None
    assert regated["revocation"]["removed_from_workspace"] is True
    assert datasets.get_active_dataset()["source"] == "default"


def test_this_module_never_builds_an_oracle_by_hand(workspace_root: Path):
    """The same source-level property ``test_genesis_jobs.py`` pins for the
    attach path, re-asserted because this unit added a SECOND producer of V1
    evidence: the only way an oracle exists here is still
    ``LoadOracle.from_probe_state``, the function that hashes the file the probe
    actually loaded."""
    assert "LoadOracle.from_probe_state(" in GENESIS_JOBS_SOURCE
    assert not re.search(r"LoadOracle\(", GENESIS_JOBS_SOURCE)


# ---------------------------------------------------------------------------
# §4 The previous verdict survives, and the change is attributable
# ---------------------------------------------------------------------------


def test_the_prior_verdict_bytes_are_preserved_before_the_new_one_lands(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """A re-gate must never silently overwrite the verdict a reader was already
    shown. The BYTES are archived — not a re-serialisation of a parsed
    verdict — and the new one is genuinely different."""
    parent = _generate(crossroads_osm_file)
    gate_json = genesis_jobs.job_dir(parent["id"]) / genesis_jobs.GATE_JSON_FILENAME
    before = gate_json.read_bytes()

    _live_check(parent["id"], monkeypatch, _probe_for(parent))

    archived = (
        genesis_jobs.job_dir(parent["id"])
        / genesis_jobs.GATE_HISTORY_DIRNAME
        / "gate.0001.json"
    )
    assert archived.read_bytes() == before
    assert gate_json.read_bytes() != before


def test_a_second_live_check_archives_beside_the_first(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """The archive numbers up. Re-running a live check is a normal thing to do
    (a licence came back, the machine was fixed) and must not cost the earlier
    evidence."""
    parent = _generate(crossroads_osm_file)
    _live_check(parent["id"], monkeypatch, _probe_for(parent))
    _live_check(parent["id"], monkeypatch, _probe_for(parent))

    history = genesis_jobs.job_dir(parent["id"]) / genesis_jobs.GATE_HISTORY_DIRNAME
    assert sorted(p.name for p in history.iterdir()) == ["gate.0001.json", "gate.0002.json"]


def test_a_first_gate_archives_nothing(workspace_root: Path, crossroads_osm_file: Path):
    """Absence is the honest answer for a generation nobody re-gated: no empty
    directory, and the record says so rather than naming a file."""
    parent = _generate(crossroads_osm_file)

    assert not (genesis_jobs.job_dir(parent["id"]) / genesis_jobs.GATE_HISTORY_DIRNAME).exists()
    assert parent["gate"]["regate"] is None


def test_the_regate_names_the_job_that_caused_it(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """Lineage in both directions: the parent's verdict names the live-check job
    that replaced it and where the previous one is kept, and the live-check
    job's ``derived_from`` names the parent."""
    parent = _generate(crossroads_osm_file)
    _install_probe(monkeypatch, _probe_for(parent))
    child = genesis_jobs.create_live_check_job(parent["id"], requested_by="yunha")
    finished = genesis_jobs.run_live_check_job(child["id"], child["workspace_id"])

    regate = genesis_jobs.get_job(parent["id"])["gate"]["regate"]
    assert regate["source"] == "live_check_job"
    assert regate["job_id"] == child["id"]
    assert regate["at"]
    assert regate["previous_gate_archived"] == "gate_history/gate.0001.json"

    lineage = finished["derived_from"]
    assert lineage["kind"] == genesis_jobs.LIVE_CHECK_LINEAGE_KIND
    assert lineage["job_id"] == parent["id"]
    assert lineage["requested_by"] == "yunha"
    assert lineage["parent_sha256"] == parent["artifact"]["sha256"]
    # A live-check job is not a fix job: it carries none of that lineage's
    # approval fields, so a reader can never mistake one for the other.
    assert "finding_code" not in lineage
    assert "verb" not in lineage


# ---------------------------------------------------------------------------
# §5 The parent's directory is untouched; a probe that never answered decides
#    nothing
# ---------------------------------------------------------------------------


def test_the_probe_only_ever_opens_this_jobs_own_clone(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """CLAUDE.md's clone rule, as an assertion: the path handed to COM is inside
    the LIVE-CHECK job's work directory and is byte-identical to the parent's
    artifact."""
    parent = _generate(crossroads_osm_file)
    opened: list[Path] = []

    inner = _probe_for(parent)

    def _recording_probe(net_path: Path, label: str, **kwargs: Any) -> dict[str, Any]:
        opened.append(Path(net_path))
        return inner(net_path, label, **kwargs)

    _install_probe(monkeypatch, _recording_probe)
    child = genesis_jobs.create_live_check_job(parent["id"], requested_by="yunha")
    genesis_jobs.run_live_check_job(child["id"], child["workspace_id"])

    assert len(opened) == 1
    assert opened[0].parent == genesis_jobs.job_dir(child["id"])
    assert opened[0].read_bytes() == (
        genesis_jobs.job_dir(parent["id"]) / parent["artifact"]["inpx"]
    ).read_bytes()


def test_nothing_but_the_verdict_changes_under_the_parents_directory(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """The artifact, the census and the source .osm are the parent's own
    evidence and stay byte-identical; only the gate files (and the archive of
    the one being replaced) move."""
    parent = _generate(crossroads_osm_file)
    parent_dir = genesis_jobs.job_dir(parent["id"])
    before = _tree_digest(parent_dir)

    _live_check(parent["id"], monkeypatch, _probe_for(parent))

    after = _tree_digest(parent_dir)
    changed = {name for name in before if before[name] != after.get(name)}
    added = set(after) - set(before)
    assert changed == {genesis_jobs.GATE_JSON_FILENAME, genesis_jobs.GATE_REPORT_FILENAME}
    assert added == {str(Path(genesis_jobs.GATE_HISTORY_DIRNAME) / "gate.0001.json")}
    assert set(before) - set(after) == set()


def test_a_probe_that_never_answered_re_gates_nothing(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """No COM on the machine is a JOB failure, not evidence. The parent keeps
    the verdict it had, the failure is named, and no archive is created (because
    no re-gate happened)."""
    parent = _generate(crossroads_osm_file)
    before_gate = dict(parent["gate"])

    def _broken_probe(net_path: Path, label: str, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("pywin32 is not installed")

    finished = _live_check(parent["id"], monkeypatch, _broken_probe)

    assert finished["status"] == genesis_jobs.STATUS_ERROR
    assert finished["refusal"]["code"] == "live_check_failed:probe_failed"
    assert "pywin32" in finished["refusal"]["reasons"][0]

    unchanged = genesis_jobs.get_job(parent["id"])
    assert unchanged["gate"]["checked_at"] == before_gate["checked_at"]
    assert not (genesis_jobs.job_dir(parent["id"]) / genesis_jobs.GATE_HISTORY_DIRNAME).exists()


def test_a_record_with_no_load_result_is_not_treated_as_evidence(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """``fatal_error`` and a missing ``load_ok`` both mean the probe produced no
    verdict. Neither may reach the gate as "the load did not succeed" — that
    sentence belongs only to a probe that actually tried and was refused."""
    parent = _generate(crossroads_osm_file)

    def _fatal_probe(net_path: Path, label: str, **_kwargs: Any) -> dict[str, Any]:
        return {
            "label": label,
            "network": str(net_path),
            "connected": False,
            "fatal_error": "ImportError: No module named 'pythoncom'",
        }

    finished = _live_check(parent["id"], monkeypatch, _fatal_probe)

    assert finished["status"] == genesis_jobs.STATUS_ERROR
    assert finished["refusal"]["code"] == "live_check_failed:probe_produced_no_verdict"
    assert genesis_jobs.get_job(parent["id"])["status"] == genesis_jobs.STATUS_PROVISIONAL
    # The record is still written: a probe that died mid-COM left evidence of
    # what it managed to measure, and that file is how anyone diagnoses it.
    assert (
        genesis_jobs.job_dir(finished["id"]) / genesis_jobs.LIVE_CHECK_STATE_FILENAME
    ).is_file()


def test_a_refused_dataset_promote_is_named_rather_than_called_unexpected(
    workspace_root: Path, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    """On Windows a held handle on ``uploads/current`` is normal operation
    (services/datasets.py), and it means something specific: the evidence WAS
    produced and the verdict WAS re-decided; only the promote failed. The job
    says that, instead of reporting an unexpected error over a run that mostly
    worked."""
    parent = _generate(crossroads_osm_file)

    def refuse(*args: Any, **kwargs: Any):
        raise datasets.DatasetStorageError("something still has the model open")

    monkeypatch.setattr(datasets, "save_active_dataset", refuse)
    finished = _live_check(parent["id"], monkeypatch, _probe_for(parent))

    assert finished["status"] == genesis_jobs.STATUS_ERROR
    assert finished["refusal"]["code"] == "live_check_failed:parent_publish_refused"
    # The parent kept the verdict this run produced -- that is what makes the
    # distinction worth drawing.
    regated = genesis_jobs.get_job(parent["id"])
    assert regated["refusal"]["code"] == "publish_failed"
    assert _v1_state(parent["id"]) == CheckState.PASS.value


# ---------------------------------------------------------------------------
# §6 The HTTP surface — enqueue only, and refusals arrive as codes
# ---------------------------------------------------------------------------


def test_the_route_enqueues_and_answers_202(
    client: TestClient, crossroads_osm_file: Path, monkeypatch: pytest.MonkeyPatch
):
    parent = _generate(crossroads_osm_file)
    _install_probe(monkeypatch, _probe_for(parent))

    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/live-check/run", headers=admin_headers()
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["id"] != parent["id"]
    assert body["derived_from"]["job_id"] == parent["id"]
    assert body["derived_from"]["kind"] == genesis_jobs.LIVE_CHECK_LINEAGE_KIND
    assert body["submitted_by"] == "suite-admin"
    # The queued job is pollable through the SAME endpoint every other job uses.
    polled = client.get(f"/api/genesis/jobs/{body['id']}")
    assert polled.status_code == 200
    assert polled.json()["id"] == body["id"]

    # Drain the daemon thread before workspace_root tears WORKSPACES_ROOT down --
    # the same discipline (and the same reason) as
    # ``test_genesis_fix_routes.py``'s own 202 test.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        status = client.get(f"/api/genesis/jobs/{body['id']}").json()["status"]
        if status in genesis_jobs.TERMINAL_STATUSES:
            break
        time.sleep(0.02)
    else:  # pragma: no cover - a wedged runner is the failure, not a flake
        pytest.fail("the live-check job did not reach a terminal status in time")
    assert status == genesis_jobs.STATUS_CHECKED


def test_an_ineligible_parent_answers_409_with_the_named_code(
    client: TestClient, workspace_root: Path
):
    parent = _seed_job(_island_network(), name="ungated")

    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/live-check/run", headers=admin_headers()
    )

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "genesis_live_check_parent_not_gated"
    assert response.json()["detail"]["reasons"]


def test_an_unknown_job_is_404(client: TestClient, workspace_root: Path):
    response = client.post(
        "/api/genesis/jobs/abcdef123456/live-check/run", headers=admin_headers()
    )
    assert response.status_code == 404


def test_anonymous_callers_cannot_spend_com_time(client: TestClient, workspace_root: Path):
    """A live check burns a real Vissim seat against a project's tree, and
    ``requested_by`` is the reason a session is required at all — the same rule
    the attach route and the fix route already follow."""
    assert (
        client.post("/api/genesis/jobs/abcdef123456/live-check/run").status_code == 401
    )


def test_the_request_handler_never_touches_com(workspace_root: Path):
    """The 202 is the point: this route only mints and starts a job. Pinned
    against the source because "we always remember to enqueue" is a convention,
    and the job queue is what makes it a property."""
    assert "genesis_jobs.start_live_check_job(" in ROUTER_SOURCE
    assert "run_live_check_job" not in ROUTER_SOURCE
    # Asserted over the router's CODE, not its prose: its module docstring
    # discusses ``vissim_com`` precisely to say it never imports it, and a pin
    # that cannot tell an import from a sentence about imports is a pin that
    # will one day pass for the wrong reason.
    imported = {
        alias.name
        for node in ast.walk(ast.parse(ROUTER_SOURCE))
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or ""
        for node in ast.walk(ast.parse(ROUTER_SOURCE))
        if isinstance(node, ast.ImportFrom)
    }
    assert not any("vissim" in name for name in imported), sorted(imported)
    # The COM leg has exactly ONE construction site in the service layer, and it
    # is the seam tests replace.
    assert GENESIS_JOBS_SOURCE.count("from services.vissim_open_probe import probe_open") == 1
    assert GENESIS_JOBS_SOURCE.count("probe = _build_live_check_probe()") == 1
    assert GENESIS_JOBS_SOURCE.count("def _build_live_check_probe(") == 1


# ---------------------------------------------------------------------------
# §7 Unit #33 finding F1 — two re-gates on the same parent never interleave
# ---------------------------------------------------------------------------


def _oracle_from_probe(parent: dict[str, Any], probe, *, label: str):
    """A :class:`LoadOracle` built the same way a live-check job's own runner
    builds one -- ``LoadOracle.from_probe_state`` over the probe's ``com``
    record, wrapped in the state.json shape -- but without the clone/job
    machinery, so two of these can be handed straight to ``gate_and_publish``
    and raced against each other at the exact choke point F1 named.
    """
    inpx_path = genesis_jobs.job_dir(parent["id"]) / parent["artifact"]["inpx"]
    com = probe(inpx_path, label)
    state = {
        "runs": {
            label: {
                "label": label,
                "network": str(inpx_path),
                "started_utc": "2026-08-14T09:00:00+00:00",
                "finished_utc": "2026-08-14T09:00:20+00:00",
                "run": {"com": com},
            }
        }
    }
    return LoadOracle.from_probe_state(state, label=label)


def test_two_concurrent_regates_never_interleave_and_lose_no_verdict(
    workspace_root: Path, crossroads_osm_file: Path
):
    """Adversarial finding F1 (unit #33 review). Before the per-(workspace, job)
    lock in ``genesis_jobs._gate_lock``, ``gate_and_publish``'s critical section
    -- read the outgoing ``gate.json`` -> archive it -> write the new verdict ->
    update the registry summary -- had no mutual exclusion at all, and the
    live-check job path and ``attach_live_check`` both reach it for the SAME
    parent with nothing stopping a double-click across two tabs from running
    both at once. Proven outcomes on the unfixed code: two racers each archive
    the SAME pre-race verdict and each write their own new one, so whichever
    write loses -- possibly a REFUSED carrying real evidence -- vanishes with no
    trace; or ``gate.json`` on disk ends up naming a verdict the registry
    summary disagrees with.

    This test forces the interleaving through hooks, never through timing.
    ``genesis_jobs._gate_lock`` is wrapped so entering/leaving the ACTUAL lock
    each racer acquires is timestamped -- that span is, by definition, the
    critical section the fix protects, so recording it (rather than guessing
    at it from the outside) is what makes assertion (a) a proof rather than a
    hopeful timing check. ``_archive_previous_gate`` is separately hooked purely
    to PARK the first racer (via a ``threading.Event``, never a sleep) at a
    known instant inside its own held lock, so the second racer can be started
    while the first is deterministically still inside its critical section --
    giving a real, not probabilistic, chance to observe an overlap if the lock
    were not doing its job. It then asserts the fix's three remaining
    guarantees: the on-disk verdict and the registry's summary verdict agree;
    ``gate_history`` holds exactly the pre-race verdict and the first racer's
    verdict, one each; and both racers' own verdicts are independently
    recoverable (one current, one archived) -- nothing was lost.

    Verified during development (not asserted here -- this is a one-time check
    of the test itself, not a permanent code path) that assertion (a) fails
    when the wrapper below acquires ``contextlib.nullcontext()`` instead of the
    real per-job lock: the second racer's recorded span then starts while the
    first racer's is still open, because nothing then stops it from running
    ``_archive_previous_gate`` while the first is still parked there.
    """
    parent = _generate(crossroads_osm_file)
    work_dir = genesis_jobs.job_dir(parent["id"])
    pre_race_bytes = (work_dir / genesis_jobs.GATE_JSON_FILENAME).read_bytes()

    oracle_first, gap_first = _oracle_from_probe(
        parent, _probe_for(parent), label="live_check_first"
    )
    oracle_second, gap_second = _oracle_from_probe(
        parent,
        _probe_for(
            parent,
            load_ok=False,
            load_error="The network file could not be loaded.",
            save_ok=None,
        ),
        label="live_check_second",
    )
    assert gap_first is None and oracle_first is not None
    assert gap_second is None and oracle_second is not None

    # Timestamps around the REAL lock's own acquire/release -- the ground
    # truth for "did the two critical sections overlap", not a proxy for it.
    lock_events: list[tuple[str, str, float]] = []
    real_gate_lock = genesis_jobs._gate_lock

    class _RecordingLock:
        def __init__(self, inner) -> None:
            self._inner = inner

        def __enter__(self):
            self._inner.__enter__()
            lock_events.append((threading.current_thread().name, "acquired", time.monotonic()))
            return self

        def __exit__(self, exc_type, exc, tb):
            lock_events.append((threading.current_thread().name, "released", time.monotonic()))
            return self._inner.__exit__(exc_type, exc, tb)

    def _recording_gate_lock(job_id: str) -> "_RecordingLock":
        return _RecordingLock(real_gate_lock(job_id))

    # A separate hook, only to PARK the first racer at a known instant while
    # it still holds the lock above -- the deterministic substitute for "hope
    # the scheduler interleaves them".
    first_entered = threading.Event()
    first_release = threading.Event()
    real_archive = genesis_jobs._archive_previous_gate

    def _parking_archive_hook(job_work_dir: Path):
        if threading.current_thread().name == "first_racer":
            first_entered.set()
            assert first_release.wait(timeout=10), "test never released the first racer"
        return real_archive(job_work_dir)

    results: dict[str, dict[str, Any]] = {}
    errors: dict[str, BaseException] = {}
    workspace_id = parent["workspace_id"]

    def _racer(key: str, oracle: LoadOracle, gap: str | None, label: str) -> None:
        try:
            with workspaces.bind_workspace(workspace_id):
                results[key] = genesis_jobs.gate_and_publish(
                    parent["id"],
                    live_oracle=oracle,
                    live_oracle_gap=gap,
                    attestation={
                        "by": "tester",
                        "at": "2026-08-14T09:00:00+00:00",
                        "label": label,
                        "basis": genesis_jobs.LIVE_CHECK_BASIS,
                    },
                    regate_source={
                        "source": "live_check_job",
                        "job_id": f"{key}-racer-job",
                        "at": "2026-08-14T09:00:00+00:00",
                    },
                )
        except BaseException as exc:  # noqa: BLE001 - surfaced by the assertion below
            errors[key] = exc

    genesis_jobs._gate_lock = _recording_gate_lock
    genesis_jobs._archive_previous_gate = _parking_archive_hook
    try:
        first_thread = threading.Thread(
            target=_racer,
            args=("first", oracle_first, gap_first, "live_check_first"),
            name="first_racer",
        )
        second_thread = threading.Thread(
            target=_racer,
            args=("second", oracle_second, gap_second, "live_check_second"),
            name="second_racer",
        )

        first_thread.start()
        assert first_entered.wait(timeout=10), "the first racer never reached the archive step"
        second_thread.start()
        # Released only AFTER the second racer has been started, so a build
        # that lost the lock has every chance to let the two interleave.
        first_release.set()
        first_thread.join(timeout=10)
        second_thread.join(timeout=10)
    finally:
        genesis_jobs._gate_lock = real_gate_lock
        genesis_jobs._archive_previous_gate = real_archive

    assert not first_thread.is_alive() and not second_thread.is_alive(), "a racer thread hung"
    assert not errors, errors

    # (a) The two critical sections never overlapped -- measured on the real
    # lock's own acquire/release instants, not guessed at from the outside.
    by_thread: dict[str, dict[str, float]] = {}
    for name, kind, ts in lock_events:
        by_thread.setdefault(name, {})[kind] = ts
    assert set(by_thread) == {"first_racer", "second_racer"}
    assert by_thread["first_racer"]["released"] <= by_thread["second_racer"]["acquired"], (
        "the first racer still held the lock when the second racer acquired it -- "
        "the critical sections overlapped"
    )

    # (b) The on-disk verdict and the registry's summary verdict agree.
    final_gate_json = json.loads((work_dir / genesis_jobs.GATE_JSON_FILENAME).read_text(encoding="utf-8"))
    registry_record = genesis_jobs.get_job(parent["id"])
    assert final_gate_json["verdict"] == registry_record["gate"]["verdict"]
    # The second racer (load_ok=False) is the one that ran last, so its REFUSED
    # verdict is what survives on top -- exactly the ordering the lock promises,
    # not a coincidence of which write happened to land.
    assert registry_record["status"] == genesis_jobs.STATUS_REFUSED
    assert final_gate_json["verdict"] == results["second"]["gate"]["verdict"]

    # (c) gate_history holds every superseded verdict exactly once: the
    # pre-race verdict this test captured before either racer ran, and the
    # first racer's verdict that the second racer's own re-gate superseded.
    history_dir = work_dir / genesis_jobs.GATE_HISTORY_DIRNAME
    archived_names = sorted(p.name for p in history_dir.iterdir())
    assert archived_names == ["gate.0001.json", "gate.0002.json"]
    assert (history_dir / "gate.0001.json").read_bytes() == pre_race_bytes

    # (d) Neither racer's verdict was lost: the first racer's is the archived
    # gate.0002.json, the second racer's is the current gate.json -- one
    # current, one archived, both readable.
    archived_first = json.loads((history_dir / "gate.0002.json").read_text(encoding="utf-8"))
    assert archived_first["verdict"] == results["first"]["gate"]["verdict"]
    assert archived_first["verdict"] != genesis_jobs.GateVerdict.REFUSED.value
    assert final_gate_json["verdict"] == genesis_jobs.GateVerdict.REFUSED.value

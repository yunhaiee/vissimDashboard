"""Roadmap B5-2 stage 3 -- the two HTTP endpoints over the fix registry
(``services.agent.genesis_fixes``) and the fix-job pipeline
(``services.genesis_jobs.create_genesis_fix_job``/``start_genesis_fix_job``):

* ``GET  /api/genesis/jobs/{job_id}/fixes`` -- what the registry says about
  EVERY finding in a job's stored gate verdict.
* ``POST /api/genesis/jobs/{job_id}/fixes/apply`` -- spend one approved fix as
  a sibling job.

No COM here either: the same ``FakeFixEditSession`` seam
``test_genesis_fix_jobs.py`` uses for the pipeline underneath these routes,
reused directly rather than re-faked -- these tests are about the HTTP
boundary, not about re-proving the pipeline §1-§6 already prove.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from conftest import admin_headers
from services import genesis_jobs, workspaces

from tests.test_genesis_fix_jobs import (
    _FINDING_CODE,
    _MEASURED_LINK_1_NODE_EVIDENCE,
    _PARAMS,
    _VERB,
    _fake_session_factory,
    _fake_v6_node_evidence,
    _job_dirs,
    _seed_passing_parent,
    _seed_refused_fix_parent,
    _seed_two_input_refused_parent,
    _v6_zero_volume_network,
)
from tests.test_genesis_jobs import _seed_job


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Same shape as ``test_genesis_fix_jobs.py``'s own fixture of the same
    name -- duplicated, not imported, per this repo's convention (see that
    file's docstring: cross-imports only plain helper FUNCTIONS, never
    fixtures)."""
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


def _apply_body(**overrides: Any) -> dict[str, Any]:
    body = {"finding_code": _FINDING_CODE, "verb": _VERB, "params": dict(_PARAMS)}
    body.update(overrides)
    return body


# ===========================================================================
# 404 -- unknown job, both routes
# ===========================================================================


def test_get_fixes_404s_on_an_unknown_job(client: TestClient, workspace_root: Path):
    response = client.get("/api/genesis/jobs/does-not-exist/fixes", headers=admin_headers())
    assert response.status_code == 404


def test_apply_fix_404s_on_an_unknown_job(client: TestClient, workspace_root: Path):
    response = client.post(
        "/api/genesis/jobs/does-not-exist/fixes/apply",
        json=_apply_body(),
        headers=admin_headers(),
    )
    assert response.status_code == 404


# ===========================================================================
# 409 -- GET before a gate verdict exists
# ===========================================================================


def test_get_fixes_409s_when_the_job_has_no_stored_verdict_yet(
    client: TestClient, workspace_root: Path
):
    """``_seed_job`` (mid-gating: status RUNNING, no ``gate.json`` written
    yet) is the SAME "job exists, verdict does not" state
    ``GET .../gate`` answers 409 for -- this route must draw the same line,
    with its OWN named code."""
    record = _seed_job(_v6_zero_volume_network(with_island=False), name="ungated")
    response = client.get(f"/api/genesis/jobs/{record['id']}/fixes", headers=admin_headers())
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "genesis_fix_no_gate_verdict"


# ===========================================================================
# 401 -- unauthenticated, both routes
# ===========================================================================


def test_get_fixes_401s_without_a_session(client: TestClient, workspace_root: Path):
    parent = _seed_refused_fix_parent(with_island=True)
    assert client.get(f"/api/genesis/jobs/{parent['id']}/fixes").status_code == 401


def test_apply_fix_401s_without_a_session(client: TestClient, workspace_root: Path):
    parent = _seed_refused_fix_parent(with_island=True)
    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/fixes/apply", json=_apply_body()
    )
    assert response.status_code == 401


# ===========================================================================
# GET shape -- complete over the verdict, verbatim reasons, ask-first requires
# ===========================================================================


def test_get_fixes_covers_every_finding_with_no_drops(client: TestClient, workspace_root: Path):
    """"complete over the verdict's findings -- no silent drops" (the spec's
    own words), checked against an INDEPENDENT count taken straight off the
    stored gate.json, not against a number this test made up."""
    parent = _seed_refused_fix_parent(with_island=True)
    stored = json.loads(
        (genesis_jobs.job_dir(parent["id"]) / genesis_jobs.GATE_JSON_FILENAME).read_text(
            encoding="utf-8"
        )
    )
    total_raw = sum(
        len(check.get("findings", [])) + len(check.get("warnings", []))
        for check in stored["checks"]
    )
    assert total_raw > 0, "fixture must actually carry findings for this test to mean anything"

    response = client.get(f"/api/genesis/jobs/{parent['id']}/fixes", headers=admin_headers())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["job_id"] == parent["id"]
    assert body["verdict"] == "refused"
    assert body["unmapped_finding_codes"] == []
    assert len(body["findings"]) == total_raw


def test_get_fixes_not_automatable_entry_carries_its_reason_verbatim(
    client: TestClient, workspace_root: Path
):
    from services.agent import genesis_fixes as gf

    parent = _seed_refused_fix_parent(with_island=True)
    response = client.get(f"/api/genesis/jobs/{parent['id']}/fixes", headers=admin_headers())
    body = response.json()

    v3_entries = [entry for entry in body["findings"] if entry["code"].startswith("v3_")]
    assert v3_entries, "the island link must produce at least one V3 finding"
    entry = v3_entries[0]
    disposition = entry["disposition"]
    assert disposition["status"] == "NOT_AUTOMATABLE"
    assert disposition["verb"] is None
    assert disposition["reason_code"] == gf.CONNECTIVITY_IS_NOT_INVENTED
    mapping = gf.get_genesis_fix(entry["code"])
    assert mapping.reason_ko in disposition["rationale"]["ko"]
    assert mapping.reason_en in disposition["rationale"]["en"]
    assert mapping.reason_ja in disposition["rationale"]["ja"]


def test_get_fixes_v6_on_an_unverified_entrance_carries_requires_naming_the_volume(
    client: TestClient, workspace_root: Path
):
    parent = _seed_refused_fix_parent(with_island=True)
    response = client.get(f"/api/genesis/jobs/{parent['id']}/fixes", headers=admin_headers())
    body = response.json()

    v6_entries = [
        entry for entry in body["findings"] if entry["code"] == "v6_input_volume_is_zero"
    ]
    assert len(v6_entries) == 1
    disposition = v6_entries[0]["disposition"]
    assert disposition["status"] == "AUTOMATABLE"
    assert disposition["verb"] == "set_vehicle_input_volume"
    # No volume was supplied to this GET -- the ask-first rule -- so no
    # PROPOSAL, and the missing param is named rather than invented.
    assert disposition["requires"] == ["volume"]
    assert disposition["params"] is not None and "volume" not in disposition["params"]


# ===========================================================================
# POST apply -- happy path
# ===========================================================================


def test_apply_fix_happy_path_returns_202_and_names_the_session_user_as_approver(
    client: TestClient, workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        genesis_jobs, "_build_fix_edit_session", _fake_session_factory(readback=500.0)
    )
    parent = _seed_refused_fix_parent(with_island=True)
    headers = admin_headers()

    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/fixes/apply",
        json=_apply_body(approval_rationale="sibling entrance measured 500 veh/h"),
        headers=headers,
    )
    assert response.status_code == 202, response.text
    sibling = response.json()
    assert sibling["id"] != parent["id"]
    assert sibling["derived_from"]["job_id"] == parent["id"]
    assert sibling["derived_from"]["finding_code"] == _FINDING_CODE
    assert sibling["derived_from"]["verb"] == _VERB
    assert sibling["derived_from"]["approved_by"] == "suite-admin"
    assert "approve" not in _apply_body(), "approved_by must never be a client-suppliable field"

    # Direct read agrees -- the 202 body is not the only place this lands.
    direct = client.get(f"/api/genesis/jobs/{sibling['id']}", headers=headers)
    assert direct.status_code == 200
    assert direct.json()["derived_from"]["approved_by"] == "suite-admin"

    # Drain the daemon thread before the workspace_root fixture tears down
    # WORKSPACES_ROOT: otherwise the fake-COM run can still be mid-flight
    # against a root the NEXT test's fixture has already replaced.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if client.get(f"/api/genesis/jobs/{sibling['id']}", headers=headers).json()[
            "status"
        ] in genesis_jobs.TERMINAL_STATUSES:
            break
        time.sleep(0.02)
    else:
        pytest.fail("fix job did not reach a terminal status in time")


# ===========================================================================
# POST apply -- named refusals
# ===========================================================================


def test_apply_fix_under_a_not_automatable_finding_is_400_verb_mismatch(
    client: TestClient, workspace_root: Path
):
    """``v3_orphan_link`` is declared NOT_AUTOMATABLE catalogue-wide -- no
    verb may ever be applied under it, regardless of whether this parent's
    OWN verdict happens to carry that exact code."""
    parent = _seed_refused_fix_parent(with_island=True)
    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/fixes/apply",
        json=_apply_body(finding_code="v3_orphan_link", verb="set_link_lane_count", params={}),
        headers=admin_headers(),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "genesis_fix_verb_mismatch"


def test_apply_fix_with_a_verb_the_findings_own_mapping_does_not_name_is_400(
    client: TestClient, workspace_root: Path
):
    """An AUTOMATABLE finding whose mapping names a DIFFERENT verb than the
    one the caller supplies -- the "arbitrary verb" case the spec calls out
    by name."""
    parent = _seed_refused_fix_parent(with_island=True)
    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/fixes/apply",
        json=_apply_body(verb="set_link_lane_count", params={}),
        headers=admin_headers(),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "genesis_fix_verb_mismatch"


def test_apply_fix_with_an_unknown_finding_code_is_400(client: TestClient, workspace_root: Path):
    parent = _seed_refused_fix_parent(with_island=True)
    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/fixes/apply",
        json=_apply_body(finding_code="not_a_real_finding_code"),
        headers=admin_headers(),
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "genesis_fix_unknown_finding"


def test_apply_fix_with_a_stale_verb_is_409(client: TestClient, workspace_root: Path):
    parent = _seed_refused_fix_parent(with_island=True)
    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/fixes/apply",
        json=_apply_body(params={"input_no": "1", "volume": "not-a-number", "interval_no": 1}),
        headers=admin_headers(),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "genesis_fix_verb_no_longer_registered"


def test_apply_fix_on_a_non_refused_parent_is_409(client: TestClient, workspace_root: Path):
    parent = _seed_passing_parent()
    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/fixes/apply",
        json=_apply_body(),
        headers=admin_headers(),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "genesis_fix_parent_not_refused"


# ===========================================================================
# Fix round (adversarial pass) -- the two exploits that must die at the
# HTTP boundary too, not merely at the service layer §7 already proves.
# ===========================================================================


def test_apply_fix_on_a_measured_entrance_is_409_measurement_gate_not_satisfied(
    client: TestClient, workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """Exploit #1, route level: a hand-crafted, otherwise well-formed POST
    against a MEASURED entrance must be refused BY NAME through the SAME
    door a legitimate approval uses -- the router's own static check alone
    (verb resolves + matches the mapping) would have let this through; only
    the service layer's real-evidence re-derivation catches it. No sibling
    job directory is created."""
    monkeypatch.setattr(genesis_jobs, "derive_v6_node_evidence", _fake_v6_node_evidence())
    parent = _seed_refused_fix_parent(with_island=True)
    before = _job_dirs(workspace_root)

    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/fixes/apply",
        json=_apply_body(),
        headers=admin_headers(),
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "genesis_fix_measurement_gate_not_satisfied"

    after = _job_dirs(workspace_root)
    assert after == before, "a measurement-gate refusal must not create a sibling job directory"


def test_apply_fix_with_params_on_a_different_input_than_the_finding_is_409(
    client: TestClient, workspace_root: Path
):
    """Exploit #2, route level: the same finding-on-input-1/params-on-input-2
    mismatch §7 proves at the service layer, driven through the HTTP door.
    ``subject`` names input 1's finding explicitly (unit #30): this doubles as
    proof the route THREADS the body's subject through to the service layer --
    a route that dropped it would answer with the ambiguity code instead."""
    parent = _seed_two_input_refused_parent()
    before = _job_dirs(workspace_root)

    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/fixes/apply",
        json=_apply_body(subject="1", params={"input_no": "2", "volume": 500.0, "interval_no": 1}),
        headers=admin_headers(),
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "genesis_fix_params_target_mismatch"

    after = _job_dirs(workspace_root)
    assert after == before


def test_apply_fix_without_subject_on_a_duplicated_code_is_409_ambiguous(
    client: TestClient, workspace_root: Path
):
    """Unit #30, route level: a subject-less POST against a code that appears
    twice in the parent's verdict is refused with the NEW named code, the
    candidate subjects riding in ``reasons`` verbatim, and no sibling job
    directory -- a legacy caller (the body simply lacks the optional field)
    gets a refusal that tells it exactly what to send, never a silent
    first-match."""
    parent = _seed_two_input_refused_parent()
    before = _job_dirs(workspace_root)

    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/fixes/apply",
        json=_apply_body(),  # no `subject` key at all
        headers=admin_headers(),
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "genesis_fix_finding_ambiguous_without_subject"
    assert "'1'" in detail["reasons"][0]
    assert "'2'" in detail["reasons"][0]

    after = _job_dirs(workspace_root)
    assert after == before


def test_get_fixes_on_a_measured_entrance_shows_the_structural_refusal_not_requires_volume(
    client: TestClient, workspace_root: Path, monkeypatch: pytest.MonkeyPatch
):
    """Fix round item 3 / test item 4: GET must be able to SHOW the
    measurement doctrine. A MEASURED entrance's V6 finding must read as the
    structural refusal it is -- not as "just needs a volume", which is what
    every OTHER (genuinely unverified) V6 finding legitimately shows.

    Same seam as the service-level regressions: real ``services.observations``
    data cannot itself produce ENTRANCE_MEASURED today (leg-keyed rows, never
    an OBJECT_ID -- see ``genesis_jobs.derive_v6_node_evidence``'s own
    docstring), so ``derive_v6_node_evidence`` is monkeypatched directly to
    supply the evidence a genuinely joinable entrance would carry.
    """
    monkeypatch.setattr(genesis_jobs, "derive_v6_node_evidence", _fake_v6_node_evidence())
    parent = _seed_refused_fix_parent(with_island=True)

    response = client.get(f"/api/genesis/jobs/{parent['id']}/fixes", headers=admin_headers())
    assert response.status_code == 200, response.text
    body = response.json()

    v6_entries = [
        entry for entry in body["findings"] if entry["code"] == "v6_input_volume_is_zero"
    ]
    assert len(v6_entries) == 1
    disposition = v6_entries[0]["disposition"]
    assert disposition["status"] == "AUTOMATABLE", "the DECLARED status is unchanged"
    assert disposition["verb"] == "set_vehicle_input_volume"
    assert disposition["reason_code"] == "INPUT_VOLUME_IS_OBSERVED_DATA"
    assert disposition["requires"] == [], "a measured entrance cannot be fixed by supplying MORE params"
    assert disposition["params"] is None
    assert "MEASURED" in disposition["rationale"]["en"]
    assert str(_MEASURED_LINK_1_NODE_EVIDENCE["total_real"]) in disposition["rationale"]["en"]

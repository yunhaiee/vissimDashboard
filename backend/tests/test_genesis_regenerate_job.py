"""Unit #38 (b) — REGENERATE_WITH_FIX: the treatment that re-derives instead
of editing.

Unit #34 proved the V7 shared-polyline findings on every real refused job are
UNCORRECTABLE from the census: the draft's own twin records are byte-identical,
so the corruption predates the draft and no coordinate exists to propose that
was not invented. That left 164 real findings with an honest refusal and no
treatment. Unit #38 (a) fixed the converter that caused it; this half is how an
already-refused job gets the benefit **without anything being invented**: the
SAME source bytes, sha-verified against the parent's own record, re-derived
through the corrected converter and judged by the same unmodified gate.

The doctrine each family guards:

* §1 the disposition is MEASURED, not declared — a finding the census CAN
  correct keeps unit #34's ``set_link_geometry`` path, and only the measured
  ``draft_shares_the_corruption`` reason earns the regenerate path;
* §2 eligibility refuses BY NAME (never a disabled button), including the one
  refusal that keeps this from being a re-roll button;
* §3 the lineage is a claim that was CHECKED: same bytes, same digest, named
  converter fixes, named findings;
* §4 the parent is byte-untouched — the B5-2 rule every sibling job obeys;
* §5 the regenerated job really does clear the class, measured end to end on a
  source whose parent artifact was produced from those same bytes by the
  PRE-FIX converter;
* §6 the HTTP surface: 202, 404, 409-by-code, and no anonymous callers.

No COM anywhere: a regeneration is an ordinary generation (pure Python), so the
runner here is the real one.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from conftest import admin_headers
from services import genesis_jobs, workspaces
from services.agent import fixes as agent_fixes
from services.agent import genesis_fixes
from services.genesis.osm import WAY_REDECLARATION_FIX_ID

from tests.test_genesis_jobs import _generate, _island_network, _seed_job
from tests.test_genesis_osm_draft import LAT0, LON0, _osm_text
from tests.test_genesis_osm_redeclared_ways import _pre_fix

_CODE = "v7_links_share_one_polyline"

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


# ---------------------------------------------------------------------------
# The corrupted source, and a parent generated from it by the PRE-FIX reader
# ---------------------------------------------------------------------------

#: Two crossing roads so the network has junctions, connectors and an exit —
#: one of them declared TWICE, exactly as the real Overpass exports do.
_NODES = [
    (1, LAT0, LON0 - 0.004),
    (2, LAT0, LON0 - 0.002),
    (3, LAT0, LON0),
    (4, LAT0, LON0 + 0.002),
    (5, LAT0, LON0 + 0.004),
    (6, LAT0 - 0.003, LON0),
    (7, LAT0 + 0.003, LON0),
]
_MAIN = {"highway": "secondary", "name": "Main St", "lanes": "2"}
_CROSS = {"highway": "residential", "name": "Cross St"}


def _duplicated_source() -> str:
    return _osm_text(
        _NODES,
        [
            (101, [1, 2, 3, 4, 5], dict(_MAIN)),
            (102, [6, 3, 7], dict(_CROSS)),
            (101, [1, 2, 3, 4, 5], dict(_MAIN)),
        ],
    )


def _clean_source() -> str:
    return _osm_text(
        _NODES,
        [
            (101, [1, 2, 3, 4, 5], dict(_MAIN)),
            (102, [6, 3, 7], dict(_CROSS)),
        ],
    )


def _osm_file(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _pre_fix_parent(tmp_path: Path, *, name: str = "dupe") -> dict:
    """A real generation of the duplicated source, run by the PRE-FIX reader.

    This is the honest shape of the problem: the parent's artifact, census,
    stored source and gate verdict all really were produced from these bytes by
    the converter as it behaved before this unit — so everything measured about
    the regeneration afterwards is a comparison against the real defect, not
    against a hand-written imitation of it.
    """
    source = _osm_file(tmp_path, f"{name}.osm", _duplicated_source())
    with _pre_fix():
        parent = _generate(source, name=name)
    assert parent["status"] in genesis_jobs.GATED_STATUSES, parent
    return parent


def _v7_count(record: dict[str, Any]) -> int:
    return sum(
        1
        for entry in genesis_jobs.iter_stored_gate_findings(_stored_gate(record["id"]))
        if entry[2]["code"] == _CODE
    )


def _stored_gate(job_id: str) -> dict[str, Any]:
    import json

    return json.loads(
        (genesis_jobs.job_dir(job_id) / genesis_jobs.GATE_JSON_FILENAME).read_text(encoding="utf-8")
    )


def _dir_fingerprint(path: Path) -> dict[str, str]:
    """Every file under ``path``, by relative name → sha256 of its bytes."""
    return {
        str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


# ---------------------------------------------------------------------------
# §1 The disposition is measured, never declared
# ---------------------------------------------------------------------------


def test_regenerate_with_fix_can_never_be_declared_by_a_registry_entry(
    monkeypatch: pytest.MonkeyPatch,
):
    """A catalogue author must not be able to type this status onto an entry:
    whether regeneration is the answer depends on what the EVIDENCE measured
    about a given finding, exactly as ``VERB_UNREGISTERED`` depends on the live
    tool registry.

    The real entry for this code is lifted out for the duration (and restored
    by ``monkeypatch``) so the guard under test is the STATUS check rather than
    the duplicate-code check that would fire first.
    """
    monkeypatch.delitem(genesis_fixes._REGISTRY, _CODE)
    mapping = genesis_fixes.GenesisFixMapping(
        code=_CODE,
        status=genesis_fixes.REGENERATE_WITH_FIX,
        check_id="V7",
        label_ko="x",
        label_en="x",
        label_ja="x",
        reason_ko="x",
        reason_en="x",
        reason_ja="x",
        reason_code="X",
    )
    with pytest.raises(ValueError) as excinfo:
        genesis_fixes.register_genesis_fix(mapping)
    assert "derived, never declared" in str(excinfo.value)


def test_only_the_uncorrectable_measured_reason_earns_the_regenerate_path():
    """The eligibility measurement in isolation. A refusal with any OTHER
    measured reason -- or none -- is not a regeneration candidate: it either has
    unit #34's edit path or an honest refusal, and offering a whole
    re-derivation instead would be the bigger hammer for the smaller nail."""

    class _Proposal:
        def __init__(self, evidence):
            self.evidence = evidence

    eligible = genesis_fixes.regeneration_disposition(
        _CODE, _Proposal({"census_derivation": {"reason_code": "draft_shares_the_corruption"}})
    )
    assert eligible is not None
    assert eligible["status"] == genesis_fixes.REGENERATE_WITH_FIX
    assert eligible["derivation_reason_code"] == "draft_shares_the_corruption"
    assert set(eligible["explanation"]) == {"ko", "en", "ja"}

    for evidence in (
        {},
        {"census_derivation": {}},
        {"census_derivation": {"reason_code": "draft_equals_emitted"}},
        {"census_derivation": {"reason_code": "point_count_differs"}},
    ):
        assert genesis_fixes.regeneration_disposition(_CODE, _Proposal(evidence)) is None


def test_the_route_renders_the_regenerate_disposition_on_the_real_finding(
    tmp_path: Path, client: TestClient
):
    """End to end through the fixes listing: the same finding unit #34 left as
    ``NO_TOOL_WITHOUT_FABRICATION`` now carries the third disposition, the
    converter-fix ledger, and the explanation a card can render -- while
    KEEPING the measured refusal reason that justifies it."""
    parent = _pre_fix_parent(tmp_path)

    listed = client.get(f"/api/genesis/jobs/{parent['id']}/fixes", headers=admin_headers())
    assert listed.status_code == 200, listed.text
    entries = [e for e in listed.json()["findings"] if e["code"] == _CODE]
    assert entries, "the pre-fix parent must carry the shared-polyline finding"

    disposition = entries[0]["disposition"]
    assert disposition["status"] == genesis_fixes.REGENERATE_WITH_FIX
    assert disposition["verb"] is None and disposition["params"] is None
    assert disposition["reason_code"] == genesis_fixes.NO_TOOL_WITHOUT_FABRICATION
    assert "upstream of the draft" in disposition["rationale"]["en"]
    regenerate = disposition["regenerate"]
    assert regenerate["derivation_reason_code"] == "draft_shares_the_corruption"
    assert [fix["id"] for fix in regenerate["converter_fixes"]] == [WAY_REDECLARATION_FIX_ID]
    assert "re-derived" in regenerate["explanation"]["en"]


def test_a_census_correctable_finding_keeps_the_edit_path(
    workspace_root: Path, client: TestClient
):
    """The counterexample that gives §1 its teeth, borrowed from unit #34's own
    fixture: twins the DRAFT records distinctly are correctable, so the entry
    stays AUTOMATABLE with real params and no regenerate block."""
    from tests.test_genesis_fix_v7_polyline import (
        _DRAFT_7,
        _DRAFT_8,
        _draft_link,
        _seed_v7_refused_parent,
    )

    parent = _seed_v7_refused_parent([_draft_link(7, _DRAFT_7), _draft_link(8, _DRAFT_8)])
    listed = client.get(f"/api/genesis/jobs/{parent['id']}/fixes", headers=admin_headers())
    (entry,) = [e for e in listed.json()["findings"] if e["code"] == _CODE]

    assert entry["disposition"]["status"] == agent_fixes.AUTOMATABLE
    assert entry["disposition"]["params"]["points_xy"] == _DRAFT_7
    assert "regenerate" not in entry["disposition"]

    # ... and the service-layer gate agrees: this parent is NOT regenerable.
    assert genesis_jobs.regenerable_findings(parent["id"]) == []


# ---------------------------------------------------------------------------
# §2 Eligibility refuses by NAME
# ---------------------------------------------------------------------------


def test_an_ungated_parent_is_refused_by_name(workspace_root: Path):
    parent = _seed_job(_island_network(), name="ungated")
    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_regenerate_job(parent["id"], requested_by="reviewer")
    assert excinfo.value.code == "genesis_regenerate_parent_not_gated"


def test_a_parent_with_no_source_of_its_own_is_refused_by_name(workspace_root: Path):
    """A live-check job and a fix job both carry ``source_osm: None`` by
    construction. A regeneration needs the FILE, not a lineage pointer."""
    parent = _seed_job(_island_network(), name="nosource")
    genesis_jobs.gate_and_publish(parent["id"])
    genesis_jobs._update(parent["id"], source_osm=None)

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_regenerate_job(parent["id"], requested_by="reviewer")
    assert excinfo.value.code == "genesis_regenerate_source_missing"
    assert "regenerate the generation those were derived from" in excinfo.value.reasons[0]


def test_a_composed_parent_refuses_rather_than_regenerating_thinner(
    tmp_path: Path, workspace_root: Path
):
    """Refuter finding F7 (2026-08-15), narrowed by unit #69's refuter finding
    F1 (2026-08-26): a plain regeneration re-runs the pipeline with
    side_inputs=None, so regenerating a COMPOSED parent through THIS door
    would mint an artifact silently lacking the demand/signals/fleet the
    parent carried -- indistinguishable from a job that never had them. Unit
    #69 decided HALF of "how side inputs travel through a regeneration"
    (composing a FIRST layer onto a BARE source, through its own door); a
    SECOND layer onto an already-composed parent stays undecided and this
    path still refuses by name -- the message just has to say that
    accurately now, not claim the whole question is unanswered."""
    parent = _pre_fix_parent(tmp_path)
    genesis_jobs._update(parent["id"], side_inputs={"demand": "tok_demand", "signals": "tok_sig"})
    before = set(genesis_jobs.genesis_dir().joinpath("jobs").iterdir())

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_regenerate_job(parent["id"], requested_by="reviewer")
    assert excinfo.value.code == "genesis_regenerate_parent_has_side_inputs"
    assert "demand" in excinfo.value.reasons[0]
    assert "signals" in excinfo.value.reasons[0]
    # The rewritten sentence no longer claims the WHOLE question is
    # undecided -- unit #69 answered the bare-source half -- and names the
    # SECOND-layer question that is still open, plus the honest workaround.
    assert "BARE source" in excinfo.value.reasons[0]
    assert "REPLACE" in excinfo.value.reasons[0] and "MERGED" in excinfo.value.reasons[0]
    assert "regenerate-with-side-inputs" in excinfo.value.reasons[0]
    # This parent was generated directly (no lineage), so the workaround
    # falls to the generic "start fresh" branch, not a named ancestor job.
    assert "start from (or regenerate) a generation that carries no side inputs" in excinfo.value.reasons[0]
    assert set(genesis_jobs.genesis_dir().joinpath("jobs").iterdir()) == before


def test_a_source_that_no_longer_matches_its_recorded_digest_is_refused(
    tmp_path: Path, workspace_root: Path
):
    """The lineage claims "the same source". So the claim is VERIFIED rather
    than assumed -- a file edited under a job would make the regenerated
    artifact a comparison against something that no longer exists."""
    parent = _pre_fix_parent(tmp_path)
    stored = genesis_jobs.artifact_path(parent["id"], "source_osm")
    stored.write_text(_clean_source(), encoding="utf-8", newline="")

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_regenerate_job(parent["id"], requested_by="reviewer")
    assert excinfo.value.code == "genesis_regenerate_source_sha_mismatch"


def test_a_parent_with_nothing_to_regenerate_is_refused_by_name(
    tmp_path: Path, workspace_root: Path
):
    """The refusal that keeps this from being a re-roll button: a clean source
    gates without the finding, so there is nothing a re-derivation would
    change, and the refusal says which path the other findings have."""
    clean = _osm_file(tmp_path, "clean.osm", _clean_source())
    parent = _generate(clean, name="clean")
    assert parent["status"] in genesis_jobs.GATED_STATUSES
    assert _v7_count(parent) == 0

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_regenerate_job(parent["id"], requested_by="reviewer")
    assert excinfo.value.code == "genesis_regenerate_not_eligible"
    assert "POST .../fixes/apply" in excinfo.value.reasons[0]


def test_a_refusal_creates_nothing_on_disk(tmp_path: Path, workspace_root: Path):
    """A refused regeneration must not leave a half-built job behind -- the
    same discipline unit #33's eligibility refusals follow."""
    before = sorted(p.name for p in genesis_jobs.jobs_root().glob("*"))
    parent = _seed_job(_island_network(), name="ungated")
    with pytest.raises(genesis_jobs.GenesisRefused):
        genesis_jobs.create_regenerate_job(parent["id"], requested_by="reviewer")
    after = sorted(p.name for p in genesis_jobs.jobs_root().glob("*"))
    assert after == sorted(before + [parent["id"]])


# ---------------------------------------------------------------------------
# §3 The lineage is a claim that was checked
# ---------------------------------------------------------------------------


def test_the_regenerated_job_carries_a_verified_lineage(
    tmp_path: Path, workspace_root: Path
):
    parent = _pre_fix_parent(tmp_path)
    parent_source = genesis_jobs.artifact_path(parent["id"], "source_osm")
    parent_bytes = parent_source.read_bytes()

    child = genesis_jobs.create_regenerate_job(
        parent["id"], requested_by="reviewer", reason="the way was declared twice"
    )

    derived = child["derived_from"]
    assert derived["kind"] == genesis_jobs.REGENERATE_LINEAGE_KIND
    assert derived["job_id"] == parent["id"]
    assert derived["requested_by"] == "reviewer"
    assert derived["reason"] == "the way was declared twice"
    # The digest of the bytes ACTUALLY copied, equal to the parent's recorded one.
    assert derived["source_sha256"] == derived["parent_source_sha256"]
    assert derived["source_sha256"] == hashlib.sha256(parent_bytes).hexdigest()
    assert [fix["id"] for fix in derived["converter_fixes"]] == [WAY_REDECLARATION_FIX_ID]
    assert [f["code"] for f in derived["findings"]] == [_CODE] * len(derived["findings"])
    assert derived["findings"], "the lineage must name WHICH findings earned the regeneration"
    assert all(f["derivation_reason_code"] == "draft_shares_the_corruption" for f in derived["findings"])

    # The copied source is the parent's own bytes, and the child's own options
    # are the parent's -- a regeneration that changed the input would be
    # comparing two different questions.
    child_source = genesis_jobs.job_dir(child["id"]) / child["source_osm"]["stored_as"]
    assert child_source.read_bytes() == parent_bytes
    assert child["options"] == parent["options"]


# ---------------------------------------------------------------------------
# §4 The parent is byte-untouched
# ---------------------------------------------------------------------------


def test_the_parent_directory_is_byte_identical_after_a_regeneration(
    tmp_path: Path, workspace_root: Path
):
    """B5-2's rule for every sibling job, measured file by file: the parent is
    READ, never written."""
    parent = _pre_fix_parent(tmp_path)
    parent_dir = genesis_jobs.job_dir(parent["id"])
    before = _dir_fingerprint(parent_dir)
    parent_record = genesis_jobs.get_job(parent["id"])

    child = genesis_jobs.create_regenerate_job(parent["id"], requested_by="reviewer")
    genesis_jobs.run_genesis_job(child["id"], child["workspace_id"])

    assert _dir_fingerprint(parent_dir) == before
    assert genesis_jobs.get_job(parent["id"]) == parent_record


# ---------------------------------------------------------------------------
# §5 The regenerated job really does clear the class
# ---------------------------------------------------------------------------


def test_the_regenerated_job_gates_with_no_shared_polyline_finding(
    tmp_path: Path, workspace_root: Path
):
    """The measurement this whole unit exists for, on one synthetic source:
    the parent (built by the pre-fix converter from these bytes) carries the
    class, and the regeneration -- same bytes, same gate -- carries none.

    Read-only on the four REAL refused jobs, the same before/after was
    62/27/5/62 → 0/0/0/0.
    """
    parent = _pre_fix_parent(tmp_path)
    assert _v7_count(parent) > 0

    child = genesis_jobs.create_regenerate_job(parent["id"], requested_by="reviewer")
    finished = genesis_jobs.run_genesis_job(child["id"], child["workspace_id"])

    assert finished["status"] in genesis_jobs.GATED_STATUSES, finished
    assert _v7_count(finished) == 0
    # The regenerated artifact is a DIFFERENT network, not a copy of the
    # parent's: fewer links, because the repeat no longer doubled them.
    assert finished["network"]["links"] < parent["network"]["links"]
    # And it gated on its OWN merits -- its own verdict, not the parent's.
    assert finished["gate"]["network_sha256"] != parent["gate"]["network_sha256"]


def test_the_regenerated_job_is_not_itself_regenerable(
    tmp_path: Path, workspace_root: Path
):
    """The cure does not carry the disease: a job the corrected converter
    produced has nothing left for this path to treat, and asking again is
    refused by the same named eligibility rule."""
    parent = _pre_fix_parent(tmp_path)
    child = genesis_jobs.create_regenerate_job(parent["id"], requested_by="reviewer")
    genesis_jobs.run_genesis_job(child["id"], child["workspace_id"])

    assert genesis_jobs.regenerable_findings(child["id"]) == []
    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_regenerate_job(child["id"], requested_by="reviewer")
    assert excinfo.value.code == "genesis_regenerate_not_eligible"


# ---------------------------------------------------------------------------
# §6 The HTTP surface
# ---------------------------------------------------------------------------


def test_the_route_enqueues_and_answers_202(
    tmp_path: Path, client: TestClient
):
    parent = _pre_fix_parent(tmp_path)

    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/regenerate",
        json={"reason": "one way id, two declarations"},
        headers=admin_headers(),
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["id"] != parent["id"]
    assert body["derived_from"]["kind"] == genesis_jobs.REGENERATE_LINEAGE_KIND
    assert body["derived_from"]["job_id"] == parent["id"]
    # WHO is stamped server-side from the session, never from the body.
    assert body["derived_from"]["requested_by"] == "suite-admin"
    assert body["submitted_by"] == "suite-admin"

    # Drain the daemon thread before the workspace fixture tears the root down --
    # the same discipline unit #33's own 202 test follows.
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        status = client.get(f"/api/genesis/jobs/{body['id']}").json()["status"]
        if status in genesis_jobs.TERMINAL_STATUSES:
            break
        time.sleep(0.02)
    else:  # pragma: no cover - a wedged runner is the failure, not a flake
        pytest.fail("the regenerated job did not reach a terminal status in time")
    assert status in genesis_jobs.GATED_STATUSES


def test_the_route_cannot_be_asked_to_change_the_input(
    tmp_path: Path, client: TestClient
):
    """``extra="forbid"``: every field a caller could add here would be a way to
    change the input while calling the result a treatment."""
    parent = _pre_fix_parent(tmp_path)
    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/regenerate",
        json={"reason": "ok", "exclude_service": True},
        headers=admin_headers(),
    )
    assert response.status_code == 422, response.text


def test_an_ineligible_parent_answers_409_with_the_named_code(
    workspace_root: Path, client: TestClient
):
    parent = _seed_job(_island_network(), name="ungated")
    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/regenerate", headers=admin_headers()
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "genesis_regenerate_parent_not_gated"
    assert response.json()["detail"]["reasons"]


def test_an_unknown_job_is_404(workspace_root: Path, client: TestClient):
    response = client.post(
        "/api/genesis/jobs/abcdef123456/regenerate", headers=admin_headers()
    )
    assert response.status_code == 404


def test_anonymous_callers_cannot_start_a_regeneration(workspace_root: Path, client: TestClient):
    assert client.post("/api/genesis/jobs/abcdef123456/regenerate").status_code == 401


def test_the_request_handler_only_enqueues(workspace_root: Path):
    """The 202 is the point: this route mints and starts a job and does no
    generation work of its own, exactly like ``POST /jobs``."""
    assert "genesis_jobs.start_regenerate_job(" in ROUTER_SOURCE

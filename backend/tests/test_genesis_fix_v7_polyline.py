"""Unit #34 — V7 gets its key: ``corrected_points_xy`` derived from the
DRAFT's OWN record (``genesis_jobs.derive_v7_corrected_polyline``), so the
ALREADY-REGISTERED ``set_link_geometry`` disposition for
``v7_links_share_one_polyline`` (and its V7 siblings) comes alive.

The doctrine under test: the correction is the draft's own pre-corruption
polyline for the finding's OWN link — never the other twin's polyline, never
interpolated, never the approver's. Three honest "nothing" cases must KEEP
refusing ``NO_TOOL_WITHOUT_FABRICATION``, now with the measured reason:

* the draft's records for the twins are THEMSELVES identical (the corruption
  happened upstream of the draft — what every real refused Daejeon job
  measured as of 2026-08-14, 164/164 findings across 4 jobs);
* the draft's record equals the polyline the artifact already carries (a
  no-op dressed as a fix);
* the approver submits coordinates that differ from the derivation
  (``genesis_fix_points_not_census_derived`` — fabrication wearing an
  approval).

Fixture shape follows ``tests/test_genesis_fix_jobs.py`` exactly: a private
tmp workspace per test, hand-written parent networks through ``_seed_job`` +
the REAL gate, no COM anywhere (nothing in this file ever RUNS a fix job —
the unit under test is the create-time evidence re-derivation, the same
preflight step B5-2 established). The twins double as V3 islands, so each
parent is genuinely REFUSED by the real gate without extra scaffolding.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import main
from conftest import admin_headers
from services import genesis_jobs, workspaces
from services.agent import fixes as agent_fixes
from services.agent import genesis_fixes

from tests.test_genesis_gate import (
    _DEMAND_SUPPORT as demand_support,
)
from tests.test_genesis_gate import _SUPPORT as gate_support
from tests.test_genesis_gate import _demand as demand_layer
from tests.test_genesis_gate import link, network, straight_pair
from tests.test_genesis_jobs import _seed_job

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Same shape as ``test_genesis_fix_jobs.py``'s fixture of the same name —
    duplicated rather than imported across test modules (the repo's existing
    convention only cross-imports plain helper FUNCTIONS)."""
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
# Network + census fixtures
# ---------------------------------------------------------------------------

_CODE = "v7_links_share_one_polyline"
_VERB = "set_link_geometry"

#: What the EMITTED network carries for both twins — the corrupted state.
_SHARED = [(0.0, 303.0), (100.0, 303.0)]
#: The draft's own (distinct) records — link 7's is the derivable correction.
_DRAFT_7 = [[0.0, 300.0], [100.0, 300.0]]
_DRAFT_8 = [[0.0, 306.0], [100.0, 306.0]]


def _twin_network(*, second_group: bool = False) -> str:
    """``straight_pair`` (drivable, so the gate's demand checks stay quiet)
    plus twins 7/8 written on the SAME polyline — V7's shared-polyline finding
    AND V3's island refusal in one pair of links. ``second_group`` adds twins
    11/12 on a second shared polyline, so the SAME code appears twice (unit
    #30's ambiguity shape)."""
    links = list(straight_pair()) + [link(7, _SHARED), link(8, _SHARED)]
    if second_group:
        shared_2 = [(0.0, 313.0), (100.0, 313.0)]
        links += [link(11, shared_2), link(12, shared_2)]
    return network(
        links=links,
        extra=demand_layer(),
        support=gate_support + demand_support,
    )


def _draft_link(link_id: int, points: list[list[float]]) -> dict[str, Any]:
    """One VALID ``DraftLink`` record — every required field, minimal values."""
    return {
        "id": link_id,
        "name": f"link {link_id}",
        "osm_way_id": 9000 + link_id,
        "segment_index": 0,
        "forward": True,
        "lanes": 1,
        "lane_width_m": 3.5,
        "points": points,
        "lane_provenance": {},
        "highway": "residential",
        "from_node_osm_id": 1,
        "to_node_osm_id": 2,
        "side_offset_m": 0.0,
    }


def _census(link_entries: list[dict[str, Any]]) -> dict[str, Any]:
    """A census that VALIDATES as a real ``DraftNetwork`` (unlike
    ``_seed_job``'s deliberately-minimal default), so ``job_draft_or_none``
    parses it and the deriver can read ``links[].points``."""
    return {
        "name": "seeded",
        "source_osm": "seed.osm",
        "metadata": {"connectivity": {"dangling_approaches": []}},
        "links": link_entries,
    }


def _seed_v7_refused_parent(
    census_links: list[dict[str, Any]], *, second_group: bool = False, name: str = "v7_parent"
) -> dict[str, Any]:
    record = _seed_job(
        _twin_network(second_group=second_group), census=_census(census_links), name=name
    )
    result = genesis_jobs.gate_and_publish(record["id"])
    assert result["status"] == genesis_jobs.STATUS_REFUSED, result
    return result


def _v7_entries(client: TestClient, job_id: str) -> list[dict[str, Any]]:
    listed = client.get(f"/api/genesis/jobs/{job_id}/fixes", headers=admin_headers())
    assert listed.status_code == 200, listed.text
    return [e for e in listed.json()["findings"] if e["code"] == _CODE]


# ---------------------------------------------------------------------------
# §0 The code tuple mirrors the registry (drift protection for the deriver)
# ---------------------------------------------------------------------------


def test_v7_code_tuple_mirrors_the_fix_registry():
    """Every code the deriver answers for is a registered AUTOMATABLE
    ``set_link_geometry`` mapping, and every registered v7 code is one the
    deriver answers for — a new v7 code cannot silently fall between."""
    for code in genesis_jobs.V7_GEOMETRY_FINDING_CODES:
        mapping = genesis_fixes.get_genesis_fix(code)
        assert mapping is not None, code
        assert mapping.status == agent_fixes.AUTOMATABLE, code
        assert mapping.verb == _VERB, code
    registered_v7 = {
        mapping.code
        for mapping in genesis_fixes.get_genesis_fix_registry()
        if mapping.code.startswith("v7_")
    }
    assert registered_v7 == set(genesis_jobs.V7_GEOMETRY_FINDING_CODES)


# ---------------------------------------------------------------------------
# §1 The correctable case: the draft's own record flows end to end
# ---------------------------------------------------------------------------


def test_derives_the_drafts_own_polyline_and_the_fix_job_carries_its_provenance(
    workspace_root: Path, client: TestClient
):
    parent = _seed_v7_refused_parent([_draft_link(7, _DRAFT_7), _draft_link(8, _DRAFT_8)])

    # The deriver itself: link 7's OWN draft record, verbatim, with provenance.
    draft = genesis_jobs.job_draft_or_none(parent["id"])
    finding = {"code": _CODE, "subject": "7 (link 7)", "context": {"count": 2, "links": ["7", "8"]}}
    derivation = genesis_jobs.derive_v7_corrected_polyline(
        _CODE, finding, draft=draft, job_id=parent["id"]
    )
    assert derivation["corrected_points_xy"] == _DRAFT_7
    assert derivation["provenance"]["source_field"] == "links[].points"
    assert derivation["provenance"]["link_id"] == "7"

    # The route's real rendering: a full PROPOSAL carrying the draft's points.
    (entry,) = _v7_entries(client, parent["id"])
    disposition = entry["disposition"]
    assert disposition["status"] == agent_fixes.AUTOMATABLE
    assert disposition["verb"] == _VERB
    assert disposition["params"] == {"link_id": "7", "points_xy": _DRAFT_7}
    assert disposition["requires"] == []

    # Approving EXACTLY the rendered params passes create's re-derivation, and
    # the minted job's lineage carries both the points and their provenance.
    record = genesis_jobs.create_genesis_fix_job(
        parent["id"],
        finding_code=_CODE,
        subject=None,
        verb=_VERB,
        params=dict(disposition["params"]),
        approved_by="reviewer",
        approval_rationale="the draft's own record, unmodified",
    )
    derived = record["derived_from"]
    assert derived["subject"] == "7 (link 7)"
    assert derived["params"]["points_xy"] == _DRAFT_7
    assert derived["census_derivation"]["source_artifact"] == "genesis_json"
    assert derived["census_derivation"]["source_field"] == "links[].points"
    assert derived["census_derivation"]["link_id"] == "7"
    # And a non-derived fix keeps the key ABSENT (presence is the fact) — the
    # v6 family's own tests pin that side; here we only pin the round trip.
    reread = genesis_jobs.get_job(record["id"])
    assert reread["derived_from"]["census_derivation"] == derived["census_derivation"]


# ---------------------------------------------------------------------------
# §2 Uncorrectable: the draft itself shares the corruption (the real jobs'
# measured case) — the named refusal stands, with the measured reason
# ---------------------------------------------------------------------------


def test_upstream_identical_draft_twins_keep_the_honest_refusal(
    workspace_root: Path, client: TestClient
):
    shared_as_lists = [list(p) for p in _SHARED]
    parent = _seed_v7_refused_parent(
        [_draft_link(7, shared_as_lists), _draft_link(8, shared_as_lists)]
    )

    draft = genesis_jobs.job_draft_or_none(parent["id"])
    finding = {"code": _CODE, "subject": "7 (link 7)", "context": {"count": 2, "links": ["7", "8"]}}
    derivation = genesis_jobs.derive_v7_corrected_polyline(
        _CODE, finding, draft=draft, job_id=parent["id"]
    )
    assert derivation["corrected_points_xy"] is None
    assert derivation["provenance"]["reason_code"] == "draft_shares_the_corruption"

    (entry,) = _v7_entries(client, parent["id"])
    disposition = entry["disposition"]
    assert disposition["params"] is None
    assert disposition["reason_code"] == genesis_fixes.NO_TOOL_WITHOUT_FABRICATION
    assert "upstream of the draft" in disposition["rationale"]["en"]

    # An approval cannot ride through the refusal, however well-formed.
    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_CODE,
            subject=None,
            verb=_VERB,
            params={"link_id": "7", "points_xy": _DRAFT_7},
            approved_by="reviewer",
            approval_rationale="trying anyway",
        )
    assert excinfo.value.code == "genesis_fix_measurement_gate_not_satisfied"


# ---------------------------------------------------------------------------
# §3 Uncorrectable: the draft's record IS the corrupted state (no-op)
# ---------------------------------------------------------------------------


def test_a_correction_equal_to_the_shared_polyline_refuses_as_a_noop(
    workspace_root: Path, client: TestClient
):
    shared_as_lists = [list(p) for p in _SHARED]
    # Twins DISTINCT in the draft (so the upstream rule does not fire), but
    # the subject's own record equals what the artifact already carries.
    parent = _seed_v7_refused_parent(
        [_draft_link(7, shared_as_lists), _draft_link(8, _DRAFT_8)]
    )

    draft = genesis_jobs.job_draft_or_none(parent["id"])
    finding = {"code": _CODE, "subject": "7 (link 7)", "context": {"count": 2, "links": ["7", "8"]}}
    derivation = genesis_jobs.derive_v7_corrected_polyline(
        _CODE, finding, draft=draft, job_id=parent["id"]
    )
    assert derivation["corrected_points_xy"] is None
    assert derivation["provenance"]["reason_code"] == "draft_equals_emitted"

    (entry,) = _v7_entries(client, parent["id"])
    assert entry["disposition"]["params"] is None
    assert entry["disposition"]["reason_code"] == genesis_fixes.NO_TOOL_WITHOUT_FABRICATION

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_CODE,
            subject=None,
            verb=_VERB,
            params={"link_id": "7", "points_xy": shared_as_lists},
            approved_by="reviewer",
            approval_rationale="re-applying the corrupted state",
        )
    assert excinfo.value.code == "genesis_fix_measurement_gate_not_satisfied"


# ---------------------------------------------------------------------------
# §4 Subject addressing rides unit #30's machinery unchanged (regression)
# ---------------------------------------------------------------------------


def test_second_v7_finding_is_addressable_by_subject(workspace_root: Path):
    draft_11 = [[0.0, 310.0], [100.0, 310.0]]
    parent = _seed_v7_refused_parent(
        [
            _draft_link(7, _DRAFT_7),
            _draft_link(8, _DRAFT_8),
            _draft_link(11, draft_11),
            _draft_link(12, [[0.0, 316.0], [100.0, 316.0]]),
        ],
        second_group=True,
    )

    # Code-only create refuses by name — two subjects carry this code.
    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_CODE,
            subject=None,
            verb=_VERB,
            params={"link_id": "11", "points_xy": draft_11},
            approved_by="reviewer",
            approval_rationale="no subject named",
        )
    assert excinfo.value.code == "genesis_fix_finding_ambiguous_without_subject"

    # Naming the SECOND subject resolves ITS finding and derives ITS record.
    record = genesis_jobs.create_genesis_fix_job(
        parent["id"],
        finding_code=_CODE,
        subject="11 (link 11)",
        verb=_VERB,
        params={"link_id": "11", "points_xy": draft_11},
        approved_by="reviewer",
        approval_rationale="the second group's own record",
    )
    derived = record["derived_from"]
    assert derived["subject"] == "11 (link 11)"
    assert derived["params"]["points_xy"] == draft_11
    assert derived["census_derivation"]["link_id"] == "11"


# ---------------------------------------------------------------------------
# §5 The B5-2 exploit guards still fire for V7 — plus unit #34's own guard
# ---------------------------------------------------------------------------


def test_params_addressing_and_fabricated_points_both_refuse(workspace_root: Path):
    parent = _seed_v7_refused_parent([_draft_link(7, _DRAFT_7), _draft_link(8, _DRAFT_8)])

    # Exploit #2's pattern: params addressing the TWIN under the subject's
    # finding — refused by the expected_fix_target cross-check.
    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_CODE,
            subject=None,
            verb=_VERB,
            params={"link_id": "8", "points_xy": _DRAFT_7},
            approved_by="reviewer",
            approval_rationale="aimed at the twin",
        )
    assert excinfo.value.code == "genesis_fix_params_target_mismatch"

    # Unit #34's own guard: right link, INVENTED coordinates — the derivation
    # succeeded, but the approval must carry exactly the derived points.
    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_genesis_fix_job(
            parent["id"],
            finding_code=_CODE,
            subject=None,
            verb=_VERB,
            params={"link_id": "7", "points_xy": [[0.0, 999.0], [100.0, 999.0]]},
            approved_by="reviewer",
            approval_rationale="my own coordinates",
        )
    assert excinfo.value.code == "genesis_fix_points_not_census_derived"

    # And nothing above minted a job: the parent has no siblings.
    siblings = [
        job
        for job in genesis_jobs.list_jobs()
        if (job.get("derived_from") or {}).get("job_id") == parent["id"]
    ]
    assert siblings == []


# ---------------------------------------------------------------------------
# §6 A missing/invalid census stays an honest "nothing" (the pre-#34 default)
# ---------------------------------------------------------------------------


def test_a_census_that_does_not_validate_yields_nothing(workspace_root: Path):
    """``_seed_job``'s DEFAULT census is deliberately not a valid
    ``DraftNetwork`` — exactly the hand-seeded shape ``job_draft_or_none``
    reads as None. The deriver must answer ``no_draft_census``, not crash."""
    record = _seed_job(_twin_network(), name="minimal_census")
    parent = genesis_jobs.gate_and_publish(record["id"])
    assert parent["status"] == genesis_jobs.STATUS_REFUSED

    finding = {"code": _CODE, "subject": "7 (link 7)", "context": {"count": 2, "links": ["7", "8"]}}
    derivation = genesis_jobs.derive_v7_corrected_polyline(
        _CODE, finding, draft=genesis_jobs.job_draft_or_none(parent["id"]), job_id=parent["id"]
    )
    assert derivation["corrected_points_xy"] is None
    assert derivation["provenance"]["reason_code"] == "no_draft_census"

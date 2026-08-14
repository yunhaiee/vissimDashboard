"""Unit #32 -- ``tools/make_b52_demo_job.py``: manufacture the ONE genesis
job the live demo is missing (a REFUSED job whose refusal names an
AUTOMATABLE, ask-first fix) against REAL workspace machinery, offline.

Every test drives the same public entry point (:func:`run_demo_job`) the CLI
uses, against a private ``tmp_path`` workspace root (the same
``monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", ...)`` shape
``test_genesis_fix_jobs.py``/``test_genesis_fix_routes.py`` use) -- no COM, no
live server, no network access. The demo network the tool builds from is the
repo's own ``client-data/데모-교차로/demo-crossroads.osm``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services import genesis_jobs, workspaces
from tools import make_b52_demo_job as tool


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private workspaces root for one test, same shape as
    ``test_genesis_fix_jobs.py``'s own fixture of the same name."""
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "legacy-uploads")
    return tmp_path / "workspaces"


@pytest.fixture()
def demo_workspace_id(workspace_root: Path) -> str:
    record = workspaces.ensure_default_workspace()
    return record["id"]


def _job_dir_ids(workspace_id: str) -> set[str]:
    with workspaces.bind_workspace(workspace_id):
        root = genesis_jobs.jobs_root()
        if not root.is_dir():
            return set()
        return {p.name for p in root.iterdir() if p.is_dir()}


def _stored_finding_codes(workspace_id: str, job_id: str) -> set[str]:
    with workspaces.bind_workspace(workspace_id):
        gate_path = genesis_jobs.job_dir(job_id) / genesis_jobs.GATE_JSON_FILENAME
        raw = json.loads(gate_path.read_text(encoding="utf-8"))
    return {
        finding["code"]
        for check in raw.get("checks", [])
        for origin in ("findings", "warnings")
        for finding in check.get(origin, [])
    }


# ===========================================================================
# The honest pipeline reaches (a)-(d)
# ===========================================================================


def test_creates_a_refused_job_satisfying_a_through_d(demo_workspace_id: str) -> None:
    result = tool.run_demo_job(demo_workspace_id)

    assert result["reused"] is False
    record = result["record"]

    # (a) status refused.
    assert record["status"] == genesis_jobs.STATUS_REFUSED
    assert record["name"].startswith(tool.NAME_PREFIX)

    # (b) stored gate.json contains v6_input_volume_is_zero.
    codes = _stored_finding_codes(demo_workspace_id, record["id"])
    assert tool.TARGET_FINDING_CODE in codes

    # (c) AUTOMATABLE, ask-first, NOT a structural refusal.
    disposition = result["report"]["disposition"]
    assert disposition["status"] == "AUTOMATABLE"
    assert disposition["verb"] == tool.TARGET_VERB
    assert disposition["requires"] == ["volume"]
    assert disposition["reason_code"] != "INPUT_VOLUME_IS_OBSERVED_DATA"

    # (d) create_genesis_fix_job's own preflight would accept -- and no fix
    # job was actually minted (this parent's derived_from must be absent, and
    # no sibling job directory exists beyond the parent itself).
    assert result["report"]["preflight_proposal_verb"] == tool.TARGET_VERB
    assert "derived_from" not in record
    assert _job_dir_ids(demo_workspace_id) == {record["id"]}


def test_the_target_link_carries_no_observation_coverage(demo_workspace_id: str) -> None:
    """The demo's own doctrine, asserted directly: an entrance that WAS
    measured would flip the card to a structural refusal, so the demo only
    means what it claims to mean if nothing in this workspace covers it."""
    result = tool.run_demo_job(demo_workspace_id)
    link_id = result["report"]["target_link_id"]
    with workspaces.bind_workspace(demo_workspace_id):
        assert genesis_jobs.derive_v6_node_evidence(str(link_id)) is None


# ===========================================================================
# Idempotence
# ===========================================================================


def test_a_second_call_reuses_the_first_job_rather_than_creating_another(
    demo_workspace_id: str,
) -> None:
    first = tool.run_demo_job(demo_workspace_id)
    second = tool.run_demo_job(demo_workspace_id)

    assert second["reused"] is True
    assert second["record"]["id"] == first["record"]["id"]
    assert _job_dir_ids(demo_workspace_id) == {first["record"]["id"]}


def test_force_new_creates_a_second_job_alongside_the_first(demo_workspace_id: str) -> None:
    first = tool.run_demo_job(demo_workspace_id)
    second = tool.run_demo_job(demo_workspace_id, force_new=True)

    assert second["reused"] is False
    assert second["record"]["id"] != first["record"]["id"]
    assert second["record"]["name"].startswith(tool.NAME_PREFIX)
    assert _job_dir_ids(demo_workspace_id) == {first["record"]["id"], second["record"]["id"]}


def test_idempotence_scan_ignores_a_demo_job_with_a_different_status(
    demo_workspace_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``DEMO-수정루프-*`` job that is not itself REFUSED (e.g. one an
    operator is mid-editing, or a fix-job sibling that published) must not
    short-circuit the scan -- only a genuinely satisfying candidate may."""
    first = tool.run_demo_job(demo_workspace_id)
    with workspaces.bind_workspace(demo_workspace_id):
        record = genesis_jobs.get_job(first["record"]["id"])
        record["status"] = genesis_jobs.STATUS_PROVISIONAL
        genesis_jobs._upsert(record)  # noqa: SLF001 - test-only direct mutation

    second = tool.run_demo_job(demo_workspace_id)
    assert second["reused"] is False
    assert second["record"]["id"] != first["record"]["id"]


# ===========================================================================
# Named refusals -- none of them may leave a job directory behind
# ===========================================================================


def test_refuses_without_a_workspace_and_names_the_available_ids(
    demo_workspace_id: str,
) -> None:
    with pytest.raises(tool.DemoJobRefused, match="workspace"):
        tool.run_demo_job(None)
    with pytest.raises(tool.DemoJobRefused, match=demo_workspace_id):
        tool.run_demo_job("")


def test_refuses_an_unknown_workspace_id(demo_workspace_id: str) -> None:
    before = _job_dir_ids(demo_workspace_id)
    with pytest.raises(tool.DemoJobRefused, match="Unknown workspace"):
        tool.run_demo_job("not-a-real-workspace-id")
    assert _job_dir_ids(demo_workspace_id) == before


def test_refuses_a_name_without_the_demo_marker(demo_workspace_id: str) -> None:
    before = _job_dir_ids(demo_workspace_id)
    with pytest.raises(tool.DemoJobRefused, match=tool.NAME_PREFIX):
        tool.run_demo_job(demo_workspace_id, name="not-a-demo-name")
    assert _job_dir_ids(demo_workspace_id) == before


def _fake_measured_evidence(link_id: str) -> dict[str, Any]:
    """The SAME fixture technique ``test_genesis_fix_jobs.py``'s own
    ``_fake_v6_node_evidence`` uses -- real ``services.observations`` data
    cannot key an observed row by link id today (a named, pre-existing gap;
    see ``genesis_jobs.derive_v6_node_evidence``'s own docstring), so the
    REFUSAL WIRING downstream of a measured entrance is proven by
    monkeypatching that one function directly, exactly as the app's own
    fix-job suite already does for this same gap."""
    return {
        "node_id": "8",
        "node_name": "측정된 교차로",
        "total_real": 649,
        "total_sim_basam": None,
        "count_basis": "turning_counts",
        "directions": [{"direction": "8-1-100.0-1-150.0", "real": 649, "sim_basam": 0}],
    }


def test_refuses_by_name_when_the_target_link_is_measured_rather_than_forging_the_demo(
    demo_workspace_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one negative case the spec calls out by name: a workspace whose
    observations cover the demo's chosen entrance must make the script
    refuse loudly, never silently ship a structural-refusal card dressed up
    as the ask-first one."""
    monkeypatch.setattr(genesis_jobs, "derive_v6_node_evidence", _fake_measured_evidence)
    before = _job_dir_ids(demo_workspace_id)

    with pytest.raises(tool.DemoJobRefused, match="measured"):
        tool.run_demo_job(demo_workspace_id)

    after = _job_dir_ids(demo_workspace_id)
    assert after == before, "a measured-entrance refusal must not leave a job directory behind"


def test_idempotence_scan_skips_a_stale_candidate_that_has_gone_measured(
    demo_workspace_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A previously-built demo job that would now show as measured (e.g. an
    observation set ingested afterwards) must be skipped by the scan rather
    than reused -- and the scan must fall through to building a fresh one
    instead of reusing a job whose card would now lie."""
    first = tool.run_demo_job(demo_workspace_id)
    first_link = first["report"]["target_link_id"]

    def _fake(link_id: str) -> dict[str, Any] | None:
        if str(link_id) == str(first_link):
            return _fake_measured_evidence(link_id)
        return None

    monkeypatch.setattr(genesis_jobs, "derive_v6_node_evidence", _fake)

    with pytest.raises(tool.DemoJobRefused, match="measured"):
        tool.run_demo_job(demo_workspace_id)

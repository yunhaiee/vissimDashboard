"""Unit #69 — regenerating WITH a newly staged side input.

The CTA this backs: ``genesis/phases`` builds a signals side-input token from
a confirmed phase diagram, bound to an EXISTING generation job that already
has a verdict (with no side inputs of its own -- that is the whole reason the
CTA exists). "이 signals 입력으로 재생성" starts a fresh derivation of that
job's own verified source, this time composed with the signals layer.

Measured first (see the worktree report): unit #38's ``POST .../regenerate``
cannot serve this call shape. Its request model forbids every field but
``reason`` (``extra="forbid"``), its function copies the parent's OWN options
with ``side_inputs: None`` always, and its eligibility gate
(``genesis_regenerate_not_eligible``) requires an unrelated
``REGENERATE_WITH_FIX`` finding that an ordinarily-gated job simply will not
carry. ``POST /jobs/{id}/regenerate-with-side-inputs`` is the sibling this
unit adds instead: same source-verification doors
(``_regeneration_source_preflight``, unit #38's own four codes, reused
verbatim), a DIFFERENT lineage kind
(``genesis_jobs.SIGNALS_REGENERATION_LINEAGE_KIND``), and the exact
``create_genesis_job`` compose-a-side-input body ``POST /jobs`` already uses
for a fresh upload -- so composing demand/signals here is not a second,
un-audited implementation of that behaviour.

The doctrine each test guards:

* §1 the source doors are unit #38's OWN, reused rather than re-implemented
  (not_gated / source_missing / sha_mismatch / has_side_inputs);
* §2 the one NEW door: neither token named refuses by name, rather than
  quietly doing nothing a caller could mistake for unit #38's plain route;
* §3 the new job's lineage names a DIFFERENT kind than a plain regeneration,
  and carries the parent id + the verified digest;
* §4 the parent is byte-untouched, the same B5-2 rule every sibling obeys;
* §5 the HTTP surface: 202, 404, 409-by-code, and the EXACT shape the
  ``genesis/phases`` CTA sends (``{"signals_token": "..."}``, nothing else)
  is accepted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from conftest import admin_headers
from services import genesis_jobs, workspaces

from tests.test_genesis_job_layers import (
    _await_terminal,
    _stage,
    demand_document,
    signals_document,
    write_document,
)
from tests.test_genesis_jobs import _generate
from tests.test_genesis_osm_draft import crossroads_osm


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


def _plain_parent(tmp_path: Path, workspace_root: Path, *, name: str = "plain") -> dict:
    """A real, gated, geometry-only generation -- exactly the shape the
    phases-page CTA's ``jobId`` is in the ordinary case: already verdicted,
    no side inputs of its own yet."""
    osm_path = tmp_path / f"{name}.osm"
    osm_path.write_text(crossroads_osm(), encoding="utf-8", newline="")
    parent = _generate(osm_path, name=name)
    assert parent["status"] in genesis_jobs.GATED_STATUSES, parent
    assert parent["side_inputs"] is None
    return parent


# ---------------------------------------------------------------------------
# §1 The source doors are unit #38's own, reused
# ---------------------------------------------------------------------------


def test_an_ungated_parent_is_refused_by_name(tmp_path: Path, workspace_root: Path):
    osm_path = tmp_path / "ungated.osm"
    osm_path.write_text(crossroads_osm(), encoding="utf-8", newline="")
    record = genesis_jobs.create_genesis_job(osm_path=osm_path, name="ungated")

    signals_path = write_document(tmp_path, "signals.json", signals_document())
    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_signals_regeneration_job(
            record["id"], signals_input=signals_path, requested_by="reviewer"
        )
    assert excinfo.value.code == "genesis_regenerate_parent_not_gated"


def test_a_parent_with_no_source_of_its_own_is_refused_by_name(
    tmp_path: Path, workspace_root: Path
):
    parent = _plain_parent(tmp_path, workspace_root)
    genesis_jobs._update(parent["id"], source_osm=None)

    signals_path = write_document(tmp_path, "signals.json", signals_document())
    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_signals_regeneration_job(
            parent["id"], signals_input=signals_path, requested_by="reviewer"
        )
    assert excinfo.value.code == "genesis_regenerate_source_missing"


def test_a_source_that_no_longer_matches_its_recorded_digest_is_refused(
    tmp_path: Path, workspace_root: Path
):
    parent = _plain_parent(tmp_path, workspace_root)
    stored = genesis_jobs.artifact_path(parent["id"], "source_osm")
    stored.write_text(stored.read_text(encoding="utf-8") + "\n<!-- edited -->", encoding="utf-8")

    signals_path = write_document(tmp_path, "signals.json", signals_document())
    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_signals_regeneration_job(
            parent["id"], signals_input=signals_path, requested_by="reviewer"
        )
    assert excinfo.value.code == "genesis_regenerate_source_sha_mismatch"


def test_a_parent_that_already_carries_a_side_input_is_refused_by_name(
    tmp_path: Path, workspace_root: Path
):
    """Composing a SECOND layer onto a parent that already has one is still
    nobody's decision to make on this caller's behalf -- unit #38's own
    refusal, unchanged, covers this route too."""
    parent = _plain_parent(tmp_path, workspace_root)
    genesis_jobs._update(parent["id"], side_inputs={"demand": "tok_demand"})
    before = set(genesis_jobs.genesis_dir().joinpath("jobs").iterdir())

    signals_path = write_document(tmp_path, "signals.json", signals_document())
    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_signals_regeneration_job(
            parent["id"], signals_input=signals_path, requested_by="reviewer"
        )
    assert excinfo.value.code == "genesis_regenerate_parent_has_side_inputs"
    # The message has to be TRUE for this door's own caller too, not just for
    # unit #38's plain regenerate -- refuter finding F1 (2026-08-26).
    assert "demand" in excinfo.value.reasons[0]
    assert "BARE source" in excinfo.value.reasons[0]
    assert "regenerate-with-side-inputs" in excinfo.value.reasons[0]
    # This parent has no lineage of its own (`_plain_parent` generates
    # directly), so the workaround falls to the generic "start fresh" branch.
    assert "start from (or regenerate) a generation that carries no side inputs" in excinfo.value.reasons[0]
    assert set(genesis_jobs.genesis_dir().joinpath("jobs").iterdir()) == before


def test_the_iteration_loop_is_named_by_the_workaround_not_left_to_dead_end(
    tmp_path: Path, workspace_root: Path
):
    """Refuter finding F1 (2026-08-26), reproduced exactly as measured: stage
    v1 -> regenerate parent A with it (through THIS door) -> child B carries
    side_inputs.signals -> the phases page's picker naturally lands on B (the
    newest job) -> stage a revised v2 and try to regenerate B -> refused.

    Before this fix the refusal claimed "no unit has made yet" decided how
    side inputs travel through a regeneration, which was flatly false (this
    very door IS that decision, for a bare parent) and left the user with no
    honest next step. The rewrite names B's own ORIGINAL parent (A, which
    genuinely carries no side inputs) as the job to regenerate instead --
    the workaround that actually works today, not a dead end."""
    parent_a = _plain_parent(tmp_path, workspace_root, name="original")
    signals_v1 = write_document(tmp_path, "signals-v1.json", signals_document())
    child_b = genesis_jobs.create_signals_regeneration_job(
        parent_a["id"], signals_input=signals_v1, requested_by="reviewer"
    )
    genesis_jobs.run_genesis_job(child_b["id"], child_b["workspace_id"])
    b_record = genesis_jobs.get_job(child_b["id"])
    assert b_record["side_inputs"], "child B must actually carry the composed signals layer"
    assert b_record["derived_from"]["job_id"] == parent_a["id"]

    signals_v2 = write_document(tmp_path, "signals-v2.json", signals_document())
    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_signals_regeneration_job(
            child_b["id"], signals_input=signals_v2, requested_by="reviewer"
        )
    assert excinfo.value.code == "genesis_regenerate_parent_has_side_inputs"
    # A is bare, so the workaround names it BY ID -- not the generic fallback.
    assert f"regenerate from job {parent_a['id']} instead" in excinfo.value.reasons[0]
    assert "with the revised token" in excinfo.value.reasons[0]

    # And the workaround is proven to actually work: regenerating A (not B)
    # with the revised v2 token succeeds.
    child_c = genesis_jobs.create_signals_regeneration_job(
        parent_a["id"], signals_input=signals_v2, requested_by="reviewer"
    )
    assert child_c["id"] not in (parent_a["id"], child_b["id"])


# ---------------------------------------------------------------------------
# §2 The one new door
# ---------------------------------------------------------------------------


def test_neither_token_named_is_refused_by_name(tmp_path: Path, workspace_root: Path):
    parent = _plain_parent(tmp_path, workspace_root)
    before = set(genesis_jobs.genesis_dir().joinpath("jobs").iterdir())

    with pytest.raises(genesis_jobs.GenesisRefused) as excinfo:
        genesis_jobs.create_signals_regeneration_job(parent["id"], requested_by="reviewer")
    assert excinfo.value.code == "genesis_regenerate_with_side_inputs_no_token"
    assert set(genesis_jobs.genesis_dir().joinpath("jobs").iterdir()) == before


# ---------------------------------------------------------------------------
# §3 Lineage: a DIFFERENT kind than a plain regeneration
# ---------------------------------------------------------------------------


def test_the_regenerated_job_carries_a_distinct_lineage_kind(
    tmp_path: Path, workspace_root: Path
):
    parent = _plain_parent(tmp_path, workspace_root)
    signals_path = write_document(tmp_path, "signals.json", signals_document())

    record = genesis_jobs.create_signals_regeneration_job(
        parent["id"], signals_input=signals_path, requested_by="reviewer", reason="현시도 업데이트"
    )

    assert record["derived_from"]["kind"] == genesis_jobs.SIGNALS_REGENERATION_LINEAGE_KIND
    assert record["derived_from"]["kind"] != genesis_jobs.REGENERATE_LINEAGE_KIND
    assert record["derived_from"]["job_id"] == parent["id"]
    assert record["derived_from"]["requested_by"] == "reviewer"
    assert record["derived_from"]["reason"] == "현시도 업데이트"
    assert record["derived_from"]["parent_source_sha256"] == parent["source_osm"]["sha256"]


def test_an_ordinary_generation_still_carries_no_derived_from_key(
    tmp_path: Path, workspace_root: Path
):
    """The absence-is-the-fact discipline every ``derived_from`` reader
    already relies on must survive this unit's new, optional parameter."""
    parent = _plain_parent(tmp_path, workspace_root)
    assert "derived_from" not in parent


# ---------------------------------------------------------------------------
# §4 The parent is byte-untouched
# ---------------------------------------------------------------------------


def test_the_parent_source_is_byte_untouched_by_a_successful_regeneration(
    tmp_path: Path, workspace_root: Path
):
    parent = _plain_parent(tmp_path, workspace_root)
    parent_source = genesis_jobs.artifact_path(parent["id"], "source_osm")
    before_bytes = parent_source.read_bytes()
    signals_path = write_document(tmp_path, "signals.json", signals_document())

    record = genesis_jobs.create_signals_regeneration_job(
        parent["id"], signals_input=signals_path, requested_by="reviewer"
    )

    assert record["id"] != parent["id"]
    assert parent_source.read_bytes() == before_bytes
    assert genesis_jobs.get_job(parent["id"])["side_inputs"] is None


def test_the_new_job_actually_carries_the_signals_layer(
    tmp_path: Path, workspace_root: Path
):
    parent = _plain_parent(tmp_path, workspace_root)
    signals_path = write_document(tmp_path, "signals.json", signals_document())

    record = genesis_jobs.create_signals_regeneration_job(
        parent["id"], signals_input=signals_path, requested_by="reviewer"
    )
    run = genesis_jobs.run_genesis_job(record["id"], record["workspace_id"])

    assert run["status"] == genesis_jobs.STATUS_PROVISIONAL, run.get("refusal")
    assert sorted(run["side_inputs"]) == ["signals"]
    assert run["layers"]["signals"]["controllers"] == 1


# ---------------------------------------------------------------------------
# §5 The HTTP surface
# ---------------------------------------------------------------------------


def _stage_signals(client: TestClient, headers: dict) -> str:
    response = client.post(
        "/api/genesis/side-inputs?kind=signals",
        content=json.dumps(signals_document(), ensure_ascii=False).encode("utf-8"),
        headers={"X-Filename": "signals.json", **headers},
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def test_the_exact_shape_the_phases_cta_sends_is_accepted(client: TestClient, tmp_path: Path):
    """``{"signals_token": "..."}`` and nothing else -- the request body the
    ``genesis/phases`` CTA actually issues (no ``reason``, no
    ``demand_token``). A request-shape round trip so the frontend and this
    door cannot drift apart silently."""
    headers = admin_headers()
    osm_path = tmp_path / "http-plain.osm"
    osm_path.write_text(crossroads_osm(), encoding="utf-8", newline="")
    parent = _generate(osm_path, name="http-plain")
    assert parent["status"] in genesis_jobs.GATED_STATUSES

    signals_token = _stage_signals(client, headers)

    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/regenerate-with-side-inputs",
        json={"signals_token": signals_token},
        headers=headers,
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["id"] != parent["id"]
    assert body["derived_from"]["kind"] == "signals_regeneration"
    assert body["derived_from"]["job_id"] == parent["id"]
    # Awaited so the background thread finishes before this test's own
    # workspace_root teardown unmounts the tree it is still writing into --
    # the same discipline test_genesis_job_layers.py's own HTTP tests keep.
    terminal = _await_terminal(client, body["id"])
    assert terminal["status"] == genesis_jobs.STATUS_PROVISIONAL, terminal.get("refusal")


def test_a_token_given_as_the_wrong_kind_is_refused(client: TestClient, tmp_path: Path):
    """GAP D1 (refuter, 2026-08-26): the wrong-kind check lives in the shared
    `_resolve_side_input` helper `POST /jobs` already exercises, but nothing
    had exercised it on THIS route -- an easy hole for a future edit to open
    without a single test noticing. A demand document handed to the signals
    field would otherwise refuse for a reason ("no assignments") that says
    nothing about the real mistake -- the same trap
    test_a_token_given_as_the_wrong_kind_is_refused in test_genesis_job_layers.py
    guards on `POST /jobs`."""
    headers = admin_headers()
    osm_path = tmp_path / "http-wrong-kind.osm"
    osm_path.write_text(crossroads_osm(), encoding="utf-8", newline="")
    parent = _generate(osm_path, name="http-wrong-kind")

    demand_token = _stage(client, "demand", demand_document(), headers)
    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/regenerate-with-side-inputs",
        json={"signals_token": demand_token},
        headers=headers,
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "genesis_side_input_kind_mismatch"


def test_an_unknown_parent_job_is_a_404(client: TestClient):
    headers = admin_headers()
    signals_token = _stage_signals(client, headers)
    response = client.post(
        "/api/genesis/jobs/does-not-exist/regenerate-with-side-inputs",
        json={"signals_token": signals_token},
        headers=headers,
    )
    assert response.status_code == 404


def test_no_token_named_over_http_is_a_409_by_code(client: TestClient, tmp_path: Path):
    headers = admin_headers()
    osm_path = tmp_path / "http-no-token.osm"
    osm_path.write_text(crossroads_osm(), encoding="utf-8", newline="")
    parent = _generate(osm_path, name="http-no-token")

    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/regenerate-with-side-inputs",
        json={},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "genesis_regenerate_with_side_inputs_no_token"


def test_a_parent_with_side_inputs_is_a_409_by_code_over_http(
    client: TestClient, tmp_path: Path
):
    headers = admin_headers()
    osm_path = tmp_path / "http-composed.osm"
    osm_path.write_text(crossroads_osm(), encoding="utf-8", newline="")
    parent = _generate(osm_path, name="http-composed")
    genesis_jobs._update(parent["id"], side_inputs={"signals": "tok"})

    signals_token = _stage_signals(client, headers)
    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/regenerate-with-side-inputs",
        json={"signals_token": signals_token},
        headers=headers,
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "genesis_regenerate_parent_has_side_inputs"


def test_extra_fields_are_forbidden(client: TestClient, tmp_path: Path):
    """Same discipline as every other genesis request model in this router:
    a caller cannot smuggle an unreviewed field past this door."""
    headers = admin_headers()
    osm_path = tmp_path / "http-extra.osm"
    osm_path.write_text(crossroads_osm(), encoding="utf-8", newline="")
    parent = _generate(osm_path, name="http-extra")

    signals_token = _stage_signals(client, headers)
    response = client.post(
        f"/api/genesis/jobs/{parent['id']}/regenerate-with-side-inputs",
        json={"signals_token": signals_token, "unexpected": True},
        headers=headers,
    )
    assert response.status_code == 422

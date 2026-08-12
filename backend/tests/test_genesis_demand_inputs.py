"""Demand side-input UI — the browser-clickable path from a Daejeon counts
workbook to a staged ``genesis.demand.v1`` document (roadmap v2 §5.B).

Before this unit, attaching demand to a generation meant hand-writing the
``genesis.demand.v1`` JSON (counts from ``DaejeonCountAdapter`` + a hand-made
``approach_links`` site→leg→link map) and staging it with a bare token: every
step but the raw upload required a terminal. This unit adds the two mutating
routes that make the whole thing clickable —

* ``POST /api/genesis/demand-inputs/parse`` — one counts workbook in, the
  adapter's own read (sites, legs, classes, refusals) out, plus a staged token;
* ``POST /api/genesis/demand-inputs/build`` — that token + a caller's
  ``approach_links`` in, a staged ``demand`` side input out —

and one read, ``GET /api/genesis/side-inputs``, so a job-start form can offer
pickers instead of a hand-typed token.

**No second door** is the property under test throughout: the build route must
refuse an unknown site/leg with ``services.genesis.demand``'s OWN reason
(``check_approach_link_sites``, extracted from ``generate_demand`` for exactly
this reuse), and the document it stages must be BYTE-COMPATIBLE with what
``side_inputs.parse_side_input`` accepts directly — proven by parsing the
staged bytes a second, independent way and comparing summaries.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

from conftest import admin_headers
from services import genesis_jobs
from services.genesis import side_inputs as si
from services.genesis.draft import link_table_fingerprint

from tests.test_dataimport_daejeon import _approach_rows, _write_workbook
from tests.test_genesis_jobs import (  # noqa: F401 - fixtures
    _generate,
    client,
    crossroads_osm_file,
    workspace_root,
)
from tests.test_genesis_side_inputs import as_bytes, signals_document

# ---------------------------------------------------------------------------
# A real generation, for `built_against_job` — CRITICAL fix (roadmap v2 §5.B/G)
#
# `built_against_job` names the generation `approach_links`' link ids were read
# off, so these tests use an ACTUAL job with a real `genesis_json` artifact
# (the same synchronous `_generate` helper `tests/test_genesis_jobs.py` uses)
# rather than a placeholder token the build route would have to fake its way
# past.
# ---------------------------------------------------------------------------


def _built_against_job(crossroads_osm_file: Path) -> str:
    record = _generate(crossroads_osm_file)
    assert record["status"] != genesis_jobs.STATUS_ERROR, record
    return record["id"]


# ---------------------------------------------------------------------------
# A small synthetic counts workbook — invented data, real export shape
# ---------------------------------------------------------------------------


def _counts_workbook(tmp_path: Path, *, approach: str = "가람방면") -> tuple[str, bytes]:
    """The workbook's OWN name matters here, not just its bytes: the export
    states no site id anywhere in the sheet, so the site id ``DaejeonCountAdapter``
    falls back to is read from the ``…_<교차로명>_<N>분_…`` file-name pattern
    (see the adapter's own docstring). A test that renamed the upload to
    ``counts.xlsx`` on the way in would silently turn every fixture into a
    ``daejeon_site_identity_unavailable`` refusal -- so the staged upload here
    keeps the generated file's real name, exactly as a consultant's browser
    upload would.
    """
    path = _write_workbook(tmp_path, _approach_rows(approach))
    return path.name, path.read_bytes()


def _stage_counts(client: TestClient, tmp_path: Path, *, approach: str = "가람방면") -> dict:
    filename, content = _counts_workbook(tmp_path, approach=approach)
    # The real file name is Korean and includes spaces/brackets, so it travels
    # as a percent-encoded query parameter -- exactly the way
    # lib/api.ts's uploadGenesisOsm names its own upload (?filename=…), since a
    # raw non-ASCII value is not a legal HTTP header.
    response = client.post(
        f"/api/genesis/demand-inputs/parse?filename={quote(filename)}",
        content=content,
        headers=admin_headers(),
    )
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# POST /api/genesis/demand-inputs/parse
# ---------------------------------------------------------------------------


def test_anonymous_cannot_parse_a_counts_workbook(client: TestClient):
    response = client.post(
        "/api/genesis/demand-inputs/parse?filename=x.xlsx", content=b"junk"
    )
    assert response.status_code == 401


def test_a_clean_workbook_parses_and_stages_a_counts_token(client: TestClient, tmp_path: Path):
    body = _stage_counts(client, tmp_path)
    assert body["kind"] == "counts"
    assert body["token"]
    assert body["filename"].endswith(".xlsx")
    assert body["sha256"]

    summary = body["summary"]
    assert summary["adapter"] == "daejeon_smart_intersection"
    assert summary["ok"] is True
    assert summary["counts"] > 0
    assert summary["sites"] == [
        {"site_id": "가람네거리", "site_name": "가람네거리", "legs": ["가람방면"]}
    ]
    assert summary["vehicle_classes"]
    assert summary["window"]["start"] and summary["window"]["end"]
    # The adapter's own refusal semantics pass through VERBATIM: the export
    # states no site id, so the file-name fallback's own WARN is reported here
    # rather than being softened or omitted.
    codes = [issue["code"] for issue in summary["issues"]]
    assert "daejeon_site_id_from_file_name" in codes


def test_a_non_workbook_upload_refuses_without_a_token(client: TestClient):
    response = client.post(
        "/api/genesis/demand-inputs/parse",
        content=b"not a workbook at all",
        headers={"X-Filename": "notes.txt", **admin_headers()},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "genesis_counts_not_workbook"


def test_an_unreadable_xlsx_body_refuses_rather_than_reporting_an_empty_parse(
    client: TestClient,
):
    response = client.post(
        "/api/genesis/demand-inputs/parse",
        content=b"PK\x03\x04 this is not a real zip/xlsx payload",
        headers={"X-Filename": "counts.xlsx", **admin_headers()},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "genesis_counts_unreadable"


def test_an_empty_upload_refuses(client: TestClient):
    response = client.post(
        "/api/genesis/demand-inputs/parse",
        content=b"",
        headers={"X-Filename": "counts.xlsx", **admin_headers()},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "genesis_counts_empty"


def test_an_oversized_counts_workbook_is_refused_with_the_limit(client: TestClient):
    oversized = b"x" * (si.MAX_SIDE_INPUT_BYTES + 1)
    response = client.post(
        "/api/genesis/demand-inputs/parse",
        content=oversized,
        headers={"X-Filename": "counts.xlsx", **admin_headers()},
    )
    assert response.status_code == 413
    assert str(si.MAX_SIDE_INPUT_BYTES // (1024 * 1024)) in response.json()["detail"]


# ---------------------------------------------------------------------------
# POST /api/genesis/demand-inputs/build
# ---------------------------------------------------------------------------


def test_anonymous_cannot_build_a_demand_input(client: TestClient):
    response = client.post(
        "/api/genesis/demand-inputs/build",
        json={
            "counts_token": "0" * 32,
            "approach_links": {"S": {"L": 1}},
            "built_against_job": "x" * 12,
        },
    )
    assert response.status_code == 401


def test_build_refuses_without_built_against_job(client: TestClient, tmp_path: Path):
    """CRITICAL fix: a link id means nothing without the generation that
    assigned it, so the field is REQUIRED at the schema level -- omitting it
    refuses (FastAPI's own named "field required" error), never silently
    building a document with no provenance for its numbering."""
    staged_counts = _stage_counts(client, tmp_path)
    site = staged_counts["summary"]["sites"][0]
    response = client.post(
        "/api/genesis/demand-inputs/build",
        json={
            "counts_token": staged_counts["token"],
            "approach_links": {site["site_id"]: {site["legs"][0]: 1}},
        },
        headers=admin_headers(),
    )
    assert response.status_code == 422
    assert any(
        error.get("loc") == ["body", "built_against_job"]
        for error in response.json()["detail"]
    )


def test_build_stages_a_demand_document_byte_compatible_with_the_raw_route(
    client: TestClient, tmp_path: Path, crossroads_osm_file: Path
):
    staged_counts = _stage_counts(client, tmp_path)
    site = staged_counts["summary"]["sites"][0]
    site_id, leg = site["site_id"], site["legs"][0]
    job_id = _built_against_job(crossroads_osm_file)

    response = client.post(
        "/api/genesis/demand-inputs/build",
        json={
            "counts_token": staged_counts["token"],
            "approach_links": {site_id: {leg: 7}},
            "built_against_job": job_id,
        },
        headers=admin_headers(),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "demand"
    assert body["token"]

    summary = body["summary"]
    assert summary["sites_mapped"] == [site_id]
    assert summary["approaches_mapped"] == 1
    assert summary["doc_kind"] == si.DEMAND_DOC_KIND

    # Byte-compatibility (build → parse → same summary): the document this
    # route staged is read back off disk and re-parsed through the SAME
    # side_inputs.parse_side_input the raw upload route uses -- a second,
    # independent path to the identical summary.
    staged_path = genesis_jobs.staged_side_input(body["token"])["path"]
    reparsed = si.parse_side_input(si.KIND_DEMAND, staged_path.read_bytes())
    assert reparsed.summary() == summary

    # CRITICAL fix: the fingerprint round-trips through parse_side_input --
    # the SAME string generate_demand will compare against at generation
    # time -- and it is this job's OWN link-table fingerprint, not a copy
    # computed some other way.
    assert reparsed.built_against_job == job_id
    census = json.loads(genesis_jobs.artifact_path(job_id, "genesis_json").read_text(encoding="utf-8"))
    from services.genesis.draft import DraftNetwork

    expected_fingerprint = link_table_fingerprint(DraftNetwork.model_validate(census))
    assert reparsed.built_against_link_table_sha256 == expected_fingerprint


def test_build_404s_on_an_unknown_built_against_job(client: TestClient, tmp_path: Path):
    staged_counts = _stage_counts(client, tmp_path)
    site = staged_counts["summary"]["sites"][0]
    response = client.post(
        "/api/genesis/demand-inputs/build",
        json={
            "counts_token": staged_counts["token"],
            "approach_links": {site["site_id"]: {site["legs"][0]: 1}},
            "built_against_job": "0" * 12,
        },
        headers=admin_headers(),
    )
    assert response.status_code == 404


def test_build_404s_when_built_against_job_has_no_genesis_json_artifact(
    client: TestClient, tmp_path: Path
):
    """An errored generation ("there is no artifact") is a different answer
    from "the artifact has a fault" -- and either way, a job with no
    genesis_json is a named refusal (404), never a fingerprint computed from
    nothing."""
    broken = tmp_path / "broken.osm"
    broken.write_text("<osm><way></osm>", encoding="utf-8")
    record = _generate(broken)
    assert record["status"] == genesis_jobs.STATUS_ERROR
    assert record["artifact"] is None

    staged_counts = _stage_counts(client, tmp_path)
    site = staged_counts["summary"]["sites"][0]
    response = client.post(
        "/api/genesis/demand-inputs/build",
        json={
            "counts_token": staged_counts["token"],
            "approach_links": {site["site_id"]: {site["legs"][0]: 1}},
            "built_against_job": record["id"],
        },
        headers=admin_headers(),
    )
    assert response.status_code == 404


def test_build_404s_when_built_against_job_belongs_to_another_workspace(
    client: TestClient, tmp_path: Path, crossroads_osm_file: Path
):
    """Physically isolated, the same discipline
    ``tests/test_genesis_jobs.py::test_a_job_in_one_project_does_not_exist_in_another``
    proves at the service layer -- here proven through the HTTP route: a job
    id from the DEFAULT workspace is unknown from a DIFFERENT one, so the
    build route 404s rather than fingerprinting a foreign job's draft."""
    job_id = _built_against_job(crossroads_osm_file)  # lands in the DEFAULT workspace

    created = client.post(
        "/api/workspaces", json={"name": "다른 프로젝트"}, headers=admin_headers()
    )
    assert created.status_code == 200, created.text
    other_headers = {"X-Workspace-Id": created.json()["id"], **admin_headers()}

    filename, content = _counts_workbook(tmp_path)
    staged_counts_response = client.post(
        f"/api/genesis/demand-inputs/parse?filename={quote(filename)}",
        content=content,
        headers=other_headers,
    )
    assert staged_counts_response.status_code == 200, staged_counts_response.text
    staged_counts = staged_counts_response.json()
    site = staged_counts["summary"]["sites"][0]

    response = client.post(
        "/api/genesis/demand-inputs/build",
        json={
            "counts_token": staged_counts["token"],
            "approach_links": {site["site_id"]: {site["legs"][0]: 1}},
            "built_against_job": job_id,
        },
        headers=other_headers,
    )
    assert response.status_code == 404


def test_build_refuses_an_unknown_site_with_the_underlying_modules_own_reason(
    client: TestClient, tmp_path: Path, crossroads_osm_file: Path
):
    staged_counts = _stage_counts(client, tmp_path)
    response = client.post(
        "/api/genesis/demand-inputs/build",
        json={
            "counts_token": staged_counts["token"],
            "approach_links": {"이런지점없음": {"어떤접근로": 1}},
            "built_against_job": _built_against_job(crossroads_osm_file),
        },
        headers=admin_headers(),
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "genesis_demand_map_names_unknown_site"
    assert any("이런지점없음" in reason for reason in detail["reasons"])


def test_build_refuses_an_unknown_leg_at_a_real_site(
    client: TestClient, tmp_path: Path, crossroads_osm_file: Path
):
    staged_counts = _stage_counts(client, tmp_path)
    site_id = staged_counts["summary"]["sites"][0]["site_id"]
    response = client.post(
        "/api/genesis/demand-inputs/build",
        json={
            "counts_token": staged_counts["token"],
            "approach_links": {site_id: {"없는접근로": 1}},
            "built_against_job": _built_against_job(crossroads_osm_file),
        },
        headers=admin_headers(),
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "genesis_demand_map_names_unknown_approach"
    assert any("없는접근로" in reason for reason in detail["reasons"])


def test_build_refusal_reasons_come_back_in_the_requests_language(
    client: TestClient, tmp_path: Path, crossroads_osm_file: Path
):
    """MEDIUM A: the route used to hard-code ``.get("ko")`` even though the
    file already has ``_language(request)`` (used by the gate/report routes)
    -- so an English caller got Korean prose back. One language pair
    (English) suffices to prove the fix picks the caller's language."""
    staged_counts = _stage_counts(client, tmp_path)
    response = client.post(
        "/api/genesis/demand-inputs/build?language=en",
        json={
            "counts_token": staged_counts["token"],
            "approach_links": {"이런지점없음": {"어떤접근로": 1}},
            "built_against_job": _built_against_job(crossroads_osm_file),
        },
        headers=admin_headers(),
    )
    assert response.status_code == 400
    reasons = response.json()["detail"]["reasons"]
    # The en message's own wording -- and NOT the ko sentence's wording
    # ("카운트에 없는"), which would show up here if the route still hard-coded
    # `.get("ko")` regardless of `?language=en`. (The site id itself,
    # "이런지점없음", is echoed back verbatim inside the en sentence too --
    # that is the caller's own input, not a translation, so it is not what
    # this assertion checks.)
    assert any("The map names site(s)" in reason and "absent from the counts" in reason for reason in reasons)
    assert not any("카운트에 없는" in reason for reason in reasons)


def test_build_refuses_an_unknown_counts_token(client: TestClient):
    response = client.post(
        "/api/genesis/demand-inputs/build",
        json={
            "counts_token": "0" * 32,
            "approach_links": {"S": {"L": 1}},
            "built_against_job": "x" * 12,
        },
        headers=admin_headers(),
    )
    assert response.status_code == 404


def test_build_refuses_a_token_staged_as_a_different_kind(client: TestClient):
    """A demand/signals token used where a COUNTS token is expected must be
    refused BY KIND, the same discipline ``_resolve_side_input`` applies to
    ``demand_token``/``signals_token`` on ``POST /jobs`` -- otherwise a
    mistaken field would be handed to a builder that refuses for a reason
    naming the wrong mistake."""
    staged = client.post(
        "/api/genesis/side-inputs?kind=signals",
        content=as_bytes(signals_document()),
        headers={"X-Filename": "signals.json", **admin_headers()},
    )
    assert staged.status_code == 200, staged.text
    signals_token = staged.json()["token"]

    response = client.post(
        "/api/genesis/demand-inputs/build",
        json={
            "counts_token": signals_token,
            "approach_links": {"S": {"L": 1}},
            "built_against_job": "x" * 12,
        },
        headers=admin_headers(),
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/genesis/side-inputs
# ---------------------------------------------------------------------------


def test_anonymous_cannot_list_staged_side_inputs(client: TestClient):
    assert client.get("/api/genesis/side-inputs").status_code == 401


def test_listing_shows_every_staged_kind_with_its_summary(
    client: TestClient, tmp_path: Path, crossroads_osm_file: Path
):
    staged_counts = _stage_counts(client, tmp_path)
    site = staged_counts["summary"]["sites"][0]
    built = client.post(
        "/api/genesis/demand-inputs/build",
        json={
            "counts_token": staged_counts["token"],
            "approach_links": {site["site_id"]: {site["legs"][0]: 3}},
            "built_against_job": _built_against_job(crossroads_osm_file),
        },
        headers=admin_headers(),
    )
    assert built.status_code == 200, built.text

    listing = client.get("/api/genesis/side-inputs", headers=admin_headers())
    assert listing.status_code == 200
    entries = {entry["token"]: entry for entry in listing.json()["side_inputs"]}

    counts_entry = entries[staged_counts["token"]]
    assert counts_entry["kind"] == "counts"
    assert counts_entry["filename"].endswith(".xlsx")
    assert counts_entry["summary"]["counts"] > 0

    demand_entry = entries[built.json()["token"]]
    assert demand_entry["kind"] == "demand"
    assert demand_entry["summary"]["doc_kind"] == si.DEMAND_DOC_KIND
    assert demand_entry["summary"]["sites_mapped"] == [site["site_id"]]


def test_listing_is_empty_when_nothing_is_staged(client: TestClient):
    response = client.get("/api/genesis/side-inputs", headers=admin_headers())
    assert response.status_code == 200
    assert response.json() == {"side_inputs": []}

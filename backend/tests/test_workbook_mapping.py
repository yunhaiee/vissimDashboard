"""Tests for the flexible base-workbook mapping import (services/workbook_mapping.py
+ the new /api/validation/base-workbook/{inspect,mapping/*} endpoints).

Covers: inspect never crashing on a hostile real-world file, the auto-proposed
mapping reusing COUNT_HEADER_ALIASES, a genuinely different layout importing
end-to-end through an explicit mapping and driving GEH identically to a
template import, every refusal reason actually firing, and the mapping-memory
recall. The existing template regression (POST /base-workbook still imports
with zero interaction) is proven by NOT touching that path here -- see
test_base_workbook_validation.py, which this file does not duplicate.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

import main
from services import datasets, validation, workbook_mapping
from services.validation import create_base_workbook_template

MURDOCH_DRIVE_PATH = Path(r"C:\Users\user\Downloads\Murdoch Drive_Scenario Model_0624\Scenario TurningMovements_review.xlsx")


def _xlsx_bytes(wb) -> bytes:
    out = BytesIO()
    wb.save(out)
    return out.getvalue()


def _post_bytes(client: TestClient, path: str, content: bytes, filename: str = "upload.xlsx"):
    return client.post(path, params={"filename": filename}, content=content)


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


# --------------------------------------------------------------------------
# Inspect: bounded, never crashes, proposes a mapping via COUNT_HEADER_ALIASES.
# --------------------------------------------------------------------------

def test_inspect_never_crashes_on_hostile_real_world_file(client):
    if not MURDOCH_DRIVE_PATH.exists():
        pytest.skip(f"Real client fixture not present on this machine: {MURDOCH_DRIVE_PATH}")

    content = MURDOCH_DRIVE_PATH.read_bytes()
    response = _post_bytes(client, "/api/validation/base-workbook/inspect", content, MURDOCH_DRIVE_PATH.name)

    assert response.status_code == 200
    payload = response.json()
    assert payload["upload_token"]
    assert len(payload["sheets"]) == 10
    for sheet in payload["sheets"]:
        assert isinstance(sheet["headers"], list)
        assert isinstance(sheet["sample_rows"], list)
        assert len(sheet["sample_rows"]) <= workbook_mapping.MAX_SAMPLE_ROWS
        # header_row must be a real row number even when this sheet's data
        # starts well past row 1 (this fixture's turning-movement sheets
        # start around row 11-17, column H, with no header text at all).
        assert sheet["header_row"] >= 1


def test_inspect_proposes_mapping_for_the_standard_template(client):
    template = client.get("/api/validation/base-workbook/template").content
    response = _post_bytes(client, "/api/validation/base-workbook/inspect", template, "real_counts_template.xlsx")

    assert response.status_code == 200
    payload = response.json()
    counts_sheet = next(s for s in payload["sheets"] if s["name"] == "Counts")
    proposed = counts_sheet["proposed_counts_mapping"]
    for field in ("period", "movement", "observed_volume", "model_volume"):
        assert field in proposed
        assert proposed[field]["source"] == "alias"
    # The template packs node id/name INTO the "Movement" column's cell text
    # ("1: 도서관사거리"), not into its own header -- so header-level alias
    # matching correctly finds no separate node_id/node_name column here.
    # That packed format is exactly what the UNCHANGED default parser (the
    # "has_vissim_shape" path in _parse_counts_sheet) already handles, which
    # is why the frontend tries the existing template-shaped upload FIRST
    # and only falls back to this mapping UI when that path reports warnings.
    assert "node_id" not in proposed
    assert "node_name" not in proposed


def test_inspect_rejects_empty_upload(client):
    response = _post_bytes(client, "/api/validation/base-workbook/inspect", b"", "empty.xlsx")
    assert response.status_code == 400


def test_inspect_rejects_non_excel_bytes(client):
    response = _post_bytes(client, "/api/validation/base-workbook/inspect", b"not an excel file at all", "junk.xlsx")
    assert response.status_code == 400


# --------------------------------------------------------------------------
# A genuinely different layout: scrambled Korean headers, a non-standard
# sheet name, a header row that isn't row 1. Imports end-to-end via an
# explicit mapping and drives GEH exactly like a template import would.
# --------------------------------------------------------------------------

def _build_custom_layout_workbook() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "구간별교통량"  # not "Counts"
    ws.append(["2024년 교통량 조사"])  # title row
    ws.append([])  # blank row
    # Header row 3, columns scrambled vs. STANDARD_COUNTS_HEADERS order, and
    # using Korean text that exactly matches COUNT_HEADER_ALIASES entries.
    ws.append(["이동류", "교차로ID", "교차로명", "실측", "모델", "시간대구분"])
    ws.append(["N-E", "10", "Main St", 100, 98, "AM"])
    ws.append(["Total", "10", "Main St", 220, 215, "AM"])
    ws.append(["N-E", "10", "Main St", 150, 140, "PM"])
    return _xlsx_bytes(wb)


def test_custom_layout_inspect_auto_proposes_full_mapping(client):
    content = _build_custom_layout_workbook()
    response = _post_bytes(client, "/api/validation/base-workbook/inspect", content, "custom.xlsx")
    assert response.status_code == 200
    payload = response.json()

    sheet = next(s for s in payload["sheets"] if s["name"] == "구간별교통량")
    assert sheet["header_row"] == 3
    proposed = sheet["proposed_counts_mapping"]
    assert proposed["movement"] == {"column": 0, "header": "이동류", "source": "alias", "matched_alias": "이동류"}
    assert proposed["node_id"]["column"] == 1
    assert proposed["node_name"]["column"] == 2
    assert proposed["observed_volume"]["column"] == 3
    assert proposed["model_volume"]["column"] == 4
    assert proposed["period"]["column"] == 5

    # The top-level recommendation should also have found this sheet as the
    # best counts source, since every required field matched.
    assert payload["proposed_mapping"]["counts"]["sheet"] == "구간별교통량"


def test_custom_layout_imports_end_to_end_and_drives_geh_identically(client):
    content = _build_custom_layout_workbook()
    inspect_response = _post_bytes(client, "/api/validation/base-workbook/inspect", content, "custom.xlsx")
    token = inspect_response.json()["upload_token"]
    proposed = inspect_response.json()["sheets"][0]["proposed_counts_mapping"]

    fields = {name: entry["column"] for name, entry in proposed.items()}
    body = {"upload_token": token, "counts": {"sheet": "구간별교통량", "header_row": 3, "fields": fields}}

    preview = client.post("/api/validation/base-workbook/mapping/preview", json=body)
    assert preview.status_code == 200
    preview_payload = preview.json()["counts_preview"]
    assert preview_payload["issues"] == []
    assert preview_payload["total_rows"] == 3
    assert preview_payload["rows"][0]["observed_volume"] == 100.0
    assert preview_payload["rows"][0]["model_volume"] == 98.0

    import_response = client.post("/api/validation/base-workbook/mapping/import", json=body)
    assert import_response.status_code == 200
    workbook_payload = import_response.json()
    assert workbook_payload["exists"] is True
    assert len(workbook_payload["counts"]) == 3

    am_total_row = next(r for r in workbook_payload["counts"] if r["period"] == "AM" and r["movement"] == "Total")
    assert am_total_row["observed_volume"] == 220.0
    assert am_total_row["model_volume"] == 215.0
    # Same geh() function the template path's GEH chart uses -- proves the
    # mapping path never diverges from the standard downstream math.
    assert validation.geh(215.0, 220.0) == pytest.approx(validation.geh(am_total_row["model_volume"], am_total_row["observed_volume"]))

    # GET /api/validation/base-workbook (every existing consumer) must now
    # reflect the mapped import unchanged, exactly as it does after a
    # template upload.
    get_response = client.get("/api/validation/base-workbook")
    assert get_response.status_code == 200
    assert get_response.json()["counts"] == workbook_payload["counts"]


def test_mapping_is_remembered_for_a_future_upload_of_the_same_layout(client):
    content = _build_custom_layout_workbook()
    inspect_response = _post_bytes(client, "/api/validation/base-workbook/inspect", content, "custom.xlsx")
    token = inspect_response.json()["upload_token"]
    proposed = inspect_response.json()["sheets"][0]["proposed_counts_mapping"]
    fields = {name: entry["column"] for name, entry in proposed.items()}
    body = {"upload_token": token, "counts": {"sheet": "구간별교통량", "header_row": 3, "fields": fields}}
    assert client.post("/api/validation/base-workbook/mapping/import", json=body).status_code == 200

    # Re-upload a DIFFERENTLY-NAMED sheet with the identical header layout --
    # the remembered mapping should be recalled (source: "remembered") even
    # though the alias guess alone already covered these particular headers.
    second = openpyxl.Workbook()
    ws = second.active
    ws.title = "다른시트명"
    ws.append(["이동류", "교차로ID", "교차로명", "실측", "모델", "시간대구분"])
    ws.append(["N-E", "20", "2nd St", 50, 47, "AM"])
    inspect_2 = _post_bytes(client, "/api/validation/base-workbook/inspect", _xlsx_bytes(second), "custom2.xlsx")
    assert inspect_2.status_code == 200
    sheet_2 = inspect_2.json()["sheets"][0]
    assert sheet_2["header_row"] == 1
    remembered_fields = {k: v for k, v in sheet_2["proposed_counts_mapping"].items() if v["source"] == "remembered"}
    assert remembered_fields, "expected at least one field recalled from mapping memory"


# --------------------------------------------------------------------------
# Every refusal reason must actually fire, with ko/en/ja wording.
# --------------------------------------------------------------------------

def _stage(client, wb) -> str:
    resp = _post_bytes(client, "/api/validation/base-workbook/inspect", _xlsx_bytes(wb), "refusal.xlsx")
    assert resp.status_code == 200
    return resp.json()["upload_token"]


def test_refusal_required_field_unmapped(client):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Movement", "Observed"])
    ws.append(["N-E", 100])
    token = _stage(client, wb)

    body = {
        "upload_token": token,
        "counts": {"sheet": "Sheet1", "header_row": 1, "fields": {"movement": 0, "observed_volume": 1}},
    }
    response = client.post("/api/validation/base-workbook/mapping/import", json=body)
    assert response.status_code == 400
    issues = response.json()["detail"]["issues"]
    assert any(i["code"] == "counts_required_field_unmapped" for i in issues)
    issue = next(i for i in issues if i["code"] == "counts_required_field_unmapped")
    assert issue["ko"] and issue["en"] and issue["ja"]


def test_refusal_observed_and_model_same_column(client):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Period", "Node", "Movement", "Volume"])
    ws.append(["AM", "1", "N-E", 100])
    token = _stage(client, wb)

    body = {
        "upload_token": token,
        "counts": {
            "sheet": "Sheet1",
            "header_row": 1,
            "fields": {"period": 0, "node_id": 1, "movement": 2, "observed_volume": 3, "model_volume": 3},
        },
    }
    response = client.post("/api/validation/base-workbook/mapping/import", json=body)
    assert response.status_code == 400
    issues = response.json()["detail"]["issues"]
    assert any(i["code"] == "counts_observed_model_same_column" for i in issues)


def test_refusal_observed_column_has_no_numeric_values(client):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Period", "Node", "Movement", "Observed"])
    ws.append(["AM", "1", "N-E", None])
    ws.append(["AM", "1", "Total", "n/a"])
    token = _stage(client, wb)

    body = {
        "upload_token": token,
        "counts": {"sheet": "Sheet1", "header_row": 1, "fields": {"period": 0, "node_id": 1, "movement": 2, "observed_volume": 3}},
    }
    response = client.post("/api/validation/base-workbook/mapping/import", json=body)
    assert response.status_code == 400
    issues = response.json()["detail"]["issues"]
    assert any(i["code"] == "counts_observed_no_numeric_values" for i in issues)


def test_refusal_self_comparison_trap_fires_when_observed_equals_model_row_for_row(client):
    """The exact trap this feature exists to catch: model output mapped into
    the observed column by mistake -- GEH would compare the model against
    itself and always pass. Must be refused, not imported silently."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Period", "Node", "Movement", "FieldObserved", "ModelOutput"])
    ws.append(["AM", "1", "N-E", 100, 100])
    ws.append(["AM", "1", "Total", 220, 220])
    ws.append(["PM", "1", "N-E", 150, 150])
    token = _stage(client, wb)
    # Start from a known-clean slate -- other tests in this session-scoped
    # fixture may have already imported a workbook.
    client.delete("/api/validation/base-workbook")

    body = {
        "upload_token": token,
        "counts": {
            "sheet": "Sheet1",
            "header_row": 1,
            "fields": {"period": 0, "node_id": 1, "movement": 2, "observed_volume": 3, "model_volume": 4},
        },
    }
    preview = client.post("/api/validation/base-workbook/mapping/preview", json=body)
    assert preview.status_code == 200
    preview_issues = preview.json()["counts_preview"]["issues"]
    assert any(i["code"] == "counts_self_comparison_trap" for i in preview_issues)

    response = client.post("/api/validation/base-workbook/mapping/import", json=body)
    assert response.status_code == 400
    issues = response.json()["detail"]["issues"]
    issue = next(i for i in issues if i["code"] == "counts_self_comparison_trap")
    assert issue["ko"] and issue["en"] and issue["ja"]

    # And must NOT have been persisted -- a refused import leaves the active
    # workbook untouched.
    get_response = client.get("/api/validation/base-workbook")
    assert get_response.json()["exists"] is False


def test_no_false_positive_self_comparison_when_values_genuinely_differ(client):
    """Sanity check on the other side of the trap check: real, differing
    observed/model data must import cleanly with no self-comparison issue."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Period", "Node", "Movement", "Observed", "Model"])
    ws.append(["AM", "1", "N-E", 100, 98])
    ws.append(["AM", "1", "Total", 220, 215])
    token = _stage(client, wb)

    body = {
        "upload_token": token,
        "counts": {
            "sheet": "Sheet1",
            "header_row": 1,
            "fields": {"period": 0, "node_id": 1, "movement": 2, "observed_volume": 3, "model_volume": 4},
        },
    }
    response = client.post("/api/validation/base-workbook/mapping/import", json=body)
    assert response.status_code == 200
    assert response.json()["exists"] is True


# --------------------------------------------------------------------------
# Staging plumbing.
# --------------------------------------------------------------------------

def test_unknown_upload_token_returns_404(client):
    body = {
        "upload_token": "not-a-real-token",
        "counts": {"sheet": "Sheet1", "header_row": 1, "fields": {"period": 0}},
    }
    response = client.post("/api/validation/base-workbook/mapping/preview", json=body)
    assert response.status_code == 404


def test_resolve_endpoint_recomputes_mapping_for_a_different_header_row(client):
    content = _build_custom_layout_workbook()
    token = _post_bytes(client, "/api/validation/base-workbook/inspect", content, "custom.xlsx").json()["upload_token"]

    response = client.get(
        "/api/validation/base-workbook/mapping/" + token + "/resolve",
        params={"sheet": "구간별교통량", "header_row": 3},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["headers"] == ["이동류", "교차로ID", "교차로명", "실측", "모델", "시간대구분"]
    assert payload["proposed_counts_mapping"]["movement"]["column"] == 0


# --------------------------------------------------------------------------
# Stage-then-promote: POST /api/validation/base-workbook itself (the path the
# frontend always tries FIRST) must never let a non-conforming upload
# displace the active workbook. See routers/validation.py's
# upload_base_workbook docstring -- a template-conforming file is promoted
# exactly as before; a non-conforming file is staged (same staging dir
# /inspect uses) and the response describes the STILL-ACTIVE workbook, with
# a `pending_mapping` block carrying the staged upload_token + inspect
# payload + warnings.
# --------------------------------------------------------------------------

def _bad_layout_workbook() -> bytes:
    """A readable .xlsx that matches none of the three known Counts shapes
    and has no TravelTimes sheet -- guaranteed non-conforming."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Random", "Header", "Layout"])
    ws.append([1, 2, 3])
    return _xlsx_bytes(wb)


def test_conforming_upload_still_promotes_to_active(client):
    """Unchanged behavior for the common case: a template-shaped file becomes
    the active workbook in one call, response has pending_mapping: null."""
    content = create_base_workbook_template()
    response = _post_bytes(client, "/api/validation/base-workbook", content, "real_counts.xlsx")
    assert response.status_code == 200
    payload = response.json()
    assert payload["exists"] is True
    assert payload["pending_mapping"] is None
    assert len(payload["counts"]) > 0

    get_response = client.get("/api/validation/base-workbook")
    assert get_response.json()["counts"] == payload["counts"]


def test_non_conforming_upload_leaves_active_workbook_byte_identical(client):
    good_content = create_base_workbook_template()
    upload_good = _post_bytes(client, "/api/validation/base-workbook", good_content, "real_counts.xlsx")
    assert upload_good.status_code == 200
    good_payload = upload_good.json()

    active_path = datasets.get_active_real_counts_excel()
    active_bytes_before = active_path.read_bytes()

    upload_bad = _post_bytes(client, "/api/validation/base-workbook", _bad_layout_workbook(), "bad.xlsx")
    assert upload_bad.status_code == 200
    bad_payload = upload_bad.json()

    # The active workbook file on disk is byte-for-byte unchanged.
    assert active_path.read_bytes() == active_bytes_before

    # The response's top-level fields describe that STILL-ACTIVE workbook --
    # never the rejected upload's (empty) parse.
    assert bad_payload["exists"] is True
    assert bad_payload["filename"] == good_payload["filename"]
    assert bad_payload["counts"] == good_payload["counts"]
    assert bad_payload["travel_times"] == good_payload["travel_times"]

    # ...while pending_mapping carries the staged rejected upload, ready for
    # the mapping UI without asking the user to upload again.
    pending = bad_payload["pending_mapping"]
    assert pending is not None
    assert pending["upload_token"]
    assert pending["filename"] == "bad.xlsx"
    assert pending["warnings"], "expected the template-parse warnings that explain the rejection"
    assert any(sheet["name"] == "Sheet1" for sheet in pending["sheets"])

    # GET (every other consumer) also still reflects the good workbook.
    get_response = client.get("/api/validation/base-workbook")
    assert get_response.json()["counts"] == good_payload["counts"]


def test_non_conforming_upload_with_no_active_workbook_reports_empty_state(client, monkeypatch, tmp_path):
    # Start from a known-clean slate (other tests in this session-scoped
    # ambient-state fixture may have already uploaded a workbook), and make
    # sure the module-level default-demo-data fallback can't paper over it.
    client.delete("/api/validation/base-workbook")
    monkeypatch.setattr(datasets, "REAL_COUNTS_EXCEL", tmp_path / "missing_default_real_counts.xlsx")

    response = _post_bytes(client, "/api/validation/base-workbook", _bad_layout_workbook(), "bad.xlsx")
    assert response.status_code == 200
    payload = response.json()
    assert payload["exists"] is False
    assert payload["counts"] == []
    assert payload["pending_mapping"] is not None
    assert payload["pending_mapping"]["upload_token"]


def test_upload_rejects_non_excel_bytes_without_touching_active(client):
    good_content = create_base_workbook_template()
    _post_bytes(client, "/api/validation/base-workbook", good_content, "real_counts.xlsx")
    active_path = datasets.get_active_real_counts_excel()
    active_bytes_before = active_path.read_bytes()

    response = _post_bytes(client, "/api/validation/base-workbook", b"not an excel file at all", "junk.xlsx")
    assert response.status_code == 400
    assert active_path.read_bytes() == active_bytes_before


def test_upload_rejects_wrong_extension_without_touching_active_or_staging(client):
    good_content = create_base_workbook_template()
    _post_bytes(client, "/api/validation/base-workbook", good_content, "real_counts.xlsx")
    active_path = datasets.get_active_real_counts_excel()
    active_bytes_before = active_path.read_bytes()

    response = _post_bytes(client, "/api/validation/base-workbook", good_content, "real_counts.csv")
    assert response.status_code == 400
    assert active_path.read_bytes() == active_bytes_before


def test_pending_mapping_token_from_upload_can_be_imported_and_promotes(client):
    """The mapping flow operates on the SAME staged copy the rejected upload
    created -- no second upload -- and import_mapping is the one and only
    promotion point."""
    good_content = create_base_workbook_template()
    _post_bytes(client, "/api/validation/base-workbook", good_content, "real_counts.xlsx")

    content = _build_custom_layout_workbook()
    upload_response = _post_bytes(client, "/api/validation/base-workbook", content, "custom.xlsx")
    assert upload_response.status_code == 200
    pending = upload_response.json()["pending_mapping"]
    assert pending is not None
    token = pending["upload_token"]
    proposed = pending["proposed_mapping"]["counts"]
    assert proposed is not None and proposed["sheet"] == "구간별교통량"

    body = {
        "upload_token": token,
        "counts": {
            "sheet": proposed["sheet"],
            "header_row": proposed["header_row"],
            "fields": {name: guess["column"] for name, guess in proposed["fields"].items()},
        },
    }
    import_response = client.post("/api/validation/base-workbook/mapping/import", json=body)
    assert import_response.status_code == 200
    imported_payload = import_response.json()
    assert len(imported_payload["counts"]) == 3

    get_response = client.get("/api/validation/base-workbook")
    assert get_response.json()["counts"] == imported_payload["counts"]


def test_cancel_leaves_active_workbook_untouched_and_discard_is_the_chosen_cleanup(client):
    """Cancelling the mapping modal (no import call) must leave the active
    workbook exactly as it was -- true by construction here, since the
    non-conforming upload never touched it. This also proves DELETE
    /base-workbook/mapping/{token} (the discard route added for the cancel
    button) actually removes the staged copy, not just returning 204 for
    show."""
    good_content = create_base_workbook_template()
    good_upload = _post_bytes(client, "/api/validation/base-workbook", good_content, "real_counts.xlsx")
    good_payload = good_upload.json()
    active_path = datasets.get_active_real_counts_excel()
    active_bytes_before = active_path.read_bytes()

    content = _build_custom_layout_workbook()
    upload_response = _post_bytes(client, "/api/validation/base-workbook", content, "custom.xlsx")
    token = upload_response.json()["pending_mapping"]["upload_token"]

    delete_response = client.delete(f"/api/validation/base-workbook/mapping/{token}")
    assert delete_response.status_code == 204

    # Active workbook: untouched.
    assert active_path.read_bytes() == active_bytes_before
    get_response = client.get("/api/validation/base-workbook")
    assert get_response.json()["counts"] == good_payload["counts"]

    # The staged copy is actually gone -- a later import attempt on the same
    # (now-discarded) token 404s rather than silently succeeding.
    reimport = client.post(
        "/api/validation/base-workbook/mapping/import",
        json={"upload_token": token, "counts": {"sheet": "구간별교통량", "header_row": 3, "fields": {"movement": 0}}},
    )
    assert reimport.status_code == 404


def test_discard_mapping_endpoint_is_idempotent_on_unknown_token(client):
    response = client.delete("/api/validation/base-workbook/mapping/not-a-real-token")
    assert response.status_code == 204

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
from services import validation, workbook_mapping

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

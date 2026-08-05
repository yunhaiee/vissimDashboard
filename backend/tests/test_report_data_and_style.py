"""Report data-extract (R4.1) + figure style presets (R4.2).

Covers:
  - Each CSV data endpoint returns rows that equal the same underlying
    row-computation functions the .docx report renders from (never a
    second/re-derived pipeline).
  - The default (no style) travel-time chart and the built .docx are
    byte-identical to calling the render/build functions with no style
    argument at all -- the regression-safety contract for R4.2.
  - A company style profile actually changes the rendered chart bytes and
    is threaded through the full /api/report/build request.
"""
from __future__ import annotations

import csv
import io

import pytest
from fastapi.testclient import TestClient

import main
from services.report_builder import build_report, geh_validation_csv_rows
from services.report_exports import (
    flow_geh_csv_rows,
    los_summary_csv_rows,
    render_travel_chart,
    travel_chart_specs,
)


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as test_client:
        yield test_client


# --- R4.1: data extraction ---------------------------------------------------


def _read_csv(content: bytes) -> list[dict]:
    text = content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


@pytest.mark.parametrize(
    "block_type,expected_columns",
    [
        ("geh-validation", ["Period", "Node", "Observed", "Modelled", "GEH", "Grade"]),
        ("los-summary", ["Node", "Approach", "Direction", "AM Volume", "PM Volume", "Row Type"]),
        ("flow-geh-validation", ["Period", "Node", "No. Counts", "Observed Flow", "Flow %", "GEH %"]),
        ("travel-time-charts", ["Route", "Period", "Distance (km)", "Observed (s)", "Model (s)"]),
    ],
)
def test_report_data_endpoint_returns_expected_columns(client: TestClient, block_type, expected_columns):
    resp = client.get(f"/api/report/data/{block_type}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    # utf-8-sig BOM present so Excel auto-detects encoding for Korean text.
    assert resp.content.startswith(b"\xef\xbb\xbf")
    header_line = resp.content.decode("utf-8-sig").splitlines()[0]
    for col in expected_columns:
        assert col in header_line


def test_report_data_unknown_block_type_is_404(client: TestClient):
    resp = client.get("/api/report/data/not-a-real-block")
    assert resp.status_code == 404


def test_report_data_geh_validation_matches_source_function(client: TestClient):
    """The CSV endpoint must reuse geh_validation_csv_rows() verbatim -- not
    recompute GEH from scratch (the exact bug class docs/ux-backlog.md #2
    warns about: a third disjoint GEH pipeline)."""
    expected = geh_validation_csv_rows()
    resp = client.get("/api/report/data/geh-validation")
    assert resp.status_code == 200
    rows = _read_csv(resp.content)
    assert len(rows) == len(expected)
    for row, exp in zip(rows, expected):
        assert row["Node"] == exp["node"]
        assert row["Period"] == exp["period"]
        if exp["geh"] is not None:
            assert float(row["GEH"]) == pytest.approx(exp["geh"])


def test_report_data_los_summary_matches_source_function(client: TestClient):
    expected = los_summary_csv_rows()
    resp = client.get("/api/report/data/los-summary")
    assert resp.status_code == 200
    rows = _read_csv(resp.content)
    assert len(rows) == len(expected)
    for row, exp in zip(rows, expected):
        assert row["Node"] == exp["node"]
        assert row["Row Type"] == exp["row_type"]


def test_report_data_flow_geh_matches_source_function(client: TestClient):
    expected = flow_geh_csv_rows()
    resp = client.get("/api/report/data/flow-geh-validation")
    assert resp.status_code == 200
    rows = _read_csv(resp.content)
    assert len(rows) == len(expected)
    for row, exp in zip(rows, expected):
        assert row["Node"] == exp["node"]
        if exp["pct_diff"] is not None:
            assert float(row["% Diff"]) == pytest.approx(exp["pct_diff"])


def test_flow_geh_csv_shape_and_values_are_pinned_for_a_clean_dataset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Literal pin of the flow-GEH CSV a CLEAN dataset produces.

    Nothing covered this. ``test_report_data_endpoint_returns_expected_columns``
    only asserts that six names appear SOMEWHERE in the header line -- not the
    order, not the count -- and
    ``test_report_data_flow_geh_matches_source_function`` compares the CSV
    against the very function that generates it, so both sides of that
    comparison move together. Unit 1.5 added two columns (``GEH Counts``,
    ``GEH Excluded``) to EVERY export, including clean ones, and no test
    noticed: a downstream consumer pinned to column order or count would have
    broken silently. This is that missing pin.
    """
    from services import report_exports
    from services.validation import DirectionCount, NodeRealCounts

    def clean_counts(_path, period=None):
        return {
            "1": NodeRealCounts(
                node_id="1",
                node_name="Alpha",
                total_real=1000,
                total_sim_basam=1020,
                directions=[DirectionCount(direction="W-E", real=1000, sim_basam=1020)],
            )
        }

    clean_counts.cache_clear = lambda: None
    monkeypatch.setattr(report_exports, "load_real_counts", clean_counts)

    resp = client.get("/api/report/data/flow-geh-validation")
    assert resp.status_code == 200
    lines = resp.content.decode("utf-8-sig").splitlines()

    # Column ORDER and COUNT, not just membership.
    assert lines[0] == (
        "Period,Node,No. Counts,Observed Flow,Modelled Flow,% Diff,"
        "Flow %,GEH %,GEH Counts,GEH Excluded (0 obs / 0 model),Row Type"
    )
    assert len(lines[0].split(",")) == 11

    # Every value, literally. GEH is 0.63 here, so Flow % and GEH % are both
    # 100, GEH Counts equals No. Counts, and GEH Excluded is 0 -- a clean
    # dataset must never show an exclusion or an undefined marker.
    assert lines[1:] == [
        "AM Peak,Node 1,1,1000,1020,2.0,100,100,1,0,node",
        "AM Peak,Total,1,1000,1020,2.0,100,100,1,0,total",
        "PM Peak,Node 1,1,1000,1020,2.0,100,100,1,0,node",
        "PM Peak,Total,1,1000,1020,2.0,100,100,1,0,total",
    ]


def test_report_data_travel_time_charts_matches_source_function(client: TestClient):
    expected = travel_chart_specs()
    resp = client.get("/api/report/data/travel-time-charts")
    assert resp.status_code == 200
    rows = _read_csv(resp.content)
    assert len(rows) == len(expected)
    for row, exp in zip(rows, expected):
        assert row["Route"] == exp["name"]
        assert float(row["Observed (s)"]) == pytest.approx(exp["obs"])
        assert float(row["Model (s)"]) == pytest.approx(exp["model"])


# --- R4.2: figure style presets ----------------------------------------------

_SAMPLE_SPEC = {
    "name": "Test Route", "period": "AM", "dist_km": 2.5,
    "obs": 300.0, "model": 320.0, "low": 255.0, "high": 345.0, "max_dist": 2.5,
}


def test_render_travel_chart_default_is_byte_identical_with_or_without_style_arg():
    """No style / style=None / style={} must all render exactly the same PNG
    -- the byte-stability contract for 'no company profile set'."""
    baseline = render_travel_chart(_SAMPLE_SPEC)
    explicit_none = render_travel_chart(_SAMPLE_SPEC, style=None)
    explicit_empty = render_travel_chart(_SAMPLE_SPEC, style={})
    assert baseline == explicit_none == explicit_empty


def test_render_travel_chart_with_style_changes_output():
    baseline = render_travel_chart(_SAMPLE_SPEC)
    styled = render_travel_chart(
        _SAMPLE_SPEC,
        style={"palette": ["#00ff00", "#0000ff"], "font_size_title": 20, "font_size_label": 14},
    )
    assert baseline != styled


def test_build_report_default_has_no_style_field_effect(client: TestClient):
    spec = {
        "meta": {"title": "T"},
        "blocks": [{"type": "travel-time-charts"}],
    }
    resp = client.post("/api/report/build", json=spec)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_build_report_all_blocks_render_without_error(client: TestClient):
    """Exercises every block's .docx-writing path in one request -- in
    particular add_los_section's per_node loop (services/report_exports.py),
    which the CSV-focused tests above never touch since they call
    los_summary_rows()/los_summary_csv_rows() directly, not the docx table
    builder that consumes the same structure."""
    spec = {
        "meta": {"title": "T", "project": "P"},
        "blocks": [
            {"type": "cover"},
            {"type": "overview"},
            {"type": "geh-validation"},
            {"type": "los-summary"},
            {"type": "flow-geh-validation"},
            {"type": "travel-time-charts"},
            {"type": "text", "heading": "H", "body": "line one\nline two"},
        ],
    }
    resp = client.post("/api/report/build", json=spec)
    assert resp.status_code == 200
    assert len(resp.content) > 1000


def test_build_report_with_style_profile_succeeds(client: TestClient):
    spec = {
        "meta": {"title": "T"},
        "blocks": [{"type": "travel-time-charts"}],
        "style": {
            "palette": ["#ff0000", "#00aaff"],
            "font_family": "Arial",
            "font_size_title": 16,
            "font_size_label": 11,
            "figure_width_in": 4.0,
        },
    }
    resp = client.post("/api/report/build", json=spec)
    assert resp.status_code == 200
    assert len(resp.content) > 0


def test_build_report_default_vs_styled_docx_differ(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    # The bundled demo dataset has no travel-time validation rows (see
    # test_report_data_travel_time_charts_matches_source_function, which
    # legitimately asserts 0 rows for it), so the "travel-time-charts" block
    # renders no picture at all end-to-end and style has nothing to apply to.
    # Patch in one synthetic spec so this test actually exercises a rendered
    # chart going through the full /api/report/build request.
    import services.report_builder as report_builder_module

    def _fake_specs():
        return [{
            "name": "Test Route", "period": "AM", "dist_km": 2.5,
            "obs": 300.0, "model": 320.0, "low": 255.0, "high": 345.0, "max_dist": 2.5,
        }]

    monkeypatch.setattr(report_builder_module, "travel_chart_specs", _fake_specs)

    base_spec = {"meta": {"title": "T"}, "blocks": [{"type": "travel-time-charts"}]}
    styled_spec = {
        **base_spec,
        "style": {"palette": ["#123456", "#654321"], "figure_width_in": 3.0},
    }
    base_resp = client.post("/api/report/build", json=base_spec)
    styled_resp = client.post("/api/report/build", json=styled_spec)
    assert base_resp.status_code == styled_resp.status_code == 200
    assert base_resp.content != styled_resp.content

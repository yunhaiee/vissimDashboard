"""Track R unit R-3 — ``services/report/graphics.py`` + ``GET /api/report/graphics``.

Mirrors ``tests/test_report_figures.py``'s own doctrine (that file's module
docstring is binding here too): every generator is tested at the SAME seam
``routers/calibration.py``'s own tests already patch -- the reader function
itself, on the module it lives on (``tools_network.build_shadow_model``,
``tools_metrics.build_reality_check_report``). No test stands up a real
Vissim run or a real DB-backed dataset -- this module reads NOTHING itself,
so patching its readers is patching the actual I/O boundary.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import main
from services import datasets
from services.agent import tools_metrics, tools_network
from services.report import graphics, spine

_SVG_NS = "{http://www.w3.org/2000/svg}"

# Transcribed INDEPENDENTLY from frontend/components/BaseValidationMap.tsx
# (resolveNodeStyle ~lines 438-455, buildLegend ~lines 525-535) -- these are
# literal hex strings, never read from graphics.VERDICT_COLORS itself. A test
# that instead asserted ``marker.fill == graphics.VERDICT_COLORS["good"]["fill"]``
# would compare the module's palette against itself and pass even if two
# bands' colours were swapped (measured: this was exactly the gap the R-3
# refuter's mutation run found on the first pass). Pinning the real hex
# values here is what makes a swapped-colour mutation actually die.
_EXPECTED_VERDICT_COLORS = {
    "good": {"fill": "#16a34a", "stroke": "#166534"},
    "fair": {"fill": "#f59e0b", "stroke": "#92400e"},
    "poor": {"fill": "#dc2626", "stroke": "#7f1d1d"},
    "undefined": {"fill": "#94a3b8", "stroke": "#64748b"},
    "ungraded": {"fill": "#f8fafc", "stroke": "#64748b"},
}

# NIT 4: a name/label carrying every XML-special class this module might one
# day mishandle if a "simplify to an f-string" refactor replaced the
# ElementTree builder with hand-assembled markup -- '&' and '<'/'>' (the
# baseline escapes), '"' (an attribute-value breakout), the CDATA terminator
# ']]>' (breaks out of a CDATA section if one were ever introduced), and a
# literal '<script>' tag (the actual injection payload a naive f-string
# renderer would let straight through). Verified today only by hand
# (ElementTree escapes correctly); nothing pinned it before this fix.
_XML_SPECIALS_NAME = 'A & B <tag> "quoted" ]]> <script>alert(1)</script>'


def _assert_no_raw_xml_injection(svg: str) -> None:
    assert "<script" not in svg, "a literal <script> tag survived unescaped into the SVG"
    assert "]]>" not in svg, "a raw CDATA terminator survived unescaped into the SVG"


def _svg_tree(svg: str) -> ET.Element:
    return ET.fromstring(svg)


def _findall(root: ET.Element, tag: str) -> list[ET.Element]:
    return root.findall(f".//{_SVG_NS}{tag}")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _shadow_model(nodes=None, links=None) -> dict:
    return {
        "nodes": nodes
        if nodes is not None
        else [
            {"id": "1", "name": "Alpha", "x": 0.0, "y": 0.0, "polygon": []},
            {"id": "2", "name": "Bravo", "x": 100.0, "y": 50.0, "polygon": []},
            {"id": "3", "name": "Charlie", "x": 200.0, "y": -20.0, "polygon": []},
        ],
        "links": links
        if links is not None
        else [
            {
                "id": "10", "name": "L10", "points": [[0.0, 0.0], [100.0, 50.0]], "lanes": 2,
                "lane_widths": [3.5, 3.5], "width_total": 7.0, "length": 111.8,
                "is_connector": False, "from_link": None, "to_link": None,
                "from_pos": None, "to_pos": None,
            },
            {
                "id": "11", "name": "C11", "points": [[100.0, 50.0], [200.0, -20.0]], "lanes": 1,
                "lane_widths": [3.5], "width_total": 3.5, "length": 122.1,
                "is_connector": True, "from_link": "10", "to_link": "12",
                "from_pos": 0.0, "to_pos": 0.0,
            },
        ],
    }


def _checked_report(rows: list[dict]) -> dict:
    return {"status": "CHECKED", "geh_threshold": 5.0, "nodes": rows}


# Node "3" (Charlie) is deliberately left out -- it stays ungraded.
_GOLDEN_REPORT_ROWS = [
    {"node_id": "1", "node_name": "Alpha", "observed_total": 1000.0, "modelled_total": 1010.0, "node_total_geh": 0.315},
    {"node_id": "2", "node_name": "Bravo", "observed_total": 1000.0, "modelled_total": 1200.0, "node_total_geh": 6.03},
]


class FakeReadableInpx:
    def is_file(self) -> bool:
        return True


class FakeUnreadableInpx:
    def is_file(self) -> bool:
        return False


# ===========================================================================
# 1. node_map
# ===========================================================================


def test_node_map_golden_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model())
    monkeypatch.setattr(
        tools_metrics, "build_reality_check_report", lambda scenario_id, **kw: _checked_report(_GOLDEN_REPORT_ROWS)
    )

    fig = graphics.node_map("scn-1")

    assert fig["absent"] is False
    assert fig["figure_id"] == graphics.NODE_MAP_ID
    assert fig["spine_section_id"] == "statistical_comparison"
    assert fig["citation"] == spine.CIT_2_12_2_STATISTICAL_COMPARISON

    # DEFECT CLASS: nodes present in the network but never graded must never
    # be omitted, and must never be coloured as passing.
    assert fig["meta"]["total_node_count"] == 3
    assert fig["meta"]["graded_node_count"] == 2
    assert fig["meta"]["ungraded_node_count"] == 1
    assert fig["meta"]["verdict_counts"] == {"good": 1, "fair": 1, "poor": 0, "undefined": 0, "ungraded": 1}

    root = _svg_tree(fig["svg"])
    markers = _findall(root, "circle")
    assert len(markers) == 3, "SVG node-marker count must equal graded + ungraded, never fewer"
    by_id = {m.get("data-node-id"): m for m in markers}
    assert by_id["1"].get("data-verdict") == "good"
    assert by_id["1"].get("fill") == _EXPECTED_VERDICT_COLORS["good"]["fill"]
    assert by_id["1"].get("stroke") == _EXPECTED_VERDICT_COLORS["good"]["stroke"]
    assert by_id["2"].get("data-verdict") == "fair"
    assert by_id["2"].get("fill") == _EXPECTED_VERDICT_COLORS["fair"]["fill"]
    # The ungraded node must carry the explicit ungraded style, never the
    # "good"/passing colour and never be missing from the markup.
    assert by_id["3"].get("data-verdict") == "ungraded"
    assert by_id["3"].get("fill") == _EXPECTED_VERDICT_COLORS["ungraded"]["fill"]
    assert by_id["3"].get("fill") != _EXPECTED_VERDICT_COLORS["good"]["fill"]


def test_node_map_legend_matches_verdict_vocabulary():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model())
    monkeypatch.setattr(
        tools_metrics, "build_reality_check_report", lambda scenario_id, **kw: _checked_report(_GOLDEN_REPORT_ROWS)
    )
    try:
        fig = graphics.node_map("scn-1")
    finally:
        monkeypatch.undo()

    assert fig["absent"] is False
    legend_keys = {entry["key"] for entry in fig["legend"]}
    assert legend_keys == graphics.VERDICT_KEYS
    root = _svg_tree(fig["svg"])
    legend_group = root.find(f".//{_SVG_NS}g[@class='legend']")
    assert legend_group is not None
    assert len(_findall(legend_group, "rect")) == len(graphics.VERDICT_KEYS)


def _legend_swatches_by_key(svg: str) -> dict[str, ET.Element]:
    root = _svg_tree(svg)
    legend_group = root.find(f".//{_SVG_NS}g[@class='legend']")
    assert legend_group is not None, "no legend group in this SVG"
    return {el.get("data-key"): el for el in _findall(legend_group, "rect")}


def test_node_map_legend_swatch_colors_in_the_svg_match_the_frontend_source_exactly(monkeypatch: pytest.MonkeyPatch):
    """DEFECT 3: the JSON ``legend`` payload can be right while the DRAWN
    ``<rect>`` in the SVG lies (e.g. fill/stroke swapped inside
    ``_embed_legend``) -- and the SVG, not the JSON, is what a DOCX export
    would actually place on the page. This reads the swatch colours back OUT
    of the SVG markup and compares them against ``_EXPECTED_VERDICT_COLORS``
    -- transcribed independently of ``graphics.VERDICT_COLORS`` -- so a
    mutation that swaps every swatch's fill/stroke inside the renderer is
    caught even though the JSON legend list stays untouched."""
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model())
    monkeypatch.setattr(
        tools_metrics, "build_reality_check_report", lambda scenario_id, **kw: _checked_report(_GOLDEN_REPORT_ROWS)
    )
    fig = graphics.node_map("scn-1")
    swatches = _legend_swatches_by_key(fig["svg"])
    assert set(swatches) == set(_EXPECTED_VERDICT_COLORS)
    for key, expected in _EXPECTED_VERDICT_COLORS.items():
        assert swatches[key].get("fill") == expected["fill"], f"{key} fill"
        assert swatches[key].get("stroke") == expected["stroke"], f"{key} stroke"


def test_before_after_legend_swatch_colors_in_the_svg_match_the_frontend_source_exactly(
    monkeypatch: pytest.MonkeyPatch,
):
    """Same DEFECT-3 proof as the node_map test above, for the OTHER real
    figure that embeds a verdict-palette legend -- both call the same shared
    ``_embed_legend``/``_verdict_legend_entries`` helpers, but a mutation
    scoped to just one call site (or to ``before_after_geh_chart``'s own
    legend_keys list) would only be caught here."""
    before_rows = [{"node_id": "1", "node_name": "Alpha", "observed_total": 1.0, "modelled_total": 1.0, "node_total_geh": 0.0}]
    after_rows = [{"node_id": "1", "node_name": "Alpha", "observed_total": 1.0, "modelled_total": 1.1, "node_total_geh": 1.5}]
    monkeypatch.setattr(tools_metrics, "build_reality_check_report", _before_after_reader(before_rows, after_rows))
    fig = graphics.before_after_geh_chart("before-1", "after-1")
    swatches = _legend_swatches_by_key(fig["svg"])
    assert set(swatches) == set(_EXPECTED_VERDICT_COLORS)
    for key, expected in _EXPECTED_VERDICT_COLORS.items():
        assert swatches[key].get("fill") == expected["fill"], f"{key} fill"
        assert swatches[key].get("stroke") == expected["stroke"], f"{key} stroke"


def test_node_map_connectors_are_visually_distinct_from_links():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model())
    monkeypatch.setattr(
        tools_metrics, "build_reality_check_report", lambda scenario_id, **kw: _checked_report(_GOLDEN_REPORT_ROWS)
    )
    try:
        fig = graphics.node_map("scn-1")
    finally:
        monkeypatch.undo()

    root = _svg_tree(fig["svg"])
    lines = _findall(root, "polyline")
    by_id = {line.get("data-link-id"): line for line in lines}
    assert by_id["10"].get("data-connector") == "false"
    assert by_id["11"].get("data-connector") == "true"
    assert by_id["10"].get("class") != by_id["11"].get("class")
    # A connector must carry SOME distinguishing visual marker -- here, a
    # dash pattern the plain link does not have.
    assert by_id["10"].get("stroke-dasharray") == "none"
    assert by_id["11"].get("stroke-dasharray") != "none"


def test_node_map_absent_without_scenario_id():
    fig = graphics.node_map(None)
    assert fig == {
        "figure_id": graphics.NODE_MAP_ID,
        "spine_section_id": "statistical_comparison",
        "absent": True,
        "reason": fig["reason"],
    }
    assert "svg" not in fig and "meta" not in fig


def test_node_map_absent_on_not_checkable_carries_the_reports_own_message(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model())
    message = {"ko": "실행 결과가 없습니다", "en": "no run result", "ja": "実行結果がありません"}
    monkeypatch.setattr(
        tools_metrics,
        "build_reality_check_report",
        lambda scenario_id, **kw: {"status": "NOT_CHECKABLE", "reason_code": "RUN_NOT_FOUND", "message": message},
    )
    fig = graphics.node_map("missing-run")
    assert fig["absent"] is True
    assert fig["reason"] == message


def test_node_map_absent_with_no_readable_network(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeUnreadableInpx())
    fig = graphics.node_map("scn-1")
    assert fig["absent"] is True
    assert "NO_READABLE_NETWORK" in fig["reason"]["ko"]
    assert "svg" not in fig


def test_node_map_absent_when_shadow_model_reader_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())

    def raises(path):
        raise ValueError("corrupt inpx")

    monkeypatch.setattr(tools_network, "build_shadow_model", raises)
    fig = graphics.node_map("scn-1")
    assert fig["absent"] is True
    assert "ValueError" in fig["reason"]["en"]
    assert "corrupt inpx" in fig["reason"]["en"]


def test_node_map_absent_when_reality_check_reader_raises(monkeypatch: pytest.MonkeyPatch):
    """DEFECT-class mutation target: unwrap the try/except around the
    reality-check reader and this test dies (a raise propagates instead of
    becoming a named absence)."""
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model())

    def raises(scenario_id, **kw):
        raise RuntimeError("result db is locked")

    monkeypatch.setattr(tools_metrics, "build_reality_check_report", raises)
    fig = graphics.node_map("scn-1")
    assert fig["absent"] is True
    assert "svg" not in fig
    assert "RuntimeError" in fig["reason"]["en"]
    assert "result db is locked" in fig["reason"]["en"]
    assert "RuntimeError" in fig["reason"]["ko"]
    assert "RuntimeError" in fig["reason"]["ja"]


def test_node_map_verdicts_without_geometry_is_named_not_silently_dropped(monkeypatch: pytest.MonkeyPatch):
    """A reality-check node id absent from the shadow model's own node list
    cannot be spatially plotted -- it must be named in ``meta``, not merged
    away without a trace."""
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model())
    rows = _GOLDEN_REPORT_ROWS + [
        {"node_id": "999", "node_name": "Ghost", "observed_total": 1.0, "modelled_total": 1.0, "node_total_geh": 0.0}
    ]
    monkeypatch.setattr(tools_metrics, "build_reality_check_report", lambda scenario_id, **kw: _checked_report(rows))
    fig = graphics.node_map("scn-1")
    assert fig["meta"]["verdicts_without_geometry"] == ["999"]


def test_node_map_svg_is_deterministic_apart_from_generated_at(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model())
    monkeypatch.setattr(
        tools_metrics, "build_reality_check_report", lambda scenario_id, **kw: _checked_report(_GOLDEN_REPORT_ROWS)
    )
    first = graphics.node_map("scn-1")["svg"]
    second = graphics.node_map("scn-1")["svg"]

    def _strip(svg: str) -> str:
        return re.sub(r"generated_at=\S+", "generated_at=X", svg)

    assert _strip(first) == _strip(second)


def test_node_map_zero_nodes_is_not_an_absence(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model(nodes=[], links=[]))
    monkeypatch.setattr(tools_metrics, "build_reality_check_report", lambda scenario_id, **kw: _checked_report([]))
    fig = graphics.node_map("scn-1")
    assert fig["absent"] is False
    assert fig["meta"]["total_node_count"] == 0


def test_node_map_escapes_xml_special_characters_in_node_names(monkeypatch: pytest.MonkeyPatch):
    """NIT 4: a node name carrying '&','<','>','\"', ']]>' and a literal
    <script> tag must never break the SVG's XML-well-formedness or survive
    unescaped -- the node name lands in the ``data-node-name`` ATTRIBUTE
    here, so this specifically exercises ElementTree's attribute-escaping
    path (which also escapes '\"', unlike its text-content path)."""
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(
        tools_network,
        "build_shadow_model",
        lambda path: _shadow_model(
            nodes=[{"id": "1", "name": _XML_SPECIALS_NAME, "x": 0.0, "y": 0.0, "polygon": []}], links=[]
        ),
    )
    monkeypatch.setattr(tools_metrics, "build_reality_check_report", lambda scenario_id, **kw: _checked_report([]))

    fig = graphics.node_map("scn-1")
    svg = fig["svg"]

    root = _svg_tree(svg)  # raises ET.ParseError if the markup is not well-formed
    _assert_no_raw_xml_injection(svg)
    assert "&amp;" in svg
    assert "&lt;" in svg
    assert "&gt;" in svg
    assert "&quot;" in svg  # attribute-value escaping, unlike text content

    marker = root.find(f".//{_SVG_NS}circle[@data-node-id='1']")
    assert marker is not None
    # Round-trip proof: parsing the escaped attribute back out recovers the
    # ORIGINAL string exactly -- not merely "did not crash".
    assert marker.get("data-node-name") == _XML_SPECIALS_NAME


# ===========================================================================
# 2. before_after_geh_chart
# ===========================================================================


def _before_after_reader(before_rows: list[dict], after_rows: list[dict]):
    def reader(scenario_id, **kwargs):
        if scenario_id == "before-1":
            return _checked_report(before_rows)
        if scenario_id == "after-1":
            return _checked_report(after_rows)
        raise AssertionError(f"unexpected scenario_id {scenario_id!r}")

    return reader


def test_before_after_golden_path_and_one_sided_nodes_never_dropped(monkeypatch: pytest.MonkeyPatch):
    # Node "1" carries DELIBERATELY far-apart before/after values -- 2.0
    # (good, short bar, green) vs 12.0 (poor, tall bar, red) -- rather than
    # two values that both happen to fall in the same "good" band. A refuter
    # mutation that swaps which (geh, present) pair draws under the
    # "before"/"after" offset (labels/data-side attrs left untouched) is
    # invisible if both sides render the same colour and a similar height;
    # it is NOT invisible here (see the side-correctness assertions below).
    before_rows = [
        {"node_id": "1", "node_name": "Alpha", "observed_total": 1000.0, "modelled_total": 1010.0, "node_total_geh": 2.0},
        {"node_id": "2", "node_name": "Bravo", "observed_total": 1000.0, "modelled_total": 1200.0, "node_total_geh": 6.03},
    ]
    after_rows = [
        {"node_id": "1", "node_name": "Alpha", "observed_total": 1000.0, "modelled_total": 1005.0, "node_total_geh": 12.0},
        # node "2" DROPPED after -- one-sided (before-only).
        {"node_id": "3", "node_name": "Charlie", "observed_total": 500.0, "modelled_total": 520.0, "node_total_geh": 0.89},
        # node "3" is after-only -- the symmetric one-sided case.
    ]
    monkeypatch.setattr(tools_metrics, "build_reality_check_report", _before_after_reader(before_rows, after_rows))

    fig = graphics.before_after_geh_chart("before-1", "after-1")

    assert fig["absent"] is False
    assert fig["figure_id"] == graphics.BEFORE_AFTER_GEH_CHART_ID
    assert fig["spine_section_id"] == "calibration"
    assert fig["citation"] == spine.CIT_2_12_2_DATASET_COMPARISON
    assert fig["source_caption"]["scenario_id"] == ["before-1", "after-1"]

    # NEVER inner-joined away: all three ids appear, node "1" is NOT one-sided.
    assert fig["meta"]["node_count"] == 3
    assert set(fig["meta"]["one_sided_node_ids"]) == {"2", "3"}
    assert fig["meta"]["before_only_node_ids"] == ["2"]
    assert fig["meta"]["after_only_node_ids"] == ["3"]

    rows_by_id = {row["node_id"]: row for row in fig["meta"]["rows"]}
    assert rows_by_id["1"]["one_sided"] is False
    assert rows_by_id["1"]["geh_before"] == 2.0
    assert rows_by_id["1"]["geh_after"] == 12.0
    assert rows_by_id["1"]["verdict_before"] == "good"
    assert rows_by_id["1"]["verdict_after"] == "poor"
    assert rows_by_id["2"]["one_sided"] is True
    assert rows_by_id["2"]["after_present"] is False
    assert rows_by_id["2"]["geh_after"] is None
    assert rows_by_id["3"]["one_sided"] is True
    assert rows_by_id["3"]["before_present"] is False

    # The one-sided nodes are marked in the SVG itself too, not just in meta.
    root = _svg_tree(fig["svg"])
    all_bars = [el for el in _findall(root, "rect") if (el.get("class") or "").startswith("bar")]
    missing_bars = [el for el in all_bars if el.get("data-missing") == "true"]
    missing_node_ids = {el.get("data-node-id") for el in missing_bars}
    assert missing_node_ids == {"2", "3"}

    # DEFECT 1 -- SIDE-SWAP: locate node "1"'s two bars by their data-side
    # attribute (not by drawing order or x-position) and assert EACH ONE
    # encodes the value/verdict/colour of the RUN its own label claims. A
    # mutation that swaps which (geh, present) tuple feeds the "before"
    # offset vs the "after" offset -- leaving data-side/data-node-id
    # untouched -- flips these three independent signals (data-geh,
    # data-verdict, fill) together and is caught by any one of them.
    node_1_bars = {el.get("data-side"): el for el in all_bars if el.get("data-node-id") == "1"}
    assert set(node_1_bars) == {"before", "after"}, "DEFECT 2: node '1' (two-sided) must draw BOTH bars"
    before_bar = node_1_bars["before"]
    after_bar = node_1_bars["after"]
    assert before_bar.get("data-geh") == "2.00", "the 'before' bar must encode the BEFORE run's GEH"
    assert after_bar.get("data-geh") == "12.00", "the 'after' bar must encode the AFTER run's GEH"
    assert before_bar.get("data-verdict") == "good"
    assert after_bar.get("data-verdict") == "poor"
    assert before_bar.get("fill") == _EXPECTED_VERDICT_COLORS["good"]["fill"]
    assert after_bar.get("fill") == _EXPECTED_VERDICT_COLORS["poor"]["fill"]
    # The poor (12.0) bar must be drawn TALLER than the good (2.0) bar --
    # an independent, geometry-level cross-check that does not rely on the
    # data-* attributes alone (those could be individually mutated too).
    assert float(after_bar.get("height")) > float(before_bar.get("height"))

    # DEFECT 2 -- BAR COMPLETENESS: EVERY (row, side) pair draws EXACTLY one
    # <rect> -- a real bar when that side is present, an explicit
    # "data-missing" placeholder when it is not (the renderer never omits
    # the rect outright, per this module's "never silently drop a side"
    # rule) -- so the total bar count is always ``2 * len(rows)``, and the
    # split between "present" (data-missing=false) and "absent"
    # (data-missing=true) always matches the rows' own present flags.
    # Structural, not tied to the one-sided fixture coincidence: a mutation
    # that drops only ONE side of a fully-present (two-sided) node -- meta
    # untouched -- breaks BOTH invariants below even though it draws no
    # "data-missing=true" marker at all (that marker only exists for the
    # genuinely-absent-evidence case, which this mutation does not touch).
    rows = fig["meta"]["rows"]
    assert len(all_bars) == 2 * len(rows) == 6
    present_count = sum(int(row["before_present"]) + int(row["after_present"]) for row in rows)
    assert present_count == 4  # node 1: 2 (both sides), node 2: 1, node 3: 1
    drawn_present = [b for b in all_bars if b.get("data-missing") == "false"]
    drawn_missing = [b for b in all_bars if b.get("data-missing") == "true"]
    assert len(drawn_present) == present_count
    assert len(drawn_missing) == 2 * len(rows) - present_count

    # Per-node per-side presence, independent of the aggregate counts above:
    # every row's PRESENT side(s) must each have their own bar, by exact
    # (node_id, side) identity -- this is what catches a mutation scoped to
    # ONE specific node (e.g. "skip node 1's before-bar") that an aggregate
    # count alone could miss if it happened to drop a DIFFERENT bar too.
    bars_by_node_side = {(el.get("data-node-id"), el.get("data-side")) for el in all_bars}
    for row in rows:
        if row["before_present"]:
            assert (row["node_id"], "before") in bars_by_node_side, f"missing before-bar for node {row['node_id']}"
        if row["after_present"]:
            assert (row["node_id"], "after") in bars_by_node_side, f"missing after-bar for node {row['node_id']}"


def test_before_after_absent_when_before_scenario_id_missing():
    fig = graphics.before_after_geh_chart(None, "after-1")
    assert fig["absent"] is True
    assert fig["spine_section_id"] == "calibration"
    assert "before_scenario_id" in fig["reason"]["en"]


def test_before_after_absent_when_after_scenario_id_missing():
    fig = graphics.before_after_geh_chart("before-1", None)
    assert fig["absent"] is True
    assert "after_scenario_id" in fig["reason"]["en"]


def test_before_after_absent_when_before_reader_raises(monkeypatch: pytest.MonkeyPatch):
    def raises(scenario_id, **kw):
        raise RuntimeError("workspace lock held")

    monkeypatch.setattr(tools_metrics, "build_reality_check_report", raises)
    fig = graphics.before_after_geh_chart("before-1", "after-1")
    assert fig["absent"] is True
    assert "RuntimeError" in fig["reason"]["en"]
    assert "이전" in fig["reason"]["ko"] or "before" in fig["reason"]["en"].lower()


def test_before_after_absent_when_after_reader_raises_names_which_side(monkeypatch: pytest.MonkeyPatch):
    """DEFECT-class mutation target: unwrap either reader's try/except and
    this test (or the sibling above) dies."""
    before_rows = [{"node_id": "1", "node_name": "Alpha", "observed_total": 1.0, "modelled_total": 1.0, "node_total_geh": 0.0}]

    def reader(scenario_id, **kw):
        if scenario_id == "before-1":
            return _checked_report(before_rows)
        raise ValueError("torn result db")

    monkeypatch.setattr(tools_metrics, "build_reality_check_report", reader)
    fig = graphics.before_after_geh_chart("before-1", "after-1")
    assert fig["absent"] is True
    assert "ValueError" in fig["reason"]["en"]
    assert "torn result db" in fig["reason"]["en"]
    assert "after" in fig["reason"]["en"].lower()


def test_before_after_absent_on_not_checkable_names_the_side(monkeypatch: pytest.MonkeyPatch):
    message = {"ko": "실행 결과가 없습니다", "en": "no run result", "ja": "実行結果がありません"}

    def reader(scenario_id, **kw):
        return {"status": "NOT_CHECKABLE", "reason_code": "RUN_NOT_FOUND", "message": message}

    monkeypatch.setattr(tools_metrics, "build_reality_check_report", reader)
    fig = graphics.before_after_geh_chart("before-1", "after-1")
    assert fig["absent"] is True
    # The report's own message text survives, tagged with which side failed.
    assert "no run result" in fig["reason"]["en"]


def test_before_after_svg_deterministic_apart_from_generated_at(monkeypatch: pytest.MonkeyPatch):
    before_rows = [{"node_id": "1", "node_name": "Alpha", "observed_total": 1.0, "modelled_total": 1.0, "node_total_geh": 0.0}]
    after_rows = [{"node_id": "1", "node_name": "Alpha", "observed_total": 1.0, "modelled_total": 1.1, "node_total_geh": 1.5}]
    monkeypatch.setattr(tools_metrics, "build_reality_check_report", _before_after_reader(before_rows, after_rows))
    first = graphics.before_after_geh_chart("before-1", "after-1")["svg"]
    second = graphics.before_after_geh_chart("before-1", "after-1")["svg"]

    def _strip(svg: str) -> str:
        return re.sub(r"generated_at=\S+", "generated_at=X", svg)

    assert _strip(first) == _strip(second)


def test_before_after_escapes_xml_special_characters_in_node_label(monkeypatch: pytest.MonkeyPatch):
    """NIT 4: the bar-group's per-node label is drawn as SVG text content
    from the node id (see ``_render_before_after_svg``'s ``text=node_id``) --
    this exercises ElementTree's TEXT-content escaping path (distinct from
    node_map's attribute path: '\"' is not escaped in text content, but
    '&'/'<'/'>' still are, which is what actually neutralises a literal
    <script> tag or a raw ']]>' )."""
    before_rows = [
        {"node_id": _XML_SPECIALS_NAME, "node_name": "Alpha", "observed_total": 1.0, "modelled_total": 1.0, "node_total_geh": 0.0}
    ]
    after_rows = [
        {"node_id": _XML_SPECIALS_NAME, "node_name": "Alpha", "observed_total": 1.0, "modelled_total": 1.1, "node_total_geh": 1.5}
    ]
    monkeypatch.setattr(tools_metrics, "build_reality_check_report", _before_after_reader(before_rows, after_rows))

    fig = graphics.before_after_geh_chart("before-1", "after-1")
    svg = fig["svg"]

    root = _svg_tree(svg)  # raises ET.ParseError if the markup is not well-formed
    _assert_no_raw_xml_injection(svg)
    assert "&amp;" in svg
    assert "&lt;" in svg
    assert "&gt;" in svg

    label = root.find(f".//{_SVG_NS}text[@class='node-label']")
    assert label is not None
    assert label.text == _XML_SPECIALS_NAME
    # The bars' own data-node-id attribute must round-trip identically too.
    bars = [el for el in _findall(root, "rect") if (el.get("class") or "").startswith("bar")]
    assert {el.get("data-node-id") for el in bars} == {_XML_SPECIALS_NAME}


# ===========================================================================
# 3. network_sketch
# ===========================================================================


def test_network_sketch_golden_path_and_named_spine_gap(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model())

    fig = graphics.network_sketch()

    assert fig["absent"] is False
    assert fig["figure_id"] == graphics.NETWORK_SKETCH_ID
    # The named design gap: no invented spine section id.
    assert fig["spine_section_id"] is None
    assert fig["citation"] is None
    # NIT 5 fix: independently-stated substrings, not a self-referential
    # ``fig["meta"][...] == graphics.NETWORK_SKETCH_SECTION_GAP`` compare --
    # that line always agrees with itself (both sides are the same module
    # constant by construction) and cannot catch the gap text going missing
    # or empty from the PAYLOAD specifically.
    gap_text = fig["meta"]["spine_binding_gap"]
    assert isinstance(gap_text, str) and gap_text, "meta must carry real, non-empty gap prose"
    assert "network_sketch" in gap_text
    assert "no REGULATION_SECTIONS id" in gap_text
    assert "REGULATION_SECTIONS" in graphics.NETWORK_SKETCH_SECTION_GAP

    root = _svg_tree(fig["svg"])
    assert len(_findall(root, "circle")) == 3
    assert len(_findall(root, "polyline")) == 2


def test_network_sketch_stroke_width_scales_with_link_width_and_connectors_distinct(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    links = [
        {
            "id": "10", "name": "Wide", "points": [[0.0, 0.0], [50.0, 0.0]], "lanes": 4,
            "lane_widths": [3.5] * 4, "width_total": 14.0, "length": 50.0,
            "is_connector": False, "from_link": None, "to_link": None, "from_pos": None, "to_pos": None,
        },
        {
            "id": "11", "name": "Narrow", "points": [[0.0, 0.0], [0.0, 50.0]], "lanes": 1,
            "lane_widths": [3.0], "width_total": 3.0, "length": 50.0,
            "is_connector": False, "from_link": None, "to_link": None, "from_pos": None, "to_pos": None,
        },
        {
            "id": "12", "name": "Ramp", "points": [[50.0, 0.0], [0.0, 50.0]], "lanes": 1,
            "lane_widths": [3.5], "width_total": 3.5, "length": 70.0,
            "is_connector": True, "from_link": "10", "to_link": "11", "from_pos": 0.0, "to_pos": 0.0,
        },
    ]
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model(nodes=[], links=links))

    fig = graphics.network_sketch()
    root = _svg_tree(fig["svg"])
    by_id = {line.get("data-link-id"): line for line in _findall(root, "polyline")}
    wide_stroke = float(by_id["10"].get("stroke-width"))
    narrow_stroke = float(by_id["11"].get("stroke-width"))
    assert wide_stroke > narrow_stroke, "stroke width must scale with the link's own lane width, not be fixed"
    assert by_id["12"].get("data-connector") == "true"
    assert by_id["10"].get("data-connector") == "false"
    assert by_id["12"].get("class") != by_id["10"].get("class")
    assert by_id["12"].get("stroke-dasharray") != "none"
    assert by_id["10"].get("stroke-dasharray") == "none"


def test_network_sketch_absent_with_no_readable_network(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeUnreadableInpx())
    fig = graphics.network_sketch()
    assert fig["absent"] is True
    assert fig["spine_section_id"] is None
    assert "NO_READABLE_NETWORK" in fig["reason"]["ko"]


def test_network_sketch_absent_when_shadow_model_reader_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())

    def raises(path):
        raise ValueError("corrupt inpx")

    monkeypatch.setattr(tools_network, "build_shadow_model", raises)
    fig = graphics.network_sketch()
    assert fig["absent"] is True
    assert "ValueError" in fig["reason"]["en"]


def test_network_sketch_zero_nodes_and_links_is_not_an_absence(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model(nodes=[], links=[]))
    fig = graphics.network_sketch()
    assert fig["absent"] is False
    assert fig["meta"]["node_count"] == 0
    assert fig["meta"]["link_count"] == 0


def test_network_sketch_escapes_xml_special_characters_in_node_names(monkeypatch: pytest.MonkeyPatch):
    """NIT 4: network_sketch draws the node's own NAME as visible SVG text
    content (``text=str(node.get("name") or node_id)``) -- the same
    text-content escaping path as the before/after label test, exercised
    here on the third and last generator."""
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(
        tools_network,
        "build_shadow_model",
        lambda path: _shadow_model(
            nodes=[{"id": "1", "name": _XML_SPECIALS_NAME, "x": 0.0, "y": 0.0, "polygon": []}], links=[]
        ),
    )

    fig = graphics.network_sketch()
    svg = fig["svg"]

    root = _svg_tree(svg)  # raises ET.ParseError if the markup is not well-formed
    _assert_no_raw_xml_injection(svg)
    assert "&amp;" in svg
    assert "&lt;" in svg
    assert "&gt;" in svg

    label = root.find(f".//{_SVG_NS}text[@class='node-label']")
    assert label is not None
    assert label.text == _XML_SPECIALS_NAME
    marker = root.find(f".//{_SVG_NS}circle[@data-node-id='1']")
    assert marker is not None
    assert marker.get("data-node-name") == _XML_SPECIALS_NAME


# ===========================================================================
# Cross-cutting: spine binding, palette vocabulary, caption completeness,
# JSON round-trip, timestamps.
# ===========================================================================

_REAL_SPINE_IDS = {section.id for section in spine.REGULATION_SECTIONS}


def test_node_map_and_before_after_name_a_real_spine_section_id():
    for section_id in (graphics.SECTION_STATISTICAL_COMPARISON, graphics.SECTION_CALIBRATION):
        assert section_id in _REAL_SPINE_IDS


def test_an_invented_spine_section_id_is_refused():
    with pytest.raises(KeyError):
        graphics._section("not_a_real_section")


def test_network_sketch_spine_section_id_is_deliberately_none_not_invented():
    """The named-gap decision: network_sketch must not silently acquire SOME
    real-but-wrong section id. If a future edit binds it to one, this test
    (plus the golden-path assertion above) must be updated deliberately --
    that is the whole point of the gap being NAMED."""
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model())
    try:
        fig = graphics.network_sketch()
    finally:
        monkeypatch.undo()
    assert fig["spine_section_id"] is None
    assert None not in _REAL_SPINE_IDS  # sanity: "no id" is not accidentally a real id


def test_verdict_palette_keys_match_verdict_label_vocabulary():
    """Both-direction set equality -- the palette map's keys must equal the
    label map's keys must equal the declared vocabulary. A mutation adding a
    band to one dict without the other, or silently dropping one, dies here."""
    assert set(graphics.VERDICT_COLORS) == set(graphics._VERDICT_LABELS)
    assert set(graphics.VERDICT_COLORS) == graphics.VERDICT_KEYS
    assert graphics.VERDICT_KEYS == {"good", "fair", "poor", "undefined", "ungraded"}
    for key, colors in graphics.VERDICT_COLORS.items():
        assert set(colors) == {"fill", "stroke"}, key
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", colors["fill"]), key
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", colors["stroke"]), key


def test_verdict_palette_values_match_the_frontend_source_exactly():
    """Pins the ACTUAL hex values (see ``_EXPECTED_VERDICT_COLORS``'s own
    comment) rather than round-tripping ``graphics.VERDICT_COLORS`` against
    itself -- catches a mutation that swaps two bands' colours, which the
    golden-path test alone (if it read its expectation from the same dict
    under test) would not."""
    assert graphics.VERDICT_COLORS == _EXPECTED_VERDICT_COLORS


@pytest.fixture()
def all_three_golden(monkeypatch: pytest.MonkeyPatch):
    """Patch every reader so all three generators return a REAL (non-absent)
    figure, for the cross-cutting sweeps below."""
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model())

    def reader(scenario_id, **kw):
        if scenario_id in ("before-1", "scn-1"):
            return _checked_report(_GOLDEN_REPORT_ROWS)
        return _checked_report([{"node_id": "1", "node_name": "Alpha", "observed_total": 1.0, "modelled_total": 1.1, "node_total_geh": 1.5}])

    monkeypatch.setattr(tools_metrics, "build_reality_check_report", reader)

    return graphics.all_graphics(
        scenario_id="scn-1", before_scenario_id="before-1", after_scenario_id="after-1"
    )


def test_all_graphics_order_and_ids(all_three_golden):
    assert [fig["figure_id"] for fig in all_three_golden] == [
        graphics.NODE_MAP_ID,
        graphics.BEFORE_AFTER_GEH_CHART_ID,
        graphics.NETWORK_SKETCH_ID,
    ]
    assert all(fig["absent"] is False for fig in all_three_golden)


@pytest.mark.parametrize("index", [0, 1, 2], ids=["node_map", "before_after", "network_sketch"])
def test_source_caption_is_complete_on_every_real_graphic(all_three_golden, index):
    fig = all_three_golden[index]
    caption = fig["source_caption"]
    assert set(caption) == graphics._CAPTION_KEYS
    assert caption["generated_at"], "generated_at must be populated, never blank"
    assert caption["reader"], "reader must name the function(s) that produced the data"


@pytest.mark.parametrize("index", [0, 1, 2], ids=["node_map", "before_after", "network_sketch"])
def test_caption_scenario_id_survives_into_the_embedded_svg_text(all_three_golden, index):
    """DEFECT-class mutation target: drop the caption's scenario_id (either
    from the payload field or from the embedded SVG text) and this dies."""
    fig = all_three_golden[index]
    root = _svg_tree(fig["svg"])
    caption_text_el = root.find(f".//{_SVG_NS}text[@class='source-caption']")
    assert caption_text_el is not None, "every real graphic must embed a source-caption text element"
    caption_text = caption_text_el.text or ""
    expected = fig["source_caption"]["scenario_id"]
    if isinstance(expected, list):
        for scenario_id in expected:
            assert scenario_id in caption_text
    elif expected is not None:
        assert expected in caption_text
    else:
        assert "scenario_id=None" in caption_text
    assert "reader=" in caption_text
    assert "generated_at=" in caption_text
    assert "dataset_revision=" in caption_text


@pytest.mark.parametrize("index", [0, 1, 2], ids=["node_map", "before_after", "network_sketch"])
def test_generated_at_is_timezone_aware(all_three_golden, index):
    fig = all_three_golden[index]
    parsed = datetime.fromisoformat(fig["source_caption"]["generated_at"])
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() is not None


@pytest.mark.parametrize("index", [0, 1, 2], ids=["node_map", "before_after", "network_sketch"])
def test_every_graphic_round_trips_through_json(all_three_golden, index):
    fig = all_three_golden[index]
    assert json.loads(json.dumps(fig)) == fig


@pytest.mark.parametrize("index", [0, 1, 2], ids=["node_map", "before_after", "network_sketch"])
def test_every_real_graphic_svg_is_well_formed_and_carries_a_legend(all_three_golden, index):
    fig = all_three_golden[index]
    root = _svg_tree(fig["svg"])  # raises on malformed XML
    assert root.tag == f"{_SVG_NS}svg"
    legend_group = root.find(f".//{_SVG_NS}g[@class='legend']")
    assert legend_group is not None
    assert len(fig["legend"]) > 0


def test_all_graphics_degrades_the_missing_ones_without_failing_the_rest(monkeypatch: pytest.MonkeyPatch):
    """No scenario ids -> node_map and before_after are named absences,
    network_sketch (no scenario dependency) is unaffected -- mirrors
    figures.all_figures's own 'degrade one, not all' contract."""
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model())

    result = graphics.all_graphics()
    by_id = {fig["figure_id"]: fig for fig in result}
    assert by_id[graphics.NODE_MAP_ID]["absent"] is True
    assert by_id[graphics.BEFORE_AFTER_GEH_CHART_ID]["absent"] is True
    assert by_id[graphics.NETWORK_SKETCH_ID]["absent"] is False


def test_all_graphics_degrades_when_a_reader_raises_not_just_when_absent(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model())

    def raises(scenario_id, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(tools_metrics, "build_reality_check_report", raises)
    result = graphics.all_graphics(scenario_id="scn-1", before_scenario_id="b", after_scenario_id="a")
    by_id = {fig["figure_id"]: fig for fig in result}
    assert len(result) == 3
    assert by_id[graphics.NODE_MAP_ID]["absent"] is True
    assert "RuntimeError" in by_id[graphics.NODE_MAP_ID]["reason"]["en"]
    assert by_id[graphics.BEFORE_AFTER_GEH_CHART_ID]["absent"] is True
    assert by_id[graphics.NETWORK_SKETCH_ID]["absent"] is False


# ---------------------------------------------------------------------------
# Route level
# ---------------------------------------------------------------------------


@pytest.fixture()
def client() -> TestClient:
    with TestClient(main.app) as test_client:
        yield test_client


def test_route_returns_three_graphics_with_no_query_params(client: TestClient):
    response = client.get("/api/report/graphics")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [fig["figure_id"] for fig in body["graphics"]] == [
        graphics.NODE_MAP_ID,
        graphics.BEFORE_AFTER_GEH_CHART_ID,
        graphics.NETWORK_SKETCH_ID,
    ]
    # No scenario_id/before/after on a fresh workspace -> node_map and
    # before_after are, at minimum, named absences -- never a 4xx.
    assert body["graphics"][0]["absent"] is True
    assert body["graphics"][1]["absent"] is True


def test_route_stays_200_when_a_reader_raises(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    def raises(scenario_id, **kw):
        raise RuntimeError("workspace lock held")

    monkeypatch.setattr(tools_metrics, "build_reality_check_report", raises)
    response = client.get("/api/report/graphics", params={"scenario_id": "scn-1"})
    assert response.status_code == 200, response.text
    figs = {fig["figure_id"]: fig for fig in response.json()["graphics"]}
    assert figs[graphics.NODE_MAP_ID]["absent"] is True
    assert "RuntimeError" in figs[graphics.NODE_MAP_ID]["reason"]["en"]
    assert set(figs) == {graphics.NODE_MAP_ID, graphics.BEFORE_AFTER_GEH_CHART_ID, graphics.NETWORK_SKETCH_ID}


def test_route_is_unauthenticated_like_its_neighbors_on_this_router(client: TestClient):
    """No Authorization header, no 401/403 -- parity with GET /figures and
    GET /spine on this same router (see routers/report.py's own docstring
    on this route for the precedent)."""
    response = client.get("/api/report/graphics")
    assert response.status_code == 200


def test_route_golden_path_through_all_readers(monkeypatch: pytest.MonkeyPatch, client: TestClient):
    monkeypatch.setattr(datasets, "get_active_inpx_path", lambda: FakeReadableInpx())
    monkeypatch.setattr(tools_network, "build_shadow_model", lambda path: _shadow_model())
    monkeypatch.setattr(
        tools_metrics,
        "build_reality_check_report",
        _before_after_reader(_GOLDEN_REPORT_ROWS, _GOLDEN_REPORT_ROWS),
    )
    response = client.get(
        "/api/report/graphics",
        params={"scenario_id": "before-1", "before_scenario_id": "before-1", "after_scenario_id": "after-1"},
    )
    assert response.status_code == 200, response.text
    figs = response.json()["graphics"]
    assert all(fig["absent"] is False for fig in figs)


def test_route_not_in_test_auth_core_guarded_set():
    """This route is deliberately unauthenticated (parity with GET /figures,
    GET /spine) -- so it must NOT appear in test_auth_core.py's declared
    guarded-route set. Reuses that file's own ``_dependency_callables``
    helper rather than a second traversal implementation, so the two tests
    cannot silently disagree about what "guarded" means."""
    from routers import auth as auth_router
    from tests.test_auth_core import _dependency_callables

    guarded = set()
    for route in main.app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        calls = _dependency_callables(dependant)
        if auth_router.current_user in calls or auth_router.optional_current_user in calls:
            guarded.add(getattr(route, "path", str(route)))

    assert "/api/report/graphics" not in guarded

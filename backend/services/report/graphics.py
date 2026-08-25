"""Track R unit R-3 — three GRAPHIC figure generators
(docs/product/report-engine-design-2026-08-24.md, layer 2's remaining rows;
``services/report/spine.py`` is layer 1, ``services/report/figures.py`` (R-2)
is the sibling TABLE-figure module and its doctrine is BINDING here — this
module only adapts it from "rows" to "SVG".

WHAT A GRAPHIC IS
-----------------
The same two-shape contract R-2 established, adapted for a rendered picture
instead of a row table:

* a real figure — ``{figure_id, spine_section_id, absent: False, title
  (ko/en/ja), svg (a complete ``<svg>...</svg>`` string), legend (a JSON list
  of verdict/series swatches, ko/en/ja), source_caption, citation, meta}``.
  ``source_caption`` is MANDATORY (the design doc's rule quoted in R-2's own
  docstring: "이 그림은 어느 실행의 것인가"에 답 못 하는 도판은 방출하지 않는다)
  and is embedded BOTH as payload fields (for a caller that never parses the
  SVG) AND as a ``<text class="source-caption">`` element inside the SVG
  itself (for a caller — a DOCX export, a printed page — that only ever sees
  the picture). ``meta`` is this module's addition over R-2's shape: a
  JSON-native summary of the structural facts the SVG draws (node/link
  counts, verdict tallies, which node ids are one-sided, …), so a test — or a
  future UI badge — never has to re-parse SVG geometry to answer "how many
  nodes did this map grade".
* a NAMED ABSENCE — ``{figure_id, spine_section_id, absent: True, reason
  (ko/en/ja)}``, identical in shape to R-2's, for the identical reason: a
  reader that found nothing and a reader that was never asked are different
  facts and must render differently. An existing-but-empty network (zero
  nodes, zero links) is NOT an absence — it is a real, empty picture — mirror
  of R-2's ``table_5_6_applied_table`` "zero behaviours is not an absence"
  precedent.

CAPTION SHAPE — WHY IT DIFFERS FROM R-2's
------------------------------------------
R-2's ``_caption`` carries a fixed ``{run_id, session_id, dataset_revision,
generated_at, reader}``. None of this module's three figures touches a
session, and one of them (:func:`before_after_geh_chart`) is tied to TWO
runs, not one — the design brief's own words are "the figure states both
scenario ids". Rather than force a two-run figure through a singular
``run_id`` key, this module declares its OWN fixed caption key set —
``{scenario_id, dataset_revision, generated_at, reader}`` — held constant
across all three generators here (:data:`_CAPTION_KEYS`) so a caller can
still check completeness without a per-figure branch, exactly as R-2's
docstring asks. ``scenario_id`` is a single string for
:func:`node_map`, a ``[before, after]`` two-element list for
:func:`before_after_geh_chart`, and ``None`` for :func:`network_sketch`
(a network sketch is not scoped to any one run — see that function's own
docstring for why ``None`` there is a legitimate value, not a missing key).

READERS REUSED, NEVER RE-IMPLEMENTED
-------------------------------------
Every generator's docstring names the exact function(s) that do the actual
read. :func:`node_map` and :func:`network_sketch` read network geometry via
``services.agent.tools_network.build_shadow_model`` — the SAME reader R-2's
``table_5_6_applied_table`` uses, resolved the SAME way (``services.datasets.
get_active_inpx_path`` -> ``build_shadow_model``, mtime-cached, no COM;
``services/`` may not import ``routers/``, so the "which network" glue is the
small local duplication R-2's module docstring already explains and
justifies). :func:`node_map` and :func:`before_after_geh_chart` read verdicts
via ``services.agent.tools_metrics.build_reality_check_report`` — the SAME
reader R-2's ``geh_node_table`` uses, imported lazily inside each function
body for the identical reason ``geh_node_table`` does (importing
``tools_metrics`` at module scope reorders tool registration in
``default_registry``).

EVERY READER CALL IS GUARDED, NAMED ABSENCE ON RAISE
------------------------------------------------------
Exactly R-2's rule, unchanged: an exception out of any reader becomes a named
absence naming the exception class and message — never a bare 500 that takes
the other two graphics down with it. :func:`all_graphics` gathers all three
independently (mirrors R-2's ``all_figures`` / ``routers/calibration.
get_flow_rail``'s "degrade one, not all" shape) — see that function's own
docstring for the choice to extend rather than fork R-2's aggregator name.

SVG DETERMINISM
----------------
Every SVG is built with :mod:`xml.etree.ElementTree` (attribute insertion
order is preserved since Python 3.8, and this module always writes attributes
in the same order for the same figure) and every float coordinate is
formatted through :func:`_fmt` (fixed two decimals, never Python's ``repr``
variability). Every list this module iterates to build markup — shadow
nodes, shadow links, before/after node ids — is SORTED first
(:func:`_sort_key`, numeric-aware). The one input that legitimately varies
call to call is ``generated_at`` (the server clock, exactly
``services/report/figures.py``'s ``_generated_at`` — same
``datetime.now(timezone.utc).isoformat()`` call ``services/agent/
reality_check.py`` and ``services/genesis_jobs.py``'s ``_now_iso`` both use):
two calls with identical readers produce byte-identical SVG apart from that
one embedded string.

VERDICT PALETTE — TRANSCRIBED, NOT INVENTED
----------------------------------------------
:data:`VERDICT_COLORS` is copied verbatim (fill + stroke, per band) from the
frontend's OWN GEH-verdict marker/legend palette —
``frontend/components/BaseValidationMap.tsx``: the per-node marker fill/
stroke in ``resolveNodeStyle`` (lines 438-455) and the same hexes repeated in
``buildLegend`` (lines 525-535). Five bands, not R-2's binary PASS/FAIL:
good (GEH<5) ``#16a34a``/``#166534``, fair (5<=GEH<10) ``#f59e0b``/
``#92400e``, poor (GEH>=10) ``#dc2626``/``#7f1d1d``, undefined (GEH computed
but not finite/defined) ``#94a3b8``/``#64748b``, and — the one this module
newly needs, because a spatial map has to draw a node the reality-check
never mentioned at all — "no validation data" ``#f8fafc``/``#64748b``, the
SAME pair that file uses (lines 426-427) for a node absent from the report
entirely. That fifth band is renamed ``"ungraded"`` here because "no
validation data" is this module's exact R-2-worst-case-discipline case: a
node present in the network but never graded is drawn, explicitly, in this
style — never omitted, never colored as passing.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from services import datasets, validation
from services.agent import tools_network
from services.report import spine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Spine section ids — validated against R-1's own declaration, never re-typed
# blind (identical mechanism to figures.py's ``_section``; a fresh, local
# copy rather than importing R-2's private helper — this module does not
# depend on figures.py at all, the same "small local duplication over a
# cross-module private import" call R-2's own docstring explains for its
# routers/ glue).
# ---------------------------------------------------------------------------

_SPINE_SECTION_IDS = {section.id for section in spine.REGULATION_SECTIONS}


def _section(section_id: str) -> str:
    if section_id not in _SPINE_SECTION_IDS:
        raise KeyError(
            f"report graphic names spine section {section_id!r}, which "
            f"services.report.spine does not declare (it has: {sorted(_SPINE_SECTION_IDS)})"
        )
    return section_id


SECTION_STATISTICAL_COMPARISON = _section("statistical_comparison")
SECTION_CALIBRATION = _section("calibration")

#: :func:`network_sketch`'s deliberate, NAMED gap — read the function's own
#: docstring for the full reasoning. Exposed as a module constant (not just
#: prose) so a caller — the future /report UI, a completeness audit — can
#: read the gap the same mechanical way it reads everything else here,
#: instead of a comment only a person reading source would ever see.
NETWORK_SKETCH_SECTION_GAP = (
    "network_sketch names no REGULATION_SECTIONS id. MRWA OMG v2.0's "
    "§2.12.2/§5.8 extract (docs/product/reference/mrwa-reporting-"
    "requirements.md) never requires a network TOPOLOGY diagram — none of "
    "spine.py's eight regulation-derived sections is 'here is the network "
    "we built' (the two that sound closest, model_evaluation_results and "
    "network_wide_performance, are both Table 5-15 PERFORMANCE-output rows "
    "— VEHARR/DISTTOT/TRAVTMTOT/etc — not a topology sketch). Forcing this "
    "figure onto one of the eight would be the exact invented-capability "
    "claim spine.py's own docstring refuses. Closing this gap is a house "
    "addition via spine.build_spine(extra_sections=...), left to whichever "
    "unit owns that call; this module edits spine.py from nowhere."
)


# ---------------------------------------------------------------------------
# Shared shape builders — same discipline as figures.py's, adapted for SVG.
# ---------------------------------------------------------------------------

_CAPTION_KEYS = frozenset({"scenario_id", "dataset_revision", "generated_at", "reader"})


def _generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _active_dataset_revision() -> str | None:
    """Verbatim copy of ``services.report.figures._active_dataset_revision``
    (never raises; a caption field that cannot be filled is ``None``, not a
    500 for the whole figure) — kept local rather than imported so this
    module has no dependency on its sibling, mirroring that module's own
    "small local duplication" choice."""
    try:
        revision = datasets.get_active_dataset().get("revision")
    except Exception as exc:  # noqa: BLE001 — a caption field, not a fault
        logger.warning("report.graphics: active dataset unreadable (%s: %s)", type(exc).__name__, exc)
        return None
    return str(revision) if revision else None


def _caption(*, scenario_id: str | list[str] | None, reader: str) -> dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "dataset_revision": _active_dataset_revision(),
        "generated_at": _generated_at(),
        "reader": reader,
    }


def _reason(ko: str, en: str, ja: str) -> dict[str, str]:
    return {"ko": ko, "en": en, "ja": ja}


def _absent(figure_id: str, section_id: str | None, reason: dict[str, str]) -> dict[str, Any]:
    return {
        "figure_id": figure_id,
        "spine_section_id": section_id,
        "absent": True,
        "reason": reason,
    }


def _graphic(
    *,
    figure_id: str,
    section_id: str | None,
    title: dict[str, str],
    svg: str,
    legend: list[dict[str, str]],
    source_caption: dict[str, Any],
    citation: str | None,
    meta: dict[str, Any],
) -> dict[str, Any]:
    return {
        "figure_id": figure_id,
        "spine_section_id": section_id,
        "absent": False,
        "title": title,
        "svg": svg,
        "legend": legend,
        "source_caption": source_caption,
        "citation": citation,
        "meta": meta,
    }


# ---------------------------------------------------------------------------
# Verdict palette — see the module docstring's "VERDICT PALETTE" section for
# the transcription source (frontend/components/BaseValidationMap.tsx).
# ---------------------------------------------------------------------------

VERDICT_COLORS: dict[str, dict[str, str]] = {
    "good": {"fill": "#16a34a", "stroke": "#166534"},
    "fair": {"fill": "#f59e0b", "stroke": "#92400e"},
    "poor": {"fill": "#dc2626", "stroke": "#7f1d1d"},
    "undefined": {"fill": "#94a3b8", "stroke": "#64748b"},
    "ungraded": {"fill": "#f8fafc", "stroke": "#64748b"},
}

_VERDICT_LABELS: dict[str, dict[str, str]] = {
    "good": {"ko": "양호 (GEH<5)", "en": "Good (GEH<5)", "ja": "良好 (GEH<5)"},
    "fair": {"ko": "보통 (5≤GEH<10)", "en": "Fair (5<=GEH<10)", "ja": "普通 (5≤GEH<10)"},
    "poor": {"ko": "미흡 (GEH≥10)", "en": "Poor (GEH>=10)", "ja": "不良 (GEH≥10)"},
    "undefined": {"ko": "판정불가", "en": "Undefined GEH", "ja": "判定不可"},
    "ungraded": {"ko": "미채점 (근거 없음)", "en": "Ungraded (no evidence)", "ja": "未採点(根拠なし)"},
}

#: Vocabulary this module grades a node into — palette keys and label keys
#: MUST agree (a test asserts the set equality both ways); a mutation that
#: adds a band to one dict without the other is caught there.
VERDICT_KEYS = frozenset(VERDICT_COLORS)


def _verdict_legend_entries(keys: list[str]) -> list[dict[str, str]]:
    entries = []
    for key in keys:
        colors = VERDICT_COLORS[key]
        labels = _VERDICT_LABELS[key]
        entries.append({"key": key, **labels, **colors})
    return entries


# ---------------------------------------------------------------------------
# SVG plumbing — shared by all three generators.
# ---------------------------------------------------------------------------

_SVG_NS = "http://www.w3.org/2000/svg"


def _fmt(value: float) -> str:
    """Deterministic float formatting: always two decimals, never Python's
    ``repr``-dependent ``str(float)`` (whose output has changed across
    Python versions and is never guaranteed byte-stable)."""
    return f"{float(value):.2f}"


def _sort_key(raw_id: Any) -> tuple[int, Any]:
    """Numeric-aware sort key: ``"2"`` before ``"10"`` when both are digit
    strings, falling back to lexical order otherwise -- used everywhere this
    module iterates node/link ids, so the same input always yields the same
    drawing order (SVG determinism) regardless of dict/JSON ordering
    upstream."""
    text = str(raw_id)
    if text.isdigit():
        return (0, int(text))
    return (1, text)


def _svg_root(view_box: str, *, width: int, height: int) -> ET.Element:
    return ET.Element(
        "svg",
        {
            "xmlns": _SVG_NS,
            "viewBox": view_box,
            "width": str(width),
            "height": str(height),
            "font-family": "sans-serif",
        },
    )


def _sub(parent: ET.Element, tag: str, attrib: dict[str, str] | None = None, text: str | None = None) -> ET.Element:
    el = ET.SubElement(parent, tag, attrib or {})
    if text is not None:
        el.text = text
    return el


def _bounds(points: list[tuple[float, float]], *, margin_ratio: float = 0.10) -> tuple[float, float, float, float]:
    """``(min_x, min_y, width, height)`` for a viewBox, with margin.

    Degenerates gracefully to a fixed unit square when ``points`` is empty
    (a network with zero nodes is a real, empty result — see
    ``services.report.figures.table_5_6_applied_table``'s "zero behaviours
    is not an absence" precedent -- never a crash)."""
    if not points:
        return (0.0, 0.0, 100.0, 100.0)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = (max_x - min_x) or 1.0
    height = (max_y - min_y) or 1.0
    margin = max(width, height) * margin_ratio or 1.0
    return (min_x - margin, min_y - margin, width + 2 * margin, height + 2 * margin)


def _project(x: float, y: float) -> tuple[float, float]:
    """Network (x, y) -> SVG plotting coordinates. Vissim's raw XML plane is
    a standard y-up Cartesian plane (``services.inpx_parser`` stores node/
    link points verbatim, no axis flip); SVG's y-axis increases downward, so
    y is negated here to keep the drawn network right-side up rather than
    mirrored top-to-bottom."""
    return (x, -y)


def _embed_caption(svg_root: ET.Element, caption: dict[str, Any], *, x: float, y: float) -> None:
    scenario = caption["scenario_id"]
    scenario_text = ", ".join(scenario) if isinstance(scenario, list) else str(scenario)
    line = (
        f"scenario_id={scenario_text} | dataset_revision={caption['dataset_revision']} | "
        f"generated_at={caption['generated_at']} | reader={caption['reader']}"
    )
    _sub(
        svg_root,
        "text",
        {"x": _fmt(x), "y": _fmt(y), "class": "source-caption", "font-size": "10"},
        text=line,
    )


def _embed_legend(svg_root: ET.Element, entries: list[dict[str, str]], *, x: float, y: float) -> None:
    group = _sub(svg_root, "g", {"class": "legend"})
    row_h = 16.0
    for index, entry in enumerate(entries):
        row_y = y + index * row_h
        _sub(
            group,
            "rect",
            {
                "class": "legend-swatch",
                "data-key": entry["key"],
                "x": _fmt(x),
                "y": _fmt(row_y),
                "width": "12",
                "height": "12",
                "fill": entry["fill"],
                "stroke": entry["stroke"],
            },
        )
        _sub(
            group,
            "text",
            {"x": _fmt(x + 16.0), "y": _fmt(row_y + 10.0), "font-size": "10"},
            text=entry["ko"],
        )


def _render(root: ET.Element) -> str:
    return ET.tostring(root, encoding="unicode")


# ---------------------------------------------------------------------------
# 1. 노드 지도 (판정 칠) — statistical_comparison
# ---------------------------------------------------------------------------

NODE_MAP_ID = "node_map"

_NODE_MAP_TITLE = {"ko": "노드 지도 (판정 칠)", "en": "Node map (verdict paint)", "ja": "ノードマップ(判定塗り)"}
NODE_MAP_READER = "services.agent.tools_network.build_shadow_model + services.agent.tools_metrics.build_reality_check_report"


def node_map(
    scenario_id: str | None,
    *,
    period: str | None = None,
    observation_set_id: str | None = None,
    observation_period: str | None = None,
) -> dict[str, Any]:
    """Shadow-model nodes plotted at their real x/y, coloured by the
    reality-check verdict for that node (statistical_comparison — the SAME
    section :func:`services.report.figures.geh_node_table` binds to, since
    this is a spatial rendering of the identical evidence).

    Two readers, both reused verbatim, both independently guarded:

    * ``services.agent.tools_network.build_shadow_model`` (via
      ``services.datasets.get_active_inpx_path``, mtime-cached, no COM) for
      node positions (``x``/``y``) and the link skeleton drawn underneath for
      context — the SAME resolution
      :func:`services.report.figures.table_5_6_applied_table` uses, so a
      missing/unreadable network fails with the identical
      ``NO_READABLE_NETWORK`` code that figure already answers with.
    * ``services.agent.tools_metrics.build_reality_check_report`` (imported
      lazily, same anti-registration-reorder reason
      :func:`services.report.figures.geh_node_table` states) for the
      per-node verdict. ``scenario_id`` absent, or the report's own
      ``status`` is ``NOT_CHECKABLE`` -> a NAMED ABSENCE carrying the
      report's own ``message`` verbatim, exactly like ``geh_node_table``.

    EVERY shadow node is drawn — never only the graded ones. A node the
    reality-check report never mentions is drawn in the explicit
    ``"ungraded"`` style (never omitted, never coloured as passing — R-2's
    worst-case discipline, restated for a picture instead of a row). A graded
    node whose GEH is itself undefined (0 observed and 0 modelled — see
    ``services.validation.geh``'s own docstring) is drawn ``"undefined"``,
    distinct from both a passing grade and from "never graded at all".
    """
    if not scenario_id:
        return _absent(
            NODE_MAP_ID,
            SECTION_STATISTICAL_COMPARISON,
            _reason(
                "실행(scenario_id)이 지정되지 않아 노드 지도를 만들 수 없습니다.",
                "No scenario_id was supplied, so no node map can be built.",
                "実行(scenario_id)が指定されていないため、ノードマップを作成できません。",
            ),
        )

    try:
        path = datasets.get_active_inpx_path()
    except Exception as exc:  # noqa: BLE001 — no dataset configured at all
        logger.info("report.graphics: no active inpx path (%s: %s)", type(exc).__name__, exc)
        path = None

    shadow: dict[str, Any] | None = None
    if path is not None and path.is_file():
        try:
            shadow = tools_network.build_shadow_model(path)
        except Exception as exc:  # noqa: BLE001 — an unparseable .inpx is evidence, not a 500
            logger.warning("report.graphics: shadow model unavailable (%s: %s)", type(exc).__name__, exc)
            return _absent(
                NODE_MAP_ID,
                SECTION_STATISTICAL_COMPARISON,
                _reason(
                    f"네트워크를 읽는 리더가 오류를 내며 실패했습니다 ({type(exc).__name__}: {exc}).",
                    f"The reader that reads the network raised an error ({type(exc).__name__}: {exc}).",
                    f"ネットワークを読み込むリーダーがエラーで失敗しました ({type(exc).__name__}: {exc})。",
                ),
            )

    nodes_raw = shadow.get("nodes") if isinstance(shadow, dict) else None
    if not isinstance(nodes_raw, list):
        return _absent(
            NODE_MAP_ID,
            SECTION_STATISTICAL_COMPARISON,
            _reason(
                "읽을 수 있는 네트워크(.inpx)가 없어 노드 지도를 만들 수 없습니다 (NO_READABLE_NETWORK).",
                "There is no readable network (.inpx), so no node map can be built (NO_READABLE_NETWORK).",
                "読み取れるネットワーク(.inpx)がないため、ノードマップを作成できません(NO_READABLE_NETWORK)。",
            ),
        )
    links_raw = shadow.get("links") if isinstance(shadow.get("links"), list) else []

    from services.agent.tools_metrics import build_reality_check_report

    try:
        report = build_reality_check_report(
            scenario_id,
            period=period or None,
            targets=None,
            observation_set_id=observation_set_id or None,
            observation_period=observation_period or None,
        )
    except Exception as exc:  # noqa: BLE001 — a crashed reader is a named absence, not a 500
        logger.warning("report.graphics: build_reality_check_report raised (%s: %s)", type(exc).__name__, exc)
        return _absent(
            NODE_MAP_ID,
            SECTION_STATISTICAL_COMPARISON,
            _reason(
                f"판정을 읽는 리더가 오류를 내며 실패했습니다 ({type(exc).__name__}: {exc}).",
                f"The reader that grades each node raised an error ({type(exc).__name__}: {exc}).",
                f"判定を読み込むリーダーがエラーで失敗しました ({type(exc).__name__}: {exc})。",
            ),
        )

    if report.get("status") != "CHECKED":
        return _absent(NODE_MAP_ID, SECTION_STATISTICAL_COMPARISON, dict(report.get("message") or {}))

    report_nodes = {str(node.get("node_id")): node for node in (report.get("nodes") or [])}
    shadow_ids = {str(node.get("id")) for node in nodes_raw}
    verdicts_without_geometry = sorted((set(report_nodes) - shadow_ids), key=_sort_key)

    projected = [_project(float(n.get("x", 0.0)), float(n.get("y", 0.0))) for n in nodes_raw]
    for link in links_raw:
        for px, py in (link.get("points") or []):
            projected.append(_project(float(px), float(py)))
    min_x, min_y, width, height = _bounds(projected)
    view_box = f"{_fmt(min_x)} {_fmt(min_y)} {_fmt(width)} {_fmt(height)}"

    root = _svg_root(view_box, width=900, height=700)

    link_group = _sub(root, "g", {"class": "link-skeleton"})
    for link in sorted(links_raw, key=lambda entry: _sort_key(entry.get("id"))):
        pts = link.get("points") or []
        if len(pts) < 2:
            continue
        proj_pts = [_project(float(px), float(py)) for px, py in pts]
        points_attr = " ".join(f"{_fmt(px)},{_fmt(py)}" for px, py in proj_pts)
        is_connector = bool(link.get("is_connector"))
        _sub(
            link_group,
            "polyline",
            {
                # "link"/"connector" — same two-way vocabulary
                # services/genesis/geojson.py's KIND_LINK/KIND_CONNECTOR use.
                "class": "connector" if is_connector else "link",
                "data-link-id": str(link.get("id", "")),
                "data-connector": "true" if is_connector else "false",
                "points": points_attr,
                "fill": "none",
                "stroke": "#cbd5e1" if not is_connector else "#94a3b8",
                "stroke-width": "1",
                "stroke-dasharray": "4,3" if is_connector else "none",
                "opacity": "0.7",
            },
        )

    verdict_counts = {key: 0 for key in VERDICT_KEYS}
    node_group = _sub(root, "g", {"class": "nodes"})
    for node in sorted(nodes_raw, key=lambda entry: _sort_key(entry.get("id"))):
        node_id = str(node.get("id", ""))
        report_node = report_nodes.get(node_id)
        if report_node is None:
            verdict_key = "ungraded"
            geh_value = None
        else:
            grade = validation.geh_grade(report_node.get("node_total_geh"))
            verdict_key = grade if grade is not None else "undefined"
            geh_value = report_node.get("node_total_geh")
        verdict_counts[verdict_key] += 1
        colors = VERDICT_COLORS[verdict_key]
        cx, cy = _project(float(node.get("x", 0.0)), float(node.get("y", 0.0)))
        _sub(
            node_group,
            "circle",
            {
                "class": "node-marker",
                "data-node-id": node_id,
                "data-node-name": str(node.get("name", "")),
                "data-verdict": verdict_key,
                "data-geh": "" if geh_value is None else _fmt(geh_value),
                "cx": _fmt(cx),
                "cy": _fmt(cy),
                "r": "7",
                "fill": colors["fill"],
                "stroke": colors["stroke"],
                "stroke-width": "1.5",
            },
        )
        _sub(
            node_group,
            "text",
            {"x": _fmt(cx + 9.0), "y": _fmt(cy + 3.0), "font-size": "9", "class": "node-label"},
            text=node_id,
        )

    legend_entries = _verdict_legend_entries(sorted(VERDICT_KEYS))
    _embed_legend(root, legend_entries, x=min_x + width * 0.02, y=min_y + height * 0.04)

    caption = _caption(scenario_id=scenario_id, reader=NODE_MAP_READER)
    _embed_caption(root, caption, x=min_x + width * 0.02, y=min_y + height * 0.98)

    meta = {
        "scenario_id": scenario_id,
        "total_node_count": len(nodes_raw),
        "graded_node_count": sum(count for key, count in verdict_counts.items() if key not in ("ungraded",)),
        "ungraded_node_count": verdict_counts["ungraded"],
        "verdict_counts": verdict_counts,
        "verdicts_without_geometry": verdicts_without_geometry,
        "link_count": len(links_raw),
        "connector_count": sum(1 for link in links_raw if link.get("is_connector")),
    }

    return _graphic(
        figure_id=NODE_MAP_ID,
        section_id=SECTION_STATISTICAL_COMPARISON,
        title=_NODE_MAP_TITLE,
        svg=_render(root),
        legend=legend_entries,
        source_caption=caption,
        citation=spine.CIT_2_12_2_STATISTICAL_COMPARISON,
        meta=meta,
    )


# ---------------------------------------------------------------------------
# 2. 전후 비교 (GEH 막대) — calibration
# ---------------------------------------------------------------------------

BEFORE_AFTER_GEH_CHART_ID = "before_after_geh_chart"

_BEFORE_AFTER_TITLE = {"ko": "전후 비교 (노드별 GEH)", "en": "Before/after comparison (GEH by node)", "ja": "前後比較(ノード別GEH)"}
BEFORE_AFTER_READER = "services.agent.tools_metrics.build_reality_check_report"


def before_after_geh_chart(
    before_scenario_id: str | None,
    after_scenario_id: str | None,
    *,
    period: str | None = None,
    observation_set_id: str | None = None,
    observation_period: str | None = None,
) -> dict[str, Any]:
    """Per-node GEH, before vs after, as grouped bars (calibration).

    Calls ``build_reality_check_report`` TWICE — once per scenario, both with
    the SAME ``period``/``observation_set_id``/``observation_period`` so the
    comparison is apples-to-apples — and never inner-joins the two node sets.
    A node graded in only one of the two runs still appears on the chart,
    flagged ``one_sided`` (never silently dropped, the exact discipline R-2's
    ``approval_history_table``/``geh_node_table`` hold for missing rows,
    restated here for a missing SIDE of a row instead).

    Both ``before_scenario_id`` and ``after_scenario_id`` are required -- a
    comparison naming only one run cannot state "before what" or "after
    what", so either missing -> NAMED ABSENCE (naming which one). Either
    reader raising, or either report coming back other than ``CHECKED``,
    likewise becomes a named absence -- tagged with WHICH side ("이전"/
    "이후") failed, so the reason is never ambiguous about which run
    is the problem.
    """
    if not before_scenario_id or not after_scenario_id:
        missing = []
        if not before_scenario_id:
            missing.append("before_scenario_id")
        if not after_scenario_id:
            missing.append("after_scenario_id")
        return _absent(
            BEFORE_AFTER_GEH_CHART_ID,
            SECTION_CALIBRATION,
            _reason(
                f"비교할 실행이 모두 지정되지 않아 전후 비교를 만들 수 없습니다 (누락: {', '.join(missing)}).",
                f"Not every scenario_id needed for the comparison was supplied "
                f"(missing: {', '.join(missing)}).",
                f"比較に必要な実行がすべて指定されていないため、前後比較を作成できません"
                f"(不足: {', '.join(missing)})。",
            ),
        )

    from services.agent.tools_metrics import build_reality_check_report

    def _run(label_ko: str, label_en: str, label_ja: str, scenario_id: str):
        try:
            report = build_reality_check_report(
                scenario_id,
                period=period or None,
                targets=None,
                observation_set_id=observation_set_id or None,
                observation_period=observation_period or None,
            )
        except Exception as exc:  # noqa: BLE001 — a crashed reader is a named absence, not a 500
            logger.warning(
                "report.graphics: build_reality_check_report raised for %s (%s: %s)",
                label_en, type(exc).__name__, exc,
            )
            return None, _reason(
                f"[{label_ko}] 판정을 읽는 리더가 오류를 내며 실패했습니다 ({type(exc).__name__}: {exc}).",
                f"[{label_en}] The reader that grades each node raised an error "
                f"({type(exc).__name__}: {exc}).",
                f"[{label_ja}] 判定を読み込むリーダーがエラーで失敗しました ({type(exc).__name__}: {exc})。",
            )
        if report.get("status") != "CHECKED":
            message = dict(report.get("message") or {})
            if not message:
                message = _reason(
                    f"[{label_ko}] 판정 결과를 확인할 수 없습니다.",
                    f"[{label_en}] The check result could not be confirmed.",
                    f"[{label_ja}] 判定結果を確認できません。",
                )
            else:
                message = {
                    lang: f"[{label_ko if lang == 'ko' else (label_en if lang == 'en' else label_ja)}] {text}"
                    for lang, text in message.items()
                }
            return None, message
        return report, None

    before_report, before_err = _run("이전", "before", "以前", before_scenario_id)
    if before_err is not None:
        return _absent(BEFORE_AFTER_GEH_CHART_ID, SECTION_CALIBRATION, before_err)
    after_report, after_err = _run("이후", "after", "以後", after_scenario_id)
    if after_err is not None:
        return _absent(BEFORE_AFTER_GEH_CHART_ID, SECTION_CALIBRATION, after_err)

    before_nodes = {str(n.get("node_id")): n for n in (before_report.get("nodes") or [])}
    after_nodes = {str(n.get("node_id")): n for n in (after_report.get("nodes") or [])}
    all_ids = sorted(set(before_nodes) | set(after_nodes), key=_sort_key)

    rows: list[dict[str, Any]] = []
    one_sided_ids: list[str] = []
    for node_id in all_ids:
        before_node = before_nodes.get(node_id)
        after_node = after_nodes.get(node_id)
        before_present = before_node is not None
        after_present = after_node is not None
        one_sided = before_present != after_present
        if one_sided:
            one_sided_ids.append(node_id)
        before_geh = before_node.get("node_total_geh") if before_present else None
        after_geh = after_node.get("node_total_geh") if after_present else None
        rows.append(
            {
                "node_id": node_id,
                "node_name": (before_node or after_node or {}).get("node_name"),
                "geh_before": before_geh,
                "geh_after": after_geh,
                "before_present": before_present,
                "after_present": after_present,
                "one_sided": one_sided,
                "verdict_before": validation.geh_grade(before_geh) if before_present else None,
                "verdict_after": validation.geh_grade(after_geh) if after_present else None,
            }
        )

    caption = _caption(scenario_id=[before_scenario_id, after_scenario_id], reader=BEFORE_AFTER_READER)
    svg, legend_entries = _render_before_after_svg(rows, caption)

    meta = {
        "before_scenario_id": before_scenario_id,
        "after_scenario_id": after_scenario_id,
        "node_count": len(all_ids),
        "one_sided_node_ids": one_sided_ids,
        "before_only_node_ids": sorted((set(before_nodes) - set(after_nodes)), key=_sort_key),
        "after_only_node_ids": sorted((set(after_nodes) - set(before_nodes)), key=_sort_key),
        "rows": rows,
    }

    return _graphic(
        figure_id=BEFORE_AFTER_GEH_CHART_ID,
        section_id=SECTION_CALIBRATION,
        title=_BEFORE_AFTER_TITLE,
        svg=svg,
        legend=legend_entries,
        source_caption=caption,
        citation=spine.CIT_2_12_2_DATASET_COMPARISON,
        meta=meta,
    )


def _render_before_after_svg(
    rows: list[dict[str, Any]], caption: dict[str, Any]
) -> tuple[str, list[dict[str, str]]]:
    """Grouped bar chart: two bars per node (before/after), each coloured by
    ITS OWN GEH grade (:data:`VERDICT_COLORS`) — reusing the exact palette
    :func:`node_map` uses, per this module's "reuse the exact GEH-grade
    palette, do not invent one" rule. A missing side draws a short
    ``"ungraded"``-styled marker rather than a misleading zero-height bar (a
    0-height bar would read as "GEH=0", i.e. a perfect match, which is the
    opposite of "no evidence")."""
    slot_w = 70.0
    margin_left, margin_right = 60.0, 40.0
    margin_top, margin_bottom = 30.0, 90.0
    chart_h = 260.0
    width = margin_left + margin_right + slot_w * max(len(rows), 1)
    height = margin_top + chart_h + margin_bottom

    present_values = [v for row in rows for v in (row["geh_before"], row["geh_after"]) if v is not None]
    max_geh = max([*present_values, validation.GEH_FAIR_LT]) * 1.15

    root = _svg_root(f"0 0 {_fmt(width)} {_fmt(height)}", width=int(width), height=int(height))

    baseline_y = margin_top + chart_h
    axis_group = _sub(root, "g", {"class": "axis"})
    _sub(
        axis_group,
        "line",
        {
            "x1": _fmt(margin_left), "y1": _fmt(baseline_y),
            "x2": _fmt(width - margin_right), "y2": _fmt(baseline_y),
            "stroke": "#334155", "stroke-width": "1",
        },
    )
    for threshold, label in ((validation.GEH_GOOD_LT, "GEH 5"), (validation.GEH_FAIR_LT, "GEH 10")):
        ref_y = baseline_y - (threshold / max_geh) * chart_h
        _sub(
            axis_group,
            "line",
            {
                "class": "geh-threshold",
                "x1": _fmt(margin_left), "y1": _fmt(ref_y),
                "x2": _fmt(width - margin_right), "y2": _fmt(ref_y),
                "stroke": "#94a3b8", "stroke-width": "0.5", "stroke-dasharray": "3,3",
            },
        )
        _sub(axis_group, "text", {"x": "4", "y": _fmt(ref_y + 3.0), "font-size": "8"}, text=label)

    bars_group = _sub(root, "g", {"class": "bars"})
    bar_w = 20.0
    for index, row in enumerate(rows):
        slot_x = margin_left + index * slot_w
        node_id = row["node_id"]
        for offset, side, geh_value, present in (
            (6.0, "before", row["geh_before"], row["before_present"]),
            (6.0 + bar_w + 4.0, "after", row["geh_after"], row["after_present"]),
        ):
            bar_x = slot_x + offset
            if present and geh_value is not None:
                grade = validation.geh_grade(geh_value)
                verdict_key = grade if grade is not None else "undefined"
                bar_h = max((geh_value / max_geh) * chart_h, 1.0)
                colors = VERDICT_COLORS[verdict_key]
                _sub(
                    bars_group,
                    "rect",
                    {
                        "class": f"bar bar-{side}",
                        "data-node-id": node_id, "data-side": side, "data-verdict": verdict_key,
                        "data-geh": _fmt(geh_value), "data-missing": "false",
                        "x": _fmt(bar_x), "y": _fmt(baseline_y - bar_h),
                        "width": _fmt(bar_w), "height": _fmt(bar_h),
                        "fill": colors["fill"], "stroke": colors["stroke"], "stroke-width": "1",
                    },
                )
            elif present:
                # Graded but undefined GEH (0 observed and 0 modelled).
                colors = VERDICT_COLORS["undefined"]
                _sub(
                    bars_group,
                    "rect",
                    {
                        "class": f"bar bar-{side}",
                        "data-node-id": node_id, "data-side": side, "data-verdict": "undefined",
                        "data-geh": "", "data-missing": "false",
                        "x": _fmt(bar_x), "y": _fmt(baseline_y - 4.0),
                        "width": _fmt(bar_w), "height": "4.00",
                        "fill": colors["fill"], "stroke": colors["stroke"], "stroke-width": "1",
                    },
                )
            else:
                # No evidence at all on this side — the one-sided case.
                colors = VERDICT_COLORS["ungraded"]
                _sub(
                    bars_group,
                    "rect",
                    {
                        "class": f"bar bar-{side} bar-missing",
                        "data-node-id": node_id, "data-side": side, "data-verdict": "ungraded",
                        "data-geh": "", "data-missing": "true", "data-one-sided": "true",
                        "x": _fmt(bar_x), "y": _fmt(baseline_y - 3.0),
                        "width": _fmt(bar_w), "height": "3.00",
                        "fill": colors["fill"], "stroke": colors["stroke"],
                        "stroke-width": "1", "stroke-dasharray": "2,2",
                    },
                )
        label_x = slot_x + (6.0 + bar_w + (6.0 + bar_w + 4.0)) / 2.0
        _sub(
            bars_group,
            "text",
            {
                "x": _fmt(label_x), "y": _fmt(baseline_y + 14.0),
                "font-size": "9", "text-anchor": "middle",
                "class": "node-label" if not row["one_sided"] else "node-label one-sided",
                "data-node-id": node_id, "data-one-sided": "true" if row["one_sided"] else "false",
            },
            text=node_id,
        )

    legend_keys = ["good", "fair", "poor", "undefined", "ungraded"]
    legend_entries = _verdict_legend_entries(legend_keys)
    _embed_legend(root, legend_entries, x=width - margin_right - 4.0, y=8.0)

    _embed_caption(root, caption, x=4.0, y=height - 6.0)

    return _render(root), legend_entries


# ---------------------------------------------------------------------------
# 3. 네트워크 도식 — NO regulation-derived section (named gap, see
#    NETWORK_SKETCH_SECTION_GAP above).
# ---------------------------------------------------------------------------

NETWORK_SKETCH_ID = "network_sketch"

_NETWORK_SKETCH_TITLE = {"ko": "네트워크 도식", "en": "Network sketch", "ja": "ネットワーク図"}
NETWORK_SKETCH_READER = "services.agent.tools_network.build_shadow_model"


def network_sketch() -> dict[str, Any]:
    """The active dataset's link polylines + node markers, geometry only —
    no scenario, no verdict (see the module docstring's caption-shape note
    for why ``scenario_id`` is legitimately ``None`` here).

    ``spine_section_id`` is deliberately ``None`` -- read
    :data:`NETWORK_SKETCH_SECTION_GAP`. MRWA's own extract requires no
    network-topology diagram, and none of ``services.report.spine``'s eight
    regulation-derived sections honestly covers one; this figure states that
    gap rather than binding to a wrong-but-passing id.

    Reader: ``services.agent.tools_network.build_shadow_model``, resolved
    exactly like :func:`node_map`'s and
    ``services.report.figures.table_5_6_applied_table``'s own copy of the
    same "which network is active" glue -- NO_READABLE_NETWORK on a missing/
    unreadable ``.inpx``, a raised reader exception named verbatim, and an
    empty (zero node/zero link) network rendered as a real, empty sketch
    rather than an absence, mirroring ``table_5_6_applied_table``'s own
    "zero behaviours is not an absence" precedent.
    """
    try:
        path = datasets.get_active_inpx_path()
    except Exception as exc:  # noqa: BLE001 — no dataset configured at all
        logger.info("report.graphics: no active inpx path (%s: %s)", type(exc).__name__, exc)
        path = None

    shadow: dict[str, Any] | None = None
    if path is not None and path.is_file():
        try:
            shadow = tools_network.build_shadow_model(path)
        except Exception as exc:  # noqa: BLE001 — an unparseable .inpx is evidence, not a 500
            logger.warning("report.graphics: shadow model unavailable (%s: %s)", type(exc).__name__, exc)
            return _absent(
                NETWORK_SKETCH_ID,
                None,
                _reason(
                    f"네트워크를 읽는 리더가 오류를 내며 실패했습니다 ({type(exc).__name__}: {exc}).",
                    f"The reader that reads the network raised an error ({type(exc).__name__}: {exc}).",
                    f"ネットワークを読み込むリーダーがエラーで失敗しました ({type(exc).__name__}: {exc})。",
                ),
            )

    nodes_raw = shadow.get("nodes") if isinstance(shadow, dict) else None
    if not isinstance(nodes_raw, list):
        return _absent(
            NETWORK_SKETCH_ID,
            None,
            _reason(
                "읽을 수 있는 네트워크(.inpx)가 없어 네트워크 도식을 만들 수 없습니다 (NO_READABLE_NETWORK).",
                "There is no readable network (.inpx), so no network sketch can be built (NO_READABLE_NETWORK).",
                "読み取れるネットワーク(.inpx)がないため、ネットワーク図を作成できません(NO_READABLE_NETWORK)。",
            ),
        )
    links_raw = shadow.get("links") if isinstance(shadow.get("links"), list) else []

    projected = [_project(float(n.get("x", 0.0)), float(n.get("y", 0.0))) for n in nodes_raw]
    for link in links_raw:
        for px, py in (link.get("points") or []):
            projected.append(_project(float(px), float(py)))
    min_x, min_y, width, height = _bounds(projected)
    view_box = f"{_fmt(min_x)} {_fmt(min_y)} {_fmt(width)} {_fmt(height)}"

    root = _svg_root(view_box, width=900, height=700)

    link_group = _sub(root, "g", {"class": "links"})
    for link in sorted(links_raw, key=lambda entry: _sort_key(entry.get("id"))):
        pts = link.get("points") or []
        if len(pts) < 2:
            continue
        proj_pts = [_project(float(px), float(py)) for px, py in pts]
        points_attr = " ".join(f"{_fmt(px)},{_fmt(py)}" for px, py in proj_pts)
        is_connector = bool(link.get("is_connector"))
        # Stroke width scaled from the link's own total lane width -- lane
        # count/widths straight from services.inpx_parser._parse_links, no
        # scale invented here beyond a legibility clamp.
        width_total = link.get("width_total")
        try:
            stroke_w = max(0.6, min(float(width_total or 3.5) * 0.35, 6.0))
        except (TypeError, ValueError):
            stroke_w = 1.0
        _sub(
            link_group,
            "polyline",
            {
                "class": "connector" if is_connector else "link",
                "data-link-id": str(link.get("id", "")),
                "data-connector": "true" if is_connector else "false",
                "data-lanes": str(link.get("lanes", "")),
                "points": points_attr,
                "fill": "none",
                "stroke": "#0ea5e9" if is_connector else "#334155",
                "stroke-width": _fmt(stroke_w),
                "stroke-dasharray": "6,4" if is_connector else "none",
            },
        )

    node_group = _sub(root, "g", {"class": "nodes"})
    for node in sorted(nodes_raw, key=lambda entry: _sort_key(entry.get("id"))):
        node_id = str(node.get("id", ""))
        cx, cy = _project(float(node.get("x", 0.0)), float(node.get("y", 0.0)))
        _sub(
            node_group,
            "circle",
            {
                "class": "node-marker",
                "data-node-id": node_id,
                "data-node-name": str(node.get("name", "")),
                "cx": _fmt(cx), "cy": _fmt(cy), "r": "6",
                "fill": "#f8fafc", "stroke": "#334155", "stroke-width": "1.5",
            },
        )
        _sub(
            node_group,
            "text",
            {"x": _fmt(cx + 8.0), "y": _fmt(cy + 3.0), "font-size": "9", "class": "node-label"},
            text=str(node.get("name") or node_id),
        )

    legend_entries = [
        {"key": "link", "ko": "링크", "en": "Link", "ja": "リンク", "fill": "none", "stroke": "#334155"},
        {"key": "connector", "ko": "커넥터", "en": "Connector", "ja": "コネクタ", "fill": "none", "stroke": "#0ea5e9"},
        {"key": "node", "ko": "노드", "en": "Node", "ja": "ノード", "fill": "#f8fafc", "stroke": "#334155"},
    ]
    _embed_legend(root, legend_entries, x=min_x + width * 0.02, y=min_y + height * 0.04)

    caption = _caption(scenario_id=None, reader=NETWORK_SKETCH_READER)
    _embed_caption(root, caption, x=min_x + width * 0.02, y=min_y + height * 0.98)

    meta = {
        "node_count": len(nodes_raw),
        "link_count": len(links_raw),
        "connector_count": sum(1 for link in links_raw if link.get("is_connector")),
        "spine_binding_gap": NETWORK_SKETCH_SECTION_GAP,
    }

    return _graphic(
        figure_id=NETWORK_SKETCH_ID,
        section_id=None,
        title=_NETWORK_SKETCH_TITLE,
        svg=_render(root),
        legend=legend_entries,
        source_caption=caption,
        citation=None,
        meta=meta,
    )


# ---------------------------------------------------------------------------
# All three, gathered — mirrors figures.all_figures's "degrade one, not all"
# shape. Named ``all_graphics`` (not folded into R-2's ``all_figures``) so a
# caller can request the two kinds independently: a DOCX export assembling a
# print-ready page needs SVG blobs in a different slot than JSON rows, and
# ``GET /api/report/figures``'s existing response shape (``{"figures": [...]}``)
# would otherwise silently start mixing rows-shaped and svg-shaped objects
# in one list.
# ---------------------------------------------------------------------------


def all_graphics(
    *,
    scenario_id: str | None = None,
    period: str | None = None,
    observation_set_id: str | None = None,
    observation_period: str | None = None,
    before_scenario_id: str | None = None,
    after_scenario_id: str | None = None,
) -> list[dict[str, Any]]:
    """The three graphics, each gathered independently."""
    return [
        node_map(
            scenario_id,
            period=period,
            observation_set_id=observation_set_id,
            observation_period=observation_period,
        ),
        before_after_geh_chart(
            before_scenario_id,
            after_scenario_id,
            period=period,
            observation_set_id=observation_set_id,
            observation_period=observation_period,
        ),
        network_sketch(),
    ]


__all__ = [
    "BEFORE_AFTER_GEH_CHART_ID",
    "NETWORK_SKETCH_ID",
    "NETWORK_SKETCH_SECTION_GAP",
    "NODE_MAP_ID",
    "VERDICT_COLORS",
    "VERDICT_KEYS",
    "all_graphics",
    "before_after_geh_chart",
    "network_sketch",
    "node_map",
]

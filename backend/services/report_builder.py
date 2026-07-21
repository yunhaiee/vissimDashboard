"""Assemble a single Word (.docx) report from an ordered list of blocks.

Styling follows a formal "Model Build Report" layout (Title page + control
table, ALL-CAPS Heading 1, Heading 2 sub-sections, "Table N / Figure N"
captions, Body Text paragraphs).

Spec from the frontend report builder:
  {
    "meta": {"title","subtitle","author","date","project","revision","description","reviewer"},
    "blocks": [{"type": "cover"}, {"type": "overview"}, {"type": "geh-validation"},
               {"type": "los-summary"}, {"type": "flow-geh-validation"},
               {"type": "travel-time-charts"}, {"type": "text","heading","body"}]
  }
"""
from __future__ import annotations

import os
from io import BytesIO

from services.datasets import get_active_dataset, get_active_real_counts_excel

# A blank .docx stripped from the reference "Model Build Report" — carries its
# styles/fonts (Arial MT body, Trebuchet MS headings, Title) so the output looks
# the same. Falls back to the python-docx default template if missing.
_TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates", "report_template.docx")
from services.validation import load_real_counts
from services.report_exports import (
    add_los_section,
    add_flow_geh_section,
    travel_chart_specs,
    render_travel_chart,
    _setcell,
    _geh,
)

_GRADE_FILL = {"good": "C6E0B4", "fair": "FFFF00", "poor": "ED7D31"}

# Heading colours match the reference report (applied as direct run formatting,
# since the .docx styles carry no colour): H1 red-orange, H2/H3 orange.
_H1_HEX = (0xFD, 0x1F, 0x01)
_H2_HEX = (0xFD, 0x7E, 0x04)


def _apply_heading_colors(doc):
    from docx.shared import RGBColor

    h1 = RGBColor(*_H1_HEX)
    h2 = RGBColor(*_H2_HEX)
    for p in doc.paragraphs:
        name = p.style.name if p.style else ""
        if name == "Heading 1":
            for r in p.runs:
                r.font.color.rgb = h1
        elif name in ("Heading 2", "Heading 3"):
            for r in p.runs:
                r.font.color.rgb = h2


class _Counter:
    def __init__(self):
        self.table = 0
        self.figure = 0

    def next_table(self):
        self.table += 1
        return self.table

    def next_figure(self):
        self.figure += 1
        return self.figure


def _styled(doc, text, style):
    """add_paragraph with a built-in style, falling back to Normal if absent."""
    try:
        return doc.add_paragraph(text, style=style)
    except (KeyError, ValueError):
        return doc.add_paragraph(text)


def _section_heading(doc, text):
    doc.add_heading(str(text).upper(), level=1)


def _caption(doc, text):
    from docx.shared import Pt

    try:
        p = doc.add_paragraph(text, style="Caption")
    except (KeyError, ValueError):
        p = doc.add_paragraph()
        run = p.add_run(text)
        run.italic = True
        run.font.size = Pt(9)
    return p


def _geh_grade(g):
    if g is None:
        return ""
    return "good" if g < 5 else ("fair" if g < 10 else "poor")


# --- cover ------------------------------------------------------------------

def _add_cover(doc, meta):
    from docx.shared import Pt
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT

    title = (meta.get("title") or "VISSIM Model").strip()
    subtitle = (meta.get("subtitle") or "Model Build Report").strip()
    for line in (title, subtitle):
        p = _styled(doc, line, "Title")
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT

    doc.add_paragraph("")

    # Project control table (PROJECT row + Revision/Description/Originator/Review/Date).
    project = (meta.get("project") or "").strip()
    t = doc.add_table(rows=3, cols=5)
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.style = "Table Grid"
    r0 = t.rows[0].cells
    _setcell(r0[0], "PROJECT", bold=True, fill="1F3864", align="left")
    r0[0].paragraphs[0].runs[0].font.color.rgb = __white()
    proj = r0[1].merge(r0[2]).merge(r0[3]).merge(r0[4])
    _setcell(proj, project, bold=True, align="left")
    hdr = ["Revision", "Description", "Originator", "Review", "Date"]
    for i, c in enumerate(t.rows[1].cells):
        _setcell(c, hdr[i], bold=True, fill="D9D9D9")
    data = [
        meta.get("revision") or "v1.0",
        meta.get("description") or subtitle,
        meta.get("author") or "",
        meta.get("reviewer") or "",
        meta.get("date") or "",
    ]
    for i, c in enumerate(t.rows[2].cells):
        _setcell(c, data[i], align="left")

    ds = get_active_dataset().get("filename")
    if ds:
        p = doc.add_paragraph()
        pr = p.add_run(f"Dataset: {ds}")
        pr.font.size = Pt(9)
    doc.add_page_break()


def __white():
    from docx.shared import RGBColor

    return RGBColor(0xFF, 0xFF, 0xFF)


# --- count GEH validation ---------------------------------------------------

def _geh_validation_rows(period):
    path = str(get_active_real_counts_excel())
    load_real_counts.cache_clear()
    rc = load_real_counts(path, period)
    rows = []
    for nid in sorted(rc, key=lambda x: int(x) if x.isdigit() else 999):
        n = rc[nid]
        obs = n.get("total_real")
        mdl = n.get("total_sim_basam")
        g = _geh(mdl, obs) if (obs is not None and mdl is not None) else None
        rows.append((nid, obs, mdl, g))
    return rows


def _add_geh_validation(doc, counter, *, heading="Count Validation (GEH)"):
    from docx.shared import Pt
    from docx.enum.table import WD_TABLE_ALIGNMENT

    _section_heading(doc, heading)
    _styled(doc,
            "노드 총 교통량(실측 vs 모델)에 대한 GEH. GEH = sqrt(2*(model-obs)^2/(model+obs)); "
            "<5 양호, 5~10 보통, >=10 불량.",
            "Body Text")
    head = ["Node", "Observed", "Modelled", "GEH", "Grade"]
    for period, label in (("AM", "AM Peak"), ("PM", "PM Peak")):
        rows = _geh_validation_rows(period)
        _caption(doc, f"Table {counter.next_table()} - Count GEH Validation ({label})")
        if not rows:
            _styled(doc, "No observed counts for this period.", "Body Text")
            continue
        t = doc.add_table(rows=1 + len(rows), cols=5)
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.style = "Table Grid"
        for i, hcell in enumerate(t.rows[0].cells):
            _setcell(hcell, head[i], bold=True, fill="D9D9D9")
        for ri, (nid, obs, mdl, g) in enumerate(rows, start=1):
            c = t.rows[ri].cells
            grade = _geh_grade(g)
            _setcell(c[0], f"Node {nid}")
            _setcell(c[1], f"{round(obs):,}" if obs is not None else "-")
            _setcell(c[2], f"{round(mdl):,}" if mdl is not None else "-")
            _setcell(c[3], f"{g:.2f}" if g is not None else "-", bold=True, fill=_GRADE_FILL.get(grade))
            _setcell(c[4], {"good": "양호", "fair": "보통", "poor": "불량"}.get(grade, "-"))
        doc.add_paragraph("")


# --- overview ---------------------------------------------------------------

def _add_overview(doc, *, heading="Overview"):
    _section_heading(doc, heading)
    total = good = 0
    for period in ("AM", "PM"):
        for _nid, obs, mdl, g in _geh_validation_rows(period):
            if g is None:
                continue
            total += 1
            if g < 5:
                good += 1
    geh_pct = (good / total * 100) if total else 0
    specs = travel_chart_specs()
    tt_total = len(specs)
    tt_in = sum(1 for s in specs if s["low"] <= s["model"] <= s["high"])
    tt_pct = (tt_in / tt_total * 100) if tt_total else 0
    _styled(doc,
            f"본 보고서는 VISSIM 미시 시뮬레이션 모델의 교통량(GEH)·통행시간 검증 결과를 정리한 것이다. "
            f"교통량 검증 결과 노드 총량 GEH<5 비율은 {geh_pct:.0f}% 이며, "
            f"통행시간은 {tt_total}개 구간 중 {tt_in}개({tt_pct:.0f}%)가 관측 ±15% 허용범위 내에 들었다. "
            f"세부 결과는 이하 표 및 그래프와 같다.",
            "Body Text")
    doc.add_paragraph("")


# --- travel-time charts -----------------------------------------------------

def _add_travel_charts(doc, counter, *, heading="Travel Time Validation"):
    from docx.shared import Inches

    _section_heading(doc, heading)
    _styled(doc,
            "루트별 누적 통행시간(관측 TomTom / 모델 / ±15% 허용범위). x축 = 시작점부터의 거리(km).",
            "Body Text")
    specs = travel_chart_specs()
    if not specs:
        _styled(doc, "통행시간 데이터가 없습니다.", "Body Text")
        return
    for spec in specs:
        png = render_travel_chart(spec)
        doc.add_picture(BytesIO(png), width=Inches(5.8))
        _caption(doc, f"Figure {counter.next_figure()} - {spec['name']} ({spec['period']} Peak)")
        doc.add_paragraph("")


# --- free text --------------------------------------------------------------

def _add_text(doc, block):
    heading = (block.get("heading") or "").strip()
    if heading:
        doc.add_heading(heading, level=2)
    body = (block.get("body") or "").strip()
    for para in body.split("\n"):
        _styled(doc, para, "Body Text")


# --- assembly ---------------------------------------------------------------

def build_report(spec: dict) -> bytes:
    from docx import Document

    meta = spec.get("meta") or {}
    blocks = spec.get("blocks") or []
    doc = Document(_TEMPLATE_PATH) if os.path.exists(_TEMPLATE_PATH) else Document()
    counter = _Counter()

    for block in blocks:
        btype = str(block.get("type") or "").strip()
        heading = block.get("heading")
        if btype == "cover":
            _add_cover(doc, meta)
        elif btype == "overview":
            _add_overview(doc, heading=heading or "Overview")
        elif btype == "geh-validation":
            _add_geh_validation(doc, counter, heading=heading or "Count Validation (GEH)")
        elif btype == "los-summary":
            _section_heading(doc, heading or "Level of Service (LOS)")
            _caption(doc, f"Table {counter.next_table()} - Intersection Level of Service (AM/PM)")
            add_los_section(doc, with_heading=False)
        elif btype == "flow-geh-validation":
            _section_heading(doc, heading or "Flow / GEH Validation")
            _caption(doc, f"Table {counter.next_table()} - Flow / GEH Validation Summary (AM/PM)")
            add_flow_geh_section(doc, with_heading=False)
        elif btype == "travel-time-charts":
            _add_travel_charts(doc, counter, heading=heading or "Travel Time Validation")
        elif btype == "text":
            _add_text(doc, block)
        # unknown types are ignored

    if not blocks:
        doc.add_paragraph("빈 보고서입니다. 섹션을 추가한 뒤 다시 생성하세요.")

    _apply_heading_colors(doc)

    out = BytesIO()
    doc.save(out)
    return out.getvalue()

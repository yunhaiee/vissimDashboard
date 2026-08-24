"""Track R unit R-4 — the style-profile extractor + per-workspace store.

See ``services/report/style_profile.py``'s module docstring for the full
doctrine this file protects: extraction-as-proposal, "actually used" palette
scoping, the four named refusals (checked before ``python-docx`` ever opens
untrusted bytes), and append-only corrections. This file's job is narrower —
prove the doctrine holds by running real DOCX bytes through the real code:

1. **The golden round trip.** ``client-data/report-style-samples/`` carries a
   DOCX GENERATED FROM a hand-authored JSON profile
   (``style-profile-dochak-v1.json``); extracting the DOCX must reproduce
   those authored values. Skipped-with-reason on a fresh checkout, the same
   convention ``test_rag_retrieval_quality.py`` uses for its own gitignored
   client-data cases — 0 failed either way; a failure here is real.
2. **Named-absence honesty**, on a synthetic minimal DOCX built in-process
   (no client data needed): every catalogue element this document genuinely
   lacks must be a named entry in ``not_extracted``, never a silently empty
   field. Checked as a MECHANICAL INVARIANT (:func:`_uncovered_absent_leaves`)
   rather than a fixed list of expected reasons, so a NEW field added later
   that forgets to name its own absence fails automatically.
3. **The five refusals** (four from ``_open_docx_or_refuse``'s bytes-level
   precheck, plus ``style_profile_no_sections`` a phase later once the
   document has actually opened), each constructed in-process and each
   checked for its own code — never a bare exception, never the wrong one
   firing for the wrong reason.
4. **The store**: append-only corrections (the original extraction is never
   touched), server-stamped editor identity (a body-supplied ``edited_by``/
   ``edited_at`` is a 422 at the FastAPI validation layer, before the store
   ever runs), and registry quarantine-on-corrupt (U6's pattern, mirrored
   from ``services/calibration_attestation.py``).
5. **The HTTP surface**: a full upload -> list -> get -> patch cycle, and the
   auth boundary this unit's own docstring argues for (uploads/corrections
   need a signed-in user; reads stay open).
6. **Both-direction key pins** on the schema this module's docstring and the
   frontend (eventually) depend on: a key silently added OR removed on either
   side fails, the same discipline ``services/report/figures.py``'s refuter
   note applies to its own column/row keys.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import docx
import pytest
from docx.oxml.ns import qn
from fastapi.testclient import TestClient

import main
from conftest import bearer_headers
from services import workspaces
from services.dataimport import xlsx_guard as xlsx_guard_module
from services.report import style_profile

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "client-data" / "report-style-samples"
SAMPLE_DOCX = SAMPLE_DIR / "샘플보고서-도착스타일-v1.docx"
SAMPLE_JSON = SAMPLE_DIR / "style-profile-dochak-v1.json"


def _skip_without_sample() -> None:
    if not SAMPLE_DOCX.is_file() or not SAMPLE_JSON.is_file():
        pytest.skip(
            "client-data/report-style-samples/ is gitignored client data, absent on a fresh "
            "checkout -- same convention as test_rag_retrieval_quality.py's skip_without_a_source_pdf."
        )


def _build_minimal_docx() -> bytes:
    """A completely unstyled document: one Normal paragraph, no tables, no
    header/footer content, no run-level color overrides. Used by every test
    that needs a REAL, valid DOCX but does not need client data."""
    document = docx.Document()
    document.add_paragraph("A single unstyled paragraph, nothing else.", style="Normal")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


# =============================================================================
# 1. Golden round trip
# =============================================================================


def test_golden_round_trip_reproduces_the_authored_profile():
    _skip_without_sample()
    expected = json.loads(SAMPLE_JSON.read_text(encoding="utf-8"))
    content = SAMPLE_DOCX.read_bytes()

    profile = style_profile.public_profile(
        style_profile.extract_style_profile(content, source_file=SAMPLE_DOCX.name)
    )

    # A fully-styled sample: nothing should be named as un-extractable.
    assert profile["not_extracted"] == []

    palette = profile["palette"]
    expected_palette = expected["palette"]
    assert palette["title"] == expected_palette["primary"]
    assert palette["heading1"] == expected_palette["primary"]
    assert palette["heading2"] == expected_palette["accent"]
    assert palette["heading3"] == expected_palette["accent"]
    assert palette["body"] == expected_palette["body_text"]
    assert palette["emphasis"] == expected_palette["emphasis"]
    assert palette["table_header_fill"] == expected_palette["table_header_fill"]
    assert palette["table_border"] == expected_palette["table_border"]
    assert palette["header_footer_text"] == expected_palette["header_footer_text"]

    fonts = profile["fonts"]
    expected_fonts = expected["fonts"]
    assert fonts["heading_family"]["eastAsia"] == expected_fonts["heading_family"]
    assert fonts["body_family"]["eastAsia"] == expected_fonts["body_family"]
    ladder = fonts["ladder_pt"]
    expected_ladder = expected_fonts["ladder_pt"]
    for key in ("title", "heading1", "heading2", "heading3", "body", "caption", "table"):
        assert ladder[key] == expected_ladder[key], key

    expected_page = expected["page"]
    assert profile["page"]["size"] == expected_page["size"]
    for side in ("top", "bottom", "left", "right"):
        # EMU/twip round-trip through Word introduces sub-mm noise (measured:
        # 24.99 vs an authored 25) -- a tolerance, not an exact-equality claim.
        assert abs(profile["page"]["margins_mm"][side] - expected_page["margins_mm"][side]) < 0.1

    numbering = profile["heading_numbering"]
    expected_numbering = expected["heading_numbering"]
    assert numbering["scheme"] == "typed_decimal"
    assert expected_numbering["scheme"] == "typed-decimal"
    assert numbering["detected"]["h1"] == expected_numbering["h1"]
    assert numbering["detected"]["h2"] == expected_numbering["h2"]
    # The JSON's "h3": "1.1.1" is an ILLUSTRATIVE pattern; the document's one
    # real Heading-3 paragraph is numbered "2.1.1" at its actual position.
    # Both are three-level typed-decimal, which is what the scheme claims --
    # checked structurally rather than string-equal to the JSON's example.
    assert numbering["detected"]["h3"].count(".") == expected_numbering["h3"].count(".") == 2

    table_style = profile["table_style"]
    expected_table_style = expected["table_style"]
    assert table_style["header_row"]["fill"] == expected_table_style["header_row"]["fill"]
    assert table_style["header_row"]["text_color"] == expected_table_style["header_row"]["text_color"]
    assert table_style["header_row"]["bold"] == expected_table_style["header_row"]["bold"]
    assert table_style["header_row"]["size_pt"] == expected_table_style["header_row"]["size_pt"]
    assert table_style["borders"]["color"] == expected_table_style["borders"]["color"]
    assert table_style["borders"]["width_pt"] == expected_table_style["borders"]["width_pt"]

    logo = profile["header"]["logo"]
    expected_logo = expected["header"]["logo"]
    assert logo["present"] is True
    logo_file = SAMPLE_DIR / expected_logo["file"]
    assert logo["sha256"] == hashlib.sha256(logo_file.read_bytes()).hexdigest()
    assert logo["display_width_mm"] == expected_logo["width_mm"]
    assert logo["width_px"] and logo["height_px"]

    assert profile["header"]["text"] == expected["header"]["right_text"]
    assert profile["header"]["rule_below"] is True

    page_number = profile["footer"]["page_number"]
    assert page_number["present"] is True
    assert page_number["format"] == expected["footer"]["format"]
    assert page_number["position"] == "center"

    assert profile["caption"]["prefix"] == expected_table_style["caption_prefix"]


# =============================================================================
# 2. Named-absence honesty, and the invariant that makes it mechanical
# =============================================================================


def _uncovered_absent_leaves(value: Any, not_extracted_fields: set[str], prefix: str = "") -> list[str]:
    """Every ``None``/``False`` leaf in ``value`` whose dotted path is not
    named by (or an ancestor/descendant of) some entry in
    ``not_extracted_fields``. An empty result IS the invariant holding."""
    problems: list[str] = []
    if isinstance(value, dict):
        for key, sub in value.items():
            if key in ("not_extracted", "kind"):
                continue
            problems += _uncovered_absent_leaves(sub, not_extracted_fields, f"{prefix}.{key}" if prefix else key)
    elif value is None or value is False:
        covered = any(
            prefix == field or prefix.startswith(field + ".") or field.startswith(prefix + ".")
            for field in not_extracted_fields
        )
        if not covered:
            problems.append(prefix)
    return problems


def test_named_absence_on_a_synthetic_minimal_docx():
    """No tables, no header/footer content, no explicit colors anywhere.
    Everything the catalogue could have reported must come back None/False
    AND named in ``not_extracted`` -- nothing invented, nothing silent."""
    content = _build_minimal_docx()
    profile = style_profile.public_profile(
        style_profile.extract_style_profile(content, source_file="synthetic.docx")
    )

    assert profile["table_style"] is None
    assert profile["header"]["logo"]["present"] is False
    assert profile["header"]["text"] is None
    assert profile["footer"]["page_number"]["present"] is False
    assert profile["palette"]["emphasis"] is None
    assert profile["caption"]["prefix"] is None
    assert profile["heading_numbering"]["scheme"] == "none"

    not_extracted_fields = {entry["field"] for entry in profile["not_extracted"]}
    assert not_extracted_fields, "a document with nothing styled must name SOMETHING as absent"
    for field in ("table_style", "header", "footer.page_number", "palette.emphasis", "heading_numbering"):
        assert field in not_extracted_fields, field

    # every reason is a real ko/en/ja triple, never an empty placeholder
    for entry in profile["not_extracted"]:
        for lang in ("ko", "en", "ja"):
            assert entry["reason"][lang].strip()

    uncovered = _uncovered_absent_leaves(profile, not_extracted_fields)
    assert uncovered == [], f"absent without a stated reason: {uncovered}"


def test_font_extraction_reads_the_eastasia_slot_not_ascii():
    """Regression guard for the exact landmine this unit's build instructions
    named by name: "ascii-only extraction is a defect". The golden sample's
    own ascii happens to EQUAL its eastAsia ("맑은 고딕" both), so the round
    trip test alone cannot catch a reader that reads the wrong slot -- this
    fixture makes them visibly different so it can.
    """
    document = docx.Document()
    document.add_paragraph("hello", style="Normal")
    normal_style = document.styles["Normal"]
    run_properties = normal_style.element.get_or_add_rPr()
    rfonts = run_properties.makeelement(qn("w:rFonts"), {})
    rfonts.set(qn("w:ascii"), "Calibri")
    rfonts.set(qn("w:eastAsia"), "맑은 고딕")
    run_properties.append(rfonts)
    buf = io.BytesIO()
    document.save(buf)

    profile = style_profile.public_profile(
        style_profile.extract_style_profile(buf.getvalue(), source_file="fonts.docx")
    )
    assert profile["fonts"]["body_family"]["ascii"] == "Calibri"
    assert profile["fonts"]["body_family"]["eastAsia"] == "맑은 고딕"


# =============================================================================
# 2b. Mutation-derived pins (R-4 refuter, 2026-08-24) -- each of these was
# PROVEN unpinned by actually applying the mutation it guards against and
# watching the rest of this file stay green. See handoff notes for the full
# mutation log.
# =============================================================================


def test_minimal_docx_body_color_is_named_absent_not_fabricated():
    """A style with NO ``<w:color>`` element at all (the 'unspecified' branch
    of :func:`style_profile._style_color_hex`) must report ``None`` with a
    named reason -- never a fabricated default hex like ``"000000"``.
    Verified unpinned: swapping the ``unspecified`` branch's ``return None,
    "unspecified"`` for ``return "000000", None`` left every other test in
    this file green, because ``_uncovered_absent_leaves`` only ever
    scrutinizes ``None``/``False`` leaves -- a fabricated non-None value is
    invisible to it. ``palette.body`` on the bog-standard minimal docx (no
    color ever set on Normal) is the simplest real trigger for this branch.
    """
    content = _build_minimal_docx()
    profile = style_profile.public_profile(
        style_profile.extract_style_profile(content, source_file="unspecified-color.docx")
    )
    assert profile["palette"]["body"] is None
    not_extracted_fields = {e["field"]: e for e in profile["not_extracted"]}
    assert "palette.body" in not_extracted_fields
    reason = not_extracted_fields["palette.body"]["reason"]
    assert "명시" in reason["ko"]
    assert reason["en"].strip() and reason["ja"].strip()


def test_unused_heading_styles_never_leak_their_latent_template_default_color():
    """A bare ``docx.Document()``'s built-in ``Title`` style carries a REAL
    literal color (``w:val="17365D"``) even though no paragraph in a
    minimal/fresh document ever applies it -- confirmed directly below
    against ``docx.Document().styles["Title"].font.color.rgb``. If
    :func:`style_profile._style_is_used` were ever broken to report a style
    as "used" regardless of whether any paragraph actually carries it, this
    latent template default would leak into ``palette.title`` as though it
    were really styled in THIS document -- exactly the "actually used, not
    latent" doctrine the module docstring names as its own headline claim.
    Verified unpinned: hardcoding ``_style_is_used`` to always return
    ``True`` left every other test in this file green (the golden sample
    already uses every heading role, so it can't distinguish "correctly
    gated" from "not gated at all"; the minimal-docx test never previously
    asserted ``palette.title``/``heading1-3``).
    """
    content = _build_minimal_docx()
    profile = style_profile.public_profile(
        style_profile.extract_style_profile(content, source_file="unused-headings.docx")
    )
    for key in ("title", "heading1", "heading2", "heading3"):
        assert profile["palette"][key] is None, key
    not_extracted_fields = {e["field"] for e in profile["not_extracted"]}
    for field in ("palette.title", "palette.heading1", "palette.heading2", "palette.heading3"):
        assert field in not_extracted_fields, field

    # The independently-confirmed fact this test guards against: the bare
    # python-docx template's Title style really does carry a literal color.
    bare_title = docx.Document().styles["Title"]
    assert str(bare_title.font.color.rgb) == "17365D"


def test_theme_only_color_reference_is_never_reported_as_a_literal_hex():
    """A style whose ``<w:color>`` carries ONLY a ``themeColor`` reference
    with no literal ``w:val`` fallback -- the module docstring's own
    ``"theme_only"`` branch, distinct from the no-``<w:color>``-at-all
    ``"unspecified"`` branch pinned above -- must never be reported as a
    real hex value. Verified unpinned: changing the ``theme_only`` branch's
    ``return None, "theme_only"`` to ``return "000000", None`` left every
    other test in this file green.
    """
    document = docx.Document()
    document.add_paragraph("hello", style="Normal")
    document.add_paragraph("A Title", style="Title")
    title_style = document.styles["Title"]
    rpr = title_style.element.get_or_add_rPr()
    existing_color = rpr.find(qn("w:color"))
    if existing_color is not None:
        rpr.remove(existing_color)
    color_el = rpr.makeelement(qn("w:color"), {})
    color_el.set(qn("w:themeColor"), "accent1")  # deliberately NO w:val fallback
    rpr.append(color_el)

    buf = io.BytesIO()
    document.save(buf)
    profile = style_profile.public_profile(
        style_profile.extract_style_profile(buf.getvalue(), source_file="theme-only.docx")
    )
    assert profile["palette"]["title"] is None
    not_extracted_fields = {e["field"]: e for e in profile["not_extracted"]}
    assert "palette.title" in not_extracted_fields
    reason = not_extracted_fields["palette.title"]["reason"]
    assert "테마" in reason["ko"]
    assert "theme" in reason["en"].lower()


def test_page_geometry_reads_the_first_section_not_a_later_one():
    """A two-section document whose sections carry DIFFERENT page geometry
    -- extraction must read ``sections[0]``, never a later section (e.g.
    ``sections[-1]`` or a ``sections[1]``-with-fallback scheme). Every other
    fixture in this file has exactly one section, so a
    ``sections[0]`` -> ``sections[-1]`` regression would otherwise be
    invisible. Verified unpinned: changing ``document.sections[0]`` to
    ``document.sections[-1]`` in :func:`style_profile.extract_style_profile`
    left every other test in this file green.
    """
    from docx.shared import Mm

    document = docx.Document()
    document.add_paragraph("first section body", style="Normal")
    document.sections[0].page_width = Mm(210)
    document.sections[0].page_height = Mm(297)  # A4

    document.add_section()
    document.sections[1].page_width = Mm(420)
    document.sections[1].page_height = Mm(297)  # deliberately NOT A4

    buf = io.BytesIO()
    document.save(buf)
    profile = style_profile.public_profile(
        style_profile.extract_style_profile(buf.getvalue(), source_file="two-section.docx")
    )
    assert profile["page"]["size"] == "A4"
    assert abs(profile["page"]["width_mm"] - 210) < 0.5


def test_empty_sectpr_names_page_geometry_leaves_and_a_partial_one_names_only_what_it_lacks():
    """A ``<w:sectPr>`` can EXIST (so extract_style_profile's zero-sections
    refusal correctly stays silent) yet carry no ``<w:pgSz>``/``<w:pgMar>``
    children at all -- before this fix, page.width_mm, page.height_mm, and
    all four page.margins_mm.* leaves came back silently None with ZERO
    not_extracted coverage (:func:`_extract_page` took no ``not_extracted``
    parameter and never called ``_mark_missing``). Also covers the PARTIAL
    case -- pgSz present, pgMar removed -- to prove only the leaves actually
    missing get named, not the whole page.* subtree indiscriminately.
    """
    # --- fully empty sectPr: no pgSz, no pgMar -----------------------------
    document = docx.Document()
    document.add_paragraph("body text", style="Normal")
    body = document.element.body
    sect_pr = body.find(qn("w:sectPr"))
    for child in list(sect_pr):
        sect_pr.remove(child)

    buf = io.BytesIO()
    document.save(buf)
    profile = style_profile.public_profile(
        style_profile.extract_style_profile(buf.getvalue(), source_file="empty-sectpr.docx")
    )

    assert profile["page"]["width_mm"] is None
    assert profile["page"]["height_mm"] is None
    for side in ("top", "bottom", "left", "right"):
        assert profile["page"]["margins_mm"][side] is None, side

    not_extracted_fields = {e["field"] for e in profile["not_extracted"]}
    for field in (
        "page.width_mm",
        "page.height_mm",
        "page.margins_mm.top",
        "page.margins_mm.bottom",
        "page.margins_mm.left",
        "page.margins_mm.right",
    ):
        assert field in not_extracted_fields, field

    uncovered = _uncovered_absent_leaves(profile, not_extracted_fields)
    assert uncovered == [], f"absent without a stated reason: {uncovered}"

    # --- partial sectPr: pgSz present, pgMar removed -----------------------
    document2 = docx.Document()
    document2.add_paragraph("body text", style="Normal")
    body2 = document2.element.body
    sect_pr2 = body2.find(qn("w:sectPr"))
    pg_mar = sect_pr2.find(qn("w:pgMar"))
    sect_pr2.remove(pg_mar)

    buf2 = io.BytesIO()
    document2.save(buf2)
    profile2 = style_profile.public_profile(
        style_profile.extract_style_profile(buf2.getvalue(), source_file="partial-sectpr.docx")
    )

    assert profile2["page"]["width_mm"] is not None
    assert profile2["page"]["height_mm"] is not None
    for side in ("top", "bottom", "left", "right"):
        assert profile2["page"]["margins_mm"][side] is None, side

    not_extracted_fields2 = {e["field"] for e in profile2["not_extracted"]}
    # only the margin leaves are missing -- pgSz survived, so width/height
    # must NOT be named
    assert "page.width_mm" not in not_extracted_fields2
    assert "page.height_mm" not in not_extracted_fields2
    for side in ("top", "bottom", "left", "right"):
        assert f"page.margins_mm.{side}" in not_extracted_fields2, side

    uncovered2 = _uncovered_absent_leaves(profile2, not_extracted_fields2)
    assert uncovered2 == [], f"absent without a stated reason: {uncovered2}"


# =============================================================================
# 2c. Builder fixes for the refuter's second pass on this unit (R-4) --
# ordinary Table-Style-formatted tables, asymmetric rFonts, and a header
# paragraph with no runs, each of which silently leaked a None leaf with no
# not_extracted entry before this fix.
# =============================================================================


def test_table_style_formatted_table_names_every_direct_formatting_leaf_it_lacks():
    """A table using a named TABLE STYLE (``Table Grid``) and otherwise
    completely ordinary -- real text in every cell, no per-run bold/color/
    size override, no per-cell shading -- is how most real Word tables are
    actually built. Before this fix, table_style.header_row.text_color/
    .bold/.size_pt, table_style.borders.width_pt, and
    fonts.ladder_pt.table came back silently None with no not_extracted
    entry: the value lives in the table STYLE's own definition, which this
    extractor deliberately does not read (scope expansion into the style
    catalogue is not the fix -- named absence is), and that must be a named
    absence, never a silent one.
    """
    document = docx.Document()
    document.add_paragraph("intro", style="Normal")
    table = document.add_table(rows=2, cols=2)
    table.style = "Table Grid"
    table.rows[0].cells[0].text = "Header"
    table.rows[0].cells[1].text = "Header 2"
    table.rows[1].cells[0].text = "Body"
    table.rows[1].cells[1].text = "Body 2"

    buf = io.BytesIO()
    document.save(buf)
    profile = style_profile.public_profile(
        style_profile.extract_style_profile(buf.getvalue(), source_file="table-style-only.docx")
    )

    table_style = profile["table_style"]
    assert table_style is not None
    assert table_style["header_row"]["text_color"] is None
    assert table_style["header_row"]["bold"] is None
    assert table_style["header_row"]["size_pt"] is None
    assert table_style["borders"]["width_pt"] is None
    assert profile["fonts"]["ladder_pt"]["table"] is None

    not_extracted_fields = {e["field"] for e in profile["not_extracted"]}
    for field in (
        "table_style.header_row.text_color",
        "table_style.header_row.bold",
        "table_style.header_row.size_pt",
        "table_style.borders.width_pt",
        "fonts.ladder_pt.table",
    ):
        assert field in not_extracted_fields, field

    uncovered = _uncovered_absent_leaves(profile, not_extracted_fields)
    assert uncovered == [], f"absent without a stated reason: {uncovered}"


def test_asymmetric_rfonts_with_only_ascii_set_names_the_missing_eastasia_leaf():
    """Regression guard for the exact defect this unit's build instructions
    named: rFonts with ONLY ``w:ascii`` set (no ``w:eastAsia`` at all) --
    on both the body (Normal) and a used heading role (Title) -- must name
    fonts.body_family.eastAsia / fonts.heading_family.eastAsia as absent on
    their OWN leaf, never silently None and never backfilled from the ascii
    slot that IS present (ascii != eastAsia; see the module docstring's own
    eastAsia-vs-ascii doctrine).
    """
    document = docx.Document()
    document.add_paragraph("hello", style="Normal")
    document.add_paragraph("A Title", style="Title")

    for style_name in ("Normal", "Title"):
        style = document.styles[style_name]
        rpr = style.element.get_or_add_rPr()
        existing = rpr.find(qn("w:rFonts"))
        if existing is not None:
            rpr.remove(existing)
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rfonts.set(qn("w:ascii"), "Calibri")  # deliberately NO eastAsia
        rpr.append(rfonts)

    buf = io.BytesIO()
    document.save(buf)
    profile = style_profile.public_profile(
        style_profile.extract_style_profile(buf.getvalue(), source_file="ascii-only.docx")
    )

    assert profile["fonts"]["body_family"]["ascii"] == "Calibri"
    assert profile["fonts"]["body_family"]["eastAsia"] is None
    assert profile["fonts"]["heading_family"]["ascii"] == "Calibri"
    assert profile["fonts"]["heading_family"]["eastAsia"] is None

    not_extracted_fields = {e["field"] for e in profile["not_extracted"]}
    assert "fonts.body_family.eastAsia" in not_extracted_fields
    assert "fonts.heading_family.eastAsia" in not_extracted_fields
    # named on its OWN leaf -- never folded into a whole-family absence
    assert "fonts.body_family" not in not_extracted_fields
    assert "fonts.heading_family" not in not_extracted_fields

    uncovered = _uncovered_absent_leaves(profile, not_extracted_fields)
    assert uncovered == [], f"absent without a stated reason: {uncovered}"


def test_header_with_no_runs_names_text_size_and_color_as_absent():
    """A header that IS defined (not linked-to-previous) but whose one
    paragraph carries zero runs -- the default state right after unlinking,
    before any text is typed -- must name header.text_size_pt and
    header.text_color as absent, not just header.text. Before this fix only
    header.text was named: header.text_size_pt/header.text_color were
    silently None and UNCOVERED by _uncovered_absent_leaves's dotted-prefix
    rule ("header.text_size_pt" does not start with "header.text.").
    header.rule_below is checked as the sibling boundary case: a MEASURED
    structural fact (no rule found), not an unknown, so it must stay OUT of
    not_extracted even though it too is False.
    """
    document = docx.Document()
    document.add_paragraph("body text", style="Normal")
    section = document.sections[0]
    section.header.is_linked_to_previous = False
    assert section.header.paragraphs and not section.header.paragraphs[0].runs  # the precondition

    buf = io.BytesIO()
    document.save(buf)
    profile = style_profile.public_profile(
        style_profile.extract_style_profile(buf.getvalue(), source_file="header-no-runs.docx")
    )

    assert profile["header"]["text"] is None
    assert profile["header"]["text_size_pt"] is None
    assert profile["header"]["text_color"] is None
    assert profile["header"]["rule_below"] is False

    not_extracted_fields = {e["field"] for e in profile["not_extracted"]}
    for field in ("header.text", "header.text_size_pt", "header.text_color"):
        assert field in not_extracted_fields, field
    assert "header.rule_below" not in not_extracted_fields

    uncovered = _uncovered_absent_leaves(profile, not_extracted_fields)
    # header.rule_below=False is excluded here ON PURPOSE: the refuter's own
    # lower-confidence, explicitly out-of-scope-for-this-fix finding (see the
    # build instructions) -- a MEASURED structural fact ("no rule found
    # below this header"), not an unknown, so it is deliberately never named
    # in not_extracted even though the mechanical invariant would otherwise
    # flag it. Every OTHER leaf this fix touches must still be covered.
    uncovered = [leaf for leaf in uncovered if leaf != "header.rule_below"]
    assert uncovered == [], f"absent without a stated reason: {uncovered}"


# =============================================================================
# 3. The five refusals
# =============================================================================


def test_refuses_non_zip_bytes_by_name():
    with pytest.raises(style_profile.StyleProfileRefused) as caught:
        style_profile.extract_style_profile(b"not a docx at all, just text", source_file="x.docx")
    assert caught.value.code == "style_profile_not_a_zip"
    assert caught.value.status == 400


def test_refuses_a_password_protected_or_legacy_doc_file_by_name():
    cfbf_signature = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    with pytest.raises(style_profile.StyleProfileRefused) as caught:
        style_profile.extract_style_profile(cfbf_signature + b"\x00" * 200, source_file="secret.docx")
    assert caught.value.code == "style_profile_password_protected"
    assert caught.value.status == 400


def test_refuses_a_zip_that_is_not_a_docx_by_name():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("hello.txt", "just a text file inside a real zip")
    with pytest.raises(style_profile.StyleProfileRefused) as caught:
        style_profile.extract_style_profile(buf.getvalue(), source_file="not-a-docx.zip")
    assert caught.value.code == "style_profile_not_a_docx"
    assert caught.value.status == 400


def test_refuses_an_unreadable_docx_shaped_zip_by_name():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("word/document.xml", b"this is not valid XML at all <<<broken")
        archive.writestr("[Content_Types].xml", b"<Types/>")
    with pytest.raises(style_profile.StyleProfileRefused) as caught:
        style_profile.extract_style_profile(buf.getvalue(), source_file="corrupt.docx")
    assert caught.value.code == "style_profile_unreadable"
    assert caught.value.status == 400


def test_refuses_a_zip_bomb_before_python_docx_ever_opens_it(monkeypatch: pytest.MonkeyPatch):
    """Caps lowered (the same technique tests/test_xlsx_guard.py's own
    isolation tests use) so the fixture stays tiny; ``docx.Document`` is
    monkeypatched to FAIL the test if it is ever called, proving the guard
    runs strictly before the file is opened -- not merely that a refusal
    happens somewhere."""
    monkeypatch.setattr(xlsx_guard_module, "MAX_MEMBER_DECOMPRESSED_BYTES", 1_000)
    monkeypatch.setattr(xlsx_guard_module, "MAX_COMPRESSION_RATIO", 1_000_000.0)
    monkeypatch.setattr(
        style_profile.docx,
        "Document",
        lambda *a, **k: pytest.fail("a zip bomb must never reach python-docx.Document()"),
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("word/document.xml", b"A" * 5_000)

    with pytest.raises(style_profile.StyleProfileRefused) as caught:
        style_profile.extract_style_profile(buf.getvalue(), source_file="bomb.docx")
    assert caught.value.code == "style_profile_zip_bomb"
    assert caught.value.status == 400


def test_refuses_a_docx_with_zero_sections_by_name():
    """A DOCX python-docx can open fine but whose body carries not one
    ``<w:sectPr>`` -- not even the trailing body-level one every normal
    Word document writes -- must be refused BY NAME, never let
    ``sections[0]`` raise a bare ``IndexError`` across the module boundary.
    ``page``/``header``/``footer`` all key off ``sections[0]``, so this is
    a refusal, not three parallel all-None sub-trees (see the module
    docstring's "fifth refusal" note for the reasoning).
    """
    document = docx.Document()
    document.add_paragraph("orphaned body, no section at all", style="Normal")
    body = document.element.body
    sect_pr = body.find(qn("w:sectPr"))
    assert sect_pr is not None  # the normal, single-section state before we break it
    body.remove(sect_pr)

    buf = io.BytesIO()
    document.save(buf)
    content = buf.getvalue()

    # confirm the fixture actually reproduces the raw python-docx crash this
    # defect is about, before the module's own refusal is asserted
    reopened = docx.Document(io.BytesIO(content))
    assert len(reopened.sections) == 0
    with pytest.raises(IndexError):
        reopened.sections[0]

    with pytest.raises(style_profile.StyleProfileRefused) as caught:
        style_profile.extract_style_profile(content, source_file="no-sections.docx")
    assert caught.value.code == "style_profile_no_sections"
    assert caught.value.status == 422


# =============================================================================
# 4. The store — U6 pattern, append-only corrections
# =============================================================================


@pytest.fixture()
def workspace_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(workspaces, "WORKSPACES_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(workspaces, "LEGACY_UPLOADS_ROOT", tmp_path / "legacy-uploads")
    default_id = workspaces.default_workspace_id()
    token = workspaces.set_current_workspace_id(default_id)
    try:
        yield tmp_path / "workspaces"
    finally:
        workspaces.reset_current_workspace_id(token)


def test_store_put_list_get_round_trip(workspace_root):
    content = _build_minimal_docx()
    summary = style_profile.store_profile(content=content, source_file="회사양식.docx", name=None)
    assert summary["name"] == "회사양식"  # default: source filename stem
    assert summary["source_sha256"] == hashlib.sha256(content).hexdigest()

    listed = style_profile.list_profiles()
    assert [entry["id"] for entry in listed] == [summary["id"]]

    record = style_profile.get_profile(summary["id"])
    assert record["source_file"] == "회사양식.docx"
    assert record["corrections"] == []
    assert record["resolved_profile"] == record["original_profile"]


def test_get_profile_refuses_an_unknown_id(workspace_root):
    with pytest.raises(style_profile.StyleProfileRefused) as caught:
        style_profile.get_profile("deadbeef0000")
    assert caught.value.code == "style_profile_not_found"
    assert caught.value.status == 404


def test_corrections_are_append_only_and_never_touch_the_original(workspace_root):
    content = _build_minimal_docx()
    summary = style_profile.store_profile(content=content, source_file="a.docx")
    profile_id = summary["id"]
    original_before = style_profile.get_profile(profile_id)["original_profile"]

    first = style_profile.apply_correction(
        profile_id, fields={"palette": {"emphasis": "FF0000"}}, edited_by="alice", reason="first fix"
    )
    assert len(first["corrections"]) == 1
    assert first["corrections"][0]["revision"] == 1
    assert first["corrections"][0]["edited_by"] == "alice"
    assert first["original_profile"] == original_before
    assert first["resolved_profile"]["palette"]["emphasis"] == "FF0000"

    second = style_profile.apply_correction(
        profile_id, fields={"palette": {"emphasis": "00FF00"}}, edited_by="bob"
    )
    assert len(second["corrections"]) == 2
    assert [c["revision"] for c in second["corrections"]] == [1, 2]
    assert second["corrections"][0]["edited_by"] == "alice"  # first event untouched
    assert second["corrections"][1]["edited_by"] == "bob"
    # the ORIGINAL extraction is intact after TWO corrections
    assert second["original_profile"] == original_before
    assert second["resolved_profile"]["palette"]["emphasis"] == "00FF00"

    # a sibling field the corrections never touched survives the deep merge
    assert second["resolved_profile"]["page"] == original_before["page"]


def test_correction_refuses_an_empty_editor_and_empty_fields(workspace_root):
    content = _build_minimal_docx()
    summary = style_profile.store_profile(content=content, source_file="a.docx")

    with pytest.raises(style_profile.StyleProfileRefused) as no_editor:
        style_profile.apply_correction(summary["id"], fields={"palette": {"emphasis": "FF0000"}}, edited_by="  ")
    assert no_editor.value.code == "style_profile_no_editor"
    assert no_editor.value.status == 401

    with pytest.raises(style_profile.StyleProfileRefused) as empty_fields:
        style_profile.apply_correction(summary["id"], fields={}, edited_by="alice")
    assert empty_fields.value.code == "style_profile_empty_correction"


def test_corrupt_registry_is_quarantined_not_overwritten(workspace_root):
    content = _build_minimal_docx()
    style_profile.store_profile(content=content, source_file="a.docx")
    registry_path = style_profile.registry_path()
    original_corrupt_bytes = b"{not valid json at all"
    registry_path.write_bytes(original_corrupt_bytes)

    # degrades to "no profiles" rather than crashing or losing the corrupt file
    assert style_profile.list_profiles() == []

    quarantined = sorted(registry_path.parent.glob(f"{registry_path.name}.corrupt-*"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == original_corrupt_bytes

    health = style_profile.registry_health_status()
    assert health is not None
    assert health["code"] == "registry_quarantined"

    # the profile's OWN directory (written before the registry was corrupted)
    # is untouched -- only the index was lost.
    fresh_registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert fresh_registry == {"profiles": {}, "incidents": [fresh_registry["incidents"][0]]}

    # a fresh store after quarantine behaves normally
    summary2 = style_profile.store_profile(content=content, source_file="b.docx")
    assert [entry["id"] for entry in style_profile.list_profiles()] == [summary2["id"]]


def test_write_json_atomic_uses_a_distinct_temp_file_then_replaces(tmp_path: Path):
    """:func:`style_profile._write_json_atomic` must write to a SAME-
    DIRECTORY temp file and ``os.replace`` it onto the destination -- never
    write the destination path directly -- or a process kill mid-write can
    leave a half-written registry/profile file on disk instead of either the
    complete old one or the complete new one. No black-box behavioral test
    can observe a crash mid-write, so this pins the IMPLEMENTATION TECHNIQUE
    directly via a spy on ``os.replace``. Verified unpinned: collapsing
    ``_write_json_atomic`` to a plain ``open(path, "w").write(...)`` (no temp
    file, no ``os.replace``) left every other test in this file green.
    """
    target = tmp_path / "atomic-write-test.json"
    style_profile._write_json_atomic(target, {"hello": "world"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"hello": "world"}
    assert list(tmp_path.glob("*.tmp")) == [], "no leftover temp files after a successful write"

    calls: list[tuple[str, str]] = []
    orig_replace = style_profile.os.replace

    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        return orig_replace(src, dst)

    style_profile.os.replace = spy_replace
    try:
        style_profile._write_json_atomic(target, {"hello": "world2"})
    finally:
        style_profile.os.replace = orig_replace

    assert len(calls) == 1, calls
    src, dst = calls[0]
    assert src != dst, "the temp source path must differ from the final destination path"
    assert dst == str(target)
    assert json.loads(target.read_text(encoding="utf-8")) == {"hello": "world2"}


# =============================================================================
# 5. HTTP surface
# =============================================================================


@pytest.fixture()
def client(workspace_root) -> TestClient:
    return TestClient(main.app)


@pytest.fixture()
def headers() -> dict[str, str]:
    return bearer_headers("style-profile-uploader", "modeler")


def test_http_upload_requires_auth(client: TestClient):
    response = client.post("/api/report/style-profiles?filename=a.docx", content=_build_minimal_docx())
    assert response.status_code == 401


def test_http_patch_requires_auth(client: TestClient):
    response = client.patch(
        "/api/report/style-profiles/deadbeef0000", json={"fields": {"palette": {"emphasis": "FF0000"}}}
    )
    assert response.status_code == 401


def test_http_reads_stay_open(client: TestClient):
    assert client.get("/api/report/style-profiles").status_code == 200
    assert client.get("/api/report/style-profiles/deadbeef0000").status_code == 404


def test_http_full_upload_list_get_patch_cycle(client: TestClient, headers: dict[str, str]):
    content = _build_minimal_docx()
    upload = client.post(
        "/api/report/style-profiles?filename=%ED%9A%8C%EC%82%AC%EC%96%91%EC%8B%9D.docx",
        headers=headers,
        content=content,
    )
    assert upload.status_code == 201
    body = upload.json()
    assert body["source_file"] == "회사양식.docx"
    assert "not_extracted" in body["original_profile"]
    profile_id = body["id"]

    listing = client.get("/api/report/style-profiles")
    assert listing.status_code == 200
    assert any(entry["id"] == profile_id for entry in listing.json()["profiles"])

    detail = client.get(f"/api/report/style-profiles/{profile_id}")
    assert detail.status_code == 200
    assert detail.json()["corrections"] == []

    patched = client.patch(
        f"/api/report/style-profiles/{profile_id}",
        headers=headers,
        json={"fields": {"palette": {"emphasis": "112233"}}, "reason": "manual fix"},
    )
    assert patched.status_code == 200
    assert patched.json()["resolved_profile"]["palette"]["emphasis"] == "112233"
    assert patched.json()["corrections"][0]["edited_by"] == "style-profile-uploader"

    detail_after = client.get(f"/api/report/style-profiles/{profile_id}")
    assert len(detail_after.json()["corrections"]) == 1


def test_http_patch_rejects_client_supplied_edited_by_and_edited_at(
    client: TestClient, headers: dict[str, str]
):
    """The server-stamped-editor invariant, enforced at the pydantic layer
    (``extra="forbid"``) -- a body carrying either field never reaches the
    store at all."""
    upload = client.post(
        "/api/report/style-profiles?filename=a.docx", headers=headers, content=_build_minimal_docx()
    )
    profile_id = upload.json()["id"]

    rejected_by = client.patch(
        f"/api/report/style-profiles/{profile_id}",
        headers=headers,
        json={"fields": {"palette": {"emphasis": "FF0000"}}, "edited_by": "someone-else"},
    )
    assert rejected_by.status_code == 422

    rejected_at = client.patch(
        f"/api/report/style-profiles/{profile_id}",
        headers=headers,
        json={"fields": {"palette": {"emphasis": "FF0000"}}, "edited_at": "2020-01-01T00:00:00Z"},
    )
    assert rejected_at.status_code == 422

    rejected_empty = client.patch(
        f"/api/report/style-profiles/{profile_id}", headers=headers, json={"fields": {}}
    )
    assert rejected_empty.status_code == 422

    # confirm none of the rejected bodies left a correction behind
    detail = client.get(f"/api/report/style-profiles/{profile_id}")
    assert detail.json()["corrections"] == []


def test_http_upload_refuses_a_bomb_with_a_named_400(client: TestClient, headers: dict[str, str], monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(xlsx_guard_module, "MAX_MEMBER_DECOMPRESSED_BYTES", 1_000)
    monkeypatch.setattr(xlsx_guard_module, "MAX_COMPRESSION_RATIO", 1_000_000.0)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.writestr("word/document.xml", b"A" * 5_000)

    response = client.post(
        "/api/report/style-profiles?filename=bomb.docx", headers=headers, content=buf.getvalue()
    )
    assert response.status_code == 400
    assert response.json()["detail"]["reason_code"] == "style_profile_zip_bomb"


def test_http_upload_requires_a_filename(client: TestClient, headers: dict[str, str]):
    response = client.post("/api/report/style-profiles", headers=headers, content=_build_minimal_docx())
    assert response.status_code == 400


# =============================================================================
# 6. Both-direction key pins
# =============================================================================

_PINNED_TOP_LEVEL_KEYS = {
    "kind",
    "page",
    "palette",
    "fonts",
    "heading_numbering",
    "table_style",
    "header",
    "footer",
    "caption",
    "not_extracted",
}

_PINNED_PALETTE_KEYS = {
    "title",
    "heading1",
    "heading2",
    "heading3",
    "body",
    "emphasis",
    "table_header_fill",
    "table_border",
    "header_footer_text",
}

_PINNED_LADDER_KEYS = {"title", "heading1", "heading2", "heading3", "body", "caption", "table"}


@pytest.fixture(scope="module")
def _minimal_profile() -> dict[str, Any]:
    return style_profile.public_profile(
        style_profile.extract_style_profile(_build_minimal_docx(), source_file="pins.docx")
    )


def test_top_level_profile_keys_are_pinned_both_directions(_minimal_profile: dict[str, Any]):
    assert set(_minimal_profile.keys()) == _PINNED_TOP_LEVEL_KEYS


def test_palette_keys_are_pinned_both_directions(_minimal_profile: dict[str, Any]):
    assert set(_minimal_profile["palette"].keys()) == _PINNED_PALETTE_KEYS


def test_font_ladder_keys_are_pinned_both_directions(_minimal_profile: dict[str, Any]):
    assert set(_minimal_profile["fonts"]["ladder_pt"].keys()) == _PINNED_LADDER_KEYS


def test_extracted_profile_is_json_serializable_with_no_private_keys(_minimal_profile: dict[str, Any]):
    """``public_profile`` must strip every ``_``-prefixed key (today: only
    ``header.logo._bytes``) -- this is what actually protects a caller from
    a raw-bytes leak into an HTTP response or a JSON file on disk."""
    serialized = json.dumps(_minimal_profile)  # raises if any value is not JSON-safe
    assert "_bytes" not in serialized

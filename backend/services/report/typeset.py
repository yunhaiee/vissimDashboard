"""Track R unit R-5a — the REPORT TYPESETTER (설계 문서의 "층 3 / 2. 적용").

One call, one Word file: R-1's section spine, filled with R-2's table figures
and R-3's graphic figures, typeset in the style R-4 extracted from a client's
own past report — or in the Prism default when no profile is chosen.

    compose_report(...) -> (docx bytes, manifest)
    compose_artifact_zip(...) -> (zip bytes, manifest)

WHAT THIS MODULE ASSEMBLES FROM, AND WHAT IT MUST NEVER READ
--------------------------------------------------------------
ONLY the R-1/R-2/R-3 payloads: ``spine.build_spine`` for the section list and
order, ``figures.all_figures`` for the four table figures,
``graphics.all_graphics`` for the three graphic figures. Nothing else is read;
no SQL, no workbook, no COM.

That fence is load-bearing, not tidiness. ``services/report_exports.py`` — the
PRE-EXISTING v1 .docx/CSV surface — assembles its "Flow / GEH 검증표" from
``flow_geh_verdict()``, the STALE WORKBOOK GEH path that R-2's own module
docstring records as deliberately excluded ("낡은 워크북 GEH 경로
flow_geh_verdict는 문서화된 배제 — 한 노드 두 숫자 금지"). R-2's
``geh_node_table`` scores the same nodes through ``build_reality_check_report``
instead. A document that pulled one section from each would print TWO different
GEH numbers for one node, each defensible on its own and irreconcilable
together — which is the exact failure the whole validation layer exists to
prevent. So this module imports ``services.report_exports`` NOWHERE, not even
for its private ``_setcell``/``_set_border`` cell helpers (which carry the
legacy palette in their default arguments and write neither text colour nor an
eastAsia font, so they could not serve a profile-driven typesetter anyway).
``tests/test_report_typeset.py`` holds a GREP GUARD over this module's source
for exactly that: an import of ``report_exports`` or a mention of
``flow_geh_verdict`` fails the suite.

The legacy exporters keep their names, their routes and their behaviour —
``POST /api/report/build``, ``GET /api/report/data/{block_type}`` and the
``services/report_builder.py`` block assembler are a SEPARATE, still-supported
CSV/legacy surface. This unit does not replace them and does not touch them.

SVG IN A DOCX — THE MEASUREMENT, AND THE DECISION IT FORCED
--------------------------------------------------------------
R-3's three figures are SVG strings. Two routes were considered and ONE was
measured, on this machine, against the installed toolchain:

* ``w14:svgBlip`` extension XML (Word 2016+) needs (i) an image PART with
  content type ``image/svg+xml`` and (ii) a RASTER fallback ``a:blip`` for
  every client that does not understand the extension. Measured:
  ``docx.image.constants.MIME_TYPE`` enumerates exactly ``BMP, GIF, JPEG,
  PNG, TIFF`` — no SVG member exists, and ``Document.add_picture`` on SVG
  bytes raises ``UnrecognizedImageError``; python-docx 1.2.0 cannot mint the
  part at all. The fallback is worse: no SVG rasteriser is installed
  (``cairosvg``, ``svglib``, ``reportlab``, ``cairocffi``, ``wand``, ``pyvips``
  all ``ModuleNotFoundError``), and PIL 12.3.0 — which IS installed — has no
  SVG codec: ``registered_extensions()`` carries no ``.svg`` and
  ``Image.open`` raises ``UnidentifiedImageError``. Adding a dependency was
  out of scope for this unit. The only fallback image obtainable without one
  is a fabricated placeholder — a picture that is not the figure — which is
  precisely the faked-embed this unit was told not to ship.
* **CHOSEN: the honest limitation.** A graphic figure lands in the DOCX as its
  DATA — the figure's ``meta`` rendered as a real, styled table, its legend
  written out, its source caption in full — plus an in-document line NAMING
  the SVG original's entry in the artifact zip
  (``POST /api/report/compose/artifacts``). The SVG itself ships in that zip,
  byte-identical to what ``GET /api/report/graphics`` serves.

That downgrade is NAMED, never silent: :data:`LIMITATION_SVG_NOT_EMBEDDED` is
appended to the manifest's ``limitations`` (with the measurement above as its
reason) whenever at least one graphic figure is typeset, and the in-document
line tells the reader of the DOCX — who may never see the manifest — where the
picture is.

ABSENCE IS RENDERED, NEVER SKIPPED
------------------------------------
Three DIFFERENT absences, three different renderings, because collapsing them
would be the lie:

* an ABSENT FIGURE (R-2/R-3's ``absent: True``) renders its own trilingual
  reason as a muted sentence under its own heading;
* a NOT_AVAILABLE required output (R-1's Table 5-15 audit) renders as a muted
  sentence naming the output and the reason no extractor exists;
* a section EXCLUDED BY SELECTION does not render its heading — the user asked
  for that — but it is listed by name in the document's front matter AND
  recorded in the manifest as ``excluded_by_selection``, which is a different
  manifest state from any evidence absence. R-1's rule is that a deleted
  section reads as a requirement that does not exist; a section the author
  consciously dropped still has to be visible AS dropped.

A figure's ``source_caption`` always survives into the document text, every
key of it — R-2/R-3's own release rule ("이 그림은 어느 실행의 것인가"에 답 못
하는 도판은 방출하지 않는다) would be void if the DOCX quietly dropped it.

NUMBERING
----------
Headings carry TYPED numbers (``1.``, ``1.1``, ``1.1.1``) rather than driving
``numbering.xml``, and the exact token shape — how many components, and
whether a trailing dot — is read off the PROFILE's own
``heading_numbering.detected`` rather than hardcoded. R-4's extractor reports
``scheme: "numbering_xml"`` for a sample that used Word's automatic numbering;
this build cannot reproduce that scheme, so it typesets decimal numbers and
NAMES the downgrade in the manifest (:data:`LIMITATION_NUMBERING_SCHEME`)
instead of pretending the schemes matched.

CAPTION PREFIX
---------------
A profile carries exactly ONE caption prefix (R-4's ``caption.prefix`` — "표 "
in the Dochak sample). This module uses it for EVERY figure caption, table and
graphic alike, because a second prefix ("그림 ") is not derivable from the
profile and inventing one would be fabricating style the client never showed
us. Named in the manifest as :data:`LIMITATION_SINGLE_CAPTION_PREFIX`.

LANGUAGE
---------
ko primary; ``en``/``ja`` read the SAME trilingual fields the payloads already
carry (figure titles, column headers, absence reasons, source-key
descriptions). Row VALUES are never translated — R-2's rule, unchanged here.
"""

from __future__ import annotations

import json
import logging
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Iterable, Mapping, Sequence

import docx
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from services.report import figures as report_figures
from services.report import graphics as report_graphics
from services.report import spine, style_apply
from services.report.default_style import DEFAULT_PROFILE_NAME, PRISM_DEFAULT_PROFILE
# A plain function import, not ``from services.report import style_profile``
# -- every composer function in this module takes a parameter literally
# NAMED ``style_profile`` (the profile dict), which would shadow a module
# import of the same name inside the function body.
from services.report.style_profile import normalize_display_text

logger = logging.getLogger(__name__)

TYPESET_MANIFEST_KIND = "prism.report_typeset_manifest.v1"

LANGUAGES = ("ko", "en", "ja")

#: The appendix that holds figures R-3 deliberately binds to NO spine section
#: (``graphics.NETWORK_SKETCH_SECTION_GAP``). A figure with a null section id
#: is never dropped — it gets this house section, which says in its own body
#: why it is not one of Main Roads' eight.
UNBOUND_SECTION_ID = "unbound_figures"

SECTION_STATE_INCLUDED = "included"
SECTION_STATE_EXCLUDED = "excluded_by_selection"

FIGURE_KIND_TABLE = "table"
FIGURE_KIND_GRAPHIC = "graphic"

#: What a ``None`` cell prints as. A blank cell reads as "we had nothing to
#: say here"; this reads as "the reader returned no value for this cell",
#: which is what R-2 actually means by a ``None``.
NULL_CELL = "—"

ARTIFACT_MANIFEST_NAME = "manifest.json"
ARTIFACT_SVG_DIR = "svg"

#: The ONLY style leaves this typesetter does not claim to reproduce when a
#: profile is applied and R-4's extractor is run back over the output, each
#: with the reason it is not claimed. Everything else in the extractor's schema
#: MUST come back identical — ``tests/test_report_typeset.py`` reads this map
#: and asserts exact equality on every leaf that is NOT in it, so a field
#: quietly dropped from the applier fails instead of vanishing from a
#: hand-maintained expectation list.
#:
#: All three entries are the same fact: ``heading_numbering.detected`` is not a
#: style property at all. R-4 reports the numbers it FOUND TYPED in the sample
#: document's own headings ("2.1.1", because that sample's one Heading 3 sat
#: under its section 2.1). A different document with a different section tree
#: types different numbers while using the identical numbering SCHEME. What is
#: reproducible — and what the test checks structurally instead — is the
#: scheme's shape: one component per heading level, and the trailing dot
#: exactly where the profile had one.
#: An entry added here SILENCES the oracle for that leaf, so this map is
#: itself locked, twice, by ``tests/test_report_typeset.py``:
#:
#: 1. its key set is pinned by EXACT set equality against the three literal
#:    paths below, so any addition fails a test and forces a deliberate,
#:    reviewable test edit rather than a one-line escape;
#: 2. every key must match :data:`NOT_CLAIMED_KEY_PATTERN` — the structural
#:    family "a number this document typed into its own headings". A palette,
#:    ladder or table leaf therefore cannot be admitted even by editing the
#:    pinned set: the predicate itself would have to be rewritten, which is
#:    exactly the visible act this lock exists to force.
#:
#: Measured, not hypothetical: a real regression (table border width hardcoded
#: to 3.0) was hidden from BOTH the 50-test unit scope and the 223-test report
#: scope by adding one plausible line to this map. Both locks exist because of
#: that.
ROUNDTRIP_NOT_CLAIMED: dict[str, str] = {
    "heading_numbering.detected.h1": (
        "the numbers a document shows are its own section tree's, not the style's — the SHAPE "
        "(component count, trailing dot) is what this typesetter reproduces"
    ),
    "heading_numbering.detected.h2": (
        "the numbers a document shows are its own section tree's, not the style's — the SHAPE "
        "(component count, trailing dot) is what this typesetter reproduces"
    ),
    "heading_numbering.detected.h3": (
        "the numbers a document shows are its own section tree's, not the style's — the SHAPE "
        "(component count, trailing dot) is what this typesetter reproduces"
    ),
}

#: The ONLY structural family a leaf may belong to and still be excused from
#: the round-trip oracle: a heading number this document typed into its own
#: text. See :data:`ROUNDTRIP_NOT_CLAIMED`'s second lock.
NOT_CLAIMED_KEY_PATTERN = r"^heading_numbering\.detected\.h\d+$"

#: Profile leaves the typesetter produces STRUCTURALLY rather than by applying
#: a value — the third bucket beside :data:`style_apply.APPLIED_FIELDS` and
#: :data:`ROUNDTRIP_NOT_CLAIMED`. Together the three must cover every leaf of
#: the profile schema EXACTLY: a leaf in none of them is a value nothing
#: consumes (a dead field that would sit in the default profile forever,
#: invisible to every test), and a leaf in two of them is an ambiguity about
#: who wrote it.
#:
#: This map's key set is pinned by exact set equality too, and by the same
#: reasoning: it is the other place a leaf could be quietly excused from
#: being applied.
DERIVED_LEAVES: dict[str, str] = {
    "kind": "the schema marker itself — R-4 stamps it on extraction; nothing applies it",
    "page.size": (
        "derived by the extractor from the page dimensions it read (A4 within tolerance, else "
        "Custom) — page.width_mm/height_mm are the applied fields it follows from"
    ),
    "footer.page_number.present": (
        "true exactly when a PAGE field was written; it follows from the format/position/size/"
        "color fields rather than being set on its own"
    ),
}


# ---------------------------------------------------------------------------
# Named limitations — module constants so a test pins the TEXT, not a string
# literal buried in a branch.
# ---------------------------------------------------------------------------

LIMITATION_SVG_NOT_EMBEDDED = {
    "code": "svg_not_embedded_in_docx",
    "reason": {
        "ko": (
            "python-docx 1.2.0은 SVG를 문서에 직접 넣을 수 없고(지원 이미지 형식은 BMP/GIF/"
            "JPEG/PNG/TIFF뿐), 이 환경에는 SVG 래스터라이저가 설치돼 있지 않습니다. "
            "그래픽 도판은 문서 안에 데이터(요약 표·범례·출처)로 조판하고, SVG 원본은 "
            "아티팩트 zip으로 함께 내보냅니다 — 가짜 이미지를 끼워 넣지 않습니다."
        ),
        "en": (
            "python-docx 1.2.0 cannot embed SVG (its supported image formats are BMP/GIF/JPEG/"
            "PNG/TIFF only) and no SVG rasteriser is installed in this environment. Graphic "
            "figures are typeset in the document as their DATA (summary table, legend, source "
            "caption) and the SVG originals ship in the artifact zip — no placeholder image is "
            "faked in their place."
        ),
        "ja": (
            "python-docx 1.2.0はSVGを文書に直接埋め込めず（対応画像形式はBMP/GIF/JPEG/PNG/"
            "TIFFのみ）、この環境にはSVGラスタライザがインストールされていません。グラフィック"
            "図版は文書内にデータ（要約表・凡例・出典）として組版し、SVG原本はアーティファクト"
            "zipで出力します — 偽の画像は挿入しません。"
        ),
    },
}

LIMITATION_NUMBERING_SCHEME = {
    "code": "heading_numbering_scheme_downgraded",
    "reason": {
        "ko": (
            "스타일 프로파일의 제목 번호 체계가 자동 번호(numbering.xml)로 추출되었으나 이 "
            "조판기는 번호를 텍스트로 타이핑합니다 — 번호 모양은 같아도 Word의 자동 갱신은 "
            "따라가지 못합니다."
        ),
        "en": (
            "The style profile's heading numbering was extracted as Word automatic numbering "
            "(numbering.xml), but this typesetter types its numbers as text — the shape matches, "
            "Word's automatic renumbering does not."
        ),
        "ja": (
            "スタイルプロファイルの見出し番号体系は自動番号(numbering.xml)として抽出されました"
            "が、この組版機は番号をテキストとして入力します — 形は同じでもWordの自動更新には"
            "追随しません。"
        ),
    },
}

LIMITATION_SINGLE_CAPTION_PREFIX = {
    "code": "single_caption_prefix",
    "reason": {
        "ko": (
            "스타일 프로파일은 캡션 접두어를 하나만 담습니다. 표와 그래픽 도판 모두 그 하나를 "
            "씁니다 — 프로파일에 없는 두 번째 접두어를 지어내지 않습니다."
        ),
        "en": (
            "A style profile carries exactly one caption prefix. Both table and graphic figures "
            "use it — a second prefix the profile never showed is not invented here."
        ),
        "ja": (
            "スタイルプロファイルはキャプション接頭辞を一つだけ持ちます。表もグラフィック図版も"
            "その一つを使います — プロファイルにない二つ目の接頭辞は作りません。"
        ),
    },
}

LIMITATION_NUMBERING_DEPTH = {
    "code": "heading_numbering_depth_mismatch",
    "reason": {
        "ko": (
            "스타일 프로파일이 보고한 제목 번호 예시의 자릿수가 그 제목 수준과 다릅니다. 이 "
            "조판기는 수준에 맞는 자릿수로 번호를 매기고, 후행 마침표 유무만 프로파일을 "
            "따릅니다."
        ),
        "en": (
            "The heading-number example the style profile reported has a different component "
            "count from its own heading level. This typesetter numbers by level and follows the "
            "profile only for the trailing dot."
        ),
        "ja": (
            "スタイルプロファイルが報告した見出し番号の例は、その見出しレベルと桁数が異なり"
            "ます。この組版機はレベルどおりの桁数で採番し、末尾のピリオドの有無のみプロ"
            "ファイルに従います。"
        ),
    },
}


# ---------------------------------------------------------------------------
# Refusals — the shape services.report.style_profile.StyleProfileRefused,
# services.calibration_attestation.StepAttestationRefused and
# services.demand_target_map already use, so routers/report.py translates it
# into HTTPException(status, exc.to_dict()) the same one-line way.
# ---------------------------------------------------------------------------


class TypesetRefused(RuntimeError):
    """A named refusal. Never a bare exception across this module's boundary.

    The SAME envelope ``services.report.style_profile.StyleProfileRefused``
    already puts on the wire from this very router
    (``{"ok": false, "reason_code": ..., "message": {ko/en/ja}, ...}``), so a
    client of ``/api/report`` parses one refusal shape rather than two.
    """

    def __init__(self, code: str, messages: Mapping[str, str], *, status: int = 422, **detail: Any) -> None:
        super().__init__(f"{code}: {messages.get('en') or ''}")
        self.code = code
        self.messages = dict(messages)
        self.status = status
        self.detail = dict(detail)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "reason_code": self.code, "message": dict(self.messages), **self.detail}


def _refuse(code: str, ko: str, en: str, ja: str, *, status: int = 422, **detail: Any) -> TypesetRefused:
    return TypesetRefused(code, {"ko": ko, "en": en, "ja": ja}, status=status, **detail)


# ---------------------------------------------------------------------------
# Trilingual label table. Every user-facing string this module writes into a
# document lives here — ko primary, en/ja beside it, per CLAUDE.md.
# ---------------------------------------------------------------------------

_L: dict[str, dict[str, str]] = {
    "report_title": {
        "ko": "Vissim 모델 보정·검증 보고서",
        "en": "Vissim model calibration and validation report",
        "ja": "Vissimモデル キャリブレーション・検証レポート",
    },
    "generated": {"ko": "생성", "en": "Generated", "ja": "生成"},
    "style_profile": {"ko": "스타일 프로파일", "en": "Style profile", "ja": "スタイルプロファイル"},
    "honesty_line": {
        "ko": "명명된 부재 {absences}건 · 선택에서 제외된 절 {excluded}건 — 이 보고서는 빈 자리를 지우지 않고 이름으로 남깁니다.",
        "en": "{absences} named absence(s) · {excluded} section(s) excluded by selection — this report names its gaps instead of deleting them.",
        "ja": "命名された不在{absences}件・選択から除外された節{excluded}件 — このレポートは空白を消さず名前で残します。",
    },
    "excluded_sections": {
        "ko": "선택에서 제외된 절",
        "en": "Sections excluded by selection",
        "ja": "選択から除外された節",
    },
    "basis": {"ko": "근거", "en": "Basis", "ja": "根拠"},
    "figures": {"ko": "도판과 증거", "en": "Figures and evidence", "ja": "図版と証拠"},
    "named_absence": {"ko": "명명된 부재", "en": "Named absence", "ja": "命名された不在"},
    "no_evidence": {"ko": "증거 없음", "en": "No evidence", "ja": "証拠なし"},
    "no_figures_bound": {
        "ko": "이 절에 결박된 도판이 이 빌드에 없습니다 — 절을 지우지 않고 그 사실을 남깁니다.",
        "en": "No figure in this build is bound to this section — the section stays, and says so.",
        "ja": "このビルドにはこの節に結び付いた図版がありません — 節は残し、その事実を記します。",
    },
    "not_available_output": {
        "ko": "이 빌드에서 추출 불가",
        "en": "Not extractable in this build",
        "ja": "このビルドでは抽出不可",
    },
    "source": {"ko": "출처", "en": "Source", "ja": "出典"},
    "citation": {"ko": "인용", "en": "Citation", "ja": "引用"},
    "legend": {"ko": "범례", "en": "Legend", "ja": "凡例"},
    "svg_original": {"ko": "도판 원본(SVG)", "en": "Figure original (SVG)", "ja": "図版原本(SVG)"},
    "in_artifact_zip": {
        "ko": "아티팩트 zip에 포함 (POST /api/report/compose/artifacts)",
        "en": "shipped in the artifact zip (POST /api/report/compose/artifacts)",
        "ja": "アーティファクトzipに同梱 (POST /api/report/compose/artifacts)",
    },
    "meta_key": {"ko": "항목", "en": "Item", "ja": "項目"},
    "meta_value": {"ko": "값", "en": "Value", "ja": "値"},
    "none_value": {"ko": "없음", "en": "none", "ja": "なし"},
    "unbound_title": {
        "ko": "부록 — 스파인에 결박되지 않은 도판",
        "en": "Appendix — figures bound to no spine section",
        "ja": "付録 — スパインに結び付かない図版",
    },
}


def _t(key: str, language: str, **fmt: Any) -> str:
    entry = _L[key]
    text = entry.get(language) or entry["ko"]
    return text.format(**fmt) if fmt else text


def _pick(value: Any, language: str) -> str:
    """A ko/en/ja triple rendered in ``language`` (ko is the fallback — the
    product's primary, per CLAUDE.md). Anything else is stringified as-is."""
    if isinstance(value, Mapping):
        return str(value.get(language) or value.get("ko") or "")
    return "" if value is None else str(value)


def _now_iso() -> str:
    """``datetime.now(timezone.utc).isoformat()`` — the same call
    ``services/report/figures.py``'s ``_generated_at`` and
    ``services/genesis_jobs.py``'s ``_now_iso`` make."""
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Paragraph writers
# ---------------------------------------------------------------------------


def _muted(document: Any, applied: style_apply.AppliedStyle, text: str) -> Any:
    """A body paragraph in the profile's muted header/footer grey at caption
    size. Deliberately NOT bold: R-4 reads ``palette.emphasis`` off bold runs
    that carry an explicit RGB colour, so a bold muted run would make the
    emphasis colour ambiguous in this document's own re-extraction."""
    paragraph = document.add_paragraph(style="Normal")
    run = paragraph.add_run(text)
    run.font.size = Pt(float(applied.value("fonts.ladder_pt.caption")))
    run.font.color.rgb = RGBColor.from_string(str(applied.value("palette.header_footer_text")))
    return paragraph


def _emphasis(document: Any, applied: style_apply.AppliedStyle, text: str) -> Any:
    paragraph = document.add_paragraph(style="Normal")
    run = paragraph.add_run(text)
    run.bold = True
    run.font.color.rgb = RGBColor.from_string(str(applied.value("palette.emphasis")))
    return paragraph


def _caption_paragraph(document: Any, applied: style_apply.AppliedStyle, text: str) -> Any:
    paragraph = document.add_paragraph(style="Caption")
    paragraph.add_run(text)
    return paragraph


def _heading(document: Any, level: int, number: str, title: str) -> Any:
    style_name = {1: "Heading 1", 2: "Heading 2", 3: "Heading 3"}[level]
    paragraph = document.add_paragraph(style=style_name)
    paragraph.add_run(f"{number} {title}" if number else title)
    return paragraph


# ---------------------------------------------------------------------------
# Table writer — every table in the document is written here, so "the profile's
# table style" is one code path rather than one per figure kind.
# ---------------------------------------------------------------------------


def _write_table(
    document: Any,
    applied: style_apply.AppliedStyle,
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
) -> Any:
    table = document.add_table(rows=1 + len(rows), cols=max(1, len(headers)))
    _table_borders(
        table,
        color_hex=str(applied.value("table_style.borders.color")),
        width_pt=float(applied.value("table_style.borders.width_pt")),
    )

    header_fill = str(applied.value("table_style.header_row.fill"))
    header_color = str(applied.value("table_style.header_row.text_color"))
    header_bold = bool(applied.value("table_style.header_row.bold"))
    header_size = float(applied.value("table_style.header_row.size_pt"))
    body_size = float(applied.value("fonts.ladder_pt.table"))
    # Table text is body-class, not its own font family in the schema (there
    # is no "table family" leaf — only body_family/heading_family exist).
    # MEASURED: a table cell's paragraph carries no w:pStyle at all, so it
    # resolves fonts only through whichever style is marked w:default="1" —
    # correct today because that IS Normal, but a run is the highest-priority
    # OOXML formatting level, so writing the family directly here removes the
    # dependency on that indirection entirely (see style_apply.set_run_fonts's
    # docstring for the fuller measurement).
    ascii_family = str(applied.value("fonts.body_family.ascii"))
    east_asia = str(applied.value("fonts.body_family.eastAsia"))

    for index, text in enumerate(headers):
        cell = table.rows[0].cells[index]
        _write_cell(
            cell,
            text,
            size_pt=header_size,
            bold=header_bold,
            color_hex=header_color,
            fill=header_fill,
            ascii_family=ascii_family,
            east_asia=east_asia,
        )
    for row_index, row in enumerate(rows, start=1):
        for index in range(len(headers)):
            value = row[index] if index < len(row) else NULL_CELL
            _write_cell(
                table.rows[row_index].cells[index],
                value,
                size_pt=body_size,
                ascii_family=ascii_family,
                east_asia=east_asia,
            )
    return table


def _write_cell(
    cell: Any,
    text: str,
    *,
    size_pt: float,
    ascii_family: str,
    east_asia: str,
    bold: bool = False,
    color_hex: str | None = None,
    fill: str | None = None,
) -> None:
    cell.text = ""
    paragraph = cell.paragraphs[0]
    run = paragraph.add_run(text if text not in (None, "") else NULL_CELL)
    run.font.size = Pt(size_pt)
    style_apply.set_run_fonts(run, ascii_family=ascii_family, east_asia=east_asia)
    # ALWAYS written, never only when true: a profile whose header row is
    # deliberately NOT bold is a real style statement, and leaving ``run.bold``
    # unset would make R-4 read it back as ``None`` — "no explicit bold on the
    # header cell", a NAMED ABSENCE — instead of the explicit ``False`` the
    # profile actually asked for. "Off" and "unstated" are different facts.
    run.bold = bool(bold)
    if color_hex:
        run.font.color.rgb = RGBColor.from_string(color_hex)
    if fill:
        tc_pr = cell._tc.get_or_add_tcPr()
        shd = OxmlElement("w:shd")
        shd.set(qn("w:val"), "clear")
        shd.set(qn("w:color"), "auto")
        shd.set(qn("w:fill"), fill)
        tc_pr.append(shd)


def _table_borders(table: Any, *, color_hex: str, width_pt: float) -> None:
    """``w:tblBorders`` on the table itself — the FIRST place R-4's
    ``_table_borders`` looks (its per-cell ``w:tcBorders`` read is only the
    fallback), so a profile's border colour and width are read back from where
    they were written."""
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    eighths = str(max(1, int(round(width_pt * 8))))
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), eighths)
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), color_hex)
        borders.append(element)
    tbl_pr.append(borders)


# ---------------------------------------------------------------------------
# Numbering
# ---------------------------------------------------------------------------


def _number(level: int, counters: Sequence[int], applied: style_apply.AppliedStyle) -> str:
    pattern = applied.value(f"heading_numbering.detected.h{level}")
    trailing_dot = isinstance(pattern, str) and pattern.endswith(".")
    text = ".".join(str(value) for value in counters[:level])
    return text + ("." if trailing_dot else "")


def _numbering_depth_mismatch(applied: style_apply.AppliedStyle) -> bool:
    for level in (1, 2, 3):
        pattern = applied.value(f"heading_numbering.detected.h{level}")
        if not isinstance(pattern, str) or not pattern:
            continue
        components = [part for part in pattern.rstrip(".").split(".") if part]
        if len(components) != level:
            return True
    return False


# ---------------------------------------------------------------------------
# Source captions — every key survives, always.
# ---------------------------------------------------------------------------


def _caption_value(value: Any, language: str) -> str:
    if value is None:
        return _t("none_value", language)
    if isinstance(value, (list, tuple)):
        return ", ".join(_caption_value(item, language) for item in value) or _t("none_value", language)
    return str(value)


def source_caption_text(caption: Mapping[str, Any] | None, language: str) -> str:
    """``출처: k=v · k=v · …`` over EVERY key the caption carries, sorted.

    Sorted rather than in the payload's own dict order because R-2 and R-3
    declare DIFFERENT key sets (``run_id``/``session_id``/… vs
    ``scenario_id``/…) and a stable, kind-independent rendering is what lets
    one test assert "no key was dropped" for both.
    """
    label = _t("source", language)
    if not isinstance(caption, Mapping) or not caption:
        return f"{label}: {_t('none_value', language)}"
    parts = [f"{key}={_caption_value(caption[key], language)}" for key in sorted(caption)]
    return f"{label}: " + " · ".join(parts)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _validate_language(language: str) -> str:
    if language not in LANGUAGES:
        raise _refuse(
            "typeset_unsupported_language",
            f"'{language}'는 지원하지 않는 언어입니다 (지원: {', '.join(LANGUAGES)}).",
            f"'{language}' is not a supported language (supported: {', '.join(LANGUAGES)}).",
            f"'{language}'はサポートされていない言語です（対応: {', '.join(LANGUAGES)}）。",
            status=422,
            language=language,
            supported=list(LANGUAGES),
        )
    return language


def _selected_section_ids(selection: Mapping[str, Any] | None, known: Sequence[str]) -> list[str]:
    if selection is None:
        return list(known)
    if not isinstance(selection, Mapping):
        raise _refuse(
            "typeset_bad_selection",
            "선택(selection)은 객체여야 합니다.",
            "selection must be an object.",
            "selection はオブジェクトである必要があります。",
            status=422,
        )
    requested = selection.get("sections")
    if requested is None:
        return list(known)
    if not isinstance(requested, (list, tuple)):
        raise _refuse(
            "typeset_bad_selection",
            "selection.sections는 절 id 목록이어야 합니다.",
            "selection.sections must be a list of section ids.",
            "selection.sections は節idのリストである必要があります。",
            status=422,
        )
    ids = [str(item) for item in requested]
    unknown = sorted({item for item in ids if item not in set(known)})
    if unknown:
        raise _refuse(
            "typeset_unknown_section",
            f"이 스파인에 없는 절 id입니다: {', '.join(unknown)} (있는 것: {', '.join(known)}).",
            f"No such section id in this spine: {', '.join(unknown)} (available: {', '.join(known)}).",
            f"このスパインに存在しない節idです: {', '.join(unknown)}（存在するもの: {', '.join(known)}）。",
            status=422,
            unknown=unknown,
            available=list(known),
        )
    if not ids:
        raise _refuse(
            "typeset_empty_selection",
            "선택된 절이 하나도 없습니다 — 절이 없는 보고서는 만들지 않습니다.",
            "No section is selected — a report with no sections is not produced.",
            "選択された節が一つもありません — 節のないレポートは作成しません。",
            status=422,
        )
    # Selection never reorders: the spine's order is the guideline's, and
    # R-1 pins it. A caller's list order is read as a SET.
    chosen = set(ids)
    return [item for item in known if item in chosen]


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def compose_report(
    *,
    selection: Mapping[str, Any] | None = None,
    style_profile: Mapping[str, Any] | None = None,
    logo_bytes: bytes | None = None,
    language: str = "ko",
    title: str | None = None,
    profile_id: str | None = None,
    profile_name: str | None = None,
    scenario_id: str | None = None,
    period: str | None = None,
    observation_set_id: str | None = None,
    observation_period: str | None = None,
    session_id: str | None = None,
    before_scenario_id: str | None = None,
    after_scenario_id: str | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Assemble the whole report. Returns ``(docx bytes, manifest)``.

    ``style_profile`` is a profile dict in R-4's schema (a stored record's
    ``resolved_profile``, or ``None`` for the Prism default). ``selection``
    is ``{"sections": [id, ...]}``; ``None`` means every section. The figure
    inputs are the same parameters ``GET /api/report/figures`` and
    ``GET /api/report/graphics`` already take, passed straight through.

    Raises only :class:`TypesetRefused`.
    """
    result = _compose(
        selection=selection,
        style_profile=style_profile,
        logo_bytes=logo_bytes,
        language=language,
        title=title,
        profile_id=profile_id,
        profile_name=profile_name,
        scenario_id=scenario_id,
        period=period,
        observation_set_id=observation_set_id,
        observation_period=observation_period,
        session_id=session_id,
        before_scenario_id=before_scenario_id,
        after_scenario_id=after_scenario_id,
    )
    return result["docx"], result["manifest"]


def compose_artifact_zip(**kwargs: Any) -> tuple[bytes, dict[str, Any]]:
    """The sibling artifact bundle: every graphic figure's SVG ORIGINAL plus
    ``manifest.json``. Same parameters as :func:`compose_report`.

    The SVG bytes are the ones ``GET /api/report/graphics`` serves, untouched —
    this is where the pictures the DOCX could not embed actually live (see the
    module docstring's SVG measurement).
    """
    result = _compose(**kwargs)
    manifest = result["manifest"]
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, svg in sorted(result["artifacts"].items()):
            archive.writestr(name, svg)
        archive.writestr(
            ARTIFACT_MANIFEST_NAME,
            # ``default=str`` so a caller that hands in a profile still
            # carrying R-4's private ``header.logo["_bytes"]`` (the extractor's
            # in-process shape, before ``public_profile`` strips it) gets a
            # manifest rather than a TypeError 500 at the last line.
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True, default=str),
        )
    return buffer.getvalue(), manifest


def _compose(
    *,
    selection: Mapping[str, Any] | None = None,
    style_profile: Mapping[str, Any] | None = None,
    logo_bytes: bytes | None = None,
    language: str = "ko",
    title: str | None = None,
    profile_id: str | None = None,
    profile_name: str | None = None,
    scenario_id: str | None = None,
    period: str | None = None,
    observation_set_id: str | None = None,
    observation_period: str | None = None,
    session_id: str | None = None,
    before_scenario_id: str | None = None,
    after_scenario_id: str | None = None,
) -> dict[str, Any]:
    language = _validate_language(language)
    # Heals an ALREADY-STORED NFD profile name (uploaded before the door fix
    # in routers/report.py normalized it) on this compose, with no re-upload
    # required -- see services.report.style_profile.normalize_display_text's
    # docstring for the full measurement. Both this manifest's "profile_name"
    # and the front-matter line _write_front_matter renders read this same
    # normalized value, so there is exactly one place this heals from.
    profile_name = normalize_display_text(profile_name)
    sections = spine.build_spine()

    gathered = _gather_figures(
        scenario_id=scenario_id,
        period=period,
        observation_set_id=observation_set_id,
        observation_period=observation_period,
        session_id=session_id,
        before_scenario_id=before_scenario_id,
        after_scenario_id=after_scenario_id,
    )
    by_section = _group_by_section(gathered, [section.id for section in sections])

    known_ids = [section.id for section in sections]
    if by_section.get(UNBOUND_SECTION_ID):
        known_ids = known_ids + [UNBOUND_SECTION_ID]
    included = _selected_section_ids(selection, known_ids)
    included_set = set(included)

    document = docx.Document()
    applied = style_apply.apply_profile(document, style_profile, logo_bytes=logo_bytes)

    generated_at = _now_iso()
    manifest: dict[str, Any] = {
        "kind": TYPESET_MANIFEST_KIND,
        "generated_at": generated_at,
        "language": language,
        "title": title or _t("report_title", language),
        "style": {
            "source": "profile" if style_profile else "default",
            "profile_id": profile_id,
            "profile_name": profile_name or (None if style_profile else DEFAULT_PROFILE_NAME),
            "fallbacks": applied.fallbacks,
            "resolved": dict(applied.resolved),
        },
        "sections": [],
        "figures": [],
        "artifacts": [],
        "limitations": [dict(LIMITATION_SINGLE_CAPTION_PREFIX)],
    }
    for entry in applied.limitations:
        manifest["limitations"].append(dict(entry))
    if str(applied.value("heading_numbering.scheme")) == "numbering_xml":
        manifest["limitations"].append(dict(LIMITATION_NUMBERING_SCHEME))
    if _numbering_depth_mismatch(applied):
        manifest["limitations"].append(dict(LIMITATION_NUMBERING_DEPTH))

    artifacts: dict[str, str] = {}

    # -- section records first, so the front matter can state the counts ---
    excluded = [section for section in sections if section.id not in included_set]
    absence_count = _count_named_absences(by_section, included_set, sections)

    _write_front_matter(
        document,
        applied,
        language=language,
        title=manifest["title"],
        generated_at=generated_at,
        style_name=manifest["style"]["profile_name"],
        absences=absence_count,
        excluded=excluded,
        excluded_unbound=(UNBOUND_SECTION_ID in known_ids and UNBOUND_SECTION_ID not in included_set),
        has_graphics=any(
            item["kind"] == FIGURE_KIND_GRAPHIC and not item["figure"].get("absent") for item in gathered
        ),
    )

    caption_prefix = str(applied.value("caption.prefix"))
    caption_counter = 0
    h1 = 0
    for section in sections:
        if section.id not in included_set:
            manifest["sections"].append(
                {
                    "id": section.id,
                    "title": _pick(
                        {"ko": section.title_ko, "en": section.title_en, "ja": section.title_ja}, language
                    ),
                    "state": SECTION_STATE_EXCLUDED,
                    "heading_number": None,
                    "figure_ids": [item["figure"].get("figure_id") for item in by_section.get(section.id, [])],
                    "named_absences": [],
                }
            )
            continue
        h1 += 1
        caption_counter, record = _write_section(
            document,
            applied,
            language=language,
            heading_index=h1,
            section_id=section.id,
            title=_pick({"ko": section.title_ko, "en": section.title_en, "ja": section.title_ja}, language),
            basis=list(section.basis),
            items=by_section.get(section.id, []),
            not_available=_not_available_outputs(section),
            caption_prefix=caption_prefix,
            caption_counter=caption_counter,
            artifacts=artifacts,
            manifest=manifest,
        )
        manifest["sections"].append(record)

    if UNBOUND_SECTION_ID in included_set and by_section.get(UNBOUND_SECTION_ID):
        h1 += 1
        caption_counter, record = _write_section(
            document,
            applied,
            language=language,
            heading_index=h1,
            section_id=UNBOUND_SECTION_ID,
            title=_t("unbound_title", language),
            basis=[report_graphics.NETWORK_SKETCH_SECTION_GAP],
            items=by_section[UNBOUND_SECTION_ID],
            not_available=[],
            caption_prefix=caption_prefix,
            caption_counter=caption_counter,
            artifacts=artifacts,
            manifest=manifest,
        )
        manifest["sections"].append(record)
    elif by_section.get(UNBOUND_SECTION_ID):
        manifest["sections"].append(
            {
                "id": UNBOUND_SECTION_ID,
                "title": _t("unbound_title", language),
                "state": SECTION_STATE_EXCLUDED,
                "heading_number": None,
                "figure_ids": [item["figure"].get("figure_id") for item in by_section[UNBOUND_SECTION_ID]],
                "named_absences": [],
            }
        )

    if artifacts:
        manifest["limitations"].append(dict(LIMITATION_SVG_NOT_EMBEDDED))
    manifest["artifacts"] = [
        {"name": name, "content_type": "image/svg+xml", "bytes": len(svg.encode("utf-8"))}
        for name, svg in sorted(artifacts.items())
    ]

    buffer = BytesIO()
    document.save(buffer)
    return {"docx": buffer.getvalue(), "manifest": manifest, "artifacts": artifacts}


# ---------------------------------------------------------------------------
# Gathering
# ---------------------------------------------------------------------------


def _gather_figures(
    *,
    scenario_id: str | None,
    period: str | None,
    observation_set_id: str | None,
    observation_period: str | None,
    session_id: str | None,
    before_scenario_id: str | None,
    after_scenario_id: str | None,
) -> list[dict[str, Any]]:
    """R-2's four and R-3's three, tagged with their kind, in payload order.

    Each aggregator already gathers its own members independently ("degrade
    one, not all"); this only tags them, and never re-reads a figure a second
    way.
    """
    items: list[dict[str, Any]] = []
    for figure in report_figures.all_figures(
        scenario_id=scenario_id,
        period=period,
        observation_set_id=observation_set_id,
        observation_period=observation_period,
        session_id=session_id,
        language="ko",
    ):
        items.append({"kind": FIGURE_KIND_TABLE, "figure": figure})
    for figure in report_graphics.all_graphics(
        scenario_id=scenario_id,
        period=period,
        observation_set_id=observation_set_id,
        observation_period=observation_period,
        before_scenario_id=before_scenario_id,
        after_scenario_id=after_scenario_id,
    ):
        items.append({"kind": FIGURE_KIND_GRAPHIC, "figure": figure})
    return items


def _group_by_section(items: Iterable[dict[str, Any]], section_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    known = set(section_ids)
    for item in items:
        section_id = item["figure"].get("spine_section_id")
        key = str(section_id) if section_id in known else UNBOUND_SECTION_ID
        grouped.setdefault(key, []).append(item)
    return grouped


def _not_available_outputs(section: spine.ReportSection) -> list[tuple[str, str]]:
    """``(output name, reason)`` for every Table 5-15 output this section owes
    that R-1's audit marks NOT_AVAILABLE."""
    out: list[tuple[str, str]] = []
    for row_type in section.required_output_types:
        row = spine.table_5_15_row(row_type)
        for output in row.outputs:
            if output.availability == spine.NOT_AVAILABLE:
                out.append((f"{row.type} · {output.name}", output.reason or ""))
    return out


def _count_named_absences(
    by_section: Mapping[str, list[dict[str, Any]]],
    included: set[str],
    sections: Sequence[spine.ReportSection],
) -> int:
    """How many named absences the INCLUDED part of the document will carry.

    Counted off the same ``by_section`` grouping the writers use, never off a
    second walk of the raw list — a front-matter count that disagreed with the
    body would be its own small lie.
    """
    count = 0
    for section_id, items in by_section.items():
        if section_id not in included:
            continue
        count += sum(1 for item in items if item["figure"].get("absent"))
    for section in sections:
        if section.id in included:
            count += len(_not_available_outputs(section))
            # A section with no figure bound to it renders one "명명된 부재"
            # block of its own (see ``_write_section``'s ``if not items``).
            if not by_section.get(section.id):
                count += 1
    return count


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------


def _write_front_matter(
    document: Any,
    applied: style_apply.AppliedStyle,
    *,
    language: str,
    title: str,
    generated_at: str,
    style_name: str | None,
    absences: int,
    excluded: Sequence[spine.ReportSection],
    excluded_unbound: bool,
    has_graphics: bool,
) -> None:
    heading = document.add_paragraph(style="Title")
    heading.add_run(title)

    _muted(
        document,
        applied,
        f"{_t('generated', language)}: {generated_at} · "
        f"{_t('style_profile', language)}: {style_name or _t('none_value', language)}",
    )

    excluded_count = len(excluded) + (1 if excluded_unbound else 0)
    _emphasis(
        document,
        applied,
        _t("honesty_line", language, absences=absences, excluded=excluded_count),
    )

    if excluded_count:
        names = [
            _pick({"ko": section.title_ko, "en": section.title_en, "ja": section.title_ja}, language)
            for section in excluded
        ]
        if excluded_unbound:
            names.append(_t("unbound_title", language))
        _muted(document, applied, f"{_t('excluded_sections', language)}: " + " · ".join(names))

    if has_graphics:
        _muted(document, applied, _pick(LIMITATION_SVG_NOT_EMBEDDED["reason"], language))


def _write_section(
    document: Any,
    applied: style_apply.AppliedStyle,
    *,
    language: str,
    heading_index: int,
    section_id: str,
    title: str,
    basis: Sequence[str],
    items: Sequence[dict[str, Any]],
    not_available: Sequence[tuple[str, str]],
    caption_prefix: str,
    caption_counter: int,
    artifacts: dict[str, str],
    manifest: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    named_absences: list[dict[str, Any]] = []

    _heading(document, 1, _number(1, [heading_index], applied), title)

    # -- N.1 근거 ---------------------------------------------------------
    _heading(document, 2, _number(2, [heading_index, 1], applied), _t("basis", language))
    for sentence in basis:
        _muted(document, applied, sentence)

    # -- N.2 도판과 증거 ---------------------------------------------------
    _heading(document, 2, _number(2, [heading_index, 2], applied), _t("figures", language))
    h3 = 0
    for item in items:
        h3 += 1
        figure = item["figure"]
        figure_id = str(figure.get("figure_id") or "")
        number = _number(3, [heading_index, 2, h3], applied)
        if figure.get("absent"):
            _heading(document, 3, number, figure_id)
            reason = _pick(figure.get("reason"), language)
            _muted(document, applied, f"{_t('no_evidence', language)} — {reason}")
            named_absences.append({"figure_id": figure_id, "reason": reason})
            manifest["figures"].append(
                {
                    "figure_id": figure_id,
                    "kind": item["kind"],
                    "section_id": section_id,
                    "absent": True,
                    "caption": None,
                    "artifact": None,
                }
            )
            continue

        figure_title = _pick(figure.get("title"), language) or figure_id
        _heading(document, 3, number, figure_title)
        caption_counter += 1
        caption_text = f"{caption_prefix}{caption_counter}. {figure_title}"
        _caption_paragraph(document, applied, caption_text)

        artifact_name: str | None = None
        if item["kind"] == FIGURE_KIND_TABLE:
            _write_figure_table(document, applied, figure, language)
        else:
            _write_graphic_block(document, applied, figure, language)
            svg = figure.get("svg")
            if isinstance(svg, str) and svg:
                artifact_name = f"{ARTIFACT_SVG_DIR}/{figure_id}.svg"
                artifacts[artifact_name] = svg
                _muted(
                    document,
                    applied,
                    f"{_t('svg_original', language)}: {artifact_name} — {_t('in_artifact_zip', language)}",
                )

        _muted(document, applied, source_caption_text(figure.get("source_caption"), language))
        citation = figure.get("citation")
        if citation:
            _muted(document, applied, f"{_t('citation', language)}: {citation}")

        manifest["figures"].append(
            {
                "figure_id": figure_id,
                "kind": item["kind"],
                "section_id": section_id,
                "absent": False,
                "caption": caption_text,
                "artifact": artifact_name,
            }
        )

    if not items:
        h3 += 1
        _heading(document, 3, _number(3, [heading_index, 2, h3], applied), _t("named_absence", language))
        _muted(document, applied, _t("no_figures_bound", language))
        named_absences.append({"figure_id": None, "reason": _t("no_figures_bound", language)})

    for name, reason in not_available:
        sentence = f"{_t('not_available_output', language)} — {name}: {reason}"
        _muted(document, applied, sentence)
        named_absences.append({"figure_id": None, "reason": sentence})

    record = {
        "id": section_id,
        "title": title,
        "state": SECTION_STATE_INCLUDED,
        "heading_number": _number(1, [heading_index], applied),
        "figure_ids": [str(item["figure"].get("figure_id") or "") for item in items],
        "named_absences": named_absences,
    }
    return caption_counter, record


def _write_figure_table(
    document: Any, applied: style_apply.AppliedStyle, figure: Mapping[str, Any], language: str
) -> None:
    columns = figure.get("columns") or []
    headers = [_pick(column, language) or str(column.get("key") or "") for column in columns]
    keys = [str(column.get("key") or "") for column in columns]
    rows: list[list[str]] = []
    for row in figure.get("rows") or []:
        rows.append([_cell_text(row.get(key) if isinstance(row, Mapping) else None, language) for key in keys])
    _write_table(document, applied, headers, rows)


def _write_graphic_block(
    document: Any, applied: style_apply.AppliedStyle, figure: Mapping[str, Any], language: str
) -> None:
    """A graphic figure, rendered as its DATA. See the module docstring's SVG
    measurement for why this is the picture's stand-in and not a fake image."""
    meta = figure.get("meta") or {}
    headers = [_t("meta_key", language), _t("meta_value", language)]
    rows = [[str(key), _cell_text(meta[key], language)] for key in sorted(meta)] if isinstance(meta, Mapping) else []
    _write_table(document, applied, headers, rows)

    legend = figure.get("legend") or []
    if legend:
        labels = [f"{_pick(entry, language)}" for entry in legend if isinstance(entry, Mapping)]
        _muted(document, applied, f"{_t('legend', language)}: " + " · ".join(label for label in labels if label))


def _cell_text(value: Any, language: str) -> str:
    if value is None:
        return NULL_CELL
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(_cell_text(item, language) for item in value) or NULL_CELL
    if isinstance(value, Mapping):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


__all__ = [
    "ARTIFACT_MANIFEST_NAME",
    "ARTIFACT_SVG_DIR",
    "FIGURE_KIND_GRAPHIC",
    "FIGURE_KIND_TABLE",
    "LANGUAGES",
    "LIMITATION_NUMBERING_DEPTH",
    "LIMITATION_NUMBERING_SCHEME",
    "LIMITATION_SINGLE_CAPTION_PREFIX",
    "LIMITATION_SVG_NOT_EMBEDDED",
    "DERIVED_LEAVES",
    "NOT_CLAIMED_KEY_PATTERN",
    "NULL_CELL",
    "ROUNDTRIP_NOT_CLAIMED",
    "SECTION_STATE_EXCLUDED",
    "SECTION_STATE_INCLUDED",
    "TYPESET_MANIFEST_KIND",
    "TypesetRefused",
    "UNBOUND_SECTION_ID",
    "compose_artifact_zip",
    "compose_report",
    "source_caption_text",
]

"""Track R unit R-4 — style-profile extractor + per-workspace store
(docs/product/report-engine-design-2026-08-24.md, layer 3 / R4.3).

WHAT THIS MODULE DOES, IN ONE SENTENCE
---------------------------------------
Turns an uploaded, styled DOCX into a JSON style catalogue this product can
later reapply (:func:`extract_style_profile`), and remembers it per workspace
(the store functions below), so a consultant uploads a past report ONCE and
every future report can match its look.

EXTRACTION IS A PROPOSAL, NOT A MEASUREMENT (C1-b grammar)
------------------------------------------------------------
Every profile carries ``provenance`` (source file, server-stamped
``extracted_at``, ``extractor_version``) and a first-class ``not_extracted``
list. Every catalogue element this extractor could not pin down is NAMED
there with a reason — never silently left ``None``, never guessed. The
invariant this module holds itself to, and that
``tests/test_report_style_profile.py`` checks mechanically: **every ``None``/
``False``-absent leaf in the profile has a matching ``not_extracted`` entry
naming its dotted path.** No field is quietly empty.

Two distinct kinds of "could not extract", both real and both named:

1. **Structural absence** — the document has no tables at all, so there is no
   table style to describe; no header content, so no logo/rule/text to read.
   ``table_style`` (etc.) is ``None`` and the reason says so.
2. **Present but unreadable/unspecified** — a style exists and is USED, but
   its color is not a literal RGB: either no ``<w:color>`` element at all
   (Word will render the theme's own default — see
   :func:`_style_color_hex`'s ``"unspecified"`` branch, whose reason is
   verbatim the design doc's own example: "본문 스타일에 색이 명시돼 있지
   않음 — 테마 기본값") or a theme-color reference with no literal ``w:val``
   fallback baked in (``"theme_only"``). Word's own default template
   actually bakes a literal ``w:val`` alongside most ``w:themeColor``
   references (verified against a bare ``docx.Document()``'s ``Title`` style:
   ``<w:color w:val="17365D" w:themeColor="text2" .../>``) — that IS a real,
   literal hex Word wrote into the file, so it is extracted, not guessed.
   Only a color with NO literal fallback at all is named "theme-only".

"ACTUALLY USED", NOT "LATENT IN THE STYLE CATALOGUE"
-------------------------------------------------------
The design doc's own words: "팔레트(제목/본문/강조색이 **실제로 사용된** 색)".
``python-docx`` ships built-in ``Title``/``Heading 1-3``/``Caption`` style
DEFINITIONS in every document regardless of whether the body ever applies
them, so reading the style catalogue blind would report colors and fonts
that render nowhere in this specific document. Every heading-family/caption
extraction here is therefore GATED on :func:`_style_is_used`: at least one
paragraph in the document body must carry that style, or the whole role is
named absent ("문서에 해당 스타일을 사용하는 문단이 없음") rather than
reporting the template's latent default.

REFUSALS BY NAME, NEVER A BARE EXCEPTION
-------------------------------------------
:class:`StyleProfileRefused` carries a stable ``code``, a ko/en/ja
``messages`` triple and an HTTP ``status`` — the exact shape
``services.calibration_attestation.StepAttestationRefused`` (#53) and
``services.demand_target_map``'s refusal already use in this codebase, so
``routers/report.py`` translates it into ``HTTPException(status, exc.to_dict())``
the same one-line way those routers already do. Four refusals, checked in
this order and for this reason:

1. ``style_profile_password_protected`` — checked FIRST, from the raw magic
   bytes, before any zip parsing is attempted. A real password-protected
   ``.docx`` ("Encrypt with Password" in Word) is not a zip at all — Word
   wraps the whole OOXML package inside an OLE2/CFBF compound-file container
   (MS-OFFCRYPTO), which starts with the fixed 8-byte CFBF signature
   ``D0 CF 11 E0 A1 B1 1A E1``. Catching this by its own signature gives a
   caller a specific, actionable message ("이 파일은 암호로 보호되어 있습니다")
   instead of a generic "not a zip" that would send them looking for file
   corruption instead of a password dialog.
2. ``style_profile_zip_bomb`` — the SECOND check, and the reason it runs
   before this module ever calls ``zipfile.ZipFile(...).namelist()`` on the
   untrusted bytes for real inspection: :func:`services.dataimport.xlsx_guard
   .guard_xlsx_bytes` is format-agnostic (it measures declared/compressed
   size and ratio per ZIP MEMBER via ``ZipInfo`` — it never looks at member
   NAMES), so it guards a DOCX exactly as soundly as it guards an XLSX. This
   is REUSE, not a mirror rewrite: a DOCX is a zip like any other, and this
   module deliberately does not duplicate the guard's two-layer
   header-precheck + streamed-re-measurement discipline (see that module's
   own docstring for the measured caps and the adversarial finding that
   produced them).
3. ``style_profile_not_a_docx`` — the zip parses and passed the bomb guard,
   but carries no ``word/document.xml``: a legitimate zip that simply is not
   an OOXML wordprocessing package (could be an .xlsx, a plain .zip, a
   renamed file of another kind entirely).
4. ``style_profile_unreadable`` — ``word/document.xml`` exists but
   ``python-docx.Document()`` still could not open the package (corrupt XML,
   a missing required part, or any other structural fault
   ``docx.opc.exceptions.PackageNotFoundError``/``KeyError``/an XML parse
   error surfaces). The catch-all, and deliberately the LAST one tried —
   everything more specific above it gets its own name first.

A fifth refusal, ``style_profile_no_sections``, lives a phase later — inside
:func:`extract_style_profile` itself, right after ``_open_docx_or_refuse``
hands back a document that DID open successfully but carries not one
``<w:sectPr>``. ``page``/``header``/``footer`` all key off ``sections[0]``,
so this is not one absent catalogue leaf (the "structural absence" doctrine
above) but the whole page/header/footer register having no ground truth —
refused by name rather than faked as three parallel all-None sub-trees.

Nothing here ever raises a bare exception across the module boundary:
:func:`extract_style_profile` raises only :class:`StyleProfileRefused`.

STORAGE — U6's PATTERN, THE SHAPE ``calibration_attestation.py`` (#53) AND
``demand_target_map.py`` (#54) ALREADY COPY
-----------------------------------------------------------------------------
``<workspace>/report/style-profiles/registry.json`` is the LIGHTWEIGHT INDEX
only (id, name, source file, source sha256, created_at, revision count, has
logo) — module ``RLock`` held across read-modify-write, same-directory
temp-file + ``fsync`` + ``os.replace``, and a registry that fails to parse is
QUARANTINED aside with its bytes intact rather than silently replaced
(:func:`_quarantine_and_restart`, byte-for-byte the same ceremony
``calibration_attestation._quarantine_and_restart`` runs).

The FULL record — the extracted profile, the correction history, the logo
descriptor — lives one directory per profile:
``<workspace>/report/style-profiles/<id>/profile.json`` (+ ``logo.<ext>``
alongside it when a header logo was found), per this unit's own build
instructions. Two files rather than one because a profile carries a BINARY
(the logo) that cannot live inside JSON, and because a corrupt single
profile must never be able to take the whole registry — and every OTHER
profile in the workspace — down with it the way one bad record inside a
single shared JSON blob could.

APPEND-ONLY CORRECTIONS: "틀리면 사람이 고친다"
--------------------------------------------------
:func:`apply_correction` NEVER rewrites ``original_profile`` — the bytes the
extractor actually produced stay on disk forever, exactly as
``calibration_attestation``'s attestations and ``demand_target_map``'s
mappings are never overwritten by a later event, only appended to. A
correction is stored as one more entry in ``corrections`` (partial fields,
deep-merged over the original in order), stamped with the SERVER clock and
the SIGNED-IN user — never fields on the request body
(``routers.report.StyleProfileCorrectionRequest`` sets
``model_config = ConfigDict(extra="forbid")`` for the identical reason
``routers.calibration.StepAttestationRequest`` does: a body carrying
``edited_by``/``edited_at`` is a 422 at the door, not a name somebody else's
session put on a correction). :func:`resolved_profile` is what a caller
reads to see "the profile as corrected today" — ``original_profile`` plus
every correction's fields applied in order, last write wins per leaf key.

Unlike an attestation, a style correction carries NO mandatory reason: an
attestation is an unverifiable claim about the physical world ("I did this
step in Vissim"), which is why #53 refuses a reason-less signature outright.
A style correction is the opposite — it is a claim a reviewer can verify by
simply re-opening the uploaded DOCX and looking, so requiring a
justification here would be ceremony without the evidentiary weight it has
for an attestation. ``reason`` is accepted and stored when given, never
required.

NO COM, no Vissim, no network call, no ``routers/`` import (this stays a
leaf ``services/`` module, like ``calibration_attestation.py``).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import struct
import threading
import time
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Mapping, TypeVar
from uuid import uuid4

import docx
from docx.enum.dml import MSO_COLOR_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn

from services import workspaces
from services.dataimport.xlsx_guard import XlsxBombError, guard_xlsx_bytes

logger = logging.getLogger(__name__)

STYLE_PROFILE_DOC_KIND = "prism.report_style_profile.v1"
EXTRACTOR_VERSION = "prism.report_style_profile_extractor.v1"

REPORT_DIRNAME = "report"
PROFILES_DIRNAME = "style-profiles"
REGISTRY_FILENAME = "registry.json"
PROFILE_FILENAME = "profile.json"

#: Same hex-only, length-capped id shape every client-supplied/server-minted
#: id in this repo uses (``calibration_attestation._ID_RE``).
_ID_RE = re.compile(r"^[0-9a-f]{8,32}$")

MAX_NAME_CHARS = 200
MAX_REASON_CHARS = 2000

_LOCK = threading.RLock()
_READ_RETRY_ATTEMPTS = 3
_READ_RETRY_DELAY_S = 0.02
_MAX_INCIDENTS = 20

_registry_health: dict[str, dict[str, Any]] = {}
_last_known_record_count: dict[str, int] = {}


# =============================================================================
# Refusals
# =============================================================================


class StyleProfileError(RuntimeError):
    """Base class for every failure this module raises."""


class StyleProfileRefused(StyleProfileError):
    """A DOCX (or a stored profile op) that must not proceed, named.

    Same envelope as ``services.calibration_attestation.StepAttestationRefused``:
    ``code`` is stable and machine-readable, ``messages`` is the ko/en/ja
    triple the dashboard renders, and the router turns it into a 4xx whose
    body IS :meth:`to_dict`.
    """

    def __init__(
        self,
        code: str,
        messages: Mapping[str, str],
        *,
        status: int = 422,
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {messages.get('en') or ''}")
        self.code = code
        self.messages = dict(messages)
        self.status = status
        self.detail = dict(detail or {})

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "reason_code": self.code,
            "message": self.messages,
            **self.detail,
        }


class RegistryUnavailableError(StyleProfileError):
    """The registry exists but its current contents could not be read."""


def _refuse(code: str, ko: str, en: str, ja: str, *, status: int = 422, **detail: Any) -> StyleProfileRefused:
    return StyleProfileRefused(code, {"ko": ko, "en": en, "ja": ja}, status=status, detail=detail)


# =============================================================================
# Extraction — pure: bytes in, a JSON-safe-ish profile dict out (the only
# non-JSON-safe leaf is header.logo["_bytes"], stripped by _public_profile
# before anything is persisted or returned over HTTP).
# =============================================================================

#: MS-OFFCRYPTO / OLE2 compound-file signature. A real Word "Encrypt with
#: Password" .docx is this container format, not a zip at all.
_CFBF_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SIGNATURE = b"\xff\xd8\xff"

_EMU_PER_MM = 36000.0

#: ``표 3-1. ...`` -> leading run of non-digit characters before the number.
_CAPTION_PREFIX_RE = re.compile(r"^(\D+?)\d")

#: ``1.`` / ``1.1`` / ``2.1.1`` (with or without a trailing dot) at the start
#: of a heading paragraph's text, followed by whitespace.
_LEADING_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)*\.?)\s")

_A4_WIDTH_MM = 210.0
_A4_HEIGHT_MM = 297.0
_PAGE_SIZE_TOLERANCE_MM = 2.0

_ALIGNMENT_NAMES: dict[Any, str] = {
    None: "left",
    WD_ALIGN_PARAGRAPH.LEFT: "left",
    WD_ALIGN_PARAGRAPH.CENTER: "center",
    WD_ALIGN_PARAGRAPH.RIGHT: "right",
    WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mm(length: Any) -> float | None:
    if length is None:
        return None
    return round(length.mm, 2)


def _pt(length: Any) -> float | None:
    if length is None:
        return None
    return round(length.pt, 2)


def _mark_missing(not_extracted: list[dict[str, Any]], field: str, ko: str, en: str, ja: str) -> None:
    not_extracted.append({"field": field, "reason": {"ko": ko, "en": en, "ja": ja}})


def _style_is_used(document: "docx.document.Document", style_name: str) -> bool:
    return any((p.style.name if p.style else None) == style_name for p in document.paragraphs)


def _get_style(document: "docx.document.Document", style_name: str):
    try:
        return document.styles[style_name]
    except KeyError:
        return None


def _style_rfonts(style: Any) -> dict[str, str | None]:
    rpr = style.element.find(qn("w:rPr")) if style is not None else None
    rfonts = rpr.find(qn("w:rFonts")) if rpr is not None else None
    if rfonts is None:
        return {"ascii": None, "eastAsia": None}
    return {"ascii": rfonts.get(qn("w:ascii")), "eastAsia": rfonts.get(qn("w:eastAsia"))}


def _style_color_hex(style: Any) -> tuple[str | None, str | None]:
    """``(hex, problem)`` — ``problem`` is ``None`` on success, else
    ``"unspecified"`` (no ``<w:color>`` element at all) or ``"theme_only"``
    (a theme-color reference with no literal RGB fallback). See the module
    docstring's "Present but unreadable/unspecified" section.
    """
    try:
        color = style.font.color
    except Exception:  # noqa: BLE001 — a style with no readable font block
        return None, "unspecified"
    if color is None or color.type is None:
        return None, "unspecified"
    try:
        rgb = color.rgb
    except Exception:  # noqa: BLE001 — theme ref python-docx cannot resolve
        rgb = None
    if rgb is None:
        return None, "theme_only"
    return str(rgb), None


def _color_reason(role_ko: str, role_en: str, role_ja: str, problem: str | None) -> tuple[str, str, str]:
    if problem == "theme_only":
        return (
            f"{role_ko} 색이 테마 색상 참조뿐이고 고정 RGB 값이 없어 추출할 수 없습니다.",
            f"{role_en} color is a theme-color reference with no literal RGB fallback, so it "
            "cannot be extracted as a fixed hex value.",
            f"{role_ja}の色はテーマ色参照のみで固定RGB値がないため抽出できません。",
        )
    # "unspecified", or any other unreadable state defaults to this — the
    # exact reason wording the design doc gives as its own example.
    return (
        f"{role_ko} 스타일에 색이 명시돼 있지 않음 — 테마 기본값.",
        f"No color is set on the {role_en} style — falls back to the Word theme default.",
        f"{role_ja}スタイルに色が指定されていません — テーマの既定値。",
    )


def _style_not_used_reason(role_ko: str, role_en: str, role_ja: str) -> tuple[str, str, str]:
    return (
        f"문서에 {role_ko} 스타일을 사용하는 문단이 없습니다 — 실제로 쓰이지 않은 스타일은 "
        "팔레트에 포함하지 않습니다.",
        f"No paragraph in the document uses the {role_en} style — a style that is never "
        "actually applied is not reported as part of the palette.",
        f"文書に{role_ja}スタイルを使う段落がありません — 実際に使われていないスタイルは"
        "パレットに含めません。",
    )


_HEADING_ROLES: tuple[tuple[str, str, str, str, str], ...] = (
    # (profile key, docx style name, ko role, en role, ja role)
    ("title", "Title", "제목(Title)", "Title", "タイトル"),
    ("heading1", "Heading 1", "제목1(Heading 1)", "Heading 1", "見出し1"),
    ("heading2", "Heading 2", "제목2(Heading 2)", "Heading 2", "見出し2"),
    ("heading3", "Heading 3", "제목3(Heading 3)", "Heading 3", "見出し3"),
)


def _extract_palette_and_fonts(
    document: "docx.document.Document", not_extracted: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    palette: dict[str, Any] = {}
    ladder_pt: dict[str, Any] = {}
    heading_family: dict[str, Any] | None = None
    heading_family_role: str | None = None

    for key, style_name, ko, en, ja in _HEADING_ROLES:
        style = _get_style(document, style_name)
        used = style is not None and _style_is_used(document, style_name)
        if not used:
            palette[key] = None
            ladder_pt[key] = None
            r_ko, r_en, r_ja = _style_not_used_reason(ko, en, ja)
            _mark_missing(not_extracted, f"palette.{key}", r_ko, r_en, r_ja)
            _mark_missing(not_extracted, f"fonts.ladder_pt.{key}", r_ko, r_en, r_ja)
            continue

        hexval, problem = _style_color_hex(style)
        palette[key] = hexval
        if hexval is None:
            r_ko, r_en, r_ja = _color_reason(ko, en, ja, problem)
            _mark_missing(not_extracted, f"palette.{key}", r_ko, r_en, r_ja)

        ladder_pt[key] = _style_size_pt(style)
        if ladder_pt[key] is None:
            _mark_missing(
                not_extracted,
                f"fonts.ladder_pt.{key}",
                f"{ko} 스타일에 글자 크기가 명시돼 있지 않습니다.",
                f"No font size is set on the {en} style.",
                f"{ja}スタイルにフォントサイズが指定されていません。",
            )

        # heading_family is read off the FIRST used heading-ish role that
        # carries a legible rFonts block (title, then heading1, ...) — one
        # family for the whole heading register, matching the design doc's
        # "제목" (singular) rather than a per-level family.
        if heading_family is None:
            rfonts = _style_rfonts(style)
            if rfonts.get("eastAsia") or rfonts.get("ascii"):
                heading_family = rfonts
                heading_family_role = ko
                # rFonts is commonly ASYMMETRIC (only ``w:ascii`` set, e.g. a
                # Latin-only style) -- each missing slot is named on its OWN
                # leaf, never left silently None and never backfilled from
                # the slot that IS present (that would be fabrication: ascii
                # and eastAsia are different fonts, see the module docstring).
                if not rfonts.get("eastAsia"):
                    _mark_missing(
                        not_extracted,
                        "fonts.heading_family.eastAsia",
                        f"{ko} 스타일에 한중일(eastAsia) 글꼴이 지정돼 있지 않습니다 (ascii "
                        "글꼴로 대신 채우지 않습니다).",
                        f"No eastAsia font is set on the {en} style (never backfilled from "
                        "the ascii font).",
                        f"{ja}スタイルに東アジア(eastAsia)フォントが指定されていません"
                        "（asciiフォントで代用しません）。",
                    )
                if not rfonts.get("ascii"):
                    _mark_missing(
                        not_extracted,
                        "fonts.heading_family.ascii",
                        f"{ko} 스타일에 라틴(ascii) 글꼴이 지정돼 있지 않습니다.",
                        f"No ascii font is set on the {en} style.",
                        f"{ja}スタイルにラテン(ascii)フォントが指定されていません。",
                    )

    if heading_family is None:
        heading_family = {"ascii": None, "eastAsia": None}
        _mark_missing(
            not_extracted,
            "fonts.heading_family",
            "제목 계열 스타일 중 실제로 사용된 것이 없거나, 사용된 스타일에 글꼴이 "
            "명시돼 있지 않습니다.",
            "No used heading-register style carries an explicit font.",
            "実際に使われている見出し系スタイルがないか、使われているスタイルに"
            "フォントが指定されていません。",
        )

    # --- body (Normal) ------------------------------------------------
    normal = _get_style(document, "Normal")
    normal_used = normal is not None and _style_is_used(document, "Normal")
    if not normal_used:
        palette["body"] = None
        ladder_pt["body"] = None
        body_family = {"ascii": None, "eastAsia": None}
        r_ko, r_en, r_ja = _style_not_used_reason("본문(Normal)", "Normal (body)", "本文(Normal)")
        _mark_missing(not_extracted, "palette.body", r_ko, r_en, r_ja)
        _mark_missing(not_extracted, "fonts.ladder_pt.body", r_ko, r_en, r_ja)
        _mark_missing(not_extracted, "fonts.body_family", r_ko, r_en, r_ja)
    else:
        hexval, problem = _style_color_hex(normal)
        palette["body"] = hexval
        if hexval is None:
            r_ko, r_en, r_ja = _color_reason("본문(Normal)", "Normal (body)", "本文(Normal)", problem)
            _mark_missing(not_extracted, "palette.body", r_ko, r_en, r_ja)
        ladder_pt["body"] = _style_size_pt(normal)
        if ladder_pt["body"] is None:
            _mark_missing(
                not_extracted,
                "fonts.ladder_pt.body",
                "본문(Normal) 스타일에 글자 크기가 명시돼 있지 않습니다.",
                "No font size is set on the Normal (body) style.",
                "本文(Normal)スタイルにフォントサイズが指定されていません。",
            )
        body_family = _style_rfonts(normal)
        if not body_family.get("eastAsia") and not body_family.get("ascii"):
            _mark_missing(
                not_extracted,
                "fonts.body_family",
                "본문(Normal) 스타일에 글꼴이 명시돼 있지 않습니다.",
                "No font is set on the Normal (body) style.",
                "本文(Normal)スタイルにフォントが指定されていません。",
            )
        else:
            # Asymmetric rFonts (only ONE of ascii/eastAsia set) is common
            # and real -- the missing slot is named on its OWN leaf, never
            # backfilled from the slot that IS present. See the identical
            # doctrine applied to fonts.heading_family.* above.
            if not body_family.get("eastAsia"):
                _mark_missing(
                    not_extracted,
                    "fonts.body_family.eastAsia",
                    "본문(Normal) 스타일에 한중일(eastAsia) 글꼴이 지정돼 있지 않습니다 "
                    "(ascii 글꼴로 대신 채우지 않습니다).",
                    "No eastAsia font is set on the Normal (body) style (never backfilled "
                    "from the ascii font).",
                    "本文(Normal)スタイルに東アジア(eastAsia)フォントが指定されていません"
                    "（asciiフォントで代用しません）。",
                )
            if not body_family.get("ascii"):
                _mark_missing(
                    not_extracted,
                    "fonts.body_family.ascii",
                    "본문(Normal) 스타일에 라틴(ascii) 글꼴이 지정돼 있지 않습니다.",
                    "No ascii font is set on the Normal (body) style.",
                    "本文(Normal)スタイルにラテン(ascii)フォントが指定されていません。",
                )

    # --- caption --------------------------------------------------------
    caption = _get_style(document, "Caption")
    caption_used = caption is not None and _style_is_used(document, "Caption")
    if not caption_used:
        palette["header_footer_text"] = None  # provisional; header/footer may still fill it
        ladder_pt["caption"] = None
        r_ko, r_en, r_ja = _style_not_used_reason("캡션(Caption)", "Caption", "キャプション(Caption)")
        _mark_missing(not_extracted, "fonts.ladder_pt.caption", r_ko, r_en, r_ja)
        caption_color_hex, caption_color_problem = None, "not_used"
    else:
        ladder_pt["caption"] = _style_size_pt(caption)
        if ladder_pt["caption"] is None:
            _mark_missing(
                not_extracted,
                "fonts.ladder_pt.caption",
                "캡션(Caption) 스타일에 글자 크기가 명시돼 있지 않습니다.",
                "No font size is set on the Caption style.",
                "キャプション(Caption)スタイルにフォントサイズが指定されていません。",
            )
        caption_color_hex, caption_color_problem = _style_color_hex(caption)

    palette["emphasis"] = None  # filled by _extract_emphasis
    palette["table_header_fill"] = None  # filled by _extract_table_style
    palette["table_border"] = None  # filled by _extract_table_style
    palette["header_footer_text"] = None  # filled below / by header+footer

    fonts = {
        "heading_family": heading_family,
        "body_family": body_family,
        "ladder_pt": ladder_pt,
    }
    return palette, fonts, {
        "caption_color_hex": caption_color_hex,
        "caption_color_problem": caption_color_problem,
        "heading_family_role": heading_family_role,
    }


def _style_size_pt(style: Any) -> float | None:
    try:
        size = style.font.size
    except Exception:  # noqa: BLE001
        return None
    return _pt(size)


def _extract_emphasis(document: "docx.document.Document", not_extracted: list[dict[str, Any]]) -> str | None:
    """A bold run whose color differs from the surrounding body — the one
    per-run override this profile catalogues (everything else lives on
    styles). If more than one distinct such color is found, the FIRST is
    reported and the ambiguity is named — never averaged, never guessed.
    """
    found: list[str] = []
    for paragraph in document.paragraphs:
        for run in paragraph.runs:
            if not run.bold:
                continue
            try:
                color = run.font.color
            except Exception:  # noqa: BLE001
                continue
            if color is None or color.type != MSO_COLOR_TYPE.RGB:
                continue
            try:
                hexval = str(color.rgb) if color.rgb is not None else None
            except Exception:  # noqa: BLE001
                hexval = None
            if hexval:
                found.append(hexval)
    if not found:
        _mark_missing(
            not_extracted,
            "palette.emphasis",
            "굵게 표시되면서 고정 색이 지정된 텍스트가 문서에 없어 강조색을 추출하지 "
            "못했습니다.",
            "No bold run with an explicit RGB color was found, so an emphasis color could "
            "not be extracted.",
            "太字かつ固定色が指定されたテキストが文書になく、強調色を抽出できませんでした。",
        )
        return None
    distinct = sorted(set(found))
    if len(distinct) > 1:
        _mark_missing(
            not_extracted,
            "palette.emphasis",
            f"강조색으로 보이는 색이 여러 개({', '.join(distinct)}) 발견되어 하나로 "
            f"단정하지 않았습니다. 첫 번째로 발견된 {found[0]}을(를) 잠정값으로 둡니다.",
            f"Multiple candidate emphasis colors were found ({', '.join(distinct)}); the "
            f"first one seen ({found[0]}) is reported provisionally rather than guessed.",
            f"強調色らしき色が複数（{', '.join(distinct)}）見つかったため一つに断定していません。"
            f"最初に見つかった{found[0]}を暫定値とします。",
        )
    return found[0]


def _extract_page(section: Any, not_extracted: list[dict[str, Any]]) -> dict[str, Any]:
    width_mm = _mm(section.page_width)
    height_mm = _mm(section.page_height)
    size = "Custom"
    if (
        width_mm is not None
        and height_mm is not None
        and abs(width_mm - _A4_WIDTH_MM) <= _PAGE_SIZE_TOLERANCE_MM
        and abs(height_mm - _A4_HEIGHT_MM) <= _PAGE_SIZE_TOLERANCE_MM
    ):
        size = "A4"

    # A <w:sectPr> can EXIST (so extract_style_profile's zero-sections
    # refusal correctly stays silent) yet carry no <w:pgSz>/<w:pgMar>
    # children at all -- each leaf is checked and named on its OWN dotted
    # path, independently, so a section with pgSz but no pgMar (or vice
    # versa) only marks the leaves it actually lacks.
    if width_mm is None:
        _mark_missing(
            not_extracted,
            "page.width_mm",
            "이 섹션에 페이지 크기(w:pgSz)가 지정돼 있지 않아 페이지 너비를 추출하지 "
            "못했습니다.",
            "This section declares no page size (w:pgSz), so the page width could not be "
            "extracted.",
            "このセクションにはページサイズ(w:pgSz)が指定されておらず、ページ幅を"
            "抽出できませんでした。",
        )
    if height_mm is None:
        _mark_missing(
            not_extracted,
            "page.height_mm",
            "이 섹션에 페이지 크기(w:pgSz)가 지정돼 있지 않아 페이지 높이를 추출하지 "
            "못했습니다.",
            "This section declares no page size (w:pgSz), so the page height could not be "
            "extracted.",
            "このセクションにはページサイズ(w:pgSz)が指定されておらず、ページの高さを"
            "抽出できませんでした。",
        )

    margins_mm = {
        "top": _mm(section.top_margin),
        "bottom": _mm(section.bottom_margin),
        "left": _mm(section.left_margin),
        "right": _mm(section.right_margin),
    }
    _MARGIN_LABELS = {
        "top": ("위쪽", "top", "上"),
        "bottom": ("아래쪽", "bottom", "下"),
        "left": ("왼쪽", "left", "左"),
        "right": ("오른쪽", "right", "右"),
    }
    for side, value in margins_mm.items():
        if value is None:
            ko_side, en_side, ja_side = _MARGIN_LABELS[side]
            _mark_missing(
                not_extracted,
                f"page.margins_mm.{side}",
                f"이 섹션에 {ko_side} 여백(w:pgMar)이 지정돼 있지 않습니다.",
                f"No {en_side} margin (w:pgMar) is set on this section.",
                f"このセクションに{ja_side}余白(w:pgMar)が指定されていません。",
            )

    return {
        "size": size,
        "width_mm": width_mm,
        "height_mm": height_mm,
        "margins_mm": margins_mm,
    }


def _extract_heading_numbering(
    document: "docx.document.Document", not_extracted: list[dict[str, Any]]
) -> dict[str, Any]:
    style_to_level = {"Heading 1": 1, "Heading 2": 2, "Heading 3": 3}
    numpr_seen = False
    samples: dict[int, str] = {}
    for paragraph in document.paragraphs:
        style_name = paragraph.style.name if paragraph.style else None
        level = style_to_level.get(style_name or "")
        if level is None:
            continue
        p_element = paragraph._p
        p_pr = p_element.find(qn("w:pPr"))
        if p_pr is not None and p_pr.find(qn("w:numPr")) is not None:
            numpr_seen = True
        if level not in samples:
            match = _LEADING_NUMBER_RE.match(paragraph.text.strip())
            if match:
                samples[level] = match.group(1)

    if numpr_seen:
        return {"scheme": "numbering_xml", "detected": {}}

    if samples:
        detected = {f"h{level}": token for level, token in sorted(samples.items())}
        return {"scheme": "typed_decimal", "detected": detected}

    _mark_missing(
        not_extracted,
        "heading_numbering",
        "제목 스타일(Heading 1-3)을 쓰는 문단이 없거나, 있어도 텍스트 앞에 번호 "
        "패턴이 없어 번호 체계를 판별하지 못했습니다.",
        "No paragraph uses a heading style, or none carries a leading number pattern, so "
        "the numbering scheme could not be determined.",
        "見出しスタイル(Heading 1-3)を使う段落がないか、あっても先頭に番号パターンが"
        "なく、番号体系を判定できませんでした。",
    )
    return {"scheme": "none", "detected": {}}


def _cell_shd_fill(cell: Any) -> str | None:
    tc_pr = cell._tc.find(qn("w:tcPr"))
    shd = tc_pr.find(qn("w:shd")) if tc_pr is not None else None
    if shd is None:
        return None
    fill = shd.get(qn("w:fill"))
    if not fill or fill.upper() in ("AUTO", "NONE"):
        return None
    return fill.upper()


def _first_run(cell: Any):
    for paragraph in cell.paragraphs:
        for run in paragraph.runs:
            if run.text.strip():
                return run
    return None


def _run_color_hex(run: Any) -> str | None:
    try:
        color = run.font.color
    except Exception:  # noqa: BLE001
        return None
    if color is None or color.type != MSO_COLOR_TYPE.RGB:
        return None
    try:
        return str(color.rgb) if color.rgb is not None else None
    except Exception:  # noqa: BLE001
        return None


def _table_borders(table: Any) -> tuple[str | None, float | None]:
    tbl_pr = table._tbl.find(qn("w:tblPr"))
    borders = tbl_pr.find(qn("w:tblBorders")) if tbl_pr is not None else None
    if borders is None and table.rows:
        # Fallback: per-cell borders on the header row's first cell.
        tc_pr = table.rows[0].cells[0]._tc.find(qn("w:tcPr"))
        borders = tc_pr.find(qn("w:tcBorders")) if tc_pr is not None else None
    if borders is None:
        return None, None
    # NOTE: lxml elements with no children are falsy (a real gotcha — `w:top`
    # etc. are leaf elements carrying only attributes), so this MUST be
    # `is None` chaining rather than `or` chaining, or a real border element
    # would be silently skipped in favour of the next candidate.
    edge = borders.find(qn("w:top"))
    if edge is None:
        edge = borders.find(qn("w:insideH"))
    if edge is None:
        edge = borders.find(qn("w:bottom"))
    if edge is None:
        return None, None
    color = edge.get(qn("w:color"))
    sz = edge.get(qn("w:sz"))
    width_pt = None
    if sz is not None:
        try:
            width_pt = round(int(sz) / 8.0, 2)
        except ValueError:
            width_pt = None
    if color and color.upper() not in ("AUTO", "NONE"):
        color = color.upper()
    else:
        color = None
    return color, width_pt


def _extract_table_style(
    document: "docx.document.Document", not_extracted: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, float | None]:
    tables = document.tables
    if not tables:
        _mark_missing(
            not_extracted,
            "table_style",
            "문서에 표가 없어 표 스타일을 추출할 수 없습니다.",
            "The document has no tables, so a table style could not be extracted.",
            "文書に表がないため表スタイルを抽出できませんでした。",
        )
        _mark_missing(
            not_extracted,
            "fonts.ladder_pt.table",
            "문서에 표가 없어 표 본문 글자 크기를 추출할 수 없습니다.",
            "The document has no tables, so the table body font size could not be extracted.",
            "文書に表がないため表本文のフォントサイズを抽出できませんでした。",
        )
        return None, None

    table = tables[0]
    header_row = table.rows[0]
    fills = {_cell_shd_fill(cell) for cell in header_row.cells}
    fills.discard(None)
    header_fill = sorted(fills)[0] if len(fills) == 1 else None
    if header_fill is None:
        _mark_missing(
            not_extracted,
            "table_style.header_row.fill",
            "표 머리글 행의 배경색이 셀마다 다르거나 지정돼 있지 않습니다.",
            "The table header row's background color is inconsistent across cells, or not set.",
            "表のヘッダー行の背景色がセルごとに異なるか、指定されていません。",
        )

    header_run = _first_run(header_row.cells[0])
    header_text_color = _run_color_hex(header_run) if header_run is not None else None
    header_bold = bool(header_run.bold) if header_run is not None and header_run.bold is not None else None
    header_size_pt = _pt(header_run.font.size) if header_run is not None else None

    # An ordinary table formatted via a TABLE STYLE (the common case -- no
    # per-run/per-cell direct override) leaves all three of these None: the
    # value lives in the table style's own definition, which this extractor
    # deliberately does not read (see _extract_table_style's own header_fill
    # branch above for the same "direct formatting only" doctrine). Named
    # absence, not scope expansion into the style catalogue.
    if header_text_color is None:
        _mark_missing(
            not_extracted,
            "table_style.header_row.text_color",
            "표 머리글 셀에 직접 지정된 글자 색이 없습니다 (표 스타일에서 오는 값일 수 "
            "있어 이 추출기는 표 스타일 정의까지는 읽지 않습니다).",
            "No explicit text color is set on the table header cell (the value may come "
            "from the table style, which this extractor does not read).",
            "表のヘッダーセルに直接指定された文字色がありません（表スタイルに由来する"
            "値の可能性があり、この抽出機能では表スタイルの定義までは読み取りません）。",
        )
    if header_bold is None:
        _mark_missing(
            not_extracted,
            "table_style.header_row.bold",
            "표 머리글 셀에 직접 지정된 굵기(bold)가 없습니다 (표 스타일에서 오는 값일 "
            "수 있습니다).",
            "No explicit bold is set on the table header cell (the value may come from the "
            "table style).",
            "表のヘッダーセルに直接指定された太字設定がありません（表スタイルに由来する"
            "値の可能性があります）。",
        )
    if header_size_pt is None:
        _mark_missing(
            not_extracted,
            "table_style.header_row.size_pt",
            "표 머리글 셀에 직접 지정된 글자 크기가 없습니다 (표 스타일에서 오는 값일 "
            "수 있습니다).",
            "No explicit font size is set on the table header cell (the value may come from "
            "the table style).",
            "表のヘッダーセルに直接指定されたフォントサイズがありません（表スタイルに"
            "由来する値の可能性があります）。",
        )

    table_text_size_pt = header_size_pt
    if len(table.rows) > 1:
        body_run = _first_run(table.rows[1].cells[0])
        if body_run is not None:
            table_text_size_pt = _pt(body_run.font.size) or table_text_size_pt

    border_color, border_width_pt = _table_borders(table)
    if border_color is None:
        _mark_missing(
            not_extracted,
            "table_style.borders.color",
            "표 테두리 색을 표 또는 셀 서식에서 찾을 수 없습니다.",
            "No table or cell border color could be found.",
            "表または セル書式に表の罫線色が見つかりません。",
        )
    if border_width_pt is None:
        _mark_missing(
            not_extracted,
            "table_style.borders.width_pt",
            "표 테두리 두께를 표 또는 셀 서식에서 찾을 수 없습니다 (표 스타일에서 오는 "
            "값일 수 있습니다).",
            "No table or cell border width could be found (the value may come from the "
            "table style).",
            "表またはセル書式に罫線の太さが見つかりません（表スタイルに由来する値の"
            "可能性があります）。",
        )

    table_style = {
        "header_row": {
            "fill": header_fill,
            "text_color": header_text_color,
            "bold": header_bold,
            "size_pt": header_size_pt,
        },
        "borders": {"color": border_color, "width_pt": border_width_pt},
    }
    return table_style, table_text_size_pt


def _extract_caption(
    document: "docx.document.Document", not_extracted: list[dict[str, Any]]
) -> str | None:
    prefixes: list[str] = []
    for paragraph in document.paragraphs:
        if not paragraph.style or paragraph.style.name != "Caption":
            continue
        match = _CAPTION_PREFIX_RE.match(paragraph.text.strip())
        if match:
            prefixes.append(match.group(1))
    if not prefixes:
        _mark_missing(
            not_extracted,
            "caption.prefix",
            "캡션 문단이 없거나, 있어도 '표 3-1.'과 같은 番호 앞에 붙는 접두어 패턴을 "
            "찾지 못했습니다.",
            "No caption paragraph exists, or none carries a leading-prefix-before-number "
            "pattern (e.g. 'Table 3-1.').",
            "キャプション段落がないか、あっても番号の前に付く接頭辞パターン"
            "（例: '表3-1.'）が見つかりませんでした。",
        )
        return None
    distinct = sorted(set(prefixes))
    if len(distinct) > 1:
        _mark_missing(
            not_extracted,
            "caption.prefix",
            f"캡션 접두어가 문서 안에서 여러 형태({', '.join(distinct)!r})로 발견되어 "
            "하나로 단정하지 않았습니다.",
            f"Multiple caption prefix forms were found in the document ({distinct!r}); not "
            "reported as one value.",
            f"キャプションの接頭辞が文書内で複数の形（{distinct!r}）で見つかったため、"
            "一つに断定していません。",
        )
    return prefixes[0]


def _image_content_type(data: bytes) -> str:
    if data.startswith(_PNG_SIGNATURE):
        return "image/png"
    if data.startswith(_JPEG_SIGNATURE):
        return "image/jpeg"
    return "application/octet-stream"


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    """PNG pixel dimensions from the IHDR chunk, no imaging library needed.

    Only PNG is decoded (the sample style asset and the overwhelming majority
    of report-header logos in practice); other formats report ``None``
    dimensions and are named in ``not_extracted`` rather than mis-measured.
    """
    if not data.startswith(_PNG_SIGNATURE) or len(data) < 24:
        return None
    width, height = struct.unpack(">II", data[16:24])
    return int(width), int(height)


def _extract_header(
    document: "docx.document.Document", not_extracted: list[dict[str, Any]]
) -> tuple[dict[str, Any], str | None]:
    """Returns ``(header_dict, caption_color_hex_carrying_no_reason)``.

    The second element only matters for callers wiring
    ``palette.header_footer_text`` — see :func:`extract_style_profile`.
    """
    section = document.sections[0]
    header = section.header
    if header.is_linked_to_previous:
        _mark_missing(
            not_extracted,
            "header",
            "이 문서에는 별도로 정의된 머리글이 없습니다 (이전 섹션과 연결됨/기본값).",
            "This document defines no header of its own (linked to the previous section, "
            "i.e. the default).",
            "この文書には独自に定義されたヘッダーがありません（前のセクションと連結/既定値）。",
        )
        return {
            "logo": {"present": False},
            "text": None,
            "text_size_pt": None,
            "text_color": None,
            "rule_below": False,
        }, None

    paragraph = header.paragraphs[0] if header.paragraphs else None

    # --- logo -----------------------------------------------------------
    logo: dict[str, Any] = {"present": False}
    if paragraph is not None:
        p_element = paragraph._p
        blip = None
        extent = None
        for element in p_element.iter():
            local = element.tag.split("}")[-1]
            if local == "blip" and blip is None:
                blip = element
            elif local == "extent" and extent is None:
                extent = element
        if blip is not None:
            embed_rid = blip.get(qn("r:embed"))
            image_part = header.part.related_parts.get(embed_rid) if embed_rid else None
            if image_part is not None:
                image_bytes = image_part.blob
                content_type = _image_content_type(image_bytes)
                pixel_dims = _png_dimensions(image_bytes)
                display_width_mm = None
                display_height_mm = None
                if extent is not None:
                    cx = extent.get("cx")
                    cy = extent.get("cy")
                    if cx is not None:
                        display_width_mm = round(int(cx) / _EMU_PER_MM, 2)
                    if cy is not None:
                        display_height_mm = round(int(cy) / _EMU_PER_MM, 2)
                logo = {
                    "present": True,
                    "sha256": hashlib.sha256(image_bytes).hexdigest(),
                    "content_type": content_type,
                    "width_px": pixel_dims[0] if pixel_dims else None,
                    "height_px": pixel_dims[1] if pixel_dims else None,
                    "display_width_mm": display_width_mm,
                    "display_height_mm": display_height_mm,
                    "_bytes": image_bytes,
                }
                if pixel_dims is None:
                    _mark_missing(
                        not_extracted,
                        "header.logo.width_px",
                        "로고 이미지가 PNG가 아니어서(또는 헤더를 해석할 수 없어) 픽셀 "
                        "크기를 읽지 못했습니다.",
                        "The logo image is not PNG (or could not be parsed), so pixel "
                        "dimensions could not be read.",
                        "ロゴ画像がPNGでない（またはヘッダーを解析できない）ため、"
                        "ピクセルサイズを読み取れませんでした。",
                    )
            else:
                _mark_missing(
                    not_extracted,
                    "header.logo",
                    "머리글에 그림이 있지만 이미지 데이터를 관계(rels)에서 찾지 못했습니다.",
                    "The header contains a drawing, but the image data could not be found "
                    "via its relationship.",
                    "ヘッダーに図はありますが、関係(rels)から画像データを見つけられません"
                    "でした。",
                )
        else:
            _mark_missing(
                not_extracted,
                "header.logo",
                "머리글에 이미지가 없습니다.",
                "The header carries no image.",
                "ヘッダーに画像がありません。",
            )

    # --- text -------------------------------------------------------------
    text_value = None
    text_size_pt = None
    text_color = None
    if paragraph is not None:
        text_run = next((r for r in paragraph.runs if r.text.strip()), None)
        if text_run is not None:
            text_value = text_run.text.strip()
            text_size_pt = _pt(text_run.font.size)
            text_color = _run_color_hex(text_run)
            if text_color is None:
                _mark_missing(
                    not_extracted,
                    "header.text_color",
                    "머리글 텍스트에 고정 색이 지정돼 있지 않습니다.",
                    "No explicit color is set on the header text.",
                    "ヘッダーテキストに固定色が指定されていません。",
                )
        else:
            _mark_missing(
                not_extracted,
                "header.text",
                "머리글에 텍스트가 없습니다.",
                "The header carries no text.",
                "ヘッダーにテキストがありません。",
            )
            # header.text_size_pt / header.text_color stay None right along
            # with header.text (there is no run to measure a size or color
            # FROM) -- "header.text" alone does not cover these two sibling
            # leaves under _uncovered_absent_leaves's dotted-prefix rule, so
            # each is named on its own path too.
            _mark_missing(
                not_extracted,
                "header.text_size_pt",
                "머리글에 텍스트가 없어 글자 크기를 추출할 수 없습니다.",
                "The header carries no text, so a font size could not be extracted.",
                "ヘッダーにテキストがないため、フォントサイズを抽出できませんでした。",
            )
            _mark_missing(
                not_extracted,
                "header.text_color",
                "머리글에 텍스트가 없어 글자 색을 추출할 수 없습니다.",
                "The header carries no text, so a text color could not be extracted.",
                "ヘッダーにテキストがないため、文字色を抽出できませんでした。",
            )

    # --- rule ---------------------------------------------------------
    rule_below = False
    if paragraph is not None:
        p_pr = paragraph._p.find(qn("w:pPr"))
        p_bdr = p_pr.find(qn("w:pBdr")) if p_pr is not None else None
        rule_below = p_bdr is not None and p_bdr.find(qn("w:bottom")) is not None

    return {
        "logo": logo,
        "text": text_value,
        "text_size_pt": text_size_pt,
        "text_color": text_color,
        "rule_below": rule_below,
    }, None


def _extract_footer(
    document: "docx.document.Document", not_extracted: list[dict[str, Any]]
) -> dict[str, Any]:
    section = document.sections[0]
    footer = section.footer
    if footer.is_linked_to_previous:
        _mark_missing(
            not_extracted,
            "footer.page_number",
            "이 문서에는 별도로 정의된 바닥글이 없습니다 (이전 섹션과 연결됨/기본값).",
            "This document defines no footer of its own (linked to the previous section, "
            "i.e. the default).",
            "この文書には独自に定義されたフッターがありません（前のセクションと連結/既定値）。",
        )
        return {"page_number": {"present": False}}

    paragraph = footer.paragraphs[0] if footer.paragraphs else None
    if paragraph is None:
        _mark_missing(
            not_extracted,
            "footer.page_number",
            "바닥글에 문단이 없습니다.",
            "The footer carries no paragraph.",
            "フッターに段落がありません。",
        )
        return {"page_number": {"present": False}}

    p_element = paragraph._p
    parts: list[str] = []
    found_page_field = False
    size_pt = None
    color = None
    # Deliberately starts from each RUN (``w:r``) rather than a blanket
    # ``p_element.iter()``: a paragraph-mark's own ``w:pPr/w:rPr`` would
    # otherwise be visited first (it precedes every run in document order)
    # and could be mistaken for a run's formatting.
    for run_element in p_element.findall(qn("w:r")):
        rpr = run_element.find(qn("w:rPr"))
        if rpr is not None and size_pt is None:
            sz = rpr.find(qn("w:sz"))
            if sz is not None and sz.get(qn("w:val")):
                try:
                    size_pt = round(int(sz.get(qn("w:val"))) / 2.0, 2)
                except ValueError:
                    size_pt = None
            color_el = rpr.find(qn("w:color"))
            if color_el is not None:
                val = color_el.get(qn("w:val"))
                if val and val.upper() not in ("AUTO", "NONE"):
                    color = val.upper()
        for element in run_element.iter():
            local = element.tag.split("}")[-1]
            if local == "instrText":
                if element.text and "PAGE" in element.text:
                    found_page_field = True
                    parts.append("N")
            elif local == "t":
                parts.append(element.text or "")

    if not found_page_field:
        _mark_missing(
            not_extracted,
            "footer.page_number",
            "바닥글에 페이지 번호 필드(PAGE)가 없습니다.",
            "The footer carries no PAGE number field.",
            "フッターにページ番号フィールド(PAGE)がありません。",
        )
        return {"page_number": {"present": False}}

    position = _ALIGNMENT_NAMES.get(paragraph.alignment, "left")
    if color is None:
        _mark_missing(
            not_extracted,
            "footer.page_number.color",
            "바닥글 페이지 번호에 고정 색이 지정돼 있지 않습니다.",
            "No explicit color is set on the footer page number.",
            "フッターのページ番号に固定色が指定されていません。",
        )

    return {
        "page_number": {
            "present": True,
            "position": position,
            "format": "".join(parts),
            "size_pt": size_pt,
            "color": color,
        }
    }


def _open_docx_or_refuse(content: bytes, *, source_file: str) -> "docx.document.Document":
    if content[:8] == _CFBF_SIGNATURE:
        raise _refuse(
            "style_profile_password_protected",
            "이 파일은 암호로 보호되어 있거나 예전 .doc 형식입니다. 암호를 해제한 뒤 "
            ".docx로 다시 저장해 올려 주세요.",
            "This file is password-protected (or an old-format .doc). Remove the password "
            "and re-save as .docx before uploading.",
            "このファイルはパスワードで保護されているか、旧形式の.docです。パスワードを"
            "解除して.docxとして保存し直してからアップロードしてください。",
            status=400,
            source_file=source_file,
        )

    try:
        archive = zipfile.ZipFile(BytesIO(content))
        archive.close()
    except (zipfile.BadZipFile, OSError) as exc:
        raise _refuse(
            "style_profile_not_a_zip",
            "이 파일은 올바른 .docx(zip) 파일이 아닙니다.",
            "This file is not a valid .docx (zip) file.",
            "このファイルは正しい.docx（zip）ファイルではありません。",
            status=400,
            source_file=source_file,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc

    try:
        guard_xlsx_bytes(content, label=source_file)
    except XlsxBombError as exc:
        raise _refuse(
            "style_profile_zip_bomb",
            "이 파일은 압축 해제 시 비정상적으로 큰 데이터를 만들어 내는 구조여서 "
            "열지 않고 거부했습니다.",
            "This file's zip structure would decompress to an abnormal amount of data; it "
            "was refused before opening.",
            "このファイルは解凍時に異常に大きなデータを生成する構造のため、開かずに"
            "拒否しました。",
            status=400,
            source_file=source_file,
            detail=exc.detail,
        ) from exc

    with zipfile.ZipFile(BytesIO(content)) as archive:
        names = archive.namelist()
    if "word/document.xml" not in names:
        raise _refuse(
            "style_profile_not_a_docx",
            "이 zip 파일에는 word/document.xml이 없어 Word 문서로 보이지 않습니다.",
            "This zip file has no word/document.xml, so it does not look like a Word "
            "document.",
            "このzipファイルにはword/document.xmlがなく、Word文書には見えません。",
            status=400,
            source_file=source_file,
        )

    try:
        return docx.Document(BytesIO(content))
    except Exception as exc:  # noqa: BLE001 — PackageNotFoundError, KeyError, an lxml
        # XMLSyntaxError, or anything else python-docx can raise while opening
        # an otherwise zip-valid, word/document.xml-bearing package. Every
        # more specific fault above this already got its own name; this is
        # the deliberate catch-all.
        raise _refuse(
            "style_profile_unreadable",
            "이 문서를 열 수 없습니다 (손상되었거나 지원하지 않는 구조).",
            "This document could not be opened (corrupt, or an unsupported structure).",
            "この文書を開けません（破損している、またはサポートされていない構造です）。",
            status=400,
            source_file=source_file,
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc


def extract_style_profile(content: bytes, *, source_file: str) -> dict[str, Any]:
    """The whole extraction. Raises :class:`StyleProfileRefused` and nothing
    else. See the module docstring for the refusal order and for the
    "actually used" / "structural absence vs unspecified" doctrine every
    branch below follows.

    The returned dict is not fully JSON-safe by itself:
    ``header["logo"]["_bytes"]`` (when a logo was found) is a raw ``bytes``
    object, private to callers in this package — :func:`_public_profile`
    strips it before anything is persisted or returned over HTTP, and the
    STORE layer (:func:`store_profile`) is what actually writes those bytes
    to ``logo.<ext>``.
    """
    document = _open_docx_or_refuse(content, source_file=source_file)

    if not document.sections:
        # A package python-docx can open but that carries NOT ONE <w:sectPr>
        # (not even the body's own trailing one every normal Word document
        # writes) is degenerate: _extract_page, _extract_header, and
        # _extract_footer all key off sections[0], so this is not a single
        # missing leaf like "no tables" -- it is the whole page/header/footer
        # register having no ground truth to read at all. Refusal (not
        # named-absence-on-every-page.*-leaf) is the honest call here per
        # CLAUDE.md's own rule ("a refused edit with a clear reason beats a
        # completed edit with wrong numbers"): faking three independent
        # all-absent sub-trees to route around a structural fault this deep
        # is more likely to hide a real bug than to serve a real document --
        # no legitimate Word-authored .docx is missing every section.
        raise _refuse(
            "style_profile_no_sections",
            "이 문서에는 섹션이 하나도 없어 페이지 크기·여백·머리글·바닥글을 읽을 수 "
            "없습니다.",
            "This document has no sections at all, so page size, margins, header, and "
            "footer cannot be read.",
            "この文書にはセクションが一つもなく、ページサイズ・余白・ヘッダー・"
            "フッターを読み取れません。",
            status=422,
            source_file=source_file,
        )

    not_extracted: list[dict[str, Any]] = []

    palette, fonts, extra = _extract_palette_and_fonts(document, not_extracted)
    palette["emphasis"] = _extract_emphasis(document, not_extracted)

    page = _extract_page(document.sections[0], not_extracted)
    heading_numbering = _extract_heading_numbering(document, not_extracted)
    table_style, table_text_pt = _extract_table_style(document, not_extracted)
    fonts["ladder_pt"]["table"] = table_text_pt
    if table_style is not None and table_text_pt is None:
        # A table exists (so _extract_table_style's own "no tables" branch
        # already marked this leaf, above, and did NOT run) but no direct
        # font size was found on either its header or body row -- the size
        # may live in the table style's own definition, unread by design.
        _mark_missing(
            not_extracted,
            "fonts.ladder_pt.table",
            "표 안의 텍스트에 직접 지정된 글자 크기가 없어 표 본문 글자 크기를 추출하지 "
            "못했습니다 (표 스타일에서 오는 값일 수 있습니다).",
            "No table text carries an explicit font size, so the table body font size could "
            "not be extracted (the value may come from the table style).",
            "表内のテキストに直接指定されたフォントサイズがなく、表本文のフォント"
            "サイズを抽出できませんでした（表スタイルに由来する値の可能性があります）。",
        )
    if table_style is not None:
        palette["table_header_fill"] = table_style["header_row"]["fill"]
        palette["table_border"] = table_style["borders"]["color"]

    # palette.table_header_fill / palette.table_border mirror table_style's
    # own sub-fields (a deliberate duplication: "palette" and "table style"
    # are two separate catalogue entries in the design doc that happen to
    # share one source). Whatever reason table_style itself carries — no
    # tables at all, or an ambiguous/unset color within a real table — is
    # re-stated at the palette.* path too, so the "every None leaf is named"
    # invariant holds without a reader having to know the two paths are
    # related.
    if palette["table_header_fill"] is None:
        _mark_missing(
            not_extracted,
            "palette.table_header_fill",
            "표 머리글 배경색을 추출하지 못했습니다 (표가 없거나, 색이 셀마다 달라 "
            "하나로 단정할 수 없습니다).",
            "The table header background color could not be extracted (no tables, or the "
            "color is inconsistent across cells).",
            "表のヘッダー背景色を抽出できませんでした（表がないか、色がセルごとに"
            "異なり一つに断定できません）。",
        )
    if palette["table_border"] is None:
        _mark_missing(
            not_extracted,
            "palette.table_border",
            "표 테두리 색을 추출하지 못했습니다 (표가 없거나, 테두리 색 정보를 찾을 "
            "수 없습니다).",
            "The table border color could not be extracted (no tables, or no border color "
            "information could be found).",
            "表の罫線色を抽出できませんでした（表がないか、罫線の色情報が見つかりません）。",
        )

    header, _ = _extract_header(document, not_extracted)
    footer = _extract_footer(document, not_extracted)
    caption_prefix = _extract_caption(document, not_extracted)

    # header_footer_text: prefer the Caption style's own color (matches the
    # design sample, where Caption/header/footer share one muted gray); fall
    # back to the header text run's color when Caption is unused/unspecified.
    header_footer_hex = extra.get("caption_color_hex")
    if header_footer_hex is None:
        header_footer_hex = header.get("text_color")
    palette["header_footer_text"] = header_footer_hex
    if header_footer_hex is None:
        _mark_missing(
            not_extracted,
            "palette.header_footer_text",
            "캡션·머리글·바닥글 어디에서도 고정된 회색조 텍스트 색을 찾지 못했습니다.",
            "No fixed gray text color could be found in the caption, header, or footer.",
            "キャプション・ヘッダー・フッターのいずれにも固定のグレー系テキスト色が"
            "見つかりませんでした。",
        )

    profile = {
        "kind": STYLE_PROFILE_DOC_KIND,
        "page": page,
        "palette": palette,
        "fonts": fonts,
        "heading_numbering": heading_numbering,
        "table_style": table_style,
        "header": header,
        "footer": footer,
        "caption": {"prefix": caption_prefix},
        "not_extracted": not_extracted,
    }
    return profile


def _strip_private(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _strip_private(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [_strip_private(v) for v in value]
    return value


def public_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """``profile`` with every ``_``-prefixed private key removed recursively
    (today: only ``header.logo._bytes``). This is what gets persisted as
    JSON and what any HTTP response returns — the raw image bytes never
    round-trip through either."""
    return _strip_private(dict(profile))


# =============================================================================
# Store — U6 pattern for the registry; one directory per profile for the
# full record + logo. See the module docstring's "STORAGE" section.
# =============================================================================


def profiles_dir() -> Path:
    return (
        workspaces.workspace_dir(workspaces.current_workspace_id())
        / REPORT_DIRNAME
        / PROFILES_DIRNAME
    )


def registry_path() -> Path:
    return profiles_dir() / REGISTRY_FILENAME


def _record_dir(profile_id: str) -> Path:
    return profiles_dir() / profile_id


def _record_path(profile_id: str) -> Path:
    return _record_dir(profile_id) / PROFILE_FILENAME


def _clean_name(name: str | None, *, source_file: str) -> str:
    candidate = (name or "").strip()
    if not candidate:
        candidate = Path(source_file).stem or "style-profile"
    return candidate[:MAX_NAME_CHARS]


def _image_ext(content_type: str) -> str:
    return {"image/png": ".png", "image/jpeg": ".jpg"}.get(content_type, ".bin")


def _not_found(profile_id: str) -> StyleProfileRefused:
    return _refuse(
        "style_profile_not_found",
        "그런 스타일 프로파일이 이 프로젝트에 없습니다.",
        "No such style profile exists in this project.",
        "そのスタイルプロファイルはこのプロジェクトにありません。",
        status=404,
        profile_id=profile_id,
    )


def store_profile(*, content: bytes, source_file: str, name: str | None = None) -> dict[str, Any]:
    """Extract + persist ONE profile. Returns its summary (see :func:`get_profile`).

    Raises :class:`StyleProfileRefused` for anything :func:`extract_style_profile`
    refuses. Nothing is written to disk in that case.
    """
    profile = extract_style_profile(content, source_file=source_file)
    logo = profile.get("header", {}).get("logo") or {}
    logo_bytes: bytes | None = logo.get("_bytes")

    public = public_profile(profile)
    profile_id = uuid4().hex[:12]
    now = _now_iso()
    source_sha256 = hashlib.sha256(content).hexdigest()
    workspace_id = workspaces.current_workspace_id()

    logo_descriptor: dict[str, Any] | None = None
    if logo_bytes:
        ext = _image_ext(logo.get("content_type") or "")
        logo_descriptor = {
            "filename": f"logo{ext}",
            "sha256": logo.get("sha256"),
            "content_type": logo.get("content_type"),
        }

    record = {
        "kind": STYLE_PROFILE_DOC_KIND,
        "id": profile_id,
        "workspace_id": workspace_id,
        "name": _clean_name(name, source_file=source_file),
        "source_file": source_file,
        "source_sha256": source_sha256,
        "created_at": now,
        "extractor_version": EXTRACTOR_VERSION,
        "original_profile": public,
        "logo": logo_descriptor,
        "corrections": [],
    }

    with _LOCK:
        record_dir = _record_dir(profile_id)
        record_dir.mkdir(parents=True, exist_ok=True)
        try:
            _write_json_atomic(_record_path(profile_id), record)
            if logo_bytes and logo_descriptor is not None:
                (record_dir / logo_descriptor["filename"]).write_bytes(logo_bytes)

            def index(profiles: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], bool]:
                profiles[profile_id] = {
                    "id": profile_id,
                    "name": record["name"],
                    "source_file": source_file,
                    "source_sha256": source_sha256,
                    "created_at": now,
                    "revision_count": 0,
                    "has_logo": logo_descriptor is not None,
                }
                return profiles[profile_id], True

            summary = _mutate_registry(index)
        except BaseException:
            # Anything failed after the directory was created (the profile
            # write, the logo write, or the registry index update) -- leave
            # no orphan directory with no index entry, and no index entry
            # pointing at a directory that never finished writing.
            _safe_rmtree(record_dir)
            raise

    logger.info("report style-profiles: stored %s (%s) from %r", profile_id, record["name"], source_file)
    return summary | {"not_extracted_count": len(public.get("not_extracted") or [])}


def list_profiles() -> list[dict[str, Any]]:
    """Every profile's INDEX entry in this workspace, newest first."""
    with _LOCK:
        index = _read_index()
    return sorted(index.values(), key=lambda entry: str(entry.get("created_at") or ""), reverse=True)


def get_profile(profile_id: str) -> dict[str, Any]:
    """The FULL record (original profile + correction history + resolved
    profile). Raises :class:`StyleProfileRefused` (404) for an unknown id.
    """
    if not profile_id or not _ID_RE.match(profile_id):
        raise _not_found(profile_id)
    path = _record_path(profile_id)
    with _LOCK:
        if not path.exists():
            raise _not_found(profile_id)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StyleProfileError(
                f"Style profile {profile_id} exists in the index but its record at {path} could "
                f"not be read ({type(exc).__name__}: {exc})."
            ) from exc
    record["resolved_profile"] = resolved_profile(record)
    return record


def profile_logo_bytes(profile_id: str) -> bytes | None:
    """The stored header-logo image for one profile, or ``None``.

    The profile DICT never carries the image: :func:`public_profile` strips
    ``header.logo["_bytes"]`` out of everything that is persisted or returned
    over HTTP, and :func:`store_profile` writes the bytes to ``logo.<ext>``
    beside the record instead. A caller that has to actually re-embed the logo
    — ``services.report.style_apply.apply_profile`` is the only one today —
    reads them here rather than reaching into this module's private path
    helpers.

    ``None`` (never an exception) for a profile that has no logo, a record
    whose ``logo`` descriptor names a file that is gone, or an unreadable
    file: the applier turns that into a NAMED fallback ("the profile declares a
    logo but no bytes arrived"), which is a better answer than a 500 and a
    strictly better one than a silently logo-less header.
    """
    try:
        record = get_profile(profile_id)
    except StyleProfileError:
        return None
    descriptor = record.get("logo")
    if not isinstance(descriptor, Mapping):
        return None
    filename = str(descriptor.get("filename") or "")
    if not filename:
        return None
    path = _record_dir(profile_id) / filename
    try:
        return path.read_bytes()
    except OSError:
        return None


def resolved_profile(record: Mapping[str, Any]) -> dict[str, Any]:
    """``original_profile`` with every correction's ``fields`` deep-merged on
    top, in order — "the profile as corrected today"."""
    result: dict[str, Any] = json.loads(json.dumps(record.get("original_profile") or {}))
    for correction in record.get("corrections") or []:
        _deep_merge(result, correction.get("fields") or {})
    return result


def _deep_merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> None:
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def apply_correction(
    profile_id: str,
    *,
    fields: Mapping[str, Any],
    edited_by: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Append ONE correction. Never touches ``original_profile``.

    Raises:
        StyleProfileRefused: unknown id, no editor identified, or an empty
            ``fields`` mapping (a correction that changes nothing is not one).
    """
    editor = (edited_by or "").strip()
    if not editor:
        raise _refuse(
            "style_profile_no_editor",
            "수정한 사람을 확인할 수 없습니다. 로그인한 사용자만 프로파일을 고칠 수 "
            "있습니다.",
            "The editor could not be identified. Only a signed-in user can correct a "
            "profile.",
            "修正した人を確認できません。ログイン中の利用者のみプロファイルを"
            "修正できます。",
            status=401,
        )
    if not fields:
        raise _refuse(
            "style_profile_empty_correction",
            "수정할 내용이 없습니다.",
            "There is nothing to correct.",
            "修正する内容がありません。",
        )
    cleaned_reason = None
    if reason is not None:
        cleaned_reason = reason.strip()[:MAX_REASON_CHARS] or None

    if not profile_id or not _ID_RE.match(profile_id):
        raise _not_found(profile_id)

    now = _now_iso()
    with _LOCK:
        path = _record_path(profile_id)
        if not path.exists():
            raise _not_found(profile_id)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StyleProfileError(
                f"Style profile {profile_id}'s record at {path} could not be read "
                f"({type(exc).__name__}: {exc}); refusing to write a correction over it."
            ) from exc

        corrections = list(record.get("corrections") or [])
        revision = len(corrections) + 1
        corrections.append(
            {
                "revision": revision,
                "fields": dict(fields),
                "edited_by": editor,
                "edited_at": now,
                "reason": cleaned_reason,
            }
        )
        record["corrections"] = corrections
        _write_json_atomic(path, record)

        def bump(profiles: dict[str, dict[str, Any]]) -> tuple[None, bool]:
            entry = profiles.get(profile_id)
            if entry is not None:
                entry["revision_count"] = revision
            return None, True

        _mutate_registry(bump)

    logger.info("report style-profiles: %s corrected profile %s (revision %d)", editor, profile_id, revision)
    record["resolved_profile"] = resolved_profile(record)
    return record


# --- registry I/O — same shape as calibration_attestation.py ----------------

_T = TypeVar("_T")


def _mutate_registry(mutator: Callable[[dict[str, dict[str, Any]]], tuple[_T, bool]]) -> _T:
    with _LOCK:
        path = registry_path()
        payload = _read_registry_payload(path)
        result, changed = mutator(payload["profiles"])
        if changed:
            _write_json_atomic(path, payload)
        return result


def _read_index() -> dict[str, dict[str, Any]]:
    try:
        return _read_registry_payload(registry_path())["profiles"]
    except RegistryUnavailableError as exc:
        logger.error(
            "report style-profiles: reading the registry at %s failed (%s); reporting NO "
            "profiles for this call. The file is untouched.",
            registry_path(),
            exc,
        )
        return {}


def _read_registry_payload(path: Path) -> dict[str, Any]:
    with _LOCK:
        if not path.exists():
            return _empty_registry_payload()

        raw: bytes | None = None
        last_error: OSError | None = None
        for attempt in range(_READ_RETRY_ATTEMPTS):
            try:
                raw = path.read_bytes()
                break
            except OSError as exc:
                last_error = exc
                if attempt + 1 < _READ_RETRY_ATTEMPTS:
                    time.sleep(_READ_RETRY_DELAY_S)
        if raw is None:
            raise RegistryUnavailableError(
                f"Could not read {path} after {_READ_RETRY_ATTEMPTS} attempts: "
                f"{type(last_error).__name__}: {last_error}"
            )

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            return _quarantine_and_restart(path, raw, exc)

        normalized = _normalize_registry_payload(payload)
        if normalized is None:
            return _quarantine_and_restart(
                path, raw, ValueError("registry payload is not a {'profiles': {...}} object")
            )
        _remember_state(path, normalized)
        return normalized


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex[:8]}.tmp")
        serialized = json.dumps(payload, ensure_ascii=False, indent=2)
        try:
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                logger.warning(
                    "report style-profiles: could not remove temp file %s after a failed write; "
                    "it is inert.",
                    tmp_path,
                )
            raise
        if path.name == REGISTRY_FILENAME:
            _remember_state(path, payload)


def _empty_registry_payload() -> dict[str, Any]:
    return {"profiles": {}, "incidents": []}


def _normalize_registry_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    profiles = payload.get("profiles")
    if not isinstance(profiles, dict):
        return None
    clean: dict[str, dict[str, Any]] = {}
    for key, entry in profiles.items():
        if not isinstance(entry, dict):
            logger.warning(
                "report style-profiles: dropping unreadable registry entry %r (%s, not an object)",
                key,
                type(entry).__name__,
            )
            continue
        if not entry.get("id"):
            entry = {**entry, "id": str(key)}
        clean[str(key)] = entry
    incidents = payload.get("incidents")
    clean_incidents = (
        [entry for entry in incidents if isinstance(entry, dict)] if isinstance(incidents, list) else []
    )
    return {"profiles": clean, "incidents": clean_incidents[-_MAX_INCIDENTS:]}


def _quarantine_and_restart(path: Path, raw: bytes, exc: Exception) -> dict[str, Any]:
    key = str(path)
    lost = _last_known_record_count.get(key)
    try:
        quarantine_path = _quarantine_file(path)
    except OSError as move_exc:
        raise RegistryUnavailableError(
            f"The style-profile registry at {path} did not parse ({type(exc).__name__}: {exc}) "
            f"and could not be moved aside for quarantine ({type(move_exc).__name__}: {move_exc}). "
            "Refusing to overwrite it — those bytes are the only copy of this project's profile "
            "index."
        ) from move_exc

    incident = {
        "code": "registry_quarantined",
        "at": _now_iso(),
        "quarantine_path": str(quarantine_path),
        "corrupt_bytes": len(raw),
        "lost_record_count": lost,
        "parse_error": f"{type(exc).__name__}: {exc}",
    }
    logger.error(
        "report style-profiles: the registry at %s is CORRUPT (%s: %s, %d bytes). Quarantined at "
        "%s and a fresh registry started. Records lost: %s. Per-profile directories under %s are "
        "UNTOUCHED — only the index was corrupt.",
        path,
        type(exc).__name__,
        exc,
        len(raw),
        quarantine_path,
        lost if lost is not None else "unknown (first read since process start)",
        path.parent,
    )

    payload = _empty_registry_payload()
    payload["incidents"] = [incident]
    _write_json_atomic(path, payload)
    return payload


def _quarantine_file(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.name}.corrupt-{stamp}")
    counter = 2
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.corrupt-{stamp}-{counter}")
        counter += 1
    os.replace(path, candidate)
    return candidate


def _remember_state(path: Path, payload: dict[str, Any]) -> None:
    key = str(path)
    _last_known_record_count[key] = len(payload.get("profiles") or {})
    incidents = [
        entry
        for entry in (payload.get("incidents") or [])
        if isinstance(entry, dict) and entry.get("code") == "registry_quarantined"
    ]
    if not incidents:
        _registry_health.pop(key, None)
        return
    latest = incidents[-1]
    _registry_health[key] = {
        "code": "registry_quarantined",
        "at": latest.get("at"),
        "quarantine_path": latest.get("quarantine_path"),
        "lost_record_count": latest.get("lost_record_count"),
    }


def registry_health_status() -> dict[str, Any] | None:
    return _registry_health.get(str(registry_path()))


def _safe_rmtree(path: Path) -> None:
    import shutil

    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


__all__ = [
    "EXTRACTOR_VERSION",
    "STYLE_PROFILE_DOC_KIND",
    "RegistryUnavailableError",
    "StyleProfileError",
    "StyleProfileRefused",
    "apply_correction",
    "extract_style_profile",
    "get_profile",
    "list_profiles",
    "profile_logo_bytes",
    "profiles_dir",
    "public_profile",
    "registry_health_status",
    "registry_path",
    "resolved_profile",
    "store_profile",
]

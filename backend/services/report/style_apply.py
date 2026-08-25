"""Track R unit R-5a — the profile -> DOCX layer ("층 3 / 2. 적용").

R-4 (``services/report/style_profile.py``) turns a styled DOCX into a JSON
style catalogue. This module is the OTHER direction, and R-4's extractor is its
oracle: everything written here is written so that running
:func:`services.report.style_profile.extract_style_profile` back over the
result returns the profile that went in. ``tests/test_report_typeset.py``'s
golden round-trip is that claim, checked field by field over the extractor's
whole leaf set — not a spot check.

ONE SCHEMA, TWO DIRECTIONS
---------------------------
The dict this module consumes is exactly what the extractor emits and what the
store holds (``store_profile`` persists ``public_profile(...)`` and
``resolved_profile`` hands back the same shape with corrections merged). There
is no second "apply schema" to drift from the extraction schema, and
:data:`services.report.default_style.PRISM_DEFAULT_PROFILE` is expressed in it
too, so a stored client profile and the house default travel the same code
path.

THE CARDINAL SIN IS THE SILENT FALLBACK
-----------------------------------------
A profile is a PROPOSAL (R-4's C1-b grammar), and a real one has holes: every
``None`` leaf the extractor emits carries a matching ``not_extracted`` entry
naming its dotted path. Applying such a profile means falling back to the Prism
default FOR THAT FIELD — and every single fallback is NAMED in the returned
:class:`AppliedStyle`'s ``fallbacks`` list, with the field's dotted path, the
profile's own reason when it had one, and the default value actually used. A
report that quietly renders in the house font because the client's font could
not be read, and never says so, is the same class of lie as a silently deleted
section (R-1) or an empty table pretending to be an evidenced one (R-2).

Three distinct triggers, all named the same way and distinguishable by
``cause``:

* ``profile_named_gap`` — the path (or a dotted ancestor of it) appears in the
  profile's own ``not_extracted`` list. The profile's own trilingual reason is
  carried through verbatim rather than restated here.
* ``profile_missing`` — the path is absent from the dict, or its value is
  ``None``, without a matching ``not_extracted`` entry. A hand-written or
  partially-corrected profile can be in this state; the extractor's own output
  never is (R-4 holds that invariant), so this cause firing on extractor output
  would itself be a bug worth seeing.
* ``profile_unusable`` — a value is PRESENT but this module cannot write it (a
  malformed hex, a non-numeric size). Named separately because "you gave me
  nothing" and "you gave me something I could not use" are different facts to a
  user looking at why their report does not match their sample.

Choosing NO profile is not a fallback and is not recorded as one: the Prism
default is then the style the user asked for. Reporting forty-odd "fell back to
the default" entries for a default-styled report would bury the ONE entry that
matters when a real client profile has a real hole — the honesty record has to
stay readable to be honest.

WHAT WORD CANNOT STORE EXACTLY IS A NAMED LIMITATION, NOT A SILENT ROUNDING
-----------------------------------------------------------------------------
Two measured quantisations in the OOXML format itself:

* font size lives in HALF-POINTS (``w:sz``), so 10.5pt is exact and 10.3pt is
  not;
* border width lives in EIGHTHS of a point (``w:sz`` on ``w:tblBorders``), so
  0.5pt is exact and 0.6pt is not.

When a profile carries a value that does not land on the grid, this module
writes the nearest representable value AND appends a ``limitations`` entry
naming the field, the asked-for value and the written one. It never silently
rounds, and it never refuses a whole report over a tenth of a point.

NO READERS, NO ROUTERS, NO I/O
-------------------------------
This module takes a ``docx.Document`` and a dict and mutates the document. It
reads no run data, opens no file, and imports neither ``routers/`` nor
``services/report_exports.py`` — see ``services/report/typeset.py``'s docstring
for why the legacy exporters are fenced off from this whole unit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Mapping

from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor

from services.report.default_style import PRISM_DEFAULT_PROFILE

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# The field set this module applies — every dotted path it reads from a
# profile, in application order. Kept as data (not scattered through the code)
# because tests/test_report_typeset.py checks it against BOTH the extractor's
# own leaf set and the default profile's, so a field added to R-4's schema and
# forgotten here fails loudly instead of quietly never being applied.
# ---------------------------------------------------------------------------

APPLIED_FIELDS: tuple[str, ...] = (
    "page.width_mm",
    "page.height_mm",
    "page.margins_mm.top",
    "page.margins_mm.bottom",
    "page.margins_mm.left",
    "page.margins_mm.right",
    "fonts.body_family.ascii",
    "fonts.body_family.eastAsia",
    "fonts.heading_family.ascii",
    "fonts.heading_family.eastAsia",
    "fonts.ladder_pt.title",
    "fonts.ladder_pt.heading1",
    "fonts.ladder_pt.heading2",
    "fonts.ladder_pt.heading3",
    "fonts.ladder_pt.body",
    "fonts.ladder_pt.caption",
    "fonts.ladder_pt.table",
    "palette.title",
    "palette.heading1",
    "palette.heading2",
    "palette.heading3",
    "palette.body",
    "palette.emphasis",
    "palette.table_header_fill",
    "palette.table_border",
    "palette.header_footer_text",
    "heading_numbering.scheme",
    "heading_numbering.detected.h1",
    "heading_numbering.detected.h2",
    "heading_numbering.detected.h3",
    "table_style.header_row.fill",
    "table_style.header_row.text_color",
    "table_style.header_row.bold",
    "table_style.header_row.size_pt",
    "table_style.borders.color",
    "table_style.borders.width_pt",
    "header.logo",
    "header.text",
    "header.text_size_pt",
    "header.text_color",
    "header.rule_below",
    "footer.page_number.position",
    "footer.page_number.format",
    "footer.page_number.size_pt",
    "footer.page_number.color",
    "caption.prefix",
)

#: The heading style name each ladder/palette role maps to, and the profile
#: keys that feed it. Order matters only for readability.
_HEADING_STYLES: tuple[tuple[str, str], ...] = (
    ("title", "Title"),
    ("heading1", "Heading 1"),
    ("heading2", "Heading 2"),
    ("heading3", "Heading 3"),
)

_ALIGNMENTS = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}


@dataclass
class AppliedStyle:
    """What :func:`apply_profile` did, in full.

    ``resolved`` maps every dotted path in :data:`APPLIED_FIELDS` to the value
    actually written, so the typesetter reads ONE place for "what colour is a
    table header" rather than re-deriving the profile-or-default question per
    table. ``fallbacks`` and ``limitations`` are the honesty record — see the
    module docstring.
    """

    resolved: dict[str, Any] = field(default_factory=dict)
    fallbacks: list[dict[str, Any]] = field(default_factory=list)
    limitations: list[dict[str, Any]] = field(default_factory=list)

    def value(self, path: str) -> Any:
        return self.resolved.get(path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resolved": dict(self.resolved),
            "fallbacks": list(self.fallbacks),
            "limitations": list(self.limitations),
        }


# ---------------------------------------------------------------------------
# Resolution — profile leaf, or the Prism default with the fallback named.
# ---------------------------------------------------------------------------


def _get_path(source: Mapping[str, Any] | None, path: str) -> tuple[bool, Any]:
    """``(found, value)``. ``found`` is False when any segment is missing."""
    current: Any = source
    for segment in path.split("."):
        if not isinstance(current, Mapping) or segment not in current:
            return False, None
        current = current[segment]
    return True, current


def _named_gap_reason(profile: Mapping[str, Any], path: str) -> dict[str, str] | None:
    """The profile's OWN trilingual reason for ``path``, when it named one.

    Matched on the dotted path or any dotted ANCESTOR of it — R-4 marks
    ``table_style`` (the whole sub-tree) when a document has no tables at all,
    and marks ``table_style.header_row.fill`` when it has tables but an
    ambiguous fill. Both must cover ``table_style.header_row.fill``, exactly as
    R-4's own ``_uncovered_absent_leaves`` prefix rule reads them.
    """
    entries = profile.get("not_extracted") or []
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        marked = str(entry.get("field") or "")
        if not marked:
            continue
        if path == marked or path.startswith(marked + "."):
            reason = entry.get("reason")
            if isinstance(reason, Mapping):
                return {k: str(v) for k, v in reason.items()}
            return None
    return None


_FALLBACK_TEXT = {
    "profile_named_gap": (
        "이 항목은 스타일 프로파일이 '추출 못 함'으로 명명한 자리입니다 — Prism 기본값으로 조판했습니다.",
        "The style profile named this field as not extracted — the Prism default was used instead.",
        "この項目はスタイルプロファイルが「抽出できず」と明示した箇所です — Prismの既定値で組版しました。",
    ),
    "profile_missing": (
        "이 항목이 스타일 프로파일에 없습니다 — Prism 기본값으로 조판했습니다.",
        "The style profile carries no value for this field — the Prism default was used instead.",
        "この項目がスタイルプロファイルにありません — Prismの既定値で組版しました。",
    ),
    "profile_unusable": (
        "스타일 프로파일의 값을 이 형식으로 쓸 수 없어 Prism 기본값으로 조판했습니다.",
        "The style profile's value could not be written in this format — the Prism default was used.",
        "スタイルプロファイルの値をこの形式で書き込めないため、Prismの既定値で組版しました。",
    ),
}


class _Resolver:
    """Profile-leaf-or-default, with every fallback named. One instance per
    :func:`apply_profile` call."""

    def __init__(self, profile: Mapping[str, Any] | None) -> None:
        self._profile: Mapping[str, Any] = profile if isinstance(profile, Mapping) else {}
        #: NO profile at all means the Prism default IS the chosen style, not a
        #: fallback from anything — recording 40-odd "fell back" entries for the
        #: default would drown the ONE entry that matters when a real client
        #: profile genuinely has a hole. A fallback is only a fallback when
        #: something was supplied and came up short.
        self.has_profile: bool = bool(self._profile)
        self.fallbacks: list[dict[str, Any]] = []
        self.limitations: list[dict[str, Any]] = []
        self.resolved: dict[str, Any] = {}

    # -- fallbacks ------------------------------------------------------
    def _record_fallback(self, path: str, cause: str, default_value: Any) -> None:
        if not self.has_profile:
            return
        ko, en, ja = _FALLBACK_TEXT[cause]
        entry: dict[str, Any] = {
            "field": path,
            "cause": cause,
            "used_default": default_value,
            "reason": {"ko": ko, "en": en, "ja": ja},
        }
        profile_reason = _named_gap_reason(self._profile, path)
        if profile_reason is not None:
            # The PROFILE's own words for why the field is missing, carried
            # through verbatim rather than paraphrased — R-4 wrote them from
            # the document it actually read; this module has no better one.
            entry["profile_reason"] = profile_reason
        self.fallbacks.append(entry)

    def get(self, path: str) -> Any:
        default_found, default_value = _get_path(PRISM_DEFAULT_PROFILE, path)
        if not default_found:
            raise KeyError(
                f"style_apply asked for {path!r}, which services.report.default_style."
                "PRISM_DEFAULT_PROFILE does not declare — a field with no default has no "
                "fallback, which is exactly the silent-hole this module refuses to have"
            )
        found, value = _get_path(self._profile, path)
        if not found or value is None:
            cause = "profile_named_gap" if _named_gap_reason(self._profile, path) else "profile_missing"
            self._record_fallback(path, cause, default_value)
            self.resolved[path] = default_value
            return default_value
        self.resolved[path] = value
        return value

    def unusable(self, path: str) -> Any:
        """A present-but-unwritable value: fall back and name it separately."""
        _, default_value = _get_path(PRISM_DEFAULT_PROFILE, path)
        self._record_fallback(path, "profile_unusable", default_value)
        self.resolved[path] = default_value
        return default_value

    # -- typed getters ---------------------------------------------------
    def hex_color(self, path: str) -> str:
        value = self.get(path)
        if _valid_hex(value):
            return str(value).upper()
        return str(self.unusable(path)).upper()

    def points(self, path: str) -> float:
        value = self.get(path)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return float(self.unusable(path))
        if number <= 0:
            return float(self.unusable(path))
        return self._quantise(path, number, 2.0, "font_size_half_point_grid")

    def border_points(self, path: str) -> float:
        value = self.get(path)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return float(self.unusable(path))
        if number <= 0:
            return float(self.unusable(path))
        return self._quantise(path, number, 8.0, "border_width_eighth_point_grid")

    def _quantise(self, path: str, value: float, per_point: float, code: str) -> float:
        """OOXML stores this quantity on a fixed grid; land on it, and SAY SO
        when the profile asked for something between two teeth."""
        snapped = round(value * per_point) / per_point
        if abs(snapped - value) > 1e-9:
            self.limitations.append(
                {
                    "code": code,
                    "field": path,
                    "requested": value,
                    "written": snapped,
                    "reason": {
                        "ko": (
                            f"Word는 이 값을 1/{int(per_point)}pt 단위로만 저장합니다 — "
                            f"{value}pt를 {snapped}pt로 기록했습니다."
                        ),
                        "en": (
                            f"Word stores this quantity only in 1/{int(per_point)}pt steps — "
                            f"{value}pt was written as {snapped}pt."
                        ),
                        "ja": (
                            f"Wordはこの値を1/{int(per_point)}pt単位でのみ保存します — "
                            f"{value}ptを{snapped}ptとして記録しました。"
                        ),
                    },
                }
            )
            self.resolved[path] = snapped
        return snapped


def _valid_hex(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 6:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Low-level OOXML writers.
#
# Deliberately written here rather than imported from
# ``services/report_exports.py``. That module's ``_setcell``/``_set_border``
# are private, carry the legacy exporters' HARDCODED palette in their default
# arguments (``color="E69138"``, ``size=9``), write no text colour and no
# eastAsia font at all — none of which a profile-driven typesetter can use —
# and importing anything from that module would trip this unit's own
# never-import-the-legacy-readers guard (see ``typeset.py``'s docstring).
# ---------------------------------------------------------------------------


def _set_east_asia(rpr_owner: Any, family: str) -> None:
    """Write ``w:rFonts/@w:eastAsia`` — python-docx's ``font.name`` sets only
    ``w:ascii``/``w:hAnsi``, and R-4 reads the eastAsia slot separately BY
    DESIGN ("ascii and eastAsia are different fonts", never backfilled)."""
    rpr = rpr_owner.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    rfonts.set(qn("w:eastAsia"), family)


def _style_font(style: Any, *, ascii_family: str, east_asia: str, size_pt: float, color_hex: str) -> None:
    style.font.name = ascii_family
    style.font.size = Pt(size_pt)
    style.font.color.rgb = RGBColor.from_string(color_hex)
    _set_east_asia(style.element, east_asia)


def _paragraph_bottom_rule(paragraph: Any, color_hex: str, width_pt: float) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    borders = p_pr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        p_pr.append(borders)
    bottom = borders.find(qn("w:bottom"))
    if bottom is None:
        bottom = OxmlElement("w:bottom")
        borders.append(bottom)
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(max(2, int(round(width_pt * 8)))))
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), color_hex)


def _page_field_runs(paragraph: Any, *, size_pt: float, color_hex: str) -> None:
    """A live ``{ PAGE }`` field: begin / instrText / end, and NO cached
    result. The cached-result form (begin / separate / "1" / end) would put a
    literal ``w:t`` inside the field, which R-4's footer reader would read as
    part of the format string — "- N -" would come back as "- N1 -"."""

    def _run_with_rpr() -> Any:
        run = paragraph.add_run()
        run.font.size = Pt(size_pt)
        run.font.color.rgb = RGBColor.from_string(color_hex)
        return run

    begin = _run_with_rpr()
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    begin._r.append(fld_begin)

    instr_run = _run_with_rpr()
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    instr_run._r.append(instr)

    end = _run_with_rpr()
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    end._r.append(fld_end)


# ---------------------------------------------------------------------------
# The whole application.
# ---------------------------------------------------------------------------


def apply_profile(
    document: Any,
    profile: Mapping[str, Any] | None,
    *,
    logo_bytes: bytes | None = None,
) -> AppliedStyle:
    """Write ``profile`` onto ``document``'s styles, page, header and footer.

    Every field comes from :data:`APPLIED_FIELDS`; every field the profile
    cannot supply falls back to :data:`services.report.default_style.
    PRISM_DEFAULT_PROFILE` and is NAMED in the returned
    :class:`AppliedStyle`. Returns before any content is added — the
    typesetter then reads ``AppliedStyle.resolved`` for the values it needs
    per table and per caption.

    ``logo_bytes`` is the header logo's image bytes. The profile itself only
    DESCRIBES a logo (present/sha256/content_type/size — R-4 strips the raw
    bytes out of every persisted and HTTP-returned profile); the bytes live
    beside the record on disk. A profile that claims ``present: True`` while no
    bytes reach this function gets a NAMED fallback, never a silently
    logo-less header.
    """
    resolver = _Resolver(profile)

    _apply_page(document, resolver)
    _apply_styles(document, resolver)
    _apply_header(document, resolver, logo_bytes)
    _apply_footer(document, resolver)
    # Table + caption values are consumed by the typesetter, not written onto
    # a style here: R-4 reads them off the FIRST REAL TABLE's direct
    # formatting (never off a table-style definition, by its own stated
    # doctrine), so writing them into a latent style would leave the extractor
    # with nothing to read.
    header_fill = resolver.hex_color("table_style.header_row.fill")
    resolver.hex_color("table_style.header_row.text_color")
    _apply_table_header_bold(resolver)
    resolver.points("table_style.header_row.size_pt")
    border_color = resolver.hex_color("table_style.borders.color")
    resolver.border_points("table_style.borders.width_pt")
    # ``palette.table_header_fill`` / ``palette.table_border`` MIRROR the two
    # table-style leaves above — R-4's extractor writes both paths off one
    # source and says so in its own docstring. A human correction that edits
    # only one of a mirrored pair leaves the profile self-contradictory; the
    # table-style path wins (it is where R-4 reads the value back FROM), and
    # the disagreement is named rather than resolved in silence.
    _mirror(resolver, "palette.table_header_fill", "table_style.header_row.fill", header_fill)
    _mirror(resolver, "palette.table_border", "table_style.borders.color", border_color)
    resolver.points("fonts.ladder_pt.table")
    resolver.get("caption.prefix")
    resolver.get("heading_numbering.scheme")
    for level in ("h1", "h2", "h3"):
        resolver.get(f"heading_numbering.detected.{level}")
    resolver.hex_color("palette.emphasis")

    return AppliedStyle(
        resolved=dict(resolver.resolved),
        fallbacks=list(resolver.fallbacks),
        limitations=list(resolver.limitations),
    )


def _mirror(resolver: _Resolver, mirror_path: str, source_path: str, written: str) -> None:
    """Resolve a leaf that DUPLICATES another, and name any disagreement."""
    value = resolver.hex_color(mirror_path)
    if value != written:
        resolver.limitations.append(
            {
                "code": "mirrored_palette_leaf_disagrees",
                "field": mirror_path,
                "requested": value,
                "written": written,
                "reason": {
                    "ko": (
                        f"프로파일의 {mirror_path}({value})와 {source_path}({written})가 서로 다릅니다 "
                        f"— 두 항목은 같은 값을 가리키므로 {source_path}를 적용했습니다."
                    ),
                    "en": (
                        f"The profile's {mirror_path} ({value}) and {source_path} ({written}) disagree "
                        f"— they mirror one source, so {source_path} was applied."
                    ),
                    "ja": (
                        f"プロファイルの{mirror_path}（{value}）と{source_path}（{written}）が一致しません "
                        f"— 両者は同一の値を指すため{source_path}を適用しました。"
                    ),
                },
            }
        )
    resolver.resolved[mirror_path] = written


def _apply_table_header_bold(resolver: _Resolver) -> None:
    value = resolver.get("table_style.header_row.bold")
    if not isinstance(value, bool):
        resolver.unusable("table_style.header_row.bold")


def _apply_page(document: Any, resolver: _Resolver) -> None:
    section = document.sections[0]
    section.page_width = Mm(_positive(resolver, "page.width_mm"))
    section.page_height = Mm(_positive(resolver, "page.height_mm"))
    section.top_margin = Mm(_positive(resolver, "page.margins_mm.top"))
    section.bottom_margin = Mm(_positive(resolver, "page.margins_mm.bottom"))
    section.left_margin = Mm(_positive(resolver, "page.margins_mm.left"))
    section.right_margin = Mm(_positive(resolver, "page.margins_mm.right"))


def _positive(resolver: _Resolver, path: str) -> float:
    value = resolver.get(path)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(resolver.unusable(path))
    if number <= 0:
        return float(resolver.unusable(path))
    return number


def _apply_styles(document: Any, resolver: _Resolver) -> None:
    body_ascii = str(resolver.get("fonts.body_family.ascii"))
    body_east = str(resolver.get("fonts.body_family.eastAsia"))
    heading_ascii = str(resolver.get("fonts.heading_family.ascii"))
    heading_east = str(resolver.get("fonts.heading_family.eastAsia"))

    _style_font(
        document.styles["Normal"],
        ascii_family=body_ascii,
        east_asia=body_east,
        size_pt=resolver.points("fonts.ladder_pt.body"),
        color_hex=resolver.hex_color("palette.body"),
    )
    for role, style_name in _HEADING_STYLES:
        _style_font(
            document.styles[style_name],
            ascii_family=heading_ascii,
            east_asia=heading_east,
            size_pt=resolver.points(f"fonts.ladder_pt.{role}"),
            color_hex=resolver.hex_color(f"palette.{role}"),
        )
    # Caption shares the muted header/footer grey by design — R-4 reads
    # ``palette.header_footer_text`` off the Caption style first and only falls
    # back to the header run's colour, so this is the leaf that feeds it.
    _style_font(
        document.styles["Caption"],
        ascii_family=body_ascii,
        east_asia=body_east,
        size_pt=resolver.points("fonts.ladder_pt.caption"),
        color_hex=resolver.hex_color("palette.header_footer_text"),
    )


_LOGO_MISSING_BYTES = {
    "ko": "프로파일은 머리글 로고가 있다고 하지만 이미지 바이트가 전달되지 않아 로고 없이 조판했습니다.",
    "en": "The profile declares a header logo but no image bytes were supplied, so the header was "
    "typeset without one.",
    "ja": "プロファイルはヘッダーロゴがあると宣言していますが画像バイトが渡されなかったため、"
    "ロゴなしで組版しました。",
}


def _apply_header(document: Any, resolver: _Resolver, logo_bytes: bytes | None) -> None:
    section = document.sections[0]
    header = section.header
    header.is_linked_to_previous = False
    paragraph = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    for run in list(paragraph.runs):
        run._r.getparent().remove(run._r)

    text_color = resolver.hex_color("header.text_color")
    text_size = resolver.points("header.text_size_pt")

    logo = resolver.get("header.logo")
    logo_present = bool(isinstance(logo, Mapping) and logo.get("present"))
    if logo_present:
        if logo_bytes:
            width_mm = logo.get("display_width_mm")
            run = paragraph.add_run()
            try:
                if isinstance(width_mm, (int, float)) and width_mm > 0:
                    run.add_picture(BytesIO(logo_bytes), width=Mm(float(width_mm)))
                else:
                    run.add_picture(BytesIO(logo_bytes))
            except Exception as exc:  # noqa: BLE001 — an unreadable image is DATA, not a 500
                run._r.getparent().remove(run._r)
                resolver.fallbacks.append(
                    {
                        "field": "header.logo",
                        "cause": "profile_unusable",
                        "used_default": {"present": False},
                        "reason": {
                            "ko": f"머리글 로고 이미지를 삽입할 수 없었습니다 ({type(exc).__name__}: {exc}).",
                            "en": f"The header logo image could not be embedded ({type(exc).__name__}: {exc}).",
                            "ja": f"ヘッダーロゴ画像を埋め込めませんでした（{type(exc).__name__}: {exc}）。",
                        },
                    }
                )
        elif resolver.has_profile:
            resolver.fallbacks.append(
                {
                    "field": "header.logo",
                    "cause": "profile_missing",
                    "used_default": {"present": False},
                    "reason": dict(_LOGO_MISSING_BYTES),
                }
            )

    # A tab-only run separates the logo from the right-hand text. R-4's header
    # reader takes the first run whose text STRIPS to something non-empty, so a
    # pure-tab run is invisible to it — which is the point: the layout spacer
    # must not be mistaken for the header text.
    tab_stops = paragraph.paragraph_format.tab_stops
    text_width = section.page_width - section.left_margin - section.right_margin
    tab_stops.add_tab_stop(text_width, WD_TAB_ALIGNMENT.RIGHT)
    spacer = paragraph.add_run("\t")
    spacer.font.size = Pt(text_size)

    header_text = resolver.get("header.text")
    text_run = paragraph.add_run(str(header_text))
    text_run.font.size = Pt(text_size)
    text_run.font.color.rgb = RGBColor.from_string(text_color)

    if bool(resolver.get("header.rule_below")):
        _paragraph_bottom_rule(paragraph, text_color, 0.5)


_FOOTER_FORMAT_NO_SLOT = {
    "ko": "바닥글 형식 문자열에 페이지 번호 자리('N')가 없어 Prism 기본 형식으로 조판했습니다.",
    "en": "The footer format string carries no page-number slot ('N'), so the Prism default format "
    "was used.",
    "ja": "フッターの書式文字列にページ番号のスロット（'N'）がないため、Prismの既定書式で組版しました。",
}


def _apply_footer(document: Any, resolver: _Resolver) -> None:
    section = document.sections[0]
    footer = section.footer
    footer.is_linked_to_previous = False
    paragraph = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    for run in list(paragraph.runs):
        run._r.getparent().remove(run._r)

    position = str(resolver.get("footer.page_number.position") or "").lower()
    if position not in _ALIGNMENTS:
        position = str(resolver.unusable("footer.page_number.position")).lower()
    paragraph.alignment = _ALIGNMENTS[position]

    size_pt = resolver.points("footer.page_number.size_pt")
    color_hex = resolver.hex_color("footer.page_number.color")

    template = resolver.get("footer.page_number.format")
    if not isinstance(template, str) or "N" not in template:
        resolver.fallbacks.append(
            {
                "field": "footer.page_number.format",
                "cause": "profile_unusable",
                "used_default": PRISM_DEFAULT_PROFILE["footer"]["page_number"]["format"],
                "reason": dict(_FOOTER_FORMAT_NO_SLOT),
            }
        )
        template = PRISM_DEFAULT_PROFILE["footer"]["page_number"]["format"]
        resolver.resolved["footer.page_number.format"] = template

    prefix, suffix = template.split("N", 1)
    if prefix:
        run = paragraph.add_run(prefix)
        run.font.size = Pt(size_pt)
        run.font.color.rgb = RGBColor.from_string(color_hex)
    _page_field_runs(paragraph, size_pt=size_pt, color_hex=color_hex)
    if suffix:
        run = paragraph.add_run(suffix)
        run.font.size = Pt(size_pt)
        run.font.color.rgb = RGBColor.from_string(color_hex)


__all__ = [
    "APPLIED_FIELDS",
    "AppliedStyle",
    "apply_profile",
]

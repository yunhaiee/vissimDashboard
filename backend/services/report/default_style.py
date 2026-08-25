"""Track R unit R-5a — the PRISM DEFAULT style profile.

The design doc's own words for the /report UI's style picker: "스타일 프로파일
선택 (기본: **Prism 기본 스타일**)". This module is that default, expressed as
data in exactly the schema :func:`services.report.style_profile.
extract_style_profile` emits — one schema for the whole track, never a second
one — so :func:`services.report.style_apply.apply_profile` reads a stored
client profile and this constant through the identical code path.

WHY IT IS COMPLETE, AND WHY THAT MATTERS
-----------------------------------------
Every leaf :data:`PRISM_DEFAULT_PROFILE` declares carries a real value. Not one
is ``None``. That is the whole point: it is what a PER-FIELD fallback falls
back TO. R-4's extractor is honest about what it could not read (a ``None``
leaf always has a matching ``not_extracted`` entry naming its dotted path), and
:func:`services.report.style_apply.apply_profile` turns each of those named
gaps into "use the Prism default for THIS field, and say so in the manifest".
A default with holes of its own would turn one named gap into a silent one, so
this module has none — and ``tests/test_report_typeset.py`` checks that
mechanically (every leaf of the extractor's own schema is present here and
non-``None``), so a leaf added to the schema later cannot quietly ship a
default of ``None``.

``header.logo`` is the one leaf that looks like a hole and is not:
``{"present": False}`` is a complete, deliberate value — "the house style ships
no logo" — not "a logo we failed to read". A client profile that DOES carry a
logo overrides it.

THE VALUES ARE THE PRODUCT'S OWN, NOT A CLIENT'S
--------------------------------------------------
The palette is Prism's own UI slate/blue register (``frontend/app/globals.css``
tokens ``--color-slate-*`` / ``--color-sky-*`` family), not the Dochak sample's
navy — copying a client's brand into the product default would ship one
client's identity to every other client. The pt ladder and A4/30mm-gutter page
geometry follow the same Korean consulting-report convention the sample does,
because that convention is the market's, not any one firm's.
"""

from __future__ import annotations

from typing import Any

#: Same document-kind marker the extractor stamps, so a default profile and an
#: extracted one are indistinguishable to every consumer downstream.
DEFAULT_PROFILE_KIND = "prism.report_style_profile.v1"

DEFAULT_PROFILE_NAME = "Prism 기본 스타일"

PRISM_DEFAULT_PROFILE: dict[str, Any] = {
    "kind": DEFAULT_PROFILE_KIND,
    "page": {
        "size": "A4",
        "width_mm": 210.0,
        "height_mm": 297.0,
        # 30mm left = the binding gutter this market's reports keep.
        "margins_mm": {"top": 25.0, "bottom": 25.0, "left": 30.0, "right": 25.0},
    },
    "palette": {
        "title": "0F172A",
        "heading1": "0F172A",
        "heading2": "1D4ED8",
        "heading3": "1D4ED8",
        "body": "1F2937",
        "emphasis": "B91C1C",
        "table_header_fill": "0F172A",
        "table_border": "CBD5E1",
        "header_footer_text": "64748B",
    },
    "fonts": {
        "heading_family": {"ascii": "맑은 고딕", "eastAsia": "맑은 고딕"},
        "body_family": {"ascii": "맑은 고딕", "eastAsia": "맑은 고딕"},
        "ladder_pt": {
            "title": 22.0,
            "heading1": 16.0,
            "heading2": 14.0,
            "heading3": 12.0,
            "body": 10.5,
            "caption": 9.0,
            "table": 9.5,
        },
    },
    "heading_numbering": {
        # "typed_decimal" is the extractor's own token for "the number is typed
        # into the heading text" (as opposed to "numbering_xml"). See
        # services/report/typeset.py's NUMBERING section for why this build
        # types its numbers rather than driving numbering.xml.
        "scheme": "typed_decimal",
        "detected": {"h1": "1.", "h2": "1.1", "h3": "1.1.1"},
    },
    "table_style": {
        "header_row": {
            "fill": "0F172A",
            "text_color": "FFFFFF",
            "bold": True,
            "size_pt": 9.5,
        },
        "borders": {"color": "CBD5E1", "width_pt": 0.5},
    },
    "header": {
        # A deliberate, complete "no logo" — not an unread one.
        "logo": {"present": False},
        "text": "Prism · Vissim 모델 보정·검증 보고서",
        "text_size_pt": 8.5,
        "text_color": "64748B",
        "rule_below": True,
    },
    "footer": {
        "page_number": {
            "present": True,
            "position": "center",
            "format": "- N -",
            "size_pt": 8.5,
            "color": "64748B",
        }
    },
    "caption": {"prefix": "표 "},
    "not_extracted": [],
}


__all__ = ["DEFAULT_PROFILE_KIND", "DEFAULT_PROFILE_NAME", "PRISM_DEFAULT_PROFILE"]

"""Track R unit R-5a — the styled report typesetter.

See ``services/report/typeset.py`` and ``services/report/style_apply.py`` for
the doctrine this file protects. Its job is to prove that doctrine holds by
running the REAL composer and reading the REAL bytes back:

0. **The locks on the oracle's own escape hatches.** ``ROUNDTRIP_NOT_CLAIMED``
   excuses a leaf from the round-trip check, so it is pinned twice: exact key
   set, AND a structural predicate admitting only typed-heading-number leaves.
   Both exist because of a measured failure — a real regression (table border
   width hardcoded) was hidden from the whole suite by one plausible line added
   to that map. ``DERIVED_LEAVES`` gets the same lock for the same reason.
1. **The synthetic round trip — the CI oracle.** A hand-built profile whose
   every leaf is perturbed away from the Prism default (different hexes, pt
   ladder, twip-exact page geometry, opposite numbering polarity, its own
   caption prefix, a PNG logo generated in-process) is composed, then R-4's
   extractor is run over MY OWN OUTPUT BYTES and must return every leaf exact.
   Runs UNCONDITIONALLY — no client data, no skip.
2. **The golden round trip** — the same oracle against the user-approved
   Dochak sample in ``client-data/report-style-samples/``, which is the
   real-world check. Skipped-with-reason on a fresh checkout (``client-data/``
   is gitignored) — which is exactly why family 1 exists.
3. **Schema set discipline** — default profile leaves == extractor schema
   leaves, both directions, and every leaf consumed by APPLIED_FIELDS /
   DERIVED_LEAVES. A dead leaf added to the default profile was once invisible
   to everything; a one-directional subset check cannot see it.
4. **Absence honesty** — an absent figure, a NOT_AVAILABLE Table 5-15 output
   and a section with no figures each render their own named sentence, in the
   muted style, into the document. Dropping any of them is a mutation this
   file kills.
5. **Manifest honesty** — a profile with a hole falls back PER FIELD and names
   every fallback; a report composed with no profile at all names none (the
   Prism default is then the chosen style, not a fallback from anything). Both
   OOXML quantisation grids (half-point font sizes, eighth-point border
   widths) are named limitations, tested on both sides.
6. **Caption survival** — every key of every figure's ``source_caption``
   survives into the document text. R-2/R-3 refuse to emit a figure that
   cannot say which run it came from; a DOCX that drops the caption would void
   that at the last step.
7. **Reopenability / determinism / selection / HTTP / wire literals**, plus an
   AST-BASED FENCE proving no module in this unit can reach the legacy
   exporters or their stale-workbook GEH reader — by import statement OR by
   string handed to ``importlib``, which a ``^import`` regex cannot see.

Fixtures, not live readers: the figure aggregators are monkeypatched to fixed
payloads in R-2/R-3's exact shapes, so every assertion here is about the
TYPESETTER and none of it depends on demo data, a workspace's contents, or
Vissim.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
import re
import unicodedata
import zipfile
from pathlib import Path
from typing import Any

import docx
import pytest
from docx.oxml import parse_xml
from docx.oxml.ns import qn
from fastapi.testclient import TestClient

import main
from services.report import default_style, figures as report_figures
from services.report import graphics as report_graphics
from services.report import spine, style_apply, style_profile, typeset

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "client-data" / "report-style-samples"
SAMPLE_DOCX = SAMPLE_DIR / "샘플보고서-도착스타일-v1.docx"


def _skip_without_sample() -> None:
    if not SAMPLE_DOCX.is_file():
        pytest.skip(
            "client-data/report-style-samples/ is gitignored client data, absent on a fresh "
            "checkout -- same convention as test_report_style_profile.py's own golden round trip."
        )


# =============================================================================
# Fixture figures — R-2's table shape and R-3's graphic shape, verbatim.
# =============================================================================

FIXTURE_TABLE_CAPTION = {
    "run_id": "RUN-FIXTURE-1",
    "session_id": "SESSION-FIXTURE-1",
    "dataset_revision": "rev-fixture-7",
    "generated_at": "2026-08-25T00:00:00+00:00",
    "reader": "services.agent.tools_metrics.build_reality_check_report",
}

FIXTURE_GRAPHIC_CAPTION = {
    "scenario_id": "RUN-FIXTURE-1",
    "dataset_revision": "rev-fixture-7",
    "generated_at": "2026-08-25T00:00:00+00:00",
    "reader": "services.agent.tools_network.build_shadow_model",
}

FIXTURE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 60">'
    '<rect x="1" y="1" width="98" height="58" fill="#f8fafc" stroke="#334155"/>'
    "</svg>"
)


def _table_figure() -> dict[str, Any]:
    return {
        "figure_id": "geh_node_table",
        "spine_section_id": "statistical_comparison",
        "absent": False,
        "title": {"ko": "GEH 노드 표", "en": "GEH by node", "ja": "GEHノード表"},
        "columns": [
            {"key": "node_id", "ko": "노드", "en": "Node", "ja": "ノード"},
            {"key": "geh", "ko": "GEH", "en": "GEH", "ja": "GEH"},
        ],
        "rows": [{"node_id": "8", "geh": 3.21}, {"node_id": "9", "geh": None}],
        "source_caption": dict(FIXTURE_TABLE_CAPTION),
        "citation": "MRWA OMG v2.0 §5.6.3 p.214",
    }


def _absent_table_figure() -> dict[str, Any]:
    return {
        "figure_id": "approval_history_table",
        "spine_section_id": "parameter_adjustment_record",
        "absent": True,
        "reason": {
            "ko": "세션(session_id)이 지정되지 않아 승인 이력을 불러올 수 없습니다.",
            "en": "No session_id was supplied, so the approval history cannot be read.",
            "ja": "session_idが指定されていないため承認履歴を読み込めません。",
        },
    }


def _graphic_figure() -> dict[str, Any]:
    return {
        "figure_id": "node_map",
        "spine_section_id": "statistical_comparison",
        "absent": False,
        "title": {"ko": "노드 지도", "en": "Node map", "ja": "ノードマップ"},
        "svg": FIXTURE_SVG,
        "legend": [
            {"key": "good", "ko": "양호", "en": "Good", "ja": "良好", "fill": "#16a34a", "stroke": "#166534"},
            {"key": "poor", "ko": "불량", "en": "Poor", "ja": "不良", "fill": "#dc2626", "stroke": "#7f1d1d"},
        ],
        "source_caption": dict(FIXTURE_GRAPHIC_CAPTION),
        "citation": None,
        "meta": {"total_node_count": 3, "graded_node_count": 2, "ungraded_node_ids": ["11"]},
    }


def _absent_graphic_figure() -> dict[str, Any]:
    return {
        "figure_id": "before_after_geh_chart",
        "spine_section_id": "calibration",
        "absent": True,
        "reason": {
            "ko": "비교할 실행이 모두 지정되지 않아 전후 비교를 만들 수 없습니다.",
            "en": "Not every scenario_id needed for the comparison was supplied.",
            "ja": "比較する実行がすべて指定されていないため前後比較を作成できません。",
        },
    }


# =============================================================================
# The SYNTHETIC style profile — the oracle that runs on a fresh checkout.
#
# The client-data golden round trip below is the real-world sample check, but
# it SKIPS where client-data is absent, which on CI is always: only 2 of 55
# leaves were value-checked there. This profile needs no client data at all.
#
# Every leaf is perturbed AWAY from PRISM_DEFAULT_PROFILE on purpose — a leaf
# that happens to equal the default cannot distinguish "the applier wrote the
# profile's value" from "the applier fell back and nobody noticed". The three
# leaves that legitimately cannot be perturbed are pinned by name in
# _UNPERTURBABLE below, so a FOURTH leaf drifting back to the default fails.
#
# The page geometry is deliberately TWIP-EXACT (US Letter and inch-fraction
# margins). Word stores page size and margins in twips, not EMU: an arbitrary
# millimetre value (216.0mm, 15.0mm) comes back off by 0.01mm and would make
# this oracle look flaky when it is the format quantising, not the typesetter
# losing a value. Measured, then chosen.
# =============================================================================


def _synthetic_logo_png() -> bytes:
    """A real 400x100 PNG, generated in-process. 400/100 = 4, so a 32mm
    display width gives exactly 8mm of height — the descriptor below is
    arithmetic, not a guess."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (400, 100), (20, 40, 80)).save(buffer, "PNG")
    return buffer.getvalue()


SYNTHETIC_LOGO_PNG = _synthetic_logo_png()

SYNTHETIC_PROFILE: dict[str, Any] = {
    "kind": "prism.report_style_profile.v1",
    "page": {
        "size": "Custom",
        "width_mm": 215.9,
        "height_mm": 279.4,
        "margins_mm": {"top": 19.05, "bottom": 31.75, "left": 38.1, "right": 12.7},
    },
    "palette": {
        "title": "112233",
        "heading1": "223344",
        "heading2": "334455",
        "heading3": "445566",
        "body": "556677",
        "emphasis": "667788",
        "table_header_fill": "778899",
        "table_border": "8899AA",
        "header_footer_text": "99AABB",
    },
    "fonts": {
        "heading_family": {"ascii": "Batang", "eastAsia": "바탕"},
        "body_family": {"ascii": "Gulim", "eastAsia": "굴림"},
        "ladder_pt": {
            "title": 20.0,
            "heading1": 15.0,
            "heading2": 13.0,
            "heading3": 11.5,
            "body": 11.0,
            "caption": 8.0,
            "table": 8.5,
        },
    },
    # Opposite trailing-dot polarity from the default on every level.
    "heading_numbering": {"scheme": "typed_decimal", "detected": {"h1": "1", "h2": "1.1.", "h3": "1.1.1."}},
    "table_style": {
        # bold False, not True: an explicit "not bold" is a real style
        # statement and must not come back as R-4's "no explicit bold" absence.
        "header_row": {"fill": "778899", "text_color": "EEDDCC", "bold": False, "size_pt": 9.0},
        "borders": {"color": "8899AA", "width_pt": 0.75},
    },
    "header": {
        "logo": {
            "present": True,
            "sha256": hashlib.sha256(SYNTHETIC_LOGO_PNG).hexdigest(),
            "content_type": "image/png",
            "width_px": 400,
            "height_px": 100,
            "display_width_mm": 32.0,
            "display_height_mm": 8.0,
        },
        "text": "Synthetic header text",
        "text_size_pt": 7.5,
        "text_color": "AABBCC",
        "rule_below": False,
    },
    "footer": {
        "page_number": {
            "present": True,
            "position": "right",
            "format": "Page N",
            "size_pt": 7.0,
            "color": "BBCCDD",
        }
    },
    "caption": {"prefix": "Fig "},
    "not_extracted": [],
}

#: The only three leaves that cannot be perturbed away from the Prism default,
#: each for a stated reason — pinned as an EXACT set so a third leaf drifting
#: back to the default value weakens the oracle loudly instead of quietly.
_UNPERTURBABLE = {
    # A profile reporting Word's automatic numbering is a named DOWNGRADE, not
    # a style this build can reproduce — so the round-trippable scheme is the
    # typed one, and that is the default's too.
    "heading_numbering.scheme",
    # "no page number at all" is a different document, not a different style.
    "footer.page_number.present",
    # The schema marker itself.
    "kind",
}


@pytest.fixture()
def fixed_figures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both aggregators pinned to fixed payloads: one real table, one real
    graphic, one absent table, one absent graphic."""
    monkeypatch.setattr(
        report_figures, "all_figures", lambda **_kwargs: [_table_figure(), _absent_table_figure()]
    )
    monkeypatch.setattr(
        report_graphics, "all_graphics", lambda **_kwargs: [_graphic_figure(), _absent_graphic_figure()]
    )


# =============================================================================
# Helpers
# =============================================================================


def _open(content: bytes) -> Any:
    return docx.Document(io.BytesIO(content))


def _document_text(content: bytes) -> str:
    document = _open(content)
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def _headings(content: bytes) -> list[tuple[str, str]]:
    document = _open(content)
    return [
        (paragraph.style.name, paragraph.text)
        for paragraph in document.paragraphs
        if paragraph.style is not None and paragraph.style.name.startswith("Heading")
    ]


def _leaves(value: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, item in value.items():
        path = f"{prefix}{key}"
        if isinstance(item, dict):
            out.update(_leaves(item, path + "."))
        else:
            out[path] = item
    return out


def _dochak_profile() -> tuple[dict[str, Any], bytes | None]:
    full = style_profile.extract_style_profile(SAMPLE_DOCX.read_bytes(), source_file=SAMPLE_DOCX.name)
    logo_bytes = ((full.get("header") or {}).get("logo") or {}).get("_bytes")
    return style_profile.public_profile(full), logo_bytes


# =============================================================================
# 0. THE LOCKS ON THE ORACLE'S OWN ESCAPE HATCHES
#
# Measured, not hypothetical: a REAL regression (the table border width
# hardcoded to 3.0) was hidden from the whole 50-test unit scope AND the
# 223-test report scope by adding ONE plausible line to ROUNDTRIP_NOT_CLAIMED.
# An oracle with a one-line off switch is not an oracle. Two independent
# locks, so silencing a leaf requires editing a pinned literal set AND
# rewriting a structural predicate — both visible in a diff.
# =============================================================================


def test_the_not_claimed_map_key_set_is_pinned_exactly() -> None:
    """LOCK 1 — exact set equality against the three literal paths. Any
    addition fails HERE, forcing a deliberate, reviewable test edit rather
    than a silent one-line escape from the round-trip oracle."""
    assert set(typeset.ROUNDTRIP_NOT_CLAIMED) == {
        "heading_numbering.detected.h1",
        "heading_numbering.detected.h2",
        "heading_numbering.detected.h3",
    }


def test_every_not_claimed_key_is_structurally_a_typed_heading_number() -> None:
    """LOCK 2 — the structural predicate. Even a deliberate edit of the pinned
    set above cannot admit a palette, ladder, page or table leaf: those are
    STYLE, and a style leaf that does not survive the round trip is a bug, not
    an exemption. Admitting one would mean rewriting this predicate, which is
    precisely the visible act the lock exists to force."""
    pattern = re.compile(typeset.NOT_CLAIMED_KEY_PATTERN)
    for path, reason in typeset.ROUNDTRIP_NOT_CLAIMED.items():
        assert pattern.match(path), (
            f"{path!r} is not a typed-heading-number leaf — a style leaf may not be excused "
            "from the round-trip oracle"
        )
        assert reason.strip()

    # ...and the predicate really is narrow: the leaves a regression would
    # most want to hide behind it do NOT match.
    for tempting in ("palette.title", "table_style.borders.width_pt", "fonts.ladder_pt.body", "page.width_mm"):
        assert not pattern.match(tempting)


def test_the_derived_leaves_map_key_set_is_pinned_exactly() -> None:
    """The OTHER place a leaf could be quietly excused from being applied.
    Same lock, same reason."""
    assert set(typeset.DERIVED_LEAVES) == {"kind", "page.size", "footer.page_number.present"}
    for path, reason in typeset.DERIVED_LEAVES.items():
        assert reason.strip()
    # A style value can never be "derived": derivation is for the schema
    # marker and for values that FOLLOW from applied fields.
    assert not any(path.startswith(("palette.", "fonts.", "table_style.")) for path in typeset.DERIVED_LEAVES)


# =============================================================================
# 1. THE SYNTHETIC ROUND TRIP — the oracle that runs everywhere, no client data
# =============================================================================


def _compose_synthetic() -> tuple[bytes, dict[str, Any]]:
    return typeset.compose_report(
        style_profile=SYNTHETIC_PROFILE, logo_bytes=SYNTHETIC_LOGO_PNG, profile_name="synthetic"
    )


def test_the_synthetic_profile_really_differs_from_the_prism_default() -> None:
    """The oracle below is only as strong as the perturbation: a leaf equal to
    the Prism default cannot tell "applied the profile" from "fell back and
    nobody noticed"."""
    synthetic = _leaves({k: v for k, v in SYNTHETIC_PROFILE.items() if k != "not_extracted"})
    default = _leaves({k: v for k, v in default_style.PRISM_DEFAULT_PROFILE.items() if k != "not_extracted"})

    shared = {path for path in synthetic if path in default and synthetic[path] == default[path]}
    assert shared == _UNPERTURBABLE, (
        "a leaf drifted back to the Prism default value — the round-trip oracle can no longer "
        f"distinguish applied-from-defaulted for it: {sorted(shared - _UNPERTURBABLE)}"
    )


def test_synthetic_round_trip_reproduces_every_style_leaf(fixed_figures: None) -> None:
    """THE CI ORACLE. No client data, no skip: compose in a profile whose every
    leaf differs from the house default, then run R-4's extractor over MY OWN
    BYTES and demand every leaf back, exact, except the (locked) three."""
    content, manifest = _compose_synthetic()
    extracted = style_profile.public_profile(
        style_profile.extract_style_profile(content, source_file="composed.docx")
    )

    before = _leaves({k: v for k, v in SYNTHETIC_PROFILE.items() if k != "not_extracted"})
    after = _leaves({k: v for k, v in extracted.items() if k != "not_extracted"})
    assert set(after) == set(before)

    mismatches = {
        path: (before[path], after[path])
        for path in sorted(before)
        if before[path] != after[path] and path not in typeset.ROUNDTRIP_NOT_CLAIMED
    }
    assert mismatches == {}, f"style leaves did not survive the round trip: {mismatches}"

    assert len(before) - len(typeset.ROUNDTRIP_NOT_CLAIMED) >= 45
    assert manifest["style"]["fallbacks"] == []
    assert manifest["style"]["source"] == "profile"


def test_synthetic_round_trip_names_nothing_as_unextractable(fixed_figures: None) -> None:
    content, _manifest = _compose_synthetic()
    extracted = style_profile.extract_style_profile(content, source_file="composed.docx")
    assert [entry["field"] for entry in extracted["not_extracted"]] == []


def test_synthetic_round_trip_keeps_the_numbering_shape(fixed_figures: None) -> None:
    """The synthetic profile's trailing-dot polarity is the OPPOSITE of the
    default's on every level, so a typesetter that ignored the profile and
    typed the house shape fails here."""
    content, _manifest = _compose_synthetic()
    extracted = style_profile.extract_style_profile(content, source_file="composed.docx")
    for level in (1, 2, 3):
        expected = SYNTHETIC_PROFILE["heading_numbering"]["detected"][f"h{level}"]
        got = extracted["heading_numbering"]["detected"][f"h{level}"]
        assert len(got.rstrip(".").split(".")) == level
        assert got.endswith(".") == expected.endswith(".")


def test_synthetic_round_trip_carries_the_generated_logo_through(fixed_figures: None) -> None:
    content, _manifest = _compose_synthetic()
    extracted = style_profile.public_profile(
        style_profile.extract_style_profile(content, source_file="composed.docx")
    )
    assert extracted["header"]["logo"] == SYNTHETIC_PROFILE["header"]["logo"]


# =============================================================================
# 2. THE GOLDEN ROUND TRIP — the same oracle against the real client sample
# =============================================================================


def test_golden_round_trip_reproduces_every_style_leaf(fixed_figures: None) -> None:
    """The oracle. Every leaf of the profile that went in comes back out of MY
    OWN BYTES, except the leaves ``typeset.ROUNDTRIP_NOT_CLAIMED`` names."""
    _skip_without_sample()
    profile, logo_bytes = _dochak_profile()

    content, manifest = typeset.compose_report(
        style_profile=profile, logo_bytes=logo_bytes, profile_name="dochak-v1"
    )
    extracted = style_profile.public_profile(
        style_profile.extract_style_profile(content, source_file="composed.docx")
    )

    before = _leaves({k: v for k, v in profile.items() if k != "not_extracted"})
    after = _leaves({k: v for k, v in extracted.items() if k != "not_extracted"})

    # Both directions: a leaf that vanished from the output and a leaf that
    # appeared out of nowhere are both failures.
    assert set(after) == set(before)

    mismatches = {
        path: (before[path], after[path])
        for path in sorted(before)
        if before[path] != after[path] and path not in typeset.ROUNDTRIP_NOT_CLAIMED
    }
    assert mismatches == {}, f"style leaves did not survive the round trip: {mismatches}"

    # ...and the claim is not vacuous: the great majority of leaves really are
    # compared, not excused.
    assert len(before) - len(typeset.ROUNDTRIP_NOT_CLAIMED) >= 45
    assert manifest["style"]["source"] == "profile"


def test_golden_round_trip_names_nothing_as_unextractable(fixed_figures: None) -> None:
    """The Dochak sample extracts with an EMPTY ``not_extracted``. A report
    composed from it must too — a composed document that loses a style role
    (an unused Caption, a colourless heading) would show up here as a named
    absence R-4 invented for it."""
    _skip_without_sample()
    profile, logo_bytes = _dochak_profile()
    assert profile["not_extracted"] == []

    content, _manifest = typeset.compose_report(style_profile=profile, logo_bytes=logo_bytes)
    extracted = style_profile.extract_style_profile(content, source_file="composed.docx")
    assert [entry["field"] for entry in extracted["not_extracted"]] == []


def test_golden_round_trip_numbering_keeps_the_scheme_shape(fixed_figures: None) -> None:
    """``heading_numbering.detected`` is content, not style (that is exactly
    why it is in ROUNDTRIP_NOT_CLAIMED). What IS reproduced is the scheme's
    shape: one component per level, trailing dot where the profile had one."""
    _skip_without_sample()
    profile, logo_bytes = _dochak_profile()
    content, _manifest = typeset.compose_report(style_profile=profile, logo_bytes=logo_bytes)
    extracted = style_profile.extract_style_profile(content, source_file="composed.docx")

    assert extracted["heading_numbering"]["scheme"] == profile["heading_numbering"]["scheme"]
    for level in (1, 2, 3):
        expected = profile["heading_numbering"]["detected"][f"h{level}"]
        got = extracted["heading_numbering"]["detected"][f"h{level}"]
        assert len(got.rstrip(".").split(".")) == level
        assert got.endswith(".") == expected.endswith(".")


def test_roundtrip_not_claimed_only_names_real_leaves(fixed_figures: None) -> None:
    """A path listed as "not claimed" that is not a real profile leaf would be
    an excuse for nothing — and would silently widen the exemption if the
    schema ever renamed that leaf. (The two LOCKS on the map itself are at the
    top of this file; this only checks the paths are real.)"""
    _skip_without_sample()
    profile, _logo = _dochak_profile()
    leaves = _leaves({k: v for k, v in profile.items() if k != "not_extracted"})
    assert set(typeset.ROUNDTRIP_NOT_CLAIMED) <= set(leaves)


def test_golden_round_trip_carries_the_logo_bytes_through(fixed_figures: None) -> None:
    """The header logo survives as the SAME IMAGE — the sha256 R-4 computes off
    the re-extracted document equals the one it computed off the sample."""
    _skip_without_sample()
    profile, logo_bytes = _dochak_profile()
    assert logo_bytes, "the sample's header carries a logo; this test is about carrying it through"

    content, manifest = typeset.compose_report(style_profile=profile, logo_bytes=logo_bytes)
    extracted = style_profile.extract_style_profile(content, source_file="composed.docx")
    assert extracted["header"]["logo"]["sha256"] == profile["header"]["logo"]["sha256"]
    assert manifest["style"]["fallbacks"] == []


def test_a_profile_logo_without_bytes_is_a_named_fallback(fixed_figures: None) -> None:
    """A profile that DECLARES a logo while no bytes reach the applier gets a
    named fallback, never a silently logo-less header."""
    _skip_without_sample()
    profile, _logo = _dochak_profile()

    _content, manifest = typeset.compose_report(style_profile=profile, logo_bytes=None)
    named = [entry["field"] for entry in manifest["style"]["fallbacks"]]
    assert "header.logo" in named


# =============================================================================
# 2. Absence honesty
# =============================================================================


def test_an_absent_figure_renders_its_named_reason(fixed_figures: None) -> None:
    content, manifest = typeset.compose_report()
    text = _document_text(content)

    reason = _absent_table_figure()["reason"]["ko"]
    assert reason in text
    assert _absent_graphic_figure()["reason"]["ko"] in text
    assert typeset._t("no_evidence", "ko") in text

    absent_ids = {entry["figure_id"] for entry in manifest["figures"] if entry["absent"]}
    assert absent_ids == {"approval_history_table", "before_after_geh_chart"}


def _run_color(run: Any) -> str | None:
    try:
        rgb = run.font.color.rgb
    except Exception:  # noqa: BLE001 — a theme colour python-docx cannot resolve
        return None
    return None if rgb is None else str(rgb)


def test_named_absences_are_muted_and_emphasis_is_reserved(fixed_figures: None) -> None:
    """"muted style" is not decoration — it is the visual half of the named
    absence, and the emphasis colour is reserved for the ONE lead line that
    states the gap counts. A named absence printed in the emphasis colour
    would read as a finding rather than a gap (and would make R-4's emphasis
    extraction ambiguous on this document)."""
    palette = default_style.PRISM_DEFAULT_PROFILE["palette"]
    content, _manifest = typeset.compose_report()
    document = _open(content)

    reason = _absent_table_figure()["reason"]["ko"]
    absence = next(paragraph for paragraph in document.paragraphs if reason in paragraph.text)
    assert absence.runs
    for run in absence.runs:
        assert _run_color(run) == palette["header_footer_text"]
        assert not run.bold

    emphasised = [
        paragraph
        for paragraph in document.paragraphs
        if any(run.bold and _run_color(run) == palette["emphasis"] for run in paragraph.runs)
    ]
    assert len(emphasised) == 1
    # ...and it is the front-matter line that states the gap counts, whose
    # invariant tail survives the count substitution.
    tail = typeset._L["honesty_line"]["ko"].split("— ", 1)[1]
    assert tail in emphasised[0].text


def test_a_section_with_no_figures_says_so_instead_of_vanishing(fixed_figures: None) -> None:
    """R-1's rule: 절 삭제는 거짓말. ``validation`` has no figure bound to it in
    this build — its heading is still there, and it carries a named absence."""
    content, manifest = typeset.compose_report()
    headings = [text for _style, text in _headings(content)]
    assert any("검증 (Validation)" in text for text in headings)
    assert typeset._t("no_figures_bound", "ko") in _document_text(content)

    validation = next(entry for entry in manifest["sections"] if entry["id"] == "validation")
    assert validation["state"] == typeset.SECTION_STATE_INCLUDED
    assert validation["named_absences"], "a section with no figures owes a named absence"


def test_not_available_table_5_15_outputs_are_named_in_the_document(fixed_figures: None) -> None:
    """R-1 audits five Table 5-15 outputs as NOT_AVAILABLE. Every one of them
    that belongs to an included section is written into the document with its
    reason — an omitted requirement reads as a requirement that passed."""
    content, _manifest = typeset.compose_report()
    text = _document_text(content)

    unavailable = spine.unavailable_outputs()
    assert unavailable, "R-1 declares NOT_AVAILABLE outputs; this test is about surfacing them"
    for row_type, name in unavailable:
        assert f"{row_type} · {name}" in text, f"{row_type}/{name} was not named in the document"
    assert typeset._t("not_available_output", "ko") in text


def test_a_figure_bound_to_no_spine_section_gets_the_named_appendix(monkeypatch: pytest.MonkeyPatch) -> None:
    """R-3's ``network_sketch`` binds to NO regulation section on purpose. It is
    never dropped: it lands in the appendix, whose body quotes R-3's own
    NETWORK_SKETCH_SECTION_GAP constant."""
    unbound = _graphic_figure() | {"figure_id": "network_sketch", "spine_section_id": None}
    monkeypatch.setattr(report_figures, "all_figures", lambda **_k: [])
    monkeypatch.setattr(report_graphics, "all_graphics", lambda **_k: [unbound])

    content, manifest = typeset.compose_report()
    text = _document_text(content)
    assert report_graphics.NETWORK_SKETCH_SECTION_GAP in text
    appendix = next(entry for entry in manifest["sections"] if entry["id"] == typeset.UNBOUND_SECTION_ID)
    assert appendix["figure_ids"] == ["network_sketch"]


# =============================================================================
# 3. Manifest honesty — per-field fallbacks
# =============================================================================


def _holed_profile() -> dict[str, Any]:
    """A profile in R-4's own shape with TWO holes, each named in
    ``not_extracted`` exactly as the extractor would name them."""
    profile = json.loads(json.dumps(default_style.PRISM_DEFAULT_PROFILE))
    profile["palette"]["emphasis"] = None
    profile["fonts"]["ladder_pt"]["caption"] = None
    profile["not_extracted"] = [
        {
            "field": "palette.emphasis",
            "reason": {"ko": "굵게 표시된 색이 없음", "en": "no bold coloured run", "ja": "太字の色なし"},
        },
        {
            "field": "fonts.ladder_pt.caption",
            "reason": {"ko": "캡션 크기 없음", "en": "no caption size", "ja": "キャプションサイズなし"},
        },
    ]
    return profile


def test_every_per_field_fallback_is_named_in_the_manifest(fixed_figures: None) -> None:
    _content, manifest = typeset.compose_report(style_profile=_holed_profile())
    fallbacks = {entry["field"]: entry for entry in manifest["style"]["fallbacks"]}

    assert "palette.emphasis" in fallbacks
    assert "fonts.ladder_pt.caption" in fallbacks
    for field, entry in fallbacks.items():
        assert entry["cause"] == "profile_named_gap", field
        assert entry["used_default"] is not None, field
        assert set(entry["reason"]) == {"ko", "en", "ja"}, field
        # The PROFILE's own words for the gap, carried through verbatim.
        assert entry["profile_reason"]["ko"], field
    assert fallbacks["palette.emphasis"]["used_default"] == (
        default_style.PRISM_DEFAULT_PROFILE["palette"]["emphasis"]
    )


def test_a_value_present_but_unusable_is_named_apart_from_a_missing_one(fixed_figures: None) -> None:
    profile = json.loads(json.dumps(default_style.PRISM_DEFAULT_PROFILE))
    profile["palette"]["body"] = "not-a-hex"
    profile["footer"]["page_number"]["position"] = "diagonal"

    _content, manifest = typeset.compose_report(style_profile=profile)
    causes = {entry["field"]: entry["cause"] for entry in manifest["style"]["fallbacks"]}
    assert causes["palette.body"] == "profile_unusable"
    assert causes["footer.page_number.position"] == "profile_unusable"


def test_no_profile_means_no_fallbacks(fixed_figures: None) -> None:
    """Choosing the Prism default is not falling back from anything. Reporting
    forty "fell back" entries here would bury the one that matters."""
    _content, manifest = typeset.compose_report()
    assert manifest["style"]["fallbacks"] == []
    assert manifest["style"]["source"] == "default"
    assert manifest["style"]["profile_name"] == default_style.DEFAULT_PROFILE_NAME


def test_a_font_size_off_the_half_point_grid_is_a_named_limitation(fixed_figures: None) -> None:
    """Word stores font sizes in half-points. 10.3pt cannot be written; the
    nearest representable value is, and the manifest says so."""
    profile = json.loads(json.dumps(default_style.PRISM_DEFAULT_PROFILE))
    profile["fonts"]["ladder_pt"]["body"] = 10.3

    _content, manifest = typeset.compose_report(style_profile=profile)
    entry = next(
        item
        for item in manifest["limitations"]
        if item.get("code") == "font_size_half_point_grid" and item.get("field") == "fonts.ladder_pt.body"
    )
    assert entry["requested"] == 10.3
    assert entry["written"] == 10.5


def test_the_caption_prefix_comes_from_the_profile(fixed_figures: None) -> None:
    """"표 " is the Dochak sample's prefix, not this module's. A profile that
    says something else gets something else — hardcoding the Korean default
    would pass every other test in this file."""
    profile = json.loads(json.dumps(default_style.PRISM_DEFAULT_PROFILE))
    profile["caption"]["prefix"] = "TBL "

    content, _manifest = typeset.compose_report(style_profile=profile)
    document = _open(content)
    captions = [
        paragraph.text
        for paragraph in document.paragraphs
        if paragraph.style is not None and paragraph.style.name == "Caption"
    ]
    assert captions, "a report with real figures has captions"
    assert all(text.startswith("TBL ") for text in captions), captions

    extracted = style_profile.extract_style_profile(content, source_file="composed.docx")
    assert extracted["caption"]["prefix"] == "TBL "


def test_an_automatic_numbering_profile_is_a_named_downgrade(fixed_figures: None) -> None:
    """R-4 reports ``numbering_xml`` for a sample that used Word's automatic
    numbering. This build types its numbers instead — and says so."""
    profile = json.loads(json.dumps(default_style.PRISM_DEFAULT_PROFILE))
    profile["heading_numbering"]["scheme"] = "numbering_xml"

    _content, manifest = typeset.compose_report(style_profile=profile)
    codes = [entry["code"] for entry in manifest["limitations"]]
    assert typeset.LIMITATION_NUMBERING_SCHEME["code"] in codes


def test_a_numbering_example_at_the_wrong_depth_is_named(fixed_figures: None) -> None:
    profile = json.loads(json.dumps(default_style.PRISM_DEFAULT_PROFILE))
    profile["heading_numbering"]["detected"]["h2"] = "1.1.1.1"

    _content, manifest = typeset.compose_report(style_profile=profile)
    codes = [entry["code"] for entry in manifest["limitations"]]
    assert typeset.LIMITATION_NUMBERING_DEPTH["code"] in codes


def test_a_mirrored_palette_leaf_that_disagrees_is_named(fixed_figures: None) -> None:
    """R-4 writes ``palette.table_header_fill`` and
    ``table_style.header_row.fill`` off ONE source. A correction that edits
    only one leaves the profile self-contradictory — the table style wins (it
    is where R-4 reads the value back from) and the disagreement is named."""
    profile = json.loads(json.dumps(default_style.PRISM_DEFAULT_PROFILE))
    profile["palette"]["table_header_fill"] = "AB12CD"

    content, manifest = typeset.compose_report(style_profile=profile)
    entry = next(
        item for item in manifest["limitations"] if item.get("code") == "mirrored_palette_leaf_disagrees"
    )
    assert entry["field"] == "palette.table_header_fill"
    assert entry["written"] == default_style.PRISM_DEFAULT_PROFILE["table_style"]["header_row"]["fill"]

    extracted = style_profile.extract_style_profile(content, source_file="composed.docx")
    assert extracted["palette"]["table_header_fill"] == entry["written"]


#: All six edges ``_table_borders`` writes. ALL of them are verified, never
#: just ``w:top``: R-4's own reader looks at ``w:top`` and falls back to
#: ``insideH``/``bottom``, so a table whose top edge carried the right width
#: while the other five carried nonsense read back as correct through the
#: extractor AND through a single-edge XML assertion. Measured — that exact
#: shape (correct top, ``w:sz="999"`` elsewhere) passed the whole file.
#: Today's applier writes all six from one shared value in one loop, so the
#: divergence is not reachable; a future per-edge conditional would make it
#: reachable and invisible at the same time.
_BORDER_EDGES = ("top", "left", "bottom", "right", "insideH", "insideV")


def _border_widths(content: bytes) -> dict[str, str | None]:
    """``{edge: w:sz}`` for every edge of the first table's ``w:tblBorders``."""
    borders = _open(content).tables[0]._tbl.tblPr.find(qn("w:tblBorders"))
    assert borders is not None, "the table carries no w:tblBorders at all"
    widths: dict[str, str | None] = {}
    for edge in _BORDER_EDGES:
        element = borders.find(qn(f"w:{edge}"))
        widths[edge] = None if element is None else element.get(qn("w:sz"))
    return widths


def test_a_border_width_off_the_eighth_point_grid_is_a_named_limitation(fixed_figures: None) -> None:
    """The SECOND half of the quantisation pair the module claims. Word stores
    a table border width in EIGHTHS of a point (``w:tblBorders/@w:sz``), so
    0.6pt cannot be written; 0.625pt (5/8) is. The nearest representable value
    goes in, the manifest says so, and R-4 reads exactly that value back — a
    silent round would look identical in the document and be a lie in the
    manifest."""
    profile = json.loads(json.dumps(default_style.PRISM_DEFAULT_PROFILE))
    profile["table_style"]["borders"]["width_pt"] = 0.6

    content, manifest = typeset.compose_report(style_profile=profile)
    entry = next(
        item
        for item in manifest["limitations"]
        if item.get("code") == "border_width_eighth_point_grid"
        and item.get("field") == "table_style.borders.width_pt"
    )
    assert entry["requested"] == 0.6
    assert entry["written"] == 0.625
    assert set(entry["reason"]) == {"ko", "en", "ja"}

    # ...and the value that reached the document IS the quantised one: w:sz is
    # in eighths, so 0.625pt is written as 5. Asserted on the raw attribute
    # rather than through R-4, because R-4's own reader reports
    # ``round(sz / 8.0, 2)`` — it hands back 0.62 for a genuine 5/8, a
    # two-decimal READING precision that is not this typesetter's rounding and
    # must not be confused with it.
    assert _border_widths(content) == dict.fromkeys(_BORDER_EDGES, "5")
    assert style_profile.extract_style_profile(content, source_file="composed.docx")[
        "table_style"
    ]["borders"]["width_pt"] == 0.62


def test_a_border_width_on_the_grid_names_no_limitation(fixed_figures: None) -> None:
    """The other side of the same claim: an exactly-representable width is
    written silently, because there is nothing to confess — and it reaches
    every edge, not just the one R-4 happens to read first."""
    profile = json.loads(json.dumps(default_style.PRISM_DEFAULT_PROFILE))
    content, manifest = typeset.compose_report(style_profile=profile)
    assert not [
        item for item in manifest["limitations"] if item.get("code") == "border_width_eighth_point_grid"
    ]
    # The default's 0.5pt is exactly 4/8, on all six edges.
    assert _border_widths(content) == dict.fromkeys(_BORDER_EDGES, "4")


def test_the_svg_limitation_is_named_whenever_a_graphic_is_typeset(fixed_figures: None) -> None:
    _content, manifest = typeset.compose_report()
    codes = [entry["code"] for entry in manifest["limitations"]]
    assert typeset.LIMITATION_SVG_NOT_EMBEDDED["code"] in codes
    assert typeset.LIMITATION_SINGLE_CAPTION_PREFIX["code"] in codes


def test_the_svg_limitation_is_stated_in_the_document_itself(fixed_figures: None) -> None:
    """A reader of the .docx may never see the manifest. The document names
    the limitation AND the artifact file the picture actually lives in."""
    content, manifest = typeset.compose_report()
    text = _document_text(content)
    assert typeset.LIMITATION_SVG_NOT_EMBEDDED["reason"]["ko"] in text

    artifact_names = {entry["name"] for entry in manifest["artifacts"]}
    assert artifact_names == {"svg/node_map.svg"}
    for name in artifact_names:
        assert name in text, "the document must name the artifact its figure ships in"


# =============================================================================
# 4. Caption survival
# =============================================================================


def test_every_source_caption_key_survives_into_the_document(fixed_figures: None) -> None:
    """R-2/R-3 refuse to release a figure that cannot say which run it came
    from. The DOCX must not undo that at the last step: every KEY and every
    VALUE of every real figure's source caption is in the document text."""
    content, _manifest = typeset.compose_report()
    text = _document_text(content)

    for caption in (FIXTURE_TABLE_CAPTION, FIXTURE_GRAPHIC_CAPTION):
        for key, value in caption.items():
            assert f"{key}=" in text, f"caption key {key} was dropped"
            if value is not None:
                assert str(value) in text, f"caption value for {key} was dropped"
    assert "MRWA OMG v2.0 §5.6.3 p.214" in text, "a figure's citation was dropped"


def test_source_caption_text_renders_a_none_value_by_name(fixed_figures: None) -> None:
    """A caption KEY whose value is legitimately None still prints its key —
    R-2's contract is about the key's presence, and a dropped key would read as
    a caption that never had that field."""
    rendered = typeset.source_caption_text({"run_id": None, "reader": "x"}, "ko")
    assert "run_id=" in rendered
    assert "reader=x" in rendered


# =============================================================================
# 5. Reopenability, artifacts, determinism
# =============================================================================


def test_the_bytes_reopen_as_a_real_docx(fixed_figures: None) -> None:
    content, _manifest = typeset.compose_report()
    assert content[:2] == b"PK"
    document = _open(content)
    assert document.tables, "a report with a real table figure has a real table"
    styles = {paragraph.style.name for paragraph in document.paragraphs if paragraph.style}
    assert {"Title", "Heading 1", "Heading 2", "Heading 3", "Normal", "Caption"} <= styles


def test_the_artifact_zip_validates_and_carries_the_svg_originals(fixed_figures: None) -> None:
    content, manifest = typeset.compose_artifact_zip()
    archive = zipfile.ZipFile(io.BytesIO(content))
    assert archive.testzip() is None
    assert set(archive.namelist()) == {"svg/node_map.svg", typeset.ARTIFACT_MANIFEST_NAME}

    assert archive.read("svg/node_map.svg").decode("utf-8") == FIXTURE_SVG
    stored = json.loads(archive.read(typeset.ARTIFACT_MANIFEST_NAME).decode("utf-8"))
    assert stored["kind"] == typeset.TYPESET_MANIFEST_KIND
    assert stored["artifacts"] == manifest["artifacts"]


_ISO_STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d[.\d]*\+00:00")


def test_composition_is_deterministic_apart_from_the_clock(fixed_figures: None) -> None:
    """Content determinism — the document's TEXT and the manifest, with the
    one legitimately-varying value (the server clock) masked."""
    first, first_manifest = typeset.compose_report()
    second, second_manifest = typeset.compose_report()

    assert _ISO_STAMP.sub("<t>", _document_text(first)) == _ISO_STAMP.sub("<t>", _document_text(second))

    first_manifest["generated_at"] = second_manifest["generated_at"] = "<t>"
    assert first_manifest == second_manifest


def test_only_word_document_xml_varies_between_two_compositions(fixed_figures: None) -> None:
    """The determinism claim, stated to match the MEASUREMENT rather than
    overstated.

    The raw .docx bytes are NOT stable call to call, and this test does not
    pretend otherwise: python-docx saves through ``zipfile``, whose default
    ``ZipInfo`` stamps each member with the WALL-CLOCK DOS timestamp, so the
    container metadata moves even when nothing in the content does. What IS
    true, and what was measured: the member NAMES are identical and every
    member's CONTENT is byte-identical except ``word/document.xml`` — and even
    that differs only in the embedded ``generated_at``.
    """
    first, _m1 = typeset.compose_report()
    second, _m2 = typeset.compose_report()

    archive_a = zipfile.ZipFile(io.BytesIO(first))
    archive_b = zipfile.ZipFile(io.BytesIO(second))
    assert archive_a.namelist() == archive_b.namelist()

    differing = [name for name in archive_a.namelist() if archive_a.read(name) != archive_b.read(name)]
    assert differing == ["word/document.xml"], differing

    body_a = _ISO_STAMP.sub("<t>", archive_a.read("word/document.xml").decode("utf-8"))
    body_b = _ISO_STAMP.sub("<t>", archive_b.read("word/document.xml").decode("utf-8"))
    assert body_a == body_b, "word/document.xml varies by more than the embedded timestamp"


# =============================================================================
# 6. Selection honesty
# =============================================================================


def test_the_manifest_section_state_literals_are_pinned() -> None:
    """The WIRE literals, pinned as strings.

    Every other test in this file compares against the constants, so renaming
    ``SECTION_STATE_EXCLUDED``'s VALUE kept all 50 green while silently
    breaking every external consumer that reads the manifest — the /report UI
    (R-5b) and anything parsing ``manifest.json`` out of the artifact zip. A
    constant's name is internal; its value is a published contract."""
    assert typeset.SECTION_STATE_EXCLUDED == "excluded_by_selection"
    assert typeset.SECTION_STATE_INCLUDED == "included"
    assert typeset.TYPESET_MANIFEST_KIND == "prism.report_typeset_manifest.v1"
    assert typeset.UNBOUND_SECTION_ID == "unbound_figures"
    assert typeset.FIGURE_KIND_TABLE == "table"
    assert typeset.FIGURE_KIND_GRAPHIC == "graphic"
    assert typeset.ARTIFACT_MANIFEST_NAME == "manifest.json"
    assert typeset.ARTIFACT_SVG_DIR == "svg"


def test_the_manifest_states_reach_the_wire_as_those_literals(fixed_figures: None) -> None:
    """...and the pinned literals are what a consumer actually receives, read
    out of the artifact zip's manifest.json rather than off the constants."""
    content, _manifest = typeset.compose_artifact_zip(selection={"sections": ["statistical_comparison"]})
    stored = json.loads(
        zipfile.ZipFile(io.BytesIO(content)).read(typeset.ARTIFACT_MANIFEST_NAME).decode("utf-8")
    )
    states = {entry["id"]: entry["state"] for entry in stored["sections"]}
    assert states["statistical_comparison"] == "included"
    assert states["validation"] == "excluded_by_selection"


def test_an_excluded_section_is_absent_and_the_manifest_says_why(fixed_figures: None) -> None:
    """Excluded-by-selection is a DIFFERENT state from an evidence absence: the
    heading really is gone (the author asked for that), and both the manifest
    and the document's front matter name it as excluded."""
    content, manifest = typeset.compose_report(selection={"sections": ["statistical_comparison"]})

    headings = [text for _style, text in _headings(content)]
    assert not any("검증 (Validation)" in text for text in headings)
    assert any("모델 성능과 실측의 통계 비교" in text for text in headings)

    states = {entry["id"]: entry["state"] for entry in manifest["sections"]}
    assert states["statistical_comparison"] == typeset.SECTION_STATE_INCLUDED
    assert states["validation"] == typeset.SECTION_STATE_EXCLUDED
    assert states["limitations"] == typeset.SECTION_STATE_EXCLUDED

    # ...and the DOCX itself does not pretend the excluded sections never
    # existed: the front matter lists them by name.
    text = _document_text(content)
    assert typeset._t("excluded_sections", "ko") in text
    assert "검증 (Validation)" in text


def test_an_excluded_section_is_a_different_manifest_state_from_an_absence(fixed_figures: None) -> None:
    _content, manifest = typeset.compose_report(selection={"sections": ["validation"]})
    excluded = next(entry for entry in manifest["sections"] if entry["id"] == "calibration")
    included = next(entry for entry in manifest["sections"] if entry["id"] == "validation")

    assert excluded["state"] == typeset.SECTION_STATE_EXCLUDED
    assert excluded["heading_number"] is None
    assert excluded["named_absences"] == []
    assert included["state"] == typeset.SECTION_STATE_INCLUDED
    assert included["named_absences"], "an included section with no figures still names its absence"


def test_the_section_order_is_the_spines_own(fixed_figures: None) -> None:
    """R-1 pins the section order to the GUIDELINE's order and states why. The
    document must carry that order, not a house preference — a reordered report
    silently re-argues which requirement comes first."""
    content, manifest = typeset.compose_report()

    level_one = [
        text for style, text in _headings(content) if style == "Heading 1"
    ]
    expected = [section.title_ko for section in spine.build_spine()]
    # ...plus the appendix, which is always last (it is not Main Roads').
    assert [text.split(" ", 1)[1] for text in level_one][: len(expected)] == expected

    included = [entry["id"] for entry in manifest["sections"] if entry["state"] == typeset.SECTION_STATE_INCLUDED]
    assert included[: len(expected)] == [section.id for section in spine.build_spine()]


def test_selection_never_reorders_the_spine(fixed_figures: None) -> None:
    """The section order is Main Roads', and R-1 pins it. A caller's list order
    is read as a SET, not as a running order."""
    forward, _m = typeset.compose_report(selection={"sections": ["calibration", "validation"]})
    reversed_, _m2 = typeset.compose_report(selection={"sections": ["validation", "calibration"]})
    assert _headings(forward) == _headings(reversed_)


def test_an_unknown_section_id_is_a_named_refusal(fixed_figures: None) -> None:
    with pytest.raises(typeset.TypesetRefused) as excinfo:
        typeset.compose_report(selection={"sections": ["no_such_section"]})
    assert excinfo.value.code == "typeset_unknown_section"
    assert excinfo.value.status == 422
    assert set(excinfo.value.messages) == {"ko", "en", "ja"}
    payload = excinfo.value.to_dict()
    assert payload["reason_code"] == "typeset_unknown_section"
    assert payload["unknown"] == ["no_such_section"]


def test_an_empty_selection_is_a_named_refusal(fixed_figures: None) -> None:
    with pytest.raises(typeset.TypesetRefused) as excinfo:
        typeset.compose_report(selection={"sections": []})
    assert excinfo.value.code == "typeset_empty_selection"


def test_an_unsupported_language_is_a_named_refusal(fixed_figures: None) -> None:
    with pytest.raises(typeset.TypesetRefused) as excinfo:
        typeset.compose_report(language="fr")
    assert excinfo.value.code == "typeset_unsupported_language"


#: The typesetter's OWN labels (as opposed to the payloads' trilingual
#: fields). Distinctive enough that the ko form appearing in an en/ja document
#: can only mean the language argument was ignored.
_OWN_LABEL_KEYS = ("basis", "figures", "source", "no_evidence", "named_absence")


@pytest.mark.parametrize("language", ["ko", "en", "ja"])
def test_each_language_renders_its_own_strings(fixed_figures: None, language: str) -> None:
    content, manifest = typeset.compose_report(language=language)
    text = _document_text(content)
    assert manifest["language"] == language

    # ...the PAYLOADS' trilingual fields,
    assert _table_figure()["title"][language] in text
    assert _absent_table_figure()["reason"][language] in text
    # ...and this module's OWN label table, which is a separate mechanism and
    # would otherwise go unchecked (a `_t` that always returned ko passed
    # every other assertion in this file).
    for key in _OWN_LABEL_KEYS:
        assert typeset._t(key, language) in text, key
    if language != "ko":
        for key in _OWN_LABEL_KEYS:
            assert typeset._t(key, "ko") not in text, key

    # Row VALUES are never translated (R-2's rule) — the same cell text in all
    # three languages.
    assert "3.21" in text


# =============================================================================
# 7. The fence: no legacy data reader may be smuggled in
# =============================================================================


#: Every module inside this unit's fence. All three are scanned, not just
#: typeset.py: a helper module is exactly where a smuggled reader would sit.
FENCED_MODULES = (typeset, style_apply, default_style)

#: Substrings that may not appear ANYWHERE in the executable code of a fenced
#: module — not as an import statement, not as a string handed to
#: ``importlib``, not as an attribute path.
FENCED_TOKENS = ("report_exports", "report_builder", "flow_geh_verdict", "los_summary_rows")


def _executable_source(path: Path) -> str:
    """``path``'s code with every DOCSTRING and every comment removed.

    ``ast.unparse`` drops comments outright, and this strips the module, class
    and function docstrings, so what remains is exactly what RUNS — string
    LITERALS included. That last part is the whole point of parsing instead of
    regexing: a lazy ``importlib.import_module("services.report_exports")`` is
    an ordinary spelling, not obfuscation, and it is a STRING — a
    ``^import``/``^from`` regex sees nothing while the fenced module runs. That
    exact evasion was demonstrated against this guard's first version.

    Docstrings are excluded because this unit's modules DISCUSS the legacy
    exporters at length (naming what they must not read is half the doctrine);
    prose is not execution. Evasion by string concatenation stays out of
    scope — the recorded boundary is deliberate spelling, not adversarial
    obfuscation by the module's own author.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = list(node.body)
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


@pytest.mark.parametrize("module", FENCED_MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_no_fenced_module_can_reach_the_legacy_exporters(module: Any) -> None:
    """``services/report_exports.py`` builds its Flow/GEH table from
    ``flow_geh_verdict()`` — the STALE workbook GEH path R-2's docstring
    excludes by name. One node must never print two GEH numbers, so no module
    in this unit may name it, import it, or import it by string."""
    code = _executable_source(Path(module.__file__))
    for token in FENCED_TOKENS:
        assert token not in code, f"{module.__name__} reaches the fenced-off {token!r}"


def test_the_fence_scan_sees_a_string_import_not_just_an_import_statement(tmp_path: Path) -> None:
    """The guard's own guard. A scanner that only catches ``import x`` gives
    false confidence, so this proves the scan reads string literals too — and
    still excludes prose — against a synthetic module in a tmp dir rather than
    by mutating a real one."""
    probe = tmp_path / "fence_probe.py"
    probe.write_text(
        'import importlib\n'
        '\n'
        '\n'
        'def go():\n'
        '    """A docstring naming report_exports harmlessly."""\n'
        '    return importlib.import_module("services.report_exports")\n',
        encoding="utf-8",
    )
    code = _executable_source(probe)
    assert "report_exports" in code, "a string-spelled import must be visible to the scan"
    assert "harmlessly" not in code, "docstrings must be excluded from the scan"


# =============================================================================
# 8. The default profile, and the applied field set
# =============================================================================


def test_the_prism_default_profile_has_no_holes_of_its_own() -> None:
    """A default with a ``None`` leaf would turn one named gap into a silent
    one — the fallback would fall into nothing."""
    leaves = _leaves({k: v for k, v in default_style.PRISM_DEFAULT_PROFILE.items() if k != "not_extracted"})
    empty = {path: value for path, value in leaves.items() if value is None}
    assert empty == {}
    assert default_style.PRISM_DEFAULT_PROFILE["not_extracted"] == []


def test_every_applied_field_has_a_default(fixed_figures: None) -> None:
    """The resolver raises rather than guess when a field has no default. This
    pins the whole APPLIED_FIELDS list against that."""
    document = docx.Document()
    applied = style_apply.apply_profile(document, None)
    missing = [path for path in style_apply.APPLIED_FIELDS if path not in applied.resolved]
    assert missing == []


def _schema_leaves(profile: dict[str, Any]) -> set[str]:
    """A profile's leaf paths, with the ``header.logo`` sub-tree collapsed to
    one leaf.

    That collapse is the single deliberate exception, and it is structural
    rather than convenient: the logo is IMAGE BYTES, not a value. R-4 reports
    sha256, content type and pixel/display size for a logo it FOUND — seven
    leaves — while a profile that ships no logo has exactly one thing to say
    (``present: False``). Comparing the sub-tree leaf-for-leaf would compare
    "did this document have a logo" instead of "do the schemas agree".
    """
    leaves = set(_leaves({k: v for k, v in profile.items() if k != "not_extracted"}))
    return {"header.logo" if path.startswith("header.logo") else path for path in leaves}


def test_the_schema_the_default_and_the_field_sets_all_agree_exactly(fixed_figures: None) -> None:
    """THREE-WAY SET DISCIPLINE, both directions on every edge.

    Measured failure this closes: a dead ``palette.watermark`` leaf added to
    PRISM_DEFAULT_PROFILE was invisible to the entire suite — nothing read it,
    nothing extracted it, nothing complained. A subset check in one direction
    cannot see a field that exists and does nothing.

    * default profile leaves == extractor schema leaves (BOTH ways): a leaf R-4
      can extract but the default cannot supply is a field with no fallback; a
      leaf the default carries but R-4 never emits is dead weight.
    * every leaf is consumed by exactly one of APPLIED_FIELDS (written),
      DERIVED_LEAVES (follows structurally) — or, additionally, excused from
      the VALUE oracle by ROUNDTRIP_NOT_CLAIMED.

    The schema side is MEASURED from a real extraction of this build's own
    synthetic-profile output, never typed, so it cannot drift from R-4.
    """
    content, _manifest = _compose_synthetic()
    extracted = style_profile.public_profile(
        style_profile.extract_style_profile(content, source_file="composed.docx")
    )
    schema = _schema_leaves(extracted)
    default = _schema_leaves(default_style.PRISM_DEFAULT_PROFILE)

    assert default - schema == set(), f"the default profile carries leaves R-4 never emits: {default - schema}"
    assert schema - default == set(), f"R-4 emits leaves the default cannot supply: {schema - default}"

    applied = set(style_apply.APPLIED_FIELDS)
    derived = set(typeset.DERIVED_LEAVES)
    consumed = applied | derived | set(typeset.ROUNDTRIP_NOT_CLAIMED)
    assert schema - consumed == set(), f"leaves nothing consumes: {schema - consumed}"
    assert consumed - schema == set(), f"fields claimed over leaves that do not exist: {consumed - schema}"

    # A field cannot be both written and structurally derived — that would make
    # "who wrote this value" unanswerable. (APPLIED_FIELDS and
    # ROUNDTRIP_NOT_CLAIMED DO overlap on purpose: the heading-number leaves
    # are READ by the applier for their trailing-dot shape while their VALUE is
    # not claimed by the oracle.)
    assert applied.isdisjoint(derived)
    assert set(typeset.ROUNDTRIP_NOT_CLAIMED) <= applied


# =============================================================================
# 9. HTTP
# =============================================================================


@pytest.fixture()
def client() -> Any:
    with TestClient(main.app) as test_client:
        yield test_client


def test_compose_route_returns_a_real_docx(client: Any, fixed_figures: None) -> None:
    response = client.post("/api/report/compose", json={})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert "attachment" in response.headers["content-disposition"]
    document = _open(response.content)
    assert document.paragraphs


def test_compose_artifacts_route_returns_a_valid_zip(client: Any, fixed_figures: None) -> None:
    response = client.post("/api/report/compose/artifacts", json={})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.testzip() is None
    assert typeset.ARTIFACT_MANIFEST_NAME in archive.namelist()


def test_compose_route_is_open_like_the_other_report_reads(client: Any, fixed_figures: None) -> None:
    """No auth dependency: composing writes no durable, named record. See
    ``routers/report.py``'s module docstring for the measured precedent."""
    response = client.post("/api/report/compose", json={})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "body,expected_code",
    [
        ({"selection": {"sections": ["nope"]}}, "typeset_unknown_section"),
        ({"selection": {"sections": []}}, "typeset_empty_selection"),
        ({"language": "fr"}, "typeset_unsupported_language"),
    ],
)
def test_hostile_bodies_are_named_4xx_never_500(
    client: Any, fixed_figures: None, body: dict[str, Any], expected_code: str
) -> None:
    response = client.post("/api/report/compose", json=body)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["ok"] is False
    assert detail["reason_code"] == expected_code
    assert set(detail["message"]) == {"ko", "en", "ja"}


def test_an_unknown_style_profile_id_is_a_404_not_a_500(client: Any, fixed_figures: None) -> None:
    response = client.post("/api/report/compose", json={"style_profile_id": "deadbeef"})
    assert response.status_code == 404
    assert response.json()["detail"]["reason_code"] == "style_profile_not_found"


def test_an_unknown_body_key_is_a_422_at_the_door(client: Any, fixed_figures: None) -> None:
    """``extra="forbid"``: a misspelled key must not silently do nothing."""
    response = client.post("/api/report/compose", json={"sections": ["calibration"]})
    assert response.status_code == 422


def test_a_stored_profile_drives_the_composed_document(client: Any, fixed_figures: None) -> None:
    """End to end over HTTP: upload a styled DOCX to R-4's store, compose with
    the id it returns, and read the palette back out of the composed bytes."""
    _skip_without_sample()
    from conftest import bearer_headers

    upload = client.post(
        "/api/report/style-profiles?filename=sample.docx",
        content=SAMPLE_DOCX.read_bytes(),
        headers=bearer_headers("report-typeset-uploader", "modeler"),
    )
    assert upload.status_code == 201, upload.text
    profile_id = upload.json()["id"]

    response = client.post("/api/report/compose", json={"style_profile_id": profile_id})
    assert response.status_code == 200
    extracted = style_profile.extract_style_profile(response.content, source_file="composed.docx")
    stored = upload.json()["resolved_profile"]
    assert extracted["palette"]["title"] == stored["palette"]["title"]
    assert extracted["header"]["logo"]["sha256"] == stored["header"]["logo"]["sha256"]


# =============================================================================
# 10. FONTS — one body family, one heading family, every run
#
# The user-reported defect (a Word-for-Mac screenshot: "the text font are not
# the same throughout the report"): MEASURED by unzipping a composed .docx's
# word/document.xml + word/styles.xml. R-4's own extractor never catches this
# class of bug -- it reads font FAMILY off style DEFINITIONS (Normal, Heading
# N), never off actual runs, so a leak in a run that bypasses its style never
# reaches it. This section IS that missing coverage: a walker that resolves
# every Hangul-bearing run's EFFECTIVE eastAsia font the way OOXML itself
# does (run rPr -> paragraph style, up its w:basedOn chain -> docDefaults),
# over the REAL composed bytes.
# =============================================================================

_HANGUL_RE = re.compile(r"[가-힣]")

#: The paragraph styles this typesetter's headings use -- everything else
#: (Normal, Caption, Header, Footer, a table cell's default paragraph) is
#: body-class. There is no third font family in the schema.
_HEADING_STYLE_NAMES = {"Title", "Heading 1", "Heading 2", "Heading 3"}


#: A theme-shadowed literal resolves to this sentinel (``"theme:<theme
#: name>"``) rather than to the literal ``w:eastAsia`` value -- see
#: :func:`_effective_rfonts_east_asia`. Exported (no leading underscore) so a
#: caller can name the exact shape without re-deriving the ``"theme:"``
#: prefix.
THEME_SENTINEL_PREFIX = "theme:"


def _effective_rfonts_east_asia(rfonts: Any) -> str | None:
    """The eastAsia font a real OOXML consumer resolves off ONE ``w:rFonts``
    element, modeling Word's REAL precedence rather than the naive "read the
    literal attribute" a first-pass walker would do.

    MEASURED (unit #63b, unzipping a composed .docx's ``word/styles.xml`` and
    dissecting the Title style's ``rPr``): ``w:eastAsiaTheme`` -- when
    present -- wins over its literal ``w:eastAsia`` sibling in Word's own
    rendering (OOXML §17.3.2.26), the opposite of what an earlier version of
    ``style_apply``'s own docstrings claimed. A walker that read only the
    literal (this function's pre-#63b shape) would report the CORRECT font
    for a style whose theme attribute silently overrode it -- passing on
    exactly the defect the user reported. Modeling the override here means
    the pre-fix state (theme left beside the literal) resolves to a
    ``"theme:<theme-name>"`` sentinel that can never equal a real font-family
    string, so a test comparing against the profile's actual family FAILS on
    unfixed output instead of passing by accident.
    """
    theme = rfonts.get(qn("w:eastAsiaTheme"))
    if theme:
        return f"{THEME_SENTINEL_PREFIX}{theme}"
    return rfonts.get(qn("w:eastAsia"))


def _style_own_east_asia(style: Any) -> str | None:
    """ONE style's OWN effective eastAsia (theme-aware) -- NOT its basedOn
    chain."""
    rpr = style.element.find(qn("w:rPr"))
    if rpr is None:
        return None
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        return None
    return _effective_rfonts_east_asia(rfonts)


def _style_chain_east_asia(style: Any) -> str | None:
    """Walk UP ``w:basedOn`` until a style states its own eastAsia, or the
    chain ends. ``id()`` (not style name) breaks any pathological cycle."""
    seen: set[int] = set()
    current = style
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        value = _style_own_east_asia(current)
        if value:
            return value
        current = current.base_style
    return None


def _doc_defaults_east_asia(document: Any) -> str | None:
    """The LAST resort in OOXML's resolution order -- see
    ``style_apply._set_doc_defaults_fonts``'s docstring for why this module
    floors it on the body family rather than leaving python-docx's
    theme-only default (Calibri-class, no dependable Korean member) in
    place."""
    styles_element = document.styles.element
    doc_defaults = styles_element.find(qn("w:docDefaults"))
    if doc_defaults is None:
        return None
    rpr_default = doc_defaults.find(qn("w:rPrDefault"))
    if rpr_default is None:
        return None
    rpr = rpr_default.find(qn("w:rPr"))
    if rpr is None:
        return None
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        return None
    return _effective_rfonts_east_asia(rfonts)


def effective_east_asia_font(document: Any, run: Any, paragraph: Any) -> tuple[str | None, str]:
    """The font a real OOXML consumer would use to render ``run``'s East
    Asian text, and WHERE it came from (``"run-direct"``, ``"style-chain"``
    or ``"docDefaults"``) -- the source lets a caller assert not just the
    right family but that the specific leak sites (table cells, header,
    footer) carry it DIRECTLY rather than depending on inheritance alone.

    Resolution order: the run's own ``rPr/rFonts`` first (the run is the
    highest-priority formatting level in OOXML); then the paragraph's style,
    walked up its ``w:basedOn`` chain; then ``w:docDefaults`` as the final
    floor. No character-style (``w:rStyle``) step -- nothing this codebase
    writes ever applies one to a run.

    Theme-aware at every step (:func:`_effective_rfonts_east_asia`): a slot
    shadowed by ``w:eastAsiaTheme`` resolves to the ``"theme:..."`` sentinel
    rather than the literal beside it, so this walker models Word's actual
    precedence instead of the naive "a literal attribute is present, so it
    must be what renders" assumption the pre-#63b walker made.

    Exported (no leading underscore) so a future report unit can reuse this
    exact walker rather than re-measuring the same resolution order.
    """
    rpr = run._r.find(qn("w:rPr"))
    if rpr is not None:
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is not None:
            direct = _effective_rfonts_east_asia(rfonts)
            if direct:
                return direct, "run-direct"
    style = paragraph.style
    from_style = _style_chain_east_asia(style) if style is not None else None
    if from_style:
        return from_style, "style-chain"
    return _doc_defaults_east_asia(document), "docDefaults"


def _walk_hangul_runs(document: Any, paragraphs: Any, where: str) -> list[tuple[str, str | None, str, str | None, str]]:
    """``(where, paragraph_style_name, run_text, resolved_east_asia, source)``
    for every run in ``paragraphs`` whose text contains at least one Hangul
    syllable."""
    found: list[tuple[str, str | None, str, str | None, str]] = []
    for paragraph in paragraphs:
        style_name = paragraph.style.name if paragraph.style is not None else None
        for run in paragraph.runs:
            if _HANGUL_RE.search(run.text or ""):
                resolved, source = effective_east_asia_font(document, run, paragraph)
                found.append((where, style_name, run.text, resolved, source))
    return found


def _every_hangul_run(content: bytes) -> list[tuple[str, str | None, str, str | None, str]]:
    """Every Hangul-bearing run in the WHOLE composed document: body
    paragraphs, every table cell, the header, the footer."""
    document = docx.Document(io.BytesIO(content))
    runs = _walk_hangul_runs(document, document.paragraphs, "body")
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                runs.extend(_walk_hangul_runs(document, cell.paragraphs, "table-cell"))
    section = document.sections[0]
    runs.extend(_walk_hangul_runs(document, section.header.paragraphs, "header"))
    runs.extend(_walk_hangul_runs(document, section.footer.paragraphs, "footer"))
    return runs


#: A DISTINCTIVE synthetic profile for the font walker: body and heading
#: families both mix a Latin ascii face with a DIFFERENT-per-class Korean
#: face (real mixed-script rendering, which is correct and not this unit's
#: defect), and the two eastAsia values are deliberately different from each
#: other AND from every value SYNTHETIC_PROFILE above uses, so a leaked
#: Calibri/None/wrong-class value cannot coincidentally match by reusing a
#: fixture constant.
FONT_WALKER_PROFILE: dict[str, Any] = copy.deepcopy(SYNTHETIC_PROFILE)
FONT_WALKER_PROFILE["fonts"]["body_family"] = {"ascii": "Times New Roman", "eastAsia": "궁서"}
FONT_WALKER_PROFILE["fonts"]["heading_family"] = {"ascii": "Georgia", "eastAsia": "돋움"}
# SYNTHETIC_PROFILE's own header/footer text is plain ASCII ("Synthetic
# header text" / "Page N") -- neither exercises the Hangul walker at the
# header/footer leak sites this unit measured, so both are overridden with
# Korean text here specifically to reach them.
FONT_WALKER_PROFILE["header"]["text"] = "머리글 예시"
FONT_WALKER_PROFILE["footer"]["page_number"]["format"] = "쪽 N"


def test_every_hangul_run_resolves_to_its_class_font_family(fixed_figures: None) -> None:
    """THE regression net for defect #2. Composes with
    :data:`FONT_WALKER_PROFILE` and walks EVERY Hangul run in the real
    output bytes: body-class paragraphs (Normal/Caption), table cells, the
    header and the footer must all resolve to the profile's BODY family;
    Title/Heading 1-3 must resolve to the HEADING family. None may resolve to
    ``None`` (docDefaults with no literal font -- the pre-fix state) or to
    anything but those two exact values.

    Also pins WHERE each of the three measured leak sites resolves its font
    FROM: table cells, the header and the footer must show ``"run-direct"``
    -- proving the explicit ``style_apply.set_run_fonts`` write actually
    fired, not merely that inheritance happens to still cover for its
    absence. Body/heading paragraphs are expected at ``"style-chain"``
    (they are directly styled with Normal/Caption/Title/Heading N, which
    already carry the right rFonts -- no leak was measured there).
    """
    content, _manifest = typeset.compose_report(style_profile=FONT_WALKER_PROFILE, profile_name="폰트 워커")
    runs = _every_hangul_run(content)

    body_east = FONT_WALKER_PROFILE["fonts"]["body_family"]["eastAsia"]
    heading_east = FONT_WALKER_PROFILE["fonts"]["heading_family"]["eastAsia"]

    seen_where: set[str] = set()
    seen_class: set[str] = set()
    for where, style_name, text, resolved, source in runs:
        seen_where.add(where)
        is_heading = style_name in _HEADING_STYLE_NAMES
        seen_class.add("heading" if is_heading else "body")
        expected = heading_east if is_heading else body_east
        assert resolved == expected, (
            f"{where} run under style {style_name!r} ({text!r}) resolved eastAsia to "
            f"{resolved!r} via {source} -- expected the profile's "
            f"{'heading' if is_heading else 'body'} family {expected!r}"
        )
        if where in ("table-cell", "header", "footer"):
            assert source == "run-direct", (
                f"{where} run ({text!r}) resolved via {source}, not a direct rFonts write on "
                "the run itself -- the measured leak site's explicit-font write appears to be "
                "missing even though it still resolves correctly by inheritance"
            )

    # A vacuous walk (zero runs found, or one class/location never visited)
    # would make every assertion above trivially true and prove nothing --
    # the exact failure mode test_report_typeset.py's own section 0 already
    # guards ROUNDTRIP_NOT_CLAIMED / DERIVED_LEAVES against.
    assert seen_where >= {"body", "table-cell", "header", "footer"}, seen_where
    assert seen_class == {"body", "heading"}, seen_class
    assert len(runs) >= 10, "too few Hangul runs walked for this to be a meaningful regression net"


def test_doc_defaults_are_floored_to_the_profiles_body_family() -> None:
    """Narrower and cheaper than the walk above: ``w:docDefaults`` itself,
    directly off ``style_apply.apply_profile``'s output, regardless of
    whether any run in THIS build's spine actually reaches it. See
    ``style_apply._set_doc_defaults_fonts``'s docstring -- today every
    paragraph style in this typesetter's output resolves through Normal
    before reaching docDefaults, so this is a floor against a FUTURE style
    that does not, not a claim that current output depends on it."""
    document = docx.Document()
    style_apply.apply_profile(document, FONT_WALKER_PROFILE)

    styles_element = document.styles.element
    doc_defaults = styles_element.find(qn("w:docDefaults"))
    assert doc_defaults is not None
    rfonts = doc_defaults.find(qn("w:rPrDefault")).find(qn("w:rPr")).find(qn("w:rFonts"))
    assert rfonts.get(qn("w:ascii")) == "Times New Roman"
    assert rfonts.get(qn("w:hAnsi")) == "Times New Roman"
    assert rfonts.get(qn("w:eastAsia")) == "궁서"
    # MEASURED (unit #63b): a THEME attribute wins over its literal sibling
    # in Word's own rendering when both are present on one w:rFonts (OOXML
    # §17.3.2.26) -- the opposite of what an earlier version of this test and
    # style_apply's own docstrings claimed. python-docx's template seeds this
    # exact element with w:asciiTheme="minorHAnsi"/w:eastAsiaTheme=
    # "minorEastAsia"; style_apply._set_doc_defaults_fonts now strips all
    # four *Theme attributes (style_apply._strip_theme_fonts) so the literal
    # fonts just asserted above actually govern instead of losing silently.
    assert rfonts.get(qn("w:asciiTheme")) is None
    assert rfonts.get(qn("w:hAnsiTheme")) is None
    assert rfonts.get(qn("w:eastAsiaTheme")) is None
    assert rfonts.get(qn("w:cstheme")) is None


# =============================================================================
# 10a2. THEME-ATTRIBUTE PRECEDENCE -- unit #63b
#
# The user-reported defect this unit fixes: the composed report's TITLE
# renders sans ("arial 느낌") while the body renders serif. MEASURED (by
# unzipping a composed .docx's word/styles.xml and dissecting the Title
# style's rPr): python-docx's default template seeds Title/Heading 1-3/
# Caption's rPr AND docDefaults' rPrDefault with *Theme attributes
# (w:asciiTheme, w:hAnsiTheme, w:eastAsiaTheme, w:cstheme); Normal carries
# none. Word gives a THEME attribute precedence over its literal sibling
# when both sit on one w:rFonts (OOXML §17.3.2.26) -- the opposite of what
# an earlier version of style_apply's own docstrings (and section 10a's test
# above, before this unit) claimed. So Normal's literal always won (no theme
# attribute to lose to) and Title's always lost (all four present) -- one
# style rendering correctly and the other not, exactly the asymmetry the
# user reported. style_apply._strip_theme_fonts (called from every literal
# font write site) is the fix; these two tests are its regression net.
# =============================================================================

_RFONTS_THEME_LITERAL_SLOT_PAIRS: tuple[tuple[str, str], ...] = (
    ("ascii", "asciiTheme"),
    ("hAnsi", "hAnsiTheme"),
    ("eastAsia", "eastAsiaTheme"),
    ("cs", "cstheme"),
)


def test_no_rfonts_element_carries_both_a_literal_and_a_theme_font_for_one_slot(
    fixed_figures: None,
) -> None:
    """The class-closing net, not a Title-only spot check: scan EVERY
    ``w:rFonts`` element in the composed archive's document/styles/header/
    footer parts and fail if any ONE of them carries both a literal font
    attribute (``w:ascii``, ``w:hAnsi``, ``w:eastAsia``, ``w:cs``) and its
    THEME sibling (``w:asciiTheme``, ``w:hAnsiTheme``, ``w:eastAsiaTheme``,
    ``w:cstheme``) for the SAME slot -- the exact shape MEASURED as this
    unit's defect.

    Scanning the whole archive rather than pinning the four sites this unit
    happened to measure (Title/Heading 1-3/Caption/docDefaults) means a
    FUTURE style python-docx seeds with theme attributes -- one this unit
    never saw -- is caught here too, without anyone writing it a dedicated
    test.
    """
    content, _manifest = typeset.compose_report(
        style_profile=FONT_WALKER_PROFILE, profile_name="테마 속성 전수 검사"
    )

    archive = zipfile.ZipFile(io.BytesIO(content))
    part_names = ("word/document.xml", "word/styles.xml", "word/header1.xml", "word/footer1.xml")
    violations: list[tuple[str, str, str]] = []
    checked_parts = 0
    total_rfonts = 0
    for name in part_names:
        assert name in archive.namelist(), f"expected part {name} in the composed archive"
        checked_parts += 1
        root = parse_xml(archive.read(name))
        for rfonts in root.iter(qn("w:rFonts")):
            total_rfonts += 1
            for literal_attr, theme_attr in _RFONTS_THEME_LITERAL_SLOT_PAIRS:
                literal_value = rfonts.get(qn(f"w:{literal_attr}"))
                theme_value = rfonts.get(qn(f"w:{theme_attr}"))
                if literal_value and theme_value:
                    violations.append((name, literal_attr, theme_attr))

    assert checked_parts == len(part_names), f"expected all {len(part_names)} parts, checked {checked_parts}"
    assert total_rfonts >= 10, "too few w:rFonts elements scanned for this to be a meaningful net"
    assert not violations, (
        "rFonts element(s) carry BOTH a literal font and its theme sibling for the same slot -- "
        f"the theme silently wins in Word's own rendering, overriding the literal: {violations}"
    )


def test_title_paragraph_resolves_the_heading_family_not_a_shadowed_theme(fixed_figures: None) -> None:
    """THE user-reported defect, pinned directly on the Title paragraph
    rather than swept in with every other Hangul run: the front matter's
    Title run must resolve, through the theme-aware
    :func:`effective_east_asia_font` walker, to the profile's HEADING
    eastAsia family -- never to a ``"theme:..."`` sentinel, which is what
    the walker reports when a theme attribute still shadows the literal (see
    :data:`THEME_SENTINEL_PREFIX`). The sentinel can never equal a real
    font-family string, so this assertion fails outright on unfixed output
    instead of silently passing by resolving to python-docx's Calibri-class
    theme default.
    """
    content, _manifest = typeset.compose_report(
        style_profile=FONT_WALKER_PROFILE, profile_name="타이틀 폰트 검사"
    )
    document = _open(content)

    title_paragraphs = [
        paragraph
        for paragraph in document.paragraphs
        if paragraph.style is not None and paragraph.style.name == "Title"
    ]
    assert title_paragraphs, "no Title-styled paragraph found in the composed document"
    title_paragraph = title_paragraphs[0]
    runs = [run for run in title_paragraph.runs if (run.text or "").strip()]
    assert runs, "the Title paragraph carries no non-empty run to resolve a font from"

    heading_east = FONT_WALKER_PROFILE["fonts"]["heading_family"]["eastAsia"]
    for run in runs:
        resolved, source = effective_east_asia_font(document, run, title_paragraph)
        assert not str(resolved or "").startswith(THEME_SENTINEL_PREFIX), (
            f"Title run {run.text!r} resolved to {resolved!r} via {source} -- a theme attribute is "
            "still shadowing the literal heading font"
        )
        assert resolved == heading_east, (
            f"Title run {run.text!r} resolved eastAsia to {resolved!r} via {source} -- expected the "
            f"profile's heading family {heading_east!r}"
        )


# =============================================================================
# 10b. NFD FONT-FAMILY STRINGS -- one layer deeper than display text
#
# The refuter's follow-on to section 10: font-FAMILY strings themselves
# (fonts.body_family.*/fonts.heading_family.*) flow through _Resolver.get
# into every site section 10 just fixed (Normal/Heading/Caption styles,
# docDefaults, header, footer, table cells) with NO normalization. This is
# NOT the same class of harm as section 11's display-name defect: a family
# string is a w:rFonts ATTRIBUTE VALUE Word matches against installed font
# names by EXACT string, so an NFD family matches NOTHING installed and Word
# substitutes a fallback for the WHOLE style -- worse than a bare-jamo label.
# =============================================================================

_JAMO_RANGES = ((0x1100, 0x11FF), (0xA960, 0xA97F), (0xD7B0, 0xD7FF))


def _has_conjoining_jamo(text: str) -> bool:
    return any(any(lo <= ord(ch) <= hi for lo, hi in _JAMO_RANGES) for ch in text)


NFD_BODY_EAST_ASIA = unicodedata.normalize("NFD", "나눈고딕")
NFD_HEADING_EAST_ASIA = unicodedata.normalize("NFD", "본고딕")

#: FONT_WALKER_PROFILE, but the two eastAsia family strings are NFD --
#: everything else (ascii faces, palette, page geometry) stays the same so
#: this profile exercises the identical code paths section 10's test does.
NFD_FONT_FAMILY_PROFILE: dict[str, Any] = copy.deepcopy(FONT_WALKER_PROFILE)
NFD_FONT_FAMILY_PROFILE["fonts"]["body_family"] = {"ascii": "Times New Roman", "eastAsia": NFD_BODY_EAST_ASIA}
NFD_FONT_FAMILY_PROFILE["fonts"]["heading_family"] = {"ascii": "Georgia", "eastAsia": NFD_HEADING_EAST_ASIA}


def test_compose_normalizes_nfd_font_family_strings_at_every_leak_site(fixed_figures: None) -> None:
    """End to end: compose with :data:`NFD_FONT_FAMILY_PROFILE` (an NFD
    ``fonts.body_family.eastAsia``/``fonts.heading_family.eastAsia``, as if
    read from a client DOCX authored on a Mac) and prove the healing at
    BOTH consequences the refuter named:

    (a) no conjoining-jamo codepoint survives into ANY XML part that carries
        an rFonts attribute -- word/document.xml, word/styles.xml,
        word/header1.xml, word/footer1.xml;
    (b) the NFC family is written, as the literal w:eastAsia attribute
        value, at each of section 10's three measured leak sites (table
        cells and header/footer are body-class -- see word/document.xml's
        table and word/header1.xml/word/footer1.xml) as well as in
        word/styles.xml (Normal/Caption get body, Title/Heading N get
        heading);
    (c) the manifest's ``style.resolved`` reports the NFC form, never the
        raw NFD the profile carried.

    Also re-runs the section-10 walker over THIS document: every Hangul run
    must resolve to the NFC family, proving the healing happens before
    apply-time, not just at the raw-string level.
    """
    assert _has_conjoining_jamo(NFD_BODY_EAST_ASIA) and _has_conjoining_jamo(NFD_HEADING_EAST_ASIA), (
        "fixture must actually be decomposed, or this test proves nothing"
    )

    content, manifest = typeset.compose_report(
        style_profile=NFD_FONT_FAMILY_PROFILE, profile_name="NFD 글꼴 테스트"
    )

    nfc_body = unicodedata.normalize("NFC", NFD_BODY_EAST_ASIA)
    nfc_heading = unicodedata.normalize("NFC", NFD_HEADING_EAST_ASIA)
    assert nfc_body != NFD_BODY_EAST_ASIA and nfc_heading != NFD_HEADING_EAST_ASIA

    # (c) manifest resolved values are NFC.
    resolved = manifest["style"]["resolved"]
    assert resolved["fonts.body_family.eastAsia"] == nfc_body
    assert resolved["fonts.heading_family.eastAsia"] == nfc_heading
    assert not _has_conjoining_jamo(resolved["fonts.body_family.eastAsia"])
    assert not _has_conjoining_jamo(resolved["fonts.heading_family.eastAsia"])

    archive = zipfile.ZipFile(io.BytesIO(content))
    parts = {
        name: archive.read(name).decode("utf-8")
        for name in ("word/document.xml", "word/styles.xml", "word/header1.xml", "word/footer1.xml")
    }

    # (a) no conjoining jamo anywhere in any part that carries rFonts.
    for name, xml_text in parts.items():
        assert not _has_conjoining_jamo(xml_text), f"{name} still carries a conjoining-jamo codepoint"
        assert NFD_BODY_EAST_ASIA not in xml_text, f"{name} carries the raw NFD body family string"
        assert NFD_HEADING_EAST_ASIA not in xml_text, f"{name} carries the raw NFD heading family string"

    # (b) the NFC family lands as the literal w:eastAsia attribute value at
    # each measured leak site.
    body_attr = f'w:eastAsia="{nfc_body}"'
    heading_attr = f'w:eastAsia="{nfc_heading}"'
    assert body_attr in parts["word/document.xml"], "table cell run: NFC body family not found"
    assert body_attr in parts["word/header1.xml"], "header run: NFC body family not found"
    assert body_attr in parts["word/footer1.xml"], "footer run: NFC body family not found"
    assert body_attr in parts["word/styles.xml"], "Normal style: NFC body family not found"
    assert heading_attr in parts["word/styles.xml"], "a heading style: NFC heading family not found"

    # Re-run section 10's walker over this exact document: every Hangul run
    # must resolve to the NFC family, never the raw NFD the profile carried.
    for where, style_name, text, resolved_font, source in _every_hangul_run(content):
        is_heading = style_name in _HEADING_STYLE_NAMES
        expected = nfc_heading if is_heading else nfc_body
        assert resolved_font == expected, (
            f"{where} run under style {style_name!r} ({text!r}) resolved eastAsia to "
            f"{resolved_font!r} via {source} -- expected the NFC family {expected!r}"
        )
        assert resolved_font is None or not _has_conjoining_jamo(resolved_font)


# =============================================================================
# 10c. THE WHOLE-ARCHIVE SCAN -- closes the class, not just the instances
#
# Round 3, the refuter's third pass: header.text leaked raw NFD into
# word/header1.xml even after rounds 1-2 fixed the profile's own name and
# the font-FAMILY strings. header.text, footer.page_number.format and
# caption.prefix are all DISPLAY-TEXT leaves (style_apply._DISPLAY_TEXT_PATHS)
# -- swept alongside the font families here so ONE profile carries every
# NFD-capable leaf this unit's schema has, and ONE test scans every produced
# XML part rather than re-deriving a per-leaf-site assertion by hand. A
# FUTURE leaf that joins neither _FONT_FAMILY_PATHS nor _DISPLAY_TEXT_PATHS
# fails THIS test without anyone writing a new one for it.
# =============================================================================

NFD_HEADER_TEXT = unicodedata.normalize("NFD", "화이트라벨 머리글")
NFD_FOOTER_FORMAT = unicodedata.normalize("NFD", "쪽 N")
NFD_CAPTION_PREFIX = unicodedata.normalize("NFD", "그림 ")

#: NFD_FONT_FAMILY_PROFILE, plus NFD on every DISPLAY-TEXT leaf too -- every
#: string leaf this unit's schema can leak NFD through, in ONE profile.
WHOLE_ARCHIVE_NFD_PROFILE: dict[str, Any] = copy.deepcopy(NFD_FONT_FAMILY_PROFILE)
WHOLE_ARCHIVE_NFD_PROFILE["header"]["text"] = NFD_HEADER_TEXT
WHOLE_ARCHIVE_NFD_PROFILE["footer"]["page_number"]["format"] = NFD_FOOTER_FORMAT
WHOLE_ARCHIVE_NFD_PROFILE["caption"]["prefix"] = NFD_CAPTION_PREFIX


def test_no_conjoining_jamo_survives_anywhere_in_the_composed_archive(fixed_figures: None) -> None:
    """Compose with :data:`WHOLE_ARCHIVE_NFD_PROFILE` and scan EVERY ``.xml``
    member of the produced zip (not just document/styles/header1/footer1 --
    whatever is actually in there, including parts this test never
    specifically reasoned about) for conjoining-jamo codepoints. None may
    survive ANYWHERE, and the NFC form of every swept leaf must be found
    SOMEWHERE in the archive -- proving the healing actually happened, not
    merely that the raw NFD vanished (which a silent drop of the whole field
    would also produce).
    """
    fixture_strings = (
        NFD_BODY_EAST_ASIA,
        NFD_HEADING_EAST_ASIA,
        NFD_HEADER_TEXT,
        NFD_FOOTER_FORMAT,
        NFD_CAPTION_PREFIX,
    )
    assert all(_has_conjoining_jamo(value) for value in fixture_strings), (
        "every fixture string must actually be decomposed, or this test proves nothing"
    )

    content, _manifest = typeset.compose_report(
        style_profile=WHOLE_ARCHIVE_NFD_PROFILE, profile_name="전체 아카이브 스캔"
    )

    archive = zipfile.ZipFile(io.BytesIO(content))
    xml_members = [name for name in archive.namelist() if name.endswith(".xml")]
    # A vacuous scan (too few parts) would make the loop below prove nothing
    # -- document.xml, styles.xml, header1.xml and footer1.xml at minimum.
    assert len(xml_members) >= 4, xml_members

    parts = {name: archive.read(name).decode("utf-8") for name in xml_members}

    for name, xml_text in parts.items():
        assert not _has_conjoining_jamo(xml_text), f"{name} still carries a conjoining-jamo codepoint"

    whole_archive_text = " ".join(parts.values())
    expected_nfc = {
        unicodedata.normalize("NFC", NFD_BODY_EAST_ASIA),
        unicodedata.normalize("NFC", NFD_HEADING_EAST_ASIA),
        unicodedata.normalize("NFC", NFD_HEADER_TEXT),
        # The footer FORMAT's own NFC form still carries the literal "N"
        # placeholder -- the run actually written to the document is only
        # the text BEFORE it (see style_apply._apply_footer's prefix/suffix
        # split), so that is what must be found in the archive.
        unicodedata.normalize("NFC", NFD_FOOTER_FORMAT).split("N", 1)[0],
        unicodedata.normalize("NFC", NFD_CAPTION_PREFIX),
    }
    for expected in expected_nfc:
        assert expected in whole_archive_text, f"NFC form {expected!r} not found anywhere in the archive"


# =============================================================================
# 11. NFD -> NFC — an already-stored profile's name heals at compose time
#
# routers/report.py's upload door now NFC-normalizes a new upload (see
# test_report_style_profile.py's door tests), but a profile uploaded BEFORE
# that fix is already stored NFD. This section proves the SECOND boundary:
# compose-time normalization heals it with no re-upload.
# =============================================================================


def test_compose_normalizes_an_already_stored_nfd_profile_name(fixed_figures: None) -> None:
    """Simulates an already-stored NFD profile name (as if uploaded before
    the door fix) by passing an NFD ``profile_name`` straight into
    :func:`typeset.compose_report` -- the same parameter
    ``_compose_kwargs`` in routers/report.py fills from a STORED record's
    ``name``, never re-validated or re-normalized between storage and this
    call in production. The manifest's ``style.profile_name`` AND the
    document's own front-matter line must both come back NFC, with no
    conjoining-jamo codepoint anywhere in either.
    """
    nfd_name = unicodedata.normalize("NFD", "샘플보고서-도착스타일")
    assert any(0x1100 <= ord(ch) <= 0x11FF for ch in nfd_name), "fixture must actually be decomposed"

    content, manifest = typeset.compose_report(profile_name=nfd_name)

    nfc_name = unicodedata.normalize("NFC", nfd_name)
    assert manifest["style"]["profile_name"] == nfc_name

    document_text = _document_text(content)
    assert nfc_name in document_text
    assert nfd_name not in document_text
    for ch in document_text:
        assert not (0x1100 <= ord(ch) <= 0x11FF), f"conjoining jamo survived into document text: {ch!r}"
        assert not (0xA960 <= ord(ch) <= 0xA97F), f"conjoining jamo survived into document text: {ch!r}"
        assert not (0xD7B0 <= ord(ch) <= 0xD7FF), f"conjoining jamo survived into document text: {ch!r}"


def test_compose_route_normalizes_a_stored_nfd_profile_name_over_http(
    client: Any, fixed_figures: None
) -> None:
    """The same healing, but through the REAL store + HTTP compose route --
    an NFD name is written straight into the on-disk registry (bypassing
    the upload-door normalization on purpose, to stand in for a profile that
    was already stored before that fix existed), then composed by id."""
    from services.report import style_profile as sp

    nfd_name = unicodedata.normalize("NFD", "고객 스타일")

    # A minimal, valid DOCX -- style_profile.store_profile only needs
    # something extract_style_profile can open.
    minimal = docx.Document()
    minimal.add_paragraph("just one paragraph", style="Normal")
    buffer = io.BytesIO()
    minimal.save(buffer)

    summary = sp.store_profile(content=buffer.getvalue(), source_file="a.docx", name=nfd_name)
    # store_profile ALREADY normalizes (this unit's own door-adjacent fix in
    # _clean_name) -- overwrite the persisted record directly to reproduce a
    # profile that predates that fix, exactly like a real already-stored one.
    record_path = sp._record_path(summary["id"])
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["name"] = nfd_name
    record_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    payload = sp._read_registry_payload(sp.registry_path())
    payload["profiles"][summary["id"]]["name"] = nfd_name
    sp._write_json_atomic(sp.registry_path(), payload)

    response = client.post("/api/report/compose", json={"style_profile_id": summary["id"]})
    assert response.status_code == 200
    document_text = _document_text(response.content)
    nfc_name = unicodedata.normalize("NFC", nfd_name)
    assert nfc_name in document_text
    assert nfd_name not in document_text

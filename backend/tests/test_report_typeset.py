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
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

import docx
import pytest
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

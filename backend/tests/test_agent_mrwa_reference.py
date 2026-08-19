"""Integrity tests for services.agent.mrwa_reference — Phase 0.3.

These tests protect the ONE property that makes the reference pack
trustworthy: every number carries a real, traceable citation, and nothing
claims a page number that was never independently confirmed. They do not
re-derive the MRWA numbers themselves (this module is a transcription, not a
calculation) — see docs/product/master-roadmap-2026-07.md item 0.3 and this
unit's own drafted DECISIONS.md entry for where each number came from.
"""

from __future__ import annotations

import re

import pytest

import services.agent.mrwa_reference as ref
import services.agent.tools_edit as tools_edit

# The ONLY citations in this module allowed to carry a specific page number
# (a "p.N" / "p.B-N" pattern) — every other citation must contain the
# literal PAGE_CITE_PENDING marker instead. Keeping this list here (not in
# the module itself) means a future edit that quietly invents a new page
# number fails this test rather than passing silently.
_ALLOWED_EXACT_PAGE_CITATIONS = {
    "op p.53",
    "Appendix B, p.B-8",
    "Appendix B §B.3, p.B-9",
    "Appendix A, p.78 (as already cited by services.agent.tools_edit.py's own "
    "_MRWA_DETECTOR_SETBACK_M comment; that comment's own caveat applies here too — "
    "this codebase's local Appendix A extract runs only to ~p.49 and does not itself "
    "carry p.78, so the page is inherited, not independently re-verified)",
    "Appendix A, p.78 (same inherited-page caveat as DETECTOR_SETBACK_M — see its own citation string)",
    # §2.12.1's thirteen calibration steps. The page range is a DOCUMENT page
    # range (the source PDF's page minus 18) transcribed in
    # docs/product/handoff-2026-08-18.md §4.1(b), which read it off the client's
    # own copy of the guideline — the same class of traceability "op p.53" has.
    "op §2.12.1, p.34-42",
    # The three §2.12.1 PRINCIPLES cite their own pages, because two of them are
    # not on the step pages: "one parameter at a time" is in §2.12.1's preamble
    # and the independence rule is in §2.12's preamble, both on document p.33
    # (source footer "Page 33 of 271"), while the route-before-volume rule is
    # §2.12.1.11 on p.41. Sharing the steps' p.34-42 for all three attached two
    # sentences to a page range that does not contain them.
    "op §2.12.1, p.33",
    "op §2.12, p.33",
    "op §2.12.1.11, p.41",
    # §5.2.5.1 is the one §5 clause whose page IS independently re-verifiable
    # from material in this checkout: docs/product/reference/
    # mrwa-section5-extract.md carries the heading "5.2.5.1 General (p.187-189)"
    # and places Table 5-6 at p.187. Hence a real page here where the sibling
    # §5 tables above still say PAGE_CITE_PENDING.
    "op §5.2.5.1, p.187-188",
    # Table 5-6 itself, added by unit #44. Same evidence class as the line
    # above and re-checkable the same way: docs/product/reference/
    # mrwa-section5-extract.md prints the table under its own caption
    # "**Table 5-6: Recommended car following model parameters** (p.187)",
    # immediately below the "5.2.5.1 General (p.187-189)" heading. The page is
    # read off the extract's caption, not inferred from the section range --
    # which is why this is an exact page and Tables 5-8/5-9/5-10 (which the
    # extract carries no caption page for) still say PAGE_CITE_PENDING.
    "op §5.2.5.1, Table 5-6, p.187",
    # The merging-area lane-change/lateral bands, added by unit #51. Same
    # evidence class again: docs/product/reference/mrwa-section5-extract.md
    # prints the whole bullet list with its own "(p.188)" marker, inside the
    # "5.2.5.1 General (p.187-189)" section — so the page is read off the
    # extract, not inferred from the section range.
    "op §5.2.5.1, p.188",
}

# Citations that mention a page-ISH token (e.g. "Table B-3") but are section
# references, not page numbers -- explicitly not required to be in the exact
# allowlist above, and not flagged as "pending" either, since they never
# claimed a specific page in the first place.
_SECTION_ONLY_CITATION_SUBSTRINGS = ("§B.", "Table B-3", "§5")

_PAGE_NUMBER_PATTERN = re.compile(r"\bp\.\s*B?-?\d+")


def test_every_entry_has_a_nonempty_citation():
    for table_name, entry in ref.all_entries():
        assert entry.citation, f"{table_name}: entry with context={entry.context!r} has no citation"


def test_no_invented_page_numbers():
    """Any citation that names a specific page (a 'p.N'/'p.B-N' pattern)
    must be one of the independently-traceable citations we actually
    verified against source material — everything else must say
    PAGE_CITE_PENDING literally, never a guessed page."""
    for table_name, entry in ref.all_entries():
        has_page_number = bool(_PAGE_NUMBER_PATTERN.search(entry.citation))
        if not has_page_number:
            continue
        if ref.PAGE_CITE_PENDING in entry.citation:
            # e.g. "op §5, Table 5-9, page cite pending" contains no actual
            # page digits, so this branch is rare, but a citation string
            # could in principle contain both a real page AND the pending
            # marker for a DIFFERENT sub-claim -- treat that as fine either
            # way, since it never overclaims.
            continue
        assert entry.citation in _ALLOWED_EXACT_PAGE_CITATIONS, (
            f"{table_name}: entry with context={entry.context!r} claims a specific page "
            f"({entry.citation!r}) that is not in the independently-traceable allowlist -- "
            "either add real evidence to the allowlist or change the citation to say "
            "PAGE_CITE_PENDING."
        )


def test_incomplete_tables_carry_a_coverage_note():
    for table in ref.ALL_TABLES:
        if not table.complete:
            assert table.coverage_note, f"{table.name} is marked incomplete but has no coverage_note"


def test_table_5_8_is_a_documented_gap_not_invented_data():
    # The one table this unit could not source any numbers for at all --
    # pinned explicitly so a future edit cannot quietly add invented rows
    # without this test forcing a conscious decision.
    assert ref.TABLE_5_8_RSA_DESIGN_SPEED.entries == ()
    assert ref.TABLE_5_8_RSA_DESIGN_SPEED.complete is False
    assert "no" in ref.TABLE_5_8_RSA_DESIGN_SPEED.coverage_note.lower()


def test_table_5_9_gap_acceptance_covers_the_two_known_movement_types():
    contexts = {e.context for e in ref.TABLE_5_9_GAP_ACCEPTANCE.entries}
    assert any("left turn" in c and "critical gap" in c for c in contexts)
    assert any("crossing, 2-lane" in c and "critical gap" in c for c in contexts)
    assert ref.TABLE_5_9_GAP_ACCEPTANCE.complete is False


def test_table_5_9_critical_gap_envelope_matches_the_two_transcribed_rows():
    # left turn 5.0s, crossing 2-lane 4.0s -- computed from data, not a
    # second hardcoded literal (see the function's own docstring).
    assert ref.table_5_9_critical_gap_envelope() == (4.0, 5.0)


def test_table_5_9_follow_up_headway_rows_are_flagged_not_vissim_comparable():
    follow_up_rows = [e for e in ref.TABLE_5_9_GAP_ACCEPTANCE.entries if "follow-up headway" in e.context]
    assert follow_up_rows, "expected at least one follow-up headway row"
    for row in follow_up_rows:
        assert "NOT Vissim-comparable" in row.context


def test_roundabout_critical_gap_minimums_have_a_real_page_citation():
    for entry in ref.ROUNDABOUT_CRITICAL_GAP_MINIMUM.entries:
        assert entry.citation == "op p.53"
    single_lane = [e for e in ref.ROUNDABOUT_CRITICAL_GAP_MINIMUM.entries if "single-lane" in e.context]
    multi_lane = [e for e in ref.ROUNDABOUT_CRITICAL_GAP_MINIMUM.entries if "multi-lane" in e.context]
    assert single_lane[0].value == 2.1
    assert multi_lane[0].value == 1.5


def test_detector_defaults_stay_in_sync_with_tools_edit_constants():
    # tools_edit.py owns the canonical constants add_detector's own
    # default-pos logic actually runs on; this module intentionally
    # re-states rather than imports them (see its own module docstring) --
    # this test is the guard against the two silently drifting apart.
    assert ref.DETECTOR_SETBACK_M.value == tools_edit._MRWA_DETECTOR_SETBACK_M
    assert ref.DETECTOR_LENGTH_M.value == tools_edit._MRWA_DETECTOR_LENGTH_M


def test_saturation_flow_rr67_worked_example_matches_the_extract():
    example = ref.RR67_FORMULA["worked_example"]
    assert example == {"wl": 4.2, "position": "offside", "f": 1.0, "r": 22, "sf_pcu_h": 2036}


def test_saturation_flow_method_hierarchy_is_ordered_measurement_first():
    labels = [label for label, _citation in ref.SATURATION_FLOW_METHOD_HIERARCHY]
    assert labels[0].startswith("on-site measurement")
    assert labels[-1].startswith("SCATS MF data")


def test_no_invented_page_numbers_in_the_non_reference_value_citations():
    """RR67_FORMULA / WARMUP_FORMULA / SATURATION_FLOW_METHOD_HIERARCHY carry
    their own citation strings outside the ReferenceValue/ReferenceTable
    shape all_entries() scans -- covered separately here so nothing in the
    module escapes the never-invent check just by being a plain dict/tuple
    instead of a ReferenceValue."""
    citations = [
        ref.RR67_FORMULA["citation"],
        ref.WARMUP_FORMULA["citation"],
        ref.CALIBRATION_STEPS_CITATION,
        ref.CAR_FOLLOWING_FROM_SATURATION_FLOW_CITATION,
    ]
    citations.extend(citation for _label, citation in ref.SATURATION_FLOW_METHOD_HIERARCHY)
    citations.extend(citation for _n, _label, citation in ref.CALIBRATION_STEPS_2_12_1)
    citations.extend(citation for _label, citation in ref.CALIBRATION_PRINCIPLES_2_12_1)

    for citation in citations:
        has_page_number = bool(_PAGE_NUMBER_PATTERN.search(citation))
        if not has_page_number:
            continue
        assert citation in _ALLOWED_EXACT_PAGE_CITATIONS, (
            f"citation {citation!r} claims a specific page not in the independently-traceable "
            "allowlist -- either add real evidence to the allowlist or use PAGE_CITE_PENDING."
        )


def test_the_calibration_step_order_is_the_guidelines_own():
    """§2.12.1's order is the whole point of encoding it: the CAPACITY steps
    (1-9) come before demand (10) and route choice (11), which is what lets a
    diagnosis say "the next step the authority names is 7, not another demand
    edit". Encoded as data so that claim is checkable rather than asserted in
    prose."""
    steps = {number: label for number, label, _citation in ref.CALIBRATION_STEPS_2_12_1}

    assert list(steps) == list(range(1, 14))
    assert steps[6] == "signalised intersections"
    assert steps[7] == "stop-line saturation flow"
    assert steps[10] == "demand calibration"
    assert steps[11] == "route choice"
    assert ref.calibration_step(7) == (7, steps[7], ref.CALIBRATION_STEPS_CITATION)


def test_an_unknown_calibration_step_raises_rather_than_being_invented():
    with pytest.raises(KeyError):
        ref.calibration_step(0)
    with pytest.raises(KeyError):
        ref.calibration_step(14)


def test_each_calibration_principle_cites_the_page_it_is_actually_on():
    """REGRESSION (refuter, 2026-08-18). All three principles shared the STEP
    list's citation, "op §2.12.1, p.34-42". Two of them are not on those pages:

    * "adjust one single parameter at a time" is in §2.12.1's own preamble,
      document p.33 (source PDF page 51, whose printed footer reads "Page 33 of
      271" -- the -18 offset the module documents);
    * "validation data must be independent and cannot have been used during
      calibration" is in the preamble of §2.12, a DIFFERENT section, also p.33.

    p.34-42 is where the thirteen numbered sub-sections live and where neither
    sentence appears, so the old citation pointed a reader at pages that do not
    carry the text. Only the third principle (§2.12.1.11 "Route Choices",
    p.41) was inside the range, and it is now cited at its own subsection.

    A page range that does not contain its text is the exact failure this
    module exists to prevent, so each principle carries its own citation and
    none of them may silently fall back to the step list's.
    """
    citations = dict(
        (label, citation) for label, citation in ref.CALIBRATION_PRINCIPLES_2_12_1
    )

    assert citations["change one parameter at a time"] == "op §2.12.1, p.33"
    assert (
        citations["validation data must be independent of the data used for calibration"]
        == "op §2.12, p.33"
    )
    assert (
        citations["route choice (§2.12.1.11) is confirmed BEFORE volume calibration"]
        == "op §2.12.1.11, p.41"
    )
    # None of them may share the step list's range again.
    assert ref.CALIBRATION_STEPS_CITATION == "op §2.12.1, p.34-42"
    assert all(
        citation != ref.CALIBRATION_STEPS_CITATION
        for _label, citation in ref.CALIBRATION_PRINCIPLES_2_12_1
    )


def test_all_tables_are_reachable_from_all_entries():
    table_names_in_entries = {name for name, _entry in ref.all_entries()}
    for table in ref.ALL_TABLES:
        if table.entries:
            assert table.name in table_names_in_entries
    assert "(standalone)" in table_names_in_entries

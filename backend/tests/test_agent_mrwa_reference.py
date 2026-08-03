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
    citations = [ref.RR67_FORMULA["citation"], ref.WARMUP_FORMULA["citation"]]
    citations.extend(citation for _label, citation in ref.SATURATION_FLOW_METHOD_HIERARCHY)

    for citation in citations:
        has_page_number = bool(_PAGE_NUMBER_PATTERN.search(citation))
        if not has_page_number:
            continue
        assert citation in _ALLOWED_EXACT_PAGE_CITATIONS, (
            f"citation {citation!r} claims a specific page not in the independently-traceable "
            "allowlist -- either add real evidence to the allowlist or use PAGE_CITE_PENDING."
        )


def test_all_tables_are_reachable_from_all_entries():
    table_names_in_entries = {name for name, _entry in ref.all_entries()}
    for table in ref.ALL_TABLES:
        if table.entries:
            assert table.name in table_names_in_entries
    assert "(standalone)" in table_names_in_entries

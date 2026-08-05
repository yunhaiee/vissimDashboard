"""Characterization + regression pins for GEH rows whose observed AND modelled
volumes are both zero (unit 1.5, D3).

Why this file exists
--------------------
``report_exports._geh`` used to return **0.0** when ``model + observed <= 0`` --
a *passing* GEH. A movement (or a whole node) with 0 observed and 0 modelled
vehicles therefore scored 0.0, counted as "GEH < 5", and inflated the Flow/GEH
percentages printed in a customer-facing .docx, the Count-Validation grade
(양호), and the executive-summary sentence.

Every expectation below was FIRST captured against that old behaviour, run
green, and only then updated -- each test carries its own BEFORE -> AFTER
table. The pre-existing report tests could not catch this drift: they assert
"CSV == source function", so both sides of the comparison move together. These
tests assert against *literal expected numbers* instead.
"""
from __future__ import annotations

import pytest

from services import report_builder, report_exports
from services.validation import DirectionCount, NodeRealCounts


def _direction(name: str, real: int, sim: int) -> DirectionCount:
    return DirectionCount(direction=name, real=real, sim_basam=sim)


def _node(node_id: str, name: str, total_real, total_sim, directions) -> NodeRealCounts:
    return NodeRealCounts(
        node_id=node_id,
        node_name=name,
        total_real=total_real,
        total_sim_basam=total_sim,
        directions=directions,
    )


# AM carries the interesting shapes; PM is deliberately clean, so the AM/PM
# split stays visible in the pinned numbers.
_FIXTURE = {
    "AM": {
        # Two healthy movements + ONE 0/0 movement (the poisoned row).
        "1": _node("1", "Alpha", 1000, 1020, [
            _direction("W-E", 500, 510),
            _direction("N-S", 500, 510),
            _direction("E-W", 0, 0),
        ]),
        # The WHOLE node is 0/0 -- node total and its only movement.
        "2": _node("2", "Bravo", 0, 0, [
            _direction("W-E", 0, 0),
        ]),
        # A genuine failure, so the percentages are not trivially 100%.
        "3": _node("3", "Charlie", 800, 1200, [
            _direction("W-E", 800, 1200),
        ]),
    },
    "PM": {
        "1": _node("1", "Alpha", 900, 910, [
            _direction("W-E", 900, 910),
        ]),
    },
}


@pytest.fixture
def zero_row_counts(monkeypatch):
    """Point both report modules at the fixture above.

    ``_collect_flow_geh``/``_geh_validation_rows`` call
    ``load_real_counts.cache_clear()`` before reading, so the stub carries that
    attribute too.
    """

    def fake_load_real_counts(_path, period=None):
        return _FIXTURE.get((period or "AM").upper(), {})

    fake_load_real_counts.cache_clear = lambda: None
    monkeypatch.setattr(report_exports, "load_real_counts", fake_load_real_counts)
    monkeypatch.setattr(report_builder, "load_real_counts", fake_load_real_counts)
    return fake_load_real_counts


# --- Flow / GEH validation (.docx table + CSV) -------------------------------


def test_flow_geh_percentages_exclude_zero_over_zero_rows(zero_row_counts):
    """A 0/0 movement is no longer a free GEH pass -- and a node with NO defined
    GEH at all publishes "undefined", not a number.

    (No. Counts, Flow %, GEH %)   BEFORE 1.5      AFTER 1.5       CORRECTED
      AM Node 1                   (3, 100, 100)   (3, 100, 100)   unchanged   [1 excluded]
      AM Node 2                   (1, 100, 100)   (1, 100,   0)   (1, 100, None) [1 excluded]
      AM Node 3                   (1,   0,   0)   (1,   0,   0)   unchanged
      AM Total                    (5,  80,  80)   (5,  80,  67)   unchanged   [2 excluded]
      PM Node 1 / PM Total        (1, 100, 100)   (1, 100, 100)   unchanged

    Node 2's ``0`` was the second wrong answer to the same question: before the
    unit it published a wrongly-GOOD 100%, the unit made it a wrongly-BAD 0%,
    and neither said "undefined". ``None`` renders as "-" in the .docx and as an
    empty cell in the CSV, matching what ``report_builder`` and ``exporters``
    already do for the same state.

    ``No. Counts`` and ``Flow %`` are deliberately UNCHANGED: only the GEH
    denominator shrinks, and it is published as ``geh_counts`` /
    ``geh_excluded`` instead of being hidden inside the percentage. The Total
    row still aggregates over every node -- an undefined node contributes 0 to
    both ``geh_ok`` and ``geh_counts``, so AM Total stays 2/3 = 67%.
    """
    rows = {(row["period"], row["node"]): row for row in report_exports.flow_geh_csv_rows()}
    observed = {
        key: (row["counts"], row["flow_pct"], row["geh_pct"], row["geh_counts"], row["geh_excluded"])
        for key, row in rows.items()
    }
    assert observed == {
        ("AM Peak", "Node 1"): (3, 100, 100, 2, 1),
        ("AM Peak", "Node 2"): (1, 100, None, 0, 1),
        ("AM Peak", "Node 3"): (1, 0, 0, 1, 0),
        ("AM Peak", "Total"): (5, 80, 67, 3, 2),
        ("PM Peak", "Node 1"): (1, 100, 100, 1, 0),
        ("PM Peak", "Total"): (1, 100, 100, 1, 0),
    }
    # Node 3 is the control: a genuine 0/1 GEH failure must still publish 0,
    # so "undefined" and "measured and bad" stay distinguishable.
    assert rows[("AM Peak", "Node 3")]["geh_pct"] == 0
    assert rows[("AM Peak", "Node 2")]["geh_pct"] is None


def test_flow_geh_docx_discloses_the_excluded_rows(zero_row_counts):
    """The shrunken denominator must be visible in the customer-facing .docx,
    not silently swallowed (D3)."""
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    report_exports.add_flow_geh_section(doc)
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "GEH % is calculated over 3 of 5 counts" in text
    assert "2 count(s) excluded" in text
    # PM has nothing to exclude, so it gets no note -- the disclosure is
    # conditional, not boilerplate.
    assert text.count("count(s) excluded") == 1
    # The per-node "-" marker needs its own sentence: a reader who sees "-" in
    # one row must not read it as a missing 0%.
    assert 'A row showing "-" in the GEH column' in text


def test_flow_geh_docx_marks_an_all_undefined_node_rather_than_printing_zero_percent(zero_row_counts):
    """AM Node 2 has ONE count, and it is 0 observed / 0 modelled, so no GEH
    exists for it at all.

    Row  BEFORE 1.5  ['Node 2','1','0','0','+0.0%','100%','100%']  (wrongly good)
    Row  AFTER 1.5   ['Node 2','1','0','0','+0.0%','100%','0%']    (wrongly bad)
    Row  CORRECTED   ['Node 2','1','0','0','+0.0%','100%','-']     (undefined)
    """
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    report_exports.add_flow_geh_section(doc)
    cells = [tuple(c.text for c in row.cells) for table in doc.tables for row in table.rows]
    node2 = next(row for row in cells if row[0] == "Node 2")
    assert node2 == ("Node 2", "1", "0", "0", "+0.0%", "100%", "-")
    # A measured-and-failing node must still print a real 0%, not "-".
    node3 = next(row for row in cells if row[0] == "Node 3")
    assert node3[6] == "0%"


def test_flow_geh_docx_cells_come_from_the_csv_row_builder(zero_row_counts):
    """The .docx table and the CSV must be ONE computation (D2): every
    percentage printed in the table appears in the CSV rows verbatim."""
    docx = pytest.importorskip("docx")
    doc = docx.Document()
    report_exports.add_flow_geh_section(doc)
    cells = {tuple(c.text for c in row.cells) for table in doc.tables for row in table.rows}
    am_total = next(row for row in cells if row[0] == "Total" and row[1] == "5")
    assert am_total[5] == "80%"
    assert am_total[6] == "67%"


# --- Count validation (GEH) table + CSV --------------------------------------


def test_node_total_geh_of_zero_over_zero_is_not_a_good_grade(zero_row_counts):
    """A node whose observed AND modelled totals are both 0 no longer prints
    GEH 0.00 / 양호.

    (GEH, Grade)     BEFORE            AFTER
      AM Node 1      (0.63, 양호)       unchanged
      AM Node 2      (0.00, 양호)       (None, '-')
      AM Node 3      (12.65, 불량)      unchanged
      PM Node 1      (0.33, 양호)       unchanged
    """
    rows = {(row["period"], row["node"]): row for row in report_builder.geh_validation_csv_rows()}
    observed = {key: (row["geh"], row["grade"]) for key, row in rows.items()}
    assert observed == {
        ("AM Peak", "Node 1"): (0.63, "양호"),
        ("AM Peak", "Node 2"): (None, "-"),
        ("AM Peak", "Node 3"): (12.65, "불량"),
        ("PM Peak", "Node 1"): (0.33, "양호"),
    }


def test_overview_sentence_geh_percentage_excludes_zero_over_zero(zero_row_counts, monkeypatch):
    """Executive-summary sentence (report_builder._add_overview).

    BEFORE: 4 node totals counted, 3 with GEH<5 (Node 2's 0/0 among them) -> 75%
    AFTER : 3 node totals counted, 2 with GEH<5                           -> 67%,
            plus an explicit note naming the denominator and the exclusions.
    """
    docx = pytest.importorskip("docx")
    monkeypatch.setattr(report_builder, "travel_chart_specs", lambda: [])
    doc = docx.Document()
    report_builder._add_overview(doc)
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "67%" in text
    assert "75%" not in text
    assert "GEH 산정 대상 3개" in text
    assert "1개는 제외" in text


def test_overview_sentence_has_no_exclusion_note_when_nothing_is_excluded(monkeypatch):
    """The disclosure is conditional: a clean dataset reads exactly as before."""
    docx = pytest.importorskip("docx")

    def clean_counts(_path, period=None):
        return {"1": _node("1", "Alpha", 1000, 1020, [_direction("W-E", 1000, 1020)])}

    clean_counts.cache_clear = lambda: None
    monkeypatch.setattr(report_builder, "load_real_counts", clean_counts)
    monkeypatch.setattr(report_builder, "travel_chart_specs", lambda: [])
    doc = docx.Document()
    report_builder._add_overview(doc)
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "100%" in text
    assert "제외" not in text

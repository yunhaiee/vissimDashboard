"""MRWA §5.6.3.1 critical-intersection override in the report exports.

§5.6.3.1 (body text, doc p.213, immediately after Table 5-12):

    "Even if the model outputs meet the traffic volume validation criteria
    for the entire network, the modeller should check and ensure that turning
    volumes at critical intersection(s) achieve GEH less than 5."

Before this, ``report_exports`` published network-wide rates only, so a model
whose designated critical intersection carried a GEH 6 movement could show a
clean PASS on a model MRWA would reject.

The load-bearing test here is the COUNTERFACTUAL: identical count data, one
run with a critical intersection designated and one without. If both come out
the same, the override is decorative.

Also pinned: the two §5.6.3 criteria with NO observed-side ingest path in
this codebase (Table 5-14 signal timing, doc p.216; §5.6.3.5 saturation flow,
doc p.216) publish an explicit "not assessable" state rather than being
omitted -- an omitted criterion reads as a criterion that passed.
"""
from __future__ import annotations

import pytest

from services import report_exports
from services.validation import DirectionCount, NodeRealCounts

NOT_ASSESSABLE = "관측값 없음 — 평가 불가 (not assessable: no observed data in this project)"
NOT_DESIGNATED = (
    "critical 교차로 미지정 — 검사 미실행 "
    "(no critical intersection designated: this check did not run)"
)


@pytest.fixture
def counts_where_network_passes_but_node_2_does_not(monkeypatch):
    """20 clean movements at node 1 plus 1 clean + 1 bad at node 2.

    21 of 22 movements are under GEH 5 = 95.45 %, which rounds to 95 and so
    CLEARS Category 1's Table 5-12 network-wide requirement of 95 %. Node 2's
    1000-vs-1200 movement is GEH 6.03 -- above 5.
    """

    def fake_load_real_counts(_path, period=None):
        return {
            "1": NodeRealCounts(
                node_id="1",
                node_name="Alpha",
                total_real=None,
                total_sim_basam=None,
                directions=[
                    DirectionCount(direction=f"D{i}", real=1000, sim_basam=1020)
                    for i in range(20)
                ],
            ),
            "2": NodeRealCounts(
                node_id="2",
                node_name="Bravo",
                total_real=None,
                total_sim_basam=None,
                directions=[
                    DirectionCount(direction="W-E", real=1000, sim_basam=1010),
                    DirectionCount(direction="E-W", real=1000, sim_basam=1200),
                ],
            ),
        }

    fake_load_real_counts.cache_clear = lambda: None
    monkeypatch.setattr(report_exports, "load_real_counts", fake_load_real_counts)
    return fake_load_real_counts


def test_network_wide_rates_pass_on_this_data(counts_where_network_passes_but_node_2_does_not):
    """The precondition the override exists for. Without it this is a PASS."""
    verdict = report_exports.flow_geh_verdict()
    assert verdict["network_passed"] is True
    assert verdict["periods"]["AM Peak"]["geh_pct"] == 95
    assert verdict["periods"]["AM Peak"]["geh_counts"] == 22
    assert verdict["verdict"] == "PASS"
    # ...and the report says the override did not run, rather than implying it did.
    assert NOT_DESIGNATED in verdict["reasons"]
    assert verdict["critical_passed"] is None


def test_designating_the_failing_node_flips_the_verdict_and_names_it(
    counts_where_network_passes_but_node_2_does_not,
):
    """THE counterfactual: same data, one designation, opposite verdict."""
    verdict = report_exports.flow_geh_verdict(critical_node_ids="2")

    assert verdict["network_passed"] is True, "the network leg must still pass"
    assert verdict["critical_passed"] is False
    assert verdict["verdict"] == "FAIL"

    # The node is NAMED -- both its label and its id -- and the number is shown.
    joined = " ".join(verdict["reasons"])
    assert "Bravo" in joined
    assert "id 2" in joined
    assert "6.03" in joined
    assert "5.6.3.1" in joined and "p.213" in joined
    assert verdict["critical_failures"], "the failing node(s) must be enumerated"


def test_designating_a_clean_node_leaves_the_verdict_passing(
    counts_where_network_passes_but_node_2_does_not,
):
    """The override must not fail everything it touches -- node 1 is clean."""
    verdict = report_exports.flow_geh_verdict(critical_node_ids="1")
    assert verdict["critical_passed"] is True
    assert verdict["verdict"] == "PASS"


def test_criteria_coverage_states_are_explicit_never_a_silent_pass(
    counts_where_network_passes_but_node_2_does_not,
):
    rows = {r["criterion"]: r for r in report_exports.criteria_coverage_rows()}
    assert set(rows) == {"critical_intersection", "signal_timing", "saturation_flow"}

    assert rows["critical_intersection"]["criterion_state"] == NOT_DESIGNATED
    assert rows["signal_timing"]["criterion_state"] == NOT_ASSESSABLE
    assert rows["saturation_flow"]["criterion_state"] == NOT_ASSESSABLE

    # ERRATA GUARD: citations name the TABLE and the DOC PAGE, never an
    # in-text "Figure N" label (§5 carries nine such cross-reference errors).
    assert rows["signal_timing"]["node"] == "Signal timing (Table 5-14, doc p.216)"
    assert rows["saturation_flow"]["node"] == "Saturation flow (5.6.3.5, doc p.216)"
    assert rows["critical_intersection"]["node"] == "Critical intersections (5.6.3.1, doc p.213)"
    for row in rows.values():
        assert "Figure" not in row["node"]
        # No numeric cell may be invented for a criterion that was not measured.
        assert row["counts"] is None and row["geh_pct"] is None and row["flow_pct"] is None

    designated = {r["criterion"]: r for r in report_exports.criteria_coverage_rows("2")}
    assert designated["critical_intersection"]["criterion_state"].startswith("FAIL")
    assert "Bravo" in designated["critical_intersection"]["criterion_state"]


def test_the_docx_section_carries_the_verdict_and_the_not_assessable_states(
    counts_where_network_passes_but_node_2_does_not,
):
    pytest.importorskip("docx")
    from docx import Document

    doc = Document()
    report_exports.add_flow_geh_section(doc, critical_node_ids="2")
    text = "\n".join(p.text for p in doc.paragraphs)
    text += "\n" + "\n".join(
        cell.text for table in doc.tables for row in table.rows for cell in row.cells
    )

    assert "FAIL" in text
    assert "Bravo" in text
    assert NOT_ASSESSABLE in text
    assert "Table 5-14, doc p.216" in text
    # And the corrected note: no DMRB attribution, no flat ">85%" target.
    assert "DMRB" not in text
    assert "Target: >85% on both" not in text
    assert "Table 5-12" in text and "doc p.213" in text


def test_the_docx_section_without_a_designation_says_so(
    counts_where_network_passes_but_node_2_does_not,
):
    pytest.importorskip("docx")
    from docx import Document

    doc = Document()
    report_exports.add_flow_geh_section(doc)
    text = "\n".join(p.text for p in doc.paragraphs)
    text += "\n" + "\n".join(
        cell.text for table in doc.tables for row in table.rows for cell in row.cells
    )
    assert NOT_DESIGNATED in text

"""Tests for eval/report.py (M5.4): Wilson CI math (against independently
hand-computed reference values, including the 0/N and N/N edges) and
deterministic markdown rendering from canned result records -- no agent
loop, no filesystem beyond tmp_path JSONL fixtures.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.report import (
    RateSummary,
    load_injection_records,
    per_injector_arm_table,
    pooled_arm_table,
    render_full_report,
    render_per_injector_table,
    render_pooled_table,
    wilson_ci,
)

# ---------------------------------------------------------------------------
# Wilson CI: hand-computed reference values.
#
# Computed independently (standard closed-form Wilson score interval,
# z = 1.959963984540054 for two-sided 95%):
#   lower = max(0, center - margin); upper = min(1, center + margin)
#   center = (p + z^2/2n) / (1 + z^2/n)
#   margin = z * sqrt(p(1-p)/n + z^2/4n^2) / (1 + z^2/n)
# Cross-checked against the well-known textbook figure for 0/10 (~0-0.278)
# and 5/10 (~0.237-0.763) before being pinned to full float precision here.
# ---------------------------------------------------------------------------

_HAND_COMPUTED = [
    # (successes, n, expected_lower, expected_upper)
    (0, 10, 0.0, 0.2775327998628892),
    (10, 10, 0.7224672001371107, 1.0),  # true value is 0.999999999999... -> clamped to 1.0
    (5, 10, 0.236593090512564, 0.7634069094874361),
    (1, 7, 0.02567962434474355, 0.5131278292743189),
    (6, 7, 0.4868721707256811, 0.9743203756552565),
    (0, 1, 0.0, 0.7934506856227626),
    (1, 1, 0.20654931437723745, 1.0),
    (23, 100, 0.15843265880303448, 0.3215438302287378),
]


@pytest.mark.parametrize("successes,n,expected_lower,expected_upper", _HAND_COMPUTED)
def test_wilson_ci_matches_hand_computed_values(successes, n, expected_lower, expected_upper):
    lower, upper = wilson_ci(successes, n)
    assert lower == pytest.approx(expected_lower, abs=1e-9)
    assert upper == pytest.approx(expected_upper, abs=1e-9)


def test_wilson_ci_zero_of_n_edge_lower_bound_is_exactly_zero():
    # Floating-point subtraction can leave a ~1e-17 residue instead of a
    # literal 0.0 for some (successes, n) pairs -- assert "effectively zero"
    # rather than bit-exact equality, which is the honest claim here.
    lower, _ = wilson_ci(0, 25)
    assert lower == pytest.approx(0.0, abs=1e-9)


def test_wilson_ci_n_of_n_edge_upper_bound_is_exactly_one():
    _, upper = wilson_ci(25, 25)
    assert upper == pytest.approx(1.0, abs=1e-9)


def test_wilson_ci_is_symmetric_around_p_half():
    lower, upper = wilson_ci(5, 10)
    assert lower == pytest.approx(1 - upper, abs=1e-12)


def test_wilson_ci_bounds_always_within_unit_interval_and_ordered():
    for n in (1, 2, 3, 5, 10, 17, 50, 200):
        for successes in range(0, n + 1):
            lower, upper = wilson_ci(successes, n)
            assert 0.0 <= lower <= upper <= 1.0, (successes, n, lower, upper)


def test_wilson_ci_rejects_non_positive_n():
    with pytest.raises(ValueError):
        wilson_ci(0, 0)
    with pytest.raises(ValueError):
        wilson_ci(0, -1)


def test_wilson_ci_rejects_successes_out_of_range():
    with pytest.raises(ValueError):
        wilson_ci(-1, 10)
    with pytest.raises(ValueError):
        wilson_ci(11, 10)


# ---------------------------------------------------------------------------
# RateSummary
# ---------------------------------------------------------------------------


def test_rate_summary_zero_trials_has_no_rate_and_renders_na():
    summary = RateSummary.from_counts(0, 0)
    assert summary.rate is None
    assert summary.ci_low is None and summary.ci_high is None
    assert summary.render() == "n/a (N=0)"


def test_rate_summary_from_counts_matches_wilson_ci():
    summary = RateSummary.from_counts(5, 10)
    lower, upper = wilson_ci(5, 10)
    assert summary.rate == 0.5
    assert summary.ci_low == pytest.approx(lower)
    assert summary.ci_high == pytest.approx(upper)
    rendered = summary.render()
    assert "50.0%" in rendered and "5/10" in rendered


# ---------------------------------------------------------------------------
# Loading JSONL
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
            handle.write("\n")  # a blank line between records -- must be skipped


def test_load_injection_records_reads_and_concatenates_multiple_files(tmp_path: Path):
    file_a = tmp_path / "full_stack.jsonl"
    file_b = tmp_path / "ablation.jsonl"
    _write_jsonl(file_a, [{"injector": "x", "arm": "full_stack", "detected": True, "refused": True}])
    _write_jsonl(
        file_b,
        [
            {"injector": "x", "arm": "ablation", "detected": False, "refused": False},
            {"injector": "y", "arm": "ablation", "detected": False, "refused": True},
        ],
    )

    records = load_injection_records([file_a, file_b])
    assert len(records) == 3
    assert records[0]["arm"] == "full_stack"
    assert records[1]["arm"] == "ablation"
    assert records[2]["injector"] == "y"


# ---------------------------------------------------------------------------
# Canned records for table rendering
# ---------------------------------------------------------------------------


def _canned_records() -> list[dict]:
    """Deliberately inserted out of sorted order, and with a mix of
    detected/refused outcomes, to prove both the arithmetic and the
    deterministic (sorted) row ordering."""
    return [
        {"injector": "wrong_param_name", "arm": "full_stack", "detected": False, "refused": True},
        {"injector": "clamped_write", "arm": "full_stack", "detected": True, "refused": True},
        {"injector": "clamped_write", "arm": "full_stack", "detected": True, "refused": True},
        {"injector": "clamped_write", "arm": "ablation", "detected": False, "refused": False},
        {"injector": "clamped_write", "arm": "ablation", "detected": False, "refused": False},
        {"injector": "wrong_param_name", "arm": "full_stack", "detected": False, "refused": True},
    ]


def test_per_injector_arm_table_groups_and_sorts_deterministically():
    rows = per_injector_arm_table(_canned_records())
    keys = [(r["injector"], r["arm"]) for r in rows]
    assert keys == sorted(keys)
    assert keys == [
        ("clamped_write", "ablation"),
        ("clamped_write", "full_stack"),
        ("wrong_param_name", "full_stack"),
    ]

    clamped_full = next(r for r in rows if r["injector"] == "clamped_write" and r["arm"] == "full_stack")
    assert clamped_full["n"] == 2
    assert clamped_full["detection"].successes == 2
    assert clamped_full["detection"].rate == 1.0

    clamped_ablation = next(r for r in rows if r["injector"] == "clamped_write" and r["arm"] == "ablation")
    assert clamped_ablation["n"] == 2
    assert clamped_ablation["detection"].successes == 0
    assert clamped_ablation["detection"].rate == 0.0

    wrong_param = next(r for r in rows if r["injector"] == "wrong_param_name")
    assert wrong_param["n"] == 2
    assert wrong_param["detection"].rate == 0.0
    assert wrong_param["refusal"].rate == 1.0


def test_pooled_arm_table_aggregates_across_injectors_sorted_by_arm():
    rows = pooled_arm_table(_canned_records())
    assert [r["arm"] for r in rows] == ["ablation", "full_stack"]

    ablation_row = rows[0]
    assert ablation_row["n"] == 2
    assert ablation_row["detection"].successes == 0

    full_stack_row = rows[1]
    assert full_stack_row["n"] == 4  # 2 clamped_write + 2 wrong_param_name
    assert full_stack_row["detection"].successes == 2  # only the clamped_write pair


def test_render_per_injector_table_is_deterministic_and_sorted_in_output():
    markdown = render_per_injector_table(_canned_records())
    lines = [line for line in markdown.splitlines() if line.startswith("|") and "injector" not in line and "---" not in line]
    # clamped_write/ablation must render before clamped_write/full_stack,
    # which must render before wrong_param_name/full_stack.
    assert lines[0].startswith("| clamped_write | ablation |")
    assert lines[1].startswith("| clamped_write | full_stack |")
    assert lines[2].startswith("| wrong_param_name | full_stack |")
    assert "100.0%" in lines[1]  # clamped_write/full_stack detection rate
    assert "0.0%" in lines[0]  # clamped_write/ablation detection rate


def test_render_per_injector_table_handles_empty_records():
    markdown = render_per_injector_table([])
    assert "no records" in markdown


def test_render_pooled_table_handles_empty_records():
    markdown = render_pooled_table([])
    assert "no records" in markdown


def test_render_full_report_contains_header_and_both_tables():
    report = render_full_report(_canned_records())
    assert "# M5.4 failure-injection results" in report
    assert "total trials: 6" in report
    assert "Wilson score interval" in report
    assert "## Per-injector, per-arm" in report
    assert "## Pooled (across every injector), per-arm" in report
    assert "clamped_write" in report and "wrong_param_name" in report


def test_render_full_report_is_byte_identical_across_repeated_calls():
    records = _canned_records()
    assert render_full_report(records) == render_full_report(list(reversed(records)))

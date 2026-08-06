"""MRWA §5.6.3 validation criteria -- backend semantics (section-5 pack).

Three layers:

  1. ``shared/mrwa-validation-criteria.json`` is the ONE source both runtimes
     answer to. ``services.validation_profiles`` LOADS it (it holds no literal
     copy of any number), so the tests below pin the FILE against the
     page-cited transcription in
     ``docs/product/reference/mrwa-section5-extract.md``. The TypeScript half
     keeps literals and is pinned to the same file by
     ``frontend/tests/dashboard-contract.test.ts``.

  2. Characterization: ``report_exports._flow_pass`` used to carry the
     700/100/2700/0.15/400 literals inline. Re-pointing it at the shared file
     must not move a single answer.

  3. The two rules that are easy to get silently wrong: Table 5-14's
     "whichever is SMALLER" direction, and the §5.6.3.1 critical-intersection
     override, which is STRICTER than any category's network-wide rate.

Citations are DOC pages (the number printed as "Page NNN of 271"); doc page
= PDF page - 18.
"""
from __future__ import annotations

import json
import math

import pytest

from services import validation_profiles as vp


# --- layer 1: the shared file says what the guideline says --------------------


def test_shared_criteria_file_is_present_and_loaded_not_duplicated():
    """A silently-missing file would make the module fail loudly (it loads at
    import time), which is the point -- there is no literal fallback to drift."""
    assert vp.CRITERIA_PATH.exists(), f"missing cross-language file: {vp.CRITERIA_PATH}"
    raw = vp.CRITERIA_PATH.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert set(data["categories"]) == {"cat1", "cat2", "cat3"}
    # The module must not carry its own copy of any threshold.
    source = (vp.__file__ or "")
    assert source.endswith("validation_profiles.py")


def test_table_5_12_matches_the_page_cited_extract():
    """Table 5-12 "Traffic flow validation criteria", §5.6.3.1, doc p.213."""
    expected = {
        # (geh<5, geh<10, flow low, flow mid, flow high, R^2)
        "cat1": (95, 100, 95, 95, 95, 0.95),
        "cat2": (85, 95, 90, 90, 90, 0.95),
        "cat3": (80, 90, 85, 85, 85, 0.9),
    }
    for key, (g5, g10, low, mid, high, r2) in expected.items():
        p = vp.VALIDATION_PROFILES[key]
        assert (
            p.geh_under_5_pct,
            p.geh_under_10_pct,
            p.flow_low_within_pct,
            p.flow_mid_within_pct,
            p.flow_high_within_pct,
            p.r_squared_min,
        ) == (g5, g10, low, mid, high, r2)

    # The three tolerance WIDTHS are category-invariant -- they live in
    # Table 5-12, NOT Table 5-13 (which has exactly one row, travel time).
    assert (vp.FLOW_LOW_MAX_VPH, vp.FLOW_LOW_TOLERANCE_VPH) == (700, 100)
    assert (vp.FLOW_MID_MAX_VPH, vp.FLOW_MID_TOLERANCE_PCT) == (2700, 15)
    assert vp.FLOW_HIGH_TOLERANCE_VPH == 400


def test_table_5_13_travel_time_is_one_row_of_pass_rates():
    """Table 5-13, §5.6.3.2, doc p.215; band from the body text above it."""
    assert [vp.VALIDATION_PROFILES[k].travel_within_pct for k in ("cat1", "cat2", "cat3")] == [
        100,
        90,
        85,
    ]
    assert vp.TRAVEL_TOLERANCE_PCT == 15
    assert vp.TRAVEL_TOLERANCE_MIN_MINUTES == 1


def test_table_5_14_and_the_other_two_criteria_are_category_invariant():
    """Table 5-14 (p.216), §5.6.3.5 (p.216) and §5.6.3.1 (p.213) give no
    Category 1/2/3 split -- every profile must therefore agree."""
    assert vp.SIGNAL_CYCLE_TIME_SECONDS == 3
    assert vp.SIGNAL_CYCLE_TIME_PCT == 5
    assert vp.SIGNAL_GREEN_TIME_SECONDS == 3
    assert vp.SIGNAL_GREEN_TIME_PCT == 10
    # The vehicle-actuated row states a comparison obligation with NO numeric
    # tolerance. None means "the guideline gives no number"; never invent one.
    assert vp.SIGNAL_ACTUATED_CALL_FREQUENCY_TOLERANCE is None
    assert vp.SATURATION_FLOW_WITHIN_PCT == 10
    assert vp.CRITICAL_INTERSECTION_GEH_MAX == 5

    assert len({p.saturation_flow_within_pct for p in vp.VALIDATION_PROFILES.values()}) == 1
    assert len({p.critical_intersection_geh_max for p in vp.VALIDATION_PROFILES.values()}) == 1
    for profile in vp.VALIDATION_PROFILES.values():
        assert profile.signal_timing is vp.SIGNAL_TIMING_BANDS


def test_citations_name_the_table_and_the_doc_page_not_an_in_text_label():
    """ERRATA GUARD: §5's in-text cross-references carry nine measured errors
    (body text says "Figure N" where the object is "Table N"), so a citation
    that repeats an in-text label can point at the wrong object. And the doc
    pages are 213/215/216 -- 231/234 are the PDF indices."""
    for profile in vp.VALIDATION_PROFILES.values():
        assert "Table 5-12 (p.213)" in profile.citation
        assert "Table 5-13 (p.215)" in profile.citation
        assert "Vissim Guidelines" in profile.citation
        assert "Model Validation" not in profile.citation  # §5's title is NOT that
        assert "231" not in profile.citation and "234" not in profile.citation
    text = vp.CRITERIA_PATH.read_text(encoding="utf-8")
    # No citation in the shared file may cite a figure.
    assert "Figure 5-" not in text


def test_get_active_profile_never_raises():
    assert vp.get_active_profile().id == "cat1"
    assert vp.get_active_profile(None).id == "cat1"
    assert vp.get_active_profile("nonsense").id == "cat1"
    assert vp.get_active_profile("cat3").id == "cat3"


# --- layer 2: characterization of the re-pointed flow bands -------------------


def _legacy_flow_pass(m, o):
    """report_exports._flow_pass EXACTLY as it read before the re-point."""
    diff = abs(m - o)
    if o < 700:
        return diff <= 100
    if o <= 2700:
        return (diff / o) <= 0.15 if o > 0 else diff == 0
    return diff <= 400


@pytest.mark.parametrize(
    "modelled,observed",
    [
        (0, 0), (100, 0), (0, 100),
        (699, 699), (799, 699), (800, 699), (599, 699), (598, 699),
        (700, 700), (805, 700), (806, 700), (595, 700), (594, 700),
        (1000, 1000), (1150, 1000), (1151, 1000), (850, 1000), (849, 1000),
        (2700, 2700), (3105, 2700), (3106, 2700),
        (2701, 2701), (3101, 2701), (3102, 2701),
        (3000, 3000), (3400, 3000), (3401, 3000), (2600, 3000), (2599, 3000),
        (-50, -50), (-20, 100), (5, 3), (1.5, 1.5),
    ],
)
def test_flow_band_answers_are_byte_for_byte_the_legacy_answers(modelled, observed):
    """Re-pointing report_exports._flow_pass at the shared file must not move
    a single answer, including on the exactly-on-the-band pairs where a
    ``ratio*100 <= 15`` rewrite would have flipped the result."""
    from services.report_exports import _flow_pass

    assert _flow_pass(modelled, observed) == _legacy_flow_pass(modelled, observed)
    assert vp.flow_within_tolerance(modelled, observed) == _legacy_flow_pass(modelled, observed)


def test_flow_band_is_not_assessable_rather_than_a_free_pass():
    """The legacy helper would have raised on a missing side; a silent True
    would be worse. None is falsy, so the existing
    ``sum(1 for ... if _flow_pass(...))`` callers are unaffected."""
    assert vp.flow_within_tolerance(None, 100) is None
    assert vp.flow_within_tolerance(100, None) is None
    assert vp.flow_within_tolerance(math.nan, 100) is None
    assert vp.flow_within_tolerance(math.inf, 100) is None
    assert vp.flow_within_tolerance("abc", 100) is None


# --- layer 3a: Table 5-14's "whichever is SMALLER" direction ------------------


def _greater_direction(observed_seconds, band):
    """The WRONG direction, spelled out. Travel time (§5.6.3.2, p.215) is
    "whichever is greater"; a shared max() helper applied to Table 5-14 would
    compute exactly this and silently loosen every signal-timing band."""
    return max(float(band.seconds), observed_seconds * band.pct / 100.0)


@pytest.mark.parametrize("case", json.loads(vp.CRITERIA_PATH.read_text(encoding="utf-8"))[
    "signal_timing_direction_cases"
], ids=lambda c: c["name"])
def test_signal_timing_tolerance_takes_the_smaller_bound(case):
    band = vp.SIGNAL_TIMING_BANDS[case["band"]]
    observed = case["observed_seconds"]
    actual = vp.signal_timing_tolerance_seconds(observed, band)
    assert actual == pytest.approx(case["tolerance_seconds"])
    # And the counterfactual, from the same fixture row.
    assert _greater_direction(observed, band) == pytest.approx(case["max_direction_would_be"])


def test_flipping_the_direction_to_max_would_pass_a_model_the_guideline_rejects():
    """THE counterfactual. A 120 s observed cycle modelled at 125 s is 5 s out.

      SMALLER (correct): min(3 s, 5 % of 120 = 6 s) = 3 s -> 5 > 3 -> FAIL
      GREATER (wrong):   max(3 s, 6 s)             = 6 s -> 5 <= 6 -> PASS

    If this test ever passes with a max() implementation, the check has been
    silently loosened. Same shape on green time, where 10 % widens it further.
    """
    cycle = vp.SIGNAL_TIMING_BANDS["cycle_time"]
    assert vp.signal_timing_tolerance_seconds(120, cycle) == pytest.approx(3.0)
    assert _greater_direction(120, cycle) == pytest.approx(6.0)
    assert vp.signal_timing_within_tolerance(120, 125, cycle) is False
    assert abs(125 - 120) <= _greater_direction(120, cycle)  # the wrong answer

    green = vp.SIGNAL_TIMING_BANDS["green_time"]
    assert vp.signal_timing_tolerance_seconds(60, green) == pytest.approx(3.0)
    assert _greater_direction(60, green) == pytest.approx(6.0)
    assert vp.signal_timing_within_tolerance(60, 65, green) is False
    assert abs(65 - 60) <= _greater_direction(60, green)


def test_signal_timing_smaller_bound_can_be_the_percentage_too():
    """Not just "always 3 seconds": below 60 s the percentage is the tighter
    bound, so a hard-coded 3 s would be too LOOSE there."""
    cycle = vp.SIGNAL_TIMING_BANDS["cycle_time"]
    assert vp.signal_timing_tolerance_seconds(40, cycle) == pytest.approx(2.0)
    assert vp.signal_timing_within_tolerance(40, 42, cycle) is True
    assert vp.signal_timing_within_tolerance(40, 42.5, cycle) is False

    green = vp.SIGNAL_TIMING_BANDS["green_time"]
    assert vp.signal_timing_tolerance_seconds(20, green) == pytest.approx(2.0)
    assert vp.signal_timing_within_tolerance(20, 22, green) is True
    assert vp.signal_timing_within_tolerance(20, 23, green) is False


def test_missing_signal_timing_observation_is_not_assessable_not_a_pass():
    cycle = vp.SIGNAL_TIMING_BANDS["cycle_time"]
    assert vp.signal_timing_tolerance_seconds(None, cycle) is None
    assert vp.signal_timing_tolerance_seconds(math.nan, cycle) is None
    assert vp.signal_timing_tolerance_seconds(-1, cycle) is None
    assert vp.signal_timing_within_tolerance(None, 100, cycle) is None
    assert vp.signal_timing_within_tolerance(100, None, cycle) is None


# --- layer 3b: §5.6.3.5 saturation flow ---------------------------------------


def test_saturation_flow_within_ten_percent():
    assert vp.saturation_flow_within_tolerance(1800, 1980) is True   # exactly 10 %
    assert vp.saturation_flow_within_tolerance(1800, 1981) is False
    assert vp.saturation_flow_within_tolerance(1800, 1620) is True   # -10 %
    assert vp.saturation_flow_within_tolerance(1800, 1619) is False
    # Not assessable, never a pass.
    assert vp.saturation_flow_within_tolerance(None, 1800) is None
    assert vp.saturation_flow_within_tolerance(1800, None) is None
    assert vp.saturation_flow_within_tolerance(0, 1800) is None


# --- layer 3c: §5.6.3.1 critical-intersection override ------------------------


def test_parse_critical_node_ids_normalises_and_dedupes():
    assert vp.parse_critical_node_ids("12, 15 7") == ["12", "15", "7"]
    assert vp.parse_critical_node_ids("12,12; 7") == ["12", "7"]
    assert vp.parse_critical_node_ids(["12", "7", "12"]) == ["12", "7"]
    assert vp.parse_critical_node_ids(None) == []
    assert vp.parse_critical_node_ids("   ") == []


def test_the_override_is_stricter_than_every_category_pass_rate():
    """THE reason this rule exists. A node whose movements are 95 % under
    GEH 5 clears Category 1's network-wide rate and still fails §5.6.3.1."""
    nodes = [
        {
            "node_id": "9",
            "node_name": "Critical Cross",
            "geh_values": [1.0] * 19 + [7.5],  # 95 % under 5 -- Cat 1's rate
        }
    ]
    rate_would_pass = 19 / 20 * 100 >= vp.VALIDATION_PROFILES["cat1"].geh_under_5_pct
    assert rate_would_pass is True

    result = vp.evaluate_critical_intersections(nodes, "9")
    assert result["passed"] is False
    assert result["failing_node_ids"] == ["9"]
    assert result["nodes"][0]["worst_geh"] == pytest.approx(7.5)
    assert result["nodes"][0]["failing_count"] == 1


def test_undefined_geh_is_excluded_from_both_sides_never_a_free_pass():
    nodes = [{"node_id": "3", "node_name": "Ghost", "geh_values": [None, None]}]
    result = vp.evaluate_critical_intersections(nodes, "3")
    assert result["nodes"][0]["passed"] is None
    assert result["nodes"][0]["compared_count"] == 0
    assert result["not_assessable_node_ids"] == ["3"]
    # Designated but not comparable is NOT a pass.
    assert result["passed"] is None


def test_nothing_designated_is_reported_as_a_non_result():
    nodes = [{"node_id": "1", "node_name": "A", "geh_values": [1.0]}]
    result = vp.evaluate_critical_intersections(nodes, "")
    assert result["designated"] is False
    assert result["passed"] is None  # never True -- the check did not run
    assert result["nodes"] == []


def test_a_designated_id_absent_from_the_data_is_disclosed():
    nodes = [{"node_id": "1", "node_name": "A", "geh_values": [1.0]}]
    result = vp.evaluate_critical_intersections(nodes, "1 42")
    assert result["missing_ids"] == ["42"]
    assert result["passed"] is True  # node 1 did pass; 42 is disclosed separately


def test_boundary_geh_five_is_a_failure_not_a_pass():
    """"achieve GEH less than 5" -- 5.0 exactly is not less than 5. Matches
    services.validation.geh_grade, where 5.0 is already 'fair', not 'good'."""
    nodes = [{"node_id": "1", "node_name": "A", "geh_values": [4.999]}]
    assert vp.evaluate_critical_intersections(nodes, "1")["passed"] is True
    nodes = [{"node_id": "1", "node_name": "A", "geh_values": [5.0]}]
    assert vp.evaluate_critical_intersections(nodes, "1")["passed"] is False

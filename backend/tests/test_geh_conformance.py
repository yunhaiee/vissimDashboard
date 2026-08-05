"""Direct semantics tests for the ONE backend GEH util (unit 1.5, D1/D2).

Two layers:
  1. ``shared/geh-conformance.json`` -- the cross-language table. The SAME file
     is consumed by ``frontend/tests/geh-conformance.test.ts`` against
     ``frontend/lib/geh.ts``, so the Python and TypeScript implementations
     cannot drift apart silently. Add a case to the JSON before changing
     either implementation.
  2. Python-only structural checks (one home, no rounding at the source).

Before this file, ``services.validation.geh`` had NO direct unit test at all
-- ``geh(0, 0) is None`` had never been asserted anywhere.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from services.validation import GEH_FAIR_LT, GEH_GOOD_LT, geh, geh_grade, geh_pass_rate

CONFORMANCE_PATH = Path(__file__).resolve().parents[2] / "shared" / "geh-conformance.json"
_FIXTURE = json.loads(CONFORMANCE_PATH.read_text(encoding="utf-8"))
_TOLERANCE = _FIXTURE["tolerance"]

# JSON cannot encode NaN/Infinity, which is exactly why the two implementations
# were free to diverge on those inputs while this fixture stayed green. The
# fixture spells them as STRINGS and both readers decode the same three
# spellings. An unknown string must RAISE -- a decoder that shrugged and
# returned the string would silently re-open the hole.
_SENTINELS = {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}
assert set(_SENTINELS) == set(_FIXTURE["non_finite_sentinels"]), (
    "the fixture declares a non-finite sentinel this reader cannot decode"
)


def _decode(value):
    if isinstance(value, str):
        if value not in _SENTINELS:
            raise ValueError(f"unknown non-finite sentinel in the fixture: {value!r}")
        return _SENTINELS[value]
    return value


def test_conformance_fixture_is_present_and_non_trivial():
    """A silently-missing fixture would make every parametrized case below
    vanish instead of fail -- the exact "test disabled itself" failure mode."""
    assert CONFORMANCE_PATH.exists(), f"missing cross-language fixture: {CONFORMANCE_PATH}"
    assert len(_FIXTURE["cases"]) >= 24
    assert len(_FIXTURE["pass_rate_cases"]) >= 6
    # The zero/zero case is the whole reason the fixture exists; never let it
    # be quietly dropped.
    assert any(c["model"] == 0 and c["observed"] == 0 and c["geh"] is None for c in _FIXTURE["cases"])
    # Nor the non-finite cases: they are the ones the JSON encoding could not
    # express, and the pair of implementations diverged on every one of them.
    sentinel_cases = [
        c for c in _FIXTURE["cases"] if isinstance(c["model"], str) or isinstance(c["observed"], str)
    ]
    assert {c["model"] for c in sentinel_cases} | {c["observed"] for c in sentinel_cases} >= set(_SENTINELS)
    assert any(
        any(isinstance(v, str) for v in c["values"]) for c in _FIXTURE["pass_rate_cases"]
    ), "expected at least one pass-rate case carrying a non-finite value"


@pytest.mark.parametrize("case", _FIXTURE["cases"], ids=[c["name"] for c in _FIXTURE["cases"]])
def test_geh_matches_cross_language_fixture(case):
    result = geh(_decode(case["model"]), _decode(case["observed"]))
    expected = _decode(case["geh"])
    if expected is None:
        assert result is None
    else:
        assert result is not None
        assert result == pytest.approx(expected, abs=_TOLERANCE, rel=_TOLERANCE)


@pytest.mark.parametrize("case", _FIXTURE["cases"], ids=[c["name"] for c in _FIXTURE["cases"]])
def test_geh_grade_matches_cross_language_fixture(case):
    assert geh_grade(geh(_decode(case["model"]), _decode(case["observed"]))) == case["grade"]


@pytest.mark.parametrize(
    "case", _FIXTURE["pass_rate_cases"], ids=[c["name"] for c in _FIXTURE["pass_rate_cases"]]
)
def test_geh_pass_rate_matches_cross_language_fixture(case):
    result = geh_pass_rate([_decode(v) for v in case["values"]], case["threshold"])
    assert result["passed"] == case["passed"]
    assert result["total"] == case["total"]
    assert result["excluded"] == case["excluded"]
    assert result["rate"] == pytest.approx(case["rate"], abs=_TOLERANCE, rel=_TOLERANCE)


# --- semantics that no fixture row can express -------------------------------


def test_zero_over_zero_is_none_not_a_passing_zero():
    """The single most load-bearing assertion in this unit: the old
    report_exports copy returned 0.0 here, which reads as a PASS."""
    assert geh(0, 0) is None
    assert geh(0.0, 0.0) is None
    assert geh_grade(geh(0, 0)) is None


def test_none_inputs_return_none_rather_than_coercing_to_zero():
    assert geh(None, 100) is None
    assert geh(100, None) is None
    assert geh(None, None) is None


def test_non_finite_inputs_are_undefined_not_poor():
    """The Python counterpart to geh-conformance.test.ts's hand-written
    non-finite test, which had none.

    BEFORE (measured): geh(nan,100)=nan, geh(inf,100)=nan, geh_grade(nan)='poor',
    geh_grade(inf)='poor' -- NaN fails BOTH ``<`` comparisons and falls out of
    the bottom of geh_grade as a confident bad grade. TypeScript returned null
    for every one of these, and no fixture row could express the divergence.
    """
    assert geh(math.nan, 100) is None
    assert geh(100, math.nan) is None
    assert geh(math.inf, 100) is None
    assert geh(100, math.inf) is None
    assert geh(-math.inf, 100) is None
    assert geh_grade(math.nan) is None
    assert geh_grade(math.inf) is None
    assert geh_grade(-math.inf) is None


def test_a_result_that_overflows_is_undefined_on_both_runtimes():
    """Finite inputs, non-finite answer. Python used to raise OverflowError
    where JS returned Infinity -- two different wrongs; both now answer None."""
    assert geh(1e200, 1) is None  # (model-observed)**2 raises OverflowError
    assert geh(1e154, 0) is None  # 2.0 * 1e308 -> inf, no exception raised


def test_pass_rate_excludes_non_finite_from_the_denominator():
    """BEFORE (measured): geh_pass_rate([1.0, nan, 12.0]) counted the NaN in the
    DENOMINATOR while reporting ``excluded: 0``; ``-inf < threshold`` was a free
    PASS."""
    result = geh_pass_rate([1.0, math.nan, 12.0])
    assert (result["passed"], result["total"], result["excluded"]) == (1, 2, 1)
    assert result["rate"] == pytest.approx(50.0)

    result = geh_pass_rate([1.0, math.inf, 4.0, -math.inf, None])
    assert (result["passed"], result["total"], result["excluded"]) == (2, 2, 3)
    assert result["rate"] == pytest.approx(100.0)


def test_source_does_not_round():
    """Each render site rounds to its own precision, so the source must not.
    A 2dp-rounded source would return 0.0 for this pair."""
    value = geh(1000.004, 1000.0)
    assert value is not None
    assert 0 < value < 1e-3
    assert value == pytest.approx(math.sqrt(2 * 0.004**2 / 2000.004), rel=1e-9)


def test_bands_are_a_single_spelling():
    assert GEH_GOOD_LT == 5.0
    assert GEH_FAIR_LT == 10.0
    assert geh_grade(4.999) == "good"
    assert geh_grade(5.0) == "fair"
    assert geh_grade(9.999) == "fair"
    assert geh_grade(10.0) == "poor"
    assert geh_grade(None) is None


def test_pass_rate_accepts_a_generator_and_does_not_exhaust_it_twice():
    result = geh_pass_rate(value for value in (1.0, None, 12.0))
    assert (result["passed"], result["total"], result["excluded"]) == (1, 2, 1)
    assert result["rate"] == pytest.approx(50.0)


def test_there_is_exactly_one_backend_geh_implementation():
    """D2: report_exports/report_builder must re-export the shared function,
    not define their own. Identity -- not "same answer" -- is the assertion."""
    from services import exporters, report_builder, report_exports

    assert report_exports._geh is geh
    assert report_builder._geh is geh
    assert exporters.geh is geh
    assert report_builder._geh_grade is geh_grade

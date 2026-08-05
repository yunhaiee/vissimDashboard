"""Tests for the SCATS→fixed-time conversion (roadmap 1.6 / R1.3b).

**Every expected value in this file is derived BY HAND in the test's own
docstring, from the formula as MRWA Appendix A states it.** That is not
ceremony. The Phase 0.3 unit shipped a transcription error (13/20 vs 14/20)
that only a re-measurement caught, and a formula module whose tests assert
``f(x) == f(x)`` proves nothing at all. A reviewer must be able to check every
number in this file with a calculator and the extract, without running the
code.

Three families of test:

1. **Worked examples** — one per formula, arithmetic shown.
2. **Published-table re-derivation** — the formulas reproduce the numbers
   Appendix A itself tabulates (Table A-5's endpoints, Table A-3's structure).
   This is the strongest check available without the full PDF: if our formula
   and their table disagree, one of us has it wrong, and it is not going to be
   them.
3. **Invariants and refusals** — the properties A.2 implies (Σt̄ᵢ = c̄, both
   frequency routes agree, a phase's parts re-add to its duration) and the
   garbage-in refusals, each asserted on its own code.
"""
from __future__ import annotations

import ast
import math
import re
from pathlib import Path
from types import ModuleType

import pytest

from services.dataimport import fixed_time as fixed_time_module
from services.dataimport.base import AdapterInfo, ImportResult, IssueSeverity
from services.dataimport.fixed_time import (
    ALL_RED_MINIMUM_S,
    EXTRACT,
    REFUSE_CODES,
    TABLE_A3_CLEARANCE_SPEED_MS,
    TABLE_A5_ENDPOINTS_M_TO_S,
    WA_DECELERATION_MS2,
    WA_REACTION_TIME_S,
    WA_STANDARD_WALK_S,
    YELLOW_MINIMUM_S,
    ConversionSettings,
    FixedTimePlan,
    all_red_time_formula_s,
    all_red_time_s,
    all_red_time_wa_s,
    convert_to_fixed_time,
    intergreen_time_s,
    interpolate_offset_s,
    pedestrian_clearance_1_s,
    pedestrian_clearance_2_s,
    pedestrian_demand_frequency,
    protection_time_red_arrow_with_flash_s,
    protection_time_timed_red_arrow_s,
    round_up_to_step,
    yellow_time_both_approaches_s,
    yellow_time_s,
)
from services.dataimport.quality import run_quality_gate
from services.dataimport.schema import (
    CoordinationData,
    PhaseData,
    ProgramSourceKind,
    Provenance,
    SignalControllerData,
    SignalProgramData,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROV = Provenance(source_file="phase_history.csv", locator="all rows", native_id="359")


# ---------------------------------------------------------------------------
# Builders — deliberately explicit, so each test's input reads like the table
# its docstring works through.
# ---------------------------------------------------------------------------


def phase(
    phase_id: str,
    occurrences: int | None,
    total_s: float | None,
    *,
    stretch: bool = False,
    intergreen_s: float | None = None,
    amber_s: float | None = None,
    all_red_s: float | None = None,
    min_green_s: float | None = None,
    frequency: float | None = None,
    sequence_index: int | None = None,
) -> PhaseData:
    return PhaseData(
        provenance=PROV,
        phase_id=phase_id,
        observation_count=occurrences,
        observed_total_duration_s=total_s,
        is_stretch_phase=stretch,
        intergreen_s=intergreen_s,
        amber_s=amber_s,
        all_red_s=all_red_s,
        min_green_s=min_green_s,
        frequency=frequency,
        sequence_index=sequence_index,
    )


def program(
    phases: list[PhaseData],
    *,
    cycles: int | None = 10,
    cycle_time_s: float | None = None,
    kind: ProgramSourceKind = ProgramSourceKind.OBSERVED_HISTORY,
    program_id: str = "observed_history",
    coordination: CoordinationData | None = None,
) -> SignalProgramData:
    return SignalProgramData(
        provenance=PROV,
        program_id=program_id,
        source_kind=kind,
        observed_cycle_count=cycles,
        cycle_time_s=cycle_time_s,
        phases=phases,
        coordination=coordination,
    )


def controller(
    programs: list[SignalProgramData],
    *,
    cycle_min_s: float | None = None,
    cycle_max_s: float | None = None,
    coordination: CoordinationData | None = None,
) -> SignalControllerData:
    return SignalControllerData(
        provenance=PROV,
        controller_id="359",
        programs=programs,
        cycle_min_s=cycle_min_s,
        cycle_max_s=cycle_max_s,
        coordination=coordination,
    )


#: The canonical worked example, used by several tests. Three phases over a
#: 10-complete-cycle window; A = stretch, C runs in 4 of 10 cycles.
#: Hand arithmetic lives in :func:`test_a2_worked_example_full_derivation`.
def worked_example_controller(**kwargs) -> SignalControllerData:
    return controller(
        [
            program(
                [
                    phase("A", 10, 403.0, stretch=True, intergreen_s=6.0),
                    phase("B", 10, 252.5, intergreen_s=5.0),
                    phase("C", 4, 101.2, intergreen_s=5.0),
                ],
                cycles=10,
            )
        ],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 0. Citation traceability — the module's own citations really do quote the
#    extract
#
# Nothing quoted here is typed out in this file. The fragments are read out of
# ``fixed_time.py``'s own ``CIT_*`` constants by introspection and asserted
# against the extract, so a mis-transcribed citation FAILS instead of being
# faithfully re-typed into the test (a test that hardcodes the same fragments
# the module hardcodes checks the extract, not the module). Discovery is by
# name prefix, so a fifteenth citation is covered the day it is written.
# ---------------------------------------------------------------------------


#: A citation reads ``<section> <location> (<extract path>) — [prose] '<quote>'``.
#: Everything up to and including the path marker is boilerplate — and it has to
#: be matched on the path, not on the first quote mark, because several
#: citations quote the extract's own SECTION TITLE before it (``§A.3 'Yellow'
#: (…) — …``).
_CITATION_BOILERPLATE_RE = re.compile(rf".*?\({re.escape(EXTRACT)}\)\s*—\s*", re.DOTALL)

#: Below this a "fragment" is punctuation or a stray word, and finding it inside
#: a 4 kB document proves nothing.
_MIN_SUBSTANTIVE_FRAGMENT_CHARS = 10


def _normalise_prose(text: str) -> str:
    """Put a quotation and the document it came from into the same shape.

    The extract hard-wraps its bullets (a sentence spanning two lines is still
    the same sentence), wears ``**bold**`` on every bullet's lead-in and
    backticks around SCATS event names; a quotation of it carries none of that.
    """
    return " ".join(text.replace("**", "").replace("`", "").split())


def _citation_constants(module: ModuleType) -> dict[str, str]:
    """Every citation the module declares, found by introspection rather than
    by a list maintained here — that is what makes a new constant covered
    automatically."""
    return {
        name: value
        for name, value in vars(module).items()
        if name.startswith("CIT_") and isinstance(value, str)
    }


def _quoted_fragments(citation: str) -> list[str]:
    """The substantive quoted text of one citation, ``...`` elisions split into
    separate fragments.

    Returns ``[]`` when the citation carries no extract-path marker or quotes
    nothing at all — both are failures, reported by constant name by the
    caller.
    """
    boilerplate = _CITATION_BOILERPLATE_RE.match(citation)
    if boilerplate is None:
        return []
    # Greedy first-quote-to-last-quote: quotations contain apostrophes of their
    # own ("the next phase's intergreen"), so a lazy match would truncate them.
    quoted = re.search(r"'(.*)'", citation[boilerplate.end() :], re.DOTALL)
    if quoted is None:
        return []
    fragments = []
    for piece in quoted.group(1).split("..."):
        # The trailing full stop is the citation's own sentence terminator, not
        # part of the extract's line ("…coordinated partner." in the citation
        # vs "…coordinated partner (Fig A-5, p.11)." in the extract).
        fragment = _normalise_prose(piece).rstrip(".").strip()
        if len(fragment) >= _MIN_SUBSTANTIVE_FRAGMENT_CHARS:
            fragments.append(fragment)
    return fragments


def _citation_traceability_failures(
    module: ModuleType, extract_text: str
) -> tuple[list[str], list[tuple[str, str]]]:
    """``(citations quoting nothing checkable, (constant, unfound fragment))``.

    Factored out of the test so the identical comparison can be pointed at a
    deliberately corrupted copy of the module to prove it bites.
    """
    unquotable: list[str] = []
    missing: list[tuple[str, str]] = []
    for name, citation in sorted(_citation_constants(module).items()):
        fragments = _quoted_fragments(citation)
        if not fragments:
            unquotable.append(name)
            continue
        missing.extend((name, f) for f in fragments if f not in extract_text)
    return unquotable, missing


def test_the_appendix_a_extract_the_module_cites_is_a_file_on_this_machine():
    """Traceability is only checkable while the source ships with the repo. If
    the extract moves or empties, every citation in ``fixed_time.py`` becomes
    unfalsifiable prose, and this is the test that says so."""
    path = REPO_ROOT / EXTRACT
    assert path.is_file(), f"{path} is missing — fixed_time.EXTRACT points nowhere"
    assert "MRWA Appendix A" in path.read_text(encoding="utf-8")


def test_module_citations_quote_the_appendix_a_extract_verbatim():
    """Every ``CIT_*`` constant in ``fixed_time.py`` claims to quote the extract;
    this reads those constants and checks the claim, quote by quote.

    That direction matters. The old version of this test listed the fragments
    itself, so the constants were never compared to anything and a formula
    citation could say ``t_r=1.5`` forever without failing. Here the asserted
    text IS the module's text: if someone edits a citation away from its source
    — or edits the extract out from under a citation — the fragment stops being
    found and the test names the constant.
    """
    extract_text = _normalise_prose((REPO_ROOT / EXTRACT).read_text(encoding="utf-8"))
    constants = _citation_constants(fixed_time_module)
    # 14 is the current inventory (§A.2 ×5, §A.3 ×5, §A.4, §A.5 ×2, impl notes).
    # Discovery finding fewer means the introspection broke, not that the
    # citations passed.
    assert len(constants) >= 14, f"only found {sorted(constants)}"
    for name, citation in sorted(constants.items()):
        assert EXTRACT in citation, f"{name} does not name the file it cites"

    unquotable, missing = _citation_traceability_failures(fixed_time_module, extract_text)
    assert unquotable == [], f"citations quoting nothing checkable: {unquotable}"
    assert missing == [], f"quoted in fixed_time.py, absent from {EXTRACT}: {missing}"


def test_published_constants_match_the_extracts_own_numbers():
    """The bare constants, re-read from the source rather than from memory.

    A.3: ``WA uses t_r=1.0, a_d=3.0``; yellow ``min 3.0 s``; all-red ``min
    1.0 s``; ``walk = 6 s WA standard``. Table A-3's five rows are
    ``40→11, 50→13, 60→16, 70→19, 80→22 m/s``.
    """
    assert (WA_REACTION_TIME_S, WA_DECELERATION_MS2) == (1.0, 3.0)
    assert YELLOW_MINIMUM_S == 3.0
    assert ALL_RED_MINIMUM_S == 1.0
    assert WA_STANDARD_WALK_S == 6.0
    assert TABLE_A3_CLEARANCE_SPEED_MS == {
        40.0: 11.0,
        50.0: 13.0,
        60.0: 16.0,
        70.0: 19.0,
        80.0: 22.0,
    }


# ---------------------------------------------------------------------------
# 1. Rounding primitive
# ---------------------------------------------------------------------------


def test_round_up_to_step_is_directional_and_exact_on_multiples():
    """Every rounding rule Appendix A states is a round-UP.

    By hand, step 0.5: 3.777… → 4.0 (7.555… ceilings to 8 half-steps);
    3.0 → 3.0 exactly (6 half-steps, not 6.5 — an exact multiple must not be
    pushed up by binary float noise); 2.5001 → 3.0.
    Step 1.0: 37.5 → 38; 4.0 → 4.

    The float-noise epsilon is 1e-9 **in step units**, so an excess under half
    a nanosecond of a step is absorbed on purpose — asserted here so the
    tolerance is a stated property rather than an accident.
    """
    assert round_up_to_step(3.7777777, 0.5) == 4.0
    assert round_up_to_step(3.0, 0.5) == 3.0
    assert round_up_to_step(2.5001, 0.5) == 3.0
    assert round_up_to_step(2.5 + 1e-12, 0.5) == 2.5  # absorbed, by design
    assert round_up_to_step(37.5, 1.0) == 38.0
    assert round_up_to_step(4.0, 1.0) == 4.0
    with pytest.raises(ValueError):
        round_up_to_step(1.0, 0.0)
    with pytest.raises(ValueError):
        round_up_to_step(math.inf, 0.5)


# ---------------------------------------------------------------------------
# 2. A.3 yellow
# ---------------------------------------------------------------------------


def test_yellow_worked_example_60kmh_level():
    """A.3: ``t_y = t_r + 0.5(V/3.6)/(a_d + 9.8G)``, WA t_r=1.0, a_d=3.0.

    By hand, V=60 km/h, G=0::

        V/3.6            = 60/3.6            = 16.666666… m/s
        0.5 * 16.666666… =                     8.333333…
        a_d + 9.8*0      =                     3.0
        8.333333… / 3.0  =                     2.777777…
        + t_r 1.0        =                     3.777777…
        round UP to 0.5  = 3.777…/0.5 = 7.555… → 8 half-steps → 4.0 s
        min 3.0 s        = not binding
    """
    assert yellow_time_s(60.0) == 4.0


def test_yellow_worked_example_40kmh_level():
    """By hand, V=40 km/h, G=0::

        40/3.6 = 11.111111… ; 0.5*11.111111… = 5.555555…
        5.555555… / 3.0 = 1.851851… ; + 1.0 = 2.851851…
        round UP to 0.5: 2.851…/0.5 = 5.703… → 6 half-steps → 3.0 s
        min 3.0 s: also 3.0 — the two agree here.
    """
    assert yellow_time_s(40.0) == 3.0


def test_yellow_minimum_binds_below_the_computed_value():
    """A.3's ``min 3.0 s``, made to bite. By hand, V=30 km/h, G=0::

        30/3.6 = 8.333333… ; 0.5*8.333333… = 4.166666…
        4.166666… / 3.0 = 1.388888… ; + 1.0 = 2.388888…
        round UP to 0.5 → 2.5 s, which the 3.0 s minimum overrides.
    """
    assert yellow_time_s(30.0) == 3.0


def test_yellow_on_a_grade_and_the_same_yellow_both_approaches_assumption():
    """Grade term ``9.8G``, decimal fraction, positive uphill.

    By hand, V=60 km/h (0.5·V/3.6 = 8.333333…), |G| = 0.04::

        downgrade  a_d + 9.8*(-0.04) = 3.0 - 0.392 = 2.608
                   8.333333…/2.608 = 3.195296… ; +1.0 = 4.195296…
                   round UP 0.5: /0.5 = 8.390… → 9 → 4.5 s
        upgrade    a_d + 9.8*(+0.04) = 3.0 + 0.392 = 3.392
                   8.333333…/3.392 = 2.456761… ; +1.0 = 3.456761…
                   round UP 0.5: /0.5 = 6.913… → 7 → 3.5 s

    ASSUMPTION A7 — A.3 says a graded site uses the same yellow both
    approaches but not which value; we take the LONGER (the downgrade's).
    """
    assert yellow_time_s(60.0, -0.04) == 4.5
    assert yellow_time_s(60.0, 0.04) == 3.5
    assert yellow_time_both_approaches_s(60.0, 0.04) == 4.5


def test_yellow_refuses_nonsense_rather_than_returning_a_number():
    """Garbage in → a refusal with a reason. A downgrade steeper than
    a_d/9.8 = 3.0/9.8 = 0.3061 drives the denominator to zero or below, where
    A.3's expression stops meaning anything."""
    with pytest.raises(ValueError):
        yellow_time_s(0.0)
    with pytest.raises(ValueError):
        yellow_time_s(-60.0)
    with pytest.raises(ValueError):
        yellow_time_s(60.0, math.nan)
    with pytest.raises(ValueError, match=r"a_d \+ 9\.8G"):
        yellow_time_s(60.0, -0.35)


# ---------------------------------------------------------------------------
# 3. A.3 all-red
# ---------------------------------------------------------------------------


def test_all_red_wa_table_and_general_formula_diverge_worked_example():
    """A.3 gives two all-red methods and WA's is the more conservative.

    By hand, clearance L_c = 27 m, posted speed 50 km/h::

        WA method (Table A-3: 50 → 13 m/s)
            27 / 13 = 2.076923…  → round UP 0.5: /0.5 = 4.153… → 5 → 2.5 s
        general formula  t_ar = 3.6·L_c/V
            3.6*27/50 = 97.2/50 = 1.944 → /0.5 = 3.888 → 4 → 2.0 s

    They differ by a full half-second, which is why the module never silently
    swaps one for the other.
    """
    assert all_red_time_wa_s(27.0, 50.0) == 2.5
    assert all_red_time_formula_s(27.0, 50.0) == 2.0
    assert all_red_time_s(27.0, 50.0) == 2.5  # WA's own method wins on-table


def test_all_red_worked_example_60kmh():
    """By hand, L_c = 20 m, posted 60 km/h (Table A-3: 60 → 16 m/s)::

        20 / 16 = 1.25 → round UP 0.5: /0.5 = 2.5 → 3 → 1.5 s
        min 1.0 s not binding.
    """
    assert all_red_time_s(20.0, 60.0) == 1.5


def test_all_red_minimum_binds():
    """By hand, L_c = 4 m, posted 80 km/h (Table A-3: 80 → 22 m/s)::

        4 / 22 = 0.181818… → round UP 0.5 → 0.5 s, which A.3's 1.0 s
        minimum overrides.
    """
    assert all_red_time_s(4.0, 80.0) == 1.0


def test_off_table_posted_speed_falls_back_to_the_published_formula():
    """ASSUMPTION A8 — Table A-3 is published data, not a curve.

    55 km/h has no row, so the WA method returns ``None`` rather than an
    interpolated m/s figure, and the convenience wrapper uses A.3's own
    general formula instead. By hand, L_c = 20 m at 55 km/h::

        3.6*20/55 = 72/55 = 1.309090… → /0.5 = 2.618… → 3 → 1.5 s
    """
    assert all_red_time_wa_s(20.0, 55.0) is None
    assert all_red_time_s(20.0, 55.0) == 1.5


def test_table_a3_speeds_are_exactly_floor_of_v_over_3_6():
    """An *observation* about how Table A-3 was built, recorded as a test so
    the encoded table can be re-checked — NOT a licence to extrapolate it
    (see ASSUMPTION A8 and the previous test).

    By hand: 40/3.6 = 11.11→11 · 50/3.6 = 13.88→13 · 60/3.6 = 16.66→16 ·
    70/3.6 = 19.44→19 · 80/3.6 = 22.22→22. All five published rows are the
    FLOOR, which is why the WA method is the conservative one: a smaller
    divisor yields a longer all-red.
    """
    for speed_kmh, speed_ms in TABLE_A3_CLEARANCE_SPEED_MS.items():
        assert speed_ms == math.floor(speed_kmh / 3.6), speed_kmh


def test_intergreen_is_the_sum_a3_states():
    """A.3: intergreen = early cut-off + yellow + all-red.
    By hand: 7.0 + 4.0 + 1.5 = 12.5 s (AWFS-style ~7 s early cut-off)."""
    assert intergreen_time_s(7.0, 4.0, 1.5) == 12.5
    assert intergreen_time_s(0.0, 4.0, 1.5) == 5.5
    with pytest.raises(ValueError):
        intergreen_time_s(-1.0, 4.0, 1.5)


# ---------------------------------------------------------------------------
# 4. A.3 pedestrian
# ---------------------------------------------------------------------------


def test_pedestrian_clearance_reproduces_table_a5_published_endpoints():
    """The strongest available check on this formula: it must regenerate the
    numbers Appendix A itself tabulates.

    A.3 states Table A-5 spans ``4–45 m → 4–38 s``. By hand::

        4 m  / 1.2 m/s = 3.333333…  → round UP to 1 s → 4 s   ✓ table's low end
        45 m / 1.2 m/s = 37.5       → round UP to 1 s → 38 s  ✓ table's high end
    """
    for crossing_m, expected_s in TABLE_A5_ENDPOINTS_M_TO_S:
        assert pedestrian_clearance_1_s(crossing_m) == expected_s
    assert TABLE_A5_ENDPOINTS_M_TO_S == ((4.0, 4.0), (45.0, 38.0))


def test_pedestrian_clearance_middle_case_and_exact_multiple():
    """By hand::

        12 m  / 1.2 = 10.0    → already whole → 10 s (not pushed to 11)
        13 m  / 1.2 = 10.8333 → round UP      → 11 s
    """
    assert pedestrian_clearance_1_s(12.0) == 10.0
    assert pedestrian_clearance_1_s(13.0) == 11.0
    with pytest.raises(ValueError):
        pedestrian_clearance_1_s(0.0)


def test_clearance_2_is_intergreen_minus_one_and_refuses_below_that():
    """A.3's WA rule: Clearance-2 = intergreen − 1 s. By hand, 6.0 − 1 = 5.0.

    An intergreen of 1 s or less would yield a zero/negative interval; the
    module refuses rather than handing a signal plan a clearance that does not
    exist.
    """
    assert pedestrian_clearance_2_s(6.0) == 5.0
    assert pedestrian_clearance_2_s(12.5) == 11.5
    with pytest.raises(ValueError):
        pedestrian_clearance_2_s(1.0)
    with pytest.raises(ValueError):
        pedestrian_clearance_2_s(0.5)


def test_protection_time_worked_examples():
    """A.3's two computable protection types, distances from the pushbutton
    pole. ASSUMPTION A9: rounded UP to 1 s (A.3 states no rounding).

    Timed red arrow, C ÷ 1.2, C = 12 m to the middle of the exit lane::
        12 / 1.2 = 10.0 → 10 s

    Red arrow + flash, max(B, 55%·A) ÷ 1.2, A = 20 m full crossing,
    B = 9 m to the median::
        0.55 * 20 = 11.0 ; max(9, 11) = 11 ; 11/1.2 = 9.166666… → 10 s

    And with a wider median, B governs instead: A = 20, B = 14::
        max(14, 11) = 14 ; 14/1.2 = 11.666666… → 12 s
    """
    assert protection_time_timed_red_arrow_s(12.0) == 10.0
    assert protection_time_red_arrow_with_flash_s(20.0, 9.0) == 10.0
    assert protection_time_red_arrow_with_flash_s(20.0, 14.0) == 12.0


def test_pedestrian_demand_frequency_worked_example():
    """A.2 p.11: demands met ÷ complete cycles. By hand, 7 ÷ 10 = 0.7.

    Uncapped: 12 demands in 10 cycles is 1.2, a walk served more than once a
    cycle — representable, not an error (the schema's own note).
    """
    assert pedestrian_demand_frequency(7, 10) == 0.7
    assert pedestrian_demand_frequency(12, 10) == 1.2
    assert pedestrian_demand_frequency(0, 10) == 0.0
    with pytest.raises(ValueError):
        pedestrian_demand_frequency(7, 0)
    with pytest.raises(ValueError):
        pedestrian_demand_frequency(-1, 10)


# ---------------------------------------------------------------------------
# 5. A.5 offsets
# ---------------------------------------------------------------------------


def test_offset_linear_interpolation_worked_example():
    """A.5's default: linear interpolation between published offset values by
    average cycle length.

    By hand, table [(80 s, 10 s), (120 s, 30 s)], average cycle 100 s::

        ratio  = (100 - 80) / (120 - 80) = 20/40 = 0.5
        offset = 10 + 0.5 * (30 - 10)    = 10 + 10 = 20.0 s

    And at 90 s: ratio 10/40 = 0.25 → 10 + 0.25*20 = 15.0 s.
    """
    table = [(80.0, 10.0), (120.0, 30.0)]
    assert interpolate_offset_s(100.0, table) == 20.0
    assert interpolate_offset_s(90.0, table) == 15.0
    assert interpolate_offset_s(80.0, table) == 10.0
    assert interpolate_offset_s(120.0, table) == 30.0


def test_offset_clamps_outside_the_published_range():
    """ASSUMPTION A10: A.5 authorises interpolation BETWEEN offset values, so
    a cycle outside the published range clamps to the nearest published
    offset instead of extrapolating a plan Main Roads never issued."""
    table = [(80.0, 10.0), (120.0, 30.0)]
    assert interpolate_offset_s(60.0, table) == 10.0
    assert interpolate_offset_s(200.0, table) == 30.0


def test_offset_refuses_an_unusable_table():
    """One point is not something to interpolate between, and two offsets for
    one cycle length is a contradiction the module will not resolve."""
    with pytest.raises(ValueError, match="at least two"):
        interpolate_offset_s(100.0, [(80.0, 10.0)])
    with pytest.raises(ValueError, match="duplicate cycle"):
        interpolate_offset_s(100.0, [(80.0, 10.0), (80.0, 12.0), (120.0, 30.0)])
    with pytest.raises(ValueError):
        interpolate_offset_s(0.0, [(80.0, 10.0), (120.0, 30.0)])


# ---------------------------------------------------------------------------
# 6. A.2 — the conversion itself
# ---------------------------------------------------------------------------


def test_a2_worked_example_full_derivation():
    """The whole A.2 conversion, worked by hand.

    Input — SCATS Phase History over a window of **n = 10 complete
    stretch-to-stretch cycles**, phase A being the stretch phase:

        ==== =========== ================== ==========
        phase occurrences total activation  intergreen
        ==== =========== ================== ==========
        A     10          403.0 s            6.0 s
        B     10          252.5 s            5.0 s
        C      4          101.2 s            5.0 s
        ==== =========== ================== ==========

    A.2 average phase time, t̄ᵢ = total activation ÷ n_cycles::

        t̄_A = 403.0 / 10 = 40.30 s
        t̄_B = 252.5 / 10 = 25.25 s
        t̄_C = 101.2 / 10 = 10.12 s

    A.2's second route for a non-every-cycle phase, t̄ᵢ = SCATS avg × fᵢ::

        SCATS avg C = 101.2 / 4 = 25.30 s
        f_C         = 4 / 10    = 0.40
        25.30 * 0.40            = 10.12 s   ✓ same number, as A.2 implies

    A.2 average cycle, c̄ = total duration ÷ n = Σ t̄ᵢ::

        (403.0 + 252.5 + 101.2) / 10 = 756.7 / 10 = 75.67 s
        40.30 + 25.25 + 10.12        = 75.67 s    ✓

    Round up (extract's 1.6 note: SCATS fractions always round up;
    ASSUMPTION A2 fixes the precision at 1 s)::

        A: 40.30 → 41 s   B: 25.25 → 26 s   C: 10.12 → 11 s
        plan cycle = 41 + 26 + 11 = 78 s
        residual   = 78 - 75.67   = 2.33 s   (ASSUMPTION A1)

    Green = rounded phase time − intergreen (ASSUMPTION A3, never re-rounded)::

        A: 41 - 6 = 35 s    B: 26 - 5 = 21 s    C: 11 - 5 = 6 s

    Start offsets, stretch phase at 0 (ASSUMPTION A4)::

        A: 0 s    B: 0 + 41 = 41 s    C: 41 + 26 = 67 s
    """
    plan = convert_to_fixed_time(worked_example_controller())

    assert plan.ok is True
    assert plan.complete_cycles == 10
    assert plan.stretch_phase_id == "A"
    assert plan.average_cycle_s == pytest.approx(75.67)
    assert plan.plan_cycle_s == 78.0
    assert plan.round_up_residual_s == pytest.approx(2.33)

    by_id = {row.phase_id: row for row in plan.rows}
    assert [row.phase_id for row in plan.rows] == ["A", "B", "C"]

    assert by_id["A"].average_phase_s == pytest.approx(40.30)
    assert by_id["B"].average_phase_s == pytest.approx(25.25)
    assert by_id["C"].average_phase_s == pytest.approx(10.12)

    assert by_id["C"].scats_average_s == pytest.approx(25.30)
    assert by_id["C"].frequency == pytest.approx(0.40)
    assert by_id["A"].frequency == 1.0

    assert [by_id[p].rounded_phase_s for p in "ABC"] == [41.0, 26.0, 11.0]
    assert [by_id[p].green_s for p in "ABC"] == [35.0, 21.0, 6.0]
    assert [by_id[p].start_offset_s for p in "ABC"] == [0.0, 41.0, 67.0]

    derived = plan.program
    assert derived is not None
    assert derived.source_kind is ProgramSourceKind.DERIVED
    assert derived.cycle_time_s == 78.0
    assert [p.phase_id for p in derived.phases] == ["A", "B", "C"]
    assert [p.sequence_index for p in derived.phases] == [0, 1, 2]
    assert [p.green_s for p in derived.phases] == [35.0, 21.0, 6.0]
    assert derived.observed_cycle_count == 10


def test_derived_program_labels_itself_as_derived_and_names_its_authority():
    """``ProgramSourceKind.DERIVED`` plus a ``derived_by`` naming A.2 is what
    stops a report calling an average a configuration — the neutral schema's
    single most important honesty flag."""
    plan = convert_to_fixed_time(worked_example_controller())
    derived = plan.program
    assert derived is not None
    assert derived.source_kind is ProgramSourceKind.DERIVED
    assert "Appendix-A A.2" in (derived.provenance.derived_by or "")
    assert derived.program_id == "observed_history_fixed_time"
    for phase_out in derived.phases:
        assert "Appendix-A A.2" in (phase_out.provenance.derived_by or "")
    # The two things A.2 cannot supply are marked absent, not guessed.
    assert "offset_s" in derived.absent
    assert "signal_group_windows" in derived.absent
    assert derived.offset_s is None
    assert derived.signal_group_windows == []


def test_a2_invariants_hold_on_the_worked_example():
    """The three properties A.2's own text implies, asserted as properties
    rather than as more restatements of the expected numbers:

    1. ``Σ t̄ᵢ = c̄`` (A.2 bullet 3, before rounding).
    2. ``t̄ᵢ = SCATS avg × fᵢ`` — A.2's two routes are one number.
    3. ``green + intergreen = the phase's derived duration`` exactly
       (ASSUMPTION A3), and the offsets tile the cycle with no gap or overlap.
    """
    plan = convert_to_fixed_time(worked_example_controller())
    rows = [row for row in plan.rows if not row.dropped]

    assert sum(row.average_phase_s for row in rows) == pytest.approx(plan.average_cycle_s)
    for row in rows:
        assert row.scats_average_s is not None and row.frequency is not None
        assert row.scats_average_s * row.frequency == pytest.approx(row.average_phase_s)
        assert row.green_s + row.intergreen_s == pytest.approx(row.rounded_phase_s)
        assert row.rounded_phase_s >= row.average_phase_s  # round UP, never down
        assert row.rounded_phase_s - row.average_phase_s < 1.0

    assert sum(row.rounded_phase_s for row in rows) == plan.plan_cycle_s
    expected_offset = 0.0
    for row in rows:
        assert row.start_offset_s == pytest.approx(expected_offset)
        expected_offset += row.rounded_phase_s
    assert expected_offset == plan.plan_cycle_s


def test_stretch_phase_is_rotated_to_offset_zero():
    """ASSUMPTION A4 / A.2 bullet 1: the stretch phase defines the cycle
    boundary, so the plan starts there even when the source lists it third.

    Same three phases as the worked example, listed C, B, A with explicit
    sequence indices 0, 1, 2 — the derived order must be A (offset 0), then
    the source order resumes: C, B.
    """
    plan = convert_to_fixed_time(
        controller(
            [
                program(
                    [
                        phase("C", 4, 101.2, intergreen_s=5.0, sequence_index=0),
                        phase("B", 10, 252.5, intergreen_s=5.0, sequence_index=1),
                        phase("A", 10, 403.0, stretch=True, intergreen_s=6.0, sequence_index=2),
                    ]
                )
            ]
        )
    )
    assert plan.ok
    assert [row.phase_id for row in plan.rows] == ["A", "C", "B"]
    assert plan.rows[0].start_offset_s == 0.0
    assert plan.rows[0].is_stretch_phase is True
    # Same phases, same times — only the order changed.
    assert plan.plan_cycle_s == 78.0
    assert plan.average_cycle_s == pytest.approx(75.67)


def test_phase_order_falls_back_to_source_order_with_an_info():
    """When sequence indices are missing or duplicated the module keeps the
    source's own record order and SAYS it did, rather than inventing a
    sequence. (The worked-example builder states no indices at all.)"""
    plan = convert_to_fixed_time(worked_example_controller())
    assert "fixed_time_phase_order_from_source_list" in plan.issue_codes(IssueSeverity.INFO)

    duplicated = convert_to_fixed_time(
        controller(
            [
                program(
                    [
                        phase("A", 10, 403.0, stretch=True, intergreen_s=6.0, sequence_index=1),
                        phase("B", 10, 252.5, intergreen_s=5.0, sequence_index=1),
                    ]
                )
            ]
        )
    )
    assert "fixed_time_phase_order_from_source_list" in duplicated.issue_codes(IssueSeverity.INFO)


def test_frequency_adjustment_scales_a_low_frequency_phase_down():
    """A.2's frequency adjustment is the point of the whole exercise, so it
    gets its own worked example free of the other phases' noise.

    Phase D runs in 2 of 20 cycles, 30.0 s each time (60.0 s total)::

        SCATS avg = 60.0 / 2  = 30.0 s   (what the phase actually gets)
        f_D       = 2 / 20    = 0.10
        t̄_D       = 30.0*0.10 = 3.0 s    (its share of an average cycle)
                  = 60.0 / 20 = 3.0 s    ✓ both routes

    A fixed-time plan gives it 3 s, not 30 s: it only runs a tenth of the
    time, and the other nine cycles' green belongs to whoever used it.
    """
    plan = convert_to_fixed_time(
        controller(
            [
                program(
                    [
                        phase("A", 20, 1000.0, stretch=True, intergreen_s=6.0),
                        phase("D", 2, 60.0, intergreen_s=1.5),
                    ],
                    cycles=20,
                )
            ]
        )
    )
    assert plan.ok
    row = {r.phase_id: r for r in plan.rows}["D"]
    assert row.scats_average_s == 30.0
    assert row.frequency == pytest.approx(0.10)
    assert row.average_phase_s == pytest.approx(3.0)
    assert row.rounded_phase_s == 3.0
    assert row.green_s == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# 7. Edge cases the extract is silent on — each flagged, none silent
# ---------------------------------------------------------------------------


def test_zero_demand_phase_is_dropped_loudly():
    """ASSUMPTION A5. A phase with 0 occurrences has fᵢ = 0, so A.2's own
    t̄ᵢ = avg × fᵢ is 0 s. A 0-second phase is not a runnable plan element, so
    it is left out — and named, because whatever it serves loses service.

    By hand: A 10×400.0 s, B 10×250.0 s, Z 0×0.0 s over 10 cycles::

        t̄_A = 40.0 → 40 s ;  t̄_B = 25.0 → 25 s ;  t̄_Z = 0.0 → dropped
        c̄ = (400+250+0)/10 = 65.0 s ; plan cycle = 40 + 25 = 65 s
    """
    plan = convert_to_fixed_time(
        controller(
            [
                program(
                    [
                        phase("A", 10, 400.0, stretch=True, intergreen_s=6.0),
                        phase("B", 10, 250.0, intergreen_s=5.0),
                        phase("Z", 0, 0.0, intergreen_s=5.0),
                    ]
                )
            ]
        )
    )
    assert plan.ok
    assert plan.average_cycle_s == pytest.approx(65.0)
    assert plan.plan_cycle_s == 65.0
    assert "fixed_time_phase_never_ran" in plan.issue_codes(IssueSeverity.WARN)

    dropped = [row for row in plan.rows if row.dropped]
    assert [row.phase_id for row in dropped] == ["Z"]
    assert dropped[0].rounded_phase_s is None
    assert "never ran" in (dropped[0].drop_reason or "")
    # The drop is visible in the table AND absent from the program.
    assert [p.phase_id for p in plan.program.phases] == ["A", "B"]
    warn = next(i for i in plan.warnings if i.code == "fixed_time_phase_never_ran")
    assert warn.context["dropped_phase_ids"] == ["Z"]


def test_degenerate_program_where_every_phase_is_zero_is_refused():
    """A plan with no runnable phase is not a plan.

    The stretch phase still has to run every cycle (10 activations in 10
    cycles) or the earlier occurrence cross-check refuses first — this input
    is a source claiming those activations took zero time.
    """
    plan = convert_to_fixed_time(
        controller([program([phase("A", 10, 0.0, stretch=True), phase("B", 0, 0.0)])])
    )
    assert plan.ok is False
    assert plan.program is None
    assert "fixed_time_all_phases_dropped" in plan.issue_codes(IssueSeverity.REFUSE)


def test_green_split_is_left_unknown_rather_than_back_computed():
    """No intergreen and no yellow+all-red pair means A.3's split is not
    derivable from these records, and A.3's formulae need approach speed,
    grade and clearance distance the neutral model does not carry.

    The phase's *duration* is still derived — only its split is unknown, and
    the schema's ``absent`` map says so in machine-readable form.
    """
    plan = convert_to_fixed_time(
        controller(
            [
                program(
                    [
                        phase("A", 10, 403.0, stretch=True),
                        phase("B", 10, 252.5, intergreen_s=5.0),
                    ]
                )
            ]
        )
    )
    assert plan.ok
    assert "fixed_time_green_split_unavailable" in plan.issue_codes(IssueSeverity.WARN)
    phase_a = plan.program.phases[0]
    assert phase_a.phase_id == "A"
    assert phase_a.green_s is None
    assert "green_s" in phase_a.absent
    # Duration is still known: 403.0/10 = 40.3 → 41 s.
    assert plan.rows[0].rounded_phase_s == 41.0


def test_intergreen_composed_from_yellow_plus_all_red_says_early_cut_off_is_missing():
    """ASSUMPTION A6. A.3 defines intergreen = early cut-off + yellow +
    all-red; early cut-off is per-signal-group and site-justified, so when
    only the pair is known the composed value omits it and says so.

    By hand: yellow 4.0 + all-red 1.5 = 5.5 s intergreen; phase time
    403.0/10 = 40.3 → 41 s; green 41 − 5.5 = 35.5 s.
    """
    plan = convert_to_fixed_time(
        controller(
            [
                program(
                    [
                        phase("A", 10, 403.0, stretch=True, amber_s=4.0, all_red_s=1.5),
                        phase("B", 10, 252.5, intergreen_s=5.0),
                    ]
                )
            ]
        )
    )
    assert plan.ok
    assert "fixed_time_intergreen_composed" in plan.issue_codes(IssueSeverity.INFO)
    assert plan.rows[0].intergreen_s == 5.5
    assert plan.rows[0].green_s == pytest.approx(35.5)


def test_half_an_intergreen_is_an_unknown_not_a_number():
    """Yellow alone (or all-red alone) is not half an intergreen. Treating it
    as one would understate every clearance at the site."""
    plan = convert_to_fixed_time(
        controller(
            [
                program(
                    [
                        phase("A", 10, 403.0, stretch=True, amber_s=4.0),
                        phase("B", 10, 252.5, intergreen_s=5.0),
                    ]
                )
            ]
        )
    )
    assert plan.ok
    assert plan.rows[0].intergreen_s is None
    assert "fixed_time_green_split_unavailable" in plan.issue_codes(IssueSeverity.WARN)


def test_green_below_min_green_warns_when_the_phase_runs_every_cycle():
    """A phase that runs EVERY cycle and still derives a green under the
    controller's own minimum points at bad input — SCATS would not have
    served less than the minimum.

    By hand: B 10×252.5 s over 10 cycles → 25.25 → 26 s, intergreen 5 →
    green 21 s, against a stated minimum green of 25 s.

    Green is NOT raised to the minimum: MRWA §5 uses min green as the
    controller states it, and silently inflating a phase would move the error
    rather than report it.
    """
    plan = convert_to_fixed_time(
        controller(
            [
                program(
                    [
                        phase("A", 10, 403.0, stretch=True, intergreen_s=6.0),
                        phase("B", 10, 252.5, intergreen_s=5.0, min_green_s=25.0),
                    ]
                )
            ]
        )
    )
    assert plan.ok
    warn = next(i for i in plan.warnings if i.code == "fixed_time_green_below_min_green")
    assert warn.context == {
        "phase_id": "B",
        "green_s": 21.0,
        "min_green_s": 25.0,
        "frequency": 1.0,
    }
    assert plan.rows[1].green_s == 21.0  # unchanged, not raised


def test_green_below_min_green_is_only_an_info_for_a_low_frequency_phase():
    """The same comparison is EXPECTED for a phase that runs sometimes: A.2
    deliberately scales it by fᵢ, so its fixed-time share is smaller than the
    green it actually receives when it runs.

    By hand: C 4×101.2 s over 10 cycles → 10.12 → 11 s, intergreen 5 →
    green 6 s, against a stated minimum green of 10 s. f_C = 0.4, so 6 < 10
    is the frequency adjustment working, not a defect.
    """
    plan = convert_to_fixed_time(
        controller(
            [
                program(
                    [
                        phase("A", 10, 403.0, stretch=True, intergreen_s=6.0),
                        phase("C", 4, 101.2, intergreen_s=5.0, min_green_s=10.0),
                    ]
                )
            ]
        )
    )
    assert plan.ok
    codes = plan.issue_codes(IssueSeverity.INFO)
    assert "fixed_time_green_below_min_green" in codes
    assert "fixed_time_green_below_min_green" not in plan.issue_codes(IssueSeverity.WARN)


def test_coordinated_site_with_a_low_frequency_phase_warns_about_fig_a5():
    """A.2 bullet 4 / Fig A-5 p.11: a coordinated site with low-frequency
    phases must have its cycles RECOUNTED at the coordinated partner. Only
    this site's history is ever supplied here, so the module warns that the
    cycle count — and every phase time divided by it — may be wrong."""
    plan = convert_to_fixed_time(
        worked_example_controller(coordination=CoordinationData(subsystem="SS12"))
    )
    assert plan.ok
    warn = next(
        i for i in plan.warnings if i.code == "fixed_time_coordinated_cycle_recount_required"
    )
    assert warn.context["low_frequency_phase_ids"] == ["C"]

    # No coordination stated -> no such warning; the module does not invent a
    # subsystem in order to have something to warn about.
    plain = convert_to_fixed_time(worked_example_controller())
    assert "fixed_time_coordinated_cycle_recount_required" not in plain.issue_codes()


def test_plan_cycle_outside_the_published_bounds_warns():
    """A.5 / the Link & Offset Plans tab states cycle bounds. The derived
    78 s plan (worked example) sits outside both a [90, 120] and a [40, 60]
    window, and the module says which side."""
    high = convert_to_fixed_time(worked_example_controller(cycle_min_s=90.0, cycle_max_s=120.0))
    assert "fixed_time_cycle_outside_published_bounds" in high.issue_codes(IssueSeverity.WARN)
    assert "below" in next(
        i.message for i in high.warnings if i.code == "fixed_time_cycle_outside_published_bounds"
    )

    low = convert_to_fixed_time(worked_example_controller(cycle_min_s=40.0, cycle_max_s=60.0))
    assert "above" in next(
        i.message for i in low.warnings if i.code == "fixed_time_cycle_outside_published_bounds"
    )

    inside = convert_to_fixed_time(worked_example_controller(cycle_min_s=60.0, cycle_max_s=120.0))
    assert "fixed_time_cycle_outside_published_bounds" not in inside.issue_codes()


def test_yellow_trap_is_declared_unchecked_on_every_successful_conversion():
    """The extract's own 1.6 note asks this module to "refuse yellow-trap
    sequences". It cannot: A.4's rule turns on an opposed FILTERING right
    turn, and ``schema.TurnType`` is geometric by design and carries no
    opposed/permissive semantics. Silence would read as a pass, so the
    conversion states plainly, every time, that it did not check."""
    plan = convert_to_fixed_time(worked_example_controller())
    notice = next(i for i in plan.infos if i.code == "fixed_time_yellow_trap_not_checkable")
    assert "NOT checked" in notice.message
    assert "A.4" in (notice.citation or "")


def test_round_up_residual_is_always_disclosed_with_its_numbers():
    """ASSUMPTION A1 is the conversion's most consequential choice, so it is
    an always-fires disclosure carrying the arithmetic, not a comment in a
    file nobody opens. Worked example: 78 − 75.67 = 2.33 s over 3 phases."""
    plan = convert_to_fixed_time(worked_example_controller())
    info = next(i for i in plan.infos if i.code == "fixed_time_round_up_residual")
    assert info.context["plan_cycle_s"] == 78.0
    assert info.context["average_cycle_s"] == pytest.approx(75.67)
    assert info.context["residual_s"] == pytest.approx(2.33)
    assert info.context["phase_count"] == 3


def test_round_up_step_is_configurable_and_changes_nothing_else():
    """ASSUMPTION A2 is a setting, not a hardcode. At a 0.5 s step the worked
    example rounds 40.30→40.5, 25.25→25.5, 10.12→10.5, giving a 76.5 s plan
    cycle and a residual of 76.5 − 75.67 = 0.83 s."""
    plan = convert_to_fixed_time(
        worked_example_controller(),
        settings=ConversionSettings(phase_time_round_up_step_s=0.5),
    )
    assert plan.ok
    assert [row.rounded_phase_s for row in plan.rows] == [40.5, 25.5, 10.5]
    assert plan.plan_cycle_s == 76.5
    assert plan.round_up_residual_s == pytest.approx(0.83)
    assert plan.average_cycle_s == pytest.approx(75.67)  # c̄ is unaffected by rounding


# ---------------------------------------------------------------------------
# 8. Refusals — garbage in, clear refusal, and NO plan
# ---------------------------------------------------------------------------


def _refuse(plan: FixedTimePlan, code: str) -> None:
    assert plan.ok is False, f"expected a refusal ({code})"
    assert plan.program is None, "a refused conversion must produce no plan at all"
    assert code in plan.issue_codes(IssueSeverity.REFUSE), plan.issue_codes()


def test_refuses_when_the_controller_has_no_observed_history_program():
    plan = convert_to_fixed_time(
        controller(
            [
                program(
                    [phase("A", 10, 403.0, stretch=True)],
                    kind=ProgramSourceKind.DECLARED,
                    program_id="declared_timings",
                )
            ]
        )
    )
    _refuse(plan, "fixed_time_no_observed_program")


def test_refuses_when_several_observed_programs_could_be_meant():
    plan = convert_to_fixed_time(
        controller(
            [
                program([phase("A", 10, 403.0, stretch=True)], program_id="am"),
                program([phase("A", 10, 500.0, stretch=True)], program_id="pm"),
            ]
        )
    )
    _refuse(plan, "fixed_time_multiple_observed_programs")
    issue = plan.refusals[0]
    assert sorted(issue.context["program_ids"]) == ["am", "pm"]


def test_refuses_a_declared_program_passed_explicitly():
    """Converting a DECLARED plan would relabel a configuration as an
    estimate; converting a DERIVED one would average an average."""
    declared = program(
        [phase("A", 10, 403.0, stretch=True)],
        kind=ProgramSourceKind.DECLARED,
        program_id="declared_timings",
    )
    plan = convert_to_fixed_time(controller([declared]), declared)
    _refuse(plan, "fixed_time_source_kind_not_observed")


def test_refuses_a_program_with_no_phases():
    plan = convert_to_fixed_time(controller([program([])]))
    _refuse(plan, "fixed_time_no_phases")


@pytest.mark.parametrize("cycles", [None, 0])
def test_refuses_an_unusable_complete_cycle_count(cycles):
    """A.2 divides everything by n, and the extract's 1.6 note demands
    COMPLETE stretch-to-stretch cycles only."""
    plan = convert_to_fixed_time(
        controller([program([phase("A", 10, 403.0, stretch=True)], cycles=cycles)])
    )
    _refuse(plan, "fixed_time_cycle_count_unusable")


def test_refuses_when_no_stretch_phase_is_identified():
    plan = convert_to_fixed_time(
        controller([program([phase("A", 10, 403.0), phase("B", 10, 252.5)])])
    )
    _refuse(plan, "fixed_time_stretch_phase_unidentified")


def test_refuses_when_two_phases_claim_to_be_the_stretch_phase():
    plan = convert_to_fixed_time(
        controller(
            [program([phase("A", 10, 403.0, stretch=True), phase("B", 10, 252.5, stretch=True)])]
        )
    )
    _refuse(plan, "fixed_time_stretch_phase_ambiguous")
    assert plan.refusals[0].context["flagged"] == ["A", "B"]


def test_refuses_when_the_stretch_occurrences_disagree_with_the_cycle_count():
    """A.2 defines n as the stretch-phase starts, so 11 starts against a
    stated 10 complete cycles is the source contradicting itself — and the
    off-by-one is the classic one, so the message names it: A.2's calculation
    period runs to the first stretch start AFTER the window, which bounds the
    period rather than sitting inside it."""
    plan = convert_to_fixed_time(
        controller([program([phase("A", 11, 443.0, stretch=True)], cycles=10)])
    )
    _refuse(plan, "fixed_time_stretch_occurrence_mismatch")
    message = plan.refusals[0].message
    assert "calculation period" in message
    assert plan.refusals[0].context == {"stretch_occurrences": 11, "observed_cycle_count": 10}


def test_missing_stretch_occurrences_warn_but_do_not_refuse():
    """An unstated occurrence count means the cross-check could not run —
    that is a warning about what we could not verify, not a contradiction."""
    plan = convert_to_fixed_time(
        controller([program([phase("A", None, 403.0, stretch=True, intergreen_s=6.0)])])
    )
    assert plan.ok
    assert "fixed_time_stretch_occurrences_unverified" in plan.issue_codes(IssueSeverity.WARN)
    assert plan.rows[0].frequency is None
    assert "frequency" in plan.program.phases[0].absent


def test_refuses_a_phase_with_no_observed_total():
    plan = convert_to_fixed_time(
        controller([program([phase("A", 10, 403.0, stretch=True), phase("B", 10, None)])])
    )
    _refuse(plan, "fixed_time_phase_total_missing")


def test_refuses_a_non_finite_observed_total():
    plan = convert_to_fixed_time(
        controller([program([phase("A", 10, math.inf, stretch=True)])])
    )
    _refuse(plan, "fixed_time_phase_total_invalid")


def test_refuses_a_stated_frequency_that_contradicts_the_counts():
    """A.2 defines fᵢ = occurrences ÷ cycles. A source stating 0.5 while its
    own counts give 4/10 = 0.4 is not a rounding difference, it is a
    disagreement about how often the phase ran."""
    plan = convert_to_fixed_time(
        controller(
            [
                program(
                    [
                        phase("A", 10, 403.0, stretch=True),
                        phase("C", 4, 101.2, frequency=0.5),
                    ]
                )
            ]
        )
    )
    _refuse(plan, "fixed_time_frequency_contradicts_counts")
    context = plan.refusals[0].context
    assert context["stated_frequency"] == 0.5
    assert context["counted_frequency"] == pytest.approx(0.4)


def test_refuses_a_stated_cycle_that_contradicts_the_history():
    """The worked example's own totals give c̄ = 75.67 s. A stated 100 s is
    24.33 s away, well past the tolerance max(2.0 s, 5% of 75.67 = 3.78 s)."""
    plan = convert_to_fixed_time(
        controller(
            [
                program(
                    [
                        phase("A", 10, 403.0, stretch=True, intergreen_s=6.0),
                        phase("B", 10, 252.5, intergreen_s=5.0),
                        phase("C", 4, 101.2, intergreen_s=5.0),
                    ],
                    cycle_time_s=100.0,
                )
            ]
        )
    )
    _refuse(plan, "fixed_time_stated_cycle_contradicts_history")
    context = plan.refusals[0].context
    assert context["recomputed_cycle_s"] == pytest.approx(75.67)
    assert context["tolerance_s"] == pytest.approx(3.7835)


def test_a_small_stated_cycle_difference_only_warns():
    """Inside the tolerance the recomputed c̄ is used and the difference is
    reported: 76.5 stated vs 75.67 recomputed is 0.83 s, under the 3.78 s
    tolerance."""
    plan = convert_to_fixed_time(
        controller(
            [
                program(
                    [
                        phase("A", 10, 403.0, stretch=True, intergreen_s=6.0),
                        phase("B", 10, 252.5, intergreen_s=5.0),
                        phase("C", 4, 101.2, intergreen_s=5.0),
                    ],
                    cycle_time_s=76.5,
                )
            ]
        )
    )
    assert plan.ok
    assert "fixed_time_stated_cycle_differs_slightly" in plan.issue_codes(IssueSeverity.WARN)
    assert plan.average_cycle_s == pytest.approx(75.67)


def test_an_exactly_agreeing_stated_cycle_produces_no_noise():
    """c̄ is a sum of quotients, so an exact agreement can still land a few
    ulps apart; a warning about 3e-14 seconds would be noise."""
    plan = convert_to_fixed_time(
        controller(
            [
                program(
                    [
                        phase("A", 10, 403.0, stretch=True, intergreen_s=6.0),
                        phase("B", 10, 252.5, intergreen_s=5.0),
                        phase("C", 4, 101.2, intergreen_s=5.0),
                    ],
                    cycle_time_s=75.67,
                )
            ]
        )
    )
    assert plan.ok
    assert "fixed_time_stated_cycle_differs_slightly" not in plan.issue_codes()


def test_refuses_when_the_intergreen_swallows_the_whole_phase():
    """A phase whose clearance is at least its whole derived duration leaves
    no green. Either the clearance times or the history is wrong; shaving one
    to fit the other is exactly the silent-wrong-number failure this layer
    exists to prevent.

    By hand: C 4×101.2 s over 10 cycles → 10.12 → 11 s, against a stated
    intergreen of 12 s.
    """
    plan = convert_to_fixed_time(
        controller(
            [
                program(
                    [
                        phase("A", 10, 403.0, stretch=True, intergreen_s=6.0),
                        phase("C", 4, 101.2, intergreen_s=12.0),
                    ]
                )
            ]
        )
    )
    _refuse(plan, "fixed_time_intergreen_exceeds_phase")
    assert plan.refusals[0].context == {
        "phase_id": "C",
        "intergreen_s": 12.0,
        "phase_time_s": 11.0,
    }


# ---------------------------------------------------------------------------
# 9. Structural integrity of the module's own contract
# ---------------------------------------------------------------------------


MODULE_SOURCE = (
    REPO_ROOT / "backend" / "services" / "dataimport" / "fixed_time.py"
).read_text(encoding="utf-8")


#: REFUSE emission sites in ``fixed_time.py`` today. A scanner that silently
#: stopped matching would otherwise "pass" on an empty set.
_REFUSE_EMISSION_SITE_BASELINE = 14


def _is_refuse_severity(node: ast.AST) -> bool:
    """``IssueSeverity.REFUSE``, however the enum was imported."""
    if not isinstance(node, ast.Attribute) or node.attr != "REFUSE":
        return False
    owner = node.value
    if isinstance(owner, ast.Name):
        return owner.id == "IssueSeverity"
    if isinstance(owner, ast.Attribute):
        return owner.attr == "IssueSeverity"
    return False


def _refuse_bound_names(tree: ast.AST) -> set[str]:
    """Names bound straight-line to ``IssueSeverity.REFUSE`` (``sev =
    IssueSeverity.REFUSE`` and then ``_issue(sev, …)``) — one of the shapes a
    source-text regex cannot see."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_refuse_severity(node.value):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if _is_refuse_severity(node.value) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
        elif isinstance(node, ast.NamedExpr) and _is_refuse_severity(node.value):
            names.add(node.target.id)
    return names


def _refuse_emissions(source: str) -> tuple[list[str], int]:
    """``(codes, site count)`` for every REFUSE issue the module constructs.

    Read off the syntax tree, not the source text: the regex this replaced saw
    only ``IssueSeverity.REFUSE,`` followed by a newline and a literal, so a
    single-line emission and a severity bound to a local both slipped past it,
    and either lets a real refusal drift out of :data:`REFUSE_CODES` unnoticed.

    A call counts when its first positional argument resolves to
    ``IssueSeverity.REFUSE`` — directly or through such a local — and one of its
    arguments is a ``fixed_time_*`` string literal, which is the code.
    """
    tree = ast.parse(source)
    bound = _refuse_bound_names(tree)
    codes: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        severity = node.args[0]
        if not (
            _is_refuse_severity(severity)
            or (isinstance(severity, ast.Name) and severity.id in bound)
        ):
            continue
        arguments = [*node.args, *(kw.value for kw in node.keywords)]
        for argument in arguments:
            if (
                isinstance(argument, ast.Constant)
                and isinstance(argument.value, str)
                and argument.value.startswith("fixed_time_")
            ):
                codes.append(argument.value)
                break
    return codes, len(codes)


def test_refuse_codes_is_the_complete_and_exact_set_of_refusals():
    """``REFUSE_CODES`` is documentation only if it can drift. Every code it
    lists must be emitted somewhere in the module, and every code emitted at
    REFUSE tier must be listed — so the docstring's "these produce no plan at
    all" claim stays true."""
    codes, sites = _refuse_emissions(MODULE_SOURCE)
    assert sites >= _REFUSE_EMISSION_SITE_BASELINE, (
        f"only {sites} REFUSE emission sites found (baseline "
        f"{_REFUSE_EMISSION_SITE_BASELINE}) — the scanner is broken, or refusals "
        "were deleted"
    )
    assert set(codes) == set(REFUSE_CODES), {
        "emitted_but_unlisted": sorted(set(codes) - set(REFUSE_CODES)),
        "listed_but_never_emitted": sorted(set(REFUSE_CODES) - set(codes)),
    }


def test_every_issue_the_module_emits_carries_a_traceable_citation():
    """A refusal that cannot defend itself is an obstacle, not a control. Run
    the conversion across success and failure paths and require every issue to
    cite the Appendix A extract by path."""
    plans = [
        convert_to_fixed_time(worked_example_controller(coordination=CoordinationData(subsystem="S"))),
        convert_to_fixed_time(worked_example_controller(cycle_min_s=90.0)),
        convert_to_fixed_time(controller([program([])])),
        convert_to_fixed_time(
            controller([program([phase("A", 10, 403.0), phase("B", 10, 252.5)])])
        ),
        convert_to_fixed_time(
            controller([program([phase("A", 11, 443.0, stretch=True)], cycles=10)])
        ),
    ]
    seen = 0
    for plan in plans:
        for issue in plan.issues:
            seen += 1
            assert issue.citation, f"{issue.code} has no citation"
            assert EXTRACT in issue.citation, f"{issue.code} cites {issue.citation!r}"
    assert seen >= 10


def test_module_imports_nothing_com_or_vissim_coupled():
    """Non-COM by construction (this unit is testable with no simulator on the
    machine) — asserted on the import graph, not merely intended. Prose may
    mention Vissim; an import statement may not."""
    imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", MODULE_SOURCE, re.MULTILINE)
    banned = [
        name
        for name in imports
        if any(token in name.lower() for token in ("vissim", "win32com", "pythoncom", "comtypes"))
    ]
    assert banned == []
    # It also stays inside its own package plus the stdlib + pydantic.
    external = [name for name in imports if not name.startswith(".")]
    assert set(external) <= {"__future__", "math", "dataclasses", "typing", "pydantic"}, external


def test_derived_plan_passes_the_data_quality_gate_it_will_face_next():
    """Cross-module coherence: the plan this module emits must survive
    ``quality.py``'s own signal checks, including
    ``check_program_cycle_consistency`` (phase durations must sum to the
    stated cycle). The worked example's 35+6 + 21+5 + 6+5 = 78 s does, exactly
    — which is the point of making the plan cycle the sum of the rounded phase
    times rather than the rounded average (ASSUMPTION A1)."""
    plan = convert_to_fixed_time(worked_example_controller())
    assert plan.program is not None
    result = ImportResult(
        adapter=AdapterInfo(name="test", region="AU-WA", source_system="SCATS", version="0"),
        controllers=[controller([plan.program])],
    )
    issues = run_quality_gate(result)
    refusals = [i.code for i in issues if i.severity is IssueSeverity.REFUSE]
    assert refusals == []
    assert "program_phase_sum_mismatch" not in [i.code for i in issues]
    assert "program_phase_sum_rounding" not in [i.code for i in issues]

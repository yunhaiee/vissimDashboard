"""Tests for roadmap unit 1.6 / R1.3b — both halves.

Sections 0-9 cover the SCATS→fixed-time conversion (``fixed_time.py``, MRWA
Appendix A). Sections 10-15 cover the LinSig re-entry (``linsig.py``, MRWA
Operational Modelling Guidelines §3): degree of saturation and bonus green,
deferred in 2026-08-05's DECISIONS entry until Main Roads' LinSig chapter
arrived, and built the day it did. They share a test file because they are one
unit and because several of their guarantees are only checkable ACROSS the two
— that the weighted-average rule is one implementation and not two, that the
two authorities' rounding rules are kept apart, and that their issue-code
namespaces do not overlap.

**Every expected value in this file is derived BY HAND in the test's own
docstring, from the formula as MRWA states it.** That is not
ceremony. The Phase 0.3 unit shipped a transcription error (13/20 vs 14/20)
that only a re-measurement caught, and a formula module whose tests assert
``f(x) == f(x)`` proves nothing at all. A reviewer must be able to check every
number in this file with a calculator and the extract, without running the
code.

Three families of test:

1. **Worked examples** — one per formula, arithmetic shown.
2. **Published-table re-derivation** — the formulas reproduce the numbers MRWA
   itself tabulates (Appendix A's Table A-5 endpoints and Table A-3 structure;
   the LinSig chapter's Tables 3-3 and 3-4 in full, every printed cell). This
   is the strongest check available without the full PDF: if our formula and
   their table disagree, one of us has it wrong, and it is not going to be
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
from services.dataimport import linsig as linsig_module
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
    average_phase_time_s,
    convert_to_fixed_time,
    intergreen_time_s,
    interpolate_offset_s,
    pedestrian_clearance_1_s,
    pedestrian_clearance_2_s,
    pedestrian_demand_frequency,
    phase_frequency,
    protection_time_red_arrow_with_flash_s,
    protection_time_timed_red_arrow_s,
    round_up_to_step,
    scats_average_phase_time_s,
    yellow_time_both_approaches_s,
    yellow_time_s,
)
from services.dataimport.linsig import (
    DOS_VALIDATION_MAXIMUM_PCT,
    PCU_LENGTH_M,
    PRC_PRACTICAL_OPERATING_LEVEL_PCT,
    SECONDS_PER_HOUR,
    TABLE_3_3_N_CYCLES,
    TABLE_3_3_PUBLISHED,
    TABLE_3_3_PUBLISHED_TOTALS,
    TABLE_3_4_PUBLISHED_BONUS,
    TABLE_3_4_PUBLISHED_CASES,
    TABLE_3_4_PUBLISHED_WEIGHTED,
    GivewayPeriod,
    GreenKey,
    LaneSaturation,
    LinSigSettings,
    PhaseObservation,
    PhaseSequenceCase,
    QueueLane,
    SignalGroupSpec,
    ValidationDataBasis,
    actual_average_phase_time_s,
    bonus_green_protected_case_s,
    bonus_green_s,
    bonus_green_unprotected_case_s,
    build_infrequent_phase_table,
    compute_bonus_green,
    convert_mean_maximum_queues,
    cycle_time_to_model_s,
    degree_of_saturation_pct,
    demand_frequency,
    mean_maximum_queue_m,
    mean_maximum_queue_pcu,
    practical_reserve_capacity_pct,
    validate_degree_of_saturation,
    weighted_average_green_s,
    weighted_average_phase_time_s,
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
#: (…) — …``). Built per extract path: two modules, two source documents, one
#: comparison.
def _citation_boilerplate_re(extract_path: str) -> re.Pattern[str]:
    return re.compile(rf".*?\({re.escape(extract_path)}\)\s*—\s*", re.DOTALL)


#: Below this a "fragment" is punctuation or a stray word, and finding it inside
#: a 4 kB document proves nothing.
_MIN_SUBSTANTIVE_FRAGMENT_CHARS = 10

#: Markdown blockquote markers, which the LinSig extract wears on every line of
#: quoted document text.
_BLOCKQUOTE_RE = re.compile(r"^[ \t]*>+[ \t]?", re.MULTILINE)


def _normalise_prose(text: str) -> str:
    """Put a quotation and the document it came from into the same shape.

    The extracts hard-wrap their bullets (a sentence spanning two lines is
    still the same sentence), wear ``**bold**`` on every bullet's lead-in,
    backticks around SCATS event names, and — in the LinSig extract, which
    quotes the guidelines as markdown blockquotes — a ``>`` at the head of
    every quoted line. A quotation of any of it carries none of that.

    The blockquote strip is load-bearing rather than cosmetic: 19 of the
    LinSig citations' fragments span a wrapped blockquote line, so without it
    a ``>`` lands in the middle of the sentence and the fragment can only be
    "found" by chopping every citation at the extract's line breaks — which
    would make the citations track the extract's *formatting* instead of its
    text.
    """
    text = _BLOCKQUOTE_RE.sub("", text)
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


def _quoted_fragments(citation: str, extract_path: str) -> list[str]:
    """The substantive quoted text of one citation, ``...`` elisions split into
    separate fragments.

    Returns ``[]`` when the citation carries no extract-path marker or quotes
    nothing at all — both are failures, reported by constant name by the
    caller.
    """
    boilerplate = _citation_boilerplate_re(extract_path).match(citation)
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
    module: ModuleType, extract_path: str, extract_text: str
) -> tuple[list[str], list[tuple[str, str]]]:
    """``(citations quoting nothing checkable, (constant, unfound fragment))``.

    Factored out of the test so the identical comparison can be pointed at a
    deliberately corrupted copy of the module to prove it bites.
    """
    unquotable: list[str] = []
    missing: list[tuple[str, str]] = []
    for name, citation in sorted(_citation_constants(module).items()):
        fragments = _quoted_fragments(citation, extract_path)
        if not fragments:
            unquotable.append(name)
            continue
        missing.extend((name, f) for f in fragments if f not in extract_text)
    return unquotable, missing


#: ``(module, its extract, a marker proving the right file, inventory floor)``.
#: Unit 1.6 answers to two documents — Appendix A for the SCATS→fixed-time
#: conversion, the OMG LinSig chapter for the degree-of-saturation and
#: bonus-green items deferred until it arrived — and each module names exactly
#: one of them. The inventory floors exist because discovery finding FEWER
#: constants would otherwise read as a pass.
_CITATION_SOURCES = [
    pytest.param(fixed_time_module, "MRWA Appendix A", 14, id="appendix_a"),
    pytest.param(linsig_module, "MRWA OMG v2.0 §3 (LinSig)", 27, id="linsig"),
]


@pytest.mark.parametrize(("module", "marker", "minimum_constants"), _CITATION_SOURCES)
def test_the_extract_a_module_cites_is_a_file_on_this_machine(module, marker, minimum_constants):
    """Traceability is only checkable while the source ships with the repo. If
    an extract moves or empties, every citation in the module that cites it
    becomes unfalsifiable prose, and this is the test that says so."""
    path = REPO_ROOT / module.EXTRACT
    assert path.is_file(), f"{path} is missing — {module.__name__}.EXTRACT points nowhere"
    assert marker in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(("module", "marker", "minimum_constants"), _CITATION_SOURCES)
def test_module_citations_quote_their_extract_verbatim(module, marker, minimum_constants):
    """Every ``CIT_*`` constant claims to quote its module's extract; this reads
    those constants and checks the claim, quote by quote.

    That direction matters. The old version of this test listed the fragments
    itself, so the constants were never compared to anything and a formula
    citation could say ``t_r=1.5`` forever without failing. Here the asserted
    text IS the module's text: if someone edits a citation away from its source
    — or edits the extract out from under a citation — the fragment stops being
    found and the test names the constant.

    Both of unit 1.6's source documents are covered: ``fixed_time.py`` against
    the Appendix A extract (14 constants — §A.2 ×5, §A.3 ×5, §A.4, §A.5 ×2,
    impl notes) and ``linsig.py`` against the LinSig-tables extract (27 — the
    Table 3-3/3-4 formulae, the demand-dependency method, DoS, PRC and MMQ).
    """
    extract_text = _normalise_prose((REPO_ROOT / module.EXTRACT).read_text(encoding="utf-8"))
    constants = _citation_constants(module)
    assert len(constants) >= minimum_constants, f"only found {sorted(constants)}"
    for name, citation in sorted(constants.items()):
        assert module.EXTRACT in citation, f"{name} does not name the file it cites"

    unquotable, missing = _citation_traceability_failures(module, module.EXTRACT, extract_text)
    assert unquotable == [], f"citations quoting nothing checkable: {unquotable}"
    assert missing == [], f"quoted in {module.__name__}, absent from {module.EXTRACT}: {missing}"


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
LINSIG_SOURCE = (
    REPO_ROOT / "backend" / "services" / "dataimport" / "linsig.py"
).read_text(encoding="utf-8")


#: REFUSE emission sites in each module today. A scanner that silently stopped
#: matching would otherwise "pass" on an empty set. Sites, not codes: linsig's
#: 24 sites carry 23 distinct codes, because ``linsig_duplicate_lane_id`` is
#: emitted by both the DoS and the MMQ entry points.
_REFUSE_EMISSION_SITE_BASELINE = 14
_LINSIG_REFUSE_EMISSION_SITE_BASELINE = 24


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


def _refuse_emissions(source: str, prefix: str) -> tuple[list[str], int]:
    """``(codes, site count)`` for every REFUSE issue the module constructs.

    Read off the syntax tree, not the source text: the regex this replaced saw
    only ``IssueSeverity.REFUSE,`` followed by a newline and a literal, so a
    single-line emission and a severity bound to a local both slipped past it,
    and either lets a real refusal drift out of :data:`REFUSE_CODES` unnoticed.

    A call counts when its first positional argument resolves to
    ``IssueSeverity.REFUSE`` — directly or through such a local — and one of its
    arguments is a ``<prefix>*`` string literal, which is the code. The prefix
    is a parameter because unit 1.6 now has two modules with two code
    namespaces (``fixed_time_*`` and ``linsig_*``); scanning either for the
    other's prefix would find nothing and "pass".
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
                and argument.value.startswith(prefix)
            ):
                codes.append(argument.value)
                break
    return codes, len(codes)


@pytest.mark.parametrize(
    ("source", "prefix", "declared", "baseline"),
    [
        pytest.param(
            MODULE_SOURCE,
            "fixed_time_",
            REFUSE_CODES,
            _REFUSE_EMISSION_SITE_BASELINE,
            id="fixed_time",
        ),
        pytest.param(
            LINSIG_SOURCE,
            "linsig_",
            linsig_module.REFUSE_CODES,
            _LINSIG_REFUSE_EMISSION_SITE_BASELINE,
            id="linsig",
        ),
    ],
)
def test_refuse_codes_is_the_complete_and_exact_set_of_refusals(source, prefix, declared, baseline):
    """``REFUSE_CODES`` is documentation only if it can drift. Every code it
    lists must be emitted somewhere in the module, and every code emitted at
    REFUSE tier must be listed — so each module's "these produce no plan/table
    at all" claim stays true."""
    codes, sites = _refuse_emissions(source, prefix)
    assert sites >= baseline, (
        f"only {sites} REFUSE emission sites found (baseline {baseline}) — the scanner is "
        "broken, or refusals were deleted"
    )
    assert set(codes) == set(declared), {
        "emitted_but_unlisted": sorted(set(codes) - set(declared)),
        "listed_but_never_emitted": sorted(set(declared) - set(codes)),
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


@pytest.mark.parametrize(
    ("source", "allowed"),
    [
        pytest.param(
            MODULE_SOURCE,
            {"__future__", "math", "dataclasses", "typing", "pydantic"},
            id="fixed_time",
        ),
        pytest.param(
            LINSIG_SOURCE,
            {"__future__", "math", "dataclasses", "enum", "typing", "pydantic"},
            id="linsig",
        ),
    ],
)
def test_module_imports_nothing_com_or_vissim_coupled(source, allowed):
    """Non-COM by construction (this unit is testable with no simulator on the
    machine) — asserted on the import graph, not merely intended. Prose may
    mention Vissim; an import statement may not. ``linsig.py`` gets the same
    treatment for the same reason: LinSig is software it computes inputs and
    checks FOR, never talks to."""
    imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", source, re.MULTILINE)
    banned = [
        name
        for name in imports
        if any(
            token in name.lower()
            for token in ("vissim", "win32com", "pythoncom", "comtypes", "linsig.com")
        )
    ]
    assert banned == []
    # Each also stays inside its own package plus the stdlib + pydantic.
    external = [name for name in imports if not name.startswith(".")]
    assert set(external) <= allowed, external


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


# ===========================================================================
# 10. LinSig re-entry (OMG v2.0 §3) — Table 3-3, the weighted-average cycle
#
# Unit 1.6's deferred half, in ``services/dataimport/linsig.py``. Same three
# families of test as above, and the same rule: every expected number is
# derived BY HAND in the test's own docstring. The published-table family is
# unusually strong here — the LinSig chapter prints both of its worked tables
# in full, so the tests re-derive Main Roads' own numbers rather than our own.
# ===========================================================================


def table_3_3_observations(minimums: dict[str, float] | None = None) -> list[PhaseObservation]:
    """Table 3-3's input columns, read out of the published constant so the
    test inputs cannot drift from the table they claim to reproduce."""
    return [
        PhaseObservation(
            phase_id=phase_id,
            occurrences=occurrences,
            total_phase_time_s=total,
            minimum_phase_time_s=(minimums or {}).get(phase_id),
        )
        for phase_id, occurrences, total, _, _ in TABLE_3_3_PUBLISHED
    ]


def test_linsig_published_constants_match_the_extracts_own_numbers():
    """The bare constants, re-read from the source rather than from memory.

    §3.11.3: "1 PCU = 7.35 m". §3.10.1: "the DoS should not exceed 100 per
    cent". §3.11.2.1: "a practical operating level of 90%". Table 3-3's total
    row: 3600 s / 105 s / 100 s over 36 cycles.
    """
    assert PCU_LENGTH_M == 7.35
    assert DOS_VALIDATION_MAXIMUM_PCT == 100.0
    assert PRC_PRACTICAL_OPERATING_LEVEL_PCT == 90.0
    assert SECONDS_PER_HOUR == 3600.0
    assert TABLE_3_3_N_CYCLES == 36
    assert TABLE_3_3_PUBLISHED_TOTALS == (3600.0, 105, 100)
    assert [row[0] for row in TABLE_3_3_PUBLISHED] == ["A", "B", "C"]


def test_table_3_3_worked_example_reproduces_the_published_table():
    """Table 3-3 (p.105), re-derived. The document's own four formulae::

        demand frequency       = occurrences ÷ cycles
        actual average phase   = total phase time in an hour ÷ occurrences
        weighted average phase = total phase time in an hour ÷ cycles

    By hand, over n = 36 cycles::

        A  36/36 = 100 %   2223/36 = 61.75      2223/36 = 61.75
        B  36/36 = 100 %   1113/36 = 30.91666…  1113/36 = 30.91666…
        C  22/36 =  61.1%   264/22 = 12.0        264/36 =  7.33333…

        total phase time     2223 + 1113 + 264 = 3600 s  ✓ one hour
        actual-average cycle 61.75 + 30.91666… + 12       = 104.66666…
        weighted-average     61.75 + 30.91666… + 7.33333… = 100.0 exactly

    The document PRINTS those columns as whole seconds — 62 / 31 / 12 and
    62 / 31 / 7, totalling 105 and 100 — and this test reproduces the printed
    integers by nearest-rounding at the point of display only. See ASSUMPTION
    A11 and the next test: the module itself rounds nothing.
    """
    table = build_infrequent_phase_table(table_3_3_observations(), TABLE_3_3_N_CYCLES)
    assert table.ok is True
    rows = {row.phase_id: row for row in table.rows}

    assert rows["A"].demand_frequency == 1.0
    assert rows["B"].demand_frequency == 1.0
    assert rows["C"].demand_frequency == pytest.approx(22 / 36)

    assert rows["A"].actual_average_phase_time_s == pytest.approx(61.75)
    assert rows["B"].actual_average_phase_time_s == pytest.approx(1113 / 36)
    assert rows["C"].actual_average_phase_time_s == 12.0

    assert rows["A"].weighted_average_phase_time_s == pytest.approx(61.75)
    assert rows["B"].weighted_average_phase_time_s == pytest.approx(30.916666666)
    assert rows["C"].weighted_average_phase_time_s == pytest.approx(7.333333333)

    # …and every printed cell of the published table comes back out.
    for phase_id, _, _, printed_actual, printed_weighted in TABLE_3_3_PUBLISHED:
        assert round(rows[phase_id].actual_average_phase_time_s) == printed_actual, phase_id
        assert round(rows[phase_id].weighted_average_phase_time_s) == printed_weighted, phase_id

    published_total, published_actual_cycle, published_weighted_cycle = TABLE_3_3_PUBLISHED_TOTALS
    assert table.total_phase_time_s == published_total
    assert round(table.actual_average_cycle_s) == published_actual_cycle
    assert round(table.weighted_average_cycle_s) == published_weighted_cycle
    assert table.actual_average_cycle_s == pytest.approx(104.6666666)
    assert table.weighted_average_cycle_s == pytest.approx(100.0)


def test_the_cycle_to_model_is_100_not_105():
    """p.105's load-bearing sentence: "The cycle time of 100 seconds, not 105
    seconds, should be modelled replicating the correct number of cycles in an
    hour (36 cycles)."

    By hand: 3600 ÷ 36 = 100.0 s. The 105 s is what you get by summing the
    ACTUAL average phase times (62 + 31 + 12), and it is wrong because C's
    12 s belongs to only 22 of the 36 cycles.

    The 105 is not hidden — it is computed, carried and named, so a reviewer
    can see the number the guideline is warning about.
    """
    table = build_infrequent_phase_table(table_3_3_observations(), TABLE_3_3_N_CYCLES)
    assert table.cycle_time_to_model_s == 100.0
    assert table.cycle_time_to_model_s == cycle_time_to_model_s(36)
    assert table.actual_average_cycle_s == pytest.approx(104.6666666)
    assert round(table.actual_average_cycle_s) == 105

    info = next(
        i
        for i in table.infos
        if i.code == "linsig_actual_average_cycle_is_not_the_modelled_cycle"
    )
    assert info.context["cycle_time_to_model_s"] == 100.0
    assert info.context["actual_average_cycle_s"] == pytest.approx(104.6666666)
    assert info.context["n_cycles"] == 36
    assert "100 seconds, not 105 seconds" in info.message


def test_the_two_routes_to_the_modelled_cycle_agree_when_the_totals_fill_the_hour():
    """Σ(weighted average phase time) and (modelled period ÷ cycles) are the
    same number, because Σ(totalᵢ ÷ n) = (Σ totalᵢ) ÷ n. By hand:
    61.75 + 30.91666… + 7.33333… = 100.0 = 3600 ÷ 36.

    That identity is exactly why the module REFUSES totals that do not fill the
    modelled period: past that point the two routes disagree and neither can be
    said to be the cycle.
    """
    table = build_infrequent_phase_table(table_3_3_observations(), TABLE_3_3_N_CYCLES)
    assert table.weighted_average_cycle_s == pytest.approx(table.cycle_time_to_model_s)
    assert sum(row.weighted_average_phase_time_s for row in table.rows) == pytest.approx(100.0)


def test_the_printed_table_rounds_to_nearest_but_the_module_rounds_not_at_all():
    """ASSUMPTION A11, made to bite.

    Table 3-3 prints C's weighted average of 264/36 = 7.333… as **7** — nearest.
    §3.5.15.2.3 rounds 61 % × 6 s = 3.66 to **4** — up. Appendix A's blanket
    rule is round-up, which would make this cell **8** and the modelled cycle
    101 s rather than the 100 s p.105 explicitly demands.

    With the authority self-inconsistent across three pages, the module returns
    7.333… and lets its caller round where it must.
    """
    table = build_infrequent_phase_table(table_3_3_observations(), TABLE_3_3_N_CYCLES)
    weighted_c = {row.phase_id: row for row in table.rows}["C"].weighted_average_phase_time_s
    assert weighted_c == pytest.approx(7.3333333)
    assert weighted_c not in (7.0, 8.0)
    assert round(weighted_c) == 7  # what the document prints
    assert round_up_to_step(weighted_c, 1.0) == 8.0  # what Appendix A's rule would give


def test_weighted_and_actual_averages_differ_exactly_for_infrequent_phases():
    """The property the whole section exists to teach: the two averages are the
    same number when a phase runs every cycle and differ otherwise, by the
    factor fᵢ.

    By hand, C: actual 12.0 × f 0.6111… = 7.3333… = weighted. ✓
    """
    table = build_infrequent_phase_table(table_3_3_observations(), TABLE_3_3_N_CYCLES)
    for row in table.rows:
        assert row.actual_average_phase_time_s * row.demand_frequency == pytest.approx(
            row.weighted_average_phase_time_s
        )
        if row.demand_frequency == 1.0:
            assert row.actual_average_phase_time_s == pytest.approx(
                row.weighted_average_phase_time_s
            )
        else:
            assert row.weighted_average_phase_time_s < row.actual_average_phase_time_s


def test_a_weighted_average_below_the_minimum_phase_time_is_reported():
    """The document's own observation about this very example: "the weighted
    average phase time for C phase is shorter than minimum phase time".

    By hand: C's weighted average is 7.33 s against a 10 s minimum phase time.
    That is not a defect — it is the condition demand-dependency adjustment
    exists for, and the WARN says so rather than implying bad data.
    """
    table = build_infrequent_phase_table(
        table_3_3_observations({"C": 10.0}), TABLE_3_3_N_CYCLES
    )
    assert table.ok
    warn = next(
        i
        for i in table.warnings
        if i.code == "linsig_weighted_average_below_minimum_phase_time"
    )
    assert warn.context["phase_ids"] == ["C"]
    assert [row.below_minimum_phase_time for row in table.rows] == [False, False, True]


def test_unstated_occurrences_degrade_two_columns_but_not_the_modelled_cycle():
    """The weighted average divides by the CYCLE count, so it survives a source
    that lost its occurrence counts; the demand frequency and actual average do
    not. The module says which, and does not pretend the case weighting for
    bonus green can be built from what is left."""
    observations = [
        PhaseObservation("A", 2223.0, occurrences=36),
        PhaseObservation("B", 1113.0, occurrences=36),
        PhaseObservation("C", 264.0, occurrences=None),
    ]
    table = build_infrequent_phase_table(observations, TABLE_3_3_N_CYCLES)
    assert table.ok
    row_c = {row.phase_id: row for row in table.rows}["C"]
    assert row_c.weighted_average_phase_time_s == pytest.approx(7.3333333)
    assert row_c.demand_frequency is None
    assert row_c.actual_average_phase_time_s is None
    assert table.cycle_time_to_model_s == 100.0
    assert table.actual_average_cycle_s is None  # cannot be formed, not guessed at
    warn = next(i for i in table.warnings if i.code == "linsig_occurrences_unstated")
    assert warn.context["phase_ids"] == ["C"]


def test_a_phase_running_more_often_than_there_are_cycles_warns_but_is_allowed():
    """Appendix A's repeat-right-turn phasing runs a phase twice in a cycle, so
    fᵢ > 1 is representable. It just cannot be a phase-sequence case
    probability, which must partition the hour — and that is what the WARN is
    for. By hand: 40 occurrences in 36 cycles = 1.111…"""
    observations = [
        PhaseObservation("A", 3336.0, occurrences=36),
        PhaseObservation("C", 264.0, occurrences=40),
    ]
    table = build_infrequent_phase_table(observations, TABLE_3_3_N_CYCLES)
    assert table.ok
    warn = next(i for i in table.warnings if i.code == "linsig_demand_frequency_above_one")
    assert warn.context["phase_ids"] == ["C"]
    assert {row.phase_id: row for row in table.rows}["C"].demand_frequency == pytest.approx(
        40 / 36
    )


def test_totals_that_do_not_fill_the_modelled_hour_are_refused():
    """3400 s of phase time in a 3600 s hour means 200 s of the modelled period
    is unaccounted for: Σ(weighted averages) = 94.4 s while 3600 ÷ 36 = 100 s.
    Two different cycles, no way to choose, so no table."""
    observations = [
        PhaseObservation("A", 2023.0, occurrences=36),
        PhaseObservation("B", 1113.0, occurrences=36),
        PhaseObservation("C", 264.0, occurrences=22),
    ]
    table = build_infrequent_phase_table(observations, TABLE_3_3_N_CYCLES)
    assert table.ok is False
    assert table.rows == ()
    refusal = next(
        i
        for i in table.refusals
        if i.code == "linsig_phase_totals_contradict_modelled_period"
    )
    assert refusal.context["total_phase_time_s"] == 3400.0
    assert refusal.context["difference_s"] == 200.0


def test_a_small_shortfall_against_the_modelled_hour_only_warns():
    """Inside the tolerance the table is built and the difference is reported:
    3597 s against 3600 s is 3 s, under the 5 s default."""
    observations = [
        PhaseObservation("A", 2220.0, occurrences=36),
        PhaseObservation("B", 1113.0, occurrences=36),
        PhaseObservation("C", 264.0, occurrences=22),
    ]
    table = build_infrequent_phase_table(observations, TABLE_3_3_N_CYCLES)
    assert table.ok
    assert "linsig_phase_totals_differ_slightly" in table.issue_codes(IssueSeverity.WARN)
    assert table.cycle_time_to_model_s == 100.0  # from the PERIOD, as the doc says
    assert table.weighted_average_cycle_s == pytest.approx(3597 / 36)


def test_the_modelled_period_tolerance_is_a_setting_not_a_hardcode():
    """ASSUMPTION A12's tolerance is a named ``LinSigSettings`` field, so a
    caller whose window genuinely is not the modelled period widens it in the
    open rather than working around a refusal.

    By hand: 3400 s of phase time against a 3600 s hour is 200 s out — refused
    at the 5 s default. Allowed at 250 s, and then the two routes to the cycle
    differ by exactly 200/36 = 5.5555… s: 3600/36 = 100 s from the period,
    3400/36 = 94.4444… s from the totals. That gap is what the WARN is for.
    """
    observations = [
        PhaseObservation("A", 2023.0, occurrences=36),
        PhaseObservation("B", 1113.0, occurrences=36),
        PhaseObservation("C", 264.0, occurrences=22),
    ]
    assert build_infrequent_phase_table(observations, 36).ok is False
    loose = build_infrequent_phase_table(
        observations, 36, settings=LinSigSettings(modelled_period_tolerance_s=250.0)
    )
    assert loose.ok is True
    assert "linsig_phase_totals_differ_slightly" in loose.issue_codes(IssueSeverity.WARN)
    assert loose.cycle_time_to_model_s == 100.0
    assert loose.weighted_average_cycle_s == pytest.approx(3400 / 36)
    assert loose.cycle_time_to_model_s - loose.weighted_average_cycle_s == pytest.approx(200 / 36)


def test_a_modelled_period_other_than_an_hour_is_stated_not_assumed():
    """ASSUMPTION A12: 3600 is the length of the hour LinSig models, not a fact
    about anyone's data. By hand, a 1800 s period over 20 cycles → 90 s."""
    assert cycle_time_to_model_s(20, 1800.0) == 90.0
    table = build_infrequent_phase_table(
        [PhaseObservation("A", 1200.0, occurrences=20), PhaseObservation("B", 600.0, 20)],
        20,
        modelled_period_s=1800.0,
    )
    assert table.ok
    assert table.cycle_time_to_model_s == 90.0
    assert table.modelled_period_s == 1800.0


@pytest.mark.parametrize(
    ("phases", "cycles", "code"),
    [
        pytest.param([], 36, "linsig_no_phase_rows", id="no_rows"),
        pytest.param(
            [PhaseObservation("A", 1800.0, 36), PhaseObservation("A", 1800.0, 36)],
            36,
            "linsig_duplicate_phase_id",
            id="duplicate_phase",
        ),
        pytest.param(
            [PhaseObservation("A", 3600.0, 36)], 0, "linsig_cycle_count_unusable", id="zero_cycles"
        ),
        pytest.param(
            [PhaseObservation("A", 3600.0, 36)],
            None,
            "linsig_cycle_count_unusable",
            id="no_cycles",
        ),
        pytest.param(
            [PhaseObservation("A", math.inf, 36)],
            36,
            "linsig_phase_total_unusable",
            id="non_finite_total",
        ),
        pytest.param(
            [PhaseObservation("A", -10.0, 36)],
            36,
            "linsig_phase_total_unusable",
            id="negative_total",
        ),
        pytest.param(
            [PhaseObservation("A", 3600.0, -1)],
            36,
            "linsig_occurrences_unusable",
            id="negative_occurrences",
        ),
    ],
)
def test_table_3_3_refusals(phases, cycles, code):
    """Garbage in → a refusal naming its own code, and NO table."""
    table = build_infrequent_phase_table(phases, cycles)
    assert table.ok is False, code
    assert table.rows == (), "a refused table must produce no rows at all"
    assert code in table.issue_codes(IssueSeverity.REFUSE), table.issue_codes()


# ===========================================================================
# 11. A.2 reconciliation — the same rule, two authorities, one implementation
# ===========================================================================


def test_the_linsig_averages_are_appendix_a_s_own_functions():
    """The reconciliation verdict, asserted rather than claimed.

    Appendix A §A.2 bullet 3 and OMG §3.5.15.2 state the same three
    quantities under different names, so ``linsig.py`` delegates to
    ``fixed_time.py`` instead of restating the arithmetic. Numeric agreement
    alone would not prove that — two copies of ``a / b`` also agree — so this
    also asserts that the REFUSAL MESSAGES come from the Appendix-A functions,
    which they can only do if the LinSig entry points really are wrappers.
    """
    for _, occurrences, total, _, _ in TABLE_3_3_PUBLISHED:
        assert weighted_average_phase_time_s(total, 36) == average_phase_time_s(total, 36)
        assert actual_average_phase_time_s(total, occurrences) == scats_average_phase_time_s(
            total, occurrences
        )
        assert demand_frequency(occurrences, 36) == phase_frequency(occurrences, 36)

    with pytest.raises(ValueError, match="average_phase_time_s"):
        weighted_average_phase_time_s(100.0, 0)
    with pytest.raises(ValueError, match="scats_average_phase_time_s"):
        actual_average_phase_time_s(100.0, 0)
    with pytest.raises(ValueError, match="phase_frequency"):
        demand_frequency(4, 0)


def test_the_cycle_rule_is_the_phase_rule_applied_to_the_whole_period():
    """Σ(totalᵢ ÷ n) = (Σ totalᵢ) ÷ n, so the LinSig "cycle time to model" is
    the weighted-average function again rather than a fourth division.

    By hand: 3600 ÷ 36 = 100 = 61.75 + 30.91666… + 7.33333…
    """
    assert cycle_time_to_model_s(36) == average_phase_time_s(3600.0, 36)
    assert cycle_time_to_model_s(36) == 100.0


def test_the_two_authorities_agree_before_rounding_and_differ_after_it():
    """The one place the two documents genuinely part company, kept visible.

    Fed the SAME history — Table 3-3's own — the Appendix-A converter and the
    LinSig table produce IDENTICAL per-phase averages, because they are the
    same rule. They then produce DIFFERENT cycle times, because their rounding
    rules differ and neither module is entitled to adopt the other's:

    * Appendix A / the main doc: "SCATS fractions always round up" (fixed_time
      ASSUMPTION A1/A2), so 61.75 → 62, 30.91666… → 31, 7.33333… → 8 and the
      plan cycle is 62 + 31 + 8 = **101 s**.
    * OMG §3.5.15.2 (ASSUMPTION A11): round nothing; model 3600 ÷ 36 =
      **100 s**, which is p.105's explicit instruction.

    The 1 s residual is disclosed by the converter on every run. Unifying the
    two would mean silently choosing one authority's arithmetic for the other's
    output, which is exactly what this test exists to prevent.
    """
    scats_history = controller(
        [
            program(
                [
                    phase("A", 36, 2223.0, stretch=True),
                    phase("B", 36, 1113.0),
                    phase("C", 22, 264.0),
                ],
                cycles=36,
            )
        ]
    )
    plan = convert_to_fixed_time(scats_history)
    table = build_infrequent_phase_table(table_3_3_observations(), TABLE_3_3_N_CYCLES)

    assert plan.ok and table.ok
    appendix_a = {row.phase_id: row for row in plan.rows}
    linsig = {row.phase_id: row for row in table.rows}
    for phase_id in ("A", "B", "C"):
        assert appendix_a[phase_id].average_phase_s == pytest.approx(
            linsig[phase_id].weighted_average_phase_time_s
        )
        assert appendix_a[phase_id].scats_average_s == pytest.approx(
            linsig[phase_id].actual_average_phase_time_s
        )
        assert appendix_a[phase_id].frequency == pytest.approx(linsig[phase_id].demand_frequency)

    assert plan.average_cycle_s == pytest.approx(100.0)
    assert table.cycle_time_to_model_s == 100.0
    # …and after each authority's own rounding rule:
    assert [appendix_a[p].rounded_phase_s for p in "ABC"] == [62.0, 31.0, 8.0]
    assert plan.plan_cycle_s == 101.0
    assert plan.round_up_residual_s == pytest.approx(1.0)


# ===========================================================================
# 12. Table 3-4 — bonus green
# ===========================================================================

#: The worked example's signal groups. SG3 is the filtered right turn whose
#: opposed/unopposed split p.107 makes mandatory.
TABLE_3_4_SPECS = [
    SignalGroupSpec("SG1", lanes=("1/1",)),
    SignalGroupSpec("SG2", lanes=("2/1",)),
    SignalGroupSpec("SG3", filtered_right_turn=True, lanes=("3/1",)),
    SignalGroupSpec("SG4", lanes=("4/1",)),
]


def table_3_4_cases(**overrides) -> list[PhaseSequenceCase]:
    """The published cases, built from the constant so the test input cannot
    drift from the table it reproduces. The demanded case (Figure 3-52) is the
    one coded in Signal Timings View."""
    return [
        PhaseSequenceCase(
            label=label,
            probability=overrides.get(label, probability),
            greens=greens,
            is_modelled=label.startswith("A,B,C"),
        )
        for label, probability, greens in TABLE_3_4_PUBLISHED_CASES
    ]


def test_table_3_4_worked_example_reproduces_all_five_columns_to_two_decimals():
    """Table 3-4 (p.107), re-derived — the document's own five signal-group
    columns, both rows, to 2 dp.

    "Weighted average green is calculated based on the % activation" and
    "Bonus green time = weighted average green − modelled green (Figure 3-52)".
    The 61 % is Table 3-3's C-phase demand frequency (22/36); 39 % is its
    complement. By hand::

        SG1 opposed?no   0.61×51 + 0.39×63 = 31.11 + 24.57 = 55.68 ; −51 = +4.68
        SG2              0.61×63 + 0.39×63 = 38.43 + 24.57 = 63.00 ; −63 =  0
        SG3 opposed      0.61×47 + 0.39×59 = 28.67 + 23.01 = 51.68 ; −47 = +4.68
        SG3 unopposed    0.61× 6 + 0.39× 0 =  3.66 +  0.00 =  3.66 ; − 6 = −2.34
        SG4              0.61×25 + 0.39×25 = 15.25 +  9.75 = 25.00 ; −25 =  0

    Every one of those ten numbers is printed in the document, and every one is
    re-derived here from its two inputs.
    """
    table = compute_bonus_green(table_3_4_cases(), TABLE_3_4_SPECS)
    assert table.ok is True
    assert table.modelled_case_label == "A,B,C (Figure 3-52)"

    rows = {row.key: row for row in table.rows}
    assert set(rows) == set(TABLE_3_4_PUBLISHED_WEIGHTED)
    for key, published_weighted in TABLE_3_4_PUBLISHED_WEIGHTED.items():
        assert round(rows[key].weighted_average_green_s, 2) == published_weighted, key.label
    for key, published_bonus in TABLE_3_4_PUBLISHED_BONUS.items():
        assert round(rows[key].bonus_green_s, 2) == published_bonus, key.label

    # The modelled green really is the demanded case's, not the average.
    assert rows[GreenKey("SG1")].modelled_green_s == 51.0
    assert rows[GreenKey("SG3", GivewayPeriod.UNOPPOSED)].modelled_green_s == 6.0
    assert [label for label, _, _ in rows[GreenKey("SG1")].case_greens] == [
        "A,B,C (Figure 3-52)",
        "A,B (Figure 3-53)",
    ]


def test_bonus_green_is_signed_and_the_negative_one_is_not_softened():
    """Table 3-4's own SG3-unopposed bonus is −2.34 s. §3.5.15.4.1 applies
    negative bonus green "to the traffic lanes that get less green time due to
    the early termination", so a magnitude here would hand LinSig capacity on
    exactly the movement that lost it."""
    table = compute_bonus_green(table_3_4_cases(), TABLE_3_4_SPECS)
    unopposed = {row.key: row for row in table.rows}[GreenKey("SG3", GivewayPeriod.UNOPPOSED)]
    assert unopposed.bonus_green_s < 0
    assert round(unopposed.bonus_green_s, 2) == -2.34
    info = next(i for i in table.infos if i.code == "linsig_bonus_green_negative")
    assert info.context["columns"] == ["SG3 (unopposed)"]


def test_bonus_green_is_applied_per_lane_and_a_filtered_turn_lane_gets_both_periods():
    """Step 4 of the method: "Apply the signal group bonus green time to the
    relevant lanes in Lanes Timings View." A lane serving a filtered right turn
    legitimately receives two entries — one per give-way period — which is the
    whole point of keeping them separate."""
    table = compute_bonus_green(table_3_4_cases(), TABLE_3_4_SPECS)
    applications = table.lane_applications
    assert sorted(applications) == ["1/1", "2/1", "3/1", "4/1"]
    assert [label for label, _ in applications["1/1"]] == ["SG1"]
    assert round(applications["1/1"][0][1], 2) == 4.68
    assert sorted(label for label, _ in applications["3/1"]) == [
        "SG3 (opposed)",
        "SG3 (unopposed)",
    ]
    assert sorted(round(bonus, 2) for _, bonus in applications["3/1"]) == [-2.34, 4.68]


@pytest.mark.parametrize(
    ("keys", "id_"),
    [
        pytest.param(("whole",), "no_split_at_all", id="no_split_at_all"),
        pytest.param(("opposed",), "opposed_only", id="opposed_only"),
        pytest.param(("opposed", "unopposed", "whole"), "stray_whole", id="stray_whole_column"),
    ],
)
def test_a_filtered_right_turn_without_the_mandatory_split_is_refused(keys, id_):
    """p.107: "It is important to separate SG3 to opposed and unopposed periods
    as it affects the capacity calculations during the give-way period."

    A single weighted average for such a signal group mixes give-way capacity
    with unopposed capacity, so the bonus derived from it is wrong by
    construction — there is no partial answer to give, and the module gives
    none. A stray whole-group column ALONGSIDE the split is refused too: it
    would be double-counted green.
    """
    mapping = {
        "whole": GreenKey("SG3"),
        "opposed": GreenKey("SG3", GivewayPeriod.OPPOSED),
        "unopposed": GreenKey("SG3", GivewayPeriod.UNOPPOSED),
    }
    greens = {mapping[k]: 20.0 for k in keys}
    cases = [
        PhaseSequenceCase("demanded", 0.6, {GreenKey("SG1"): 50.0, **greens}, is_modelled=True),
        PhaseSequenceCase("skipped", 0.4, {GreenKey("SG1"): 60.0, **greens}),
    ]
    table = compute_bonus_green(
        cases, [SignalGroupSpec("SG1"), SignalGroupSpec("SG3", filtered_right_turn=True)]
    )
    assert table.ok is False
    assert table.rows == ()
    refusal = next(i for i in table.refusals if i.code == "linsig_filtered_turn_not_split")
    assert refusal.context["sg_ids"] == ["SG3"]


def test_an_undeclared_signal_group_says_the_mandatory_split_was_not_checked():
    """The module cannot know a signal group is a filtered right turn from its
    green times, so an undeclared group means the p.107 check did not run.
    Silence would read as a pass — this is the same doctrine as
    ``fixed_time_yellow_trap_not_checkable``."""
    cases = [
        PhaseSequenceCase("demanded", 0.6, {GreenKey("SG3"): 20.0}, is_modelled=True),
        PhaseSequenceCase("skipped", 0.4, {GreenKey("SG3"): 30.0}),
    ]
    table = compute_bonus_green(cases)
    assert table.ok  # it computes…
    warn = next(i for i in table.warnings if i.code == "linsig_signal_group_undeclared")
    assert warn.context["sg_ids"] == ["SG3"]  # …but says what it could not check
    assert "not checked" in warn.message or "NOT checked" in warn.message


def test_a_declared_group_that_appears_in_no_case_is_reported():
    """Its green is missing from the calculation entirely, which is a bigger
    problem than a missing row."""
    cases = [
        PhaseSequenceCase("demanded", 0.6, {GreenKey("SG1"): 20.0}, is_modelled=True),
        PhaseSequenceCase("skipped", 0.4, {GreenKey("SG1"): 30.0}),
    ]
    table = compute_bonus_green(cases, [SignalGroupSpec("SG1"), SignalGroupSpec("SG9")])
    assert table.ok
    warn = next(i for i in table.warnings if i.code == "linsig_signal_group_not_in_cases")
    assert warn.context["sg_ids"] == ["SG9"]


def test_a_signal_group_missing_from_one_case_is_unknown_not_zero():
    """ASSUMPTION A14. Figure 3-53 states SG3-unopposed as an explicit **0**;
    reading an absent key as zero would erase the difference between "this
    group gets no green in this case" and "nobody recorded it"."""
    cases = [
        PhaseSequenceCase(
            "demanded", 0.6, {GreenKey("SG1"): 50.0, GreenKey("SG2"): 20.0}, is_modelled=True
        ),
        PhaseSequenceCase("skipped", 0.4, {GreenKey("SG1"): 60.0}),
    ]
    table = compute_bonus_green(cases)
    assert table.ok is False
    refusal = next(i for i in table.refusals if i.code == "linsig_case_signal_groups_differ")
    assert refusal.context["missing_by_case"] == {"skipped": ["SG2"]}


def test_bonus_green_with_no_declared_lane_is_reported_not_dropped():
    """ASSUMPTION A15: the number is computed and named, because a bonus that
    reaches no lane changes nothing in the LinSig model — and silently dropping
    it would hide that."""
    table = compute_bonus_green(
        table_3_4_cases(), [SignalGroupSpec("SG3", filtered_right_turn=True)]
    )
    # SG3 is declared (and split), the rest are not, so nothing is refused.
    assert table.ok
    warn = next(i for i in table.warnings if i.code == "linsig_bonus_green_no_lane_declared")
    assert "SG1" in warn.context["columns"]
    assert table.lane_applications == {}


def test_bonus_green_always_discloses_the_method_choice_and_the_submission_artefact():
    """Two always-on disclosures, in the 1.6 tradition of stating what a number
    does NOT settle: the document says "there is not a definitive method" and
    requires the green-time calculation spreadsheet in the submission."""
    table = compute_bonus_green(table_3_4_cases(), TABLE_3_4_SPECS)
    codes = table.issue_codes(IssueSeverity.INFO)
    assert "linsig_demand_dependency_judgement_required" in codes
    assert "linsig_bonus_green_spreadsheet_required" in codes


@pytest.mark.parametrize(
    ("cases", "specs", "code"),
    [
        pytest.param([], [], "linsig_no_cases", id="no_cases"),
        pytest.param(
            [
                PhaseSequenceCase("a", 0.6, {GreenKey("SG1"): 10.0}, is_modelled=True),
                PhaseSequenceCase("b", 0.3, {GreenKey("SG1"): 20.0}),
            ],
            [],
            "linsig_case_probabilities_do_not_sum_to_one",
            id="probabilities_do_not_partition",
        ),
        pytest.param(
            [PhaseSequenceCase("a", 1.5, {GreenKey("SG1"): 10.0}, is_modelled=True)],
            [],
            "linsig_case_probability_unusable",
            id="probability_above_one",
        ),
        pytest.param(
            [PhaseSequenceCase("a", math.nan, {GreenKey("SG1"): 10.0}, is_modelled=True)],
            [],
            "linsig_case_probability_unusable",
            id="probability_not_a_number",
        ),
        pytest.param(
            [PhaseSequenceCase("a", 1.0, {GreenKey("SG1"): 10.0})],
            [],
            "linsig_modelled_case_unidentified",
            id="no_modelled_case",
        ),
        pytest.param(
            [
                PhaseSequenceCase("a", 0.5, {GreenKey("SG1"): 10.0}, is_modelled=True),
                PhaseSequenceCase("b", 0.5, {GreenKey("SG1"): 20.0}, is_modelled=True),
            ],
            [],
            "linsig_modelled_case_ambiguous",
            id="two_modelled_cases",
        ),
        pytest.param(
            [PhaseSequenceCase("a", 1.0, {GreenKey("SG1"): -5.0}, is_modelled=True)],
            [],
            "linsig_case_green_unusable",
            id="negative_green",
        ),
        pytest.param(
            [PhaseSequenceCase("a", 1.0, {GreenKey("SG1"): 10.0}, is_modelled=True)],
            [SignalGroupSpec("SG1"), SignalGroupSpec("SG1", True)],
            "linsig_duplicate_signal_group_spec",
            id="duplicate_spec",
        ),
    ],
)
def test_bonus_green_refusals(cases, specs, code):
    """Garbage in → a refusal naming its own code, and NO rows."""
    table = compute_bonus_green(cases, specs)
    assert table.ok is False, code
    assert table.rows == (), "a refused table must produce no rows at all"
    assert code in table.issue_codes(IssueSeverity.REFUSE), table.issue_codes()


def test_weighted_average_green_refuses_a_case_set_that_is_not_a_partition():
    """The pure formula raises rather than normalising: normalising a 0.61/0.30
    pair to sum to 1 would invent the missing 9 %'s green."""
    assert weighted_average_green_s([(0.61, 51.0), (0.39, 63.0)]) == pytest.approx(55.68)
    with pytest.raises(ValueError, match="sum to 1"):
        weighted_average_green_s([(0.61, 51.0), (0.30, 63.0)])
    with pytest.raises(ValueError):
        weighted_average_green_s([])
    with pytest.raises(ValueError):
        weighted_average_green_s([(1.0, -1.0)])


def test_pedestrian_protection_bonus_worked_examples_both_signs():
    """§3.5.15.3's two mirrored rules, with the doc's own numbers.

    Protected case coded (§3.5.15.3.1): crossing activated 60 % of the time,
    10 s timed red arrow → +(1 − 0.6) × 10 = **+4 s**, the doc's "positive
    bonus green time of 4 seconds (40% x 10s)".

    Unprotected case coded (§3.5.15.3.2, dummy phase): activated 20 % →
    −0.2 × 10 = **−2 s**, the doc's "negative bonus green time of 2 seconds
    (20% x 10 seconds protection period)".

    The sign depends entirely on which case is coded in Signal Timings View,
    which is why neither is a default.
    """
    assert bonus_green_protected_case_s(0.60, 10.0) == pytest.approx(4.0)
    assert bonus_green_unprotected_case_s(0.20, 10.0) == pytest.approx(-2.0)
    # The §3.5.15.3.4 figure (40 % × 10 s = 4 s) is the SAME arithmetic used by
    # a different, non-preferred method — it adjusts the protection period, not
    # a lane's green — so it is not a third rule.
    assert bonus_green_protected_case_s(0.0, 10.0) == 10.0
    assert bonus_green_unprotected_case_s(1.0, 10.0) == -10.0
    with pytest.raises(ValueError):
        bonus_green_protected_case_s(1.5, 10.0)


def test_the_protection_rules_are_the_general_bonus_formula_with_the_algebra_done():
    """Not two extra rules — one rule, twice, with G cancelled.

    With G the green available when the crossing is not called and p the
    protection period, the two cases are (f: protected, 1−f: not)::

        weighted average = f(G − p) + (1 − f)G = G − f·p
        protected case coded:   modelled = G − p → bonus = p(1 − f)
        unprotected case coded: modelled = G     → bonus = −f·p

    Asserted here across an arbitrary G (37 s), so the helpers cannot drift
    away from :func:`bonus_green_s` without this failing.
    """
    available_green, protection, frequency = 37.0, 10.0, 0.6
    cases = [(frequency, available_green - protection), (1 - frequency, available_green)]
    weighted = weighted_average_green_s(cases)
    assert weighted == pytest.approx(available_green - frequency * protection)
    assert bonus_green_s(weighted, available_green - protection) == pytest.approx(
        bonus_green_protected_case_s(frequency, protection)
    )
    assert bonus_green_s(weighted, available_green) == pytest.approx(
        bonus_green_unprotected_case_s(frequency, protection)
    )


# ===========================================================================
# 13. §3.10.1 degree of saturation, §3.11.2.1 PRC
# ===========================================================================


def dos_lanes(*pairs: tuple[str, float, float]) -> list[LaneSaturation]:
    return [LaneSaturation(lane_id, demand, capacity) for lane_id, demand, capacity in pairs]


def test_degree_of_saturation_worked_examples():
    """§3.11.2: "the ratio of demand flow to the maximum flow which can be
    passed through the intersection from a particular lane", in per cent.

    By hand: 720 ÷ 900 = 0.8 → 80.0 %. 950 ÷ 900 = 1.0555… → 105.56 %.
    900 ÷ 900 = exactly 100 %, which §3.10.1 permits — the rule is "should not
    EXCEED 100 per cent".
    """
    assert degree_of_saturation_pct(720.0, 900.0) == 80.0
    assert degree_of_saturation_pct(950.0, 900.0) == pytest.approx(105.5555555)
    assert degree_of_saturation_pct(900.0, 900.0) == 100.0
    assert degree_of_saturation_pct(0.0, 900.0) == 0.0
    with pytest.raises(ValueError):
        degree_of_saturation_pct(720.0, 0.0)
    with pytest.raises(ValueError):
        degree_of_saturation_pct(-1.0, 900.0)


def test_dos_validation_is_flat_at_100_percent_with_no_banding():
    """§3.10.1 is the whole numeric rule: "the DoS should not exceed 100 per
    cent". Unlike MRWA's Vissim chapter (Tables 5-12/5-13) there is no category
    split and no tolerance band, so a lane at exactly 100 % passes and one at
    100.1 % does not.

    By hand: 900/900 = 100.0 % (pass); 1001/1000 = 100.1 % (refuse).
    """
    passing = validate_degree_of_saturation(
        dos_lanes(("1/1", 900.0, 900.0), ("1/2", 630.0, 900.0)),
        basis=ValidationDataBasis.STOP_LINE_COUNTS_AND_SCATS,
        cycle_time_s=100.0,
    )
    assert passing.ok is True
    assert passing.assessed is True
    assert passing.highest_dos_pct == 100.0
    assert passing.highest_dos_lane_id == "1/1"
    assert [row.exceeds_validation_maximum for row in passing.rows] == [False, False]

    failing = validate_degree_of_saturation(
        dos_lanes(("1/1", 1001.0, 1000.0), ("1/2", 630.0, 900.0)),
        basis=ValidationDataBasis.STOP_LINE_COUNTS_AND_SCATS,
        cycle_time_s=100.0,
    )
    assert failing.ok is False
    refusal = next(
        i for i in failing.refusals if i.code == "linsig_dos_exceeds_validation_maximum"
    )
    assert refusal.context["lane_ids"] == ["1/1"]
    assert refusal.context["highest_dos_pct"] == pytest.approx(100.1)
    assert refusal.context["maximum_pct"] == 100.0
    # The rows survive a refusal — the numbers are still reportable.
    assert failing.rows[0].dos_pct == pytest.approx(100.1)


def test_dos_validation_requires_the_precondition_section_3_10_1_states():
    """ASSUMPTION A17. The ≤ 100 % rule is stated for a base model built from
    stop-line turning counts with SCATS-extracted signal timings. On any other
    basis the criterion is not applied, no pass is claimed, and an exceedance
    is reported at WARN rather than refused — the rule is not ours to extend to
    models it was not written about.

    By hand: 1200/1000 = 120 %, on an unstated basis.
    """
    result = validate_degree_of_saturation(dos_lanes(("1/1", 1200.0, 1000.0)), cycle_time_s=90.0)
    assert result.assessed is False
    assert result.ok is True, "not a refusal: the criterion was not applied"
    assert result.highest_dos_pct == pytest.approx(120.0)
    assert "linsig_dos_validation_basis_not_stated_rule" in result.issue_codes(IssueSeverity.WARN)
    warn = next(
        i
        for i in result.warnings
        if i.code == "linsig_dos_exceeds_validation_maximum_basis_unstated"
    )
    assert warn.context["highest_dos_pct"] == pytest.approx(120.0)
    assert "linsig_dos_exceeds_validation_maximum" not in result.issue_codes()


def test_practical_reserve_capacity_worked_example_from_the_document():
    """§3.11.2.1's own worked example: "if the highest DoS in the network is
    80.0%, the PRC is 12.5%".

    By hand, first branch (DoS < 90 %): (90 − 80) / 80 = 0.125 → 12.5 %.
    And at 60 %: (90 − 60) / 60 = 0.5 → 50 %.
    """
    assert practical_reserve_capacity_pct(80.0) == 12.5
    assert practical_reserve_capacity_pct(60.0) == 50.0
    assert practical_reserve_capacity_pct(90.0) == 0.0  # branches meet, continuous
    with pytest.raises(ValueError):
        practical_reserve_capacity_pct(0.0)


def test_prc_second_branch_is_negative_and_flagged_as_unworked():
    """ASSUMPTION A18: the ≥ 90 % branch is implemented exactly as printed —
    (90 − DoS) / 90 — and the document gives it no worked example.

    By hand, DoS 95 %: (90 − 95) / 90 = −0.05555… → −5.5556 %.
    """
    assert practical_reserve_capacity_pct(95.0) == pytest.approx(-5.5555555)
    result = validate_degree_of_saturation(
        dos_lanes(("1/1", 950.0, 1000.0)),
        basis=ValidationDataBasis.STOP_LINE_COUNTS_AND_SCATS,
        cycle_time_s=110.0,
    )
    assert result.practical_reserve_capacity_pct == pytest.approx(-5.5555555)
    warn = next(i for i in result.warnings if i.code == "linsig_prc_negative")
    assert warn.context["highest_dos_pct"] == pytest.approx(95.0)


def test_a_prc_without_its_cycle_time_is_reported_as_incomparable():
    """§3.11.2.1: "PRC is a function of cycle time and therefore the cycle time
    should be referenced." A PRC quoted alone cannot be compared to any
    other."""
    without = validate_degree_of_saturation(dos_lanes(("1/1", 720.0, 900.0)))
    assert without.practical_reserve_capacity_pct == 12.5
    assert "linsig_prc_cycle_time_not_stated" in without.issue_codes(IssueSeverity.WARN)

    with_cycle = validate_degree_of_saturation(dos_lanes(("1/1", 720.0, 900.0)), cycle_time_s=100.0)
    assert with_cycle.cycle_time_s == 100.0
    assert "linsig_prc_cycle_time_not_stated" not in with_cycle.issue_codes()


def test_dos_validation_states_that_queues_were_not_checked():
    """DoS is the PRIMARY validation method, not the only one: §3.10 requires
    queue length at the start of green as well, and §3.10.2 states no numeric
    tolerance for it ("similar with the same magnitude"). Coding one would be
    inventing the client's acceptance criterion, so the module says plainly
    that a DoS pass alone is not a validated model."""
    result = validate_degree_of_saturation(
        dos_lanes(("1/1", 720.0, 900.0)),
        basis=ValidationDataBasis.STOP_LINE_COUNTS_AND_SCATS,
        cycle_time_s=100.0,
    )
    assert result.ok
    notice = next(
        i for i in result.infos if i.code == "linsig_dos_queue_validation_not_performed"
    )
    assert "not a validated model" in notice.message
    assert "§3.10.2" in (notice.citation or "")


@pytest.mark.parametrize(
    ("lanes", "code"),
    [
        pytest.param([], "linsig_dos_no_lanes", id="no_lanes"),
        pytest.param(
            dos_lanes(("1/1", 720.0, 900.0), ("1/1", 100.0, 900.0)),
            "linsig_duplicate_lane_id",
            id="duplicate_lane",
        ),
        pytest.param(dos_lanes(("1/1", 720.0, 0.0)), "linsig_dos_capacity_unusable", id="no_cap"),
        pytest.param(
            dos_lanes(("1/1", 720.0, math.inf)),
            "linsig_dos_capacity_unusable",
            id="non_finite_cap",
        ),
        pytest.param(
            dos_lanes(("1/1", -1.0, 900.0)), "linsig_dos_demand_unusable", id="negative_demand"
        ),
    ],
)
def test_dos_refusals(lanes, code):
    """Garbage in → a refusal naming its own code, and NO rows. An empty
    validation is not a passed validation."""
    result = validate_degree_of_saturation(
        lanes, basis=ValidationDataBasis.STOP_LINE_COUNTS_AND_SCATS
    )
    assert result.ok is False, code
    assert result.rows == ()
    assert code in result.issue_codes(IssueSeverity.REFUSE), result.issue_codes()


# ===========================================================================
# 14. §3.11.3 mean maximum queue — the PCU→metre conversion
# ===========================================================================


def test_pcu_to_metre_conversion_worked_examples():
    """§3.11.3: "The results should be converted to meters assuming
    1 PCU = 7.35 m."

    By hand: 1 PCU = 7.35 m · 5 PCU = 36.75 m · 12 PCU = 88.2 m ·
    100 m ÷ 7.35 = 13.60544… PCU. The round trip is exact to float precision,
    which is the only sense in which a unit conversion can be "tested".
    """
    assert mean_maximum_queue_m(1.0) == 7.35
    assert mean_maximum_queue_m(5.0) == pytest.approx(36.75)
    assert mean_maximum_queue_m(12.0) == pytest.approx(88.2)
    assert mean_maximum_queue_m(0.0) == 0.0
    assert mean_maximum_queue_pcu(100.0) == pytest.approx(13.6054421)
    assert mean_maximum_queue_pcu(mean_maximum_queue_m(12.0)) == pytest.approx(12.0)
    with pytest.raises(ValueError):
        mean_maximum_queue_m(-1.0)


def test_mmq_report_flags_a_queue_that_does_not_fit_its_lane():
    """§3.11.3: "If the modelled MMQ exceeds the physical lane length, LinSig
    highlights the MMQ value in red."

    By hand: 12 PCU × 7.35 = 88.2 m in a 60 m lane → does not fit. 5 PCU =
    36.75 m in the same lane → fits. The highest MMQ is carried because "the
    highest MMQ output of each intersection is usually required".
    """
    report = convert_mean_maximum_queues(
        [QueueLane("1/1", 12.0, 60.0), QueueLane("1/2", 5.0, 60.0)]
    )
    assert report.ok
    assert [row.mmq_m for row in report.rows] == [pytest.approx(88.2), pytest.approx(36.75)]
    assert [row.exceeds_lane_length for row in report.rows] == [True, False]
    assert report.highest_mmq_lane_id == "1/1"
    assert report.highest_mmq_pcu == 12.0
    assert report.highest_mmq_m == pytest.approx(88.2)
    warn = next(i for i in report.warnings if i.code == "linsig_mmq_exceeds_lane_length")
    assert warn.context["lane_ids"] == ["1/1"]


def test_mmq_without_a_lane_length_converts_but_says_the_fit_was_not_checked():
    report = convert_mean_maximum_queues([QueueLane("1/1", 12.0)])
    assert report.ok
    assert report.rows[0].mmq_m == pytest.approx(88.2)
    assert report.rows[0].exceeds_lane_length is False
    warn = next(i for i in report.warnings if i.code == "linsig_mmq_lane_length_unknown")
    assert warn.context["lane_ids"] == ["1/1"]


@pytest.mark.parametrize(
    ("lanes", "code"),
    [
        pytest.param([], "linsig_mmq_no_lanes", id="no_lanes"),
        pytest.param(
            [QueueLane("1/1", 3.0), QueueLane("1/1", 4.0)],
            "linsig_duplicate_lane_id",
            id="duplicate_lane",
        ),
        pytest.param([QueueLane("1/1", -3.0)], "linsig_mmq_unusable", id="negative_mmq"),
        pytest.param([QueueLane("1/1", math.nan)], "linsig_mmq_unusable", id="mmq_not_a_number"),
        pytest.param(
            [QueueLane("1/1", 3.0, 0.0)], "linsig_lane_length_unusable", id="zero_lane_length"
        ),
    ],
)
def test_mmq_refusals(lanes, code):
    report = convert_mean_maximum_queues(lanes)
    assert report.ok is False, code
    assert report.rows == ()
    assert code in report.issue_codes(IssueSeverity.REFUSE), report.issue_codes()


# ===========================================================================
# 15. Structural integrity of the LinSig module's own contract
# ===========================================================================


def _every_linsig_result():
    """One of every result object this module can produce, across success and
    failure paths."""
    return [
        build_infrequent_phase_table(table_3_3_observations(), TABLE_3_3_N_CYCLES),
        build_infrequent_phase_table([], 36),
        compute_bonus_green(table_3_4_cases(), TABLE_3_4_SPECS),
        compute_bonus_green([]),
        validate_degree_of_saturation(
            dos_lanes(("1/1", 1200.0, 1000.0)),
            basis=ValidationDataBasis.STOP_LINE_COUNTS_AND_SCATS,
            cycle_time_s=100.0,
        ),
        validate_degree_of_saturation(dos_lanes(("1/1", 720.0, 900.0))),
        validate_degree_of_saturation([]),
        convert_mean_maximum_queues([QueueLane("1/1", 12.0, 60.0)]),
        convert_mean_maximum_queues([]),
    ]


def test_every_linsig_issue_carries_a_traceable_citation():
    """A refusal that cannot defend itself is an obstacle, not a control — and
    a WARN that cannot is just an opinion. Every issue, on every path, cites
    the LinSig extract by path."""
    seen = 0
    for result in _every_linsig_result():
        for issue in result.issues:
            seen += 1
            assert issue.citation, f"{issue.code} has no citation"
            assert linsig_module.EXTRACT in issue.citation, f"{issue.code} cites {issue.citation!r}"
    assert seen >= 20


def test_every_linsig_result_and_issue_is_labelled_with_its_validation_regime():
    """The scoping trap this module was built against: a DoS and a GEH are both
    "a validation number", they come from different software with different
    acceptance regimes, and nothing about the float says which.

    So the label is structural — on every result object AND in every issue's
    context, because a finding travels further than the result it came from.
    """
    for result in _every_linsig_result():
        assert result.regime == linsig_module.REGIME
        for issue in result.issues:
            assert issue.context.get("regime") == linsig_module.REGIME, issue.code

    assert "LinSig" in linsig_module.REGIME
    assert "GEH" in linsig_module.REGIME
    assert "NOT" in linsig_module.REGIME
    # Every entry point says it unprompted, on success as on failure.
    for result in _every_linsig_result():
        assert "linsig_regime_is_linsig_not_vissim" in result.issue_codes(IssueSeverity.INFO)


def test_the_two_modules_of_unit_1_6_do_not_share_an_issue_code_namespace():
    """``fixed_time_*`` and ``linsig_*`` are disjoint by construction, which is
    what lets each module's drift scan be exact rather than approximate — and
    what stops a Vissim-regime refusal being mistaken for a LinSig one in a
    report that lists codes."""
    assert REFUSE_CODES & linsig_module.REFUSE_CODES == frozenset()
    assert all(code.startswith("fixed_time_") for code in REFUSE_CODES)
    assert all(code.startswith("linsig_") for code in linsig_module.REFUSE_CODES)

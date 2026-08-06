"""MRWA LinSig chapter (OMG v2.0 §3) — demand dependency, bonus green, DoS.

**What this module is.** Unit 1.6's deferred half. The SCATS→fixed-time
converter (``fixed_time.py``) shipped the Appendix-A conversion and explicitly
left out degree-of-saturation and bonus-green because the LinSig chapter was
not on this machine (DECISIONS 2026-08-05: "The LinSig items re-enter scope
only if TK supplies that chapter"). That chapter arrived; this is those items,
built from ``docs/product/reference/mrwa-linsig-tables-extract.md`` in the same
self-citing style — every formula names the section and page it implements, and
a formula with no traceable line in that extract does not get written.

**Why a sibling module and not more of ``fixed_time.py``.** Three reasons, in
order of how much damage the alternative does:

1. **Different authority, and the citation machinery is keyed to one.** Every
   ``CIT_*`` in ``fixed_time.py`` quotes the Appendix A extract, and its test
   mechanically compares each constant against *that* file. Mixing two source
   documents into one module makes its ``EXTRACT`` constant a half-truth and
   its "every issue cites the extract" test unstatable. Here each module names
   exactly one source, and the test now runs the same comparison twice.
2. **Different validation regime, and the module boundary is the label.**
   Appendix A feeds a *Vissim* fixed-time plan. §3.10 numbers feed *LinSig*,
   which validates on degree of saturation and queue — **not GEH**. See
   :data:`REGIME`: it is stamped on every result object and every issue this
   module emits, so a LinSig DoS cannot drift into a Vissim §5 comparison
   without someone deleting a label that says it must not.
3. **Different input vocabulary.** A.2's inputs are SCATS observations, which
   the neutral model carries. §3.5.15's inputs are LinSig model objects —
   phase-sequence cases, signal groups split by give-way period, lanes — which
   the neutral model does not carry and should not be made to fake. This
   module therefore defines its own small input records and imports **nothing**
   from ``schema.py``.

**What is shared, deliberately.** The weighted-average rule is one rule stated
by two authorities, so it is one piece of code: :func:`weighted_average_phase_
time_s` and friends delegate to ``fixed_time``'s A.2 functions. The
reconciliation is written out under "A.2 reconciliation" below and is asserted
mechanically in the tests, not merely claimed here. (Sibling-imports-sibling
has precedent in this package: ``demand.py`` imports ``is_all_class_total``
from ``quality.py`` for the same reason — one definition, not two.)

**It refuses rather than guesses**, on the same tiers as the rest of the import
layer: a source-data contradiction is a ``REFUSE`` :class:`ImportIssue` and
produces no table at all (:data:`REFUSE_CODES`); nonsense arguments to a pure
formula raise ``ValueError``, because that is a caller bug and not a finding.

Nothing here imports Vissim, LinSig, or COM: LinSig is a piece of software this
module computes *inputs and checks for*, never talks to.

A.2 reconciliation — the weighted-average rule, stated twice
------------------------------------------------------------
Appendix A §A.2 bullet 3 and OMG §3.5.15.2 (p.105) are **the same arithmetic**,
and the LinSig chapter's own worked example proves it:

===============================  ==============================  ================
Appendix A §A.2                  OMG §3.5.15.2 Table 3-3          both compute
===============================  ==============================  ================
SCATS avg phase time             "Actual Average Phase Time"      total ÷ occurrences
t̄ᵢ (avg phase time)              "Weighted Average Phase Time"    total ÷ n_cycles
fᵢ (frequency)                   "Demand Frequencies"             occurrences ÷ n_cycles
c̄ = total duration ÷ n_cycles    cycle time to model (3600 ÷ n)   period ÷ n_cycles
===============================  ==============================  ================

So they are **not unified silently — they are unified explicitly**: the three
functions live once, in ``fixed_time.py``, with A.2's citation, and this module
delegates to them with the LinSig citation alongside. The fourth row is the
same function again (Σ(totalᵢ ÷ n) = (Σ totalᵢ) ÷ n), which is why
:func:`cycle_time_to_model_s` is ``average_phase_time_s`` applied to the whole
modelled period rather than a second implementation of a division.

The **one genuine difference** is which period each authority names, and it is
kept visible rather than averaged away: A.2's period is the observation window
(first stretch-phase start to the first after it), while §3.5.15.2 pins it at
the modelled hour — "3600" is not a fact about the data, it is the length of
the hour LinSig models. That is why the period is a parameter here
(:data:`SECONDS_PER_HOUR` default) and why supplying phase totals that do not
fill it is a refusal instead of a quietly different cycle
(``linsig_phase_totals_contradict_modelled_period``).

Deliberately NOT implemented, and why
-------------------------------------
* **Queue-length validation (§3.10.2).** It is half of LinSig's validation
  regime and it is stated without a number: "The values should be similar with
  the same magnitude". Coding a tolerance would be inventing the client's
  acceptance criterion. Every DoS validation therefore emits
  ``linsig_dos_queue_validation_not_performed`` rather than letting a DoS-only
  pass read as a validated model. The one queue quantity the doc *does* make
  numeric — the MMQ→metre conversion — is implemented (§3.11.3).
* **The scheme-assessment DoS acceptance level (§3.11.2).** The document
  defers it to Main Roads' *Traffic Signal Approval Policy* or the project
  scope, neither of which is on this machine. Only §3.10.1's base-model rule
  (DoS ≤ 100 %) is coded, and it is coded with its precondition attached.
* **The alternative-phases worked example (§3.5.15.4).** Its per-signal-group
  green times live in Figure 3-58, which is a graphic; the extract's own
  inventory records that those bars are unrecoverable. The general bonus-green
  machinery here covers the case (positive and negative bonuses on different
  lanes); what cannot be done is reproducing that example's numbers, so no test
  pretends to.
* **Method selection and the dummy-phase / multiple-sequences / signal-setup
  methods.** The doc is explicit that "there is not a definitive method" and
  that the modeller must exercise engineering judgement and document it. Bonus
  green is Main Roads' preferred method in all three worked examples and is the
  one with published arithmetic; the other three are modelling procedures, not
  formulas. Every bonus-green result carries
  ``linsig_demand_dependency_judgement_required`` so the choice is never
  implied to have been made here.
* **A neutral-model bridge.** ``convert_to_fixed_time`` already owns the
  neutral-model → averages path, with its own refusals; a second entry point
  from ``SignalProgramData`` into this module would duplicate them and be the
  place they drift apart.

ASSUMPTIONS — places the LinSig chapter is silent or self-inconsistent
----------------------------------------------------------------------
Continues ``fixed_time.py``'s A-series (A1–A10 live there). Each is marked
``ASSUMPTION`` at its own definition too; this is the index.

A11. **Nothing in this module rounds.** The document rounds two ways within
     three pages: Table 3-3 prints 264/36 = 7.33 as **7** (nearest), while
     §3.5.15.2.3 rounds 61 % × 6 s = 3.66 to **4** (up), and Appendix A's
     blanket rule is "SCATS fractions always round up". With the authority
     self-inconsistent, every function here returns the exact value and the
     caller rounds where it must. The tests reproduce Table 3-3's *printed*
     integers by nearest-rounding at the point of display only, and say so.
A12. **The modelled period is one hour (3600 s), overridable**, and the phase
     totals must fill it. Table 3-3's total column is exactly 3600 s and the
     bonus-green method's step 1 says "in the modelled hour"; a window that
     does not fill the period makes "3600 ÷ n_cycles" the wrong cycle.
     Tolerance: :attr:`LinSigSettings.modelled_period_tolerance_s`.
A13. **Phase-sequence case probabilities must partition the cycles** (sum to
     1). The worked example's 61 %/39 % is exhaustive by construction; a
     weighted average over cases that do not partition the hour is not an
     average of anything.
A14. **A signal group missing from one case is unknown, not zero.** Figure
     3-53 states SG3-unopposed as an explicit 0; the module refuses a case set
     whose signal groups differ rather than reading absence as no green.
A15. **Bonus green is computed per signal-group-period and applied per lane.**
     The doc computes in Table 3-4 by signal group and applies in Lane Timings
     View; a bonus with no declared lane is computed and flagged unplaced
     (``linsig_bonus_green_no_lane_declared``), never silently dropped.
A16. **DoS is carried in per cent**, matching every DoS figure the document
     prints (100 %, 90 %, 80 %) and the PRC formulae that consume it.
A17. **§3.10.1's precondition is required, not assumed.** The ≤ 100 % rule is
     stated for a base model built from stop-line turning counts and
     SCATS-extracted signal timings. A caller that cannot assert that basis
     gets DoS values and a WARN, not a pass — the rule is not ours to extend to
     models it was not written for.
A18. **PRC's second branch is implemented exactly as printed**, including its
     negative result, and flagged: the document gives it no worked example, and
     the extract records that the fraction bars were lost in the text layer
     (numerator and denominator unambiguous; the first branch is verified
     against the doc's own 80 % → 12.5 %).
A19. **1 PCU = 7.35 m applies to Mean Maximum Queue only.** §3.11.3 states it
     for MMQ reporting; §3.10.2's validation queue ("Back of Uniform Queue at
     End of Red") is a different output and the doc states no conversion for
     it, so this module converts the one and not the other.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .base import ImportIssue, IssueSeverity
from .fixed_time import average_phase_time_s, phase_frequency, scats_average_phase_time_s

# ---------------------------------------------------------------------------
# Citations
#
# Every string below names the section AND page of
# docs/product/reference/mrwa-linsig-tables-extract.md that the rule comes
# from. The extract's page numbers are DOC pages (the "Page NNN of 271" print),
# matching the Appendix A extract's convention.
# ---------------------------------------------------------------------------

EXTRACT = "docs/product/reference/mrwa-linsig-tables-extract.md"

CIT_3_5_15_JUDGEMENT = (
    f"MRWA OMG v2.0 §3.5.15 p.102 ({EXTRACT}) — 'As each modelling project and the "
    "intersection setup are different, there is not a definitive method on making demand "
    "dependency adjustments for each case ... the modeller should make the engineering "
    "judgement on the most appropriate demand dependency adjustment method for each project "
    "... Mixing different methods within a modelling project is not recommended'"
)
CIT_3_5_15_1_1_BONUS_GREEN_METHOD = (
    # NB: no apostrophe may appear in the prose before the quote — the citation
    # test reads from the FIRST quote mark, so "Main Roads' preferred method"
    # here would swallow the boilerplate into the fragment.
    f"MRWA OMG v2.0 §3.5.15.1.1 p.103 ({EXTRACT}) — the preferred method: "
    "'Calculates the total green time for each signal group in the modelled hour ... "
    "Divides the total green time in step 1 by the number of cycles in the hour ... "
    "Calculates the bonus green time for each signal group, which is the average green time "
    "in step 2 subtracts the modelled green time in Signal Timings View'"
)
CIT_3_5_15_1_1_APPLY_TO_LANES = (
    f"MRWA OMG v2.0 §3.5.15.1.1 p.103 ({EXTRACT}) — 'Apply the signal group bonus green time "
    "to the relevant lanes in Lanes Timings View.'"
)
CIT_3_5_15_2_TOTAL_PHASE_TIME = (
    f"MRWA OMG v2.0 §3.5.15.2 Table 3-3 p.105 ({EXTRACT}) — 'Total phase time is the sum of "
    "all phase lengths in an hour'"
)
CIT_3_5_15_2_DEMAND_FREQUENCY = (
    f"MRWA OMG v2.0 §3.5.15.2 Table 3-3 p.105 ({EXTRACT}) — column header: 'Demand "
    "Frequencies = Number of Occurrences / Number of Cycles'"
)
CIT_3_5_15_2_ACTUAL_AVERAGE = (
    f"MRWA OMG v2.0 §3.5.15.2 p.105 ({EXTRACT}) — 'Average phase time = total phase time in "
    "an hour ÷ number of occurrences.'"
)
CIT_3_5_15_2_WEIGHTED_AVERAGE = (
    f"MRWA OMG v2.0 §3.5.15.2 p.105 ({EXTRACT}) — 'Weighted average phase time = Total phase "
    "time in an hour ÷ number of cycles.'"
)
CIT_3_5_15_2_MINIMUM_PHASE_TIME = (
    f"MRWA OMG v2.0 §3.5.15.2 p.105 ({EXTRACT}) — 'Minimum phase time is the minimum time "
    "when the phase is activated in a cycle ... In this example, the weighted average phase "
    "time for C phase is shorter than minimum phase time'"
)
CIT_3_5_15_2_CYCLE_TO_MODEL = (
    f"MRWA OMG v2.0 §3.5.15.2 p.105 ({EXTRACT}) — 'The cycle time of 100 seconds, not 105 "
    "seconds, should be modelled replicating the correct number of cycles in an hour "
    "(36 cycles).'"
)
CIT_3_5_15_2_1_WEIGHTED_AVERAGE_GREEN = (
    f"MRWA OMG v2.0 §3.5.15.2.1 Table 3-4 p.107 ({EXTRACT}) — 'Weighted average green is "
    "calculated based on the % activation'"
)
CIT_3_5_15_2_1_BONUS_GREEN_FORMULA = (
    f"MRWA OMG v2.0 §3.5.15.2.1 Table 3-4 p.107 ({EXTRACT}) — 'Bonus green time = weighted "
    "average green − modelled green (Figure 3-52)'"
)
CIT_3_5_15_2_1_OPPOSED_SPLIT = (
    f"MRWA OMG v2.0 §3.5.15.2.1 p.107 ({EXTRACT}) — 'It is important to separate SG3 to "
    "opposed and unopposed periods as it affects the capacity calculations during the "
    "give-way period.'"
)
CIT_3_5_15_2_1_FILTERED_TURN_SPLIT = (
    f"MRWA OMG v2.0 §3.5.15.2.1 p.107 ({EXTRACT}) — 'modellers should ensure the SG3 times "
    "are calculated separately for the opposed and unopposed periods, as it affects the "
    "capacity calculations for the filtered right-turn movement'"
)
CIT_3_5_15_2_1_SPREADSHEET = (
    f"MRWA OMG v2.0 §3.5.15.2.1 p.107 ({EXTRACT}) — 'The green time calculation spreadsheet "
    "should be included in the modelling submission for ease of reference.'"
)
CIT_3_5_15_3_1_PROTECTED_CASE = (
    f"MRWA OMG v2.0 §3.5.15.3.1 p.109 ({EXTRACT}) — 'a positive bonus green time of 4 seconds "
    "(40% x 10s) is applied to Lane 1/1'"
)
CIT_3_5_15_3_2_UNPROTECTED_CASE = (
    f"MRWA OMG v2.0 §3.5.15.3.2 p.110 ({EXTRACT}) — 'a negative bonus green time of 2 seconds "
    "(20% x 10 seconds protection period) is required'"
)
CIT_3_5_15_4_1_SIGNED_BONUS = (
    f"MRWA OMG v2.0 §3.5.15.4.1 p.111 ({EXTRACT}) — 'In Lane Timings View, negative bonus "
    "green time is applied to the traffic lanes that get less green time due to the early "
    "termination'"
)
CIT_3_10_VALIDATION_COMPARISONS = (
    f"MRWA OMG v2.0 §3.10 p.121 ({EXTRACT}) — 'The validation comparisons should include ... "
    "queue length at the start of green period'"
)
CIT_3_10_1_DOS_PRIMARY = (
    f"MRWA OMG v2.0 §3.10.1 p.121 ({EXTRACT}) — 'Degree of saturation (DoS) is a LinSig "
    "output that represents the ratio of flow to capacity ... it is the primary validation "
    "method'"
)
CIT_3_10_1_DOS_MAXIMUM = (
    f"MRWA OMG v2.0 §3.10.1 p.121 ({EXTRACT}) — 'For a base model, if the traffic data is "
    "collected using stop line turning counts and the signal timings are extracted from the "
    "SCATS system, the DoS should not exceed 100 per cent.'"
)
CIT_3_10_1_ON_SITE_REVIEW = (
    f"MRWA OMG v2.0 §3.10.1 p.121 ({EXTRACT}) — 'modellers should review the magnitude of the "
    "modelled DoS is reflective to on-site conditions'"
)
CIT_3_10_2_QUEUE_NO_TOLERANCE = (
    f"MRWA OMG v2.0 §3.10.2 p.121 ({EXTRACT}) — 'The values should be similar with the same "
    "magnitude between the site measured queue and the LinSig output queues'"
)
CIT_3_11_2_DOS_DEFINITION = (
    f"MRWA OMG v2.0 §3.11.2 p.122 ({EXTRACT}) — 'Degree of saturation (DoS) is defined as the "
    "ratio of demand flow to the maximum flow which can be passed through the intersection "
    "from a particular lane'"
)
CIT_3_11_2_1_PRC = (
    f"MRWA OMG v2.0 §3.11.2.1 p.123 ({EXTRACT}) — 'Practical Reserve Capacity (PRC) is the "
    "amount by which traffic at an intersection can increase before any link becomes a "
    "practical operating level of 90% ... PRC = (90% − Highest DoS) / Highest DoS ... "
    "PRC = (90% − Highest DoS) / 90% ... For example, if the highest DoS in the network is "
    "80.0%, the PRC is 12.5%'"
)
CIT_3_11_2_1_PRC_CYCLE_TIME = (
    f"MRWA OMG v2.0 §3.11.2.1 p.123 ({EXTRACT}) — 'PRC is a function of cycle time and "
    "therefore the cycle time should be referenced.'"
)
CIT_3_11_3_MMQ_PCU = (
    f"MRWA OMG v2.0 §3.11.3 p.123 ({EXTRACT}) — 'The results should be converted to meters "
    "assuming 1 PCU = 7.35 m.'"
)
CIT_3_11_3_MMQ_LANE_LENGTH = (
    f"MRWA OMG v2.0 §3.11.3 p.123 ({EXTRACT}) — 'If the modelled MMQ exceeds the physical "
    "lane length, LinSig highlights the MMQ value in red.'"
)

#: What every number this module produces is FOR. Stamped on every result
#: object and every issue's context, because the scoping trap here is silent:
#: a DoS and a GEH are both "a validation number", they come from different
#: software with different acceptance regimes, and nothing about the float
#: itself says which. §3.10 validates LinSig on DoS + queue; MRWA's Vissim
#: chapter validates on GEH/flow. They do not substitute for one another.
REGIME = (
    "LinSig (MRWA OMG v2.0 §3.10) — validated on degree of saturation and queue at the start "
    "of green, NOT on GEH. A number from this module must never be compared against a Vissim "
    "GEH/flow criterion, or vice versa."
)


# ---------------------------------------------------------------------------
# Published constants and tables
# ---------------------------------------------------------------------------

#: §3.5.15.1.1 step 1-2 ("in the modelled hour" / "the number of cycles in the
#: hour") and Table 3-3's 3600 s total. ASSUMPTION A12 — overridable.
SECONDS_PER_HOUR = 3600.0

#: §3.11.3: "The results should be converted to meters assuming 1 PCU = 7.35 m."
PCU_LENGTH_M = 7.35

#: §3.10.1's whole numeric validation rule. Deliberately a CONSTANT and not a
#: :class:`LinSigSettings` field: a tolerance a caller can widen is a tolerance
#: that will be widened, and this one is the client authority's own number.
DOS_VALIDATION_MAXIMUM_PCT = 100.0

#: §3.11.2.1: PRC measures headroom to "a practical operating level of 90%".
PRC_PRACTICAL_OPERATING_LEVEL_PCT = 90.0


class GivewayPeriod(str, Enum):
    """§3.5.15.2.1 p.107's mandatory split for a filtered right turn.

    A filtered (opposed) right-turn signal group runs part of its green under
    give-way conditions and part unopposed, and the two have different
    capacity. The document requires them separated; :data:`REFUSE_CODES`'
    ``linsig_filtered_turn_not_split`` is that requirement with teeth.
    """

    OPPOSED = "opposed"
    UNOPPOSED = "unopposed"


@dataclass(frozen=True)
class GreenKey:
    """What one Table 3-4 column is about: a signal group, optionally one
    give-way period of it. ``period is None`` means "the whole signal group",
    which is correct for every group that is not a filtered right turn."""

    sg_id: str
    period: GivewayPeriod | None = None

    @property
    def label(self) -> str:
        return self.sg_id if self.period is None else f"{self.sg_id} ({self.period.value})"


def _sort_key(key: GreenKey) -> tuple[str, str]:
    return (key.sg_id, "" if key.period is None else key.period.value)


#: Table 3-3 (p.105) exactly as published: ``(phase, occurrences, total phase
#: time s, PRINTED actual average s, PRINTED weighted average s)`` over
#: :data:`TABLE_3_3_N_CYCLES` cycles. Kept as data so the tests re-derive Main
#: Roads' own printed numbers instead of asserting our formulas against
#: themselves. The printed columns are integers because the document prints
#: integers — see ASSUMPTION A11; this module computes 61.75, not 62.
TABLE_3_3_N_CYCLES = 36
TABLE_3_3_PUBLISHED: tuple[tuple[str, int, float, int, int], ...] = (
    ("A", 36, 2223.0, 62, 62),
    ("B", 36, 1113.0, 31, 31),
    ("C", 22, 264.0, 12, 7),
)
#: Table 3-3's own total row: total phase time, the ACTUAL-average cycle (105 s,
#: which the document says not to model) and the WEIGHTED-average cycle (100 s,
#: which it says to model).
TABLE_3_3_PUBLISHED_TOTALS: tuple[float, int, int] = (3600.0, 105, 100)

#: Table 3-4 (p.107) exactly as published: the two phase-sequence cases with
#: their % activation and per-column green times. The first is Figure 3-52 (the
#: demanded case) and is the one coded in Signal Timings View, i.e. the
#: "modelled green" the bonus is measured against.
TABLE_3_4_PUBLISHED_CASES: tuple[tuple[str, float, dict[GreenKey, float]], ...] = (
    (
        "A,B,C (Figure 3-52)",
        0.61,
        {
            GreenKey("SG1"): 51.0,
            GreenKey("SG2"): 63.0,
            GreenKey("SG3", GivewayPeriod.OPPOSED): 47.0,
            GreenKey("SG3", GivewayPeriod.UNOPPOSED): 6.0,
            GreenKey("SG4"): 25.0,
        },
    ),
    (
        "A,B (Figure 3-53)",
        0.39,
        {
            GreenKey("SG1"): 63.0,
            GreenKey("SG2"): 63.0,
            GreenKey("SG3", GivewayPeriod.OPPOSED): 59.0,
            GreenKey("SG3", GivewayPeriod.UNOPPOSED): 0.0,
            GreenKey("SG4"): 25.0,
        },
    ),
)
#: Table 3-4's published "Weighted Average Green Time" row (2 dp).
TABLE_3_4_PUBLISHED_WEIGHTED: dict[GreenKey, float] = {
    GreenKey("SG1"): 55.68,
    GreenKey("SG2"): 63.0,
    GreenKey("SG3", GivewayPeriod.OPPOSED): 51.68,
    GreenKey("SG3", GivewayPeriod.UNOPPOSED): 3.66,
    GreenKey("SG4"): 25.0,
}
#: Table 3-4's published "Bonus Green Time" row (2 dp). SIGNED: SG3-unopposed
#: is negative in the document's own table.
TABLE_3_4_PUBLISHED_BONUS: dict[GreenKey, float] = {
    GreenKey("SG1"): 4.68,
    GreenKey("SG2"): 0.0,
    GreenKey("SG3", GivewayPeriod.OPPOSED): 4.68,
    GreenKey("SG3", GivewayPeriod.UNOPPOSED): -2.34,
    GreenKey("SG4"): 0.0,
}


# ---------------------------------------------------------------------------
# Argument guards
#
# Same doctrine as fixed_time.py: these raise, because a negative green or a
# zero capacity is a caller bug. Data-level problems arrive through the builder
# functions and come back as ImportIssues instead.
# ---------------------------------------------------------------------------

#: Below this, two computed times/percentages are the same number. Float-noise
#: suppression only; every substantive tolerance is a named settings field.
_FLOAT_EQUALITY_EPSILON = 1e-9


def _require_positive(name: str, value: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number, got {value!r}")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and > 0, got {value!r}")
    return value


def _require_non_negative(name: str, value: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{name} must be a number, got {value!r}")
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and >= 0, got {value!r}")
    return value


def _is_usable_number(value: object, *, allow_zero: bool = True) -> bool:
    """A source-supplied number this module can compute with. Used by the
    builder functions, which must *report* a bad number rather than raise on
    it — the pure formulas above are the ones that raise."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    number = float(value)
    if not math.isfinite(number):
        return False
    return number >= 0 if allow_zero else number > 0


# ---------------------------------------------------------------------------
# §3.5.15.2 Table 3-3 — the four published formulae, as pure functions
#
# Three of the four are Appendix A §A.2's arithmetic under LinSig's names, so
# they delegate to fixed_time rather than restating a division. See the module
# docstring's "A.2 reconciliation".
# ---------------------------------------------------------------------------


def total_phase_time_s(phase_lengths_s: Sequence[float]) -> float:
    """Table 3-3 formula 1: "Total phase time is the sum of all phase lengths
    in an hour". Citation: :data:`CIT_3_5_15_2_TOTAL_PHASE_TIME`.

    Trivial by design — it exists so that the *input* to every other formula on
    this page has one definition and one citation, and so a caller cannot
    quietly hand in a mean where the table wants a sum.
    """
    lengths = [_require_non_negative("phase length", length) for length in phase_lengths_s]
    if not lengths:
        raise ValueError("total_phase_time_s: no phase lengths supplied")
    return math.fsum(lengths)


def demand_frequency(occurrences: int, n_cycles: int) -> float:
    """Table 3-3 column 1: "Demand Frequencies = Number of Occurrences /
    Number of Cycles". Citation: :data:`CIT_3_5_15_2_DEMAND_FREQUENCY`.

    **Same rule as Appendix A §A.2's fᵢ**, so this is ``fixed_time.
    phase_frequency`` and not a second division (module docstring, "A.2
    reconciliation"). Worked: C phase, 22 occurrences in 36 cycles →
    22/36 = 0.6111… , printed by the document as 61 %.
    """
    return phase_frequency(occurrences, n_cycles)


def actual_average_phase_time_s(total_phase_time_s: float, occurrences: int) -> float:
    """Table 3-3 formula 2: "Average phase time = total phase time in an hour ÷
    number of occurrences". Citation: :data:`CIT_3_5_15_2_ACTUAL_AVERAGE`.

    **Same rule as Appendix A §A.2's "SCATS avg phase time"** — shared code.
    This is the column that must NOT be summed into a cycle time: 62 + 31 + 12
    = 105 s, and p.105 says to model 100.
    """
    return scats_average_phase_time_s(total_phase_time_s, occurrences)


def weighted_average_phase_time_s(total_phase_time_s: float, n_cycles: int) -> float:
    """Table 3-3 formula 3: "Weighted average phase time = Total phase time in
    an hour ÷ number of cycles". Citation:
    :data:`CIT_3_5_15_2_WEIGHTED_AVERAGE`.

    **Same rule as Appendix A §A.2's t̄ᵢ** — shared code. Worked: C phase,
    264 s over 36 cycles → 7.333… s, which the document prints as 7.
    """
    return average_phase_time_s(total_phase_time_s, n_cycles)


def cycle_time_to_model_s(n_cycles: int, modelled_period_s: float = SECONDS_PER_HOUR) -> float:
    """p.105's load-bearing rule: "The cycle time of 100 seconds, not 105
    seconds, should be modelled replicating the correct number of cycles in an
    hour (36 cycles)". Citation: :data:`CIT_3_5_15_2_CYCLE_TO_MODEL`.

    3600 ÷ 36 = 100 s. This is :func:`weighted_average_phase_time_s` applied to
    the whole modelled period rather than to one phase — Σ(totalᵢ ÷ n) =
    (Σ totalᵢ) ÷ n — which is why it is the same function again and not a
    fourth implementation of a division.

    **ASSUMPTION A12**: the period defaults to one hour. That 3600 is not a
    fact about anyone's data; it is the length of the hour LinSig models, and a
    caller whose modelled period is not an hour must say so.
    """
    return average_phase_time_s(_require_positive("modelled_period_s", modelled_period_s), n_cycles)


# ---------------------------------------------------------------------------
# §3.5.15.2.1 Table 3-4 — bonus green
# ---------------------------------------------------------------------------


def weighted_average_green_s(
    cases: Sequence[tuple[float, float]], *, probability_sum_tolerance: float = 1e-9
) -> float:
    """Table 3-4 row 3: "Weighted average green is calculated based on the %
    activation" — Σ over cases of ``p(case) × green(SG, case)``.
    Citation: :data:`CIT_3_5_15_2_1_WEIGHTED_AVERAGE_GREEN`.

    ``cases`` is ``[(probability, green_s), …]``. Worked (Table 3-4, SG1)::

        0.61 × 51 + 0.39 × 63 = 31.11 + 24.57 = 55.68 s

    **ASSUMPTION A13**: the probabilities must sum to 1. A "weighted average"
    over cases that do not partition the modelled hour is not an average of
    anything, so an unpartitioned case set is refused rather than normalised —
    normalising would silently invent the missing case's green.
    """
    if not cases:
        raise ValueError("weighted_average_green_s: no cases supplied")
    tolerance = _require_non_negative("probability_sum_tolerance", probability_sum_tolerance)
    weighted = []
    total_probability = 0.0
    for index, (probability, green) in enumerate(cases):
        probability = _require_non_negative(f"case {index} probability", probability)
        if probability > 1.0:
            raise ValueError(f"case {index} probability must be <= 1, got {probability!r}")
        green = _require_non_negative(f"case {index} green_s", green)
        total_probability += probability
        weighted.append(probability * green)
    if abs(total_probability - 1.0) > tolerance:
        raise ValueError(
            "weighted_average_green_s: the case probabilities must sum to 1 (they partition "
            f"the modelled hour); got {total_probability!r}"
        )
    return math.fsum(weighted)


def bonus_green_s(weighted_average_green: float, modelled_green: float) -> float:
    """Table 3-4 row 4: "Bonus green time = weighted average green − modelled
    green (Figure 3-52)". Citation:
    :data:`CIT_3_5_15_2_1_BONUS_GREEN_FORMULA`.

    **SIGNED, and the sign is load-bearing.** Table 3-4's own SG3-unopposed
    bonus is −2.34 s, and §3.5.15.4.1 applies "negative bonus green time … to
    the traffic lanes that get less green time due to the early termination".
    Taking an absolute value here would hand LinSig extra capacity on exactly
    the movements that lost it.

    ``modelled_green`` is the green coded in Signal Timings View — the
    *demanded* case (Figure 3-52) in the worked example, not the weighted
    average. Worked (SG1): 55.68 − 51 = +4.68 s.
    """
    return _require_non_negative("weighted_average_green", weighted_average_green) - (
        _require_non_negative("modelled_green", modelled_green)
    )


def bonus_green_protected_case_s(activation_frequency: float, protection_period_s: float) -> float:
    """§3.5.15.3.1: the model codes the *protected* case (the pedestrian
    protection period always applies), so the bonus is positive:
    ``+(1 − f) × protection``. Citation:
    :data:`CIT_3_5_15_3_1_PROTECTED_CASE`.

    The doc's worked example: crossing activated 60 % of the time, 10 s timed
    red arrow → "a positive bonus green time of 4 seconds (40% x 10s)".

    This is not a separate rule — it is :func:`bonus_green_s` with the algebra
    done: with G the green available when the crossing is not called,
    weighted average = f(G − p) + (1 − f)G = G − f·p, modelled = G − p, so the
    bonus is p(1 − f) and G cancels. The tests assert that equivalence rather
    than trusting this comment.
    """
    frequency = _require_non_negative("activation_frequency", activation_frequency)
    if frequency > 1.0:
        raise ValueError(f"activation_frequency must be <= 1, got {activation_frequency!r}")
    return (1.0 - frequency) * _require_non_negative("protection_period_s", protection_period_s)


def bonus_green_unprotected_case_s(
    activation_frequency: float, protection_period_s: float
) -> float:
    """§3.5.15.3.2: the model codes the *unprotected* case (a dummy phase
    without the pedestrian signal group), which "would overestimate capacity if
    the pedestrian crossings are still occasionally activated", so the bonus is
    negative: ``−f × protection``. Citation:
    :data:`CIT_3_5_15_3_2_UNPROTECTED_CASE`.

    The doc's worked example: activated 20 % of the time, 10 s protection → "a
    negative bonus green time of 2 seconds (20% x 10 seconds protection
    period)".

    The mirror of :func:`bonus_green_protected_case_s`: the sign depends
    entirely on which case is coded in Signal Timings View, which is why both
    exist and neither is a default.
    """
    frequency = _require_non_negative("activation_frequency", activation_frequency)
    if frequency > 1.0:
        raise ValueError(f"activation_frequency must be <= 1, got {activation_frequency!r}")
    return -frequency * _require_non_negative("protection_period_s", protection_period_s)


# ---------------------------------------------------------------------------
# §3.10.1 / §3.11.2 degree of saturation, §3.11.2.1 PRC, §3.11.3 MMQ
# ---------------------------------------------------------------------------


def degree_of_saturation_pct(demand_flow: float, capacity_flow: float) -> float:
    """§3.11.2's per-lane definition: "the ratio of demand flow to the maximum
    flow which can be passed through the intersection from a particular lane",
    expressed in per cent (ASSUMPTION A16).
    Citations: :data:`CIT_3_11_2_DOS_DEFINITION`, :data:`CIT_3_10_1_DOS_PRIMARY`.

    Flow units are the caller's (pcu/h in LinSig) and cancel. A capacity of
    zero is refused rather than returning an infinite DoS: a lane that can pass
    nothing is not a lane with a very large degree of saturation, it is a
    modelling error upstream.
    """
    demand_flow = _require_non_negative("demand_flow", demand_flow)
    capacity_flow = _require_positive("capacity_flow", capacity_flow)
    return 100.0 * demand_flow / capacity_flow


def practical_reserve_capacity_pct(highest_dos_pct: float) -> float:
    """§3.11.2.1, both branches exactly as printed.
    Citation: :data:`CIT_3_11_2_1_PRC`::

        PRC = (90% − Highest DoS) / Highest DoS   if highest DoS <  90%
        PRC = (90% − Highest DoS) / 90%           if highest DoS >= 90%

    Worked, the doc's own: highest DoS 80.0 % → (90 − 80)/80 = 0.125 = 12.5 %.

    **ASSUMPTION A18**: the second branch is implemented as written, negative
    result and all, and callers are warned that the document gives it no worked
    example. The two branches agree at exactly 90 % (both give 0), so the
    function is continuous there.

    PRC is a function of cycle time (:data:`CIT_3_11_2_1_PRC_CYCLE_TIME`); the
    cycle time must be reported alongside any PRC this returns, which is why
    :class:`DegreeOfSaturationValidation` carries one.
    """
    dos = _require_positive("highest_dos_pct", highest_dos_pct)
    if dos < PRC_PRACTICAL_OPERATING_LEVEL_PCT:
        return 100.0 * (PRC_PRACTICAL_OPERATING_LEVEL_PCT - dos) / dos
    return 100.0 * (PRC_PRACTICAL_OPERATING_LEVEL_PCT - dos) / PRC_PRACTICAL_OPERATING_LEVEL_PCT


def mean_maximum_queue_m(mmq_pcu: float) -> float:
    """§3.11.3: "The results should be converted to meters assuming
    1 PCU = 7.35 m". Citation: :data:`CIT_3_11_3_MMQ_PCU`.

    Worked: 12 PCU × 7.35 = 88.2 m. **ASSUMPTION A19**: this conversion is
    stated for MMQ reporting only; §3.10.2's validation queue (Back of Uniform
    Queue at End of Red) is a different output with no stated conversion.
    """
    return _require_non_negative("mmq_pcu", mmq_pcu) * PCU_LENGTH_M


def mean_maximum_queue_pcu(mmq_m: float) -> float:
    """The inverse of :func:`mean_maximum_queue_m`, at the same published
    7.35 m. Citation: :data:`CIT_3_11_3_MMQ_PCU`. Worked: 100 m ÷ 7.35 =
    13.6054… PCU."""
    return _require_non_negative("mmq_m", mmq_m) / PCU_LENGTH_M


# ---------------------------------------------------------------------------
# Settings, issue plumbing, result types
# ---------------------------------------------------------------------------


class LinSigSettings(BaseModel):
    """Every tolerance the checks judge by, in one place and overridable —
    same shape as ``quality.QualityThresholds`` and
    ``fixed_time.ConversionSettings``.

    Note what is NOT here: :data:`DOS_VALIDATION_MAXIMUM_PCT` and
    :data:`PCU_LENGTH_M`. Those are the client authority's published numbers,
    not tolerances, and a settings field is an invitation to move them.
    """

    model_config = ConfigDict(extra="forbid")

    probability_sum_tolerance: float = Field(
        default=1e-6,
        ge=0,
        description="ASSUMPTION A13: how far the case probabilities may miss 1.0.",
    )
    modelled_period_tolerance_s: float = Field(
        default=5.0,
        ge=0,
        description=(
            "ASSUMPTION A12: how far the summed phase totals may miss the modelled period. "
            "Table 3-3's own total balances to the second; a whole hour that misses by more "
            "than a few means the window is not the period."
        ),
    )
    minimum_phase_time_tolerance_s: float = Field(
        default=0.0,
        ge=0,
        description=(
            "How far a weighted average may sit under a stated minimum phase time before it "
            "is reported. Default 0: the document's own example reports the shortfall."
        ),
    )


def _issue(
    severity: IssueSeverity,
    code: str,
    message: str,
    *,
    subject: str | None = None,
    citation: str | None = None,
    context: dict | None = None,
) -> ImportIssue:
    """Every issue this module emits carries the regime label in its context.

    Not decoration: a finding travels further than the result object it came
    from — into a report table, an approval card, a JSON payload — and the
    moment it arrives somewhere without :data:`REGIME` attached, nothing
    distinguishes a LinSig DoS from a Vissim validation number.
    """
    payload = dict(context or {})
    payload.setdefault("regime", REGIME)
    return ImportIssue(
        severity=severity,
        code=code,
        message=message,
        subject=subject,
        citation=citation,
        context=payload,
    )


def _regime_notice(subject: str | None) -> ImportIssue:
    """Always emitted, by every entry point in this module."""
    return _issue(
        IssueSeverity.INFO,
        "linsig_regime_is_linsig_not_vissim",
        "These numbers feed LinSig's validation regime (§3.10: degree of saturation and queue "
        "at the start of green). They are NOT Vissim numbers and carry no GEH: a LinSig DoS "
        "and a Vissim GEH are different acceptance tests from different chapters, and neither "
        "substitutes for the other. Label them wherever they are reported.",
        subject=subject,
        citation=CIT_3_10_VALIDATION_COMPARISONS,
    )


class _IssueReport:
    """The four severity views every result object in this module has. A mixin
    rather than a base dataclass so each result can order its own fields."""

    issues: tuple[ImportIssue, ...]

    @property
    def refusals(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.issues if i.severity is IssueSeverity.REFUSE)

    @property
    def warnings(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.issues if i.severity is IssueSeverity.WARN)

    @property
    def infos(self) -> tuple[ImportIssue, ...]:
        return tuple(i for i in self.issues if i.severity is IssueSeverity.INFO)

    @property
    def ok(self) -> bool:
        return not self.refusals

    def issue_codes(self, severity: IssueSeverity | None = None) -> list[str]:
        return [i.code for i in self.issues if severity is None or i.severity is severity]


#: Every code this module can emit at REFUSE tier. Kept as data so "these
#: produce no table at all" is testable rather than aspirational — the test
#: reads the module's syntax tree and requires this set to be exactly the set
#: of codes emitted with ``IssueSeverity.REFUSE``.
REFUSE_CODES: frozenset[str] = frozenset(
    {
        "linsig_no_phase_rows",
        "linsig_duplicate_phase_id",
        "linsig_cycle_count_unusable",
        "linsig_phase_total_unusable",
        "linsig_occurrences_unusable",
        "linsig_phase_totals_contradict_modelled_period",
        "linsig_no_cases",
        "linsig_case_probability_unusable",
        "linsig_case_probabilities_do_not_sum_to_one",
        "linsig_modelled_case_unidentified",
        "linsig_modelled_case_ambiguous",
        "linsig_case_signal_groups_differ",
        "linsig_case_green_unusable",
        "linsig_duplicate_signal_group_spec",
        "linsig_filtered_turn_not_split",
        "linsig_dos_no_lanes",
        "linsig_dos_capacity_unusable",
        "linsig_dos_demand_unusable",
        "linsig_dos_exceeds_validation_maximum",
        "linsig_duplicate_lane_id",
        "linsig_mmq_no_lanes",
        "linsig_mmq_unusable",
        "linsig_lane_length_unusable",
    }
)


# ---------------------------------------------------------------------------
# Table 3-3 — infrequent-phase table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PhaseObservation:
    """One phase's hour, as Table 3-3's input columns.

    Deliberately not ``schema.PhaseData``: see the module docstring's layering
    argument. ``occurrences`` may be ``None`` — the weighted average needs only
    the total and the cycle count, so a source that lost the occurrence counts
    still gets the column the document says to model, with the two columns that
    need occurrences left unknown.
    """

    phase_id: str
    total_phase_time_s: float
    occurrences: int | None = None
    minimum_phase_time_s: float | None = None


@dataclass(frozen=True)
class InfrequentPhaseRow:
    """One row of Table 3-3, every number re-derivable by hand from
    ``total_phase_time_s``, ``occurrences`` and the table's cycle count::

        demand_frequency              = occurrences / n_cycles
        actual_average_phase_time_s   = total / occurrences
        weighted_average_phase_time_s = total / n_cycles
    """

    phase_id: str
    occurrences: int | None
    total_phase_time_s: float
    demand_frequency: float | None
    actual_average_phase_time_s: float | None
    weighted_average_phase_time_s: float
    minimum_phase_time_s: float | None
    below_minimum_phase_time: bool


@dataclass(frozen=True)
class InfrequentPhaseTable(_IssueReport):
    """Table 3-3 computed, plus the one sentence that makes it matter.

    ``cycle_time_to_model_s`` is the number p.105 tells the modeller to code
    ("100 seconds, not 105 seconds"); ``actual_average_cycle_s`` is the 105 —
    carried, named, and never returned as the thing to model.

    ``rows`` is empty exactly when a ``REFUSE`` is present.
    """

    n_cycles: int | None
    modelled_period_s: float
    rows: tuple[InfrequentPhaseRow, ...]
    total_phase_time_s: float | None
    actual_average_cycle_s: float | None
    weighted_average_cycle_s: float | None
    cycle_time_to_model_s: float | None
    issues: tuple[ImportIssue, ...]
    regime: str = REGIME


def build_infrequent_phase_table(
    phases: Sequence[PhaseObservation],
    n_cycles: int,
    *,
    modelled_period_s: float = SECONDS_PER_HOUR,
    settings: LinSigSettings | None = None,
    subject: str | None = None,
) -> InfrequentPhaseTable:
    """§3.5.15.2's Table 3-3 for one intersection-hour.

    Returns a table **always** — the import layer's "report, do not raise"
    contract. ``table.ok`` is false and ``table.rows`` is empty when any check
    refused.

    Where the raise/report line falls, and why it is not arbitrary:
    ``phases`` and ``n_cycles`` are *observations* (what the history file said
    happened), so a bad one is a finding and comes back as a REFUSE.
    ``modelled_period_s`` and ``settings`` are *modelling decisions made by
    this caller*, so a nonsense one is a bug in the caller and raises.
    """
    settings = settings or LinSigSettings()
    issues: list[ImportIssue] = [_regime_notice(subject)]

    stated_cycles = n_cycles if isinstance(n_cycles, int) and not isinstance(n_cycles, bool) else None

    def _bail() -> InfrequentPhaseTable:
        return InfrequentPhaseTable(
            n_cycles=stated_cycles,
            modelled_period_s=modelled_period_s,
            rows=(),
            total_phase_time_s=None,
            actual_average_cycle_s=None,
            weighted_average_cycle_s=None,
            cycle_time_to_model_s=None,
            issues=tuple(issues),
        )

    if not phases:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "linsig_no_phase_rows",
                "No phase observations supplied, so there is no Table 3-3 to build.",
                subject=subject,
                citation=CIT_3_5_15_2_TOTAL_PHASE_TIME,
            )
        )
        return _bail()

    phase_ids = [p.phase_id for p in phases]
    duplicates = sorted({phase_id for phase_id in phase_ids if phase_ids.count(phase_id) > 1})
    if duplicates:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "linsig_duplicate_phase_id",
                f"Phase id(s) {duplicates} appear more than once. Table 3-3 has one row per "
                "phase and its totals are sums over distinct phases; two rows for one phase "
                "would double-count the hour.",
                subject=subject,
                citation=CIT_3_5_15_2_TOTAL_PHASE_TIME,
                context={"phase_ids": duplicates},
            )
        )
        return _bail()

    if not isinstance(n_cycles, int) or isinstance(n_cycles, bool) or n_cycles <= 0:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "linsig_cycle_count_unusable",
                f"Number of cycles is {n_cycles!r}. Every column of Table 3-3 except the total "
                "divides by it, and the cycle time to model is the modelled period ÷ it, so a "
                "missing or non-positive count cannot be worked around.",
                subject=subject,
                citation=CIT_3_5_15_2_WEIGHTED_AVERAGE,
                context={"n_cycles": n_cycles},
            )
        )
        return _bail()

    for observation in phases:
        total = observation.total_phase_time_s
        if not _is_usable_number(total):
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "linsig_phase_total_unusable",
                    f"Phase {observation.phase_id} states a total phase time of {total!r}. "
                    "Table 3-3's every other column is derived from it.",
                    subject=subject,
                    citation=CIT_3_5_15_2_TOTAL_PHASE_TIME,
                    context={"phase_id": observation.phase_id, "total_phase_time_s": total},
                )
            )
            return _bail()
        occurrences = observation.occurrences
        if occurrences is not None and (
            not isinstance(occurrences, int) or isinstance(occurrences, bool) or occurrences < 0
        ):
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "linsig_occurrences_unusable",
                    f"Phase {observation.phase_id} states {occurrences!r} occurrences. The "
                    "demand frequency is occurrences ÷ cycles, so it must be a non-negative "
                    "count or absent.",
                    subject=subject,
                    citation=CIT_3_5_15_2_DEMAND_FREQUENCY,
                    context={"phase_id": observation.phase_id, "occurrences": occurrences},
                )
            )
            return _bail()

    total_time = total_phase_time_s([p.total_phase_time_s for p in phases])
    period = _require_positive("modelled_period_s", modelled_period_s)
    difference = abs(total_time - period)
    if difference > settings.modelled_period_tolerance_s:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "linsig_phase_totals_contradict_modelled_period",
                f"The phase totals sum to {total_time:g} s but the modelled period is "
                f"{period:g} s (difference {difference:g} s, tolerance "
                f"{settings.modelled_period_tolerance_s:g} s). Table 3-3's total column is the "
                "modelled hour itself, and the cycle time to model is that period ÷ the cycle "
                "count — if the observations do not fill the period, the two routes to the "
                "modelled cycle disagree and neither can be trusted.",
                subject=subject,
                citation=CIT_3_5_15_2_CYCLE_TO_MODEL,
                context={
                    "total_phase_time_s": total_time,
                    "modelled_period_s": period,
                    "difference_s": difference,
                    "tolerance_s": settings.modelled_period_tolerance_s,
                },
            )
        )
        return _bail()
    if difference > _FLOAT_EQUALITY_EPSILON:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "linsig_phase_totals_differ_slightly",
                f"The phase totals sum to {total_time:g} s against a modelled period of "
                f"{period:g} s ({difference:g} s apart, inside the tolerance). The cycle time "
                "to model was computed from the PERIOD, so it and the sum of the weighted "
                "averages differ by that much ÷ the cycle count.",
                subject=subject,
                citation=CIT_3_5_15_2_CYCLE_TO_MODEL,
                context={
                    "total_phase_time_s": total_time,
                    "modelled_period_s": period,
                    "difference_s": difference,
                },
            )
        )

    rows: list[InfrequentPhaseRow] = []
    unstated_occurrences: list[str] = []
    above_one: list[str] = []
    below_minimum: list[str] = []
    for observation in phases:
        total = float(observation.total_phase_time_s)
        weighted = weighted_average_phase_time_s(total, n_cycles)
        occurrences = observation.occurrences
        if occurrences is None:
            unstated_occurrences.append(observation.phase_id)
            frequency = None
            actual = None
        else:
            frequency = demand_frequency(occurrences, n_cycles)
            actual = actual_average_phase_time_s(total, occurrences) if occurrences else None
            if frequency > 1.0:
                above_one.append(observation.phase_id)
        minimum = observation.minimum_phase_time_s
        under = (
            minimum is not None
            and weighted < minimum - settings.minimum_phase_time_tolerance_s
        )
        if under:
            below_minimum.append(observation.phase_id)
        rows.append(
            InfrequentPhaseRow(
                phase_id=observation.phase_id,
                occurrences=occurrences,
                total_phase_time_s=total,
                demand_frequency=frequency,
                actual_average_phase_time_s=actual,
                weighted_average_phase_time_s=weighted,
                minimum_phase_time_s=minimum,
                below_minimum_phase_time=bool(under),
            )
        )

    if unstated_occurrences:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "linsig_occurrences_unstated",
                f"Phase(s) {unstated_occurrences} state no occurrence count, so their demand "
                "frequency and actual average phase time are unknown. The weighted average — "
                "the column p.105 says to model — is unaffected: it divides by the cycle "
                "count, not by occurrences. A bonus-green case weighting still needs the "
                "frequency, so it cannot be built from this table alone.",
                subject=subject,
                citation=CIT_3_5_15_2_DEMAND_FREQUENCY,
                context={"phase_ids": unstated_occurrences},
            )
        )
    if above_one:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "linsig_demand_frequency_above_one",
                f"Phase(s) {above_one} ran more often than there were cycles (frequency above "
                "100 %). That is representable — Appendix A's repeat-right-turn phasing runs a "
                "phase twice in a cycle — but Table 3-3's own frequencies are all ≤ 100 % and "
                "such a frequency CANNOT be used as a phase-sequence case probability, which "
                "must partition the hour.",
                subject=subject,
                citation=CIT_3_5_15_2_DEMAND_FREQUENCY,
                context={"phase_ids": above_one},
            )
        )
    if below_minimum:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "linsig_weighted_average_below_minimum_phase_time",
                f"Phase(s) {below_minimum}: the weighted average phase time is below the stated "
                "minimum phase time — the document's own worked example ('the weighted average "
                "phase time for C phase is shorter than minimum phase time'). This is the "
                "condition demand-dependency adjustment exists for, not a defect: the phase "
                "cannot simply be coded at its weighted average, so bonus green (Main Roads' "
                "preferred method) or one of the alternatives is required.",
                subject=subject,
                citation=CIT_3_5_15_2_MINIMUM_PHASE_TIME,
                context={"phase_ids": below_minimum},
            )
        )

    weighted_cycle = math.fsum(row.weighted_average_phase_time_s for row in rows)
    actual_cycle = (
        math.fsum(row.actual_average_phase_time_s or 0.0 for row in rows)
        if all(row.actual_average_phase_time_s is not None for row in rows)
        else None
    )
    to_model = cycle_time_to_model_s(n_cycles, period)

    issues.append(
        _issue(
            IssueSeverity.INFO,
            "linsig_actual_average_cycle_is_not_the_modelled_cycle",
            f"Model the WEIGHTED-average cycle: {to_model:g} s over {n_cycles} cycles"
            + (
                f", not the {actual_cycle:g} s you get by summing the actual average phase "
                "times."
                if actual_cycle is not None
                else " (the actual-average cycle could not be formed: some phase states no "
                "occurrence count)."
            )
            + " p.105: 'The cycle time of 100 seconds, not 105 seconds, should be modelled "
            "replicating the correct number of cycles in an hour'. Summing the actual averages "
            "is the classic error this section exists to prevent — it over-states the cycle by "
            "whatever the infrequent phases' skipped cycles were worth.",
            subject=subject,
            citation=CIT_3_5_15_2_CYCLE_TO_MODEL,
            context={
                "cycle_time_to_model_s": to_model,
                "weighted_average_cycle_s": weighted_cycle,
                "actual_average_cycle_s": actual_cycle,
                "n_cycles": n_cycles,
            },
        )
    )

    return InfrequentPhaseTable(
        n_cycles=n_cycles,
        modelled_period_s=period,
        rows=tuple(rows),
        total_phase_time_s=total_time,
        actual_average_cycle_s=actual_cycle,
        weighted_average_cycle_s=weighted_cycle,
        cycle_time_to_model_s=to_model,
        issues=tuple(issues),
    )


# ---------------------------------------------------------------------------
# Table 3-4 — bonus green
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SignalGroupSpec:
    """What the module needs to know about a signal group that its green times
    alone cannot say.

    ``filtered_right_turn`` is the one that changes an answer into a refusal:
    p.107 requires such a group's opposed and unopposed periods to be
    calculated separately "as it affects the capacity calculations for the
    filtered right-turn movement". ``lanes`` is where the bonus is actually
    applied (Lane Timings View) — see ASSUMPTION A15.
    """

    sg_id: str
    filtered_right_turn: bool = False
    lanes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PhaseSequenceCase:
    """One column-block of Table 3-4: a phase sequence, how often it runs, and
    the green each signal-group period gets when it does.

    ``is_modelled`` marks the case coded in Signal Timings View — the one the
    bonus is measured against ("modelled green (Figure 3-52)"). Exactly one
    case must carry it; the module will not guess which drawing was coded.
    """

    label: str
    probability: float
    greens: Mapping[GreenKey, float]
    is_modelled: bool = False


@dataclass(frozen=True)
class BonusGreenRow:
    """One Table 3-4 column, worked. ``bonus_green_s`` is SIGNED."""

    key: GreenKey
    case_greens: tuple[tuple[str, float, float], ...]
    weighted_average_green_s: float
    modelled_green_s: float
    bonus_green_s: float
    lanes: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return self.key.label


@dataclass(frozen=True)
class BonusGreenTable(_IssueReport):
    """Table 3-4 computed: per signal-group period, and per lane.

    ``rows`` is empty exactly when a ``REFUSE`` is present.
    """

    rows: tuple[BonusGreenRow, ...]
    modelled_case_label: str | None
    issues: tuple[ImportIssue, ...]
    regime: str = REGIME

    @property
    def lane_applications(self) -> dict[str, tuple[tuple[str, float], ...]]:
        """``lane -> ((column label, signed bonus), …)`` — the Lane Timings
        View entry the doc's step 4 asks for. A lane serving a filtered right
        turn legitimately receives two entries, one per give-way period."""
        applications: dict[str, list[tuple[str, float]]] = {}
        for row in self.rows:
            for lane in row.lanes:
                applications.setdefault(lane, []).append((row.label, row.bonus_green_s))
        return {lane: tuple(entries) for lane, entries in applications.items()}


def compute_bonus_green(
    cases: Sequence[PhaseSequenceCase],
    signal_groups: Sequence[SignalGroupSpec] = (),
    *,
    settings: LinSigSettings | None = None,
    subject: str | None = None,
) -> BonusGreenTable:
    """§3.5.15.1.1's four steps, as Table 3-4 works them (p.107).

    Steps 1-2 ("total green in the modelled hour ÷ the number of cycles")
    arrive already done, as the per-case greens and their % activation — which
    is how the document itself tabulates them. Step 3 is
    :func:`weighted_average_green_s` then :func:`bonus_green_s`; step 4 is
    :attr:`BonusGreenTable.lane_applications`.

    Returns a table **always**; ``table.ok`` is false and ``table.rows`` empty
    when any check refused.
    """
    settings = settings or LinSigSettings()
    issues: list[ImportIssue] = [_regime_notice(subject)]

    def _bail() -> BonusGreenTable:
        return BonusGreenTable(rows=(), modelled_case_label=None, issues=tuple(issues))

    if not cases:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "linsig_no_cases",
                "No phase-sequence cases supplied. The weighted average green is a sum over "
                "cases; with none there is nothing to weight.",
                subject=subject,
                citation=CIT_3_5_15_1_1_BONUS_GREEN_METHOD,
            )
        )
        return _bail()

    spec_ids = [spec.sg_id for spec in signal_groups]
    duplicate_specs = sorted({sg for sg in spec_ids if spec_ids.count(sg) > 1})
    if duplicate_specs:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "linsig_duplicate_signal_group_spec",
                f"Signal group(s) {duplicate_specs} are declared more than once; the module "
                "will not choose between two declarations of the same group (one of them may "
                "be the one that says it is a filtered right turn).",
                subject=subject,
                citation=CIT_3_5_15_2_1_FILTERED_TURN_SPLIT,
                context={"sg_ids": duplicate_specs},
            )
        )
        return _bail()
    specs = {spec.sg_id: spec for spec in signal_groups}

    total_probability = 0.0
    for case in cases:
        probability = case.probability
        if not _is_usable_number(probability) or float(probability) > 1.0:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "linsig_case_probability_unusable",
                    f"Case {case.label!r} states a % activation of {probability!r}. It weights "
                    "that case's green in the average, so it must be a share between 0 and 1.",
                    subject=subject,
                    citation=CIT_3_5_15_2_1_WEIGHTED_AVERAGE_GREEN,
                    context={"case": case.label, "probability": probability},
                )
            )
            return _bail()
        total_probability += float(probability)

    if abs(total_probability - 1.0) > settings.probability_sum_tolerance:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "linsig_case_probabilities_do_not_sum_to_one",
                f"The case % activations sum to {total_probability:g}, not 1. Table 3-4's own "
                "cases (61 % + 39 %) partition the hour: every cycle runs exactly one of the "
                "sequences. A set that does not partition it has a case missing or double "
                "counted, and the module will not normalise the gap away — normalising would "
                "invent the missing case's green.",
                subject=subject,
                citation=CIT_3_5_15_2_1_WEIGHTED_AVERAGE_GREEN,
                context={
                    "probability_sum": total_probability,
                    "case_labels": [case.label for case in cases],
                },
            )
        )
        return _bail()

    modelled = [case for case in cases if case.is_modelled]
    if not modelled:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "linsig_modelled_case_unidentified",
                "No case is marked as the one coded in Signal Timings View. The bonus is "
                "'weighted average green − modelled green', and which case is modelled is a "
                "decision about the LinSig file, not something the numbers reveal.",
                subject=subject,
                citation=CIT_3_5_15_2_1_BONUS_GREEN_FORMULA,
                context={"case_labels": [case.label for case in cases]},
            )
        )
        return _bail()
    if len(modelled) > 1:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "linsig_modelled_case_ambiguous",
                f"{len(modelled)} cases are marked as modelled "
                f"({[case.label for case in modelled]}). Signal Timings View codes one phase "
                "split; the bonus is measured against that one.",
                subject=subject,
                citation=CIT_3_5_15_2_1_BONUS_GREEN_FORMULA,
                context={"case_labels": [case.label for case in modelled]},
            )
        )
        return _bail()
    modelled_case = modelled[0]

    key_sets = [frozenset(case.greens) for case in cases]
    if len(set(key_sets)) != 1:
        union = set().union(*key_sets)
        differing = {
            case.label: sorted(k.label for k in union - set(case.greens)) for case in cases
        }
        missing_only = {label: missing for label, missing in differing.items() if missing}
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "linsig_case_signal_groups_differ",
                "The cases do not describe the same signal-group periods: "
                f"{missing_only}. A "
                "group absent from one case is UNKNOWN, not zero — Figure 3-53 states its "
                "zero explicitly (SG3 unopposed = 0), which is exactly the distinction that "
                "would be lost by defaulting.",
                subject=subject,
                citation=CIT_3_5_15_2_1_WEIGHTED_AVERAGE_GREEN,
                context={"missing_by_case": missing_only},
            )
        )
        return _bail()
    keys = sorted(key_sets[0], key=_sort_key)

    for case in cases:
        for key in keys:
            green = case.greens[key]
            if not _is_usable_number(green):
                issues.append(
                    _issue(
                        IssueSeverity.REFUSE,
                        "linsig_case_green_unusable",
                        f"Case {case.label!r} states a green of {green!r} for {key.label}. A "
                        "green time is a non-negative number of seconds; the bonus that would "
                        "be derived from it is what LinSig then models.",
                        subject=subject,
                        citation=CIT_3_5_15_2_1_WEIGHTED_AVERAGE_GREEN,
                        context={"case": case.label, "column": key.label, "green_s": green},
                    )
                )
                return _bail()

    # The mandatory opposed/unopposed split (p.107). Checked before any number
    # is produced: a single bonus figure for a filtered right turn is wrong by
    # construction, not merely imprecise.
    periods_by_sg: dict[str, set[GivewayPeriod | None]] = {}
    for key in keys:
        periods_by_sg.setdefault(key.sg_id, set()).add(key.period)
    unsplit = sorted(
        sg_id
        for sg_id, periods in periods_by_sg.items()
        if specs.get(sg_id) is not None
        and specs[sg_id].filtered_right_turn
        and periods != {GivewayPeriod.OPPOSED, GivewayPeriod.UNOPPOSED}
    )
    if unsplit:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "linsig_filtered_turn_not_split",
                f"Signal group(s) {unsplit} are declared filtered right turns but their green "
                "is not split into opposed and unopposed periods. p.107 is explicit: 'It is "
                "important to separate SG3 to opposed and unopposed periods as it affects the "
                "capacity calculations during the give-way period.' A single weighted average "
                "for such a group mixes give-way capacity with unopposed capacity, so the "
                "bonus derived from it is wrong by construction — there is no partial answer "
                "to give here.",
                subject=subject,
                citation=CIT_3_5_15_2_1_OPPOSED_SPLIT,
                context={
                    "sg_ids": unsplit,
                    "periods_present": {
                        sg_id: sorted(
                            "whole signal group" if p is None else p.value
                            for p in periods_by_sg[sg_id]
                        )
                        for sg_id in unsplit
                    },
                },
            )
        )
        return _bail()

    undeclared = sorted({key.sg_id for key in keys} - set(specs))
    if undeclared:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "linsig_signal_group_undeclared",
                f"Signal group(s) {undeclared} appear in the cases but were not declared, so "
                "TWO things were not checked for them: whether they are filtered right turns "
                "needing the mandatory opposed/unopposed split, and which lanes their bonus is "
                "applied to. Silence here is not a pass — declare them to have them checked.",
                subject=subject,
                citation=CIT_3_5_15_2_1_FILTERED_TURN_SPLIT,
                context={"sg_ids": undeclared},
            )
        )
    absent = sorted(set(specs) - {key.sg_id for key in keys})
    if absent:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "linsig_signal_group_not_in_cases",
                f"Signal group(s) {absent} were declared but appear in no case, so they get no "
                "bonus green row. If they run at this intersection their green is missing from "
                "the calculation entirely.",
                subject=subject,
                citation=CIT_3_5_15_2_1_WEIGHTED_AVERAGE_GREEN,
                context={"sg_ids": absent},
            )
        )

    rows: list[BonusGreenRow] = []
    unplaced: list[str] = []
    negative: list[str] = []
    for key in keys:
        weighted = weighted_average_green_s(
            [(float(case.probability), float(case.greens[key])) for case in cases],
            probability_sum_tolerance=settings.probability_sum_tolerance,
        )
        modelled_green = float(modelled_case.greens[key])
        bonus = bonus_green_s(weighted, modelled_green)
        lanes = specs[key.sg_id].lanes if key.sg_id in specs else ()
        if not lanes and abs(bonus) > _FLOAT_EQUALITY_EPSILON:
            unplaced.append(key.label)
        if bonus < -_FLOAT_EQUALITY_EPSILON:
            negative.append(key.label)
        rows.append(
            BonusGreenRow(
                key=key,
                case_greens=tuple(
                    (case.label, float(case.probability), float(case.greens[key]))
                    for case in cases
                ),
                weighted_average_green_s=weighted,
                modelled_green_s=modelled_green,
                bonus_green_s=bonus,
                lanes=tuple(lanes),
            )
        )

    if unplaced:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "linsig_bonus_green_no_lane_declared",
                f"Column(s) {unplaced} have a non-zero bonus green but no declared lane. Step 4 "
                "of the method applies the bonus in Lane Timings View; a bonus that reaches no "
                "lane changes nothing in the model (ASSUMPTION A15 — it is computed and "
                "reported, never silently dropped).",
                subject=subject,
                citation=CIT_3_5_15_1_1_APPLY_TO_LANES,
                context={"columns": unplaced},
            )
        )
    if negative:
        issues.append(
            _issue(
                IssueSeverity.INFO,
                "linsig_bonus_green_negative",
                f"Column(s) {negative} carry a NEGATIVE bonus green, which is correct and "
                "intended: Table 3-4's own SG3-unopposed bonus is −2.34 s, and §3.5.15.4.1 "
                "applies negative bonus green 'to the traffic lanes that get less green time "
                "due to the early termination'. Do not enter these as magnitudes.",
                subject=subject,
                citation=CIT_3_5_15_4_1_SIGNED_BONUS,
                context={"columns": negative},
            )
        )

    issues.append(
        _issue(
            IssueSeverity.INFO,
            "linsig_demand_dependency_judgement_required",
            "Bonus green is Main Roads' PREFERRED demand-dependency method, not the only one, "
            "and the document is explicit that 'there is not a definitive method'. The choice "
            "of method, the frequencies used, and the assumptions behind them are the "
            "modeller's engineering judgement, to be discussed with Main Roads and documented "
            "in the modelling report. Methods must not be mixed within a project.",
            subject=subject,
            citation=CIT_3_5_15_JUDGEMENT,
        )
    )
    issues.append(
        _issue(
            IssueSeverity.INFO,
            "linsig_bonus_green_spreadsheet_required",
            "p.107: 'The green time calculation spreadsheet should be included in the modelling "
            "submission for ease of reference.' This table IS that calculation — every row "
            "carries its per-case greens, its weighted average and its modelled green, so the "
            "submission artefact can be produced from it without re-deriving anything.",
            subject=subject,
            citation=CIT_3_5_15_2_1_SPREADSHEET,
        )
    )

    return BonusGreenTable(
        rows=tuple(rows), modelled_case_label=modelled_case.label, issues=tuple(issues)
    )


# ---------------------------------------------------------------------------
# §3.10.1 degree-of-saturation validation
# ---------------------------------------------------------------------------


class ValidationDataBasis(str, Enum):
    """§3.10.1's precondition, made explicit (ASSUMPTION A17).

    The ≤ 100 % rule is stated for a base model whose "traffic data is
    collected using stop line turning counts and the signal timings are
    extracted from the SCATS system". A caller that cannot assert that has a
    model the rule was not written about.
    """

    #: Stop-line turning counts + SCATS-extracted signal timings: the rule applies.
    STOP_LINE_COUNTS_AND_SCATS = "stop_line_counts_and_scats"
    #: Anything else, or unknown: DoS is still computed, no pass/fail is claimed.
    OTHER = "other"


@dataclass(frozen=True)
class LaneSaturation:
    """One lane's flow and capacity, in the caller's own units (pcu/h in
    LinSig). The units cancel in the ratio; what matters is that both come from
    the same model run."""

    lane_id: str
    demand_flow: float
    capacity_flow: float


@dataclass(frozen=True)
class DegreeOfSaturationRow:
    lane_id: str
    demand_flow: float
    capacity_flow: float
    dos_pct: float
    exceeds_validation_maximum: bool


@dataclass(frozen=True)
class DegreeOfSaturationValidation(_IssueReport):
    """§3.10.1's validation, with its precondition and its silence about queues
    both carried in the result rather than left to the reader.

    ``assessed`` is False when the data basis is not the one §3.10.1 names: the
    DoS values are still reported, but no pass has been claimed.
    """

    rows: tuple[DegreeOfSaturationRow, ...]
    highest_dos_pct: float | None
    highest_dos_lane_id: str | None
    practical_reserve_capacity_pct: float | None
    cycle_time_s: float | None
    basis: ValidationDataBasis
    assessed: bool
    issues: tuple[ImportIssue, ...]
    regime: str = REGIME


def validate_degree_of_saturation(
    lanes: Sequence[LaneSaturation],
    *,
    basis: ValidationDataBasis = ValidationDataBasis.OTHER,
    cycle_time_s: float | None = None,
    subject: str | None = None,
) -> DegreeOfSaturationValidation:
    """§3.10.1's whole numeric rule: **DoS ≤ 100 %**, flat, no banding.

    Unlike MRWA's Vissim chapter (Tables 5-12/5-13), there is no category split
    and no tolerance band here — the LinSig chapter states one number. Every
    result is labelled with :data:`REGIME` for that reason.

    Returns a validation **always**; ``ok`` is false when a lane exceeds the
    maximum on a base model built the way §3.10.1 names.

    There is no ``settings`` argument on purpose: the only number this function
    judges by is :data:`DOS_VALIDATION_MAXIMUM_PCT`, and that one is Main
    Roads', not ours to soften.
    """
    issues: list[ImportIssue] = [_regime_notice(subject)]
    assessed = basis is ValidationDataBasis.STOP_LINE_COUNTS_AND_SCATS

    def _bail() -> DegreeOfSaturationValidation:
        return DegreeOfSaturationValidation(
            rows=(),
            highest_dos_pct=None,
            highest_dos_lane_id=None,
            practical_reserve_capacity_pct=None,
            cycle_time_s=cycle_time_s,
            basis=basis,
            assessed=assessed,
            issues=tuple(issues),
        )

    if not lanes:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "linsig_dos_no_lanes",
                "No lanes supplied, so there is no degree of saturation to validate. An empty "
                "validation is not a passed validation.",
                subject=subject,
                citation=CIT_3_10_1_DOS_PRIMARY,
            )
        )
        return _bail()

    lane_ids = [lane.lane_id for lane in lanes]
    duplicates = sorted({lane_id for lane_id in lane_ids if lane_ids.count(lane_id) > 1})
    if duplicates:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "linsig_duplicate_lane_id",
                f"Lane id(s) {duplicates} appear more than once; the highest DoS would be "
                "attributed to an ambiguous lane.",
                subject=subject,
                citation=CIT_3_11_2_DOS_DEFINITION,
                context={"lane_ids": duplicates},
            )
        )
        return _bail()

    rows: list[DegreeOfSaturationRow] = []
    for lane in lanes:
        capacity = lane.capacity_flow
        if not _is_usable_number(capacity, allow_zero=False):
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "linsig_dos_capacity_unusable",
                    f"Lane {lane.lane_id} states a capacity of {capacity!r}. DoS is demand ÷ "
                    "capacity; a lane that can pass nothing has no degree of saturation, it "
                    "has a modelling error upstream.",
                    subject=subject,
                    citation=CIT_3_11_2_DOS_DEFINITION,
                    context={"lane_id": lane.lane_id, "capacity_flow": capacity},
                )
            )
            return _bail()
        demand = lane.demand_flow
        if not _is_usable_number(demand):
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "linsig_dos_demand_unusable",
                    f"Lane {lane.lane_id} states a demand flow of {demand!r}.",
                    subject=subject,
                    citation=CIT_3_11_2_DOS_DEFINITION,
                    context={"lane_id": lane.lane_id, "demand_flow": demand},
                )
            )
            return _bail()
        dos = degree_of_saturation_pct(float(demand), float(capacity))
        rows.append(
            DegreeOfSaturationRow(
                lane_id=lane.lane_id,
                demand_flow=float(demand),
                capacity_flow=float(capacity),
                dos_pct=dos,
                exceeds_validation_maximum=dos > DOS_VALIDATION_MAXIMUM_PCT
                + _FLOAT_EQUALITY_EPSILON,
            )
        )

    highest = max(rows, key=lambda row: row.dos_pct)
    exceeded = [row.lane_id for row in rows if row.exceeds_validation_maximum]

    if not assessed:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "linsig_dos_validation_basis_not_stated_rule",
                "§3.10.1's DoS ≤ 100 % rule is stated for a base model whose traffic data comes "
                "from stop-line turning counts and whose signal timings are extracted from "
                f"SCATS. This model's basis is {basis.value!r}, so the criterion was NOT "
                "applied and no pass has been claimed — the DoS values are reported, "
                "unassessed. Validation requirements are to be agreed with Main Roads before "
                "any data collection or modelling.",
                subject=subject,
                citation=CIT_3_10_1_DOS_MAXIMUM,
                context={"basis": basis.value, "assessed": False},
            )
        )

    if exceeded and assessed:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "linsig_dos_exceeds_validation_maximum",
                f"Lane(s) {exceeded} exceed §3.10.1's DoS maximum of "
                f"{DOS_VALIDATION_MAXIMUM_PCT:g} % (highest: {highest.lane_id} at "
                f"{highest.dos_pct:.1f} %). For a base model built from stop-line turning "
                "counts with SCATS-extracted signal timings, 'the DoS should not exceed 100 "
                "per cent' — flat, with no banding and no tolerance. The model is not "
                "validated.",
                subject=subject,
                citation=CIT_3_10_1_DOS_MAXIMUM,
                context={
                    "lane_ids": exceeded,
                    "highest_dos_pct": highest.dos_pct,
                    "highest_dos_lane_id": highest.lane_id,
                    "maximum_pct": DOS_VALIDATION_MAXIMUM_PCT,
                },
            )
        )
    elif exceeded:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "linsig_dos_exceeds_validation_maximum_basis_unstated",
                f"Lane(s) {exceeded} exceed 100 % degree of saturation (highest: "
                f"{highest.lane_id} at {highest.dos_pct:.1f} %). §3.10.1's rule is not being "
                "applied because the data basis is not the one it names, so this is reported "
                "rather than refused — but a DoS above 100 % is over-capacity in any regime "
                "and the magnitude should be reviewed against on-site conditions.",
                subject=subject,
                citation=CIT_3_10_1_ON_SITE_REVIEW,
                context={
                    "lane_ids": exceeded,
                    "highest_dos_pct": highest.dos_pct,
                    "basis": basis.value,
                },
            )
        )

    prc = practical_reserve_capacity_pct(highest.dos_pct) if highest.dos_pct > 0 else None
    if prc is not None and highest.dos_pct >= PRC_PRACTICAL_OPERATING_LEVEL_PCT:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "linsig_prc_negative",
                f"Highest DoS {highest.dos_pct:.1f} % is at or above the 90 % practical "
                f"operating level, so PRC comes from §3.11.2.1's SECOND branch and is "
                f"{prc:.2f} % — negative headroom. The document gives that branch no worked "
                "example (ASSUMPTION A18); it is implemented exactly as printed.",
                subject=subject,
                citation=CIT_3_11_2_1_PRC,
                context={"highest_dos_pct": highest.dos_pct, "prc_pct": prc},
            )
        )
    if prc is not None and cycle_time_s is None:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "linsig_prc_cycle_time_not_stated",
                f"PRC {prc:.2f} % was computed but no cycle time was supplied. §3.11.2.1: 'PRC "
                "is a function of cycle time and therefore the cycle time should be "
                "referenced.' A PRC reported without its cycle time is not comparable to any "
                "other PRC.",
                subject=subject,
                citation=CIT_3_11_2_1_PRC_CYCLE_TIME,
                context={"prc_pct": prc},
            )
        )

    issues.append(
        _issue(
            IssueSeverity.INFO,
            "linsig_dos_queue_validation_not_performed",
            "Degree of saturation is the PRIMARY validation method but not the only one: "
            "§3.10 requires the comparison to include queue length at the start of the green "
            "period as well, and this module did not check it. §3.10.2 states no numeric "
            "tolerance for queues ('similar with the same magnitude'), so coding one would be "
            "inventing the acceptance criterion. A DoS pass alone is not a validated model.",
            subject=subject,
            citation=CIT_3_10_2_QUEUE_NO_TOLERANCE,
        )
    )

    return DegreeOfSaturationValidation(
        rows=tuple(rows),
        highest_dos_pct=highest.dos_pct,
        highest_dos_lane_id=highest.lane_id,
        practical_reserve_capacity_pct=prc,
        cycle_time_s=cycle_time_s,
        basis=basis,
        assessed=assessed,
        issues=tuple(issues),
    )


# ---------------------------------------------------------------------------
# §3.11.3 mean maximum queue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QueueLane:
    """One lane's Mean Maximum Queue output, in PCU as LinSig reports it, and
    the physical length it has to fit into when that is known."""

    lane_id: str
    mmq_pcu: float
    lane_length_m: float | None = None


@dataclass(frozen=True)
class MeanMaximumQueueRow:
    lane_id: str
    mmq_pcu: float
    mmq_m: float
    lane_length_m: float | None
    exceeds_lane_length: bool


@dataclass(frozen=True)
class MeanMaximumQueueReport(_IssueReport):
    rows: tuple[MeanMaximumQueueRow, ...]
    highest_mmq_pcu: float | None
    highest_mmq_m: float | None
    highest_mmq_lane_id: str | None
    issues: tuple[ImportIssue, ...]
    regime: str = REGIME


def convert_mean_maximum_queues(
    lanes: Sequence[QueueLane], *, subject: str | None = None
) -> MeanMaximumQueueReport:
    """§3.11.3: MMQ in PCU → metres at 1 PCU = 7.35 m, with the fit check the
    section asks for ("whether it can fit into the physical operating space").

    "For Traffic Signal Approval, the highest MMQ output of each intersection is
    usually required", so the highest is carried on the report.
    """
    issues: list[ImportIssue] = [_regime_notice(subject)]

    def _bail() -> MeanMaximumQueueReport:
        return MeanMaximumQueueReport(
            rows=(),
            highest_mmq_pcu=None,
            highest_mmq_m=None,
            highest_mmq_lane_id=None,
            issues=tuple(issues),
        )

    if not lanes:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "linsig_mmq_no_lanes",
                "No lanes supplied, so there is no Mean Maximum Queue to convert.",
                subject=subject,
                citation=CIT_3_11_3_MMQ_PCU,
            )
        )
        return _bail()

    lane_ids = [lane.lane_id for lane in lanes]
    duplicates = sorted({lane_id for lane_id in lane_ids if lane_ids.count(lane_id) > 1})
    if duplicates:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "linsig_duplicate_lane_id",
                f"Lane id(s) {duplicates} appear more than once; the highest MMQ would be "
                "attributed to an ambiguous lane.",
                subject=subject,
                citation=CIT_3_11_3_MMQ_PCU,
                context={"lane_ids": duplicates},
            )
        )
        return _bail()

    rows: list[MeanMaximumQueueRow] = []
    unknown_length: list[str] = []
    overflowing: list[str] = []
    for lane in lanes:
        pcu = lane.mmq_pcu
        if not _is_usable_number(pcu):
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "linsig_mmq_unusable",
                    f"Lane {lane.lane_id} states an MMQ of {pcu!r} PCU.",
                    subject=subject,
                    citation=CIT_3_11_3_MMQ_PCU,
                    context={"lane_id": lane.lane_id, "mmq_pcu": pcu},
                )
            )
            return _bail()
        metres = mean_maximum_queue_m(float(pcu))
        length = lane.lane_length_m
        if length is None:
            unknown_length.append(lane.lane_id)
            exceeds = False
        elif not _is_usable_number(length, allow_zero=False):
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "linsig_lane_length_unusable",
                    f"Lane {lane.lane_id} states a physical length of {length!r}. A lane with "
                    "no length cannot hold a queue, so the fit check §3.11.3 asks for would be "
                    "meaningless — state the length or leave it absent.",
                    subject=subject,
                    citation=CIT_3_11_3_MMQ_LANE_LENGTH,
                    context={"lane_id": lane.lane_id, "lane_length_m": length},
                )
            )
            return _bail()
        else:
            length = float(length)
            exceeds = metres > length + _FLOAT_EQUALITY_EPSILON
            if exceeds:
                overflowing.append(lane.lane_id)
        rows.append(
            MeanMaximumQueueRow(
                lane_id=lane.lane_id,
                mmq_pcu=float(pcu),
                mmq_m=metres,
                lane_length_m=length,
                exceeds_lane_length=exceeds,
            )
        )

    if overflowing:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "linsig_mmq_exceeds_lane_length",
                f"Lane(s) {overflowing}: the modelled Mean Maximum Queue is longer than the "
                "physical lane — the condition LinSig itself highlights in red. The queue does "
                "not fit into the operating space, so it will block back into whatever is "
                "upstream, which a single-intersection LinSig model does not represent.",
                subject=subject,
                citation=CIT_3_11_3_MMQ_LANE_LENGTH,
                context={"lane_ids": overflowing},
            )
        )
    if unknown_length:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "linsig_mmq_lane_length_unknown",
                f"Lane(s) {unknown_length} state no physical length, so the fit check §3.11.3 "
                "asks for could not be run for them. The metre conversion is still exact.",
                subject=subject,
                citation=CIT_3_11_3_MMQ_LANE_LENGTH,
                context={"lane_ids": unknown_length},
            )
        )

    highest = max(rows, key=lambda row: row.mmq_pcu)
    return MeanMaximumQueueReport(
        rows=tuple(rows),
        highest_mmq_pcu=highest.mmq_pcu,
        highest_mmq_m=highest.mmq_m,
        highest_mmq_lane_id=highest.lane_id,
        issues=tuple(issues),
    )


__all__ = [
    "CIT_3_5_15_1_1_APPLY_TO_LANES",
    "CIT_3_5_15_1_1_BONUS_GREEN_METHOD",
    "CIT_3_5_15_2_1_BONUS_GREEN_FORMULA",
    "CIT_3_5_15_2_1_FILTERED_TURN_SPLIT",
    "CIT_3_5_15_2_1_OPPOSED_SPLIT",
    "CIT_3_5_15_2_1_SPREADSHEET",
    "CIT_3_5_15_2_1_WEIGHTED_AVERAGE_GREEN",
    "CIT_3_5_15_2_ACTUAL_AVERAGE",
    "CIT_3_5_15_2_CYCLE_TO_MODEL",
    "CIT_3_5_15_2_DEMAND_FREQUENCY",
    "CIT_3_5_15_2_MINIMUM_PHASE_TIME",
    "CIT_3_5_15_2_TOTAL_PHASE_TIME",
    "CIT_3_5_15_2_WEIGHTED_AVERAGE",
    "CIT_3_5_15_3_1_PROTECTED_CASE",
    "CIT_3_5_15_3_2_UNPROTECTED_CASE",
    "CIT_3_5_15_4_1_SIGNED_BONUS",
    "CIT_3_5_15_JUDGEMENT",
    "CIT_3_10_1_DOS_MAXIMUM",
    "CIT_3_10_1_DOS_PRIMARY",
    "CIT_3_10_1_ON_SITE_REVIEW",
    "CIT_3_10_2_QUEUE_NO_TOLERANCE",
    "CIT_3_10_VALIDATION_COMPARISONS",
    "CIT_3_11_2_1_PRC",
    "CIT_3_11_2_1_PRC_CYCLE_TIME",
    "CIT_3_11_2_DOS_DEFINITION",
    "CIT_3_11_3_MMQ_LANE_LENGTH",
    "CIT_3_11_3_MMQ_PCU",
    "DOS_VALIDATION_MAXIMUM_PCT",
    "EXTRACT",
    "PCU_LENGTH_M",
    "PRC_PRACTICAL_OPERATING_LEVEL_PCT",
    "REFUSE_CODES",
    "REGIME",
    "SECONDS_PER_HOUR",
    "TABLE_3_3_N_CYCLES",
    "TABLE_3_3_PUBLISHED",
    "TABLE_3_3_PUBLISHED_TOTALS",
    "TABLE_3_4_PUBLISHED_BONUS",
    "TABLE_3_4_PUBLISHED_CASES",
    "TABLE_3_4_PUBLISHED_WEIGHTED",
    "BonusGreenRow",
    "BonusGreenTable",
    "DegreeOfSaturationRow",
    "DegreeOfSaturationValidation",
    "GivewayPeriod",
    "GreenKey",
    "InfrequentPhaseRow",
    "InfrequentPhaseTable",
    "LaneSaturation",
    "LinSigSettings",
    "MeanMaximumQueueReport",
    "MeanMaximumQueueRow",
    "PhaseObservation",
    "PhaseSequenceCase",
    "QueueLane",
    "SignalGroupSpec",
    "ValidationDataBasis",
    "actual_average_phase_time_s",
    "bonus_green_protected_case_s",
    "bonus_green_s",
    "bonus_green_unprotected_case_s",
    "build_infrequent_phase_table",
    "compute_bonus_green",
    "convert_mean_maximum_queues",
    "cycle_time_to_model_s",
    "degree_of_saturation_pct",
    "demand_frequency",
    "mean_maximum_queue_m",
    "mean_maximum_queue_pcu",
    "practical_reserve_capacity_pct",
    "total_phase_time_s",
    "validate_degree_of_saturation",
    "weighted_average_green_s",
    "weighted_average_phase_time_s",
]

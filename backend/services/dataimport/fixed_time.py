"""SCATS→fixed-time conversion (roadmap 1.6 / TK requirement R1.3b).

**What this module is.** Adaptive-controller history is not a signal plan: it
is a record of what a demand-responsive controller happened to do. Micro
simulation needs a *fixed-time* plan. MRWA Appendix A §A.2/§A.3 specifies the
conversion, and this module is that specification in code — every formula
below cites the exact bullet of
``docs/product/reference/mrwa-appendix-a-extract.md`` it implements, in the
same self-citing style as ``services/agent/mrwa_reference.py`` (Phase 0.3).

**Input and output are both the neutral model** (``schema.py``), never a file:
an ``OBSERVED_HISTORY`` :class:`SignalProgramData` in, a ``DERIVED``
:class:`SignalProgramData` out, with a :class:`FixedTimePlan` wrapper carrying
the full arithmetic so a report, an approval card or a reviewer can re-derive
every number by hand. That makes the module region-agnostic by construction —
exactly like ``quality.py``, it never sees a SCATS file, so a Daejeon adaptive
history that lands in the neutral model converts through the same code. (The
name is ``fixed_time``, not ``scats_fixed_time``, for that reason.)

**It refuses rather than guesses.** Vissim will run a plan derived from
self-contradictory history without a murmur; so would a spreadsheet. Every
contradiction that would make a computed number wrong is a ``REFUSE``
:class:`ImportIssue` and produces **no plan at all** — see
:data:`REFUSE_CODES`. Everything coherent-but-thin is a ``WARN``; everything
that is merely a fact about the derivation is an ``INFO``. Same tiers, same
doctrine, as the import layer this module sits in.

Nothing here imports Vissim, and nothing here is wired into a router: the
consumer is the genesis-(b) signals milestone (roadmap 3.1), which is the unit
that gets to decide how a plan lands in a real network.

Deliberately NOT implemented, and why
-------------------------------------
* **Degree of saturation, bonus green, LinSig Tables 3-3/3-4.** R1.3b names
  these, but they live in the Operational Modelling Guidelines' *LinSig
  chapter*, which is not among the extracts on this machine. The Appendix A
  extract — this unit's stated formula source — contains no DoS or bonus-green
  formula, so none is written here. Inventing one would be exactly the
  0.3-unit failure mode (a plausible-looking number nobody can trace).
* **Yellow-trap refusal** (A.4, p.41-42). The extract's own 1.6 note asks for
  it, but it is *not computable from the neutral model*: the rule turns on an
  opposed **filtering** right turn, and ``schema.TurnType``'s own docstring
  states the enum is geometric and carries no opposed/permissive semantics.
  The converter says so with an INFO
  (``fixed_time_yellow_trap_not_checkable``) on every run rather than
  implying it checked. See :func:`_yellow_trap_notice`.
* **Signal-group green windows** (the Vissim-native ``sgStart``/``sgEnd``
  view). Appendix A gives no rule for laying signal groups out inside the
  derived phases, and the layout depends on the target network, so every line
  of it would be assumption. The plan instead publishes each phase's start
  offset (:attr:`PhaseDerivation.start_offset_s`) — which is what a converter
  needs — and leaves the layout to roadmap 3.1.
* **LMA vs Phase Sequence Chart agreement** (extract's 1.6 note). Both are
  drawings; neither is machine-readable, and the adapter says so already.

ASSUMPTIONS — places Appendix A is silent or self-inconsistent
--------------------------------------------------------------
Each is marked ``ASSUMPTION`` at its own definition too; this is the index.

A1. **Round-up wins over Σt̄ᵢ = c̄.** A.2 states the identity Σt̄ᵢ = c̄, and the
    extract's 1.6 note states "SCATS fractions always round up". After rounding
    every phase up, the two cannot both hold. This module makes the *rounded
    phase times* authoritative and defines the plan's cycle as their sum, then
    reports the residual (``fixed_time_round_up_residual``, always emitted).
    Conservative because the alternative — keeping c̄ and shaving a phase —
    silently takes green off a movement.
A2. **Round-up precision for phase times is 1 s.** The extract fixes 0.5 s for
    yellow/all-red and 1 s for ped clearance but states no precision for phase
    times. Whole seconds is SCATS' and Vissim's own working unit.
    Overridable: :attr:`ConversionSettings.phase_time_round_up_step_s`.
A3. **Green is the residual, never re-rounded.** ``green = rounded_phase_time −
    intergreen`` exactly, so a phase's parts always re-add to its stated
    duration. Rounding green up as well would break that invariant.
A4. **The plan's cycle starts at the stretch phase.** A.2 makes the stretch
    phase the cycle boundary, so the derived phase order is rotated to put it
    at offset 0.
A5. **A phase that never ran is dropped, loudly.** A.2's own t̄ᵢ = avg × fᵢ
    gives 0 s at fᵢ = 0. A 0-second phase is not a runnable plan element, so it
    is left out and named in ``fixed_time_phase_never_ran``.
A6. **Early cut-off is not added when intergreen is composed.** A.3 defines
    intergreen = early cut-off + yellow + all-red, but early cut-off is a
    per-signal-group, site-justified value, and a phase can serve several
    groups. When only yellow and all-red are known the composed intergreen
    omits it and says so (``fixed_time_intergreen_composed``).
A7. **Same-yellow-on-a-grade takes the longer of the two approaches.** A.3
    says a graded site uses the same yellow both ways but not which; the
    downgrade value is the longer and the safer.
A8. **Table A-3 is not extrapolated.** Its five posted speeds reproduce
    exactly as ``floor(V/3.6)`` (proven in the tests), but that is an
    observation about how the table was built, not a licence to invent rows.
    Off-table speeds fall back to A.3's own published formula and say so.
A9. **Protection-time rounding is up to 1 s**, matching the ped-clearance rule
    it shares a walking-speed basis with. A.3 states no rounding for
    protection times.
A10. **Offsets outside the published cycle range are clamped**, not
    extrapolated. A.5 authorises linear interpolation *between* offset values.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Sequence

from pydantic import BaseModel, ConfigDict, Field

from .base import ImportIssue, IssueSeverity
from .schema import (
    PhaseData,
    ProgramSourceKind,
    Provenance,
    SignalControllerData,
    SignalProgramData,
)

# ---------------------------------------------------------------------------
# Citations
#
# Every string below names the section AND the bullet/page inside
# docs/product/reference/mrwa-appendix-a-extract.md that the formula comes
# from, so a reviewer can re-check a number against the source in one hop. A
# formula with no traceable line in that extract does not get written (see the
# module docstring's "Deliberately NOT implemented").
# ---------------------------------------------------------------------------

EXTRACT = "docs/product/reference/mrwa-appendix-a-extract.md"

CIT_A2_STRETCH = (
    f"MRWA Appendix A §A.2 bullet 1 ({EXTRACT}) — 'Stretch phase (runs every cycle, "
    "usually A) defines cycle boundaries. Calculation period: first stretch-phase start "
    "in window → first stretch-phase start after window.'"
)
CIT_A2_AVG_CYCLE = (
    f"MRWA Appendix A §A.2 bullet 2 ({EXTRACT}) — 'Avg cycle c̄ = total duration ÷ "
    "n_cycles (n = stretch-phase starts).'"
)
CIT_A2_AVG_PHASE = (
    f"MRWA Appendix A §A.2 bullet 3 ({EXTRACT}) — 'Avg phase i t̄ᵢ = total activation "
    "time of i (green + its intergreen) ÷ n_cycles; Σ t̄ᵢ = c̄. Non-every-cycle phases: "
    "t̄ᵢ = SCATS avg phase time × frequency fᵢ (fᵢ = occurrences/cycles; stretch = 1.0).'"
)
CIT_A2_FIG_A5 = (
    f"MRWA Appendix A §A.2 bullet 4, Fig A-5 p.11 ({EXTRACT}) — 'Coordinated sites with "
    "low-frequency phases: recount cycles at the coordinated partner.'"
)
CIT_A2_PED_DEMAND = (
    f"MRWA Appendix A §A.2 bullet 5, p.11 ({EXTRACT}) — 'Ped demand frequency = count of "
    "Demand=Off Active=On events ÷ complete cycles.'"
)
CIT_A3_YELLOW = (
    f"MRWA Appendix A §A.3 'Yellow' (Austroads AGTM09-16), Table A-2 p.15 ({EXTRACT}) — "
    "'t_y = t_r + 0.5(V/3.6)/(a_d + 9.8G), min 3.0 s, round UP to 0.5 s; WA uses t_r=1.0, "
    "a_d=3.0; same yellow both approaches on a grade.'"
)
CIT_A3_ALL_RED = (
    f"MRWA Appendix A §A.3 'All-red', Tables A-3/A-4 p.17-18 ({EXTRACT}) — 't_ar = "
    "3.6·L_c/V, min 1.0 s, round UP to 0.5 s; WA method = clearance distance ÷ "
    "posted-speed m/s (Table A-3: 40→11, 50→13, 60→16, 70→19, 80→22 m/s).'"
)
CIT_A3_INTERGREEN = (
    f"MRWA Appendix A §A.3 'Intergreen' ({EXTRACT}) — 'Intergreen = early cut-off + "
    "yellow + all-red. Early cut-off is application-specific ... site-justified.'"
)
CIT_A3_PEDESTRIAN = (
    f"MRWA Appendix A §A.3 'Pedestrian', Table A-5 ({EXTRACT}) — 'walk = 6 s WA standard; "
    "clearance = crossing length A ÷ 1.2 m/s rounded up (Table A-5, 4–45 m → 4–38 s); "
    "Clearance-2 = intergreen − 1 s (WA rule).'"
)
CIT_A3_PROTECTION = (
    f"MRWA Appendix A §A.3 'Protection types (6)' ({EXTRACT}) — distances from the "
    "pushbutton pole (A: full crossing, B: to median or 55%A, C: to exit-lane middle); "
    "'timed red arrow (C÷1.2) / red arrow max(B,55%A)÷1.2 + flash'."
)
CIT_A4_YELLOW_TRAP = (
    f"MRWA Appendix A §A.4 p.41-42 ({EXTRACT}) — 'yellow-trap avoidance rule: an opposed "
    "filtering right turn must never overlap the next phase's intergreen'."
)
CIT_A5_OFFSETS = (
    f"MRWA Appendix A §A.5 ({EXTRACT}) — 'Offset derivation: identify active plan from "
    "Strategic Monitor → thresholds from TrafficMap → linear interpolation (default) or "
    "hysteresis between offset values by average cycle length. Worked example TCS 359 "
    "(p.44-49).'"
)
CIT_A5_CYCLE_BOUNDS = (
    f"MRWA Appendix A §A.5 / §A.1 Link & Offset Plans tab ({EXTRACT}) — the tab states "
    "'subsystem, coordinated phase, cycle bounds'."
)
CIT_IMPL_NOTES = (
    f"MRWA Appendix A extract §'Module implementation notes (1.6)' ({EXTRACT}) — 'Count "
    "only complete stretch-to-stretch cycles; frequency-adjust non-every-cycle phases; "
    "round yellow/all-red UP to 0.5 s, ped clearance UP to 1 s; SCATS fractions always "
    "round up (main doc rule); use posted speed not design speed for all-red (WA "
    "practice); validate LMA vs Phase Sequence Chart agreement; refuse yellow-trap "
    "sequences.'"
)

# ---------------------------------------------------------------------------
# Published constants (A.3)
# ---------------------------------------------------------------------------

#: WA's own driver reaction time in A.3's yellow formula. A.3: "WA uses
#: t_r=1.0, a_d=3.0".
WA_REACTION_TIME_S = 1.0
#: WA's own deceleration in A.3's yellow formula (m/s²). Same bullet.
WA_DECELERATION_MS2 = 3.0
#: Gravity as A.3 writes it in the yellow formula's grade term (9.8G).
GRAVITY_MS2 = 9.8
#: A.3: yellow is never below 3.0 s.
YELLOW_MINIMUM_S = 3.0
#: A.3: all-red is never below 1.0 s.
ALL_RED_MINIMUM_S = 1.0
#: A.3 / extract 1.6 note: yellow and all-red round UP to 0.5 s.
CLEARANCE_ROUND_UP_STEP_S = 0.5
#: A.3: WA standard walk time.
WA_STANDARD_WALK_S = 6.0
#: A.3: pedestrian walking speed used for clearance and arrow protection.
PEDESTRIAN_WALKING_SPEED_MS = 1.2
#: A.3 / extract 1.6 note: pedestrian clearance rounds UP to 1 s.
PEDESTRIAN_ROUND_UP_STEP_S = 1.0
#: A.3: the "55%A" term in the red-arrow-with-flash protection distance.
PROTECTION_FULL_CROSSING_FRACTION = 0.55

#: A.3 Table A-3 — the posted-speed-to-m/s figures WA's own all-red method
#: divides by. Encoded EXACTLY as the extract lists them (40→11, 50→13, 60→16,
#: 70→19, 80→22) and never interpolated: see ASSUMPTION A8.
TABLE_A3_CLEARANCE_SPEED_MS: dict[float, float] = {
    40.0: 11.0,
    50.0: 13.0,
    60.0: 16.0,
    70.0: 19.0,
    80.0: 22.0,
}

#: A.3 Table A-5's two stated endpoints (crossing length m → clearance s), kept
#: so the tests can prove the formula reproduces the published table rather
#: than merely asserting the formula against itself.
TABLE_A5_ENDPOINTS_M_TO_S: tuple[tuple[float, float], ...] = ((4.0, 4.0), (45.0, 38.0))


# ---------------------------------------------------------------------------
# Rounding helpers
#
# "Round UP" here is a true ceiling, not Python's banker's rounding, because
# every rounding rule Appendix A states is directional (yellow, all-red, ped
# clearance, and the main doc's blanket "SCATS fractions always round up").
# The epsilon exists only to stop binary float noise from pushing an exact
# multiple onto the next step (3.0 must round to 3.0, not 3.5).
# ---------------------------------------------------------------------------

_ROUNDING_EPSILON = 1e-9

#: Seconds below which two computed times are treated as the same number.
#: Used only for float-noise suppression, never to widen a real tolerance —
#: every substantive tolerance in this module is a named
#: :class:`ConversionSettings` field the caller can see and change.
_FLOAT_EQUALITY_EPSILON_S = 1e-9


def round_up_to_step(value: float, step: float) -> float:
    """Smallest multiple of ``step`` that is >= ``value``. Directional by
    design — see this section's comment. Raises on a non-positive or
    non-finite step (a programming error, not a data problem)."""
    if not math.isfinite(step) or step <= 0:
        raise ValueError(f"round_up_to_step: step must be finite and positive, got {step!r}")
    if not math.isfinite(value):
        raise ValueError(f"round_up_to_step: value must be finite, got {value!r}")
    return math.ceil(value / step - _ROUNDING_EPSILON) * step


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


# ---------------------------------------------------------------------------
# A.3 time settings — pure formulas
#
# These raise ValueError on nonsense inputs rather than returning a number: a
# negative crossing length or a zero speed is a caller bug, and the house rule
# is that a refusal with a reason beats a computed wrong number. Data-level
# problems (a source that simply did not state a speed) are the converter's
# job and surface as ImportIssues instead.
# ---------------------------------------------------------------------------


def yellow_time_s(
    speed_kmh: float,
    grade: float = 0.0,
    *,
    reaction_time_s: float = WA_REACTION_TIME_S,
    deceleration_ms2: float = WA_DECELERATION_MS2,
) -> float:
    """A.3: ``t_y = t_r + 0.5(V/3.6)/(a_d + 9.8G)``, floored at 3.0 s and
    rounded UP to 0.5 s. Citation: :data:`CIT_A3_YELLOW`.

    ``grade`` is a decimal fraction, **positive uphill** — the sign convention
    the formula itself forces: an uphill grade enlarges the denominator and so
    shortens the yellow, which is the standard Austroads/ITE reading of the
    same expression. A grade steep enough to drive ``a_d + 9.8G`` to zero or
    below is refused rather than producing an infinite or negative yellow.

    Note on ordering: the 3.0 s minimum and the 0.5 s round-up commute here
    because 3.0 is itself a multiple of 0.5, so this implementation's
    round-then-floor order cannot differ from the reverse.
    """
    speed_kmh = _require_positive("speed_kmh", speed_kmh)
    reaction_time_s = _require_non_negative("reaction_time_s", reaction_time_s)
    deceleration_ms2 = _require_positive("deceleration_ms2", deceleration_ms2)
    if not math.isfinite(grade):
        raise ValueError(f"grade must be finite, got {grade!r}")
    denominator = deceleration_ms2 + GRAVITY_MS2 * float(grade)
    if denominator <= 0:
        raise ValueError(
            "yellow_time_s: a_d + 9.8G must be > 0 (a downgrade steeper than "
            f"{deceleration_ms2 / GRAVITY_MS2:.3f} makes A.3's formula meaningless); "
            f"got a_d={deceleration_ms2}, G={grade}"
        )
    raw = reaction_time_s + 0.5 * (speed_kmh / 3.6) / denominator
    return max(YELLOW_MINIMUM_S, round_up_to_step(raw, CLEARANCE_ROUND_UP_STEP_S))


def yellow_time_both_approaches_s(
    speed_kmh: float,
    grade_magnitude: float,
    *,
    reaction_time_s: float = WA_REACTION_TIME_S,
    deceleration_ms2: float = WA_DECELERATION_MS2,
) -> float:
    """A.3's "same yellow both approaches on a grade", resolved.

    **ASSUMPTION A7.** A.3 says a graded site uses one yellow for both
    approaches but never says which of the two computed values. This returns
    the LONGER — the downgrade one — because a yellow that is too short is the
    failure with a crash in it.
    """
    magnitude = abs(_require_non_negative("grade_magnitude", grade_magnitude))
    uphill = yellow_time_s(
        speed_kmh,
        magnitude,
        reaction_time_s=reaction_time_s,
        deceleration_ms2=deceleration_ms2,
    )
    downhill = yellow_time_s(
        speed_kmh,
        -magnitude,
        reaction_time_s=reaction_time_s,
        deceleration_ms2=deceleration_ms2,
    )
    return max(uphill, downhill)


def all_red_time_formula_s(clearance_distance_m: float, speed_kmh: float) -> float:
    """A.3's published general form: ``t_ar = 3.6·L_c/V``, floored at 1.0 s,
    rounded UP to 0.5 s. Citation: :data:`CIT_A3_ALL_RED`."""
    clearance_distance_m = _require_non_negative("clearance_distance_m", clearance_distance_m)
    speed_kmh = _require_positive("speed_kmh", speed_kmh)
    raw = 3.6 * clearance_distance_m / speed_kmh
    return max(ALL_RED_MINIMUM_S, round_up_to_step(raw, CLEARANCE_ROUND_UP_STEP_S))


def all_red_time_wa_s(clearance_distance_m: float, posted_speed_kmh: float) -> float | None:
    """A.3's WA method: clearance distance ÷ the Table A-3 m/s figure for the
    **posted** speed (the extract's 1.6 note: "use posted speed not design
    speed"), floored at 1.0 s, rounded UP to 0.5 s.

    Returns ``None`` when Table A-3 has no row for that speed — see ASSUMPTION
    A8; the table is published data, not a curve to extrapolate.
    Citation: :data:`CIT_A3_ALL_RED`.
    """
    clearance_distance_m = _require_non_negative("clearance_distance_m", clearance_distance_m)
    posted_speed_kmh = _require_positive("posted_speed_kmh", posted_speed_kmh)
    speed_ms = TABLE_A3_CLEARANCE_SPEED_MS.get(float(posted_speed_kmh))
    if speed_ms is None:
        return None
    raw = clearance_distance_m / speed_ms
    return max(ALL_RED_MINIMUM_S, round_up_to_step(raw, CLEARANCE_ROUND_UP_STEP_S))


def all_red_time_s(clearance_distance_m: float, posted_speed_kmh: float) -> float:
    """WA's Table A-3 method when the posted speed is a published row,
    otherwise A.3's general formula. Both are A.3's own; neither is invented.

    The two genuinely differ — Table A-3 is the more conservative, because its
    m/s figures are ``floor(V/3.6)`` — so a caller that must know which method
    produced its number should call :func:`all_red_time_wa_s` first and treat
    ``None`` as "off-table, formula fallback". This convenience wrapper cannot
    say which it used, and does not pretend to.
    """
    wa = all_red_time_wa_s(clearance_distance_m, posted_speed_kmh)
    if wa is not None:
        return wa
    return all_red_time_formula_s(clearance_distance_m, posted_speed_kmh)


def intergreen_time_s(early_cut_off_s: float, yellow_s: float, all_red_s: float) -> float:
    """A.3: ``intergreen = early cut-off + yellow + all-red``.
    Citation: :data:`CIT_A3_INTERGREEN`. No rounding is applied — each
    component carries its own rule and this is their sum."""
    return (
        _require_non_negative("early_cut_off_s", early_cut_off_s)
        + _require_non_negative("yellow_s", yellow_s)
        + _require_non_negative("all_red_s", all_red_s)
    )


def pedestrian_clearance_1_s(
    crossing_length_m: float, *, walking_speed_ms: float = PEDESTRIAN_WALKING_SPEED_MS
) -> float:
    """A.3: crossing length A ÷ 1.2 m/s, rounded UP to 1 s (Table A-5).
    Citation: :data:`CIT_A3_PEDESTRIAN`."""
    crossing_length_m = _require_positive("crossing_length_m", crossing_length_m)
    walking_speed_ms = _require_positive("walking_speed_ms", walking_speed_ms)
    return round_up_to_step(crossing_length_m / walking_speed_ms, PEDESTRIAN_ROUND_UP_STEP_S)


def pedestrian_clearance_2_s(intergreen_s: float) -> float:
    """A.3's WA rule: ``Clearance-2 = intergreen − 1 s``.
    Citation: :data:`CIT_A3_PEDESTRIAN`.

    Refuses an intergreen below 1 s rather than returning a negative or zero
    clearance — a clearance interval that does not exist is not a number this
    module will hand to a signal plan.
    """
    intergreen_s = _require_non_negative("intergreen_s", intergreen_s)
    if intergreen_s <= 1.0:
        raise ValueError(
            "pedestrian_clearance_2_s: A.3's WA rule (intergreen − 1 s) needs an "
            f"intergreen above 1.0 s; got {intergreen_s}"
        )
    return intergreen_s - 1.0


def protection_time_timed_red_arrow_s(
    distance_c_m: float, *, walking_speed_ms: float = PEDESTRIAN_WALKING_SPEED_MS
) -> float:
    """A.3 protection type 4 (timed red arrow): ``C ÷ 1.2``, C measured from
    the pushbutton pole to the middle of the exit lane.
    Citation: :data:`CIT_A3_PROTECTION`. **ASSUMPTION A9**: rounded UP to 1 s,
    matching the ped-clearance rule it shares a walking-speed basis with; A.3
    states no rounding for protection times."""
    distance_c_m = _require_positive("distance_c_m", distance_c_m)
    walking_speed_ms = _require_positive("walking_speed_ms", walking_speed_ms)
    return round_up_to_step(distance_c_m / walking_speed_ms, PEDESTRIAN_ROUND_UP_STEP_S)


def protection_time_red_arrow_with_flash_s(
    distance_a_m: float,
    distance_b_m: float,
    *,
    walking_speed_ms: float = PEDESTRIAN_WALKING_SPEED_MS,
) -> float:
    """A.3 protection type 5 (red arrow + flash): ``max(B, 55%·A) ÷ 1.2``,
    where A is the full crossing and B reaches the median.
    Citation: :data:`CIT_A3_PROTECTION`. Same ASSUMPTION A9 rounding."""
    distance_a_m = _require_positive("distance_a_m", distance_a_m)
    distance_b_m = _require_positive("distance_b_m", distance_b_m)
    walking_speed_ms = _require_positive("walking_speed_ms", walking_speed_ms)
    governing = max(distance_b_m, PROTECTION_FULL_CROSSING_FRACTION * distance_a_m)
    return round_up_to_step(governing / walking_speed_ms, PEDESTRIAN_ROUND_UP_STEP_S)


def pedestrian_demand_frequency(demands_met: int, complete_cycles: int) -> float:
    """A.2 p.11: ``count(Walk j:Demand=Off Active=On) ÷ complete cycles``.
    Citation: :data:`CIT_A2_PED_DEMAND`.

    Uncapped on purpose — the neutral schema's own note explains that a walk
    served twice in a cycle is representable, not a validation error.
    """
    if not isinstance(demands_met, int) or isinstance(demands_met, bool) or demands_met < 0:
        raise ValueError(f"demands_met must be a non-negative int, got {demands_met!r}")
    if (
        not isinstance(complete_cycles, int)
        or isinstance(complete_cycles, bool)
        or complete_cycles <= 0
    ):
        raise ValueError(
            "pedestrian_demand_frequency: A.2 p.11 divides by complete cycles, so a "
            f"positive cycle count is required; got {complete_cycles!r}"
        )
    return demands_met / complete_cycles


def interpolate_offset_s(
    average_cycle_s: float, points: Sequence[tuple[float, float]]
) -> float:
    """A.5's default offset derivation: linear interpolation between published
    (cycle length, offset) values by average cycle length.
    Citation: :data:`CIT_A5_OFFSETS`.

    **ASSUMPTION A10**: an average cycle outside the published range is
    CLAMPED to the nearest published offset. A.5 authorises interpolation
    *between* offset values; extrapolating past the ends would invent a plan
    Main Roads never published. (A.5 also offers hysteresis as an alternative
    to interpolation; it needs a previous-state input this function is not
    given, so only the stated default is implemented.)
    """
    average_cycle_s = _require_positive("average_cycle_s", average_cycle_s)
    if len(points) < 2:
        raise ValueError(
            "interpolate_offset_s: A.5 interpolates BETWEEN offset values; at least two "
            f"(cycle, offset) points are required, got {len(points)}"
        )
    cleaned: list[tuple[float, float]] = []
    for cycle, offset in points:
        cleaned.append(
            (_require_positive("point cycle", cycle), _require_non_negative("point offset", offset))
        )
    cleaned.sort()
    cycles = [cycle for cycle, _ in cleaned]
    if len(set(cycles)) != len(cycles):
        raise ValueError(
            f"interpolate_offset_s: duplicate cycle lengths in the offset table {cycles}; "
            "the module will not choose between two offsets for one cycle"
        )
    if average_cycle_s <= cleaned[0][0]:
        return cleaned[0][1]
    if average_cycle_s >= cleaned[-1][0]:
        return cleaned[-1][1]
    for (low_cycle, low_offset), (high_cycle, high_offset) in zip(cleaned, cleaned[1:]):
        if low_cycle <= average_cycle_s <= high_cycle:
            span = high_cycle - low_cycle
            ratio = (average_cycle_s - low_cycle) / span
            return low_offset + ratio * (high_offset - low_offset)
    raise AssertionError("unreachable: average cycle lies inside the sorted table")


# ---------------------------------------------------------------------------
# A.2 conversion — settings, result types
# ---------------------------------------------------------------------------


class ConversionSettings(BaseModel):
    """Every number the conversion judges by, in one place and overridable —
    the same shape as ``quality.QualityThresholds``, and the cycle tolerances
    are deliberately IDENTICAL to that gate's so a plan this module emits
    cannot fail the gate it will later be checked by."""

    model_config = ConfigDict(extra="forbid")

    phase_time_round_up_step_s: float = Field(
        default=1.0,
        gt=0,
        description="ASSUMPTION A2: whole seconds. Appendix A states no phase-time precision.",
    )
    cycle_mismatch_tolerance_s: float = Field(
        default=2.0,
        ge=0,
        description="Stated vs recomputed c̄ may differ by this much (matches quality.py)…",
    )
    cycle_mismatch_tolerance_fraction: float = Field(
        default=0.05, ge=0, description="…or this share of the cycle, whichever is larger."
    )
    frequency_tolerance: float = Field(
        default=1e-3,
        ge=0,
        description="A source-stated fᵢ may differ from occurrences÷cycles by this much.",
    )


@dataclass(frozen=True)
class PhaseDerivation:
    """One row of the conversion's worked arithmetic — the audit trail A.2
    implies and R4.0's "parameter change record + rationale" section needs.

    Every field is a number a reviewer can re-derive by hand from the two
    columns ``occurrences`` and ``observed_total_s`` plus the cycle count::

        scats_average_s     = observed_total_s / occurrences
        frequency           = occurrences / complete_cycles
        average_phase_s     = observed_total_s / complete_cycles
                            = scats_average_s * frequency      (A.2's two routes)
        rounded_phase_s     = round-up(average_phase_s)        (ASSUMPTION A1/A2)
        green_s             = rounded_phase_s - intergreen_s   (ASSUMPTION A3)
    """

    phase_id: str
    occurrences: int | None
    observed_total_s: float
    scats_average_s: float | None
    frequency: float | None
    average_phase_s: float
    rounded_phase_s: float | None
    start_offset_s: float | None
    intergreen_s: float | None
    green_s: float | None
    is_stretch_phase: bool
    dropped: bool = False
    drop_reason: str | None = None


@dataclass(frozen=True)
class FixedTimePlan:
    """The conversion's whole output: the derived program, the arithmetic that
    produced it, and every finding.

    ``program`` is ``None`` exactly when a ``REFUSE`` issue is present — the
    module produces no plan at all from contradictory history rather than a
    plan with a warning stapled to it.
    """

    controller_id: str
    source_program_id: str | None
    program: SignalProgramData | None
    complete_cycles: int | None
    stretch_phase_id: str | None
    average_cycle_s: float | None
    plan_cycle_s: float | None
    round_up_residual_s: float | None
    rows: tuple[PhaseDerivation, ...]
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


#: Every code this module can emit at REFUSE tier. Kept as data so the
#: docstring's "these produce no plan at all" claim is testable rather than
#: aspirational.
REFUSE_CODES: frozenset[str] = frozenset(
    {
        "fixed_time_no_observed_program",
        "fixed_time_multiple_observed_programs",
        "fixed_time_source_kind_not_observed",
        "fixed_time_no_phases",
        "fixed_time_cycle_count_unusable",
        "fixed_time_stretch_phase_unidentified",
        "fixed_time_stretch_phase_ambiguous",
        "fixed_time_stretch_occurrence_mismatch",
        "fixed_time_phase_total_missing",
        "fixed_time_phase_total_invalid",
        "fixed_time_frequency_contradicts_counts",
        "fixed_time_stated_cycle_contradicts_history",
        "fixed_time_intergreen_exceeds_phase",
        "fixed_time_all_phases_dropped",
    }
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
    return ImportIssue(
        severity=severity,
        code=code,
        message=message,
        subject=subject,
        citation=citation,
        context=context or {},
    )


def _yellow_trap_notice(subject: str) -> ImportIssue:
    """Always emitted. The extract's 1.6 note asks 1.6 to "refuse yellow-trap
    sequences"; the neutral model cannot express the opposed-filtering-turn
    property the rule turns on, so this module states plainly that it did NOT
    check rather than letting silence read as a pass."""
    return _issue(
        IssueSeverity.INFO,
        "fixed_time_yellow_trap_not_checkable",
        "Yellow-trap avoidance was NOT checked. A.4's rule turns on an opposed FILTERING "
        "right turn, and the neutral model carries no opposed/permissive semantics "
        "(schema.TurnType is geometric by design), so the property is not computable from "
        "this input. It must be checked against the Phase Sequence Chart before the plan "
        "is coded.",
        subject=subject,
        citation=CIT_A4_YELLOW_TRAP,
    )


def _resolve_program(
    controller: SignalControllerData,
    program: SignalProgramData | None,
    subject: str,
) -> tuple[SignalProgramData | None, list[ImportIssue]]:
    if program is not None:
        if program.source_kind is not ProgramSourceKind.OBSERVED_HISTORY:
            return None, [
                _issue(
                    IssueSeverity.REFUSE,
                    "fixed_time_source_kind_not_observed",
                    f"Program {program.program_id} is {program.source_kind.value!r}, not "
                    "'observed_history'. A.2 averages OBSERVATIONS of an adaptive controller; "
                    "converting a declared plan would relabel a fact as an estimate, and "
                    "converting a derived one would average an average.",
                    subject=subject,
                    citation=CIT_A2_AVG_PHASE,
                    context={"source_kind": program.source_kind.value},
                )
            ]
        return program, []

    observed = [
        candidate
        for candidate in controller.programs
        if candidate.source_kind is ProgramSourceKind.OBSERVED_HISTORY
    ]
    if not observed:
        return None, [
            _issue(
                IssueSeverity.REFUSE,
                "fixed_time_no_observed_program",
                f"Controller {controller.controller_id} carries no observed-history program, so "
                "there is nothing for A.2 to average. Supply one explicitly if the intent was a "
                "different program.",
                subject=subject,
                citation=CIT_A2_AVG_PHASE,
                context={"program_ids": [p.program_id for p in controller.programs]},
            )
        ]
    if len(observed) > 1:
        return None, [
            _issue(
                IssueSeverity.REFUSE,
                "fixed_time_multiple_observed_programs",
                f"Controller {controller.controller_id} carries {len(observed)} observed-history "
                "programs; the module will not choose which window to convert. Pass one "
                "explicitly.",
                subject=subject,
                citation=CIT_A2_AVG_PHASE,
                context={"program_ids": [p.program_id for p in observed]},
            )
        ]
    return observed[0], []


def _ordered_phases(program: SignalProgramData, stretch_id: str) -> tuple[list[PhaseData], bool]:
    """A.2/ASSUMPTION A4: cycle order with the stretch phase at offset 0.

    Returns ``(phases, used_sequence_index)``. ``sequence_index`` is honoured
    when every phase states one and they are distinct; otherwise the source's
    own list order is kept (and the caller reports that it was).
    """
    indices = [phase.sequence_index for phase in program.phases]
    if all(index is not None for index in indices) and len(set(indices)) == len(indices):
        ordered = sorted(program.phases, key=lambda p: p.sequence_index or 0)
        used_index = True
    else:
        ordered = list(program.phases)
        used_index = False
    position = next(
        (i for i, phase in enumerate(ordered) if phase.phase_id == stretch_id), 0
    )
    return ordered[position:] + ordered[:position], used_index


def convert_to_fixed_time(
    controller: SignalControllerData,
    program: SignalProgramData | None = None,
    *,
    settings: ConversionSettings | None = None,
) -> FixedTimePlan:
    """Convert one observed adaptive history into a fixed-time plan (A.2).

    ``program`` defaults to the controller's single ``OBSERVED_HISTORY``
    program; zero or several is a refusal, not a guess.

    Returns a :class:`FixedTimePlan` **always** — the import layer's own
    contract ("report, do not raise"). ``plan.ok`` is false and
    ``plan.program`` is ``None`` when any check refused.
    """
    settings = settings or ConversionSettings()
    subject = f"controller {controller.controller_id}"
    issues: list[ImportIssue] = []

    resolved, resolve_issues = _resolve_program(controller, program, subject)
    issues.extend(resolve_issues)
    if resolved is None:
        return FixedTimePlan(
            controller_id=controller.controller_id,
            source_program_id=program.program_id if program is not None else None,
            program=None,
            complete_cycles=None,
            stretch_phase_id=None,
            average_cycle_s=None,
            plan_cycle_s=None,
            round_up_residual_s=None,
            rows=(),
            issues=tuple(issues),
        )

    subject = f"{subject} / program {resolved.program_id}"

    def _bail() -> FixedTimePlan:
        return FixedTimePlan(
            controller_id=controller.controller_id,
            source_program_id=resolved.program_id,
            program=None,
            complete_cycles=resolved.observed_cycle_count,
            stretch_phase_id=None,
            average_cycle_s=None,
            plan_cycle_s=None,
            round_up_residual_s=None,
            rows=(),
            issues=tuple(issues),
        )

    # -- 1. the inputs A.2 divides by ------------------------------------
    if not resolved.phases:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "fixed_time_no_phases",
                f"Program {resolved.program_id} carries no phase records, so no phase time can "
                "be averaged.",
                subject=subject,
                citation=CIT_A2_AVG_PHASE,
            )
        )
        return _bail()

    cycles = resolved.observed_cycle_count
    if cycles is None or cycles <= 0:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "fixed_time_cycle_count_unusable",
                f"Program {resolved.program_id} states a complete-cycle count of {cycles!r}. A.2 "
                "divides every phase time by it, and the extract's own 1.6 note requires COMPLETE "
                "stretch-to-stretch cycles only, so a missing or non-positive count cannot be "
                "worked around.",
                subject=subject,
                citation=CIT_IMPL_NOTES,
                context={"observed_cycle_count": cycles},
            )
        )
        return _bail()

    # -- 2. the stretch phase: A.2's cycle boundary -----------------------
    stretch_phases = [phase for phase in resolved.phases if phase.is_stretch_phase]
    if len(stretch_phases) > 1:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "fixed_time_stretch_phase_ambiguous",
                f"Program {resolved.program_id} flags {len(stretch_phases)} phases as the stretch "
                f"phase ({[p.phase_id for p in stretch_phases]}). A.2 has exactly one — it is what "
                "defines the cycle boundary.",
                subject=subject,
                citation=CIT_A2_STRETCH,
                context={"flagged": [p.phase_id for p in stretch_phases]},
            )
        )
        return _bail()
    if not stretch_phases:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "fixed_time_stretch_phase_unidentified",
                f"Program {resolved.program_id} flags no stretch phase. Without it the "
                "complete-cycle count cannot be verified against the phase occurrences, so the "
                "cycle A.2 divides by is unattributable. (The SCATS adapter leaves the flag unset "
                "when two phases tie — that ambiguity has to be resolved upstream, not here.)",
                subject=subject,
                citation=CIT_A2_STRETCH,
            )
        )
        return _bail()
    stretch = stretch_phases[0]

    if stretch.observation_count is None:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "fixed_time_stretch_occurrences_unverified",
                f"Stretch phase {stretch.phase_id} states no occurrence count, so the stated "
                f"complete-cycle count ({cycles}) could not be cross-checked against A.2's "
                "definition (n = stretch-phase starts). The conversion proceeded on the stated "
                "count.",
                subject=subject,
                citation=CIT_A2_AVG_CYCLE,
                context={"observed_cycle_count": cycles},
            )
        )
    elif stretch.observation_count != cycles:
        extra = stretch.observation_count - cycles
        hint = (
            " This looks like the closing stretch-phase start being counted as an activation: "
            "A.2's calculation period runs from the first stretch start in the window to the "
            "first start AFTER it, so that closing start bounds the period rather than sitting "
            "inside it."
            if extra == 1
            else ""
        )
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "fixed_time_stretch_occurrence_mismatch",
                f"Stretch phase {stretch.phase_id} ran {stretch.observation_count}× but the "
                f"program states {cycles} complete cycles. A.2 defines n as the stretch-phase "
                f"starts, so the two must agree.{hint}",
                subject=subject,
                citation=CIT_A2_STRETCH,
                context={
                    "stretch_occurrences": stretch.observation_count,
                    "observed_cycle_count": cycles,
                },
            )
        )
        return _bail()

    # -- 3. per-phase averages (A.2's two equivalent routes) --------------
    order, used_sequence_index = _ordered_phases(resolved, stretch.phase_id)
    if not used_sequence_index:
        issues.append(
            _issue(
                IssueSeverity.INFO,
                "fixed_time_phase_order_from_source_list",
                "Phase order was taken from the source's own record order: not every phase states "
                "a distinct sequence_index. The stretch phase was rotated to offset 0 either way "
                "(A.2 makes it the cycle boundary).",
                subject=subject,
                citation=CIT_A2_STRETCH,
            )
        )

    averages: list[tuple[PhaseData, float, float | None, float | None]] = []
    for phase in order:
        total = phase.observed_total_duration_s
        if total is None:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "fixed_time_phase_total_missing",
                    f"Phase {phase.phase_id} states no observed total activation time. A.2's t̄ᵢ "
                    "is that total ÷ cycles; a plan missing one phase's time is not the site's "
                    "plan.",
                    subject=subject,
                    citation=CIT_A2_AVG_PHASE,
                    context={"phase_id": phase.phase_id},
                )
            )
            return _bail()
        if not math.isfinite(total):
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "fixed_time_phase_total_invalid",
                    f"Phase {phase.phase_id} states a non-finite observed total ({total!r}).",
                    subject=subject,
                    citation=CIT_A2_AVG_PHASE,
                    context={"phase_id": phase.phase_id, "observed_total_duration_s": total},
                )
            )
            return _bail()

        # The stretch phase's own occurrences-vs-cycles agreement is already
        # settled above (fixed_time_stretch_occurrence_mismatch) — A.2 pins its
        # frequency at 1.0 by definition, so it is not re-checked here.
        occurrences = phase.observation_count
        frequency = (occurrences / cycles) if occurrences is not None else None
        if (
            frequency is not None
            and phase.frequency is not None
            and abs(phase.frequency - frequency) > settings.frequency_tolerance
        ):
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "fixed_time_frequency_contradicts_counts",
                    f"Phase {phase.phase_id} states frequency {phase.frequency:g} but its own "
                    f"counts give {occurrences}/{cycles} = {frequency:g}. A.2 defines fᵢ as "
                    "occurrences ÷ cycles; the source contradicts itself.",
                    subject=subject,
                    citation=CIT_A2_AVG_PHASE,
                    context={
                        "phase_id": phase.phase_id,
                        "stated_frequency": phase.frequency,
                        "counted_frequency": frequency,
                    },
                )
            )
            return _bail()

        scats_average = (total / occurrences) if occurrences else None
        averages.append((phase, total / cycles, frequency, scats_average))

    # -- 4. average cycle c̄, cross-checked against any stated value -------
    # A.2: Σ t̄ᵢ = c̄. Computing c̄ as the sum of the per-phase averages rather
    # than as (total duration ÷ n) makes that identity structural instead of
    # something the module hopes holds; the two are the same number.
    average_cycle = sum(item[1] for item in averages)
    if resolved.cycle_time_s is not None:
        difference = abs(resolved.cycle_time_s - average_cycle)
        tolerance = max(
            settings.cycle_mismatch_tolerance_s,
            average_cycle * settings.cycle_mismatch_tolerance_fraction,
        )
        if difference > tolerance:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "fixed_time_stated_cycle_contradicts_history",
                    f"Program {resolved.program_id} states a cycle of {resolved.cycle_time_s:g} s "
                    f"but its own phase totals give c̄ = {average_cycle:g} s (difference "
                    f"{difference:g} s, tolerance {tolerance:g} s). A.2 requires Σ t̄ᵢ = c̄.",
                    subject=subject,
                    citation=CIT_A2_AVG_CYCLE,
                    context={
                        "stated_cycle_s": resolved.cycle_time_s,
                        "recomputed_cycle_s": average_cycle,
                        "tolerance_s": tolerance,
                    },
                )
            )
            return _bail()
        # Not `> 0`: c̄ is a sum of quotients, so an exact agreement can still
        # land a few ulps apart and a warning about 3e-14 seconds is noise.
        if difference > _FLOAT_EQUALITY_EPSILON_S:
            issues.append(
                _issue(
                    IssueSeverity.WARN,
                    "fixed_time_stated_cycle_differs_slightly",
                    f"Program {resolved.program_id}'s stated cycle {resolved.cycle_time_s:g} s "
                    f"differs from the recomputed c̄ {average_cycle:g} s by {difference:g} s "
                    f"(inside the {tolerance:g} s tolerance). The recomputed value was used.",
                    subject=subject,
                    citation=CIT_A2_AVG_CYCLE,
                    context={
                        "stated_cycle_s": resolved.cycle_time_s,
                        "recomputed_cycle_s": average_cycle,
                    },
                )
            )

    # -- 5. round up, drop never-run phases, split green/intergreen -------
    kept: list[tuple[PhaseData, float, float, float | None]] = []
    dropped_ids: list[str] = []
    for phase, average, frequency, scats_average in averages:
        if average <= 0:
            dropped_ids.append(phase.phase_id)
            continue
        rounded = round_up_to_step(average, settings.phase_time_round_up_step_s)
        intergreen, intergreen_note = _phase_intergreen(phase)
        if intergreen is not None and intergreen >= rounded:
            issues.append(
                _issue(
                    IssueSeverity.REFUSE,
                    "fixed_time_intergreen_exceeds_phase",
                    f"Phase {phase.phase_id}: its intergreen ({intergreen:g} s) is at least its "
                    f"whole derived phase time ({rounded:g} s), leaving no green. Either the "
                    "clearance times or the observed history is wrong; the module will not shave "
                    "one to fit the other.",
                    subject=subject,
                    citation=CIT_A3_INTERGREEN,
                    context={
                        "phase_id": phase.phase_id,
                        "intergreen_s": intergreen,
                        "phase_time_s": rounded,
                    },
                )
            )
            return _bail()
        if intergreen_note is not None:
            issues.append(intergreen_note(subject, phase.phase_id))
        kept.append((phase, average, rounded, intergreen))

    if dropped_ids:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "fixed_time_phase_never_ran",
                f"Phase(s) {dropped_ids} never ran in the observation window, so A.2's own "
                "t̄ᵢ = avg × fᵢ gives them 0 s. They are LEFT OUT of the fixed-time plan "
                "(ASSUMPTION A5) — a 0-second phase is not runnable. Whatever movements they "
                "serve get no service in this plan; confirm that matches the modelled period.",
                subject=subject,
                citation=CIT_A2_AVG_PHASE,
                context={"dropped_phase_ids": dropped_ids},
            )
        )
    if not kept:
        issues.append(
            _issue(
                IssueSeverity.REFUSE,
                "fixed_time_all_phases_dropped",
                f"Every phase of program {resolved.program_id} averages 0 s; there is no plan to "
                "build.",
                subject=subject,
                citation=CIT_A2_AVG_PHASE,
            )
        )
        return _bail()

    plan_cycle = sum(rounded for _, _, rounded, _ in kept)
    residual = plan_cycle - average_cycle

    # -- 6. build the derived program ------------------------------------
    derived_phases: list[PhaseData] = []
    row_by_id: dict[str, PhaseDerivation] = {}
    offset = 0.0
    missing_green_split: list[str] = []
    for index, (phase, average, rounded, intergreen) in enumerate(kept):
        green = None if intergreen is None else round(rounded - intergreen, 6)
        if green is None:
            missing_green_split.append(phase.phase_id)
        derived = PhaseData(
            provenance=Provenance(
                source_file=phase.provenance.source_file,
                native_id=phase.phase_id,
                derived_by=(
                    "MRWA Appendix-A A.2 — t̄ᵢ = total activation time ÷ complete cycles, "
                    "rounded up per the extract's 1.6 note"
                ),
                note=(
                    f"t̄ = {phase.observed_total_duration_s:g} s ÷ {cycles} cycles = "
                    f"{average:g} s → {rounded:g} s"
                ),
            ),
            phase_id=phase.phase_id,
            name=phase.name,
            sequence_index=index,
            green_s=green,
            amber_s=phase.amber_s,
            all_red_s=phase.all_red_s,
            intergreen_s=intergreen,
            min_green_s=phase.min_green_s,
            max_green_s=phase.max_green_s,
            is_stretch_phase=bool(phase.is_stretch_phase),
            frequency=(phase.observation_count / cycles)
            if phase.observation_count is not None
            else None,
        )
        if green is None:
            derived.mark_absent(
                "green_s",
                "No intergreen and no yellow+all-red pair in the source, so A.2's phase time "
                "cannot be split into green and clearance. The phase's total duration is still "
                "derived; only its split is unknown.",
            )
        if derived.frequency is None:
            derived.mark_absent(
                "frequency",
                "The source states no occurrence count for this phase, so A.2's fᵢ is unknown.",
            )
        derived_phases.append(derived)

        if green is not None and phase.min_green_s is not None and green < phase.min_green_s:
            frequency = (
                phase.observation_count / cycles if phase.observation_count is not None else None
            )
            low_frequency = frequency is not None and frequency < 1.0
            issues.append(
                _issue(
                    IssueSeverity.INFO if low_frequency else IssueSeverity.WARN,
                    "fixed_time_green_below_min_green",
                    f"Phase {phase.phase_id}: derived green {green:g} s is below the stated "
                    f"minimum green {phase.min_green_s:g} s."
                    + (
                        f" Expected here — the phase runs in only {frequency:.3g} of cycles, and "
                        "A.2 deliberately scales a non-every-cycle phase down by its frequency, "
                        "so its fixed-time share is smaller than the green it actually gets."
                        if low_frequency
                        else " This phase runs every cycle, so a green below the controller's own "
                        "minimum points at bad input. Green was NOT raised to the minimum — MRWA "
                        "§5 uses min green as the controller states it."
                    ),
                    subject=subject,
                    citation=CIT_A2_AVG_PHASE,
                    context={
                        "phase_id": phase.phase_id,
                        "green_s": green,
                        "min_green_s": phase.min_green_s,
                        "frequency": frequency,
                    },
                )
            )

        row_by_id[phase.phase_id] = PhaseDerivation(
            phase_id=phase.phase_id,
            occurrences=phase.observation_count,
            observed_total_s=phase.observed_total_duration_s or 0.0,
            scats_average_s=(
                (phase.observed_total_duration_s or 0.0) / phase.observation_count
                if phase.observation_count
                else None
            ),
            frequency=derived.frequency,
            average_phase_s=average,
            rounded_phase_s=rounded,
            start_offset_s=offset,
            intergreen_s=intergreen,
            green_s=green,
            is_stretch_phase=bool(phase.is_stretch_phase),
        )
        offset += rounded

    # The derivation table stays in A.2's cycle order (stretch phase first),
    # dropped phases included in place — a reviewer reading the table must see
    # the same sequence the plan was built from, not the survivors alone.
    rows: list[PhaseDerivation] = []
    for phase, average, frequency, scats_average in averages:
        existing = row_by_id.get(phase.phase_id)
        if existing is not None:
            rows.append(existing)
            continue
        rows.append(
            PhaseDerivation(
                phase_id=phase.phase_id,
                occurrences=phase.observation_count,
                observed_total_s=phase.observed_total_duration_s or 0.0,
                scats_average_s=scats_average,
                frequency=frequency,
                average_phase_s=average,
                rounded_phase_s=None,
                start_offset_s=None,
                intergreen_s=None,
                green_s=None,
                is_stretch_phase=bool(phase.is_stretch_phase),
                dropped=True,
                drop_reason=(
                    "A.2's t̄ᵢ = avg × fᵢ is 0 s — the phase never ran in the observation "
                    "window (ASSUMPTION A5)."
                ),
            )
        )

    if missing_green_split:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "fixed_time_green_split_unavailable",
                f"Phase(s) {missing_green_split}: the source states neither an intergreen nor a "
                "yellow+all-red pair, so A.2's phase time could not be split into green and "
                "clearance. It was NOT back-computed — A.3's formulae need approach speed, grade "
                "and clearance distance that the neutral records do not carry.",
                subject=subject,
                citation=CIT_A3_INTERGREEN,
                context={"phase_ids": missing_green_split},
            )
        )

    program_out = SignalProgramData(
        provenance=Provenance(
            source_file=resolved.provenance.source_file,
            native_id=f"{resolved.program_id}_fixed_time",
            derived_by=(
                "MRWA Appendix-A A.2 — SCATS→fixed-time conversion (services/dataimport/"
                "fixed_time.py, roadmap 1.6)"
            ),
            note=(
                f"c̄ = {average_cycle:g} s over {cycles} complete cycles; plan cycle "
                f"{plan_cycle:g} s after round-up (residual {residual:g} s)"
            ),
        ),
        program_id=f"{resolved.program_id}_fixed_time",
        name=f"Fixed-time plan derived from {resolved.name or resolved.program_id}",
        source_kind=ProgramSourceKind.DERIVED,
        cycle_time_s=plan_cycle,
        phases=derived_phases,
        observation_window=resolved.observation_window,
        observed_cycle_count=cycles,
        time_of_day=resolved.time_of_day,
        coordination=resolved.coordination,
    )
    program_out.mark_absent(
        "offset_s",
        "A.5 derives an offset by identifying the active link plan from the Strategic Monitor "
        "graph and interpolating the published offset table; neither input is in the neutral "
        "records. interpolate_offset_s() computes it once a caller supplies the table.",
    )
    program_out.mark_absent(
        "signal_group_windows",
        "Appendix A states no rule for laying signal groups out inside the derived phases, and "
        "the layout depends on the target network — that is roadmap 3.1's step. Each phase's "
        "start offset is published on FixedTimePlan.rows instead.",
    )

    # -- 7. always-on disclosures + context-dependent warnings ------------
    issues.append(
        _issue(
            IssueSeverity.INFO,
            "fixed_time_round_up_residual",
            f"Plan cycle {plan_cycle:g} s = the sum of {len(kept)} rounded-up phase times, against "
            f"an observed average cycle c̄ of {average_cycle:g} s (residual {residual:g} s). A.2 "
            "states Σ t̄ᵢ = c̄ while the extract's 1.6 note requires SCATS fractions to round up; "
            "both cannot hold, and this module makes the rounded phase times authoritative "
            "(ASSUMPTION A1).",
            subject=subject,
            citation=CIT_IMPL_NOTES,
            context={
                "plan_cycle_s": plan_cycle,
                "average_cycle_s": average_cycle,
                "residual_s": residual,
                "phase_count": len(kept),
            },
        )
    )
    issues.append(_yellow_trap_notice(subject))

    low_frequency_ids = [
        row.phase_id for row in rows if row.frequency is not None and row.frequency < 1.0
    ]
    if low_frequency_ids and (
        resolved.coordination is not None or controller.coordination is not None
    ):
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "fixed_time_coordinated_cycle_recount_required",
                f"This site is coordinated and has low-frequency phase(s) {low_frequency_ids}. "
                "A.2/Fig A-5 requires the cycles to be RECOUNTED at the coordinated partner in "
                "that case; only this site's history was supplied, so the cycle count — and every "
                "phase time divided by it — may be wrong.",
                subject=subject,
                citation=CIT_A2_FIG_A5,
                context={"low_frequency_phase_ids": low_frequency_ids},
            )
        )

    bounds = (controller.cycle_min_s, controller.cycle_max_s)
    if bounds[0] is not None and plan_cycle < bounds[0]:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "fixed_time_cycle_outside_published_bounds",
                f"Derived plan cycle {plan_cycle:g} s is below the controller's stated minimum "
                f"cycle {bounds[0]:g} s.",
                subject=subject,
                citation=CIT_A5_CYCLE_BOUNDS,
                context={"plan_cycle_s": plan_cycle, "cycle_min_s": bounds[0]},
            )
        )
    elif bounds[1] is not None and plan_cycle > bounds[1]:
        issues.append(
            _issue(
                IssueSeverity.WARN,
                "fixed_time_cycle_outside_published_bounds",
                f"Derived plan cycle {plan_cycle:g} s is above the controller's stated maximum "
                f"cycle {bounds[1]:g} s.",
                subject=subject,
                citation=CIT_A5_CYCLE_BOUNDS,
                context={"plan_cycle_s": plan_cycle, "cycle_max_s": bounds[1]},
            )
        )

    return FixedTimePlan(
        controller_id=controller.controller_id,
        source_program_id=resolved.program_id,
        program=program_out,
        complete_cycles=cycles,
        stretch_phase_id=stretch.phase_id,
        average_cycle_s=average_cycle,
        plan_cycle_s=plan_cycle,
        round_up_residual_s=residual,
        rows=tuple(rows),
        issues=tuple(issues),
    )


def _phase_intergreen(
    phase: PhaseData,
) -> tuple[float | None, Callable[[str, str], ImportIssue] | None]:
    """A.3's intergreen for one phase, and an optional issue factory.

    Priority: the stated ``intergreen_s`` (which already includes early cut-off
    by A.3's definition), else ``amber + all_red`` when BOTH are stated. One of
    the pair alone is not half an intergreen — it is an unknown, and is treated
    as one.

    Returns ``(intergreen_or_None, issue_factory_or_None)``; the factory is
    deferred so the caller can attach the right subject.
    """
    if phase.intergreen_s is not None:
        return phase.intergreen_s, None
    if phase.amber_s is not None and phase.all_red_s is not None:
        composed = phase.amber_s + phase.all_red_s

        def _note(subject: str, phase_id: str) -> ImportIssue:
            return _issue(
                IssueSeverity.INFO,
                "fixed_time_intergreen_composed",
                f"Phase {phase_id}: intergreen composed as yellow + all-red = {composed:g} s. "
                "A.3 defines intergreen as early cut-off + yellow + all-red, but early cut-off is "
                "a per-signal-group, site-justified value and a phase may serve several groups, "
                "so it is NOT included here (ASSUMPTION A6). Green is correspondingly generous by "
                "whatever the early cut-off is.",
                subject=subject,
                citation=CIT_A3_INTERGREEN,
                context={"phase_id": phase_id, "intergreen_s": composed},
            )

        return composed, _note
    return None, None


__all__ = [
    "ALL_RED_MINIMUM_S",
    "CIT_A2_AVG_CYCLE",
    "CIT_A2_AVG_PHASE",
    "CIT_A2_FIG_A5",
    "CIT_A2_PED_DEMAND",
    "CIT_A2_STRETCH",
    "CIT_A3_ALL_RED",
    "CIT_A3_INTERGREEN",
    "CIT_A3_PEDESTRIAN",
    "CIT_A3_PROTECTION",
    "CIT_A3_YELLOW",
    "CIT_A4_YELLOW_TRAP",
    "CIT_A5_CYCLE_BOUNDS",
    "CIT_A5_OFFSETS",
    "CIT_IMPL_NOTES",
    "CLEARANCE_ROUND_UP_STEP_S",
    "ConversionSettings",
    "EXTRACT",
    "FixedTimePlan",
    "GRAVITY_MS2",
    "PEDESTRIAN_ROUND_UP_STEP_S",
    "PEDESTRIAN_WALKING_SPEED_MS",
    "PROTECTION_FULL_CROSSING_FRACTION",
    "PhaseDerivation",
    "REFUSE_CODES",
    "TABLE_A3_CLEARANCE_SPEED_MS",
    "TABLE_A5_ENDPOINTS_M_TO_S",
    "WA_DECELERATION_MS2",
    "WA_REACTION_TIME_S",
    "WA_STANDARD_WALK_S",
    "YELLOW_MINIMUM_S",
    "all_red_time_formula_s",
    "all_red_time_s",
    "all_red_time_wa_s",
    "convert_to_fixed_time",
    "intergreen_time_s",
    "interpolate_offset_s",
    "pedestrian_clearance_1_s",
    "pedestrian_clearance_2_s",
    "pedestrian_demand_frequency",
    "protection_time_red_arrow_with_flash_s",
    "protection_time_timed_red_arrow_s",
    "round_up_to_step",
    "yellow_time_both_approaches_s",
    "yellow_time_s",
]

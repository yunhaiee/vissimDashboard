"""MRWA §5.6.3 base-model validation criteria -- the backend half.

THIS MODULE HOLDS NO LITERAL THRESHOLDS. Every number is loaded from
``shared/mrwa-validation-criteria.json``, the same file
``frontend/lib/validationProfiles.ts`` answers to (that side keeps literals
and is pinned to the JSON by ``frontend/tests/dashboard-contract.test.ts``).
Same cross-language pattern as ``shared/geh-conformance.json``: change the
JSON first, and both runtimes move together instead of drifting.

Before this module the only backend copy of the flow bands was
``report_exports._flow_pass``, whose 700/100/2700/0.15/400 literals had no
connection to the frontend module at all -- a guideline revision applied on
one side would have shipped a report and a dashboard that disagreed.

Source: MRWA Operational Modelling Guidelines v2.0, §5 "Vissim Guidelines",
§5.6.3 "Model Validation Requirement".

  * Table 5-12 "Traffic flow validation criteria"  (§5.6.3.1, doc p.213)
  * Table 5-13 "Travel time validation criteria"   (§5.6.3.2, doc p.215)
  * Table 5-14 "Microsimulation signal timing
    validation criteria"                           (§5.6.3.4, doc p.216)
  * saturation flow, body text                     (§5.6.3.5, doc p.216)
  * critical-intersection override, body text      (§5.6.3.1, doc p.213)

CITATIONS ARE DOC PAGES (the number printed as "Page NNN of 271"); doc page
= PDF page - 18. ERRATA GUARD: §5's in-text figure/table cross-references
carry nine measured errors (body text says "Figure N" where the object is
"Table N"), so every citation here names the TABLE and the DOC PAGE, never
an in-text label. See docs/product/reference/mrwa-section5-extract.md.

DIRECTION LANDMINE: travel time is "whichever is GREATER" and signal timing
is "whichever is SMALLER". They are opposites. ``signal_timing_tolerance_
seconds`` uses ``min`` and must never be replaced by, or merged with, a
travel-time style ``max`` helper -- that would silently loosen every
signal-timing band.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

CRITERIA_PATH = Path(__file__).resolve().parents[2] / "shared" / "mrwa-validation-criteria.json"

_CRITERIA = json.loads(CRITERIA_PATH.read_text(encoding="utf-8"))

_FLOW = _CRITERIA["flow_bands"]
_TRAVEL = _CRITERIA["travel_time"]
_SIGNAL = _CRITERIA["signal_timing"]
_SATURATION = _CRITERIA["saturation_flow"]
_CRITICAL = _CRITERIA["critical_intersection"]

# --- Table 5-12 flow bands (§5.6.3.1, doc p.213) -----------------------------
# The three tolerance WIDTHS are category-invariant; only the pass rates in
# VALIDATION_PROFILES vary by category.
FLOW_LOW_MAX_VPH: float = _FLOW["low_max_vph"]
FLOW_LOW_TOLERANCE_VPH: float = _FLOW["low_tolerance_vph"]
FLOW_MID_MAX_VPH: float = _FLOW["mid_max_vph"]
FLOW_MID_TOLERANCE_PCT: float = _FLOW["mid_tolerance_pct"]
FLOW_HIGH_TOLERANCE_VPH: float = _FLOW["high_tolerance_vph"]

# --- §5.6.3.2 travel time (band p.215 body text, rate Table 5-13 p.215) ------
TRAVEL_TOLERANCE_PCT: float = _TRAVEL["tolerance_pct"]
TRAVEL_TOLERANCE_MIN_MINUTES: float = _TRAVEL["tolerance_min_minutes"]

# --- Table 5-14 signal timing (§5.6.3.4, doc p.216) --------------------------
# Category-invariant: Table 5-14 gives no pass rate and no Cat 1/2/3 split.
SIGNAL_CYCLE_TIME_SECONDS: float = _SIGNAL["cycle_time_seconds"]
SIGNAL_CYCLE_TIME_PCT: float = _SIGNAL["cycle_time_pct"]
SIGNAL_GREEN_TIME_SECONDS: float = _SIGNAL["green_time_seconds"]
SIGNAL_GREEN_TIME_PCT: float = _SIGNAL["green_time_pct"]
# Table 5-14's vehicle-actuated row states a comparison obligation with NO
# numeric tolerance. ``None`` means "the guideline gives no number", which is
# not "not yet implemented" -- do not invent one.
SIGNAL_ACTUATED_CALL_FREQUENCY_TOLERANCE = _SIGNAL["actuated_call_frequency_tolerance"]

# --- §5.6.3.5 saturation flow (doc p.216) ------------------------------------
SATURATION_FLOW_WITHIN_PCT: float = _SATURATION["within_pct"]

# --- §5.6.3.1 critical-intersection override (doc p.213) ---------------------
CRITICAL_INTERSECTION_GEH_MAX: float = _CRITICAL["geh_max"]


@dataclass(frozen=True)
class SignalTimingBand:
    """One "within X seconds or Y per cent, whichever is SMALLER" band."""

    seconds: float
    pct: float


SIGNAL_TIMING_BANDS: dict[str, SignalTimingBand] = {
    "cycle_time": SignalTimingBand(SIGNAL_CYCLE_TIME_SECONDS, SIGNAL_CYCLE_TIME_PCT),
    "green_time": SignalTimingBand(SIGNAL_GREEN_TIME_SECONDS, SIGNAL_GREEN_TIME_PCT),
}


@dataclass(frozen=True)
class ValidationProfile:
    """One model category's complete §5.6.3 criteria set.

    Mirrors ``ValidationProfile`` in frontend/lib/validationProfiles.ts field
    for field, including the deliberate choice to carry the
    category-INVARIANT tolerances per-profile so a consumer reads exactly one
    object.
    """

    id: str
    name_ko: str
    name_en: str
    citation: str
    geh_under_5_pct: float
    geh_under_10_pct: float
    r_squared_min: float
    flow_low_within_pct: float
    flow_low_tolerance_vph: float
    flow_mid_within_pct: float
    flow_mid_tolerance_pct: float
    flow_high_within_pct: float
    flow_high_tolerance_vph: float
    travel_within_pct: float
    travel_tolerance_pct: float
    travel_tolerance_min_minutes: float
    saturation_flow_within_pct: float
    critical_intersection_geh_max: float

    @property
    def signal_timing(self) -> dict[str, SignalTimingBand]:
        """Table 5-14's bands. Category-invariant -- same object for every profile."""
        return SIGNAL_TIMING_BANDS


def _build_profile(key: str, raw: dict) -> ValidationProfile:
    return ValidationProfile(
        id=key,
        name_ko=raw["name_ko"],
        name_en=raw["name_en"],
        citation=(
            f"MRWA OMG v2.0 §5 Vissim Guidelines — Table 5-12 (p.213) / "
            f"Table 5-13 (p.215), {raw['name_en']}"
        ),
        geh_under_5_pct=raw["geh_under_5_pct"],
        geh_under_10_pct=raw["geh_under_10_pct"],
        r_squared_min=raw["r_squared_min"],
        flow_low_within_pct=raw["flow_low_within_pct"],
        flow_low_tolerance_vph=FLOW_LOW_TOLERANCE_VPH,
        flow_mid_within_pct=raw["flow_mid_within_pct"],
        flow_mid_tolerance_pct=FLOW_MID_TOLERANCE_PCT,
        flow_high_within_pct=raw["flow_high_within_pct"],
        flow_high_tolerance_vph=FLOW_HIGH_TOLERANCE_VPH,
        travel_within_pct=raw["travel_within_pct"],
        travel_tolerance_pct=TRAVEL_TOLERANCE_PCT,
        travel_tolerance_min_minutes=TRAVEL_TOLERANCE_MIN_MINUTES,
        saturation_flow_within_pct=SATURATION_FLOW_WITHIN_PCT,
        critical_intersection_geh_max=CRITICAL_INTERSECTION_GEH_MAX,
    )


VALIDATION_PROFILES: dict[str, ValidationProfile] = {
    key: _build_profile(key, raw) for key, raw in _CRITERIA["categories"].items()
}

DEFAULT_VALIDATION_CATEGORY = "cat1"
VALIDATION_CATEGORY_ORDER: tuple[str, ...] = ("cat1", "cat2", "cat3")


def get_active_profile(category: str | None = None) -> ValidationProfile:
    """Resolve ``category`` to its criteria set, defaulting to Category 1.

    Never raises; an unrecognised value falls back exactly as the TypeScript
    ``getActiveProfile`` does.
    """
    if category and category in VALIDATION_PROFILES:
        return VALIDATION_PROFILES[category]
    return VALIDATION_PROFILES[DEFAULT_VALIDATION_CATEGORY]


# --- Table 5-12: per-movement flow tolerance ---------------------------------


def flow_within_tolerance(modelled, observed) -> bool | None:
    """Whether one movement meets its Table 5-12 flow band (§5.6.3.1, p.213).

    Band is chosen by the OBSERVED flow:
      * ``< 700`` veh/h            -> within ±100 veh/h
      * ``700`` .. ``2700`` veh/h  -> within ±15 %
      * ``> 2700`` veh/h           -> within ±400 veh/h

    ``None`` when either side is missing or non-finite -- not assessable,
    never a silent pass.

    The tolerance WIDTHS are category-invariant, which is why this takes no
    profile: only the required pass RATE varies by category, and that is
    applied by the caller against ``profile.flow_*_within_pct``.
    """
    if modelled is None or observed is None:
        return None
    try:
        modelled = float(modelled)
        observed = float(observed)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(modelled) or not math.isfinite(observed):
        return None

    diff = abs(modelled - observed)
    if observed < FLOW_LOW_MAX_VPH:
        return diff <= FLOW_LOW_TOLERANCE_VPH
    if observed <= FLOW_MID_MAX_VPH:
        # observed >= 700 here, so the division is always safe; the historical
        # ``if o > 0 else diff == 0`` guard in report_exports._flow_pass was
        # unreachable in this branch.
        #
        # Written as ``ratio <= pct/100`` rather than ``ratio*100 <= pct`` so
        # this is bit-for-bit the comparison the previous hard-coded
        # ``(diff / o) <= 0.15`` made: ``15 / 100.0`` rounds to the very same
        # double as the literal ``0.15``, whereas ``ratio * 100`` introduces a
        # second rounding that can flip an exactly-on-the-band movement.
        return (diff / observed) <= FLOW_MID_TOLERANCE_PCT / 100.0
    return diff <= FLOW_HIGH_TOLERANCE_VPH


# --- Table 5-14: the "whichever is SMALLER" direction ------------------------
#
# Kept separate from anything travel-time shaped ON PURPOSE. Travel time
# (§5.6.3.2, p.215) is "15 per cent or one minute, whichever is GREATER";
# Table 5-14 (p.216) is "3 seconds or 5/10 per cent, whichever is SMALLER".
# One shared max() helper would allow 6 s at a 120 s observed cycle where the
# guideline allows 3 s. Never merge the two.


def signal_timing_tolerance_seconds(observed_seconds, band: SignalTimingBand) -> float | None:
    """``min(band.seconds, observed * band.pct / 100)`` -- the SMALLER bound.

    ``None`` for a missing, non-finite or negative observation: there is no
    meaningful band around an impossible observed value, and a silent ``0``
    would read as "must match exactly".
    """
    if observed_seconds is None:
        return None
    try:
        observed = float(observed_seconds)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(observed) or observed < 0:
        return None
    relative = observed * band.pct / 100.0
    # SMALLER, not greater. See the block comment above.
    return min(float(band.seconds), relative)


def signal_timing_within_tolerance(
    observed_seconds, modelled_seconds, band: SignalTimingBand
) -> bool | None:
    """Whether a modelled signal time sits inside its Table 5-14 band.

    ``None`` -- never ``False`` and never ``True`` -- when either side is
    missing: an observation that was never collected is "not assessable",
    which must not render as a pass or a failure.
    """
    tolerance = signal_timing_tolerance_seconds(observed_seconds, band)
    if tolerance is None or modelled_seconds is None:
        return None
    try:
        modelled = float(modelled_seconds)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(modelled):
        return None
    return abs(modelled - float(observed_seconds)) <= tolerance


# --- §5.6.3.5 saturation flow -------------------------------------------------


def saturation_flow_within_tolerance(
    observed, modelled, within_pct: float = SATURATION_FLOW_WITHIN_PCT
) -> bool | None:
    """"Modelled saturation flow values should be within 10 per cent of
    observed values" (§5.6.3.5 body text, doc p.216).

    ``None`` when either side is missing or the observed value is not
    positive: not assessable, not a pass.
    """
    if observed is None or modelled is None:
        return None
    try:
        observed = float(observed)
        modelled = float(modelled)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(observed) or not math.isfinite(modelled) or observed <= 0:
        return None
    return abs(modelled - observed) / observed * 100 <= within_pct


# --- §5.6.3.1 critical-intersection override ----------------------------------


def parse_critical_node_ids(raw) -> list[str]:
    """Normalise an analyst-supplied critical-intersection list.

    Accepts a string ("12, 15 7") or any iterable of ids. De-duplicates while
    preserving input order, mirroring ``parseCriticalNodeIds`` in
    frontend/lib/validationProfiles.ts.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        tokens: Iterable[str] = raw.replace(",", " ").replace(";", " ").split()
    else:
        tokens = []
        for item in raw:
            if item is None:
                continue
            tokens = [*tokens, *str(item).replace(",", " ").replace(";", " ").split()]
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        node_id = token.strip()
        if not node_id or node_id in seen:
            continue
        seen.add(node_id)
        out.append(node_id)
    return out


def evaluate_critical_intersections(
    nodes: Sequence[dict],
    critical_node_ids,
    geh_max: float = CRITICAL_INTERSECTION_GEH_MAX,
) -> dict:
    """Apply the §5.6.3.1 override (body text, doc p.213).

    "Even if the model outputs meet the traffic volume validation criteria
    for the entire network, the modeller should check and ensure that turning
    volumes at critical intersection(s) achieve GEH less than 5."

    Deliberately NOT a pass rate: ``profile.geh_under_5_pct`` does not satisfy
    this. Category 1 tolerates 5 % of movements at GEH >= 5 network-wide,
    while this rule tolerates none of them at a designated intersection.

    ``nodes`` items: ``{"node_id": str, "node_name": str|None,
    "geh_values": [float|None, ...]}``. ``None`` GEH values are UNDEFINED
    (0 observed and 0 modelled, or a missing side) and are excluded from both
    the numerator and the denominator -- exactly as ``geh_pass_rate`` does --
    rather than scoring a free pass.

    ``passed`` is ``True`` / ``False`` / ``None``, where ``None`` means the
    check did not run (nothing designated) or could not run (every designated
    node missing or with nothing comparable). ``None`` must never be rendered
    as a pass.
    """
    requested = parse_critical_node_ids(critical_node_ids)
    by_id = {str(node.get("node_id")): node for node in nodes}

    results: list[dict] = []
    missing_ids: list[str] = []
    failing_ids: list[str] = []
    not_assessable_ids: list[str] = []

    for node_id in requested:
        node = by_id.get(node_id)
        if node is None:
            missing_ids.append(node_id)
            continue
        defined = [
            float(value)
            for value in (node.get("geh_values") or [])
            if value is not None and math.isfinite(float(value))
        ]
        failing = [value for value in defined if value >= geh_max]
        passed = None if not defined else not failing
        results.append(
            {
                "node_id": node_id,
                "node_name": node.get("node_name"),
                "compared_count": len(defined),
                "failing_count": len(failing),
                "worst_geh": max(defined) if defined else None,
                "passed": passed,
            }
        )
        if passed is False:
            failing_ids.append(node_id)
        if passed is None:
            not_assessable_ids.append(node_id)

    if not requested:
        overall = None  # the check did not run -- say so, never imply a pass
    elif failing_ids:
        overall = False
    elif any(r["passed"] is True for r in results):
        overall = True
    else:
        overall = None  # designated, but nothing comparable to check against

    return {
        "designated": bool(requested),
        "requested_ids": requested,
        "nodes": results,
        "missing_ids": missing_ids,
        "failing_node_ids": failing_ids,
        "not_assessable_node_ids": not_assessable_ids,
        "geh_max": geh_max,
        "passed": overall,
    }

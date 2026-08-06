// Single source of truth for the MRWA Operational Modelling Guidelines §5
// Category 1/2/3 base-model acceptance thresholds. Every place that renders
// a GEH / flow-band / R-squared / travel-time pass criterion on the
// base-model validation page (and any future consumer -- report exports,
// the dashboard, etc.) MUST resolve its numbers from here via
// `getActiveProfile()`. Do not re-hardcode any of these numbers locally --
// that is exactly how the page ended up with a silently Category-1-only
// `CATEGORY_ONE` constant before this module existed.
//
// Source: MRWA Operational Modelling Guidelines v2.0, §5 "Vissim Guidelines",
// §5.6 "Model Calibration and Validation" -> §5.6.3 "Model Validation
// Requirement":
//   - Table 5-12 "Traffic flow validation criteria"  (§5.6.3.1, doc p.213)
//   - Table 5-13 "Travel time validation criteria"   (§5.6.3.2, doc p.215)
//   - Table 5-14 "Microsimulation signal timing
//     validation criteria"                           (§5.6.3.4, doc p.216)
//   - saturation flow, body text                     (§5.6.3.5, doc p.216)
//   - critical-intersection override, body text      (§5.6.3.1, doc p.213)
//
// CITATIONS ARE DOC PAGES -- the number printed as "Page NNN of 271" in the
// guideline, matching the house convention of docs/product/reference/
// mrwa-appendix-a-extract.md and mrwa-section5-extract.md. Doc page = PDF
// page - 18, so a reader following a PDF viewer's page counter lands 18
// pages early.
//
// Corrected 2026-08-06: this header used to cite the PDF page indices
// (doc page + 18) rather than the printed doc pages, and used to give §5's
// title as the name of its validation subsection. §5's actual title is
// "Vissim Guidelines", and these thresholds are the VISSIM chapter's: §6
// Aimsun and §3 LinSig carry their own, and LinSig does not validate on GEH
// at all -- naming the profile "§5" without "Vissim" invites
// mis-application. Every NUMBER was already correct and none of them moved.
//
// ERRATA GUARD: §5's in-text figure/table cross-references contain nine
// measured errors (the body text says "Figure N" where the object is
// "Table N" -- §5.2.3, §5.2.3.2, §5.2.3.3, §5.2.3.4, §5.2.4, §5.2.5.1,
// §5.3.5, §5.6.2.2, §5.8.1.2; see the "Doc defects found" block of
// docs/product/reference/mrwa-section5-extract.md). Every citation in this
// module therefore names the TABLE and the DOC PAGE, never an in-text
// label.
//
// The flow-band tolerances (±100 veh/h below 700 veh/h, ±15% between
// 700-2700 veh/h, ±400 veh/h above 2700 veh/h) live in **Table 5-12**, not
// Table 5-13 -- Table 5-13 has exactly ONE row (travel time) and carries
// only that criterion's pass RATE. The travel-time tolerance band itself
// ("±15% or one minute, whichever is greater") is stated in the §5.6.3.2
// body text above Table 5-13, not in the table.
//
// Both the flow-band widths and the travel-time band are the SAME across
// all three categories -- what varies by category is the required PASS RATE
// (the share of compared movements/routes that must fall inside that band),
// not the band width itself. Both are still carried per-profile below
// (rather than factored out as one shared constant) so a consumer only ever
// has to read one object to get a category's complete criteria set, and so
// a future guideline revision that DOES vary a tolerance by category is a
// one-line change instead of a structural one. The same reasoning is why
// the three category-INVARIANT criteria added below (signal timing,
// saturation flow, critical-intersection GEH) are also carried per-profile.
//
// Cross-language: shared/mrwa-validation-criteria.json is the authority
// both this module and backend/services/validation_profiles.py answer to
// (the Python side LOADS that file; this side keeps literals and is pinned
// to it by frontend/tests/dashboard-contract.test.ts). Same pattern as
// shared/geh-conformance.json.

export type ValidationCategory = "cat1" | "cat2" | "cat3";

export const DEFAULT_VALIDATION_CATEGORY: ValidationCategory = "cat1";

export const VALIDATION_CATEGORY_ORDER: readonly ValidationCategory[] = ["cat1", "cat2", "cat3"];

/**
 * One "within X seconds or Y per cent, whichever is SMALLER" band from
 * Table 5-14 (§5.6.3.4, doc p.216).
 */
export interface SignalTimingBand {
  /** Absolute bound, in seconds. */
  seconds: number;
  /** Relative bound, as a percentage of the observed value. */
  pct: number;
}

/**
 * Table 5-14's signal-timing criteria (§5.6.3.4, doc p.216).
 *
 * Category-invariant: unlike Tables 5-12/5-13 this is a flat criteria table
 * with no pass rate and no Category 1/2/3 split.
 */
export interface SignalTimingCriteria {
  /** Fixed time control -- cycle time: within 3 s or 5 %, whichever is smaller. */
  cycleTime: SignalTimingBand;
  /** Fixed time control -- green time: within 3 s or 10 %, whichever is smaller. */
  greenTime: SignalTimingBand;
  /**
   * Vehicle actuated control -- call frequency. Table 5-14 states a
   * comparison obligation with NO numeric tolerance, so there is nothing to
   * put here. `null` means "no number exists in the guideline", which is
   * not the same as "not yet implemented" -- do not invent one.
   */
  actuatedCallFrequencyTolerance: null;
}

export interface ValidationProfile {
  id: ValidationCategory;
  /** Korean display name, e.g. "카테고리 1". */
  nameKo: string;
  /** English display name, e.g. "Category 1". */
  nameEn: string;
  /** Guideline citation shown alongside thresholds in the UI. */
  citation: string;
  /** Required share (%) of compared movements with GEH < 5. Table 5-12 (p.213). */
  gehUnder5Pct: number;
  /** Required share (%) of compared movements with GEH < 10. Table 5-12 (p.213). */
  gehUnder10Pct: number;
  /** Required R-squared (observed vs modelled volumes), e.g. 0.95 means "R² > 0.95". Table 5-12 (p.213). */
  rSquaredMin: number;
  /** Required pass rate (%) for flows < 700 veh/h to fall within ±flowLowToleranceVph. */
  flowLowWithinPct: number;
  /** Tolerance band (veh/h) for the low-flow criterion. Category-invariant per Table 5-12 (p.213). */
  flowLowToleranceVph: number;
  /** Required pass rate (%) for flows 700-2700 veh/h to fall within ±flowMidTolerancePct. */
  flowMidWithinPct: number;
  /** Tolerance band (%) for the mid-flow criterion. Category-invariant per Table 5-12 (p.213). */
  flowMidTolerancePct: number;
  /** Required pass rate (%) for flows > 2700 veh/h to fall within ±flowHighToleranceVph. */
  flowHighWithinPct: number;
  /** Tolerance band (veh/h) for the high-flow criterion. Category-invariant per Table 5-12 (p.213). */
  flowHighToleranceVph: number;
  /** Required pass rate (%) of routes whose travel time falls within tolerance. Table 5-13 (p.215). */
  travelWithinPct: number;
  /** Travel-time tolerance band (%). Category-invariant, §5.6.3.2 body text (p.215). */
  travelTolerancePct: number;
  /**
   * Travel-time tolerance floor, in minutes: the guideline's band is
   * "±travelTolerancePct% OR this many minutes, whichever is GREATER"
   * (§5.6.3.2 body text, doc p.215).
   * Not yet applied by the base-model page's travel-time check (see
   * DECISIONS/report gap note) -- carried here so the number is not lost.
   *
   * NOTE THE DIRECTION. This one is "greater"; `signalTiming`'s two bands
   * below are "smaller". They are opposites, and a shared
   * `tolerance = max(pct * x, floor)` helper used for both would silently
   * loosen the signal-timing check. See `signalTimingToleranceSeconds`.
   */
  travelToleranceMinMinutes: number;
  /**
   * Table 5-14 signal-timing bands (§5.6.3.4, doc p.216). Category-invariant
   * -- carried per-profile only so `getActiveProfile()` still returns one
   * complete criteria set, exactly as the flow-band widths above are.
   */
  signalTiming: SignalTimingCriteria;
  /**
   * "Modelled saturation flow values should be within 10 per cent of
   * observed values" (§5.6.3.5 body text, doc p.216). Category-invariant.
   */
  saturationFlowWithinPct: number;
  /**
   * The §5.6.3.1 critical-intersection override (body text, doc p.213):
   * "Even if the model outputs meet the traffic volume validation criteria
   * for the entire network, the modeller should check and ensure that
   * turning volumes at critical intersection(s) achieve GEH less than 5."
   *
   * Category-invariant, and STRICTER than every category's network-wide
   * rate: `gehUnder5Pct` does NOT satisfy it. Cat 1 tolerates 5 % of
   * movements at GEH >= 5 network-wide; this rule tolerates none of them at
   * a designated critical intersection. See `evaluateCriticalIntersections`.
   */
  criticalIntersectionGehMax: number;
}

// Table 5-12's flow-band tolerance widths do not vary by category.
const FLOW_LOW_TOLERANCE_VPH = 100;
const FLOW_MID_TOLERANCE_PCT = 15;
const FLOW_HIGH_TOLERANCE_VPH = 400;

// The §5.6.3.2 body-text travel-time tolerance band does not vary by
// category either (Table 5-13 carries only the pass rate).
const TRAVEL_TOLERANCE_PCT = 15;
const TRAVEL_TOLERANCE_MIN_MINUTES = 1;

/**
 * Table 5-14 (§5.6.3.4, doc p.216) -- flat, no category split.
 *
 * "Within 3 seconds or 5% (whichever is smaller)" for cycle time, and
 * "within 3 seconds or 10% (whichever is smaller)" for green time, both
 * against the recorded average of SCATS history data for the same one-hour
 * period.
 */
export const SIGNAL_TIMING_CRITERIA: Readonly<SignalTimingCriteria> = {
  cycleTime: { seconds: 3, pct: 5 },
  greenTime: { seconds: 3, pct: 10 },
  actuatedCallFrequencyTolerance: null,
};

/** §5.6.3.5 body text (doc p.216). Category-invariant. */
export const SATURATION_FLOW_WITHIN_PCT = 10;

/** §5.6.3.1 body text (doc p.213). Category-invariant. */
export const CRITICAL_INTERSECTION_GEH_MAX = 5;

export const VALIDATION_PROFILES: Readonly<Record<ValidationCategory, ValidationProfile>> = {
  cat1: {
    id: "cat1",
    nameKo: "카테고리 1",
    nameEn: "Category 1",
    citation: "MRWA OMG v2.0 §5 Vissim Guidelines — Table 5-12 (p.213) / Table 5-13 (p.215), Cat 1",
    gehUnder5Pct: 95,
    gehUnder10Pct: 100,
    rSquaredMin: 0.95,
    flowLowWithinPct: 95,
    flowLowToleranceVph: FLOW_LOW_TOLERANCE_VPH,
    flowMidWithinPct: 95,
    flowMidTolerancePct: FLOW_MID_TOLERANCE_PCT,
    flowHighWithinPct: 95,
    flowHighToleranceVph: FLOW_HIGH_TOLERANCE_VPH,
    travelWithinPct: 100,
    travelTolerancePct: TRAVEL_TOLERANCE_PCT,
    travelToleranceMinMinutes: TRAVEL_TOLERANCE_MIN_MINUTES,
    signalTiming: SIGNAL_TIMING_CRITERIA,
    saturationFlowWithinPct: SATURATION_FLOW_WITHIN_PCT,
    criticalIntersectionGehMax: CRITICAL_INTERSECTION_GEH_MAX,
  },
  cat2: {
    id: "cat2",
    nameKo: "카테고리 2",
    nameEn: "Category 2",
    citation: "MRWA OMG v2.0 §5 Vissim Guidelines — Table 5-12 (p.213) / Table 5-13 (p.215), Cat 2",
    gehUnder5Pct: 85,
    gehUnder10Pct: 95,
    rSquaredMin: 0.95,
    flowLowWithinPct: 90,
    flowLowToleranceVph: FLOW_LOW_TOLERANCE_VPH,
    flowMidWithinPct: 90,
    flowMidTolerancePct: FLOW_MID_TOLERANCE_PCT,
    flowHighWithinPct: 90,
    flowHighToleranceVph: FLOW_HIGH_TOLERANCE_VPH,
    travelWithinPct: 90,
    travelTolerancePct: TRAVEL_TOLERANCE_PCT,
    travelToleranceMinMinutes: TRAVEL_TOLERANCE_MIN_MINUTES,
    signalTiming: SIGNAL_TIMING_CRITERIA,
    saturationFlowWithinPct: SATURATION_FLOW_WITHIN_PCT,
    criticalIntersectionGehMax: CRITICAL_INTERSECTION_GEH_MAX,
  },
  cat3: {
    id: "cat3",
    nameKo: "카테고리 3",
    nameEn: "Category 3",
    citation: "MRWA OMG v2.0 §5 Vissim Guidelines — Table 5-12 (p.213) / Table 5-13 (p.215), Cat 3",
    gehUnder5Pct: 80,
    gehUnder10Pct: 90,
    rSquaredMin: 0.9,
    flowLowWithinPct: 85,
    flowLowToleranceVph: FLOW_LOW_TOLERANCE_VPH,
    flowMidWithinPct: 85,
    flowMidTolerancePct: FLOW_MID_TOLERANCE_PCT,
    flowHighWithinPct: 85,
    flowHighToleranceVph: FLOW_HIGH_TOLERANCE_VPH,
    travelWithinPct: 85,
    travelTolerancePct: TRAVEL_TOLERANCE_PCT,
    travelToleranceMinMinutes: TRAVEL_TOLERANCE_MIN_MINUTES,
    signalTiming: SIGNAL_TIMING_CRITERIA,
    saturationFlowWithinPct: SATURATION_FLOW_WITHIN_PCT,
    criticalIntersectionGehMax: CRITICAL_INTERSECTION_GEH_MAX,
  },
};

export function isValidationCategory(value: unknown): value is ValidationCategory {
  return value === "cat1" || value === "cat2" || value === "cat3";
}

/**
 * Resolves `category` to its full threshold set, falling back to the
 * default (Category 1 -- today's behavior) for a missing or unrecognized
 * value. Never throws; safe to call with `null`/`undefined` (e.g. before a
 * stored preference has loaded).
 */
export function getActiveProfile(category?: ValidationCategory | null): ValidationProfile {
  if (category && VALIDATION_PROFILES[category]) return VALIDATION_PROFILES[category];
  return VALIDATION_PROFILES[DEFAULT_VALIDATION_CATEGORY];
}

// --- Table 5-14 signal timing: the "whichever is SMALLER" direction --------
//
// This helper exists SEPARATELY from anything travel-time shaped on purpose.
// Travel time (§5.6.3.2, p.215) is "15 per cent or one minute, whichever is
// GREATER"; Table 5-14 (p.216) is "3 seconds or 5/10 per cent, whichever is
// SMALLER". Reusing one `max()` helper for both would silently widen every
// signal-timing band -- at a 120 s observed cycle, max() allows 6 s where
// the guideline allows 3 s. Never merge the two.

/**
 * The Table 5-14 tolerance, in seconds, that applies to one observed value:
 * `min(band.seconds, observed * band.pct / 100)`.
 *
 * Returns `null` for a missing or non-finite observed value, and for a
 * negative one -- there is no meaningful band around an impossible
 * observation, and a silent 0 would read as "must match exactly".
 */
export function signalTimingToleranceSeconds(
  observedSeconds: number | null | undefined,
  band: SignalTimingBand,
): number | null {
  if (observedSeconds == null || !Number.isFinite(observedSeconds) || observedSeconds < 0) return null;
  const relative = (observedSeconds * band.pct) / 100;
  // SMALLER, not greater. See the block comment above.
  return Math.min(band.seconds, relative);
}

/**
 * Whether a modelled signal time is inside its Table 5-14 band.
 *
 * Returns `null` -- never `false` and never `true` -- when either side is
 * missing: a signal-timing observation that was never collected is "not
 * assessable", which must not render as a pass or a failure.
 */
export function signalTimingWithinTolerance(
  observedSeconds: number | null | undefined,
  modelledSeconds: number | null | undefined,
  band: SignalTimingBand,
): boolean | null {
  const tolerance = signalTimingToleranceSeconds(observedSeconds, band);
  if (tolerance == null) return null;
  if (modelledSeconds == null || !Number.isFinite(modelledSeconds)) return null;
  return Math.abs(modelledSeconds - (observedSeconds as number)) <= tolerance;
}

// --- §5.6.3.5 saturation flow ---------------------------------------------

/**
 * Whether a modelled lane saturation flow is within `withinPct` of the
 * observed value (§5.6.3.5 body text, doc p.216 -- 10 per cent).
 *
 * `null` when either side is missing or the observed value is not positive:
 * "not assessable", not a pass.
 */
export function saturationFlowWithinTolerance(
  observed: number | null | undefined,
  modelled: number | null | undefined,
  withinPct: number = SATURATION_FLOW_WITHIN_PCT,
): boolean | null {
  if (observed == null || !Number.isFinite(observed) || observed <= 0) return null;
  if (modelled == null || !Number.isFinite(modelled)) return null;
  return (Math.abs(modelled - observed) / observed) * 100 <= withinPct;
}

// --- §5.6.3.1 critical-intersection override -------------------------------

/** One designated intersection's compared movements. */
export interface CriticalIntersectionInput {
  nodeId: string;
  nodeName?: string | null;
  /**
   * Every compared movement's GEH at this node. `null` entries are
   * movements whose GEH is UNDEFINED (0 observed and 0 modelled, or a
   * missing side) -- they are excluded from both the numerator and the
   * denominator, exactly as `gehPassRate` does, rather than scoring a free
   * pass.
   */
  gehValues: Array<number | null | undefined>;
}

export interface CriticalIntersectionNodeResult {
  nodeId: string;
  nodeName: string | null;
  /** Movements at this node with a DEFINED GEH. */
  comparedCount: number;
  /** Of those, how many are at or above the threshold. */
  failingCount: number;
  worstGeh: number | null;
  /**
   * `true` when every compared movement is under the threshold, `false`
   * when at least one is not, and `null` when the node has nothing
   * comparable at all. Never `true` on an empty node.
   */
  passed: boolean | null;
}

export interface CriticalIntersectionEvaluation {
  /** Whether the analyst named any critical intersections at all. */
  designated: boolean;
  /** The ids as designated, de-duplicated and in input order. */
  requestedIds: string[];
  /** Per-node outcome, in `requestedIds` order, for ids present in the data. */
  nodes: CriticalIntersectionNodeResult[];
  /** Designated ids with no matching node in the compared data. */
  missingIds: string[];
  /** Ids whose node failed the override. */
  failingNodeIds: string[];
  /** Ids present in the data but with no comparable movement. */
  notAssessableNodeIds: string[];
  /** The GEH bound actually applied (profile-resolved). */
  gehMax: number;
  /**
   * `true` when every designated intersection passed, `false` when at least
   * one failed, and `null` when the check did not run (nothing designated)
   * or could not run (every designated node missing/not comparable).
   *
   * `null` must never be rendered as a pass: "critical 교차로 미지정"
   * (no critical intersection designated) is a statement about coverage,
   * not a result.
   */
  passed: boolean | null;
}

/**
 * Parses an analyst-typed critical-intersection list ("12, 15 7") into
 * de-duplicated node ids, preserving input order. Accepts commas,
 * whitespace, semicolons and newlines as separators.
 */
export function parseCriticalNodeIds(raw: string | null | undefined): string[] {
  if (!raw) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const token of raw.split(/[\s,;]+/)) {
    const id = token.trim();
    if (!id || seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

/**
 * Applies the §5.6.3.1 override (doc p.213): every designated critical
 * intersection must INDIVIDUALLY achieve GEH < `gehMax` on its turning
 * volumes, even when the network-wide Table 5-12 pass rates are met.
 *
 * This is deliberately NOT expressed as a pass rate. `profile.gehUnder5Pct`
 * does not satisfy it: Category 1 tolerates 5 % of movements at GEH >= 5
 * network-wide, while this rule tolerates none of them at a designated
 * intersection.
 */
export function evaluateCriticalIntersections(
  nodes: readonly CriticalIntersectionInput[],
  criticalNodeIds: readonly string[],
  gehMax: number = CRITICAL_INTERSECTION_GEH_MAX,
): CriticalIntersectionEvaluation {
  const requestedIds = parseCriticalNodeIds(criticalNodeIds.join(" "));
  const byId = new Map(nodes.map((node) => [String(node.nodeId), node]));

  const results: CriticalIntersectionNodeResult[] = [];
  const missingIds: string[] = [];
  const failingNodeIds: string[] = [];
  const notAssessableNodeIds: string[] = [];

  for (const id of requestedIds) {
    const node = byId.get(id);
    if (!node) {
      missingIds.push(id);
      continue;
    }
    const defined = node.gehValues.filter(
      (value): value is number => value != null && Number.isFinite(value),
    );
    const failing = defined.filter((value) => value >= gehMax);
    const result: CriticalIntersectionNodeResult = {
      nodeId: id,
      nodeName: node.nodeName ?? null,
      comparedCount: defined.length,
      failingCount: failing.length,
      worstGeh: defined.length > 0 ? Math.max(...defined) : null,
      passed: defined.length === 0 ? null : failing.length === 0,
    };
    results.push(result);
    if (result.passed === false) failingNodeIds.push(id);
    if (result.passed === null) notAssessableNodeIds.push(id);
  }

  let passed: boolean | null;
  if (requestedIds.length === 0) {
    passed = null; // the check did not run -- say so, never imply a pass
  } else if (failingNodeIds.length > 0) {
    passed = false;
  } else if (results.some((result) => result.passed === true)) {
    passed = true;
  } else {
    passed = null; // designated, but nothing comparable to check against
  }

  return {
    designated: requestedIds.length > 0,
    requestedIds,
    nodes: results,
    missingIds,
    failingNodeIds,
    notAssessableNodeIds,
    gehMax,
    passed,
  };
}

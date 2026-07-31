// Single source of truth for the MRWA Operational Modelling Guidelines §5
// Category 1/2/3 base-model acceptance thresholds. Every place that renders
// a GEH / flow-band / R-squared / travel-time pass criterion on the
// base-model validation page (and any future consumer -- report exports,
// the dashboard, etc.) MUST resolve its numbers from here via
// `getActiveProfile()`. Do not re-hardcode any of these numbers locally --
// that is exactly how the page ended up with a silently Category-1-only
// `CATEGORY_ONE` constant before this module existed.
//
// Source: MRWA Operational Modelling Guidelines, §5 "Model Validation"
// (micro-simulation), Tables 5-12/5-13 (p.231-234) -- see the "§5 Vissim
// Guidelines 정독 결과" section of docs/product/tk-requirements-2026-07-31.md
// for the read-through that confirmed these numbers, including the
// discovery that this codebase's original hard-coded Category 1 constant
// (gehUnder5Pct 95 / gehUnder10Pct 100 / travelWithinPct 100) already
// matched Table 5-12 Cat 1 exactly.
//
// Flow-band tolerances (±100 veh/h below 700 veh/h, ±15% between 700-2700
// veh/h, ±400 veh/h above 2700 veh/h) and the travel-time tolerance band
// (±15% or 1 minute, whichever is greater) are the SAME across all three
// categories in Table 5-13 -- what varies by category is the required PASS
// RATE (the share of compared movements/routes that must fall inside that
// band), not the band width itself. Both are still carried per-profile
// below (rather than factored out as one shared constant) so a consumer
// only ever has to read one object to get a category's complete criteria
// set, and so a future guideline revision that DOES vary a tolerance by
// category is a one-line change instead of a structural one.

export type ValidationCategory = "cat1" | "cat2" | "cat3";

export const DEFAULT_VALIDATION_CATEGORY: ValidationCategory = "cat1";

export const VALIDATION_CATEGORY_ORDER: readonly ValidationCategory[] = ["cat1", "cat2", "cat3"];

export interface ValidationProfile {
  id: ValidationCategory;
  /** Korean display name, e.g. "카테고리 1". */
  nameKo: string;
  /** English display name, e.g. "Category 1". */
  nameEn: string;
  /** Guideline citation shown alongside thresholds in the UI. */
  citation: string;
  /** Required share (%) of compared movements with GEH < 5. */
  gehUnder5Pct: number;
  /** Required share (%) of compared movements with GEH < 10. */
  gehUnder10Pct: number;
  /** Required R-squared (observed vs modelled volumes), e.g. 0.95 means "R² > 0.95". */
  rSquaredMin: number;
  /** Required pass rate (%) for flows < 700 veh/h to fall within ±flowLowToleranceVph. */
  flowLowWithinPct: number;
  /** Tolerance band (veh/h) for the low-flow criterion. Category-invariant per Table 5-13. */
  flowLowToleranceVph: number;
  /** Required pass rate (%) for flows 700-2700 veh/h to fall within ±flowMidTolerancePct. */
  flowMidWithinPct: number;
  /** Tolerance band (%) for the mid-flow criterion. Category-invariant per Table 5-13. */
  flowMidTolerancePct: number;
  /** Required pass rate (%) for flows > 2700 veh/h to fall within ±flowHighToleranceVph. */
  flowHighWithinPct: number;
  /** Tolerance band (veh/h) for the high-flow criterion. Category-invariant per Table 5-13. */
  flowHighToleranceVph: number;
  /** Required pass rate (%) of routes whose travel time falls within tolerance. */
  travelWithinPct: number;
  /** Travel-time tolerance band (%). Category-invariant per Table 5-13. */
  travelTolerancePct: number;
  /**
   * Travel-time tolerance floor, in minutes: the guideline's band is
   * "±travelTolerancePct% OR this many minutes, whichever is GREATER".
   * Not yet applied by the base-model page's travel-time check (see
   * DECISIONS/report gap note) -- carried here so the number is not lost.
   */
  travelToleranceMinMinutes: number;
}

// Table 5-13's flow-band tolerance widths do not vary by category.
const FLOW_LOW_TOLERANCE_VPH = 100;
const FLOW_MID_TOLERANCE_PCT = 15;
const FLOW_HIGH_TOLERANCE_VPH = 400;

// Table 5-13's travel-time tolerance band does not vary by category either.
const TRAVEL_TOLERANCE_PCT = 15;
const TRAVEL_TOLERANCE_MIN_MINUTES = 1;

export const VALIDATION_PROFILES: Readonly<Record<ValidationCategory, ValidationProfile>> = {
  cat1: {
    id: "cat1",
    nameKo: "카테고리 1",
    nameEn: "Category 1",
    citation: "MRWA OMG §5 Table 5-12/5-13 (Cat 1)",
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
  },
  cat2: {
    id: "cat2",
    nameKo: "카테고리 2",
    nameEn: "Category 2",
    citation: "MRWA OMG §5 Table 5-12/5-13 (Cat 2)",
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
  },
  cat3: {
    id: "cat3",
    nameKo: "카테고리 3",
    nameEn: "Category 3",
    citation: "MRWA OMG §5 Table 5-12/5-13 (Cat 3)",
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

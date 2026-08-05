/**
 * THE single frontend GEH implementation (unit 1.5, D1/D2).
 *
 * Before this module there were four live copies of the same formula --
 * `services/validation.py`, `services/report_exports.py`, and one private copy
 * inside each of `app/dashboard/page.tsx` and `app/base-model/page.tsx` -- and
 * the `report_exports` one silently disagreed with the rest (it returned a
 * PASSING 0.0 where the statistic is undefined).
 *
 * "One util" means one implementation per runtime, not a network call per row:
 * the backend home is `backend/services/validation.py`, this file is the
 * frontend home, and `shared/geh-conformance.json` is a table of cases both
 * sides are tested against so they can never drift apart silently. Add a case
 * to that JSON before changing anything here.
 */

/** GEH bands: < 5 good, < 10 fair, >= 10 poor. */
export const GEH_GOOD_LT = 5;
export const GEH_FAIR_LT = 10;

export type GehGrade = "good" | "fair" | "poor";

/**
 * GEH statistic. Returns `null` -- never a number -- when it is undefined:
 * either input missing, either input non-finite (NaN / ±Infinity),
 * `model + observed <= 0`, or the result itself overflowing to non-finite.
 *
 * A row with 0 observed and 0 modelled vehicles carries no information; the
 * old backend copy scored it 0.0, which reads as a pass on a customer-facing
 * report. Deliberately unrounded: every render site rounds to its own display
 * precision.
 *
 * The non-finite input path is REACHABLE, not theoretical: the dashboard's
 * pre-GEH volume scaling divides by a stored count that can be 0.
 */
export function geh(model: number | null | undefined, observed: number | null | undefined): number | null {
  if (model == null || observed == null) return null;
  if (!Number.isFinite(model) || !Number.isFinite(observed)) return null;
  const denom = model + observed;
  if (denom <= 0) return null;
  // Result guard: finite inputs can still overflow (`2 * 1e308` is Infinity).
  // Python raises OverflowError on the same pair where JS returns Infinity, so
  // without this both runtimes would be wrong in two DIFFERENT ways.
  const value = Math.sqrt((2 * (model - observed) ** 2) / denom);
  return Number.isFinite(value) ? value : null;
}

/** `good` / `fair` / `poor`, or `null` when the GEH is undefined. */
export function gehGrade(value: number | null | undefined): GehGrade | null {
  if (value == null || !Number.isFinite(value)) return null;
  if (value < GEH_GOOD_LT) return "good";
  if (value < GEH_FAIR_LT) return "fair";
  return "poor";
}

export type GehPassRate = {
  passed: number;
  /** Rows with a DEFINED GEH -- the denominator actually used. */
  total: number;
  /** Rows dropped because GEH is undefined. Disclose this, never hide it. */
  excluded: number;
  rate: number;
};

/**
 * Aggregate GEH values into pass/total/excluded/rate.
 *
 * `null` values are excluded from BOTH the numerator and the denominator, and
 * counted in `excluded` so a caller can disclose the shrunken denominator.
 */
export function gehPassRate(
  values: Array<number | null | undefined>,
  threshold: number = GEH_GOOD_LT,
): GehPassRate {
  const usable = values.filter((value): value is number => value != null && Number.isFinite(value));
  const passed = usable.filter((value) => value < threshold).length;
  return {
    passed,
    total: usable.length,
    excluded: values.length - usable.length,
    rate: usable.length > 0 ? (passed / usable.length) * 100 : 0,
  };
}

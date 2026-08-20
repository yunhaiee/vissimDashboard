/**
 * TypeScript half of the cross-language GEH conformance suite (unit 1.5, D2).
 *
 * Reads the SAME `shared/geh-conformance.json` that
 * `backend/tests/test_geh_conformance.py` reads, so `frontend/lib/geh.ts` and
 * `backend/services/validation.py` cannot drift apart silently. A case added
 * to that JSON is automatically enforced on both sides; a case removed from it
 * fails the "fixture is non-trivial" guard below rather than quietly
 * shrinking the suite.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import { GEH_FAIR_LT, GEH_GOOD_LT, geh, gehGrade, gehPassRate } from "../lib/geh.ts";

/** A numeric slot: a number, null, or a non-finite sentinel string. */
type Slot = number | string | null;

type Case = {
  name: string;
  model: Slot;
  observed: Slot;
  geh: Slot;
  grade: "good" | "fair" | "poor" | null;
};

type PassRateCase = {
  name: string;
  values: Slot[];
  threshold: number;
  passed: number;
  total: number;
  excluded: number;
  rate: number;
};

const repoRoot = path.resolve(import.meta.dirname, "..", "..");
const fixturePath = path.join(repoRoot, "shared", "geh-conformance.json");
const fixture = JSON.parse(readFileSync(fixturePath, "utf-8")) as {
  tolerance: number;
  non_finite_sentinels: string[];
  cases: Case[];
  pass_rate_cases: PassRateCase[];
};

// JSON cannot encode NaN/Infinity, which is exactly why this harness used to be
// blind to the one class of input the two implementations actually disagreed
// on. The fixture spells them as STRINGS; both readers decode the same three
// spellings, and an unknown string THROWS -- a decoder that shrugged and passed
// the string through would silently re-open the hole.
const SENTINELS: Record<string, number> = {
  NaN: Number.NaN,
  Infinity: Number.POSITIVE_INFINITY,
  "-Infinity": Number.NEGATIVE_INFINITY,
};

function decode(value: Slot): number | null {
  if (typeof value !== "string") return value;
  if (!(value in SENTINELS)) throw new Error(`unknown non-finite sentinel in the fixture: ${value}`);
  return SENTINELS[value];
}

function closeEnough(actual: number, expected: number): boolean {
  const scale = Math.max(1, Math.abs(expected));
  return Math.abs(actual - expected) <= fixture.tolerance * scale;
}

test("the shared conformance fixture is present and non-trivial", () => {
  assert.ok(fixture.cases.length >= 24, "expected the full GEH case table");
  assert.ok(fixture.pass_rate_cases.length >= 6, "expected the pass-rate case table");
  assert.deepEqual(
    [...fixture.non_finite_sentinels].sort(),
    Object.keys(SENTINELS).sort(),
    "the fixture declares a non-finite sentinel this reader cannot decode",
  );
  assert.ok(
    fixture.cases.some((c) => c.model === 0 && c.observed === 0 && c.geh === null),
    "expected the 0-observed/0-modelled case -- the whole reason this fixture exists",
  );
  // The non-finite cases are the ones plain JSON could not express, and the two
  // implementations diverged on every one of them. Never let them be dropped.
  const spellings = new Set(
    fixture.cases.flatMap((c) => [c.model, c.observed]).filter((v) => typeof v === "string"),
  );
  for (const sentinel of fixture.non_finite_sentinels) {
    assert.ok(spellings.has(sentinel), `expected a case exercising the ${sentinel} input`);
  }
  assert.ok(
    fixture.pass_rate_cases.some((c) => c.values.some((v) => typeof v === "string")),
    "expected at least one pass-rate case carrying a non-finite value",
  );
});

test("geh() matches the cross-language fixture case for case", () => {
  for (const testCase of fixture.cases) {
    const actual = geh(decode(testCase.model), decode(testCase.observed));
    const expected = decode(testCase.geh);
    if (expected === null) {
      assert.equal(actual, null, `${testCase.name}: expected null, got ${actual}`);
    } else {
      assert.notEqual(actual, null, `${testCase.name}: expected a number`);
      assert.ok(
        closeEnough(actual as number, expected),
        `${testCase.name}: expected ${expected}, got ${actual}`,
      );
    }
  }
});

test("gehGrade() matches the cross-language fixture case for case", () => {
  for (const testCase of fixture.cases) {
    assert.equal(
      gehGrade(geh(decode(testCase.model), decode(testCase.observed))),
      testCase.grade,
      `${testCase.name}: grade mismatch`,
    );
  }
});

test("gehPassRate() excludes undefined GEH from BOTH numerator and denominator", () => {
  for (const testCase of fixture.pass_rate_cases) {
    const result = gehPassRate(testCase.values.map(decode), testCase.threshold);
    assert.equal(result.passed, testCase.passed, `${testCase.name}: passed`);
    assert.equal(result.total, testCase.total, `${testCase.name}: total`);
    assert.equal(result.excluded, testCase.excluded, `${testCase.name}: excluded`);
    assert.ok(closeEnough(result.rate, testCase.rate), `${testCase.name}: rate`);
  }
});

test("zero over zero is null, not a passing 0.0", () => {
  assert.equal(geh(0, 0), null);
  assert.equal(gehGrade(geh(0, 0)), null);
});

test("null and non-finite inputs return null rather than coercing to zero", () => {
  assert.equal(geh(null, 100), null);
  assert.equal(geh(100, null), null);
  assert.equal(geh(undefined, undefined), null);
  assert.equal(geh(Number.NaN, 100), null);
  assert.equal(geh(Number.POSITIVE_INFINITY, 100), null);
  assert.equal(geh(100, Number.NaN), null);
  assert.equal(geh(100, Number.POSITIVE_INFINITY), null);
  assert.equal(geh(Number.NEGATIVE_INFINITY, 100), null);
  assert.equal(gehGrade(Number.NaN), null);
  assert.equal(gehGrade(Number.POSITIVE_INFINITY), null);
});

test("a result that overflows is undefined on both runtimes", () => {
  // Finite inputs, non-finite answer. Python RAISED OverflowError on the first
  // pair where JS returned Infinity -- two different wrongs; both answer null.
  assert.equal(geh(1e200, 1), null);
  assert.equal(geh(1e154, 0), null);
});

test("gehPassRate excludes non-finite from the denominator, not just null", () => {
  const nan = gehPassRate([1.0, Number.NaN, 12.0]);
  assert.deepEqual([nan.passed, nan.total, nan.excluded, nan.rate], [1, 2, 1, 50]);
  // -Infinity < threshold would otherwise be a free PASS.
  const inf = gehPassRate([1.0, Number.POSITIVE_INFINITY, 4.0, Number.NEGATIVE_INFINITY, null]);
  assert.deepEqual([inf.passed, inf.total, inf.excluded, inf.rate], [2, 2, 3, 100]);
});

test("the source does not round", () => {
  const value = geh(1000.004, 1000);
  assert.notEqual(value, null);
  assert.ok((value as number) > 0 && (value as number) < 1e-3);
});

test("band constants are a single spelling", () => {
  assert.equal(GEH_GOOD_LT, 5);
  assert.equal(GEH_FAIR_LT, 10);
  assert.equal(gehGrade(4.999), "good");
  assert.equal(gehGrade(5), "fair");
  assert.equal(gehGrade(9.999), "fair");
  assert.equal(gehGrade(10), "poor");
});

test("neither page keeps a private copy of the GEH formula (D2)", () => {
  const rootDir = path.resolve(import.meta.dirname, "..");
  // RETARGETED, meaning unchanged: unit #50 absorbed `/base-model` into the
  // calibration workspace's scoring station, so the screen this pin is about
  // is components/calibration/BaseModelStation.tsx now. The path was left
  // behind and this suite has been failing on ENOENT ever since — an
  // assertion that cannot run is worse than none, because the run reports it
  // as a failure nobody reads rather than as the rule it is.
  for (const relativePath of ["app/dashboard/page.tsx", "components/calibration/BaseModelStation.tsx"]) {
    const source = readFileSync(path.join(rootDir, relativePath), "utf-8");
    assert.doesNotMatch(
      source,
      /Math\.sqrt\(\s*\(?\s*2\s*\*/,
      `${relativePath} still contains an inline GEH formula -- import it from lib/geh.ts`,
    );
    assert.match(source, /from "@\/lib\/geh"/, `${relativePath} should import the shared GEH module`);
  }
});

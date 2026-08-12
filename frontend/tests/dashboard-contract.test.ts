import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import { DEFAULT_NEXT_PATH, loginHref, safeNextPath } from "../lib/authRedirect.ts";
import {
  CRITICAL_INTERSECTION_GEH_MAX,
  SATURATION_FLOW_WITHIN_PCT,
  SIGNAL_TIMING_CRITERIA,
  VALIDATION_PROFILES,
  evaluateCriticalIntersections,
  parseCriticalNodeIds,
  saturationFlowWithinTolerance,
  signalTimingToleranceSeconds,
  signalTimingWithinTolerance,
  type SignalTimingBand,
} from "../lib/validationProfiles.ts";

const rootDir = path.resolve(import.meta.dirname, "..");
const repoRoot = path.resolve(rootDir, "..");

function read(relativePath: string) {
  return readFileSync(path.join(rootDir, relativePath), "utf-8");
}

/**
 * The source between two markers, or "" when either marker is missing.
 *
 * Lets a pin be scoped to ONE function instead of the whole file, which is the
 * only way to assert that something is ABSENT from a specific place: a
 * file-wide `doesNotMatch` for `sameOriginRequestInit` would fail on the
 * unrelated helpers that legitimately use it. Returns "" rather than throwing
 * so a moved marker surfaces as a named pin failure alongside every other
 * failure in the run (see contractTest above), not as an aborted test.
 */
function sliceBetween(source: string, startMarker: string, endMarker: string): string {
  const start = source.indexOf(startMarker);
  if (start === -1) return "";
  const end = source.indexOf(endMarker, start + startMarker.length);
  return end === -1 ? "" : source.slice(start, end);
}

/** A RegExp matching `text` literally -- Korean UI strings are full of `.`,
 * `(` and `·`, which a raw `new RegExp(text)` would treat as syntax. */
function literalPattern(text: string): RegExp {
  return new RegExp(text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
}

type Pin = {
  match(source: string, pattern: RegExp, message: string): void;
  doesNotMatch(source: string, pattern: RegExp, message: string): void;
};

/**
 * Runs every pin in a test, even after one of them fails.
 *
 * These tests regex-pin raw source, so a pin goes stale whenever the markup it
 * describes is restyled. With plain `assert`, the first stale pin throws and
 * aborts the rest of the test() callback. Measured 2026-08-05: one stale
 * base-model layout pin -- which described `app/base-model/page.tsx.bak`, a
 * snapshot that never shipped -- had been hiding two more broken pins and 53
 * healthy ones ever since the source import. `pin.match` records a failure
 * instead of throwing, so a run always reports every stale pin at once.
 *
 * The other half of the fix is granularity: keep each test on one concern, and
 * keep brittle layout/style pins in their own test, so a restyle can only ever
 * surface next to other style pins and never on top of behavioural checks.
 */
function contractTest(name: string, body: (pin: Pin) => void) {
  test(name, () => {
    const failures: string[] = [];
    const record = (ok: boolean, pattern: RegExp, message: string) => {
      if (!ok) failures.push(`${message}\n      pattern: ${pattern}`);
    };

    body({
      match: (source, pattern, message) => record(pattern.test(source), pattern, message),
      doesNotMatch: (source, pattern, message) => record(!pattern.test(source), pattern, message),
    });

    if (failures.length > 0) {
      assert.fail(
        `${failures.length} contract pin${failures.length > 1 ? "s" : ""} failed:\n    - ${failures.join("\n    - ")}`,
      );
    }
  });
}

contractTest("main dashboard keeps scenario-comparison core composition", (pin) => {
  const page = read("app/dashboard/page.tsx");

  pin.match(
    page,
    /<DashboardScenarioPicker scenarios=\{scenarios\} selected=\{selected\} onChange=\{handleSelectedChange\} \/>/,
    "expected dashboard-specific scenario picker on the main dashboard",
  );
  pin.match(page, /<DashboardMap/, "expected map comparison panel on main dashboard");
  pin.match(page, /<NodeFlowPanel/, "expected node flow panel on main dashboard");
  pin.match(page, /fetchActiveDataset\(\)/, "expected dashboard to check whether a VISSIM dataset is uploaded");
  pin.match(page, /const hasUploadedDataset = activeDataset\?\.source === "uploaded"/, "expected dashboard to require an uploaded dataset");
  pin.match(page, /<DashboardDatasetGate \/>/, "expected scenario controls to be gated before upload");
  pin.match(page, /<DashboardUploadRequiredPanel \/>/, "expected dashboard analysis area to stay locked before upload");
  pin.match(page, /if \(!hasUploadedDataset \|\| !selected\.length\)/, "expected comparison fetches to skip default sample data");
  pin.match(
    page,
    /title=\{t\("네트워크 KPI 비교", "Network KPI Comparison"\)\}/,
    "expected network KPI comparison section on main dashboard",
  );
  pin.match(page, /교통 운영 분석/, "expected concise operations-analysis page title");
  pin.doesNotMatch(page, /시나리오 비교분석 \+ 교차로 비교분석/, "expected old combined title to be removed");
});

contractTest("app shell exposes the merged dashboard routes", (pin) => {
  const shell = read("components/AppShell.tsx");
  const floatingAssistant = read("components/FloatingAssistant.tsx");
  const simulationPage = read("app/simulation/page.tsx");

  pin.doesNotMatch(shell, /href: "\/intersections"/, "expected intersections route to be removed from top navigation");
  pin.doesNotMatch(shell, /href: "\/"/, "expected prompt home route to be removed from top navigation");
  pin.match(shell, /href: "\/base-model"/, "expected base-model validation route in top navigation");
  pin.match(shell, /href: "\/dashboard"/, "expected dashboard route in top navigation");
  pin.match(shell, /href: "\/health"/, "expected model health-check route in top navigation");
  pin.doesNotMatch(shell, /label: "Prompt"/, "expected prompt-only entry to be removed once assistant is floating");
  pin.match(shell, /FloatingAssistant/, "expected assistant chat launcher to be available across pages");
  // Streaming (not the older one-shot askAssistant) is the current call path
  // so answers render token-by-token instead of appearing all at once.
  pin.match(floatingAssistant, /streamAssistant/, "expected floating assistant to call the assistant query API");
  pin.match(
    floatingAssistant,
    /selected_scenario_ids: activePageContext\?\.selectedScenarioIds/,
    "expected floating assistant to send current page scenario selections to the assistant API",
  );
  pin.match(
    floatingAssistant,
    /selected_node_ids: activePageContext\?\.selectedNodeIds/,
    "expected floating assistant to send current page node selections to the assistant API",
  );
  pin.match(floatingAssistant, /PageContextSummary/, "expected floating assistant to show current page context");
  pin.match(floatingAssistant, /useLanguage/, "expected floating assistant to follow the app language toggle");
  pin.match(floatingAssistant, /response_language: lang/, "expected floating assistant responses to follow the active app language toggle");
  pin.doesNotMatch(floatingAssistant, /inferAssistantResponseLanguage/, "expected prompt text language inference not to override the app language toggle");
  pin.match(floatingAssistant, /buildRouteAssistantContext\(pathname, t\)/, "expected assistant route labels and starters to be localized");
  // Execution consent must never be inferred from the wording of the user's
  // message (a client-side keyword match let a suggested starter chip launch
  // a real VISSIM run with no confirmation). The first send always proposes;
  // running only happens after an explicit confirm click, which resends the
  // same prompt with allowExecution=true.
  pin.doesNotMatch(floatingAssistant, /shouldAllowPromptExecution/, "expected the client-side execution-consent heuristic to be removed");
  pin.match(
    floatingAssistant,
    /allow_tool_execution: allowExecution/,
    "expected execution consent to come from explicit user confirmation, not message wording",
  );
  pin.match(floatingAssistant, /function confirmPromptExecution/, "expected an explicit confirm action before a proposed simulation runs");
  pin.match(floatingAssistant, /function declinePromptExecution/, "expected an explicit decline action for a proposed simulation");
  pin.match(floatingAssistant, /requires_confirmation && !declined/, "expected a visible confirm/cancel control when the backend requires confirmation");
  pin.match(floatingAssistant, /AssistantAnswerContent/, "expected floating assistant to render structured answers");
  pin.match(floatingAssistant, /parseAssistantAnswer/, "expected assistant answers to be split into stable sections");
  pin.match(floatingAssistant, /PANEL_SIZE_STORAGE_KEY/, "expected assistant panel size to persist across sessions");
  pin.match(floatingAssistant, /--assistant-panel-width/, "expected assistant panel width to be driven by resize state");
  pin.match(floatingAssistant, /cursor-nwse-resize/, "expected assistant panel to expose a resize handle");
  pin.match(
    floatingAssistant,
    /vissim:assistant-simulation-complete/,
    "expected floating assistant to notify dashboard pages after prompt-run simulations",
  );
  pin.doesNotMatch(shell, /href: "\/simulation"/, "expected simulation route to be hidden from top navigation");
  pin.match(simulationPage, /SimulationPage/, "expected simulation page implementation to remain available in the codebase");
  pin.doesNotMatch(shell, /Intersection Comparison/, "expected separate intersection comparison label to be removed");
  pin.match(
    shell,
    /Base Model Validation/,
    "expected base model validation label in top navigation",
  );
});

contractTest("root entry redirects to dashboard once assistant is floating", (pin) => {
  const page = read("app/page.tsx");

  pin.match(page, /redirect/, "expected root route to redirect instead of rendering a prompt-only page");
  pin.match(page, /"\/dashboard"/, "expected root path to land on the dashboard");
  pin.doesNotMatch(page, /VissimPromptHome/, "expected prompt-only first screen to be removed from root");
});

// The base-model contract used to be one 59-pin test(). It is split by concern
// below so a single stale pin cannot abort the rest -- see contractTest above
// for the 2026-08-05 measurement that motivated the split.

contractTest("base model page wires the validation workbook lifecycle", (pin) => {
  const page = read("app/base-model/page.tsx");

  pin.match(page, /fetchBaseWorkbook\(\)/, "expected base workbook loading");
  pin.match(page, /uploadBaseWorkbook/, "expected standalone workbook upload support");
  pin.match(page, /resetBaseWorkbook/, "expected standalone workbook reset support");
  pin.match(page, /handleWorkbookReset/, "expected reset button handler");
  pin.match(page, /VissimDatasetBanner/, "expected Vissim dataset update control on base-model validation page");
  pin.match(page, /handleDatasetChange/, "expected base-model page to reload validation inputs after dataset changes");
  pin.match(page, /\[datasetRevision, selectedScenarioId, t\]/, "expected selected scenario results to refetch after dataset changes");
  pin.match(page, /fetchResults\(selectedScenarioId\)/, "expected selected base scenario result loading");
});

contractTest("base model page publishes assistant page context", (pin) => {
  const page = read("app/base-model/page.tsx");

  pin.match(page, /vissim:page-context/, "expected base-model page to publish assistant page context");
  pin.match(page, /selectedScenarioIds: selectedScenarioId \? \[selectedScenarioId\] : \[\]/, "expected selected base scenario to be passed to floating assistant context");
  pin.match(page, /Validation workbook is not connected/, "expected workbook status to be included in assistant context");
});

// Layout pins are quarantined in their own test on purpose: they go stale on
// every restyle, so grouping them means a stale one can only ever surface
// alongside another layout pin, never on top of the behavioural checks.
//
// Re-pinned 2026-08-05. Both pins here used to describe app/base-model/page.tsx.bak
// -- an abandoned snapshot that shipped in the 2026-07-21 source drop alongside
// the test pinning it. Neither had ever matched the live page: the 5-column
// `xl:grid-cols-[1fr_1fr_0.85fr_1.15fr_1.15fr]` setup row exists at no commit in
// this repo, and the `bg-slate-50/70 shadow-none` override was dropped in acf43e9
// when the Console card-alignment pass moved to equal-height `h-full` cards. The
// original intent -- setup controls share one aligned card row -- is re-expressed
// against the layout that actually shipped.
contractTest("base model setup controls share one aligned card row", (pin) => {
  const page = read("app/base-model/page.tsx");

  pin.match(page, /mt-4 grid gap-3 lg:grid-cols-\[1\.6fr_1fr\]/, "expected base-model setup controls to align in one card row");
  pin.match(page, /<div className="grid gap-3 sm:grid-cols-2">/, "expected the dataset and workbook cards to pair up inside that row");
  pin.match(page, /fullWidth className="h-full"/, "expected base-model dataset control to match adjacent setup-card sizing");
});

contractTest("base model page keeps the Category 1 GEH and travel-time checks", (pin) => {
  const page = read("app/base-model/page.tsx");
  // Category 1/2/3 thresholds moved out of page.tsx into a shared module
  // (Phase 0 item 0.6, 2026-07-31) so every consumer reads the same numbers
  // instead of each hard-coding its own CATEGORY_ONE-style constant.
  const validationProfiles = read("lib/validationProfiles.ts");

  pin.match(page, /GEH < 5/, "expected GEH band chart content");
  pin.match(page, /GehFormulaExplanation/, "expected GEH formula explanation in the Category 1 card");
  pin.match(page, /viewBox="0 0 230 58"/, "expected GEH formula to render as a fixed SVG");
  pin.match(page, /M78 31 L84 42 L94 13 L218 13/, "expected GEH radical path to be fixed");
  pin.match(page, /M \+ C/, "expected GEH formula denominator");
  pin.match(page, /x1="110" x2="203" y1="32" y2="32"/, "expected GEH formula fraction bar");
  pin.match(page, /C\/O/, "expected counted or observed volume definition");
  pin.match(page, /observed_travel_time_sec/, "expected observed travel time handling");
  pin.match(page, /model_travel_time_sec/, "expected model travel time handling");
  pin.match(
    validationProfiles,
    /travelWithinPct: 100/,
    "expected Category 1 travel time threshold (centralized in lib/validationProfiles.ts as of Phase 0 item 0.6)",
  );
  pin.match(page, /getActiveProfile\(category\)/, "expected base-model page to resolve thresholds through the shared validation-profile module");
  pin.match(page, /ValidationCategorySelector/, "expected a Category 1\/2\/3 selector on the base-model validation page");
});

// --- section-5 validation pack (2026-08-06) ---------------------------------
//
// The numbers in lib/validationProfiles.ts were all correct against MRWA OMG
// v2.0 Tables 5-12/5-13; its CITATIONS were not. It cited PDF pages 231-234
// where the doc pages are 213 (Table 5-12) and 215 (Table 5-13), titled §5
// "Model Validation" when §5 is "Vissim Guidelines", and attributed the flow
// bands to Table 5-13 when they live in Table 5-12 (5-13 has exactly one row,
// travel time). These pins are what keeps a corrected citation corrected.
contractTest("validation profiles cite doc pages, the right section title, and the right tables", (pin) => {
  const profiles = read("lib/validationProfiles.ts");

  pin.match(profiles, /§5 "Vissim Guidelines"/, "expected §5's actual title, not \"Model Validation\"");
  pin.doesNotMatch(profiles, /§5 "Model Validation"/, "expected the wrong §5 title to be gone");
  pin.match(profiles, /Table 5-12 "Traffic flow validation criteria"\s*\(§5\.6\.3\.1, doc p\.213\)/, "expected Table 5-12 cited at doc p.213");
  pin.match(profiles, /Table 5-13 "Travel time validation criteria"\s*\(§5\.6\.3\.2, doc p\.215\)/, "expected Table 5-13 cited at doc p.215");
  pin.doesNotMatch(profiles, /p\.231|p\.234|231-234/, "expected the PDF page numbers (231-234) to be replaced by doc pages");
  pin.match(
    profiles,
    /flow-band tolerances[\s\S]{0,220}?\*\*Table 5-12\*\*, not\s*\n?\/\/ Table 5-13/,
    "expected the flow bands to be attributed to Table 5-12, not Table 5-13",
  );
  pin.match(
    profiles,
    /Table 5-13 has exactly ONE row \(travel time\)/,
    "expected the one-row nature of Table 5-13 to be stated where the mis-attribution used to be",
  );
  // Errata guard: §5's in-text figure/table cross-references carry nine
  // measured errors, so no citation in this module may name a figure.
  pin.doesNotMatch(profiles, /refer to Figure 5-|see Figure 5-/, "citations must name the TABLE and page, never an in-text figure label");

  // The per-profile citation string is what the UI actually shows on hover.
  for (const profile of Object.values(VALIDATION_PROFILES)) {
    pin.match(profile.citation, /Vissim Guidelines/, `expected ${profile.id} citation to name the Vissim chapter`);
    pin.match(profile.citation, /Table 5-12 \(p\.213\)/, `expected ${profile.id} citation to cite Table 5-12 at p.213`);
    pin.match(profile.citation, /Table 5-13 \(p\.215\)/, `expected ${profile.id} citation to cite Table 5-13 at p.215`);
    pin.doesNotMatch(profile.citation, /231|234/, `expected ${profile.id} citation to drop the PDF page numbers`);
  }
});

contractTest("base model page surfaces the three §5.6.3 criteria it could not before", (pin) => {
  const page = read("app/base-model/page.tsx");
  const profiles = read("lib/validationProfiles.ts");
  const jaDictionary = read("lib/i18n-ja.ts");

  // Table 5-14 (doc p.216) had no representation at all.
  pin.match(profiles, /SIGNAL_TIMING_CRITERIA/, "expected Table 5-14's signal-timing criteria in the shared module");
  pin.match(profiles, /signalTimingToleranceSeconds/, "expected a dedicated smaller-direction helper for Table 5-14");
  pin.match(
    profiles,
    /return Math\.min\(band\.seconds, relative\)/,
    "Table 5-14 is \"whichever is SMALLER\" -- a max() here would silently loosen every signal-timing band",
  );
  pin.match(profiles, /SATURATION_FLOW_WITHIN_PCT = 10/, "expected §5.6.3.5's 10% saturation-flow criterion");
  pin.match(profiles, /CRITICAL_INTERSECTION_GEH_MAX = 5/, "expected §5.6.3.1's per-intersection GEH bound");
  pin.match(profiles, /export function evaluateCriticalIntersections\(/, "expected a criticalIntersection rule shape in the shared module");

  // Consumers: the page evaluates the override for real, and states the two
  // criteria it CANNOT evaluate rather than omitting them (an omitted
  // criterion reads as a criterion that passed).
  pin.match(page, /CriteriaCoverageCard/, "expected a §5.6.3 criteria-coverage card on the base-model page");
  pin.match(page, /evaluateCriticalIntersections\(/, "expected the base-model page to apply the §5.6.3.1 override");
  pin.match(page, /criticalNodeIdsInput/, "expected an analyst-supplied critical-intersection input");
  pin.match(page, /관측값 없음 — 평가 불가/, "expected the explicit not-assessable state for the unmeasurable criteria");
  pin.match(page, /critical 교차로 미지정/, "expected the page to say the override did not run when nothing is designated");
  pin.match(
    page,
    /const overallPass = countStats\.trafficPass && travelStats\.travelPass && critical\.passed !== false;/,
    "a failing critical intersection must flip the OVERALL verdict, not sit in a side panel",
  );
  pin.match(
    page,
    /if \(critical\.passed === false\) \{\s*\n\s*return "border-rose-200/,
    "expected the result panel to turn red on a critical-intersection failure even when both network legs pass",
  );

  // Table 5-12's tolerance widths are no longer re-hardcoded in the page's
  // flow-band summariser -- it reads the active profile.
  pin.match(page, /pass = diff <= profile\.flowLowToleranceVph;/, "expected the low flow band to read the profile");
  pin.match(page, /<= profile\.flowMidTolerancePct/, "expected the mid flow band to read the profile");
  pin.match(page, /pass = diff <= profile\.flowHighToleranceVph;/, "expected the high flow band to read the profile");

  // ja: pickByLanguage keys off the KOREAN string, so a two-argument t(ko, en)
  // with no entry renders English for Japanese users.
  for (const korean of [
    "Critical 교차로 (§5.6.3.1, p.213)",
    "신호시간 (Table 5-14, p.216)",
    "포화교통류율 (§5.6.3.5, p.216)",
    "평가 불가",
    "지정된 critical 교차로에 비교 가능한 이동류가 없어 관측값 없음 — 평가 불가.",
  ]) {
    pin.match(jaDictionary, literalPattern(`"${korean}":`), `criteria-coverage string has no Japanese entry: ${korean}`);
  }
});

// Real behaviour, not source pins. These three are where being wrong ships a
// wrong number to a client rather than a stale comment.

test("section-5: the TS profile literals match shared/mrwa-validation-criteria.json", () => {
  // ONE source of truth across runtimes: backend/services/validation_profiles.py
  // LOADS this file, and these literals are pinned to it. Same pattern as
  // shared/geh-conformance.json.
  const fixture = JSON.parse(
    readFileSync(path.join(repoRoot, "shared", "mrwa-validation-criteria.json"), "utf-8"),
  ) as {
    categories: Record<string, Record<string, number | string>>;
    flow_bands: Record<string, number>;
    travel_time: Record<string, number | string>;
    signal_timing: Record<string, number | string | null>;
    saturation_flow: Record<string, number>;
    critical_intersection: Record<string, number | boolean>;
  };

  assert.deepEqual(Object.keys(fixture.categories).sort(), ["cat1", "cat2", "cat3"]);
  for (const [key, raw] of Object.entries(fixture.categories)) {
    const profile = VALIDATION_PROFILES[key as keyof typeof VALIDATION_PROFILES];
    assert.ok(profile, `no TS profile for ${key}`);
    assert.equal(profile.nameEn, raw.name_en, `${key}: nameEn`);
    assert.equal(profile.nameKo, raw.name_ko, `${key}: nameKo`);
    assert.equal(profile.gehUnder5Pct, raw.geh_under_5_pct, `${key}: gehUnder5Pct`);
    assert.equal(profile.gehUnder10Pct, raw.geh_under_10_pct, `${key}: gehUnder10Pct`);
    assert.equal(profile.rSquaredMin, raw.r_squared_min, `${key}: rSquaredMin`);
    assert.equal(profile.flowLowWithinPct, raw.flow_low_within_pct, `${key}: flowLowWithinPct`);
    assert.equal(profile.flowMidWithinPct, raw.flow_mid_within_pct, `${key}: flowMidWithinPct`);
    assert.equal(profile.flowHighWithinPct, raw.flow_high_within_pct, `${key}: flowHighWithinPct`);
    assert.equal(profile.travelWithinPct, raw.travel_within_pct, `${key}: travelWithinPct`);

    // Category-invariant widths, carried per-profile so one object is a
    // complete criteria set.
    assert.equal(profile.flowLowToleranceVph, fixture.flow_bands.low_tolerance_vph, `${key}: flow low width`);
    assert.equal(profile.flowMidTolerancePct, fixture.flow_bands.mid_tolerance_pct, `${key}: flow mid width`);
    assert.equal(profile.flowHighToleranceVph, fixture.flow_bands.high_tolerance_vph, `${key}: flow high width`);
    assert.equal(profile.travelTolerancePct, fixture.travel_time.tolerance_pct, `${key}: travel width`);
    assert.equal(profile.travelToleranceMinMinutes, fixture.travel_time.tolerance_min_minutes, `${key}: travel floor`);
    assert.equal(profile.saturationFlowWithinPct, fixture.saturation_flow.within_pct, `${key}: saturation flow`);
    assert.equal(profile.criticalIntersectionGehMax, fixture.critical_intersection.geh_max, `${key}: critical GEH`);
    assert.equal(profile.signalTiming.cycleTime.seconds, fixture.signal_timing.cycle_time_seconds, `${key}: cycle s`);
    assert.equal(profile.signalTiming.cycleTime.pct, fixture.signal_timing.cycle_time_pct, `${key}: cycle %`);
    assert.equal(profile.signalTiming.greenTime.seconds, fixture.signal_timing.green_time_seconds, `${key}: green s`);
    assert.equal(profile.signalTiming.greenTime.pct, fixture.signal_timing.green_time_pct, `${key}: green %`);
    assert.equal(profile.signalTiming.actuatedCallFrequencyTolerance, null, `${key}: actuated tolerance`);
  }

  // The two directions are OPPOSITES and the fixture says so out loud.
  assert.equal(fixture.travel_time.direction, "greater");
  assert.equal(fixture.signal_timing.direction, "smaller");
  assert.equal(SATURATION_FLOW_WITHIN_PCT, fixture.saturation_flow.within_pct);
  assert.equal(CRITICAL_INTERSECTION_GEH_MAX, fixture.critical_intersection.geh_max);
});

test("section-5: Table 5-14 takes the SMALLER bound -- flipping it to max() must fail", () => {
  // The wrong direction, spelled out. Travel time (§5.6.3.2, p.215) is
  // "whichever is greater"; Table 5-14 (p.216) is "whichever is smaller". One
  // shared max() helper for both would silently widen every signal band.
  const greaterDirection = (observed: number, band: SignalTimingBand) =>
    Math.max(band.seconds, (observed * band.pct) / 100);

  const cycle = SIGNAL_TIMING_CRITERIA.cycleTime;
  const green = SIGNAL_TIMING_CRITERIA.greenTime;

  // Values chosen where smaller !== greater, in BOTH orderings.
  assert.equal(signalTimingToleranceSeconds(120, cycle), 3); // 5% of 120 = 6 s
  assert.equal(greaterDirection(120, cycle), 6);
  assert.equal(signalTimingToleranceSeconds(40, cycle), 2); // 5% of 40 = 2 s
  assert.equal(greaterDirection(40, cycle), 3);
  assert.equal(signalTimingToleranceSeconds(60, green), 3); // 10% of 60 = 6 s
  assert.equal(greaterDirection(60, green), 6);
  assert.equal(signalTimingToleranceSeconds(20, green), 2); // 10% of 20 = 2 s
  assert.equal(greaterDirection(20, green), 3);
  // Boundary: at 60 s the two directions agree, so only the cases above can
  // catch a flipped implementation.
  assert.equal(signalTimingToleranceSeconds(60, cycle), 3);
  assert.equal(greaterDirection(60, cycle), 3);

  // THE counterfactual: a 120 s cycle modelled at 125 s is 5 s out.
  //   SMALLER (correct): 3 s -> FAIL.   GREATER (wrong): 6 s -> PASS.
  assert.equal(signalTimingWithinTolerance(120, 125, cycle), false);
  assert.ok(Math.abs(125 - 120) <= greaterDirection(120, cycle), "the max() answer would have passed this");
  assert.equal(signalTimingWithinTolerance(60, 65, green), false);
  assert.ok(Math.abs(65 - 60) <= greaterDirection(60, green), "the max() answer would have passed this too");

  // A missing observation is not assessable -- never a pass, never a failure.
  assert.equal(signalTimingToleranceSeconds(null, cycle), null);
  assert.equal(signalTimingToleranceSeconds(Number.NaN, cycle), null);
  assert.equal(signalTimingToleranceSeconds(-1, cycle), null);
  assert.equal(signalTimingWithinTolerance(null, 100, cycle), null);
  assert.equal(signalTimingWithinTolerance(100, null, cycle), null);

  // §5.6.3.5: within 10% of observed, and not assessable when unmeasured.
  assert.equal(saturationFlowWithinTolerance(1800, 1980), true);
  assert.equal(saturationFlowWithinTolerance(1800, 1981), false);
  assert.equal(saturationFlowWithinTolerance(null, 1980), null);
  assert.equal(saturationFlowWithinTolerance(1800, null), null);
  assert.equal(saturationFlowWithinTolerance(0, 1980), null);
});

test("section-5: the critical-intersection override is load-bearing, not decorative", () => {
  // 19 of 20 movements under GEH 5 = 95%, which MEETS Category 1's
  // network-wide Table 5-12 requirement (gehUnder5Pct = 95).
  const nodes = [
    { nodeId: "9", nodeName: "Critical Cross", gehValues: [...Array(19).fill(1.0), 7.5] },
    { nodeId: "1", nodeName: "Clean", gehValues: [1.0, 2.0] },
  ];
  assert.equal(VALIDATION_PROFILES.cat1.gehUnder5Pct, 95);
  assert.ok((19 / 20) * 100 >= VALIDATION_PROFILES.cat1.gehUnder5Pct, "network-wide rate must PASS on this data");

  // Same data, no designation -> the check did not run. NEVER a pass.
  const undesignated = evaluateCriticalIntersections(nodes, []);
  assert.equal(undesignated.designated, false);
  assert.equal(undesignated.passed, null);
  assert.deepEqual(undesignated.nodes, []);

  // Same data, node 9 designated -> FAIL, and the node is named.
  const designated = evaluateCriticalIntersections(nodes, ["9"]);
  assert.equal(designated.passed, false);
  assert.deepEqual(designated.failingNodeIds, ["9"]);
  assert.equal(designated.nodes[0].nodeName, "Critical Cross");
  assert.equal(designated.nodes[0].worstGeh, 7.5);
  assert.equal(designated.nodes[0].failingCount, 1);

  // The override must not fail everything it touches.
  assert.equal(evaluateCriticalIntersections(nodes, ["1"]).passed, true);

  // "achieve GEH less than 5" -- 5.0 exactly is not less than 5, matching
  // gehGrade where 5.0 is already "fair".
  assert.equal(evaluateCriticalIntersections([{ nodeId: "1", gehValues: [4.999] }], ["1"]).passed, true);
  assert.equal(evaluateCriticalIntersections([{ nodeId: "1", gehValues: [5] }], ["1"]).passed, false);

  // Undefined GEH is excluded from BOTH sides -- never a free pass.
  const ghost = evaluateCriticalIntersections([{ nodeId: "3", gehValues: [null, null] }], ["3"]);
  assert.equal(ghost.passed, null);
  assert.equal(ghost.nodes[0].comparedCount, 0);
  assert.deepEqual(ghost.notAssessableNodeIds, ["3"]);

  // A designated id that is not in the data is disclosed, not swallowed.
  assert.deepEqual(evaluateCriticalIntersections(nodes, ["1", "42"]).missingIds, ["42"]);

  assert.deepEqual(parseCriticalNodeIds("12, 15 7"), ["12", "15", "7"]);
  assert.deepEqual(parseCriticalNodeIds("12,12; 7"), ["12", "7"]);
  assert.deepEqual(parseCriticalNodeIds("   "), []);
  assert.deepEqual(parseCriticalNodeIds(null), []);
});

contractTest("base model page gates scenario selection on an uploaded dataset", (pin) => {
  const page = read("app/base-model/page.tsx");

  pin.match(page, /fetchActiveDataset/, "expected base-model page to check whether a VISSIM dataset is uploaded");
  pin.match(page, /shouldLoadScenarioSelection \? fetchScenarios\(\) : Promise\.resolve<ScenarioMeta\[\]>\(\[\]\)/, "expected default dataset state to keep scenario dropdowns empty");
  pin.match(page, /disabled=\{scenarios\.length === 0\}/, "expected scenario dropdowns disabled until a dataset is uploaded");
  pin.match(page, /setPeriod\("AM"\)/, "expected dataset restore to reset the analysis-period dropdown");
});

contractTest("base model page renders the intersection-by-direction count matrix", (pin) => {
  const page = read("app/base-model/page.tsx");

  pin.match(page, /CountComparisonOverview/, "expected visual count comparison overview");
  pin.match(page, /CountNodeDirectionMatrixRow/, "expected node rows in the intersection-by-direction matrix");
  pin.match(page, /CountDirectionMatrixCell/, "expected cardinal-direction comparison cells");
  pin.match(page, /COUNT_DIRECTION_ORDER\.map/, "expected fixed north/east/south/west direction columns");
  pin.match(page, /grid-cols-\[190px_repeat\(4,minmax\(230px,1fr\)\)\]/, "expected stable intersection-by-direction matrix layout");
  pin.match(page, /buildCountDirectionGroups/, "expected count rows grouped into north/east/south/west approaches");
  pin.match(page, /ComparisonBarLine/, "expected observed-vs-model bar comparison");
  pin.match(page, /groupCountComparisonsByNode/, "expected count rows grouped by node");
  pin.doesNotMatch(page, /<table className="w-full text-sm">/, "expected old long count comparison table to be removed");
  pin.match(
    page,
    /Please check the travel time measurement sections defined in VISSIM are consistent with the observed delay/,
    "expected Vissim travel-time measurement warning message",
  );
});

contractTest("base model page labels follow the language toggle", (pin) => {
  const page = read("app/base-model/page.tsx");

  pin.match(page, /t\("노드", "Node"\)/, "expected node label to follow the language toggle");
  pin.match(page, /t\("루트", "Route"\)/, "expected route label to follow the language toggle");
  pin.match(page, /t\("통과", "PASS"\)/, "expected pass badge to follow the language toggle");
  pin.match(page, /t\("확인", "CHECK"\)/, "expected check badge to follow the language toggle");
});

contractTest("validation workbook API bindings stay wired", (pin) => {
  const api = read("lib/api.ts");

  pin.match(api, /fetchBaseWorkbook/, "expected workbook API binding");
  pin.match(api, /uploadBaseWorkbook/, "expected workbook upload API binding");
  pin.match(api, /resetBaseWorkbook/, "expected workbook reset API binding");
  pin.match(api, /method: "DELETE"/, "expected workbook reset to delete only validation workbook data");
  pin.match(api, /traveltime_interval_class/, "expected traveltime interval class typing");
});

contractTest("dataset banner copy follows the language toggle", (pin) => {
  const datasetBanner = read("components/VissimDatasetBanner.tsx");

  pin.match(datasetBanner, /모델 선택/, "expected dataset card title in Korean mode");
  pin.match(datasetBanner, /"Model"/, "expected dataset card title in English mode");
  pin.match(datasetBanner, /파일\/ZIP 업로드/, "expected dataset upload button in Korean mode");
  pin.match(datasetBanner, /Load File \/ ZIP/, "expected dataset upload button in English mode");
  pin.match(datasetBanner, /기본값 복원/, "expected dataset reset button in Korean mode");
  pin.match(datasetBanner, /Restore Default/, "expected dataset reset button in English mode");
  pin.doesNotMatch(datasetBanner, /Vissim dataset/, "expected old mixed-case dataset label to be removed");
  pin.doesNotMatch(datasetBanner, /Load file \/ zip/, "expected old mixed-case upload label to be removed");
});

// Style pins, quarantined for the same reason as the base-model layout test above.
contractTest("dataset banner controls keep the shared button styling", (pin) => {
  const datasetBanner = read("components/VissimDatasetBanner.tsx");

  pin.match(datasetBanner, /ui-button-primary inline-flex items-center px-3 py-2 text-xs/, "expected dataset upload button to match the Excel upload style");
  // Re-pinned 2026-08-05. This used to expect `ui-button-danger`, matching
  // base-model's Excel reset. The Console retheme (77e9149) deliberately made
  // Restore Default a ghost button while the Excel reset stayed danger, so the
  // two no longer match by design -- the destructive warning moved into
  // ConfirmDialog rather than being carried by the button colour.
  pin.match(datasetBanner, /ui-button-ghost inline-flex items-center px-3 py-2 text-xs/, "expected dataset reset button to use the ghost style, with the destructive warning carried by ConfirmDialog");
  // The override hooks stay in the component even though base-model now sizes
  // the card with `h-full` instead of passing bg/shadow overrides.
  pin.match(datasetBanner, /hasCustomBackground/, "expected dataset card to allow the base-model gray background override");
  pin.match(datasetBanner, /hasCustomShadow/, "expected dataset card to allow the base-model shadow override");
});

contractTest("intersection comparison is merged into the main dashboard", (pin) => {
  const page = read("app/dashboard/page.tsx");
  const proxy = read("proxy.ts");

  pin.match(page, /selectedNodeIds/, "expected multi-node comparison state on the main dashboard");
  pin.match(page, /vissim:page-context/, "expected dashboard to publish assistant page context");
  pin.match(page, /selectedScenarioIds: selected/, "expected selected dashboard scenarios to be passed to floating assistant context");
  pin.match(page, /selectedNodeIds,/, "expected selected dashboard nodes to be passed to floating assistant context");
  pin.match(page, /IntersectionPerformanceGrid/, "expected intersection performance cards on the main dashboard");
  pin.match(page, /DirectionDelayChart/, "expected cardinal-direction delay bars in intersection performance");
  pin.match(page, /flex h-\[520px\] min-h-0 flex-col overflow-hidden/, "expected selected-intersection panel to contain its own scroll area");
  pin.match(page, /mt-4 min-h-0 flex-1 overflow-hidden border-t/, "expected node detail panel to shrink within the card");
  pin.doesNotMatch(page, /h-\[calc\(100%-112px\)\]/, "expected fixed selected-panel height calculation to be removed");
  pin.match(page, /LOS_COLORS/, "expected delay bars to use LOS colors");
  pin.match(page, /IntersectionMetricBars/, "expected horizontal intersection metric bars");
  pin.match(page, /VolumeByIntersectionChart/, "expected volume composition chart by selected intersection");
  pin.match(page, /QLENMAX_MEAN/, "expected queue charts to consume aggregated VISSIM queue metrics");
  pin.match(page, /useSectionLabels/, "expected travel-time charts to use section labels when intervals collapse");
  // Travel-time chart must plot the real measured values, not a synthesized curve.
  pin.doesNotMatch(page, /stabilizeTravelTimeProfile/, "expected the fabricated travel-time easing to be removed");
  pin.doesNotMatch(page, /Math\.pow\(progress, 1\.18\)/, "expected no synthetic accelerated display profile on travel time");
  pin.match(page, /SectionJumpIndex/, "expected section shortcut index");
  pin.match(page, /xl:grid-cols-\[minmax\(0,1fr\)_460px\]/, "expected widened map/intersection grid (Console-density pass)");
  pin.doesNotMatch(
    page,
    /xl:grid-cols-\[148px_minmax\(0,1fr\)\]/,
    "expected shortcut tab not to reserve a layout column",
  );
  pin.match(
    page,
    /className="group absolute left-0 top-24 z-\[950\] h-11 w-10 -translate-x-3"/,
    "expected shortcut index root to reserve only a slim handle",
  );
  pin.match(
    page,
    /pointer-events-none absolute right-\[42px\] top-0 w-\[188px\][^"]*group-hover:pointer-events-auto[^"]*group-hover:opacity-100/,
    "expected shortcut panel to appear only on hover or focus",
  );
  pin.match(page, /aria-label=\{t\("분석 목차 열기", "Open analysis sections"\)\}/, "expected accessible side tab handle");
  pin.match(page, /id="intersection-performance"/, "expected intersection performance anchor");
  pin.match(page, /id="travel-time-comparison"/, "expected travel time anchor");
  pin.match(page, /id="network-kpi-comparison"/, "expected network KPI anchor");
  pin.match(page, /id="overall-results-analysis"/, "expected overall results anchor");
  pin.match(page, /TravelTimeComparison/, "expected travel-time comparison on the main dashboard");
  pin.match(page, /OverallResultsSection/, "expected overall result analysis on the main dashboard");
  pin.match(page, /결과 해석과 보고서 생성만 간단하게 제공합니다/, "expected simplified overall analysis copy");
  pin.match(page, /xl:grid-cols-\[minmax\(0,1fr\)_360px\]/, "expected simplified interpretation/report layout");
  pin.doesNotMatch(page, /t\("시나리오 순위"/, "expected scenario ranking UI to be removed from overall analysis");
  pin.doesNotMatch(page, /t\("주요 인사이트"/, "expected key insight UI to be removed from overall analysis");
  pin.match(page, /intersectionCards=\{intersectionCards\}/, "expected overall analysis to receive selected intersection evidence");
  pin.match(page, /buildCalibrationReportMarkdown/, "expected report markdown generator in overall analysis");
  pin.match(page, /handleDownloadWordReport/, "expected Word report download handler");
  pin.match(page, /base-model-calibration-validation-\$\{stamp\}\.doc/, "expected generated Word report filename");
  pin.match(proxy, /matcher: "\/intersections\/:path\*"/, "expected old intersections route to redirect");
  pin.match(proxy, /NextResponse\.redirect\(new URL\("\/dashboard", request\.url\)\)/, "expected redirect to the merged dashboard");
});

contractTest("dashboard map signalized intersection uses traffic light artwork", (pin) => {
  const map = read("components/DashboardMap.tsx");

  pin.match(map, /function buildSignalSignSvg/, "expected dedicated signal intersection icon");
  pin.match(map, /<rect x="20" y="3" width="24" height="58" rx="12"/, "expected vertical traffic light body");
  pin.match(map, /fill="#f3161d"/, "expected red signal lamp");
  pin.match(map, /fill="#ffd719"/, "expected yellow signal lamp");
  pin.match(map, /fill="#4aa72f"/, "expected green signal lamp");
});

contractTest("dataset mutations prefer the same-origin API proxy", (pin) => {
  const api = read("lib/api.ts");
  const nextConfig = read("next.config.ts");

  pin.match(api, /async function apiMutationFetch/, "expected shared mutation fetch helper");
  pin.match(api, /async function apiDirectMutationFetch/, "expected long-running assistant requests to bypass the proxy timeout");
  pin.match(api, /function listDirectMutationApiBases/, "expected direct assistant requests to use a proxy-free base list");
  pin.match(api, /isLikelyFrontendApiBase/, "expected stored frontend origins to be skipped for direct assistant requests");
  pin.match(api, /fetch\(normalizedPath, sameOriginRequestInit\(init\)\)/, "expected same-origin API proxy attempt first");
  pin.match(api, /askAssistant[\s\S]*apiDirectMutationFetch\("\/api\/assistant\/query"/, "expected assistant query to call the backend API directly");
  pin.match(api, /response_language\?: "ko" \| "en"/, "expected assistant requests to carry the active UI language");
  pin.match(api, /"settings" \| "capabilities"/, "expected assistant API typing to include capability/help questions");
  pin.match(api, /uploadInpxDataset[\s\S]*apiMutationFetch\(path/, "expected dataset upload to use API proxy");
  pin.match(api, /resetActiveDataset[\s\S]*apiMutationFetch\("\/api\/datasets\/reset"/, "expected dataset reset to use API proxy");
  pin.match(nextConfig, /proxyClientMaxBodySize/, "expected API proxy upload body limit override for VISSIM zip datasets");
  pin.match(nextConfig, /250 \* 1024 \* 1024/, "expected VISSIM zip upload proxy limit above the 10MB Next default");
  pin.match(nextConfig, /API_PROXY_MAX_BODY_SIZE_BYTES/, "expected configurable API proxy upload limit");
});

contractTest("node direction table keeps all three comparison view modes", (pin) => {
  const nodeTable = read("components/NodeDirectionTable.tsx");

  pin.match(nodeTable, /\{ key: "matrix", label:/, "expected matrix mode option");
  pin.match(nodeTable, /\{ key: "intersection", label:/, "expected intersection mode option");
  pin.match(nodeTable, /\{ key: "bars", label:/, "expected bars mode option");
});

contractTest("selected intersection panel omits scenario total volume chart", (pin) => {
  const nodeFlowPanel = read("components/NodeFlowPanel.tsx");

  pin.doesNotMatch(nodeFlowPanel, /Total Volume by Scenario/, "expected scenario total volume chart to be removed");
  pin.doesNotMatch(nodeFlowPanel, /function ScenarioVolumeBars/, "expected compact volume chart component to be removed");
  pin.doesNotMatch(
    nodeFlowPanel,
    /scenarioTotals/,
    "expected selected-intersection panel to avoid scenario total volume calculations",
  );
});

contractTest("network preview page publishes assistant page context", (pin) => {
  const page = read("app/network/page.tsx");

  pin.match(page, /vissim:page-context/, "expected network preview page to publish assistant page context");
  pin.match(
    page,
    /vissim:assistant-context-request/,
    "expected network preview page to republish context when the floating assistant asks for it",
  );
  pin.match(page, /route: "\/network"/, "expected network preview page context to identify its own route");
  // Multi-select (v2 UX unit, 2026-07-29): diagram clicks publish the FULL
  // selection as STRUCTURED lists, not only a prose note — these two keys
  // are what FloatingAssistant turns into selected_link_ids /
  // selected_element(s) on the request.
  pin.match(
    page,
    /selectedLinks,\s*selectedElements,/,
    "expected the network preview page to publish the full link/element selection as structured context",
  );
});

contractTest("P6/P7: diagram selection reaches the assistant request AND the agent turn, not just a chip", (pin) => {
  const networkPage = read("app/network/page.tsx");
  const dashboardPage = read("app/dashboard/page.tsx");
  const networkViewer = read("components/NetworkViewer.tsx");
  const floatingAssistant = read("components/FloatingAssistant.tsx");
  const api = read("lib/api.ts");

  // Element glyphs are clickable, guarded by the same click-vs-drag check the
  // link/node hit targets use, so a pan that ends over a glyph is not a click.
  pin.match(
    networkViewer,
    /onElementClick\?: \(ref: NetworkElementRef, additive: boolean\) => void/,
    "expected the viewer to expose element clicks with the shift-click additive flag",
  );
  pin.match(
    networkViewer,
    /sel\.style\("cursor", "pointer"\)\.on\("click", \(event: MouseEvent\) => \{\s*if \(isDragClick\(event\)\) return;\s*onElementClick\(ref, event\.shiftKey\);/,
    "expected element clicks to reuse the drag guard before publishing a reference",
  );

  for (const [label, page] of [["network preview", networkPage], ["dashboard", dashboardPage]] as const) {
    pin.match(page, /onElementClick=\{handleElementClick\}/, `expected ${label} to wire element clicks`);
    pin.match(page, /selectedLinks,\s*selectedElements,/, `expected ${label} to publish the full multi-select link/element lists`);
  }

  pin.match(
    floatingAssistant,
    /selected_link_ids: activePageContext\?\.selectedLinks\?\.map\(\(link\) => link\.id\) \?\? \[\]/,
    "expected the floating assistant to send clicked links to the assistant API",
  );
  // Multi-select (v2 UX unit, 2026-07-29): the singular/plural HTTP-field
  // split for elements is centralized in selectionFieldsForRequest (exactly
  // one selected element -> legacy `selected_element`, two or more ->
  // `selected_elements`), shared by both the plain-assistant and agent-mode
  // request builders below.
  pin.match(
    floatingAssistant,
    /function selectionFieldsForRequest\(/,
    "expected a single shared singular/plural element-field split for outgoing requests",
  );
  pin.match(
    floatingAssistant,
    /\.\.\.selectionFieldsForRequest\(activePageContext\)/,
    "expected the floating assistant to send the clicked element(s) to the assistant API via the shared split",
  );
  pin.match(api, /selected_link_ids\?: string\[\]/, "expected the assistant request type to carry clicked link ids");
  pin.match(api, /selected_element\?: \{/, "expected the assistant request type to carry the clicked element");
  pin.match(api, /selected_elements\?: \{/, "expected the assistant request type to carry the multi-select element list");

  // P7: agent mode sends the SAME selection (plus node/scenario ids, which had
  // never reached the agent at all) on its own streamAgentQuery payload — the
  // agent-mode branch is a separate request builder, so the P6 assertions
  // above pass whether or not it was wired.
  pin.match(
    floatingAssistant,
    /session_id: agentSessionId \?\? undefined,[\s\S]*?selected_node_ids: activePageContext\?\.selectedNodeIds \?\? \[\],\s*selected_link_ids: activePageContext\?\.selectedLinks\?\.map\(\(link\) => link\.id\) \?\? \[\],[\s\S]*?\.\.\.selectionFieldsForRequest\(activePageContext\),/,
    "expected agent mode to send the on-screen selection with the agent turn",
  );
  pin.match(
    api,
    /export interface AgentQueryRequest \{[\s\S]*?selected_link_ids\?: string\[\];/,
    "expected the agent request type to carry the on-screen selection",
  );
});

contractTest("P2: agent session id is broadcast and consumed for the edited-network view", (pin) => {
  const floatingAssistant = read("components/FloatingAssistant.tsx");
  const networkPage = read("app/network/page.tsx");

  pin.match(
    floatingAssistant,
    /window\.dispatchEvent\(new CustomEvent\("vissim:agent-session", \{ detail: \{ sessionId: agentSessionId \} \}\)\)/,
    "expected the floating assistant to broadcast its current agent session id whenever it changes",
  );
  pin.match(
    networkPage,
    /window\.addEventListener\("vissim:agent-session", handleAgentSession\)/,
    "expected the network preview page to listen for the broadcast agent session id",
  );
});

contractTest("unit 1.5: unmeasured per-approach delay is rejected BEFORE numeric coercion", (pin) => {
  const dashboardPage = read("app/dashboard/page.tsx");

  // `toFiniteNumber` is `Number(value)` + `Number.isFinite`, and
  // `Number(null) === 0` is finite. The backend sends explicit JSON nulls for
  // every unmeasured approach (`_build_node_directions` ->
  // `_normalize_nullable_direction_map`), so coercing first turned "not
  // measured" into four bars reading 0초/대 at LOS A -- a fabricated
  // measurement, and it made the 데이터 없음 panel unreachable.
  pin.doesNotMatch(
    dashboardPage,
    /const value = toFiniteNumber\(measured\[direction\]\);/,
    "per-approach delay must not coerce a JSON null into a measured 0 -- test for null first",
  );
  pin.match(
    dashboardPage,
    /const raw = measured\[direction\];\s*\n\s*const value = raw == null \? null : toFiniteNumber\(raw\);/,
    "expected the null/undefined guard to sit BEFORE the numeric coercion",
  );
  // The two states this guard makes reachable must both still exist.
  pin.match(
    dashboardPage,
    /const hasMeasured = values\.some\(\(item\) => item\.value != null\);/,
    "expected the chart's no-measurement branch to key off a null-preserving value",
  );
  pin.match(dashboardPage, /데이터 없음/, "expected the explicit no-data panel for an unmeasured node");
});

// --- unit U9: login UI, session state, and the approval card's 401 path ----

contractTest("U9: auth requests are structurally incapable of carrying X-Workspace-Id", (pin) => {
  const api = read("lib/api.ts");
  // U7 measured this and wrote it into backend/routers/auth.py's docstring: an
  // ABSENT X-Workspace-Id resolves to the default workspace, but a header that
  // is PRESENT and names an unknown workspace 404s inside
  // main.WorkspaceScopeMiddleware BEFORE routing -- so a browser holding a
  // stale workspace id would get a workspace error instead of a login screen.
  // The guarantee therefore has to be structural: the auth calls use their own
  // init builder, and that builder deletes the header rather than trusting
  // that no caller ever sets it.
  const authFetchBody = sliceBetween(api, "async function authFetch(", "/** Exchange credentials");
  const authBindings = sliceBetween(api, "export async function login(", "export async function uploadInpxDataset(");

  pin.match(authFetchBody, /\S/, "expected an authFetch helper in lib/api.ts (markers moved?)");
  pin.match(authBindings, /\S/, "expected the login/logout/whoami bindings in lib/api.ts (markers moved?)");

  pin.match(
    api,
    /function noWorkspaceRequestInit\(init\?: RequestInit\): RequestInit \{[\s\S]*?headers\.delete\(WORKSPACE_HEADER\);/,
    "expected the auth init builder to DELETE the workspace header, not merely omit it",
  );
  pin.match(authFetchBody, /noWorkspaceRequestInit\(init\)/, "expected authFetch's same-origin leg to use the workspace-free init");
  pin.match(
    authFetchBody,
    /noWorkspaceRequestInitForBase\(base, init\)/,
    "expected authFetch's direct-base leg to use the workspace-free init too",
  );
  pin.doesNotMatch(
    authFetchBody,
    /withWorkspaceHeader|sameOriginRequestInit|requestInitForBase\(/,
    "authFetch must not reach any init helper that injects X-Workspace-Id",
  );
  // Cookies: same-origin fetch sends them by default, and the direct
  // cross-origin leg needs them stated. Neither may be turned off.
  pin.match(api, /credentials: "include"/, "expected auth requests to send the session cookie");
  pin.doesNotMatch(api, /credentials: "omit"/, "no request in this client may opt out of cookies");

  pin.match(authBindings, /authFetch\("\/api\/auth\/login"/, "expected login() to POST through authFetch");
  pin.match(authBindings, /authFetch\("\/api\/auth\/logout"/, "expected logout() to go through authFetch");
  pin.match(authBindings, /authFetch\("\/api\/auth\/whoami"/, "expected whoami() to go through authFetch");
  pin.match(authBindings, /method: "POST"/, "expected login to be a POST, never a GET with credentials in the URL");
  pin.match(
    authBindings,
    /if \(r\.status === 401\) return null;/,
    "expected whoami() to treat 401 as the anonymous ANSWER, not an error",
  );
});

contractTest("U9: the login page posts real credentials and validates its own redirect", (pin) => {
  const page = read("app/login/page.tsx");
  const layout = read("app/layout.tsx");
  const provider = read("components/AuthProvider.tsx");

  pin.match(page, /import \{ safeNextPath \} from "@\/lib\/authRedirect";/, "expected the login page to validate `next` with the shared guard");
  pin.match(page, /const nextPath = safeNextPath\(searchParams\.get\("next"\)\);/, "expected `next` to be validated before it can reach the router");
  pin.doesNotMatch(page, /router\.replace\(searchParams/, "the raw query parameter must never reach router.replace");
  pin.match(page, /router\.replace\(nextPath\)/, "expected a successful login to return the user to the validated path");
  pin.match(page, /<Suspense fallback=\{null\}>/, "expected the useSearchParams tree to sit under a Suspense boundary (Next 16)");
  pin.match(page, /await login\(username\.trim\(\), password, lang\)/, "expected the login submit to call the session provider's login");
  pin.match(page, /autoComplete="current-password"/, "expected a real password field");
  // One message for wrong password, unknown username and disabled account --
  // the backend refuses to distinguish them, and neither may this page.
  pin.match(page, /kind: "credentials"/, "expected a single bad-credentials state");
  pin.match(page, /kind: "network"/, "expected an unreachable-API state distinct from bad credentials");
  pin.doesNotMatch(page, /No such user|Unknown username/, "the login page must not invent an account-enumeration oracle");

  pin.match(provider, /whoami\(\)/, "expected session state to be populated from whoami()");
  // Storage USE, not the word: the provider's own docstring explains why it
  // stores nothing, and that sentence must not be what makes this pin pass.
  pin.doesNotMatch(
    provider,
    /(?:local|session)Storage\.(?:get|set|remove)Item/,
    "the session cookie is HttpOnly -- no token may be mirrored into JS storage",
  );
  pin.match(layout, /<AuthProvider>/, "expected the session provider to wrap the app shell");
});

contractTest("U9: the login page's Korean strings all exist in the ja dictionary", (pin) => {
  // pickByLanguage keys ja off the KOREAN string (lib/i18n.ts), so a
  // two-argument t(ko, en) with no dictionary entry silently renders English
  // for Japanese users. The login page uses the two-argument form throughout,
  // which makes this mapping load-bearing rather than decorative.
  const page = read("app/login/page.tsx");
  const jaDictionary = read("lib/i18n-ja.ts");
  const twoArgCalls = [...page.matchAll(/\bt\(\s*"((?:[^"\\]|\\.)+)"\s*,\s*"(?:[^"\\]|\\.)*"\s*,?\s*\)/g)];
  const koreanStrings = Array.from(new Set(twoArgCalls.map((match) => match[1])));

  pin.match(
    `count=${koreanStrings.length}`,
    /count=(1[0-9]|[2-9]\d)/,
    "expected at least 10 two-argument t() strings on the login page -- a smaller number means this extraction went stale, not that the page shrank",
  );
  for (const korean of koreanStrings) {
    pin.match(
      jaDictionary,
      literalPattern(`"${korean}":`),
      `login page string has no Japanese entry in lib/i18n-ja.ts: ${korean}`,
    );
  }
});

contractTest("U9: the approval card reacts to 401/403 from the server, never to a guessed role", (pin) => {
  const api = read("lib/api.ts");
  const floatingAssistant = read("components/FloatingAssistant.tsx");
  const shell = read("components/AppShell.tsx");

  pin.match(
    api,
    /const APPROVAL_STATUSES_WITH_OWN_UI = \[401, 403, 404\];/,
    "expected 401/403 to reach the UI as AgentApprovalError, like the existing 404",
  );
  pin.match(
    floatingAssistant,
    /const approvalStatus = caught instanceof AgentApprovalError \? caught\.status : null;/,
    "expected the approve failure path to branch on the server's status",
  );
  pin.match(
    floatingAssistant,
    /approvalStatus === 401[\s\S]{0,400}?로그인이 필요합니다/,
    "expected a 401 from approve/decline to say a sign-in is needed",
  );
  pin.match(
    floatingAssistant,
    /errorLoginHref: approvalStatus === 401 \? loginHref\(pathname\) : undefined/,
    "expected the 401 state to carry a /login link back to the current page",
  );
  pin.match(
    floatingAssistant,
    /approvalStatus === 403[\s\S]{0,200}?caught\.message/,
    "expected the 403 detail (which names the roles) to be surfaced verbatim, not rewritten client-side",
  );
  pin.match(
    floatingAssistant,
    /href=\{approval\.errorLoginHref\}/,
    "expected the approval card to render the sign-in link instead of a dead error",
  );
  // The server is the authority on who may approve: no role check may gate the
  // buttons client-side.
  pin.doesNotMatch(
    floatingAssistant,
    /user\?\.role|role === "reviewer"|isReviewer/,
    "the approval buttons must never be hidden or disabled from a client-side role guess",
  );

  pin.match(shell, /<AccountAffordance \/>/, "expected the signed-in identity to appear in the shared shell");
  pin.match(shell, /loginHref\(pathname\)/, "expected the anonymous shell affordance to link to /login with a validated next");
  pin.match(shell, /roleLabel\(user\.role, t\)/, "expected the shell to show the current role alongside the name");
  pin.match(shell, /if \(status === "loading"\) return null;/, "expected the shell not to flash a sign-in link before whoami() answers");
});

// ---------------------------------------------------------------------------
// Unit P④ — user↔project membership and the admin screen
// ---------------------------------------------------------------------------

contractTest("P4: the admin API is structurally unable to carry a project id", (pin) => {
  // The backend puts every non-default project behind membership
  // (backend/services/membership.py), so a browser holding a stale or
  // forbidden `vissim-workspace-id` in localStorage gets a 404/403 from
  // main.WorkspaceScopeMiddleware BEFORE routing. The admin screen is the ONLY
  // place project access can be repaired -- if its own calls carried that
  // header, the page for fixing broken project access would be unreachable to
  // exactly the people whose project access is broken.
  //
  // So it is structural, not conventional: the whole admin block goes through
  // `authFetch`, the one path that DELETES the header (pinned above), via a
  // single `adminRequest` helper.
  const api = read("lib/api.ts");
  const marker = "async function adminRequest";
  const adminSlice = api.includes(marker) ? api.slice(api.indexOf(marker)) : "";

  pin.match(adminSlice, /\S/, "expected the admin bindings in lib/api.ts (marker moved?)");
  pin.match(adminSlice, /authFetch\(path, \{ cache: "no-store"/, "expected adminRequest to go through authFetch");
  pin.doesNotMatch(
    adminSlice,
    /apiFetch\(|apiMutationFetch\(|apiDirectMutationFetch\(/,
    "no admin binding may use a fetch path that injects X-Workspace-Id",
  );
  // Every admin binding routes through the one helper, so a new endpoint
  // cannot pick a different transport by accident.
  for (const fn of [
    "fetchAdminUsers",
    "fetchAdminWorkspaces",
    "createAdminUser",
    "setAdminUserPassword",
    "setAdminUserDisabled",
    "setAdminUserRole",
    "fetchWorkspaceMembers",
    "grantWorkspaceMember",
    "revokeWorkspaceMember",
  ]) {
    pin.match(adminSlice, new RegExp(`export async function ${fn}\\b`), `expected an admin binding named ${fn}`);
  }
  pin.match(
    adminSlice,
    /throw new AuthRequestError\(await readApiError\(r\), r\.status\)/,
    "expected admin failures to carry the HTTP status so 401 and 403 stay distinguishable",
  );
});

contractTest("P4: the admin page defers to the server and never renders a broken shell", (pin) => {
  const page = read("app/admin/page.tsx");
  const shell = read("components/AppShell.tsx");

  // 401 and 403 are different facts. 401 -> the login screen with `next` set
  // back here; 403 -> an explicit refusal, because auto-routing a signed-in
  // non-admin to /login lands on "이미 로그인되어 있습니다" and a Continue
  // button back to the same refusal, which is a loop rather than a destination.
  pin.match(
    page,
    /caught\.status === 401[\s\S]{0,160}?router\.replace\(loginHref\("\/admin"\)\)/,
    "expected a 401 to route to /login with next=/admin",
  );
  pin.match(
    page,
    /caught\.status === 403[\s\S]{0,160}?setAccess\("forbidden"\)/,
    "expected a 403 to become an explicit refusal state",
  );
  pin.match(page, /access === "forbidden"/, "expected the refusal state to have its own screen");
  pin.match(page, /href=\{loginHref\("\/admin"\)\}/, "expected the refusal screen to offer a sign-in link");
  pin.match(
    page,
    /if \(status === "loading" \|\| access === "loading"\)/,
    "expected the page not to flash a screen before whoami() and the first load settle",
  );

  // The server decides. The page may not skip the request because the cached
  // role looks wrong -- that would show an empty console to a user whose role
  // changed since the last whoami().
  pin.doesNotMatch(page, /user\?\.role === "admin"/, "the admin page must ask the server, not gate on the cached role");
  // No generated password anywhere: the operator types it (services/auth.py).
  pin.doesNotMatch(
    page,
    /Math\.random|crypto\.getRandomValues|generatePassword/,
    "the admin screen must never invent a credential",
  );
  pin.match(page, /autoComplete="new-password"/, "expected real password fields on the create/reset forms");

  // The shell link IS gated on the cached role, and that is fine: it hides a
  // menu item, it does not gate a capability.
  pin.match(shell, /user\?\.role === "admin"/, "expected the admin nav link to be shown only to admins");
  pin.match(shell, /href: "\/admin"/, "expected the shell to link to the admin screen");
});

contractTest("P4: every admin-page string carries its own Japanese", (pin) => {
  // pickByLanguage keys ja off the KOREAN string (lib/i18n.ts), so a
  // two-argument t(ko, en) with no dictionary entry silently renders English
  // for Japanese users -- the same hazard the login page's pin above guards.
  // This page answers it differently: it uses the THREE-argument form
  // everywhere, so there is no dictionary to keep in sync at all. The pin is
  // therefore "no two-argument call exists", which needs no dictionary lookup.
  const page = read("app/admin/page.tsx");
  const twoArgCalls = [...page.matchAll(/\bt\(\s*"((?:[^"\\]|\\.)+)"\s*,\s*"(?:[^"\\]|\\.)*"\s*,?\s*\)/g)];
  const localizedCalls = [...page.matchAll(/\bt\(\s*"/g)];

  pin.match(
    `count=${twoArgCalls.length}`,
    /^count=0$/,
    `the admin page must use t(ko, en, ja) everywhere; two-argument calls found: ${twoArgCalls
      .map((match) => match[1])
      .join(" | ")}`,
  );
  pin.match(
    `count=${localizedCalls.length}`,
    /count=([2-9]\d|\d{3,})/,
    "expected at least 20 localized strings on the admin page -- fewer means this extraction went stale, not that the page shrank",
  );
});

// ---------------------------------------------------------------------------
// Roadmap row 2.4 — network diff view
// ---------------------------------------------------------------------------

contractTest("2.4: the diff view reads ONE endpoint, and the backend guards it", (pin) => {
  const api = read("lib/api.ts");
  const page = read("app/network-diff/page.tsx");
  const shell = read("components/AppShell.tsx");
  // The backend half of the wiring, read across the repo root exactly the way
  // the shared/mrwa-validation-criteria.json pin above does. The endpoint path
  // and the ownership call are the two facts this page cannot check from its
  // own side, and both are load-bearing: without the second, a diff of another
  // project's network would render perfectly.
  const backendRouter = readFileSync(
    path.join(repoRoot, "backend", "routers", "network_diff.py"),
    "utf-8",
  );
  const backendMain = readFileSync(path.join(repoRoot, "backend", "main.py"), "utf-8");

  // --- endpoint path -------------------------------------------------------
  pin.match(
    api,
    /apiFetch\(`\/api\/network\/diff\?\$\{params\.toString\(\)\}`/,
    "expected the diff binding to call GET /api/network/diff",
  );
  pin.match(
    api,
    /new URLSearchParams\(\{ session_id: sessionId, base, target \}\)/,
    "expected session_id + base + target to be the request's whole parameter surface",
  );
  pin.match(backendRouter, /prefix="\/api\/network\/diff"/, "expected the backend router to claim that exact path");
  pin.match(backendMain, /app\.include_router\(network_diff\.router\)/, "expected the diff router to be registered");
  // The URL lives in lib/api.ts alone -- a page that issued its own request
  // would drift from the binding's NetworkDiffError handling on the next
  // change. (The path appears in the page's header COMMENT, which is why this
  // pins the request machinery rather than the string.)
  pin.doesNotMatch(page, /\bapiFetch\(|\bfetch\(/, "the page must go through fetchNetworkDiff, never issue its own request");
  pin.match(page, /fetchNetworkDiff\(sessionId, baseToken, targetToken\)/, "expected the page to call the shared binding");

  // --- the picker reuses the EXISTING artifact enumeration ------------------
  pin.match(page, /fetchAgentDownloads\(\{ limit: 50 \}\)/, "expected the session list to come from the existing unscoped download listing");
  pin.match(page, /fetchAgentDownloads\(\{ sessionId \}\)/, "expected the version list to come from the existing per-session download listing");

  // --- ownership -----------------------------------------------------------
  pin.match(
    backendRouter,
    /from services\.audit_trail import resolve_session_dir/,
    "expected the diff router to resolve sessions through the shared ownership-checking helper",
  );
  pin.match(
    backendRouter,
    /session_dir = resolve_session_dir\(session_id\)\n\s+if session_dir is None:\n\s+#[\s\S]{0,200}?raise HTTPException\(status_code=404, detail=_NO_SESSION_DETAIL\)/,
    "expected a foreign/unknown session to 404 through one branch, before any version is resolved",
  );
  pin.match(
    backendRouter,
    /_NO_SESSION_DETAIL = "No matching session\."/,
    "expected ONE detail string, so a foreign session cannot be told apart from a missing one",
  );

  // --- read-only + mtime-keyed cache ---------------------------------------
  pin.match(
    backendRouter,
    /_cache: dict\[tuple\[str, float, str, float\], dict\[str, Any\]\] = \{\}/,
    "expected the diff cache to be keyed on BOTH paths and BOTH mtimes",
  );
  pin.doesNotMatch(
    backendRouter,
    /\.write_text\(|\.write_bytes\(|shutil\.|os\.remove|os\.replace|\.mkdir\(|\.unlink\(/,
    "viewing a diff must never write, move or delete anything",
  );

  // --- the 404 UX, mirroring the approval card's status branching ----------
  pin.match(page, /caught instanceof NetworkDiffError \? caught\.status : null/, "expected the page to branch on the server's status, not on message text");
  pin.match(page, /diffError\.status === 404[\s\S]{0,600}?다른 프로젝트의 세션/, "expected the 404 state to say it means EITHER missing OR another project's");
  pin.match(page, /diffError\.status === 401[\s\S]{0,300}?loginHref\(pathname\)/, "expected a 401 to offer a sign-in link back to this page");

  // --- honest empty states -------------------------------------------------
  pin.match(page, /!diff\.refused && !diff\.has_changes[\s\S]{0,400}?차이 없음/, "expected an explicit 차이 없음 state rather than a blank page");
  pin.match(page, /result\.identical_sources[\s\S]{0,300}?같은 파일을 가리킵니다/, "expected same-file selections to say WHY the diff is empty");
  pin.match(
    page,
    /diff\.refused &&[\s\S]{0,900}?변경 목록이 비어 있는 것이 아니라, 아예 비교되지 않았습니다/,
    "expected a refusal to be shown as 'no comparison happened', never as an empty diff",
  );
  pin.match(page, /versions\.length === 0 && !versionsLoading[\s\S]{0,300}?저장된 편집 버전이 없습니다/, "expected a session with no saved edit versions to say so");

  // --- POSITION: a count and a band, never a distance ----------------------
  pin.match(page, /result\.diff\.position\.geometry_differs_count/, "expected the position section to render the payload's COUNT");
  pin.match(page, /세었을 뿐, 재지 않음/, "expected the count to be labelled counted-not-measured");
  pin.match(page, /WARN/, "expected the position section to carry the payload's WARN band");
  pin.match(page, /\{result\.diff\.position\.basis\}/, "expected the georeference basis to be shown verbatim, not paraphrased");
  pin.match(page, /result\.diff\.position\.max_residual_m/, "expected the uncertainty band to come from the payload");
  // The three ways this section could start claiming positional accuracy.
  pin.doesNotMatch(
    page,
    /geometry_differs_count\s*[*/+-]/,
    "the geometry count must never be arithmetic'd into a magnitude",
  );
  pin.doesNotMatch(
    page,
    /max_residual_m\s*[*/]|residual_lane_widths\s*[*/]/,
    "the uncertainty band must never be scaled into a per-link distance",
  );
  pin.doesNotMatch(
    page,
    /toFixed|react-leaflet|from "leaflet"|MapViewer|DashboardMap|NetworkViewer/,
    "no map overlay and no derived precision -- this view has no defensible coordinates to draw",
  );
  // PositionScope.reported is false and nothing may flip it.
  pin.doesNotMatch(backendRouter, /reported\s*=\s*True|"reported":\s*True/, "the backend must never set PositionScope.reported");

  // --- shell ---------------------------------------------------------------
  pin.match(shell, /href: "\/network-diff"/, "expected the diff view in the top navigation");
  pin.match(shell, /icon: "network-diff"/, "expected the diff view to carry its own nav icon");
});

contractTest("2.4: every diff-view string carries its own Japanese", (pin) => {
  // pickByLanguage keys ja off the KOREAN string (lib/i18n.ts), so a
  // two-argument t(ko, en) with no entry in the GENERATED lib/i18n-ja.ts
  // silently renders English for Japanese users -- the same hazard the login
  // page's pin above covers by checking the dictionary. This page takes the
  // other road: every call is the three-argument form, so no string on it
  // depends on a generated file at all. Pinned so a later edit cannot quietly
  // drop back to two arguments and lose ja.
  const page = read("app/network-diff/page.tsx");

  pin.doesNotMatch(
    page,
    /\bt\(\s*"(?:[^"\\]|\\.)*"\s*,\s*"(?:[^"\\]|\\.)*"\s*,?\s*\)/,
    "a two-argument t() on this page would fall back to English for ja -- pass the third argument",
  );
  pin.doesNotMatch(
    page,
    /\bt\(\s*`[^`]*`\s*,\s*`[^`]*`\s*,?\s*\)/,
    "a two-argument template-literal t() has the same ja gap -- pass the third argument",
  );
  // Guards the extraction itself: if the page ever stops using t(), the two
  // doesNotMatch pins above would pass vacuously.
  const calls = page.match(/\bt\(/g) ?? [];
  pin.match(
    `count=${calls.length}`,
    /count=([5-9]\d|\d{3,})/,
    "expected at least 50 t() calls on the diff view -- fewer means this extraction went stale, not that the page shrank",
  );
});

// Real behaviour, not a source pin: the open-redirect guard is the one piece of
// U9 where being wrong is a security bug rather than a cosmetic one.
test("U9: safeNextPath only ever returns a same-origin path", () => {
  assert.equal(safeNextPath("/dashboard"), "/dashboard");
  assert.equal(safeNextPath("/network?x=1#a"), "/network?x=1#a");
  assert.equal(safeNextPath("  /health  "), "/health");

  // Everything that could leave the origin, or smuggle a scheme past a prefix
  // check. TAB_PATH/NEWLINE_PATH are built from escapes so the literal control
  // characters never live in this file.
  const hostile: Array<string | null | undefined> = [
    "//evil.example",
    "///evil.example",
    "https://evil.example/pay",
    "http://evil.example",
    "/\\evil.example",
    "javascript:alert(1)",
    "data:text/html,<script>",
    "\\\\evil.example",
    "/pa th",
    `/a${String.fromCharCode(9)}b`,
    `/a${String.fromCharCode(10)}b`,
    "",
    "   ",
    null,
    undefined,
  ];
  for (const candidate of hostile) {
    assert.equal(safeNextPath(candidate), DEFAULT_NEXT_PATH, `expected ${JSON.stringify(candidate)} to be refused`);
  }

  // Bouncing back to the login screen is a loop, not a destination.
  assert.equal(safeNextPath("/login"), DEFAULT_NEXT_PATH);
  assert.equal(safeNextPath("/login?next=/x"), DEFAULT_NEXT_PATH);

  // loginHref encodes exactly what safeNextPath accepted, and omits the
  // parameter entirely when it would only name the default.
  assert.equal(loginHref("/network"), "/login?next=%2Fnetwork");
  assert.equal(loginHref("https://evil.example"), "/login");
  assert.equal(loginHref(DEFAULT_NEXT_PATH), "/login");
});

// ---------------------------------------------------------------------------
// Roadmap v2 §5.B B1 / §5.A A4 -- the genesis page and its gate rendering
// ---------------------------------------------------------------------------
//
// Kept in their own tests (and away from the layout/style pins) for the reason
// this file's contractTest docstring gives: a restyle of the verdict cards must
// never be able to hide the behavioural pins below it.

contractTest("genesis view renders the gate's tri-state without collapsing it", (pin) => {
  const page = read("app/genesis/page.tsx");
  // `statusLabel` (the four terminal job states' own wording) moved to
  // lib/genesisArtifacts.ts in B3-FIX so `GenesisComparisonPanel`'s job
  // picker could share the exact same wording instead of re-deriving it --
  // see that file's own comment. The two full-sentence pins below moved with it.
  const genesisLib = read("lib/genesisArtifacts.ts");

  // The design rule the whole R1.5 gate hangs on: a check that could not RUN is
  // never a pass. Three states, three words, and not_checkable said out loud.
  pin.match(page, /case "not_checkable":\s*\n\s*return t\("확인 불가"/, "expected NOT_CHECKABLE to have its own word, not a grey pending state");
  pin.match(page, /case "fail":\s*\n\s*return t\("실패"/, "expected FAIL to be rendered distinctly");
  pin.match(page, /case "pass":\s*\n\s*return t\("통과"/, "expected PASS to be rendered distinctly");
  pin.match(
    page,
    /if \(state === "pass"\) return "border-emerald/,
    "expected only the PASS state to get the success colour",
  );

  // The four terminal job states are four answers, never ok/not-ok. In
  // particular `provisional` must not read as success: it means the network was
  // registered AND a refuse-tier check could not run.
  for (const status of ["refused", "provisional", "published", "error"]) {
    pin.match(page, new RegExp(`case "${status}":`), `expected the ${status} job state to be named on the genesis page`);
  }
  pin.match(genesisLib, /잠정 등록 — 미확인 검사 있음/, "expected the provisional label to state that a check could not run");
  pin.match(genesisLib, /거부됨 — 등록하지 않음/, "expected the refused label to state that nothing was registered");

  // Every gate sentence comes from the backend payload (gate.py's own ko/en/ja
  // table), never from a dictionary in this file.
  pin.match(page, /\{check\.title\}/, "expected the check title to come from the gate payload");
  pin.match(page, /\{finding\.message\}/, "expected finding text to come from the gate payload");
  pin.match(page, /\{gap\.message\}/, "expected gap text to come from the gate payload");

  // A dropped finding nobody counts is the silent drop this project forbids.
  pin.match(page, /check\.findings_omitted > 0/, "expected omitted findings to be counted on screen");

  // The verdict shown is the STORED one -- the verdict that actually decided
  // whether the artifact was published, never a fresh re-run.
  pin.match(page, /fetchGenesisGateReport\(gatedJobId, lang\)/, "expected the gate report to be read from the stored-verdict endpoint");
  // A re-gate (live-check evidence) changes the verdict without changing the
  // job id, so the fetch key has to include when the verdict was decided --
  // otherwise the page would keep showing the superseded one.
  pin.match(page, /\}, \[gatedJobId, gateCheckedAt, lang\]\);/, "expected the gate fetch to re-key on checked_at, so a re-gate is not shown stale");

  // ko primary with an explicit ja argument on every t(), so no string here
  // depends on an entry landing in the generated lib/i18n-ja.ts dictionary.
  pin.doesNotMatch(
    page,
    /\bt\(\s*"(?:[^"\\]|\\.)*"\s*,\s*"(?:[^"\\]|\\.)*"\s*,?\s*\)/,
    "a two-argument t() on this page would fall back to English for ja -- pass the third argument",
  );
  const calls = page.match(/\bt\(/g) ?? [];
  pin.match(
    `count=${calls.length}`,
    /count=([4-9]\d|\d{3,})/,
    "expected at least 40 t() calls on the genesis page -- fewer means this extraction went stale",
  );
});

contractTest("genesis API surface keeps its workspace and refusal contract", (pin) => {
  const api = read("lib/api.ts");
  const shell = read("components/AppShell.tsx");

  const genesisSection = sliceBetween(api, "export type GenesisJobStatus", "export function genesisArtifactUrl");

  // Mutations go through apiMutationFetch, which is one of the two places
  // X-Workspace-Id and credentials are attached -- a bare fetch() here would
  // publish a generated network into the DEFAULT project.
  pin.match(genesisSection, /uploadGenesisOsm[\s\S]*?apiMutationFetch\(path, \{ method: "POST"/, "expected the .osm upload to go through apiMutationFetch");
  pin.match(genesisSection, /startGenesisJob[\s\S]*?apiMutationFetch\("\/api\/genesis\/jobs"/, "expected starting a job to go through apiMutationFetch");
  pin.match(genesisSection, /attachGenesisLiveCheck[\s\S]*?apiMutationFetch\(`\/api\/genesis\/jobs\/\$\{jobId\}\/live-check`/, "expected live-check attachment to go through apiMutationFetch");
  pin.doesNotMatch(genesisSection, /\n\s*const r = await fetch\(/, "genesis calls must not bypass the workspace-header fetch helpers");

  // The four job states and the three verdicts are in the type, so a UI that
  // collapsed them would stop compiling rather than quietly lie.
  pin.match(genesisSection, /"refused"\s*\n?\s*\|\s*"provisional"\s*\n?\s*\|\s*"published"/, "expected refused/provisional/published to be distinct job states");
  pin.match(genesisSection, /GenesisGateVerdict = "pass" \| "refused" \| "incomplete"/, "expected the gate's own tri-state verdict in the client type");
  pin.match(genesisSection, /findings_omitted: number/, "expected the omitted-findings count to survive into the client type");
  pin.match(genesisSection, /admitted: boolean/, "expected live evidence to carry whether the gate ADMITTED it");

  pin.match(shell, /href: "\/genesis"/, "expected the genesis page to be reachable from the main navigation");
});

// Style pin, quarantined for the same reason as the base-model layout test
// above: it goes stale on any restyle of these buttons, so it stays isolated
// from behavioural checks.
//
// globals.css inverts the dark palette so `bg-slate-900` (and `bg-slate-800`)
// render as ivory, not near-black -- see the "Dark remap" comment at
// app/globals.css:153-164. A chip on that background must pair with
// `text-ink` (kept dark) for its label to read; pairing it with `text-white`
// instead renders white-on-ivory, effectively invisible. Every compliant call
// site in the app already follows this (app/health/page.tsx,
// app/downloads/page.tsx, app/base-model/page.tsx); these four genesis
// primary CTAs were the only violations found in a full-app sweep on
// 2026-08-12.
contractTest("genesis primary CTAs stay legible on the inverted dark palette", (pin) => {
  const violation =
    /className="[^"]*bg-slate-900[^"]*text-white[^"]*"|className="[^"]*text-white[^"]*bg-slate-900[^"]*"/;

  pin.doesNotMatch(
    read("app/genesis/page.tsx"),
    violation,
    "expected the 생성 시작 button not to pair bg-slate-900 with text-white (renders ivory-on-ivory; use text-ink)",
  );
  pin.doesNotMatch(
    read("app/genesis/region/page.tsx"),
    violation,
    "expected the region-extract buttons not to pair bg-slate-900 with text-white (renders ivory-on-ivory; use text-ink)",
  );
  pin.doesNotMatch(
    read("app/genesis/demand/page.tsx"),
    violation,
    "expected the demand upload button not to pair bg-slate-900 with text-white (renders ivory-on-ivory; use text-ink)",
  );
  pin.doesNotMatch(
    read("app/genesis/phases/page.tsx"),
    violation,
    "expected the phases signal-staging button not to pair bg-slate-900 with text-white (renders ivory-on-ivory; use text-ink)",
  );
});

// ---------------------------------------------------------------------------
// Roadmap v2 §5.B B2 -- the region picker, and where it deliberately stops
// ---------------------------------------------------------------------------
//
// Two tests, each on one concern, and neither carrying a layout pin: the value
// of this unit is that a fetched region enters generation on B1's road and no
// other, and a restyle of the measurement card must never be able to hide that.

contractTest("B2: the region page ends at the staging door and hands off to B1's route", (pin) => {
  const page = read("app/genesis/region/page.tsx");

  // THE property. The only thing the page does with a completed fetch is call
  // `startGenesisJob` with the token -- the same call the upload flow makes.
  pin.match(
    page,
    /await startGenesisJob\(\{\s*\n\s*upload_token: selected\.upload_token,/,
    "expected the staged extract to be handed to B1's existing generation route by token",
  );
  // And it must never grow a road of its own into the gate or the dataset slot.
  for (const forbidden of ["gate", "publish", "datasets/upload", "artifact"]) {
    pin.doesNotMatch(
      page,
      new RegExp(`/api/[\\w/{}$-]*${forbidden}`),
      `the region page must not address a ${forbidden} endpoint -- generation, gating and publishing stay on B1's road`,
    );
  }

  // Every number on screen is the SERVER's measurement. A cap recomputed in the
  // browser could disagree with the cap that actually refuses.
  pin.match(page, /previewRegion\(parsed\)/, "expected the region to be measured by the server, not in the browser");
  pin.doesNotMatch(
    page,
    /validity_radius_m\s*=[^=]/,
    "the browser must not compute a validity radius of its own",
  );
  // An estimate presented as a guarantee is how "it will fit" becomes a refusal
  // nobody predicted.
  pin.match(page, /estimated_bytes/, "expected the estimated size to be shown against the cap before fetching");
  // MEASURED against the running app 2026-08-11: a 1.4 x 8.4 km region is
  // 12 km² against a 17 km² square limit -- comfortably "inside" by area -- and
  // is refused, because its half-extent is 4 195 m against a 2 079 m radius.
  // The headline has to be the comparison that actually decides, or the screen
  // tells that user they are fine right up until the refusal.
  pin.match(
    page,
    /within_region_cap\s*\n?\s*\?\s*t\("한계 안"/,
    "expected the verdict headline to lead with the half-extent verdict, not with the area",
  );
  pin.match(
    page,
    /한계 초과 — 이 영역은 추출할 수 없습니다/,
    "expected an over-limit region to say so in words, not only by colour",
  );
  pin.match(
    page,
    /예상치일 뿐이며, 한계는 실제로 도착한 바이트에 대해 적용됩니다/,
    "expected the size estimate to say it is an estimate and that the cap applies to the bytes that arrive",
  );

  // The five fetch states are five answers. `refused` in particular must read
  // as a decision with a reason, never as a crash.
  for (const status of ["queued", "running", "staged", "refused", "error"]) {
    pin.match(page, new RegExp(`case "${status}":`), `expected the ${status} fetch state to be named on the region page`);
  }
  pin.match(page, /거부됨 — 아무것도 받지 않았습니다/, "expected a refusal to say that nothing was downloaded");
  pin.match(
    page,
    /selected\.refusal\.reasons\.map/,
    "expected the backend's own refusal reasons to be shown verbatim, numbers included",
  );

  // Provenance is the product thesis. The instance, the query, the time, the
  // byte count and the licence all have to be on screen.
  for (const field of ["endpoint", "fetched_at", "bytes", "sha256", "licence", "query"]) {
    pin.match(
      page,
      new RegExp(`selected\\.provenance\\.${field}`),
      `expected the extract's ${field} to be visible -- a network whose origin cannot be reconstructed is not auditable`,
    );
  }
  pin.match(
    page,
    /attribution_note_present/,
    "expected a missing ODbL note to be reported as missing rather than replaced with one we wrote",
  );

  // What of the 0.4 mockup did NOT ship is said on the page, not only in a
  // commit message -- a consultant who read the mockup would otherwise look for
  // the lasso and conclude the page is broken.
  pin.match(
    page,
    /폴리곤 라쏘·도로 개별 선택·블록 제외/,
    "expected the deferred lasso / per-road / block-exclusion features to be named on the page",
  );

  // ko primary with an explicit ja argument on every t().
  pin.doesNotMatch(
    page,
    /\bt\(\s*"(?:[^"\\]|\\.)*"\s*,\s*"(?:[^"\\]|\\.)*"\s*,?\s*\)/,
    "a two-argument t() on this page would fall back to English for ja -- pass the third argument",
  );
  const calls = page.match(/\bt\(/g) ?? [];
  pin.match(
    `count=${calls.length}`,
    /count=([4-9]\d|\d{3,})/,
    "expected at least 40 t() calls on the region page -- fewer means this extraction went stale",
  );
});

contractTest("B2: the region API bindings carry the workspace header and the measured refusal", (pin) => {
  const api = read("lib/api.ts");
  const genesisPage = read("app/genesis/page.tsx");

  const regionSection = sliceBetween(
    api,
    "// Roadmap v2 §5.B B2 -- Region",
    "// Admin: accounts and project membership",
  );
  pin.match(regionSection, /\S/, "expected the region bindings in lib/api.ts (marker moved?)");

  // The bindings live ABOVE the admin block on purpose: the P4 pin slices
  // lib/api.ts from `adminRequest` to EOF and forbids apiFetch/apiMutationFetch
  // inside that slice, so a region binding placed below it would fail a test
  // about a different unit entirely.
  const adminSlice = api.slice(api.indexOf("async function adminRequest"));
  pin.doesNotMatch(adminSlice, /startRegionFetch|previewRegion/, "region bindings must stay above the admin block");

  // The mutation goes through apiMutationFetch, one of the two places
  // X-Workspace-Id and credentials are attached -- a bare fetch() here would
  // stage a client's extract into the DEFAULT project.
  pin.match(
    regionSection,
    /startRegionFetch[\s\S]*?apiMutationFetch\("\/api\/genesis\/region\/fetches"/,
    "expected starting a region fetch to go through apiMutationFetch",
  );
  pin.doesNotMatch(
    regionSection,
    /\n\s*const r = await fetch\(/,
    "region calls must not bypass the workspace-header fetch helpers",
  );

  // Five states in the type, so a UI that collapsed them would stop compiling
  // rather than quietly lie.
  pin.match(
    regionSection,
    /RegionFetchStatus = "queued" \| "running" \| "staged" \| "refused" \| "error"/,
    "expected the five fetch states to be distinct in the client type",
  );
  // "Too large" without the numbers is not an actionable refusal.
  pin.match(
    regionSection,
    /class RegionFetchError extends GenesisError/,
    "expected region refusals to carry their measurement",
  );
  pin.match(regionSection, /measurement: Record<string, unknown>/, "expected the measurement to survive onto the error");
  pin.match(
    regionSection,
    /within_region_cap: boolean/,
    "expected the cap verdict to be part of the measurement the UI reads",
  );
  // The Overpass instance is SERVER configuration. The client type reads it
  // back; nothing here ever sends one, because a caller-supplied URL would make
  // the fetch route an SSRF instrument pointed at whatever the backend reaches.
  const startBinding = sliceBetween(regionSection, "export async function startRegionFetch", "export async function fetchRegionFetches");
  pin.match(startBinding, /\S/, "expected the startRegionFetch binding (marker moved?)");
  pin.doesNotMatch(startBinding, /endpoint/, "the client must never send an Overpass endpoint");

  // Discoverable: the upload flow points at the region flow.
  pin.match(genesisPage, /href="\/genesis\/region"/, "expected the genesis page to link to the region extractor");
});

// ---------------------------------------------------------------------------
// Roadmap v2 §5.B B3 (완결) -- the gate's verdict gets a map, and a comparison
// ---------------------------------------------------------------------------

contractTest("B3: the gate findings map never draws a silent blank and never invents excluded-zone geometry", (pin) => {
  const map = read("components/genesis/GateFindingsMap.tsx");
  const lib = read("lib/genesisArtifacts.ts");

  // No artifact, a failed network fetch and an empty network are three
  // DIFFERENT named states -- never a blank canvas.
  pin.match(map, /!hasArtifact\s*\)\s*\{[\s\S]*?<EmptyState/, "expected a job with no artifact to render a named empty state");
  pin.match(map, /loadError &&[\s\S]*?<EmptyState/, "expected a failed network fetch to render a named empty state");
  pin.match(map, /network\.links\.length === 0[\s\S]*?<EmptyState/, "expected an empty network to render a named empty state");

  // Every finding this page cannot place on the map is LISTED, not dropped.
  pin.match(map, /unresolvedCount > 0/, "expected unresolved findings to get their own disclosed section");
  pin.match(
    lib,
    /return \{ linkIds: \[\], connectorIds: \[\], resolved: false \};/,
    "expected an unmatched finding subject to resolve to `resolved: false`, not a guess",
  );

  // Excluded zones are NAMED but carry NO drawable geometry -- `build_draft`
  // rebuilds the network from a smaller extent after removing them, so their
  // recorded link_ids point at a draft that was thrown away. The map must say
  // that plainly rather than invent a pin location for them.
  pin.match(
    lib,
    /excluded_zones\?: GenesisIsolatedZone\[\];/,
    "expected excluded zones to be a named field on the connectivity metadata",
  );
  pin.match(
    map,
    /이 구역은 최종 좌표가 확정되기 전에 제외되어, 지도에 그릴 좌표가 없습니다/,
    "expected an excluded zone's detail panel to say plainly that it has no coordinates left to draw",
  );

  // The artifact fetch stays on the same workspace-header/credentials path
  // every other genesis read in this app uses -- a bare fetch() here would
  // silently read a different workspace's job.
  pin.match(
    lib,
    /await apiFetch\(`\/api\/genesis\/jobs\/\$\{jobId\}\/artifact\/genesis_json`/,
    "expected the draft network to be read through apiFetch",
  );
  pin.doesNotMatch(lib, /\n\s*const r = await fetch\(/, "genesis artifact reads must not bypass the workspace-header fetch helper");

  // FIX B3-1: a bare all-digit finding subject is NOT proof of a link/
  // connector id -- V5's signal-head numbers and V6's composition/input
  // numbers are just as dense and start from 1 -- so the bare-numeric
  // fallback must be gated on the finding's OWN code, not applied to every
  // code alike (which is how a V5 finding used to highlight an unrelated
  // link that merely shared its numeric id).
  pin.match(
    lib,
    /finding: Pick<GenesisGateFinding, "subject" \| "code">/,
    "expected resolveFindingHighlight to take the finding's code, not only its subject",
  );
  pin.match(
    lib,
    /BARE_ID_RE\.test\(subject\) && BARE_ID_RESOLVABLE_CODES\.has\(finding\.code\)/,
    "expected the bare-numeric fallback to require both a bare digit string AND an allow-listed code",
  );
  const bareIdAllowList = sliceBetween(lib, "const BARE_ID_RESOLVABLE_CODES = new Set<string>([", "]);");
  pin.match(bareIdAllowList, /v7_link_without_geometry/, "expected v7_link_without_geometry in the allow-list -- it shares one id namespace across links and connectors by construction");
  pin.match(bareIdAllowList, /v4_more_mapped_lanes_than_link_has/, "expected V4's bare connector-id codes in the allow-list");
  // Re-verification traced EVERY broadened allow-list entry to its GateFinding
  // call site and confirmed the subject is a link/connector id (bare `_label()`
  // fallback when the element has no name). Pin each one: an accidental
  // removal fails nothing at runtime -- that code's findings would just
  // quietly downgrade to "could not place on the map".
  for (const code of [
    "v4_lane_reference_malformed",
    "v4_lane_index_outside_link",
    "v3_orphan_link",
    "v3_isolated_zone",
    "v3_unreachable_zone",
    "v3_trapped_zone",
    "v3_broken_zone",
    "v3_input_cannot_reach_an_exit",
    "v4_lane_drop_without_runway",
    "v5_approach_reaches_no_head_in_node",
    "v6_link_demand_over_capacity",
    "v7_zero_length_link",
    "v7_link_below_generator_floor",
    "v7_links_share_one_polyline",
    "v9_stop_line_without_queue_counter",
  ]) {
    pin.match(
      bareIdAllowList,
      new RegExp(`"${code}"`),
      `expected ${code} to stay in the bare-id allow-list -- it was traced to a link/connector-id subject at its gate.py call site`,
    );
  }
  pin.doesNotMatch(
    bareIdAllowList,
    /v5_head_without_controller|v5_head_signal_group_malformed|v5_head_without_signal_group/,
    "V5's signal-head-id codes must NEVER be trusted for the bare-numeric fallback -- they are a third id space, not a link/connector id, and this exact collision was the CRITICAL finding",
  );
  pin.doesNotMatch(
    bareIdAllowList,
    /v6_composition_empty|v6_composition_relflow_sum_zero|v6_composition_relflow_negative|v6_composition_unused|v6_input_without_volume|v6_input_volume_is_zero|v6_input_composition_missing|v6_input_without_routing_coverage/,
    "V6's composition/input-id codes must NEVER be trusted for the bare-numeric fallback -- a fourth and fifth id space, not a link/connector id (v6_composition_unused added 2026-08-12 with the warn-tier orphan clause)",
  );

  // FIX B3-3: a clicked link/connector can carry MORE than one finding (three
  // V4 lane findings on one connector is a real shape) -- the detail panel
  // must list every finding touching the clicked element, not silently
  // collapse to the first and hide the rest.
  pin.match(map, /kind: "finding"; refs: FindingRef\[\]/, "expected a selection to carry EVERY finding on the clicked element, not just one");
  pin.match(map, /selected\.refs\.map\(/, "expected the detail panel to render the full list of findings for the clicked element");
  pin.doesNotMatch(map, /if \(refs\[0\]\) setSelected/, "must not collapse a multi-finding element's click down to only its first finding");

  // FIX B3-9: a JSX `onWheel` prop is registered PASSIVE by this app's React
  // 19, so `event.preventDefault()` inside it is a silent no-op and the page
  // scrolls underneath an attempted zoom. The fix must be a real, non-passive
  // DOM listener, not the JSX prop.
  pin.doesNotMatch(map, /onWheel=\{handleWheel\}/, "a JSX onWheel handler cannot preventDefault under this app's React 19 -- must not be reintroduced");
  pin.match(
    map,
    /addEventListener\("wheel", handleWheelNative, \{ passive: false \}\)/,
    "expected the wheel handler to be attached as a real, non-passive DOM listener",
  );

  // FIX B3-10: panning/zooming sets `view` on every pointermove, and the old
  // inline JSX read `view.scale` per polyline for strokeWidth -- rebuilding
  // ~5,000 elements on every drag frame. The fix keeps `view` OFF the
  // polyline layer's props entirely (a memoized component) and uses
  // `vector-effect="non-scaling-stroke"` for constant on-screen width instead
  // of a per-render division.
  pin.match(map, /const NetworkLayer = memo\(function NetworkLayer\(/, "expected the polyline layer to be a component memoized against everything except `view`");
  pin.match(map, /vectorEffect="non-scaling-stroke"/, "expected constant-width strokes via vector-effect, not a view.scale division read on every element");
  pin.doesNotMatch(map, /strokeWidth=\{[\d.]+ \/ view\.scale\}/, "must not reintroduce a per-element view.scale read for stroke width");

  // FIX B3-11: `loading` was ALSO a dependency of the fetch effect above --
  // `setLoading(true)` (inside the effect) then re-fired the effect on the
  // very next render, and React runs the PREVIOUS run's cleanup first, which
  // cancelled the fetch that same call had just started. Measured (react-dom
  // 19.2.4): a near-instant response could lose the race outright (stuck
  // `loading`); anything slower survived but paid for a wasted duplicate
  // fetch. Removing the dependency is race-free at every delay tested.
  // Found live 2026-08-12, second finding on this same effect; re-verified
  // empirically 2026-08-12.
  const fetchEffectDeps = sliceBetween(map, "}, [open, network,", "]);\n\n  const bbox");
  pin.match(fetchEffectDeps, /\S/, "expected the fetch effect's dependency array in GateFindingsMap.tsx (markers moved? the doesNotMatch below would pass vacuously on empty input)");
  pin.doesNotMatch(
    fetchEffectDeps,
    /\bloading\b/,
    "the fetch effect must not depend on `loading` -- it sets `loading` itself, so a dependency on it makes the effect cancel its own in-flight fetch via cleanup, producing an eternal spinner",
  );
});

contractTest("B3: generation comparison stays scoped to gate results and named counts, never geometry", (pin) => {
  const panel = read("components/genesis/GenesisComparisonPanel.tsx");
  const lib = read("lib/genesisArtifacts.ts");

  // The scope is stated ON SCREEN, not only in a comment -- "compare with a
  // previous generation" without this line would read as a full network diff.
  pin.match(
    panel,
    /게이트 판정과 개수만 비교합니다 — 네트워크 기하 자체의 차이는 비교하지 않습니다/,
    "expected the comparison panel to state its own scope on screen",
  );

  // Findings are matched by an identity the report actually carries (code +
  // subject) -- never by array position, which two differently-shaped reports
  // would silently misalign.
  pin.match(
    lib,
    /function findingKey\(finding: GenesisGateFinding\): string \{\s*\n\s*return `\$\{finding\.code\} \$\{finding\.subject\}`;/,
    "expected findings to be matched by code+subject identity, not by list position",
  );

  // A capped finding list is disclosed as a LOWER BOUND, never presented as a
  // complete diff.
  pin.match(
    panel,
    /baseFindingsOmitted > 0 \|\| d\.targetFindingsOmitted > 0/,
    "expected a truncated gate report to be disclosed as a lower-bound comparison",
  );

  // The compared job's verdict comes from the SAME stored-verdict endpoint
  // the main page reads for the selected job -- no second, re-run gate.
  pin.match(
    panel,
    /fetchGenesisGateReport\(priorId, lang\)/,
    "expected the compared job's gate report to be read from the stored-verdict endpoint",
  );

  // FIX B3-7: gate.py can legitimately emit the SAME code+subject twice within
  // one check (V2's per-side connector findings) -- a Set-based diff shows
  // +0/-0 for base=[K] vs target=[K,K], silently undercounting. The diff must
  // be a multiset: count occurrences per key, and disclose when a key
  // repeated so the reader knows code+subject is not always unique.
  pin.match(lib, /const baseCounts = new Map<string, number>\(\);/, "expected the finding diff to tally occurrences per code+subject key (multiset), not just Set membership");
  pin.match(lib, /if \(baseCount > 1 \|\| targetCount > 1\) duplicateKeyPresent = true;/, "expected a repeated code+subject key to be flagged, not silently folded away");
  pin.match(panel, /duplicateKeysPresent/, "expected the comparison panel to disclose when a check repeated a code+subject key");

  // FIX B3-4: `compareGenesisChecks` used to diff `check.findings` only --
  // `check.warnings` (which GateFindingsMap already reads) never entered the
  // comparison at all, so a warn-tier-only change between two generations
  // rendered as "unchanged". The diff and the table must cover both tiers.
  pin.match(lib, /addedWarnings: GenesisGateFinding\[\];/, "expected the check delta to carry a warn-tier added/removed diff, separate from the refuse/warn findings diff");
  pin.match(panel, /delta\.addedWarnings\.length/, "expected the comparison table to render warn-tier deltas, not silently drop them");

  // FIX B3-5: a job with no artifact and a job whose artifact fetch FAILED
  // used to collapse into the same `null` -> "—", so "no excluded zones" was
  // indistinguishable from "could not check". They must render differently.
  pin.match(panel, /status: "error"; message: string/, "expected a failed excluded-zone fetch to be a distinct, named outcome from 'no artifact'");
  pin.match(panel, /불러오지 못함/, "expected a failed excluded-zone fetch to render its own marker, not the same em dash as 'no artifact'");

  // FIX B3-8 residual (re-verify): a current job that HAS a gate but whose
  // report fetch FAILED must branch to its own named error before the
  // "loading…" message -- otherwise the failure reads as pending forever.
  // page.tsx owns that fetch and its error state, so it must forward it.
  pin.match(panel, /currentReportError \? \(/, "expected a failed current-report fetch to branch to a named error state before the loading message");
  pin.match(panel, /현재 생성의 게이트 결과를 불러오지 못해 비교할 수 없습니다/, "expected the failed current-report state to be named on screen");
  pin.match(read("app/genesis/page.tsx"), /currentReportError=\{reportError\}/, "expected page.tsx to forward its report-fetch error into the comparison panel");

  // FIX B3-6: the "compare against" picker showed a raw `job.status` and a
  // raw ISO `job.created_at` next to the job history list's already-
  // translated versions of the exact same two fields.
  pin.match(panel, /statusLabel\(job\.status, t\)/, "expected the comparison picker to translate job.status instead of showing it raw");
  pin.match(panel, /formatTimestamp\(job\.created_at, lang\)/, "expected the comparison picker to format job.created_at instead of showing a raw ISO string");

  // FIX B3-8: the panel used to gate its whole result block on `currentReport`
  // with no branch naming the case where it is null -- a queued/running
  // selected job (no gate at all, ever, until terminal) rendered nothing.
  pin.match(panel, /!currentJob\.gate \?/, "expected the panel to name the case where the currently-selected job has no gate at all");
  pin.match(
    panel,
    /이 생성에는 아직 게이트 결과가 없어 비교할 수 없습니다/,
    "expected a named empty state -- never a silent blank -- when the current generation has no gate result yet",
  );
});


// ---------------------------------------------------------------------------
// Roadmap C1-b -- 현시도 제안+확인. The pins here are the unit's refusal-shaped
// invariants, not its layout: a restyle must not touch any of them.
// ---------------------------------------------------------------------------

contractTest("the 현시도 review screen never presents a proposal as a verified fact", (pin) => {
  const page = read("app/genesis/phases/page.tsx");

  // The load-bearing sentence. A proposal is a machine READING; the only claim
  // of correctness on this page belongs to the person who confirms.
  pin.match(
    page,
    /이 앱이 검증한 것이 아닙니다/,
    "expected the header to state, in ko, that the app has NOT verified the model's reading",
  );
  pin.match(
    page,
    /모델의 판독 \(제안\)/,
    "expected the proposal panel to be titled as a reading/proposal, never as a result",
  );
  pin.match(
    page,
    /phase\.ambiguity && \(/,
    "expected every phase's ambiguity note to be rendered -- it is the model saying what it could NOT resolve",
  );
  // A confidence score would invite a threshold, and a threshold is how a
  // low-confidence guess gets promoted to a reading.
  pin.doesNotMatch(page, /confidence/i, "expected no confidence score anywhere on the review screen");

  // The cross-check's three tiers must stay three words. `observation` in
  // particular corroborates a measurement and must never read as an endorsement.
  pin.match(page, /findingWord\(/, "expected flag/observation/agrees to be rendered as three distinct words");
  pin.match(page, /배정의 정오는 여기서 판단하지 않습니다/, "expected the cross-check panel to disclaim judging correctness");
});

contractTest("the 현시도 review screen keeps confirmation the only road to signal input", (pin) => {
  const page = read("app/genesis/phases/page.tsx");
  const api = read("lib/api.ts");

  // The browser must never assemble a genesis.signals.v1 document: a second
  // builder could drift from the validator, and "an unconfirmed proposal is
  // inert" would become a client-side convention instead of a server refusal.
  pin.doesNotMatch(page, /kind:\s*"genesis\.signals/, "expected the page never to assemble a signals document itself");
  pin.doesNotMatch(page, /assignments:\s*\[/, "expected the page never to build the assignments array a signals document is made of");
  pin.match(page, /stagePhaseDiagramSignalsInput\(confirmedIds\)/, "expected staging to send review ids to the server, not a document");
  pin.match(
    page,
    /rows\.filter\(\(row\) => row\.confirmed\)/,
    "expected only CONFIRMED reviews to be offered for staging",
  );

  // The attester comes from the session. There is deliberately no field for a
  // caller to put somebody else's name in.
  pin.doesNotMatch(
    sliceBetween(api, "export type PhaseDiagramConfirmation", "/** Record ONE human's attestation"),
    /attested_by/,
    "expected the confirmation payload to carry no attester name -- the server takes it from the session",
  );
  pin.match(page, /image_sha256: review\.render\.image_sha256/, "expected the confirmation to be bound to the image the human looked at");
  pin.match(page, /source_sha256: review\.render\.source_sha256/, "expected the confirmation to be bound to the sheet file it was made against");
  pin.match(page, /changes\.push\(/, "expected what the human changed against the proposal to be recorded");
});

contractTest("the 현시도 review screen distinguishes stale, unconfirmed and blank-form", (pin) => {
  const page = read("app/genesis/phases/page.tsx");

  // Three states, three words. `stale` must NOT collapse into "nobody
  // confirmed": somebody did, and hiding that loses the record.
  pin.match(page, /재확인 필요/, "expected a stale confirmation to read as 재확인 필요");
  pin.match(page, /미확인/, "expected an unconfirmed review to have its own word");
  pin.match(page, /확인됨/, "expected a confirmed review to have its own word");
  pin.match(
    page,
    /review\.stale_reasons\.length > 0 && \(/,
    "expected the stale reasons to be shown verbatim, not summarised away",
  );

  // Roadmap C1 option (b): a refused or absent proposal is the SAME screen with
  // a blank form, not a separate workflow.
  pin.match(
    page,
    /!review\.proposal \|\| review\.proposal\.outcome === "refused"/,
    "expected a refused OR missing proposal to take the same branch -- the blank manual-entry form",
  );
  pin.match(
    page,
    /아래 양식에 직접 입력하십시오/,
    "expected the blank-form branch to say so in ko rather than rendering an empty panel",
  );
});

contractTest("the demand side-input UI never invents a link id and stays one door", (pin) => {
  const page = read("app/genesis/demand/page.tsx");
  const api = read("lib/api.ts");
  const genesisPage = read("app/genesis/page.tsx");

  // No second door: the browser must never assemble a genesis.demand.v1
  // document itself -- a second builder in TypeScript could drift from
  // side_inputs.parse_side_input, the one validator that actually decides
  // whether a document is usable.
  pin.doesNotMatch(page, /kind:\s*"genesis\.demand/, "expected the page never to assemble a demand document itself");
  pin.match(page, /buildDemandInput\(payload\)/, "expected the page to hand the join to the server, not build a document");

  // Every leg starts EMPTY, and only a non-empty cell survives into the
  // payload -- a link id is never guessed on the caller's behalf.
  pin.match(
    page,
    /site\.legs\.map\(\(leg\) => \[leg, ""\]\)/,
    "expected every mapping cell to initialise empty, never a default link id",
  );
  pin.match(
    page,
    /if \(!trimmed\) continue; \/\/ empty stays empty/,
    "expected an empty cell to stay OUT of the approach_links payload rather than being filled in",
  );

  // The bindings live ABOVE the admin block, the same discipline the B2 pin
  // enforces for the region bindings -- the P4 pin slices lib/api.ts from
  // `adminRequest` to EOF and forbids apiFetch/apiMutationFetch there.
  const demandSection = sliceBetween(
    api,
    "// Demand side-input UI",
    "/** One row of `GET /api/admin/workspaces/{id}/members`",
  );
  pin.match(demandSection, /\S/, "expected the demand side-input bindings in lib/api.ts (marker moved?)");
  const adminSlice = api.slice(api.indexOf("async function adminRequest"));
  pin.doesNotMatch(
    adminSlice,
    /parseDemandCounts|buildDemandInput|fetchStagedSideInputs/,
    "demand side-input bindings must stay above the admin block",
  );
  pin.match(
    demandSection,
    /parseDemandCounts[\s\S]*?apiMutationFetch\(path, \{ method: "POST"/,
    "expected the counts upload to go through apiMutationFetch, one of the two workspace-header fetch paths",
  );
  pin.match(
    demandSection,
    /buildDemandInput[\s\S]*?apiMutationFetch\("\/api\/genesis\/demand-inputs\/build"/,
    "expected building the demand input to go through apiMutationFetch",
  );

  // The job-start form only ever NAMES a staged token -- it never fabricates
  // one, and an unpicked dropdown must send nothing (undefined), not "".
  pin.match(
    genesisPage,
    /demand_token: demandToken \|\| undefined/,
    "expected an unpicked demand dropdown to send undefined, never an empty-string token",
  );
  pin.match(
    genesisPage,
    /signals_token: signalsToken \|\| undefined/,
    "expected an unpicked signals dropdown to send undefined, never an empty-string token",
  );
});

// ---------------------------------------------------------------------------
// Adversarial pass on commit b3318ab -- CRITICAL: demand tokens silently
// portable across generations with renumbered links; MEDIUM B: silent link-id
// truncation in the mapping table.
// ---------------------------------------------------------------------------

contractTest(
  "the demand page requires a generation pick and never silently truncates a link id",
  (pin) => {
    const page = read("app/genesis/demand/page.tsx");
    const api = read("lib/api.ts");

    // MEDIUM B: Number.parseInt truncates at the first non-digit
    // ("1e5" -> 1, "12.5" -> 12) while the cell keeps showing the typed text,
    // so a different, plausible link id was built with zero warning. A
    // strict digits-only pattern is now the only thing allowed to decide
    // whether a cell becomes a link id.
    pin.match(
      page,
      /const LINK_ID_PATTERN = \/\^\\d\+\$\/;/,
      "expected a strict digits-only link id pattern to exist",
    );
    pin.doesNotMatch(
      page,
      /Number\.parseInt\(trimmed, 10\)/,
      "expected parseInt's silent truncation to be gone -- LINK_ID_PATTERN must be the only gate on a link id cell",
    );
    pin.match(
      page,
      /isValidLinkIdCell\(value\)/,
      "expected the invalid-cell count to be derived from the same validator the build guard uses",
    );

    // A cell that fails the pattern is a NAMED, VISIBLE, per-cell error, and
    // it blocks 만들기 -- never a silent coercion, and never lost among the
    // other cells.
    pin.match(
      page,
      /invalid\s*\n\s*\? "border-rose-400 bg-rose-50 text-rose-900"\s*\n\s*: "border-slate-300"/,
      "expected an invalid link-id cell to render its own error style",
    );
    pin.match(
      page,
      /disabled=\{building \|\| !builtAgainstJob \|\| invalidCellCount > 0\}/,
      "expected 만들기 to stay disabled while any cell fails the link-id pattern or no generation is picked",
    );

    // CRITICAL: a draft link id is a sequential counter draft.py resets on
    // every build, so a mapping means nothing without the generation it was
    // read from -- the picker is required, and the build payload must always
    // carry it.
    pin.match(
      page,
      /built_against_job: builtAgainstJob,/,
      "expected the build payload to carry built_against_job",
    );
    pin.match(
      page,
      /statusLabel\(job\.status, t\)/,
      "expected the generation picker to reuse statusLabel rather than a hand-rolled job.status label",
    );
    pin.match(
      page,
      /formatTimestamp\(job\.created_at, lang\)/,
      "expected the generation picker to reuse formatTimestamp rather than a raw ISO string",
    );
    pin.doesNotMatch(
      page,
      /\{job\.name\} · \{job\.status\}/,
      "expected the picker not to hand-roll job.status the way the 현시도 page's own picker still does",
    );

    // The client binding declares the field required, not optional -- an
    // omitted pick must fail typecheck, not silently post nothing.
    const demandSection = sliceBetween(
      api,
      "// Demand side-input UI",
      "/** One row of `GET /api/admin/workspaces/{id}/members`",
    );
    pin.match(
      demandSection,
      /built_against_job: string;/,
      "expected buildDemandInput's payload type to require built_against_job",
    );
  },
);

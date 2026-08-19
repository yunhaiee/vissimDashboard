import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import { DEFAULT_NEXT_PATH, loginHref, safeNextPath } from "../lib/authRedirect.ts";
import { DEFAULT_LANE_M, dividerOffsetCoord, laneBoundaryOffsets, offsetPolyline } from "../lib/roadWidth.ts";
import { messageWithoutSubject } from "../lib/findingMessage.ts";
import {
  REGION_PREFILL_STORAGE_KEY,
  stashRegionPrefill,
  takeRegionPrefill,
} from "../lib/region-prefill.ts";
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

/**
 * The source with its comments removed — and NOTHING else removed.
 *
 * For the one pin shape that cannot work on raw source: "this CSS class must
 * never appear in markup again". The files in this repo explain their own
 * fixes, so the banned class is named — correctly and usefully — in the comment
 * right above the code that stopped using it, and a file-wide `doesNotMatch`
 * would fire on that explanation forever. Stripping comments lets the pin mean
 * what it says (no live usage) while the prose that documents WHY stays put.
 *
 * WHY THIS IS A SCANNER AND NOT TWO REGEXES. The first version was a pair of
 * `.replace()` calls — one for block comments, one matching a double slash
 * through to the end of its line — and the second of those ate everything
 * after the `//` in a URL. (The old expressions are described rather than
 * quoted here: a regex for a block comment cannot be written inside a block
 * comment, which is its own small lesson about stripping by pattern.) The
 * CARTO tile template
 * `"https://{s}.basemaps.cartocdn.com/..."` is exactly such a line, and a
 * banned class sitting after it on the same line vanished with it. That is the
 * dangerous direction for a `doesNotMatch`: the haystack shrinks, so the pin
 * PASSES while the violation is still in the file. A pin that cannot fail is
 * worse than no pin, because the suite reports it as green.
 *
 * So the scanner copies string and template literals through verbatim, and
 * recognises regex literals (a `/` in a position where an expression may
 * start), which is the other place a stray quote or `//` can hide. Two
 * safeguards keep a mis-parse from ever silently shrinking the haystack:
 *
 *   * an unterminated `'`/`"` string ends at the newline, because a JS string
 *     cannot span a raw one. Without this, a bare apostrophe in JSX text
 *     (`don't`) would open a "string" that swallowed the rest of the file;
 *   * the regex heuristic only fires after a character that genuinely cannot
 *     end an expression, so a division is never mistaken for a regex.
 *
 * `contractTest("contract helper: withoutComments…")` below is its self-test,
 * including the exact tile-URL line that proved the old version wrong.
 */
const REGEX_MAY_FOLLOW = /[(,=:[!&|?{};+\-*%^~<>\n]/;

function withoutComments(source: string): string {
  let out = "";
  let i = 0;
  let lastMeaningful = "\n";

  const copyDelimited = (quote: string, allowNewline: boolean) => {
    out += source[i];
    i += 1;
    while (i < source.length) {
      const ch = source[i];
      if (ch === "\\") {
        out += source.slice(i, i + 2);
        i += 2;
        continue;
      }
      if (!allowNewline && ch === "\n") return; // unterminated -- not a string
      out += ch;
      i += 1;
      if (ch === quote) return;
    }
  };

  while (i < source.length) {
    const c = source[i];
    const next = source[i + 1];

    if (c === "/" && next === "/") {
      while (i < source.length && source[i] !== "\n") i += 1;
      continue;
    }
    if (c === "/" && next === "*") {
      i += 2;
      while (i < source.length && !(source[i] === "*" && source[i + 1] === "/")) i += 1;
      i += 2;
      continue;
    }
    if (c === '"' || c === "'" || c === "`") {
      copyDelimited(c, c === "`");
      lastMeaningful = c;
      continue;
    }
    if (c === "/" && REGEX_MAY_FOLLOW.test(lastMeaningful)) {
      // A regex literal. Copied through for the same reason a string is: it can
      // contain a quote or a `//` that would otherwise derail the scan.
      out += c;
      i += 1;
      let inClass = false;
      while (i < source.length) {
        const ch = source[i];
        if (ch === "\\") {
          out += source.slice(i, i + 2);
          i += 2;
          continue;
        }
        if (ch === "\n") break; // unterminated -- it was a division after all
        out += ch;
        i += 1;
        if (ch === "[") inClass = true;
        else if (ch === "]") inClass = false;
        else if (ch === "/" && !inClass) break;
      }
      lastMeaningful = "/";
      continue;
    }

    out += c;
    if (!/\s/.test(c) || c === "\n") lastMeaningful = c;
    i += 1;
  }
  return out;
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

// ---------------------------------------------------------------------------
// The helpers this file pins WITH have to be pinned too
// ---------------------------------------------------------------------------
//
// `withoutComments` feeds several `doesNotMatch` pins, and a stripper that
// removes too much makes every one of them pass vacuously -- a green suite over
// a live violation, which is strictly worse than no pin. Its first version did
// exactly that: `//` inside a URL string ate the rest of that line.

contractTest("contract helper: withoutComments strips comments and NOTHING else", (pin) => {
  // THE REGRESSION, verbatim: this is the CARTO tile template every map in this
  // app draws. The `//` is inside a string, so the class after it must survive.
  const tileLine =
    'const TILE_URL = "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png"; const bad = "bg-amber-50";';
  pin.match(
    withoutComments(tileLine),
    /bg-amber-50/,
    "a `//` inside a string must not be treated as a comment -- the old stripper ate this whole line",
  );
  pin.match(
    withoutComments(tileLine),
    /basemaps\.cartocdn\.com/,
    "the URL itself must survive intact",
  );

  // Template literals and escaped quotes are strings too.
  pin.match(
    withoutComments("const u = `https://x/y`; const c = 'text-amber-900';"),
    /text-amber-900/,
    "a `//` inside a template literal must not start a comment either",
  );
  pin.match(
    withoutComments('const s = "a \\" // b"; const c = "border-amber-300";'),
    /border-amber-300/,
    "an escaped quote must not end the string early",
  );

  // A regex literal can carry a quote or a slash pair; neither may derail the
  // scan for the rest of the file.
  pin.match(
    withoutComments("const r = /[\"']/g; const c = \"bg-amber-100\";"),
    /bg-amber-100/,
    "a quote inside a regex literal must not open a string that swallows the file",
  );

  // A bare apostrophe in JSX text cannot open a string that runs past the line.
  pin.match(
    withoutComments("<p>don't</p>\nconst c = \"text-amber-700\";"),
    /text-amber-700/,
    "an unterminated quote must end at the newline -- a JS string cannot span one",
  );

  // And it still does its actual job: real comments go.
  pin.doesNotMatch(
    withoutComments('// we removed text-amber-900 here\nconst c = "ok";'),
    /text-amber-900/,
    "a line comment naming the banned class must still be stripped",
  );
  pin.doesNotMatch(
    withoutComments('/* text-amber-900 was here */ const c = "ok";'),
    /text-amber-900/,
    "a block comment naming the banned class must still be stripped",
  );
  // Division is not a regex.
  pin.match(
    withoutComments('const half = total / 2; const c = "text-amber-800";'),
    /text-amber-800/,
    "a division must not be parsed as a regex literal",
  );
});

contractTest("U-1: the ⓘ trigger draws its glyph as geometry, not as a text node", (pin) => {
  const ui = read("components/genesis/ui.tsx");

  // Reported from live review 2026-08-13: the "i" sat below the circle's
  // centre. Flex centring centres a text node's LINE BOX, which is symmetric
  // about the baseline -- but a lowercase "i" has all its ink above the
  // baseline and no descender to balance the ascent, so it lands low by an
  // amount that changes with whichever ko/ja fallback font resolves. A margin
  // nudge would be a constant tuned against one machine's font stack; two
  // shapes in a square viewBox are centred by arithmetic this repo controls.
  pin.match(ui, /<svg viewBox="0 0 16 16"/, "expected the ⓘ glyph to be drawn as SVG geometry");
  pin.match(ui, /<circle cx="8"/, "expected the glyph's dot to be centred on the viewBox's own axis");
  pin.doesNotMatch(
    ui,
    /<span aria-hidden>i<\/span>/,
    "expected the text-node 'i' to be gone -- it is what could not be centred reliably",
  );
});

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
  pin.match(floatingAssistant, /cursor-ew-resize/, "expected the assistant drawer to expose a width-resize handle on its left edge");
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

// Unit #18 -- variant B of the approved flow mockup: the pipeline IS the
// navigation. The pins below describe the SHAPE of that claim (which page sits
// under which stage, and that the stepper cannot know anything the URL does not
// say). Style lives in its own test further down, per the quarantine rule above.
contractTest("#18: the sidebar is the pipeline — selector on top, four numbered groups", (pin) => {
  const shell = read("components/AppShell.tsx");

  // "Fixed on top" is an ORDER claim, so it is pinned as one: everything
  // between the brand link and the nav element, which is where the selector has
  // to be for the mockup's layout to hold.
  const aboveNav = sliceBetween(shell, "app-side-brand", "app-side-nav");
  pin.match(aboveNav, /<ProjectSelector \/>/, "expected the 프로젝트 selector to sit above the navigation, not inside a page");

  const networkGroup = sliceBetween(shell, 'ordinal: "①"', 'ordinal: "②"');
  pin.match(networkGroup, /href: "\/genesis"/, "expected 생성 under ① 네트워크");
  pin.match(networkGroup, /href: "\/network"/, "expected the network view under ① 네트워크");
  // RETIRED (unit #27, backlog item 8, 2026-08-14): 변경 비교 stopped being a
  // row of this group -- the mockup's own "+비교" annotation, user-ordered --
  // and became a mode of /network instead. See "unit #27: 변경 비교 is a mode
  // of /network, not its own route" below for what replaced this pin.
  pin.doesNotMatch(networkGroup, /href: "\/network-diff"/, "expected the diff view's OWN row to be gone from ① 네트워크 -- it is a mode of the network view now");

  // The half of variant B that actually moves something: both screens were
  // reachable only from the genesis hub before this unit.
  const dataGroup = sliceBetween(shell, 'ordinal: "②"', 'ordinal: "③"');
  pin.match(dataGroup, /href: "\/genesis\/demand"/, "expected 수요 promoted to a first-class ② 데이터 entry");
  pin.match(dataGroup, /href: "\/genesis\/phases"/, "expected 현시도 promoted to a first-class ② 데이터 entry");
  // Unit #28: the observation STORE (backend/routers/observations.py) had a
  // complete API and zero frontend consumers before this row.
  pin.match(dataGroup, /href: "\/observations"/, "expected 실측 관측 as a first-class ② 데이터 entry");
  pin.match(dataGroup, /실측 관측/, "expected the observations row to carry its Korean label");

  const simGroup = sliceBetween(shell, 'ordinal: "③"', 'ordinal: "④"');
  pin.match(simGroup, /href: "\/dashboard"/, "expected the dashboard under ③ 시뮬·보정");
  pin.match(simGroup, /href: "\/base-model"/, "expected base-model validation under ③ 시뮬·보정");
  pin.match(simGroup, /href: "\/health"/, "expected the health check under ③ 시뮬·보정");

  const reportGroup = sliceBetween(shell, 'ordinal: "④"', "// Unit P④");
  pin.match(reportGroup, /href: "\/report"/, "expected the report builder under ④ 보고서");
  pin.match(reportGroup, /href: "\/downloads"/, "expected downloads absorbed into ④ 보고서");

  // 사용자 관리 is not a pipeline stage, and an ordinal would claim it is.
  pin.match(shell, /ordinal: null/, "expected the admin group to carry no pipeline ordinal");
});

contractTest("#18: the pipeline stepper is derived from the route and stores nothing", (pin) => {
  const shell = read("components/AppShell.tsx");

  pin.match(
    shell,
    /const ROUTE_STAGE: Readonly<Record<string, PipelineStage>> = \{/,
    "expected a static route->stage table rather than a computed or fetched one",
  );
  pin.match(shell, /"\/genesis\/region": "network"/, "expected the region sub-screen to map to the 네트워크 stage");
  pin.match(shell, /"\/genesis\/demand": "data"/, "expected the demand screen to map to the 데이터 stage");
  pin.match(shell, /"\/observations": "data"/, "expected the observations screen to map to the 데이터 stage");
  pin.match(shell, /"\/base-model": "sim"/, "expected base-model validation to map to the 시뮬·보정 stage");
  pin.match(shell, /"\/downloads": "report"/, "expected downloads to map to the 보고서 stage");
  pin.match(
    shell,
    /<PipelineStepper current=\{stageForPath\(pathname\)\} \/>/,
    "expected the stepper's only input to be the current route",
  );

  // The doctrine pin. A stepper that kept state -- or that had a "done" step --
  // would be asserting progress this app has never measured; the mockup drew
  // exactly that with fake data, and it is the one thing from it not shipped.
  const stepper = sliceBetween(shell, "function PipelineStepper(", "// Unit U9.");
  pin.doesNotMatch(stepper, /useState|useEffect|useRef|localStorage/, "expected the stepper to carry no state of its own");
  pin.doesNotMatch(stepper, /emerald|done|complete/, "expected no 'completed stage' treatment the app cannot measure");
});

// Style pins, quarantined per the rule above.
contractTest("#18: the stepper's current stage uses the sky selection treatment", (pin) => {
  const shell = read("components/AppShell.tsx");
  const stepper = sliceBetween(shell, "function PipelineStepper(", "// Unit U9.");

  // Three-color rule: sky = selection, and the foreground comes from the LIGHT
  // end of the family because globals.css remaps sky-50 to a navy fill.
  pin.match(stepper, /border-sky-200 bg-sky-50 font-semibold text-sky-600/, "expected the current stage to take the sky selection treatment");
  pin.match(stepper, /border-slate-200 text-slate-500/, "expected non-current stages to stay mono");
  pin.doesNotMatch(stepper, /amber|yellow|rose|emerald/, "expected no fourth hue in the stepper");
});

contractTest("#18: the 프로젝트 picker moved to the sidebar, and a switch can never leave a page stale", (pin) => {
  const shell = read("components/AppShell.tsx");
  const selector = read("components/ProjectSelector.tsx");
  const banner = read("components/VissimDatasetBanner.tsx");

  pin.match(shell, /import ProjectSelector from "@\/components\/ProjectSelector"/, "expected the shell to own the project picker");
  pin.doesNotMatch(banner, /fetchWorkspaces|createWorkspace|vissim-workspace-picker/, "expected the picker to be gone from the dataset banner");

  // Moved, not reinvented: the two behaviours that are easy to lose in a move.
  pin.match(selector, /id="vissim-workspace-picker"/, "expected the moved picker to keep its control id");
  pin.match(
    selector,
    /setWorkspaceId\(isDefault \? null : workspaceId\)/,
    "expected the default workspace to keep storing null, so a fresh browser sends no header",
  );

  // A SWITCH IS A FULL RELOAD (adversarial verification, 2026-08-13). The
  // banner's broadcast reaches exactly the pages that subscribe -- and while
  // the picker lived in the banner, those were the only pages a switch could
  // happen on. From the sidebar a switch is possible on EVERY page, and seven
  // pipeline pages load workspace-scoped data on mount with no listener: a
  // broadcast-based switch shows the PREVIOUS project's data under the new
  // project's header. Tearing the page down cannot go stale, for any page,
  // ever -- including the next page nobody wires up. Scoped to the switch
  // handler so a reload anywhere else cannot satisfy the pin.
  const switchHandler = sliceBetween(selector, "const handleSwitchWorkspace", "const handleCreateWorkspace");
  pin.match(
    switchHandler,
    /window\.location\.reload\(\)/,
    "expected a project switch to tear the page down -- the one mechanism no unsubscribed page can be left out of",
  );
  pin.doesNotMatch(
    switchHandler,
    /fetchActiveDataset|publishDatasetChange/,
    "expected the switch NOT to fetch-and-broadcast -- that path only reaches subscribers, which is the stale-data bug the reload closes",
  );

  // The banner's own upload/reset stays in-page and reaches pages EXACTLY
  // once, through the one subscription: not zero (a silent drop) and not twice
  // (a direct call beside the broadcast). Slice-scoped because the refuter
  // proved the file-wide versions of these pins were blind to a duplicated
  // direct call and to a defeated refetch.
  const subscription = sliceBetween(banner, "subscribeToDatasetChange((dataset) => {", "const handleSelectFile");
  pin.match(subscription, /onDatasetChange\?\.\(dataset\)/, "expected pages to receive onDatasetChange from the ONE subscription");
  const runUpload = sliceBetween(banner, "const runUpload = useCallback(", "const handleFileSelected");
  const resetHandler = sliceBetween(banner, "const handleReset = useCallback(", "return (");
  pin.match(runUpload, /publishDatasetChange\(dataset\)/, "expected a finished upload to announce through the shared broadcast");
  pin.doesNotMatch(runUpload, /onDatasetChange/, "expected the upload path to deliver only via the broadcast -- a direct call beside it double-fires every page");
  pin.match(resetHandler, /publishDatasetChange\(dataset\)/, "expected a reset to announce through the shared broadcast");
  pin.doesNotMatch(resetHandler, /onDatasetChange/, "expected the reset path to deliver only via the broadcast");
  pin.doesNotMatch(banner, /new CustomEvent\("vissim-dataset-change"/, "expected the hand-written broadcast copy to be gone from the banner");

  // The busy guard, pinned as an ORDERING rather than substring presence: the
  // refuter proved presence-only pins accept a collapsed ~0ms busy window. The
  // flag must be up before the awaited work starts and come down in the settle
  // path -- and with a switch being a reload, this guard is what stops a
  // mid-upload switch from killing the upload it is scoped to.
  pin.match(runUpload, /setDatasetBusy\(true\);[\s\S]*?try \{[\s\S]*?await /, "expected the busy flag raised BEFORE the upload's awaited work begins");
  pin.match(runUpload, /finally \{[\s\S]*?setDatasetBusy\(false\)/, "expected the busy flag cleared in the settle path, not beside where it was raised");
  pin.match(selector, /subscribeToDatasetBusy/, "expected the picker to stay disabled while an upload is in flight");

  // SELF-HEAL (user hit this live, 2026-08-14): a stored project id the server
  // refuses (deleted project / non-member account) 403s workspace_forbidden on
  // every request, and the picker used to just hide -- a dead end with no way
  // back. The recovery must (a) fire only on the TYPED error, (b) only while a
  // header is actually stored, (c) drop the header and reload, (d) be one-shot
  // per tab so a still-refusing server cannot cause a reload loop.
  pin.match(
    selector,
    /error instanceof WorkspaceAccessError &&\s*getWorkspaceId\(\) &&\s*window\.sessionStorage\.getItem\(WORKSPACE_RECOVERY_FLAG\) === null/,
    "expected the workspace_forbidden recovery to be gated on the typed error AND a stored header AND the one-shot flag",
  );
  pin.match(
    selector,
    /setWorkspaceId\(null\);\s*window\.location\.reload\(\);/,
    "expected recovery to drop the refused header and reload into the default project",
  );
  pin.match(
    selector,
    /window\.sessionStorage\.removeItem\(WORKSPACE_RECOVERY_FLAG\)/,
    "expected a successful listing to re-arm the recovery for a future refusal",
  );
  pin.match(
    read("lib/api.ts"),
    /code === "workspace_forbidden" \|\| code === "workspace_not_found"/,
    "expected fetchWorkspaces to type exactly the two server refusal codes -- other failures must keep hiding the picker, not reloading",
  );
});

// Re-pinned by unit #22 (2026-08-14 directive): the root used to land on the
// dashboard "once assistant is floating" (the prompt-only home screen was
// gone, so the dashboard was the least-wrong default). The new story is the
// dashboard was never the front door, it is stage ③ of the pipeline -- the
// project OVERVIEW is, and it is the one screen that can even tell whether a
// project exists to show.
contractTest("root entry redirects to the project overview, not the dashboard", (pin) => {
  const page = read("app/page.tsx");

  pin.match(page, /redirect/, "expected root route to redirect instead of rendering a prompt-only page");
  pin.match(page, /"\/overview"/, "expected root path to land on the project overview");
  pin.doesNotMatch(page, /"\/dashboard"/, "expected root to no longer redirect straight to the dashboard -- dashboard is stage ③, not the front door");
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

  pin.match(datasetBanner, /ui-button-primary inline-flex items-center px-3 py-2 text-\[14px\]/, "expected dataset upload button to match the Excel upload style");
  // Re-pinned 2026-08-05. This used to expect `ui-button-danger`, matching
  // base-model's Excel reset. The Console retheme (77e9149) deliberately made
  // Restore Default a ghost button while the Excel reset stayed danger, so the
  // two no longer match by design -- the destructive warning moved into
  // ConfirmDialog rather than being carried by the button colour.
  pin.match(datasetBanner, /ui-button-ghost inline-flex items-center px-3 py-2 text-\[14px\]/, "expected dataset reset button to use the ghost style, with the destructive warning carried by ConfirmDialog");
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
// Unit U-2-core — the write-approval card's own rationale + a real reviewer
// reason, closing D4's named gap ("the production approval UI sends no
// `reason` at all ... the DOCX 근거 cell reads '-' for real UI approvals")
// ---------------------------------------------------------------------------

contractTest("U-2-core: the approval card renders the proposal's own rationale verbatim, and names what this flow does not produce", (pin) => {
  const floatingAssistant = read("components/FloatingAssistant.tsx");
  const assistantRouter = readFileSync(path.join(repoRoot, "backend", "routers", "assistant.py"), "utf-8");

  // `action.summary` -- the planner's own step narration -- was already on
  // the wire before this unit (routers/assistant.py's action_entry) but was
  // never rendered on the card itself. This is the new component, and its
  // one job is to print it back unchanged: `{summary}` alone, no wrapping
  // sentence, no re-summarizing call.
  pin.match(
    floatingAssistant,
    /function AgentActionRationale\(\{ summary \}: \{ summary: string \}\) \{/,
    "expected a dedicated component for the proposal's own rationale line",
  );
  pin.match(
    floatingAssistant,
    /<p className="mt-0\.5 text-\[13px\] leading-5 text-slate-700">\{summary\}<\/p>/,
    "expected the rationale to render the raw `summary` value verbatim -- not re-worded or wrapped in extra text",
  );
  pin.match(
    floatingAssistant,
    /<AgentActionDetail action=\{action\} focus=\{approval\.focus\} modelEdited=\{modelEdited\} \/>[\s\S]*?<AgentActionRationale summary=\{action\.summary\} \/>/,
    "expected every action row in the approval card to render its own rationale, right after its from/to detail line",
  );

  // The escape hatch this unit's own brief requires: this chat-driven write
  // path's PlannerDecision carries no confidence value and no populated
  // assumptions (unlike genesis's FixProposal) -- say so in code rather than
  // fabricating a confidence chip or demand-estimate framing this payload
  // cannot support.
  pin.match(
    floatingAssistant,
    /no separate confidence signal exists for it at all/,
    "expected the code to name that this write-approval flow has no confidence signal, rather than silently omitting a chip",
  );
  pin.match(
    floatingAssistant,
    /hardcoded to[\s\S]{0,10}`\[\]` server-side today/,
    "expected the code to name that `assumptions` is real on the wire but always empty for this flow today",
  );

  // The assumptions block itself must still exist -- this unit adds the
  // rationale line; it does not touch the (already-shipped) assumptions
  // renderer, which is exactly what stops populating whenever
  // AgentApproval.assumptions above finally does.
  pin.match(
    floatingAssistant,
    /\{approval\.assumptions\.length > 0 && \(/,
    "expected the pre-existing assumptions block to remain, for when the wire shape starts populating it",
  );

  // Confirms this is honestly sourced, not invented: the wire-shape comment
  // this unit's rationale relies on is still the router's own, unchanged.
  pin.match(
    assistantRouter,
    /No structured "assumption" signal exists yet from the planner/,
    "expected the backend's own comment confirming assumptions stays [] for this flow (source for the frontend's explanatory comment)",
  );
});

contractTest("U-2-core: the reviewer's optional reason reaches the SAME approve/decline POST, capped to the backend's own limit", (pin) => {
  const floatingAssistant = read("components/FloatingAssistant.tsx");
  const api = read("lib/api.ts");
  const assistantRouter = readFileSync(path.join(repoRoot, "backend", "routers", "assistant.py"), "utf-8");

  // Source of truth for the cap: the backend's pydantic field 422s past this
  // number, so the frontend's own cap must equal it exactly -- not merely
  // "close enough" -- or a reason under the frontend's cap could still 422.
  pin.match(assistantRouter, /MAX_APPROVAL_REASON_CHARS = 600/, "expected the backend's approval-reason cap to still be 600 (frontend cap below is pinned to this exact number)");
  pin.match(floatingAssistant, /const APPROVAL_REASON_MAX_CHARS = 600;/, "expected the frontend's approval-reason cap to equal the backend's MAX_APPROVAL_REASON_CHARS");

  // The wire type: additive-only, optional, matches what AgentApproveRequest
  // already accepted server-side (Phase 1.3) but no client ever sent.
  const approveRequestType = sliceBetween(api, "export interface AgentApproveRequest {", "export interface AssistantSource {");
  pin.match(approveRequestType, /reason\?: string;/, "expected AgentApproveRequest to carry the optional reviewer reason");

  // Double enforcement against the cap -- native maxLength AND a defensive
  // slice on every keystroke -- so pasted text can never put more than 600
  // chars into component state, and therefore never into the POST body:
  // this is the concrete prevention for "a reason that silently exceeds the
  // cap and 422s".
  pin.match(floatingAssistant, /maxLength=\{APPROVAL_REASON_MAX_CHARS\}/, "expected the reason <input> to cap input length natively");
  pin.match(
    floatingAssistant,
    /onChange=\{\(event\) => setReason\(event\.target\.value\.slice\(0, APPROVAL_REASON_MAX_CHARS\)\)\}/,
    "expected a defensive slice to the cap on every keystroke, independent of the input's own maxLength",
  );
  // The live counter near the limit, and its emphasis as the value
  // approaches the cap. Retargeted by U-2 (the drawer's Console-vocabulary
  // pass, 2026-08-14): this used to reuse the card's amber-for-warning
  // color, which the app settled as banned app-wide (dashboard-contract.test.ts's
  // "U-1: three accent colours" test, 2026-08-13 -- rose/emerald/sky are the
  // only three accents). Nearing the cap is told apart by WEIGHT now, never a
  // fourth hue.
  pin.match(
    floatingAssistant,
    /\{reason\.length\}\/\{APPROVAL_REASON_MAX_CHARS\}/,
    "expected a live character counter next to the reason input",
  );
  pin.match(
    floatingAssistant,
    /reasonNearCap \? "font-semibold text-slate-800" : "text-slate-400"/,
    "expected the counter to gain weight (never a color) as the reason nears the cap",
  );
  pin.doesNotMatch(
    floatingAssistant,
    /reasonNearCap[\s\S]{0,10}amber/,
    "the near-cap counter must never reintroduce amber -- three-color rule",
  );

  // The reason reaches BOTH Run and Cancel -- the backend records it into
  // the ledger regardless of outcome (_record_approval_decision), so the UI
  // must not silently drop it on the decline path.
  pin.match(
    floatingAssistant,
    /onClick=\{\(\) => onRun\(reason\.trim\(\) \|\| undefined\)\}/,
    "expected the Run button to send the reviewer's trimmed reason",
  );
  pin.match(
    floatingAssistant,
    /onClick=\{\(\) => onCancelApproval\(reason\.trim\(\) \|\| undefined\)\}/,
    "expected the Cancel button to send the reviewer's trimmed reason too -- the backend records a reason on decline exactly like approve",
  );
  pin.match(
    assistantRouter,
    /reason=\(payload\.reason or ""\)\.strip\(\) or None,/,
    "expected the backend to still record `reason` regardless of approved/declined (source for the Cancel-path pin above)",
  );

  // The reason travels all the way from the card's local state, through
  // FloatingChatMessage's callback, to the actual POST body -- not lost at
  // any hop in between.
  pin.match(
    floatingAssistant,
    /onRun=\{\(reason\) => onResolveApproval\(true, reason\)\}/,
    "expected the card's Run callback to forward the reason up",
  );
  pin.match(
    floatingAssistant,
    /onResolveApproval: \(approved: boolean, reason\?: string\) => void;/,
    "expected FloatingChatMessage's resolve callback type to carry the optional reason",
  );
  pin.match(
    floatingAssistant,
    /if \(message\.agentApproval\) void runAgentApproval\(message\.id, message\.agentApproval, approved, reason\);/,
    "expected the top-level handler to forward the reason into runAgentApproval",
  );
  pin.match(
    floatingAssistant,
    /\{ session_id: approval\.sessionId, approval_id: approval\.approvalId, approved, reason \}/,
    "expected the actual approve/decline POST body to carry the reason",
  );
});

// ---------------------------------------------------------------------------
// Unit U-2 — the assistant drawer's Console-vocabulary pass: one
// authoritative approval surface, and the three-color rule enforced inside
// the drawer.
// ---------------------------------------------------------------------------

contractTest("U-2: an approval renders on exactly one surface -- the card, never the step log", (pin) => {
  const floatingAssistant = read("components/FloatingAssistant.tsx");

  // THE REMOVED DUPLICATE: AgentStepNarration used to print its own
  // "(승인 대기)" status chip on a "pending_approval" step, restating the
  // exact fact AgentApprovalCard states as its own headline a few lines
  // below in the same message. `withoutComments` is required -- this unit's
  // own doc comment on AgentStepNarration legitimately explains the removed
  // pattern while naming it, and a raw doesNotMatch would fire on that
  // explanation forever.
  pin.doesNotMatch(
    withoutComments(floatingAssistant),
    /step\.status === "pending_approval"/,
    "single-surface approval rule: a step must not branch on pending_approval to render its own status chip -- AgentApprovalCard is the one authoritative surface for an approval's state",
  );
  // The positive half: the step's own summary text still renders (the
  // narration is not deleted, only its duplicate status suffix is), and the
  // "error" suffix -- a DIFFERENT concern (a failed step, not a paused one) --
  // survives untouched.
  pin.match(
    floatingAssistant,
    /\{index \+ 1\}\. \{step\.summary\}/,
    "expected the step narration to keep its own per-step summary line",
  );
  pin.match(
    floatingAssistant,
    /step\.status === "error" && \(/,
    "expected the step narration's error suffix to survive -- it is a different concern from an approval's status",
  );
  // The card remains the single place an approval's state is announced.
  // `withoutComments` is required: this unit's own doc comment on
  // AgentStepNarration (above) legitimately quotes this exact headline while
  // explaining what moved, and a raw match would pass even if the LIVE
  // headline were deleted, off the comment alone.
  pin.match(
    withoutComments(floatingAssistant),
    /실행 전 확인이 필요합니다/,
    "expected AgentApprovalCard's own headline to remain the one place an approval's pending state is announced",
  );
});

contractTest("U-2: the drawer's Console-vocabulary pass -- three-color rule, unified selection chips, 13px floor", (pin) => {
  const floatingAssistant = read("components/FloatingAssistant.tsx");

  // THREE-COLOR RULE: amber was never one of the three accents (rose/emerald/
  // sky -- dashboard-contract.test.ts's "U-1: three accent colours" test,
  // 2026-08-13), and this drawer carried seven amber call sites before this
  // pass. `withoutComments` because this file's own doc comments now narrate
  // the removed amber usages while explaining the fix.
  pin.doesNotMatch(
    withoutComments(floatingAssistant),
    /(?:border|bg|text|ring|from|via|to)-amber-/,
    "the assistant drawer must carry no amber-family class -- warn/notice states render mono with a heavier hairline",
  );
  // ...and no raw Tailwind blue either: links, chips and "active" fills take
  // either the app's sky selection/active accent or the neutral ink ramp,
  // never a fourth ad-hoc hue that is neither.
  pin.doesNotMatch(
    withoutComments(floatingAssistant),
    /(?:border|bg|text|ring|from|via|to)-blue-\d/,
    "the assistant drawer must carry no raw Tailwind blue -- selection/active takes sky, everything else mono",
  );

  // DEDUPED SELECTION CHIP: the header's page-context pills and the
  // welcome-screen summary card used to render the SAME selection (dataset,
  // scenarios, nodes, links, elements) two different ways -- one mono, one
  // blue. Both now share one constant, so they cannot drift apart again.
  const selectionChipUseCount = (floatingAssistant.match(/className=\{SELECTION_CHIP_CLASS\}/g) ?? []).length;
  pin.match(
    `count=${selectionChipUseCount}`,
    /^count=2$/,
    "expected both page-context chip renderers (the header pills and the welcome-screen summary) to share the one SELECTION_CHIP_CLASS constant",
  );
  pin.match(
    floatingAssistant,
    /const SELECTION_CHIP_CLASS = "rounded-full border border-sky-200 bg-sky-50 px-2 py-0\.5 text-\[13px\] font-semibold text-sky-600";/,
    "expected the shared selection chip to use the app's established sky selection accent (AppShell's own step-indicator recipe)",
  );

  // TYPE FLOOR: unit #25/#26 raised the app-wide floor to 13px -- nothing
  // under it may survive inside the drawer.
  pin.doesNotMatch(
    withoutComments(floatingAssistant),
    /text-\[(?:[1-9]|10|11|12)(?:\.\d+)?px\]/,
    "no text size under the 13px app-wide floor may survive inside the assistant drawer",
  );
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
  // MOVED (unit #27, backlog item 8, 2026-08-14): this used to be
  // app/network-diff/page.tsx. It is components/NetworkDiffView.tsx now, the
  // body transplanted verbatim, which is why every pin below still reads
  // unchanged -- "the page" in their messages means this component's markup,
  // exactly as it did when it was the whole page.
  const page = read("components/NetworkDiffView.tsx");
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

  // --- shell -----------------------------------------------------------
  // RETIRED (unit #27, 2026-08-14): the diff view no longer has its own
  // top-nav row or nav icon -- see "unit #27: 변경 비교 is a mode of /network,
  // not its own route" below for what replaced these two pins.
});

contractTest("2.4: every diff-view string carries its own Japanese", (pin) => {
  // pickByLanguage keys ja off the KOREAN string (lib/i18n.ts), so a
  // two-argument t(ko, en) with no entry in the GENERATED lib/i18n-ja.ts
  // silently renders English for Japanese users -- the same hazard the login
  // page's pin above covers by checking the dictionary. This page takes the
  // other road: every call is the three-argument form, so no string on it
  // depends on a generated file at all. Pinned so a later edit cannot quietly
  // drop back to two arguments and lose ja.
  //
  // MOVED (unit #27, 2026-08-14): components/NetworkDiffView.tsx now, not
  // app/network-diff/page.tsx -- see the sibling "2.4" contractTest above.
  const page = read("components/NetworkDiffView.tsx");

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

// ---------------------------------------------------------------------------
// Unit #27 -- backlog item 8's last half: 변경 비교 stops being its own sidebar
// entry and becomes a MODE of /network (the mockup's own "+비교" annotation,
// user-ordered 2026-08-14). The diff view's own machinery (endpoint, tokens,
// POSITION doctrine) is unchanged and re-pinned above, verbatim, against its
// new home components/NetworkDiffView.tsx -- everything below is the NEW
// surface: the mode toggle, the URL, and the redirect that keeps old
// /network-diff links working.
// ---------------------------------------------------------------------------

contractTest("unit #27: 변경 비교 is a mode of /network, not its own route", (pin) => {
  // app/network/page.tsx is CRLF (same quirk as app/dashboard/page.tsx --
  // see the block comment further down this file); normalize before matching
  // multi-line patterns so a line-ending quirk cannot itself decide the pin.
  const networkPage = read("app/network/page.tsx").replace(/\r\n/g, "\n");
  const redirectPage = read("app/network-diff/page.tsx");
  const shell = read("components/AppShell.tsx");

  // --- the mode lives in the URL, not in component state -------------------
  pin.match(networkPage, /type NetworkMode = "network" \| "diff";/, "expected an explicit two-state mode type");
  pin.match(
    networkPage,
    /const mode: NetworkMode = searchParams\.get\(NETWORK_MODE_PARAM\) === "diff" \? "diff" : "network";/,
    "expected the mode to be READ from the URL every render, not mirrored into its own useState",
  );
  pin.match(
    networkPage,
    /router\.push\(query \? `\/network\?\$\{query\}` : "\/network"\);/,
    "expected switching modes to push a new URL, so the browser's back/forward can move between them",
  );

  // --- the pills themselves --------------------------------------------------
  pin.match(networkPage, /function NetworkModeToggle\(/, "expected a dedicated mode-pill component");
  pin.match(networkPage, /aria-pressed=\{active\}/, "expected the mode pills to be real aria-pressed toggle buttons");
  pin.match(
    networkPage,
    /active \? "bg-sky-50 text-sky-600" : "text-slate-500 hover:text-slate-800"/,
    "expected the selected pill to take the sky selection treatment PipelineStepper already established -- no fourth hue",
  );
  pin.match(
    networkPage,
    /<NetworkModeToggle mode=\{mode\} onChange=\{setMode\} \/>/,
    "expected the toggle to actually be wired into the page header, not just defined",
  );

  // --- 비교 mode renders NetworkDiffView in place of the viewer body --------
  pin.match(
    networkPage,
    /mode === "network" \? \(/,
    "expected the viewer body (diagram, focus banner, selection chips) to be conditional on mode",
  );
  pin.match(
    networkPage,
    /\) : \(\s*<NetworkDiffView \/>\s*\)/,
    "expected 비교 mode to swap in NetworkDiffView where the viewer body was",
  );

  // --- NetworkDiffView has exactly one consumer -----------------------------
  // (plus the redirect below, which deliberately does NOT import it -- a
  // redirect that rendered the view itself would be a second consumer and
  // would defeat the whole point of moving the fetch-triggering component
  // behind a real navigation).
  pin.match(
    networkPage,
    /import NetworkDiffView from "@\/components\/NetworkDiffView";/,
    "expected /network to be the one screen that imports the diff view",
  );
  // (A comment on the redirect page NAMES the component for orientation --
  // this checks for an actual import or JSX usage, not the word.)
  pin.doesNotMatch(
    redirectPage,
    /import\s+NetworkDiffView|<NetworkDiffView/,
    "expected the /network-diff redirect to import nothing from the diff view -- it forwards the BROWSER, not the component",
  );

  // --- /network-diff itself is a plain client redirect ----------------------
  pin.match(redirectPage, /router\.replace\("\/network\?mode=diff"\);/, "expected /network-diff to redirect into the network view's diff mode");
  pin.doesNotMatch(
    redirectPage,
    /fetchNetworkDiff|fetchAgentDownloads/,
    "expected the redirect page to fetch nothing itself -- all of that lives in NetworkDiffView now",
  );

  // --- the redirect target still gets an honest pipeline stage --------------
  pin.match(shell, /"\/network-diff": "network",/, "expected ROUTE_STAGE to still map the redirect route while it is in flight");
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
  // unit #23: the verdict grid (and everything else the drawer carried) moved
  // off the hub and into its own route when the drawer became a detail page.
  const page = read("app/genesis/jobs/[id]/page.tsx");
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
  // unit #23: `statusClasses` (the switch these four cases live in) moved to
  // lib/genesisArtifacts.ts, alongside `statusLabel`, once BOTH the hub's
  // history row and this detail page needed to colour a job's status.
  for (const status of ["refused", "provisional", "published", "error"]) {
    pin.match(genesisLib, new RegExp(`case "${status}":`), `expected the ${status} job state to be named in the shared status helpers`);
  }
  pin.match(genesisLib, /잠정 등록 — 미확인 검사 있음/, "expected the provisional label to state that a check could not run");
  pin.match(genesisLib, /return t\("거부됨", "REFUSED", "拒否"\)/, "expected the refused label to be the verdict word alone (user 2026-08-14) -- the not-registered consequence lives in the detail help drawer");

  // Every gate sentence comes from the backend payload (gate.py's own ko/en/ja
  // table), never from a dictionary in this file.
  pin.match(page, /\{check\.title\}/, "expected the check title to come from the gate payload");
  // RE-AIMED, unit #41: `CollapsibleMessages` now takes the FINDINGS rather
  // than a pre-mapped array of message strings (it needs each finding's own
  // identity to sort proposals first, badge them, and make a placeable row a
  // map control). What this pin has always guarded is unchanged and is now
  // asserted where it actually happens: the check's own findings array goes in
  // untouched, and the sentence that comes out is `finding.message`, verbatim.
  pin.match(
    page,
    /findings=\{selected\.findings\}/,
    "expected the gate's own findings array to be handed to the list untouched -- no client-side filtering of what the gate said",
  );
  // RE-AIMED, unit #41-b: the row prints the gate's own sentence MINUS the
  // subject the same row already printed above it -- gate.py words many
  // findings as "<subject>: <what is wrong>", and leading with `SubjectLabel`
  // (unit #41) made those rows print the road twice
  // ("service[FWD] w1294917882s0service [FWD] w1294917882s0: 모델 경계가…").
  // What this pin has always guarded is unchanged: the sentence is the
  // payload's, passed through a trim that can only remove an exact leading copy
  // of the subject -- never a client-authored replacement, and never a
  // rewording. The trim's own boundary rule is pinned separately below.
  pin.match(
    page,
    /\{messageWithoutSubject\(row\.finding\.subject, row\.finding\.message\)\}/,
    "expected finding text to still be the gate payload's own sentence -- the fold, the unit #41 reorder and the unit #41-b subject trim pass messages through, they do not rewrite them",
  );
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

  // END MARKER CO-EDITED by U-1-quick: this slice used to end at
  // `export function genesisArtifactUrl`, which no longer exists -- that
  // function built a bare `<a href>` URL for the artifact route and was the bug
  // reported as "all five artifact buttons do nothing" (see the U-1-quick
  // download test below for the three mechanisms). The slice now ends at the
  // region block's first export, which is the next stable landmark; everything
  // this test asserts about the genesis bindings is still inside it, and the
  // slice additionally now covers `fetchGenesisArtifact`, which the
  // "must not bypass the workspace-header fetch helpers" pin below applies to.
  const genesisSection = sliceBetween(api, "export type GenesisJobStatus", "export type RegionPolygonVertices");

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
//
// UX pass U-1 (2026-08-13) went one better on app/genesis/page.tsx's 생성 시작
// button specifically: it now uses `.ui-button-primary`, globals.css's own
// sanctioned primitive, which carries the correct foreground for the inverted
// palette (and its own `:disabled` opacity) instead of a hand-rolled
// bg-slate-900/text-ink/disabled:bg-slate-300 triple. The pin below is
// unchanged and still guards the other three pages' hand-rolled CTAs.
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
  // unit #23: the drawer's own primary CTA (적용 승인) moved to its own route --
  // swept here too so the sweep does not stop covering it just because it
  // changed address.
  pin.doesNotMatch(
    read("app/genesis/jobs/[id]/page.tsx"),
    violation,
    "expected the detail route's 적용 승인 button not to pair bg-slate-900 with text-white (renders ivory-on-ivory; use text-ink)",
  );
});

// ---------------------------------------------------------------------------
// UX pass U-1 (docs/ux-backlog.md) -- progressive disclosure on the genesis
// screens. These are NOT layout pins: what they hold is the doctrine that
// survived the density pass. "Nothing is silently dropped" is this product's
// whole thesis, and reducing what a screen shows is only allowed when the
// reduction is a COLLAPSE with a click path back and when a fault is still
// visible without one.
// ---------------------------------------------------------------------------

// REWRITTEN TWICE, and the second rewrite is a USER DECISION worth stating.
//
// v1 ("the R1.5 checklist collapses by default and opens itself by severity")
// pinned `checkAutoOpens`, `aria-expanded` and a per-row `useState` -- nine
// independently-collapsible rows.
// v2 (U-1-quick, item #4) turned those rows into a 3x3 grid with ONE detail
// panel, and kept severity as an ORDER: `checkRank`/`worstCheckId` chose which
// single cell was already open on arrival.
// v3 (2026-08-13, docs/ux-backlog.md SECOND ELABORATION item 12) is this one.
// After testing v2 live the user asked for NO microcopy, NO checkmark and NO
// auto-select: every cell starts closed and the detail area renders nothing
// until a click. `checkRank` and `worstCheckId` are DELETED, and their pins
// with them.
//
// SO WHAT STILL CARRIES THE DOCTRINE, now that nothing opens itself? The rule
// was never "a panel must be open"; it was "a check that FAILED or could not
// RUN must be visible without reading and without clicking". That now lives
// entirely on the cells, and this test holds exactly those two properties:
//
//   * every cell prints its id AND its tri-state WORD with no click at all --
//     실패 / 확인 불가 are words on screen, never a colour a reader has to
//     decode;
//   * the state CHIP is visibly not the pass chip whether or not anything is
//     selected: rose for a fail, a DOUBLED mono border for 확인 불가. This is
//     load-bearing here and almost nowhere else: globals.css remaps the amber
//     family to NEUTRAL, so `not_checkable`'s fill alone renders grey -- the
//     chip's weight is the only thing standing between "could not run" and
//     "nothing to report". (The user's second live pass, 2026-08-13, removed
//     the CELLS' own state ring: two thicknesses of white card border read as
//     two kinds of card. The cards are identical now; the chip carries all of
//     it.)
//
// What was given up is only that severity no longer decides what the reader is
// LOOKING AT. That is a presentation choice the user made; it drops no claim.
contractTest("U-1: the R1.5 grid starts with every cell closed, and severity still reads off the cells", (pin) => {
  // unit #23: GateCheckGrid moved to the detail route with the rest of the
  // drawer's content.
  const page = read("app/genesis/jobs/[id]/page.tsx");
  const gridBody = sliceBetween(page, "function GateCheckGrid(", "/** The five downloadable artifacts");
  pin.match(gridBody, /\S/, "expected to find the GateCheckGrid body (markers moved? every doesNotMatch below would pass vacuously on empty input)");
  // The two helpers that own every class a cell wears. Sliced apart from the
  // grid on purpose: the point of the 2026-08-13 change is that the grid's own
  // markup names no colour at all, so "the tint is gone" and "the tint moved
  // here" are two different assertions against two different regions.
  const cellStyling = sliceBetween(page, "function checkStateTagClasses(", "function verdictLabel(");
  pin.match(cellStyling, /\S/, "expected to find the cell/chip styling helpers (markers moved?)");

  // (1) NOTHING IS SELECTED ON MOUNT. The initial state is null and stays null
  // until a click -- asserted on the state initializer itself, because a
  // component that seeded it from anything else is exactly what v2 was.
  pin.match(
    gridBody,
    /useState<string \| null>\(null\)/,
    "expected the grid's selection to start as null -- the user asked for every cell closed on arrival",
  );
  pin.doesNotMatch(
    gridBody,
    /useState\(\(\) =>/,
    "expected no computed initial selection -- a lazy initializer here is how v2 auto-opened a cell",
  );
  // The detail is INSIDE a `selected &&` guard, so an unclicked grid renders no
  // detail markup at all rather than an empty styled box.
  pin.match(
    gridBody,
    /\{selected && \(/,
    "expected the detail area to render nothing at all until a cell is clicked",
  );

  // (2) THE SEVERITY MACHINERY IS GONE, not merely unused. Leaving `checkRank`
  // beside a grid that no longer calls it is leaving the next reader to
  // discover which of the two is live.
  pin.doesNotMatch(page, /function checkRank\(/, "expected the severity RANK to be deleted with the auto-select it existed for");
  pin.doesNotMatch(page, /function worstCheckId\(/, "expected the worst-check chooser to be deleted along with checkRank");
  pin.doesNotMatch(page, /function GateCheckRow\(/, "expected the per-row disclosure component to stay gone");
  pin.doesNotMatch(
    page,
    /aria-expanded=\{open\}/,
    "expected no per-check expand toggle to survive -- the grid has one detail area, not nine",
  );

  // (3) SEVERITY IS STILL READABLE WITHOUT A CLICK -- the half of the doctrine
  // that must never move. Id + tri-state word on every cell, and a chip whose
  // weight/hue is visibly not the pass chip on every non-pass cell.
  pin.match(page, /grid grid-cols-3/, "expected the nine checks to be laid out as a 3-column grid");
  pin.match(
    gridBody,
    /\{check\.id\}[\s\S]{0,300}checkStateLabel\(check\.state, t\)/,
    "expected the id and the tri-state word to be visible on EVERY cell without a click",
  );
  pin.match(gridBody, /aria-pressed=\{isSelected\}/, "expected each cell to expose its selected state to assistive tech");

  // (3a) COLOUR LIVES ON THE CHIP, NOT ON THE CARD (user, live review
  // 2026-08-13). Nine tinted cards read as a block of colour to decode rather
  // than nine verdicts to scan, and the tint outweighed the two things that
  // carry the meaning -- the id and the state word. The card is mono for every
  // state; the small chip that already prints the word carries the hue.
  pin.match(page, /function checkStateTagClasses\(/, "expected ONE helper owning the state chip's colour");
  pin.match(page, /function checkCellClasses\(/, "expected a separate helper for the (mono) card");
  pin.doesNotMatch(
    page,
    /function checkStateClasses\(/,
    "expected the merged card+chip helper to be gone -- one function returning both is how the tint got onto the card",
  );
  pin.doesNotMatch(
    gridBody,
    /bg-(?:emerald|rose|sky)-/,
    "the grid's own markup must carry NO tint -- every colour it shows comes through checkStateTagClasses",
  );
  pin.match(
    gridBody,
    /checkCellClasses\(isSelected\)/,
    "expected the cell card to take the mono card helper -- and to take ONLY the selection, since state no longer changes the card",
  );
  pin.match(
    gridBody,
    /checkStateTagClasses\(check\.state\)/,
    "expected the cell's state chip to be the thing that carries the colour",
  );
  // ...and the DETAIL below the grid follows the same rule, so one state cannot
  // be grey in the cell and green in its own detail.
  pin.match(
    gridBody,
    /checkStateTagClasses\(selected\.state\)/,
    "expected the detail's chip to carry the colour, matching the cells",
  );

  // (3b) THE CARDS ARE IDENTICAL FOR EVERY STATE (user, second live pass
  // 2026-08-13): the non-pass ring made cell borders come in two thicknesses of
  // white. With it gone, the pre-reading signal lives ENTIRELY on the chip --
  // so the chip's doubled mono border for 확인 불가 is now itself load-bearing
  // and pinned, and the cell helper must not read the state at all.
  pin.doesNotMatch(
    cellStyling,
    /ring-1 ring-slate-400/,
    "expected the cells' state ring to be gone -- every card wears the pass card's hairline, per the user's live instruction",
  );
  pin.match(
    cellStyling,
    /function checkCellClasses\(isSelected: boolean\)/,
    "expected the card helper to take ONLY the selection -- a state parameter here is the road back to state-driven card borders",
  );
  pin.match(
    cellStyling,
    /"border-2 border-slate-400 bg-slate-100 text-slate-600"/,
    "expected 확인 불가's chip to keep its doubled mono border with GREY ink (user: full-strength ink outshouted the two hues) -- the border weight, not brightness, is what keeps 'could not run' from reading as fine",
  );

  // (4) NO MICROCOPY ON THE CELLS. The tier line ("거부 등급"/"경고 등급") used
  // to be printed on every cell AND in the detail; it now appears once, in the
  // detail. Counted rather than pattern-matched, so re-adding it to the cell
  // fails here even though the string legitimately survives.
  const tierPhrases = (gridBody.match(/t\("거부 등급", "refuse tier", "拒否ティア"\)/g) ?? []).length;
  pin.match(
    `count=${tierPhrases}`,
    /^count=1$/,
    "expected the refuse/warn tier line exactly once (in the detail) -- the user asked for no microcopy on the cells",
  );

  // (5) THE CLICK HAS AN ANSWER: one fill step, a 2px ring, a brighter title,
  // over ~100ms.
  pin.match(cellStyling, /ring-2 ring-slate-500/, "expected the selected cell to take a 2px ring");
  pin.match(gridBody, /duration-100/, "expected the ~100ms transition the user asked for on the active cell");
  pin.match(
    gridBody,
    /isSelected \? "font-semibold text-slate-900"/,
    "expected the selected cell's title to brighten by one step",
  );
  pin.match(
    gridBody,
    /grid grid-cols-3 gap-3\.5/,
    "expected the widened (~14px) cell gaps -- the 6px grid read as one block of colour",
  );

  // (6) GREEN ON GREEN, the contrast bug (SECOND ELABORATION item 11). The dark
  // remap in globals.css re-points `bg-emerald-50` to a DARK green (#1e2b22)
  // but never remapped `text-emerald-900`, which stayed Tailwind's default
  // #064e3b -- a dark green on a dark green. The foreground must come from the
  // remapped LIGHT end of the family. Same trap for sky (`text-sky-700+` over
  // `bg-sky-50` #032042), which is why the status chip is pinned too.
  // `withoutComments`, because this page NAMES the banned class in the doc
  // comment that explains the mechanism -- the pin is about live markup.
  const pageMarkup = withoutComments(page);
  pin.doesNotMatch(
    pageMarkup,
    /text-emerald-900/,
    "the emerald-900 foreground is NOT remapped for the dark theme -- over bg-emerald-50 it renders green-on-green (the reported contrast bug)",
  );
  pin.doesNotMatch(
    pageMarkup,
    /text-sky-(?:700|800|900)/,
    "sky's 700/800/900 foregrounds are NOT remapped either -- over bg-sky-50 (#032042) they render blue-on-blue",
  );
  pin.match(
    page,
    /if \(state === "pass"\) return "border-emerald-300 bg-emerald-50 text-emerald-800";/,
    "expected the PASS chip to keep its green tint in background+border and take an INK-strength foreground from the remapped end",
  );

  // (7) ONE detail region, and it is a live region that is MOUNTED even when
  // empty -- an aria-live element that appears together with its content is an
  // element no screen reader was watching when the content arrived.
  const liveRegions = (gridBody.match(/aria-live="polite"/g) ?? []).length;
  pin.match(`count=${liveRegions}`, /^count=1$/, "expected exactly one detail region under the grid");
  pin.match(
    gridBody,
    /<div aria-live="polite">\s*\n\s*\{selected &&/,
    "expected the live region to be mounted around the conditional detail, not conditionally rendered with it",
  );

  // (8) A clean pass still names its emptiness rather than showing a blank
  // panel -- unchanged from v2, and the reason the detail is not simply hidden
  // for a check with nothing recorded.
  pin.match(
    page,
    /이 검사에 기록된 항목이 없습니다/,
    "expected an empty check to name its emptiness when selected, not render a blank panel",
  );
});

// ---------------------------------------------------------------------------
// U-1 unit #13 -- the Console split (docs/ux-backlog.md, SECOND ELABORATION
// items 9, 10, 14 and the LIST/MASTER-DETAIL reference paragraphs).
// ---------------------------------------------------------------------------

// RETIRED (unit #23, 2026-08-14): "U-1: the genesis page is a list/detail
// split with ONE verdict header". The floor plan this test pinned -- a
// ~46%-wide right column that opened beside the history table when a row was
// clicked -- no longer exists. That is the point of this unit (user decision,
// docs/product/mockups/genesis-detail-layout-mockup-2026-08-14.html option
// D): the drawer's real workflow needed the map wide, which a 46% column
// could never give it, so every generation now gets its own route instead of
// an in-page selection. Every BEHAVIOUR this test held onto survives below,
// split across the two tests that replace it (the hub is a list with a modal;
// the detail route holds the merged verdict header, the split and the fix
// flow) -- nothing here was dropped silently. What did NOT survive, because
// the concept it named stopped existing rather than moving: `selectedId`,
// the auto-select guard, the `{!selected ? (` nothing-selected branch and its
// quiet empty-state line, and `aria-selected` on a row (a row is a link now,
// not a local selection).

contractTest("unit #23: the genesis hub is a history list with one primary action opening a modal", (pin) => {
  const page = read("app/genesis/page.tsx");

  // THE TABLE survives verbatim -- the user's LIST REFERENCE names its five
  // columns; a history row that lost one of them is a row that stopped
  // distinguishing two generations of the same city (U-1-quick #5).
  for (const [ko, en] of [
    ["이름", "Name"],
    ["생성일", "Created"],
    ["영역", "Region"],
    ["규모", "Scale"],
    ["판정", "Verdict"],
  ]) {
    pin.match(
      page,
      new RegExp(`scope="col"[\\s\\S]{0,120}t\\("${ko}", "${en}"`),
      `expected the history table to keep its ${ko}/${en} column header`,
    );
  }
  // Borderless rows: separation comes from row height, not from a rule between
  // every pair -- the reference design's own choice, and what stops ten rows
  // reading as a grid of boxes.
  const historyRows = sliceBetween(page, "<tbody>", "</tbody>");
  pin.match(historyRows, /\S/, "expected to find the history table body (markers moved?)");
  pin.doesNotMatch(
    historyRows,
    /border-b/,
    "expected borderless history rows -- the LIST REFERENCE puts the separation in the row height",
  );
  pin.match(
    page,
    /const HISTORY_PAGE_SIZE = 10;/,
    "expected pagination once the list grows past a page, as the LIST REFERENCE asks",
  );

  // NEW (unit #23): a row click is a real navigation, not a local selection --
  // matched in both the click and the keyboard (Enter/Space) handler, so the
  // row stays operable without a mouse.
  const rowNavigations = (historyRows.match(/router\.push\(`\/genesis\/jobs\/\$\{job\.id\}`\)/g) ?? []).length;
  pin.match(
    `count=${rowNavigations}`,
    /^count=2$/,
    "expected the history row to navigate to its own detail route from both the click and the keyboard handler",
  );

  // NEW (unit #23): ONE primary action, top-right of the page header, that
  // OPENS a dialog rather than starting anything itself -- the Console list
  // pattern's "one control" rule, now applied at the page level so the list
  // panel's own action slot can stay reserved for pagination.
  pin.match(
    page,
    /onClick=\{\(\) => setModalOpen\(true\)\}[\s\S]{0,80}className="ui-button-primary/,
    "expected ONE primary 새 생성 button that opens the modal, styled as this app's one sanctioned primary CTA",
  );
  const primaryTriggers = (page.match(/setModalOpen\(true\)/g) ?? []).length;
  pin.match(`count=${primaryTriggers}`, /^count=1$/, "expected exactly one control that can open the new-generation modal");

  // NEW (unit #23): the modal reuses the shared dialog primitive -- Escape
  // and backdrop-click are ModalDialog's OWN job (components/genesis/ui.tsx,
  // checked in the dedicated ModalDialog test below), so this page only needs
  // to prove it is actually wired to that component, not a bespoke overlay.
  pin.match(page, /import \{ ModalDialog, refusalHeadline \} from "@\/components\/genesis\/ui";/, "expected the hub to take its dialog from the shared primitive, not a bespoke overlay (Panel left the import when the list lost its card chrome, PageHelp when the ? drawer retired to /docs -- user 2026-08-14)");
  pin.match(
    page,
    /<ModalDialog\s*\n\s*open=\{modalOpen\}\s*\n\s*onClose=\{\(\) => setModalOpen\(false\)\}/,
    "expected the new-generation dialog to be driven by the page's own open/close state, not an uncontrolled one",
  );

  // The form itself -- file input, exclude-service checkbox, both dropdowns
  // and the submit button -- moved INTO the dialog's children, whole. Checked
  // by proving the submit control is textually inside the ModalDialog block
  // (open marker to the file's end, since this is the last JSX in the file).
  const modalBody = page.slice(page.indexOf("<ModalDialog"));
  pin.match(modalBody, /accept=".osm,.xml"/, "expected the file input to live inside the modal");
  pin.match(modalBody, /checked=\{excludeService\}/, "expected the exclude-service checkbox to live inside the modal");
  pin.match(modalBody, /생성 시작/, "expected the submit button to live inside the modal");

  // NEW (unit #23): the list stays live while anything on it is still
  // running, since the per-job poll that used to do this lives on the detail
  // route now and this page does not open it.
  pin.match(
    page,
    /if \(!jobs\.some\(\(job\) => !isTerminal\(job\.status\)\)\) return;/,
    "expected the hub to keep polling the list while any job on it is non-terminal",
  );

  // NOTHING BELOW 12px (SECOND ELABORATION item 13), still true post-split.
  for (const [label, source] of [
    ["app/genesis/page.tsx", page],
    ["components/genesis/ui.tsx", read("components/genesis/ui.tsx")],
  ] as const) {
    pin.doesNotMatch(
      source,
      /text-\[(?:[0-9]|1[01])(?:\.\d+)?px\]/,
      `expected no type below 12px in ${label} -- the Console pass sets 13px body / 12px secondary as the floor`,
    );
  }
});

contractTest("unit #23: the genesis detail route holds the merged verdict header and the 65/35 split", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");

  // THE MERGED VERDICT HEADER (item 10, unchanged by the move to a route).
  // Two banners said the same thing in two boxes; there is one block, and the
  // counts are the Console stat pattern rather than a run-on sentence.
  pin.match(page, /function Stat\(\{ label, value, tone \}/, "expected the Console stat pattern to exist as one named component");
  for (const [ko, en] of [["통과", "pass"], ["실패", "fail"], ["확인 불가", "not checkable"]]) {
    pin.match(
      page,
      new RegExp(`<Stat[\\s\\S]{0,90}t\\("${ko}", "${en}"`),
      `expected the ${ko} count to be rendered as a stat, not as part of a sentence`,
    );
  }
  const verdictBanners = (page.match(/statusLabel\(selected\.status, t\)/g) ?? []).length;
  pin.match(
    `count=${verdictBanners}`,
    /^count=2$/,
    "expected the selected job's status phrase in exactly two places -- the top bar's status chip and the verdict header's pre-report fallback -- never a second banner repeating the gate verdict",
  );
  pin.match(
    page,
    /report \? verdictLabel\(report\.verdict, t\) : statusLabel\(selected\.status, t\)/,
    "expected ONE headline that is the gate verdict when there is one and the job status otherwise -- not both stacked",
  );

  // The artifact row is compacted into the top bar's right side (still gated
  // on the job actually carrying an artifact), exactly once.
  const artifactRows = (page.match(/<ArtifactRow /g) ?? []).length;
  pin.match(`count=${artifactRows}`, /^count=1$/, "expected exactly one artifact row on the page");
  pin.match(
    page,
    /\{selected\.artifact && \(\s*\n\s*<div className="shrink-0[\s\S]{0,200}<ArtifactRow/,
    "expected the artifact row to be gated on the job actually carrying an artifact",
  );

  // NEW (unit #23): the 65/35 split -- map wide on the left, verdict/fix rail
  // narrow on the right (mockup option D, user decision 2026-08-14) --
  // inverted from the old drawer's map-under-the-grid stack.
  pin.match(
    page,
    /xl:grid-cols-\[65fr_35fr\]/,
    "expected the map/rail split to be 65/35, per the accepted mockup option",
  );
  pin.match(page, /<GenesisTileMap jobId=\{selected\.id\} report=\{report\} hasArtifact=\{Boolean\(selected\.artifact\)\}/, "expected the map to sit in the split's left column");
  pin.match(page, /<GateCheckGrid/, "expected the V1-V9 grid to sit in the split's right column");

  // NEW (unit #23): every fetch keys off the URL's id, not off anything a
  // hub visit might have left in memory -- required for a cold, deep-linked
  // load to work at all.
  pin.match(page, /const params = useParams<\{ id: string \}>\(\);/, "expected the job id to be read from the route's own params");
  pin.match(page, /const record = await fetchGenesisJob\(jobId\);/, "expected the job poll to fetch by the URL's id");
  pin.match(page, /\}, \[jobId\]\);/, "expected the job poll effect to be keyed on the URL's id");

  // NEW (unit #23): the stale-flash guard -- `selected` is read back only
  // when the cached entry's own id still matches the CURRENT url, the same
  // discipline the old drawer used to stop one job's verdict from surviving
  // on screen under a different job's heading.
  pin.match(
    page,
    /const selected = selectedEntry && selectedEntry\.id === jobId \? selectedEntry\.record : null;/,
    "expected the displayed job to be read back only when its cache key still matches the URL's id",
  );

  // NEW (unit #23): an unknown or foreign id renders a named empty state with
  // a way back, never a crash -- and the back link is present in every branch
  // (not-found, loading, and the full render), not only the happy path.
  pin.match(
    page,
    /이 생성을 찾을 수 없습니다 — 삭제되었거나 다른 프로젝트의 생성일 수 있습니다\./,
    "expected an unknown/foreign job id to render a named empty state",
  );
  const backLinks = (page.match(/href="\/genesis"/g) ?? []).length;
  pin.match(`count=${backLinks}`, /^count=1$/, "expected the back-to-history link to be declared once and reused across every branch (not-found, loading, full render)");

  // NEW (unit #23): approving a fix now NAVIGATES to the sibling job's own
  // route instead of re-pointing an in-page selection at it -- the lineage
  // line the sibling's own top bar renders is what makes the jump legible.
  pin.match(
    page,
    /const handleFixApplied = useCallback\(\s*\n\s*\(appliedJob: GenesisJob\) => \{\s*\n\s*router\.push\(`\/genesis\/jobs\/\$\{appliedJob\.id\}`\);/,
    "expected a fix apply to navigate to the sibling job's own detail route",
  );

  // NOTHING BELOW 12px (SECOND ELABORATION item 13) -- extended to the new
  // route and the map/comparison chrome it now mounts.
  for (const [label, source] of [
    ["app/genesis/jobs/[id]/page.tsx", page],
    ["components/genesis/findings.tsx", read("components/genesis/findings.tsx")],
    ["components/genesis/GenesisTileMap.tsx", read("components/genesis/GenesisTileMap.tsx")],
    ["components/genesis/GenesisComparisonPanel.tsx", read("components/genesis/GenesisComparisonPanel.tsx")],
  ] as const) {
    pin.doesNotMatch(
      source,
      /text-\[(?:[0-9]|1[01])(?:\.\d+)?px\]/,
      `expected no type below 12px in ${label} -- the Console pass sets 13px body / 12px secondary as the floor`,
    );
  }
});

contractTest("unit #23: ModalDialog is the shared centered-dialog primitive -- button trigger, Escape, backdrop click", (pin) => {
  const ui = read("components/genesis/ui.tsx");

  pin.match(ui, /export function ModalDialog\(/, "expected one shared centered-dialog primitive");
  const modalBody = sliceBetween(ui, "export function ModalDialog(", "\n/**\n * ko/en/ja for the backend's stable refusal codes");
  pin.match(modalBody, /\S/, "expected to find the ModalDialog body (markers moved?)");
  pin.match(modalBody, /role="dialog"/, "expected the dialog to announce itself as a dialog");
  pin.match(modalBody, /aria-modal="true"/, "expected the dialog to be marked modal");
  pin.match(modalBody, /event\.key === "Escape"/, "expected Escape to close it");
  pin.match(
    modalBody,
    /className="fixed inset-0 z-50 flex items-center justify-center bg-black\/60[^"]*"\s*\n\s*onClick=\{onClose\}/,
    "expected a click on the backdrop to close the dialog, and the backdrop to be LITERAL black (user 2026-08-14: the remapped slate ramp washed the page light instead of dimming it)",
  );
  pin.match(modalBody, /closeRef\.current\?\.focus\(\);/, "expected the close button to take focus on open");
});

contractTest("U-1: the comparison's scope sentence has exactly one source", (pin) => {
  const page = read("app/genesis/page.tsx");
  const panel = read("components/genesis/GenesisComparisonPanel.tsx");

  // The scope was stated TWICE inside one card: the page's Panel subtitle and
  // the panel's own first line, in slightly different words. The panel owns it
  // (the B3 test pins it there); the page must not restate it.
  pin.doesNotMatch(
    page,
    /게이트 판정과 개수만/,
    "expected the genesis page to stop restating the comparison panel's own scope line one line above it",
  );
  pin.match(
    panel,
    /게이트 판정과 개수만 비교합니다/,
    "the scope line must still exist -- the page dropped its duplicate, not the sentence",
  );
});

contractTest("U-1: the four genesis screens share one Panel and one refusalHeadline", (pin) => {
  const ui = read("components/genesis/ui.tsx");
  const pages = [
    "app/genesis/page.tsx",
    "app/genesis/region/page.tsx",
    "app/genesis/demand/page.tsx",
    "app/genesis/phases/page.tsx",
  ];

  pin.match(ui, /export function Panel\(/, "expected the shared Panel to live in components/genesis/ui.tsx");
  pin.match(ui, /export function refusalHeadline\(/, "expected the shared refusalHeadline to live beside it");

  for (const relativePath of pages) {
    const page = read(relativePath);
    pin.doesNotMatch(
      page,
      /\bfunction Panel\(/,
      `${relativePath} must not carry a fifth local Panel -- four copies with two different paddings is what this extraction removed`,
    );
    pin.doesNotMatch(
      page,
      /\bfunction refusalHeadline\(/,
      `${relativePath} must not carry its own refusalHeadline -- the backend's codes are one namespace, so they get one table`,
    );
    pin.match(
      page,
      /from "@\/components\/genesis\/ui"/,
      `expected ${relativePath} to take its Panel/refusalHeadline from the shared module`,
    );
  }

  // Each screen's own refusal codes survived the merge -- one from each of the
  // four call sites, so a half-copied table fails here rather than in front of
  // a user holding a refusal with no headline.
  for (const code of [
    "genesis_upload_not_osm",
    "region_too_large",
    "phase_diagram_attestation_stale",
    "genesis_counts_not_workbook",
  ]) {
    pin.match(ui, new RegExp(`case "${code}":`), `expected ${code} to survive into the shared refusal table`);
  }
});

// ---------------------------------------------------------------------------
// U-1-quick (docs/ux-backlog.md, the user's detailed elaboration of U-1, items
// #2 #3 #5) -- the artifact download bug, the residual prose, and the history
// row. Item #4 (the V1-V9 grid) is the rewritten test above.
// ---------------------------------------------------------------------------

contractTest("U-1-quick: artifact downloads go through apiFetch, never a bare href", (pin) => {
  const api = read("lib/api.ts");
  // unit #23: ArtifactRow moved to the detail route (compacted into its own
  // top bar) with the rest of the drawer's content.
  const page = read("app/genesis/jobs/[id]/page.tsx");

  // THE BUG, reported from live use: all five artifact buttons did nothing.
  // They were plain anchors built from `genesisArtifactUrl()` = `apiUrl(...)`,
  // an ABSOLUTE cross-origin URL, and that broke three ways at once --
  //   (1) the session cookie is host-only for the page's own origin, so the
  //       authenticated route (`download_artifact` takes `current_user`) 401s;
  //   (2) an anchor cannot send `X-Workspace-Id`, so the request resolves in
  //       the DEFAULT workspace and any other project's job is 404 -- the named
  //       1.4 hole ("the health-page <a href> can't carry a header");
  //   (3) a bare href has no failure branch, so the page said nothing either way.
  // The URL builder is DELETED rather than left beside the fix: leaving it is
  // leaving the trap.
  pin.doesNotMatch(
    page,
    /href=\{genesisArtifactUrl\(/,
    "expected no artifact anchor -- an <a href> cannot carry the session cookie or the workspace header",
  );
  pin.doesNotMatch(
    api,
    /export function genesisArtifactUrl/,
    "expected the bare artifact-URL builder to be deleted, not left next to the fix",
  );

  // The replacement goes through apiFetch -- the one path that takes the
  // same-origin proxy first and attaches X-Workspace-Id.
  pin.match(api, /export async function fetchGenesisArtifact\(/, "expected one binding to own the artifact route");
  pin.match(
    api,
    /const r = await apiFetch\(`\/api\/genesis\/jobs\/\$\{jobId\}\/artifact\/\$\{kind\}`/,
    "expected the artifact bytes to be fetched through apiFetch (cookie + workspace header)",
  );
  pin.match(
    api,
    /filenameFromContentDisposition\(r\.headers\.get\("content-disposition"\)\) \?\? `\$\{jobId\}\.\$\{kind\}`/,
    "expected the saved filename to come from the response's own header, with a fallback that invents nothing",
  );
  pin.match(page, /await fetchGenesisArtifact\(jobId, kind\)/, "expected the page to download through the binding");
  pin.match(page, /saveBlob\(artifact\.blob, artifact\.filename\)/, "expected the fetched bytes to be handed to a real download");

  // And the failure is NAMED, per status, instead of being silent -- which is
  // the half of this bug a user actually experienced.
  pin.match(page, /function downloadFailureText\(caught: unknown, t: Translate\): string \{/, "expected a named reason for every download failure");
  pin.match(page, /caught\.status === 401/, "expected a 401 to be told apart -- it means sign in");
  pin.match(page, /caught\.status === 403/, "expected a 403 to be told apart -- it means the wrong project");
  pin.match(page, /caught\.status === 404/, "expected a 404 to be told apart -- no such file, or another project's generation");
  pin.match(page, /내려받지 못했습니다/, "expected a failed download to say so on screen rather than do nothing");
});

contractTest("U-1-quick: doctrine sentences move into a reachable ⓘ, operation hints are deleted", (pin) => {
  const ui = read("components/genesis/ui.tsx");
  const page = read("app/genesis/page.tsx");
  // unit #23: 판정/잠정 등록's own doctrine moved a second time -- off the hub's
  // PageHelp (it described the DRAWER) and onto the drawer's own route.
  const detail = read("app/genesis/jobs/[id]/page.tsx");
  // CO-EDITED by U-1-map: `GateFindingsMap.tsx` is gone. Its fetch/states/
  // detail composition became `GenesisTileMap.tsx` (leaflet + CARTO over the
  // server-computed WGS84) and its pan/zoom SVG canvas became
  // `DraftLocalMap.tsx`, kept as the honest fallback for a draft the server
  // will not place. Both halves are read here: the disclaimer moved with the
  // component that owns the map's chrome, and the deleted operation hint must
  // stay deleted in BOTH.
  const tileMap = read("components/genesis/GenesisTileMap.tsx");
  const localMap = read("components/genesis/DraftLocalMap.tsx");
  const panel = read("components/genesis/GenesisComparisonPanel.tsx");
  const help = read("lib/help-content.tsx");

  // ONE tooltip primitive, and it must be reachable by every input a user has.
  // A `title` attribute (or a hover-only div) would DELETE these sentences for
  // keyboard and touch users, which is the same as dropping them.
  pin.match(ui, /export function InfoTip\(/, "expected one shared ⓘ rather than five per-screen tooltips");
  pin.match(ui, /role="tooltip"/, "expected the tooltip content to be announced as a tooltip");
  pin.match(ui, /aria-describedby=\{open \? tooltipId : undefined\}/, "expected the trigger to point at its content while open");
  pin.match(ui, /onFocus=\{\(\) => setHovering\(true\)\}/, "expected TAB to reach the sentence, not only the mouse");
  pin.match(ui, /onClick=\{\(\) => setPinned\(\(current\) => !current\)\}/, "expected a click/tap to latch it open -- the only thing that works on touch");
  pin.match(ui, /event\.key === "Escape"/, "expected Escape to close it");

  // The four DOCTRINE sentences: each still lives verbatim in the file that
  // owns it, now inside the ⓘ instead of permanently on screen.
  // The local-coordinates disclaimer is now the FALLBACK arm of the map's ⓘ —
  // it is the honest sentence for a draft with no recorded projection origin,
  // and the georeferenced arm beside it is the sentence for one that has it.
  // Still verbatim, still in an InfoTip, still in the file that owns the map.
  pin.match(
    tileMap,
    /<InfoTip[\s\S]{0,900}"링크는 초안 네트워크/,
    "expected the map's local-coordinates disclaimer to stay in the ⓘ, as the no-basemap arm",
  );
  pin.match(
    panel,
    /<InfoTip[\s\S]{0,200}게이트 판정과 개수만 비교합니다/,
    "expected the comparison's scope line to stay in this panel, inside the ⓘ",
  );
  // UNIT #20: these two doctrine sentences moved AGAIN -- out of the inline
  // ⓘ they sat in here (U-1-quick's own migration) and into the page's own
  // `PageHelp` drawer, alongside a third ⓘ that made the same move (`판정`'s
  // R1.5 doctrine). All three still live VERBATIM in this file, just under a
  // drawer entry's `title`/`body` instead of an `<InfoTip>` -- checked below,
  // together with proof that the OLD inline `<InfoTip>` is actually gone from
  // each of the three positions, not merely duplicated.
  //
  // UNIT #23: 판정 게이트 and 잠정 등록 moved a THIRD time -- off the hub's own
  // PageHelp (genesisHelpEntries, which described the drawer) and onto the
  // drawer's own route, alongside the R1.5 grid they were always about. 제외
  // 도로 stayed on the hub (it is the exclude-service checkbox's own
  // doctrine, and that checkbox is still part of the hub's generation form).
  //
  // 2026-08-14: a FOURTH move, this time every drawer entry at once. The
  // per-page ? drawer retired (user: the topbar book icon is the one door to
  // the documentation), so the sentences live in lib/help-content.tsx and
  // render at /docs. The doesNotMatch pins against the PAGE files below keep
  // their old job regardless: the inline ⓘ positions must stay empty no
  // matter which surface the prose reads from.
  pin.doesNotMatch(
    page,
    /checked=\{excludeService\}[\s\S]{0,500}<InfoTip/,
    "expected the excluded-roads ⓘ to be gone from beside the checkbox -- it now lives in the page's help drawer",
  );
  pin.match(
    help,
    /t\("제외 도로"[\s\S]{0,300}제외된 도로는 버려지지 않고/,
    "expected the excluded-roads disclosure to survive in the doctrine module rendered at /docs",
  );

  pin.doesNotMatch(
    detail,
    /\{t\("판정", "Verdict", "判定"\)\}[\s\S]{0,120}<InfoTip/,
    "expected the verdict-grid doctrine ⓘ to be gone from inline -- it now lives in the detail page's own help drawer",
  );
  pin.doesNotMatch(
    detail,
    /등록되었지만 잠정입니다[\s\S]{0,250}<InfoTip/,
    "expected the provisional-status doctrine ⓘ to be gone from inline -- it now lives in the detail page's own help drawer",
  );
  pin.match(
    help,
    /t\("판정 게이트"[\s\S]{0,400}R1\.5 검증 게이트 V1–V9/,
    "expected the R1.5 doctrine sentence to survive in the doctrine module rendered at /docs",
  );
  pin.match(
    help,
    /t\("잠정 등록"[\s\S]{0,600}프로젝트에 등록되었습니다\. 다만 실행되지 못한/,
    "expected the provisional doctrine paragraph to survive in the doctrine module rendered at /docs",
  );

  // The one line that was DELETED rather than moved: how to use a mouse is not
  // a disclosure and has no claim in it to lose. The pattern requires the `t(`
  // call form on purpose -- the deletion note left in DraftLocalMap.tsx names
  // the removed string, and a bare-string pin would match that comment.
  for (const [label, source] of [["GenesisTileMap", tileMap], ["DraftLocalMap", localMap]] as const) {
    pin.doesNotMatch(
      source,
      /t\("드래그로 이동/,
      `expected the drag/zoom operation hint to stay deleted in ${label}, not to reappear when the renderer split in two`,
    );
  }
});

// ---------------------------------------------------------------------------
// Unit #20 (superseded 2026-08-14) -- the per-page ? drawer is RETIRED. The
// doctrine it carried was not deleted: it moved verbatim into
// lib/help-content.tsx and renders as one reading surface at /docs, reachable
// from the book icon the topbar puts on every route. This block pins the three
// legs of that arrangement -- the module, the page, and the door -- and pins
// the old arrangement absent, so a private per-page drawer cannot quietly come
// back double-entried.
// ---------------------------------------------------------------------------

contractTest("unit #20: the ? drawer is retired -- doctrine lives in lib/help-content and renders at /docs behind the topbar book icon", (pin) => {
  const ui = read("components/genesis/ui.tsx");
  const help = read("lib/help-content.tsx");
  const docs = read("app/docs/page.tsx");
  const shell = read("components/AppShell.tsx");

  // The primitive is gone as CODE. Its retirement note may (and does) name it;
  // `withoutComments` keeps that note from tripping a code pin.
  pin.doesNotMatch(
    withoutComments(ui),
    /PageHelp/,
    "the ? drawer primitive must stay deleted, not linger as an unused export",
  );

  // The doctrine module: all four areas' entries, t()-built, static forever.
  pin.match(help, /export function genesisHelpEntries\(/, "expected the hub's doctrine to live in the shared module");
  pin.match(help, /export function regionHelpEntries\(/, "expected the region doctrine to live in the shared module");
  pin.match(help, /export function phasesHelpEntries\(/, "expected the phases doctrine to live in the shared module");
  pin.match(help, /export function jobHelpEntries\(/, "expected the job-detail doctrine to live in the shared module");
  pin.doesNotMatch(help, /await fetch|apiFetch|apiMutationFetch/, "doctrine is static -- the module must never fetch");
  pin.doesNotMatch(docs, /await fetch|apiFetch|apiMutationFetch/, "the documentation page renders static doctrine only -- it must never fetch");

  // The page renders ALL FOUR areas, not whichever subset survived a refactor.
  pin.match(docs, /genesisHelpEntries\(t\)/, "expected /docs to render the hub's entries");
  pin.match(docs, /regionHelpEntries\(t\)/, "expected /docs to render the region entries (no cap passed -- the ?? 64 fallback is the honest browser-side number)");
  pin.match(docs, /phasesHelpEntries\(t\)/, "expected /docs to render the phases entries");
  pin.match(docs, /jobHelpEntries\(t\)/, "expected /docs to render the job-detail entries");

  // The door: the topbar's book icon, on every route.
  pin.match(shell, /href="\/docs"/, "expected the topbar book icon to be the standing door to the documentation");

  // No page keeps a private trigger.
  for (const [label, source] of [
    ["hub", read("app/genesis/page.tsx")],
    ["region", read("app/genesis/region/page.tsx")],
    ["phases", read("app/genesis/phases/page.tsx")],
    ["job detail", read("app/genesis/jobs/[id]/page.tsx")],
  ] as const) {
    pin.doesNotMatch(
      withoutComments(source),
      /PageHelp/,
      `expected the ${label} page to carry no ? drawer -- doctrine is read at /docs now`,
    );
  }
});

contractTest("U-1-quick: a 생성 이력 row states its region and its scale, or says '—'", (pin) => {
  const page = read("app/genesis/page.tsx");
  const lib = read("lib/genesisArtifacts.ts");
  const api = read("lib/api.ts");
  // The backend half, read across the repo root the way the 2.4 diff test does:
  // the field this row renders does not exist unless the runner writes it.
  const jobsService = readFileSync(
    path.join(repoRoot, "backend", "services", "genesis_jobs.py"),
    "utf-8",
  );

  // THE THREE-WAY DISTINCTION is the whole point, and it is why the field is
  // optional-AND-nullable rather than merely nullable: a record written before
  // this field existed knows nothing (→ "—"), while `null` is a MEASUREMENT
  // (the frozen .osm was read and carries no fetch provenance → an upload).
  // Collapsing them would let a legacy generation claim it was uploaded.
  pin.match(api, /requested_region\?: GenesisRequestedRegion \| null;/, "expected the field to be optional AND nullable -- absent and null are different answers");
  pin.match(lib, /export function requestedRegionLabel\(/, "expected one place to word the region, shared like statusLabel");
  pin.match(lib, /if \(region === undefined\) return "—";/, "expected a record with no such field to render an em dash, never an invented region");
  pin.match(lib, /if \(region === null\) return t\("업로드"/, "expected a measured absence of fetch provenance to read as an upload");
  pin.match(lib, /폴리곤 \$\{region\.polygon_vertices\}점/, "expected a polygon fetch to state its vertex count");
  pin.match(lib, /export function networkScaleLabel\(/, "expected the link/connector/node scale to be worded in the same shared place");
  pin.match(lib, /if \(!network\) return "—";/, "expected a generation with no network to say so rather than render zeroes");

  // The row reads both off the job record and derives neither.
  pin.match(page, /requestedRegionLabel\(job\.requested_region, t\)/, "expected the history row to render the recorded region");
  pin.match(page, /networkScaleLabel\(job\.network, t\)/, "expected the history row to render the recorded network scale");
  pin.match(page, /statusLabel\(job\.status, t\)/, "expected the row to keep its verdict chip beside the two new facts");

  // The backend writes it, once, from the region the runner already read.
  pin.match(jobsService, /def _requested_region_summary\(/, "expected the job record's region summary to be built in one named place");
  pin.match(
    jobsService,
    /requested_region=_requested_region_summary\(requested_bbox, requested_polygon\)/,
    "expected the runner to persist the region it already reads for the parser",
  );
  pin.match(
    jobsService,
    /summary\["polygon_vertices"\] = len\(requested_polygon\)/,
    "expected a lasso to be recorded as a COUNT -- the ring stays in the artifact, not in every listing row",
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
  // UNIT #20 -- "the limit becomes the button's job". The stat grid, the
  // 한계 안/한계 초과 chip and the "자세한 근거" byte-estimate detail this
  // block used to pin came OFF the screen entirely (docs the removal above
  // `overLimitReason`'s own declaration in region/page.tsx); the fetch behind
  // them is untouched (`estimated_bytes` still exists on the wire, in
  // `lib/api.ts`'s `RegionMeasurement`, just never rendered here). What
  // replaced the permanent display is the extract button's own gating: over
  // the cap, it is disabled, and the reason -- read from this SAME
  // measurement, never invented -- sits in a `title` on the button's wrapping
  // span, the pattern this app already uses for a disabled cross-navigation
  // chip. MEASURED against the running app 2026-08-11: a 1.4 x 8.4 km region
  // is 12 km² against a 17 km² square limit -- comfortably "inside" by area --
  // and is refused, because its half-extent is 4 195 m against a 2 079 m
  // radius, which is why the reason leads with the half-extent, not the area.
  pin.match(
    page,
    /const overLimitReason = useMemo\(\(\) => \{\s*\n\s*if \(!measurement \|\| measurement\.within_region_cap\) return null;/,
    "expected the over-limit hover reason to fire ONLY when a real measurement says over-cap -- never for 'not enough corners yet'",
  );
  pin.match(
    page,
    /half_extent_m\.toFixed\(0\)[\s\S]{0,40}validity_radius_m\.toFixed\(0\)/,
    "expected the hover reason to lead with the half-extent-vs-limit comparison, the one that actually decides",
  );
  pin.match(
    page,
    /area_km2\.toFixed\(2\)[\s\S]{0,40}max_area_km2\.toFixed\(1\)/,
    "expected the hover reason to also state the area against its own limit, from the same measurement",
  );
  pin.match(
    page,
    /<span title=\{overLimitReason \?\? undefined\}>/,
    "expected the reason to sit on the button's WRAPPING span -- a title on a disabled button does not fire in some browsers",
  );
  pin.doesNotMatch(
    page,
    /within_region_cap\s*\n?\s*\?\s*t\("한계 안"/,
    "the always-visible 한계 안/한계 초과 verdict chip was removed -- the button's own disabled state and hover reason carry the limit now",
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

  // REWRITTEN 2026-08-13 (U-1 polish, the user's decision after live testing --
  // docs/ux-backlog.md, U-1 "REGION-PAGE ADDENDA"). These three pins used to
  // REQUIRE the "이 화면에 일부러 넣지 않은 것" disclosure and its two deferred
  // features. The section is now removed, so they are inverted -- the same
  // shape this file already uses for a superseded sentence (see the
  // bounding-box disclosure pinned absent in the lasso test below).
  //
  // THE DOCTRINE NOTE, because on this codebase deleting a disclosure is
  // normally the offence: the named-absence rule governs DATA silently dropped
  // during processing -- a road the trim cut, a count the workbook did not
  // carry, a zone the gate could not reach. It does NOT govern unbuilt roadmap
  // features. A reader cannot act on "a button that does not exist yet",
  // nothing on screen is wrong without the paragraph, and the deferral record
  // already has a durable home in docs/ux-backlog.md and
  // docs/product/master-roadmap-v2-2026-08-11.md, which carry it with its
  // reason and its sequencing. So the SECTION is pinned absent while the
  // dropped-data disclosures on this page (the polygon-is-the-boundary card,
  // the estimate caveat, the missing-ODbL note) stay pinned present above and
  // in the lasso test.
  //
  // `withoutComments` on the two section-name pins, for the reason this file
  // already documents for the deleted drag/zoom hint: the removal note left in
  // the source NAMES both removed sections so the next reader learns why they
  // went, and a raw doesNotMatch would fire on the explanation forever.
  const pageCode = withoutComments(page);
  pin.doesNotMatch(
    pageCode,
    /이 화면에 일부러 넣지 않은 것/,
    "the deferred-features disclosure was removed by the user on 2026-08-13 -- it must not come back",
  );
  pin.doesNotMatch(
    pageCode,
    /도로 개별 선택·블록 제외\(공유 벽 규칙\)는 의도적으로 다음 단계로 미뤘습니다/,
    "the deferred per-road / block-exclusion paragraph belongs to the backlog and the roadmap now, not to this screen",
  );
  pin.doesNotMatch(
    page,
    /사각형 영역만 지원합니다/,
    "the page must not still claim rectangles are the only supported shape",
  );

  // 추출 이력 -- REMOVED 2026-08-13 by the same user decision. Every generation
  // consumes a FRESH extract (the B4 runbook's own rule), so a list of past
  // staged extracts carried no action; generation history lives on the hub.
  // UI ONLY: the listing binding and the state it fills are deliberately still
  // here, so this pin asserts the LIST is gone and the plumbing is not.
  pin.doesNotMatch(
    pageCode,
    /추출 이력/,
    "the staged-extract history list was removed -- generation history lives on /genesis",
  );
  pin.doesNotMatch(
    pageCode,
    /아직 추출한 영역이 없습니다/,
    "the removed list must not leave its empty state behind",
  );
  pin.match(
    page,
    /const entries = await fetchRegionFetches\(\);/,
    "the listing plumbing stays -- the LIST was removed, not the endpoint or the state it fills",
  );
  // The failure that list used to report has to keep a home, or removing the
  // section would have silently deleted an error path.
  pin.match(
    page,
    /추출 목록을 불러오지 못했습니다/,
    "expected the listing/polling failure to be named at page level now that its panel is gone",
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

// ---------------------------------------------------------------------------
// The lasso (stage (a)) -- one shape at a time, and what it is judged as
// ---------------------------------------------------------------------------
//
// Its own test, single-concern: the value of the lasso is NOT that a polygon can
// be drawn, it is that the page tells the truth about which shape gets judged.
// That truth CHANGED when the trim became polygon-native: the query, the tail
// trim and the model-boundary decision are all the drawn ring now, and only the
// residual metre band and the size estimate are still its bounding box. So the
// pins below moved with it, and the old sentence is pinned ABSENT -- a
// disclosure kept past its own fix misleads exactly as much as a missing one.

contractTest("B2 lasso: the page is lasso-only and says the polygon is what gets cut", (pin) => {
  const page = read("app/genesis/region/page.tsx");
  const map = read("components/genesis/RegionLassoMap.tsx");
  const help = read("lib/help-content.tsx");

  // UNIT #20: THE MODE TOGGLE DIED. This page used to be rectangle-or-lasso;
  // it is now lasso-only, so there is no `RegionMode` type, no mode state and
  // no pill toggle left to pin. `parsed` is UNCONDITIONALLY the drawn ring --
  // the same object the preview measures and the fetch is started with, so the
  // two can never describe different regions.
  // `withoutComments`: the header comment above legitimately NAMES the removed
  // `RegionMode` type as part of documenting the removal (this file's own
  // convention -- see the doc comment on `withoutComments` itself). A raw
  // doesNotMatch would fire on that explanation forever.
  pin.doesNotMatch(withoutComments(page), /RegionMode/, "the rectangle/lasso mode type must be gone -- this page sends one shape now");
  pin.doesNotMatch(page, /aria-pressed=\{mode === value\}/, "the mode toggle pills must be gone with the mode they switched");
  pin.doesNotMatch(page, /function RegionGauge\(/, "RegionGauge existed ONLY for rectangle mode -- it must be gone with it, not left as dead code");
  pin.doesNotMatch(page, /BoxFields|BoxKey|parseBox\(/, "the rectangle coordinate fields' types and parser must be gone -- this screen no longer sends a bbox");
  pin.doesNotMatch(page, /북위 \(north\)/, "the rectangle north/south/east/west input fields must be gone -- lasso-only now");
  pin.match(
    page,
    /return vertices\.length >= 3 \? \{ polygon: vertices \} : null;/,
    "expected the polygon payload to be the drawn ring, and nothing before three corners",
  );

  // An empty ring must be genuinely empty: `parsed` is null before three
  // corners, and the measure effect returns on null `parsed` BEFORE it arms
  // the debounce timer -- so opening this page fires no Overpass-limits
  // preview for a shape nobody drew yet.
  pin.match(
    page,
    /if \(!parsed\) \{\s*\n\s*setMeasurement\(null\);/,
    "expected an unfinished shape to clear the measurement and return BEFORE the preview is scheduled -- zero corners must fire no request",
  );
  pin.match(page, /previewRegion\(parsed\)/, "the polygon must be measured by the same server preview");
  pin.match(
    page,
    /startRegionFetch\(\{ \.\.\.parsed, name:/,
    "expected the fetch to send exactly the shape the preview measured",
  );

  // THE disclosure. In Korean, naming both halves: the drawn polygon is what
  // is queried AND what is cut, and the box is what is left over. 2026-08-14:
  // the sentences left this page with the ? drawer -- they live in the shared
  // doctrine module now and render at /docs behind the topbar book icon.
  pin.match(
    help,
    /그린 폴리곤이 그대로 기준입니다/,
    "expected the doctrine to say that the drawn polygon is the boundary",
  );
  pin.match(
    help,
    /폴리곤 변에서 자르고 그 자리에 노드를 만듭니다/,
    "expected the doctrine to name the mechanism: a road past the ring is cut AT the ring",
  );
  pin.match(
    help,
    /거리 기반 경계 띠와 용량 추정에만 쓰입니다/,
    "expected the doctrine to name what the bounding box is still used for, rather than dropping it",
  );
  // The SUPERSEDED sentence, pinned absent. It was true until the trim became
  // polygon-native (backend: osm.trim_ways_to_requested_bbox chooses its kernels
  // by shape); leaving it on screen would tell a user their notch is judged as
  // interior when it is now cut at the ring.
  pin.doesNotMatch(
    page,
    /점선 사각형이 경계 판정 기준입니다|내부 막다른 길로 분류됩니다/,
    "the superseded bounding-box disclosure must not survive its own fix",
  );
  pin.doesNotMatch(
    help,
    /점선 사각형이 경계 판정 기준입니다|내부 막다른 길로 분류됩니다/,
    "the superseded bounding-box disclosure must not resurface in the doctrine module either",
  );
  // And the rectangle drawn is the SERVER's derived box, not a second
  // computation in the browser -- the same rule the cap numbers already follow.
  pin.match(
    page,
    /bbox=\{measurement\?\.region \?\? null\}/,
    "expected the drawn rectangle to be the server's own derived bounding box",
  );
  // Demoted, not deleted: a hairline keeps the residual visible without competing
  // with the shape the user drew.
  pin.match(
    map,
    /weight: 1,\s*\n\s*opacity: 0\.45,\s*\n\s*dashArray: "3 6",/,
    "expected the bounding box to be drawn as a demoted hairline, still present",
  );
  // UNIT #20 -- THE EDITABLE VERTEX TABLE, replacing the old read-only dotted
  // coordinate enumeration. `vertices` (this page's own state) is the single
  // source of truth: `VertexRow` receives it as `lat`/`lon` props and reports
  // an edit back through `onCommit`, which is wired to `updateVertex` -- the
  // ONE place `setVertices` mutates a coordinate. The map, in turn, is handed
  // the SAME `vertices` array as a prop, so an edit that reaches `updateVertex`
  // is visible on the map on the very next render, not through a second copy.
  pin.match(
    page,
    /onCommit=\{updateVertex\}/,
    "expected each vertex row to be wired to the single vertices-mutating callback",
  );
  pin.match(
    page,
    /const updateVertex = useCallback\(\(index: number, lat: number, lon: number\) => \{\s*\n\s*setVertices\(\(current\) => current\.map\(\(vertex, i\) => \(i === index \? \[lat, lon\] : vertex\)\)\);/,
    "expected an edited vertex to update the SAME vertices array the map reads, not a parallel copy",
  );
  pin.match(
    page,
    /<RegionLassoMap\s*\n\s*vertices=\{vertices\}/,
    "expected the map to be driven by the exact same vertices state the table edits",
  );
  // THE GUARD: an invalid draft must never reach `onCommit`, so it can never
  // reach `setVertices`, the map, or a submitted payload. Checked at the
  // SOURCE of the commit call in `VertexRow`, not merely asserted in prose.
  pin.match(
    page,
    /const nextLatOk = nextLatText\.trim\(\) !== "" && isValidLatitude\(nextLat\);/,
    "expected a committed latitude to be range-checked before it can reach onCommit",
  );
  pin.match(
    page,
    /if \(nextLatOk && nextLonOk\) onCommit\(index, nextLat, nextLon\);/,
    "expected the row to call onCommit ONLY when both fields are valid -- never feed NaN or an out-of-range degree to the map",
  );
  // ADVERSARIAL FIX (found in this unit's own self-review): a row is keyed by
  // INDEX, so deleting a row out of the MIDDLE of the table does not remount
  // every row after it -- without a resync, the row now sitting at a shifted
  // index would go on showing the deleted-away neighbour's stale draft text
  // while `vertices` (and the map) already hold the correct value there.
  pin.match(
    page,
    /useEffect\(\(\) => \{\s*\n\s*setLatText\(String\(lat\)\);\s*\n\s*setLonText\(String\(lon\)\);\s*\n\s*\}, \[lat, lon\]\);/,
    "expected a row's draft text to resync when the vertex it is bound to changes underneath it (e.g. a delete shifting every later index)",
  );
  pin.match(
    page,
    /function isValidLatitude\(value: number\): boolean \{\s*\n\s*return Number\.isFinite\(value\) && value >= -90 && value <= 90;/,
    "expected latitude to be range-checked against -90..90, not merely parsed",
  );
  pin.match(
    page,
    /function isValidLongitude\(value: number\): boolean \{\s*\n\s*return Number\.isFinite\(value\) && value >= -180 && value <= 180;/,
    "expected longitude to be range-checked against -180..180, not merely parsed",
  );
  // ADVERSARIAL GUARD: deleting a vertex down below three corners while the
  // ring is still "closed" must not leave the map drawing a degenerate filled
  // polygon, nor leave its own click handler refusing to accept new corners
  // (`RegionLassoMap`'s `if (current.closed) return;`). This effect is what
  // keeps "closed" meaning "a real ring" no matter which path shrank the array.
  pin.match(
    page,
    /if \(vertices\.length < 3 && ringClosed\) setRingClosed\(false\);/,
    "expected the ring-closed flag to reset itself if an edit or a delete drops the vertex count below three",
  );
  pin.match(
    page,
    /limits\.max_polygon_vertices/,
    "expected the corner cap to be read from the server's limits, not written in the browser",
  );
  pin.doesNotMatch(
    page,
    /(?:maxVertices|MAX_[A-Z_]*VERTICES)\s*=\s*\d/,
    "the browser must not carry its own copy of the corner cap",
  );
  // MEASURED against the running app: a backend older than that field put "점 0 /
  // undefined" on screen. A limit the UI cannot state is a limit it must not
  // mention -- the counter falls back to null and the server's refusal does the
  // talking.
  pin.match(
    page,
    /Number\.isFinite\(cap\) && cap > 0 \? cap : null/,
    "expected a served corner cap to be checked before it is shown, never rendered raw",
  );

  // The drawing surface: leaflet client-only, the mockup's own semantics, and no
  // transform between a click and the query.
  pin.match(page, /dynamic\(\(\) => import\("@\/components\/genesis\/RegionLassoMap"\), \{\s*\n?\s*ssr: false,?\s*\n?\}\)/, "expected the map to be mounted client-only");
  pin.match(map, /await import\("leaflet"\)/, "expected leaflet to load inside the effect, as the other maps do");
  pin.match(map, /doubleClickZoom: false/, "a double click closes the ring, so the map must not also zoom on it");
  pin.match(map, /map\.on\("dblclick"/, "expected double-click to close the ring, the mockup's decided semantics");
  pin.match(
    map,
    /DOUBLE_CLICK_GRACE_MS/,
    "expected a single click to be held so closing the ring cannot also drop a duplicate corner",
  );
  // Raw WGS84: the click's latlng is what is sent. The pattern is the SHAPE a
  // georeference takes in this repo (`DashboardMap`'s GEO_PARAMS query string),
  // not the word, so the component's own "no Helmert here" comment does not
  // satisfy a pin about its code.
  pin.doesNotMatch(
    map,
    /scale_x=|offset_x=|rotation_deg=/,
    "the lasso is raw WGS84 -- a fit or offset here would invent a georeference the product refuses",
  );
  pin.match(
    map,
    /current\.onAddVertex\(event\.latlng\.lat, event\.latlng\.lng\)/,
    "expected the clicked latitude/longitude to be passed through untransformed",
  );
  // ko primary on the map's two strings too: they are passed IN from the page's
  // t(), so this component must own no user-facing text of its own.
  pin.doesNotMatch(
    map,
    /"[^"]*[가-힣][^"]*"/,
    "the map component must take its labels from the page's t(), not hard-code Korean",
  );

  // UNIT #20 RETIRED (2026-08-14) -- the page's ? drawer went with the rest of
  // them; its five sections moved verbatim into lib/help-content.tsx's
  // `regionHelpEntries` and render at /docs. The corner-cap sentence keeps its
  // `${maxVertices ?? 64}` template -- the cap travels as the builder's one
  // parameter, so the sentence still cannot state a number the server has not
  // said. One distinctive sentence per moved section stays pinned there.
  pin.match(
    help,
    /점은 최대 \$\{maxVertices \?\? 64\}개까지 찍을 수 있습니다/,
    "expected the 라쏘 조작 doctrine to state the corner cap as a template, never a hard number",
  );
  pin.match(
    help,
    /영역을 추출하려면 로그인이 필요합니다/,
    "expected the 데이터 출처 doctrine to state the OSM licence and the login requirement",
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

  // A polygon takes the SAME road as a box: one binding, one mutation helper, one
  // set of workspace headers. A second "fetch a polygon" call would be a second
  // place the workspace could be forgotten.
  pin.match(
    regionSection,
    /export type RegionShape =/,
    "expected one shape union, so a caller cannot send a box and a polygon at once",
  );
  pin.match(
    regionSection,
    /startRegionFetch\(payload: RegionShape & \{ name\?: string \}\)/,
    "expected the polygon to go through the SAME startRegionFetch binding as the box",
  );
  pin.match(
    startBinding,
    /apiMutationFetch\("\/api\/genesis\/region\/fetches"/,
    "expected the polygon fetch to keep the workspace-header mutation helper",
  );
  pin.match(
    regionSection,
    /polygon: shape\.polygon\.map\(\(\[lat, lon\]\) => `\$\{lat\},\$\{lon\}`\)\.join\(";"\)/,
    "expected the preview to carry the ring on the query string, keeping it an open GET",
  );
  pin.match(
    regionSection,
    /max_polygon_vertices: number/,
    "expected the corner cap to be served, so the browser never carries its own copy",
  );

  // Discoverable: the upload flow points at the region flow.
  pin.match(genesisPage, /href="\/genesis\/region"/, "expected the genesis page to link to the region extractor");
});

// ---------------------------------------------------------------------------
// Roadmap v2 §5.B B3 (완결) -- the gate's verdict gets a map, and a comparison
// ---------------------------------------------------------------------------

contractTest("B3: the gate findings map never draws a silent blank and never invents excluded-zone geometry", (pin) => {
  // CO-EDITED BY U-1-map (docs/ux-backlog.md item #1). `GateFindingsMap.tsx`
  // was split, not restyled, and every pin below moved with the half that now
  // owns the code it describes:
  //   * `GenesisTileMap.tsx`  -- the fetches, the four named states, the
  //     leaflet/CARTO basemap, and the choice between it and the fallback;
  //   * `DraftLocalMap.tsx`   -- the local-coordinates SVG canvas, unchanged:
  //     the memoized polyline layer, `vector-effect` stroke widths and the
  //     non-passive wheel listener are all still here and still pinned, because
  //     that canvas is still what a draft with no recorded origin is drawn on;
  //   * `findings.tsx`        -- the detail panel and the highlight grouping
  //     both renderers share, which is what stops the two of them from ever
  //     disagreeing about which finding touches which link.
  const tileMap = read("components/genesis/GenesisTileMap.tsx");
  const localMap = read("components/genesis/DraftLocalMap.tsx");
  const detail = read("components/genesis/findings.tsx");
  const lib = read("lib/genesisArtifacts.ts");

  // No artifact, a failed network fetch and an empty network are three
  // DIFFERENT named states -- never a blank canvas.
  pin.match(tileMap, /!hasArtifact\s*\)\s*\{[\s\S]*?<EmptyState/, "expected a job with no artifact to render a named empty state");
  pin.match(tileMap, /loadError &&[\s\S]*?<EmptyState/, "expected a failed network fetch to render a named empty state");
  pin.match(tileMap, /network\.links\.length === 0[\s\S]*?<EmptyState/, "expected an empty network to render a named empty state");

  // A FOURTH named state, added by U-1-map: the census loaded but the SERVER
  // refused to place this draft on the earth (no recorded projection origin).
  // That is a degradation, not a failure -- the shape and its findings are
  // still drawn, in local coordinates, above a sentence that says the basemap
  // is missing and quotes the server's own reason. What it must never do is
  // substitute an origin: a network drawn about a guessed origin is the right
  // shape in the wrong place, with nothing on screen to say so.
  pin.match(tileMap, /geo \? \(/, "expected the basemap to be used only when the server actually returned degrees");
  pin.match(
    tileMap,
    /배경지도 없이 이 생성의 로컬 좌표로만 그립니다/,
    "expected the no-basemap fallback to name itself on screen rather than silently degrade",
  );
  pin.match(tileMap, /\{geoRefusal && \(/, "expected the server's own refusal reason to be quoted, not swallowed");
  pin.match(tileMap, /<DraftLocalMap/, "expected the local-coordinates canvas to remain the fallback renderer");

  // Every finding this page cannot place on the map is LISTED, not dropped.
  pin.match(detail, /unresolvedCount > 0/, "expected unresolved findings to get their own disclosed section");
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
    detail,
    /이 구역은 최종 좌표가 확정되기 전에 제외되어, 지도에 그릴 좌표가 없습니다/,
    "expected an excluded zone's detail panel to say plainly that it has no coordinates left to draw",
  );
  // The detail panel is SHARED by both renderers rather than copied into each:
  // two copies of "why is this link red" is two answers, and a basemap does not
  // change what a finding says.
  pin.match(detail, /export function FindingsDetailPanel\(/, "expected ONE detail panel behind both renderers");
  for (const [label, source] of [["GenesisTileMap", tileMap], ["DraftLocalMap", localMap]] as const) {
    pin.doesNotMatch(
      source,
      /function FindingsDetailPanel\(|function DetailPanel\(/,
      `expected ${label} to import the shared detail panel, not grow its own copy`,
    );
  }

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
  // RE-AIMED, unit #41: the union member broke across lines when it grew an
  // optional measured `point` (a V3 testimony's own coordinate). `refs` is
  // still a LIST and that is still the claim being held here.
  pin.match(
    detail,
    /kind: "finding";\s*\n\s*refs: FindingRef\[\];/,
    "expected a selection to carry EVERY finding on the clicked element, not just one",
  );
  pin.match(detail, /selected\.refs\.map\(/, "expected the detail panel to render the full list of findings for the clicked element");
  for (const [label, source] of [["GenesisTileMap", tileMap], ["DraftLocalMap", localMap]] as const) {
    pin.doesNotMatch(
      source,
      /if \(refs\[0\]\) setSelected/,
      `${label} must not collapse a multi-finding element's click down to only its first finding`,
    );
  }

  // FIX B3-9: a JSX `onWheel` prop is registered PASSIVE by this app's React
  // 19, so `event.preventDefault()` inside it is a silent no-op and the page
  // scrolls underneath an attempted zoom. The fix must be a real, non-passive
  // DOM listener, not the JSX prop.
  pin.doesNotMatch(localMap, /onWheel=\{handleWheel\}/, "a JSX onWheel handler cannot preventDefault under this app's React 19 -- must not be reintroduced");
  pin.match(
    localMap,
    /addEventListener\("wheel", handleWheelNative, \{ passive: false \}\)/,
    "expected the wheel handler to be attached as a real, non-passive DOM listener",
  );

  // FIX B3-10: panning/zooming sets `view` on every pointermove, and the old
  // inline JSX read `view.scale` per polyline for strokeWidth -- rebuilding
  // ~5,000 elements on every drag frame. The fix keeps `view` OFF the
  // polyline layer's props entirely (a memoized component) and uses
  // `vector-effect="non-scaling-stroke"` for constant on-screen width instead
  // of a per-render division.
  pin.match(localMap, /const NetworkLayer = memo\(function NetworkLayer\(/, "expected the polyline layer to be a component memoized against everything except `view`");
  pin.match(localMap, /vectorEffect="non-scaling-stroke"/, "expected constant-width strokes via vector-effect, not a view.scale division read on every element");
  pin.doesNotMatch(localMap, /strokeWidth=\{[\d.]+ \/ view\.scale\}/, "must not reintroduce a per-element view.scale read for stroke width");

  // FIX B3-11: `loading` was ALSO a dependency of the fetch effect above --
  // `setLoading(true)` (inside the effect) then re-fired the effect on the
  // very next render, and React runs the PREVIOUS run's cleanup first, which
  // cancelled the fetch that same call had just started. Measured (react-dom
  // 19.2.4): a near-instant response could lose the race outright (stuck
  // `loading`); anything slower survived but paid for a wasted duplicate
  // fetch. Removing the dependency is race-free at every delay tested.
  // Found live 2026-08-12, second finding on this same effect; re-verified
  // empirically 2026-08-12.
  // The dependency array moved to GenesisTileMap.tsx with the fetch, and lost
  // `open` (the panel no longer collapses -- the drawer is the disclosure now).
  // Both halves of the eternal-spinner fix are still asserted: `loadError` is
  // IN the guard, and `loading` is OUT of the deps.
  const fetchEffectDeps = sliceBetween(tileMap, "}, [network, loadError,", "]);");
  pin.match(fetchEffectDeps, /\S/, "expected the fetch effect's dependency array in GenesisTileMap.tsx (markers moved? the doesNotMatch below would pass vacuously on empty input)");
  pin.doesNotMatch(
    fetchEffectDeps,
    /\bloading\b/,
    "the fetch effect must not depend on `loading` -- it sets `loading` itself, so a dependency on it makes the effect cancel its own in-flight fetch via cleanup, producing an eternal spinner",
  );
  pin.match(
    tileMap,
    /if \(network \|\| loadError !== null \|\| !hasArtifact\) return;/,
    "expected `loadError` to stay IN the fetch guard -- without it a failed fetch clears its own error and retries forever",
  );
});

// ---------------------------------------------------------------------------
// U-1-map -- the map's own source of degrees, and the three-colour discipline
// ---------------------------------------------------------------------------

contractTest("U-1-map: the basemap's WGS84 comes from the server, through apiFetch, and is never guessed", (pin) => {
  const lib = read("lib/genesisArtifacts.ts");
  const tileMap = read("components/genesis/GenesisTileMap.tsx");
  const router = readFileSync(path.join(repoRoot, "backend", "routers", "genesis.py"), "utf-8");
  const service = readFileSync(
    path.join(repoRoot, "backend", "services", "genesis", "geojson.py"),
    "utf-8",
  );

  // ONE binding owns the route, and it goes through apiFetch -- the only path
  // that takes the same-origin proxy and attaches the session cookie plus
  // X-Workspace-Id. A bare fetch() here would silently read the DEFAULT
  // project's job, which is the named 1.4 hole.
  pin.match(
    lib,
    /await apiFetch\(`\/api\/genesis\/jobs\/\$\{jobId\}\/artifact\/geojson`/,
    "expected the GeoJSON to be read through apiFetch (cookie + workspace header)",
  );
  pin.doesNotMatch(lib, /\n\s*const r = await fetch\(/, "genesis artifact reads must not bypass the workspace-header fetch helper");
  pin.match(tileMap, /fetchGenesisDraftGeoJson\(jobId\)/, "expected the map to take its degrees from that one binding");

  // THE CONVERSION IS SERVER-SIDE, on the plane the draft RECORDED. The client
  // must not carry a projection at all: the whole reason this is honest (unlike
  // the consultant Helmert fit) is that it inverts a transform this program
  // performed, using the origin that transform wrote down.
  pin.match(service, /def plane_for_draft\(/, "expected the plane to be reconstructed server-side from the draft's own metadata");
  pin.match(service, /LocalTangentPlane\(lat0_deg=float\(lat\), lon0_deg=float\(lon\)\)/, "expected the EXACT recorded origin, not a re-derived one");
  pin.doesNotMatch(
    withoutComments(tileMap),
    /Math\.(?:sinh|atan|radians)|20037508/,
    "the client must carry no projection arithmetic of its own -- the server owns the inverse",
  );

  // AND IT REFUSES rather than guessing. A draft with no recorded origin cannot
  // be placed; inventing one draws every link in the right shape at the wrong
  // place, which is the silent kind of wrong this product exists to refuse.
  pin.match(service, /class DraftGeoJsonRefused\(Exception\):/, "expected a named refusal for a draft that cannot be placed");
  pin.match(service, /"genesis_draft_has_no_projection_origin"/, "expected the refusal to carry a stable code the UI can translate");
  pin.doesNotMatch(
    withoutComments(service),
    /origin_lat_deg", 0\.0|lat0_deg=0\.0/,
    "a (0, 0) fallback origin would place a Perth network in the Gulf of Guinea -- there must be none",
  );

  // The route is declared ABOVE `/artifact/{kind}`; FastAPI matches in
  // declaration order, so the generic route would otherwise swallow it.
  pin.match(
    router,
    /@router\.get\("\/jobs\/\{job_id\}\/artifact\/geojson"\)[\s\S]*?@router\.get\("\/jobs\/\{job_id\}\/artifact\/\{kind\}"\)/,
    "expected the geojson route to be declared before the generic artifact-kind route",
  );
  pin.match(
    router,
    /def get_artifact_geojson\(job_id: str, user: dict\[str, Any\] = Depends\(current_user\)\)/,
    "expected the geojson route to require a signed-in user, like every other artifact read",
  );
});

contractTest("U-1-map: the genesis screens use three accent colours, and the fourth state is mono", (pin) => {
  // A USER DECISION, 2026-08-13, resolving the amber question this backlog had
  // carried open since the palette audit. The accents are rose (fail/refuse),
  // emerald (pass), sky (selection/info/link) -- and nothing else.
  //
  // WHY DROPPING AMBER DOES NOT WEAKEN THE not_checkable DOCTRINE, which is the
  // only thing at stake: R1.5 requires that a check which could not RUN never
  // reads as fine. globals.css already remaps the ENTIRE amber family to
  // neutral for this theme, so amber was never carrying that weight here -- it
  // was rendering grey, at the same weight as ordinary chrome. The replacement
  // is louder, not quieter: ink-strength text inside a DOUBLED neutral border
  // (and, on the map, a heavier dashed stroke), beside a green pass chip. The
  // WORD is printed either way. A hue was dropped; no claim was.
  const files: Array<[string, string]> = [
    ["app/genesis/page.tsx", read("app/genesis/page.tsx")],
    // unit #23: the check-state chip/card colours (checkStateTagClasses,
    // checkCellClasses) moved to the detail route with the rest of the
    // gate grid -- swept here too, not just on the hub that no longer
    // renders them.
    ["app/genesis/jobs/[id]/page.tsx", read("app/genesis/jobs/[id]/page.tsx")],
    ["components/genesis/findings.tsx", read("components/genesis/findings.tsx")],
    ["components/genesis/GenesisTileMap.tsx", read("components/genesis/GenesisTileMap.tsx")],
    ["components/genesis/DraftLocalMap.tsx", read("components/genesis/DraftLocalMap.tsx")],
    ["components/genesis/GenesisComparisonPanel.tsx", read("components/genesis/GenesisComparisonPanel.tsx")],
  ];
  for (const [label, source] of files) {
    // `withoutComments`, because these files NAME the banned family while
    // explaining the conversion -- the pin is about live markup.
    pin.doesNotMatch(
      withoutComments(source),
      /(?:border|bg|text|ring|from|to)-amber-/,
      `${label} must carry no amber-family class -- the accents are rose, emerald and sky, and every former amber state renders mono`,
    );
    pin.doesNotMatch(
      withoutComments(source),
      /#d97706|#f59e0b|#fbbf24/,
      `${label} must carry no amber hex either -- the sweep covers the map strokes, not only the Tailwind classes`,
    );
  }

  // The three that remain, each still meaning exactly one thing.
  const findings = read("components/genesis/findings.tsx");
  pin.match(findings, /REFUSE_COLOR = "#e11d48"/, "expected rose to stay the refuse tier's colour");
  pin.match(findings, /SELECTED_COLOR = "#0ea5e9"/, "expected sky to stay reserved for the clicked element");

  // The warn tier is told apart by WEIGHT and SHAPE now, so the dash is
  // load-bearing -- and both renderers plus the legend must use the same one,
  // or the legend stops describing the map.
  pin.match(findings, /export const WARN_DASH = /, "expected one shared dash pattern for the warn tier");
  // BOTH warn strokes -- links and connectors are drawn by two separate passes,
  // and dashing only one of them would leave half the warn tier looking normal.
  const svgDashes = (read("components/genesis/DraftLocalMap.tsx").match(/strokeDasharray=\{WARN_DASH\}/g) ?? []).length;
  pin.match(`count=${svgDashes}`, /^count=2$/, "expected the SVG canvas to dash BOTH its warn strokes (links and connectors)");
  pin.match(
    read("components/genesis/GenesisTileMap.tsx"),
    /dashArray: pass === "warn" \? WARN_DASH : undefined/,
    "expected the leaflet layer to dash its warn strokes with the same pattern",
  );
  pin.match(findings, /dashed \? \(/, "expected the legend swatch to show the dash, so it still describes what the map draws");
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
  // unit #23: GenesisComparisonPanel's mount point moved to the detail route.
  pin.match(read("app/genesis/jobs/[id]/page.tsx"), /currentReportError=\{reportError\}/, "expected the detail page to forward its report-fetch error into the comparison panel");

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
    // other cells. The foreground moved from `text-rose-900` to the ink ramp on
    // 2026-08-13 under the tinted-box rule (see "U-1: three accent colours,
    // tint in the fill, ink in the text"): the ERROR STYLE is what this pin is
    // about and it is unchanged -- rose fill, rose border -- while the text
    // stops taking its colour from the same family it sits on.
    pin.match(
      page,
      /invalid\s*\n\s*\? "border-rose-400 bg-rose-50 text-slate-800"\s*\n\s*: "border-slate-300"/,
      "expected an invalid link-id cell to render its own error style (rose fill + border, ink-ramp text)",
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

// ---------------------------------------------------------------------------
// Unit #28 -- the observation STORE (backend/routers/observations.py) gets a
// route. Two tests: the API bindings (lib/api.ts, the filename must travel
// verbatim -- the Daejeon adapter reads the site id and window out of it) and
// the page's own behaviour (a 422 gate refusal renders as a named block, and
// the master-detail selection never becomes a dynamic-route navigation).
// ---------------------------------------------------------------------------

contractTest("unit #28: the observation store API bindings stay wired, filenames travel verbatim", (pin) => {
  const api = read("lib/api.ts");
  const observationsSection = sliceBetween(
    api,
    "// Observation store (unit #28)",
    "/** One row of `GET /api/admin/workspaces/{id}/members`",
  );
  pin.match(observationsSection, /export class ObservationRefusalError extends Error/, "expected a dedicated refusal error type, not a generic Error");
  pin.match(observationsSection, /export async function uploadObservationWorkbook\(file: File\)/, "expected a typed upload helper");
  // THE FILENAME MUST TRAVEL VERBATIM -- the router's own docstring: the
  // Daejeon adapter reads the site id and the declared collection window out
  // of the file name, so a client-side rename would destroy both.
  pin.match(
    observationsSection,
    /filename=\$\{encodeURIComponent\(file\.name\)\}/,
    "expected the upload query string to carry file.name untouched, only percent-encoded",
  );
  pin.doesNotMatch(
    observationsSection,
    /file\.name\.replace|sanitizeFilename|renameUpload/,
    "expected no filename transform anywhere in the observation upload path",
  );
  pin.match(
    observationsSection,
    /apiMutationFetch\(path, \{ method: "POST", cache: "no-store", body: file \}\)/,
    "expected the upload to reuse the shared mutation-fetch helper with a raw-bytes body, exactly like uploadGenesisOsm",
  );
  // The gate's per-issue citation survives the wire type (refuter finding A,
  // 2026-08-14): services/observations.py stores it verbatim, and a type that
  // omits the field is how it silently vanished from the first cut's UI.
  pin.match(observationsSection, /citation: string \| null;/, "expected the gate-issue wire type to carry the citation the backend stores verbatim");
  pin.match(observationsSection, /export async function ingestObservationSet/, "expected a typed ingest helper");
  pin.match(observationsSection, /apiMutationFetch\("\/api\/observations\/ingest"/, "expected ingest to POST the documented route");
  pin.match(observationsSection, /export async function fetchObservationSets/, "expected a typed sets-list helper");
  pin.match(observationsSection, /export async function fetchObservationSet\(setId: string\)/, "expected a typed set-detail helper");
  pin.match(observationsSection, /export async function deleteObservationSet/, "expected a typed delete helper");
  // Every call here has to go through apiFetch/apiMutationFetch (which inject
  // X-Workspace-Id via sameOriginRequestInit/requestInitForBase) rather than a
  // hand-rolled `fetch(...)`, which would silently drop the workspace scope --
  // see lib/api.ts's own module comment on why the header is injected only at
  // those two choke points.
  pin.doesNotMatch(
    observationsSection,
    /(?<![A-Za-z])fetch\(/,
    "expected every observation call to go through apiFetch/apiMutationFetch, never a bare fetch()",
  );
});

contractTest("unit #28: the observations page renders a 422 gate refusal as a named block, and master-detail needs no dynamic route", (pin) => {
  const page = read("app/observations/page.tsx");
  const help = read("lib/help-content.tsx");
  const docs = read("app/docs/page.tsx");

  // A gate refusal (422, nothing stored) renders the backend's OWN localized
  // sentence plus its findings verbatim -- never a generic error toast.
  pin.match(page, /ingestError instanceof ObservationRefusalError/, "expected the ingest error path to distinguish a structured refusal");
  pin.match(page, /ingestError\.messages\[lang\]/, "expected the refusal's own ko\/en\/ja sentence to render, never a re-worded one");
  pin.match(page, /ingestError\.detail\.gate as ObservationGateSummary/, "expected a gate refusal's findings to be read from detail.gate");
  pin.match(page, /<GateIssuesList gate=\{gate\} t=\{t\} lang=\{lang\} \/>/, "expected the gate findings to render through the shared list, verbatim per issue");
  // Refuter finding A (2026-08-14): the backend sends each issue's citation
  // ("every finding cites its rule" -- the MRWA-authority doctrine) and the
  // first cut dropped it. Each issue renders ITS OWN localized sentence and,
  // when present, its citation line.
  pin.match(page, /issue\.messages\[lang\] \|\| issue\.message/, "expected each gate issue to render its own localized sentence, not one fixed language");
  pin.match(page, /\{t\("근거", "Basis", "根拠"\)\}: \{issue\.citation\}/, "expected an issue's citation to render -- a finding without its rule is a bare opinion");

  // Master-detail with NO dynamic route: selection is a local id, and a
  // fetch, never a navigation to a new /observations/:id page.
  pin.match(page, /const \[selectedId, setSelectedId\] = useState<string \| null>\(null\)/, "expected row selection to be local component state");
  pin.match(page, /fetchObservationSet\(selectedId\)/, "expected selecting a row to fetch that set's detail inline");
  pin.doesNotMatch(page, /useRouter|router\.push|next\/navigation/, "expected no navigation import -- this page must never grow a dynamic detail route");

  // The upload call per staged file, filename untouched at the call site too.
  pin.match(page, /uploadObservationWorkbook\(file\)/, "expected one raw upload call per selected file");

  // Delete goes through the shared ModalDialog primitive, per the unit brief.
  pin.match(page, /import \{ ModalDialog, Panel \} from "@\/components\/genesis\/ui"/, "expected the shared Panel\/ModalDialog primitives, not page-local chrome");
  pin.match(page, /open=\{deleteTarget !== null\}/, "expected the delete confirmation to be a ModalDialog gated on the delete target");
  pin.match(page, /await deleteObservationSet\(deleteTarget\.id\)/, "expected the confirm button to call the typed delete helper");

  // THREE-COLOR RULE, frozen: rose = refusal, emerald = stored/pass, sky =
  // selection, and nothing else takes a fourth hue.
  pin.match(page, /border-rose-300 bg-rose-50/, "expected the refusal block to use the frozen rose palette");
  pin.match(page, /border-emerald-300 bg-emerald-50/, "expected the stored-success block to use the frozen emerald palette");
  pin.match(page, /selected \? "bg-sky-50" : "hover:bg-slate-50"/, "expected the selected list row to use the frozen sky selection treatment");
  pin.doesNotMatch(page, /amber|yellow/i, "expected no fourth hue on the observations screen");

  // No hand-rolled fetch on the page either -- everything goes through the
  // typed lib/api.ts helpers, which is where the workspace header is injected.
  pin.doesNotMatch(page, /(?<![A-Za-z])fetch\(/, "expected the page to call only the typed lib/api.ts helpers, never a bare fetch()");

  // Doctrine lives at /docs, not as inline prose on the page (unit #20's
  // retirement rule, restated in this file's own docs test below).
  pin.match(help, /export function observationsHelpEntries/, "expected a docs entry for what an observation set is for");
  pin.match(docs, /observationsHelpEntries\(t\)/, "expected /docs to actually render the observations entry");
});

// ---------------------------------------------------------------------------
// U-1 polish (2026-08-13) -- the colour rules for the three genesis sub-screens
// (region / demand / phases). Layout-adjacent by nature, so they live in one
// test of their own, per this file's own rule about keeping brittle style pins
// away from behavioural ones.
// ---------------------------------------------------------------------------

contractTest("U-1: three accent colours, tint in the fill, ink in the text", (pin) => {
  const files: Array<[string, string]> = [
    ["app/genesis/region/page.tsx", read("app/genesis/region/page.tsx")],
    ["app/genesis/demand/page.tsx", read("app/genesis/demand/page.tsx")],
    ["app/genesis/phases/page.tsx", read("app/genesis/phases/page.tsx")],
  ];

  // ---- RULE 1: a tinted box is a FILL, never an INK -------------------------
  //
  // REPORTED FROM LIVE USE (the user, 2026-08-13): the region page's polygon
  // info box was blue-on-blue, the same disease as the gate's green-on-green
  // pass cells. THE MECHANISM, measured against globals.css: the `@theme` dark
  // remap covers only the Tailwind shades this app happened to be using when it
  // was written -- sky 50/200/400/500/600, emerald 50-800, blue 50-800, rose
  // 50-900, amber 50-900, slate 50-950. A `text-*` shade OUTSIDE that list keeps
  // Tailwind's stock LIGHT-mode value and lands on a DARK fill of its own
  // family: `text-sky-900` (#0c4a6e) on `bg-sky-50` (remapped to #032042),
  // `text-emerald-900` (#064e3b) on `bg-emerald-50` (remapped to #1e2b22).
  //
  // The rule is family-wide rather than a per-shade allow-list, because an
  // allow-list means re-reading globals.css every time a box is added: a tinted
  // box states its tint in the BACKGROUND and the BORDER, and takes its text
  // from the neutral ink ramp.
  //
  // The pattern is anchored on a pair of quotes and forbids newlines between
  // them, so it fires on a class STRING (a className attribute or a
  // classes-returning helper) and not on the doc comments that legitimately
  // name the removed combinations while explaining them.
  const tintFamilyInk =
    /"[^"\n]*bg-(sky|emerald|blue|rose|amber)-(?:50|100)[^"\n]*text-\1-\d{2,3}[^"\n]*"|"[^"\n]*text-(sky|emerald|blue|rose|amber)-\d{2,3}[^"\n]*bg-\2-(?:50|100)[^"\n]*"/;
  // `border-sky-300` is the same fall-through in a border -- sky-300 is not in
  // the remap either, so it renders as stock #7dd3fc, a bright cyan hairline
  // nothing else in this app draws.
  const unmappedSkyBorder = /"[^"\n]*border-sky-300[^"\n]*"/;

  for (const [name, source] of files) {
    pin.doesNotMatch(
      source,
      tintFamilyInk,
      `${name}: a tinted box must carry its tint in the fill and border only -- a same-family text-* shade renders dark-on-dark under the globals.css remap`,
    );
    pin.doesNotMatch(
      source,
      unmappedSkyBorder,
      `${name}: sky-300 is outside the dark remap and renders as stock #7dd3fc -- use border-sky-200`,
    );
  }

  // The corrected forms exist, so this test fails in BOTH directions: a
  // regression that reintroduces the family ink trips the pins above, and a
  // sweep that deleted the tints instead of fixing them trips these.
  pin.match(
    files[0][1],
    /"border-sky-200 bg-sky-50 text-slate-800"/,
    "expected the region page's info tint to survive as fill + border with ink-ramp text",
  );
  pin.match(
    files[0][1],
    /"border-emerald-300 bg-emerald-50 text-slate-800"/,
    "expected the staged/pass tint to survive the same way",
  );
  pin.match(
    files[2][1],
    /"border-emerald-300 bg-emerald-50 text-slate-800"/,
    "expected the 확인됨 chip to keep its green fill with ink-ramp text",
  );

  // ---- RULE 2: three accent colours, and amber is not one of them -----------
  //
  // USER DECISION, 2026-08-13, settling the amber question this repo has
  // carried open since the palette lock. The genesis screens have exactly three
  // accent hues: rose = fail/refuse, emerald = pass, sky = selection/info/link.
  // Everything that used amber -- 확인 불가 / not_checkable, warn-tier findings,
  // 재확인 필요, rests-on-attestation, provisional chips, every amber notice box
  // -- renders MONO: the neutral ramp at ink strength, a heavier
  // `border-slate-400` hairline, and a `ring-1` on the full-width notices.
  // Distinct through WEIGHT and SHAPE, never through a fourth hue.
  //
  // THE DOCTRINE, which is the only thing that had to survive: not_checkable
  // must still not read as "fine". A mono chip with a heavy ring sitting beside
  // a green pass chip is visibly NOT the green state, and its own word
  // (확인 불가 / 재확인 필요 / 경고) is printed on it either way. WHAT IS
  // DROPPED IS A HUE, NEVER A CLAIM -- and on this theme it was barely a hue:
  // globals.css:199-211 already remapped the whole amber family to white-alphas
  // and the ivory ramp, so "amber" rendered grey while still being named as a
  // warning colour in every call site. Naming the grey is the honest version.
  //
  // `withoutComments` is required here: the doc comments in all three files
  // legitimately name the banned classes while explaining the conversion, and a
  // raw doesNotMatch would fire on the explanation forever.
  const lassoMap = read("components/genesis/RegionLassoMap.tsx");
  for (const [name, source] of [...files, ["components/genesis/RegionLassoMap.tsx", lassoMap] as [string, string]]) {
    pin.doesNotMatch(
      withoutComments(source),
      /(?:border|bg|text|fill|stroke|ring|from|via|to)-amber-/,
      `${name}: amber is not one of the three accent colours (rose/emerald/sky) -- a formerly-amber state renders MONO, distinct by weight and ring`,
    );
    // The hex half of the same sweep. Leaflet takes colours as strings, so a
    // class-only pin would have left the lasso's own amber bounding box and its
    // yellow first corner exactly where they were.
    pin.doesNotMatch(
      withoutComments(source),
      /#d97706|#f59e0b|#fbbf24|#fef08a|#facc15|#eab308/i,
      `${name}: no amber or yellow hex either -- the sweep covers map strokes and markers, not only Tailwind classes`,
    );
  }
  // The lasso's substitutions, both kept: a neutral residual frame that is still
  // drawn (deleting it would hide the residual the metre band is measured on),
  // and a first corner told apart by size and fill rather than by a fourth hue.
  pin.match(
    lassoMap,
    /color: "#898781",\s*\n\s*weight: 1,/,
    "expected the residual bounding box to keep its hairline in the neutral muted grey",
  );
  // UNIT #48 MOVED THIS, and moved nothing else about it. The corners became
  // `L.marker` + `L.divIcon` because Leaflet implements DRAGGING on Marker
  // alone, so `circleMarker`'s `radius` became the handle's own pixel size --
  // same two sizes, same reason: with amber gone, the first corner (the one a
  // closing double-click aims at) is findable by SIZE and FILL or not at all.
  pin.match(
    lassoMap,
    /HANDLE_SIZE_PX = \(isFirst: boolean\) => \(isFirst \? 14 : 11\)/,
    "expected the ring's first corner to be findable by SIZE now that it cannot be yellow",
  );
  pin.match(
    lassoMap,
    /fillColor: index === 0 \? "#fafaf9" : "#e0f2fe"|background:\$\{index === 0 \? "#fafaf9" : "#e0f2fe"\}/,
    "expected the first corner's ivory centre to survive the handle rewrite -- size AND fill are what replaced the hue",
  );
  // MONO exists rather than the states having been quietly de-emphasised into
  // the page background: the heavy hairline is what carries them now.
  pin.match(
    files[1][1],
    /"border-slate-400 bg-slate-100 text-slate-800"/,
    "expected the demand page's WARN issue to render mono with a heavier hairline, not as a plain muted row",
  );
  pin.match(
    files[2][1],
    /"border-slate-400 bg-slate-100 text-slate-800"/,
    "expected 재확인 필요 and 확인 요망 to render mono with a heavier hairline",
  );
  pin.match(
    files[2][1],
    /border-slate-400 bg-slate-100 px-4 py-3\.5 text-slate-800 ring-1 ring-slate-400/,
    "expected the stale-confirmation banner -- the one thing on that screen that must not be one click away -- to carry the heavy ring",
  );
  // And the words the colour used to carry are all still printed.
  pin.match(files[1][1], /t\("경고", "WARN", "警告"\)/, "the WARN word stays -- the word is what says warning, not the hue");
  pin.match(files[2][1], /t\("재확인 필요", "RE-CONFIRM", "再確認要"\)/, "재확인 필요 stays a printed word");
  pin.match(files[2][1], /t\("확인 요망", "FLAG", "要確認"\)/, "확인 요망 stays a printed word");

  // ---- RULE 3: the 12px type floor -----------------------------------------
  //
  // The user's report was "too much small text", and these three screens had
  // four sizes below the floor (9, 10, 10.5 and 11px) carrying real content --
  // the region measurement's coordinate readout, the phases state chips, every
  // provenance line. Console density: section titles 15px, body 13, secondary
  // 12 muted, nothing under 12.
  for (const [name, source] of [...files, ["components/genesis/RegionLassoMap.tsx", read("components/genesis/RegionLassoMap.tsx")] as [string, string]]) {
    pin.doesNotMatch(
      withoutComments(source),
      /text-\[(?:\d|10|10\.5|11)(?:\.\d+)?px\]/,
      `${name}: nothing under 12px survives the density pass -- raise it to text-xs`,
    );
  }
});

// ---------------------------------------------------------------------------
// Unit #19 -- clicking a link/connector on the genesis map selects it and
// shows what the gate said about it, through the SAME resolver the reverse
// (finding -> geometry) direction already used. Single concern: this test is
// about the SELECTION mechanism (one state, one resolver, both renderers
// clickable, click-empty-clears) -- not a restyle, and not the B3 detail-panel
// pins above, which cover the finding/excluded-zone arms this unit left alone.
// ---------------------------------------------------------------------------

contractTest("U-19: map selection is one state, driven by one resolver, in both renderers", (pin) => {
  const findings = read("components/genesis/findings.tsx");
  const tileMap = read("components/genesis/GenesisTileMap.tsx");
  const localMap = read("components/genesis/DraftLocalMap.tsx");

  // THE SHARED RESOLVER. `resolveFindingHighlight` is called from exactly TWO
  // places -- both in findings.tsx -- and neither renderer re-implements any
  // part of the id-or-exact-name match to answer "which findings touch this
  // clicked element": a click reads `highlights.byLink`/`byConnector`, which
  // `buildHighlights` already built by running the one resolver over every
  // finding. Counted rather than matched once, so a THIRD call site (a
  // second, independently-drifting implementation) fails this pin even if it
  // happens to agree with the first today.
  const resolverCalls = (findings.match(/resolveFindingHighlight\(ref\.finding, network\)/g) ?? []).length;
  pin.match(
    `count=${resolverCalls}`,
    /^count=2$/,
    "expected resolveFindingHighlight to be called from exactly two places in findings.tsx (buildHighlights, and selectedHighlightOf's finding arm)",
  );
  for (const [label, source] of [["GenesisTileMap", tileMap], ["DraftLocalMap", localMap]] as const) {
    pin.doesNotMatch(
      source,
      /resolveFindingHighlight\(/,
      `expected ${label} never to call resolveFindingHighlight directly -- the map-to-finding direction reads highlights.byLink/byConnector, which buildHighlights already computed with it`,
    );
  }

  // A clicked element's findings come from that SAME per-element grouping --
  // not a fresh match against the clicked id/name.
  pin.match(
    tileMap,
    /const refs = highlights\.byLink\.get\(linkId\) \?\? \[\];/,
    "expected a clicked link's findings to come from highlights.byLink, the resolver's own output",
  );
  pin.match(
    tileMap,
    /const refs = highlights\.byConnector\.get\(connectorId\) \?\? \[\];/,
    "expected a clicked connector's findings to come from highlights.byConnector, the resolver's own output",
  );

  // EVERY element is selectable now, not only one a finding already
  // highlighted -- `refs` is legitimately empty for most clicks, and
  // selection must not be gated on it being non-empty (that was the OLD
  // behaviour this unit replaced).
  pin.match(
    findings,
    /kind: "element"; elementKind: "link" \| "connector"; id: number; name: string; refs: FindingRef\[\]/,
    "expected a selection to carry an element identity (kind+id+name+refs), not only a finding list",
  );
  pin.doesNotMatch(
    tileMap,
    /refs\.length/,
    "expected the tile map's handlers never to READ refs.length -- any gate on it is the old 'only finding-bearing elements are selectable' behaviour returning in a new spelling (the refuter proved the narrow `> 0` pin was blind to `if (refs.length)`)",
  );
  // ...and the positive half the refuter demanded: setSelected follows the
  // refs lookup UNCONDITIONALLY in both handlers -- no branch in between.
  pin.match(
    tileMap,
    /const refs = highlights\.byLink\.get\(linkId\) \?\? \[\];\s*const link = linksById\.get\(linkId\);\s*setSelected\(\{ kind: "element", elementKind: "link"/,
    "expected handleSelectLink to select unconditionally -- nothing between the refs lookup and setSelected",
  );
  pin.match(
    tileMap,
    /const refs = highlights\.byConnector\.get\(connectorId\) \?\? \[\];\s*const connector = connectorsById\.get\(connectorId\);\s*setSelected\(\{ kind: "element", elementKind: "connector"/,
    "expected handleSelectConnector to select unconditionally -- nothing between the refs lookup and setSelected",
  );

  // ONE selection state, owned by the container (GenesisTileMap) -- neither
  // renderer grows its own copy.
  const selectionStateCount = (tileMap.match(/useState<SelectedItem>/g) ?? []).length;
  pin.match(
    `count=${selectionStateCount}`,
    /^count=1$/,
    "expected exactly one useState<SelectedItem> in GenesisTileMap.tsx -- FindingsTileLayer must not carry its own copy",
  );
  pin.doesNotMatch(localMap, /useState<SelectedItem>/, "expected DraftLocalMap not to carry its own selection state either");
  pin.match(tileMap, /onClearSelection=\{handleClearSelection\}/, "expected the leaflet layer to receive the shared clear function (matched twice: itself and DraftLocalMap)");
  pin.match(localMap, /onClearSelection: \(\) => void;/, "expected DraftLocalMap to accept the same clear callback contract, not invent its own");

  // EVERY FEATURE IS CLICKABLE on the leaflet renderer now -- U-19 removed the
  // old tier gate (`clickable = pass === "refuse" || pass === "warn"`), which
  // is also what gives every feature the pointer cursor on hover (leaflet's
  // canvas renderer toggles `leaflet-interactive` -- `cursor: pointer` --
  // for any layer with `interactive: true`, with no extra CSS needed).
  pin.match(tileMap, /interactive: true,/, "expected every leaflet feature to be interactive, not only refuse/warn-tier ones");
  pin.doesNotMatch(
    tileMap,
    /const clickable = pass === "refuse" \|\| pass === "warn";/,
    "expected the tier-gated clickable flag to be gone -- every pass is now clickable",
  );

  // CLICKING EMPTY MAP CLEARS -- and a feature's own click must stop the
  // event reaching that map-level handler, or selecting a feature would
  // immediately clear itself right back out (leaflet Path layers bubble
  // their click to the map by default: `bubblingMouseEvents: true`).
  pin.match(
    tileMap,
    /map\.on\("click", \(\) => handlers\.current\.onClearSelection\(\)\);/,
    "expected clicking the empty basemap to clear the selection",
  );
  pin.match(
    tileMap,
    /L\.DomEvent\.stopPropagation\(event\);/,
    "expected a feature click to stop propagation so it cannot also clear the selection it just set",
  );

  // THE SVG FALLBACK gets the SAME semantics: every normal-tier polyline is
  // clickable too now (previously only warn/refuse tier had onClick -- these
  // two exact lines are new, using the block's own `connector`/`link` loop
  // variable rather than the warn/refuse blocks' `connectorId`/`linkId`).
  pin.match(localMap, /onClick=\{\(\) => onSelectConnector\(connector\.id\)\}/, "expected a normal-tier connector to be clickable, not only warn/refuse ones");
  pin.match(localMap, /onClick=\{\(\) => onSelectLink\(link\.id\)\}/, "expected a normal-tier link to be clickable, not only warn/refuse ones");
  // The background rect distinguishes a CLICK (clears) from a DRAG (pans) by
  // movement distance -- it is both surfaces, so a pan must not also clear.
  pin.match(
    localMap,
    /if \(movedX <= BACKGROUND_CLICK_TOLERANCE && movedY <= BACKGROUND_CLICK_TOLERANCE\) \{\s*\n\s*onClearSelection\(\);/,
    "expected a click (not a drag) on empty canvas to clear the selection",
  );

  // THE DETAIL STRIP shows only what travels -- kind, mono id, name if
  // present, then the matching findings -- and says so plainly when there are
  // none, never an empty box.
  pin.match(
    findings,
    /\{selected\.elementKind === "link" \? t\("링크", "Link", "リンク"\) : t\("커넥터", "Connector", "コネクタ"\)\}/,
    "expected the detail strip's kind label to read 링크/커넥터",
  );
  // RE-AIMED, unit #41: the strip reads ROAD NAME first with the machine
  // identity small behind it (the user could not place "1547" on a map). Both
  // halves of the original claim still hold and are pinned where they now
  // live: the id still travels, and nothing renders as a blank line.
  pin.match(
    findings,
    /<SubjectLabel\s+subject=\{selected\.name\}\s+id=\{selected\.id\}/,
    "expected the clicked element's own id to still travel with the strip -- unit #41 moved it behind the name, it did not drop it",
  );
  pin.match(
    findings,
    /const tail = \[parts\.trailing, shownId === null \? "" : `#\$\{shownId\}`\]\.filter\(Boolean\)\.join\(" · "\);/,
    "expected the trailing identity to be assembled from the parts that EXIST -- an absent id or direction must not print a stray separator",
  );
  pin.match(
    findings,
    /\{tail && <span className="ml-1\.5 font-mono text-\[14px\] font-normal text-slate-500">\{tail\}<\/span>\}/,
    "expected the machine identity to render mono and small, and only when there is one -- never a blank line",
  );
  pin.match(
    findings,
    /확인된 소견 없음/,
    "expected an element with no matching findings to say so plainly, not render an empty list or hide the panel",
  );
  pin.doesNotMatch(
    findings,
    /lane counts|link length|length_m|lane_width_m/,
    "the detail strip must show only what the geojson contract carries (id, kind, name, findings) -- no invented attributes",
  );
});

// ---------------------------------------------------------------------------
// Unit #19 round 2 -- the genesis draft map's roads become width-scaled
// ribbons (real lane count x lane width), reaching visual parity with the
// dashboard/network tab, per the user's direct request. `NetworkViewer.tsx`
// and `TrajectoryLayer.tsx` use CRLF line endings (unlike the genesis files
// round 1 touched) -- every pattern below either avoids `\n`/`$` anchors
// entirely or reads through a CRLF-normalized copy, so a line-ending quirk in
// a pre-existing file can never make a pin pass vacuously.
// ---------------------------------------------------------------------------

contractTest("U-19 round 2: one width formula draws width-scaled ribbons in both genesis renderers", (pin) => {
  const roadWidth = read("lib/roadWidth.ts");
  const networkViewer = read("components/NetworkViewer.tsx").replace(/\r\n/g, "\n");
  const trajectoryLayer = read("components/TrajectoryLayer.tsx").replace(/\r\n/g, "\n");
  const genesisArtifacts = read("lib/genesisArtifacts.ts");
  const findings = read("components/genesis/findings.tsx");
  const tileMap = read("components/genesis/GenesisTileMap.tsx");
  const localMap = read("components/genesis/DraftLocalMap.tsx");

  // ---- ONE formula, extracted (not duplicated) ------------------------------
  //
  // `lib/roadWidth.ts` is the single source; NetworkViewer.tsx (which used to
  // define linkWidthMeters/DEFAULT_LANE_M/MIN_ROAD_PX locally) and
  // TrajectoryLayer.tsx (which used to import them FROM NetworkViewer) both
  // now import from the shared module instead -- a pure extraction, so the
  // dashboard/network tab's own rendering is provably unchanged.
  pin.match(roadWidth, /export function linkWidthMeters\(l: RoadWidthInput\): number \{/, "expected the shared module to export the one width formula");
  // Unit #29 ("port lane markings to the genesis draft maps") widened this
  // ONE import statement to also carry the lane-marking geometry constants
  // and the two offset helpers -- sliceBetween isolates just the import
  // block so this pin keeps proving the SAME thing (NetworkViewer imports
  // the width formula rather than defining it locally) without caring about
  // the extra names now sharing the statement.
  const networkViewerRoadWidthImport = sliceBetween(networkViewer, "import {\n  CONNECTOR_DASH_M,", '} from "@/lib/roadWidth";');
  pin.match(networkViewerRoadWidthImport, /\S/, "expected NetworkViewer's shared-module import block to still be found (marker moved?)");
  pin.match(
    networkViewerRoadWidthImport,
    /DEFAULT_LANE_M,[\s\S]*MIN_ROAD_PX,[\s\S]*linkWidthMeters,/,
    "expected NetworkViewer to import the formula rather than defining its own",
  );
  pin.doesNotMatch(networkViewer, /export function linkWidthMeters\(/, "expected NetworkViewer's own local linkWidthMeters definition to be gone");
  pin.doesNotMatch(networkViewer, /export const DEFAULT_LANE_M/, "expected NetworkViewer's own local DEFAULT_LANE_M export to be gone");
  pin.doesNotMatch(networkViewer, /export const MIN_ROAD_PX/, "expected NetworkViewer's own local MIN_ROAD_PX export to be gone");
  pin.match(trajectoryLayer, /import \{ DEFAULT_LANE_M, MIN_ROAD_PX, linkWidthMeters \} from "@\/lib\/roadWidth";/, "expected TrajectoryLayer to follow NetworkViewer's import to the same shared module");
  pin.doesNotMatch(
    trajectoryLayer,
    /import \{[^}]*linkWidthMeters[^}]*\} from "@\/components\/NetworkViewer"/,
    "expected TrajectoryLayer's old import of linkWidthMeters FROM NetworkViewer to be gone -- both now take it from lib/roadWidth",
  );

  // ---- The genesis adapter delegates, it does not reimplement ---------------
  //
  // Neither genesis type carries width_total/lane_widths -- the adapter turns
  // one measured lane_width_m into a lane_widths array and lets the SAME
  // linkWidthMeters sum it, rather than multiplying lanes x lane_width_m by
  // hand (which would be a second formula that happens to agree today).
  // Unit #29 added a sibling import (laneBoundaryOffsets, for the genesis
  // lane-divider adapter below) to this same statement -- still one import
  // from the shared module, not a second linkWidthMeters copy.
  pin.match(genesisArtifacts, /import \{ laneBoundaryOffsets, linkWidthMeters \} from "@\/lib\/roadWidth";/, "expected the genesis adapter to import the shared formula");
  pin.match(
    genesisArtifacts,
    /export function genesisElementWidthMeters\(element: \{ lanes: number; lane_width_m: number \}\): number \{/,
    "expected one named adapter function for both GenesisDraftLink and GenesisDraftConnector",
  );
  pin.match(
    genesisArtifacts,
    /return linkWidthMeters\(\{ lane_widths: new Array\(laneCount\)\.fill\(element\.lane_width_m\), lanes: element\.lanes \}\);/,
    "expected the adapter to hand a shaped object to linkWidthMeters, not compute a total itself",
  );
  pin.doesNotMatch(
    genesisArtifacts,
    /lanes\s*\)?\s*\*|\*\s*\(?\s*(?:element\.)?lane_width_m/,
    "expected no hand-multiplied lanes x lane_width_m anywhere in the genesis adapter, under ANY spacing or parenthesisation -- that arithmetic must live inside linkWidthMeters alone",
  );
  // And neither renderer re-implements the multiplication either -- both must
  // call the one adapter function to get a width.
  for (const [label, source] of [["GenesisTileMap", tileMap], ["DraftLocalMap", localMap]] as const) {
    pin.doesNotMatch(
      source,
      /\.lanes \* .*\.lane_width_m|lane_width_m.*\*.*\.lanes/,
      `expected ${label} never to multiply lanes by lane_width_m itself -- it must call genesisElementWidthMeters`,
    );
    pin.match(
      source,
      /genesisElementWidthMeters\(/,
      `expected ${label} to draw its road width through the one shared adapter`,
    );
  }

  // ---- Zoom-reactive weight on the leaflet renderer --------------------------
  //
  // Real metres only mean a fixed pixel count AT one zoom level -- without a
  // redraw on "zoomend" a road's weight would freeze at whatever it was on the
  // first paint and stop tracking the zoom the user is actually looking at.
  pin.match(tileMap, /map\.on\("zoomend", \(\) => setZoomTick\(\(current\) => current \+ 1\)\);/, "expected a zoomend listener to trigger a redraw");
  pin.match(
    tileMap,
    /const pxPerMeter = 1 \/ metersPerPixel\(map\.getCenter\(\)\.lat, map\.getZoom\(\)\);/,
    "expected pixels-per-metre to be recomputed from the map's OWN current zoom/centre on every redraw, never cached",
  );
  const drawEffectDeps = sliceBetween(tileMap, "}, [ready, geo, linksById, connectorsById,", "]);");
  pin.match(drawEffectDeps, /\S/, "expected the draw effect's dependency array (markers moved? the doesNotMatch below would pass vacuously on empty input)");
  pin.match(drawEffectDeps, /\bzoomTick\b/, "expected the draw effect to depend on zoomTick, or a zoom change would never trigger a redraw");
  pin.doesNotMatch(
    tileMap,
    /pass === "selected" \? 6 : pass === "refuse" \? 5 : pass === "warn" \? 4 : isConnector \? 1 : 2/,
    "expected the old flat 1px/2px normal-tier weight to be gone",
  );
  pin.match(
    tileMap,
    /: Math\.max\(widthMeters \* pxPerMeter, MIN_ROAD_PX\);/,
    "expected the normal tier's weight to be the real width in metres converted to px, floored",
  );
  // refuse/warn/selected keep their EXISTING fixed weights -- only the normal
  // ribbon's formula changed.
  pin.match(tileMap, /pass === "selected"\s*\n\s*\? 6\s*\n\s*: pass === "refuse"\s*\n\s*\? 5\s*\n\s*: pass === "warn"\s*\n\s*\? 4\s*\n/, "expected refuse(5)/warn(4)/selected(6) weights to stay exactly the round-1 vocabulary");

  // ---- Meter-unit stroke on the SVG fallback, same floor ---------------------
  pin.match(localMap, /return \{ projX, projY, scale \};/, "expected fitProjection to expose its metres-to-view-units scale, not only the point projectors");
  pin.match(
    localMap,
    /const roadW = Math\.max\(genesisElementWidthMeters\(connector\) \* projection\.scale, MIN_ROAD_PX\);/,
    "expected a normal-tier connector's stroke width to be its real width in metres, scaled and floored",
  );
  pin.match(
    localMap,
    /const roadW = Math\.max\(genesisElementWidthMeters\(link\) \* projection\.scale, MIN_ROAD_PX\);/,
    "expected a normal-tier link's stroke width to be its real width in metres, scaled and floored",
  );
  // Anchored on the ribbon's own colour (not a bare "0.6"/"1.1" search) so this
  // stays precise about the RIBBON specifically -- the connector identity-cue
  // overlay below legitimately reuses a small fixed stroke width of its own
  // for its thin dashed centreline, and that is a different element, not the
  // pavement this pin is about.
  pin.doesNotMatch(
    localMap,
    /stroke=\{NORMAL_CONNECTOR\}\s*\n\s*strokeWidth=\{0\.6\}/,
    "expected the old flat 0.6 connector RIBBON stroke width to be gone",
  );
  pin.doesNotMatch(
    localMap,
    /stroke=\{NORMAL_LINK\}\s*\n\s*strokeWidth=\{1\.1\}/,
    "expected the old flat 1.1 link stroke width to be gone",
  );

  // ---- The floor is ONE constant, not two independently-chosen numbers ------
  //
  // Unit #29 widened both genesis renderers' roadWidth import to also carry
  // the lane-marking geometry constants and offsetPolyline -- sliceBetween
  // isolates just the import block so these two pins keep proving the SAME
  // thing (MIN_ROAD_PX comes from the shared module, not a local invention).
  const tileMapRoadWidthImport = sliceBetween(tileMap, "import {\n  LANE_DASH_M,", '} from "@/lib/roadWidth";');
  const localMapRoadWidthImport = sliceBetween(localMap, "import {\n  LANE_DASH_M,", '} from "@/lib/roadWidth";');
  pin.match(tileMapRoadWidthImport, /\bMIN_ROAD_PX,/, "expected the tile map to import the shared floor, not invent its own");
  pin.match(localMapRoadWidthImport, /\bMIN_ROAD_PX,/, "expected the SVG fallback to import the SAME shared floor");

  // ---- Connector vs. link: mirrors NetworkViewer, not a third convention ----
  //
  // NetworkViewer.tsx's own comment, read directly: connectors get the SAME
  // width rule and floor as links (pavement, not annotation) -- so this pin
  // is deliberately AGAINST a narrower connector ribbon, which would be
  // inventing a convention NetworkViewer itself does not use.
  pin.match(
    networkViewer,
    /Connectors use the SAME width rule and floor as links/,
    "expected NetworkViewer's own connector-width doctrine to still say what this unit mirrors (source moved? re-verify the citation)",
  );
  pin.match(findings, /export const CONNECTOR_IDENTITY_DASH = "2 6";/, "expected one shared connector-identity dash pattern");
  pin.doesNotMatch(
    findings,
    /CONNECTOR_IDENTITY_DASH = "7 5"/,
    "the connector identity dash must never equal WARN_DASH's rhythm -- 'this is a connector' and 'this failed a warn check' must never share one dash pattern",
  );
  for (const [label, source] of [["GenesisTileMap", tileMap], ["DraftLocalMap", localMap]] as const) {
    pin.match(source, /CONNECTOR_IDENTITY_DASH/, `expected ${label} to draw the connector identity cue`);
  }
  pin.match(tileMap, /if \(pass === "normal" && isConnector\) \{/, "expected the identity cue on the leaflet renderer to be scoped to normal-tier connectors only -- a warn/refuse/selected connector keeps its own vocabulary, not a second dash layered on top");
  pin.match(tileMap, /interactive: false,/, "expected the leaflet identity-cue overlay to be non-interactive so the click target stays the wide ribbon");
  // Anchored to the identity-cue polyline specifically (its own dash prop
  // within ~200 chars of its own pointerEvents) -- a bare `pointerEvents="none"`
  // search would pass vacuously, since the pre-existing selected-highlight
  // overlays already carried that attribute before this unit touched the file.
  pin.match(
    localMap,
    /strokeDasharray=\{CONNECTOR_IDENTITY_DASH\}[\s\S]{0,200}pointerEvents="none"/,
    "expected the SVG identity-cue overlay specifically to be non-interactive so the click target stays the wide ribbon",
  );

  // ---- Honesty rule: no invented width when data is missing ------------------
  pin.match(
    tileMap,
    /const widthMeters = element \? genesisElementWidthMeters\(element\) : 0;/,
    "expected a feature with no matching census element to get width 0 (floored to MIN_ROAD_PX), never an invented plausible-looking width",
  );
  // The real-census measurement this rule is judged against is recorded in
  // the adapter's own header comment, not only in this test's prose.
  pin.match(
    genesisArtifacts,
    /1831 links \+ 3828 connectors,\s*\n\/\/ zero missing or zero\/negative `lanes` or `lane_width_m` in that file/,
    "expected the real-census honesty measurement to be cited in source, not only claimed in a report",
  );
});

// ---------------------------------------------------------------------------
// Unit #29 -- "port lane markings to the genesis draft maps". NetworkViewer's
// own lane-divider/connector-dash geometry (LANE_DASH_M and its siblings) and
// its two offset helpers (laneBoundaryOffsets, offsetPolyline) moved to
// lib/roadWidth.ts, the same single-source module U-19 round 2 already
// established for road width -- so a genesis link's own interior lane
// dividers draw with the EXACT geometry the dashboard/network tab draws, not
// a second formula that happens to agree today.
// ---------------------------------------------------------------------------

test("unit #29: laneBoundaryOffsets/offsetPolyline -- the shared divider-offset geometry, checked by value", () => {
  // Single-lane (and zero-lane, a defensive floor) links carry no interior
  // boundary at all.
  assert.deepEqual(laneBoundaryOffsets({ lanes: 1 }), []);
  assert.deepEqual(laneBoundaryOffsets({ lanes: 0 }), []);

  // UNIFORM lane widths reduce exactly to the closed form this unit's own
  // brief states: divider i of an L-lane link sits at (i/L - 0.5) * width.
  const closedForm = (i: number, L: number, width: number) => (i / L - 0.5) * width;

  const threeLane = laneBoundaryOffsets({ lanes: 3, lane_widths: [3, 3, 3] });
  assert.equal(threeLane.length, 2);
  assert.ok(Math.abs(threeLane[0] - closedForm(1, 3, 9)) < 1e-9, `expected ${closedForm(1, 3, 9)}, got ${threeLane[0]}`);
  assert.ok(Math.abs(threeLane[1] - closedForm(2, 3, 9)) < 1e-9, `expected ${closedForm(2, 3, 9)}, got ${threeLane[1]}`);

  // NON-uniform lane widths -- the reason the cumulative-sum form moved
  // verbatim rather than the simpler closed form: NetworkViewer's own
  // NetworkLink can carry a real per-lane breakdown, and a divider must sit
  // at the REAL cumulative boundary, not an evenly-divided one.
  const skewed = laneBoundaryOffsets({ lanes: 3, lane_widths: [2, 4, 3] }); // sum 9
  assert.deepEqual(skewed, [-2.5, 1.5]); // after lane 0 (w2): 2-4.5; after lane 1 (w4): 6-4.5

  // No lane_widths and no width_total -- falls back to lanes x DEFAULT_LANE_M
  // (linkWidthMeters's own fallback), the same shape every genesis element
  // with no per-lane data ultimately goes through too.
  const fallback = laneBoundaryOffsets({ lanes: 3 });
  const fallbackWidth = 3 * DEFAULT_LANE_M;
  assert.equal(fallback.length, 2);
  assert.ok(Math.abs(fallback[0] - closedForm(1, 3, fallbackWidth)) < 1e-9);
  assert.ok(Math.abs(fallback[1] - closedForm(2, 3, fallbackWidth)) < 1e-9);

  // offsetPolyline on a straight horizontal segment: the perpendicular is
  // straight up/down, per this module's own normal convention
  // (`[-dy/len, dx/len]`) -- a positive offset on an eastbound segment moves
  // +y, matching a divider drawn symmetrically about the centreline.
  const straight: [number, number][] = [[0, 0], [10, 0]];
  assert.deepEqual(offsetPolyline(straight, 2), [[0, 2], [10, 2]]);
  assert.deepEqual(offsetPolyline(straight, -2), [[0, -2], [10, -2]]);

  // Degenerate inputs pass through unchanged rather than throwing or NaN-ing.
  assert.deepEqual(offsetPolyline([[1, 1]], 5), [[1, 1]]); // fewer than 2 points
  assert.deepEqual(offsetPolyline(straight, NaN), straight); // non-finite distance
});

contractTest("unit #29: lane-marking geometry is ONE export, and NetworkViewer imports it rather than redefining it", (pin) => {
  const roadWidth = read("lib/roadWidth.ts");
  const networkViewer = read("components/NetworkViewer.tsx").replace(/\r\n/g, "\n");

  // ---- Single source: every constant + both helper functions exported once --
  const MOVED_CONSTANTS = [
    "MARKING_COLOR",
    "LANE_DASH_M",
    "LANE_GAP_M",
    "LANE_DASH_STROKE_M",
    "LANE_DASH_STROKE_MIN_PX",
    "CONNECTOR_DASH_M",
    "CONNECTOR_GAP_M",
    "CONNECTOR_DASH_STROKE_M",
    "CONNECTOR_DASH_STROKE_MIN_PX",
  ];
  for (const name of MOVED_CONSTANTS) {
    pin.match(roadWidth, new RegExp(`export const ${name} = `), `expected roadWidth.ts to export ${name} as the single source`);
  }
  pin.match(roadWidth, /export function laneBoundaryOffsets\(l: RoadWidthInput\): number\[\] \{/, "expected the divider-offset computation to be a pure, exported function");
  pin.match(roadWidth, /export function offsetPolyline\(pts: Pt\[\], dist: number\): Pt\[\] \{/, "expected the offset-polyline helper to be a pure, exported function");

  // ---- NetworkViewer imports every name, defines NONE of them locally -------
  //
  // Anchored on `=` right after the identifier (a local `const NAME =`) so an
  // unrelated identifier that merely shares a prefix (e.g. a hypothetical
  // `LANE_DASH_MAX`) cannot false-positive this doesNotMatch, and the pattern
  // still fires on the EXACT redefinition it exists to catch.
  for (const name of MOVED_CONSTANTS) {
    pin.doesNotMatch(
      networkViewer,
      new RegExp(`const ${name}\\s*=`),
      `expected NetworkViewer's own local \`const ${name}\` to be gone -- it must come from the import only`,
    );
  }
  pin.doesNotMatch(networkViewer, /function laneBoundaryOffsets\(/, "expected NetworkViewer's own local laneBoundaryOffsets definition to be gone");
  pin.doesNotMatch(networkViewer, /function offsetPolyline\(/, "expected NetworkViewer's own local offsetPolyline definition to be gone");
  pin.match(networkViewer, /\blaneBoundaryOffsets,\n/, "expected NetworkViewer to import laneBoundaryOffsets rather than defining it");
  pin.match(networkViewer, /\boffsetPolyline,\n/, "expected NetworkViewer to import offsetPolyline rather than defining it");

  // ---- NetworkViewer's OWN drawing call sites are unchanged ------------------
  //
  // Proves rendering did not move by one pixel: these are the exact call
  // sites that existed before the move, spelled identically, now resolving
  // to the imported functions instead of file-local ones.
  pin.match(networkViewer, /for \(const offM of laneBoundaryOffsets\(link\)\) \{/, "expected NetworkViewer's lane-divider loop to still call laneBoundaryOffsets(link) unchanged");
  pin.match(networkViewer, /const line = offsetPolyline\(pts, offM \* pxPerMeter\);/, "expected NetworkViewer's divider-path construction to be byte-identical to before the move");
  pin.match(
    networkViewer,
    /\.attr\("stroke-width", Math\.max\(LANE_DASH_STROKE_M \* pxPerMeter, LANE_DASH_STROKE_MIN_PX \/ k\)\)/,
    "expected NetworkViewer's lane-divider stroke floor to be unchanged",
  );
  pin.match(
    networkViewer,
    /\.attr\("stroke-dasharray", `\$\{LANE_DASH_M \* pxPerMeter\} \$\{LANE_GAP_M \* pxPerMeter\}`\)/,
    "expected NetworkViewer's lane-divider dash pattern to be unchanged",
  );
  pin.match(
    networkViewer,
    /\.attr\("stroke-width", Math\.max\(CONNECTOR_DASH_STROKE_M \* pxPerMeter, CONNECTOR_DASH_STROKE_MIN_PX \/ k\)\)/,
    "expected NetworkViewer's connector centre-dash stroke floor to be unchanged",
  );
});

contractTest("unit #29: both genesis draft maps draw their own interior lane dividers, in the shared geometry and colour", (pin) => {
  const genesisArtifacts = read("lib/genesisArtifacts.ts");
  const tileMap = read("components/genesis/GenesisTileMap.tsx");
  const localMap = read("components/genesis/DraftLocalMap.tsx");

  // ---- The adapter delegates, it does not reimplement ------------------------
  //
  // Same shape as genesisElementWidthMeters (U-19 round 2): neither genesis
  // type carries a lane_widths breakdown, so this builds one from the single
  // measured lane_width_m and hands it to the SAME laneBoundaryOffsets the
  // dashboard uses, rather than computing an offset formula by hand.
  pin.match(genesisArtifacts, /import \{ laneBoundaryOffsets, linkWidthMeters \} from "@\/lib\/roadWidth";/, "expected the genesis adapter to import laneBoundaryOffsets from the shared module");
  pin.match(
    genesisArtifacts,
    /export function genesisElementLaneOffsetsM\(element: \{ lanes: number; lane_width_m: number \}\): number\[\] \{/,
    "expected one named adapter function for lane-divider offsets, mirroring genesisElementWidthMeters's own shape",
  );
  pin.match(
    genesisArtifacts,
    /return laneBoundaryOffsets\(\{ lane_widths: new Array\(laneCount\)\.fill\(element\.lane_width_m\), lanes: element\.lanes \}\);/,
    "expected the lane-offset adapter to hand a shaped object to laneBoundaryOffsets, not compute an offset itself",
  );

  // ---- Both renderers import the shared offset helper and mono colour, and
  // both call the one adapter rather than hand-rolling the (i/L - 0.5)*width
  // formula themselves. -------------------------------------------------------
  for (const [label, source] of [["GenesisTileMap", tileMap], ["DraftLocalMap", localMap]] as const) {
    const roadWidthImport = sliceBetween(source, "import {\n  LANE_DASH_M,", '} from "@/lib/roadWidth";');
    pin.match(roadWidthImport, /\S/, `expected ${label}'s shared-module import block to still be found (marker moved?)`);
    pin.match(roadWidthImport, /\bMARKING_COLOR,/, `expected ${label} to import the shared mono marking colour`);
    pin.match(roadWidthImport, /\boffsetPolyline,/, `expected ${label} to import the shared offset helper`);
    pin.match(source, /genesisElementLaneOffsetsM\(/, `expected ${label} to compute lane-divider offsets through the one shared adapter`);
    pin.doesNotMatch(
      source,
      /i \/ L - 0\.5|- 0\.5\) \* (?:widthMeters|weight|roadW)/,
      `expected ${label} not to reimplement the divider-offset formula itself -- it must call genesisElementLaneOffsetsM`,
    );
  }

  // ---- DraftLocalMap: the offset formula, drawn in the shared mono colour,
  // pointer-events none (decoration, not a second click target), and gated on
  // the SAME MIN_ROAD_PX floor the ribbon itself is judged against. ----------
  pin.match(
    localMap,
    /const dividerOffsetsM = roadW > MIN_ROAD_PX \? genesisElementLaneOffsetsM\(link\) : \[\];/,
    "expected DraftLocalMap's lane dividers to be gated on the ribbon's own MIN_ROAD_PX floor",
  );
  // Zoom fix (adversarial finding #1): a divider's offset is baked into
  // VERTEX POSITIONS, which the parent `<g transform="scale(view.scale)">`
  // DOES scale -- unlike the ribbon's own `vectorEffect="non-scaling-stroke"`
  // width, which the same transform leaves alone. An offset computed as bare
  // `offM * projection.scale` (fit-scale only) is therefore correct at
  // view.scale 1 and WRONG at every other zoom: it drifts outward with the
  // group's own scale while the ribbon it is supposed to sit inside stays a
  // constant on-screen width, so the outermost divider of a wide road walks
  // outside its own ribbon as the user zooms in. The fix routes the offset
  // through `dividerOffsetCoord`, which divides by `viewScale` so the
  // group's `scale(view.scale)` multiplies it right back out to a constant
  // on-screen value -- see the value test below for the invariant itself.
  pin.doesNotMatch(
    localMap,
    /offsetPolyline\(projectedPoints, offM \* projection\.scale\)/,
    "expected the OLD zoom-incoherent divider offset (baked at fit scale, never corrected for view.scale) to be gone -- see unit #29's zoom-fix test for why it drifts outside its own ribbon under zoom",
  );
  pin.match(
    localMap,
    /offsetPolyline\(projectedPoints, dividerOffsetCoord\(offM, projection\.scale, viewScale\)\)/,
    "expected DraftLocalMap's divider path to be built via the shared offsetPolyline helper, offset through dividerOffsetCoord so the on-screen distance from the centreline stays constant across zoom instead of scaling with the group transform",
  );
  pin.match(
    localMap,
    /stroke=\{MARKING_COLOR\}[\s\S]{0,450}pointerEvents="none"/,
    "expected DraftLocalMap's lane-divider overlay to draw in the shared mono marking colour and stay non-interactive",
  );
  // Anchored on the connector block specifically (bounded by the two `.map`
  // headers) so this proves connectors do NOT get lane dividers, matching
  // NetworkViewer -- not merely that the string is absent from the whole file.
  const connectorBlock = sliceBetween(localMap, "network.connectors.map((connector) => {", "network.links.map((link) => {");
  pin.match(connectorBlock, /\S/, "expected DraftLocalMap's connector-rendering block to still be found (marker moved?)");
  pin.doesNotMatch(connectorBlock, /genesisElementLaneOffsetsM/, "expected DraftLocalMap connectors NOT to draw interior lane dividers -- only their existing CONNECTOR_IDENTITY_DASH cue");

  // ---- GenesisTileMap: same formula/colour/pointer-events discipline, but
  // gated live on the CURRENT zoom (weight, not a frozen fit-scale value) and
  // explicitly scoped to links (`!isConnector`). ------------------------------
  pin.match(
    tileMap,
    /if \(pass === "normal" && !isConnector && element && weight > MIN_ROAD_PX\) \{/,
    "expected GenesisTileMap's lane dividers to be links-only, normal-tier-only, and gated on the SAME MIN_ROAD_PX floor the ribbon itself is judged against",
  );
  pin.match(
    tileMap,
    /offsetPolyline\(projected, offM \* pxPerMeter\)/,
    "expected GenesisTileMap's divider path to be built via the shared offsetPolyline helper, in the map's own live px-per-metre",
  );
  pin.match(
    tileMap,
    /color: MARKING_COLOR,[\s\S]{0,400}interactive: false,/,
    "expected GenesisTileMap's lane-divider overlay to draw in the shared mono marking colour and stay non-interactive",
  );
});

// ---------------------------------------------------------------------------
// Unit #29 zoom fix (adversarial finding #1) -- DraftLocalMap's lane dividers
// baked their offset into vertex positions at fit scale only, so under zoom
// the offset scaled with the group transform while the ribbon's own
// vectorEffect="non-scaling-stroke" width stayed frozen: the outermost
// divider of a wide road walked outside its own ribbon. The fix is
// `dividerOffsetCoord` (lib/roadWidth.ts) plus a new sibling layer
// (`LaneDividersLayer`) that can take `view.scale` without undoing
// `NetworkLayer`'s own memoization.
// ---------------------------------------------------------------------------

contractTest("unit #29 zoom fix: dividerOffsetCoord is a pure, exported helper, and DraftLocalMap's divider layer takes view.scale on purpose", (pin) => {
  const roadWidth = read("lib/roadWidth.ts");
  const localMap = read("components/genesis/DraftLocalMap.tsx");

  pin.match(
    roadWidth,
    /export function dividerOffsetCoord\(offM: number, projectionScale: number, viewScale: number\): number \{/,
    "expected a pure, exported helper computing the screen-frozen divider offset",
  );
  pin.match(
    roadWidth,
    /return \(offM \* projectionScale\) \/ viewScale;/,
    "expected the helper to divide the fit-scale offset by viewScale, so the caller's own scale(view.scale) transform multiplies it right back out to a constant on-screen value",
  );

  // The layer that needs this: a NEW sibling of NetworkLayer, deliberately
  // NOT folded into it (a changing viewScale prop on NetworkLayer would
  // force it to rebuild all ~5,000 polylines on every wheel tick, undoing
  // the whole point of B3-10's memoization).
  pin.match(localMap, /const LaneDividersLayer = memo\(function LaneDividersLayer\(/, "expected the lane-divider layer to be its own memoized sibling component, not folded back into NetworkLayer");
  pin.match(localMap, /viewScale: number;/, "expected LaneDividersLayer to declare viewScale as a real prop, not read view.scale from a closure");
  pin.match(localMap, /<LaneDividersLayer\b[\s\S]{0,200}viewScale=\{view\.scale\}/, "expected the parent to hand LaneDividersLayer the CURRENT view.scale on every render, so it reacts to zoom");
  // The one thing this fix must NOT do: give NetworkLayer itself a view-
  // reactive prop. If this ever fires, someone "fixed" the divider bug by
  // widening NetworkLayer's own props instead of using the sibling layer,
  // which would silently reintroduce the ~5,000-element-rebuild-per-tick
  // regression B3-10 exists to prevent.
  const networkLayerProps = sliceBetween(localMap, "const NetworkLayer = memo(function NetworkLayer({", "}: {");
  pin.match(networkLayerProps, /\S/, "expected NetworkLayer's own prop list to still be found (signature moved?)");
  pin.doesNotMatch(networkLayerProps, /viewScale/, "expected NetworkLayer to stay ignorant of view.scale -- a divider's zoom-reactivity must live in LaneDividersLayer alone");

  // dividerOffsetCoord imported alongside the rest of the shared module, not
  // reimplemented locally.
  const localMapRoadWidthImport = sliceBetween(localMap, "import {\n  LANE_DASH_M,", '} from "@/lib/roadWidth";');
  pin.match(localMapRoadWidthImport, /\bdividerOffsetCoord,/, "expected DraftLocalMap to import dividerOffsetCoord from the shared module rather than dividing by view.scale inline");
});

test("unit #29 zoom fix: dividerOffsetCoord keeps a lane divider's on-screen offset constant across zoom", () => {
  // At fit scale (viewScale 1) the helper must reduce to the plain,
  // un-corrected fit-scale offset every OTHER caller (GenesisTileMap, which
  // has no equivalent frozen-ribbon problem to correct for) still uses.
  const offM = 4.2; // a representative lane-boundary offset, metres
  const projectionScale = 0.85; // a representative fit-scale metres-to-view-units factor
  assert.ok(
    Math.abs(dividerOffsetCoord(offM, projectionScale, 1) - offM * projectionScale) < 1e-9,
    "expected dividerOffsetCoord(offM, projectionScale, 1) to equal the plain fit-scale offset",
  );

  // THE REFUTER'S SCENARIO: a 6-lane link, three WHEEL_ZOOM_STEP (1.18)
  // ticks in -- the defect report's own "about 3 wheel ticks" depth at which
  // an un-corrected offset visibly exits a 6-lane ribbon. The on-screen
  // offset (coordinate * viewScale, i.e. what the parent's own
  // scale(view.scale) transform produces) must be UNCHANGED from viewScale 1.
  const zoomedViewScale = 1.18 ** 3;
  const onScreenAtFit = offM * projectionScale;
  const coordAtZoom = dividerOffsetCoord(offM, projectionScale, zoomedViewScale);
  const onScreenAtZoom = coordAtZoom * zoomedViewScale;
  assert.ok(
    Math.abs(onScreenAtZoom - onScreenAtFit) < 1e-9,
    `expected the on-screen offset to stay ${onScreenAtFit} at viewScale ${zoomedViewScale} (3 wheel ticks in), got ${onScreenAtZoom}`,
  );

  // THE INVARIANT ITSELF, generalized: dividerOffsetCoord(...) * viewScale
  // === offM * projectionScale, for ANY viewScale -- not just the one zoom
  // depth above.
  for (const viewScale of [0.5, 1, 1.18, 1.18 ** 2, 1.18 ** 5, 24]) {
    const coord = dividerOffsetCoord(offM, projectionScale, viewScale);
    assert.ok(
      Math.abs(coord * viewScale - offM * projectionScale) < 1e-9,
      `expected dividerOffsetCoord(${offM}, ${projectionScale}, ${viewScale}) * ${viewScale} === ${offM * projectionScale}, got ${coord * viewScale}`,
    );
  }
});

// ---------------------------------------------------------------------------
// Roadmap B5-2 stage 4 -- the per-finding fix card in the genesis drawer, the
// human half of "거부는 진단, 에이전트는 치료". Stage 3 (backend, commit 194a2f6)
// landed GET/POST .../fixes[/apply]; this is the client side of that door.
// ---------------------------------------------------------------------------

contractTest("B5-2 stage 4: a disposition's own status is not the actionable test", (pin) => {
  // unit #23: the fix strip moved to the detail route with the rest of the
  // drawer's content.
  const page = read("app/genesis/jobs/[id]/page.tsx");

  // THE TRAP: `_render_fix_disposition`'s own docstring (routers/genesis.py)
  // names "three shapes, never a fourth" -- an AUTOMATABLE-DECLARED mapping
  // whose builder refused for a STRUCTURAL reason (V6's MEASURED entrance is
  // the real one) reports status "AUTOMATABLE" with params: null. Gating the
  // apply button on `status === "AUTOMATABLE"` alone would render an enabled
  // button over a refusal -- `params !== null` is pinned as the actual test.
  pin.match(
    page,
    /function fixDispositionIsActionable\(disposition: GenesisFixDisposition\): boolean \{\s*\n\s*return disposition\.status === "AUTOMATABLE" && disposition\.params !== null;/,
    "expected the actionable test to require params !== null, not status alone -- status alone is true for a structural refusal too",
  );
  // And FixFindingRow's own `actionable` goes through it -- never a second
  // copy of the params !== null logic. (unit #30: no first-for-code guard in
  // front of it anymore -- every finding is its own (code, subject) target.)
  pin.match(
    page,
    /const actionable = fixDispositionIsActionable\(disposition\);/,
    "expected FixFindingRow's own actionable flag to be derived from the shared test, not a second copy of the params !== null logic",
  );
});

contractTest("unit #35: a connection is never invented -- it is testified, or attested by a named human", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");
  const api = read("lib/api.ts");
  const findingRow = sliceBetween(page, "function FixFindingRow({", "\n/**\n * V1–V9 AS A GRID");
  pin.match(findingRow, /\S/, "expected to find the FixFindingRow body (markers moved?)");

  // TIER 1 -- the source's own testimony, quoted with the ids it declares.
  // Read off the payload, never a sentence this file composes about ids it
  // guessed: the way and node numbers come from `disposition.source_testimony`.
  pin.match(
    findingRow,
    /disposition\.source_testimony\.connected_way_ids\.join\(", "\)[\s\S]{0,200}?disposition\.source_testimony\.node_id/,
    "expected the testimony quote to print the backend's own way/node ids",
  );
  // The derived to-link is exactly what a reader CAN check and a derivation
  // cannot: no tick, no approve button. The gate is on the payload's own
  // to_link, so the sentence names the link the fix would actually build to.
  pin.match(
    findingRow,
    /const needsTargetConfirmation = disposition\.verb === "add_connector";/,
    "expected the connector treatment to demand an explicit target confirmation",
  );
  pin.match(
    findingRow,
    /checked=\{targetConfirmed\}/,
    "expected the confirmation to be a real control the reader ticks, not an implied consent",
  );
  pin.match(
    findingRow,
    /링크 \$\{String\(disposition\.params\?\.to_link \?\? ""\)\}/,
    "expected the confirmation sentence to name the DERIVED to-link from the payload",
  );

  // TIER 2 -- the attestation. A basis is required, the target must be chosen
  // from the backend's own measured candidates, and BOTH gate the button.
  pin.match(
    findingRow,
    /disabled=\{applying \|\| !attestChoice \|\| !attestBasis\.trim\(\)\}/,
    "expected the attestation button gated on a chosen target AND a non-empty basis",
  );
  // RE-AIMED, unit #41: the picker stopped being a <select> of strings and
  // became a radio list whose rows light their own link on the map (a native
  // <option> cannot answer a pointer, so "이 후보가 어디인가" had no answer at
  // all). WHAT is offered is unchanged and is what this pin holds: the
  // backend's own measured candidate array, mapped one row per candidate, with
  // the value bound to the candidate's own link id -- never a text field a
  // reader could type an arbitrary id into.
  // RE-AIMED again, unit #42: the block now also renders when only the
  // dead-end answer is on offer, so the candidate list is mapped defensively
  // off an optional invitation. WHAT is offered is still the backend's own
  // measured array and nothing this client composed.
  pin.match(
    findingRow,
    /\(attestation\?\.candidates \?\? \[\]\)\.map\(\(candidate\) => \{/,
    "expected the target picker to offer the backend's own measured candidates, never a free-text link id",
  );
  pin.match(
    findingRow,
    /type="radio"[\s\S]{0,200}?value=\{candidate\.link_id\}/,
    "expected each candidate row to submit the candidate's OWN link id, not a value this client composed",
  );
  pin.doesNotMatch(
    sliceBetween(findingRow, "{worldGapAsked && (", "{t(\"근거 (필수)\""),
    /type="text"/,
    "expected no free-text link-id field beside the measured candidate list",
  );
  // The submitted params are the SERVER's derivation for that candidate -- this
  // client never composes connector geometry, and the apply path re-derives
  // and refuses anything that differs.
  pin.match(
    findingRow,
    /params: attestChoice\.params,/,
    "expected the attesting approval to submit the server-derived connector params verbatim",
  );
  // Structural, not merely conventional: there is nowhere in the request body
  // to put somebody else's name, so this client cannot attest on their behalf.
  pin.match(
    api,
    /attestation\?: \{ to_link: string; basis: string \};/,
    "expected the attestation body to carry ONLY the target and the basis -- the route stamps who and when",
  );
  pin.doesNotMatch(
    findingRow,
    /attested_by|attested_at/,
    "expected the client to be structurally unable to name an attester or a timestamp",
  );

  // Both new confidence levels are NAMED in the reader's own language, and
  // neither borrows the arithmetic wording -- the whole point of a separate
  // evidence level.
  pin.match(
    page,
    /case "SOURCE_TESTIFIED_CONNECTION":\s*\n\s*return t\("소스가 증언한 연결"/,
    "expected the source-testimony confidence to have its own ko/en/ja wording",
  );
  pin.match(
    page,
    /case "HUMAN_ATTESTED_CONNECTION":\s*\n\s*return t\("운영자가 확인한 연결"/,
    "expected the human-attestation confidence to have its own ko/en/ja wording",
  );
  // ...and the assumption tokens say what the machine contributed, so a
  // "source testified" badge can never be read as "the source drew this".
  pin.match(
    page,
    /case "CONNECTOR_GEOMETRY_CONSTRUCTED":/,
    "expected the constructed-geometry caveat to be rendered as a named assumption",
  );
  pin.match(
    page,
    /case "ATTESTED_NOT_MEASURED":/,
    "expected the attested-not-measured caveat to be rendered as a named assumption",
  );
});

contractTest("B5-2 stage 4: a refusal is never a button", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");

  // The informational branch -- NOT_AUTOMATABLE, VERB_UNREGISTERED, or an
  // AUTOMATABLE mapping's structural refusal (see the test above) -- must
  // carry no <button at all, under ANY spacing or nesting a future edit might
  // introduce. Sliced to exactly that branch so this cannot pass vacuously
  // against the button that legitimately exists two branches over.
  const informationalBranch = sliceBetween(page, "{!actionable ? (", ") : (");
  pin.match(informationalBranch, /\S/, "expected to find the informational (non-actionable) branch (markers moved?)");
  // Unit #35 put ONE control inside this branch -- the attestation block, which
  // is a QUESTION that unlocks a proposal, not a button on a refusal. Unit #38
  // put a SECOND -- the regenerate block, which is a different TREATMENT (a new
  // generation from the same source), not a fix applied to this artifact. The
  // rule is unchanged for the refusal itself, so the pin slices the branch at
  // whichever treatment block comes first: everything the reader meets FIRST
  // (the rationale, the reason code) still has no control of any kind.
  const treatmentMarkers = [
    // Unit #42 renamed this one: the world-gap block now asks ONE question with
    // two honest answers (the road continues to X / the road ends here), so it
    // renders when EITHER is on offer. Same rule, same slice.
    "{worldGapAsked && (",
    "{disposition.regenerate && (",
    // Unit #40 put a THIRD in -- the widened-region proposal, whose action
    // leaves this screen entirely for the region page's own ask-first button.
    // Same rule, same slice: the refusal a reader meets FIRST still has no
    // control of any kind on it.
    "{disposition.boundary_extension && (",
    // Unit #42's own NAMED refusal is deliberately NOT a treatment marker: it
    // renders prose and no control, so it belongs to the refusal half of the
    // slice and the no-<button pin below covers it.
  ];
  for (const marker of treatmentMarkers) {
    pin.match(
      `${informationalBranch.includes(marker)}`,
      /true/,
      `expected the informational branch to still contain ${marker} (marker moved?)`,
    );
  }
  const refusalOnly = treatmentMarkers.reduce(
    (shortest, marker) => {
      const prefix = informationalBranch.split(marker)[0];
      return prefix.length < shortest.length ? prefix : shortest;
    },
    informationalBranch,
  );
  pin.match(
    `${refusalOnly.length < informationalBranch.length}`,
    /true/,
    "expected the treatment blocks to follow the refusal text, not replace it (marker moved?)",
  );
  pin.doesNotMatch(
    refusalOnly,
    /<button/,
    "expected NO button on the refusal itself -- a refusal is information, not a disabled control",
  );
  pin.match(
    informationalBranch,
    /NEVER a button/,
    "expected the doctrine comment stating the rule to stay attached to the branch it describes",
  );

  // The actionable branch is the ONLY place a button may appear, and it is
  // real, not decorative: disabled by both an in-flight request and any
  // still-missing required value.
  const actionableBranch = sliceBetween(page, ") : (\n        <div className=\"mt-2 flex flex-col gap-2\">", "    </div>\n  );\n}");
  pin.match(actionableBranch, /\S/, "expected to find the actionable branch (markers moved?)");
  pin.match(actionableBranch, /<button/, "expected the apply button to live in the actionable branch");
  pin.match(
    actionableBranch,
    /disabled=\{applying \|\| missingRequired \|\| \(needsTargetConfirmation && !targetConfirmed\)\}/,
    "expected the apply button disabled by an in-flight submit, any unmet required field, and (unit #35) an unconfirmed connector target",
  );
});

contractTest("unit #38: a finding the census cannot correct is treated by regenerating", (pin) => {
  const api = read("lib/api.ts");
  const page = read("app/genesis/jobs/[id]/page.tsx");

  // The route goes through the typed mutation helper -- X-Workspace-Id is
  // attached there -- and it is a DIFFERENT route from the apply one, pinned
  // together so neither can quietly become the other.
  pin.match(
    api,
    /export async function regenerateGenesisJob[\s\S]*?apiMutationFetch\(`\/api\/genesis\/jobs\/\$\{jobId\}\/regenerate`/,
    "expected the regeneration to go through apiMutationFetch, not a hand-built request",
  );
  pin.match(
    api,
    /apiMutationFetch\(`\/api\/genesis\/jobs\/\$\{jobId\}\/fixes\/apply`/,
    "expected the apply route to survive beside the regenerate one -- they are two different treatments",
  );
  // The client sends the REASON and nothing else: no requester, no options, no
  // source. Every other field would be a way to change the input while calling
  // the result a treatment, and WHO asked is stamped server-side.
  pin.match(
    api,
    /regenerateGenesisJob\(jobId: string, reason\?: string\)/,
    "expected the helper to take only a job id and the requester's own sentence",
  );
  pin.doesNotMatch(
    sliceBetween(api, "export async function regenerateGenesisJob", "\n}\n"),
    /requested_by|exclude_service|source/,
    "expected the regenerate body to carry no requester, no options and no source -- the server owns all three",
  );

  // The block renders ONLY when the backend measured that this finding has the
  // regenerate treatment (key-ABSENT otherwise), and it sits inside the
  // informational branch, after the refusal it does not replace.
  pin.match(
    page,
    /\{disposition\.regenerate && \(/,
    "expected the regenerate block to be gated on the backend's own measurement, never on a status string",
  );
  pin.match(
    page,
    /재생성으로 치료/,
    "expected the treatment's ko-primary label",
  );
  pin.match(
    page,
    /disposition\.regenerate\.explanation\.ko/,
    "expected the backend's own trilingual explanation to be rendered rather than re-worded here",
  );
  pin.match(
    page,
    /regenerateGenesisJob\(jobId, `\$\{finding\.code\} \/ \$\{finding\.subject\}`\)/,
    "expected the audit sentence to quote the finding this re-derivation was asked for",
  );
});

contractTest("unit #40: a boundary-caused shortage is treated by a PRE-FILLED lasso", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");
  const region = read("app/genesis/region/page.tsx");
  const handoff = read("lib/region-prefill.ts");

  // 1. THE GATE. The action renders only when the BACKEND derived a region for
  //    this finding -- key-absent on an interior shortage, on a splice it
  //    refused by name, and on every non-V4 card. Never on a status string,
  //    and never on a client-side guess about what the boundary did.
  pin.match(
    page,
    /\{disposition\.boundary_extension && \(/,
    "expected the widen-the-boundary action to be gated on the backend's own derived extension",
  );
  const treatmentBlock = sliceBetween(
    page,
    "{disposition.boundary_extension && (",
    "{/* Unit #35 -- the ask-first QUESTION",
  );
  pin.match(treatmentBlock, /<button/, "expected to find the treatment block (markers moved?)");
  pin.doesNotMatch(
    withoutComments(treatmentBlock),
    /boundary_attribution/,
    "expected the action to key off the derived EXTENSION, never off unit #39's attribution -- an attributed shortage whose splice refused by name has no region to propose and must show no button",
  );
  pin.match(
    page,
    /경계를 넓혀 다시 생성/,
    "expected the treatment's ko-primary label",
  );

  // 2. THE HAND-OFF SPELLING. One module owns the key and the shape, and both
  //    sides import it -- a second copy of the string is how a writer and a
  //    reader drift into never meeting.
  pin.match(
    handoff,
    /export const REGION_PREFILL_STORAGE_KEY = "prism-region-prefill";/,
    "expected the sessionStorage key to be declared exactly once, in the hand-off module",
  );
  // The `, type RegionPrefill` half arrived with unit #48, whose multi-corridor
  // proposal builds the payload in a named helper rather than inline. The pin
  // still says what it always said -- the WRITE goes through the shared module,
  // never through a second copy of the key.
  pin.match(
    page,
    /import \{ stashRegionPrefill(?:, [^}]+)? \} from "@\/lib\/region-prefill";/,
    "expected the job page to write the proposal through the shared hand-off module",
  );
  pin.match(
    region,
    // Both names, from that module, in whatever order and across however many
    // lines Prettier wants -- the load-bearing fact is WHERE the region page
    // gets its reader, not the shape of the import statement.
    /import \{(?=[^}]*\btakeRegionPrefill\b)(?=[^}]*\btype RegionPrefill\b)[^}]*\} from "@\/lib\/region-prefill";/,
    "expected the region page to read the proposal through the same shared module",
  );
  for (const source of [page, region]) {
    pin.doesNotMatch(
      withoutComments(source),
      /sessionStorage/,
      "expected neither page to touch sessionStorage directly -- the hand-off module owns the store",
    );
  }
  // The read is ONE-SHOT: taken and removed together, so a refresh lands on the
  // empty ring the region page's own initial-state note promises.
  pin.match(
    handoff,
    /getItem\(REGION_PREFILL_STORAGE_KEY\);\s*\n\s*window\.sessionStorage\.removeItem\(REGION_PREFILL_STORAGE_KEY\);/,
    "expected takeRegionPrefill to remove the entry in the same breath it reads it",
  );
  // The stored polygon is RE-VALIDATED before it reaches the map: a NaN handed
  // to Leaflet throws inside the tile layer, and devtools can edit this store.
  pin.match(
    handoff,
    /if \(!Array\.isArray\(polygon\) \|\| polygon\.length < 3 \|\| !polygon\.every\(isVertex\)\) return null;/,
    "expected a stored ring to be re-validated (three corners, every vertex in range) before it is handed back",
  );

  // 3. THE PRE-FILL LANDS IN THE PAGE'S OWN STATE, so the map, the editable
  //    table and the server-side measurement all keep reading one array -- and
  //    the page's own extract button stays the only thing that fetches.
  pin.match(
    region,
    /const handed = takeRegionPrefill\(getWorkspaceId\(\)\);\s*\n\s*if \(!handed\) return;\s*\n\s*setPrefill\(handed\);\s*\n\s*setVertices\(handed\.polygon\);/,
    "expected the pre-fill to become this page's own vertices rather than a second shape beside them",
  );
  const arrival = sliceBetween(region, "const handed = takeRegionPrefill(", "const loadFetches");
  pin.match(arrival, /\S/, "expected to find the arrival effect (markers moved?)");
  pin.doesNotMatch(
    arrival,
    /startRegionFetch|startGenesisJob/,
    "expected the hand-off to fetch NOTHING on arrival -- the extract button is the ask-first gate",
  );

  // 3b. THE PROPOSAL IS STAMPED WITH ITS 프로젝트, and the reader checks it.
  //     sessionStorage is per-tab and survives the reload ProjectSelector
  //     switches projects with, so a write that lost its navigation would
  //     otherwise pre-fill ANOTHER project's extract with this one's polygon
  //     under a banner naming a job that does not exist there. (Behaviour --
  //     not just spelling -- is exercised by the imported-module test below.)
  pin.match(
    page,
    /workspace_id: getWorkspaceId\(\),/,
    "expected the job page to stamp the proposal with the project it was derived in",
  );
  pin.match(
    handoff,
    /if \(\(workspaceId \?\? null\) !== \(currentWorkspaceId \?\? null\)\) return null;/,
    "expected takeRegionPrefill to refuse a proposal stamped with another project",
  );

  // 4. THE BANNER says what the shape is treating, in the backend's own
  //    numbers, so a reader can compare it against the map instead of trusting
  //    a polygon that appeared out of nowhere.
  pin.match(
    region,
    /\{prefill && \(/,
    "expected the region page to explain itself when it opens on a shape nobody drew here",
  );
  pin.match(
    region,
    /작업 \$\{prefill\.parent_job\}의 \$\{prefill\.finding_code\} 부족분 \$\{prefill\.deficit_m\} m/,
    "expected the banner to name the parent job, the finding and the measured deficit it treats",
  );
  // ...AND it names the part of the finger the source never declared. This is
  // the screen that owns the extract button: a reader who presses it here must
  // not be able to read an extrapolated tail as measured road. The pin is
  // scoped to the banner block (not the file) because `extrapolated_m` said
  // anywhere else on the page would not be said to that reader.
  const banner = sliceBetween(region, "{prefill && (", "<div className=\"mb-4 flex flex-col gap-3\">");
  pin.match(banner, /\S/, "expected to find the arrival banner (markers moved?)");
  pin.match(
    banner,
    /\{prefill\.extrapolated_m > 0 && \(/,
    "expected the banner to state the extrapolation split whenever there is one",
  );
  pin.match(
    banner,
    /\$\{prefill\.measured_source_m\} m뿐이고, 나머지 \$\{prefill\.extrapolated_m\} m/,
    "expected the ko banner to quote BOTH numbers -- how much of the finger the source declared, and how much was drawn on the last measured bearing",
  );
  pin.match(
    banner,
    /border-sky-200 bg-sky-50/,
    "expected the banner to follow the tinted-box rule -- sky is the info tint, the ink comes from the neutral ramp",
  );
  pin.match(
    banner,
    /text-slate-800/,
    "expected the banner's ink to come from the neutral ramp, not from the sky family",
  );
});

contractTest("unit #40: the region hand-off is one-shot AND project-scoped", () => {
  // BEHAVIOUR, not spelling: `lib/region-prefill.ts` imports nothing, so it runs
  // here against a fake store. Three properties, each of which was reachable as
  // a real defect before it was pinned:
  //   * one-shot -- a second read returns null, so a proposal cannot reappear
  //     over a ring the user has since edited;
  //   * project-scoped -- a proposal stamped in project A is REFUSED (and still
  //     removed) when read in project B. sessionStorage is per-tab and survives
  //     the reload a project switch does, so "written once" is not the same as
  //     "read in the project it came from";
  //   * re-validated -- a store a devtools user edited into nonsense yields
  //     null rather than a NaN vertex that throws inside Leaflet's tile layer.
  const store = new Map<string, string>();
  const previousWindow = (globalThis as { window?: unknown }).window;
  (globalThis as { window?: unknown }).window = {
    sessionStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
      removeItem: (key: string) => void store.delete(key),
    },
  };
  try {
    const proposal = {
      polygon: [
        [36.35, 127.38],
        [36.35, 127.39],
        [36.34, 127.39],
      ] as Array<[number, number]>,
      workspace_id: "bfc69251bd04",
      parent_job: "5e550c739aed",
      finding_code: "v4_lane_drop_without_runway",
      subject: "90 (둔산로)",
      deficit_m: 147.85,
      added_area_km2: 0.012976,
      measured_source_m: 127.06,
      extrapolated_m: 50.36,
    };

    assert.equal(stashRegionPrefill(proposal), true);
    const same = takeRegionPrefill("bfc69251bd04");
    assert.equal(same?.parent_job, "5e550c739aed");
    assert.equal(same?.extrapolated_m, 50.36);
    assert.equal(
      takeRegionPrefill("bfc69251bd04"),
      null,
      "one-shot: the second read must find nothing",
    );

    // Project A's proposal, read in project B: refused, and gone afterwards.
    assert.equal(stashRegionPrefill(proposal), true);
    assert.equal(
      takeRegionPrefill("a9cbf243c5f4"),
      null,
      "a proposal derived in another 프로젝트 must never pre-fill this one's extract",
    );
    assert.equal(store.size, 0, "the refused proposal must still be removed from the store");

    // The default project is `null` on both sides, and it is not a wildcard.
    assert.equal(stashRegionPrefill({ ...proposal, workspace_id: null }), true);
    assert.equal(takeRegionPrefill("bfc69251bd04"), null);
    assert.equal(stashRegionPrefill({ ...proposal, workspace_id: null }), true);
    assert.equal(takeRegionPrefill(null)?.workspace_id, null);

    // A store edited by hand: every one of these is dropped, never repaired.
    for (const broken of [
      "{not json",
      JSON.stringify({ ...proposal, polygon: [[36.35, 127.38]] }),
      JSON.stringify({ ...proposal, polygon: [[36.35, 127.38], [36.35, 127.39], ["x", 1]] }),
      JSON.stringify({ ...proposal, polygon: [[36.35, 127.38], [36.35, 127.39], [999, 1]] }),
      JSON.stringify({ ...proposal, parent_job: "" }),
      JSON.stringify({ ...proposal, measured_source_m: "127" }),
      JSON.stringify({ ...proposal, extrapolated_m: Number.NaN }),
      JSON.stringify({ ...proposal, workspace_id: 7 }),
    ]) {
      store.set(REGION_PREFILL_STORAGE_KEY, broken);
      assert.equal(takeRegionPrefill("bfc69251bd04"), null, `expected to drop: ${broken}`);
      assert.equal(store.size, 0);
    }
  } finally {
    if (previousWindow === undefined) delete (globalThis as { window?: unknown }).window;
    else (globalThis as { window?: unknown }).window = previousWindow;
  }
});

contractTest("B5-2 stage 4: the requires-gated button and the demand-estimate confidence chip", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");

  // `missingRequired` is computed from EVERY field the backend named in
  // `disposition.requires`, not a hand-picked subset (e.g. only "volume").
  pin.match(
    page,
    /const missingRequired = disposition\.requires\.some\(\(field\) => !requiredValues\[field\]\?\.trim\(\)\);/,
    "expected the button's disabled state to be driven by every field in disposition.requires, not a hardcoded field name",
  );

  // The confidence chip must read visibly different for a demand estimate --
  // mono + wording (a doubled border + dotted underline, the SAME mono flag
  // `report.counts.not_checkable` already uses elsewhere on this page), never
  // a fourth colour. A plain arithmetic/ceiling confidence takes the OTHER
  // branch of the same ternary, so this is a real branch, not decoration.
  pin.match(
    page,
    /fixIsDemandEstimateFraming\(disposition\)\s*\n\s*\? "border-2 border-slate-400 bg-slate-100 text-slate-900 underline decoration-dotted decoration-slate-400 underline-offset-4"\s*\n\s*: "border-slate-300 bg-white\/\[0\.06\]\s*text-slate-600"/,
    "expected the confidence chip to visibly branch on the demand-estimate framing, mono + weight only",
  );
  pin.doesNotMatch(
    withoutComments(page),
    /fixIsDemandEstimateFraming[\s\S]{0,400}(?:bg-amber|bg-emerald-50|bg-rose-50)/,
    "expected the demand-estimate flag to stay mono -- no fourth colour and no borrowed rose/emerald tint either",
  );

  // The confidence chip is not fooled by a literal confidence of "NONE": V6's
  // ask-first shape (no volume supplied yet) reports exactly that, and this
  // is the function documented to catch it anyway (D4's own doctrine, not
  // only a literal string match against the confidence field).
  pin.match(
    page,
    /disposition\.confidence === "DEMAND_ESTIMATE_FROM_UNVERIFIED_ENTRANCE" \|\|\s*\n\s*disposition\.assumptions\.includes\("ENTRANCE_NOT_VERIFIED_MEASURED"\) \|\|\s*\n\s*\(disposition\.verb === "set_vehicle_input_volume" && disposition\.reason_code === "EVIDENCE_INSUFFICIENT"\)/,
    "expected the demand-estimate test to also catch V6's ask-first shape, whose literal confidence is NONE on a GET",
  );
});

contractTest("B5-2 stage 4: an approval is a card, never a modal", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");
  // THE TWO PINS THIS TEST WAS BUILT AROUND ARE REMOVED BY THE PRODUCT OWNER'S
  // OWN INSTRUCTION (unit #41-c, user 2026-08-18: "1번 승인 카드 문장도 없애고").
  // They required the auto-publish disclosure -- "승인하면 실제 Vissim 편집이
  // 실행되고 네트워크가 다시 게이트를 거칩니다 — 통과하면 그 결과가 자동으로 이
  // 프로젝트의 활성 데이터셋으로 등록됩니다." and its English twin -- to be on
  // every approval card. Unit #41-b had already deleted the attestation card's
  // shorter form of the same sentence under the same authority; this was the
  // longer one, and both halves of the family are now pinned ABSENT instead,
  // in the unit #42/#41-b consent test. Recorded rather than silently dropped:
  // a pin removed without a named authority is indistinguishable from a pin
  // lost in a rebase.
  //
  // What did NOT come from that instruction, and so survives here: an approval
  // on this page is a CARD the reader can read, scroll and leave, never a
  // dialog that blocks until it is dismissed. Nothing in the source has ever
  // called `window.confirm`, and this is the only pin that says so.
  pin.doesNotMatch(
    page,
    /window\.confirm/,
    "expected an approval to stay a card the reader can read and leave, never a confirm() dialog that blocks the page",
  );
});

contractTest("B5-2 stage 4: the fixes fetch is cached once per (job, verdict), and apply goes through the typed helper", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");
  const api = read("lib/api.ts");

  // Same discipline as the gate report fetch immediately above it in source,
  // but WITHOUT `lang` in either the call or the dependency array: the
  // backend's own rationale already comes back trilingual, so a language
  // toggle must not trigger a second network round trip the report fetch
  // needs for a different reason (gate.py renders check titles per-language
  // server-side).
  pin.match(
    page,
    /fetchGenesisJobFixes\(gatedJobId\)\s*\n\s*\.then\(\(payload\) => \{\s*\n\s*if \(!cancelled\) \{\s*\n\s*setFixesEntry\(\{ jobId: gatedJobId, checkedAt: gateCheckedAt \?\? "", payload \}\);/,
    "expected the fixes fetch to key its cache entry on (job id, checked_at)",
  );
  pin.match(
    page,
    /\}, \[gatedJobId, gateCheckedAt\]\);/,
    "expected the fixes effect's own dependency array to omit `lang` -- one fetch per (job, verdict), not per (job, verdict, language)",
  );
  pin.match(
    api,
    /export async function fetchGenesisJobFixes\(jobId: string\): Promise<GenesisJobFixes> \{/,
    "expected the fetch binding to take no language parameter at all",
  );
  // The render-side cache key mirrors the fetch key exactly, the same "read
  // back only when the key still matches" discipline `report` uses.
  pin.match(
    page,
    /fixesEntry && selected && fixesEntry\.jobId === selected\.id && fixesEntry\.checkedAt === \(selected\.gate\?\.checked_at \?\? ""\)/,
    "expected the displayed fixes payload to be read back only when its cache key still matches the selected job's current verdict",
  );

  // The apply call goes through the typed helper everywhere -- never a raw
  // fetch either in the binding itself or at its one call site.
  // (Unit #35 broke the body type across lines when `attestation` joined it;
  // the pin follows the formatting, and still asserts the one thing that
  // matters here -- this helper, and no raw fetch, carries the apply.)
  pin.match(
    api,
    /export async function applyGenesisFix\(\s*jobId: string,\s*body: \{[\s\S]{0,1400}?\},\s*\): Promise<GenesisJob> \{\s*const r = await apiMutationFetch\(`\/api\/genesis\/jobs\/\$\{jobId\}\/fixes\/apply`/,
    "expected applyGenesisFix to go through apiMutationFetch, the same workspace-header-carrying helper every other genesis mutation uses",
  );
  const findingRow = sliceBetween(page, "function FixFindingRow({", "\n/**\n * V1–V9 AS A GRID");
  pin.match(findingRow, /\S/, "expected to find the FixFindingRow body (markers moved?)");
  pin.match(
    findingRow,
    /const job = await applyGenesisFix\(jobId, \{/,
    "expected the apply click to call the typed helper",
  );
  pin.doesNotMatch(
    findingRow,
    /\n\s*const r = await fetch\(/,
    "expected the apply flow never to bypass the typed helper with a raw fetch",
  );
});

contractTest("B5-2 stage 4: a fix job's drawer header states its own lineage", (pin) => {
  // unit #23: the lineage line is now in the detail route's own top bar.
  const page = read("app/genesis/jobs/[id]/page.tsx");
  const api = read("lib/api.ts");

  // key-ABSENT on an ordinary generation (never null) -- the same discipline
  // `requested_region` already established on this type, pinned the same way.
  pin.match(
    api,
    /export interface GenesisFixLineage \{\s*\n\s*job_id: string;\s*\n\s*finding_code: string;\s*\n\s*subject\?: string;\s*\n\s*verb: string;\s*\n\s*params: Record<string, unknown>;\s*\n\s*approved_by: string \| null;\s*\n\s*approval_rationale: string \| null;\s*\n\s*approved_at: string;\s*\n\s*parent_sha256: string;\s*\n\s*\}/,
    "expected the GenesisFixLineage type to mirror derived_from's exact backend shape -- subject optional, since records written before unit #30 lack the key",
  );
  // CO-EDITED by unit #33, which added the SECOND lineage kind (a live-check
  // job's). `derived_from` stays key-ABSENT for an ordinary generation -- the
  // property this pin has always guarded -- and its type became the union of
  // the two, discriminated by a `kind` the fix lineage deliberately does not
  // carry.
  pin.match(
    api,
    /derived_from\?: GenesisJobLineage;/,
    "expected GenesisJob.derived_from to be optional (key-absent for an ordinary generation), not nullable",
  );
  pin.match(
    api,
    /export type GenesisJobLineage = GenesisFixLineage \| GenesisLiveCheckLineage;/,
    "expected the two lineage kinds to be a discriminated union, so a live-check job cannot be read as a fix job",
  );

  // The drawer header line itself: 파생 — {parent id} · {finding_code} · {approved_by},
  // now behind the union's own narrowing.
  pin.match(
    page,
    /\{selected\.derived_from &&\s*\n\s*\("kind" in selected\.derived_from \?/,
    "expected the lineage line to be gated on derived_from's presence and narrowed by kind, exactly like an ordinary job renders none",
  );
  pin.match(
    page,
    /t\("파생", "Derived from", "派生"\)\} — \{selected\.derived_from\.job_id\} ·\{" "\}\s*\n\s*\{selected\.derived_from\.finding_code\} · \{selected\.derived_from\.approved_by \?\? "—"\}/,
    "expected the lineage line to read parent id, finding_code and approved_by, in that order",
  );

  // The history table grew NO new column for this -- the detail page's own
  // top bar carries it. unit #23: the table itself stayed on the hub.
  const hub = read("app/genesis/page.tsx");
  const historyHead = sliceBetween(hub, "<thead>", "</thead>");
  pin.match(historyHead, /\S/, "expected to find the history table's own header row (markers moved?)");
  pin.doesNotMatch(
    historyHead,
    /파생|Derived from/,
    "expected the history table to grow no lineage column -- the detail page's top bar carries it",
  );
});

// ---------------------------------------------------------------------------
// Unit #33 -- V1's live check becomes a UI-triggered queued COM job
// ---------------------------------------------------------------------------
//
// V1 is the one gate check that cannot be answered offline, and until this unit
// the only way to answer it was for somebody to run
// `backend/tools/genesis_open_live_check.py` at a console. The pins below guard
// the three things that make the button honest: it goes through the workspace-
// header mutation helper (a bare fetch would live-check ANOTHER project's job),
// it never renders as a disabled control, and it never prints a verdict of its
// own -- the words a reader gets are gate.py's, through the V1 row.

contractTest("unit #33: the V1 live check is queued through the typed mutation helper", (pin) => {
  const api = read("lib/api.ts");
  const page = read("app/genesis/jobs/[id]/page.tsx");

  pin.match(
    api,
    /export async function startGenesisLiveCheck[\s\S]*?apiMutationFetch\(`\/api\/genesis\/jobs\/\$\{jobId\}\/live-check\/run`/,
    "expected the queued live check to go through apiMutationFetch -- X-Workspace-Id is attached there",
  );
  // The SIBLING route (attach an operator's state.json) still exists and is a
  // different thing: pinned together so neither can quietly become the other.
  pin.match(
    api,
    /apiMutationFetch\(`\/api\/genesis\/jobs\/\$\{jobId\}\/live-check`/,
    "expected the attestation route to survive beside the queued one -- they are two different claims",
  );
  pin.match(
    page,
    /startGenesisLiveCheck\(jobId\)/,
    "expected the page to start the live check through the typed helper, never a hand-built request",
  );
  // A queued job is polled with the SAME helper and the SAME terminal rule
  // every other job on this page uses.
  pin.match(
    page,
    /fetchGenesisJob\(checkJob\.id\)/,
    "expected the live-check job to be polled through the shared job helper",
  );
  pin.match(
    page,
    /if \(!checkJob \|\| isTerminal\(checkJob\.status\)\) return;/,
    "expected the live-check poll to stop at a terminal status, using the shared isTerminal rule",
  );
});

contractTest("unit #33: the V1 trigger is one button on V1, and ineligible is a sentence", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");
  const control = sliceBetween(page, "function LiveCheckControl", "function GateCheckGrid");
  pin.match(control, /\S/, "expected to find LiveCheckControl (markers moved?)");

  // The button's own words, ko primary.
  pin.match(
    control,
    /t\("실 Vissim으로 확인", "Check in a real Vissim", "実Vissimで確認"\)/,
    "expected the V1 trigger to be spelled 실 Vissim으로 확인",
  );
  // ONLY V1 gets it: the other eight checks are answered offline and a control
  // beside them would imply otherwise.
  pin.match(
    page,
    /selected\.id === "V1" && \(\s*\n\s*<LiveCheckControl/,
    "expected the live-check trigger to render on the V1 cell only",
  );

  // REFUSAL IS INFORMATION: an ineligible job renders the backend's own named
  // reason and returns BEFORE any button exists -- never a greyed control.
  pin.match(
    control,
    /if \(refusalCode\) \{[\s\S]*?refusalHeadline\(refusalCode, t\)[\s\S]*?\n  \}/,
    "expected an ineligible job to render the named refusal and no control at all",
  );
  const ineligible = sliceBetween(control, "if (refusalCode) {", "const running =");
  pin.doesNotMatch(
    ineligible,
    /<button/,
    "expected NO button on the ineligible path -- a refusal is a sentence, not a disabled control",
  );

  // The control reports the JOB's progress and the load's outcome; it never
  // words a verdict. That is the V1 row's job, re-fetched from the stored gate.
  pin.doesNotMatch(
    control,
    /통과|확인 불가|PASS|NOT CHECKABLE/,
    "the live-check control must never print a gate verdict -- gate.py owns those words",
  );
});

contractTest("unit #33: a live-check job's own state is named, and it is not a pass", (pin) => {
  const genesisLib = read("lib/genesisArtifacts.ts");
  const api = read("lib/api.ts");
  const ui = read("components/genesis/ui.tsx");

  pin.match(
    api,
    /\| "checked";/,
    "expected the live-check job's own terminal state in the client type",
  );
  pin.match(
    genesisLib,
    /t\("실 Vissim 확인 완료", "LIVE CHECK DONE", "実Vissim確認 完了"\)/,
    "expected `checked` to be named as evidence produced, never as a verdict word",
  );
  // Mono, like `provisional` -- it is not a pass, and the three-colour rule
  // says the green belongs to the parent's chip.
  pin.match(
    genesisLib,
    /case "provisional":\s*\n(?:\s*\/\/[^\n]*\n)*\s*case "checked":\s*\n\s*return "border-slate-400 bg-slate-100 text-slate-900";/,
    "expected `checked` to take the mono treatment, never an accent colour",
  );
  pin.match(
    genesisLib,
    /export const TERMINAL: GenesisJobStatus\[\] = \[[\s\S]*?"checked",/,
    "expected `checked` to be terminal, so a page polling one stops at 1 Hz",
  );

  // The two eligibility refusals join the SHARED headline table, like every
  // other backend refusal code this app renders.
  for (const code of [
    "genesis_live_check_parent_not_gated",
    "genesis_live_check_artifact_missing",
  ]) {
    pin.match(
      ui,
      new RegExp(`case "${code}":`),
      `expected ${code} to have a ko/en/ja headline in the shared refusal table`,
    );
  }
});

contractTest("unit #33: an app-observed V1 is not dressed as an operator's claim", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");
  const api = read("lib/api.ts");

  // Two bases, two sentences. The attestation wording must survive unchanged
  // beside the new one -- the weaker claim keeps its label.
  pin.match(
    page,
    /live_evidence\.basis === "app_observed"\s*\n\s*\? t\(\s*\n\s*"라이브 검사 \(이 앱이 직접 실행\)"/,
    "expected an app-run live check to be worded as this app's own run",
  );
  pin.match(
    page,
    /"라이브 검사 증언 \(운영자 진술\)"/,
    "expected the operator-attestation wording to survive beside the app-observed one",
  );
  pin.match(
    page,
    /\{selected\.gate\.rests_on_attestation && \(/,
    "expected the rests-on-attestation disclosure to stay gated on the backend's own flag",
  );
  // The re-gate's lineage is on screen: which job replaced the verdict, and
  // where the previous one was kept.
  pin.match(
    page,
    /selected\.gate\.regate\?\.job_id/,
    "expected the verdict to name the live-check job that re-decided it",
  );
  pin.match(
    api,
    // `[^}]*`, not `[\s\S]*?`: a lazy gap walks straight through the closing
    // brace into the NEXT interface that happens to declare the same field
    // (GenesisLiveCheckResult does), and a pin that can match across a type
    // boundary is a pin that passes for the wrong type. Caught by mutation.
    /export interface GenesisRegateProvenance \{[^}]*previous_gate_archived: string \| null;\s*\n\}/,
    "expected the archived previous verdict to be carried into the re-gate provenance type",
  );
});

contractTest("Unit #30: a fix addresses its finding by (code, subject) -- duplicates are individually applicable", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");
  const api = read("lib/api.ts");

  // The POST body carries the finding's own subject, REQUIRED in the binding
  // (optional only on the wire, for legacy callers) -- this client is
  // structurally unable to send the ambiguous, code-only approval the backend
  // now refuses by name (genesis_fix_finding_ambiguous_without_subject).
  // (The body type grew a multi-line shape in unit #35, when the optional
  // `attestation` field joined it -- the pin follows the formatting; what it
  // asserts is unchanged: `subject` is REQUIRED, and it sits beside the code.)
  pin.match(
    api,
    /body: \{\s*finding_code: string;\s*subject: string;\s*verb: string;/,
    "expected applyGenesisFix's body type to require the finding's subject beside its code",
  );
  pin.match(
    page,
    /finding_code: finding\.code,[\s\S]{0,400}?subject: finding\.subject,/,
    "expected the apply click to send the finding's own subject beside its code",
  );

  // The listing type carries the subject the client sends back -- one field,
  // read off GET .../fixes, never derived client-side.
  pin.match(
    api,
    /export interface GenesisFixFinding \{[\s\S]{0,600}?\n\s*subject: string;/,
    "expected GenesisFixFinding to carry the subject the apply call sends back",
  );

  // The old first-for-code guard is GONE: with (code, subject) as the
  // address, the SECOND of two same-coded findings is its own apply target --
  // the backend no longer resolves duplicates to the first match, so the UI
  // must not either. (Its pinned rationale used to cite exactly that
  // first-match behavior; B5-2's recorded limit, closed by this unit.)
  pin.doesNotMatch(
    page,
    /firstOccurrenceOfCode|isFirstForCode/,
    "expected the first-occurrence guard machinery to be gone -- subject addressing makes every duplicate-coded finding individually applicable",
  );
});

contractTest("B5-2 stage 4: the genesis_fix_* refusal codes join the shared headline table", (pin) => {
  const ui = read("components/genesis/ui.tsx");

  for (const code of [
    "genesis_fix_unknown_finding",
    "genesis_fix_verb_mismatch",
    "genesis_fix_parent_not_refused",
    "genesis_fix_finding_not_in_parent_verdict",
    "genesis_fix_params_target_mismatch",
    "genesis_fix_finding_ambiguous_without_subject",
    "genesis_fix_measurement_gate_not_satisfied",
    "genesis_fix_verb_no_longer_registered",
    "genesis_fix_no_gate_verdict",
  ]) {
    pin.match(ui, new RegExp(`case "${code}":`), `expected ${code} to have a headline in the shared refusal table`);
  }

  // D4 speaking verbatim: `genesis_fix_measurement_gate_not_satisfied` must
  // read as "re-checked against reality", never a generic "request refused"
  // -- the whole point of carrying the underlying reason through unmodified.
  pin.match(
    ui,
    /case "genesis_fix_measurement_gate_not_satisfied":\s*\n\s*return t\(\s*\n\s*"실제 근거를 다시 확인한 결과, 이 승인은 더 이상 유효하지 않습니다",/,
    "expected the measurement-gate refusal headline to say evidence was RE-CHECKED, not merely 'refused'",
  );

  // The error box below the apply button quotes `reasons` verbatim (never a
  // re-worded backend refusal) -- the same discipline `selected.refusal`
  // already uses elsewhere on this page. unit #23: this page is now the
  // detail route.
  // CO-EDITED by unit #33: the helper that builds this box gained a SECOND
  // caller (the V1 live check) and with it a caller-supplied fallback, so the
  // call is now one line. What the pin guards is unchanged -- the code goes
  // through the shared table, and the reasons come through untouched.
  const page = read("app/genesis/jobs/[id]/page.tsx");
  pin.match(
    page,
    /headline: refusalHeadline\(caught\.code, t, fallback\)/,
    "expected the apply-error box to translate the CODE through refusalHeadline",
  );
  pin.match(
    page,
    /genesisRefusalText\(\s*\n\s*applyError,\s*\n\s*t,\s*\n\s*t\("수정을 적용하지 못했습니다"/,
    "expected the fix flow to keep its OWN fallback sentence -- a live-check refusal must never read as a failed fix",
  );
  pin.match(
    page,
    /reasons: caught\.reasons \}/,
    "expected the apply-error box to carry the backend's reasons array through unmodified",
  );
});

// ---------------------------------------------------------------------------
// Unit #21 (2026-08-14, option A) -- vehicle-trajectory replay moves off the
// network view and into the dashboard as part of RUN RESULTS: replay is
// something you do to a simulation run, not to a network. `app/dashboard/
// page.tsx` and `app/network/page.tsx` both use CRLF line endings (same
// landmine the U-19 round 2 block above already documents for NetworkViewer.tsx
// / TrajectoryLayer.tsx) -- every pattern below either stays single-line (CRLF
// is irrelevant to a substring/regex match with no embedded `\n`) or reads
// through a CRLF-normalized copy for `sliceBetween`, which does a literal
// `indexOf` and would silently mismatch a lone `\n` marker against a real
// `\r\n` in the file.
// ---------------------------------------------------------------------------

contractTest("unit #21: dashboard replay is anchored to the selected run, not a free picker", (pin) => {
  const dashboardPage = read("app/dashboard/page.tsx").replace(/\r\n/g, "\n");

  // The anchor is exactly activeScenarios[0] -- the SAME "primary" scenario
  // NetworkDiagramView already colors the diagram against -- not an
  // independent piece of state that could silently disagree with it.
  pin.match(
    dashboardPage,
    /const primaryScenario = activeScenarios\[0\] \?\? null;/,
    "expected the replay anchor to be the scenario comparison's own primary selection",
  );
  pin.match(
    dashboardPage,
    /const jobId = primaryScenario \? runtimeScenarioJobId\(primaryScenario\.id\) : null;/,
    "expected the anchored run to be DECODED from the primary scenario's own id, not picked separately",
  );
  pin.match(
    dashboardPage,
    /playbackEngine\.load\(replayJob \? replayJob\.id : undefined\);/,
    "expected the load call to play the anchored job's own trajectory (or the demo fallback), never an independently-chosen source",
  );
  // The old free-floating <select> and its backing state must be gone --
  // there is no longer a "재생 소스" picker independent of the run selection.
  pin.doesNotMatch(
    dashboardPage,
    /trajectorySourceJobId/,
    "expected the standalone playback-source-picker state to be gone -- replay reads the scenario selection instead",
  );

  // Switching the run mid-playback (a different scenario becomes primary, or
  // the dataset changes) must drop any loaded frames back to idle rather than
  // leaving the OLD run's frames animating under the NEW run's label.
  pin.match(
    dashboardPage,
    /const replayAnchorKey = replayJob \? `job:\$\{replayJob\.id\}` : replayableJobs\.length === 0 \? "demo" : "none";/,
    "expected a key that changes identity whenever the anchored run does",
  );
  pin.match(
    dashboardPage,
    /useEffect\(\(\) => \{\s*playbackEngine\.reset\(\);\s*\}, \[playbackEngine, replayAnchorKey\]\);/,
    "expected loaded playback to reset whenever the anchor key changes, preventing a stale run's frames from surviving a switch",
  );
});

contractTest("unit #21: demo replay is offered only when the project has zero replayable runs", (pin) => {
  const dashboardPage = read("app/dashboard/page.tsx").replace(/\r\n/g, "\n");

  // Isolate VehicleReplaySection's three-way source-line branch so each slice
  // below can only ever see the state it claims to.
  const anchoredBranch = sliceBetween(dashboardPage, "{replayJob ? (", ") : hasReplayableRuns ? (");
  const refusalBranch = sliceBetween(dashboardPage, ") : hasReplayableRuns ? (", ') : (\n        <>');
  const demoBranch = sliceBetween(dashboardPage, ') : (\n        <>', ')}\n\n      {canLoad');
  pin.match(anchoredBranch, /\S/, "expected to find the anchored-run branch (markers moved?)");
  pin.match(refusalBranch, /\S/, "expected to find the no-trajectory refusal branch (markers moved?)");
  pin.match(demoBranch, /\S/, "expected to find the demo-fallback branch (markers moved?)");

  // "데모" (demo) must never be offered while the project HAS replayable runs
  // -- whether the anchored run has a trajectory or not -- only once there are
  // NONE anywhere in the project.
  pin.doesNotMatch(anchoredBranch, /데모/, "expected no demo label while a real run is anchored");
  pin.doesNotMatch(refusalBranch, /데모/, "expected no demo label in the no-trajectory refusal -- demo must not silently substitute for the selected run");
  pin.match(demoBranch, /데모/, "expected demo to be named, in the ONE branch where the whole project has no replayable run");

  // B5-2 stage 4's rule, carried into this unit: a refusal is information, not
  // a disabled control standing in for one -- no button anywhere in that branch.
  pin.doesNotMatch(refusalBranch, /<button/, "expected NO button in the no-trajectory refusal branch -- a refusal is information, not a disabled control");
});

contractTest("unit #21: /network carries no trajectory-playback code anymore", (pin) => {
  const networkPage = read("app/network/page.tsx").replace(/\r\n/g, "\n");

  pin.doesNotMatch(networkPage, /TrajectoryLayer/, "expected /network to import nothing from TrajectoryLayer.tsx");
  pin.doesNotMatch(networkPage, /createProjectionStore/, "expected /network's projection store to be gone");
  pin.doesNotMatch(networkPage, /useTrajectoryPlayback/, "expected /network's playback hook to be gone");
  pin.doesNotMatch(networkPage, /PLAYBACK_SPEEDS/, "expected /network's speed control to be gone");
  pin.doesNotMatch(networkPage, /replayableJobs/, "expected /network's replayable-job fetch to be gone");
  pin.doesNotMatch(networkPage, /차량 궤적 재생/, "expected the '차량 궤적 재생' panel heading to be gone from /network");
  pin.doesNotMatch(networkPage, /재생 준비/, "expected the '재생 준비' control to be gone from /network");
  pin.doesNotMatch(networkPage, /fetchSimulationJobs/, "expected /network to no longer fetch simulation jobs at all");
});

contractTest("unit #21: the projection/zoom-transform sync is preserved in its new home", (pin) => {
  const dashboardPage = read("app/dashboard/page.tsx").replace(/\r\n/g, "\n");

  // The load-bearing sync call itself: NetworkViewer's live zoom transform ->
  // the plain external store the canvas overlay reads every frame from.
  pin.match(
    dashboardPage,
    /onProjectionChange=\{projection\.set\}/,
    "expected the dashboard's diagram view to feed the SAME projection store the replay canvas reads",
  );
  // The overlay itself is mounted in the diagram (도식) branch only -- there is
  // no ViewProjection for the Leaflet map, so replay draws nowhere else.
  pin.match(
    dashboardPage,
    /\{playback\.frames && \(\s*<TrajectoryLayer engine=\{playbackEngine\} frames=\{playback\.frames\} projection=\{projection\} network=\{network\} \/>\s*\)\}/,
    "expected the vehicle canvas overlay to mount in the diagram view, reading the same engine/frames/projection/network the panel below drives",
  );
});

// ---------------------------------------------------------------------------
// Unit #22 (2026-08-14 directive): / lands on a PROJECT OVERVIEW instead of
// the dashboard, and the overview is the one screen that decides whether
// there is even a project to show.
// ---------------------------------------------------------------------------

contractTest("unit #22: the overview composes only existing lib/api.ts helpers, never a raw fetch", (pin) => {
  const overview = read("app/overview/page.tsx").replace(/\r\n/g, "\n");

  // The five reads this page is built from -- every one an EXISTING helper
  // already documented (and already used) elsewhere in the app, never a new
  // endpoint invented for this unit.
  pin.match(overview, /fetchWorkspaces\(\)/, "expected the overview to read the current project from fetchWorkspaces");
  pin.match(overview, /fetchActiveDataset\(\)/, "expected ① 네트워크 to read the active dataset from fetchActiveDataset");
  pin.match(overview, /fetchGenesisJobs\(\)/, "expected ① 네트워크's verdict chip to read fetchGenesisJobs");
  pin.match(overview, /fetchStagedSideInputs\(\)/, "expected ② 데이터 to read the same staged-input listing the genesis job form uses");
  pin.match(overview, /fetchSimulationJobs\(\)/, "expected ③ 시뮬·보정 to read fetchSimulationJobs");
  pin.match(overview, /createWorkspace\(trimmed\)/, "expected the no-project branch to create a project through the existing createWorkspace helper");

  // No hand-rolled request of its own -- every read on this page goes through
  // one of the five helpers above.
  pin.doesNotMatch(withoutComments(overview), /apiFetch\(|apiMutationFetch\(|fetch\(/, "expected NO raw fetch call on the overview -- every read must be an existing named helper");

  // Frontend-only, per this unit's own hard constraint: no backend route was
  // added, so no LIVE code on this page should build a raw API path -- only
  // this file's own doc comments may NAME one (e.g. explaining which read
  // needs a signed-in caller), so this is scoped past comments the same way
  // the raw-fetch pin above is.
  pin.doesNotMatch(withoutComments(overview), /"\/api\//, "expected no code on the overview to build a raw API path string -- it must go through lib/api.ts helpers exclusively");
});

contractTest("unit #22: the overview renders four stage cards, each deep-linking to its own stage", (pin) => {
  const overview = read("app/overview/page.tsx").replace(/\r\n/g, "\n");

  // The four cards, in pipeline order, each carrying the SAME ordinal the
  // sidebar's own NavGroups use (components/AppShell.tsx) -- one numbering,
  // never a second one invented for this screen.
  pin.match(overview, /title=\{`① \$\{t\("네트워크", "Network", "ネットワーク"\)\}`\}/, "expected card ① 네트워크");
  pin.match(overview, /title=\{`② \$\{t\("데이터", "Data", "データ"\)\}`\}/, "expected card ② 데이터");
  pin.match(overview, /title=\{`③ \$\{t\("시뮬·보정", "Simulate & Calibrate", "シミュ・補正"\)\}`\}/, "expected card ③ 시뮬·보정");
  pin.match(overview, /title=\{`④ \$\{t\("보고서", "Report", "レポート"\)\}`\}/, "expected card ④ 보고서");

  // Every card's own deep link(s) -- the whole point of a stage CARD is that
  // it goes somewhere.
  pin.match(overview, /StageLink\s+href="\/genesis"/, "expected ① 네트워크 to deep-link to /genesis");
  pin.match(overview, /StageLink href="\/genesis\/demand"/, "expected ② 데이터 to deep-link to /genesis/demand");
  pin.match(overview, /StageLink href="\/genesis\/phases"/, "expected ② 데이터 to deep-link to /genesis/phases");
  pin.match(overview, /StageLink href="\/dashboard"/, "expected ③ 시뮬·보정 to deep-link to /dashboard");
  pin.match(overview, /StageLink href="\/report"/, "expected ④ 보고서 to deep-link to /report");
  pin.match(overview, /StageLink href="\/downloads"/, "expected ④ 보고서 to deep-link to /downloads");

  // ① 네트워크's named empty state, exact wording from the unit's own spec.
  pin.match(overview, /아직 생성된 네트워크 없음/, "expected ①'s named empty state when fetchGenesisJobs comes back empty");
  pin.match(overview, /생성하기 →/, "expected ①'s empty-state link to read 생성하기");

  // ② 데이터's fact shape: 수요 N · 신호 N, built from the SAME staged-input
  // listing (filtered by kind), never a second count invented independently.
  pin.match(
    overview,
    /entry\.kind === "demand"/,
    "expected 수요 N to come from filtering the staged side-input listing by kind",
  );
  pin.match(
    overview,
    /entry\.kind === "signals"/,
    "expected 신호 N to come from filtering the staged side-input listing by kind",
  );
  pin.match(
    overview,
    /\$\{t\("수요", "Demand", "需要"\)\} \$\{demandCount\} · \$\{t\("신호", "Signals", "信号"\)\} \$\{signalsCount\}/,
    "expected the exact 수요 N · 신호 N fact format",
  );

  // ③ 시뮬·보정: a completed-run count derived by filtering for status "done",
  // never counting queued/running/error jobs as completed.
  pin.match(overview, /job\.status === "done"/, "expected ③'s count to filter simulation jobs to status done");
});

contractTest("unit #22: a 401 or a load failure never renders as a false zero", (pin) => {
  const overview = read("app/overview/page.tsx").replace(/\r\n/g, "\n");

  // GET /api/genesis/side-inputs is the one read on this page that requires a
  // signed-in caller -- an anonymous 401 there must read as "sign in to see
  // this", never silently fall through to the "수요 0 · 신호 0" empty state.
  pin.match(
    overview,
    /sideInputsResult\.reason instanceof GenesisError && sideInputsResult\.reason\.status === 401/,
    "expected the staged-input 401 to be typed and handled explicitly, not swallowed into the empty branch",
  );
  // Sliced by the two cards' own opening tags (not by the divider comments
  // around them, which repeat identically before every card and would slice
  // an empty span) -- from ②'s <StageCard> up to ③'s, which is everything ②
  // actually renders.
  const cardTwoOpen = '<StageCard title={`② ${t("데이터", "Data", "データ")}`}>';
  const cardThreeOpen = '<StageCard title={`③ ${t("시뮬·보정", "Simulate & Calibrate", "シミュ・補正")}`}>';
  const dataCard = sliceBetween(overview, cardTwoOpen, cardThreeOpen);
  pin.match(dataCard, /\S/, "expected to find card ②'s own JSX span (markers moved?)");
  pin.match(dataCard, /sideInputsAuthRequired \?/, "expected ② 데이터 to branch on the 401 BEFORE checking the zero-count empty state");
  pin.match(dataCard, /로그인하면 등록된 데이터를 볼 수 있습니다/, "expected a named sign-in note, distinct from the empty and error notes");

  // Every OTHER fetch failure on this page (network error, backend down) also
  // gets its own named error branch -- never folded into a catch that sets
  // state to [] / null indistinguishably from a genuine zero.
  pin.match(overview, /genesisJobsError \?/, "expected ① to distinguish a load failure from a genuine zero generations");
  pin.match(overview, /sideInputsError \?/, "expected ② to distinguish a load failure from a genuine zero staged inputs");
  pin.match(overview, /simulationJobsError \?/, "expected ③ to distinguish a load failure from a genuine zero completed runs");

  // The no-project branch must never fire on a plain load failure: only a
  // confirmed empty listing or a typed WorkspaceAccessError may claim "no
  // project exists". A page that folded a network error into this branch
  // would tell a user their project vanished when the backend merely hiccuped.
  pin.match(
    overview,
    /workspacesFailure === "access" \|\| \(workspaces !== null && workspaces\.workspaces\.length === 0\)/,
    "expected the no-project branch to require a confirmed empty listing or a typed access error, never a bare fetch failure",
  );
  pin.match(
    overview,
    /workspacesResult\.reason instanceof WorkspaceAccessError \? "access" : "error"/,
    "expected fetchWorkspaces failures to be typed apart -- an unrelated network fault must not be read as 'no project'",
  );
});

contractTest("unit #22: the no-project branch renders ONLY the create-project card", (pin) => {
  const overview = read("app/overview/page.tsx").replace(/\r\n/g, "\n");

  const noProjectBranch = sliceBetween(overview, "if (noProject) {", "// fetchWorkspaces() itself failed");
  pin.match(noProjectBranch, /return <CreateProjectOnly \/>;/, "expected the no-project branch to return ONLY the create-project component");
  pin.doesNotMatch(noProjectBranch, /StageCard|<header/, "expected the no-project branch to render no stage card and no project header");

  const createProjectFn = sliceBetween(overview, "function CreateProjectOnly()", "// The normal overview");
  pin.match(createProjectFn, /\S/, "expected to find CreateProjectOnly's own body (markers moved?)");
  pin.match(createProjectFn, /await createWorkspace\(trimmed\)/, "expected the create-project card to call the existing createWorkspace helper, not a new request");
  pin.doesNotMatch(createProjectFn, /apiMutationFetch\(|apiFetch\(/, "expected no duplicated request logic inside the create-project card");
  pin.match(createProjectFn, /setWorkspaceId\(created\.id\)/, "expected a created project to become the active workspace");
  pin.match(createProjectFn, /window\.location\.reload\(\)/, "expected creating a project to reload, the same full-reload switch every other project change on this app uses");

  // Anonymous default-workspace sessions are NOT this state -- only a
  // confirmed-empty listing or a typed access error reaches it (also pinned
  // above); this pin guards the RENDER side, that no-project truly means
  // "nothing else on the page".
  pin.match(overview, /Anonymous default-workspace sessions/, "expected the doc comment to name why the default workspace never takes this branch");
});

contractTest("unit #22: 개요 sits above the numbered groups, outside the pipeline stepper", (pin) => {
  const shell = read("components/AppShell.tsx").replace(/\r\n/g, "\n");

  // Placed inside <nav>, before the four numbered groups render -- "directly
  // under the 프로젝트 selector, above group ①".
  const navOpen = sliceBetween(shell, '<nav className="app-side-nav"', "{groups.map((group, index) => (");
  pin.match(navOpen, /href="\/overview"/, "expected the 개요 link to sit before the numbered groups inside <nav>");
  pin.match(navOpen, /<NavIcon name="overview" \/>/, "expected the 개요 link to carry its own nav icon");
  pin.match(navOpen, /aria-current=\{pathname === "\/overview" \? "page" : undefined\}/, "expected 개요's aria-current to follow the same pattern every other nav link uses");

  // NOT a pipeline stage: absent from ROUTE_STAGE, and the stepper's own
  // reasoning says why -- mirroring the /admin comment rather than a new,
  // unrelated justification.
  pin.doesNotMatch(shell, /"\/overview":\s*"(network|data|sim|report)"/, "expected /overview to be absent from ROUTE_STAGE entirely");
  pin.match(shell, /`\/overview` \(unit #22\) is absent for the same reason/, "expected a comment explaining why /overview carries no pipeline stage, mirroring the /admin reasoning");
});

// ---------------------------------------------------------------------------
// Unit #24 (2026-08-14 directive): 개요 gets a fixed title and the project it
// used to show becomes a SELECTABLE subtitle control sharing the sidebar
// picker's own logic; the sidebar's full-width 새 프로젝트 button becomes a
// small + icon on the 프로젝트 label's own row; the four stage cards lay out
// 2x2 at desktop instead of 4-across.
// ---------------------------------------------------------------------------

contractTest("#24: the picker's fetch/switch/create logic lives in ONE exported hook, not two", (pin) => {
  const selector = read("components/ProjectSelector.tsx").replace(/\r\n/g, "\n");

  pin.match(selector, /export function useWorkspacePicker\(\)/, "expected the fetch/switch/create/self-heal logic to live in one exported hook");
  pin.match(selector, /export default function ProjectSelector\(\)/, "expected the sidebar component to stay the default export");
  pin.match(selector, /export function WorkspacePickerInline\(/, "expected a second, named export for the overview's inline control");

  // The sidebar component itself holds no fetch or switch decision of its
  // own -- scoped to its own body so a duplicate defined ABOVE the hook
  // (rather than a call to it) could not silently satisfy this pin.
  const projectSelectorFn = sliceBetween(selector, "export default function ProjectSelector()", "export function WorkspacePickerInline(");
  pin.match(projectSelectorFn, /\S/, "expected to find ProjectSelector's own body (markers moved?)");
  pin.match(projectSelectorFn, /\} = useWorkspacePicker\(\);/, "expected the sidebar component to consume the shared hook rather than its own state");
  pin.doesNotMatch(projectSelectorFn, /fetchWorkspaces\(\)|setWorkspaceId\(isDefault/, "expected the sidebar component to hold no fetch or switch logic of its own -- only the shared hook may");

  // The overview's inline control is the same story: it renders a bare
  // label+select, no create-flow of its own, sourced from the same hook.
  const inlineFn = selector.slice(selector.indexOf("export function WorkspacePickerInline("));
  pin.match(inlineFn, /\S/, "expected to find WorkspacePickerInline's own body (marker moved?)");
  pin.match(
    inlineFn,
    /\{ workspaces, selectedWorkspaceId, locked, handleSwitchWorkspace \} = useWorkspacePicker\(\);/,
    "expected the inline control to read its list and switch handler from the same shared hook",
  );
  pin.doesNotMatch(inlineFn, /creating|handleCreateWorkspace/, "expected no create-flow inside the overview's inline control -- creation stays on the sidebar's + button");
});

contractTest("#24: the sidebar's 새 프로젝트 button is a small + icon on the 프로젝트 label's row", (pin) => {
  const selector = read("components/ProjectSelector.tsx").replace(/\r\n/g, "\n");
  const projectSelectorFn = sliceBetween(selector, "export default function ProjectSelector()", "export function WorkspacePickerInline(");

  // Label and button share one row -- label left, button right -- scoped to
  // that row's own wrapping div so a match elsewhere in the component cannot
  // satisfy it.
  const labelRow = sliceBetween(projectSelectorFn, '<div className="flex items-center justify-between', "</div>");
  pin.match(labelRow, /\S/, "expected to find the label/button row (markers moved?)");
  pin.match(labelRow, /htmlFor="vissim-workspace-picker"/, "expected the 프로젝트 label to open this row");
  pin.match(labelRow, /aria-label=\{t\("새 프로젝트", "New Project", "新規プロジェクト"\)\}/, "expected the + button's accessible name to stay 새 프로젝트, now carried by aria-label since the button is icon-only");
  pin.match(labelRow, /onClick=\{\(\) => setCreating\(\(open\) => !open\)\}/, "expected the + button to keep opening the exact same creating-flow toggle");
  pin.match(labelRow, /<svg viewBox="0 0 24 24"/, "expected the + glyph to be drawn as SVG geometry, mono/ghost vocabulary");
  pin.doesNotMatch(labelRow, />\s*\{t\("새 프로젝트", "New Project", "新規プロジェクト"\)\}\s*<\/button>/, "expected the old visible button text to be gone -- the button is icon-only now");

  // The old full-width button chrome is gone; the create-flow it opens is not.
  pin.doesNotMatch(projectSelectorFn, /ui-button-ghost mt-1\.5 w-full/, "expected the old full-width 새 프로젝트 button chrome to be removed");
  pin.match(projectSelectorFn, /\{creating && \(/, "expected the creating-flow to still open exactly as before");
  pin.match(projectSelectorFn, /void handleCreateWorkspace\(\)/, "expected the 만들기 button to still call the shared create handler");
});

contractTest("#24: 개요's title is fixed and the project becomes a selectable subtitle sharing ProjectSelector's hook", (pin) => {
  const overview = read("app/overview/page.tsx").replace(/\r\n/g, "\n");

  pin.match(
    overview,
    /import \{ WorkspacePickerInline \} from "@\/components\/ProjectSelector"/,
    "expected the subtitle control to import the shared picker component, not reimplement one",
  );
  pin.match(overview, /\{t\("개요", "Overview", "概要"\)\}/, "expected the header title to read 개요 rather than the project name");
  pin.match(overview, /<WorkspacePickerInline id="overview-workspace-picker" \/>/, "expected the project to render as the shared selectable control where the subtitle used to sit");

  // No second implementation: the page must not compute its own "current
  // workspace" for display, nor redefine any part of the switch decision --
  // scoped past comments, since #18's own doctrine is legitimately NAMED in
  // this file's prose (CreateProjectOnly's own comments quote
  // `setWorkspaceId(isDefault ? null : workspaceId)` and `handleSwitchWorkspace`
  // to explain an unrelated, pre-existing branch).
  const liveCode = withoutComments(overview);
  pin.doesNotMatch(liveCode, /currentWorkspace/, "expected the page to carry no 'current workspace' computed for display -- the shared picker owns that now");
  pin.doesNotMatch(liveCode, /const handleSwitchWorkspace/, "expected overview to reuse the shared switch handler, never redefine one");
  pin.doesNotMatch(liveCode, /setWorkspaceId\(isDefault/, "expected overview to carry no second copy of the isDefault switch branch");
});

contractTest("#24: the overview's four stage cards render 2x2 at desktop, one column on narrow screens", (pin) => {
  const overview = read("app/overview/page.tsx").replace(/\r\n/g, "\n");

  pin.match(
    overview,
    /<div className="grid grid-cols-1 gap-5 sm:grid-cols-2">/,
    "expected a single mobile column that becomes a 2-column (2x2 for four cards) grid from sm upward",
  );
  pin.doesNotMatch(withoutComments(overview), /xl:grid-cols-4/, "expected the old 4-across desktop layout to be gone");

  // Pipeline order is unchanged -- the four StageCards still appear in the
  // same ①②③④ source order, which is what makes a 2-column grid read as
  // ① ② / ③ ④ left-right, top-bottom. sliceBetween scans forward from each
  // start marker for the next, so a non-empty slice IS the order proof: an
  // out-of-order card would make the corresponding slice come back "".
  const networkToData = sliceBetween(overview, 'title={`① ${t("네트워크"', 'title={`② ${t("데이터"');
  pin.match(networkToData, /StageLink\s+href="\/genesis"/, "expected ① to precede ② in source order (top row of the 2x2 grid)");
  const dataToSim = sliceBetween(overview, 'title={`② ${t("데이터"', 'title={`③ ${t("시뮬·보정"');
  pin.match(dataToSim, /StageLink href="\/genesis\/demand"/, "expected ② to precede ③ in source order");
  const simToReport = sliceBetween(overview, 'title={`③ ${t("시뮬·보정"', 'title={`④ ${t("보고서"');
  pin.match(simToReport, /StageLink href="\/dashboard"/, "expected ③ to precede ④ in source order, completing the 2x2 reading order");
});

// ---------------------------------------------------------------------------
// Unit #41 (2026-08-17) -- the finding card, repaired against a live session
// ---------------------------------------------------------------------------
//
// Five things the user hit using the job-detail screen on a real refused
// generation (job 2712071437d0: 1831 links; V3 raised ten findings, exactly one
// of which carried an applicable proposal). Every pin below holds ONE of them,
// and the doctrine each protects is the same one this product has always run
// on: a refusal must stay complete, and nothing on the screen may claim more
// than the payload measured.
//
//   1. A finding was not clickable -> the map never reacted.
//   2. The testimony card had no map context at all.
//   3. Doctrine paragraphs and raw machine codes covered the card.
//   4. Card inside card inside card.
//   5. The one real proposal was ninth, under a six-item fold.

contractTest("unit #41: a finding row is a map control exactly when the census can place it", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");
  const findings = read("components/genesis/findings.tsx");
  const tileMap = read("components/genesis/GenesisTileMap.tsx");

  // THE ANSWER TO "IS THERE ANYWHERE TO GO" IS THE RESOLVER'S, not a guess.
  // `placeable` is filled inside buildHighlights' existing loop, from the SAME
  // `resolveFindingHighlight` result that decides the colours -- the U-19 pin
  // above still counts the call sites, so this cannot become a third one.
  pin.match(
    findings,
    /placeable\.add\(findingKeyOf\(ref\.finding\)\);/,
    "expected placeable to be filled from the resolver's own answer, in the pass that already ran it",
  );
  pin.match(
    findings,
    /if \(!highlight\.resolved\) \{\s*\n\s*unresolved\.push\(ref\);\s*\n\s*continue;\s*\n\s*\}\s*\n\s*placeable\.add\(/,
    "expected an UNRESOLVED finding to be excluded from placeable -- it is named in the unresolved list instead, with no invented coordinates",
  );

  // ...and it travels OUT of the map, because the census that answers it is
  // fetched there and nowhere else.
  pin.match(
    tileMap,
    /onPlaceableChange\?\.\(highlights\.placeable\);/,
    "expected the map to publish which findings it can place, rather than the rail deriving a second answer",
  );

  // THE ROW ITSELF: a control when there is somewhere to go, plain text when
  // there is not. Both halves pinned -- a version that made every row a button
  // would be the same lie in the other direction.
  pin.match(
    page,
    /placeable\.has\(row\.key\) \? \(\s*\n\s*<button/,
    "expected a placeable finding row to be a real control that reaches the map",
  );
  pin.match(
    page,
    /onClick=\{\(\) => onFocusFinding\(row\.ref\)\}/,
    "expected the row's click to hand the map the finding itself, not a re-derived id",
  );
  pin.match(
    page,
    /\) : \(\s*\n\s*<span className="block px-1\.5 py-1">\{body\}<\/span>\s*\n\s*\)/,
    "expected an unplaceable finding to render as text, never as a control that would do nothing",
  );

  // ONE selection state still, owned by the map: an outside request PUSHES
  // into it and is applied on the sequence number alone, so the census
  // arriving cannot re-stamp a click the reader made after the request.
  const selectionStates = (tileMap.match(/useState<SelectedItem>/g) ?? []).length;
  pin.match(`count=${selectionStates}`, /^count=1$/, "expected the map to keep owning exactly one selection state");
  pin.match(
    tileMap,
    /\}, \[requestSeq\]\);/,
    "expected the external-request effect to depend on the sequence number ALONE",
  );
  // An element request carries an id and nothing else, so it is re-resolved
  // through the map's own handler -- otherwise an externally selected link
  // would render nameless and finding-less.
  pin.match(
    tileMap,
    /if \(item\?\.kind === "element"\) \{\s*\n\s*if \(item\.elementKind === "link"\) selectLink\(item\.id\);\s*\n\s*else selectConnector\(item\.id\);/,
    "expected an externally requested element to go through the map's own resolver, not to be taken at face value",
  );
});

contractTest("unit #41: a proposal sorts first, wears the sky badge, and the fold can never close over it", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");

  // A BADGE MAY NOT PROMISE A BUTTON THAT IS NOT THERE: the same
  // `fixDispositionIsActionable` the approve branch itself is gated on.
  pin.match(
    page,
    /function fixFindingIsProposal\(finding: GenesisFixFinding\): boolean \{\s*\n\s*return fixDispositionIsActionable\(finding\.disposition\);/,
    "expected the proposal badge to be driven by the SAME actionable test the apply button is gated on",
  );

  // DISPLAY ORDER ONLY -- a partition of the payload, never a mutation of it.
  // `.sort()` on the fetched array would reorder the cached payload itself.
  //
  // RE-AIMED, unit #41-b: `orderedFixes` was the separate 수정 제안 section's own
  // partition, and that section is gone -- each disposition renders inside the
  // 소견 that raised it, so the proposal-first ORDER is `CollapsibleMessages`'s
  // (pinned below, unchanged) and there is one ordering rather than two. What
  // this pin holds is the other half of the same claim, at its new address: the
  // fetched catalogue is read through FILTERED COPIES, never reordered in place.
  pin.match(
    page,
    /const fixesFromOrigin = \(via: "finding" \| "warning"\): GenesisFixFinding\[\] =>\s*\n\s*checkFixes\.filter\(\(entry\) => entry\.origin === via\);/,
    "expected the catalogue to be read through a filtered COPY per origin, never sorted or spliced in place",
  );
  // ...and the pairing is POSITIONAL, which is the property that makes the
  // merge safe: (code, subject) cannot tell gate.py's real duplicate pair apart
  // (`v3_silent_departure_point / 대덕대로 [FWD] w52496957s1`, links 427 AND
  // 429), so matching on it would put one finding's disposition under the
  // other's row. Both payloads walk the same stored gate.json in the same order.
  pin.match(
    page,
    /fix: checkFixes\[payloadIndex\] \?\? null,/,
    "expected a row's disposition to be paired by PAYLOAD POSITION -- a (code, subject) lookup cannot tell two same-keyed findings apart, and gate.py emits them",
  );
  // AND THE OTHER SIDE OF THAT SLICE IS RENDERED, NOT DROPPED (refuter,
  // 2026-08-18). The gate payload caps a check at `MAX_FINDINGS_IN_PAYLOAD`
  // (25) while the catalogue answers for every stored finding, so positional
  // pairing leaves a TAIL: on the live job f57cf27e33f5 that is 41 of V7's 66
  // dispositions. Only the tail's RENDERING was pinned, not its derivation --
  // proved by mutation: `checkFixes.slice(findings.length)` narrowed to
  // `.slice(findings.length, findings.length)` empties the tail and all 124
  // tests stayed green, which is exactly the silent disappearance this unit
  // says it prevents. Re-derived here from the same two lengths.
  pin.match(
    page,
    /const unpaired = checkFixes\.slice\(findings\.length\);/,
    "expected EVERY catalogue entry past the gate payload's cap to be kept as the unpaired tail -- a narrowed or emptied slice drops dispositions with no sign on screen",
  );
  pin.match(
    page,
    /\{unpaired\.length > 0 && \(/,
    "expected the tail to render whenever it is non-empty",
  );
  // Sliced from the tail's own JSX to the end of the component -- NOT from the
  // derivation, whose region stops at the first `</>` (the row body's fragment)
  // and so never reached this markup at all: a `.slice(0, 5)` over the tail
  // survived that spelling of the pin.
  const tailBlock = sliceBetween(page, "{unpaired.length > 0 && (", "/** THE ARTIFACT ROW");
  pin.match(tailBlock, /unpaired\.map\(/, "expected to find the tail's own render (markers moved? the doesNotMatch below would pass vacuously)");
  pin.doesNotMatch(
    tailBlock,
    /unpaired\.slice\(|unpaired\.filter\(/,
    "expected the tail to be rendered whole -- a second cap over the overflow list is the same silent drop one level down",
  );
  const gridBody = sliceBetween(page, "function GateCheckGrid(", "/** The five downloadable artifacts");
  pin.match(gridBody, /\S/, "expected to find the GateCheckGrid body (markers moved? the doesNotMatch below would pass vacuously)");
  pin.doesNotMatch(gridBody, /\.sort\(/, "expected nothing in the grid to sort a fetched array in place");

  // THE FOLD. `FINDINGS_COLLAPSED_COUNT` stays the standing cut, but the
  // visible slice is never smaller than the number of proposals -- which is
  // exactly the case that opened this unit (one proposal, ninth of ten, cut
  // at six).
  pin.match(
    page,
    /const visibleCount = Math\.max\(FINDINGS_COLLAPSED_COUNT, withProposal\.length\);/,
    "expected the collapsed slice to grow rather than ever hide a proposal behind the fold",
  );
  pin.match(
    page,
    /const ordered = \[\.\.\.withProposal, \.\.\.rows\.filter\(\(row\) => !proposalKeys\.has\(row\.key\)\)\];/,
    "expected the finding list to put proposal-bearing rows first, as a partition of the gate's own array",
  );
  // AND THE BADGE ANSWERS FOR ITS OWN ROW (refuter, 2026-08-18). Unit #41-b put
  // the disposition card INSIDE the row that wears this badge, so the two are
  // now one line apart and may not disagree. `proposalKeys` is keyed on
  // (code, subject), which cannot tell gate.py's duplicate pair apart
  // (`v3_silent_departure_point / 대덕대로 [FWD] w52496957s1`, links 427 AND
  // 429): with one derivable and one not, BOTH rows wore 제안 while only one
  // card carried an approve button -- simulated, 1 disagreement in 3 rows. The
  // row's own paired entry answers exactly. `proposalKeys` still drives the
  // FOLD, which is safe because it is a superset of the true proposals.
  pin.match(
    page,
    /const isProposal = Boolean\(row\.fix && fixFindingIsProposal\(row\.fix\)\);/,
    "expected the 제안 badge to be decided by the row's OWN paired disposition -- a (code, subject) set cannot tell a duplicate pair apart, and the card it would contradict is now one line below it",
  );
  pin.doesNotMatch(
    sliceBetween(page, "<ul className=\"mt-2 space-y-1\">", "{overflow > 0 && ("),
    /const isProposal = proposalKeys\.has\(row\.key\)/,
    "expected the badge never to go back to the check-wide (code, subject) set while the card beside it is paired per row",
  );
  // The SERVER-side omission line is evidence and is untouched by any of this.
  pin.match(
    page,
    /selected\.findings_omitted > 0/,
    "expected the gate's own omitted-findings count to keep rendering separately from the presentation fold",
  );

  // THE COLOUR comes through a named helper, so the grid's markup still names
  // no tint of its own (the 2026-08-13 rule) -- and sky is the ONLY new one.
  pin.match(
    page,
    /function fixProposalBadgeClasses\(\): string \{\s*\n\s*return "border-sky-300 bg-sky-50 text-slate-900";/,
    "expected the proposal badge's colour to live in one named helper, in the sky selection/suggestion accent",
  );
  pin.doesNotMatch(
    withoutComments(page),
    /bg-(?:amber|violet|indigo|teal|orange)-/,
    "expected no fourth hue to arrive with the proposal badge -- rose/emerald/sky and mono is the whole palette",
  );
});

contractTest("unit #41: the doctrine moved behind a disclosure, and nothing was deleted", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");

  // A NATIVE <details>, not a button -- load-bearing here: the refusal branch
  // of a fix card may carry no control at all, and a disclosure spelled as a
  // <button> would make the refusal itself read as actionable (the "a refusal
  // is never a button" pin above slices exactly that region).
  pin.match(
    page,
    /function CardDetails\(\{ summary, children \}[\s\S]{0,400}?<details/,
    "expected the disclosure to be a native <details>, so a refusal keeps carrying no button",
  );
  pin.doesNotMatch(
    sliceBetween(page, "function CardDetails(", "/** The one summary label"),
    /<button/,
    "expected the disclosure toggle never to be spelled as a button",
  );

  // MOVED, NOT DROPPED. The backend's full rationale, the assumption tokens
  // and every machine identifier are still rendered -- inside the disclosure.
  // The completeness of a refusal is this product's whole claim.
  // COUNTED, not matched once. The refusal branch and the approval branch each
  // open their own disclosure with the same first line, so a single `match`
  // here stays green while ONE of the two silently drops the rationale --
  // proved by mutation (deleting the approval branch's copy failed no pin).
  const rationaleInDetails = (
    page.match(
      /<CardDetails summary=\{moreLabel\(t\)\}>\s*\n\s*<p className="font-mono leading-relaxed text-slate-700">\{rationale\}<\/p>/g,
    ) ?? []
  ).length;
  pin.match(
    `count=${rationaleInDetails}`,
    /^count=2$/,
    "expected BOTH the refusal card and the approval card to keep the backend's own rationale verbatim inside their disclosure",
  );
  pin.match(
    page,
    /\{finding\.code\}\s*\n\s*\{disposition\.reason_code \? ` · \$\{disposition\.reason_code\}` : ""\}/,
    "expected the raw finding code and reason code to keep rendering (a bug report needs them) -- behind the toggle, not on the card face",
  );
  pin.match(
    page,
    /disposition\.assumptions\.map\(\(token\) => \(\s*\n\s*<li key=\{token\}>\{fixAssumptionLabel\(token, t\)\}<\/li>/,
    "expected every assumption token to keep its named sentence",
  );
  // The two standing attestation explanations moved into the disclosure with
  // their exact wording -- a moved sentence must be the SAME sentence.
  pin.match(
    page,
    /원본 데이터는 이 지점에서 이어지는 도로를 선언하지 않습니다\./,
    "expected the 'the source declares no continuation' sentence to survive the move, word for word",
  );
  pin.match(
    page,
    /연결로의 형상과 차로 대응은 기존 두 링크 끝에서 시스템이 구성합니다/,
    "expected the 'the system constructs the connector' sentence to survive the move, word for word",
  );

  // THE TRIPLE CARD. A fix row is a rule between rows now, and a treatment
  // block is a left rule -- not a bordered box inside a bordered box inside a
  // bordered box.
  pin.doesNotMatch(
    page,
    /<div className="rounded-lg border border-slate-200 bg-slate-50 px-3\.5 py-3 text-\[15px\] text-slate-800">/,
    "expected the fix row to stop being its own card inside the opened check's card",
  );
  // RE-AIMED, unit #41-b: the flat rules-not-boxes list became a MERGE -- the
  // disposition renders inside the <li> of the 소견 that raised it, so the rows
  // are the finding list's own and the "card inside a card" this pin was
  // written against is one level shallower still. The claim survives at its new
  // address: a disposition is a sibling of its row's control (never nested
  // inside that <button>, which would be invalid markup and an unaimable
  // click), and it prints no subject of its own.
  pin.match(
    page,
    /\{row\.fix && \(\s*\n\s*<FixFindingRow\s*\n\s*key=\{rowKeyOf\(row\.fix\)\}\s*\n\s*instanceKey=\{rowKeyOf\(row\.fix\)\}\s*\n\s*showSubject=\{false\}/,
    "expected a proposal to render inside its own finding's row, as a sibling of that row's control, printing no second subject",
  );
  // The tail the payload cap left without a row keeps the rules-not-boxes shape
  // AND its own subject -- it has no row above it to carry one.
  pin.match(
    page,
    /<div className="mt-2 flex flex-col divide-y divide-slate-200 border-t border-slate-200">/,
    "expected the unpaired dispositions to stay separated by rules rather than stacked as cards",
  );
  const treatmentBoxes = (page.match(/mt-3 flex flex-col gap-2 border-l-2 border-slate-300 pl-3/g) ?? []).length;
  pin.match(
    `count=${treatmentBoxes}`,
    /^count=3$/,
    "expected all three treatment blocks (regenerate, widen-boundary, attest) to read as a left rule, not a nested box",
  );
});

contractTest("unit #41: the testimony card can be placed on the map, and only from a measured coordinate", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");
  const tileMap = read("components/genesis/GenesisTileMap.tsx");
  const api = read("lib/api.ts");

  // The coordinate is the BACKEND's, on the testimony it already ships.
  pin.match(
    api,
    /coordinates: \{ lat: number; lon: number \} \| null;/,
    "expected the source testimony to carry its own measured coordinate, nullable",
  );
  pin.match(
    page,
    /lat: disposition\.source_testimony!\.coordinates!\.lat,/,
    "expected the map jump to use the testimony's OWN coordinate, never a position this client derived",
  );
  pin.match(
    page,
    /\{disposition\.source_testimony\.coordinates && \(/,
    "expected the control to appear only when a coordinate was actually measured",
  );

  // NO FALLBACK PIN. A finding with no coordinate gets no marker at all --
  // dropping one at a link end this file picked would be a location the
  // product invented.
  pin.match(
    tileMap,
    /if \(selectedPoint\) \{\s*\n\s*L\.circleMarker/,
    "expected the departure marker to be drawn only from a supplied point",
  );
  pin.doesNotMatch(
    withoutComments(tileMap),
    /selectedPoint \?\? \{|selectedPoint \|\| \{/,
    "expected no substituted coordinate when the payload carries none",
  );

  // The candidate list answers "where is this one" -- on hover, on focus and
  // on pick, all three, because a keyboard reader needs the same answer a
  // mouse gets.
  pin.match(page, /onMouseEnter=\{focus\}/, "expected hovering a candidate to show it on the map");
  pin.match(page, /onFocus=\{focus\}/, "expected keyboard focus to show a candidate on the map too");
  pin.match(
    page,
    /setAttestTarget\(event\.target\.value\);\s*\n\s*focus\(\);/,
    "expected picking a candidate to show it on the map as well as select it",
  );
});

contractTest("unit #41: a subject reads road-name first, and no name is ever invented", (pin) => {
  const lib = read("lib/genesisArtifacts.ts");
  const findings = read("components/genesis/findings.tsx");
  const page = read("app/genesis/jobs/[id]/page.tsx");

  // The split is derived from the two spellings the payload actually uses --
  // gate.py's `_label()` and the census's own name -- and NOTHING else.
  pin.match(
    lib,
    /export function describeGenesisSubject\(subject: string\): GenesisSubjectLabel \{/,
    "expected one exported helper owning the road-name/id split",
  );
  pin.match(
    lib,
    /if \(!name\) return \{ lead: raw, trailing: "", id \};/,
    "expected a subject with no name at all to lead with its bare id -- exactly what the old id-first rendering showed, never a substituted name",
  );
  pin.match(
    lib,
    /if \(bracket <= 0\) return \{ lead: name, trailing: "", id \};/,
    "expected a name with no direction token to stay whole rather than be cut at a guess",
  );

  // ONE renderer for it, used on every surface a subject appears on, so the
  // same element cannot read two ways on one screen.
  pin.match(findings, /export function SubjectLabel\(/, "expected ONE shared subject renderer");
  // AND IT ACTUALLY LEADS WITH THE NAME (refuter, 2026-08-18). Everything above
  // pins the SPLIT; nothing pinned the RENDER, so the whole unit's claim could
  // be reverted inside this one span and stay green -- proved by mutation
  // (`{parts.lead}` -> `{parts.id ?? parts.lead}` restored id-first rendering
  // on every surface at once and failed no test). The name is the bold lead;
  // the machine identity is the muted tail behind it, never the other way up.
  pin.match(
    findings,
    /<span className="font-semibold text-slate-900">\{parts\.lead\}<\/span>/,
    "expected the shared renderer to LEAD with the road name in bold -- the split is useless if the id is printed first anyway",
  );
  pin.match(
    findings,
    /const tail = \[parts\.trailing, shownId === null \? "" : `#\$\{shownId\}`\]/,
    "expected the direction token and the id to be the TAIL behind the name, and an absent id to add nothing rather than print a coerced blank",
  );
  const pageUses = (page.match(/<SubjectLabel/g) ?? []).length;
  pin.match(
    `count=${pageUses}`,
    /^count=([3-9]|\d{2,})$/,
    "expected the detail page to print subjects through the shared renderer on the fix card, the finding rows and the candidate rows",
  );
  pin.doesNotMatch(
    page,
    /\{`\$\{candidate\.link_id\} · \$\{candidate\.name\}/,
    "expected the id-first candidate string to be gone -- the road name leads now",
  );
});

contractTest("unit #41: two findings sharing one (code, subject) are two rows, not one", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");

  // FOUND ON THE LIVE SCREEN (job f57cf27e33f5, 2026-08-17): V3 raises
  // `v3_silent_departure_point / 대덕대로 [FWD] w52496957s1` TWICE -- once for
  // link 427 and once for link 429 -- which is exactly the duplicate shape
  // unit #30's own doctrine documents. `(code, subject)` is the finding's
  // identity for APPLYING a fix; it is not a unique LIST key, and using it as
  // one made React treat the two cards as one component. The row's list key
  // and its radio-group name both take the row's index as well.
  //
  // AND THAT INDEX IS THE PAYLOAD'S, NOT THE SCREEN'S (refuter, 2026-08-18).
  // `orderedFixes` is a DISPLAY partition and a live check re-gates this job in
  // place (the fixes fetch is keyed on `gate.checked_at`, so the payload is
  // replaced with no navigation). Keyed by display position, a row crossing the
  // partition makes React PRESERVE a key whose finding changed: simulated on
  // the real duplicate pair, `<code>::<subject>::2` was link 429's card and
  // became link 427's, carrying 429's typed attestation basis, its picked
  // destination and its ticked map-confirmation onto a different link. An
  // attestation is a recorded human claim about ONE object. The payload index
  // is invariant under every reorder, so a preserved key is the same finding.
  // RE-AIMED, unit #41-b, and the claim got STRONGER rather than weaker: the
  // stateful card now lives INSIDE the proposal-first finding list rather than
  // beside it, so the reordering array and the state-carrying component are the
  // same list. The identity is unchanged -- (code, subject, PAYLOAD index) --
  // and it moved out of the render body into one named helper so both the
  // merged rows and the unpaired tail spell it exactly once.
  pin.match(
    page,
    /const rowKeyOf = \(entry: GenesisFixFinding\): string =>\s*\n\s*`\$\{entry\.code\}::\$\{entry\.subject\}::\$\{checkFixes\.indexOf\(entry\)\}`;/,
    "expected a fix row's identity to be (code, subject, PAYLOAD index) -- gate.py can emit the same (code, subject) twice, and a display-order index lets a reorder carry one finding's attestation state onto the other",
  );
  pin.match(
    page,
    /key=\{rowKeyOf\(row\.fix\)\}\s*\n\s*instanceKey=\{rowKeyOf\(row\.fix\)\}/,
    "expected the React key and the row's instanceKey to be the SAME payload-stable identity -- two spellings is how they drift apart",
  );
  // AND THE <li> AROUND IT, which is new exposure this unit created: React
  // identity runs from the nearest keyed ancestor, so a display-indexed <li>
  // would carry the card's typed basis across a re-gate reorder even though the
  // card's own key is payload-stable.
  pin.match(
    page,
    /<li key=\{`\$\{row\.key\}::\$\{row\.payloadIndex\}`\}/,
    "expected the merged finding row's own key to be payload-stable too -- the stateful card is inside it now, and a display-indexed <li> reintroduces exactly the state-slide unit #41's refuter closed",
  );
  pin.match(
    page,
    /const rows = findings\.map\(\(finding, payloadIndex\) => \(\{/,
    "expected payloadIndex to be taken from the GATE PAYLOAD's own array, before the proposal-first partition reorders anything",
  );
  pin.doesNotMatch(
    page,
    /visible\.map\(\((?:row|entry|finding), index\)/,
    "expected the merged rows never to take their identity from their position in the DISPLAY partition",
  );
  pin.match(
    page,
    /name=\{`attest-\$\{instanceKey\}`\}/,
    "expected the candidate radio group to be named per ROW, not per (code, subject) -- two duplicate cards sharing one group name merge into one picker",
  );
  // The APPLY payload still addresses the finding by (code, subject) -- the
  // row key is a rendering concern and must not leak into the request.
  pin.match(
    page,
    /finding_code: finding\.code,/,
    "expected the apply body to keep addressing the finding by its own code",
  );
  pin.match(
    page,
    /subject: finding\.subject,/,
    "expected the apply body to keep addressing the finding by its own subject -- never by a row index",
  );
  pin.doesNotMatch(
    sliceBetween(page, "const job = await applyGenesisFix(jobId, {", "});"),
    /instanceKey/,
    "expected the row's rendering identity never to reach the apply request",
  );
});

contractTest("unit #42: 'the road continues to X' and 'the road ends here' cannot both be held", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");

  // THE STRUCTURAL CLAIM OF THE UNIT. A road cannot both continue to link X and
  // end here, so the two answers must be un-holdable together -- not by a rule
  // some later edit forgets, but by living in ONE radio group whose value can
  // only be one thing. The sentinel is what makes that possible: draft link ids
  // are numeric, so it can never collide with a candidate's own value.
  pin.match(
    page,
    /const DEAD_END_ANSWER = "__real_world_dead_end__";/,
    "expected a sentinel answer value that no draft link id can ever collide with",
  );
  pin.match(
    page,
    /const deadEndChosen = Boolean\(deadEnd\) && attestTarget === DEAD_END_ANSWER;/,
    "expected the dead-end answer to be READ OFF the same state the candidate picker writes (a second state variable is how two exclusive answers become simultaneously holdable), and gated on the offer still being present after a live-check payload swap",
  );
  const worldGap = sliceBetween(page, "{worldGapAsked && (", '{deadEndError && (');
  pin.match(worldGap, /\S/, "expected to find the world-gap question block (markers moved?)");
  // ONE group name for both answers. Two names would render two independent
  // groups, and a reader could tick a destination AND "it ends here".
  const radioNames = worldGap.match(/name=\{`[^`]+`\}/g) ?? [];
  pin.match(
    `${radioNames.length >= 2 && new Set(radioNames).size === 1}`,
    /true/,
    `expected every radio in the world-gap block to share ONE group name, found ${JSON.stringify(radioNames)}`,
  );
  pin.match(
    worldGap,
    /value=\{DEAD_END_ANSWER\}/,
    "expected the dead-end answer to be an option in the candidate group, not a control beside it",
  );
  // Both answers are ask-first, and the SAME required basis gates both: a
  // confirmation with no stated basis is a click, exactly as an attestation
  // with none is.
  pin.match(
    worldGap,
    /disabled=\{confirmingDeadEnd \|\| !attestBasis\.trim\(\)\}/,
    "expected the confirm button to stay locked until the confirmer has written their basis",
  );
  pin.match(
    worldGap,
    /disabled=\{applying \|\| !attestChoice \|\| !attestBasis\.trim\(\)\}/,
    "expected the attest button to keep its own ask-first lock beside the new one",
  );
});

contractTest("unit #42: a confirmed dead end excludes ONE way, and the scope is shown before the signature", (pin) => {
  const api = read("lib/api.ts");
  const page = read("app/genesis/jobs/[id]/page.tsx");

  // A DIFFERENT ROUTE from both apply and regenerate, pinned together so none
  // of the three can quietly become another. A dead-end confirmation never
  // edits this artifact; it starts a re-derivation without the confirmed way.
  pin.match(
    api,
    /export async function confirmGenesisDeadEnd[\s\S]*?apiMutationFetch\(`\/api\/genesis\/jobs\/\$\{jobId\}\/dead-end-confirmation`/,
    "expected the confirmation to go through apiMutationFetch on its own route",
  );
  const body = sliceBetween(api, "export async function confirmGenesisDeadEnd", "\n}\n");
  // STRUCTURAL, not conventional: there is nowhere in this body to name the way
  // to remove, so a client cannot widen an exclusion past the road the reader
  // actually looked at -- the server re-derives it from the finding's own
  // census record. And nowhere to name a confirmer, for unit #35's reason.
  pin.doesNotMatch(
    body,
    /way_id:|confirmed_by|confirmed_at/,
    "expected the confirm body to carry no way id and no confirmer -- the server derives the one and stamps the other",
  );
  pin.match(
    body,
    /acknowledged_collateral_way_ids: number\[\];/,
    "expected the acknowledged-collateral echo to be part of the request -- the server refuses a set that differs from its own re-derivation, and that refusal is the proof the reader saw what they were removing",
  );
  // The scope a reader signs for is RENDERED, and the empty case is a
  // measurement rather than a silence.
  pin.match(
    page,
    /제외되는 것: way \$\{deadEnd\.way_id\} 하나/,
    "expected the confirm panel to name the ONE way being excluded",
  );
  pin.match(
    page,
    /연결된 다른 도로는 제외되지 않습니다/,
    "expected the panel to say out loud that roads connected to it are NOT excluded",
  );
  pin.match(
    page,
    /소스 기준으로 이 제외 때문에 새로 끊기는 도로는 없습니다/,
    "expected zero collateral to be stated as a measurement, never left as an absence a reader must infer",
  );
  pin.match(
    page,
    /deadEnd\.collateral\.length > 0 \?[\s\S]{0,400}?text-rose-700/,
    "expected a non-empty collateral list to render in the failure colour -- it is the one thing on this panel that can be a surprise",
  );
  // The two answers have different consequences, so they got different consent
  // sentences. One line for both would have been a lie about one of them.
  //
  // UNIT #41-b, BY THE PRODUCT OWNER'S OWN INSTRUCTION: the ATTESTING half of
  // that pair is deleted, and the pin that held it ("expected the attestation
  // consent line to keep saying that a real Vissim edit runs", which matched
  // `: t(\n "승인하면 실제 Vissim 편집이 실행되고 네트워크가 다시 게이트를
  // 거칩니다."`) is REMOVED with it -- user, live pass 2026-08-18: "'승인하면
  // 실제 Vissim 편집이 실행되고 네트워크가 다시 게이트를 거칩니다.' 이것도
  // 없애도". Recorded here rather than silently dropped, because a pin removed
  // without a named authority is indistinguishable from a pin lost in a rebase.
  //
  // The dead-end sentence is UNTOUCHED and still pinned: it names a consequence
  // the button cannot ("a NEW job from the same source; this artifact is left
  // alone"), which is the thing that made it a separate sentence to begin with.
  // It is now rendered under a guard rather than as a ternary arm, so the
  // attesting answer carries no consent line at all.
  pin.match(
    page,
    /\{deadEndChosen && \(\s*\n[\s\S]{0,200}?t\(\s*\n?\s*"확인하면 이 way를 명명 제외한 새 생성이 같은 원본으로 시작됩니다/,
    "expected the dead-end consent line to say a NEW generation starts and this artifact is not edited, and to render only for the answer it is about",
  );
  // THE WHOLE "A REAL VISSIM EDIT RUNS" SENTENCE FAMILY IS NOW BANNED, in
  // every language and both lengths (unit #41-c, user 2026-08-18: "1번 승인
  // 카드 문장도 없애고"). Unit #41-b deleted the attestation card's short form
  // and these pins carried a trailing period precisely to let the approval
  // card's longer form through; that exemption is spent -- the long form is
  // deleted too, and the B5-2 pins that required it are removed under the same
  // authority (see "B5-2 stage 4: an approval is a card, never a modal").
  //
  // So the pattern is now the STEM the two forms share, with no terminal
  // punctuation and no surrounding quotes: that covers the short form, the
  // long form, and a re-add spelled with single quotes or a template literal,
  // in one pattern per language. Against de-commented source, because both
  // pages explain the deletion in prose that necessarily quotes it.
  for (const [stem, language] of [
    ["승인하면 실제 Vissim 편집이 실행되고 네트워크가 다시 게이트를 거칩니다", "ko"],
    ["Approving runs a real Vissim edit and re-gates the network", "en"],
    ["承認すると実際のVissim編集が実行され、ネットワークは再度ゲートを通過します", "ja"],
  ] as const) {
    pin.doesNotMatch(
      withoutComments(page),
      new RegExp(stem),
      `expected the "a real Vissim edit runs" sentence to stay deleted in ${language}, in EITHER length (user instruction, 2026-08-18) -- a three-language string comes back one language at a time, and the longer form is how it came back once already`,
    );
  }
});

contractTest("unit #42: the dead-end refusal is a sentence, never a control", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");
  const api = read("lib/api.ts");

  // The evidence hierarchy is the load-bearing refusal: the source declares
  // this road continues, so no human confirmation may delete it. The reader is
  // TOLD that, with the backend's own measured reason -- not left to wonder
  // where the option went.
  const refusalBlock = sliceBetween(
    page,
    "{disposition.dead_end_refusal && (",
    "{/* Unit #35 -- the ask-first QUESTION",
  );
  pin.match(refusalBlock, /\S/, "expected to find the dead-end refusal block (markers moved?)");
  pin.doesNotMatch(
    refusalBlock,
    /<button|<input|onClick/,
    "expected the dead-end refusal to render prose only -- a refusal with a control is not a refusal",
  );
  pin.match(
    refusalBlock,
    /disposition\.dead_end_refusal\.reason_code/,
    "expected the refusal to quote the backend's OWN measured reason code, not a message this client wrote",
  );
  pin.match(
    refusalBlock,
    /disposition\.dead_end_refusal\.detail/,
    "expected the measured detail to travel with the code",
  );
  // Presence IS the fact on both keys, so an absent one can never be read as
  // "the disposition was considered and rejected".
  pin.match(
    api,
    /dead_end_confirmation\?: GenesisFixDeadEndConfirmation;\s*\n\s*dead_end_refusal\?: GenesisFixDeadEndRefusal;/,
    "expected both dead-end keys to be optional on the disposition -- presence is the fact",
  );
});

// ---------------------------------------------------------------------------
// Unit #41-b -- the four things the user asked for after seeing #41 and #42 on
// the real screen (live pass 2026-08-18, job 2712071437d0)
// ---------------------------------------------------------------------------
//
//   1. "수정 제안 칸 따로 만들지 말고 위에 걍 넣어 같이. 그럼 이름 반복될 필요도
//      없고 칸도 줄고 더 간략할 것 같애" -- the separate section is gone and each
//      disposition renders inside the 소견 that raised it.
//   2. "the second 자세히는 없애" -- one 자세히 per card, not two.
//   3. "'승인하면 실제 Vissim 편집이…' 이것도 없애도" -- pinned in the unit #42
//      test above, which is where the sentence used to be held.
//   4. "이름 반복 service[FWD] w1294917882s0 이게 너무 자주돼" -- the subject was
//      printed by SubjectLabel and then AGAIN by the gate sentence it leads.
//
// Every one of these is a DISPLAY change. The pins below are written to fail if
// any of them ever becomes a data change: the API calls, the (code, subject)
// apply address and the payload-index row identity are all pinned unchanged in
// their own tests above, and nothing here relaxes them.

contractTest("unit #41-b: the proposal renders inside its finding, and the separate 수정 제안 칸 is gone", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");
  const gridBody = sliceBetween(page, "function GateCheckGrid(", "/** The five downloadable artifacts");
  pin.match(gridBody, /\S/, "expected to find the GateCheckGrid body (markers moved? the doesNotMatch below would pass vacuously)");

  // THE 칸 IS GONE -- heading and all. Pinned on the heading STRING rather than
  // on a class, because a section that came back under a different border would
  // be the same box the user asked to lose.
  pin.doesNotMatch(
    withoutComments(page),
    /t\("수정 제안", "Fix proposals", "修正提案"\)/,
    "expected the separate 수정 제안 section heading to be gone -- the dispositions read inside their own findings now",
  );

  // ...and its ⓘ MOVED rather than went with it. The sentence is doctrine
  // ("자동 수정이 없다는 답은 정보이지 버튼이 아닙니다 — 거부는 진단, 에이전트는
  // 치료"), so losing it with the box it hung on would be deleting a claim.
  pin.match(
    page,
    /\{selected\.title\}\s*\n\s*<InfoTip/,
    "expected the fix-catalogue disclosure to move onto the check's own title, which is exactly what it describes",
  );
  pin.match(
    page,
    /자동 수정이 없다는 답은 정보이지 버튼이 아닙니다 — 거부는 진단, 에이전트는 치료\./,
    "expected the catalogue's doctrine sentence to survive the move, word for word",
  );
  // The three catalogue STATES survive too -- loading, refused, and a check with
  // no individual proposal. A merge that silently dropped them would make a
  // 401 look like "this check is clean".
  for (const [sentence, why] of [
    ["수정 제안을 불러오는 중…", "the loading state"],
    ["수정 제안을 보려면 로그인이 필요합니다", "the signed-out refusal"],
    ["수정 제안을 불러오지 못했습니다", "the generic fetch failure"],
    ["이 검사에는 개별 수정 제안이 없습니다", "the no-proposal answer"],
  ] as const) {
    pin.match(
      page,
      new RegExp(sentence),
      `expected ${why} to survive the merge -- it is the reader's only sign the catalogue did not answer`,
    );
  }

  // THE SUBJECT IS PRINTED ONCE PER CARD. The merged card is told not to print
  // one (its row already did); the unpaired tail, which has no row above it, is
  // told to. Both halves pinned -- a version that printed none would lose the
  // subject entirely for the capped tail.
  pin.match(
    page,
    /\{showSubject && \(\s*\n\s*<div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">/,
    "expected the fix card's own subject header to be conditional -- merged under its row, the row already named the finding",
  );
  pin.match(
    page,
    /showSubject\s*\n\s*jobId=\{jobId\}\s*\n\s*finding=\{entry\}/,
    "expected the unpaired dispositions to keep printing their own subject -- there is no row above them to print it",
  );

  // NOTHING ABOUT THE PAYLOAD MOVED. The apply address is still the finding's
  // own (code, subject) and the catalogue fetch is untouched -- re-asserted here
  // because this unit is the one that could most plausibly have "simplified"
  // them into the row's identity.
  pin.match(page, /finding_code: finding\.code,/, "expected the apply body to keep addressing the finding by its own code");
  pin.match(page, /subject: finding\.subject,/, "expected the apply body to keep addressing the finding by its own subject");
  pin.doesNotMatch(gridBody, /\.sort\(/, "expected nothing in the grid to sort a fetched array in place");
});

contractTest("unit #41-b: one 자세히 per card, and the sentences the second one held moved into it", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");

  // ONE PER CARD. Counted, not matched: the bug the user hit was TWO toggles
  // with the SAME summary line in one card, which no single `match` can see.
  // Two in the file = one on the refusal card, one on the approval card -- the
  // two branches of `!actionable`, which a reader never meets together.
  const disclosures = (page.match(/<CardDetails summary=\{moreLabel\(t\)\}>/g) ?? []).length;
  pin.match(
    `count=${disclosures}`,
    /^count=2$/,
    "expected exactly two 자세히 disclosures in the file -- one per card branch, never two in one card (user, live pass 2026-08-18: 'the second 자세히는 없애')",
  );
  // The attestation block is where the second one lived. It must now contain
  // none at all -- sliced, so this cannot pass on the refusal card's copy.
  const worldGap = sliceBetween(page, "{worldGapAsked && (", "{deadEndError && (");
  pin.match(worldGap, /\S/, "expected to find the world-gap question block (markers moved?)");
  pin.doesNotMatch(
    worldGap,
    /<CardDetails/,
    "expected the attestation block's own disclosure to be gone -- its contents moved up into the card's single 자세히",
  );

  // MOVED, NOT DROPPED -- all three sentences, and each still under the exact
  // condition that used to render it. The two attestation explanations are
  // pinned verbatim by unit #41's own test above; what this pin adds is that
  // they are inside the surviving disclosure and still conditional, and that
  // unit #42's dead-end doctrine travelled with them.
  const refusalDisclosure = sliceBetween(page, "<CardDetails summary={moreLabel(t)}>", "</CardDetails>");
  pin.match(refusalDisclosure, /\S/, "expected to find the surviving disclosure (markers moved?)");
  pin.match(
    refusalDisclosure,
    /\{worldGapAsked && \([\s\S]{0,1200}?원본 데이터는 이 지점에서 이어지는 도로를 선언하지 않습니다/,
    "expected the 'the source declares no continuation' sentence to render only where it used to -- when the world gap is actually asked",
  );
  // UNIT #41-c NARROWED THIS ONE. It describes how the system would BUILD a
  // connector and ends "확인하시는 것은 도로가 이어진다는 사실뿐입니다" -- but it
  // rode in on `worldGapAsked`, which is ALSO true when the only answer on
  // offer is the dead end (`candidates || deadEnd`). On a road the source
  // shares the gap on with no reachable link end, the reader was told what
  // they attest is that the road continues, on the one card whose single
  // answer is that it does not. Its condition is the offer it was always
  // about. A card carrying BOTH answers is unchanged.
  pin.match(
    page,
    /const connectorOffered = Boolean\(attestation && attestation\.candidates\.length > 0\);/,
    "expected 'is a connector actually on offer' to be its own named answer, not folded back into worldGapAsked",
  );
  pin.match(
    page,
    /const worldGapAsked = connectorOffered \|\| Boolean\(deadEnd\);/,
    "expected worldGapAsked to keep meaning 'either answer is on offer' -- the narrowing is which sentence reads it, not what it means",
  );
  pin.match(
    refusalDisclosure,
    /\{connectorOffered && \([\s\S]{0,800}?연결로의 형상과 차로 대응은 기존 두 링크 끝에서 시스템이 구성합니다/,
    "expected the 'the system constructs the connector' sentence to render ONLY where a connector is on offer -- on a dead-end-only card it contradicts the single answer that card has",
  );
  pin.match(
    refusalDisclosure,
    /\{deadEnd && \([\s\S]{0,800}?여기서 끝난다고 확인하셔도 원본 \.osm은 편집되지 않습니다/,
    "expected unit #42's dead-end doctrine to move into the same disclosure, still gated on the dead-end offer being present",
  );
  // The machine identity stays LAST inside it (unit #41's own order: what it is,
  // what it says, then the identifiers a bug report needs).
  pin.match(
    refusalDisclosure,
    /여기서 끝난다고 확인하셔도[\s\S]*?\{finding\.code\}/,
    "expected the raw finding/reason codes to stay at the END of the disclosure, behind the prose that was moved in above them",
  );
  // AND IT IS STILL A NATIVE <details>: the refusal branch may carry no button,
  // and a merged card that spelled its toggle as one would break that.
  pin.doesNotMatch(
    sliceBetween(page, "function CardDetails(", "/** The one summary label"),
    /<button/,
    "expected the disclosure toggle never to be spelled as a button",
  );
});

contractTest("unit #41-d: the one-line answer names the treatment THIS card offers", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");
  const summary = sliceBetween(page, '<p className="text-[15px] leading-relaxed text-slate-700">', "</p>");
  pin.match(summary, /\S/, "expected to find the one-line answer (markers moved? the pins below would pass vacuously)");

  // IT BRANCHES ON WHICH ANSWER IS ON OFFER, not on "either is". `worldGapAsked`
  // is true for the dead-end offer alone, and the sentence it selected said the
  // fix "적용" -- which is what attesting a CONNECTOR does (a real add_connector
  // edit against this artifact). A dead-end-only card treats the finding the
  // opposite way and has no such button.
  pin.match(
    summary,
    /\{connectorOffered\s*\n\s*\? t\(\s*\n\s*"자동 수정 없음 — 현장을 아는 사람이 확인하면 적용할 수 있습니다\./,
    "expected the 'and then it applies' answer to be gated on a connector actually being on offer -- that is the only answer that applies anything",
  );
  pin.doesNotMatch(
    summary,
    /\{worldGapAsked/,
    "expected the one-line answer never to branch on 'either answer is on offer' -- the two answers have opposite treatments",
  );

  // THE DEAD-END-ONLY ANSWER, in all three languages. #42's mechanism: the
  // confirmation POSTs and comes back a NEW job with that road excluded by
  // name; nothing about this artifact is edited.
  for (const [sentence, language] of [
    ["자동 수정 없음 — 현장을 아는 사람이 확인하면 그 도로를 제외하고 다시 생성합니다\\.", "ko"],
    ["No automated fix — someone who knows the site can confirm it ends, and then it is generated again with that road left out\\.", "en"],
    ["自動修正なし — 現場をご存じの方が確認すれば、その道路を除いて再生成します。", "ja"],
  ] as const) {
    pin.match(
      summary,
      new RegExp(sentence),
      `expected the dead-end-only card to say its treatment is a re-generation with that road left out, in ${language} -- it has no apply button to promise`,
    );
  }
  pin.match(
    summary,
    /: deadEnd\s*\n\s*\? t\(\s*\n\s*"자동 수정 없음 — 현장을 아는 사람이 확인하면 그 도로를/,
    "expected the re-generation answer to be the one selected when the dead end is the only offer",
  );
  // A MIXED card keeps "적용": `connectorOffered` is true there and is tested
  // first, so both-answers-open reads as the connector's treatment -- pinned as
  // ORDER, because swapping the two arms would silently change that card too.
  pin.match(
    summary,
    /\{connectorOffered[\s\S]*?: deadEnd/,
    "expected connectorOffered to be tested BEFORE deadEnd, so a card carrying both answers keeps the connector's wording",
  );
});

test("unit #41-b: the subject trim removes a repeated subject and NOTHING else", () => {
  // THE REPORTED RENDER. gate.py words this finding "<subject>: <what is
  // wrong>", and the row prints the subject itself immediately above.
  assert.equal(
    messageWithoutSubject(
      "service [FWD] w1294917882s0",
      "service [FWD] w1294917882s0: 모델 경계가 아닌데 이어지는 도로가 없습니다",
    ),
    "모델 경계가 아닌데 이어지는 도로가 없습니다",
  );
  // A space boundary counts too, not only a colon.
  assert.equal(messageWithoutSubject("링크 42", "링크 42 has no outgoing connector"), "has no outgoing connector");

  // THE BOUNDARY RULE, which is the whole reason this is a helper and not an
  // inline `.replace()`. A subject that is merely the HEAD of a longer token
  // must not be trimmed -- doing so would silently renumber the object the
  // sentence is about.
  assert.equal(messageWithoutSubject("링크 4", "링크 42는 진입로가 없습니다"), "링크 42는 진입로가 없습니다");
  assert.equal(messageWithoutSubject("V3", "V300 refused"), "V300 refused");

  // NOT A PREFIX AT ALL -> byte for byte. The backend owns the sentence.
  assert.equal(
    messageWithoutSubject("대덕대로 [FWD] w52496957s1", "이 링크에서 출발하는 차량이 없습니다"),
    "이 링크에서 출발하는 차량이 없습니다",
  );
  // Only the FIRST character onward counts -- a subject appearing mid-sentence
  // is part of what the gate said and is never touched.
  assert.equal(
    messageWithoutSubject("링크 42", "커넥터가 링크 42로 이어지지 않습니다"),
    "커넥터가 링크 42로 이어지지 않습니다",
  );
  // ...and the ANCHORING is what makes that true, not luck (refuter,
  // 2026-08-18). The case above survives a `startsWith` -> `includes` weakening
  // by coincidence: the slice happens to land mid-token, so the separator guard
  // still refuses it. Proved by mutation -- that weakening kept all 124 tests
  // green. Here the slice lands exactly on a separator, so ONLY the anchoring
  // stops the head of the sentence being eaten: a trim that searched anywhere
  // would answer "링크 42가 문제" and drop the clause naming which check spoke.
  assert.equal(
    messageWithoutSubject("링크 42", "V3 검사: 링크 42가 문제"),
    "V3 검사: 링크 42가 문제",
  );
  assert.equal(messageWithoutSubject("AB", "X: AB continues"), "X: AB continues");

  // A message that is ONLY its subject stays whole: an empty row says less than
  // a repeated one.
  assert.equal(messageWithoutSubject("링크 42", "링크 42"), "링크 42");
  assert.equal(messageWithoutSubject("링크 42", "링크 42:"), "링크 42:");

  // Degenerate inputs answer with the message rather than throwing -- this runs
  // inside a render.
  assert.equal(messageWithoutSubject("", "무언가 잘못되었습니다"), "무언가 잘못되었습니다");
  assert.equal(messageWithoutSubject("링크 42", ""), "");
});

// ---------------------------------------------------------------------------
// Unit #47 — the diagnosis ladder (#43) and the Table 5-6 proposal set (#44)
// reach a surface a user can see, and the #44-b grant reaches the product path
// without ever becoming a thing a file can claim.
// ---------------------------------------------------------------------------

contractTest("unit #47: the approval card states the whole SET, not just the one write it pauses on", (pin) => {
  const floatingAssistant = read("components/FloatingAssistant.tsx");
  const api = read("lib/api.ts");

  pin.match(
    api,
    /car_following_set\?: AgentCarFollowingSet;/,
    "expected the set to ride on the approval payload as a sibling of actions/warnings/focus",
  );
  pin.match(
    floatingAssistant,
    /carFollowingSet: event\.car_following_set/,
    "expected the approval state to carry the set through from the wire event",
  );
  pin.match(
    floatingAssistant,
    /<CarFollowingSetPanel set=\{approval\.carFollowingSet\} action=\{approval\.actions\[0\]\} \/>/,
    "expected the approval card to render the set panel",
  );
  // The whole point: all three members are listed, and the one being approved
  // now is told apart from the two that are not.
  pin.match(
    floatingAssistant,
    /members\.map\(\(member\) => \{[\s\S]{0,400}?const isThis = member === thisParameter;/,
    "expected every set member to render, with THIS card's write identified among them",
  );
  // A member is one of THREE things, not two (refuter, #47): the write being
  // approved now, one already in the model, or one that is not applied yet. The
  // label used to be a flat "미승인 / not yet approved" for both of the other
  // two, which is false as soon as the first of the three lands -- and this
  // panel exists precisely so a partly-applied row cannot go unnoticed.
  pin.match(
    floatingAssistant,
    /isThis[\s\S]{0,300}?이 승인[\s\S]{0,160}?현재 값 일치[\s\S]{0,160}?미적용/,
    "expected a member to be told apart as this approval / already landed / not applied yet",
  );
  pin.match(
    floatingAssistant,
    /const landed = \(member: string\) =>\s*\n\s*typeof current\[member\] === "number" && current\[member\] === expected\[member\];/,
    "expected 'already landed' to be read off the live model's value, not an approval history",
  );
  pin.match(
    api,
    /current_values\?: Record<string, number \| null> \| null;/,
    "expected the card payload to carry what the behaviour currently holds",
  );
  pin.doesNotMatch(
    withoutComments(floatingAssistant),
    /미승인|not yet approved/,
    "the panel must not claim a member is unapproved -- it knows the model's state, not the approval's",
  );
  // Both bracketing rows, so rounding DOWN is auditable rather than a number
  // that appeared from nowhere.
  pin.match(
    floatingAssistant,
    /bracketed[\s\S]{0,700}?bracket_lower\?\.saturation_flow_pcu[\s\S]{0,300}?bracket_upper\?\.saturation_flow_pcu/,
    "expected a bracketed lookup to show BOTH neighbouring rows",
  );
  pin.match(
    floatingAssistant,
    /아래 행으로 내림/,
    "expected the down-rounding policy to be stated where the two rows are shown",
  );
  // The measurement is cited on the card, not merely asserted to exist.
  pin.match(
    floatingAssistant,
    /measurement\.attribution \?\? "-"\} · \{measurement\.method \?\? "-"/,
    "expected the measurement's attribution and method on the card",
  );
  // The consent sentence: the forecast plus the radius the human agrees to.
  pin.match(
    floatingAssistant,
    /set\.consent_radius_phrase/,
    "expected the consented radius phrase (the sentence the exception rests on) on the card",
  );
  pin.match(
    floatingAssistant,
    /approvedPath\.expected_to_refuse[\s\S]{0,600}?승인해도 거부됩니다[\s\S]{0,200}?승인 시 통과 예상/,
    "expected the approved-path forecast to say clean or refused, in the user's language",
  );
});

contractTest("unit #47: the partial-approval warning is the backend's sentence, quoted, never the UI's", (pin) => {
  const floatingAssistant = read("components/FloatingAssistant.tsx");
  const saturationFlow = readFileSync(
    path.join(repoRoot, "backend", "services", "agent", "saturation_flow.py"),
    "utf-8",
  );

  // The sentence has ONE home, and it is the module that knows why it is true.
  pin.match(
    saturationFlow,
    /PARTIAL_APPROVAL_WARNING: dict\[str, str\] = \{/,
    "expected the set's partial-approval warning to be a named constant the UI can quote",
  );
  pin.match(
    saturationFlow,
    /행태가 표의 어느 행과도 일치하지 않게 됩니다\./,
    "expected the Korean sentence to still say a partly-approved set matches no published row",
  );
  // ... and it is still spliced back into every member's rationale, so nothing
  // a reader saw before this extraction stopped being said.
  for (const lang of ["ko", "en", "ja"]) {
    pin.match(
      saturationFlow,
      literalPattern(`+ PARTIAL_APPROVAL_WARNING["${lang}"]`),
      `expected the ${lang} rationale to still end with that same sentence`,
    );
  }

  pin.match(
    floatingAssistant,
    /pickText\(set\.partial_approval_warning, lang\)/,
    "expected the card to render the backend's own sentence in the user's language",
  );
  // The UI must not carry its own copy of it. A second wording is a second
  // thing to keep true.
  pin.doesNotMatch(
    withoutComments(floatingAssistant),
    /어느 행과도 일치하지 않|matching no published row/,
    "the UI must never write its own version of the partial-approval warning",
  );
});

contractTest("unit #47: the diagnosis ladder renders per node -- rung, next action, and the evidence behind a fold", (pin) => {
  const floatingAssistant = read("components/FloatingAssistant.tsx");
  const api = read("lib/api.ts");
  const orchestrator = readFileSync(
    path.join(repoRoot, "backend", "services", "agent", "orchestrator.py"),
    "utf-8",
  );
  const assistantRouter = readFileSync(
    path.join(repoRoot, "backend", "routers", "assistant.py"),
    "utf-8",
  );

  // It has to be on the wire at all -- it was not, before this unit.
  pin.match(
    orchestrator,
    /if decision\.tool == "run_reality_check" and envelope\["status"\] == "ok":/,
    "expected the reality-check step to graft its diagnosis onto the step payload",
  );
  pin.match(
    assistantRouter,
    /if "diagnosis" in value:\s*\n\s*streamed_step\["diagnosis"\] = value\["diagnosis"\]/,
    "expected the router to forward the diagnosis onto the streamed step event",
  );
  pin.match(api, /diagnosis\?: AgentDiagnosis;/, "expected AgentStep to carry the diagnosis");

  pin.match(
    floatingAssistant,
    /<AgentDiagnosisLadder steps=\{message\.agentSteps\} \/>/,
    "expected the ladder to render in the chat message alongside the step narration",
  );
  // Five rungs, in human words.
  for (const rung of ["DEMAND_LEVEL", "ROUTING_SPLIT", "CAPACITY_LIMIT", "SIGNAL_TIMING", "UNCLASSIFIABLE"]) {
    pin.match(
      floatingAssistant,
      literalPattern(`${rung}: [`),
      `expected a human label for the ${rung} rung`,
    );
  }
  // The next action -- the thing a modeller does next -- is the backend's own
  // sentence, and the rung's reasoning folds away behind a disclosure.
  pin.match(
    floatingAssistant,
    /pickText\(node\.next_action\?\.text, lang\)/,
    "expected the next action to be the backend's own localized sentence",
  );
  pin.match(
    floatingAssistant,
    /<details[\s\S]{0,600}?pickText\(node\.why, lang\)/,
    "expected the rung's reasoning to sit behind a disclosure, not in the one-line verdict",
  );
  // #44's unwired hand-off, made to follow naturally from the rung that needs it.
  pin.match(
    floatingAssistant,
    /node\.rung === "CAPACITY_LIMIT"[\s\S]{0,400}?Table 5-6/,
    "expected a capacity-limited node to say that a measurement yields the Table 5-6 set",
  );
  // Raw codes are shown, but folded and mono -- the #41 doctrine convention.
  pin.match(
    floatingAssistant,
    /text-slate-400">\s*\{node\.rung\}/,
    "expected the raw rung/next-action codes to be mono and inside the fold",
  );
});

contractTest("unit #47: a grant is re-minted from memory, never carried by the pending-approval file", (pin) => {
  const assistantRouter = readFileSync(
    path.join(repoRoot, "backend", "routers", "assistant.py"),
    "utf-8",
  );
  const broker = readFileSync(
    path.join(repoRoot, "backend", "services", "agent", "car_following_broker.py"),
    "utf-8",
  );

  // The file gets a REFERENCE and nothing else.
  pin.match(
    assistantRouter,
    /pending_file_payload\["car_following_set_id"\] = car_following_set_id/,
    "expected only an opaque set id to be persisted beside the signatures",
  );
  pin.doesNotMatch(
    assistantRouter,
    /pending_file_payload\["car_following_grant"\]/,
    "the pending-approval file must never carry a grant",
  );
  // Both gates get the grant LATE, from the broker, against the live model.
  pin.match(
    assistantRouter,
    /gate = ProposeWriteGate\(\s*\n\s*grant_provider=car_following_broker\.grant_provider\(/,
    "expected the proposing gate to resolve its grant through the broker",
  );
  pin.match(
    assistantRouter,
    /grant_provider=\(\s*\n\s*car_following_broker\.grant_provider\([\s\S]{0,300}?set_id=str\(car_following_set_ref\)/,
    "expected the approve gate to be pinned to the set id the file named, and re-mint it",
  );
  // The broker never deserializes: it re-runs the real minting path.
  // Anchored inside mint() specifically: the same call also appears in
  // remember(), and an unanchored pin stayed green when mint() was mutated to
  // rehydrate instead (proved by mutation, 2026-08-19).
  // Anchored on each function's OWN failure branch: the two call sites are
  // otherwise byte-identical, and a pin that could match either stayed green
  // while mint() was mutated to rehydrate (proved by mutation, 2026-08-19).
  pin.match(
    broker,
    /grant = saturation_flow\.grant_from_proposals\(actions\)\s*\n\s*except \(ValueError, TypeError, AttributeError\):\s*\n\s*return \{\*\*result, "reason": REMINT_FAILED\}/,
    "expected mint() to re-run the only minting path rather than rehydrate a payload",
  );
  pin.match(
    broker,
    /grant = saturation_flow\.grant_from_proposals\(actions\)\s*\n\s*except \(ValueError, TypeError, AttributeError\):\s*\n\s*return None/,
    "expected remember() to validate a set by actually minting one, and keep nothing when it cannot",
  );
  pin.match(
    broker,
    /current = car_following_grant\.consent_radius\(behavior\)[\s\S]{0,400}?if current != consented:/,
    "expected the radius to be re-derived from the live model and compared before any grant is issued",
  );
  pin.doesNotMatch(
    withoutComments(broker),
    /write_text\(|json\.dump|open\(/,
    "the broker must never write a grant, or anything a grant can be rebuilt from, to disk",
  );
  // The store is an ISLAND (refuter, #47). mint() re-derives the grant off the
  // remembered objects, so a reference into the store is a lever on what the
  // grant covers -- measured: mutating a remembered proposal's `value` after
  // remember() made the grant cover ax=99.0 and stop covering the ax=2.5 the
  // human was shown. Copy on the way in AND on the way out.
  pin.match(
    broker,
    /sets\[grant\.set_id\] = _snapshot\(actions\)/,
    "expected remember() to store a private copy, not the caller's objects",
  );
  pin.match(
    broker,
    /return None if stored is None else _snapshot\(stored\)/,
    "expected recall() to hand out a copy, not the stored objects",
  );
});

contractTest("unit #47: the capacity premise is not obtainable by leaving a field out", (pin) => {
  const toolsCalibration = readFileSync(
    path.join(repoRoot, "backend", "services", "agent", "tools_calibration.py"),
    "utf-8",
  );

  // geh_ladder's last branch is `concentrated and control == CONTROL_SIGNALIZED`,
  // and `control` comes from the CALLER (targets[].node_control). Measured on
  // one run's evidence: declaring "signalized" gave SIGNAL_TIMING and a refusal;
  // omitting the field gave CAPACITY_LIMIT, a proposal and a mintable grant. So
  // the omission is refused by name -- MRWA step 6 precedes step 7.
  pin.match(
    toolsCalibration,
    /NODE_CONTROL_UNDECLARED = "NODE_CONTROL_UNDECLARED"/,
    "expected the undeclared-control refusal to have a name of its own",
  );
  pin.match(
    toolsCalibration,
    /if geh_ladder\.NODE_CONTROL_UNKNOWN in \(diagnosis_payload\.get\("missing_evidence"\) or \(\)\):/,
    "expected the ladder's own caveat to be what triggers the refusal",
  );
  pin.match(
    toolsCalibration,
    /"reason_code": NODE_CONTROL_UNDECLARED[\s\S]{0,1200}?"ko":[\s\S]{0,600}?"en":[\s\S]{0,600}?"ja":/,
    "expected the refusal to explain itself in all three languages",
  );
  // ... and the description the planner READS must ask for the field, not
  // assert the derivation is immune to every argument (the retracted sentence).
  pin.doesNotMatch(
    toolsCalibration,
    /no argument a caller can pass/,
    "the tool must not claim its rung derivation is immune to every caller argument",
  );
  pin.match(
    toolsCalibration,
    /Include this node's\s*\n?\s*"?\s*"?node_control in targets/,
    "expected the planner-facing description to ask for node_control by name",
  );
});

// ---------------------------------------------------------------------------
// Unit #48 -- the boundary treatment learns the OTHER finding, and learns to
// propose ONE lasso for all of them.
//
// Three screens' worth of the same fact, all from the user's 2026-08-19 live
// pass:
//   1. a `v3_orphan_link` the lasso severed now carries a widened region of its
//      own -- one finger per SEVERED END, which is why it cannot ride in #40's
//      single-`corridor_patch` payload;
//   2. a job with several boundary findings gets ONE region covering all of
//      them, because two proposals derived from the same original polygon
//      overwrite each other (which is exactly why repeating the extension never
//      healed the orphan);
//   3. the region page's corners can be DRAGGED, which is the premise of
//      handing a human a proposed shape at all.
// ---------------------------------------------------------------------------

contractTest("unit #48: the orphan's region and the all-at-once region are the backend's, never a client guess", (pin) => {
  const page = read("app/genesis/jobs/[id]/page.tsx");

  // 1. BOTH ARE GATED ON A DERIVED SHAPE, never on an attribution and never on
  //    a status string. An orphan the SOURCE itself leaves unconnected has an
  //    attribution key of neither kind and must show no button at all.
  pin.match(
    page,
    /\{disposition\.boundary_extension_patches && \(/,
    "expected the orphan's widen-the-boundary action to be gated on the backend's own derived region",
  );
  const orphanBlock = sliceBetween(
    page,
    "{disposition.boundary_extension_patches && (",
    "{/* Unit #42 -- a NAMED refusal",
  );
  pin.match(orphanBlock, /<BoundaryPatchesProposal/, "expected to find the orphan treatment block (markers moved?)");
  pin.doesNotMatch(
    withoutComments(orphanBlock),
    /orphan_boundary_attribution/,
    "expected the action to key off the derived REGION, never off the attribution -- an attributed orphan whose fingers refused by name has nothing to propose and must show no button",
  );
  pin.match(
    page,
    /\{fixes\?\.boundary_extension_all && \(/,
    "expected the all-at-once region to render only when the backend derived one",
  );

  // 2. THE COMPOSITE'S LABEL IS CHECKABLE. A shape that claims to cover every
  //    boundary finding must NAME them, or the reader has to trust that theirs
  //    is in there.
  const proposalComponent = sliceBetween(
    page,
    "function BoundaryPatchesProposal(",
    "\n/**\n * ONE finding's disposition",
  );
  pin.match(proposalComponent, /\S/, "expected to find the shared proposal component (markers moved?)");
  pin.match(
    proposalComponent,
    /proposal\.covers\.map\(\(cover\) => \(/,
    "expected every covered finding to be named, not just counted",
  );
  // ...and the extrapolated tail is stated in the PROSE, never folded into the
  // measured figure. This component renders above a button that navigates to
  // the screen owning the extract; a reader must not be able to read a drawn
  // bearing as measured road.
  pin.match(
    proposalComponent,
    /totalExtrapolated > 0 && \(/,
    "expected the extrapolation split to be stated whenever there is one",
  );
  pin.match(
    proposalComponent,
    /마지막으로 측정된 방향으로 그은 것입니다/,
    "expected the ko copy to say the remainder was DRAWN on the last measured bearing",
  );
  // The hand-off goes through the shared module and NOTHING is fetched here.
  pin.match(
    proposalComponent,
    /stashRegionPrefill\(boundaryPatchesPrefill\(proposal, jobId\)\)/,
    "expected the proposal to be carried through the shared one-shot hand-off",
  );
  pin.doesNotMatch(
    proposalComponent,
    /startRegionFetch|startGenesisJob|applyGenesisFix/,
    "expected the button to hand a shape to the region page and fetch nothing -- that page's extract button is the ask-first gate",
  );

  // 3. THE COMPOSITE'S REFUSAL IS A SENTENCE, NEVER A CONTROL -- and it says
  //    the per-finding proposals survived, because that is what makes refusing
  //    the all-at-once option safe rather than a dead end.
  const refusalBlock = sliceBetween(
    page,
    "{fixes?.boundary_extension_all_refusal && (",
    "{report ? (",
  );
  pin.match(refusalBlock, /\S/, "expected to find the composite's refusal block (markers moved?)");
  pin.doesNotMatch(refusalBlock, /<button/, "a refusal is a diagnosis, never a button");
  pin.match(
    refusalBlock,
    /boundary_extension_all_refusal\.reason_code/,
    "expected the backend's own named reason to be printed rather than re-worded",
  );
  pin.match(
    refusalBlock,
    /소견별 제안은 각 카드에 그대로 있으니/,
    "expected the refusal to say the per-finding proposals are unaffected",
  );

  // 4. THE PROJECT STAMP travels with a multi-corridor proposal exactly as it
  //    does with a single-finding one: sessionStorage is per-tab and outlives
  //    the reload a project switch does.
  const prefillBuilder = sliceBetween(page, "function boundaryPatchesPrefill(", "\nfunction BoundaryPatchesProposal(");
  pin.match(prefillBuilder, /workspace_id: getWorkspaceId\(\),/, "expected the multi proposal to be stamped with the project it was derived in");
  pin.match(prefillBuilder, /covers: proposal\.covers\.map\(/, "expected every covered finding to travel with the shape");
});

contractTest("unit #48: a region page corner is dragged and typed through ONE setter", (pin) => {
  const page = read("app/genesis/region/page.tsx");
  const map = read("components/genesis/RegionLassoMap.tsx");

  // 1. ONE SOURCE OF TRUTH. The map's drag lands in the SAME callback the
  //    editable table's `onCommit` calls -- `vertices` stays this page's only
  //    shape state, so dragging a corner moves the row beneath it and typing a
  //    degree moves the handle above it, with neither side holding a copy.
  pin.match(
    page,
    /onMoveVertex=\{updateVertex\}/,
    "expected a dragged corner to land in the SAME vertices-mutating callback the table's typed edit uses",
  );
  pin.match(
    page,
    /onCommit=\{updateVertex\}/,
    "expected the table's own edit to still use that one callback (both halves of the binding, pinned together)",
  );

  // 2. THE HANDLES ARE REAL AND THEY COMMIT ON DROP. `L.marker` because Leaflet
  //    implements dragging on Marker alone; `dragend` because the redraw effect
  //    replaces the whole layer group, so committing per mouse-move would tear
  //    down the marker the pointer is holding.
  pin.match(map, /draggable: true,/, "expected the ring's corners to be draggable");
  // Measured against the source with COMMENTS STRIPPED, all three of these: a
  // character-distance bound taken over raw source is a pin on how much prose
  // sits inside a handler, and it fires on an edit that explains the code
  // better rather than on one that breaks it. (Refuter, unit #48: two of these
  // did exactly that.)
  pin.match(
    withoutComments(map),
    /marker\.on\("dragend", \(\) => \{[\s\S]{0,240}?handlers\.current\.onMoveVertex\(index, nextLat, nextLon\);/,
    "expected the drag to commit on drop, through the page's own setter",
  );
  // ...and ONCE, which is what the message above claims and what a bound on the
  // `dragend` handler alone never checked: a second call from inside `drag`
  // would satisfy that pattern and commit on every mouse-move.
  pin.match(
    withoutComments(map),
    /^(?:(?!onMoveVertex\()[\s\S])*onMoveVertex\((?:(?!onMoveVertex\()[\s\S])*$/,
    "expected EXACTLY ONE onMoveVertex call site in the map -- a second one is a per-mouse-move commit",
  );
  pin.match(
    withoutComments(map),
    /marker\.on\("drag", \(\) => \{[\s\S]{0,500}?outline\.setLatLngs\(moved\);/,
    "expected the outline to follow the handle DURING the drag without a React round trip per mouse-move",
  );

  // 2b. A REDRAW MAY NOT LAND MID-DRAG. Leaflet's `Marker.onRemove` ->
  //     `MarkerDrag.removeHooks` -> `Draggable.disable` "counts as first ending
  //     the drag" and FIRES `dragend`, so rebuilding the layer group while a
  //     handle is held does not merely repaint: it commits the corner to
  //     wherever the pointer happened to be and takes the handle away. Keeping
  //     the drag's own writes off React state is NOT enough, because `bbox` is
  //     a fresh object out of every server preview and a preview is scheduled
  //     by the previous commit. (Refuter, unit #48: this was reproducible with
  //     nothing but two drags inside one debounce window.)
  pin.match(
    withoutComments(map),
    /marker\.on\("dragstart", \(\) => \{\s*draggingRef\.current = true;/,
    "expected a drag in flight to be recorded, so the redraw effect can defer",
  );
  pin.match(
    withoutComments(map),
    /if \(draggingRef\.current\) return;[\s\S]{0,400}?drawingRef\.current\?\.remove\(\)/,
    "expected the redraw effect to refuse to tear down the layer group while a corner is held",
  );
  pin.match(
    withoutComments(map),
    /draggingRef\.current = false;[\s\S]{0,240}?handlers\.current\.onMoveVertex\(/,
    "expected the drag flag to be lowered BEFORE the commit, so the deferred redraw runs with it",
  );

  // 3. A CLOSED RING IS DRAGGABLE, and that does not contradict "a closed ring
  //    refuses clicks": that rule is about ADDING a corner, and moving one adds
  //    nothing. The pre-filled proposal arrives closed, so a rule that stopped
  //    drags there would disable the case this was built for.
  // Pinned as "every `draggable:` is literally `true`" rather than as a
  // character distance between the click guard and the word `draggable`: those
  // two sit ~7300 characters apart, so the old bound of 200 could not fail for
  // any implementation at all, including one that DID disable dragging on a
  // closed ring under another spelling. (Refuter, unit #48.)
  pin.doesNotMatch(
    withoutComments(map),
    /draggable:(?!\s*true\b)/,
    "the closed-ring guard must not be extended over dragging -- moving a corner is not adding one",
  );
  // The guard it DOES still cover: clicking to add.
  pin.match(
    map,
    /pendingClick = setTimeout\(\(\) => \{[\s\S]{0,200}?if \(current\.closed\) return;/,
    "expected the closed ring to still refuse a click that would ADD a corner",
  );

  // 4. CLICKING A HANDLE MUST NOT DROP A SECOND CORNER ON TOP OF IT -- and the
  //    closing double-click must still work when it lands ON the first corner,
  //    which is where a user aims it. Interactive markers swallow both, so the
  //    dblclick is forwarded to the same `onClose` the map handler calls.
  pin.match(
    map,
    /marker\.on\("dblclick"[\s\S]{0,400}?current\.onClose\(\);/,
    "expected a double-click on a corner to close the ring, not to be eaten by the marker",
  );
  pin.match(
    map,
    /marker\.on\("click", \(event: \{ originalEvent\?: Event \}\) => \{\s*\n\s*event\.originalEvent\?\.stopPropagation\(\);/,
    "expected a click on a corner to stop there rather than adding a duplicate corner under the pointer",
  );

  // 5. STILL NO TEXT OF ITS OWN. The handle's hover title is built from a
  //    string the page's t() supplies, like every other label this map shows.
  pin.match(map, /handleLabel: string;/, "expected the handle's label to arrive as a prop");
  pin.match(page, /handleLabel=\{t\("점", "Point", "点"\)\}/, "expected the page to own the ko/en/ja wording");
});

contractTest("unit #48: a multi-finding hand-off names every finding it covers", (pin) => {
  const region = read("app/genesis/region/page.tsx");
  const handoff = read("lib/region-prefill.ts");

  // The banner BRANCHES: a proposal covering several findings must not print
  // the single-finding sentence, whose scalar fields describe only the FIRST
  // covered one. This is the screen that owns the extract button.
  pin.match(
    region,
    /\{prefill\.covers && prefill\.covers\.length > 1 \? \(/,
    "expected the arrival banner to branch on how many findings the shape covers",
  );
  const banner = sliceBetween(region, "{prefill && (", '<div className="mb-4 flex flex-col gap-3">');
  pin.match(banner, /\S/, "expected to find the arrival banner (markers moved?)");
  pin.match(
    banner,
    /prefill\.covers\.map\(\(cover\) => \(/,
    "expected every covered finding to be named in the banner, not just counted",
  );
  pin.match(
    banner,
    /prefill\.covers\.some\(\(cover\) => cover\.extrapolated_m > 0\) && \(/,
    "expected the multi-finding banner to state what an extrapolated tail IS, not only its metres",
  );

  // ONE covered finding is not the same as one KIND of measurement. An orphan's
  // hand-off is always a single covered finding, so it lands in the singular
  // branch -- where `deficit_m` is `basis_m`, and for an orphan that number is
  // metres the boundary CUT, not a runway shortage. The backend's own basis
  // note refuses to "call every finger's own number a deficit, because half of
  // them are not one"; the banner over the extract button is the last place
  // that could undo the refusal, and it did. (Refuter, unit #48.)
  const job = read("app/genesis/jobs/[id]/page.tsx");
  pin.match(
    job,
    /basis_kind: cover\.basis_kind,/,
    "expected basis_kind to travel with the hand-off -- without it the region page cannot tell a cut from a shortage",
  );
  pin.match(
    handoff,
    /if \(typeof row\.basis_kind !== "string"\) return null;/,
    "expected a cover with no basis_kind to drop the proposal rather than be read as a deficit",
  );
  pin.match(
    region,
    /prefill\.covers\?\.\[0\]\?\.basis_kind === BASIS_BOUNDARY_CUT \? \(/,
    "expected the singular banner to branch on WHAT was measured, not only on how many findings",
  );
  pin.doesNotMatch(
    // Comment-stripped: this branch's own comment says the word "shortage" in
    // order to explain why the branch exists, and a pin that cannot tell prose
    // from rendered text is a pin on the prose.
    sliceBetween(
      withoutComments(region),
      "prefill.covers?.[0]?.basis_kind === BASIS_BOUNDARY_CUT ? (",
      ") : (",
    ),
    /부족분|shortage|不足分/,
    "a boundary CUT must not be renamed a shortage on the screen that owns the extract button",
  );

  // The store is re-validated, and an INVALID `covers` drops the WHOLE
  // proposal. Returning the shape without it would put a banner naming one
  // finding over a button that fetches a region derived for three.
  pin.match(
    handoff,
    /if \(!Array\.isArray\(candidate\.covers\) \|\| candidate\.covers\.length === 0\) return null;/,
    "expected a present-but-broken covers list to drop the proposal rather than be ignored",
  );
});

contractTest("unit #48: the multi-finding hand-off round-trips, and a broken covers list is dropped", () => {
  // BEHAVIOUR, not spelling -- the same fake-store harness unit #40's own
  // hand-off test uses, extended to the field this unit added.
  const store = new Map<string, string>();
  const previousWindow = (globalThis as { window?: unknown }).window;
  (globalThis as { window?: unknown }).window = {
    sessionStorage: {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => void store.set(key, value),
      removeItem: (key: string) => void store.delete(key),
    },
  };
  try {
    const multi = {
      polygon: [
        [36.35, 127.38],
        [36.35, 127.39],
        [36.34, 127.39],
      ] as Array<[number, number]>,
      workspace_id: "bfc69251bd04",
      parent_job: "e84e57316ccb",
      finding_code: "v3_orphan_link",
      subject: "10 (tertiary_link [FWD] w37972527s0)",
      deficit_m: 9.32,
      added_area_km2: 0.00032,
      measured_source_m: 9.31,
      extrapolated_m: 1.87,
      covers: [
        {
          finding_code: "v3_orphan_link",
          subject: "10 (tertiary_link [FWD] w37972527s0)",
          basis_kind: "boundary_cut_metres",
          length_m: 11.18,
          measured_source_m: 9.31,
          extrapolated_m: 1.87,
        },
        {
          finding_code: "v4_lane_drop_without_runway",
          subject: "108 (둔산로 [FWD] w1416532929s1)",
          basis_kind: "lane_change_runway_deficit",
          length_m: 13.94,
          measured_source_m: 13.94,
          extrapolated_m: 0,
        },
      ],
    };

    assert.equal(stashRegionPrefill(multi), true);
    const read1 = takeRegionPrefill("bfc69251bd04");
    assert.equal(read1?.covers?.length, 2);
    assert.equal(read1?.covers?.[1].finding_code, "v4_lane_drop_without_runway");
    assert.equal(read1?.covers?.[1].extrapolated_m, 0);
    // REGRESSION (refuter, unit #48): `basis_kind` must survive the hand-off.
    // It is the ONLY thing that tells the region page an orphan's metres are
    // metres the boundary CUT and not a runway shortage, and the banner it
    // renders sits over the extract button. Dropping it printed
    // "v3_orphan_link shortage of 9.32 m" -- the exact rename the backend's own
    // basis note refuses to make.
    assert.equal(read1?.covers?.[0].basis_kind, "boundary_cut_metres");
    assert.equal(read1?.covers?.[1].basis_kind, "lane_change_runway_deficit");
    assert.equal(takeRegionPrefill("bfc69251bd04"), null, "one-shot still holds for a multi proposal");

    // A #40 single-finding proposal still reads back with no `covers` at all --
    // the field is ADDITIVE, and the old shape did not change under it.
    const { covers: _covers, ...single } = multi;
    assert.equal(stashRegionPrefill(single), true);
    assert.equal(takeRegionPrefill("bfc69251bd04")?.covers, undefined);

    // Present but broken: the WHOLE proposal is dropped, never half-read.
    for (const broken of [
      JSON.stringify({ ...multi, covers: [] }),
      JSON.stringify({ ...multi, covers: "two" }),
      JSON.stringify({ ...multi, covers: [{ finding_code: "x", subject: "y" }] }),
      JSON.stringify({
        ...multi,
        covers: [{ ...multi.covers[0], extrapolated_m: "1.87" }],
      }),
      JSON.stringify({ ...multi, covers: [{ ...multi.covers[0], subject: 7 }] }),
      // A cover with no `basis_kind` cannot be told apart from a runway
      // shortage, so it is dropped with the rest rather than guessed at.
      JSON.stringify({
        ...multi,
        covers: [{ ...multi.covers[0], basis_kind: undefined }],
      }),
    ]) {
      store.set(REGION_PREFILL_STORAGE_KEY, broken);
      assert.equal(takeRegionPrefill("bfc69251bd04"), null, `expected to drop: ${broken}`);
      assert.equal(store.size, 0, "a dropped proposal must still leave the store empty");
    }
  } finally {
    if (previousWindow === undefined) delete (globalThis as { window?: unknown }).window;
    else (globalThis as { window?: unknown }).window = previousWindow;
  }
});
